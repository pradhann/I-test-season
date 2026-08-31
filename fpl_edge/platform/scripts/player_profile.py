"""The player profile panel: one player's Understat season, read FPL-first.

One panel script, two consumers (the chat toolbelt and the xPoints drawer),
per the house rule. The lens is deliberately FPL, never IRL scouting: shot
volume trend, xG against actual returns (finishing luck, labelled as luck),
xA and key passes (creativity that becomes assist points), and the minutes
pattern (starts vs cameos -- the denominator of every projection). Nothing
here rates a player as a footballer; it prices him as an FPL asset.

**This panel NEVER fetches.** It reads ``understat_player_match`` /
``understat_player_map`` -- written only by ``fpl_edge/ingest/understat.py``,
the one sanctioned fetch path -- and when the warehouse holds nothing for a
player it says so, with the exact action that would fill the gap. A panel that
fetched on read would block its 10s budget on understat.com's latency and turn
every drawer open into a crawl.

Two honesty rules inherited from the ingest and repeated in the payload:

* The numbers are **Understat's shot model, not FPL points**. xG knows nothing
  about clean sheets, bonus, or defensive contribution; ``note`` says so and
  renderers must show it.
* ``goals - xG`` is labelled **finishing luck**, not finishing skill: over a
  handful of matches it is mostly variance, and the label field carries that
  caveat so a renderer cannot present a hot streak as ability.

Reads are point-in-time: latest row per (understat_id, season, match_id) at or
before ``as_of`` (default now). The tables are append-only with the fetch
instant stamped, so "what did his profile look like before the deadline" is a
real query, not a guess.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import UTC, empty, q, season_param

#: How the empty panel tells its consumers to fill itself. Shared wording so
#: the drawer button, the chat tool and the reason string all point at the
#: same one sanctioned path.
FETCH_HINT = (
    "fetch it via the chat player_profile tool or the drawer's Fetch-profile "
    "button (POST /api/players/{code}/fetch_profile)"
)


def _instant(as_of: str | None) -> dt.datetime | None:
    if as_of is None:
        return dt.datetime.now(UTC)
    try:
        parsed = dt.datetime.fromisoformat(as_of)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _has_table(wh, name: str) -> bool:
    df = q(wh, "SELECT table_name FROM information_schema.tables "
               "WHERE table_schema = 'main' AND table_name = ?", (name,))
    return not df.empty


def _round(v: float, nd: int = 2) -> float:
    return float(round(float(v), nd))


def player_profile(wh, code: int, season: str, as_of: str | None = None) -> dict[str, Any]:
    """One player's cached Understat season: trend, totals, luck, minutes."""
    t = _instant(as_of)
    if t is None:
        return empty(f"as_of {as_of!r} is not an ISO instant; nothing was read.")

    # Who is this, in our own terms? A code dim_player has never seen gets a
    # refusal, not a profile of nobody.
    # ORDER BY/LIMIT, not QUALIFY: a whole-relation window over zero rows
    # yields one all-NULL row on this DuckDB, and an unknown code must read
    # as ABSENT, not as a player named None.
    ident = q(wh,
        "SELECT web_name FROM dim_player "
        "WHERE season = ? AND code = ? AND as_of <= ? AND web_name IS NOT NULL "
        "ORDER BY as_of DESC LIMIT 1",
        (season, int(code), t))
    if ident.empty:
        return empty(f"No player with code {code} in dim_player for {season} "
                     f"as of {t:%Y-%m-%d %H:%M}Z.")
    web_name = str(ident.iloc[0]["web_name"])

    if not (_has_table(wh, "understat_player_match")
            and _has_table(wh, "understat_player_map")):
        return empty(
            f"No Understat data for {web_name} yet -- the understat tables do "
            f"not exist in this warehouse; {FETCH_HINT.format(code=int(code))}."
        )

    rows = q(wh, """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY understat_id, season, match_id ORDER BY as_of DESC) rn
            FROM understat_player_match
            WHERE code = ? AND season = ? AND as_of <= ?
        ) WHERE rn = 1 ORDER BY date, match_id
        """, (int(code), season, t))
    if rows.empty:
        return empty(
            f"No Understat rows for {web_name} ({season}) in the warehouse yet; "
            f"{FETCH_HINT.format(code=int(code))}. The panel never fetches."
        )

    mapping = q(wh, """
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of DESC) rn
            FROM understat_player_map WHERE code = ? AND as_of <= ?
        ) WHERE rn = 1
        """, (int(code), t))
    source: dict[str, Any] = {
        "understat_id": int(rows.iloc[0]["understat_id"]),
        "understat_name": None, "understat_team": None, "resolved_basis": None,
    }
    if not mapping.empty:
        m = mapping.iloc[0]
        source = {
            "understat_id": int(m["understat_id"]),
            "understat_name": str(m["understat_name"]),
            "understat_team": None if m["understat_team"] is None else str(m["understat_team"]),
            "resolved_basis": str(m["resolved_basis"]),
        }

    team = source["understat_team"]
    matches: list[dict[str, Any]] = []
    for r in rows.itertuples():
        h, a = str(r.h_team or ""), str(r.a_team or "")
        venue, opponent = None, None
        if team and team == h:
            venue, opponent = "H", a
        elif team and team == a:
            venue, opponent = "A", h
        matches.append({
            "date": str(pd.Timestamp(r.date).date()),
            "opponent": opponent,
            "venue": venue,
            "score": f"{h} {int(r.h_goals)}-{int(r.a_goals)} {a}" if h and a else None,
            "minutes": int(r.minutes),
            "started": str(r.position or "") != "Sub",
            "shots": int(r.shots),
            "goals": int(r.goals),
            "assists": int(r.assists),
            "key_passes": int(r.key_passes),
            "xg": _round(r.xg),
            "xa": _round(r.xa),
            "npg": int(r.npg),
            "npxg": _round(r.npxg),
        })

    n = len(matches)
    minutes = int(rows["minutes"].sum())
    goals = int(rows["goals"].sum())
    npg = int(rows["npg"].sum())
    xg = float(rows["xg"].sum())
    npxg = float(rows["npxg"].sum())
    xa = float(rows["xa"].sum())
    totals = {
        "matches": n, "minutes": minutes, "shots": int(rows["shots"].sum()),
        "goals": goals, "assists": int(rows["assists"].sum()),
        "key_passes": int(rows["key_passes"].sum()),
        "xg": _round(xg), "xa": _round(xa), "npg": npg, "npxg": _round(npxg),
    }

    per90 = None
    if minutes > 0:
        f = 90.0 / minutes
        per90 = {
            "shots": _round(totals["shots"] * f), "xg": _round(xg * f),
            "xa": _round(xa * f), "key_passes": _round(totals["key_passes"] * f),
            "npxg": _round(npxg * f),
        }

    starts = sum(1 for m in matches if m["started"])
    minutes_pattern = {
        "starts": starts,
        "sub_appearances": n - starts,
        "full_90s": sum(1 for m in matches if m["minutes"] >= 90),
        "avg_minutes": _round(minutes / n, 1),
        "last5_minutes": [m["minutes"] for m in matches[-5:]],
    }

    return {
        "season": season,
        "code": int(code),
        "name": web_name,
        "source": source,
        "matches": matches,
        "totals": totals,
        "per90": per90,
        "finishing": {
            "goals_minus_xg": _round(goals - xg),
            "npg_minus_npxg": _round(npg - npxg),
            "label": (
                "finishing luck: goals minus Understat xG. Positive = running "
                "hot, negative = running cold; over a handful of matches this "
                "is variance, not proven skill."
            ),
        },
        "minutes_pattern": minutes_pattern,
        "as_of": str(rows["as_of"].max()),
        "note": (
            "Numbers are Understat's shot model (xG/xA), copied verbatim -- "
            "not FPL points and blind to clean sheets, bonus and DC."
        ),
    }


_MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "minutes", "started", "shots", "goals", "assists",
                 "key_passes", "xg", "xa", "npg", "npxg"],
    "properties": {
        "date": {"type": "string"},
        "opponent": {"type": ["string", "null"],
                     "description": "Null when the stored Understat team name "
                                    "matches neither side (e.g. mid-season "
                                    "transfer); the score string still tells "
                                    "the truth."},
        "venue": {"type": ["string", "null"], "enum": ["H", "A", None]},
        "score": {"type": ["string", "null"]},
        "minutes": {"type": "integer"},
        "started": {"type": "boolean",
                    "description": "Understat position != 'Sub'."},
        "shots": {"type": "integer"},
        "goals": {"type": "integer"},
        "assists": {"type": "integer"},
        "key_passes": {"type": "integer"},
        "xg": {"type": "number"},
        "xa": {"type": "number"},
        "npg": {"type": "integer"},
        "npxg": {"type": "number"},
    },
}

PROFILE_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code"],
    "properties": {
        "code": {"type": "integer", "minimum": 1,
                 "description": "Our stable PlayerCode, never element_id."},
        "season": season_param(),
        "as_of": {"type": ["string", "null"], "default": None,
                  "description": "ISO instant; reads are point-in-time to it."},
    },
}

PROFILE_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "code", "name", "source", "matches", "totals",
                 "finishing", "minutes_pattern", "as_of", "note"],
    "properties": {
        "season": {"type": "string"},
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "source": {
            "type": "object",
            "additionalProperties": False,
            "required": ["understat_id"],
            "properties": {
                "understat_id": {"type": "integer"},
                "understat_name": {"type": ["string", "null"]},
                "understat_team": {"type": ["string", "null"]},
                "resolved_basis": {
                    "type": ["string", "null"],
                    "description": "'exact' or 'containment' -- how the strict "
                                   "resolver placed this player. Never an edit "
                                   "distance, so never a guess.",
                },
            },
        },
        "matches": {"type": "array", "items": _MATCH_SCHEMA,
                    "description": "Chronological, oldest first."},
        "totals": {
            "type": "object",
            "additionalProperties": False,
            "required": ["matches", "minutes", "shots", "goals", "assists",
                         "key_passes", "xg", "xa", "npg", "npxg"],
            "properties": {k: {"type": "integer"} for k in
                           ("matches", "minutes", "shots", "goals", "assists",
                            "key_passes", "npg")} |
                          {k: {"type": "number"} for k in ("xg", "xa", "npxg")},
        },
        "per90": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "description": "Null when total minutes are zero -- never divided anyway.",
            "properties": {k: {"type": "number"} for k in
                           ("shots", "xg", "xa", "key_passes", "npxg")},
        },
        "finishing": {
            "type": "object",
            "additionalProperties": False,
            "required": ["goals_minus_xg", "npg_minus_npxg", "label"],
            "properties": {
                "goals_minus_xg": {"type": "number"},
                "npg_minus_npxg": {"type": "number"},
                "label": {"type": "string",
                          "description": "Renderers must show this caveat, not "
                                         "just the signed number."},
            },
        },
        "minutes_pattern": {
            "type": "object",
            "additionalProperties": False,
            "required": ["starts", "sub_appearances", "full_90s",
                         "avg_minutes", "last5_minutes"],
            "properties": {
                "starts": {"type": "integer"},
                "sub_appearances": {"type": "integer"},
                "full_90s": {"type": "integer"},
                "avg_minutes": {"type": "number"},
                "last5_minutes": {"type": "array", "items": {"type": "integer"}},
            },
        },
        "as_of": {"type": "string",
                  "description": "The newest fetch instant behind these rows."},
        "note": {"type": "string"},
    },
}

register_script(
    "player_profile",
    player_profile,
    params_schema=PROFILE_PARAMS,
    result_schema=PROFILE_RESULT,
    title="Player profile (Understat)",
    description=(
        "One player's cached Understat season through the FPL lens: per-match "
        "shots/xG/xA trend, totals, finishing luck (labelled as luck), and the "
        "minutes pattern. Reads only; the fetch is a separate, on-demand path."
    ),
)
