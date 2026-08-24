"""Empirical-Bayes hierarchical minutes model.

The idea
--------
A player's own record is the best evidence about his minutes and there is never
enough of it. Nine games into a season a fringe centre-back has nine
observations; a naive per-player rate on nine trials is mostly noise, and by the
time it is not, the season is over. So the estimate is shrunk toward a prior
built from *where in the club he sits*: the (position, within-club depth rank)
cell. The amount of shrinkage is not a taste parameter - it is the
Dirichlet-multinomial concentration implied by how much genuine spread there is
between players in that cell, estimated by moments from the training seasons.

Cells with real spread (fourth-choice midfielders, who range from "occasional
starter" to "never on the bench") get weak shrinkage. Cells that are homogeneous
(third-choice goalkeepers, who all play zero) get strong shrinkage, which is
exactly right: for those players the cell tells you nearly everything.

On top of the shrunken rate sit three corrections, each a handful of parameters
fitted by minimising training log loss rather than asserted:

* an **availability gate** that scales the "appears at all" mass by what the
  published status and ``chance_of_playing_next_round`` are empirically worth -
  FPL's 75% flags are not 75% and the data says by how much;
* a **congestion** adjustment for clubs likely to have played midweek in Europe;
* a **vector scaling** of the final three-way distribution.

The cold-start path (:class:`_Stage` fitted with ``cold=True``) is the same
machinery with the current-season evidence term structurally absent and the
cell definition extended by whether the player is new to the club, because a
summer signing's previous-season record is evidence about him but much weaker
evidence about his role.

STATUS: RESEARCH, not in the production import closure (reachability audit 2026-08-20, docs/platform/AUDIT_2026-08-20.md). Kept deliberately: the partial-pooling alternative if the GBM's cold-start behaviour degrades. Nothing imports this from production code, and anything that starts to should say so in ROADMAP.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.minutes import measured
from fpl_edge.models.minutes.base import BaseMinutesModel, normalise
from fpl_edge.models.minutes.training import TrainingSet

#: Availability gate buckets. Order matters only for readability.
GATE_NAMES = (
    "clean",          # status 'a', nothing published
    "doubt_75",
    "doubt_50",
    "doubt_25",
    "injured",        # status i / u / n
    "suspended",
    "flagged_other",  # flagged but no percentage published
    "unknown",        # no availability published at all - not the same as fit
)
_MIN_GATE_ROWS = 25
#: 0-3 evidence weights and kappa scale, 4-5 congestion, 6-8 vector scaling,
#: 9 flag-staleness time constant, 10-13 market tilt.
N_PARAMS = 14
_KAPPA_BOUNDS = (0.5, 60.0)
_PRIOR_POOL = 20.0  # pseudo-observations pulling a small cell toward the global rate


def _gate_index(frame: pd.DataFrame) -> np.ndarray:
    chance = frame["chance_next"].to_numpy(dtype=float)
    has = frame["has_chance"].to_numpy(dtype=float) > 0.5
    status = frame["status_flagged"].to_numpy(dtype=float)
    unknown = np.isnan(status)
    flagged = status > 0.5
    injured = frame["status_injured"].to_numpy(dtype=float) > 0.5
    susp = frame["status_suspended"].to_numpy(dtype=float) > 0.5
    idx = np.zeros(len(frame), dtype=int)
    idx = np.where(flagged, 6, idx)
    idx = np.where(has & (chance >= 70), 1, idx)
    idx = np.where(has & (chance >= 40) & (chance < 70), 2, idx)
    idx = np.where(has & (chance < 40), 3, idx)
    idx = np.where(injured, 4, idx)
    idx = np.where(susp, 5, idx)
    idx = np.where(unknown, 7, idx)
    return idx


def _rates(frame: pd.DataFrame, cols: tuple[str, str, str]) -> np.ndarray:
    return np.nan_to_num(
        np.column_stack([frame[c].to_numpy(dtype=float) for c in cols]), nan=0.0
    )


def _cell_keys(frame: pd.DataFrame, *, cold: bool) -> np.ndarray:
    pos = np.nan_to_num(frame["position"].to_numpy(dtype=float), nan=0.0).astype(int)
    depth = np.nan_to_num(frame["depth_rank"].to_numpy(dtype=float), nan=9.0)
    depth = np.clip(depth, 1, 6).astype(int)
    if not cold:
        return pos * 10 + depth
    new = (np.nan_to_num(frame["is_new_signing"].to_numpy(dtype=float), nan=1.0) > 0.5).astype(int)
    return (pos * 10 + depth) * 10 + new


@dataclass
class _Design:
    """Everything the objective needs, precomputed once."""

    c5: np.ndarray
    cs: np.ndarray
    cp: np.ndarray
    cell: np.ndarray
    gate: np.ndarray
    cong: np.ndarray
    horizon: np.ndarray
    z_price: np.ndarray
    z_own: np.ndarray
    y: np.ndarray | None


@dataclass
class _Stage:
    cold: bool
    global_rate: np.ndarray = field(default_factory=lambda: np.full(3, 1 / 3))
    cell_index: dict[int, int] = field(default_factory=dict)
    cell_prior: np.ndarray = field(default_factory=lambda: np.full((1, 3), 1 / 3))
    cell_kappa: np.ndarray = field(default_factory=lambda: np.full(1, 8.0))
    params: np.ndarray = field(default_factory=lambda: np.zeros(N_PARAMS))
    gate_mult: np.ndarray = field(default_factory=lambda: np.ones(len(GATE_NAMES)))
    market_stats: dict[int, tuple[float, float, float, float]] = field(default_factory=dict)

    # -- design ---------------------------------------------------------

    def design(self, frame: pd.DataFrame) -> _Design:
        n_season = np.nan_to_num(frame["n_obs_season"].to_numpy(dtype=float), nan=0.0)
        n5 = np.minimum(n_season, 5.0)
        n_prev = np.nan_to_num(frame["prev_n_obs"].to_numpy(dtype=float), nan=0.0)
        c5 = _rates(frame, ("unavail_rate_5", "cameo_rate_5", "full_rate_5")) * n5[:, None]
        cs = (
            _rates(frame, ("unavail_rate_season", "cameo_rate_season", "full_rate_season"))
            * n_season[:, None]
        )
        cp = (
            _rates(frame, ("prev_unavail_rate", "prev_cameo_rate", "prev_full_rate"))
            * n_prev[:, None]
        )
        keys = _cell_keys(frame, cold=self.cold)
        cell = np.array([self.cell_index.get(int(k), 0) for k in keys], dtype=int)
        cong = np.nan_to_num(frame["euro_congestion"].to_numpy(dtype=float), nan=0.0)
        # How many gameweeks ahead the availability flag has to survive. A warm
        # row is always predicted from its own deadline, so its flag is current;
        # a cold row is predicted from the season-start snapshot, so a GW6 row is
        # reading a five-week-old injury note.
        if self.cold:
            horizon = np.clip(
                np.nan_to_num(frame["gw_idx"].to_numpy(dtype=float), nan=1.0), 1.0, None
            )
        else:
            horizon = np.ones(len(frame))
        z_price, z_own = self._market_z(frame)
        y = frame["bucket"].to_numpy(dtype=int) if "bucket" in frame.columns else None
        return _Design(c5=c5, cs=cs, cp=cp, cell=cell, gate=_gate_index(frame), cong=cong,
                       horizon=horizon, z_price=z_price, z_own=z_own, y=y)

    def _market_z(self, frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        """Price and ownership as within-position z-scores.

        The market is not a competing model, it is an input: FPL prices a player
        by expected involvement, so at a cold start - when there is no
        current-season evidence at all - price is one of the few live signals
        about whether a summer signing walked into the XI. Standardisation
        constants are frozen at fit time so a single-row prediction frame gets
        the same transform as the training cross-section.
        """
        pos = np.nan_to_num(frame["position"].to_numpy(dtype=float), nan=0.0).astype(int)
        price = np.log(np.clip(frame["price_tenths"].to_numpy(dtype=float), 30.0, None))
        own = np.log1p(np.clip(frame["selected_by_pct"].to_numpy(dtype=float), 0.0, None))
        zp = np.zeros(len(frame))
        zo = np.zeros(len(frame))
        for p_id, (mu_p, sd_p, mu_o, sd_o) in self.market_stats.items():
            sel = pos == p_id
            if not sel.any():
                continue
            zp[sel] = (price[sel] - mu_p) / sd_p
            zo[sel] = (own[sel] - mu_o) / sd_o
        return np.nan_to_num(zp, nan=0.0), np.nan_to_num(zo, nan=0.0)

    def _fit_market_stats(self, frame: pd.DataFrame) -> None:
        pos = np.nan_to_num(frame["position"].to_numpy(dtype=float), nan=0.0).astype(int)
        price = np.log(np.clip(frame["price_tenths"].to_numpy(dtype=float), 30.0, None))
        own = np.log1p(np.clip(frame["selected_by_pct"].to_numpy(dtype=float), 0.0, None))
        stats: dict[int, tuple[float, float, float, float]] = {}
        for p_id in np.unique(pos):
            sel = pos == p_id
            stats[int(p_id)] = (
                float(np.nanmean(price[sel])), float(max(np.nanstd(price[sel]), 1e-3)),
                float(np.nanmean(own[sel])), float(max(np.nanstd(own[sel]), 1e-3)),
            )
        self.market_stats = stats

    # -- fitting ---------------------------------------------------------

    def _build_cells(self, frame: pd.DataFrame) -> None:
        y = frame["bucket"].to_numpy(dtype=int)
        onehot = np.zeros((len(y), 3))
        onehot[np.arange(len(y)), y] = 1.0
        self.global_rate = onehot.mean(axis=0)
        keys = _cell_keys(frame, cold=self.cold)
        uniq = sorted({int(k) for k in keys})
        self.cell_index = {k: i for i, k in enumerate(uniq)}
        priors = np.zeros((len(uniq), 3))
        kappas = np.zeros(len(uniq))
        codes = frame["code"].to_numpy()
        for k, i in self.cell_index.items():
            sel = keys == k
            n = int(sel.sum())
            priors[i] = (onehot[sel].sum(axis=0) + _PRIOR_POOL * self.global_rate) / (
                n + _PRIOR_POOL
            )
            kappas[i] = self._moment_kappa(codes[sel], onehot[sel][:, 2], priors[i][2])
        self.cell_prior = priors
        self.cell_kappa = kappas

    @staticmethod
    def _moment_kappa(codes: np.ndarray, full_flag: np.ndarray, m: float) -> float:
        """Beta-binomial concentration by moments, on the 60+ minute margin.

        Between-player spread beyond what independent coin flips would produce is
        real heterogeneity; the concentration is what remains once the sampling
        noise is subtracted. A cell with no excess spread is one where the cell
        mean *is* the answer, and gets heavy shrinkage.
        """
        df = pd.DataFrame({"code": codes, "x": full_flag})
        g = df.groupby("code")["x"]
        n = g.size().to_numpy(dtype=float)
        p = g.mean().to_numpy(dtype=float)
        keep = n >= 2
        n, p = n[keep], p[keep]
        if len(n) < 8 or not (0.02 < m < 0.98):
            return 8.0
        w = n / n.sum()
        v_obs = float((w * (p - m) ** 2).sum())
        v_bin = float(m * (1 - m) * (w / n).sum())
        v_true = max(v_obs - v_bin, 1e-4)
        kappa = m * (1 - m) / v_true - 1.0
        return float(np.clip(kappa, *_KAPPA_BOUNDS))

    def _probs(self, d: _Design, params: np.ndarray, *, refit_gate: bool = False) -> np.ndarray:
        a5, a_season, lam, kscale = np.exp(params[:4])
        if self.cold:
            counts = lam * d.cp
        else:
            counts = a5 * d.c5 + a_season * d.cs + lam * d.cp
        prior = self.cell_prior[d.cell]
        kappa = (self.cell_kappa[d.cell] * kscale)[:, None]
        p = (counts + kappa * prior) / (counts.sum(axis=1, keepdims=True) + kappa)

        tau = np.exp(params[9])
        decay = np.exp(-(d.horizon - 1.0) / tau)
        if refit_gate:
            self.gate_mult = self._fit_gate(p, d, decay)
        # a stale flag decays toward "tells us nothing" (multiplier 1)
        gate = (1.0 - (1.0 - self.gate_mult[d.gate]) * decay)[:, None]
        play = np.clip(p[:, 1:] * gate, 1e-5, None)
        p = np.column_stack([np.clip(1.0 - play.sum(axis=1), 1e-5, None), play])

        cong = d.cong[:, None]
        adj = np.column_stack(
            [np.ones(len(p)), np.exp(params[4] * cong[:, 0]), np.exp(params[5] * cong[:, 0])]
        )
        tilt = np.column_stack(
            [
                np.zeros(len(p)),
                params[10] * d.z_price + params[12] * d.z_own,
                params[11] * d.z_price + params[13] * d.z_own,
            ]
        )
        p = p * adj * np.exp(tilt) * np.exp(params[6:9])[None, :]
        return normalise(p)

    def _fit_gate(self, base: np.ndarray, d: _Design, decay: np.ndarray) -> np.ndarray:
        """What a published flag is empirically worth, per gate bucket."""
        assert d.y is not None
        mult = np.ones(len(GATE_NAMES))
        played = (d.y > 0).astype(float)
        base_play = base[:, 1:].sum(axis=1)
        for g in range(len(GATE_NAMES)):
            sel = d.gate == g
            if sel.sum() < _MIN_GATE_ROWS:
                continue
            # The model says P(play) = base * (1 - (1 - m) * decay(h)). Matching
            # the observed number of appearances to that expectation and solving
            # for m uses every row at its own horizon, instead of throwing away
            # everything but the handful predicted from a fresh flag.
            shortfall = float(base_play[sel].sum() - played[sel].sum())
            leverage = float((base_play[sel] * decay[sel]).sum())
            if leverage <= 1e-6:
                continue
            mult[g] = float(np.clip(1.0 - shortfall / leverage, 0.005, 1.4))
        return mult

    def fit(self, frame: pd.DataFrame) -> _Stage:
        if frame.empty:
            return self
        self._build_cells(frame)
        self._fit_market_stats(frame)
        d = self.design(frame)
        assert d.y is not None
        y = d.y

        def objective(params: np.ndarray) -> float:
            p = self._probs(d, params, refit_gate=True)
            nll = -np.log(p[np.arange(len(y)), y]).mean()
            return float(nll + 1e-3 * np.sum(params[6:9] ** 2))

        x0 = np.zeros(N_PARAMS)
        x0[:4] = [np.log(1.0), np.log(0.6), np.log(0.35), 0.0]
        x0[9] = np.log(2.0)
        bounds = (
            [(-6, 3)] * 4 + [(-2, 2)] * 2 + [(-2, 2)] * 3 + [(-2.0, 4.0)] + [(-1.5, 1.5)] * 4
        )
        res = minimize(objective, x0, method="L-BFGS-B", bounds=bounds,
                       options={"maxiter": 200, "ftol": 1e-9})
        self.params = res.x
        self._probs(d, self.params, refit_gate=True)  # freeze the gate at the optimum
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        return self._probs(self.design(frame), self.params)

    # -- introspection ----------------------------------------------------

    def summary(self) -> dict[str, float]:
        a5, a_season, lam, kscale = np.exp(self.params[:4])
        return {
            "weight_last5": float(a5),
            "weight_season": float(a_season),
            "weight_prev_season": float(lam),
            "kappa_scale": float(kscale),
            "congestion_cameo": float(self.params[4]),
            "congestion_full": float(self.params[5]),
            "flag_halflife_gws": float(np.exp(self.params[9])),
            "tilt_price_full": float(self.params[11]),
            "tilt_own_full": float(self.params[13]),
            "median_kappa": float(np.median(self.cell_kappa)),
            **{f"gate_{n}": float(m) for n, m in zip(GATE_NAMES, self.gate_mult)},
        }


class HierarchicalMinutesModel(BaseMinutesModel):
    """Approach (a): shrink the player toward his position-and-depth cell."""

    name = "hierarchical"

    def __init__(self) -> None:
        super().__init__()
        self.warm = _Stage(cold=False)
        self.cold = _Stage(cold=True)
        self.card = ModelCard(
            name="minutes.hierarchical_eb",
            approach=(
                "Dirichlet-multinomial shrinkage of a player's recency-weighted bucket counts "
                "toward an empirical-Bayes (position x club-depth) prior, with fitted "
                "availability-gate, congestion and vector-scaling corrections; separate "
                "cold-start stage using only prior-season evidence"
            ),
            baseline="per-player previous-season rate (smoothed)",
            metric="multiclass log loss (walk-forward, held-out season)",
            score=measured.LOG_LOSS["hierarchical"],
            baseline_score=measured.LOG_LOSS["prior_season"],
            trained_through=measured.TRAINED_THROUGH,
            notes=measured.notes(
                "hierarchical",
                extra=(
                    "beaten by the GBM on real data at every horizon; kept because "
                    "its 14 parameters are inspectable and it degrades gracefully",
                ),
            ),
        )

    def fit(self, ts: TrainingSet) -> HierarchicalMinutesModel:
        self.warm.fit(ts.warm)
        self.cold.fit(ts.cold_frame)
        self.learn_mean_minutes(ts.frame)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        out = np.zeros((len(frame), 3))
        cold_mask = frame["is_cold_start"].to_numpy(dtype=float) >= 0.5
        if (~cold_mask).any():
            out[~cold_mask] = self.warm.predict(frame[~cold_mask])
        if cold_mask.any():
            out[cold_mask] = self.cold.predict(frame[cold_mask])
        return out

    def summary(self) -> dict[str, dict[str, float]]:
        return {"warm": self.warm.summary(), "cold": self.cold.summary()}
