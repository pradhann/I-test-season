"""Turning simulated scores into a rank distribution.

The hard part is resolution. We simulate ~10,000 rivals but report against a
field of ~5.9 million, so one simulated rival is worth ~590 real managers.
Empirically counting rivals who beat me gives ranks quantised to 590, which is
useless for P(top 100) and marginal for P(top 1k).

Two things rescue it. First, conditional on a single Monte Carlo draw of player
points, the spread of scores *across managers* is squad-selection noise: a sum
of eleven-ish exchangeable contributions, which is close to Gaussian and very
smooth. Second, we only need the far tail of that conditional distribution, not
of the unconditional one. So per simulation we fit the first three moments of
the rival score distribution and use a Cornish-Fisher expansion for the
survival function, blending into the empirical count wherever the empirical
count has enough support to be trusted. Deep-tail probabilities therefore carry
a distributional assumption on top of Monte Carlo error, and
:meth:`RankDistribution.summary` labels which estimates those are.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np
from scipy.stats import norm

#: Below this many rivals beating me, the empirical count is too granular and
#: the parametric tail takes over; above the upper bound, the empirical count is
#: used outright. In between they are blended linearly.
BLEND_LO = 8.0
BLEND_HI = 40.0


def _cornish_fisher_survival(t: np.ndarray, skew: np.ndarray) -> np.ndarray:
    """P(Z > t) for a standardised variable with the given skewness.

    Inverts the second-order Cornish-Fisher quantile expansion
    ``t = z + (z^2 - 1) * g1 / 6`` for ``z``, then reads off the normal tail.
    """
    g = np.clip(skew, -1.5, 1.5) / 6.0
    z = np.empty_like(t)
    small = np.abs(g) < 1e-6
    z[small] = t[small]
    gg = g[~small]
    tt = t[~small]
    disc = np.maximum(1.0 + 4.0 * gg * (tt + gg), 1e-12)
    z[~small] = (-1.0 + np.sqrt(disc)) / (2.0 * gg)
    # For negative skew the alternate root is the continuous one at large |t|.
    bad = ~np.isfinite(z)
    z[bad] = t[bad]
    return norm.sf(z)


def rank_from_scores(
    my_scores: np.ndarray,
    rival_scores: np.ndarray,
    *,
    field_size: int,
    tie_break: float = 0.5,
) -> np.ndarray:
    """Expected overall rank per simulation.

    Parameters
    ----------
    my_scores
        ``(n_sims,)``.
    rival_scores
        ``(n_rivals, n_sims)`` scored against the *same* point draws.

    Returns
    -------
    ``(n_sims,)`` float ranks, ``1.0`` being first.
    """
    my = np.asarray(my_scores, dtype=np.float64)
    riv = np.asarray(rival_scores, dtype=np.float64)
    m = riv.shape[0]
    above = (riv > my[None, :]).sum(axis=0)
    equal = (riv == my[None, :]).sum(axis=0)
    k = above + tie_break * equal
    p_emp = k / m

    mu = riv.mean(axis=0)
    sd = riv.std(axis=0)
    sd = np.where(sd < 1e-9, 1e-9, sd)
    z = (riv - mu) / sd
    skew = (z ** 3).mean(axis=0)
    t = (my - mu) / sd
    p_par = _cornish_fisher_survival(t, skew)

    w = np.clip((k - BLEND_LO) / (BLEND_HI - BLEND_LO), 0.0, 1.0)
    p = w * p_emp + (1.0 - w) * p_par
    p = np.clip(p, 0.5 / field_size, 1.0)
    return 1.0 + p * field_size


@dataclass(frozen=True)
class RankDistribution:
    """The full simulated distribution of my final rank.

    Everything the optimizer needs is a functional of ``ranks``; the scores are
    retained for diagnostics and for the field-model validation.
    """

    ranks: np.ndarray            # (n_sims,)
    my_scores: np.ndarray        # (n_sims,)
    field_mean_score: np.ndarray  # (n_sims,) mean rival score per simulation
    rival_scores: np.ndarray | None = None
    field_size: int = 5_896_644
    label: str = ""

    @property
    def n_sims(self) -> int:
        return int(self.ranks.shape[0])

    def p_top(self, threshold: int) -> float:
        return float((self.ranks <= threshold).mean())

    def se_p_top(self, threshold: int) -> float:
        """Binomial Monte Carlo standard error of :meth:`p_top`."""
        p = self.p_top(threshold)
        return math.sqrt(max(p * (1.0 - p), 0.0) / self.n_sims)

    def rank_quantile(self, q: float) -> float:
        return float(np.quantile(self.ranks, q))

    def median_rank(self) -> float:
        return self.rank_quantile(0.5)

    def expected_points(self) -> float:
        return float(self.my_scores.mean())

    def correlation_with_field(self) -> float:
        """Corr(my season score, mean field season score) across simulations.

        This is the number that decides whether the whole engine is doing
        anything. Independent sampling would put it at zero.
        """
        return float(np.corrcoef(self.my_scores, self.field_mean_score)[0, 1])

    def beta_on_field(self) -> float:
        """Regression slope of my score on the field's mean score."""
        cov = np.cov(self.my_scores, self.field_mean_score)
        return float(cov[0, 1] / cov[1, 1])

    def summary(self, thresholds=(100, 1_000, 10_000, 100_000)) -> dict:
        out: dict[str, float | str | int] = {
            "label": self.label,
            "n_sims": self.n_sims,
            "field_size": self.field_size,
            "mean_points": self.expected_points(),
            "sd_points": float(self.my_scores.std()),
            "median_rank": self.median_rank(),
            "rank_p10": self.rank_quantile(0.10),
            "rank_p90": self.rank_quantile(0.90),
            "corr_with_field": self.correlation_with_field(),
            "beta_on_field": self.beta_on_field(),
        }
        for t in thresholds:
            out[f"p_top_{t}"] = self.p_top(t)
            out[f"se_top_{t}"] = self.se_p_top(t)
            # Below ~ field_size / n_rivals the empirical rival count cannot
            # resolve the threshold and the Cornish-Fisher tail is load-bearing.
            out[f"extrapolated_top_{t}"] = int(bool(t < self.field_size / 200))
        return out

    def histogram(self, bins=(100, 1_000, 10_000, 100_000, 1_000_000)) -> dict[str, float]:
        edges = [0, *bins, self.field_size + 1]
        out = {}
        for lo, hi in itertools.pairwise(edges):
            mask = (self.ranks > lo) & (self.ranks <= hi)
            out[f"({lo:,}, {hi:,}]"] = float(mask.mean())
        return out


@dataclass(frozen=True)
class Counterfactual:
    """Paired comparison of two candidate decisions.

    Both candidates are evaluated on the *same* simulations and the *same*
    sampled field, so the difference is a paired statistic. That matters: the
    unpaired standard error of a 0.5pp difference in P(top 10k) would need
    hundreds of thousands of simulations to resolve, while the paired one
    typically resolves it in a few thousand.
    """

    a: RankDistribution
    b: RankDistribution

    def delta_p_top(self, threshold: int) -> float:
        return self.a.p_top(threshold) - self.b.p_top(threshold)

    def se_delta_p_top(self, threshold: int) -> float:
        ia = (self.a.ranks <= threshold).astype(np.float64)
        ib = (self.b.ranks <= threshold).astype(np.float64)
        d = ia - ib
        return float(d.std(ddof=1) / math.sqrt(len(d)))

    def delta_points(self) -> float:
        return float((self.a.my_scores - self.b.my_scores).mean())

    def se_delta_points(self) -> float:
        d = self.a.my_scores - self.b.my_scores
        return float(d.std(ddof=1) / math.sqrt(len(d)))

    def summary(self, thresholds=(100, 1_000, 10_000, 100_000)) -> dict:
        out: dict[str, float | str] = {
            "a": self.a.label,
            "b": self.b.label,
            "delta_mean_points": self.delta_points(),
            "se_delta_mean_points": self.se_delta_points(),
        }
        for t in thresholds:
            out[f"delta_p_top_{t}"] = self.delta_p_top(t)
            out[f"se_delta_p_top_{t}"] = self.se_delta_p_top(t)
        return out
