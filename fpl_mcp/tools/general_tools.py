"""
Live-API team summary tool.

Historically this module carried a generic raw-table query interface
(``query_fpl_data``) and a player-history tool over the live FPL API. Those
are superseded by the semantic-layer tools in ``tools/semantic_tools.py``,
which read the fpl-edge warehouse's point-in-time macros instead of the
mutable live API. What remains here is the one genuinely distinct tool: a
W/D/L summary of a club's recent results straight from the FPL fixtures
endpoint.
"""

from __future__ import annotations

# Use absolute imports rather than package-relative imports.  When this
# server is run via ``uv --directory`` or as a script, modules are
# imported from the top-level package (fpl_server).  Do not use
# leading dots for relative imports.
from fpl_mcp.utils import fpl_data  # type: ignore
from fpl_mcp.server import mcp  # type: ignore


@mcp.tool()
def get_team_summary(team: str, last_n_games: int = 5) -> str:
    """Summarise a team's recent performance over the last N completed games.

    Args:
        team: Team name or ID. The name is case-insensitive and can be
            partial (e.g. "United" will match Manchester United). If an
            integer is provided, it is treated as the team ID.
        last_n_games: Number of completed games to include in the
            summary.

    Returns:
        A multi-line string reporting total games played, wins, draws,
        losses, goals scored, goals conceded and points accumulated.
    """
    # Resolve team ID
    try:
        team_id = int(team)
    except Exception:
        team_id = fpl_data.get_team_id_by_name(str(team))
    if team_id is None:
        return f"Team '{team}' not found."
    summary = fpl_data.compute_team_summary(team_id, last_n_games=last_n_games)
    # Get team name
    teams_df = fpl_data.get_teams_df()
    team_row = teams_df.loc[teams_df["id"] == team_id]
    if not team_row.empty:
        team_name = team_row.iloc[0]["name"]
    else:
        team_name = str(team_id)
    return (
        f"Summary for {team_name} (last {summary['games']} completed games):\n"
        f"Wins: {summary['wins']}, Draws: {summary['draws']}, Losses: {summary['losses']}\n"
        f"Goals scored: {summary['goals_scored']}, Goals conceded: {summary['goals_conceded']}\n"
        f"Total points: {summary['points']}"
    )
