"""market_watch — the bookmaker-derived priors, finally read by something.

``fact_odds_derived`` holds real derivation work: clean-sheet probabilities
inverted from correct-score grids, team scoring rates (lambdas) from totals
ladders and Dixon-Coles fits, and per-player anytime/xG-share from scorer
markets. It was written on 2026-08-20 and then **nothing ever read it** — the
module docstring claimed "the projection model" consumed it; no such consumer
existed. Finished-looking output with no downstream is indistinguishable from
no output, so this panel is the table's first reader.

Honesty constraints:

* **Staleness is stated, not hidden.** Rows are keyed by fixture; if the
  derivation has not run for the fixtures the reader cares about (the coming
  gameweek), this panel says which gameweek the numbers describe rather than
  presenting last week's market as this week's.
* **Method disagreement is the content, not noise.** A clean-sheet number is
  shown per method (grid inversion vs Dixon-Coles vs independent Poisson);
  where they diverge, that spread is the uncertainty a reader should carry.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import empty, q, season_param

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
    },
}

_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "market", "value"],
    "properties": {
        "name": {"type": "string"},
        "market": {"type": "string"},
        "method": {"type": "string"},
        "value": {"type": "number"},
        "spread": {"type": ["number", "null"]},
        "fixture": {"type": "string"},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "rows"],
    "properties": {
        "season": {"type": "string"},
        "as_of": {"type": ["string", "null"]},
        "coverage": {"type": "string"},
        "rows": {"type": "array", "items": _ROW},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def market_watch(wh, *, season: str, limit: int = 20) -> dict[str, Any]:
    limit = int(limit)

    derived = q(
        wh,
        """
        WITH latest AS (
          SELECT *, row_number() OVER (
            PARTITION BY fixture_key, entity_type, entity_code, market, method
            ORDER BY as_of DESC) AS rn
          FROM fact_odds_derived WHERE season = ?
        )
        SELECT fixture_key, entity_type, entity_code, market, method, value, as_of
        FROM latest WHERE rn = 1
        """,
        (season,),
    )
    if derived.empty:
        return empty(
            "No derived odds exist yet. `uv run python scripts/ingest_odds_extras.py` "
            "derives clean-sheet, team-lambda and scorer priors from stored odds."
        )

    # Which fixtures do these rows describe? The key embeds the date.
    dates = sorted({k.split(":")[1] for k in derived["fixture_key"]})
    coverage = f"fixtures dated {dates[0]}..{dates[-1]}" if dates else "unknown"
    notes: list[str] = []
    notes.append(
        f"Derived from odds for {derived['fixture_key'].nunique()} fixtures "
        f"({coverage}). If that is not the coming gameweek, re-run the "
        "derivation before acting on these numbers."
    )

    teams = q(wh, "SELECT DISTINCT team_code, short_name FROM dim_team WHERE season = ?", (season,))
    tname = dict(zip(teams["team_code"].astype(int), teams["short_name"]))
    players = q(wh, "SELECT DISTINCT code, web_name FROM dim_player WHERE season = ?", (season,))
    pname = dict(zip(players["code"].astype(int), players["web_name"]))

    rows: list[dict[str, Any]] = []

    cs = derived[derived["market"] == "clean_sheet_prob"]
    if not cs.empty:
        # One row per (team, fixture): the mean across methods, with the
        # method spread carried as its own column -- the disagreement IS the
        # uncertainty, and averaging it away silently would fabricate
        # confidence.
        g = cs.groupby(["entity_code", "fixture_key"])["value"]
        agg = g.agg(["mean", "min", "max", "count"]).reset_index()
        agg = agg.sort_values("mean", ascending=False)
        for r in agg.itertuples(index=False):
            rows.append({
                "name": tname.get(int(r.entity_code), str(int(r.entity_code))),
                "market": "clean sheet",
                "method": f"{int(r.count)} methods",
                "value": round(float(r.mean), 3),
                "spread": round(float(r.max - r.min), 3),
                "fixture": str(r.fixture_key).split(":", 2)[2],
            })

    xg = derived[derived["market"] == "xg_share"].sort_values("value", ascending=False)
    for r in xg.head(limit).itertuples(index=False):
        rows.append({
            "name": pname.get(int(r.entity_code), str(int(r.entity_code))),
            "market": "xG share",
            "method": str(r.method),
            "value": round(float(r.value), 3),
            "spread": None,
            "fixture": str(r.fixture_key).split(":", 2)[2],
        })

    if not rows:
        return empty("Derived odds exist but carry no clean-sheet or xG-share rows.")

    return {
        "season": season,
        "as_of": str(derived["as_of"].max()),
        "coverage": coverage,
        "rows": rows[: max(limit, 20)],
        "notes": notes,
    }


register_script(
    "market_watch",
    market_watch,
    params_schema=PARAMS,
    result_schema=RESULT,
    description="Bookmaker-derived priors: clean-sheet probability per team "
                "(with cross-method spread) and player xG shares.",
)
