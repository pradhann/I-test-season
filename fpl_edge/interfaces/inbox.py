"""The idea inbox: raw text in, a tracked falsifiable claim out.

This is the seam every surface goes through. Telegram, the CLI and the MCP server
all call :meth:`IdeaInbox.submit` with a string and differ only in how they render
what comes back. There is exactly one implementation of "what happens when the
user has a thought", so the three surfaces cannot drift into three subtly
different notions of what an idea is.

The ordering inside :meth:`submit` is load-bearing:

1. Parse. If the text does not determine a player, stop and ask -- see
   :mod:`fpl_edge.interfaces.parsing` for why guessing is worse than asking.
2. Write the idea row, with its thesis, and commit.
3. Capture the submission-time context and commit.
4. *Then* compute the verdict and commit that.

The verdict is last because it is the only step that can be slow or can fail. A
model that times out, throws, or has not been written yet must not be able to
lose the user's thought -- the thesis and its timestamp are the durable asset,
the verdict is an opinion about it. Steps 2-4 are separate commits for the same
reason.

Security note: ``text`` is untrusted input from a chat app. It is bound as a SQL
parameter, matched against regexes and string-compared. It is never evaluated,
never formatted into a query, and never treated as a command. The only strings
this module dispatches on are the literal command tokens in
:mod:`fpl_edge.interfaces.telegram`, which are matched exactly and never contain
user-supplied text.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
import uuid
from dataclasses import dataclass

from fpl_edge.config import USER
from fpl_edge.interfaces.features import (
    club_share,
    club_team_code,
    comparator_set,
    form_percentile,
    player_history,
    player_universe,
    population_base_rates,
    safe_bool,
    safe_float,
    safe_int,
)
from fpl_edge.interfaces.ideas import (
    DEFAULT_HORIZON,
    CandidateMatch,
    Clarification,
    Comparator,
    Idea,
    IdeaContext,
    IdeaKind,
    IdeaStatus,
    Verdict,
    build_thesis,
    make_idea_id,
)
from fpl_edge.interfaces.parsing import (
    MessageParser,
    PlayerResolver,
    has_fpl_intent,
    interpret_reply,
)
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.interfaces.verdict import PriorVerdict, TimeBounded, VerdictProvider, issue
from fpl_edge.store import Snapshot, Warehouse
from fpl_edge.types import GwId, PlayerCode, Season

log = logging.getLogger("fpl_edge.inbox")

UTC = dt.timezone.utc

DEFAULT_SEASON = Season("2026-27")


@dataclass(frozen=True, slots=True)
class Submission:
    """What came back from putting a message in the inbox.

    Exactly one of ``idea`` and ``clarification`` is set. ``latency_ms`` is
    measured end to end inside :meth:`IdeaInbox.submit` and is the number the
    "verdict inside a minute" requirement is actually about, so it is returned to
    every caller rather than only logged.
    """

    latency_ms: float
    idea: Idea | None = None
    verdict: Verdict | None = None
    clarification: Clarification | None = None
    resolved_from_clarification: bool = False

    @property
    def ok(self) -> bool:
        return self.idea is not None

    def render(self) -> str:
        """Plain text for a chat reply. No markup: the user's own words are
        echoed back inside it, and Telegram markup in a message the user typed
        must not become formatting -- or a link -- in the bot's reply."""
        if self.clarification is not None:
            return self.clarification.render()
        if self.idea is None:
            return "Could not read that as an idea."
        idea = self.idea
        lines = [
            f"Logged {idea.idea_id}",
            f"Thesis: {idea.thesis}",
        ]
        if self.verdict is not None:
            lines.append(f"Verdict: {self.verdict.headline()} [{self.verdict.confidence} confidence]")
            lines.append(f"Why: {self.verdict.rationale}")
        lines.append(f"Settles: {idea.window_label}. Tracking from now, acted on or not.")
        lines.append(f"({self.latency_ms:.0f} ms)")
        return "\n".join(lines)


class IdeaInbox:
    """Accepts text, produces tracked ideas.

    One inbox per warehouse. Cheap to construct; the expensive part (the player
    universe for a given instant) is built per submission because the universe
    genuinely changes between messages and caching it would let a stale price
    into a thesis.
    """

    def __init__(
        self,
        warehouse: Warehouse,
        *,
        season: Season = DEFAULT_SEASON,
        provider: VerdictProvider | TimeBounded | None = None,
        clock=None,
    ) -> None:
        self.wh = warehouse
        self.registry = IdeaRegistry(warehouse)
        self.season = season
        self.provider = provider or PriorVerdict()
        self._clock = clock or (lambda: dt.datetime.now(UTC))

    def now(self) -> dt.datetime:
        return self._clock().astimezone(UTC)

    # -- the one entry point -------------------------------------------------

    def submit(
        self,
        text: str,
        *,
        source: str = "cli",
        source_ref: str | None = None,
        now: dt.datetime | None = None,
        acted: bool = False,
    ) -> Submission:
        """Turn one message into a registry entry with a verdict.

        ``acted`` defaults to False and stays False unless the user says
        otherwise. That default is the whole design: the ideas worth learning
        from are disproportionately the ones that were never acted on, because
        those are the ones no other record of the season contains.
        """
        started = time.perf_counter()
        submitted_at = (now or self.now()).astimezone(UTC)
        snapshot = self.wh.snapshot_at(submitted_at)

        # An outstanding question from this chat takes priority: a bare "2" is
        # only meaningful as an answer to one.
        if source_ref:
            answered = self._try_answer_pending(text, source, source_ref, snapshot, submitted_at)
            if answered is not None:
                return self._finish(answered, started)

        players = player_universe(snapshot, str(self.season))
        if players.empty:
            return self._finish(
                Submission(
                    latency_ms=0.0,
                    clarification=Clarification(
                        raw_text=text,
                        question=(
                            f"No {self.season} player list is loaded at "
                            f"{submitted_at:%Y-%m-%d %H:%M}Z, so I cannot resolve names. "
                            "Run `make ingest` first."
                        ),
                        candidates=(),
                        pending_id="",
                        source=source,
                        source_ref=source_ref,
                        kind="no_universe",
                    ),
                ),
                started,
            )

        resolver = PlayerResolver(players)
        default_gw = self._default_gw(snapshot)
        parsed = MessageParser(resolver, default_gw=default_gw).parse(text)

        if parsed.subject is None or parsed.subject.ambiguous or parsed.subject.best is None:
            return self._finish(
                self._ask(text, parsed, source, source_ref, submitted_at), started
            )

        idea = self._build_idea(
            text=text,
            kind=parsed.kind,
            subject_code=parsed.subject.best.code,
            subject_label=parsed.subject.best.label,
            rival_code=parsed.rival.best.code if parsed.rival and parsed.rival.best else None,
            comparator=parsed.comparator,
            gw=int(parsed.gw) if parsed.gw else default_gw,
            horizon_gws=parsed.horizon_gws,
            confidence=parsed.subject.confidence,
            players=players,
            source=source,
            source_ref=source_ref,
            submitted_at=submitted_at,
            acted=acted,
        )
        return self._finish(self._commit(idea, snapshot, players, submitted_at), started)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _finish(sub: Submission, started: float) -> Submission:
        elapsed = (time.perf_counter() - started) * 1000.0
        return Submission(
            latency_ms=elapsed,
            idea=sub.idea,
            verdict=sub.verdict,
            clarification=sub.clarification,
            resolved_from_clarification=sub.resolved_from_clarification,
        )

    def _default_gw(self, snapshot: Snapshot) -> int:
        """The gameweek an unqualified idea is about.

        The next deadline, always. A message sent on Tuesday about a player is
        about the coming weekend; anchoring to a completed gameweek would make
        every unqualified idea unfalsifiable from birth.
        """
        try:
            return int(snapshot.next_gw(str(self.season)))
        except KeyError:
            pass
        # No event calendar for this season. Archive seasons ingested from
        # vaastav carry results but no dim_event rows, and defaulting to GW1
        # there dated a November idea to the opening weekend -- a window that had
        # already closed, so the idea was born unfalsifiable. The gameweek after
        # the last finalised one is the honest inference.
        results = snapshot.results_before(str(self.season))
        if not results.empty and "gw" in results.columns:
            return int(results["gw"].max()) + 1
        return 1

    def _ask(
        self,
        text: str,
        parsed,
        source: str,
        source_ref: str | None,
        when: dt.datetime,
    ) -> Submission:
        """Ask, or say this was not an idea. They are different answers.

        The live bot's first three messages were "/start", "Hi" and "This is the
        second message". None is an idea, and replying to "Hi" with a shortlist
        of players whose names are two edits away is worse than useless -- it
        also leaves a pending question that would eat the user's next real
        message. So chatter gets a short, honest reply and nothing is stored.
        """
        res = parsed.subject
        pending_id = f"pend_{uuid.uuid4().hex[:12]}"

        if (res is None or not res.candidates) and not has_fpl_intent(text):
            return Submission(
                latency_ms=0.0,
                clarification=Clarification(
                    raw_text=text,
                    question=(
                        "That does not look like an FPL idea, so I have not logged "
                        "anything. Send me something like:\n"
                        "  I like Rashford\n"
                        "  Semenyo captain GW12?\n"
                        "  sell Wood\n"
                        "/help for what else I can do."
                    ),
                    candidates=(),
                    pending_id="",
                    source=source,
                    source_ref=source_ref,
                    kind="not_an_idea",
                ),
            )

        if res is None or not res.candidates:
            question = (
                "That reads like an FPL idea but I could not identify the player. "
                "Try a surname, e.g. 'Semenyo captain GW12?'."
            )
            candidates: tuple[CandidateMatch, ...] = ()
            kind = "not_found"
        elif res.ambiguous:
            question = f"More than one player matches {res.query!r}. Which one?"
            candidates = res.candidates
            kind = "ambiguous"
        else:
            # Nothing cleared the bar. These are suggestions, not a shortlist,
            # and the wording has to say so or the user will assume the first one
            # was already picked.
            question = (
                f"No player in {self.season} clearly matches {res.query!r}. "
                "Closest I have, if one of these is who you meant:"
            )
            candidates = res.candidates
            kind = "not_found"

        clar = Clarification(
            raw_text=text,
            question=question,
            candidates=candidates,
            pending_id=pending_id,
            source=source,
            source_ref=source_ref,
            kind=kind,
        )
        if source_ref and candidates:
            self.registry.put_pending(
                pending_id=pending_id,
                created_utc=when,
                source=source,
                source_ref=source_ref,
                raw_text=text,
                question=question,
                candidates=candidates,
            )
        return Submission(latency_ms=0.0, clarification=clar)

    def _try_answer_pending(
        self,
        text: str,
        source: str,
        source_ref: str,
        snapshot: Snapshot,
        when: dt.datetime,
    ) -> Submission | None:
        """If this message answers an outstanding question, complete that idea.

        Returns None when the message is not an answer, in which case it is
        handled as a fresh idea. That fallthrough matters: a user who ignores the
        question and types something new must not have their new thought silently
        interpreted as an answer to the old one.
        """
        pending = self.registry.open_pending(source, source_ref)
        if pending is None:
            return None
        pending_id, original_text, candidates = pending
        chosen = interpret_reply(text, candidates)
        if chosen is None:
            return None

        players = player_universe(snapshot, str(self.season))
        resolver = PlayerResolver(players)
        default_gw = self._default_gw(snapshot)
        # Re-parse the ORIGINAL message for intent and gameweek. The reply is
        # only ever the identity of the player; everything else the user meant is
        # in what they first typed.
        parsed = MessageParser(resolver, default_gw=default_gw).parse(original_text)
        comparator = parsed.comparator
        if comparator is Comparator.NAMED_PLAYER and not (parsed.rival and parsed.rival.best):
            comparator = Comparator.PRICE_PEER_MEDIAN

        idea = self._build_idea(
            text=original_text,
            kind=parsed.kind,
            subject_code=chosen.code,
            subject_label=chosen.label,
            rival_code=parsed.rival.best.code if parsed.rival and parsed.rival.best else None,
            comparator=comparator,
            gw=int(parsed.gw) if parsed.gw else default_gw,
            horizon_gws=parsed.horizon_gws,
            confidence=1.0,  # the user said so themselves
            players=players,
            source=source,
            source_ref=source_ref,
            submitted_at=when,
            acted=False,
        )
        self.registry.close_pending(pending_id)
        sub = self._commit(idea, snapshot, players, when)
        return Submission(
            latency_ms=0.0, idea=sub.idea, verdict=sub.verdict,
            resolved_from_clarification=True,
        )

    def _build_idea(
        self,
        *,
        text: str,
        kind: IdeaKind,
        subject_code: PlayerCode,
        subject_label: str,
        rival_code: PlayerCode | None,
        comparator: Comparator,
        gw: int,
        horizon_gws: int,
        confidence: float,
        players,
        source: str,
        source_ref: str | None,
        submitted_at: dt.datetime,
        acted: bool,
    ) -> Idea:
        subj_rows = players[players["code"] == int(subject_code)]
        subject = subj_rows.iloc[0] if not subj_rows.empty else None
        subject_name = str(subject["web_name"]) if subject is not None else subject_label

        _, comparator_label = comparator_set(
            players, subject, comparator, comparator_code=rival_code
        )
        thesis, rule = build_thesis(
            kind=kind,
            subject_name=subject_name,
            comparator=comparator,
            comparator_label=comparator_label,
            gw=GwId(gw),
            horizon_gws=horizon_gws,
        )
        return Idea(
            idea_id=make_idea_id(submitted_at, source, source_ref, text),
            created_utc=submitted_at,
            as_of=submitted_at,
            source=source,
            source_ref=source_ref,
            raw_text=text,
            season=self.season,
            kind=kind,
            subject_code=subject_code,
            subject_name=subject_name,
            comparator=comparator,
            comparator_code=rival_code,
            comparator_label=comparator_label,
            gw=GwId(gw),
            horizon_gws=horizon_gws or DEFAULT_HORIZON[kind],
            thesis=thesis,
            resolution_rule=rule,
            parse_confidence=float(confidence),
            status=IdeaStatus.OPEN,
            acted=acted,
            acted_utc=submitted_at if acted else None,
        )

    def _commit(self, idea: Idea, snapshot: Snapshot, players, when: dt.datetime) -> Submission:
        """Durable first, opinion second. See the module docstring."""
        fresh = self.registry.insert_idea(idea)
        if not fresh:
            existing = self.registry.get(idea.idea_id)
            return Submission(
                latency_ms=0.0, idea=existing,
                verdict=self.registry.latest_verdict(idea.idea_id),
            )

        try:
            self.registry.insert_context(self._capture_context(idea, snapshot, players, when))
        except Exception:  # noqa: BLE001 - context is for analysis, never worth losing an idea
            # Logged, not swallowed. A silent failure here is invisible until
            # `fpl idea review` quietly reports n=6 out of 18 ideas and the bias
            # probes lose most of their power for a reason nobody can see.
            log.exception("context capture failed for %s", idea.idea_id)

        verdict = issue(idea, snapshot, self.provider, now=when)
        self.registry.insert_verdict(verdict)
        return Submission(latency_ms=0.0, idea=idea, verdict=verdict)

    def _capture_context(self, idea: Idea, snapshot: Snapshot, players, when: dt.datetime) -> IdeaContext:
        """Freeze the features the bias probes will need in six months' time."""
        code = int(idea.subject_code) if idea.subject_code is not None else None
        row = None
        if code is not None:
            hit = players[players["code"] == code]
            row = hit.iloc[0] if not hit.empty else None

        history = player_history(snapshot, str(idea.season))
        if not history.empty and "position" in players.columns:
            history = history.merge(
                players[["code", "position"]].astype({"code": int}), on="code", how="left"
            )
        pop = population_base_rates(history)

        supported = club_team_code(snapshot, str(idea.season), USER.supported_club)
        club_rate, universe_n = club_share(players, supported)

        mine = history[history["code"] == code] if (not history.empty and code) else history.iloc[0:0]
        h = mine.iloc[0] if not mine.empty else None
        position = int(row["position"]) if row is not None and row.get("position") is not None else None

        return IdeaContext(
            idea_id=idea.idea_id,
            captured_utc=when,
            position=position,
            team_code=safe_int(row["team_code"]) if row is not None else None,
            price_tenths=safe_int(row["price_tenths"]) if row is not None else None,
            selected_by_pct=safe_float(row["selected_by_pct"]) if row is not None else None,
            availability=str(row["status"]) if row is not None else None,
            form_points_last3=safe_float(h["form_points_last3"]) if h is not None else None,
            form_percentile=(
                form_percentile(history, code, position) if (h is not None and code) else None
            ),
            minutes_last3=safe_int(h["minutes_last3"]) if h is not None else None,
            last_match_gw=safe_int(h["last_match_gw"]) if h is not None else None,
            last_match_points=safe_int(h["last_match_points"]) if h is not None else None,
            last_match_was_home=safe_bool(h["last_match_was_home"]) if h is not None else None,
            gws_since_haul=safe_int(h["gws_since_haul"]) if h is not None else None,
            season_ppg=safe_float(h["season_ppg"]) if h is not None else None,
            pop_home_rate=pop["pop_home_rate"],  # type: ignore[arg-type]
            pop_mean_gws_since_haul=pop["pop_mean_gws_since_haul"],  # type: ignore[arg-type]
            pop_recent_haul_rate=pop["pop_recent_haul_rate"],  # type: ignore[arg-type]
            pop_mean_form_pct=pop["pop_mean_form_pct"],  # type: ignore[arg-type]
            pop_n=int(pop["pop_n"] or 0),
            supported_team_code=supported,
            is_supported_club=(
                None if (supported is None or row is None)
                else bool(int(row["team_code"]) == supported)
            ),
            pop_supported_club_rate=club_rate,
            pop_universe_n=universe_n,
        )
