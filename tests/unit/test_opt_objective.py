"""The model and the scorer must agree, and the mode switch must be real.

The first half of this file guards against the classic MILP failure: the
encoding drifts from the formula it is supposed to encode, the solver reports a
number nobody recomputes, and the squad is quietly optimal for something else.
Every configuration here is solved and then re-scored from the returned
decisions by :func:`fpl_edge.opt.scoring.score_plan`, which shares no code with
the encoding.
"""

from __future__ import annotations

import pytest

from fpl_edge.opt import (
    AutosubWeights,
    ObjectiveMode,
    OptimizerConfig,
    RankUtilityUnavailableError,
    gw_contributions,
    score_plan,
    solve_horizon,
    validate_plan,
)
from fpl_edge.types import Position
from tests.unit.test_opt_support import synthetic_problem

SMALL = {Position.GKP: 4, Position.DEF: 9, Position.MID: 9, Position.FWD: 5}

CONFIGS = {
    "plain": OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset()),
    "discounted": OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        gw_discount=(1.0, 0.85),
    ),
    "no_bench_order": OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        model_bench_order=False,
    ),
    "heavy_bench": OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        autosubs=AutosubWeights(gk=0.4, outfield=(0.5, 0.3, 0.2)),
    ),
    "bench_boost": OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset({"bboost"})
    ),
    "triple_captain": OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset({"3xc"})
    ),
}


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_reported_objective_equals_an_independent_recomputation(name):
    config = CONFIGS[name]
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve_horizon(problem, config)
    assert validate_plan(problem, plan) == []
    assert plan.objective == pytest.approx(score_plan(problem, plan, config), abs=1e-6)


def test_objective_agreement_with_transfers_and_hits():
    """Hits are part of the objective, so a plan that takes them must still agree."""
    from fpl_edge.opt import SquadState
    from fpl_edge.types import Money

    base = synthetic_problem(per_position=SMALL, n_gws=3)
    seed = solve_horizon(base, CONFIGS["plain"])
    held = seed.decisions[0].squad
    idx = base.index_of

    # Own a squad, then make a different set of players far better from GW2 on,
    # so several hits are worth taking.
    def xp_of(i, pos, j):
        code = base.players[i].code
        if j == 0:
            return 5.0 if code in held else 1.0
        return 1.0 if code in held else 9.0

    problem = synthetic_problem(
        per_position=SMALL,
        n_gws=3,
        price_of=lambda i, pos, j: 40,
        xp_of=xp_of,
        state=SquadState(
            holdings={c: Money(40) for c in held}, bank=Money(0), free_transfers=1
        ),
    )
    plan = solve_horizon(problem, CONFIGS["plain"])
    assert validate_plan(problem, plan) == []
    assert plan.total_hits > 0, "expected the optimiser to pay for transfers here"
    assert plan.objective == pytest.approx(score_plan(problem, plan, CONFIGS["plain"]), abs=1e-6)
    assert idx  # keep the index lookup meaningful


def test_per_gameweek_contributions_sum_to_the_objective():
    config = CONFIGS["discounted"]
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve_horizon(problem, config)
    parts = gw_contributions(problem, plan, config)
    assert [gw for gw, _ in parts] == [int(g) for g in problem.gws]
    assert sum(v for _, v in parts) == pytest.approx(plan.objective, abs=1e-6)


def test_discounting_actually_changes_the_objective():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    flat = solve_horizon(problem, CONFIGS["plain"]).objective
    discounted = solve_horizon(problem, CONFIGS["discounted"]).objective
    assert discounted < flat


def test_captain_uplift_is_exactly_one_extra_copy_of_expected_points():
    config = CONFIGS["plain"]
    problem = synthetic_problem(per_position=SMALL, n_gws=1, p_play=1.0)
    plan = solve_horizon(problem, config)
    d = plan.decisions[0]
    idx = problem.index_of
    xi = sum(float(problem.xpts[idx[c], 0]) for c in d.starting_xi)
    bench = (
        config.autosubs.gk * float(problem.xpts[idx[d.bench[0]], 0])
        + sum(
            w * float(problem.xpts[idx[c], 0])
            for w, c in zip(config.autosubs.outfield, d.bench[1:])
        )
    )
    captain = float(problem.xpts[idx[d.captain], 0])
    # p_play = 1 everywhere, so the vice-captain term is exactly zero.
    assert plan.objective == pytest.approx(xi + captain + bench, abs=1e-6)


def test_vice_captain_term_is_worth_something_when_the_captain_might_blank():
    config = CONFIGS["plain"]
    certain = solve_horizon(
        synthetic_problem(per_position=SMALL, n_gws=1, p_play=1.0), config
    )
    doubtful = solve_horizon(
        synthetic_problem(per_position=SMALL, n_gws=1, p_play=0.5), config
    )
    # Same squad, same points; the only difference is the vice-captain term.
    assert doubtful.objective > certain.objective


# -- the mode switch --------------------------------------------------------


def test_rank_utility_refuses_to_run_without_a_provider():
    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    config = OptimizerConfig(mode=ObjectiveMode.RANK_UTILITY, allowed_chips=frozenset())
    with pytest.raises(RankUtilityUnavailableError) as exc:
        solve_horizon(problem, config)
    assert "EXPECTED_POINTS" in str(exc.value)
    assert isinstance(exc.value, NotImplementedError)


def test_rank_utility_with_a_provider_is_still_not_implemented():
    """A provider must not silently fall back to a linearised surrogate."""
    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    config = OptimizerConfig(mode=ObjectiveMode.RANK_UTILITY, allowed_chips=frozenset())

    class Stub:
        def linear_coefficients(self, codes, gws, *, incumbent=None):
            raise AssertionError("must not be called")

        def evaluate_plan(self, plan):
            raise AssertionError("must not be called")

    with pytest.raises(NotImplementedError) as exc:
        solve_horizon(problem, config, rank_utility=Stub())
    assert "trust-region" in str(exc.value)


def test_scoring_a_rank_utility_plan_refuses_to_use_means():
    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    plan = solve_horizon(problem, CONFIGS["plain"])
    rank_cfg = OptimizerConfig(mode=ObjectiveMode.RANK_UTILITY)
    with pytest.raises(RankUtilityUnavailableError):
        score_plan(problem, plan, rank_cfg)


def test_the_two_modes_are_distinct_values():
    assert ObjectiveMode.RANK_UTILITY != ObjectiveMode.EXPECTED_POINTS
    assert str(ObjectiveMode.EXPECTED_POINTS) == "expected_points"
