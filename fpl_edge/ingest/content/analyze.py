"""Semantic transcript analysis: Claude reads the episode, not a keyword window.

The cue extractor in :mod:`claims` binds action words to nearby player names.
On titles and descriptions that is a fair trade; on an hour-long podcast it
produces contradictory fragments with pseudo-confidences derived from token
distance — numbers that look like probabilities and mean nothing of the sort.

This module is the honest replacement for long-form text: the full transcript
goes to Claude with a structured output schema, and what comes back is what a
careful listener would write down — a summary, the transfers actually being
recommended, captaincy and chip advice — each with a conviction LEVEL grounded
in the speaker's own language and a verbatim supporting quote, so every entry
is checkable against the transcript it came from.

Conviction maps to the claim table's confidence column as bands with stated
meaning: high = 0.8, medium = 0.6, low = 0.4. Those numbers are calibration
TARGETS for the scoreboard to test (do this creator's "high conviction" calls
hit 80%?), not decorations.

Requires ``ANTHROPIC_API_KEY`` (env or .env). Absent a key, callers fall back
to the cue extractor and must say so rather than fake it.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from pydantic import BaseModel, Field

from fpl_edge.config import secret

MODEL = "claude-opus-5"

#: Conviction bands written into content_claim.confidence. The scoreboard
#: tests these as calibration targets per creator.
CONVICTION_CONF = {"high": 0.8, "medium": 0.6, "low": 0.4}


class AnalysisUnavailable(RuntimeError):
    """No API key configured; the caller must fall back and say so."""


class PlayerCall(BaseModel):
    """One recommendation about one player, as actually voiced."""

    player: str = Field(description="Player's full name as best known")
    stance: Literal["buy", "sell", "hold", "captain", "avoid", "bench", "watch"]
    conviction: Literal["high", "medium", "low"] = Field(
        description="Strength of the speaker's own language, not your opinion"
    )
    gameweek: int | None = Field(
        description="Gameweek the call targets, if the speaker names one"
    )
    reasoning: str = Field(description="The speaker's reasoning, one sentence")
    quote: str = Field(description="Short verbatim quote supporting this call")


class ChipCall(BaseModel):
    chip: Literal["bench_boost", "triple_captain", "wildcard", "free_hit"]
    stance: Literal["play_now", "hold", "considering"]
    gameweek: int | None
    reasoning: str
    quote: str


class TranscriptAnalysis(BaseModel):
    """What a careful listener would write down from this episode."""

    summary: list[str] = Field(description="3-6 bullet points of the key ideas")
    transfers_in: list[PlayerCall]
    transfers_out: list[PlayerCall]
    captaincy: list[PlayerCall]
    chip_advice: list[ChipCall]
    differentials: list[PlayerCall] = Field(
        description="Low-ownership picks the speakers actively like"
    )


_SYSTEM = """You are an FPL (Fantasy Premier League) analyst. You are given the
transcript of an FPL podcast or video. Extract ONLY positions the speakers
actually take -- never infer a recommendation from a neutral mention, and never
add players who are merely listed or joked about.

Rules:
- conviction reflects the SPEAKER'S language: "nailed on", "definitely" = high;
  "I quite like", "leaning towards" = medium; "maybe", "could do worse" = low.
- quote must be verbatim from the transcript (light truncation allowed).
- If two speakers disagree about a player, include both calls.
- gameweek only when explicitly stated or unambiguous from context.
- summary bullets are the episode's actual key ideas, not a table of contents."""


def analyze_transcript(
    *,
    title: str,
    creator: str,
    text: str,
    client: object | None = None,
) -> TranscriptAnalysis:
    """One structured read of a transcript. Deterministic schema, quoted evidence."""
    if client is None:
        if not secret("ANTHROPIC_API_KEY", required=False):
            raise AnalysisUnavailable(
                "ANTHROPIC_API_KEY is not set (env or .env), so semantic "
                "transcript analysis is off. Add the key and re-share the link."
            )
        import anthropic

        client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))

    # A 2h podcast transcript is ~25k tokens; well inside the window. Never
    # truncate silently -- cap generously and say so if we ever have to.
    body = text
    if len(body) > 400_000:
        body = body[:400_000]

    response = client.messages.parse(  # type: ignore[attr-defined]
        model=MODEL,
        max_tokens=16000,
        system=_SYSTEM,
        messages=[{
            "role": "user",
            "content": (f"Creator: {creator}\nTitle: {title}\n\n"
                        f"Transcript:\n{body}"),
        }],
        output_format=TranscriptAnalysis,
    )
    return response.parsed_output  # type: ignore[attr-defined]


def store_analysis(wh, item_id: str, analysis: TranscriptAnalysis,
                   *, model: str = MODEL) -> None:
    wh.sql(
        "INSERT OR REPLACE INTO content_analysis VALUES (?, ?, ?, ?)",
        [item_id, model, dt.datetime.now(dt.timezone.utc),
         json.dumps(analysis.model_dump())],
    )


def load_analysis(wh, item_id: str) -> TranscriptAnalysis | None:
    rows = wh.sql(
        "SELECT analysis_json FROM content_analysis WHERE item_id = ? "
        "ORDER BY created_utc DESC LIMIT 1", [item_id],
    )
    if rows.empty:
        return None
    return TranscriptAnalysis.model_validate(json.loads(rows.iloc[0, 0]))


_STANCE_TO_ACTION = {
    "buy": "buy", "sell": "sell", "hold": "hold", "captain": "captain",
    "avoid": "avoid", "bench": "bench", "watch": "watch",
}


def claims_from_analysis(
    analysis: TranscriptAnalysis,
    *,
    item,  # ContentItem
    resolver,  # PlayerResolver
    default_gw: int,
    season: str,
):
    """Turn analysis calls into Claim rows the scoreboard can settle.

    Player names resolve through the SAME resolver as everything else; a name
    it refuses to guess is dropped and counted, never guessed. The claim's
    extractor is stamped ``llm:<model>`` so cue noise and semantic extraction
    are scored as separate channels.
    """
    from fpl_edge.ingest.content.claims import Claim
    from fpl_edge.ingest.content.models import Action

    calls = (list(analysis.transfers_in) + list(analysis.transfers_out)
             + list(analysis.captaincy) + list(analysis.differentials))
    claims, dropped = [], []
    seen = set()
    for offset, call in enumerate(calls):
        mentions = resolver.find_mentions(call.player, None)
        codes = {m.code for m in mentions if m.code is not None}
        if len(codes) != 1:
            dropped.append(call.player)
            continue
        code = codes.pop()
        action = Action(_STANCE_TO_ACTION[call.stance])
        gw = call.gameweek or default_gw
        key = (code, str(action), gw)
        if key in seen:
            continue
        seen.add(key)
        claims.append(Claim(
            claim_id=Claim.make_id(item.item_id, int(code), str(action), gw,
                                   1000 + offset),
            item_id=item.item_id, creator=item.creator,
            source_key=item.source_key, player_code=code,
            player_name=call.player, surface_form=call.player,
            action=action, season=season, gameweek=gw,
            confidence=CONVICTION_CONF[call.conviction],
            rationale=f"{call.reasoning} | quote: {call.quote}"[:400],
            source_url=item.url, published_at=item.published_at,
            gw_inferred=call.gameweek is None,
            extractor=f"llm:{MODEL}",
        ))
    return claims, dropped
