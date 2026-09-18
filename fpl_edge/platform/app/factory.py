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
from fpl_edge.platform.app.routes_transcripts import _transcripts_router
from fpl_edge.platform.registry import repo_sha
from fpl_edge.store.warehouse import DEFAULT_DB

WEB_DIST = Path(__file__).resolve().parents[3] / "web" / "dist"

#: Set to anything other than "" or "0" to run the container boot sequence
#: (fpl_edge/platform/boot.py) on startup. Off by default, so the test suite
#: and the Mac dev server keep building apps without touching a volume.
BOOT_ENV = "FPL_EDGE_BOOT"


def _lifespan_for(db_path: Path):
    """Boot steps 7 and 8, and the shutdown that stops the scheduler.

    Boot runs here rather than in the container's CMD so that the report it
    produces is readable by ``/api/health`` in the same process. A
    :class:`~fpl_edge.platform.boot.BootFailure` raised here aborts uvicorn's
    startup, which is the loud failure the spec asks for: a service that
    answered requests without a mounted volume would accept writes and lose
    them on the next restart.

    The scheduler starts last and is the one step whose failure does not fail
    the boot. A dead scheduler shows as ``scheduler.running: false`` in the
    health payload and a red dot on the Pipelines panel. Failing the health
    check over it would restart-loop the service and take the UI down for a
    bug in a background task.

    A lifespan rather than ``on_event``, which FastAPI deprecated: the two
    handlers added roughly a hundred deprecation warnings to every suite run.
    Neither runs unless the app is entered as a context manager, so a bare
    ``TestClient(create_app(db))`` still builds an app that touches no volume
    and starts no loop.
    """
    import os
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        from fpl_edge.platform import boot as boot_mod
        from fpl_edge.platform import scheduler as sched_mod

        if str(os.environ.get(BOOT_ENV, "")) not in ("", "0"):
            data_dir = Path(os.environ.get("FPL_EDGE_DATA_DIR",
                                           str(boot_mod.DEFAULT_DATA_DIR)))
            boot_mod.boot(data_dir, db_path=db_path)
        if sched_mod.scheduler_enabled():
            scheduler = sched_mod.Scheduler(db_path)
            app.state.scheduler = scheduler
            scheduler.start()
        try:
            yield
        finally:
            scheduler = getattr(app.state, "scheduler", None)
            if scheduler is not None:
                await scheduler.stop()
                app.state.scheduler = None

    return _lifespan


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
        description="Multi-user FPL decision platform. Panels, one guarded "
                    "query path, inbox, chat.",
        lifespan=_lifespan_for(db_path),
        # The three generated schema routes name every operator route, so
        # they are re-added by install_auth behind the operator tier rather
        # than served here where no dependency reaches them.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Exposed for tests, which point the agent at a fake CLI script; the
    # chat router closes over the same object.
    app.state.chat_agent = chat_agent
    # Set at build time, not in the lifespan: /api/health and the tests read it
    # on apps that were never entered as a context manager.
    app.state.scheduler = None

    # Sign-in, sessions, the per-user key routes, and the one dependency that
    # applies docs/platform/AUTH.md's access matrix to every route below.
    # Installed first so the dependency is on app.router before any router is
    # included: FastAPI copies the parent's dependency list into each include,
    # and a dependency appended afterwards would not reach them.
    from fpl_edge.platform.auth.routes import install_auth

    install_auth(app)

    deps = Deps(app=app, db_path=db_path, chat_agent=chat_agent)
    app.include_router(_core_router(deps))
    app.include_router(_inbox_router(deps))
    app.include_router(_solve_router(deps))
    app.include_router(_players_router(deps))
    app.include_router(_pipelines_router(deps))
    app.include_router(_content_router(deps))
    app.include_router(_chat_router(deps))
    # The Mac ASR worker's two routes. Bearer-authenticated, and included here
    # like every other router: before the static Mount("/"), which is a
    # catch-all matched in order.
    app.include_router(_transcripts_router(deps))

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
