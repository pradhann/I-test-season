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
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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
    note           VARCHAR
)
"""

#: The statuses a run may finish with. Mirrors the DAG's outcome vocabulary
#: where the concepts overlap, deliberately -- one language for "what
#: happened" everywhere.
STATUSES = ("ok", "error", "refused", "skipped_fresh", "no_source")


def ensure_table(wh) -> None:
    wh.sql(_DDL)


class RunRecord:
    """Mutable per-run accumulator the ``record_run`` context hands out."""

    def __init__(self, pipeline: str, source: str | None):
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

    def add(self, written: int, unchanged: int = 0) -> None:
        self.written += int(written)
        self.unchanged += int(unchanged)


@contextmanager
def record_run(wh, pipeline: str, source: str | None = None,
               ) -> Iterator[RunRecord]:
    """One ledger row per execution, written even when the run raises.

    The exception is re-raised after the row lands: the ledger observes,
    it never swallows. A raised run gets status="error" with the exception
    in the note (bounded); a clean exit gets the record's own status or "ok".
    """
    ensure_table(wh)
    rec = RunRecord(pipeline, source)
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
        "INSERT INTO fetch_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rec.run_id, rec.pipeline, rec.source, rec.started,
         dt.datetime.now(UTC), status, rec.written, rec.unchanged,
         rec.http_status, rec.credits, note),
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
