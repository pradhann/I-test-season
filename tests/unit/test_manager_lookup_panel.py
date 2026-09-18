"""The ``manager_lookup`` panel: identity, and the twenty names it refuses.

Two of these tests moved here from ``tests/unit/test_mcp_team_and_expert_tools.py``
when tools 28, 32 and 33 collapsed into this one panel. They are the reason the
panel exists in the shape it does, and they must stay green here.

``test_no_stale_seed_name_can_reach_an_answer``
    Twenty invented identities, one panel, zero fabricated answers. FPL entry
    ids are assigned per season in registration order, so a curated map rots
    every August and rots silently: a stale id resolves to a different real
    person rather than to a 404. All twenty ids in ``EXPERT_SEEDS`` were
    checked against the live API on 2026-08-24 and every one now belongs to
    somebody else. No stale id may reach a payload, by value or by lookup.

``test_the_panel_ships_no_name_to_id_map``
    The mechanism rather than its symptom, checked structurally so renaming a
    constant does not slip a new map past this file.

The panel reads the warehouse only. These tests seed ``dim_manager`` and the
manager fact tables in ``tmp_path`` and never touch the live file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.rivals.elite import ELITE_NAMED
from fpl_edge.ingest.rivals.names import norm
from fpl_edge.ingest.rivals.roster import EXPERT_SEEDS
from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
from fpl_edge.platform.registry import ParamsInvalid, registered, run_script
from fpl_edge.platform.scripts import manager_lookup as panel
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
T0 = pd.Timestamp("2026-08-01 12:00", tz="UTC")

#: A manager the engine has actually verified, taken from the curated list
#: rather than written down here, so this file cannot become a twenty-first
#: unverified name-to-id pair of its own.
VERIFIED = ELITE_NAMED[0]

#: A crawled entry nobody curated: the panel should find it by the name the
#: API reported, and that name is invented here rather than borrowed.
CRAWLED_ID = 987654
CRAWLED_NAME = "Wilhelmina Quaintly"


def _seed(tmp_path, *, with_managers=True):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    if with_managers:
        wh.append("dim_manager", pd.DataFrame([
            {"entry_id": int(VERIFIED.entry_id), "player_name": VERIFIED.name,
             "entry_name": "The Curated XI", "region": "England",
             "years_active": 9, "source": "elite_named", "as_of": T0},
            {"entry_id": CRAWLED_ID, "player_name": CRAWLED_NAME,
             "entry_name": "Quaintly FC", "region": "Wales",
             "years_active": 3, "source": "top10k_sample", "as_of": T0},
        ]))
        wh.append("fact_manager_gw", pd.DataFrame([
            {"entry_id": int(VERIFIED.entry_id), "season": SEASON, "gw": 4,
             "points": 61, "total_points": 236, "overall_rank": 142520,
             "bank_tenths": 5, "value_tenths": 1012, "event_transfers": 1,
             "event_transfers_cost": 0, "points_on_bench": 3, "as_of": T0},
        ]))
        wh.append("fact_manager_season", pd.DataFrame([
            {"entry_id": int(VERIFIED.entry_id), "season": "2025-26",
             "total_points": 2504, "overall_rank": 1211,
             "rank_percentage": 0.01, "rank_percentage_text": "top 1%",
             "as_of": T0},
        ]))
    wh.close()
    return path


@pytest.fixture()
def db(tmp_path):
    return _seed(tmp_path)


def test_the_panel_is_registered():
    assert "manager_lookup" in registered()


# ---------------------------------------------------------------------------
# the two assertions that moved here from the expert-tools test
# ---------------------------------------------------------------------------


def test_no_stale_seed_name_can_reach_an_answer(db):
    """THE test. Twenty invented identities, zero fabricated answers.

    Two of the twenty names also appear on the curated list with a DIFFERENT,
    verified id. Those resolve, to the verified id. Every other name is
    refused outright, and no rotted id appears anywhere in the payload.
    """
    assert len(EXPERT_SEEDS) == 20  # the map itself must not shrink unseen
    verified_ids = {int(e.entry_id) for e in ELITE_NAMED}
    verified_names = {norm(e.name) for e in ELITE_NAMED}
    refused = 0
    for name, stale_id in EXPERT_SEEDS.items():
        result = run_script(
            "manager_lookup", {"manager": name, "season": SEASON}, db=db,
        ).result
        blob = repr(result)
        assert str(stale_id) not in blob, (name, stale_id, blob)
        if result["manager"] is not None:
            assert int(result["manager"]["entry_id"]) in verified_ids
            assert norm(name) in verified_names
            continue
        refused += 1
        assert result["refusal"]["kind"] in {"unverified", "stale_seed"}
        assert "no verified source" in result["refusal"]["reason"]
    assert refused >= 18, refused


def test_the_panel_ships_no_name_to_id_map():
    """The mechanism, not just its symptom: no such literal may come back.

    Checked structurally rather than by name, so renaming a constant does not
    slip a new map past this file.
    """
    for attr, value in vars(panel).items():
        if attr.startswith("__") or not isinstance(value, dict) or not value:
            continue
        if all(isinstance(k, str) and isinstance(v, int) for k, v in value.items()):
            pytest.fail(
                f"manager_lookup.{attr} is a name-to-id map: {value!r}. Entry "
                "ids rot every season; resolve names through a verifying "
                "source."
            )
    source = Path(panel.__file__).read_text(encoding="utf-8")
    for name, stale_id in EXPERT_SEEDS.items():
        assert f'"{name}": {stale_id}' not in source
        assert f"'{name}': {stale_id}" not in source


# ---------------------------------------------------------------------------
# what the panel does answer
# ---------------------------------------------------------------------------


def test_a_verified_curated_name_resolves_with_its_provenance(db):
    result = run_script(
        "manager_lookup", {"manager": VERIFIED.name, "season": SEASON}, db=db,
    ).result
    assert result["manager"]["entry_id"] == int(VERIFIED.entry_id)
    assert result["manager"]["verified"] is True
    assert "curated elite list" in result["manager"]["origin"]
    assert result["refusal"] is None
    assert result["season_record"]["total_points"] == 236
    assert result["past_record"][0]["season"] == "2025-26"


def test_a_crawled_name_resolves_through_dim_manager(db):
    result = run_script(
        "manager_lookup", {"manager": "Quaintly", "season": SEASON}, db=db,
    ).result
    assert result["manager"]["entry_id"] == CRAWLED_ID
    assert "crawled from the API" in result["manager"]["origin"]


def test_a_bare_number_is_read_as_given_and_marked_unverified(db):
    result = run_script(
        "manager_lookup", {"manager": "31337", "season": SEASON}, db=db,
    ).result
    assert result["manager"]["entry_id"] == 31337
    assert result["manager"]["player_name"] is None
    assert result["manager"]["verified"] is False
    assert "not verified" in result["manager"]["origin"]


def test_a_number_the_crawl_has_read_carries_the_name_it_read(db):
    result = run_script(
        "manager_lookup", {"manager": str(CRAWLED_ID), "season": SEASON}, db=db,
    ).result
    assert result["manager"]["player_name"] == CRAWLED_NAME
    assert result["manager"]["verified"] is True


def test_a_two_character_query_is_refused_rather_than_matched(db):
    result = run_script("manager_lookup", {"manager": "qu"}, db=db).result
    assert result["refusal"]["kind"] == "too_short"


def test_an_empty_transfer_list_names_the_crawl_rather_than_the_manager(db):
    """The real loss from dropping the live API, said out loud."""
    result = run_script(
        "manager_lookup", {"manager": VERIFIED.name, "season": SEASON}, db=db,
    ).result
    assert result["transfers"] == []
    assert "the crawl holds no" in result["transfers_reason"]
    assert "not that no transfer was made" in result["transfers_reason"]


def test_a_warehouse_with_no_crawl_still_refuses_rather_than_guesses(tmp_path):
    db = _seed(tmp_path, with_managers=False)
    result = run_script("manager_lookup", {"manager": "Quaintly"}, db=db).result
    assert result["manager"] is None
    assert result["refusal"]["kind"] == "unverified"
    assert "holds no crawled manager" in result["refusal"]["reason"]


def test_the_params_schema_rejects_an_entry_id(db):
    """entry_id is never an input. A caller passes the name or the number."""
    with pytest.raises(ParamsInvalid):
        run_script("manager_lookup", {"manager": "Quaintly",
                                      "entry_id": 4490171}, db=db)
