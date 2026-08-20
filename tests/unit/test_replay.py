"""Transfer, chip and budget mechanics under replay.

Each test encodes a rule from docs/rules.md that a naive backtest gets wrong.
"""

from __future__ import annotations

import pytest

from fpl_edge.eval.replay import (
    ChipWeekFtPolicy,
    Decision,
    InvalidDecision,
    SquadState,
    apply_decision,
    revert_free_hit,
)
from fpl_edge.eval.scoring import Chip, Pick
from fpl_edge.types import GwId, Position as P

LAYOUT = [
    (1, P.GKP), (2, P.DEF), (3, P.DEF), (4, P.DEF), (5, P.DEF),
    (6, P.MID), (7, P.MID), (8, P.MID), (9, P.MID),
    (10, P.FWD), (11, P.FWD),
    (12, P.GKP), (13, P.DEF), (14, P.MID), (15, P.FWD),
]
# Five clubs, three players each -- exactly at the max-per-club limit.
TEAM_OF = {code: (i // 3) + 1 for i, (code, _) in enumerate(LAYOUT)}
# Spare players each get their own club so tests can swap several in freely.
TEAM_OF.update({20 + i: 10 + i for i in range(10)})

PRICE = {code: 50 for code, _ in LAYOUT}
PRICE.update({20 + i: 50 for i in range(10)})

POS_OF = dict(LAYOUT) | {20: P.GKP, 21: P.DEF, 22: P.MID, 23: P.FWD, 24: P.DEF,
                         25: P.MID, 26: P.FWD, 27: P.DEF, 28: P.MID, 29: P.FWD}


def picks(captain: int = 10, vice: int = 6) -> tuple[Pick, ...]:
    return tuple(
        Pick(code=c, position=p, order=c, is_captain=(c == captain), is_vice=(c == vice))
        for c, p in LAYOUT
    )


def swap(base: tuple[Pick, ...], out_code: int, in_code: int) -> tuple[Pick, ...]:
    return tuple(
        Pick(code=in_code, position=POS_OF[in_code], order=p.order,
             is_captain=p.is_captain, is_vice=p.is_vice) if p.code == out_code else p
        for p in base
    )


def initial(gw: int = 1) -> SquadState:
    state, hits, out, into = apply_decision(None, Decision(picks()), PRICE, TEAM_OF, GwId(gw))
    assert hits == 0 and out == ()
    return state


def test_initial_squad_costs_nothing_and_banks_the_remainder() -> None:
    s = initial()
    assert s.bank_tenths == 1000 - 15 * 50  # 100.0m budget, 15 players at 5.0m
    assert s.free_transfers == 1


def test_one_free_transfer_costs_nothing() -> None:
    s = initial()
    new, hits, out, into = apply_decision(
        s, Decision(swap(picks(), 11, 26)), PRICE, TEAM_OF, GwId(2)
    )
    assert hits == 0 and out == (11,) and into == (26,)
    assert new.free_transfers == 1  # used the one, accrued one


def test_second_transfer_in_a_week_costs_four() -> None:
    s = initial()
    two = swap(swap(picks(), 11, 26), 9, 25)
    _, hits, out, into = apply_decision(s, Decision(two), PRICE, TEAM_OF, GwId(2))
    assert len(out) == 2 and len(into) == 2
    assert hits == 4


def test_free_transfers_accrue_and_cap_at_five() -> None:
    s = initial()
    for gw in range(2, 12):
        s, hits, _, _ = apply_decision(s, Decision(picks()), PRICE, TEAM_OF, GwId(gw))
        assert hits == 0
    assert s.free_transfers == 5  # never exceeds the banked maximum


def test_selling_price_applies_the_fifty_percent_fee() -> None:
    """Bought at 7.5, risen to 7.8, sells at 7.6 -- the official worked example."""
    s = initial()
    s = SquadState(
        picks=s.picks,
        bought_at={**s.bought_at, 11: 75},
        bank_tenths=0,
        free_transfers=1,
    )
    price = {**PRICE, 11: 78, 26: 76}
    new, _, _, _ = apply_decision(s, Decision(swap(picks(), 11, 26)), price, TEAM_OF, GwId(2))
    # Sold for 7.6 (keeping half the 0.3 rise, floored), bought in at 7.6.
    assert new.bank_tenths == 0


def test_price_fall_is_borne_in_full() -> None:
    s = initial()
    s = SquadState(picks=s.picks, bought_at={**s.bought_at, 11: 75},
                   bank_tenths=0, free_transfers=1)
    price = {**PRICE, 11: 70, 26: 70}
    new, _, _, _ = apply_decision(s, Decision(swap(picks(), 11, 26)), price, TEAM_OF, GwId(2))
    assert new.bank_tenths == 0  # sold at 7.0, bought at 7.0


def test_going_over_budget_is_rejected() -> None:
    s = initial()
    s = SquadState(picks=s.picks, bought_at=s.bought_at, bank_tenths=0, free_transfers=1)
    price = {**PRICE, 26: 200}  # a 20.0m player we cannot afford
    with pytest.raises(InvalidDecision, match="bank went negative"):
        apply_decision(s, Decision(swap(picks(), 11, 26)), price, TEAM_OF, GwId(2))


def test_more_than_three_from_one_club_is_rejected() -> None:
    s = initial()
    team = {**TEAM_OF, 26: 1}  # club 1 already has three players
    with pytest.raises(InvalidDecision, match="more than 3"):
        apply_decision(s, Decision(swap(picks(), 11, 26)), PRICE, team, GwId(2))


def test_wildcard_is_locked_in_gw1() -> None:
    with pytest.raises(InvalidDecision, match="not available in GW1"):
        apply_decision(None, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(1))


def test_free_hit_is_locked_in_gw1() -> None:
    with pytest.raises(InvalidDecision, match="not available in GW1"):
        apply_decision(None, Decision(picks(), chip=Chip.FREE_HIT), PRICE, TEAM_OF, GwId(1))


def test_bench_boost_is_allowed_in_gw1() -> None:
    state, hits, _, _ = apply_decision(
        None, Decision(picks(), chip=Chip.BENCH_BOOST), PRICE, TEAM_OF, GwId(1)
    )
    assert (Chip.BENCH_BOOST, 1) in state.chips_used


def test_wildcard_makes_all_transfers_free() -> None:
    s = initial()
    many = picks()
    for out_c, in_c in [(11, 26), (9, 25), (8, 28), (5, 27)]:
        many = swap(many, out_c, in_c)
    _, hits, out, _ = apply_decision(
        s, Decision(many, chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(4)
    )
    assert len(out) == 4
    assert hits == 0


def test_chip_week_retains_saved_free_transfers() -> None:
    s = initial()
    s = SquadState(picks=s.picks, bought_at=s.bought_at, bank_tenths=s.bank_tenths,
                   free_transfers=3)
    new, _, _, _ = apply_decision(
        s, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(4)
    )
    assert new.free_transfers == 3  # literal reading of the official wording

    accrue, _, _, _ = apply_decision(
        s, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(4),
        ft_policy=ChipWeekFtPolicy.RETAIN_AND_ACCRUE,
    )
    assert accrue.free_transfers == 4  # the alternative reading, one flag away


def test_same_chip_cannot_be_used_twice_in_one_half() -> None:
    s = initial()
    s, _, _, _ = apply_decision(s, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(4))
    with pytest.raises(InvalidDecision, match="already used"):
        apply_decision(s, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(9))


def test_second_half_wildcard_is_a_fresh_chip() -> None:
    s = initial()
    s, _, _, _ = apply_decision(s, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(4))
    s2, _, _, _ = apply_decision(s, Decision(picks(), chip=Chip.WILDCARD), PRICE, TEAM_OF, GwId(25))
    assert len([c for c, _ in s2.chips_used if c is Chip.WILDCARD]) == 2


def test_free_hit_cannot_be_played_in_consecutive_gameweeks() -> None:
    s = initial()
    s, _, _, _ = apply_decision(s, Decision(picks(), chip=Chip.FREE_HIT), PRICE, TEAM_OF, GwId(19))
    with pytest.raises(InvalidDecision, match="consecutive"):
        apply_decision(s, Decision(picks(), chip=Chip.FREE_HIT), PRICE, TEAM_OF, GwId(20))


def test_free_hit_squad_reverts_the_following_week() -> None:
    s = initial()
    original = s.codes
    fh = picks()
    for out_c, in_c in [(11, 26), (9, 25), (8, 28)]:
        fh = swap(fh, out_c, in_c)
    after, _, _, _ = apply_decision(s, Decision(fh, chip=Chip.FREE_HIT), PRICE, TEAM_OF, GwId(5))
    assert after.codes != original
    reverted = revert_free_hit(after)
    assert reverted.codes == original
    assert reverted.chips_used == after.chips_used  # the chip stays spent


def test_two_chips_in_one_gameweek_is_rejected() -> None:
    s = initial()
    s, _, _, _ = apply_decision(s, Decision(picks(), chip=Chip.BENCH_BOOST), PRICE, TEAM_OF, GwId(4))
    with pytest.raises(InvalidDecision, match="already played in GW4"):
        apply_decision(s, Decision(picks(), chip=Chip.TRIPLE_CAPTAIN), PRICE, TEAM_OF, GwId(4))


def test_unbalanced_transfers_are_rejected() -> None:
    s = initial()
    short = picks()[:14]
    with pytest.raises(InvalidDecision):
        apply_decision(s, Decision(short), PRICE, TEAM_OF, GwId(2))


def test_captain_must_be_a_starter() -> None:
    bad = tuple(
        Pick(code=c, position=p, order=c, is_captain=(c == 15), is_vice=(c == 6))
        for c, p in LAYOUT
    )
    with pytest.raises(InvalidDecision, match="starting XI"):
        apply_decision(None, Decision(bad), PRICE, TEAM_OF, GwId(1))


def test_held_squad_survives_a_real_world_club_move() -> None:
    """FPL grandfathers a 4-from-one-club squad created by a transfer window.

    Found in the 2025-26 backtest: a mid-season club move put a held player
    onto a club where three others were already owned, and every subsequent
    hold was rejected as illegal. Nobody is forced to sell; only newly bought
    players may push a club over the cap.
    """
    s = initial()
    # Player 11 "moves" to club 1, which already has players 1, 2, 3.
    moved = {**TEAM_OF, 11: 1}
    held, hits, _, _ = apply_decision(s, Decision(picks()), PRICE, moved, GwId(2))
    assert hits == 0  # holding is legal

    # But BUYING a fourth for that club is still refused. The incoming player
    # must be a MID like the one going out, or position validation fires first
    # and this stops testing the club rule at all.
    with pytest.raises(InvalidDecision, match="newly bought"):
        team = {**moved, 28: 1}
        apply_decision(held, Decision(swap(picks(), 9, 28)), PRICE, team, GwId(3))
