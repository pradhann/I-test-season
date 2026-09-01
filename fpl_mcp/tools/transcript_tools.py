"""MCP tool for retrieving YouTube transcripts, panel-scoped and honest.

This module exposes a single tool, ``fetch_youtube_transcript``, that accepts
a YouTube URL and returns the video's published English captions as plain
text. It delegates to ``utils.video_transcript``, which routes through the
engine's sanctioned caption path (:mod:`fpl_edge.ingest.content.youtube`):

* captions are fetched only for creators on the owner's curated panel
  (``PANEL_CREATORS``) -- the 2026-08-27 policy the engine enforces in code;
* an off-panel video is refused with the reason and a pointer at the
  platform's paste-a-link flow, which previews before transcribing;
* a 403/429 from YouTube is reported as the source declining, never retried
  and never collapsed into an empty string with no explanation.

The tool output includes:

* ``transcript`` -- the caption lines joined by newlines, or ``""``;
* ``video_id`` -- the extracted 11-character YouTube video ID;
* ``route`` -- what mechanically happened ("innertube", "off_panel", ...);
* ``reason`` -- ALWAYS present when ``transcript`` is empty: the honest
  sentence saying why there is no transcript. Never an empty result with no
  reason.
"""

from __future__ import annotations

from typing import Dict, Optional

from fpl_mcp.server import mcp  # Shared FastMCP instance
from fpl_mcp.utils.video_transcript import extract_video_id, get_transcript


@mcp.tool()
def fetch_youtube_transcript(url: str) -> Dict[str, Optional[str]]:  # type: ignore[override]
    """Retrieve the published English captions for a panel creator's video.

    Args:
        url: The full YouTube URL (``youtu.be`` or ``youtube.com/watch?v=``).

    Returns:
        A dictionary with:
        ``transcript`` (str): the caption text, newline-separated, or ``""``;
        ``video_id`` (str | None): the 11-character video ID, or ``None`` if
        the URL is invalid;
        ``route`` (str): what happened mechanically;
        ``reason`` (str | None): why the transcript is empty, when it is.

    Notes:
        Captions are fetched only for creators on the owner's curated panel;
        any other video is refused with the reason and the sanctioned
        alternative (the platform's paste-a-link preview flow). The returned
        transcript is unprocessed; use the ``transcript_summary_guidance``
        prompt to summarise it for FPL analysis.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {
            "transcript": "",
            "video_id": None,
            "route": "invalid_url",
            "reason": ("not a recognisable YouTube URL; paste a standard "
                       "watch, youtu.be, shorts or live link."),
        }
    result = get_transcript(video_id)
    return {
        "transcript": result.text,
        "video_id": video_id,
        "route": result.route,
        "reason": result.reason,
    }
