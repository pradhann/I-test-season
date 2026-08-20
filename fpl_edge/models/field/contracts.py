"""What a field sample IS, and what it must admit about itself.

The objective is P(top-10k), which is a functional of the joint distribution of
rival squads -- who owns whom *together*, who wears the armband, who is holding
a chip -- not of any marginal. Three constructions of that joint distribution
exist in this repo (see ``docs/platform/field_model.md``), and they differ most
in what they can honestly claim to know at a given instant in the gameweek
cycle. So the sample type carries its provenance as a first-class field rather
than a log line: a decision made against a prior must be auditable as such.

The squad arrays themselves reuse :class:`fpl_edge.sim.field.FieldSquads`
verbatim. That is deliberate: ``FieldModel.score`` and
:func:`fpl_edge.sim.squad.score_squads` are the consumers, and inventing a
second 15-slot layout would mean a translation layer that exists only to have
bugs. A ``FieldSample`` is a ``FieldSquads`` plus the metadata the objective
needs and the sampler must not lose.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Literal, Mapping

import numpy as np

from fpl_edge.sim.field import FieldSquads

#: The cohorts a field sample can claim to represent. ``overall`` is the whole
#: ~6M-entry field; ``top1k`` is the top of the overall league (measurable only
#: once a gameweek has been scored); ``elite`` is the crawled pool of managers
#: with demonstrated long-run records (measurable one deadline after they lock).
Cohort = Literal["overall", "top1k", "elite"]

COHORTS: tuple[str, ...] = ("overall", "top1k", "elite")

#: Provenance labels, in decreasing order of how much they know. These are the
#: values that appear in ``FieldSample.provenance`` and they are load-bearing:
#: a backtest that scores a ``PRIOR``-provenance decision as if it had used
#: measured picks is lying to itself.
PROVENANCE_EMPIRICAL = "empirical_picks"           # observed squads, resampled
PROVENANCE_EMPIRICAL_DRIFTED = "empirical_picks+flow_drift"
PROVENANCE_HYBRID = "hybrid(empirical+marginals)"  # bootstrap mixed with PPS
PROVENANCE_HYBRID_DRIFTED = "hybrid(empirical+marginals)+flow_drift"
PROVENANCE_MARGINALS_PRIOR = "ownership_marginals:prior"  # no picks observable


@dataclass(frozen=True)
class FieldSample:
    """``n`` sampled rival squads for one cohort, with their provenance.

    ``squads`` is consumable directly by the simulator::

        field_model.score(sample.squads, points, minutes)   # FieldModel.score
        # or, without a FieldModel:
        score_squads(points[sample.squads.slots], ...)      # sim.squad

    ``chips`` is per-rival: the chip name each sampled rival is playing this
    gameweek, or ``None``. The current sim scores every rival at captain
    multiplier 2 and no bench boost; the array is carried so that upgrading the
    scorer does not require re-plumbing the sampler.
    """

    squads: FieldSquads
    cohort: str
    gw: int
    provenance: str
    #: How many *distinct observed* squads the sample rests on. 0 for a pure
    #: marginal prior. This is the number that sets the binomial standard error
    #: on every cohort share quoted from the sample.
    n_observed: int = 0
    #: The gameweek the observed squads locked at, if any. For a GW-k decision
    #: this is k-1 (picks are public only after each deadline), and the gap is
    #: what the drift step exists to cover.
    observed_gw: int | None = None
    #: Fraction of rivals drawn by resampling observed squads (the rest come
    #: from the marginal sampler).
    empirical_weight: float = 0.0
    drift_applied: bool = False
    chips: np.ndarray | None = None    # (n,) object, chip name or None
    #: Free-form audit trail: sample sizes, drift receipts, degradations taken.
    notes: tuple[str, ...] = ()
    #: Measured cohort chip-play rates for this gameweek, e.g.
    #: ``{"wildcard": 0.04, "3xc": 0.01}``. Empty when unmeasured -- never a
    #: fabricated prior dressed as a measurement.
    chip_rates: Mapping[str, float] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cohort not in COHORTS:
            raise ValueError(f"unknown cohort {self.cohort!r}; expected one of {COHORTS}")
        if self.chips is not None and len(self.chips) != self.squads.n_rivals:
            raise ValueError("chips must have one entry per sampled rival")
        if not 0.0 <= self.empirical_weight <= 1.0:
            raise ValueError("empirical_weight is a fraction of the sample")
        if self.n_observed == 0 and self.empirical_weight > 0.0:
            raise ValueError(
                "empirical_weight > 0 with n_observed == 0: a sample cannot lean "
                "on observations it does not have"
            )

    @property
    def n_rivals(self) -> int:
        return self.squads.n_rivals

    @property
    def is_prior(self) -> bool:
        """True when nothing in this sample was measured from real squads."""
        return self.n_observed == 0

    def standard_error(self, share: float) -> float:
        """Binomial SE on a cohort share, from the observed squads only.

        Resampling does not add information: 10,000 bootstrap draws of 600
        observed squads still know only what 600 squads know, so the SE is
        computed on ``n_observed``, not ``n_rivals``. For a pure prior it is
        undefined and asking for it is a category error, hence the raise.
        """
        if self.n_observed <= 0:
            raise ValueError(
                "this sample is a prior (n_observed=0); it has no sampling error "
                "because it has no sample"
            )
        p = min(max(float(share), 0.0), 1.0)
        return float(np.sqrt(p * (1.0 - p) / self.n_observed))
