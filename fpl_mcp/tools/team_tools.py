"""
MCP tools for working with a specific FPL team.

These tools inspect the current or historical picks for a Fantasy Premier
League entry. The entry defaults to the owner's, read from
``fpl_edge.config.USER`` -- the single source every other module in this repo
uses for that number -- and can be overridden per call with ``team_id``.

Note that the FPL API exposes some endpoints without requiring
authentication. However, certain information may be limited or
subject to rate limits. These tools make best-effort queries and
format the results in a human-readable manner.

An element id that the bootstrap table does not know is reported as unknown,
with the raw id, rather than being skipped or given an invented name.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pandas as pd
import requests

# Absolute imports so that this module can be run from the project
# root using uv or python without a package context.  Avoid leading
# dots for relative imports.
from fpl_edge.config import USER
from fpl_mcp.utils import fpl_data  # type: ignore
from fpl_mcp.server import mcp  # type: ignore

#: Default entry to query: the owner's, from the engine's config rather than a
#: second hand-maintained copy of the number. Override per call with ``team_id``.
TEAM_ID: int = int(USER.entry_id)


def _lookup_element(elements_df: pd.DataFrame, elem_id: Any) -> Optional[pd.Series]:
    """The bootstrap row for ``elem_id``, or None when it is not in the table.

    ``DataFrame.loc`` raises ``KeyError`` on a missing label and, unlike a
    dict, has no ``.get``. This is the tolerant lookup the caller wanted:
    None means "this id is not in the table", and the caller must then SAY
    that rather than invent a player.
    """
    if elem_id is None:
        return None
    try:
        row = elements_df.loc[elem_id]
    except (KeyError, TypeError):
        return None
    if isinstance(row, pd.DataFrame):  # a duplicated id in the index
        row = row.iloc[0]
    return row


def _fetch_team_event_picks(team_id: int, gw: int) -> Dict[str, Any]:
    """Fetch the picks for a team in a given gameweek.

    Args:
        team_id: FPL entry/team identifier.
        gw: Gameweek number (1-38).

    Returns:
        JSON dictionary containing picks and chip usage.
    """
    endpoint = f"https://fantasy.premierleague.com/api/entry/{team_id}/event/{gw}/picks/"
    resp = requests.get(endpoint)
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def get_team_picks(gw: int, team_id: Optional[int] = None) -> str:
    """Retrieve the squad picks for a team in a specific gameweek.

    Args:
        gw: The gameweek number (1 through 38). Use the current
            gameweek to see your latest picks.
        team_id: FPL entry id to inspect. Defaults to the owner's entry.

    Returns:
        A formatted listing of players selected for that gameweek,
        including captaincy and multiplier information. The output
        includes each player's position, team, price and total
        points to aid in analysis. A pick whose element id is absent
        from the bootstrap table is listed as unknown with its raw id,
        never dropped and never given a made-up name.

    Example:

        ``get_team_picks(3)``

    ````
    Team picks for GW3:
    ============================================
    Position  Player               Team     Price   Pts  Mult  C/V
    ------------------------------------------------------------
    GK        Ederson             MCI      5.5    12    1
    DEF       Alexander-Arnold    LIV      8.0    15    2      C
    ...
    ````
    """
    entry_id = int(team_id) if team_id is not None else TEAM_ID
    data = _fetch_team_event_picks(entry_id, gw)
    picks = data.get("picks", [])
    # Load elements DataFrame to map ids to names and positions
    elements_df = fpl_data.get_elements_df()
    # Create mapping of element id to row for quick lookup
    elements_df = elements_df.set_index("id")

    # Build table rows
    rows = []
    unresolved = []
    for pick in picks:
        elem_id = pick.get("element")
        player = _lookup_element(elements_df, elem_id)
        mult = pick.get("multiplier", 1)
        is_cap = "C" if pick.get("is_captain", False) else ("V" if pick.get("is_vice_captain", False) else "")
        if player is None:
            # The id is not in the bootstrap table. Report the pick with what
            # the FPL payload actually told us and mark the rest unknown --
            # a squad rendered 14-strong with no explanation, or a plausible
            # invented name, would both be worse than saying so.
            unresolved.append(elem_id)
            rows.append({
                "Position": "?",
                "Player": f"unknown (element {elem_id})",
                "Team": "unknown",
                "Price": "?",
                "Pts": "?",
                "Mult": mult,
                "C/V": is_cap,
            })
            continue
        rows.append({
            "Position": player["position"],
            "Player": f"{player['first_name']} {player['second_name']}",
            "Team": player["team_name"],
            "Price": f"{player['now_cost'] / 10.0:.1f}",
            "Pts": str(player["total_points"]),
            "Mult": mult,
            "C/V": is_cap,
        })
    if not rows:
        return f"No picks found for team {entry_id} in gameweek {gw}."
    # Sort by position order: GK, DEF, MID, FWD then by multiplier descending.
    # Unknown positions sort last rather than being silently interleaved.
    order = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}
    rows.sort(key=lambda r: (order.get(r["Position"], 99), -r["Mult"]))
    # Build header and table string
    header = f"Team picks for GW{gw} (team {entry_id}):\n"
    header += "Position  Player                        Team               Price  Pts  Mult  C/V\n"
    header += "-----------------------------------------------------------------------------\n"
    lines = []
    for r in rows:
        lines.append(
            f"{r['Position']:<8} {r['Player']:<28} {r['Team']:<18} {r['Price']:<5} {r['Pts']:<4} {r['Mult']:<4} {r['C/V']}"
        )
    out = header + "\n".join(lines)
    if unresolved:
        out += (
            f"\n\nNote: {len(unresolved)} element id(s) "
            f"({', '.join(str(u) for u in unresolved)}) are not in the current "
            f"bootstrap data, so their name, club, price and points are unknown. "
            f"Element ids are reassigned every season -- a stale cache or a "
            f"different season's picks is the usual cause."
        )
    return out
