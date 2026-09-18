"""The two source routes the Creators panel reads and presses.

    GET  /api/content/sources                     every source, one state each
    POST /api/content/sources/{key}/fetch         fetch just this one (202)
    GET  /api/content/sources/{key}/fetch_state   what that fetch got

Nothing here reaches the network: the per-source fetch shells out through
``pipelines.contracts.run_step``, which is exactly the seam that gets stubbed.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from fpl_edge.ingest.content import source_state as ss
from fpl_edge.ingest.content.sources import BY_KEY
from fpl_edge.pipelines import contracts
from fpl_edge.platform.app import create_app
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.sql(
        "CREATE TABLE content_source (source_key VARCHAR PRIMARY KEY, "
        "creator VARCHAR, kind VARCHAR, url VARCHAR, policy VARCHAR, "
        "note VARCHAR, last_probe_utc TIMESTAMPTZ, last_http_status INTEGER, "
        "last_items INTEGER, last_error VARCHAR)")
    wh.sql(
        "CREATE TABLE content_item (item_id VARCHAR PRIMARY KEY, "
        "source_key VARCHAR, creator VARCHAR, kind VARCHAR, title VARCHAR, "
        "url VARCHAR, published_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ, "
        "text_source VARCHAR, text VARCHAR, text_sha256 VARCHAR)")
    now = dt.datetime.now(UTC)
    src = BY_KEY["pod_fplwire"]
    wh.sql("INSERT INTO content_source VALUES (?,?,?,?,?,?,?,?,?,?)",
           ["pod_fplwire", src.creator, str(src.kind), src.url,
            str(src.policy), None, now - dt.timedelta(hours=2), 200, 1, None])
    wh.sql("INSERT INTO content_item VALUES (?,?,?,?,?,?,?,?,?,?,?)",
           ["i1", "pod_fplwire", src.creator, str(src.kind), "an episode",
            "https://example.invalid/e", now - dt.timedelta(hours=3),
            now - dt.timedelta(hours=2), "description", "body", "0" * 64])
    wh.close()
    return path


@pytest.fixture()
def client(db):
    return TestClient(create_app(db))


def _settled(client, key, timeout_s=20.0):
    """Poll fetch_state until the daemon thread reaches a terminal state.

    The route answers 202 immediately and does the work in a thread, exactly
    like the pipeline trigger; the browser polls, and so does this.
    """
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = client.get(f"/api/content/sources/{key}/fetch_state").json()
        if state["state"] in ("done", "error", "idle"):
            return state
        time.sleep(0.05)
    raise AssertionError(f"fetch of {key} never settled: {state}")


# -- GET /api/content/sources ------------------------------------------------


def test_sources_lists_every_source_with_one_state_each(client):
    body = client.get("/api/content/sources").json()
    assert body["window_days"] == 2
    keys = [s["key"] for s in body["sources"]]
    assert len(keys) == len(set(keys))
    assert {s.key for s in BY_KEY.values()} <= set(keys)
    # The vocabulary travels with the data, so the UI never hardcodes it.
    assert {s["state"] for s in body["states"]} == {str(s) for s in ss.SourceState}
    for row in body["sources"]:
        assert row["state"] and row["reason"] and row["label"]
        assert isinstance(row["can_fetch_now"], bool)
    assert sum(body["counts"].values()) == len(body["sources"])


def test_the_word_excluded_is_gone_and_live_feeds_say_so(client):
    body = client.get("/api/content/sources").json()
    by_key = {s["key"]: s for s in body["sources"]}
    assert by_key["pod_fplwire"]["state"] == "live"
    assert by_key["pod_fplwire"]["n_items_window"] == 1
    assert by_key["x_fpl"]["state"] == "blocked"
    assert by_key["reddit_fantasypl"]["state"] == "blocked"
    for row in body["sources"]:
        assert "exclude" not in row["state"]


def test_the_window_is_a_parameter_and_is_echoed_back(client):
    body = client.get("/api/content/sources", params={"window_days": 30}).json()
    assert body["window_days"] == 30
    # Clamped, so a hand-typed 5000 cannot ask for a decade.
    assert client.get("/api/content/sources",
                      params={"window_days": 5000}).json()["window_days"] == 90


# -- POST /api/content/sources/{key}/fetch -----------------------------------


def test_an_unknown_source_key_is_a_404_that_names_the_known_ones(client):
    res = client.post("/api/content/sources/not_a_source/fetch")
    assert res.status_code == 404
    assert "pod_fplwire" in res.json()["detail"]


def test_a_blocked_source_refuses_with_its_recorded_reason(client):
    res = client.post("/api/content/sources/x_fpl/fetch")
    assert res.status_code == 409
    body = res.json()
    assert body["state"] == "blocked"
    assert "nitter" in body["reason"] or "twitter" in body["reason"].lower()


def test_a_fetch_runs_the_ingester_for_exactly_one_source(client, monkeypatch):
    seen: list[list[str]] = []

    def fake_step(name, argv, *, timeout=None):
        seen.append(argv)
        return contracts.Step(name=name, ok=True, seconds=2.0, detail="1 item")

    monkeypatch.setattr(contracts, "run_step", fake_step)

    res = client.post("/api/content/sources/pod_fplwire/fetch",
                      json={"backfill_days": 3})
    assert res.status_code == 202
    body = res.json()
    assert body["started"] is True and body["source_key"] == "pod_fplwire"
    assert body["run_id"] and body["backfill_days"] == 3

    state = _settled(client, "pod_fplwire")
    assert state["state"] == "done"
    assert len(seen) == 1
    argv = seen[0]
    assert "ingest" in argv
    assert argv[argv.index("--only") + 1] == "pod_fplwire"
    assert argv[argv.index("--backfill-days") + 1] == "3"


def test_the_fetch_state_reports_what_the_run_actually_got(client, monkeypatch):
    monkeypatch.setattr(
        contracts, "run_step",
        lambda name, argv, *, timeout=None: contracts.Step(
            name=name, ok=True, seconds=4.0, detail="fetched"))
    client.post("/api/content/sources/pod_fplwire/fetch")
    state = _settled(client, "pod_fplwire")
    assert state["state"] == "done"
    result = state["result"]
    assert result["ok"] is True
    assert result["items_before"] == 1 and result["items_after"] == 1
    assert result["new_items"] == 0
    assert result["http_status"] == 200
    assert result["newest_title"] == "an episode"
    # The refreshed state rides along, so the panel repaints from one response.
    assert state["source"]["key"] == "pod_fplwire"


def test_a_failed_fetch_is_reported_as_an_error_not_a_success(client, monkeypatch):
    monkeypatch.setattr(
        contracts, "run_step",
        lambda name, argv, *, timeout=None: contracts.Step(
            name=name, ok=False, seconds=1.0, detail="HTTP 503 from the feed"))
    client.post("/api/content/sources/pod_fplwire/fetch")
    state = _settled(client, "pod_fplwire")
    assert state["state"] == "error"
    assert state["result"]["ok"] is False
    assert "503" in state["detail"]
    # Both stamps survive the hand-off to the worker thread.
    assert state["started_utc"] and state["finished_utc"]
    assert state["started_utc"] <= state["finished_utc"]


def test_the_backfill_window_is_clamped(client, monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(
        contracts, "run_step",
        lambda name, argv, *, timeout=None: (
            seen.append(argv),
            contracts.Step(name=name, ok=True, seconds=1.0, detail=""))[1])
    client.post("/api/content/sources/pod_fplwire/fetch",
                json={"backfill_days": 9999})
    _settled(client, "pod_fplwire")
    assert seen[0][seen[0].index("--backfill-days") + 1] == "30"


def test_fetch_state_of_an_untouched_source_is_idle(client):
    state = client.get("/api/content/sources/pod_ffhub/fetch_state").json()
    assert state["state"] == "idle" and state["run_id"] is None
    assert state["source"]["key"] == "pod_ffhub"


def test_fetch_state_404s_for_an_unknown_key(client):
    assert client.get("/api/content/sources/nope/fetch_state").status_code == 404
