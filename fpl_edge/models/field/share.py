"""Two quantities that are both called "ownership", and never the same number.

This module exists because of one bug. Effective ownership -- the mean FPL
multiplier the field applies to a player -- was handed to a sampler whose
argument is a *squad-inclusion probability*. EO counts a captain twice, drops
benched owners, and sums to about 12 across the player set; inclusion is a
probability in [0, 1] and sums to exactly 15. At the 2026-27 GW1 forecast
Haaland's EO is 1.139 against an inclusion probability of 0.730 (see
``fpl_edge.sim.engine._align_ownership``). The sampler clipped 1.139 to 0.999
and produced a field that owned Haaland almost universally, so every Haaland
differential was priced against a field that does not exist -- the single most
consequential decision of the gameweek, silently inverted.

Both numbers are floating-point arrays of length ``n_players``. Nothing about
their *runtime representation* distinguishes them, which is why the confusion
survived review. So this module refuses to represent them the same way:

* :class:`InclusionProbability` carries its array as ``p_in_squad``;
* :class:`EffectiveOwnership` carries its array as ``multiplier``.

There is deliberately no shared attribute name, no ``.values``, and no
``__array__``: substituting one type for the other raises ``AttributeError`` at
the first use rather than clipping silently at 1.0. The constructors also check
the two invariants that actually separate the quantities -- range and total --
so an array that walked in through a dict or a Parquet round-trip is caught at
the boundary:

======================  =====================  ==================
                        InclusionProbability   EffectiveOwnership
======================  =====================  ==================
per-player range        [0, 1]                 [0, 3]
sums (whole universe)   15 (squad size)        ~12 (XI + armband)
benched owners count?   yes                    no
captain counted twice?  no                     yes
======================  =====================  ==================

Which one a consumer wants is not a matter of taste:

* **squad sampling** wants inclusion -- you cannot draw 15 players from a
  vector that is not a probability (``FieldModel.sample_squads``);
* **the rank objective** wants both, separately: the ``(1 - 2 share)``
  variance flip in :mod:`fpl_edge.rank.coefficients` uses *inclusion* for squad
  membership and *captaincy share* for the armband, while the pace increment
  -- the field's expected score, the thing my score is measured against -- is
  the EO-weighted sum of player points and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.models.ownership.eo import effective_ownership as _eo_algebra
from fpl_edge.sim.squad import SQUAD_SIZE, XI_SIZE, PlayerUniverse

#: A whole-universe inclusion vector sums to the squad size. Allowed slack
#: before construction fails; 1.5 is wide enough for a forecast that has not
#: been renormalised and narrow enough to reject an EO vector (which totals
#: about 12).
INCLUSION_SUM_TOLERANCE = 1.5

#: Ceiling on a per-player multiplier: started (1) + captained (1) + triple
#: captain (1). Anything above this is not effective ownership.
MAX_MULTIPLIER = 3.0

#: A full field starts eleven and captains one, so EO totals ~12; chips move it
#: (bench boost adds up to 4, triple captain adds 1 per player who plays it).
EO_SUM_RANGE = (9.0, 17.0)


class ShareTypeError(TypeError):
    """Raised when an ownership-like array is used as the wrong quantity."""


def _clean(x, name: str) -> np.ndarray:
    arr = np.asarray(x, dtype=np.float64)
    if arr.ndim != 1:
        raise ValueError(f"{name} must be a 1-D per-player array, got shape {arr.shape}")
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} contains non-finite values")
    return arr


@dataclass(frozen=True)
class InclusionProbability:
    """P(a cohort member's fifteen contains player p). **Not** EO.

    The array lives on ``p_in_squad``. It is the only thing
    :meth:`fpl_edge.sim.field.FieldModel.sample_squads` can consume, and
    :meth:`for_sampler` is the explicit unwrap -- explicit because handing a
    bare array across that boundary is exactly how the original bug travelled.
    """

    p_in_squad: np.ndarray
    cohort: str
    provenance: str
    #: Distinct observed squads behind the estimate. 0 for a forecast/prior,
    #: which is what makes :meth:`standard_error` a category error there.
    n_observed: int = 0
    #: Set False for a per-position or per-subset slice, where the total is not
    #: the squad size and checking it would be wrong rather than lax.
    whole_universe: bool = True

    def __post_init__(self) -> None:
        arr = _clean(self.p_in_squad, "p_in_squad")
        object.__setattr__(self, "p_in_squad", arr)
        if arr.size and (arr.min() < -1e-9 or arr.max() > 1.0 + 1e-9):
            hi = float(arr.max())
            raise ValueError(
                f"inclusion probabilities must lie in [0, 1]; got max {hi:.4f}. "
                "A value above 1 is the signature of effective ownership being "
                "passed as an inclusion probability -- EO counts a captain "
                "twice and is not a probability. Use EffectiveOwnership, or "
                "pass the squad-inclusion share (own_mean / selected_by_pct)."
            )
        if self.whole_universe:
            total = float(arr.sum())
            if abs(total - SQUAD_SIZE) > INCLUSION_SUM_TOLERANCE:
                raise ValueError(
                    f"whole-universe inclusion probabilities must sum to about "
                    f"{SQUAD_SIZE} (every manager holds exactly fifteen); got "
                    f"{total:.3f}. A total near 12 means this is effective "
                    f"ownership (eleven starters plus one armband). Pass "
                    f"whole_universe=False only for a genuine subset."
                )

    def __array__(self, *args, **kwargs):  # pragma: no cover - guard
        raise ShareTypeError(
            "refusing to coerce InclusionProbability to an array implicitly. "
            "Call .for_sampler() (squad sampling) or .p_in_squad -- the point "
            "of this type is that the unwrap is visible in review."
        )

    def for_sampler(self) -> np.ndarray:
        """The array ``FieldModel.sample_squads(ownership=...)`` wants."""
        return self.p_in_squad

    @property
    def n_players(self) -> int:
        return int(self.p_in_squad.size)

    def standard_error(self) -> np.ndarray:
        """Per-player binomial SE, from observed squads only."""
        if self.n_observed <= 0:
            raise ValueError(
                f"{self.provenance!r} rests on no observed squads, so it has no "
                "sampling error -- it is a forecast, and its error is model "
                "error, which this class cannot know."
            )
        p = np.clip(self.p_in_squad, 0.0, 1.0)
        return np.sqrt(p * (1.0 - p) / self.n_observed)


@dataclass(frozen=True)
class EffectiveOwnership:
    """Mean FPL multiplier the cohort applies to player p. **Not** a probability.

    The array lives on ``multiplier``. ``EO = start_share + captain_share +
    triple_captain_share`` (:mod:`fpl_edge.models.ownership.eo`), so it ranges
    over [0, 3] and is the weight on player points in the field's expected
    score -- the pace increment the rank objective measures my score against.
    """

    multiplier: np.ndarray
    cohort: str
    provenance: str
    #: The components, kept so a caller never has to re-derive them (and so
    #: ``multiplier >= start_share`` is checkable rather than assumed).
    start_share: np.ndarray | None = None
    captain_share: np.ndarray | None = None
    triple_captain_share: np.ndarray | None = None
    n_observed: int = 0
    whole_universe: bool = True

    def __post_init__(self) -> None:
        arr = _clean(self.multiplier, "multiplier")
        object.__setattr__(self, "multiplier", arr)
        if arr.size and (arr.min() < -1e-9 or arr.max() > MAX_MULTIPLIER + 1e-9):
            raise ValueError(
                f"effective ownership is a mean multiplier in [0, {MAX_MULTIPLIER}]; "
                f"got range [{arr.min():.3f}, {arr.max():.3f}]"
            )
        if self.whole_universe and arr.size:
            total = float(arr.sum())
            lo, hi = EO_SUM_RANGE
            if not lo <= total <= hi:
                raise ValueError(
                    f"whole-universe effective ownership should total about 12 "
                    f"(eleven starters plus one armband, moved by chips); got "
                    f"{total:.3f}. A total near 15 means this is squad-inclusion "
                    f"ownership -- use InclusionProbability."
                )
        if self.start_share is not None:
            ss = _clean(self.start_share, "start_share")
            object.__setattr__(self, "start_share", ss)
            if (arr + 1e-9 < ss).any():
                raise ValueError(
                    "EO must be at least start_share: captaincy is additive. "
                    "EO = ownership - captaincy is the classic sign error."
                )

    def __array__(self, *args, **kwargs):  # pragma: no cover - guard
        raise ShareTypeError(
            "refusing to coerce EffectiveOwnership to an array implicitly. "
            "It is NOT a squad-inclusion probability: it counts a captain twice, "
            "drops benched owners, and can exceed 1. If a sampler is asking for "
            "this, it wants InclusionProbability instead. Use .as_weights() when "
            "you really do want the pace weights."
        )

    def as_weights(self) -> np.ndarray:
        """The weights in ``pace_increment = sum_p EO_p * points_p``."""
        return self.multiplier

    @property
    def n_players(self) -> int:
        return int(self.multiplier.size)

    def pace_increment(self, points: np.ndarray) -> np.ndarray:
        """The field's expected score for a ``(n_players, ...)`` points draw.

        This is the quantity ``m = E[my score - pace increment]`` in
        ``docs/platform/rank_objectives.md`` §1 is defined against, and the only
        legitimate use of EO in the objective.
        """
        pts = np.asarray(points, dtype=np.float64)
        if pts.shape[0] != self.n_players:
            raise ValueError(
                f"points has {pts.shape[0]} rows, EO has {self.n_players} players"
            )
        return np.tensordot(self.multiplier, pts, axes=(0, 0))


# ---------------------------------------------------------------------------
# guards
# ---------------------------------------------------------------------------


def require_inclusion(x: object, *, argument: str = "ownership") -> np.ndarray:
    """Unwrap an :class:`InclusionProbability`, refusing anything else.

    Call this at any boundary that used to take a bare array. A raw ndarray is
    rejected on purpose: it is the shape the bug had, and "it looked like the
    right type" is precisely the failure being engineered out.
    """
    if isinstance(x, InclusionProbability):
        return x.for_sampler()
    if isinstance(x, EffectiveOwnership):
        raise ShareTypeError(
            f"{argument} was given effective ownership. EO counts a captain "
            "twice, drops benched owners and can exceed 1, so it cannot be a "
            "squad-inclusion probability. This is the Haaland bug: EO 1.139 "
            "clips to 0.999 and the sampled field owns him universally."
        )
    raise ShareTypeError(
        f"{argument} must be an InclusionProbability, not "
        f"{type(x).__name__}. Wrap it at the point where you know which "
        "quantity it is: InclusionProbability(arr, cohort=..., provenance=...)."
    )


def require_effective_ownership(x: object, *, argument: str = "eo") -> np.ndarray:
    """Unwrap an :class:`EffectiveOwnership`, refusing anything else."""
    if isinstance(x, EffectiveOwnership):
        return x.as_weights()
    if isinstance(x, InclusionProbability):
        raise ShareTypeError(
            f"{argument} was given a squad-inclusion probability. It ignores "
            "the armband and counts benched owners, so using it as a pace "
            "weight understates every captaincy magnet."
        )
    raise ShareTypeError(
        f"{argument} must be an EffectiveOwnership, not {type(x).__name__}."
    )


# ---------------------------------------------------------------------------
# construction from a field sample
# ---------------------------------------------------------------------------


def inclusion_probability(sample, n_players: int) -> InclusionProbability:
    """Squad-inclusion share, measured from the sampled rival squads.

    Measured from the *realised* squads rather than echoed from the forecast
    that generated them, so any renormalisation the sampler had to perform
    (``FieldModel.ownership_renormalisation``) shows up here instead of being
    quietly asserted away.
    """
    return InclusionProbability(
        p_in_squad=sample.squads.ownership_realised(n_players),
        cohort=sample.cohort,
        provenance=f"{sample.provenance}@gw{sample.gw}",
        n_observed=int(getattr(sample, "n_observed", 0)),
    )


def captaincy_share(sample, n_players: int) -> np.ndarray:
    """Share of the cohort wearing the armband on each player.

    A plain array by design: this one is unambiguous (a probability that sums
    to 1 across players), it is the ``share`` the ``(1 - 2 share)`` armband
    flip in :mod:`fpl_edge.rank.coefficients` takes, and giving it a wrapper
    type would dilute the two that need one.
    """
    return sample.squads.captain_share(n_players)


def effective_ownership(sample, n_players: int) -> EffectiveOwnership:
    """EO for a field sample: ``start + captain + triple_captain``.

    The triple-captain term comes from the sample's per-rival ``chips`` array
    when it has one -- the exact set of rivals playing ``3xc``, and the players
    they armbanded -- and is zero otherwise. Zero is the truthful value for a
    marginal prior: no rival in that construction is holding a chip, and
    spreading a *rate* over the captain distribution would be a modelling
    assumption wearing a measurement's clothes.
    """
    squads = sample.squads
    start = squads.start_share(n_players)
    cap = squads.captain_share(n_players)
    tc = np.zeros(n_players, dtype=np.float64)
    chips = getattr(sample, "chips", None)
    if chips is not None:
        playing = np.array([c == "3xc" for c in chips], dtype=bool)
        if playing.any():
            rows = np.flatnonzero(playing)
            picks = squads.slots[rows, squads.captain_slot[rows]]
            tc = np.bincount(picks, minlength=n_players) / squads.n_rivals
    return EffectiveOwnership(
        multiplier=_eo_algebra(start, cap, tc, validate=False),
        cohort=sample.cohort,
        provenance=f"{sample.provenance}@gw{sample.gw}",
        start_share=start,
        captain_share=cap,
        triple_captain_share=tc,
        n_observed=int(getattr(sample, "n_observed", 0)),
    )


def share_table(sample, universe: PlayerUniverse) -> pd.DataFrame:
    """The long ``(code, gw, own_share, captain_share)`` table the solver takes.

    Matches :data:`fpl_edge.rank.coefficients.SHARE_COLUMNS` exactly, so a
    rank-aware solve can swap this in for its ownership-marginal fallback with
    no translation layer. ``own_share`` is squad **inclusion**, never EO: the
    ``(1 - 2 * share)`` variance flip asks "does the cohort hold him", and an
    EO above 0.5 for a lightly-owned captaincy magnet would flip the sign of
    the variance credit on a player the cohort mostly does not own.

    The provenance rides along in ``frame.attrs['provenance']`` and as a
    constant column, because the solver refuses shares that cannot say where
    they came from.
    """
    n = universe.n_players
    inc = inclusion_probability(sample, n)
    cap = captaincy_share(sample, n)
    frame = pd.DataFrame({
        "code": np.asarray(universe.codes, dtype=np.int64),
        "gw": int(sample.gw),
        "own_share": inc.p_in_squad,
        "captain_share": cap,
        "cohort": sample.cohort,
        "provenance": sample.provenance,
    })
    frame.attrs["provenance"] = sample.provenance
    frame.attrs["n_observed"] = int(getattr(sample, "n_observed", 0))
    frame.attrs["cohort"] = sample.cohort
    return frame


def reconcile(inclusion: InclusionProbability, eo: EffectiveOwnership) -> dict[str, float]:
    """Audit numbers for the two quantities side by side.

    ``bench_share`` is ``inclusion - start``: owners who hold the player and do
    not play him, which is the term EO drops and inclusion keeps. A large
    ``max_eo_minus_inclusion`` is the captaincy magnet the original bug
    destroyed, and quoting it is how a reviewer sees the two are not the same
    number without having to trust a docstring.
    """
    if inclusion.n_players != eo.n_players:
        raise ValueError("inclusion and EO must cover the same universe")
    start = eo.start_share
    if start is None:
        raise ValueError("EO without its start_share component cannot be reconciled")
    diff = eo.multiplier - inclusion.p_in_squad
    return {
        "inclusion_sum": float(inclusion.p_in_squad.sum()),
        "eo_sum": float(eo.multiplier.sum()),
        "start_sum": float(start.sum()),
        "expected_inclusion_sum": float(SQUAD_SIZE),
        "expected_start_sum": float(XI_SIZE),
        "max_bench_share": float((inclusion.p_in_squad - start).max()),
        "max_eo_minus_inclusion": float(diff.max()),
        "argmax_eo_minus_inclusion": int(np.argmax(diff)),
    }
