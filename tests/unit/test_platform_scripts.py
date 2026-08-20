"""Every panel script against an empty warehouse: empty, explained, never faked.

This is the anti-fabrication suite. A dashboard that invents plausible rows
when the data is missing is worse than one that shows nothing, because it is
believed at the deadline. So each script is run against a schema-only warehouse
and must come back ``{empty: true, reason: ...}`` with a reason that names what
is missing -- and, critically, must NOT come back with rows.

The reason strings are asserted loosely (a keyword, not the sentence) so that
improving the wording is not a test change, but the *presence* of an actionable
reason is pinned.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers all five)
from fpl_edge.platform.registry import registered, run_script, script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc

ALL_SCRIPTS = ["squad_overview", "projection_table", "fixture_ticker",
               "price_radar", "idea_registry"]


@pytest.fixture()
def empty_db(tmp_path):
    """A warehouse with the full schema and not one fact in it."""
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


@pytest.fixture()
def seeded_db(tmp_path):
    """Enough real-shaped data for the fixture ticker to have something to say."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    stamp = pd.Timestamp("2026-08-01", tz="UTC")
    wh.append("dim_team", pd.DataFrame([
        {"season": "2026-27", "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": stamp},
        {"season": "2026-27", "team_code": 2, "team_id": 2, "name": "Chelsea",
         "short_name": "CHE", "as_of": stamp},
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": "2026-27", "gw": 1, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-14 17:30", tz="UTC"), "as_of": stamp},
    ]))
    wh.append("fact_fixture", pd.DataFrame([
        {"season": "2026-27", "fixture_id": 1, "gw": 1,
         "kickoff_utc": pd.Timestamp("2099-08-14 19:00", tz="UTC"),
         "home_team_code": 1, "away_team_code": 2, "finished": False,
         "home_score": None, "away_score": None, "as_of": stamp},
    ]))
    wh.close()
    return path


def test_all_five_scripts_are_registered():
    assert set(ALL_SCRIPTS) <= set(registered())
    assert len(registered()) >= 5


@pytest.mark.parametrize("name", ALL_SCRIPTS)
def test_every_script_is_empty_and_explains_itself(name, empty_db):
    run = run_script(name, {}, db=empty_db)
    assert run.result.get("empty") is True, (
        f"{name} produced rows from an empty warehouse: {run.result}"
    )
    reason = run.result.get("reason", "")
    assert isinstance(reason, str) and len(reason) > 20, (
        f"{name} returned empty with no usable reason: {reason!r}"
    )


@pytest.mark.parametrize("name", ALL_SCRIPTS)
def test_an_empty_result_carries_no_rows(name, empty_db):
    """The shape must be *only* {empty, reason} -- not an empty rows list
    alongside a summary, which renders as a real panel with nothing in it."""
    run = run_script(name, {}, db=empty_db)
    assert set(run.result) == {"empty", "reason"}


@pytest.mark.parametrize("name", ALL_SCRIPTS)
def test_every_script_still_stamps_provenance_when_empty(name, empty_db):
    run = run_script(name, {}, db=empty_db)
    assert run.provenance["script"] == name
    assert run.provenance["repo_sha"]
    assert dt.datetime.fromisoformat(run.provenance["generated_at"]).tzinfo is not None


@pytest.mark.parametrize("name", ALL_SCRIPTS)
def test_every_script_declares_both_schemas_and_a_docstring(name):
    spec = script(name)
    assert spec.params_schema["type"] == "object"
    assert spec.result_schema.get("oneOf"), "result schema must admit the empty shape"
    assert len(spec.doc) > 20, f"{name} needs a docstring saying what it returns"
    assert spec.title and spec.description


def test_the_reason_names_the_fix_not_just_the_gap(empty_db):
    """A reason a reader cannot act on is only half an answer."""
    run = run_script("fixture_ticker", {}, db=empty_db)
    assert "ingest" in run.result["reason"].lower()


def test_fixture_ticker_returns_real_fixtures_when_they_exist(seeded_db):
    run = run_script("fixture_ticker", {"horizon": 1}, db=seeded_db)
    assert run.result.get("empty") is not True
    assert run.result["gws"] == [1]
    assert run.result["row_count"] == 2

    by_name = {t["short_name"]: t for t in run.result["teams"]}
    ars = by_name["ARS"]["fixtures"][0]
    che = by_name["CHE"]["fixtures"][0]
    # Home is upper-case, away lower-case -- the Telegram grid's convention.
    assert ars["opponents"][0]["label"] == "CHE"
    assert ars["opponents"][0]["is_home"] is True
    assert che["opponents"][0]["label"] == "ars"
    assert che["opponents"][0]["is_home"] is False
    assert ars["blank"] is False and ars["double"] is False


def test_fixture_ticker_marks_a_blank_gameweek_explicitly(seeded_db):
    """A club with no fixture gets a blank slot, not a missing key: a blank is
    a decision, and a missing key renders as nothing at all."""
    run = run_script("fixture_ticker", {"horizon": 2}, db=seeded_db)
    ars = next(t for t in run.result["teams"] if t["short_name"] == "ARS")
    gw2 = ars["fixtures"][1]
    assert gw2["gw"] == 2 and gw2["blank"] is True and gw2["opponents"] == []


def test_price_radar_needs_two_snapshots_to_say_anything(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("fact_player_state", pd.DataFrame([{
        "season": "2026-27", "code": 100, "element_id": 1, "price_tenths": 50,
        "selected_by_pct": 1.0, "status": "a", "chance_of_playing_next_round": None,
        "news": "", "news_added": None, "transfers_in_event": 10,
        "transfers_out_event": 2, "cost_change_start": 0,
        "as_of": pd.Timestamp("2026-08-01", tz="UTC"),
    }]))
    wh.close()
    run = run_script("price_radar", {}, db=path)
    assert run.result["empty"] is True
    assert "one" in run.result["reason"].lower()


def test_price_radar_reports_flow_between_two_snapshots(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    rows = []
    for stamp, tin, tout in (
        (pd.Timestamp("2026-08-01 00:00", tz="UTC"), 1_000, 500),
        (pd.Timestamp("2026-08-01 02:00", tz="UTC"), 5_000, 700),
    ):
        rows.append({
            "season": "2026-27", "code": 100, "element_id": 1, "price_tenths": 50,
            "selected_by_pct": 1.0, "status": "a",
            "chance_of_playing_next_round": None, "news": "", "news_added": None,
            "transfers_in_event": tin, "transfers_out_event": tout,
            "cost_change_start": 0, "as_of": stamp,
        })
    wh.append("fact_player_state", pd.DataFrame(rows))
    wh.append("dim_player", pd.DataFrame([{
        "season": "2026-27", "code": 100, "element_id": 1, "web_name": "Riser",
        "first_name": "R", "second_name": "Iser", "position": 3, "team_code": 1,
        "as_of": pd.Timestamp("2026-08-01", tz="UTC"),
    }]))
    wh.close()

    run = run_script("price_radar", {}, db=path)
    assert run.result.get("empty") is not True
    assert run.result["window"]["hours"] == 2.0
    top = run.result["risers"][0]
    # 4000 in, 200 out over 2h = net 3800, 1900/hour.
    assert top["name"] == "Riser"
    assert top["transfers_in"] == 4_000 and top["transfers_out"] == 200
    assert top["net"] == 3_800 and top["net_per_hour"] == 1_900.0


def test_price_radar_drops_a_counter_reset_rather_than_calling_it_an_outflow(tmp_path):
    """Counters reset at each deadline; a negative delta is a new gameweek."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    rows = []
    for stamp, tin, tout in (
        (pd.Timestamp("2026-08-01 00:00", tz="UTC"), 900_000, 100_000),
        (pd.Timestamp("2026-08-01 02:00", tz="UTC"), 12, 3),
    ):
        rows.append({
            "season": "2026-27", "code": 100, "element_id": 1, "price_tenths": 50,
            "selected_by_pct": 1.0, "status": "a",
            "chance_of_playing_next_round": None, "news": "", "news_added": None,
            "transfers_in_event": tin, "transfers_out_event": tout,
            "cost_change_start": 0, "as_of": stamp,
        })
    wh.append("fact_player_state", pd.DataFrame(rows))
    wh.close()
    run = run_script("price_radar", {}, db=path)
    assert run.result["empty"] is True
    assert "reset" in run.result["reason"].lower()


def test_projection_table_says_to_run_solve_when_no_artefact(empty_db):
    run = run_script("projection_table", {}, db=empty_db)
    assert run.result["empty"] is True
    assert "solve" in run.result["reason"].lower()


def test_idea_registry_is_empty_before_any_idea_is_filed(empty_db):
    run = run_script("idea_registry", {}, db=empty_db)
    assert run.result["empty"] is True


def test_squad_overview_does_not_reach_the_network_without_players(empty_db, monkeypatch):
    """With no players in the warehouse the script must short-circuit BEFORE
    touching the FPL API: a panel refresh on an empty database must not fire
    an HTTP request."""
    import httpx

    def explode(*a, **k):
        raise AssertionError("squad_overview reached the network")

    monkeypatch.setattr(httpx.Client, "request", explode)
    monkeypatch.setattr(httpx.Client, "send", explode)
    run = run_script("squad_overview", {}, db=empty_db)
    assert run.result["empty"] is True
    assert "ingest" in run.result["reason"].lower()
