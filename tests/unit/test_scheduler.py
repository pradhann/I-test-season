"""The in-process scheduler. DEPLOYMENT.md §2.4.

Three things are asserted here because three things would be silent failures:

* **Due evaluation against a frozen clock.** The schedule has one definition,
  in ``deadline_dag.due_tasks`` plus ``registry.registry_due``, and
  ``scheduler.owed_at`` sums exactly those two at an explicit instant. A copy
  of the arithmetic inside the scheduler would drift from the registry the
  first time a task moved.
* **The writer lock.** The scheduler tick and the UI pipeline trigger are both
  writers in one process now that launchd is gone, and DuckDB answers a
  collision with transaction conflicts. The test starts the scheduler against
  a tmp warehouse and asserts that a UI-side write waits for the tick rather
  than racing it.
* **The env gate.** The loop is off unless ``FPL_EDGE_SCHEDULER`` says
  otherwise, so the suite, the Mac dev server and ``make deploy-check`` never
  start one against whatever warehouse they happened to point at.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import threading
import time

import pytest

from fpl_edge.pipelines import registry as pipe_registry
from fpl_edge.platform import scheduler as sched

UTC = dt.UTC


@pytest.fixture()
def db(tmp_path):
    from fpl_edge.store import Warehouse

    path = tmp_path / "fpl.duckdb"
    with Warehouse(path):
        pass
    return path


# -- the env gate ------------------------------------------------------------


def test_the_scheduler_is_off_unless_the_variable_says_otherwise():
    assert sched.scheduler_enabled({}) is False
    assert sched.scheduler_enabled({sched.SCHEDULER_ENV: ""}) is False
    assert sched.scheduler_enabled({sched.SCHEDULER_ENV: "0"}) is False
    assert sched.scheduler_enabled({sched.SCHEDULER_ENV: "1"}) is True
    assert sched.scheduler_enabled({sched.SCHEDULER_ENV: "yes"}) is True


def test_creating_an_app_starts_no_scheduler_by_default(db, monkeypatch):
    from fastapi.testclient import TestClient

    from fpl_edge.platform.app.factory import create_app

    monkeypatch.delenv(sched.SCHEDULER_ENV, raising=False)
    app = create_app(db)
    with TestClient(app):
        assert app.state.scheduler is None
        health = TestClient(app).get("/api/health").json()
    assert health["scheduler"]["running"] is False
    assert sched.SCHEDULER_ENV in health["scheduler"]["reason"]


def test_the_variable_starts_one_and_shutdown_stops_it(db, monkeypatch):
    from fastapi.testclient import TestClient

    from fpl_edge.platform.app.factory import create_app

    monkeypatch.setenv(sched.SCHEDULER_ENV, "1")
    # A tick that does nothing: this test is about the lifecycle, and running
    # real pipelines here would spend real time and touch the network gate.
    monkeypatch.setattr("fpl_edge.jobs.deadline_dag.tick",
                        lambda **kwargs: None)
    app = create_app(db)
    with TestClient(app) as client:
        assert app.state.scheduler is not None
        for _ in range(100):
            if app.state.scheduler.state.ticks:
                break
            time.sleep(0.02)
        body = client.get("/api/health").json()
        assert body["scheduler"]["running"] is True
        assert body["scheduler"]["ticks"] >= 1, "the first tick did not run immediately"
    assert app.state.scheduler is None


def test_the_interval_default_is_the_launchd_cadence():
    assert sched.tick_interval_s({}) == 600.0
    assert sched.tick_interval_s({sched.INTERVAL_ENV: "5"}) == 5.0
    # A value that cannot be read is the default, never zero, which would spin.
    assert sched.tick_interval_s({sched.INTERVAL_ENV: "nonsense"}) == 600.0
    assert sched.tick_interval_s({sched.INTERVAL_ENV: "0"}) == 600.0


# -- Due evaluation against a frozen clock -----------------------------------


def test_owed_at_is_the_registrys_answer_and_nothing_else():
    """A frozen instant chosen so exactly the calendar and interval tasks the
    registry declares are owed, compared against the registry itself."""
    now = dt.datetime(2026, 9, 17, 12, 30, tzinfo=UTC)
    owed = sched.owed_at(now, deadlines=[])
    from fpl_edge.jobs import deadline_dag as dag

    expected = sorted(
        dag.due_tasks([], now) + pipe_registry.registry_due([], now),
        key=lambda d: (d.due_utc, d.task))
    assert [(d.task, d.due_utc) for d in owed] == \
           [(d.task, d.due_utc) for d in expected]
    assert owed, "no firing was owed at an instant chosen because several are"


def test_a_daily_task_is_owed_for_every_instant_in_the_lookback():
    """The boot catch-up rule, which needs no extra mechanism.

    ``contracts.LOOKBACK`` is 36h, so a daily task has two owed instants at any
    moment and each is judged against the task's own ``stale_window``.
    content_transcribe is Calendar(12:00 UTC) with a 6h window: at 12:30 UTC
    today's firing is 30 minutes old and runnable, and yesterday's is 24.5
    hours old and recorded as skipped_stale rather than burst-fired.
    """
    now = dt.datetime(2026, 9, 17, 12, 30, tzinfo=UTC)
    owed = [d for d in sched.owed_at(now, deadlines=[])
            if d.task == "content_transcribe"]
    assert [(d.due_utc, d.stale) for d in owed] == [
        (dt.datetime(2026, 9, 16, 12, 0, tzinfo=UTC), True),
        (dt.datetime(2026, 9, 17, 12, 0, tzinfo=UTC), False),
    ]


def test_a_generous_stale_window_keeps_an_older_firing_runnable():
    """content_analyse carries a 23h window, so at 12:30 UTC the firing from
    13:30 UTC yesterday is 23 hours old and still inside it."""
    now = dt.datetime(2026, 9, 17, 12, 30, tzinfo=UTC)
    owed = [d for d in sched.owed_at(now, deadlines=[])
            if d.task == "content_analyse"]
    assert [(d.due_utc, d.stale) for d in owed] == [
        (dt.datetime(2026, 9, 16, 13, 30, tzinfo=UTC), False),
    ]


def test_deadline_relative_tasks_need_deadlines_to_be_owed():
    now = dt.datetime(2026, 9, 17, 12, 30, tzinfo=UTC)
    without = {d.task for d in sched.owed_at(now, deadlines=[])}
    deadline = now + dt.timedelta(hours=4)
    with_one = {d.task for d in sched.owed_at(now, deadlines=[(5, deadline)])}
    assert with_one >= without
    assert with_one - without, "a deadline four hours out owed no new firing"


# -- the writer lock ---------------------------------------------------------


def test_the_lease_is_reentrant_and_free_when_nobody_holds_it():
    assert sched.writer_lease_is_free() is True
    with sched.write_lease(holder="outer"):
        assert sched.writer_lease_is_free() is False
        # Reentrant: one thread's nested writes must not deadlock it.
        with sched.write_lease(holder="inner"):
            pass
    assert sched.writer_lease_is_free() is True


def test_a_second_thread_cannot_take_a_held_lease():
    taken = threading.Event()
    refused: list[Exception] = []

    def _grab() -> None:
        try:
            with sched.write_lease(timeout_s=0.05, holder="other thread"):
                taken.set()
        except sched.WriterBusy as exc:
            refused.append(exc)

    with sched.write_lease(holder="test"):
        thread = threading.Thread(target=_grab)
        thread.start()
        thread.join(timeout=5.0)
    assert not taken.is_set()
    assert len(refused) == 1
    assert "writer lease" in str(refused[0])


def test_a_ui_write_waits_for_a_running_tick(db):
    """The scheduler runs in a thread against a tmp warehouse and the UI-side
    write, which is what POST /api/pipelines/{task_id}/run does in its worker
    thread, waits for the lease rather than opening a second writer.

    The tick is injected: the assertion is about the lock, and running real
    pipelines here would spend real time. The lease it takes is the same one
    the real tick takes, because ``run_one_tick`` takes it around whatever
    ``tick_fn`` is.
    """
    tick_started = threading.Event()
    release_tick = threading.Event()
    order: list[str] = []

    def _slow_tick(**kwargs):
        order.append("tick start")
        tick_started.set()
        release_tick.wait(timeout=10.0)
        order.append("tick end")
        return None

    scheduler = sched.Scheduler(db, tick_fn=_slow_tick)
    ticker = threading.Thread(target=scheduler.run_one_tick, daemon=True)
    ticker.start()
    assert tick_started.wait(timeout=5.0), "the tick never started"

    ui_done = threading.Event()

    def _ui_write() -> None:
        # Exactly the call the trigger route's worker thread makes.
        with sched.write_lease(holder="ui run test_task"):
            order.append("ui write")
        ui_done.set()

    ui = threading.Thread(target=_ui_write, daemon=True)
    ui.start()
    # The UI thread must still be waiting while the tick holds the lease.
    assert not ui_done.wait(timeout=0.5), "the UI write ran during the tick"
    assert order == ["tick start"]

    release_tick.set()
    assert ui_done.wait(timeout=5.0), "the UI write never got the lease"
    ticker.join(timeout=5.0)
    assert order == ["tick start", "tick end", "ui write"]
    assert sched.writer_lease_is_free()


# -- single flight -----------------------------------------------------------


def test_an_overlapping_tick_is_skipped_and_counted(db):
    """A tick still running when the next is due is skipped, and the skip is
    counted rather than silent. The settlement chain is 17 steps and can
    outlive a 600s cadence."""
    running = threading.Event()
    release = threading.Event()

    def _slow_tick(**kwargs):
        running.set()
        release.wait(timeout=10.0)
        return None

    scheduler = sched.Scheduler(db, tick_fn=_slow_tick)
    first = threading.Thread(target=scheduler.run_one_tick, daemon=True)
    first.start()
    assert running.wait(timeout=5.0)

    result: list[object] = []
    second = threading.Thread(
        target=lambda: result.append(scheduler.run_one_tick()), daemon=True)
    second.start()
    second.join(timeout=5.0)

    assert result == [None], "the overlapping tick was not skipped"
    assert scheduler.state.skipped_overlap == 1
    release.set()
    first.join(timeout=5.0)
    assert scheduler.state.ticks == 1


def test_a_failing_tick_records_the_error_and_does_not_end_the_loop(db):
    def _boom(**kwargs):
        raise RuntimeError("the tick broke")

    scheduler = sched.Scheduler(db, tick_fn=_boom)
    assert scheduler.run_one_tick() is None
    assert "RuntimeError: the tick broke" == scheduler.state.last_error
    assert scheduler.state.ticks == 1
    assert sched.writer_lease_is_free(), "a failed tick kept the writer lease"
    # And the loop survives it: a second tick still runs.
    scheduler.run_one_tick()
    assert scheduler.state.ticks == 2


def test_the_loop_ticks_immediately_and_stops_on_request(db):
    ticks: list[float] = []

    def _tick(**kwargs):
        ticks.append(time.monotonic())
        return None

    async def _drive() -> None:
        scheduler = sched.Scheduler(db, tick_fn=_tick, interval_s=0.05)
        scheduler.start()
        await asyncio.sleep(0.3)
        await scheduler.stop(timeout_s=5.0)
        assert scheduler.state.running is False

    asyncio.run(_drive())
    assert len(ticks) >= 2, "the loop did not tick repeatedly"


def test_every_tick_reports_one_line_so_a_dead_loop_is_visible(db, caplog):
    """A scheduler that runs silently cannot be told apart from one that died.

    ``/api/health`` carries the tick state, but that route trims to
    ``{ok, now}`` for a caller with no session, so on a deployment where
    sign-in is not configured the log is the only place an operator can see
    the loop is alive. This was found on the live Railway service on
    2026-09-20, where nothing could establish whether the scheduler was
    ticking.
    """
    import logging

    loop = sched.Scheduler(db)
    with caplog.at_level(logging.INFO, logger="fpl_edge.platform.scheduler"):
        loop.run_one_tick()
    lines = [r.getMessage() for r in caplog.records
             if r.name == "fpl_edge.platform.scheduler"]
    assert any("scheduler tick" in line for line in lines), lines
    assert any("task(s) fired" in line for line in lines), lines
