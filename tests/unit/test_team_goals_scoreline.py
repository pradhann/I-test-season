"""Scoreline algebra: the joint matrix and everything derived from it.

The contract that matters here is internal consistency. ``p_clean_sheet`` feeds
defender points directly, so it is asserted to equal the matrix marginal rather
than merely to look plausible -- a clean sheet probability that disagrees with
the matrix the simulator samples from is a bug that would surface only as
mis-priced defenders several layers downstream.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.models.team_goals import metrics as M
from fpl_edge.models.team_goals.scoreline import (
    GoalRates,
    clean_sheet_probs,
    expected_goals,
    goal_difference_probs,
    outcome_probs,
    prob_over,
    score_matrix,
    tau,
    total_goals_probs,
)

RATES = [
    GoalRates(1.6, 1.1, -0.08),
    GoalRates(0.6, 2.4, 0.10),
    GoalRates(2.9, 0.4, 0.0),
    GoalRates(1.0, 1.0, -0.30),
]


@pytest.mark.parametrize("rates", RATES)
def test_score_matrix_sums_to_one(rates: GoalRates) -> None:
    mat = score_matrix(rates)
    assert mat.shape == (9, 9)
    assert np.all(mat >= 0.0)
    assert mat.sum() == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("rates", RATES)
def test_clean_sheet_equals_matrix_marginal(rates: GoalRates) -> None:
    mat = score_matrix(rates)
    cs_home, cs_away = clean_sheet_probs(mat)
    # A home clean sheet is exactly "the away side scored zero", i.e. the away
    # marginal evaluated at 0, which is column 0 of the joint matrix.
    assert cs_home == pytest.approx(mat.sum(axis=0)[0])
    assert cs_home == pytest.approx(mat[:, 0].sum())
    assert cs_away == pytest.approx(mat[0, :].sum())
    assert 0.0 < cs_home < 1.0
    assert 0.0 < cs_away < 1.0


@pytest.mark.parametrize("rates", RATES)
def test_derived_distributions_are_distributions(rates: GoalRates) -> None:
    mat = score_matrix(rates)
    ph, pdw, pa = outcome_probs(mat)
    assert ph + pdw + pa == pytest.approx(1.0, abs=1e-12)
    _, gd = goal_difference_probs(mat)
    assert gd.sum() == pytest.approx(1.0, abs=1e-12)
    assert total_goals_probs(mat).sum() == pytest.approx(1.0, abs=1e-12)
    assert 0.0 < prob_over(mat, 2.5) < 1.0


def test_expected_goals_track_lambda() -> None:
    rates = GoalRates(1.7, 1.05, 0.0)
    xg_home, xg_away = expected_goals(score_matrix(rates, 12))
    assert xg_home == pytest.approx(rates.home, abs=1e-4)
    assert xg_away == pytest.approx(rates.away, abs=1e-4)


def test_tau_only_touches_the_low_score_corner() -> None:
    lam, mu, rho = 1.5, 1.2, -0.1
    for x in range(4):
        for y in range(4):
            t = float(tau(np.array(x), np.array(y), np.array(lam), np.array(mu), rho))
            if (x, y) in {(0, 0), (0, 1), (1, 0), (1, 1)}:
                assert t != pytest.approx(1.0)
            else:
                assert t == pytest.approx(1.0)


def test_negative_rho_lifts_draws_and_damps_one_nil() -> None:
    """The empirical direction of the Dixon-Coles correction."""
    base = score_matrix(GoalRates(1.4, 1.1, 0.0))
    corrected = score_matrix(GoalRates(1.4, 1.1, -0.12))
    assert corrected[0, 0] > base[0, 0]
    assert corrected[1, 1] > base[1, 1]
    assert corrected[1, 0] < base[1, 0]
    assert corrected[0, 1] < base[0, 1]


def test_rates_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        GoalRates(0.0, 1.0)


def test_truncation_error_is_negligible_at_default_max_goals() -> None:
    tight = score_matrix(GoalRates(2.2, 1.9, 0.0), 8, normalise=False)
    wide = score_matrix(GoalRates(2.2, 1.9, 0.0), 20, normalise=False)
    assert wide.sum() - tight.sum() < 2e-3


# -- metrics -----------------------------------------------------------------


def test_rps_bounds_and_ordering() -> None:
    perfect = np.eye(3)
    assert M.rps(perfect, np.array([0, 1, 2])) == pytest.approx(0.0)
    # Worst possible on an ordered space: all mass two categories away.
    assert M.rps(np.array([[1.0, 0.0, 0.0]]), np.array([2])) == pytest.approx(1.0)
    # Being wrong by one category must cost less than being wrong by two.
    near = M.rps(np.array([[0.0, 1.0, 0.0]]), np.array([2]))
    far = M.rps(np.array([[1.0, 0.0, 0.0]]), np.array([2]))
    assert near < far


def test_log_loss_ignores_order() -> None:
    """The contrast with RPS that justifies reporting both."""
    a = M.log_loss(np.array([[0.0, 1.0, 0.0]]), np.array([2]))
    b = M.log_loss(np.array([[1.0, 0.0, 0.0]]), np.array([2]))
    assert a == pytest.approx(b)


def test_brier_and_calibration_table() -> None:
    p = np.array([0.9, 0.1, 0.9, 0.1])
    y = np.array([1.0, 0.0, 1.0, 0.0])
    assert M.brier(p, y) == pytest.approx(0.01)
    rows = M.calibration_table(p, y, n_bins=10)
    assert sum(r["count"] for r in rows) == 4
    hit = [r for r in rows if r["count"]]
    assert all(abs(r["mean_predicted"] - r["empirical_rate"]) < 0.15 for r in hit)
