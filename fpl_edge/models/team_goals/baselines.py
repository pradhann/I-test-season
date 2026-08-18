"""The two naive baselines the Dixon-Coles model is required to beat.

Neither is a strawman. The first is the null hypothesis "no club is better than
any other, home teams score more"; anything that cannot beat it is measuring
noise. The second, last season's goals-for and goals-against, is what a
reasonable person with a newspaper would do, and it is genuinely hard to beat
over a full season -- team strength is persistent.

Both baselines carry a known, deliberate weakness that the Dixon-Coles model
does not: a promoted club has no previous-season row, so the table baseline
hands it exactly the league average. That is the failure mode :mod:`.promoted`
exists to fix, and :mod:`.evaluate` reports a promoted-clubs-only slice so the
size of it is a number rather than an argument.

``rho`` is fitted for every baseline by a one-dimensional MLE against its own
implied rates. Withholding the low-score correction from the baselines would
make the comparison on clean sheets unfair to them and the headline result
unearned.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.team_goals.base import BaseGoalModel
from fpl_edge.models.team_goals.data import (
    InsufficientHistoryError,
    read_finished_matches,
    season_order,
)
from fpl_edge.models.team_goals.dixon_coles import RHO_BOUNDS, _decay_weights
from fpl_edge.models.team_goals.scoreline import GoalRates
from fpl_edge.store import Snapshot
from fpl_edge.types import Season

_TAU_FLOOR = 1e-9


def fit_rho(
    lam: np.ndarray, mu: np.ndarray, x: np.ndarray, y: np.ndarray, w: np.ndarray | None = None
) -> float:
    """One-dimensional MLE for the Dixon-Coles dependence, rates held fixed."""
    if len(x) == 0:
        return 0.0
    w = np.ones_like(lam) if w is None else w
    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)

    def nll(rho: float) -> float:
        tau = np.ones_like(lam)
        tau = np.where(m00, 1.0 - lam * mu * rho, tau)
        tau = np.where(m01, 1.0 + lam * rho, tau)
        tau = np.where(m10, 1.0 + mu * rho, tau)
        tau = np.where(m11, 1.0 - rho, tau)
        return float(-(w * np.log(np.clip(tau, _TAU_FLOOR, None))).sum())

    res = minimize_scalar(nll, bounds=RHO_BOUNDS, method="bounded")
    return float(res.x)


@dataclass(frozen=True, slots=True)
class LeagueAverages:
    home_goals: float
    away_goals: float
    rho: float


class HomeAdvantageOnlyModel(BaseGoalModel):
    """Every club identical; only home advantage and the league scoring rate.

    The null model. Its log loss is the price of knowing nothing about who is
    playing, and every other number in the evaluation is meaningful only
    relative to it.
    """

    def __init__(
        self,
        *,
        half_life_days: float | None = None,
        max_goals: int = 8,
        card: ModelCard | None = None,
    ) -> None:
        super().__init__(max_goals=max_goals)
        self.half_life_days = half_life_days
        self.card = card if card is not None else HOME_ONLY_CARD
        self._avg: LeagueAverages | None = None
        self._key: tuple[object, ...] | None = None

    def fit(self, snapshot: Snapshot) -> LeagueAverages:
        key = (snapshot.as_of, self.half_life_days)
        if self._avg is not None and self._key == key:
            return self._avg
        m = read_finished_matches(snapshot, min_matches=20)
        w = (
            np.ones(len(m))
            if self.half_life_days is None
            else _decay_weights(m["kickoff_utc"], snapshot.as_of, self.half_life_days)
        )
        x = m["home_score"].to_numpy(float)
        y = m["away_score"].to_numpy(float)
        lam = float((w * x).sum() / w.sum())
        mu = float((w * y).sum() / w.sum())
        rho = fit_rho(np.full(len(x), lam), np.full(len(x), mu), x, y, w)
        self._avg, self._key = LeagueAverages(lam, mu, rho), key
        return self._avg

    def rates_for(
        self, snapshot: Snapshot, season: Season, fixtures: pd.DataFrame
    ) -> dict[int, GoalRates]:
        avg = self.fit(snapshot)
        rates = GoalRates(avg.home_goals, avg.away_goals, avg.rho)
        return {int(f): rates for f in fixtures["fixture_id"]}


@dataclass(frozen=True, slots=True)
class TableRatings:
    """Previous-season goals-for / goals-against ratios, per club."""

    season: str
    attack_ratio: dict[int, float]
    defence_ratio: dict[int, float]
    home_goals: float
    away_goals: float
    rho: float
    missing: frozenset[int]

    def rates(self, home: int, away: int) -> GoalRates:
        lam = self.home_goals * self.attack_ratio.get(home, 1.0) * self.defence_ratio.get(away, 1.0)
        mu = self.away_goals * self.attack_ratio.get(away, 1.0) * self.defence_ratio.get(home, 1.0)
        return GoalRates(max(lam, 1e-3), max(mu, 1e-3), self.rho)


class LastSeasonTableModel(BaseGoalModel):
    """Ratio model on the previous season's goals-for and goals-against.

    ``lambda_home = mean_home_goals * GF_ratio(home) * GA_ratio(away)``, with the
    ratios taken against the previous season's league average. No fitting beyond
    the dependence parameter -- that is the point of a baseline.

    Promoted clubs are absent from the previous table and get a ratio of 1.0,
    i.e. the league average. This is exactly the silent fallback the design
    forbids for the real model; here it is retained deliberately so the cost of
    it can be measured.
    """

    def __init__(self, *, max_goals: int = 8, card: ModelCard | None = None) -> None:
        super().__init__(max_goals=max_goals)
        self.card = card if card is not None else TABLE_CARD
        self._ratings: TableRatings | None = None
        self._key: tuple[object, ...] | None = None

    def fit(self, snapshot: Snapshot, season: Season) -> TableRatings:
        key = (snapshot.as_of, str(season))
        if self._ratings is not None and self._key == key:
            return self._ratings
        m = read_finished_matches(snapshot, min_matches=20)
        prior_seasons = [s for s in season_order(m["season"]) if s < str(season)]
        if not prior_seasons:
            raise InsufficientHistoryError(
                f"no completed season before {season} visible at {snapshot.as_of}"
            )
        last = prior_seasons[-1]
        sub = m[m["season"] == last]
        n = len(sub)
        league_rate = float((sub["home_score"].sum() + sub["away_score"].sum()) / (2 * n))
        atk: dict[int, float] = {}
        dfn: dict[int, float] = {}
        for code in sorted(set(sub["home_team_code"]) | set(sub["away_team_code"])):
            h = sub[sub["home_team_code"] == code]
            a = sub[sub["away_team_code"] == code]
            played = len(h) + len(a)
            gf = float(h["home_score"].sum() + a["away_score"].sum()) / played
            ga = float(h["away_score"].sum() + a["home_score"].sum()) / played
            atk[int(code)] = gf / league_rate
            dfn[int(code)] = ga / league_rate
        ratings = TableRatings(
            season=last,
            attack_ratio=atk,
            defence_ratio=dfn,
            home_goals=float(sub["home_score"].mean()),
            away_goals=float(sub["away_score"].mean()),
            rho=0.0,
            missing=frozenset(),
        )
        # Fit rho against the ratings' own in-sample implied rates.
        lam = np.array([ratings.rates(int(r.home_team_code), int(r.away_team_code)).home
                        for r in sub.itertuples(index=False)])
        mu = np.array([ratings.rates(int(r.home_team_code), int(r.away_team_code)).away
                       for r in sub.itertuples(index=False)])
        rho = fit_rho(lam, mu, sub["home_score"].to_numpy(float), sub["away_score"].to_numpy(float))
        ratings = TableRatings(
            season=last,
            attack_ratio=atk,
            defence_ratio=dfn,
            home_goals=ratings.home_goals,
            away_goals=ratings.away_goals,
            rho=rho,
            missing=frozenset(),
        )
        self._ratings, self._key = ratings, key
        return ratings

    def rates_for(
        self, snapshot: Snapshot, season: Season, fixtures: pd.DataFrame
    ) -> dict[int, GoalRates]:
        ratings = self.fit(snapshot, season)
        return {
            int(fx.fixture_id): ratings.rates(int(fx.home_team_code), int(fx.away_team_code))
            for fx in fixtures.itertuples(index=False)
        }

    def promoted_seen(self, fixtures: pd.DataFrame) -> set[int]:
        """Clubs in these fixtures with no previous-season rating (silent fallback)."""
        if self._ratings is None:
            return set()
        teams = set(fixtures["home_team_code"].astype(int)) | set(
            fixtures["away_team_code"].astype(int)
        )
        return teams - set(self._ratings.attack_ratio)




HOME_ONLY_CARD = ModelCard(
    name="team_goals.home_advantage_only",
    approach="Single league-wide home and away scoring rate, no club effects",
    baseline="uniform 1/3 outcome probabilities",
    metric="out-of-sample 1X2 log loss, walk-forward, refit every gameweek",
    score=1.07346,
    baseline_score=1.09861,  # log 3
    trained_through="2025-26",
    notes=(
        "REAL DATA, 3 seasons walk-forward, 1140 fixtures.",
        ("The null model: knowing only that home teams score more is worth 0.025 nats "
        "over a coin flip between three outcomes."),
        "Clean-sheet Brier 0.17854 against a base rate of 0.23202.",
    ),
)

TABLE_CARD = ModelCard(
    name="team_goals.last_season_table",
    approach="Previous-season goals-for / goals-against ratio model with fitted rho",
    baseline="team_goals.home_advantage_only",
    metric="out-of-sample 1X2 log loss, walk-forward, refit every gameweek",
    score=1.03778,
    baseline_score=1.07346,
    trained_through="2025-26",
    notes=(
        "REAL DATA, 3 seasons walk-forward, 1140 fixtures.",
        ("Clean-sheet Brier 0.17814 vs 0.17854 for the null model -- last season's "
        "table barely improves clean-sheet calibration even where it improves outcomes."),
        ("Promoted clubs silently receive the league average. On promoted fixtures it "
        "scores 1.06215, worse than its own overall 1.03778 and 0.15 nats behind "
        "Dixon-Coles, which is the cost of that fallback."),
    ),
)
