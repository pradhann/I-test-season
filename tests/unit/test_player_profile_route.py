"""POST/GET /api/players/{code}/fetch_profile: the async-on-click contract.

The route is the ONLY server surface that triggers the Understat ingest, and
the drawer's whole loop rests on three promises: the POST returns immediately
(202) while the fetch runs; the state is pollable and ends in ``done`` or in
``error`` carrying the ingest's own words (a resolver refusal must arrive
verbatim, candidates and all); and a second click while running starts no
second fetch. All exercised with the ingest monkeypatched -- no test touches
understat.com, and the unit conftest's network guard would fail any that
tried.
"""

from __future__ import annotations

import datetime as dt
import threading
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fpl_edge.platform.scripts  # noqa: F401  (registers the panel scripts)
from fpl_edge.ingest import understat as understat_mod
from fpl_edge.platform.app import create_app
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
CODE = 223094


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_player", pd.DataFrame([{
        "season": SEASON, "code": CODE, "element_id": 1, "web_name": "Haaland",
        "first_name": "Erling", "second_name": "Haaland", "position": 4,
        "team_code": 43, "as_of": pd.Timestamp("2026-08-01", tz="UTC"),
    }]))
    wh.close()
    return path


@pytest.fixture()
def client(db):
    return TestClient(create_app(db))


def _wait_for(client, code, *, until, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/players/{code}/fetch_profile").json()
        if state["state"] in until:
            return state
        time.sleep(0.02)
    raise AssertionError(f"fetch state never reached {until}: {state}")


def test_idle_until_asked(client):
    state = client.get(f"/api/players/{CODE}/fetch_profile").json()
    assert state == {"code": CODE, "state": "idle", "detail": None}


def test_post_starts_the_ingest_and_polling_reaches_done(client, db, monkeypatch):
    calls = []

    def fake_fetch(code, season, db=None, **kw):
        calls.append((code, season, db))
        return {"code": code, "season": season, "understat_id": 8260,
                "understat_name": "Erling Haaland", "resolved_basis": "exact",
                "rows_appended": 2, "rows_total": 2,
                "as_of": "2026-08-31T12:00:00+00:00"}

    monkeypatch.setattr(understat_mod, "fetch_player_profile", fake_fetch)
    r = client.post(f"/api/players/{CODE}/fetch_profile", json={})
    assert r.status_code == 202
    # a fast ingest may legitimately finish before the response is built
    assert r.json()["state"] in {"running", "done"}

    state = _wait_for(client, CODE, until={"done", "error"})
    assert state["state"] == "done"
    assert state["summary"]["understat_id"] == 8260
    assert calls and calls[0][0] == CODE and calls[0][1] == SEASON
    # the route passes ITS OWN warehouse path, not the production default
    assert str(calls[0][2]) == str(db)


def test_a_resolver_refusal_surfaces_verbatim_as_error(client, monkeypatch):
    def refusing(code, season, db=None, **kw):
        raise understat_mod.UnresolvedPlayerError(
            "cannot place 'Haaland': candidates offered: Somebody Else "
            "(Elsewhere, understat id 99). Nothing was written.",
            [{"id": "99", "player": "Somebody Else", "team": "Elsewhere"}],
        )

    monkeypatch.setattr(understat_mod, "fetch_player_profile", refusing)
    assert client.post(f"/api/players/{CODE}/fetch_profile",
                       json={}).status_code == 202
    state = _wait_for(client, CODE, until={"done", "error"})
    assert state["state"] == "error"
    assert "Somebody Else" in state["detail"]
    assert "UnresolvedPlayerError" in state["detail"]


def test_a_second_click_while_running_starts_no_second_fetch(client, monkeypatch):
    release = threading.Event()
    calls = []

    def slow_fetch(code, season, db=None, **kw):
        calls.append(code)
        release.wait(timeout=5)
        return {"code": code, "season": season, "understat_id": 8260,
                "understat_name": "x", "resolved_basis": "exact",
                "rows_appended": 0, "rows_total": 0, "as_of": "x"}

    monkeypatch.setattr(understat_mod, "fetch_player_profile", slow_fetch)
    try:
        r1 = client.post(f"/api/players/{CODE}/fetch_profile", json={})
        r2 = client.post(f"/api/players/{CODE}/fetch_profile", json={})
        assert r1.status_code == r2.status_code == 202
        assert r2.json()["state"] == "running"
        # give a wrongly-spawned second thread a moment to show itself
        time.sleep(0.1)
        assert calls == [CODE]
    finally:
        release.set()
    _wait_for(client, CODE, until={"done"})


def test_the_route_and_the_panel_close_the_loop(client, db, monkeypatch):
    """The drawer's actual sequence: empty panel -> POST -> poll -> panel has
    rows. The fake ingest writes real rows through the real store, so this is
    the whole seam minus the network."""
    empty = client.post("/api/scripts/player_profile/run",
                        json={"code": CODE}).json()["result"]
    assert empty["empty"] and "fetch" in empty["reason"].lower()

    def writing_fetch(code, season, db=None, **kw):
        as_of = dt.datetime(2026, 8, 31, 12, tzinfo=UTC)
        wh = Warehouse(db)
        try:
            store = understat_mod.UnderstatStore(wh)
            store.append("understat_player_match", pd.DataFrame([{
                "understat_id": 8260, "code": code, "season": season,
                "match_id": 1, "date": dt.date(2026, 8, 28), "minutes": 90,
                "shots": 5, "goals": 2, "assists": 0, "key_passes": 0,
                "npg": 2, "xg": 0.69, "xa": 0.0, "npxg": 0.69,
                "position": "FW", "h_team": "Crystal Palace",
                "a_team": "Manchester City", "h_goals": 1, "a_goals": 4,
                "as_of": as_of,
            }]))
        finally:
            wh.close()
        return {"code": code, "season": season, "understat_id": 8260,
                "understat_name": "Erling Haaland", "resolved_basis": "exact",
                "rows_appended": 1, "rows_total": 1,
                "as_of": as_of.isoformat()}

    monkeypatch.setattr(understat_mod, "fetch_player_profile", writing_fetch)
    client.post(f"/api/players/{CODE}/fetch_profile", json={})
    _wait_for(client, CODE, until={"done"})

    filled = client.post("/api/scripts/player_profile/run",
                         json={"code": CODE}).json()["result"]
    assert not filled.get("empty")
    assert filled["totals"]["goals"] == 2
    assert "not FPL points" in filled["note"]
