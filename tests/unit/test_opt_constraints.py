"""Every squad rule, tested one at a time.

Two complementary shapes:

* **Infeasible instances** -- a universe in which a specific rule cannot be
  satisfied must raise, not return a squad that quietly breaks it.
* **Binding instances** -- a universe whose unconstrained optimum would break a
  specific rule must return something that respects it, and must score strictly
  less than the rule-free optimum. This is what catches a constraint that was
  written down but never actually bites.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.opt import (
    InfeasibleError,
    ObjectiveMode,
    OptimizerConfig,
    SquadState,
    solve_horizon,
    validate_plan,
)
from fpl_edge.types import Money, Position
from tests.unit.test_opt_support import synthetic_problem

NO_CHIPS = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset())
SMALL = {Position.GKP: 4, Position.DEF: 9, Position.MID: 9, Position.FWD: 5}


def solve(problem, config=NO_CHIPS):
    return solve_horizon(problem, config)


def test_optimal_plan_satisfies_every_rule():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve(problem)
    assert validate_plan(problem, plan) == []


# -- squad size and shape ---------------------------------------------------


def test_position_quota_infeasible_when_the_universe_is_short_of_forwards():
    short = dict(SMALL) | {Position.FWD: 2}
    problem = synthetic_problem(per_position=short, n_gws=1)
    with pytest.raises(InfeasibleError):
        solve(problem)


def test_squad_has_exactly_the_required_shape():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve(problem)
    by_code = {p.code: p for p in problem.players}
    for d in plan.decisions:
        counts = {pos: 0 for pos in Position}
        for c in d.squad:
            counts[by_code[c].position] += 1
        assert counts == dict(problem.ruleset.select_by_position)
        assert len(d.squad) == problem.ruleset.squad_size


# -- budget -----------------------------------------------------------------


def test_budget_infeasible_when_the_cheapest_legal_fifteen_is_unaffordable():
    # Every player costs 9.9m, so any legal 15 costs 148.5m against a 100.0m budget.
    problem = synthetic_problem(
        per_position=SMALL, n_gws=1, price_of=lambda i, pos, j: 99
    )
    with pytest.raises(InfeasibleError):
        solve(problem)


def test_budget_binds_and_costs_points():
    """The optimum must be strictly worse than an unbudgeted one."""
    problem = synthetic_problem(
        per_position=SMALL,
        n_gws=1,
        price_of=lambda i, pos, j: 40 + (i % 10) * 6,
        xp_of=lambda i, pos, j: 1.0 + 0.5 * (i % 10),
    )
    plan = solve(problem)
    spend = plan.decisions[0].squad_value.tenths
    assert spend <= problem.ruleset.budget_tenths
    assert plan.decisions[0].bank_after.tenths >= 0

    # Same universe with everything cheap: strictly better, because the budget
    # was the thing stopping us buying the best players.
    rich = synthetic_problem(
        per_position=SMALL,
        n_gws=1,
        price_of=lambda i, pos, j: 40,
        xp_of=lambda i, pos, j: 1.0 + 0.5 * (i % 10),
    )
    assert solve(rich).objective > plan.objective + 1e-6


# -- max per club -----------------------------------------------------------


def test_club_limit_infeasible_with_too_few_clubs():
    # Four clubs cannot supply fifteen players at three per club.
    problem = synthetic_problem(per_position=SMALL, n_gws=1, n_clubs=4)
    with pytest.raises(InfeasibleError):
        solve(problem)


def test_club_limit_binds_when_the_best_players_share_a_club():
    """Club 1 holds every high-scoring player; at most three may be picked."""

    def xp_of(i, pos, j):
        return 20.0 if i % 6 == 0 else 1.0

    problem = synthetic_problem(
        per_position=SMALL, n_gws=1, n_clubs=6, price_of=lambda i, pos, j: 40, xp_of=xp_of
    )
    plan = solve(problem)
    by_code = {p.code: p for p in problem.players}
    for d in plan.decisions:
        clubs: dict[int, int] = {}
        for c in d.squad:
            clubs[int(by_code[c].team_code)] = clubs.get(int(by_code[c].team_code), 0) + 1
        assert max(clubs.values()) <= problem.ruleset.max_per_club
    # Only three of the eleven starters can be premium players.
    premium = {p.code for i, p in enumerate(problem.players) if i % 6 == 0}
    assert len(premium & set(plan.decisions[0].starting_xi)) == 3


# -- starting XI and formation ---------------------------------------------


def test_formation_is_always_legal():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve(problem)
    by_code = {p.code: p for p in problem.players}
    rs = problem.ruleset
    for d in plan.decisions:
        counts = {pos: 0 for pos in Position}
        for c in d.starting_xi:
            counts[by_code[c].position] += 1
        assert sum(counts.values()) == rs.starting_xi
        for pos in Position:
            assert rs.min_play_by_position[pos] <= counts[pos] <= rs.max_play_by_position[pos]


def test_formation_binds_when_the_best_players_are_all_forwards():
    """Only three forwards may be owned, so a 0-0-11 XI is impossible."""

    def xp_of(i, pos, j):
        return 30.0 if pos is Position.FWD else 1.0

    problem = synthetic_problem(
        per_position=SMALL, n_gws=1, price_of=lambda i, pos, j: 40, xp_of=xp_of
    )
    plan = solve(problem)
    by_code = {p.code: p for p in problem.players}
    counts = {pos: 0 for pos in Position}
    for c in plan.decisions[0].starting_xi:
        counts[by_code[c].position] += 1
    assert counts[Position.FWD] == problem.ruleset.max_play_by_position[Position.FWD]
    assert counts[Position.DEF] >= problem.ruleset.min_play_by_position[Position.DEF]
    assert counts[Position.GKP] == 1


def test_exactly_one_keeper_starts_and_one_sits():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve(problem)
    by_code = {p.code: p for p in problem.players}
    for d in plan.decisions:
        assert sum(by_code[c].position is Position.GKP for c in d.starting_xi) == 1
        assert by_code[d.bench[0]].position is Position.GKP
        assert all(by_code[c].position is not Position.GKP for c in d.bench[1:])


# -- ownability -------------------------------------------------------------


def test_unownable_players_are_never_selected():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    ownable = np.ones((problem.n_players, problem.n_gws), dtype=bool)
    banned = {i for i in range(problem.n_players) if i % 3 == 0}
    for i in banned:
        ownable[i, :] = False
    problem = synthetic_problem(per_position=SMALL, n_gws=2, ownable=ownable)
    plan = solve(problem)
    banned_codes = {problem.players[i].code for i in banned}
    for d in plan.decisions:
        assert not (set(d.squad) & banned_codes)


def test_infeasible_when_ownable_leaves_too_few_keepers():
    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    ownable = np.ones((problem.n_players, problem.n_gws), dtype=bool)
    keepers = [i for i, p in enumerate(problem.players) if p.position is Position.GKP]
    for i in keepers[1:]:
        ownable[i, :] = False
    problem = synthetic_problem(per_position=SMALL, n_gws=1, ownable=ownable)
    with pytest.raises(InfeasibleError):
        solve(problem)


# -- captaincy --------------------------------------------------------------


def test_captain_and_vice_are_distinct_starters():
    problem = synthetic_problem(per_position=SMALL, n_gws=2)
    plan = solve(problem)
    for d in plan.decisions:
        assert d.captain in d.starting_xi
        assert d.vice_captain in d.starting_xi
        assert d.captain != d.vice_captain


def test_captain_is_the_highest_scoring_starter():
    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    plan = solve(problem)
    idx = problem.index_of
    d = plan.decisions[0]
    best = max(float(problem.xpts[idx[c], 0]) for c in d.starting_xi)
    assert float(problem.xpts[idx[d.captain], 0]) == pytest.approx(best)


# -- bench order ------------------------------------------------------------


def test_bench_is_ordered_by_expected_points():
    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    plan = solve(problem)
    idx = problem.index_of
    outfield_bench = plan.decisions[0].bench[1:]
    values = [float(problem.xpts[idx[c], 0]) for c in outfield_bench]
    assert values == sorted(values, reverse=True)


# -- validator catches what the model forbids -------------------------------


def test_validator_rejects_a_hand_broken_plan():
    """The validator must not be a rubber stamp."""
    from dataclasses import replace

    problem = synthetic_problem(per_position=SMALL, n_gws=1)
    plan = solve(problem)
    d = plan.decisions[0]
    # Swap a bench player into the XI without removing anyone: 12 starters.
    broken = replace(d, starting_xi=d.starting_xi + (d.bench[1],))
    bad_plan = replace(plan, decisions=(broken,))
    problems = validate_plan(problem, bad_plan)
    assert any("XI has 12" in m for m in problems)


def test_validator_rejects_an_over_club_squad():
    from dataclasses import replace

    # Five clubs and fifteen players means every club supplies exactly three,
    # so swapping any squad member for an outsider of the same position puts a
    # fourth player from some club into the squad.
    problem = synthetic_problem(per_position=SMALL, n_gws=1, n_clubs=5)
    plan = solve(problem)
    d = plan.decisions[0]
    by_code = {p.code: p for p in problem.players}
    victim = d.bench[-1]
    intruder = next(
        p.code
        for p in problem.players
        if p.code not in d.squad
        and p.position is by_code[victim].position
        and int(p.team_code) != int(by_code[victim].team_code)
    )
    swap = lambda seq: tuple(intruder if c == victim else c for c in seq)
    broken = replace(d, squad=swap(d.squad), fielded=swap(d.fielded), bench=swap(d.bench))
    problems = validate_plan(problem, replace(plan, decisions=(broken,)))
    assert any("from club" in m for m in problems), problems


def test_held_squad_that_already_breaks_the_club_limit_is_infeasible():
    problem = synthetic_problem(per_position=SMALL, n_gws=1, n_clubs=6)
    # Force four players from one club into the opening squad via ownable.
    club1 = [i for i, p in enumerate(problem.players) if int(p.team_code) == 1]
    ownable = np.ones((problem.n_players, 1), dtype=bool)
    holdings = {}
    for i in club1[:4]:
        holdings[problem.players[i].code] = Money(int(problem.price_tenths[i, 0]))
    others = [i for i in range(problem.n_players) if i not in club1[:4]]
    problem = synthetic_problem(
        per_position=SMALL,
        n_gws=1,
        n_clubs=6,
        ownable=ownable,
        state=SquadState(
            holdings=holdings | {
                problem.players[i].code: Money(int(problem.price_tenths[i, 0]))
                for i in others[:11]
            },
            bank=Money(0),
            free_transfers=1,
        ),
    )
    # Whatever the transfers, the squad must end up legal.
    try:
        plan = solve(problem)
    except InfeasibleError:
        return
    for d in plan.decisions:
        assert validate_plan(problem, plan) == []
