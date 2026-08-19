"""The optimiser against the real 2026/27 universe.

592 players, real positions, real clubs, real prices, from the committed
fixture under ``tests/fixtures/opt``. The expected points there are synthetic
and deliberately not a points model; what these tests check is that the
optimiser scales to the real instance and that whatever it returns is legal and
scored correctly -- including when it stops on a time limit rather than at a
proven optimum, which is the case a decision engine must not lie about.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fpl_edge.opt import (
    ObjectiveMode,
    OptimizerConfig,
    SolverConfig,
    SquadState,
    score_plan,
    solve_horizon,
    validate_plan,
)
from fpl_edge.types import Money, Position
from tests.unit.test_opt_support import fixture_problem


def test_the_fixture_universe_is_the_real_one():
    problem = fixture_problem((1,))
    assert problem.n_players == 592
    counts = {pos: 0 for pos in Position}
    for p in problem.players:
        counts[p.position] += 1
    assert sum(counts.values()) == 592
    assert len({p.team_code for p in problem.players}) == 20
    assert problem.price_tenths.dtype.kind in "iu"
    assert int(problem.price_tenths.min()) >= 35


def test_gameweek_one_squad_from_the_full_universe():
    """GW1 is a pure selection problem: unlimited free transfers, no squad yet."""
    problem = fixture_problem((1,))
    config = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        solver=SolverConfig(time_limit_s=120.0),
    )
    plan, stats = solve_horizon(problem, config, return_stats=True)
    assert validate_plan(problem, plan) == []
    assert plan.objective == pytest.approx(score_plan(problem, plan, config), abs=1e-6)

    d = plan.decisions[0]
    assert d.squad_value.tenths + d.bank_after.tenths == problem.ruleset.budget_tenths
    assert d.hits == 0, "transfers before the first deadline are free"
    assert d.chip in (None, "bboost", "3xc"), "Wildcard and Free Hit are locked out of GW1"
    assert stats.n_binary > 1000


def test_five_gameweek_plan_is_legal_and_correctly_scored_even_under_a_time_limit():
    """A gap-limited answer must still be a legal squad with an honest objective.

    The five-gameweek chip-planning instance does not always close inside a
    short budget. What must never happen is a plan that breaks a rule, or a
    reported objective that does not match the decisions.
    """
    problem = fixture_problem((1, 2, 3, 4, 5))
    config = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        solver=SolverConfig(time_limit_s=60.0, mip_gap_rel=0.02),
    )
    plan = solve_horizon(problem, config)
    assert validate_plan(problem, plan) == []
    assert plan.objective == pytest.approx(score_plan(problem, plan, config), abs=1e-6)
    assert len(plan.decisions) == 5
    if plan.mip_gap is not None and plan.mip_gap > 0.02:
        assert any("optimality gap" in n for n in plan.notes), (
            "an unproven plan must say so in its notes"
        )


def test_pruning_the_universe_keeps_everything_legal():
    problem = fixture_problem((1, 2, 3))
    config = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        max_candidates_per_position=40,
        solver=SolverConfig(time_limit_s=120.0),
    )
    plan = solve_horizon(problem, config)
    pruned = problem.prune(40)
    assert pruned.n_players < problem.n_players
    assert validate_plan(pruned, plan) == []
    assert plan.objective == pytest.approx(score_plan(pruned, plan, config), abs=1e-6)


def test_a_held_squad_carries_its_purchase_prices_into_the_next_gameweek():
    """Roll a GW1 squad forward and check the ledger through a real transfer."""
    problem = fixture_problem((1, 2, 3))
    config = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        solver=SolverConfig(time_limit_s=120.0),
    )
    opener = solve_horizon(problem, config).decisions[0]
    idx = problem.index_of
    holdings = {c: Money(int(problem.price_tenths[idx[c], 0])) for c in opener.squad}

    rolled = fixture_problem(
        (2, 3, 4),
        state=SquadState(
            holdings=holdings, bank=opener.bank_after, free_transfers=1
        ),
    )
    plan = solve_horizon(rolled, config)
    assert validate_plan(rolled, plan) == []
    assert plan.objective == pytest.approx(score_plan(rolled, plan, config), abs=1e-6)
    for d in plan.decisions:
        assert d.bank_after.tenths >= 0
        assert len(d.transfers_in) == len(d.transfers_out)


def test_pruning_never_drops_a_player_you_already_own():
    problem = fixture_problem((1, 2))
    held = [p.code for p in problem.players[:15]]
    with_squad = replace(
        problem,
        state=SquadState(
            holdings={
                c: Money(int(problem.price_tenths[problem.index_of[c], 0])) for c in held
            },
            bank=Money(0),
        ),
    )
    pruned = with_squad.prune(20)
    assert set(held) <= {p.code for p in pruned.players}
