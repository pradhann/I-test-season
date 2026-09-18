"""The two routes the Mac ASR worker talks to. DEPLOYMENT.md §1.4 to §1.7.

Fork 1 of the deployment spec splits transcription across two hosts.
``mlx-whisper`` needs an Apple Metal GPU, ``pyproject.toml`` guards it with
``sys_platform == 'darwin'``, and a Railway container is Linux, so the audio
half of the corpus cannot be produced there. It is also the one part of the
content pipeline that spends no metered credits and no model tokens, which is
why it stays on the owner's Mac rather than moving to a CPU engine or a hosted
API. The queue and the warehouse stay on the server, because the server is the
single writer and the authority.

``GET /api/transcripts/queue``
    What to transcribe, from the same ``select_queue`` the local command uses,
    with the same relevance gate. Below-threshold items are recorded in
    ``content_transcribe_skip`` exactly as the command records them, so an item
    turned away is turned away once rather than re-scored on every poll.

``POST /api/transcripts``
    One finished transcription, written through ``asr.store_transcription``,
    the same function the local path calls, so the ``transcript_segment`` and
    ``transcript_provenance`` rows a pushed transcript leaves are identical to
    a locally produced one. The handler builds the dataclasses and writes; it
    contains no second copy of the write.

Idempotency is ``(item_id, sha256(joined segment text))``. ``content_item``
already stores that hash in ``text_sha256`` once a transcript is promoted, so
a retry after a dropped connection, a duplicate worker run, or a worker that
lost its local bookkeeping all compare equal and change nothing.
``audio_sha256`` is deliberately not the key: it is empty by construction on
the captions path, which would make every caption push look identical to every
other.

Auth is a shared bearer secret compared with ``hmac.compare_digest``, with a
second accepted value in ``TRANSCRIPT_PUSH_TOKEN_NEXT`` so a rotation never
needs the two sides to restart together. This is a machine-to-machine call
between two systems the owner controls and it writes to one table family. It
is not the auth layer, and it does not pretend to be.

This module makes it two routes in this app that write to the corpus. The
other is ``POST /api/ingest/link``, and the package docstring says so.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import logging
import os
import time
from collections import deque
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from fpl_edge.platform.app.helpers import UTC, Deps

log = logging.getLogger(__name__)

#: The primary bearer secret, and the second value accepted during a rotation.
TOKEN_ENV = "TRANSCRIPT_PUSH_TOKEN"
TOKEN_NEXT_ENV = "TRANSCRIPT_PUSH_TOKEN_NEXT"

#: Body ceiling. A 2.5-hour episode is roughly 1 MB of segment JSON, so this
#: is a bound on absurdity rather than on real work.
MAX_BODY_BYTES = 16 * 1024 * 1024

#: Pushes per minute per token. A nightly run pushes a few dozen.
RATE_LIMIT_PER_MIN = 60

#: How many items one queue poll may return, and the ceiling on ``limit``.
QUEUE_DEFAULT_LIMIT = 20
QUEUE_MAX_LIMIT = 200

#: The kinds the queue serves by default. Both, because the worker decides
#: what it can do with each one and the server should not guess.
QUEUE_DEFAULT_KINDS = "podcast,youtube"

#: How far back the queue looks, in days. 0 means every stored item.
QUEUE_DEFAULT_SINCE_DAYS = 21

#: The one body an unauthenticated caller ever sees. It says nothing about
#: whether an item_id exists, which would make this route an existence oracle.
_UNAUTHORIZED = {"error": "unauthorized"}


class SegmentIn(BaseModel):
    """One timestamped chunk, as ``asr.Segment`` holds it."""

    seq: int
    start_s: float
    #: Not a column of ``transcript_segment``, which is
    #: ``(item_id, seq, start_s, text)``, but ``Segment`` requires it and the
    #: coverage arithmetic reads it.
    end_s: float
    text: str


class TranscriptIn(BaseModel):
    """Exactly what ``asr.store_transcription`` needs to write a transcript.

    Every field maps to one on ``asr.Transcription``, ``asr.Segment``, or a
    column of ``transcript_provenance``. Nothing here is invented, and nothing
    the server could compute for itself is accepted from the worker.
    """

    item_id: str = Field(min_length=1, max_length=64)
    derivation: str = Field(pattern="^(asr|captions)$")
    engine: str
    model: str
    language: str | None = None
    audio_url: str = ""
    #: Empty on the captions path by construction, which is why the
    #: idempotency key is built on the text hash instead.
    audio_sha256: str = ""
    audio_bytes: int = 0
    #: None means the audio was never downloaded, which is the only honest
    #: answer on the captions path. It is never filled from the last cue.
    audio_seconds: float | None = None
    covered_seconds: float = 0.0
    wall_seconds: float = 0.0
    created_utc: dt.datetime | None = None
    segments: list[SegmentIn] = Field(default_factory=list)
    #: Overwriting a stored transcript that differs is an explicit act: the
    #: stored one cost minutes of GPU and the push may be a regression.
    replace: bool = False


def _configured_tokens() -> list[str]:
    """The accepted bearer values, primary first. Values are never logged."""
    from fpl_edge import config

    out = []
    for name in (TOKEN_ENV, TOKEN_NEXT_ENV):
        try:
            value = config.secret(name, required=False)
        except Exception:  # noqa: BLE001 - an unreadable .env is not a crash
            value = os.environ.get(name) or None
        if value:
            out.append(value)
    return out


def _presented_token(request: Request) -> str | None:
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


class _RateLimiter:
    """Pushes per minute, per token prefix, in this process.

    In-process is the right scope: there is one service, one replica and one
    writer by construction, so a shared store would be a second moving part
    guarding a limit that only one process can exceed.
    """

    def __init__(self, per_minute: int = RATE_LIMIT_PER_MIN) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] > 60.0:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True


def _transcripts_router(deps: Deps) -> APIRouter:
    """The queue the Mac reads and the endpoint it pushes back to."""

    db_path = deps.db_path
    router = APIRouter()
    limiter = _RateLimiter()

    def _authorized(request: Request) -> bool:
        """True when the presented bearer matches an accepted value.

        A server with no token configured accepts nothing, and says so in the
        log rather than in the response: the response body for a refused call
        is the same either way, so a caller learns nothing from it.
        """
        tokens = _configured_tokens()
        presented = _presented_token(request)
        client = request.client.host if request.client else "unknown"
        if not tokens:
            log.error("%s is not set on this service, so the transcript routes "
                      "refuse every request", TOKEN_ENV)
            return False
        if presented is None:
            log.warning("transcript push from %s with no bearer token", client)
            return False
        for candidate in tokens:
            if hmac.compare_digest(presented, candidate):
                return True
        # First four characters only. The whole token never reaches a log.
        log.warning("transcript push from %s with an unaccepted token %s...",
                    client, presented[:4])
        return False

    @router.get("/api/transcripts/queue")
    def get_queue(request: Request,
                  limit: int = QUEUE_DEFAULT_LIMIT,
                  kinds: str = QUEUE_DEFAULT_KINDS,
                  since: int = QUEUE_DEFAULT_SINCE_DAYS,
                  resolve_audio: bool = True) -> JSONResponse:
        """What the Mac should transcribe next, newest first.

        Read-only apart from the skip rows the relevance gate writes, which it
        already writes on every local run. Those rows are what stop a
        below-threshold item being re-scored on every poll.
        """
        if not _authorized(request):
            return JSONResponse(_UNAUTHORIZED, status_code=401)
        if not db_path.exists():
            return JSONResponse(
                {"items": [], "empty": True,
                 "reason": f"no warehouse at {db_path}; nothing is queued yet"})

        from fpl_edge.ingest.content import asr
        from fpl_edge.ingest.content.transcribe_cmd import (
            record_gate_skips,
            select_queue,
        )
        from fpl_edge.platform.query import read_copy

        wanted = tuple(k.strip() for k in kinds.split(",") if k.strip())
        if not wanted:
            return JSONResponse(
                {"detail": "kinds must name at least one content_item.kind"},
                status_code=400)
        limit = max(1, min(int(limit), QUEUE_MAX_LIMIT))
        cutoff = (dt.datetime.now(UTC) - dt.timedelta(days=since)) if since else None

        with read_copy(db_path) as wh:
            from fpl_edge.ingest.content.analyse_cmd import RELEVANCE_THRESHOLD

            selection = select_queue(
                wh, kinds=wanted, creators=frozenset(), since=cutoff,
                min_relevance=RELEVANCE_THRESHOLD)
            stored_enclosures, enclosure_origin = asr.enclosure_lookup(wh)

        # The gate's verdicts, recorded exactly as the local command records
        # them. This is the one write a queue poll performs.
        recorded = 0
        try:
            recorded = record_gate_skips(str(db_path), selection.gated)
        except Exception as exc:  # noqa: BLE001 - serving the queue matters more
            log.warning("could not record %d relevance skips: %s: %s",
                        len(selection.gated), type(exc).__name__, exc)

        rows = selection.queue.head(limit)
        items: list[dict[str, Any]] = []
        for row in rows.itertuples(index=False):
            item_id = str(row.item_id)
            items.append({
                "item_id": item_id,
                "source_key": str(row.source_key),
                "creator": str(row.creator),
                "title": str(row.title or ""),
                "kind": str(row.kind),
                "url": str(row.url or ""),
                "published_at": (None if row.published_at is None
                                 else str(row.published_at)),
                "audio_url": stored_enclosures.get(item_id),
            })

        audio_note = f"audio urls from {enclosure_origin}"
        if resolve_audio and _network_allowed():
            found = _resolve_missing_enclosures(items)
            if found:
                audio_note += f", plus {found} re-parsed from the source feeds"
        elif resolve_audio:
            audio_note += ("; the feed re-parse was skipped because "
                           "FPL_EDGE_DISABLE_NETWORK_INGEST is set")

        return JSONResponse({
            "items": items,
            "queued": selection.queued_before_gate,
            "passed_gate": len(selection.queue),
            "gated": len(selection.gated),
            "gate_rows_recorded": recorded,
            "already_transcribed": selection.already,
            "audio_note": audio_note,
            "generated_at": dt.datetime.now(UTC).isoformat(),
        })

    @router.post("/api/transcripts")
    async def post_transcript(request: Request) -> JSONResponse:
        """Store one finished transcription. See the module docstring."""
        if not _authorized(request):
            return JSONResponse(_UNAUTHORIZED, status_code=401)

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return JSONResponse(
                {"detail": f"body of {declared} bytes is over the "
                           f"{MAX_BODY_BYTES} byte cap"}, status_code=413)
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            return JSONResponse(
                {"detail": f"body of {len(raw)} bytes is over the "
                           f"{MAX_BODY_BYTES} byte cap"}, status_code=413)

        token = _presented_token(request) or ""
        if not limiter.allow(token[:8]):
            return JSONResponse(
                {"detail": f"over {RATE_LIMIT_PER_MIN} pushes per minute for "
                           f"this token; nothing was written"}, status_code=429)

        try:
            body = TranscriptIn.model_validate_json(raw)
        except ValidationError as exc:
            return JSONResponse({"detail": exc.errors(include_url=False)},
                                status_code=400)

        if not body.segments:
            return JSONResponse(
                {"detail": f"{body.item_id}: refusing to store zero segments"},
                status_code=400)
        text = " ".join(s.text for s in body.segments).strip()
        if not text:
            return JSONResponse(
                {"detail": f"{body.item_id}: refusing to store an empty "
                           f"transcript"}, status_code=400)
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()

        if not db_path.exists():
            return JSONResponse(
                {"detail": f"no warehouse at {db_path}; there is nothing to "
                           f"attach a transcript to"}, status_code=503)

        from fpl_edge.platform.query import read_copy

        with read_copy(db_path) as wh:
            prior = wh.sql(
                "SELECT text_source, text_sha256 FROM content_item "
                "WHERE item_id = ?", [body.item_id])
        if prior.empty:
            return JSONResponse(
                {"detail": f"no content_item {body.item_id!r}; an orphan "
                           f"transcript has nothing to attach to"},
                status_code=404)
        prior_source = str(prior.iloc[0]["text_source"] or "")
        prior_hash = str(prior.iloc[0]["text_sha256"] or "")

        if prior_source == "transcript" and prior_hash == text_sha256:
            return JSONResponse({
                "stored": False,
                "item_id": body.item_id,
                "reason": "identical transcript already stored",
                "text_sha256": text_sha256,
            })
        if prior_source == "transcript" and not body.replace:
            return JSONResponse({
                "stored": False,
                "item_id": body.item_id,
                "reason": ("a different transcript is already stored for this "
                           "item. Push again with replace=true to overwrite "
                           "it; the swap is then recorded in "
                           "transcript_provenance as prior_text_sha256."),
                "stored_text_sha256": prior_hash,
                "pushed_text_sha256": text_sha256,
            }, status_code=409)

        transcription = _to_transcription(body)
        written, dropped, cleanup_note = _store(db_path, body.item_id,
                                                transcription, body.derivation)
        if written is None:
            return JSONResponse({"detail": cleanup_note}, status_code=400)
        payload: dict[str, Any] = {
            "stored": True,
            "item_id": body.item_id,
            "segments": written,
            "text_sha256": text_sha256,
            "derivation": body.derivation,
            "stale_analyses_dropped": dropped,
        }
        if cleanup_note:
            payload["note"] = cleanup_note
        return JSONResponse(payload)

    return router


def _network_allowed() -> bool:
    """The same switch every scheduled fetch honours."""
    return os.environ.get("FPL_EDGE_DISABLE_NETWORK_INGEST", "") in ("", "0")


def _resolve_missing_enclosures(items: list[dict[str, Any]]) -> int:
    """Fill ``audio_url`` for podcast items the stored column did not cover.

    At most one feed read per source, and only for sources that appear in the
    items being served. The Mac cannot download audio it has no url for, so
    serving the item without one would send the worker a row it can do nothing
    with.
    """
    from fpl_edge.ingest.content.sources import BY_KEY

    needed = sorted({
        str(i["source_key"]) for i in items
        if i["kind"] == "podcast" and not i.get("audio_url")
        and str(i["source_key"]) in BY_KEY
    })
    if not needed:
        return 0

    from fpl_edge.ingest.content import asr
    from fpl_edge.ingest.content.transcribe_cmd import _asr_fetcher

    found: dict[str, str] = {}
    fetcher = _asr_fetcher(2.0)
    try:
        for key in needed:
            try:
                urls, _status = asr.enclosures_from_feed(fetcher, BY_KEY[key])
            except Exception as exc:  # noqa: BLE001 - one feed, not the poll
                log.warning("feed re-parse failed for %s: %s: %s", key,
                            type(exc).__name__, exc)
                continue
            found.update(urls)
    finally:
        fetcher.close()

    filled = 0
    for item in items:
        if not item.get("audio_url") and item["item_id"] in found:
            item["audio_url"] = found[item["item_id"]]
            filled += 1
    return filled


def _to_transcription(body: TranscriptIn):
    """The pushed JSON as the dataclasses ``asr`` writes from."""
    from fpl_edge.ingest.content import asr

    return asr.Transcription(
        segments=tuple(
            asr.Segment(seq=int(s.seq), start_s=float(s.start_s),
                        end_s=float(s.end_s), text=s.text)
            for s in body.segments
        ),
        model=body.model,
        engine=body.engine,
        language=body.language,
        audio_seconds=(None if body.audio_seconds is None
                       else float(body.audio_seconds)),
        covered_seconds=float(body.covered_seconds),
        wall_seconds=float(body.wall_seconds),
        audio_sha256=body.audio_sha256,
        audio_bytes=int(body.audio_bytes),
        audio_url=body.audio_url,
        created_utc=(body.created_utc or dt.datetime.now(UTC)),
    )


def _store(db_path, item_id: str, transcription, derivation: str):
    """Two write leases, behind the process-wide writer lock.

    Separate leases for the reason ``transcribe_cmd`` gives: sharing one
    transaction meant a failure in the stale-analysis cleanup rolled back the
    transcript, and that is what the 2026-09-01 and 2026-09-02 nightly
    failures were. The transcript is the expensive half, so it commits first
    and the cleanup is allowed to fail without taking it down.

    Both leases sit inside the scheduler's writer lease, so a push that
    arrives while a tick is running waits for the tick rather than racing it
    into a DuckDB transaction conflict.
    """
    from fpl_edge.ingest.content import asr
    from fpl_edge.ingest.content.analyse_cmd import _write_with_retry
    from fpl_edge.platform import scheduler

    written = 0
    dropped = 0
    note = ""

    def _write(wh):
        nonlocal written
        written = asr.store_transcription(wh, item_id, transcription,
                                          derivation=derivation)

    def _drop_stale(wh):
        nonlocal dropped
        dropped = asr.stale_analyses(wh, item_id)

    with scheduler.write_lease(holder=f"POST /api/transcripts {item_id}"):
        try:
            _write_with_retry(str(db_path), _write)
        except asr.PartialTranscript as exc:
            return None, 0, str(exc)
        try:
            _write_with_retry(str(db_path), _drop_stale)
        except Exception as exc:  # noqa: BLE001 - tidy-up, never the write
            note = (f"transcript stored; the stale-analysis cleanup failed "
                    f"({type(exc).__name__}: {exc}). Run "
                    f"`pipeline analyze --retry-skipped` to refresh it.")
    return written, dropped, note
