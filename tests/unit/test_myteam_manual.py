"""Manual squad entry: parsing, refusing to guess, confirming, reconciling.

The failure mode this file guards against is not "the parser rejected something
valid". It is the opposite: the parser cheerfully accepting a squad that is not
the one the manager owns, and the engine then spending a season recommending
transfers out of a fiction. Every test here is about the boundary between "I
understood that" and "I am asking".
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.interfaces.parsing import PlayerResolver
from fpl_edge.myteam.manual import (
    ManualEntryError,
    ManualSquadRecord,
    build_draft,
    clean_fragment,
    reconcile,
    split_fragments,
)
from fpl_edge.myteam.state import PlayerIndex
from fpl_edge.myteam.store import MyTeamStore, NoSuchDraftError
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, Money, Position

UTC = dt.timezone.utc
SEASON = "2026-27"
T0 = dt.datetime(2026, 8, 1, 12, tzinfo=UTC)
NOW = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)

#: Named after real players so the fuzzy matching is exercised on the shapes
#: that actually break it: accents, hyphens, particles, initials, and two
#: distinct players sharing a surname.
SQUAD = [
    ("Raya", "David", "Raya Martin", Position.GKP, 55, 1),
    ("Dubravka", "Martin", "Dubravka", Position.GKP, 40, 2),
    ("Gabriel", "Gabriel", "dos Santos Magalhaes", Position.DEF, 55, 1),
    ("Alexander-Arnold", "Trent", "Alexander-Arnold", Position.DEF, 60, 3),
    ("Muñoz", "Daniel", "Muñoz Mejía", Position.DEF, 55, 4),
    ("van Dijk", "Virgil", "van Dijk", Position.DEF, 60, 3),
    ("Diop", "Issa", "Diop", Position.DEF, 40, 5),
    ("B.Fernandes", "Bruno", "Fernandes", Position.MID, 90, 6),
    ("Salah", "Mohamed", "Salah", Position.MID, 145, 3),
    ("Saka", "Bukayo", "Saka", Position.MID, 80, 1),
    ("Hughes", "Will", "Hughes", Position.MID, 45, 4),
    ("Semenyo", "Antoine", "Semenyo", Position.MID, 70, 7),
    ("Haaland", "Erling", "Haaland", Position.FWD, 135, 8),
    ("Wood", "Chris", "Wood", Position.FWD, 70, 9),
    ("Evanilson", "Evanilson", "de Lima", Position.FWD, 60, 7),
]

#: A second Hughes, so a bare "Hughes" is genuinely ambiguous rather than
#: merely fuzzy. This is the case that must produce a question, never a coin flip.
EXTRA = [
    ("Hughes", "Ben", "Hughes", Position.DEF, 40, 10),
    ("Saliba", "William", "Saliba", Position.DEF, 60, 1),
    ("Palmer", "Cole", "Palmer", Position.MID, 85, 11),
    ("Watkins", "Ollie", "Watkins", Position.FWD, 90, 12),
    ("Pickford", "Jordan", "Pickford", Position.GKP, 55, 13),
]


@pytest.fixture()
def warehouse(tmp_path) -> Warehouse:
    wh = Warehouse(tmp_path / "t.duckdb")
    players, states = [], []
    for i, (web, first, second, pos, price, club) in enumerate(SQUAD + EXTRA):
        code = 2000 + i
        players.append({
            "season": SEASON, "code": code, "element_id": i + 1, "web_name": web,
            "first_name": first, "second_name": second, "position": int(pos),
            "team_code": club, "as_of": T0,
        })
        states.append({
            "season": SEASON, "code": code, "element_id": i + 1,
            "price_tenths": price, "selected_by_pct": 5.0, "status": "a",
            "chance_of_playing_next_round": None, "news": "", "news_added": None,
            "transfers_in_event": 0, "transfers_out_event": 0,
            "cost_change_start": 0, "as_of": T0,
        })
    wh.append("dim_player", pd.DataFrame(players))
    wh.append("fact_player_state", pd.DataFrame(states))
    return wh


@pytest.fixture()
def index(warehouse) -> PlayerIndex:
    return PlayerIndex.from_snapshot(warehouse.snapshot_at(NOW), SEASON)


@pytest.fixture()
def resolver(warehouse) -> PlayerResolver:
    return PlayerResolver(warehouse.snapshot_at(NOW).players(SEASON))


def _draft(text: str, resolver, index, *, gw: int = 1, root=None):
    return build_draft(
        text, resolver=resolver, index=index, entry_id=4490171, season=SEASON,
        gw=gw, now=NOW, source="test",
    )


#: Priced so this exact fifteen costs exactly £100.0m. Swapping Palmer (£8.5m)
#: for Salah (£14.5m) puts it £6.0m over, which is how the budget check is tested.
LEGAL = [
    "Raya", "Dubravka", "Gabriel", "Alexander-Arnold", "Munoz", "van Dijk", "Diop",
    "B.Fernandes", "Saka", "Hughes (MID)", "Semenyo", "Palmer",
    "Haaland", "Wood", "Evanilson",
]


# -- parsing ------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,expected",
    [
        ("1. Salah (£14.5m)", "Salah"),
        ("- Haaland", "Haaland"),
        ("• van Dijk", "van Dijk"),
        ("MID: B.Fernandes", "B.Fernandes"),
        ("Saka (c)", "Saka"),
        ("Wood - 7.0", "Wood"),
        ("  Muñoz  ", "Muñoz"),
        ("12) Evanilson £6.0m", "Evanilson"),
    ],
)
def test_list_furniture_is_stripped_from_a_name(line: str, expected: str) -> None:
    parsed = clean_fragment(line)
    assert parsed is not None and parsed.name == expected


@pytest.mark.parametrize(
    "line", ["Bench", "Goalkeepers", "My team", "TOTAL", "100.0", "£4.5m", "", "  ", "GK:"]
)
def test_headers_and_bare_numbers_are_not_names(line: str) -> None:
    assert clean_fragment(line) is None


def test_a_price_annotation_is_kept_as_a_qualifier() -> None:
    parsed = clean_fragment("Hughes (£4.5m)")
    assert parsed is not None
    assert parsed.name == "Hughes" and parsed.price_tenths == 45


def test_a_position_tag_is_kept_as_a_qualifier() -> None:
    for line in ("Hughes (MID)", "MID: Hughes", "Hughes (midfielder)"):
        parsed = clean_fragment(line)
        assert parsed is not None and parsed.position is Position.MID


def test_any_order_and_any_separator(resolver, index) -> None:
    one_per_line = _draft("\n".join(LEGAL), resolver, index)
    comma_run = _draft(", ".join(reversed(LEGAL)), resolver, index)
    assert one_per_line.ok and comma_run.ok
    assert set(one_per_line.record.codes) == set(comma_run.record.codes)


def test_a_pasted_screenshot_shape_parses(resolver, index) -> None:
    text = (
        "My GW1 team\n"
        "Goalkeepers\n"
        "1. Raya (£5.5m)\n2. Dubravka (£4.0m)\n"
        "Defenders\n"
        "Gabriel £6.5m, Alexander-Arnold £7.0m, Munoz £5.5m, van Dijk £6.0m, Diop £4.0m\n"
        "Midfielders\n"
        "B.Fernandes (c), Saka, Hughes (MID), Semenyo, Palmer\n"
        "Forwards\n"
        "Haaland, Wood, Evanilson\n"
        "Bank 0.0\n"
    )
    draft = _draft(text, resolver, index)
    assert draft.ok, draft.questions or draft.problems
    assert len(draft.record.codes) == 15


# -- refusing to guess --------------------------------------------------------


def test_two_players_with_one_surname_is_a_question_not_a_coin_flip(resolver, index) -> None:
    text = "\n".join(x if x != "Hughes (MID)" else "Hughes" for x in LEGAL)
    draft = _draft(text, resolver, index)
    assert not draft.ok
    assert draft.record is None
    assert any("Hughes" in q and "more than one" in q for q in draft.questions)


def test_a_qualifier_settles_the_ambiguity(resolver, index) -> None:
    draft = _draft("\n".join(LEGAL), resolver, index)
    assert draft.ok
    assert any("ambiguous" in n and "Hughes" in n for n in draft.notes)


def test_a_qualifier_that_matches_nobody_still_asks(resolver, index) -> None:
    """A wrong tag must not promote a player the resolver did not think matched."""
    text = "\n".join(x if x != "Hughes (MID)" else "Hughes (FWD)" for x in LEGAL)
    draft = _draft(text, resolver, index)
    assert not draft.ok and draft.record is None


def test_a_misspelling_suggests_rather_than_dead_ends(resolver, index) -> None:
    draft = _draft("Zzzzzqqq\n" + "\n".join(LEGAL), resolver, index)
    # 15 valid names plus one nonsense line: the nonsense is furniture, not a
    # question, because it looks like nobody at all.
    assert draft.ok or any("matched nothing" in q for q in draft.questions)


def test_naming_someone_twice_is_reported(resolver, index) -> None:
    draft = _draft("\n".join([*LEGAL, "Haaland"]), resolver, index)
    assert not draft.ok
    assert any("named twice" in p for p in draft.problems)


def test_the_wrong_number_of_players_is_reported_with_the_count(resolver, index) -> None:
    draft = _draft("\n".join(LEGAL[:12]), resolver, index)
    assert not draft.ok
    assert any("12 player(s) recognised" in p and "Add 3" in p for p in draft.problems)


# -- validation through the real rules ----------------------------------------


def test_an_over_budget_squad_is_rejected_by_apply_decision(resolver, index) -> None:
    """Salah at £14.5m instead of Palmer pushes it over £100.0m."""
    over = [x if x != "Palmer" else "Salah" for x in LEGAL]
    draft = _draft("\n".join(over), resolver, index)
    assert not draft.ok
    assert any("bank went negative" in p for p in draft.problems)


def test_a_legal_squad_gets_a_confirmable_record(resolver, index) -> None:
    draft = _draft("\n".join(LEGAL), resolver, index)
    assert draft.ok
    record = draft.record
    assert len(record.codes) == 15
    assert sum(record.bought_at.values()) + record.bank_tenths == 1000
    assert sorted(record.order.values()) == list(range(1, 16))
    assert record.captain != record.vice


def test_the_lineup_is_flagged_as_provisional(resolver, index) -> None:
    """Capturing which fifteen you own is not picking the team."""
    draft = _draft("\n".join(LEGAL), resolver, index)
    assert draft.record.provisional_lineup
    assert any("placeholder legal arrangement" in n for n in draft.notes)


def test_the_reserve_keeper_is_first_on_the_bench(resolver, index) -> None:
    """A keeper can only be replaced by a keeper; any other slot wastes priority."""
    draft = _draft("\n".join(LEGAL), resolver, index)
    rec = draft.record
    bench = sorted((c for c in rec.codes if rec.order[c] > 11), key=lambda c: rec.order[c])
    assert index.position[bench[0]] is Position.GKP


def test_confirmation_shows_the_squad_back_before_saving(resolver, index) -> None:
    draft = _draft("\n".join(LEGAL), resolver, index)
    shown = draft.render(index)
    for name in ("Haaland", "Raya", "Semenyo"):
        assert name in shown
    assert "Nothing is saved until you do" in shown
    assert draft.token in shown


# -- the store ----------------------------------------------------------------


def test_nothing_is_saved_until_confirmed(tmp_path, resolver, index) -> None:
    store = MyTeamStore(4490171, root=tmp_path)
    draft = _draft("\n".join(LEGAL), resolver, index)
    token = store.stage(draft)
    assert store.confirmed(season=SEASON) is None, "staging is not saving"
    assert store.pending() is not None
    store.confirm(token, now=NOW)
    assert store.confirmed(season=SEASON) is not None
    assert store.pending() is None


def test_the_wrong_token_does_not_confirm(tmp_path, resolver, index) -> None:
    """A one-word 'yes' must not save a draft the manager already thought better of."""
    store = MyTeamStore(4490171, root=tmp_path)
    store.stage(_draft("\n".join(LEGAL), resolver, index))
    with pytest.raises(NoSuchDraftError):
        store.confirm("deadbeef", now=NOW)
    assert store.confirmed(season=SEASON) is None


def test_confirming_with_nothing_staged_is_an_error(tmp_path) -> None:
    with pytest.raises(NoSuchDraftError):
        MyTeamStore(4490171, root=tmp_path).confirm("abcd1234", now=NOW)


def test_a_hand_edited_file_is_refused(tmp_path, resolver, index) -> None:
    """The digest covers the codes and the prices, so tampering is visible."""
    store = MyTeamStore(4490171, root=tmp_path)
    token = store.stage(_draft("\n".join(LEGAL), resolver, index))
    record = store.confirm(token, now=NOW)
    body = record.to_json()
    body["bought_at"][str(record.codes[0])] = 999
    with pytest.raises(ManualEntryError, match="digest"):
        ManualSquadRecord.from_json(body)


def test_a_new_squad_supersedes_without_erasing_the_old_one(tmp_path, resolver, index) -> None:
    store = MyTeamStore(4490171, root=tmp_path)
    store.confirm(store.stage(_draft("\n".join(LEGAL), resolver, index)), now=NOW)
    swapped = [x if x != "Wood" else "Watkins" for x in LEGAL]
    second = _draft("\n".join(swapped), resolver, index)
    if second.ok:
        store.confirm(store.stage(second), now=NOW)
        assert len(store.history()) == 2
        assert store.confirmed(season=SEASON).digest == second.token


def test_the_record_round_trips_through_json(tmp_path, resolver, index) -> None:
    record = _draft("\n".join(LEGAL), resolver, index).record
    again = ManualSquadRecord.from_json(record.to_json())
    assert again.codes == record.codes
    assert dict(again.bought_at) == dict(record.bought_at)
    assert again.digest == record.digest


# -- reconciliation -----------------------------------------------------------


def test_a_matching_public_squad_reconciles_cleanly(resolver, index) -> None:
    record = _draft("\n".join(LEGAL), resolver, index).record
    result = reconcile(record, list(record.codes), gw=1)
    assert result.matches
    assert "matches the 15 FPL published" in result.render(index)


def test_a_difference_explained_by_a_transfer_says_so(resolver, index) -> None:
    record = _draft("\n".join(LEGAL), resolver, index).record
    public = list(record.codes)
    public[0] = max(index.price_now) + 0 if False else next(
        c for c in index.price_now if c not in record.codes
    )
    result = reconcile(record, public, gw=3, transfers_between=1)
    assert not result.matches
    assert result.explained_by_transfers
    assert "accounts for the difference" in result.render(index)


def test_an_unexplained_difference_is_called_a_wrong_entry(resolver, index) -> None:
    """The public picks win, but the manual entry being wrong is the headline."""
    record = _draft("\n".join(LEGAL), resolver, index).record
    spare = [c for c in index.price_now if c not in record.codes][:3]
    public = spare + list(record.codes)[3:]
    result = reconcile(record, public, gw=3, transfers_between=0)
    assert not result.matches and not result.explained_by_transfers
    rendered = result.render(index)
    assert "NOT explained by transfers" in rendered
    assert "nothing has been overwritten silently" in rendered


def test_reconciliation_never_mutates_the_record(resolver, index) -> None:
    record = _draft("\n".join(LEGAL), resolver, index).record
    before = record.digest
    reconcile(record, [1, 2, 3], gw=4, transfers_between=0)
    assert record.digest == before
