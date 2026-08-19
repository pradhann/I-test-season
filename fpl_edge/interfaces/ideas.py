"""The Idea: a thought the user had, turned into a falsifiable claim.

The unit of value here is not the text message. It is the *thesis* -- a sentence
that a later gameweek can prove wrong. "I like Rashford" cannot be wrong. "Marcus
Rashford outscores the median MID priced within £0.5m of him over GW1-GW6" can,
and the moment it is written down the user has a track record whether they want
one or not.

Three properties fall out of that and are enforced here rather than by
convention:

* **A thesis names its comparator.** Beating "the median captain choice" is a
  measurable event; "being good" is not. :class:`Comparator` is the closed set of
  yardsticks the tracker knows how to resolve, so an idea cannot be written down
  in a form nothing can score.
* **A thesis names its window.** ``gw`` and ``horizon_gws`` fix when the claim is
  settled, which stops the retrospective drift where a bad call becomes "a
  long-term hold".
* **The record is immutable except by resolution.** Ideas are never edited to
  match what happened. Being wrong on the record is the product.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
from dataclasses import dataclass, field

from fpl_edge.types import GwId, PlayerCode, Season

UTC = dt.timezone.utc


class IdeaKind(enum.StrEnum):
    """What the user was actually proposing.

    The kind picks the default comparator and horizon, because those are what
    make the claim falsifiable and the user is never going to type them.
    """

    CAPTAIN = "captain"
    TRANSFER_IN = "transfer_in"
    FADE = "fade"
    DIFFERENTIAL = "differential"
    COMPARE = "compare"
    WATCH = "watch"


class Comparator(enum.StrEnum):
    """The yardstick a thesis is measured against.

    Every member here must have a resolver in :mod:`fpl_edge.interfaces.tracking`.
    Adding one without a resolver produces ideas that can never be settled, which
    is the failure mode this enum exists to prevent.
    """

    #: Median points among the players the field was most likely to captain,
    #: with the candidate set frozen at submission time.
    MEDIAN_CAPTAIN = "median_captain"
    #: Median points among same-position players within a price band of the
    #: subject, again frozen at submission.
    PRICE_PEER_MEDIAN = "price_peer_median"
    #: A specific rival player the user named ("Salah or Palmer?").
    NAMED_PLAYER = "named_player"
    #: Median points among everyone who took the field.
    FIELD_MEDIAN = "field_median"


class Stance(enum.StrEnum):
    AGREE = "agree"
    DISAGREE = "disagree"
    NEUTRAL = "neutral"


class Outcome(enum.StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PUSH = "push"


class IdeaStatus(enum.StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    #: Unresolvable through no fault of the user -- e.g. the player left the
    #: league mid-horizon. Voided ideas are excluded from hit rates but kept, so
    #: the bias probes still see that the thought occurred.
    VOID = "void"


#: How long each kind of claim runs for, in gameweeks. A captaincy call is a
#: one-week bet; "I like X" is a hold and judging it on a single blank would be
#: a strawman of what the user meant.
DEFAULT_HORIZON: dict[IdeaKind, int] = {
    IdeaKind.CAPTAIN: 1,
    IdeaKind.COMPARE: 1,
    IdeaKind.TRANSFER_IN: 6,
    IdeaKind.FADE: 6,
    IdeaKind.DIFFERENTIAL: 6,
    IdeaKind.WATCH: 6,
}

DEFAULT_COMPARATOR: dict[IdeaKind, Comparator] = {
    IdeaKind.CAPTAIN: Comparator.MEDIAN_CAPTAIN,
    IdeaKind.COMPARE: Comparator.NAMED_PLAYER,
    IdeaKind.TRANSFER_IN: Comparator.PRICE_PEER_MEDIAN,
    IdeaKind.FADE: Comparator.PRICE_PEER_MEDIAN,
    IdeaKind.DIFFERENTIAL: Comparator.PRICE_PEER_MEDIAN,
    IdeaKind.WATCH: Comparator.PRICE_PEER_MEDIAN,
}

#: Price band, in tenths of a million, defining "a player of this class".
PRICE_PEER_BAND_TENTHS = 5

#: How many of the most-owned players stand in for "the captain choices". The
#: real quantity is captaincy share, which the ownership model will supply; until
#: then ownership rank is the honest proxy and is recorded as such.
CAPTAIN_POOL_SIZE = 10


def make_idea_id(created_utc: dt.datetime, source: str, source_ref: str | None, raw_text: str) -> str:
    """A time-sortable, content-derived identifier.

    Sortable so that ``ORDER BY idea_id`` is chronological without a join;
    content-derived so that the same message replayed from the same Telegram
    update produces the same id and the insert is idempotent. Telegram *will*
    redeliver an update if the process dies between handling and acknowledging
    the offset, and a duplicate idea would quietly corrupt the hit rate.
    """
    digest = hashlib.sha256(
        "\x00".join(
            [created_utc.astimezone(UTC).isoformat(), source, source_ref or "", raw_text]
        ).encode("utf-8")
    ).hexdigest()[:10]
    return f"idea_{created_utc.astimezone(UTC):%Y%m%dT%H%M%S}_{digest}"


@dataclass(frozen=True, slots=True)
class Idea:
    """A user hypothesis, in the form a tracker can settle."""

    idea_id: str
    created_utc: dt.datetime
    as_of: dt.datetime
    source: str
    raw_text: str
    season: Season
    kind: IdeaKind
    subject_code: PlayerCode | None
    subject_name: str | None
    comparator: Comparator
    comparator_code: PlayerCode | None
    comparator_label: str
    gw: GwId
    horizon_gws: int
    thesis: str
    resolution_rule: str
    parse_confidence: float
    status: IdeaStatus = IdeaStatus.OPEN
    acted: bool = False
    source_ref: str | None = None
    acted_utc: dt.datetime | None = None
    resolved_utc: dt.datetime | None = None
    outcome: Outcome | None = None
    subject_points: float | None = None
    comparator_points: float | None = None

    def __post_init__(self) -> None:
        for label, ts in (("created_utc", self.created_utc), ("as_of", self.as_of)):
            if ts.tzinfo is None:
                raise ValueError(f"Idea.{label} must be tz-aware UTC, got naive {ts!r}")
        if self.horizon_gws < 1:
            raise ValueError("horizon_gws must be >= 1")
        if self.comparator is Comparator.NAMED_PLAYER and self.comparator_code is None:
            raise ValueError("a named_player comparator needs comparator_code")

    @property
    def last_gw(self) -> int:
        """The final gameweek the claim covers, inclusive."""
        return int(self.gw) + self.horizon_gws - 1

    @property
    def gw_range(self) -> range:
        return range(int(self.gw), self.last_gw + 1)

    @property
    def window_label(self) -> str:
        return f"GW{self.gw}" if self.horizon_gws == 1 else f"GW{self.gw}-GW{self.last_gw}"


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the engine thought, at the moment the user thought it.

    ``p_thesis_true`` is the probability that this exact written thesis resolves
    :attr:`Outcome.CORRECT`. It is deliberately not "a rating out of 10": the
    thesis has a resolver, so the number is checkable against realised outcomes
    and the engine's own calibration becomes measurable alongside the user's.
    """

    idea_id: str
    issued_utc: dt.datetime
    provider: str
    provider_version: str
    stance: Stance
    p_thesis_true: float
    confidence: str
    rationale: str
    degraded: bool = False
    latency_ms: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.p_thesis_true <= 1.0:
            raise ValueError(f"p_thesis_true out of range: {self.p_thesis_true}")
        if self.confidence not in ("low", "medium", "high"):
            raise ValueError(f"confidence must be low/medium/high, got {self.confidence!r}")

    @property
    def verdict_id(self) -> str:
        return f"{self.idea_id}::{self.provider}@{self.issued_utc:%Y%m%dT%H%M%S%f}"

    def headline(self) -> str:
        arrow = {Stance.AGREE: "backs", Stance.DISAGREE: "fades", Stance.NEUTRAL: "is neutral on"}
        return f"model {arrow[self.stance]} this — P(thesis true) = {self.p_thesis_true:.0%}"


@dataclass(frozen=True, slots=True)
class IdeaContext:
    """Subject features and population base rates, frozen at submission.

    Both halves are needed. The subject's form percentile alone says nothing --
    the bias question is whether the user picks high-form players *more often
    than the population would produce by chance*, and the population moves week
    to week (mid-gameweek, most players' last match was at home simply because
    of the fixture order). Capturing the base rate at the same instant is what
    turns the review from an assertion into a test.
    """

    idea_id: str
    captured_utc: dt.datetime
    position: int | None = None
    team_code: int | None = None
    price_tenths: int | None = None
    selected_by_pct: float | None = None
    availability: str | None = None
    form_points_last3: float | None = None
    form_percentile: float | None = None
    minutes_last3: int | None = None
    last_match_gw: int | None = None
    last_match_points: int | None = None
    last_match_was_home: bool | None = None
    gws_since_haul: int | None = None
    season_ppg: float | None = None
    pop_home_rate: float | None = None
    pop_mean_gws_since_haul: float | None = None
    pop_recent_haul_rate: float | None = None
    pop_mean_form_pct: float | None = None
    pop_n: int = 0
    supported_team_code: int | None = None
    is_supported_club: bool | None = None
    pop_supported_club_rate: float | None = None
    pop_universe_n: int = 0


@dataclass(frozen=True, slots=True)
class CandidateMatch:
    """One player the parser thinks the user might have meant."""

    code: PlayerCode
    label: str
    hint: str
    score: float


@dataclass(frozen=True, slots=True)
class Clarification:
    """The parser refusing to guess.

    Returned instead of an Idea when two players fit the text about equally well.
    Guessing here is worse than asking: a mis-resolved subject silently pollutes
    both the hit rate and the bias analysis, and the user cannot see it happened.
    """

    raw_text: str
    question: str
    candidates: tuple[CandidateMatch, ...]
    pending_id: str
    source: str = "cli"
    source_ref: str | None = None
    #: Why we are asking. These need different answers, and collapsing them is
    #: how "Hi" becomes a shortlist of defenders whose names rhyme with it.
    #:   ambiguous   -- two players fit; pick one
    #:   not_found   -- clearly about a player, but not one we can identify
    #:   not_an_idea -- chatter. Not a failed idea; not an idea at all.
    #:   no_universe -- the warehouse has no player list for this instant
    kind: str = "ambiguous"

    @property
    def is_idea_attempt(self) -> bool:
        """False for chatter. Chatter must not be stored as a pending question."""
        return self.kind in ("ambiguous", "not_found")

    def render(self) -> str:
        lines = [self.question]
        for i, c in enumerate(self.candidates, start=1):
            lines.append(f"  {i}. {c.label} ({c.hint})")
        if self.candidates:
            lines.append("Reply with a number, or the full name.")
        return "\n".join(lines)


def build_thesis(
    *,
    kind: IdeaKind,
    subject_name: str,
    comparator: Comparator,
    comparator_label: str,
    gw: GwId,
    horizon_gws: int,
) -> tuple[str, str]:
    """Turn a resolved intent into (thesis, resolution_rule).

    Both strings are stored. The thesis is what gets read back to the user; the
    resolution rule is the contract with the tracker, written in English so that
    a disagreement about whether an idea "came off" is settled by reading the row
    rather than by re-litigating it.
    """
    window = f"GW{gw}" if horizon_gws == 1 else f"GW{gw}-GW{int(gw) + horizon_gws - 1}"
    total = "points" if horizon_gws == 1 else "total points"

    if kind is IdeaKind.FADE:
        thesis = f"{subject_name} scores FEWER {total} than {comparator_label} over {window}."
        rule = (
            f"Correct if {subject_name}'s FPL {total} across {window} is strictly less than "
            f"{comparator_label}'s; incorrect if strictly greater; push if equal. "
            "The comparator set is frozen at submission time and does not chase the outcome."
        )
        return thesis, rule

    verb = "outscores"
    if kind is IdeaKind.CAPTAIN:
        thesis = f"{subject_name} {verb} {comparator_label} in {window}."
    else:
        thesis = f"{subject_name} {verb} {comparator_label} over {window}."
    rule = (
        f"Correct if {subject_name}'s FPL {total} across {window} strictly exceeds "
        f"{comparator_label}'s; incorrect if strictly less; push if equal. "
        "The comparator set is frozen at submission time and does not chase the outcome."
    )
    return thesis, rule


@dataclass(frozen=True, slots=True)
class IdeaRecord:
    """An idea joined to its verdict and, once settled, its result.

    Convenience for the review surfaces; the registry is still the source of
    truth. ``verdict`` is optional because the idea row is written *before* the
    verdict is computed -- if the model dies the thought is still on the record.
    """

    idea: Idea
    verdict: Verdict | None = None
    context: IdeaContext | None = None
    observations: tuple[tuple[int, float | None, float | None], ...] = field(default_factory=tuple)

    @property
    def margin(self) -> float | None:
        """Subject minus comparator, signed so positive always means the user was right."""
        if self.idea.subject_points is None or self.idea.comparator_points is None:
            return None
        diff = self.idea.subject_points - self.idea.comparator_points
        return -diff if self.idea.kind is IdeaKind.FADE else diff
