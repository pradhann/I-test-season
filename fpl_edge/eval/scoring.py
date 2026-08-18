"""Exact gameweek scoring: autosubs, captaincy fallback and chips.

This is deliberately a pure function over realised outcomes. It is shared by the
backtest (scoring what actually happened) and the simulator (scoring each Monte
Carlo draw), which means a bug here would corrupt both in the same direction and
be invisible in comparison. Hence the unusually heavy test suite around it.

The rules encoded here are transcribed from docs/rules.md and re-read from the
rule registry at call time rather than hardcoded.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from fpl_edge.rules import rules
from fpl_edge.types import Position


class Chip(enum.StrEnum):
    NONE = "none"
    BENCH_BOOST = "bboost"
    TRIPLE_CAPTAIN = "3xc"
    WILDCARD = "wildcard"   # affects transfers, not scoring
    FREE_HIT = "freehit"    # affects transfers, not scoring


@dataclass(frozen=True, slots=True)
class Pick:
    """One of the 15 selected players.

    ``order`` is 1-15 exactly as FPL stores it: 1-11 are the starting XI and
    12-15 are the bench in substitution-priority order. For the bench, order 12
    is the reserve goalkeeper by convention only when the manager put them
    there; the GK substitution rule keys on position, not on slot.
    """

    code: int
    position: Position
    order: int
    is_captain: bool = False
    is_vice: bool = False

    @property
    def is_starter(self) -> bool:
        return self.order <= 11


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a player actually did (or did in one Monte Carlo draw)."""

    code: int
    minutes: int
    points: int

    @property
    def played(self) -> bool:
        """FPL's definition: an appearance on the pitch, or a card.

        We approximate 'received a card' by minutes > 0, because a player cannot
        be booked without being on the pitch. The edge case of a card for an
        unused substitute exists but does not trigger appearance points, and FPL
        treats such a player as having played for autosub purposes. It is rare
        enough that we flag it rather than model it.
        """
        return self.minutes > 0


@dataclass(frozen=True, slots=True)
class GwScore:
    """Result of scoring one gameweek."""

    total: int
    starters: tuple[int, ...]
    bench: tuple[int, ...]
    subs_made: tuple[tuple[int, int], ...]  # (off, on)
    captain: int | None
    captain_multiplier: int
    transfer_cost: int

    @property
    def net(self) -> int:
        return self.total - self.transfer_cost


def _formation_ok(positions: list[Position]) -> bool:
    r = rules()
    mn = r.get("squad.min_play_by_position")
    if len(positions) != r.get("squad.starting_xi"):
        return False
    counts = {p: positions.count(p) for p in Position}
    return (
        counts[Position.GKP] == 1
        and counts[Position.DEF] >= mn["DEF"]
        and counts[Position.FWD] >= mn["FWD"]
    )


def apply_autosubs(
    picks: list[Pick],
    outcomes: dict[int, Outcome],
) -> tuple[list[Pick], list[tuple[int, int]]]:
    """Return the effective starting XI after automatic substitutions.

    FPL's actual procedure, in order:

    1. If the starting goalkeeper did not play and the bench goalkeeper did,
       they swap. A goalkeeper can only ever be replaced by a goalkeeper.
    2. Bench outfielders are considered in priority order. Each is substituted
       in for a non-playing starter provided the resulting formation is still
       legal (1 GKP, >=3 DEF, >=1 FWD).

    Returns the effective XI and the list of (off, on) code pairs.
    """
    starters = sorted([p for p in picks if p.is_starter], key=lambda p: p.order)
    bench = sorted([p for p in picks if not p.is_starter], key=lambda p: p.order)
    subs: list[tuple[int, int]] = []

    def played(code: int) -> bool:
        o = outcomes.get(code)
        return o is not None and o.played

    # 1. Goalkeeper, handled separately and first.
    start_gk = next((p for p in starters if p.position is Position.GKP), None)
    bench_gk = next((p for p in bench if p.position is Position.GKP), None)
    if start_gk and bench_gk and not played(start_gk.code) and played(bench_gk.code):
        starters = [bench_gk if p.code == start_gk.code else p for p in starters]
        bench = [start_gk if p.code == bench_gk.code else p for p in bench]
        subs.append((start_gk.code, bench_gk.code))

    # 2. Outfielders, in bench priority order.
    for sub in [p for p in bench if p.position is not Position.GKP]:
        if not played(sub.code):
            continue
        # Replace the first non-playing starter that keeps the formation legal.
        for i, starter in enumerate(starters):
            if starter.position is Position.GKP or played(starter.code):
                continue
            candidate = list(starters)
            candidate[i] = sub
            if _formation_ok([p.position for p in candidate]):
                starters = candidate
                subs.append((starter.code, sub.code))
                break

    return starters, subs


def resolve_captain(
    picks: list[Pick],
    outcomes: dict[int, Outcome],
    chip: Chip,
) -> tuple[int | None, int]:
    """Return (captained player code, multiplier).

    If the captain played no minutes the vice-captain is promoted. If neither
    played, nobody is doubled. Note the multiplier applies to the *armband*, so
    Triple Captain still triples if the vice inherits it.
    """
    captain = next((p for p in picks if p.is_captain), None)
    vice = next((p for p in picks if p.is_vice), None)
    mult = 3 if chip is Chip.TRIPLE_CAPTAIN else 2

    def played(p: Pick | None) -> bool:
        return p is not None and (o := outcomes.get(p.code)) is not None and o.played

    if played(captain):
        return captain.code, mult  # type: ignore[union-attr]
    if played(vice):
        return vice.code, mult  # type: ignore[union-attr]
    return None, 1


def score_gameweek(
    picks: list[Pick],
    outcomes: dict[int, Outcome],
    *,
    chip: Chip = Chip.NONE,
    transfer_cost: int = 0,
) -> GwScore:
    """Score one gameweek exactly as FPL would.

    ``transfer_cost`` is a positive number of points to deduct (e.g. 4 for one
    hit); it is reported separately so gross and net are both visible.
    """
    r = rules()
    if len(picks) != r.get("squad.size"):
        raise ValueError(f"expected {r.get('squad.size')} picks, got {len(picks)}")
    if len({p.order for p in picks}) != len(picks):
        raise ValueError("pick orders must be unique 1..15")

    if chip is Chip.BENCH_BOOST:
        # Every player counts; no substitutions are possible or needed.
        scoring_players = list(picks)
        subs: list[tuple[int, int]] = []
    else:
        scoring_players, subs = apply_autosubs(picks, outcomes)

    cap_code, cap_mult = resolve_captain(picks, outcomes, chip)

    total = 0
    for p in scoring_players:
        o = outcomes.get(p.code)
        if o is None:
            continue
        mult = cap_mult if (cap_code is not None and p.code == cap_code) else 1
        total += o.points * mult

    starters = tuple(p.code for p in scoring_players)
    bench = tuple(p.code for p in picks if p.code not in set(starters))
    return GwScore(
        total=total,
        starters=starters,
        bench=bench,
        subs_made=tuple(subs),
        captain=cap_code,
        captain_multiplier=cap_mult if cap_code is not None else 1,
        transfer_cost=transfer_cost,
    )
