"""Reading a pasted link without writing anything.

Everything here is read-only from end to end: the warehouse is consulted
through a read copy and nothing is written whatever the outcome. The preflight
decides what a URL IS (a YouTube video, an article, something unusable), what
it would cost to ingest, and whether the archive already holds it, so the owner
approves a known price rather than a guess.

Canonical identity is the video id, not the URL: ``watch?v=``, ``youtu.be/``,
``/live/`` and ``/embed/`` are one video, and the id comes from the one
authority in ``fpl_edge.ingest.content.urls``.

Split out of the 1,700-line ``fpl_edge/platform/link_jobs.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 17). The package
``__init__`` re-exports the surface the flat module had.
"""

from __future__ import annotations
import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from fpl_edge.store.warehouse import DEFAULT_DB


UTC = dt.timezone.utc


STAGES: tuple[str, ...] = ("fetch", "preview", "transcribe", "analyse", "attribute")


CAPTION_RATE = 286.0     # published caption cues, x realtime


ASR_RATE = 11.5          # local mlx-whisper, x realtime


_STAGE_SPAN: dict[str, tuple[int, int]] = {
    "fetch": (0, 8),
    "preview": (8, 10),
    "transcribe": (10, 70),
    "analyse": (70, 90),
    "attribute": (90, 100),
}


MIN_SUBSTANTIVE_CHARS = 400


PREVIEW_TTL = dt.timedelta(minutes=30)


_NON_EPISODE: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^https?://(?:[\w-]+\.)*premierleague\.com/", re.I),
     ("an FPL app page (league invite, squad, transfers), not a published "
      "episode or article. A league invite was once stored as an article "
      "titled 'a6fgym' with three characters of text; that is what this "
      "refusal exists to prevent.")),
    (re.compile(r"^https?://(?:[\w-]+\.)*youtube\.com/(?:@|c/|user/|channel/|"
                r"playlist|results|feed)", re.I),
     ("a YouTube channel, playlist or search page rather than a single video. "
      "Paste one video; the bulk crawler owns whole channels.")),
)


_AUDIO_SUFFIXES = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".mp4",
                   ".mpeg", ".mpga")


_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


_LENGTH_RE = re.compile(r'"lengthSeconds"\s*:\s*"(\d+)"')


_CAPTION_TRACKS = '"captionTracks"'


class LinkRefused(ValueError):
    """The request cannot become a job at all (no URL, or not an http URL)."""


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _iso(moment: dt.datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def _substantive(text: str) -> int:
    """Characters that carry content. Whitespace and punctuation are not it."""
    return sum(1 for ch in text if ch.isalnum())


@dataclass(frozen=True)
class Preflight:
    """What the fetch stage established. No warehouse write has happened yet."""

    url: str                       #: as pasted
    ingest_url: str                #: canonical form handed to the ingester
    kind: str                      #: "youtube" | "article" | "audio" | "unusable"
    canonical: str                 #: identity key, video id where there is one
    path: str | None               #: "captions" | "asr" | "text" | None
    path_reason: str = ""
    media_seconds: float | None = None
    title: str | None = None
    status: int | None = None
    existing_item_id: str | None = None
    sibling_item_ids: tuple[str, ...] = ()
    refusal: str | None = None     #: a failure-state code, when refused
    reason: str | None = None      #: the sentence the UI shows
    # -- everything the preview halt shows, all of it read out of the ONE
    # response the preflight was already fetching. Adding these cost the
    # source zero extra requests.
    creator: str | None = None
    channel: str | None = None
    creator_basis: str = ""
    creator_reason: str = ""
    tracked: bool | None = None
    published_at: dt.datetime | None = None
    published_basis: str = ""
    description: str = ""
    gameweek: dict[str, Any] | None = None

    @property
    def refused(self) -> bool:
        return self.refusal is not None

    @property
    def duplicate(self) -> bool:
        return self.existing_item_id is not None

    def eta_s(self) -> tuple[float | None, str | None]:
        """Seconds of transcription remaining, and the basis. Never invented.

        Both halves must be measured: the rate comes from the contract's
        measurements and the duration from the source itself. Missing either
        yields ``None`` and a sentence saying which one, because a number here
        that is not derived from both is a guess wearing a unit.
        """
        rate = {"captions": CAPTION_RATE, "asr": ASR_RATE}.get(self.path or "")
        if rate is None:
            return None, ("no transcription path is known yet, so there is no "
                          "measured rate to divide by")
        if not self.media_seconds:
            return None, (f"the {self.path} rate is known ({rate:g}x realtime) "
                          f"but this source did not state its duration, so the "
                          f"ETA would be a guess")
        return round(self.media_seconds / rate, 1), f"{self.path}@{rate:g}x realtime"

    def preview(self) -> dict[str, Any]:
        """What the owner reads BEFORE anything is transcribed.

        Enough to answer "is this relevant?" and nothing that required a
        second request to obtain: who published it, what they called it, when
        they published it, which gameweek that makes it (labelled stated or
        inferred), what they say it is about, and what saying yes would cost.
        """
        eta_s, basis = self.eta_s()
        return {
            "url": self.url,
            "kind": self.kind,
            "title": self.title,
            # who -- the resolved identity, never a placeholder and never a
            # guess. `tracked` says whether the panel scope would carry it.
            "creator": self.creator,
            "channel": self.channel,
            "creator_basis": self.creator_basis,
            "creator_reason": self.creator_reason,
            "tracked": self.tracked,
            # when, and therefore which gameweek
            "published_at": _iso(self.published_at),
            "published_basis": self.published_basis,
            "gameweek": self.gameweek,
            # what it says it is about
            "description": self.description,
            # what saying yes would cost
            "media_seconds": self.media_seconds,
            "transcript_path": self.path,
            "path_reason": self.path_reason,
            "eta_s": eta_s,
            "eta_basis": basis if eta_s is not None else None,
            "eta_reason": None if eta_s is not None else basis,
            "duplicate_of": list(self.sibling_item_ids),
            "existing_item_id": self.existing_item_id,
        }


def _reader_context(db: Path | str) -> tuple[Any, frozenset[str]]:
    """The calendar and the panel roster, through a read copy. No lock at all.

    Both are needed to build the preview -- the calendar to infer a gameweek,
    the roster to say whether a resolved creator is one the board carries --
    and both are reads. :func:`fpl_edge.platform.query.read_copy` takes a
    private file copy, so this cannot contend with the bot, the DAG or a
    concurrent ingest.
    """
    from fpl_edge.platform.query import read_copy

    db_path = Path(db)
    if not db_path.exists():
        return None, frozenset()
    try:
        with read_copy(db_path) as wh:
            calendar = None
            try:
                from fpl_edge.ingest.content.calendar import load_calendar

                calendar, _ = load_calendar(wh)
            except Exception:  # noqa: BLE001 - no dim_event yet is not an error
                calendar = None
            try:
                from fpl_edge.interfaces.creators import panel_show_names

                shows = panel_show_names(wh)
            except Exception:  # noqa: BLE001 - another team's table
                shows = frozenset()
            return calendar, shows
    except Exception:  # noqa: BLE001 - an unreadable warehouse yields no context
        return None, frozenset()


def _gw_preview(calendar, published_at: dt.datetime | None, title: str | None,
                published_basis: str) -> dict[str, Any] | None:
    """The gameweek as far as it can be known BEFORE a transcript exists.

    Title and publish date only: there is no analysis yet, by design, because
    producing one is the expensive thing the owner is deciding about. So a
    preview gameweek is more often ``inferred`` than the final one, and it says
    so -- phase two re-resolves with the analysed calls in hand and may sharpen
    ``inferred`` into ``stated``.
    """
    from fpl_edge.interfaces.creators import resolve_gameweek

    if published_at is None:
        return None
    return resolve_gameweek(calendar=calendar, published_at=published_at,
                            title=title or "",
                            published_basis=published_basis).public()


def _yt_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def _default_fetcher(kind: str):
    """The fetcher for one preflight probe.

    ``respect_robots`` mirrors :func:`fpl_edge.interfaces.creators.ingest_link`
    exactly -- off for the owner's own YouTube link (the sanctioned exception),
    on for everything else -- so the preflight never reaches somewhere the
    ingester itself would not go.
    """
    from fpl_edge.ingest.content.fetch import ContentFetcher

    return ContentFetcher("user_link", respect_robots=(kind != "youtube"))


def preflight(url: str, db: Path | str = DEFAULT_DB, *,
              fetcher_factory: Callable[[str], Any] = _default_fetcher) -> Preflight:
    """Classify the URL, probe the source, and look for what we already hold.

    Read-only from end to end: the warehouse is consulted through a read copy
    and nothing is written whatever the outcome.
    """
    from fpl_edge.ingest.content.urls import youtube_id

    url = (url or "").strip()
    for pattern, why in _NON_EPISODE:
        if pattern.search(url):
            return Preflight(url=url, ingest_url=url, kind="unusable",
                             canonical=f"url:{url}", path=None,
                             refusal="not_an_episode", reason=why)

    lowered = url.split("?")[0].lower()
    if lowered.endswith(_AUDIO_SUFFIXES):
        return Preflight(
            url=url, ingest_url=url, kind="audio", canonical=f"url:{url}",
            path="asr",
            path_reason="a media file needs local ASR (~11.5x realtime)",
            refusal="no_asr_route_for_pasted_media",
            reason=("This is a media file, not a page with published captions. "
                    "The pasted-link route transcribes captions only; audio is "
                    "transcribed by `uv run fpl pipeline transcribe`, which owns "
                    "the local ASR engine, its coverage check and its provenance "
                    "row. Nothing was stored."),
        )

    vid = youtube_id(url)
    if vid:
        return _preflight_youtube(url, vid, db, fetcher_factory)
    return _preflight_article(url, db, fetcher_factory)


def _preflight_youtube(url: str, vid: str, db: Path | str,
                       fetcher_factory: Callable[[str], Any]) -> Preflight:
    canonical = f"yt:{vid}"
    # Canonical watch form, not the pasted form: it is the shape
    # ``ingest_link`` recognises on every host and path variant, so a
    # ``/embed/`` or ``m.youtube.com`` paste cannot fall through to the article
    # branch and get stored as a page about a video.
    ingest_url = _yt_watch_url(vid)

    # Lazy: take.py imports this module, so naming it at module level
    # would close the loop. One lookup, on a path that already touches
    # the warehouse.
    from fpl_edge.platform.link_jobs.take import _existing_item

    existing, siblings = _existing_item(db, vid=vid, url=url)
    if existing is not None:
        return Preflight(
            url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
            path="captions",
            path_reason="already transcribed; nothing to fetch",
            existing_item_id=existing, sibling_item_ids=siblings,
            reason=(f"Already in the warehouse as {existing}"
                    + (f" (stored under {len(siblings)} urls; "
                       f"watch?v=, youtu.be/ and /live/ are one video)"
                       if len(siblings) > 1 else "")),
        )

    fetcher = fetcher_factory("youtube")
    try:
        # retries=0 deliberately: ContentFetcher retries 429 by default, and a
        # 429 is the source declining.
        watch = fetcher.get(ingest_url, retries=0)
    finally:
        fetcher.close()

    if watch.status in (403, 429):
        return Preflight(
            url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
            path=None, status=watch.status, refusal="source_refused",
            reason=(f"YouTube returned {watch.status} for this video. That is "
                    f"the source declining, and it is obeyed rather than "
                    f"retried. Nothing was fetched, transcribed or stored."),
        )
    if getattr(watch, "robots_blocked", False):
        return Preflight(
            url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
            path=None, refusal="robots_disallow",
            reason="robots.txt disallows this URL for our user agent.",
        )
    if not watch.ok:
        detail = watch.status if watch.status is not None else watch.error
        return Preflight(
            url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
            path=None, status=watch.status, refusal="fetch_failed",
            reason=f"the watch page returned {detail}; nothing was stored.",
        )

    body = watch.text
    title = None
    match = _TITLE_RE.search(body)
    if match:
        title = re.sub(r"\s*-\s*YouTube\s*$", "", match.group(1)).strip() or None
    length = _LENGTH_RE.search(body)
    media_seconds = float(length.group(1)) if length else None

    # -- identity, publication and gameweek, out of the SAME response --------
    # Not one extra request: this is the body that was fetched to decide
    # whether the video is ingestable at all. Reading four more fields out of
    # it is what makes the preview halt possible without paying the source
    # twice, which is the condition docs/data_sources.md 7A rests on.
    from fpl_edge.ingest.content.youtube import (
        channel_from_watch,
        creator_for_channel,
        description_from_watch,
        published_from_watch,
    )

    calendar, panel_shows = _reader_context(db)
    channel = channel_from_watch(body)
    match_ = creator_for_channel(channel, panel_shows=panel_shows)
    creator = match_.creator or channel.name
    published = published_from_watch(body)
    published_basis = ("the video's own datePublished" if published is not None
                       else "the watch page stated no publication date")
    description = description_from_watch(body)[:1200]
    gameweek = _gw_preview(calendar, published, title, published_basis)
    identity = {
        "creator": creator,
        "channel": channel.name,
        "creator_basis": match_.basis,
        "creator_reason": match_.reason,
        "tracked": match_.tracked if match_.resolved else False,
        "published_at": published,
        "published_basis": published_basis,
        "description": description,
        "gameweek": gameweek,
    }

    if length and _CAPTION_TRACKS not in body:
        # The player response WAS readable (it gave us a duration) and it lists
        # no caption track. There is no audio route for YouTube in this repo --
        # nothing here downloads YouTube media -- so this is the end of it, and
        # it ends before anything is written.
        return Preflight(
            url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
            path=None, title=title, media_seconds=media_seconds,
            status=watch.status, refusal="no_transcript_source", **identity,
            reason=("this video publishes no caption track, and there is no "
                    "downloadable audio for it here -- YouTube media is not "
                    "downloaded by this engine, so local ASR has nothing to "
                    "read. Nothing was stored."),
        )

    path_reason = ("published captions (~286x realtime)"
                   if _CAPTION_TRACKS in body else
                   "published captions (~286x realtime); the watch page did "
                   "not advertise a track, so the transcribe stage confirms it")
    return Preflight(
        url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
        path="captions", path_reason=path_reason, title=title,
        media_seconds=media_seconds, status=watch.status,
        sibling_item_ids=siblings, **identity,
    )


def _preflight_article(url: str, db: Path | str,
                       fetcher_factory: Callable[[str], Any]) -> Preflight:
    from fpl_edge.ingest.content.feeds import strip_html

    if not re.match(r"^https?://", url, re.I):
        return Preflight(url=url, ingest_url=url, kind="unusable",
                         canonical=f"url:{url}", path=None,
                         refusal="not_an_episode",
                         reason="not an http(s) URL.")

    # Lazy: take.py imports this module, so naming it at module level
    # would close the loop. One lookup, on a path that already touches
    # the warehouse.
    from fpl_edge.platform.link_jobs.take import _existing_item

    existing, siblings = _existing_item(db, vid=None, url=url)
    if existing is not None:
        return Preflight(url=url, ingest_url=url, kind="article",
                         canonical=f"url:{url}", path="text",
                         path_reason="already stored; nothing to fetch",
                         existing_item_id=existing, sibling_item_ids=siblings,
                         reason=f"Already in the warehouse as {existing}")

    fetcher = fetcher_factory("article")
    try:
        resp = fetcher.get(url, retries=0)
    finally:
        fetcher.close()

    if resp.status in (403, 429):
        return Preflight(url=url, ingest_url=url, kind="article",
                         canonical=f"url:{url}", path=None, status=resp.status,
                         refusal="source_refused",
                         reason=(f"{url} returned {resp.status}. The source is "
                                 f"declining; that is obeyed, not retried. "
                                 f"Nothing was stored."))
    if getattr(resp, "robots_blocked", False):
        return Preflight(url=url, ingest_url=url, kind="article",
                         canonical=f"url:{url}", path=None,
                         refusal="robots_disallow",
                         reason="robots.txt disallows this URL for our user agent.")
    if not resp.ok:
        detail = resp.status if resp.status is not None else resp.error
        return Preflight(url=url, ingest_url=url, kind="article",
                         canonical=f"url:{url}", path=None, status=resp.status,
                         refusal="fetch_failed",
                         reason=f"{url} returned {detail}; nothing was stored.")

    text = strip_html(resp.text)
    chars = _substantive(text)
    if chars < MIN_SUBSTANTIVE_CHARS:
        return Preflight(
            url=url, ingest_url=url, kind="article", canonical=f"url:{url}",
            path=None, status=resp.status, refusal="not_an_episode",
            reason=(f"this page carries {chars} substantive characters, below "
                    f"the {MIN_SUBSTANTIVE_CHARS} an article needs to be worth "
                    f"a take. An FPL league invite was once stored as an "
                    f"article titled 'a6fgym' with three; nothing was stored "
                    f"this time."),
        )

    title = None
    match = _TITLE_RE.search(resp.text)
    if match:
        title = match.group(1).strip() or None

    # The article half of the preview. A registered feed states its show, so a
    # page on a registered source's host is that show's -- matched on the exact
    # host, never on resemblance -- and an unregistered host stays unresolved
    # with a reason rather than acquiring a name.
    from fpl_edge.interfaces.creators import _creator_from_host

    calendar, panel_shows = _reader_context(db)
    host_creator, host_basis, host_reason = _creator_from_host(url)
    published = _now()
    published_basis = ("an article carries no publication instant this route "
                       "can trust, so the paste time is used")
    gameweek = _gw_preview(calendar, published, title, published_basis)
    return Preflight(url=url, ingest_url=url, kind="article",
                     canonical=f"url:{url}", path="text",
                     path_reason=(f"article text, {chars} substantive "
                                  f"characters; no transcription needed"),
                     title=title, status=resp.status,
                     sibling_item_ids=siblings,
                     creator=host_creator, channel=None,
                     creator_basis=host_basis, creator_reason=host_reason,
                     tracked=(bool(host_creator)
                              and (not panel_shows or host_creator in panel_shows)),
                     published_at=published, published_basis=published_basis,
                     description=text[:1200], gameweek=gameweek)
