"""The on-demand Understat profile fetch: one POST to start it, one GET to
poll it."""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from fpl_edge.platform.app.helpers import SEASON_DEFAULT, UTC, Deps, FetchProfileRequest


def _players_router(deps: Deps) -> APIRouter:
    """The on-demand Understat profile fetch: one POST to start it, one GET
    to poll it."""

    app = deps.app
    db_path = deps.db_path
    router = APIRouter()

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

    @router.post("/api/players/{code}/fetch_profile")
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

    @router.get("/api/players/{code}/fetch_profile")
    def get_fetch_profile(code: int) -> JSONResponse:
        return JSONResponse(_profile_fetch_state(code))

    return router
