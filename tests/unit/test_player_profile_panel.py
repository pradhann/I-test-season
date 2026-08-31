"""The player_profile panel: honest emptiness, FPL-lens content, PIT reads.

The contract under test is CHAT_ARCHITECTURE §6's read half: the panel NEVER
fetches -- it reads what the on-demand ingest cached and reports absence with
a reason that names the fix. The silent-failure lens applies (PANEL_LEDGER):
if this panel silently did nothing, a drawer would render a fabricated
profile or an unexplained blank; every test here pins one of the ways it must
instead say something true.

Hermetic: every test seeds its own DuckDB in tmp_path.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401 - registration is the import
from fpl_edge.ingest.understat import UnderstatStore
from fpl_edge.platform.registry import registered, run_script, script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
CODE = 223094
STAMP = pd.Timestamp("2026-08-01", tz="UTC")
FETCHED = dt.datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def _match(match_id, date, *, minutes=90, shots=3, goals=0, assists=0,
           key_passes=1, npg=None, xg=0.4, xa=0.1, npxg=None, position="FW",
           as_of=FETCHED):
    return {
        "understat_id": 8260, "code": CODE, "season": SEASON,
        "match_id": match_id, "date": dt.date.fromisoformat(date),
        "minutes": minutes, "shots": shots, "goals": goals, "assists": assists,
        "key_passes": key_passes, "npg": goals if npg is None else npg,
        "xg": xg, "xa": xa, "npxg": xg if npxg is None else npxg,
        "position": position, "h_team": "Manchester City", "a_team": "Burnley",
        "h_goals": 2, "a_goals": 0, "as_of": as_of,
    }


def _seed(tmp_path, matches=(), *, with_map=True, with_tables=True):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_player", pd.DataFrame([{
        "season": SEASON, "code": CODE, "element_id": 1, "web_name": "Haaland",
        "first_name": "Erling", "second_name": "Haaland", "position": 4,
        "team_code": 43, "as_of": STAMP,
    }]))
    if with_tables:
        store = UnderstatStore(wh)
        if with_map:
            store.append("understat_player_map", pd.DataFrame([{
                "code": CODE, "understat_id": 8260,
                "understat_name": "Erling Haaland",
                "understat_team": "Manchester City",
                "resolved_basis": "exact", "as_of": FETCHED,
            }]))
        if matches:
            store.append("understat_player_match", pd.DataFrame(list(matches)))
    wh.close()
    return path


def _run(db, **params):
    return run_script("player_profile", {"code": CODE, **params}, db=db).result


def test_the_script_is_registered_with_both_schemas():
    assert "player_profile" in registered()
    spec = script("player_profile")
    assert spec.params_schema["properties"]["code"]["type"] == "integer"
    assert "oneOf" in spec.result_schema  # the honest-empty branch is wrapped in


def test_an_unknown_code_is_a_refusal_not_a_profile_of_nobody(tmp_path):
    res = run_script("player_profile", {"code": 424242},
                     db=_seed(tmp_path)).result
    assert res["empty"] and "424242" in res["reason"]


def test_absent_tables_read_as_absence_with_a_fetch_hint(tmp_path):
    res = _run(_seed(tmp_path, with_tables=False))
    assert res["empty"]
    assert "fetch" in res["reason"].lower()
    assert "/api/players/223094/fetch_profile" in res["reason"]


def test_no_rows_yet_names_the_player_and_the_fix_and_never_fetches(tmp_path):
    res = _run(_seed(tmp_path, matches=()))
    assert res["empty"]
    assert "Haaland" in res["reason"]
    assert "never fetches" in res["reason"]
    assert "/api/players/223094/fetch_profile" in res["reason"]


def test_a_cached_profile_reads_fpl_first(tmp_path):
    db = _seed(tmp_path, matches=[
        _match(1, "2026-08-16", shots=5, goals=2, xg=0.9),
        _match(2, "2026-08-23", shots=2, goals=0, xg=0.6, minutes=61,
               position="Sub"),
    ])
    res = _run(db)
    assert not res.get("empty")
    assert res["name"] == "Haaland"
    assert [m["date"] for m in res["matches"]] == ["2026-08-16", "2026-08-23"]
    assert res["totals"]["shots"] == 7 and res["totals"]["goals"] == 2
    assert res["totals"]["xg"] == pytest.approx(1.5)
    # venue/opponent derived from the mapped team, honestly
    assert res["matches"][0]["venue"] == "H"
    assert res["matches"][0]["opponent"] == "Burnley"
    # minutes pattern: the Sub row is a cameo, not a start
    assert res["minutes_pattern"]["starts"] == 1
    assert res["minutes_pattern"]["sub_appearances"] == 1
    assert res["matches"][1]["started"] is False


def test_finishing_luck_is_signed_and_labelled_as_luck(tmp_path):
    db = _seed(tmp_path, matches=[_match(1, "2026-08-16", goals=2, xg=0.9)])
    res = _run(db)
    assert res["finishing"]["goals_minus_xg"] == pytest.approx(1.1)
    assert "luck" in res["finishing"]["label"]
    assert "variance" in res["finishing"]["label"]
    assert "not FPL points" in res["note"]


def test_as_of_is_respected_rows_fetched_later_are_invisible(tmp_path):
    """PIT: a profile fetched after the instant asked about did not exist at
    that instant, and the panel must say so instead of quietly reading it."""
    db = _seed(tmp_path, matches=[_match(1, "2026-08-16")])
    before = (FETCHED - dt.timedelta(days=1)).isoformat()
    res = _run(db, as_of=before)
    assert res["empty"], "rows stamped after as_of leaked into a PIT read"
    res_now = _run(db, as_of=(FETCHED + dt.timedelta(minutes=1)).isoformat())
    assert not res_now.get("empty")


def test_a_revision_reads_latest_at_each_instant(tmp_path):
    """Understat revising xG is a new fact at a later as_of; each instant
    must see the number that was current THEN."""
    later = FETCHED + dt.timedelta(days=1)
    db = _seed(tmp_path, matches=[
        _match(1, "2026-08-16", xg=0.4),
        _match(1, "2026-08-16", xg=0.7, as_of=later),
    ])
    old = _run(db, as_of=(FETCHED + dt.timedelta(hours=1)).isoformat())
    new = _run(db, as_of=(later + dt.timedelta(hours=1)).isoformat())
    assert old["matches"][0]["xg"] == pytest.approx(0.4)
    assert new["matches"][0]["xg"] == pytest.approx(0.7)
    assert len(new["matches"]) == 1, "a revision must supersede, not duplicate"


def test_a_bad_as_of_reads_nothing_rather_than_reading_now(tmp_path):
    db = _seed(tmp_path, matches=[_match(1, "2026-08-16")])
    res = _run(db, as_of="not-a-timestamp")
    assert res["empty"] and "not-a-timestamp" in res["reason"]


def test_zero_minutes_yields_null_per90_not_a_division(tmp_path):
    db = _seed(tmp_path, matches=[
        _match(1, "2026-08-16", minutes=0, shots=0, xg=0.0, xa=0.0,
               key_passes=0, position="Sub"),
    ])
    res = _run(db)
    assert res["per90"] is None
