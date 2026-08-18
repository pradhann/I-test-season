from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.eval.calibration import (
    brier_score,
    crps_ensemble,
    log_loss,
    multiclass_brier,
    reliability_curve,
    skill_score,
)


def test_perfect_forecast_scores_zero() -> None:
    p = np.array([1.0, 0.0, 1.0, 0.0])
    y = np.array([1, 0, 1, 0])
    assert brier_score(p, y) == 0.0
    assert log_loss(p, y) < 1e-9


def test_always_half_is_the_uninformed_reference() -> None:
    p = np.full(100, 0.5)
    y = np.random.default_rng(0).integers(0, 2, 100)
    assert brier_score(p, y) == pytest.approx(0.25)
    assert log_loss(p, y) == pytest.approx(np.log(2), abs=1e-9)


def test_confident_and_wrong_is_clipped_not_infinite() -> None:
    """An unclipped miss returns inf and hides everything else about the model."""
    got = log_loss(np.array([0.0]), np.array([1]))
    assert np.isfinite(got) and got > 20


def test_multiclass_brier_requires_normalised_rows() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        multiclass_brier(np.array([[0.5, 0.2, 0.2]]), np.array([0]))


def test_multiclass_brier_perfect_is_zero() -> None:
    p = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    assert multiclass_brier(p, np.array([0, 2])) == 0.0


def test_crps_of_a_point_forecast_equals_mean_absolute_error() -> None:
    """Deterministic forecast: CRPS must collapse to MAE, making it comparable."""
    obs = np.array([3.0, 7.0])
    samples = np.repeat(np.array([[5.0], [5.0]]), 500, axis=1)
    assert crps_ensemble(samples, obs) == pytest.approx(np.mean([2.0, 2.0]), abs=1e-9)


def test_crps_rewards_a_sharper_correct_distribution() -> None:
    rng = np.random.default_rng(1)
    obs = np.zeros(400)
    tight = rng.normal(0, 1, (400, 600))
    loose = rng.normal(0, 5, (400, 600))
    assert crps_ensemble(tight, obs) < crps_ensemble(loose, obs)


def test_crps_penalises_a_confidently_wrong_distribution() -> None:
    rng = np.random.default_rng(2)
    obs = np.zeros(200)
    right = rng.normal(0, 1, (200, 400))
    biased = rng.normal(6, 1, (200, 400))
    assert crps_ensemble(right, obs) < crps_ensemble(biased, obs)


def test_reliability_curve_detects_overconfidence() -> None:
    rng = np.random.default_rng(3)
    # Claim 0.9 but only deliver 0.5 of the time.
    probs = np.full(2000, 0.9)
    outcomes = rng.binomial(1, 0.5, 2000)
    curve = reliability_curve(probs, outcomes)
    assert curve.max_deviation > 0.3


def test_reliability_curve_is_flat_when_calibrated() -> None:
    rng = np.random.default_rng(4)
    probs = rng.uniform(0.05, 0.95, 20000)
    outcomes = rng.binomial(1, probs)
    curve = reliability_curve(probs, outcomes)
    assert curve.max_deviation < 0.05


def test_skill_score_signs() -> None:
    assert skill_score(0.10, 0.20) == pytest.approx(0.5)
    assert skill_score(0.20, 0.20) == 0.0
    assert skill_score(0.30, 0.20) < 0


def test_mismatched_lengths_are_rejected() -> None:
    with pytest.raises(ValueError):
        brier_score(np.array([0.5, 0.5]), np.array([1]))
