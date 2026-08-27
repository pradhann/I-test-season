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

Two backends, in preference order:

1. **Claude Code CLI** (``claude -p``) — runs on the user's Max subscription,
   no metered API tokens. This is the default: the bot lives on the same
   machine as the user's Claude Code login, and a subscription the user
   already pays for beats per-token billing for a nightly content job.
2. **Anthropic SDK** — only if ``ANTHROPIC_API_KEY`` is explicitly set and the
   CLI is unusable.

Absent both, callers fall back to the cue extractor and must say so rather
than fake it.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from fpl_edge.config import secret

MODEL = "claude-opus-5"

#: A bare Anthropic model id: ``claude-`` followed by hyphen-separated
#: lowercase alphanumeric segments (``claude-opus-5``, ``claude-sonnet-4-6``,
#: ``claude-opus-4-5-20251101``). Nothing else.
_MODEL_ID_RE = re.compile(r"^claude-[a-z0-9]+(?:-[a-z0-9]+)*$")

#: Conviction bands written into content_claim.confidence. The scoreboard
#: tests these as calibration targets per creator.
CONVICTION_CONF = {"high": 0.8, "medium": 0.6, "low": 0.4}


class AnalysisUnavailable(RuntimeError):
    """No usable backend; the caller must fall back and say so."""


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


def _find_claude_cli() -> str | None:
    """The working Claude Code binary, if any.

    The nvm shim on this machine runs Claude Code under node 18, which it
    cannot (needs >= 20) -- resolve the native install explicitly rather than
    trusting PATH order.
    """
    import shutil
    from pathlib import Path

    native = Path.home() / ".local/bin/claude"
    if native.exists():
        return str(native)
    return shutil.which("claude")


def _analyze_via_cli(cli: str, *, title: str, creator: str, body: str,
                     timeout_s: int = 600) -> TranscriptAnalysis:
    """Structured analysis through headless Claude Code -- the Max plan.

    ``claude -p`` cannot run nested inside a Claude Code session, so the
    guard env vars are scrubbed; under launchd (the bot, the nightly job)
    they are absent anyway. The prompt travels on stdin: a 25k-token
    transcript does not belong in argv.
    """
    import json as _json
    import os
    import subprocess

    schema = _json.dumps(TranscriptAnalysis.model_json_schema())
    prompt = (
        f"{_SYSTEM}\n\n"
        f"Reply with ONLY a JSON object matching this JSON Schema -- no prose, "
        f"no code fences:\n{schema}\n\n"
        f"Creator: {creator}\nTitle: {title}\n\nTranscript:\n{body}"
    )
    env = {k: v for k, v in os.environ.items()
           if k not in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")}
    proc = subprocess.run(
        [cli, "-p", "--output-format", "json"],
        input=prompt, capture_output=True, text=True,
        timeout=timeout_s, env=env,
    )
    if proc.returncode != 0:
        raise AnalysisUnavailable(
            f"claude CLI exited {proc.returncode}: {proc.stderr.strip()[:200]}"
        )
    envelope = _json.loads(proc.stdout)
    if envelope.get("is_error") or "revoked" in str(envelope.get("result", "")):
        raise AnalysisUnavailable(
            "The Claude Code CLI login is revoked or errored. Run "
            "`claude login` once in Terminal (uses your Max plan), then retry. "
            f"Detail: {str(envelope.get('result'))[:160]}"
        )
    raw = str(envelope.get("result", "")).strip()
    if raw.startswith("```"):
        raw = raw.strip("`\n")
        raw = raw[raw.find("{"):]
    raw = raw[raw.find("{"): raw.rfind("}") + 1]
    return TranscriptAnalysis.model_validate_json(raw)


def analyze_transcript(
    *,
    title: str,
    creator: str,
    text: str,
    client: object | None = None,
) -> TranscriptAnalysis:
    """One structured read of a transcript. Deterministic schema, quoted evidence."""
    # A 2h podcast transcript is ~25k tokens; well inside the window. Never
    # truncate silently -- cap generously and say so if we ever have to.
    body = text
    if len(body) > 400_000:
        body = body[:400_000]

    if client is None:
        cli = _find_claude_cli()
        cli_error: str | None = None
        if cli:
            try:
                return _analyze_via_cli(cli, title=title, creator=creator, body=body)
            except AnalysisUnavailable as exc:
                cli_error = str(exc)
        if not secret("ANTHROPIC_API_KEY", required=False):
            raise AnalysisUnavailable(
                (cli_error or "No Claude Code CLI found.")
                + " No ANTHROPIC_API_KEY fallback is configured either."
            )
        import anthropic

        client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY"))

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


def validate_model_id(model: str) -> str:
    """The model id, or a loud failure. Never a silently accepted label.

    ``content_analysis`` is keyed on ``(item_id, model)`` precisely so that a
    re-read with a newer model does not overwrite what an older one said. That
    only works if ``model`` names a MODEL. A row once landed stamped
    ``max-plan:claude-fable-5-session`` -- a description of the plumbing, not a
    model id -- and it took the primary key with it: the later real run keyed
    differently and did not dedupe against it.

    So the column is validated at the only door that writes it. A backend
    label, a session id, an empty string or a plan name is a programming
    error, and it fails here rather than becoming a permanent second row that
    nothing can join to.
    """
    if not isinstance(model, str) or not _MODEL_ID_RE.match(model):
        raise ValueError(
            f"content_analysis.model must be a bare Anthropic model id such as "
            f"{MODEL!r}, not {model!r}. Backends, plans and session ids do not "
            f"belong in this column -- it is half the primary key, and a "
            f"non-model value silently defeats deduplication."
        )
    return model


def store_analysis(wh, item_id: str, analysis: TranscriptAnalysis,
                   *, model: str = MODEL) -> None:
    wh.sql(
        "INSERT OR REPLACE INTO content_analysis VALUES (?, ?, ?, ?)",
        [item_id, validate_model_id(model), dt.datetime.now(dt.timezone.utc),
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


#: Analysis stances onto the CLOSED Action set the scoreboard can settle.
#: "watch" is deliberately unmapped: a watch is not a scoreable position, and
#: pushing it into the scoreboard would dilute the creator's record with
#: non-calls. Watches stay in content_analysis, visible but unscored.
_STANCE_TO_ACTION = {
    "buy": "buy", "sell": "sell", "hold": "hold", "captain": "captain",
    "avoid": "avoid", "bench": "bench",
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

    # Accept either a bare PlayerResolver or the SeasonResolvers wrapper,
    # exactly as the cue extractor does -- season scoping recovers surnames
    # that are only ambiguous across seasons.
    if hasattr(resolver, "for_season"):
        resolver = resolver.for_season(season)

    calls = (list(analysis.transfers_in) + list(analysis.transfers_out)
             + list(analysis.captaincy) + list(analysis.differentials))
    claims, dropped = [], []
    seen = set()
    for offset, call in enumerate(calls):
        if call.stance not in _STANCE_TO_ACTION:
            continue  # e.g. "watch": kept in the analysis, not scoreable
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
