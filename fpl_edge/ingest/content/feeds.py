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
* **A link is a link or it is nothing.** See :func:`resolve_link` below.

On links, because this cost the corpus its clickability for 353 of 372 podcast
items. The parser was not, as reported, "preferring ``<guid>`` over ``<link>``":
it read ``<link>`` first and always had. The actual shape of the failure is that
**most podcast items have no ``<link>`` element at all.** Measured over the 22
archived podcast feeds in ``data/raw/content/``:

===============================  =====  ======  ==========
feed                             items  <link>  <enclosure>
===============================  =====  ======  ==========
feeds.megaphone.fm/COMG4898871165  785       0        785
feeds.megaphone.fm/BLU5639728837   498       0        498
feeds.megaphone.fm/BLU8570913833   778       1        778
feeds.megaphone.fm/BLU9598812574   436     171        436
...                                ...     ...        ...
===============================  =====  ======  ==========

10 of 22 feeds emit ``<link>`` on zero or one item; **22 of 22 emit
``<enclosure>`` on every item**. The downstream loader then fell back to the
GUID (``url = entry.link or entry.guid``), and a Megaphone GUID is a bare UUID
-- ``74b8ffec-a205-11f1-9f9e-87719b00dbeb`` -- which is stored in a column
called ``url``, rendered as a link, and is not one.

So link resolution is now explicit, ordered, and validated against the only
property that matters: does it start with ``http://`` or ``https://``. A
candidate that does not is not considered, and if no candidate survives the
entry carries ``link=""`` and a ``link_reason``. A GUID is never a candidate
unless the feed itself says it is a permalink AND it is an http(s) URL, which is
exactly what RSS 2.0's ``isPermaLink`` means.
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


#: Rels on an ``<atom:link>`` that are not the entry's own web page. ``self`` is
#: the feed document, ``enclosure`` is the media file (captured separately, and
#: only used as a link of last resort), ``payment`` is a donation page that
#: several podcast hosts attach to every episode.
_NOT_ALTERNATE = frozenset(
    {"self", "enclosure", "edit", "replies", "payment", "hub", "via", "related",
     "next", "previous", "first", "last"}
)


def http_url(value: str | None) -> str | None:
    """The value if it is an absolute http(s) URL, else None. No repair.

    Deliberately not ``urljoin``-ing a relative path against the feed URL, and
    deliberately not accepting ``spotify:episode:...``, ``//host/path`` or a
    bare UUID. Every one of those would produce a string that *looks* like a
    link in a column called ``url``; only the first of them could be argued to
    be recoverable, and guessing a base host for it is fabrication.
    """
    if not value:
        return None
    candidate = value.strip()
    lowered = candidate.lower()
    if lowered.startswith(("http://", "https://")) and len(candidate) > 8:
        return candidate
    return None


class Enclosure:
    """The media file an item points at: the thing ASR needs, and its metadata.

    ``length`` is the feed's own ``length`` attribute in bytes, kept only when
    it is positive. Megaphone stamps ``length="0"`` on every episode; a
    zero-byte audio file is not a fact about the audio, it is a placeholder, and
    recording it as 0 would let a caller compute a bitrate from a lie. Absent
    and zero are both stored as None.
    """

    __slots__ = ("length", "mime_type", "url")

    def __init__(self, url: str, length: int | None, mime_type: str | None) -> None:
        self.url = url
        self.length = length
        self.mime_type = mime_type


class FeedEntry:
    __slots__ = (
        "body", "enclosure", "guid", "link", "link_basis", "link_reason",
        "published_at", "title",
    )

    def __init__(self, title: str, link: str, published_at: dt.datetime,
                 body: str, guid: str, *, link_basis: str | None = None,
                 link_reason: str | None = None,
                 enclosure: Enclosure | None = None) -> None:
        self.title = title
        #: An absolute http(s) URL, or "" when the feed offered none. Never a
        #: GUID, never a scheme-less or relative string.
        self.link = link
        self.published_at = published_at
        self.body = body
        self.guid = guid
        #: Which element the link came from: link | atom_alternate |
        #: guid_permalink | enclosure. None when ``link`` is "".
        self.link_basis = link_basis
        #: Why ``link`` is "", when it is. None when a link was resolved.
        self.link_reason = link_reason
        #: The audio/video file, when the item carries one.
        self.enclosure = enclosure

    @property
    def enclosure_url(self) -> str | None:
        return self.enclosure.url if self.enclosure is not None else None

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


def _rss_link(node: etree._Element) -> str | None:
    """``<link>`` as text, or as an ``href`` on an atom-shaped ``<link/>``."""
    for found in node.findall("link", namespaces=_NS):
        url = http_url(found.text) or http_url(found.get("href"))
        if url:
            return url
    return None


def _atom_alternate(node: etree._Element) -> str | None:
    """``<atom:link rel="alternate">`` -- the entry's own page.

    ``rel`` defaults to ``alternate`` when absent (RFC 4287 §4.2.7.2), so an
    unadorned ``<link href=...>`` counts. Everything in :data:`_NOT_ALTERNATE`
    is refused by name rather than by "not the first one we saw": the previous
    lookup took whichever ``atom:link`` came first, which on a feed that leads
    with ``rel="self"`` is the feed's own URL on every single item -- the same
    collapse-to-one-URL failure the loader's GUID identity rule exists to
    survive.
    """
    for found in node.findall("atom:link", namespaces=_NS):
        if (found.get("rel") or "alternate").strip().lower() in _NOT_ALTERNATE:
            continue
        url = http_url(found.get("href"))
        if url:
            return url
    return None


def _guid_permalink(node: etree._Element) -> str | None:
    """The GUID, but only when RSS 2.0 says it is a URL and it looks like one.

    ``isPermaLink`` defaults to ``"true"``. That default is safe here only
    because :func:`http_url` still has to pass: Megaphone's ``isPermaLink=
    "false"`` UUIDs are refused twice over, and a feed that omits the attribute
    while putting a UUID in the element is refused by the scheme check.
    """
    found = node.find("guid", namespaces=_NS)
    if found is None:
        return None
    if (found.get("isPermaLink") or "true").strip().lower() != "true":
        return None
    return http_url(found.text)


def _enclosure(node: etree._Element) -> Enclosure | None:
    """``<enclosure>``, or the atom spelling of it.

    Captured for its own sake and not only as a link fallback: it is the URL of
    the audio, and the audio is the content. Show notes are a sponsor read with
    a sentence of football in them; the episode is twenty minutes of the thing
    we are actually trying to measure. Local ASR needs this column to exist
    before it can turn one into the other.
    """
    candidates: list[tuple[str | None, str | None, str | None]] = []
    for found in node.findall("enclosure", namespaces=_NS):
        candidates.append((found.get("url"), found.get("length"), found.get("type")))
    for found in node.findall("atom:link", namespaces=_NS):
        if (found.get("rel") or "").strip().lower() == "enclosure":
            candidates.append((found.get("href"), found.get("length"), found.get("type")))
    for raw_url, raw_length, raw_type in candidates:
        url = http_url(raw_url)
        if not url:
            continue
        length: int | None = None
        try:
            parsed = int((raw_length or "").strip())
        except ValueError:
            parsed = 0
        if parsed > 0:
            length = parsed
        mime = (raw_type or "").strip() or None
        return Enclosure(url=url, length=length, mime_type=mime)
    return None


#: In preference order, with the reason each one sits where it does:
#:
#: 1. ``link`` -- what the publisher said the episode's page is.
#: 2. ``atom_alternate`` -- the same statement in the Atom dialect.
#: 3. ``guid_permalink`` -- the publisher asserting the id IS the page.
#: 4. ``enclosure`` -- the media file itself. Last because it is an mp3 rather
#:    than a page, so a human following it gets audio instead of show notes.
#:    Still vastly better than the alternative: it is a real, resolvable,
#:    clickable URL that plays the episode, and for 10 of the 22 registered
#:    podcast feeds it is the ONLY thing on offer.
LINK_BASES: tuple[str, ...] = ("link", "atom_alternate", "guid_permalink", "enclosure")

#: Recorded on the item when every candidate above came up empty.
NO_LINK_REASON = "feed_item_has_no_link_alternate_permalink_or_enclosure"


def resolve_link(
    node: etree._Element, enclosure: Enclosure | None
) -> tuple[str, str | None, str | None]:
    """(url, basis, reason). ``url`` is "" exactly when ``reason`` is set.

    The contract the caller depends on: this function returns an absolute
    http(s) URL or nothing at all. There is no third outcome, and in particular
    there is no outcome in which a GUID is returned as though it were a link.
    """
    for basis, url in (
        ("link", _rss_link(node)),
        ("atom_alternate", _atom_alternate(node)),
        ("guid_permalink", _guid_permalink(node)),
        ("enclosure", enclosure.url if enclosure is not None else None),
    ):
        if url:
            return url, basis, None
    return "", None, NO_LINK_REASON


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
        enclosure = _enclosure(node)
        link, link_basis, link_reason = resolve_link(node, enclosure)
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
        # Identity, NOT the link, and the two must not be conflated again. The
        # loader keys item_id on this value: several feeds put one constant
        # site URL on every item, so keying on the link collapsed 84 episodes
        # into 1. A GUID is a perfectly good identity and a terrible URL.
        guid = _first_text(node, ("guid", "atom:id", "yt:videoId")) or link
        entries.append(
            FeedEntry(
                title=strip_html(title),
                link=link,
                published_at=published,
                body=strip_html(body_raw),
                guid=guid.strip(),
                link_basis=link_basis,
                link_reason=link_reason,
                enclosure=enclosure,
            )
        )
    return entries, dropped
