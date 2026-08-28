"""Paste-a-link ingestion, as a server-side job the UI polls.

    POST /api/ingest/link       {url}     -> {job_id, stages:[...]}
    GET  /api/ingest/link/{id}            -> {stage, pct, eta_s, done, error, item_id}

The owner's ask was "paste a URL, it transcribes, analyses, and shows the take
inline with quotes and timestamps". This module is the job wrapper around that
sentence and nothing more: the ingestion itself is
:func:`fpl_edge.interfaces.creators.ingest_link`, which is the sanctioned
single-URL path -- it is why the robots gate has an owner-initiated exception
(``docs/data_sources.md`` §7A), and a second ingester would either duplicate
that exception or quietly widen it. Everything here is preflight, progress,
refusal and read-back.

Four things this module is careful about, each because the warehouse already
contains the scar:

**It refuses before it stores.** An FPL league invite was once ingested as an
"article" titled ``a6fgym`` carrying three substantive characters. So the
preflight names non-episode URL shapes explicitly, and any other page whose
extracted text is below :data:`MIN_SUBSTANTIVE_CHARS` is refused with the
measured count rather than stored as a take about nothing.

**It canonicalises on the video id.** ``watch?v=``, ``youtu.be/``, ``/live/``
and ``/embed/`` are one video. One Andy (LTFPL) video is stored under two urls
-- one row carrying the analysis, the other its 1,199 transcript segments --
which is what a URL-keyed identity buys you. The preflight uses
:func:`fpl_edge.platform.scripts.creators.youtube_id`, the existing authority,
returns the EXISTING item instead of ingesting again, and reads segments across
the whole sibling set so the take still has timestamps when the analysis and
the transcript live on different rows.

**A refusal is obeyed, not retried.** 403 and 429 mean the source is declining.
Every fetch here passes ``retries=0`` (``ContentFetcher.get`` retries 429 by
default) and a refusal ends the job with the real status recorded.

**Nothing partial is ever presented as whole.** A transcription that dies
mid-way stores nothing -- ``ingest_link``'s first write happens after the
transcript is complete, and :class:`~fpl_edge.ingest.content.asr.PartialTranscript`
is surfaced by name with the same guarantee.

ETA
---
Derived from two measured rates, never guessed: captions run at ~286x realtime
and local ASR at ~11.5x. The preflight reads ``lengthSeconds`` off the watch
page, so a 20-minute video reports ~4s before the wait starts. When the media
duration or the path is not known, ``eta_s`` is ``None`` and ``eta_basis`` says
why. There is no fallback constant.

THE WRITE LOCK
--------------
DuckDB is single-writer XOR many-readers and the platform server holds readers
while this runs. Preflight and read-back go through
:func:`fpl_edge.platform.query.read_copy` (a private file copy, no lock at
all). The one write is ``ingest_link``, handed a
:class:`~fpl_edge.store.warehouse.LeasedWarehouse` that connects lazily -- and
``ingest_link`` touches its warehouse argument only after the fetch and the
transcript, so the lock is taken for persistence and not for the network. See
:data:`WRITE_ATTEMPTS` for the contention backoff.

WHERE THIS DEPARTS FROM THE CONTRACT, AND WHY
---------------------------------------------
The four stage names are shipped as agreed. Three things behind them are not
what the contract assumed, and pretending otherwise would be the same class of
lie as an invented ETA:

1. **``transcribe`` and ``analyse`` are one call, not two.** ``ingest_link``
   transcribes and analyses without a boundary anything here can observe, so
   ``analyse`` is entered on the way out of that call rather than when the
   analysis actually starts. The two stages are honest as an ordering and are
   not a measurement of where the time went.
2. **``transcript_provenance`` is not this route's progress source.** That
   table is written by :func:`fpl_edge.ingest.content.asr.store_transcription`,
   which the owner-shared-link path does not call -- it writes
   ``transcript_segment`` directly. So there is no ``covered_seconds`` /
   ``audio_seconds`` row to divide, and the transcribe fraction is
   elapsed-over-ETA instead: still derived from a measured rate and a measured
   duration, but a projection rather than an observation.
3. **A pasted media file is refused, not transcribed.** ASR at 11.5x realtime
   is real and :mod:`fpl_edge.ingest.content.asr` implements it, but nothing in
   this engine downloads YouTube media and ``ingest_link`` has no audio branch
   at all. Wiring one in here would be the second ingester this module exists
   not to be. Podcast audio goes through ``fpl pipeline transcribe``, which
   already owns the coverage check and the provenance row; the ASR rate is
   still carried in :data:`ASR_RATE` so the ETA arithmetic is ready the day
   ``ingest_link`` grows that branch.

One cost is accepted knowingly: the preflight fetches the YouTube watch page
and ``ingest_link`` fetches it again, so an accepted paste costs the source two
requests instead of one. That is the price of refusing a league invite before a
row is written rather than after.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fpl_edge.store.warehouse import DEFAULT_DB

UTC = dt.timezone.utc

#: The stage names the UI is built against. Order is the contract.
STAGES: tuple[str, ...] = ("fetch", "transcribe", "analyse", "attribute")

#: Measured throughput, ``docs/platform/creators_build_contract.md`` §3. These
#: are the ONLY basis for an ETA in this module; there is no default guess.
CAPTION_RATE = 286.0     # published caption cues, x realtime
ASR_RATE = 11.5          # local mlx-whisper, x realtime

#: pct is a stage ladder, not a spinner. Within ``transcribe`` the fraction is
#: elapsed/eta -- real because the eta came from a measured rate and a measured
#: duration -- and it is capped below the ceiling so it cannot claim completion.
_STAGE_SPAN: dict[str, tuple[int, int]] = {
    "fetch": (0, 10),
    "transcribe": (10, 70),
    "analyse": (70, 90),
    "attribute": (90, 100),
}

#: An article shorter than this is not an article. The stored ``a6fgym`` row
#: had three. Set well above any plausible real page so the refusal is never a
#: judgement call.
MIN_SUBSTANTIVE_CHARS = 400

#: Attempts at the one write, backing off on lock contention.
WRITE_ATTEMPTS = 3

#: How long a finished job stays pollable in memory. It is also on disk, so
#: this only bounds the process's own footprint.
JOB_TTL = dt.timedelta(hours=6)

REPO_ROOT = Path(__file__).resolve().parents[2]
LINK_JOBS_DIR = REPO_ROOT / "data" / "warehouse" / "jobs" / "link"

#: URL shapes that are not a published episode or article. Each is a real
#: family of paste, not a hypothetical: the league invite is the one that got
#: through, the channel/playlist pages are the obvious neighbours.
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

#: Media files. The pasted-link route transcribes published captions; audio
#: goes through the pipeline's ASR command, which owns the coverage check and
#: the provenance row.
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


# ---------------------------------------------------------------------------
# Preflight: everything that can be known before a single row is written.


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
    from fpl_edge.platform.scripts.creators import youtube_id

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

    if length and _CAPTION_TRACKS not in body:
        # The player response WAS readable (it gave us a duration) and it lists
        # no caption track. There is no audio route for YouTube in this repo --
        # nothing here downloads YouTube media -- so this is the end of it, and
        # it ends before anything is written.
        return Preflight(
            url=url, ingest_url=ingest_url, kind="youtube", canonical=canonical,
            path=None, title=title, media_seconds=media_seconds,
            status=watch.status, refusal="no_transcript_source",
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
        sibling_item_ids=siblings,
    )


def _preflight_article(url: str, db: Path | str,
                       fetcher_factory: Callable[[str], Any]) -> Preflight:
    from fpl_edge.ingest.content.feeds import strip_html

    if not re.match(r"^https?://", url, re.I):
        return Preflight(url=url, ingest_url=url, kind="unusable",
                         canonical=f"url:{url}", path=None,
                         refusal="not_an_episode",
                         reason="not an http(s) URL.")

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
    return Preflight(url=url, ingest_url=url, kind="article",
                     canonical=f"url:{url}", path="text",
                     path_reason=(f"article text, {chars} substantive "
                                  f"characters; no transcription needed"),
                     title=title, status=resp.status,
                     sibling_item_ids=siblings)


# ---------------------------------------------------------------------------
# What the warehouse already holds. Read copy only: no lock is taken.


def _table_exists(wh, name: str) -> bool:
    return bool(wh.sql(
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).iloc[0]["n"])


def _existing_item(db: Path | str, *, vid: str | None,
                   url: str) -> tuple[str | None, tuple[str, ...]]:
    """The item(s) already holding this publication, richest row first.

    One Andy (LTFPL) video sits in the warehouse under two urls: one row
    carries the analysis, the other carries 1,199 transcript segments. So the
    representative row is the one with an analysis, then the one with
    segments -- and every sibling id is returned too, because the take is
    assembled across all of them or it loses its timestamps.
    """
    from fpl_edge.platform.query import read_copy
    from fpl_edge.platform.scripts.creators import youtube_id

    db_path = Path(db)
    if not db_path.exists():
        return None, ()
    try:
        with read_copy(db_path) as wh:
            if not _table_exists(wh, "content_item"):
                return None, ()
            if vid:
                rows = wh.sql(
                    "SELECT item_id, url FROM content_item "
                    "WHERE url IS NOT NULL AND url LIKE ?", ["%" + vid + "%"],
                )
                candidates = [
                    str(r["item_id"]) for r in rows.to_dict("records")
                    if youtube_id(str(r["url"])) == vid
                ]
            else:
                rows = wh.sql(
                    "SELECT item_id FROM content_item WHERE url = ?", [url],
                )
                candidates = [str(r["item_id"]) for r in rows.to_dict("records")]
            if not candidates:
                return None, ()
            ranked = _rank_items(wh, candidates)
    except Exception:  # noqa: BLE001 - an unreadable warehouse is not a duplicate
        return None, ()
    return ranked[0], tuple(ranked)


def _rank_items(wh, item_ids: list[str]) -> list[str]:
    """Analysis beats transcript beats bare row; ties broken by segment count."""
    placeholders = ", ".join("?" for _ in item_ids)
    analysed: set[str] = set()
    if _table_exists(wh, "content_analysis"):
        analysed = {
            str(r["item_id"]) for r in wh.sql(
                f"SELECT DISTINCT item_id FROM content_analysis "
                f"WHERE item_id IN ({placeholders})", list(item_ids)
            ).to_dict("records")
        }
    segments: dict[str, int] = {}
    if _table_exists(wh, "transcript_segment"):
        segments = {
            str(r["item_id"]): int(r["n"]) for r in wh.sql(
                f"SELECT item_id, count(*) AS n FROM transcript_segment "
                f"WHERE item_id IN ({placeholders}) GROUP BY item_id",
                list(item_ids),
            ).to_dict("records")
        }
    return sorted(
        item_ids,
        key=lambda i: (0 if i in analysed else 1,
                       0 if segments.get(i) else 1,
                       -segments.get(i, 0), i),
    )


def _item_for_url(db: Path | str, *, vid: str | None,
                  url: str) -> tuple[str | None, tuple[str, ...]]:
    """Read back what the ingester just wrote. It returns no item id itself."""
    return _existing_item(db, vid=vid, url=url)


# ---------------------------------------------------------------------------
# The take: quotes with timestamps, assembled from what is stored.


def build_take(db: Path | str, item_ids: tuple[str, ...] | list[str],
               url: str | None) -> dict[str, Any]:
    """The inline take for one publication, with deep links.

    Built with the SAME helpers the creator board uses
    (``TranscriptIndex``/``_take``/``_resolver`` in
    :mod:`fpl_edge.platform.scripts.creators`) rather than a second shaping of
    the same data -- two surfaces that disagree about what a creator said is
    the failure this whole page exists to avoid.

    Transcript segments are pooled across every sibling row, because the pair
    of Andy rows in the warehouse keep the analysis and the segments apart and
    a per-row index would silently lose every timestamp.
    """
    from fpl_edge.platform.query import read_copy
    from fpl_edge.platform.scripts.common import SEASON_DEFAULT
    from fpl_edge.platform.scripts.creators import (
        TranscriptIndex,
        _analyses,
        _resolver,
        _take,
    )

    ids = [str(i) for i in item_ids if i]
    if not ids:
        return {"take": None, "reason": "no stored item to read a take from"}

    db_path = Path(db)
    if not db_path.exists():
        return {"take": None, "reason": f"no warehouse at {db_path}"}

    with read_copy(db_path) as wh:
        if not _table_exists(wh, "content_item"):
            return {"take": None,
                    "reason": "this warehouse has no content tables yet"}
        placeholders = ", ".join("?" for _ in ids)
        items = wh.sql(
            f"SELECT item_id, title, url, creator, text_source "
            f"FROM content_item WHERE item_id IN ({placeholders})", list(ids),
        ).to_dict("records")
        rows: list[tuple[float | None, str]] = []
        if _table_exists(wh, "transcript_segment"):
            rows = [
                (None if r["start_s"] is None else float(r["start_s"]),
                 str(r["text"]))
                for r in wh.sql(
                    f"SELECT start_s, text FROM transcript_segment "
                    f"WHERE item_id IN ({placeholders}) ORDER BY item_id, seq",
                    list(ids),
                ).to_dict("records")
            ]
        index = TranscriptIndex(rows)
        analyses = _analyses(wh, set(ids)) if _table_exists(wh, "content_analysis") else {}
        person, person_reason = _attribution(wh, ids)
        resolver = _resolver(wh, SEASON_DEFAULT, _now())

    head = items[0] if items else {}
    link = url or (str(head.get("url")) if head.get("url") else None)
    payload: dict[str, Any] = {
        "item_id": ids[0],
        "item_ids": ids,
        "title": str(head.get("title")) if head.get("title") else None,
        "url": link,
        "text_source": str(head.get("text_source")) if head.get("text_source") else None,
        "n_segments": len(rows),
        "person": person,
        "person_reason": person_reason,
    }
    for item_id in ids:                       # analysis-carrying row wins
        if item_id in analyses:
            analysis, model = analyses[item_id]
            payload["take"] = _take(analysis, model, resolver, index, link)
            payload["reason"] = None
            return payload
    payload["take"] = None
    payload["reason"] = (
        "the transcript is stored but no analysis has been run on it yet"
        if rows else
        "no analysis and no transcript are stored for this item"
    )
    return payload


def _attribution(wh, item_ids: list[str]) -> tuple[str | None, str]:
    """Who said it, if the panel has established that. Never guessed.

    A pasted link is stored under the ``user-shared`` creator, which is not a
    panel show, so there is normally no ``item_person`` row -- and that is the
    honest answer, not a gap to fill with a default. This does NOT run
    :func:`fpl_edge.ingest.content.panel.attribute_items`: that walks the whole
    corpus and writes ``item_person`` for all of it, which one paste has no
    business triggering.
    """
    if not _table_exists(wh, "item_person"):
        return None, ("the panel's person layer has not been built in this "
                      "warehouse, so no attribution is available")
    placeholders = ", ".join("?" for _ in item_ids)
    rows = wh.sql(
        f"SELECT person_key, basis FROM item_person "
        f"WHERE item_id IN ({placeholders})", list(item_ids),
    ).to_dict("records")
    if not rows:
        return None, ("a pasted link is stored under 'user-shared', which is "
                      "not a panel show; no person owns it and none is guessed")
    return str(rows[0]["person_key"]), f"basis: {rows[0].get('basis')}"


# ---------------------------------------------------------------------------
# The one write.


def _ingest_once(db: Path | str, url: str):
    """``ingest_link`` under a lease that connects lazily.

    ``ingest_link`` does its fetching and transcription before it touches the
    warehouse argument, and :class:`LeasedWarehouse` connects on first
    attribute access -- so the write lock is taken for the persistence phase
    and not held across the network work that precedes it.
    """
    from fpl_edge.interfaces.creators import ingest_link
    from fpl_edge.store.warehouse import LeasedWarehouse

    lease = LeasedWarehouse(db, lock_timeout_s=120.0)
    try:
        return ingest_link(lease, url)
    finally:
        lease.release()


def ingest_with_retry(db: Path | str, url: str, *,
                      attempts: int = WRITE_ATTEMPTS,
                      ingest: Callable[[Path | str, str], Any] | None = None):
    """Retry on lock contention with backoff; re-raise anything else.

    Losing a race for the single writer lock is expected on a machine where
    the bot, the DAG and this server all write -- so it backs off rather than
    surfacing "database is locked" as the user's answer.
    """
    run = ingest or _ingest_once
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return run(db, url)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is the lock
            message = str(exc).lower()
            if "lock" not in message and "conflict" not in message:
                raise
            last = exc
            if attempt < attempts - 1:
                time.sleep(min(8.0, 2.0 ** attempt) * (0.5 + random.random()))
    raise RuntimeError(
        f"the warehouse write lock was held by another process through "
        f"{attempts} attempts; nothing was stored. Last error: {last}"
    )


# ---------------------------------------------------------------------------
# Jobs.


@dataclass
class _Job:
    job_id: str
    url: str
    stage: str = "fetch"
    done: bool = False
    error: str | None = None
    error_code: str | None = None
    item_id: str | None = None
    eta_s: float | None = None
    eta_basis: str | None = None
    eta_reason: str | None = None
    path: str | None = None
    path_reason: str = ""
    media_seconds: float | None = None
    title: str | None = None
    status: int | None = None
    duplicate_of: tuple[str, ...] = ()
    result: dict[str, Any] | None = None
    started_utc: dt.datetime = field(default_factory=_now)
    stage_started_utc: dt.datetime = field(default_factory=_now)
    finished_utc: dt.datetime | None = None
    note: str | None = None

    def pct(self) -> int:
        """Position on the stage ladder, refined inside ``transcribe``."""
        floor, ceiling = _STAGE_SPAN[self.stage]
        if self.done:
            return 100 if self.error is None else floor
        if self.stage == "transcribe" and self.eta_s:
            elapsed = (_now() - self.stage_started_utc).total_seconds()
            fraction = min(elapsed / self.eta_s, 0.95)
            return int(floor + fraction * (ceiling - floor))
        return floor

    def public(self) -> dict[str, Any]:
        """The polled shape. The first six keys are the agreed contract."""
        return {
            "stage": self.stage,
            "pct": self.pct(),
            "eta_s": self.eta_s,
            "done": self.done,
            "error": self.error,
            "item_id": self.item_id,
            # Additive, and all of it load-bearing for the UI's copy:
            "job_id": self.job_id,
            "stages": list(STAGES),
            "url": self.url,
            "title": self.title,
            "error_code": self.error_code,
            "eta_basis": self.eta_basis,
            "eta_reason": self.eta_reason,
            "transcript_path": self.path,
            "path_reason": self.path_reason,
            "media_seconds": self.media_seconds,
            "source_status": self.status,
            "duplicate_of": list(self.duplicate_of),
            "note": self.note,
            "result": self.result,
            "started_utc": _iso(self.started_utc),
            "finished_utc": _iso(self.finished_utc),
        }


class LinkJobs:
    """Server-side job state for pasted links, keyed by ``job_id``.

    State lives here and on disk, never in a request: the user pastes, walks
    away, comes back and polls. ``background=False`` runs the job inline, which
    is what the tests and any CLI caller want.
    """

    def __init__(self, db: Path | str = DEFAULT_DB, *,
                 store_dir: Path | str | None = None,
                 background: bool = True,
                 preflight_fn: Callable[..., Preflight] | None = None,
                 ingest_fn: Callable[[Path | str, str], Any] | None = None) -> None:
        self.db = Path(db)
        self.store_dir = Path(store_dir) if store_dir else LINK_JOBS_DIR
        self.background = background
        self._preflight = preflight_fn or preflight
        self._ingest = ingest_fn
        self._jobs: dict[str, _Job] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # -- api ----------------------------------------------------------------

    def submit(self, url: str) -> dict[str, Any]:
        """Accept a URL and start the job. The contract's POST response."""
        url = (url or "").strip()
        if not url:
            raise LinkRefused("url is required")
        if not re.match(r"^https?://", url, re.I):
            raise LinkRefused(
                f"{url!r} is not an http(s) URL. Paste the link to one episode "
                f"or article."
            )
        job = _Job(job_id=uuid.uuid4().hex[:16], url=url)
        with self._lock:
            self._jobs[job.job_id] = job
        self._persist(job)

        if self.background:
            thread = threading.Thread(target=self._run, args=(job.job_id,),
                                      name=f"link-ingest-{job.job_id}",
                                      daemon=True)
            self._threads[job.job_id] = thread
            thread.start()
        else:
            self._run(job.job_id)

        return {"job_id": job.job_id, "stages": list(STAGES),
                "url": url, "accepted_utc": _iso(job.started_utc)}

    def poll(self, job_id: str) -> dict[str, Any] | None:
        """The contract's GET response, or None when the id is unknown.

        A finished job keeps answering: the terminal state is held in memory
        and mirrored to disk, so a reload after completion reads the same
        payload rather than a 404.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            self._sweep()
            return job.public()
        return self._load(job_id)

    def wait_for(self, job_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """Block until the job's thread finishes. For tests and CLI callers."""
        thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)
        return self.poll(job_id)

    # -- the worker ---------------------------------------------------------

    def _run(self, job_id: str) -> None:
        job = self._jobs[job_id]
        try:
            self._run_stages(job)
        except Exception as exc:  # noqa: BLE001 - a job must never die silently
            self._fail(job, f"{type(exc).__name__}: {exc}", code="unhandled")

    def _run_stages(self, job: _Job) -> None:
        from fpl_edge.ingest.content import asr

        # -- fetch ----------------------------------------------------------
        pre = self._preflight(job.url, self.db)
        job.title = pre.title
        job.status = pre.status
        job.path = pre.path
        job.path_reason = pre.path_reason
        job.media_seconds = pre.media_seconds
        job.eta_s, basis = pre.eta_s()
        job.eta_basis = basis if job.eta_s is not None else None
        job.eta_reason = None if job.eta_s is not None else basis
        self._persist(job)

        if pre.refused:
            self._fail(job, pre.reason or "refused", code=pre.refusal)
            return

        if pre.duplicate:
            # The same video under a second URL form. Return the EXISTING item;
            # ingesting again would put a second row (and a second set of
            # claims) behind one publication.
            job.duplicate_of = pre.sibling_item_ids or (pre.existing_item_id,)
            job.item_id = pre.existing_item_id
            job.note = pre.reason
            self._finish(job, pre)
            return

        # -- transcribe + analyse -------------------------------------------
        # ``ingest_link`` fuses these two: it transcribes and analyses in one
        # call and there is no callback between them. The stage is advanced to
        # `analyse` on the way out rather than pretending to observe a boundary
        # that does not exist. See the deviation note in the module docstring.
        self._advance(job, "transcribe")
        try:
            findings = ingest_with_retry(self.db, pre.ingest_url,
                                         ingest=self._ingest)
        except asr.PartialTranscript as exc:
            self._fail(job, f"the transcription stopped short of the audio it "
                            f"was given, so nothing was stored: {exc}",
                       code="partial_transcript")
            return
        except asr.AudioUnavailable as exc:
            self._fail(job, f"the audio could not be read, so nothing was "
                            f"stored: {exc}", code="no_transcript_source")
            return
        except asr.AsrUnavailable as exc:
            self._fail(job, f"the local speech engine is not installed, and no "
                            f"remote one is used: {exc}", code="asr_unavailable")
            return
        except Exception as exc:  # noqa: BLE001 - reported, never half-stored
            self._fail(job, f"{type(exc).__name__}: {exc}", code="ingest_failed")
            return

        source = str(getattr(findings, "text_source", "") or "")
        if source.startswith("unavailable"):
            route = source[source.find("(") + 1:source.rfind(")")] or "unknown"
            if any(code in route for code in ("403", "429")):
                self._fail(
                    job,
                    f"the source declined ({route}). That is obeyed rather "
                    f"than retried; nothing was stored.",
                    code="source_refused")
            else:
                self._fail(
                    job,
                    f"no transcript could be obtained ({route}) and there is "
                    f"no downloadable audio for it here, so local ASR has "
                    f"nothing to read. Nothing was stored.",
                    code="no_transcript_source")
            return

        self._advance(job, "analyse")
        job.title = getattr(findings, "title", None) or job.title
        note = str(getattr(findings, "analysis_note", "") or "")
        job.note = note or None

        # -- attribute -------------------------------------------------------
        from fpl_edge.platform.scripts.creators import youtube_id

        vid = youtube_id(pre.ingest_url)
        item_id, siblings = _item_for_url(self.db, vid=vid, url=pre.ingest_url)
        if item_id is None:
            self._fail(job, "the ingest reported success but no item row can "
                            "be read back for this url; nothing is being shown "
                            "as stored.", code="item_not_readable")
            return
        job.item_id = item_id
        job.duplicate_of = siblings if len(siblings) > 1 else ()
        self._finish(job, pre, siblings=siblings)

    def _finish(self, job: _Job, pre: Preflight,
                siblings: tuple[str, ...] = ()) -> None:
        self._advance(job, "attribute")
        ids = siblings or job.duplicate_of or ((job.item_id,) if job.item_id else ())
        try:
            job.result = build_take(self.db, ids, pre.url)
        except Exception as exc:  # noqa: BLE001 - the item is stored either way
            job.result = {"take": None,
                          "reason": f"the take could not be read back: "
                                    f"{type(exc).__name__}: {exc}"}
        job.done = True
        job.error = None
        job.eta_s = None
        job.eta_basis = None
        job.eta_reason = "finished"
        job.finished_utc = _now()
        self._persist(job)

    def _advance(self, job: _Job, stage: str) -> None:
        job.stage = stage
        job.stage_started_utc = _now()
        self._persist(job)

    def _fail(self, job: _Job, reason: str, *, code: str | None) -> None:
        job.done = True
        job.error = reason
        job.error_code = code
        job.item_id = None
        job.eta_s = None
        job.eta_basis = None
        job.eta_reason = "the job ended before the work it would have timed"
        job.finished_utc = _now()
        self._persist(job)

    # -- persistence --------------------------------------------------------

    def _path(self, job_id: str) -> Path:
        return self.store_dir / f"{job_id}.json"

    def _persist(self, job: _Job) -> None:
        """Atomic snapshot, so a poll never reads a half-written file."""
        try:
            self.store_dir.mkdir(parents=True, exist_ok=True)
            path = self._path(job.job_id)
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(job.public(), indent=2, default=str))
            tmp.replace(path)
        except OSError:
            pass  # the in-memory state is authoritative while this process lives

    def _load(self, job_id: str) -> dict[str, Any] | None:
        if not re.fullmatch(r"[0-9a-f]{4,64}", job_id or ""):
            return None
        try:
            return json.loads(self._path(job_id).read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def _sweep(self) -> None:
        """Drop finished jobs older than the TTL. Disk keeps the record."""
        cutoff = _now() - JOB_TTL
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.done and job.finished_utc and job.finished_utc < cutoff:
                    self._jobs.pop(job_id, None)
                    self._threads.pop(job_id, None)
