"""Reconstructing the manager's squad state from public data alone.

The awkward truth this module is organised around: FPL publishes a manager's
picks only *after* the gameweek has kicked off, and the endpoint that would show
them beforehand needs the account password. So the state of the team splits into
three tiers, and the code says which tier every number came from rather than
presenting a uniform, confident answer:

**Observed.** Straight off a public endpoint. The 15 and their order once a
gameweek has started; the price paid for anyone bought via a transfer; bank and
squad value at every past deadline; which chips were played and when.

**Derived.** Computed from observed facts plus the verified rule registry, with
no free parameters. Purchase prices for players never transferred (they were
bought at the season-start price, and prices do not move before the season
starts). Banked free transfers, which FPL exposes nowhere public but which the
accrual rule determines exactly -- and which we then *check* against the hits the
manager actually paid, because `event_transfers_cost` is observed and a wrong
free-transfer count would predict the wrong hit.

**Unavailable.** Named explicitly in :attr:`MyTeamState.unavailable` and never
filled in with a plausible-looking guess. The squad before the first kickoff of a
gameweek is the big one; per-season transfer counts for previous seasons are the
other, because FPL issues a new entry id every season and publishes no mapping
from a manager to their old ids.

Every reconstruction is pushed through :func:`fpl_edge.eval.replay.apply_decision`,
which is the same code the backtest uses and which enforces the real game: 15
players, 2/5/5/3, a hundred million, three to a club, a legal formation, a
captain and vice in the XI, and chip windows. If a reconstruction will not
survive that, the reconstruction is wrong -- not the validator.
"""

from __future__ import annotations

import datetime as dt
import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from fpl_edge.eval.replay import Decision, InvalidDecision, SquadState, apply_decision
from fpl_edge.eval.scoring import Chip, Pick
from fpl_edge.myteam.sources import (
    ChipPlay,
    EntryHistory,
    EntrySummary,
    GwPicks,
    PublicEntryClient,
    TransferRow,
)
from fpl_edge.rules import rules
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Money, Position, selling_price

if TYPE_CHECKING:  # pragma: no cover - import cycle: manual.py needs PlayerIndex
    from fpl_edge.myteam.manual import ManualSquadRecord

UTC = dt.timezone.utc

#: Named so a caller can branch on provenance without string matching.
class Provenance(enum.StrEnum):
    """Where the 15 came from. Never inferred, always recorded."""

    PUBLIC_PICKS = "public_picks"   # observed: the gameweek has started
    MANUAL = "manual"               # the manager told us, and confirmed it back
    NONE = "none"                   # we genuinely do not know


class ReconstructionError(RuntimeError):
    """The public data does not support a squad state we can stand behind."""


@dataclass(frozen=True, slots=True)
class ChipStatus:
    """Which chips are left, per half of the season.

    Chips come two of each -- one usable in GW1-19, one in GW20-38 -- and the
    windows differ by chip (Wildcard and Free Hit are locked in GW1; Bench Boost
    and Triple Captain are not). Collapsing that to a single "3 chips left" is
    the kind of summary that reads fine and then loses a Wildcard.
    """

    chip: str
    windows: tuple[tuple[int, int], ...]
    played: tuple[GwId, ...]

    @property
    def remaining(self) -> int:
        return sum(self.remaining_in(i) for i in range(len(self.windows)))

    def remaining_in(self, half: int) -> int:
        lo, hi = self.windows[half]
        used = sum(1 for gw in self.played if lo <= int(gw) <= hi)
        return max(0, 1 - used)

    def available_in_gw(self, gw: int) -> bool:
        for i, (lo, hi) in enumerate(self.windows):
            if lo <= gw <= hi:
                return self.remaining_in(i) > 0
        return False

    def describe(self) -> str:
        halves = ", ".join(
            f"GW{lo}-{hi}: {self.remaining_in(i)}"
            for i, (lo, hi) in enumerate(self.windows)
        )
        return f"{self.chip} ({halves})"


@dataclass(frozen=True, slots=True)
class LedgerCheck:
    """One cross-check of a derived number against an observed one."""

    name: str
    derived: str
    observed: str
    ok: bool
    note: str = ""

    def render(self) -> str:
        mark = "ok" if self.ok else "MISMATCH"
        tail = f" -- {self.note}" if self.note else ""
        return f"[{mark}] {self.name}: derived {self.derived}, observed {self.observed}{tail}"


@dataclass(frozen=True, slots=True)
class MyTeamState:
    """Everything the engine knows about the manager's team, with provenance.

    ``gw`` is the gameweek this state is *entering*: the next deadline. ``picks``
    is None when the squad is genuinely unknown, which is the pre-GW1 case and
    which callers must handle rather than defaulting to an empty squad.
    """

    entry_id: int
    season: str
    gw: GwId
    as_of: dt.datetime

    picks: tuple[Pick, ...] | None
    bought_at: Mapping[int, int]          # code -> purchase price in tenths
    bank_tenths: int
    free_transfers: int
    chips_used: tuple[tuple[Chip, GwId], ...] = ()
    provenance: Provenance = Provenance.NONE

    #: Manager-level facts straight off /api/entry/.
    team_name: str = ""
    manager_name: str = ""
    total_transfers_this_entry: int = 0
    past_seasons: tuple[tuple[str, int, int], ...] = ()   # (season, points, rank)

    #: What we could not obtain and did not invent. Rendered in every report.
    unavailable: tuple[str, ...] = ()
    #: Numbers computed from rules rather than read off an endpoint.
    derived: tuple[str, ...] = ()
    #: Derived-vs-observed reconciliations, including the failures.
    checks: tuple[LedgerCheck, ...] = ()

    @property
    def known(self) -> bool:
        return self.picks is not None

    @property
    def codes(self) -> frozenset[int]:
        return frozenset(p.code for p in self.picks) if self.picks else frozenset()

    @property
    def bank(self) -> Money:
        return Money(self.bank_tenths)

    @property
    def failures(self) -> tuple[LedgerCheck, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def squad_value(self, price_now: Mapping[int, int]) -> Money:
        """Sale value: what the bank would hold if everything were sold today."""
        if not self.picks:
            return Money(0)
        return Money(
            sum(
                selling_price(Money(self.bought_at[p.code]), Money(int(price_now[p.code]))).tenths
                for p in self.picks
            )
        )

    def team_value(self, price_now: Mapping[int, int]) -> Money:
        """Squad sale value plus bank -- the number FPL shows as 'Value'."""
        return self.squad_value(price_now) + self.bank

    def chip_status(self) -> tuple[ChipStatus, ...]:
        windows = rules().get("chips.windows")
        return tuple(
            ChipStatus(
                chip=name,
                windows=tuple((int(a), int(b)) for a, b in spans),
                played=tuple(gw for chip, gw in self.chips_used if str(chip) == name),
            )
            for name, spans in sorted(windows.items())
        )

    def to_squad_state(self) -> SquadState:
        """The replay/backtest view of this state.

        Raises rather than fabricating an empty squad when the 15 are unknown:
        an optimiser handed a zero-player 'current squad' will happily recommend
        fifteen transfers.
        """
        if self.picks is None:
            raise ReconstructionError(
                f"the squad for {self.season} GW{self.gw} is not known. Before a "
                f"gameweek kicks off FPL publishes nobody's picks, and "
                f"/api/my-team/{self.entry_id}/ needs the account password, which "
                f"this engine does not have and will not ask for. Enter the 15 "
                f"once with `fpl myteam set` (or /setsquad in the bot)."
            )
        return SquadState(
            picks=self.picks,
            bought_at=dict(self.bought_at),
            bank_tenths=self.bank_tenths,
            free_transfers=self.free_transfers,
            chips_used=self.chips_used,
        )

    def validate(self, price_now: Mapping[int, int], team_of: Mapping[int, int]) -> SquadState:
        """Push the reconstruction through the real rule enforcement.

        For a squad that has never made a transfer this is exactly the initial
        selection the game validated at the GW1 deadline, so
        ``apply_decision(None, ...)`` against the *purchase* prices reproduces
        the game's own £100.0m check. For a squad mid-season the budget check no
        longer applies (price rises legitimately push squad value above 100), so
        the transition form is used instead: an unchanged 15 applied to the
        previous state, which still enforces shape, club limits, formation,
        captaincy and a non-negative bank.
        """
        state = self.to_squad_state()
        decision = Decision(picks=state.picks, chip=Chip.NONE)
        if not self.chips_used and self._is_initial_squad():
            purchase = {p.code: self.bought_at[p.code] for p in state.picks}
            checked, _hits, _out, _into = apply_decision(
                None, decision, purchase, dict(team_of), GwId(1)
            )
            budget = rules().get("squad.budget_tenths")
            if checked.bank_tenths != self.bank_tenths:
                raise InvalidDecision(
                    f"reconstructed bank {Money(self.bank_tenths)} does not match "
                    f"{Money(budget)} minus the {Money(sum(purchase.values()))} paid "
                    f"for the squad ({Money(checked.bank_tenths)}). One of the "
                    f"purchase prices is wrong."
                )
            return checked
        # Mid-season: validate the no-op transition off the state itself.
        prior = replace(state, picks=state.picks)
        checked, hits, out, into = apply_decision(
            prior, decision, dict(price_now), dict(team_of), self.gw
        )
        assert not out and not into and hits == 0
        return checked

    def _is_initial_squad(self) -> bool:
        """True when nothing has been bought or sold since the squad was built."""
        return self.total_transfers_this_entry == 0

    def render(self) -> str:
        lines = [
            f"Entry {self.entry_id} — {self.team_name}"
            + (f" ({self.manager_name})" if self.manager_name else ""),
            f"{self.season}, entering GW{self.gw}. Squad source: {self.provenance.value}.",
        ]
        if self.picks:
            lines.append(
                f"15 players, bank {self.bank}, "
                f"{self.free_transfers} free transfer(s), "
                f"{self.total_transfers_this_entry} transfer(s) made this season."
            )
        else:
            lines.append(
                f"Squad unknown. Bank {self.bank}, "
                f"{self.total_transfers_this_entry} transfer(s) made this season."
            )
        lines.append("Chips: " + "; ".join(c.describe() for c in self.chip_status()))
        if self.derived:
            lines.append("Derived, not observed: " + "; ".join(self.derived))
        if self.unavailable:
            lines.append("Not available without a login: " + "; ".join(self.unavailable))
        for check in self.checks:
            lines.append("  " + check.render())
        return "\n".join(lines)


# -- identity mapping --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlayerIndex:
    """element_id <-> code, plus the per-player facts a squad state needs.

    Built from a point-in-time snapshot, so it is the player universe as it was
    at that instant. ``element_id`` is per-season and reassigned; ``code`` is
    stable. Everything stored keys on code, and element_id survives only long
    enough to translate an API payload.
    """

    code_by_element: Mapping[int, int]
    element_by_code: Mapping[int, int]
    position: Mapping[int, Position]
    team_code: Mapping[int, int]
    price_now: Mapping[int, int]
    start_price: Mapping[int, int]
    name: Mapping[int, str]

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, season: str) -> "PlayerIndex":
        players = snapshot.players(season)
        if players.empty:
            raise ReconstructionError(
                f"no players visible at {snapshot.as_of:%Y-%m-%d %H:%M}Z for {season}; "
                "run `make ingest` first"
            )
        state = snapshot.table("fact_player_state", where="season = ?", params=[season])
        # start price = today's price minus the total change since season start.
        # The rule registry records that prices do not move before the season
        # begins, so pre-season this is simply today's price.
        change = (
            dict(zip(state["code"].astype(int), state["cost_change_start"].fillna(0).astype(int)))
            if "cost_change_start" in state.columns
            else {}
        )
        codes = players["code"].astype(int)
        price = dict(zip(codes, players["price_tenths"].astype(int)))
        return cls(
            code_by_element=dict(zip(players["element_id"].astype(int), codes)),
            element_by_code=dict(zip(codes, players["element_id"].astype(int))),
            position={c: Position(int(p)) for c, p in zip(codes, players["position"])},
            team_code=dict(zip(codes, players["team_code"].astype(int))),
            price_now=price,
            start_price={c: int(px) - int(change.get(c, 0)) for c, px in price.items()},
            name=dict(zip(codes, players["web_name"].astype(str))),
        )

    def code(self, element_id: int) -> int:
        try:
            return self.code_by_element[int(element_id)]
        except KeyError:
            raise ReconstructionError(
                f"element_id {element_id} is not in the snapshot's player universe. "
                "element_id is per-season and reassigned; the snapshot is probably "
                "for a different season, or the warehouse is stale."
            ) from None


# -- purchase prices ---------------------------------------------------------


def purchase_prices(
    codes: Sequence[int],
    transfers: Sequence[TransferRow],
    index: PlayerIndex,
    *,
    up_to_gw: int,
) -> dict[int, int]:
    """The price actually paid for each currently-owned player, in tenths.

    Two sources, in priority order:

    1. The most recent transfer *in* at or before ``up_to_gw``. ``element_in_cost``
       is the price paid, which is precisely what the 50% sell-on fee is measured
       against. A player bought, sold and bought again takes the latest purchase.
    2. Otherwise the player has been owned since the squad was built, so the
       price paid is the season-start price. Prices do not change before the
       season starts (``prices.no_change_before_season``), so for anyone in the
       original 15 the start price *is* the purchase price.

    Using today's price instead of the purchase price is the classic error: it
    overstates the sale proceeds of every player who has risen, by half the rise
    each, and hands the optimiser a budget it does not have.
    """
    want = {int(c) for c in codes}
    paid: dict[int, int] = {}
    for row in transfers:
        if int(row.gw) > int(up_to_gw):
            continue
        code_in = index.code(row.element_in)
        if code_in in want:
            paid[code_in] = row.element_in_cost.tenths
        code_out = index.code(row.element_out)
        # A player sold is no longer held at that price; if he is re-bought a
        # later row overwrites this. Dropping him keeps the chain honest.
        paid.pop(code_out, None)

    out: dict[int, int] = {}
    for code in want:
        if code in paid:
            out[code] = paid[code]
            continue
        try:
            out[code] = int(index.start_price[code])
        except KeyError:
            raise ReconstructionError(
                f"no purchase price for code {code}: never transferred in, and not "
                f"in the snapshot's price table."
            ) from None
    return out


# -- free transfers ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FreeTransferLedger:
    """Free transfers entering each gameweek, plus the hits that proves it.

    FPL exposes the banked free-transfer count on no public endpoint. The
    accrual rule determines it exactly, though, and the result is falsifiable:
    ``event_transfers_cost`` *is* published, and equals
    ``4 * max(0, transfers - free)``. If our count were wrong we would predict
    the wrong hit somewhere, so every reconstruction re-derives the hits and
    compares.
    """

    entering: Mapping[int, int]
    checks: tuple[LedgerCheck, ...]

    @property
    def consistent(self) -> bool:
        return all(c.ok for c in self.checks)


def derive_free_transfers(
    history: EntryHistory, *, up_to_gw: int
) -> FreeTransferLedger:
    """Walk the accrual rule forward over the gameweeks actually played."""
    r = rules()
    cap = int(r.get("transfers.max_banked"))
    per_gw = int(r.get("transfers.free_per_gw"))
    hit_cost = -int(r.get("transfers.hit_cost"))
    chip_by_gw = {int(c.gw): c.name for c in history.chips}

    entering: dict[int, int] = {}
    checks: list[LedgerCheck] = []
    # Zero entering GW1, and that is not "none available": transfers before the
    # first deadline are unlimited and free, so there is no banked count to
    # carry. The single free transfer for GW2 is generated by the normal
    # carryover rule, not banked from GW1.
    free = 0
    played = [row for row in history.current if int(row.gw) <= int(up_to_gw)]
    for row in played:
        gw = int(row.gw)
        entering[gw] = free
        chip = chip_by_gw.get(gw)
        n = int(row.event_transfers)
        if gw == 1:
            predicted_hit = 0
            free = per_gw
        elif chip in ("wildcard", "freehit"):
            # Wildcard / Free Hit weeks: saved transfers are retained. The
            # registry says so; the replay harness makes the alternative reading
            # an explicit flag rather than a silent choice.
            predicted_hit = 0
        else:
            predicted_hit = hit_cost * max(0, n - free)
            free = min(cap, max(0, free - free_used(n, free)) + per_gw)
        observed_hit = int(row.event_transfers_cost)
        checks.append(
            LedgerCheck(
                name=f"GW{gw} hit",
                derived=f"-{predicted_hit}",
                observed=f"-{observed_hit}",
                ok=predicted_hit == observed_hit,
                note=(
                    ""
                    if predicted_hit == observed_hit
                    else f"{n} transfer(s) against {entering[gw]} free; the "
                         "free-transfer reconstruction disagrees with the hit "
                         "actually paid"
                ),
            )
        )
    entering[int(up_to_gw) + 1] = free
    return FreeTransferLedger(entering=entering, checks=tuple(checks))


def free_used(n_transfers: int, free: int) -> int:
    """Free transfers consumed by ``n`` transfers. Never more than you had."""
    return min(n_transfers, free)


# -- picks -------------------------------------------------------------------


def picks_from_public(public: GwPicks, index: PlayerIndex) -> tuple[Pick, ...]:
    """Translate the public picks payload into the engine's Pick tuple.

    FPL's ``position`` field is the 1-15 slot order, which is exactly what
    :class:`~fpl_edge.eval.scoring.Pick.order` means, so the two line up without
    reinterpretation. Note the multiplier is deliberately ignored: it encodes the
    chip and the captaincy, both of which are carried separately, and reading the
    XI off multiplier > 0 breaks under Bench Boost.
    """
    out = []
    for p in sorted(public.picks, key=lambda x: x.position):
        code = index.code(p.element)
        out.append(
            Pick(
                code=code,
                position=index.position[code],
                order=int(p.position),
                is_captain=bool(p.is_captain),
                is_vice=bool(p.is_vice_captain),
            )
        )
    return tuple(out)


CHIP_BY_API_NAME = {
    "wildcard": Chip.WILDCARD,
    "freehit": Chip.FREE_HIT,
    "bboost": Chip.BENCH_BOOST,
    "3xc": Chip.TRIPLE_CAPTAIN,
}


def chips_used_from(history: EntryHistory) -> tuple[tuple[Chip, GwId], ...]:
    out = []
    for c in history.chips:
        chip = CHIP_BY_API_NAME.get(c.name)
        if chip is None:
            raise ReconstructionError(
                f"unknown chip {c.name!r} played in GW{c.gw}. The chip list is "
                "verified in the rule registry; a new one means the registry is "
                "stale and the chip windows cannot be trusted."
            )
        out.append((chip, c.gw))
    return tuple(out)


# -- the reconstruction ------------------------------------------------------


#: Stated once, reused everywhere, so the wording of what we cannot see does not
#: drift between the CLI, the bot and the weekly report.
NO_PICKS_BEFORE_KICKOFF = (
    "the current squad before the gameweek kicks off (FPL publishes picks only "
    "after the deadline; /api/my-team/ needs the account password)"
)
NO_PAST_SEASON_TRANSFERS = (
    "per-season transfer counts before this season (FPL issues a new entry id "
    "each season and publishes no mapping from a manager to their old ids, so "
    "/api/entry/{id}/transfers/ can only ever cover the current entry)"
)


def reconstruct(
    snapshot: Snapshot,
    *,
    entry_id: int,
    season: str,
    client: PublicEntryClient | None = None,
    entry: EntrySummary | None = None,
    history: EntryHistory | None = None,
    transfers: Sequence[TransferRow] | None = None,
    picks: GwPicks | None = None,
    manual: "ManualSquadRecord | None" = None,
    gw: int | None = None,
    validate: bool = True,
) -> MyTeamState:
    """Assemble the manager's state from public data, plus a manual squad if set.

    The payloads can be injected (that is how the tests pin behaviour against
    committed fixtures) or fetched through ``client``. Both routes produce the
    same object, so a bug cannot hide behind "the live data is different".
    """
    if client is None and entry is None:
        raise ValueError("reconstruct needs either a client or pre-fetched payloads")
    if entry is None:
        entry = client.entry(entry_id)          # type: ignore[union-attr]
    if history is None:
        history = client.history(entry_id) if client else EntryHistory((), (), ())
    if transfers is None:
        transfers = client.transfers(entry_id) if client else ()

    index = PlayerIndex.from_snapshot(snapshot, season)
    last_played = int(history.last_gw) if history.last_gw is not None else 0
    target_gw = int(gw) if gw is not None else last_played + 1

    unavailable: list[str] = [NO_PAST_SEASON_TRANSFERS]
    derived: list[str] = []
    checks: list[LedgerCheck] = []

    # -- the 15 --------------------------------------------------------------
    if picks is None and client is not None and last_played >= 1:
        picks = client.picks(entry_id, last_played)

    squad: tuple[Pick, ...] | None = None
    provenance = Provenance.NONE
    if picks is not None:
        squad = picks_from_public(picks, index)
        provenance = Provenance.PUBLIC_PICKS
    elif manual is not None:
        squad = manual.to_picks(index)
        provenance = Provenance.MANUAL
    else:
        unavailable.append(NO_PICKS_BEFORE_KICKOFF)

    # -- money ---------------------------------------------------------------
    if history.current:
        bank = history.current[-1].bank.tenths
        observed_value = history.current[-1].value
    elif entry.last_deadline_bank is not None:
        bank = entry.last_deadline_bank.tenths
        observed_value = entry.last_deadline_value
    elif manual is not None:
        bank = manual.bank_tenths
        observed_value = None
        derived.append("bank (from the squad you entered, budget minus what it cost)")
    else:
        bank = None
        observed_value = None

    # -- purchase prices -----------------------------------------------------
    bought_at: dict[int, int] = {}
    if squad is not None:
        if manual is not None and provenance is Provenance.MANUAL:
            bought_at = dict(manual.bought_at)
        else:
            bought_at = purchase_prices(
                [p.code for p in squad], transfers, index, up_to_gw=last_played
            )
            never_transferred = [
                c for c in bought_at
                if c not in {index.code(t.element_in) for t in transfers}
            ]
            if never_transferred:
                derived.append(
                    f"purchase price for {len(never_transferred)} player(s) held "
                    "since the squad was built (season-start price)"
                )

    if bank is None:
        budget = int(rules().get("squad.budget_tenths"))
        if squad is not None:
            # We know the squad but FPL has published no bank for it. The only
            # arithmetic that holds is the one that built it: budget minus what
            # was paid. Reporting the full budget here would claim £100.0m in
            # hand on top of a squad already bought with it.
            bank = budget - sum(bought_at.values())
            derived.append("bank (budget minus what the squad cost; FPL has "
                           "published no bank for this gameweek)")
        else:
            bank = budget
            derived.append("bank (no deadline has passed, so the full budget is unspent)")

    # -- free transfers ------------------------------------------------------
    ledger = derive_free_transfers(history, up_to_gw=last_played)
    free_transfers = int(ledger.entering.get(target_gw, 1))
    if last_played >= 1:
        derived.append("banked free transfers (accrual rule; checked against hits paid)")
        checks.extend(ledger.checks)
    else:
        derived.append(
            "free transfers (none banked: transfers before the first deadline are "
            "unlimited, so GW1 has no free-transfer count)"
        )

    # -- squad value cross-check --------------------------------------------
    state = MyTeamState(
        entry_id=int(entry_id),
        season=season,
        gw=GwId(target_gw),
        as_of=snapshot.as_of,
        picks=squad,
        bought_at=bought_at,
        bank_tenths=bank,
        free_transfers=free_transfers,
        chips_used=chips_used_from(history),
        provenance=provenance,
        team_name=entry.name,
        manager_name=entry.player_name,
        total_transfers_this_entry=int(entry.last_deadline_total_transfers),
        past_seasons=tuple(
            (p.season_name, p.total_points, p.rank) for p in history.past
        ),
        unavailable=tuple(unavailable),
        derived=tuple(dict.fromkeys(derived)),
        checks=tuple(checks),
    )

    if squad is not None and observed_value is not None:
        ours = state.team_value(index.price_now)
        checks.append(
            LedgerCheck(
                name="team value",
                derived=str(ours),
                observed=str(observed_value),
                ok=ours.tenths == observed_value.tenths,
                note=(
                    ""
                    if ours.tenths == observed_value.tenths
                    else "a purchase price is wrong, or prices moved since the "
                         "last deadline (FPL's value is as at that deadline)"
                ),
            )
        )
        state = replace(state, checks=tuple(checks))

    if validate and squad is not None:
        state.validate(index.price_now, index.team_code)
    return state
