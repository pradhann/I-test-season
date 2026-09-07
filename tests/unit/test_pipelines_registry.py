"""The pipelines package: registry invariants, runner capture, derived health.

Break-watch-restored during the build for each pinned behaviour:

* the registry rejects a duplicate id at validation (watched fail with two
  rows sharing an id);
* a deadline-relative registry rule computes EXACTLY the instants the DAG's
  own due_tasks computes -- the parity that lets the registry own identity
  while the DAG keeps byte-identical scheduling;
* a calendar registry task rides the DAG tick's existing machinery: one
  dag_firing row, idempotent re-tick, one fetch_run ledger row with the
  trigger, and a captured log file;
* the network kill-switch (FPL_EDGE_DISABLE_NETWORK_INGEST) turns every
  fetching registry task into an honest no_source without running a step.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.jobs import deadline_dag as dag
from fpl_edge.jobs import post_gw
from fpl_edge.pipelines import health, registry, runner
from fpl_edge.store import Warehouse, fetch_ledger

UTC = dt.UTC
SEASON = "2026-27"
GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
DEADLINES = [(1, GW1)]


def seed(db_path):
    with Warehouse(db_path) as wh:
        wh.sql(
            "INSERT INTO dim_event (season, gw, deadline_utc, is_finished, as_of) "
            "VALUES (?, 1, ?, FALSE, ?)",
            [SEASON, GW1, dt.datetime(2026, 8, 1, tzinfo=UTC)],
        )
    return db_path


@pytest.fixture()
def db(tmp_path):
    return seed(tmp_path / "reg.duckdb")


def stub_task(run, *, task_id="stub_calendar", due=None,
              window=dt.timedelta(hours=26), **kw):
    return registry.Task(
        id=task_id, description="a test stub",
        due=due or registry.Calendar(hour_utc=0),
        stale_window=window, run=run, **kw,
    )


def quiet_run(detail="stub ran", **result_kw):
    calls = []

    def run(ctx):
        calls.append(ctx)
        print("hello from the stub")
        return dag.TaskResult(outcome="quiet", detail=detail, **result_kw)

    return run, calls


# -- registry invariants -----------------------------------------------------


def test_the_real_registry_validates_and_ids_are_unique():
    registry.validate(registry.TASKS)
    ids = [t.id for t in registry.TASKS]
    assert len(ids) == len(set(ids))


def test_a_duplicate_id_is_rejected():
    run, _ = quiet_run()
    a = stub_task(run, task_id="twice")
    b = stub_task(run, task_id="twice")
    with pytest.raises(ValueError, match="duplicate task id"):
        registry.validate([a, b])


def test_an_empty_id_is_rejected():
    run, _ = quiet_run()
    with pytest.raises(ValueError, match="empty id"):
        registry.validate([stub_task(run, task_id="  ")])


def test_the_legacy_five_are_present_and_dag_scheduled():
    legacy = {t.id for t in registry.TASKS if t.scheduled_by_dag}
    assert legacy == {"presser_projection_refresh", "price_radar",
                      "final_solve_delivery", "lineup_captain_check",
                      "odds_refresh"}
    # and their stale windows are THE dag windows, not copies that can drift
    for t in registry.TASKS:
        if t.scheduled_by_dag:
            assert t.stale_window == dag.STALE_WINDOWS[t.id]


def test_the_folded_in_pipelines_are_registered():
    ids = {t.id for t in registry.TASKS}
    assert {"post_gw_settlement", "fpl_core_insights", "content_transcribe",
            "content_fast_rss", "audio_retention", "panel_picks_crawl"} <= ids


def test_the_panel_picks_crawl_runs_shortly_after_settlement():
    """The panel crawl reads the roster settlement leaves behind and feeds the
    Creators page, so it fires after the 10:30 settlement slot, and with the
    23h window the lid-down tick can still run it the same day."""
    settle = registry.by_id("post_gw_settlement")
    panel = registry.by_id("panel_picks_crawl")
    assert panel is not None and settle is not None
    assert isinstance(panel.due, registry.Calendar)
    assert (panel.due.hour_utc, panel.due.minute) > (settle.due.hour_utc,
                                                     settle.due.minute)
    assert panel.stale_window == dt.timedelta(hours=23)
    assert panel.family == "settlement"
    assert panel.enabled


# -- due arithmetic and the deadline-relative parity -------------------------


def test_deadline_relative_parity_with_the_dag():
    """The registry's instants for a deadline-relative rule must equal the
    DAG's own, or promoting the registry would silently move a firing."""
    run, _ = quiet_run()
    now = GW1 - dt.timedelta(hours=29)  # one hour after the T-30h instant
    for task_id in ("presser_projection_refresh", "final_solve_delivery",
                    "lineup_captain_check"):
        task = stub_task(
            run, task_id=task_id,
            due=registry.DeadlineRelative(
                hours_before=dag.DEADLINE_OFFSETS[task_id].total_seconds() / 3600
            ),
        )
        got = registry.due_instants(task, DEADLINES, now)
        want = [(d.gw, d.due_utc, d.deadline_utc)
                for d in dag.due_tasks(DEADLINES, now) if d.task == task_id]
        assert got == want


def test_the_odds_ladder_parity():
    run, _ = quiet_run()
    now = GW1 - dt.timedelta(hours=4)  # T-36h and T-12h and T-5h all owed
    task = stub_task(run, task_id="odds_refresh",
                     due=registry.DeadlineRelative(hours_before=(36.0, 12.0, 5.0)))
    got = sorted(inst for _, inst, _ in registry.due_instants(task, DEADLINES, now))
    want = sorted(d.due_utc for d in dag.due_tasks(DEADLINES, now)
                  if d.task == "odds_refresh")
    assert got == want


def test_registry_due_never_double_schedules_a_dag_task():
    now = GW1 - dt.timedelta(hours=29)
    owed = registry.registry_due(DEADLINES, now, season=SEASON)
    legacy = {t.id for t in registry.TASKS if t.scheduled_by_dag}
    assert not [d for d in owed if d.task in legacy]


def test_interval_instants_are_epoch_aligned():
    run, _ = quiet_run()
    task = stub_task(run, due=registry.Interval(hours=4))
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    got = registry.due_instants(task, [], now, lookback=dt.timedelta(hours=6))
    instants = [inst for _, inst, _ in got]
    assert instants == [dt.datetime(2026, 8, 19, 20, 0, tzinfo=UTC),
                        dt.datetime(2026, 8, 20, 0, 0, tzinfo=UTC)]
    epoch = dt.datetime(1970, 1, 1, tzinfo=UTC)
    for inst in instants:
        assert ((inst - epoch).total_seconds() / 3600) % 4 == 0
    # and every calendar/interval firing carries the NO_GW sentinel
    assert all(gw == registry.NO_GW for gw, _, _ in got)


def test_calendar_local_walk_matches_the_dag_nightly_walk():
    """02:00 Europe/London via the registry == the DAG's nightly_instants,
    DST arithmetic included."""
    run, _ = quiet_run()
    task = stub_task(run, due=registry.Calendar(hour_local=2, tz="Europe/London"))
    for now in (dt.datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
                dt.datetime(2026, 12, 10, 6, 0, tzinfo=UTC),
                dt.datetime(2027, 3, 28, 12, 0, tzinfo=UTC),
                dt.datetime(2026, 10, 25, 12, 0, tzinfo=UTC)):
        got = [inst for _, inst, _ in registry.due_instants(
            task, [], now, lookback=dt.timedelta(hours=12))]
        assert got == dag.nightly_instants(now, lookback=dt.timedelta(hours=12))


def test_calendar_shape_is_validated():
    with pytest.raises(ValueError):
        registry.Calendar()
    with pytest.raises(ValueError):
        registry.Calendar(hour_utc=1, hour_local=2, tz="Europe/London")
    with pytest.raises(ValueError):
        registry.Calendar(hour_local=2)  # no tz


def test_on_demand_is_never_owed():
    run, _ = quiet_run()
    task = stub_task(run, due=registry.OnDemand())
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    assert registry.due_instants(task, DEADLINES, now) == []


def test_stale_window_for_consults_the_registry():
    assert dag.stale_window_for("content_fast_rss") == dt.timedelta(hours=3)
    assert dag.stale_window_for("price_radar") == dag.STALE_WINDOWS["price_radar"]
    assert dag.stale_window_for("no_such_task") == dag.STALE_WINDOW


# -- the tick runs registry tasks with its existing machinery ----------------


def test_the_tick_fires_a_registry_calendar_task_once(db, monkeypatch, tmp_path):
    run, calls = quiet_run(ledger_written=3)
    # 20h window: yesterday's 00:00 firing (25h old, inside the 36h lookback)
    # is recorded skipped_stale; only today's runs.
    stub = stub_task(run, window=dt.timedelta(hours=20))
    monkeypatch.setattr(registry, "TASKS", (stub,))
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)

    dag.tick(now=now, season=SEASON, db_path=db, send=False, polish=False)
    assert len(calls) == 1
    with Warehouse(db) as wh:
        firing = wh.sql(
            "SELECT gw, due_utc, outcome FROM dag_firing "
            "WHERE task = 'stub_calendar' AND outcome = 'quiet'"
        )
        ledger = wh.sql("SELECT * FROM fetch_run WHERE pipeline = 'stub_calendar'")
    assert len(firing) == 1
    assert int(firing.iloc[0]["gw"]) == registry.NO_GW
    assert firing.iloc[0]["due_utc"].to_pydatetime() == dt.datetime(
        2026, 8, 20, 0, 0, tzinfo=UTC)
    assert len(ledger) == 1
    row = ledger.iloc[0]
    assert row["status"] == "ok"
    assert row["trigger"] == "scheduler"
    assert int(row["rows_written"]) == 3
    log = runner.log_path_for(str(row["run_id"]), log_dir=tmp_path / "logs")
    assert log.exists() and "hello from the stub" in log.read_text()

    # Idempotency: the identical tick claims nothing and runs nothing again.
    dag.tick(now=now, season=SEASON, db_path=db, send=False, polish=False)
    assert len(calls) == 1
    with Warehouse(db) as wh:
        assert len(wh.sql(
            "SELECT * FROM dag_firing WHERE task = 'stub_calendar' "
            "AND outcome = 'quiet'")) == 1
        assert len(wh.sql(
            "SELECT * FROM fetch_run WHERE pipeline = 'stub_calendar'")) == 1


def test_a_raising_registry_task_is_an_error_row_with_a_log_tail(
        db, monkeypatch, tmp_path):
    def boom(ctx):
        print("about to explode")
        raise RuntimeError("pipeline exploded")

    monkeypatch.setattr(registry, "TASKS", (stub_task(boom),))
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)

    dag.tick(now=now, season=SEASON, db_path=db, send=False, polish=False)
    with Warehouse(db) as wh:
        firing = wh.sql(
            "SELECT outcome, detail FROM dag_firing WHERE task = 'stub_calendar'")
        ledger = wh.sql(
            "SELECT status, note FROM fetch_run WHERE pipeline = 'stub_calendar'")
    assert firing.iloc[0]["outcome"] == "error"
    assert "pipeline exploded" in firing.iloc[0]["detail"]
    assert ledger.iloc[0]["status"] == "error"
    assert "--- log tail ---" in ledger.iloc[0]["note"]


def test_a_stale_registry_firing_is_recorded_never_run(db, monkeypatch, tmp_path):
    run, calls = quiet_run()
    stub = stub_task(run, window=dt.timedelta(minutes=30))
    monkeypatch.setattr(registry, "TASKS", (stub,))
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(dag, "PLAN_DIR", tmp_path)
    now = dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)  # 00:00 firing is 65m old

    dag.tick(now=now, season=SEASON, db_path=db, send=False, polish=False)
    assert calls == []
    with Warehouse(db) as wh:
        firing = wh.sql(
            "SELECT outcome FROM dag_firing WHERE task = 'stub_calendar'")
        ledger = wh.sql("SELECT * FROM fetch_run WHERE pipeline = 'stub_calendar'")
    assert len(firing) >= 1
    assert set(firing["outcome"]) == {"skipped_stale"}
    assert ledger.empty  # a skip is not a run; the ledger stays honest


# -- the network kill-switch -------------------------------------------------


def _ctx(tmp_path, now=None):
    now = now or dt.datetime(2026, 8, 20, 1, 5, tzinfo=UTC)
    return dag.TaskContext(season=SEASON, gw=0, due_utc=now, deadline_utc=None,
                           now=now, db_path=tmp_path / "gate.duckdb")


@pytest.mark.parametrize("fn", [
    registry.run_post_gw_settlement,
    registry.run_transcribe_nightly,
    registry.run_fpl_core_insights,
    registry.run_fast_rss,
    registry.run_panel_picks_crawl,
])
def test_fetching_tasks_honour_the_network_kill_switch(fn, tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "1")

    def forbidden(*a, **k):  # no step may even be constructed
        raise AssertionError("a gated task tried to run a step")

    monkeypatch.setattr(dag, "run_step", forbidden)
    monkeypatch.setattr(registry, "run_step", forbidden)
    monkeypatch.setattr(post_gw, "_run", forbidden)
    res = fn(_ctx(tmp_path))
    assert res.outcome == "no_source"
    assert "FPL_EDGE_DISABLE_NETWORK_INGEST" in res.detail


def test_post_gw_cli_and_registry_run_the_same_step_list(tmp_path, monkeypatch):
    """The parity guarantee: one list, two callers."""
    steps = post_gw.settlement_steps("python")
    names = [name for name, _ in steps]
    assert names[0] == "ingest_live"
    assert names.index("settle_results") < names.index("score_projections")
    assert names.index("settle_results") < names.index("crawl_elite")
    # The panel crawl is a settlement step too, after the cohort crawls
    # whose coincidental coverage it exists to replace.
    assert names.index("crawl_elite_named") < names.index("crawl_panel")
    assert names[-3:] == ["intel", "retro_report", "weekly_idea_report"]

    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    ran: list[str] = []

    def fake_run(report, name, argv):
        ran.append(name)
        report.steps.append(post_gw.StepResult(name=name, ok=True, seconds=0.0))

    monkeypatch.setattr(post_gw, "_run", fake_run)
    res = registry.run_post_gw_settlement(_ctx(tmp_path))
    assert ran == names
    assert res.outcome == "quiet"  # a clean settlement enqueues nothing


def test_post_gw_failure_becomes_the_same_titled_alert(tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")

    def fake_run(report, name, argv):
        report.steps.append(post_gw.StepResult(
            name=name, ok=(name != "settle_results"), seconds=0.0,
            detail="boom" if name == "settle_results" else ""))

    monkeypatch.setattr(post_gw, "_run", fake_run)
    res = registry.run_post_gw_settlement(_ctx(tmp_path))
    # ``error``, not ``delivered``: the alert still goes out (``delivers`` is
    # true for both), but only ``error`` reaches the ledger as a failure.
    assert res.outcome == "error" and res.kind == "alert"
    assert res.delivers is True
    assert "settle_results" in res.title and res.title.startswith("post_gw FAILED")


# -- failure honesty: a failed step is never an ok ledger row ----------------
#
# The defect this pins, measured 2026-09-03: `run_transcribe_nightly` returned
# ``delivered`` when its step failed, `runner.LEDGER_STATUS` maps ``delivered``
# to ``ok``, and three consecutive nightly failures (09-01, 09-02, 09-03) sit
# in ``fetch_run`` with status ``ok`` and a note beginning "delivered:
# transcribe failed:". The Pipelines panel showed green for a week.
#
# Written as a sweep over EVERY subprocess-shaped registry task rather than a
# test of the one that was broken, because the bug was a copy of a pattern and
# the next copy would be just as silent.

_SUBPROCESS_TASKS = (
    "post_gw_settlement",
    "fpl_core_insights",
    "content_transcribe",
    "content_fast_rss",
    "content_analyse",
    "content_analyse_backlog",
)


@pytest.mark.parametrize("task_id", _SUBPROCESS_TASKS)
def test_a_failing_step_never_lands_as_an_ok_ledger_row(
        task_id, tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")

    def failing_step(name, argv, *, timeout=None):
        return dag.Step(name=name, ok=False, seconds=1.0,
                        detail="boom: the step exited non-zero")

    monkeypatch.setattr(registry, "run_step", failing_step)

    def failing_post_gw(report, name, argv):
        report.steps.append(post_gw.StepResult(
            name=name, ok=False, seconds=0.0, detail="boom"))

    monkeypatch.setattr(post_gw, "_run", failing_post_gw)

    db = tmp_path / f"{task_id}.duckdb"
    Warehouse(db).close()
    out = runner.run_task(task_id, db_path=db, trigger="cli")

    assert out.result.outcome == "error", (
        f"{task_id} reported {out.result.outcome!r} for a failed step")
    assert out.record.status == "error"

    with Warehouse(db) as wh:
        rows = wh.sql("SELECT status, note FROM fetch_run WHERE pipeline = ?",
                      [task_id])
    assert len(rows) == 1
    assert rows.iloc[0]["status"] == "error"
    assert not rows.iloc[0]["note"].startswith("delivered:")


@pytest.mark.parametrize("task_id", _SUBPROCESS_TASKS)
def test_a_clean_step_still_lands_as_ok(task_id, tmp_path, monkeypatch):
    """The other half: the fix must not turn healthy runs red."""
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(
        registry, "run_step",
        lambda name, argv, *, timeout=None: dag.Step(
            name=name, ok=True, seconds=1.0, detail="fine"))
    monkeypatch.setattr(
        post_gw, "_run",
        lambda report, name, argv: report.steps.append(
            post_gw.StepResult(name=name, ok=True, seconds=0.0)))

    db = tmp_path / f"{task_id}-ok.duckdb"
    Warehouse(db).close()
    out = runner.run_task(task_id, db_path=db, trigger="cli")
    assert out.record.status == "ok"
    assert out.result.delivers is False


def test_an_error_result_still_delivers_its_alert():
    """Honesty in the ledger must not cost the notification."""
    res = dag.TaskResult(outcome="error", kind="alert", title="X FAILED",
                         body="why")
    assert res.delivers is True
    assert dag.TaskResult(outcome="error", detail="no title").delivers is False


# -- the analyse rung --------------------------------------------------------


def test_content_analyse_is_registered_twice_and_budgeted():
    """The missing rung. Two firings a day, both bounded, both UI-triggerable."""
    daily = registry.by_id("content_analyse")
    backlog = registry.by_id("content_analyse_backlog")
    assert daily is not None and backlog is not None
    for task in (daily, backlog):
        assert task.family == "content"
        assert task.enabled and not task.scheduled_by_dag
        assert task.budget_s == registry.ANALYSE_BUDGET_S
        # Free: the analysis backend is the Max-plan CLI, so a UI trigger
        # must not be gated behind a credit confirm nobody can satisfy.
        assert task.credits_estimate == 0.0 and task.confirm_required is False
        assert registry.runner_for(task.id) is not None
    # After the 12:00 transcription slot, and again 12 hours later.
    assert isinstance(daily.due, registry.Calendar)
    assert (daily.due.hour_utc, daily.due.minute) == (13, 30)
    assert (backlog.due.hour_utc, backlog.due.minute) == (1, 30)
    transcribe = registry.by_id("content_transcribe")
    assert transcribe.due.hour_utc < daily.due.hour_utc


def test_the_analyse_step_is_budgeted_and_resumable(tmp_path, monkeypatch):
    """Budget goes to the command; no --limit, so the queue is the resume."""
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    monkeypatch.setenv("FPL_EDGE_ANALYSE_BUDGET_S", "123")
    seen: list[list[str]] = []

    def capture(name, argv, *, timeout=None):
        seen.append(argv)
        return dag.Step(name=name, ok=True, seconds=1.0, detail="done")

    monkeypatch.setattr(registry, "run_step", capture)
    registry.run_content_analyse(_ctx(tmp_path))
    registry.run_content_analyse_backlog(_ctx(tmp_path))

    for argv in seen:
        assert argv[-4:-2] == ["--since"] or "--since" in argv
        assert "analyze" in argv
        assert float(argv[argv.index("--budget-s") + 1]) == 123.0
        # A --limit would make the run non-resumable in the way that matters:
        # the queue is "items with no analysis row", and a limit truncates it
        # the same way every firing, so the tail would never be reached.
        assert "--limit" not in argv
    assert seen[0][seen[0].index("--since") + 1] == str(registry.ANALYSE_SINCE_DAYS)
    assert seen[1][seen[1].index("--since") + 1] == "0"


@pytest.mark.parametrize("fn", [
    registry.run_content_analyse,
    registry.run_content_analyse_backlog,
])
def test_analyse_honours_the_network_kill_switch(fn, tmp_path, monkeypatch):
    """It calls a model, so it leaves the machine, so the switch gates it."""
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "1")

    def forbidden(*a, **k):
        raise AssertionError("a gated task tried to run a step")

    monkeypatch.setattr(registry, "run_step", forbidden)
    res = fn(_ctx(tmp_path))
    assert res.outcome == "no_source"


# -- the runner seam ---------------------------------------------------------


def test_run_task_records_trigger_and_duration(tmp_path, monkeypatch):
    run, calls = quiet_run(ledger_written=2)
    monkeypatch.setattr(registry, "TASKS", (stub_task(run),))
    monkeypatch.setattr(runner, "LOG_DIR", tmp_path / "logs")
    db = tmp_path / "run.duckdb"
    Warehouse(db).close()  # create the file

    out = runner.run_task("stub_calendar", db_path=db, trigger="ui")
    assert len(calls) == 1
    assert out.log_path.exists()
    with Warehouse(db) as wh:
        row = wh.sql("SELECT * FROM fetch_run").iloc[0]
        last = health.last_run(wh, "stub_calendar")
    assert row["trigger"] == "ui"
    assert int(row["rows_written"]) == 2
    assert last is not None and last["duration_ms"] is not None
    assert last["duration_ms"] >= 0


def test_run_task_refuses_unknown_and_disabled(tmp_path, monkeypatch):
    run, _ = quiet_run()
    monkeypatch.setattr(registry, "TASKS",
                        (stub_task(run, task_id="off", enabled=False),))
    with pytest.raises(KeyError):
        runner.run_task("nope", db_path=tmp_path / "x.duckdb")
    with pytest.raises(ValueError, match="disabled"):
        runner.run_task("off", db_path=tmp_path / "x.duckdb")


def test_the_log_is_tail_truncated_with_a_marker(tmp_path):
    path = tmp_path / "big.log"
    runner._write_log(path, "x" * (runner.LOG_CAP_BYTES + 5000))
    data = path.read_bytes()
    assert len(data) <= runner.LOG_CAP_BYTES + 200
    assert b"log truncated" in data[:100]


def test_an_invalid_trigger_is_refused(tmp_path):
    with pytest.raises(ValueError):
        runner.execute("stub", _ctx(tmp_path), trigger="cron")


# -- derived health ----------------------------------------------------------


def _ledger_row(wh, pipeline, *, status="ok", age_h=0.0, credits=0.0,
                duration_s=1.0):
    rec = fetch_ledger.RunRecord(pipeline)
    rec.started = dt.datetime.now(UTC) - dt.timedelta(hours=age_h)
    rec.finished = rec.started + dt.timedelta(seconds=duration_s)
    rec.credits = credits
    fetch_ledger.record_finished(wh, rec, status=status,
                                 note="err note" if status == "error" else None)


def test_health_states_follow_the_rules(tmp_path, monkeypatch):
    run, _ = quiet_run()
    task = stub_task(run, task_id="hp", due=registry.Interval(hours=4),
                     window=dt.timedelta(hours=3))
    wh = Warehouse(tmp_path / "h.duckdb")
    try:
        assert health.task_health(wh, task)["state"] == "never_ran"

        _ledger_row(wh, "hp", status="ok", age_h=9.0)  # > 4h * grace 2
        assert health.task_health(wh, task)["state"] == "stale"

        _ledger_row(wh, "hp", status="ok", age_h=1.0)
        assert health.task_health(wh, task)["state"] == "ok"

        _ledger_row(wh, "hp", status="error", age_h=0.5)
        _ledger_row(wh, "hp", status="error", age_h=0.2)
        got = health.task_health(wh, task)
        assert got["state"] == "failing"
        assert got["consecutive_failures"] == 2
        assert "err note" in got["reason"]

        disabled = stub_task(run, task_id="hp", due=registry.Interval(hours=4),
                             enabled=False)
        assert health.task_health(wh, disabled)["state"] == "disabled"

        # a claimed-but-unfinished firing shows as running
        dag.apply_migrations(wh)
        wh.sql("INSERT INTO dag_firing VALUES ('hp', ?, 0, ?, ?, 'running', NULL)",
               [SEASON, dt.datetime.now(UTC), dt.datetime.now(UTC)])
        assert health.task_health(wh, task)["state"] == "running"
    finally:
        wh.close()


def test_deadline_relative_tasks_are_never_cadence_stale(tmp_path):
    run, _ = quiet_run()
    task = stub_task(run, task_id="dr",
                     due=registry.DeadlineRelative(hours_before=30))
    wh = Warehouse(tmp_path / "dr.duckdb")
    try:
        _ledger_row(wh, "dr", status="ok", age_h=120.0)  # five days: normal
        assert health.task_health(wh, task)["state"] == "ok"
    finally:
        wh.close()


def test_avg_duration_uses_only_ok_runs(tmp_path):
    wh = Warehouse(tmp_path / "avg.duckdb")
    try:
        _ledger_row(wh, "p", status="ok", duration_s=2.0)
        _ledger_row(wh, "p", status="ok", duration_s=4.0)
        _ledger_row(wh, "p", status="error", duration_s=100.0)
        avg = health.avg_duration_ms(wh, "p")
        assert avg == pytest.approx(3000.0, rel=0.01)
    finally:
        wh.close()


def test_pipeline_status_shape_is_the_declared_contract(db):
    """The panel is built against THIS shape; changing it is a contract
    change, not a refactor."""
    # An explicit `now` before GW1: next_due for the deadline-relative tasks
    # must come from the seeded deadline, and the schedule test must not
    # depend on the wall clock.
    now = dt.datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
    with Warehouse(db) as wh:
        rows = health.pipeline_status(wh, season=SEASON, now=now)
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {t.id for t in registry.TASKS}
    row = by_id["content_fast_rss"]
    assert set(row) == {"id", "description", "family", "schedule", "enabled",
                        "health", "last_run", "avg_duration_ms", "next_due",
                        "metered"}
    assert set(row["health"]) == {"state", "reason", "consecutive_failures"}
    assert set(row["metered"]) == {"confirm_required", "credits_estimate",
                                   "month_credits"}
    assert row["schedule"] == "every 4h"
    assert row["health"]["state"] == "never_ran"
    assert row["next_due"] is not None
    assert by_id["audio_retention"]["schedule"] == "weekly"
    assert by_id["post_gw_settlement"]["schedule"] == "daily 10:30 UTC"
    assert by_id["panel_picks_crawl"]["schedule"] == "daily 11:15 UTC"
    assert by_id["price_radar"]["schedule"] == "daily 02:00 Europe/London"
    assert "ladder" in by_id["odds_refresh"]["schedule"]
    assert by_id["odds_refresh"]["metered"]["confirm_required"] is True
    # deadline-relative next_due comes from the seeded GW1 deadline
    assert by_id["presser_projection_refresh"]["next_due"] == (
        GW1 - dt.timedelta(hours=30)).isoformat()


def test_month_credits_sums_the_local_ledger(tmp_path):
    wh = Warehouse(tmp_path / "mc.duckdb")
    try:
        _ledger_row(wh, "odds_refresh", status="ok", credits=12.0)
        _ledger_row(wh, "odds_refresh", status="ok", credits=2.0)
        assert health.month_credits(wh, "odds_refresh") == pytest.approx(14.0)
    finally:
        wh.close()
