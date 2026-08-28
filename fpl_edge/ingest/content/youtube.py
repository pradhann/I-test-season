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
caller explicitly passes ``allow_disallowed_routes=True``. It exists so the
capability is documented and reviewable rather than quietly reintroduced later
by someone who reads the note above and concludes the library must be broken.

POLICY CHANGE, 2026-08-27: captions for the curated panel only
--------------------------------------------------------------

Everything above still describes the routes accurately. What changed is the
decision about them, and it was made by the repo owner, not by this code:

    "Why is the youtube off limits -- have a path for posting youtube links
    but also fetch for the popular ones."

The gate above was set by an earlier session and read the robots policy as an
absolute. The owner has re-read it as a question of *scale*, and re-decided.
The distinction that carries the whole change:

* **A general crawl** over 13 channels' back catalogues, thousands of videos,
  is what ``Disallow: /youtubei/`` is for. It stays refused. Nothing here
  enumerates a channel's archive through the caption route.
* **The curated panel**, :data:`PANEL_CREATORS` -- eight creator identities
  covering twelve of the sixteen people in the owner's panel
  (docs/platform/CREATOR_ELITE_PROMPT.md §4), six of which have a registered
  YouTube channel -- is a named, bounded list of people the owner has decided
  to follow, at a handful of recent videos each. That is the thing the owner
  asked for and it is what :func:`fetch_panel_captions` will do. It will not
  do it for anyone else: the creator is checked against the panel and an
  off-panel request is refused in code, not by convention.

The politeness terms that come with the change, all mandatory:

* the project User-Agent, never a browser string (``fetch.ContentFetcher``);
* at least :data:`PANEL_DELAY_S` seconds between requests, enforced per host;
* every raw body archived, and the audio cache in
  :mod:`fpl_edge.ingest.content.asr` consulted before the network, so nothing
  is fetched twice;
* the REAL http status recorded on the source row, failures included;
* **403 and 429 stop the run.** They are the source declining. A refusal is
  recorded and obeyed -- :attr:`PanelCaptions.refused` exists so the caller
  cannot accidentally treat it as an ordinary miss and carry on.

Recorded in docs/data_sources.md §7A, with the reasoning, so the reversal is
auditable rather than folklore.
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

#: Minimum seconds between requests on the panel caption path. Double the
#: package default: this route is used by explicit owner decision rather than
#: by robots.txt permission, so the rate has to be visibly conservative.
PANEL_DELAY_S = 2.0

#: The curated panel, as creator identities that match
#: ``sources.Source.creator``. Copied from the owner's seed table in
#: docs/platform/CREATOR_ELITE_PROMPT.md §4 and mapped onto the content
#: registry, one line per panel member so the mapping is checkable:
#:
#:   Mark Sutherns .......... FPL BlackBox
#:   Ben Crellin ............ Fantasy Football Hub
#:   FPL Salah .............. Fantasy Football Hub
#:   FPL Harry .............. FPL Harry
#:   FPL Pras ............... The FPL Wire
#:   Az Phillips ............ FPL BlackBox / Fantasy Football Scout
#:   Andy ................... Let's Talk FPL
#:   Lee Bonfield ........... FPL Family
#:   Big Man Bakar .......... Fantasy Football Hub
#:   Trophy FPL ............. Fantasy Football Scout
#:   Tom .................... Who Got The Assist?
#:   Sam Bonfield ........... FPL Family
#:
#: Four panel members are deliberately absent because they publish nowhere in
#: the content registry: Ash (FPL Hints), Josh (FPL Graduates), Sertalp B. Cay
#: and Erik Ibsen. They are named here rather than silently dropped, because
#: "the panel is nine people" and "we fetch for nine people" being different
#: numbers is exactly the sort of drift this constant exists to prevent.
#:
#: THIS IS A CEILING, NOT A ROSTER, and it is a code constant on purpose.
#: ``fpl_edge/ingest/content/panel.py`` owns the live roster in
#: ``data/panels/creator_panel_2026_27.yaml``, and a run may legitimately be
#: narrower than this set. It may not be wider. A fetch permission that a data
#: file can widen is not a permission, it is a default -- editing a YAML would
#: silently extend a crawl the owner scoped to named people. Raising the
#: ceiling is an edit to this line, in a diff, with the owner's decision behind
#: it. :func:`divergence_from_roster` reports shows the roster carries that
#: this ceiling does not, so the gap is visible rather than silent.
#: The shows the owner named, and ONLY those. Deliberately a code constant
#: rather than a read of data/panels/creator_panel_2026_27.yaml: a fetch
#: permission that a YAML edit can widen is a default, not a permission. It is
#: a CEILING -- a run may be narrower, never wider.
#:
#: Revised 2026-08-27 on the owner's explicit instruction. He named FPL Wire,
#: Solio, FPL Harry, FPL Raptor and FPL Mark (BlackBox), then added Andy
#: (Let's Talk FPL); Crellin, Az Phillips and FPL Salah came in on measured
#: record. The previous list was inherited from the older sixteen-person panel
#: in CREATOR_ELITE_PROMPT.md §4: it REFUSED FPL Raptor, whom the owner named,
#: while permitting FPL Family, Who Got The Assist? and Fantasy Football Scout,
#: whom he did not. Net effect is narrower, not wider -- three shows dropped,
#: two added.
PANEL_CREATORS: frozenset[str] = frozenset({
    "The FPL Wire",          # Pras, Zophar, Lateriser, BigMan Bakar
    "Let's Talk FPL",        # Andy
    "FPL Harry",             # Harry Daniels
    "FPL Raptor",            # Ross Dowsett
    "FPL BlackBox",          # Mark Sutherns, Az Phillips
    "Fantasy Football Hub",  # Ben Crellin, FPL Salah
    "Solio Analytics",       # Cay, Currie, Gjaerum, Palmer
})

#: Panel members with no source in the registry. Reported, not fetched.
#: Panel members with no source in the registry: named by the owner, but there
#: is nothing registered to fetch. Reported so the gap is visible, never
#: fetched -- a name in the ceiling that resolves to no source would be a
#: permission to fetch something nobody has defined. Solio Analytics is the
#: live case: the owner named it, and its four co-founders carry verified
#: entry ids, but no feed for it exists yet.
PANEL_WITHOUT_SOURCE: tuple[str, ...] = (
    "Solio Analytics",
)

_API_KEY_RE = re.compile(r'"INNERTUBE_API_KEY":"([^"]+)"')
_VIDEO_ID_RE = re.compile(r'"videoId":"([\w-]{11})"')
_PUBLISH_RE = re.compile(r'itemprop="datePublished" content="([^"]+)"')
_PUBLISH_JSON_RE = re.compile(r'"publishDate":"([0-9]{4}-[0-9]{2}-[0-9]{2}[^"]*)"')
_TITLE_RE = re.compile(r'<meta name="title" content="([^"]*)"')
_DESC_RE = re.compile(r'"shortDescription":"(.*?)","isCrawlable"', re.DOTALL)
_EXTERNAL_ID_RE = re.compile(r'"externalId":"(UC[\w-]{22})"')

# -- who published it ---------------------------------------------------------
#
# The watch page states the channel explicitly and this module used to throw it
# away, which is why every pasted link was filed under the placeholder creator
# ``user-shared``. Verified live on 2026-08-27 against a FPL Raptor watch page:
#
#     "author":"FPL Raptor"
#     "ownerChannelName":"FPL Raptor"
#     "externalChannelId":"UC54QLWzsMifTRjNQ02z5pCw"
#
# ``ownerChannelName`` is preferred over ``author`` because ``author`` also
# appears in unrelated embedded JSON-LD blocks on some pages; the owner name is
# only ever emitted by the microformat renderer for the video's own channel.
# The UC id is preferred over BOTH, because it is the key the source registry
# already stores and a display name can be changed by its owner at any time.
#
# POLITENESS: these patterns are read from a response the caller has ALREADY
# fetched. :func:`channel_from_watch` takes text, never a fetcher, so it cannot
# issue a request -- the extra identity costs the source exactly zero
# additional hits, which is the condition docs/data_sources.md 7A rests on.
_OWNER_NAME_RE = re.compile(r'"ownerChannelName"\s*:\s*"((?:[^"\\]|\\.)*)"')
_AUTHOR_RE = re.compile(r'"author"\s*:\s*"((?:[^"\\]|\\.)*)"')
_ITEMPROP_NAME_RE = re.compile(r'<link\s+itemprop="name"\s+content="([^"]*)"')
_OWNER_ID_RE = re.compile(
    r'"(?:externalChannelId|ownerChannelId|channelId)"\s*:\s*"(UC[\w-]{22})"'
)


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


def published_from_watch(html_text: str) -> dt.datetime | None:
    """The video's real publication instant, from a page already fetched.

    Public because the pasted-link route needs it: it was stamping every
    pasted item with the moment of the PASTE, which made the gameweek
    inference ("the next deadline after publication") answer a question about
    today rather than about the video. Like :func:`channel_from_watch` this
    takes text and issues no request.
    """
    return _published_from_watch(html_text)


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


def description_from_watch(html_text: str) -> str:
    """The video's own description, from a page already fetched.

    Public because the paste-a-link preview shows it: the owner decides
    whether a video is worth 105 GPU-seconds by reading who made it and what
    they say it is about, and the description is most of the second half. Like
    the other readers in this section it takes text and issues no request.
    """
    return _description_from_watch(html_text)


def _description_from_watch(html_text: str) -> str:
    match = _DESC_RE.search(html_text)
    if not match:
        return ""
    try:
        decoded = json.loads(f'"{match.group(1)}"')
    except json.JSONDecodeError:
        decoded = match.group(1)
    return strip_html(str(decoded))


# -- who published it: the channel, and what it maps to ----------------------


@dataclass(frozen=True, slots=True)
class Channel:
    """The publishing channel as the watch page itself states it.

    ``name`` is the display name verbatim; ``channel_id`` the canonical ``UC``
    id. ``basis`` names the field that answered, or -- when nothing did -- the
    reason, because a caller that cannot say WHERE a name came from has no
    business filing content under it.
    """

    name: str | None
    channel_id: str | None
    basis: str

    @property
    def known(self) -> bool:
        return bool(self.name or self.channel_id)


def _unescape_json_string(raw: str) -> str:
    try:
        return str(json.loads(f'"{raw}"'))
    except json.JSONDecodeError:
        return raw


def channel_from_watch(html_text: str) -> Channel:
    """Read the channel off a watch page that has ALREADY been fetched.

    Takes text, not a fetcher, and issues no request: the identity is a free
    read of a body the caller holds. That is what keeps the politeness terms in
    ``docs/data_sources.md`` 7A true across this change -- the request count is
    unchanged, only the number of fields parsed out of the response moved.

    Order matters. ``ownerChannelName`` is the microformat renderer's statement
    about the video's own channel; ``author`` also occurs in embedded JSON-LD
    for unrelated entities on some pages, so it is the fallback rather than the
    first read.
    """
    name = None
    basis = "no channel field on the page"
    for pattern, label in ((_OWNER_NAME_RE, "ownerChannelName"),
                           (_ITEMPROP_NAME_RE, "itemprop=name"),
                           (_AUTHOR_RE, "author")):
        match = pattern.search(html_text)
        if match and match.group(1).strip():
            name = strip_html(_unescape_json_string(match.group(1))).strip()
            basis = label
            break
    id_match = _OWNER_ID_RE.search(html_text)
    channel_id = id_match.group(1) if id_match else None
    if name is None and channel_id is not None:
        basis = "externalChannelId only; the page named no channel"
    return Channel(name=name or None, channel_id=channel_id, basis=basis)


def fold_creator(name: str | None) -> str:
    """Case-, space- and punctuation-insensitive key for creator identity.

    Folds to lowercase alphanumerics so ``"Let's Talk FPL"``, ``"lets talk
    fpl"`` and the ``@LetsTalkFPL`` handle are one key. It is still an EXACT
    comparison after folding -- no containment, no edit distance. "FPL Harry"
    and "FPL Harry Clips" stay different creators, which is the point: a
    near-match that becomes an attribution is a fabricated name with extra
    steps.
    """
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


@dataclass(frozen=True, slots=True)
class CreatorMatch:
    """What a channel resolved to, and how. ``creator`` is None when nothing did.

    ``tracked`` answers the question the creator board actually asks: is this
    identity one the panel scope admits? A resolved-but-untracked channel is a
    real, named creator whose content the board legitimately excludes -- the
    reader still sees WHO said it.
    """

    creator: str | None
    basis: str
    reason: str
    tracked: bool = False

    @property
    def resolved(self) -> bool:
        return self.creator is not None


def creator_for_channel(channel: Channel, *,
                        panel_shows: frozenset[str] | set[str] = frozenset(),
                        ) -> CreatorMatch:
    """Map a watch page's channel onto a registry / panel creator identity.

    Three exact matches, strongest first, and NO fuzzy fallback:

    1. the canonical ``UC`` id against ``sources.YOUTUBE_SOURCES[*].channel_id``
       -- the strongest key there is, because a display name is renameable and
       the id is not;
    2. the folded display name (or ``@handle``) against every registered
       source's creator;
    3. the folded display name against the live panel roster's ``show_creator``
       values, passed in by the caller so this module never opens a warehouse.

    An unmatched channel is NOT an error and NOT a gap to fill. It comes back
    with ``creator=None`` and a reason; the caller keeps the real channel name
    as the creator and marks the item untracked. What must never happen is a
    name being invented for it.

    An EMPTY ``panel_shows`` means the roster could not be read, not that the
    panel is empty, so a registry match degrades UPWARD to ``tracked=True`` --
    the same direction ``creators._panel_shows`` degrades in. An unreadable
    roster must not look like a world in which nobody is tracked.
    """
    from fpl_edge.ingest.content.sources import ALL_SOURCES, YOUTUBE_SOURCES

    if channel.channel_id:
        for source in YOUTUBE_SOURCES:
            if source.channel_id == channel.channel_id:
                return CreatorMatch(
                    source.creator, "channel_id",
                    f"the watch page's channel id {channel.channel_id} is "
                    f"registered to {source.creator} ({source.key})",
                    tracked=str(source.creator) in set(panel_shows) or not panel_shows,
                )

    key = fold_creator(channel.name)
    if key:
        for source in ALL_SOURCES:
            if fold_creator(source.creator) == key or (
                source.handle and fold_creator(source.handle) == key
            ):
                return CreatorMatch(
                    source.creator, "channel_name",
                    f"the channel name {channel.name!r} is exactly a "
                    f"registered creator ({source.key})",
                    tracked=str(source.creator) in set(panel_shows) or not panel_shows,
                )
        for show in panel_shows:
            if fold_creator(show) == key:
                return CreatorMatch(
                    str(show), "panel_show",
                    f"the channel name {channel.name!r} is exactly a show on "
                    f"the owner's panel roster",
                    tracked=True,
                )

    if channel.name:
        return CreatorMatch(
            None, "unregistered_channel",
            f"the page names the channel {channel.name!r}, and no registered "
            f"source or panel show has that identity. The real name is kept "
            f"and the item is marked pasted-but-not-tracked rather than "
            f"mapped onto a creator it does not belong to.",
        )
    return CreatorMatch(
        None, "no_channel_on_page",
        f"the page did not state a channel ({channel.basis}), so there is no "
        f"identity to resolve and none is invented.",
    )


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


# -- panel captions: timestamped, bounded, and refusable -------------------


class OffPanelRefused(RuntimeError):
    """A caption fetch was asked for a creator who is not on the panel.

    Raised, not returned. The scale limit is the entire justification for the
    2026-08-27 policy change, so a caller that widens it has made a mistake
    that must stop the run rather than quietly enlarge the crawl.
    """


def is_panel_creator(creator: str | None) -> bool:
    """Is this creator one the owner named in the panel?"""
    return bool(creator) and str(creator).strip() in PANEL_CREATORS


def panel_youtube_sources() -> tuple[object, ...]:
    """The registered YouTube sources belonging to panel creators.

    Six of the thirteen registered channels, as of 2026-08-27. The gap between
    those two numbers is the policy: the other seven channels are still
    description-only, and that is on purpose.
    """
    from fpl_edge.ingest.content.sources import YOUTUBE_SOURCES

    return tuple(s for s in YOUTUBE_SOURCES if is_panel_creator(s.creator))


def divergence_from_roster(warehouse) -> tuple[str, ...]:
    """Shows the live panel roster carries that :data:`PANEL_CREATORS` does not.

    Reported, never acted on. The roster in ``panel_person_show`` is curated
    and may legitimately move ahead of this module's ceiling; when it does, the
    caption route refuses those shows and this function is how the owner finds
    out, instead of discovering it as an absence six weeks later.

    Returns an empty tuple when the panel tables do not exist yet.
    """
    try:
        rows = warehouse.sql(
            "SELECT DISTINCT s.show_creator FROM panel_person_show s "
            "JOIN panel_person p USING (person_key) WHERE p.active"
        )
    except Exception:  # noqa: BLE001 - another team's table, may not exist
        return ()
    return tuple(sorted(
        str(v) for v in rows["show_creator"] if str(v) not in PANEL_CREATORS
    ))


@dataclass(frozen=True, slots=True)
class TimedLine:
    """One caption cue. ``start_s`` is what a deep link points at."""

    start_s: float
    text: str


@dataclass(frozen=True, slots=True)
class PanelCaptions:
    """The outcome of one panel caption fetch, refusals included.

    ``refused`` is separate from "no captions" on purpose. A video with
    captions disabled is a fact about that video and the run continues; a 403
    or a 429 is YouTube declining to serve *us*, and the run must stop rather
    than work through the rest of the queue collecting more of them.
    """

    video_id: str
    lines: tuple[TimedLine, ...]
    route: str
    status: int | None = None
    refused: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.lines)

    @property
    def text(self) -> str:
        return " ".join(line.text for line in self.lines).strip()


def panel_fetcher(*, delay_s: float = PANEL_DELAY_S):
    """A :class:`ContentFetcher` configured for the panel caption route.

    ``respect_robots=False`` is the whole policy change in one keyword, and it
    is confined to this function so it is greppable. It is NOT a general
    licence: :func:`fetch_panel_captions` still refuses any creator off the
    panel, and every other fetcher in this package keeps the robots check on.
    The delay is doubled rather than left at the package default.
    """
    from fpl_edge.ingest.content.fetch import ContentFetcher

    return ContentFetcher("youtube_panel", delay_s=delay_s, respect_robots=False)


def fetch_panel_captions(
    fetcher: ContentFetcher, video_id: str, *, creator: str
) -> PanelCaptions:
    """English captions with timestamps, for a panel creator's video only.

    Raises :class:`OffPanelRefused` when ``creator`` is not on the panel. That
    check is first, before any network call, so an off-panel request costs
    YouTube nothing at all.
    """
    if not is_panel_creator(creator):
        raise OffPanelRefused(
            f"{creator!r} is not on the curated panel. The 2026-08-27 policy "
            f"permits caption fetching for {sorted(PANEL_CREATORS)} and for no "
            f"one else; widening it is a decision for the owner, not a default."
        )

    # ``youtube_transcript_api`` is installed and would answer this in one
    # call. It is deliberately NOT used here. It builds its own HTTP client:
    # its own User-Agent, no inter-request delay, and no status we can record
    # on the source row. Politeness is the entire justification the owner's
    # policy change rests on, so this path goes through ContentFetcher --
    # project UA, PANEL_DELAY_S between requests, the real status carried back
    # -- even though it costs three requests instead of one. The library is
    # still used by the owner-shared-link path in interfaces/creators.py,
    # which is a single video at a human's explicit request.
    watch = fetcher.get(WATCH_URL.format(vid=video_id))
    if watch.status in (403, 429):
        return PanelCaptions(video_id, (), f"watch_{watch.status}", watch.status,
                             refused=True)
    if not watch.ok:
        return PanelCaptions(video_id, (), f"watch_{watch.status or watch.error}",
                             watch.status)
    key_match = _API_KEY_RE.search(watch.text)
    if not key_match:
        return PanelCaptions(video_id, (), "no_innertube_key", watch.status)

    player = fetcher.post_json(
        f"https://www.youtube.com/youtubei/v1/player?key={key_match.group(1)}",
        {
            "context": {"client": {"clientName": "ANDROID", "clientVersion": "20.10.38"}},
            "videoId": video_id,
        },
    )
    if player.status in (403, 429):
        return PanelCaptions(video_id, (), f"player_{player.status}", player.status,
                             refused=True)
    if not player.ok:
        return PanelCaptions(video_id, (), f"player_{player.status or player.error}",
                             player.status)
    try:
        data = json.loads(player.body)
    except json.JSONDecodeError:
        return PanelCaptions(video_id, (), "player_not_json", player.status)

    tracks = (
        data.get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )
    track = next((t for t in tracks if str(t.get("languageCode", "")).startswith("en")), None)
    if track is None or not track.get("baseUrl"):
        return PanelCaptions(video_id, (), "no_english_track", player.status)

    caption = fetcher.get(re.sub(r"&fmt=\w+$", "", str(track["baseUrl"])))
    if caption.status in (403, 429):
        return PanelCaptions(video_id, (), f"timedtext_{caption.status}",
                             caption.status, refused=True)
    if not caption.ok:
        return PanelCaptions(video_id, (),
                             f"timedtext_{caption.status or caption.error}",
                             caption.status)
    lines = timed_lines_from_xml(caption.body)
    route = "innertube" if lines else "timedtext_empty"
    return PanelCaptions(video_id, tuple(lines), route, caption.status)


def timed_lines_from_xml(body: bytes) -> list[TimedLine]:
    """timedtext XML -> cues with start times.

    The untimed sibling of this function threw the ``start`` attribute away,
    which is why the two stored transcripts in the warehouse cannot be deep
    linked. A cue with no parsable start is dropped rather than defaulted to
    0.0: a link that jumps to the beginning of a fifty-minute video is worse
    than no link, because it looks like it worked.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    out: list[TimedLine] = []
    for node in root.iter():
        if node.tag not in ("text", "p"):
            continue
        text = strip_html(node.text or "")
        if not text:
            continue
        raw = node.get("start") if node.get("start") is not None else node.get("t")
        if raw is None:
            continue
        try:
            start = float(raw)
        except ValueError:
            continue
        # The Android/`p` dialect gives milliseconds; the classic `text`
        # dialect gives seconds. Mixing them up puts every deep link a
        # thousand times too late.
        if node.tag == "p" and node.get("t") is not None:
            start /= 1000.0
        out.append(TimedLine(start_s=start, text=text))
    return out
