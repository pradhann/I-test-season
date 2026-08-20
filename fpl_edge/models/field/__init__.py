"""The FIELD: the joint distribution of rival squads, per cohort, with provenance.

P(top-10k) is decided by my squad against the *top-10k cohort's* squads --
their 15 picks, armbands and chips, jointly -- not against marginal ownership.
This package owns that distribution. Three constructions are compared in
``docs/platform/field_model.md``; what ships is a hybrid:

* :mod:`~fpl_edge.models.field.observed` -- real crawled squads, read
  point-in-time and bootstrap-resampled with their joint structure intact;
* :mod:`~fpl_edge.models.field.drift` -- the timing bridge: picks are public
  only after each deadline, so a GW-k decision sees GW k-1 squads, drifted
  forward on the live transfer-flow counters;
* :mod:`~fpl_edge.models.field.cohorts` -- captaincy shares and chip-play
  rates measured per cohort, with binomial errors, never priors in disguise;
* :mod:`~fpl_edge.models.field.share` -- the two quantities both called
  "ownership", given different types with differently named arrays so that
  passing one where the other belongs raises instead of clipping at 1.0;
* :mod:`~fpl_edge.models.field.hybrid` -- the sampler:
  ``sample_squads(n, cohort='top1k'|'elite'|'overall', as_of=...)`` returning
  arrays the sim scores directly, degrading to ownership marginals with an
  explicit ``ownership_marginals:prior`` label when (as before GW1) no picks
  exist.

Today (pre-GW1 2026-27) ``fact_manager_pick`` holds zero rows and every
gameweek deadline is in the future, so every cohort resolves to the marginal
prior. That is the correct answer, and the label says so on every sample.
"""

from fpl_edge.models.field.cohorts import CohortRates, measure_cohort, rates_from_observed
from fpl_edge.models.field.contracts import (
    COHORTS,
    PROVENANCE_EMPIRICAL,
    PROVENANCE_EMPIRICAL_DRIFTED,
    PROVENANCE_HYBRID,
    PROVENANCE_HYBRID_DRIFTED,
    PROVENANCE_MARGINALS_PRIOR,
    Cohort,
    FieldSample,
)
from fpl_edge.models.field.drift import (
    DriftRates,
    FlowVelocity,
    apply_transfer_drift,
    drift_rates,
    measure_flow_velocity,
)
from fpl_edge.models.field.hybrid import HybridConfig, HybridFieldSampler
from fpl_edge.models.field.observed import (
    ObservedSquads,
    last_locked_gw,
    load_observed_squads,
)
from fpl_edge.models.field.share import (
    EffectiveOwnership,
    InclusionProbability,
    ShareTypeError,
    captaincy_share,
    effective_ownership,
    inclusion_probability,
    reconcile,
    require_effective_ownership,
    require_inclusion,
    share_table,
)

__all__ = [
    "COHORTS",
    "Cohort",
    "CohortRates",
    "DriftRates",
    "EffectiveOwnership",
    "FieldSample",
    "FlowVelocity",
    "HybridConfig",
    "HybridFieldSampler",
    "InclusionProbability",
    "ObservedSquads",
    "PROVENANCE_EMPIRICAL",
    "PROVENANCE_EMPIRICAL_DRIFTED",
    "PROVENANCE_HYBRID",
    "PROVENANCE_HYBRID_DRIFTED",
    "PROVENANCE_MARGINALS_PRIOR",
    "ShareTypeError",
    "apply_transfer_drift",
    "captaincy_share",
    "drift_rates",
    "effective_ownership",
    "inclusion_probability",
    "last_locked_gw",
    "load_observed_squads",
    "measure_cohort",
    "measure_flow_velocity",
    "rates_from_observed",
    "reconcile",
    "require_effective_ownership",
    "require_inclusion",
    "share_table",
]
