"""The job machine: submit, poll, approve, decline, cancel.

A pasted link becomes a job with stages, a TTL and one decision point. The
owner sees the preflight's price before anything is written, and a refusal is
obeyed rather than retried. ``LinkJobs`` owns the state and every exception the
API surface turns into a status code.

Split out of ``fpl_edge/platform/link_jobs.py`` (ARCHITECTURE_REVIEW.md
Section 3 and Section 4 row 17).
"""

from __future__ import annotations
import datetime as dt
import json
import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from fpl_edge.store.warehouse import DEFAULT_DB

from fpl_edge.platform.link_jobs.preflight import LinkRefused, PREVIEW_TTL, Preflight, STAGES, _STAGE_SPAN, _iso, _now, preflight
from fpl_edge.platform.link_jobs.take import _item_for_url, build_take, discard_item, ingest_with_retry


JOB_TTL = dt.timedelta(hours=6)


REPO_ROOT = Path(__file__).resolve().parents[2]


LINK_JOBS_DIR = REPO_ROOT / "data" / "warehouse" / "jobs" / "link"


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
    # -- who said it, and which gameweek (asks 1 and 3) --------------------
    creator: str | None = None
    creator_basis: str | None = None
    creator_reason: str | None = None
    channel: str | None = None
    tracked: bool | None = None
    gameweek: dict[str, Any] | None = None
    # -- the preview halt (ask 2) ------------------------------------------
    #: True while the job is parked at ``preview`` waiting for a decision. The
    #: job is NOT done and it is NOT running; it is a third thing, and the UI
    #: needs to be able to tell it apart from both.
    awaiting_decision: bool = False
    preview: dict[str, Any] | None = None
    preview_expires_utc: dt.datetime | None = None
    declined: bool = False
    #: The preflight, held across the halt so accepting does not re-probe the
    #: source. Not serialised; a job reloaded from disk after a restart cannot
    #: be accepted and says so.
    pre: Preflight | None = field(default=None, repr=False)
    # -- cancellation ------------------------------------------------------
    #: Set by :meth:`LinkJobs.cancel`. The worker reads it at the checkpoints
    #: in ``_run_stages``; it is an Event rather than a bool so a caller can
    #: never observe a half-written flag across threads.
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    cancelled: bool = False
    cancel_requested_utc: dt.datetime | None = None
    #: Set when the cancel landed after the ingester had already written. The
    #: item is discarded rather than deleted; see ``_cancel``.
    cancelled_after_write: bool = False
    discarded_item_id: str | None = None

    def pct(self) -> int:
        """Position on the stage ladder, refined inside ``transcribe``."""
        floor, ceiling = _STAGE_SPAN[self.stage]
        if self.done:
            return 100 if self.error is None else floor
        if self.awaiting_decision:
            # Parked, not progressing. A bar that keeps creeping while nothing
            # is happening is the spinner this ladder exists not to be.
            return floor
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
            "creator": self.creator,
            "creator_basis": self.creator_basis,
            "creator_reason": self.creator_reason,
            "channel": self.channel,
            "tracked": self.tracked,
            "gameweek": self.gameweek,
            "awaiting_decision": self.awaiting_decision,
            "preview": self.preview,
            "preview_expires_utc": _iso(self.preview_expires_utc),
            "declined": self.declined,
            "cancelled": self.cancelled,
            "cancel_requested_utc": _iso(self.cancel_requested_utc),
            "cancelled_after_write": self.cancelled_after_write,
            "discarded_item_id": self.discarded_item_id,
            "result": self.result,
            "started_utc": _iso(self.started_utc),
            "finished_utc": _iso(self.finished_utc),
        }


class _Cancelled(RuntimeError):
    """Raised inside the worker to unwind to ``_run`` after a cancel."""


class UnknownJob(KeyError):
    """No job with that id, in memory or on disk."""


class JobAlreadyFinished(RuntimeError):
    """Cancel was asked for a job that has already ended.

    Kept distinct from success on purpose. "Cancelled" and "it had already
    finished, and here is the item it produced" are different answers, and
    reporting the second as the first would tell the owner nothing was stored
    when something was. The item is removable -- that is what ``discard`` is
    for -- but removing it is a second, explicit decision.
    """

    def __init__(self, message: str, state: dict[str, Any]) -> None:
        super().__init__(message)
        self.state = state


class NotAwaitingDecision(RuntimeError):
    """Accept/decline was asked for a job that is not parked at the preview."""

    def __init__(self, message: str, state: dict[str, Any]) -> None:
        super().__init__(message)
        self.state = state


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

        self._start(job, self._run)
        return {"job_id": job.job_id, "stages": list(STAGES),
                "url": url, "accepted_utc": _iso(job.started_utc)}

    def _start(self, job: _Job, target) -> None:
        if self.background:
            thread = threading.Thread(target=target, args=(job.job_id,),
                                      name=f"link-ingest-{job.job_id}",
                                      daemon=True)
            self._threads[job.job_id] = thread
            thread.start()
        else:
            target(job.job_id)

    def accept(self, job_id: str) -> dict[str, Any]:
        """Go ahead and transcribe. The only thing that spends GPU seconds.

        Everything before this call cost one page fetch. Everything after it is
        the pipeline exactly as it was.
        """
        job = self._require(job_id)
        if not job.awaiting_decision:
            raise NotAwaitingDecision(
                f"job {job_id} is not waiting for a decision (stage "
                f"{job.stage!r}, done={job.done}); there is nothing to accept.",
                job.public())
        if job.pre is None:  # pragma: no cover - only after a process restart
            raise NotAwaitingDecision(
                f"job {job_id} was parked by a previous process, so its "
                f"preflight is gone and accepting it would silently re-probe "
                f"the source. Paste the link again.", job.public())
        job.awaiting_decision = False
        job.preview_expires_utc = None
        self._persist(job)
        self._start(job, self._run_after_decision)
        return job.public()

    def decline(self, job_id: str, *, reason: str = "") -> dict[str, Any]:
        """Say no at the preview halt. Nothing was written, so nothing is undone.

        This is the headline control the owner asked for: "if it's not relevant
        why even transcribe?". The cost of declining is the one page fetch that
        produced the preview, and zero GPU seconds.
        """
        job = self._require(job_id)
        if job.done:
            raise JobAlreadyFinished(
                f"job {job_id} has already finished; declining it now would "
                f"claim nothing was stored when something may have been.",
                job.public())
        if not job.awaiting_decision:
            # Declining a phase-two job is a cancel, and cancel is where the
            # after-the-write case is handled honestly.
            return self.cancel(job_id)
        job.awaiting_decision = False
        job.declined = True
        job.done = True
        job.error = (
            "declined at the preview: nothing was transcribed, analysed or "
            "stored. The only cost was the one page fetch that produced the "
            "preview."
            + (f" Reason: {reason}" if reason else ""))
        job.error_code = "declined"
        job.item_id = None
        job.eta_s = None
        job.eta_basis = None
        job.eta_reason = "declined before the work it would have timed"
        job.finished_utc = _now()
        self._persist(job)
        return job.public()

    def _require(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            state = self._load(job_id)
            if state is None:
                raise UnknownJob(f"no ingest job {job_id!r}")
            raise JobAlreadyFinished(
                f"job {job_id} is not live in this process.", state)
        self._expire_preview(job)
        return job

    def _expire_preview(self, job: _Job) -> None:
        """A decision nobody made is not a decision that is still pending.

        Expiry writes nothing and undoes nothing -- there is nothing to undo,
        which is the property the halt was placed before the write to get.
        """
        if not job.awaiting_decision or job.preview_expires_utc is None:
            return
        if _now() < job.preview_expires_utc:
            return
        job.awaiting_decision = False
        job.done = True
        job.error = (
            f"the preview was not accepted within "
            f"{int(PREVIEW_TTL.total_seconds() // 60)} minutes, so the job "
            f"ended. Nothing was transcribed and nothing was stored; paste the "
            f"link again to get a fresh preview.")
        job.error_code = "preview_expired"
        job.eta_s = None
        job.eta_basis = None
        job.eta_reason = "expired before the work it would have timed"
        job.finished_utc = _now()
        self._persist(job)

    def poll(self, job_id: str) -> dict[str, Any] | None:
        """The contract's GET response, or None when the id is unknown.

        A finished job keeps answering: the terminal state is held in memory
        and mirrored to disk, so a reload after completion reads the same
        payload rather than a 404.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is not None:
            self._expire_preview(job)
            self._sweep()
            return job.public()
        return self._load(job_id)

    def wait_for(self, job_id: str, timeout: float = 30.0) -> dict[str, Any] | None:
        """Block until the job's current thread finishes. Tests and CLI callers.

        A job has up to two threads across its life -- one for the preview,
        one for the accepted ingest -- and this joins whichever is current, so
        a caller that accepts and then waits gets the ingest's end and not the
        preview's.
        """
        thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)
        return self.poll(job_id)

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Stop a job in flight. Nothing partial is left behind.

        WHAT "NOTHING BEHIND" MEANS HERE, precisely, because the honest
        version is narrower than the slogan:

        * cancelled during ``fetch`` -- no row has been written at all, and
          none ever will be. Guaranteed by ordering, not by cleanup.
        * cancelled during ``transcribe``/``analyse`` -- ``ingest_link`` is a
          single opaque call with no interruption point, so it is allowed to
          finish and the item it wrote is immediately DISCARDED (hidden from
          every read path, nothing deleted). The reader sees nothing, which is
          what a cancel promises; the archive keeps what was fetched, which is
          what the immutability rule requires. ``cancelled_after_write`` says
          which of the two happened, so the answer is never rounded.
        * already finished -- :class:`JobAlreadyFinished`, carrying the state,
          because pretending a completed ingest was cancelled would hide a
          stored item from its owner.
        """
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            state = self._load(job_id)
            if state is None:
                raise UnknownJob(f"no ingest job {job_id!r}")
            raise JobAlreadyFinished(
                f"job {job_id} is no longer running in this process; it "
                f"cannot be cancelled. If it stored an item, discard that "
                f"item by id instead.", state)
        if job.done:
            raise JobAlreadyFinished(
                f"job {job_id} already finished; there is nothing in flight to "
                f"cancel. Discard the item it stored if you want it gone.",
                job.public())
        if job.awaiting_decision:
            # Parked at the preview: aborting is simply declining, and it is
            # clean by construction because nothing has been written.
            return self.decline(job_id, reason="aborted at the preview")
        job.cancel.set()
        job.cancel_requested_utc = _now()
        self._persist(job)
        if not self.background:
            # Inline mode never reaches a checkpoint after this call, so the
            # cancel is applied here rather than reported as pending forever.
            self._cancel(job)
        return job.public()

    # -- the worker ---------------------------------------------------------

    def _checkpoint(self, job: _Job) -> None:
        """Honour a cancel at a point where nothing has been written yet."""
        if job.cancel.is_set():
            self._cancel(job)
            raise _Cancelled(job.job_id)

    def _cancel(self, job: _Job, *, note: str | None = None) -> None:
        job.done = True
        job.awaiting_decision = False
        job.cancelled = True
        job.error = note or (
            "cancelled before anything was written; no item, no transcript "
            "and no claims exist for this url.")
        job.error_code = "cancelled"
        job.item_id = None
        job.eta_s = None
        job.eta_basis = None
        job.eta_reason = "the job was cancelled before the work it would have timed"
        job.finished_utc = _now()
        self._persist(job)

    def _cancel_after_write(self, job: _Job, pre: Preflight) -> None:
        """The cancel landed while the one opaque write was running.

        The ingester has already stored an item. It is discarded, not deleted:
        ``content_claim`` is immutable by the rule at the top of
        ``migrations/content_001_claims.sql``, and a cancel is a statement
        about what the owner wants to SEE, not a licence to rewrite the
        archive. The discard removes it from every read path, so from the
        reader's side the cancel left nothing behind.
        """
        from fpl_edge.ingest.content.urls import youtube_id

        item_id, siblings = _item_for_url(self.db, vid=youtube_id(pre.ingest_url),
                                          url=pre.ingest_url)
        job.cancelled_after_write = True
        if item_id is None:
            self._cancel(job, note=(
                "cancelled during the transcribe/analyse call. No item can be "
                "read back for this url, so nothing was left behind."))
            return
        failures: list[str] = []
        for target in (siblings or (item_id,)):
            try:
                discard_item(self.db, target,
                             reason=f"ingest job {job.job_id} was cancelled "
                                    f"while it was running")
            except Exception as exc:  # noqa: BLE001 - reported, never silent
                failures.append(f"{target}: {type(exc).__name__}: {exc}")
        job.discarded_item_id = item_id
        if failures:
            self._cancel(job, note=(
                f"cancelled during the transcribe/analyse call, which had "
                f"already stored {item_id}. Hiding it FAILED ({'; '.join(failures)}), "
                f"so that item is still visible -- discard it explicitly."))
            return
        self._cancel(job, note=(
            f"cancelled during the transcribe/analyse call, which had already "
            f"finished writing {item_id}. That item is discarded: it is hidden "
            f"from every read path, so nothing from this job is visible. "
            f"Nothing was deleted -- restore it if the cancel was a mistake."))

    def _run(self, job_id: str) -> None:
        """Phase one: one page fetch, then HALT.

        Nothing after this method has run when it returns, unless the URL was
        refused or already stored -- both of which are answers, not work.
        """
        job = self._jobs[job_id]
        try:
            self._run_preview(job)
        except _Cancelled:
            pass  # ``_cancel`` has already written the terminal state
        except Exception as exc:  # noqa: BLE001 - a job must never die silently
            self._fail(job, f"{type(exc).__name__}: {exc}", code="unhandled")

    def _run_after_decision(self, job_id: str) -> None:
        """Phase two: transcribe, analyse, attribute, store. Costs GPU seconds."""
        job = self._jobs[job_id]
        try:
            self._run_stages(job, job.pre)
        except _Cancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - a job must never die silently
            self._fail(job, f"{type(exc).__name__}: {exc}", code="unhandled")

    def _run_preview(self, job: _Job) -> None:
        """Fetch the page, build the preview, and stop.

        THE HALT IS THE FEATURE. The owner's instruction was "look at the
        summary before transcribing -- if it's not relevant why even
        transcribe?", and a 20-minute video is ~105 seconds of local ASR. So
        everything that can be known from the one request the preflight was
        already making is assembled here -- who published it, what they called
        it, when, which gameweek that makes it, what they say it is about, and
        what saying yes would cost -- and then nothing else happens until a
        human says go.

        Two cases skip the halt, because neither is a decision about spending
        anything: a refused URL (there is nothing to transcribe) and a
        duplicate (it is already transcribed).
        """
        pre = self._preflight(job.url, self.db)
        job.pre = pre
        job.title = pre.title
        job.status = pre.status
        job.path = pre.path
        job.path_reason = pre.path_reason
        job.media_seconds = pre.media_seconds
        job.eta_s, basis = pre.eta_s()
        job.eta_basis = basis if job.eta_s is not None else None
        job.eta_reason = None if job.eta_s is not None else basis
        job.creator = pre.creator
        job.creator_basis = pre.creator_basis or None
        job.creator_reason = pre.creator_reason or None
        job.channel = pre.channel
        job.tracked = pre.tracked
        job.gameweek = pre.gameweek
        self._persist(job)

        self._checkpoint(job)

        if pre.refused:
            self._fail(job, pre.reason or "refused", code=pre.refusal)
            return

        if pre.duplicate:
            # The same video under a second URL form. Return the EXISTING item;
            # ingesting again would put a second row (and a second set of
            # claims) behind one publication. No decision is owed: nothing
            # would be transcribed either way.
            job.duplicate_of = pre.sibling_item_ids or (pre.existing_item_id,)
            job.item_id = pre.existing_item_id
            job.note = pre.reason
            self._finish(job, pre)
            return

        self._advance(job, "preview")
        job.preview = pre.preview()
        job.awaiting_decision = True
        job.preview_expires_utc = _now() + PREVIEW_TTL
        job.note = (
            "waiting for a decision. Nothing has been transcribed, analysed or "
            "stored, and declining costs nothing further."
        )
        self._persist(job)

    def _run_stages(self, job: _Job, pre: Preflight) -> None:
        from fpl_edge.ingest.content import asr

        # -- transcribe + analyse -------------------------------------------
        # ``ingest_link`` fuses these two: it transcribes and analyses in one
        # call and there is no callback between them. The stage is advanced to
        # `analyse` on the way out rather than pretending to observe a boundary
        # that does not exist. See the deviation note in the module docstring.
        self._advance(job, "transcribe")
        # THE cancellation boundary. Before this line nothing has been
        # written, so a cancel is clean by construction. `ingest_link` is one
        # opaque call -- there is no callback inside it to poll -- so the flag
        # is read again the instant it returns, and if the owner cancelled
        # while it ran, whatever it wrote is discarded rather than shown.
        self._checkpoint(job)
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

        if job.cancel.is_set():
            self._cancel_after_write(job, pre)
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
        # Phase two REFINES what the preview established; it must not erase it.
        # The gameweek in particular can sharpen from `inferred` to `stated`
        # once the analysed calls exist -- but an ingester that reports nothing
        # leaves the preview's answer standing rather than blanking it.
        for attr in ("creator", "creator_basis", "creator_reason", "channel",
                     "tracked"):
            value = getattr(findings, attr, None)
            if value is not None:
                setattr(job, attr, value)
        resolution = getattr(findings, "gameweek", None)
        if resolution is not None:
            job.gameweek = resolution.public()

        # -- attribute -------------------------------------------------------
        from fpl_edge.ingest.content.urls import youtube_id

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
        job.awaiting_decision = False
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
        job.awaiting_decision = False
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
