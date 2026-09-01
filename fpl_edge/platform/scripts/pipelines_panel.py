"""The pipelines control panel's data path: the board, and one run's log.

Two scripts, both reads (house law: a panel never triggers anything -- the
trigger is a route, ``POST /api/pipelines/{id}/run``, with its own confirm
gate):

``pipeline_board``
    :func:`fpl_edge.pipelines.health.pipeline_status` -- the declared
    control-panel contract, served verbatim -- plus what that payload alone
    cannot carry: the last ten ledger rows per pipeline (the sparkline and
    the drawer's run table) and one top-level summary the header chips read.
    The board never recomputes a health rule; the rules live in ``health.py``
    and only there.

``pipeline_run_log``
    The tail of one run's captured log file. ``run_id`` is the ledger's own
    uuid-hex and the params schema refuses anything else, so the value can
    never be a path; the resolved file is additionally required to sit inside
    the pipeline_logs directory before a byte is read. A run whose log is
    gone (rotated, other machine, disk-full at write time) is a named gap,
    not an error.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.pipelines import health
from fpl_edge.pipelines.runner import LOG_DIR
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import UTC, empty, q, source_dir

#: How many recent runs each board row carries -- the sparkline's width and
#: the drawer's run table, one number.
RUNS_PER_PIPELINE = 10

#: How many trailing log lines one drawer request serves. The full file is on
#: disk (capped at runner.LOG_CAP_BYTES); the panel shows the end, where the
#: outcome and the error live.
LOG_TAIL_LINES = 200

#: The Odds API monthly allowance the summary quotes beside month spend. A
#: spend with no denominator beside it is a number nobody can sanity-check.
#: Imported from the module that measured it rather than restated.
def _month_cap() -> float:
    from fpl_edge.ingest.odds import FREE_TIER_MONTHLY_CREDITS

    return float(FREE_TIER_MONTHLY_CREDITS)


# ---------------------------------------------------------------------------
# pipeline_board
# ---------------------------------------------------------------------------

_RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "status", "started", "duration_ms", "trigger"],
    "properties": {
        "run_id": {"type": "string",
                   "description": "The ledger's uuid-hex; pipeline_run_log's key."},
        "status": {"enum": ["ok", "error", "refused", "skipped_fresh", "no_source"]},
        "started": {"type": ["string", "null"]},
        "duration_ms": {"type": ["number", "null"]},
        "rows_written": {"type": ["integer", "null"]},
        "rows_unchanged": {"type": ["integer", "null"]},
        "credits": {"type": ["number", "null"]},
        "trigger": {"type": ["string", "null"],
                    "description": "scheduler | ui | cli -- who asked."},
        "note": {"type": ["string", "null"]},
    },
}

_LAST_RUN_SCHEMA: dict[str, Any] = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "properties": {
        "status": {"type": ["string", "null"]},
        "started": {"type": ["string", "null"]},
        "duration_ms": {"type": ["number", "null"]},
        "rows_written": {"type": ["integer", "null"]},
        "rows_unchanged": {"type": ["integer", "null"]},
        "credits": {"type": ["number", "null"]},
        "note": {"type": ["string", "null"]},
        "trigger": {"type": ["string", "null"]},
        "log_path": {"type": ["string", "null"]},
    },
}

_ROW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "description", "family", "schedule", "enabled",
                 "health", "last_run", "avg_duration_ms", "next_due",
                 "metered", "runs"],
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "family": {"type": "string"},
        "schedule": {"type": "string",
                     "description": "Human words from health.describe_due; never a cron string."},
        "enabled": {"type": "boolean"},
        "health": {
            "type": "object",
            "additionalProperties": False,
            "required": ["state", "reason", "consecutive_failures"],
            "properties": {
                "state": {"enum": ["ok", "failing", "stale", "running",
                                   "never_ran", "disabled"]},
                "reason": {"type": "string",
                           "description": "Renderable prose. The reason IS the product; "
                                          "a bare dot is forbidden downstream."},
                "consecutive_failures": {"type": "integer"},
            },
        },
        "last_run": _LAST_RUN_SCHEMA,
        "avg_duration_ms": {"type": ["number", "null"],
                            "description": "Mean of the last 20 OK runs (health.AVG_OVER_RUNS)."},
        "next_due": {"type": ["string", "null"]},
        "metered": {
            "type": "object",
            "additionalProperties": False,
            "required": ["confirm_required", "credits_estimate", "month_credits"],
            "properties": {
                "confirm_required": {"type": "boolean"},
                "credits_estimate": {"type": "number"},
                "month_credits": {"type": "number"},
            },
        },
        "runs": {"type": "array", "items": _RUN_SCHEMA,
                 "description": f"Newest first, at most {RUNS_PER_PIPELINE}."},
    },
}

BOARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}

BOARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["generated_at", "summary", "families", "rows", "row_count"],
    "properties": {
        "generated_at": {"type": "string"},
        "row_count": {"type": "integer"},
        "families": {
            "type": "array", "items": {"type": "string"},
            "description": "Family order for the view: registry order, "
                           "de-duplicated -- the board never invents a sort.",
        },
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["n_ok", "n_failing", "n_stale", "n_never_ran",
                         "n_running", "n_disabled", "month_credits",
                         "month_credits_cap"],
            "properties": {
                "n_ok": {"type": "integer"},
                "n_failing": {"type": "integer"},
                "n_stale": {"type": "integer"},
                "n_never_ran": {"type": "integer"},
                "n_running": {"type": "integer"},
                "n_disabled": {"type": "integer"},
                "month_credits": {"type": "number",
                                  "description": "Sum of every pipeline's ledger credits "
                                                 "this calendar month (UTC)."},
                "month_credits_cap": {"type": "number",
                                      "description": "The Odds API monthly allowance the "
                                                     "spend is quoted against."},
            },
        },
        "rows": {"type": "array", "items": _ROW_SCHEMA},
    },
}


def _ledger_exists(wh) -> bool:
    df = q(wh, "SELECT count(*) AS c FROM information_schema.tables "
               "WHERE table_name = 'fetch_run'")
    return not df.empty and int(df.iloc[0]["c"]) > 0


def _duration_ms(started, finished) -> float | None:
    s = pd.to_datetime(started, utc=True, errors="coerce")
    f = pd.to_datetime(finished, utc=True, errors="coerce")
    if pd.isna(s) or pd.isna(f):
        return None
    return max(0.0, (f - s).total_seconds() * 1000.0)


def _recent_runs(wh) -> dict[str, list[dict[str, Any]]]:
    """Last N ledger rows per pipeline, newest first, in one query."""
    df = q(
        wh,
        'SELECT pipeline, run_id, status, started_utc, finished_utc, '
        'rows_written, rows_unchanged, credits_spent, note, "trigger" FROM ('
        "  SELECT *, row_number() OVER (PARTITION BY pipeline"
        "                               ORDER BY started_utc DESC) rn"
        "  FROM fetch_run"
        f") WHERE rn <= {int(RUNS_PER_PIPELINE)} "
        "ORDER BY pipeline, started_utc DESC",
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for r in df.to_dict("records"):
        started = pd.to_datetime(r["started_utc"], utc=True, errors="coerce")
        out.setdefault(str(r["pipeline"]), []).append({
            "run_id": str(r["run_id"]),
            "status": str(r["status"]),
            "started": None if pd.isna(started) else started.isoformat(),
            "duration_ms": _duration_ms(r["started_utc"], r["finished_utc"]),
            "rows_written": None if pd.isna(r["rows_written"]) else int(r["rows_written"]),
            "rows_unchanged": None if pd.isna(r["rows_unchanged"]) else int(r["rows_unchanged"]),
            "credits": None if pd.isna(r["credits_spent"]) else float(r["credits_spent"]),
            "trigger": None if r["trigger"] is None else str(r["trigger"]),
            "note": None if r["note"] is None else str(r["note"]),
        })
    return out


def pipeline_board(wh) -> dict[str, Any]:
    """Every registered pipeline: health with its reason, last run, average
    time, next due, spend -- plus the recent-run history per row."""
    if not _ledger_exists(wh):
        return empty(
            "No fetch_run ledger in this warehouse yet -- no pipeline has ever "
            "run through the runner. Any scheduler tick or `uv run fpl "
            "pipelines run <task>` writes the first row."
        )

    now = dt.datetime.now(UTC)
    rows = health.pipeline_status(wh, now=now)
    runs = _recent_runs(wh)
    for row in rows:
        row["runs"] = runs.get(row["id"], [])

    counts = {"ok": 0, "failing": 0, "stale": 0, "never_ran": 0,
              "running": 0, "disabled": 0}
    month = 0.0
    families: list[str] = []
    for row in rows:
        counts[row["health"]["state"]] = counts.get(row["health"]["state"], 0) + 1
        month += float(row["metered"]["month_credits"])
        if row["family"] not in families:
            families.append(row["family"])

    return {
        "generated_at": now.isoformat(),
        "row_count": len(rows),
        "families": families,
        "summary": {
            "n_ok": counts["ok"],
            "n_failing": counts["failing"],
            "n_stale": counts["stale"],
            "n_never_ran": counts["never_ran"],
            "n_running": counts["running"],
            "n_disabled": counts["disabled"],
            "month_credits": round(month, 2),
            "month_credits_cap": _month_cap(),
        },
        "rows": rows,
    }


register_script(
    "pipeline_board",
    pipeline_board,
    params_schema=BOARD_PARAMS,
    result_schema=BOARD_RESULT,
    title="Pipelines",
    description=(
        "Every registered pipeline: health with its reason, schedule in human "
        "words, last run, average duration, next due, month credits, and the "
        "last ten runs for the sparkline. Reads only; triggering is a route."
    ),
)


# ---------------------------------------------------------------------------
# pipeline_run_log
# ---------------------------------------------------------------------------

LOG_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id"],
    "properties": {
        "run_id": {
            "type": "string",
            "pattern": "^[0-9a-f]{32}$",
            "description": "The fetch_run uuid-hex. The pattern is the path-safety "
                           "gate: nothing else reaches the filesystem.",
        },
    },
}

LOG_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["run_id", "found", "lines"],
    "properties": {
        "run_id": {"type": "string"},
        "found": {"type": "boolean"},
        "reason": {"type": ["string", "null"],
                   "description": "Why there are no lines, when found is false."},
        "lines": {"type": "array", "items": {"type": "string"}},
        "n_lines_total": {"type": ["integer", "null"]},
        "truncated": {"type": "boolean",
                      "description": f"True when the file holds more than "
                                     f"{LOG_TAIL_LINES} lines and only the tail is served."},
    },
}


def _log_dirs(wh) -> list[Path]:
    """Where a run's log may live: next to the warehouse this panel reads
    (tests and non-default deployments), and the runner's own LOG_DIR (the
    production path, cwd-relative exactly as the runner writes it)."""
    dirs = [source_dir(wh) / "pipeline_logs", LOG_DIR]
    seen: list[Path] = []
    for d in dirs:
        r = d.resolve()
        if r not in seen:
            seen.append(r)
    return seen


def pipeline_run_log(wh, *, run_id: str) -> dict[str, Any]:
    """The tail of one run's captured log. Path-safe by construction: the
    params schema admits only uuid-hex, and the resolved file must sit inside
    a known pipeline_logs directory."""
    for log_dir in _log_dirs(wh):
        path = (log_dir / f"{run_id}.log").resolve()
        if path.parent != log_dir:
            # Unreachable past the schema's pattern; kept as defence in depth.
            return {"run_id": run_id, "found": False, "lines": [],
                    "reason": "refused: the resolved path leaves the log directory",
                    "n_lines_total": None, "truncated": False}
        if not path.exists():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError as exc:
            return {"run_id": run_id, "found": False, "lines": [],
                    "reason": f"log file unreadable: {type(exc).__name__}: {exc}",
                    "n_lines_total": None, "truncated": False}
        lines = text.splitlines()
        tail = lines[-LOG_TAIL_LINES:]
        return {"run_id": run_id, "found": True, "reason": None,
                "lines": tail, "n_lines_total": len(lines),
                "truncated": len(lines) > len(tail)}
    return {
        "run_id": run_id, "found": False, "lines": [],
        "reason": ("No log file for this run. The ledger row is the run's "
                   "record; its log was not written (disk full at write "
                   "time), was written on another machine, or predates the "
                   "runner's log capture."),
        "n_lines_total": None, "truncated": False,
    }


register_script(
    "pipeline_run_log",
    pipeline_run_log,
    params_schema=LOG_PARAMS,
    result_schema=LOG_RESULT,
    title="One run's log",
    description=(
        f"The last {LOG_TAIL_LINES} lines of one pipeline run's captured log, "
        "keyed by the ledger's run_id. uuid-hex only; the file must resolve "
        "inside pipeline_logs/."
    ),
)


__all__ = ["pipeline_board", "pipeline_run_log"]
