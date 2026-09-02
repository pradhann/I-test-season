"""squad_overview — your actual 15, priced, flagged and projected.

Reuses :meth:`QuestionRouter._team_state` rather than re-deriving the squad.
That method already encodes a decision worth not relitigating: try the private
API, fall back to public picks, fall back to the manually-entered squad, and
record *which* of the three answered in ``state.provenance``. Reimplementing it
here would produce a panel and a Telegram answer that disagree about your team,
which is the single worst failure this platform can have.

Two consequences of that reuse are visible in the code below:

* ``FPL_EDGE_DISABLE_PRIVATE`` is honoured through the router, not re-checked
  here -- the guard belongs at the client, and a second copy would drift.
* The squad lookup can touch the network, and a panel must not hang or explode
  because the FPL API is down at 3am. Every failure becomes an honest empty
  naming what could not be reached.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    empty,
    latest_as_of,
    load_projection,
    q,
    season_param,
)
from fpl_edge.config import USER

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "entry_id": {"type": ["integer", "null"], "default": None},
    },
}

_PLAYER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "price", "is_starter"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "price": {"type": "number"},
        "own_pct": {"type": ["number", "null"]},
        "is_starter": {"type": "boolean"},
        "is_captain": {"type": "boolean"},
        "is_vice": {"type": "boolean"},
        "multiplier": {"type": ["integer", "null"]},
        "status": {"type": ["string", "null"]},
        "news": {"type": ["string", "null"]},
        "xpts": {"type": ["number", "null"]},
        "p_haul": {"type": ["number", "null"]},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "entry_id", "provenance_source", "starters", "bench"],
    "properties": {
        "season": {"type": "string"},
        "entry_id": {"type": "integer"},
        "team_name": {"type": ["string", "null"]},
        "gw": {"type": ["integer", "null"]},
        "provenance_source": {"type": "string"},
        "bank_tenths": {"type": ["integer", "null"]},
        "squad_value_tenths": {"type": ["integer", "null"]},
        "projected_xi_xpts": {"type": ["number", "null"]},
        "captain": {"type": ["string", "null"]},
        "vice": {"type": ["string", "null"]},
        # The DATA-BIRTH instant of the xpts/p_haul columns: when the solved
        # projection artefact was generated (the model RUN, not this panel's
        # read time), and what that source actually is — so no view can label
        # these numbers "consensus". Null when no artefact is cached.
        "projection_generated": {"type": ["string", "null"]},
        "projection_source": {"type": ["string", "null"]},
        "starters": {"type": "array", "items": _PLAYER},
        "bench": {"type": "array", "items": _PLAYER},
        # Chip ledger from MyTeamState.chip_status() — the rules registry's
        # windows and the entry's played chips, reused, never re-derived.
        # Empty when the squad source cannot say (e.g. manual entry).
        "chips": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["chip", "windows", "played"],
                "properties": {
                    "chip": {"type": "string"},
                    "windows": {"type": "array",
                                "items": {"type": "array",
                                          "items": {"type": "integer"}}},
                    "played": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
        "flags": {"type": "array", "items": {"type": "string"}},
        "as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

_SOURCE_LABEL = {
    "PRIVATE_API": "live from your FPL account",
    "PUBLIC_PICKS": "public picks (published after the deadline)",
    "MANUAL": "as you entered it with /setsquad",
}


def squad_overview(wh, *, season: str, entry_id: int | None = None) -> dict[str, Any]:
    """Your 15 with price, availability and this gameweek's projection."""
    eid = int(entry_id) if entry_id is not None else int(USER.entry_id)

    players = q(
        wh,
        """
        SELECT s.code, p.web_name, p.position, t.short_name AS team,
               p.team_code,
               s.price_tenths, s.selected_by_pct, s.status, s.news
        FROM (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY season, code
                                             ORDER BY as_of DESC) rn
                FROM fact_player_state WHERE season = ?
            ) WHERE rn = 1
        ) s
        JOIN (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY season, code
                                             ORDER BY as_of DESC) rn
                FROM dim_player WHERE season = ?
            ) WHERE rn = 1
        ) p USING (season, code)
        LEFT JOIN (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, row_number() OVER (PARTITION BY season, team_code
                                             ORDER BY as_of DESC) rn
                FROM dim_team WHERE season = ?
            ) WHERE rn = 1
        ) t ON t.team_code = p.team_code
        """,
        (season, season, season),
    )
    if players.empty:
        return empty(
            f"No {season} players in the warehouse, so a squad could not be "
            f"described even if it were fetched. Run `make ingest` first."
        )

    try:
        from fpl_edge.interfaces.qa import QuestionRouter

        router = QuestionRouter(wh, season=season, entry_id=eid)
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 - a panel reports, it does not crash
        return empty(
            f"Could not read squad for entry {eid}: {type(exc).__name__}: {exc}. "
            f"Before GW1 FPL publishes nothing publicly -- run `fpl myteam auth` "
            f"once, or text /setsquad with your 15."
        )

    if state is None or state.picks is None:
        return empty(
            f"No squad visible for entry {eid} yet. FPL publishes picks only "
            f"after a deadline passes; until GW1 locks, either run "
            f"`fpl myteam auth` (reads it live) or text /setsquad with your 15."
        )

    by_code = {int(r["code"]): r for _, r in players.iterrows()}
    proj = load_projection(wh)
    xpts, phaul = {}, {}
    notes: list[str] = []
    projection_generated = None
    projection_source = None
    if proj is None:
        notes.append(
            "No projection artefact cached, so xPts columns are null. "
            "Run `make solve` to fill them."
        )
    else:
        xpts = dict(zip(proj["code"].astype(int), proj["xpts"]))
        if "p_haul" in proj.columns:
            phaul = dict(zip(proj["code"].astype(int), proj["p_haul"]))
        # Data-birth instant of the xPts columns: the artefact's own write
        # time — the model RUN, not this panel's state read. Named so the
        # view never captions a solved number as a consensus.
        import datetime as dt

        from fpl_edge.platform.scripts.common import (
            PROJECTION_NAME,
            source_dir,
        )

        artefact = source_dir(wh) / PROJECTION_NAME
        if artefact.exists():
            projection_generated = dt.datetime.fromtimestamp(
                artefact.stat().st_mtime, dt.UTC).isoformat()
        projection_source = (
            f"solved artefact {PROJECTION_NAME} — the engine's own "
            f"simulation run, not the provider consensus")

    def card(pick) -> dict[str, Any]:
        code = int(pick.code)
        row = by_code.get(code)
        def cell(col, cast=str):
            if row is None:
                return None
            v = row.get(col)
            return None if v is None or v != v else cast(v)
        return {
            "code": code,
            "name": cell("web_name") or str(code),
            "pos": POSITION_NAME.get(int(row["position"]) if row is not None else 0, "?"),
            "team": cell("team"),
            "team_code": cell("team_code", int),
            "price": round(float(row["price_tenths"]) / 10.0, 1) if row is not None else 0.0,
            "own_pct": cell("selected_by_pct", float),
            "is_starter": bool(pick.is_starter),
            "is_captain": bool(pick.is_captain),
            "is_vice": bool(pick.is_vice),
            "multiplier": int(getattr(pick, "multiplier", 0) or 0),
            "status": cell("status"),
            "news": cell("news"),
            "xpts": round(float(xpts[code]), 3) if code in xpts else None,
            "p_haul": round(float(phaul[code]), 4) if code in phaul else None,
        }

    starters = [card(p) for p in state.picks if p.is_starter]
    bench = [card(p) for p in state.picks if not p.is_starter]

    flags = [
        f"{c['name']}: {c['status']}" + (f" — {c['news']}" if c["news"] else "")
        for c in starters + bench
        if c["status"] in ("i", "s", "d", "u")
    ]
    xi_total = sum(c["xpts"] or 0.0 for c in starters) if xpts else None
    cap = next((c["name"] for c in starters + bench if c["is_captain"]), None)
    vice = next((c["name"] for c in starters + bench if c["is_vice"]), None)
    value = sum(int(round(c["price"] * 10)) for c in starters + bench)

    # Chip ledger: reuse the state's own chip_status() (rules-registry
    # windows + played chips). A source that cannot say serves [] plus a note
    # rather than a guessed "all available".
    chips: list[dict[str, Any]] = []
    try:
        for cs in (state.chip_status() if hasattr(state, "chip_status") else ()):
            chips.append({
                "chip": str(cs.chip),
                "windows": [[int(a), int(b)] for a, b in cs.windows],
                "played": [int(g) for g in cs.played],
            })
    except Exception as exc:  # noqa: BLE001 - chips are context, not the squad
        chips = []
        notes.append(f"chip status unavailable: {type(exc).__name__}: {exc}")
    if not chips:
        notes.append("chip ledger not served by this squad source — "
                     "used/available chips are unknown, not all-available.")

    source = getattr(state.provenance, "name", str(state.provenance))
    return {
        "season": season,
        "entry_id": eid,
        "team_name": getattr(USER, "team_name", None) if eid == int(USER.entry_id) else None,
        "gw": int(state.gw) if getattr(state, "gw", None) is not None else None,
        "provenance_source": _SOURCE_LABEL.get(source, source),
        "bank_tenths": int(state.bank.tenths) if getattr(state, "bank", None) else None,
        "squad_value_tenths": value,
        "projected_xi_xpts": round(float(xi_total), 2) if xi_total is not None else None,
        "captain": cap,
        "vice": vice,
        "projection_generated": projection_generated,
        "projection_source": projection_source,
        "starters": starters,
        "bench": bench,
        "chips": chips,
        "flags": flags,
        "as_of": latest_as_of(wh, "fact_player_state", season),
        "notes": notes,
    }


register_script(
    "squad_overview",
    squad_overview,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Squad overview",
    description="Your 15 with price, availability and projected points, and where the squad was read from.",
)
