"""Blend of the statistical fit and the market, in log-rate space.

If the market wins the head-to-head, the useful product is not "throw away the
Dixon-Coles fit" -- odds coverage is incomplete, quotes arrive late, and the
statistical model is the only thing available for a fixture nobody has priced.
The blend is the honest operational answer: geometric mean of the two goal-rate
vectors where both exist, statistical fit alone where the market is silent.

Geometric rather than arithmetic because the models are additive in *log* rates,
which is where their errors are roughly symmetric.

STATUS: RESEARCH, not in the production import closure (reachability audit 2026-08-20, docs/platform/AUDIT_2026-08-20.md). Kept deliberately: the market/model blend, waiting on the calibration loop. Nothing imports this from production code, and anything that starts to should say so in ROADMAP.
"""

from __future__ import annotations

import pandas as pd

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.team_goals.base import BaseGoalModel
from fpl_edge.models.team_goals.dixon_coles import DixonColesModel
from fpl_edge.models.team_goals.market import MarketImpliedModel
from fpl_edge.models.team_goals.scoreline import GoalRates
from fpl_edge.store import Snapshot
from fpl_edge.types import Season


class BlendedGoalModel(BaseGoalModel):
    """``market_weight`` of the market, the rest Dixon-Coles, in log space."""

    def __init__(
        self,
        dixon_coles: DixonColesModel,
        market: MarketImpliedModel,
        *,
        market_weight: float = 0.5,
        borrow_rho: bool = True,
        max_goals: int = 8,
        card: ModelCard | None = None,
    ) -> None:
        super().__init__(max_goals=max_goals)
        if not 0.0 <= market_weight <= 1.0:
            raise ValueError("market_weight must be in [0, 1]")
        self.dc = dixon_coles
        self.market = market
        self.market_weight = market_weight
        self.borrow_rho = borrow_rho
        self.card = card if card is not None else BLEND_CARD
        self.last_market_coverage: float = float("nan")

    def rates_for(
        self, snapshot: Snapshot, season: Season, fixtures: pd.DataFrame
    ) -> dict[int, GoalRates]:
        dc_rates = self.dc.rates_for(snapshot, season, fixtures)
        if self.borrow_rho:
            self.market.set_rho(self.dc.fit(snapshot, season).rho)
        mkt_rates = self.market.rates_for(snapshot, season, fixtures)
        self.last_market_coverage = self.market.last_coverage
        w = self.market_weight
        out: dict[int, GoalRates] = {}
        for fid, dcr in dc_rates.items():
            mr = mkt_rates.get(fid)
            if mr is None:
                out[fid] = dcr
                continue
            out[fid] = GoalRates(
                float(dcr.home ** (1 - w) * mr.home**w),
                float(dcr.away ** (1 - w) * mr.away**w),
                dcr.rho,
            )
        return out


BLEND_CARD = ModelCard(
    name="team_goals.blend",
    approach=(
        "Geometric blend of Dixon-Coles and market-implied goal rates in log space, "
        "equal weights, falling back to the fit where no quote exists"
    ),
    baseline="team_goals.dixon_coles",
    metric="out-of-sample 1X2 log loss, walk-forward, refit every gameweek",
    score=1.01199,
    baseline_score=1.02862,
    trained_through="2025-26 (synthetic)",
    notes=(
        "SYNTHETIC DATA ONLY; no real odds exist to blend with yet.",
        "Beats Dixon-Coles by 0.0166 log loss, 95% CI [-0.0232, -0.0105].",
        ("Not distinguishable from the market itself: +0.0055, 95% CI [-0.0008, +0.0122], "
        "while covering the 6.4% of fixtures the market does not price."),
        "Clean-sheet Brier 0.19034, statistically tied with the market's 0.19023.",
        "The blend weight is fixed at 0.5 and has NOT been tuned out of sample.",
    ),
)
