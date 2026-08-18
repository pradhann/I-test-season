"""Scoring-engine tests.

Shared by the backtest and the simulator, so a bug here corrupts both in the
same direction and would be invisible in any comparison between them.
"""

from __future__ import annotations

import pytest

from fpl_edge.eval.scoring import Chip, Outcome, Pick, apply_autosubs, score_gameweek
from fpl_edge.types import Position as P

# A 1-4-4-2 with bench GK, DEF, MID, FWD in that priority order.
LAYOUT = [
    (1, P.GKP), (2, P.DEF), (3, P.DEF), (4, P.DEF), (5, P.DEF),
    (6, P.MID), (7, P.MID), (8, P.MID), (9, P.MID),
    (10, P.FWD), (11, P.FWD),
    (12, P.GKP), (13, P.DEF), (14, P.MID), (15, P.FWD),
]


def squad(captain: int = 10, vice: int = 6) -> list[Pick]:
    return [
        Pick(code=order, position=pos, order=order,
             is_captain=(order == captain), is_vice=(order == vice))
        for order, pos in LAYOUT
    ]


def outcomes(**overrides: tuple[int, int]) -> dict[int, Outcome]:
    """Everyone plays 90 for 2 points unless overridden with (minutes, points)."""
    base = {c: Outcome(c, 90, 2) for c, _ in LAYOUT}
    for code_str, (mins, pts) in overrides.items():
        code = int(code_str[1:])
        base[code] = Outcome(code, mins, pts)
    return base


def test_baseline_captain_doubles() -> None:
    s = score_gameweek(squad(), outcomes(p10=(90, 10)))
    # 10 starters at 2 = 20, plus captain 10 doubled = 20. Bench excluded.
    assert s.total == 20 + 20
    assert s.captain == 10 and s.captain_multiplier == 2


def test_bench_does_not_score_without_bench_boost() -> None:
    s = score_gameweek(squad(), outcomes(p15=(90, 20)))
    assert 15 not in s.starters
    # 11 starters at 2 = 22, plus the captain's doubling adding one more 2.
    assert s.total == 24


def test_captain_blank_promotes_vice() -> None:
    s = score_gameweek(squad(captain=10, vice=6), outcomes(p10=(0, 0), p6=(90, 9)))
    assert s.captain == 6 and s.captain_multiplier == 2
    # p10 blanks and is replaced by bench FWD 15 (2 pts). Ten others at 2,
    # of which the vice scores 9 doubled.
    assert s.total == (9 * 2) + (2 * 9) + 2


def test_both_captain_and_vice_blank_means_nobody_doubled() -> None:
    s = score_gameweek(squad(captain=10, vice=6), outcomes(p10=(0, 0), p6=(0, 0)))
    assert s.captain is None
    assert s.captain_multiplier == 1


def test_triple_captain_triples() -> None:
    s = score_gameweek(squad(), outcomes(p10=(90, 10)), chip=Chip.TRIPLE_CAPTAIN)
    assert s.captain_multiplier == 3
    assert s.total == 20 + 30


def test_triple_captain_still_triples_when_vice_inherits() -> None:
    s = score_gameweek(
        squad(captain=10, vice=6), outcomes(p10=(0, 0), p6=(90, 9)),
        chip=Chip.TRIPLE_CAPTAIN,
    )
    assert s.captain == 6 and s.captain_multiplier == 3


def test_goalkeeper_only_replaced_by_goalkeeper() -> None:
    starters, subs = apply_autosubs(squad(), outcomes(p1=(0, 0)))
    assert (1, 12) in subs
    assert {p.position for p in starters}.issuperset({P.GKP})
    assert sum(1 for p in starters if p.position is P.GKP) == 1


def test_bench_goalkeeper_who_also_blanked_does_not_come_on() -> None:
    _, subs = apply_autosubs(squad(), outcomes(p1=(0, 0), p12=(0, 0)))
    assert subs == []


def test_formation_floor_blocks_illegal_substitution() -> None:
    """A 3-at-the-back side losing a defender cannot bring on a midfielder."""
    layout = [
        (1, P.GKP), (2, P.DEF), (3, P.DEF), (4, P.DEF),
        (5, P.MID), (6, P.MID), (7, P.MID), (8, P.MID), (9, P.MID),
        (10, P.FWD), (11, P.FWD),
        (12, P.GKP), (13, P.MID), (14, P.MID), (15, P.FWD),  # no bench defender
    ]
    picks = [Pick(code=o, position=p, order=o, is_captain=(o == 10), is_vice=(o == 5))
             for o, p in layout]
    outs = {c: Outcome(c, 90, 2) for c, _ in layout}
    outs[2] = Outcome(2, 0, 0)  # a defender blanks

    starters, subs = apply_autosubs(picks, outs)
    # Bench has no defender, so the only legal move is to keep 3 at the back by
    # not substituting at all -- bringing on MID 13 would leave 2 defenders.
    assert all(on != 13 for _, on in subs)
    defs = sum(1 for p in starters if p.position is P.DEF)
    assert defs >= 3 or subs == []


def test_bench_priority_order_is_respected() -> None:
    """Two blanks, two eligible bench outfielders: the higher-priority one first."""
    outs = outcomes(p6=(0, 0), p7=(0, 0), p13=(90, 5), p14=(90, 7))
    _, subs = apply_autosubs(squad(), outs)
    ons = [on for _, on in subs]
    assert ons.index(13) < ons.index(14)


def test_bench_boost_counts_all_fifteen_and_makes_no_subs() -> None:
    s = score_gameweek(squad(), outcomes(), chip=Chip.BENCH_BOOST)
    assert len(s.starters) == 15
    assert s.subs_made == ()
    assert s.total == 15 * 2 + 2  # captain 10 doubled adds one extra 2


def test_bench_boost_does_not_autosub_a_blanking_starter() -> None:
    """With Bench Boost everyone already counts, so a blank is simply a zero."""
    s = score_gameweek(squad(), outcomes(p2=(0, 0)), chip=Chip.BENCH_BOOST)
    assert s.subs_made == ()
    assert s.total == 14 * 2 + 2


def test_transfer_cost_is_reported_separately_from_gross() -> None:
    s = score_gameweek(squad(), outcomes(), transfer_cost=8)
    assert s.total == 24
    assert s.net == 16


def test_yellow_card_with_zero_minutes_is_not_treated_as_played() -> None:
    """Documented approximation: we key 'played' on minutes > 0."""
    outs = outcomes(p2=(0, -1))
    starters, _ = apply_autosubs(squad(), outs)
    assert 2 not in [p.code for p in starters]


def test_wrong_squad_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="15 picks"):
        score_gameweek(squad()[:14], outcomes())


def test_duplicate_orders_are_rejected() -> None:
    bad = squad()
    bad[1] = Pick(code=99, position=P.DEF, order=1)
    with pytest.raises(ValueError, match="unique"):
        score_gameweek(bad, outcomes())
