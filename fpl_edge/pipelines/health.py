"""Derived pipeline health: one set of rules, returned as data.

Nothing here is stored, invented per-call, or asked of a model. Health is a
pure function of three inputs -- the registry row, the fetch ledger, and the
firing ledger -- evaluated the same way for the control panel, the CLI, and
the tests that pin the rules.

The states, in precedence order (first match wins):

* ``disabled``   -- the registry row says so.
* ``running``    -- a ``dag_firing`` row is claimed and still ``running``.
* ``never_ran``  -- no fetch_run row exists for the pipeline.
* ``failing``    -- the most recent finished run has status ``error``;
  ``consecutive_failures`` counts the unbroken error streak.
* ``stale``      -- the last successful run is older than the task's own
  cadence times a grace factor. The cadence comes from the task's due rule:
  an :class:`~fpl_edge.pipelines.registry.Interval` task is stale past
  ``hours x GRACE_FACTOR``; a :class:`~fpl_edge.pipelines.registry.Calendar`
  daily past :data:`CALENDAR_STALE_H`. DeadlineRelative tasks are never
  cadence-stale here -- days between gameweeks are normal, and their
  per-firing staleness is already the DAG's stale-window job. OnDemand tasks
  likewise.
* ``ok``         -- everything else.

``pipeline_status`` is the single control-panel payload; its shape is a
declared contract (tests/unit/test_pipelines_registry.py pins it) so the
panel script can be built against it without reading this module.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from fpl_edge.jobs import deadline_dag as dag
from fpl_edge.pipelines import registry
from fpl_edge.pipelines.registry import (
    Calendar,
    DeadlineRelative,
    Interval,
    OnDemand,
    Task,
)

UTC = dt.UTC

#: An Interval task is stale after this many missed cadences.
GRACE_FACTOR = 2.0

#: A daily Calendar task is stale past this age (24h cadence + slack for a
#: slow run and a late tick).
CALENDAR_STALE_H = 30.0

#: How many recent OK runs feed the duration average.
AVG_OVER_RUNS = 20


# --------------------------------------------------------------------------
# Schedule rendering and cadence
# --------------------------------------------------------------------------


def describe_due(due: registry.Due) -> str:
    """Human-readable schedule for the panel. Data in, words out."""
    if isinstance(due, Calendar):
        if due.hour_local is not None:
            return f"daily {due.hour_local:02d}:{due.minute:02d} {due.tz}"
        return f"daily {due.hour_utc:02d}:{due.minute:02d} UTC"
    if isinstance(due, Interval):
        if due.hours % 24 == 0 and due.hours >= 24:
            days = int(due.hours // 24)
            return "weekly" if days == 7 else f"every {days}d"
        return f"every {due.hours:g}h"
    if isinstance(due, DeadlineRelative):
        rungs = "/".join(f"T-{h:g}h" for h in due.offsets())
        return (f"{rungs} before each deadline" if len(due.offsets()) == 1
                else f"deadline ladder {rungs}")
    if isinstance(due, OnDemand):
        return "on demand"
    return type(due).__name__


def cadence_hours(due: registry.Due) -> float | None:
    """Expected hours between runs, or None where cadence has no meaning."""
    if isinstance(due, Calendar):
        return 24.0
    if isinstance(due, Interval):
        return float(due.hours)
    return None


def next_due_utc(
    task: Task,
    now: dt.datetime,
    deadlines: list[tuple[int, dt.datetime]] | None = None,
) -> dt.datetime | None:
    """The task's next scheduled instant after ``now``, or None (OnDemand,
    or a DeadlineRelative task with no future deadline known)."""
    now = now.astimezone(UTC)
    due = task.due
    if isinstance(due, OnDemand):
        return None
    if isinstance(due, (Calendar, Interval)):
        # The next instant is the first one a lookahead window contains.
        # Reuse the owed-instant walk by asking "what is owed just before
        # instant X" from the future looking back.
        span = dt.timedelta(hours=(cadence_hours(due) or 24.0) * 2 + 26)
        future = registry.due_instants(task, [], now + span, lookback=span)
        later = [inst for _, inst, _ in future if inst > now]
        return min(later) if later else None
    if isinstance(due, DeadlineRelative):
        if not deadlines:
            return None
        candidates = [
            d.astimezone(UTC) - dt.timedelta(hours=h)
            for _, d in deadlines
            for h in due.offsets()
        ]
        later = [c for c in candidates if c > now]
        return min(later) if later else None
    return None


# --------------------------------------------------------------------------
# Ledger reads
# --------------------------------------------------------------------------


def _table_exists(wh, name: str) -> bool:
    return int(wh.sql(
        "SELECT count(*) c FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).iloc[0]["c"]) > 0


def _as_utc(value) -> dt.datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").to_pydatetime()


def _runs(wh, pipeline: str, limit: int) -> pd.DataFrame:
    if not _table_exists(wh, "fetch_run"):
        return pd.DataFrame()
    return wh.sql(
        "SELECT * FROM fetch_run WHERE pipeline = ? "
        "ORDER BY started_utc DESC LIMIT ?",
        [pipeline, int(limit)],
    )


def _duration_ms(row) -> float | None:
    started = _as_utc(row.get("started_utc"))
    finished = _as_utc(row.get("finished_utc"))
    if started is None or finished is None:
        return None
    return max(0.0, (finished - started).total_seconds() * 1000.0)


def last_run(wh, pipeline: str) -> dict[str, Any] | None:
    """The most recent ledger row, with a ``duration_ms`` convenience and the
    joinable ``log_path`` the runner wrote."""
    runs = _runs(wh, pipeline, 1)
    if runs.empty:
        return None
    row = runs.iloc[0].to_dict()
    from fpl_edge.pipelines.runner import log_path_for

    started = _as_utc(row.get("started_utc"))
    return {
        "status": row.get("status"),
        "started": started.isoformat() if started else None,
        "duration_ms": _duration_ms(row),
        "rows_written": None if pd.isna(row.get("rows_written")) else int(row["rows_written"]),
        "rows_unchanged": None if pd.isna(row.get("rows_unchanged")) else int(row["rows_unchanged"]),
        "credits": None if pd.isna(row.get("credits_spent")) else float(row["credits_spent"]),
        "note": row.get("note"),
        "trigger": row.get("trigger"),
        "log_path": str(log_path_for(str(row["run_id"]))),
    }


def consecutive_failures(wh, pipeline: str) -> int:
    """Length of the unbroken trailing error streak. 0 when the newest run
    finished any other way."""
    runs = _runs(wh, pipeline, 50)
    n = 0
    for status in runs["status"] if not runs.empty else []:
        if status == "error":
            n += 1
        else:
            break
    return n


def avg_duration_ms(wh, pipeline: str, *, last: int = AVG_OVER_RUNS) -> float | None:
    """Mean duration of the last N OK runs -- the panel's progress
    expectation. A derived read, never a stored aggregate."""
    if not _table_exists(wh, "fetch_run"):
        return None
    runs = wh.sql(
        "SELECT started_utc, finished_utc FROM fetch_run "
        "WHERE pipeline = ? AND status IN ('ok', 'skipped_fresh') "
        "ORDER BY started_utc DESC LIMIT ?",
        [pipeline, int(last)],
    )
    durations = [d for d in (_duration_ms(r) for r in runs.to_dict("records"))
                 if d is not None]
    return (sum(durations) / len(durations)) if durations else None


def month_credits(wh, pipeline: str, *, now: dt.datetime | None = None) -> float:
    """Credits this pipeline's ledger rows record for the current calendar
    month (UTC). Local ledger arithmetic; never a vendor call."""
    if not _table_exists(wh, "fetch_run"):
        return 0.0
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    df = wh.sql(
        "SELECT coalesce(sum(credits_spent), 0) s FROM fetch_run "
        "WHERE pipeline = ? AND started_utc >= ?",
        [pipeline, month_start],
    )
    return float(df.iloc[0]["s"])


# --------------------------------------------------------------------------
# The rules
# --------------------------------------------------------------------------


def task_health(wh, task: Task, *, now: dt.datetime | None = None) -> dict[str, Any]:
    """{state, reason, consecutive_failures} for one task. See the module
    docstring for the precedence order -- the rules live HERE and only here."""
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)

    if not task.enabled:
        return {"state": "disabled", "reason": "disabled in the registry",
                "consecutive_failures": 0}

    if _table_exists(wh, "dag_firing"):
        running = wh.sql(
            "SELECT count(*) c FROM dag_firing WHERE task = ? AND outcome = 'running'",
            [task.id],
        )
        if int(running.iloc[0]["c"]) > 0:
            return {"state": "running",
                    "reason": "a firing is claimed and not yet finished",
                    "consecutive_failures": 0}

    runs = _runs(wh, task.id, 1)
    if runs.empty:
        return {"state": "never_ran", "reason": "no ledger row yet",
                "consecutive_failures": 0}

    streak = consecutive_failures(wh, task.id)
    if streak > 0:
        note = str(runs.iloc[0].get("note") or "")[:120]
        return {"state": "failing",
                "reason": f"last run errored ({streak} consecutive): {note}",
                "consecutive_failures": streak}

    cadence = cadence_hours(task.due)
    if cadence is not None:
        ok = wh.sql(
            "SELECT max(finished_utc) f FROM fetch_run "
            "WHERE pipeline = ? AND status IN ('ok', 'skipped_fresh')",
            [task.id],
        )
        last_ok = _as_utc(ok.iloc[0]["f"]) if not ok.empty else None
        budget_h = (CALENDAR_STALE_H if isinstance(task.due, Calendar)
                    else cadence * GRACE_FACTOR)
        if last_ok is None:
            return {"state": "stale",
                    "reason": "runs exist but none has succeeded yet",
                    "consecutive_failures": streak}
        age_h = (now - last_ok).total_seconds() / 3600.0
        if age_h > budget_h:
            return {"state": "stale",
                    "reason": (f"last success {age_h:.1f}h ago against a "
                               f"{budget_h:g}h budget ({describe_due(task.due)})"),
                    "consecutive_failures": streak}

    return {"state": "ok", "reason": "last run succeeded inside its cadence",
            "consecutive_failures": 0}


def _read_deadlines(wh, season: str) -> list[tuple[int, dt.datetime]]:
    if not _table_exists(wh, "dim_event"):
        return []
    df = wh.sql(
        "SELECT gw, deadline_utc FROM dim_event WHERE season = ? "
        "QUALIFY row_number() OVER (PARTITION BY gw ORDER BY as_of DESC) = 1 "
        "ORDER BY gw",
        [season],
    )
    out = []
    for r in df.itertuples(index=False):
        deadline = _as_utc(r.deadline_utc)
        if deadline is not None:
            out.append((int(r.gw), deadline))
    return out


def pipeline_status(
    wh,
    *,
    now: dt.datetime | None = None,
    season: str = dag.SEASON,
    tasks: tuple[Task, ...] | None = None,
) -> list[dict[str, Any]]:
    """The single control-panel payload: one dict per registry task.

    Shape is a declared contract (pinned in tests): id, description, family,
    schedule, enabled, health {state, reason, consecutive_failures},
    last_run {status, started, duration_ms, rows_written, rows_unchanged,
    credits, note, trigger, log_path} | None, avg_duration_ms, next_due,
    metered {confirm_required, credits_estimate, month_credits}.
    """
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    tasks = tasks if tasks is not None else registry.TASKS
    deadlines = _read_deadlines(wh, season)
    out: list[dict[str, Any]] = []
    for task in tasks:
        nxt = next_due_utc(task, now, deadlines)
        out.append({
            "id": task.id,
            "description": task.description,
            "family": task.family,
            "schedule": describe_due(task.due),
            "enabled": task.enabled,
            "health": task_health(wh, task, now=now),
            "last_run": last_run(wh, task.id),
            "avg_duration_ms": avg_duration_ms(wh, task.id),
            "next_due": nxt.isoformat() if nxt else None,
            "metered": {
                "confirm_required": task.confirm_required,
                "credits_estimate": task.credits_estimate,
                "month_credits": month_credits(wh, task.id, now=now),
            },
        })
    return out
