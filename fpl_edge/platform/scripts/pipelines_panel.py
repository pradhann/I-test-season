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
import json
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.pipelines import health, registry
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
        "note": {"type": ["string", "null"],
                 "description": "The ledger note, verbatim. Whatever the "
                                "runner wrote, including a JSON note, is "
                                "served here unparsed."},
        "note_is_json": {"type": "boolean",
                         "description": "Computed: the WHOLE note parsed as a "
                                        "JSON object. False does not mean the "
                                        "fields below are absent; a prose note "
                                        "can still carry a spend= line."},
        "fields_from": {"type": ["string", "null"],
                        "enum": ["note_json", "spend_line", None],
                        "description": "Which shape the model and token "
                                       "fields were read out of, or null when "
                                       "the note carries neither."},
        "model": {"type": ["string", "null"],
                  "description": "From the note's JSON or its spend= line, "
                                 "when one carries a model name. Never "
                                 "inferred."},
        "tokens": {"type": ["number", "null"],
                   "description": "From the same object: tokens, or input "
                                  "plus output where it splits them."},
        "trigger": {"type": ["string", "null"]},
        "log_path": {"type": ["string", "null"]},
    },
}

_ROW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "description", "family", "schedule", "due_kind",
                 "enabled", "health", "last_run", "last_run_age_days",
                 "last_success", "stale_window", "stale_window_hours",
                 "stale_by_window", "avg_duration_ms", "next_due",
                 "metered", "runs"],
    "properties": {
        "id": {"type": "string"},
        "description": {"type": "string"},
        "family": {"type": "string"},
        "schedule": {"type": "string",
                     "description": "Human words from health.describe_due; never a cron string."},
        "due_kind": {"enum": ["daily", "deadline", "interval", "on demand"],
                     "description": "Which of the registry's four Due shapes "
                                    "this row carries, in words."},
        "last_run_age_days": {
            "type": ["number", "null"],
            "description": "Computed: days between the last run's start and "
                           "generated_at. Fractional; the view rounds.",
        },
        "last_success": {
            "type": ["string", "null"],
            "description": "max(finished_utc) over ledger rows with status ok "
                           "or skipped_fresh. Null means no run has ever "
                           "succeeded.",
        },
        "stale_window": {
            "type": "string",
            "description": "The registry's stale_window in words (hours and "
                           "days, never seconds): how late a firing may be "
                           "before the scheduler drops it.",
        },
        "stale_window_hours": {
            "type": "number",
            "description": "The same window as a number, for sorting.",
        },
        "stale_by_window": {
            "type": "boolean",
            "description": "Computed: last_success is older than "
                           "stale_window_hours, or nothing has succeeded yet. "
                           "This is the TASK'S OWN window, not health.state's "
                           "cadence budget; the two disagree by design. "
                           "Always false for a deadline-relative task whose "
                           "next_due is still in the future: between "
                           "deadlines such a task is waiting, not late.",
        },
        "enabled": {"type": "boolean"},
        "health": {
            "type": "object",
            "additionalProperties": False,
            "required": ["state", "reason", "consecutive_failures"],
            "properties": {
                "state": {"enum": ["ok", "failing", "refused", "stale",
                                   "running", "never_ran", "disabled"]},
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
            "required": ["n_ok", "n_failing", "n_refused", "n_stale",
                         "n_never_ran", "n_running", "n_disabled",
                         "month_credits", "month_credits_cap"],
            "properties": {
                "n_ok": {"type": "integer"},
                "n_failing": {"type": "integer"},
                "n_refused": {"type": "integer",
                              "description": "Runs that finished without an error and "
                                             "without fetching: no_source or refused."},
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


#: The registry's four Due shapes, in the words the panel serves. A reader of
#: the board never meets the class name.
_DUE_KINDS: dict[type, str] = {
    registry.Calendar: "daily",
    registry.DeadlineRelative: "deadline",
    registry.Interval: "interval",
    registry.OnDemand: "on demand",
}

#: Keys a JSON ledger note may carry the token count under. The runner's note
#: format belongs to whoever writes the note, so this reads several spellings
#: and serves nothing when it recognises none of them.
_TOKEN_KEYS = ("tokens", "tokens_total", "total_tokens")
_TOKEN_PAIRS = (("tokens_in", "tokens_out"), ("input_tokens", "output_tokens"))


def _note_fields(note: Any) -> dict[str, Any]:
    """The model and token count a ledger note carries, read defensively.

    The note column is a free-text field that different writers fill
    differently, so this never assumes a shape. TWO shapes are recognised:

    * the whole note parsed as a JSON object (``note_is_json`` true), and
    * a trailing ``spend={...}`` line inside an otherwise prose note, which is
      the format :func:`fpl_edge.store.fetch_ledger.spend_note` writes.

    The second one is why ``content_analyse`` showed model None and tokens
    None on 2026-09-19 with a fully-populated spend line sitting in its note:
    this panel was built before that format landed and only ever tried the
    first shape. ``fields_from`` names which shape answered, so a reader is
    never left guessing why a prose note has a model beside it.

    Anything neither shape recognises comes back as absent, with the raw note
    served beside it untouched.
    """
    blank = {"note_is_json": False, "fields_from": None,
             "model": None, "tokens": None}
    if not note:
        return blank
    whole_json = True
    try:
        obj = json.loads(str(note))
    except (TypeError, ValueError):
        whole_json = False
        obj = None
    if not isinstance(obj, dict):
        whole_json = False
        obj = None
    if obj is None:
        # The prose case: the spend line is parsed by the module that writes
        # it, so the format has exactly one reader and one writer.
        from fpl_edge.store.fetch_ledger import parse_spend

        obj = parse_spend(str(note))
    if not isinstance(obj, dict):
        return blank
    source = "note_json" if whole_json else "spend_line"

    model = obj.get("model")
    model = str(model) if isinstance(model, str) else None

    tokens: float | None = None
    for key in _TOKEN_KEYS:
        value = obj.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            tokens = float(value)
            break
    if tokens is None:
        for a, b in _TOKEN_PAIRS:
            va, vb = obj.get(a), obj.get(b)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                tokens = float(va) + float(vb)
                break
    return {"note_is_json": whole_json, "fields_from": source,
            "model": model, "tokens": tokens}


def _last_success(wh) -> dict[str, dt.datetime]:
    """pipeline -> the last instant it finished successfully, in one query."""
    df = q(
        wh,
        "SELECT pipeline, max(finished_utc) AS f FROM fetch_run "
        "WHERE status IN ('ok', 'skipped_fresh') GROUP BY pipeline",
    )
    out: dict[str, dt.datetime] = {}
    for r in df.to_dict("records"):
        stamp = pd.to_datetime(r["f"], utc=True, errors="coerce")
        if not pd.isna(stamp):
            out[str(r["pipeline"])] = stamp.to_pydatetime()
    return out


def _age_days(started: Any, now: dt.datetime) -> float | None:
    """Days since a run started, to ONE decimal.

    It used to be served raw, so the board carried 15 significant figures of
    float for a number whose question is "is this from today or from last
    week". Rounding here rather than in the view keeps one answer for the
    browser, the API and the MCP tool; 0.1 days is 2.4 hours, which is finer
    than any staleness rule on this page reads.
    """
    stamp = pd.to_datetime(started, utc=True, errors="coerce")
    if pd.isna(stamp):
        return None
    return round(max(0.0, (now - stamp.to_pydatetime()).total_seconds() / 86400.0), 1)


def pipeline_board(wh) -> dict[str, Any]:
    """Every registered pipeline: health with its reason, last run, average
    time, next due, spend, the recent-run history, and the registry facts the
    health payload does not carry (the due kind, the stale window in words,
    whether the row is stale by that window, and the age of the last run in
    days). Every field is a stored column or a computation named in the
    result schema."""
    if not _ledger_exists(wh):
        return empty(
            "No fetch_run ledger in this warehouse yet -- no pipeline has ever "
            "run through the runner. Any scheduler tick or `uv run fpl "
            "pipelines run <task>` writes the first row."
        )

    now = dt.datetime.now(UTC)
    rows = health.pipeline_status(wh, now=now)
    runs = _recent_runs(wh)
    succeeded = _last_success(wh)
    for row in rows:
        row["runs"] = runs.get(row["id"], [])
        task = registry.by_id(row["id"])
        if task is None:
            # health.pipeline_status was handed tasks the registry does not
            # hold. Serve the row rather than raising, with the registry
            # facts absent and saying so.
            row.update(due_kind="on demand", stale_window="unknown",
                       stale_window_hours=0.0, last_success=None,
                       stale_by_window=False, last_run_age_days=None)
            continue
        window_h = task.stale_window.total_seconds() / 3600.0
        ok_at = succeeded.get(row["id"])
        row["due_kind"] = _DUE_KINDS.get(type(task.due), "on demand")
        row["stale_window"] = health.span_words(window_h)
        row["stale_window_hours"] = round(window_h, 3)
        row["last_success"] = ok_at.isoformat() if ok_at else None
        # Stale by the task's OWN window: never having succeeded counts,
        # because a task with no success is not fresh, it is unproven.
        #
        # Except on a deadline-relative task that is not yet owed. Those fire
        # around a deadline and then have nothing to do until the next one, so
        # between deadlines their last success is necessarily older than a
        # window measured in hours: final_solve_delivery, lineup_captain_check
        # and presser_projection_refresh all read "stale 17 days" on
        # 2026-09-19, which is arithmetically true and reads as three alarms
        # for three tasks behaving exactly as designed. A task whose next due
        # instant is in the future is waiting, not late, and the window has no
        # claim on it until that instant passes.
        overdue = (now - ok_at).total_seconds() / 3600.0 > window_h if ok_at else True
        next_due = pd.to_datetime(row.get("next_due"), utc=True, errors="coerce")
        waiting = (isinstance(task.due, registry.DeadlineRelative)
                   and not pd.isna(next_due)
                   and next_due.to_pydatetime() > now)
        row["stale_by_window"] = bool(overdue and not waiting)
        last = row.get("last_run")
        row["last_run_age_days"] = (
            None if not last else _age_days(last.get("started"), now))
        if last:
            last.update(_note_fields(last.get("note")))

    counts = {"ok": 0, "failing": 0, "refused": 0, "stale": 0,
              "never_ran": 0, "running": 0, "disabled": 0}
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
            "n_refused": counts["refused"],
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
        "words, the stale window and whether the row is past it, last run "
        "with its trigger and its note, next due, month credits, and the last "
        "ten runs. Reads only; triggering is a route."
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
