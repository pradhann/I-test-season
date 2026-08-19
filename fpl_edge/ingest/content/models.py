"""The two records this package produces, and the one field that matters most.

``published_at`` is not metadata. It is the whole reason this data is safe to
put in a model.

A creator saying "Haaland is the captain" is worth something only if we can
prove they said it *before* the deadline it applies to. Content is the easiest
place in an FPL pipeline to leak the future: podcast archives are full of
episodes recorded after the matches, blogs republish with updated timestamps,
and a "GW12 captain picks" episode published on the Monday after GW12 is a
description of what happened wearing the costume of a prediction. Backfilling a
few hundred of those and measuring a 70% hit rate would produce a beautiful,
entirely fictitious creator weighting.

So ``published_at`` is required, tz-aware UTC, carried on both records, and is
the column every read filters on. :class:`ContentItem` refuses to be built
without it. There is no "unknown date" path -- an item whose date cannot be
parsed is dropped and counted, because a claim with an unknown timestamp is
indistinguishable from a claim made after the fact.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
from dataclasses import dataclass

from fpl_edge.types import GwId, PlayerCode

UTC = dt.UTC


class Action(enum.StrEnum):
    """What a creator is telling you to do. Closed set, on purpose.

    A free-text action cannot be scored, and a claim that cannot be scored
    cannot earn a creator any weight.
    """

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"
    CAPTAIN = "captain"
    TRIPLE_CAPTAIN = "triple_captain"
    BENCH = "bench"
    AVOID = "avoid"

    @property
    def is_positive(self) -> bool:
        """True when the creator wants the player *in* and scoring.

        Determines which side of the benchmark counts as a hit. ``sell``,
        ``bench`` and ``avoid`` are correct when the player underperforms, so
        scoring them with the same comparison as ``buy`` would credit a creator
        for being wrong.
        """
        return self in (Action.BUY, Action.HOLD, Action.CAPTAIN, Action.TRIPLE_CAPTAIN)


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ContentItem:
    """One video, episode or article, with its text and its publication instant."""

    item_id: str
    source_key: str
    creator: str
    kind: str
    title: str
    url: str
    published_at: dt.datetime
    text: str
    #: Instant we retrieved it. Distinct from ``published_at``: a 2024 episode
    #: fetched today was still only knowable from its own publication date.
    fetched_at: dt.datetime
    text_source: str = "description"  # description | transcript | article

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "published_at", _require_utc(self.published_at, "published_at")
        )
        object.__setattr__(self, "fetched_at", _require_utc(self.fetched_at, "fetched_at"))

    @staticmethod
    def make_id(source_key: str, url: str) -> str:
        return hashlib.sha256(f"{source_key}|{url}".encode()).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class Claim:
    """A single extracted recommendation, keyed to a stable player code.

    ``player_code`` is :class:`~fpl_edge.types.PlayerCode`, never ``element_id``.
    A creator's 2023-24 claim about Declan Rice and their 2025-26 claim about him
    have to be the same player for a track record to mean anything, and element
    ids are reassigned every summer.
    """

    claim_id: str
    item_id: str
    creator: str
    source_key: str
    player_code: PlayerCode
    player_name: str  # as matched, for audit
    surface_form: str  # as written by the creator, verbatim
    action: Action
    season: str
    gameweek: GwId
    confidence: float
    rationale: str
    source_url: str
    published_at: dt.datetime
    #: Set when the gameweek was inferred from ``published_at`` rather than
    #: stated in the text. Scoring keeps these, but they are reported separately
    #: because an inferred gameweek is a weaker claim than a stated one.
    gw_inferred: bool = False
    #: 'cue' (keyword-window heuristics) or 'llm:<model>' (semantic read with
    #: conviction-band confidence). Scored as separate channels.
    extractor: str = "cue"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "published_at", _require_utc(self.published_at, "published_at")
        )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence out of range: {self.confidence!r}")

    @staticmethod
    def make_id(item_id: str, player_code: int, action: str, gw: int, offset: int) -> str:
        raw = f"{item_id}|{player_code}|{action}|{gw}|{offset}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]
