"""Hit policies: how freely to spend 4 points to change your mind.

A hit is a straightforward bet -- pay 4 now, expect more than 4 back over the
horizon you are buying. What makes it interesting is that the two failure modes
are opposite and both common. The passive manager never takes a hit and carries
a broken squad through an international break. The active manager takes eleven
hits by GW20 and has spent 44 points, which is roughly a top-10k season's entire
margin, on churn.

The policies here are caps and floors on that behaviour, parameterised so a
sweep can find where the cost curve turns rather than asserting a number.

A caveat that matters for interpreting any result these produce: the harness
prices hits exactly (``transfers.hit_cost = -4`` per extra transfer, deducted at
the start of the next gameweek, with free transfers accruing to a cap of five
per ``transfers.max_banked``). What it cannot price is the *reason* the inner
strategy wanted the transfer. A cap that forbids a hit to replace a
season-ending injury and a cap that forbids a hit to chase last week's hat-trick
look identical here and are not remotely the same decision. Reading a hit-cap
sweep as "hits are bad" rather than "this inner strategy's hits were bad" is the
mistake to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fpl_edge.eval.replay import Decision, SquadState
from fpl_edge.rules import rules
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

from strategies.base import PolicySpec, revert_transfers, transfers_in_decision


@dataclass
class HitCap:
    """Never take more than ``max_hits_per_gw`` extra transfers in one gameweek.

    Reverts the excess rather than blocking the gameweek, so the strategy still
    makes its highest-priority moves. Which ones survive is decided by
    :func:`strategies.base.revert_transfers`, which is deliberately arbitrary --
    a smart chooser would be an optimiser and the backtest would measure it
    instead of the cap.
    """

    max_hits_per_gw: int = 1
    name: str = "hit_cap"

    def __post_init__(self) -> None:
        self.name = f"hitcap<={self.max_hits_per_gw}/gw"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if state is None:
            return decision
        made = transfers_in_decision(decision, state)
        allowed = state.free_transfers + self.max_hits_per_gw
        if made <= allowed:
            return decision
        return revert_transfers(decision, state, allowed)


@dataclass
class SeasonHitBudget:
    """Spend at most ``max_hits`` extra transfers across the whole season.

    Stateful, and deliberately so: a per-gameweek cap and a season budget are
    different hypotheses. The cap says "never do more than one at a time"; the
    budget says "you get twelve of these all year, spend them where they matter"
    and permits a four-transfer wildcard-substitute week early if the rest of
    the season is quiet.

    The counter is reset by :meth:`reset`, which the backtester must call at the
    start of each replayed season. Forgetting to would carry a spent budget into
    the next season and silently turn the policy into "no hits at all".
    """

    max_hits: int = 12
    _spent: int = field(default=0, init=False, repr=False)
    name: str = "season_hit_budget"

    def __post_init__(self) -> None:
        self.name = f"hitbudget<={self.max_hits}/season"

    def reset(self) -> None:
        self._spent = 0

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if state is None:
            self._spent = 0
            return decision
        made = transfers_in_decision(decision, state)
        extra = max(0, made - state.free_transfers)
        remaining = max(0, self.max_hits - self._spent)
        if extra <= remaining:
            self._spent += extra
            return decision
        allowed = state.free_transfers + remaining
        self._spent += remaining
        return revert_transfers(decision, state, allowed)


@dataclass
class NoHitsAfter:
    """Stop taking hits from ``gw`` onward.

    Encodes the run-in version of hit discipline: late hits have fewer gameweeks
    to repay themselves, so the break-even expected gain per week rises sharply
    as the season closes. With ``h`` gameweeks left, a 4-point hit needs to earn
    more than ``4/h`` points per week, which at h=2 is a demand almost no
    transfer meets.
    """

    gw: int = 33
    name: str = "no_hits_after"

    def __post_init__(self) -> None:
        self.name = f"nohits>=gw{self.gw}"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if state is None or int(gw) < self.gw:
            return decision
        return revert_transfers(decision, state, state.free_transfers)


def break_even_gain_per_week(gws_remaining: int) -> float:
    """Points per week a 4-point hit must earn to be worth taking.

    Pure arithmetic from ``transfers.hit_cost``, exposed because it is the
    number that should be quoted whenever a late hit is proposed and almost
    never is.
    """
    cost = abs(rules().get("transfers.hit_cost"))
    return float(cost) / max(gws_remaining, 1)


SPECS: tuple[PolicySpec, ...] = (
    PolicySpec(
        name="hit_cap",
        build=lambda max_hits_per_gw=1: HitCap(max_hits_per_gw=max_hits_per_gw),
        grid={"max_hits_per_gw": [0, 1, 2, 3]},
        hypothesis="Capping hits per gameweek beats unconstrained churn.",
        evidence="PARTIALLY TESTABLE NOW. Hits per gameweek are readable for the "
                 "season in progress from /entry/{id}/history/ 'current' "
                 "(event_transfers_cost) with no picks endpoint needed, so the "
                 "elite's realised hit rate becomes measurable from GW1 onward.",
    ),
    PolicySpec(
        name="season_hit_budget",
        build=lambda max_hits=12: SeasonHitBudget(max_hits=max_hits),
        grid={"max_hits": [0, 4, 8, 12, 20]},
        hypothesis="A season-long allowance beats a per-week cap, because it "
                   "permits concentrating hits in the few decisive weeks.",
        evidence="UNTESTED. Distinguishing it from hit_cap requires per-gameweek "
                 "hit timing, which arrives with the same 'current' block.",
    ),
    PolicySpec(
        name="no_hits_after",
        build=lambda gw=33: NoHitsAfter(gw=gw),
        grid={"gw": [26, 30, 33, 36, 39]},
        hypothesis="Late hits cannot repay themselves and should be forbidden.",
        evidence="Break-even arithmetic is exact; the open question is where the "
                 "inner strategy's expected gain crosses 4/h, which the grid "
                 "brackets. gw=39 is the never-binds control arm.",
    ),
)
