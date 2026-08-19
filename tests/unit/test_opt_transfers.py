"""Transfers: the sell-on fee, free-transfer carryover, and hits.

The sell-on fee is the arithmetic most likely to be quietly wrong, because it
is the one place FPL rounds against you and the one place a float turns 7.6
into 7.5999999. The worked example from the rules -- bought at 7.5, now 7.8,
sells at 7.6 -- is driven end to end through an actual transfer here, not just
asserted against the helper function.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from fpl_edge.opt import (
    ObjectiveMode,
    OptimizerConfig,
    SquadState,
    replay_finances,
    solve_horizon,
    validate_plan,
)
from fpl_edge.types import Money, Position, selling_price
from tests.unit.test_opt_support import synthetic_problem

CONFIG = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset())

# The universe is laid out by position in the order GKP, DEF, MID, FWD, so with
# two extra midfield candidates the indices are fixed and worth naming:
GKP_SLOTS = range(2)
DEF_SLOTS = range(2, 7)
HELD_MIDS = range(7, 12)
MOVER = 7          # bought at 7.5m, worth 7.8m in the second gameweek
CHEAP, DEAR = 12, 13   # the two candidates, one tenth apart
FWD_SLOTS = range(14, 17)
SELL_SHAPE = {Position.GKP: 2, Position.DEF: 5, Position.MID: 7, Position.FWD: 3}


def _sell_on_fee_problem(*, candidate_price, mover_gw2_price: int = 78):
    """A squad holding one player bought at 7.5m and now worth 7.8m.

    Everyone else costs 4.0m, has never changed price, and has no replacement
    available at any price, so the only money in the system is whatever the
    mover sells for. That makes the plan a direct readout of the sell-on fee.
    """

    per_gw = (
        candidate_price if isinstance(candidate_price, tuple) else (candidate_price,) * 2
    )

    def price_of(i, pos, j):
        if i == MOVER:
            return 75 if j == 0 else mover_gw2_price
        if i == CHEAP:
            return per_gw[j]
        if i == DEAR:
            return per_gw[j] + 1
        return 40

    def xp_of(i, pos, j):
        if i == MOVER:
            return 0.0
        if i == CHEAP:
            return 20.0
        if i == DEAR:
            return 30.0   # strictly better, and exactly one tenth out of reach
        if i in HELD_MIDS:
            return 50.0   # never worth selling to raise cash
        return 5.0

    problem = synthetic_problem(
        per_position=SELL_SHAPE, n_gws=2, n_clubs=7, price_of=price_of, xp_of=xp_of
    )
    held = [p.code for i, p in enumerate(problem.players) if i not in (CHEAP, DEAR)]
    holdings = {
        problem.players[i].code: Money(int(problem.price_tenths[i, 0]))
        for i, p in enumerate(problem.players)
        if p.code in held
    }
    assert len(holdings) == problem.ruleset.squad_size
    return replace(
        problem,
        state=SquadState(holdings=holdings, bank=Money(0), free_transfers=1),
    )


def test_selling_price_helper_matches_the_official_worked_example():
    assert selling_price(Money(75), Money(78)) == Money(76)
    assert selling_price(Money(75), Money(79)) == Money(77)
    assert selling_price(Money(75), Money(72)) == Money(72)  # falls are borne in full
    assert selling_price(Money(75), Money(75)) == Money(75)


def _mover_and_candidates(problem):
    mids = [i for i, p in enumerate(problem.players) if p.position is Position.MID]
    mover = next(i for i in mids if int(problem.price_tenths[i, 0]) == 75)
    cands = sorted(
        (i for i in mids if float(problem.xpts[i, 0]) in (20.0, 30.0)),
        key=lambda i: int(problem.price_tenths[i, 1]),
    )
    return mover, cands


def test_a_player_bought_at_75_and_worth_78_sells_for_exactly_76():
    """One tenth either way changes which transfer is affordable."""
    problem = _sell_on_fee_problem(candidate_price=76)
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []

    gw1, gw2 = plan.decisions
    assert gw1.transfers_in == (), "nothing is affordable before the price rise"
    assert gw2.transfers_out == (problem.players[MOVER].code,)
    # 7.6m of proceeds buys the 7.6m candidate and nothing dearer, even though
    # the 7.7m one is worth ten more expected points.
    assert gw2.transfers_in == (problem.players[CHEAP].code,)
    assert gw2.bank_after == Money(0)
    assert gw2.hits == 0


def test_one_tenth_dearer_and_the_transfer_becomes_unaffordable():
    """The same squad with the cheapest candidate at 7.7m: no transfer at all.

    Together with the previous test this pins the sale at exactly 7.6m from
    both sides. A model that forgot the fee would have 7.8m and buy here; one
    that charged the fee twice would have 7.5m and fail the test above.
    """
    problem = _sell_on_fee_problem(candidate_price=77)
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []
    assert all(d.transfers_in == () for d in plan.decisions)


def test_a_price_fall_is_borne_in_full_through_a_transfer():
    # 7.5m bought, 7.2m now sells for 7.2m. A rule that split the change
    # symmetrically would give 7.5 + (7.2 - 7.5) / 2 = 7.35, floored to 7.3, so
    # a 7.3m candidate is exactly the probe that tells the two apart.
    problem = _sell_on_fee_problem(candidate_price=(76, 73), mover_gw2_price=72)
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []
    assert all(d.transfers_in == () for d in plan.decisions)

    # One tenth cheaper and the same sale does clear, so the squad really was
    # one tenth short rather than blocked by something else.
    reachable = _sell_on_fee_problem(candidate_price=(76, 72), mover_gw2_price=72)
    plan = solve_horizon(reachable, CONFIG)
    assert validate_plan(reachable, plan) == []
    assert plan.decisions[1].transfers_in == (reachable.players[CHEAP].code,)
    assert plan.decisions[1].bank_after == Money(0)


# -- free transfers ---------------------------------------------------------


def _static_squad_problem(n_gws: int, *, upgrades: int = 0, upgrade_gw: int = 0):
    """A held squad worth keeping, plus ``upgrades`` irresistible midfielders."""
    per_position = {Position.GKP: 2, Position.DEF: 5, Position.MID: 5 + upgrades, Position.FWD: 3}
    n_held = 15

    def xp_of(i, pos, j):
        held_slots = {
            Position.GKP: range(2),
            Position.DEF: range(2, 7),
            Position.MID: range(7, 12),
            Position.FWD: range(12 + upgrades, 15 + upgrades),
        }
        if any(i in r for r in held_slots.values()):
            return 5.0
        return 100.0 if j >= upgrade_gw else 0.0

    problem = synthetic_problem(
        per_position=per_position,
        n_gws=n_gws,
        n_clubs=7,
        price_of=lambda i, pos, j: 40,
        xp_of=xp_of,
    )
    held = [
        p.code for i, p in enumerate(problem.players) if float(problem.xpts[i, 0]) == 5.0
    ]
    assert len(held) == n_held, held
    return problem, held


def test_free_transfers_accumulate_and_cap_at_five():
    problem, held = _static_squad_problem(7)
    problem = replace(problem, state=SquadState(
            holdings={c: Money(40) for c in held}, bank=Money(0), free_transfers=1
        ))
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []
    assert all(d.transfers_in == () for d in plan.decisions)
    banked = [d.free_transfers_available for d in plan.decisions]
    assert banked == [1, 2, 3, 4, 5, 5, 5]
    assert max(banked) == problem.ruleset.max_banked_ft


def test_hits_are_charged_for_transfers_beyond_the_free_allowance():
    problem, held = _static_squad_problem(1, upgrades=3)
    problem = replace(problem, state=SquadState(
            holdings={c: Money(40) for c in held}, bank=Money(0), free_transfers=1
        ))
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []
    d = plan.decisions[0]
    assert len(d.transfers_in) == 3
    assert d.hits == 2  # one free, two paid
    assert d.hit_points == 8


def test_banking_a_transfer_beats_taking_a_hit_when_the_upgrade_can_wait():
    """Two upgrades that only pay from GW2 should be bought with two free transfers."""
    problem, held = _static_squad_problem(2, upgrades=2, upgrade_gw=1)
    problem = replace(problem, state=SquadState(
            holdings={c: Money(40) for c in held}, bank=Money(0), free_transfers=1
        ))
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []
    assert plan.decisions[0].transfers_in == ()
    assert len(plan.decisions[1].transfers_in) == 2
    assert plan.total_hits == 0


def test_ledger_replay_agrees_with_the_reported_plan():
    problem, held = _static_squad_problem(3, upgrades=2)
    problem = replace(problem, state=SquadState(
            holdings={c: Money(40) for c in held}, bank=Money(0), free_transfers=1
        ))
    plan = solve_horizon(problem, CONFIG)
    assert replay_finances(problem, plan) == []


def test_transfer_cap_is_read_from_the_registry():
    problem, _ = _static_squad_problem(1)
    # A fifteen-player squad can never make twenty transfers, so the cap is a
    # rule we carry and enforce rather than one that can bind. Recording it here
    # means a registry change is noticed.
    assert problem.ruleset.transfer_cap_per_gw == 20
    assert problem.ruleset.hit_cost == -4
    assert problem.ruleset.free_per_gw == 1


def test_preseason_gameweek_one_is_free_and_unlimited():
    problem = synthetic_problem(
        per_position={Position.GKP: 4, Position.DEF: 9, Position.MID: 9, Position.FWD: 5},
        n_gws=2,
    )
    assert problem.state.is_preseason
    assert problem.initial_bank == Money(problem.ruleset.budget_tenths)
    assert problem.initial_free_transfers == 0
    plan = solve_horizon(problem, CONFIG)
    assert validate_plan(problem, plan) == []
    assert len(plan.decisions[0].transfers_in) == problem.ruleset.squad_size
    assert plan.decisions[0].hits == 0
    assert plan.decisions[1].free_transfers_available == 1


def test_preseason_squad_fits_inside_the_hundred_million_budget():
    problem = synthetic_problem(
        per_position={Position.GKP: 4, Position.DEF: 9, Position.MID: 9, Position.FWD: 5},
        n_gws=1,
        price_of=lambda i, pos, j: 40 + (i % 12) * 6,
        xp_of=lambda i, pos, j: 1.0 + 0.4 * (i % 12),
    )
    plan = solve_horizon(problem, CONFIG)
    d = plan.decisions[0]
    assert d.squad_value.tenths + d.bank_after.tenths == problem.ruleset.budget_tenths
    assert d.squad_value.tenths <= 1000
    assert d.bank_after.tenths >= 0


@pytest.mark.parametrize("bought,now,expect", [(75, 78, 76), (75, 79, 77), (100, 111, 105)])
def test_sell_on_fee_floors_to_a_tenth(bought, now, expect):
    assert selling_price(Money(bought), Money(now)).tenths == expect
