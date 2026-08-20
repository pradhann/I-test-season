"""Multi-gameweek squad optimisation.

The entry point is :func:`fpl_edge.opt.milp.solve_horizon`. Read
:mod:`fpl_edge.opt.config` first: the objective mode is a mandatory, explicit
choice, and ``RANK_UTILITY`` refuses to run without a simulator rather than
quietly optimising expected points.
"""

from fpl_edge.opt.config import (
    FT_VALUE_DEFAULT,
    FT_VALUE_LIST_SOTA,
    REPORT_DECAY_BASES,
    AutosubWeights,
    ObjectiveMode,
    OptimizerConfig,
    SolverBackend,
    SolverConfig,
)
from fpl_edge.opt.interfaces import (
    POINTS_FORECAST_COLUMNS,
    PRICE_FORECAST_COLUMNS,
    VARIANCE_FORECAST_COLUMNS,
    PointsForecast,
    PointsVarianceForecast,
    PriceForecast,
    RankInputsUnavailableError,
    RankUtilityProvider,
    RankUtilityUnavailableError,
    StaticPriceForecast,
    TablePriceForecast,
)
from fpl_edge.opt.milp import (
    CUT_CRITERIA,
    InfeasibleError,
    ModelStats,
    NoIncumbentError,
    enumerate_plans,
    solve_horizon,
)
from fpl_edge.opt.plan import GwDecision, HorizonPlan
from fpl_edge.opt.problem import (
    CHIP_NAMES,
    ChipState,
    HorizonProblem,
    PlayerRow,
    Ruleset,
    SquadState,
    build_problem,
)
from fpl_edge.opt.scoring import (
    PlanInvalidError,
    assert_valid,
    banked_ft_value,
    decay_metrics,
    gw_contributions,
    replay_finances,
    score_plan,
    validate_plan,
)

__all__ = [
    "AutosubWeights",
    "CHIP_NAMES",
    "CUT_CRITERIA",
    "ChipState",
    "FT_VALUE_DEFAULT",
    "FT_VALUE_LIST_SOTA",
    "GwDecision",
    "HorizonPlan",
    "HorizonProblem",
    "InfeasibleError",
    "ModelStats",
    "NoIncumbentError",
    "ObjectiveMode",
    "OptimizerConfig",
    "POINTS_FORECAST_COLUMNS",
    "PRICE_FORECAST_COLUMNS",
    "PlanInvalidError",
    "PlayerRow",
    "PointsForecast",
    "PointsVarianceForecast",
    "PriceForecast",
    "REPORT_DECAY_BASES",
    "RankInputsUnavailableError",
    "RankUtilityProvider",
    "RankUtilityUnavailableError",
    "Ruleset",
    "SolverBackend",
    "SolverConfig",
    "SquadState",
    "StaticPriceForecast",
    "TablePriceForecast",
    "VARIANCE_FORECAST_COLUMNS",
    "assert_valid",
    "banked_ft_value",
    "build_problem",
    "decay_metrics",
    "enumerate_plans",
    "gw_contributions",
    "replay_finances",
    "score_plan",
    "solve_horizon",
    "validate_plan",
]
