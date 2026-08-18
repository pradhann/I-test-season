"""Point-in-time correctness tests.

These are the tests that decide whether the backtest is worth anything. Each one
encodes a specific way an FPL backtest leaks the future.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.store import Warehouse

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
