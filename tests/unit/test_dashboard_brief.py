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
    from fpl_edge.myteam.state import ChipStatus

    picks = []
    for c in SQUAD_CODES:
        picks.append(SimpleNamespace(
            code=c, is_starter=c in STARTERS,
            is_captain=c == 120, is_vice=c == 121,
            multiplier=2 if c == 120 else (1 if c in STARTERS else 0),
        ))
    chips = (
        ChipStatus(chip="3xc", windows=((1, 19), (20, 38)), played=()),
        ChipStatus(chip="bboost", windows=((1, 19), (20, 38)), played=(1,)),
        ChipStatus(chip="freehit", windows=((2, 19), (20, 38)), played=()),
        ChipStatus(chip="wildcard", windows=((2, 19), (20, 38)), played=()),
    )
    return SimpleNamespace(
        picks=picks, gw=3,
        bank=SimpleNamespace(tenths=15), bank_tenths=15,
        provenance=SimpleNamespace(name="PUBLIC_PICKS"),
        free_transfers=1,
        chip_status=lambda: chips,
    )


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = _seed(tmp_path)
    from fpl_edge.interfaces.qa import QuestionRouter
    monkeypatch.setattr(QuestionRouter, "_team_state",
                        lambda self: _fake_state())
    return path


def _write_transfer_plan(tmp_path, generated_at, *, out=(131,), into=(202,),
                         horizon=(3, 4, 5), gain=3.3, alternatives=None,
                         hit_verdicts=(), chosen_hits=0):
    """The artefact `fpl recommend --commit` writes — Fwd2 -> PlanIn by
    default, with a losing alternative and the solved roll on the table."""
    if alternatives is None:
        alternatives = [
            {"out": [121], "in": [201], "n_transfers": 1, "hits": 0,
             "hit_points": 0, "objective": 122.0, "chip": "", "label": ""},
            {"out": [], "in": [], "n_transfers": 0, "hits": 0,
             "hit_points": 0, "objective": 120.1, "chip": "", "label": "roll"},
        ]
    plan = {
        "generated_at": generated_at,
        "season": SEASON,
        "gw": int(horizon[0]),
        "horizon_gws": list(horizon),
        "objective_mode": "expected_points",
        "free_transfers": 2,
        "unlimited_transfers": False,
        "chosen": {"out": list(out), "in": list(into),
                   "n_transfers": len(into), "hits": chosen_hits,
                   "hit_points": chosen_hits * 4, "bank_after_tenths": 5,
                   "chip": "", "objective": 123.4, "label": "",
                   "captain": 120, "vice_captain": 121,
                   "starting_xi": list(STARTERS)},
        "roll": {"objective": 120.1},
        "gain_over_roll": gain,
        "alternatives": list(alternatives),
        "hit_verdicts": list(hit_verdicts),
        "notes": ["EXPECTED_POINTS is a surrogate."],
        "n_candidates_screened": 8,
        "n_candidates_solved": 8,
        "solve_seconds": 42.0,
        "bounds": "candidates capped at 25/position, 60s per MILP; a capped "
                  "solve is best-found, not a proven optimum",
    }
    (tmp_path / "transfer_plan.json").write_text(json.dumps(plan))


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
    move_props = set(node["properties"]["moves"]["items"]["properties"])
    sug_props = set(node["properties"]["suggested_xi"]["properties"])
    for banned in ("recommendation", "advice", "verdict", "claim", "text",
                   "message", "headline", "because", "sentence"):
        assert banned not in alert_props
        assert banned not in tile_props
        assert banned not in move_props, (
            "a move card is rule id + numbers; the because-sentence lives "
            "in the view's templates"
        )
        assert banned not in sug_props


# ---------------------------------------------------- the anti-drift contract


def test_bench_and_captain_are_fixed_in_the_squad_not_argued_in_prose(db):
    """The owner's rule: bench/captain fixes happen IN the squad. The alert
    kinds are gone from the payload AND from the schema's enum."""
    brief = run_script("dashboard_brief", {}, db=db).result
    assert not [a for a in brief["alerts"]
                if a["kind"] in ("BENCH", "CAPTAIN")]
    node = script("dashboard_brief").result_schema["oneOf"][0]
    kinds = node["properties"]["alerts"]["items"]["properties"]["kind"]["enum"]
    assert "BENCH" not in kinds and "CAPTAIN" not in kinds


def test_suggested_xi_swaps_equal_squad_overview_numbers(db):
    """ANTI-DRIFT: the suggested swaps are squad_overview's own xPts applied,
    number for number — never a second computation."""
    sq = run_script("squad_overview", {}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["suggested_xi"]
    assert s is not None
    assert s["swaps"], "the seeded GK inversion (3.6 vs 2.0) must swap"
    by_code = {p["code"]: p for p in sq["starters"] + sq["bench"]}
    total = 0.0
    for sw in s["swaps"]:
        assert sw["numbers"]["bench_xpts"] == by_code[sw["in"]["code"]]["xpts"]
        assert sw["numbers"]["starter_xpts"] == by_code[sw["out"]["code"]]["xpts"]
        assert sw["numbers"]["swing"] == pytest.approx(round(
            by_code[sw["in"]["code"]]["xpts"]
            - by_code[sw["out"]["code"]]["xpts"], 3))
        total += sw["numbers"]["swing"]
    assert s["swap_delta_xpts"] == pytest.approx(round(total, 2))
    assert s["source_panel"] == "squad_overview"
    assert s["source_as_of"] == sq["as_of"].replace(" ", "T")
    # the GK inversion specifically: BenchGK in, StartGK out
    moves = {(sw["out"]["code"], sw["in"]["code"]) for sw in s["swaps"]}
    assert (100, 101) in moves
    # and the lineup composes: swapped-in on the pitch, swapped-out benched
    assert 101 in s["xi_codes"] and 100 not in s["xi_codes"]
    assert 100 in s["bench_codes"] and 101 not in s["bench_codes"]
    assert len(s["xi_codes"]) == 11 and len(s["bench_codes"]) == 4
    assert s["n_changes"] == len(s["swaps"])


def test_suggested_captain_prints_both_measures_never_blended(db):
    sq = run_script("squad_overview", {}, db=db).result
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["suggested_xi"]
    xi = set(s["xi_codes"])
    pool = [p for p in sq["starters"] + sq["bench"] if p["code"] in xi]
    haul_pick = max((p for p in pool if p["p_haul"] is not None),
                    key=lambda p: p["p_haul"])
    mean_pick = max((p for p in pool if p["xpts"] is not None),
                    key=lambda p: p["xpts"])
    assert s["captain_by_haul"]["code"] == haul_pick["code"]
    assert s["captain_by_mean"]["code"] == mean_pick["code"]
    # the suggestion is the mean pick — the pitch's own currency — with the
    # haul measure printed beside it, never averaged in
    assert s["captain"]["code"] == mean_pick["code"]
    assert s["your_captain"]["code"] == 120, "armband is on Mid1"
    n = s["captain_numbers"]
    assert n["haul_pick_p_haul"] == haul_pick["p_haul"]
    assert n["mean_pick_xpts"] == mean_pick["xpts"]
    assert not any("combined" in k or "score" in k for k in n)


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


def test_a_stale_transfer_plan_is_a_named_gap_and_renders_no_move(db, tmp_path):
    _write_transfer_plan(tmp_path, "2026-08-20T00:00:00+00:00", horizon=(2, 3, 4))
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] == "stale"
    assert s["plan"] is None, "a stale plan must not render a recommendation"
    assert "no longer have" in (s["reason"] or ""), (
        "the stale reason must say the moves were priced against a squad "
        "you no longer have"
    )
    mirrors = [a for a in brief["alerts"] if a["rule"] == "solve_stale"]
    assert mirrors and mirrors[0]["priority"] == 0
    statuses = {w["check"]: w["status"] for w in brief["watch_log"]}
    assert statuses["solver"] == "gap"


def test_a_missing_transfer_plan_is_the_missing_state_with_the_runner_pointer(db):
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] == "missing"
    assert s["plan"] is None
    assert "transfer plan artefact" in (s["reason"] or "")
    assert "mode=transfers" in (s["reason"] or ""), (
        "the fix must be named: POST /api/solve mode=transfers"
    )
    rows = [a for a in brief["alerts"] if a["rule"] == "solve_missing"]
    assert rows and rows[0]["drill"].get("tab") == "pipelines"
    statuses = {w["check"]: w["status"] for w in brief["watch_log"]}
    assert statuses["solver"] == "gap"


def test_a_fresh_transfer_plan_serves_named_moves_in_the_solvers_currency(db, tmp_path):
    # generated inside the T-4h window before the (seeded 2099) deadline
    _write_transfer_plan(tmp_path, "2099-09-05T15:00:00+00:00")
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] == "fresh"
    p = s["plan"]
    assert p is not None
    moves = {(m["out"]["code"], m["in"]["code"]) for m in p["moves"]}
    assert moves == {(131, 202)}, "the chosen move: Fwd2 -> PlanIn"
    m = p["moves"][0]
    assert m["out"]["name"] == "Fwd2" and m["in"]["name"] == "PlanIn", (
        "playerRefs resolve through sem_players, never raw codes"
    )
    assert m["price_delta"] == pytest.approx(0.4)   # 8.0 - 7.6
    # the gain is the solver's own forecast, in the currency the payload
    # names — never blended with consensus numbers
    assert p["objective_mode"] == "expected_points"
    assert p["gain_over_roll"] == pytest.approx(3.3)
    assert p["is_roll"] is False
    assert p["free_transfers"] == 2 and p["hits"] == 0
    assert p["captain"]["code"] == 120
    assert p["your_captain"]["code"] == 120, "armband is on Mid1"
    assert p["bounds"] and "best-found" in p["bounds"]
    # alternatives are name summaries + the solver's numbers, capped at 3
    assert len(p["alternatives"]) <= 3
    summaries = [a["summary"] for a in p["alternatives"]]
    assert any("Mid2" in s_ and "TemplateMan" in s_ for s_ in summaries)
    assert any("roll" in s_ for s_ in summaries)
    assert p["hit_verdict"] is None, "no hit, no verdict"
    statuses = {w["check"]: w["status"] for w in brief["watch_log"]}
    assert statuses["solver"] == "clear"


def test_a_roll_recommendation_is_a_recommendation_not_an_empty_state(db, tmp_path):
    _write_transfer_plan(tmp_path, dt.datetime.now(UTC).isoformat(),
                         out=(), into=(), gain=0.0,
                         alternatives=[])
    brief = run_script("dashboard_brief", {}, db=db).result
    s = brief["solve"]
    assert s["state"] in ("fresh", "aging")
    p = s["plan"]
    assert p is not None, "banking the transfer IS a recommendation"
    assert p["is_roll"] is True
    assert p["moves"] == []


def test_idea_due_is_gone_from_payload_and_schema(db):
    """Owner's call: the idea registry is useless in briefings — the tile
    kind, its watch check and its schema enum entry are all gone."""
    brief = run_script("dashboard_brief", {}, db=db).result
    assert not [t for t in brief["tiles"] if t["kind"] == "idea_due"]
    assert not [w for w in brief["watch_log"] if w["check"] == "idea_due"]
    assert "idea_registry" not in brief["sources_as_of"]
    node = script("dashboard_brief").result_schema["oneOf"][0]
    kinds = node["properties"]["tiles"]["items"]["properties"]["kind"]["enum"]
    assert "idea_due" not in kinds


# ------------------------------------------------------- chips + moves rules


def test_squad_overview_serves_the_chip_ledger(db):
    res = run_script("squad_overview", {}, db=db).result
    chips = {c["chip"]: c for c in res["chips"]}
    assert set(chips) == {"3xc", "bboost", "freehit", "wildcard"}
    assert chips["bboost"]["played"] == [1]
    assert chips["bboost"]["windows"] == [[1, 19], [20, 38]]
    assert chips["wildcard"]["windows"][0] == [2, 19], "WC locked in GW1"
    assert chips["3xc"]["played"] == []


def test_moves_are_a_named_gap_when_no_consensus_exists(db):
    """The default seed has no provider projections: the rules must say so
    rather than serving nothing silently."""
    brief = run_script("dashboard_brief", {}, db=db).result
    assert brief["moves"] == []
    gap = [e for e in brief["empty_kinds"] if e["kind"] == "moves"]
    assert gap and "consensus" in gap[0]["reason"]
    statuses = {w["check"]: w["status"] for w in brief["watch_log"]}
    assert statuses["move_rules"] == "gap"


#: Enriched seed for the move rules: a third club (Gamma) with the easiest
#: attacking run that the squad does not cover, provider projections at GW3,
#: and two settled gameweeks of returns.
def _seed_moves(db, tmp_path, *, gapman_returns: bool = True):
    import numpy as np

    wh = Warehouse(db)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 3, "team_id": 3, "name": "Gamma",
         "short_name": "GAM", "as_of": T1},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": 203, "element_id": 203,
         "web_name": "GapMan", "first_name": "Gap", "second_name": "Man",
         "position": 3, "team_code": 3, "as_of": T1},
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": 203, "element_id": 203,
         "price_tenths": 75, "selected_by_pct": 4.0, "status": "a",
         "news": "", "news_added": None,
         "chance_of_playing_next_round": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": T1,
         "can_select": True, "can_transact": True, "removed": False},
    ]))
    # schedule: GW1-2 settled, GW3-5 the near board's window. Gamma's run is
    # the easiest attack (twice vs leaky Beta) by construction.
    fx = []

    def fixt(fid, gw, home, away, day, finished):
        fx.append({"season": SEASON, "fixture_id": fid, "gw": gw,
                   "kickoff_utc": pd.Timestamp(day, tz="UTC"),
                   "home_team_code": home, "away_team_code": away,
                   "finished": finished,
                   "home_score": 1 if finished else None,
                   "away_score": 1 if finished else None,
                   "as_of": T1})
    fixt(101, 1, 1, 2, "2026-08-22", True)
    fixt(102, 1, 3, 2, "2026-08-22", True)
    fixt(201, 2, 2, 3, "2026-08-29", True)
    fixt(202, 2, 1, 3, "2026-08-29", True)
    fixt(301, 3, 3, 2, "2027-09-06", False)
    fixt(401, 4, 1, 3, "2027-09-13", False)
    fixt(501, 5, 2, 3, "2027-09-20", False)
    wh.append("fact_fixture", pd.DataFrame(fx))

    # settled returns (sem_player_form): GapMan scored twice; TemplateMan has
    # 2G+1A; every squad player blanks.
    pf = []

    def played(code, fid, gw, goals=0, assists=0):
        pf.append({"season": SEASON, "code": code, "fixture_id": fid,
                   "gw": gw, "minutes": 90, "goals_scored": goals,
                   "assists": assists, "starts": 1, "total_points": 2,
                   "was_home": None, "as_of": T1})
    if gapman_returns:
        played(203, 102, 1, goals=1)
        played(203, 201, 2, goals=1)
    else:
        played(203, 102, 1)
        played(203, 201, 2)
    played(201, 101, 1, goals=2, assists=1)
    for c in (120, 121, 122, 123, 124):
        played(c, 101 if c % 2 == 0 else 102, 1)
    wh.append("fact_player_fixture", pd.DataFrame(pf))

    # provider projections at GW3 (p_appear served, xmins not — today's
    # regime) for the mids involved on both sides of every rule. Written
    # through the ProjectionStore, the same path the ingest uses.
    from fpl_edge.ingest.projections.store import ProjectionStore

    rows = []
    for code, xp in ((120, 6.0), (121, 5.0), (122, 4.2), (123, 4.0),
                     (124, 3.0), (203, 5.5), (201, 5.8)):
        rows.append({"provider": "s1", "season": SEASON, "gw": 3,
                     "code": code, "xp": xp, "xp_if_appears": xp,
                     "p_appear": 0.9, "xmins": None, "as_of": T1})
    frame = pd.DataFrame(rows)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    ProjectionStore(wh).append("fact_projection", frame)
    wh.close()

    # the fitted-split artefact the fixture board reads (never fits itself):
    # Beta leaky (defence +0.5), Alpha tight (-0.3), Gamma average.
    atk = {1: 0.2, 2: 0.0, 3: 0.3}
    dfn = {1: -0.3, 2: 0.5, 3: 0.0}
    codes = [1, 2, 3]
    ratings = pd.DataFrame({
        "season": SEASON, "team_code": codes,
        "attack": [atk[c] for c in codes],
        "defence": [dfn[c] for c in codes],
        "is_promoted": False, "matches_seen": 2,
        "intercept": 0.1, "home_adv": 0.2, "rho": -0.05,
        "mean_attack": float(np.mean(list(atk.values()))),
        "mean_defence": float(np.mean(list(dfn.values()))),
        "half_life_days": 180.0, "n_matches": 6, "effective_n": 5.0,
        "converged": True,
        "fitted_at": pd.Timestamp("2026-08-30 12:30", tz="UTC"),
        "snapshot_as_of": T1,
    })
    ratings.to_parquet(tmp_path / "fixture_ratings.parquet")


def test_coverage_gap_names_the_uncovered_easy_run(db, tmp_path):
    _seed_moves(db, tmp_path)
    brief = run_script("dashboard_brief", {}, db=db).result
    cov = [m for m in brief["moves"] if m["rule"] == "coverage_gap"]
    assert cov, "Gamma's run is rank-1 attack and the squad holds nobody"
    m = cov[0]
    assert m["team"] == "GAM" and m["numbers"]["attack_rank"] == 1
    assert m["numbers"]["held_count"] == 0
    assert m["in"]["code"] == 203, "GapMan: top consensus xPts with returns"
    assert m["numbers"]["cand_returns"] == 2
    assert m["out"]["code"] == 124, ("MidBench: lowest consensus xPts "
                                     "affordable within bank + sale")
    # price maths from the payload, never recomputed client-side
    assert m["numbers"]["in_price"] == 7.5
    assert m["numbers"]["out_price"] == 6.0
    assert m["numbers"]["bank"] == 1.5
    assert m["gws"] == [1, 2]
    panels = {s["panel"] for s in m["sources"]}
    assert {"projection_table", "squad_overview",
            "fixture_board"} <= panels
    # the fixture ranks come from fixture_board's own horizon block
    tf = {t["team_code"]: t for t in brief["team_fixtures"]}
    assert tf[3]["horizon_attack_rank"] == 1
    # and the same rank the card quotes
    assert m["numbers"]["attack_rank"] == tf[3]["horizon_attack_rank"]


def test_coverage_candidate_xpts_equals_the_projection_panels_number(db, tmp_path):
    """ANTI-DRIFT: the card's xPts is the projection panel's number for the
    same (code, gw) — same semantic view, same rounding."""
    _seed_moves(db, tmp_path)
    brief = run_script("dashboard_brief", {}, db=db).result
    proj = run_script("projection_table", {"gw": 3}, db=db).result
    by_code = {r["code"]: r for r in proj["rows"]}
    for m in brief["moves"]:
        assert m["numbers"]["cand_xpts"] == by_code[m["in"]["code"]]["xpts"]
        assert m["numbers"]["out_xpts"] == by_code[m["out"]["code"]]["xpts"]


def test_coverage_gap_stays_quiet_when_no_candidate_has_returns(db, tmp_path):
    """The no-gap day: the run is still rank-1 but no Gamma attacker has a
    recent return, so no coverage card — and the absence is measured, not
    silent (watch still reports)."""
    _seed_moves(db, tmp_path, gapman_returns=False)
    brief = run_script("dashboard_brief", {}, db=db).result
    assert not [m for m in brief["moves"] if m["rule"] == "coverage_gap"]
    statuses = {w["check"]: w["status"] for w in brief["watch_log"]}
    assert statuses["move_rules"] in ("clear", "firing")   # measured, not gap


def test_form_upgrade_fires_on_both_gates_and_cites_thresholds(db, tmp_path):
    _seed_moves(db, tmp_path)
    brief = run_script("dashboard_brief", {}, db=db).result
    form = [m for m in brief["moves"] if m["rule"] == "form_upgrade"]
    assert form, "TemplateMan beats a squad mid on returns AND xPts"
    m = form[0]
    assert m["in"]["code"] == 201
    n = m["numbers"]
    thr = brief["thresholds"]
    assert n["cand_returns"] >= n["out_returns"] + thr["form_returns_margin"]
    assert n["cand_xpts"] >= n["out_xpts"] + thr["form_xpts_margin"]
    assert n["bank"] + n["out_price"] >= n["in_price"]
    # thresholds echoed so the view renders the gates from the payload
    for key in ("form_returns_margin", "form_xpts_margin", "move_cap",
                "coverage_attack_rank_top", "coverage_max_held",
                "recent_returns_min", "recent_gws"):
        assert key in thr


def test_moves_cap_is_enforced_and_disclosed(db, tmp_path):
    _seed_moves(db, tmp_path)
    brief = run_script("dashboard_brief", {}, db=db).result
    assert len(brief["moves"]) <= brief["thresholds"]["move_cap"]
    assert brief["moves_suppressed"] >= 0


def test_squad_projection_serves_p_appear_and_labels_xmins_absent(db, tmp_path):
    """Today's regime: p_appear covered, xmins not — the payload serves what
    exists and nulls what does not; nothing is fabricated."""
    _seed_moves(db, tmp_path)
    brief = run_script("dashboard_brief", {}, db=db).result
    rows = {r["code"]: r for r in brief["squad_projection"]}
    assert rows[120]["p_appear"] == pytest.approx(0.9)
    assert rows[120]["xmins"] is None
    assert brief["projection_gw"] == 3
    # squad players with no provider row carry nulls, not zeros
    assert rows[100]["p_appear"] is None and rows[100]["xmins"] is None


def test_team_fixtures_copies_the_boards_opponent_only_lens(db, tmp_path):
    """ANTI-DRIFT: team_fixtures is fixture_board's opponent_only lens copied
    field-for-field for the next fixture — labels CAPS home / lower away."""
    _seed_moves(db, tmp_path)
    brief = run_script("dashboard_brief", {}, db=db).result
    fb = run_script("fixture_board",
                    {"horizon": 3, "from_gw": 3, "include_form": False,
                     "include_calibration": False}, db=db).result
    fb_by = {t["team_code"]: t for t in fb["teams"]}
    for tf in brief["team_fixtures"]:
        src_team = fb_by[tf["team_code"]]
        first = next((o for slot in src_team["fixtures"]
                      for o in slot["opponents"]
                      if slot["gw"] == fb["gws"][0]), None)
        if first is None:
            assert tf["next"] is None
            continue
        assert tf["next"]["label"] == first["label"]
        oo = first["opponent_only"]
        assert tf["next"]["attack_ease"] == oo["attack_ease"]
        assert tf["next"]["defence_ease"] == oo["defence_ease"]
        assert tf["next"]["attack_rank"] == oo["attack_rank"]
    # home fixture labels are CAPS, away lower — the fixtures-tab convention
    gam = next(t for t in brief["team_fixtures"] if t["team_code"] == 3)
    assert gam["next"]["is_home"] and gam["next"]["label"].isupper()
    beta = next(t for t in brief["team_fixtures"] if t["team_code"] == 2)
    assert not beta["next"]["is_home"] and beta["next"]["label"].islower()


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
