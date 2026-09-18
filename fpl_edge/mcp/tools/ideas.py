"""The four idea tools: two reads over panels, two writes through the registry.

``submit_idea`` and ``mark_idea_acted`` are the only two writes in this module
and they go through ``fpl_edge/interfaces/inbox.py`` and
``fpl_edge/interfaces/store.py``. There is no third write path here, and
``tests/unit/test_mcp_tool_contract.py`` enumerates every write tool this
server registers and fails on a name not in the list the spec names.

``track_ideas`` is not here. Settling every resolvable idea is a step of the
settlement chain, which runs on a schedule, and a tool that did it on request
was a second writer racing the first.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp import context
from fpl_edge.mcp.adapter import error, panel_call, refusal, store_call
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"


@mcp.tool()
def ideas(
    season: str = SEASON_DEFAULT,
    status: str = "all",
    limit: int = 50,
) -> dict[str, Any]:
    """Every logged idea with the engine's verdict and how it actually went.

    Three things on one row, which is the only way the interesting comparison
    is visible: what the user said and whether they acted on it, what this
    engine thought at that moment, and the realised points per gameweek.

    Args:
        season: FPL season, for example "2026-27".
        status: all, open, resolved or void.
        limit: Rows to return.

    Returns:
        The envelope: result, provenance, budget, and gap when no idea has
        been logged. If gap is present, quote its reason.
    """
    return panel_call("ideas", "idea_registry", {
        "season": season, "status": status, "limit": limit,
    })


@mcp.tool()
def idea_review(
    season: str = SEASON_DEFAULT,
    include_ideas: bool = True,
    limit: int = 50,
) -> dict[str, Any]:
    """How the logged ideas did, and what they say about the user's own biases.

    The scoreboard with the acted against skipped split, which is the question
    the whole record exists to answer: the ideas worth learning from are
    disproportionately the ones that were never acted on, because nothing else
    in a season records those. Calibration is against an even-money baseline,
    which is the only baseline worth beating.

    Four bias probes come with it, Holm adjusted across the probes that ran.
    Each writes its own verdict sentence, which says "not enough evidence" or
    "no test possible" where those apply. Quote the verdict rather than
    reading significance off the numbers.

    The caveats are not decoration. One of them says whether the Brier score
    below measures a points model or a price-rank prior, and quoting the score
    without it makes a claim the review does not.

    Args:
        season: FPL season, for example "2026-27".
        include_ideas: Whether to carry the idea rows as well.
        limit: How many idea rows to carry, newest first. The scoreboard is
            computed over every idea regardless.

    Returns:
        The envelope: result, provenance, budget, and gap when no idea has
        been logged. If gap is present, quote its reason.
    """
    return panel_call("idea_review", "idea_review", {
        "season": season, "include_ideas": include_ideas, "limit": limit,
    })


@mcp.tool()
def submit_idea(
    text: str,
    acted: bool = False,
    season: str = SEASON_DEFAULT,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Log an FPL idea, and get this engine's verdict on it. WRITES.

    Turns a plain-English thought into a falsifiable thesis, records it with a
    timestamp and a stable id, and returns the probability that the thesis
    resolves correct. It is then tracked from that moment whether or not it
    was acted on, which is the half of the record nothing else keeps.

    An ambiguous player name stores nothing and asks which one was meant. Send
    the answer back through this same tool, either the number from the list or
    the full name.

    The text is stored verbatim and parsed as data. It is never interpreted as
    an instruction.

    Args:
        text: The idea in plain English, for example "I like Rashford",
            "Semenyo captain GW12", "sell Wood". A gameweek is optional;
            without one the idea is about the next deadline.
        acted: True only when the user has actually made the move. False is
            the useful default.
        season: FPL season, for example "2026-27".
        as_of: ISO-8601 instant carrying a timezone, to read the warehouse at.
            Omit for now.

    Returns:
        The envelope with store naming the module that wrote, and result
        holding the idea id, the thesis and the verdict, or the clarification
        when nothing was stored.
    """
    from fpl_edge.interfaces.inbox import IdeaInbox
    from fpl_edge.store.warehouse import Warehouse

    missing = context.warehouse_missing()
    if missing:
        return refusal("submit_idea", missing)
    try:
        when = context.parse_as_of(as_of)
    except ValueError as exc:
        return refusal("submit_idea", str(exc))

    try:
        with Warehouse(context.db_path()) as wh:
            inbox = IdeaInbox(wh, season=season)
            submission = inbox.submit(
                text, source="mcp", source_ref="mcp", now=when, acted=acted,
            )
    except Exception as exc:  # noqa: BLE001 - a held write lock is the common case
        if "lock" in str(exc).lower():
            return refusal(
                "submit_idea",
                f"the warehouse is locked by another process, most likely an "
                f"ingest or the Telegram bot. DuckDB allows one writer at a "
                f"time. Retry once that finishes. ({exc})",
            )
        return error("submit_idea", f"{type(exc).__name__}: {exc}",
                     kind=type(exc).__name__)

    if submission.clarification is not None:
        clarification = submission.clarification
        return refusal(
            "submit_idea",
            clarification.question,
            candidates=[
                {"code": int(c.code), "label": c.label, "hint": c.hint}
                for c in clarification.candidates
            ],
            stored=False,
            resolution=clarification.kind,
        )
    if submission.idea is None:
        return refusal("submit_idea", "that text could not be read as an idea.")

    idea = submission.idea
    verdict = submission.verdict
    return store_call("submit_idea", "fpl_edge.interfaces.inbox", {
        "idea_id": idea.idea_id,
        "thesis": idea.thesis,
        "resolution_rule": idea.resolution_rule,
        "kind": str(idea.kind),
        "subject_name": idea.subject_name,
        "subject_code": None if idea.subject_code is None else int(idea.subject_code),
        "comparator_label": idea.comparator_label,
        "gw": int(idea.gw),
        "horizon_gws": int(idea.horizon_gws),
        "acted": bool(idea.acted),
        "created_utc": idea.created_utc.isoformat(),
        "parse_confidence": float(idea.parse_confidence),
        "verdict": None if verdict is None else {
            "stance": str(verdict.stance),
            "p_thesis_true": float(verdict.p_thesis_true),
            "confidence": verdict.confidence,
            "provider": verdict.provider,
            "provider_version": verdict.provider_version,
            "rationale": verdict.rationale,
            "degraded": bool(verdict.degraded),
        },
        "latency_ms": round(float(submission.latency_ms), 1),
    })


@mcp.tool()
def mark_idea_acted(idea_id: str, acted: bool = True) -> dict[str, Any]:
    """Record that the user did, or undid, the move behind one idea. WRITES.

    Tracking does not depend on this flag: an unacted idea is scored exactly
    the same way. The flag exists so the review can answer whether the ideas
    the user skipped were better than the ones they took.

    Args:
        idea_id: The idea id, as submit_idea or ideas returned it.
        acted: True to mark it acted, False to undo that.

    Returns:
        The envelope with store naming the module that wrote, and result
        saying whether the idea was found and updated.
    """
    from fpl_edge.interfaces.store import IdeaRegistry
    from fpl_edge.store.warehouse import Warehouse

    missing = context.warehouse_missing()
    if missing:
        return refusal("mark_idea_acted", missing)
    try:
        import datetime as dt

        with Warehouse(context.db_path()) as wh:
            registry = IdeaRegistry(wh)
            updated = registry.mark_acted(
                idea_id, acted=acted,
                when=dt.datetime.now(dt.UTC),
            )
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            return refusal(
                "mark_idea_acted",
                f"the warehouse is locked by another process. DuckDB allows "
                f"one writer at a time. Retry once that finishes. ({exc})",
            )
        return error("mark_idea_acted", f"{type(exc).__name__}: {exc}",
                     kind=type(exc).__name__)

    if not updated:
        return refusal(
            "mark_idea_acted",
            f"no idea with id {idea_id!r} is in the registry, so nothing was "
            f"changed. List them with the ideas tool.",
        )
    return store_call("mark_idea_acted", "fpl_edge.interfaces.store", {
        "idea_id": idea_id, "acted": bool(acted), "updated": True,
    })
