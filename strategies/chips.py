"""Chip-timing policies, written against the 2026-27 rules rather than folklore.

Almost every piece of received wisdom about chip timing was formed under rules
that no longer apply, and applying it unchanged this season is a real error
rather than a pedantic one. From ``docs/rules.md``, verified against the API and
the official rules page:

* ``chips.count_each = 2`` -- **two** wildcards, two free hits, two bench boosts
  and two triple captains, one of each per half.
* ``chips.windows`` -- wildcard and free hit are available in GW2-19 and GW20-38.
  They are **not** available in GW1. Bench boost and triple captain are
  available from GW1.
* ``chips.freehit_not_consecutive`` -- a free hit played in GW19 does not make
  the second one usable until GW21.
* ``chips.wildcard_freehit_cancellable = False`` -- once confirmed, it is gone.

Older seasons were materially different: for several years there was one bench
boost and one triple captain for the entire season, and the free hit did not
exist before 2016/17. So "the winners bench-boosted in GW37" is a statement
about a season with one bench boost and tells you very little about a season
with two. Any cross-season chip analysis has to declare which rule regime each
observation came from, which is why :func:`fpl_edge.models.copying.features.chip_timing`
tags usages rather than pooling them.

These policies therefore express timing as a **calendar**, one entry per chip
instance, and they check the rule registry before playing anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fpl_edge.eval.replay import Decision, SquadState
from fpl_edge.eval.scoring import Chip
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

from strategies.base import PolicySpec, chip_window_allows, chips_used_count


@dataclass
class ChipCalendar:
    """Play each chip in a nominated gameweek, if the rules allow it then.

    The calendar maps a chip to the gameweeks its two instances should be played
    in. A gameweek outside the chip's window is skipped rather than attempted,
    and skipping is recorded implicitly by the chip going unplayed -- which is
    itself a result worth seeing in a backtest, because an unplayed chip is a
    real cost that a policy sweeping over impossible dates should be charged for.
    """

    calendar: dict[Chip, tuple[int, ...]] = field(default_factory=dict)
    name: str = "chip_calendar"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if decision.chip != Chip.NONE:
            # The inner strategy already asked for a chip. Overriding it would
            # make the comparison against the unwrapped inner strategy
            # uninterpretable, so the calendar defers.
            return decision
        for chip, weeks in self.calendar.items():
            if int(gw) not in weeks:
                continue
            if not chip_window_allows(chip, gw):
                continue
            if chips_used_count(state, chip) >= len(weeks):
                continue
            if state is not None and any(g == int(gw) for _c, g in state.chips_used):
                continue  # chips.one_per_gw
            return Decision(picks=decision.picks, chip=chip)
        return decision


@dataclass
class HoldWildcard:
    """Refuse to wildcard before ``not_before_gw``. The classic patience claim.

    The folk position is that the first wildcard should be held past the early
    international break, on the argument that the first few gameweeks reveal
    which promoted-side and cheap-enabler picks are real. The counter-position
    is that holding it wastes the weeks in which a broken squad is bleeding rank.

    This policy encodes only the constraint, not a claim about which is right --
    sweeping ``not_before_gw`` across the season is how the question gets
    answered, and the sweep will include values that are certainly too late.
    """

    not_before_gw: int = 8
    name: str = "hold_wildcard"

    def __post_init__(self) -> None:
        self.name = f"hold_wc>={self.not_before_gw}"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if decision.chip == Chip.WILDCARD and int(gw) < self.not_before_gw:
            # Strip the chip but keep the squad. That is deliberately a bad
            # outcome -- an unlimited-transfer squad without the wildcard behind
            # it is illegal and the harness will reject it -- and it is the
            # honest representation of "this policy forbade the move". A policy
            # that silently reverted the transfers as well would be measuring
            # two behaviours at once.
            return Decision(picks=decision.picks, chip=Chip.NONE)
        return decision


@dataclass
class BenchBoostOnDoubles:
    """Play bench boost in a gameweek where the squad has the most fixtures.

    The mechanism is not subtle: bench boost pays the four bench players'
    scores, and a bench player with two fixtures scores roughly twice as much as
    one with a single fixture. The policy holds the chip until a gameweek in
    which at least ``min_double_starters`` of the squad have more than one
    fixture, or until ``deadline_gw``, after which an unplayed chip is worth
    nothing and it is played regardless.
    """

    min_double_starters: int = 6
    deadline_gw: int = 37
    name: str = "bb_on_doubles"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if decision.chip != Chip.NONE or state is None:
            return decision
        if not chip_window_allows(Chip.BENCH_BOOST, gw):
            return decision
        if chips_used_count(state, Chip.BENCH_BOOST) >= 2:
            return decision

        if int(gw) >= self.deadline_gw:
            return Decision(picks=decision.picks, chip=Chip.BENCH_BOOST)

        fixtures = snapshot.upcoming_fixtures(season, horizon_gws=1)
        if fixtures.empty:
            return decision
        counts: dict[int, int] = {}
        for row in fixtures.itertuples():
            for club in (int(row.home_team_code), int(row.away_team_code)):
                counts[club] = counts.get(club, 0) + 1
        players = snapshot.players(season).set_index("code")
        doubles = 0
        for pick in decision.picks:
            club = players["team_code"].get(pick.code)
            if club is not None and counts.get(int(club), 0) > 1:
                doubles += 1
        if doubles >= self.min_double_starters:
            return Decision(picks=decision.picks, chip=Chip.BENCH_BOOST)
        return decision


#: The chip hypotheses worth sweeping, with the grid each is tested over.
SPECS: tuple[PolicySpec, ...] = (
    PolicySpec(
        name="hold_wildcard",
        build=lambda not_before_gw=8: HoldWildcard(not_before_gw=not_before_gw),
        grid={"not_before_gw": [2, 4, 6, 8, 10, 12, 16]},
        hypothesis="The first wildcard is worth more held past the early fixtures.",
        evidence="UNTESTED. Requires per-gameweek chip usage, which the API "
                 "publishes only for the season in progress; no past season's "
                 "chip dates are retrievable for any entry.",
    ),
    PolicySpec(
        name="bench_boost_on_doubles",
        build=lambda min_double_starters=6, deadline_gw=37: BenchBoostOnDoubles(
            min_double_starters=min_double_starters, deadline_gw=deadline_gw),
        grid={"min_double_starters": [4, 6, 8, 10], "deadline_gw": [30, 34, 37]},
        hypothesis="Bench boost is worth roughly its bench's fixture count.",
        evidence="Mechanically near-certain in direction; the open question is "
                 "the threshold, which the grid exists to find.",
    ),
)
