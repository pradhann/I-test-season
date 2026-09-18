"""``player_form``: one player's settled gameweeks, with the season of each row.

The panel behind MCP tool 4 (``player_form``), which until now read the two
semantic macros directly from the toolbelt. Same macros, same point-in-time
rule, one implementation: ``sem_player_form`` for the realised per-fixture
return including the official expected stats, ``sem_fixtures`` for the
opponent and the venue.

Three rules this panel exists to keep.

1. **A row appears only after its gameweek's points were finalised.**
   ``sem_player_form`` stamps ``as_of`` at the finalisation instant, so a read
   at a deadline sees completed gameweeks and nothing else. That is the leak
   guard, and it is the macro's, not this file's.

2. **The season of every row is stated.** Early in a season there is nothing
   settled yet, so the recent form a reader wants is last season's. Returning
   those rows silently would label one season's numbers as another's. The
   payload carries ``season`` per row, ``seasons_covered`` for the set, and
   ``season_note`` saying in one sentence which seasons the rows came from.

3. **Nothing settled is an empty with a reason, never a blank table.** The
   reason names the player, the season filter that was applied and the
   instant, so the reader can tell "this player has not played" apart from
   "this warehouse has not ingested a finalised gameweek yet".

The panel takes ``code``, the stable cross-season FPL player code, never an
element id: element ids are reassigned every August and a form table is the
one surface where following the same player across a season boundary is the
whole point.

A NOTE ON SQL PLACEHOLDERS

Queries here bind ``$1``-style numbered parameters. The house prose gate reads
a question mark followed by whitespace as a rhetorical question, so a
positional placeholder in a SQL string fails a lint that exists for the
payload's English. Numbered parameters bind identically through
``guarded_query``; nothing is interpolated into SQL text. The same workaround
is recorded in ``platform/scripts/creators/episodes.py``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import POSITION_NAME, SEASON_DEFAULT, empty, q

UTC = dt.UTC

#: How many settled gameweeks a caller may ask for. Above this the payload is
#: a season history rather than form, and the question is a different one.
MAX_LAST_K = 38


def _as_of(value: str | None) -> dt.datetime:
    """Parse the optional instant. Timezone-aware UTC, always.

    A naive value is rejected rather than assumed to be UTC, matching the
    rule the whole engine applies to FPL's own deadlines.
    """
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(
            "as_of must carry a timezone, for example 2026-08-18T22:50:00Z"
        )
    return parsed.astimezone(UTC)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _i(value: Any) -> int | None:
    out = _f(value)
    return None if out is None else int(out)


def _identity(wh, when: dt.datetime, code: int) -> dict[str, Any] | None:
    """The player's most recent identity row at or before ``when``.

    Read from ``sem_players`` in the latest season that holds the code, so a
    player who moved club in the summer is named by the club he is at now.
    """
    rows = q(
        wh,
        "SELECT season, code, web_name, position, team, price "
        "FROM sem_players($1::TIMESTAMPTZ) WHERE code = $2 "
        "ORDER BY season DESC LIMIT 1",
        (when, int(code)),
    )
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    pos = _i(row.get("position"))
    return {
        "code": int(code),
        "web_name": str(row.get("web_name") or ""),
        "position": POSITION_NAME.get(pos or 0),
        "team": None if row.get("team") is None else str(row["team"]),
        "price": _f(row.get("price")),
        "season": str(row.get("season") or ""),
    }


def player_form(
    wh,
    *,
    code: int,
    last_k: int = 6,
    season: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """One player's most recent settled gameweeks, newest last.

    Points, minutes, goals, assists, the official expected numbers, bonus and
    BPS per fixture, with the opponent and the venue joined from the schedule.
    """
    when = _as_of(as_of)
    who = _identity(wh, when, int(code))
    if who is None:
        return empty(
            f"no player with code {int(code)} in sem_players at "
            f"{when:%Y-%m-%d %H:%M}Z. The code is the stable cross-season FPL "
            f"player code, not an element id."
        )

    sql = (
        "SELECT f.season, f.gw, fx.opponent, "
        "CASE WHEN f.was_home THEN 'H' ELSE 'A' END AS venue, "
        "f.minutes, f.total_points, f.goals_scored, f.assists, "
        "f.clean_sheets, f.goals_conceded, f.expected_goals, "
        "f.expected_assists, f.expected_goals_conceded, f.bonus, f.bps, "
        "f.starts, f.saves, f.defensive_contribution, f.fixture_id "
        "FROM sem_player_form($1::TIMESTAMPTZ) f "
        "LEFT JOIN sem_fixtures($2::TIMESTAMPTZ) fx "
        "  ON fx.season = f.season AND fx.fixture_id = f.fixture_id "
        "  AND fx.is_home = f.was_home "
        "WHERE f.code = $3"
    )
    params: list[Any] = [when, when, int(code)]
    if season is not None:
        sql += " AND f.season = $4"
        params.append(season)
    sql += " ORDER BY f.season DESC, f.gw DESC LIMIT " + str(int(last_k))
    frame = q(wh, sql, tuple(params))

    if frame.empty:
        scope = f"in {season}" if season else "in any season"
        return empty(
            f"no settled gameweeks for {who['web_name']} {scope} at "
            f"{when:%Y-%m-%d %H:%M}Z; form rows appear only after a "
            f"gameweek's points are finalised."
        )

    records = list(reversed(frame.to_dict("records")))
    rows = [
        {
            "season": str(r["season"]),
            "gw": _i(r["gw"]),
            "fixture_id": _i(r["fixture_id"]),
            "opponent": None if r["opponent"] is None else str(r["opponent"]),
            "venue": str(r["venue"]),
            "minutes": _i(r["minutes"]),
            "points": _i(r["total_points"]),
            "goals": _i(r["goals_scored"]),
            "assists": _i(r["assists"]),
            "clean_sheets": _i(r["clean_sheets"]),
            "goals_conceded": _i(r["goals_conceded"]),
            "xg": _f(r["expected_goals"]),
            "xa": _f(r["expected_assists"]),
            "xgc": _f(r["expected_goals_conceded"]),
            "bonus": _i(r["bonus"]),
            "bps": _i(r["bps"]),
            "starts": _i(r["starts"]),
            "saves": _i(r["saves"]),
            "defensive_contribution": _i(r["defensive_contribution"]),
        }
        for r in records
    ]

    covered = sorted({r["season"] for r in rows})
    if season is not None:
        note = f"rows are from {season}, the season this run filtered to."
    elif SEASON_DEFAULT not in covered:
        note = (
            f"rows are from {', '.join(covered)}; {SEASON_DEFAULT} has no "
            f"settled gameweek at this instant."
        )
    elif len(covered) > 1:
        note = f"rows span {', '.join(covered)}, oldest first."
    else:
        note = f"every row is from {covered[0]}."

    minutes = [r["minutes"] or 0 for r in rows]
    points = [r["points"] or 0 for r in rows]
    return {
        "as_of": when.isoformat(),
        "player": who,
        "season_filter": season,
        "last_k": int(last_k),
        "seasons_covered": covered,
        "season_note": note,
        "totals": {
            "rows": len(rows),
            "minutes": sum(minutes),
            "points": sum(points),
            "points_per_start": (
                round(sum(points) / sum(1 for r in rows if (r["starts"] or 0) > 0), 2)
                if any((r["starts"] or 0) > 0 for r in rows) else None
            ),
        },
        "rows": rows,
    }


PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code"],
    "properties": {
        "code": {"type": "integer", "minimum": 1, "description":
                 "The stable cross-season FPL player code, not an element id."},
        "last_k": {"type": "integer", "minimum": 1, "maximum": MAX_LAST_K,
                   "default": 6, "description":
                   "How many settled gameweeks to return, newest last."},
        "season": {"type": ["string", "null"], "default": None, "description":
                   "Restrict rows to one season. Null walks backwards across "
                   "seasons until the row cap is filled."},
        "as_of": {"type": ["string", "null"], "default": None, "description":
                  "ISO-8601 instant carrying a timezone. A gameweek is "
                  "visible only once its points were finalised at or before "
                  "it. Null means now."},
    },
}

_ROW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "gw", "fixture_id", "opponent", "venue", "minutes",
                 "points", "goals", "assists", "clean_sheets",
                 "goals_conceded", "xg", "xa", "xgc", "bonus", "bps", "starts",
                 "saves", "defensive_contribution"],
    "properties": {
        "season": {"type": "string", "description":
                   "The season this gameweek belongs to, stated per row "
                   "because a form table can span a season boundary."},
        "gw": {"type": ["integer", "null"], "description": "The gameweek."},
        "fixture_id": {"type": ["integer", "null"], "description":
                       "The fixture this return came from."},
        "opponent": {"type": ["string", "null"], "description":
                     "The opponent's short name, or null when the schedule "
                     "row for this fixture is not in the warehouse."},
        "venue": {"enum": ["H", "A"], "description": "Home or away."},
        "minutes": {"type": ["integer", "null"], "description": "Minutes played."},
        "points": {"type": ["integer", "null"], "description":
                   "FPL points scored in this fixture, as finalised."},
        "goals": {"type": ["integer", "null"], "description": "Goals scored."},
        "assists": {"type": ["integer", "null"], "description": "Assists."},
        "clean_sheets": {"type": ["integer", "null"], "description":
                         "One when the clean sheet was awarded."},
        "goals_conceded": {"type": ["integer", "null"], "description":
                           "Goals conceded while on the pitch."},
        "xg": {"type": ["number", "null"], "description":
               "Expected goals, FPL's own published number for this fixture."},
        "xa": {"type": ["number", "null"], "description":
               "Expected assists, FPL's own published number."},
        "xgc": {"type": ["number", "null"], "description":
                "Expected goals conceded, FPL's own published number."},
        "bonus": {"type": ["integer", "null"], "description": "Bonus points."},
        "bps": {"type": ["integer", "null"], "description":
                "The bonus points system score, which is not the bonus."},
        "starts": {"type": ["integer", "null"], "description":
                   "One when the player started the fixture."},
        "saves": {"type": ["integer", "null"], "description":
                  "Saves, populated for goalkeepers."},
        "defensive_contribution": {"type": ["integer", "null"], "description":
                                   "The defensive contribution count FPL "
                                   "publishes for this fixture."},
    },
}

_PLAYER: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "web_name", "position", "team", "price", "season"],
    "properties": {
        "code": {"type": "integer", "description": "The code that was asked for."},
        "web_name": {"type": "string", "description":
                     "The name FPL displays for this player."},
        "position": {"type": ["string", "null"], "description":
                     "GKP, DEF, MID or FWD."},
        "team": {"type": ["string", "null"], "description":
                 "The club short name in the latest season holding the code."},
        "price": {"type": ["number", "null"], "description":
                  "Current price in millions, from the same identity row."},
        "season": {"type": "string", "description":
                   "The season the identity row was read from, which is the "
                   "latest one this code appears in, not necessarily the "
                   "season of the rows below."},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["as_of", "player", "season_filter", "last_k",
                 "seasons_covered", "season_note", "totals", "rows"],
    "properties": {
        "as_of": {"type": "string", "description":
                  "The instant this panel read as of, ISO-8601 UTC."},
        "player": _PLAYER,
        "season_filter": {"type": ["string", "null"], "description":
                          "The season filter this run applied, echoed."},
        "last_k": {"type": "integer", "description":
                   "The row cap this run applied."},
        "seasons_covered": {"type": "array", "items": {"type": "string"},
                            "description":
                            "Every season the returned rows come from."},
        "season_note": {"type": "string", "description":
                        "One sentence naming which seasons these rows are "
                        "from, for a surface that prints one line."},
        "totals": {
            "type": "object",
            "additionalProperties": False,
            "required": ["rows", "minutes", "points", "points_per_start"],
            "properties": {
                "rows": {"type": "integer", "description":
                         "How many settled gameweeks are below."},
                "minutes": {"type": "integer", "description":
                            "Minutes summed over those gameweeks."},
                "points": {"type": "integer", "description":
                           "FPL points summed over those gameweeks."},
                "points_per_start": {"type": ["number", "null"], "description":
                                     "Points divided by starts, null when "
                                     "there was no start in the window."},
            },
        },
        "rows": {"type": "array", "items": _ROW, "description":
                 "The settled gameweeks, oldest first."},
    },
}

register_script(
    name="player_form",
    fn=player_form,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Player form",
    description="One player's settled gameweeks: points, minutes, the official "
                "expected numbers, bonus and BPS, with the opponent and the "
                "season of every row named.",
)
