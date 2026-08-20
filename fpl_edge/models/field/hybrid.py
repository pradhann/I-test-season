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

from dataclasses import dataclass

import numpy as np

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
        gw: int,
        ownership: np.ndarray | None = None,
        captaincy: np.ndarray | None = None,
        expected_points: np.ndarray | None = None,
        seed: int | None = None,
    ) -> FieldSample:
        """Draw ``n`` rival squads for the given cohort and gameweek.

        ``ownership`` / ``captaincy`` / ``expected_points`` are
        ``(n_players,)`` arrays aligned to the universe. ``ownership`` is the
        **overall squad-inclusion share** (not EO -- see
        ``engine._align_ownership`` for that scar) and is used for the
        transfer-flow denominators and as the marginal fallback; when the
        cohort has measured picks, the marginal *component* uses the measured
        cohort marginals instead, because 600 elite squads say more about the
        elite template than an overall forecast does.
        """
        rng = np.random.default_rng(self.config.seed if seed is None else seed)
        notes: list[str] = []

        observed = None
        if cohort in ("top1k", "elite") and self.snapshot is not None:
            observed, reason = load_observed_squads(
                self.snapshot, self.season, self.universe, cohort
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
        if self.snapshot is not None and gw > observed.gw:
            velocity = measure_flow_velocity(self.snapshot, self.season, self.universe)
            if velocity is None:
                notes.append(
                    f"observed squads are from GW{observed.gw} but no transfer flow "
                    "is measurable; sample carried forward UNDRIFTED"
                )
            else:
                horizon = max(
                    (self.snapshot.as_of - observed.as_of).total_seconds() / 86400.0, 0.0
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
