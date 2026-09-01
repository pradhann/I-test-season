"""Local speech-to-text, so the corpus holds what was *said*.

Why this module exists
----------------------

479 of the 594 items in ``content_item`` carry ``text_source='description'``:
show notes, not speech. The Creators tab therefore renders summaries like "No
player, captaincy or chip positions are stated anywhere in the notes", which is
a true statement about the notes and a useless one about the creator. Two items
in the entire warehouse carry a real transcript. Transcription is the unlock.

Three invariants, in order of how badly breaking them would hurt
----------------------------------------------------------------

1. **No Anthropic tokens are ever spent on transcription.** Not as a fallback,
   not "just for the hard ones", not behind a flag. This module imports no
   model client and makes no HTTP request to any inference API. The only
   network it does is downloading the creator's own published audio. If the
   local engine is missing, it raises :class:`AsrUnavailable` with the exact
   install command and stops -- it does not degrade to something remote.
2. **Nothing partial is ever stored as if it were whole.** Whisper's
   best-known failure is stopping early: a 62-minute episode transcribed to
   4 minutes of text, returned without an error, indistinguishable from a
   short episode. That is worse than no transcript, because a claim extractor
   reading it would report "the creator never mentioned Haaland". So the
   decoded audio duration is measured independently of the decoder, coverage
   is computed against it, and a run that covers less than
   :data:`MIN_COVERAGE` of the audio raises :class:`PartialTranscript` and
   stores nothing.
3. **Segments carry timestamps.** The UI deep-links to the moment a claim was
   made; an untimed wall of text cannot do that. ``word_timestamps`` is off
   (it costs ~30% for alignment we do not use) but segment-level start/end is
   always present.

Engine choice
-------------

MLX-Whisper, via its local Python API. Benchmarked in this repo at roughly 5x
faster-whisper on this machine (docs/platform/SECTION_PROMPTS.md, "HISTORY
WORTH KNOWING"); it runs on the Metal GPU through mlx.

Audio decode: PyAV, **not** a system ``ffmpeg``. ``mlx_whisper.transcribe``
accepts a str path -- in which case it shells out to ``ffmpeg`` -- or a numpy
array of 16 kHz mono float32, in which case it does not. PyAV ships the FFmpeg
libraries inside its wheel, so ``uv pip install av`` removes a Homebrew
dependency from a pipeline that has to run unattended. A system ``ffmpeg`` is
still used if PyAV is absent and the binary is on PATH; see
:func:`backend_status`, which reports which of the two is actually available
rather than assuming.

Caching
-------

Downloaded audio is content-addressed under :data:`AUDIO_CACHE` by the sha256
of its URL, and the cache is consulted *before* the network, so a re-run of a
failed batch re-fetches nothing. That is a politeness measure first and a speed
measure second: these are individual creators' hosting bills.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import shutil
import subprocess
import time
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

UTC = dt.UTC

#: Where downloaded audio lands. Sits under the same ``data/raw/content`` root
#: the rest of this package archives to.
AUDIO_CACHE = Path("data/raw/content/asr_audio")

#: Default weights. large-v3-turbo is the quality/speed knee on Apple Silicon:
#: near large-v3 word error rate at roughly 6x its decode cost, ~1.6 GB on
#: first use, cached by huggingface_hub thereafter.
DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"

#: Fraction of the audio's real duration the transcript's last segment must
#: reach. Trailing outro music and silence legitimately produce no segments, so
#: this is not 1.0; but a transcript that stops at 40% of the episode is the
#: early-stop failure and must not be stored.
MIN_COVERAGE = 0.80

#: ...unless the absolute shortfall is small. A 4-minute clip whose last 45s
#: are an outro sting is 81% covered and fine; the ratio alone would fail a
#: 3-minute clip with the same 45s outro. Either test passing is enough.
MAX_UNCOVERED_S = 90.0

#: Whisper's native sample rate. Feeding it anything else silently changes the
#: pitch the model hears and destroys accuracy.
SAMPLE_RATE = 16_000

#: Audio content types we will decode. A feed that serves an HTML error page
#: with a 200 is a real and common failure; decoding it would produce either an
#: exception or, worse, a few seconds of noise transcribed as words.
AUDIO_CONTENT_TYPES = ("audio/", "video/", "application/octet-stream")

_EXT_BY_TYPE = {
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a", "audio/aac": ".aac", "audio/ogg": ".ogg",
    "audio/opus": ".opus", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "video/mp4": ".mp4",
}


class AsrUnavailable(RuntimeError):
    """The local engine is not installed.

    Carries the exact command to fix it. Never caught and turned into a
    remote-API fallback: see invariant 1 in the module docstring.
    """


class AudioUnavailable(RuntimeError):
    """The audio could not be fetched or is not audio. Nothing is stored."""


class PartialTranscript(RuntimeError):
    """The decoder returned less than the audio contains. Nothing is stored."""


# ---------------------------------------------------------------------------
# Backend discovery


@dataclass(frozen=True, slots=True)
class BackendStatus:
    """What is actually installed, measured by importing it.

    Reported by ``pipeline transcribe`` before any work starts, so a run that
    cannot possibly succeed says why in its first three lines instead of after
    a 40-minute download.
    """

    mlx_whisper: bool
    decoder: str | None            # "pyav" | "ffmpeg" | None
    mlx_whisper_error: str | None = None

    @property
    def ready(self) -> bool:
        return self.mlx_whisper and self.decoder is not None

    def install_hint(self) -> str:
        missing = []
        if not self.mlx_whisper:
            missing.append("mlx-whisper")
        if self.decoder is None:
            missing.append("av")
        if not missing:
            return ""
        return (
            f"missing: {', '.join(missing)}. Install with:\n"
            f"    uv pip install {' '.join(missing)}\n"
            f"(PyAV bundles FFmpeg, so no Homebrew install is needed. A system "
            f"ffmpeg on PATH works as an alternative decoder.)"
        )

    def render(self) -> str:
        lines = [
            f"engine:   mlx-whisper {'present' if self.mlx_whisper else 'MISSING'}"
            + (f" ({self.mlx_whisper_error})" if self.mlx_whisper_error else ""),
            f"decoder:  {self.decoder or 'MISSING (need PyAV or ffmpeg on PATH)'}",
        ]
        if not self.ready:
            lines.append(self.install_hint())
        return "\n".join(lines)


def backend_status() -> BackendStatus:
    """Import the engine and look for a decoder. No network, no model load."""
    err: str | None = None
    try:
        import mlx_whisper  # noqa: F401
        has_mlx = True
    except Exception as exc:  # noqa: BLE001 - an mlx that fails to load is missing
        has_mlx = False
        err = f"{type(exc).__name__}: {exc}"

    decoder: str | None = None
    try:
        import av  # noqa: F401
        decoder = "pyav"
    except ImportError:
        if shutil.which("ffmpeg"):
            decoder = "ffmpeg"
    return BackendStatus(mlx_whisper=has_mlx, decoder=decoder, mlx_whisper_error=err)


def require_backend() -> BackendStatus:
    status = backend_status()
    if not status.ready:
        raise AsrUnavailable(status.install_hint())
    return status


# ---------------------------------------------------------------------------
# Audio acquisition


def cache_path_for(url: str, *, content_type: str | None = None) -> Path:
    """Content-addressed cache location for a URL's audio.

    Keyed on the URL rather than the body because the point is to answer "have
    we already downloaded this?" *before* spending the download.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    ext = _EXT_BY_TYPE.get((content_type or "").split(";")[0].strip().lower())
    if ext is None:
        suffix = Path(urllib.parse.urlsplit(url).path).suffix.lower()
        ext = suffix if suffix in set(_EXT_BY_TYPE.values()) else ".audio"
    return AUDIO_CACHE / f"{digest}{ext}"


def cached_audio(url: str) -> Path | None:
    """An already-downloaded copy, or None. Consulted before any request."""
    for path in sorted(AUDIO_CACHE.glob(
        hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".*"
    )):
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


@dataclass(frozen=True, slots=True)
class AudioFetch:
    """The outcome of trying to obtain one audio file, successes and refusals.

    ``status`` is the REAL HTTP code and is recorded even when it is a refusal,
    because "this feed now 403s" is a fact the source registry needs and a
    silently-skipped item is a fact it never learns.
    """

    url: str
    path: Path | None
    status: int | None
    bytes_received: int
    from_cache: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None and self.error is None


def fetch_audio(fetcher, url: str, *, max_bytes: int = 400 * 1024 * 1024) -> AudioFetch:
    """Download one audio file, politely, once.

    ``fetcher`` is a :class:`~fpl_edge.ingest.content.fetch.ContentFetcher`,
    which supplies the project User-Agent, the robots.txt check and the
    inter-request delay. It is passed in rather than constructed so the caller
    owns the rate limit for the whole batch -- a fetcher per item would reset
    the throttle on every download and defeat it entirely.

    A refusal (403/429) is returned, not retried around. See the policy note in
    :mod:`fpl_edge.ingest.content.youtube`.
    """
    hit = cached_audio(url)
    if hit is not None:
        return AudioFetch(url=url, path=hit, status=None,
                          bytes_received=hit.stat().st_size, from_cache=True)

    resp = fetcher.get(url, retries=1)
    if resp.robots_blocked:
        return AudioFetch(url, None, None, 0, False, error="robots_disallow")
    if resp.status in (403, 429):
        # The source declining. Recorded and obeyed.
        return AudioFetch(url, None, resp.status, len(resp.body), False,
                          error=f"refused_{resp.status}")
    if not resp.ok:
        return AudioFetch(url, None, resp.status, len(resp.body), False,
                          error=resp.error or f"http_{resp.status}")
    if not resp.body:
        return AudioFetch(url, None, resp.status, 0, False, error="empty_body")
    if len(resp.body) > max_bytes:
        return AudioFetch(url, None, resp.status, len(resp.body), False,
                          error=f"too_large_{len(resp.body)}")
    if _looks_like_html(resp.body):
        # A 200 that is an error page. Decoding it yields either a crash or a
        # few seconds of noise transcribed into sentences nobody said.
        return AudioFetch(url, None, resp.status, len(resp.body), False,
                          error="not_audio_html")

    path = cache_path_for(url, content_type=_sniff_type(resp.body))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(resp.body)
    # Atomic rename: a half-written cache entry that a later run trusts is the
    # same class of bug as a partial transcript.
    tmp.replace(path)
    return AudioFetch(url, path, resp.status, len(resp.body), False)


def _looks_like_html(body: bytes) -> bool:
    head = body[:512].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html", b"<?xml"))


def _sniff_type(body: bytes) -> str | None:
    if body[:3] == b"ID3" or body[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if body[4:8] == b"ftyp":
        return "audio/mp4"
    if body[:4] == b"OggS":
        return "audio/ogg"
    if body[:4] == b"RIFF":
        return "audio/wav"
    return None


# ---------------------------------------------------------------------------
# Decode


def decode_audio(path: Path, *, decoder: str) -> tuple[object, float]:
    """Audio file -> (float32 mono 16 kHz numpy array, duration seconds).

    The duration is computed from the sample count of what was actually
    decoded, not from the container's declared metadata. A truncated download
    declares the original length in its header, so trusting the header would
    make a half-downloaded episode look fully covered.
    """
    import numpy as np

    if decoder == "pyav":
        samples = _decode_pyav(path)
    elif decoder == "ffmpeg":
        samples = _decode_ffmpeg(path)
    else:  # pragma: no cover - guarded by require_backend
        raise AsrUnavailable(f"unknown decoder {decoder!r}")
    if samples.size == 0:
        raise AudioUnavailable(f"decoded 0 samples from {path.name}")
    return samples.astype(np.float32), samples.size / SAMPLE_RATE


def _decode_pyav(path: Path):
    import av
    import numpy as np

    chunks: list = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise AudioUnavailable(f"no audio stream in {path.name}")
        stream.thread_type = "AUTO"
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype="float32")
    return np.concatenate(chunks)


def _decode_ffmpeg(path: Path):
    import numpy as np

    # Fixed argv, no shell: `path` cannot be read as a flag or a command.
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-threads", "0", "-i", str(path),
         "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le",
         "-ar", str(SAMPLE_RATE), "-"],
        capture_output=True, check=False,
    )
    if proc.returncode != 0:
        raise AudioUnavailable(
            f"ffmpeg failed on {path.name}: "
            f"{proc.stderr.decode('utf-8', 'replace')[-200:]}"
        )
    return np.frombuffer(proc.stdout, np.int16).astype("float32") / 32768.0


# ---------------------------------------------------------------------------
# Transcription


@dataclass(frozen=True, slots=True)
class Segment:
    """One timestamped chunk of speech. ``start_s`` is what the UI links to."""

    seq: int
    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True, slots=True)
class Transcription:
    """A complete transcription, with the evidence that it *is* complete."""

    segments: tuple[Segment, ...]
    model: str
    engine: str
    language: str | None
    #: Duration of the audio as decoded, or None when the audio was never
    #: downloaded -- the published-captions path, where the only honest answer
    #: is that we do not know how long the video is. NOT filled in with the
    #: last cue's timestamp, which would be a lower bound wearing a
    #: measurement's clothes and would make ``coverage`` read 100% by
    #: construction.
    audio_seconds: float | None
    covered_seconds: float
    wall_seconds: float
    audio_sha256: str
    audio_bytes: int
    audio_url: str
    created_utc: dt.datetime = field(default_factory=lambda: dt.datetime.now(UTC))

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments).strip()

    @property
    def coverage(self) -> float | None:
        """Fraction of the audio the transcript reaches, or None if unmeasured."""
        if not self.audio_seconds:
            return None
        return self.covered_seconds / self.audio_seconds

    @property
    def rate(self) -> float | None:
        """Minutes of audio per minute of wall clock. The number to batch on."""
        if not self.audio_seconds or not self.wall_seconds:
            return None
        return self.audio_seconds / self.wall_seconds

    def render(self) -> str:
        if self.audio_seconds is None:
            return (f"{len(self.segments)} segments to "
                    f"{self.covered_seconds / 60:.1f} min, {len(self.text)} chars "
                    f"({self.engine}; audio duration not measured)")
        return (
            f"{len(self.segments)} segments, {self.audio_seconds / 60:.1f} min audio "
            f"in {self.wall_seconds:.0f}s wall ({(self.rate or 0):.1f}x realtime), "
            f"coverage {(self.coverage or 0):.0%}, {len(self.text)} chars"
        )


def transcribe_file(
    path: Path,
    *,
    audio_url: str = "",
    model: str = DEFAULT_MODEL,
    language: str | None = "en",
    status: BackendStatus | None = None,
    min_coverage: float = MIN_COVERAGE,
) -> Transcription:
    """Transcribe one audio file locally. Raises rather than returning a stub.

    Raises
    ------
    AsrUnavailable
        The engine or a decoder is not installed.
    AudioUnavailable
        The file could not be decoded into audio.
    PartialTranscript
        The decoder returned fewer seconds of speech than the audio holds. The
        caller must store nothing; see invariant 2.
    """
    status = status or require_backend()
    if not status.ready:
        raise AsrUnavailable(status.install_hint())
    assert status.decoder is not None

    audio, duration_s = decode_audio(path, decoder=status.decoder)

    from mlx_whisper.transcribe import transcribe as _mlx_transcribe

    started = time.monotonic()
    result = _mlx_transcribe(
        audio,
        path_or_hf_repo=model,
        language=language,
        word_timestamps=False,
        verbose=None,
        condition_on_previous_text=False,
    )
    wall = time.monotonic() - started

    segments = tuple(_segments_from(result.get("segments") or ()))
    if not segments:
        raise PartialTranscript(
            f"{path.name}: engine returned no segments for "
            f"{duration_s / 60:.1f} minutes of audio; nothing stored"
        )
    covered = max(s.end_s for s in segments)
    uncovered = duration_s - covered
    if covered < min_coverage * duration_s and uncovered > MAX_UNCOVERED_S:
        raise PartialTranscript(
            f"{path.name}: transcript ends at {covered / 60:.1f} min of "
            f"{duration_s / 60:.1f} min audio ({covered / duration_s:.0%} "
            f"coverage, {uncovered / 60:.1f} min missing). Refusing to store a "
            f"partial transcript as a whole one."
        )

    return Transcription(
        segments=segments,
        model=model,
        engine="mlx-whisper",
        language=str(result.get("language") or language or ""),
        audio_seconds=duration_s,
        covered_seconds=covered,
        wall_seconds=wall,
        audio_sha256=_sha256_file(path),
        audio_bytes=path.stat().st_size,
        audio_url=audio_url,
    )


def _segments_from(raw: Iterable[dict]) -> Iterable[Segment]:
    seq = 0
    for item in raw:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        yield Segment(
            seq=seq,
            start_s=float(item.get("start") or 0.0),
            end_s=float(item.get("end") or item.get("start") or 0.0),
            text=text,
        )
        seq += 1


def transcription_from_captions(
    lines, *, video_id: str, route: str, wall_seconds: float
) -> Transcription:
    """Published caption cues -> the same record shape ASR output uses.

    Stored through the same path so both derivations land in
    ``transcript_segment`` identically and the difference is recorded in
    ``transcript_provenance.derivation`` rather than implied by the shape of
    the row. ``end_s`` is the NEXT cue's start, which is a display convenience
    and not a measurement -- caption tracks carry a duration per cue that this
    route does not read, and the last cue gets its own start.
    """
    starts = [float(line.start_s) for line in lines]
    segments = tuple(
        Segment(
            seq=i,
            start_s=starts[i],
            end_s=starts[i + 1] if i + 1 < len(starts) else starts[i],
            text=str(line.text).strip(),
        )
        for i, line in enumerate(lines)
        if str(line.text).strip()
    )
    return Transcription(
        segments=segments,
        model=route,
        engine="youtube_captions",
        language="en",
        audio_seconds=None,          # never downloaded; see the field's note
        covered_seconds=max(starts) if starts else 0.0,
        wall_seconds=wall_seconds,
        audio_sha256="",
        audio_bytes=0,
        audio_url=f"https://www.youtube.com/watch?v={video_id}",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Persistence
#
# transcript_segment is (item_id, seq, start_s, text) and STAYS that shape:
# fpl_edge/interfaces/creators.py inserts into it positionally with four
# values, so adding a fifth column would break the owner-shared-link path --
# which the brief requires to keep working exactly as it is. Derivation is
# therefore recorded one row per item in a side table, which is also its
# natural grain: a derivation is a property of how the item was transcribed,
# not of each individual segment.

PROVENANCE_DDL = """
CREATE TABLE IF NOT EXISTS transcript_provenance (
    item_id           VARCHAR PRIMARY KEY,
    -- 'asr' (this module, from the creator's own audio) or 'captions'
    -- (published caption track). Segments with NO row here predate this table
    -- and are caption-derived; absence is not evidence of ASR.
    derivation        VARCHAR NOT NULL,
    engine            VARCHAR,          -- mlx-whisper | youtube_captions
    model             VARCHAR,          -- weights id, so a re-run is comparable
    language          VARCHAR,
    audio_url         VARCHAR,
    audio_sha256      VARCHAR,
    audio_bytes       BIGINT,
    audio_seconds     DOUBLE,
    covered_seconds   DOUBLE,           -- last segment end: the completeness proof
    wall_seconds      DOUBLE,
    n_segments        INTEGER,
    -- content_item.text is REPLACED when a transcript supersedes show notes.
    -- The hash of what was there before makes that swap auditable.
    prior_text_source VARCHAR,
    prior_text_sha256 VARCHAR,
    created_utc       TIMESTAMPTZ NOT NULL
)
"""


def ensure_schema(wh) -> None:
    """Create the provenance table. Idempotent; safe under concurrent writers."""
    wh.sql(PROVENANCE_DDL)


def store_provenance(
    wh,
    item_id: str,
    transcription: Transcription,
    *,
    derivation: str,
    prior_text_source: str | None = None,
    prior_text_sha256: str | None = None,
) -> None:
    """Write (replacing) THE provenance row for one item.

    Extracted from :func:`store_transcription` so the owner-shared-link path
    (:func:`fpl_edge.interfaces.creators.ingest_link`), which writes
    ``transcript_segment`` itself, can record the same receipt through the
    same insert instead of growing a second schema -- PIPELINES.md §3
    defect 4: paste-a-link was the one transcript path with no provenance.
    The column list and order are the DDL's above; this is the only INSERT
    into ``transcript_provenance`` in the repo.
    """
    ensure_schema(wh)
    wh.sql("DELETE FROM transcript_provenance WHERE item_id = ?", [item_id])
    wh.sql(
        "INSERT INTO transcript_provenance VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            item_id, derivation, transcription.engine, transcription.model,
            transcription.language, transcription.audio_url,
            transcription.audio_sha256, int(transcription.audio_bytes),
            None if transcription.audio_seconds is None
            else float(transcription.audio_seconds),
            float(transcription.covered_seconds),
            float(transcription.wall_seconds), len(transcription.segments),
            prior_text_source, prior_text_sha256, transcription.created_utc,
        ],
    )


def store_transcription(
    wh,
    item_id: str,
    transcription: Transcription,
    *,
    derivation: str = "asr",
    promote_text: bool = True,
) -> int:
    """Write segments, provenance, and (optionally) promote the item's text.

    Called inside a short write lease, one item at a time, never around a
    transcription. Everything here is a handful of statements against rows we
    already have in memory: the lock is held for milliseconds.

    Returns the number of segments written. Raises on an empty transcription
    rather than writing a row that claims an item was transcribed to nothing.
    """
    if not transcription.segments:
        raise PartialTranscript(f"{item_id}: refusing to store zero segments")

    text = transcription.text
    if not text.strip():
        raise PartialTranscript(f"{item_id}: refusing to store an empty transcript")

    ensure_schema(wh)
    prior = wh.sql(
        "SELECT text_source, text_sha256 FROM content_item WHERE item_id = ?",
        [item_id],
    )
    prior_source = str(prior.iloc[0]["text_source"]) if not prior.empty else None
    prior_hash = str(prior.iloc[0]["text_sha256"]) if not prior.empty else None

    wh.sql("BEGIN TRANSACTION")
    try:
        wh.sql("DELETE FROM transcript_segment WHERE item_id = ?", [item_id])
        for seg in transcription.segments:
            wh.sql(
                "INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
                [item_id, seg.seq, seg.start_s, seg.text],
            )
        store_provenance(wh, item_id, transcription, derivation=derivation,
                         prior_text_source=prior_source,
                         prior_text_sha256=prior_hash)
        if promote_text:
            # text AND text_source move together. Setting text_source alone
            # would tell analyze.is_scoreable() to treat show notes as speech,
            # which is the exact mislabelling this whole module exists to end.
            wh.sql(
                "UPDATE content_item SET text = ?, text_source = 'transcript', "
                "text_sha256 = ? WHERE item_id = ?",
                [text, hashlib.sha256(text.encode("utf-8")).hexdigest(), item_id],
            )
        wh.sql("COMMIT")
    except Exception:
        wh.sql("ROLLBACK")
        raise
    return len(transcription.segments)


def stale_analyses(wh, item_id: str) -> int:
    """Delete analyses of this item that read something other than the transcript.

    An analysis is a derived artifact keyed (item_id, model) and rewritten by
    ``INSERT OR REPLACE``; deleting one loses nothing that re-running
    ``pipeline analyze`` does not restore. Leaving it is the harmful option:
    ``analyze`` skips items that already have a row for the model, so the
    Creators tab would keep rendering the show-notes read ("no positions are
    stated in the notes") on an item that now holds an hour of speech.

    Returns how many were removed.
    """
    rows = wh.sql(
        "SELECT count(*) c FROM content_analysis WHERE item_id = ? AND "
        "coalesce(json_extract_string(analysis_json, '$.evidence.text_source'), "
        "         'unknown') <> 'transcript'",
        [item_id],
    )
    n = int(rows.iloc[0]["c"])
    if n:
        wh.sql(
            "DELETE FROM content_analysis WHERE item_id = ? AND "
            "coalesce(json_extract_string(analysis_json, '$.evidence.text_source'), "
            "         'unknown') <> 'transcript'",
            [item_id],
        )
    return n


# ---------------------------------------------------------------------------
# Podcast enclosures
#
# The audio URL for an episode lives in the RSS <enclosure>. A concurrent agent
# is adding `content_item.enclosure_url`; this module prefers that column and
# falls back to re-parsing the feed when it is not there yet.


#: Where a stored enclosure URL may live, most authoritative first.
#:
#: ``content_item_asset`` is where the concurrent feed-repair work put it
#: (migration ``content_003_item_url_and_enclosure.sql``), as a 1:1 side table
#: rather than a new column, because ``content_item`` is written positionally
#: by code this package does not own and widening it breaks those writers with
#: a column-count error. ``content_item.enclosure_url`` is checked too because
#: it is the shape this module was originally specified against; whichever
#: exists is used, and if neither does the feed is re-parsed. All three paths
#: produce the same mapping, so nothing downstream has to care which ran.
_ENCLOSURE_SOURCES: tuple[tuple[str, str], ...] = (
    ("content_item_asset", "enclosure_url"),
    ("content_item", "enclosure_url"),
)


def enclosure_lookup(wh) -> tuple[dict[str, str], str]:
    """``({item_id: audio_url}, where_it_came_from)`` from stored columns.

    Returns ``({}, "none")`` when no stored enclosure column exists yet, which
    is the caller's signal to fall back to :func:`enclosures_from_feed`. The
    name is returned rather than logged here so the command can print which of
    the three paths actually ran -- "no audio available" caused by a missing
    column and by a feed that stopped serving enclosures are different
    problems with the same symptom.
    """
    for table, column in _ENCLOSURE_SOURCES:
        present = int(wh.sql(
            "SELECT count(*) c FROM information_schema.columns "
            "WHERE table_name = ? AND column_name = ?", [table, column],
        ).iloc[0]["c"])
        if not present:
            continue
        rows = wh.sql(
            f"SELECT item_id, {column} AS url FROM {table} "
            f"WHERE {column} IS NOT NULL AND {column} <> ''"
        )
        return ({str(r.item_id): str(r.url) for r in rows.itertuples(index=False)},
                f"{table}.{column}")
    return {}, "none"


def enclosures_from_feed(fetcher, source) -> tuple[dict[str, str], int | None]:
    """``{item_id: audio_url}`` for one podcast feed, from the feed itself.

    The fallback path for as long as ``content_item.enclosure_url`` does not
    exist. It re-uses :func:`fpl_edge.ingest.content.feeds.parse_feed` rather
    than hand-rolling a second XML reader, and recomputes ``item_id`` exactly
    the way :func:`fpl_edge.ingest.content.loaders.load_feed_source` does --
    ``make_id(source_key, guid or link)``. Those two rules have to agree
    character for character or the join silently matches nothing and this step
    reports "no audio available" for a feed that publishes audio on every item.

    Returns the mapping and the REAL HTTP status of the feed fetch, including
    failures.
    """
    from fpl_edge.ingest.content.feeds import parse_feed
    from fpl_edge.ingest.content.models import ContentItem

    resp = fetcher.get(source.url)
    if not resp.ok:
        return {}, resp.status

    entries, _ = parse_feed(resp.body)
    out: dict[str, str] = {}
    for entry in entries:
        url = getattr(entry, "enclosure_url", None)
        if not url:
            continue
        enclosure = getattr(entry, "enclosure", None)
        mime = ((getattr(enclosure, "mime_type", None) or "").split(";")[0]
                .strip().lower())
        if mime and not mime.startswith(AUDIO_CONTENT_TYPES):
            continue
        identity = entry.guid or entry.link
        if not identity:
            continue
        out[ContentItem.make_id(source.key, identity)] = url
    return out, resp.status


# ---------------------------------------------------------------------------
# Audio retention (PIPELINES.md §3 defect 3, §4.4)
#
# The cache exists so a failed batch never re-downloads; once an item's
# transcript is STORED, the audio has done its work and the file is pure
# growth (episodes are 20-400MB). The deletion rule is deliberately narrow:
# a file may go only when its item holds (a) transcript segments, (b) the
# promoted transcript text, and (c) a transcript_provenance row whose
# audio_sha256 is non-empty -- the hash outlives the file, so "what exactly
# was transcribed" stays answerable forever. A file matched by NO such row
# is NEVER deleted, whatever it is: an undeciphered download, a failed run's
# leftovers, or audio for an item transcribed before provenance existed
# (those rows are caption-derived and carry no sha; their audio, if any, is
# not provably done).


@dataclass(frozen=True, slots=True)
class RetentionSweep:
    """What one cache sweep found and (unless dry_run) did."""

    #: Files deleted (or, on a dry run, that WOULD be deleted).
    deleted: tuple[Path, ...]
    bytes_freed: int
    #: Files in the cache matched by no qualifying provenance row. Kept.
    kept_unmatched: int
    #: Qualifying provenance rows whose cached file is already gone.
    matched_missing: int
    dry_run: bool
    note: str = ""

    def summary(self) -> str:
        verb = "would delete" if self.dry_run else "deleted"
        line = (f"{verb} {len(self.deleted)} file(s), "
                f"{self.bytes_freed / 1_048_576:.1f} MB; "
                f"kept {self.kept_unmatched} without provenance; "
                f"{self.matched_missing} already gone")
        return f"{line}; {self.note}" if self.note else line

    def render(self) -> str:
        lines = [self.summary()]
        for path in self.deleted:
            lines.append(f"  {'DRY ' if self.dry_run else ''}delete {path}")
        return "\n".join(lines)


def _retention_tables_present(wh) -> bool:
    needed = {"transcript_provenance", "transcript_segment", "content_item"}
    have = set(wh.sql(
        "SELECT table_name FROM information_schema.tables"
    )["table_name"].astype(str))
    return needed <= have


def sweep_audio_cache(
    wh, *, dry_run: bool = False, cache_dir: Path | str | None = None
) -> RetentionSweep:
    """Delete cached audio whose transcript is stored with full provenance.

    Never deletes a file no qualifying provenance row points at -- absence of
    provenance is absence of proof, not permission. Safe on a warehouse that
    has never transcribed anything (missing tables -> nothing is deletable).
    ``wh`` may be a read-only copy: the only writes are file deletions.
    """
    cache = Path(cache_dir) if cache_dir is not None else AUDIO_CACHE
    files = (sorted(p for p in cache.glob("*") if p.is_file())
             if cache.exists() else [])

    if not _retention_tables_present(wh):
        return RetentionSweep(
            deleted=(), bytes_freed=0, kept_unmatched=len(files),
            matched_missing=0, dry_run=dry_run,
            note="no transcript provenance in this warehouse; nothing is deletable",
        )

    rows = wh.sql(
        "SELECT p.audio_url FROM transcript_provenance p "
        "JOIN content_item i ON i.item_id = p.item_id "
        "WHERE coalesce(p.audio_sha256, '') <> '' "
        "  AND coalesce(p.audio_url, '') <> '' "
        "  AND i.text_source = 'transcript' "
        "  AND EXISTS (SELECT 1 FROM transcript_segment t "
        "              WHERE t.item_id = p.item_id)"
    )

    deletable: set[Path] = set()
    matched_missing = 0
    for url in rows["audio_url"].astype(str):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        found = [p for p in cache.glob(digest + ".*") if p.is_file()]
        if found:
            deletable.update(found)
        else:
            matched_missing += 1

    deleted: list[Path] = []
    freed = 0
    for path in sorted(deletable):
        freed += path.stat().st_size
        if not dry_run:
            path.unlink()
        deleted.append(path)

    kept = len([p for p in files if p not in deletable])
    return RetentionSweep(
        deleted=tuple(deleted), bytes_freed=freed, kept_unmatched=kept,
        matched_missing=matched_missing, dry_run=dry_run,
    )
