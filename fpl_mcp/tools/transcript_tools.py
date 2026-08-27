"""MCP tools for retrieving YouTube transcripts.

This module exposes a single tool, ``fetch_youtube_transcript``, that
accepts a YouTube URL and returns the video's auto‑generated English
transcript as plain text.  It uses the utilities in
``utils.video_transcript`` to extract the video ID and download the
transcript via YouTube's Innertube API.  No summarisation or
post‑processing is performed here; the returned text can be passed to
the language model for further analysis and summarisation.

The tool output includes:

* ``transcript`` – A single string containing the transcript lines
  separated by newline characters.  If no transcript is available,
  this value will be an empty string.
* ``video_id`` – The extracted 11‑character YouTube video ID.  This
  allows callers to reference or cache transcripts by ID.

Example call:

.. code-block:: json

    {
      "url": "https://www.youtube.com/watch?v=DFlm3_EIbko"
    }

The returned ``transcript`` can then be summarised by the language
model using guidance provided in ``prompts.transcript_summary_guidance``.
"""

from __future__ import annotations

from typing import Dict

from fpl_mcp.server import mcp  # Shared FastMCP instance
from fpl_mcp.utils.video_transcript import extract_video_id, get_transcript


@mcp.tool()
def fetch_youtube_transcript(url: str) -> Dict[str, str]:  # type: ignore[override]
    """Retrieve the auto‑generated English transcript for a YouTube video.

    Args:
        url: The full YouTube URL (either ``youtu.be`` or
            ``youtube.com/watch?v=`` format).

    Returns:
        A dictionary with two keys:
        ``transcript`` (str): The video's English transcript as plain
        text, or an empty string if unavailable.
        ``video_id`` (str): The extracted 11‑character video ID, or
        ``None`` if the URL is invalid.

    Notes:
        The transcript returned is unprocessed and may contain
        time‑code markers and filler phrases.  Use the
        ``transcript_summary_guidance`` prompt to instruct the
        language model how to summarise this text for FPL analysis.
    """
    video_id = extract_video_id(url)
    if not video_id:
        return {"transcript": "", "video_id": None}
    lines = get_transcript(video_id)
    # Join lines with newlines to preserve some structure for the LLM
    transcript_text = "\n".join(lines)
    return {"transcript": transcript_text, "video_id": video_id}