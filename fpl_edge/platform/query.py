"""The one guarded query path.

Argus's rule (docs/platform/argus_architecture.md §2.4, §2.5 item 7): chat, the
UI and panel scripts must funnel through a *single* execution policy, so that
guards, caps and provenance live in exactly one place rather than being
reimplemented per surface. Here that policy is :func:`guarded_query`.

What the guard actually buys us is not protection from a malicious user -- this
is a single-operator local platform and the operator already owns the file. It
buys three things that matter every day:

* **The write lock stays free.** Every read opens ``Warehouse.read_copy()``: a
  private file copy, closed the moment the query returns. The Telegram bot under
  launchd holds write leases; a UI panel that opened the live database read-only
  would block the next ingest for as long as a browser tab stayed open.
* **A typo cannot mutate history.** The warehouse is append-only by design and
  the whole point-in-time story rests on that. An ``UPDATE`` typed into a query
  box would be silent, permanent and unattributable, so write verbs are rejected
  before DuckDB ever sees the text.
* **A cartesian join cannot wedge the process.** Row and byte caps turn a
  mistake into an error message instead of a hung server and a 4GB JSON body.

``as_of`` routes through :meth:`Warehouse.snapshot_at` semantics: the SQL runs
against a connection whose point-in-time tables have been replaced by views
filtered to the instant, so a query written without an ``as_of`` predicate still
cannot read the future.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.store.warehouse import DEFAULT_DB, PIT_KEYS, Warehouse

#: Hard ceilings. Deliberately generous for a local operator and deliberately
#: finite: the failure mode we are preventing is an accident, not an attack.
MAX_ROWS = 10_000
MAX_BYTES = 5 * 1024 * 1024

#: Statements that change data or schema. Matched as whole words so a column
#: named ``updated_utc`` or a player called "Delete" in a string literal does
#: not trip the guard.
_WRITE_VERBS = (
    "insert", "update", "delete", "drop", "create", "alter", "truncate",
    "replace", "merge", "grant", "revoke", "attach", "detach", "copy",
    "export", "import", "install", "load", "set", "reset", "call", "pragma",
    "vacuum", "checkpoint", "begin", "commit", "rollback",
)
_WRITE_RE = re.compile(r"\b(" + "|".join(_WRITE_VERBS) + r")\b", re.I)
_READ_START = re.compile(r"^\s*(select|with|explain|describe|show|summarize|table|from|values)\b", re.I)
#: Statements that can legally be wrapped in ``SELECT * FROM (...) LIMIT n``.
_WRAPPABLE = re.compile(r"^\s*(select|with|table|from|values)\b", re.I)


class QueryError(ValueError):
    """A query the guard refused, or one whose result exceeded the caps."""


@contextlib.contextmanager
def read_copy(db: Path | str = DEFAULT_DB):
    """``Warehouse.read_copy`` that also deletes the copy afterwards.

    ``read_copy`` mkdtemp's a private copy of the database and, by design, does
    not own its lifetime -- a batch job exits and the OS reaps it. A server does
    not exit. At one copy per panel refresh and ~47MB per copy, a browser tab
    left open overnight fills the disk, so every platform read goes through
    here instead.
    """
    wh = Warehouse.read_copy(db)
    tmp_dir = Path(wh.path).parent
    try:
        yield wh
    finally:
        wh.close()
        if tmp_dir.name.startswith("fpl-read-") or tmp_dir.parent.name.startswith("fpl-read-"):
            shutil.rmtree(tmp_dir, ignore_errors=True)


@dataclass(frozen=True)
class QueryResult:
    sql: str
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    truncated: bool
    as_of: str | None = None
    elapsed_ms: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sql": self.sql,
            "columns": self.columns,
            "rows": self.rows,
            "row_count": self.row_count,
            "truncated": self.truncated,
            "as_of": self.as_of,
            "elapsed_ms": self.elapsed_ms,
            "notes": self.notes,
        }


def strip_sql_noise(sql: str) -> str:
    """Remove comments and string literals so verb matching sees only code.

    Without this, ``SELECT 'do not delete' AS note`` is rejected and
    ``SELECT 1 -- delete`` is rejected, both wrongly; worse, ``/* */`` comments
    are how one hides a second statement from a naive splitter.
    """
    out = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
            continue
        if ch == "/" and sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
            continue
        if ch in "'\"":
            quote = ch
            j = i + 1
            while j < n:
                if sql[j] == quote:
                    if j + 1 < n and sql[j + 1] == quote:
                        j += 2
                        continue
                    break
                j += 1
            # A quoted identifier is code, not data; keep it so that
            # "delete" as a column name still reads naturally, but blank the
            # contents of single-quoted string literals.
            out.append(sql[i : j + 1] if quote == '"' else " '' ")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def assert_single_statement(sql: str) -> None:
    body = strip_sql_noise(sql).strip().rstrip(";").strip()
    if ";" in body:
        raise QueryError(
            "multiple statements are not allowed: send one SELECT at a time. "
            "A trailing semicolon is fine; a second statement is not."
        )
    if not body:
        raise QueryError("empty query")


def assert_read_only(sql: str) -> None:
    body = strip_sql_noise(sql).strip().rstrip(";").strip()
    if not _READ_START.match(body):
        raise QueryError(
            f"only read statements are allowed; this one starts with "
            f"{body.split()[0] if body.split() else '?'!r}. "
            f"The warehouse is append-only and every write goes through an "
            f"ingest job so that it carries provenance."
        )
    hit = _WRITE_RE.search(body)
    if hit:
        raise QueryError(
            f"refused: the statement contains the write verb {hit.group(1).lower()!r}. "
            f"Reads only -- writes belong in an ingest job where they are recorded."
        )


def _pit_view_sql(as_of: dt.datetime, catalog: str) -> list[str]:
    """Replace each point-in-time table with a latest-row-per-entity view.

    This is the same window function :meth:`Snapshot.table` uses. Doing it as
    views means arbitrary user SQL inherits point-in-time correctness without
    the author having to remember an ``as_of <= ?`` predicate -- the failure
    mode being defended against is forgetting, not lying.

    The base table must be named ``<catalog>.main.<table>``, not ``main.<table>``:
    an unqualified or ``main.``-qualified reference resolves back to the temp
    view being defined and DuckDB refuses it as infinite recursion. The catalog
    alias is read from the connection rather than assumed, because it is derived
    from the database *filename* and a test warehouse is not called ``fpl``.
    """
    stmts = []
    stamp = as_of.astimezone(dt.timezone.utc).isoformat()
    for table, keys in PIT_KEYS.items():
        cols = ", ".join(keys)
        stmts.append(
            f'CREATE OR REPLACE TEMP VIEW {table} AS '
            f"SELECT * EXCLUDE (rn) FROM ("
            f"  SELECT *, ROW_NUMBER() OVER (PARTITION BY {cols} ORDER BY as_of DESC) AS rn"
            f'  FROM "{catalog}".main.{table} WHERE as_of <= TIMESTAMPTZ \'{stamp}\''
            f") WHERE rn = 1"
        )
    return stmts


def catalog_name(wh: Warehouse) -> str:
    """The attached database's alias, e.g. ``fpl`` for ``fpl.duckdb``."""
    rows = wh.sql("SELECT database_name FROM duckdb_databases() WHERE NOT internal")
    if rows.empty:
        raise QueryError("no user database attached to this connection")
    return str(rows.iloc[0]["database_name"])


def _frame_to_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    """JSON-safe rows. Timestamps become ISO strings, NaN becomes None."""
    if df.empty:
        return []
    safe = df.copy()
    for col in safe.columns:
        if pd.api.types.is_datetime64_any_dtype(safe[col]):
            safe[col] = safe[col].astype(str)
    safe = safe.where(pd.notna(safe), None)
    rows = safe.to_dict(orient="records")
    for row in rows:
        for k, v in row.items():
            if isinstance(v, (pd.Timestamp, dt.datetime, dt.date)):
                row[k] = v.isoformat()
            elif hasattr(v, "item"):
                try:
                    row[k] = v.item()
                except (ValueError, AttributeError):
                    row[k] = str(v)
            elif isinstance(v, float) and v != v:
                row[k] = None
    return rows


def guarded_query(
    sql: str,
    params: Sequence[Any] = (),
    *,
    as_of: dt.datetime | None = None,
    db: Path | str = DEFAULT_DB,
    max_rows: int = MAX_ROWS,
    max_bytes: int = MAX_BYTES,
    warehouse: Warehouse | None = None,
) -> QueryResult:
    """Execute one read-only statement against a private read copy.

    ``params`` binds ``?`` placeholders. It is not exposed over HTTP -- the
    query endpoint takes literal SQL -- but panel scripts and chat tools use it
    so that a season string or an entry id is never formatted into SQL text.
    Interpolation is how a guard that reads SQL correctly still ends up
    executing something the caller did not intend.

    ``warehouse`` exists for callers that already hold a read copy (panel
    scripts run several queries per invocation and should not copy the file
    once per query). It is never the live writable handle in production code.
    """
    if not isinstance(sql, str) or not sql.strip():
        raise QueryError("sql must be a non-empty string")
    if len(sql) > 100_000:
        raise QueryError("sql text is implausibly long (>100k chars)")
    assert_single_statement(sql)
    assert_read_only(sql)

    started = dt.datetime.now(dt.timezone.utc)
    notes: list[str] = []
    with contextlib.ExitStack() as stack:
        wh = warehouse if warehouse is not None else stack.enter_context(read_copy(db))
        if as_of is not None:
            if as_of.tzinfo is None:
                raise QueryError("as_of must be timezone-aware UTC")
            for stmt in _pit_view_sql(as_of, catalog_name(wh)):
                wh.sql(stmt)
            notes.append(
                "point-in-time tables were replaced by views filtered to as_of; "
                "rows written after that instant are invisible to this query"
            )
        # Fetch one extra row so truncation is detectable rather than assumed.
        body = sql.strip().rstrip(";").strip()
        if _WRAPPABLE.match(body):
            # The newlines are not cosmetic: a statement ending in a `--`
            # comment would otherwise swallow the closing paren, and the query
            # would die with a parser error that names nothing the caller wrote.
            df = wh.sql(
                f"SELECT * FROM (\n{body}\n) AS _guarded LIMIT {int(max_rows) + 1}",
                params,
            )
        else:
            # EXPLAIN / DESCRIBE / SHOW are not table subqueries in DuckDB.
            # They are also bounded by construction, so capping after the fact
            # is honest rather than a hole.
            df = wh.sql(body, params)

    truncated = len(df) > max_rows
    if truncated:
        df = df.head(max_rows)
        notes.append(f"result truncated to the {max_rows}-row cap")

    approx = int(df.memory_usage(deep=True).sum()) if not df.empty else 0
    if approx > max_bytes:
        raise QueryError(
            f"result is ~{approx / 1e6:.1f}MB, over the {max_bytes / 1e6:.0f}MB cap. "
            f"Aggregate or project fewer columns in SQL rather than in the client."
        )

    elapsed = int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000)
    return QueryResult(
        sql=sql.strip(),
        rows=_frame_to_rows(df),
        columns=[str(c) for c in df.columns],
        row_count=int(len(df)),
        truncated=truncated,
        as_of=as_of.astimezone(dt.timezone.utc).isoformat() if as_of else None,
        elapsed_ms=elapsed,
        notes=notes,
    )
