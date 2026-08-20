"""projection_table — the player board, joined to live price and ownership.

The projection artefact is a *simulation output* frozen at solve time; price
and ownership move every day. Rendering the artefact alone shows a price that
was true last Tuesday next to a projection, which is exactly the kind of
quietly-stale panel that gets trusted. So the artefact supplies the
distributional columns (xpts, p10, p90, p_haul) and the warehouse supplies the
identity and market columns as of now, and the response reports both instants.
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

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "position": {
            "type": ["integer", "null"],
            "enum": [1, 2, 3, 4, None],
            "default": None,
            "description": "1 GKP, 2 DEF, 3 MID, 4 FWD; null for all.",
        },
        "sort": {
            "type": "string",
            "enum": ["xpts", "p_haul", "value", "price", "own"],
            "default": "xpts",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 800, "default": 50},
        "max_price": {"type": ["number", "null"], "default": None},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "rows", "row_count", "sort", "projection_generated"],
    "properties": {
        "season": {"type": "string"},
        "sort": {"type": "string"},
        "row_count": {"type": "integer"},
        "projection_generated": {"type": ["string", "null"]},
        "state_as_of": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "name", "pos", "price", "own_pct", "xpts"],
                "properties": {
                    "code": {"type": "integer"},
                    "name": {"type": "string"},
                    "pos": {"type": "string"},
                    "team": {"type": ["string", "null"]},
                    "price": {"type": "number"},
                    "own_pct": {"type": ["number", "null"]},
                    "xpts": {"type": "number"},
                    "p10": {"type": ["number", "null"]},
                    "p90": {"type": ["number", "null"]},
                    "p_haul": {"type": ["number", "null"]},
                    "value": {"type": ["number", "null"]},
                    "status": {"type": ["string", "null"]},
                },
            },
        },
    },
}


def projection_table(
    wh,
    *,
    season: str,
    position: int | None = None,
    sort: str = "xpts",
    limit: int = 50,
    max_price: float | None = None,
) -> dict[str, Any]:
    """Projected points per player with the spread, priced and owned as of now.

    Returns empty when no projection artefact has been solved, because a points
    projection is the one column on this panel that cannot be reconstructed
    from the warehouse alone.
    """
    proj = load_projection(wh)
    if proj is None:
        return empty(
            "No projection artefact cached. Run `make solve` to write "
            "data/warehouse/gw1_projection.parquet, then reload this panel."
        )
    if proj.empty:
        return empty("The projection artefact exists but contains no players.")

    state = q(
        wh,
        """
        SELECT s.code, p.web_name, p.position, t.short_name AS team,
               s.price_tenths, s.selected_by_pct, s.status
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
    notes: list[str] = []
    if state.empty:
        notes.append(
            f"No {season} player state in the warehouse, so price, ownership and "
            f"availability come from the projection artefact and may be stale."
        )
        merged = proj.copy()
        merged["team"] = None
        merged["status"] = None
    else:
        # The warehouse wins on every column both sides carry: the artefact's
        # copies of price/ownership/name are a snapshot from solve time, and
        # showing them beside a live projection is the stale-panel trap this
        # script exists to avoid.
        overlap = [c for c in ("web_name", "position", "price_tenths", "selected_by_pct")
                   if c in proj.columns and c in state.columns]
        merged = proj.drop(columns=overlap).merge(state, on="code", how="inner")
        if merged.empty:
            return empty(
                f"The projection artefact and the {season} warehouse rows share no "
                f"player codes -- the artefact is probably from another season."
            )

    if position is not None:
        merged = merged[merged["position"] == position]
    if max_price is not None:
        merged = merged[merged["price_tenths"] <= max_price * 10]
    if merged.empty:
        return empty("No player matches that position/price filter.")

    merged["value"] = merged["xpts"] / (merged["price_tenths"] / 10.0).clip(lower=0.1)
    key = {"xpts": "xpts", "p_haul": "p_haul", "value": "value",
           "price": "price_tenths", "own": "selected_by_pct"}[sort]
    merged = merged.sort_values(key, ascending=False).head(int(limit))

    def num(row, col):
        v = row.get(col)
        return None if v is None or v != v else float(v)

    rows = []
    for _, r in merged.iterrows():
        rows.append({
            "code": int(r["code"]),
            "name": str(r["web_name"]),
            "pos": POSITION_NAME.get(int(r["position"]), str(r["position"])),
            "team": None if r.get("team") is None or r.get("team") != r.get("team")
                    else str(r["team"]),
            "price": round(float(r["price_tenths"]) / 10.0, 1),
            "own_pct": num(r, "selected_by_pct"),
            "xpts": round(float(r["xpts"]), 3),
            "p10": num(r, "p10"),
            "p90": num(r, "p90"),
            "p_haul": num(r, "p_haul"),
            "value": round(float(r["value"]), 3),
            "status": None if r.get("status") is None or r.get("status") != r.get("status")
                      else str(r["status"]),
        })

    generated = None
    path = getattr(wh, "source_path", None)
    if path is not None:
        import datetime as dt
        from pathlib import Path

        artefact = Path(path).parent / "gw1_projection.parquet"
        if artefact.exists():
            generated = dt.datetime.fromtimestamp(
                artefact.stat().st_mtime, dt.timezone.utc).isoformat()

    state_as_of = latest_as_of(wh, "fact_player_state", season)
    return {
        "season": season,
        "sort": sort,
        "row_count": len(rows),
        "rows": rows,
        "projection_generated": generated,
        "state_as_of": state_as_of,
        "as_of": state_as_of,
        "notes": notes,
    }


register_script(
    "projection_table",
    projection_table,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Projection table",
    description="Projected points and spread per player, priced and owned as of now.",
)
