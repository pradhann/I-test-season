"""Settling ideas against what actually happened.

Run after every gameweek finalises. It walks every open idea, records the
gameweeks that have landed, and closes the ones whose window is complete.

Two properties are the point of this module.

**It does not care whether the user acted.** ``acted`` is never read here. An idea
the user talked themselves out of is scored exactly as hard as one they made a
transfer for, because the counterfactual is the only thing that can tell them
whether their instincts or their second-guessing are the problem. Nothing else in
an FPL season records the transfers you did not make.

**The comparator was frozen before kickoff.** ``idea.as_of`` fixes the ownership,
prices and availability that defined "the median captain choice" or "the peers in
this price band". Recomputing that set now, with hindsight, would produce a
comparator selected partly by its own results -- the median would drift toward
whoever happened to do well, and the user's hit rate would become a measure of
nothing. So the set is rebuilt from a snapshot at ``as_of`` and the *only* thing
read from the present is the realised points of players already in it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from fpl_edge.interfaces.features import (
    _finished_results,
    comparator_set,
    gws_with_results,
    player_universe,
    realised_points,
)
from fpl_edge.interfaces.ideas import Idea, IdeaKind, IdeaStatus, Outcome
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc


@dataclass(frozen=True, slots=True)
class TrackResult:
    """What one pass of the tracker did."""

    observed: int = 0
    resolved: int = 0
    voided: int = 0
    still_open: int = 0
    notes: tuple[str, ...] = ()

    def render(self) -> str:
        return (
            f"tracked {self.observed} gameweek observations, resolved {self.resolved} ideas, "
            f"voided {self.voided}, {self.still_open} still open"
        )


def resolve_comparator_points(
    idea: Idea,
    *,
    frozen_players: pd.DataFrame,
    results: pd.DataFrame,
    gws: range,
) -> tuple[float | None, int]:
    """Points scored by the comparator over ``gws``, using the frozen set.

    The comparator's score is the **median across its members of each member's
    total over the window**, not the sum of per-gameweek medians. The distinction
    matters over a multi-week horizon: the per-week median is a different player
    each week and the resulting figure belongs to no one, which would make the
    thesis "beats the median" mean "beats a portfolio nobody could hold".
    """
    codes, _ = comparator_set(
        frozen_players,
        _subject_row(frozen_players, idea),
        idea.comparator,
        comparator_code=idea.comparator_code,
    )
    if not codes:
        return None, 0
    totals = realised_points(results, codes, gws)
    if totals.empty:
        return None, len(codes)
    return float(totals.median()), len(codes)


def _subject_row(players: pd.DataFrame, idea: Idea) -> pd.Series | None:
    if idea.subject_code is None or players.empty:
        return None
    hit = players[players["code"] == int(idea.subject_code)]
    return None if hit.empty else hit.iloc[0]


def _outcome(idea: Idea, subject: float, comparator: float) -> Outcome:
    """Apply the thesis's own resolution rule.

    A FADE thesis claims the subject scores *fewer*, so its comparison is
    inverted here rather than at the call site -- keeping the flip in one place
    is what stops a bearish idea from being scored as if it were bullish.
    """
    if subject == comparator:
        return Outcome.PUSH
    beat = subject > comparator
    if idea.kind is IdeaKind.FADE:
        return Outcome.CORRECT if not beat else Outcome.INCORRECT
    return Outcome.CORRECT if beat else Outcome.INCORRECT


def track(
    warehouse: Warehouse,
    *,
    season: str = "2026-27",
    now: dt.datetime | None = None,
    registry: IdeaRegistry | None = None,
) -> TrackResult:
    """Advance every open idea as far as the finalised results allow.

    Idempotent: observations are upserted per (idea, gameweek) and an already
    resolved idea is skipped, so this is safe to run on a timer.
    """
    when = (now or dt.datetime.now(UTC)).astimezone(UTC)
    reg = registry or IdeaRegistry(warehouse)
    snapshot_now = warehouse.snapshot_at(when)
    results = _finished_results(snapshot_now, season)
    finished = gws_with_results(results)

    observed = resolved = voided = still_open = 0
    notes: list[str] = []

    for idea in reg.ideas(season=season, status=IdeaStatus.OPEN):
        if idea.subject_code is None:
            reg.void(idea.idea_id, resolved_utc=when, reason="no resolved subject")
            voided += 1
            continue

        # The world as it was when the idea was had. This is the leakage-critical
        # line in the module: everything the comparator is built from comes from
        # here, and only realised points come from `results`.
        frozen = player_universe(warehouse.snapshot_at(idea.as_of), season)
        if frozen.empty:
            still_open += 1
            continue

        window = list(idea.gw_range)
        landed = [gw for gw in window if gw in finished]

        for gw in landed:
            subj = realised_points(results, [int(idea.subject_code)], range(gw, gw + 1))
            comp, n_peers = resolve_comparator_points(
                idea, frozen_players=frozen, results=results, gws=range(gw, gw + 1)
            )
            reg.record_observation(
                idea.idea_id,
                gw,
                observed_utc=when,
                subject_points=float(subj.iloc[0]) if not subj.empty else 0.0,
                comparator_points=comp,
                note=f"{n_peers} in frozen comparator set",
            )
            observed += 1

        if len(landed) < len(window):
            still_open += 1
            continue

        subject_total = realised_points(results, [int(idea.subject_code)], idea.gw_range)
        comp_total, n_peers = resolve_comparator_points(
            idea, frozen_players=frozen, results=results, gws=idea.gw_range
        )
        if comp_total is None:
            reg.void(
                idea.idea_id, resolved_utc=when,
                reason=f"comparator {idea.comparator} had no members at as_of",
            )
            voided += 1
            notes.append(f"{idea.idea_id}: voided, empty comparator")
            continue

        subject_points = float(subject_total.iloc[0]) if not subject_total.empty else 0.0
        outcome = _outcome(idea, subject_points, comp_total)
        reg.resolve(
            idea.idea_id,
            outcome=outcome,
            subject_points=subject_points,
            comparator_points=comp_total,
            resolved_utc=when,
        )
        resolved += 1
        notes.append(
            f"{idea.idea_id}: {outcome} ({subject_points:.0f} vs {comp_total:.0f} "
            f"over {idea.window_label}, {n_peers} peers)"
        )

    return TrackResult(
        observed=observed, resolved=resolved, voided=voided,
        still_open=still_open, notes=tuple(notes),
    )
