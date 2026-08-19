"""Point-in-time correctness tests.

These are the tests that decide whether the backtest is worth anything. Each one
encodes a specific way an FPL backtest leaks the future.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.store import ConflictingFactError, LeakageError, Warehouse

UTC = dt.timezone.utc


def T(day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 8, day, hour, tzinfo=UTC)


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    return Warehouse(tmp_path / "t.duckdb")


def _state(code: int, price: int, own: float, as_of: dt.datetime, status: str = "a") -> pd.DataFrame:
    return pd.DataFrame(
        [{
            "season": "2026-27", "code": code, "element_id": code, "price_tenths": price,
            "selected_by_pct": own, "status": status, "chance_of_playing_next_round": None,
            "news": "", "news_added": None, "transfers_in_event": 0,
            "transfers_out_event": 0, "cost_change_start": 0, "as_of": as_of,
        }]
    )


def test_price_rise_after_deadline_is_invisible(wh: Warehouse) -> None:
    """The classic leak: optimising a squad at a price it only reached later."""
    wh.append("fact_player_state", _state(1, 75, 10.0, T(18)))
    wh.append("fact_player_state", _state(1, 76, 14.0, T(20)))

    at_deadline = wh.snapshot_at(T(19)).table("fact_player_state")
    assert at_deadline.iloc[0]["price_tenths"] == 75
    assert at_deadline.iloc[0]["selected_by_pct"] == 10.0

    later = wh.snapshot_at(T(21)).table("fact_player_state")
    assert later.iloc[0]["price_tenths"] == 76


def test_injury_news_published_after_deadline_is_invisible(wh: Warehouse) -> None:
    """Knowing about a Friday-night injury when picking on Friday morning."""
    wh.append("fact_player_state", _state(2, 100, 30.0, T(19, 8), status="a"))
    wh.append("fact_player_state", _state(2, 100, 30.0, T(19, 18), status="i"))

    before = wh.snapshot_at(T(19, 10)).table("fact_player_state")
    assert before.iloc[0]["status"] == "a"
    after = wh.snapshot_at(T(19, 20)).table("fact_player_state")
    assert after.iloc[0]["status"] == "i"


def test_snapshot_returns_exactly_one_row_per_entity(wh: Warehouse) -> None:
    for hour in (1, 5, 9):
        wh.append("fact_player_state", _state(3, 70 + hour, 5.0, T(18, hour)))
    snap = wh.snapshot_at(T(18, 23)).table("fact_player_state")
    assert len(snap) == 1
    assert snap.iloc[0]["price_tenths"] == 79  # the latest, not the first


def test_results_are_invisible_until_finalisation(wh: Warehouse) -> None:
    """A gameweek that has kicked off but not finalised must not be readable."""
    # results_before() drops codes with no dim_player row, so the player has to
    # actually exist for this to test what it claims to test.
    wh.append("dim_player", pd.DataFrame([{
        "season": "2026-27", "code": 4, "element_id": 4, "web_name": "P",
        "first_name": "A", "second_name": "B", "position": 2, "team_code": 1,
        "as_of": T(1),
    }]))
    row = {
        "season": "2026-27", "code": 4, "fixture_id": 1, "gw": 1, "minutes": 90,
        "goals_scored": 1, "assists": 0, "clean_sheets": 1, "goals_conceded": 0,
        "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
        "red_cards": 0, "saves": 0, "bonus": 3, "bps": 40, "starts": 1, "tackles": 2,
        "clearances_blocks_interceptions": 3, "recoveries": 5,
        "defensive_contribution": 0, "expected_goals": 0.4, "expected_assists": 0.1,
        "expected_goals_conceded": 0.8, "total_points": 13, "was_home": True,
        "as_of": T(23),  # finalised 09:00 the day after the last match
    }
    wh.append("fact_player_fixture", pd.DataFrame([row]))
    assert wh.snapshot_at(T(21)).results_before("2026-27").empty
    assert len(wh.snapshot_at(T(24)).results_before("2026-27")) == 1


def test_naive_datetime_is_rejected(wh: Warehouse) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        wh.snapshot_at(dt.datetime(2026, 8, 19, 12))


def test_rows_without_as_of_are_rejected(wh: Warehouse) -> None:
    bad = _state(5, 50, 1.0, T(18)).drop(columns=["as_of"])
    with pytest.raises(ValueError, match="as_of"):
        wh.append("fact_player_state", bad)


def test_append_is_idempotent(wh: Warehouse) -> None:
    df = _state(6, 55, 2.0, T(18))
    assert wh.append("fact_player_state", df) == 1
    assert wh.append("fact_player_state", df) == 0


def test_next_gw_respects_as_of(wh: Warehouse) -> None:
    events = pd.DataFrame([
        {"season": "2026-27", "gw": 1, "deadline_utc": T(21, 17), "is_finished": False,
         "as_of": T(1)},
        {"season": "2026-27", "gw": 2, "deadline_utc": T(28, 17), "is_finished": False,
         "as_of": T(1)},
    ])
    wh.append("dim_event", events)
    assert wh.snapshot_at(T(19)).next_gw("2026-27") == 1
    assert wh.snapshot_at(T(22)).next_gw("2026-27") == 2


def test_unknown_table_is_refused(wh: Warehouse) -> None:
    with pytest.raises(KeyError):
        wh.snapshot_at(T(19)).table("raw_fetch")


def test_timestamps_read_back_as_utc_regardless_of_host_timezone(wh: Warehouse) -> None:
    """DuckDB renders TIMESTAMPTZ in the session zone; we pin it to UTC.

    Without the pin, a machine set to US/Pacific reads the GW1 deadline back as
    10:30 local rather than 17:30Z. The instant is identical, but every
    comparison, format and golden test then depends on the host's locale.
    """
    events = pd.DataFrame([{
        "season": "2026-27", "gw": 1,
        "deadline_utc": dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC),
        "is_finished": False, "as_of": T(1),
    }])
    wh.append("dim_event", events)
    got = wh.snapshot_at(T(19)).deadline("2026-27", 1)
    assert got.utcoffset() == dt.timedelta(0)
    assert got == dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
    assert got.hour == 17


def test_contradicting_row_at_the_same_as_of_is_refused(wh: Warehouse) -> None:
    """A correction must not silently vanish behind an identical key.

    Appending is idempotent for identical rows, but two different prices
    claiming the same player at the same instant is a contradiction. Keeping
    whichever arrived first would lose a corrected bonus award or a re-scraped
    price without trace.
    """
    wh.append("fact_player_state", _state(7, 75, 10.0, T(18)))
    with pytest.raises(ConflictingFactError, match="contradict"):
        wh.append("fact_player_state", _state(7, 76, 10.0, T(18)))

    # The documented resolution: give the correction a later as_of.
    assert wh.append("fact_player_state", _state(7, 76, 10.0, T(18, 13))) == 1
    assert wh.snapshot_at(T(19)).table("fact_player_state").iloc[0]["price_tenths"] == 76


def test_identical_rows_repeated_within_one_batch_are_idempotent(wh: Warehouse) -> None:
    df = pd.concat([_state(8, 50, 1.0, T(18))] * 3, ignore_index=True)
    assert wh.append("fact_player_state", df) == 1


def test_naive_as_of_is_rejected_not_silently_localised(wh: Warehouse) -> None:
    """pd.to_datetime(..., utc=True) localises naive input without complaint.

    That made the old guard unreachable, so a naive local-time timestamp was
    reinterpreted as UTC and every fact shifted by the host's offset.
    """
    df = _state(9, 50, 1.0, T(18))
    df["as_of"] = [dt.datetime(2026, 8, 18, 12)]  # naive
    with pytest.raises(ValueError, match="timezone-aware"):
        wh.append("fact_player_state", df)


def test_non_utc_offsets_are_converted_not_rejected(wh: Warehouse) -> None:
    """Aware-but-not-UTC is unambiguous, so convert it rather than refuse it."""
    df = _state(10, 50, 1.0, T(18))
    df["as_of"] = [dt.datetime(2026, 8, 18, 13, tzinfo=dt.timezone(dt.timedelta(hours=1)))]
    assert wh.append("fact_player_state", df) == 1
    got = wh.snapshot_at(T(19)).table("fact_player_state")
    assert got.iloc[0]["as_of"].to_pydatetime() == dt.datetime(2026, 8, 18, 12, tzinfo=UTC)


def test_snapshot_row_order_is_deterministic(wh: Warehouse) -> None:
    """DuckDB parallelises scans; without ORDER BY the order varies by thread
    count, making seeded model runs irreproducible for no visible reason."""
    for code in (5, 1, 3, 2, 4):
        wh.append("fact_player_state", _state(code, 50 + code, 1.0, T(18)))
    first = wh.snapshot_at(T(19)).table("fact_player_state")["code"].tolist()
    assert first == sorted(first)
    for _ in range(3):
        assert wh.snapshot_at(T(19)).table("fact_player_state")["code"].tolist() == first


def test_raw_warehouse_is_not_reachable_from_a_snapshot(wh: Warehouse) -> None:
    """snapshot.warehouse.sql(...) read the entire future in one line and looked
    like ordinary code in review. The handle is now private."""
    snap = wh.snapshot_at(T(19))
    with pytest.raises(LeakageError, match="bypasses point-in-time"):
        _ = snap.warehouse


def test_escape_hatch_demands_a_substantive_reason(wh: Warehouse) -> None:
    snap = wh.snapshot_at(T(19))
    with pytest.raises(ValueError, match="substantive reason"):
        snap.escape_hatch_unfiltered("because")
    assert snap.escape_hatch_unfiltered(
        "schema introspection for the leakage audit, not model input"
    ) is wh


def test_snapshot_constructed_directly_still_validates_its_as_of(wh: Warehouse) -> None:
    """snapshot_at() validated, but the constructor was an unguarded back door."""
    from fpl_edge.store.warehouse import Snapshot

    with pytest.raises(ValueError, match="timezone-aware"):
        Snapshot(wh, dt.datetime(2026, 8, 19, 12))


def test_result_observable_before_its_own_kickoff_is_refused(wh: Warehouse) -> None:
    """as_of set to the deadline rather than to finalisation is exactly how a
    backtest ends up reading the future."""
    wh.append("fact_fixture", pd.DataFrame([{
        "season": "2026-27", "fixture_id": 77, "gw": 1,
        "kickoff_utc": T(22, 14), "home_team_code": 1, "away_team_code": 2,
        "finished": False, "home_score": None, "away_score": None, "as_of": T(1),
    }]))
    row = {
        "season": "2026-27", "code": 11, "fixture_id": 77, "gw": 1, "minutes": 90,
        "goals_scored": 1, "assists": 0, "clean_sheets": 0, "goals_conceded": 1,
        "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
        "red_cards": 0, "saves": 0, "bonus": 0, "bps": 20, "starts": 1, "tackles": 0,
        "clearances_blocks_interceptions": 0, "recoveries": 0,
        "defensive_contribution": 0, "expected_goals": 0.3, "expected_assists": 0.0,
        "expected_goals_conceded": 1.0, "total_points": 6, "was_home": True,
        "as_of": T(20),  # two days BEFORE kickoff
    }
    with pytest.raises(ValueError, match="before their own kickoff"):
        wh.append("fact_player_fixture", pd.DataFrame([row]))

    ok = {**row, "as_of": T(23)}
    assert wh.append("fact_player_fixture", pd.DataFrame([ok])) == 1


def test_orphan_codes_cannot_reach_results(wh: Warehouse) -> None:
    """A refused manager element can still leave per-fixture rows behind, and
    those would enter a training set as an unlabelled player."""
    wh.append("fact_fixture", pd.DataFrame([{
        "season": "2026-27", "fixture_id": 88, "gw": 1, "kickoff_utc": T(20, 14),
        "home_team_code": 1, "away_team_code": 2, "finished": True,
        # A finished fixture is only observable after it has been played.
        "home_score": 1, "away_score": 0, "as_of": T(20, 16),
    }]))
    wh.append("dim_player", pd.DataFrame([{
        "season": "2026-27", "code": 100, "element_id": 1, "web_name": "Real",
        "first_name": "R", "second_name": "P", "position": 3, "team_code": 1,
        "as_of": T(1),
    }]))
    base = {
        "season": "2026-27", "fixture_id": 88, "gw": 1, "minutes": 90,
        "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 0,
        "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
        "red_cards": 0, "saves": 0, "bonus": 0, "bps": 10, "starts": 1, "tackles": 0,
        "clearances_blocks_interceptions": 0, "recoveries": 0,
        "defensive_contribution": 0, "expected_goals": 0.0, "expected_assists": 0.0,
        "expected_goals_conceded": 0.0, "total_points": 2, "was_home": True,
        "as_of": T(21),
    }
    wh.append("fact_player_fixture", pd.DataFrame([
        {**base, "code": 100}, {**base, "code": 999},  # 999 has no dim_player row
    ]))
    got = wh.snapshot_at(T(25)).results_before("2026-27")
    assert set(got["code"]) == {100}


def test_concurrent_writer_raises_a_useful_error_not_a_raw_ioexception(
    tmp_path, monkeypatch
) -> None:
    """DuckDB permits one writer per file, across processes.

    A scheduled weekly run colliding with an ad-hoc ingest should wait briefly
    and then say what happened, not die with a raw IOException. The lock is
    cross-process, so it cannot be reproduced with two in-process connections;
    we drive the handler directly instead.
    """
    import duckdb as _duckdb

    from fpl_edge.store import WarehouseLockedError

    calls = {"n": 0}

    def always_locked(*args, **kwargs):
        calls["n"] += 1
        raise _duckdb.IOException(
            'IO Error: Could not set lock on file "x": Conflicting lock is held'
        )

    monkeypatch.setattr(_duckdb, "connect", always_locked)
    with pytest.raises(WarehouseLockedError, match="one writer"):
        Warehouse(tmp_path / "locked.duckdb", lock_timeout_s=0.6)
    assert calls["n"] > 1, "should have retried rather than failing on first attempt"


def test_non_lock_io_errors_are_not_swallowed(tmp_path, monkeypatch) -> None:
    import duckdb as _duckdb

    def disk_full(*args, **kwargs):
        raise _duckdb.IOException("IO Error: disk is full")

    monkeypatch.setattr(_duckdb, "connect", disk_full)
    with pytest.raises(_duckdb.IOException, match="disk is full"):
        Warehouse(tmp_path / "x.duckdb", lock_timeout_s=5.0)


def test_players_works_against_a_database_missing_newer_columns(tmp_path) -> None:
    """A read-only consumer cannot run migrations.

    Hard-coding a newer column list in players() breaks every reader against an
    older file. That happened for real when can_select was introduced while a
    long-running writer held the single-writer lock, so no migration could run.
    """
    import duckdb

    path = tmp_path / "old.duckdb"
    # Build the pre-migration schema directly; DuckDB refuses to DROP a column
    # that an index depends on, so we never add the newer ones.
    con = duckdb.connect(str(path))
    con.execute("SET TimeZone='UTC'")
    con.execute("""
        CREATE TABLE dim_player (
            season VARCHAR, code INTEGER, element_id INTEGER, web_name VARCHAR,
            first_name VARCHAR, second_name VARCHAR, position INTEGER,
            team_code INTEGER, as_of TIMESTAMPTZ
        )""")
    con.execute("""
        CREATE TABLE fact_player_state (
            season VARCHAR, code INTEGER, element_id INTEGER, price_tenths INTEGER,
            selected_by_pct DOUBLE, status VARCHAR,
            chance_of_playing_next_round INTEGER, news VARCHAR,
            news_added TIMESTAMPTZ, transfers_in_event BIGINT,
            transfers_out_event BIGINT, cost_change_start INTEGER, as_of TIMESTAMPTZ
        )""")
    con.execute(
        "INSERT INTO dim_player VALUES ('2026-27',1,1,'A','A','B',3,1,?)", [T(1)]
    )
    con.execute(
        "INSERT INTO fact_player_state VALUES "
        "('2026-27',1,1,50,5.0,'a',NULL,'',NULL,0,0,0,?)", [T(1)]
    )
    con.close()

    with Warehouse(path, read_only=True) as wh:
        got = wh.snapshot_at(T(19)).players("2026-27")
        assert len(got) == 1
        assert "can_select" not in got.columns
        # selectable() must still work, falling back to status.
        assert len(wh.snapshot_at(T(19)).selectable("2026-27")) == 1


def test_leased_warehouse_frees_the_lock_between_uses(tmp_path) -> None:
    """A long-lived bot must not starve every other writer for months.

    The lease connects on demand and releases on request; while released,
    another writer can take the file.
    """
    from fpl_edge.store import LeasedWarehouse

    path = tmp_path / "l.duckdb"
    lease = LeasedWarehouse(path)
    lease.append("fact_player_state", _state(1, 50, 1.0, T(18)))  # opens on demand
    lease.release()

    other = Warehouse(path, lock_timeout_s=2.0)  # must not time out
    assert other.append("fact_player_state", _state(2, 60, 2.0, T(18))) == 1
    other.close()

    # The lease reopens transparently after a release.
    assert len(lease.snapshot_at(T(19)).table("fact_player_state")) == 2
    lease.close()
