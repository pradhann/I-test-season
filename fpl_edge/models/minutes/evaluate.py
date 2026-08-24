"""Walk-forward evaluation of the minutes models against the required baselines.

Protocol
--------
Train on every season strictly before the test season; test on the held-out
season, gameweek by gameweek, each from a Snapshot taken at that gameweek's own
deadline. No test-season row ever contributes to a fitted parameter, and no
feature is computed from a Snapshot later than the deadline it is predicting.

Metrics are multiclass log loss (the thing we actually care about: the minutes
distribution feeds a points simulation, and a mis-priced tail is a mis-priced
captaincy call) and the multiclass Brier score (bounded, so a single confident
mistake cannot dominate the comparison). Calibration is reported separately,
because a model can win on log loss while being systematically overconfident
about nailed starters, which is exactly the failure mode that ruins a season.

Run::

    uv run python -m fpl_edge.models.minutes.evaluate

STATUS: EVALUATION HARNESS, run via `python -m` (see docs/models/); not in the production import closure and not expected to be. It exists to mint the committed evidence CSVs, not to serve requests.
"""

from __future__ import annotations

import argparse
import datetime as dt
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.models.minutes.base import normalise
from fpl_edge.models.minutes.baselines import (
    BaseRateBaseline,
    ChanceOfPlayingBaseline,
    PriorSeasonRateBaseline,
)
from fpl_edge.models.minutes.dataset import FIXTURE_DIR, load_csv_warehouse
from fpl_edge.models.minutes.features import attach_labels, build_feature_frame
from fpl_edge.models.minutes.gbm import GBMMinutesModel
from fpl_edge.models.minutes.hierarchical import HierarchicalMinutesModel
from fpl_edge.models.minutes.training import LABEL_LAG, TrainingSetBuilder
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, Season

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "models"
CLASS_NAMES = ("unavailable", "cameo", "full")

#: Model order in every report. Baselines first so the comparison reads top-down.
MODEL_ORDER = ("base_rate", "prior_season", "fpl_chance", "hierarchical", "gbm")


def build_models() -> dict[str, object]:
    return {
        "base_rate": BaseRateBaseline(),
        "prior_season": PriorSeasonRateBaseline(),
        "fpl_chance": ChanceOfPlayingBaseline(),
        "hierarchical": HierarchicalMinutesModel(),
        "gbm": GBMMinutesModel(),
    }


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def multiclass_log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = normalise(p)
    return float(-np.log(p[np.arange(len(y)), y]).mean())


def multiclass_brier(y: np.ndarray, p: np.ndarray) -> float:
    """Mean squared error summed over the three classes. Range 0-2."""
    p = normalise(p)
    onehot = np.zeros_like(p)
    onehot[np.arange(len(y)), y] = 1.0
    return float(((p - onehot) ** 2).sum(axis=1).mean())


def reliability_table(
    y: np.ndarray, p: np.ndarray, class_idx: int, *, n_bins: int = 10
) -> pd.DataFrame:
    """Binned predicted vs observed frequency for one class."""
    pred = normalise(p)[:, class_idx]
    obs = (y == class_idx).astype(float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    which = np.clip(np.digitize(pred, edges[1:-1], right=False), 0, n_bins - 1)
    rows = []
    for b in range(n_bins):
        sel = which == b
        rows.append(
            {
                "class": CLASS_NAMES[class_idx],
                "bin_lower": round(float(edges[b]), 3),
                "bin_upper": round(float(edges[b + 1]), 3),
                "n": int(sel.sum()),
                "mean_pred": round(float(pred[sel].mean()), 4) if sel.any() else np.nan,
                "obs_freq": round(float(obs[sel].mean()), 4) if sel.any() else np.nan,
            }
        )
    return pd.DataFrame(rows)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, class_idx: int,
                               *, n_bins: int = 10) -> float:
    tab = reliability_table(y, p, class_idx, n_bins=n_bins).dropna()
    if tab.empty:
        return float("nan")
    w = tab["n"] / tab["n"].sum()
    return float((w * (tab["mean_pred"] - tab["obs_freq"]).abs()).sum())


# --------------------------------------------------------------------------
# walk-forward
# --------------------------------------------------------------------------


@dataclass
class Fold:
    test_season: str
    train_seasons: tuple[str, ...]
    predictions: pd.DataFrame
    models: dict[str, object]


def run_fold(
    wh: Warehouse,
    test_season: Season,
    train_seasons: tuple[Season, ...],
    *,
    catalog_at: dt.datetime,
    models: dict[str, object] | None = None,
    train_gws: list[int] | None = None,
    test_gws: list[int] | None = None,
) -> Fold:
    catalog = wh.snapshot_at(catalog_at)
    builder = TrainingSetBuilder(wh.snapshot_at, catalog)
    ts = builder.build(list(train_seasons), gws=train_gws)
    models = models or build_models()
    for m in models.values():
        m.fit(ts)  # type: ignore[attr-defined]

    ev = builder.deadlines(test_season)
    if test_gws is not None:
        ev = ev[ev["gw"].isin(test_gws)]
    out: list[pd.DataFrame] = []
    for row in ev.itertuples():
        deadline = pd.Timestamp(row.deadline_utc).to_pydatetime()
        frame = build_feature_frame(
            wh.snapshot_at(deadline), test_season, [GwId(int(row.gw))]
        )
        labelled = attach_labels(frame, wh.snapshot_at(deadline + LABEL_LAG), test_season)
        if labelled.empty:
            continue
        ids = labelled[["season", "code", "fixture_id", "gw", "is_cold_start", "bucket"]]
        for name, model in models.items():
            p = normalise(model.predict_proba(labelled))  # type: ignore[attr-defined]
            out.append(
                ids.assign(model=name, p_unavailable=p[:, 0], p_cameo=p[:, 1], p_full=p[:, 2])
            )
    preds = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return Fold(test_season=test_season, train_seasons=train_seasons, predictions=preds,
                models=models)


def _metrics(df: pd.DataFrame) -> dict[str, float]:
    y = df["bucket"].to_numpy(dtype=int)
    p = df[["p_unavailable", "p_cameo", "p_full"]].to_numpy(dtype=float)
    return {
        "n": len(df),
        "log_loss": round(multiclass_log_loss(y, p), 4),
        "brier": round(multiclass_brier(y, p), 4),
        "ece_full": round(expected_calibration_error(y, p, 2), 4),
        "ece_unavailable": round(expected_calibration_error(y, p, 0), 4),
    }


def summarise(fold: Fold) -> pd.DataFrame:
    rows = []
    preds = fold.predictions
    slices = {
        "all": preds,
        "gw1_cold_start": preds[preds["is_cold_start"] >= 0.5],
        "warm": preds[preds["is_cold_start"] < 0.5],
    }
    for slice_name, sl in slices.items():
        if sl.empty:
            continue
        for name in MODEL_ORDER:
            part = sl[sl["model"] == name]
            if part.empty:
                continue
            rows.append(
                {
                    "test_season": fold.test_season,
                    "train_seasons": "+".join(fold.train_seasons),
                    "slice": slice_name,
                    "model": name,
                    **_metrics(part),
                }
            )
    return pd.DataFrame(rows)


def calibration_frame(fold: Fold) -> pd.DataFrame:
    rows = []
    for name in MODEL_ORDER:
        part = fold.predictions[fold.predictions["model"] == name]
        if part.empty:
            continue
        y = part["bucket"].to_numpy(dtype=int)
        p = part[["p_unavailable", "p_cameo", "p_full"]].to_numpy(dtype=float)
        for class_idx in (0, 2):
            tab = reliability_table(y, p, class_idx)
            tab.insert(0, "model", name)
            tab.insert(0, "test_season", fold.test_season)
            rows.append(tab)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def walk_forward(
    wh: Warehouse, seasons: list[Season], *, catalog_at: dt.datetime
) -> tuple[pd.DataFrame, pd.DataFrame, list[Fold]]:
    """Every season after the first is a test fold trained on all earlier ones."""
    summaries, calibrations, folds = [], [], []
    for i in range(1, len(seasons)):
        fold = run_fold(
            wh, seasons[i], tuple(seasons[:i]), catalog_at=catalog_at
        )
        if fold.predictions.empty:
            continue
        folds.append(fold)
        summaries.append(summarise(fold))
        calibrations.append(calibration_frame(fold))
    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    calib = pd.concat(calibrations, ignore_index=True) if calibrations else pd.DataFrame()
    return summary, calib, folds


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixtures", type=Path, default=FIXTURE_DIR,
                    help="directory of committed CSV tables")
    ap.add_argument("--warehouse", type=Path, default=None,
                    help="evaluate against a real warehouse instead of the fixtures")
    ap.add_argument("--seasons", nargs="*", default=None)
    ap.add_argument("--catalog-at", default="2026-08-18T12:00:00+00:00")
    ap.add_argument("--write-docs", action="store_true")
    ap.add_argument("--tag", default="", help="filename suffix for the written CSVs")
    args = ap.parse_args(argv)

    tmp = None
    if args.warehouse:
        wh = Warehouse(args.warehouse, read_only=True)
        source = str(args.warehouse)
    else:
        tmp = tempfile.TemporaryDirectory()
        wh = load_csv_warehouse(args.fixtures, Path(tmp.name) / "fixtures.duckdb")
        source = str(args.fixtures)

    catalog_at = dt.datetime.fromisoformat(args.catalog_at)
    seasons = args.seasons
    if not seasons:
        # Seasons we can score are the ones with realised minutes. Deadlines do
        # not gate this: dim_event only ever carries the live season, and
        # historical deadlines are reconstructed from the fixture list.
        res = wh.snapshot_at(catalog_at).table("fact_player_fixture")
        seasons = sorted(set(res["season"])) if not res.empty else []
    print(f"source: {source}\nseasons with results: {seasons}\n")
    cov = wh.snapshot_at(catalog_at).table("fact_player_state")
    if not cov.empty:
        known = float(cov["status"].notna().mean())
        chance = float(cov["chance_of_playing_next_round"].notna().mean())
        print(f"availability coverage: status {known:.1%}, "
              f"chance_of_playing_next_round {chance:.1%} of state rows\n")
    if len(seasons) < 2:
        print("need at least two labelled seasons to walk forward")
        return 1

    summary, calib, folds = walk_forward(wh, seasons, catalog_at=catalog_at)
    pd.set_option("display.width", 200)
    print(summary.to_string(index=False))

    if folds:
        print("\nhierarchical fitted parameters (last fold):")
        for stage, params in folds[-1].models["hierarchical"].summary().items():  # type: ignore
            print(f"  {stage}: " + ", ".join(f"{k}={v:.3f}" for k, v in params.items()))

    if args.write_docs:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        tag = f"_{args.tag}" if args.tag else ""
        eval_path = DOCS_DIR / f"minutes_eval{tag}.csv"
        calib_path = DOCS_DIR / f"minutes_calibration{tag}.csv"
        summary.to_csv(eval_path, index=False)
        calib.to_csv(calib_path, index=False)
        print(f"\nwrote {eval_path} and {calib_path}")
    if tmp:
        wh.close()
        tmp.cleanup()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
