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
from pathlib import Path
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

#: How much longer than its budget the transcription PROCESS is allowed to
#: live before ``run_step`` kills it.
#:
#: The budget is checked between items, never inside one, so the worst-case
#: overrun is exactly the longest single item. Measured on this machine
#: 2026-09-03: MLX-Whisper runs at 11.7-12.3x realtime (107.5 min of audio in
#: 549s; 26.8 min in 131s), and the panel's longest regular episodes run past
#: two hours -- 150 min of audio is ~770s of ASR before the download. The old
#: grace was 900s, which a single long episode can exceed on its own, and
#: that is what the 2026-09-03 ledger row "timed out after 4500s" was: not a
#: hung process, a budget of 3600s plus one episode that did not fit in the
#: 900s left for it. 1800s covers the longest episode in the registry with
#: room for its download.
TRANSCRIBE_GRACE_S = 1800.0

#: Default wall-clock budget for ONE claim-extraction pass, seconds.
#: Overridable per-deploy with FPL_EDGE_ANALYSE_BUDGET_S.
#:
#: 30 minutes is a bound, not a target. ``pipeline analyze`` is resumable by
#: construction -- its queue is "items with no content_analysis row for this
#: model", and every finished item writes one -- so a run that stops at the
#: budget leaves the rest of the backlog for the next firing instead of
#: needing one enormous run. Two firings a day at 30 minutes is what drains a
#: backlog without ever holding the machine for an hour.
ANALYSE_BUDGET_S = 1800.0

#: How many days back the daily analyse pass looks. The catch-up firing
#: overrides this to 0 -- "every stored item, oldest gap first" -- which is
#: the row that actually eats a backlog.
ANALYSE_SINCE_DAYS = 21


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
    #: Grouping for the control panel, following PIPELINES.md §1's map:
    #: core / odds / settlement / results / content / maintenance.
    family: str = "core"


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
    (the parity test in tests/unit/test_pipelines_registry.py holds the two equal).
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
    Task. Raises at import for the real registry, so a duplicated id cannot
    reach a tick."""
    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, Task):
            raise TypeError(f"registry rows must be Task, got {type(task).__name__}")
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

    The failing outcome is ``error``, not ``delivered``. Both deliver -- see
    ``TaskResult.delivers`` -- but only ``error`` maps to an ``error`` row in
    the ledger (``runner.LEDGER_STATUS``). Returning ``delivered`` here sent
    the alert AND wrote status ``ok``, so the Pipelines panel showed green
    for a settlement chain that had failed steps.
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
    return TaskResult(outcome="error", kind="alert", title=title, body=body,
                      detail=detail, steps=steps)


def run_transcribe_nightly(ctx: TaskContext) -> TaskResult:
    """Nightly budgeted transcription: captions first (they are near-free and
    the queue serves panel YouTube alongside podcasts), podcast ASR under a
    wall-clock budget, the deterministic relevance gate deciding what is
    worth the GPU (pipeline.py, ``--min-relevance``).

    A failed step is ``error``. It used to be ``delivered``, which
    ``runner.LEDGER_STATUS`` maps to ledger status ``ok`` -- so three
    consecutive nights of failure (2026-09-01, -02, -03) are recorded in
    ``fetch_run`` as successful runs whose note begins "delivered: transcribe
    failed:". The alert still goes out: ``TaskResult.delivers`` admits
    ``error`` precisely so honesty in the ledger does not cost a
    notification."""
    if _network_disabled():
        return _GATED
    budget = float(os.environ.get("FPL_EDGE_TRANSCRIBE_BUDGET_S",
                                  TRANSCRIBE_BUDGET_S))
    step = run_step(
        "content_transcribe",
        [ctx.python, "-m", "fpl_edge.ingest.content.pipeline", "transcribe",
         "--budget-s", str(budget)],
        timeout=budget + TRANSCRIBE_GRACE_S,
    )
    if step.ok:
        return TaskResult(outcome="quiet", detail=step.detail[-300:], steps=[step])
    return TaskResult(
        outcome="error", kind="alert", steps=[step],
        detail=f"transcribe failed: {step.detail[-200:]}",
        title="Nightly transcription FAILED",
        body=f"content_transcribe exited non-zero after {step.seconds}s.\n\n"
             f"{step.detail}",
    )


def _table_rows(ctx: TaskContext, table: str) -> int | None:
    """``count(*)`` for one table, or None if the warehouse cannot be read.

    Used to turn a subprocess step into a ledger count. The steps here write
    through their own connections, so nothing they do is visible to the
    TaskResult unless it is measured either side -- which is why
    ``content_fast_rss`` has 21 ledger rows all reading ``rows_written 0``
    while the same runs were landing items.
    """
    try:
        with ctx.read() as wh:
            exists = int(wh.sql(
                "SELECT count(*) c FROM information_schema.tables "
                "WHERE table_name = ?", [table]).iloc[0]["c"])
            if not exists:
                return 0
            return int(wh.sql(f"SELECT count(*) c FROM {table}").iloc[0]["c"])
    except Exception:  # noqa: BLE001 - a count is not worth failing a run over
        return None


def _grew_by(before: int | None, after: int | None) -> int:
    if before is None or after is None or after < before:
        return 0
    return after - before


def _content_analysis_rows(ctx: TaskContext) -> int | None:
    """How many rows ``content_analysis`` holds, or None if unreadable.

    Read either side of the analyse step so the ledger's ``rows_written``
    is a measured delta rather than a number scraped out of stdout. An
    unreadable warehouse yields None and the ledger simply records no count;
    it never blocks or fails the run.
    """
    return _table_rows(ctx, "content_analysis")


def _run_analyse(ctx: TaskContext, *, since_days: int, label: str) -> TaskResult:
    """One budgeted, resumable claim-extraction pass over stored text.

    THE missing rung. Discovery (``content_fast_rss``, ``post_gw_settlement``)
    and transcription (``content_transcribe``) were both scheduled; the step
    that turns stored text into ``content_analysis`` / ``content_claim`` rows
    never was, so it only ever ran when somebody typed it. On 2026-09-03 that
    showed as 773 stored items, 122 analyses, and a newest analysis dated
    2026-08-27 -- a week of "the Creators tab is stale" caused entirely by a
    missing registry row.

    Three properties make this safe to schedule rather than run by hand:

    * **Bounded.** ``--budget-s`` is a wall-clock stop checked between items,
      so the firing cannot run into the next one.
    * **Resumable.** ``pipeline analyze`` queues exactly the items with no
      ``content_analysis`` row for this model and no
      ``content_analysis_skip`` verdict, so a run that stops at the budget
      leaves a smaller queue behind and the next firing continues from there.
      Draining a backlog is many bounded runs, never one giant one.
    * **Free of metered credits.** The backend is the Max-plan ``claude``
      CLI (``ingest/content/analyze.py``), so ``credits_estimate`` is 0 and
      the cost that matters is the wall clock, which is what is budgeted.

    The model call leaves the machine, so the network kill-switch gates this
    exactly as it gates the fetchers.
    """
    if _network_disabled():
        return _GATED
    budget = float(os.environ.get("FPL_EDGE_ANALYSE_BUDGET_S", ANALYSE_BUDGET_S))
    before = _content_analysis_rows(ctx)
    step = run_step(
        "content_analyse",
        [ctx.python, "-m", "fpl_edge.ingest.content.pipeline", "analyze",
         "--since", str(since_days), "--budget-s", str(budget)],
        # The command stops itself at the budget between items; the process
        # timeout is the backstop for one call that hangs, never the plan.
        timeout=budget + 600,
    )
    after = _content_analysis_rows(ctx)
    written = _grew_by(before, after)
    detail = f"{label}; +{written} analyses; {step.detail[-200:]}"
    if step.ok:
        return TaskResult(outcome="quiet", detail=detail, steps=[step],
                          ledger_written=written)
    return TaskResult(
        outcome="error", kind="alert", steps=[step], detail=detail,
        ledger_written=written,
        title="Claim extraction FAILED",
        body=f"content_analyse exited non-zero after {step.seconds}s.\n\n"
             f"{step.detail}",
    )


def run_content_analyse(ctx: TaskContext) -> TaskResult:
    """The daily pass: the last three weeks, right after transcription.

    Fresh-first. The transcription slot is 12:00 UTC with a 3600s budget, so
    13:30 is after it on any night it behaves and still before the evening.
    """
    return _run_analyse(ctx, since_days=ANALYSE_SINCE_DAYS,
                        label=f"last {ANALYSE_SINCE_DAYS}d")


def run_content_analyse_backlog(ctx: TaskContext) -> TaskResult:
    """The second pass, overnight: no window at all, so it eats the backlog.

    Same runner, same budget, one different flag. ``--since 0`` drops the
    21-day filter, which is what lets the 644 never-analysed items -- some of
    them seasons old, and the only place a creator's *measured* hit rate can
    come from before a gameweek is played -- actually get read.
    """
    return _run_analyse(ctx, since_days=0, label="full backlog")


def run_forecast_refresh(ctx: TaskContext) -> TaskResult:
    """Roll the committed points forecast forward to the next open horizon.

    ``fpl recommend`` (the squad-anchored solver behind the dashboard) reads
    ``forecast.parquet``, and only ``fpl solve`` writes it. Nothing scheduled
    that, so on 2026-09-07 the forecast still covered GW3-7 while the solve
    wanted GW4-8 and refused to score 32 unprojected players as zero. The
    same never-scheduled-artefact failure as claim extraction and the
    ratings refit. --forecast-only: the forecast is the model fit, not the MILP, so
    no plan is solved and a solver with no incumbent can never lose it. Local warehouse only; no network gate.

    ``--forecast-source consensus``: the equal-weight provider mean, the number
    every other dashboard surface shows. The engine's own model ran ~40% hot
    against it in every position on 2026-09-07 and sold a GK for one it rated
    29.2 over five gameweeks against the providers' 12.1; the solver was
    optimising a currency the owner never saw. Equal weights, not earned:
    three gameweeks of scoring is too thin a track record to default to.
    Uncovered (player, gw) rows are filled from the engine model and labelled.
    """
    step = run_step(
        "forecast_refresh",
        [ctx.python, "-m", "fpl_edge.cli.main", "solve", "--db", str(ctx.db_path),
         "--forecast-only", "--forecast-source", "consensus", "--horizon", "5"],
        timeout=1800,
    )
    outcome = "quiet" if step.ok else "error"
    return TaskResult(outcome=outcome, detail=step.detail[-300:], steps=[step])


def run_fixture_ratings_refit(ctx: TaskContext) -> TaskResult:
    """Refit the Dixon-Coles club split the Fixtures board colours from.

    ``fixture_ratings.parquet`` was only ever written by hand
    (``python -m fpl_edge.platform.scripts.fixtures --build``), so it sat
    227 hours stale while the nightly job refreshed the DEPRECATED blended
    file instead. The fit reads the local warehouse only and takes about
    2.5 seconds, so it runs daily after settlement and on demand. No network
    gate: nothing here leaves the machine.
    """
    step = run_step(
        "fixture_ratings_refit",
        # --db is not optional: without it the build writes beside DEFAULT_DB,
        # and a unit test that ran this task rebuilt the REAL artefact from
        # inside the suite (fitted_at 2026-09-07 08:35 UTC, a pytest run).
        [ctx.python, "-m", "fpl_edge.platform.scripts.fixtures", "--build",
         "--db", str(ctx.db_path)],
        timeout=300,
    )
    outcome = "quiet" if step.ok else "error"
    return TaskResult(outcome=outcome, detail=step.detail[-300:], steps=[step])


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


def run_panel_picks_crawl(ctx: TaskContext) -> TaskResult:
    """The creator panel's own teams, from ``panel_person``.

    Every active person with a verified entry id gets picks, transfers,
    chips and gameweek history through the rivals ingest path
    (:mod:`fpl_edge.ingest.rivals.panel_picks`). This exists because the
    cohort crawls select by league and rank and never read the panel, so
    the Creators page could show squads only for the panel members who
    happened to be in a crawled cohort -- 7 of 15 on 2026-09-07.

    Runs at 11:15 UTC, after the settlement slot, AND as a step inside the
    settlement chain (``post_gw.settlement_steps``): on a settlement day the
    standalone firing replays from cache for almost nothing. The budget is
    the module's own default (700), sized for a cold full-season crawl; a
    steady-state run is ~2 requests per person.

    A person the API refuses, a name that no longer matches the verified
    roster, or a write that did not commit exits the module non-zero, so
    the outcome here is ``error`` -- never a fake ``quiet`` for a crawl that
    covered fourteen of fifteen.
    """
    if _network_disabled():
        return _GATED
    step = run_step(
        "panel_picks_crawl",
        [ctx.python, "-m", "fpl_edge.ingest.rivals.panel_picks",
         "--db", str(ctx.db_path)],
        timeout=1200.0,
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
    before = _table_rows(ctx, "content_item")
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
    # Measured, not assumed. Every one of this task's 21 ledger rows to
    # 2026-09-03 says rows_written 0, because the items are written by the
    # subprocess through its own connection and nothing here ever counted
    # them. A ledger column that is structurally always zero teaches a reader
    # to ignore it.
    landed = _grew_by(before, _table_rows(ctx, "content_item"))
    detail = (f"{len(sources)} fast-tier sources; +{landed} items; "
              + _steps_detail(steps))
    outcome = "quiet" if all(s.ok for s in steps) else "error"
    return TaskResult(outcome=outcome, detail=detail, steps=steps,
                      ledger_written=landed)


def run_briefing_intel(ctx: TaskContext) -> TaskResult:
    """The model-authored salience pass over the panels (briefing_intel.py).

    One in-process call: assemble the panel context, ask the Max-plan CLI
    once through claude-agent-sdk, validate every item against the inputs,
    write the sibling artefact atomically. The panels only read the local
    warehouse, but the model call itself leaves the machine, so the
    kill-switch gates this task exactly like the fetching ones — a gated
    unit-test tick must never spawn the CLI. A failure raises out of
    ``generate`` and the runner records the ledger row as ``error`` with the
    reason — the failure-honesty contract. Kept items ride to the ledger as
    ``rows_written``.
    """
    if _network_disabled():
        return _GATED
    from fpl_edge.platform import briefing_intel

    artefact = briefing_intel.generate(ctx.db_path, season=ctx.season,
                                       now=ctx.now)
    kept = len(artefact.get("items") or [])
    rejected = int(artefact.get("rejected_n") or 0)
    return TaskResult(
        outcome="quiet",
        detail=(f"{kept} item(s) kept, {rejected} rejected, "
                f"meta_prompt {artefact.get('meta_prompt_hash')}, "
                f"{artefact.get('duration_s')}s"),
        ledger_written=kept,
    )


def run_audio_retention(ctx: TaskContext) -> TaskResult:
    """Weekly sweep of the ASR audio cache (PIPELINES.md §3 defect 3).

    Deletes ONLY audio whose item holds a stored transcript AND a
    ``transcript_provenance`` row carrying ``audio_sha256`` -- the hash
    outlives the file, so integrity survives the deletion. Everything else in
    the cache is kept, always. No network; the gate does not apply. The
    deletion count rides to the fetch ledger as ``rows_written``.
    """
    from fpl_edge.ingest.content import asr

    # The cache dir follows the DATABASE THE RUN USED. asr.AUDIO_CACHE is a
    # repo-relative constant, so a unit test ticking this task against a tmp
    # warehouse swept the REAL data/raw/content/asr_audio (312 such runs by
    # 2026-09-07); only the rule "delete nothing without a provenance row"
    # kept those sweeps at zero deletions. The real database resolves to the
    # real directory unchanged.
    cache_dir = Path(ctx.db_path).parent.parent / "raw" / "content" / "asr_audio"
    with ctx.read() as wh:
        sweep = asr.sweep_audio_cache(wh, dry_run=False, cache_dir=cache_dir)
    return TaskResult(
        outcome="quiet",
        detail=sweep.summary(),
        ledger_written=len(sweep.deleted),
    )


# --------------------------------------------------------------------------
# THE registry. Adding authority is adding one row here. Nothing else runs.
# --------------------------------------------------------------------------

    # Stale windows: a task whose value does not decay within the day keeps a
    # 23h window, so a tick that slept through the due instant (the Mac lid
    # was down on Aug 31 and Sep 6) runs late instead of skipping the day.
    # Time-sensitive tasks (prices, odds, deadline-relative) keep tight ones.
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
        family="odds",
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
        stale_window=dt.timedelta(hours=23),
        run=run_post_gw_settlement,
        family="settlement",
    ),
    # ---- the previously-manual pipelines (PIPELINES.md §6.3) --------------
    Task(
        id="fpl_core_insights",
        description="Daily per-match xG (FPL-Core-Insights), after settlement's slot",
        due=Calendar(hour_utc=11, minute=30),
        stale_window=dt.timedelta(hours=12),
        run=run_fpl_core_insights,
        family="results",
    ),
    Task(
        id="panel_picks_crawl",
        description="Daily picks/transfers/chips/history for every verified, "
                    "active panel_person, after the settlement slot",
        due=Calendar(hour_utc=11, minute=15),
        # 23h, not 12h: the tick sleeps with the lid (see the comment above
        # TASKS), and yesterday's squads are still yesterday's squads.
        stale_window=dt.timedelta(hours=23),
        run=run_panel_picks_crawl,
        family="settlement",
    ),
    Task(
        id="content_transcribe",
        description="Nightly budgeted transcription: captions first, podcast ASR "
                    "behind the deterministic relevance gate",
        due=Calendar(hour_utc=12, minute=0),
        stale_window=dt.timedelta(hours=6),
        run=run_transcribe_nightly,
        budget_s=TRANSCRIBE_BUDGET_S,
        family="content",
    ),
    Task(
        id="content_analyse",
        description="Claim extraction over the last 21 days, 30m budget, "
                    "resumable; runs after the nightly transcription slot",
        due=Calendar(hour_utc=13, minute=30),
        # Shorter than the 12h gap to the backlog firing: a slept-through
        # analyse is dropped and the next one does the work, because the
        # queue it reads is the same either way.
        stale_window=dt.timedelta(hours=23),
        run=run_content_analyse,
        budget_s=ANALYSE_BUDGET_S,
        family="content",
    ),
    Task(
        id="content_analyse_backlog",
        description="Second daily claim-extraction pass with no date window, "
                    "30m budget: chews the never-analysed backlog",
        due=Calendar(hour_utc=1, minute=30),
        stale_window=dt.timedelta(hours=23),
        run=run_content_analyse_backlog,
        budget_s=ANALYSE_BUDGET_S,
        family="content",
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
        family="content",
    ),
    Task(
        id="forecast_refresh",
        description="Daily model fit + committed points forecast for the next "
                    "open horizon, the artefact the squad-anchored solver reads.",
        due=Calendar(hour_utc=11, minute=30),
        stale_window=dt.timedelta(hours=23),
        run=run_forecast_refresh,
    ),
    Task(
        id="fixture_ratings_refit",
        description="Daily Dixon-Coles refit of the club attack/defence split "
                    "the Fixtures board reads; 2.5s, local only.",
        due=Calendar(hour_utc=11, minute=0),
        stale_window=dt.timedelta(hours=23),
        run=run_fixture_ratings_refit,
    ),
    Task(
        id="briefing_intel",
        # The registry admits ONE due shape per task, so this rides Calendar
        # (07:40 local, after the morning fetches); on-demand triggering is
        # the runner's existing manual seam — POST /api/pipelines/
        # briefing_intel/run calls runner.run_task exactly like any task.
        description="Model-authored salience pass over the panels; artefact "
                    "clearly labelled, never merged into dashboard_brief.",
        due=Calendar(hour_local=7, minute=40, tz="Europe/London"),
        stale_window=dt.timedelta(hours=23),
        run=run_briefing_intel,
        family="core",
    ),
    Task(
        id="audio_retention",
        description="Weekly ASR audio-cache sweep: delete only after stored "
                    "transcript + provenance with audio_sha256",
        due=Interval(hours=24 * 7),
        stale_window=dt.timedelta(hours=24),
        run=run_audio_retention,
        family="maintenance",
    ),
)

validate(TASKS)
