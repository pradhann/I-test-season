"""Historical ingest: as_of semantics, manager stripping and leakage.

Everything runs offline from ``tests/fixtures/vaastav`` -- real slices of
vaastav's archive with real timestamps, real column drift between seasons, and
the real identity hazards (two Ben Davieses, two Palmers, manager elements).
Regenerate them with::

    uv run python scripts/ingest_history.py --build-fixtures

The load itself is exercised end-to-end, because the thing worth testing is not
that a function returns a frame but that what lands in the warehouse cannot be
read before it was knowable.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.player_mapping import PlayerCodeIndex
from fpl_edge.ingest.vaastav import (
    FILE_MERGED_GW,
    FILE_PLAYERS_RAW,
    MissingSourceError,
    VaastavRepo,
    build_calendar,
    ingest_history,
    season_epoch,
)
from fpl_edge.store import Warehouse
from fpl_edge.types import Position

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "vaastav"
SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")
UTC = dt.timezone.utc

RICE = 204480
KUDUS = 460842
RAYA = 154561
ARTETA = 100051017
EMERY = 100037568

ARSENAL, TOTTENHAM, WEST_HAM, BRENTFORD = 3, 6, 21, 94


@pytest.fixture(scope="module")
def repo() -> VaastavRepo:
    return VaastavRepo(FIXTURE_ROOT, offline=True)


@pytest.fixture(scope="module")
def loaded(tmp_path_factory, repo: VaastavRepo) -> tuple[Warehouse, list, PlayerCodeIndex]:
    db = tmp_path_factory.mktemp("wh") / "hist.duckdb"
    wh = Warehouse(db)
    reports, index = ingest_history(wh, repo, SEASONS)
    return wh, reports, index


@pytest.fixture(scope="module")
def wh(loaded) -> Warehouse:
    return loaded[0]


# ---------------------------------------------------------------------------
# as_of derivation
# ---------------------------------------------------------------------------


def test_deadline_is_ninety_minutes_before_the_first_kickoff(repo: VaastavRepo) -> None:
    """Derived, because vaastav ships no deadlines. Checked against known values."""
    calendar = build_calendar(repo.read_csv("2025-26", "fixtures.csv"))
    assert calendar[1].first_kickoff_utc == dt.datetime(2025, 8, 15, 19, 0, tzinfo=UTC)
    assert calendar[1].deadline_utc == dt.datetime(2025, 8, 15, 17, 30, tzinfo=UTC)

    calendar_22 = build_calendar(repo.read_csv("2022-23", "fixtures.csv"))
    assert calendar_22[1].deadline_utc == dt.datetime(2022, 8, 5, 17, 30, tzinfo=UTC)


def test_points_final_is_0900_uk_the_day_after_the_last_match(repo: VaastavRepo) -> None:
    """Not kickoff, not the commit date. 2025-26 GW1 ended Mon 18 Aug 19:00Z."""
    calendar = build_calendar(repo.read_csv("2025-26", "fixtures.csv"))
    assert calendar[1].last_kickoff_utc == dt.datetime(2025, 8, 18, 19, 0, tzinfo=UTC)
    # 09:00 BST == 08:00Z.
    assert calendar[1].points_final_utc == dt.datetime(2025, 8, 19, 8, 0, tzinfo=UTC)


def test_points_final_respects_the_bst_gmt_boundary() -> None:
    """09:00 UK is 08:00Z in summer and 09:00Z in winter. A fixed offset is a bug."""
    fixtures = pd.DataFrame([
        {"id": 1, "event": 1, "kickoff_time": "2025-08-16T14:00:00Z",
         "team_h": 1, "team_a": 2, "finished": True},
        {"id": 2, "event": 2, "kickoff_time": "2025-12-20T15:00:00Z",
         "team_h": 1, "team_a": 2, "finished": True},
        # Kicks off 20:00 UK on the Sunday the clocks go back.
        {"id": 3, "event": 3, "kickoff_time": "2025-10-26T20:00:00Z",
         "team_h": 1, "team_a": 2, "finished": True},
    ])
    cal = build_calendar(fixtures)
    assert cal[1].points_final_utc == dt.datetime(2025, 8, 17, 8, 0, tzinfo=UTC)   # BST
    assert cal[2].points_final_utc == dt.datetime(2025, 12, 21, 9, 0, tzinfo=UTC)  # GMT
    assert cal[3].points_final_utc == dt.datetime(2025, 10, 27, 9, 0, tzinfo=UTC)  # GMT


def test_late_night_match_finalises_the_next_calendar_day() -> None:
    """A 20:00 UK Monday kickoff is final 09:00 UK Tuesday, not Monday."""
    fixtures = pd.DataFrame([
        {"id": 1, "event": 1, "kickoff_time": "2025-02-03T20:00:00Z",
         "team_h": 1, "team_a": 2, "finished": True},
    ])
    cal = build_calendar(fixtures)
    assert cal[1].points_final_utc == dt.datetime(2025, 2, 4, 9, 0, tzinfo=UTC)


def test_undated_fixtures_do_not_define_a_gameweek() -> None:
    fixtures = pd.DataFrame([
        {"id": 1, "event": None, "kickoff_time": None, "team_h": 1, "team_a": 2,
         "finished": False},
        {"id": 2, "event": 1, "kickoff_time": "2025-08-16T14:00:00Z", "team_h": 3,
         "team_a": 4, "finished": True},
    ])
    cal = build_calendar(fixtures)
    assert set(cal) == {1}
    assert season_epoch(cal) == dt.datetime(2025, 8, 16, 12, 30, tzinfo=UTC)


# ---------------------------------------------------------------------------
# what lands in the warehouse
# ---------------------------------------------------------------------------


def test_every_target_table_is_populated_for_every_season(loaded) -> None:
    _, reports, _ = loaded
    assert [r.season for r in reports] == list(SEASONS)
    for report in reports:
        for table in ("dim_team", "dim_player", "fact_fixture", "fact_player_fixture"):
            assert report.rows[table] > 0, f"{report.season}: {table} empty"
        assert report.dropped_unmatched_rows == 0
        assert report.dropped_no_fixture == 0
        assert report.match_rate == 1.0


def test_every_fact_row_carries_a_tz_aware_as_of(wh: Warehouse) -> None:
    for table in ("dim_team", "dim_player", "fact_fixture", "fact_player_fixture"):
        n_null = wh.sql(f"SELECT count(*) c FROM {table} WHERE as_of IS NULL").iloc[0]["c"]
        assert n_null == 0, f"{table} has rows with no as_of"


def test_reingest_is_idempotent(repo: VaastavRepo, tmp_path) -> None:
    """Raw archives must be replayable without duplicating rows."""
    with Warehouse(tmp_path / "t.duckdb") as w:
        first, _ = ingest_history(w, repo, ("2025-26",))
        second, _ = ingest_history(w, repo, ("2025-26",))
    assert sum(first[0].rows.values()) > 0
    assert set(second[0].rows.values()) == {0}


def test_offline_repo_refuses_to_invent_a_missing_file(tmp_path) -> None:
    bare = VaastavRepo(tmp_path, offline=True)
    with pytest.raises(MissingSourceError, match="offline"):
        bare.read_csv("2025-26", FILE_PLAYERS_RAW)


# ---------------------------------------------------------------------------
# manager elements
# ---------------------------------------------------------------------------


def test_position_from_api_still_rejects_element_type_five() -> None:
    """The contract this ingest relies on. If it ever coerces, managers leak in."""
    with pytest.raises(ValueError, match="Manager"):
        Position.from_api(5)


def test_manager_rows_never_reach_the_warehouse(wh: Warehouse, loaded) -> None:
    _, reports, index = loaded

    # The fixture really does contain them, so this is not vacuous.
    managers = index.manager_elements("2024-25")
    assert managers, "fixture no longer contains manager elements"
    assert sum(r.dropped_manager_rows for r in reports) > 0

    positions = wh.sql("SELECT DISTINCT position FROM dim_player")["position"].tolist()
    assert set(positions) <= {int(p) for p in Position}
    assert 5 not in positions

    for table in ("dim_player", "fact_player_fixture"):
        hit = wh.sql(
            f"SELECT count(*) c FROM {table} WHERE code IN (?, ?)", [ARTETA, EMERY]
        ).iloc[0]["c"]
        assert hit == 0, f"manager codes reached {table}"

    # And no element id belonging to a manager was mapped onto some other player.
    leaked = wh.sql(
        "SELECT count(*) c FROM dim_player WHERE season = '2024-25' AND element_id IN "
        f"({','.join(str(int(m)) for m in sorted(managers))})"
    ).iloc[0]["c"]
    assert leaked == 0


# ---------------------------------------------------------------------------
# cross-season identity, as stored
# ---------------------------------------------------------------------------


def test_club_mover_has_one_code_and_two_clubs_in_the_warehouse(wh: Warehouse) -> None:
    rows = wh.sql(
        "SELECT season, element_id, team_code FROM dim_player WHERE code = ? "
        "ORDER BY as_of", [RICE]
    )
    assert rows["season"].tolist() == list(SEASONS)
    assert rows["element_id"].nunique() == 4          # reassigned every season
    assert rows.iloc[0]["team_code"] == WEST_HAM
    assert set(rows["team_code"].tolist()[1:]) == {ARSENAL}


def test_mid_season_transfer_becomes_visible_only_at_that_deadline(wh: Warehouse) -> None:
    """Raya joined Arsenal after 2023-24 GW1. Nobody knew that at the GW1 deadline."""
    gw1_deadline = dt.datetime(2023, 8, 11, 17, 30, tzinfo=UTC)
    gw2_deadline = dt.datetime(2023, 8, 18, 17, 15, tzinfo=UTC)

    at_gw1 = wh.snapshot_at(gw1_deadline).table(
        "dim_player", where="season = '2023-24' AND code = ?", params=[RAYA]
    )
    assert at_gw1.iloc[0]["team_code"] == BRENTFORD

    at_gw2 = wh.snapshot_at(gw2_deadline).table(
        "dim_player", where="season = '2023-24' AND code = ?", params=[RAYA]
    )
    assert at_gw2.iloc[0]["team_code"] == ARSENAL
    # Same element id throughout: only `code` and the club changed.
    assert at_gw1.iloc[0]["element_id"] == at_gw2.iloc[0]["element_id"] == 113


def test_summer_transfer_shows_up_in_the_right_season(wh: Warehouse) -> None:
    rows = wh.sql(
        "SELECT season, team_code FROM dim_player WHERE code = ? ORDER BY as_of", [KUDUS]
    )
    clubs = dict(zip(rows["season"], rows["team_code"]))
    assert clubs["2024-25"] == WEST_HAM
    assert clubs["2025-26"] == TOTTENHAM


# ---------------------------------------------------------------------------
# leakage
# ---------------------------------------------------------------------------


def _deadline(wh: Warehouse, season: str, gw: int) -> dt.datetime:
    ko = wh.sql(
        "SELECT min(kickoff_utc) k FROM fact_fixture WHERE season = ? AND gw = ?",
        [season, gw],
    ).iloc[0]["k"]
    return ko.to_pydatetime() - dt.timedelta(minutes=90)


@pytest.mark.parametrize("season", SEASONS)
@pytest.mark.parametrize("gw", [2, 3, 4])
def test_snapshot_at_gw_deadline_sees_gw_minus_one_but_not_gw(
    wh: Warehouse, season: str, gw: int
) -> None:
    """The definition-of-done leakage proof, across every committed season.

    Standing at GW_k's deadline, GW_{k-1} is finalised and readable; GW_k has not
    kicked off and must be invisible. Anything else and every backtest built on
    this warehouse is scoring itself with the answers.
    """
    snap = wh.snapshot_at(_deadline(wh, season, gw))
    seen = snap.results_before(season)
    visible = set(seen["gw"].astype(int))

    assert gw - 1 in visible, f"{season} GW{gw - 1} should be final by GW{gw}'s deadline"
    assert gw not in visible, f"{season} GW{gw} leaked into its own deadline snapshot"
    assert max(visible) == gw - 1
    assert not seen.empty


def test_results_are_invisible_between_kickoff_and_finalisation(wh: Warehouse) -> None:
    """A gameweek that has been played but not finalised is still unknown.

    Bonus and the final BPS ranking are provisional until 09:00 UK the next day,
    so "the match is over" is not the same instant as "the points are known".
    """
    last_kickoff = dt.datetime(2025, 8, 18, 19, 0, tzinfo=UTC)
    final_at = dt.datetime(2025, 8, 19, 8, 0, tzinfo=UTC)

    just_after_full_time = last_kickoff + dt.timedelta(hours=2, minutes=30)
    assert just_after_full_time < final_at

    seen = wh.snapshot_at(just_after_full_time).results_before("2025-26")
    assert seen.empty

    seen = wh.snapshot_at(final_at).results_before("2025-26")
    assert set(seen["gw"].astype(int)) == {1}


def test_fixture_schedule_is_public_early_but_the_score_is_not(wh: Warehouse) -> None:
    """Knowing *when* Arsenal play is not leakage. Knowing the result is."""
    epoch = _deadline(wh, "2025-26", 1)
    at_epoch = wh.snapshot_at(epoch).table("fact_fixture", where="season = '2025-26'")
    gw1 = at_epoch[at_epoch["gw"] == 1]
    assert len(gw1) == 10                      # the whole gameweek is scheduled
    assert gw1["home_score"].isna().all()      # and not one score is known
    assert not gw1["finished"].any()

    later = wh.snapshot_at(dt.datetime(2025, 8, 19, 8, 0, tzinfo=UTC)).table(
        "fact_fixture", where="season = '2025-26' AND gw = 1"
    )
    assert later["home_score"].notna().all()
    assert later["finished"].all()


def test_upcoming_fixtures_never_include_a_played_match(wh: Warehouse) -> None:
    snap = wh.snapshot_at(_deadline(wh, "2025-26", 3))
    upcoming = snap.upcoming_fixtures("2025-26")
    assert not upcoming.empty
    assert (upcoming["gw"] >= 3).all()
    assert upcoming["home_score"].isna().all()


# ---------------------------------------------------------------------------
# honest nulls
# ---------------------------------------------------------------------------


def test_stats_that_did_not_exist_are_null_not_zero(wh: Warehouse) -> None:
    """Defensive contribution arrived in 2025-26. Zero would be a lie about 2022-23."""
    counts = wh.sql(
        "SELECT season, count(*) n, count(defensive_contribution) dc, count(tackles) tk "
        "FROM fact_player_fixture GROUP BY season ORDER BY season"
    ).set_index("season")
    for season in ("2022-23", "2023-24", "2024-25"):
        assert counts.loc[season, "dc"] == 0
        assert counts.loc[season, "tk"] == 0
        assert counts.loc[season, "n"] > 0
    assert counts.loc["2025-26", "dc"] == counts.loc["2025-26", "n"]


def test_stats_that_did_exist_are_carried_through(wh: Warehouse) -> None:
    row = wh.sql(
        "SELECT * FROM fact_player_fixture WHERE season = '2025-26' AND code = ? "
        "AND gw = 1", [RICE]
    )
    assert len(row) == 1
    assert row.iloc[0]["minutes"] > 0
    assert pd.notna(row.iloc[0]["bps"])
    assert pd.notna(row.iloc[0]["expected_goals"])
    assert row.iloc[0]["was_home"] in (True, False)


def test_ownership_is_left_null_rather_than_guessed(loaded, repo: VaastavRepo) -> None:
    """The ownership denominator needs the whole player pool; a slice cannot give it.

    vaastav stores `selected` as an absolute squad count, and the percentage is
    only recoverable as sum(selected)/15 across every player in the gameweek.
    On a slice that is meaningless, so it stays NULL and says so.
    """
    wh, reports, _ = loaded
    n = wh.sql("SELECT count(*) c FROM fact_player_state WHERE selected_by_pct IS NOT NULL")
    assert n.iloc[0]["c"] == 0
    assert any("ownership denominator" in w for r in reports for w in r.warnings)


def test_unavailable_columns_are_null_not_defaulted(wh: Warehouse) -> None:
    """vaastav's per-gameweek archive has no injury news. Filling it would be a lie."""
    row = wh.sql("SELECT * FROM fact_player_state LIMIT 1")
    assert row.iloc[0]["status"] is None
    assert pd.isna(row.iloc[0]["chance_of_playing_next_round"])
    assert pd.notna(row.iloc[0]["price_tenths"])


# ---------------------------------------------------------------------------
# upstream shape
# ---------------------------------------------------------------------------


@pytest.mark.network
def test_upstream_layout_is_unchanged() -> None:
    """vaastav's paths move between seasons; fail loudly rather than silently."""
    live = VaastavRepo(offline=False)
    try:
        raw = live.read_csv("2025-26", FILE_PLAYERS_RAW)
        merged = live.read_csv("2025-26", FILE_MERGED_GW)
    finally:
        live.close()
    assert {"id", "code", "element_type", "team", "team_code"} <= set(raw.columns)
    assert {"element", "fixture", "GW", "value", "selected"} <= set(merged.columns)
    assert "code" not in merged.columns
