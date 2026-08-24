"""The platform HTTP surface — DESIGN.md §2.1, implemented exactly.

    GET  /api/panels                  registered panels + their pinned scripts
    POST /api/scripts/{name}/run      {result, provenance}, 10s soft budget
    POST /api/query                   guarded read-only SQL
    GET  /api/inbox                   deliveries, newest first
    POST /api/inbox/{id}/ack          acknowledge one
    GET  /api/monitors                monitor definitions
    POST /api/chat                    QuestionRouter answer (text + images)
    GET  /api/chat/stream             SSE escalation to the headless agent
    /                                 the built web/ bundle, if present

What is deliberately *absent* is as load-bearing as what is here: there is no
route that writes a fact, no route that takes SQL from a panel, and no route
that accepts credentials. The server holds no API key -- the chat escalation
shells out to the Max-plan `claude` CLI, which owns its own auth (DESIGN §2
item 6), and every secret the engine needs is read through
:func:`fpl_edge.config.secret` at the point of use.

Binding: loopback only, by default and in the plist. This is a single-operator
local platform with no auth layer; a server that answers on 0.0.0.0 would put
an unauthenticated SQL endpoint on the local network.
"""

from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from fpl_edge.platform import inbox as inbox_mod
from fpl_edge.platform import panels as panels_mod
from fpl_edge.platform.query import QueryError, guarded_query, read_copy
from fpl_edge.platform.registry import (
    ParamsInvalid,
    ResultInvalid,
    ScriptError,
    describe_all as describe_scripts,
    repo_sha,
    run_script,
)
from fpl_edge.store.warehouse import DEFAULT_DB

# Importing the scripts package is what registers the five panel scripts.
import fpl_edge.platform.scripts  # noqa: F401

UTC = dt.timezone.utc

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


class ChatRequest(BaseModel):
    text: str
    season: str | None = None


class SolveRequest(BaseModel):
    mode: str = "both"


def create_app(db: Path | str = DEFAULT_DB) -> FastAPI:
    """Build the app. ``db`` is injectable so tests can seed a tmp warehouse."""
    db_path = Path(db)
    app = FastAPI(
        title="i-test platform",
        version="1.0",
        description="Single-operator FPL decision platform. Panels, one guarded "
                    "query path, inbox, chat.",
    )

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
        except Exception as exc:  # noqa: BLE001 - a SQL error is the user's answer
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

    @app.post("/api/chat")
    def post_chat(body: ChatRequest) -> JSONResponse:
        return JSONResponse(_chat(body, db_path))

    @app.get("/api/chat/stream")
    async def get_chat_stream(text: str):
        from sse_starlette.sse import EventSourceResponse

        return EventSourceResponse(_escalate(text))

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


def _chat(body: ChatRequest, db_path: Path) -> dict[str, Any]:
    """Route one message through the existing deterministic QuestionRouter.

    The router is reused rather than reimplemented so that the web pane and the
    Telegram bot give the *same* answer to the same question -- two chat
    surfaces that disagree is worse than one chat surface.

    It gets a :class:`LeasedWarehouse`, not a read copy, because a handful of
    intents legitimately write (a shared link is transcribed and stored). The
    lease connects on first use and is released here, so the lock is held for
    the duration of one answer rather than the life of the server, which is
    exactly the posture the bot already uses between polls.
    """
    from fpl_edge.interfaces.qa import SEASON_DEFAULT, QuestionRouter
    from fpl_edge.store.warehouse import LeasedWarehouse

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if not db_path.exists():
        return {"routed": False, "intent": None,
                "text": f"No warehouse at {db_path}; run `make ingest` first.",
                "images": []}

    lease = LeasedWarehouse(db_path)
    try:
        router = QuestionRouter(lease, season=body.season or SEASON_DEFAULT)
        answer = router.route(text)
    except Exception as exc:  # noqa: BLE001 - a failed answer must say so
        return {"routed": False, "intent": None, "images": [],
                "text": f"That answer failed: {type(exc).__name__}: {exc}"}
    finally:
        lease.release()

    if answer is None:
        return {
            "routed": False,
            "intent": None,
            "images": [],
            "text": (
                "I don't have a deterministic answer for that. The router "
                "matches a fixed set of question intents and does not guess at "
                "the rest -- in Telegram this message would be filed as an idea. "
                "Open the agent escalation (/api/chat/stream) for open-ended "
                "questions about the warehouse or the codebase."
            ),
            "escalation_available": True,
        }

    return {
        "routed": True,
        "intent": _intent_for(text, db_path, body.season),
        "text": answer.text,
        "images": [
            {"filename": name,
             "mime": "image/png",
             "base64": base64.b64encode(png).decode("ascii")}
            for name, png in answer.images
        ],
        "provenance": {"repo_sha": repo_sha(),
                       "generated_at": dt.datetime.now(UTC).isoformat()},
    }


def _intent_for(text: str, db_path: Path, season: str | None) -> str | None:
    """Which intent matched, for the trace. Pattern matching only, no handlers."""
    from fpl_edge.interfaces.qa import SEASON_DEFAULT, QuestionRouter

    try:
        router = QuestionRouter(None, season=season or SEASON_DEFAULT)
        for intent in router.intents:
            if intent.name == "creator_summary":
                from fpl_edge.interfaces.creators import match_creators

                if not match_creators(text):
                    continue
            if intent.pattern.search(text.strip()):
                return intent.name
    except Exception:  # noqa: BLE001 - the trace is a nicety, never a failure
        return None
    return None


async def _escalate(text: str):
    """Stream a headless `claude -p` answer as SSE events.

    No API key lives in this process: the CLI carries the Max-plan session, so
    the escalation costs nothing per call and the server stays credential-free
    (DESIGN §2 item 6). If the binary is not installed the stream says so once
    and ends -- silence would be indistinguishable from a slow model.
    """
    import asyncio
    import shutil

    binary = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not Path(binary).exists():
        yield {"event": "error", "data":
               "The `claude` CLI is not installed, so agent escalation is "
               "unavailable. The deterministic router at POST /api/chat still works."}
        return

    yield {"event": "start", "data": text}
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, "-p", text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(Path(__file__).resolve().parents[2]),
        )
    except OSError as exc:
        yield {"event": "error", "data": f"could not start the agent: {exc}"}
        return
    try:
        assert proc.stdout is not None
        async for line in proc.stdout:
            yield {"event": "token", "data": line.decode("utf-8", "replace").rstrip("\n")}
        await proc.wait()
        yield {"event": "done", "data": str(proc.returncode)}
    finally:
        if proc.returncode is None:
            proc.kill()


#: Module-level app for `uvicorn fpl_edge.platform.app:app`.
app = create_app()


def serve(host: str = "127.0.0.1", port: int = 8321,
          db: Path | str = DEFAULT_DB, reload: bool = False) -> None:
    import uvicorn

    uvicorn.run(create_app(db), host=host, port=port, reload=reload)
