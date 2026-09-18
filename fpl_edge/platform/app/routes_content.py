"""Content source state and the per-source fetch, the pasted-link job with its
preview gate, and the three item annotation routes."""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from fpl_edge.platform import link_jobs as link_jobs_mod
from fpl_edge.platform.app.helpers import (
    UTC,
    Deps,
    DiscardRequest,
    GameweekRequest,
    IngestLinkRequest,
    SourceFetchRequest,
)
from fpl_edge.platform.query import read_copy


def _content_router(deps: Deps) -> APIRouter:
    """Content source state and per-source fetch, the pasted-link job with its
    preview gate, and the three item annotation routes."""

    app = deps.app
    db_path = deps.db_path
    router = APIRouter()

    # ---- content sources: honest state, and a per-source manual fetch ----
    # The Creators panel used to split sources into "on the panel" and
    # "excluded", and 25 live feeds were sitting in the second bucket -- 39 of
    # 41 sources had answered HTTP 200 that same day. These two routes replace
    # that word with a measured state per source
    # (fpl_edge/ingest/content/source_state.py) and give the panel a button
    # that fetches exactly one of them and says what came back.
    app.state.source_fetches = {}
    source_fetch_lock = threading.Lock()

    def _source_health(source_key: str, *, window_days: int = 2):
        from fpl_edge.ingest.content import source_state as src_state

        if not db_path.exists():
            return None
        try:
            with read_copy(db_path) as wh:
                rows = src_state.source_states(wh, window_days=window_days)
        except Exception:  # noqa: BLE001 - a read hiccup is not a state
            return None
        for row in rows:
            if row.key == source_key:
                return row
        return None

    def _source_item_stats(source_key: str) -> dict[str, Any]:
        """Items stored for one source right now: the before/after measure."""
        empty = {"n_items": 0, "newest_published": None, "newest_title": None}
        if not db_path.exists():
            return empty
        try:
            with read_copy(db_path) as wh:
                df = wh.sql(
                    "SELECT count(*) n, max(published_at) newest "
                    "FROM content_item WHERE source_key = ?", [source_key])
                n = int(df.iloc[0]["n"])
                title = None
                if n:
                    top = wh.sql(
                        "SELECT title FROM content_item WHERE source_key = ? "
                        "ORDER BY published_at DESC LIMIT 1", [source_key])
                    title = None if top.empty else str(top.iloc[0]["title"])
                newest = df.iloc[0]["newest"]
                import pandas as pd

                ts = pd.to_datetime(newest, utc=True, errors="coerce")
                return {"n_items": n,
                        "newest_published": None if pd.isna(ts) else ts.isoformat(),
                        "newest_title": title}
        except Exception:  # noqa: BLE001
            return empty

    def _source_fetch_state(source_key: str) -> dict[str, Any]:
        return dict(app.state.source_fetches.get(source_key)
                    or {"source_key": source_key, "state": "idle",
                        "detail": None, "run_id": None, "result": None})

    @router.get("/api/content/sources")
    def get_content_sources(window_days: int = 2) -> JSONResponse:
        """Every registered source with ONE measured state, plus the counts.

        Read-only. ``window_days`` is what "recently" means for the live /
        quiet / stale split; the response echoes it so a panel never has to
        guess which window produced the words it is rendering.
        """
        from fpl_edge.ingest.content import source_state as src_state

        window_days = max(1, min(int(window_days), 90))
        states = [
            {"state": str(state), "label": src_state.STATE_LABEL[state],
             "can_fetch": str(state) in src_state.FETCHABLE_STATES}
            for state in src_state.SourceState
        ]
        if not db_path.exists():
            return JSONResponse({
                "as_of": dt.datetime.now(UTC).isoformat(),
                "window_days": window_days, "states": states,
                "counts": {}, "sources": [],
                "reason": f"no warehouse at {db_path}",
            })
        with read_copy(db_path) as wh:
            rows = src_state.source_states(wh, window_days=window_days)
        return JSONResponse({
            "as_of": dt.datetime.now(UTC).isoformat(),
            "window_days": window_days,
            "states": states,
            "counts": src_state.state_counts(rows),
            "sources": [r.to_dict() for r in rows],
        })

    @router.post("/api/content/sources/{source_key}/fetch")
    def post_source_fetch(source_key: str,
                          body: SourceFetchRequest | None = None) -> JSONResponse:
        """Fetch ONE source now, and report what it got.

        The pipelines run-route pattern exactly: a daemon thread does the
        work, the 202 carries a ``run_id``, and the browser polls
        ``fetch_state``. What it runs is ``pipeline ingest --only <key>``,
        the same ingester the scheduled tasks run, so a button press and a
        scheduled fetch leave the same rows and the same probe columns.

        A source that is blocked on policy or has no verified feed is a 409
        with the recorded reason, never a run that quietly does nothing.
        """
        import sys
        import uuid

        from fpl_edge.ingest.content.sources import BY_KEY
        from fpl_edge.pipelines.contracts import run_step

        source = BY_KEY.get(source_key)
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=f"no content source {source_key!r} in the registry; "
                       f"known: {sorted(BY_KEY)}")

        health = _source_health(source_key)
        if health is not None and not health.can_fetch_now:
            return JSONResponse(
                {"detail": f"source {source_key!r} is {health.state}: "
                           f"{health.reason}",
                 "source_key": source_key, "state": health.state,
                 "reason": health.reason, "source": health.to_dict()},
                status_code=409)

        days = max(0, min(int(body.backfill_days if body else 1), 30))
        videos = max(1, min(int(body.max_videos if body else 6), 30))

        with source_fetch_lock:
            current = _source_fetch_state(source_key)
            if current["state"] == "running":
                return JSONResponse(
                    {**current,
                     "detail": f"a fetch of {source_key!r} is already running; "
                               f"poll fetch_state instead of starting a second."},
                    status_code=409)
            run_id = uuid.uuid4().hex
            started_utc = dt.datetime.now(UTC).isoformat()
            before = _source_item_stats(source_key)

            def _run() -> None:
                step = run_step(
                    f"fetch:{source_key}",
                    [sys.executable, "-m", "fpl_edge.ingest.content.pipeline",
                     "--db", str(db_path), "ingest",
                     "--backfill-days", str(days),
                     "--max-videos", str(videos),
                     "--only", source_key],
                    timeout=900,
                )
                after = _source_item_stats(source_key)
                refreshed = _source_health(source_key)
                app.state.source_fetches[source_key] = {
                    "source_key": source_key,
                    "state": "done" if step.ok else "error",
                    "detail": step.detail,
                    "run_id": run_id,
                    "started_utc": started_utc,
                    "finished_utc": dt.datetime.now(UTC).isoformat(),
                    "result": {
                        "ok": step.ok,
                        "seconds": step.seconds,
                        "items_before": before["n_items"],
                        "items_after": after["n_items"],
                        "new_items": max(0, after["n_items"] - before["n_items"]),
                        "newest_published": after["newest_published"],
                        "newest_title": after["newest_title"],
                        "http_status": (None if refreshed is None
                                        else refreshed.last_http_status),
                        "last_error": (None if refreshed is None
                                       else refreshed.last_error),
                        "backfill_days": days,
                    },
                    "source": None if refreshed is None else refreshed.to_dict(),
                }

            app.state.source_fetches[source_key] = {
                "source_key": source_key, "state": "running", "detail": None,
                "run_id": run_id, "result": None,
                "started_utc": started_utc,
            }
            threading.Thread(target=_run, daemon=True,
                             name=f"source-fetch-{source_key}").start()
        return JSONResponse({"started": True, "source_key": source_key,
                             "run_id": run_id, "state": "running",
                             "backfill_days": days}, status_code=202)

    @router.get("/api/content/sources/{source_key}/fetch_state")
    def get_source_fetch_state(source_key: str) -> JSONResponse:
        from fpl_edge.ingest.content.sources import BY_KEY

        if source_key not in BY_KEY:
            raise HTTPException(
                status_code=404,
                detail=f"no content source {source_key!r} in the registry")
        state = _source_fetch_state(source_key)
        if state.get("source") is None:
            health = _source_health(source_key)
            state["source"] = None if health is None else health.to_dict()
        return JSONResponse(state)

    # ---- paste a link (fpl_edge/platform/link_jobs.py) ----
    # The corpus-writing route on this server, and it writes exactly one way:
    # through the owner-initiated ingester in interfaces/creators.py. The job
    # runs server-side and the browser polls, so closing the tab mid-transcribe
    # loses nothing; state is keyed by job_id and never held in a request. The
    # item annotation routes further down also write, but only to the paste
    # flow's own ledger table -- they never touch the archive or a claim.
    app.state.link_jobs = link_jobs_mod.LinkJobs(db_path)

    @router.post("/api/ingest/link")
    def post_ingest_link(body: IngestLinkRequest) -> JSONResponse:
        # Only an unusable REQUEST is a 4xx here. A URL that turns out to be a
        # league invite, a duplicate or a 403 is a real job with a real answer,
        # and it surfaces through the poll's `error` -- one shape for the UI to
        # render instead of two.
        try:
            started = app.state.link_jobs.submit(body.url)
        except link_jobs_mod.LinkRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(started, status_code=202)

    @router.get("/api/ingest/link/{job_id}")
    def get_ingest_link(job_id: str) -> JSONResponse:
        state = app.state.link_jobs.poll(job_id)
        if state is None:
            raise HTTPException(status_code=404,
                                detail=f"no ingest job {job_id!r}")
        return JSONResponse(state)

    # ---- the preview gate: decide BEFORE anything is transcribed ----
    # POST /api/ingest/link now returns after one page fetch and parks at
    # stage "preview" with `awaiting_decision: true` and a `preview` payload.
    # Accept spends the GPU seconds; decline spends nothing. DELETE is the
    # single abort verb: it declines a parked job and cancels a running one.

    @router.post("/api/ingest/link/{job_id}/accept")
    def post_accept_link(job_id: str) -> JSONResponse:
        """Go ahead and transcribe. The only call that costs GPU seconds."""
        try:
            return JSONResponse(app.state.link_jobs.accept(job_id),
                                status_code=202)
        except link_jobs_mod.UnknownJob as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (link_jobs_mod.NotAwaitingDecision,
                link_jobs_mod.JobAlreadyFinished) as exc:
            return JSONResponse({"detail": str(exc), "job": exc.state},
                                status_code=409)

    @router.post("/api/ingest/link/{job_id}/decline")
    def post_decline_link(job_id: str, body: DiscardRequest | None = None) -> JSONResponse:
        """Say no at the preview. Nothing was written, so nothing is undone."""
        try:
            return JSONResponse(app.state.link_jobs.decline(
                job_id, reason=(body.reason if body else "")))
        except link_jobs_mod.UnknownJob as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except link_jobs_mod.JobAlreadyFinished as exc:
            return JSONResponse({"detail": str(exc), "job": exc.state},
                                status_code=409)

    @router.delete("/api/ingest/link/{job_id}")
    def delete_ingest_link(job_id: str) -> JSONResponse:
        """Abort. Declines a parked job; cancels a running one."""
        try:
            return JSONResponse(app.state.link_jobs.cancel(job_id))
        except link_jobs_mod.UnknownJob as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except link_jobs_mod.JobAlreadyFinished as exc:
            # 409, not 200: the caller asked to stop something that had already
            # stopped, and the item it produced is still there. Saying
            # "cancelled" would hide a stored row from its owner.
            return JSONResponse({"detail": str(exc), "job": exc.state},
                                status_code=409)

    @router.post("/api/content/items/{item_id}/discard")
    def post_discard_item(item_id: str, body: DiscardRequest | None = None) -> JSONResponse:
        """Hide an item that turned out to be irrelevant. Deletes nothing.

        The second writing route on this server, and it writes one UPDATE to
        ``user_link_item``. It does not touch ``content_item``,
        ``transcript_segment``, ``content_analysis`` or ``content_claim``:
        those are the archive and a claim is an utterance that cannot be
        un-made. ``restore`` is a real inverse for exactly that reason.
        """
        try:
            return JSONResponse(link_jobs_mod.discard_item(
                db_path, item_id, reason=(body.reason if body else "")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/content/items/{item_id}/restore")
    def post_restore_item(item_id: str, body: DiscardRequest | None = None) -> JSONResponse:
        try:
            return JSONResponse(link_jobs_mod.restore_item(
                db_path, item_id, reason=(body.reason if body else "")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/content/items/{item_id}/gameweek")
    def post_item_gameweek(item_id: str, body: GameweekRequest) -> JSONResponse:
        """Correct the gameweek an item is about.

        Recorded as a correction, never as a silent overwrite: the prior value
        is kept in ``gw_corrected_from``, the publish-date inference stays in
        ``gw_inferred``, and ``gw_basis`` becomes ``"corrected"`` so no reader
        can mistake a hand-entered week for a derived one.
        """
        try:
            return JSONResponse(link_jobs_mod.correct_gameweek(
                db_path, item_id, body.gameweek, note=body.note))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
