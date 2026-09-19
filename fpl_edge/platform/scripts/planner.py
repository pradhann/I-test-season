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

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    UTC,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
    source_dir,
)
from fpl_edge.platform.users import PRIVATE_GAP, UserContext, owner_context
from fpl_edge.rules import rules

CANDIDATE_LIMIT = 1200   # sanity ceiling; the pool is ALL players

#: ``entry_id`` is NOT a param, for the reason given on ``squad_overview``'s:
#: the id and the FPL login always come from the same object, the request's
#: user context, so neither can name a team the other does not own.
PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "horizon": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        "view": {
            "type": "string",
            "enum": ["grid", "headline"],
            "default": "grid",
            "description": "'grid' is the planner's own payload: your 15 plus "
                           "every selectable player and their per-gameweek "
                           "numbers. 'headline' drops the candidate pool and "
                           "keeps the per-gameweek numbers for your 15 alone, "
                           "so the plan, the armband and the alternatives fit "
                           "inside a caller's payload cap. Both views carry "
                           "`plan`.",
        },
    },
}

#: The committed plan's headline, lifted out of transfer_plan.json beside the
#: warehouse this run was given. Codes only: the grid already resolves every
#: name it needs, and inventing a second name table here would be a second
#: place for a name to be wrong.
_PLAN_HEADLINE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["generated_at", "gw", "horizon_gws"],
    "properties": {
        "generated_at": {"type": ["string", "null"]},
        "gw": {"type": ["integer", "null"]},
        "horizon_gws": {"type": "array", "items": {"type": "integer"}},
        "objective_mode": {"type": ["string", "null"]},
        "forecast_source": {"type": ["string", "null"]},
        "free_transfers": {"type": ["integer", "null"]},
        "free_transfers_source": {"type": ["string", "null"]},
        "gain_over_roll": {"type": ["number", "null"]},
        "chosen": {"type": ["object", "null"]},
        "alternatives": {"type": "array"},
        "unconstrained": {"type": ["object", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": ["string", "null"],
                   "description": "why there is no plan, when there is none"},
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
        "xmins": _PER_GW_MAP,
        "p_appear": _PER_GW_MAP,
        "metrics": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "gp": {"type": "integer"}, "mins": {"type": "number"},
                    "goals": {"type": "number"}, "assists": {"type": "number"},
                    "xg": {"type": ["number", "null"]},
                    "xa": {"type": ["number", "null"]},
                    "shots": {"type": ["number", "null"]},
                },
            },
        },
        "metrics_note": {"type": "string"},
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
        "view": {"enum": ["grid", "headline"]},
        # The committed plan's headline. The MCP tool called `transfer_plan`
        # wrapped this panel and got a grid with no plan in it, so an agent
        # asking for the plan received 1,200 candidate players and none of the
        # solver's own decisions. Served in both views because a grid without
        # the standing plan beside it is the same gap.
        "plan": _PLAN_HEADLINE,
    },
}

_SOURCE_LABEL = {
    "PRIVATE_API": "live from your FPL account",
    "PUBLIC_PICKS": "public picks (published after the deadline)",
    "MANUAL": "as you entered it with /setsquad",
}


def _plan_headline(wh) -> dict[str, Any] | None:
    """transfer_plan.json's headline, from beside the warehouse this run used.

    Codes and numbers only, verbatim. Freshness is not judged here: the
    dashboard brief and routes_solve both own that rule and this panel must
    not become a third opinion about whether a plan may be shown. The
    artefact's own ``generated_at`` travels with it so the caller can judge.
    """
    import json
    from pathlib import Path

    path = Path(source_dir(wh)) / "transfer_plan.json"
    if not path.exists():
        return {"generated_at": None, "gw": None, "horizon_gws": [],
                "reason": (f"no {path.name} beside this warehouse; the "
                           f"auto_resolve task writes one after each "
                           f"deadline, or solve now.")}
    try:
        plan = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return {"generated_at": None, "gw": None, "horizon_gws": [],
                "reason": f"{path.name} unreadable: {type(exc).__name__}: {exc}"}
    keep = ("generated_at", "gw", "horizon_gws", "objective_mode",
            "forecast_source", "free_transfers", "free_transfers_source",
            "gain_over_roll", "chosen", "alternatives", "unconstrained",
            "notes")
    out = {k: plan.get(k) for k in keep if k in plan}
    out.setdefault("generated_at", None)
    out.setdefault("gw", None)
    out.setdefault("horizon_gws", [])
    out.setdefault("alternatives", [])
    out.setdefault("notes", [])
    out["reason"] = None
    return out


def planner_grid(
    wh,
    *,
    season: str,
    horizon: int = 5,
    view: str = "grid",
    ctx: UserContext | None = None,
) -> dict[str, Any]:
    """Your 15, candidate ins, and per-GW consensus xPts for the planner grid.

    One payload: squad (with prices), the top candidates by consensus xPts
    over the horizon, an {code -> {gw -> xPts}} map plus the cross-source
    spread, and the transfer rules (FTs entering, banking cap, hit cost) so
    the grid's arithmetic mirrors the verified rule registry.
    """
    ctx = ctx if ctx is not None else owner_context()
    eid = int(ctx.entry_id)
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
        "SELECT gw, code, position, xpts_mean, xpts_spread, xmins_mean "
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

        router = QuestionRouter(wh, season=season, entry_id=eid, user=ctx)
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 - a panel reports, it does not crash
        if not ctx.can_read_private:
            return empty(
                f"Could not read squad for entry {eid}: "
                f"{type(exc).__name__}: {exc}. {PRIVATE_GAP}"
            )
        return empty(
            f"Could not read squad for entry {eid}: {type(exc).__name__}: {exc}. "
            f"Run `fpl myteam auth` once, or text /setsquad with your 15."
        )
    if state is None or state.picks is None:
        if not ctx.can_read_private:
            return empty(f"No squad visible for entry {eid} yet: {PRIVATE_GAP}")
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

    # The pool is EVERY player the warehouse knows (the FPL-site idiom: browse
    # anyone, not a pre-trimmed shortlist). Ordered by summed consensus xPts
    # over the horizon; players no source projects sink to the bottom but stay
    # selectable -- their grid cells are honest dashes, not zeros.
    horizon_cons = cons[cons["gw"].isin(gws)]
    xsum = horizon_cons.groupby("code")["xpts_mean"].sum().to_dict()
    candidates: list[dict[str, Any]] = []
    for code, row in by_code.items():
        if code in squad_codes:
            continue
        candidates.append({
            "code": int(code),
            "name": cell(row, "web_name", str) or str(code),
            "pos": POSITION_NAME.get(cell(row, "position", int) or 0, "?"),
            "team": cell(row, "team", str),
            "team_code": cell(row, "team_code", int),
            "price": cell(row, "price", float) or 0.0,
            "own_pct": cell(row, "selected_by_pct", float),
        })
    candidates.sort(key=lambda c: (-(xsum.get(c["code"], 0.0)), c["name"]))
    del candidates[CANDIDATE_LIMIT:]

    wanted = squad_codes | {c["code"] for c in candidates}
    xpts: dict[str, dict[str, float]] = {}
    spread: dict[str, dict[str, float]] = {}
    xmins: dict[str, dict[str, float]] = {}
    p_appear: dict[str, dict[str, float]] = {}
    for _, r in horizon_cons.iterrows():
        code = int(r["code"])
        if code not in wanted:
            continue
        gw_key, code_key = str(int(r["gw"])), str(code)
        if r["xpts_mean"] == r["xpts_mean"]:
            xpts.setdefault(code_key, {})[gw_key] = round(float(r["xpts_mean"]), 3)
        if r["xpts_spread"] is not None and r["xpts_spread"] == r["xpts_spread"]:
            spread.setdefault(code_key, {})[gw_key] = round(float(r["xpts_spread"]), 3)
        if r.get("xmins_mean") is not None and r["xmins_mean"] == r["xmins_mean"]:
            xmins.setdefault(code_key, {})[gw_key] = round(float(r["xmins_mean"]), 1)

    # Minutes risk: p(appear) per (code, gw), averaged over the sources that
    # publish it (fplform today). xmins would be preferable but no source
    # publishes it beyond GW1; the map stays and fills if that changes.
    pa = q(
        wh,
        "SELECT code, gw, AVG(p_appear) pa FROM sem_projections(?) "
        "WHERE season = ? AND p_appear IS NOT NULL GROUP BY 1, 2",
        (now, season),
    )
    for _, r in pa.iterrows():
        if int(r["gw"]) in set(gws):
            p_appear.setdefault(str(int(r["code"])), {})[str(int(r["gw"]))] = round(float(r["pa"]), 3)

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

    # Season-to-date metrics. Two sources, separated by what each one knows.
    #
    # The OFFICIAL settled return (sem_player_form) carries minutes, goals,
    # assists AND expected_goals/expected_assists, for every player, in every
    # settled gameweek: 610/610 rows in GW1, 626/626 in GW2, 654/654 in GW3.
    # It is the xG source here.
    #
    # The third party (sem_player_match_stats) is read ONLY for shots, which
    # the official feed does not publish per player. That feed writes a row per
    # player-match and leaves an event column NULL when the count is zero, so
    # xG was NULL for 252 of the 400 GW1 rows -- everyone who took no shot.
    # Reading those NULLs as unknown blanked the xG column for most of the
    # squad while the complete official number sat one table away.
    #
    # NULL-means-zero is not an assumption: summed under that reading the
    # publisher's goals reconcile with the official settled goals for 400 of
    # 400 GW1 players, exactly. Shots are therefore summed with COALESCE, and a
    # player with no row at all still gets NULL rather than a fabricated zero.
    metrics: dict[str, dict[str, Any]] = {}

    def nn(v, default=0.0):
        """nan-safe float: `NaN or 0` keeps NaN because NaN is truthy."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return default if f != f else f

    def r2(v):
        """Round a summed metric, or None when the source has no row at all."""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return None if f != f else round(f, 2)

    form = q(
        wh,
        "SELECT code, COUNT(*) gp, SUM(minutes) mins, SUM(goals_scored) goals, "
        "SUM(assists) assists, SUM(expected_goals) xg, SUM(expected_assists) xa "
        "FROM sem_player_form(?) WHERE season = ? GROUP BY 1",
        (now, season),
    )
    for _, r in form.iterrows():
        metrics[str(int(r["code"]))] = {
            "gp": int(r["gp"]), "mins": nn(r["mins"]),
            "goals": nn(r["goals"]), "assists": nn(r["assists"]),
            "xg": r2(r["xg"]), "xa": r2(r["xa"]), "shots": None,
        }
    third = q(
        wh,
        "SELECT code, COUNT(*) gp, SUM(minutes_played) mins, SUM(goals) goals, "
        "SUM(assists) assists, SUM(xg) xg, SUM(xa) xa, "
        "SUM(COALESCE(total_shots, 0)) shots "
        "FROM sem_player_match_stats(?) WHERE season = ? GROUP BY 1",
        (now, season),
    )
    for _, r in third.iterrows():
        key = str(int(r["code"]))
        m = metrics.get(key)
        if m is None:
            # Not settled officially yet: the publisher is all we have, and its
            # NULL xG genuinely means the player took no shot.
            metrics[key] = {
                "gp": int(r["gp"]), "mins": nn(r["mins"]),
                "goals": nn(r["goals"]), "assists": nn(r["assists"]),
                "xg": r2(r["xg"]) or 0.0, "xa": r2(r["xa"]) or 0.0,
                "shots": r2(r["shots"]),
            }
            continue
        m["shots"] = r2(r["shots"])
    settled = not form.empty
    metrics_note = (
        f"Season-to-date over {season}. Minutes, goals, assists, xG and xA "
        + ("from the official settled returns"
           if settled else
           "from the publisher's per-match feed, official returns not settled "
           "yet")
        + "; shots from FPL-Core-Insights, which the official feed does not "
          "publish."
    )

    ft = int(getattr(state, "free_transfers", None) or free_per_gw)
    source = getattr(state.provenance, "name", str(state.provenance))
    notes.append(
        "Sell prices are simplified: current price both ways. FPL's 50% "
        "sell-on fee on price rises is not modelled in v1."
    )

    # The headline view is the same payload with the pool taken out: the 15,
    # their per-gameweek numbers, the rules and the standing plan. That is what
    # an agent asking "what does the plan say" needs, and it is two orders of
    # magnitude smaller than 1,200 candidates times five gameweeks times four
    # maps, which is what put this tool over its caller's payload cap.
    headline = str(view) == "headline"
    if headline:
        keep_codes = {str(c) for c in squad_codes}
        candidates = []
        xpts = {k: v for k, v in xpts.items() if k in keep_codes}
        spread = {k: v for k, v in spread.items() if k in keep_codes}
        xmins = {k: v for k, v in xmins.items() if k in keep_codes}
        p_appear = {k: v for k, v in p_appear.items() if k in keep_codes}
        metrics = {k: v for k, v in metrics.items() if k in keep_codes}
        notes.append(
            "headline view: the candidate pool is empty and the per-gameweek "
            "maps cover your 15 only. Ask for view=\"grid\" to browse "
            "transfer targets."
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
        "xmins": xmins,
        "p_appear": p_appear,
        "metrics": metrics,
        "metrics_note": metrics_note,
        "view": "headline" if headline else "grid",
        "plan": _plan_headline(wh),
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
