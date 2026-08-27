"""
Reusable prompts to guide the language model when using the FPL MCP tools.

These prompts point the model at the right tool vocabulary — the
semantic-layer data tools first, the live-API and video tools for what
the warehouse does not hold.  They are registered with FastMCP via the
``@mcp.prompt()`` decorator.  When queried, Claude can consult this
guidance before attempting to call a tool.
"""

from __future__ import annotations

# Import the shared MCP server.  Absolute import ensures this works
# both when run as a package (python -m fpl_server.tools.prompts) and
# as a script (python tools/prompts.py) from the fpl_server directory.
from fpl_mcp.server import mcp  # type: ignore


@mcp.prompt()
def fpl_query_guidance() -> str:
    """
    Guidance on querying FPL data through this server.

    The preferred vocabulary for player, projection, form, fixture and
    ownership questions is the SEMANTIC LAYER: six point-in-time table
    macros stored in the fpl-edge warehouse, surfaced here as thin tools.
    Every one takes an optional ``as_of`` ISO-8601 UTC instant (default
    now) and answers with what was knowable at that instant.

    - ``player_projections(player, gw?, season?)`` — every source's
      xPts/xMins/p(appear) for one player, side by side
      (macro: sem_projections).
    - ``projection_disagreement(gw, season?, top_n?, player?)`` — biggest
      cross-source xPts spreads; the spread IS the uncertainty estimate
      (macro: sem_projection_consensus).
    - ``xpts_aggregate(group_by, gw, ...)`` — consensus xPts grouped by
      team, position or price_band, with position/team/price filters
      (macro: sem_projection_consensus).
    - ``player_form(player, last_k?, season?)`` — most recent settled
      gameweeks incl. official xG/xA/xGC; spans seasons and names which
      season each row is from (macro: sem_player_form).
    - ``fixture_difficulty(team?, next_k?)`` — upcoming schedule joined to
      the model-fitted 0-1 difficulty artefact when it exists
      (macro: sem_fixtures + fixture_difficulty.parquet).
    - ``ownership_eo(player?, metric?, preset?)`` — marginal ownership and
      every external EO metric; presets "template" and "differential"
      (macro: sem_ownership).

    Conventions: player names are web names, partial match is fine, and
    ambiguity returns a candidate list rather than a guess. Seasons use
    FPL's "2026-27" form. Positions are GKP/DEF/MID/FWD; teams are short
    names like MCI. Empty results explain themselves (e.g. which fetches
    exist outside the as_of window) — trust that text over re-querying.

    For live-API oddities the semantic layer does not cover:
    ``get_team_summary`` (a club's recent W/D/L), ``get_team_picks`` /
    ``get_manager_history`` (entry endpoints), and the expert/content
    tools for creator intelligence.
    """
    return (
        "Prefer the semantic-layer tools for data questions: player_projections "
        "(per-source xPts for a player), projection_disagreement (cross-source "
        "spreads), xpts_aggregate (consensus xPts by team/position/price_band), "
        "player_form (settled gameweeks with xG/xA/xGC), fixture_difficulty "
        "(upcoming schedule with model ratings) and ownership_eo (marginal + "
        "effective ownership, template/differential presets). All take an "
        "optional as_of ISO-8601 UTC instant and answer point-in-time; player "
        "names are partial-match web names and ambiguity returns a list, not a "
        "guess. Use get_team_summary / team-entry / expert tools only for what "
        "the warehouse does not hold."
    )


@mcp.prompt()
def video_summary_guidance() -> str:
    """
    Guidance for summarising FPL YouTube videos.

    This prompt tells the language model how and when to use the
    ``summarise_fpl_youtube`` tool.  When a user provides a YouTube
    link to a Fantasy Premier League podcast, preview or analysis
    video and asks for a summary, recommended players or insights,
    call the ``summarise_fpl_youtube`` tool with the ``url``
    parameter set to the full video URL.

    The tool returns a dictionary with four keys:

    - ``summary`` (str): A concise overall summary (about 600
      characters) covering the main talking points.  It prioritises
      sentences containing FPL keywords such as player names,
      captaincy, fixtures and rotation.

    - ``players`` (list): Up to ten of the most frequently
      mentioned players.  Each entry has ``player_name`` and
      ``reasoning`` fields.  Reasoning combines transcript lines
      mentioning price, minutes, rotation, fixtures, etc., and
      includes the player's FPL price and position.

    - ``main_points`` (list): A list of broader topics discussed
      during the video (e.g. "Captaincy", "Differentials", "Fixtures
      Analysis").  Each entry has ``topic`` and ``summary`` fields
      summarising the key points from the transcript.

    - ``video_id`` (str): The YouTube video ID for reference.

    Example call:

    .. code-block:: json

        {
          "url": "https://www.youtube.com/watch?v=DFlm3_EIbko"
        }

    You generally do not need to present the ``video_id`` to the
    user unless they explicitly ask for it.  Use ``summary`` and
    ``main_points`` to answer high-level questions about the video's
    content, and use ``players`` to provide actionable
    recommendations or insights.
    """
    return (
        "When given a YouTube link to an FPL-related podcast or video and asked to summarise or "
        "extract recommendations, use the `summarise_fpl_youtube` tool. Pass the full URL in the "
        "`url` parameter. The tool returns a concise overall 'summary' of the video, a list of up "
        "to ten recommended players with reasoning (including their price and position), and a list "
        "of broader 'main_points' topics with brief summaries. It also returns the video ID for reference."
    )


@mcp.prompt()
def transcript_summary_guidance() -> str:
    """
    Guidance on summarising raw YouTube transcripts for FPL analysis.

    When you call ``fetch_youtube_transcript`` with a YouTube URL,
    you'll receive a raw transcript as plain text.  This transcript
    likely contains filler words (e.g. "uh", "you know"), greetings
    and irrelevant chatter.  To extract actionable information for
    Fantasy Premier League (FPL), follow these steps:

    1. **Clean the text**: Ignore or remove obvious filler phrases and
       pleasantries.  Focus on sentences that mention player names,
       prices, positions, fixtures, minutes, rotation, captaincy,
       differentials, chip strategies (wildcard, bench boost, free hit)
       and other FPL‑relevant topics.

    2. **Identify players**: Cross‑reference names in the transcript
       against the list of Premier League players.  For each player
       discussed, note why they were mentioned (e.g. "cheap enabler",
       "minutes risk", "captaincy option", "great upcoming fixtures").

    3. **Extract themes**: Group the discussion into high‑level topics
       such as Captaincy, Fixtures Analysis, Differentials, Rotation,
       Chip Strategy, Goalkeepers, etc.  Summarise the key points
       raised under each theme in one or two sentences.

    4. **Compose a summary**: Write a short paragraph (3–5 sentences)
       that captures the overall narrative of the video.  Mention the
       main themes and the standout recommendations without going into
       exhaustive detail.

    5. **Answer questions**: If the user asks specific questions
       about the video (e.g. "Who did they recommend as captain?"),
       search the transcript for relevant lines and summarise those
       answers using the context you extracted.

    By following this guidance, you can turn a raw transcript into
    meaningful FPL insights that include recommended players, their
    rationale (price, minutes, fixtures, etc.), and high‑level
    strategic advice.
    """
    return "To summarise a raw YouTube transcript for FPL, ignore filler and focus on lines that mention players, prices, minutes, rotation, fixtures, captaincy, differentials or chip strategies.  Extract the players mentioned and note why they were discussed.  Group the discussion into themes such as Captaincy, Fixtures Analysis, Differentials, Rotation and Chip Strategy, and summarise each in one or two sentences.  Finally write a concise paragraph capturing the overall narrative of the video.  Use the transcript context to answer follow‑up questions about recommendations or strategies."
