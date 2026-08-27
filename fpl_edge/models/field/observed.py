"""Observed rival squads, read point-in-time and resampled with their joint
structure intact.

The empirical construction: instead of *generating* squads from marginal
ownership, take the squads real managers actually locked and bootstrap them.
Every pairwise correlation, the budget, the 3-per-club rule, bench ordering,
captaincy conditional on ownership -- all of it comes for free, exactly,
because the sample *is* the population object being modelled. What it costs is
sample size (hundreds to low thousands of squads, so cohort shares carry a
binomial SE worth quoting) and timing (picks are public only after the
deadline, so a GW-k decision sees GW k-1 squads; see ``drift.py``).

Availability, stated plainly
----------------------------
``fact_manager_pick`` rows are stamped ``as_of = the gameweek deadline`` at
ingest, so a Snapshot taken before the first deadline of a season sees an empty
table. That is not a bug to code around: **before GW1 locks there are no
observed squads in the world**, and this module returns ``None`` with a stated
reason rather than something invented. The hybrid sampler degrades to
ownership marginals with an explicit prior label when that happens.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Importing the schema registers the rival tables' PIT keys, which is what lets
# Snapshot.table read them with the same leakage guarantees as the core tables.
import fpl_edge.ingest.rivals.schema  # noqa: F401  (import for side effect)
from fpl_edge.sim.field import FieldSquads
from fpl_edge.sim.squad import SQUAD_SIZE, XI_SIZE, PlayerUniverse
from fpl_edge.store import Snapshot

#: dim_manager.source prefix written by the top-1k standings sampler
#: (:mod:`fpl_edge.ingest.rivals.top1k`). Membership of the top1k cohort is
#: "has any dim_manager row with this prefix"; everything else in the crawl
#: pool is the elite cohort.
TOP1K_SOURCE_PREFIX = "top1k"

#: The cohorts, in precedence order. Mutually exclusive: :func:`resolve_cohorts`
#: assigns every tracked entry to exactly one of them.
#:
#: * ``top1k``        -- any ``dim_manager`` row at or before the snapshot whose
#:   ``source`` starts with :data:`TOP1K_SOURCE_PREFIX`.
#: * ``elite``        -- otherwise, any ``dim_manager`` row at or before the
#:   snapshot (the curated pool: expert, elite_list, winner, mini_league,
#:   snowball, elite_named).
#: * ``unclassified`` -- has picks but no ``dim_manager`` row at all. Surfaced
#:   rather than silently dropped, because a squad we hold and cannot classify
#:   is a crawl bug worth seeing, not a row to lose.
#:
#: **Why top1k outranks curation.** The top1k crawl samples a *defined
#: population* (the top N of the overall table at a stated season/gw) and its
#: shares are read as an estimate of what that population does; dropping an
#: entry from it because a curator also listed them removes exactly the
#: strongest managers and biases the estimate. The elite pool makes no sampling
#: claim -- it is a roster -- so losing an overlapping member costs it only
#: sample size, which is visible in ``n``. Before this rule an entry found by
#: both crawls inflated *both* denominators. How many entries that is on any
#: given day is a property of the crawl, not of this rule, so no count is
#: quoted here -- it moves every time the top1k sampler runs. The live number
#: is ``SELECT count(*) FROM sem_manager_cohort(now()) WHERE cohort = 'top1k'
#: AND n_sources > n_top1k_sources``.
#:
#: The same rule is implemented in SQL by ``sem_manager_cohort`` in
#: ``fpl_edge/store/views.sql``; ``tests/unit/test_field_eo_agreement.py``
#: pins the two equal.
COHORTS = ("top1k", "elite", "unclassified")


class CohortReadError(RuntimeError):
    """``dim_manager`` is unreadable, so cohort membership is unknown.

    Distinct from "nobody is tracked yet", which is an empty mapping. Raised
    rather than swallowed because a broken read and an empty world produce the
    same *shape* of answer and wildly different meanings -- see
    :func:`resolve_cohorts`.
    """


@dataclass(frozen=True)
class ObservedSquads:
    """Real squads as they locked, in the simulator's 15-slot layout.

    Slot order is the sim's contract: 11 starters, then the bench keeper, then
    the three outfield substitutes in autosub priority order. The FPL API's
    ``position`` field (1..11 starters, 12 bench GK, 13..15 outfield bench)
    maps onto it directly, which is verified rather than assumed at build time.
    """

    slots: np.ndarray            # (K, 15) universe indices
    captain_slot: np.ndarray     # (K,)
    vice_slot: np.ndarray        # (K,)
    entry_ids: np.ndarray        # (K,) who locked each squad
    chips: np.ndarray            # (K,) object: chip name or None
    season: str
    gw: int
    cohort: str
    as_of: dt.datetime
    #: (K, 15) the FPL multiplier each manager locked against each slot, as the
    #: API returned it: 0 benched, 1 started, 2 captain, 3 triple captain -- and
    #: 1 for a benched pick under Bench Boost. Stored rather than reconstructed
    #: from armband + chip because only the stored value gets Bench Boost right.
    multipliers: np.ndarray = None  # type: ignore[assignment]
    dropped: int = 0             # squads discarded for failing validation

    @property
    def n(self) -> int:
        return int(self.slots.shape[0])

    def ownership(self, n_players: int) -> np.ndarray:
        """Share of the cohort owning each player."""
        return np.bincount(self.slots.ravel(), minlength=n_players) / self.n

    def captain_share(self, n_players: int) -> np.ndarray:
        picks = self.slots[np.arange(self.n), self.captain_slot]
        return np.bincount(picks, minlength=n_players) / self.n

    def start_share(self, n_players: int) -> np.ndarray:
        return np.bincount(
            self.slots[:, :XI_SIZE].ravel(), minlength=n_players
        ) / self.n

    def eo(self, n_players: int) -> np.ndarray:
        """Effective ownership: the mean FPL multiplier the cohort applies.

        THE canonical definition for this repo, matching ``sem_elite_ownership``
        in ``fpl_edge/store/views.sql`` exactly::

            eo_p = sum over m of weight[m] * multiplier[m, p] / sum of weight

        Weights are not implemented yet, so every weight is 1 and the
        denominator is ``self.n``. Summing the *stored* multipliers is what
        makes this exact: it needs no chip-rate fallback, and it gets Bench
        Boost (benched picks scoring 1) and Triple Captain (3) right because
        the API already resolved them at the deadline.

        Kept separate from :meth:`ownership` on purpose: a benched player counts
        toward ownership and contributes nothing here.
        """
        if self.multipliers is None:
            raise ValueError(
                "this ObservedSquads carries no stored multipliers, so its "
                "effective ownership is unknown; reload it with "
                "load_observed_squads rather than inferring one"
            )
        return np.bincount(
            self.slots.ravel(),
            weights=self.multipliers.ravel().astype(float),
            minlength=n_players,
        ) / self.n

    def chip_rates(self) -> dict[str, float]:
        """Share of the cohort playing each chip in this gameweek."""
        played = [c for c in self.chips if c]
        return {
            chip: played.count(chip) / self.n for chip in sorted(set(played))
        }

    def pairwise_coownership(self, players: np.ndarray) -> np.ndarray:
        """P(own i AND own j) for the given universe indices, from the sample.

        This is the moment the marginal sampler cannot reproduce and the
        copula construction would be fitted to; it is exposed so the gap can
        be *measured* against any generated field rather than argued about.
        """
        idx = np.asarray(players, dtype=np.int64)
        owned = np.zeros((self.n, len(idx)), dtype=bool)
        for j, p in enumerate(idx):
            owned[:, j] = (self.slots == p).any(axis=1)
        return owned.astype(float).T @ owned.astype(float) / self.n

    def bootstrap(
        self, n: int, rng: np.random.Generator, universe: PlayerUniverse
    ) -> tuple[FieldSquads, np.ndarray]:
        """Resample ``n`` squads with replacement, joint structure intact.

        Each draw is a whole squad -- slots, armband, bench order together --
        so nothing about the within-squad dependence is disturbed. The cost is
        atomicity: from K observed squads, an ``n``-rival field contains each
        observed squad ``n/K`` times in expectation, which understates the
        diversity of the real cohort. The hybrid sampler pays that down by
        mixing in marginal draws; this method does one thing.

        Returns the sampled squads and the (n,) indices into the observed
        sample, so chips and entry ids can be carried alongside.
        """
        if self.n == 0:
            raise ValueError("cannot bootstrap an empty sample")
        pick = rng.integers(0, self.n, size=n)
        slots = self.slots[pick]
        return FieldSquads(
            slots=slots,
            slot_pos=universe.position[slots],
            captain_slot=self.captain_slot[pick],
            vice_slot=self.vice_slot[pick],
        ), pick


def last_locked_gw(snapshot: Snapshot, season: str) -> int | None:
    """The most recent gameweek whose deadline has passed at ``snapshot.as_of``.

    ``None`` before the first deadline of the season -- the case where every
    picks table is legitimately empty.
    """
    events = snapshot.table("dim_event", where="season = ?", params=[season])
    if events.empty:
        return None
    past = events[pd.to_datetime(events["deadline_utc"], utc=True) <= snapshot.as_of]
    return None if past.empty else int(past["gw"].max())


def resolve_cohorts(snapshot: Snapshot) -> dict[int, str]:
    """Every tracked entry -> exactly one cohort, by the rule in :data:`COHORTS`.

    The single Python definition of cohort membership; the SQL twin is
    ``sem_manager_cohort`` in ``store/views.sql``. Entries absent from
    ``dim_manager`` are absent here too -- callers label them
    ``'unclassified'`` when they turn up holding picks.

    Note the deliberate use of *any* row at or before the snapshot rather than
    the newest one: a manager the standings sampler saw once is a top1k
    manager forever, so the classification cannot flip-flop as ``as_of`` moves.

    An empty ``dim_manager`` returns ``{}`` -- a world with no tracked entries
    is a real state of the season, and every squad in it is honestly
    ``'unclassified'``. An *unreadable* ``dim_manager`` raises. The two used to
    be the same answer, and the empty dict was the more dangerous one of the
    pair: :func:`load_observed_squads` reported the failure to the user as "no
    crawled 'elite' squads for <season> GW<g>" -- a broken read dressed up as
    an empty world, repeated verbatim by the ownership panel's ``cohort_note``
    -- and for ``cohort='unclassified'`` the ``~isin({})`` mask is all-True, so
    every crawled squad in the warehouse was reported as a crawl bug. The SQL
    twin (``sem_manager_cohort``) has no such fallback and would have raised,
    so the swallow was also drift the agreement test cannot see.
    """
    try:
        top1k = snapshot.table(
            "dim_manager", where="source LIKE ?", params=[f"{TOP1K_SOURCE_PREFIX}%"]
        )
        everyone = snapshot.table("dim_manager")
    except Exception as exc:
        raise CohortReadError(
            "dim_manager could not be read at "
            f"{snapshot.as_of.isoformat()} ({type(exc).__name__}: {exc}), so no "
            "entry can be classified; refusing to report an unreadable crawl as "
            "an empty one"
        ) from exc
    top_ids = set() if top1k.empty else {int(e) for e in top1k["entry_id"]}
    all_ids = set() if everyone.empty else {int(e) for e in everyone["entry_id"]}
    # top1k first, so an entry sampled by both crawls lands in one cohort only.
    return {e: ("top1k" if e in top_ids else "elite") for e in all_ids}


def load_observed_squads(
    snapshot: Snapshot,
    season: str,
    universe: PlayerUniverse,
    cohort: str,
    *,
    gw: int | None = None,
) -> tuple[ObservedSquads | None, str]:
    """Observed squads for a cohort at the given instant, or (None, why not).

    ``gw`` defaults to the last locked gameweek. The second element of the
    return is a human-readable reason whenever the first is ``None``, because
    "empty" has at least four distinct causes here (pre-GW1, tables absent,
    cohort not yet sampled, all rows failed validation) and the hybrid model's
    provenance notes must distinguish them.
    """
    if gw is None:
        gw = last_locked_gw(snapshot, season)
        if gw is None:
            return None, (
                f"no gameweek deadline has passed at {snapshot.as_of.isoformat()}; "
                "picks are private until a deadline locks, so there are no observed "
                "squads in existence yet"
            )
    try:
        picks = snapshot.table(
            "fact_manager_pick", where="season = ? AND gw = ?", params=[season, gw]
        )
    except Exception as exc:
        return None, f"rival pick tables not present in this warehouse ({type(exc).__name__})"
    if picks.empty:
        return None, (
            f"fact_manager_pick has no rows for {season} GW{gw} at "
            f"{snapshot.as_of.isoformat()}; the crawl has not run since that deadline"
        )

    # Cohort membership: exactly one cohort per entry (see COHORTS). A cohort
    # name outside COHORTS -- 'overall' -- deliberately applies no filter.
    if cohort in COHORTS:
        assignment = resolve_cohorts(snapshot)
        if cohort == "unclassified":
            keep = ~picks["entry_id"].astype(int).isin(assignment)
        else:
            keep = picks["entry_id"].astype(int).map(assignment) == cohort
        picks = picks[keep]
        if picks.empty:
            return None, f"no crawled {cohort!r} squads for {season} GW{gw}"

    # element_id is a per-season id; the universe speaks stable codes.
    players = snapshot.table("dim_player", where="season = ?", params=[season])
    known = set(int(c) for c in universe.codes)
    el_to_idx = {
        int(e): universe.index_of(int(c))
        for e, c in zip(players["element_id"], players["code"])
        if pd.notna(e) and int(c) in known
    }

    try:
        chips = snapshot.table(
            "fact_manager_chip", where="season = ? AND gw = ?", params=[season, gw]
        )
    except Exception:
        chips = pd.DataFrame(columns=["entry_id", "chip"])
    chip_of = (
        {} if chips.empty else
        dict(zip(chips["entry_id"].astype(int), chips["chip"]))
    )

    rows, caps, vices, eids, chp, mults = [], [], [], [], [], []
    dropped = 0
    for eid, g in picks.groupby("entry_id", sort=True):
        squad = _one_squad(g, el_to_idx, universe)
        if squad is None:
            dropped += 1
            continue
        slots, cap, vice, mult = squad
        rows.append(slots)
        caps.append(cap)
        vices.append(vice)
        mults.append(mult)
        eids.append(int(eid))
        chp.append(chip_of.get(int(eid)))
    if not rows:
        return None, (
            f"all {picks['entry_id'].nunique()} crawled squads for {season} GW{gw} "
            "failed validation (element ids missing from the universe?)"
        )

    return ObservedSquads(
        slots=np.array(rows, dtype=np.int64),
        captain_slot=np.array(caps, dtype=np.int64),
        vice_slot=np.array(vices, dtype=np.int64),
        entry_ids=np.array(eids, dtype=np.int64),
        chips=np.array(chp, dtype=object),
        season=season, gw=int(gw), cohort=cohort, as_of=snapshot.as_of,
        multipliers=np.array(mults, dtype=np.int64),
        dropped=dropped,
    ), f"{len(rows)} observed {cohort} squads for {season} GW{gw} ({dropped} dropped)"


def _one_squad(
    g: pd.DataFrame, el_to_idx: dict[int, int], universe: PlayerUniverse
) -> tuple[np.ndarray, int, int, np.ndarray] | None:
    """One manager's picks frame into (slots, captain_slot, vice_slot, mults).

    Returns None for anything malformed rather than repairing it: a squad
    invented by a repair step is exactly the fabricated data this package
    promises not to produce. Free Hit and Wildcard squads are ordinary 15-pick
    squads at the deadline and pass through unchanged.
    """
    if len(g) != SQUAD_SIZE:
        return None
    g = g.sort_values("slot")
    if list(g["slot"]) != list(range(1, SQUAD_SIZE + 1)):
        return None
    try:
        slots = np.array([el_to_idx[int(e)] for e in g["element_id"]], dtype=np.int64)
    except KeyError:
        return None
    # The API's slot 12 is the bench keeper; the sim requires it. Verify.
    if universe.position[slots[XI_SIZE]] != 1:
        return None
    cap = np.flatnonzero(g["is_captain"].to_numpy(dtype=bool))
    vice = np.flatnonzero(g["is_vice_captain"].to_numpy(dtype=bool))
    if len(cap) != 1 or len(vice) != 1:
        return None
    # The multiplier the API resolved at the deadline, kept verbatim. A missing
    # one is a hole in the crawl, not a zero: refuse the squad rather than
    # quietly pretending the manager benched everyone.
    mult = pd.to_numeric(g["multiplier"], errors="coerce")
    if mult.isna().any():
        return None
    return slots, int(cap[0]), int(vice[0]), mult.to_numpy(dtype=np.int64)
