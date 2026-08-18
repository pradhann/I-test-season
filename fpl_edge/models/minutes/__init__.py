"""Minutes model: the three-way distribution over how long a player is on the pitch.

Minutes are the largest single driver of FPL variance - a 12-point haul and a
blank differ mostly by whether the player started - and the public projections
handle them worst, usually by multiplying a points-per-90 by a hand-set "80%
chance to start". This package produces a real distribution over
``(unavailable, cameo, full)`` and measures it.

Two independent approaches are implemented so the choice can be made on
out-of-sample loss rather than on preference:

* :class:`~fpl_edge.models.minutes.hierarchical.HierarchicalMinutesModel` -
  empirical-Bayes shrinkage toward a (position x club depth) prior.
* :class:`~fpl_edge.models.minutes.gbm.GBMMinutesModel` - histogram gradient
  boosting over the engineered features.

Both are scored against three baselines in
:mod:`fpl_edge.models.minutes.evaluate`; the measured numbers are on each
model's :class:`~fpl_edge.models.contracts.ModelCard` and in
``docs/models/minutes.md``.
"""

from fpl_edge.models.minutes.baselines import (
    BaseRateBaseline,
    ChanceOfPlayingBaseline,
    PriorSeasonRateBaseline,
)
from fpl_edge.models.minutes.features import (
    COLD_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    bucket_of_minutes,
    build_feature_frame,
)
from fpl_edge.models.minutes.gbm import GBMMinutesModel
from fpl_edge.models.minutes.hierarchical import HierarchicalMinutesModel
from fpl_edge.models.minutes.training import TrainingSet, TrainingSetBuilder

__all__ = [
    "COLD_FEATURE_COLUMNS",
    "FEATURE_COLUMNS",
    "BaseRateBaseline",
    "ChanceOfPlayingBaseline",
    "GBMMinutesModel",
    "HierarchicalMinutesModel",
    "PriorSeasonRateBaseline",
    "TrainingSet",
    "TrainingSetBuilder",
    "bucket_of_minutes",
    "build_feature_frame",
]
