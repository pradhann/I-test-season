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

There are TWO grains here, and keeping them apart is load-bearing.

A **call** is a recommendation: buy, sell, captain. It is a prediction, it goes
to ``content_claim``, and the scoreboard settles it. An **insight** is an
observation -- "Semenyo is playing as a false nine now", "Arsenal's fixtures
turn in GW6" -- which is most of what an analytical channel actually produces
and none of which is a bet. Insights go to ``content_insight`` (see
migrations/content_005_insights.sql) and are never scored, because scoring an
observation would mark a creator wrong for being right about a role change
whose player then blanked. The prompt draws the line explicitly; see
:data:`_INSIGHT_RULES`.

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
import hashlib
import json
import re
from dataclasses import dataclass
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


def insights_permitted(text_source: str | None) -> bool:
    """May observations from this source become ``content_insight`` rows?

    Same gate as :func:`is_scoreable` -- transcripts and articles yes, show
    notes no -- but NOT the same argument, and the two are kept as separate
    functions so that a future change to one cannot silently redefine the
    other.

    ``is_scoreable`` is about calibration: a conviction band read off
    promotional copy would decalibrate a channel that exists to be calibrated.
    Nothing calibrates an insight; nothing settles it at all. The reason show
    notes are refused here is the QUOTE requirement. Every insight carries a
    verbatim span, and a description contains no speech -- it is a headline,
    sponsor copy, affiliate links and a chapter list. A chapter marker reading
    "12:30 Semenyo's new role" is a topic label. Asked to quote an observation
    out of it, a cooperative model returns "Semenyo's new role" as though
    somebody had asserted that his role changed. Nobody asserted anything, and
    a fabricated observation attributed to a named creator is exactly the
    failure this package refuses.
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
        # An episode of pure analysis -- role changes, set pieces, fixture
        # swings -- and not one transfer named is a REAL read, not a barren
        # one. Omitting insights here is how a Solio-style creator went on
        # reading as having said nothing.
        or analysis.insights
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


#: Closed set of insight topics. Closed for the same reason ``Action`` is: a
#: free-text topic cannot be grouped, filtered or rendered as a facet, and the
#: UI is about to render these. ``other`` is the honest escape hatch -- better
#: an insight filed as ``other`` than one stretched into ``tactical`` because
#: the enum left no room.
INSIGHT_TOPICS = (
    "role_change", "set_pieces", "minutes", "fixture_swing", "tactical",
    "injury_return", "price", "chip_strategy", "other",
)

#: What an insight can be ABOUT. ``none`` is a first-class answer: "the
#: international break resets everyone's minutes" has no entity, and inventing
#: one to satisfy a NOT NULL is how fake data gets born.
INSIGHT_ENTITY_KINDS = ("player", "team", "fixture", "gameweek", "none")


class Insight(BaseModel):
    """One OBSERVATION the speaker made. Explicitly not a recommendation.

    "Semenyo is on set pieces" may inform a buy. It is not a buy. Nothing
    settles it, so it never reaches ``content_claim``, ``claim_outcome`` or
    ``creator_score`` -- see migrations/content_005_insights.sql.
    """

    topic: Literal[
        "role_change", "set_pieces", "minutes", "fixture_swing", "tactical",
        "injury_return", "price", "chip_strategy", "other",
    ]
    entity_kind: Literal["player", "team", "fixture", "gameweek", "none"] = Field(
        description="What the observation is about; 'none' when about the game at large"
    )
    entity_name: str = Field(
        description="The entity exactly as spoken; empty string when entity_kind is none"
    )
    claim_text: str = Field(description="The observation in one plain line")
    quote: str = Field(
        description="VERBATIM span from the source. No quote, no insight."
    )
    horizon_gw: int | None = Field(
        description="First gameweek it applies to, ONLY if the speaker stated one"
    )
    horizon_gw_end: int | None = Field(
        description="Last gameweek of a stated range; null for a single gameweek"
    )
    conviction: Literal["high", "medium", "low"] = Field(
        description="How firmly the observation was asserted, in the speaker's language"
    )


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
    #: Observations, not recommendations. Defaulted so that the several hundred
    #: ``content_analysis`` rows written before this field existed still
    #: validate -- but an empty list from an old row means "NOT EXTRACTED", not
    #: "none were said". :func:`load_insights` distinguishes the two by looking
    #: for the key itself in the stored JSON; do not read emptiness off this
    #: attribute and conclude the episode was insight-free.
    insights: list[Insight] = Field(
        default_factory=list,
        description="Observations the speakers made that are NOT recommendations",
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


#: The observation/recommendation boundary, spelled out.
#:
#: Appended to :data:`_SYSTEM` for every source. Without it the model has one
#: shape available to it -- the player call -- and so it flattens everything
#: into one: "Semenyo is playing as a false nine" comes back as a buy, which is
#: a prediction nobody made, filed under a creator's name, and settled by the
#: scoreboard. The rules below give the observation somewhere else to go and
#: then give an operational test for which bucket a sentence lands in, because
#: a definition ("an insight is not a recommendation") is not a procedure.
_INSIGHT_RULES = """

INSIGHTS -- the observations, which are NOT recommendations.

Most of what an analytical channel says is not a call. It is the REASON a call
might be made: a role change, a set-piece order, a minutes pattern, a run of
fixtures turning. Put those in `insights`. Keep them out of transfers_in,
transfers_out, captaincy and differentials.

The test, applied to each sentence -- ask it in this order:

1. Does the sentence tell the listener what to DO with their team -- transfer
   someone in or out, captain, bench, avoid? Then it is a RECOMMENDATION and
   belongs in the call lists. Insights are not the place for it.
2. Otherwise, does it describe how the world IS or WILL BE -- who plays where,
   who takes the corners, who starts, whose fixtures turn, who is back from
   injury -- leaving the listener to decide what to do about it? Then it is an
   INSIGHT.

Worked examples:
  "Semenyo is playing as a false nine now"          -> insight, role_change
  "so get Semenyo in this week"                     -> recommendation (buy)
  "Wirtz has been on the set pieces since the
   international break"                             -> insight, set_pieces
  "Wirtz is the pick because of those set pieces"   -> recommendation (buy)
  "Arsenal's fixtures turn in GW6"                  -> insight, fixture_swing
  "Spurs rotate their keeper in cup weeks"          -> insight, minutes
  "which is why Vicario has to go"                  -> recommendation (sell)

A sentence carrying both -- "Semenyo's on set pieces now, so he's a buy" --
produces BOTH: one insight for the observation and one call for the
recommendation. Do not drop either and do not merge them.

Fields:
- topic: one of role_change, set_pieces, minutes, fixture_swing, tactical,
  injury_return, price, chip_strategy, other. Use `other` rather than
  stretching a named topic to fit.
- entity_kind: `player` when it is about one named player; `team` about a club;
  `fixture` about a specific match; `gameweek` about a week itself; `none` when
  it is about the game at large. Never name a player when a team was meant.
- entity_name: exactly as SPOKEN -- "Spurs", "Semenyo", "the Arsenal lot". Do
  not expand, correct or canonicalise it. Empty string when entity_kind=none.
- claim_text: the observation in one plain line, in your words.
- quote: VERBATIM from the text. IF YOU CANNOT QUOTE IT, DO NOT RECORD IT.
- horizon_gw / horizon_gw_end: the gameweek window ONLY if stated. "Fixtures
  turn in GW6" -> 6 and null. "GW6 through GW12" -> 6 and 12. Not stated ->
  both null. Never infer a window from context or from the episode's date.
- conviction: the speaker's certainty about THE OBSERVATION, in their own
  language, not your confidence that it is true.

Never record as an insight:
- A prediction of points, goals or a scoreline. That is an opinion about the
  future, not an observation of the setup.
- A well-known fact restated to fill time ("Haaland plays for City").
- Something asked as a question, floated and rejected, or read out from a
  viewer's message rather than asserted by the speaker.
- Anything you inferred, connected or concluded. Every insight is a thing
  somebody SAID, and the quote proves it.

Always emit the `insights` key, even when the list is empty. An empty list is a
real, correct and frequent answer; an ABSENT key is read downstream as "this
item was never examined for insights", which is a different and false thing to
say about an episode you have just read."""


def _system_prompt() -> str:
    """The full system turn: calls, then the observation/recommendation line.

    One function so the CLI backend and the SDK backend cannot drift into
    sending different instructions and producing rows that look alike.
    """
    return _SYSTEM + _INSIGHT_RULES


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
- insights must be EMPTY. An insight is an observation somebody made out loud,
  and nobody speaks here. A chapter marker reading "12:30 Semenyo's new role"
  is a TOPIC LABEL, not a statement that his role changed; a bullet in a blurb
  is a teaser for a discussion that happens somewhere you cannot see. There is
  nothing in this text to quote, so there is nothing to record. Return [].
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
        f"{_system_prompt()}\n\n"
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
        system=_system_prompt(),
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


# ---------------------------------------------------------------------------
# Insights: the second grain
#
# Everything below writes and reads ``content_insight``. It lives in this
# module rather than in ContentStore because store.py is owned by another agent
# this cycle; the read path deliberately mirrors
# ContentStore.claims_visible_at line for line so that moving it there later is
# a cut and paste rather than a redesign.
# ---------------------------------------------------------------------------

#: Column order of ``content_insight``, matching content_005_insights.sql.
#: Named explicitly in every INSERT: this table is written by more than one
#: caller and a positional ``VALUES (?, ...)`` is what breaks when the next
#: migration adds a column.
INSIGHT_COLS = (
    "insight_id", "item_id", "creator", "source_key", "topic", "entity_kind",
    "player_code", "entity_ref", "entity_name", "claim_text", "quote",
    "start_s", "horizon_gw", "horizon_gw_end", "confidence", "published_at",
    "season", "gameweek", "extractor",
)

#: The FPL season is 38 gameweeks. A horizon outside that is a model slip, and
#: a slip is dropped to NULL rather than stored -- an insight pointing at GW54
#: would silently never match any planner window.
_MAX_GW = 38


def _insight_gw(value: object) -> int | None:
    """A stated gameweek, or None. Never a guess and never out of range."""
    try:
        gw = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return gw if 1 <= gw <= _MAX_GW else None


def normalise_entity_ref(name: str | None) -> str | None:
    """Grouping key for a non-player entity. NOT a foreign key.

    Folded through :mod:`fpl_edge.ingest.rivals.names`, the repo's ONE name
    normaliser, so "Spurs", "spurs" and "SPURS " group -- and so that a second
    name matcher does not grow here, which is the drift that module exists to
    prevent. There is no team-code resolver in this package and this function
    does not pretend to be one: it lowercases, folds accents and squeezes
    punctuation, and that is the whole contract. ``entity_name`` keeps the
    spoken form for audit.
    """
    if not name or not str(name).strip():
        return None
    from fpl_edge.ingest.rivals.names import norm as _name_norm

    ref = " ".join(re.sub(r"[^a-z0-9 ]", " ", _name_norm(str(name))).split())
    return ref or None


@dataclass(frozen=True, slots=True)
class InsightRow:
    """One row of ``content_insight``, ready to write.

    Frozen because an insight is an immutable utterance, exactly as
    :class:`~fpl_edge.ingest.content.models.Claim` is. There is no update path
    and there is no outcome table: nothing settles an observation.
    """

    insight_id: str
    item_id: str
    creator: str
    source_key: str
    topic: str
    entity_kind: str
    #: Stable cross-season PlayerCode, or None. None is a NORMAL state: the
    #: entity may not be a player at all, or may be a player whose spoken name
    #: the strict resolver refused to guess. Either way ``entity_name`` holds
    #: what was actually said and the UI can still render it.
    player_code: int | None
    #: Normalised grouping string for team/fixture/gameweek entities.
    entity_ref: str | None
    #: As spoken, verbatim. Empty string when entity_kind == 'none'.
    entity_name: str
    claim_text: str
    #: Verbatim from the source. Never empty -- the writer refuses.
    quote: str
    start_s: float | None
    horizon_gw: int | None
    horizon_gw_end: int | None
    confidence: float
    published_at: dt.datetime
    season: str
    gameweek: int
    extractor: str

    def __post_init__(self) -> None:
        if self.published_at.tzinfo is None:
            raise ValueError(
                f"published_at must be timezone-aware UTC, got naive "
                f"{self.published_at!r}"
            )
        object.__setattr__(self, "published_at",
                           self.published_at.astimezone(dt.UTC))
        if not str(self.quote).strip():
            # The one invariant with no exception. An insight without a
            # verbatim span is an assertion the pipeline invented and
            # attributed to a named person.
            raise ValueError("an insight must carry a verbatim quote")
        if self.topic not in INSIGHT_TOPICS:
            raise ValueError(f"unknown insight topic: {self.topic!r}")
        if self.entity_kind not in INSIGHT_ENTITY_KINDS:
            raise ValueError(f"unknown entity_kind: {self.entity_kind!r}")
        if self.player_code is not None and self.entity_kind != "player":
            raise ValueError(
                f"player_code is set on a {self.entity_kind!r} insight; a code "
                f"on a non-player entity is a mis-join waiting to happen"
            )

    @staticmethod
    def make_id(item_id: str, topic: str, entity_kind: str,
                entity_key: str, quote: str) -> str:
        """Content-addressed, so a re-read does not duplicate the same insight.

        Deliberately keyed on the QUOTE rather than on a positional offset the
        way ``Claim.make_id`` is. Two claims can legitimately share
        (player, action, gameweek) -- two hosts disagreeing -- so claims need an
        offset to stay distinct. An insight is identified by the span that
        proves it: the same quote, about the same entity, on the same topic, is
        the same observation however the model rephrases ``claim_text`` on a
        later run. Hashing that makes re-extraction idempotent instead of
        additive.
        """
        raw = f"{item_id}|{topic}|{entity_kind}|{entity_key}|{quote.strip()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]


def insights_from_analysis(
    analysis: TranscriptAnalysis,
    *,
    item,                       # ContentItem
    resolver,                   # PlayerResolver | SeasonResolvers
    default_gw: int,
    season: str,
    model: str = MODEL,
    text_source: str | None = None,
    locate=None,                # Callable[[str], float | None]
) -> tuple[list[InsightRow], list[tuple[str, str]]]:
    """Turn the analysis's observations into ``content_insight`` rows.

    Returns ``(rows, dropped)`` where ``dropped`` is ``(reason, detail)`` pairs
    -- the caller reports them rather than discovering a silent shortfall.

    What this function will NOT do, in order of how badly it would matter:

    * It will not emit a row without a verbatim quote. That is enforced twice:
      here, where the insight is skipped and counted, and again in
      :class:`InsightRow`, which refuses to be constructed.
    * It will not guess a player. Resolution goes through
      :func:`_resolve_call_name`, the same strict path claims use -- exact
      alias, then containment, then a given-name prefix, never an edit
      distance. A name it refuses keeps ``entity_name`` and gets a NULL
      ``player_code``; the row survives so the creator's words can still be
      shown, and nothing downstream can join a stranger's history onto it.
    * It will not produce anything from show notes. ``text_source``, when
      given, is gated on :func:`insights_permitted`.
    * It will not infer a horizon. An unstated window stays NULL rather than
      defaulting to ``default_gw`` -- ``default_gw`` answers "when was this
      said", which is the ``gameweek`` column, not "when does it apply".

    ``locate`` is an optional ``quote -> start_s`` lookup (the platform's
    ``TranscriptIndex.find`` is exactly this shape). Absent, or returning None,
    ``start_s`` is NULL. There is no fallback offset: a deep link to the wrong
    minute is worse than no deep link.
    """
    if text_source is not None and not insights_permitted(text_source):
        return [], [("thin_source", str(text_source))]

    # Accept a bare PlayerResolver or the SeasonResolvers wrapper, exactly as
    # claims_from_analysis does.
    if hasattr(resolver, "for_season"):
        resolver = resolver.for_season(season)

    rows: list[InsightRow] = []
    dropped: list[tuple[str, str]] = []
    seen: set[str] = set()

    for ins in list(getattr(analysis, "insights", []) or []):
        claim_text = str(getattr(ins, "claim_text", "") or "").strip()
        quote = str(getattr(ins, "quote", "") or "").strip()
        topic = str(getattr(ins, "topic", "") or "")
        kind = str(getattr(ins, "entity_kind", "") or "")
        spoken = str(getattr(ins, "entity_name", "") or "").strip()

        if not quote:
            dropped.append(("no_quote", claim_text[:120]))
            continue
        if not claim_text:
            dropped.append(("no_claim_text", quote[:120]))
            continue
        if topic not in INSIGHT_TOPICS:
            dropped.append(("bad_topic", f"{topic!r}: {claim_text[:80]}"))
            continue
        if kind not in INSIGHT_ENTITY_KINDS:
            dropped.append(("bad_entity_kind", f"{kind!r}: {claim_text[:80]}"))
            continue
        if kind != "none" and not spoken:
            # "player" with no name is not an insight about a player, it is a
            # hole. Refuse it rather than storing an entity nobody can read.
            dropped.append(("no_entity_name", f"{kind}: {claim_text[:80]}"))
            continue

        code: int | None = None
        entity_ref: str | None = None
        if kind == "player":
            resolved = _resolve_call_name(resolver, spoken)
            if resolved is None:
                # NOT a drop. The observation was still made and is still
                # worth showing; it simply cannot be joined to a player.
                dropped.append(("unresolved_player", spoken))
            else:
                code = int(resolved)
        elif kind != "none":
            entity_ref = normalise_entity_ref(spoken)

        start = _insight_gw(getattr(ins, "horizon_gw", None))
        end = _insight_gw(getattr(ins, "horizon_gw_end", None))
        if start is None:
            # An end with no beginning is not a window. Nothing is invented to
            # complete it.
            end = None
        elif end is not None and end < start:
            end = None

        conviction = str(getattr(ins, "conviction", "") or "")
        if conviction not in CONVICTION_CONF:
            dropped.append(("bad_conviction", f"{conviction!r}: {claim_text[:80]}"))
            continue

        entity_key = str(code) if code is not None else (entity_ref or spoken.lower())
        insight_id = InsightRow.make_id(item.item_id, topic, kind, entity_key, quote)
        if insight_id in seen:
            continue
        seen.add(insight_id)

        rows.append(InsightRow(
            insight_id=insight_id,
            item_id=item.item_id,
            creator=item.creator,
            source_key=item.source_key,
            topic=topic,
            entity_kind=kind,
            player_code=code,
            entity_ref=entity_ref,
            entity_name=spoken,
            claim_text=claim_text[:600],
            quote=quote[:600],
            start_s=(None if locate is None else locate(quote)),
            horizon_gw=start,
            horizon_gw_end=end,
            confidence=CONVICTION_CONF[conviction],
            published_at=item.published_at,
            season=season,
            gameweek=int(default_gw),
            extractor=f"llm:{validate_model_id(model)}",
        ))
    return rows, dropped


def store_insights(wh, rows: list[InsightRow]) -> int:
    """Insert-once. Returns how many rows were new.

    Insert-once for the reason ``content_claim`` is: re-running extraction must
    be additive and must never edit what a creator said. There is no upsert and
    no revision table here -- unlike ``claim_outcome``, an insight is complete
    the moment it is uttered, so there is nothing about it that can later turn
    out differently.
    """
    if not rows:
        return 0
    ids = [r.insight_id for r in rows]
    placeholders = ", ".join("?" for _ in ids)
    have = wh.sql(
        f"SELECT insight_id FROM content_insight WHERE insight_id IN ({placeholders})",
        ids,
    )
    existing = set(have["insight_id"].astype(str)) if not have.empty else set()

    cols = ", ".join(INSIGHT_COLS)
    marks = ", ".join("?" for _ in INSIGHT_COLS)
    written = 0
    for row in rows:
        if row.insight_id in existing:
            continue
        existing.add(row.insight_id)  # a duplicate inside one batch, too
        wh.sql(
            f"INSERT INTO content_insight ({cols}) VALUES ({marks})",
            [
                row.insight_id, row.item_id, row.creator, row.source_key,
                row.topic, row.entity_kind,
                None if row.player_code is None else int(row.player_code),
                row.entity_ref, row.entity_name, row.claim_text, row.quote,
                None if row.start_s is None else float(row.start_s),
                row.horizon_gw, row.horizon_gw_end, float(row.confidence),
                row.published_at, row.season, int(row.gameweek), row.extractor,
            ],
        )
        written += 1
    return written


def insights_visible_at(
    wh,
    as_of: dt.datetime,
    *,
    season: str | None = None,
    creator: str | None = None,
    player_code: int | None = None,
    topic: str | None = None,
):
    """Insights a manager could have read before ``as_of``. The sanctioned read.

    Mirrors :meth:`ContentStore.claims_visible_at` exactly, including the
    strictly-less-than. An insight published at the very instant of the
    deadline could not have been acted on, and the boundary case is far more
    likely to be a timestamp rounded to the minute than a genuinely
    simultaneous publication.

    The same trap that makes this necessary for claims makes it necessary here,
    in a slightly nastier form: a Monday-morning "here is why Semenyo was
    playing as a false nine on Saturday" is a true observation, published too
    late to have informed anything, and it reads exactly like foresight once
    the timestamp is dropped.
    """
    if as_of.tzinfo is None:
        raise ValueError(f"as_of must be timezone-aware UTC, got naive {as_of!r}")
    as_of = as_of.astimezone(dt.UTC)
    where = ["published_at < ?"]
    params: list[object] = [as_of]
    for column, value in (("season", season), ("creator", creator),
                          ("topic", topic)):
        if value is not None:
            where.append(f"{column} = ?")
            params.append(value)
    if player_code is not None:
        where.append("player_code = ?")
        params.append(int(player_code))
    return wh.sql(
        "SELECT * FROM content_insight WHERE " + " AND ".join(where)
        + " ORDER BY published_at, insight_id",
        params,
    )


#: Top-level key of ``analysis_json`` holding the insight list. Its PRESENCE is
#: the only way to tell "extracted, and there were none" from "written before
#: insights existed" -- see :func:`load_insights`.
INSIGHTS_KEY = "insights"


def analysis_has_insight_field(payload: dict) -> bool:
    """Did whatever wrote this analysis know about insights at all?"""
    return INSIGHTS_KEY in (payload or {})


def load_insights(wh, item_id: str) -> list[Insight] | None:
    """The stored insights, or ``None`` meaning NOT EXTRACTED.

    The distinction is the whole function. ``[]`` means a model read this item
    and found no observations, which is a real result. ``None`` means the row
    predates the insight field, so nothing is known either way -- and rendering
    that as "no insights" would tell the reader something the warehouse never
    said. Callers must branch on it; do not use ``or []``.
    """
    rows = wh.sql(
        "SELECT analysis_json FROM content_analysis WHERE item_id = ? "
        "ORDER BY created_utc DESC LIMIT 1", [item_id],
    )
    if rows.empty:
        return None
    payload = json.loads(rows.iloc[0, 0])
    if not analysis_has_insight_field(payload):
        return None
    return [Insight.model_validate(x) for x in (payload[INSIGHTS_KEY] or [])]
