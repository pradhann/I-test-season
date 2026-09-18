"""The solve routes, and the two persisted plan artefacts they serve.

``_PLAN_PATH`` and ``_TRANSFER_PLAN_PATH`` anchor on ``parents[3]`` rather than
the ``parents[2]`` they carried in ``app.py``: this file sits one directory
deeper and the repo root is what both paths mean.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from fpl_edge.platform.app.helpers import (
    SEASON_DEFAULT,
    UTC,
    Deps,
    SolveRequest,
    _brief_thresholds,
    _deadline_calendar,
    _player_lookup,
)
from fpl_edge.platform.query import read_copy
from fpl_edge.platform.users import (
    GW1_PLAN_NAME,
    PLANS_DIR,
    TRANSFER_PLAN_NAME,
    UserContext,
    current_user,
)


def _solve_router(deps: Deps) -> APIRouter:
    """Starting a solve, its status, and the two persisted plan artefacts."""

    db_path = deps.db_path
    router = APIRouter()

    @router.post("/api/solve")
    def post_solve(
        body: SolveRequest | None = None,
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        # Unlike a monitor (the 501 above), the solve is safe to fire from a
        # browser: the CLI subprocess owns its own locking and artefacts, the
        # runner enforces one-at-a-time, and nothing here double-sends.
        mode = (body.mode if body is not None else "both").strip().lower()
        from fpl_edge.platform import solve_runner
        from fpl_edge.platform.users import PRIVATE_GAP

        if not user.is_owner:
            # A solve starts from the fifteen the manager holds and commits a
            # plan priced against their bank, both of which come from a squad
            # this server cannot read for anybody but the owner. Running it
            # anyway would solve the owner's team and overwrite the owner's
            # plan, so the answer is the same named gap the panels give.
            return JSONResponse(
                status_code=403,
                content={"ok": False, "started": False, "reason": PRIVATE_GAP},
            )

        if mode not in solve_runner.MODES:
            raise HTTPException(
                status_code=400,
                detail=f"mode must be one of {list(solve_runner.MODES)}",
            )
        options = body.options if body is not None else None
        try:
            # Validated here as well as in start(): a bad option is a 400 with
            # the reason, and nothing is spawned for it.
            if mode == "transfers":
                solve_runner.normalise_options(options)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(solve_runner.start(mode, options=options))

    @router.get("/api/solve/status")
    def get_solve_status() -> JSONResponse:
        from fpl_edge.platform import solve_runner

        return JSONResponse(solve_runner.status())

    @router.get("/api/solve/plan")
    def get_solve_plan(
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        return JSONResponse(_solve_plan(db_path, user))

    @router.get("/api/solve/transfer-plan")
    def get_transfer_plan(
        user: UserContext = Depends(current_user),
    ) -> JSONResponse:
        """transfer_plan.json (the `fpl recommend` artefact) with names resolved
        and its freshness judged against the deadline calendar.

        A plan is one manager's: it names the fifteen they hold and the moves
        priced against their bank. Each user reads their own copy, and the
        owner keeps reading the file beside the warehouse until it is moved."""
        return JSONResponse(_transfer_plan(db_path, user))

    return router


#: Anchored to the repo root like squad_section.PLAN_PATH -- a relative path
#: made that section's plan "missing" whenever the process ran from another
#: directory, and this route must not re-learn that lesson.
_PLAN_PATH = Path(__file__).resolve().parents[3] / "data" / "warehouse" / GW1_PLAN_NAME


def _plan_path(user: UserContext | None, name: str, legacy: Path) -> Path:
    """This user's plan artefact, or the owner's pre-split file.

    One helper for both plans so the two routes cannot drift apart about where
    a plan lives. A caller with no user is the owner, which is what the tests
    and the CLI are.
    """
    if user is None:
        from fpl_edge.platform.users import owner_context

        user = owner_context()
    return user.artefact(PLANS_DIR, name, legacy=legacy)


def _solve_plan(db_path: Path, user: UserContext | None = None) -> dict[str, Any]:
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

    plan_path = _plan_path(user, GW1_PLAN_NAME, _PLAN_PATH)
    if not plan_path.exists():
        return {
            "exists": False,
            "reason": f"no plan artefact at {plan_path.name}; run a solve first.",
        }
    try:
        plan = json.loads(plan_path.read_text())
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
                wanted = {int(c) for c in plan.get("gw1", {}).get("squad", [])}
                players = _player_lookup(wh, season, wanted)
                next_gw = _deadline_calendar(wh, None, dt.datetime.now(UTC))["next_gw"]
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


#: `fpl recommend --commit` writes here; the Planner tab and the dashboard's
#: solver card both read this one artefact. Module-level so a test can point
#: it at a fixture.
_TRANSFER_PLAN_PATH = (Path(__file__).resolve().parents[3] / "data" / "warehouse"
                       / TRANSFER_PLAN_NAME)


_GAP_NOTE = re.compile(r"(\d+(?:\.\d+)?)% optimality gap")

#: Imported from the brief's own THRESHOLDS so one number governs both
#: surfaces. A plan younger than this is "fresh"; older than this and still
#: pre-deadline it is "aging".
_SOLVE_FRESH_WINDOW_H = float(
    _brief_thresholds().get("solve_fresh_window_h", 12))


def _plan_codes(plan: dict[str, Any]) -> set[int]:
    codes: set[int] = set()
    moves = [plan.get("chosen") or {}, plan.get("unconstrained") or {},
             *(plan.get("alternatives") or [])]
    for m in moves:
        for key in ("out", "in", "starting_xi"):
            codes |= {int(c) for c in (m.get(key) or [])}
        for key in ("captain", "vice_captain"):
            if m.get(key) is not None:
                codes.add(int(m[key]))
    codes |= {int(c) for c in ((plan.get("constraints") or {}).get("must_keep") or [])}
    codes |= {int(c) for c in ((plan.get("constraints") or {}).get("ban") or [])}
    return codes


def _transfer_plan(db_path: Path, user: UserContext | None = None) -> dict[str, Any]:
    """transfer_plan.json, resolved and judged.

    Names, positions, teams and prices for every code the plan mentions come
    from the warehouse through a read copy. Freshness is judged the way the
    dashboard brief judges it: a plan generated before the last deadline, or
    solved for a gameweek that is not the next one, is STALE -- a record of a
    past decision the client must show as a gap with Re-solve, never as
    guidance. The optimality gap is parsed from the solver's own note so it
    can be printed beside the gain.
    """
    import json

    now = dt.datetime.now(UTC)
    plan_path = _plan_path(user, TRANSFER_PLAN_NAME, _TRANSFER_PLAN_PATH)
    if not plan_path.exists():
        return {"exists": False,
                "reason": (f"no transfer plan artefact at {plan_path.name}; "
                           f"solve to commit one (POST /api/solve mode=transfers).")}
    try:
        plan = json.loads(plan_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": False,
                "reason": f"transfer plan unreadable: {type(exc).__name__}: {exc}"}

    players: dict[str, Any] = {}
    cal: dict[str, Any] = {"next_gw": None, "next_deadline_utc": None,
                           "last_gw": None, "last_deadline_utc": None}
    held_now: list[int] = []
    reason = None
    if db_path.exists():
        try:
            with read_copy(db_path) as wh:
                season = plan.get("season")
                players = _player_lookup(wh, season, _plan_codes(plan))
                cal = _deadline_calendar(wh, season, now)
                held_now = _held_squad(wh, season, user)
        except Exception as exc:  # noqa: BLE001 - names are a nicety, the plan is the payload
            reason = f"could not resolve names: {type(exc).__name__}: {exc}"
    else:
        reason = f"no warehouse at {db_path}; codes shown unresolved."

    gen = None
    try:
        gen = dt.datetime.fromisoformat(str(plan.get("generated_at")))
        if gen.tzinfo is None:
            gen = gen.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        gen = None
    age_hours = (round((now - gen).total_seconds() / 3600.0, 1) if gen else None)

    stale_reason = None
    plan_gw = plan.get("gw")
    h = plan.get("horizon_gws") or []
    span = f"GW{h[0]}-{h[-1]}" if h else "?"
    last = cal.get("last_deadline_utc")
    if gen is not None and last is not None and gen < dt.datetime.fromisoformat(last):
        stale_reason = (
            f"plan generated {gen.strftime('%Y-%m-%d %H:%MZ')} for {span}; the "
            f"GW{cal.get('last_gw')} deadline has passed since, so its moves "
            f"were priced against a squad and a market you no longer have."
        )
    elif plan_gw is not None and cal.get("next_gw") is not None and int(plan_gw) != int(cal["next_gw"]):
        stale_reason = (
            f"plan solved for GW{plan_gw} but the next open deadline is "
            f"GW{cal['next_gw']}; it is a record of a past decision."
        )
    elif gen is None:
        stale_reason = "plan carries no readable generated_at; its age is unknowable."

    gap = None
    for note in plan.get("notes") or []:
        m = _GAP_NOTE.search(str(note))
        if m:
            gap = float(m.group(1))
            break

    # One freshness verdict, computed once, so the Planner and the Dashboard
    # cannot colour the same artefact differently. They used to: the Planner
    # called anything under 24h "standing" and green while the brief called the
    # same 5-hour-old plan "aging" and amber.
    # A plan's moves are diffed against the fifteen held when it was solved.
    # The dashboard refuses one whose squad has changed since; without the same
    # check here the Planner would render, in full, the plan the Dashboard
    # calls superseded. Two surfaces, one verdict.
    solved_against = [int(c) for c in (plan.get("squad_before") or [])]
    superseded = bool(solved_against and held_now
                      and set(solved_against) != set(held_now))
    superseded_reason = None
    if superseded:
        gone = sorted(set(solved_against) - set(held_now))
        got = sorted(set(held_now) - set(solved_against))
        superseded_reason = (
            f"the plan was solved against a different squad: {len(gone)} "
            f"player(s) it assumed you held are not in your fifteen, and "
            f"{len(got)} you hold were not in it. Re-solve."
        )

    state = "fresh"
    if stale_reason is not None:
        state = "stale"
    elif superseded:
        state = "superseded"
    elif age_hours is None:
        state = "aging"
    else:
        state = "fresh" if age_hours <= _SOLVE_FRESH_WINDOW_H else "aging"

    return {
        "exists": True,
        "superseded": superseded,
        "superseded_reason": superseded_reason,
        "path": str(plan_path.relative_to(plan_path.parents[2])),
        "plan": plan,
        "players": players,
        "as_of": plan.get("generated_at"),
        "age_hours": age_hours,
        "state": state,
        "stale": stale_reason is not None,
        "stale_reason": stale_reason,
        "optimality_gap_pct": gap,
        **cal,
        "reason": reason,
    }


def _held_squad(wh, season: str | None,
                user: UserContext | None = None) -> list[int]:
    """The codes the manager holds right now, through the squad panel.

    The panel is the sanctioned read for this, so the plan check and the
    squad card can never disagree about what "your fifteen" means.
    """
    try:
        from fpl_edge.platform.scripts.squad import squad_overview

        sq = squad_overview(wh, season=season or SEASON_DEFAULT, ctx=user)
    except Exception:  # noqa: BLE001 - an unreadable squad is not a plan error
        return []
    if not isinstance(sq, dict) or sq.get("empty"):
        return []
    return [int(p["code"])
            for p in [*(sq.get("starters") or []), *(sq.get("bench") or [])]
            if p.get("code") is not None]


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
