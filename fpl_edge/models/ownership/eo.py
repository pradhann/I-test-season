"""Effective ownership algebra.

This module is deliberately small, pure and paranoid, because effective
ownership is where sign errors hide. The engine's objective is
P(rank < threshold), and rank is decided by *my score minus the field's score*.
The field's score is a weighted sum of player points where the weight on player
p is exactly its effective ownership. Get a sign wrong here and every
differential is mispriced in the direction that feels clever.

The definition
--------------

Effective ownership is the **mean FPL multiplier the field applies to a
player**, not a probability and not a percentage of owners::

    EO_p = (1/M) * sum over managers m of  multiplier_m(p)

where the multiplier is

===========================================  ==========
manager m's relationship to player p          multiplier
===========================================  ==========
does not own p                                0
owns p but benches them                       0
starts p                                      1
starts p and captains p                       2
starts p and triple-captains p                3
===========================================  ==========

Collecting terms, with

* ``start_share``  = share of the whole field **starting** p,
* ``captain_share`` = share of the whole field **captaining** p (this INCLUDES
  managers using the Triple Captain chip on p),
* ``triple_captain_share`` = the subset of those using Triple Captain,

then

.. math::

    EO_p = start\\_share_p + captain\\_share_p + triple\\_captain\\_share_p

The last term is easy to get wrong. A triple captain contributes 3, which is
1 (started) + 1 (captained) + 1 (extra). So triple captaincy is added *once
more*, not twice more, and not instead of the ordinary captain term.

Three sign traps this module exists to prevent
----------------------------------------------

1. ``EO = ownership - captaincy``. Captaincy is always **additive**. A player
   the field captains is harder to beat, not easier.
2. ``EO = ownership * (1 + captaincy)``. Captaincy share is a share of the
   *whole field*, not of the player's owners, so it is added, not multiplied.
3. Using ``selected_by_percent`` as ``start_share``. Ownership counts bench
   players, who score nothing. ``start_share <= ownership`` always, with
   equality only if every owner starts them.

Units are fractions of the field in [0, 1] throughout. EO itself is NOT bounded
by 1: a universally owned, universally captained player has EO = 2.0.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Multiplier the FPL rules apply to an ordinary captain.
CAPTAIN_MULTIPLIER = 2
#: Multiplier under the Triple Captain chip.
TRIPLE_CAPTAIN_MULTIPLIER = 3

_TOL = 1e-9


def _as_array(x: object, name: str) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    if arr.ndim == 0:
        arr = arr.reshape(1)
    if not np.isfinite(arr).all():
        raise ValueError(f"{name} contains non-finite values")
    return arr


def effective_ownership(
    start_share: np.ndarray | float,
    captain_share: np.ndarray | float,
    triple_captain_share: np.ndarray | float = 0.0,
    *,
    validate: bool = True,
) -> np.ndarray:
    """Mean multiplier the field applies to each player.

    Parameters
    ----------
    start_share:
        Share of the whole field that **starts** the player (bench excluded).
    captain_share:
        Share of the whole field that captains the player. Includes Triple
        Captain users.
    triple_captain_share:
        Share of the whole field using the Triple Captain chip on the player.
        Must be a subset of ``captain_share``.

    Returns
    -------
    ``start_share + captain_share + triple_captain_share``.
    """
    s = _as_array(start_share, "start_share")
    c = _as_array(captain_share, "captain_share")
    t = _as_array(triple_captain_share, "triple_captain_share")
    if t.size == 1 and s.size > 1:
        t = np.full_like(s, float(t[0]))
    if validate:
        _validate_shares(s, c, t)
    return s + c + t


def _validate_shares(s: np.ndarray, c: np.ndarray, t: np.ndarray) -> None:
    if not (s.shape == c.shape == t.shape):
        raise ValueError(f"shape mismatch: start{s.shape} captain{c.shape} tc{t.shape}")
    for arr, name in ((s, "start_share"), (c, "captain_share"), (t, "triple_captain_share")):
        if (arr < -_TOL).any():
            raise ValueError(f"{name} has negative entries; shares are fractions in [0, 1]")
        if (arr > 1.0 + _TOL).any():
            raise ValueError(
                f"{name} exceeds 1.0. Shares are fractions of the field, not percentages"
            )
    if (t > c + _TOL).any():
        raise ValueError(
            "triple_captain_share exceeds captain_share. Triple Captain users are a "
            "SUBSET of captainers, so captain_share must already include them."
        )
    if (c > s + _TOL).any():
        raise ValueError(
            "captain_share exceeds start_share. A captained player is by definition "
            "started, so captain_share <= start_share must hold."
        )


def start_share_from_ownership(
    ownership: np.ndarray | float,
    p_start_given_owned: np.ndarray | float,
    *,
    bench_boost_share: float = 0.0,
) -> np.ndarray:
    """Convert ownership into starting share.

    ``ownership`` is FPL's ``selected_by_percent`` as a fraction: it counts the
    four bench players too, who score nothing. ``p_start_given_owned`` is the
    probability an owner names the player in their XI.

    ``bench_boost_share`` is the share of *owners* playing the Bench Boost chip
    this gameweek; for them every owned player counts, so the effective start
    probability is lifted toward 1.
    """
    own = _as_array(ownership, "ownership")
    p = _as_array(p_start_given_owned, "p_start_given_owned")
    if (own < -_TOL).any() or (own > 1.0 + _TOL).any():
        raise ValueError("ownership must be a fraction in [0, 1], not a percentage")
    if (p < -_TOL).any() or (p > 1.0 + _TOL).any():
        raise ValueError("p_start_given_owned must be in [0, 1]")
    if not 0.0 <= bench_boost_share <= 1.0:
        raise ValueError("bench_boost_share must be in [0, 1]")
    eff_p = p + bench_boost_share * (1.0 - p)
    return own * eff_p


@dataclass(frozen=True, slots=True)
class FieldShares:
    """One gameweek's field composition for a set of players.

    Every array is a fraction of the **whole field**, indexed consistently.
    """

    ownership: np.ndarray
    p_start_given_owned: np.ndarray
    captain_share: np.ndarray
    triple_captain_share: np.ndarray | None = None
    bench_boost_share: float = 0.0

    def start_share(self) -> np.ndarray:
        return start_share_from_ownership(
            self.ownership, self.p_start_given_owned,
            bench_boost_share=self.bench_boost_share,
        )

    def eo(self) -> np.ndarray:
        tc = (
            np.zeros_like(self.ownership)
            if self.triple_captain_share is None
            else self.triple_captain_share
        )
        return effective_ownership(self.start_share(), self.captain_share, tc)

    def field_mean_points(self, points: np.ndarray) -> np.ndarray:
        """The average manager's score given per-player points.

        This is the identity the whole objective rests on: the field's mean
        score is the EO-weighted sum of points. If your EO vector is wrong,
        your estimate of the field is wrong, and rank is a function of the
        difference between your score and that.
        """
        pts = _as_array(points, "points")
        if pts.shape != self.ownership.shape:
            raise ValueError("points must align with the ownership vector")
        return float(np.sum(self.eo() * pts))


def my_multiplier(owned: bool, started: bool, captain: bool, triple: bool = False) -> int:
    """The multiplier *my* team applies. The single-manager analogue of EO."""
    if not owned or not started:
        if captain:
            raise ValueError("a captain must be owned and started")
        return 0
    if triple and not captain:
        raise ValueError("triple captain implies captain")
    if triple:
        return TRIPLE_CAPTAIN_MULTIPLIER
    if captain:
        return CAPTAIN_MULTIPLIER
    return 1


def rank_edge(my_mult: np.ndarray | float, eo: np.ndarray | float,
              points: np.ndarray | float) -> float:
    """Expected points gained on the average manager.

    ``sum((my_multiplier - EO) * points)``. Positive means I gain on the field.
    Owning a 100%-EO player at multiplier 1 is worth exactly zero rank edge no
    matter how many points they score -- which is the entire reason this module
    exists.
    """
    m = _as_array(my_mult, "my_mult")
    e = _as_array(eo, "eo")
    p = _as_array(points, "points")
    return float(np.sum((m - e) * p))
