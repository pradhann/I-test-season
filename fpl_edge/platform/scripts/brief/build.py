"""``dashboard_brief``: the assembler, and the panel registration.

Every number in the payload is a source panel's own. This module owns the
order the blocks run in and the one dict literal that is the payload; it
computes nothing itself."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.brief.plan import _moves, _solve_block, _verdict
from fpl_edge.platform.scripts.brief.schema import PARAMS, RESULT, THRESHOLDS
from fpl_edge.platform.scripts.brief.tiles import (
    BriefCtx,
    _best_xi,
    _calendar,
    _consensus_standouts,
    _creator_shift,
    _fixture_turns,
    _header_stats,
    _ownership_gates,
    _price_flow,
    _projection_provenance,
    _source_panels,
    _squad_checks,
    _squad_minutes,
    _standing,
)
from fpl_edge.platform.scripts.common import UTC, empty
from fpl_edge.platform.users import UserContext, owner_context


def dashboard_brief(wh, *, season: str,
                    ctx: UserContext | None = None) -> dict[str, Any]:
    """Select-and-threshold over the source panels; one payload, one clock set."""
    user = ctx if ctx is not None else owner_context()
    ctx = BriefCtx(
        wh=wh,
        season=season,
        now=dt.datetime.now(UTC),
        eid=int(user.entry_id),
        user=user,
        sources_as_of={},
        alerts=[],
        tiles=[],            # (gate margin, tile)
        watch=[],
        empties=[],
        notes=[],
    )
    sources_as_of = ctx.sources_as_of
    alerts, tiles, watch = ctx.alerts, ctx.tiles, ctx.watch
    empties, notes = ctx.empties, ctx.notes
    eid = ctx.eid

    cal = _calendar(ctx)
    if cal is None:
        return empty(
            f"No {season} events in the warehouse; without deadlines the "
            f"brief cannot date a single claim. Run `make ingest` first."
        )
    g_next, deadlines, next_deadline, last_deadline = cal

    sq, pr, own = _source_panels(ctx)
    xi_median, starters, squad15, suggested_xi = _squad_checks(ctx, sq)
    best_xi, squad_source, squad_codes, squad_team_codes = _best_xi(
        ctx, sq, starters, squad15, g_next, deadlines)
    tplan, tplan_named = _price_flow(ctx, pr, squad_codes)
    _ownership_gates(ctx, own, sq, squad15, squad_codes, xi_median)
    _consensus_standouts(ctx, sq, starters, squad_codes, g_next)
    team_fixtures, fixtures_scale, near = _fixture_turns(
        ctx, own, squad_team_codes, g_next)
    squad_projection, cons_next, proj_as_of = _squad_minutes(ctx, squad15, g_next)
    moves, moves_suppressed = _moves(
        ctx, sq, squad15, squad_codes, team_fixtures, near, cons_next,
        proj_as_of, g_next)
    _creator_shift(ctx)
    solve = _solve_block(ctx, pr, squad15, squad_codes, tplan, tplan_named,
                         g_next, next_deadline, last_deadline)
    xpts_source, xpts_as_of_v, p_haul_source, p_haul_generated = (
        _projection_provenance(sq))
    verdict, ch_rule, chip_val = _verdict(
        ctx, squad15, suggested_xi, solve, moves, proj_as_of, p_haul_generated)
    header = _header_stats(sq, solve, tplan, ch_rule, chip_val)
    standing = _standing(ctx)

    # ---- assemble --------------------------------------------------------
    alerts.sort(key=lambda a: (
        a["priority"],
        -max([abs(v) for v in a["numbers"].values() if v is not None] or [0.0]),
    ))

    # Rank: priority class first, then gate magnitude WITHIN a kind, kinds
    # interleaved — one flooding kind (31 xPts standouts on a normal Monday)
    # must not evict the rest of the catalogue from all six slots; that is the
    # squeeze the alert/tile split exists to prevent, applied inside the tiles.
    within: dict[str, int] = {}
    ranked: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
    for prio, margin, tile in sorted(tiles, key=lambda t: (t[0], -t[1])):
        idx = within.get(tile["kind"], 0)
        within[tile["kind"]] = idx + 1
        ranked.append(((prio, idx, -margin), tile))
    ranked.sort(key=lambda r: r[0])
    cap_n = int(THRESHOLDS["tile_cap"])
    kept = [t for _, t in ranked[:cap_n]]
    suppressed: dict[str, int] = {}
    for _, t in ranked[cap_n:]:
        suppressed[t["kind"]] = suppressed.get(t["kind"], 0) + 1

    # Oldest load-bearing contributing clock. The solve artefact's clock is
    # excluded: both of its clocks print on the solver card itself, and a
    # stale plan is a named gap, not a contributor to the alerts' age.
    load_bearing = [v for k, v in sources_as_of.items()
                    if v and k != "solve_plan"]
    as_of = min(load_bearing) if load_bearing else None

    deadline_utc = None
    if g_next is not None:
        row = deadlines[deadlines["gw"] == g_next]
        if not row.empty and row.iloc[0]["deadline_utc"] is not None:
            deadline_utc = str(row.iloc[0]["deadline_utc"]).replace(" ", "T")

    return {
        "season": season,
        "gw": g_next,
        "entry_id": eid,
        "as_of": as_of,
        "sources_as_of": sources_as_of,
        "deadline_utc": deadline_utc,
        "xi_median_xpts": xi_median,
        "thresholds": {k: float(v) for k, v in THRESHOLDS.items()},
        "alerts": alerts,
        "tiles": kept,
        "suppressed_counts": suppressed,
        "empty_kinds": empties,
        "watch_log": watch,
        "solve": solve,
        "suggested_xi": suggested_xi,
        "best_xi": best_xi,
        "squad_source": squad_source,
        "team_fixtures": team_fixtures,
        "squad_projection": squad_projection,
        "projection_gw": g_next,
        "moves": moves,
        "moves_suppressed": moves_suppressed,
        "verdict": verdict,
        "header": header,
        "standing": standing,
        "xpts_source": xpts_source,
        "xpts_as_of": xpts_as_of_v,
        "p_haul_source": p_haul_source,
        "p_haul_generated": p_haul_generated,
        "fixtures_scale": fixtures_scale,
        "notes": notes,
    }


register_script(
    "dashboard_brief",
    dashboard_brief,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Dashboard brief",
    description="Alerts, gated tiles, watch log and the solve state; "
                "selected and thresholded from the source panels, never "
                "recomputed. Thresholds echoed; every item cites its source.",
)
