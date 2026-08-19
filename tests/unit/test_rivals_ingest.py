"""Parsing and observability. The `as_of` assertions are the important ones.

Everything else in this file is ordinary parsing coverage. The tests about when
a fact became public are the ones that stop this package from quietly poisoning
every backtest that reads it: a rival's squad stamped with the crawl time rather
than the deadline lets a model "know" the elite's GW7 team on the Sunday, after
the captain's hat-trick, and every copying result computed on top of that is
worthless in a way that produces excellent-looking numbers.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.rivals.history import parse_history
from fpl_edge.ingest.rivals.picks import parse_picks, parse_transfers
from fpl_edge.ingest.rivals.roster import _league_members

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rivals"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
DEADLINE_GW3 = dt.datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


# -- history ----------------------------------------------------------------

def test_past_seasons_parse_with_ranks_and_percentages():
    past, current, chips = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert len(past) == 4
    row = past[past["season"] == "2018/19"].iloc[0]
    assert row["overall_rank"] == 9524
    assert row["rank_percentage"] == pytest.approx(0.2)
    assert row["total_points"] == 2387


def test_season_labels_keep_fpl_slash_form():
    """'2018/19' is not silently rewritten to the warehouse's '2018-19'.

    These rows describe a manager's finish and never join to dim_player, so
    converting would invent an equivalence between two unrelated spellings.
    """
    past, _c, _ch = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert set(past["season"]) == {"2018/19", "2019/20", "2021/22", "2022/23"}


def test_current_gameweeks_carry_hits_and_value():
    _p, current, _ch = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert len(current) == 4
    gw3 = current[current["gw"] == 3].iloc[0]
    assert gw3["event_transfers"] == 3
    assert gw3["event_transfers_cost"] == 8
    assert gw3["value_tenths"] == 1008


def test_chips_parse_with_gameweek():
    _p, _c, chips = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert list(chips["chip"]) == ["wildcard"]
    assert list(chips["gw"]) == [4]


def test_empty_history_yields_empty_frames_not_an_exception():
    past, current, chips = parse_history(
        1, {"past": [], "current": [], "chips": []}, as_of=AS_OF, season="2026-27"
    )
    assert past.empty and current.empty and chips.empty


def test_missing_rank_percentage_becomes_null_not_zero():
    """A null percentile is 'unknown'. Zero would mean 'finished first'."""
    body = {"past": [{"season_name": "2020/21", "total_points": 2000,
                      "rank": 500, "rank_percentage": ""}], "current": [], "chips": []}
    past, _c, _ch = parse_history(1, body, as_of=AS_OF, season="2026-27")
    assert pd.isna(past.iloc[0]["rank_percentage"])


# -- picks ------------------------------------------------------------------

def test_picks_are_stamped_with_the_deadline_not_the_crawl_time():
    picks, _chips = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    assert (picks["as_of"] == DEADLINE_GW3).all(), (
        "a squad stamped with the crawl instant lets a backtest read it after "
        "the gameweek was scored"
    )


def test_multiplier_is_taken_from_the_api_not_rebuilt_from_flags():
    """Triple captain is multiplier 3, and no boolean pair encodes that."""
    picks, _chips = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    cap = picks[picks["is_captain"]].iloc[0]
    assert cap["element_id"] == 301
    assert cap["multiplier"] == 3
    bench = picks[picks["slot"] > 11]
    assert (bench["multiplier"] == 0).all()


def test_active_chip_becomes_a_chip_row():
    _picks, chips = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    assert len(chips) == 1
    assert chips.iloc[0]["chip"] == "3xc"
    assert chips.iloc[0]["as_of"] == DEADLINE_GW3


def test_no_active_chip_produces_no_chip_row():
    body = dict(_load("picks_gw3.json"))
    body["active_chip"] = None
    _picks, chips = parse_picks(
        999, 3, body, season="2026-27", deadline=DEADLINE_GW3
    )
    assert chips.empty


def test_fifteen_picks_with_bench_slots_preserved():
    picks, _c = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    assert len(picks) == 15
    assert sorted(picks["slot"]) == list(range(1, 16))


# -- transfers --------------------------------------------------------------

DEADLINES = {
    2: dt.datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    3: DEADLINE_GW3,
}


def test_transfers_are_stamped_with_their_gameweek_deadline():
    df = parse_transfers(999, _load("transfers.json"), season="2026-27", deadlines=DEADLINES)
    gw3 = df[df["gw"] == 3].iloc[0]
    assert gw3["as_of"] == DEADLINE_GW3
    # The manager MADE the transfer before the deadline; it became public AT it.
    assert gw3["time_utc"] < gw3["as_of"]


def test_transfer_for_an_unknown_gameweek_is_dropped_not_guessed():
    """Event 99 has no deadline. Admitting it would mean inventing an as_of."""
    df = parse_transfers(999, _load("transfers.json"), season="2026-27", deadlines=DEADLINES)
    assert 99 not in set(df["gw"])
    assert len(df) == 2


def test_transfer_costs_survive_parsing():
    df = parse_transfers(999, _load("transfers.json"), season="2026-27", deadlines=DEADLINES)
    row = df[df["element_in"] == 305].iloc[0]
    assert row["element_in_cost"] == 75
    assert row["element_out_cost"] == 71


def test_empty_transfer_list_is_the_normal_preseason_answer():
    df = parse_transfers(999, [], season="2026-27", deadlines=DEADLINES)
    assert df.empty
    assert list(df.columns)[:3] == ["entry_id", "season", "gw"]


# -- league membership ------------------------------------------------------

class _StubFetcher:
    def __init__(self, body):
        self.body = body
        self.calls = 0

    def get_json(self, endpoint, params=None):
        self.calls += 1

        class _F:
            pass

        f = _F()
        f.body = self.body
        return f


def test_membership_read_from_new_entries_when_standings_are_empty():
    """Pre-season every member sits in new_entries; standings is empty.

    This is the shape the API actually returns before GW1 and the reason the
    crawl can build a pool at all right now.
    """
    stub = _StubFetcher(_load("league_standings_preseason.json"))
    members, pages = _league_members(stub, 76109, kind="classic", max_pages=4)
    assert {m["entry_id"] for m in members} == {111, 222}
    assert pages == 1, "paging continued past a page that said has_next=false"
    assert members[0]["player_name"] == "Ada Lovelace"


def test_membership_paging_stops_at_the_cap():
    body = _load("league_standings_preseason.json")
    body["new_entries"]["has_next"] = True
    stub = _StubFetcher(body)
    _members, pages = _league_members(stub, 1, kind="classic", max_pages=3)
    assert pages == 3, "the page cap did not bound an endlessly-paging league"
