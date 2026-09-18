"""``projections``: projected points, where the sources disagree, one player.

One tool, three modes, over one panel. The old server had two tools that read
the same rows for the same gameweek and sorted them differently, which is one
question and one answer.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp.adapter import panel_call, resolve_player
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"

#: What each mode asks the panel for. ``player`` adds the per-source
#: breakdown for the resolved code.
MODES = ("xpts", "spread", "player")


@mcp.tool()
def projections(
    mode: str = "xpts",
    season: str = SEASON_DEFAULT,
    gw: int | None = None,
    player: str | None = None,
    position: int | None = None,
    team: str | None = None,
    limit: int = 50,
    max_price: float | None = None,
    min_p_appear: float | None = None,
    weighting: str = "equal",
) -> dict[str, Any]:
    """Projected points, joined to live price and ownership. Three modes.

    mode="xpts" ranks by the consensus projection. mode="spread" ranks by how
    far the sources disagree, which is where a projection is least reliable
    and where a differential most often hides. mode="player" resolves one name
    and adds the per-source breakdown for that player across the gameweek and
    the next four.

    The consensus is the unweighted mean across providers by default. Set
    weighting="earned" for the blend that weights each provider by its
    measured record. They are different numbers and the payload names which
    one it carried.

    A projection is a model output, not a price and not a promise. Quote it
    with the spread beside it.

    Args:
        mode: "xpts", "spread" or "player".
        season: FPL season, for example "2026-27".
        gw: Gameweek. Omit for the solved-artefact view rather than a
            provider gameweek view.
        player: Required for mode="player". Free text; an ambiguous name comes
            back as a candidate list rather than a guess.
        position: 1 GKP, 2 DEF, 3 MID, 4 FWD. Omit for all.
        team: Club short name, for example "ARS".
        limit: Rows to return, 1 to 800.
        max_price: Drop players above this price in millions.
        min_p_appear: Drop players whose appearance probability is below this
            or unknown.
        weighting: "equal" or "earned".

    Returns:
        The envelope: result, provenance, budget, and gap when no source
        projects this gameweek. If gap is present, quote its reason.
    """
    if mode not in MODES:
        from fpl_edge.mcp.adapter import refusal

        return refusal(
            "projections",
            f"mode must be one of {', '.join(MODES)}, not {mode!r}.",
        )

    detail_code = None
    if mode == "player":
        if not player:
            from fpl_edge.mcp.adapter import refusal

            return refusal(
                "projections",
                'mode="player" needs a player name to resolve.',
            )
        detail_code, refused = resolve_player(
            "projections", player, season=season,
        )
        if refused is not None:
            return refused

    return panel_call("projections", "projection_table", {
        "season": season,
        "gw": gw,
        "sort": "spread" if mode == "spread" else "xpts",
        "position": position,
        "team": team,
        "limit": limit,
        "max_price": max_price,
        "min_p_appear": min_p_appear,
        "detail_code": detail_code,
        "weighting": weighting,
    })
