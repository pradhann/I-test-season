"""Deliveries, and the monitor definitions read back off the deadline DAG."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from fpl_edge.platform import deliveries as deliveries_mod
from fpl_edge.platform.app.helpers import Deps, _monitor_definitions


def _inbox_router(deps: Deps) -> APIRouter:
    """Deliveries and the monitor definitions read back off the deadline DAG."""

    db_path = deps.db_path
    router = APIRouter()

    @router.get("/api/inbox")
    def get_inbox(limit: int = 50, include_acked: bool = False) -> JSONResponse:
        return JSONResponse(
            deliveries_mod.list_deliveries(db=db_path, limit=limit,
                                           include_acked=include_acked)
        )

    @router.post("/api/inbox/{delivery_id}/ack")
    def post_ack(delivery_id: str) -> JSONResponse:
        result = deliveries_mod.ack(delivery_id, db=db_path)
        if not result.get("found"):
            raise HTTPException(status_code=404, detail=result.get("reason", "not found"))
        return JSONResponse(result)

    @router.get("/api/monitors")
    def get_monitors() -> dict[str, Any]:
        return _monitor_definitions(db_path)

    @router.post("/api/monitors/{name}/run")
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

    return router
