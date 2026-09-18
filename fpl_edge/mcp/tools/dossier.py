"""The five player tools: the dossier, form, the Understat profile, the radar
and the intel feed.

Every one takes ``player`` as free text and resolves it in the adapter before
the panel runs, so an ambiguous name costs a round trip rather than a panel
budget, and the panel itself takes the stable cross-season player code.

``player_profile`` owns the one call in this server that crosses the ingest
boundary: when the warehouse holds no Understat rows for a player, it fetches
them through ``fpl_edge/ingest/understat.py``, which is the same call the web
route makes and the only exception to the rule that tools do not fetch.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp import context
from fpl_edge.mcp.adapter import panel_call, refusal, resolve_player
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"

#: Every kind of intel item the store holds, for the tool's own argument.
INTEL_KINDS = (
    "availability", "press_conference", "set_piece", "out_of_position",
    "formation", "source_probe",
)


@mcp.tool()
def player(
    player: str,
    season: str = SEASON_DEFAULT,
    gw: int | None = None,
    horizon_gws: int = 5,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Everything the engine knows about one player, in sixteen sections.

    Identity and availability, price and price pressure, ownership and
    effective ownership, the projected points distribution, minutes and
    rotation risk, fixtures on this engine's own ratings, underlying xG and xA
    per 90, set-piece and penalty duty with any change to it, defensive
    contribution likelihood, the bookmakers' anytime-scorer price, injury news
    with its timestamp, out-of-position and formation signals, press coverage,
    what creators are saying, what skilled managers own, and where this model
    and the market disagree.

    A section with no data carries the reason it is empty rather than
    disappearing. Report those as not available and quote the reason; do not
    substitute another source or an estimate.

    The projection section reads the cached artefact. There is no live refit
    here: it takes about 95 seconds against a 10 second budget.

    Args:
        player: Player name, free text. An ambiguous name comes back as a
            candidate list rather than a guess.
        season: FPL season, for example "2026-27".
        gw: Gameweek. Omit for the next open one.
        horizon_gws: How many gameweeks ahead the fixtures section rates.
        as_of: ISO-8601 instant carrying a timezone. Omit for now.

    Returns:
        The envelope: result, provenance, budget, and gap listing the sections
        that are empty. If gap is present, quote its reason.
    """
    code, refused = resolve_player("player", player, season=season, as_of=as_of)
    if refused is not None:
        return refused
    return panel_call("player", "player_dossier", {
        "code": code, "season": season, "gw": gw,
        "horizon_gws": horizon_gws, "as_of": as_of,
    })


@mcp.tool()
def player_form(
    player: str,
    last_k: int = 6,
    season: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """One player's most recent settled gameweeks, with the season of each row.

    Points, minutes, goals, assists, FPL's own expected numbers, bonus and BPS
    per fixture, with the opponent and the venue. A row appears only once its
    gameweek's points were finalised, so early in a season the recent form
    comes from the previous season and the payload says so per row rather than
    pretending.

    Args:
        player: Player name, free text. Matched on the stable player code, so
            history follows a player across a transfer and a season boundary.
        last_k: How many settled gameweeks, 1 to 38.
        season: Restrict to one season. Omit to walk backwards across seasons
            until the row cap is filled.
        as_of: ISO-8601 instant carrying a timezone. Omit for now.

    Returns:
        The envelope: result, provenance, budget, and gap when nothing is
        settled yet. If gap is present, quote its reason.
    """
    code, refused = resolve_player(
        "player_form", player, season=season or SEASON_DEFAULT, as_of=as_of,
    )
    if refused is not None:
        return refused
    return panel_call("player_form", "player_form", {
        "code": code, "last_k": last_k, "season": season, "as_of": as_of,
    })


@mcp.tool()
def player_profile(
    player: str,
    season: str = SEASON_DEFAULT,
    fetch_if_missing: bool = True,
) -> dict[str, Any]:
    """One player's Understat season: shots, xG against actual, the minutes pattern.

    Shot volume, xG against goals and xA against assists with the finishing
    gap labelled as luck rather than as skill, key passes, and the starts
    against cameos pattern. These are Understat's shot model, not FPL points,
    and the payload repeats that.

    When the warehouse holds nothing for this player, this performs the one
    sanctioned on-demand fetch from understat.com through the engine's ingest
    module, stores it append-only, and reads again. Pass
    fetch_if_missing=False to report the gap instead of fetching.

    Args:
        player: Player name, free text.
        season: FPL season, for example "2026-27".
        fetch_if_missing: Whether to fetch when the warehouse holds nothing.

    Returns:
        The envelope: result, provenance, budget, and gap when no profile is
        held and none was fetched. If gap is present, quote its reason.
    """
    code, refused = resolve_player("player_profile", player, season=season)
    if refused is not None:
        return refused

    envelope = panel_call("player_profile", "player_profile", {
        "code": code, "season": season,
    })
    if not envelope.get("ok") or envelope.get("gap") is None or not fetch_if_missing:
        return envelope

    from fpl_edge.ingest import understat as understat_mod

    try:
        summary = understat_mod.fetch_player_profile(
            int(code), season, db=context.db_path(),
        )
    except understat_mod.UnresolvedPlayerError as exc:
        return refusal(
            "player_profile",
            f"refused to fetch: {exc}. The resolver accepts an exact or a "
            f"containment name match and never an edit distance, so nothing "
            f"was stored.",
        )
    except understat_mod.UnderstatError as exc:
        from fpl_edge.mcp.adapter import error

        return error("player_profile", f"Understat fetch failed: {exc}",
                     kind=type(exc).__name__, panel="player_profile")

    fetched = panel_call("player_profile", "player_profile", {
        "code": code, "season": season,
    })
    fetched["fetched"] = {
        "rows_appended": summary["rows_appended"],
        "rows_total": summary["rows_total"],
        "understat_id": summary["understat_id"],
        "resolved_basis": summary["resolved_basis"],
        "note": "fetched from understat.com during this call and stored "
                "append-only, then read back through the panel.",
    }
    return fetched


@mcp.tool()
def player_radar(player: str, season: str = SEASON_DEFAULT) -> dict[str, Any]:
    """One player's per-90 percentiles against same-position peers.

    The pizza chart the player drawer draws: each per-90 rate as a percentile
    within the same position, over peers who clear a minutes floor. A
    percentile is a rank within that peer set and nothing else, so it says who
    a player looks like rather than what he will score.

    Args:
        player: Player name, free text.
        season: FPL season, for example "2026-27".

    Returns:
        The envelope: result, provenance, budget, and gap when the player has
        too few minutes to rank. If gap is present, quote its reason.
    """
    code, refused = resolve_player("player_radar", player, season=season)
    if refused is not None:
        return refused
    return panel_call("player_radar", "player_radar", {
        "code": code, "season": season,
    })


@mcp.tool()
def player_intel(
    player: str | None = None,
    kind: str | None = None,
    hours: float = 72.0,
    season: str = SEASON_DEFAULT,
    as_of: str | None = None,
    limit: int = 25,
    include_changes: bool = True,
    min_goals_per_game: float = 0.02,
) -> dict[str, Any]:
    """Recent news, press coverage and detected set-piece moves, with timestamps.

    The lighter counterpart to player: use it for "what is the latest news",
    "any injury updates", "has anyone's penalty duty changed". Every item
    carries when the world could have known it and when this pipeline saw it,
    so an item that reached the warehouse nine hours late is visibly different
    from one that arrived in twelve minutes.

    Set-piece moves come from the detector that compares consecutive
    observations of FPL's own stated order, valued in goals per game. Penalty
    duty is worth roughly 0.10 goals per game to a first-choice taker, which
    is more than the gap between most price tiers and the fact most likely to
    change without the price moving.

    An empty result distinguishes a quiet window from a missing feed. Quote
    which one it says.

    Args:
        player: Restrict to one player, free text. Omit for league-wide.
        kind: One of availability, press_conference, set_piece,
            out_of_position, formation, source_probe. Omit for every kind.
        hours: Only items published this recently before the instant.
        season: FPL season, for example "2026-27".
        as_of: ISO-8601 instant carrying a timezone. Items published after it
            are invisible. Omit for now.
        limit: Row cap, applied to items and to changes separately.
        include_changes: Whether to read the set-piece detector too.
        min_goals_per_game: Threshold on a duty change. The default hides a
            third to fourth shuffle and always shows a move into or out of
            first choice.

    Returns:
        The envelope: result, provenance, budget, and gap when nothing
        matched. If gap is present, quote its reason.
    """
    code = None
    if player:
        code, refused = resolve_player(
            "player_intel", player, season=season, as_of=as_of,
        )
        if refused is not None:
            return refused
    if kind is not None and kind not in INTEL_KINDS:
        return refusal(
            "player_intel",
            f"kind must be one of {', '.join(INTEL_KINDS)}, not {kind!r}.",
        )
    return panel_call("player_intel", "player_intel", {
        "code": code, "kind": kind, "hours": hours, "season": season,
        "as_of": as_of, "limit": limit, "include_changes": include_changes,
        "min_goals_per_game": min_goals_per_game,
    })
