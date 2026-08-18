"""Bonus allocation, including the official tie-breaking examples."""

from __future__ import annotations

import numpy as np

from fpl_edge.models.points.bps import allocate_bonus, bps_from_events, bps_weights
from fpl_edge.types import Position


def col(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.int64).reshape(-1, 1)


def test_clean_ranking_awards_three_two_one() -> None:
    assert allocate_bonus(col(40, 30, 20, 10)).ravel().tolist() == [3, 2, 1, 0]


def test_tie_for_first_gives_two_threes_then_a_one() -> None:
    """Official example: players 1 and 2 get 3 each, player 3 gets 1."""
    assert allocate_bonus(col(40, 40, 30, 20)).ravel().tolist() == [3, 3, 1, 0]


def test_tie_for_second_gives_three_two_two() -> None:
    assert allocate_bonus(col(40, 30, 30, 20)).ravel().tolist() == [3, 2, 2, 0]


def test_tie_for_third_gives_three_two_one_one() -> None:
    assert allocate_bonus(col(40, 30, 20, 20)).ravel().tolist() == [3, 2, 1, 1]


def test_three_way_tie_for_first() -> None:
    assert allocate_bonus(col(40, 40, 40, 20)).ravel().tolist() == [3, 3, 3, 0]


def test_bonus_never_exceeds_three() -> None:
    rng = np.random.default_rng(0)
    bps = rng.integers(0, 60, size=(22, 200))
    bonus = allocate_bonus(bps)
    assert bonus.max() <= 3 and bonus.min() >= 0


def test_cbi_accrues_per_completed_group_of_three() -> None:
    w = bps_weights()
    z = np.zeros(4, dtype=np.int64)
    got = bps_from_events(
        position=Position.DEF,
        minutes=np.full(4, 90), goals=z, assists=z, clean_sheet=z, saves=z,
        goals_conceded=z, yellow=z, red=z, penalties_saved=z, penalties_missed=z,
        own_goals=z, cbi=np.array([2, 3, 5, 6]), recoveries=z, tackles=z,
    )
    base = w["play_over_60"]
    assert got.tolist() == [base, base + 1, base + 1, base + 2]


def test_forward_goal_is_worth_more_bps_than_a_defender_goal() -> None:
    z = np.zeros(1, dtype=np.int64)
    one = np.ones(1, dtype=np.int64)
    kw = dict(minutes=np.full(1, 90), assists=z, clean_sheet=z, saves=z,
              goals_conceded=z, yellow=z, red=z, penalties_saved=z,
              penalties_missed=z, own_goals=z, cbi=z, recoveries=z, tackles=z)
    fwd = bps_from_events(position=Position.FWD, goals=one, **kw)[0]
    dfd = bps_from_events(position=Position.DEF, goals=one, **kw)[0]
    assert fwd > dfd


def test_penalty_goals_score_less_bps_than_open_play() -> None:
    z = np.zeros(1, dtype=np.int64)
    one = np.ones(1, dtype=np.int64)
    kw = dict(minutes=np.full(1, 90), assists=z, clean_sheet=z, saves=z,
              goals_conceded=z, yellow=z, red=z, penalties_saved=z,
              penalties_missed=z, own_goals=z, cbi=z, recoveries=z, tackles=z)
    open_play = bps_from_events(position=Position.FWD, goals=one, **kw)[0]
    from_pen = bps_from_events(position=Position.FWD, goals=one, pen_goals=one, **kw)[0]
    assert from_pen < open_play
