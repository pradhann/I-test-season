"""The player dossier, pinned before the module is split into three.

``fpl_edge/interfaces/dossier.py`` is 1,674 lines with no test file naming it.
ARCHITECTURE_REVIEW.md Section 4 row 18 splits it into ``dossier/load.py``,
``dossier/sections.py`` and ``dossier/render.py``, which moves five loaders and
sixteen section builders across a module boundary. These tests pin the contract
that split can break without raising: one section per key in ``EXPECTED``, in
that order, each carrying either a body or a reason, and a reason that is never
blank.

That contract is BRIEF_SHARED.md rule 9 in test form. Absence is shown as
absence, with the reason.

The warehouse is the committed synthetic league under
``fpl_edge/models/team_goals/``, seeded into ``tmp_path`` and given four
players. It is used rather than a hand-built seed because the Dixon-Coles fit
inside ``_load_fixtures`` needs three promotion events in the training window,
and the synthetic league is the only offline source of those. No network, no
read of ``data/warehouse/fpl.duckdb``, and ``projection_path`` points at a
missing tmp file so the committed ``gw1_projection.parquet`` is never read.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.interfaces import dossier as dmod
from fpl_edge.models.team_goals.data import InsufficientHistoryError
from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.synthetic import build_warehouse, load_league
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC

#: The synthetic league's current season, matching test_fixture_difficulty.py.
SEASON = "2025-26"

#: Mid-season: completed matches behind the snapshot, fixtures ahead of it.
AS_OF = dt.datetime(2025, 12, 4, 10, 0, tzinfo=UTC)

T0 = pd.Timestamp("2025-08-01 12:00", tz="UTC")

#: (code, web_name, first_name, second_name, position, price_tenths, own_pct).
#: The team code is filled from the synthetic league's own club list.
PLAYERS = [
    (100, "Raya", "David", "Raya", 1, 55, 20.0),
    (200, "Gabriel", "Gabriel", "Magalhaes", 2, 60, 30.0),
    (300, "Palmer", "Cole", "Palmer", 3, 105, 45.0),
    (400, "Jackson", "Nicolas", "Jackson", 4, 75, 12.0),
]


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("dossier") / "fpl.duckdb"
    wh = build_warehouse(load_league(FIXTURES_DIR), path)
    # The season is a module constant, not caller input, so it is inlined
    # rather than bound. A bound placeholder would trip the prose gate, which
    # reads a question mark followed by a space as a rhetorical question.
    clubs = wh.sql(
        f"SELECT DISTINCT team_code FROM dim_team WHERE season = '{SEASON}' "
        f"ORDER BY 1")["team_code"].tolist()
    # Two clubs, two players each, so the identity and fixtures sections have
    # a real opponent to name.
    team_of = {100: clubs[0], 200: clubs[0], 300: clubs[1], 400: clubs[1]}
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": code, "web_name": web,
         "first_name": first, "second_name": second, "position": pos,
         "team_code": team_of[code], "as_of": T0}
        for code, web, first, second, pos, _, _ in PLAYERS
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": code,
         "price_tenths": price, "selected_by_pct": own, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": T0}
        for code, _, _, _, _, price, own in PLAYERS
    ]))
    wh.close()
    return path


@pytest.fixture(scope="module")
def built(seeded_db, tmp_path_factory):
    """One dossier for Palmer, reused by the pins below."""
    missing = tmp_path_factory.mktemp("dossier_proj") / "absent.parquet"
    with Warehouse.read_copy(seeded_db) as wh:
        doss, clarification = dmod.build(
            wh, "Palmer", season=SEASON, as_of=AS_OF, projection_path=missing)
    assert clarification is None
    assert doss is not None
    return doss


# ---------------------------------------------------------------------------
# the section contract
# ---------------------------------------------------------------------------


def test_the_expected_keys_are_the_sixteen_the_split_has_to_preserve():
    assert tuple(dmod.EXPECTED) == (
        "identity", "price", "ownership", "projection", "minutes", "fixtures",
        "rates", "set_pieces", "defensive", "odds", "availability", "tactical",
        "press", "creators", "elite", "disagreement",
    )


def test_every_expected_key_yields_exactly_one_section_in_render_order(built):
    """One section per builder, keyed and ordered by ``EXPECTED``. A split
    that loses a builder shortens this tuple and nothing else would notice."""
    assert tuple(s.key for s in built.sections) == tuple(dmod.EXPECTED)
    assert len(built.sections) == 16


def test_every_section_carries_a_body_or_a_gap_and_never_both(built):
    for s in built.sections:
        assert (s.body is None) != (s.gap is None), s.key
        assert s.present is (s.body is not None)


def test_every_gap_names_a_reason_that_is_not_blank(built):
    """BRIEF_SHARED.md rule 9: absence is shown as absence, with the reason.
    An empty string renders as a silent omission."""
    gaps = [s for s in built.sections if s.gap is not None]
    assert gaps, "the partial seed must produce at least one gap"
    for s in gaps:
        assert s.gap.strip(), f"section {s.key} has an empty gap reason"
        assert len(s.gap.strip()) > 10, s.key


def test_the_seeded_warehouse_splits_the_sections_seven_present_nine_gap(built):
    """The exact partition on this seed, pinned so a split cannot turn a body
    into a gap or the reverse and still pass the shape tests above."""
    present = tuple(s.key for s in built.sections if s.present)
    gapped = tuple(s.key for s in built.sections if not s.present)
    assert present == ("identity", "price", "ownership", "minutes",
                       "fixtures", "defensive", "availability")
    assert gapped == ("projection", "rates", "set_pieces", "odds", "tactical",
                      "press", "creators", "elite", "disagreement")


def test_every_section_title_comes_from_the_expected_map(built):
    for s in built.sections:
        assert s.title == dmod.EXPECTED[s.key]


def test_no_section_gap_is_a_swallowed_exception(built):
    """build:1509-1512 turns a raising builder into a gap that starts with
    "section raised". That is the right degradation and the wrong result: on
    this seed every builder must complete on its own terms."""
    raised = [s.key for s in built.sections
              if s.gap and s.gap.startswith("section raised")]
    assert raised == []


def test_a_section_with_neither_body_nor_gap_cannot_be_constructed():
    with pytest.raises(ValueError):
        dmod.Section(key="identity", title="t")
    with pytest.raises(ValueError):
        dmod.Section(key="identity", title="t", body="b", gap="g")


# ---------------------------------------------------------------------------
# the dossier envelope
# ---------------------------------------------------------------------------


def test_the_dossier_carries_the_identity_it_resolved(built):
    assert built.query == "Palmer"
    assert built.code == 300
    assert built.name == "Palmer"
    assert built.full_name == "Palmer (Cole Palmer)"
    assert built.season == SEASON
    assert built.as_of == AS_OF
    assert built.build_ms > 0.0


def test_the_gameweek_defaults_to_the_next_one_in_the_snapshot(built):
    assert built.gw == 18


def test_identity_is_present_because_the_player_list_is_seeded(built):
    identity = next(s for s in built.sections if s.key == "identity")
    assert identity.body is not None
    assert "Palmer" in identity.body


def test_an_unknown_name_returns_a_clarification_rather_than_a_dossier(
        seeded_db, tmp_path):
    with Warehouse.read_copy(seeded_db) as wh:
        doss, clarification = dmod.build(
            wh, "Zzzzqqq", season=SEASON, as_of=AS_OF,
            projection_path=tmp_path / "absent.parquet")
    assert doss is None
    assert clarification is not None
    assert clarification.kind in ("not_found", "ambiguous")
    assert clarification.question.strip()


def test_an_empty_warehouse_refuses_with_no_universe(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    with Warehouse.read_copy(path) as wh:
        doss, clarification = dmod.build(
            wh, "Palmer", season=SEASON, as_of=AS_OF,
            projection_path=tmp_path / "absent.parquet")
    assert doss is None
    assert clarification is not None
    assert clarification.kind == "no_universe"


# ---------------------------------------------------------------------------
# current behaviour that the split must not change, bug and all
# ---------------------------------------------------------------------------


def test_a_warehouse_with_too_little_history_loses_the_whole_dossier(tmp_path):
    """Pinned as current behaviour, not as desired behaviour.

    ``_load_fixtures`` at dossier.py:348-357 catches ValueError and KeyError.
    ``InsufficientHistoryError`` subclasses RuntimeError (team_goals/data.py:33)
    so it escapes ``build`` and the caller gets a traceback rather than a
    ``fixtures`` gap with a reason. Every other loader degrades to a reason;
    this one does not.
    """
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": T0},
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-22 17:30", tz="UTC"),
         "as_of": T0},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": 300, "element_id": 300,
         "web_name": "Palmer", "first_name": "Cole", "second_name": "Palmer",
         "position": 3, "team_code": 1, "as_of": T0},
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": 300, "element_id": 300,
         "price_tenths": 105, "selected_by_pct": 45.0, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": T0},
    ]))
    wh.close()
    with Warehouse.read_copy(path) as reader:
        with pytest.raises(InsufficientHistoryError):
            dmod.build(reader, "Palmer", season=SEASON, as_of=AS_OF,
                       projection_path=tmp_path / "absent.parquet")
