"""Source kind -> :class:`ContentItem` list, with every failure reported.

Nothing in here raises on a bad source. A source that 404s, times out, serves
malformed XML or is refused on policy grounds produces zero items and a
:class:`~fpl_edge.ingest.content.sources.ProbeResult` saying why, and the run
continues. The alternative -- an exception that aborts the pipeline -- means one
dead podcast feed on a Thursday night costs the whole corpus before a deadline.
"""

from __future__ import annotations

import datetime as dt
import re

from fpl_edge.ingest.content.feeds import parse_feed, strip_html
from fpl_edge.ingest.content.fetch import ContentFetcher
from fpl_edge.ingest.content.models import ContentItem
from fpl_edge.ingest.content.sources import AccessPolicy, ProbeResult, Source, SourceKind
from fpl_edge.ingest.content.youtube import videos_from_channel_page

UTC = dt.UTC

_ARTICLE_RE = re.compile(
    r"(?is)<(?:article|main)\b[^>]*>(.*?)</(?:article|main)>"
)


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def load_feed_source(
    fetcher: ContentFetcher,
    source: Source,
    *,
    max_items: int | None = None,
    since: dt.datetime | None = None,
) -> tuple[list[ContentItem], ProbeResult]:
    """Podcast or blog RSS. The body is the show notes / excerpt."""
    if source.policy is not AccessPolicy.OPEN:
        return [], ProbeResult(
            source.key, source.url, None, 0, 0,
            skipped_reason="policy", error=None,
        )
    resp = fetcher.get(source.url)
    if not resp.ok:
        return [], ProbeResult(
            source.key, source.url, resp.status, len(resp.body), 0,
            error="robots_disallow" if resp.robots_blocked else resp.error,
        )
    entries, bad_dates = parse_feed(resp.body)
    fetched = _now()
    items: list[ContentItem] = []
    for entry in entries:
        if since is not None and entry.published_at < since:
            continue
        url = entry.link or entry.guid
        # Identity comes from the GUID, not the link. Several feeds -- Sky
        # Sports FPL and FPL JUiCE among them -- put a single constant site URL
        # on every item, so keying on the link collapsed 84 episodes into 1 and
        # 378 into 1. That failure was silent: the feed returned 200, parsed to
        # the right item count, and then quietly lost 99% of the archive at
        # insert time because every row deduplicated onto the same id.
        identity = entry.guid or url
        text = entry.text
        if source.excerpt_only and entry.link:
            article = _fetch_article(fetcher, entry.link)
            if article:
                text = f"{entry.title}.\n{article}"
        items.append(
            ContentItem(
                item_id=ContentItem.make_id(source.key, identity),
                source_key=source.key,
                creator=source.creator,
                kind=str(source.kind),
                title=entry.title,
                url=url,
                published_at=entry.published_at,
                text=text,
                fetched_at=fetched,
                text_source="article" if source.excerpt_only else "description",
            )
        )
        if max_items is not None and len(items) >= max_items:
            break
    _ = bad_dates
    return items, ProbeResult(
        source.key, source.url, resp.status, len(resp.body), len(entries)
    )


def _fetch_article(fetcher: ContentFetcher, url: str) -> str:
    resp = fetcher.get(url)
    if not resp.ok:
        return ""
    match = _ARTICLE_RE.search(resp.text)
    body = match.group(1) if match else resp.text
    text = strip_html(body)
    return text[:20000]


def load_youtube_source(
    fetcher: ContentFetcher,
    source: Source,
    *,
    max_videos: int = 8,
    since: dt.datetime | None = None,
) -> tuple[list[ContentItem], ProbeResult]:
    """A channel's recent uploads: title plus description, from permitted pages.

    Not the Atom feed and not transcripts. Both are Disallowed in
    ``youtube.com/robots.txt`` -- see :mod:`fpl_edge.ingest.content.youtube` for
    the measurement showing the transcript route works and the reason it is
    unused anyway.
    """
    if source.policy is not AccessPolicy.OPEN:
        return [], ProbeResult(source.key, source.url, None, 0, 0, skipped_reason="policy")

    handle = source.handle or source.key.removeprefix("yt_")
    videos, resp = videos_from_channel_page(fetcher, handle, limit=max_videos)
    if not videos:
        return [], ProbeResult(
            source.key, source.url, resp.status, len(resp.body), 0,
            error="robots_disallow" if resp.robots_blocked else (resp.error or "no_videos"),
        )

    if since is not None:
        videos = [v for v in videos if v.published_at >= since]

    fetched = _now()
    items = [
        ContentItem(
            item_id=ContentItem.make_id(source.key, video.url),
            source_key=source.key,
            creator=source.creator,
            kind=str(source.kind),
            title=video.title,
            url=video.url,
            published_at=video.published_at,
            text=video.text,
            fetched_at=fetched,
            text_source="description",
        )
        for video in videos
    ]
    return items, ProbeResult(
        source.key, source.url, resp.status, len(resp.body), len(items)
    )


def load_source(
    fetcher: ContentFetcher, source: Source, **kwargs: object
) -> tuple[list[ContentItem], ProbeResult]:
    if source.kind is SourceKind.YOUTUBE:
        return load_youtube_source(
            fetcher, source,
            max_videos=int(kwargs.get("max_videos", 8)),  # type: ignore[arg-type]
            since=kwargs.get("since"),  # type: ignore[arg-type]
        )
    if source.kind in (SourceKind.PODCAST, SourceKind.BLOG):
        return load_feed_source(
            fetcher, source,
            max_items=kwargs.get("max_items"),  # type: ignore[arg-type]
            since=kwargs.get("since"),  # type: ignore[arg-type]
        )
    return [], ProbeResult(
        source.key, source.url, None, 0, 0,
        skipped_reason="policy" if source.policy is not AccessPolicy.OPEN else "unsupported",
    )
