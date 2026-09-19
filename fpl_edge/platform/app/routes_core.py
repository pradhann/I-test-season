"""Health, the deadline clock, the panel registry, the script runner and the
guarded query path."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from fpl_edge.platform import panels as panels_mod
from fpl_edge.platform.app.helpers import UTC, Deps, QueryRequest, RunRequest
from fpl_edge.platform.query import QueryError, guarded_query, read_copy
from fpl_edge.platform.registry import (
    ParamsInvalid,
    ResultInvalid,
    ScriptError,
    repo_sha,
    run_script,
)
from fpl_edge.platform.registry import describe_all as describe_scripts
from fpl_edge.platform.users import UserContext, current_user

#: The health fields an anonymous caller sees. AUTH.md row 1: every other
#: field on that payload describes this deployment's own infrastructure, and
#: ``warehouse`` is a filesystem path on the host.
PUBLIC_HEALTH_FIELDS = ("ok", "now")


def caller_tiers(request: Request) -> frozenset[str]:
    """Which tiers the caller of ``request`` meets.

    Read from the session the auth dependency already resolved onto the
    request, so the catalogue and the enforcement answer from one fact. An
    app built without ``install_auth`` has no session and no sign-in
    configured, which ``anon_is_owner`` answers as the operator, exactly as
    it does for the CLI and the tests.
    """
    from fpl_edge.platform.auth import policy, settings

    session = getattr(getattr(request, "state", None), "auth_session", None)
    return policy.tiers_allowed(
        signed_in=session is not None,
        is_operator=bool(session is not None and session.user.is_operator),
        anon_is_owner=settings.anon_is_owner(),
    )


def _core_router(deps: Deps) -> APIRouter:
    """Health, the deadline clock, the panel registry, the script runner and
    the guarded query path."""

    db_path = deps.db_path
    router = APIRouter()

    @router.get("/api/health")
    def health(request: Request) -> JSONResponse:
        """Railway's healthcheck target, and the operator's one-glance page.

        503 when the volume is absent or unwritable, the warehouse file is
        missing, or a migration set failed. Each of those means serving a
        request would lose data or return a wrong answer.

        200 with ``scheduler.running: false`` when the scheduler is dead. The
        status code deliberately does not follow the scheduler: a failing
        health check stops Railway routing traffic and restarts a container
        that keeps failing, so a crashed background task would take the whole
        UI down with it. The correct response to a dead scheduler is a red dot
        on the Pipelines panel, not an outage.

        A process that never ran the boot sequence (the Mac dev server, the
        test suite) reports what it can measure and stays 200.
        """
        from fpl_edge.platform import boot as boot_mod
        from fpl_edge.platform import scheduler as sched_mod

        payload: dict[str, Any] = {
            "ok": True,
            "repo_sha": repo_sha(),
            "warehouse": str(db_path),
            "warehouse_present": db_path.exists(),
            "now": dt.datetime.now(UTC).isoformat(),
        }
        report = boot_mod.LAST_REPORT
        if report is not None and report.db_path != str(db_path):
            # A report for a different warehouse describes a different
            # deployment. Reporting it here would be a health payload about a
            # file this app does not serve.
            report = None
        if report is None:
            payload["boot"] = {
                "ran": False,
                "reason": f"{boot_mod.__name__}.boot has not run in this "
                          f"process; set FPL_EDGE_BOOT=1 to run it at startup",
            }
        else:
            payload["boot"] = {"ran": True, **report.to_dict()}
            payload["volume"] = payload["boot"]["volume"]
            payload["migrations"] = payload["boot"]["migrations"]
            payload["artefacts"] = payload["boot"]["artefacts"]
            payload["ok"] = bool(report.ok) and payload["warehouse_present"]

        scheduler = getattr(deps.app.state, "scheduler", None)
        if scheduler is None:
            payload["scheduler"] = {
                "running": False,
                "reason": f"{sched_mod.SCHEDULER_ENV} is not set in this "
                          f"process, so no scheduler was started",
            }
        else:
            payload["scheduler"] = scheduler.state.to_dict()

        status = 200 if payload["ok"] else 503
        from fpl_edge.platform.auth.policy import ANONYMOUS

        if caller_tiers(request) == frozenset({ANONYMOUS}):
            # AUTH.md row 1. The status code is unchanged, so Railway's
            # healthcheck still reads the same signal. What the anonymous
            # body loses is the warehouse path, the boot report, the
            # migration list and the scheduler state, which describe this
            # deployment rather than whether it is serving.
            payload = {k: payload[k] for k in PUBLIC_HEALTH_FIELDS}
        return JSONResponse(payload, status_code=status)

    @router.get("/api/deadline")
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

    @router.get("/api/panels")
    def get_panels(request: Request) -> dict[str, Any]:
        """The catalogue, filtered to what this caller may actually run.

        AUTH.md 6.2 rule 2: an anonymous visitor's UI must not render a tab
        that 401s on click. The tiers are enforced already; this is the
        payload catching up with them, so a name the caller cannot run is
        absent rather than listed and then refused.
        """
        from fpl_edge.platform.auth.policy import OPERATOR, SCRIPT_TIERS

        tiers = caller_tiers(request)

        def allowed(name: str) -> bool:
            # An unclassified script is operator only, the same fail-closed
            # answer auth/routes.py gives an unclassified route.
            return SCRIPT_TIERS.get(name, OPERATOR) in tiers

        return {
            "panels": [p for p in panels_mod.describe_all()
                       if allowed(p["script"])],
            "scripts": [row for row in describe_scripts()
                        if allowed(row["name"])],
            "repo_sha": repo_sha(),
        }

    #: A panel failure the UI can render. Bounded so a pandas repr or a whole
    #: SQL statement inside an exception message cannot flood the page.
    _ERROR_REASON_CHARS = 500

    def _panel_error(panel: str, exc: BaseException) -> JSONResponse:
        """The structured 500 for a broken panel: {error, panel, reason}.

        Never a bare "Internal Server Error" body. The UI needs to draw
        "couldn't load -- retry" as a state DISTINCT from the honest-empty
        shape ({empty, reason}, a 200): one means the data is genuinely
        absent, the other means this code failed. Collapsing them was how a
        third of the fixture board came to fail silently.
        """
        reason = f"{type(exc).__name__}: {exc}"
        if len(reason) > _ERROR_REASON_CHARS:
            reason = reason[: _ERROR_REASON_CHARS - 1] + "…"
        return JSONResponse(
            status_code=500,
            content={"error": True, "panel": panel, "reason": reason},
        )

    @router.post("/api/scripts/{name}/run")
    def post_run_script(
        name: str,
        body: RunRequest | None = None,
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        """Run one panel script for the requesting user.

        The user context is resolved here, once, and handed to the runner,
        which passes it only to the scripts that declare a ``ctx``. Whose team
        a panel describes is therefore never something the request body can
        say: ``squad_overview``, ``dashboard_brief`` and ``planner_grid``
        stopped taking ``entry_id`` as a param for exactly that reason.
        """
        params = body.resolved() if body is not None else {}
        try:
            run = run_script(name, params, db=db_path, ctx=user)
        except KeyError as exc:
            # Only the registry's own "no such script" KeyError is a 404. A
            # KeyError raised INSIDE a script is that script's bug, and serving
            # it as 404 would tell the UI the panel does not exist.
            if "no panel script named" in str(exc):
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            return _panel_error(name, exc)
        except ParamsInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (ResultInvalid, ScriptError) as exc:
            # The script broke its own contract. That is a 500: the caller did
            # nothing wrong and must not be led to think the shape is theirs to
            # fix.
            return _panel_error(name, exc)
        except Exception as exc:  # noqa: BLE001 - the contract IS the catch-all
            return _panel_error(name, exc)
        return JSONResponse(run.to_dict())

    @router.post("/api/query")
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

    return router
