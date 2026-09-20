"""The briefing artefact and the agent conversations, including the chart
assets a turn produced."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from fpl_edge.platform.app.helpers import Deps, TurnRequest
from fpl_edge.platform.users import UserContext, current_user


def _chat_router(deps: Deps) -> APIRouter:
    """The briefing artefact and the agent conversations, including the chart
    assets a turn produced."""

    from fpl_edge.platform.chat_agent import (
        ChatAgentError,
        TurnInFlight,
        UnknownConversation,
    )

    db_path = deps.db_path
    router = APIRouter()

    # ---- the intelligence briefing (fpl_edge/platform/briefing_intel.py) --
    # GET is the read: the model-authored salience artefact plus freshness
    # plus what this process knows about a run in flight. A missing artefact
    # is 404-shaped JSON, never an exception, so the UI renders the gap and
    # offers the button.
    #
    # POST is the write, and it is on demand and per user. Each manager runs
    # their own brief on their own credential into their own artefact, and
    # there is no schedule behind it, so nothing spends anybody's tokens
    # unasked. The operator's scheduled briefing_intel task is untouched and
    # still runs through POST /api/pipelines/briefing_intel/run on the CLI
    # login of the machine the server runs on.

    @router.get("/api/briefing")
    def get_briefing(
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        from fpl_edge.platform import briefing_intel

        return JSONResponse(briefing_intel.briefing_response(db_path, ctx=user))

    @router.post("/api/briefing")
    def post_briefing(
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        """Run this caller's analysis brief on this caller's credential.

        202 with the run record, which the page follows by polling GET. 409
        while one is already running for this account, because two runs would
        spend the caller's tokens twice and race to write one file. 403 with
        the same named body the chat turn gives when no credential is stored.
        503 when the network kill-switch is on, since the model call leaves
        the machine.

        The tier is the chat tier for the same reason: the request spends the
        caller's credential. The access matrix refuses a signed-in manager
        with no stored credential before this handler runs, and the handler
        refuses again on its own, so the route is correct read by itself
        rather than only in company with the table.

        A team id is not required. Five of the seven input panels are league
        wide; the two that describe one squad serve their own named empty for
        a caller with no team, and the pass keeps that empty in the context
        it sends, so the brief covers the board and says which squad panels
        it had nothing from. The pass only refuses when every panel is empty.
        """
        from fpl_edge.platform import briefing_intel
        from fpl_edge.platform.app.helpers import SEASON_DEFAULT
        from fpl_edge.platform.auth.routes import NoKeyRefused

        try:
            credential_env = user.credential_env()
        except Exception as exc:  # noqa: BLE001 - see below
            # The only thing that raises here is a stored credential the
            # current secrets cannot decrypt, which is the caller's problem
            # to fix on the Account tab and not a server fault. Its own words
            # ride out in the flat body shape the UI already prints.
            return JSONResponse(
                {"error": "key_unreadable", "detail": str(exc),
                 "remediation_url": "/#account"},
                status_code=403)
        if not credential_env and not user.is_owner:
            raise NoKeyRefused
        try:
            state = briefing_intel.start_for_user(
                db_path, season=SEASON_DEFAULT, ctx=user,
                credential_env=credential_env)
        except briefing_intel.BriefingRunInFlight as exc:
            return JSONResponse(
                {"error": "briefing_in_flight", "detail": str(exc),
                 "run": briefing_intel.run_state(user)},
                status_code=409)
        except briefing_intel.BriefingGated as exc:
            return JSONResponse(
                {"error": "network_disabled", "detail": str(exc)},
                status_code=503)
        return JSONResponse({"started": True, "run": state}, status_code=202)

    # ---- agent conversations (fpl_edge/platform/chat_agent.py) ----
    # The router fast-path above stays untouched; these routes are the
    # explicit escalation. Argus discipline: the turn runs server-side to
    # completion, events are persisted then broadcast, and /stream replays
    # from any seq so a reload re-attaches mid-turn.

    @router.post("/api/conversations")
    def post_conversation(
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        meta = deps.agent_for(user).create_conversation()
        return JSONResponse({"conv_id": meta["conv_id"], "meta": meta})

    @router.get("/api/conversations")
    def get_conversations(
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        return JSONResponse(
            {"conversations": deps.agent_for(user).list_conversations()})

    @router.delete("/api/conversations/{conv_id}")
    def delete_conversation(
        conv_id: str,
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        """Delete one conversation and its transcript. Irreversible.

        409 while a turn is in flight: the same guard start_turn uses, for the
        same reason. The caller stops the turn first.
        """
        try:
            meta = deps.agent_for(user).delete_conversation(conv_id)
        except TurnInFlight as exc:
            return JSONResponse(
                {"detail": str(exc), **deps.agent_for(user).running(conv_id)},
                status_code=409,
            )
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ChatAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse({"deleted": conv_id, "meta": meta})

    @router.post("/api/conversations/{conv_id}/chat")
    def post_conversation_chat(
        conv_id: str,
        body: TurnRequest,
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        # The context goes with the turn so the runner thread can ask it for
        # the caller's own Anthropic key at the point of use. A signed-in
        # manager with no stored key never reaches here: the key tier in
        # fpl_edge/platform/auth/policy.py refused the request already.
        try:
            started = deps.agent_for(user).start_turn(conv_id, body.text,
                                                      user=user)
        except TurnInFlight as exc:
            return JSONResponse(
                {"detail": str(exc), **deps.agent_for(user).running(conv_id)},
                status_code=409,
            )
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ChatAgentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(started, status_code=202)

    @router.get("/api/conversations/{conv_id}/stream")
    def get_conversation_stream(
        conv_id: str, after: int = -1, once: int = 0,
        user: UserContext = Depends(current_user),
    ):
        """SSE. ``once=1`` replays and closes (curl/tests); default follows
        live with comment heartbeats, indefinitely."""
        from sse_starlette.sse import EventSourceResponse

        agent = deps.agent_for(user)
        # Resolved before the stream opens. subscribe() is a generator, so
        # an unknown id raised from inside it reaches sse-starlette rather
        # than this except, and the caller got a 200 with a broken body.
        try:
            agent.require_conversation(conv_id)
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return EventSourceResponse(
            agent.subscribe(conv_id, after=after, follow=not once))

    @router.get("/api/conversations/{conv_id}/events")
    def get_conversation_events(
        conv_id: str, after: int = -1,
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        try:
            events = deps.agent_for(user).events(conv_id, after=after)
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse({
            "events": events,
            **deps.agent_for(user).running(conv_id),
            "meta": deps.agent_for(user).meta(conv_id),
        })

    @router.post("/api/conversations/{conv_id}/stop")
    def post_conversation_stop(
        conv_id: str,
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        try:
            return JSONResponse(deps.agent_for(user).stop(conv_id))
        except UnknownConversation as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/chat/assets/{asset_id}.{ext}")
    def get_chat_asset(
        asset_id: str, ext: str,
        user: UserContext = Depends(current_user),
    ):
        from fastapi.responses import FileResponse

        # python_viz writes SVG alongside PNG; SVG is preferred by the chat
        # sub-app (crisp at any zoom, themeable), PNG stays for the legacy
        # pane and image fallbacks. Anything else 404s -- the extension is
        # part of the allowlist, exactly like the hex id.
        if ext not in ("png", "svg"):
            raise HTTPException(status_code=404, detail="no such asset")
        path = deps.agent_for(user).asset_path(asset_id, ext=ext)
        if path is None:
            raise HTTPException(status_code=404, detail="no such asset")
        media = "image/png" if ext == "png" else "image/svg+xml"
        return FileResponse(path, media_type=media)

    return router
