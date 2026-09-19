"""``boot.promote_incoming``: seeding a live volume without losing a database.

The warehouse reaches a running service as ``fpl.duckdb.incoming`` because the
container may hold ``fpl.duckdb`` open and DuckDB replays a WAL against
whatever file carries the name. Boot promotes the upload before anything opens
a connection, and a bad upload must cost nothing.
"""
from __future__ import annotations

import duckdb
import pytest

from fpl_edge.platform import boot


def _warehouse(path, players: int) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE dim_player (code INTEGER, web_name VARCHAR)")
    for i in range(players):
        con.execute("INSERT INTO dim_player VALUES (?, ?)", [i, f"p{i}"])
    con.close()


def _report(db) -> boot.BootReport:
    return boot.BootReport(data_dir=str(db.parent), db_path=str(db))


def test_no_upload_leaves_the_database_alone(tmp_path):
    db = tmp_path / "fpl.duckdb"
    _warehouse(db, 5)
    before = db.stat().st_size
    report = _report(db)
    boot.promote_incoming(db, report)
    assert report.promoted is None
    assert db.stat().st_size == before


def test_an_upload_replaces_the_database_and_is_reported(tmp_path):
    db = tmp_path / "fpl.duckdb"
    _warehouse(db, 1)
    incoming = tmp_path / "fpl.duckdb.incoming"
    _warehouse(incoming, 40)
    report = _report(db)
    boot.promote_incoming(db, report)
    assert not incoming.exists(), "the upload is moved, never copied and left"
    assert report.promoted["players"] == 40
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 40
    finally:
        con.close()


def test_the_replaced_databases_wal_is_dropped(tmp_path):
    """A WAL belonging to the old file would be replayed into the new one."""
    db = tmp_path / "fpl.duckdb"
    _warehouse(db, 1)
    wal = tmp_path / "fpl.duckdb.wal"
    wal.write_bytes(b"not this database's write-ahead log")
    _warehouse(tmp_path / "fpl.duckdb.incoming", 3)
    report = _report(db)
    boot.promote_incoming(db, report)
    assert not wal.exists()
    assert report.promoted["wal_dropped"] is True


def test_a_file_that_is_not_a_warehouse_fails_the_boot_and_keeps_the_old_one(tmp_path):
    db = tmp_path / "fpl.duckdb"
    _warehouse(db, 7)
    (tmp_path / "fpl.duckdb.incoming").write_bytes(b"half an scp")
    report = _report(db)
    with pytest.raises(boot.BootFailure) as caught:
        boot.promote_incoming(db, report)
    assert "fpl.duckdb.incoming" in str(caught.value)
    con = duckdb.connect(str(db), read_only=True)
    try:
        assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 7
    finally:
        con.close()


def test_a_schema_only_upload_is_refused(tmp_path):
    """The shape a first boot leaves behind, uploaded by mistake."""
    db = tmp_path / "fpl.duckdb"
    _warehouse(db, 9)
    _warehouse(tmp_path / "fpl.duckdb.incoming", 0)
    with pytest.raises(boot.BootFailure) as caught:
        boot.promote_incoming(db, _report(db))
    assert "dim_player" in str(caught.value)


def test_an_upload_onto_an_empty_volume_is_the_first_database(tmp_path):
    db = tmp_path / "fpl.duckdb"
    _warehouse(tmp_path / "fpl.duckdb.incoming", 12)
    report = _report(db)
    boot.promote_incoming(db, report)
    assert db.is_file()
    assert report.promoted["replaced_bytes"] is None
