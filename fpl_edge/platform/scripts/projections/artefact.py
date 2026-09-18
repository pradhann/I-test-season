"""``_artefact_mode``: the solved artefact, one row per player.

The default branch. It shares nothing with the gameweek mode except the
dispatcher and the schemas."""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.scripts.common import POSITION_NAME, empty, latest_as_of, load_projection, q

# ---------------------------------------------------------------------------
# artefact mode: the original behaviour, unchanged (the dashboard's contract)
# ---------------------------------------------------------------------------

def _artefact_mode(
    wh,
    *,
    season: str,
    position: int | None,
    sort: str,
    limit: int,
    max_price: float | None,
) -> dict[str, Any]:
    proj = load_projection(wh)
    if proj is None:
        return empty(
            "No projection artefact cached. Run `make solve` to write "
            "data/warehouse/gw1_projection.parquet, then reload this panel."
        )
    if proj.empty:
        return empty("The projection artefact exists but contains no players.")

    notes: list[str] = []
    if sort in ("spread", "p_appear", "xmins"):
        notes.append(
            f"sort={sort!r} belongs to gameweek mode; the artefact carries no "
            f"such column, so this fell back to xpts."
        )
        sort = "xpts"

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
                f"player codes. The artefact is probably from another season."
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
