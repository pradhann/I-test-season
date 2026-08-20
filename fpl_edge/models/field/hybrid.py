"""The field sampler the engine should actually use: empirical when the world
permits it, marginal when it does not, and honest about which it was.

Why a hybrid rather than the winner of a bake-off: the three constructions
compared in ``docs/platform/field_model.md`` are good at different *times of
the week*. Bootstrapping observed squads is unbeatable on joint structure but
only exists after a deadline and only knows a few hundred squads; the Madow
marginal sampler knows nothing about co-ownership but works from any ownership
forecast at any instant and never runs out of diversity. So the hybrid:

1. **Resamples observed cohort squads** when the warehouse has them,
   preserving the exact joint distribution (budget, club rule, captaincy
   conditional on ownership, chip context);
2. **Drifts them across the timing gap** -- picks lock a week before the
   decision they inform -- using the live transfer-flow counters
   (:mod:`fpl_edge.models.field.drift`);
3. **Mixes in marginal draws** at the measured cohort marginals, which pays
   down the bootstrap's atom problem (n rivals from K observed squads repeat
   each squad n/K times) at the price of the marginal sampler's known
   correlation defects;
4. **Degrades to pure ownership marginals with a prior label** when nothing
   has been observed -- which before GW1 of a season is the truth, not a
   failure mode.

The mixture weight is ``K / (K + prior_strength)``: with the current ~600-squad
elite crawl and the default prior strength it leans on observation for about
sixty percent of the field, and approaches pure-empirical as the top-1k
sampler fills the tables.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.models.field.cohorts import CohortRates, measure_cohort
from fpl_edge.models.field.contracts import (
    PROVENANCE_EMPIRICAL,
    PROVENANCE_EMPIRICAL_DRIFTED,
    PROVENANCE_HYBRID,
    PROVENANCE_HYBRID_DRIFTED,
    PROVENANCE_MARGINALS_PRIOR,
    FieldSample,
)
from fpl_edge.models.field.drift import (
    apply_transfer_drift,
    drift_rates,
    measure_flow_velocity,
)
from fpl_edge.models.field.observed import load_observed_squads
from fpl_edge.models.field.share import (
    EffectiveOwnership,
    InclusionProbability,
    effective_ownership,
    inclusion_probability,
    require_inclusion,
    share_table,
)
from fpl_edge.sim.field import DEFAULT_FIELD_SIZE, FieldConfig, FieldModel, FieldSquads
from fpl_edge.sim.squad import PlayerUniverse
from fpl_edge.store import Snapshot


@dataclass(frozen=True, slots=True)
class HybridConfig:
    """Knobs, each of which is a modelling claim.

    ``prior_strength``
        The K at which observation and prior split the field evenly. It prices
        two defects against each other: the bootstrap's atomicity (small K
        repeats squads) versus the marginal sampler's broken correlations. 400
        makes today's 600-squad crawl worth ~60% of the field and a 1,000-entry
        top-1k sample worth ~71%.
    ``min_observed``
        Below this K a "sample" is a rumour; it is ignored and the sampler
        degrades to marginals with the reason recorded.
    """

    prior_strength: float = 400.0
    min_observed: int = 30
    max_swaps: int = 2
    price_tolerance_tenths: int = 3
    field_size: int = DEFAULT_FIELD_SIZE
    seed: int = 20_260_821


class HybridFieldSampler:
    """``sample_squads(n, cohort=...)`` -> arrays the sim scores directly.

    The returned :class:`~fpl_edge.models.field.contracts.FieldSample` wraps a
    :class:`~fpl_edge.sim.field.FieldSquads`, so scoring is exactly the
    existing path::

        sample = sampler.sample_squads(10_000, cohort="top1k", gw=2, ...)
        scores = FieldModel(universe).score(sample.squads, points, minutes)

    ``snapshot`` may be None for warehouse-free use (tests, synthetic worlds);
    then only the marginal path exists and every sample is labelled a prior.
    """

    def __init__(
        self,
        universe: PlayerUniverse,
        *,
        snapshot: Snapshot | None = None,
        season: str | None = None,
        config: HybridConfig | None = None,
    ) -> None:
        if snapshot is not None and season is None:
            raise ValueError("a snapshot without a season cannot locate picks")
        self.universe = universe
        self.snapshot = snapshot
        self.season = season
        self.config = config or HybridConfig()

    # -- public contract ----------------------------------------------------

    def sample_squads(
        self,
        n: int,
        cohort: str = "top1k",
        *,
        as_of: dt.datetime | None = None,
        gw: int | None = None,
        ownership: InclusionProbability | np.ndarray | None = None,
        captaincy: np.ndarray | None = None,
        expected_points: np.ndarray | None = None,
        seed: int | None = None,
    ) -> FieldSample:
        """Draw ``n`` rival squads for the given cohort, as of an instant.

        ``as_of`` is the decision instant -- normally the deadline being solved
        for. Everything the sample rests on is read through a Snapshot at that
        instant, so a backtest at GW9 cannot see GW9's picks (they are private
        until the deadline passes) or GW10's transfer flow. Passing an ``as_of``
        later than the sampler's own Snapshot is refused rather than silently
        widened: a Snapshot is a promise about what was knowable, and quietly
        extending it is how leakage gets into a backtest.

        ``gw`` defaults to the gameweek that ``as_of`` is deciding -- the first
        one whose deadline has not yet passed. The observed squads will be from
        the gameweek *before* it; the gap is what :mod:`.drift` covers.

        ``ownership`` is the **squad-inclusion share** and is accepted either as
        an :class:`~fpl_edge.models.field.share.InclusionProbability` (preferred:
        an EO array is then rejected by type) or as a bare array for callers
        that predate the wrapper. It supplies the transfer-flow denominators and
        the marginal fallback. ``captaincy`` is the share of the cohort
        captaining each player. When the cohort has measured picks, the marginal
        *component* uses the measured cohort marginals instead, because 600
        elite squads say more about the elite template than an overall forecast
        does.
        """
        rng = np.random.default_rng(self.config.seed if seed is None else seed)
        notes: list[str] = []
        if isinstance(ownership, InclusionProbability):
            ownership = require_inclusion(ownership)
        elif ownership is not None:
            ownership = np.asarray(ownership, dtype=float)
            if ownership.size and ownership.max() > 1.0:
                raise ValueError(
                    f"ownership has a value of {ownership.max():.4f}, above 1. "
                    "A squad-inclusion share is a probability; a value above 1 "
                    "is effective ownership, which counts a captain twice. Wrap "
                    "the right quantity in InclusionProbability."
                )

        snapshot, as_of = self._resolve_snapshot(as_of, notes)
        gw = self._resolve_gw(gw, snapshot, as_of)

        observed = None
        if cohort in ("top1k", "elite") and snapshot is not None:
            observed, reason = load_observed_squads(
                snapshot, self.season, self.universe, cohort
            )
            notes.append(reason)
            if observed is not None and observed.n < self.config.min_observed:
                notes.append(
                    f"only {observed.n} observed squads (< min_observed="
                    f"{self.config.min_observed}); treating as unobserved"
                )
                observed = None

        if observed is None:
            return self._marginal_prior(
                n, cohort, gw, ownership, captaincy, expected_points, rng, notes
            )

        # -- empirical component ---------------------------------------------
        k = observed.n
        w_emp = k / (k + self.config.prior_strength)
        n_emp = int(round(n * w_emp))
        n_marg = n - n_emp
        emp_squads, picked = observed.bootstrap(n_emp, rng, self.universe)
        chips = np.empty(n, dtype=object)
        chips[:n_emp] = observed.chips[picked]
        chips[n_emp:] = None

        drifted = False
        if snapshot is not None and gw > observed.gw:
            velocity = measure_flow_velocity(snapshot, self.season, self.universe)
            if velocity is None:
                notes.append(
                    f"observed squads are from GW{observed.gw} but no transfer flow "
                    "is measurable; sample carried forward UNDRIFTED"
                )
            else:
                horizon = max(
                    (snapshot.as_of - observed.as_of).total_seconds() / 86400.0, 0.0
                )
                denom = ownership if ownership is not None else observed.ownership(
                    self.universe.n_players
                )
                if ownership is None:
                    notes.append(
                        "flow denominators use measured cohort ownership (no overall "
                        "ownership supplied); per-owner sell rates for cohort "
                        "differentials will be overstated"
                    )
                rates = drift_rates(velocity, denom, self.config.field_size, horizon)
                emp_squads, receipt = apply_transfer_drift(
                    emp_squads, self.universe, rates, rng,
                    expected_points=expected_points,
                    max_swaps=self.config.max_swaps,
                    price_tolerance_tenths=self.config.price_tolerance_tenths,
                )
                drifted = True
                notes.append(
                    f"drifted GW{observed.gw}->GW{gw} over {horizon:.2f}d: {receipt}"
                )

        # -- marginal component, at the measured cohort marginals -------------
        if n_marg > 0:
            own_m = observed.ownership(self.universe.n_players)
            cap_m = observed.captain_share(self.universe.n_players)
            xp = expected_points
            if xp is None:
                xp = self.universe.price_tenths.astype(float)
                notes.append(
                    "expected_points not supplied; marginal component ordered by "
                    "price as a proxy"
                )
            marg = self._marginal_model(n_marg, rng).sample_squads(own_m, cap_m, xp)
            squads = _concat(emp_squads, marg)
        else:
            squads = emp_squads

        if n_marg > 0:
            provenance = PROVENANCE_HYBRID_DRIFTED if drifted else PROVENANCE_HYBRID
        else:
            provenance = PROVENANCE_EMPIRICAL_DRIFTED if drifted else PROVENANCE_EMPIRICAL
        return FieldSample(
            squads=squads, cohort=cohort, gw=int(gw), provenance=provenance,
            n_observed=k, observed_gw=observed.gw,
            empirical_weight=n_emp / n if n else 0.0,
            drift_applied=drifted, chips=chips, notes=tuple(notes),
            chip_rates=observed.chip_rates(),
        )

    # -- derived quantities, deliberately separate ----------------------------
    #
    # inclusion_probability() and effective_ownership() return DIFFERENT TYPES
    # holding DIFFERENTLY NAMED arrays. See share.py: this package exists
    # because those two numbers were once the same anonymous float array and
    # one was passed where the other belonged.

    def inclusion_probability(self, sample: FieldSample) -> InclusionProbability:
        """P(a cohort member holds p), measured off the sampled squads."""
        return inclusion_probability(sample, self.universe.n_players)

    def effective_ownership(self, sample: FieldSample) -> EffectiveOwnership:
        """Mean multiplier the cohort applies to p. Sums to ~12, not 15."""
        return effective_ownership(sample, self.universe.n_players)

    def share_table(self, sample: FieldSample) -> pd.DataFrame:
        """``(code, gw, own_share, captain_share)`` for the rank-aware solver.

        ``own_share`` is inclusion, not EO -- see
        :func:`fpl_edge.models.field.share.share_table`.
        """
        return share_table(sample, self.universe)

    def cohort_rates(self, cohort: str, *, gw: int | None = None) -> CohortRates:
        """Measured captaincy shares and chip-play rates for a cohort.

        Returns an ``unmeasured``-provenance object rather than raising when
        the cohort has not been crawled; every array is then None, because a
        zero would be a claim.
        """
        if self.snapshot is None:
            return CohortRates(cohort=cohort, gw=gw, n_managers=0,
                               provenance="unmeasured (no snapshot)")
        return measure_cohort(
            self.snapshot, self.season, self.universe, cohort, gw=gw
        )

    # -- timing ----------------------------------------------------------------

    def _resolve_snapshot(
        self, as_of: dt.datetime | None, notes: list[str]
    ) -> tuple[Snapshot | None, dt.datetime | None]:
        """The Snapshot to read at, honouring ``as_of`` without widening it."""
        if self.snapshot is None:
            return None, as_of
        if as_of is None:
            return self.snapshot, self.snapshot.as_of
        if as_of > self.snapshot.as_of:
            raise ValueError(
                f"as_of={as_of.isoformat()} is later than this sampler's Snapshot "
                f"({self.snapshot.as_of.isoformat()}). Reading forward would show "
                "picks and transfer flow that were not public at the requested "
                "instant. Build the sampler from a Snapshot at or after as_of."
            )
        if as_of == self.snapshot.as_of:
            return self.snapshot, as_of
        wh = self.snapshot.escape_hatch_unfiltered(
            "narrowing to a strictly earlier as_of for a field sample; an earlier "
            "Snapshot is a subset of the current one, so nothing escapes "
            "point-in-time filtering"
        )
        notes.append(f"reading at as_of={as_of.isoformat()} (narrowed from the sampler's Snapshot)")
        return wh.snapshot_at(as_of), as_of

    def _resolve_gw(
        self, gw: int | None, snapshot: Snapshot | None, as_of: dt.datetime | None
    ) -> int:
        """The gameweek being decided at ``as_of``: the next one to lock."""
        if gw is not None:
            return int(gw)
        if snapshot is None:
            raise ValueError(
                "sample_squads needs a gameweek: pass gw=, or construct the "
                "sampler with a Snapshot so it can be derived from as_of."
            )
        events = snapshot.table("dim_event", where="season = ?", params=[self.season])
        if events.empty:
            raise ValueError(f"no dim_event rows for {self.season}; cannot derive gw")
        at = as_of or snapshot.as_of
        deadlines = pd.to_datetime(events["deadline_utc"], utc=True)
        upcoming = events[deadlines > at]
        if upcoming.empty:
            raise ValueError(
                f"every {self.season} deadline has passed at {at.isoformat()}; "
                "there is no gameweek to decide"
            )
        return int(upcoming["gw"].min())

    # -- fallback -------------------------------------------------------------

    def _marginal_prior(
        self, n, cohort, gw, ownership, captaincy, expected_points, rng, notes
    ) -> FieldSample:
        """Ownership-marginals fallback. Explicitly a prior; never silent.

        Pre-GW1 the picks tables are legitimately empty and this is the only
        construction that exists. It requires the caller's ownership forecast
        -- there is nothing to measure one from -- and refuses to invent one.
        """
        missing = [name for name, v in (
            ("ownership", ownership), ("captaincy", captaincy),
            ("expected_points", expected_points),
        ) if v is None]
        if missing:
            raise ValueError(
                f"no observed {cohort!r} squads are available and the marginal "
                f"fallback is missing {missing}. Supply the ownership forecast "
                f"(e.g. OwnershipForecaster output aligned to the universe); the "
                f"sampler will not fabricate one."
            )
        marg = self._marginal_model(n, rng).sample_squads(
            np.asarray(ownership, dtype=float),
            np.asarray(captaincy, dtype=float),
            np.asarray(expected_points, dtype=float),
        )
        notes.append(
            f"cohort {cohort!r} sampled from ownership marginals only; treat every "
            "cohort-specific conclusion as a prior"
        )
        return FieldSample(
            squads=marg, cohort=cohort, gw=int(gw),
            provenance=PROVENANCE_MARGINALS_PRIOR,
            n_observed=0, observed_gw=None, empirical_weight=0.0,
            drift_applied=False, chips=None, notes=tuple(notes), chip_rates={},
        )

    def _marginal_model(self, n: int, rng: np.random.Generator) -> FieldModel:
        # A fresh derived seed per call keeps the marginal component
        # independent of the bootstrap draws while staying deterministic.
        seed = int(rng.integers(0, 2**31 - 1))
        return FieldModel(self.universe, FieldConfig(n_rivals=n, seed=seed))


def _concat(a: FieldSquads, b: FieldSquads) -> FieldSquads:
    return FieldSquads(
        slots=np.vstack([a.slots, b.slots]),
        slot_pos=np.vstack([a.slot_pos, b.slot_pos]),
        captain_slot=np.concatenate([a.captain_slot, b.captain_slot]),
        vice_slot=np.concatenate([a.vice_slot, b.vice_slot]),
    )
