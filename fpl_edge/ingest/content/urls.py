"""URL grammar for content items: canonical identity and deep links.

One authority for reading a video id out of a URL. There were three before this
module existed, and the weakest of them lived in
``fpl_edge/interfaces/creators.py`` and matched exactly eleven id characters
where this one accepts six to twenty, and knew nothing about ``/embed/``
(ARCHITECTURE_REVIEW.md check 1).

This is a leaf on purpose. It imports the standard library and nothing else, so
``fpl_edge/ingest/content/pipeline.py`` can read a video id without importing
``fpl_edge.platform.scripts.creators``, which was the single
``ingest.content -> platform.scripts`` edge and the one that closed cycle C2
(ARCHITECTURE_REVIEW.md check 6, move M1). Adding an import to this module from
anywhere above ``ingest`` in the layering puts that cycle back.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
             "music.youtube.com", "youtu.be", "www.youtu.be"}
_YT_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def youtube_id(url: str | None) -> str | None:
    """The video id behind any YouTube URL form, or None.

    ``watch?v=X``, ``watch?reload=9&v=X``, ``youtu.be/X?si=...``,
    ``youtube.com/live/X``, ``/embed/X`` and ``/shorts/X`` are all one video.
    Both of the real duplicate pairs in the warehouse differ only in query
    junk, which is exactly the shape a naive URL key fails on.
    """
    if not url:
        return None
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host not in _YT_HOSTS:
        return None
    if host.endswith("youtu.be"):
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        candidate = ""
        for prefix in _YT_PATH_PREFIXES:
            if parsed.path.startswith(prefix):
                candidate = parsed.path[len(prefix):].split("/")[0]
                break
    return candidate if candidate and _YT_ID.match(candidate) else None


def canonical_key(url: str | None, item_id: str) -> str:
    """One key per underlying publication, not per stored row.

    Falls back to the URL, then to the item id: a source with no URL grammar we
    understand is still one item, and two of them must not collapse together.
    """
    vid = youtube_id(url)
    if vid:
        return f"yt:{vid}"
    return f"url:{url}" if url else f"item:{item_id}"


def deep_link(url: str | None, start_s: float | None) -> str | None:
    """A link that lands on the moment, when the platform has a grammar for it.

    YouTube gets ``&t=NNNs`` against the canonical watch URL. Everything else
    gets the item URL untouched: podcast ``url`` values here are episode pages,
    not media files, and inventing a ``#t=`` fragment for a page that ignores it
    would produce a link that silently lands at the top. ``start_s`` is still
    reported so the UI can print the offset beside a plain link.
    """
    if not url:
        return None
    vid = youtube_id(url)
    if vid is None or start_s is None:
        return str(url)
    # Floor, never round. Landing a fraction of a second early replays the
    # start of the sentence; rounding up can start the viewer after the words
    # they clicked to hear, which reads as a broken link.
    return f"https://www.youtube.com/watch?v={vid}&t={max(int(float(start_s)), 0)}s"
