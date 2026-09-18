"""The ``player_intel`` panel: the point-in-time filter and the named empties.

Two assertions carry this file.

``test_an_item_published_after_the_as_of_is_invisible`` is the leak guard. An
item's ``published_at`` is the instant the world could have known it, so a read
at a past deadline must not see news that broke afterwards. The rule lives in
``IntelStore.items``; this pins that the panel does not route around it.

``test_the_empty_case_names_the_filter_rather_than_the_store`` is the
distinction the whole payload is shaped around. "No news in the last 72 hours"
and "this warehouse has no intel tables" are different findings, and a reader
who cannot tell them apart cannot tell a quiet week from a dead feed.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.intel.items import Duty, DutyChange, IntelItem, IntelKind
from fpl_edge.intel.store import IntelStore
from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
from fpl_edge.platform.registry import ParamsInvalid, registered, run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"

T0 = pd.Timestamp("2026-07-01 12:00", tz="UTC")
NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

#: (code, web_name, team_code)
PLAYERS = [(100, "Semenyo", 1), (200, "Raya", 2)]


def _item(item_id, *, code, published, kind=IntelKind.AVAILABILITY,
          headline="knock in training", lag_hours=1.0):
    return IntelItem(
        item_id=item_id,
        published_at=published,
        observed_at=published + dt.timedelta(hours=lag_hours),
        kind=kind,
        headline=headline,
        source="club site",
        season=SEASON,
        player_code=code,
        team_code=1,
        body="Reported by the club.",
        source_url="https://example.invalid/news",
        confidence=0.9,
    )


def _seed(tmp_path, *, with_intel=True):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": code, "team_id": code, "name": name,
         "short_name": short, "as_of": T0}
        for code, name, short in ((1, "Bournemouth", "BOU"), (2, "Arsenal", "ARS"))
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": code, "web_name": web,
         "first_name": web, "second_name": web, "position": 3,
         "team_code": team, "as_of": T0}
        for code, web, team in PLAYERS
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": code,
         "price_tenths": 75, "selected_by_pct": 10.0, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": T0}
        for code, _, _ in PLAYERS
    ]))
    if with_intel:
        store = IntelStore(wh)
        store.put_items([
            _item("fresh", code=100,
                  published=NOW - dt.timedelta(hours=6)),
            _item("stale", code=100,
                  published=NOW - dt.timedelta(days=30)),
            _item("future", code=100,
                  published=NOW + dt.timedelta(hours=6),
                  headline="ruled out for the weekend"),
            _item("presser", code=100,
                  published=NOW - dt.timedelta(hours=4),
                  kind=IntelKind.PRESS_CONFERENCE,
                  headline="manager asked about the knock"),
        ])
        store.put_changes([
            DutyChange(
                change_id="big", season=SEASON, code=100, duty=Duty.PENALTIES,
                ord_before=2, ord_after=1,
                prior_as_of=NOW - dt.timedelta(days=2),
                detected_at=NOW - dt.timedelta(days=1),
                delta_goals_per_game=0.09,
                headline="first-choice penalties", team_code=1,
            ),
            DutyChange(
                change_id="small", season=SEASON, code=200,
                duty=Duty.CORNERS_INDIRECT, ord_before=4, ord_after=3,
                prior_as_of=NOW - dt.timedelta(days=2),
                detected_at=NOW - dt.timedelta(days=1),
                delta_goals_per_game=0.001,
                headline="fourth to third on corners", team_code=2,
            ),
        ])
    wh.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return _seed(tmp_path)


def test_the_panel_is_registered():
    assert "player_intel" in registered()


def test_an_item_published_after_the_as_of_is_invisible(db):
    """THE leak guard. The item published six hours from now is not news yet."""
    result = run_script(
        "player_intel",
        {"code": 100, "hours": 72, "as_of": NOW.isoformat()},
        db=db,
    ).result
    ids = [item["item_id"] for item in result["items"]]
    assert "future" not in ids
    assert set(ids) == {"fresh", "presser"}


def test_an_item_outside_the_window_is_counted_rather_than_listed(db):
    result = run_script(
        "player_intel",
        {"code": 100, "hours": 72, "as_of": NOW.isoformat()},
        db=db,
    ).result
    assert result["counts"]["items_in_window"] == 2
    assert result["counts"]["items_outside_window"] == 1


def test_every_item_carries_both_instants_and_the_lag(db):
    result = run_script(
        "player_intel",
        {"code": 100, "as_of": NOW.isoformat()},
        db=db,
    ).result
    for item in result["items"]:
        assert item["published_at"]
        assert item["observed_at"]
        assert item["observed_at"] > item["published_at"]
        assert item["lag_hours"] == pytest.approx(1.0)


def test_the_kind_filter_restricts_and_is_echoed(db):
    result = run_script(
        "player_intel",
        {"code": 100, "kind": "press_conference", "as_of": NOW.isoformat()},
        db=db,
    ).result
    assert result["kind"] == "press_conference"
    assert [item["item_id"] for item in result["items"]] == ["presser"]


def test_a_set_piece_move_below_the_threshold_is_dropped(db):
    result = run_script(
        "player_intel",
        {"hours": 72, "as_of": NOW.isoformat()},
        db=db,
    ).result
    assert [c["change_id"] for c in result["changes"]] == ["big"]
    assert result["changes"][0]["is_promotion"] is True
    assert result["changes"][0]["prior_observation"]


def test_the_empty_case_names_the_filter_rather_than_the_store(db):
    """A quiet window is a result. It says so, and says what is outside it."""
    result = run_script(
        "player_intel",
        {"code": 100, "hours": 1, "include_changes": False,
         "as_of": NOW.isoformat()},
        db=db,
    ).result
    assert result["empty"] is True
    assert "Semenyo" in result["reason"]
    assert "outside the time window" in result["reason"]


def test_a_warehouse_with_no_intel_tables_says_that_instead(tmp_path):
    """The other empty: no feed at all, with the command that creates one."""
    db = _seed(tmp_path, with_intel=False)
    result = run_script("player_intel", {"as_of": NOW.isoformat()}, db=db).result
    assert result["empty"] is True
    assert "intel collect" in result["reason"]
    assert "missing feed" in result["reason"]


def test_items_absent_but_changes_present_is_not_an_empty(db):
    """Two channels in one payload: one being quiet does not blank the other."""
    result = run_script(
        "player_intel",
        {"code": 100, "hours": 1, "as_of": NOW.isoformat()},
        db=db,
    ).result
    assert "empty" not in result
    assert result["items"] == []
    assert result["items_reason"]
    assert [c["change_id"] for c in result["changes"]] == ["big"]


def test_an_unknown_kind_is_refused_by_the_schema(db):
    with pytest.raises(ParamsInvalid):
        run_script("player_intel", {"kind": "rumour"}, db=db)


def test_the_params_schema_rejects_an_entry_id(db):
    with pytest.raises(ParamsInvalid):
        run_script("player_intel", {"entry_id": 4490171}, db=db)


def test_a_naive_as_of_is_refused(db):
    with pytest.raises(ValueError, match="timezone"):
        run_script("player_intel", {"as_of": "2026-09-18T12:00:00"}, db=db)
