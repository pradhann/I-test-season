"""The public-endpoint layer, and the boundary we refuse to cross.

The fixtures under ``tests/fixtures/myteam/`` are the *real* payloads for entry
4490171, fetched on 2026-08-18. They are committed so the parsing is pinned
against bytes FPL actually served rather than against what the docs imply, and
so the pre-season shape -- every interesting field null or empty -- cannot quietly
stop being tested once the season starts.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from fpl_edge.myteam.sources import (
    AUTHENTICATED_PATHS,
    AuthenticatedEndpointError,
    PublicEntryClient,
    forbid_authenticated_url,
    parse_entry,
    parse_history,
    parse_picks,
    parse_transfers,
)
from fpl_edge.types import Money

FIXTURES = Path(__file__).parent.parent / "fixtures" / "myteam"
ENTRY_ID = 4490171


def load(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture()
def entry():
    return parse_entry(load("entry_4490171.json"))


@pytest.fixture()
def history():
    return parse_history(load("history_4490171.json"))


# -- the forbidden endpoint --------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://fantasy.premierleague.com/api/my-team/4490171/",
        "https://fantasy.premierleague.com/api/My-Team/4490171/",
        "/api/me/",
        "https://users.premierleague.com/accounts/login/",
    ],
)
def test_authenticated_urls_are_refused(url: str) -> None:
    """The 403 must fail as a rule, not as a network result.

    If this ever passes silently, someone has decided the fix for
    /api/my-team/ returning 403 is to supply credentials.
    """
    with pytest.raises(AuthenticatedEndpointError):
        forbid_authenticated_url(url)


def test_public_entry_urls_are_allowed() -> None:
    for path in (
        f"https://fantasy.premierleague.com/api/entry/{ENTRY_ID}/",
        f"https://fantasy.premierleague.com/api/entry/{ENTRY_ID}/history/",
        f"https://fantasy.premierleague.com/api/entry/{ENTRY_ID}/transfers/",
        f"https://fantasy.premierleague.com/api/entry/{ENTRY_ID}/event/1/picks/",
    ):
        assert forbid_authenticated_url(path) == path


def test_client_checks_every_url_it_builds(monkeypatch) -> None:
    """The guard is in the request path, not only in the helper."""
    seen: list[str] = []

    class Recorder:
        def get_json(self, endpoint, params=None):
            seen.append(endpoint)
            raise RuntimeError("stop here")

        def close(self):
            pass

    client = PublicEntryClient(fetcher=Recorder())
    with pytest.raises(RuntimeError):
        client.entry(ENTRY_ID)
    assert seen == [f"entry/{ENTRY_ID}/"]

    # And the forbidden one never reaches the fetcher at all.
    client.base_url = "https://fantasy.premierleague.com/api"
    with pytest.raises(AuthenticatedEndpointError):
        client._get(f"my-team/{ENTRY_ID}/")
    assert seen == [f"entry/{ENTRY_ID}/"]


def test_authenticated_paths_are_not_empty() -> None:
    """A refactor that empties this tuple would silently disable the guard."""
    assert "/my-team/" in AUTHENTICATED_PATHS


# -- parsing the real pre-season payloads ------------------------------------


def test_entry_identifies_the_manager(entry) -> None:
    assert entry.entry_id == ENTRY_ID
    assert entry.name == "i-test"
    assert entry.player_name == "Nripesh Pradhan"


def test_preseason_money_is_none_not_zero(entry) -> None:
    """None means 'the season has not started'; 0 means 'you spent it all'.

    Coercing the first into the second makes the budget arithmetic lie, and it
    lies in the direction of a squad you cannot afford.
    """
    assert entry.last_deadline_bank is None
    assert entry.last_deadline_value is None
    assert entry.last_deadline_total_transfers == 0
    assert entry.entered_events == ()
    assert entry.has_started is False


def test_history_has_eight_past_seasons_and_no_current_gameweeks(history) -> None:
    assert len(history.past) == 8
    assert history.current == ()
    assert history.chips == ()
    assert history.last_gw is None
    assert history.past[2].season_name == "2018/19"
    assert history.past[2].rank == 9_524


def test_transfers_endpoint_is_empty_preseason() -> None:
    assert parse_transfers(load("transfers_4490171.json")) == ()


def test_money_must_arrive_as_integer_tenths() -> None:
    """A float from the API would be a schema change we must notice loudly."""
    with pytest.raises(TypeError):
        parse_entry({**load("entry_4490171.json"), "last_deadline_bank": 2.5})


def test_transfers_are_sorted_forward_in_time() -> None:
    """The endpoint returns newest-first; purchase-price derivation walks forward."""
    rows = parse_transfers(
        [
            {"event": 5, "element_in": 2, "element_in_cost": 70, "element_out": 1,
             "element_out_cost": 65, "time": "2026-09-20T10:00:00Z"},
            {"event": 3, "element_in": 1, "element_in_cost": 60, "element_out": 9,
             "element_out_cost": 50, "time": "2026-09-05T10:00:00Z"},
        ]
    )
    assert [int(r.gw) for r in rows] == [3, 5]
    assert rows[0].element_in_cost == Money(60)


def test_picks_carry_slot_order_and_captaincy() -> None:
    picks = parse_picks(
        3,
        {
            "active_chip": "bboost",
            "entry_history": {"bank": 12, "value": 1013, "event_transfers": 2,
                              "event_transfers_cost": 4},
            "picks": [
                {"element": 7, "position": 1, "multiplier": 2, "is_captain": True,
                 "is_vice_captain": False},
                {"element": 8, "position": 12, "multiplier": 1, "is_captain": False,
                 "is_vice_captain": True},
            ],
        },
    )
    assert picks.active_chip == "bboost"
    assert picks.bank == Money(12) and picks.value == Money(1013)
    assert picks.event_transfers_cost == 4
    assert picks.picks[0].is_captain and picks.picks[0].position == 1


def test_timestamps_are_always_utc_aware() -> None:
    history = parse_history(
        {"current": [], "past": [], "chips": [{"name": "wildcard", "event": 8,
                                               "time": "2026-10-17T10:00:00.000000Z"}]}
    )
    played = history.chips[0].played_utc
    assert played is not None
    assert played.utcoffset() == dt.timedelta(0)
