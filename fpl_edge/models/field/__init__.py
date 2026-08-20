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
* :mod:`~fpl_edge.models.field.hybrid` -- the sampler:
  ``sample_squads(n, cohort='top1k'|'elite'|'overall')`` returning arrays the
  sim scores directly, degrading to ownership marginals with an explicit
  ``ownership_marginals:prior`` label when (as before GW1) no picks exist.
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

__all__ = [
    "COHORTS",
    "Cohort",
    "CohortRates",
    "DriftRates",
    "FieldSample",
    "FlowVelocity",
    "HybridConfig",
    "HybridFieldSampler",
    "ObservedSquads",
    "PROVENANCE_EMPIRICAL",
    "PROVENANCE_EMPIRICAL_DRIFTED",
    "PROVENANCE_HYBRID",
    "PROVENANCE_HYBRID_DRIFTED",
    "PROVENANCE_MARGINALS_PRIOR",
    "apply_transfer_drift",
    "drift_rates",
    "last_locked_gw",
    "load_observed_squads",
    "measure_cohort",
    "measure_flow_velocity",
    "rates_from_observed",
]
