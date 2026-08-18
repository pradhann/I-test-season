"""Calibration scoring for probabilistic outputs.

Every probabilistic output in this system is scored with these, because a
model whose probabilities are miscalibrated cannot be used for rank utility even
if its rankings are good. Being right about who is best is not the same as being
right about how often they haul, and the second is what P(rank < threshold)
consumes.

Convention: lower is better for every metric here, so a ModelCard comparison is
always ``score < baseline_score``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _check(probs: np.ndarray, outcomes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes)
    if probs.shape[0] != outcomes.shape[0]:
        raise ValueError(f"{probs.shape[0]} predictions vs {outcomes.shape[0]} outcomes")
    if probs.size == 0:
        raise ValueError("empty prediction set")
    return probs, outcomes


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Binary Brier score: mean squared error of the probability."""
    probs, outcomes = _check(probs, outcomes)
    if not np.isin(outcomes, (0, 1)).all():
        raise ValueError("binary Brier requires 0/1 outcomes")
    return float(np.mean((probs - outcomes) ** 2))


def multiclass_brier(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Brier score for K-class predictions.

    ``probs`` is (n, K) and must sum to 1 per row; ``outcomes`` are class indices.
    """
    probs, outcomes = _check(probs, outcomes)
    if probs.ndim != 2:
        raise ValueError("multiclass_brier expects (n, K) probabilities")
    row_sums = probs.sum(axis=1)
    if not np.allclose(row_sums, 1.0, atol=1e-6):
        raise ValueError(f"rows must sum to 1; worst row sums to {row_sums.max():.6f}")
    onehot = np.zeros_like(probs)
    onehot[np.arange(len(outcomes)), outcomes.astype(int)] = 1.0
    return float(np.mean(np.sum((probs - onehot) ** 2, axis=1)))


def log_loss(probs: np.ndarray, outcomes: np.ndarray, *, eps: float = 1e-12) -> float:
    """Negative log likelihood, binary or multiclass.

    Clipped at ``eps``: an unclipped confident miss returns infinity, which
    destroys a whole season's average and hides everything else about the model.
    The clip is reported rather than hidden.
    """
    probs, outcomes = _check(probs, outcomes)
    if probs.ndim == 1:
        p = np.clip(probs, eps, 1 - eps)
        return float(-np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p)))
    p = np.clip(probs, eps, 1.0)
    chosen = p[np.arange(len(outcomes)), outcomes.astype(int)]
    return float(-np.mean(np.log(chosen)))


def crps_ensemble(samples: np.ndarray, observed: np.ndarray) -> float:
    """Continuous Ranked Probability Score from Monte Carlo samples.

    ``samples`` is (n_obs, n_samples); ``observed`` is (n_obs,). Uses the
    energy-form estimator::

        CRPS = E|X - y| - 0.5 * E|X - X'|

    This is the right metric for the points model, whose output is a full
    distribution over an integer score rather than a probability of one event.
    Reduces to mean absolute error for a deterministic forecast, so it is
    directly comparable against a point-estimate baseline.
    """
    samples = np.asarray(samples, dtype=float)
    observed = np.asarray(observed, dtype=float)
    if samples.ndim != 2:
        raise ValueError("samples must be (n_obs, n_samples)")
    if samples.shape[0] != observed.shape[0]:
        raise ValueError("sample rows must match observations")

    n = samples.shape[1]
    term1 = np.abs(samples - observed[:, None]).mean(axis=1)
    # Sorted trick: E|X - X'| computed in O(n log n) instead of O(n^2).
    s = np.sort(samples, axis=1)
    weights = 2 * np.arange(1, n + 1) - n - 1
    term2 = (2.0 / (n * n)) * (s * weights).sum(axis=1)
    return float(np.mean(term1 - 0.5 * term2))


@dataclass(frozen=True, slots=True)
class ReliabilityCurve:
    """Binned observed frequency against predicted probability."""

    bin_lower: np.ndarray
    bin_upper: np.ndarray
    predicted: np.ndarray
    observed: np.ndarray
    count: np.ndarray

    @property
    def max_deviation(self) -> float:
        """Largest gap between predicted and observed in any populated bin."""
        pop = self.count > 0
        if not pop.any():
            return float("nan")
        return float(np.max(np.abs(self.predicted[pop] - self.observed[pop])))

    def to_csv(self, path: str) -> None:
        import csv

        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["bin_lower", "bin_upper", "predicted", "observed", "count"])
            for row in zip(self.bin_lower, self.bin_upper, self.predicted,
                           self.observed, self.count):
                w.writerow(row)


def reliability_curve(
    probs: np.ndarray, outcomes: np.ndarray, *, n_bins: int = 10
) -> ReliabilityCurve:
    """Bin predictions and compare mean prediction to observed frequency."""
    probs, outcomes = _check(probs, outcomes)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(probs, edges[1:-1], right=False), 0, n_bins - 1)

    pred = np.full(n_bins, np.nan)
    obs = np.full(n_bins, np.nan)
    cnt = np.zeros(n_bins, dtype=int)
    for b in range(n_bins):
        m = idx == b
        cnt[b] = int(m.sum())
        if cnt[b]:
            pred[b] = float(probs[m].mean())
            obs[b] = float(outcomes[m].mean())
    return ReliabilityCurve(edges[:-1], edges[1:], pred, obs, cnt)


def skill_score(score: float, baseline_score: float) -> float:
    """Fractional improvement over a baseline. 0 means no better, 1 means perfect."""
    if baseline_score == 0:
        return float("nan")
    return 1.0 - score / baseline_score
