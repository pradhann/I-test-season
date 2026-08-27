"""
Tools for analysing Fantasy Premier League experts' teams.

This module defines a small suite of general‑purpose functions for working
with well‑known FPL managers ("experts").  It maintains a mapping of
expert names to their FPL entry (team) IDs and exposes functions to
summarise their squad selections, recent transfers and historical
performance.  The goal is to provide abstract, composable building
blocks that allow a language model to answer questions such as:

* Which players are currently owned by multiple experts?
* What transfers did FPL Harry make in the last few gameweeks?
* How has FPL Raptor performed historically and what chips have they used?

The functions in this module use only publicly available endpoints of
the official FPL API and therefore do not require authentication.

"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import requests
import pandas as pd

from fpl_mcp.utils import fpl_data  # type: ignore
from fpl_mcp.server import mcp  # type: ignore


def _lookup_element(elements_df: pd.DataFrame, elem_id: Any) -> Optional[pd.Series]:
    """The bootstrap row for ``elem_id``, or None when it is not in the table.

    ``DataFrame.loc`` raises ``KeyError`` on a missing label and, unlike a
    dict, has no ``.get``. This is the tolerant lookup the caller wanted:
    None means "this id is not in the table", and the caller must then SAY
    that rather than invent a player or a price.
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


# Mapping of expert display names to their FPL entry IDs.
# You can extend this dictionary as new experts are added.  Names are
# case‑insensitive when looked up in the public tools.
EXPERTS: Dict[str, int] = {
    "FPL Focal": 200,
    "FPL Harry": 1320,
    "FPL Raptor": 1587,
    "FPL Pickle": 14501,
    "FPL Mate": 16267,
    "Ben Crellin": 6586,
    "Az Phillips": 441,
    "Kelly Somers": 1924811,
    "Julien Laurens": 1514450,
    "Sam Bonfield": 260,
    "Lee Bonfield": 341,
    "Holly Shand": 135,
    "Ian Irwing": 7577129,
    "FPL Sonaldo": 16725,
    "Pras": 3570,
    "Gianni Buttice": 17614,
    "BigMan Bakar": 963,
    "Yelena": 251,
    "Stormzy": 698910,
    "Chunkz": 2253812,
}


def _resolve_expert(name_or_id: str) -> Optional[Tuple[str, int]]:
    """Resolve an expert name or numeric string to a (name, id) tuple.

    Args:
        name_or_id: Either an expert's display name or their entry id as a string.

    Returns:
        A tuple of the canonical display name and numeric entry id, or None if
        no match is found.
    """
    # Try numeric conversion first
    try:
        num = int(name_or_id)
        # Find matching name in the EXPERTS mapping if possible
        for n, i in EXPERTS.items():
            if i == num:
                return n, i
        return str(num), num
    except Exception:
        # Lookup by case‑insensitive name
        lookup = {k.lower(): (k, v) for k, v in EXPERTS.items()}
        return lookup.get(name_or_id.strip().lower())


def _get_current_gameweek() -> int:
    """Return the current gameweek number according to the bootstrap data.

    If the current gameweek cannot be determined (e.g. off‑season), returns 1.
    """
    data = fpl_data.get_bootstrap_data(force_refresh=False)
    events = data.get("events", [])
    # Look for the current event
    for event in events:
        if event.get("is_current"):
            return int(event.get("id"))
    # If no current event, use the next event
    for event in events:
        if event.get("is_next"):
            return int(event.get("id"))
    # Fall back to the first event
    return 1


def _fetch_team_picks(manager_id: int, gw: int) -> Dict[str, object]:
    """Fetch the picks for a manager in a given gameweek.

    Args:
        manager_id: The FPL entry ID of the manager.
        gw: Gameweek number (1–38).

    Returns:
        A JSON dictionary with the picks and chip usage.
    """
    url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/event/{gw}/picks/"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()


def _fetch_transfers(manager_id: int) -> List[Dict[str, object]]:
    """Fetch all transfers made by a manager in the current season.

    Args:
        manager_id: The FPL entry ID of the manager.

    Returns:
        A list of transfer dicts, each containing keys such as
        ``element_in``, ``element_out``, ``event`` and ``time``.
    """
    url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/transfers/"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()  # type: ignore[return-value]


def _fetch_manager_history(manager_id: int) -> Dict[str, object]:
    """Fetch the historical performance of a manager.

    Args:
        manager_id: The FPL entry ID of the manager.

    Returns:
        A JSON dictionary with keys such as ``current``, ``past`` and ``chips``.
    """
    url = f"https://fantasy.premierleague.com/api/entry/{manager_id}/history/"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.json()  # type: ignore[return-value]


@mcp.tool()
def get_expert_teams_summary(gw: Optional[int] = None, experts: Optional[List[str]] = None) -> str:
    """Summarise which players are currently owned by selected experts.

    Given a list of expert names (or None for all) and an optional gameweek,
    this function fetches each manager's picks and builds a cross‑tabulation
    of players to the experts who own them.  Players are sorted in descending
    order of how many experts own them.  The output lists each player with
    their position, team name, price (in £m) and the experts who have them.

    Args:
        gw: The gameweek to fetch picks for.  If omitted, the current gameweek
            is used (determined from the bootstrap data).
        experts: Optional list of expert names or entry IDs to include.  If
            None, all experts in the mapping are used.

    Returns:
        A multi‑line string summarising player ownership across the experts.
    """
    # Determine gameweek
    gameweek = gw or _get_current_gameweek()
    # Determine which experts to use
    if experts:
        names_ids: List[Tuple[str, int]] = []
        for ex in experts:
            resolved = _resolve_expert(str(ex))
            if resolved:
                names_ids.append(resolved)
    else:
        names_ids = list((name, eid) for name, eid in EXPERTS.items())
    if not names_ids:
        return "No experts found. Please provide valid names or IDs."
    # Load elements dataframe for id→name mapping
    elements_df = fpl_data.get_elements_df()
    elements_df = elements_df.set_index("id")
    # Build a mapping of player id → list of expert names who own them
    ownership: Dict[int, List[str]] = {}
    for name, eid in names_ids:
        try:
            data = _fetch_team_picks(eid, gameweek)
        except Exception:
            continue  # Skip if fetch fails
        picks = data.get("picks", [])
        for pick in picks:
            elem_id = pick.get("element")
            if elem_id is None:
                continue
            ownership.setdefault(elem_id, []).append(name)
    if not ownership:
        return f"No picks found for the selected experts in gameweek {gameweek}."
    # Build rows with player info and owners
    rows = []
    for pid, owners in ownership.items():
        if pid not in elements_df.index:
            continue
        player = elements_df.loc[pid]
        rows.append({
            "player_name": f"{player['first_name']} {player['second_name']}",
            "team": player.get("team_name", ""),
            "position": player.get("position", ""),
            "price_m": player.get("now_cost", 0) / 10.0,
            "owned_by": ", ".join(sorted(owners)),
            "count": len(owners),
        })
    # Sort by number of owners descending then by player name
    rows.sort(key=lambda r: (-r["count"], r["player_name"]))
    # Build output string
    header = f"Expert ownership summary for GW{gameweek}:\n"
    header += f"{'Player':<25} {'Team':<20} {'Pos':<4} {'Price':<5} Owned by\n"
    header += "-" * 80 + "\n"
    lines = []
    for r in rows:
        lines.append(
            f"{r['player_name']:<25} {r['team']:<20} {r['position']:<4} {r['price_m']:<5.1f} {r['owned_by']}"
        )
    return header + "\n".join(lines)


@mcp.tool()
def get_expert_transfers(expert: str, last_n: int = 5) -> str:
    """Retrieve the latest transfers for a given expert.

    This function fetches all transfers made by the specified manager during the
    current season and returns the most recent ``last_n`` entries.  Each
    transfer is reported with the gameweek, incoming player, outgoing player and
    the cost difference.  If the expert name or ID is not recognised, a
    helpful error message is returned.

    Args:
        expert: Display name or entry ID of the expert.
        last_n: Number of most recent transfers to show (default 5).

    Returns:
        A human-readable summary of the expert's recent transfers. An element
        id absent from the bootstrap table is reported as unknown with its raw
        id and no price -- never as a made-up name or a £0.0m price.
    """
    resolved = _resolve_expert(expert)
    if not resolved:
        return f"Expert '{expert}' not found."
    name, eid = resolved
    try:
        transfers = _fetch_transfers(eid)
    except Exception as e:
        return f"Failed to fetch transfers for {name}: {e}"
    if not transfers:
        return f"No transfers recorded for {name} this season."
    # Load elements for name lookup
    elements_df = fpl_data.get_elements_df().set_index("id")
    # Sort transfers by time descending (ISO timestamps) and take last_n
    transfers_sorted = sorted(transfers, key=lambda t: t.get("time", ""), reverse=True)[:last_n]
    lines = [f"Latest {min(last_n, len(transfers_sorted))} transfers for {name}:"]
    unresolved: List[Any] = []

    def _describe(elem_id: Any) -> str:
        """Render one side of a transfer, or say the id is unknown.

        The previous version fell back to a price of 0.0, which reads as a
        free player rather than as missing data. A number we do not have is
        not printed as a number.
        """
        player = _lookup_element(elements_df, elem_id)
        if player is None:
            unresolved.append(elem_id)
            return f"unknown player (element {elem_id}, price unknown)"
        player_name = f"{player['first_name']} {player['second_name']}"
        return f"{player_name} (£{player['now_cost'] / 10.0:.1f}m)"

    for tr in transfers_sorted:
        gw = tr.get("event")
        lines.append(
            f"GW{gw}: In {_describe(tr.get('element_in'))}, "
            f"Out {_describe(tr.get('element_out'))}"
        )
    if unresolved:
        lines.append(
            f"Note: {len(unresolved)} element id(s) "
            f"({', '.join(str(u) for u in unresolved)}) are not in the current "
            f"bootstrap data, so their name and price are unknown. Element ids "
            f"are reassigned every season -- a stale cache or a transfer from a "
            f"previous season is the usual cause."
        )
    return "\n".join(lines)


@mcp.tool()
def get_manager_history(manager: str) -> str:
    """Summarise the historical performance of a manager.

    This function returns a high‑level overview of a manager's FPL history,
    including past season ranks, chip usage and current season gameweek scores.
    If the input cannot be resolved to a known expert, it is interpreted as
    an arbitrary entry ID.

    Args:
        manager: Display name or entry ID of the manager.

    Returns:
        A formatted summary of the manager's history and current season.
    """
    resolved = _resolve_expert(manager)
    if not resolved:
        return f"Manager '{manager}' not found."
    name, eid = resolved
    try:
        history = _fetch_manager_history(eid)
    except Exception as e:
        return f"Failed to fetch history for {name}: {e}"
    output_lines = [f"History for {name} (ID {eid}):"]
    # Past seasons
    past = history.get("past", [])
    if past:
        output_lines.append("Past seasons:")
        for season in past:
            season_name = season.get("season_name")
            rank = season.get("rank")
            points = season.get("total_points")
            output_lines.append(f"- {season_name}: {points} pts, Rank {rank}")
    # Chips used this season
    chips = history.get("chips", [])
    if chips:
        used = [f"GW{c.get('event')}: {c.get('name').replace('_', ' ').title()}" for c in chips]
        output_lines.append("Chips used this season: " + ", ".join(used))
    # Current season scores
    current = history.get("current", [])
    if current:
        # Compute total points and average score
        total_points = sum(ev.get("points", 0) for ev in current)
        avg_points = total_points / len(current)
        highest = max(current, key=lambda ev: ev.get("points", 0))
        high_gw = highest.get("event")
        high_points = highest.get("points")
        output_lines.append(
            f"Current season: {len(current)} gameweeks, total {total_points} pts, "
            f"average {avg_points:.1f} pts, highest GW{high_gw} with {high_points} pts."
        )
    return "\n".join(output_lines)