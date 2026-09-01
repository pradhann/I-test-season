"""The one execution path every pipeline run goes through.

Owner elevation 2026-08-31: monitoring, logs and timings are core objects of
the product, not notes. So every run -- a scheduler tick's firing, a CLI
trigger, later a UI button -- passes through :func:`execute`:

* **One ledger row per run** (``fetch_run``, PIPELINES.md §4.2), with honest
  ``started``/``finished`` stamps taken around the work itself, the counts
  the task reports, and a ``trigger`` column saying who asked.
* **Logs are captured, always.** In-process prints are redirected; each
  subprocess step's recorded tail is included; the whole thing lands in
  ``data/warehouse/pipeline_logs/<run_id>.log``, tail-truncated at
  :data:`LOG_CAP_BYTES` with an explicit marker. A run that did not go ``ok``
  carries the last :data:`NOTE_TAIL_LINES` log lines in its ledger note, so
  the panel can show *why* without opening the file.
* **The ledger write is separable from the run.** The deadline DAG runs
  tasks with the DuckDB write lock free and reopens to record -- so
  :func:`execute` fully populates the :class:`~fpl_edge.store.fetch_ledger.
  RunRecord` and :func:`record` lands it inside whatever write burst the
  caller owns. :func:`run_task` is the standalone composition of the two,
  and the seam the UI trigger routes will call.

What the runner does NOT do: claim ``dag_firing`` rows. Scheduled firings
are claimed by the DAG tick before it calls in here; a manual ``run_task``
is not a scheduled firing and leaves no firing row -- the ledger row with
``trigger='cli'``/``'ui'`` is its record.
"""

from __future__ import annotations

import datetime as dt
import io
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

from fpl_edge.jobs.deadline_dag import SEASON, TaskContext, TaskResult
from fpl_edge.pipelines import registry
from fpl_edge.store import DEFAULT_DB, Warehouse, fetch_ledger

UTC = dt.UTC

#: Where every run's captured output lands, named by the ledger's run_id so
#: the two are joinable with no extra column.
LOG_DIR = Path("data/warehouse/pipeline_logs")

#: A log is tail-truncated to this many bytes, with a marker line at the top.
LOG_CAP_BYTES = 200_000

#: How many trailing log lines ride into the ledger note on a non-ok run.
NOTE_TAIL_LINES = 30

#: Ledger note ceiling. The note is a summary channel, not the log.
NOTE_CAP_CHARS = 4_000

#: Who asked for the run. Stored in fetch_run."trigger".
TRIGGERS = ("scheduler", "ui", "cli")

#: dag_firing outcomes -> fetch_run statuses. A surjection onto the ledger's
#: smaller vocabulary: delivered and quiet both mean the run ran ("ok");
#: the honest gaps map to themselves.
LEDGER_STATUS: dict[str, str] = {
    "delivered": "ok",
    "quiet": "ok",
    "no_source": "no_source",
    "error": "error",
}


@dataclass
class RunOutcome:
    """Everything one execution produced, ready to be recorded and rendered."""

    task_id: str
    trigger: str
    result: TaskResult
    record: fetch_ledger.RunRecord
    log_path: Path


def log_path_for(run_id: str, *, log_dir: Path | None = None) -> Path:
    return (log_dir or LOG_DIR) / f"{run_id}.log"


def _write_log(path: Path, text: str) -> None:
    data = text.encode("utf-8", errors="replace")
    if len(data) > LOG_CAP_BYTES:
        kept = data[-LOG_CAP_BYTES:]
        marker = (f"[log truncated: kept the last {LOG_CAP_BYTES} of "
                  f"{len(data)} bytes]\n").encode()
        data = marker + kept
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _tail_lines(text: str, n: int) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def execute(
    task_id: str,
    ctx: TaskContext,
    *,
    fn=None,
    trigger: str = "scheduler",
    log_dir: Path | None = None,
) -> RunOutcome:
    """Run one task, capturing everything. Never raises.

    ``fn`` overrides the registry runner -- the DAG tick passes its own
    ``TASKS`` entry first so tests that monkeypatch that dict keep working.
    An exception inside the task becomes an ``error`` TaskResult with the
    traceback in both the detail and the log, exactly as the tick always
    treated it.
    """
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger {trigger!r} not in {TRIGGERS}")
    fn = fn or registry.runner_for(task_id)
    rec = fetch_ledger.RunRecord(task_id, "deadline_dag")
    rec.trigger = trigger

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            if fn is None:
                result = TaskResult(
                    outcome="error",
                    detail=f"no runner registered for task {task_id!r}",
                )
            else:
                result = fn(ctx)
    except Exception:  # noqa: BLE001 - the error IS the captured result
        tb = traceback.format_exc()
        buf.write("\n" + tb)
        result = TaskResult(outcome="error", detail=tb[-600:])
    rec.finished = dt.datetime.now(UTC)

    # The log: a header, every recorded step, then whatever was printed.
    lines = [
        f"# task={task_id} trigger={trigger} run_id={rec.run_id}",
        f"# started={rec.started.isoformat()} finished={rec.finished.isoformat()}",
        f"# outcome={result.outcome} detail={result.detail[:300]}",
    ]
    for step in result.steps:
        mark = "ok" if step.ok else "FAILED"
        lines.append(f"step {step.name}: {mark} ({step.seconds}s) {step.detail}")
    captured = buf.getvalue()
    if captured.strip():
        lines += ["--- captured output ---", captured]
    log_text = "\n".join(lines)
    path = log_path_for(rec.run_id, log_dir=log_dir)
    try:
        _write_log(path, log_text)
    except OSError:
        # A full disk must not turn a finished run into a failed one; the
        # ledger row still records what happened.
        pass

    status = LEDGER_STATUS.get(result.outcome, "ok")
    note = (f"{result.outcome}: {result.detail}" if result.detail
            else result.outcome)
    if status != "ok":
        note += "\n--- log tail ---\n" + _tail_lines(log_text, NOTE_TAIL_LINES)
    rec.status = status
    rec.note = note[:NOTE_CAP_CHARS]
    rec.add(result.ledger_written, result.ledger_unchanged)
    return RunOutcome(task_id=task_id, trigger=trigger, result=result,
                      record=rec, log_path=path)


def record(wh, outcome: RunOutcome) -> None:
    """Land the finished run's ledger row inside the caller's write burst."""
    fetch_ledger.record_finished(
        wh, outcome.record,
        status=outcome.record.status or "ok",
        note=outcome.record.note,
    )


def run_task(
    task_id: str,
    wh=None,
    *,
    db_path: Path | str | None = None,
    trigger: str = "cli",
    season: str = SEASON,
    now: dt.datetime | None = None,
) -> RunOutcome:
    """Execute one registry task outside the scheduler, and record it.

    The seam the UI trigger routes (PIPELINES.md §6.4) and the CLI call. The
    firing ledger is untouched -- this is not a scheduled firing -- but the
    fetch ledger gets its row with the caller's ``trigger``.

    Pass EITHER an open warehouse (used only for the ledger write, after the
    task has finished -- but note the task's own subprocesses need the write
    lock, so an open *writer* handle held here will contend with them; prefer
    ``db_path``) or a ``db_path`` for a short-lease write at the end.

    A disabled task is refused loudly rather than run quietly: disabling in
    the registry must mean disabled everywhere.
    """
    task = registry.by_id(task_id)
    if task is None:
        raise KeyError(f"no task {task_id!r} in the registry")
    if not task.enabled:
        raise ValueError(f"task {task_id!r} is disabled in the registry")

    if db_path is not None:
        db = Path(db_path)
    elif wh is not None and getattr(wh, "path", None) is not None:
        db = Path(wh.path)
    else:
        db = Path(DEFAULT_DB)
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)

    ctx = TaskContext(
        season=season, gw=registry.NO_GW, due_utc=now, deadline_utc=None,
        now=now, db_path=db,
    )
    outcome = execute(task_id, ctx, trigger=trigger)
    if wh is not None:
        record(wh, outcome)
    else:
        with Warehouse(db, lock_timeout_s=180.0) as ledger_wh:
            record(ledger_wh, outcome)
    return outcome
