"""planner_grid — one payload for the multi-gameweek transfer-planner grid.

The planner is a manual sandbox (fplreview's idiom): rows are your 15 plus any
candidate ins, columns are the next H gameweeks, cells are consensus xPts.
Everything the grid computes client-side — per-GW XI totals, bank after each
move, free transfers used/banked, hit costs — derives from THIS payload, so
the game rules ride along in it (``ft_entering``, ``rules.free_per_gw``,
``rules.max_banked``, ``rules.hit_cost``) rather than being hardcoded twice.
The rule values come from the verified registry (:mod:`fpl_edge.rules`), never
from literals here.

Squad access mirrors :mod:`fpl_edge.platform.scripts.squad`: the same
``QuestionRouter._team_state`` fallback chain (private API → public picks →
manual), short-circuited BEFORE any network call when the warehouse has no
players. Projections come from ``sem_projection_consensus`` at now — the
source-disagreement spread rides along as the uncertainty estimate.

v1 limitation, stated rather than hidden: selling uses the CURRENT price both
ways. FPL's real sell price keeps only half of any rise above your purchase
price (``prices.sell_on_fee_fraction``); modelling that needs per-player
purchase prices we do not reliably hold, so the payload carries a note and the
grid repeats it.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.config import USER
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    UTC,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
)
from fpl_edge.rules import rules

CANDIDATE_LIMIT = 150

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "horizon": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        "entry_id": {"type": ["integer", "null"], "default": None},
    },
}

_SQUAD_PLAYER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "price", "is_captain"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]}, "team_code": {"type": ["integer", "null"]},
        "price": {"type": "number"},
        "is_captain": {"type": "boolean"},
    },
}

_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "price"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]}, "team_code": {"type": ["integer", "null"]},
        "price": {"type": "number"},
        "own_pct": {"type": ["number", "null"]},
    },
}

#: {code(str) -> {gw(str) -> number}} — JSON object keys are strings.
_PER_GW_MAP = {
    "type": "object",
    "additionalProperties": {
        "type": "object",
        "additionalProperties": {"type": "number"},
    },
}

# Must not also match the registry's {empty, reason} oneOf branch: the empty
# shape lacks every required key here and additionalProperties is false.
RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "gws", "squad", "candidates", "xpts", "spread",
                 "ft_entering", "bank_tenths", "rules"],
    "properties": {
        "season": {"type": "string"},
        "gws": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
        "deadline_utc": {"type": ["string", "null"]},
        "provenance_source": {"type": "string"},
        "squad": {"type": "array", "items": _SQUAD_PLAYER, "minItems": 1},
        "candidates": {"type": "array", "items": _CANDIDATE},
        "xpts": _PER_GW_MAP,
        "spread": _PER_GW_MAP,
        "ft_entering": {"type": "integer", "minimum": 0},
        "bank_tenths": {"type": "integer"},
        "rules": {
            "type": "object",
            "additionalProperties": False,
            "required": ["free_per_gw", "max_banked", "hit_cost"],
            "properties": {
                "free_per_gw": {"type": "integer"},
                "max_banked": {"type": "integer"},
                # Points LOST per transfer beyond the free ones, as a positive
                # number (registry stores -4; the grid subtracts this).
                "hit_cost": {"type": "integer", "minimum": 0},
            },
        },
        "as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

_SOURCE_LABEL = {
    "PRIVATE_API": "live from your FPL account",
    "PUBLIC_PICKS": "public picks (published after the deadline)",
    "MANUAL": "as you entered it with /setsquad",
}


def planner_grid(
    wh,
    *,
    season: str,
    horizon: int = 5,
    entry_id: int | None = None,
) -> dict[str, Any]:
    """Your 15, candidate ins, and per-GW consensus xPts for the planner grid.

    One payload: squad (with prices), the top candidates by consensus xPts
    over the horizon, an {code -> {gw -> xPts}} map plus the cross-source
    spread, and the transfer rules (FTs entering, banking cap, hit cost) so
    the grid's arithmetic mirrors the verified rule registry.
    """
    eid = int(entry_id) if entry_id is not None else int(USER.entry_id)
    now = dt.datetime.now(UTC)

    players = q(
        wh,
        "SELECT code, web_name, position, team, team_code, price, selected_by_pct "
        "FROM sem_players(?) WHERE season = ?",
        (now, season),
    )
    if players.empty:
        return empty(
            f"No {season} players in the warehouse, so there is nothing to "
            f"plan with. Run `make ingest` first."
        )

    cons = q(
        wh,
        "SELECT gw, code, position, xpts_mean, xpts_spread "
        "FROM sem_projection_consensus(?) WHERE season = ?",
        (now, season),
    )
    if cons.empty:
        return empty(
            f"No provider projections for {season} in the warehouse, so the "
            f"grid would be all blanks. Ingest projections (`make ingest`) "
            f"and reload."
        )

    g0 = next_gw(wh, season, now)
    covered = sorted(int(g) for g in cons["gw"].unique())
    if g0 is None:
        g0 = covered[0]
    gws = [g for g in range(g0, g0 + int(horizon)) if g in set(covered)]
    notes: list[str] = []
    if not gws:
        return empty(
            f"The next deadline is GW{g0} but consensus projections only cover "
            f"GW{covered[0]}-GW{covered[-1]}. Re-ingest projections."
        )
    if len(gws) < int(horizon):
        notes.append(
            f"Horizon clamped to {len(gws)} GW(s): consensus projections stop "
            f"at GW{covered[-1]}."
        )

    # Squad: the same fallback chain the Telegram answers use, short-circuited
    # above so an empty warehouse never fires an HTTP request.
    try:
        from fpl_edge.interfaces.qa import QuestionRouter

        router = QuestionRouter(wh, season=season, entry_id=eid)
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 - a panel reports, it does not crash
        return empty(
            f"Could not read squad for entry {eid}: {type(exc).__name__}: {exc}. "
            f"Run `fpl myteam auth` once, or text /setsquad with your 15."
        )
    if state is None or state.picks is None:
        return empty(
            f"No squad visible for entry {eid} yet. FPL publishes picks only "
            f"after a deadline passes; until then run `fpl myteam auth` or "
            f"text /setsquad with your 15."
        )

    reg = rules()
    free_per_gw = int(reg.get("transfers.free_per_gw"))
    max_banked = int(reg.get("transfers.max_banked"))
    hit_cost = abs(int(reg.get("transfers.hit_cost")))

    by_code = {int(r["code"]): r for _, r in players.iterrows()}

    def cell(row, col, cast):
        v = None if row is None else row.get(col)
        return None if v is None or v != v else cast(v)

    squad: list[dict[str, Any]] = []
    for pick in state.picks:
        code = int(pick.code)
        row = by_code.get(code)
        squad.append({
            "code": code,
            "name": cell(row, "web_name", str) or str(code),
            "pos": POSITION_NAME.get(cell(row, "position", int) or 0, "?"),
            "team": cell(row, "team", str),
            "team_code": cell(row, "team_code", int),
            "price": cell(row, "price", float) or 0.0,
            "is_captain": bool(pick.is_captain),
        })
    squad_codes = {p["code"] for p in squad}
    missing = [p["name"] for p in squad if p["pos"] == "?"]
    if missing:
        notes.append(
            f"{len(missing)} squad player(s) not found in the warehouse "
            f"({', '.join(missing[:3])}…): price shown as 0.0."
        )

    # Candidates: top by summed consensus xPts over the horizon, squad excluded.
    horizon_cons = cons[cons["gw"].isin(gws)]
    ranked = (
        horizon_cons.groupby("code", as_index=False)["xpts_mean"].sum()
        .sort_values("xpts_mean", ascending=False)
    )
    candidates: list[dict[str, Any]] = []
    for _, r in ranked.iterrows():
        code = int(r["code"])
        if code in squad_codes:
            continue
        row = by_code.get(code)
        if row is None:
            continue  # projected but unknown to the warehouse: unpickable
        candidates.append({
            "code": code,
            "name": cell(row, "web_name", str) or str(code),
            "pos": POSITION_NAME.get(cell(row, "position", int) or 0, "?"),
            "team": cell(row, "team", str),
            "team_code": cell(row, "team_code", int),
            "price": cell(row, "price", float) or 0.0,
            "own_pct": cell(row, "selected_by_pct", float),
        })
        if len(candidates) >= CANDIDATE_LIMIT:
            break

    wanted = squad_codes | {c["code"] for c in candidates}
    xpts: dict[str, dict[str, float]] = {}
    spread: dict[str, dict[str, float]] = {}
    for _, r in horizon_cons.iterrows():
        code = int(r["code"])
        if code not in wanted:
            continue
        gw_key, code_key = str(int(r["gw"])), str(code)
        if r["xpts_mean"] == r["xpts_mean"]:
            xpts.setdefault(code_key, {})[gw_key] = round(float(r["xpts_mean"]), 3)
        if r["xpts_spread"] is not None and r["xpts_spread"] == r["xpts_spread"]:
            spread.setdefault(code_key, {})[gw_key] = round(float(r["xpts_spread"]), 3)

    deadline = q(
        wh,
        "SELECT deadline_utc FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, gw ORDER BY as_of DESC) rn"
        "  FROM dim_event WHERE season = ? AND gw = ?"
        ") WHERE rn = 1",
        (season, gws[0]),
    )
    deadline_utc = (
        None if deadline.empty or deadline.iloc[0]["deadline_utc"] is None
        else str(deadline.iloc[0]["deadline_utc"]).replace(" ", "T")
    )

    ft = int(getattr(state, "free_transfers", None) or free_per_gw)
    source = getattr(state.provenance, "name", str(state.provenance))
    notes.append(
        "Sell prices are simplified: current price both ways. FPL's 50% "
        "sell-on fee on price rises is not modelled in v1."
    )

    return {
        "season": season,
        "gws": gws,
        "deadline_utc": deadline_utc,
        "provenance_source": _SOURCE_LABEL.get(source, source),
        "squad": squad,
        "candidates": candidates,
        "xpts": xpts,
        "spread": spread,
        "ft_entering": max(0, min(ft, max_banked)),
        "bank_tenths": int(getattr(state, "bank_tenths", None) or 0),
        "rules": {
            "free_per_gw": free_per_gw,
            "max_banked": max_banked,
            "hit_cost": hit_cost,
        },
        "as_of": latest_as_of(wh, "fact_player_state", season),
        "notes": notes,
    }


register_script(
    name="planner_grid",
    fn=planner_grid,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Transfer planner grid",
    description="Squad, per-GW consensus projections, prices and transfer "
                "rules in one payload for the planner grid.",
)
