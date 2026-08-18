"""Feature-construction tests, including the ones that catch leakage.

The expensive property to protect is that a feature row for gameweek N contains
nothing that was not public at gameweek N's deadline. These tests assert it by
building the same target rows from two different instants and showing the
earlier one knows strictly less.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.minutes.dataset import FIXTURE_DIR, load_csv_warehouse
from fpl_edge.models.minutes.features import (
    COLD_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    SnapshotView,
    bucket_of_minutes,
    build_feature_frame,
)
from fpl_edge.store import Warehouse
from fpl_edge.types import MinutesBucket

UTC = dt.UTC
CATALOG_AT = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)
SEASON = "2025-26"


@pytest.fixture(scope="module")
def wh(tmp_path_factory) -> Warehouse:
    path = tmp_path_factory.mktemp("minutes") / "fixtures.duckdb"
    return load_csv_warehouse(FIXTURE_DIR, path)


@pytest.fixture(scope="module")
def deadlines(wh: Warehouse) -> pd.DataFrame:
    ev = wh.snapshot_at(CATALOG_AT).table("dim_event", where="season = ?", params=[SEASON])
    return ev.sort_values("gw").reset_index(drop=True)


def _at(deadlines: pd.DataFrame, gw: int) -> dt.datetime:
    row = deadlines[deadlines["gw"] == gw].iloc[0]
    return pd.Timestamp(row["deadline_utc"]).to_pydatetime()


# --------------------------------------------------------------------------
# bucket boundaries
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "minutes,expected",
    [
        (0, MinutesBucket.UNAVAILABLE),
        (None, MinutesBucket.UNAVAILABLE),
        (1, MinutesBucket.CAMEO),
        (59, MinutesBucket.CAMEO),
        (60, MinutesBucket.FULL),  # the FPL appearance-point boundary
        (90, MinutesBucket.FULL),
    ],
)
def test_bucket_boundaries(minutes, expected) -> None:
    assert bucket_of_minutes(minutes) is expected


# --------------------------------------------------------------------------
# leakage
# --------------------------------------------------------------------------


def test_features_only_see_gameweeks_already_played(wh, deadlines) -> None:
    """At GW10's deadline exactly nine gameweeks of evidence exist, not ten."""
    frame = build_feature_frame(wh.snapshot_at(_at(deadlines, 10)), SEASON, [10])
    assert not frame.empty
    assert set(frame["n_obs_season"]) == {9.0}


def test_the_same_gameweek_looks_different_from_the_future(wh, deadlines) -> None:
    """The as-of filter binds: reading later sees more of the season."""
    early = build_feature_frame(wh.snapshot_at(_at(deadlines, 10)), SEASON, [10])
    late = build_feature_frame(
        wh.snapshot_at(dt.datetime(2026, 6, 1, tzinfo=UTC)), SEASON, [10]
    )
    assert early["n_obs_season"].max() == 9
    # 22, not 21: from the future the "history" for GW10 includes GW10 itself.
    # That is the leak, sitting right there, and the as-of filter is what stops it.
    assert late["n_obs_season"].max() == 22
    key = ["code", "fixture_id"]
    merged = early.merge(late, on=key, suffixes=("_early", "_late"))
    assert len(merged) == len(early)
    assert (merged["n_obs_season_late"] > merged["n_obs_season_early"]).all()


def test_no_history_row_is_dated_after_the_target_kickoff(wh, deadlines) -> None:
    view = SnapshotView(wh.snapshot_at(_at(deadlines, 15)))
    grid = view.opportunity_grid()
    target = build_feature_frame(view, SEASON, [15])["target_ko"].min()
    assert grid[grid["season"] == SEASON]["kickoff_utc"].max() < target


# --------------------------------------------------------------------------
# the opportunity grid
# --------------------------------------------------------------------------


def test_a_player_with_no_result_row_counts_as_zero_minutes(tmp_path) -> None:
    """Not being in the squad is evidence, not a missing value.

    FPL's history endpoint is not guaranteed to carry a row for a player who was
    left out entirely, and a model that learns only from rows that exist would
    conclude every player always plays.
    """
    small = Warehouse(tmp_path / "small.duckdb")
    as_of = dt.datetime(2025, 8, 1, tzinfo=UTC)
    played = dt.datetime(2025, 8, 17, 9, tzinfo=UTC)
    small.append("dim_player", pd.DataFrame([
        {"season": "2025-26", "code": 1, "element_id": 1, "web_name": "Starter",
         "position": 3, "team_code": 500, "as_of": as_of},
        {"season": "2025-26", "code": 2, "element_id": 2, "web_name": "Reserve",
         "position": 3, "team_code": 500, "as_of": as_of},
    ]))
    small.append("fact_fixture", pd.DataFrame([
        {"season": "2025-26", "fixture_id": 1, "gw": 1,
         "kickoff_utc": dt.datetime(2025, 8, 16, 14, tzinfo=UTC),
         "home_team_code": 500, "away_team_code": 501, "finished": True,
         "home_score": 1, "away_score": 0, "as_of": played},
    ]))
    small.append("fact_player_fixture", pd.DataFrame([
        {"season": "2025-26", "code": 1, "fixture_id": 1, "gw": 1, "minutes": 90,
         "starts": 1, "total_points": 2, "was_home": True, "as_of": played},
    ]))

    grid = SnapshotView(small.snapshot_at(played + dt.timedelta(days=1))).opportunity_grid()
    assert len(grid) == 2
    reserve = grid[grid["code"] == 2].iloc[0]
    assert reserve["minutes"] == 0
    assert reserve["bucket"] == int(MinutesBucket.UNAVAILABLE)


# --------------------------------------------------------------------------
# shape and content
# --------------------------------------------------------------------------


def test_frame_has_every_declared_feature_and_one_row_per_player_fixture(wh, deadlines) -> None:
    frame = build_feature_frame(wh.snapshot_at(_at(deadlines, 7)), SEASON, [7])
    for col in FEATURE_COLUMNS:
        assert col in frame.columns, col
    assert set(COLD_FEATURE_COLUMNS) <= set(FEATURE_COLUMNS)
    assert not frame.duplicated(subset=["code", "fixture_id"]).any()
    assert frame["gw"].unique().tolist() == [7]
    # 12 clubs x 21 squad players, each club playing once
    assert len(frame) == 252


def test_availability_flags_are_read_from_the_snapshot(wh, deadlines) -> None:
    frame = build_feature_frame(wh.snapshot_at(_at(deadlines, 12)), SEASON, [12])
    assert frame["status_flagged"].sum() > 0
    flagged = frame[frame["status_flagged"] > 0]
    assert (flagged["news_len"] > 0).all()
    published = frame[frame["has_chance"] > 0]
    assert published["chance_next"].between(0, 100).all()
    assert frame.loc[frame["has_chance"] == 0, "chance_next"].isna().all()


def test_depth_rank_is_within_club_and_position(wh, deadlines) -> None:
    frame = build_feature_frame(wh.snapshot_at(_at(deadlines, 12)), SEASON, [12])
    for (_fx, _team, _pos), g in frame.groupby(["fixture_id", "team_code", "position"]):
        assert sorted(g["depth_rank"]) == list(np.arange(1, len(g) + 1))
    gk = frame[frame["position"] == 1]
    assert (gk["squad_size_pos"] == 3).all()


def test_congestion_features_track_the_published_schedule(wh, deadlines) -> None:
    midweek = build_feature_frame(wh.snapshot_at(_at(deadlines, 11)), SEASON, [11])
    weekend = build_feature_frame(wh.snapshot_at(_at(deadlines, 9)), SEASON, [9])
    assert (midweek["is_midweek"] == 1).all()
    assert (weekend["is_midweek"] == 0).all()
    assert midweek["days_rest"].max() < weekend["days_rest"].max()
