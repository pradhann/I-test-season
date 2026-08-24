"""ownership_eo against a seeded warehouse: the season/gw trap, pinned.

The audit (docs/platform/data_audit.md Q6) found ``eo_top10k``/``eo_elite``
stored under season 2025-26 GW38 — last season's final state — while
``eo_predicted`` lives under the current season. The trap test here seeds a
*stale* ``eo_predicted`` row under the old season at a HIGHER gw than the
live one: a pivot that forgets the season filter picks the stale row (gw 38
beats gw 1) and quietly reports last season's EO as current. Verified to fail
when the season filter is removed from the script's live pivot.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers ownership_eo)
from fpl_edge.platform.registry import run_script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
T = pd.Timestamp("2026-08-01 12:00", tz="UTC")
SEASON = "2026-27"
OLD = "2025-26"


def _player(season, code, element_id, name, pos, team_code=1):
    return {"season": season, "code": code, "element_id": element_id,
            "web_name": name, "first_name": "A", "second_name": name,
            "position": pos, "team_code": team_code, "as_of": T}


def _state(season, code, element_id, own, price=100):
    return {"season": season, "code": code, "element_id": element_id,
            "price_tenths": price, "selected_by_pct": own, "status": "a",
            "chance_of_playing_next_round": None, "news": "",
            "news_added": None, "transfers_in_event": 0,
            "transfers_out_event": 0, "cost_change_start": 0, "as_of": T}


@pytest.fixture()
def empty_db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


@pytest.fixture()
def seeded_db(tmp_path):
    """Two current players, one old-season player, EO rows on both sides of
    the season split, and a two-source consensus for GW1."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 1, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": T},
    ]))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": False,
         "deadline_utc": pd.Timestamp("2099-08-14 17:30", tz="UTC"), "as_of": T},
    ]))
    wh.append("dim_player", pd.DataFrame([
        _player(SEASON, 100, 10, "Premium", 4),
        _player(SEASON, 200, 20, "Diff", 3),
        _player(OLD, 300, 30, "OldTemplate", 3),
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        _state(SEASON, 100, 10, 60.0, price=150),
        _state(SEASON, 200, 20, 4.0, price=70),
        _state(OLD, 300, 30, 45.0),
    ]))
    ins = ("INSERT INTO fact_external_ownership "
           "(provider, season, gw, code, metric, value, as_of) VALUES "
           "(?, ?, ?, ?, ?, ?, TIMESTAMPTZ '2026-08-01 12:00:00+00')")
    # live: the current season's predicted EO
    wh.sql(ins, ["livefpl", SEASON, 1, 100, "eo_predicted", 0.95])
    # stale: LAST SEASON's final state — including a stale eo_predicted for
    # the same player at gw 38, the row a season-blind pivot would pick.
    wh.sql(ins, ["livefpl", OLD, 38, 100, "eo_predicted", 0.20])
    wh.sql(ins, ["livefpl", OLD, 38, 300, "eo_top10k", 1.20])
    wh.sql(ins, ["livefpl", OLD, 38, 300, "eo_elite", 1.10])
    wh.sql(ins, ["livefpl", OLD, 38, 100, "eo_top10k", 0.50])
    pj = ("INSERT INTO fact_projection (provider, season, gw, code, xp, as_of) "
          "VALUES (?, ?, ?, ?, ?, TIMESTAMPTZ '2026-08-01 12:00:00+00')")
    for provider, code, xp in (("a", 100, 5.0), ("b", 100, 6.0),
                               ("a", 200, 4.4), ("b", 200, 4.6)):
        wh.sql(pj, [provider, SEASON, 1, code, xp])
    wh.close()
    return path


def run(db, **params):
    params.setdefault("coverage", False)  # tests never reach the network
    return run_script("ownership_eo", params, db=db).result


def test_empty_warehouse_is_an_honest_empty(empty_db):
    res = run(empty_db)
    assert res.get("empty") is True
    assert set(res) == {"empty", "reason"}
    assert "ingest" in res["reason"].lower()


def test_live_eo_ranks_the_template(seeded_db):
    res = run(seeded_db)
    assert res.get("empty") is not True
    top = res["rows"][0]
    assert top["code"] == 100 and top["name"] == "Premium"
    assert top["eo_pred_pct"] == 95.0          # 0.95 fraction -> percent
    assert top["own_pct"] == 60.0              # marginal, no captaincy
    assert top["xpts"] == 5.5                  # mean of the two sources
    assert res["xpts_gw"] == 1


def test_the_trap_stale_season_rows_never_enter_the_current_template(seeded_db):
    """A season-blind pivot would report the 2025-26 GW38 eo_predicted (20%)
    as current, because gw 38 out-sorts gw 1. It must not."""
    res = run(seeded_db)
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["eo_pred_pct"] == 95.0, (
        "the 2025-26 GW38 row leaked into the current template"
    )
    # the old-season-only player has no current-season identity at all
    assert 300 not in by_code
    assert all(r["code"] != 300 for r in res["differentials"])


def test_stale_metrics_are_quarantined_and_labelled(seeded_db):
    res = run(seeded_db)
    ls = res["last_season"]
    assert ls is not None
    assert ls["season"] == OLD and ls["gw"] == 38
    old = next(r for r in ls["rows"] if r["code"] == 300)
    assert old["name"] == "OldTemplate"
    assert old["eo_top10k_pct"] == 120.0 and old["eo_elite_pct"] == 110.0
    # gws_covered enumerates both sides of the split, flagged live/stale
    flags = {(c["metric"], c["season"]): c["live"] for c in res["gws_covered"]}
    assert flags[("eo_predicted", SEASON)] is True
    assert flags[("eo_top10k", OLD)] is False
    assert OLD in res["metrics_note"] and "never merged" in res["metrics_note"]


def test_differentials_are_low_owned_high_xpts(seeded_db):
    res = run(seeded_db, diff_max_own=15.0)
    codes = [r["code"] for r in res["differentials"]]
    assert codes == [200]                       # 4% owned, 4.5 xPts
    assert res["differentials"][0]["xpts"] == 4.5
    assert 100 not in codes                     # 60% owned is nobody's differential


def test_no_elite_picks_degrades_honestly(seeded_db):
    res = run(seeded_db)
    assert all(r["elite_own_pct"] is None for r in res["rows"])
    assert "no elite picks" in res["cohort_note"].lower()


def test_elite_picks_produce_a_real_cohort_column(seeded_db):
    from fpl_edge.ingest.rivals.schema import migrate

    wh = Warehouse(seeded_db)
    migrate(wh)
    wh.append("fact_manager_pick", pd.DataFrame([
        # entry 1: owns Premium as captain; entry 2: owns both, no armband
        {"entry_id": 1, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
         "multiplier": 2, "is_captain": True, "is_vice_captain": False, "as_of": T},
        {"entry_id": 2, "season": SEASON, "gw": 1, "element_id": 10, "slot": 1,
         "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
        {"entry_id": 2, "season": SEASON, "gw": 1, "element_id": 20, "slot": 2,
         "multiplier": 1, "is_captain": False, "is_vice_captain": False, "as_of": T},
    ]))
    wh.close()

    res = run(seeded_db)
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["elite_own_pct"] == 100.0
    assert by_code[100]["elite_eo_pct"] == 150.0    # (2 + 1) / 2 managers
    assert by_code[200]["elite_own_pct"] == 50.0
    assert "2 crawled managers" in res["cohort_note"]
    assert "GW1" in res["cohort_note"]


def test_squad_coverage_marks_owned_and_missing(seeded_db, monkeypatch):
    class FakeRouter:
        def __init__(self, wh, *, season, entry_id):
            pass

        def _team_state(self):
            return SimpleNamespace(
                picks=[SimpleNamespace(code=100)],
                provenance=SimpleNamespace(name="MANUAL"),
            )

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", FakeRouter)
    res = run(seeded_db, coverage=True)
    by_code = {r["code"]: r for r in res["rows"]}
    assert by_code[100]["in_squad"] is True
    assert by_code[200]["in_squad"] is False
    assert "MANUAL" in res["squad_note"]


def test_unreadable_squad_means_null_coverage_not_a_crash(seeded_db, monkeypatch):
    class ExplodingRouter:
        def __init__(self, *a, **k):
            raise ConnectionError("FPL API down")

    monkeypatch.setattr("fpl_edge.interfaces.qa.QuestionRouter", ExplodingRouter)
    res = run(seeded_db, coverage=True)
    assert all(r["in_squad"] is None for r in res["rows"])
    assert "unreadable" in res["squad_note"]
