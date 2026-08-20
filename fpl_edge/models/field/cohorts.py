"""Cohort behaviour measured from crawled picks: captaincy, chips, ownership.

Everything here is a *measurement with a sample size attached*, or it is
explicitly labelled unmeasured. The distinction is the whole point of the
module: "the top-1k triple-captain rate is 2.4% +/- 0.5" and "we assume elite
managers wildcard around GW9" are different kinds of statement, and the second
must never be emitted wearing the first one's clothes.

Chip-play rates need denominators handled with care. The numerator for GW g is
managers whose ``fact_manager_chip`` row exists for g; the denominator is
managers whose *picks* were crawled for g, not the whole pool -- a manager
whose squad we never fetched can contribute to neither count.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field as dc_field
from typing import Mapping

import numpy as np

from fpl_edge.models.field.observed import ObservedSquads, load_observed_squads
from fpl_edge.sim.squad import PlayerUniverse
from fpl_edge.store import Snapshot

#: The four chips, as the API spells them.
CHIPS = ("wildcard", "freehit", "bboost", "3xc")


@dataclass(frozen=True)
class CohortRates:
    """One cohort's measured behaviour for one gameweek.

    ``provenance`` is ``measured:{cohort}:gw{g}:n{K}`` when built from real
    squads and ``unmeasured`` otherwise; in the unmeasured case every array is
    None and every rate dict is empty, because zeros would be a claim.
    """

    cohort: str
    gw: int | None
    n_managers: int
    provenance: str
    as_of: dt.datetime | None = None
    ownership: np.ndarray | None = None        # (P,) share owning
    start_share: np.ndarray | None = None      # (P,) share starting
    captain_share: np.ndarray | None = None    # (P,) share captaining
    chip_rates: Mapping[str, float] = dc_field(default_factory=dict)

    @property
    def measured(self) -> bool:
        return self.n_managers > 0

    def standard_error(self, share: float) -> float:
        if not self.measured:
            raise ValueError("an unmeasured cohort has no sampling error to quote")
        p = min(max(float(share), 0.0), 1.0)
        return float(np.sqrt(p * (1.0 - p) / self.n_managers))

    def eo(self, triple_captain_share: np.ndarray | None = None) -> np.ndarray:
        """Effective ownership from the measured shares.

        ``EO = start + captain + triple_captain`` (see
        :mod:`fpl_edge.models.ownership.eo`). The triple-captain vector is only
        knowable per-player from picks with ``multiplier == 3``; when not
        supplied, the ``3xc`` chip rate spread over the captain distribution is
        the best available stand-in and is what is used.
        """
        if not self.measured:
            raise ValueError("cannot compute EO for an unmeasured cohort")
        tc = triple_captain_share
        if tc is None:
            tc = self.captain_share * float(self.chip_rates.get("3xc", 0.0))
        return self.start_share + self.captain_share + tc


def rates_from_observed(observed: ObservedSquads, universe: PlayerUniverse) -> CohortRates:
    """Measured rates from an already-loaded sample of observed squads."""
    n_players = universe.n_players
    return CohortRates(
        cohort=observed.cohort,
        gw=observed.gw,
        n_managers=observed.n,
        provenance=f"measured:{observed.cohort}:gw{observed.gw}:n{observed.n}",
        as_of=observed.as_of,
        ownership=observed.ownership(n_players),
        start_share=observed.start_share(n_players),
        captain_share=observed.captain_share(n_players),
        chip_rates=observed.chip_rates(),
    )


def measure_cohort(
    snapshot: Snapshot,
    season: str,
    universe: PlayerUniverse,
    cohort: str,
    *,
    gw: int | None = None,
) -> CohortRates:
    """Cohort rates at a point in time, degrading honestly.

    Pre-GW1 (or before the cohort has been crawled) this returns an
    ``unmeasured`` CohortRates rather than raising, because "we do not know
    yet" is an expected state of the season, not an error -- but it returns it
    *labelled*, so no caller can mistake the absence for a zero.
    """
    observed, reason = load_observed_squads(snapshot, season, universe, cohort, gw=gw)
    if observed is None:
        return CohortRates(
            cohort=cohort, gw=gw, n_managers=0,
            provenance=f"unmeasured ({reason})",
        )
    return rates_from_observed(observed, universe)
