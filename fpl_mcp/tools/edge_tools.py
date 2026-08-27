"""
MCP tools for the fpl-edge decision engine.

These expose the engine's *idea inbox* -- the user's own hypotheses, turned
into falsifiable theses, given a model verdict and tracked whether or not they
were acted on -- alongside the weekly decision report. They sit next to the
semantic-layer/team/expert tools so that a chat can go from "who is in form?"
(``player_form``) to "log that I like him and tell me if I am wrong"
(``submit_idea``) without leaving the conversation.

The engine lives in a separate repository. This module locates it at import time
and degrades gracefully if it is absent: a missing engine returns an explanatory
string from each tool rather than raising at import, which would take the whole
fpl_mcp server -- and every existing tool -- down with it.

Configuration, both optional:

* ``FPL_EDGE_HOME`` -- path to the fpl-edge checkout. Defaults to a sibling
  directory of this repository named ``i-test-season``.
* ``FPL_EDGE_DB`` -- path to the DuckDB warehouse. Defaults to
  ``$FPL_EDGE_HOME/data/warehouse/fpl.duckdb``.

Security note. ``submit_idea`` takes free text that ultimately originates from a
chat. It is treated as DATA: parsed into an Idea record, bound as a SQL
parameter, and never executed or interpreted as an instruction to the engine.
Nothing here reads, returns or logs a secret; the Telegram token is used only by
the bot process in the engine repository and has no path into this server.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any, Optional

from fpl_mcp.server import mcp  # type: ignore


# -----------------------------------------------------------------------------
# Locate the engine. Import failure must not break the rest of the server.

def _engine_home() -> Path:
    # The engine lives in this same repo: fpl_mcp/ and fpl_edge/ are siblings,
    # so the checkout root is two parents up from this file. FPL_EDGE_HOME is
    # kept only as an override for pointing the toolbelt at another checkout.
    configured = os.environ.get("FPL_EDGE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2]


_HOME = _engine_home()

_IMPORT_ERROR: Optional[str] = None
try:
    from fpl_edge.interfaces.bias import review as _review  # type: ignore
    from fpl_edge.interfaces.inbox import IdeaInbox  # type: ignore
    from fpl_edge.interfaces.registry import IdeaRegistry  # type: ignore
    from fpl_edge.interfaces.report import weekly_report as _weekly_report  # type: ignore
    from fpl_edge.interfaces.tracking import track as _track  # type: ignore
    from fpl_edge.store import Warehouse  # type: ignore
except Exception as exc:  # noqa: BLE001 - see module docstring
    _IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


DEFAULT_SEASON = "2026-27"
UTC = dt.timezone.utc


def _db_path() -> Path:
    configured = os.environ.get("FPL_EDGE_DB")
    if configured:
        return Path(configured).expanduser()
    return _HOME / "data" / "warehouse" / "fpl.duckdb"


def _unavailable() -> Optional[str]:
    """Return a human-readable reason the engine cannot be used, or None."""
    if _IMPORT_ERROR is not None:
        return (
            f"The fpl-edge engine is not importable from {_HOME}. "
            f"Set FPL_EDGE_HOME to the checkout directory. ({_IMPORT_ERROR})"
        )
    db = _db_path()
    if not db.exists():
        return (
            f"No fpl-edge warehouse at {db}. Run `make ingest` in the engine "
            "repository, or set FPL_EDGE_DB."
        )
    return None


def _open(*, read_only: bool = False) -> "Warehouse":
    """Open the warehouse.

    ``read_only`` matters here in a way it does not in the engine's own CLI.
    DuckDB permits exactly one writer, and on this machine the Telegram bot may
    be long-polling against the same file all evening. Read-only tools take a
    shared lock and work regardless; only submit/track need to write, and those
    surface a clear message if the bot happens to hold the lock.
    """
    return Warehouse(_db_path(), read_only=read_only)


def _locked_message(exc: Exception) -> str:
    return (
        "The fpl-edge warehouse is locked by another process -- most likely the "
        "Telegram bot (`fpl idea telegram`) or an ingest run. DuckDB allows one "
        "writer at a time. Stop that process and retry, or use the read-only "
        f"tools (review_ideas, weekly_decision_report, engine_status).\n{exc}"
    )


def _now(as_of: Optional[str]) -> "dt.datetime":
    """Parse an optional ISO instant. Tz-aware UTC, always.

    A naive timestamp is rejected rather than assumed to be UTC: the engine's
    rule registry is explicit that only the API's UTC deadlines are
    authoritative, and silently localising here would be the same class of bug
    as reading a deadline off the rules page.
    """
    if not as_of:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(as_of.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of must carry a timezone, e.g. 2026-08-18T22:50:00Z")
    return parsed.astimezone(UTC)


# -----------------------------------------------------------------------------
# Tools


@mcp.tool()
def submit_idea(
    text: str,
    acted: bool = False,
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
) -> str:
    """Log an FPL idea and get an immediate model verdict on it.

    Turns a plain-English thought into a falsifiable thesis, records it in the
    engine's thesis registry with a timestamp and a stable id, and returns the
    model's probability that the thesis resolves correct. The idea is then
    tracked automatically from that moment, **whether or not it is acted on** --
    the ideas that were skipped are the ones nothing else records.

    If the text names a player ambiguously (there are two Palmers in 2026-27),
    this asks which one was meant and stores nothing, rather than guessing. Send
    the answer back through this same tool -- either the number from the list or
    the full name -- to complete the idea.

    Args:
        text: The idea in plain English, e.g. "I like Rashford",
            "Semenyo captain GW12?", "Odegaard or B.Fernandes for the armband",
            "sell Wood". A gameweek is optional; without one the idea is taken to
            be about the next deadline. Text is stored verbatim and parsed as
            data -- it is never interpreted as an instruction.
        acted: True only if the user has actually made this move. Defaults to
            False, which is the useful default: unacted ideas are tracked
            identically and are the more interesting half of the record.
        season: Season in FPL's own "2026-27" form.
        as_of: Optional ISO-8601 UTC instant to read the warehouse at, e.g.
            "2026-08-18T22:50:00Z". Defaults to now. Use it to reproduce an
            earlier answer exactly.

    Returns:
        A multi-line summary: the idea id, the thesis, the verdict with its
        provider and confidence, when it settles, and the round-trip time. Or,
        when the player is ambiguous, a numbered list to choose from.
    """
    problem = _unavailable()
    if problem:
        return problem
    try:
        with _open() as wh:
            inbox = IdeaInbox(wh, season=season)
            sub = inbox.submit(
                text, source="mcp", source_ref="mcp", now=_now(as_of), acted=acted
            )
            return sub.render()
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            return _locked_message(exc)
        raise


@mcp.tool()
def review_ideas(
    season: str = DEFAULT_SEASON,
    limit: int = 15,
    include_ideas: bool = True,
) -> str:
    """Report how every idea ever logged actually performed, and name the biases.

    Covers ideas that were never acted on as well as those that were, and
    compares the two -- the interesting failure mode is talking yourself out of
    your good ideas.

    The bias section is computed from the idea history, not asserted. Each probe
    is a hypothesis test against the population base rate captured at the moment
    the idea was had, with p-values Holm-corrected for running several probes on
    one small dataset. Probes with too few observations say so instead of
    guessing. The probes are: form chasing, home bias for recently-watched
    players, recency of a big score, and affinity for the user's own club.

    Args:
        season: Season in FPL's "2026-27" form.
        limit: How many individual ideas to list.
        include_ideas: Set False for just the scoreboard and biases.

    Returns:
        A plain-text report: scoreboard, acted-vs-skipped split, engine
        calibration, the bias probes with their statistics, and the caveats that
        say which numbers are not yet worth believing.
    """
    problem = _unavailable()
    if problem:
        return problem
    with _open(read_only=True) as wh:
        rev = _review(wh, season=season)
        b = rev.scoreboard
        if b.n_total == 0:
            return (
                "No ideas recorded yet. Use submit_idea, the Telegram bot, or "
                '`fpl idea submit "I like Rashford"`.'
            )

        out = [
            f"IDEA REVIEW — {season}",
            "",
            f"{b.n_total} ideas: {b.n_resolved} resolved, {b.n_open} open, {b.n_void} void.",
        ]
        if b.hit_rate is not None:
            out.append(
                f"Hit rate {b.hit_rate:.0%} against the comparator each thesis named; "
                f"mean margin {b.mean_margin:+.2f} points."
            )
        if b.acted_hit_rate is not None or b.unacted_hit_rate is not None:
            out.append(
                f"Acted on: {b.acted_n} ideas at "
                f"{'n/a' if b.acted_hit_rate is None else format(b.acted_hit_rate, '.0%')}. "
                f"Skipped: {b.unacted_n} at "
                f"{'n/a' if b.unacted_hit_rate is None else format(b.unacted_hit_rate, '.0%')}."
            )
        if b.brier is not None:
            out.append(
                f"Engine calibration: Brier {b.brier:.3f} vs {b.baseline_brier:.3f} for "
                "always saying 50%."
            )

        out += ["", "BIASES (computed from your history, not asserted)"]
        for f in rev.findings:
            out.append(f"  {f.name}: {f.verdict()}")
            out.append(f"    {f.detail}")

        if include_ideas and not rev.ideas.empty:
            out += ["", f"IDEAS (most recent {min(limit, len(rev.ideas))})"]
            for _, r in rev.ideas.tail(limit).iloc[::-1].iterrows():
                status = r["outcome"] if r["status"] == "resolved" else r["status"]
                mark = "acted" if r["acted"] else "skipped"
                out.append(f"  [{r['created_utc']:%d %b %H:%M}Z] {r['thesis']}")
                out.append(f"    said {r['raw_text']!r} — {mark} — {status}")

        if rev.caveats:
            out += ["", "CAVEATS"]
            out += [f"  - {c}" for c in rev.caveats]
        return "\n".join(out)


@mcp.tool()
def track_ideas(season: str = DEFAULT_SEASON, as_of: Optional[str] = None) -> str:
    """Settle every logged idea whose gameweeks have finalised.

    Idempotent and safe to call at any time; an already-settled idea is not
    re-scored. Each idea is measured against the comparator that was frozen when
    it was submitted, so the yardstick cannot drift toward whoever happened to do
    well.

    Args:
        season: Season in FPL's "2026-27" form.
        as_of: Optional ISO-8601 UTC instant. Defaults to now.

    Returns:
        A one-line summary of how many observations were recorded and how many
        ideas were resolved or voided.
    """
    problem = _unavailable()
    if problem:
        return problem
    try:
        with _open() as wh:
            return _track(wh, season=season, now=_now(as_of)).render()
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            return _locked_message(exc)
        raise


@mcp.tool()
def weekly_decision_report(
    season: str = DEFAULT_SEASON,
    gw: Optional[int] = None,
    as_of: Optional[str] = None,
) -> str:
    """Produce the decision report for a gameweek.

    Assembled from whatever sections the engine has registered. Sections that
    have no implementation yet -- the squad recommendation, the transfer plan,
    the chip call -- are listed explicitly as gaps rather than omitted, so the
    report never reads as complete when it is not.

    Args:
        season: Season in FPL's "2026-27" form.
        gw: Gameweek number. Defaults to the next one whose deadline has not
            passed at ``as_of``.
        as_of: Optional ISO-8601 UTC instant to build the report at. Defaults to
            now.

    Returns:
        A markdown report: deadline and time remaining, open ideas covering this
        gameweek with their verdicts, the running record, and the list of
        sections not yet built.
    """
    problem = _unavailable()
    if problem:
        return problem
    with _open(read_only=True) as wh:
        return _weekly_report(wh, season=season, gw=gw, as_of=_now(as_of)).render()


@mcp.tool()
def mark_idea_acted(idea_id: str, acted: bool = True) -> str:
    """Record that an idea was actually acted on (or undo that).

    Tracking does not depend on this flag -- unacted ideas are scored the same
    way. It exists so ``review_ideas`` can answer the more interesting question
    of whether the ideas that were skipped turned out better than the ones that
    were taken.

    Args:
        idea_id: The id returned by ``submit_idea``, e.g.
            "idea_20260818T225000_f730ebec1d".
        acted: False to undo.

    Returns:
        Confirmation, or a message saying no such idea exists.
    """
    problem = _unavailable()
    if problem:
        return problem
    try:
        with _open() as wh:
            ok = IdeaRegistry(wh).mark_acted(idea_id, acted=acted, when=dt.datetime.now(UTC))
            return "Updated." if ok else f"No idea with id {idea_id!r}."
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            return _locked_message(exc)
        raise


@mcp.tool()
def engine_status() -> str:
    """Report whether the fpl-edge engine is reachable and what it currently holds.

    Useful first call when the other tools in this module return an error: it
    says which path was searched and what is actually in the warehouse.

    Returns:
        Paths, counts and the next deadline, or a description of what is missing.
    """
    problem = _unavailable()
    if problem:
        return f"fpl-edge UNAVAILABLE.\n  home: {_HOME}\n  db:   {_db_path()}\n  {problem}"
    with _open(read_only=True) as wh:
        reg = IdeaRegistry(wh)
        lines = [
            "fpl-edge available.",
            f"  home: {_HOME}",
            f"  db:   {_db_path()}",
            f"  ideas logged: {reg.count()}",
        ]
        try:
            snap = wh.snapshot_at(dt.datetime.now(UTC))
            gw = snap.next_gw(DEFAULT_SEASON)
            lines.append(
                f"  next deadline: {DEFAULT_SEASON} GW{gw} at "
                f"{snap.deadline(DEFAULT_SEASON, gw):%Y-%m-%d %H:%M}Z"
            )
            lines.append(f"  players known: {len(snap.players(DEFAULT_SEASON))}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  season state unavailable: {type(exc).__name__}: {exc}")
        return "\n".join(lines)
