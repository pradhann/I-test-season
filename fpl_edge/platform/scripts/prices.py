"""price_radar — net-transfer velocity between consecutive state snapshots.

FPL does not publish the price-change algorithm, and this panel does not
pretend to have reverse-engineered it. What it reports is the observable:
``transfers_in_event`` and ``transfers_out_event`` are cumulative counters
within a gameweek, so the *difference between two consecutive ingests* is the
net flow that actually happened in that window, and dividing by the elapsed
hours makes windows of different lengths comparable.

Two honesty constraints follow from how the counters behave:

* **The counters reset at each deadline.** A negative delta across a reset is
  not an outflow, it is a new gameweek. Those pairs are dropped rather than
  reported as a record-breaking sell-off.
* **Velocity is not a probability.** A player crossing a threshold rises when
  FPL says so, not when a panel says so. The output is named ``net_per_hour``
  and carries no predicted change, because the number that would be invented
  here is precisely the one a reader would act on.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import POSITION_NAME, empty, q, season_param

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
        "direction": {
            "type": "string",
            "enum": ["both", "in", "out"],
            "default": "both",
        },
        "min_hours": {
            "type": "number",
            "minimum": 0.01,
            "default": 0.25,
            "description": (
                "Ignore snapshot pairs closer together than this. Kept well "
                "below the nominal half-hourly ingest cadence because a job "
                "that fires at 23:18 and 22:48 is 29.9h/60 apart, not 0.5h, "
                "and a threshold set at the nominal value excludes every "
                "real pair."
            ),
        },
    },
}

_MOVER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "price", "net", "net_per_hour"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]},
        "price": {"type": "number"},
        "own_pct": {"type": ["number", "null"]},
        "transfers_in": {"type": "integer"},
        "transfers_out": {"type": "integer"},
        "net": {"type": "integer"},
        "net_per_hour": {"type": "number"},
        "price_change_tenths": {"type": "integer"},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "window", "risers", "fallers"],
    "properties": {
        "season": {"type": "string"},
        "as_of": {"type": ["string", "null"]},
        "window": {
            "type": "object",
            "additionalProperties": False,
            "required": ["from_utc", "to_utc", "hours"],
            "properties": {
                "from_utc": {"type": "string"},
                "to_utc": {"type": "string"},
                "hours": {"type": "number"},
            },
        },
        "risers": {"type": "array", "items": _MOVER},
        "fallers": {"type": "array", "items": _MOVER},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def price_radar(
    wh,
    *,
    season: str,
    limit: int = 20,
    direction: str = "both",
    min_hours: float = 0.5,
) -> dict[str, Any]:
    """Net transfer flow per hour between the two most recent state snapshots.

    Needs at least two ingests of ``fact_player_state`` in the same gameweek to
    say anything at all: a single snapshot is a cumulative total since the
    deadline, not a velocity, and reporting it as one would overstate every
    early-week number.
    """
    stamps = q(
        wh,
        "SELECT DISTINCT as_of FROM fact_player_state WHERE season = ? "
        "ORDER BY as_of DESC LIMIT 40",
        (season,),
    )
    if stamps.empty:
        return empty(
            f"No {season} player state in the warehouse. Run `make ingest`."
        )
    if len(stamps) < 2:
        return empty(
            f"Only one {season} state snapshot exists ({stamps.iloc[0]['as_of']}). "
            f"Transfer velocity is the difference between two ingests, so the "
            f"radar lights up after the next `make ingest`."
        )

    import pandas as pd

    times = pd.to_datetime(stamps["as_of"], utc=True).sort_values(ascending=False)
    latest = times.iloc[0]
    prior = None
    hours = 0.0
    skipped_reset = False
    for candidate in times.iloc[1:]:
        gap = (latest - candidate).total_seconds() / 3600.0
        if gap < min_hours:
            continue
        prior = candidate
        hours = gap
        break
    if prior is None:
        return empty(
            f"The two most recent {season} snapshots are less than {min_hours}h "
            f"apart, which is too short a window to read a rate from."
        )

    pair = q(
        wh,
        """
        WITH a AS (
            SELECT code, transfers_in_event AS ti, transfers_out_event AS tout,
                   price_tenths AS px
            FROM fact_player_state WHERE season = ? AND as_of = ?
        ), b AS (
            SELECT code, transfers_in_event AS ti, transfers_out_event AS tout,
                   price_tenths AS px, selected_by_pct, status
            FROM fact_player_state WHERE season = ? AND as_of = ?
        )
        SELECT b.code,
               b.ti - a.ti  AS d_in,
               b.tout - a.tout AS d_out,
               b.px - a.px  AS d_px,
               b.px AS price_tenths, b.selected_by_pct
        FROM b JOIN a USING (code)
        """,
        (season, prior.to_pydatetime(), season, latest.to_pydatetime()),
    )
    if pair.empty:
        return empty(
            f"The two most recent {season} snapshots share no player codes, so no "
            f"flow can be differenced between them."
        )

    pair = pair.fillna({"d_in": 0, "d_out": 0, "d_px": 0})
    # A negative cumulative delta means the counters reset at a deadline; that
    # is a new gameweek, not an outflow.
    reset = (pair["d_in"] < 0) | (pair["d_out"] < 0)
    if reset.any():
        skipped_reset = True
        pair = pair[~reset]
    if pair.empty:
        return empty(
            f"The transfer counters reset between the two most recent {season} "
            f"snapshots (a deadline passed), so no comparable window exists yet."
        )

    names = q(
        wh,
        "SELECT code, web_name, position, team_code FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn"
        "  FROM dim_player WHERE season = ?"
        ") WHERE rn = 1",
        (season,),
    )
    teams = q(
        wh,
        "SELECT team_code, short_name FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, team_code ORDER BY as_of DESC) rn"
        "  FROM dim_team WHERE season = ?"
        ") WHERE rn = 1",
        (season,),
    )
    pair = pair.merge(names, on="code", how="left")
    if not teams.empty:
        pair = pair.merge(teams, on="team_code", how="left")
    else:
        pair["short_name"] = None

    pair["net"] = pair["d_in"] - pair["d_out"]
    pair["rate"] = pair["net"] / max(hours, 1e-9)

    # Preseason the counters are all zero, and "top risers" whose net flow is
    # zero is the fabrication this whole module is written to avoid: the panel
    # would look identical to a real one while ranking nothing. Movers only.
    moved = pair[pair["net"] != 0]
    if moved.empty:
        return empty(
            f"No transfer flow between {prior.isoformat()} and {latest.isoformat()} "
            f"-- every one of the {len(pair)} players differenced has a net of zero. "
            f"FPL's transfers_in_event/transfers_out_event counters stay at zero "
            f"until the season is underway, so this panel is blank by design "
            f"before GW1 rather than ranking ties."
        )
    pair = moved

    def rows(frame):
        out = []
        for _, r in frame.iterrows():
            name = r.get("web_name")
            out.append({
                "code": int(r["code"]),
                "name": str(name) if name == name and name is not None else str(int(r["code"])),
                "pos": POSITION_NAME.get(
                    int(r["position"]) if r.get("position") == r.get("position")
                    and r.get("position") is not None else 0, "?"),
                "team": None if r.get("short_name") is None
                        or r.get("short_name") != r.get("short_name")
                        else str(r["short_name"]),
                "price": round(float(r["price_tenths"]) / 10.0, 1),
                "own_pct": None if r.get("selected_by_pct") is None
                           or r.get("selected_by_pct") != r.get("selected_by_pct")
                           else float(r["selected_by_pct"]),
                "transfers_in": int(r["d_in"]),
                "transfers_out": int(r["d_out"]),
                "net": int(r["net"]),
                "net_per_hour": round(float(r["rate"]), 1),
                "price_change_tenths": int(r["d_px"]),
            })
        return out

    risers = rows(pair.nlargest(int(limit), "rate")) if direction in ("both", "in") else []
    fallers = rows(pair.nsmallest(int(limit), "rate")) if direction in ("both", "out") else []

    notes = [
        "net_per_hour is observed transfer flow, not a predicted price change: "
        "FPL's threshold algorithm is unpublished and is not modelled here."
    ]
    if skipped_reset:
        notes.append(
            "Players whose counters reset between the two snapshots were dropped "
            "rather than reported as outflow."
        )

    return {
        "season": season,
        "as_of": latest.isoformat(),
        "window": {
            "from_utc": prior.isoformat(),
            "to_utc": latest.isoformat(),
            "hours": round(float(hours), 2),
        },
        "risers": risers,
        "fallers": fallers,
        "notes": notes,
    }


register_script(
    "price_radar",
    price_radar,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Price radar",
    description="Net transfer velocity between the two most recent state snapshots.",
)
