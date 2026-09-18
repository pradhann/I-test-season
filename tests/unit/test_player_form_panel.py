"""The ``player_form`` panel: the leak guard, season spanning, honest empties.

Three assertions carry this file.

``test_a_gameweek_finalised_after_the_instant_is_invisible`` is the leak guard.
A form row's ``as_of`` is the points-finalisation instant, so a read at a
deadline must not see the gameweek that finalises an hour later. If that ever
passes silently, every backtest built on this panel is reading results early.

``test_rows_span_seasons_and_every_row_names_its_own`` is the reason the panel
exists rather than a three-line query. In August there is nothing settled, so
the recent form a reader wants is last season's, and returning those rows
without the season on each one labels one season's numbers as another's.

``test_nothing_settled_is_an_empty_with_the_reason`` is rule 9: absence is
shown as absence, with a reason that names the player and the instant.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
from fpl_edge.platform.registry import ParamsInvalid, registered, run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
THIS = "2026-27"
LAST = "2025-26"

T0 = pd.Timestamp("2025-07-01 12:00", tz="UTC")

#: (code, web_name, team_code)
PLAYERS = [(100, "Semenyo", 1), (200, "Raya", 2)]

#: (season, gw, fixture_id, home_code, away_code, finalised_at)
FIXTURES = [
    (LAST, 36, 901, 1, 2, "2026-05-02 20:00"),
    (LAST, 37, 902, 2, 1, "2026-05-09 20:00"),
    (LAST, 38, 903, 1, 2, "2026-05-16 20:00"),
    (THIS, 1, 101, 1, 2, "2026-08-15 20:00"),
]


def _seed(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": season, "team_code": code, "team_id": code,
         "name": name, "short_name": short, "as_of": T0}
        for season in (LAST, THIS)
        for code, name, short in ((1, "Bournemouth", "BOU"), (2, "Arsenal", "ARS"))
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": season, "code": code, "element_id": code, "web_name": web,
         "first_name": web, "second_name": web, "position": 3,
         "team_code": team, "as_of": T0}
        for season in (LAST, THIS)
        for code, web, team in PLAYERS
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": season, "code": code, "element_id": code,
         "price_tenths": 75, "selected_by_pct": 10.0, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": T0}
        for season in (LAST, THIS)
        for code, _, _ in PLAYERS
    ]))
    wh.append("fact_fixture", pd.DataFrame([
        {"season": season, "fixture_id": fid, "gw": gw,
         "home_team_code": home, "away_team_code": away,
         "kickoff_utc": pd.Timestamp(stamp, tz="UTC") - pd.Timedelta(hours=2),
         "finished": True, "home_score": 1, "away_score": 0,
         # The warehouse refuses a finished fixture observable before its own
         # kickoff, so the schedule row is stamped at finalisation too.
         "as_of": pd.Timestamp(stamp, tz="UTC")}
        for season, gw, fid, home, away, stamp in FIXTURES
    ]))
    wh.append("fact_player_fixture", pd.DataFrame([
        {"season": season, "code": 100, "fixture_id": fid, "gw": gw,
         "minutes": 90, "goals_scored": 1, "assists": 0, "clean_sheets": 0,
         "goals_conceded": 0, "own_goals": 0, "penalties_saved": 0,
         "penalties_missed": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0,
         "bonus": 2, "bps": 30, "starts": 1, "tackles": 1,
         "clearances_blocks_interceptions": 1, "recoveries": 5,
         "defensive_contribution": 7, "expected_goals": 0.4,
         "expected_assists": 0.1, "expected_goals_conceded": 1.2,
         "total_points": 8, "was_home": home == 1,
         "as_of": pd.Timestamp(stamp, tz="UTC")}
        for season, gw, fid, home, _away, stamp in FIXTURES
    ]))
    wh.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return _seed(tmp_path)


def test_the_panel_is_registered():
    assert "player_form" in registered()


def test_a_gameweek_finalised_after_the_instant_is_invisible(db):
    """THE leak guard. GW1 of this season finalises at 20:00 on 15 August.

    Read at 19:00 that day, it must not be in the payload. A panel that
    returns it is reading a result an hour before the world had it, and every
    backtest built on this surface inherits that.
    """
    before = run_script(
        "player_form",
        {"code": 100, "last_k": 10, "as_of": "2026-08-15T19:00:00Z"},
        db=db,
    ).result
    assert THIS not in before["seasons_covered"], before["seasons_covered"]
    assert all(row["season"] == LAST for row in before["rows"])

    after = run_script(
        "player_form",
        {"code": 100, "last_k": 10, "as_of": "2026-08-15T21:00:00Z"},
        db=db,
    ).result
    assert THIS in after["seasons_covered"]
    assert after["rows"][-1]["gw"] == 1


def test_rows_span_seasons_and_every_row_names_its_own(db):
    result = run_script(
        "player_form",
        {"code": 100, "last_k": 4, "as_of": "2026-08-15T21:00:00Z"},
        db=db,
    ).result
    assert result["seasons_covered"] == [LAST, THIS]
    assert [row["season"] for row in result["rows"]] == [LAST, LAST, LAST, THIS]
    assert [row["gw"] for row in result["rows"]] == [36, 37, 38, 1]
    assert "span" in result["season_note"]


def test_the_season_filter_restricts_rows_and_is_echoed(db):
    result = run_script(
        "player_form",
        {"code": 100, "last_k": 10, "season": LAST,
         "as_of": "2026-08-15T21:00:00Z"},
        db=db,
    ).result
    assert result["season_filter"] == LAST
    assert result["seasons_covered"] == [LAST]
    assert len(result["rows"]) == 3


def test_the_opponent_and_venue_come_from_the_schedule(db):
    result = run_script(
        "player_form",
        {"code": 100, "last_k": 10, "as_of": "2026-08-15T21:00:00Z"},
        db=db,
    ).result
    first = result["rows"][0]
    assert first["opponent"] == "ARS"
    assert first["venue"] == "H"
    second = result["rows"][1]
    assert second["venue"] == "A"


def test_nothing_settled_is_an_empty_with_the_reason(db):
    """A player with no settled gameweek says so, and names why."""
    result = run_script(
        "player_form",
        {"code": 200, "last_k": 6, "as_of": "2026-08-15T21:00:00Z"},
        db=db,
    ).result
    assert result["empty"] is True
    assert "Raya" in result["reason"]
    assert "finalised" in result["reason"]


def test_an_unknown_code_is_an_empty_that_names_the_code(db):
    result = run_script("player_form", {"code": 999999}, db=db).result
    assert result["empty"] is True
    assert "999999" in result["reason"]
    assert "element id" in result["reason"]


def test_a_naive_as_of_is_refused(db):
    with pytest.raises(ValueError, match="timezone"):
        run_script("player_form", {"code": 100, "as_of": "2026-08-15T21:00:00"},
                   db=db)


def test_the_params_schema_rejects_an_entry_id(db):
    """entry_id is never an input here, and additionalProperties proves it."""
    with pytest.raises(ParamsInvalid):
        run_script("player_form", {"code": 100, "entry_id": 4490171}, db=db)


def test_totals_are_the_sum_of_the_rows_shown(db):
    result = run_script(
        "player_form",
        {"code": 100, "last_k": 10, "as_of": "2026-08-15T21:00:00Z"},
        db=db,
    ).result
    assert result["totals"]["rows"] == len(result["rows"])
    assert result["totals"]["points"] == sum(r["points"] for r in result["rows"])
    assert result["totals"]["minutes"] == sum(r["minutes"] for r in result["rows"])
