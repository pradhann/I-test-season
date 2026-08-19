"""Reconstructing squad state from public data, and refusing to guess.

The reconstruction has three jobs and this file tests each one adversarially:

1. **Purchase prices.** The sell-on fee is measured against what you paid, not
   what the player is worth. Getting this wrong overstates the budget by half of
   every rise, which is invisible until the optimiser proposes a squad you
   cannot afford.
2. **Free transfers.** FPL publishes the banked count nowhere, so it is derived
   from the accrual rule -- and then checked against ``event_transfers_cost``,
   which *is* published. A wrong derivation predicts the wrong hit, so the check
   has teeth.
3. **Saying what it does not know.** The pre-GW1 squad is unobtainable, and the
   reconstruction has to return "unknown" rather than an empty squad, because an
   optimiser handed a zero-player squad will cheerfully recommend fifteen
   transfers.
"""

from __future__ import annotations

import dataclasses
import datetime as dt

import pandas as pd
import pytest

from fpl_edge.eval.replay import InvalidDecision
from fpl_edge.eval.scoring import Chip, Pick
from fpl_edge.myteam.sources import (
    ChipPlay,
    EntryHistory,
    EntrySummary,
    GwHistoryRow,
    GwPicks,
    PublicPick,
    TransferRow,
)
from fpl_edge.myteam.state import (
    NO_PICKS_BEFORE_KICKOFF,
    PlayerIndex,
    Provenance,
    ReconstructionError,
    derive_free_transfers,
    purchase_prices,
    reconstruct,
)
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, Money, Position, selling_price

UTC = dt.timezone.utc
SEASON = "2026-27"
T0 = dt.datetime(2026, 8, 1, 12, tzinfo=UTC)
NOW = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)

#: A universe big enough to build several legal squads from: 6 GKP, 12 DEF,
#: 12 MID, 9 FWD across 10 clubs, so the three-per-club limit is reachable.
_LAYOUT = [(Position.GKP, 6), (Position.DEF, 12), (Position.MID, 12), (Position.FWD, 9)]


def _universe() -> tuple[pd.DataFrame, pd.DataFrame]:
    players, states = [], []
    code = 1000
    for pos, n in _LAYOUT:
        for i in range(n):
            code += 1
            price = 40 + (i % 6) * 5 + int(pos) * 5
            players.append({
                "season": SEASON, "code": code, "element_id": code - 1000,
                "web_name": f"{pos.name}{i}", "first_name": "F",
                "second_name": f"{pos.name}{i}", "position": int(pos),
                "team_code": 1 + (i % 10), "as_of": T0,
            })
            states.append({
                "season": SEASON, "code": code, "element_id": code - 1000,
                "price_tenths": price, "selected_by_pct": 5.0, "status": "a",
                "chance_of_playing_next_round": None, "news": "", "news_added": None,
                "transfers_in_event": 0, "transfers_out_event": 0,
                "cost_change_start": 0, "as_of": T0,
            })
    return pd.DataFrame(players), pd.DataFrame(states)


@pytest.fixture()
def warehouse(tmp_path) -> Warehouse:
    wh = Warehouse(tmp_path / "t.duckdb")
    players, states = _universe()
    wh.append("dim_player", players)
    wh.append("fact_player_state", states)
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": gw,
         "deadline_utc": dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
                         + dt.timedelta(days=7 * (gw - 1)),
         "is_finished": False, "as_of": T0}
        for gw in range(1, 11)
    ]))
    return wh


@pytest.fixture()
def index(warehouse) -> PlayerIndex:
    return PlayerIndex.from_snapshot(warehouse.snapshot_at(NOW), SEASON)


def _legal_squad(index: PlayerIndex) -> list[int]:
    """A cheapest-first legal 15: 2/5/5/3, at most three per club, under £100m."""
    chosen: list[int] = []
    per_club: dict[int, int] = {}
    for pos, want in ((Position.GKP, 2), (Position.DEF, 5), (Position.MID, 5), (Position.FWD, 3)):
        pool = sorted(
            (c for c, p in index.position.items() if p is pos),
            key=lambda c: (index.price_now[c], c),
        )
        taken = 0
        for code in pool:
            club = index.team_code[code]
            if per_club.get(club, 0) >= 3:
                continue
            chosen.append(code)
            per_club[club] = per_club.get(club, 0) + 1
            taken += 1
            if taken == want:
                break
        assert taken == want, f"could not fill {pos}"
    return chosen


def _picks(codes: list[int], index: PlayerIndex) -> tuple[Pick, ...]:
    """Order the 15 into a legal 3-5-2 with a captain and vice."""
    by_pos = {p: [c for c in codes if index.position[c] is p] for p in Position}
    xi = by_pos[Position.GKP][:1] + by_pos[Position.DEF][:3] \
        + by_pos[Position.MID][:5] + by_pos[Position.FWD][:2]
    bench = [c for c in codes if c not in xi]
    bench.sort(key=lambda c: index.position[c] is not Position.GKP)
    ordered = xi + bench
    return tuple(
        Pick(code=c, position=index.position[c], order=i + 1,
             is_captain=(i == 1), is_vice=(i == 2))
        for i, c in enumerate(ordered)
    )


def _entry(**over) -> EntrySummary:
    base = dict(
        entry_id=4490171, name="i-test", player_name="Nripesh Pradhan",
        started_event=1, current_event=None, entered_events=(),
        last_deadline_bank=None, last_deadline_value=None,
        last_deadline_total_transfers=0, years_active=10, favourite_team=16,
        summary_overall_points=None, summary_overall_rank=None,
    )
    return EntrySummary(**{**base, **over})


def _row(gw: int, *, transfers: int = 0, cost: int = 0, bank: int = 5,
         value: int = 1000) -> GwHistoryRow:
    return GwHistoryRow(
        gw=GwId(gw), points=50, total_points=50 * gw, rank=1, overall_rank=1,
        bank=Money(bank), value=Money(value), event_transfers=transfers,
        event_transfers_cost=cost, points_on_bench=3,
    )


# -- the pre-GW1 hole --------------------------------------------------------


def test_before_the_season_the_squad_is_unknown_not_empty(warehouse) -> None:
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON,
        entry=_entry(), history=EntryHistory((), (), ()), transfers=(),
    )
    assert state.picks is None
    assert state.known is False
    assert state.provenance is Provenance.NONE
    assert any(NO_PICKS_BEFORE_KICKOFF in u for u in state.unavailable)


def test_unknown_squad_refuses_to_become_a_squad_state(warehouse) -> None:
    """An empty SquadState would let an optimiser recommend fifteen transfers."""
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON,
        entry=_entry(), history=EntryHistory((), (), ()), transfers=(),
    )
    with pytest.raises(ReconstructionError, match="not known"):
        state.to_squad_state()


def test_full_budget_is_the_bank_before_any_deadline(warehouse) -> None:
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON,
        entry=_entry(), history=EntryHistory((), (), ()), transfers=(),
    )
    assert state.bank == Money(1000)
    assert any("full budget is unspent" in d for d in state.derived)


def test_past_season_transfer_counts_are_declared_unavailable(warehouse) -> None:
    """FPL issues a new entry id each season, so old transfers are unreachable."""
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON,
        entry=_entry(), history=EntryHistory((), (), ()), transfers=(),
    )
    assert any("new entry id each season" in u for u in state.unavailable)


# -- purchase prices ---------------------------------------------------------


def test_never_transferred_players_are_priced_at_the_season_start(index) -> None:
    codes = _legal_squad(index)[:3]
    paid = purchase_prices(codes, (), index, up_to_gw=5)
    assert paid == {c: index.start_price[c] for c in codes}


def test_a_transfer_in_sets_the_purchase_price(index) -> None:
    codes = _legal_squad(index)
    bought = codes[0]
    rows = (
        TransferRow(gw=GwId(3), element_in=index.element_by_code[bought],
                    element_in_cost=Money(77), element_out=index.element_by_code[codes[1]],
                    element_out_cost=Money(50), made_utc=None),
    )
    paid = purchase_prices([bought], rows, index, up_to_gw=5)
    assert paid[bought] == 77, "the price paid, not today's price"


def test_rebuying_a_player_takes_the_latest_price(index) -> None:
    """Bought at 7.0, sold, bought back at 8.2 -- the fee is measured off 8.2."""
    codes = _legal_squad(index)
    hero, filler = codes[0], codes[1]
    e = index.element_by_code
    rows = (
        TransferRow(GwId(2), e[hero], Money(70), e[filler], Money(50), None),
        TransferRow(GwId(4), e[filler], Money(52), e[hero], Money(74), None),
        TransferRow(GwId(6), e[hero], Money(82), e[filler], Money(52), None),
    )
    assert purchase_prices([hero], rows, index, up_to_gw=8)[hero] == 82


def test_purchase_prices_respect_the_as_of_gameweek(index) -> None:
    """Asking about GW3 must not see a GW6 transfer."""
    codes = _legal_squad(index)
    hero, filler = codes[0], codes[1]
    e = index.element_by_code
    rows = (
        TransferRow(GwId(2), e[hero], Money(70), e[filler], Money(50), None),
        TransferRow(GwId(6), e[hero], Money(82), e[filler], Money(52), None),
    )
    assert purchase_prices([hero], rows, index, up_to_gw=3)[hero] == 70


def test_squad_value_applies_the_sell_on_fee_to_each_rise(warehouse, index) -> None:
    """The whole reason purchase price is tracked: 7.5 bought, 7.8 now, 7.6 out.

    Using today's price as the sale value would report 7.8 and hand the
    optimiser a tenth it does not have -- once per risen player.
    """
    codes = _legal_squad(index)
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((), (), ()), transfers=(),
        picks=_public(_picks(codes, index), index), validate=False,
    )
    hero = codes[0]
    bought = dict(state.bought_at) | {hero: 75}
    price_now = dict(index.price_now) | {hero: 78}
    risen = dataclasses.replace(state, bought_at=bought)

    rest = sum(
        selling_price(Money(bought[c]), Money(price_now[c])).tenths
        for c in codes if c != hero
    )
    assert risen.squad_value(price_now).tenths == rest + 76
    # And the naive version is wrong by exactly the two tenths kept by the game.
    assert sum(price_now[c] for c in codes) == rest + 78 + (
        sum(price_now[c] for c in codes if c != hero) - rest
    )


# -- free transfers ----------------------------------------------------------


def test_no_banked_free_transfers_entering_gw1() -> None:
    """Not 'none available' -- transfers before the first deadline are unlimited."""
    ledger = derive_free_transfers(EntryHistory((), (), ()), up_to_gw=0)
    assert ledger.entering[1] == 0


def test_one_free_transfer_entering_gw2() -> None:
    ledger = derive_free_transfers(EntryHistory((_row(1, transfers=15),), (), ()), up_to_gw=1)
    assert ledger.entering[2] == 1
    assert ledger.consistent, "GW1 transfers are free and must predict no hit"


def test_free_transfers_bank_up_to_the_cap() -> None:
    rows = tuple(_row(gw) for gw in range(1, 10))
    ledger = derive_free_transfers(EntryHistory(rows, (), ()), up_to_gw=9)
    # 1 after GW1, then +1 a week, capped at 5.
    assert [ledger.entering[gw] for gw in range(2, 9)] == [1, 2, 3, 4, 5, 5, 5]


def test_a_hit_is_predicted_and_matches_what_was_paid() -> None:
    """Three transfers on one free one costs -8, and we must predict exactly that."""
    rows = (_row(1), _row(2, transfers=3, cost=8))
    ledger = derive_free_transfers(EntryHistory(rows, (), ()), up_to_gw=2)
    assert ledger.consistent
    assert ledger.entering[3] == 1


def test_a_wrong_free_transfer_count_is_caught_by_the_observed_hit() -> None:
    """The check has teeth: an impossible hit is reported, not absorbed."""
    rows = (_row(1), _row(2, transfers=1, cost=4))  # 1 transfer on 1 free is not a hit
    ledger = derive_free_transfers(EntryHistory(rows, (), ()), up_to_gw=2)
    assert not ledger.consistent
    bad = [c for c in ledger.checks if not c.ok]
    assert bad and "GW2 hit" == bad[0].name


def test_wildcard_retains_banked_transfers() -> None:
    rows = (_row(1), _row(2), _row(3), _row(4, transfers=6))
    chips = (ChipPlay("wildcard", GwId(4), None),)
    ledger = derive_free_transfers(EntryHistory(rows, (), chips), up_to_gw=4)
    assert ledger.consistent, "a wildcard week charges no hit"
    assert ledger.entering[4] == 3
    assert ledger.entering[5] == 3, "saved transfers are retained through the chip"


# -- chips -------------------------------------------------------------------


def test_chips_remaining_are_reported_per_half(warehouse) -> None:
    chips = (ChipPlay("wildcard", GwId(8), None), ChipPlay("bboost", GwId(25), None))
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory(tuple(_row(gw) for gw in range(1, 26)), (), chips),
        transfers=(),
    )
    status = {c.chip: c for c in state.chip_status()}
    assert status["wildcard"].remaining_in(0) == 0, "first-half wildcard is gone"
    assert status["wildcard"].remaining_in(1) == 1
    assert status["bboost"].remaining_in(0) == 1
    assert status["bboost"].remaining_in(1) == 0
    assert status["3xc"].remaining == 2


def test_wildcard_is_not_available_in_gw1(warehouse) -> None:
    """The registry is explicit and this is the rule people get wrong."""
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((), (), ()), transfers=(),
    )
    status = {c.chip: c for c in state.chip_status()}
    assert status["wildcard"].available_in_gw(1) is False
    assert status["freehit"].available_in_gw(1) is False
    assert status["bboost"].available_in_gw(1) is True
    assert status["3xc"].available_in_gw(1) is True


def test_an_unknown_chip_is_a_stale_registry_not_a_shrug(warehouse) -> None:
    with pytest.raises(ReconstructionError, match="unknown chip"):
        reconstruct(
            warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
            history=EntryHistory((), (), (ChipPlay("assistant_manager", GwId(5), None),)),
            transfers=(),
        )


# -- validation through the real rules ---------------------------------------


def _public(picks: tuple[Pick, ...], index: PlayerIndex) -> GwPicks:
    return GwPicks(
        gw=GwId(1), active_chip=None,
        picks=tuple(
            PublicPick(element=index.element_by_code[p.code], position=p.order,
                       multiplier=2 if p.is_captain else (1 if p.is_starter else 0),
                       is_captain=p.is_captain, is_vice_captain=p.is_vice)
            for p in picks
        ),
        bank=None, value=None, event_transfers=0, event_transfers_cost=0,
    )


def test_a_legal_squad_survives_apply_decision(warehouse, index) -> None:
    codes = _legal_squad(index)
    picks = _picks(codes, index)
    spend = sum(index.price_now[c] for c in codes)
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((), (), ()), transfers=(),
        picks=_public(picks, index),
    )
    assert state.provenance is Provenance.PUBLIC_PICKS
    assert len(state.picks) == 15
    # reconstruct() validated it; do it again explicitly so the assertion is here.
    checked = state.validate(index.price_now, index.team_code)
    assert checked.bank_tenths == 1000 - spend


def test_four_players_from_one_club_is_rejected(warehouse, index) -> None:
    """A reconstruction that would be illegal is a bug in the reconstruction.

    Built by swapping members of a legal 15 for same-position players from a
    club that already has three, so the *only* rule broken is the club limit.
    """
    codes = _legal_squad(index)
    counts: dict[int, int] = {}
    for c in codes:
        counts[index.team_code[c]] = counts.get(index.team_code[c], 0) + 1
    crowded = max(counts, key=lambda club: counts[club])
    assert counts[crowded] == 3, "the legal squad should sit on the limit somewhere"

    victim, replacement = next(
        (owned, spare)
        for owned in codes
        if index.team_code[owned] != crowded
        for spare in sorted(index.position)
        if spare not in codes
        and index.position[spare] is index.position[owned]
        and index.team_code[spare] == crowded
    )
    illegal = [replacement if c == victim else c for c in codes]

    with pytest.raises(InvalidDecision, match="more than 3 players from club"):
        reconstruct(
            warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
            history=EntryHistory((), (), ()), transfers=(),
            picks=_public(_picks(illegal, index), index),
        )


def test_team_value_is_cross_checked_against_fpls_own(warehouse, index) -> None:
    codes = _legal_squad(index)
    picks = _picks(codes, index)
    truth = sum(index.price_now[c] for c in codes) + 5
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((_row(1, bank=5, value=truth),), (), ()), transfers=(),
        picks=_public(picks, index), validate=False,
    )
    check = next(c for c in state.checks if c.name == "team value")
    assert check.ok, check.render()


def test_a_wrong_purchase_price_shows_up_as_a_value_mismatch(warehouse, index) -> None:
    codes = _legal_squad(index)
    picks = _picks(codes, index)
    state = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((_row(1, bank=5, value=9999),), (), ()), transfers=(),
        picks=_public(picks, index), validate=False,
    )
    assert state.failures
    assert state.failures[0].name == "team value"


# -- identity ----------------------------------------------------------------


def test_element_id_is_translated_to_a_stable_code(index) -> None:
    code = next(iter(index.price_now))
    assert index.code(index.element_by_code[code]) == code


def test_an_unknown_element_id_is_a_stale_warehouse_not_a_new_player(index) -> None:
    with pytest.raises(ReconstructionError, match="per-season and reassigned"):
        index.code(999_999)
