"""The task registry: every scheduled pipeline is one reviewable row here.

PIPELINES.md §4.1 / §5 decision 1, closed with the owner 2026-08-31. The
deadline DAG is THE scheduler; this module is its authority list. The Argus
rule transfers verbatim: **adding authority is one line in ``TASKS``**, and a
reviewer reads that line -- what runs, when, how stale is too stale, what it
costs -- without opening the runner.

Four due shapes, no more:

* :class:`Calendar` -- a daily wall-clock instant, either UTC or a named tz
  (the tz walk is the DAG's own calendar-walk, so DST is handled by
  ``zoneinfo`` and never by adding 24h).
* :class:`DeadlineRelative` -- hours before each gameweek deadline, the DAG's
  native shape. May carry a tuple of offsets (the odds ladder).
* :class:`Interval` -- every N hours, at instants aligned to whole multiples
  of the interval since the Unix epoch (UTC), so every process computes the
  same firing key.
* :class:`OnDemand` -- never due on a tick; exists so a manual/UI-triggered
  pipeline still has a registry row, a stale story, and budget metadata.

What this module deliberately does NOT have: a dependency engine. Order
dependencies (settlement before crawls) are modelled as ordered sub-steps
inside one composite task, exactly the shape ``post_gw`` already has. A
general DAG-of-tasks is machinery nothing here needs yet.

Scheduling mechanics stay in :mod:`fpl_edge.jobs.deadline_dag`: the tick
claims a ``dag_firing`` row per (task, season, gw, due_utc) before running,
per-task stale windows drop slept-through firings, and outcomes keep the
migration's vocabulary. Registry tasks ride that machinery unchanged; the
five original DAG tasks are listed here with ``scheduled_by_dag=True`` --
their due instants keep coming from the DAG's own ``due_tasks`` so their
behaviour stays byte-identical, and the registry row is their identity for
the ledger and the future Pipelines panel.

Calendar/Interval firings carry ``gw=NO_GW`` (0). PIPELINES.md sketched
``gw|NULL``, but ``dag_firing.gw`` is NOT NULL inside the primary key and
DuckDB (correctly) refuses NULL there; 0 is the explicit "not
gameweek-scoped" sentinel, matching ``_gw_for_instant``'s own no-deadline
answer.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from fpl_edge.jobs import deadline_dag as dag
from fpl_edge.jobs.deadline_dag import Step, TaskContext, TaskResult, run_step

UTC = dt.UTC

#: The "not gameweek-scoped" sentinel for calendar/interval firings. See the
#: module docstring for why this is 0 and not NULL.
NO_GW = 0

#: Instants for :class:`Interval` are counted from here, so two processes
#: (or a tick and a test) always agree on the firing key.
_EPOCH = dt.datetime(1970, 1, 1, tzinfo=UTC)

#: Default nightly wall-clock budget for the transcription task, seconds.
#: Overridable per-deploy with FPL_EDGE_TRANSCRIBE_BUDGET_S.
TRANSCRIBE_BUDGET_S = 3600.0


# --------------------------------------------------------------------------
# Due shapes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Calendar:
    """Daily at a wall-clock instant. Exactly one of hour_utc / hour_local."""

    hour_utc: int | None = None
    minute: int = 0
    #: Local-time alternative, for tasks whose "2am" is a statement about a
    #: wall clock (FPL's price run). Requires ``tz``.
    hour_local: int | None = None
    tz: str | None = None

    def __post_init__(self) -> None:
        if (self.hour_utc is None) == (self.hour_local is None):
            raise ValueError("Calendar needs exactly one of hour_utc / hour_local")
        if self.hour_local is not None and not self.tz:
            raise ValueError("Calendar(hour_local=...) needs a tz")


@dataclass(frozen=True, slots=True)
class DeadlineRelative:
    """Hours before each gameweek deadline. A tuple is a ladder (odds)."""

    hours_before: float | tuple[float, ...]

    def offsets(self) -> tuple[float, ...]:
        hb = self.hours_before
        return hb if isinstance(hb, tuple) else (float(hb),)


@dataclass(frozen=True, slots=True)
class Interval:
    """Every N hours, at epoch-aligned UTC instants."""

    hours: float


@dataclass(frozen=True, slots=True)
class OnDemand:
    """Never due on a tick. The row exists for identity and metadata."""


Due = Calendar | DeadlineRelative | Interval | OnDemand


# --------------------------------------------------------------------------
# The Task row
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    """One scheduled pipeline. One row in :data:`TASKS`, one reviewable line."""

    id: str
    description: str
    due: Due
    #: How late a firing may run before it is recorded ``skipped_stale``
    #: instead. Same semantics as the DAG's STALE_WINDOWS: value that decays
    #: with the deadline gets a tight window, an idempotent refresh a
    #: generous one.
    stale_window: dt.timedelta
    #: Takes a :class:`~fpl_edge.jobs.deadline_dag.TaskContext`, returns a
    #: :class:`~fpl_edge.jobs.deadline_dag.TaskResult`. Subprocess-shaped
    #: work goes through ``deadline_dag.run_step`` exactly as the original
    #: tasks do.
    run: Callable[[TaskContext], TaskResult]
    #: Metered-API credits one execution is expected to spend. 0 for free
    #: pipelines. The UI confirm flow (PIPELINES.md §6.4) reads this.
    credits_estimate: float = 0.0
    #: True when a manual/UI trigger must show cost and ask first.
    confirm_required: bool = False
    enabled: bool = True
    #: True for the five original DAG tasks: their due instants keep coming
    #: from deadline_dag.due_tasks (byte-identical behaviour, tests pinned),
    #: and registry_due must NOT emit them a second time.
    scheduled_by_dag: bool = False
    #: Wall-clock budget metadata for the panel, where the task has one.
    budget_s: float | None = None


# --------------------------------------------------------------------------
# Due-instant arithmetic (pure; the DAG's calendar-walk transposed)
# --------------------------------------------------------------------------


def _calendar_instants(
    cal: Calendar, now: dt.datetime, lookback: dt.timedelta
) -> list[dt.datetime]:
    """Every due instant in (now - lookback, now], in UTC.

    Walks local dates and converts each -- the DAG's ``nightly_instants``
    method -- because on a DST boundary the gap between consecutive local
    instants is 23 or 25 hours and only the calendar walk gets that right.
    """
    tzinfo = ZoneInfo(cal.tz) if cal.hour_local is not None else UTC
    hour = cal.hour_local if cal.hour_local is not None else cal.hour_utc
    assert hour is not None
    now = now.astimezone(UTC)
    start = now - lookback
    out: list[dt.datetime] = []
    local_date = (start.astimezone(tzinfo) - dt.timedelta(days=1)).date()
    end_date = now.astimezone(tzinfo).date()
    while local_date <= end_date:
        local = dt.datetime(
            local_date.year, local_date.month, local_date.day,
            hour, cal.minute, tzinfo=tzinfo,
        )
        inst = local.astimezone(UTC)
        if start < inst <= now:
            out.append(inst)
        local_date += dt.timedelta(days=1)
    return sorted(out)


def _interval_instants(
    iv: Interval, now: dt.datetime, lookback: dt.timedelta
) -> list[dt.datetime]:
    """Epoch-aligned multiples of the interval in (now - lookback, now]."""
    step = dt.timedelta(hours=iv.hours)
    now = now.astimezone(UTC)
    start = now - lookback
    k = int((now - _EPOCH) / step)  # floor: the newest instant at or before now
    out: list[dt.datetime] = []
    inst = _EPOCH + k * step
    while inst > start:
        if inst <= now:
            out.append(inst)
        inst -= step
    return sorted(out)


def due_instants(
    task: Task,
    deadlines: Sequence[tuple[int, dt.datetime]],
    now: dt.datetime,
    *,
    lookback: dt.timedelta = dag.LOOKBACK,
) -> list[tuple[int, dt.datetime, dt.datetime | None]]:
    """(gw, due_utc, deadline_utc) for every firing owed in the window.

    Calendar/Interval firings carry :data:`NO_GW` and no deadline; a
    DeadlineRelative task gets one instant per offset per deadline, exactly
    the arithmetic ``deadline_dag.due_tasks`` performs for the legacy tasks
    (the parity test in tests/unit/test_jobs_registry.py holds the two equal).
    """
    now = now.astimezone(UTC)
    due = task.due
    if isinstance(due, OnDemand):
        return []
    if isinstance(due, Calendar):
        return [(NO_GW, i, None) for i in _calendar_instants(due, now, lookback)]
    if isinstance(due, Interval):
        return [(NO_GW, i, None) for i in _interval_instants(due, now, lookback)]
    if isinstance(due, DeadlineRelative):
        horizon = now - lookback
        out: list[tuple[int, dt.datetime, dt.datetime | None]] = []
        for gw, deadline in sorted(deadlines):
            deadline = deadline.astimezone(UTC)
            for hours in due.offsets():
                inst = deadline - dt.timedelta(hours=hours)
                if horizon < inst <= now:
                    out.append((int(gw), inst, deadline))
        return out
    raise TypeError(f"unknown due shape: {type(due).__name__}")


def registry_due(
    deadlines: Sequence[tuple[int, dt.datetime]],
    now: dt.datetime,
    *,
    season: str = dag.SEASON,
    lookback: dt.timedelta = dag.LOOKBACK,
) -> list[dag.Due]:
    """Owed firings for every enabled registry task the DAG does not already
    schedule itself. Staleness is decided here with the task's own window, so
    the same rule applies to a launchd tick and a manual one."""
    now = now.astimezone(UTC)
    out: list[dag.Due] = []
    for task in TASKS:
        if not task.enabled or task.scheduled_by_dag:
            continue
        for gw, inst, deadline in due_instants(task, deadlines, now, lookback=lookback):
            out.append(dag.Due(
                task=task.id, season=season, gw=gw, due_utc=inst,
                deadline_utc=deadline, stale=(now - inst) > task.stale_window,
            ))
    return out


def by_id(task_id: str) -> Task | None:
    for task in TASKS:
        if task.id == task_id:
            return task
    return None


def runner_for(task_id: str) -> Callable[[TaskContext], TaskResult] | None:
    task = by_id(task_id)
    return task.run if task is not None else None


def stale_window_of(task_id: str) -> dt.timedelta | None:
    task = by_id(task_id)
    return task.stale_window if task is not None else None


def validate(tasks: Sequence[Task]) -> None:
    """The registry invariant: ids are unique, non-empty, and every row is a
    Task. Raises ValueError -- at import for the real registry, so a
    duplicated id cannot reach a tick."""
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, Task):
            raise ValueError(f"registry rows must be Task, got {type(task).__name__}")
        if not task.id or not task.id.strip():
            raise ValueError("a registry task has an empty id")
        if task.id in seen:
            raise ValueError(f"duplicate task id in registry: {task.id!r}")
        seen.add(task.id)


# --------------------------------------------------------------------------
# Runners
# --------------------------------------------------------------------------


def _network_disabled() -> bool:
    """One switch, honoured everywhere PIPELINES.md schedules a fetch.

    Same rule and same reporting as ``deadline_dag.odds_refresh``: a gated
    run is ``no_source`` -- an honest gap -- never a fake success.
    """
    return os.environ.get("FPL_EDGE_DISABLE_NETWORK_INGEST", "") not in ("", "0")


_GATED = TaskResult(
    outcome="no_source",
    detail="skipped: FPL_EDGE_DISABLE_NETWORK_INGEST is set; nothing was fetched",
)


def _steps_detail(steps: list[Step]) -> str:
    failed = [s.name for s in steps if not s.ok]
    detail = f"{len(steps) - len(failed)}/{len(steps)} steps ok"
    if failed:
        detail += "; failed: " + ",".join(failed)
    return detail


def run_post_gw_settlement(ctx: TaskContext) -> TaskResult:
    """The post-gameweek settlement chain as one composite calendar task.

    One task, ordered sub-steps, because that is exactly the shape
    ``post_gw.main`` has: a sequential chain in one process (single DuckDB
    writer), settlement before the crawls that read it. The step list is
    ``post_gw.settlement_steps`` -- THE list the CLI runs -- so the two paths
    cannot drift during the parity period.

    Outcome mirrors post_gw's alert contract: a clean run is ``quiet`` (an
    alert that arrives nightly is an alert nobody reads); a run with failed
    steps DELIVERS the same titled alert ``post_gw.notify_failures`` sends,
    through the DAG's own outbox path.
    """
    if _network_disabled():
        return _GATED
    from fpl_edge.jobs import post_gw

    report = post_gw.JobReport(started_utc=ctx.now.astimezone(UTC).isoformat())
    for name, argv in post_gw.settlement_steps(ctx.python):
        post_gw._run(report, name, argv)

    steps = [Step(name=s.name, ok=s.ok, seconds=s.seconds, detail=s.detail)
             for s in report.steps]
    detail = _steps_detail(steps)
    if report.ok:
        return TaskResult(outcome="quiet", detail=detail, steps=steps)
    title, body = post_gw.alert_text(report)
    return TaskResult(outcome="delivered", kind="alert", title=title, body=body,
                      detail=detail, steps=steps)


def run_transcribe_nightly(ctx: TaskContext) -> TaskResult:
    """Nightly budgeted transcription: captions first (they are near-free and
    the queue serves panel YouTube alongside podcasts), podcast ASR under a
    wall-clock budget, the deterministic relevance gate deciding what is
    worth the GPU (pipeline.py, ``--min-relevance``)."""
    if _network_disabled():
        return _GATED
    budget = float(os.environ.get("FPL_EDGE_TRANSCRIBE_BUDGET_S",
                                  TRANSCRIBE_BUDGET_S))
    step = run_step(
        "content_transcribe",
        [ctx.python, "-m", "fpl_edge.ingest.content.pipeline", "transcribe",
         "--budget-s", str(budget)],
        timeout=budget + 900,
    )
    if step.ok:
        return TaskResult(outcome="quiet", detail=step.detail[-300:], steps=[step])
    return TaskResult(
        outcome="delivered", kind="alert", steps=[step],
        detail=f"transcribe failed: {step.detail[-200:]}",
        title="Nightly transcription FAILED",
        body=f"content_transcribe exited non-zero after {step.seconds}s.\n\n"
             f"{step.detail}",
    )


def run_fpl_core_insights(ctx: TaskContext) -> TaskResult:
    """Daily per-match xG from FPL-Core-Insights, after the settlement slot
    so the gameweeks it fetches are the ones settlement just closed."""
    if _network_disabled():
        return _GATED
    step = run_step(
        "fpl_core_insights",
        [ctx.python, "-m", "fpl_edge.ingest.fpl_core_insights",
         "--season", ctx.season],
    )
    outcome = "quiet" if step.ok else "error"
    return TaskResult(outcome=outcome, detail=step.detail[-300:], steps=[step])


def run_fast_rss(ctx: TaskContext) -> TaskResult:
    """4-hourly ingest of the fast tier only: the panel creators' feeds.

    Conditional-fetch cheap by construction (backfill-days 1, a handful of
    sources), and panel-creator caption transcription rides the same firing --
    captions are ~286x realtime, so a small budget covers everything the
    ingest just landed. Podcast ASR stays on the nightly task; this rung
    deliberately never downloads audio (``--kinds youtube``).

    A failed fetch is an ``error`` outcome, never ``quiet`` -- the odds-ladder
    lesson: a refresh that fetched nothing must go red in dag_firing.
    """
    if _network_disabled():
        return _GATED
    from fpl_edge.ingest.content.sources import fast_tier

    sources = fast_tier()
    if not sources:
        return TaskResult(outcome="no_source", detail="no fast-tier sources registered")
    keys = ",".join(s.key for s in sources)
    steps = [run_step(
        "ingest_fast_rss",
        [ctx.python, "-m", "fpl_edge.ingest.content.pipeline", "ingest",
         "--backfill-days", "1", "--only", keys],
    )]
    steps.append(run_step(
        "captions_fast",
        [ctx.python, "-m", "fpl_edge.ingest.content.pipeline", "transcribe",
         "--kinds", "youtube", "--since", "2", "--budget-s", "300"],
        timeout=600,
    ))
    detail = f"{len(sources)} fast-tier sources; " + _steps_detail(steps)
    outcome = "quiet" if all(s.ok for s in steps) else "error"
    return TaskResult(outcome=outcome, detail=detail, steps=steps)


def run_audio_retention(ctx: TaskContext) -> TaskResult:
    """Weekly sweep of the ASR audio cache (PIPELINES.md §3 defect 3).

    Deletes ONLY audio whose item holds a stored transcript AND a
    ``transcript_provenance`` row carrying ``audio_sha256`` -- the hash
    outlives the file, so integrity survives the deletion. Everything else in
    the cache is kept, always. No network; the gate does not apply. The
    deletion count rides to the fetch ledger as ``rows_written``.
    """
    from fpl_edge.ingest.content import asr

    with ctx.read() as wh:
        sweep = asr.sweep_audio_cache(wh, dry_run=False)
    return TaskResult(
        outcome="quiet",
        detail=sweep.summary(),
        ledger_written=len(sweep.deleted),
    )


# --------------------------------------------------------------------------
# THE registry. Adding authority is adding one row here. Nothing else runs.
# --------------------------------------------------------------------------

TASKS: tuple[Task, ...] = (
    # ---- the five original DAG tasks, scheduled by the DAG itself ---------
    # (scheduled_by_dag=True: due instants still come from deadline_dag's own
    # due_tasks/DEADLINE_OFFSETS/ODDS_LADDER, so behaviour -- and every
    # existing test -- is byte-identical. The rows here are their identity.)
    Task(
        id="presser_projection_refresh",
        description="T-30h: ingest live/odds-fixtures/content/projections + injury digest",
        due=DeadlineRelative(hours_before=30),
        stale_window=dag.STALE_WINDOWS["presser_projection_refresh"],
        run=dag.presser_projection_refresh,
        scheduled_by_dag=True,
    ),
    Task(
        id="price_radar",
        description="02:00 Europe/London: net-transfer velocity radar, deterministic",
        due=Calendar(hour_local=dag.NIGHTLY_LOCAL_HOUR, tz="Europe/London"),
        stale_window=dag.STALE_WINDOWS["price_radar"],
        run=dag.price_radar,
        scheduled_by_dag=True,
    ),
    Task(
        id="final_solve_delivery",
        description="T-4h: deliver the freshest stored plan (never solves)",
        due=DeadlineRelative(hours_before=4),
        stale_window=dag.STALE_WINDOWS["final_solve_delivery"],
        run=dag.final_solve_delivery,
        scheduled_by_dag=True,
    ),
    Task(
        id="lineup_captain_check",
        description="T-90m: confirmed XI vs picked captain (Pulselive teamsheets)",
        due=DeadlineRelative(hours_before=1.5),
        stale_window=dag.STALE_WINDOWS["lineup_captain_check"],
        run=dag.lineup_captain_check,
        scheduled_by_dag=True,
    ),
    Task(
        id="odds_refresh",
        description="Odds ladder T-36h/T-12h/T-5h; extras once per GW at T-36h",
        due=DeadlineRelative(hours_before=(36.0, 12.0, 5.0)),
        stale_window=dag.STALE_WINDOWS["odds_refresh"],
        run=dag.odds_refresh,
        credits_estimate=12.0,
        confirm_required=True,
        scheduled_by_dag=True,
    ),
    # ---- post_gw folded in (PIPELINES.md §6.2) ----------------------------
    # 10:30 UTC lands in the plist's intended slot ("after FPL finalises
    # points at 09:00 UK"; the 03:00-US-local plist lands 10:00-11:00 UTC).
    # The plist keeps firing during the parity window -- every step is
    # idempotent, so the doubled run is safe and comparable.
    Task(
        id="post_gw_settlement",
        description="Daily settlement chain: live snapshot, results, projections, "
                    "scoring, odds top-up, content ingest, cohort crawls, reports",
        due=Calendar(hour_utc=10, minute=30),
        stale_window=dt.timedelta(hours=12),
        run=run_post_gw_settlement,
    ),
    # ---- the previously-manual pipelines (PIPELINES.md §6.3) --------------
    Task(
        id="fpl_core_insights",
        description="Daily per-match xG (FPL-Core-Insights), after settlement's slot",
        due=Calendar(hour_utc=11, minute=30),
        stale_window=dt.timedelta(hours=12),
        run=run_fpl_core_insights,
    ),
    Task(
        id="content_transcribe",
        description="Nightly budgeted transcription: captions first, podcast ASR "
                    "behind the deterministic relevance gate",
        due=Calendar(hour_utc=12, minute=0),
        stale_window=dt.timedelta(hours=6),
        run=run_transcribe_nightly,
        budget_s=TRANSCRIBE_BUDGET_S,
    ),
    Task(
        id="content_fast_rss",
        description="4-hourly ingest of panel creators' feeds (fast tier) + "
                    "immediate panel caption transcription",
        due=Interval(hours=4),
        # Deliberately SHORTER than the 4h gap between rungs, the odds-ladder
        # rule: a slept-through rung is dropped and the next one does the work.
        stale_window=dt.timedelta(hours=3),
        run=run_fast_rss,
    ),
    Task(
        id="audio_retention",
        description="Weekly ASR audio-cache sweep: delete only after stored "
                    "transcript + provenance with audio_sha256",
        due=Interval(hours=24 * 7),
        stale_window=dt.timedelta(hours=24),
        run=run_audio_retention,
    ),
)

validate(TASKS)
