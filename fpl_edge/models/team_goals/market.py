"""Market-implied goal rates: the baseline the statistical model has to beat.

Bookmaker prices aggregate team news, lineups, motivation, weather and the
opinion of everyone willing to stake money against them. Treating them as a
strong prior is not deference, it is arithmetic: a market that was systematically
beatable by a Dixon-Coles fit on public scorelines would not survive. So the
market is the primary baseline in the model card, and any claim that the
statistical model improves on it has to be a measured out-of-sample number.

Inversion
---------
The market quotes probabilities; the engine needs goal rates, because clean
sheets and goals-conceded points are not directly priced in the 1X2 market.
Given de-vigged ``(p_home, p_draw, p_away)`` and optionally ``P(total > line)``,
we solve for the ``(lambda_home, lambda_away)`` whose Dixon-Coles score matrix
reproduces them, in the least-squares sense.

Two unknowns against three or four constraints, so the system is overdetermined
and the residual is informative: a large residual means the quoted probabilities
are not consistent with *any* bivariate Poisson, which is a data-quality signal
worth surfacing rather than silently absorbing.

``rho`` is not identified by these markets. It defaults to 0 (independent
Poisson marginals with the tau correction switched off) and can be set from a
Dixon-Coles fit -- that matters for clean sheets specifically, since rho moves
mass into exactly the 0-0 / 1-0 / 0-1 cells that decide them.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.team_goals.base import BaseGoalModel
from fpl_edge.models.team_goals.odds import FixtureOdds, OddsProvider, fixture_key
from fpl_edge.models.team_goals.scoreline import (
    GoalRates,
    outcome_probs,
    prob_over,
    score_matrix,
)
from fpl_edge.store import Snapshot
from fpl_edge.types import Season

#: Starting point for the inversion: roughly a league-average home fixture.
_START = np.log(np.array([1.55, 1.15]))

_MAX_GOALS_FOR_INVERSION = 10


@dataclass(frozen=True, slots=True)
class Inversion:
    rates: GoalRates
    residual: float
    used_totals: bool


def invert_odds(
    odds: FixtureOdds,
    *,
    rho: float = 0.0,
    totals_weight: float = 1.0,
    max_goals: int = _MAX_GOALS_FOR_INVERSION,
) -> Inversion:
    """Solve for the goal rates implied by one fixture's prices."""
    target = np.array([odds.p_home, odds.p_draw, odds.p_away])
    use_totals = odds.has_totals
    line = float(odds.totals_line) if odds.totals_line is not None else 0.0
    p_over = float(odds.p_over) if odds.p_over is not None else 0.0

    def residuals(log_rates: np.ndarray) -> np.ndarray:
        lam, mu = np.exp(log_rates)
        mat = score_matrix(GoalRates(float(lam), float(mu), rho), max_goals)
        res = np.asarray(outcome_probs(mat)) - target
        if use_totals:
            res = np.concatenate([res, [totals_weight * (prob_over(mat, line) - p_over)]])
        return res

    sol = least_squares(residuals, _START, method="lm", xtol=1e-12, ftol=1e-12)
    lam, mu = np.exp(sol.x)
    return Inversion(
        rates=GoalRates(float(lam), float(mu), rho),
        residual=float(np.sqrt(np.mean(sol.fun**2))),
        used_totals=use_totals,
    )


class MarketImpliedModel(BaseGoalModel):
    """:class:`~fpl_edge.models.contracts.TeamStrengthModel` backed by odds.

    Fixtures with no quote are *omitted* from the output frame rather than
    filled in from somewhere else. Coverage is then an explicit property of the
    result, and :mod:`.evaluate` compares models on the covered subset so the
    market is never credited with predictions it did not make.
    """

    def __init__(
        self,
        provider: OddsProvider,
        *,
        rho: float = 0.0,
        max_goals: int = 8,
        card: ModelCard | None = None,
    ) -> None:
        super().__init__(max_goals=max_goals)
        self.provider = provider
        self.rho = rho
        self.card = card if card is not None else MARKET_CARD
        self.last_coverage: float = float("nan")
        self.last_residuals: list[float] = []

    def set_rho(self, rho: float) -> None:
        """Borrow the low-score dependence from a fitted Dixon-Coles model."""
        self.rho = float(rho)

    def rates_for(
        self, snapshot: Snapshot, season: Season, fixtures: pd.DataFrame
    ) -> dict[int, GoalRates]:
        keys = [fixture_key(str(season), int(f)) for f in fixtures["fixture_id"]]
        quotes = self.provider.odds_for(keys, snapshot.as_of)
        self.last_coverage = len(quotes) / len(keys) if keys else float("nan")
        self.last_residuals = []
        out: dict[int, GoalRates] = {}
        for fid, key in zip(fixtures["fixture_id"], keys, strict=True):
            quote = quotes.get(key)
            if quote is None:
                continue
            inv = invert_odds(quote, rho=self.rho)
            self.last_residuals.append(inv.residual)
            out[int(fid)] = inv.rates
        return out


MARKET_CARD = ModelCard(
    name="team_goals.market_implied",
    approach=(
        "Goal rates inverted from de-vigged bookmaker 1X2 and over/under prices "
        "by least squares against a Dixon-Coles score matrix"
    ),
    baseline="team_goals.dixon_coles",
    metric="out-of-sample 1X2 log loss, walk-forward, refit every gameweek",
    score=1.00647,
    baseline_score=1.02862,
    trained_through="2025-26 (synthetic)",
    notes=(
        ("SYNTHETIC DATA ONLY. fact_odds is empty, so this has never been measured "
        "against real bookmaker prices and the score is not a claim about them."),
        ("The synthetic bookmaker prices off the true goal rates with 0.08 log-normal "
        "noise and a proportional overround, so its margin here is a design "
        "parameter of the simulator, not evidence."),
        "Coverage 93.6% of fixtures; the 6.4% nobody priced are omitted, not imputed.",
        "Clean-sheet Brier 0.19023 vs 0.19302 Dixon-Coles; RPS(1X2) 0.20722 vs 0.21457.",
        "Beats every model tried, on every metric, on the fixtures it covers.",
    ),
)
