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


#: What a stored analysis actually rests on. ``content_item.text_source`` is
#: the only honest answer to "how much did the model get to read?", so it is
#: mapped to a depth and travels WITH the analysis rather than being left for a
#: reader to re-derive by joining back to the item.
#:
#: 354 podcast rows and 97 YouTube rows in this warehouse are ``description``:
#: a headline, two lines of blurb, then sponsor copy, affiliate links and
#: league codes. A summary squeezed out of 1.2KB of marketing copy is a
#: different artefact from one distilled from an hour of speech, and the
#: Creators tab shows them side by side. So the difference is recorded, not
#: implied.
DEPTH_BY_TEXT_SOURCE = {
    "transcript": "transcript",
    "article": "article",
    "description": "notes",
}

#: Depths whose analyses are NOT considered takes for scoreboard purposes.
#: See :func:`is_scoreable`.
THIN_DEPTHS = frozenset({"notes"})

#: Below this much prose (after links and promo furniture are discounted)
#: there is nothing to analyse and we do not spend a model call finding that
#: out. Measured on the sample: a stripped FPL show-note blurb that still says
#: something runs 300-1500 chars; pure link-farm notes strip to under 200.
MIN_SUBSTANTIVE_CHARS = 240

_URL_RE = re.compile(r"https?://\S+|\bwww\.\S+")
#: Show-note furniture: separator bars, bare emoji bullets, social handles.
_FURNITURE_RE = re.compile(r"[\u2500-\u257f\u2580-\u259f_]{3,}")
_WS_RE = re.compile(r"\s+")


def substantive_text(text: str | None) -> str:
    """The prose left once links and separator furniture are removed.

    Used ONLY to decide whether an item is worth a model call. The model
    itself still receives the untouched text, so every quote it returns is
    verbatim against what was actually published.
    """
    if not text:
        return ""
    stripped = _FURNITURE_RE.sub(" ", _URL_RE.sub(" ", text))
    return _WS_RE.sub(" ", stripped).strip()


def depth_for(text_source: str | None) -> str:
    """``transcript`` | ``article`` | ``notes`` | ``unknown``."""
    return DEPTH_BY_TEXT_SOURCE.get(str(text_source or ""), "unknown")


def is_thin(text_source: str | None) -> bool:
    """True when the source is show notes rather than the thing itself."""
    return depth_for(text_source) in THIN_DEPTHS


def is_scoreable(text_source: str | None) -> bool:
    """May calls from this source become ``content_claim`` rows?

    No, for show notes. The conviction bands are calibration TARGETS -- a
    creator's "high conviction" calls are meant to be testable at 80%. A
    conviction level read off promotional copy is not the speaker's language
    about a player, it is a headline, and feeding it into the same bands would
    quietly decalibrate the channel that exists to be calibrated. Show-note
    analyses are still stored and still shown; they just do not vote.

    The cue extractor already covers descriptions, and ``content_claim.
    extractor`` keeps the two channels distinguishable, exactly as the panel
    contract requires.
    """
    return not is_thin(text_source)


def analysis_is_empty(analysis: TranscriptAnalysis) -> bool:
    """True when the model found nothing: no summary and no calls.

    That is a legitimate answer for a page of sponsor copy, and it must be
    recorded as "we looked and there was nothing" rather than stored as a
    take. Nothing is invented to fill the gap.
    """
    return not (
        analysis.summary or analysis.transfers_in or analysis.transfers_out
        or analysis.captaincy or analysis.chip_advice or analysis.differentials
    )


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


#: Prepended when the text is show notes rather than the episode. Without it
#: the model is being handed sponsor copy under a heading that says
#: "Transcript:", and a cooperative model will manufacture a take out of the
#: title. The correct output for a page of affiliate links is empty lists, and
#: it has to be told so explicitly.
_NOTES_PREAMBLE = """IMPORTANT -- THIS IS NOT A TRANSCRIPT.

What follows is the DESCRIPTION published alongside the episode: a headline, at
most a few lines of blurb, then sponsor copy, affiliate links, league codes,
membership pitches and chapter markers. Nobody speaks in it.

Therefore:
- Extract a position ONLY where the notes themselves state one in words. A
  title such as "MY GW2 TEAM" or "Sell Salah?" names a TOPIC, not a stance;
  never convert a headline or a question into a call.
- Sponsor copy, tool plugs, league codes and chapter lists are not opinions.
- Empty lists are the CORRECT and expected answer here. Returning nothing is
  a real result; inventing a take from marketing copy is not.
- summary should describe only what the notes actually say the episode covers.
"""


def _user_prompt(*, title: str, creator: str, body: str,
                 text_source: str = "transcript") -> str:
    """The user turn, labelled with what the text actually is."""
    label = {"transcript": "Transcript", "article": "Article",
             "description": "Episode description (show notes)"}.get(
                 str(text_source or ""), "Text")
    head = _NOTES_PREAMBLE + "\n" if is_thin(text_source) else ""
    return (f"{head}Creator: {creator}\nTitle: {title}\n\n{label}:\n{body}")


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
                     text_source: str = "transcript",
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
        + _user_prompt(title=title, creator=creator, body=body,
                       text_source=text_source)
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
    text_source: str = "transcript",
    client: object | None = None,
) -> TranscriptAnalysis:
    """One structured read of ONE item. Deterministic schema, quoted evidence.

    ``text_source`` is the item's own ``content_item.text_source``. It changes
    the prompt (see :data:`_NOTES_PREAMBLE`) because reading show notes and
    reading a transcript are different jobs, and it is recorded alongside the
    result by :func:`store_analysis`.

    Nothing but this one item's own text is ever in the prompt. That is what
    keeps the corpus point-in-time safe: a later episode cannot inform an
    earlier item's analysis, because it is never in the room.
    """
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
                return _analyze_via_cli(cli, title=title, creator=creator,
                                        body=body, text_source=text_source)
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
            "content": _user_prompt(title=title, creator=creator, body=body,
                                    text_source=text_source),
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


#: Key under which :func:`store_analysis` records what the analysis rests on.
#: It is nested inside ``analysis_json`` rather than added as a column because
#: ``content_analysis`` has a fixed four-column shape that other agents are
#: reading right now, and because evidence about an analysis belongs to that
#: analysis -- a reader who has the JSON has the caveat too, with no join.
EVIDENCE_KEY = "evidence"


def evidence_block(*, text_source: str | None, chars: int | None = None,
                   substantive_chars: int | None = None) -> dict:
    """What this analysis was derived from, in the analysis's own words.

    The panel contract carries ``text_source`` on the item; this puts the same
    fact, plus how much prose there actually was, inside the take itself so a
    show-notes summary cannot be rendered as if it were a considered read of
    an hour of speech.
    """
    return {
        "text_source": text_source,
        "depth": depth_for(text_source),
        "thin": is_thin(text_source),
        "scoreable": is_scoreable(text_source),
        "chars": None if chars is None else int(chars),
        "substantive_chars": (None if substantive_chars is None
                              else int(substantive_chars)),
    }


def store_analysis(wh, item_id: str, analysis: TranscriptAnalysis,
                   *, model: str = MODEL, text_source: str | None = None,
                   chars: int | None = None,
                   substantive_chars: int | None = None) -> None:
    """Persist one analysis, with the provenance of the text it read.

    ``text_source`` is optional only so the single-link path in
    :mod:`fpl_edge.interfaces.creators` keeps working unchanged; every bulk
    write supplies it.
    """
    payload = analysis.model_dump()
    if text_source is not None:
        payload[EVIDENCE_KEY] = evidence_block(
            text_source=text_source, chars=chars,
            substantive_chars=substantive_chars,
        )
    wh.sql(
        "INSERT OR REPLACE INTO content_analysis VALUES (?, ?, ?, ?)",
        [item_id, validate_model_id(model), dt.datetime.now(dt.timezone.utc),
         json.dumps(payload)],
    )


def load_analysis(wh, item_id: str) -> TranscriptAnalysis | None:
    rows = wh.sql(
        "SELECT analysis_json FROM content_analysis WHERE item_id = ? "
        "ORDER BY created_utc DESC LIMIT 1", [item_id],
    )
    if rows.empty:
        return None
    # ``evidence`` is an extra key; pydantic ignores it by default, which is
    # what we want -- the analysis model stays the model's own output shape.
    return TranscriptAnalysis.model_validate(json.loads(rows.iloc[0, 0]))


def load_evidence(wh, item_id: str) -> dict | None:
    """The evidence block for the newest stored analysis, if it has one.

    ``None`` means the row predates evidence-stamping -- which is itself worth
    surfacing, since it means the depth is unrecorded rather than deep.
    """
    rows = wh.sql(
        "SELECT analysis_json FROM content_analysis WHERE item_id = ? "
        "ORDER BY created_utc DESC LIMIT 1", [item_id],
    )
    if rows.empty:
        return None
    return json.loads(rows.iloc[0, 0]).get(EVIDENCE_KEY)


#: Analysis stances onto the CLOSED Action set the scoreboard can settle.
#: "watch" is deliberately unmapped: a watch is not a scoreable position, and
#: pushing it into the scoreboard would dilute the creator's record with
#: non-calls. Watches stay in content_analysis, visible but unscored.
_STANCE_TO_ACTION = {
    "buy": "buy", "sell": "sell", "hold": "hold", "captain": "captain",
    "avoid": "avoid", "bench": "bench",
}


def _resolve_call_name(resolver, spoken: str):
    """Resolve a name the model returned in a structured field.

    `call.player` is a NAME, not prose, so the prose scanner is the wrong tool:
    find_mentions tokenises on [a-z0-9]+, so "Martin Odegaard" loses the stroke
    letter, fails on "degaard", falls back to the bare token "martin" and
    resolves to David Raya Martin. Measured on the live warehouse.

    But strict lookup alone is too strict: it refuses "Ezri Konsa" for
    "ezri konsa ngoyo" and "Will Osula" for "william osula", which are the same
    people. The rule below keeps those and still refuses the three real
    misattributions the scanner was making -- "Louie Barry" -> Thierno Barry,
    "Mohammed Vuskovic" -> Luka Vuskovic, "Trent Hume" -> Trai Hume -- because
    every step is containment or a prefix, never an edit distance. A name that
    needs a typo forgiven ("Cristian" for "Cristhian") is dropped and counted,
    which is the correct trade: a dropped claim is missing, a wrong one is a
    fabrication attributed to a named person.
    """
    code, _ = resolver.lookup(spoken)
    if code is not None:
        return code

    # names.norm is the repo's ONE name matcher -- it exists precisely so a
    # second one cannot drift from it, and it handles the stroke letters NFKD
    # leaves alone (o-slash was a real bug: a bookmaker's "Odegaard" matched
    # nothing).
    from fpl_edge.ingest.rivals.names import norm as _name_norm  # noqa: PLC0415

    def _norm(x: str) -> str:
        return " ".join(re.sub(r"[^a-z0-9 ]", " ", _name_norm(str(x))).split())

    want = _norm(spoken)
    if not want:
        return None
    mentions = [m for m in resolver.find_mentions(spoken, None)
                if m.code is not None]
    codes = {m.code for m in mentions}
    if len(codes) != 1:
        return None
    cand = _norm(mentions[0].matched_name)
    w, c = want.split(), cand.split()
    # One name written inside the other: "ezri konsa" vs "ezri konsa ngoyo",
    # or a candidate quoted inside a sentence the model returned.
    if f" {want} " in f" {cand} " or f" {cand} " in f" {want} ":
        return mentions[0].code
    # Same surname AND the given name is a prefix of the other: dan/daniel,
    # will/william. Rejects louie/thierno and mohammed/luka.
    if len(w) >= 2 and len(c) >= 2 and w[-1] == c[-1]:
        a, b = w[0], c[0]
        if a and b and (a.startswith(b) or b.startswith(a)):
            return mentions[0].code
    return None


def claims_from_analysis(
    analysis: TranscriptAnalysis,
    *,
    item,  # ContentItem
    resolver,  # PlayerResolver
    default_gw: int,
    season: str,
    model: str = MODEL,
):
    """Turn analysis calls into Claim rows the scoreboard can settle.

    Player names resolve through the SAME resolver as everything else; a name
    it refuses to guess is dropped and counted, never guessed. The claim's
    extractor is stamped ``llm:<model>`` -- the model that actually produced
    the analysis, validated by the same door that guards content_analysis.model
    -- so cue noise and semantic extraction are scored as separate channels and
    a re-read by a different model is distinguishable from the original.
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
        # lookup(), not find_mentions(). `call.player` is a NAME the model
        # returned in a structured field, not prose to be scanned. find_mentions
        # is a longest-match scan over free text: it tokenises on [a-z0-9]+, so
        # "Martin Odegaard" loses the stroke letter, fails on "degaard", falls
        # back to the bare token "martin" and resolves it to David Raya Martin.
        # That is a MISATTRIBUTION -- a creator's call about one player written
        # to the warehouse as a claim about another -- and it silently produced
        # such rows. lookup() folds accents, matches the whole alias, and
        # refuses ambiguity instead of guessing. Verified on the live warehouse:
        # find_mentions("Martin Odegaard") -> 154561 (Raya); lookup -> 184029.
        code = _resolve_call_name(resolver, call.player)
        if code is None:
            dropped.append(call.player)
            continue
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
            extractor=f"llm:{validate_model_id(model)}",
        ))
    return claims, dropped
