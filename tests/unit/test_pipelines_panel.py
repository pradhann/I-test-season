"""The pipelines control panel: the two panel scripts and the trigger routes.

What is pinned, and why it is worth pinning:

* ``pipeline_board`` serves exactly the registry (every task, no inventions),
  the declared row shape WITH the per-row run history, and a summary whose
  counts add up to the row set -- the header chips are arithmetic over the
  rows, never a second opinion.
* ``pipeline_run_log`` is path-safe by schema (uuid-hex or a 400 before any
  filesystem touch), serves a bounded tail, and names the gap when a run has
  no log.
* The trigger route: metered-without-confirm costs nothing and returns the
  numbers the confirm strip renders; unknown is 404; disabled and
  already-running are 409; a real trigger returns 202 with the run_id that
  then appears in the ledger with ``trigger='ui'``.
"""

from __future__ import annotations

import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

import fpl_edge.platform.scripts  # noqa: F401 - registration is the import
from fpl_edge.jobs import deadline_dag as dag
from fpl_edge.pipelines import registry, runner
from fpl_edge.platform.app import create_app
from fpl_edge.platform.registry import ParamsInvalid, run_script
from fpl_edge.platform.scripts.pipelines_panel import (
    LOG_TAIL_LINES,
    RUNS_PER_PIPELINE,
)
from fpl_edge.store import Warehouse, fetch_ledger

UTC = dt.UTC


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "pipes.duckdb"
    Warehouse(path).close()
    return path


def _ledger_row(wh, pipeline, *, status="ok", age_h=0.0, credits=0.0,
                duration_s=1.0, trigger="scheduler", note=None):
    rec = fetch_ledger.RunRecord(pipeline)
    rec.started = dt.datetime.now(UTC) - dt.timedelta(hours=age_h)
    rec.finished = rec.started + dt.timedelta(seconds=duration_s)
    rec.credits = credits
    rec.trigger = trigger
    fetch_ledger.record_finished(wh, rec, status=status, note=note)
    return rec.run_id


# -- pipeline_board ----------------------------------------------------------


def test_board_without_a_ledger_is_an_honest_empty(db):
    run = run_script("pipeline_board", db=db)
    assert run.result["empty"] is True
    assert "fetch_run" in run.result["reason"]


def test_board_serves_the_registry_with_history_and_a_true_summary(db):
    with Warehouse(db) as wh:
        _ledger_row(wh, "content_fast_rss", status="ok", age_h=1.0,
                    duration_s=2.0)
        _ledger_row(wh, "content_fast_rss", status="error", age_h=0.5,
                    duration_s=9.0, note="boom")
        _ledger_row(wh, "odds_refresh", status="ok", age_h=2.0, credits=12.0)
    res = run_script("pipeline_board", db=db).result

    # Every registry task, exactly once; the board never invents a row.
    assert {r["id"] for r in res["rows"]} == {t.id for t in registry.TASKS}
    assert res["row_count"] == len(registry.TASKS)

    by_id = {r["id"]: r for r in res["rows"]}
    row = by_id["content_fast_rss"]
    # The pipeline_status contract rides through untouched, plus `runs`.
    assert set(row) == {"id", "description", "family", "schedule", "enabled",
                        "health", "last_run", "avg_duration_ms", "next_due",
                        "metered", "runs"}
    # History is newest-first and carries the sparkline/drawer fields.
    assert [r["status"] for r in row["runs"]] == ["error", "ok"]
    assert set(row["runs"][0]) == {"run_id", "status", "started",
                                   "duration_ms", "rows_written",
                                   "rows_unchanged", "credits", "trigger",
                                   "note"}
    assert row["runs"][1]["duration_ms"] == pytest.approx(2000.0, rel=0.01)
    assert row["health"]["state"] == "failing"
    assert row["health"]["reason"]          # the reason is the product

    # The summary is arithmetic over the rows, never a second opinion.
    s = res["summary"]
    states = [r["health"]["state"] for r in res["rows"]]
    assert s["n_failing"] == states.count("failing")
    assert s["n_ok"] == states.count("ok")
    assert s["n_never_ran"] == states.count("never_ran")
    assert (s["n_ok"] + s["n_failing"] + s["n_stale"] + s["n_never_ran"]
            + s["n_running"] + s["n_disabled"]) == len(res["rows"])
    assert s["month_credits"] == pytest.approx(12.0)
    assert s["month_credits_cap"] == 500.0
    # The family order is served so the view never invents a sort.
    assert set(res["families"]) == {t.family for t in registry.TASKS}


def test_board_history_is_capped_per_pipeline(db):
    with Warehouse(db) as wh:
        for i in range(RUNS_PER_PIPELINE + 3):
            _ledger_row(wh, "post_gw_settlement", age_h=float(i))
    res = run_script("pipeline_board", db=db).result
    row = next(r for r in res["rows"] if r["id"] == "post_gw_settlement")
    assert len(row["runs"]) == RUNS_PER_PIPELINE


# -- pipeline_run_log --------------------------------------------------------


def test_run_log_serves_a_bounded_tail(db, tmp_path):
    rid = "ab" * 16
    log_dir = tmp_path / "pipeline_logs"
    log_dir.mkdir()
    (log_dir / f"{rid}.log").write_text(
        "\n".join(f"line {i}" for i in range(LOG_TAIL_LINES + 50)))
    res = run_script("pipeline_run_log", {"run_id": rid}, db=db).result
    assert res["found"] is True
    assert len(res["lines"]) == LOG_TAIL_LINES
    assert res["lines"][-1] == f"line {LOG_TAIL_LINES + 49}"
    assert res["truncated"] is True
    assert res["n_lines_total"] == LOG_TAIL_LINES + 50


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "ab" * 15, "AB" * 16, "abc", "a" * 33, "",
    "ab" * 15 + "/.",
])
def test_run_log_refuses_anything_but_uuid_hex(db, bad):
    with pytest.raises(ParamsInvalid):
        run_script("pipeline_run_log", {"run_id": bad}, db=db)


def test_run_log_refuses_over_http_with_a_400(db):
    client = TestClient(create_app(db))
    r = client.post("/api/scripts/pipeline_run_log/run",
                    json={"run_id": "../../../etc/passwd"})
    assert r.status_code == 400


def test_a_run_without_a_log_is_a_named_gap(db):
    res = run_script("pipeline_run_log", {"run_id": "cd" * 16}, db=db).result
    assert res["found"] is False
    assert res["lines"] == []
    assert "No log file" in res["reason"]


# -- the trigger routes ------------------------------------------------------


def stub_task(run, *, task_id="stub_calendar", **kw):
    return registry.Task(
        id=task_id, description="a test stub",
        due=kw.pop("due", registry.Calendar(hour_utc=0)),
        stale_window=dt.timedelta(hours=26), run=run, **kw,
    )


def quiet_run(**result_kw):
    def run(ctx):
        print("hello from the trigger")
        return dag.TaskResult(outcome="quiet", detail="stub ran", **result_kw)
    return run


def test_an_unknown_pipeline_is_a_404(db):
    client = TestClient(create_app(db))
    r = client.post("/api/pipelines/nope/run", json={})
    assert r.status_code == 404
    assert client.get("/api/pipelines/nope/run_state").status_code == 404


def test_a_disabled_pipeline_is_a_409_with_a_reason(db, monkeypatch):
    monkeypatch.setattr(registry, "TASKS",
                        (stub_task(quiet_run(), task_id="off", enabled=False),))
    client = TestClient(create_app(db))
    r = client.post("/api/pipelines/off/run", json={})
    assert r.status_code == 409
    assert "disabled" in r.json()["detail"]


def test_metered_without_confirm_quotes_cost_and_runs_nothing(db):
    with Warehouse(db) as wh:
        _ledger_row(wh, "odds_refresh", credits=42.0)
    client = TestClient(create_app(db))
    r = client.post("/api/pipelines/odds_refresh/run", json={"confirm": False})
    assert r.status_code == 200
    body = r.json()
    assert body["needs_confirm"] is True
    assert body["credits_estimate"] == 12.0
    assert body["month_spend"] == pytest.approx(42.0)
    assert body["month_cap"] == 500.0
    # nothing started, nothing spent: the ledger still holds exactly one row
    with Warehouse(db) as wh:
        assert len(wh.sql("SELECT * FROM fetch_run")) == 1
    assert client.get("/api/pipelines/odds_refresh/run_state").json()["state"] == "idle"


def test_a_second_trigger_while_running_is_a_409(db, monkeypatch):
    monkeypatch.setattr(registry, "TASKS", (stub_task(quiet_run()),))
    app = create_app(db)
    app.state.pipeline_runs["stub_calendar"] = {
        "task_id": "stub_calendar", "state": "running", "detail": None,
        "run_id": "aa" * 16,
    }
    client = TestClient(app)
    r = client.post("/api/pipelines/stub_calendar/run", json={})
    assert r.status_code == 409
    assert "running" in r.json()["detail"]


def test_a_trigger_runs_records_ui_and_the_poller_sees_it(
        db, monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "TASKS",
                        (stub_task(quiet_run(ledger_written=5)),))
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")
    client = TestClient(create_app(db))

    r = client.post("/api/pipelines/stub_calendar/run", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["started"] is True
    run_id = body["run_id"]
    assert len(run_id) == 32

    # Poll exactly as the view does, until terminal.
    for _ in range(100):
        st = client.get("/api/pipelines/stub_calendar/run_state").json()
        if st["state"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert st["state"] == "done"
    assert st["run_id"] == run_id
    assert st["last_run"]["run_id"] == run_id
    assert st["last_run"]["status"] == "ok"
    assert st["last_run"]["trigger"] == "ui"
    assert st["last_run"]["rows_written"] == 5

    with Warehouse(db) as wh:
        row = wh.sql("SELECT * FROM fetch_run WHERE run_id = ?",
                     [run_id]).iloc[0]
    assert row["trigger"] == "ui"
    # and the 202's run_id joins to the captured log file
    log = runner.log_path_for(run_id, log_dir=tmp_path / "logs")
    assert log.exists() and "hello from the trigger" in log.read_text()
