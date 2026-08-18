"""The promoted-club prior.

The requirement being tested is not "the model runs for a promoted club" -- a
league-average fallback runs too. It is that a club with no top-flight history
receives an explicit, measured, *pessimistic* rating, that the rating comes from
observed promotion events rather than a constant, and that the code refuses to
guess when it has nothing to measure.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from fpl_edge.models.team_goals.data import (
    InsufficientHistoryError,
    promoted_team_codes,
    teams_in_season,
)
from fpl_edge.models.team_goals.dixon_coles import DixonColesModel
from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.promoted import (
    ESTABLISHED_PRIOR_SD,
    FALLBACK_PROMOTED_PRIOR,
    build_team_priors,
    first_season_offsets,
    fit_promoted_prior,
)
from fpl_edge.models.team_goals.synthetic import (
    PROMOTED_ATTACK_MEAN,
    PROMOTED_DEFENCE_MEAN,
    PROMOTED_ROUTE_SLOPE,
    build_warehouse,
    load_league,
)

SEASON = "2025-26"
GW1_DEADLINE = dt.datetime(2025, 8, 14, 10, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def league():
    return load_league(FIXTURES_DIR)


@pytest.fixture(scope="module")
def warehouse(league, tmp_path_factory):
    return build_warehouse(league, tmp_path_factory.mktemp("promoted") / "wh.duckdb")


@pytest.fixture(scope="module")
def history(league):
    return league.fixtures[league.fixtures["season"] < SEASON]


# -- detection ---------------------------------------------------------------


def test_promoted_clubs_are_those_with_no_prior_top_flight_match(league) -> None:
    target = teams_in_season(league.fixtures, SEASON)
    promoted = promoted_team_codes(league.fixtures, target, season=SEASON)
    assert len(promoted) == 3
    expected = set(league.routes[league.routes["season"] == SEASON]["team_code"])
    assert promoted == expected


# -- the prior itself --------------------------------------------------------


def test_prior_is_pessimistic_not_league_average(history, league) -> None:
    prior = fit_promoted_prior(history, routes=league.routes)
    assert prior.source == "fitted"
    assert prior.is_pessimistic
    assert prior.attack_mean < -0.05
    assert prior.defence_mean > 0.05
    assert prior.attack_sd > 0.0


def test_prior_recovers_the_generating_promoted_distribution(history, league) -> None:
    prior = fit_promoted_prior(history, routes=league.routes)
    # Measured against the values that generated the data. The tolerance is wide
    # because the estimator is method-of-moments on ~9 clubs, and it is
    # deliberately slightly pessimistic: promoted clubs never play themselves,
    # so their opponent pool is marginally stronger than average.
    assert prior.attack_mean == pytest.approx(PROMOTED_ATTACK_MEAN, abs=0.15)
    assert prior.defence_mean == pytest.approx(PROMOTED_DEFENCE_MEAN, abs=0.15)


def test_route_covariate_is_used_and_signed_correctly(history, league) -> None:
    """A better prior-division finish must buy a better rating."""
    prior = fit_promoted_prior(history, routes=league.routes)
    assert prior.covariate == "promotion_route"
    champion_atk, champion_def = prior.offsets(route=1)
    playoff_atk, playoff_def = prior.offsets(route=3)
    assert champion_atk > playoff_atk
    assert champion_def < playoff_def
    per_step = (champion_atk - playoff_atk) / 2
    assert per_step == pytest.approx(PROMOTED_ROUTE_SLOPE, abs=0.10)


def test_without_routes_the_regression_collapses_to_its_intercept(history) -> None:
    prior = fit_promoted_prior(history, routes=None)
    assert prior.covariate == "none"
    assert prior.offsets(route=1) == prior.offsets(route=3)
    assert prior.offsets(route=None) == (prior.attack_mean, prior.defence_mean)


def test_first_season_offsets_are_measured_per_season(history) -> None:
    obs = first_season_offsets(history)
    assert not obs.empty
    assert set(obs.columns) >= {"season", "team_code", "attack_offset", "defence_offset"}
    assert obs["attack_offset"].mean() < 0.0
    assert obs["defence_offset"].mean() > 0.0


# -- the refusal to guess ----------------------------------------------------


def test_refuses_to_invent_a_prior_without_observable_promotions(league) -> None:
    one_season = league.fixtures[league.fixtures["season"] == "2020-21"]
    with pytest.raises(InsufficientHistoryError, match="Refusing to fall back"):
        fit_promoted_prior(one_season)


def test_fallback_prior_is_opt_in_and_flagged(league) -> None:
    one_season = league.fixtures[league.fixtures["season"] == "2020-21"]
    prior = fit_promoted_prior(one_season, allow_fallback=True)
    assert prior is FALLBACK_PROMOTED_PRIOR
    assert prior.source == "assumed_fallback"
    assert prior.is_pessimistic


# -- wiring into the fit -----------------------------------------------------


def test_established_clubs_get_a_wide_mean_zero_prior(history, league) -> None:
    prior = fit_promoted_prior(history, routes=league.routes)
    priors = build_team_priors([1, 2, 3], {3}, prior)
    assert priors.attack_mean[0] == 0.0
    assert priors.attack_sd[0] == ESTABLISHED_PRIOR_SD
    assert priors.promoted.tolist() == [False, False, True]
    assert priors.n_promoted == 1


def test_a_club_with_no_matches_lands_exactly_on_its_prior(warehouse, league) -> None:
    """The MAP guarantee: zero likelihood contribution, so the prior is the answer."""
    snap = warehouse.snapshot_at(GW1_DEADLINE)
    fit = DixonColesModel(routes=league.routes).fit(snap, SEASON)
    prior = fit.prior
    routes = league.routes[league.routes["season"] == SEASON]
    assert len(fit.promoted) == 3
    for code, route in zip(routes["team_code"], routes["route"], strict=True):
        i = fit.index_of(int(code))
        want_atk, want_def = prior.offsets(float(route))
        assert fit.attack[i] == pytest.approx(want_atk, abs=1e-3)
        assert fit.defence[i] == pytest.approx(want_def, abs=1e-3)


def test_promoted_clubs_are_rated_below_the_league_at_gameweek_one(warehouse, league) -> None:
    snap = warehouse.snapshot_at(GW1_DEADLINE)
    fit = DixonColesModel(routes=league.routes).fit(snap, SEASON)
    est = fit.table()
    promoted = est[est["is_promoted"]]
    others = est[~est["is_promoted"]]
    assert len(promoted) == 3
    assert promoted["attack"].mean() < others["attack"].mean()
    assert promoted["defence"].mean() > others["defence"].mean()
    # And the rating must not be the league average, which is what the ablation
    # switch produces.
    assert abs(promoted["attack"].mean()) > 0.05


def test_the_ablation_switch_reproduces_the_league_average_failure(warehouse, league) -> None:
    snap = warehouse.snapshot_at(GW1_DEADLINE)
    naive = DixonColesModel(routes=league.routes, use_promoted_prior=False).fit(snap, SEASON)
    for code in naive.promoted:
        i = naive.index_of(int(code))
        assert naive.attack[i] == pytest.approx(0.0, abs=1e-3)
        assert naive.defence[i] == pytest.approx(0.0, abs=1e-3)


def test_promoted_clubs_concede_more_than_they_score_at_gameweek_one(
    warehouse, league
) -> None:
    """The prior has to survive the round trip into actual goal expectations."""
    snap = warehouse.snapshot_at(GW1_DEADLINE)
    model = DixonColesModel(routes=league.routes)
    frame = model.predict(snap, SEASON, [1])
    fit = model.fit(snap, SEASON)
    promoted = frame[frame["team_code"].isin(fit.promoted)]
    others = frame[~frame["team_code"].isin(fit.promoted)]
    assert not promoted.empty
    assert promoted["exp_goals_for"].mean() < others["exp_goals_for"].mean()
    assert promoted["p_clean_sheet"].mean() < others["p_clean_sheet"].mean()
    assert np.isfinite(promoted["exp_goals_against"]).all()
