"""RSS/Atom parsing without a new dependency.

``feedparser`` is the obvious choice and is deliberately not used: this package
may not add to ``pyproject.toml`` (it is shared with four other teams), and lxml
is already a project dependency. The subset of RSS 2.0 and Atom that FPL
podcasts and WordPress blogs actually emit is small.

Two details that are not cosmetic:

* **Dates are parsed strictly and never defaulted.** An item whose ``pubDate``
  cannot be read is dropped and counted, not stamped with "now". Stamping it
  with now would make an episode from 2024 look like it was published today,
  and it would then be visible to a snapshot it must not reach.
* **Timezone-naive dates are rejected the same way.** RFC 822 dates without an
  offset appear occasionally; guessing UTC for them shifts a claim by up to a
  day, and a day is the difference between before and after a deadline.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import re

from lxml import etree

UTC = dt.UTC

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "media": "http://search.yahoo.com/mrss/",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t ]+")


def strip_html(value: str) -> str:
    """HTML fragment -> readable text. Block tags become sentence breaks.

    Podcast show notes are a wall of ``<p>`` and ``<li>``; without the break the
    last word of one bullet and the first of the next fuse into a phrase that
    never existed, and the claim extractor reads across the seam.
    """
    if not value:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", value)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", ".\n", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


class UnparsableDate(ValueError):
    pass


#: RFC 5322 §3.3: "-0000" means the time is UTC but the sender's local offset is
#: unknown. Python's email parser signals that by returning a *naive* datetime,
#: which is correct per the spec and catastrophic here -- Megaphone stamps every
#: episode "-0000", so the strict rule below rejected 777 of 777 items from the
#: largest feeds in the registry before this case was handled. An explicit zero
#: offset is not a missing offset: it is UTC, stated. A date with no offset token
#: at all is still refused.
_EXPLICIT_ZERO_OFFSET = re.compile(r"[+-]00:?00\s*$")


def parse_feed_date(raw: str | None) -> dt.datetime:
    """RFC 822 or ISO 8601 -> aware UTC. Raises rather than guessing."""
    if not raw or not raw.strip():
        raise UnparsableDate("empty date")
    raw = raw.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        parsed = None
    if parsed is None:
        try:
            parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise UnparsableDate(f"unparsable date {raw!r}") from exc
    if parsed.tzinfo is None:
        if _EXPLICIT_ZERO_OFFSET.search(raw):
            return parsed.replace(tzinfo=UTC)
        raise UnparsableDate(
            f"date {raw!r} carries no UTC offset; refusing to assume one because "
            f"a day of drift can move a claim across a deadline"
        )
    return parsed.astimezone(UTC)


class FeedEntry:
    __slots__ = ("body", "guid", "link", "published_at", "title")

    def __init__(self, title: str, link: str, published_at: dt.datetime,
                 body: str, guid: str) -> None:
        self.title = title
        self.link = link
        self.published_at = published_at
        self.body = body
        self.guid = guid

    @property
    def text(self) -> str:
        """Title plus body. Both carry claims and the title carries the densest.

        "GW12 Captaincy: Salah or Haaland?" is a claim-bearing sentence in eight
        words; the description under it is often a sponsor read.
        """
        return f"{self.title}.\n{self.body}".strip()


def _first_text(node: etree._Element, paths: tuple[str, ...]) -> str | None:
    for path in paths:
        found = node.find(path, namespaces=_NS)
        if found is not None:
            if found.text and found.text.strip():
                return found.text
            href = found.get("href")
            if href:
                return href
    return None


def parse_feed(body: bytes) -> tuple[list[FeedEntry], int]:
    """Parse RSS 2.0 or Atom. Returns (entries, dropped_for_bad_date)."""
    parser = etree.XMLParser(recover=True, resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(body, parser=parser)
    except etree.XMLSyntaxError:
        return [], 0
    if root is None:
        return [], 0

    entries: list[FeedEntry] = []
    dropped = 0

    # RSS 2.0 first, Atom as the fallback. The two are handled by the same loop
    # below -- every lookup passes both spellings to `_first_text` -- so which
    # dialect matched is not information the parser needs to keep.
    nodes = root.findall(".//item") or root.findall(".//atom:entry", namespaces=_NS)

    for node in nodes:
        title = _first_text(node, ("title", "atom:title")) or ""
        link = _first_text(node, ("link", "atom:link")) or ""
        date_raw = _first_text(
            node, ("pubDate", "atom:published", "atom:updated", "published", "updated")
        )
        try:
            published = parse_feed_date(date_raw)
        except UnparsableDate:
            dropped += 1
            continue
        body_raw = _first_text(
            node,
            (
                "content:encoded",
                "media:group/media:description",
                "description",
                "itunes:summary",
                "atom:content",
                "atom:summary",
            ),
        ) or ""
        guid = _first_text(node, ("guid", "atom:id", "yt:videoId")) or link
        entries.append(
            FeedEntry(
                title=strip_html(title),
                link=link.strip(),
                published_at=published,
                body=strip_html(body_raw),
                guid=guid.strip(),
            )
        )
    return entries, dropped
