"""One effective-ownership definition, pinned across all three call sites.

Effective ownership was computed in three places that disagreed:

* ``sem_elite_ownership`` in ``store/views.sql`` summed the stored FPL
  multipliers over the cohort;
* ``CohortRates.eo()`` added ``start_share + captain_share`` plus the ``3xc``
  chip rate *spread over the captain distribution*, because the per-player
  triple-captain vector was thought unknowable;
* the ``ownership_eo`` panel ran its own query over ``fact_manager_pick`` with
  **no cohort filter at all** and labelled the blended result "elite".

Three numbers, one name. The refactor collapsed them onto the multiplier sum;
this module is the guard rail that stops them drifting apart again, so it
compares the SQL, the model and the panel against each other on one warehouse
rather than against three hand-written constants.

The definition, for the record::

    ownership = sum over m of weight[m] holding p         / sum of weight[all]
    eo        = sum over m of weight[m] * multiplier[m,p] / sum of weight[all]
    captaincy = sum over m of weight[m] captaining p      / sum of weight[all]

Weights are all 1 until the per-manager weight vector arrives, so every
denominator here is the cohort's manager count.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers ownership_eo)
from fpl_edge.models.field.cohorts import measure_cohort
from fpl_edge.models.field.observed import load_observed_squads, resolve_cohorts
from fpl_edge.platform.registry import run_script
from tests.unit.field_fixtures import SEASON, T_DECIDE, build_warehouse, toy

UTC = dt.timezone.utc


def _sql_eo(wh, cohort: str, universe) -> tuple[np.ndarray, int]:
    """The semantic layer's EO as a (P,) vector, plus its denominator."""
    df = wh.sql(
        """
        SELECT code, n_managers, own_pct, captain_pct, eo_pct
        FROM sem_elite_ownership(?)
        WHERE season = ? AND gw = 1 AND cohort = ? AND code IS NOT NULL
        """,
        [T_DECIDE, SEASON, cohort],
    )
    eo = np.zeros(universe.n_players)
    for _, r in df.iterrows():
        eo[universe.index_of(int(r["code"]))] = float(r["eo_pct"]) / 100.0
    return eo, int(df.iloc[0]["n_managers"])


@pytest.fixture()
def clean(tmp_path):
    """A warehouse whose every stored squad is complete.

    ``with_malformed`` is off deliberately: the SQL macro counts every entry
    with a stored pick row, while the Python loader drops a squad that fails
    15-slot validation. The two denominators are equal exactly when the crawl
    is complete — the state of the live warehouse — and
    ``test_the_only_disagreement_is_a_squad_the_loader_refused`` pins the one
    case where they are not.
    """
    wh, universe, meta = build_warehouse(
        tmp_path, n_managers=8, top1k_manager=2, with_malformed=False
    )
    return wh, universe, meta


def test_the_sql_macro_and_the_cohort_model_agree_on_eo(clean):
    """views.sql and models/field/cohorts.py, same warehouse, same numbers."""
    wh, universe, _ = clean
    snap = wh.snapshot_at(T_DECIDE)

    for cohort, expected_n in (("elite", 7), ("top1k", 1)):
        rates = measure_cohort(snap, SEASON, universe, cohort)
        sql_eo, sql_n = _sql_eo(wh, cohort, universe)
        assert rates.n_managers == expected_n == sql_n, (
            f"{cohort}: the two layers disagree on the DENOMINATOR"
        )
        np.testing.assert_allclose(
            rates.eo(), sql_eo, atol=1e-9,
            err_msg=f"{cohort}: SQL and CohortRates disagree on effective ownership",
        )
        # ...and on the two shares EO is kept separate from.
        own = wh.sql(
            "SELECT code, own_pct, captain_pct FROM sem_elite_ownership(?) "
            "WHERE season = ? AND gw = 1 AND cohort = ? AND code IS NOT NULL",
            [T_DECIDE, SEASON, cohort],
        )
        for _, r in own.iterrows():
            p = universe.index_of(int(r["code"]))
            assert rates.ownership[p] == pytest.approx(float(r["own_pct"]) / 100.0)
            assert rates.captain_share[p] == pytest.approx(
                float(r["captain_pct"]) / 100.0
            )


def test_the_panel_reports_the_same_eo_as_the_macro(clean, tmp_path):
    """The panel is a reader of the definition, never a second implementation."""
    wh, universe, _ = clean
    path = wh.path
    wh.close()

    res = run_script(
        "ownership_eo", {"season": SEASON, "cohort": "elite", "limit": 200,
                         "coverage": False}, db=path,
    ).result
    assert res.get("empty") is not True

    wh2 = type(wh)(path)
    snap = wh2.snapshot_at(dt.datetime.now(UTC))
    rates = measure_cohort(snap, SEASON, universe, "elite")
    assert res["cohort"] == "elite"
    assert res["cohort_n"] == rates.n_managers == 7

    eo = rates.eo()
    reported = {r["code"]: r for r in res["rows"] if r["elite_eo_pct"] is not None}
    assert reported, "the panel reported no cohort EO at all"
    for code, row in reported.items():
        p = universe.index_of(int(code))
        assert row["elite_eo_pct"] == pytest.approx(eo[p] * 100.0, abs=0.05), (
            f"panel and model disagree on EO for code {code}"
        )
        assert row["elite_own_pct"] == pytest.approx(
            rates.ownership[p] * 100.0, abs=0.05
        )
    # Every player the cohort actually holds is reported, none invented.
    assert {universe.index_of(int(c)) for c in reported} == set(
        np.flatnonzero(rates.ownership > 0)
    )
    wh2.close()


def test_eo_is_the_multiplier_sum_not_the_chip_rate_reconstruction(clean):
    """The old model formula and the canonical one are not the same number.

    One of the eight fixture managers plays Triple Captain, and the fixture
    writes the multiplier the API would return (3). The retired formula spread
    the cohort's 3xc *rate* over its whole captain distribution, which charges
    every captained player a fraction of a chip nobody played on them.
    """
    wh, universe, _ = clean
    snap = wh.snapshot_at(T_DECIDE)
    rates = measure_cohort(snap, SEASON, universe, "elite")

    retired = (rates.start_share + rates.captain_share
               + rates.captain_share * float(rates.chip_rates.get("3xc", 0.0)))
    assert not np.allclose(rates.eo(), retired), (
        "the two formulas coincide here, so this fixture cannot tell them apart"
    )
    # The canonical one is exact: total EO units divided by managers.
    observed, _ = load_observed_squads(snap, SEASON, universe, "elite")
    assert rates.eo().sum() == pytest.approx(
        observed.multipliers.sum() / observed.n
    )
    # ...and a benched player carries ownership without scoring exposure.
    benched = np.flatnonzero(rates.ownership > rates.start_share)
    assert benched.size, "no fixture squad benched anyone"
    assert (rates.eo()[benched] < rates.ownership[benched]).any()


def test_the_python_and_sql_cohort_rules_assign_the_same_managers(tmp_path):
    """B8: one manager, two crawls, one cohort — in both languages."""
    wh, universe, meta = build_warehouse(
        tmp_path, n_managers=8, top1k_manager=2, with_malformed=False
    )
    # Entry 102 is already a top1k pick; give it a curated source too, exactly
    # the overlap that used to put 17 live entries in both denominators.
    wh.append("dim_manager", pd.DataFrame([{
        "entry_id": 102, "player_name": "M2", "entry_name": "Team 2",
        "region": None, "years_active": None, "favourite_team_id": None,
        "started_event": 1, "source": "elite_list",
        "as_of": T_DECIDE - dt.timedelta(days=1),
    }]))
    snap = wh.snapshot_at(T_DECIDE)

    python_side = resolve_cohorts(snap)
    sql_side = {
        int(r["entry_id"]): str(r["cohort"])
        for _, r in wh.sql(
            "SELECT entry_id, cohort FROM sem_manager_cohort(?)", [T_DECIDE]
        ).iterrows()
    }
    assert python_side == sql_side, "the two cohort rules classify differently"
    assert python_side[102] == "top1k", "curation outranked the standings sample"

    # And the denominators stay disjoint and exhaustive.
    elite, _ = load_observed_squads(snap, SEASON, universe, "elite")
    top1k, _ = load_observed_squads(snap, SEASON, universe, "top1k")
    assert set(elite.entry_ids).isdisjoint(top1k.entry_ids)
    assert elite.n + top1k.n == 8
    for cohort, sample in (("elite", elite), ("top1k", top1k)):
        _, sql_n = _sql_eo(wh, cohort, universe)
        assert sql_n == sample.n


def test_the_only_disagreement_is_a_squad_the_loader_refused(tmp_path):
    """A 14-pick squad: SQL counts it, the loader refuses it, and says so.

    Pinned because it is the ONE way the two denominators can differ, and a
    silent difference here would look exactly like the drift this module
    exists to catch.
    """
    wh, universe, meta = build_warehouse(
        tmp_path, n_managers=8, top1k_manager=2, with_malformed=True
    )
    snap = wh.snapshot_at(T_DECIDE)
    observed, note = load_observed_squads(snap, SEASON, universe, "elite")
    _, sql_n = _sql_eo(wh, "elite", universe)

    assert observed.dropped == 1 and "1 dropped" in note
    assert sql_n == observed.n + observed.dropped, (
        "the difference between the two denominators is not fully explained"
    )
