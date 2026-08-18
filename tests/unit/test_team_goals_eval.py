"""The evaluation harness itself, and a small walk-forward regression.

The full three-season sweep lives in ``fpl_edge.models.team_goals.evaluate`` and
takes about a minute; committed numbers from it are in ``docs/models/``. What
runs here is a short slice, fast enough for the unit suite, asserting the two
things that would invalidate the committed numbers if they broke: the harness
does not leak, and the Dixon-Coles model really does beat the naive baselines
out of sample.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from fpl_edge.models.team_goals.base import BaseGoalModel
from fpl_edge.models.team_goals.baselines import HomeAdvantageOnlyModel, LastSeasonTableModel
from fpl_edge.models.team_goals.blend import BlendedGoalModel
from fpl_edge.models.team_goals.dixon_coles import DixonColesModel
from fpl_edge.models.team_goals.evaluate import (
    FIXTURES_DIR,
    all_results,
    attach_results,
    clean_sheet_calibration,
    common_coverage,
    deadlines,
    promoted_slice,
    score_frame,
    walk_forward,
)
from fpl_edge.models.team_goals.market import MarketImpliedModel
from fpl_edge.models.team_goals.odds import FrameOddsProvider
from fpl_edge.models.team_goals.synthetic import build_warehouse, load_league

SEASON = "2025-26"
MAX_GW = 10
HALF_LIFE = 400.0


@pytest.fixture(scope="module")
def league():
    return load_league(FIXTURES_DIR)


@pytest.fixture(scope="module")
def warehouse(league, tmp_path_factory):
    return build_warehouse(league, tmp_path_factory.mktemp("eval") / "wh.duckdb")


@pytest.fixture(scope="module")
def scored(warehouse, league):
    def build() -> dict[str, BaseGoalModel]:
        return {
            "home_advantage_only": HomeAdvantageOnlyModel(),
            "last_season_table": LastSeasonTableModel(),
            "dixon_coles": DixonColesModel(half_life_days=HALF_LIFE, routes=league.routes),
            "market_implied": MarketImpliedModel(FrameOddsProvider(league.odds)),
            "blend_dc_market": BlendedGoalModel(
                DixonColesModel(half_life_days=HALF_LIFE, routes=league.routes),
                MarketImpliedModel(FrameOddsProvider(league.odds)),
            ),
        }

    preds = walk_forward(warehouse, build, (SEASON,), max_gw=MAX_GW)
    return attach_results(preds, all_results(warehouse))


# -- harness hygiene ---------------------------------------------------------


def test_deadlines_come_from_the_published_calendar(warehouse) -> None:
    cal = deadlines(warehouse, SEASON)
    assert len(cal) == 38
    assert [gw for gw, _ in cal] == list(range(1, 39))
    assert all(a[1] < b[1] for a, b in itertools.pairwise(cal))


def test_every_prediction_precedes_its_own_kickoff(warehouse, league) -> None:
    """The core no-leakage property of the walk-forward loop."""
    cal = dict(deadlines(warehouse, SEASON))
    fx = league.fixtures[league.fixtures["season"] == SEASON]
    for gw, deadline in cal.items():
        kickoffs = fx[fx["gw"] == gw]["kickoff_utc"]
        assert (kickoffs > deadline).all(), f"GW{gw} kicks off before its own deadline"


def test_each_model_predicts_each_fixture_at_most_once(scored) -> None:
    counts = scored.groupby(["model", "season", "fixture_id"]).size()
    assert counts.max() == 1


def test_outcome_and_clean_sheet_labels_are_consistent(scored) -> None:
    home_win = scored[scored["outcome"] == 0]
    assert (home_win["home_score"] > home_win["away_score"]).all()
    assert (scored["cs_home"] == (scored["away_score"] == 0)).all()
    assert (scored["cs_away"] == (scored["home_score"] == 0)).all()
    assert scored["gd_index"].between(0, 16).all()


# -- the actual comparison ---------------------------------------------------


def test_dixon_coles_beats_both_naive_baselines(scored) -> None:
    common = common_coverage(scored)
    dc = score_frame(common[common["model"] == "dixon_coles"])
    home_only = score_frame(common[common["model"] == "home_advantage_only"])
    table = score_frame(common[common["model"] == "last_season_table"])
    assert dc["log_loss"] < home_only["log_loss"]
    assert dc["log_loss"] < table["log_loss"]
    assert dc["rps_outcome"] < home_only["rps_outcome"]
    assert dc["brier_cs"] < home_only["brier_cs"]
    assert dc["rps_goal_diff"] < home_only["rps_goal_diff"]


def test_the_market_is_the_bar_and_it_is_not_cleared(scored) -> None:
    """A measured loss, asserted as such. If this ever flips, re-run the sweep.

    Stated plainly because it is the finding, not a defect: on this data the
    Dixon-Coles fit does not beat the market-implied baseline.
    """
    common = common_coverage(scored)
    dc = score_frame(common[common["model"] == "dixon_coles"])
    market = score_frame(common[common["model"] == "market_implied"])
    assert market["log_loss"] < dc["log_loss"]
    assert market["brier_cs"] < dc["brier_cs"]


def test_blending_lands_between_the_two(scored) -> None:
    common = common_coverage(scored)
    dc = score_frame(common[common["model"] == "dixon_coles"])["log_loss"]
    market = score_frame(common[common["model"] == "market_implied"])["log_loss"]
    blend = score_frame(common[common["model"] == "blend_dc_market"])["log_loss"]
    assert market < blend < dc


def test_all_models_beat_a_uniform_forecast(scored) -> None:
    uniform = float(np.log(3.0))
    for model, grp in scored.groupby("model"):
        assert score_frame(grp)["log_loss"] < uniform, model


def test_clean_sheet_forecasts_are_roughly_calibrated_in_aggregate(scored) -> None:
    for model in ("dixon_coles", "market_implied"):
        s = score_frame(scored[scored["model"] == model])
        assert abs(s["mean_p_cs"] - s["base_rate_cs"]) < 0.06, model


# -- slicing helpers ---------------------------------------------------------


def test_common_coverage_restricts_to_fixtures_every_model_priced(scored) -> None:
    common = common_coverage(scored)
    per_model = common.groupby("model")["fixture_id"].nunique()
    assert per_model.nunique() == 1
    market_n = scored[scored["model"] == "market_implied"]["fixture_id"].nunique()
    dc_n = scored[scored["model"] == "dixon_coles"]["fixture_id"].nunique()
    assert market_n < dc_n, "expected incomplete odds coverage in the fixture data"
    assert per_model.iloc[0] == market_n


def test_promoted_slice_only_contains_promoted_fixtures(scored, warehouse, league) -> None:
    results = all_results(warehouse)
    sliced = promoted_slice(scored, results)
    promoted = set(league.routes[league.routes["season"] == SEASON]["team_code"])
    assert not sliced.empty
    assert (
        sliced["home_team_code"].isin(promoted) | sliced["away_team_code"].isin(promoted)
    ).all()


def test_calibration_table_is_committable(scored) -> None:
    calib = clean_sheet_calibration(common_coverage(scored))
    assert set(calib.columns) == {
        "model", "bin_lo", "bin_hi", "count", "mean_predicted", "empirical_rate"
    }
    per_model = calib.groupby("model")["count"].sum()
    assert per_model.nunique() == 1


def test_walk_forward_is_deterministic(warehouse, league) -> None:
    def build() -> dict[str, BaseGoalModel]:
        return {"dixon_coles": DixonColesModel(half_life_days=HALF_LIFE, routes=league.routes)}

    a = walk_forward(warehouse, build, (SEASON,), max_gw=3)
    b = walk_forward(warehouse, build, (SEASON,), max_gw=3)
    np.testing.assert_allclose(a["p_home"].to_numpy(float), b["p_home"].to_numpy(float))
    np.testing.assert_allclose(a["p_cs_home"].to_numpy(float), b["p_cs_home"].to_numpy(float))


def test_gameweek_one_is_predictable_with_no_current_season_data(warehouse, league) -> None:
    """The state the engine is in right now: zero matches played this season."""
    def build() -> dict[str, BaseGoalModel]:
        return {"dixon_coles": DixonColesModel(half_life_days=HALF_LIFE, routes=league.routes)}

    preds = walk_forward(warehouse, build, (SEASON,), max_gw=1)
    assert len(preds) == 10
    probs = preds[["p_home", "p_draw", "p_away"]].to_numpy(float)
    np.testing.assert_allclose(probs.sum(axis=1), 1.0, atol=1e-9)
    assert preds["p_home"].std() > 0.02, "GW1 ratings must differentiate between clubs"


# -- model cards -------------------------------------------------------------


def test_every_model_card_carries_measured_numbers() -> None:
    """No unmeasured claims. A card without a score is a card without a status."""
    from fpl_edge.models.team_goals.baselines import HOME_ONLY_CARD, TABLE_CARD
    from fpl_edge.models.team_goals.blend import BLEND_CARD
    from fpl_edge.models.team_goals.dixon_coles import DIXON_COLES_CARD
    from fpl_edge.models.team_goals.market import MARKET_CARD

    for card in (DIXON_COLES_CARD, MARKET_CARD, BLEND_CARD, HOME_ONLY_CARD, TABLE_CARD):
        assert card.score is not None, card.name
        assert card.baseline_score is not None, card.name
        assert card.beats_baseline is True, card.name
        assert card.notes, card.name


def test_the_dixon_coles_card_states_the_loss_to_the_market() -> None:
    """The finding has to survive contact with the model card, not just the docs."""
    from fpl_edge.models.team_goals.dixon_coles import DIXON_COLES_CARD

    notes = " ".join(DIXON_COLES_CARD.notes)
    assert "LOSES TO THE MARKET" in notes
    assert "UNMEASURED on real data" in notes


def test_the_market_card_does_not_claim_real_data() -> None:
    from fpl_edge.models.team_goals.market import MARKET_CARD

    assert "SYNTHETIC DATA ONLY" in " ".join(MARKET_CARD.notes)
