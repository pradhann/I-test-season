"""The Understat ingest: parser pinned to reality, resolver that refuses.

The parser tests run against ``tests/fixtures/understat/`` -- REAL bodies
fetched by hand from understat.com on 2026-08-31 (Erling Haaland, id 8260) and
trimmed to two seasons, rows verbatim. That fetch was the one development
fetch; nothing in this file, or in CI, touches the network -- the unit
conftest's ``FPL_EDGE_DISABLE_NETWORK_INGEST=1`` guard is itself under test
here, so an accidentally-live fetch path fails loudly instead of crawling.

The resolver tests are the religion tests: exact, then containment, then
REFUSAL. The counter-example test encodes why there is no edit-distance tier
-- this repo's own club resolver documents "forester" -> Brentford (d=6) and
"hull" tied between Fulham and Hull City (d=4), and the analyzer's misfixes
("Louie Barry" -> Thierno Barry) are the player-shaped versions of the same
fabrication. A refusal costs a listing; a guess writes a wrong player's
profile under a real player's code.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.understat import (
    ConflictingUnderstatError,
    UnderstatError,
    UnderstatStore,
    UnresolvedPlayerError,
    fetch_player_profile,
    parse_player_matches,
    parse_search_players,
    resolve_understat_player,
    understat_season,
)
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
FIX = Path(__file__).parents[1] / "fixtures" / "understat"
AS_OF = dt.datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
CODE = 223094  # Haaland's FPL code, used as an opaque int here
SEASON = "2026-27"


@pytest.fixture(scope="module")
def playerdata() -> dict:
    return json.loads((FIX / "player_8260_playerdata.json").read_text())


# ---------------------------------------------------------------------------
# season mapping
# ---------------------------------------------------------------------------


def test_our_season_label_maps_to_understats_starting_year():
    assert understat_season("2026-27") == "2026"
    assert understat_season("2019-20") == "2019"


def test_a_malformed_season_is_refused_not_guessed():
    with pytest.raises(ValueError, match="refusing to guess"):
        understat_season("2026")


# ---------------------------------------------------------------------------
# parser, pinned against the real fixture
# ---------------------------------------------------------------------------


def test_parser_extracts_exactly_the_current_season_from_a_full_career(playerdata):
    """The fixture carries 37 matches across two Understat seasons; our
    2026-27 must select only their '2026' rows -- two, at fetch time."""
    df = parse_player_matches(playerdata, code=CODE, season=SEASON, as_of=AS_OF)
    assert len(df) == 2
    assert set(df["season"]) == {SEASON}
    # the previous understat season must NOT leak in
    df_prev = parse_player_matches(playerdata, code=CODE, season="2025-26", as_of=AS_OF)
    assert len(df_prev) == 35


def test_parser_coerces_understats_stringly_numbers(playerdata):
    """Every numeric field arrives as a string ('xG': '0.686...'); the row
    written must be typed, or DuckDB stores text and every SUM lies."""
    df = parse_player_matches(playerdata, code=CODE, season=SEASON, as_of=AS_OF)
    row = df[df["match_id"] == 31190].iloc[0]  # Crystal Palace 1-4 MCI, 2026-08-28
    assert row["shots"] == 5 and isinstance(int(row["shots"]), int)
    assert row["goals"] == 2
    assert row["minutes"] == 90
    assert row["xg"] == pytest.approx(0.6867040395736694)
    assert row["npxg"] == pytest.approx(0.6867040395736694)
    assert row["xa"] == 0.0
    assert row["date"] == dt.date(2026, 8, 28)
    assert row["h_team"] == "Crystal Palace" and row["a_team"] == "Manchester City"


def test_parser_refuses_a_payload_whose_shape_has_changed():
    with pytest.raises(UnderstatError, match="player.id"):
        parse_player_matches({"matches": []}, code=CODE, season=SEASON, as_of=AS_OF)
    with pytest.raises(UnderstatError, match="matches"):
        parse_player_matches({"player": {"id": "8260"}}, code=CODE,
                             season=SEASON, as_of=AS_OF)


def test_parser_requires_a_utc_as_of(playerdata):
    with pytest.raises(ValueError, match="timezone-aware"):
        parse_player_matches(playerdata, code=CODE, season=SEASON,
                             as_of=dt.datetime(2026, 8, 31, 12, 0))  # noqa: DTZ001 - naive on purpose


def test_search_parser_reads_the_real_response_shape():
    payload = json.loads((FIX / "search_haaland.json").read_text())
    assert parse_search_players(payload) == [
        {"id": "8260", "player": "Erling Haaland", "team": "Manchester City"}
    ]
    assert parse_search_players({"response": {"success": False}}) == []


# ---------------------------------------------------------------------------
# the strict resolver: exact, containment, refusal -- NEVER edit distance
# ---------------------------------------------------------------------------

def _cand(cid: str, name: str, team: str = "Somewhere FC") -> dict[str, str]:
    return {"id": cid, "player": name, "team": team}


def test_exact_full_name_resolves():
    r = resolve_understat_player(
        [_cand("8260", "Erling Haaland", "Manchester City")],
        web_name="Haaland", first_name="Erling", second_name="Haaland")
    assert (r.understat_id, r.basis) == (8260, "exact")


def test_containment_keeps_the_real_same_person_cases():
    """'Ezri Konsa' IS 'Ezri Konsa Ngoyo' -- one name written inside the
    other, no letter forgiven anywhere."""
    r = resolve_understat_player(
        [_cand("1", "Ezri Konsa Ngoyo")],
        web_name="Konsa", first_name="Ezri", second_name="Konsa")
    assert (r.understat_id, r.basis) == (1, "containment")


def test_accent_folding_is_normalisation_not_edit_distance():
    """The stroke letter is the documented real bug (names.py): folding
    'Ødegaard' to 'odegaard' is the repo's ONE matcher at work, not a typo
    being forgiven."""
    r = resolve_understat_player(
        [_cand("5", "Martin Ødegaard", "Arsenal")],
        web_name="Ødegaard", first_name="Martin", second_name="Ødegaard")
    assert r.understat_id == 5


def test_edit_distance_counterexample_is_refused():
    """One letter apart, same surname, and STILL refused: 'Louie Barry' must
    never resolve to Thierno Barry (the analyzer's real misattribution), and
    'Cristian' must not have its missing 'h' forgiven into 'Cristhian'. Any
    edit-distance tier -- even d=1 -- accepts the second; the whole point of
    containment-only is that it cannot."""
    with pytest.raises(UnresolvedPlayerError, match="Thierno Barry"):
        resolve_understat_player(
            [_cand("2", "Thierno Barry", "Villa")],
            web_name="L.Barry", first_name="Louie", second_name="Barry")
    with pytest.raises(UnresolvedPlayerError, match="Cristhian Mosquera"):
        resolve_understat_player(
            [_cand("3", "Cristhian Mosquera", "Arsenal")],
            web_name="Mosquera", first_name="Cristian", second_name="Mosquera")


def test_barry_vs_barry_note():
    """...but the SAME surname alone does resolve when it is our whole query
    and only one candidate contains it -- containment, not surname matching,
    is what fires. Two Barrys would refuse (next test)."""
    r = resolve_understat_player(
        [_cand("2", "Thierno Barry", "Villa")],
        web_name="Barry", first_name=None, second_name="Barry")
    assert r.basis == "containment"


def test_ambiguity_is_refused_with_every_candidate_listed():
    with pytest.raises(UnresolvedPlayerError) as exc:
        resolve_understat_player(
            [_cand("10", "Cole Palmer", "Chelsea"),
             _cand("11", "Rio Palmer", "Leeds")],
            web_name="Palmer", first_name=None, second_name="Palmer")
    assert "Cole Palmer" in str(exc.value) and "Rio Palmer" in str(exc.value)
    assert exc.value.candidates and len(exc.value.candidates) == 2


def test_no_candidates_is_a_refusal_naming_the_gap():
    with pytest.raises(UnresolvedPlayerError, match="no candidate"):
        resolve_understat_player(
            [], web_name="Haaland", first_name="Erling", second_name="Haaland")


# ---------------------------------------------------------------------------
# the store: idempotent, contradiction-refusing, PIT
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    wh = Warehouse(tmp_path / "fpl.duckdb")
    yield UnderstatStore(wh)
    wh.close()


def _rows(playerdata):
    return parse_player_matches(playerdata, code=CODE, season=SEASON, as_of=AS_OF)


def test_append_is_idempotent(store, playerdata):
    df = _rows(playerdata)
    assert store.append("understat_player_match", df) == 2
    assert store.append("understat_player_match", df) == 0


def test_a_contradiction_at_the_same_instant_is_refused(store, playerdata):
    df = _rows(playerdata)
    store.append("understat_player_match", df)
    tampered = df.copy()
    tampered.loc[tampered.index[0], "goals"] = 9
    with pytest.raises(ConflictingUnderstatError, match="later as_of"):
        store.append("understat_player_match", tampered)


def test_a_revision_at_a_later_as_of_coexists_and_reads_pit(store, playerdata):
    """Understat re-runs its model; the revised xG is a NEW fact. as_of()
    at the old instant must still return the number we decided on then."""
    df = _rows(playerdata)
    store.append("understat_player_match", df)
    later = AS_OF + dt.timedelta(days=1)
    revised = df.copy().assign(as_of=pd.Timestamp(later))
    revised["xg"] = revised["xg"] + 0.1
    assert store.append("understat_player_match", revised) == 2

    old = store.as_of("understat_player_match", AS_OF)
    new = store.as_of("understat_player_match", later)
    assert len(old) == len(new) == 2
    assert (new["xg"].to_numpy() - old["xg"].to_numpy() == pytest.approx(0.1))
    # before anything was fetched, there is nothing -- not the earliest row
    assert store.as_of("understat_player_match", AS_OF - dt.timedelta(days=1)).empty


def test_naive_as_of_is_refused_at_the_door(store, playerdata):
    df = _rows(playerdata).assign(as_of=dt.datetime(2026, 8, 31))  # noqa: DTZ001 - naive on purpose
    with pytest.raises(ValueError, match="timezone-aware"):
        store.append("understat_player_match", df)


# ---------------------------------------------------------------------------
# the fetch path: guarded from CI, fake-transport end to end
# ---------------------------------------------------------------------------


def test_the_conftest_guard_stops_the_fetch_before_any_socket(tmp_path):
    """tests/unit/conftest.py sets FPL_EDGE_DISABLE_NETWORK_INGEST=1 for the
    whole suite; the ingest must honour it BEFORE resolving, fetching or
    writing anything."""
    with pytest.raises(UnderstatError, match="network ingest is disabled"):
        fetch_player_profile(CODE, SEASON, db=tmp_path / "fpl.duckdb")


class _FakeFetched:
    def __init__(self, body):
        self.body = body


class _FakeFetcher:
    """Replays the saved real bodies; records what was requested."""

    def __init__(self):
        self.requests: list[str] = []

    def get_json(self, endpoint, params=None):
        self.requests.append(endpoint)
        if endpoint.startswith("main/getPlayersName/"):
            return _FakeFetched(json.loads((FIX / "search_haaland.json").read_text()))
        if endpoint.startswith("getPlayerData/"):
            return _FakeFetched(json.loads((FIX / "player_8260_playerdata.json").read_text()))
        raise AssertionError(f"unexpected endpoint {endpoint}")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


@pytest.fixture()
def seeded_db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_player", pd.DataFrame([{
        "season": SEASON, "code": CODE, "element_id": 1, "web_name": "Haaland",
        "first_name": "Erling", "second_name": "Haaland", "position": 4,
        "team_code": 43, "as_of": pd.Timestamp("2026-08-01", tz="UTC"),
    }]))
    wh.close()
    return path


def test_fetch_resolves_stores_and_is_rerunnable(seeded_db, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    fake = _FakeFetcher()
    monkeypatch.setattr("fpl_edge.ingest.understat._fetcher", lambda: fake)

    summary = fetch_player_profile(CODE, SEASON, db=seeded_db, now=AS_OF)
    assert summary["understat_id"] == 8260
    assert summary["resolved_basis"] == "exact"
    assert summary["rows_appended"] == 2 and summary["rows_total"] == 2
    # one search + one data request; never a crawl
    assert fake.requests == ["main/getPlayersName/Haaland", "getPlayerData/8260"]

    # Second run: the map is cached, so NO second search happens. The re-fetch
    # is stamped at ITS OWN as_of (house append-only norm: a re-observation is
    # a new fact even when the value is unchanged), so the entity count -- not
    # the row count -- is what must stay 2.
    again = fetch_player_profile(CODE, SEASON, db=seeded_db,
                                 now=AS_OF + dt.timedelta(hours=1))
    assert again["rows_total"] == 2
    assert fake.requests == ["main/getPlayersName/Haaland", "getPlayerData/8260",
                             "getPlayerData/8260"]

    wh = Warehouse.read_copy(seeded_db)
    try:
        mapping = wh.sql("SELECT * FROM understat_player_map")
        assert len(mapping) == 1
        assert mapping.iloc[0]["resolved_basis"] == "exact"
    finally:
        wh.close()


def test_a_refused_resolution_writes_nothing(seeded_db, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")

    class _WrongPlayer(_FakeFetcher):
        def get_json(self, endpoint, params=None):
            self.requests.append(endpoint)
            if endpoint.startswith("main/getPlayersName/"):
                return _FakeFetched({"response": {"success": True, "players": [
                    {"id": "99", "player": "Somebody Else", "team": "Elsewhere"},
                ]}})
            raise AssertionError("getPlayerData must never be called after a refusal")

    fake = _WrongPlayer()
    monkeypatch.setattr("fpl_edge.ingest.understat._fetcher", lambda: fake)
    with pytest.raises(UnresolvedPlayerError, match="Somebody Else"):
        fetch_player_profile(CODE, SEASON, db=seeded_db, now=AS_OF)

    wh = Warehouse.read_copy(seeded_db)
    try:
        tables = set(wh.sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main'")["table_name"])
        for t in ("understat_player_map", "understat_player_match"):
            if t in tables:
                assert wh.sql(f"SELECT count(*) AS n FROM {t}").iloc[0]["n"] == 0
    finally:
        wh.close()


def test_an_unknown_code_is_refused_before_any_request(seeded_db, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    fake = _FakeFetcher()
    monkeypatch.setattr("fpl_edge.ingest.understat._fetcher", lambda: fake)
    with pytest.raises(UnderstatError, match="no player with code 424242"):
        fetch_player_profile(424242, SEASON, db=seeded_db, now=AS_OF)
    assert fake.requests == []
