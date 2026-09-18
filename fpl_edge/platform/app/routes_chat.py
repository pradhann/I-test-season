"""The briefing artefact and the agent conversations, including the chart
assets a turn produced."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from fpl_edge.platform.app.helpers import Deps, TurnRequest


def _chat_router(deps: Deps) -> APIRouter:
    """The briefing artefact and the agent conversations, including the chart
    assets a turn produced."""

    from fpl_edge.platform.chat_agent import (
        ChatAgentError,
        TurnInFlight,
        UnknownConversation,
    )

    chat_agent = deps.chat_agent
    db_path = deps.db_path
    router = APIRouter()

    # ---- the intelligence briefing (fpl_edge/platform/briefing_intel.py) --
    # Read-only: serves the model-authored salience artefact plus freshness.
    # A missing artefact is 404-shaped JSON, never an exception, so the UI
    # renders the gap and offers the trigger; generation itself goes through
    # POST /api/pipelines/briefing_intel/run — the same seam as every task,
    # so a UI-triggered briefing leaves the same ledger row a scheduled one
    # does. No POST here on purpose.

    @router.get("/api/briefing")
    def get_briefing() -> JSONResponse:
        from fpl_edge.platform import briefing_intel

        return JSONResponse(briefing_intel.briefing_response(db_path))

    # ---- agent conversations (fpl_edge/platform/chat_agent.py) ----
    # The router fast-path above stays untouched; these routes are the
    # explicit escalation. Argus discipline: the turn runs server-side to
    # completion, events are persisted then broadcast, and /stream replays
    # from any seq so a reload re-attaches mid-turn.

    @router.post("/api/conversations")
    def post_conversation() -> JSONResponse:
        meta = chat_agent.create_conversation()
        return JSONResponse({"conv_id": meta["conv_id"], "meta": meta})

    @router.get("/api/conversations")
    def get_conversations() -> JSONResponse:
        return JSONResponse({"conversations": chat_agent.list_conversations()})

    @router.delete("/api/conversations/{conv_id}")
    def delete_conversation(conv_id: str) -> JSONResponse:
        """Delete one conversation and its transcript. Irreversible.

        409 while a turn is in flight: the same guard start_turn uses, for the
        same reason. The caller stops the turn first.
        """
        try:
            meta = chat_agent.delete_conversation(conv_id)
        except TurnInFlight as exc:
            return JSONResponse(
                {"detail": str(exc), **chat_agent.running(conv_id)},
                status_code=409,
            )
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ChatAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"deleted": conv_id, "meta": meta})

    @router.post("/api/conversations/{conv_id}/chat")
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

    @router.get("/api/conversations/{conv_id}/stream")
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

    @router.get("/api/conversations/{conv_id}/events")
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

    @router.post("/api/conversations/{conv_id}/stop")
    def post_conversation_stop(conv_id: str) -> JSONResponse:
        try:
            return JSONResponse(chat_agent.stop(conv_id))
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/chat/assets/{asset_id}.{ext}")
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

    return router
