"""Dixon-Coles fit: gradient, recovery, contract compliance, and leakage.

Everything runs offline from the committed synthetic league in
``tests/fixtures/team_goals`` with fixed seeds, so these assertions do not
depend on the historical-data load having landed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import TEAM_STRENGTH_COLUMNS
from fpl_edge.models.team_goals.base import UnknownFixtureError
from fpl_edge.models.team_goals.data import (
    InsufficientHistoryError,
    read_finished_matches,
    read_target_fixtures,
)
from fpl_edge.models.team_goals.dixon_coles import (
    DixonColesModel,
    _decay_weights,
    _objective,
)
from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.promoted import build_team_priors, fit_promoted_prior
from fpl_edge.models.team_goals.scoreline import clean_sheet_probs, expected_goals
from fpl_edge.models.team_goals.synthetic import (
    TRUE_HOME_ADV,
    TRUE_RHO,
    build_warehouse,
    load_league,
)
from fpl_edge.store import LeakageError, Warehouse

SEASON = "2025-26"
#: Mid-season so that the target season contributes matches of its own.
AS_OF = dt.datetime(2025, 12, 4, 10, 0, tzinfo=dt.UTC)
#: The first gameweek that has not kicked off at AS_OF.
TARGET_GW = 18


@pytest.fixture(scope="module")
def league():
    return load_league(FIXTURES_DIR)


@pytest.fixture(scope="module")
def warehouse(league, tmp_path_factory) -> Warehouse:
    path = tmp_path_factory.mktemp("dc") / "wh.duckdb"
    return build_warehouse(league, path)


@pytest.fixture(scope="module")
def snapshot(warehouse):
    return warehouse.snapshot_at(AS_OF)


@pytest.fixture(scope="module")
def fitted(snapshot, league):
    model = DixonColesModel(routes=league.routes)
    model.fit(snapshot, SEASON)
    return model


# -- the likelihood ----------------------------------------------------------


def test_analytic_gradient_matches_finite_differences(snapshot, league) -> None:
    """The analytic gradient is a 40x speedup and therefore a 40x liability."""
    matches = read_finished_matches(snapshot).iloc[:400]
    codes = sorted(set(matches["home_team_code"]) | set(matches["away_team_code"]))
    prior = fit_promoted_prior(read_finished_matches(snapshot), routes=league.routes)
    priors = build_team_priors(codes, {codes[0]}, prior)
    index = {c: i for i, c in enumerate(codes)}
    kwargs = {
        "n_teams": len(codes),
        "hi": matches["home_team_code"].map(index).to_numpy(int),
        "ai": matches["away_team_code"].map(index).to_numpy(int),
        "x": matches["home_score"].to_numpy(float),
        "y": matches["away_score"].to_numpy(float),
        "w": _decay_weights(matches["kickoff_utc"], AS_OF, 400.0),
        "priors": priors,
    }
    rng = np.random.default_rng(7)
    theta = np.concatenate(
        [[0.15, 0.22, -0.07], rng.normal(0, 0.2, len(codes)), rng.normal(0, 0.2, len(codes))]
    )
    _, grad = _objective(theta, **kwargs)
    eps = 1e-6
    for i in rng.choice(theta.size, size=12, replace=False):
        up, down = theta.copy(), theta.copy()
        up[i] += eps
        down[i] -= eps
        numeric = (_objective(up, **kwargs)[0] - _objective(down, **kwargs)[0]) / (2 * eps)
        assert grad[i] == pytest.approx(numeric, rel=1e-4, abs=1e-5)


def test_time_decay_weights_halve_at_the_half_life() -> None:
    as_of = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    kicks = pd.Series(pd.to_datetime(["2026-01-01", "2025-07-05", "2025-01-01"], utc=True))
    w = _decay_weights(kicks, as_of, 180.0)
    assert w[0] == pytest.approx(1.0)
    assert w[1] == pytest.approx(0.5, abs=0.02)
    assert w[2] == pytest.approx(0.25, abs=0.02)


# -- parameter recovery ------------------------------------------------------


def test_recovers_the_generating_parameters(fitted) -> None:
    fit = fitted._fit
    assert fit.converged
    assert fit.home_adv == pytest.approx(TRUE_HOME_ADV, abs=0.06)
    assert fit.rho == pytest.approx(TRUE_RHO, abs=0.06)
    assert fit.rho < 0.0


def test_recovers_relative_team_strength(fitted, league) -> None:
    fit = fitted._fit
    truth = league.truth[league.truth["season"] == SEASON].set_index("team_code")
    est = fit.table().set_index("team_code").join(truth, rsuffix="_true", how="inner")
    assert len(est) == 20
    assert np.corrcoef(est["attack"], est["attack_true"])[0, 1] > 0.6
    assert np.corrcoef(est["defence"], est["defence_true"])[0, 1] > 0.6


def test_fit_is_deterministic(snapshot, league) -> None:
    a = DixonColesModel(routes=league.routes).fit(snapshot, SEASON)
    b = DixonColesModel(routes=league.routes).fit(snapshot, SEASON)
    np.testing.assert_allclose(a.attack, b.attack)
    np.testing.assert_allclose(a.defence, b.defence)
    assert a.rho == b.rho


# -- the TeamStrengthModel contract -----------------------------------------


def test_predict_returns_the_contract_frame(fitted, snapshot) -> None:
    frame = fitted.predict(snapshot, SEASON, [TARGET_GW])
    assert list(frame.columns) == list(TEAM_STRENGTH_COLUMNS)
    assert not frame.empty
    assert len(frame) == 2 * frame["fixture_id"].nunique()
    assert frame["is_home"].sum() == frame["fixture_id"].nunique()
    assert (frame["exp_goals_for"] > 0).all()
    assert ((frame["p_clean_sheet"] > 0) & (frame["p_clean_sheet"] < 1)).all()
    assert (frame["gw"] == TARGET_GW).all()


def test_score_matrix_agrees_with_predict(fitted, snapshot) -> None:
    """The invariant the whole package is built around."""
    frame = fitted.predict(snapshot, SEASON, [TARGET_GW])
    for fid in frame["fixture_id"].unique():
        mat = fitted.score_matrix(int(fid))
        assert mat.sum() == pytest.approx(1.0, abs=1e-12)
        cs_home, cs_away = clean_sheet_probs(mat)
        xg_home, xg_away = expected_goals(mat)
        home = frame[(frame["fixture_id"] == fid) & frame["is_home"]].iloc[0]
        away = frame[(frame["fixture_id"] == fid) & ~frame["is_home"]].iloc[0]
        assert home["p_clean_sheet"] == pytest.approx(cs_home, abs=1e-12)
        assert away["p_clean_sheet"] == pytest.approx(cs_away, abs=1e-12)
        assert home["exp_goals_for"] == pytest.approx(xg_home, abs=1e-12)
        assert home["exp_goals_against"] == pytest.approx(xg_away, abs=1e-12)
        assert away["exp_goals_for"] == pytest.approx(home["exp_goals_against"], abs=1e-12)


def test_score_matrix_refuses_unpredicted_fixtures(fitted) -> None:
    with pytest.raises(UnknownFixtureError):
        fitted.score_matrix(-12345)


def test_score_matrix_honours_a_wider_grid(fitted, snapshot) -> None:
    frame = fitted.predict(snapshot, SEASON, [TARGET_GW])
    fid = int(frame["fixture_id"].iloc[0])
    wide = fitted.score_matrix(fid, 12)
    assert wide.shape == (13, 13)
    assert wide.sum() == pytest.approx(1.0, abs=1e-12)


# -- leakage -----------------------------------------------------------------


def test_training_set_contains_no_future_matches(snapshot) -> None:
    matches = read_finished_matches(snapshot)
    assert len(matches) > 0
    assert (matches["kickoff_utc"] < AS_OF).all()


def test_target_fixtures_are_all_in_the_future(snapshot) -> None:
    fixtures = read_target_fixtures(snapshot, SEASON, [TARGET_GW])
    assert not fixtures.empty
    assert (fixtures["kickoff_utc"] > AS_OF).all()
    assert fixtures["home_score"].isna().all()


def test_a_gameweek_one_snapshot_sees_no_current_season_matches(warehouse) -> None:
    """The situation the engine is actually in today."""
    gw1_deadline = dt.datetime(2025, 8, 14, 10, 0, tzinfo=dt.UTC)
    snap = warehouse.snapshot_at(gw1_deadline)
    matches = read_finished_matches(snap)
    assert SEASON not in set(matches["season"])
    assert len(read_target_fixtures(snap, SEASON, [1])) == 10


def test_mis_stamped_result_rows_are_rejected(league, tmp_path: Path) -> None:
    """A row whose as_of predates its own kickoff must not silently train us.

    Defence in depth. `Warehouse.append` now refuses such a row at WRITE time,
    so the poisoned row is injected through a raw DuckDB connection —
    simulating a row written before that guard existed, or by any other
    process — to prove the READ guard still catches it independently. If the
    two guards ever collapse into one, this test fails and says why.
    """
    import duckdb

    db = tmp_path / "bad.duckdb"
    bad = league.fixtures.copy()
    wh = build_warehouse(
        type(league)(bad, league.truth, league.routes, league.odds, league.events, league.teams),
        db,
    )
    wh.close()

    poisoned = bad.iloc[0]
    raw = duckdb.connect(str(db))
    raw.execute("SET TimeZone='UTC'")
    raw.execute(
        """
        INSERT INTO fact_fixture
            (season, fixture_id, gw, kickoff_utc, home_team_code, away_team_code,
             finished, home_score, away_score, as_of)
        VALUES (?, 999999, ?, TIMESTAMPTZ '2030-01-01 00:00:00+00', ?, ?, TRUE, ?, ?,
                TIMESTAMPTZ '2020-01-01 00:00:00+00')
        """,
        [poisoned["season"], int(poisoned["gw"]),
         int(poisoned["home_team_code"]), int(poisoned["away_team_code"]),
         int(poisoned["home_score"]), int(poisoned["away_score"])],
    )
    raw.close()

    reopened = Warehouse(db, read_only=True)
    snap = reopened.snapshot_at(dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    with pytest.raises(LeakageError, match="kickoff at or after"):
        read_finished_matches(snap)
    reopened.close()


def test_write_guard_refuses_a_result_stamped_before_its_kickoff(
    league, tmp_path: Path
) -> None:
    """The other half of the pair: the warehouse refuses to store it at all."""
    bad = league.fixtures.copy()
    wh = build_warehouse(
        type(league)(bad, league.truth, league.routes, league.odds, league.events, league.teams),
        tmp_path / "guard.duckdb",
    )
    poisoned = bad.iloc[:1][
        ["season", "fixture_id", "gw", "kickoff_utc", "home_team_code", "away_team_code",
         "home_score", "away_score"]
    ].copy()
    poisoned["finished"] = True
    poisoned["as_of"] = pd.Timestamp("2020-01-01", tz="UTC")
    poisoned["fixture_id"] = 999_999
    poisoned["kickoff_utc"] = pd.Timestamp("2030-01-01", tz="UTC")
    with pytest.raises(ValueError, match="before their own kickoff"):
        wh.append("fact_fixture", poisoned)
    wh.close()


def test_refuses_to_fit_on_too_little_history(warehouse) -> None:
    early = dt.datetime(2020, 9, 1, tzinfo=dt.UTC)
    snap = warehouse.snapshot_at(early)
    with pytest.raises(InsufficientHistoryError, match="finished matches"):
        DixonColesModel().fit(snap, "2020-21")
