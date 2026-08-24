"""projection_table — the player board, joined to live price and ownership.

Two data regimes live behind one registered name:

* **Artefact mode** (the original, still the default): the solved simulation
  parquet frozen at solve time, joined to live price/ownership. The dashboard
  calls this with ``{limit, sort}`` and must keep rendering unchanged.
* **Gameweek mode** (``gw`` or any gw-only param present): the third-party
  provider projections in ``projection_normalized``, read through the semantic
  layer (``sem_projections`` / ``sem_projection_consensus``). ``source="all"``
  (or omitted) gives the consensus per player with the min–max SPREAD as a
  first-class column — source disagreement IS the uncertainty estimate. A
  specific ``source`` gives that vendor's raw numbers. ``detail_code`` adds a
  per-source breakdown for one player over the chosen GW and the next four.

``p_appear`` is deliberately a separate column from ``xpts`` and is never
multiplied in: "3.1 xPts" and "82% to appear" are different claims about
different random variables, and the rank layer needs them separate
(FPLForm's design, kept on purpose).
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
    load_projection,
    next_gw,
    q,
    season_param,
)

#: How many gameweeks past the chosen one the player detail covers (gw..gw+4).
DETAIL_HORIZON = 4

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
            "enum": ["xpts", "p_haul", "value", "price", "own",
                     "spread", "p_appear", "xmins"],
            "default": "xpts",
            "description": "spread/p_appear/xmins apply to gameweek mode; "
                           "p_haul applies to artefact mode.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 800, "default": 50},
        "max_price": {"type": ["number", "null"], "default": None},
        "gw": {
            "default": None,
            "oneOf": [
                {"type": "integer", "minimum": 1, "maximum": 38},
                {"const": "next"},
                {"type": "null"},
            ],
            "description": "Gameweek for provider-projection mode. 'next' "
                           "resolves the first future deadline. null keeps "
                           "the original solved-artefact behaviour.",
        },
        "source": {
            "type": ["string", "null"],
            "default": None,
            "description": "'all' (or null) = consensus across sources; a "
                           "specific source name shows that vendor alone.",
        },
        "team": {
            "type": ["string", "null"],
            "default": None,
            "description": "Team short_name filter, e.g. 'ARS'. Gameweek mode.",
        },
        "min_p_appear": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
            "default": None,
            "description": "Drop players whose consensus appearance "
                           "probability is below this (or unknown).",
        },
        "detail_code": {
            "type": ["integer", "null"],
            "default": None,
            "description": "Player code: include a per-source breakdown for "
                           "the chosen GW and the next four.",
        },
    },
}

_ARTEFACT_RESULT: dict[str, Any] = {
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

_GW_ROW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "xpts", "n_sources", "p_appear"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},
        "status": {"type": ["string", "null"]},
        "xpts": {"type": "number",
                 "description": "consensus mean, or the single source's value"},
        "xpts_min": {"type": ["number", "null"]},
        "xpts_max": {"type": ["number", "null"]},
        "spread": {"type": ["number", "null"],
                   "description": "xpts_max - xpts_min across sources"},
        "sd": {"type": ["number", "null"]},
        "n_sources": {"type": "integer"},
        "xmins": {"type": ["number", "null"]},
        "p_appear": {"type": ["number", "null"],
                     "description": "separate from xpts by design; never "
                                    "multiplied in"},
        "xp_if_appears": {"type": ["number", "null"]},
        "value": {"type": ["number", "null"]},
    },
}

_GW_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mode", "season", "gw", "source", "sort", "row_count", "rows",
                 "gw_coverage", "sources", "by_team", "by_position", "detail",
                 "notes"],
    "properties": {
        "mode": {"enum": ["consensus", "source"]},
        "season": {"type": "string"},
        "gw": {"type": "integer"},
        "source": {"type": ["string", "null"],
                   "description": "null in consensus mode"},
        "sort": {"type": "string"},
        "row_count": {"type": "integer"},
        "as_of": {"type": ["string", "null"],
                  "description": "latest provider fetch instant at this GW"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": _GW_ROW},
        "gw_coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gw", "n_sources", "n_players"],
                "properties": {
                    "gw": {"type": "integer"},
                    "n_sources": {"type": "integer"},
                    "n_players": {"type": "integer"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "by_team": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["team", "avg_xpts", "n_players"],
                "properties": {
                    "team": {"type": "string"},
                    "avg_xpts": {"type": "number"},
                    "n_players": {"type": "integer"},
                },
            },
        },
        "by_position": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pos", "avg_xpts", "n_players"],
                "properties": {
                    "pos": {"type": "string"},
                    "avg_xpts": {"type": "number"},
                    "n_players": {"type": "integer"},
                },
            },
        },
        "detail": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["code", "name", "gw_from", "gw_to", "rows", "outlier"],
            "properties": {
                "code": {"type": "integer"},
                "name": {"type": "string"},
                "gw_from": {"type": "integer"},
                "gw_to": {"type": "integer"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["gw", "source", "xpts"],
                        "properties": {
                            "gw": {"type": "integer"},
                            "source": {"type": "string"},
                            "xpts": {"type": ["number", "null"]},
                            "xmins": {"type": ["number", "null"]},
                            "p_appear": {"type": ["number", "null"]},
                            "xp_if_appears": {"type": ["number", "null"]},
                        },
                    },
                },
                "outlier": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["source", "gw", "xpts", "delta_vs_rest"],
                    "properties": {
                        "source": {"type": "string"},
                        "gw": {"type": "integer"},
                        "xpts": {"type": "number"},
                        "delta_vs_rest": {
                            "type": "number",
                            "description": "this source's xpts minus the mean "
                                           "of the other sources at the "
                                           "chosen GW",
                        },
                    },
                },
            },
        },
    },
}

#: One registered name, two honest shapes. The branches cannot both match:
#: the gameweek shape requires ``mode``, which the artefact shape's
#: additionalProperties:false forbids.
RESULT: dict[str, Any] = {"type": "object", "oneOf": [_ARTEFACT_RESULT, _GW_RESULT]}


def _num(v) -> float | None:
    """None for missing/NaN, float otherwise (pandas round-trips None as NaN)."""
    if v is None or v != v:
        return None
    return float(v)


def _rnd(v, places: int = 3) -> float | None:
    n = _num(v)
    return None if n is None else round(n, places)


def projection_table(
    wh,
    *,
    season: str,
    position: int | None = None,
    sort: str = "xpts",
    limit: int = 50,
    max_price: float | None = None,
    gw: int | str | None = None,
    source: str | None = None,
    team: str | None = None,
    min_p_appear: float | None = None,
    detail_code: int | None = None,
) -> dict[str, Any]:
    """Projected points per player: solved artefact by default, or per-gameweek
    provider consensus (with the cross-source spread as the uncertainty column)
    when ``gw``/``source``/``team``/``min_p_appear``/``detail_code`` is given.

    Returns empty when the requested regime has no data, naming what does
    exist: the artefact branch says to run the solve, the gameweek branch
    lists which gameweeks the ingested sources actually cover.
    """
    gw_mode = any(p is not None for p in (gw, source, team, min_p_appear, detail_code))
    if gw_mode:
        return _gw_mode(
            wh, season=season, position=position, sort=sort, limit=limit,
            max_price=max_price, gw=gw, source=source, team=team,
            min_p_appear=min_p_appear, detail_code=detail_code,
        )
    return _artefact_mode(
        wh, season=season, position=position, sort=sort, limit=limit,
        max_price=max_price,
    )


# ---------------------------------------------------------------------------
# artefact mode — the original behaviour, unchanged (the dashboard's contract)
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


# ---------------------------------------------------------------------------
# gameweek mode — provider projections through the semantic layer
# ---------------------------------------------------------------------------

def _gw_mode(
    wh,
    *,
    season: str,
    position: int | None,
    sort: str,
    limit: int,
    max_price: float | None,
    gw: int | str | None,
    source: str | None,
    team: str | None,
    min_p_appear: float | None,
    detail_code: int | None,
) -> dict[str, Any]:
    now = dt.datetime.now(UTC)
    notes: list[str] = []

    # Coverage first: it powers the GW picker, and it IS the honest-empty
    # message when the asked-for gameweek has nothing.
    try:
        coverage = q(
            wh,
            "SELECT gw, COUNT(DISTINCT source) AS n_sources, "
            "COUNT(DISTINCT code) AS n_players "
            "FROM sem_projections(?) WHERE season = ? AND xpts IS NOT NULL "
            "GROUP BY gw ORDER BY gw",
            (now, season),
        )
    except Exception as e:  # noqa: BLE001 -- narrow re-raise below
        if "projection_normalized" in str(e) or "fact_projection" in str(e):
            return empty(
                "The projection tables do not exist in this warehouse yet. "
                "Run the projections ingest (`python -m fpl_edge.ingest.projections`) "
                "to fetch provider projections first."
            )
        raise
    if coverage.empty:
        return empty(
            f"No provider projections ingested for {season}. Run the "
            f"projections ingest to fetch xPts sources first."
        )
    cov_rows = [{"gw": int(r["gw"]), "n_sources": int(r["n_sources"]),
                 "n_players": int(r["n_players"])} for _, r in coverage.iterrows()]
    covered = [c["gw"] for c in cov_rows]

    def coverage_text() -> str:
        return ", ".join(f"GW{c['gw']} ({c['n_sources']} source"
                         f"{'s' if c['n_sources'] != 1 else ''})" for c in cov_rows)

    # Resolve the gameweek. "next" (and an omitted gw when another gw-mode
    # param forced this branch) means the first future deadline.
    if gw is None or gw == "next":
        resolved = next_gw(wh, season)
        if resolved is None:
            resolved = covered[0]
            notes.append(
                f"No future deadline in dim_event, so this defaulted to the "
                f"first covered gameweek, GW{resolved}."
            )
        elif resolved not in covered:
            nearest = min(covered, key=lambda g: (abs(g - resolved), g))
            notes.append(
                f"The next deadline is GW{resolved} but no source covers it; "
                f"showing GW{nearest} instead. Covered: {coverage_text()}."
            )
            resolved = nearest
        gw = resolved
    gw = int(gw)
    if gw not in covered:
        return empty(
            f"No source projects GW{gw}. Gameweeks with data: {coverage_text()}."
        )

    sources_df = q(
        wh,
        "SELECT DISTINCT source FROM sem_projections(?) "
        "WHERE season = ? AND gw = ? AND xpts IS NOT NULL ORDER BY source",
        (now, season, gw),
    )
    sources = [str(s) for s in sources_df["source"]]

    consensus = source is None or source == "all"
    if not consensus and source not in sources:
        return empty(
            f"No source named {source!r} projects GW{gw}. Sources at GW{gw}: "
            f"{', '.join(sources)}."
        )

    if consensus:
        frame = q(
            wh,
            """
            WITH c AS (
                SELECT * FROM sem_projection_consensus(?)
                WHERE season = ? AND gw = ?
            ), ap AS (
                SELECT code, AVG(p_appear) AS p_appear,
                       AVG(xp_if_appears) AS xp_if_appears
                FROM sem_projections(?)
                WHERE season = ? AND gw = ? AND xpts IS NOT NULL
                GROUP BY code
            ), pl AS (
                SELECT code, selected_by_pct, status
                FROM sem_players(?) WHERE season = ?
            )
            SELECT c.code, c.web_name, c.position, c.team, c.price,
                   pl.selected_by_pct AS own_pct, pl.status,
                   c.n_sources, c.xpts_mean AS xpts, c.xpts_min, c.xpts_max,
                   c.xpts_spread AS spread, c.xpts_sd AS sd,
                   c.xmins_mean AS xmins, ap.p_appear, ap.xp_if_appears
            FROM c
            LEFT JOIN ap USING (code)
            LEFT JOIN pl USING (code)
            """,
            (now, season, gw, now, season, gw, now, season),
        )
    else:
        frame = q(
            wh,
            """
            WITH pl AS (
                SELECT code, selected_by_pct, status
                FROM sem_players(?) WHERE season = ?
            )
            SELECT pr.code, pr.web_name, pr.position, pr.team, pr.price,
                   pl.selected_by_pct AS own_pct, pl.status,
                   1 AS n_sources, pr.xpts,
                   NULL AS xpts_min, NULL AS xpts_max,
                   NULL AS spread, NULL AS sd,
                   pr.xmins, pr.p_appear, pr.xp_if_appears
            FROM sem_projections(?) pr
            LEFT JOIN pl USING (code)
            WHERE pr.season = ? AND pr.gw = ? AND pr.source = ?
              AND pr.xpts IS NOT NULL
            """,
            (now, season, now, season, gw, source),
        )
    if frame.empty:
        return empty(
            f"No projection rows survived the join at GW{gw}. Gameweeks with "
            f"data: {coverage_text()}."
        )

    frame["value"] = frame["xpts"] / frame["price"].clip(lower=0.1)

    # Aggregates over the FULL gameweek board, before player filters: the
    # strip answers "which teams/positions look best this GW", and a price
    # filter should not quietly reshape that answer.
    def agg(col: str, name_of) -> list[dict[str, Any]]:
        grouped = (frame.dropna(subset=[col]).groupby(col)["xpts"]
                   .agg(["mean", "count"]).reset_index()
                   .sort_values("mean", ascending=False))
        return [{("team" if col == "team" else "pos"): name_of(r[col]),
                 "avg_xpts": round(float(r["mean"]), 3),
                 "n_players": int(r["count"])} for _, r in grouped.iterrows()]

    by_team = agg("team", str)
    by_position = agg(
        "position",
        lambda p: POSITION_NAME.get(int(p), str(p)) if p == p else "?",
    )

    if position is not None:
        frame = frame[frame["position"] == position]
    if team is not None:
        frame = frame[frame["team"].astype(str).str.upper() == team.upper()]
    if max_price is not None:
        frame = frame[frame["price"].notna() & (frame["price"] <= max_price)]
    if min_p_appear is not None:
        frame = frame[frame["p_appear"].notna() & (frame["p_appear"] >= min_p_appear)]
        if not sources_df.empty and frame.empty:
            notes.append(
                "The p_appear filter removed every row; not every source "
                "publishes an appearance probability."
            )
    if frame.empty:
        return empty(
            f"No GW{gw} player matches those filters "
            f"(position/team/price/p_appear)."
        )

    sort_key = {"xpts": "xpts", "value": "value", "price": "price",
                "own": "own_pct", "spread": "spread", "p_appear": "p_appear",
                "xmins": "xmins", "p_haul": "xpts"}[sort]
    if sort == "p_haul":
        notes.append("sort='p_haul' belongs to artefact mode; sorted by xpts.")
    if not consensus and sort == "spread":
        sort_key = "xpts"
        notes.append(
            "spread is a cross-source column; a single source has none, "
            "so this sorted by xpts."
        )
    frame = frame.sort_values(sort_key, ascending=False, na_position="last")
    frame = frame.head(int(limit))

    rows = []
    for _, r in frame.iterrows():
        pos_v = r["position"]
        rows.append({
            "code": int(r["code"]),
            "name": "(unmapped)" if r["web_name"] is None or r["web_name"] != r["web_name"]
                    else str(r["web_name"]),
            "pos": POSITION_NAME.get(int(pos_v), str(pos_v))
                   if pos_v is not None and pos_v == pos_v else "?",
            "team": None if r["team"] is None or r["team"] != r["team"]
                    else str(r["team"]),
            "price": _rnd(r["price"], 1),
            "own_pct": _num(r["own_pct"]),
            "status": None if r["status"] is None or r["status"] != r["status"]
                      else str(r["status"]),
            "xpts": round(float(r["xpts"]), 3),
            "xpts_min": _rnd(r["xpts_min"]),
            "xpts_max": _rnd(r["xpts_max"]),
            "spread": _rnd(r["spread"]),
            "sd": _rnd(r["sd"]),
            "n_sources": int(r["n_sources"]),
            "xmins": _rnd(r["xmins"], 1),
            "p_appear": _rnd(r["p_appear"]),
            "xp_if_appears": _rnd(r["xp_if_appears"]),
            "value": _rnd(r["value"]),
        })

    detail = None
    if detail_code is not None:
        detail = _player_detail(wh, now, season, gw, int(detail_code), notes)

    fetched = q(
        wh,
        "SELECT max(fetched_at) AS f FROM sem_projections(?) "
        "WHERE season = ? AND gw = ?",
        (now, season, gw),
    )
    as_of = None
    if not fetched.empty and fetched.iloc[0]["f"] is not None:
        as_of = str(fetched.iloc[0]["f"])

    return {
        "mode": "consensus" if consensus else "source",
        "season": season,
        "gw": gw,
        "source": None if consensus else source,
        "sort": sort,
        "row_count": len(rows),
        "rows": rows,
        "gw_coverage": cov_rows,
        "sources": sources,
        "by_team": by_team,
        "by_position": by_position,
        "detail": detail,
        "as_of": as_of,
        "notes": notes,
    }


def _player_detail(
    wh, now: dt.datetime, season: str, gw: int, code: int, notes: list[str],
) -> dict[str, Any] | None:
    """Every source's numbers for one player, chosen GW through GW+4, plus
    which source is the outlier at the chosen GW (largest |xpts - mean of the
    others|; needs at least three sources to mean anything)."""
    gw_to = min(gw + DETAIL_HORIZON, 38)
    detail_df = q(
        wh,
        "SELECT gw, source, web_name, xpts, xmins, p_appear, xp_if_appears "
        "FROM sem_projections(?) "
        "WHERE season = ? AND code = ? AND gw BETWEEN ? AND ? "
        "ORDER BY gw, source",
        (now, season, code, gw, gw_to),
    )
    if detail_df.empty:
        notes.append(
            f"detail_code {code} has no projection rows in GW{gw}-GW{gw_to}; "
            f"detail omitted."
        )
        return None

    name = next((str(n) for n in detail_df["web_name"] if n is not None and n == n),
                f"code {code}")
    d_rows = [{
        "gw": int(r["gw"]),
        "source": str(r["source"]),
        "xpts": _rnd(r["xpts"]),
        "xmins": _rnd(r["xmins"], 1),
        "p_appear": _rnd(r["p_appear"]),
        "xp_if_appears": _rnd(r["xp_if_appears"]),
    } for _, r in detail_df.iterrows()]

    outlier = None
    here = [r for r in d_rows if r["gw"] == gw and r["xpts"] is not None]
    if len(here) >= 3:
        def delta_vs_rest(row):
            rest = [o["xpts"] for o in here if o is not row]
            return row["xpts"] - sum(rest) / len(rest)
        worst = max(here, key=lambda r: abs(delta_vs_rest(r)))
        outlier = {
            "source": worst["source"],
            "gw": gw,
            "xpts": worst["xpts"],
            "delta_vs_rest": round(delta_vs_rest(worst), 3),
        }

    return {
        "code": code,
        "name": name,
        "gw_from": gw,
        "gw_to": gw_to,
        "rows": d_rows,
        "outlier": outlier,
    }


register_script(
    "projection_table",
    projection_table,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Projection table",
    description="Projected points per player: the solved artefact by default, "
                "or per-gameweek provider consensus with the cross-source "
                "spread when a gameweek is chosen.",
)
