"""The fetch ledger and write-on-change: what ran, and what actually changed.

PIPELINES.md §4.2, decided with the owner. Two coupled ideas:

**The ledger** (``fetch_run``): one row per pipeline execution — status, rows
written vs rows confirmed-unchanged, credits, note. It exists because the
point-in-time store has a built-in ambiguity: an entity with no new ``as_of``
could mean *we never looked* or *we looked and nothing had changed*, and those
are different facts. The ledger is the disambiguator. It also carries the
"already latest" skip gate and everything the Pipelines panel renders.

**Write-on-change** (:func:`drop_unchanged`): before inserting, drop incoming
rows whose payload is identical to the entity's CURRENT LATEST stored row.
Measured motivation: the projections ingest was writing ~4.6k value-identical
rows per provider per day under fresh timestamps (fplform: 60k rows over 13
pulls, and it is the *most* volatile provider). The skipped count goes to the
ledger as ``rows_unchanged`` — the fact "refetched at T, unchanged" survives,
just not as fact-table bloat.

Two rules keep the PIT contract honest:

- Only a row NEWER than the stored latest may be skipped. A backfill row
  (``as_of`` at or before the latest) always writes through the normal path,
  even if its values coincide with today's — history is not deduped against
  the present.
- Freshness has TWO questions now, and displays must not conflate them:
  *when did the value last change* (max ``as_of`` in the fact table) and
  *when did we last check* (this ledger). A "team news: 48h old" chip is
  honest about change and silent about checking; the panel work in
  PIPELINES.md §6.4 wires both.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

UTC = dt.UTC

_DDL = """
CREATE TABLE IF NOT EXISTS fetch_run (
    run_id         VARCHAR PRIMARY KEY,
    pipeline       VARCHAR NOT NULL,
    source         VARCHAR,
    started_utc    TIMESTAMPTZ NOT NULL,
    finished_utc   TIMESTAMPTZ,
    status         VARCHAR NOT NULL,
    rows_written   INTEGER,
    rows_unchanged INTEGER,
    http_status    INTEGER,
    credits_spent  DOUBLE,
    note           VARCHAR,
    "trigger"      VARCHAR
)
"""

#: The statuses a run may finish with. Mirrors the DAG's outcome vocabulary
#: where the concepts overlap, deliberately -- one language for "what
#: happened" everywhere.
STATUSES = ("ok", "error", "refused", "skipped_fresh", "no_source")

#: Who asked for a run. "scheduler" is a DAG-tick firing; "ui"/"cli" are
#: manual triggers through fpl_edge.pipelines.runner.run_task.
TRIGGERS = ("scheduler", "ui", "cli")

#: The environment variable a run's trigger travels on into its subprocesses.
#: A registry task is one process that spawns several, each of which opens the
#: warehouse and writes its own ledger row; without this the child has no way
#: to know who asked, and the column it wrote was a guess.
TRIGGER_ENV = "FPL_EDGE_RUN_TRIGGER"


def trigger_from_env(default: str = "cli") -> str:
    """The trigger this process inherited, for a subprocess ledger writer.

    A subprocess started by :mod:`fpl_edge.pipelines.runner` inherits
    ``FPL_EDGE_RUN_TRIGGER`` and reports the trigger of the run that spawned
    it. Nothing set it means nothing spawned this: a person typed the command,
    so the default is "cli" and not "scheduler".

    This is the fix for the item PIPELINES_AUDIT.md raised. ``RunRecord``
    used to default ``trigger`` to "scheduler", so every subprocess-written
    row claimed the scheduler, and a settlement chain started from the
    Pipelines panel on 2026-09-09 left five rows saying a scheduler that was
    not even loaded had run them. An unset variable now says "somebody ran
    this by hand", which is true of every process nothing spawned.
    """
    value = (os.environ.get(TRIGGER_ENV) or "").strip()
    return value if value in TRIGGERS else default


@dataclass(frozen=True)
class CallUsage:
    """What a model backend REPORTED about one call. Never what we asked for.

    ``model_reported`` is the id the Claude CLI's ``modelUsage`` map or the
    SDK's ``response.model`` names, which is the only evidence of what
    actually ran. The requested id is already known from the pin in
    :mod:`fpl_edge.config` and is stored separately; conflating the two is how
    799 ``content_analysis`` rows came to be stamped ``claude-opus-5`` by
    assertion while the CLI was free to run whatever its default was.

    Every field is ``None`` when the backend said nothing. None means unknown
    and is stored as NULL; it is never rounded down to zero, because "this
    call spent nothing" and "nobody told us what this call spent" are
    different facts.
    """

    model_reported: str | None = None
    #: Every input token the call reported: fresh, cache-creation and
    #: cache-read together. One number with one stated meaning.
    tokens_in: int | None = None
    tokens_out: int | None = None

    @property
    def total(self) -> int:
        """Reported tokens, counting an unknown as zero. Budget arithmetic
        only: a budget that treated unknowns as infinite would stop on the
        first call against a backend that reports nothing, and one that
        treated them as a number would be inventing it."""
        return int(self.tokens_in or 0) + int(self.tokens_out or 0)

    def __add__(self, other: CallUsage) -> CallUsage:
        """Running total across a batch. A known value plus an unknown is the
        known value; only all-unknown stays unknown, so a partial report is
        never rounded up into a full one."""
        def plus(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return CallUsage(
            model_reported=other.model_reported or self.model_reported,
            tokens_in=plus(self.tokens_in, other.tokens_in),
            tokens_out=plus(self.tokens_out, other.tokens_out),
        )


#: How a run's model spend is written into the existing ``note`` column. The
#: note is free text that already reads "<outcome>: <detail>" and, on a
#: failure, carries a log tail after a "--- log tail ---" line. So the spend
#: goes on a line of its own, prefixed, as one line of JSON: a reader finds
#: the prefix, parses to end of line, and every other reader sees a note that
#: still reads as prose. fetch_run grows no columns.
SPEND_PREFIX = "spend="
_SPEND_RE = re.compile(rf"^{re.escape(SPEND_PREFIX)}(\{{.*\}})$", re.MULTILINE)


def spend_note(usage: CallUsage, **extra: Any) -> str:
    """One ``spend={...}`` line for a task that spent model tokens.

    ``extra`` carries whatever else that task measured -- ``calls``,
    ``budget_stopped``, ``skipped_no_body`` -- next to the three fields every
    spending task reports. Keys are sorted so two runs of the same task
    produce byte-comparable notes.
    """
    payload: dict[str, Any] = {
        "model": usage.model_reported,
        "tokens_in": usage.tokens_in,
        "tokens_out": usage.tokens_out,
    }
    payload.update(extra)
    return SPEND_PREFIX + json.dumps(payload, sort_keys=True, default=str)


def parse_spend(note: str | None) -> dict[str, Any] | None:
    """The spend payload out of a ledger note, or None if it carries none.

    Reads the LAST such line: a note that somehow accumulated two is telling
    us about the most recent write, and silently merging them would invent a
    total nobody measured.
    """
    if not note:
        return None
    found = _SPEND_RE.findall(note)
    for raw in reversed(found):
        try:
            parsed = json.loads(raw)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def ensure_table(wh) -> None:
    wh.sql(_DDL)
    # Tables created before the trigger column existed are upgraded in
    # place; DuckDB's IF NOT EXISTS makes this a no-op afterwards.
    wh.sql('ALTER TABLE fetch_run ADD COLUMN IF NOT EXISTS "trigger" VARCHAR')


class RunRecord:
    """Mutable per-run accumulator the ``record_run`` context hands out."""

    def __init__(self, pipeline: str, source: str | None = None, *,
                 trigger: str):
        if trigger not in TRIGGERS:
            raise ValueError(f"trigger {trigger!r} not in {TRIGGERS}")
        self.run_id = uuid.uuid4().hex
        self.pipeline = pipeline
        self.source = source
        self.started = dt.datetime.now(UTC)
        self.written = 0
        self.unchanged = 0
        self.credits = 0.0
        self.http_status: int | None = None
        self.note: str | None = None
        #: Set to a non-"ok" STATUSES value for a run that completed without
        #: raising but did not fetch: "skipped_fresh", "refused", "no_source".
        self.status: str | None = None
        #: Who asked (see TRIGGERS). Required, with no default, because the
        #: default used to be "scheduler" and only the pipelines runner ever
        #: overrode it: every subprocess-written row therefore claimed the
        #: scheduler, including five written on 2026-09-09 by a settlement
        #: chain a person started from the Pipelines panel while launchd was
        #: unloaded. A caller that does not know who asked calls
        #: :func:`trigger_from_env`, which answers from what spawned it.
        self.trigger: str = trigger
        #: Honest end-of-work stamp, set by runners that finish the work
        #: before they can reach the write lock. None means "stamp at insert".
        self.finished: dt.datetime | None = None

    def add(self, written: int, unchanged: int = 0) -> None:
        self.written += int(written)
        self.unchanged += int(unchanged)


@contextmanager
def record_run(wh, pipeline: str, source: str | None = None, *,
               trigger: str) -> Iterator[RunRecord]:
    """One ledger row per execution, written even when the run raises.

    The exception is re-raised after the row lands: the ledger observes,
    it never swallows. A raised run gets status="error" with the exception
    in the note (bounded); a clean exit gets the record's own status or "ok".

    ``trigger`` is required. A CLI entry point that can be run either by hand
    or as a step of a scheduled task passes :func:`trigger_from_env`.
    """
    ensure_table(wh)
    rec = RunRecord(pipeline, source, trigger=trigger)
    try:
        yield rec
    except BaseException as exc:
        _insert(wh, rec, status="error",
                note=f"{type(exc).__name__}: {exc}"[:500])
        raise
    _insert(wh, rec, status=rec.status or "ok", note=rec.note)


def record_finished(wh, rec: RunRecord, *, status: str,
                    note: str | None = None) -> None:
    """Write one already-finished run in a single statement.

    For runners that cannot hold the warehouse open across the work -- the
    deadline-DAG tick claims, closes, runs the task with the lock free, and
    reopens to record. It builds the :class:`RunRecord` before the task (so
    ``started_utc`` is honest) and lands it here in the outcome burst.
    """
    if status not in STATUSES:
        raise ValueError(f"status {status!r} not in {STATUSES}")
    ensure_table(wh)
    _insert(wh, rec, status=status, note=note)


def _insert(wh, rec: RunRecord, *, status: str, note: str | None) -> None:
    wh.sql(
        'INSERT INTO fetch_run (run_id, pipeline, source, started_utc, '
        'finished_utc, status, rows_written, rows_unchanged, http_status, '
        'credits_spent, note, "trigger") '
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rec.run_id, rec.pipeline, rec.source, rec.started,
         rec.finished or dt.datetime.now(UTC), status, rec.written,
         rec.unchanged, rec.http_status, rec.credits, note, rec.trigger),
    )


def last_run(wh, pipeline: str, source: str | None = None,
             *, ok_only: bool = True) -> dict[str, Any] | None:
    """The most recent (optionally successful) run, or None."""
    ensure_table(wh)
    where = "pipeline = ?" + (" AND source = ?" if source is not None else "")
    params: list[Any] = [pipeline] + ([source] if source is not None else [])
    if ok_only:
        where += " AND status IN ('ok', 'skipped_fresh')"
    df = wh.sql(
        f"SELECT * FROM fetch_run WHERE {where} "
        f"ORDER BY started_utc DESC LIMIT 1", params)
    return None if df.empty else df.iloc[0].to_dict()


def checked_within(wh, pipeline: str, hours: float,
                   source: str | None = None) -> bool:
    """The "already latest" gate: did a successful run finish inside the
    window? "skipped_fresh" counts -- a skip that verified freshness IS a
    check. An "error" run never counts, so failures always retry."""
    row = last_run(wh, pipeline, source, ok_only=True)
    if row is None or row.get("finished_utc") is None:
        return False
    fin = row["finished_utc"]
    if getattr(fin, "tzinfo", None) is None:
        import pandas as pd
        fin = pd.Timestamp(fin).tz_localize("UTC")
    return (dt.datetime.now(UTC) - fin) <= dt.timedelta(hours=hours)


# --------------------------------------------------------------- write-on-change

def drop_unchanged(con, table: str, entity_keys: list[str],
                   payload_cols: list[str], incoming_view: str) -> int:
    """Filter a registered incoming frame against each entity's latest row.

    Deletes from ``incoming_view`` (a DuckDB-registered temp view name) every
    row that (a) is STRICTLY NEWER than the entity's latest stored ``as_of``
    and (b) whose every payload column IS NOT DISTINCT FROM that latest row's.
    Returns how many were dropped -- the caller's ``rows_unchanged``.

    Rule (a) is the backfill guard: an older-or-equal as_of never enters this
    filter, so history cannot be deduplicated against the present. Rows at an
    as_of the table already holds are the existing anti-join/contradiction
    machinery's business, untouched here.

    Implemented as a mutation of the registered frame (DuckDB cannot DELETE
    from a registered view, so the caller re-registers the returned survivor
    frame) -- see the call sites, which own the registration lifecycle.
    """
    if not payload_cols:
        return 0
    ekey_join = " AND ".join(
        f"l.{k} IS NOT DISTINCT FROM i.{k}" for k in entity_keys)
    same_payload = " AND ".join(
        f"l.{c} IS NOT DISTINCT FROM i.{c}" for c in payload_cols)
    ekeys = ", ".join(entity_keys)
    survivors = con.execute(
        f"""
        WITH latest AS (
          SELECT * FROM (
            SELECT t.*, row_number() OVER (
              PARTITION BY {ekeys} ORDER BY as_of DESC) AS _rn
            FROM {table} t
          ) WHERE _rn = 1
        )
        SELECT i.* FROM {incoming_view} i
        LEFT JOIN latest l ON {ekey_join}
        WHERE l.as_of IS NULL          -- brand new entity: always write
           OR i.as_of <= l.as_of       -- backfill: never change-deduped
           OR NOT ({same_payload})     -- newer AND different: write
        """
    ).df()
    dropped = con.execute(
        f"SELECT count(*) FROM {incoming_view}").fetchone()[0] - len(survivors)
    con.unregister(incoming_view)
    con.register(incoming_view, survivors)
    return int(dropped)
