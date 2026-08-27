"""Contracts of the two live-API MCP tools that render bootstrap rows.

``get_team_picks`` and ``get_expert_transfers`` both take element ids from an
FPL API payload and look them up in the bootstrap elements table. Both used to
do that with ``elements_df.loc.get(id)`` -- a method pandas' ``_LocIndexer``
does not have -- so both raised ``AttributeError`` on EVERY invocation and had
apparently never run. Nothing caught it because nothing tested the toolbelt.

These tests exercise the tools end to end with the two seams that touch the
outside world -- the FPL HTTP fetch and the bootstrap DataFrame -- replaced by
fakes. No network, no warehouse, no cache file: the same reason
``test_chat_tools_contract.py`` imports ``fpl_mcp`` as an ordinary in-repo
module and fakes only at the edges.

What is asserted, beyond "does not crash":

* a known element renders its real name, club and price;
* an element id the table does not know is reported AS UNKNOWN with its raw
  id -- never skipped, never given a plausible name, never priced £0.0m.
  That last one is the invariant: a tool with no data says why.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd
import pytest

from fpl_edge.config import USER
from fpl_mcp.tools import expert_tools, team_tools


def _elements() -> pd.DataFrame:
    """A two-row stand-in for bootstrap ``elements``, with the columns used."""
    return pd.DataFrame([
        {"id": 1, "first_name": "Erling", "second_name": "Haaland",
         "position": "FWD", "team_name": "Man City", "now_cost": 145,
         "total_points": 12},
        {"id": 2, "first_name": "Bruno", "second_name": "Fernandes",
         "position": "MID", "team_name": "Man Utd", "now_cost": 90,
         "total_points": 9},
    ])


@pytest.fixture()
def elements(monkeypatch) -> pd.DataFrame:
    df = _elements()
    monkeypatch.setattr(team_tools.fpl_data, "get_elements_df", lambda *a, **k: df.copy())
    monkeypatch.setattr(expert_tools.fpl_data, "get_elements_df", lambda *a, **k: df.copy())
    return df


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real HTTP call in these tests is a test bug, not a slow test."""
    def _boom(*a, **k):  # pragma: no cover - only fires on regression
        raise AssertionError("unit tests must not hit the FPL API")

    monkeypatch.setattr(team_tools.requests, "get", _boom)
    monkeypatch.setattr(expert_tools.requests, "get", _boom)


def _picks(*elements: int) -> Dict[str, Any]:
    return {"picks": [
        {"element": e, "position": i + 1, "multiplier": 2 if i == 0 else 1,
         "is_captain": i == 0, "is_vice_captain": i == 1}
        for i, e in enumerate(elements)
    ]}


# -- get_team_picks -----------------------------------------------------------


def test_team_picks_renders_known_players(monkeypatch, elements) -> None:
    """The regression guard: this raised AttributeError on every call."""
    monkeypatch.setattr(team_tools, "_fetch_team_event_picks",
                        lambda tid, gw: _picks(1, 2))
    out = team_tools.get_team_picks(3)
    assert "Erling Haaland" in out
    assert "Bruno Fernandes" in out
    assert "14.5" in out  # now_cost 145 -> £14.5m
    assert "unknown" not in out


def test_team_picks_names_the_unknown_id_instead_of_inventing_a_player(
    monkeypatch, elements,
) -> None:
    """No fabricated data: an id we cannot resolve says so, with the raw id.

    It is also not silently dropped -- a 15-man squad rendering as 14 with no
    explanation is its own kind of lie.
    """
    monkeypatch.setattr(team_tools, "_fetch_team_event_picks",
                        lambda tid, gw: _picks(1, 999))
    out = team_tools.get_team_picks(3)
    assert "unknown (element 999)" in out
    assert "Unknown Player" not in out  # no plausible-looking placeholder name
    assert "999" in out.split("Note:")[1]  # the note enumerates the bad ids
    assert "Erling Haaland" in out  # the resolvable pick still renders
    # Every pick is accounted for: two picks in, two rows out.
    body = out.split("Note:")[0]
    assert len([ln for ln in body.splitlines() if "£" in ln or "unknown (" in ln
                or "Haaland" in ln]) == 2


def test_team_picks_reports_empty_rather_than_guessing(monkeypatch, elements) -> None:
    monkeypatch.setattr(team_tools, "_fetch_team_event_picks",
                        lambda tid, gw: {"picks": []})
    assert "No picks found" in team_tools.get_team_picks(3)


def test_team_picks_defaults_to_the_owners_entry_and_honours_an_override(
    monkeypatch, elements,
) -> None:
    """TEAM_ID comes from fpl_edge.config.USER, not a second copy of the number."""
    seen: List[int] = []

    def _fetch(team_id, gw):
        seen.append(team_id)
        return _picks(1)

    monkeypatch.setattr(team_tools, "_fetch_team_event_picks", _fetch)
    team_tools.get_team_picks(3)
    team_tools.get_team_picks(3, team_id=1234567)
    assert seen == [int(USER.entry_id), 1234567]
    assert team_tools.TEAM_ID == int(USER.entry_id)


# -- get_expert_transfers -----------------------------------------------------


def _transfer(in_id: int, out_id: int, event: int = 3) -> Dict[str, Any]:
    return {"event": event, "element_in": in_id, "element_out": out_id,
            "time": f"2026-08-2{event}T10:00:00Z"}


def test_expert_transfers_renders_known_players(monkeypatch, elements) -> None:
    """The regression guard: this raised AttributeError after the fetch."""
    monkeypatch.setattr(expert_tools, "_fetch_transfers",
                        lambda eid: [_transfer(1, 2)])
    out = expert_tools.get_expert_transfers("FPL Harry")
    assert "In Erling Haaland (£14.5m)" in out
    assert "Out Bruno Fernandes (£9.0m)" in out


def test_expert_transfers_never_prices_an_unknown_player_at_zero(
    monkeypatch, elements,
) -> None:
    """£0.0m is a fabricated figure. An unknown price is reported as unknown."""
    monkeypatch.setattr(expert_tools, "_fetch_transfers",
                        lambda eid: [_transfer(1, 999)])
    out = expert_tools.get_expert_transfers("FPL Harry")
    assert "unknown player (element 999, price unknown)" in out
    assert "£0.0m" not in out
    assert "In Erling Haaland (£14.5m)" in out
    assert "Note:" in out and "999" in out.split("Note:")[1]


def test_expert_transfers_says_so_when_there_are_none(monkeypatch, elements) -> None:
    monkeypatch.setattr(expert_tools, "_fetch_transfers", lambda eid: [])
    assert "No transfers recorded" in expert_tools.get_expert_transfers("FPL Harry")


def test_expert_transfers_rejects_an_unknown_expert_before_fetching(
    monkeypatch, elements,
) -> None:
    def _boom(eid):  # pragma: no cover - only fires on regression
        raise AssertionError("must not fetch for an unresolved expert")

    monkeypatch.setattr(expert_tools, "_fetch_transfers", _boom)
    assert "not found" in expert_tools.get_expert_transfers("Nobody At All")


# -- the shared lookup --------------------------------------------------------


@pytest.mark.parametrize("mod", [team_tools, expert_tools])
def test_lookup_element_returns_none_for_a_missing_id(mod) -> None:
    """The whole defect in one line: ``.loc`` has no ``.get``, this does."""
    df = _elements().set_index("id")
    assert mod._lookup_element(df, 1)["second_name"] == "Haaland"
    assert mod._lookup_element(df, 999) is None
    assert mod._lookup_element(df, None) is None


@pytest.mark.parametrize("mod", [team_tools, expert_tools])
@pytest.mark.parametrize("junk", ["abc", 1.5, ["a"], {"a": 1}])
def test_lookup_element_survives_a_malformed_id(mod, junk) -> None:
    """The id comes off the wire, so it is not trusted to be an int.

    pandas raises KeyError for most junk labels but TypeError for an
    unhashable one -- both mean "not in the table", and neither may reach
    the caller as a crash.
    """
    assert mod._lookup_element(_elements().set_index("id"), junk) is None
