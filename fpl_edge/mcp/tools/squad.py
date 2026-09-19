"""``my_squad`` and ``brief``: the user's own team, and the dashboard.

Both panels take an entry id and neither tool does. The id comes from the
resolved user context, so a caller cannot ask this server for somebody else's
squad by passing a number.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp import context
from fpl_edge.mcp.adapter import panel_call
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"


@mcp.tool()
def my_squad(season: str = SEASON_DEFAULT) -> dict[str, Any]:
    """The user's own 15, priced and flagged, with where the squad was read from.

    The same payload the Squad panel draws: each player with price, position,
    club, availability and the multiplier when the read path carried one. The
    payload names its source, which is the private FPL endpoint, the public
    picks or a manually entered 15, and a pre-deadline read carries no
    multiplier because FPL publishes those only once the gameweek locks.

    The FPL entry is the one this server was started for. It is not a
    parameter, so there is no way to ask this tool for another manager's team.

    Args:
        season: FPL season, for example "2026-27".

    Returns:
        The envelope: result, provenance, budget, and gap when the squad could
        not be read. If gap is present, quote its reason.
    """
    return panel_call("my_squad", "squad_overview", {
        "season": season,
    })


@mcp.tool()
def brief(season: str = SEASON_DEFAULT) -> dict[str, Any]:
    """The dashboard's own brief: alerts, gated tiles, the watch log, the solve.

    What the Dashboard page shows, selected from the source panels under the
    anti-drift contract with every threshold echoed in the payload. Use it for
    "what should I be looking at" and "what changed", and quote the thresholds
    it carries rather than restating an alert as a recommendation.

    This replaces the old weekly_decision_report, and it prints the same
    alerts and tiles the dashboard does, from the same run.

    Args:
        season: FPL season, for example "2026-27".

    Returns:
        The envelope: result, provenance, budget, and gap when a source panel
        had nothing. If gap is present, quote its reason.
    """
    return panel_call("brief", "dashboard_brief", {
        "season": season,
    })
