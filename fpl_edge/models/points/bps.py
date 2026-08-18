"""Bonus points from simulated match events.

Bonus is a *rank* statistic within a fixture, not a per-player quantity: the top
three BPS scores in each match take 3/2/1. That makes it irreducibly joint --
you cannot compute a player's bonus distribution without simulating everyone
else in the same match. This module therefore operates on a whole fixture at a
time.

The BPS weights are read from the rule registry, which transcribed them from the
official rules page.
"""

from __future__ import annotations

import numpy as np

from fpl_edge.rules import rules
from fpl_edge.types import Position


def bps_weights() -> dict[str, int]:
    return rules().get("bps.table")


def bps_from_events(
    *,
    position: Position,
    minutes: np.ndarray,
    goals: np.ndarray,
    assists: np.ndarray,
    clean_sheet: np.ndarray,
    saves: np.ndarray,
    goals_conceded: np.ndarray,
    yellow: np.ndarray,
    red: np.ndarray,
    penalties_saved: np.ndarray,
    penalties_missed: np.ndarray,
    own_goals: np.ndarray,
    cbi: np.ndarray,
    recoveries: np.ndarray,
    tackles: np.ndarray,
    pen_goals: np.ndarray | None = None,
) -> np.ndarray:
    """Vectorised BPS for one player across simulation draws.

    Only the components we actually simulate are included. Passing accuracy,
    dribbles, crosses, big chances and errors are omitted, which biases BPS
    downward roughly uniformly across players; since bonus depends only on the
    *ranking* within a fixture, a uniform downward bias is largely harmless, but
    it is not exactly harmless and is recorded in docs/known_weaknesses.md.
    """
    w = bps_weights()
    b = np.zeros_like(minutes, dtype=np.int64)

    b += np.where(minutes >= 60, w["play_over_60"], np.where(minutes > 0, w["play_1_to_60"], 0))

    pen = np.zeros_like(goals) if pen_goals is None else pen_goals
    open_goals = np.maximum(goals - pen, 0)
    if position is Position.GKP or position is Position.DEF:
        b += open_goals * w["goal_gkp_def_nonpen"]
    elif position is Position.MID:
        b += open_goals * w["goal_mid_nonpen"]
    else:
        b += open_goals * w["goal_fwd_nonpen"]
    b += pen * w["goal_from_penalty"]

    b += assists * w["assist"]

    if position in (Position.GKP, Position.DEF):
        b += clean_sheet * w["clean_sheet_gkp_def"]
        b += goals_conceded * w["goal_conceded_gkp_def"]

    b += saves * w["save"]
    b += penalties_saved * w["penalty_save"]
    b += penalties_missed * w["penalty_missed"]
    b += own_goals * w["own_goal"]
    b += yellow * w["yellow_card"]
    b += red * w["red_card"]

    # Integer division: BPS accrues per completed group of three.
    b += (cbi // 3) * w["per_3_clearances_blocks_interceptions"]
    b += (recoveries // 3) * w["per_3_recoveries"]
    b += tackles * w["successful_tackle"]
    return b


def allocate_bonus(bps: np.ndarray) -> np.ndarray:
    """Award 3/2/1 bonus by BPS rank within a fixture, honouring ties.

    ``bps`` is (n_players_in_fixture, n_sims). Returns bonus of the same shape.

    The rule that reproduces all three official worked examples is POSITIONAL,
    not distinct-value-based::

        bonus = max(0, 3 - (number of players with strictly greater BPS))

    Checked against the official examples:

    * tie for first  ``[40, 40, 30, 20]`` -> ``[3, 3, 1, 0]``
      -- note the third player gets 1, not 2. A distinct-value ranking would
      wrongly award 2 here, which is why this is worth spelling out.
    * tie for second ``[40, 30, 30, 20]`` -> ``[3, 2, 2, 0]``
    * tie for third  ``[40, 30, 20, 20]`` -> ``[3, 2, 1, 1]``
    """
    if bps.ndim != 2:
        raise ValueError("bps must be (n_players, n_sims)")
    # (n, n, sims): strictly_above[i, j, s] is True when player j beat player i.
    strictly_above = (bps[None, :, :] > bps[:, None, :]).sum(axis=1)
    return np.clip(3 - strictly_above, 0, 3).astype(np.int64)
