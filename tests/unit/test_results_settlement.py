"""Live results settlement: the gate, the rows, and the DGW honesty rule.

Before this module existed, nothing wrote the current season into
``fact_player_fixture`` — the audit's highest-leverage finding. These tests
pin the three properties that make settlement trustworthy: provisional
numbers are refused, single-fixture rows carry every column, and a double
gameweek never smears GW totals across fixtures.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.ingest.results import NotFinalError, assert_final, build_rows

UTC = dt.timezone.utc


def _fixture(fid: int, *, finished: bool = True, provisional: bool | None = None,
             kickoff: str = "2026-08-23T15:00:00Z"):
    return {"id": fid, "finished": finished,
            "finished_provisional": finished if provisional is None else provisional,
            "kickoff_time": kickoff, "team_h": 1}


def _status(gw: int = 1, *, bonus: bool) -> dict:
    return {"status": [
        {"event": gw, "date": "2026-08-22", "bonus_added": bonus},
        {"event": gw, "date": "2026-08-23", "bonus_added": bonus},
    ]}


class TestTheGate:
    def test_an_unplayed_fixture_refuses_with_no_override(self) -> None:
        now = dt.datetime(2026, 8, 24, 12, tzinfo=UTC)   # long past the 9am rule
        with pytest.raises(NotFinalError, match="not even provisionally"):
            assert_final(1, [_fixture(1),
                             _fixture(2, finished=False, provisional=False)],
                         _status(bonus=True), now=now)

    def test_provisionally_finished_settles_after_the_9am_rule(self) -> None:
        """The live GW1 shape: provisional=True, finished=False for hours.

        Past 09:00 UK the day after the last kickoff, the verified rule stands
        in for FPL's lagging flag.
        """
        now = dt.datetime(2026, 8, 24, 8, 30, tzinfo=UTC)
        assert_final(1, [_fixture(1, finished=False, provisional=True)],
                     _status(bonus=False), now=now)

    def test_provisional_bonus_before_finalisation_refuses(self) -> None:
        # last kickoff Sun 23 Aug 15:00Z -> finalises 09:00 UK Mon 24 = 08:00Z
        now = dt.datetime(2026, 8, 24, 6, 0, tzinfo=UTC)
        with pytest.raises(NotFinalError, match="bonus"):
            assert_final(1, [_fixture(1)], _status(bonus=False), now=now)

    def test_bonus_added_settles_even_before_the_9am_rule(self) -> None:
        now = dt.datetime(2026, 8, 24, 6, 0, tzinfo=UTC)
        assert_final(1, [_fixture(1)], _status(bonus=True), now=now)

    def test_past_the_finalisation_instant_settles_even_without_the_flag(self) -> None:
        now = dt.datetime(2026, 8, 24, 8, 30, tzinfo=UTC)
        assert_final(1, [_fixture(1)], _status(bonus=False), now=now)

    def test_bonus_added_alone_is_not_enough_while_fixtures_stay_provisional(self) -> None:
        """bonus_added with fixtures unprocessed and the 9am rule not yet met."""
        now = dt.datetime(2026, 8, 24, 6, 0, tzinfo=UTC)
        with pytest.raises(NotFinalError, match="not fully processed"):
            assert_final(1, [_fixture(1, finished=False, provisional=True)],
                         _status(bonus=True), now=now)


def _element(eid: int, fixtures_pts: dict[int, list], stats: dict) -> dict:
    return {
        "id": eid,
        "stats": stats,
        "explain": [{"fixture": f, "stats": s} for f, s in fixtures_pts.items()],
    }


AS_OF = dt.datetime(2026, 8, 24, 9, tzinfo=UTC)


def test_a_single_fixture_row_carries_every_column() -> None:
    live = {"elements": [_element(
        11,
        {101: [{"identifier": "minutes", "points": 2, "value": 90}]},
        {"minutes": 90, "goals_scored": 2, "assists": 0, "clean_sheets": 0,
         "goals_conceded": 1, "own_goals": 0, "penalties_saved": 0,
         "penalties_missed": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0,
         "bonus": 3, "bps": 61, "starts": 1, "tackles": 1,
         "clearances_blocks_interceptions": 2, "recoveries": 4,
         "defensive_contribution": 0, "total_points": 13,
         "expected_goals": "1.42", "expected_assists": "0.20",
         "expected_goals_conceded": "0.90"},
    )]}
    rows, warnings = build_rows(
        "2026-27", 1, live, [_fixture(101)], {11: 223094}, as_of=AS_OF
    )
    assert len(rows) == 1 and not warnings
    r = rows.iloc[0]
    assert r["code"] == 223094 and r["fixture_id"] == 101
    assert r["total_points"] == 13 and r["bps"] == 61
    assert r["expected_goals"] == pytest.approx(1.42)
    assert r["as_of"] == AS_OF


def test_a_double_gameweek_is_split_not_smeared() -> None:
    """GW totals that cannot be attributed per fixture go NULL, loudly."""
    live = {"elements": [_element(
        11,
        {
            101: [{"identifier": "minutes", "points": 2, "value": 90},
                  {"identifier": "goals_scored", "points": 8, "value": 2}],
            102: [{"identifier": "minutes", "points": 1, "value": 30}],
        },
        {"minutes": 120, "goals_scored": 2, "total_points": 12, "bps": 70,
         "expected_goals": "1.9", "bonus": 3, "starts": 2},
    )]}
    rows, warnings = build_rows(
        "2026-27", 7, live, [_fixture(101), _fixture(102)], {11: 5}, as_of=AS_OF
    )
    assert len(rows) == 2
    first = rows[rows["fixture_id"] == 101].iloc[0]
    second = rows[rows["fixture_id"] == 102].iloc[0]
    assert first["minutes"] == 90 and second["minutes"] == 30
    assert first["goals_scored"] == 2 and second["goals_scored"] == 0
    assert first["total_points"] == 10 and second["total_points"] == 1
    # the honesty rule: no invented split of GW-total-only stats
    assert first["expected_goals"] is None or first.isna()["expected_goals"]
    assert first["bps"] is None or first.isna()["bps"]
    assert warnings and "NULL" in warnings[0]


def test_unmapped_elements_are_skipped_loudly_never_guessed() -> None:
    live = {"elements": [_element(
        99, {101: [{"identifier": "minutes", "points": 1, "value": 45}]},
        {"minutes": 45, "total_points": 1},
    )]}
    rows, warnings = build_rows("2026-27", 1, live, [_fixture(101)], {}, as_of=AS_OF)
    assert rows.empty
    assert warnings and "no code mapping" in warnings[0]


def test_a_player_who_did_not_feature_writes_no_rows() -> None:
    live = {"elements": [{"id": 11, "stats": {"minutes": 0}, "explain": []}]}
    rows, _ = build_rows("2026-27", 1, live, [_fixture(101)], {11: 5}, as_of=AS_OF)
    assert rows.empty
