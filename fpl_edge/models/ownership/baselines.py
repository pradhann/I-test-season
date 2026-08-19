"""The two baselines an ownership forecast has to beat to be worth running.

Neither is a straw man. Persistence is genuinely hard to beat over a single
gameweek because ownership is a slow, sticky, heavily autocorrelated series;
most of what looks like signal is the level. Transfer momentum is what an
experienced FPL manager does in their head, and it is the obvious thing to try.

Both return ownership *shares* at the next deadline on the same scale as the
model, so the comparison is like for like.
"""

from __future__ import annotations

import numpy as np


def persistence(own: np.ndarray) -> np.ndarray:
    """Ownership stays exactly as it is now.

    Deliberately not renormalised. "Nothing changes" is the claim being tested,
    and quietly projecting it back onto the simplex would be doing part of the
    model's job for it.
    """
    return np.asarray(own, dtype=float).copy()


def transfer_momentum(own: np.ndarray, flow: np.ndarray, *, carry: float = 1.0) -> np.ndarray:
    """Next window's net flow equals this window's.

    ``flow`` is net transfers in minus out, expressed as a share of the current
    field. ``carry = 1`` is the naive extrapolation; the model fits the carry
    coefficient rather than assuming it.
    """
    return np.asarray(own, dtype=float) + carry * np.asarray(flow, dtype=float)


def drift_momentum(
    own_now: np.ndarray, own_before: np.ndarray, days_between: float, days_ahead: float
) -> np.ndarray:
    """Linear extrapolation of the observed drift rate.

    The GW1 analogue of transfer momentum. Before the first deadline there is no
    transfer flow at all -- ``transfers_in_event`` is identically zero -- so the
    only momentum available is the drift measured between two polls of the
    ownership series itself.
    """
    if days_between <= 0:
        raise ValueError("days_between must be positive")
    rate = (np.asarray(own_now, dtype=float) - np.asarray(own_before, dtype=float)) / days_between
    return np.asarray(own_now, dtype=float) + rate * days_ahead


def captaincy_persistence(prev_share: np.ndarray) -> np.ndarray:
    """Last gameweek's captaincy shares, unchanged."""
    return np.asarray(prev_share, dtype=float).copy()


def captaincy_proportional(own: np.ndarray, *, eligible: np.ndarray | None = None) -> np.ndarray:
    """Captaincy allocated in proportion to ownership.

    The natural naive model: the field captains whoever it owns. It is wrong in
    a specific and important way -- captaincy is far more concentrated than
    ownership, because everyone converges on the same one or two premiums -- and
    quantifying that gap is the point of having it as a baseline.
    """
    o = np.asarray(own, dtype=float).copy()
    if eligible is not None:
        o = o * np.asarray(eligible, dtype=float)
    total = o.sum()
    if total <= 0:
        raise ValueError("cannot allocate captaincy over zero total ownership")
    return o / total
