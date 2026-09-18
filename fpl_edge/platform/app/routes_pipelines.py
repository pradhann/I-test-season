"""The browser's one way to start a registry pipeline, plus its poller."""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from fpl_edge.platform.app.helpers import UTC, Deps, PipelineRunRequest
from fpl_edge.platform.query import read_copy


def _pipelines_router(deps: Deps) -> APIRouter:
    """The browser's one way to start a registry pipeline, plus its poller."""

    app = deps.app
    db_path = deps.db_path
    router = APIRouter()

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

    @router.post("/api/pipelines/{task_id}/run")
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

    @router.get("/api/pipelines/{task_id}/run_state")
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

    return router
