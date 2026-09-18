"""The ``player_dossier`` panel: the section contract, as a result schema.

``tests/unit/test_dossier.py`` pins the builder. This file pins the panel that
wraps it, which is a different contract and can break without the builder
changing: the result schema has to admit every shape the sixteen builders
produce, or a working dossier becomes a ``ResultInvalid`` at the deadline.

Three assertions carry the file.

``test_every_expected_section_survives_the_schema`` is the whole point. Sixteen
keys, in ``EXPECTED`` order, each with a body or a gap, through
``validate_result``. A schema too tight fails here rather than in front of a
reader.

``test_a_section_that_is_absent_carries_its_reason`` is rule 9 at the panel
boundary: a gap travels as a gap, never as a missing key or an empty string.

``test_simulate_is_not_a_parameter_and_the_payload_says_so`` pins the budget
decision. The live refit took about 95 seconds against a 10 second budget, so
the panel does not offer it and says where the projection came from instead.

The warehouse is the committed synthetic league, seeded into ``tmp_path``, for
the reason ``test_dossier.py`` gives: the Dixon-Coles fit inside the fixtures
loader needs three promotion events in the training window and the synthetic
league is the only offline source of those. No network, and no read of the
live warehouse.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.interfaces.dossier import EXPECTED
from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.synthetic import build_warehouse, load_league
from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
from fpl_edge.platform.registry import (
    ParamsInvalid,
    registered,
    run_script,
    script,
    validate_result,
)

UTC = dt.UTC

#: The synthetic league's current season, matching test_dossier.py.
SEASON = "2025-26"

#: Mid-season: completed matches behind the instant, fixtures ahead of it.
AS_OF = "2025-12-04T10:00:00Z"

T0 = pd.Timestamp("2025-08-01 12:00", tz="UTC")

#: (code, web_name, first_name, second_name, position, price_tenths, own_pct)
PLAYERS = [
    (100, "Raya", "David", "Raya", 1, 55, 20.0),
    (200, "Gabriel", "Gabriel", "Magalhaes", 2, 60, 30.0),
    (300, "Palmer", "Cole", "Palmer", 3, 105, 45.0),
    (400, "Jackson", "Nicolas", "Jackson", 4, 75, 12.0),
]


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("dossier_panel") / "fpl.duckdb"
    wh = build_warehouse(load_league(FIXTURES_DIR), path)
    # The season is a module constant, not caller input, so it is inlined
    # rather than bound: a bound placeholder would trip the house prose gate,
    # which reads a question mark followed by a space as a rhetorical question.
    clubs = wh.sql(
        f"SELECT DISTINCT team_code FROM dim_team WHERE season = '{SEASON}' "
        f"ORDER BY 1")["team_code"].tolist()
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
def built(db):
    """One dossier for Palmer, run through the registry, reused below."""
    run = run_script(
        "player_dossier",
        {"code": 300, "season": SEASON, "as_of": AS_OF},
        db=db,
    )
    assert "empty" not in run.result, run.result
    return run


def test_the_panel_is_registered():
    assert "player_dossier" in registered()


def test_every_expected_section_survives_the_schema(built):
    """Sixteen sections, in EXPECTED order, admitted by the result schema.

    ``run_script`` already validated this payload, so reaching this assertion
    is most of the test. The explicit revalidation is here so a failure names
    the schema rather than the run.
    """
    result = built.result
    assert [s["key"] for s in result["sections"]] == list(EXPECTED)
    assert len(result["sections"]) == 16
    validate_result(script("player_dossier"), result)


def test_a_section_carries_a_body_or_a_gap_and_never_both(built):
    for section in built.result["sections"]:
        has_body = section["body"] is not None
        has_gap = section["gap"] is not None
        assert has_body != has_gap, section
        if has_gap:
            assert section["gap"].strip(), section


def test_a_section_that_is_absent_carries_its_reason(built):
    """Rule 9 at the panel boundary. A gap key is listed and has a reason."""
    result = built.result
    gap_keys = {s["key"] for s in result["sections"] if s["body"] is None}
    assert set(result["gaps"]) == gap_keys
    for key in result["gaps"]:
        section = next(s for s in result["sections"] if s["key"] == key)
        assert section["gap"]


def test_the_title_of_every_section_is_the_expected_description(built):
    for section in built.result["sections"]:
        assert section["title"] == EXPECTED[section["key"]]


def test_the_code_asked_for_is_the_code_answered(built):
    assert built.result["code"] == 300
    assert built.result["web_name"] == "Palmer"


def test_simulate_is_not_a_parameter_and_the_payload_says_so(built, db):
    """The budget decision, pinned in both directions."""
    assert built.result["simulate"] is False
    assert "95 seconds" in built.result["simulate_note"]
    with pytest.raises(ParamsInvalid):
        run_script(
            "player_dossier",
            {"code": 300, "season": SEASON, "simulate": True},
            db=db,
        )


def test_the_params_schema_rejects_an_entry_id(db):
    with pytest.raises(ParamsInvalid):
        run_script(
            "player_dossier",
            {"code": 300, "season": SEASON, "entry_id": 4490171},
            db=db,
        )


def test_an_unknown_code_is_an_empty_that_names_the_code(db):
    result = run_script(
        "player_dossier", {"code": 999999, "season": SEASON}, db=db,
    ).result
    assert result["empty"] is True
    assert "999999" in result["reason"]
    assert "element id" in result["reason"]


def test_provenance_carries_the_as_of_the_dossier_was_built_at(built):
    assert built.provenance["script"] == "player_dossier"
    assert built.provenance["as_of"] == built.result["as_of"]
