"""The OwnershipModel implementation: what the field will own and captain next.

Everything the forecast reads comes through a :class:`~fpl_edge.store.Snapshot`.
Where an earlier observation is needed -- the ownership drift rate before the
first deadline is measured between two polls -- it is read by constructing an
*earlier* Snapshot from the same warehouse. That is safe by construction: an
earlier as-of instant is a strict subset of what the current one may see, so it
cannot leak the future no matter what it returns.

Outputs are :data:`~fpl_edge.models.contracts.OWNERSHIP_COLUMNS` plus the
distributional and provenance columns the rank objective needs: a standard
deviation and calibrated interval on ownership, the components of effective
ownership so the algebra can be audited, and a flag marking whether the top-10k
figure was measured from sampled picks or produced from a prior.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import ModelCard, OWNERSHIP_COLUMNS
from fpl_edge.models.ownership import captaincy as cap
from fpl_edge.models.ownership.drift import (
    ColdStartParams,
    InSeasonParams,
    ScaleParams,
    coldstart_predict,
    inseason_predict,
)
from fpl_edge.models.ownership.eo import FieldShares
from fpl_edge.models.ownership.elite import EliteTiltParams, elite_tilt
from fpl_edge.models.ownership.field import SQUAD_SIZE
from fpl_edge.store import Snapshot
from fpl_edge.types import Availability, GwId, Season

_HERE = Path(__file__).parent
PARAMS_PATH = _HERE / "params.json"
MEASURED_PATH = _HERE / "measured.json"

#: How far back to look for a second ownership observation when measuring the
#: pre-deadline drift rate. Long enough that ownership has moved by more than
#: the API's 0.1% quantisation, short enough that the drift is still local.
DRIFT_LOOKBACK = dt.timedelta(hours=24)

#: The drift measurement window must be at least this multiple of the forecast
#: horizon before the drift term is trusted. The API quantises ownership to
#: 0.1%, so a 20-minute window measures one quantisation step and extrapolating
#: it three days forward turns rounding noise into a 16-point ownership swing.
#: The historical snapshots the drift coefficient was fitted on had windows of
#: 9-13 days against horizons of 1-4 days, so this guard also keeps the live
#: forecast inside the regime the coefficient was estimated in.
MIN_DRIFT_WINDOW_RATIO = 0.5

#: Columns this model returns beyond the contract's minimum.
EXTRA_COLUMNS = (
    "own_mean", "own_sd", "own_lo", "own_hi",
    "start_share", "vice_captain_share", "triple_captain_share",
    "path", "days_to_deadline", "eo_top10k_is_prior",
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. It is written by "
            "scripts/fit_ownership.py and committed alongside the model."
        )
    return json.loads(path.read_text())


@dataclass(frozen=True, slots=True)
class OwnershipParams:
    inseason: InSeasonParams
    coldstart: ColdStartParams
    #: Median field-growth weight ``N(gw)/N(gw+1)`` by gameweek, measured across
    #: five real seasons. Between GW1 and GW2 the field grows by about 9%, which
    #: moves a 70%-owned player by several points of ownership before anybody
    #: transfers anything.
    growth_w: Mapping[str, float] = None  # type: ignore[assignment]

    @classmethod
    def load(cls, path: Path = PARAMS_PATH) -> "OwnershipParams":
        raw = _load_json(path)
        ins = raw["inseason"]
        cold = raw["coldstart"]
        return cls(
            inseason=InSeasonParams(
                coef=tuple(ins["coef"]), intercept=float(ins["intercept"]),
                scale=ScaleParams(a=float(ins["scale"]["a"]), b=float(ins["scale"]["b"]),
                                  interval_k=ins["scale"]["interval_k"]),
            ),
            coldstart=ColdStartParams(
                coef_near=tuple(cold["coef_near"]), coef_far=tuple(cold["coef_far"]),
                coef_near_nodrift=tuple(cold.get("coef_near_nodrift", ())),
                coef_far_nodrift=tuple(cold.get("coef_far_nodrift", ())),
                shrink_per_day=float(cold["shrink_per_day"]),
                scale_a=float(cold["scale_a"]), scale_b=float(cold["scale_b"]),
                scale_day_exponent=float(cold["scale_day_exponent"]),
                interval_k=cold["interval_k"],
            ),
            growth_w=raw["growth_w"],
        )

    def w_for(self, gw: int) -> float:
        """Dilution weight for the transition out of ``gw``."""
        table = self.growth_w or {}
        key = str(int(gw))
        if key in table:
            return float(table[key])
        # Beyond the tabulated range the field is effectively static.
        return float(table.get("default", 1.0))


#: Availability assumed for a "doubtful" player when the API has not published a
#: ``chance_of_playing_next_round``. Doubtful is not fine: the field sells
#: flagged players before a deadline, and treating a yellow flag as fully
#: available makes the model miss exactly the moves that are easiest to predict.
DOUBTFUL_DEFAULT_AVAILABILITY = 0.75


def _availability(status: pd.Series, chance: pd.Series) -> np.ndarray:
    """Probability the player is available, from status and the chance field."""
    out = np.ones(len(status), dtype=float)
    for i, (raw, c) in enumerate(zip(status.fillna("a"), chance)):
        code = str(raw)
        if pd.notna(c):
            out[i] = float(c) / 100.0
            continue
        try:
            available = Availability(code)
        except ValueError:
            out[i] = 1.0
            continue
        if available is Availability.AVAILABLE:
            out[i] = 1.0
        elif available is Availability.DOUBTFUL:
            out[i] = DOUBTFUL_DEFAULT_AVAILABILITY
        else:
            out[i] = 0.0
    return np.clip(out, 0.0, 1.0)


class OwnershipForecaster:
    """Forecast of ownership, captaincy and effective ownership at a deadline.

    ``expected_points`` is the seam for the points model: a mapping from player
    code to that model's expected points for the gameweek. When it is absent the
    projection feature contributes nothing rather than being faked, and the
    forecast degrades to the ownership and price signals alone.
    """

    def __init__(
        self,
        params: OwnershipParams | None = None,
        *,
        captaincy_params: cap.CaptaincyParams | None = None,
        elite_params: EliteTiltParams | None = None,
        expected_points: Mapping[int, float] | None = None,
        field_size: int | None = None,
        card: ModelCard | None = None,
    ) -> None:
        self.params = params or OwnershipParams.load()
        self.captaincy_params = captaincy_params or cap.CaptaincyParams()
        self.elite_params = elite_params or EliteTiltParams()
        self.expected_points = dict(expected_points or {})
        self.field_size = field_size
        self.card = card or build_card()

    # -- inputs -----------------------------------------------------------

    def _prior_players(self, snapshot: Snapshot, season: str,
                       lookback: dt.timedelta) -> pd.DataFrame | None:
        """The same table as it was ``lookback`` earlier.

        Ownership drift is a rate, and a rate needs two observations. The
        warehouse keeps every poll with its ``as_of``, so the second observation
        is a Snapshot at an earlier instant -- and an earlier as-of can only ever
        reveal *less* than the current one, so rewinding cannot leak the future.

        The escape hatch is used only to obtain the handle needed to construct
        that earlier Snapshot; nothing is read unfiltered. A sanctioned
        ``Snapshot.rewind(delta)`` on the warehouse would remove the need for it,
        and is worth adding.
        """
        wh = snapshot.escape_hatch_unfiltered(
            "constructing a strictly earlier Snapshot to measure the ownership "
            "drift rate; an earlier as_of is a subset of the current one and "
            "nothing is read outside point-in-time filtering"
        )
        earlier = wh.snapshot_at(snapshot.as_of - lookback)
        try:
            prior = earlier.players(season)
        except Exception:
            return None
        return None if prior.empty else prior

    def _selectable(self, players: pd.DataFrame) -> np.ndarray:
        """FPL's own ``can_select`` flag, when the warehouse carries it.

        Filtering on ``status`` alone is not enough: the two fields can diverge,
        and ``can_select`` is what the game enforces. A player nobody can buy
        can only lose ownership, so treating them as available would have the
        forecast predicting inflow that is not possible.
        """
        ok = np.ones(len(players), dtype=float)
        if "can_select" in players.columns:
            ok *= players["can_select"].fillna(True).astype(bool).to_numpy().astype(float)
        if "removed" in players.columns:
            ok *= (~players["removed"].fillna(False).astype(bool)).to_numpy().astype(float)
        return ok

    def _field_size(self, snapshot: Snapshot, season: str) -> float:
        if self.field_size:
            return float(self.field_size)
        raise ValueError(
            "field size (total_players) is not carried by the warehouse schema and "
            "no field_size was supplied. In-season flow is a share of the field, so "
            "guessing it would silently rescale every transfer signal."
        )

    # -- forecast ---------------------------------------------------------

    def forecast(self, snapshot: Snapshot, season: Season, gw: GwId) -> pd.DataFrame:
        players = snapshot.players(str(season))
        if players.empty:
            raise ValueError(f"no players visible at {snapshot.as_of} for {season}")
        players = players.sort_values("code").reset_index(drop=True)

        deadline = snapshot.deadline(str(season), int(gw))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=dt.timezone.utc)
        days = (deadline - snapshot.as_of).total_seconds() / 86400.0
        if days < 0:
            raise ValueError(
                f"GW{gw} deadline {deadline.isoformat()} is already past at "
                f"{snapshot.as_of.isoformat()}; there is nothing left to forecast"
            )

        own = players["selected_by_pct"].to_numpy(dtype=float) / 100.0
        avail = _availability(players["status"], players["chance_of_playing_next_round"])
        avail = avail * self._selectable(players)
        flagged = (avail < 1.0).astype(float)
        ep = np.array([float(self.expected_points.get(int(c), 0.0))
                       for c in players["code"]], dtype=float)

        ti = players["transfers_in_event"].fillna(0).to_numpy(dtype=float)
        to = players["transfers_out_event"].fillna(0).to_numpy(dtype=float)
        cold = int(gw) == 1 or not np.any((ti - to) != 0)

        if cold:
            drift_rate = self._drift_rate(snapshot, str(season), players, days)
            mean, sd = coldstart_predict(
                self.params.coldstart, own, ep, flagged, days, drift_rate
            )
            k = self.params.coldstart.k(0.8)
            path = "cold_start+drift" if drift_rate is not None else "cold_start"
        else:
            prior = self._prior_players(snapshot, str(season), dt.timedelta(days=8))
            own_prev = own.copy()
            dvalue = np.zeros_like(own)
            if prior is not None:
                pm = prior.set_index("code")
                own_prev = players["code"].map(
                    pm["selected_by_pct"] / 100.0).fillna(pd.Series(own)).to_numpy(dtype=float)
                dvalue = (
                    players["price_tenths"].to_numpy(dtype=float)
                    - players["code"].map(pm["price_tenths"]).fillna(
                        players["price_tenths"]).to_numpy(dtype=float)
                ) / 10.0
            n_field = self._field_size(snapshot, str(season))
            flow = (ti - to) / n_field
            pts = self._recent_points(snapshot, str(season), players)
            mean, sd = inseason_predict(
                self.params.inseason, own, own_prev, flow, pts, dvalue,
                self.params.w_for(int(gw) - 1), total=float(SQUAD_SIZE),
            )
            k = self.params.inseason.scale.k(0.8)
            path = "in_season"

        # -- captaincy and starting share ---------------------------------
        is_home = self._home_flags(snapshot, str(season), int(gw), players)
        appeal = cap.appeal_score(
            players["price_tenths"].to_numpy(), players["position"].to_numpy(),
            is_home, self.captaincy_params,
        )
        p_start = cap.normalise_start_share(
            mean, cap.start_probability(players["position"].to_numpy(), avail),
            bench_boost_share=self.captaincy_params.bench_boost_usage,
        )
        captain = cap.captaincy_share(
            mean, appeal, available=avail, params=self.captaincy_params
        )
        # A captain must be started, and every manager names exactly one, so the
        # captaincy vector lives on {c >= 0, sum c = 1, c <= start}. Project onto
        # it rather than clamping, which would break the sum.
        shares = FieldShares(
            ownership=mean, p_start_given_owned=p_start, captain_share=captain,
            bench_boost_share=self.captaincy_params.bench_boost_usage,
        )
        start = shares.start_share()
        captain = cap.cap_to_start(captain, start)
        tc = cap.triple_captain_share(captain, self.captaincy_params)
        vice = cap.vice_captain_share(
            mean, appeal, captain, available=avail, params=self.captaincy_params
        )
        shares = FieldShares(
            ownership=mean, p_start_given_owned=p_start, captain_share=captain,
            triple_captain_share=tc,
            bench_boost_share=self.captaincy_params.bench_boost_usage,
        )
        eo_overall = shares.eo()

        # -- top-10k ------------------------------------------------------
        own_top = elite_tilt(own, mean, self.elite_params)
        p_start_top = cap.normalise_start_share(
            own_top, cap.start_probability(players["position"].to_numpy(), avail),
            bench_boost_share=self.captaincy_params.bench_boost_usage,
        )
        start_top = FieldShares(
            ownership=own_top, p_start_given_owned=p_start_top,
            captain_share=np.zeros_like(own_top),
            bench_boost_share=self.captaincy_params.bench_boost_usage,
        ).start_share()
        top_cap = cap.cap_to_start(
            cap.captaincy_share(own_top, appeal, available=avail,
                                params=self.captaincy_params),
            start_top,
        )
        eo_top10k = FieldShares(
            ownership=own_top, p_start_given_owned=p_start_top, captain_share=top_cap,
            triple_captain_share=cap.triple_captain_share(top_cap, self.captaincy_params),
            bench_boost_share=self.captaincy_params.bench_boost_usage,
        ).eo()

        out = pd.DataFrame({
            "code": players["code"].to_numpy(),
            "gw": int(gw),
            "eo_overall": eo_overall,
            "captaincy_share": captain,
            "eo_top10k": eo_top10k,
            "own_mean": mean,
            "own_sd": sd,
            "own_lo": np.clip(mean - k * sd, 0.0, 1.0),
            "own_hi": np.clip(mean + k * sd, 0.0, 1.0),
            "start_share": start,
            "vice_captain_share": vice,
            "triple_captain_share": tc,
            "path": path,
            "days_to_deadline": round(days, 4),
            "eo_top10k_is_prior": not self.elite_params.measured,
        })
        return out[list(OWNERSHIP_COLUMNS) + list(EXTRA_COLUMNS)]

    # -- helpers ----------------------------------------------------------

    def _drift_rate(self, snapshot: Snapshot, season: str,
                    players: pd.DataFrame, days_ahead: float) -> np.ndarray | None:
        """Ownership change per day, measured between two warehouse snapshots.

        Returns None when only one observation exists. Before the first deadline
        this is the single most valuable signal available -- measured on real
        pre-deadline snapshots it beats persistence by more than every
        structural feature combined -- which is the entire reason ownership is
        polled rather than read once.
        """
        prior = self._prior_players(snapshot, season, DRIFT_LOOKBACK)
        if prior is None:
            return None
        pm = prior.set_index("code")
        before = players["code"].map(pm["selected_by_pct"] / 100.0)
        gap_days = (
            snapshot.as_of - pd.to_datetime(prior["as_of"], utc=True).max()
        ).total_seconds() / 86400.0
        if not np.isfinite(gap_days) or gap_days <= 1e-6 or before.isna().all():
            return None
        if gap_days < MIN_DRIFT_WINDOW_RATIO * days_ahead:
            return None
        now = players["selected_by_pct"].to_numpy(dtype=float) / 100.0
        rate = (now - before.fillna(pd.Series(now, index=before.index)).to_numpy()) / gap_days
        return np.nan_to_num(rate, nan=0.0)

    def _recent_points(self, snapshot: Snapshot, season: str,
                       players: pd.DataFrame) -> np.ndarray:
        """Points scored in the most recent finalised gameweek."""
        res = snapshot.results_before(season)
        if res.empty:
            return np.zeros(len(players), dtype=float)
        last = res["gw"].max()
        agg = res[res["gw"] == last].groupby("code")["total_points"].sum()
        return players["code"].map(agg).fillna(0.0).to_numpy(dtype=float)

    def _home_flags(self, snapshot: Snapshot, season: str, gw: int,
                    players: pd.DataFrame) -> np.ndarray | None:
        fx = snapshot.upcoming_fixtures(season)
        if fx.empty:
            return None
        fx = fx[fx["gw"] == gw]
        if fx.empty:
            return None
        home = set(fx["home_team_code"].tolist())
        away = set(fx["away_team_code"].tolist())
        return np.array([
            1.0 if t in home else (0.0 if t in away else 0.5)
            for t in players["team_code"]
        ], dtype=float)


def build_card(path: Path = MEASURED_PATH) -> ModelCard:
    """ModelCard populated from the committed out-of-sample measurements."""
    m = _load_json(path)
    ins = m["inseason"]
    cold = m["coldstart"]
    return ModelCard(
        name="ownership.OwnershipForecaster",
        approach=(
            "Two-regime ownership drift. In-season: field-growth dilution plus "
            "fitted transfer carry-over, form chasing, price pressure and "
            "overshoot correction, projected onto the sum-to-15 simplex. GW1 "
            "cold start: no transfer flow exists, so an ownership-level "
            "concentration term plus extrapolated poll-to-poll drift. Captaincy "
            "is a multinomial logit on ownership and price; EO = start + captain "
            "+ triple-captain."
        ),
        baseline="persistence (ownership unchanged); naive transfer/drift momentum",
        metric="MAE of next-deadline ownership, percentage points, leave-one-season-out",
        score=float(ins["model"]),
        baseline_score=float(ins["persistence"]),
        trained_through=m.get("trained_through"),
        notes=tuple(m.get("notes", ())),
    )
