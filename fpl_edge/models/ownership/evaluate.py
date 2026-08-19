"""Walk-forward evaluation of the ownership forecast.

``backtest.py`` scores the model leave-one-season-out, which answers "does this
functional form generalise across seasons". That is a fair question and it is
not the one an operator needs answered. The operator's question is "if I had
been running this model live, week by week, what would it have done" -- and a
leave-one-season-out fit for 2023-24 has seen 2024-25, which had not happened.

So this module runs the strict protocol instead:

**In-season.** For test season ``s`` and gameweek ``g``, the coefficients are
fitted on every transition from a season strictly before ``s``, plus every
transition in ``s`` up to and including the one that *lands* on ``g``. The
target being predicted is ownership at the ``g -> g+1`` deadline, so the fitting
set stops at the ``g-1 -> g`` transition and the predicted transition is never
in it. The training window expands as the season progresses, which is what a
live model does.

**Cold start.** GW1 has no earlier gameweek in its own season, so the fold is
the season: test season ``s`` is predicted from a fit on pre-deadline snapshots
of seasons strictly before ``s``. The first season in the panel is therefore
untestable and is reported as such rather than silently dropped.

**Captaincy.** The concentration parameter is fitted on seasons strictly before
the test season. The scoring field is simulated (there is no public per-player
captaincy series), so this measures functional form, not manager behaviour, and
every row says so.

Baselines are the two an ownership model has to beat: persistence, and naive
momentum extrapolation. Momentum is transfer flow in season and drift between
polls before the first deadline, because ``transfers_in_event`` is identically
zero until the first deadline passes.

Run::

    uv run python -m fpl_edge.models.ownership.evaluate
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.models.ownership import baselines, panel
from fpl_edge.models.ownership.drift import (
    COLDSTART_BOUNDS,
    COLDSTART_FEATURES,
    NEAR_KNOT_DAYS,
    ColdStartParams,
    coldstart_predict,
    fit_coldstart,
    fit_inseason,
    inseason_predict,
)

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "models"

#: Nominal interval coverage reported alongside the point error. A forecast with
#: an honest MAE and a dishonest width is still unusable for a rank objective.
REPORTED_COVERAGE = 0.80

#: Minimum training rows before a fold is scored. Below this the fit is noise
#: and reporting it as an out-of-sample number would flatter the baselines.
MIN_TRAIN_ROWS = 2_000


def mae_pp(truth: np.ndarray, pred: np.ndarray) -> float:
    """Mean absolute error in percentage points of ownership share."""
    return float(100.0 * np.mean(np.abs(np.asarray(truth) - np.asarray(pred))))


@dataclass(frozen=True)
class Fold:
    """One walk-forward step: what was trained on, what was predicted."""

    regime: str
    test_season: str
    test_key: str          # gameweek label, or days-to-deadline for cold start
    train_seasons: tuple[str, ...]
    n_train: int
    n_test: int
    truth: np.ndarray
    model: np.ndarray
    sd: np.ndarray
    persistence: np.ndarray
    momentum: np.ndarray
    momentum_kind: str
    k80: float = float("nan")

    def row(self, k80: float | None = None) -> dict:
        k80 = self.k80 if k80 is None else k80
        err = np.abs(self.truth - self.model)
        hi = self.truth >= 0.01
        return {
            "regime": self.regime,
            "test_season": self.test_season,
            "test_key": self.test_key,
            "train_seasons": "+".join(self.train_seasons),
            "n_train": self.n_train,
            "n": self.n_test,
            "model": mae_pp(self.truth, self.model),
            "persistence": mae_pp(self.truth, self.persistence),
            "momentum": mae_pp(self.truth, self.momentum),
            "momentum_kind": self.momentum_kind,
            "model_own_ge_1pct": mae_pp(self.truth[hi], self.model[hi]) if hi.any() else np.nan,
            "persistence_own_ge_1pct": (
                mae_pp(self.truth[hi], self.persistence[hi]) if hi.any() else np.nan
            ),
            "cover_0.80": float(np.mean(err <= k80 * self.sd)),
        }


# --------------------------------------------------------------------------
# in-season: expanding window over (season, gameweek)
# --------------------------------------------------------------------------


def walk_forward_inseason(
    frame: pd.DataFrame | None = None, *, min_train_rows: int = MIN_TRAIN_ROWS
) -> tuple[pd.DataFrame, list[Fold]]:
    """Expanding-window walk-forward over every (season, gameweek) transition.

    The fold predicting the ``g -> g+1`` move trains on transitions ending at or
    before ``g``, so the gameweek being predicted contributes nothing to the fit.
    """
    frame = panel.attach_field_size(panel.load_inseason_panel()) if frame is None else frame
    seasons = sorted(frame["season"].unique())
    k80 = _k80_inseason(frame)
    folds: list[Fold] = []
    for i, season in enumerate(seasons):
        prior = frame[frame["season"].isin(seasons[:i])]
        cur = frame[frame["season"] == season]
        for gw, test in cur.groupby("GW", sort=True):
            # Strictly-earlier transitions in this season, plus every earlier
            # season. `own_next` for GW g-1 is ownership at GW g, which is an
            # input to this fold rather than its target -- so it is in-sample.
            train = pd.concat([prior, cur[cur["GW"] < int(gw)]], ignore_index=True)
            if len(train) < min_train_rows:
                continue
            params = fit_inseason(train)
            own = test["own"].to_numpy()
            mean, sd = inseason_predict(
                params, own, test["own_prev"].to_numpy(), test["flow"].to_numpy(),
                test["pts"].to_numpy(), test["dvalue"].to_numpy(),
                float(test["w"].iloc[0]),
                # The panel is an ownership-floored subset of the player set, so
                # its shares do not sum to 15 and projecting them to their own
                # current sum would cancel the dilution term. See
                # drift.inseason_predict.
                total=None,
            )
            same_season = (f"{season}<GW{int(gw)}",) if (cur["GW"] < int(gw)).any() else ()
            folds.append(Fold(
                regime="in_season",
                test_season=str(season),
                test_key=f"GW{int(gw)}->{int(gw) + 1}",
                train_seasons=tuple(str(s) for s in seasons[:i]) + same_season,
                n_train=int(len(train)),
                n_test=int(len(test)),
                truth=test["own_next"].to_numpy(),
                model=mean, sd=sd,
                persistence=baselines.persistence(own),
                momentum=baselines.transfer_momentum(own, test["flow"].to_numpy()),
                momentum_kind="transfer flow carried forward",
                k80=params.scale.k(REPORTED_COVERAGE),
            ))
    return pd.DataFrame([f.row() for f in folds]), folds


def _k80_inseason(frame: pd.DataFrame) -> float:
    return float(fit_inseason(frame).scale.k(REPORTED_COVERAGE))


# --------------------------------------------------------------------------
# cold start: season folds, because GW1 has no earlier gameweek
# --------------------------------------------------------------------------


def walk_forward_coldstart(
    pairs: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[Fold], list[dict]]:
    """Predict each season's GW1 from pre-deadline snapshots of earlier seasons.

    Returns the scored folds and, separately, the seasons that could not be
    scored at all. The first season in the panel has nothing to train on, and a
    walk-forward evaluator that silently drops it would overstate its coverage.
    """
    pairs = panel.load_coldstart_pairs() if pairs is None else pairs
    seasons = sorted(pairs["season"].unique())
    folds: list[Fold] = []
    untestable: list[dict] = []
    for i, season in enumerate(seasons):
        train = pairs[pairs["season"].isin(seasons[:i])]
        test = pairs[pairs["season"] == season]
        if train.empty:
            untestable.append({
                "test_season": str(season),
                "reason": "first season in the panel; no earlier pre-deadline snapshot exists",
                "n_rows_unscored": int(len(test)),
            })
            continue
        params = fit_coldstart(train)
        k80 = params.k(REPORTED_COVERAGE)
        for days, g in test.groupby("days", sort=True):
            days = float(days)
            own = g["own"].to_numpy()
            drift = g["drift_rate"].to_numpy() if "drift_rate" in g else None
            mean, sd = coldstart_predict(
                params, own, g["ep"].to_numpy(), g["flag"].to_numpy(), days, drift
            )
            pers = baselines.persistence(own)
            mom, kind = _coldstart_momentum(g, own, pers, days)
            folds.append(Fold(
                regime="cold_start_near" if days <= NEAR_KNOT_DAYS else "cold_start_far",
                test_season=str(season),
                test_key=f"T-{days:.2f}d",
                train_seasons=tuple(str(s) for s in seasons[:i]),
                n_train=int(len(train)),
                n_test=int(len(g)),
                truth=g["own_true"].to_numpy(),
                model=mean, sd=sd, persistence=pers, momentum=mom, momentum_kind=kind,
                k80=k80,
            ))
    return pd.DataFrame([f.row() for f in folds]), folds, untestable


def _coldstart_momentum(
    g: pd.DataFrame, own: np.ndarray, pers: np.ndarray, days: float
) -> tuple[np.ndarray, str]:
    """Naive drift extrapolation, or persistence when only one poll exists."""
    if "own_before" not in g:
        return pers, "fallback:persistence (no earlier poll)"
    before = g["own_before"].to_numpy(dtype=float)
    ok = np.isfinite(before)
    if not ok.any():
        return pers, "fallback:persistence (no earlier poll)"
    gap = float(g["days_before"].iloc[0]) - days
    if gap <= 0:
        return pers, "fallback:persistence (earlier poll is not earlier)"
    mom = pers.copy()
    mom[ok] = baselines.drift_momentum(own[ok], before[ok], gap, days)
    return mom, f"drift from T-{float(g['days_before'].iloc[0]):.2f}d"


# --------------------------------------------------------------------------
# captaincy: parameter fitted on strictly earlier seasons
# --------------------------------------------------------------------------


def walk_forward_captaincy(
    pairs: pd.DataFrame | None = None, *, n_managers: int = 4_000, seed: int = 20260818
) -> pd.DataFrame:
    """Captaincy share against a simulated field, fitted forward only.

    The ownership vectors, prices and positions are real. The armbands are not:
    no public per-player captaincy series exists, so this scores functional form
    against a field simulated from the real ownership. Every row carries the
    caveat in its ``basis`` column so a reader cannot mistake it for a
    measurement of manager behaviour.
    """
    from fpl_edge.models.ownership import captaincy as cap
    from fpl_edge.models.ownership.simulate import simulate_field

    pairs = panel.load_coldstart_pairs() if pairs is None else pairs
    universe = pairs[pairs["days"] <= 5.0] if (pairs["days"] <= 5.0).any() else pairs
    seasons = sorted(universe["season"].unique())
    fields: dict[str, tuple] = {}
    for i, season in enumerate(seasons):
        g = universe[universe["season"] == season].sort_values("code")
        own = g["own_true"].to_numpy(dtype=float)
        price = g["now_cost"].to_numpy(dtype=float)
        pos = g["element_type"].to_numpy(dtype=int)
        fields[season] = (
            simulate_field(own, price, pos, n_managers=n_managers, seed=seed + i),
            simulate_field(own, price, pos, n_managers=n_managers, seed=seed + 500 + i),
            cap.appeal_score(price, pos),
        )

    def mae(season: str, kappa: float, lam: float | None = None) -> float:
        now, before, appeal = fields[season]
        pred = cap.captaincy_share(now.ownership, appeal,
                                   params=cap.CaptaincyParams(kappa=kappa))
        if lam is not None:
            pred = cap.blend_with_observed(pred, before.captaincy, lam)
        return float(100.0 * np.mean(np.abs(now.captaincy - pred)))

    kappa_grid = tuple(np.round(np.arange(0.06, 0.62, 0.02), 3))
    lam_grid = tuple(np.round(np.arange(0.0, 1.01, 0.05), 2))
    rows = []
    for i, hold in enumerate(seasons):
        train = seasons[:i]
        if not train:
            continue
        best_k = min(kappa_grid, key=lambda k: float(np.mean([mae(s, k) for s in train])))
        best_lam = min(
            lam_grid, key=lambda L: float(np.mean([mae(s, best_k, L) for s in train]))
        )
        now, before, appeal = fields[hold]
        pred = cap.captaincy_share(now.ownership, appeal,
                                   params=cap.CaptaincyParams(kappa=best_k))
        blended = cap.blend_with_observed(pred, before.captaincy, float(best_lam))
        rows.append({
            "basis": "SIMULATED field; no public captaincy series exists",
            "test_season": hold, "train_seasons": "+".join(train),
            "n": int(now.ownership.size), "kappa": float(best_k),
            "blend_weight": float(best_lam),
            "model_no_prior": float(100 * np.mean(np.abs(now.captaincy - pred))),
            "model_with_prior": float(100 * np.mean(np.abs(now.captaincy - blended))),
            "proportional": float(
                100 * np.mean(np.abs(
                    now.captaincy - baselines.captaincy_proportional(now.start_share)))),
            "persistence": float(
                100 * np.mean(np.abs(
                    now.captaincy - baselines.captaincy_persistence(before.captaincy)))),
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# coefficient sign audit
# --------------------------------------------------------------------------


def coefficient_signs(params: ColdStartParams) -> pd.DataFrame:
    """Every cold-start coefficient against the sign its mechanism dictates.

    The near-horizon block is identified by two snapshots. Five free parameters
    on two correlated snapshots will happily report that injured players get
    bought; the constrained fit exists to stop that, and this table is how a
    reader checks it did.
    """
    rows = []
    for block in ("coef_near", "coef_far", "coef_near_nodrift", "coef_far_nodrift"):
        coef = getattr(params, block)
        if not coef:
            continue
        for name, value, (lo, hi) in zip(COLDSTART_FEATURES, coef, COLDSTART_BOUNDS):
            rows.append({
                "block": block, "feature": name, "coef": float(value),
                "required_sign": (
                    "non-negative" if lo == 0.0
                    else "non-positive" if hi == 0.0 else "unconstrained"
                ),
                "ok": bool(lo - 1e-9 <= value <= hi + 1e-9),
            })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------
# pooled reporting
# --------------------------------------------------------------------------


def _pool(folds: list[Fold], regimes: tuple[str, ...]) -> dict:
    sel = [f for f in folds if f.regime in regimes]
    if not sel:
        return {}
    t = np.concatenate([f.truth for f in sel])
    m = np.concatenate([f.model for f in sel])
    p = np.concatenate([f.persistence for f in sel])
    q = np.concatenate([f.momentum for f in sel])
    out = {
        "regimes": "+".join(regimes),
        "n_folds": len(sel),
        "n": int(len(t)),
        "model_mae_pp": mae_pp(t, m),
        "persistence_mae_pp": mae_pp(t, p),
        "momentum_mae_pp": mae_pp(t, q),
    }
    out["lift_vs_persistence"] = 1.0 - out["model_mae_pp"] / out["persistence_mae_pp"]
    out["lift_vs_momentum"] = 1.0 - out["model_mae_pp"] / out["momentum_mae_pp"]
    # Paired sign test: on how many individual rows is the model closer? MAE can
    # be won by a handful of large errors, and the operator cares about the
    # typical row too.
    out["rows_model_closer_than_persistence"] = float(
        np.mean(np.abs(t - m) < np.abs(t - p))
    )
    return out


def walk_forward(
    frame: pd.DataFrame | None = None,
    pairs: pd.DataFrame | None = None,
    *,
    min_train_rows: int = MIN_TRAIN_ROWS,
) -> tuple[pd.DataFrame, dict]:
    """Run every walk-forward fold and pool them.

    This is the family's entry point: one call, a per-fold table and a summary
    whose numbers are the ones quoted in ``docs/models/ownership.md``.
    """
    ins_table, ins_folds = walk_forward_inseason(frame, min_train_rows=min_train_rows)
    cold_table, cold_folds, untestable = walk_forward_coldstart(pairs)
    table = pd.concat([ins_table, cold_table], ignore_index=True)
    folds = ins_folds + cold_folds
    summary = {
        "protocol": (
            "in-season: expanding window, fitted on transitions ending at or before the "
            "predicted gameweek; cold start: fitted on strictly earlier seasons only"
        ),
        "in_season": _pool(folds, ("in_season",)),
        "cold_start": _pool(folds, ("cold_start_near", "cold_start_far")),
        "cold_start_near_deadline": _pool(folds, ("cold_start_near",)),
        "cold_start_far": _pool(folds, ("cold_start_far",)),
        "untestable": untestable,
    }
    return table, summary


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-docs", action="store_true")
    ap.add_argument("--skip-captaincy", action="store_true")
    args = ap.parse_args(argv)

    pd.set_option("display.width", 220)
    table, summary = walk_forward()
    print("WALK-FORWARD OWNERSHIP EVALUATION")
    print(f"  {summary['protocol']}\n")
    print(table.to_string(index=False))

    print("\nPOOLED")
    for key in ("in_season", "cold_start", "cold_start_near_deadline", "cold_start_far"):
        s = summary[key]
        if not s:
            continue
        print(f"  {key:26s} n={s['n']:>7,d} folds={s['n_folds']:>3d}  "
              f"model {s['model_mae_pp']:.4f}pp  persistence {s['persistence_mae_pp']:.4f}pp "
              f"({s['lift_vs_persistence']:+.1%})  momentum {s['momentum_mae_pp']:.4f}pp "
              f"({s['lift_vs_momentum']:+.1%})")
    for u in summary["untestable"]:
        print(f"  UNTESTABLE  {u['test_season']}: {u['reason']} "
              f"({u['n_rows_unscored']:,} rows unscored)")

    print("\nCOLD-START COEFFICIENT SIGNS (fitted on the full panel)")
    signs = coefficient_signs(fit_coldstart(panel.load_coldstart_pairs()))
    print(signs.to_string(index=False))
    if not bool(signs["ok"].all()):
        print("  SIGN VIOLATION: a coefficient escaped its domain constraint")
        return 1

    if not args.skip_captaincy:
        print("\nCAPTAINCY (simulated field)")
        print(walk_forward_captaincy().round(4).to_string(index=False))

    if args.write_docs:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(DOCS_DIR / "ownership_walk_forward.csv", index=False)
        (DOCS_DIR / "ownership_walk_forward.json").write_text(
            json.dumps(summary, indent=1, default=float) + "\n"
        )
        print(f"\nwrote {DOCS_DIR / 'ownership_walk_forward.csv'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
