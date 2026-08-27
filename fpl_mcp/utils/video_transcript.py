"""Utility functions to download and process YouTube video transcripts.

This module provides a simple interface for fetching the auto‑generated
English transcript for a YouTube video using the same Innertube API
approach used by ``FPLWireSource``.  It also includes helpers to
extract a video ID from a full YouTube URL.  These utilities can be
used by tools that summarise videos for Fantasy Premier League (FPL)
analysis.

We avoid external dependencies such as ``youtube_transcript_api`` to
ensure the code can run without internet access to PyPI.

Functions:
    extract_video_id(url: str) -> Optional[str]:
        Parse a YouTube URL and return the video ID if present.

    get_transcript(video_id: str) -> List[str]:
        Fetch the English transcript for a given YouTube video ID.  If
        no transcript is available or an error occurs, an empty list
        is returned.

"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional

import requests

def extract_video_id(url: str) -> Optional[str]:
    """Extract the YouTube video ID from a full or shortened URL.

    Args:
        url: A string representing a YouTube watch URL or short URL.

    Returns:
        The 11‑character video ID if found, otherwise ``None``.

    Examples::

        >>> extract_video_id("https://www.youtube.com/watch?v=abc123def45")
        'abc123def45'
        >>> extract_video_id("https://youtu.be/abc123def45")
        'abc123def45'
    """
    # Patterns to match typical YouTube URL formats
    patterns = [
        r"youtube\.com/watch\?v=([\w-]{11})",
        r"youtu\.be/([\w-]{11})",
        r"youtube\.com/embed/([\w-]{11})",
    ]
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            return match.group(1)
    return None


def _get_innertube_api_key(video_id: str) -> Optional[str]:
    """Retrieve the Innertube API key from the video watch page.

    The API key is embedded in the page's JavaScript and used to
    authenticate subsequent API calls.  If the key cannot be found,
    ``None`` is returned.
    """
    try:
        html = requests.get(f"https://www.youtube.com/watch?v={video_id}", timeout=10).text
        match = re.search(r'"INNERTUBE_API_KEY":"([^\"]+)"', html)
        return match.group(1) if match else None
    except Exception:
        return None


def get_transcript(video_id: str) -> List[str]:
    """Download the English transcript for a YouTube video.

    Args:
        video_id: The 11‑character YouTube video ID.

    Returns:
        A list of caption strings in chronological order.  If the
        transcript is unavailable or an error occurs, an empty list
        is returned.
    """
    api_key = _get_innertube_api_key(video_id)
    if not api_key:
        return []
    url = f"https://www.youtube.com/youtubei/v1/player?key={api_key}"
    body = {
        "context": {
            "client": {
                "clientName": "ANDROID",
                "clientVersion": "20.10.38",
            }
        },
        "videoId": video_id,
    }
    try:
        resp = requests.post(url, json=body, timeout=10)
        data = resp.json()
    except Exception:
        return []
    # Traverse the captions object to find the English track
    captions = (
        data
        .get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    track = None
    for t in captions:
        if t.get("languageCode") == "en":
            track = t
            break
    if not track:
        return []
    base_url = track.get("baseUrl")
    if not base_url:
        return []
    # Remove fmt parameter if present to get XML
    base_url = re.sub(r"&fmt=\w+$", "", base_url)
    try:
        xml_text = requests.get(base_url, timeout=10).text
        root = ET.fromstring(xml_text)
        captions = [item.text or "" for item in root.findall("text")]
        return captions
    except Exception:
        return []