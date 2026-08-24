"""The warehouse briefing: what the chat agent knows before its first query.

An agent cannot query what it does not know exists. This module generates a
compact, LIVE briefing -- injected into the headless agent's system prompt on
every turn -- describing the semantic macros, what data each actually holds
right now (coverage measured, not asserted), the metrics vocabulary, the
rules that answers must read rather than hardcode, and the honest gaps.

Generated from the real warehouse so it cannot rot: a new macro, a new feed,
or a freshly settled gameweek shows up here with no code change. Keep it
COMPACT -- this rides in a system prompt on every turn; the target is a
briefing the agent can hold, not a schema dump.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

#: Grain and purpose, one line each. Columns come from the live DESCRIBE so
#: they are never stale; these purposes are the only hand-written part.
_MACRO_PURPOSE: dict[str, str] = {
    "sem_players": "identity+market state per (season, code): price, own%, status, news",
    "sem_projections": "every provider's xPts/xMins/p_appear per (source, season, gw, code)",
    "sem_projection_consensus": "per (season, gw, code): mean/min/max/SPREAD across sources -- spread IS the uncertainty",
    "sem_player_form": "OFFICIAL settled returns per (season, gw, code, fixture): points, minutes, goals, xG/xA/xGC, bps, defcon, cards",
    "sem_player_match_stats": "third-party per-match read: shots, xG, xA, xGOT, chances created, defensive actions",
    "sem_ownership": "FPL marginal own% beside external EO metrics per (season, code)",
    "sem_fixtures": "schedule per (season, fixture_id, team-side): opponent, home/away, kickoff",
    "sem_manager_picks": "crawled managers' locked squads per (season, gw, entry, code) with rank/cohort",
    "sem_manager_transfers": "crawled managers' transfers, player names both ways",
    "sem_elite_ownership": "per (season, gw, cohort, code): owned%, captain%, EO% within crawled cohorts",
}


def _macro_lines(wh: Warehouse, now: dt.datetime) -> list[str]:
    lines = []
    ts = f"TIMESTAMPTZ '{now.isoformat()}'"
    for name, purpose in _MACRO_PURPOSE.items():
        try:
            cols = wh.sql(f"DESCRIBE SELECT * FROM {name}({ts}) LIMIT 0")
            col_names = ", ".join(cols["column_name"].tolist())
        except Exception:
            continue  # a macro absent from this warehouse is not briefed
        lines.append(f"- {name}(t): {purpose}\n    columns: {col_names}")
    return lines


def _coverage_lines(wh: Warehouse, now: dt.datetime, season: str) -> list[str]:
    ts = f"TIMESTAMPTZ '{now.isoformat()}'"
    out = []
    try:
        cons = wh.sql(
            f"SELECT MIN(gw), MAX(gw), COUNT(DISTINCT code) FROM "
            f"sem_projection_consensus({ts}) WHERE season = ?", (season,))
        r = cons.iloc[0]
        out.append(f"- projections: GW{int(r[0])}-GW{int(r[1])}, {int(r[2])} players")
    except Exception:
        out.append("- projections: none")
    try:
        form = wh.sql(
            f"SELECT COUNT(DISTINCT gw), COUNT(*) FROM sem_player_form({ts}) "
            f"WHERE season = ?", (season,))
        n_gw, n = int(form.iloc[0][0]), int(form.iloc[0][1])
        out.append(f"- official settled returns {season}: {n_gw} gameweek(s), {n} rows"
                   + ("" if n else " -- the current season has not settled yet; "
                      "for recent form use prior seasons and SAY so"))
    except Exception:
        pass
    try:
        ms = wh.sql(
            f"SELECT MIN(gw), MAX(gw), COUNT(*) FROM sem_player_match_stats({ts}) "
            f"WHERE season = ?", (season,))
        r = ms.iloc[0]
        if int(r[2]):
            out.append(f"- shots/xG per match ({season}): GW{int(r[0])}-GW{int(r[1])} "
                       f"only ({int(r[2])} rows) -- earlier seasons have official "
                       f"xG in sem_player_form but NO shots")
    except Exception:
        pass
    try:
        mp = wh.sql(
            f"SELECT COUNT(DISTINCT entry), MAX(gw) FROM sem_manager_picks({ts}) "
            f"WHERE season = ?", (season,))
        r = mp.iloc[0]
        out.append(f"- crawled manager squads: {int(r[0])} managers through GW{int(r[1])} "
                   f"(cohorts: top-of-overall sample + named elite)")
    except Exception:
        pass
    try:
        eo = wh.sql(
            f"SELECT metric, MAX(gw) FROM (SELECT eo_metric metric, eo_gw gw FROM "
            f"sem_ownership({ts}) WHERE season = ? AND eo_metric IS NOT NULL) GROUP BY 1",
            (season,))
        if not eo.empty:
            bits = ", ".join(f"{r['metric']}@GW{int(r[1])}" for _, r in eo.iterrows())
            out.append(f"- external EO metrics live: {bits}")
    except Exception:
        pass
    return out


def _rules_lines() -> list[str]:
    from fpl_edge.rules import rules

    reg = rules()
    keys = [
        ("transfers.hit_cost", "hit cost"),
        ("transfers.free_per_gw", "free transfers per GW"),
        ("transfers.max_banked", "max banked FTs"),
        ("discipline.yellows_for_ban_1", "yellows for a 1-match ban"),
        ("defcon.def_threshold", "DEFCON threshold DEF"),
        ("defcon.mid_fwd_threshold", "DEFCON threshold MID/FWD"),
    ]
    out = []
    for key, label in keys:
        try:
            v = reg.get(key)
            if v is not None:
                out.append(f"{label}={v}")
        except Exception:
            continue
    return [", ".join(out)] if out else []


def warehouse_briefing(
    db_path: Path | None = None,
    *,
    season: str = "2026-27",
    entry_id: int | None = None,
) -> str:
    """The briefing string. Reads a live copy; safe alongside the writer."""
    now = dt.datetime.now(UTC)
    wh = Warehouse.read_copy(db_path) if db_path else Warehouse.read_copy()
    try:
        macro_lines = _macro_lines(wh, now)
        coverage = _coverage_lines(wh, now, season)
        try:
            nxt = wh.sql(
                "SELECT gw, deadline_utc FROM (SELECT *, row_number() OVER "
                "(PARTITION BY season, gw ORDER BY as_of DESC) rn FROM dim_event "
                "WHERE season = ? AND deadline_utc > ?) WHERE rn = 1 "
                "ORDER BY deadline_utc LIMIT 1", (season, now))
            deadline = (f"next deadline: GW{int(nxt.iloc[0]['gw'])} at "
                        f"{nxt.iloc[0]['deadline_utc']}") if not nxt.empty else ""
        except Exception:
            deadline = ""
    finally:
        wh.close()

    rules_line = _rules_lines()
    entry = entry_id if entry_id is not None else 4490171

    parts = [
        f"# Warehouse briefing (generated {now:%Y-%m-%d %H:%M}Z, season {season})",
        deadline,
        f"The user's FPL entry id is {entry}.",
        "",
        "## Query surface — DuckDB table macros, each takes an as-of TIMESTAMPTZ.",
        "Call them like: SELECT ... FROM sem_players(TIMESTAMPTZ '<now>') WHERE season='"
        + season + "'. Pass a past instant to see exactly what was knowable then.",
        *macro_lines,
        "",
        "## Live coverage (measured now — trust this over assumptions)",
        *coverage,
        "",
        "## Rules (READ these values; never hardcode game rules)",
        *rules_line,
        "",
        "## Standing honesty rules",
        "- Every number you state must come from a tool call this turn. Never "
        "recall FPL statistics from training data.",
        "- Money is integer tenths in raw tables; macros expose price in £m.",
        "- Player identity is the stable `code`; element_id changes each season.",
        "- If data does not exist, say so and offer the nearest thing that does.",
        "- State the as-of instant of anything time-sensitive.",
    ]
    return "\n".join(p for p in parts if p is not None)
