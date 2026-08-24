"""Scoring rules for goal-model evaluation.

Three metrics, because they answer three different questions and a model can win
one while losing another:

* **Log loss** on the 1X2 outcome. Strictly proper, unbounded penalty for
  confident errors. This is the headline number.
* **RPS** (ranked probability score, Epstein 1969; Constantinou & Fenton 2012 for
  football). Log loss treats "home win" and "away win" as equally wrong when the
  match was a draw; RPS does not, because it works on the *cumulative*
  distribution over an ordered outcome space. Reported over both the ordered
  1X2 space and over goal difference, which is the genuinely scoreline-level
  version.
* **Brier** on clean sheets. This is the number that actually prices defenders,
  so it gets measured directly rather than assumed to follow from the others.

STATUS: EVALUATION HARNESS, run via `python -m` (see docs/models/); not in the production import closure and not expected to be. It exists to mint the committed evidence CSVs, not to serve requests.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-15


def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Mean negative log probability assigned to the realised category.

    ``probs`` is (n, k); ``outcomes`` is (n,) of integer category indices.
    """
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=int)
    if probs.ndim != 2:
        raise ValueError("probs must be 2-d (n_samples, n_categories)")
    picked = probs[np.arange(probs.shape[0]), outcomes]
    return float(-np.log(np.clip(picked, _EPS, None)).mean())


def rps(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Ranked probability score over an *ordered* categorical outcome space.

    RPS = mean over samples of ``sum_k (CDF_pred(k) - CDF_obs(k))^2 / (K - 1)``.
    Lower is better; 0 is a perfect deterministic forecast.
    """
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=int)
    n, k = probs.shape
    if k < 2:
        raise ValueError("RPS needs at least two ordered categories")
    cdf_pred = np.cumsum(probs, axis=1)
    obs = np.zeros_like(probs)
    obs[np.arange(n), outcomes] = 1.0
    cdf_obs = np.cumsum(obs, axis=1)
    return float(((cdf_pred - cdf_obs) ** 2).sum(axis=1).mean() / (k - 1))


def brier(probs: np.ndarray, outcomes: np.ndarray) -> float:
    """Binary Brier score: mean squared error of a probability forecast."""
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    return float(((probs - outcomes) ** 2).mean())


def calibration_table(
    probs: np.ndarray, outcomes: np.ndarray, *, n_bins: int = 10
) -> list[dict[str, float]]:
    """Reliability-diagram rows: predicted vs empirical frequency per bin.

    Committed as CSV so the calibration claim is inspectable rather than
    asserted. A model can have a good Brier score and still be systematically
    over-confident in the 0.4-0.6 band, which is exactly the band a clean-sheet
    call is made in.
    """
    probs = np.asarray(probs, dtype=float)
    outcomes = np.asarray(outcomes, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    idx = np.clip(np.digitize(probs, edges[1:-1], right=False), 0, n_bins - 1)
    rows: list[dict[str, float]] = []
    for b in range(n_bins):
        sel = idx == b
        count = int(sel.sum())
        rows.append(
            {
                "bin_lo": float(edges[b]),
                "bin_hi": float(edges[b + 1]),
                "count": count,
                "mean_predicted": float(probs[sel].mean()) if count else float("nan"),
                "empirical_rate": float(outcomes[sel].mean()) if count else float("nan"),
            }
        )
    return rows
