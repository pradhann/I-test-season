"""
Utility functions for fetching and querying Fantasy Premier League data.

This module encapsulates calls to the public FPL API and
provides Pandas DataFrames representing players, teams and
fixtures for the tools that need the *live* API (team picks,
expert teams, video summaries). Warehouse-backed analytical
queries live in ``tools/semantic_tools.py`` instead.

Because this environment does not have access to PyPI, the MCP
SDK is vendored into the repository.  See README.md for details.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests


# Base URL for the Fantasy Premier League API
FPL_BASE_URL = "https://fantasy.premierleague.com/api"

# Directory where cached data lives
# Raw FPL API bodies land under the repo's existing raw-data convention
# (data/raw/**, already gitignored) rather than in a second cache directory
# beside the toolbelt. One place to look when asking "what did the API
# actually return", and one place for a backup to cover.
DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw" / "fpl_api"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _download_json(endpoint: str) -> Dict[str, Any]:
    """Download JSON data from the given FPL API endpoint.

    Args:
        endpoint: Path relative to the API base, e.g.
            "/bootstrap-static/".

    Returns:
        The decoded JSON object.
    """
    url = FPL_BASE_URL + endpoint
    response = requests.get(url)
    response.raise_for_status()
    return response.json()


def get_bootstrap_data(force_refresh: bool = False) -> Dict[str, Any]:
    """Retrieve the FPL bootstrap static dataset.

    The bootstrap data contains the core tables used by the FPL
    site: players (elements), teams, positions (element_types),
    events, etc. To speed up repeated queries, this function caches
    the response on disk.

    Args:
        force_refresh: If True, always download fresh data from
            the API. Otherwise, use the cached file if present.

    Returns:
        A dictionary containing the bootstrap data.
    """
    cache_path = DATA_DIR / "bootstrap_static.json"
    if not force_refresh and cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    data = _download_json("/bootstrap-static/")
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    # Additionally cache individual top-level keys for convenience
    for key, value in data.items():
        # Skip simple numeric keys or None
        filename = f"{key}.json"
        path = DATA_DIR / filename
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(value, f)
        except Exception:
            # Some values may not be serializable (e.g. None) – ignore
            pass
    return data


def get_elements_df(force_refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame containing all players (elements).

    The resulting DataFrame includes a few extra columns mapping
    numeric IDs to human-friendly names (team_name and position).

    Args:
        force_refresh: If True, bypass the cache and fetch fresh
            bootstrap data.

    Returns:
        A Pandas DataFrame with player information.
    """
    data = get_bootstrap_data(force_refresh=force_refresh)
    elements = pd.DataFrame(data["elements"])
    teams_df = pd.DataFrame(data["teams"])[["id", "name"]].rename(
        columns={"id": "team", "name": "team_name"}
    )
    positions_df = pd.DataFrame(data["element_types"])[
        ["id", "singular_name_short"]
    ].rename(columns={"id": "element_type", "singular_name_short": "position"})
    # Merge to add team_name and position
    elements = elements.merge(teams_df, on="team", how="left")
    elements = elements.merge(positions_df, on="element_type", how="left")
    # Convert selected_by_percent to float for numeric comparisons.  The API
    # exposes this as a string (e.g. "25.4") so without conversion
    # numeric filters and sorts will fail.  Coerce invalid values to NaN.
    if "selected_by_percent" in elements.columns:
        elements["selected_by_percent"] = pd.to_numeric(
            elements["selected_by_percent"], errors="coerce"
        )
    return elements


def get_teams_df(force_refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame of teams.

    Args:
        force_refresh: If True, fetch fresh bootstrap data.

    Returns:
        DataFrame with id, name, short_name and other team info.
    """
    data = get_bootstrap_data(force_refresh=force_refresh)
    return pd.DataFrame(data["teams"])


def get_fixtures_df(force_refresh: bool = False) -> pd.DataFrame:
    """Return a DataFrame containing all fixtures for the season.

    The returned DataFrame includes the home and away team IDs, the
    final scores (if finished), and the kickoff time.  To speed up
    repeated queries the raw JSON is cached in the ``data`` folder.

    Args:
        force_refresh: If True, download fresh fixtures data even if
            a cache file exists.

    Returns:
        A Pandas DataFrame with columns such as ``id``, ``event``,
        ``kickoff_time``, ``team_h``, ``team_h_score``, ``team_a``,
        ``team_a_score``, and ``finished``.
    """
    cache_path = DATA_DIR / "fixtures.json"
    if not force_refresh and cache_path.exists():
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = _download_json("/fixtures/")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    # Convert to DataFrame
    df = pd.DataFrame(data)
    # Ensure kickoff_time is datetime for sorting; errors='coerce' handles None
    if "kickoff_time" in df.columns:
        df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], errors="coerce")
    return df


def get_team_id_by_name(team_name: str) -> Optional[int]:
    """Return the team ID corresponding to a case-insensitive team name.

    Args:
        team_name: Team name (e.g. "Manchester United" or "MAN UTD").

    Returns:
        The team ID if found, otherwise None.
    """
    teams = get_teams_df()
    # Normalize names: remove punctuation and casefold
    normalized = team_name.strip().casefold()
    # Attempt exact match on full name or short name
    for _, row in teams.iterrows():
        if row["name"].casefold() == normalized or row.get("short_name", "").casefold() == normalized:
            return int(row["id"])
    # Fallback to partial match: return the first team whose name contains the query
    for _, row in teams.iterrows():
        if normalized in row["name"].casefold() or normalized in row.get("short_name", "").casefold():
            return int(row["id"])
    return None


def compute_team_summary(team: int, last_n_games: int = 5) -> Dict[str, Any]:
    """Compute a summary of a team's recent performance.

    This examines completed fixtures involving the specified team and
    returns aggregate statistics for the most recent ``last_n_games``.

    Args:
        team: Team ID.
        last_n_games: Number of completed games to include.

    Returns:
        A dictionary with keys ``games``, ``wins``, ``draws``, ``losses``,
        ``goals_scored``, ``goals_conceded`` and ``points``. If the
        team has not played any completed games, the dictionary will
        contain zeros.
    """
    fixtures = get_fixtures_df()
    # Filter completed fixtures involving this team
    mask = (
        fixtures["finished"].fillna(False).astype(bool)
        & ((fixtures["team_h"] == team) | (fixtures["team_a"] == team))
    )
    team_fixtures = fixtures[mask].copy()
    # Sort by kickoff_time descending (most recent first)
    if "kickoff_time" in team_fixtures.columns:
        team_fixtures.sort_values(by="kickoff_time", ascending=False, inplace=True)
    else:
        # fallback to event id if no kickoff_time
        team_fixtures.sort_values(by="event", ascending=False, inplace=True)
    # Take last N games
    team_fixtures = team_fixtures.head(last_n_games)
    summary = {
        "games": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "goals_scored": 0,
        "goals_conceded": 0,
        "points": 0,
    }
    for _, row in team_fixtures.iterrows():
        summary["games"] += 1
        if row["team_h"] == team:
            goals_for, goals_against = row.get("team_h_score", 0), row.get("team_a_score", 0)
        else:
            goals_for, goals_against = row.get("team_a_score", 0), row.get("team_h_score", 0)
        summary["goals_scored"] += int(goals_for or 0)
        summary["goals_conceded"] += int(goals_against or 0)
        # Determine result
        if goals_for > goals_against:
            summary["wins"] += 1
            summary["points"] += 3
        elif goals_for == goals_against:
            summary["draws"] += 1
            summary["points"] += 1
        else:
            summary["losses"] += 1
    return summary
