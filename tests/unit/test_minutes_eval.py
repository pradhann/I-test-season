"""Metric correctness, and the measurement the ModelCards claim.

Two jobs. First, check the metrics themselves - a scoring bug would make every
other number in this package meaningless. Second, hold the cards to their
committed measurement: if ``docs/models/minutes_eval.csv`` is regenerated and
the numbers move, this test fails until the cards are updated, so a card can
never quietly describe a model that no longer exists.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.minutes import measured
from fpl_edge.models.minutes.dataset import FIXTURE_DIR, load_csv_warehouse
from fpl_edge.models.minutes.evaluate import (
    DOCS_DIR,
    build_models,
    calibration_frame,
    expected_calibration_error,
    multiclass_brier,
    multiclass_log_loss,
    reliability_table,
    run_fold,
    summarise,
)
from fpl_edge.store import Warehouse

UTC = dt.UTC
CATALOG_AT = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def test_log_loss_of_a_uniform_forecast_is_ln_three() -> None:
    y = np.array([0, 1, 2, 0])
    p = np.full((4, 3), 1 / 3)
    assert multiclass_log_loss(y, p) == pytest.approx(np.log(3), abs=1e-6)


def test_brier_bounds() -> None:
    y = np.array([0, 1, 2])
    perfect = np.eye(3)[y]
    assert multiclass_brier(y, perfect) == pytest.approx(0.0, abs=1e-6)
    worst = np.eye(3)[(y + 1) % 3]
    assert multiclass_brier(y, worst) == pytest.approx(2.0, abs=1e-3)
    assert multiclass_brier(y, np.full((3, 3), 1 / 3)) == pytest.approx(2 / 3, abs=1e-6)


def test_confident_and_wrong_is_penalised_but_not_infinite() -> None:
    """The probability floor exists so one wrong certainty cannot eat a backtest."""
    y = np.array([2])
    p = np.array([[1.0, 0.0, 0.0]])
    loss = multiclass_log_loss(y, p)
    assert np.isfinite(loss)
    assert loss > 5.0


def test_reliability_table_partitions_the_rows() -> None:
    rng = np.random.default_rng(0)
    p = rng.dirichlet([2, 2, 2], size=500)
    y = np.array([rng.choice(3, p=row) for row in p])
    tab = reliability_table(y, p, 2)
    assert tab["n"].sum() == 500
    assert len(tab) == 10
    assert (tab["bin_lower"] < tab["bin_upper"]).all()


def test_ece_is_zero_for_a_perfectly_calibrated_forecast() -> None:
    rng = np.random.default_rng(1)
    n = 40_000
    p_full = rng.uniform(0.05, 0.95, size=n)
    rest = 1 - p_full
    p = np.column_stack([rest * 0.5, rest * 0.5, p_full])
    y = np.array([rng.choice(3, p=row) for row in p])
    assert expected_calibration_error(y, p, 2) < 0.02


# --------------------------------------------------------------------------
# the committed measurement
# --------------------------------------------------------------------------


def test_cards_match_the_committed_evaluation_csv() -> None:
    path = DOCS_DIR / "minutes_eval.csv"
    assert path.exists(), "run: uv run python -m fpl_edge.models.minutes.evaluate --write-docs"
    df = pd.read_csv(path)
    fold = df[(df["test_season"] == measured.TEST_SEASON) & (df["slice"] == measured.SLICE)]
    assert not fold.empty
    for name, expected in measured.LOG_LOSS.items():
        row = fold[fold["model"] == name]
        assert len(row) == 1, name
        assert row.iloc[0]["log_loss"] == pytest.approx(expected, abs=1e-9), name
        assert row.iloc[0]["brier"] == pytest.approx(measured.BRIER[name], abs=1e-9), name
    cold = df[(df["test_season"] == measured.TEST_SEASON) & (df["slice"] == "gw1_cold_start")]
    for name, expected in measured.COLD_LOG_LOSS.items():
        row = cold[cold["model"] == name]
        assert row.iloc[0]["log_loss"] == pytest.approx(expected, abs=1e-9), name


def test_committed_calibration_curve_is_well_formed() -> None:
    path = DOCS_DIR / "minutes_calibration.csv"
    assert path.exists()
    df = pd.read_csv(path)
    assert set(df.columns) == {
        "test_season", "model", "class", "bin_lower", "bin_upper", "n", "mean_pred", "obs_freq"
    }
    assert set(df["class"]) == {"unavailable", "full"}
    for (_season, _model, _cls), g in df.groupby(["test_season", "model", "class"]):
        assert len(g) == 10
        assert g["n"].sum() > 0
    populated = df[df["n"] > 0]
    assert populated["mean_pred"].between(0, 1).all()
    assert populated["obs_freq"].between(0, 1).all()


def test_both_models_beat_all_three_baselines_out_of_sample(tmp_path) -> None:
    """A small but genuine walk-forward fold, run offline from the fixtures.

    Train on the first twelve gameweeks of 2024-25, test on the first six of
    2025-26. Measured: base rate 1.057, FPL chance 0.997, previous-season rate
    0.892, hierarchical 0.788, GBM 0.768.
    """
    wh: Warehouse = load_csv_warehouse(FIXTURE_DIR, tmp_path / "f.duckdb")
    fold = run_fold(
        wh,
        "2025-26",
        ("2024-25",),
        catalog_at=CATALOG_AT,
        models=build_models(),
        train_gws=list(range(1, 13)),
        test_gws=[1, 2, 3, 4, 5, 6],
    )
    summary = summarise(fold)
    overall = summary[summary["slice"] == "all"].set_index("model")
    assert overall.loc["base_rate", "n"] == 1512

    baselines = ["base_rate", "prior_season", "fpl_chance"]
    worst_baseline = overall.loc[baselines, "log_loss"].min()
    for model in ("hierarchical", "gbm"):
        assert overall.loc[model, "log_loss"] < worst_baseline - 0.05, model
        assert overall.loc[model, "brier"] < overall.loc[baselines, "brier"].min(), model
        # calibrated to within a couple of points of probability
        assert overall.loc[model, "ece_full"] < 0.06, model

    calib = calibration_frame(fold)
    assert not calib.empty
    assert calib["n"].sum() == 2 * 5 * 1512  # two classes x five models
