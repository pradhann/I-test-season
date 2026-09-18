"""Hand-dropped paid exports: the epoch rule, the PIT mapping, idempotency.

Everything here is synthetic. The real FPL Review export is paid data and is
gitignored, so no row of it is copied into this repo; the five players below
are invented and carry the export's SHAPE, which is what the mapping has to
survive.

The PIT fixture is the point of the file. Element id 7 belongs to code 300 in
the dim_player rows written before the export instant and to code 400 in the
rows written after it. An export taken between the two must resolve 7 to 300.
Resolving it against today's roster would move ten gameweeks of projections
onto a player the export was never about.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.projections import local_csv
from fpl_edge.ingest.projections.local_csv import LocalDropError
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

SEASON = "2026-27"

#: Roster written before the export.
EARLY = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
#: The export instant, and the epoch in the test filename.
EXPORT = dt.datetime(2026, 9, 18, 15, 16, 34, tzinfo=dt.timezone.utc)
EXPORT_EPOCH = int(EXPORT.timestamp())
#: Roster written after the export, where element 7 has been reassigned.
LATE = dt.datetime(2026, 9, 25, 9, 0, tzinfo=dt.timezone.utc)

#: Five invented players in the FPL Review export shape: a UTF-8 BOM, two
#: columns per gameweek named by the gameweek, and Elite% as a percent
#: string. Element 9 is deliberately absent from every dim_player row.
CSV = (
    "﻿Pos,ID,Name,BV,SV,Team,5_xMins,5_Pts,6_xMins,6_Pts,Elite%\n"
    "GKP,1,Ines Keeper,5.0,5.0,AAA,90,4.10,88,3.80,12.5%\n"
    "DEF,2,Omar Back,4.5,4.5,AAA,85,3.20,80,3.00,4.0%\n"
    "MID,7,Sim Middle,7.5,7.4,BBB,78,5.60,76,5.10,44.0%\n"
    "FWD,8,Nadia Front,9.0,9.0,BBB,90,6.30,90,6.55,61.0%\n"
    "MID,9,Ghost Reserve,4.0,4.0,CCC,0,0.00,0,0.00,0.0%\n"
)


def player(code: int, element_id: int, web_name: str,
           as_of: dt.datetime, position: int = 3) -> dict:
    return {"season": SEASON, "code": code, "element_id": element_id,
            "web_name": web_name, "first_name": web_name,
            "second_name": web_name, "position": position,
            "team_code": 1, "as_of": as_of}


@pytest.fixture
def wh(tmp_path):
    """A warehouse with a roster that reassigns element 7 after the export."""
    with Warehouse(tmp_path / "drops.duckdb") as warehouse:
        rows = [
            player(100, 1, "Keeper", EARLY, position=1),
            player(200, 2, "Back", EARLY, position=2),
            player(300, 7, "Middle", EARLY),
            player(500, 8, "Front", EARLY, position=4),
            # After the export: 7 is reassigned to a new player, and the old
            # holder of 7 moves to a free element id.
            player(300, 21, "Middle", LATE),
            player(400, 7, "Stranger", LATE),
        ]
        frame = pd.DataFrame(rows)
        frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
        warehouse.append("dim_player", frame)
        yield warehouse


@pytest.fixture
def store(wh):
    return ProjectionStore(wh)


@pytest.fixture
def drop_dir(tmp_path):
    """A drop root holding one synthetic export at the export epoch."""
    directory = tmp_path / "projections" / "fplreview"
    directory.mkdir(parents=True)
    (directory / f"fplreview_{EXPORT_EPOCH}.csv").write_text(CSV, encoding="utf-8")
    return tmp_path / "projections"


DROP = local_csv.BY_KEY["fplreview"]


# ---------------------------------------------------------------------------
# the filename rule
# ---------------------------------------------------------------------------


def test_the_epoch_in_the_filename_is_the_as_of():
    assert local_csv.epoch_from_name(DROP, f"fplreview_{EXPORT_EPOCH}.csv") == EXPORT


def test_a_filename_with_no_epoch_is_refused_and_says_why():
    with pytest.raises(LocalDropError) as exc:
        local_csv.epoch_from_name(DROP, "fplreview_latest.csv")
    message = str(exc.value)
    assert "no unix epoch in the filename" in message
    assert "fplreview_1789744594.csv" in message, "the refusal shows the rule"


def test_a_millisecond_epoch_is_refused_rather_than_stamped():
    with pytest.raises(LocalDropError, match="Seconds, not milliseconds"):
        local_csv.epoch_from_name(DROP, f"fplreview_{EXPORT_EPOCH}000.csv")


def test_a_refused_file_is_listed_rather_than_skipped_in_silence(drop_dir):
    (drop_dir / "fplreview" / "fplreview_latest.csv").write_text(CSV, encoding="utf-8")
    files, refused = local_csv.scan(DROP, drop_dir)
    assert [f.path.name for f in files] == [f"fplreview_{EXPORT_EPOCH}.csv"]
    assert [name for name, _ in refused] == ["fplreview_latest.csv"]
    assert "no unix epoch" in refused[0][1]


# ---------------------------------------------------------------------------
# the mapping
# ---------------------------------------------------------------------------


def test_one_wide_row_becomes_one_row_per_gameweek(drop_dir):
    frame = local_csv.read(drop_dir / "fplreview" / f"fplreview_{EXPORT_EPOCH}.csv")
    assert list(frame.columns)[:2] == ["Pos", "ID"], "the BOM is stripped"
    long = local_csv.fplreview_to_long(frame, "ID")
    assert set(long["gw"]) == {5, 6}
    assert len(long) == 10
    row = long[(long["key"] == 1) & (long["gw"] == 6)].iloc[0]
    assert row["xp"] == pytest.approx(3.80)
    assert row["xmins"] == pytest.approx(88)


def test_ingest_writes_long_rows_with_the_export_epoch(wh, store, drop_dir):
    got = local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    assert got.status == ""
    assert got.ingested == [f"fplreview_{EXPORT_EPOCH}.csv"]
    landed = wh.sql(
        "SELECT gw, code, xp, xmins, as_of FROM fact_projection "
        "WHERE provider = 'fplreview' ORDER BY gw, code"
    )
    # Four of the five ids resolve, across two gameweeks.
    assert len(landed) == 8
    assert got.rows == 8
    assert set(landed["gw"]) == {5, 6}
    assert set(landed["code"]) == {100, 200, 300, 500}
    assert set(landed["as_of"]) == {pd.Timestamp(EXPORT)}
    middle = landed[(landed["code"] == 300) & (landed["gw"] == 5)].iloc[0]
    assert middle["xp"] == pytest.approx(5.60)
    assert middle["xmins"] == pytest.approx(78)


def test_the_element_map_is_read_at_the_export_instant(wh, store, drop_dir):
    """Element 7 is code 300 at the export instant and code 400 today.

    Today's roster is in the warehouse and is the easy thing to read. Reading
    it would put Sim Middle's ten gameweeks onto a different player.
    """
    local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    codes = set(wh.sql(
        "SELECT DISTINCT code FROM fact_projection WHERE provider = 'fplreview'"
    )["code"])
    assert 300 in codes, "element 7 must resolve to the player it was at export"
    assert 400 not in codes, "the post-export reassignment must not be read"


def test_an_unmappable_id_is_dropped_and_counted_with_its_name(wh, store, drop_dir):
    got = local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    assert got.unresolved == 1
    assert got.unresolved_names == ["Ghost Reserve"]
    landed = wh.sql(
        "SELECT count(*) c FROM fact_projection WHERE provider = 'fplreview'"
    ).iloc[0]["c"]
    assert landed == 8, "the unmappable id is absent, not guessed onto a player"


def test_elite_share_lands_as_ownership_with_its_own_provider(wh, store, drop_dir):
    local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    own = wh.sql(
        "SELECT code, metric, value, gw FROM fact_external_ownership "
        "WHERE provider = 'fplreview' ORDER BY code"
    )
    assert set(own["metric"]) == {"own_elite"}
    assert set(own["gw"]) == {5}, "ownership describes the next gameweek only"
    assert own[own["code"] == 500].iloc[0]["value"] == pytest.approx(0.61)


# ---------------------------------------------------------------------------
# idempotency and the drop ledger
# ---------------------------------------------------------------------------


def test_the_same_file_ingested_twice_writes_nothing_new(wh, store, drop_dir):
    first = local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    second = local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    assert first.rows == 8
    assert second.rows == 0
    assert second.ingested == []
    assert second.skipped == [f"fplreview_{EXPORT_EPOCH}.csv"]
    total = wh.sql(
        "SELECT count(*) c FROM fact_projection WHERE provider = 'fplreview'"
    ).iloc[0]["c"]
    assert total == 8


def test_the_ledger_is_one_raw_fetch_row_per_file(wh, store, drop_dir):
    local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    held = local_csv.ledger(wh, DROP)
    assert list(held) == [f"fplreview_{EXPORT_EPOCH}.csv"]
    assert len(held[f"fplreview_{EXPORT_EPOCH}.csv"]) == 64
    row = wh.sql(
        "SELECT source, endpoint, fetched_at FROM raw_fetch "
        "WHERE source = 'projections_fplreview'"
    ).iloc[0]
    assert row["fetched_at"] == pd.Timestamp(EXPORT)


def test_a_drop_edited_under_its_old_name_is_refused(wh, store, drop_dir):
    local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    path = drop_dir / "fplreview" / f"fplreview_{EXPORT_EPOCH}.csv"
    path.write_text(CSV.replace("4.10", "9.99"), encoding="utf-8")
    again = local_csv.ingest_drop(wh, store, DROP, season=SEASON, root=drop_dir)
    assert again.rows == 0
    assert [name for name, _ in again.refused] == [f"fplreview_{EXPORT_EPOCH}.csv"]
    assert "Re-export under a new epoch" in again.refused[0][1]


# ---------------------------------------------------------------------------
# the absent source
# ---------------------------------------------------------------------------


def test_an_empty_directory_is_no_source_with_a_reason(wh, store, tmp_path):
    (tmp_path / "projections" / "fplreview").mkdir(parents=True)
    got = local_csv.ingest_drop(wh, store, DROP, season=SEASON,
                                root=tmp_path / "projections")
    assert got.status == "no_source"
    assert "holds no export yet" in got.reason
    assert got.rows == 0


def test_a_missing_directory_is_no_source_with_a_reason(wh, store, tmp_path):
    got = local_csv.ingest_drop(wh, store, DROP, season=SEASON,
                                root=tmp_path / "nothing-here")
    assert got.status == "no_source"
    assert "does not exist" in got.reason


def test_a_directory_of_unusable_names_is_refused_not_ok(wh, store, tmp_path):
    directory = tmp_path / "projections" / "fplreview"
    directory.mkdir(parents=True)
    (directory / "export.csv").write_text(CSV, encoding="utf-8")
    got = local_csv.ingest_drop(wh, store, DROP, season=SEASON,
                                root=tmp_path / "projections")
    assert got.status == "refused"
    assert "no usable epoch" in got.reason


# ---------------------------------------------------------------------------
# the run: ledger row, and the read surface
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, db, drop_dir):
    from fpl_edge.ingest.projections import cli

    for name in ("_ingest_fplform", "_ingest_livefpl", "_ingest_fpl_ep",
                 "_ingest_rotowire", "_ingest_premierinjuries"):
        monkeypatch.setattr(cli, name, _skipped(name))
    monkeypatch.setattr(cli.github_csv, "FEEDS", ())
    monkeypatch.setattr(cli.local_csv, "DROP_ROOT", drop_dir)
    return cli.ingest(SEASON, db=str(db))


def _skipped(name: str):
    from fpl_edge.ingest.projections import cli

    def step(warehouse, store, season, *, first_gw, last_gw):
        return cli.StepResult(provider=name, ok=True, rows=0, parsed=0,
                              unresolved=0, detail="stub")
    return step


def test_the_run_records_one_fetch_run_row_with_its_trigger_and_files(
        monkeypatch, tmp_path, drop_dir):
    # The CLI opens its own warehouse, so the roster has to be on disk first.
    monkeypatch.delenv("FPL_EDGE_RUN_TRIGGER", raising=False)
    path = tmp_path / "cli.duckdb"
    with Warehouse(path) as fresh:
        rows = pd.DataFrame([
            player(100, 1, "Keeper", EARLY, position=1),
            player(200, 2, "Back", EARLY, position=2),
            player(300, 7, "Middle", EARLY),
            player(500, 8, "Front", EARLY, position=4),
        ])
        rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
        fresh.append("dim_player", rows)
    results = _run_cli(monkeypatch, path, drop_dir)
    assert results["fplreview"].ok
    assert results["fplreview"].rows == 8
    with Warehouse(path) as after:
        run = after.sql(
            "SELECT status, rows_written, note, \"trigger\" FROM fetch_run "
            "WHERE pipeline = 'ingest_projections' AND source = 'fplreview'"
        )
        served = after.sql(
            "SELECT source, max(fetched_at) AS last_fetched "
            "FROM sem_projections(?) WHERE season = ?"
            " GROUP BY source",
            [dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc), SEASON],
        )
    assert len(run) == 1
    assert run.iloc[0]["status"] == "ok"
    assert run.iloc[0]["rows_written"] == 8
    assert run.iloc[0]["trigger"] == "cli"
    assert f"fplreview_{EXPORT_EPOCH}.csv" in run.iloc[0]["note"]
    assert "fplreview" in set(served["source"]), \
        "a hand drop must reach the same read surface as every other provider"
    # The freshness the Projections panel prints per source is this column,
    # so a hand drop ages on the file's epoch and on nothing else.
    assert served[served["source"] == "fplreview"].iloc[0]["last_fetched"] \
        == pd.Timestamp(EXPORT)


def test_an_absent_drop_directory_is_a_no_source_ledger_row(
        monkeypatch, tmp_path):
    monkeypatch.delenv("FPL_EDGE_RUN_TRIGGER", raising=False)
    path = tmp_path / "empty.duckdb"
    with Warehouse(path) as fresh:
        rows = pd.DataFrame([player(100, 1, "Keeper", EARLY, position=1)])
        rows["as_of"] = pd.to_datetime(rows["as_of"], utc=True)
        fresh.append("dim_player", rows)
    results = _run_cli(monkeypatch, path, tmp_path / "no-drops")
    assert results["fplreview"].ok, "an absent hand drop is not a failed run"
    with Warehouse(path) as after:
        run = after.sql(
            "SELECT status, note FROM fetch_run WHERE source = 'fplreview'"
        ).iloc[0]
    assert run["status"] == "no_source"
    assert "does not exist" in run["note"]
