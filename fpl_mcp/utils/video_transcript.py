"""YouTube transcripts for the toolbelt, through fpl_edge's sanctioned route.

WHAT THIS MODULE USED TO BE, AND WHY IT CHANGED (PIPELINES.md §3 defect 2)
--------------------------------------------------------------------------
The first version of this file was a second, hand-rolled Innertube client:
bare ``requests`` against ``/youtubei/v1/player`` and ``timedtext`` -- the
exact endpoints ``youtube.com/robots.txt`` disallows and the exact route
:mod:`fpl_edge.ingest.content.youtube` refuses by default -- with no robots
check, a browserless-but-unidentified client, no archive, and every failure
collapsed to ``[]`` so a refusal and an empty video were indistinguishable.

It now delegates to the engine's one sanctioned caption path,
:func:`fpl_edge.ingest.content.youtube.fetch_panel_captions`, and inherits its
whole policy rather than restating it:

* **Panel creators only.** The 2026-08-27 owner decision permits caption
  fetching for the named creators in ``PANEL_CREATORS`` and nobody else.
  The check is the engine's (:class:`OffPanelRefused` is raised in code, not
  by convention); this module surfaces it as an honest message instead of
  an empty list.
* **Off-panel videos are not fetched here at all.** The sanctioned way to
  transcribe an arbitrary single video is the platform's paste-a-link flow
  (POST ``/api/ingest/link``), which HALTS at a preview so a human decides
  before anything is transcribed. The refusal message says so.
* **Politeness is the engine's**: project User-Agent, ``PANEL_DELAY_S``
  between requests, every body archived, and a 403/429 obeyed as the source
  declining -- reported, never retried, never silently swallowed.

Nothing here returns a bare ``[]`` any more: every empty transcript carries
the reason it is empty, and the MCP tools pass that reason to the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

#: Where an off-panel video goes instead: the platform's preview-gated
#: single-video route. Quoted in refusal messages so the caller is pointed at
#: the sanctioned path rather than left with a dead end.
PASTE_LINK_FLOW = (
    "paste the link into the platform's Creators tab (POST /api/ingest/link): "
    "that route fetches one page, shows a preview of who published it and what "
    "it is about, and transcribes only after a human accepts."
)


def extract_video_id(url: str) -> Optional[str]:
    """Extract the YouTube video ID from a full or shortened URL.

    Args:
        url: A string representing a YouTube watch URL or short URL.

    Returns:
        The 11-character video ID if found, otherwise ``None``.

    Examples::

        >>> extract_video_id("https://www.youtube.com/watch?v=abc123def45")
        'abc123def45'
        >>> extract_video_id("https://youtu.be/abc123def45")
        'abc123def45'
    """
    patterns = [
        r"youtube\.com/watch\?v=([\w-]{11})",
        r"youtu\.be/([\w-]{11})",
        r"youtube\.com/embed/([\w-]{11})",
        r"youtube\.com/(?:shorts|live)/([\w-]{11})",
    ]
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            return match.group(1)
    return None


@dataclass(frozen=True)
class TranscriptResult:
    """One transcript attempt, refusals included.

    ``lines`` empty always comes with a ``reason``: the caller can render the
    refusal instead of pretending the video had nothing to say. ``route``
    names what happened mechanically ("innertube", "off_panel",
    "source_refused_403", ...) so the outcome is greppable.
    """

    video_id: str
    lines: tuple[str, ...]
    route: str
    reason: Optional[str] = None
    creator: Optional[str] = None
    channel: Optional[str] = None

    @property
    def ok(self) -> bool:
        return bool(self.lines)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def _refused(video_id: str, route: str, reason: str, *,
             creator: str | None = None,
             channel: str | None = None) -> TranscriptResult:
    return TranscriptResult(video_id=video_id, lines=(), route=route,
                            reason=reason, creator=creator, channel=channel)


def get_transcript(video_id: str, *, fetcher=None) -> TranscriptResult:
    """English captions for a panel creator's video, else an honest refusal.

    Delegates entirely to :mod:`fpl_edge.ingest.content.youtube`: the watch
    page identifies the channel, the channel resolves to a creator, and only a
    creator on the owner's curated panel reaches the caption route. Everything
    else comes back with the reason and a pointer at the paste-a-link flow.

    ``fetcher`` is injectable for tests; by default the engine's
    :func:`panel_fetcher` supplies the project UA, the doubled inter-request
    delay and the archive. The identity read costs one watch-page fetch and
    :func:`fetch_panel_captions` fetches the watch page again itself -- two
    hits where one would do, accepted as the price of reusing the sanctioned
    function unmodified rather than growing a second caption client here.
    """
    from fpl_edge.ingest.content.youtube import (
        WATCH_URL,
        OffPanelRefused,
        channel_from_watch,
        creator_for_channel,
        fetch_panel_captions,
        panel_fetcher,
    )

    own_fetcher = fetcher is None
    if own_fetcher:
        fetcher = panel_fetcher()
    try:
        watch = fetcher.get(WATCH_URL.format(vid=video_id), retries=0)
        if watch.status in (403, 429):
            return _refused(
                video_id, f"source_refused_{watch.status}",
                f"YouTube returned {watch.status} for this video. That is the "
                f"source declining, and it is obeyed rather than retried.",
            )
        if not watch.ok:
            detail = watch.status if watch.status is not None else watch.error
            return _refused(
                video_id, "watch_unreadable",
                f"the watch page returned {detail}, so the video's channel "
                f"cannot be identified and no transcript route can be chosen.",
            )

        channel = channel_from_watch(watch.text)
        match = creator_for_channel(channel)
        creator = match.creator or channel.name

        try:
            captions = fetch_panel_captions(fetcher, video_id,
                                            creator=str(creator or ""))
        except OffPanelRefused as exc:
            # The engine's own refusal, verbatim, plus the sanctioned way out.
            return _refused(
                video_id, "off_panel",
                f"{exc} For a one-off transcript of this video, "
                f"{PASTE_LINK_FLOW}",
                creator=creator, channel=channel.name,
            )

        if captions.refused:
            return _refused(
                video_id, captions.route,
                f"YouTube declined ({captions.route}, HTTP {captions.status}). "
                f"A refusal is obeyed, not retried.",
                creator=creator, channel=channel.name,
            )
        if not captions.ok:
            return _refused(
                video_id, captions.route,
                f"no English captions could be read for this video "
                f"({captions.route}). Nothing was invented in their place.",
                creator=creator, channel=channel.name,
            )
        return TranscriptResult(
            video_id=video_id,
            lines=tuple(line.text for line in captions.lines),
            route=captions.route,
            creator=creator, channel=channel.name,
        )
    finally:
        if own_fetcher:
            fetcher.close()
