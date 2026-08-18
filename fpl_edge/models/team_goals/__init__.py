"""Team goal model: Dixon-Coles, market-implied baseline, and the evaluation.

Public surface::

    DixonColesModel        penalised-MLE bivariate goal model (the candidate)
    MarketImpliedModel     goal rates inverted from bookmaker prices (the bar)
    BlendedGoalModel       geometric blend, market where priced, model elsewhere
    HomeAdvantageOnlyModel null baseline
    LastSeasonTableModel   previous-season goals-for / goals-against baseline

All five satisfy :class:`fpl_edge.models.contracts.TeamStrengthModel`: they read
only through a :class:`~fpl_edge.store.Snapshot`, return the team-strength
contract frame, and expose ``score_matrix(fixture_id)``.

Measured out-of-sample numbers live in ``docs/models/team_goals.md`` and the
CSVs beside it; the model cards here carry the same figures.
"""

from fpl_edge.models.team_goals.baselines import HomeAdvantageOnlyModel, LastSeasonTableModel
from fpl_edge.models.team_goals.blend import BlendedGoalModel
from fpl_edge.models.team_goals.data import InsufficientHistoryError
from fpl_edge.models.team_goals.dixon_coles import (
    DixonColesFit,
    DixonColesModel,
    fit_dixon_coles,
)
from fpl_edge.models.team_goals.market import MarketImpliedModel, invert_odds
from fpl_edge.models.team_goals.odds import (
    FixtureOdds,
    FrameOddsProvider,
    NullOddsProvider,
    OddsProvider,
    SnapshotOddsProvider,
    fixture_key,
)
from fpl_edge.models.team_goals.promoted import (
    FALLBACK_PROMOTED_PRIOR,
    PromotedPrior,
    fit_promoted_prior,
)
from fpl_edge.models.team_goals.scoreline import (
    GoalRates,
    clean_sheet_probs,
    outcome_probs,
    score_matrix,
)

__all__ = [
    "FALLBACK_PROMOTED_PRIOR",
    "BlendedGoalModel",
    "DixonColesFit",
    "DixonColesModel",
    "FixtureOdds",
    "FrameOddsProvider",
    "GoalRates",
    "HomeAdvantageOnlyModel",
    "InsufficientHistoryError",
    "LastSeasonTableModel",
    "MarketImpliedModel",
    "NullOddsProvider",
    "OddsProvider",
    "PromotedPrior",
    "SnapshotOddsProvider",
    "clean_sheet_probs",
    "fit_dixon_coles",
    "fit_promoted_prior",
    "fixture_key",
    "invert_odds",
    "outcome_probs",
    "score_matrix",
]
