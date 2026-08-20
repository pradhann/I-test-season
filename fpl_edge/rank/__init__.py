"""Rank-aware decision layer: the F3 -> F2 solver, with F1 as validator.

``docs/platform/rank_objectives.md`` measured four formulations of "what should
a weekly FPL solver actually maximise" and reached a layered answer (§8):

1. **F3 (strategic)** -- a deficit-ladder policy sets this week's risk posture
   from ``(D, tau)``. Its closed-form myopic boundary is a straight line and
   captures exact dynamic programming to within 0.1pp of P(top 10k), while any
   *fixed* risk posture gives up 9-19pp. :mod:`fpl_edge.rank.policy`.
2. **F2 (tactical)** -- the posture becomes one scalar ``theta(D, tau)``, and
   candidates are ranked by the certainty-equivalent ``m + theta s^2``. This is
   what the MILP optimises, as ``ObjectiveMode.RANK_MV``.
   :mod:`fpl_edge.rank.coefficients`.
3. **F1 (validator)** -- the full simulator re-runs the top-k enumerated plans
   on common random numbers and reports paired ``Delta P(top 10k)``. It is *not*
   the argmax loop: §7.4 records that F1's Monte Carlo could not separate 0 of 6
   candidate swaps at 2 SE, while the closed forms separate captaincy and hit
   decisions analytically from the same second moments the simulator estimates
   well. :mod:`fpl_edge.rank.validate`.

:mod:`fpl_edge.rank.state` owns the sufficient statistic itself, including the
one number the study could not derive and named as the first production task:
effective ``s`` per squad, estimated from the simulator's paired draws.
"""

from fpl_edge.rank.coefficients import (
    PROVENANCE_OWNERSHIP_MARGINALS,
    SHARE_COLUMNS,
    RankCoefficients,
    RankInputsUnavailableError,
    build_rank_coefficients,
    shares_from_field_samples,
    shares_from_frame,
)
from fpl_edge.rank.policy import (
    BALANCED,
    BASELINE_MENU,
    DIFFERENTIAL,
    PUNT,
    TEMPLATE,
    Archetype,
    boundary_slope,
    captaincy_score,
    certainty_equivalent,
    crossing_deficit,
    hit_is_justified,
    hit_threshold,
    lambda_effective,
    lambda_effective_soft,
    myopic_best,
    p_hit_holding,
    should_gamble,
    sigma_with_candidate,
    theta,
    variance_credit_sign,
)
from fpl_edge.rank.state import (
    PROVENANCE_LIVE_STANDINGS,
    PROVENANCE_PRESEASON,
    PROVENANCE_SIM_PAIRED,
    PROVENANCE_STYLISED_MENU,
    SEASON_GWS,
    TOP_K,
    DeficitMoments,
    RankState,
    deficit_moments,
    pace_increments,
    pace_path,
)

__all__ = [
    "BALANCED",
    "BASELINE_MENU",
    "DIFFERENTIAL",
    "PROVENANCE_LIVE_STANDINGS",
    "PROVENANCE_OWNERSHIP_MARGINALS",
    "PROVENANCE_PRESEASON",
    "PROVENANCE_SIM_PAIRED",
    "PROVENANCE_STYLISED_MENU",
    "PUNT",
    "SEASON_GWS",
    "SHARE_COLUMNS",
    "TEMPLATE",
    "TOP_K",
    "Archetype",
    "DeficitMoments",
    "RankCoefficients",
    "RankInputsUnavailableError",
    "RankState",
    "boundary_slope",
    "build_rank_coefficients",
    "captaincy_score",
    "certainty_equivalent",
    "crossing_deficit",
    "deficit_moments",
    "hit_is_justified",
    "hit_threshold",
    "lambda_effective",
    "lambda_effective_soft",
    "myopic_best",
    "p_hit_holding",
    "pace_increments",
    "pace_path",
    "shares_from_field_samples",
    "shares_from_frame",
    "should_gamble",
    "sigma_with_candidate",
    "theta",
    "variance_credit_sign",
]
