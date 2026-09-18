"""``create_app``: seven route groups wired onto one FastAPI instance, with the
account router and the static bundle behind them.

``WEB_DIST`` anchors on ``parents[3]`` rather than the ``parents[2]`` it
carried in ``app.py``, this file being one directory deeper.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from fpl_edge.platform.app.helpers import Deps
from fpl_edge.platform.app.routes_chat import _chat_router
from fpl_edge.platform.app.routes_content import _content_router
from fpl_edge.platform.app.routes_core import _core_router
from fpl_edge.platform.app.routes_inbox import _inbox_router
from fpl_edge.platform.app.routes_pipelines import _pipelines_router
from fpl_edge.platform.app.routes_players import _players_router
from fpl_edge.platform.app.routes_solve import _solve_router
from fpl_edge.platform.registry import repo_sha
from fpl_edge.store.warehouse import DEFAULT_DB

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"


def create_app(db: Path | str = DEFAULT_DB,
               chat_root: Path | str | None = None) -> FastAPI:
    """Build the app. ``db`` is injectable so tests can seed a tmp warehouse;
    ``chat_root`` likewise for the agent conversation store."""
    from fpl_edge.platform.chat_agent import CHAT_ROOT, ChatAgent

    db_path = Path(db)
    chat_agent = ChatAgent(root=Path(chat_root) if chat_root else CHAT_ROOT)
    app = FastAPI(
        title="i-test platform",
        version="1.0",
        description="Single-operator FPL decision platform. Panels, one guarded "
                    "query path, inbox, chat.",
    )
    # Exposed for tests, which point the agent at a fake CLI script; the
    # chat router closes over the same object.
    app.state.chat_agent = chat_agent

    deps = Deps(app=app, db_path=db_path, chat_agent=chat_agent)
    app.include_router(_core_router(deps))
    app.include_router(_inbox_router(deps))
    app.include_router(_solve_router(deps))
    app.include_router(_players_router(deps))
    app.include_router(_pipelines_router(deps))
    app.include_router(_content_router(deps))
    app.include_router(_chat_router(deps))

    # Connect-your-FPL-account routes. Included BEFORE the static mount: that
    # Mount("/") is a catch-all matched in order, and a router added after it
    # answers 404.
    from fpl_edge.platform import routes_account
    app.include_router(routes_account.router)

    if WEB_DIST.is_dir():
        # Zero-build UI: a redeploy is a file write, and the browser was serving
        # yesterday's module until a hard refresh because nothing said otherwise.
        # no-cache means revalidate every time (ETag/Last-Modified still make the
        # common case a 304), so an edit is live on the next plain reload.
        @app.middleware("http")
        async def _no_stale_static(request, call_next):
            response = await call_next(request)
            ct = response.headers.get("content-type", "")
            if request.method == "GET" and (
                ct.startswith(("text/html", "text/css", "application/javascript",
                               "text/javascript"))):
                response.headers.setdefault("Cache-Control", "no-cache")
            return response

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
