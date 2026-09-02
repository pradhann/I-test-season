"""The platform HTTP surface — DESIGN.md §2.1, implemented exactly.

    GET  /api/panels                  registered panels + their pinned scripts
    POST /api/scripts/{name}/run      {result, provenance}, 10s soft budget
    POST /api/query                   guarded read-only SQL
    GET  /api/inbox                   deliveries, newest first
    POST /api/inbox/{id}/ack          acknowledge one
    GET  /api/monitors                monitor definitions
    POST /api/ingest/link             {url} -> {job_id, stages}
    GET  /api/ingest/link/{job_id}    {stage, pct, eta_s, done, error, item_id}
    POST /api/ingest/link/{job_id}/accept   transcribe it (the only GPU spend)
    POST /api/ingest/link/{job_id}/decline  say no at the preview; nothing stored
    DELETE /api/ingest/link/{job_id}  abort: declines if parked, cancels if running
    POST /api/content/items/{id}/discard   hide an ingested item (deletes nothing)
    POST /api/content/items/{id}/restore   put it back
    POST /api/content/items/{id}/gameweek  correct the gameweek, by hand, on record
    POST /api/conversations           new agent conversation -> {conv_id}
    GET  /api/conversations           conversation metas, newest first
    POST /api/conversations/{id}/chat start an agent turn (202; 409 in-flight)
    GET  /api/conversations/{id}/stream  SSE: replay > after, then live
    GET  /api/conversations/{id}/events  JSON page (non-SSE fallback)
    POST /api/conversations/{id}/stop kill the in-flight turn
    GET  /api/chat/assets/{id}.{png|svg}  charts the agent's python_viz produced
    POST /api/players/{code}/fetch_profile  start the on-demand Understat fetch (202)
    GET  /api/players/{code}/fetch_profile  its state: idle|running|done|error
    POST /api/pipelines/{task_id}/run   trigger one registry pipeline (202);
                                        metered without confirm -> needs_confirm
    GET  /api/pipelines/{task_id}/run_state  the poller: state + latest ledger row
    GET  /api/briefing                model-authored salience artefact + freshness;
                                      404-shaped JSON when it does not exist yet
    /                                 the built web/ bundle, if present

What is deliberately *absent* is as load-bearing as what is here: no route
takes SQL from a panel and no route accepts credentials. Exactly one route
writes to the CORPUS -- ``POST /api/ingest/link``, which is the owner pasting a
link at their own explicit request, and it writes only by calling
:func:`fpl_edge.interfaces.creators.ingest_link`, the sanctioned single-URL
path that the robots exception in ``docs/data_sources.md`` §7A was granted for.
It opens no warehouse handle of its own and adds no second ingester; see
:mod:`fpl_edge.platform.link_jobs`. The server holds no API key -- the chat escalation
shells out to the Max-plan `claude` CLI, which owns its own auth (DESIGN §2
item 6), and every secret the engine needs is read through
:func:`fpl_edge.config.secret` at the point of use.

Binding: loopback only, by default and in the plist. This is a single-operator
local platform with no auth layer; a server that answers on 0.0.0.0 would put
an unauthenticated SQL endpoint on the local network.
"""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Importing the scripts package is what registers the five panel scripts.
import fpl_edge.platform.scripts  # noqa: F401
from fpl_edge.platform import inbox as inbox_mod
from fpl_edge.platform import link_jobs as link_jobs_mod
from fpl_edge.platform import panels as panels_mod
from fpl_edge.platform.query import QueryError, guarded_query, read_copy
from fpl_edge.platform.registry import (
    ParamsInvalid,
    ResultInvalid,
    ScriptError,
    repo_sha,
    run_script,
)
from fpl_edge.platform.registry import (
    describe_all as describe_scripts,
)
from fpl_edge.store.warehouse import DEFAULT_DB

UTC = dt.UTC

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class RunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)

    # Argus's run tool takes params at the top level and so does the JS client
    # that will call this. Accepting both shapes costs one method and removes a
    # class of "why is my panel empty" that is really a wrapper mismatch.
    model_config = {"extra": "allow"}

    def resolved(self) -> dict[str, Any]:
        if self.params:
            return self.params
        extra = dict(self.__pydantic_extra__ or {})
        return extra


class QueryRequest(BaseModel):
    sql: str
    as_of: dt.datetime | None = None
    max_rows: int | None = None


class TurnRequest(BaseModel):
    text: str


class SolveRequest(BaseModel):
    mode: str = "both"


class IngestLinkRequest(BaseModel):
    url: str


class DiscardRequest(BaseModel):
    """Why an item is being hidden. Optional, and recorded when given."""

    reason: str = ""


class GameweekRequest(BaseModel):
    """The owner's gameweek for an item, recorded AS A CORRECTION."""

    gameweek: int
    note: str = ""


#: The season the profile-fetch route defaults to; one string, next to the
#: request model that carries it, rather than a fourth copy of the literal.
SEASON_DEFAULT = "2026-27"


class FetchProfileRequest(BaseModel):
    """Which season to fetch a player's Understat profile for."""

    season: str = SEASON_DEFAULT


class PipelineRunRequest(BaseModel):
    """Confirmation for a metered pipeline trigger. Free tasks ignore it."""

    confirm: bool = False


def create_app(db: Path | str = DEFAULT_DB,
               chat_root: Path | str | None = None) -> FastAPI:
    """Build the app. ``db`` is injectable so tests can seed a tmp warehouse;
    ``chat_root`` likewise for the agent conversation store."""
    from fpl_edge.platform.chat_agent import (
        CHAT_ROOT,
        ChatAgent,
        ChatAgentError,
        TurnInFlight,
        UnknownConversation,
    )

    db_path = Path(db)
    chat_agent = ChatAgent(root=Path(chat_root) if chat_root else CHAT_ROOT)
    app = FastAPI(
        title="i-test platform",
        version="1.0",
        description="Single-operator FPL decision platform. Panels, one guarded "
                    "query path, inbox, chat.",
    )
    # Exposed for tests, which point the agent at a fake CLI script; the
    # routes below close over the same object.
    app.state.chat_agent = chat_agent

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "repo_sha": repo_sha(),
            "warehouse": str(db_path),
            "warehouse_present": db_path.exists(),
            "now": dt.datetime.now(UTC).isoformat(),
        }

    @app.get("/api/deadline")
    def deadline() -> dict[str, Any]:
        """The next deadline, from dim_event -- the page's clock has no
        business hardcoding a date that passes."""
        try:
            with read_copy(db_path) as wh:
                now = dt.datetime.now(UTC)
                row = wh.sql(
                    "SELECT season, gw, max(deadline_utc) AS deadline_utc "
                    "FROM dim_event WHERE deadline_utc > ? "
                    "GROUP BY season, gw ORDER BY deadline_utc LIMIT 1",
                    [now],
                )
                if row.empty:
                    return {"deadline_utc": None,
                            "reason": "no future deadline in dim_event"}
                r = row.iloc[0]
                return {
                    "season": str(r["season"]), "gw": int(r["gw"]),
                    "deadline_utc": r["deadline_utc"].isoformat(),
                }
        except Exception as exc:  # noqa: BLE001 - the clock is decoration, panels matter
            return {"deadline_utc": None, "reason": f"{type(exc).__name__}: {exc}"}

    @app.get("/api/panels")
    def get_panels() -> dict[str, Any]:
        return {
            "panels": panels_mod.describe_all(),
            "scripts": describe_scripts(),
            "repo_sha": repo_sha(),
        }

    @app.post("/api/scripts/{name}/run")
    def post_run_script(name: str, body: RunRequest | None = None) -> JSONResponse:
        params = body.resolved() if body is not None else {}
        try:
            run = run_script(name, params, db=db_path)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ParamsInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ResultInvalid as exc:
            # The script broke its own contract. That is a 500: the caller did
            # nothing wrong and must not be led to think the shape is theirs to
            # fix.
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except ScriptError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse(run.to_dict())

    @app.post("/api/query")
    def post_query(body: QueryRequest) -> JSONResponse:
        try:
            result = guarded_query(
                body.sql,
                as_of=body.as_of.astimezone(UTC) if body.as_of else None,
                db=db_path,
                **({"max_rows": body.max_rows} if body.max_rows else {}),
            )
        except QueryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"{type(exc).__name__}: {exc}"
            ) from exc
        payload = result.to_dict()
        payload["provenance"] = {"repo_sha": repo_sha(),
                                 "generated_at": dt.datetime.now(UTC).isoformat()}
        return JSONResponse(payload)

    @app.get("/api/inbox")
    def get_inbox(limit: int = 50, include_acked: bool = False) -> JSONResponse:
        return JSONResponse(
            inbox_mod.list_deliveries(db=db_path, limit=limit,
                                      include_acked=include_acked)
        )

    @app.post("/api/inbox/{delivery_id}/ack")
    def post_ack(delivery_id: str) -> JSONResponse:
        result = inbox_mod.ack(delivery_id, db=db_path)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail=result.get("reason", "not found"))
        return JSONResponse(result)

    @app.get("/api/monitors")
    def get_monitors() -> dict[str, Any]:
        return _monitor_definitions(db_path)

    @app.post("/api/monitors/{name}/run")
    def post_monitor_run(name: str) -> JSONResponse:
        # Deliberately not implemented rather than quietly missing. Monitor
        # tasks are minutes of compute that take the write lock and send
        # Telegram messages; firing one from a browser needs the DAG's
        # idempotency and claim machinery, which lives in the job runner and is
        # owned there. Running it here would double-send.
        raise HTTPException(
            status_code=501,
            detail=(
                f"Manual evaluation of {name!r} is not exposed over HTTP. Monitor "
                f"tasks take the DuckDB write lock, can run for minutes and send "
                f"Telegram messages; their idempotent firing record lives in the "
                f"deadline DAG. Run `uv run python -m fpl_edge.jobs.deadline_dag "
                f"--task {name} --force` instead, so the firing is recorded once."
            ),
        )

    @app.post("/api/solve")
    def post_solve(body: SolveRequest | None = None) -> JSONResponse:
        # Unlike a monitor (the 501 above), the solve is safe to fire from a
        # browser: the CLI subprocess owns its own locking and artefacts, the
        # runner enforces one-at-a-time, and nothing here double-sends.
        mode = (body.mode if body is not None else "both").strip().lower()
        from fpl_edge.platform import solve_runner

        if mode not in solve_runner.MODES:
            raise HTTPException(
                status_code=400,
                detail=f"mode must be one of {list(solve_runner.MODES)}",
            )
        return JSONResponse(solve_runner.start(mode))

    @app.get("/api/solve/status")
    def get_solve_status() -> JSONResponse:
        from fpl_edge.platform import solve_runner

        return JSONResponse(solve_runner.status())

    @app.get("/api/solve/plan")
    def get_solve_plan() -> JSONResponse:
        return JSONResponse(_solve_plan(db_path))

    # ---- on-demand Understat profile fetch (CHAT_ARCHITECTURE §6) ----
    # The async-on-click half of the player profile: the panel only ever READS
    # the warehouse, so an absent profile is filled by this route calling the
    # one sanctioned fetch path (fpl_edge/ingest/understat.py) in a background
    # thread while the drawer polls the panel. State is in-process and honest:
    # a fetch that failed says so with the ingest's own words (including the
    # strict resolver's refusal listing its candidates), never a silent "idle".
    app.state.profile_fetches = {}
    profile_fetch_lock = threading.Lock()

    def _profile_fetch_state(code: int) -> dict[str, Any]:
        return dict(app.state.profile_fetches.get(int(code))
                    or {"code": int(code), "state": "idle", "detail": None})

    @app.post("/api/players/{code}/fetch_profile")
    def post_fetch_profile(code: int,
                           body: FetchProfileRequest | None = None) -> JSONResponse:
        season = (body.season if body is not None else SEASON_DEFAULT).strip()

        def _run() -> None:
            # Imported here, not at module top: the route must exist even if
            # the ingest module breaks, and tests monkeypatch this attribute.
            try:
                from fpl_edge.ingest import understat as understat_mod

                summary = understat_mod.fetch_player_profile(
                    int(code), season, db=db_path)
                app.state.profile_fetches[int(code)] = {
                    "code": int(code), "state": "done", "detail": None,
                    "summary": summary,
                }
            except Exception as exc:  # noqa: BLE001 - reported verbatim to the poller
                app.state.profile_fetches[int(code)] = {
                    "code": int(code), "state": "error",
                    "detail": f"{type(exc).__name__}: {exc}",
                }

        with profile_fetch_lock:
            current = _profile_fetch_state(code)
            if current["state"] == "running":
                # Idempotent: the button being clicked twice is one fetch.
                return JSONResponse(current, status_code=202)
            app.state.profile_fetches[int(code)] = {
                "code": int(code), "state": "running", "detail": None,
                "started_utc": dt.datetime.now(UTC).isoformat(),
            }
            threading.Thread(target=_run, daemon=True,
                             name=f"understat-fetch-{code}").start()
        return JSONResponse(_profile_fetch_state(code), status_code=202)

    @app.get("/api/players/{code}/fetch_profile")
    def get_fetch_profile(code: int) -> JSONResponse:
        return JSONResponse(_profile_fetch_state(code))

    # ---- pipeline triggers (PIPELINES.md §5 decision 4, §6.4) ----
    # The write half of the Pipelines panel. The panel scripts only READ the
    # ledger; this pair of routes is the one way a browser starts a run, and
    # it starts it through runner.run_task -- the same seam the CLI uses, so
    # a UI run and a CLI run leave identical records. The fetch_profile
    # pattern: work in a daemon thread, in-process state, errors verbatim to
    # the poller. A metered task without confirm:true costs nothing and
    # returns the numbers the confirm strip renders.
    app.state.pipeline_runs = {}
    pipeline_run_lock = threading.Lock()

    def _pipeline_run_state(task_id: str) -> dict[str, Any]:
        return dict(app.state.pipeline_runs.get(task_id)
                    or {"task_id": task_id, "state": "idle", "detail": None,
                        "run_id": None})

    def _serialize_ledger_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        import pandas as pd

        def _iso(v):
            ts = pd.to_datetime(v, utc=True, errors="coerce")
            return None if pd.isna(ts) else ts.isoformat()

        def _num(v, cast):
            return None if v is None or pd.isna(v) else cast(v)

        return {
            "run_id": str(row.get("run_id")),
            "status": row.get("status"),
            "started": _iso(row.get("started_utc")),
            "finished": _iso(row.get("finished_utc")),
            "rows_written": _num(row.get("rows_written"), int),
            "rows_unchanged": _num(row.get("rows_unchanged"), int),
            "credits": _num(row.get("credits_spent"), float),
            "note": row.get("note"),
            "trigger": row.get("trigger"),
        }

    def _scheduler_is_running(task_id: str) -> bool:
        """A dag_firing row claimed and still 'running' means the scheduler
        owns this task right now; a UI trigger on top would contend for the
        same locks and double the work. An unreadable warehouse does NOT
        block the trigger -- the runner itself is the final arbiter."""
        if not db_path.exists():
            return False
        try:
            with read_copy(db_path) as wh:
                exists = wh.sql(
                    "SELECT count(*) AS n FROM information_schema.tables "
                    "WHERE table_name = 'dag_firing'").iloc[0]["n"]
                if not exists:
                    return False
                n = wh.sql(
                    "SELECT count(*) AS n FROM dag_firing "
                    "WHERE task = ? AND outcome = 'running'", [task_id])
                return int(n.iloc[0]["n"]) > 0
        except Exception:  # noqa: BLE001 - a read hiccup must not veto a trigger
            return False

    @app.post("/api/pipelines/{task_id}/run")
    def post_pipeline_run(task_id: str,
                          body: PipelineRunRequest | None = None) -> JSONResponse:
        import uuid

        from fpl_edge.pipelines import health as pipe_health
        from fpl_edge.pipelines import registry as pipe_registry
        from fpl_edge.pipelines import runner as pipe_runner

        task = pipe_registry.by_id(task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail=f"no pipeline {task_id!r} in the registry; known: "
                       f"{sorted(t.id for t in pipe_registry.TASKS)}")
        if not task.enabled:
            return JSONResponse(
                {"detail": f"pipeline {task_id!r} is disabled in the registry; "
                           f"disabling there must mean disabled everywhere, "
                           f"so the trigger refuses rather than overriding it."},
                status_code=409)

        confirm = bool(body.confirm) if body is not None else False
        if task.confirm_required and not confirm:
            # The confirm gate: nothing runs, nothing is spent. The response
            # carries exactly what the inline confirm strip renders -- the
            # estimate and this month's ledger spend, with its cap.
            month_spend = None
            try:
                with read_copy(db_path) as wh:
                    month_spend = pipe_health.month_credits(wh, task_id)
            except Exception:  # noqa: BLE001
                month_spend = None      # the strip renders "spend unknown"
            from fpl_edge.ingest.odds import FREE_TIER_MONTHLY_CREDITS
            return JSONResponse({
                "needs_confirm": True,
                "task_id": task_id,
                "credits_estimate": task.credits_estimate,
                "month_spend": month_spend,
                "month_cap": float(FREE_TIER_MONTHLY_CREDITS),
            })

        with pipeline_run_lock:
            current = _pipeline_run_state(task_id)
            if current["state"] == "running":
                return JSONResponse(
                    {**current,
                     "detail": f"pipeline {task_id!r} already has a running "
                               f"firing (started by this server); poll "
                               f"run_state instead of starting a second one."},
                    status_code=409)
            if _scheduler_is_running(task_id):
                return JSONResponse(
                    {"detail": f"pipeline {task_id!r} has a dag_firing row "
                               f"claimed and still running -- the scheduler "
                               f"owns it right now.",
                     "task_id": task_id, "state": "running", "run_id": None},
                    status_code=409)

            run_id = uuid.uuid4().hex

            def _run() -> None:
                try:
                    outcome = pipe_runner.run_task(
                        task_id, db_path=db_path, trigger="ui", run_id=run_id)
                    app.state.pipeline_runs[task_id] = {
                        "task_id": task_id,
                        "state": "error" if outcome.record.status == "error" else "done",
                        "detail": outcome.result.detail,
                        "run_id": run_id,
                        "status": outcome.record.status,
                    }
                except Exception as exc:  # noqa: BLE001 - verbatim to the poller
                    app.state.pipeline_runs[task_id] = {
                        "task_id": task_id, "state": "error",
                        "detail": f"{type(exc).__name__}: {exc}",
                        "run_id": run_id, "status": "error",
                    }

            app.state.pipeline_runs[task_id] = {
                "task_id": task_id, "state": "running", "detail": None,
                "run_id": run_id,
                "started_utc": dt.datetime.now(UTC).isoformat(),
            }
            threading.Thread(target=_run, daemon=True,
                             name=f"pipeline-run-{task_id}").start()
        return JSONResponse({"started": True, "task_id": task_id,
                             "run_id": run_id}, status_code=202)

    @app.get("/api/pipelines/{task_id}/run_state")
    def get_pipeline_run_state(task_id: str) -> JSONResponse:
        from fpl_edge.pipelines import registry as pipe_registry

        if pipe_registry.by_id(task_id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"no pipeline {task_id!r} in the registry")
        state = _pipeline_run_state(task_id)
        latest = None
        if db_path.exists():
            try:
                # A direct read, NOT fetch_ledger.last_run: that helper runs
                # ensure_table (a CREATE), and the read copy is attached
                # read-only -- exactly as a poller's read should be.
                with read_copy(db_path) as wh:
                    exists = wh.sql(
                        "SELECT count(*) AS n FROM information_schema.tables "
                        "WHERE table_name = 'fetch_run'").iloc[0]["n"]
                    if exists:
                        df = wh.sql(
                            "SELECT * FROM fetch_run WHERE pipeline = ? "
                            "ORDER BY started_utc DESC LIMIT 1", [task_id])
                        if not df.empty:
                            latest = df.iloc[0].to_dict()
            except Exception as exc:  # noqa: BLE001 - the state is still useful
                state["ledger_note"] = f"could not read the ledger: {type(exc).__name__}: {exc}"
        state["last_run"] = _serialize_ledger_row(latest)
        return JSONResponse(state)

    # ---- the intelligence briefing (fpl_edge/platform/briefing_intel.py) --
    # Read-only: serves the model-authored salience artefact plus freshness.
    # A missing artefact is 404-shaped JSON, never an exception, so the UI
    # renders the gap and offers the trigger; generation itself goes through
    # POST /api/pipelines/briefing_intel/run — the same seam as every task,
    # so a UI-triggered briefing leaves the same ledger row a scheduled one
    # does. No POST here on purpose.

    @app.get("/api/briefing")
    def get_briefing() -> JSONResponse:
        from fpl_edge.platform import briefing_intel

        return JSONResponse(briefing_intel.briefing_response(db_path))

    # ---- paste a link (fpl_edge/platform/link_jobs.py) ----
    # The corpus-writing route on this server, and it writes exactly one way:
    # through the owner-initiated ingester in interfaces/creators.py. The job
    # runs server-side and the browser polls, so closing the tab mid-transcribe
    # loses nothing; state is keyed by job_id and never held in a request. The
    # item annotation routes further down also write, but only to the paste
    # flow's own ledger table -- they never touch the archive or a claim.
    app.state.link_jobs = link_jobs_mod.LinkJobs(db_path)

    @app.post("/api/ingest/link")
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

    @app.get("/api/ingest/link/{job_id}")
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

    @app.post("/api/ingest/link/{job_id}/accept")
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

    @app.post("/api/ingest/link/{job_id}/decline")
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

    @app.delete("/api/ingest/link/{job_id}")
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

    @app.post("/api/content/items/{item_id}/discard")
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

    @app.post("/api/content/items/{item_id}/restore")
    def post_restore_item(item_id: str, body: DiscardRequest | None = None) -> JSONResponse:
        try:
            return JSONResponse(link_jobs_mod.restore_item(
                db_path, item_id, reason=(body.reason if body else "")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/content/items/{item_id}/gameweek")
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

    # ---- agent conversations (fpl_edge/platform/chat_agent.py) ----
    # The router fast-path above stays untouched; these routes are the
    # explicit escalation. Argus discipline: the turn runs server-side to
    # completion, events are persisted then broadcast, and /stream replays
    # from any seq so a reload re-attaches mid-turn.

    @app.post("/api/conversations")
    def post_conversation() -> JSONResponse:
        meta = chat_agent.create_conversation()
        return JSONResponse({"conv_id": meta["conv_id"], "meta": meta})

    @app.get("/api/conversations")
    def get_conversations() -> JSONResponse:
        return JSONResponse({"conversations": chat_agent.list_conversations()})

    @app.post("/api/conversations/{conv_id}/chat")
    def post_conversation_chat(conv_id: str, body: TurnRequest) -> JSONResponse:
        try:
            started = chat_agent.start_turn(conv_id, body.text)
        except TurnInFlight as exc:
            return JSONResponse(
                {"detail": str(exc), **chat_agent.running(conv_id)},
                status_code=409,
            )
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ChatAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(started, status_code=202)

    @app.get("/api/conversations/{conv_id}/stream")
    def get_conversation_stream(conv_id: str, after: int = -1, once: int = 0):
        """SSE. ``once=1`` replays and closes (curl/tests); default follows
        live with comment heartbeats, indefinitely."""
        from sse_starlette.sse import EventSourceResponse

        try:
            stream = chat_agent.subscribe(conv_id, after=after,
                                          follow=not once)
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return EventSourceResponse(stream)

    @app.get("/api/conversations/{conv_id}/events")
    def get_conversation_events(conv_id: str, after: int = -1) -> JSONResponse:
        try:
            events = chat_agent.events(conv_id, after=after)
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({
            "events": events,
            **chat_agent.running(conv_id),
            "meta": chat_agent.meta(conv_id),
        })

    @app.post("/api/conversations/{conv_id}/stop")
    def post_conversation_stop(conv_id: str) -> JSONResponse:
        try:
            return JSONResponse(chat_agent.stop(conv_id))
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/chat/assets/{asset_id}.{ext}")
    def get_chat_asset(asset_id: str, ext: str):
        from fastapi.responses import FileResponse

        # python_viz writes SVG alongside PNG; SVG is preferred by the chat
        # sub-app (crisp at any zoom, themeable), PNG stays for the legacy
        # pane and image fallbacks. Anything else 404s -- the extension is
        # part of the allowlist, exactly like the hex id.
        if ext not in ("png", "svg"):
            raise HTTPException(status_code=404, detail="no such asset")
        path = chat_agent.asset_path(asset_id, ext=ext)
        if path is None:
            raise HTTPException(status_code=404, detail="no such asset")
        media = "image/png" if ext == "png" else "image/svg+xml"
        return FileResponse(path, media_type=media)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(WEB_DIST), html=True), name="web")
    else:
        @app.get("/")
        def no_bundle() -> dict[str, Any]:
            return {
                "ok": True,
                "ui": "not built",
                "detail": (
                    f"No bundle at {WEB_DIST}. The API is fully usable without it: "
                    f"GET /api/panels, POST /api/scripts/{{name}}/run, POST /api/query, "
                    f"GET /api/inbox, GET /api/monitors, POST /api/chat. "
                    f"Interactive docs at /docs."
                ),
                "repo_sha": repo_sha(),
            }

    return app


def _monitor_definitions(db_path: Path) -> dict[str, Any]:
    """Monitor definitions read from the DAG, which owns the schedule.

    Read-only reflection over :mod:`fpl_edge.jobs.deadline_dag`: the offsets
    ARE the schedule spec (DESIGN §3), so restating them here would create a
    second source of truth that silently disagrees the first time one moves.
    """
    try:
        from fpl_edge.jobs import deadline_dag as dag
    except Exception as exc:  # noqa: BLE001
        return {"monitors": [], "empty": True,
                "reason": f"deadline DAG not importable: {type(exc).__name__}: {exc}"}

    monitors = []
    for name, offset in getattr(dag, "DEADLINE_OFFSETS", {}).items():
        hours = offset.total_seconds() / 3600.0
        monitors.append({
            "name": name,
            "kind": "alert",
            "trigger": "deadline-relative",
            "schedule": f"T-{hours:g}h before each gameweek deadline",
            "offset_hours": hours,
            "doc": (getattr(dag.TASKS.get(name), "__doc__", "") or "").strip().split("\n")[0],
        })
    nightly = getattr(dag, "NIGHTLY_TASK", None)
    if nightly:
        hour = getattr(dag, "NIGHTLY_LOCAL_HOUR", 2)
        monitors.append({
            "name": nightly,
            "kind": "alert",
            "trigger": "wall-clock",
            "schedule": f"nightly {hour:02d}:00 UK",
            "offset_hours": None,
            "doc": (getattr(dag.TASKS.get(nightly), "__doc__", "") or "").strip().split("\n")[0],
        })

    firings: list[dict[str, Any]] = []
    reason = None
    if db_path.exists():
        try:
            from fpl_edge.platform.query import read_copy

            with read_copy(db_path) as wh:
                exists = wh.sql(
                    "SELECT count(*) AS n FROM information_schema.tables "
                    "WHERE table_name = 'dag_firing'"
                ).iloc[0]["n"]
                if exists:
                    df = wh.sql(
                        "SELECT task, due_utc, outcome, detail, ran_utc FROM dag_firing "
                        "ORDER BY due_utc DESC LIMIT 20"
                    )
                    firings = [
                        {k: (None if v is None else str(v)) for k, v in row.items()}
                        for row in df.to_dict(orient="records")
                    ]
                else:
                    reason = "no dag_firing table yet; the DAG has never ticked."
        except Exception as exc:  # noqa: BLE001
            reason = f"could not read firings: {type(exc).__name__}: {exc}"

    return {
        "monitors": monitors,
        "recent_firings": firings,
        "empty": not monitors,
        "reason": reason,
        "note": (
            "Definitions are read from fpl_edge.jobs.deadline_dag, which owns the "
            "schedule. Triggers are deterministic Python; an LLM only polishes copy "
            "after a firing and never decides one."
        ),
    }


#: Anchored to the repo root like squad_section.PLAN_PATH -- a relative path
#: made that section's plan "missing" whenever the process ran from another
#: directory, and this route must not re-learn that lesson.
_PLAN_PATH = Path(__file__).resolve().parents[2] / "data" / "warehouse" / "gw1_plan.json"


def _solve_plan(db_path: Path) -> dict[str, Any]:
    """The persisted solve artefact, with enough context to render it honestly.

    The file (written by ``fpl solve --commit``) carries player *codes* only,
    so names/positions/prices are resolved here from the warehouse -- through a
    read copy, never a writable handle. The response also carries the next open
    gameweek so the client can say "this plan targets a past deadline" instead
    of rendering a stale squad as current, and the rank-vs-points DIFF block
    recovered from the most recent solve log (the artefact persists one plan;
    the diff of the two objectives exists only in the solve's own output).
    """
    import json

    if not _PLAN_PATH.exists():
        return {
            "exists": False,
            "reason": f"no plan artefact at {_PLAN_PATH.name}; run a solve first.",
        }
    try:
        plan = json.loads(_PLAN_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": False,
                "reason": f"plan artefact unreadable: {type(exc).__name__}: {exc}"}

    players: dict[str, Any] = {}
    next_gw = None
    reason = None
    if db_path.exists():
        try:
            with read_copy(db_path) as wh:
                season = plan.get("season")
                df = wh.sql(
                    """
                    SELECT p.code, p.web_name, p.position, t.short_name AS team,
                           s.price_tenths
                    FROM (
                        SELECT * EXCLUDE (rn) FROM (
                            SELECT *, row_number() OVER (PARTITION BY season, code
                                                         ORDER BY as_of DESC) rn
                            FROM dim_player WHERE season = ?
                        ) WHERE rn = 1
                    ) p
                    LEFT JOIN (
                        SELECT * EXCLUDE (rn) FROM (
                            SELECT *, row_number() OVER (PARTITION BY season, code
                                                         ORDER BY as_of DESC) rn
                            FROM fact_player_state WHERE season = ?
                        ) WHERE rn = 1
                    ) s USING (season, code)
                    LEFT JOIN (
                        SELECT * EXCLUDE (rn) FROM (
                            SELECT *, row_number() OVER (PARTITION BY season, team_code
                                                         ORDER BY as_of DESC) rn
                            FROM dim_team WHERE season = ?
                        ) WHERE rn = 1
                    ) t ON t.team_code = p.team_code
                    """,
                    [season, season, season],
                )
                pos_name = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
                wanted = {int(c) for c in plan.get("gw1", {}).get("squad", [])}
                for row in df.to_dict(orient="records"):
                    code = int(row["code"])
                    if wanted and code not in wanted:
                        continue
                    price = row.get("price_tenths")
                    players[str(code)] = {
                        "name": str(row.get("web_name") or code),
                        "pos": pos_name.get(int(row["position"]) if row.get("position") is not None else 0, "?"),
                        "team": None if row.get("team") is None else str(row["team"]),
                        "price": None if price is None or price != price else round(float(price) / 10.0, 1),
                    }
                gw_row = wh.sql(
                    "SELECT gw FROM dim_event WHERE deadline_utc > ? "
                    "ORDER BY deadline_utc LIMIT 1",
                    [dt.datetime.now(UTC)],
                )
                if not gw_row.empty:
                    next_gw = int(gw_row.iloc[0]["gw"])
        except Exception as exc:  # noqa: BLE001 - names are a nicety, the plan is the payload
            reason = f"could not resolve names: {type(exc).__name__}: {exc}"
    else:
        reason = f"no warehouse at {db_path}; codes shown unresolved."

    return {
        "exists": True,
        "plan": plan,
        "players": players,
        "next_gw": next_gw,
        "diff_lines": _solve_diff_lines(),
        "reason": reason,
    }


def _solve_diff_lines() -> list[str]:
    """The rank-vs-points DIFF block from the newest solve log, verbatim.

    `fpl solve --mode both` prints the diff but persists only one plan, so the
    log is the diff's only durable home. Verbatim lines rather than a parsed
    structure: the solver's own words cannot drift from what it solved.
    """
    from fpl_edge.platform.solve_runner import JOBS_DIR

    logs = sorted(JOBS_DIR.glob("solve_*.log"))
    for log in reversed(logs):
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if line.startswith("--- DIFF"):
                block = [line]
                for nxt in lines[i + 1:]:
                    if nxt.startswith(("note:", "plan committed", "forecast committed")):
                        return block
                    block.append(nxt)
                return block
    return []


# The deterministic QuestionRouter route is DELETED (CHAT_ARCHITECTURE §2
# decision 1): one brain. Every message goes to the agent conversations; the
# router's genuinely good answers live on as toolbelt tools the agent calls.


def serve(host: str = "127.0.0.1", port: int = 8321,
          db: Path | str = DEFAULT_DB, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run(create_app(db), host=host, port=port, reload=reload)
