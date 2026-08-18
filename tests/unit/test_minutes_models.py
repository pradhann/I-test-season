"""Contract, behaviour and determinism tests for both minutes models.

The behavioural assertions are the ones worth reading: they pin the properties
we would want to hold even if the whole implementation were replaced - more
evidence means less shrinkage, a published injury moves mass onto "did not
feature", and the same seed gives the same numbers.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import MINUTES_COLUMNS, MinutesModel, validate_probability_frame
from fpl_edge.models.minutes import (
    BaseRateBaseline,
    ChanceOfPlayingBaseline,
    GBMMinutesModel,
    HierarchicalMinutesModel,
    PriorSeasonRateBaseline,
    TrainingSetBuilder,
)
from fpl_edge.models.minutes.dataset import FIXTURE_DIR, load_csv_warehouse
from fpl_edge.models.minutes.features import build_feature_frame
from fpl_edge.store import Warehouse

UTC = dt.UTC
CATALOG_AT = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)
TRAIN_SEASON = "2024-25"
TEST_SEASON = "2025-26"
TRAIN_GWS = list(range(1, 13))


@pytest.fixture(scope="module")
def wh(tmp_path_factory) -> Warehouse:
    return load_csv_warehouse(FIXTURE_DIR, tmp_path_factory.mktemp("minutes") / "f.duckdb")


@pytest.fixture(scope="module")
def training(wh: Warehouse):
    builder = TrainingSetBuilder(wh.snapshot_at, wh.snapshot_at(CATALOG_AT))
    return builder.build([TRAIN_SEASON], gws=TRAIN_GWS)


@pytest.fixture(scope="module")
def deadline(wh: Warehouse) -> dt.datetime:
    ev = wh.snapshot_at(CATALOG_AT).table("dim_event", where="season = ?", params=[TEST_SEASON])
    row = ev.sort_values("gw").iloc[11]
    return pd.Timestamp(row["deadline_utc"]).to_pydatetime()


@pytest.fixture(scope="module")
def frame(wh: Warehouse, deadline: dt.datetime) -> pd.DataFrame:
    return build_feature_frame(wh.snapshot_at(deadline), TEST_SEASON, [12])


@pytest.fixture(scope="module")
def models(training):
    return {
        "hierarchical": HierarchicalMinutesModel().fit(training),
        "gbm": GBMMinutesModel().fit(training),
        "base_rate": BaseRateBaseline().fit(training),
        "prior_season": PriorSeasonRateBaseline().fit(training),
        "fpl_chance": ChanceOfPlayingBaseline().fit(training),
    }


# --------------------------------------------------------------------------
# the contract
# --------------------------------------------------------------------------


def test_every_model_satisfies_the_protocol(models) -> None:
    for name, model in models.items():
        assert isinstance(model, MinutesModel), name


def test_predict_returns_the_declared_columns(models, wh, deadline) -> None:
    snap = wh.snapshot_at(deadline)
    for name, model in models.items():
        out = model.predict(snap, TEST_SEASON, [12])
        assert list(out.columns) == list(MINUTES_COLUMNS), name
        assert len(out) == 252, name
        validate_probability_frame(out, ("p_unavailable", "p_cameo", "p_full"))
        assert out["exp_minutes"].between(0, 90).all(), name
        assert not out.duplicated(subset=["code", "fixture_id"]).any(), name


def test_cards_carry_a_measured_score(models) -> None:
    for name, model in models.items():
        card = model.card
        assert card.score is not None, name
        assert card.baseline_score is not None, name
        assert card.notes, name
        assert card.baseline.strip() and card.metric.strip(), name


def test_both_models_and_the_useful_baselines_beat_what_they_claim_to(models) -> None:
    for name in ("hierarchical", "gbm", "base_rate", "prior_season"):
        assert models[name].card.beats_baseline is True, name


def test_the_fpl_chance_card_does_not_claim_a_win_it_did_not_get(models) -> None:
    """A measured negative result, pinned so it cannot be quietly upgraded.

    On real history ``chance_of_playing_next_round`` is NULL on every row, so
    this baseline degenerates to the base rate and ties it exactly. The card
    says so; if someone later populates a score that claims a win, this fails.
    """
    card = models["fpl_chance"].card
    assert card.beats_baseline is False
    assert card.score == card.baseline_score
    assert any("null on 100%" in n.lower() for n in card.notes)


def test_probabilities_are_never_zero_or_one(models, frame) -> None:
    """A minutes model is never certain. A nailed starter still fails a fitness test."""
    for name, model in models.items():
        p = model.predict_proba(frame)
        assert np.isfinite(p).all(), name
        assert p.min() > 0, name
        assert p.max() < 1, name


# --------------------------------------------------------------------------
# behaviour
# --------------------------------------------------------------------------


def test_published_injury_moves_mass_onto_did_not_feature(models, frame) -> None:
    """Same player, same form, one difference: the club has said he is injured."""
    base = frame.copy()
    base["status_flagged"] = 0.0
    base["status_injured"] = 0.0
    base["status_doubtful"] = 0.0
    base["status_suspended"] = 0.0
    base["has_chance"] = 0.0
    base["chance_next"] = np.nan
    base["news_len"] = 0.0
    base["news_injury"] = 0.0

    injured = base.copy()
    injured["status_flagged"] = 1.0
    injured["status_injured"] = 1.0
    injured["has_chance"] = 1.0
    injured["chance_next"] = 0.0
    injured["news_len"] = 28.0
    injured["news_injury"] = 1.0

    for name in ("hierarchical", "gbm"):
        p_fit = models[name].predict_proba(base)[:, 0].mean()
        p_out = models[name].predict_proba(injured)[:, 0].mean()
        assert p_out > p_fit + 0.2, f"{name}: {p_fit:.3f} -> {p_out:.3f}"


def test_more_evidence_means_less_shrinkage(models) -> None:
    """The hierarchical model's whole point, as a property.

    Two players with an identical 100%-start record, one with two games behind
    it and one with twenty. The second should be predicted higher: the first is
    still mostly prior.
    """
    model = models["hierarchical"]
    template = {
        "n_obs_season": 2.0, "full_rate_season": 1.0, "cameo_rate_season": 0.0,
        "unavail_rate_season": 0.0, "start_rate_season": 1.0, "mean_min_season": 90.0,
        "full_rate_5": 1.0, "cameo_rate_5": 0.0, "unavail_rate_5": 0.0, "start_rate_5": 1.0,
        "mean_min_5": 90.0, "prev_n_obs": 0.0, "prev_full_rate": np.nan,
        "prev_cameo_rate": np.nan, "prev_unavail_rate": np.nan, "position": 3.0,
        "depth_rank": 3.0, "euro_congestion": 0.0, "gw_idx": 10.0, "is_new_signing": 0.0,
        "chance_next": np.nan, "has_chance": 0.0, "status_flagged": 0.0,
        "status_injured": 0.0, "status_suspended": 0.0, "price_tenths": 50.0,
        "selected_by_pct": 5.0, "is_cold_start": 0.0,
    }
    sparse = dict(template)
    dense = dict(template, n_obs_season=20.0)
    df = pd.DataFrame([sparse, dense])
    p = model.predict_proba(df)
    assert p[1, 2] > p[0, 2]
    assert p[0, 2] < 1.0  # the two-game player is not taken at face value


def test_congestion_is_priced_for_european_clubs(models, frame) -> None:
    """Rotation risk must lower P(60+), and it does so by construction only in one model.

    The hierarchical model carries a signed congestion parameter, so the
    direction is guaranteed. The GBM has no monotonicity constraint and, trained
    on a single partial season, does not reliably recover the sign - it is held
    only to "does not invert materially". That asymmetry is a real cost of the
    tree model and is recorded in docs/models/minutes.md rather than hidden by
    a test that asserts nothing.
    """
    rested = frame.copy()
    rested["euro_congestion"] = 0.0
    rested["euro_club"] = 0.0
    rested["days_rest"] = 7.0
    congested = frame.copy()
    congested["euro_congestion"] = 1.0
    congested["euro_club"] = 1.0
    congested["days_rest"] = 3.0

    h_rested = models["hierarchical"].predict_proba(rested)[:, 2].mean()
    h_congested = models["hierarchical"].predict_proba(congested)[:, 2].mean()
    assert h_congested < h_rested

    g_rested = models["gbm"].predict_proba(rested)[:, 2].mean()
    g_congested = models["gbm"].predict_proba(congested)[:, 2].mean()
    assert g_congested - g_rested < 0.05


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------


def test_gbm_is_deterministic_given_its_seed(training, frame) -> None:
    a = GBMMinutesModel(seed=7).fit(training).predict_proba(frame)
    b = GBMMinutesModel(seed=7).fit(training).predict_proba(frame)
    np.testing.assert_allclose(a, b, rtol=0, atol=0)


def test_hierarchical_fit_is_deterministic(training, frame) -> None:
    a = HierarchicalMinutesModel().fit(training).predict_proba(frame)
    b = HierarchicalMinutesModel().fit(training).predict_proba(frame)
    np.testing.assert_allclose(a, b, rtol=0, atol=1e-12)


def test_hierarchical_parameters_are_inspectable(models) -> None:
    summary = models["hierarchical"].summary()
    assert {"warm", "cold"} <= set(summary)
    warm = summary["warm"]
    assert warm["weight_prev_season"] > 0
    assert 0.5 <= warm["median_kappa"] <= 60.0
    # a published "injured" flag should be worth a large discount, not a nudge
    assert warm["gate_injured"] < 0.5
