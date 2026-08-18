"""Core identities and value types.

The single most important decision in this module is which integer identifies a
player. FPL exposes two:

* ``element_id`` -- the per-season row id. **It is reused and reassigned between
  seasons.** Joining historical data on ``element_id`` silently mixes up players
  and is the most common way an FPL backtest becomes worthless.
* ``code`` -- a stable, cross-season player identifier that follows a player
  between clubs and seasons.

Everything that crosses a season boundary keys on :class:`PlayerCode`.
Everything within a single season's API payload keys on :class:`ElementId`.
The two are deliberately distinct NewTypes so a mix-up is a type error.
"""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass
from typing import NewType

#: Stable across seasons. Use for all historical joins.
PlayerCode = NewType("PlayerCode", int)

#: Per-season row id from the FPL API. NEVER use across seasons.
ElementId = NewType("ElementId", int)

#: Per-season FPL team id (1..20, alphabetical). Also reassigned each season.
TeamId = NewType("TeamId", int)

#: Stable cross-season club identifier from the API's ``teams[].code``.
TeamCode = NewType("TeamCode", int)

FixtureId = NewType("FixtureId", int)

#: Gameweek number, 1..38.
GwId = NewType("GwId", int)

#: Season in FPL's own "2026-27" form.
Season = NewType("Season", str)


class Position(enum.IntEnum):
    """FPL element types. Values match the API's ``element_type``."""

    GKP = 1
    DEF = 2
    MID = 3
    FWD = 4

    @property
    def code(self) -> str:
        return self.name

    @classmethod
    def from_api(cls, element_type: int) -> "Position":
        try:
            return cls(element_type)
        except ValueError as exc:
            # 2025/26 had element_type 5 == Manager. It does not exist in
            # 2026/27 and must be dropped, not coerced.
            raise ValueError(
                f"Unknown element_type {element_type!r}. Note that element_type 5 "
                "(Manager) existed in 2025/26 but was removed for 2026/27; such "
                "rows must be filtered out of historical data, not mapped."
            ) from exc


class Availability(enum.StrEnum):
    """FPL ``status`` codes."""

    AVAILABLE = "a"
    DOUBTFUL = "d"
    INJURED = "i"
    SUSPENDED = "s"
    UNAVAILABLE = "u"
    NOT_IN_SQUAD = "n"

    @property
    def is_selectable(self) -> bool:
        return self in (Availability.AVAILABLE, Availability.DOUBTFUL)


class MinutesBucket(enum.IntEnum):
    """Outcome space for the minutes model.

    Deliberately coarse and mutually exclusive. The 60-minute boundary is the
    one that matters for FPL scoring (appearance points and clean sheets), so it
    is a bucket edge rather than something a point estimate has to straddle.
    """

    UNAVAILABLE = 0  # did not feature at all
    CAMEO = 1  # 1-59 minutes
    FULL = 2  # 60+ minutes

    @property
    def appearance_points(self) -> int:
        return {0: 0, 1: 1, 2: 2}[int(self)]


@dataclass(frozen=True, slots=True)
class Money:
    """FPL prices in tenths of a million, the unit the API actually uses.

    Float pounds are never used internally: 0.1m increments plus a
    round-half-down sell-on fee makes float arithmetic produce off-by-one-tenth
    errors that quietly break budget feasibility in the optimizer.
    """

    tenths: int

    def __post_init__(self) -> None:
        if not isinstance(self.tenths, int):
            raise TypeError(f"Money must be an integer number of tenths, got {self.tenths!r}")

    @classmethod
    def from_millions(cls, millions: float) -> "Money":
        tenths = round(millions * 10)
        if abs(tenths - millions * 10) > 1e-6:
            raise ValueError(f"{millions} is not a whole number of 0.1m increments")
        return cls(tenths)

    @property
    def millions(self) -> float:
        return self.tenths / 10.0

    def __add__(self, other: "Money") -> "Money":
        return Money(self.tenths + other.tenths)

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.tenths - other.tenths)

    def __str__(self) -> str:
        return f"£{self.millions:.1f}m"


def selling_price(bought_at: Money, current: Money) -> Money:
    """Selling price under the 50% sell-on fee.

    You keep half of any *rise*, rounded down to the nearest 0.1m. A price fall
    is borne in full. Verified against the official worked example: bought at
    7.5, now 7.8, sells at 7.6.
    """
    rise = current.tenths - bought_at.tenths
    if rise <= 0:
        return current
    return Money(bought_at.tenths + rise // 2)


@dataclass(frozen=True, slots=True)
class Deadline:
    """A gameweek deadline in UTC.

    Always timezone-aware and always UTC. The rules page renders deadlines in
    browser-local time, so any naive datetime in this codebase is a bug.
    """

    gw: GwId
    utc: dt.datetime

    def __post_init__(self) -> None:
        if self.utc.tzinfo is None:
            raise ValueError(f"Deadline for GW{self.gw} is naive; must be tz-aware UTC")
        if self.utc.utcoffset() != dt.timedelta(0):
            raise ValueError(f"Deadline for GW{self.gw} is not UTC: {self.utc.tzinfo}")
