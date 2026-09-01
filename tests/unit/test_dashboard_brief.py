"""dashboard_brief + player_radar: schemas, honesty states, and the
ANTI-DRIFT CONTRACT.

The contract (FINAL_SPEC §7): the brief may only *select and threshold*
numbers computed by the same shared helpers its source panels use — never a
second implementation. The load-bearing test here therefore runs the SOURCE
panel and the BRIEF against the same warehouse and asserts, number for
number, that the brief's items equal the source panel's rows for the same
key. If someone ever re-implements a definition inside the brief, these
equalities are what break.

Everything runs against a temporary seeded warehouse; ``_team_state`` is
patched so no test touches the network.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers all panels)
from fpl_edge.platform.registry import registered, run_script, script
from fpl_edge.platform.scripts.radar import METRICS, _mid_rank_percentile
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"

T0 = pd.Timestamp("2026-08-30 10:00", tz="UTC")   # older state snapshot
T1 = pd.Timestamp("2026-08-30 12:00", tz="UTC")   # newer state snapshot

#: (code, name, position, team_code, price_tenths, status, news, net_in, net_out)
#: A minimal but complete squad universe: 2 GKP, 5 DEF, 5 MID, 3 FWD plus a
#: few non-owned players for tiles.
PLAYERS = [
    (100, "StartGK",  1, 1, 45, "a", "", 0, 0),
    (101, "BenchGK",  1, 2, 45, "a", "", 0, 0),
    (110, "Def1", 2, 1, 50, "a", "", 0, 0),
    (111, "Def2", 2, 1, 50, "a", "", 0, 0),
    (112, "Def3", 2, 2, 50, "a", "", 0, 0),
    (113, "Def4", 2, 2, 50, "a", "", 0, 0),
    (114, "Def5", 2, 1, 40, "a", "", 0, 0),
    (120, "Mid1", 3, 1, 120, "a", "", 0, 0),
    (121, "Mid2", 3, 2, 80, "a", "", 200, 6000),   # owned faller
    (122, "Mid3", 3, 1, 70, "a", "", 0, 0),
    (123, "Mid4", 3, 2, 66, "d", "Knock - 75% chance of playing", 0, 0),
    (124, "MidBench", 3, 1, 60, "a", "", 0, 0),
    (130, "Fwd1", 4, 2, 90, "a", "", 0, 0),
    (131, "Fwd2", 4, 1, 76, "a", "", 0, 0),
    (132, "Fwd3", 4, 2, 60, "a", "", 0, 0),
    # not owned:
    (200, "HotTarget", 3, 1, 77, "a", "", 8000, 100),   # watchlisted riser
    (201, "TemplateMan", 3, 2, 75, "a", "", 0, 0),
    (202, "PlanIn", 4, 1, 80, "a", "", 0, 0),           # the plan buys him
]

SQUAD_CODES = [100, 101, 110, 111, 112, 113, 114,
               120, 121, 122, 123, 124, 130, 131, 132]
STARTERS = [100, 110, 111, 112, 113, 114, 120, 121, 122, 123, 130]
BENCH = [101, 124, 131, 132]


def _seed(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    stamp = T0
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 1, "team_id": 1, "name": "Alpha",
         "short_name": "ALP", "as_of": stamp},
        {"season": SEASON, "team_code": 2, "team_id": 2, "name": "Beta",
         "short_name": "BET", "as_of": stamp},
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 2, "is_finished": True,
         "deadline_utc": pd.Timestamp("2026-08-28 17:30", tz="UTC"),
         "as_of": stamp},
        {"season": SEASON, "gw": 3, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-09-05 17:30", tz="UTC"),
         "as_of": stamp},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": c, "element_id": c, "web_name": n,
         "first_name": n, "second_name": n, "position": p, "team_code": tc,
         "as_of": stamp}
        for c, n, p, tc, *_ in PLAYERS
    ]))
    for t, base in ((T0, 0), (T1, 1)):
        wh.append("fact_player_state", pd.DataFrame([
            {"season": SEASON, "code": c, "element_id": c,
             "price_tenths": price, "selected_by_pct":
                 45.0 if c == 201 else (5.0 if c >= 200 else 20.0),
             "status": status, "news": news, "news_added": None,
             "chance_of_playing_next_round": None,
             "transfers_in_event": ti * base, "transfers_out_event": to * base,
             "cost_change_start": 0, "as_of": t,
             "can_select": True, "can_transact": True, "removed": False}
            for c, n, p, tc, price, status, news, ti, to in PLAYERS
        ]))
    wh.close()

    # The watchlist is written by the MCP tools, not the ingest path, so it is
    # not in the warehouse's append map — create and fill it directly.
    import duckdb
    con = duckdb.connect(str(path))
    con.execute(
        "CREATE TABLE IF NOT EXISTS watchlist ("
        "item_id VARCHAR, created_utc TIMESTAMPTZ, season VARCHAR, "
        "code INTEGER, player_name VARCHAR, note VARCHAR, source VARCHAR, "
        "resolved BOOLEAN, resolved_utc TIMESTAMPTZ)"
    )
    con.execute(
        "INSERT INTO watchlist VALUES ('wl_1', ?, ?, 200, 'HotTarget', "
        "'test', 'test', false, NULL)", [T0.to_pydatetime(), SEASON],
    )
    con.close()

    # Projection artefact next to the db — the same file squad_overview reads.
    proj = pd.DataFrame({
        "code": SQUAD_CODES + [200, 201, 202],
        "xpts": [2.0,               # StartGK — invertible vs BenchGK
                 3.6,               # BenchGK
                 4.0, 4.1, 4.2, 4.3, 3.0,     # defs
                 5.4, 5.0, 4.5, 4.4,          # mids (Mid1 highest mean)
                 3.2,                          # MidBench (no inversion: 3.2 < 4.4? -> vs weakest MID starter 4.4, no)
                 4.0, 3.9, 3.8,                # fwds
                 5.9, 4.9, 5.0],               # non-owned
        "p_haul": [0.01, 0.02,
                   0.05, 0.05, 0.05, 0.05, 0.04,
                   0.10, 0.30, 0.08, 0.07,     # Mid2 has the haul odds
                   0.03,
                   0.09, 0.08, 0.07,
                   0.20, 0.15, 0.11],
    })
    proj.to_parquet(tmp_path / "gw1_projection.parquet")
    return path


def _fake_state():
    picks = []
    for c in SQUAD_CODES:
        picks.append(SimpleNamespace(
            code=c, is_starter=c in STARTERS,
            is_captain=c == 120, is_vice=c == 121,
            multiplier=2 if c == 120 else (1 if c in STARTERS else 0),
        ))
    return SimpleNamespace(
        picks=picks, gw=3,
        bank=SimpleNamespace(tenths=15), bank_tenths=15,
        provenance=SimpleNamespace(name="PUBLIC_PICKS"),
        free_transfers=1,
    )


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = _seed(tmp_path)
    from fpl_edge.interfaces.qa import QuestionRouter
    monkeypatch.setattr(QuestionRouter, "_team_state",
                        lambda self: _fake_state())
    return path


def _write_plan(tmp_path, generated_at, horizon=(3, 4, 5)):
    plan_squad = [c for c in SQUAD_CODES if c != 131] + [202]  # Fwd2 -> PlanIn
    plan = {
        "generated_at": generated_at,
        "snapshot_as_of": generated_at,
        "season": SEASON,
        "horizon_gws": list(horizon),
        "objective_mode": "rank_mv",
        "objective": 123.456,
        "n_sims": 100,
        "solver": "status=Optimal gap=0.0",
        "notes": ["a solver note, verbatim"],
        "gw1": {"squad": plan_squad,
                "starting_xi": plan_squad[:11],
                "bench": plan_squad[11:],
                "captain": 120, "vice_captain": 121,
                "chip": "3xc", "bank_after": 0},
    }
    (tmp_path / "gw1_plan.json").write_text(json.dumps(plan))


# ---------------------------------------------------------------- registry --


def test_both_new_panels_are_registered_with_object_schemas():
    assert {"dashboard_brief", "player_radar"} <= set(registered())
    for name in ("dashboard_brief", "player_radar"):
        s = script(name)
        assert s.params_schema["type"] == "object"
        # the registry wraps result schemas in oneOf[real, EMPTY]
        assert "oneOf" in s.result_schema


def test_the_brief_echoes_every_threshold_it_applies(db):
    res = run_script("dashboard_brief", {}, db=db).result
    thr = res["thresholds"]
    for key in ("bench_margin_xpts", "own_fall_net_hr", "target_rise_net_hr",
                "template_own_pct", "diff_own_pct", "fixture_rank_move",
                "tile_cap"):
        assert key in thr, f"threshold {key} not echoed in the payload"


def test_the_brief_carries_no_free_text_recommendation_field():
    """Wording lives in view templates keyed by rule id; the payload may
    carry verbatim source strings and threshold echoes, never advice prose."""
    node = script("dashboard_brief").result_schema["oneOf"][0]
    alert_props = set(node["properties"]["alerts"]["items"]["properties"])
    tile_props = set(node["properties"]["tiles"]["items"]["properties"])
    for banned in ("recommendation", "advice", "verdict", "claim", "text",
                   "message", "headline"):
        assert banned not in alert_props
        assert banned not in tile_props


# ---------------------------------------------------- the anti-drift contract


def test_bench_inversion_numbers_equal_squad_overview_numbers(db):
    sq = run_script("squad_overview", {}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    rows = [a for a in brief["alerts"] if a["rule"] == "bench_inversion"]
    assert rows, "the seeded GK inversion (3.6 vs 2.0) must fire"
    by_code = {p["code"]: p for p in sq["starters"] + sq["bench"]}
    for a in rows:
        bench_code, starter_code = a["codes"]
        assert a["numbers"]["bench_xpts"] == by_code[bench_code]["xpts"]
        assert a["numbers"]["starter_xpts"] == by_code[starter_code]["xpts"]
        assert a["numbers"]["swing"] == pytest.approx(
            round(by_code[bench_code]["xpts"] - by_code[starter_code]["xpts"], 3))
        assert a["source_panel"] == "squad_overview"
        assert a["source_as_of"] == sq["as_of"].replace(" ", "T")


def test_captain_divergence_prints_both_measures_verbatim(db):
    sq = run_script("squad_overview", {}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    rows = [a for a in brief["alerts"] if a["rule"] == "captain_divergence"]
    assert rows, "seeded: armband on Mid1 (mean pick) but Mid2 has haul odds"
    a = rows[0]
    haul_pick = max((p for p in sq["starters"] if p["p_haul"] is not None),
                    key=lambda p: p["p_haul"])
    mean_pick = max((p for p in sq["starters"] if p["xpts"] is not None),
                    key=lambda p: p["xpts"])
    assert a["numbers"]["haul_pick_p_haul"] == haul_pick["p_haul"]
    assert a["numbers"]["mean_pick_xpts"] == mean_pick["xpts"]
    # two measures, never blended: no combined/composite number exists
    assert not any("combined" in k or "score" in k for k in a["numbers"])


def test_owned_price_fall_quotes_price_radar_rows_exactly(db):
    pr = run_script("price_radar", {"limit": 200}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    rows = [a for a in brief["alerts"] if a["rule"] == "own_price_fall"]
    assert rows, "seeded: Mid2 net -5800 over 2h = -2900/hr, past -1500"
    fallers = {r["code"]: r for r in pr["fallers"]}
    for a in rows:
        src = fallers[a["codes"][0]]
        assert a["numbers"]["net"] == src["net"]
        assert a["numbers"]["net_per_hour"] == src["net_per_hour"]
        assert a["numbers"]["window_h"] == pr["window"]["hours"]
        assert a["source_panel"] == "price_radar"


def test_price_rise_target_tile_quotes_the_riser_row_and_the_gate(db):
    pr = run_script("price_radar", {"limit": 200}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    tiles = [t for t in brief["tiles"] if t["kind"] == "price_rise_target"]
    assert tiles, "seeded: watchlisted HotTarget rises at +3950/hr"
    risers = {r["code"]: r for r in pr["risers"]}
    for t in tiles:
        assert t["number"]["value"] == risers[t["code"]]["net_per_hour"]
        assert t["number"]["unit"] == "net/hr"
        assert "2,500" in t["gate"] or "2500" in t["gate"]
        assert "not a predicted change" in t["gate"]


def test_availability_alert_carries_fpl_news_verbatim(db):
    sq = run_script("squad_overview", {}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    rows = [a for a in brief["alerts"] if a["rule"] == "availability"]
    assert rows, "seeded: Mid4 is doubtful"
    src = {p["code"]: p for p in sq["starters"] + sq["bench"]}
    for a in rows:
        assert a["news"] == src[a["codes"][0]]["news"]
        assert a["status"] == src[a["codes"][0]]["status"]
        assert a["priority"] == 0


def test_every_alert_and_tile_cites_a_source_panel(db):
    brief = run_script("dashboard_brief", {}, db=db).result
    for item in brief["alerts"] + brief["tiles"]:
        assert item["source_panel"], item
        assert "source_as_of" in item, item


def test_the_watch_log_distinguishes_clear_from_gap(db):
    brief = run_script("dashboard_brief", {}, db=db).result
    statuses = {w["check"]: w["status"] for w in brief["watch_log"]}
    assert statuses["squad_flags"] == "firing"      # Mid4 doubtful
    assert statuses["bench_order"] == "firing"      # GK inversion
    # ownership_eo has no EO feed in the seeded db — a gap or a measured zero,
    # but never absent from the log
    assert "template_gaps" in statuses
    assert all(w["detail"] for w in brief["watch_log"])


# ------------------------------------------------------------- solve states


def test_a_stale_plan_is_a_named_gap_with_a_p0_mirror_and_no_derivation(db, tmp_path):
    _write_plan(tmp_path, "2026-08-20T00:00:00+00:00", horizon=(2, 3, 4))
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] == "stale"
    assert s["derived"] is None, "a stale plan must not render a move"
    assert s["chip"] == "3xc", "the chip line survives even in the gap state"
    assert s["reason"]
    mirrors = [a for a in brief["alerts"] if a["rule"] == "solve_stale"]
    assert mirrors and mirrors[0]["priority"] == 0


def test_a_missing_plan_is_the_missing_state_with_a_pipeline_pointer(db):
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] == "missing"
    assert "plan artefact" in (s["reason"] or "")
    rows = [a for a in brief["alerts"] if a["rule"] == "solve_missing"]
    assert rows and rows[0]["drill"].get("tab") == "pipelines"


def test_a_live_plan_derives_transfers_and_labels_the_consensus_delta(db, tmp_path):
    _write_plan(tmp_path, dt.datetime.now(UTC).isoformat(), horizon=(3, 4, 5))
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] in ("fresh", "aging")
    d = s["derived"]
    assert d is not None
    assert d["method"].startswith("plan squad"), "the derivation confesses itself"
    moves = {(t["out"]["code"], t["in"]["code"]) for t in d["transfers"]}
    assert moves == {(131, 202)}, "plan swaps Fwd2 for PlanIn"
    # The solver's objective stays in its own currency; the consensus delta is
    # a separate, labelled quantity.
    assert s["objective_mode"] == "rank_mv"
    assert d["consensus_label"] is None or "consensus xPts" in d["consensus_label"]
    assert s["chip"] == "3xc" and s["chip_gw"] == 3
    assert s["hold_baseline"] is None, (
        "no hold baseline is stored; the gain slot is a named gap until the "
        "solve_plan extension ships"
    )


# ------------------------------------------------------------- player_radar


def _seed_matches(db, rows):
    wh = Warehouse(db)
    base = {c: 0.0 for c in
            ["goals", "assists", "penalties_scored", "penalties_missed",
             "total_shots", "shots_on_target", "xg", "xa", "xgot",
             "chances_created", "touches_opposition_box", "tackles",
             "tackles_won", "interceptions", "recoveries", "blocks",
             "clearances", "defensive_contributions", "saves",
             "goals_conceded", "xgot_faced", "goals_prevented"]}
    frame = []
    for code, gw, minutes, over in rows:
        r = dict(base)
        r.update(over)
        frame.append({"source": "test", "season": SEASON, "code": code,
                      "match_id": f"m{code}_{gw}", "tournament": "EPL",
                      "gw": gw, "minutes_played": minutes,
                      "start_min": 0.0, "finish_min": minutes,
                      "as_of": T1, **r})
    wh.append("fact_player_match_stats", pd.DataFrame(frame))
    wh.close()


def test_metric_sets_are_closed_and_minutes_is_never_a_slice():
    assert len(METRICS["GKP"]) == 4
    assert len(METRICS["DEF"]) == 8
    assert len(METRICS["MID"]) == 8
    assert len(METRICS["FWD"]) == 8
    for cols in METRICS.values():
        assert all(k != "minutes_played" and "minutes" not in k
                   for k, _, _ in cols)
    # DEF renders two contiguous half-arcs: attack slices first, then defence.
    groups = [g for _, _, g in METRICS["DEF"]]
    first_def = groups.index("defending")
    assert all(g == "defending" for g in groups[first_def:])
    assert all(g != "defending" for g in groups[:first_def])


def test_mid_rank_percentile_splits_ties_and_collapses_all_zero_to_50():
    assert _mid_rank_percentile(5.0, [1.0, 2.0, 5.0, 9.0]) == 62  # 2 + .5
    assert _mid_rank_percentile(0.0, [0.0, 0.0, 0.0, 0.0]) == 50
    assert _mid_rank_percentile(3.0, []) == 50


def test_radar_full_result_and_zero_separation_flag(db):
    _seed_matches(db, [
        (130, 1, 90, {"xg": 0.9, "total_shots": 4, "shots_on_target": 2,
                      "xgot": 1.0, "touches_opposition_box": 6}),
        (131, 1, 90, {"xg": 0.3, "total_shots": 2, "shots_on_target": 1,
                      "xgot": 0.2, "touches_opposition_box": 3}),
        (132, 1, 90, {"xg": 0.1, "total_shots": 1, "shots_on_target": 0,
                      "xgot": 0.1, "touches_opposition_box": 1}),
        (202, 1, 90, {"xg": 0.2, "total_shots": 1, "shots_on_target": 1,
                      "xgot": 0.3, "touches_opposition_box": 2}),
    ])
    res = run_script("player_radar", {"code": 130}, db=db).result
    assert res["pos"] == "FWD" and res["below_floor"] is False
    assert len(res["slices"]) == 8
    xg = next(s for s in res["slices"] if s["key"] == "xg_p90")
    # 4 qualifying FWDs, 130 has the highest xG/90: (3 + 0.5)/4 = 87.5 -> 88
    assert xg["percentile"] == 88
    assert res["n_peers"] == 4
    # recoveries: every FWD at 0 -> ties_at_zero, percentile 50
    rec = next(s for s in res["slices"] if s["key"] == "recoveries_p90")
    assert rec["ties_at_zero"] is True and rec["percentile"] == 50


def test_radar_below_floor_keeps_slices_but_says_so(db):
    _seed_matches(db, [
        (130, 1, 90, {"xg": 0.9}), (131, 1, 90, {"xg": 0.3}),
        (132, 1, 30, {"xg": 0.4}),    # below the 90' floor
    ])
    res = run_script("player_radar", {"code": 132}, db=db).result
    assert res["below_floor"] is True
    assert res["slices"], "below_floor keeps its slices — faded, not hidden"
    assert res["floor_minutes"] >= 90


def test_radar_no_rows_is_the_distinct_empty_shape(db):
    _seed_matches(db, [(130, 1, 90, {"xg": 0.9})])
    res = run_script("player_radar", {"code": 131}, db=db).result
    assert res.get("empty") is True
    assert "fact_player_match_stats" in res["reason"]
    assert "131" in res["reason"]
