"""Multi-gameweek squad optimisation.

The entry point is :func:`fpl_edge.opt.milp.solve_horizon`. Read
:mod:`fpl_edge.opt.config` first: the objective mode is a mandatory, explicit
choice, and ``RANK_UTILITY`` refuses to run without a simulator rather than
quietly optimising expected points.
"""

from fpl_edge.opt.config import (
    AutosubWeights,
    ObjectiveMode,
    OptimizerConfig,
    SolverBackend,
    SolverConfig,
)
from fpl_edge.opt.interfaces import (
    POINTS_FORECAST_COLUMNS,
    PRICE_FORECAST_COLUMNS,
    PointsForecast,
    PriceForecast,
    RankUtilityProvider,
    RankUtilityUnavailableError,
    StaticPriceForecast,
    TablePriceForecast,
)
from fpl_edge.opt.milp import (
    InfeasibleError,
    ModelStats,
    NoIncumbentError,
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
    gw_contributions,
    replay_finances,
    score_plan,
    validate_plan,
)

__all__ = [
    "CHIP_NAMES",
    "POINTS_FORECAST_COLUMNS",
    "PRICE_FORECAST_COLUMNS",
    "AutosubWeights",
    "ChipState",
    "GwDecision",
    "HorizonPlan",
    "HorizonProblem",
    "InfeasibleError",
    "NoIncumbentError",
    "ModelStats",
    "ObjectiveMode",
    "OptimizerConfig",
    "PlanInvalidError",
    "PlayerRow",
    "PointsForecast",
    "PriceForecast",
    "RankUtilityProvider",
    "RankUtilityUnavailableError",
    "Ruleset",
    "SolverBackend",
    "SolverConfig",
    "SquadState",
    "StaticPriceForecast",
    "TablePriceForecast",
    "assert_valid",
    "build_problem",
    "gw_contributions",
    "replay_finances",
    "score_plan",
    "solve_horizon",
    "validate_plan",
]
