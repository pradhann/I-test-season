"""Out-of-sample evaluation of the ownership forecast.

Design rules, in order of how badly violating them would invalidate the result:

1. **Split by season, never by row.** Ownership is a panel with enormous
   cross-sectional correlation within a gameweek. A random row split leaks the
   answer through the other 500 players in the same gameweek.
2. **Leave one season out.** With five seasons, every season gets to be the
   held-out one, so the reported number is not a lucky split.
3. **The baselines see exactly what the model sees.** Same rows, same features
   available, same target.
4. **Report calibration, not just MAE.** A point forecast with no honest width
   is unusable for a rank objective: P(rank < threshold) depends on the spread
   of my score against the field, and an overconfident ownership forecast makes
   every differential look safer than it is.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

import numpy as np
import pandas as pd

from fpl_edge.models.ownership import baselines
from fpl_edge.models.ownership.drift import (
    ColdStartParams,
    InSeasonParams,
    NEAR_KNOT_DAYS,
    coldstart_predict,
    fit_coldstart,
    fit_inseason,
    inseason_predict,
)

#: Nominal coverages whose empirical attainment is reported.
COVERAGES = (0.5, 0.8, 0.95)


def mae_pp(truth: np.ndarray, pred: np.ndarray) -> float:
    """Mean absolute error in percentage points of ownership."""
    return float(100.0 * np.mean(np.abs(np.asarray(truth) - np.asarray(pred))))


@dataclass(frozen=True, slots=True)
class Score:
    n: int
    model: float
    persistence: float
    momentum: float
    coverage: dict[str, float]

    @property
    def lift_vs_persistence(self) -> float:
        return float(1.0 - self.model / self.persistence)

    @property
    def lift_vs_momentum(self) -> float:
        return float(1.0 - self.model / self.momentum)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["lift_vs_persistence"] = round(self.lift_vs_persistence, 4)
        d["lift_vs_momentum"] = round(self.lift_vs_momentum, 4)
        return d


def _coverage(truth: np.ndarray, mean: np.ndarray, sd: np.ndarray,
              k: dict[str, float]) -> dict[str, float]:
    err = np.abs(np.asarray(truth) - np.asarray(mean))
    out = {}
    for c in COVERAGES:
        key = f"{c:.2f}"
        if key not in k:
            continue
        out[key] = float(np.mean(err <= float(k[key]) * np.asarray(sd)))
    return out


# --------------------------------------------------------------------------
# in-season
# --------------------------------------------------------------------------


def loso_inseason(panel: pd.DataFrame, *, min_own: float = 0.0) -> tuple[Score, pd.DataFrame]:
    """Leave-one-season-out evaluation of the in-season block.

    Returns the pooled score and a per-season breakdown.
    """
    seasons = sorted(panel["season"].unique())
    errs = {"model": [], "persistence": [], "momentum": []}
    cover_num = {f"{c:.2f}": [] for c in COVERAGES}
    per_season = []
    for hold in seasons:
        train = panel[panel["season"] != hold]
        test = panel[panel["season"] == hold]
        params = fit_inseason(train)
        rows = []
        for gw, g in test.groupby("GW", sort=True):
            own = g["own"].to_numpy()
            if min_own > 0:
                keep = own >= min_own
                if keep.sum() < 5:
                    continue
            mean, sd = inseason_predict(
                params, own, g["own_prev"].to_numpy(), g["flow"].to_numpy(),
                g["pts"].to_numpy(), g["dvalue"].to_numpy(), float(g["w"].iloc[0]),
                total=None,  # the panel is a subset of the field; see inseason_predict
            )
            pers = baselines.persistence(own)
            mom = baselines.transfer_momentum(own, g["flow"].to_numpy())
            truth = g["own_next"].to_numpy()
            sel = own >= min_own if min_own > 0 else np.ones(len(own), dtype=bool)
            rows.append((truth[sel], mean[sel], sd[sel], pers[sel], mom[sel]))
        if not rows:
            continue
        t = np.concatenate([r[0] for r in rows]); m = np.concatenate([r[1] for r in rows])
        s = np.concatenate([r[2] for r in rows]); p = np.concatenate([r[3] for r in rows])
        q = np.concatenate([r[4] for r in rows])
        errs["model"].append(np.abs(t - m)); errs["persistence"].append(np.abs(t - p))
        errs["momentum"].append(np.abs(t - q))
        cov = _coverage(t, m, s, dict(params.scale.interval_k))
        for key, v in cov.items():
            cover_num[key].append((v, len(t)))
        per_season.append({
            "season": hold, "n": int(len(t)),
            "model": mae_pp(t, m), "persistence": mae_pp(t, p), "momentum": mae_pp(t, q),
            **{f"cover_{key}": v for key, v in cov.items()},
        })
    pooled = {k: np.concatenate(v) for k, v in errs.items()}
    coverage = {
        key: float(np.average([x[0] for x in vals], weights=[x[1] for x in vals]))
        for key, vals in cover_num.items() if vals
    }
    score = Score(
        n=int(len(pooled["model"])),
        model=float(100 * pooled["model"].mean()),
        persistence=float(100 * pooled["persistence"].mean()),
        momentum=float(100 * pooled["momentum"].mean()),
        coverage=coverage,
    )
    return score, pd.DataFrame(per_season)


# --------------------------------------------------------------------------
# cold start
# --------------------------------------------------------------------------


def loso_coldstart(pairs: pd.DataFrame) -> tuple[Score, pd.DataFrame]:
    """Leave-one-season-out evaluation of the GW1 cold-start block.

    The momentum baseline here is *drift* momentum, not transfer momentum:
    before the first deadline ``transfers_in_event`` is identically zero for
    every player, so the only extrapolable flow is the movement of the ownership
    series between two polls. Where no earlier snapshot exists in that season,
    momentum falls back to persistence, which is the honest thing to do -- a
    single observation cannot support an extrapolation.
    """
    seasons = sorted(pairs["season"].unique())
    errs = {"model": [], "persistence": [], "momentum": []}
    cover_num = {f"{c:.2f}": [] for c in COVERAGES}
    rows = []
    for hold in seasons:
        train = pairs[pairs["season"] != hold]
        test = pairs[pairs["season"] == hold]
        if train.empty:
            continue
        params = fit_coldstart(train)
        for days, g in test.groupby("days", sort=True):
            days = float(days)
            own = g["own"].to_numpy()
            truth = g["own_true"].to_numpy()
            drift_rate = g["drift_rate"].to_numpy() if "drift_rate" in g else None
            mean, sd = coldstart_predict(params, own, g["ep"].to_numpy(),
                                         g["flag"].to_numpy(), days, drift_rate)
            pers = baselines.persistence(own)
            before = g["own_before"].to_numpy(dtype=float) if "own_before" in g else None
            if before is None or not np.isfinite(before).any():
                mom = pers
                mom_kind = "fallback:persistence"
            else:
                gap = float(g["days_before"].iloc[0]) - days
                ok = np.isfinite(before)
                mom = pers.copy()
                mom[ok] = baselines.drift_momentum(own[ok], before[ok], gap, days)
                mom_kind = f"drift from T-{float(g['days_before'].iloc[0]):.2f}d"
            errs["model"].append(np.abs(truth - mean))
            errs["persistence"].append(np.abs(truth - pers))
            errs["momentum"].append(np.abs(truth - mom))
            cov = _coverage(truth, mean, sd, dict(params.interval_k))
            for key, v in cov.items():
                cover_num[key].append((v, len(truth)))
            hi = own >= 0.01
            rows.append({
                "season": hold, "days_to_deadline": round(days, 2), "n": int(len(g)),
                "regime": "near" if days <= NEAR_KNOT_DAYS else "far",
                "model": mae_pp(truth, mean), "persistence": mae_pp(truth, pers),
                "momentum": mae_pp(truth, mom), "momentum_kind": mom_kind,
                "model_own_ge_1pct": mae_pp(truth[hi], mean[hi]),
                "persistence_own_ge_1pct": mae_pp(truth[hi], pers[hi]),
                **{f"cover_{key}": v for key, v in cov.items()},
            })
    pooled = {k: np.concatenate(v) for k, v in errs.items()}
    coverage = {
        key: float(np.average([x[0] for x in vals], weights=[x[1] for x in vals]))
        for key, vals in cover_num.items() if vals
    }
    score = Score(
        n=int(len(pooled["model"])),
        model=float(100 * pooled["model"].mean()),
        persistence=float(100 * pooled["persistence"].mean()),
        momentum=float(100 * pooled["momentum"].mean()),
        coverage=coverage,
    )
    return score, pd.DataFrame(rows)


def coldstart_uncertainty_curve(pairs: pd.DataFrame) -> pd.DataFrame:
    """How far ownership actually moves, by horizon and by ownership band.

    This is the table the GW1 forecast has to be honest about. It is measured,
    not assumed.
    """
    out = []
    bands = [(0.0, 0.01, "<1%"), (0.01, 0.05, "1-5%"), (0.05, 0.15, "5-15%"),
             (0.15, 1.01, ">15%")]
    for (season, days), g in pairs.groupby(["season", "days"], sort=True):
        for lo, hi, label in bands:
            m = (g["own"] >= lo) & (g["own"] < hi)
            if m.sum() < 3:
                continue
            move = 100.0 * (g["own_true"] - g["own"])[m]
            out.append({
                "season": season, "days_to_deadline": round(float(days), 2), "band": label,
                "n": int(m.sum()), "mean_abs_move_pp": float(move.abs().mean()),
                "p90_abs_move_pp": float(move.abs().quantile(0.9)),
                "max_abs_move_pp": float(move.abs().max()),
                "mean_signed_move_pp": float(move.mean()),
            })
    return pd.DataFrame(out)


# --------------------------------------------------------------------------
# captaincy (simulated field -- see simulate.py for why)
# --------------------------------------------------------------------------


def evaluate_captaincy(
    pairs: pd.DataFrame,
    *,
    n_managers: int = 4000,
    kappa_grid: Iterable[float] = tuple(np.round(np.arange(0.06, 0.62, 0.02), 3)),
    seed: int = 20260818,
) -> tuple[pd.DataFrame, dict]:
    """Leave-one-season-out captaincy evaluation against a simulated field.

    The ownership vectors, prices and positions are real -- they come from the
    committed pre-deadline snapshots -- so the *shape* of the problem is real
    even though the armbands are simulated. ``kappa`` is fitted on the training
    seasons' simulated fields and applied to the held-out one, so the
    concentration parameter is never fitted on the season it is scored against.

    Returns the per-season table and a pooled summary. Every number carries the
    ``simulated`` caveat; see the module docstring of ``simulate.py``.
    """
    from fpl_edge.models.ownership import captaincy as cap
    from fpl_edge.models.ownership.simulate import simulate_field

    universe = pairs[pairs["days"] <= 5.0] if (pairs["days"] <= 5.0).any() else pairs
    seasons = sorted(universe["season"].unique())
    fields: dict[str, tuple] = {}
    for i, season in enumerate(seasons):
        g = universe[universe["season"] == season].sort_values("code")
        own = g["own_true"].to_numpy(dtype=float)
        price = g["now_cost"].to_numpy(dtype=float)
        pos = g["element_type"].to_numpy(dtype=int)
        now = simulate_field(own, price, pos, n_managers=n_managers, seed=seed + i)
        # "last gameweek": the same field with a different private-noise draw,
        # which is what a persistence baseline would have observed.
        before = simulate_field(own, price, pos, n_managers=n_managers, seed=seed + 500 + i)
        appeal = cap.appeal_score(price, pos)
        fields[season] = (now, before, appeal)

    def mae_for(season: str, kappa: float) -> float:
        now, _before, appeal = fields[season]
        pred = cap.captaincy_share(
            now.ownership, appeal, params=cap.CaptaincyParams(kappa=kappa)
        )
        return float(100.0 * np.mean(np.abs(now.captaincy - pred)))

    def blended_mae(season: str, kappa: float, lam: float) -> float:
        now, before, appeal = fields[season]
        pred = cap.captaincy_share(
            now.ownership, appeal, params=cap.CaptaincyParams(kappa=kappa)
        )
        mixed = cap.blend_with_observed(pred, before.captaincy, lam)
        return float(100.0 * np.mean(np.abs(now.captaincy - mixed)))

    lam_grid = tuple(np.round(np.arange(0.0, 1.01, 0.05), 2))
    rows = []
    for hold in seasons:
        train = [s for s in seasons if s != hold]
        best_k = min(kappa_grid, key=lambda k: float(np.mean([mae_for(s, k) for s in train])))
        best_lam = min(
            lam_grid,
            key=lambda L: float(np.mean([blended_mae(s, best_k, L) for s in train])),
        )
        now, before, appeal = fields[hold]
        pred = cap.captaincy_share(
            now.ownership, appeal, params=cap.CaptaincyParams(kappa=best_k)
        )
        blended = cap.blend_with_observed(pred, before.captaincy, float(best_lam))
        prop = baselines.captaincy_proportional(now.start_share)
        pers = baselines.captaincy_persistence(before.captaincy)
        rows.append({
            "season": hold, "n": int(now.ownership.size), "kappa": float(best_k),
            "blend_weight": float(best_lam),
            "model_gw1_no_prior": float(100 * np.mean(np.abs(now.captaincy - pred))),
            "model_with_prior": float(100 * np.mean(np.abs(now.captaincy - blended))),
            "proportional": float(100 * np.mean(np.abs(now.captaincy - prop))),
            "persistence": float(100 * np.mean(np.abs(now.captaincy - pers))),
            "top_share_true": float(now.captaincy.max()),
            "top_share_model": float(pred.max()),
        })
    table = pd.DataFrame(rows)
    summary = {
        "basis": "simulated field; no public captaincy-share series exists",
        "n_managers": int(n_managers),
        "n_seasons": int(len(seasons)),
        "n_players_scored": int(table["n"].sum()),
        "model_gw1_no_prior": float(table["model_gw1_no_prior"].mean()),
        "model_with_prior": float(table["model_with_prior"].mean()),
        "persistence": float(table["persistence"].mean()),
        "proportional": float(table["proportional"].mean()),
        "kappa_fitted": [float(k) for k in table["kappa"]],
        "blend_weight_fitted": [float(k) for k in table["blend_weight"]],
    }
    summary["lift_vs_persistence"] = round(
        1 - summary["model_with_prior"] / summary["persistence"], 4)
    summary["lift_vs_proportional"] = round(
        1 - summary["model_gw1_no_prior"] / summary["proportional"], 4)
    return table, summary
