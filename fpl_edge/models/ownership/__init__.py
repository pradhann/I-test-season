"""Ownership, captaincy share and effective ownership.

The engine maximises P(rank < threshold), not expected points. Rank is decided
by my score against the field's, and the field's score is the effective-
ownership-weighted sum of player points. So this package is not a reporting
nicety: without it the objective is not computable at all, and every
"differential" argument is unfalsifiable.

What is measured, and what is not
---------------------------------

* **Overall ownership** -- backtested against real realised ownership,
  leave-one-season-out over five seasons, beating both persistence and transfer
  momentum. A real number.
* **GW1 cold start** -- backtested against real pre-deadline snapshots recovered
  from the upstream dataset's git history. A real number, with much wider
  uncertainty, quantified by horizon and ownership band.
* **Captaincy share** -- measured on a simulated field, because no public
  per-player captaincy series exists. A test of functional form only.
* **Top-10k ownership** -- a prior. Before the first deadline the overall league
  standings are empty and every entry's picks endpoint returns 404, so it cannot
  be measured. The sampler that will measure it is built and ready.

Every output frame carries the flag saying which of these it is.
"""

from fpl_edge.models.ownership.eo import (
    CAPTAIN_MULTIPLIER,
    TRIPLE_CAPTAIN_MULTIPLIER,
    FieldShares,
    effective_ownership,
    my_multiplier,
    rank_edge,
    start_share_from_ownership,
)
from fpl_edge.models.ownership.elite import (
    ElitePicksSampler,
    EliteSample,
    EliteTiltParams,
    elite_tilt,
)
from fpl_edge.models.ownership.model import OwnershipForecaster, OwnershipParams, build_card

__all__ = [
    "CAPTAIN_MULTIPLIER",
    "ElitePicksSampler",
    "EliteSample",
    "EliteTiltParams",
    "FieldShares",
    "OwnershipForecaster",
    "OwnershipParams",
    "TRIPLE_CAPTAIN_MULTIPLIER",
    "build_card",
    "effective_ownership",
    "elite_tilt",
    "my_multiplier",
    "rank_edge",
    "start_share_from_ownership",
]
