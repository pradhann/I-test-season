"""The in-process scheduler, and the one writer lock the whole process shares.

DEPLOYMENT.md §2.3 and §2.4. On the Mac a launchd tick every 600s drove
:func:`fpl_edge.jobs.deadline_dag.tick` in its own process, and the DuckDB
file lock kept that process apart from the web server. On Railway there is one
service, one volume and one process, so the tick moves inside the server and
the file lock no longer separates anything. Railway Cron was rejected for the
same reason: a cron container is a second process and a Railway volume
attaches to one service, so it would be either unable to reach the database or
a second writer against one file.

What this module owns
---------------------

**The loop.** One asyncio task, started from ``create_app`` when
``FPL_EDGE_SCHEDULER`` is set. It runs its first tick immediately rather than
after the first sleep, because that first tick is the boot catch-up: ``tick``
reads ``contracts.LOOKBACK`` (36h) of owed firings and evaluates each against
its own ``stale_window``, running what is inside the window and recording what
is outside it as ``skipped_stale`` with the age in the detail. Nothing extra is
needed for a restart to catch up.

**Due evaluation is not reimplemented here.** :func:`owed_at` calls
``deadline_dag.due_tasks`` and ``registry.registry_due`` with an explicit
``now``, which is the same pair ``tick`` itself sums, so the schedule has one
definition and a frozen-clock test can ask what is owed without running it.

**Single flight.** A tick still running when the next is due is skipped and
the skip is counted in :attr:`SchedulerState.skipped_overlap`, never silent.
The settlement chain is 17 steps and can outlive a 600s cadence.

**The writer lock.** Every writer inside this process takes
:func:`write_lease` before it opens the warehouse for writing. This is a
concurrency case launchd never had: the scheduler tick and
``POST /api/pipelines/{task_id}/run`` both open writers, and DuckDB answers a
collision with transaction conflicts rather than a deadlock. The lock is a
plain reentrant ``threading.Lock`` and not an asyncio primitive because both
of its callers run in threads: the tick goes through
``loop.run_in_executor``, and the UI trigger route already runs its work in a
daemon thread. The UI side WAITS for the lease with a long timeout and
reports ``WriterBusy`` if it never arrives, so a triggered run queues behind a
tick instead of racing it. The lock does not replace the DuckDB file lock: the
tasks' own subprocesses are separate processes, and the file lock is what
holds them apart.

**What it does not own.** Firing claims stay in ``deadline_dag.tick``, ledger
rows stay in ``pipelines/runner.py:execute``, and the checkpoint discipline in
``_write_with_retry`` is untouched. This module adds a loop and a lock.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from fpl_edge.store.warehouse import DEFAULT_DB

UTC = dt.UTC

log = logging.getLogger(__name__)

#: Seconds between ticks. The launchd plist's StartInterval, kept so the
#: per-task stale windows keep the meaning they were tuned for. Several of
#: them are 23 hours because the Mac's tick did not fire while the lid was
#: shut; on Railway the service does not sleep, which makes those windows
#: generous rather than necessary. Retuning them is its own change.
TICK_INTERVAL_S = 600.0

#: The environment variable that decides whether the loop starts at all.
#: Unset, empty or "0" means no scheduler, so the test suite, the Mac dev
#: server and ``make deploy-check`` all run the app without one.
SCHEDULER_ENV = "FPL_EDGE_SCHEDULER"

#: Overrides :data:`TICK_INTERVAL_S`, for the deploy check and for tests that
#: want a second tick without waiting ten minutes.
INTERVAL_ENV = "FPL_EDGE_SCHEDULER_INTERVAL_S"

#: How long a UI-triggered write waits for the lease before it gives up. Long
#: enough to sit behind a settlement chain, short enough that a stuck tick
#: surfaces as a named refusal rather than a thread that never returns.
UI_LEASE_TIMEOUT_S = 1800.0

#: The one lock. Module scope on purpose: there is one process, one volume and
#: one database file, so a per-instance lock would let two app objects in the
#: same process write at once.
_WRITER_LOCK = threading.RLock()

#: How deep the current holder is nested in the lease. Kept beside the lock
#: because ``RLock.acquire(blocking=False)`` succeeds for the thread that
#: already holds it, so a probe built on the lock alone would report the lease
#: free to the very thread holding it.
_LEASE_DEPTH = 0


class WriterBusy(RuntimeError):
    """The writer lease was held by somebody else for the whole timeout."""


def scheduler_enabled(env: dict[str, str] | None = None) -> bool:
    """Whether this process should run the loop. Default is off.

    Off by default is what keeps the suite, the Mac dev server and the
    container's own acceptance check from starting a scheduler that would
    write to whatever warehouse they happened to point at.
    """
    source = os.environ if env is None else env
    return str(source.get(SCHEDULER_ENV, "")) not in ("", "0")


def tick_interval_s(env: dict[str, str] | None = None) -> float:
    """The cadence this process ticks at, in seconds."""
    source = os.environ if env is None else env
    raw = str(source.get(INTERVAL_ENV, "")).strip()
    if not raw:
        return TICK_INTERVAL_S
    try:
        value = float(raw)
    except ValueError:
        return TICK_INTERVAL_S
    return value if value > 0 else TICK_INTERVAL_S


@contextmanager
def write_lease(*, timeout_s: float = UI_LEASE_TIMEOUT_S,
                holder: str = "unnamed") -> Iterator[None]:
    """Hold the process-wide writer lease, or raise :class:`WriterBusy`.

    Every in-process path that opens the warehouse for writing goes through
    here: the scheduler tick, and the UI pipeline trigger's worker thread.
    ``timeout_s`` of 0 or less means take it or fail at once, which is what a
    test asserting contention uses.
    """
    global _LEASE_DEPTH

    acquired = _WRITER_LOCK.acquire(timeout=timeout_s if timeout_s > 0 else 0)
    if not acquired:
        raise WriterBusy(
            f"{holder} waited {timeout_s:g}s for the writer lease and another "
            f"writer in this process still holds it. Nothing was written."
        )
    _LEASE_DEPTH += 1
    try:
        yield
    finally:
        _LEASE_DEPTH -= 1
        _WRITER_LOCK.release()


def writer_lease_is_free() -> bool:
    """True when nothing in this process currently holds the lease.

    A status probe for the health payload and for tests. It reads the depth
    rather than trying the lock, because the lock is reentrant and would
    answer "free" to the thread that is holding it.
    """
    return _LEASE_DEPTH == 0


def owed_at(
    now: dt.datetime,
    *,
    db_path: Path | str = DEFAULT_DB,
    season: str | None = None,
    deadlines: list[tuple[int, dt.datetime]] | None = None,
) -> list[Any]:
    """Every firing owed at ``now``, from the same two sources ``tick`` sums.

    ``deadlines`` is injectable so a frozen-clock test can state the fixture
    list instead of needing a warehouse. Left out, they are read from a
    private read copy exactly as the tick reads them, and an unreadable
    warehouse yields an empty deadline list rather than an exception: the
    calendar and interval tasks are still owed and still worth reporting.
    """
    from fpl_edge.jobs import deadline_dag as dag
    from fpl_edge.pipelines import contracts, registry

    season = season or contracts.SEASON
    now = now.astimezone(UTC)
    if deadlines is None:
        try:
            deadlines = dag.read_deadlines(db_path, season=season)
        except Exception:  # noqa: BLE001 - see the docstring
            deadlines = []
    return sorted(
        dag.due_tasks(deadlines, now, season=season)
        + registry.registry_due(deadlines, now, season=season),
        key=lambda d: (d.due_utc, d.task),
    )


@dataclass
class SchedulerState:
    """What the loop has done, as the health payload reports it."""

    running: bool = False
    ticks: int = 0
    skipped_overlap: int = 0
    last_tick_utc: str | None = None
    last_tick_seconds: float | None = None
    last_tick_fired: list[str] = field(default_factory=list)
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "ticks": self.ticks,
            "skipped_overlap": self.skipped_overlap,
            "last_tick_utc": self.last_tick_utc,
            "last_tick_seconds": self.last_tick_seconds,
            "last_tick_fired": list(self.last_tick_fired),
            "last_error": self.last_error,
            "interval_s": tick_interval_s(),
            "writer_lease_free": writer_lease_is_free(),
        }


class Scheduler:
    """The loop. One instance per app, one asyncio task per instance.

    ``tick_fn`` is injectable and defaults to ``deadline_dag.tick``. A test
    hands in its own so the loop, the single-flight rule and the writer lease
    can be asserted without running real pipelines.
    """

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB,
        *,
        tick_fn: Callable[..., Any] | None = None,
        interval_s: float | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._tick_fn = tick_fn
        self._interval_s = interval_s
        self.state = SchedulerState()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        #: Held for the duration of one tick. Its state is the single-flight
        #: answer: a tick that cannot take it is an overlap, not a wait.
        self._in_flight = threading.Lock()

    @property
    def interval_s(self) -> float:
        return self._interval_s if self._interval_s is not None else tick_interval_s()

    def _tick(self) -> Any:
        fn = self._tick_fn
        if fn is None:
            from fpl_edge.jobs import deadline_dag as dag

            fn = dag.tick
        return fn(db_path=self.db_path)

    def run_one_tick(self) -> Any | None:
        """One tick, under both locks. ``None`` means an overlap was skipped.

        Blocking, and called from a thread: the tick opens and closes the
        DuckDB writer several times and would block the event loop.
        """
        if not self._in_flight.acquire(blocking=False):
            self.state.skipped_overlap += 1
            log.warning("scheduler tick skipped: the previous one is still running")
            return None
        started = dt.datetime.now(UTC)
        try:
            with write_lease(holder="scheduler tick"):
                report = self._tick()
        except Exception as exc:  # noqa: BLE001 - a failed tick must not end the loop
            self.state.last_error = f"{type(exc).__name__}: {exc}"
            log.exception("scheduler tick failed")
            report = None
        finally:
            finished = dt.datetime.now(UTC)
            self.state.ticks += 1
            self.state.last_tick_utc = finished.isoformat()
            self.state.last_tick_seconds = (finished - started).total_seconds()
            self._in_flight.release()
        fired = getattr(report, "fired", None) or []
        self.state.last_tick_fired = [
            f"{f.task}:{f.outcome}" for f in fired if hasattr(f, "task")
        ]
        return report

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        self.state.running = True
        try:
            while not self._stop.is_set():
                # The first tick runs before the first sleep, which is what
                # makes a restart catch up on what it owes.
                await loop.run_in_executor(None, self.run_one_tick)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
                except TimeoutError:
                    continue
        finally:
            self.state.running = False

    def start(self) -> asyncio.Task:
        """Start the loop on the running event loop. Idempotent."""
        if self._task is not None and not self._task.done():
            return self._task
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(
            self._loop(), name="fpl-edge-scheduler")
        return self._task

    async def stop(self, *, timeout_s: float = 30.0) -> None:
        """Ask the loop to finish the tick it is on and return."""
        self._stop.set()
        task = self._task
        if task is None:
            return
        try:
            await asyncio.wait_for(task, timeout=timeout_s)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
        finally:
            self._task = None
            self.state.running = False
