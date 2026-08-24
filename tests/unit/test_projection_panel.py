"""projection_table's gameweek mode: consensus, spread, filters, detail.

Style follows test_platform_scripts.py: a seeded warehouse per test area, the
script run through ``run_script`` so both schemas are enforced, and every
assertion phrased against the declared result shape. Two invariants get their
own tests because breaking them silently would be worse than an error:

* the dashboard's original call shape (``{limit, sort}`` and ``{}``) still
  takes the artefact path and renders unchanged, and
* ``p_appear`` stays a column of its own -- the consensus xpts is never
  quietly multiplied by it.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registration is the import)
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.platform.registry import run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
SEASON = "2026-27"
T0 = pd.Timestamp("2026-08-01 12:00", tz="UTC")

#: (code, web_name, position, team_code, price_tenths, selected_by_pct)
PLAYERS = [
    (100, "Raya", 1, 1, 55, 20.0),
    (200, "Gabriel", 2, 1, 60, 30.0),
    (300, "Palmer", 3, 2, 105, 45.0),
    (400, "Jackson", 4, 2, 75, 12.0),
]

#: provider -> code -> GW2 xp. Palmer (300) is the big disagreement: 2.0 vs
#: 8.0 across sources (spread 6.0); Raya (100) is the tight one (spread 0.4).
GW2_XP = {
    "src_a": {100: 3.0, 200: 4.0, 300: 2.0, 400: 5.0},
    "src_b": {100: 3.2, 200: 4.4, 300: 4.0, 400: 5.5},
    "src_c": {100: 3.4, 200: 4.8, 300: 8.0, 400: 6.0},
}
#: Only src_a publishes p_appear/xmins, and only for some players -- the
#: real feeds are exactly this ragged.
GW2_P_APPEAR = {100: 0.95, 200: 0.90, 300: 0.50, 400: None}


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
         "deadline_utc": pd.Timestamp("2026-08-15 17:30", tz="UTC"), "as_of": T0},
        {"season": SEASON, "gw": 2, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-22 17:30", tz="UTC"), "as_of": T0},
    ]))

    store = ProjectionStore(wh)
    rows = []
    for provider, per_code in GW2_XP.items():
        for code, xp in per_code.items():
            p_app = GW2_P_APPEAR[code] if provider == "src_a" else None
            rows.append({
                "provider": provider, "season": SEASON, "gw": 2, "code": code,
                "xp": xp, "xp_if_appears": None, "p_appear": p_app,
                "xmins": 85.0 if provider == "src_a" and p_app else None,
                "as_of": T0,
            })
            # GW3 for the detail horizon; only two of the three sources
            # look that far ahead, as the real feeds do.
            if provider != "src_c":
                rows.append({
                    "provider": provider, "season": SEASON, "gw": 3,
                    "code": code, "xp": xp + 1.0, "xp_if_appears": None,
                    "p_appear": None, "xmins": None, "as_of": T0,
                })
    frame = pd.DataFrame(rows)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    store.append("fact_projection", frame)
    wh.close()
    return path


def consensus(db, **params):
    run = run_script("projection_table", {"gw": 2, **params}, db=db)
    assert run.result.get("empty") is not True, run.result.get("reason")
    return run.result


def by_code(result):
    return {r["code"]: r for r in result["rows"]}


# -- backwards compatibility -------------------------------------------------

def test_the_dashboards_call_shape_still_takes_the_artefact_path(db):
    """{limit, sort} with no gw must behave exactly as before: no artefact
    solved means an honest empty telling the operator to run the solve --
    NOT the provider consensus, which this warehouse could have served."""
    run = run_script("projection_table", {"limit": 50, "sort": "xpts"}, db=db)
    assert run.result["empty"] is True
    assert "solve" in run.result["reason"].lower()


# -- consensus mode ----------------------------------------------------------

def test_consensus_means_spread_and_source_count(db):
    rows = by_code(consensus(db))
    palmer = rows[300]
    assert palmer["xpts"] == pytest.approx((2.0 + 4.0 + 8.0) / 3, abs=1e-3)
    assert palmer["xpts_min"] == 2.0 and palmer["xpts_max"] == 8.0
    assert palmer["spread"] == pytest.approx(6.0)
    assert palmer["n_sources"] == 3
    assert palmer["team"] == "CHE" and palmer["pos"] == "MID"
    assert palmer["price"] == 10.5 and palmer["own_pct"] == 45.0
    assert palmer["value"] == pytest.approx(palmer["xpts"] / 10.5, abs=1e-3)


def test_p_appear_is_reported_beside_xpts_never_multiplied_in(db):
    """Palmer: consensus xpts 14/3, p_appear 0.5. If anyone ever 'helpfully'
    multiplies them, xpts halves and this fails."""
    palmer = by_code(consensus(db))[300]
    assert palmer["xpts"] == pytest.approx(14.0 / 3, abs=1e-3)
    assert palmer["p_appear"] == pytest.approx(0.5)
    jackson = by_code(consensus(db))[400]
    assert jackson["p_appear"] is None  # unknown stays unknown, not 1.0


def test_sort_by_spread_puts_the_biggest_disagreement_first(db):
    result = consensus(db, sort="spread")
    assert result["rows"][0]["code"] == 300      # Palmer, spread 6.0
    assert result["rows"][-1]["code"] == 100     # Raya, spread 0.4
    spreads = [r["spread"] for r in result["rows"]]
    assert spreads == sorted(spreads, reverse=True)


def test_gw_next_resolves_from_the_event_deadlines(db):
    run = run_script("projection_table", {"gw": "next"}, db=db)
    assert run.result["gw"] == 2      # GW1's deadline has passed
    assert run.result["mode"] == "consensus"


def test_aggregate_tiles_by_team_and_position(db):
    result = consensus(db)
    teams = {t["team"]: t for t in result["by_team"]}
    # CHE: Palmer 14/3 + Jackson 5.5 -> avg ~5.083; ARS: 3.2 + 4.4 -> 3.8.
    assert teams["CHE"]["avg_xpts"] == pytest.approx((14.0 / 3 + 5.5) / 2, abs=1e-3)
    assert teams["ARS"]["avg_xpts"] == pytest.approx(3.8)
    assert result["by_team"][0]["team"] == "CHE"          # sorted best-first
    poss = {p["pos"]: p for p in result["by_position"]}
    assert poss["GKP"]["avg_xpts"] == pytest.approx(3.2)
    assert poss["MID"]["n_players"] == 1


def test_coverage_and_sources_are_reported_for_the_pickers(db):
    result = consensus(db)
    assert result["gw_coverage"] == [
        {"gw": 2, "n_sources": 3, "n_players": 4},
        {"gw": 3, "n_sources": 2, "n_players": 4},
    ]
    assert result["sources"] == ["src_a", "src_b", "src_c"]


# -- filters actually filter -------------------------------------------------

def test_position_filter(db):
    result = consensus(db, position=3)
    assert [r["code"] for r in result["rows"]] == [300]


def test_team_filter_is_case_insensitive(db):
    assert set(by_code(consensus(db, team="che"))) == {300, 400}
    assert set(by_code(consensus(db, team="ARS"))) == {100, 200}


def test_max_price_filter(db):
    assert set(by_code(consensus(db, max_price=7.5))) == {100, 200, 400}
    assert set(by_code(consensus(db, max_price=6.0))) == {100, 200}


def test_min_p_appear_drops_low_and_unknown(db):
    """Palmer (0.5) is below the bar and Jackson (unknown) cannot clear it:
    an unknown appearance probability must not pass a risk filter."""
    assert set(by_code(consensus(db, min_p_appear=0.85))) == {100, 200}


def test_filters_that_exclude_everyone_are_an_honest_empty(db):
    run = run_script("projection_table", {"gw": 2, "position": 1,
                                          "max_price": 4.0}, db=db)
    assert run.result["empty"] is True
    assert "filter" in run.result["reason"].lower()


def test_aggregates_ignore_player_filters(db):
    """The strip answers 'which teams look best this GW'; a price filter on
    the table must not reshape that answer."""
    result = consensus(db, max_price=6.0)
    assert {t["team"] for t in result["by_team"]} == {"ARS", "CHE"}


# -- single-source mode ------------------------------------------------------

def test_single_source_mode_shows_that_vendor_raw(db):
    run = run_script("projection_table", {"gw": 2, "source": "src_c"}, db=db)
    result = run.result
    assert result["mode"] == "source" and result["source"] == "src_c"
    rows = by_code(result)
    assert rows[300]["xpts"] == 8.0
    assert rows[300]["spread"] is None and rows[300]["n_sources"] == 1


def test_an_unknown_source_is_empty_and_names_the_real_ones(db):
    run = run_script("projection_table", {"gw": 2, "source": "fplform"}, db=db)
    assert run.result["empty"] is True
    assert "src_a" in run.result["reason"]


# -- honest empties ----------------------------------------------------------

def test_an_uncovered_gw_names_the_gameweeks_that_do_have_data(db):
    run = run_script("projection_table", {"gw": 30}, db=db)
    assert run.result["empty"] is True
    reason = run.result["reason"]
    assert "GW2 (3 sources)" in reason and "GW3 (2 sources)" in reason


def test_gw_mode_on_a_warehouse_with_no_projections_says_to_ingest(tmp_path):
    path = tmp_path / "bare.duckdb"
    wh = Warehouse(path)
    ProjectionStore(wh)          # tables exist, zero rows
    wh.close()
    run = run_script("projection_table", {"gw": 2}, db=path)
    assert run.result["empty"] is True
    assert "ingest" in run.result["reason"].lower()


# -- the per-player detail ---------------------------------------------------

def test_detail_lists_every_source_for_the_horizon(db):
    result = consensus(db, detail_code=300)
    detail = result["detail"]
    assert detail["name"] == "Palmer"
    assert detail["gw_from"] == 2 and detail["gw_to"] == 6
    got = {(r["source"], r["gw"]): r["xpts"] for r in detail["rows"]}
    assert got[("src_a", 2)] == 2.0 and got[("src_b", 2)] == 4.0
    assert got[("src_c", 2)] == 8.0
    assert got[("src_a", 3)] == 3.0
    assert ("src_c", 3) not in got   # src_c does not project GW3: no row,
    #                                  not an invented one


def test_detail_flags_the_outlier_source(db):
    detail = consensus(db, detail_code=300)["detail"]
    # src_c says 8.0 against the others' mean of 3.0: +5.0 vs rest.
    assert detail["outlier"]["source"] == "src_c"
    assert detail["outlier"]["delta_vs_rest"] == pytest.approx(5.0)
    # Raya's sources sit within 0.4 of each other; the outlier field still
    # names the farthest, but the delta is honest about how small it is.
    tight = consensus(db, detail_code=100)["detail"]
    assert abs(tight["outlier"]["delta_vs_rest"]) < 0.5


def test_detail_for_an_unknown_code_is_null_with_a_note_not_an_error(db):
    result = consensus(db, detail_code=999999)
    assert result["detail"] is None
    assert any("999999" in n for n in result["notes"])
