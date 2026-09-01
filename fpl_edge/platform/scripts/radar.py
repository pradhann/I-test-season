"""player_radar — one player's per-90 percentiles vs same-position peers.

Feeds the pizza chart in the shared player drawer. The whole design of this
panel is honesty about small numbers:

* **Population**: season-to-date per-90 rates over ``sem_player_match_stats``
  (the same semantic view the planner's metrics read — one definition, no
  second implementation). Peers are the players sharing the subject's FPL
  position this season whose minutes clear the floor.
* **Floor**: ``max(90, round(90 * settled_gws / 3))`` minutes — a third of the
  season so far, never below one full match (≥270' from GW9 on). A subject
  below the floor keeps his slices (``below_floor: true``, rendered faded):
  hiding true small-sample facts is a different dishonesty from refusing.
  Refusing (no rows at all) is the distinct ``{empty, reason}`` shape.
* **Percentile = mid-rank**: ``100 × (n_below + 0.5 × n_equal) / N``, integer.
  Ties split; an all-zero metric collapses to 50 naturally, and
  ``ties_at_zero`` flags any metric where at least half the peers sit on zero
  so the renderer can hatch it rather than draw a fake median.
* **Closed metric sets** per position — only columns the warehouse actually
  carries. ``minutes`` is NEVER a slice: it is the floor and the header line;
  a minutes-share slice would mix bases inside one ring. Everything is
  oriented more-is-better; ``goals_prevented`` is already signed; nothing is
  inverted silently.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    UTC,
    empty,
    latest_as_of,
    q,
    season_param,
)

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code"],
    "properties": {
        "code": {"type": "integer"},
        "season": season_param(),
    },
}

_SLICE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "label", "group", "per90", "percentile", "ties_at_zero"],
    "properties": {
        "key": {"type": "string"},
        "label": {"type": "string"},
        "group": {"type": "string", "enum": ["threat", "creation", "defending"]},
        "per90": {"type": "number"},
        "percentile": {"type": "integer", "minimum": 0, "maximum": 100},
        "ties_at_zero": {"type": "boolean"},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "window", "floor_minutes", "n_peers",
                 "basis", "slices", "below_floor"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["season", "matches", "minutes"],
            "properties": {
                "season": {"type": "string"},
                "matches": {"type": "integer"},
                "minutes": {"type": "number"},
            },
        },
        "floor_minutes": {"type": "integer"},
        "settled_gws": {"type": "integer"},
        "n_peers": {"type": "integer"},
        "basis": {"type": "string"},
        "method": {"type": "string"},
        "slices": {"type": "array", "items": _SLICE, "minItems": 1},
        "below_floor": {"type": "boolean"},
        "groups_note": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
    },
}

#: The closed metric sets, per position. (key, label, group). Only columns
#: ``sem_player_match_stats`` carries; ``minutes_played`` is deliberately not
#: eligible. DEF's ordering is load-bearing: attack slices first, then
#: defence, so the renderer's two labelled half-arcs are contiguous.
METRICS: dict[str, list[tuple[str, str, str]]] = {
    "GKP": [
        ("saves", "Saves /90", "defending"),
        ("goals_prevented", "Goals prevented /90", "defending"),
        ("recoveries", "Recoveries /90", "defending"),
        ("defensive_contributions", "Def. contributions /90", "defending"),
    ],
    "DEF": [
        ("xg", "xG /90", "threat"),
        ("total_shots", "Shots /90", "threat"),
        ("touches_opposition_box", "Box touches /90", "threat"),
        ("xa", "xA /90", "creation"),
        ("tackles", "Tackles /90", "defending"),
        ("interceptions", "Interceptions /90", "defending"),
        ("recoveries", "Recoveries /90", "defending"),
        ("defensive_contributions", "Def. contributions /90", "defending"),
    ],
    "MID": [
        ("xg", "xG /90", "threat"),
        ("total_shots", "Shots /90", "threat"),
        ("touches_opposition_box", "Box touches /90", "threat"),
        ("xa", "xA /90", "creation"),
        ("chances_created", "Chances created /90", "creation"),
        ("defensive_contributions", "Def. contributions /90", "defending"),
        ("tackles", "Tackles /90", "defending"),
        ("interceptions", "Interceptions /90", "defending"),
    ],
    "FWD": [
        ("xg", "xG /90", "threat"),
        ("xgot", "xGOT /90", "threat"),
        ("total_shots", "Shots /90", "threat"),
        ("shots_on_target", "Shots on target /90", "threat"),
        ("touches_opposition_box", "Box touches /90", "threat"),
        ("xa", "xA /90", "creation"),
        ("chances_created", "Chances created /90", "creation"),
        ("recoveries", "Recoveries /90", "defending"),
    ],
}

GROUPS_NOTE = {
    "GKP": (
        "The warehouse carries no distribution, claim or sweeping columns — "
        "this chart answers shot-stopping and box activity only. Four honest "
        "slices beat nine padded ones."
    ),
    "DEF": (
        "Two labelled half-arcs: attack (xG, shots, box touches, xA) vs "
        "defence — a wing-back and a centre-half read differently at a "
        "glance. Chances created is dropped (xA carries the decision signal; "
        "eight is the legibility ceiling at drawer width)."
    ),
    "FWD": (
        "xGOT beside xG is deliberate: the percentile gap between them is "
        "finishing placement."
    ),
    "MID": None,
}

_ALL_COLS = sorted({k for cols in METRICS.values() for k, _, _ in cols})


def _mid_rank_percentile(value: float, pool: list[float]) -> int:
    """100 × (n_below + 0.5 × n_equal) / N, integer. Ties split."""
    n = len(pool)
    if n == 0:
        return 50
    below = sum(1 for v in pool if v < value)
    equal = sum(1 for v in pool if v == value)
    return round(100.0 * (below + 0.5 * equal) / n)


def player_radar(wh, *, code: int, season: str) -> dict[str, Any]:
    """Per-90 percentiles for one player vs his positional peers."""
    now = dt.datetime.now(UTC)
    code = int(code)

    who = q(
        wh,
        "SELECT code, web_name, position, team, team_code FROM sem_players(?) "
        "WHERE season = ? AND code = ?",
        (now, season, code),
    )
    if who.empty:
        return empty(
            f"No player with code {code} in the {season} warehouse "
            f"(dim_player has no row). Run `make ingest` or check the code."
        )
    pos_num = int(who.iloc[0]["position"]) if who.iloc[0]["position"] == who.iloc[0]["position"] else 0
    pos = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}.get(pos_num)
    if pos is None:
        return empty(f"Player {code} has no known FPL position; no peer group exists.")
    metrics = METRICS[pos]

    # Season-to-date sums per player, restricted to the subject's position so
    # the peer pool and the subject come from ONE query and one definition.
    cols_sql = ", ".join(f"COALESCE(SUM(m.{c}), 0) AS {c}" for c in _ALL_COLS)
    agg = q(
        wh,
        f"""
        SELECT m.code,
               COALESCE(SUM(m.minutes_played), 0) AS minutes,
               COUNT(*) AS matches,
               {cols_sql}
        FROM sem_player_match_stats(?) m
        JOIN sem_players(?) p ON p.code = m.code AND p.season = m.season
        WHERE m.season = ? AND p.position = ?
        GROUP BY m.code
        """,
        (now, now, season, pos_num),
    )
    if agg.empty:
        return empty(
            f"no rows in fact_player_match_stats for any {season} {pos} — "
            f"the per-match feed has not been ingested yet."
        )

    by_code = {int(r["code"]): r for _, r in agg.iterrows()}
    if code not in by_code:
        return empty(
            f"no rows in fact_player_match_stats for code {code} "
            f"({who.iloc[0]['web_name']}) this season — the pipeline ingests "
            f"the per-match feed daily; there is nothing to chart until it "
            f"lands."
        )

    settled = q(
        wh,
        "SELECT COUNT(DISTINCT gw) AS n FROM sem_player_match_stats(?) "
        "WHERE season = ? AND gw IS NOT NULL",
        (now, season),
    )
    settled_gws = int(settled.iloc[0]["n"]) if not settled.empty else 0
    # A third of the season so far, never below one full match. (The spec's
    # "0.33" is this exact third: 90 × 9/3 = 270 from GW9.)
    floor = max(90, round(90.0 * settled_gws / 3.0))

    def per90(row, col) -> float:
        mins = float(row["minutes"]) or 0.0
        if mins <= 0:
            return 0.0
        return 90.0 * float(row[col]) / mins

    subject = by_code[code]
    peers = [r for c, r in by_code.items() if float(r["minutes"]) >= floor]
    n_peers = len(peers)
    below_floor = float(subject["minutes"]) < floor

    slices: list[dict[str, Any]] = []
    for key, label, group in metrics:
        pool = [per90(r, key) for r in peers]
        val = per90(subject, key)
        zeros = sum(1 for v in pool if v == 0.0)
        slices.append({
            "key": f"{key}_p90",
            "label": label,
            "group": group,
            "per90": round(val, 3),
            "percentile": _mid_rank_percentile(val, pool),
            "ties_at_zero": bool(pool) and zeros * 2 >= len(pool),
        })

    return {
        "code": code,
        "name": str(who.iloc[0]["web_name"]),
        "pos": pos,
        "team": None if who.iloc[0]["team"] is None else str(who.iloc[0]["team"]),
        "team_code": None if who.iloc[0]["team_code"] != who.iloc[0]["team_code"]
                     else int(who.iloc[0]["team_code"]),
        "window": {
            "season": season,
            "matches": int(subject["matches"]),
            "minutes": round(float(subject["minutes"]), 0),
        },
        "floor_minutes": floor,
        "settled_gws": settled_gws,
        "n_peers": n_peers,
        "basis": "per90",
        "method": "mid-rank: 100 × (n_below + 0.5 × n_equal) / N, integer",
        "slices": slices,
        "below_floor": below_floor,
        "groups_note": GROUPS_NOTE.get(pos),
        "as_of": latest_as_of(wh, "fact_player_match_stats", season),
    }


register_script(
    "player_radar",
    player_radar,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Player radar",
    description="One player's per-90 percentiles vs same-position peers, "
                "mid-rank method, minutes floor — feeds the drawer pizza.",
)
