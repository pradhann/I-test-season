"""Index repair and creator-identity linking: the commands that fix the store.

Neither is on the nightly path. ``cmd_repair_index`` rebuilds a table whose
index probe says it has gone bad, and ``cmd_link_identities`` writes the
``creator_entry`` table from the narrow name rule in ``content/identity.py``.

Split out of ``fpl_edge/ingest/content/pipeline.py`` (ARCHITECTURE_REVIEW.md
Section 3 and Section 4 row 16).
"""

from __future__ import annotations

import argparse
import contextlib

from fpl_edge.ingest.content.analyse_cmd import _write_with_retry
from fpl_edge.ingest.content.pipeline_common import _now
from fpl_edge.store import Warehouse

_INDEXED_TABLES: tuple[str, ...] = (
    "content_item", "content_claim", "content_analysis", "content_insight",
    "content_analysis_skip", "content_transcribe_skip", "content_source",
    "transcript_segment", "transcript_provenance", "content_item_asset",
)


INDEX_PROBE_KEYS = 48


def _index_is_healthy(con, table: str) -> tuple[bool, str]:
    """Round-trip ONE existing row through the primary-key index, then undo it.

    DuckDB checks a primary key by walking an ART index that is persisted
    alongside the data, and that index can end up disagreeing with the rows
    it points at. When it does, reads and appends of untouched key ranges
    keep working, so every count and every SELECT says the table is fine --
    the failure only appears the moment something DELETEs or REPLACEs a row
    that lives in the broken part of the tree, and it appears as
    ``Invalid Input Error: Failed to delete all rows from index. Only deleted
    0 out of 1 rows``, which is FATAL: it invalidates the connection and takes
    the whole process with it.

    So the probe has to be a real delete. It is wrapped in a transaction that
    is always rolled back, and it is the only way to answer the question
    without waiting for a nightly job to answer it for us.
    """
    cols = [r[0] for r in con.execute(
        "SELECT column_name FROM duckdb_constraints() c, "
        "  UNNEST(c.constraint_column_names) AS t(column_name) "
        "WHERE c.table_name = ? AND c.constraint_type = 'PRIMARY KEY'",
        [table]).fetchall()]
    if not cols:
        return True, "no primary key"
    where = " AND ".join(f"{c} IS NOT DISTINCT FROM ?" for c in cols)
    # ART corruption is KEY-PREFIX-LOCAL: on 2026-09-07 content_claim passed a
    # one-key probe while every INSERT of FPL Family's claim ids died inside
    # FixedSizeAllocator. One key answers for one leaf. So probe a spread:
    # first, last, and every n/INDEX_PROBE_KEYS-th row in primary-key order,
    # so a broken region of the tree is likely to be walked. Still not proof
    # (an allocator fault on the insert path can evade any delete probe), and
    # the message says so; --force exists for exactly that case.
    key_list = ", ".join(cols)
    rows = con.execute(
        f"WITH k AS (SELECT {key_list}, row_number() OVER (ORDER BY {key_list}) AS rn, "
        f"           count(*) OVER () AS n FROM {table}) "
        f"SELECT {key_list} FROM k "
        f"WHERE rn = 1 OR rn = n OR rn % GREATEST(1, n // {INDEX_PROBE_KEYS}) = 0 "
        f"ORDER BY rn").fetchall()
    if not rows:
        return True, "empty table"
    total = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    try:
        con.execute("BEGIN")
        for row in rows:
            con.execute(f"DELETE FROM {table} WHERE {where}", list(row))
        con.execute("ROLLBACK")
        return True, (f"index round-trip ok on {len(rows)} of {total} keys spread "
                      f"across the tree (a delete probe; insert-path faults can "
                      f"still evade it, see --force)")
    except Exception as exc:  # noqa: BLE001 - the failure IS the answer
        # A FATAL index error has already invalidated the connection; the
        # rollback is best-effort and its own failure adds nothing.
        with contextlib.suppress(Exception):
            con.execute("ROLLBACK")
        return False, f"{type(exc).__name__}: {str(exc)[:200]}"


def _rebuild_table(con, table: str) -> int:
    """Recreate ``table`` from its own rows, so its indexes are built fresh.

    The DDL is DuckDB's own ``duckdb_tables().sql``, not a copy of the
    migration, so a table that has been ALTERed since is rebuilt as it
    actually is rather than as it was first written.
    """
    ddl = con.execute(
        "SELECT sql FROM duckdb_tables() WHERE table_name = ?",
        [table]).fetchone()
    if ddl is None:
        raise KeyError(f"no table {table!r}")
    tmp = f"{table}__rebuild"
    create = ddl[0].replace(f"CREATE TABLE {table}(", f"CREATE TABLE {tmp}(", 1)
    if tmp not in create:
        raise RuntimeError(f"could not retarget the DDL for {table!r}: {ddl[0][:120]}")
    con.execute(f"DROP TABLE IF EXISTS {tmp}")
    con.execute("BEGIN")
    con.execute(create)
    con.execute(f"INSERT INTO {tmp} SELECT * FROM {table}")
    n = con.execute(f"SELECT count(*) FROM {tmp}").fetchone()[0]
    con.execute(f"DROP TABLE {table}")
    con.execute(f"ALTER TABLE {tmp} RENAME TO {table}")
    con.execute("COMMIT")
    con.execute("CHECKPOINT")
    return int(n)


def cmd_repair_index(args: argparse.Namespace) -> int:
    """Verify -- and with --apply, rebuild -- the primary-key indexes.

    Written because a corrupt index on ``content_analysis`` cost this project
    a week of stale data and gave no usable error while doing it. The nightly
    transcription failed on 2026-09-01 and -09-02 forty minutes in, with a
    ledger note that was three lines of a DuckDB DataChunk dump; the analyse
    step failed the same way. Both were the same delete-through-a-broken-ART,
    and nothing in the system could say so.

    Verification is the default and it writes nothing. ``--apply`` rebuilds
    only the tables that failed, and reports the row count each way so a
    rebuild that lost a row is visible immediately rather than later.
    """
    from fpl_edge.store.warehouse import LeasedWarehouse

    tables = ([t.strip() for t in args.table.split(",") if t.strip()]
              if args.table else list(_INDEXED_TABLES))
    lease = LeasedWarehouse(args.db, lock_timeout_s=120.0)
    broken: list[tuple[str, str]] = []
    try:
        con = lease._con
        present = {r[0] for r in con.execute(
            "SELECT table_name FROM duckdb_tables()").fetchall()}
        for table in tables:
            if table not in present:
                print(f"  --      {table:<26} not in this warehouse")
                continue
            ok, why = _index_is_healthy(con, table)
            print(f"  {'ok' if ok else 'BROKEN':<7} {table:<26} {why}")
            if not ok:
                broken.append((table, why))
            elif args.force:
                # A round-trip that passes is NOT proof the index is sound:
                # this corruption is key-range-local, so a probe on a healthy
                # branch says "ok" while a delete two keys away is still
                # fatal. --force rebuilds anyway, which is what you want after
                # recovering a warehouse whose WAL replay failed on this index.
                broken.append((table, "forced rebuild"))
        print()
        if not broken:
            print(f"{len(tables)} table(s) checked, every primary-key index "
                  f"round-tripped. Nothing to repair.")
            return 0
        failed = [t for t, why in broken if why != "forced rebuild"]
        if failed:
            print(f"{len(failed)} table(s) have an unusable primary-key index: "
                  f"{', '.join(failed)}")
        forced = [t for t, why in broken if why == "forced rebuild"]
        if forced:
            print(f"{len(forced)} table(s) queued for a forced rebuild: "
                  f"{', '.join(forced)}")
        if not args.apply:
            print("Nothing was changed. Re-run with --apply to rebuild them.")
            return 1
    finally:
        if not broken or not args.apply:
            lease.release()

    # A FATAL index error invalidates the connection, so the repair takes a
    # fresh one rather than reusing the one that just probed the damage.
    lease.release()
    repaired: list[str] = []
    for table, _ in broken:
        lease = LeasedWarehouse(args.db, lock_timeout_s=120.0)
        try:
            before = int(lease._con.execute(
                f"SELECT count(*) FROM {table}").fetchone()[0])
            after = _rebuild_table(lease._con, table)
            print(f"  rebuilt {table:<26} {before} rows in, {after} rows out"
                  + ("" if before == after else "  <-- ROW COUNT CHANGED"))
            repaired.append(table)
        finally:
            lease.release()

    lease = LeasedWarehouse(args.db, lock_timeout_s=120.0)
    try:
        still: list[str] = []
        for table in repaired:
            ok, why = _index_is_healthy(lease._con, table)
            print(f"  {'ok' if ok else 'STILL BROKEN':<7} {table:<26} {why}")
            if not ok:
                still.append(table)
    finally:
        lease.release()
    return 1 if still else 0


def cmd_link_identities(args: argparse.Namespace) -> int:
    """Link creators to FPL entries where verified evidence already exists.

    Deliberately small. It writes a link ONLY where a creator's name is
    exactly, after accent folding, a name the FPL API itself reported for an
    entry. No nickname matching, no channel-name resemblance, no guessed IDs.
    Everything else is written down as unresolved WITH its reason.
    """
    from fpl_edge.ingest.content.identity import link_creator_entries

    ddl = """
    CREATE TABLE IF NOT EXISTS creator_entry (
        creator     VARCHAR NOT NULL,
        entry_id    BIGINT,
        player_name VARCHAR,
        entry_name  VARCHAR,
        method      VARCHAR NOT NULL,
        verified    BOOLEAN NOT NULL,
        reason      VARCHAR,
        as_of       TIMESTAMP WITH TIME ZONE NOT NULL,
        PRIMARY KEY (creator)
    )
    """
    with Warehouse(args.db, read_only=True) as wh:
        links = link_creator_entries(wh)

    as_of = _now()
    resolved = [x for x in links if x.entry_id is not None]

    def _write(wh):
        wh.sql(ddl)
        for x in links:
            wh.sql("INSERT OR REPLACE INTO creator_entry VALUES (?,?,?,?,?,?,?,?)",
                   [x.creator, x.entry_id, x.player_name, x.entry_name,
                    x.method, x.verified, x.reason or None, as_of])

    if not args.dry_run:
        _write_with_retry(args.db, _write)

    for x in resolved:
        print(f"  LINK  {x.creator:<26} -> entry {x.entry_id} "
              f"({x.player_name}) via {x.method}")
    print(f"\nlinked:     {len(resolved)} of {len(links)} creators")
    print(f"unresolved: {len(links) - len(resolved)}")
    from collections import Counter
    for reason, n in Counter(x.reason for x in links if x.entry_id is None).most_common():
        print(f"  {n:>3}  {reason}")
    if not resolved:
        print("\nZero links is the honest result here, not a failure: every "
              "creator in the roster is a CHANNEL name, and no channel name "
              "equals a name the FPL API reported for an entry. The alternative "
              "-- matching 'Let's Talk FPL' to 'Andy LTFPL' on resemblance -- "
              "would be a guess written down as an identity.")
    if args.dry_run:
        print("\n--dry-run: nothing written")
    return 0
