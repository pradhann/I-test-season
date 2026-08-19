"""The value types the intel layer moves around.

Everything here is frozen data with two timestamps, and the distinction between
them is the whole point of the module:

``published_at``
    When the world could have known. This is the ``as_of`` a point-in-time read
    filters on.
``observed_at``
    When *we* found out. Strictly later. Recorded so the pipeline's own lag is
    measurable, never used to hide a fact from a snapshot.

Getting these the wrong way round is the classic FPL backtest bug: a scraped
injury table has no publication stamp, so the scraper's clock silently becomes
the injury's clock, and a model "knows" on 1 September about a hamstring that
tore on 20 September. Making the two fields separate and mandatory means the
mistake has to be typed out deliberately.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
from dataclasses import dataclass

UTC = dt.timezone.utc


class IntelKind(enum.StrEnum):
    """What sort of thing an :class:`IntelItem` is.

    A closed set because every renderer switches on it, and an unknown kind
    should be a load error rather than a section that silently disappears from
    a dossier.
    """

    AVAILABILITY = "availability"
    PRESS_CONFERENCE = "press_conference"
    SET_PIECE = "set_piece"
    OUT_OF_POSITION = "out_of_position"
    FORMATION = "formation"
    SOURCE_PROBE = "source_probe"


class Duty(enum.StrEnum):
    """The three set-piece responsibilities FPL publishes an order for."""

    PENALTIES = "penalties"
    DIRECT_FREEKICKS = "direct_freekicks"
    CORNERS_INDIRECT = "corners_indirect"

    @property
    def label(self) -> str:
        return {
            Duty.PENALTIES: "penalties",
            Duty.DIRECT_FREEKICKS: "direct free kicks",
            Duty.CORNERS_INDIRECT: "corners and indirect free kicks",
        }[self]


#: Expected goals per game that first-choice penalty duty is worth to the taker.
#: Premier League sides win roughly 0.13 penalties per match and takers convert
#: about 0.79 of them, which lands near 0.10 goals per game. The number is used
#: only to VALUE a change in duty, never to predict points -- the points model
#: already carries penalties inside total xG (see fpl_edge/models/points/shares.py,
#: which says so explicitly and lists the consequence as a known weakness).
PENALTY_GOALS_PER_GAME = 0.10

#: The same quantity for the other two duties. Direct free kicks convert far
#: less often; corners produce assists rather than goals and are valued at zero
#: goals here on purpose, because an assist is worth 3 points to a taker with no
#: reliable per-corner rate available in this warehouse. Reported as a duty
#: change, valued at zero, rather than given an invented number.
DUTY_GOALS_PER_GAME: dict[Duty, float] = {
    Duty.PENALTIES: PENALTY_GOALS_PER_GAME,
    Duty.DIRECT_FREEKICKS: 0.02,
    Duty.CORNERS_INDIRECT: 0.0,
}


def _utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


def content_id(prefix: str, *parts: object) -> str:
    """A stable id derived from content, so replay is idempotent.

    The archive of raw FPL bodies is replayable by design (every response is
    written to ``data/raw`` with its sha256). If ids were random, replaying it
    would duplicate every row and quietly double every count.
    """
    digest = hashlib.sha256(
        "\x00".join("" if p is None else str(p) for p in parts).encode("utf-8")
    ).hexdigest()[:16]
    return f"{prefix}_{digest}"


@dataclass(frozen=True, slots=True)
class IntelItem:
    """One discrete piece of news about a player, a club or a source."""

    item_id: str
    published_at: dt.datetime
    observed_at: dt.datetime
    kind: IntelKind
    headline: str
    source: str
    season: str | None = None
    player_code: int | None = None
    team_code: int | None = None
    body: str | None = None
    source_url: str | None = None
    http_status: int | None = None
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "published_at", _utc(self.published_at, "published_at"))
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "observed_at"))
        if self.observed_at < self.published_at:
            # Not pedantry. This ordering is the only thing standing between the
            # warehouse and a "fact" that was recorded before it existed.
            raise ValueError(
                f"observed_at {self.observed_at.isoformat()} precedes published_at "
                f"{self.published_at.isoformat()} for {self.item_id!r}: we cannot "
                "have seen it before the world published it."
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence}")

    @property
    def lag(self) -> dt.timedelta:
        """How long we took to notice. The number a faster feed would buy."""
        return self.observed_at - self.published_at

    def age_at(self, when: dt.datetime) -> dt.timedelta:
        return _utc(when, "when") - self.published_at


@dataclass(frozen=True, slots=True)
class SetPieceDuty:
    """Where a player sits in one of their club's set-piece orders.

    ``ord is None`` is a real, meaningful state: the player is not on the list.
    It is stored rather than omitted, because "Watkins is no longer on penalties"
    is the single most valuable thing this table can say and a row that simply
    vanishes cannot say it.
    """

    season: str
    code: int
    duty: Duty
    ord: int | None
    as_of: dt.datetime
    source: str
    team_code: int | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))
        if self.ord is not None and self.ord < 1:
            raise ValueError(f"set-piece order is 1-based, got {self.ord}")

    @property
    def is_first_choice(self) -> bool:
        return self.ord == 1


@dataclass(frozen=True, slots=True)
class DutyChange:
    """A move in a set-piece order, valued in goals per game."""

    change_id: str
    season: str
    code: int
    duty: Duty
    ord_before: int | None
    ord_after: int | None
    prior_as_of: dt.datetime
    detected_at: dt.datetime
    delta_goals_per_game: float
    headline: str
    team_code: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "prior_as_of", _utc(self.prior_as_of, "prior_as_of"))
        object.__setattr__(self, "detected_at", _utc(self.detected_at, "detected_at"))
        if self.detected_at < self.prior_as_of:
            raise ValueError("a change cannot be detected before the observation it follows")

    @property
    def is_promotion(self) -> bool:
        """Did the player move toward the front of the queue?"""
        return _rank_value(self.ord_after) > _rank_value(self.ord_before)


def _rank_value(ord_: int | None) -> float:
    """Turn a 1-based order into 'share of the duty', where higher is better.

    Not linear, and not a guess dressed up as one. A club's second penalty taker
    takes a penalty only when the first is off the pitch or declines it, which is
    a small fraction of the time; third is nearly noise. The 1/2^(n-1) shape
    encodes that steep drop-off, and off-the-list is exactly zero.
    """
    if ord_ is None:
        return 0.0
    return 0.5 ** (int(ord_) - 1)


def value_duty_change(duty: Duty, before: int | None, after: int | None) -> float:
    """Goals per game gained (positive) or lost (negative) by the move."""
    base = DUTY_GOALS_PER_GAME[duty]
    return base * (_rank_value(after) - _rank_value(before))


@dataclass(frozen=True, slots=True)
class OopSignal:
    """FPL's classification against what the player's per-90 profile says.

    ``score`` is a margin, not a probability: how much better the observed rates
    fit ``plays_like`` than they fit ``fpl_position``. Left as a raw margin
    because calibrating it into a probability would need labelled role data this
    project does not have, and a fake probability is worse than an honest score.
    """

    season: str
    code: int
    fpl_position: int
    plays_like: int
    score: float
    evidence: str
    as_of: dt.datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))

    @property
    def is_mismatch(self) -> bool:
        return int(self.fpl_position) != int(self.plays_like)


@dataclass(frozen=True, slots=True)
class FormationObservation:
    """Starters by FPL position for one club in one fixture."""

    season: str
    team_code: int
    fixture_id: int
    gw: int | None
    n_def: int
    n_mid: int
    n_fwd: int
    as_of: dt.datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", _utc(self.as_of, "as_of"))

    @property
    def shape(self) -> str:
        return f"{self.n_def}-{self.n_mid}-{self.n_fwd}"


@dataclass(frozen=True, slots=True)
class SourceProbe:
    """One honest attempt to reach an external source.

    ``verdict`` is the operational answer:

    ``usable``      robots.txt allows it and the page came back.
    ``disallowed``  robots.txt or the terms say no. We stopped.
    ``blocked``     the site refused us (403 / challenge / paywall).
    ``error``       transport failure, timeout, or the site was down.

    The distinction between ``disallowed`` and ``blocked`` is the one that
    matters ethically: the first is a rule we are choosing to follow, the second
    is a door that happens to be shut. Neither is a reason to forge a User-Agent
    or a TLS fingerprint, and nothing in this package does.
    """

    probe_id: str
    probed_at: dt.datetime
    source: str
    url: str
    verdict: str
    http_status: int | None = None
    robots_status: int | None = None
    robots_allows: bool | None = None
    bytes: int | None = None
    note: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "probed_at", _utc(self.probed_at, "probed_at"))
        if self.verdict not in ("usable", "disallowed", "blocked", "error"):
            raise ValueError(f"unknown probe verdict {self.verdict!r}")

    def render(self) -> str:
        code = "—" if self.http_status is None else str(self.http_status)
        robots = (
            "robots unreadable" if self.robots_allows is None
            else ("robots allows" if self.robots_allows else "robots DISALLOWS")
        )
        return f"{self.source}: HTTP {code}, {robots} → {self.verdict}" + (
            f" ({self.note})" if self.note else ""
        )
