"""The v1 HTTP contract (DESIGN.md §2.1), exercised end to end offline.

Every test runs against a temporary warehouse seeded in-process, so the suite
never touches the real database and never takes its write lock -- which matters
because the live Telegram bot holds leases on it while these tests run.
"""

from __future__ import annotations

import base64
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fpl_edge.platform.scripts  # noqa: F401  (registers the panel scripts)
from fpl_edge.jobs import outbox
from fpl_edge.platform.app import create_app
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc


@pytest.fixture()
def db(tmp_path):
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


@pytest.fixture()
def client(db):
    return TestClient(create_app(db))


# -- GET /api/panels ---------------------------------------------------------


def test_panels_lists_every_panel_with_its_pinned_script(client):
    body = client.get("/api/panels").json()
    assert {p["id"] for p in body["panels"]} == {
        "squad", "projections", "fixtures", "prices", "ideas", "market"}
    for panel in body["panels"]:
        assert panel["script"], f"{panel['id']} pins no script"
        assert panel["params_schema"]["type"] == "object"
        assert panel["result_schema"]
    assert body["repo_sha"]


def test_every_pinned_script_actually_exists(client):
    body = client.get("/api/panels").json()
    known = {s["name"] for s in body["scripts"]}
    assert {p["script"] for p in body["panels"]} <= known


# -- POST /api/scripts/{name}/run --------------------------------------------


def test_running_a_script_returns_result_and_provenance(client):
    r = client.post("/api/scripts/fixture_ticker/run", json={"horizon": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["result"]["row_count"] == 2
    assert body["provenance"]["script"] == "fixture_ticker"
    assert body["provenance"]["repo_sha"]
    assert body["performance"] == "ok"
    assert isinstance(body["duration_ms"], int)


def test_running_a_script_with_no_body_uses_defaults(client):
    r = client.post("/api/scripts/fixture_ticker/run")
    assert r.status_code == 200
    assert r.json()["provenance"]["params"]["horizon"] == 5


def test_params_may_be_sent_at_the_top_level_or_nested(client):
    flat = client.post("/api/scripts/fixture_ticker/run", json={"horizon": 2}).json()
    nested = client.post("/api/scripts/fixture_ticker/run",
                         json={"params": {"horizon": 2}}).json()
    assert flat["result"]["gws"] == nested["result"]["gws"] == [1, 2]


def test_invalid_params_are_a_400_naming_the_field(client):
    r = client.post("/api/scripts/fixture_ticker/run", json={"horizon": 99})
    assert r.status_code == 400
    assert "horizon" in r.json()["detail"]


def test_an_unknown_script_is_a_404(client):
    r = client.post("/api/scripts/nope/run", json={})
    assert r.status_code == 404


def test_an_empty_panel_is_a_200_with_a_reason_not_an_error(client):
    """A panel with no data is a normal response. Returning 4xx/5xx would make
    'nothing solved yet' indistinguishable from a broken server."""
    r = client.post("/api/scripts/projection_table/run", json={})
    assert r.status_code == 200
    assert r.json()["result"]["empty"] is True
    assert r.json()["result"]["reason"]


# -- POST /api/query ---------------------------------------------------------


def test_query_returns_rows_and_columns(client):
    r = client.post("/api/query", json={"sql": "SELECT short_name FROM dim_team ORDER BY 1"})
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == ["short_name"]
    assert [row["short_name"] for row in body["rows"]] == ["ARS", "CHE"]
    assert body["provenance"]["repo_sha"]


@pytest.mark.parametrize("sql", [
    "DELETE FROM dim_team",
    "UPDATE dim_team SET short_name = 'X'",
    "DROP TABLE dim_team",
    "SELECT 1; DROP TABLE dim_team",
    "ATTACH '/tmp/x.db' AS x",
])
def test_query_refuses_writes_and_multi_statements(client, sql):
    r = client.post("/api/query", json={"sql": sql})
    assert r.status_code == 400


def test_query_enforces_the_row_cap(client):
    r = client.post("/api/query",
                    json={"sql": "SELECT * FROM range(500) t(i)", "max_rows": 5})
    body = r.json()
    assert body["row_count"] == 5 and body["truncated"] is True


def test_query_accepts_as_of_and_reports_it(client):
    r = client.post("/api/query", json={
        "sql": "SELECT count(*) AS n FROM dim_team",
        "as_of": "2026-07-01T00:00:00+00:00",
    })
    body = r.json()
    # The clubs were recorded on 1 Aug, so as of 1 July there are none.
    assert body["rows"] == [{"n": 0}]
    assert body["as_of"].startswith("2026-07-01")


def test_a_sql_error_is_a_400_carrying_the_message(client):
    r = client.post("/api/query", json={"sql": "SELECT nonexistent_column FROM dim_team"})
    assert r.status_code == 400
    assert "nonexistent_column" in r.json()["detail"]


def test_the_warehouse_write_lock_is_free_during_a_query(client, db):
    """The decisive property: a second writer can open the file while the
    server is serving. If reads stopped going through a copy, this deadlocks."""
    client.post("/api/query", json={"sql": "SELECT 1"})
    other = Warehouse(db)          # would raise WarehouseLockedError if held
    other.close()


# -- GET /api/inbox, POST /api/inbox/{id}/ack --------------------------------


def test_inbox_is_empty_and_explains_itself_before_any_delivery(client):
    body = client.get("/api/inbox").json()
    assert body["deliveries"] == [] and body["empty"] is True
    assert body["reason"]


def test_inbox_lists_a_delivery_and_ack_marks_it(db):
    wh = Warehouse(db)
    did = outbox.deliver(
        wh, monitor="price_radar", kind="alert", title="Salah rising",
        body="net 40k/h", now=dt.datetime(2026, 8, 2, 2, 0, tzinfo=UTC),
    )
    wh.close()

    client = TestClient(create_app(db))
    body = client.get("/api/inbox").json()
    assert [d["id"] for d in body["deliveries"]] == [did]
    assert body["deliveries"][0]["title"] == "Salah rising"
    assert body["deliveries"][0]["acked"] is False
    assert body["unacked"] == 1

    r = client.post(f"/api/inbox/{did}/ack")
    assert r.status_code == 200 and r.json()["acked"] is True

    after = client.get("/api/inbox").json()
    assert after["deliveries"] == [] and after["unacked"] == 0
    # Acking hides it from the feed but does not delete it.
    kept = client.get("/api/inbox", params={"include_acked": True}).json()
    assert [d["id"] for d in kept["deliveries"]] == [did]


def test_inbox_is_newest_first(db):
    wh = Warehouse(db)
    old = outbox.deliver(wh, monitor="m", kind="alert", title="older", body="b",
                         now=dt.datetime(2026, 8, 1, tzinfo=UTC))
    new = outbox.deliver(wh, monitor="m", kind="alert", title="newer", body="b",
                         now=dt.datetime(2026, 8, 3, tzinfo=UTC))
    wh.close()
    body = TestClient(create_app(db)).get("/api/inbox").json()
    assert [d["id"] for d in body["deliveries"]] == [new, old]


def test_acking_an_unknown_delivery_is_a_404(client):
    assert client.post("/api/inbox/does-not-exist/ack").status_code == 404


def test_the_platform_creates_no_second_delivery_table(db, client):
    """One outbox, owned by the jobs package. If the platform ever created its
    own table this fails, and the Inbox and Telegram would drift apart."""
    client.get("/api/inbox")
    wh = Warehouse(db)
    tables = set(wh.sql(
        "SELECT table_name FROM information_schema.tables")["table_name"])
    wh.close()
    assert not {t for t in tables if "deliver" in t.lower()} - {"platform_delivery"}


# -- GET /api/monitors -------------------------------------------------------


def test_monitors_are_read_from_the_dag(client):
    body = client.get("/api/monitors").json()
    names = {m["name"] for m in body["monitors"]}
    assert {"presser_projection_refresh", "final_solve_delivery",
            "lineup_captain_check", "price_radar"} <= names
    for m in body["monitors"]:
        assert m["schedule"] and m["kind"]


def test_manual_monitor_run_is_refused_with_an_explanation(client):
    r = client.post("/api/monitors/price_radar/run")
    assert r.status_code == 501
    assert "deadline_dag" in r.json()["detail"]


# -- POST /api/chat ----------------------------------------------------------


def test_chat_routes_through_the_question_router(client, monkeypatch):
    """The web pane and Telegram must answer the same question the same way,
    so the route reuses QuestionRouter rather than reimplementing intents."""
    from fpl_edge.interfaces.qa import Answer, QuestionRouter

    png = b"\x89PNG\r\n\x1a\n-fake"
    monkeypatch.setattr(
        QuestionRouter, "route",
        lambda self, text: Answer("top defenders: Gabriel", images=[("top.png", png)]),
    )
    body = client.post("/api/chat", json={"text": "which defenders have the highest xpoints"}).json()
    assert body["routed"] is True
    assert body["text"] == "top defenders: Gabriel"
    assert body["intent"] == "top_by_position"
    assert base64.b64decode(body["images"][0]["base64"]) == png
    assert body["images"][0]["mime"] == "image/png"


def test_chat_does_not_guess_at_an_unrouted_message(client, monkeypatch):
    """A message the router does not understand gets an honest miss, not an
    invented answer -- in Telegram this same message becomes an idea."""
    from fpl_edge.interfaces.qa import QuestionRouter

    monkeypatch.setattr(QuestionRouter, "route", lambda self, text: None)
    body = client.post("/api/chat", json={"text": "I like Rashford"}).json()
    assert body["routed"] is False
    assert body["images"] == []
    assert body["escalation_available"] is True


def test_chat_reports_a_handler_failure_rather_than_crashing(client, monkeypatch):
    from fpl_edge.interfaces.qa import QuestionRouter

    def boom(self, text):
        raise RuntimeError("warehouse went away")

    monkeypatch.setattr(QuestionRouter, "route", boom)
    body = client.post("/api/chat", json={"text": "review my team"}).json()
    assert body["routed"] is False
    assert "warehouse went away" in body["text"]


def test_chat_requires_text(client):
    assert client.post("/api/chat", json={"text": "  "}).status_code == 400


# -- static / health ---------------------------------------------------------


def test_root_explains_itself_when_no_bundle_is_built(tmp_path, monkeypatch, db):
    """With no built UI, / must still explain the API rather than 404.

    Pinned against a bundle-less path rather than the repo's own web/dist:
    once a bundle exists the mount takes over, and a test that asserted the
    explanation from repo state started failing the moment the UI shipped.
    """
    import fpl_edge.platform.app as app_module

    monkeypatch.setattr(app_module, "WEB_DIST", tmp_path / "absent")
    bare = TestClient(app_module.create_app(db=db))
    body = bare.get("/").json()
    assert body["ok"] is True
    assert "/api/panels" in body["detail"]


def test_root_serves_the_bundle_when_one_is_built(tmp_path, monkeypatch, db):
    """And when a bundle IS present, / serves it."""
    import fpl_edge.platform.app as app_module

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>i-test</title>")
    monkeypatch.setattr(app_module, "WEB_DIST", dist)
    built = TestClient(app_module.create_app(db=db))
    resp = built.get("/")
    assert resp.status_code == 200
    assert "i-test" in resp.text


def test_health_reports_the_warehouse_and_sha(client, db):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["warehouse"] == str(db) and body["warehouse_present"] is True
    assert body["repo_sha"]
