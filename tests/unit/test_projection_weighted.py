"""The EARNED-WEIGHT consensus: sem_projection_consensus_weighted and the
projection_table ``weighting`` switch that serves it.

The unweighted macro is the reference blend and is never changed; these tests
pin the weighted one to exact arithmetic on a warehouse with known weights,
and pin the panel to naming which blend drove every row-bearing block.

Seed: three providers, two with weight (src_a 0.6, src_b 0.4), one measured
and found wanting (src_c, weight 0). Coverage is ragged on purpose:

  code 300 Palmer   a=2.0  b=4.0  c=8.0   weighted 2.8, all three present
  code 400 Jackson        b=5.5  c=6.0   weighted 5.5 (b alone carries weight)
  code 500 Zero                  c=1.0   no weighted provider: NULL, dropped
  code 100 Raya     a=3.0  b=3.2  c=3.4   weighted 3.08
"""

from __future__ import annotations

import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registration is the import)
from fpl_edge.eval.projection_scoring import N_OBS_FLOOR
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.platform.registry import run_script
from fpl_edge.store.warehouse import Warehouse

SEASON = "2026-27"
T0 = pd.Timestamp("2026-08-01 12:00", tz="UTC")
FIT_AT = pd.Timestamp("2026-08-16 03:30", tz="UTC")   # after GW1 settled
BEFORE_FIT = pd.Timestamp("2026-08-16 03:00", tz="UTC")
AFTER_FIT = pd.Timestamp("2026-08-16 04:00", tz="UTC")

PLAYERS = [
    (100, "Raya", 1, 1, 55, 20.0),
    (300, "Palmer", 3, 2, 105, 45.0),
    (400, "Jackson", 4, 2, 75, 12.0),
    (500, "Zero", 2, 1, 40, 1.0),
]
WEIGHTS = {"src_a": 0.6, "src_b": 0.4, "src_c": 0.0}
GW2_XP = {
    "src_a": {100: 3.0, 300: 2.0},
    "src_b": {100: 3.2, 300: 4.0, 400: 5.5},
    "src_c": {100: 3.4, 300: 8.0, 400: 6.0, 500: 1.0},
}
#: (provider, gw, mae, baseline, n_obs); src_c's GW1 cell sits under the floor
SCORES = [
    ("src_a", 1, 1.20, 1.30, 600),
    ("src_b", 1, 1.25, 1.30, 600),
    ("src_c", 1, 1.60, 1.30, 40),
]

#: The ragged seed adds two feeds the plain one has no room for, because the
#: fitted weight and the applied weight only differ when coverage is ragged:
#:   src_d  fitted weight 0.5, publishes GW2 and stops. At GW2 the blend
#:          renormalises over a+b+c+d = 1.5, so src_a's applied weight is
#:          0.4, not its fitted 0.6. At GW3 src_d is gone and src_a is 0.6.
#:   src_p  a p(appear)-only feed: rows with no xp at all, so it is in the
#:          fit table with nothing to score and is not a projection provider.
STALE_PROVIDER = "src_d"
STALE_WEIGHT = 0.5
STALE_GW2_XP = {100: 4.0, 300: 6.0, 400: 7.0}
PAPP_PROVIDER = "src_p"


def _seed(
    path,
    *,
    with_fit: bool = True,
    with_scores: bool = True,
    ragged: bool = False,
) -> None:
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
            rows.append({
                "provider": provider, "season": SEASON, "gw": 2, "code": code,
                "xp": xp, "xp_if_appears": None, "p_appear": None,
                "xmins": None, "as_of": T0,
            })
            # GW3 for the matrix: src_c does not look that far ahead
            if provider != "src_c":
                rows.append({
                    "provider": provider, "season": SEASON, "gw": 3,
                    "code": code, "xp": xp + 1.0, "xp_if_appears": None,
                    "p_appear": None, "xmins": None, "as_of": T0,
                })
    if ragged:
        for code, xp in STALE_GW2_XP.items():
            rows.append({
                "provider": STALE_PROVIDER, "season": SEASON, "gw": 2,
                "code": code, "xp": xp, "xp_if_appears": None,
                "p_appear": None, "xmins": None, "as_of": T0,
            })
        for code in STALE_GW2_XP:
            rows.append({
                "provider": PAPP_PROVIDER, "season": SEASON, "gw": 2,
                "code": code, "xp": None, "xp_if_appears": None,
                "p_appear": 0.9, "xmins": None, "as_of": T0,
            })
    frame = pd.DataFrame(rows)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    store.append("fact_projection", frame)
    if with_scores:
        score_rows = []
        for provider, gw, mae, base, n in SCORES:
            for metric, v, b in (("mae", mae, base), ("rmse", mae * 1.9, base * 1.9)):
                score_rows.append({
                    "provider": provider, "season": SEASON, "gw": gw,
                    "scope": "overall", "metric": metric, "value": v,
                    "baseline": b, "n_obs": n,
                    "deadline_utc": pd.Timestamp("2026-08-15 17:30", tz="UTC"),
                    "as_of": FIT_AT,
                })
        store.append("fact_projection_score", pd.DataFrame(score_rows))
    if with_fit:
        fitted = dict(WEIGHTS)
        obs = {p: next(n for pr, _, _, _, n in SCORES if pr == p) for p in WEIGHTS}
        if ragged:
            fitted[STALE_PROVIDER] = STALE_WEIGHT
            fitted[PAPP_PROVIDER] = 0.0
            obs[STALE_PROVIDER] = 600
            obs[PAPP_PROVIDER] = 0
        store.record_weights("test:invmse:thru-gw1", pd.DataFrame([
            {"provider": p, "weight": w,
             "loss": None if w == 0 else 1.0 / w, "loss_metric": "mse",
             "baseline_loss": 2.0,
             "n_obs": obs[p],
             "earned": w > 0, "holdout": "test holdout", "as_of": FIT_AT}
            for p, w in fitted.items()
        ]))
    wh.close()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    _seed(path)
    return path


@pytest.fixture()
def db_ragged(tmp_path):
    path = tmp_path / "ragged.duckdb"
    _seed(path, ragged=True)
    return path


@pytest.fixture()
def db_no_fit(tmp_path):
    path = tmp_path / "nofit.duckdb"
    _seed(path, with_fit=False, with_scores=False)
    return path


def _macro(path, instant) -> dict[int, dict]:
    wh = Warehouse.read_copy(path)
    try:
        df = wh.sql(
            "SELECT * FROM sem_projection_consensus_weighted(?) "
            "WHERE season = ? AND gw = 2 ORDER BY code",
            (instant.to_pydatetime(), SEASON),
        )
    finally:
        wh.close()
    return {int(r["code"]): r for _, r in df.iterrows()}


def _unweighted(path, instant) -> dict[int, dict]:
    wh = Warehouse.read_copy(path)
    try:
        df = wh.sql(
            "SELECT * FROM sem_projection_consensus(?) "
            "WHERE season = ? AND gw = 2 ORDER BY code",
            (instant.to_pydatetime(), SEASON),
        )
    finally:
        wh.close()
    return {int(r["code"]): r for _, r in df.iterrows()}


# -- the macro ---------------------------------------------------------------

def test_weighted_mean_is_sum_xw_over_sum_w_over_the_providers_present(db):
    rows = _macro(db, AFTER_FIT)
    palmer = rows[300]
    # (2.0*0.6 + 4.0*0.4) / (0.6 + 0.4); src_c's 8.0 carries weight 0
    assert float(palmer["xpts_mean"]) == pytest.approx(2.8, abs=1e-9)
    assert float(rows[100]["xpts_mean"]) == pytest.approx(
        (3.0 * 0.6 + 3.2 * 0.4) / 1.0, abs=1e-9)
    # only src_b carries weight for Jackson: the weights renormalise to b alone
    assert float(rows[400]["xpts_mean"]) == pytest.approx(5.5, abs=1e-9)
    assert int(rows[400]["n_sources"]) == 2
    assert int(rows[400]["n_weighted_sources"]) == 1


def test_a_zero_weight_provider_is_out_of_the_mean_but_in_the_disagreement(db):
    palmer = _macro(db, AFTER_FIT)[300]
    assert int(palmer["n_sources"]) == 3
    assert int(palmer["n_weighted_sources"]) == 2
    assert float(palmer["xpts_min"]) == 2.0 and float(palmer["xpts_max"]) == 8.0
    assert float(palmer["xpts_spread"]) == 6.0
    # sd over all three numbers, not the two weighted ones
    assert float(palmer["xpts_sd"]) == pytest.approx(
        pd.Series([2.0, 4.0, 8.0]).std(), abs=1e-9)


def test_a_player_only_a_zero_weight_provider_covers_has_a_null_not_a_mean(db):
    zero = _macro(db, AFTER_FIT)[500]
    assert int(zero["n_sources"]) == 1
    assert int(zero["n_weighted_sources"]) == 0
    assert pd.isna(zero["xpts_mean"]), (
        "with no earned provider the blend must be NULL, never src_c's number"
    )


def test_the_macro_names_the_fit_it_used(db):
    palmer = _macro(db, AFTER_FIT)[300]
    assert palmer["weights_fit_id"] == "test:invmse:thru-gw1"
    assert pd.Timestamp(palmer["weights_as_of"]) == FIT_AT


def test_before_the_fit_exists_nothing_is_weighted(db):
    rows = _macro(db, BEFORE_FIT)
    assert all(int(r["n_weighted_sources"]) == 0 for r in rows.values())
    assert all(pd.isna(r["xpts_mean"]) for r in rows.values())
    assert all(pd.isna(r["weights_fit_id"]) for r in rows.values())
    # the players are still there with their disagreement measured
    assert int(rows[300]["n_sources"]) == 3


def test_the_unweighted_macro_is_untouched(db):
    rows = _unweighted(db, AFTER_FIT)
    assert float(rows[300]["xpts_mean"]) == pytest.approx(14.0 / 3, abs=1e-9)
    assert float(rows[500]["xpts_mean"]) == 1.0
    assert "n_weighted_sources" not in rows[300].index
    assert "weights_as_of" not in rows[300].index


# -- the panel's weighting param --------------------------------------------

def _panel(db, **params):
    return run_script("projection_table", {"gw": 2, **params}, db=db).result


def _by_code(result):
    return {r["code"]: r for r in result["rows"]}


def test_equal_is_the_default_and_names_itself_in_every_block(db):
    res = _panel(db)
    assert res["weighting"] == "equal"
    assert res["weighting_requested"] == "equal"
    assert res["blocks_weighting"] == {
        "rows": "equal", "matrix": "equal", "by_team": "equal",
        "by_position": "equal", "detail": "raw"}
    rows = _by_code(res)
    assert rows[300]["xpts"] == pytest.approx(14.0 / 3, abs=1e-3)
    assert rows[300]["n_weighted_sources"] is None
    assert 500 in rows, "the equal view keeps a player only src_c covers"


def test_earned_serves_the_weighted_macro_and_drops_the_unweightable(db):
    res = _panel(db, weighting="earned")
    assert res["weighting"] == "earned"
    assert set(res["blocks_weighting"].values()) == {"earned", "raw"}
    rows = _by_code(res)
    assert rows[300]["xpts"] == pytest.approx(2.8, abs=1e-3)
    assert rows[300]["n_sources"] == 3
    assert rows[300]["n_weighted_sources"] == 2
    assert rows[400]["xpts"] == pytest.approx(5.5, abs=1e-3)
    assert 500 not in rows
    assert any("no provider with an earned weight" in n for n in res["notes"])
    # the matrix is the weighted blend too, at GW2 and GW3
    assert res["matrix"]["300"]["2"] == pytest.approx(2.8, abs=1e-3)
    assert res["matrix"]["300"]["3"] == pytest.approx(3.8, abs=1e-3)


def test_earned_with_a_subset_renormalises_over_the_subset(db):
    res = _panel(db, weighting="earned", sources=["src_a", "src_c"])
    rows = _by_code(res)
    assert rows[300]["xpts"] == pytest.approx(2.0, abs=1e-3)
    assert rows[300]["n_sources"] == 2 and rows[300]["n_weighted_sources"] == 1
    assert res["weighting"] == "earned"


def test_a_single_source_is_raw_whatever_weighting_was_asked(db):
    res = _panel(db, weighting="earned", source="src_c")
    assert res["weighting"] == "single_source"
    assert res["weighting_requested"] == "earned"
    assert _by_code(res)[300]["xpts"] == 8.0
    assert any("does not apply to a single source" in n for n in res["notes"])


def test_an_unknown_weighting_is_rejected_at_the_door(db):
    with pytest.raises(Exception, match="weighting"):
        run_script("projection_table", {"gw": 2, "weighting": "mean"}, db=db)


# -- the weights table and the accuracy strip --------------------------------

def test_the_weights_block_carries_the_evidence(db):
    w = _panel(db)["weights"]     # served under the equal view too
    assert w["fit_id"] == "test:invmse:thru-gw1"
    assert w["scored_gws"] == [1]
    assert w["n_floor"] == N_OBS_FLOOR
    assert pd.Timestamp(w["as_of"]) == FIT_AT
    by = {r["provider"]: r for r in w["rows"]}
    assert [r["provider"] for r in w["rows"]] == ["src_a", "src_b", "src_c"]
    assert by["src_a"] == {
        "provider": "src_a", "weight": 0.6, "n_obs": 600, "mae": 1.2,
        "baseline_mae": 1.3, "loss": pytest.approx(1.667, abs=1e-3),
        "baseline_loss": 2.0, "earned": True, "holdout": "test holdout",
        "applied_weight": 0.6, "covers_anchor": True, "publishes_xpts": True}
    assert by["src_c"]["weight"] == 0.0 and by["src_c"]["earned"] is False
    assert by["src_c"]["mae"] == 1.6 and by["src_c"]["baseline_mae"] == 1.3


# -- applied weights: what the visible columns actually blend with -----------

def test_the_applied_weight_renormalises_over_the_gameweeks_providers(db_ragged):
    w = _panel(db_ragged)["weights"]
    assert w["anchor_gw"] == 2
    by = {r["provider"]: r for r in w["rows"]}
    # the fit is untouched; the applied weight divides by the fitted weights
    # of the four providers with xPts at GW2 (0.6 + 0.4 + 0.0 + 0.5)
    assert by["src_a"]["weight"] == 0.6
    assert by["src_a"]["applied_weight"] == pytest.approx(0.6 / 1.5, abs=1e-3)
    assert by[STALE_PROVIDER]["applied_weight"] == pytest.approx(
        0.5 / 1.5, abs=1e-3)
    assert by["src_c"]["applied_weight"] == 0.0, (
        "a fitted weight of 0 stays 0 however the rest renormalise"
    )
    assert sum(w["applied_by_gw"]["2"].values()) == pytest.approx(1.0, abs=1e-3)


def test_a_provider_absent_from_a_gameweek_lifts_the_rest_there(db_ragged):
    w = _panel(db_ragged)["weights"]
    # src_d and src_c publish GW2 only, so GW3 renormalises over src_a+src_b
    assert set(w["applied_by_gw"]["2"]) == {"src_a", "src_b", STALE_PROVIDER}
    assert w["applied_by_gw"]["3"] == {
        "src_a": pytest.approx(0.6, abs=1e-3),
        "src_b": pytest.approx(0.4, abs=1e-3)}
    assert (w["applied_by_gw"]["3"]["src_a"]
            > w["applied_by_gw"]["2"]["src_a"]), (
        "one static weight cannot describe columns built from two mixes"
    )


def test_a_provider_that_misses_the_anchor_carries_none_of_that_column(db_ragged):
    w = run_script(
        "projection_table", {"gw": 3}, db=db_ragged).result["weights"]
    assert w["anchor_gw"] == 3
    by = {r["provider"]: r for r in w["rows"]}
    assert by[STALE_PROVIDER]["weight"] == STALE_WEIGHT
    assert by[STALE_PROVIDER]["applied_weight"] == 0.0
    assert by[STALE_PROVIDER]["covers_anchor"] is False
    assert by["src_a"]["applied_weight"] == pytest.approx(0.6, abs=1e-3)
    assert by["src_a"]["covers_anchor"] is True


def test_a_subset_renormalises_the_applied_weight_over_the_subset(db_ragged):
    w = _panel(db_ragged, sources=["src_a", STALE_PROVIDER])["weights"]
    by = {r["provider"]: r for r in w["rows"]}
    assert by["src_a"]["applied_weight"] == pytest.approx(0.6 / 1.1, abs=1e-3)
    assert by[STALE_PROVIDER]["applied_weight"] == pytest.approx(
        0.5 / 1.1, abs=1e-3)
    assert by["src_b"]["applied_weight"] == 0.0
    assert by["src_b"]["covers_anchor"] is False


def test_a_feed_with_no_xpts_is_flagged_as_no_projection_provider(db_ragged):
    res = _panel(db_ragged)
    by = {r["provider"]: r for r in res["weights"]["rows"]}
    assert by[PAPP_PROVIDER]["publishes_xpts"] is False
    assert by[PAPP_PROVIDER]["n_obs"] == 0
    assert all(by[p]["publishes_xpts"]
               for p in ("src_a", "src_b", "src_c", STALE_PROVIDER))
    assert not any(m["source"] == PAPP_PROVIDER for m in res["source_meta"]), (
        "source_meta lists feeds with xPts, so the flag agrees with it"
    )
    assert PAPP_PROVIDER not in res["weights"]["applied_by_gw"]["2"]


def test_the_accuracy_block_is_per_provider_per_scored_gw_with_the_floor(db):
    acc = _panel(db)["provider_accuracy"]
    assert acc["scope"] == "overall"
    assert acc["scored_gws"] == [1]
    assert acc["n_floor"] == N_OBS_FLOOR
    cells = {(r["provider"], r["gw"]): r for r in acc["rows"]}
    assert set(cells) == {("src_a", 1), ("src_b", 1), ("src_c", 1)}
    a = cells[("src_a", 1)]
    assert a["mae"] == 1.2 and a["baseline_mae"] == 1.3 and a["n_obs"] == 600
    assert a["rmse"] == pytest.approx(2.28, abs=1e-3)
    assert a["meets_floor"] is True
    assert cells[("src_c", 1)]["meets_floor"] is False, (
        "40 observations sit under the floor and must say so"
    )


def test_no_fit_yet_is_an_absent_table_not_an_invented_one(db_no_fit):
    res = _panel(db_no_fit)
    assert res["weights"] is None
    assert res["provider_accuracy"] == {
        "scope": "overall", "scored_gws": [], "n_floor": N_OBS_FLOOR, "rows": []}
    earned = _panel(db_no_fit, weighting="earned")
    assert earned["empty"] is True
    assert "earned weight" in earned["reason"]
