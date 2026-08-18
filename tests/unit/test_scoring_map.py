"""Stat line to points, including the rules people most often get wrong."""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.models.points.scoring_map import (
    defensive_contribution_points,
    points_from_events,
)
from fpl_edge.types import Position as P


def a(*v: int) -> np.ndarray:
    return np.array(v, dtype=np.int64)


def blank(n: int = 1) -> dict[str, np.ndarray]:
    z = np.zeros(n, dtype=np.int64)
    return dict(goals_scored=z, assists=z, clean_sheets=z, goals_conceded=z,
                own_goals=z, penalties_saved=z, penalties_missed=z,
                yellow_cards=z, red_cards=z, saves=z, bonus=z)


def test_appearance_points_hinge_at_sixty() -> None:
    got = points_from_events(P.MID, minutes=a(0, 1, 59, 60, 90), **blank(5))
    assert got.tolist() == [0, 1, 1, 2, 2]


def test_goal_value_by_position() -> None:
    for pos, expected in [(P.GKP, 10), (P.DEF, 6), (P.MID, 5), (P.FWD, 4)]:
        kw = blank() | {"goals_scored": a(1)}
        got = points_from_events(pos, minutes=a(90), **kw)
        assert got[0] == 2 + expected, pos


def test_clean_sheet_value_by_position() -> None:
    for pos, expected in [(P.GKP, 4), (P.DEF, 4), (P.MID, 1), (P.FWD, 0)]:
        kw = blank() | {"clean_sheets": a(1)}
        got = points_from_events(pos, minutes=a(90), **kw)
        assert got[0] == 2 + expected, pos


def test_goals_conceded_penalty_is_per_two_and_only_for_gkp_def() -> None:
    kw = blank(4) | {"goals_conceded": a(0, 1, 2, 3)}
    d = points_from_events(P.DEF, minutes=np.full(4, 90), **kw)
    assert (d - 2).tolist() == [0, 0, -1, -1]  # one goal costs nothing
    m = points_from_events(P.MID, minutes=np.full(4, 90), **kw)
    assert (m - 2).tolist() == [0, 0, 0, 0]


def test_saves_score_one_per_three() -> None:
    kw = blank(5) | {"saves": a(0, 2, 3, 5, 6)}
    got = points_from_events(P.GKP, minutes=np.full(5, 90), **kw)
    assert (got - 2).tolist() == [0, 0, 1, 1, 2]


def test_defensive_contribution_defenders_exclude_recoveries() -> None:
    """A defender on 9 CBIT and 20 recoveries scores nothing for defence."""
    got = defensive_contribution_points(
        P.DEF,
        clearances_blocks_interceptions=a(6, 8, 9),
        tackles=a(3, 2, 0),
        recoveries=a(0, 0, 20),
    )
    # 6+3=9 -> no, 8+2=10 -> yes, 9+0=9 with recoveries ignored -> no
    assert got.tolist() == [0, 2, 0]


def test_defensive_contribution_midfielders_include_recoveries() -> None:
    got = defensive_contribution_points(
        P.MID,
        clearances_blocks_interceptions=a(4, 4),
        tackles=a(2, 2),
        recoveries=a(5, 6),
    )
    # 4+2+5=11 -> no (threshold 12), 4+2+6=12 -> yes
    assert got.tolist() == [0, 2]


def test_defensive_contribution_does_not_stack() -> None:
    got = defensive_contribution_points(
        P.DEF, clearances_blocks_interceptions=a(20), tackles=a(10), recoveries=a(0)
    )
    assert got.tolist() == [2]


def test_goalkeepers_score_no_defensive_contribution() -> None:
    got = defensive_contribution_points(
        P.GKP, clearances_blocks_interceptions=a(30), tackles=a(0), recoveries=a(0)
    )
    assert got.tolist() == [0]


def test_forwards_score_defensive_contribution() -> None:
    """Forwards are eligible at the 12 threshold, as in 2025-26.

    Not a 2026/27 change, contrary to an earlier assumption here: replaying the
    map over 2025-26 reproduces all 3,278 forward rows exactly, and 9 of them
    cleared the threshold. Had forwards been ineligible that season those rows
    would each be 2 points out.

    In practice this is a rounding error for forwards -- 9 qualifying rows in
    3,278 appearances, 0.27% -- so it should not drive forward selection.
    """
    got = defensive_contribution_points(
        P.FWD, clearances_blocks_interceptions=a(6), tackles=a(3), recoveries=a(3)
    )
    assert got.tolist() == [2]


def test_precomputed_defensive_contribution_column_is_thresholded() -> None:
    kw = blank(3) | {"defensive_contribution": a(9, 10, 25)}
    got = points_from_events(P.DEF, minutes=np.full(3, 90), **kw)
    assert (got - 2).tolist() == [0, 2, 2]


def test_full_haul_stat_line() -> None:
    """A defender: 90 minutes, goal, assist, clean sheet, 3 bonus, DC hit."""
    kw = blank() | {
        "goals_scored": a(1), "assists": a(1), "clean_sheets": a(1), "bonus": a(3),
        "defensive_contribution": a(12),
    }
    got = points_from_events(P.DEF, minutes=a(90), **kw)
    # 2 appearance + 6 goal + 3 assist + 4 CS + 3 bonus + 2 DC
    assert got[0] == 20


def test_red_card_and_own_goal_are_negative() -> None:
    kw = blank() | {"red_cards": a(1), "own_goals": a(1), "yellow_cards": a(1)}
    got = points_from_events(P.MID, minutes=a(45), **kw)
    assert got[0] == 1 - 3 - 2 - 1
