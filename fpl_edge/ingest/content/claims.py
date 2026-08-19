"""Turning what a creator said into something that can be proved wrong.

A summary is unfalsifiable. "The panel discussed Haaland's fixtures" cannot be
scored, cannot be aggregated, and cannot earn anyone a weight. This module
extracts the only shape that can:

    (creator, player_code, action, gameweek, confidence, rationale,
     source_url, published_at)

The extractor is deterministic and lexical -- no model call. That is a
deliberate constraint rather than a limitation accepted reluctantly. An LLM
extractor cannot be unit-tested against a fixture, cannot run offline, drifts
between provider versions, and would make the creator hit rates unreproducible:
the single number this whole package exists to compute would change when someone
else's model changed. A rule extractor has a lower recall and a knowable one.

How a claim is built
--------------------

1. Text is cut into segments. Show notes split on sentence and block
   boundaries; ASR transcripts have no punctuation at all, so they are cut into
   overlapping fixed windows -- overlap because a cue and its player routinely
   land either side of an arbitrary boundary.
2. Player mentions are found by :mod:`fpl_edge.ingest.content.resolve`.
3. Action cues are matched by phrase. Each cue carries a polarity, and a
   negator within four tokens before it flips that polarity: "I'm not bringing
   in Watkins" is an ``avoid`` claim, and reading it as ``buy`` would be worse
   than extracting nothing.
4. A cue binds to the nearest mention within :data:`BIND_WINDOW_TOKENS`. Beyond
   that the association is a coin flip, and a coin-flip claim pollutes a hit
   rate that is supposed to measure judgement.
5. Confidence comes from hedging versus commitment language, damped by the
   distance between cue and player. It is a property of *how it was said*, not
   of whether we think it is right.

Gameweek attribution is the subtle one. A stated gameweek ("GW12 captain
picks") is used as written. Otherwise the gameweek is inferred as the first one
whose deadline falls strictly after ``published_at`` -- the gameweek the creator
could still act on. Inferred gameweeks are flagged, counted, and reported
separately, because they are a weaker claim than a stated one and the reader is
entitled to know how much of the corpus rests on them.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from fpl_edge.ingest.content.models import Action, Claim, ContentItem
from fpl_edge.ingest.content.resolve import PlayerResolver, ResolutionStats, SeasonResolvers
from fpl_edge.types import GwId

UTC = dt.UTC

#: How far a cue may reach for a player, in word tokens.
BIND_WINDOW_TOKENS = 12

#: Words before a cue that invert it.
NEGATORS: frozenset[str] = frozenset({
    "not", "no", "never", "dont", "don", "wont", "won", "cant", "can",
    "wouldnt", "would", "isnt", "aint", "avoid", "avoiding", "against",
    "instead", "rather", "nobody", "nope",
})

#: Softeners. Presence lowers confidence; they do not invert.
HEDGES: frozenset[str] = frozenset({
    "maybe", "might", "could", "possibly", "perhaps", "leaning", "tempted",
    "considering", "thinking", "probably", "potentially", "unsure", "torn",
    "toss", "coin", "risky", "gamble", "punt", "if", "unless", "depends",
})

#: Commitment markers. Presence raises confidence.
COMMITS: frozenset[str] = frozenset({
    "definitely", "certainly", "absolutely", "must", "essential", "locked",
    "nailed", "confirmed", "obviously", "clearly", "no-brainer", "nobrainer",
    "guaranteed", "will", "am", "doing", "done", "100", "easy", "always",
})

#: Ordered longest-first so "triple captain" wins over "captain".
_CUES: tuple[tuple[str, Action], ...] = (
    ("triple captain", Action.TRIPLE_CAPTAIN),
    ("triple captaining", Action.TRIPLE_CAPTAIN),
    ("tripling", Action.TRIPLE_CAPTAIN),
    ("3xc", Action.TRIPLE_CAPTAIN),
    ("stay away from", Action.AVOID),
    ("steer clear of", Action.AVOID),
    ("getting rid of", Action.SELL),
    ("shipping out", Action.SELL),
    ("moving off", Action.SELL),
    ("transfer out", Action.SELL),
    ("transferring out", Action.SELL),
    ("bringing in", Action.BUY),
    ("bring in", Action.BUY),
    ("transfer in", Action.BUY),
    ("transferring in", Action.BUY),
    ("must have", Action.BUY),
    ("must own", Action.BUY),
    ("buying", Action.BUY),
    ("sticking with", Action.HOLD),
    ("hanging on to", Action.HOLD),
    ("holding on to", Action.HOLD),
    ("on the bench", Action.BENCH),
    ("benching", Action.BENCH),
    ("captaining", Action.CAPTAIN),
    ("captaincy", Action.CAPTAIN),
    ("armband", Action.CAPTAIN),
    ("skipper", Action.CAPTAIN),
    ("captain", Action.CAPTAIN),
    ("selling", Action.SELL),
    ("avoiding", Action.AVOID),
    ("swerve", Action.AVOID),
    ("swerving", Action.AVOID),
    ("fading", Action.AVOID),
    ("avoid", Action.AVOID),
    ("holding", Action.HOLD),
    ("keeping", Action.HOLD),
    ("bench", Action.BENCH),
    ("essential", Action.BUY),
    ("buy", Action.BUY),
    ("sell", Action.SELL),
    ("hold", Action.HOLD),
    ("keep", Action.HOLD),
)

_CUE_RE = re.compile(
    r"\b(" + "|".join(re.escape(phrase) for phrase, _ in _CUES) + r")\b"
)
_CUE_ACTION = dict(_CUES)

_GW_RE = re.compile(r"\b(?:gw|gameweek|game week|week)\s*\.?\s*(\d{1,2})\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[a-z0-9]+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?;:\n])\s+")

#: ASR windowing. 40 words is roughly 15 seconds of speech -- long enough to
#: contain "I'm going Haaland captain this week", short enough that two
#: unrelated recommendations rarely share one window.
WINDOW_WORDS = 40
WINDOW_STRIDE = 20


@dataclass
class ExtractionStats:
    items: int = 0
    segments: int = 0
    cues_found: int = 0
    cues_unbound: int = 0
    claims: int = 0
    claims_gw_stated: int = 0
    claims_gw_inferred: int = 0
    claims_dropped_no_gw: int = 0
    negations_applied: int = 0
    resolution: ResolutionStats = field(default_factory=ResolutionStats)

    def render(self) -> str:
        return (
            f"items={self.items} segments={self.segments} cues={self.cues_found} "
            f"unbound={self.cues_unbound} claims={self.claims} "
            f"(gw stated {self.claims_gw_stated}, inferred {self.claims_gw_inferred}, "
            f"dropped-no-gw {self.claims_dropped_no_gw}) "
            f"negations={self.negations_applied}\n  {self.resolution.render()}"
        )


class GameweekCalendar:
    """Deadlines, used to answer 'which gameweek could this still affect?'.

    Built from ``dim_event`` and therefore covering every season the warehouse
    holds, which is what makes historical backfill possible at all.
    """

    def __init__(self, deadlines: list[tuple[str, int, dt.datetime]]) -> None:
        self._rows = sorted(
            (
                (season, int(gw), d.astimezone(UTC) if d.tzinfo else d.replace(tzinfo=UTC))
                for season, gw, d in deadlines
            ),
            key=lambda r: r[2],
        )

    def next_after(self, moment: dt.datetime) -> tuple[str, GwId] | None:
        """The first gameweek whose deadline is strictly after ``moment``."""
        for season, gw, deadline in self._rows:
            if deadline > moment:
                return season, GwId(gw)
        return None

    def season_of(self, moment: dt.datetime) -> str | None:
        nxt = self.next_after(moment)
        return nxt[0] if nxt else None

    def has(self, season: str, gw: int) -> bool:
        return any(s == season and g == gw for s, g, _ in self._rows)


def segment(text: str, *, is_transcript: bool) -> list[str]:
    if not text.strip():
        return []
    if not is_transcript:
        parts = [p.strip() for p in _SENT_SPLIT_RE.split(text) if p.strip()]
        return [p for p in parts if len(p) > 8]
    words = text.split()
    if len(words) <= WINDOW_WORDS:
        return [" ".join(words)] if words else []
    out: list[str] = []
    for start in range(0, len(words) - WINDOW_WORDS + WINDOW_STRIDE, WINDOW_STRIDE):
        chunk = words[start:start + WINDOW_WORDS]
        if len(chunk) < 8:
            break
        out.append(" ".join(chunk))
    return out


def _confidence(tokens: list[str], cue_idx: int, distance: int) -> float:
    """How firmly it was said, in [0, 1]. Not how likely it is to be right."""
    lo, hi = max(0, cue_idx - 8), min(len(tokens), cue_idx + 9)
    window = tokens[lo:hi]
    hedges = sum(1 for t in window if t in HEDGES)
    commits = sum(1 for t in window if t in COMMITS)
    score = 0.55 + 0.10 * min(commits, 3) - 0.12 * min(hedges, 3)
    score -= 0.02 * min(distance, BIND_WINDOW_TOKENS)
    return round(min(0.95, max(0.05, score)), 3)


def extract_from_item(
    item: ContentItem,
    resolver: PlayerResolver | SeasonResolvers,
    calendar: GameweekCalendar,
    stats: ExtractionStats,
    *,
    title_gw: int | None = None,
) -> list[Claim]:
    """All claims in one item. Order is stable; ids are content-addressed."""
    stats.items += 1
    is_transcript = item.text_source == "transcript"
    segments = segment(item.text, is_transcript=is_transcript)
    stats.segments += len(segments)

    header_gw = title_gw if title_gw is not None else _stated_gw(item.title)
    inferred = calendar.next_after(item.published_at)

    # Resolve against the squad that existed when this was published, not
    # against five seasons of squads at once. See SeasonResolvers.
    if isinstance(resolver, SeasonResolvers):
        resolver = resolver.for_season(inferred[0] if inferred else None)

    claims: list[Claim] = []
    seen: set[tuple[int, str, int]] = set()

    for offset, seg in enumerate(segments):
        # Hyphens become spaces so "must-have" matches the "must have" cue.
        # The replacement is length-preserving, which keeps every character
        # offset -- and therefore token_spans and mention positions -- valid.
        lowered = seg.lower().replace("-", " ")
        tokens = _WORD_RE.findall(lowered)
        if not tokens:
            continue
        # Token index of each character offset, so a regex hit can be located in
        # token space without re-tokenising per cue.
        token_spans = [(m.start(), m.end()) for m in _WORD_RE.finditer(lowered)]

        mentions = resolver.find_mentions(seg, stats.resolution)
        usable = [m for m in mentions if m.code is not None]
        if not usable:
            continue

        seg_gw = _stated_gw(seg)

        # Bind cues to mentions, then keep only the CLOSEST cue per mention.
        #
        # Without that second step, "Haaland is a must-have to avoid early rank
        # losses" yields both a buy and an avoid claim about Haaland from one
        # sentence, and the avoid is simply wrong -- "avoid" there governs "rank
        # losses", not the player. Two contradictory claims from one utterance
        # are worse than one imperfect claim: they cancel in the consensus map
        # and add a coin flip to the hit rate. The nearest cue governs.
        bound: dict[int, tuple[int, Action, int, object]] = {}
        for cue_match in _CUE_RE.finditer(lowered):
            stats.cues_found += 1
            cue_action = _CUE_ACTION[cue_match.group(1)]
            cue_token = _token_index(token_spans, cue_match.start())
            if cue_token is None:
                continue
            nearest, distance = _nearest_mention(usable, token_spans, cue_token)
            if nearest is None or distance > BIND_WINDOW_TOKENS:
                stats.cues_unbound += 1
                continue
            key = int(nearest.code)  # type: ignore[arg-type]
            existing = bound.get(key)
            if existing is None or distance < existing[0]:
                bound[key] = (distance, cue_action, cue_token, nearest)

        for distance, action, cue_token, nearest in sorted(
            bound.values(), key=lambda b: b[2]
        ):
            if _negated(tokens, cue_token):
                flipped = _flip(action)
                if flipped is None:
                    continue
                stats.negations_applied += 1
                action = flipped

            gw_value = seg_gw if seg_gw is not None else header_gw
            gw_inferred = False
            if gw_value is not None and inferred is not None:
                season, _ = inferred
            elif inferred is not None:
                season, gw_from_cal = inferred
                gw_value, gw_inferred = int(gw_from_cal), True
            else:
                stats.claims_dropped_no_gw += 1
                continue
            if not calendar.has(season, gw_value):
                stats.claims_dropped_no_gw += 1
                continue

            code = int(nearest.code)  # type: ignore[arg-type]
            key = (code, str(action), gw_value)
            if key in seen:
                continue
            seen.add(key)

            claims.append(
                Claim(
                    claim_id=Claim.make_id(item.item_id, code, str(action), gw_value, offset),
                    item_id=item.item_id,
                    creator=item.creator,
                    source_key=item.source_key,
                    player_code=nearest.code,  # type: ignore[arg-type]
                    player_name=nearest.matched_name or "",
                    surface_form=nearest.surface,
                    action=action,
                    season=season,
                    gameweek=GwId(gw_value),
                    confidence=_confidence(tokens, cue_token, distance),
                    rationale=seg[:600],
                    source_url=item.url,
                    published_at=item.published_at,
                    gw_inferred=gw_inferred,
                )
            )
            stats.claims += 1
            if gw_inferred:
                stats.claims_gw_inferred += 1
            else:
                stats.claims_gw_stated += 1
    return claims


def _stated_gw(text: str) -> int | None:
    match = _GW_RE.search(text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 1 <= value <= 38 else None


def _token_index(spans: list[tuple[int, int]], char_pos: int) -> int | None:
    for idx, (start, end) in enumerate(spans):
        if start <= char_pos < end:
            return idx
        if start > char_pos:
            return idx
    return None


def _nearest_mention(mentions, spans, cue_token):  # type: ignore[no-untyped-def]
    best = None
    best_distance = 10**6
    for mention in mentions:
        idx = _token_index(spans, mention.start)
        if idx is None:
            continue
        distance = abs(idx - cue_token)
        if distance < best_distance:
            best, best_distance = mention, distance
    return best, best_distance


def _negated(tokens: list[str], cue_token: int) -> bool:
    lo = max(0, cue_token - 4)
    return any(t in NEGATORS for t in tokens[lo:cue_token])


def _flip(action: Action) -> Action | None:
    return {
        Action.BUY: Action.AVOID,
        Action.CAPTAIN: Action.AVOID,
        Action.TRIPLE_CAPTAIN: Action.AVOID,
        Action.HOLD: Action.SELL,
        Action.SELL: Action.HOLD,
        Action.AVOID: Action.BUY,
        Action.BENCH: Action.BUY,
    }.get(action)
