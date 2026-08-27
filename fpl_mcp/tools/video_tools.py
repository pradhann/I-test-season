"""MCP tools for summarising FPL‑related YouTube videos.

This module exposes a single tool, ``summarise_fpl_youtube``, that
accepts a YouTube URL and returns a structured summary tailored for
Fantasy Premier League (FPL) analysis.  The tool downloads the
auto‑generated transcript of the video, extracts mentions of FPL
players, and compiles a brief overview of the main talking points.

The output contains:

* ``summary`` – A concise overview of the entire video (around
  600 characters), constructed by selecting representative lines from
  across the transcript.  It prioritises sentences containing FPL
  keywords (players, captaincy, fixtures) to capture the main
  themes.
* ``players`` – A list of dictionaries, each with ``player_name``
  and ``reasoning`` fields.  Up to ten of the most frequently
  mentioned FPL players are returned.  For each player, the tool
  extracts transcript snippets discussing price, minutes, rotation,
  fixtures and similar FPL‑relevant topics, appending their price
  and position from the FPL dataset.
* ``main_points`` – A list of high‑level discussion topics (e.g.
  "Captaincy", "Fixtures Analysis", "Differentials").  Each entry
  contains a topic name and a short summary composed of transcript
  lines where that topic was mentioned.
* ``video_id`` – The YouTube video ID for reference.

This tool is useful for ingesting content from podcasts or video
previews and extracting actionable recommendations and insights
without manual listening.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fpl_mcp.server import mcp  # Shared FastMCP instance
from fpl_mcp.utils.video_transcript import extract_video_id, get_transcript
from fpl_mcp.utils.fpl_data import get_elements_df


def _get_player_lookup() -> Dict[str, Dict[str, object]]:
    """Create a lookup of full player names to their price and position.

    Returns a dictionary keyed by lowercase ``"first last"`` strings,
    with values containing ``price`` (float, millions) and
    ``position`` (GKP/DEF/MID/FWD).
    """
    df = get_elements_df()
    lookup: Dict[str, Dict[str, object]] = {}
    pos_map = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    for _, row in df.iterrows():
        name = f"{row['first_name']} {row['second_name']}".lower()
        lookup[name] = {
            "price": row["now_cost"] / 10,
            "position": pos_map.get(row["element_type"], "")
        }
    return lookup


def _extract_players_from_transcript(transcript: List[str], top_n: int = 10) -> List[Dict[str, str]]:
    """Identify and summarise the most mentioned players in a transcript.

    Args:
        transcript: List of transcript lines (strings).
        top_n: Maximum number of players to return.

    Returns:
        A list of dictionaries with ``player_name`` and ``reasoning``.
    """
    lookup = _get_player_lookup()
    mention_counts: Dict[str, int] = {}
    # Keep track of the transcript lines for each player
    lines_by_player: Dict[str, List[str]] = {}
    for line in transcript:
        lower_line = line.lower()
        for name in lookup.keys():
            # Use a simple substring check on the full name.  This
            # avoids false positives (e.g. "man" matching "manager").
            if name in lower_line:
                mention_counts[name] = mention_counts.get(name, 0) + 1
                lines_by_player.setdefault(name, []).append(line.strip())
    # Sort by frequency and take the top players
    sorted_players = sorted(mention_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    players: List[Dict[str, str]] = []
    # Keywords to look for when extracting reasoning.  These capture
    # price, minutes, rotation, fixtures and other FPL‑relevant topics.
    KEYWORDS = [
        "price", "cost", "cheap", "value", "rotation", "minutes", "minutes", "x mins",
        "fixtures", "fixture", "captain", "captaincy", "talisman", "differential",
        "differentials", "punt", "safe", "risk", "explosive", "form", "expected",
        "penalty", "minutes", "injury", "injuries", "bench", "substitute", "nailed"
    ]
    for name, freq in sorted_players:
        lines = lines_by_player.get(name, [])
        # Collect lines containing keywords for better context
        keyword_lines = [ln for ln in lines if any(kw in ln.lower() for kw in KEYWORDS)]
        # If no keyword lines, fall back to the first three mentions
        selected_lines = keyword_lines if keyword_lines else lines[:3]
        reasoning = " ".join(selected_lines).strip()
        # Append price and position where available
        info = lookup.get(name, {})
        price = info.get("price")
        position = info.get("position")
        if price is not None and position:
            reasoning = f"{reasoning} (Price: £{price:.1f}m, Position: {position})"
        players.append({
            "player_name": name.title(),
            "reasoning": reasoning,
        })
    return players


def _extract_main_points(transcript: List[str], max_points: int = 5) -> List[Dict[str, str]]:
    """Identify major discussion topics in the transcript.

    This function scans the transcript for lines containing FPL‑relevant
    keywords (e.g. captaincy, fixtures, differentials).  It groups
    related lines into high‑level topics and returns a concise
    summary for each.

    Args:
        transcript: List of transcript strings.
        max_points: Maximum number of topics to return.

    Returns:
        A list of dictionaries with ``topic`` and ``summary`` keys.
    """
    # Map keywords to general topic names.  If multiple keywords
    # match, the first one in this list takes precedence.
    KEYWORDS_TO_TOPIC = [
        ("captain", "Captaincy"),
        ("captaincy", "Captaincy"),
        ("differential", "Differentials"),
        ("differentials", "Differentials"),
        ("fixtures", "Fixtures Analysis"),
        ("fixture", "Fixtures Analysis"),
        ("rotation", "Rotation"),
        ("minutes", "Rotation"),
        ("injury", "Injuries"),
        ("injuries", "Injuries"),
        ("goalkeeper", "Goalkeepers"),
        ("keepers", "Goalkeepers"),
        ("bench", "Benching"),
        ("wildcard", "Wildcard"),
        ("free hit", "Free Hit"),
        ("chip", "Chips"),
    ]
    # Collect lines for each topic
    topic_lines: Dict[str, List[str]] = {}
    for line in transcript:
        lower_line = line.lower()
        for keyword, topic in KEYWORDS_TO_TOPIC:
            if keyword in lower_line:
                topic_lines.setdefault(topic, []).append(line.strip())
                break  # assign to first matching topic
    # Build topic summaries
    points: List[Dict[str, str]] = []
    for topic, lines in topic_lines.items():
        # Summarise by joining the first few lines containing this topic
        selected = lines[:3]
        summary = " ".join(selected)
        # Limit summary length to 300 characters to avoid overly long notes
        if len(summary) > 300:
            summary = summary[:297].rsplit(" ", 1)[0] + "…"
        points.append({"topic": topic, "summary": summary})
    # Return up to max_points topics, sorted by number of mentions (desc)
    sorted_points = sorted(points, key=lambda x: len(topic_lines[x["topic"]]), reverse=True)
    return sorted_points[:max_points]


def _summarise_overall(transcript: List[str], max_chars: int = 600) -> str:
    """Generate an overall summary of a transcript.

    This function selects lines at roughly regular intervals through
    the transcript to capture a broad overview of the content.  It
    prioritises lines containing FPL‑specific keywords (such as
    "player", "captain", "fixture") and concatenates them until
    ``max_chars`` is reached.

    Args:
        transcript: List of transcript strings.
        max_chars: Maximum length of the summary.

    Returns:
        A single string summarising the overall content of the video.
    """
    if not transcript:
        return ""
    # FPL‑specific keywords to prioritise
    PRIORITY_WORDS = [
        "player", "players", "captain", "captaincy", "fixtures", "fixture",
        "differential", "minutes", "rotation", "rank", "team",
    ]
    selected_lines: List[str] = []
    total_chars = 0
    # First pick lines containing priority words
    for line in transcript:
        lower = line.lower()
        if any(w in lower for w in PRIORITY_WORDS):
            if total_chars + len(line) + 1 > max_chars:
                break
            selected_lines.append(line.strip())
            total_chars += len(line) + 1
            # Stop if we already have a few sentences
            if len(selected_lines) >= 5:
                break
    # If we still have space, append the very first lines of the transcript
    if total_chars < max_chars:
        for line in transcript:
            if not line.strip():
                continue
            if total_chars + len(line) + 1 > max_chars:
                break
            selected_lines.append(line.strip())
            total_chars += len(line) + 1
            if len(selected_lines) >= 7:
                break
    summary = " ".join(selected_lines)
    return summary.strip()


def _summarise_general(transcript: List[str], max_chars: int = 800) -> str:
    """Create a brief summary of the overall video content.

    This function concatenates transcript lines until the character
    limit is reached.  It skips empty lines and trims whitespace.
    """
    summary_lines: List[str] = []
    total_chars = 0
    for line in transcript:
        if not line.strip():
            continue
        if total_chars + len(line) + 1 > max_chars:
            break
        summary_lines.append(line.strip())
        total_chars += len(line) + 1
    return " ".join(summary_lines).strip()


@mcp.tool()
def summarise_fpl_youtube(url: str) -> Dict[str, object]:  # type: ignore[override]
    """Summarise a YouTube video for FPL relevance.

    Given a YouTube link, this tool retrieves the video's
    auto‑generated English transcript, identifies FPL players
    discussed, extracts high‑level discussion topics and compiles a
    concise overview.  It returns a dictionary containing:

    * ``summary`` – A  brief overall summary (approx. 600 characters)
      covering the main talking points.  It favours sentences with
      FPL keywords such as ``player``, ``captain`` and
      ``fixtures``.
    * ``players`` – Up to ten of the most frequently mentioned
      players.  Each entry includes the ``player_name`` and a
      ``reasoning`` string.  Reasoning is derived from transcript
      lines discussing price, minutes, rotation, fixtures, etc., and
      includes the player's price and position.
    * ``main_points`` – A list of broader discussion topics (e.g.
      "Captaincy", "Fixtures Analysis", "Differentials") with a
      short summary drawn from relevant transcript lines.
    * ``video_id`` – The extracted video ID for reference.

    Args:
        url: A full YouTube URL (e.g. ``https://www.youtube.com/watch?v=...``).

    Returns:
        A dictionary with the keys ``summary``, ``players``,
        ``main_points`` and ``video_id``.  If the transcript cannot
        be obtained, ``players`` and ``main_points`` will be empty and
        ``summary`` will contain an error message.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {
            "summary": "Invalid YouTube URL. Please provide a standard watch or youtu.be link.",
            "players": [],
            "video_id": None,
        }
    transcript = get_transcript(video_id)
    if not transcript:
        return {
            "summary": "Transcript not available or failed to download.",
            "players": [],
            "main_points": [],
            "video_id": video_id,
        }
    # Generate the overall summary (approx. 600 characters)
    overall_summary = _summarise_overall(transcript)
    # Identify the top players and extract reasoning
    players = _extract_players_from_transcript(transcript)
    # Identify major topics discussed in the video
    main_points = _extract_main_points(transcript)
    return {
        "summary": overall_summary,
        "players": players,
        "main_points": main_points,
        "video_id": video_id,
    }