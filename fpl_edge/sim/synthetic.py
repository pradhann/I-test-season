"""Stand-in points and ownership models, so the simulator is never blocked.

The points model and the ownership model are owned by other teams. This module
implements their contracts (:class:`~fpl_edge.models.contracts.PointsModel`,
:class:`~fpl_edge.models.contracts.OwnershipModel`) well enough to exercise
every property the simulator depends on, and no better. It is **not** a
forecasting model and its :class:`ModelCard` says so: it is scored against no
baseline because it predicts nothing.

What it does have to get right is *structure*, because the field model would
otherwise be tested against a straw man:

* Goals are allocated by Poisson thinning of a team's expected goals, so player
  goals inside a team sum to a Poisson team total exactly. Clean sheets are then
  literally "the opponent's players scored nothing", which makes every Arsenal
  defender's clean sheet one shared event rather than five independent ones --
  the exact failure mode ``PointsSample``'s docstring warns about.
* Bonus points are ranked within a fixture, which induces negative correlation
  between team-mates competing for the same three points.
* Minutes are drawn per player and gate every other component, so blanks
  propagate to clean sheets, defensive contributions and bonus together.

Swap in the real models by passing them to :class:`~fpl_edge.sim.engine.SeasonSimulator`;
nothing in the simulator imports this module.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import ModelCard, PointsSample
from fpl_edge.sim.squad import PlayerUniverse
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

# Verified from docs/rules.md (registry-backed).
GOAL_POINTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_POINTS = {1: 4, 2: 4, 3: 1, 4: 0}
DEFCON_POINTS = {1: 0, 2: 2, 3: 2, 4: 2}
ASSIST_POINTS = 3
SAVES_PER_POINT = 3
YELLOW, RED = -1, -3
BONUS = (3, 2, 1)

#: Rough per-position rates. These are shape parameters for a stand-in, not
#: estimates; the real points model replaces all of them.
P_DEFCON = {1: 0.00, 2: 0.36, 3: 0.20, 4: 0.07}
P_YELLOW = {1: 0.03, 2: 0.13, 3: 0.11, 4: 0.10}
P_RED = 0.005
ASSIST_FRACTION = 0.70
HOME_ADVANTAGE = 0.22

# Shape parameters fitted to four seasons of real per-fixture returns in the
# warehouse (fact_player_fixture 2022-23..2025-26). See
# fpl_edge/sim/calibration.py for the targets and the measured fit.
ATT_EXP = 1.6        # how steeply goal share rises with price
CRE_EXP = 1.4        # same, for assists
SP_SLOPE = 0.050     # start probability decay by price rank inside a club
CAMEO = 0.30         # chance a non-starter gets minutes off the bench
LEAGUE_GOALS_PER_TEAM = 1.42


def selectable_players(snapshot: Snapshot, season: Season) -> pd.DataFrame:
    """Players the field may pick, read through the Snapshot.

    Prefers ``Snapshot.players()``. Falls back to joining the two point-in-time
    tables directly if that convenience view is unavailable for this warehouse
    build -- the tables are contract-stable, the view is not. Both paths honour
    the as-of instant, which is the property that actually matters.
    """
    try:
        df = snapshot.players(season)
    except Exception:  # noqa: BLE001 -- any binder/schema drift falls back
        dim = snapshot.table("dim_player", where="season = ?", params=[season])
        st = snapshot.table("fact_player_state", where="season = ?", params=[season])
        df = dim.merge(st, on=["season", "code"], suffixes=("", "_state"))
    keep = df["status"].isin(("a", "d")) | df["status"].isna()
    return df[keep].reset_index(drop=True)


@dataclass
class SyntheticPointsModel:
    """A structurally-correct, forecast-worthless points model."""

    universe: PlayerUniverse
    fixtures: pd.DataFrame
    start_prob: np.ndarray
    attack_weight: np.ndarray
    creativity_weight: np.ndarray
    team_attack: dict[int, float]
    team_defence: dict[int, float]
    #: Level calibration, chosen to reproduce the *ownership-weighted* points
    #: total -- the expected return of a random 15 drawn at the field's marginal
    #: ownership, measured at 51.5 points per gameweek in 2025-26 real data.
    #: That is the statistic the field model actually consumes, so it is the one
    #: worth matching. The cost is that the unweighted pool total then overshoots
    #: its own real value; the residual is reported by
    #: :func:`fpl_edge.sim.calibration.validate_points_model`. A property of the
    #: stand-in, not of the design: the real points model will not need it.
    points_scale: float = 1.198
    card: ModelCard = None  # type: ignore[assignment]
    _goals_cache: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if self.card is None:
            self.card = ModelCard(
                name="synthetic-points",
                approach="Poisson-thinned team goals; minutes-gated components; ranked bonus",
                baseline="none",
                metric="none -- this model forecasts nothing",
                notes=(
                    (
                        "Development stand-in for the points team's model. Preserves "
                        "the correlation structure the field model depends on (shared "
                        "clean sheets, shared team goals, competing bonus) and nothing "
                        "else."
                    ),
                ),
            )

    # -- construction --------------------------------------------------------

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, season: Season) -> SyntheticPointsModel:
        players = selectable_players(snapshot, season)
        universe = PlayerUniverse.from_players_frame(players)
        fixtures = snapshot.table("fact_fixture", where="season = ?", params=[season])
        fixtures = fixtures.sort_values(["gw", "fixture_id"]).reset_index(drop=True)

        price = universe.price_tenths.astype(float)
        pos = universe.position
        teams = universe.team_code

        # Role proxy: rank by price inside a club. The eleven most expensive
        # players at a club are a decent prior for who starts.
        start_prob = np.zeros(universe.n_players)
        attack = np.zeros(universe.n_players)
        creativity = np.zeros(universe.n_players)
        team_attack: dict[int, float] = {}
        team_defence: dict[int, float] = {}
        for t in np.unique(teams):
            m = teams == t
            idx = np.flatnonzero(m)
            order = idx[np.argsort(-price[idx], kind="stable")]
            rank = np.empty(len(order))
            rank[np.argsort(order, kind="stable")] = np.arange(len(order))
            r = np.empty(universe.n_players)
            r[order] = np.arange(len(order))
            sp = np.clip(0.95 - SP_SLOPE * r[idx], 0.01, 0.95)
            # A club only has one starting keeper, whoever else is on the books.
            gk = idx[pos[idx] == 1]
            gk_order = gk[np.argsort(-price[gk], kind="stable")]
            start_prob[idx] = sp
            start_prob[gk_order[:1]] = 0.93
            start_prob[gk_order[1:]] = 0.05

            # Steep in price: the point of these exponents is not realism per
            # player but reproducing the *ownership-weighted* points total,
            # which is a verified quantity (see docs/models/simulator.md).
            att_scale = {1: 0.0, 2: 0.10, 3: 0.55, 4: 1.0}
            cre_scale = {1: 0.0, 2: 0.22, 3: 0.85, 4: 0.55}
            rel = price[idx] / max(price[idx].max(), 1.0)
            attack[idx] = np.array([att_scale[int(p)] for p in pos[idx]]) * rel ** ATT_EXP
            creativity[idx] = np.array([cre_scale[int(p)] for p in pos[idx]]) * rel ** CRE_EXP

            # Team strength from wage-bill proxy: total price of the top 15.
            top15 = np.sort(price[idx])[::-1][:15].sum()
            team_attack[int(t)] = top15
            team_defence[int(t)] = top15

        mass = np.array(list(team_attack.values()), dtype=float)
        mu, sd = mass.mean(), mass.std()
        for t, raw in list(team_attack.items()):
            z = (raw - mu) / (sd if sd > 0 else 1.0)
            team_attack[t] = 0.20 * z
            team_defence[t] = 0.20 * z
        return cls(
            universe=universe,
            fixtures=fixtures,
            start_prob=start_prob,
            attack_weight=attack,
            creativity_weight=creativity,
            team_attack=team_attack,
            team_defence=team_defence,
        )

    # -- contract ------------------------------------------------------------

    def simulate(
        self,
        snapshot: Snapshot | None,
        season: Season,
        gw: GwId,
        *,
        n_sims: int = 10_000,
        seed: int = 0,
    ) -> PointsSample:
        rng = np.random.default_rng((seed * 1_000_003) ^ (int(gw) * 7919))
        u = self.universe
        p = u.n_players
        pts = np.zeros((p, n_sims), dtype=np.float32)
        mins = np.zeros((p, n_sims), dtype=np.int16)

        fx = self.fixtures[self.fixtures["gw"] == int(gw)]
        self._goals_cache = {}
        for _, row in fx.iterrows():
            for home in (True, False):
                team = int(row["home_team_code"] if home else row["away_team_code"])
                opp = int(row["away_team_code"] if home else row["home_team_code"])
                lam = LEAGUE_GOALS_PER_TEAM * np.exp(
                    self.team_attack[team] - self.team_defence[opp]
                    + (HOME_ADVANTAGE if home else -HOME_ADVANTAGE)
                )
                self._simulate_team(rng, team, lam, pts, mins, n_sims)

            # Goals conceded and clean sheets need both teams' totals, so they
            # are applied after both sides have been simulated.
            self._apply_defensive(row, pts, mins, n_sims)
            # Bonus is contested across both teams, and only once every other
            # component is in, because BPS tracks the same underlying events.
            self._award_bonus(rng, row, pts, mins, n_sims)

        pts = np.rint(pts * self.points_scale).astype(np.float32)
        return PointsSample(codes=u.codes, gw=gw, points=pts, minutes=mins)

    # -- internals -----------------------------------------------------------

    def _team_idx(self, team: int) -> np.ndarray:
        return np.flatnonzero(self.universe.team_code == team)

    def _simulate_team(self, rng, team, lam, pts, mins, n_sims) -> None:
        idx = self._team_idx(team)
        if len(idx) == 0:
            return
        sp = self.start_prob[idx][:, None]
        r = rng.random((len(idx), n_sims))
        started = r < sp
        cameo = (~started) & (r < sp + CAMEO * (1 - sp))
        minutes = np.where(started, rng.integers(62, 91, size=(len(idx), n_sims)),
                           np.where(cameo, rng.integers(5, 59, size=(len(idx), n_sims)), 0))
        mins[idx] = minutes
        played = minutes > 0
        frac = minutes / 90.0

        pos = self.universe.position[idx]
        pts[idx] += np.where(minutes >= 60, 2.0, np.where(played, 1.0, 0.0))

        # Poisson thinning: rates sum to the team's expected goals in
        # expectation, so team goals stay Poisson and clean sheets are exact.
        aw = self.attack_weight[idx][:, None]
        exp_frac = np.clip(self.start_prob[idx][:, None] * 0.90 + 0.12, 0, 1)
        denom = float((self.attack_weight[idx][:, None] * exp_frac).sum())
        denom = denom if denom > 1e-9 else 1.0
        goals = rng.poisson(lam * aw * frac / denom)
        cw = self.creativity_weight[idx][:, None]
        cdenom = float((self.creativity_weight[idx][:, None] * exp_frac).sum())
        cdenom = cdenom if cdenom > 1e-9 else 1.0
        assists = rng.poisson(ASSIST_FRACTION * lam * cw * frac / cdenom)

        gp = np.array([GOAL_POINTS[int(x)] for x in pos])[:, None]
        pts[idx] += goals * gp + assists * ASSIST_POINTS

        defcon_p = np.array([P_DEFCON[int(x)] for x in pos])[:, None]
        defcon = (rng.random((len(idx), n_sims)) < defcon_p) & (minutes >= 60)
        dp = np.array([DEFCON_POINTS[int(x)] for x in pos])[:, None]
        pts[idx] += defcon * dp

        yel = rng.random((len(idx), n_sims)) < np.array(
            [P_YELLOW[int(x)] for x in pos]
        )[:, None] * played
        red = (rng.random((len(idx), n_sims)) < P_RED) & played
        pts[idx] += yel * YELLOW + red * RED

        self._goals_cache[team] = (idx, goals.sum(axis=0), minutes)

    def _apply_defensive(self, row, pts, mins, n_sims) -> None:
        h, a = int(row["home_team_code"]), int(row["away_team_code"])
        if h not in self._goals_cache or a not in self._goals_cache:
            return
        for team, opp in ((h, a), (a, h)):
            idx, _, minutes = self._goals_cache[team]
            conceded = self._goals_cache[opp][1]
            pos = self.universe.position[idx]
            cs = (conceded[None, :] == 0) & (minutes >= 60)
            csp = np.array([CS_POINTS[int(x)] for x in pos])[:, None]
            pts[idx] += cs * csp
            back = np.isin(pos, (1, 2))[:, None]
            pts[idx] -= back * (conceded[None, :] // 2) * (minutes >= 60)
            gk = (pos == 1)[:, None]
            saves = np.clip(np.round(conceded[None, :] * 0.9 + 2.4), 0, None)
            pts[idx] += gk * (minutes >= 60) * (saves // SAVES_PER_POINT)

    def _award_bonus(self, rng, row, pts, mins, n_sims) -> None:
        h, a = int(row["home_team_code"]), int(row["away_team_code"])
        idx = np.concatenate([self._team_idx(h), self._team_idx(a)])
        if len(idx) == 0:
            return
        played = mins[idx] > 0
        # BPS proxy: the points already accrued plus playing time, jittered so
        # ties break randomly rather than by array order.
        bps = pts[idx] * 3.0 + (mins[idx] >= 60) * 3.0 + rng.random((len(idx), n_sims)) * 0.5
        bps = np.where(played, bps, -1e9)
        top = np.argsort(-bps, axis=0)[:3]
        for j, b in enumerate(BONUS):
            rows = idx[top[j]]
            np.add.at(pts, (rows, np.arange(n_sims)), b * (bps[top[j], np.arange(n_sims)] > -1e8))


@dataclass
class SyntheticOwnershipModel:
    """Ownership forecast built from the real ``selected_by_pct`` in the warehouse.

    ``eo_overall`` is not synthetic at all -- it is the API's own selection
    percentage at the snapshot instant. Only the split into captaincy share and
    top-10k ownership is modelled, by tilting toward expected points.
    """

    universe: PlayerUniverse
    selected_by_pct: np.ndarray
    captain_temperature: float = 1.6
    top10k_tilt: float = 0.75
    card: ModelCard = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.card is None:
            self.card = ModelCard(
                name="synthetic-ownership",
                approach="API selected_by_pct for eo_overall; xP-tilted for captaincy and top-10k",
                baseline="none",
                metric="none -- development stand-in",
            )

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, season: Season,
                      universe: PlayerUniverse) -> SyntheticOwnershipModel:
        players = selectable_players(snapshot, season).set_index("code")
        sel = players["selected_by_pct"].reindex(universe.codes).fillna(0.0).to_numpy() / 100.0
        return cls(universe=universe, selected_by_pct=sel)

    def set_expected_points(self, xp: np.ndarray) -> None:
        """Receive the current gameweek's expected points out of band."""
        self._xp = np.asarray(xp, dtype=np.float64)

    def forecast(self, snapshot, season: Season, gw: GwId,
                 expected_points: np.ndarray | None = None) -> pd.DataFrame:
        u = self.universe
        if expected_points is None:
            expected_points = getattr(self, "_xp", None)
        eo = np.clip(self.selected_by_pct, 1e-5, 0.999)
        if expected_points is None:
            expected_points = np.zeros(u.n_players)
        z = _z_within_position(expected_points, u.position)

        w = eo * np.exp(self.captain_temperature * z)
        # Only a plausible captain gets captained: heavily weight ownership.
        w = w * (eo > 0.02)
        captaincy = w / max(w.sum(), 1e-9)

        t = eo * np.exp(self.top10k_tilt * z)
        eo10k = np.zeros_like(eo)
        for p in (1, 2, 3, 4):
            m = u.position == p
            n = {1: 2, 2: 5, 3: 5, 4: 3}[p]
            eo10k[m] = np.clip(t[m] * n / max(t[m].sum(), 1e-9), 0.0, 0.999)
        return pd.DataFrame(
            {
                "code": u.codes,
                "gw": int(gw),
                "eo_overall": eo,
                "captaincy_share": captaincy,
                "eo_top10k": eo10k,
            }
        )


def _z_within_position(x: np.ndarray, position: np.ndarray) -> np.ndarray:
    out = np.zeros_like(x, dtype=np.float64)
    for p in (1, 2, 3, 4):
        m = position == p
        v = x[m]
        sd = v.std()
        out[m] = (v - v.mean()) / (sd if sd > 1e-9 else 1.0)
    return out


@dataclass
class ToyPointsModel:
    """A tiny, warehouse-free correlated points model, for tests.

    Players are grouped into ``n_teams`` teams. Each team draws one shared
    per-simulation shock (a stand-in for "the team kept a clean sheet and won"),
    so team-mates' points are correlated and a squad that doubles up on a team
    is genuinely more volatile than one that does not. That is the only property
    the simulator's unit tests need from a points model.
    """

    universe: PlayerUniverse
    base: np.ndarray            # (P,) mean points per player
    team_sd: float = 2.0
    player_sd: float = 2.0
    card: ModelCard = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.card is None:
            self.card = ModelCard(name="toy-points", approach="team shock + player noise",
                                  baseline="none", metric="none -- test fixture")

    def simulate(self, snapshot, season, gw, *, n_sims: int = 1_000, seed: int = 0):
        rng = np.random.default_rng((seed * 7_919) ^ (int(gw) * 104_729))
        u = self.universe
        teams = np.unique(u.team_code)
        tix = np.searchsorted(teams, u.team_code)
        shock = rng.normal(0.0, self.team_sd, size=(len(teams), n_sims))
        pts = self.base[:, None] + shock[tix] + rng.normal(0.0, self.player_sd,
                                                           size=(u.n_players, n_sims))
        pts = np.rint(np.clip(pts, -4, 60)).astype(np.float32)
        mins = np.where(rng.random((u.n_players, n_sims)) < 0.9, 90, 0).astype(np.int16)
        return PointsSample(codes=u.codes, gw=gw, points=pts, minutes=mins)


def toy_world(n_teams: int = 8, per_team: int = 14, seed: int = 0):
    """A deterministic toy universe, points model and ownership vector.

    Returns ``(universe, points_model, eo, captaincy, expected_points)``.
    """
    rng = np.random.default_rng(seed)
    codes, pos, team, price, names = [], [], [], [], []
    layout = [1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4, 4]
    c = 1000
    for t in range(n_teams):
        for j in range(per_team):
            codes.append(c)
            pos.append(layout[j % len(layout)])
            team.append(100 + t)
            price.append(40 + 3 * (per_team - 1 - j))
            names.append(f"T{t}P{j}")
            c += 1
    universe = PlayerUniverse(
        codes=np.array(codes, dtype=np.int64),
        position=np.array(pos, dtype=np.int8),
        team_code=np.array(team, dtype=np.int64),
        price_tenths=np.array(price, dtype=np.int64),
        web_name=np.array(names, dtype=object),
    )
    base = 1.0 + 6.0 * (universe.price_tenths - universe.price_tenths.min()) / (
        universe.price_tenths.max() - universe.price_tenths.min()
    )
    base = base + rng.normal(0.0, 0.3, size=universe.n_players)
    model = ToyPointsModel(universe=universe, base=base)

    eo = np.zeros(universe.n_players)
    for p in (1, 2, 3, 4):
        m = universe.position == p
        n = {1: 2, 2: 5, 3: 5, 4: 3}[p]
        w = np.exp(2.2 * _z_within_position(base, universe.position)[m])
        v = w * n / w.sum()
        for _ in range(20):                     # keep every entry a probability
            over = v > 0.95
            if not over.any():
                break
            excess = (v[over] - 0.95).sum()
            v[over] = 0.95
            free = ~over
            v[free] += excess * v[free] / v[free].sum()
        eo[m] = np.clip(v, 1e-4, 0.95)
    cw = eo * np.exp(2.5 * _z_within_position(base, universe.position))
    captaincy = cw / cw.sum()
    return universe, model, eo, captaincy, base


def build_synthetic_world(db_path="data/warehouse/fpl.duckdb",
                          season: Season | None = None,
                          as_of: dt.datetime | None = None):
    """Convenience: open the warehouse and build the stand-in models."""
    from fpl_edge.store import Warehouse

    if season is None:
        season = Season("2026-27")
    if as_of is None:
        as_of = dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.UTC)
    wh = Warehouse(db_path, read_only=True)
    snap = wh.snapshot_at(as_of)
    points = SyntheticPointsModel.from_snapshot(snap, season)
    own = SyntheticOwnershipModel.from_snapshot(snap, season, points.universe)
    return snap, points, own
