"""The guarded query path: what it refuses, and what it caps.

These are the tests that matter most in the platform, because ``guarded_query``
is the single chokepoint every data surface funnels through -- panels, the
query endpoint and the chat tools. A hole here is a hole everywhere.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.platform.query import (
    MAX_ROWS,
    QueryError,
    assert_read_only,
    assert_single_statement,
    guarded_query,
    strip_sql_noise,
)
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc


@pytest.fixture()
def db(tmp_path):
    """A tiny warehouse with two point-in-time facts a day apart."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": "2026-27", "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": pd.Timestamp("2026-08-01", tz="UTC")},
        {"season": "2026-27", "team_code": 2, "team_id": 2, "name": "Chelsea",
         "short_name": "CHE", "as_of": pd.Timestamp("2026-08-01", tz="UTC")},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": "2026-27", "code": 100, "element_id": 1, "web_name": "Early",
         "first_name": "E", "second_name": "Arly", "position": 3, "team_code": 1,
         "as_of": pd.Timestamp("2026-08-01", tz="UTC")},
        {"season": "2026-27", "code": 200, "element_id": 2, "web_name": "Late",
         "first_name": "L", "second_name": "Ate", "position": 4, "team_code": 2,
         "as_of": pd.Timestamp("2026-08-10", tz="UTC")},
    ]))
    wh.close()
    return path


WRITES = [
    "UPDATE fact_player_state SET price_tenths = 1",
    "DELETE FROM dim_player",
    "DROP TABLE dim_team",
    "INSERT INTO dim_team VALUES (1)",
    "CREATE TABLE evil (x INT)",
    "ALTER TABLE dim_team ADD COLUMN x INT",
    "ATTACH '/tmp/other.db' AS other",
    "COPY dim_team TO '/tmp/leak.csv'",
    "PRAGMA database_list",
    "TRUNCATE dim_team",
]


@pytest.mark.parametrize("sql", WRITES)
def test_write_verbs_are_refused(sql, db):
    with pytest.raises(QueryError):
        guarded_query(sql, db=db)


def test_write_hidden_after_a_read_is_still_refused(db):
    """A write verb anywhere in the statement is fatal, not just at the front.

    ``SELECT`` prefixing does not make the rest safe: DuckDB's ``CREATE TABLE
    AS SELECT`` and CTE-attached side effects both start with a read-looking
    token.
    """
    with pytest.raises(QueryError, match="write verb"):
        guarded_query(
            "WITH x AS (SELECT 1) SELECT * FROM x UNION ALL SELECT 1 FROM "
            "(INSERT INTO dim_team VALUES (9))", db=db,
        )


@pytest.mark.parametrize("sql", [
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE dim_team",
    "SELECT 1;DELETE FROM idea",
])
def test_multi_statement_is_refused(sql, db):
    with pytest.raises(QueryError, match="multiple statements"):
        guarded_query(sql, db=db)


def test_trailing_semicolon_is_fine(db):
    assert guarded_query("SELECT 1 AS one;", db=db).rows == [{"one": 1}]


def test_a_comment_is_not_a_statement(db):
    """`-- DROP TABLE` in a comment is text, and must not be read as a verb."""
    res = guarded_query("SELECT 1 AS one -- DROP TABLE dim_team\n", db=db)
    assert res.rows == [{"one": 1}]


def test_a_string_literal_is_not_a_verb(db):
    """A player note reading 'delete' must not trip the guard."""
    res = guarded_query("SELECT 'do not delete me' AS note", db=db)
    assert res.rows == [{"note": "do not delete me"}]


def test_strip_sql_noise_blanks_literals_and_comments():
    out = strip_sql_noise("SELECT 'drop' /* delete */ -- insert\n, 1")
    assert "drop" not in out and "delete" not in out and "insert" not in out


def test_empty_query_is_refused():
    with pytest.raises(QueryError):
        assert_single_statement("   ")
    with pytest.raises(QueryError):
        guarded_query("   ")


def test_non_select_start_is_refused():
    with pytest.raises(QueryError, match="only read statements"):
        assert_read_only("VACUUM")


def test_row_cap_truncates_and_says_so(db):
    res = guarded_query(
        "SELECT * FROM range(50) t(i)", db=db, max_rows=10,
    )
    assert res.row_count == 10
    assert res.truncated is True
    assert any("truncated" in n for n in res.notes)


def test_row_cap_is_not_reported_when_not_hit(db):
    res = guarded_query("SELECT * FROM range(3) t(i)", db=db, max_rows=10)
    assert res.row_count == 3 and res.truncated is False and res.notes == []


def test_byte_cap_refuses_an_oversized_result(db):
    with pytest.raises(QueryError, match="over the"):
        guarded_query(
            "SELECT repeat('x', 2000) AS blob FROM range(5000)",
            db=db, max_bytes=1024,
        )


def test_default_row_cap_is_ten_thousand():
    assert MAX_ROWS == 10_000


def test_as_of_hides_facts_recorded_later(db):
    """The whole point of PIT routing: a query with no as_of predicate of its
    own still cannot see a row that did not exist at the instant asked for."""
    before = guarded_query(
        "SELECT web_name FROM dim_player ORDER BY code",
        as_of=dt.datetime(2026, 8, 5, tzinfo=UTC), db=db,
    )
    assert [r["web_name"] for r in before.rows] == ["Early"]

    after = guarded_query(
        "SELECT web_name FROM dim_player ORDER BY code",
        as_of=dt.datetime(2026, 8, 15, tzinfo=UTC), db=db,
    )
    assert [r["web_name"] for r in after.rows] == ["Early", "Late"]


def test_as_of_is_reported_in_the_result(db):
    res = guarded_query(
        "SELECT 1 AS x", as_of=dt.datetime(2026, 8, 5, tzinfo=UTC), db=db)
    assert res.as_of.startswith("2026-08-05")
    assert any("as_of" in n for n in res.notes)


def test_naive_as_of_is_refused(db):
    with pytest.raises(QueryError, match="timezone-aware"):
        guarded_query("SELECT 1", as_of=dt.datetime(2026, 8, 5), db=db)


def test_params_bind_rather_than_interpolate(db):
    res = guarded_query(
        "SELECT web_name FROM dim_player WHERE season = ? AND position = ?",
        ("2026-27", 3), db=db,
    )
    assert [r["web_name"] for r in res.rows] == ["Early"]


def test_the_live_file_is_never_opened_writable(db, monkeypatch):
    """Reads must go through a copy: the bot holds write leases on the original.

    Asserted by refusing any writable open of the source path for the duration
    of the query -- if the implementation ever stops copying, this fails.
    """
    import duckdb

    real_connect = duckdb.connect
    opened: list[tuple[str, bool]] = []

    def spy(database=":memory:", read_only=False, **kw):
        opened.append((str(database), read_only))
        return real_connect(database, read_only=read_only, **kw)

    monkeypatch.setattr(duckdb, "connect", spy)
    guarded_query("SELECT 1", db=db)

    writable_on_source = [
        p for p, ro in opened if p == str(db) and not ro
    ]
    assert writable_on_source == []


def test_read_copy_cleans_up_after_itself(db):
    from pathlib import Path

    from fpl_edge.platform.query import read_copy

    with read_copy(db) as wh:
        copy_dir = Path(wh.path).parent
        assert copy_dir.exists()
    assert not copy_dir.exists()


def test_missing_warehouse_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        guarded_query("SELECT 1", db=tmp_path / "absent.duckdb")
