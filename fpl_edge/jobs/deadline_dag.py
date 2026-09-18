"""The deadline DAG: an event-relative scheduler for the pre-deadline passes.

Argus schedules on cron strings; FPL schedules on *deadlines*, which move
(BGW/DGW, TV rescheduling, a Friday 18:30 kickoff). Everything downstream of
that one difference transfers unchanged, which is why this file is small: the
seam is `due_tasks()`, and the rest is Argus's tick loop with overlap-skip,
stale-forward and outcome rows (docs/platform/argus_architecture.md §4.2,
DESIGN.md §2 rules 3-5, §3 the offsets table).

Four tasks, all keyed off `dim_event.deadline_utc`:

    T-30h    presser_projection_refresh  ingest + injury digest
    02:00 UK price_radar                 net-transfer velocity, deterministic
    T-4h     final_solve_delivery        deliver the freshest stored plan
    T-90m    lineup_captain_check        confirmed XI vs picked captain

**UTC is the only time authority.** Every due instant is computed from the
API's UTC deadline. Europe/London appears exactly once, for the nightly radar,
because "2am" is a wall-clock statement about when FPL's price run happens and
a UTC offset would drift an hour twice a year. `zoneinfo` handles the DST
arithmetic; 02:00 London is well-defined on both transition days (the spring
gap is 01:00-01:59 and the autumn ambiguity is 01:00-01:59).

**Nothing bursts.** A firing due more than STALE_WINDOW ago is recorded
`skipped_stale` and never run: a laptop that slept through Friday must not wake
on Saturday and fire Friday's pre-deadline alert as if it were news.

**Nothing double-sends.** The firing row is claimed *before* the task runs
(`INSERT ... ON CONFLICT DO NOTHING RETURNING`), so an overlapping manual tick,
a double launchd dispatch, or a restart mid-task cannot deliver twice. A row
left `running` by a crash is interrupted-not-retried, by design.

**Deterministic triggers, LLM copy only.** `price_radar` decides from arithmetic
over two warehouse snapshots. The LLM -- headless `claude -p` -- is offered the
already-decided title and body and may rewrite the prose; if it fails, times
out, or is absent, the deterministic text is delivered unchanged. No trigger
calls a model. (argus_architecture.md §4.1.)

**The write lock is held only in bursts.** DuckDB permits one writer, the
Telegram bot takes leases, and the ingest steps this job launches are writers
themselves. So the runner opens the warehouse to claim, closes it, runs the
subprocess steps, and reopens to record the outcome and enqueue the delivery.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from fpl_edge.jobs import outbox
from fpl_edge.pipelines import registry, runner
from fpl_edge.pipelines.contracts import (
    LOOKBACK,
    NIGHTLY_LOCAL_HOUR,
    NIGHTLY_TASK,
    ODDS_TASK,
    SEASON,
    STALE_WINDOW,
    STALE_WINDOWS,
    Due,
    TaskContext,
)
from fpl_edge.pipelines.tasks import TASKS
from fpl_edge.store import DEFAULT_DB, Warehouse

log = logging.getLogger("fpl_edge.jobs.deadline_dag")

UTC = dt.UTC
LONDON = ZoneInfo("Europe/London")


MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOG_DIR = Path("data/warehouse/jobs")

#: The headless Claude CLI used by :func:`polish_copy`, after a decision.
CLAUDE_BIN = Path.home() / ".local" / "bin" / "claude"

#: Deadline-relative tasks: task name -> how long BEFORE the deadline it fires.
#: These are the DESIGN.md §3 offsets, and they are the whole schedule spec.
DEADLINE_OFFSETS: dict[str, dt.timedelta] = {
    "presser_projection_refresh": dt.timedelta(hours=30),
    "final_solve_delivery": dt.timedelta(hours=4),
    "lineup_captain_check": dt.timedelta(minutes=90),
}


#: The ladder, and the reasoning behind each rung. Bookmakers reprice
#: continuously, but the *information* arrives in lumps, and the rungs are
#: placed at the lumps rather than spread evenly:
#:
#: * **T-36h** -- after midweek results have settled and the first team news
#:   leaks, and far enough out that a stale-forward (the machine slept) still
#:   lands usefully. This is the rung that also refreshes the extra markets.
#: * **T-12h** -- the morning of a Friday-evening deadline, after overnight
#:   moves and the first press conferences.
#: * **T-5h** -- after the last pressers, and deliberately ONE HOUR BEFORE
#:   ``final_solve_delivery`` at T-4h. A refresh that lands after the plan has
#:   been delivered has bought nothing for this deadline; the ordering here is
#:   the whole point of the rung, not an incidental detail.
#:
#: **Cost.** One refresh is 2 credits (featured h2h+totals) plus 1 per fixture
#: priced, and the CLI's horizon restricts that to the fixtures before the next
#: deadline: 12 credits for a 10-fixture gameweek, measured 2026-08-28. Three
#: rungs is 36 credits a gameweek. The nightly ``post_gw`` top-up is gated at
#: ``--max-age-hours 48`` so it only fires in the gap between ladders -- about
#: twice a week, 24 more. ~60 a gameweek, ~258 in a 4.3-gameweek month, against
#: a measured free tier of 500. See ``docs/data_sources.md`` §2.4.
#:
#: Why not more rungs: the binding constraint is not the credit budget, it is
#: that a refresh nobody reads before the deadline bought nothing. Every rung
#: here sits immediately before something that consumes odds.
ODDS_LADDER: tuple[dt.timedelta, ...] = (
    dt.timedelta(hours=36),
    dt.timedelta(hours=12),
    dt.timedelta(hours=5),
)

def stale_window_for(task: str) -> dt.timedelta:
    """The staleness budget for one task. See :data:`STALE_WINDOWS`.

    Registry tasks (fpl_edge/pipelines/registry.py) declare their own window on
    their Task row; this lookup consults it so there is ONE staleness answer
    per task everywhere: the tick, the tests, and the panel.
    """
    if task in STALE_WINDOWS:
        return STALE_WINDOWS[task]
    window = registry.stale_window_of(task)
    return window if window is not None else STALE_WINDOW




def nightly_instants(
    now: dt.datetime, *, lookback: dt.timedelta = LOOKBACK, hour: int = NIGHTLY_LOCAL_HOUR
) -> list[dt.datetime]:
    """Every ``hour``:00 Europe/London instant in (now - lookback, now], in UTC.

    Built by walking local dates and converting each, rather than by adding 24h
    repeatedly: on a DST boundary the gap between consecutive 02:00 London
    instants is 23 or 25 hours, and only the calendar walk gets that right.
    """
    now = now.astimezone(UTC)
    start = now - lookback
    out: list[dt.datetime] = []
    local_date = (start.astimezone(LONDON) - dt.timedelta(days=1)).date()
    end_date = now.astimezone(LONDON).date()
    while local_date <= end_date:
        local = dt.datetime(
            local_date.year, local_date.month, local_date.day, hour, 0, tzinfo=LONDON
        )
        inst = local.astimezone(UTC)
        if start < inst <= now:
            out.append(inst)
        local_date += dt.timedelta(days=1)
    return sorted(out)


def due_tasks(
    deadlines: Sequence[tuple[int, dt.datetime]],
    now: dt.datetime,
    *,
    season: str = SEASON,
    lookback: dt.timedelta = LOOKBACK,
    stale_window: dt.timedelta | None = None,
) -> list[Due]:
    """Which firings are owed at ``now``, and which of those are already stale.

    ``deadlines`` is [(gw, deadline_utc)] -- one row per gameweek, the newest
    known deadline for it. A task is owed when its due instant has passed and
    the tick has not already recorded it; staleness is decided here rather than
    at the call site so the same rule applies to a launchd tick and a manual one.

    ``stale_window`` overrides the per-task budget for every task; leave it None
    to use :data:`STALE_WINDOWS`, which is what production wants.
    """
    def _window(task: str) -> dt.timedelta:
        return stale_window if stale_window is not None else stale_window_for(task)

    now = now.astimezone(UTC)
    horizon = now - lookback
    out: list[Due] = []

    for gw, deadline in sorted(deadlines):
        deadline = deadline.astimezone(UTC)
        for task, offset in DEADLINE_OFFSETS.items():
            due = deadline - offset
            if not (horizon < due <= now):
                continue
            out.append(
                Due(
                    task=task,
                    season=season,
                    gw=int(gw),
                    due_utc=due,
                    deadline_utc=deadline,
                    stale=(now - due) > _window(task),
                )
            )

    # The odds ladder: one task, several firings per deadline. Kept out of the
    # DEADLINE_OFFSETS loop above because that loop's shape -- one offset per
    # task -- is what every other task and every existing test depends on.
    for gw, deadline in sorted(deadlines):
        deadline = deadline.astimezone(UTC)
        for offset in ODDS_LADDER:
            due = deadline - offset
            if not (horizon < due <= now):
                continue
            out.append(
                Due(
                    task=ODDS_TASK,
                    season=season,
                    gw=int(gw),
                    due_utc=due,
                    deadline_utc=deadline,
                    stale=(now - due) > _window(ODDS_TASK),
                )
            )

    # The nightly radar is not deadline-relative, but it is still filed under a
    # gameweek so the firing key matches the rest and the row reads sensibly.
    # The gameweek it belongs to is the one it is running up to.
    for inst in nightly_instants(now, lookback=lookback):
        gw = _gw_for_instant(deadlines, inst)
        out.append(
            Due(
                task=NIGHTLY_TASK,
                season=season,
                gw=gw,
                due_utc=inst,
                deadline_utc=_deadline_for_gw(deadlines, gw),
                stale=(now - inst) > _window(NIGHTLY_TASK),
            )
        )

    return sorted(out, key=lambda d: (d.due_utc, d.task))


def _gw_for_instant(deadlines: Sequence[tuple[int, dt.datetime]], inst: dt.datetime) -> int:
    """The gameweek an instant is running up to: the next deadline at or after it."""
    future = [(gw, d) for gw, d in deadlines if d.astimezone(UTC) >= inst]
    if future:
        return int(min(future, key=lambda p: p[1])[0])
    if deadlines:
        return int(max(deadlines, key=lambda p: p[1])[0])
    return 0


def _deadline_for_gw(
    deadlines: Sequence[tuple[int, dt.datetime]], gw: int
) -> dt.datetime | None:
    for g, d in deadlines:
        if int(g) == int(gw):
            return d.astimezone(UTC)
    return None


def next_due(
    deadlines: Sequence[tuple[int, dt.datetime]], now: dt.datetime
) -> list[tuple[str, dt.datetime]]:
    """The next firing of every task after ``now``. Reporting, not scheduling."""
    now = now.astimezone(UTC)
    out: list[tuple[str, dt.datetime]] = []
    for task, offset in DEADLINE_OFFSETS.items():
        future = [
            d.astimezone(UTC) - offset
            for _, d in deadlines
            if d.astimezone(UTC) - offset > now
        ]
        if future:
            out.append((task, min(future)))
    odds_future = [
        d.astimezone(UTC) - offset
        for _, d in deadlines
        for offset in ODDS_LADDER
        if d.astimezone(UTC) - offset > now
    ]
    if odds_future:
        out.append((ODDS_TASK, min(odds_future)))
    nxt = now + dt.timedelta(minutes=1)
    for _ in range(3):
        cand = nightly_instants(nxt + dt.timedelta(days=1), lookback=dt.timedelta(days=1))
        later = [c for c in cand if c > now]
        if later:
            out.append((NIGHTLY_TASK, min(later)))
            break
        nxt += dt.timedelta(days=1)
    return sorted(out, key=lambda p: p[1])


# --------------------------------------------------------------------------
# Warehouse plumbing
# --------------------------------------------------------------------------


def apply_migrations(wh) -> None:
    """Run the DAG's own DDL. Idempotent; every statement is IF NOT EXISTS."""
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        wh.sql(path.read_text())
    outbox.ensure_schema(wh)


def read_deadlines(db_path: Path | str = DEFAULT_DB, *, season: str = SEASON):
    """Deadlines from a private read-only copy: never blocks a writer.

    One row per gameweek, taking the newest `as_of` -- dim_event is append-only
    with a snapshot key, and a rescheduled deadline shows up as a later row.
    """
    with Warehouse.read_copy(db_path) as wh:
        df = wh.sql(
            "SELECT gw, deadline_utc FROM dim_event WHERE season = ? "
            "QUALIFY row_number() OVER (PARTITION BY gw ORDER BY as_of DESC) = 1 "
            "ORDER BY gw",
            [season],
        )
    return [
        (int(r.gw), r.deadline_utc.to_pydatetime()
         if hasattr(r.deadline_utc, "to_pydatetime") else r.deadline_utc)
        for r in df.itertuples(index=False)
    ]


def claim(wh, due: Due, *, now: dt.datetime, outcome: str = "running") -> bool:
    """Take ownership of a firing. True iff this process may run it.

    The whole idempotency design in one statement: the primary key is the
    firing's identity, so the first inserter wins and everybody else -- an
    overlapping tick, a relaunched job, a manual `--once` -- gets False and
    stands down.
    """
    df = wh.sql(
        "INSERT INTO dag_firing (task, season, gw, due_utc, fired_utc, outcome, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL) ON CONFLICT DO NOTHING RETURNING task",
        [due.task, due.season, due.gw, due.due_utc, now.astimezone(UTC), outcome],
    )
    return len(df) > 0


def _finish_sql(due: Due, outcome: str, detail: str) -> tuple[str, list]:
    return (
        (
            "UPDATE dag_firing SET outcome = ?, detail = ? "
            "WHERE task = ? AND season = ? AND gw = ? AND due_utc = ?"
        ),
        [outcome, detail[:800], due.task, due.season, due.gw, due.due_utc],
    )


def record_observations(
    wh, task: str, *, season: str, observed_utc: dt.datetime,
    rows: Iterable[tuple[int, str, float]],
) -> int:
    """Store the tuning series. Called on EVERY run, quiet ones included."""
    n = 0
    for code, metric, value in rows:
        wh.sql(
            "INSERT INTO dag_observation (task, observed_utc, season, code, metric, value) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
            [task, observed_utc.astimezone(UTC), season, int(code), metric, float(value)],
        )
        n += 1
    return n



# --------------------------------------------------------------------------
# LLM copy-polish. After the decision, never inside it.
# --------------------------------------------------------------------------


def polish_copy(title: str, body: str, *, timeout: float = 45.0) -> tuple[str, str]:
    """Best-effort prose polish through the headless Claude CLI.

    Three properties make this safe to have in a scheduler:

    1. It runs only on text that a deterministic task already decided to send.
    2. Any failure -- missing binary, timeout, nonzero exit, unparseable output,
       an empty rewrite -- returns the input unchanged. An alert is never
       dropped for presentation (argus_architecture.md §4.1).
    3. CLAUDECODE / CLAUDE_CODE_ENTRYPOINT are scrubbed from the child's
       environment. Inherited, they make the CLI believe it is nested inside an
       agent session and it behaves differently or refuses.
    """
    if not CLAUDE_BIN.exists():
        return title, body
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    prompt = (
        "Rewrite this Fantasy Premier League alert for a phone screen. Keep every "
        "number and name exactly as given; invent nothing; no markdown. Reply with "
        "JSON only: {\"title\": ..., \"body\": ...}.\n\n"
        f"TITLE: {title}\nBODY:\n{body}"
    )
    try:
        proc = subprocess.run(
            [str(CLAUDE_BIN), "-p", prompt],
            capture_output=True, text=True, timeout=timeout, check=False, env=env,
        )
        if proc.returncode != 0:
            return title, body
        text = proc.stdout.strip()
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return title, body
        obj = json.loads(text[start : end + 1])
        new_title = str(obj.get("title") or "").strip()
        new_body = str(obj.get("body") or "").strip()
        if not new_title or not new_body:
            return title, body
        return new_title[:200], new_body
    except Exception:  # noqa: BLE001 - polish is never worth a failed delivery
        log.info("copy polish unavailable; delivering deterministic text")
        return title, body







# --------------------------------------------------------------------------
# The tick
# --------------------------------------------------------------------------


@dataclass
class Fired:
    task: str
    gw: int
    due_utc: dt.datetime
    outcome: str
    detail: str = ""


@dataclass
class TickReport:
    now_utc: str
    season: str
    fired: list[Fired] = field(default_factory=list)
    skipped_overlap: list[str] = field(default_factory=list)
    next_due: list[tuple[str, str]] = field(default_factory=list)
    flush: str = ""

    def to_json(self) -> str:
        return json.dumps(
            {
                "now_utc": self.now_utc,
                "season": self.season,
                "fired": [vars(f) | {"due_utc": f.due_utc.isoformat()} for f in self.fired],
                "skipped_overlap": self.skipped_overlap,
                "next_due": [{"task": t, "due_utc": d} for t, d in self.next_due],
                "flush": self.flush,
            },
            indent=1,
        )


def tick(
    *,
    now: dt.datetime | None = None,
    season: str = SEASON,
    db_path: Path | str = DEFAULT_DB,
    send: bool = True,
    polish: bool | None = None,
    transport=None,
    config=None,
) -> TickReport:
    """One scheduler pass. Safe to call from launchd every 10 minutes.

    Deliberately opens and closes the writer several times: between the claim
    and the outcome the task's own subprocesses need the lock, and holding it
    across them would deadlock the job against itself.
    """
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    db_path = Path(db_path)
    if polish is None:
        polish = os.environ.get("FPL_EDGE_DAG_POLISH", "") not in ("", "0")

    deadlines = read_deadlines(db_path, season=season)
    report = TickReport(now_utc=now.isoformat(), season=season)
    report.next_due = [(t, d.isoformat()) for t, d in next_due(deadlines, now)]

    # The DAG's own five tasks, plus every registry task the DAG does not
    # schedule itself (calendar/interval rows in fpl_edge/pipelines/registry.py).
    # One owed list, one claim loop, one firing table -- the §5 decision.
    owed = sorted(
        due_tasks(deadlines, now, season=season)
        + registry.registry_due(deadlines, now, season=season),
        key=lambda d: (d.due_utc, d.task),
    )

    # Phase 1 -- claim. One short write burst for the whole tick.
    claimed: list[Due] = []
    with Warehouse(db_path, lock_timeout_s=180.0) as wh:
        apply_migrations(wh)
        for due in owed:
            if due.stale:
                # Recorded, never run. The row is the evidence that the machine
                # was down through this firing; burst-firing it now would page
                # the operator about a deadline that has already passed.
                detail = (
                    f"due {due.due_utc.isoformat()} is "
                    f"{(now - due.due_utc).total_seconds() / 3600:.1f}h old; "
                    f"stale window {STALE_WINDOW}"
                )
                if claim(wh, due, now=now, outcome="skipped_stale"):
                    stmt, params = _finish_sql(due, "skipped_stale", detail)
                    wh.sql(stmt, params)
                    report.fired.append(
                        Fired(task=due.task, gw=due.gw, due_utc=due.due_utc,
                              outcome="skipped_stale", detail=detail)
                    )
                continue
            if claim(wh, due, now=now, outcome="running"):
                claimed.append(due)
            else:
                report.skipped_overlap.append(f"{due.task}@{due.due_utc.isoformat()}")

    # Phase 2 -- run each claimed task with the lock free, through the ONE
    # execution path (fpl_edge/pipelines/runner.py): ledger record, timings,
    # captured logs. Runner lookup prefers this module's TASKS dict (the
    # legacy five; tests monkeypatch it) and falls back to the registry for
    # everything else.
    for due in claimed:
        ctx = TaskContext(
            season=season, gw=due.gw, due_utc=due.due_utc,
            deadline_utc=due.deadline_utc, now=now, db_path=db_path,
        )
        run_outcome = runner.execute(
            due.task, ctx,
            fn=TASKS.get(due.task) or registry.runner_for(due.task),
            trigger="scheduler",
        )
        result = run_outcome.result
        if result.outcome == "error" and "Traceback" in (result.detail or ""):
            log.error("task %s failed: %s", due.task, result.detail[-200:])

        if result.delivers and polish:
            result.title, result.body = polish_copy(result.title, result.body)

        # Phase 3 -- outcome and delivery commit together, or neither does.
        with Warehouse(db_path, lock_timeout_s=180.0) as wh:
            apply_migrations(wh)
            if result.observations:
                record_observations(wh, due.task, season=season,
                                    observed_utc=now, rows=result.observations)
            finish = _finish_sql(due, result.outcome, result.detail)
            if result.delivers:
                outbox.deliver(
                    wh, monitor=due.task, kind=result.kind, title=result.title,
                    body=result.body, now=now, extra_sql=[finish],
                )
            else:
                wh.sql(finish[0], finish[1])
            # The uniform ledger row (PIPELINES.md §4.2): every executed task
            # lands a fetch_run row with timings, counts and its log tail.
            # Stale skips never reach here -- a skip is not a run.
            runner.record(wh, run_outcome)
        report.fired.append(
            Fired(task=due.task, gw=due.gw, due_utc=due.due_utc,
                  outcome=result.outcome, detail=result.detail[:300])
        )

    # Phase 4 -- push whatever is pending, including anything a previous tick
    # enqueued but could not send.
    if send:
        with Warehouse(db_path, lock_timeout_s=180.0) as wh:
            outbox.ensure_schema(wh)
            report.flush = outbox.flush_outbox(
                wh, transport=transport, config=config, now=now
            ).render()
    else:
        report.flush = "outbox: flush skipped (--no-send)"

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true",
                        help="Run a single tick and exit (the launchd mode).")
    parser.add_argument("--season", default=SEASON)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--now", default=None,
                        help="ISO instant to evaluate the schedule at (testing).")
    parser.add_argument("--no-send", action="store_true",
                        help="Enqueue deliveries but do not push them to Telegram.")
    parser.add_argument("--polish", action="store_true",
                        help="Offer delivered copy to the headless Claude CLI.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = None
    if args.now:
        now = dt.datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

    report = tick(
        now=now, season=args.season, db_path=Path(args.db),
        send=not args.no_send, polish=True if args.polish else None,
    )
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    (LOG_DIR / f"dag_{stamp}.json").write_text(report.to_json())
    print(report.to_json())
    return 0 if not any(f.outcome == "error" for f in report.fired) else 1


if __name__ == "__main__":
    raise SystemExit(main())
