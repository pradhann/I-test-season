"""projection_table's two modes, pinned before ``_gw_mode`` is decomposed.

``fpl_edge/platform/scripts/projections.py`` is 1,574 lines and the mode branch
at :842-986 is the largest untested branch in ARCHITECTURE_REVIEW.md Section 4.
Row 27 decomposes ``_gw_mode`` into seven functions inside the module; row 28
then splits the module into ``projections/``. ``tests/unit/
test_projection_panel.py`` covers consensus arithmetic, spread and the filters.
What it does not pin is the two result schemas themselves: which of
``_ARTEFACT_RESULT`` and ``_GW_RESULT`` each call validates against, and the
``mode`` field that tells them apart.

These tests pin current behaviour at 2026-09-17. Every result is validated
against its schema directly rather than only through ``run_script``, because
``RESULT`` is ``oneOf`` the two and a payload that drifted from one into the
other would still pass the registry's check.

The warehouse is seeded into ``tmp_path``, with the artefact written beside it.
No network, no read of ``data/warehouse/fpl.duckdb``.
"""

from __future__ import annotations

import datetime as dt

import jsonschema
import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registration is the import)
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.platform.registry import run_script
from fpl_edge.platform.scripts.common import PROJECTION_NAME
from fpl_edge.platform.scripts.projections import (
    _ARTEFACT_RESULT,
    _GW_RESULT,
    RESULT,
)
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
T0 = pd.Timestamp("2026-08-01 12:00", tz="UTC")

#: (code, web_name, position, team_code, price_tenths, selected_by_pct)
PLAYERS = [
    (100, "Raya", 1, 1, 55, 20.0),
    (200, "Gabriel", 2, 1, 60, 30.0),
    (300, "Palmer", 3, 2, 105, 45.0),
    (400, "Jackson", 4, 2, 75, 12.0),
]

#: provider -> code -> GW2 xp. Three providers, so consensus mode has several
#: sources to average and single-source mode has one to name.
GW2_XP = {
    "src_a": {100: 3.0, 200: 4.0, 300: 2.0, 400: 5.0},
    "src_b": {100: 3.2, 200: 4.4, 300: 4.0, 400: 5.5},
    "src_c": {100: 3.4, 200: 4.8, 300: 8.0, 400: 6.0},
}


def _validate(payload, schema, label):
    errors = sorted(
        jsonschema.Draft202012Validator(schema).iter_errors(payload),
        key=lambda e: list(e.path))
    assert not errors, (
        f"{label}: " + "; ".join(f"{list(e.path)}: {e.message}" for e in errors))


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": T0},
        {"season": SEASON, "team_code": 2, "team_id": 2, "name": "Chelsea",
         "short_name": "CHE", "as_of": T0},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": code, "web_name": name,
         "first_name": "F", "second_name": name, "position": pos,
         "team_code": team, "as_of": T0}
        for code, name, pos, team, _, _ in PLAYERS
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": code, "element_id": code,
         "price_tenths": price, "selected_by_pct": own, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": T0}
        for code, _, _, _, price, own in PLAYERS
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": True,
         "deadline_utc": pd.Timestamp("2026-08-15 17:30", tz="UTC"),
         "as_of": T0},
        {"season": SEASON, "gw": 2, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-22 17:30", tz="UTC"),
         "as_of": T0},
    ]))
    store = ProjectionStore(wh)
    rows = []
    for provider, per_code in GW2_XP.items():
        for code, xp in per_code.items():
            rows.append({
                "provider": provider, "season": SEASON, "gw": 2, "code": code,
                "xp": xp, "xp_if_appears": None,
                "p_appear": 0.9 if provider == "src_a" else None,
                "xmins": 85.0 if provider == "src_a" else None,
                "as_of": T0,
            })
    frame = pd.DataFrame(rows)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    store.append("fact_projection", frame)
    wh.close()

    # The solved artefact the default (no-gw) call reads, beside the db.
    pd.DataFrame({
        "code": [code for code, *_ in PLAYERS],
        "xpts": [3.1, 4.2, 6.5, 5.4],
        "p_haul": [0.02, 0.06, 0.24, 0.18],
        "p10": [1.0, 1.0, 2.0, 1.0],
        "p90": [6.0, 8.0, 14.0, 12.0],
    }).to_parquet(tmp_path / PROJECTION_NAME, index=False)
    return path


# ---------------------------------------------------------------------------
# which schema each mode answers to
# ---------------------------------------------------------------------------


def test_the_two_result_schemas_are_the_only_two_the_panel_can_serve():
    assert RESULT["oneOf"] == [_ARTEFACT_RESULT, _GW_RESULT]
    assert _ARTEFACT_RESULT["additionalProperties"] is False
    assert _GW_RESULT["additionalProperties"] is False
    assert "mode" not in _ARTEFACT_RESULT["properties"]
    assert _GW_RESULT["properties"]["mode"]["enum"] == ["consensus", "source"]


def test_a_call_with_no_gameweek_parameter_validates_as_the_artefact_result(db):
    res = run_script("projection_table", {"limit": 10}, db=db).result
    _validate(res, _ARTEFACT_RESULT, "artefact mode")
    assert "mode" not in res
    assert res["row_count"] == 4
    assert set(_ARTEFACT_RESULT["required"]) <= set(res)


def test_a_call_naming_a_gameweek_validates_as_the_gameweek_result(db):
    res = run_script("projection_table", {"gw": 2, "limit": 10}, db=db).result
    _validate(res, _GW_RESULT, "gameweek mode")
    assert res["gw"] == 2
    assert set(_GW_RESULT["required"]) <= set(res)


def test_the_artefact_payload_does_not_also_validate_as_a_gameweek_payload(db):
    """The two schemas are a ``oneOf``, so exactly one must match. If both
    matched, the registry's own check would pass a payload that has drifted
    from one mode into the other."""
    res = run_script("projection_table", {"limit": 10}, db=db).result
    matcher = jsonschema.Draft202012Validator(_GW_RESULT)
    assert list(matcher.iter_errors(res)), "artefact payload must fail _GW_RESULT"


# ---------------------------------------------------------------------------
# the mode field
# ---------------------------------------------------------------------------


def test_mode_is_consensus_when_several_sources_cover_the_gameweek(db):
    res = run_script("projection_table", {"gw": 2, "limit": 10}, db=db).result
    assert res["mode"] == "consensus"
    assert res["source"] is None, "consensus mode names no single vendor"
    assert sorted(res["active_sources"]) == ["src_a", "src_b", "src_c"]
    assert sorted(res["sources"]) == ["src_a", "src_b", "src_c"]


def test_mode_is_source_when_one_vendor_is_named(db):
    res = run_script("projection_table",
                     {"gw": 2, "source": "src_b", "limit": 10}, db=db).result
    assert res["mode"] == "source"
    assert res["source"] == "src_b"
    assert res["active_sources"] == ["src_b"]


def test_a_one_name_subset_behaves_as_a_single_source_not_a_consensus(db):
    """The ``sources`` list is the subset selector. One name in it is one
    vendor, so the mode field has to say ``source``."""
    res = run_script("projection_table",
                     {"gw": 2, "sources": ["src_a"], "limit": 10},
                     db=db).result
    assert res["mode"] == "source"
    assert res["active_sources"] == ["src_a"]


def test_a_two_name_subset_is_a_consensus_over_exactly_those_two(db):
    res = run_script("projection_table",
                     {"gw": 2, "sources": ["src_a", "src_c"], "limit": 10},
                     db=db).result
    assert res["mode"] == "consensus"
    assert res["source"] is None
    assert sorted(res["active_sources"]) == ["src_a", "src_c"]


def test_source_all_is_the_consensus_spelling_not_a_vendor_name(db):
    res = run_script("projection_table",
                     {"gw": 2, "source": "all", "limit": 10}, db=db).result
    assert res["mode"] == "consensus"
    assert res["source"] is None


def test_every_gameweek_mode_entry_point_reaches_the_same_schema(db):
    """Six parameters switch the panel out of artefact mode
    (projections.py:585-586). Each one has to land on ``_GW_RESULT``, or a
    decomposition could route one of them back to the artefact branch."""
    for params in ({"gw": 2}, {"source": "src_a"}, {"team": "ARS"},
                   {"min_p_appear": 0.5}, {"detail_code": 300},
                   {"sources": ["src_a", "src_b"]}):
        res = run_script("projection_table", {**params, "limit": 10},
                         db=db).result
        _validate(res, _GW_RESULT, f"gameweek mode via {sorted(params)}")
        assert res["mode"] in ("consensus", "source")


# ---------------------------------------------------------------------------
# the horizon sum, and the compact shape a capped caller can hold
# ---------------------------------------------------------------------------


def _seed_gw3(db):
    """A second covered gameweek, so a horizon sum has something to add."""
    gw3 = {"src_a": {100: 1.0, 200: 2.0, 300: 3.0, 400: 4.0},
           "src_b": {100: 1.2, 200: 2.2, 300: 3.2, 400: 4.2},
           "src_c": {100: 1.4, 200: 2.4, 300: 3.4, 400: 4.4}}
    rows = [{"provider": p, "season": SEASON, "gw": 3, "code": c, "xp": x,
             "xp_if_appears": None, "p_appear": None, "xmins": None,
             "as_of": T0}
            for p, per in gw3.items() for c, x in per.items()]
    frame = pd.DataFrame(rows)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    with Warehouse(db) as wh:
        ProjectionStore(wh).append("fact_projection", frame)
    return gw3


def test_every_row_carries_the_horizon_sum_the_browser_used_to_compute(db):
    """The Projections tab's `sum` column existed only in the page: xpoints.js
    reduced res.matrix over the selected gameweeks. An API or MCP caller
    asking the same panel the same question got one gameweek and had to
    re-derive the horizon, which is the arithmetic a transfer turns on.

    The sum is over the served `gws` and in the same selection as `xpts`, so
    a row's own cells add to its own total."""
    _seed_gw3(db)
    res = run_script("projection_table", {"gw": 2, "limit": 10}, db=db).result
    _validate(res, _GW_RESULT, "horizon sum")

    assert res["gws"] == [2, 3], "both covered gameweeks are in the window"
    for row in res["rows"]:
        cells = res["matrix"][str(row["code"])]
        assert row["xp_sum"] == pytest.approx(
            round(sum(cells[str(g)] for g in res["gws"]), 3))
        assert row["xp_sum_gws"] == 2
    # and the anchor gameweek alone is still `xpts`, unchanged
    palmer = next(r for r in res["rows"] if r["code"] == 300)
    assert palmer["xpts"] == pytest.approx(
        (2.0 + 4.0 + 8.0) / 3, abs=5e-4)
    assert palmer["xp_sum"] > palmer["xpts"]


def test_a_partial_horizon_sum_counts_the_gameweeks_it_actually_had(db):
    """A player the providers cover for fewer gameweeks than the window holds
    must read a smaller count, not a filled-in total. Only GW2 is seeded
    here, so every sum is one gameweek and says so."""
    res = run_script("projection_table", {"gw": 2, "limit": 10}, db=db).result
    assert res["gws"] == [2]
    for row in res["rows"]:
        assert row["xp_sum_gws"] == 1
        assert row["xp_sum"] == pytest.approx(row["xpts"], abs=5e-4)


def test_compact_drops_the_chart_blocks_and_names_every_one_of_them(db):
    """The MCP projections tool exceeded its caller's payload cap on a normal
    call, so the agent answered from raw SQL instead of from the panel. An
    emptied block would otherwise read as "no data", which is the one thing
    this payload must never say by accident, so the view lists what it left
    out and the rows keep the decision fields."""
    _seed_gw3(db)
    res = run_script("projection_table",
                     {"gw": 2, "limit": 10, "compact": True}, db=db).result
    _validate(res, _GW_RESULT, "compact")

    assert res["compact"] is True
    assert set(res["omitted_blocks"]) == {
        "matrix", "actuals", "by_team", "by_position", "source_meta",
        "accuracy", "weights", "provider_accuracy", "detail"}
    assert res["matrix"] == {} and res["by_team"] == []
    assert any("compact view" in n for n in res["notes"])
    # the numbers a decision needs survive, including the horizon sum the
    # dropped matrix would otherwise have been the only route to
    row = res["rows"][0]
    assert {"code", "name", "xpts", "spread", "p_appear",
            "xp_sum", "xp_sum_gws"} <= set(row)
    assert row["xp_sum"] is not None
    assert "xpts_min" not in row, "compact keeps the decision fields only"


def test_a_code_filter_narrows_the_rows_and_names_what_it_could_not_find(db):
    """A filtered board keeps the full board's scale, coverage and clocks, so
    four rows read exactly as they do on the whole page. A code the board does
    not carry is reported rather than silently dropped."""
    res = run_script("projection_table",
                     {"gw": 2, "limit": 10, "codes": [300, 999]},
                     db=db).result
    _validate(res, _GW_RESULT, "code filter")

    assert [r["code"] for r in res["rows"]] == [300]
    assert res["codes_not_found"] == [999]
    # the matrix is untouched: the tint domain and the coverage counts are
    # the board's, not the filter's
    assert set(res["matrix"]) == {"100", "200", "300", "400"}
