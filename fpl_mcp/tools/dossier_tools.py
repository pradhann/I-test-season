"""
MCP tools for the fpl-edge player dossier and its news/tactical intel.

``player_dossier`` is the chat-side surface of the same
``fpl_edge.interfaces.dossier.build`` that backs ``fpl dossier <name>`` and the
Telegram reply. One implementation, three renderers -- so an answer given here
cannot drift from the one the CLI gives for the same player at the same instant.

Two design points worth stating, because both are load-bearing for how a model
should read the output:

* **Gaps are data.** Every section the dossier is supposed to contain appears in
  the result either with a ``body`` or with a ``gap`` explaining why it is
  missing. A section is never silently dropped, so "no anytime-scorer odds were
  ingested for this fixture" cannot be mistaken for "the odds say nothing
  interesting". When summarising this for the user, report the gaps.
* **Everything is read at one instant.** Pass ``as_of`` to reconstruct what was
  knowable at a past deadline; omit it for now. The warehouse is opened
  READ-ONLY, so these tools work while an ingest, a backtest or the Telegram bot
  holds the single DuckDB writer lock.

This module follows ``edge_tools.py``'s pattern for locating the engine and
degrades the same way: a missing or broken checkout returns an explanatory
string from each tool rather than raising at import, which would take the whole
fpl_mcp server -- and every existing tool -- down with it.

Configuration, both optional and shared with ``edge_tools``:

* ``FPL_EDGE_HOME`` -- path to the fpl-edge checkout. Defaults to a sibling
  directory of this repository named ``i-test-season``.
* ``FPL_EDGE_DB`` -- path to the DuckDB warehouse.

Security note. ``name`` is free text that ultimately originates from a chat. It
is treated as DATA: passed to the engine's fuzzy player resolver, string-matched
against a player list, and never executed, never interpolated into SQL, and
never used to choose a code path. When two players match it, the tool returns a
question rather than guessing -- a mis-resolved player produces a confident
dossier about the wrong person, which is worse than an extra round trip.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

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
    from fpl_edge.interfaces import dossier as _dossier  # type: ignore
    from fpl_edge.intel.items import IntelKind as _IntelKind  # type: ignore
    from fpl_edge.intel.store import IntelStore as _IntelStore  # type: ignore
    from fpl_edge.store import Warehouse as _Warehouse  # type: ignore
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
        raise ValueError("as_of must carry a timezone, e.g. 2026-08-21T17:30:00Z")
    return parsed.astimezone(UTC)


# -----------------------------------------------------------------------------
# Tools


@mcp.tool()
def player_dossier(
    name: str,
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
    gw: Optional[int] = None,
    horizon_gws: int = 5,
    simulate: bool = False,
    text: bool = False,
) -> Any:
    """Everything the fpl-edge engine knows about one player, in one view.

    Use this whenever the user names a player and wants to know about him --
    "what do you think of Semenyo?", "is Rashford worth it?", "I like Gyokeres".

    Covers, in one call: price and FPL's own price-change pressure; ownership and
    effective ownership; the projected points distribution from the engine's
    Monte Carlo points model; minutes and rotation risk; upcoming fixture
    difficulty from the engine's OWN fitted Dixon-Coles ratings rather than FPL's
    published FDR colours; xG and xA per 90; set-piece and penalty duty plus any
    detected change to it; defensive-contribution likelihood; the bookmakers'
    anytime-scorer price; injury news WITH the timestamp FPL published it;
    out-of-position and formation signals; press-conference links; and where the
    engine's goal model disagrees with the betting market.

    IMPORTANT for summarising: the result lists every expected section. A section
    with ``"body": null`` carries a ``"gap"`` string saying why there is no data.
    Report those gaps to the user -- do not treat a gap as a negative finding.
    The ``gaps`` array names them.

    Args:
        name: Player name. Fuzzy and forgiving -- surnames, nicknames ("kdb"),
            and misspellings ("rashfrod") all resolve. If two players match, the
            tool returns a question with candidates instead of guessing.
        season: FPL season, e.g. "2026-27".
        as_of: ISO UTC instant to read the warehouse at, e.g.
            "2026-08-21T17:30:00Z". Omit for now. Everything is point-in-time
            filtered, so a past instant reconstructs what was knowable then.
        gw: Gameweek to project. Defaults to the next open one.
        horizon_gws: How many fixtures ahead to rate.
        simulate: Run the points model live instead of reading the cached
            projection. Accurate to the instant but takes about 95 seconds
            because it refits the minutes model; leave False for chat.
        text: Return the rendered plain-text dossier instead of structured
            sections. Useful when the user wants to read it verbatim.

    Returns:
        A dict with ``sections`` (each with ``key``, ``title``, ``body``,
        ``gap``), ``gaps``, ``warnings`` and the resolved player identity -- or,
        when the name is ambiguous, ``{"ambiguous": true, "question": ...,
        "candidates": [...]}``. With ``text=True``, the rendered string.
    """
    problem = _unavailable()
    if problem:
        return problem
    try:
        when = _now(as_of)
    except ValueError as exc:
        return str(exc)

    kwargs: Dict[str, Any] = {"horizon_gws": int(horizon_gws), "simulate": bool(simulate)}
    if gw is not None:
        kwargs["gw"] = int(gw)

    # Read-only: the engine's ingest, backtests and Telegram bot all write to
    # this same DuckDB file and it permits one writer. A dossier that fails
    # because a simulation is running is a dossier that fails exactly when it is
    # wanted.
    try:
        with _Warehouse(_db_path(), read_only=True) as wh:
            built, clarification = _dossier.build(
                wh, name, season=season, as_of=when, **kwargs
            )
    except Exception as exc:  # noqa: BLE001
        return f"Could not build the dossier: {type(exc).__name__}: {exc}"

    if built is None:
        if clarification is None:
            return f"Could not resolve {name!r} to a player."
        return {
            "ambiguous": clarification.kind == "ambiguous",
            "reason": clarification.kind,
            "question": clarification.question,
            "candidates": [
                {"code": int(c.code), "label": c.label, "hint": c.hint, "score": c.score}
                for c in clarification.candidates
            ],
        }
    return built.render() if text else built.to_dict()


@mcp.tool()
def player_intel(
    name: Optional[str] = None,
    kind: Optional[str] = None,
    hours: float = 72.0,
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
    limit: int = 25,
) -> Any:
    """Recent news, press-conference links and tactical signals, with timestamps.

    The lighter counterpart to ``player_dossier``: use it for "what's the latest
    news?", "any injury updates?", "has anyone's penalty duty changed?".

    Every item carries ``published_at`` -- the instant the world could have known
    it, taken from FPL's own ``news_added`` for availability -- and ``observed_at``,
    the instant this pipeline saw it. Items published after ``as_of`` are
    invisible by construction, so this is safe to use when reconstructing a past
    deadline.

    Args:
        name: Restrict to one player (fuzzy). Omit for league-wide news.
        kind: One of "availability", "press_conference", "set_piece",
            "out_of_position", "formation", "source_probe". Omit for all.
        hours: Only items published this recently before ``as_of``.
        season: FPL season.
        as_of: ISO UTC instant. Omit for now.
        limit: Maximum items.

    Returns:
        A dict with ``items`` and, when nothing matched, an explicit ``note``
        saying the store was queried and the filter matched nothing -- which is
        different from the store being empty.
    """
    problem = _unavailable()
    if problem:
        return problem
    try:
        when = _now(as_of)
    except ValueError as exc:
        return str(exc)
    try:
        selected_kind = _IntelKind(kind) if kind else None
    except ValueError:
        return (
            f"Unknown kind {kind!r}. Valid: "
            + ", ".join(k.value for k in _IntelKind)
        )

    try:
        with _Warehouse(_db_path(), read_only=True) as wh:
            store, exists = _IntelStore.open_reader(wh)
            if not exists:
                return (
                    "The intel tables do not exist in this warehouse yet. Run "
                    "`uv run fpl intel collect` in the engine repository once to "
                    "create and fill them."
                )
            code = None
            if name:
                snap = wh.snapshot_at(when)
                code, clarification, _ = _dossier.resolve(snap, name, season=season)
                if code is None:
                    return {
                        "ambiguous": bool(clarification and clarification.kind == "ambiguous"),
                        "question": clarification.question if clarification else "",
                        "candidates": [
                            {"code": int(c.code), "label": c.label, "hint": c.hint}
                            for c in (clarification.candidates if clarification else ())
                        ],
                    }
            items = store.items(
                when,
                player_code=int(code) if code is not None else None,
                kind=selected_kind,
                season=season,
                limit=int(limit) * 4,
            )
    except Exception as exc:  # noqa: BLE001
        return f"Could not read intel: {type(exc).__name__}: {exc}"

    cutoff = when - dt.timedelta(hours=float(hours))
    fresh = [i for i in items if i.published_at >= cutoff][: int(limit)]
    if not fresh:
        return {
            "as_of": when.isoformat(),
            "items": [],
            "note": (
                f"No intel published in the {hours:.0f}h before {when:%Y-%m-%d %H:%M}Z "
                f"matching this filter. The store was queried and the point-in-time "
                f"filter matched nothing -- this is a result, not an error. "
                f"{len(items)} item(s) exist outside the time window."
            ),
        }
    return {
        "as_of": when.isoformat(),
        "items": [
            {
                "published_at": i.published_at.isoformat(),
                "observed_at": i.observed_at.isoformat(),
                "lag_hours": round(i.lag.total_seconds() / 3600.0, 2),
                "kind": str(i.kind),
                "headline": i.headline,
                "body": i.body,
                "source": i.source,
                "url": i.source_url,
                "player_code": i.player_code,
                "team_code": i.team_code,
                "confidence": i.confidence,
            }
            for i in fresh
        ],
    }


@mcp.tool()
def set_piece_changes(
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
    min_goals_per_game: float = 0.02,
    limit: int = 25,
) -> Any:
    """Detected changes in penalty, free-kick and corner duty, valued in goals.

    Penalty duty is worth roughly 0.10 goals per game to a first-choice taker --
    close to four goals over a season, more than the gap between most price
    tiers, and the fact most likely to change without the price moving. This
    tool surfaces every move the engine has detected by comparing consecutive
    observations of FPL's own stated order.

    Args:
        season: FPL season.
        as_of: ISO UTC instant. Changes detected after it are invisible.
        min_goals_per_game: Absolute threshold. 0.02 hides third-to-fourth
            shuffles while always showing a move into or out of first choice.
        limit: Maximum changes.

    Returns:
        A dict with ``changes``, each carrying the two observation instants it
        was derived from, or a ``note`` when none are visible.
    """
    problem = _unavailable()
    if problem:
        return problem
    try:
        when = _now(as_of)
    except ValueError as exc:
        return str(exc)
    try:
        with _Warehouse(_db_path(), read_only=True) as wh:
            store, exists = _IntelStore.open_reader(wh)
            if not exists:
                return (
                    "The intel tables do not exist in this warehouse yet. Run "
                    "`uv run fpl intel collect` in the engine repository."
                )
            changes = store.changes(when, limit=int(limit) * 8)
    except Exception as exc:  # noqa: BLE001
        return f"Could not read set-piece changes: {type(exc).__name__}: {exc}"

    big = [
        c for c in changes
        if abs(c.delta_goals_per_game) >= float(min_goals_per_game)
    ]
    big.sort(key=lambda c: (-abs(c.delta_goals_per_game), c.detected_at))
    big = big[: int(limit)]
    if not big:
        return {
            "as_of": when.isoformat(),
            "changes": [],
            "note": (
                f"No set-piece change above {min_goals_per_game:.3f} goals/game is "
                f"visible at {when:%Y-%m-%d %H:%M}Z. The detector compares consecutive "
                "FPL observations and would have recorded a move, so this is a finding "
                "rather than a missing feed."
            ),
        }
    return {
        "as_of": when.isoformat(),
        "changes": [
            {
                "detected_at": c.detected_at.isoformat(),
                "prior_observation": c.prior_as_of.isoformat(),
                "player_code": c.code,
                "team_code": c.team_code,
                "duty": str(c.duty),
                "order_before": c.ord_before,
                "order_after": c.ord_after,
                "delta_goals_per_game": round(c.delta_goals_per_game, 4),
                "headline": c.headline,
                "is_promotion": c.is_promotion,
            }
            for c in big
        ],
    }
