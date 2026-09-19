"""planner_grid: honest empties, and the payload maths the grid trusts.

The planner's client-side arithmetic (FTs, hits, bank) is driven entirely by
this payload, so the pins here are the load-bearing ones: rule values come
from the verified registry, consensus means/spreads match the source numbers,
candidates exclude the squad, and an empty warehouse yields an empty panel
that explains itself -- never a plausible grid.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers everything)
from fpl_edge.platform.registry import run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
SEASON = "2026-27"
STAMP = pd.Timestamp("2026-08-01", tz="UTC")

# 2 GKP, 5 DEF, 5 MID, 3 FWD -- a legal 15.
SQUAD_POSITIONS = [1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4]
SQUAD_CODES = list(range(100, 115))
CAND_CODES = [200, 201, 202]          # two MIDs and a FWD outside the squad
CAND_POSITIONS = {200: 3, 201: 3, 202: 4}


@pytest.fixture()
def empty_db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


def _fake_state(monkeypatch, *, picks="squad", bank=15, ft=2, gw=2):
    """Pin the squad source so the test never touches the network."""
    from fpl_edge.interfaces.qa import QuestionRouter

    if picks == "squad":
        picks = [SimpleNamespace(code=c, is_starter=i < 11, is_captain=(i == 7),
                                 is_vice=(i == 8), multiplier=2 if i == 7 else 1)
                 for i, c in enumerate(SQUAD_CODES)]
    state = SimpleNamespace(picks=picks, gw=gw, bank_tenths=bank,
                            free_transfers=ft,
                            provenance=SimpleNamespace(name="MANUAL"))
    monkeypatch.setattr(QuestionRouter, "_team_state", lambda self: state)
    return state


@pytest.fixture()
def seeded_db(tmp_path):
    """Players, prices, deadlines and two projection providers for GW2-3."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": STAMP},
    ]))
    codes = SQUAD_CODES + CAND_CODES
    positions = dict(zip(SQUAD_CODES, SQUAD_POSITIONS)) | CAND_POSITIONS
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": c, "element_id": c, "web_name": f"P{c}",
         "first_name": "A", "second_name": "B", "position": positions[c],
         "team_code": 1, "as_of": STAMP}
        for c in codes
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": c, "element_id": c, "price_tenths": 40 + c % 100,
         "selected_by_pct": 5.0, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": STAMP}
        for c in codes
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": True,
         "deadline_utc": pd.Timestamp("2026-07-01 17:30", tz="UTC"), "as_of": STAMP},
        {"season": SEASON, "gw": 2, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-28 17:30", tz="UTC"), "as_of": STAMP},
        {"season": SEASON, "gw": 3, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-09-04 17:30", tz="UTC"), "as_of": STAMP},
    ]))
    # Two providers who disagree: consensus mean and spread are both pinned.
    proj = []
    for gw in (2, 3):
        for c in codes:
            base = (c % 100) / 10.0 + gw          # deterministic, distinct
            proj.append({"provider": "alpha", "season": SEASON, "gw": gw,
                         "code": c, "xp": base, "xp_if_appears": base,
                         "p_appear": 0.9, "xmins": 80.0, "as_of": STAMP})
            proj.append({"provider": "beta", "season": SEASON, "gw": gw,
                         "code": c, "xp": base + 1.0, "xp_if_appears": base + 1.0,
                         "p_appear": 0.9, "xmins": 80.0, "as_of": STAMP})
    # fact_projection is the projections team's table: appended through their
    # store (it owns those PIT keys), not Warehouse.append.
    from fpl_edge.ingest.projections.store import ProjectionStore

    ProjectionStore(wh).append("fact_projection", pd.DataFrame(proj))
    wh.close()
    return path


def test_empty_warehouse_is_an_honest_empty(empty_db):
    run = run_script("planner_grid", {}, db=empty_db)
    assert run.result.get("empty") is True
    assert set(run.result) == {"empty", "reason"}
    assert "ingest" in run.result["reason"].lower()


def test_players_without_projections_is_empty_and_says_so(seeded_db, tmp_path, monkeypatch):
    path = tmp_path / "noproj.duckdb"
    wh = Warehouse(path)
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": 100, "element_id": 100, "web_name": "P100",
         "first_name": "A", "second_name": "B", "position": 3, "team_code": 1,
         "as_of": STAMP}]))
    wh.append("fact_player_state", pd.DataFrame([
        {"season": SEASON, "code": 100, "element_id": 100, "price_tenths": 50,
         "selected_by_pct": 5.0, "status": "a",
         "chance_of_playing_next_round": None, "news": "", "news_added": None,
         "transfers_in_event": 0, "transfers_out_event": 0,
         "cost_change_start": 0, "as_of": STAMP}]))
    wh.close()
    run = run_script("planner_grid", {}, db=path)
    assert run.result.get("empty") is True
    assert "projection" in run.result["reason"].lower()


def test_unreadable_squad_is_empty_not_a_crash(seeded_db, monkeypatch):
    from fpl_edge.interfaces.qa import QuestionRouter

    def boom(self):
        raise RuntimeError("auth expired")

    monkeypatch.setattr(QuestionRouter, "_team_state", boom)
    run = run_script("planner_grid", {}, db=seeded_db)
    assert run.result.get("empty") is True
    assert "auth expired" in run.result["reason"]


def test_no_picks_yet_is_empty(seeded_db, monkeypatch):
    _fake_state(monkeypatch, picks=None)
    run = run_script("planner_grid", {}, db=seeded_db)
    assert run.result.get("empty") is True
    assert "setsquad" in run.result["reason"].lower()


def test_payload_shape_and_numbers(seeded_db, monkeypatch):
    _fake_state(monkeypatch, bank=15, ft=2)
    run = run_script("planner_grid", {"horizon": 5}, db=seeded_db)
    res = run.result
    assert res.get("empty") is not True

    # Horizon clamps to the projected gameweeks and says so.
    assert res["gws"] == [2, 3]
    assert any("clamp" in n.lower() for n in res["notes"])
    assert res["deadline_utc"].startswith("2099-08-28T17:30")

    # Squad: all 15, priced from fact_player_state, captain carried.
    assert [p["code"] for p in res["squad"]] == SQUAD_CODES
    p100 = res["squad"][0]
    assert p100["name"] == "P100" and p100["pos"] == "GKP"
    assert p100["price"] == pytest.approx(4.0)       # (40 + 100 % 100) tenths
    assert [p["code"] for p in res["squad"] if p["is_captain"]] == [107]

    # Candidates: never a squad member, ranked by summed consensus mean.
    cand_codes = [c["code"] for c in res["candidates"]]
    assert set(cand_codes).isdisjoint(SQUAD_CODES)
    assert cand_codes == [202, 201, 200]             # higher code -> higher xp
    assert res["candidates"][0]["pos"] == "FWD"
    assert res["candidates"][0]["own_pct"] == 5.0

    # Consensus maths: mean of the two providers, spread = max - min.
    # code 100, gw 2: alpha 2.0, beta 3.0 -> mean 2.5, spread 1.0.
    assert res["xpts"]["100"]["2"] == pytest.approx(2.5)
    assert res["spread"]["100"]["2"] == pytest.approx(1.0)
    assert res["xpts"]["100"]["3"] == pytest.approx(3.5)

    # The grid's arithmetic inputs come from the state and the verified rules.
    assert res["ft_entering"] == 2
    assert res["bank_tenths"] == 15
    assert res["rules"] == {"free_per_gw": 1, "max_banked": 5, "hit_cost": 4}


def test_ft_entering_is_clamped_to_the_banking_cap(seeded_db, monkeypatch):
    _fake_state(monkeypatch, ft=9)
    run = run_script("planner_grid", {}, db=seeded_db)
    assert run.result["ft_entering"] == 5


def test_result_schema_does_not_also_match_the_empty_shape():
    import jsonschema

    from fpl_edge.platform.scripts.planner import RESULT

    validator = jsonschema.Draft202012Validator(RESULT)
    assert list(validator.iter_errors({"empty": True, "reason": "nothing"})), (
        "the real result schema must reject the {empty, reason} shape, or the "
        "registry's oneOf becomes ambiguous"
    )


def test_horizon_one_returns_a_single_gameweek(seeded_db, monkeypatch):
    _fake_state(monkeypatch)
    run = run_script("planner_grid", {"horizon": 1}, db=seeded_db)
    assert run.result["gws"] == [2]
    assert "2" in run.result["xpts"]["100"]
    assert "3" not in run.result["xpts"]["100"]


def test_the_payload_is_strict_json_never_nan(seeded_db, monkeypatch):
    """`NaN or 0` keeps NaN (NaN is truthy), and starlette's json.dumps then
    500s the whole panel. The payload must serialise with allow_nan=False.
    This exact failure took the panel down live: NaN minutes in the metrics."""
    import json

    _fake_state(monkeypatch, bank=15, ft=2)
    result = run_script("planner_grid", {"horizon": 5}, db=seeded_db).result
    json.dumps(result, allow_nan=False)   # raises ValueError on any NaN/inf


# ------------------------------------------------ where xG actually comes from
# The panel used to read xG and xA from the third party's per-match feed. That
# feed writes a row per player-match and leaves an event column NULL when the
# count is zero, so xG was NULL for 252 of the 400 GW1 rows: everyone who took
# no shot. Reading those NULLs as unknown blanked the column for 410 of 654
# players while the OFFICIAL number, complete in every settled gameweek, sat
# one table away. NULL-means-zero is not an assumption either: summed that way
# the publisher's goals reconcile with the official settled goals for 400 of
# 400 GW1 players, exactly.


def _seed_returns(db, *, official, third_party):
    with Warehouse(db) as wh:
        wh.append("fact_player_fixture", pd.DataFrame([
            {"season": SEASON, "code": c, "fixture_id": 1, "gw": 1,
             "minutes": 90, "goals_scored": 0, "assists": 0, "clean_sheets": 0,
             "goals_conceded": 0, "own_goals": 0, "penalties_saved": 0,
             "penalties_missed": 0, "yellow_cards": 0, "red_cards": 0,
             "saves": 0, "bonus": 0, "bps": 0, "starts": 1, "tackles": 0,
             "clearances_blocks_interceptions": 0, "recoveries": 0,
             "defensive_contribution": 0, "expected_goals": xg,
             "expected_assists": xa, "expected_goals_conceded": 0.0,
             "total_points": 2, "was_home": True, "as_of": STAMP}
            for c, xg, xa in official
        ]))
        wh.append("fact_player_match_stats", pd.DataFrame([
            {"source": "fpl_core_insights", "season": SEASON, "code": c,
             "match_id": "m1", "tournament": "Premier League", "gw": 1,
             "minutes_played": 90.0, "start_min": 0.0, "finish_min": 90.0,
             "goals": None, "assists": None, "penalties_scored": None,
             "penalties_missed": None, "total_shots": shots,
             "shots_on_target": None, "xg": None, "xa": None, "xgot": None,
             "chances_created": None, "touches_opposition_box": None,
             "tackles": None, "tackles_won": None, "interceptions": None,
             "recoveries": None, "blocks": None, "clearances": None,
             "defensive_contributions": None, "saves": None,
             "goals_conceded": None, "xgot_faced": None,
             "goals_prevented": None, "as_of": STAMP}
            for c, shots in third_party
        ]))


def test_xg_comes_from_the_official_feed_and_is_never_blank_for_a_settled_gw(
        seeded_db, monkeypatch):
    _fake_state(monkeypatch)
    a, b = SQUAD_CODES[0], SQUAD_CODES[1]
    _seed_returns(
        seeded_db,
        # b took no shot, so the official xG for b is a real 0.0
        official=[(a, 0.31, 0.12), (b, 0.0, 0.04)],
        # the third party records a shot count only for a
        third_party=[(a, 3.0), (b, None)],
    )
    m = run_script("planner_grid", {}, db=seeded_db).result["metrics"]
    assert m[str(a)]["xg"] == 0.31 and m[str(a)]["xa"] == 0.12
    assert m[str(b)]["xg"] == 0.0, (
        "a settled player with no shot has xG 0, not unknown"
    )
    assert m[str(b)]["xa"] == 0.04


def test_a_missing_shot_count_is_zero_for_a_player_the_feed_covers(
        seeded_db, monkeypatch):
    """The publisher omits the column rather than writing 0, so a player it
    has a row for took no shot. A player it has no row for is unknown."""
    _fake_state(monkeypatch)
    a, b, c = SQUAD_CODES[0], SQUAD_CODES[1], SQUAD_CODES[2]
    _seed_returns(seeded_db,
                  official=[(a, 0.3, 0.1), (b, 0.0, 0.0), (c, 0.0, 0.0)],
                  third_party=[(a, 2.0), (b, None)])
    m = run_script("planner_grid", {}, db=seeded_db).result["metrics"]
    assert m[str(a)]["shots"] == 2.0
    assert m[str(b)]["shots"] == 0.0, "covered by the feed, took no shot"
    assert m[str(c)]["shots"] is None, (
        "settled officially but absent from the shot feed: unknown, not zero"
    )


def test_the_metrics_note_names_the_source_of_each_column(seeded_db,
                                                          monkeypatch):
    _fake_state(monkeypatch)
    _seed_returns(seeded_db, official=[(SQUAD_CODES[0], 0.3, 0.1)],
                  third_party=[(SQUAD_CODES[0], 1.0)])
    note = run_script("planner_grid", {}, db=seeded_db).result["metrics_note"]
    assert "official settled returns" in note
    assert "shots" in note and "FPL-Core-Insights" in note


# -- whose team the grid is about -------------------------------------------


def test_the_grid_takes_no_entry_id_param_and_reads_the_context(seeded_db,
                                                                monkeypatch):
    """``entry_id`` left the params schema. A request body cannot name a team,
    so the id and the FPL login always come from the same object."""
    from fpl_edge.platform.registry import ParamsInvalid, script
    from fpl_edge.platform.users import Identity, context_for, owner_context

    assert "entry_id" not in (
        script("planner_grid").params_schema.get("properties") or {})
    with pytest.raises(ParamsInvalid):
        run_script("planner_grid", {"entry_id": 7}, db=seeded_db)

    _fake_state(monkeypatch)
    seen = []

    from fpl_edge.interfaces.qa import QuestionRouter

    real = QuestionRouter.__init__

    def spy(self, wh, *, season, entry_id=None, user=None):
        seen.append((entry_id, None if user is None else user.user_id))
        real(self, wh, season=season, entry_id=entry_id, user=user)

    monkeypatch.setattr(QuestionRouter, "__init__", spy)

    run_script("planner_grid", {}, db=seeded_db)
    assert seen[-1] == (owner_context().entry_id, "owner")

    other = context_for(Identity(user_id="abc123", entry_id=4242))
    run_script("planner_grid", {}, db=seeded_db, ctx=other)
    assert seen[-1] == (4242, "abc123")


def test_a_user_with_no_login_gets_the_named_gap_not_the_owners_squad(
        seeded_db, monkeypatch):
    from fpl_edge.platform.users import PRIVATE_GAP, Identity, context_for

    _fake_state(monkeypatch, picks=None)
    other = context_for(Identity(user_id="abc123", entry_id=4242))
    res = run_script("planner_grid", {}, db=seeded_db, ctx=other).result
    assert res.get("empty") is True
    assert PRIVATE_GAP in res["reason"]
    assert "4242" in res["reason"]
