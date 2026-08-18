"""Bivariate scoreline distribution: the Dixon-Coles low-score correction.

Everything downstream of the goal model -- clean sheets for defenders, goals
conceded penalties, match outcome probabilities -- is a functional of the joint
distribution over (home goals, away goals). So that joint matrix is the single
object this package produces, and every scalar the model reports is derived
*from the matrix* rather than computed alongside it. That is deliberate: if
``p_clean_sheet`` were computed from lambda directly it could silently disagree
with the matrix that the simulator samples from, and the disagreement would only
show up as a mis-priced defender three layers downstream.

The correction itself is Dixon & Coles (1997). Independent Poisson marginals fit
football goal counts well in aggregate but under-predict 0-0 and 1-1 and
over-predict 1-0 and 0-1. ``tau`` reweights exactly those four cells:

    tau(0,0) = 1 - lam*mu*rho      tau(0,1) = 1 + lam*rho
    tau(1,0) = 1 + mu*rho          tau(1,1) = 1 - rho

with tau == 1 everywhere else, so the correction is local to the low-score
corner and leaves the tail Poisson.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import poisson

#: Truncation point for score matrices, matching the TeamStrengthModel contract.
#: At a heavy lam = 2.2 v 1.9 the truncated cells hold ~6e-4 of the mass; the
#: matrix is then renormalised, so the loss appears as a proportional
#: reallocation across the retained cells rather than as missing mass. That is
#: well inside the sampling error of anything downstream.
DEFAULT_MAX_GOALS = 8

#: Hard floor on tau. rho is bounded during the fit, but a large lambda can
#: still drive tau(0,0) negative; clamping keeps the likelihood finite and makes
#: the region strongly unattractive to the optimiser rather than undefined.
_TAU_FLOOR = 1e-9


@dataclass(frozen=True, slots=True)
class GoalRates:
    """Expected goals for one fixture plus the dependence parameter."""

    home: float
    away: float
    rho: float = 0.0

    def __post_init__(self) -> None:
        if not (self.home > 0 and self.away > 0):
            raise ValueError(f"goal rates must be positive, got {self.home}, {self.away}")


def tau(x: np.ndarray, y: np.ndarray, lam: np.ndarray, mu: np.ndarray, rho: float) -> np.ndarray:
    """Dixon-Coles low-score dependence factor, vectorised over matches."""
    x = np.asarray(x)
    y = np.asarray(y)
    out = np.ones(np.broadcast(x, y, lam, mu).shape, dtype=float)
    lam_b, mu_b = np.broadcast_arrays(np.asarray(lam, dtype=float), np.asarray(mu, dtype=float))
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)
    out = np.where(m00, 1.0 - lam_b * mu_b * rho, out)
    out = np.where(m01, 1.0 + lam_b * rho, out)
    out = np.where(m10, 1.0 + mu_b * rho, out)
    out = np.where(m11, 1.0 - rho, out)
    return out


def score_matrix(
    rates: GoalRates, max_goals: int = DEFAULT_MAX_GOALS, *, normalise: bool = True
) -> np.ndarray:
    """Joint ``P(home = i, away = j)`` as a ``(max_goals+1, max_goals+1)`` array.

    Normalised to sum to exactly 1 so that every marginal derived from it is a
    genuine probability rather than a truncated one.
    """
    if max_goals < 1:
        raise ValueError("max_goals must be at least 1")
    i = np.arange(max_goals + 1)
    home_pmf = poisson.pmf(i, rates.home)
    away_pmf = poisson.pmf(i, rates.away)
    mat = np.outer(home_pmf, away_pmf)
    grid_x = i[:, None] * np.ones((1, max_goals + 1), dtype=int)
    grid_y = i[None, :] * np.ones((max_goals + 1, 1), dtype=int)
    mat = mat * np.clip(
        tau(grid_x, grid_y, np.full_like(mat, rates.home), np.full_like(mat, rates.away), rates.rho),
        _TAU_FLOOR,
        None,
    )
    if normalise:
        total = mat.sum()
        if total <= 0:
            raise ValueError(f"degenerate score matrix for {rates!r}")
        mat = mat / total
    return mat


def outcome_probs(matrix: np.ndarray) -> tuple[float, float, float]:
    """``(P(home win), P(draw), P(away win))`` from a joint score matrix."""
    tri = np.tril(matrix, -1).sum()  # i > j  -> home scored more
    draw = float(np.trace(matrix))
    return float(tri), draw, float(np.triu(matrix, 1).sum())


def clean_sheet_probs(matrix: np.ndarray) -> tuple[float, float]:
    """``(P(home keeps clean sheet), P(away keeps clean sheet))``.

    A team keeps a clean sheet when its *opponent* fails to score, so the home
    side's clean-sheet probability is the away marginal at zero: column 0.
    """
    return float(matrix[:, 0].sum()), float(matrix[0, :].sum())


def expected_goals(matrix: np.ndarray) -> tuple[float, float]:
    """Marginal means of the (truncated, renormalised) matrix.

    Reported instead of the raw lambda so that ``exp_goals_for`` and
    ``p_clean_sheet`` in the output frame describe the same distribution.
    """
    n = matrix.shape[0]
    goals = np.arange(n)
    return float(matrix.sum(axis=1) @ goals), float(matrix.sum(axis=0) @ goals)


def goal_difference_probs(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(differences, probabilities)`` over ordered goal difference home-away."""
    n = matrix.shape[0]
    diffs = np.arange(-(n - 1), n)
    probs = np.zeros(diffs.size)
    for k, d in enumerate(diffs):
        probs[k] = np.trace(matrix, offset=-d)
    return diffs, probs


def total_goals_probs(matrix: np.ndarray) -> np.ndarray:
    """``P(total goals = t)`` for ``t = 0 .. 2*max_goals``."""
    n = matrix.shape[0]
    out = np.zeros(2 * n - 1)
    for t in range(2 * n - 1):
        lo = max(0, t - (n - 1))
        hi = min(n - 1, t)
        idx = np.arange(lo, hi + 1)
        out[t] = matrix[idx, t - idx].sum()
    return out


def prob_over(matrix: np.ndarray, line: float = 2.5) -> float:
    """``P(total goals > line)``; the bookmaker totals market."""
    totals = total_goals_probs(matrix)
    t = np.arange(totals.size)
    return float(totals[t > line].sum())
