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
  That last one is the invariant: a tool with no data says why;
* a pick that arrives with no multiplier says so, rather than defaulting to
  the 1 that reads as "started";
* **no unverified name-to-id pair can reach a user-visible answer.** The
  module used to ship twenty of them, mirrored from a previous season and
  never checked, so ``get_manager_history("Holly Shand")`` printed a
  stranger's ranks under her name. ``test_no_stale_seed_name_can_reach_an_
  answer`` walks every one of those twenty names through all three tools and
  fails if any of them produces anything but a refusal -- and
  ``test_expert_tools_ships_no_name_to_id_map`` fails if such a map is ever
  reintroduced to the module, whatever it is called.

Identity resolution reads ``dim_manager`` through the engine's read-only
warehouse copy. That is the third seam, and it is faked here too
(``expert_tools._crawled_managers``): these tests must not touch the DuckDB
file at all, not even read-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import pytest

from fpl_edge.config import USER
from fpl_edge.ingest.rivals.elite import ELITE_NAMED
from fpl_edge.ingest.rivals.roster import EXPERT_SEEDS
from fpl_mcp.tools import expert_tools, team_tools

#: A manager the engine has actually verified, taken from the curated list
#: rather than written down here, so this file cannot become a twenty-first
#: unverified name-to-id pair of its own.
VERIFIED = ELITE_NAMED[0]


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


@pytest.fixture(autouse=True)
def no_warehouse(monkeypatch):
    """No DuckDB, not even a read copy. None means "the crawl was unreadable".

    Overridden by the ``crawled`` fixture where a test needs crawled names.
    """
    monkeypatch.setattr(expert_tools, "_crawled_managers", lambda: None)


@pytest.fixture()
def crawled(monkeypatch) -> pd.DataFrame:
    """A stand-in for ``dim_manager``: names the crawl read back from the API."""
    df = pd.DataFrame([
        {"entry_id": 424242, "player_name": "Levi Longworth",
         "entry_name": "Longworth XI", "source": "top1k:2026-27:gw1"},
    ])
    monkeypatch.setattr(expert_tools, "_crawled_managers", lambda: df.copy())
    return df


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
    out = expert_tools.get_expert_transfers(VERIFIED.name)
    assert "In Erling Haaland (£14.5m)" in out
    assert "Out Bruno Fernandes (£9.0m)" in out


def test_expert_transfers_never_prices_an_unknown_player_at_zero(
    monkeypatch, elements,
) -> None:
    """£0.0m is a fabricated figure. An unknown price is reported as unknown."""
    monkeypatch.setattr(expert_tools, "_fetch_transfers",
                        lambda eid: [_transfer(1, 999)])
    out = expert_tools.get_expert_transfers(VERIFIED.name)
    assert "unknown player (element 999, price unknown)" in out
    assert "£0.0m" not in out
    assert "In Erling Haaland (£14.5m)" in out
    assert "Note:" in out and "999" in out.split("Note:")[1]


def test_expert_transfers_says_so_when_there_are_none(monkeypatch, elements) -> None:
    monkeypatch.setattr(expert_tools, "_fetch_transfers", lambda eid: [])
    assert "No transfers recorded" in expert_tools.get_expert_transfers(VERIFIED.name)


def test_expert_transfers_rejects_an_unknown_expert_before_fetching(
    monkeypatch, elements,
) -> None:
    """An unverifiable name is refused, and refused BEFORE any request."""
    def _boom(eid):  # pragma: no cover - only fires on regression
        raise AssertionError("must not fetch for an unresolved expert")

    monkeypatch.setattr(expert_tools, "_fetch_transfers", _boom)
    out = expert_tools.get_expert_transfers("Nobody At All")
    assert "cannot verify who 'Nobody At All' is" in out


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


# -- the multiplier -----------------------------------------------------------


def _pick(element: int, **over: Any) -> Dict[str, Any]:
    p = {"element": element, "position": 1, "multiplier": 1,
         "is_captain": False, "is_vice_captain": False}
    p.update(over)
    return p


def test_team_picks_never_invents_a_multiplier(monkeypatch, elements) -> None:
    """A missing multiplier is a hole in the payload, not a 1.

    ``fpl_edge.models.field.observed._one_squad`` refuses a whole squad over
    this -- "A missing one is a hole in the crawl, not a zero". The read-only
    listing is gentler and still shows the pick, but it must never print a
    number the API did not send: defaulting to 1 asserts the player started.
    """
    payload = {"picks": [_pick(1, multiplier=2, is_captain=True), _pick(2)]}
    del payload["picks"][1]["multiplier"]
    monkeypatch.setattr(team_tools, "_fetch_team_event_picks", lambda tid, gw: payload)
    out = team_tools.get_team_picks(3)

    bruno = [ln for ln in out.splitlines() if "Bruno Fernandes" in ln]
    assert len(bruno) == 1, out
    assert "?" in bruno[0]
    # The row must not claim a multiplier of any value.
    cells = bruno[0].split()
    assert "1" not in cells and "0" not in cells, bruno[0]
    assert "no multiplier" in out  # and the note says which pick, and why
    assert "2" in out.split("no multiplier")[0]  # element 2 is named in the note
    # The pick that DID carry a multiplier still renders it verbatim.
    haaland = [ln for ln in out.splitlines() if "Erling Haaland" in ln][0]
    assert "2" in haaland.split()


@pytest.mark.parametrize("bad", [None, "", "x", [1], {}])
def test_multiplier_helper_refuses_anything_that_is_not_a_number(bad) -> None:
    rendered, rank = team_tools._multiplier({"element": 1, "multiplier": bad})
    assert rendered == "?"
    assert rank == -1  # cannot outrank a real multiplier in the sort


def test_multiplier_helper_keeps_a_real_value_verbatim() -> None:
    assert team_tools._multiplier({"multiplier": 0}) == ("0", 0)
    assert team_tools._multiplier({"multiplier": 3}) == ("3", 3)


# -- get_expert_teams_summary: the sibling of the "degrade honestly" fix ------


def _two_managers(monkeypatch, *, picks_by_entry: Dict[int, Dict[str, Any]]):
    monkeypatch.setattr(expert_tools, "_fetch_team_picks",
                        lambda eid, gw: picks_by_entry[eid])


def test_summary_never_prices_an_unresolvable_player_at_zero(
    monkeypatch, elements,
) -> None:
    """Verbatim the bug the neighbouring docstring claimed to have fixed.

    ``player.get("now_cost", 0) / 10.0`` printed £0.0m -- a free player -- for
    an id the bootstrap table does not know.
    """
    a = ELITE_NAMED[0]
    monkeypatch.setattr(expert_tools, "_fetch_team_picks",
                        lambda eid, gw: _picks(1, 999))
    out = expert_tools.get_expert_teams_summary(gw=5, experts=[a.name])
    assert "0.0" not in out
    assert "unknown player (element 999)" in out


def test_summary_keeps_an_unresolvable_player_in_the_cross_tab(
    monkeypatch, elements,
) -> None:
    """``if pid not in elements_df.index: continue`` dropped it silently.

    An ownership cross-tab that quietly loses a row understates every count
    derived from it, with nothing on the page to say so.
    """
    a = ELITE_NAMED[0]
    monkeypatch.setattr(expert_tools, "_fetch_team_picks",
                        lambda eid, gw: _picks(1, 999))
    out = expert_tools.get_expert_teams_summary(gw=5, experts=[a.name])
    body = out.split("Note:")[0]
    # Two picks in, two rows out.
    assert "Erling Haaland" in body
    assert "unknown player (element 999)" in body
    assert "999" in out.split("Note:")[1]


def test_summary_reports_a_manager_whose_picks_could_not_be_fetched(
    monkeypatch, elements,
) -> None:
    """A failed fetch changes the denominator, so it cannot be swallowed."""
    a, b = ELITE_NAMED[0], ELITE_NAMED[1]

    def _fetch(eid, gw):
        if eid == b.entry_id:
            raise RuntimeError("503 from the FPL API")
        return _picks(1)

    monkeypatch.setattr(expert_tools, "_fetch_team_picks", _fetch)
    out = expert_tools.get_expert_teams_summary(gw=5, experts=[a.name, b.name])
    assert "could not be fetched" in out
    assert b.name in out
    assert "503 from the FPL API" in out


def test_summary_defaults_to_the_verified_curated_list(monkeypatch, elements) -> None:
    """With no names given, the managers queried are the verified ones."""
    seen: List[int] = []

    def _fetch(eid, gw):
        seen.append(eid)
        return _picks(1)

    monkeypatch.setattr(expert_tools, "_fetch_team_picks", _fetch)
    expert_tools.get_expert_teams_summary(gw=5)
    assert seen == [int(e.entry_id) for e in ELITE_NAMED]


# -- identity: no unverified name may reach an answer -------------------------


def test_no_stale_seed_name_can_reach_an_answer(monkeypatch, elements) -> None:
    """THE test. Twenty invented identities, three tools, zero fabricated answers.

    Every name in the stale seed map is walked through all three tools. No
    tool may fetch the rotted id, and no tool may print it. Two of the twenty
    names ("Ben Crellin", "BigMan Bakar") also appear on the curated list with
    a DIFFERENT, verified id -- those resolve, to the verified id; every other
    name must be refused outright, with nothing fetched.
    """
    fetched: List[int] = []

    def _record(eid, *a, **k):
        fetched.append(int(eid))
        return {}

    monkeypatch.setattr(expert_tools, "_fetch_team_picks", _record)
    monkeypatch.setattr(expert_tools, "_fetch_transfers", _record)
    monkeypatch.setattr(expert_tools, "_fetch_manager_history", _record)

    assert len(EXPERT_SEEDS) == 20  # the map itself must not quietly shrink
    verified_ids = {int(e.entry_id) for e in ELITE_NAMED}
    refused = 0
    for name, stale_id in EXPERT_SEEDS.items():
        fetched.clear()
        outs = [
            expert_tools.get_expert_transfers(name),
            expert_tools.get_manager_history(name),
            expert_tools.get_expert_teams_summary(gw=5, experts=[name]),
        ]
        # The rotted pairing never reaches the page, whatever else happens.
        for out in outs:
            assert str(stale_id) not in out, (name, stale_id, out)
        # ...nor the wire.
        assert stale_id not in fetched, (name, stale_id, fetched)
        assert set(fetched) <= verified_ids, (name, fetched)
        if any(_norm_name(name) == _norm_name(e.name) for e in ELITE_NAMED):
            assert fetched, f"{name} is verified and should still answer"
            continue
        refused += 1
        for out in outs:
            assert "cannot verify" in out, (name, out)
        assert not fetched, (name, fetched)
    assert refused == 18  # 20 seeds, 2 of them independently re-verified


def _norm_name(s: str) -> str:
    from fpl_edge.ingest.rivals.names import norm

    return norm(s)


def test_expert_tools_ships_no_name_to_id_map() -> None:
    """The mechanism, not just its symptom: no such literal may come back.

    Checked structurally rather than by name, so renaming ``EXPERTS`` does not
    slip a new one past this file.
    """
    assert not hasattr(expert_tools, "EXPERTS")
    for attr, value in vars(expert_tools).items():
        if attr.startswith("__") or not isinstance(value, dict) or not value:
            continue
        if all(isinstance(k, str) and isinstance(v, int) for k, v in value.items()):
            pytest.fail(
                f"expert_tools.{attr} is a name-to-id map: {value!r}. Entry ids "
                "rot every season; resolve names through a verifying source."
            )
    # And the stale ids are not sitting in the source text either.
    src = Path(expert_tools.__file__).read_text(encoding="utf-8")
    for name, stale_id in EXPERT_SEEDS.items():
        assert f'"{name}": {stale_id}' not in src
        assert f"'{name}': {stale_id}" not in src


def test_a_verified_curated_name_still_works(monkeypatch, elements) -> None:
    """Degrading honestly is not the same as breaking: verified names answer."""
    seen: List[int] = []

    def _fetch(eid):
        seen.append(eid)
        return {"past": [{"season_name": "2025/26", "rank": 1234,
                          "total_points": 2500}],
                "chips": [], "current": []}

    monkeypatch.setattr(expert_tools, "_fetch_manager_history", _fetch)
    out = expert_tools.get_manager_history(VERIFIED.name)
    assert seen == [int(VERIFIED.entry_id)]
    assert VERIFIED.name in out
    assert str(VERIFIED.entry_id) in out
    assert "curated elite list" in out  # the provenance is on the page
    assert "2025/26" in out


def test_a_crawled_name_resolves_to_the_id_the_crawl_read_it_from(
    monkeypatch, elements, crawled,
) -> None:
    """dim_manager is a verifying source: the name came back FROM the API."""
    seen: List[int] = []

    def _fetch(eid):
        seen.append(eid)
        return {"past": [], "chips": [], "current": []}

    monkeypatch.setattr(expert_tools, "_fetch_manager_history", _fetch)
    out = expert_tools.get_manager_history("Levi Longworth")
    assert seen == [424242]
    assert "crawled from the API" in out


def test_a_numeric_id_is_queried_but_not_given_a_name(monkeypatch, elements) -> None:
    """The caller's own number is honoured; the account holder is not invented."""
    seen: List[int] = []

    def _fetch(eid):
        seen.append(eid)
        return {"past": [], "chips": [], "current": []}

    monkeypatch.setattr(expert_tools, "_fetch_manager_history", _fetch)
    out = expert_tools.get_manager_history("135")
    assert seen == [135]
    assert "entry 135" in out
    assert "not verified" in out
    assert "Holly Shand" not in out  # the old map's claim about 135


def test_a_numeric_id_the_crawl_knows_is_named_from_the_crawl(
    monkeypatch, elements, crawled,
) -> None:
    monkeypatch.setattr(expert_tools, "_fetch_manager_history",
                        lambda eid: {"past": [], "chips": [], "current": []})
    out = expert_tools.get_manager_history("424242")
    assert "Levi Longworth" in out
    assert "read back from the API" in out


def test_an_ambiguous_name_asks_rather_than_picking_one(
    monkeypatch, elements,
) -> None:
    """Two verified people, one query: the tool asks instead of guessing."""
    df = pd.DataFrame([
        {"entry_id": 11, "player_name": "Ben Crellin", "entry_name": "A",
         "source": "top1k:2026-27:gw1"},
        {"entry_id": 22, "player_name": "Ben Crellinson", "entry_name": "B",
         "source": "top1k:2026-27:gw1"},
    ])
    monkeypatch.setattr(expert_tools, "_crawled_managers", lambda: df.copy())

    def _boom(eid):  # pragma: no cover - only fires on regression
        raise AssertionError("must not fetch while the name is ambiguous")

    monkeypatch.setattr(expert_tools, "_fetch_manager_history", _boom)
    out = expert_tools.get_manager_history("Ben Crellin")
    assert "say which one you mean" in out
    assert "entry 11" in out and "entry 22" in out


def test_resolution_is_read_only_and_never_opens_the_warehouse_for_writing(
    monkeypatch,
) -> None:
    """DuckDB is single-writer: identity lookup takes a read copy or nothing."""
    src = Path(expert_tools.__file__).read_text(encoding="utf-8")
    assert "_sem._read()" in src
    assert "read_only=False" not in src
    assert "_edge._open(" not in src


@pytest.mark.parametrize("junk", ["", "  ", "ab", "0", "-4"])
def test_resolution_refuses_junk_without_fetching(monkeypatch, junk) -> None:
    manager, why = expert_tools._resolve_manager(junk)
    assert manager is None
    assert why
