"""Priors for clubs with no top-flight history.

Three clubs enter the Premier League every August with zero rows in the
warehouse. A maximum-likelihood fit has nothing to say about them, and the
default behaviour of a ridge-regularised fit -- shrink to zero, i.e. to the
league average -- is *wrong in a known direction*: promoted sides score fewer
and concede more than the average top-flight club, systematically, every
season. Treating Sunderland's first Premier League fixture as a league-average
match is not a neutral choice, it is a biased one.

So the prior is estimated, not assumed. For every promotion event observable in
the training window we measure the promoted club's first-season goal rates
against that season's league average:

    attack_offset  = log(goals scored per match / league goals per team-match)
    defence_offset = log(goals conceded per match / league goals per team-match)

which are method-of-moments estimates of exactly the additive log-rate offsets
the Dixon-Coles model parameterises. Pooling those gives a mean and a spread;
the spread becomes the prior's standard deviation, so the model says "weaker
than average, and here is how unsure we are" rather than picking a point.

If a prior-division strength covariate is available (promotion route: 1 =
champions, 2 = runner-up, 3 = play-off winner, or any continuous strength
index), the pooled mean is replaced by a regression on it, which is the
"regression of promoted-side first-season performance on prior-division
strength" the design calls for. Without that covariate the regression collapses
to its intercept and says so in ``covariate``.

Known bias, stated rather than hidden: promoted clubs do not play themselves,
so their opponent pool is marginally stronger than an established club's. The
effect is second order (2 of 38 matches) and biases the prior slightly *too*
pessimistic. It is not corrected for.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.models.team_goals.data import (
    InsufficientHistoryError,
    promoted_team_codes,
    season_order,
    teams_in_season,
)

#: Prior standard deviation on an established club's attack/defence offset.
#: Wide enough to be nearly uninformative once a club has a season of matches,
#: tight enough to keep the fit well conditioned in the first few gameweeks.
ESTABLISHED_PRIOR_SD = 0.50


@dataclass(frozen=True, slots=True)
class PromotedPrior:
    """Fitted prior over a newly promoted club's attack and defence offsets."""

    attack_mean: float
    defence_mean: float
    attack_sd: float
    defence_sd: float
    attack_route_slope: float
    defence_route_slope: float
    covariate: str
    route_centre: float
    n_clubs: int
    n_seasons: int
    source: str

    def offsets(self, route: float | None = None) -> tuple[float, float]:
        """``(attack_offset, defence_offset)`` for one promoted club.

        A better prior-division finish (lower route number) buys attacking
        strength and defensive solidity; the slopes are signed so that a missing
        covariate simply returns the pooled mean.
        """
        if route is None or self.covariate == "none":
            return self.attack_mean, self.defence_mean
        centred = float(route) - self.route_centre
        return (
            self.attack_mean + self.attack_route_slope * centred,
            self.defence_mean + self.defence_route_slope * centred,
        )

    @property
    def is_pessimistic(self) -> bool:
        """True when the prior actually says 'weaker than a league-average club'."""
        return self.attack_mean < 0.0 < self.defence_mean


#: A documented *assumption* for the cold-start case where no promotion event is
#: observable at all. It is not a measurement and is never used unless the caller
#: opts in explicitly, because a silently-applied guess is the thing this module
#: exists to prevent. Any model card built on it must say so.
FALLBACK_PROMOTED_PRIOR = PromotedPrior(
    attack_mean=-0.18,
    defence_mean=0.20,
    attack_sd=0.22,
    defence_sd=0.22,
    attack_route_slope=0.0,
    defence_route_slope=0.0,
    covariate="none",
    route_centre=2.0,
    n_clubs=0,
    n_seasons=0,
    source="assumed_fallback",
)


def first_season_offsets(matches: pd.DataFrame) -> pd.DataFrame:
    """Per-club first-season log goal-rate offsets for every observed promotion.

    Returns one row per (season, promoted club) with ``attack_offset`` and
    ``defence_offset`` measured against that season's own league average, so
    league-wide scoring drift cancels out.
    """
    rows: list[dict[str, object]] = []
    seasons = season_order(matches["season"]) if not matches.empty else []
    for season in seasons[1:]:  # the first observable season has no "prior" to be new to
        target = teams_in_season(matches, season)
        promoted = promoted_team_codes(matches, target, season=season)
        if not promoted:
            continue
        sub = matches[matches["season"] == season]
        if sub.empty:
            continue
        league_rate = float((sub["home_score"].sum() + sub["away_score"].sum()) / (2 * len(sub)))
        if league_rate <= 0:
            continue
        for code in sorted(promoted):
            home = sub[sub["home_team_code"] == code]
            away = sub[sub["away_team_code"] == code]
            played = len(home) + len(away)
            if played == 0:
                continue
            scored = float(home["home_score"].sum() + away["away_score"].sum())
            conceded = float(home["away_score"].sum() + away["home_score"].sum())
            # Half-goal smoothing: a promoted side that fails to score in a
            # partial season must not push a log offset to -inf.
            rows.append(
                {
                    "season": season,
                    "team_code": int(code),
                    "matches": played,
                    "attack_offset": float(np.log((scored + 0.5) / played / league_rate)),
                    "defence_offset": float(np.log((conceded + 0.5) / played / league_rate)),
                }
            )
    return pd.DataFrame(rows)


def fit_promoted_prior(
    matches: pd.DataFrame,
    *,
    routes: pd.DataFrame | None = None,
    min_clubs: int = 3,
    allow_fallback: bool = False,
) -> PromotedPrior:
    """Estimate the promoted-club prior from observable promotion events.

    ``routes`` is an optional ``(season, team_code, route)`` frame carrying the
    prior-division strength covariate. Supplying it turns the pooled mean into a
    regression; omitting it is fine and is recorded honestly in ``covariate``.

    Raises :class:`InsufficientHistoryError` rather than inventing a number when
    fewer than ``min_clubs`` promotions are observable, unless the caller
    explicitly accepts :data:`FALLBACK_PROMOTED_PRIOR`.
    """
    obs = first_season_offsets(matches) if not matches.empty else pd.DataFrame()
    if len(obs) < min_clubs:
        if allow_fallback:
            return FALLBACK_PROMOTED_PRIOR
        raise InsufficientHistoryError(
            f"only {len(obs)} observable promotion events in the training window, "
            f"need {min_clubs}. Refusing to fall back to a league-average prior "
            f"for promoted clubs; pass allow_fallback=True to accept the "
            f"documented assumed prior instead."
        )

    atk = obs["attack_offset"].to_numpy(dtype=float)
    dfn = obs["defence_offset"].to_numpy(dtype=float)
    # ddof=1: this is a prior over the next club drawn from the population, so
    # we want the sample spread, not the spread of the mean.
    atk_sd = float(np.std(atk, ddof=1)) if len(atk) > 1 else 0.25
    dfn_sd = float(np.std(dfn, ddof=1)) if len(dfn) > 1 else 0.25

    covariate = "none"
    a_slope = d_slope = 0.0
    centre = 2.0
    a_mean, d_mean = float(atk.mean()), float(dfn.mean())

    if routes is not None and not routes.empty:
        merged = obs.merge(routes, on=["season", "team_code"], how="inner")
        merged = merged[merged["route"].notna()]
        if len(merged) >= 4 and merged["route"].nunique() >= 2:
            x = merged["route"].to_numpy(dtype=float)
            centre = float(x.mean())
            xc = x - centre
            denom = float((xc**2).sum())
            ya = merged["attack_offset"].to_numpy(dtype=float)
            yd = merged["defence_offset"].to_numpy(dtype=float)
            a_slope = float((xc * (ya - ya.mean())).sum() / denom)
            d_slope = float((xc * (yd - yd.mean())).sum() / denom)
            a_mean, d_mean = float(ya.mean()), float(yd.mean())
            # Residual spread is what the prior is uncertain about once the
            # covariate has explained what it can.
            ra = ya - (a_mean + a_slope * xc)
            rd = yd - (d_mean + d_slope * xc)
            dof = max(len(merged) - 2, 1)
            atk_sd = float(np.sqrt((ra**2).sum() / dof))
            dfn_sd = float(np.sqrt((rd**2).sum() / dof))
            covariate = "promotion_route"

    return PromotedPrior(
        attack_mean=a_mean,
        defence_mean=d_mean,
        attack_sd=max(atk_sd, 0.05),
        defence_sd=max(dfn_sd, 0.05),
        attack_route_slope=a_slope,
        defence_route_slope=d_slope,
        covariate=covariate,
        route_centre=centre,
        n_clubs=len(obs),
        n_seasons=int(obs["season"].nunique()),
        source="fitted",
    )


@dataclass(frozen=True, slots=True)
class TeamPriors:
    """Per-team Gaussian priors handed to the Dixon-Coles MAP fit.

    Established clubs get mean 0 (the league average) with a wide spread, which
    is honest: we have data for them and the prior should get out of the way.
    Promoted clubs get the fitted promoted mean with its measured spread.
    """

    codes: np.ndarray
    attack_mean: np.ndarray
    attack_sd: np.ndarray
    defence_mean: np.ndarray
    defence_sd: np.ndarray
    promoted: np.ndarray

    @property
    def n_promoted(self) -> int:
        return int(self.promoted.sum())


def build_team_priors(
    codes: list[int],
    promoted: set[int],
    prior: PromotedPrior,
    *,
    routes: dict[int, float] | None = None,
    established_sd: float = ESTABLISHED_PRIOR_SD,
) -> TeamPriors:
    codes_arr = np.asarray(codes, dtype=int)
    n = codes_arr.size
    a_mean = np.zeros(n)
    d_mean = np.zeros(n)
    a_sd = np.full(n, established_sd)
    d_sd = np.full(n, established_sd)
    is_promoted = np.zeros(n, dtype=bool)
    for i, code in enumerate(codes_arr):
        if int(code) in promoted:
            route = (routes or {}).get(int(code))
            am, dm = prior.offsets(route)
            a_mean[i], d_mean[i] = am, dm
            a_sd[i], d_sd[i] = prior.attack_sd, prior.defence_sd
            is_promoted[i] = True
    return TeamPriors(codes_arr, a_mean, a_sd, d_mean, d_sd, is_promoted)
