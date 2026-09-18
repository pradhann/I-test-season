"""The fixtures build job, pinned before the module is split.

``tests/unit/test_fixture_board_panel.py`` covers the two panels through
``run_script``. Nothing covered ``write_artefacts``, which is what
``fixture_ratings_refit`` calls, so a split of
``fpl_edge/platform/scripts/fixtures.py`` into ``fixtures/`` could change the
artefact names or the column tuples and no test would fail.

These tests pin current behaviour at 2026-09-17, not desired behaviour. One
current behaviour worth naming: ``write_artefacts`` writes two of the three
declared artefacts. ``DIFFICULTY_NAME`` is written by
``fpl_edge/models/team_goals/ratings_cache.py``, not here, and the test below
records that so the planned merge of ``ratings_cache`` into the build module
has a before-picture.

The warehouse is the committed synthetic league under
``fpl_edge/models/team_goals/``, seeded into ``tmp_path``. No network, no read
of ``data/warehouse/fpl.duckdb``.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.synthetic import build_warehouse, load_league
from fpl_edge.platform.scripts import fixtures as fxmod
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC

#: The synthetic league's current season, matching test_ratings_cache.py.
SEASON = "2025-26"

#: Mid-season: completed matches behind the snapshot, fixtures ahead of it.
AS_OF = dt.datetime(2025, 12, 4, 10, 0, tzinfo=UTC)

CALIB_SEASON = "2022-23"


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("fixtures_build") / "fpl.duckdb"
    wh = build_warehouse(load_league(FIXTURES_DIR), path)
    wh.close()
    return path


@pytest.fixture(scope="module")
def built(synthetic_db, tmp_path_factory):
    """One build, reused. ``out_dir`` is a tmp directory, never the warehouse
    directory of the repository."""
    out = tmp_path_factory.mktemp("fixtures_artefacts")
    report = fxmod.write_artefacts(
        synthetic_db, season=SEASON, out_dir=out, now=AS_OF, calibration=True)
    return out, report


# ---------------------------------------------------------------------------
# the artefact names
# ---------------------------------------------------------------------------


def test_the_three_artefact_names_are_the_filenames_the_panels_read():
    """The names are a contract with the panel read path and with
    ``ratings_cache``. A split that renames one silently detaches the reader
    from the writer."""
    assert fxmod.DIFFICULTY_NAME == "fixture_difficulty.parquet"
    assert fxmod.RATINGS_NAME == "fixture_ratings.parquet"
    assert fxmod.CALIBRATION_NAME == "fixture_calibration.parquet"


def test_write_artefacts_writes_the_ratings_and_calibration_files(built):
    out, _ = built
    assert (out / fxmod.RATINGS_NAME).exists()
    assert (out / fxmod.CALIBRATION_NAME).exists()


def test_write_artefacts_does_not_write_the_blended_difficulty_file(built):
    """Current behaviour at fixtures.py:2396-2428: the build writes 2 of the 3
    names. ``fixture_difficulty.parquet`` comes from
    ``fpl_edge/models/team_goals/ratings_cache.py:write_fixture_difficulty``,
    which the settlement chain calls as a separate step."""
    out, _ = built
    assert not (out / fxmod.DIFFICULTY_NAME).exists()


# ---------------------------------------------------------------------------
# the column tuples
# ---------------------------------------------------------------------------


def test_the_ratings_artefact_has_exactly_the_declared_columns_in_order(built):
    out, _ = built
    df = pd.read_parquet(out / fxmod.RATINGS_NAME)
    assert tuple(df.columns) == fxmod.RATINGS_COLUMNS
    assert fxmod.RATINGS_COLUMNS == (
        "season", "team_code", "attack", "defence", "is_promoted",
        "matches_seen", "intercept", "home_adv", "rho", "mean_attack",
        "mean_defence", "half_life_days", "n_matches", "effective_n",
        "converged", "fitted_at", "snapshot_as_of",
    )


def test_the_calibration_artefact_has_exactly_the_declared_columns_in_order(built):
    out, _ = built
    df = pd.read_parquet(out / fxmod.CALIBRATION_NAME)
    assert tuple(df.columns) == fxmod.CALIBRATION_COLUMNS
    assert fxmod.CALIBRATION_COLUMNS == (
        "position", "n_starts", "fixture_pts_6gw", "team_pts_6gw", "ratio",
        "seasons", "method", "computed_at",
    )


def test_the_ratings_artefact_carries_one_row_per_club_with_a_split(built):
    out, _ = built
    df = pd.read_parquet(out / fxmod.RATINGS_NAME)
    assert len(df) == 20
    assert df["team_code"].is_unique
    assert df["season"].eq(SEASON).all()
    # attack and defence are stored apart. The whole reason this artefact
    # exists beside fixture_difficulty.parquet is that the subtraction into
    # one scalar is irreversible.
    assert df["attack"].notna().all() and df["defence"].notna().all()
    assert not df["attack"].equals(df["defence"])
    # The fit's three globals are constant across the rows.
    for column in ("intercept", "home_adv", "rho"):
        assert df[column].nunique() == 1


def test_the_report_names_every_artefact_it_wrote(built):
    out, report = built
    assert set(report) == {
        "ratings_rows", "ratings_seconds", "ratings_path",
        "calibration_rows", "calibration_seconds", "calibration_path",
    }
    assert report["ratings_rows"] == 20
    assert report["ratings_path"] == str(out / fxmod.RATINGS_NAME)
    assert report["calibration_path"] == str(out / fxmod.CALIBRATION_NAME)


def test_skipping_calibration_leaves_the_calibration_keys_out(synthetic_db,
                                                             tmp_path):
    report = fxmod.write_artefacts(
        synthetic_db, season=SEASON, out_dir=tmp_path, now=AS_OF,
        calibration=False)
    assert set(report) == {"ratings_rows", "ratings_seconds", "ratings_path"}
    assert not (tmp_path / fxmod.CALIBRATION_NAME).exists()


def test_a_warehouse_with_no_upcoming_fixtures_yields_the_empty_column_frame(
        synthetic_db):
    """build_board_ratings:280-281 returns an empty frame rather than raising.
    The columns still have to be the declared ones, because the panel reads
    them by name."""
    late = dt.datetime(2099, 1, 1, tzinfo=UTC)
    with Warehouse.read_copy(synthetic_db) as wh:
        df = fxmod.build_board_ratings(wh, season=SEASON, now=late)
    assert df.empty
    assert tuple(df.columns) == fxmod.RATINGS_COLUMNS


# ---------------------------------------------------------------------------
# calibration with data behind it
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def calibration_db(tmp_path_factory):
    """Four clubs, six gameweeks, one starter per position per club.

    The synthetic league has no ``fact_player_fixture`` rows, so the shared
    warehouse exercises only the empty branch of ``build_calibration``. This
    one carries 96 qualifying starts so the regression and the rolling-window
    spread both run.
    """
    path = tmp_path_factory.mktemp("fixtures_calib") / "fpl.duckdb"
    wh = Warehouse(path)
    stamp = pd.Timestamp("2023-06-01", tz="UTC")
    clubs = {1: "AAA", 2: "BBB", 3: "CCC", 4: "DDD"}
    wh.append("dim_team", pd.DataFrame([
        {"season": CALIB_SEASON, "team_code": code, "team_id": code,
         "name": short, "short_name": short, "as_of": stamp}
        for code, short in clubs.items()
    ]))
    # gw -> the two pairings, rotated so every club meets every other.
    pairings = {
        1: [(1, 2), (3, 4)], 2: [(1, 3), (2, 4)], 3: [(1, 4), (2, 3)],
        4: [(2, 1), (4, 3)], 5: [(3, 1), (4, 2)], 6: [(4, 1), (3, 2)],
    }
    fixtures, fid = [], 0
    for gw, pairs in pairings.items():
        for home, away in pairs:
            fid += 1
            fixtures.append({
                "season": CALIB_SEASON, "fixture_id": fid, "gw": gw,
                "kickoff_utc": pd.Timestamp(f"2022-08-{gw:02d} 15:00",
                                            tz="UTC"),
                "home_team_code": home, "away_team_code": away,
                "finished": True, "home_score": 1, "away_score": 0,
                "as_of": stamp,
            })
    wh.append("fact_fixture", pd.DataFrame(fixtures))
    # One player per (club, position). Code 1000 * club + position.
    players = [{"season": CALIB_SEASON, "code": 1000 * club + pos,
                "element_id": 1000 * club + pos, "web_name": f"P{club}{pos}",
                "first_name": "F", "second_name": f"P{club}{pos}",
                "position": pos, "team_code": club, "as_of": stamp}
               for club in clubs for pos in (1, 2, 3, 4)]
    wh.append("dim_player", pd.DataFrame(players))
    rows = []
    for fx in fixtures:
        for club, was_home in ((fx["home_team_code"], True),
                               (fx["away_team_code"], False)):
            opp = (fx["away_team_code"] if was_home
                   else fx["home_team_code"])
            for pos in (1, 2, 3, 4):
                rows.append({
                    "season": CALIB_SEASON, "code": 1000 * club + pos,
                    "fixture_id": fx["fixture_id"], "gw": fx["gw"],
                    "minutes": 90, "starts": 1, "was_home": was_home,
                    # Points rise with the club and fall with the opponent, so
                    # the two-way fit has a signal it can separate.
                    "total_points": 2 + club - opp + (1 if was_home else 0),
                    "as_of": stamp,
                })
    wh.append("fact_player_fixture", pd.DataFrame(rows))
    wh.close()
    return path


def test_calibration_rows_carry_one_row_per_position_with_a_spread_ratio(
        calibration_db):
    """min_starts and min_clubs are parameters so a test can drive the
    arithmetic at a size a human can check: 24 starts per position, 4 clubs."""
    with Warehouse.read_copy(calibration_db) as wh:
        df = fxmod.build_calibration(wh, seasons=(CALIB_SEASON,),
                                     min_starts=20, min_clubs=4)
    assert tuple(df.columns) == fxmod.CALIBRATION_COLUMNS
    assert sorted(df["position"]) == ["DEF", "FWD", "GKP", "MID"]
    assert df["n_starts"].eq(24).all()
    assert df["seasons"].eq(CALIB_SEASON).all()
    assert df["method"].str.startswith("two-way additive least squares").all()
    assert df["fixture_pts_6gw"].notna().all()
    assert df["team_pts_6gw"].notna().all()


def test_a_position_below_the_start_floor_is_dropped_not_fitted_on_noise(
        calibration_db):
    with Warehouse.read_copy(calibration_db) as wh:
        df = fxmod.build_calibration(wh, seasons=(CALIB_SEASON,),
                                     min_starts=25, min_clubs=4)
    assert df.empty
    assert tuple(df.columns) == fxmod.CALIBRATION_COLUMNS


def test_a_window_with_too_few_clubs_produces_no_row(calibration_db):
    with Warehouse.read_copy(calibration_db) as wh:
        df = fxmod.build_calibration(wh, seasons=(CALIB_SEASON,),
                                     min_starts=20, min_clubs=5)
    assert df.empty
    assert tuple(df.columns) == fxmod.CALIBRATION_COLUMNS
