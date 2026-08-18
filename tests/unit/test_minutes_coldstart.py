"""The GW1 cold start, which is the case that is live today.

On 2026-08-18 the 2026-27 season has zero rows of current-season evidence and
the GW1 deadline is three days away. A minutes model that needs five gameweeks
of form is not a minutes model yet, so the cold start is a separate fitted stage
with its own feature set, and these tests pin its behaviour.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import MINUTES_COLUMNS, validate_probability_frame
from fpl_edge.models.minutes import (
    BaseRateBaseline,
    GBMMinutesModel,
    HierarchicalMinutesModel,
    PriorSeasonRateBaseline,
    TrainingSetBuilder,
)
from fpl_edge.models.minutes.dataset import FIXTURE_DIR, load_csv_warehouse
from fpl_edge.models.minutes.evaluate import multiclass_log_loss
from fpl_edge.models.minutes.features import (
    COLD_FEATURE_COLUMNS,
    attach_labels,
    build_feature_frame,
)
from fpl_edge.models.minutes.training import LABEL_LAG
from fpl_edge.store import Warehouse

UTC = dt.UTC
CATALOG_AT = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)
#: The real 2026-27 GW1 deadline (docs/rules.md: the API's UTC value is the only
#: authority). The live-season fixture rows are keyed to it.
LIVE_DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
LIVE_SEASON = "2026-27"
TRAIN_SEASON = "2024-25"
TEST_SEASON = "2025-26"


@pytest.fixture(scope="module")
def wh(tmp_path_factory) -> Warehouse:
    return load_csv_warehouse(FIXTURE_DIR, tmp_path_factory.mktemp("cold") / "f.duckdb")


@pytest.fixture(scope="module")
def training(wh: Warehouse):
    builder = TrainingSetBuilder(wh.snapshot_at, wh.snapshot_at(CATALOG_AT))
    return builder.build([TRAIN_SEASON], gws=list(range(1, 13)))


@pytest.fixture(scope="module")
def models(training):
    return {
        "hierarchical": HierarchicalMinutesModel().fit(training),
        "gbm": GBMMinutesModel().fit(training),
        "prior_season": PriorSeasonRateBaseline().fit(training),
        "base_rate": BaseRateBaseline().fit(training),
    }


@pytest.fixture(scope="module")
def live_frame(wh: Warehouse) -> pd.DataFrame:
    """Features for the live season's GW1, built at the real deadline."""
    return build_feature_frame(
        wh.snapshot_at(LIVE_DEADLINE - dt.timedelta(hours=1)), LIVE_SEASON, [1]
    )


# --------------------------------------------------------------------------
# what a cold start looks like
# --------------------------------------------------------------------------


def test_live_season_gw1_has_no_current_season_evidence(live_frame) -> None:
    assert not live_frame.empty
    assert (live_frame["is_cold_start"] == 1.0).all()
    assert (live_frame["n_obs_season"] == 0.0).all()
    for col in ("full_rate_season", "start_rate_5", "minutes_trend", "days_rest"):
        assert live_frame[col].isna().all(), col


def test_cold_start_still_has_prior_season_and_availability_evidence(live_frame) -> None:
    returning = live_frame[live_frame["is_unseen"] == 0.0]
    assert len(returning) > 100
    assert returning["prev_n_obs"].min() > 0
    assert returning["prev_full_rate"].notna().all()
    assert live_frame["is_new_signing"].mean() > 0  # summer transfers are visible
    assert live_frame["status_flagged"].sum() > 0  # preseason injuries are visible
    assert live_frame["depth_rank"].notna().all()  # depth comes from last season


def test_cold_feature_set_is_defined_at_gw1(live_frame) -> None:
    """Every cold feature must be *usable*, not merely present."""
    for col in COLD_FEATURE_COLUMNS:
        assert col in live_frame.columns, col
        assert live_frame[col].notna().any(), f"{col} is entirely undefined at GW1"


# --------------------------------------------------------------------------
# the cold code path
# --------------------------------------------------------------------------


def test_cold_rows_are_routed_to_the_cold_stage(models, live_frame) -> None:
    """Corrupting the warm stage must not move a single GW1 prediction."""
    model = models["hierarchical"]
    before = model.predict_proba(live_frame)
    model.warm.params = np.zeros_like(model.warm.params)
    model.warm.gate_mult = np.ones_like(model.warm.gate_mult)
    after = model.predict_proba(live_frame)
    np.testing.assert_allclose(before, after, atol=0)


def test_gbm_cold_booster_only_sees_cold_features(models) -> None:
    gbm = models["gbm"]
    assert gbm.cold.model is not None
    assert set(gbm.cold.used) <= set(COLD_FEATURE_COLUMNS)
    assert "n_obs_season" not in gbm.cold.used


def test_live_gw1_predictions_are_a_valid_distribution(models, wh) -> None:
    snap = wh.snapshot_at(LIVE_DEADLINE - dt.timedelta(hours=1))
    for name in ("hierarchical", "gbm"):
        out = models[name].predict(snap, LIVE_SEASON, [1])
        assert list(out.columns) == list(MINUTES_COLUMNS), name
        assert len(out) == 252, name
        validate_probability_frame(out, ("p_unavailable", "p_cameo", "p_full"))


def test_preseason_injury_flag_is_respected_at_gw1(models, live_frame) -> None:
    flagged = live_frame[live_frame["status_injured"] > 0]
    clear = live_frame[live_frame["status_flagged"] == 0]
    assert len(flagged) > 5
    for name in ("hierarchical", "gbm"):
        p_flagged = models[name].predict_proba(flagged)[:, 0].mean()
        p_clear = models[name].predict_proba(clear)[:, 0].mean()
        assert p_flagged > p_clear, name


# --------------------------------------------------------------------------
# measured, out of sample
# --------------------------------------------------------------------------


def test_cold_start_beats_the_baselines_on_a_held_out_gw1(models, wh) -> None:
    """Train on 2024-25, predict the first deadline of 2025-26, score it.

    Numbers as measured (they are asserted with slack, and the full three-season
    walk-forward is in docs/models/minutes_eval.csv):
    base rate 1.053, FPL chance 1.045, previous-season rate 0.877,
    hierarchical 0.822, GBM 0.873.
    """
    ev = wh.snapshot_at(CATALOG_AT).table("dim_event", where="season = ?", params=[TEST_SEASON])
    deadline = pd.Timestamp(ev.sort_values("gw").iloc[0]["deadline_utc"]).to_pydatetime()
    frame = build_feature_frame(wh.snapshot_at(deadline), TEST_SEASON, [1])
    labelled = attach_labels(frame, wh.snapshot_at(deadline + LABEL_LAG), TEST_SEASON)
    assert (labelled["is_cold_start"] == 1.0).all()
    y = labelled["bucket"].to_numpy(dtype=int)

    loss = {
        name: multiclass_log_loss(y, m.predict_proba(labelled)) for name, m in models.items()
    }
    assert loss["base_rate"] > 1.0
    assert loss["hierarchical"] < loss["base_rate"] - 0.15
    assert loss["gbm"] < loss["base_rate"] - 0.15
    # the honest headline: at a cold start the shrinkage model is the better one
    assert loss["hierarchical"] < loss["prior_season"] - 0.02
    assert loss["hierarchical"] < loss["gbm"]
