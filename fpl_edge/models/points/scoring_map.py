"""Deterministic map from a match stat line to FPL points.

Kept separate from the simulator so it can be validated directly against real
historical stat lines: given what a player actually did, this must reproduce the
``total_points`` the game awarded, to the point, for every row in the archive.
That test is the strongest single check on the whole points pipeline.

All weights come from the rule registry.
"""

from __future__ import annotations

import numpy as np

from fpl_edge.rules import rules
from fpl_edge.types import Position


def defensive_contribution_points(
    position: Position,
    *,
    clearances_blocks_interceptions: np.ndarray,
    tackles: np.ndarray,
    recoveries: np.ndarray,
) -> np.ndarray:
    """Defensive contribution points.

    Two traps live here:

    * The action sets differ by position. Defenders count CBI + tackles only.
      Midfielders and forwards additionally count recoveries. Feeding recoveries
      into the defender total inflates defenders substantially.
    * The award does not stack. Twenty CBIT is still 2 points, not 4.
    """
    r = rules()
    pts = r.get("defensive_contribution.points")[position.name]
    if pts == 0:
        return np.zeros_like(clearances_blocks_interceptions, dtype=np.int64)

    cbit = clearances_blocks_interceptions + tackles
    if position is Position.DEF:
        total = cbit
        threshold = r.get("defensive_contribution.def_threshold")
    else:
        total = cbit + recoveries
        threshold = r.get("defensive_contribution.mid_fwd_threshold")

    return (total >= threshold).astype(np.int64) * pts


def points_from_events(
    position: Position,
    *,
    minutes: np.ndarray,
    goals_scored: np.ndarray,
    assists: np.ndarray,
    clean_sheets: np.ndarray,
    goals_conceded: np.ndarray,
    own_goals: np.ndarray,
    penalties_saved: np.ndarray,
    penalties_missed: np.ndarray,
    yellow_cards: np.ndarray,
    red_cards: np.ndarray,
    saves: np.ndarray,
    bonus: np.ndarray,
    clearances_blocks_interceptions: np.ndarray | None = None,
    tackles: np.ndarray | None = None,
    recoveries: np.ndarray | None = None,
    defensive_contribution: np.ndarray | None = None,
) -> np.ndarray:
    """FPL points for one player across draws (or across historical rows).

    ``defensive_contribution`` may be supplied directly (the API reports it as a
    precomputed count) in which case the threshold is applied to it; otherwise
    the component actions must be supplied and the position-specific rule is
    applied to them.
    """
    r = rules()
    zeros = np.zeros_like(minutes, dtype=np.int64)

    pts = np.where(
        minutes >= 60, r.get("scoring.minutes_long"),
        np.where(minutes > 0, r.get("scoring.minutes_short"), 0),
    ).astype(np.int64)

    pts += goals_scored * r.get("scoring.goal")[position.name]
    pts += assists * r.get("scoring.assist")
    pts += clean_sheets * r.get("scoring.clean_sheet")[position.name]

    # -1 per two conceded, goalkeepers and defenders only. Integer division, so
    # a single goal conceded costs nothing.
    if position in (Position.GKP, Position.DEF):
        per = r.get("scoring.goals_conceded_per_penalty")
        pts += (goals_conceded // per) * r.get("scoring.goals_conceded")[position.name]

    pts += (saves // r.get("scoring.saves_per_point"))
    pts += penalties_saved * r.get("scoring.penalty_save")
    pts += penalties_missed * r.get("scoring.penalty_miss")
    pts += yellow_cards * r.get("scoring.yellow_card")
    pts += red_cards * r.get("scoring.red_card")
    pts += own_goals * r.get("scoring.own_goal")
    pts += bonus

    if defensive_contribution is not None:
        dc_pts = r.get("defensive_contribution.points")[position.name]
        threshold = (
            r.get("defensive_contribution.def_threshold")
            if position is Position.DEF
            else r.get("defensive_contribution.mid_fwd_threshold")
        )
        pts += (defensive_contribution >= threshold).astype(np.int64) * dc_pts
    elif clearances_blocks_interceptions is not None:
        pts += defensive_contribution_points(
            position,
            clearances_blocks_interceptions=clearances_blocks_interceptions,
            tackles=tackles if tackles is not None else zeros,
            recoveries=recoveries if recoveries is not None else zeros,
        )

    return pts
