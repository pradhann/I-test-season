"""Overlay policies: a decision rule expressed as a modification of another one.

A finding from the manager-mining work is almost never a complete strategy. "The
elite hold their first wildcard past GW8" says nothing about which fifteen
players to own; it constrains *one* dimension of a decision that some other rule
has to make. Writing each finding as a standalone :class:`~fpl_edge.eval.replay.Strategy`
would mean re-implementing squad selection inside every one of them, and would
make it impossible to tell whether a backtest's result came from the finding or
from the squad selection that had to be bundled with it.

So a policy here wraps an inner strategy and adjusts its output. That gives the
property that makes these results interpretable: **run the inner strategy alone,
then run it wrapped, and the difference is attributable to the policy.** The
backtester gets a controlled comparison rather than two unrelated numbers.

Every policy is parameterised, because a finding stated as a threshold ("past
GW8") is a hypothesis about where a threshold lies, and the only way to learn
whether GW8 matters is to sweep it and see whether the curve has a shape.

Legality is not the policy's job to guess. :mod:`fpl_edge.eval.replay` already
enforces chip windows, squad composition, budget and the transfer cap, and it
raises :class:`~fpl_edge.eval.replay.InvalidDecision` rather than silently
correcting. Policies here check what they can cheaply (a chip's window, from the
rule registry) and let the harness reject anything else, because a policy that
quietly fixes up its own illegal output is a policy whose backtest is measuring
the fix-up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from fpl_edge.eval.replay import Decision, SquadState, Strategy
from fpl_edge.eval.scoring import Chip, Pick
from fpl_edge.rules import rules
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season


class Policy(Protocol):
    """One parameterised behaviour, applied on top of an inner decision."""

    name: str

    def adjust(
        self,
        decision: Decision,
        snapshot: Snapshot,
        state: SquadState | None,
        season: Season,
        gw: GwId,
    ) -> Decision:
        ...


@dataclass
class PolicyStrategy:
    """An inner strategy plus an ordered stack of policies.

    Order matters and is not commutative: a chip policy that plays a wildcard
    must run before a transfer-limit policy, or the limit will claw back the
    unlimited transfers the wildcard exists to permit. The stack is applied in
    the order given and the order is part of the strategy's identity, which is
    why it appears in :attr:`name`.
    """

    inner: Strategy
    policies: tuple[Policy, ...] = ()
    label: str | None = None

    @property
    def name(self) -> str:
        if self.label:
            return self.label
        chain = "+".join(p.name for p in self.policies)
        return f"{self.inner.name}[{chain}]" if chain else self.inner.name

    def decide(
        self, snapshot: Snapshot, state: SquadState | None, season: Season, gw: GwId
    ) -> Decision:
        decision = self.inner.decide(snapshot, state, season, gw)
        for policy in self.policies:
            decision = policy.adjust(decision, snapshot, state, season, gw)
        return decision


def chip_window_allows(chip: Chip, gw: GwId) -> bool:
    """Whether the rule registry permits this chip in this gameweek.

    Read from ``chips.windows`` rather than hard-coded, because the 2026-27
    rules differ from every previous season's: two of each chip, one per half,
    with **wildcard and free hit locked out of GW1** while bench boost and
    triple captain are not. A policy with GW1 wildcarding baked in would be
    silently illegal, and a policy with last season's single-chip assumption
    baked in would leave half its chips unplayed.
    """
    if chip == Chip.NONE:
        return True
    windows = rules().get("chips.windows")
    spans = windows.get(str(chip.value)) or windows.get(chip.name.lower())
    if not spans:
        return False
    return any(start <= int(gw) <= stop for start, stop in spans)


def chips_used_count(state: SquadState | None, chip: Chip) -> int:
    if state is None:
        return 0
    return sum(1 for c, _gw in state.chips_used if c == chip)


def transfers_in_decision(decision: Decision, state: SquadState | None) -> int:
    """How many transfers this decision represents. Zero on the opening squad."""
    if state is None:
        return 0
    out, _in = decision.transfers_from(state)
    return len(out)


def revert_transfers(
    decision: Decision, state: SquadState, keep: int
) -> Decision:
    """Undo all but ``keep`` of the proposed transfers, cheapest reversion first.

    Used by policies that cap activity. Reverting is deliberately dumb -- it
    restores the outgoing players in squad order -- because a clever reversion
    would be an optimiser, and then the backtest would be measuring the
    reverter rather than the cap.

    Returns the original decision untouched when it is already within the cap,
    so a policy that never binds costs nothing and shows up as an exact tie
    against the unwrapped inner strategy.
    """
    out, incoming = decision.transfers_from(state)
    if len(out) <= keep:
        return decision

    drop_incoming = sorted(incoming)[: len(incoming) - keep]
    restore = sorted(out)[: len(out) - keep]
    by_code = {p.code: p for p in decision.picks}
    old_by_code = {p.code: p for p in state.picks}

    picks: list[Pick] = []
    restore_iter = iter(restore)
    for pick in decision.picks:
        if pick.code in drop_incoming:
            code = next(restore_iter, None)
            if code is None:
                picks.append(pick)
                continue
            original = old_by_code[code]
            # Keep the SLOT the incoming player was going to occupy, so the
            # formation the inner strategy chose survives the reversion. Copying
            # the restored player's old slot instead would routinely produce an
            # illegal XI.
            picks.append(Pick(code=original.code, position=original.position,
                              order=pick.order, is_captain=pick.is_captain,
                              is_vice=pick.is_vice))
        else:
            picks.append(by_code[pick.code])
    return Decision(picks=tuple(picks), chip=decision.chip)


@dataclass
class NullPolicy:
    """Does nothing. Exists so a sweep can include 'no policy' as a real arm."""

    name: str = "none"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        return decision


@dataclass
class PolicySpec:
    """A policy factory plus the grid of parameters worth sweeping.

    The grid is part of the finding, not an afterthought. A hypothesis of the
    form "hold the wildcard until GW8" is only testable against the alternatives
    it is competing with, and recording them here stops the sweep from being
    quietly narrowed to the value that happened to win.
    """

    name: str
    build: object
    grid: dict[str, list] = field(default_factory=dict)
    hypothesis: str = ""
    evidence: str = ""
