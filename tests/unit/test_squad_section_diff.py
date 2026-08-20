"""The recommended squad is only actionable next to the squad you own.

Before the first deadline transfers are unlimited and free, so the entire GW1
decision is the set difference between the plan and your fifteen. These tests
pin the parts that were wrong when the section first rendered live: the columns
paired players who could never be swapped for each other, and a bad attribute
took the whole section down.
"""

from __future__ import annotations

import types

import pytest

from fpl_edge.interfaces import squad_section


class _Pick:
    def __init__(self, code: int, is_captain: bool = False) -> None:
        self.code = code
        self.is_captain = is_captain


NAMES = {
    1: "Raya", 2: "Leno",            # GKP
    3: "Gabriel", 4: "Shaw",         # DEF
    5: "Mbeumo", 6: "Rogers",        # MID
    7: "Watkins", 8: "Isak",         # FWD
}
POS = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3, 6: 3, 7: 4, 8: 4}
PRICE = {1: 55, 2: 45, 3: 80, 4: 45, 5: 80, 6: 75, 7: 90, 8: 90}


def _plan(squad: list[int], captain: int) -> dict:
    return {"gw1": {"squad": squad, "captain": captain}}


def _diff(monkeypatch, mine: list[int], theirs: list[int],
          my_captain: int, plan_captain: int) -> str:
    state = types.SimpleNamespace(
        picks=tuple(_Pick(c, c == my_captain) for c in mine),
        bank=types.SimpleNamespace(tenths=5),
    )
    monkeypatch.setattr(
        "fpl_edge.myteam.report.current_state",
        lambda *a, **k: state,
    )
    return "\n".join(squad_section._render_diff(
        None, "2026-27", 1, None, _plan(theirs, plan_captain), NAMES, POS, PRICE
    ))


def test_every_row_is_a_swap_the_game_would_allow(monkeypatch) -> None:
    """Out and In must line up by position, not by set-iteration order.

    Set differences sorted by code paired a keeper leaving with a defender
    arriving -- a row that reads as a transfer but is not a legal one.
    """
    text = _diff(monkeypatch, mine=[1, 4, 6, 8], theirs=[2, 3, 5, 7],
                 my_captain=8, plan_captain=7)
    rows = [r for r in text.splitlines() if r.startswith("| ") and "---" not in r][1:]
    assert rows, "no transfer rows rendered"
    for row in rows:
        left, right = (cell.strip() for cell in row.strip("|").split("|"))
        assert left[:3] == right[:3], f"row pairs different positions: {row}"


def test_an_identical_squad_says_so_instead_of_an_empty_table(monkeypatch) -> None:
    text = _diff(monkeypatch, mine=[1, 3, 5, 7], theirs=[1, 3, 5, 7],
                 my_captain=7, plan_captain=7)
    assert "Identical to what you already own" in text
    assert "| Out | In |" not in text


def test_a_captain_disagreement_is_stated_outright(monkeypatch) -> None:
    """The captain is the single highest-variance GW1 call; never leave it implied."""
    differs = _diff(monkeypatch, mine=[1, 3, 5, 8], theirs=[1, 3, 5, 7],
                    my_captain=8, plan_captain=7)
    assert "you have **Isak**" in differs and "plan says **Watkins**" in differs
    agrees = _diff(monkeypatch, mine=[1, 3, 5, 7], theirs=[1, 3, 5, 7],
                   my_captain=7, plan_captain=7)
    assert "agree on **Watkins**" in agrees


def test_free_transfers_before_the_first_deadline_are_stated(monkeypatch) -> None:
    """A 9-change diff looks alarming until you are told it costs nothing."""
    text = _diff(monkeypatch, mine=[1, 4, 6, 8], theirs=[2, 3, 5, 7],
                 my_captain=8, plan_captain=7)
    assert "unlimited and free" in text
    assert "no hit" in text


def test_an_unreadable_squad_degrades_instead_of_killing_the_section(monkeypatch) -> None:
    """This exact failure blanked the whole recommended-squad section once."""
    def boom(*a, **k):
        raise RuntimeError("token revoked")

    monkeypatch.setattr("fpl_edge.myteam.report.current_state", boom)
    text = "\n".join(squad_section._render_diff(
        None, "2026-27", 1, None, _plan([1, 3], 3), NAMES, POS, PRICE
    ))
    assert "could not be read" in text
    assert "RuntimeError" in text


def test_an_unknown_squad_points_at_the_two_ways_to_supply_one(monkeypatch) -> None:
    state = types.SimpleNamespace(picks=None, bank=types.SimpleNamespace(tenths=0))
    monkeypatch.setattr(
        "fpl_edge.myteam.report.current_state", lambda *a, **k: state
    )
    text = "\n".join(squad_section._render_diff(
        None, "2026-27", 1, None, _plan([1, 3], 3), NAMES, POS, PRICE
    ))
    assert "fpl myteam auth" in text and "fpl myteam set" in text


@pytest.mark.parametrize("attr", ["picks", "bank"])
def test_the_state_fields_the_diff_reads_actually_exist(attr: str) -> None:
    """Guards the class of bug that broke this live.

    `state.picks.picks` and `state.squad` have both been written against this
    object; neither exists. Assert the real field names on the real class.
    """
    from fpl_edge.myteam.state import MyTeamState

    # A field OR a property both satisfy attribute access; `bank` is the latter.
    assert attr in MyTeamState.__dataclass_fields__ or hasattr(MyTeamState, attr), (
        f"MyTeamState exposes no `{attr}`; the diff reads it"
    )
    assert not hasattr(MyTeamState, "squad"), (
        "`squad` is back; the diff and its callers read `picks`"
    )
