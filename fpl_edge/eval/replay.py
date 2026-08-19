"""Walk-forward season replay.

Replays a full season under a strategy, enforcing the real mechanics: free
transfer accrual and carryover, points hits, the 50% sell-on fee against the
price the player was actually bought at, chip windows, Free Hit squad reversion,
autosubs and captaincy fallback.

The strategy only ever sees a Snapshot taken at the gameweek deadline, so it is
structurally incapable of seeing a price, an injury update or a result that was
not public at the moment it decided. That is the whole point of the harness: a
backtest that can see the future measures nothing.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass, field, replace
from typing import Protocol

from fpl_edge.eval.scoring import Chip, GwScore, Outcome, Pick, score_gameweek
from fpl_edge.rules import rules
from fpl_edge.store import Snapshot, Warehouse
from fpl_edge.types import GwId, Money, Season, selling_price


class ChipWeekFtPolicy(enum.StrEnum):
    """How free transfers accrue in a week a Wildcard or Free Hit is played.

    The official wording is "if you had 2 saved free transfers, you will still
    have 2 saved free transfers the Gameweek after playing the chip". That is
    explicit that saved transfers are retained but silent on whether the week's
    own accrual also applies. We refuse to guess silently: the policy is an
    explicit parameter, the literal reading is the default, and the alternative
    is one flag away for sensitivity analysis.
    """

    RETAIN_ONLY = "retain_only"      # literal reading: f' = f
    RETAIN_AND_ACCRUE = "retain_and_accrue"  # f' = min(cap, f + 1)


class InvalidDecision(ValueError):
    """A strategy proposed something the game would have rejected."""


@dataclass(frozen=True, slots=True)
class SquadState:
    """Everything carried between gameweeks.

    ``bought_at`` is essential and is the field people forget: selling price
    depends on the price you paid, not on the price at the start of the season.
    """

    picks: tuple[Pick, ...]
    bought_at: dict[int, int]      # code -> purchase price in tenths
    bank_tenths: int
    free_transfers: int
    chips_used: tuple[tuple[Chip, GwId], ...] = ()
    pre_freehit: "SquadState | None" = None

    @property
    def codes(self) -> frozenset[int]:
        return frozenset(p.code for p in self.picks)

    def squad_value(self, price_now: dict[int, int]) -> int:
        """Sale value of the squad: what we would bank if we sold everything."""
        return sum(
            selling_price(Money(self.bought_at[p.code]), Money(price_now[p.code])).tenths
            for p in self.picks
        )


@dataclass(frozen=True, slots=True)
class Decision:
    """What a strategy chose to do at one deadline."""

    picks: tuple[Pick, ...]        # the full 15 AFTER transfers, with order/captain set
    chip: Chip = Chip.NONE

    def transfers_from(self, state: SquadState) -> tuple[frozenset[int], frozenset[int]]:
        new = frozenset(p.code for p in self.picks)
        old = state.codes
        return old - new, new - old  # (out, in)


class Strategy(Protocol):
    """A decision rule under test.

    ``snapshot`` is taken at the gameweek deadline. Reading anything else is a
    leak, and the audit suite checks for it.
    """

    name: str

    def decide(
        self, snapshot: Snapshot, state: SquadState | None, season: Season, gw: GwId
    ) -> Decision:
        ...


@dataclass(frozen=True, slots=True)
class GwResult:
    gw: GwId
    score: GwScore
    transfers_in: tuple[int, ...]
    transfers_out: tuple[int, ...]
    hits: int
    chip: Chip
    free_transfers_before: int
    bank_after: int
    squad_value_after: int


@dataclass
class ReplayResult:
    season: Season
    strategy: str
    gws: list[GwResult] = field(default_factory=list)

    @property
    def total_points(self) -> int:
        return sum(g.score.net for g in self.gws)

    @property
    def gross_points(self) -> int:
        return sum(g.score.total for g in self.gws)

    @property
    def total_hits(self) -> int:
        return sum(g.score.transfer_cost for g in self.gws)

    def cumulative(self) -> list[int]:
        out, run = [], 0
        for g in self.gws:
            run += g.score.net
            out.append(run)
        return out


def _validate_squad(picks: tuple[Pick, ...], price: dict[int, int],
                    team_of: dict[int, int], bank: int,
                    held: frozenset[int] = frozenset()) -> None:
    r = rules()
    if len(picks) != r.get("squad.size"):
        raise InvalidDecision(f"squad must contain {r.get('squad.size')} players")
    if len({p.code for p in picks}) != len(picks):
        raise InvalidDecision("duplicate player in squad")
    if sorted(p.order for p in picks) != list(range(1, r.get("squad.size") + 1)):
        raise InvalidDecision("pick orders must be exactly 1..15")

    want = r.get("squad.select_by_position")
    for pos_name, n in want.items():
        got = sum(1 for p in picks if p.position.name == pos_name)
        if got != n:
            raise InvalidDecision(f"need {n} {pos_name}, got {got}")

    limit = r.get("squad.max_per_club")
    by_club: dict[int, list[int]] = {}
    for p in picks:
        by_club.setdefault(team_of[p.code], []).append(p.code)
    for club, codes in by_club.items():
        if len(codes) <= limit:
            continue
        # A real-world transfer can move a HELD player onto a club where the
        # manager already owns three: FPL grandfathers that (nobody is forced
        # to sell). The cap therefore binds only when a newly BOUGHT player
        # contributes to the excess.
        if any(c not in held for c in codes):
            raise InvalidDecision(
                f"more than {limit} players from club {club} "
                f"(a newly bought player contributes to the excess)"
            )

    if bank < 0:
        raise InvalidDecision(f"bank went negative: {Money(bank)}")

    if sum(1 for p in picks if p.is_captain) != 1:
        raise InvalidDecision("exactly one captain required")
    if sum(1 for p in picks if p.is_vice) != 1:
        raise InvalidDecision("exactly one vice-captain required")
    cap = next(p for p in picks if p.is_captain)
    vic = next(p for p in picks if p.is_vice)
    if not cap.is_starter or not vic.is_starter:
        raise InvalidDecision("captain and vice must be in the starting XI")


def _chip_allowed(chip: Chip, gw: GwId, used: tuple[tuple[Chip, GwId], ...]) -> None:
    """Enforce chip windows, one-per-half limits and the Free Hit spacing rule."""
    if chip in (Chip.NONE,):
        return
    windows = rules().get("chips.windows")
    if chip.value not in windows:
        raise InvalidDecision(f"unknown chip {chip}")

    halves = windows[chip.value]
    in_half = [i for i, (lo, hi) in enumerate(halves) if lo <= gw <= hi]
    if not in_half:
        raise InvalidDecision(
            f"{chip.value} is not available in GW{gw} (windows {halves}). "
            "Note Wildcard and Free Hit are locked in GW1."
        )
    half = in_half[0]
    already = [g for c, g in used if c is chip and halves[half][0] <= g <= halves[half][1]]
    if already:
        raise InvalidDecision(f"{chip.value} already used in GW{already[0]} this half")

    if any(g == gw for _, g in used):
        raise InvalidDecision(f"another chip is already played in GW{gw}")

    if chip is Chip.FREE_HIT:
        prior = [g for c, g in used if c is Chip.FREE_HIT]
        if any(abs(gw - g) == 1 for g in prior):
            raise InvalidDecision("Free Hit cannot be played in consecutive gameweeks")


def apply_decision(
    state: SquadState | None,
    decision: Decision,
    price: dict[int, int],
    team_of: dict[int, int],
    gw: GwId,
    *,
    ft_policy: ChipWeekFtPolicy = ChipWeekFtPolicy.RETAIN_ONLY,
) -> tuple[SquadState, int, tuple[int, ...], tuple[int, ...]]:
    """Apply transfers, returning (new state, hit cost, transfers_out, transfers_in)."""
    r = rules()
    budget = r.get("squad.budget_tenths")
    cap_ft = r.get("transfers.max_banked")
    hit = -r.get("transfers.hit_cost")  # registry stores -4; cost is positive here

    if state is None:
        # Initial squad selection: unlimited free transfers before the first deadline.
        spend = sum(price[p.code] for p in decision.picks)
        bank = budget - spend
        new = SquadState(
            picks=decision.picks,
            bought_at={p.code: price[p.code] for p in decision.picks},
            bank_tenths=bank,
            free_transfers=1,
            chips_used=((decision.chip, gw),) if decision.chip is not Chip.NONE else (),
        )
        _validate_squad(new.picks, price, team_of, bank)  # initial squad: nothing held
        if decision.chip is not Chip.NONE:
            _chip_allowed(decision.chip, gw, ())
        return new, 0, (), tuple(sorted(p.code for p in decision.picks))

    _chip_allowed(decision.chip, gw, state.chips_used)
    out, into = decision.transfers_from(state)
    n = len(into)
    if n != len(out):
        raise InvalidDecision(f"transfers must balance: {len(out)} out, {n} in")

    proceeds = sum(
        selling_price(Money(state.bought_at[c]), Money(price[c])).tenths for c in out
    )
    cost = sum(price[c] for c in into)
    bank = state.bank_tenths + proceeds - cost

    free = state.free_transfers
    if decision.chip in (Chip.WILDCARD, Chip.FREE_HIT):
        hits = 0
        next_free = free if ft_policy is ChipWeekFtPolicy.RETAIN_ONLY else min(cap_ft, free + 1)
    else:
        cap_n = r.get("transfers.cap_per_gw")
        if n > cap_n:
            raise InvalidDecision(f"{n} transfers exceeds the {cap_n} per-gameweek cap")
        paid = max(0, n - free)
        hits = paid * hit
        next_free = min(cap_ft, max(0, free - n) + 1)

    bought_at = dict(state.bought_at)
    for c in out:
        bought_at.pop(c, None)
    for c in into:
        bought_at[c] = price[c]

    new = SquadState(
        picks=decision.picks,
        bought_at=bought_at,
        bank_tenths=bank,
        free_transfers=next_free,
        chips_used=state.chips_used + (((decision.chip, gw),) if decision.chip is not Chip.NONE else ()),
        pre_freehit=state if decision.chip is Chip.FREE_HIT else None,
    )
    # Players carried over are grandfathered against club moves; only the
    # newly bought may push a club over the cap.
    _validate_squad(new.picks, price, team_of, bank,
                    held=frozenset(state.codes - out))
    return new, hits, tuple(sorted(out)), tuple(sorted(into))


def revert_free_hit(state: SquadState) -> SquadState:
    """After a Free Hit gameweek the squad returns to what it was before.

    Free transfers and chip usage carry forward from the Free Hit week; only the
    players revert.
    """
    if state.pre_freehit is None:
        return state
    prior = state.pre_freehit
    return replace(
        prior,
        free_transfers=state.free_transfers,
        chips_used=state.chips_used,
        pre_freehit=None,
    )
