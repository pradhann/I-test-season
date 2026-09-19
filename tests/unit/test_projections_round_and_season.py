"""The two provider refusals of 2026-09-18, reproduced on a temporary warehouse.

Both providers ran nightly for weeks and then refused a payload that was
correct. Neither refusal was a site change, and neither was a bad row reaching
the warehouse, so a unit test on the parser would have caught nothing. What
each one needed was the warehouse it was asked about, which is what these
tests build.

**Rotowire.** ``_next_gw`` targets the first gameweek whose deadline is still
ahead. GW5's deadline was 2026-09-18 17:30 UTC and the scheduled ingest ran at
17:31, so it targeted GW6 while the lineups page carried all ten GW5 fixtures,
and the fixture-pair guard refused the page. The gameweek now comes from the
fixtures printed on the page.

**LiveFPL.** ``predictedEOs/1.json`` had shrunk to element_ids 1 to 599 while
FPL's element space had grown to 659. Every id in the file was a 2026-27 id,
the 60 mismatches were all players the file omits, and 60 of 599 is 10.02%
against a 10% threshold. The threshold now counts the ids in play on both
sides.

Offline. Every fetch is stubbed, so these measure the two resolutions rather
than either site's uptime.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.http import Fetched
from fpl_edge.ingest.projections import cli, livefpl, rotowire
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "projections"
SEASON = "2026-27"

#: One minute after the GW5 deadline, which is when the scheduled run fired.
FETCHED_AT = dt.datetime(2026, 9, 18, 17, 31, tzinfo=dt.timezone.utc)
EARLY = dt.datetime(2026, 9, 1, 5, 0, tzinfo=dt.timezone.utc)

#: The clubs on the archived lineups page, with their FPL team codes.
CLUBS = (("ARS", 3, "Arsenal"), ("COV", 9, "Coventry"),
         ("HUL", 88, "Hull"), ("MUN", 1, "Man Utd"))

#: The two fixtures the archived page carries.
PAGE_PAIRS = ((3, 9), (88, 1))

#: A round that is not on the page, standing in for GW4.
OTHER_PAIRS = ((9, 88), (1, 3))


def _lineups_html() -> str:
    return (FIXTURES / "rotowire_lineups.html").read_text()


def _fetched(body: object) -> Fetched:
    return Fetched(body=body, fetched_at=FETCHED_AT, sha256="0" * 64,
                   body_path=Path("data/raw/stub"), http_status=200,
                   from_cache=False)


def _fixture_rows(gw: int, pairs: tuple[tuple[int, int], ...],
                  first_id: int) -> list[dict[str, object]]:
    return [
        {"season": SEASON, "fixture_id": first_id + i, "gw": gw,
         "kickoff_utc": dt.datetime(2026, 9, 19, 14, 0, tzinfo=dt.timezone.utc),
         "home_team_code": home, "away_team_code": away, "finished": False,
         "home_score": None, "away_score": None, "as_of": EARLY}
        for i, (home, away) in enumerate(pairs)
    ]


@pytest.fixture
def warehouse(tmp_path):
    """A warehouse holding today's shape: GW5 fixtures, a GW5 deadline gone by."""
    path = tmp_path / "round.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": code, "team_id": i + 1, "name": name,
         "short_name": short, "as_of": EARLY}
        for i, (short, code, name) in enumerate(CLUBS)
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 4,
         "deadline_utc": dt.datetime(2026, 9, 12, 12, 30, tzinfo=dt.timezone.utc),
         "as_of": EARLY},
        {"season": SEASON, "gw": 5,
         "deadline_utc": dt.datetime(2026, 9, 18, 17, 30, tzinfo=dt.timezone.utc),
         "as_of": EARLY},
        {"season": SEASON, "gw": 6,
         "deadline_utc": dt.datetime(2026, 10, 10, 10, 0, tzinfo=dt.timezone.utc),
         "as_of": EARLY},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": 100, "element_id": 1, "web_name": "Saka",
         "first_name": "Bukayo", "second_name": "Saka", "position": 3,
         "team_code": 3, "as_of": EARLY},
    ]))
    wh.append("fact_fixture", pd.DataFrame(
        _fixture_rows(4, OTHER_PAIRS, 40) + _fixture_rows(5, PAGE_PAIRS, 50)
    ))
    ProjectionStore(wh)
    yield wh
    wh.close()


def _rotowire_step(warehouse, monkeypatch, html: str | None = None):
    monkeypatch.setattr(rotowire, "fetch",
                        lambda **kw: _fetched(html or _lineups_html()))
    store = ProjectionStore(warehouse)
    return cli._ingest_rotowire(warehouse, store, SEASON, first_gw=1, last_gw=8)


# ---------------------------------------------------------------------------
# rotowire: the gameweek comes from the page
# ---------------------------------------------------------------------------


def test_the_clock_says_gw6_and_the_page_is_written_under_gw5(warehouse, monkeypatch):
    """2026-09-18 17:31 UTC, one minute after the GW5 deadline.

    The page holds the round about to be played; the deadline had rolled the
    target forward to the round after it. Five of the twelve ingest errors
    since 2026-09-04 were this same window.
    """
    assert cli._next_gw(warehouse, SEASON, FETCHED_AT) == 6
    result = _rotowire_step(warehouse, monkeypatch)
    assert result.ok
    assert result.status == ""
    assert "GW5" in result.detail
    landed = warehouse.sql(
        "SELECT DISTINCT gw FROM fact_predicted_lineup WHERE provider = 'rotowire'"
    )
    assert list(landed["gw"]) == [5]


def test_a_partial_page_still_names_its_round(warehouse):
    """Rotowire drops a fixture once it has kicked off, so Sunday's page is a subset."""
    entries = rotowire.parse_lineups(_lineups_html())
    remaining = [e for e in entries if {e.team_abbr, e.opponent_abbr} == {"ARS", "COV"}]
    fixtures_by_gw = {4: set(OTHER_PAIRS), 5: set(PAGE_PAIRS)}
    short_to_code = {short: code for short, code, _ in CLUBS}
    assert rotowire.resolve_gameweek(remaining, fixtures_by_gw, short_to_code) == 5


def test_a_round_we_cannot_name_is_a_no_source_and_not_an_error(warehouse, monkeypatch):
    """A cup slate, or a page ahead of the fixture list, costs rows and not a run.

    The guard's intent is unchanged: nothing is written. What changed is that
    the ledger says "no_source" with the reason once, rather than "error" every
    night until somebody reads the log.
    """
    warehouse.sql("DELETE FROM fact_fixture WHERE gw = 5")
    result = _rotowire_step(warehouse, monkeypatch)
    assert result.ok
    assert result.status == "no_source"
    assert result.rows == 0
    assert "not all in any one gameweek" in result.note
    assert "gw4 misses" in result.note
    landed = warehouse.sql("SELECT count(*) AS c FROM fact_predicted_lineup")
    assert int(landed.iloc[0]["c"]) == 0


def test_a_page_in_two_rounds_at_once_identifies_neither():
    entries = rotowire.parse_lineups(_lineups_html())
    both = {4: {(3, 9), (88, 1), (9, 88)}, 5: {(3, 9), (88, 1), (1, 3)}}
    short_to_code = {short: code for short, code, _ in CLUBS}
    with pytest.raises(rotowire.UnmappableRoundError, match="more than one gameweek"):
        rotowire.resolve_gameweek(entries, both, short_to_code)


def test_an_unknown_club_is_still_a_hard_error_and_not_a_no_source():
    """A club we cannot name is a page change, which somebody has to look at."""
    entries = rotowire.parse_lineups(_lineups_html())
    with pytest.raises(rotowire.RotowireError, match="not in dim_team"):
        rotowire.resolve_gameweek(entries, {5: set(PAGE_PAIRS)}, {"ARS": 3})


def test_the_written_gameweek_is_still_checked_pair_by_pair(warehouse, monkeypatch):
    """resolve_gameweek picking the gw does not retire the guard that checks it."""
    monkeypatch.setattr(
        rotowire, "resolve_gameweek",
        lambda entries, fixtures_by_gw, short_to_code: 4,
    )
    with pytest.raises(rotowire.RotowireError, match="not fixtures of"):
        _rotowire_step(warehouse, monkeypatch)


# ---------------------------------------------------------------------------
# livefpl: a file that publishes fewer players than the season holds
# ---------------------------------------------------------------------------

#: What dim_player held for each season at the failing run.
CATALOGS = {
    "2022-23": set(range(1, 779)),
    "2023-24": set(range(1, 866)),
    "2024-25": set(range(1, 785)),
    "2025-26": set(range(1, 842)),
    "2026-27": set(range(1, 660)),
}

#: What predictedEOs/1.json carried on 2026-09-18: a contiguous prefix.
FILE_IDS = set(range(1, 600))


def test_the_subset_file_that_stopped_the_ingest_resolves_to_this_season():
    """599 of the season's 659 ids, all of them 2026-27 ids, none of them wrong."""
    fit = livefpl.fit_season(FILE_IDS, CATALOGS)
    assert fit.season == "2026-27"
    assert fit.unknown_to_season == 0
    assert fit.absent_from_file == 60
    assert fit.line() == ("599 ids -> 2026-27, 0 unknown to the season, "
                          "60 of the season's ids not in the file")


def test_the_share_that_refused_it_was_taken_of_the_wrong_denominator():
    """60 of 599 is 10.02% and refuses. 60 of the 659 ids in play is 9.1%."""
    assert 60 > livefpl.MAX_SEASON_MISMATCH * len(FILE_IDS)
    assert 60 <= livefpl.MAX_SEASON_MISMATCH * len(FILE_IDS | CATALOGS["2026-27"])


def test_a_season_that_fails_the_threshold_is_not_a_rival_for_the_margin():
    """2022-23 was 179 ids away and 179 is under 3 x 60, so the margin refused too."""
    assert 179 < livefpl.MIN_SEASON_MISMATCH_RATIO * 60
    assert livefpl.fit_season(FILE_IDS, CATALOGS).season == "2026-27"


def test_a_file_keyed_on_another_season_is_still_refused():
    """The reason the guard exists. Last season's ids must not map onto this one."""
    with pytest.raises(livefpl.AmbiguousSeasonError, match="within 10%"):
        livefpl.fit_season(set(range(1, 842)), {"2026-27": CATALOGS["2026-27"]})


def test_the_ledger_note_says_what_the_file_covered(tmp_path, monkeypatch):
    """One line, carrying the coverage that the refusal turned out to be about."""
    path = tmp_path / "season.duckdb"
    with Warehouse(path) as wh:
        wh.append("dim_player", pd.DataFrame([
            {"season": season, "code": 10_000 + element, "element_id": element,
             "web_name": f"p{element}", "first_name": None, "second_name": None,
             "position": 3, "team_code": 3, "as_of": EARLY}
            for season, ids in CATALOGS.items() for element in sorted(ids)
        ]))
        ProjectionStore(wh)

        bodies = {
            "player_info": {"codes": {str(i): 10_000 + i for i in sorted(FILE_IDS)}},
            "predicted_eo": {str(i): 12.0 / len(FILE_IDS) for i in sorted(FILE_IDS)},
            "top10k": {str(i): 12.0 / len(CATALOGS["2026-27"])
                       for i in sorted(CATALOGS["2026-27"])},
        }
        bodies["elite"] = bodies["top10k"]
        monkeypatch.setattr(livefpl, "fetch",
                            lambda kind, **kw: _fetched(bodies[kind]))
        result = cli._ingest_livefpl(wh, ProjectionStore(wh), SEASON,
                                     first_gw=5, last_gw=8)

    assert result.ok
    assert "\n" not in result.note
    assert result.note.startswith(
        "predicted_eo 599 ids -> 2026-27, 0 unknown to the season, "
        "60 of the season's ids not in the file"
    )
    assert "top10k 659 ids -> 2026-27, 0 unknown to the season, 0 of" in result.note
