"""YouTube, and the honest answer about transcripts.

What was measured on 2026-08-18 from this machine
-------------------------------------------------

``youtube-transcript-api`` **is not blocked and does work.** Installed into a
throwaway virtualenv and pointed at two public videos it returned 61 and 6
caption snippets on the first attempt, no proxy, no cookies. The dependency-free
Innertube route works identically: ``/watch`` 200, ``youtubei/v1/player`` 200
with six caption tracks, ``timedtext`` 200 returning well-formed XML.

**And this package still does not fetch transcripts.** ``youtube.com/robots.txt``
reads, for ``User-agent: *``::

    Disallow: /feeds/videos.xml
    Disallow: /youtubei/

Both routes to a transcript are explicitly disallowed. ``youtube-transcript-api``
works by calling ``/youtubei/v1/player``; so did the hand-rolled version in the
user's existing MCP server. The channel Atom feed -- the obvious way to list
uploads -- is disallowed too. So the technically-easy path and the permitted
path are different paths, and the brief is unambiguous about which one to take.

Reporting "youtube-transcript-api is blocked" would be false. Reporting "so we
used it" would be worse. The accurate statement is: *it works, and we do not use
it, because the endpoint it calls is Disallowed and the project's fetcher obeys
robots.txt on every request.*

What is permitted, and what that costs
--------------------------------------

Not disallowed: ``/@handle/videos`` and ``/watch?v=``. Those give, per video,
the title, the exact ``datePublished`` with offset, and the full description.
That is a real loss against transcripts -- a twenty-minute video reduced to
about 900 characters -- but it is not nothing, because FPL video titles are the
most claim-dense text in the entire corpus. "THE BEST BUDGET FORWARDS FOR
GAMEWEEK 1" and "MY GW1 CAPTAIN PICK" are complete claims in nine words, with a
stated gameweek.

:func:`fetch_transcript` is kept, implemented, and refuses to run unless a
caller explicitly passes ``allow_disallowed_routes=True``. Nothing in this
package passes it. It exists so the capability is documented and reviewable
rather than quietly reintroduced later by someone who reads the note above and
concludes the library must be broken.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from fpl_edge.ingest.content.feeds import strip_html
from fpl_edge.ingest.content.fetch import ContentFetcher, Response

UTC = dt.UTC

WATCH_URL = "https://www.youtube.com/watch?v={vid}"
CHANNEL_VIDEOS_URL = "https://www.youtube.com/@{handle}/videos"

#: Disallowed by youtube.com/robots.txt. Named here so the reason a route is
#: unused is greppable from the route itself.
ROBOTS_DISALLOWED_ROUTES = (
    "https://www.youtube.com/feeds/videos.xml",
    "https://www.youtube.com/youtubei/",
)

_API_KEY_RE = re.compile(r'"INNERTUBE_API_KEY":"([^"]+)"')
_VIDEO_ID_RE = re.compile(r'"videoId":"([\w-]{11})"')
_PUBLISH_RE = re.compile(r'itemprop="datePublished" content="([^"]+)"')
_PUBLISH_JSON_RE = re.compile(r'"publishDate":"([0-9]{4}-[0-9]{2}-[0-9]{2}[^"]*)"')
_TITLE_RE = re.compile(r'<meta name="title" content="([^"]*)"')
_DESC_RE = re.compile(r'"shortDescription":"(.*?)","isCrawlable"', re.DOTALL)
_EXTERNAL_ID_RE = re.compile(r'"externalId":"(UC[\w-]{22})"')


@dataclass(frozen=True, slots=True)
class Video:
    video_id: str
    title: str
    published_at: dt.datetime
    url: str
    description: str = ""

    @property
    def text(self) -> str:
        return f"{self.title}.\n{self.description}".strip()


def resolve_channel_id(fetcher: ContentFetcher, handle: str) -> tuple[str | None, int | None]:
    """@handle -> canonical UC id, read from the page's own externalId.

    Kept because the registry's channel ids were produced this way and should be
    reproducible, even though the ids are no longer used to build a feed URL.
    """
    resp = fetcher.get(CHANNEL_VIDEOS_URL.format(handle=handle.lstrip("@")))
    if not resp.ok:
        return None, resp.status
    match = _EXTERNAL_ID_RE.search(resp.text)
    return (match.group(1) if match else None), resp.status


def videos_from_channel_page(
    fetcher: ContentFetcher, handle: str, *, limit: int = 12
) -> tuple[list[Video], Response]:
    """Recent uploads from the channel's own grid, then one watch page each.

    The grid renders dates as "2 days ago". A relative date is not a timestamp,
    and ``published_at`` is the field that decides whether a claim is admissible
    at a deadline, so the exact ``datePublished`` is read from each watch page
    even though it costs a request per video.
    """
    resp = fetcher.get(CHANNEL_VIDEOS_URL.format(handle=handle.lstrip("@")))
    if not resp.ok:
        return [], resp
    ids: list[str] = []
    for match in _VIDEO_ID_RE.finditer(resp.text):
        vid = match.group(1)
        if vid not in ids:
            ids.append(vid)
        if len(ids) >= limit:
            break

    videos: list[Video] = []
    for vid in ids:
        page = fetcher.get(WATCH_URL.format(vid=vid))
        if not page.ok:
            continue
        published = _published_from_watch(page.text)
        if published is None:
            # No date means no admissibility test, and an inadmissible-by-unknown
            # claim is worse than a missing one. Drop it.
            continue
        title_match = _TITLE_RE.search(page.text)
        videos.append(
            Video(
                video_id=vid,
                title=strip_html(title_match.group(1)) if title_match else vid,
                published_at=published,
                url=WATCH_URL.format(vid=vid),
                description=_description_from_watch(page.text),
            )
        )
    return videos, resp


def _published_from_watch(html_text: str) -> dt.datetime | None:
    for pattern in (_PUBLISH_RE, _PUBLISH_JSON_RE):
        match = pattern.search(html_text)
        if not match:
            continue
        raw = match.group(1)
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            # A bare calendar date. Anchoring to midnight UTC is the
            # conservative reading: it can only make a claim look EARLIER than
            # it was, never later, so it cannot smuggle a post-deadline claim
            # into a pre-deadline snapshot.
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    return None


def _description_from_watch(html_text: str) -> str:
    match = _DESC_RE.search(html_text)
    if not match:
        return ""
    try:
        decoded = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        decoded = match.group(1)
    return strip_html(str(decoded))


# -- transcripts: implemented, documented, and off ---------------------------


class RobotsDisallowedRoute(RuntimeError):
    """Raised when a caller asks for a route youtube.com/robots.txt forbids."""


def fetch_transcript(
    fetcher: ContentFetcher,
    video_id: str,
    *,
    allow_disallowed_routes: bool = False,
) -> tuple[list[str], str]:
    """English captions. Refuses by default; see the module docstring.

    Both available routes -- ``youtube_transcript_api`` and the hand-rolled
    Innertube call -- terminate at ``/youtubei/``, which robots.txt disallows.
    Nothing in this package sets ``allow_disallowed_routes``.
    """
    if not allow_disallowed_routes:
        return [], "robots_disallowed_youtubei"

    lines = _transcript_via_library(video_id)
    if lines:
        return lines, "youtube_transcript_api"

    watch = fetcher.get(WATCH_URL.format(vid=video_id))
    if not watch.ok:
        return [], f"watch_{watch.status or watch.error}"
    key_match = _API_KEY_RE.search(watch.text)
    if not key_match:
        return [], "no_innertube_key"

    player = fetcher.post_json(
        f"https://www.youtube.com/youtubei/v1/player?key={key_match.group(1)}",
        {
            "context": {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38"}},
            "videoId": video_id,
        },
    )
    if not player.ok:
        return [], f"player_{player.status or player.error}"
    try:
        data = json.loads(player.body)
    except json.JSONDecodeError:
        return [], "player_not_json"

    tracks = (
        data.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    track = next((t for t in tracks if str(t.get("languageCode", "")).startswith("en")), None)
    if track is None or not track.get("baseUrl"):
        return [], "no_english_track"

    caption = fetcher.get(re.sub(r"&fmt=\w+$", "", str(track["baseUrl"])))
    if not caption.ok:
        return [], f"timedtext_{caption.status or caption.error}"
    try:
        root = ET.fromstring(caption.body)
    except ET.ParseError:
        return [], "timedtext_not_xml"
    texts = [
        strip_html(node.text or "")
        for node in root.iter()
        if node.tag in ("text", "p") and node.text
    ]
    texts = [t for t in texts if t]
    return (texts, "innertube") if texts else ([], "timedtext_empty")


def _transcript_via_library(video_id: str) -> list[str] | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        api = YouTubeTranscriptApi()
        return [snippet.text for snippet in api.fetch(video_id, languages=["en"])]
    except Exception:  # noqa: BLE001 - the library raises a wide family of its own
        return None
