"""repair-index's health probe walks a SPREAD of keys, not one.

On 2026-09-07 content_claim passed a one-key probe while inserts of one
source's ids crashed inside the ART allocator; corruption is key-prefix-local,
so one key answers for one leaf. The probe now round-trips first, last and a
stride of keys in primary-key order and admits what it cannot see.
"""

from __future__ import annotations

import duckdb

from fpl_edge.ingest.content.pipeline import INDEX_PROBE_KEYS, _index_is_healthy


def _table(con, n):
    con.execute("CREATE TABLE t (k VARCHAR PRIMARY KEY, v INTEGER NOT NULL)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(f"key{i:05d}", i) for i in range(n)])


def test_probe_samples_across_the_key_order_and_says_so():
    con = duckdb.connect()
    _table(con, 1000)
    ok, why = _index_is_healthy(con, "t")
    assert ok
    # first + last + every (1000 // 48)th row: well over one key, well under all
    probed = int(why.split("ok on ")[1].split(" of ")[0])
    assert INDEX_PROBE_KEYS <= probed <= INDEX_PROBE_KEYS + 3
    assert "of 1000 keys" in why
    assert "insert-path faults can still evade it" in why, "the probe must state its blind spot"
    # the probe is a rolled-back delete: nothing may have changed
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1000


def test_small_tables_probe_every_key_and_empty_tables_pass():
    con = duckdb.connect()
    _table(con, 5)
    ok, why = _index_is_healthy(con, "t")
    assert ok and "5 of 5 keys" in why
    con.execute("DELETE FROM t")
    assert _index_is_healthy(con, "t") == (True, "empty table")


def test_a_table_without_a_primary_key_is_not_probed():
    con = duckdb.connect()
    con.execute("CREATE TABLE u (k VARCHAR, v INTEGER)")
    con.execute("INSERT INTO u VALUES ('a', 1)")
    assert _index_is_healthy(con, "u") == (True, "no primary key")
