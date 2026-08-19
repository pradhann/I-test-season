"""Field size: how many managers there are, and how fast that is changing.

Ownership is a *share*, so the size of the field is not a cosmetic detail. Two
mechanical consequences drive most of the predictable part of ownership drift:

1. **Dilution.** If the field grows from N0 to N1 and the newcomers pick a
   flatter template than the incumbents, every heavily owned player's share
   falls even though nobody sold them. Between GW1 and GW2 of a real season the
   field grows by 8-10%, which moves a 70%-owned player by several percentage
   points for free. Persistence cannot see this; it is the single largest
   systematic edge available to an ownership model.
2. **The simplex constraint.** Every manager owns exactly 15 players, so
   ``sum_p ownership_p == 15`` exactly, always. Any forecast that does not
   respect it is internally inconsistent. Observed in real bootstrap payloads
   as ``sum(selected_by_percent) == 1499.5`` (rounding to 0.1% aside).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Every FPL squad contains exactly this many players, so ownership shares sum
#: to it. Mirrors rules.squad.size; kept as a local constant so this module has
#: no import-time dependency on the rule registry.
SQUAD_SIZE = 15


def simplex_total(n_players: int) -> float:
    """The value ``sum_p ownership_p`` must take. Independent of ``n_players``."""
    if n_players <= 0:
        raise ValueError("n_players must be positive")
    return float(SQUAD_SIZE)


def project_to_simplex(
    shares: np.ndarray, *, total: float = float(SQUAD_SIZE), floor: float = 0.0,
    iterations: int = 100,
) -> np.ndarray:
    """Rescale so the shares sum to ``total`` while staying inside ``[floor, 1]``.

    Rescaling is multiplicative rather than additive so that a player on 0.1% is
    not pushed negative by a correction sized for a player on 70%. But a single
    clip-then-scale does not hold the upper bound: scaling a vector whose top
    entry is 0.95 up by 8% puts it above 1, which is an ownership of more than
    100% and makes the effective-ownership algebra reject the frame. So the
    excess from saturated entries is redistributed over the rest until both the
    sum and the bound hold.
    """
    x = np.clip(np.asarray(shares, dtype=float), floor, 1.0)
    if x.sum() <= 0:
        raise ValueError("cannot project an all-zero ownership vector")
    if total > x.size:
        raise ValueError(
            f"cannot fit a total of {total} across {x.size} shares bounded by 1"
        )
    for _ in range(iterations):
        deficit = total - x.sum()
        if abs(deficit) < 1e-12:
            break
        free = x < 1.0 - 1e-12 if deficit > 0 else x > floor + 1e-12
        if not free.any():
            break
        scale = (total - x[~free].sum()) / max(x[free].sum(), 1e-15)
        x = np.where(free, np.clip(x * scale, floor, 1.0), x)
    return x


@dataclass(frozen=True, slots=True)
class FieldGrowth:
    """Predicted field size at the next observation point.

    ``w = N_now / N_next`` is the dilution weight: incumbent shares are
    multiplied by it before newcomers are added.
    """

    n_now: float
    n_next: float

    def __post_init__(self) -> None:
        if self.n_now <= 0 or self.n_next <= 0:
            raise ValueError("field sizes must be positive")

    @property
    def w(self) -> float:
        return self.n_now / self.n_next

    @property
    def newcomer_share(self) -> float:
        """Fraction of the *next* field that has just joined."""
        return max(0.0, 1.0 - self.w)


def newcomer_template(ownership: np.ndarray, flatness: float) -> np.ndarray:
    """The squad a newly registered manager is assumed to pick.

    Modelled as the incumbent template raised to a power below 1, which flattens
    it: newcomers are less concentrated on the top template picks than the
    settled field. ``flatness = 1`` reproduces the incumbent template exactly
    (no dilution effect); ``flatness = 0`` is a uniform random squad.
    """
    if not 0.0 <= flatness <= 1.0:
        raise ValueError("flatness must be in [0, 1]")
    o = np.clip(np.asarray(ownership, dtype=float), 1e-9, 1.0)
    q = o**flatness
    return project_to_simplex(q)


def extrapolate_total_players(
    times_seconds: np.ndarray, totals: np.ndarray, horizon_seconds: float
) -> float:
    """Straight-line extrapolation of the registration count.

    Pre-GW1 the registration count climbs steadily and roughly linearly on a
    scale of hours, so a least-squares line through the polled values is both
    adequate and honest about what it is. Requires at least two distinct polls;
    guessing a growth rate from one observation is refused.
    """
    t = np.asarray(times_seconds, dtype=float)
    y = np.asarray(totals, dtype=float)
    if t.size < 2 or np.unique(t).size < 2:
        raise ValueError(
            "need at least two distinct polls of total_players to estimate growth; "
            "a single observation cannot support an extrapolation"
        )
    slope, intercept = np.polyfit(t - t[0], y, 1)
    predicted = intercept + slope * (horizon_seconds)
    return float(max(predicted, y.max()))
