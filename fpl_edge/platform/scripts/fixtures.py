"""fixture_ticker — the next N gameweeks per club, home and away.

Deliberately *not* a difficulty model. FPL's own FDR is a fixed preseason
number and our fitted Dixon-Coles ratings are a model run that costs far more
than a panel's 10s budget. This panel reports the schedule -- who plays whom,
where, and when -- which is a fact, and leaves difficulty to the panels that
can afford to compute it honestly. A ticker with an invented difficulty colour
is the most-read and least-earned number on a dashboard.

Blanks and doubles fall out of the same query: a club with no fixture in a
gameweek gets an explicit ``null`` slot rather than a missing key, and a club
with two gets two entries, because both are decisions the operator makes.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import empty, latest_as_of, next_gw, q, season_param

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "horizon": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        "from_gw": {
            "type": ["integer", "null"],
            "default": None,
            "description": "Start gameweek; defaults to the next unplayed one.",
        },
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "gws", "teams", "row_count"],
    "properties": {
        "season": {"type": "string"},
        "gws": {"type": "array", "items": {"type": "integer"}},
        "row_count": {"type": "integer"},
        "as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "teams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["team_code", "short_name", "fixtures"],
                "properties": {
                    "team_code": {"type": "integer"},
                    "short_name": {"type": "string"},
                    "name": {"type": ["string", "null"]},
                    "n_fixtures": {"type": "integer"},
                    "fixtures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["gw", "opponents"],
                            "properties": {
                                "gw": {"type": "integer"},
                                "blank": {"type": "boolean"},
                                "double": {"type": "boolean"},
                                "opponents": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["opponent", "is_home"],
                                        "properties": {
                                            "opponent": {"type": "string"},
                                            "opponent_code": {"type": ["integer", "null"]},
                                            "is_home": {"type": "boolean"},
                                            "kickoff_utc": {"type": ["string", "null"]},
                                            "label": {"type": "string"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def fixture_ticker(
    wh, *, season: str, horizon: int = 5, from_gw: int | None = None
) -> dict[str, Any]:
    """Each club's next `horizon` gameweeks: opponent, venue, kickoff.

    UPPER-case opponent labels mean home, lower-case away -- the convention the
    Telegram fixture grid already uses, so the two surfaces read alike.
    """
    teams = q(
        wh,
        "SELECT team_code, short_name, name FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, team_code"
        "                               ORDER BY as_of DESC) rn"
        "  FROM dim_team WHERE season = ?"
        ") WHERE rn = 1 ORDER BY short_name",
        (season,),
    )
    if teams.empty:
        return empty(
            f"No {season} clubs in the warehouse. Run `make ingest` to pull the "
            f"FPL bootstrap, which is where dim_team comes from."
        )

    start = from_gw if from_gw is not None else next_gw(wh, season)
    notes: list[str] = []
    if start is None:
        played = q(
            wh, "SELECT max(gw) AS g FROM fact_fixture WHERE season = ?", (season,)
        )
        latest = None if played.empty else played.iloc[0]["g"]
        if latest is None:
            return empty(
                f"No {season} fixtures or deadlines known yet. Run `make ingest`."
            )
        start = int(latest)
        notes.append(
            f"Every {season} deadline has passed, so the ticker starts at the last "
            f"known gameweek ({start}) rather than a future one."
        )
    gws = list(range(int(start), int(start) + int(horizon)))

    fx = q(
        wh,
        "SELECT gw, fixture_id, home_team_code, away_team_code, kickoff_utc FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, fixture_id"
        "                               ORDER BY as_of DESC) rn"
        "  FROM fact_fixture WHERE season = ? AND gw >= ? AND gw <= ?"
        ") WHERE rn = 1 ORDER BY gw, kickoff_utc",
        (season, gws[0], gws[-1]),
    )
    if fx.empty:
        return empty(
            f"No {season} fixtures scheduled for GW{gws[0]}-GW{gws[-1]}. The "
            f"fixture list is ingested from the FPL API; run `make ingest`."
        )

    short = dict(zip(teams["team_code"], teams["short_name"]))
    full = dict(zip(teams["team_code"], teams["name"]))

    # (team_code, gw) -> list of opponent entries. Built in one pass over the
    # fixtures so a double gameweek needs no special case.
    per: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for _, r in fx.iterrows():
        gw = int(r["gw"])
        home, away = int(r["home_team_code"]), int(r["away_team_code"])
        kick = None if r["kickoff_utc"] is None else str(r["kickoff_utc"])
        for me, them, is_home in ((home, away, True), (away, home, False)):
            label = short.get(them, str(them))
            per.setdefault((me, gw), []).append({
                "opponent": label,
                "opponent_code": them,
                "is_home": is_home,
                "kickoff_utc": kick,
                "label": label.upper() if is_home else label.lower(),
            })

    out = []
    for _, t in teams.iterrows():
        code = int(t["team_code"])
        slots = []
        total = 0
        for gw in gws:
            opps = per.get((code, gw), [])
            total += len(opps)
            slots.append({
                "gw": gw,
                "blank": not opps,
                "double": len(opps) > 1,
                "opponents": opps,
            })
        if total == 0:
            # A club with nothing in the whole window is not in this season's
            # league; listing it as all-blank would read as five blank GWs.
            continue
        out.append({
            "team_code": code,
            "short_name": str(t["short_name"]),
            "name": None if t["name"] is None else str(t["name"]),
            "n_fixtures": total,
            "fixtures": slots,
        })

    if not out:
        return empty(
            f"No club has a fixture in GW{gws[0]}-GW{gws[-1]} for {season}."
        )
    return {
        "season": season,
        "gws": gws,
        "row_count": len(out),
        "teams": out,
        "as_of": latest_as_of(wh, "fact_fixture", season),
        "notes": notes,
    }


register_script(
    "fixture_ticker",
    fixture_ticker,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Fixture ticker",
    description="Each club's next gameweeks: opponent, venue and kickoff. Blanks and doubles explicit.",
)
