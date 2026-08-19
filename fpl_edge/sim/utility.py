"""Rank utility: the objective the optimizer actually maximises.

Expected points is the wrong objective and it is worth being precise about why.
Expected points is linear in the outcome, so it is indifferent to the shape of
the distribution; and because every manager is solving the same expected-points
problem against the same public data, its argmax converges on the template. A
template squad's rank distribution is tightly concentrated slightly above the
field median, which is precisely the shape that never finishes in the top 10k.

The functional form implemented here, exactly:

.. math::

    U = P(R \\le T) \\;+\\; w_s\\, P(R \\le S) \\;-\\; \\lambda \\cdot
        \\mathrm{CVaR}_{\\alpha}\\bigl[L(R)\\bigr]

    L(r) = \\mathrm{clip}\\!\\left(
        \\frac{\\log(r / T)}{\\log(N / T)},\\; 0,\\; 1 \\right)

where :math:`T` is ``RankUtilityConfig.target_rank``, :math:`S` is
``stretch_rank``, :math:`N` is the field size, :math:`\\lambda` is
``risk_lambda``, and :math:`\\mathrm{CVaR}_\\alpha` is the mean of the worst
:math:`\\alpha` fraction of simulations.

Design notes, because each choice is a claim:

* The first term is the stated objective, verbatim.
* The second term buys lottery tickets on the stretch goal without letting them
  dominate: ``stretch_weight`` defaults to 0.25, so hitting top 1k is worth
  a quarter of hitting top 10k *on top of* the top-10k credit it already earns.
* The penalty is on **log** rank, not rank. The difference between finishing
  400,000th and 2,000,000th is a real difference in how badly the season went;
  the difference between 11,000th and 12,000th is not. Linear rank would treat
  the second as 1000 times more important than it is.
* The penalty is a CVaR, not a variance. Variance punishes upside, which is
  self-defeating when the objective is a small exceedance probability. CVaR
  punishes only the left tail, which is what "catastrophic" means.
* :math:`U` is monotone: improving the rank in any single simulation weakly
  increases every term. An objective that could be gamed by finishing *worse*
  would be unusable, and ``test_sim_utility.py`` asserts this property.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from fpl_edge.models.contracts import RankUtilityConfig
from fpl_edge.sim.rank import RankDistribution

#: Default weight on the stretch target, relative to the primary target.
DEFAULT_STRETCH_WEIGHT = 0.25

#: Default CVaR tail fraction: "catastrophic" means the worst decile of seasons.
DEFAULT_CVAR_ALPHA = 0.10


@dataclass(frozen=True)
class RankUtility:
    """Decomposed rank utility, so a number can be argued with."""

    utility: float
    p_target: float
    p_stretch: float
    catastrophe: float
    se_utility: float
    target_rank: int
    stretch_rank: int
    risk_lambda: float
    stretch_weight: float
    cvar_alpha: float

    def as_dict(self) -> dict[str, float]:
        return {
            "utility": self.utility,
            "se_utility": self.se_utility,
            "p_target": self.p_target,
            "p_stretch": self.p_stretch,
            "catastrophe_cvar": self.catastrophe,
        }


def catastrophe_loss(ranks: np.ndarray, target_rank: int, field_size: int) -> np.ndarray:
    """Normalised log-rank loss ``L(r)``, 0 at the target and 1 at last place."""
    r = np.maximum(np.asarray(ranks, dtype=np.float64), 1.0)
    denom = math.log(max(field_size, target_rank + 1) / target_rank)
    return np.clip(np.log(r / target_rank) / denom, 0.0, 1.0)


def _cvar(loss: np.ndarray, alpha: float) -> float:
    n = len(loss)
    k = max(1, math.ceil(alpha * n))
    worst = np.partition(loss, n - k)[n - k:]
    return float(worst.mean())


def rank_utility(
    ranks: np.ndarray,
    config: RankUtilityConfig,
    *,
    field_size: int | None = None,
    stretch_weight: float = DEFAULT_STRETCH_WEIGHT,
    cvar_alpha: float = DEFAULT_CVAR_ALPHA,
) -> RankUtility:
    """Evaluate rank utility on a vector of simulated final ranks."""
    config.validate()
    n_field = field_size if field_size is not None else config.field_size
    if n_field is None:
        raise ValueError(
            "field_size is unknown: pass it explicitly or set RankUtilityConfig.field_size "
            "from the API's total_players"
        )
    ranks = np.asarray(ranks, dtype=np.float64)
    n = len(ranks)
    hit_t = (ranks <= config.target_rank).astype(np.float64)
    hit_s = (ranks <= config.stretch_rank).astype(np.float64)
    loss = catastrophe_loss(ranks, config.target_rank, n_field)
    cvar = _cvar(loss, cvar_alpha)

    utility = float(hit_t.mean() + stretch_weight * hit_s.mean() - config.risk_lambda * cvar)
    # Per-simulation utility contribution, used only for the standard error. The
    # CVaR term is not a plain mean, so this treats it as its plug-in estimate
    # and is a lower bound on the true SE; the tail term is the stable part.
    per_sim = hit_t + stretch_weight * hit_s
    se = float(np.std(per_sim, ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    return RankUtility(
        utility=utility,
        p_target=float(hit_t.mean()),
        p_stretch=float(hit_s.mean()),
        catastrophe=cvar,
        se_utility=se,
        target_rank=config.target_rank,
        stretch_rank=config.stretch_rank,
        risk_lambda=config.risk_lambda,
        stretch_weight=stretch_weight,
        cvar_alpha=cvar_alpha,
    )


def rank_utility_of(dist: RankDistribution, config: RankUtilityConfig, **kw) -> RankUtility:
    """Convenience wrapper for a :class:`RankDistribution`."""
    kw.setdefault("field_size", config.field_size or dist.field_size)
    return rank_utility(dist.ranks, config, **kw)


def make_objective(
    config: RankUtilityConfig,
    *,
    field_size: int | None = None,
    stretch_weight: float = DEFAULT_STRETCH_WEIGHT,
    cvar_alpha: float = DEFAULT_CVAR_ALPHA,
) -> Callable[[RankDistribution], float]:
    """Return the scalar objective the optimizer calls.

    Deliberately narrow: one argument in, one float out, no simulator state
    captured beyond the configuration. The optimizer should not be able to
    reach into the simulator, and the simulator should not know what a squad
    search looks like.
    """

    def objective(dist: RankDistribution) -> float:
        return rank_utility_of(
            dist, config, field_size=field_size or config.field_size or dist.field_size,
            stretch_weight=stretch_weight, cvar_alpha=cvar_alpha,
        ).utility

    objective.__doc__ = (
        f"Rank utility: P(rank<={config.target_rank:,}) "
        f"+ {stretch_weight}*P(rank<={config.stretch_rank:,}) "
        f"- {config.risk_lambda}*CVaR_{cvar_alpha}(normalised log-rank loss)"
    )
    return objective


def expected_points_objective() -> Callable[[RankDistribution], float]:
    """The naive baseline this project exists to beat."""

    def objective(dist: RankDistribution) -> float:
        return dist.expected_points()

    return objective
