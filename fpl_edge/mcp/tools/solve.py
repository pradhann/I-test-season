"""The three solver tools: read the plan, start a solve, poll it.

One tool used to do all three, and it did the solve inside the call. That took
one to five minutes against a 10 second panel budget, which is not a slow tool,
it is a tool that cannot be called. Work known to exceed the budget is refused
before it starts, and this is the route that does it asynchronously.

So: ``transfer_plan`` reads the committed plan through the Planner's own panel,
which is the same grid the Planner tab draws. ``solve_start`` spawns one solve
through ``fpl_edge/platform/solve_runner.py``, single flight, and returns
immediately. ``solve_status`` polls it. Three turns instead of one, and the
numbers are now the same numbers the page shows.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp import context
from fpl_edge.mcp.adapter import error, panel_call, refusal, store_call
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"

#: The store every write in this module goes through.
STORE = "fpl_edge.platform.solve_runner"


@mcp.tool()
def transfer_plan(
    season: str = SEASON_DEFAULT,
    horizon: int = 5,
) -> dict[str, Any]:
    """The committed transfer plan across the horizon, as the Planner draws it.

    Each gameweek's moves with the projected points and the cost of each,
    against the squad this server was started for. This reads the plan the
    last solve committed; it does not solve. Start a fresh one with
    solve_start when the plan is stale, and the payload says when it was
    written.

    Args:
        season: FPL season, for example "2026-27".
        horizon: How many gameweeks the grid covers, 1 to 10.

    Returns:
        The envelope: result, provenance, budget, and gap when no plan has
        been solved. If gap is present, quote its reason.
    """
    return panel_call("transfer_plan", "planner_grid", {
        "season": season, "horizon": horizon,
    })


@mcp.tool()
def solve_start(
    horizon: int = 5,
    max_hits: int = 0,
    seconds: int = 150,
    max_candidates: int = 20,
    must_keep: list[int] | None = None,
    ban: list[int] | None = None,
    chips: list[str] | None = None,
) -> dict[str, Any]:
    """Start the transfer solve in the background and return at once. WRITES.

    The solve is a MILP over the forecast and it takes one to five minutes, so
    it does not happen inside this call. This spawns it, single flight, and
    returns the status immediately. Poll with solve_status, then read the
    result with transfer_plan.

    A solve is already running when the status says so; this returns that one
    rather than starting a second.

    Chips are off unless asked for: playing one is the user's decision, not the
    objective's. Hits are capped at zero for the headline plan, and the
    optimiser's own hit-taking best rides along in the result.

    Args:
        horizon: Gameweeks to plan over, 1 to 8.
        max_hits: Point hits the plan may take. 0 for none, -1 for unlimited.
        seconds: Time budget per MILP, 10 to 900. Sixty seconds found no
            incumbent on a five-gameweek problem; the default did.
        max_candidates: Candidate squads to try, 5 to 80.
        must_keep: Player codes the plan may not sell.
        ban: Player codes the plan may not buy.
        chips: Any of wildcard, freehit, bboost, 3xc to allow.

    Returns:
        The envelope with store naming the module that spawned it, and result
        holding the state, the mode, the options and the log tail.
    """
    from fpl_edge.platform import solve_runner

    options: dict[str, Any] = {
        "horizon": horizon, "max_hits": max_hits, "seconds": seconds,
        "max_candidates": max_candidates,
        "must_keep": tuple(must_keep or ()), "ban": tuple(ban or ()),
        "chips": tuple(chips or ()),
    }
    try:
        started = solve_runner.start("transfers", options=options)
    except ValueError as exc:
        return refusal("solve_start", str(exc))
    except Exception as exc:  # noqa: BLE001
        return error("solve_start", f"{type(exc).__name__}: {exc}",
                     kind=type(exc).__name__)

    if started.get("already_running"):
        started["note"] = (
            "a solve was already running, so this returned that one rather "
            "than starting a second. Exactly one runs at a time."
        )
    else:
        started["note"] = (
            "started. Poll solve_status, then read the result with "
            "transfer_plan. Expect one to five minutes."
        )
    return store_call("solve_start", STORE, started)


@mcp.tool()
def solve_status() -> dict[str, Any]:
    """Whether a solve is running, and the tail of its log.

    State, mode, the options it was started with, when it started and
    finished, and the last lines it printed. An abandoned worker is reconciled
    here rather than reported as running forever.

    Args:
        None.

    Returns:
        The envelope with store naming the module that answered, and result
        holding the reconciled status.
    """
    from fpl_edge.platform import solve_runner

    try:
        current = solve_runner.status()
    except Exception as exc:  # noqa: BLE001
        return error("solve_status", f"{type(exc).__name__}: {exc}",
                     kind=type(exc).__name__)
    return store_call("solve_status", STORE, current)
