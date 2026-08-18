"""Dixon-Coles bivariate goal model, fitted by penalised maximum likelihood.

Model
-----
For a match between home club *h* and away club *a*::

    log lambda_home = c + home_adv + attack[h] + defence[a]
    log lambda_away = c + attack[a] + defence[h]

``attack`` is signed so that positive means "scores more than average" and
``defence`` so that positive means "concedes more than average" -- i.e. defence
is a leakiness parameter. That sign convention is chosen because it makes the
promoted-club prior read the way you would say it out loud: attack negative,
defence positive.

The joint distribution is independent Poisson multiplied by the Dixon-Coles
``tau`` correction on the four low-score cells (see :mod:`.scoreline`), and
``rho`` is estimated jointly with everything else rather than pinned.

Time decay
----------
Each match is weighted ``exp(-xi * age_in_days)`` with ``xi = ln 2 / half_life``.
A club's squad in August is not the club of two seasons ago, and an unweighted
fit over five seasons will happily average across a relegation and a takeover.
The half-life is a hyperparameter, tuned out-of-sample in :mod:`.evaluate`, not
guessed.

Why MAP and not plain MLE
-------------------------
Plain MLE has no answer for a promoted club (no matches, unbounded parameters)
and is badly conditioned in the opening weeks of a season. A Gaussian prior per
club turns the fit into a MAP estimate: established clubs get a wide mean-zero
prior that the data overwhelms within a season, promoted clubs get the measured
promoted prior from :mod:`.promoted`. A club with zero matches then falls back
to *the prior mean*, which is a deliberate, documented, weaker-than-average
rating -- not the league average that an unpenalised fit or a plain ridge would
hand it.

The gradient is analytic. That is worth the algebra: a walk-forward backtest
refits once per gameweek across three seasons, and finite differences over
~43 parameters made that ~40x slower. ``test_team_goals_dixon_coles.py`` checks
the analytic gradient against a finite-difference one so the speed is not bought
with a silent bug.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.team_goals.base import BaseGoalModel
from fpl_edge.models.team_goals.data import (
    InsufficientHistoryError,
    promoted_team_codes,
    read_finished_matches,
)
from fpl_edge.models.team_goals.promoted import (
    PromotedPrior,
    TeamPriors,
    build_team_priors,
    fit_promoted_prior,
)
from fpl_edge.models.team_goals.scoreline import GoalRates
from fpl_edge.store import Snapshot
from fpl_edge.types import Season

#: Bounds on the dependence parameter. Negative rho is the empirically observed
#: direction (it boosts 0-0 and 1-1, damps 1-0 and 0-1); the asymmetric upper
#: bound keeps tau(0,0) = 1 - lam*mu*rho safely positive for plausible rates.
RHO_BOUNDS = (-0.30, 0.15)

#: Clamp on the linear predictor, i.e. goal rates in roughly [0.02, 12].
_LOG_RATE_CLIP = (-4.0, 2.5)

_TAU_FLOOR = 1e-9

#: Decay half-life in days. Selected by the out-of-sample sweep on the synthetic
#: *validation* season (2022-23), which is outside the evaluation window, so the
#: reported numbers are not tuned on themselves. A sensitivity sweep on the real
#: warehouse puts the optimum at 240-400 days with a difference of 1e-4 nats
#: between them, i.e. the objective is flat here and the exact value does not
#: matter much. See docs/models/team_goals_half_life.csv.
DEFAULT_HALF_LIFE_DAYS = 400.0

MIN_MATCHES_TO_FIT = 200


@dataclass(frozen=True, slots=True)
class DixonColesFit:
    """Fitted parameters plus everything needed to audit the fit."""

    codes: np.ndarray
    intercept: float
    home_adv: float
    rho: float
    attack: np.ndarray
    defence: np.ndarray
    half_life_days: float
    n_matches: int
    effective_n: float
    as_of: dt.datetime
    promoted: frozenset[int]
    prior: PromotedPrior
    converged: bool
    neg_log_lik: float
    _index: dict[int, int] = field(default_factory=dict, compare=False)

    def index_of(self, team_code: int) -> int:
        try:
            return self._index[int(team_code)]
        except KeyError as exc:
            raise KeyError(f"team {team_code} is not in this fit") from exc

    def rates(self, home_code: int, away_code: int) -> GoalRates:
        h = self.index_of(home_code)
        a = self.index_of(away_code)
        lam = float(np.exp(self.intercept + self.home_adv + self.attack[h] + self.defence[a]))
        mu = float(np.exp(self.intercept + self.attack[a] + self.defence[h]))
        return GoalRates(lam, mu, self.rho)

    def table(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "team_code": self.codes,
                "attack": self.attack,
                "defence": self.defence,
                "is_promoted": [int(c) in self.promoted for c in self.codes],
            }
        ).sort_values("attack", ascending=False, ignore_index=True)


def _decay_weights(kickoffs: pd.Series, as_of: dt.datetime, half_life_days: float) -> np.ndarray:
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    age_days = (as_of - pd.to_datetime(kickoffs, utc=True)).dt.total_seconds().to_numpy() / 86400.0
    age_days = np.clip(age_days, 0.0, None)
    return np.exp(-np.log(2.0) * age_days / half_life_days)


def _objective(
    theta: np.ndarray,
    *,
    n_teams: int,
    hi: np.ndarray,
    ai: np.ndarray,
    x: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    priors: TeamPriors,
) -> tuple[float, np.ndarray]:
    """Negative penalised log-likelihood and its analytic gradient."""
    c, g, rho = theta[0], theta[1], theta[2]
    atk = theta[3 : 3 + n_teams]
    dfn = theta[3 + n_teams :]

    log_lam = np.clip(c + g + atk[hi] + dfn[ai], *_LOG_RATE_CLIP)
    log_mu = np.clip(c + atk[ai] + dfn[hi], *_LOG_RATE_CLIP)
    lam = np.exp(log_lam)
    mu = np.exp(log_mu)

    m00 = (x == 0) & (y == 0)
    m01 = (x == 0) & (y == 1)
    m10 = (x == 1) & (y == 0)
    m11 = (x == 1) & (y == 1)

    tau = np.ones_like(lam)
    tau = np.where(m00, 1.0 - lam * mu * rho, tau)
    tau = np.where(m01, 1.0 + lam * rho, tau)
    tau = np.where(m10, 1.0 + mu * rho, tau)
    tau = np.where(m11, 1.0 - rho, tau)
    tau_safe = np.clip(tau, _TAU_FLOOR, None)

    ll = float((w * (np.log(tau_safe) + x * log_lam - lam + y * log_mu - mu)).sum())

    dt_dlam = np.where(m00, -mu * rho, np.where(m01, rho, 0.0))
    dt_dmu = np.where(m00, -lam * rho, np.where(m10, rho, 0.0))
    dt_drho = np.where(
        m00, -lam * mu, np.where(m01, lam, np.where(m10, mu, np.where(m11, -1.0, 0.0)))
    )

    a_term = w * (x - lam + lam * dt_dlam / tau_safe)
    b_term = w * (y - mu + mu * dt_dmu / tau_safe)

    grad = np.zeros_like(theta)
    grad[0] = a_term.sum() + b_term.sum()
    grad[1] = a_term.sum()
    grad[2] = float((w * dt_drho / tau_safe).sum())
    grad[3 : 3 + n_teams] = np.bincount(hi, a_term, n_teams) + np.bincount(ai, b_term, n_teams)
    grad[3 + n_teams :] = np.bincount(ai, a_term, n_teams) + np.bincount(hi, b_term, n_teams)

    pa = 1.0 / priors.attack_sd**2
    pd_ = 1.0 / priors.defence_sd**2
    da = atk - priors.attack_mean
    dd = dfn - priors.defence_mean
    penalty = 0.5 * float((pa * da**2).sum() + (pd_ * dd**2).sum())

    obj = -(ll - penalty)
    grad = -grad
    grad[3 : 3 + n_teams] += pa * da
    grad[3 + n_teams :] += pd_ * dd
    return obj, grad


def fit_dixon_coles(
    matches: pd.DataFrame,
    *,
    as_of: dt.datetime,
    priors: TeamPriors,
    promoted: set[int],
    prior: PromotedPrior,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    max_iter: int = 500,
) -> DixonColesFit:
    """Penalised MLE over the matches supplied. Deterministic: no random start."""
    codes = priors.codes
    n = codes.size
    index = {int(c): i for i, c in enumerate(codes)}

    if matches.empty:
        hi = ai = np.zeros(0, dtype=int)
        x = y = np.zeros(0, dtype=float)
        w = np.zeros(0)
    else:
        keep = matches["home_team_code"].isin(index) & matches["away_team_code"].isin(index)
        sub = matches[keep]
        hi = sub["home_team_code"].map(index).to_numpy(dtype=int)
        ai = sub["away_team_code"].map(index).to_numpy(dtype=int)
        x = sub["home_score"].to_numpy(dtype=float)
        y = sub["away_score"].to_numpy(dtype=float)
        w = _decay_weights(sub["kickoff_utc"], as_of, half_life_days)

    mean_goals = float((x.sum() + y.sum()) / (2 * len(x))) if len(x) else 1.35
    theta0 = np.zeros(3 + 2 * n)
    theta0[0] = np.log(max(mean_goals, 0.2))
    theta0[1] = 0.20
    theta0[2] = -0.03
    theta0[3 : 3 + n] = priors.attack_mean
    theta0[3 + n :] = priors.defence_mean

    bounds = [(-2.0, 2.0), (-1.0, 1.0), RHO_BOUNDS] + [(-2.0, 2.0)] * (2 * n)
    res = minimize(
        lambda th: _objective(th, n_teams=n, hi=hi, ai=ai, x=x, y=y, w=w, priors=priors),
        theta0,
        jac=True,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": max_iter, "ftol": 1e-10, "gtol": 1e-7},
    )

    theta = res.x
    return DixonColesFit(
        codes=codes,
        intercept=float(theta[0]),
        home_adv=float(theta[1]),
        rho=float(theta[2]),
        attack=theta[3 : 3 + n].copy(),
        defence=theta[3 + n :].copy(),
        half_life_days=half_life_days,
        n_matches=len(x),
        effective_n=float(w.sum()),
        as_of=as_of,
        promoted=frozenset(int(c) for c in promoted),
        prior=prior,
        converged=bool(res.success),
        neg_log_lik=float(res.fun),
        _index=index,
    )


class DixonColesModel(BaseGoalModel):
    """:class:`~fpl_edge.models.contracts.TeamStrengthModel` implementation."""

    def __init__(
        self,
        *,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        routes: pd.DataFrame | None = None,
        allow_fallback_prior: bool = False,
        use_promoted_prior: bool = True,
        min_matches: int = MIN_MATCHES_TO_FIT,
        max_goals: int = 8,
        card: ModelCard | None = None,
    ) -> None:
        super().__init__(max_goals=max_goals)
        self.half_life_days = half_life_days
        self.routes = routes
        self.allow_fallback_prior = allow_fallback_prior
        # Ablation switch. With this False, promoted clubs fall back to the
        # mean-zero established prior -- i.e. the league average -- which is the
        # behaviour the promoted prior exists to replace. Kept so the value of
        # the prior is a measured delta rather than a design assertion.
        self.use_promoted_prior = use_promoted_prior
        self.min_matches = min_matches
        self.card = card if card is not None else DIXON_COLES_CARD
        self._fit: DixonColesFit | None = None
        self._fit_key: tuple[object, ...] | None = None

    # -- fitting -------------------------------------------------------------

    def fit(self, snapshot: Snapshot, season: Season) -> DixonColesFit:
        """Fit on everything visible at ``snapshot.as_of``. Cached per as-of."""
        key = (snapshot.as_of, str(season), self.half_life_days, self.use_promoted_prior)
        if self._fit is not None and self._fit_key == key:
            return self._fit

        matches = read_finished_matches(snapshot, min_matches=self.min_matches)
        target_teams = _teams_in_target_season(snapshot, str(season))
        promoted = promoted_team_codes(matches, target_teams, season=str(season))
        prior = fit_promoted_prior(
            matches, routes=self.routes, allow_fallback=self.allow_fallback_prior
        )
        codes = sorted(
            set(matches["home_team_code"]) | set(matches["away_team_code"]) | target_teams
        )
        route_map = None
        if self.routes is not None and not self.routes.empty:
            r = self.routes[self.routes["season"] == str(season)]
            route_map = {int(t): float(v) for t, v in zip(r["team_code"], r["route"], strict=True)}
        priors = build_team_priors(
            codes,
            promoted if self.use_promoted_prior else set(),
            prior,
            routes=route_map,
        )
        fit = fit_dixon_coles(
            matches,
            as_of=snapshot.as_of,
            priors=priors,
            promoted=promoted,
            prior=prior,
            half_life_days=self.half_life_days,
        )
        self._fit, self._fit_key = fit, key
        return fit

    def rates_for(
        self, snapshot: Snapshot, season: Season, fixtures: pd.DataFrame
    ) -> dict[int, GoalRates]:
        fit = self.fit(snapshot, season)
        return {
            int(fx.fixture_id): fit.rates(int(fx.home_team_code), int(fx.away_team_code))
            for fx in fixtures.itertuples(index=False)
        }


def _teams_in_target_season(snapshot: Snapshot, season: str) -> set[int]:
    """Clubs contesting the target season, from the published fixture list.

    The fixture *schedule* is public months in advance, so reading it is not
    leakage -- the result columns are, and the snapshot hides those.
    """
    fx = snapshot.table("fact_fixture", where="season = ?", params=[season])
    if fx.empty:
        raise InsufficientHistoryError(f"no fixtures known for {season} at {snapshot.as_of}")
    return set(fx["home_team_code"].astype(int)) | set(fx["away_team_code"].astype(int))


#: Populated from the committed walk-forward evaluation; see
#: docs/models/team_goals.md and docs/models/team_goals_metrics.csv.
DIXON_COLES_CARD = ModelCard(
    name="team_goals.dixon_coles",
    approach=(
        "Bivariate Dixon-Coles: per-club attack/defence log-rates, global home "
        "advantage, low-score tau correction, 400-day exponential time decay, "
        "fitted by penalised MLE (MAP) with a measured promoted-club prior"
    ),
    # The market is the baseline that matters and it is NOT this one. fact_odds
    # is empty, so on real data the market cannot be measured at all; the
    # strongest baseline that can be is the previous-season table. The measured
    # loss to a synthetic market is recorded in the notes rather than hidden.
    baseline="last-season goals-for/goals-against table",
    metric="out-of-sample 1X2 log loss, walk-forward, refit every gameweek",
    score=0.98184,
    baseline_score=1.03778,
    trained_through="2025-26",
    notes=(
        "REAL DATA, 3 seasons walk-forward (2023-24, 2024-25, 2025-26), 1140 fixtures.",
        "vs last-season table: -0.0559 log loss, 95% CI [-0.0722, -0.0409] (paired bootstrap).",
        "vs home-advantage-only: 1.07346, delta -0.0916, 95% CI [-0.1134, -0.0682].",
        "RPS(1X2) 0.20086 vs 0.21977 table, 0.23262 home-only.",
        "RPS(goal difference) 0.05768 vs 0.06125 table, 0.06406 home-only.",
        "Clean-sheet Brier 0.17022 vs 0.17814 table, 0.17854 home-only; base rate 0.23202.",
        ("LOSES TO THE MARKET on synthetic data where odds exist: 1.02862 vs 1.00647, "
        "delta +0.0221, 95% CI [+0.0100, +0.0353]. Clean-sheet Brier 0.19302 vs 0.19061."),
        "No odds are in fact_odds, so the market comparison is UNMEASURED on real data.",
        ("Promoted-prior ablation is within noise on real data: +0.0019 log loss, "
        "95% CI [-0.0022, +0.0058]; it is significant on clean-sheet Brier on synthetic "
        "data (+0.00082, 95% CI [+0.00010, +0.00155] without it)."),
    ),
)
