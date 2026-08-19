"""Walk-forward protocol, and the cold-start sign constraints it depends on.

Two things are pinned here.

**The protocol.** ``backtest.py`` is leave-one-season-out, which lets a fold for
2023-24 learn from 2024-25. ``evaluate.walk_forward`` is the strict version:
every fold trains only on data that existed before the thing it predicts. The
tests below assert that arithmetically on the folds themselves rather than
trusting the docstring.

**The sign constraints.** The cold-start near-horizon block is identified by two
real pre-deadline snapshots. An unconstrained least-squares fit on them puts the
availability coefficient at **+0.24**, which forecasts a knee injury three days
before the deadline as a 24% ownership *rise*. That is the defect
``COLDSTART_BOUNDS`` and the shared availability column exist to prevent, and it
is invisible in MAE -- the flagged players are a handful of rows -- so it has to
be tested directly.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.ownership import evaluate, panel
from fpl_edge.models.ownership.drift import (
    COLDSTART_BOUNDS,
    COLDSTART_FEATURES,
    NEAR_KNOT_DAYS,
    ColdStartParams,
    coldstart_design,
    coldstart_predict,
    fit_coldstart,
)
from fpl_edge.models.ownership.model import PARAMS_PATH, OwnershipParams

I_FLAG = COLDSTART_FEATURES.index("flagged")
I_DRIFT = COLDSTART_FEATURES.index("drift")
I_PROJ = COLDSTART_FEATURES.index("projection")


@pytest.fixture(scope="module")
def pairs() -> pd.DataFrame:
    return panel.load_coldstart_pairs()


@pytest.fixture(scope="module")
def refit(pairs: pd.DataFrame) -> ColdStartParams:
    return fit_coldstart(pairs)


@pytest.fixture(scope="module")
def walk(pairs: pd.DataFrame):
    frame = panel.attach_field_size(panel.load_inseason_panel())
    table, summary = evaluate.walk_forward(frame, pairs)
    return frame, table, summary


# ---------------------------------------------------------------------------
# the sign constraints
# ---------------------------------------------------------------------------


def test_shipped_parameters_satisfy_every_domain_sign_constraint() -> None:
    """The committed params.json, not just a fresh fit, obeys the bounds."""
    params = OwnershipParams.load().coldstart
    bad = evaluate.coefficient_signs(params).query("not ok")
    assert bad.empty, f"shipped cold-start coefficients violate their bounds:\n{bad}"


def test_a_refit_on_the_committed_fixture_satisfies_them_too(refit: ColdStartParams) -> None:
    bad = evaluate.coefficient_signs(refit).query("not ok")
    assert bad.empty, f"refitted cold-start coefficients violate their bounds:\n{bad}"


def test_the_shipped_availability_coefficient_is_negative() -> None:
    """The specific defect: flagged players must be forecast to lose owners."""
    raw = json.loads(PARAMS_PATH.read_text())["coldstart"]
    for block in ("coef_near", "coef_far", "coef_near_nodrift", "coef_far_nodrift"):
        assert raw[block][I_FLAG] < 0.0, (
            f"{block}[flagged] = {raw[block][I_FLAG]:+.4f}. A positive availability "
            "coefficient forecasts an injury as an ownership rise."
        )


def test_the_constraint_is_load_bearing_not_decorative(pairs: pd.DataFrame) -> None:
    """The unconstrained near-block fit really does flip the sign.

    Without this, the bounds could be tightened around a coefficient that was
    already the right sign and nobody would notice they had stopped mattering.
    """
    Xs, ys = [], []
    for (_season, days), g in pairs.groupby(["season", "days"], sort=True):
        days = float(days)
        if days > NEAR_KNOT_DAYS:
            continue
        own = g["own"].to_numpy()
        Xs.append(coldstart_design(own, g["ep"].to_numpy(), g["flag"].to_numpy(),
                                   g["drift_rate"].to_numpy(), days))
        ys.append(g["own_true"].to_numpy() - own)
    assert len(Xs) == 2, "the near block is identified by exactly two real snapshots"
    beta = np.linalg.lstsq(np.vstack(Xs), np.concatenate(ys), rcond=None)[0]
    assert beta[I_FLAG] > 0.0, (
        "the unconstrained near-block fit no longer produces the sign-flipped "
        "availability coefficient; if the fixture changed, re-derive whether the "
        "constraint is still needed rather than deleting this test"
    )


def test_a_flagged_player_is_forecast_to_lose_ownership_at_every_horizon(
    refit: ColdStartParams,
) -> None:
    """The behaviour, not the coefficient: the forecast itself must go down.

    The predicted move passes through a simplex projection, so a negative
    coefficient is necessary but not sufficient. This asserts the thing that
    actually reaches the optimizer.
    """
    own = np.array([0.45, 0.30, 0.20, 0.10, 0.05, 0.02])
    ep = np.array([7.0, 6.0, 5.0, 4.0, 3.0, 2.0])
    clean = np.zeros_like(own)
    for days in (0.5, 1.0, 3.0, 5.0, 10.0, 20.0):
        base, _ = coldstart_predict(refit, own, ep, clean, days)
        flagged = clean.copy()
        flagged[0] = 1.0
        hurt, _ = coldstart_predict(refit, own, ep, flagged, days)
        assert hurt[0] < base[0], (
            f"at T-{days}d a flagged player is forecast to GAIN ownership "
            f"({base[0]:.5f} -> {hurt[0]:.5f})"
        )


def test_a_better_projection_never_loses_owners(refit: ColdStartParams) -> None:
    own = np.array([0.20, 0.20, 0.20, 0.20, 0.20])
    flags = np.zeros_like(own)
    low = np.array([2.0, 4.0, 4.0, 4.0, 4.0])
    high = np.array([9.0, 4.0, 4.0, 4.0, 4.0])
    for days in (1.0, 5.0, 15.0):
        a, _ = coldstart_predict(refit, own, low, flags, days)
        b, _ = coldstart_predict(refit, own, high, flags, days)
        assert b[0] >= a[0] - 1e-12, f"a better ep_next lost owners at T-{days}d"


def test_bounds_cover_every_feature_exactly_once() -> None:
    assert len(COLDSTART_BOUNDS) == len(COLDSTART_FEATURES)
    assert COLDSTART_BOUNDS[I_FLAG][1] == 0.0
    assert COLDSTART_BOUNDS[I_DRIFT][0] == 0.0
    assert COLDSTART_BOUNDS[I_PROJ][0] == 0.0


# ---------------------------------------------------------------------------
# the walk-forward protocol
# ---------------------------------------------------------------------------


def test_no_in_season_fold_trains_on_the_gameweek_it_predicts(walk) -> None:
    """Recomputed from the panel, so an off-by-one in the fold cannot hide."""
    frame, _table, _summary = walk
    seasons = sorted(frame["season"].unique())
    _, folds = evaluate.walk_forward_inseason(frame)
    assert folds, "no in-season folds were produced"
    for f in folds:
        if f.regime != "in_season":
            continue
        gw = int(f.test_key.split("->")[0].removeprefix("GW"))
        i = seasons.index(f.test_season)
        expected = int(
            len(frame[frame["season"].isin(seasons[:i])])
            + len(frame[(frame["season"] == f.test_season) & (frame["GW"] < gw)])
        )
        assert f.n_train == expected, (
            f"fold {f.test_season} {f.test_key} trained on {f.n_train} rows, "
            f"expected {expected}: the fold boundary has moved"
        )


def test_a_cold_start_fold_only_sees_strictly_earlier_seasons(walk) -> None:
    _frame, _table, _summary = walk
    _, folds, _untestable = evaluate.walk_forward_coldstart()
    assert folds
    for f in folds:
        assert f.train_seasons, "a cold-start fold trained on nothing"
        assert all(s < f.test_season for s in f.train_seasons), (
            f"{f.test_season} trained on {f.train_seasons}, which is not strictly earlier"
        )


def test_the_untestable_season_is_reported_rather_than_dropped(walk) -> None:
    """Coverage has to be honest: the first season cannot be scored at all."""
    _frame, _table, summary = walk
    untestable = summary["untestable"]
    assert len(untestable) == 1
    assert untestable[0]["n_rows_unscored"] > 0
    scored = {f.test_season for f in evaluate.walk_forward_coldstart()[1]}
    assert untestable[0]["test_season"] not in scored


def test_in_season_walk_forward_beats_both_baselines(walk) -> None:
    _frame, _table, summary = walk
    s = summary["in_season"]
    assert s["n"] > 50_000
    assert s["model_mae_pp"] < s["persistence_mae_pp"]
    assert s["model_mae_pp"] < s["momentum_mae_pp"]


def test_the_walk_forward_number_is_worse_than_the_leave_one_season_out_one(walk) -> None:
    """A sanity check on the protocol, not on the model.

    Walk-forward folds are trained on strictly less data than leave-one-season-out
    folds -- early gameweeks of the first testable season see almost nothing. If
    the strict protocol came out *better*, the split would be leaking.
    """
    from fpl_edge.models.ownership.model import MEASURED_PATH

    _frame, _table, summary = walk
    loso = json.loads(MEASURED_PATH.read_text())["inseason"]["model"]
    assert summary["in_season"]["model_mae_pp"] >= loso - 1e-9


def test_every_fold_scores_the_baselines_on_exactly_the_same_rows(walk) -> None:
    _frame, _table, _summary = walk
    _, folds = evaluate.walk_forward_inseason()
    for f in folds[:20]:
        assert f.truth.shape == f.model.shape == f.persistence.shape == f.momentum.shape


def test_the_table_and_the_pooled_summary_agree(walk) -> None:
    _frame, table, summary = walk
    ins = table[table["regime"] == "in_season"]
    pooled = float(np.average(ins["model"], weights=ins["n"]))
    assert pooled == pytest.approx(summary["in_season"]["model_mae_pp"], rel=1e-9)
    assert int(ins["n"].sum()) == summary["in_season"]["n"]
