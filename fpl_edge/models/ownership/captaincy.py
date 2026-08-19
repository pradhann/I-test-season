"""Who the field captains.

Captaincy is not ownership. It is far more concentrated: the field owns a few
hundred players but captains essentially three or four, and the single most
captained player routinely takes 40-70% of the entire field. That concentration
is the whole story for rank utility, because a captain contributes twice and a
triple captain three times, so a captaincy share of 0.55 moves effective
ownership by more than any ownership difference on the board.

Model
-----

A multinomial logit over the players a manager owns::

    c_p  proportional to  own_p * exp(kappa * appeal_p) * eligible_p

``appeal_p`` is a perceived-ceiling score built from what a Snapshot actually
carries -- price, position and fixture -- because the points model belongs to
another team and this model must not reach for it. Price is the strongest
single proxy the game exposes: FPL prices premiums by expected ceiling, and the
crowd captains ceiling.

``kappa`` is the concentration parameter, and it is the one number here that
cannot be measured before a deadline has passed. It is a **stated prior**, not a
fitted value, and :func:`calibrate_kappa` exists to replace it with a measured
one the moment the API publishes ``events[].most_captained`` and
``average_entry_score`` after GW1. Treat the pre-GW1 captaincy vector as the
least trustworthy output of this package.

Vice-captaincy
--------------

The rules give the armband to the vice if the captain plays zero minutes. That
does not change effective ownership *at the deadline*, which is what this model
forecasts, but it does change realised EO, so the vice share is returned as
well rather than silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from fpl_edge.types import Position

#: Prior concentration. Larger => captaincy piles onto the single best premium.
#: Chosen so the modal captain takes a share consistent with a top-heavy but not
#: degenerate field; see docs/models/ownership.md for the sensitivity band.
DEFAULT_KAPPA = 0.35

#: Additive appeal by position, in the same units as kappa * price(£m).
#: Goalkeepers and defenders are captained so rarely that the penalty is large
#: rather than merely negative.
POSITION_APPEAL: dict[int, float] = {
    int(Position.GKP): -6.0,
    int(Position.DEF): -3.0,
    int(Position.MID): 0.0,
    int(Position.FWD): 0.3,
}

#: Home advantage, in appeal units.
HOME_APPEAL = 0.45

#: Share of *owners* who bench a player they own. Ownership counts the four
#: bench slots, so a player owned by 10% of the field is started by fewer.
#: Position-specific because bench composition is not uniform.
DEFAULT_START_PROB: dict[int, float] = {
    int(Position.GKP): 0.50,   # exactly one of two keepers plays
    int(Position.DEF): 0.78,
    int(Position.MID): 0.82,
    int(Position.FWD): 0.80,
}


@dataclass(frozen=True, slots=True)
class CaptaincyParams:
    kappa: float = DEFAULT_KAPPA
    home_appeal: float = HOME_APPEAL
    position_appeal: tuple[tuple[int, float], ...] = tuple(POSITION_APPEAL.items())
    #: Share of the field playing Triple Captain this gameweek, spread across
    #: captain choices in proportion to captaincy share.
    triple_captain_usage: float = 0.0
    #: Share of the field playing Bench Boost this gameweek.
    bench_boost_usage: float = 0.0

    def appeal_by_position(self) -> dict[int, float]:
        return dict(self.position_appeal)


def appeal_score(
    price_tenths: np.ndarray,
    position: np.ndarray,
    is_home: np.ndarray | None = None,
    params: CaptaincyParams | None = None,
) -> np.ndarray:
    """Perceived captaincy appeal, in units where ``kappa`` multiplies £m."""
    p = params or CaptaincyParams()
    price_m = np.asarray(price_tenths, dtype=float) / 10.0
    pos = np.asarray(position, dtype=int)
    table = p.appeal_by_position()
    unknown = sorted(set(pos.tolist()) - set(table))
    if unknown:
        raise ValueError(f"no captaincy appeal defined for element_type(s) {unknown}")
    out = price_m + np.array([table[int(x)] for x in pos], dtype=float)
    if is_home is not None:
        out = out + p.home_appeal * np.asarray(is_home, dtype=float)
    return out


def captaincy_share(
    ownership: np.ndarray,
    appeal: np.ndarray,
    *,
    available: np.ndarray | None = None,
    params: CaptaincyParams | None = None,
) -> np.ndarray:
    """Share of the whole field captaining each player. Sums to 1.

    Every manager names exactly one captain, so this is a probability
    distribution over players and the normalisation is not optional. A captaincy
    vector that does not sum to 1 silently rescales the field's mean score and
    therefore every rank probability downstream.
    """
    p = params or CaptaincyParams()
    own = np.asarray(ownership, dtype=float)
    a = np.asarray(appeal, dtype=float)
    if own.shape != a.shape:
        raise ValueError("ownership and appeal must align")
    mask = np.ones_like(own) if available is None else np.asarray(available, dtype=float)
    # Subtract the max before exponentiating: appeal spans ~20 units and a naive
    # exp overflows for premium prices.
    logits = p.kappa * (a - a.max())
    w = own * np.exp(logits) * mask
    total = w.sum()
    if total <= 0:
        raise ValueError("no eligible captain candidates")
    return w / total


def vice_captain_share(
    ownership: np.ndarray,
    appeal: np.ndarray,
    captain: np.ndarray,
    *,
    available: np.ndarray | None = None,
    params: CaptaincyParams | None = None,
) -> np.ndarray:
    """Share of the field naming each player vice-captain.

    Modelled as the same logit with the captain choice removed: the vice is
    typically the manager's second-favourite armband candidate. Approximated at
    the aggregate level by re-normalising the captaincy weights after damping
    each player by its own captaincy share.
    """
    c = np.asarray(captain, dtype=float)
    base = captaincy_share(ownership, appeal, available=available, params=params)
    w = base * (1.0 - c)
    total = w.sum()
    if total <= 0:
        raise ValueError("no eligible vice-captain candidates")
    return w / total


def triple_captain_share(captain: np.ndarray, params: CaptaincyParams | None = None) -> np.ndarray:
    """Split the Triple Captain chip usage across captain choices.

    Chip users are assumed to captain the same players as everyone else, in the
    same proportions. That is an assumption, and a mildly conservative one --
    chip users skew toward the very top pick -- but it keeps the subset
    invariant ``triple <= captain`` true by construction, which the EO algebra
    requires.
    """
    p = params or CaptaincyParams()
    if not 0.0 <= p.triple_captain_usage <= 1.0:
        raise ValueError("triple_captain_usage must be in [0, 1]")
    return np.asarray(captain, dtype=float) * p.triple_captain_usage


def start_probability(position: np.ndarray, available: np.ndarray | None = None) -> np.ndarray:
    """P(an owner starts this player), by position.

    Deliberately crude. The minutes model owns real start probabilities; this is
    the fallback so that ownership is never mistaken for starting share, which
    is the sign error this package exists to prevent.
    """
    pos = np.asarray(position, dtype=int)
    unknown = sorted(set(pos.tolist()) - set(DEFAULT_START_PROB))
    if unknown:
        raise ValueError(f"no start probability defined for element_type(s) {unknown}")
    out = np.array([DEFAULT_START_PROB[int(x)] for x in pos], dtype=float)
    if available is not None:
        out = out * np.asarray(available, dtype=float)
    return out


def calibrate_kappa(
    ownership: np.ndarray,
    appeal: np.ndarray,
    *,
    observed_top_share: float,
    top_index: int,
    available: np.ndarray | None = None,
    bounds: tuple[float, float] = (0.02, 1.5),
) -> float:
    """Solve for the concentration that reproduces one observed captaincy share.

    The FPL API publishes ``events[].most_captained`` once a gameweek is under
    way, and third-party samples of manager picks give the share attached to it.
    One such observation pins ``kappa``, because captaincy share is monotone in
    it. Bisection rather than a solver import: the function is monotone and the
    bracket is known.
    """
    if not 0.0 < observed_top_share < 1.0:
        raise ValueError("observed_top_share must be a fraction strictly inside (0, 1)")
    lo, hi = bounds

    def top(k: float) -> float:
        return float(captaincy_share(
            ownership, appeal, available=available, params=CaptaincyParams(kappa=k),
        )[top_index])

    if top(hi) < observed_top_share:
        return hi
    if top(lo) > observed_top_share:
        return lo
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if top(mid) < observed_top_share:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def blend_with_observed(
    predicted: np.ndarray, observed_prior: np.ndarray, weight: float
) -> np.ndarray:
    """Mix the logit forecast with the last observed captaincy distribution.

    Captaincy is sticky: measured on the simulated field, simply repeating last
    gameweek's shares beats the price-and-ownership logit outright. So once a
    gameweek has been played and real shares exist, the forecast should lean on
    them and use the logit to move them, not to replace them.

    This is exactly the resource GW1 does not have. Before the first deadline
    there is no previous distribution to blend with, ``weight`` is zero by
    force, and the captaincy forecast is the logit alone -- which is the main
    reason the GW1 captaincy number is the least trustworthy thing this package
    emits.
    """
    if not 0.0 <= weight <= 1.0:
        raise ValueError("weight must be in [0, 1]")
    p = np.asarray(predicted, dtype=float)
    o = np.asarray(observed_prior, dtype=float)
    if p.shape != o.shape:
        raise ValueError("predicted and observed_prior must align")
    mixed = (1.0 - weight) * p + weight * o
    total = mixed.sum()
    if total <= 0:
        raise ValueError("blended captaincy has no mass")
    return mixed / total


#: Every manager starts exactly this many players, so start shares sum to it.
STARTING_XI = 11
#: ...and exactly this many sit on the bench, which a Bench Boost activates.
BENCH_SIZE = 4


def cap_to_start(captain: np.ndarray, start: np.ndarray, *, iterations: int = 50) -> np.ndarray:
    """Project captaincy onto ``{c >= 0, sum c = 1, c <= start}``.

    A captain must be in the starting XI, so no player's captaincy share can
    exceed their starting share -- and the armbands must still add to exactly
    one per manager. Clamping and renormalising once satisfies neither: the
    renormalisation pushes clamped players back over their cap, and clamping
    again breaks the sum. This redistributes the clipped excess over the players
    with headroom until both hold.
    """
    c = np.clip(np.asarray(captain, dtype=float), 0.0, None)
    s = np.clip(np.asarray(start, dtype=float), 0.0, None)
    if s.sum() < 1.0 - 1e-9:
        raise ValueError(
            f"start shares sum to {s.sum():.4f} < 1, so the field cannot name one "
            "captain each; the starting-share vector is wrong"
        )
    if c.sum() <= 0:
        raise ValueError("captaincy vector has no mass")
    c = c / c.sum()
    for _ in range(iterations):
        over = c > s
        c = np.minimum(c, s)
        deficit = 1.0 - c.sum()
        if abs(deficit) < 1e-12:
            break
        headroom = np.where(over, 0.0, s - c)
        total_headroom = headroom.sum()
        if total_headroom <= 1e-15:
            break
        c = c + deficit * (headroom / total_headroom)
    return np.clip(c, 0.0, s)


def normalise_start_share(
    ownership: np.ndarray,
    p_start: np.ndarray,
    *,
    bench_boost_share: float = 0.0,
    iterations: int = 30,
) -> np.ndarray:
    """Rescale ``p_start`` so starting shares sum to the XI size.

    Every manager names exactly 11 starters (15 under Bench Boost), so
    ``sum_p ownership_p * p_start_p`` is pinned. A per-position table of start
    probabilities will not satisfy that by accident, and an unpinned version
    silently inflates or deflates every effective ownership in the frame -- and
    therefore the estimated mean score of the entire field.
    """
    own = np.asarray(ownership, dtype=float)
    p = np.clip(np.asarray(p_start, dtype=float), 0.0, 1.0)
    target = (STARTING_XI + BENCH_SIZE * bench_boost_share) * (own.sum() / (STARTING_XI + BENCH_SIZE))
    for _ in range(iterations):
        current = float(np.sum(own * p))
        if current <= 0:
            raise ValueError("no player can start; start probabilities are all zero")
        if abs(current - target) < 1e-9:
            break
        p = np.clip(p * (target / current), 0.0, 1.0)
    return p
