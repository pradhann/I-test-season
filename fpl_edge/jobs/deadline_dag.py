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
import importlib.util
import json
import logging
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from fpl_edge.jobs import outbox
from fpl_edge.store import DEFAULT_DB, Warehouse

log = logging.getLogger("fpl_edge.jobs.deadline_dag")

UTC = dt.timezone.utc
LONDON = ZoneInfo("Europe/London")

SEASON = "2026-27"

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
LOG_DIR = Path("data/warehouse/jobs")
PLAN_GLOB = "gw*_plan.json"
PLAN_DIR = Path("data/warehouse")

#: Deadline-relative tasks: task name -> how long BEFORE the deadline it fires.
#: These are the DESIGN.md §3 offsets, and they are the whole schedule spec.
DEADLINE_OFFSETS: dict[str, dt.timedelta] = {
    "presser_projection_refresh": dt.timedelta(hours=30),
    "final_solve_delivery": dt.timedelta(hours=4),
    "lineup_captain_check": dt.timedelta(minutes=90),
}

#: The one wall-clock task. FPL's price run lands around 01:30 UK; 02:00 local
#: is after it and before anybody is awake to act on stale numbers.
NIGHTLY_TASK = "price_radar"
NIGHTLY_LOCAL_HOUR = 2

#: A firing due longer ago than this is recorded and skipped, never run. Two
#: hours is chosen against the tightest offset: T-90m is worthless if delivered
#: after the deadline, and 2h means a tick can be late by a whole launchd
#: interval plus a slow ingest and still be honest.
STALE_WINDOW = dt.timedelta(hours=2)

#: How far back a tick looks for firings it never saw. Bounds how many
#: skipped_stale rows a week-long outage can write, while still leaving an
#: honest record that the machine was down through a deadline.
LOOKBACK = dt.timedelta(hours=36)

#: Net transfers per hour, on the watched set, that counts as a price move
#: worth waking someone for. A FIRST GUESS -- which is exactly why every run
#: writes its observations to dag_observation whether or not it fires. Tune
#: this against what actually preceded price changes, not against intuition.
VELOCITY_THRESHOLD = 12_000.0

#: How many of the most-owned players the radar watches in addition to whatever
#: is in the squad. Price moves on the template are actionable even when the
#: player is not owned: they move the price you will pay.
TOP_OWNED_N = 20

CLAUDE_BIN = Path.home() / ".local" / "bin" / "claude"

STEP_TIMEOUT_S = 900


# --------------------------------------------------------------------------
# Time: due-instant arithmetic. Pure functions, no I/O -- this is the seam.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Due:
    """One firing that the schedule says should have happened by ``now``."""

    task: str
    season: str
    gw: int
    due_utc: dt.datetime
    deadline_utc: dt.datetime | None
    stale: bool

    def key(self) -> tuple[str, str, int, dt.datetime]:
        return (self.task, self.season, self.gw, self.due_utc)


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
    stale_window: dt.timedelta = STALE_WINDOW,
) -> list[Due]:
    """Which firings are owed at ``now``, and which of those are already stale.

    ``deadlines`` is [(gw, deadline_utc)] -- one row per gameweek, the newest
    known deadline for it. A task is owed when its due instant has passed and
    the tick has not already recorded it; staleness is decided here rather than
    at the call site so the same rule applies to a launchd tick and a manual one.
    """
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
                    stale=(now - due) > stale_window,
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
                stale=(now - inst) > stale_window,
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
        "UPDATE dag_firing SET outcome = ?, detail = ? "
        "WHERE task = ? AND season = ? AND gw = ? AND due_utc = ?",
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
# Task results and the isolated-subprocess step runner
# --------------------------------------------------------------------------


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str = ""


@dataclass
class TaskResult:
    """What a task decided. ``outcome`` is one of the migration's six values."""

    outcome: str
    detail: str = ""
    title: str = ""
    body: str = ""
    kind: str = "digest"
    steps: list[Step] = field(default_factory=list)
    #: (code, metric, value) rows written to dag_observation on EVERY run,
    #: including quiet ones -- the series a threshold is tuned against.
    observations: list[tuple[int, str, float]] = field(default_factory=list)

    @property
    def delivers(self) -> bool:
        return self.outcome == "delivered" and bool(self.title)


def run_step(name: str, argv: list[str], *, timeout: float = STEP_TIMEOUT_S) -> Step:
    """One step as its own process, exactly as post_gw.py does it.

    Isolation is not tidiness: these steps open the warehouse for writing, and a
    hung lock or a segfault inside one must not take the scheduler with it.
    """
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return Step(
            name=name, ok=proc.returncode == 0, seconds=round(time.monotonic() - t0, 1),
            detail=" | ".join(tail)[-300:],
        )
    except subprocess.TimeoutExpired:
        return Step(name=name, ok=False, seconds=round(time.monotonic() - t0, 1),
                    detail=f"timed out after {timeout:.0f}s")
    except Exception:  # noqa: BLE001 - the step record is the error channel
        return Step(name=name, ok=False, seconds=round(time.monotonic() - t0, 1),
                    detail=traceback.format_exc()[-300:])


def _module_exists(dotted: str) -> bool:
    """Is this CLI entry point actually here yet?

    The projections providers are being built in parallel. A DAG task that hard-
    depends on a module another agent has not landed would fail the whole
    pre-deadline refresh over a missing import; instead the step is skipped and
    the digest says so, which is the same "admitting the gap beats looking
    complete" rule the report layer uses.
    """
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


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
# Tasks
# --------------------------------------------------------------------------


@dataclass
class TaskContext:
    season: str
    gw: int
    due_utc: dt.datetime
    deadline_utc: dt.datetime | None
    now: dt.datetime
    db_path: Path
    python: str = sys.executable

    def read(self):
        return Warehouse.read_copy(self.db_path)

    def write(self):
        return Warehouse(self.db_path, lock_timeout_s=180.0)


def _fmt_delta(target: dt.datetime | None, now: dt.datetime) -> str:
    if target is None:
        return "unknown"
    secs = (target.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    sign = "" if secs >= 0 else "-"
    secs = abs(secs)
    return f"{sign}{int(secs // 3600)}h{int((secs % 3600) // 60):02d}m"


def presser_projection_refresh(ctx: TaskContext) -> TaskResult:
    """T-30h: refetch what press conferences and projection sites just changed."""
    py = ctx.python
    steps = [
        run_step("ingest_live", [py, "scripts/ingest_live.py"]),
        run_step("ingest_odds_fixtures", [py, "scripts/ingest_odds.py", "--fixtures"]),
        run_step("ingest_content",
                 [py, "-m", "fpl_edge.ingest.content.pipeline", "ingest",
                  "--backfill-days", "2"]),
    ]

    projections_cli = "fpl_edge.ingest.projections.cli"
    if _module_exists(projections_cli):
        steps.append(run_step(
            "ingest_projections",
            [py, "-m", projections_cli, "ingest", "--season", ctx.season,
             "--first-gw", str(ctx.gw), "--last-gw", str(ctx.gw + 5)],
        ))
    else:
        steps.append(Step(name="ingest_projections", ok=True, seconds=0.0,
                          detail="skipped: no projections CLI in this build yet"))

    with ctx.read() as wh:
        counts = wh.sql(
            "SELECT count(*) AS n, max(as_of) AS as_of FROM fact_player_state "
            "WHERE season = ? AND as_of = (SELECT max(as_of) FROM fact_player_state "
            "WHERE season = ?)",
            [ctx.season, ctx.season],
        )
        news = wh.sql(
            "SELECT p.web_name, s.news, s.status, s.chance_of_playing_next_round AS chance "
            "FROM fact_player_state s "
            "JOIN dim_player p ON p.season = s.season AND p.code = s.code "
            "WHERE s.season = ? AND s.news_added IS NOT NULL "
            "  AND s.news_added >= ? AND s.news IS NOT NULL AND s.news <> '' "
            "QUALIFY row_number() OVER (PARTITION BY s.code ORDER BY s.as_of DESC, "
            "        p.as_of DESC) = 1 "
            "ORDER BY s.news_added DESC LIMIT 15",
            [ctx.season, ctx.now.astimezone(UTC) - dt.timedelta(hours=48)],
        )

    n_players = int(counts["n"].iloc[0]) if len(counts) else 0
    snap_as_of = counts["as_of"].iloc[0] if len(counts) else None

    lines = [f"GW{ctx.gw} deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}.", ""]
    lines.append(f"Player snapshot: {n_players} rows"
                 + (f" as of {snap_as_of}" if snap_as_of is not None else ""))
    for s in steps:
        mark = "ok" if s.ok else "FAILED"
        lines.append(f"  {s.name}: {mark} ({s.seconds}s)"
                     + (f" -- {s.detail}" if s.detail and not s.ok else ""))
        if s.ok and s.detail.startswith("skipped:"):
            lines[-1] = f"  {s.name}: {s.detail}"
    lines.append("")
    if len(news):
        lines.append(f"Injury news in the last 48h ({len(news)}):")
        for r in news.itertuples(index=False):
            chance = "" if r.chance is None else f" [{int(r.chance)}%]"
            lines.append(f"  {r.web_name} ({r.status}){chance}: {str(r.news)[:120]}")
    else:
        lines.append("No injury news added in the last 48h.")

    failed = [s.name for s in steps if not s.ok]
    detail = f"{len(steps) - len(failed)}/{len(steps)} steps ok, {len(news)} news items"
    if failed:
        detail += "; failed: " + ",".join(failed)
    return TaskResult(
        outcome="delivered", detail=detail, kind="digest",
        title=f"T-30h refresh — GW{ctx.gw}", body="\n".join(lines), steps=steps,
    )


def _held_codes(ctx: TaskContext) -> tuple[frozenset[int], str]:
    """Codes in the confirmed squad, from local state only.

    FPL_EDGE_DISABLE_PRIVATE is honoured by never reaching for the authenticated
    endpoints at all: this reads the locally confirmed squad record, which is
    what `fpl myteam confirm` wrote. With the flag set we do not even do that,
    so a test run cannot touch private state.
    """
    if os.environ.get("FPL_EDGE_DISABLE_PRIVATE", "") not in ("", "0"):
        return frozenset(), "private state disabled"
    try:
        from fpl_edge.config import UserConfig
        from fpl_edge.myteam.store import MyTeamStore

        store = MyTeamStore(UserConfig().entry_id)
        record = store.confirmed(season=ctx.season)
        if record is None:
            return frozenset(), "no confirmed squad"
        return frozenset(int(c) for c in record.codes), "confirmed squad"
    except Exception as exc:  # noqa: BLE001 - the radar still runs on top-owned
        log.info("no local squad state (%s)", type(exc).__name__)
        return frozenset(), f"squad unavailable ({type(exc).__name__})"


def price_radar(ctx: TaskContext) -> TaskResult:
    """Nightly 02:00 UK: net-transfer velocity between the last two snapshots.

    Deterministic by construction -- the fire/no-fire decision is a comparison
    of a float against VELOCITY_THRESHOLD, auditable months later from the
    dag_observation rows this writes on every run.
    """
    with ctx.read() as wh:
        snaps = wh.sql(
            "SELECT DISTINCT as_of FROM fact_player_state WHERE season = ? "
            "ORDER BY as_of DESC LIMIT 2",
            [ctx.season],
        )
        if len(snaps) < 2:
            return TaskResult(
                outcome="no_source",
                detail=f"need two player-state snapshots, have {len(snaps)}",
            )
        newest = snaps["as_of"].iloc[0]
        prev = snaps["as_of"].iloc[1]
        df = wh.sql(
            "WITH a AS (SELECT code, transfers_in_event AS ti, transfers_out_event AS to_, "
            "                  selected_by_pct, price_tenths "
            "           FROM fact_player_state WHERE season = ? AND as_of = ?), "
            "     b AS (SELECT code, transfers_in_event AS ti, transfers_out_event AS to_ "
            "           FROM fact_player_state WHERE season = ? AND as_of = ?) "
            "SELECT a.code, p.web_name, a.selected_by_pct, a.price_tenths, "
            "       a.ti AS ti_now, a.to_ AS to_now, b.ti AS ti_prev, b.to_ AS to_prev "
            "FROM a JOIN b USING (code) "
            "JOIN dim_player p ON p.season = ? AND p.code = a.code "
            "QUALIFY row_number() OVER (PARTITION BY a.code ORDER BY p.as_of DESC) = 1",
            [ctx.season, newest, ctx.season, prev, ctx.season],
        )

    hours = (newest - prev).total_seconds() / 3600.0
    if hours <= 0:
        return TaskResult(outcome="no_source", detail="snapshot timestamps not ordered")

    # FPL's transfers_*_event counters reset to zero at each deadline. A window
    # that straddles one would read every player as a huge net OUTflow. Detect
    # it from the aggregate (which only ever grows within a gameweek) and stay
    # quiet rather than firing on an artefact.
    if float(df["ti_now"].sum()) < float(df["ti_prev"].sum()):
        return TaskResult(
            outcome="quiet",
            detail=f"gameweek transfer counters reset between {prev} and {newest}",
        )

    df["net_now"] = df["ti_now"].astype(float) - df["to_now"].astype(float)
    df["net_prev"] = df["ti_prev"].astype(float) - df["to_prev"].astype(float)
    df["velocity"] = (df["net_now"] - df["net_prev"]) / hours

    held, held_note = _held_codes(ctx)
    top_owned = set(
        df.nlargest(TOP_OWNED_N, "selected_by_pct")["code"].astype(int).tolist()
    )
    watched = set(held) | top_owned
    watch = df[df["code"].astype(int).isin(watched)].copy()

    # The tuning series, stored whether or not anything fires.
    obs = []
    for r in watch.itertuples(index=False):
        obs.append((int(r.code), "net_transfer_velocity_per_h", float(r.velocity)))
        obs.append((int(r.code), "net_transfers_event", float(r.net_now)))
        obs.append((int(r.code), "selected_by_pct", float(r.selected_by_pct or 0.0)))

    movers = watch[watch["velocity"].abs() >= VELOCITY_THRESHOLD].copy()
    movers = movers.reindex(movers["velocity"].abs().sort_values(ascending=False).index)

    detail = (
        f"window {hours:.1f}h, watched {len(watch)} "
        f"({len(held)} held via {held_note}), movers {len(movers)}, "
        f"threshold {VELOCITY_THRESHOLD:.0f}/h"
    )
    result = TaskResult(
        outcome="delivered" if len(movers) else "quiet", detail=detail, kind="alert",
        observations=obs,
    )
    if not len(movers):
        return result

    lines = [
        f"Net-transfer velocity over the last {hours:.1f}h "
        f"(threshold {VELOCITY_THRESHOLD:,.0f}/h):",
        "",
    ]
    for r in movers.head(10).itertuples(index=False):
        arrow = "RISE risk" if r.velocity > 0 else "FALL risk"
        owned = " [owned]" if int(r.code) in held else ""
        lines.append(
            f"  {r.web_name}{owned}  {r.velocity:+,.0f}/h  "
            f"£{r.price_tenths / 10:.1f}m  {float(r.selected_by_pct or 0):.1f}% owned  {arrow}"
        )
    lines += ["", f"Snapshots: {prev} -> {newest}.",
              "Velocity is net transfers per hour, not FPL's own price algorithm."]
    result.title = f"Price radar — {len(movers)} mover(s)"
    result.body = "\n".join(lines)
    return result


def _freshest_plan(plan_dir: Path = PLAN_DIR) -> tuple[Path, dict] | None:
    best: tuple[dt.datetime, Path, dict] | None = None
    for path in sorted(plan_dir.glob(PLAN_GLOB)):
        try:
            obj = json.loads(path.read_text())
            gen = dt.datetime.fromisoformat(str(obj["generated_at"]))
        except Exception:  # noqa: BLE001 - a malformed artefact is not a plan
            continue
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=UTC)
        if best is None or gen > best[0]:
            best = (gen, path, obj)
    return None if best is None else (best[1], best[2])


def final_solve_delivery(ctx: TaskContext) -> TaskResult:
    """T-4h: deliver the freshest stored plan, or say honestly that there isn't one.

    This task NEVER solves. The MILP takes ~30 minutes; running it inside a tick
    would hold the process past the next launchd dispatch, and a solve that
    starts at T-4h and finishes at T-3h30 is a solve nobody asked for. The plan
    is produced by `make solve` (scripts/gw1_squad.py) and this reads the
    artefact -- so the failure mode is a stale plan clearly labelled stale,
    rather than a fresh-looking plan that missed the deadline.
    """
    found = _freshest_plan(PLAN_DIR)
    if found is None:
        return TaskResult(
            outcome="delivered", kind="alert",
            detail="no plan artefact found",
            title=f"GW{ctx.gw}: no solve to deliver",
            body=(
                f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)} and there is no "
                f"plan artefact in {PLAN_DIR}/{PLAN_GLOB}.\n\n"
                "Run `make solve` if you want the optimiser's squad before the deadline. "
                "Nothing is being guessed here."
            ),
        )

    path, plan = found
    gen = dt.datetime.fromisoformat(str(plan["generated_at"]))
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=UTC)
    age = ctx.now.astimezone(UTC) - gen

    key = f"gw{ctx.gw}"
    block = plan.get(key) or plan.get("gw1") or {}
    codes = list(block.get("squad") or [])
    names: dict[int, str] = {}
    if codes:
        with ctx.read() as wh:
            df = wh.sql(
                "SELECT code, web_name FROM dim_player WHERE season = ? "
                "QUALIFY row_number() OVER (PARTITION BY code ORDER BY as_of DESC) = 1",
                [ctx.season],
            )
        names = {int(r.code): str(r.web_name) for r in df.itertuples(index=False)}

    def nm(code) -> str:
        return names.get(int(code), f"code {code}")

    if age > dt.timedelta(hours=24):
        return TaskResult(
            outcome="delivered", kind="alert",
            detail=f"stale plan: {path.name} is {age.total_seconds() / 3600:.1f}h old",
            title=f"GW{ctx.gw}: no fresh solve",
            body=(
                f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}.\n\n"
                f"The newest plan ({path.name}) was generated {gen.isoformat()}, "
                f"{age.total_seconds() / 3600:.1f} hours ago. That is older than the "
                "24h freshness bar, so it is NOT being presented as this deadline's "
                "recommendation — prices, injuries and ownership have moved since.\n\n"
                f"Its captain was {nm(block.get('captain'))}. "
                "Re-run `make solve` to get a plan for these conditions."
            ),
        )

    xi = list(block.get("starting_xi") or [])
    bench = list(block.get("bench") or [])
    lines = [
        f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}. "
        f"Plan generated {gen.isoformat()} ({age.total_seconds() / 3600:.1f}h ago).",
        "",
        f"Captain: {nm(block.get('captain'))}   Vice: {nm(block.get('vice_captain'))}",
    ]
    if block.get("chip"):
        lines.append(f"Chip: {block['chip']}")
    if xi:
        lines += ["", "XI: " + ", ".join(nm(c) for c in xi)]
    if bench:
        lines.append("Bench: " + ", ".join(nm(c) for c in bench))
    lines += [
        "",
        f"Objective {plan.get('objective_mode', '?')} = "
        f"{float(plan.get('objective', 0.0)):.1f} over GWs "
        f"{plan.get('horizon_gws')}; {plan.get('n_sims', '?')} sims.",
        f"Source: {path}",
    ]
    return TaskResult(
        outcome="delivered", kind="report",
        detail=f"plan {path.name} age {age.total_seconds() / 3600:.1f}h",
        title=f"GW{ctx.gw} final plan — C: {nm(block.get('captain'))}",
        body="\n".join(lines),
    )


def confirmed_lineup_source(ctx: TaskContext) -> str | None:
    """Name the table holding confirmed XIs, or None if nothing provides them.

    Separated from the task so the wiring is testable today and a real source is
    a one-line change tomorrow: land a `fact_confirmed_lineup` table and this
    returns it, and the check below stops recording `no_source`.
    """
    with ctx.read() as wh:
        df = wh.sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'fact_confirmed_lineup'"
        )
    return "fact_confirmed_lineup" if len(df) else None


def lineup_captain_check(ctx: TaskContext) -> TaskResult:
    """T-90m: is the captain actually starting?

    There is no confirmed-lineup feed in the warehouse yet, so this records
    `no_source` and delivers nothing. That is deliberate: a T-90m alert that
    guessed from predicted lineups would be worse than silence, because it would
    train the operator to act on a source that is wrong about a third of the
    time. The firing row still gets written every deadline, so the day a source
    lands the gap is visible in the history rather than invented away.
    """
    source = confirmed_lineup_source(ctx)
    if source is None:
        return TaskResult(
            outcome="no_source",
            detail="no confirmed-lineup table in the warehouse (checked "
                   "fact_confirmed_lineup); nothing delivered",
        )

    plan = _freshest_plan(PLAN_DIR)
    if plan is None:
        return TaskResult(outcome="no_source", detail="lineups present but no plan to check")
    _, obj = plan
    block = obj.get(f"gw{ctx.gw}") or {}
    captain = block.get("captain")
    if captain is None:
        return TaskResult(outcome="quiet", detail="plan has no captain for this gameweek")

    with ctx.read() as wh:
        df = wh.sql(
            f"SELECT code, is_starting FROM {source} WHERE season = ? AND gw = ? "  # noqa: S608
            "AND code = ? QUALIFY row_number() OVER (PARTITION BY code "
            "ORDER BY as_of DESC) = 1",
            [ctx.season, ctx.gw, int(captain)],
        )
        names = wh.sql(
            "SELECT web_name FROM dim_player WHERE season = ? AND code = ? "
            "ORDER BY as_of DESC LIMIT 1",
            [ctx.season, int(captain)],
        )
    who = str(names["web_name"].iloc[0]) if len(names) else f"code {captain}"
    if not len(df):
        return TaskResult(outcome="quiet",
                          detail=f"captain {who} not in the confirmed-lineup feed yet")
    if bool(df["is_starting"].iloc[0]):
        return TaskResult(outcome="quiet", detail=f"captain {who} confirmed starting")
    return TaskResult(
        outcome="delivered", kind="alert",
        detail=f"captain {who} is NOT in the confirmed XI",
        title=f"ACT: captain {who} is not starting",
        body=(
            f"Confirmed lineups are out and {who} — your captain — is not in the XI. "
            f"Deadline in {_fmt_delta(ctx.deadline_utc, ctx.now)}."
        ),
    )


TASKS: dict[str, Callable[[TaskContext], TaskResult]] = {
    "presser_projection_refresh": presser_projection_refresh,
    "price_radar": price_radar,
    "final_solve_delivery": final_solve_delivery,
    "lineup_captain_check": lineup_captain_check,
}


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

    owed = due_tasks(deadlines, now, season=season)

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

    # Phase 2 -- run each claimed task with the lock free.
    for due in claimed:
        ctx = TaskContext(
            season=season, gw=due.gw, due_utc=due.due_utc,
            deadline_utc=due.deadline_utc, now=now, db_path=db_path,
        )
        try:
            result = TASKS[due.task](ctx)
        except Exception:  # noqa: BLE001 - a broken task is an outcome, not a crash
            result = TaskResult(outcome="error", detail=traceback.format_exc()[-600:])
            log.exception("task %s failed", due.task)

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
