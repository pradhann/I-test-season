"""Monte Carlo simulator and rank utility.

The entry points other packages should use:

``SeasonSimulator``
    Draws the rest of the season, samples the field, and returns a
    :class:`RankDistribution` for any candidate :class:`SquadPlan`.
``rank_utility`` / ``make_objective``
    The objective the optimizer maximises. ``make_objective`` returns a plain
    ``RankDistribution -> float`` callable with no simulator state attached.
``Counterfactual``
    A paired comparison of two candidates on identical simulations.

Read ``docs/models/simulator.md`` before changing anything in here; the
correlation structure is load-bearing and easy to break silently.
"""

from fpl_edge.sim.calibration import Anchor, validate_field, validate_points_model
from fpl_edge.sim.engine import SeasonSimulator, SquadPlan, greedy_squad, template_squad
from fpl_edge.sim.field import DEFAULT_FIELD_SIZE, FieldConfig, FieldModel, FieldSquads
from fpl_edge.sim.rank import Counterfactual, RankDistribution, rank_from_scores
from fpl_edge.sim.squad import PlayerUniverse, Squad, apply_autosubs, pick_best_xi, score_squads
from fpl_edge.sim.utility import (
    RankUtility,
    catastrophe_loss,
    expected_points_objective,
    make_objective,
    rank_utility,
    rank_utility_of,
)

__all__ = [
    "DEFAULT_FIELD_SIZE",
    "Anchor",
    "Counterfactual",
    "FieldConfig",
    "FieldModel",
    "FieldSquads",
    "PlayerUniverse",
    "RankDistribution",
    "RankUtility",
    "SeasonSimulator",
    "Squad",
    "SquadPlan",
    "apply_autosubs",
    "catastrophe_loss",
    "expected_points_objective",
    "greedy_squad",
    "make_objective",
    "pick_best_xi",
    "rank_from_scores",
    "rank_utility",
    "rank_utility_of",
    "score_squads",
    "template_squad",
    "validate_field",
    "validate_points_model",
]
