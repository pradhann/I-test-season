"""ownership_eo — the template and effective-ownership panel.

Effective ownership (EO) is the share of a cohort that *effectively* holds a
player once captaincy is counted: a player owned by 60% and captained by half
of them has 90% EO, so values above 100% are normal for premium captains.
Beside every EO metric this panel keeps FPL's own **marginal** ownership
(``selected_by_pct`` — no captaincy weighting) so the two are never confused.

Two data traps this script exists to not fall into (docs/platform/
data_audit.md Q6, both fired during the audit):

1. **Metric names.** The feed writes ``eo_predicted`` / ``eo_top10k`` /
   ``eo_elite`` — NOT the ``own_*`` names an old migration comment documents.
   A filter on the documented names returns zero rows silently.
2. **Season/gw split.** ``eo_top10k`` and ``eo_elite`` currently exist only
   under LiveFPL's last-resolved cohort — season 2025-26, GW38 — while
   ``eo_predicted`` is under the current season. A single "current season and
   gw" join silently drops two of the three metrics. Here, metrics from any
   *other* season are quarantined into ``last_season`` with their real
   season/gw stamped on them, and are never merged into the current rows.

Crawl data (``fact_manager_pick``) is used when it holds picks for the
requested season: the panel then adds observed cohort own%/EO% and states the
cohort size. When the pick tables are empty — the state at build time — the
columns stay null and ``cohort_note`` says exactly what is on file instead.

3. **The cohort denominator.** This script used to compute its own own%/EO%
   with a raw query over ``fact_manager_pick`` that had **no cohort filter**:
   its denominator was every crawled entry (1,508 in the live warehouse —
   top1k and elite blended), and it labelled the answer "elite". It now reads
   ``sem_elite_ownership``, the one place effective ownership is defined, and
   names the cohort it is reporting in ``cohort`` / ``cohort_note``. The
   ``elite_*`` row keys keep their names (the web view selects on them) and
   carry whatever cohort ``cohort`` names — ``elite`` by default.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
)

UTC = dt.timezone.utc

PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        # Differentials: nobody's differential is owned by a third of the game.
        "diff_max_own": {"type": "number", "minimum": 0.5, "maximum": 100, "default": 15.0},
        # Which crawled cohort the elite_* columns report. Mutually exclusive
        # by construction (sem_manager_cohort); 'unclassified' is the crawl-bug
        # bucket — entries holding picks with no dim_manager row.
        "cohort": {"type": "string", "enum": ["elite", "top1k", "unclassified"],
                   "default": "elite"},
        # Squad coverage may touch the network (same path as squad_overview);
        # callers that must stay offline — tests — turn it off.
        "coverage": {"type": "boolean", "default": True},
    },
}

_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},        # FPL marginal, percent
        "eo_pred_pct": {"type": ["number", "null"]},    # LiveFPL predicted EO, percent
        "elite_own_pct": {"type": ["number", "null"]},  # crawled cohort, percent
        "elite_eo_pct": {"type": ["number", "null"]},   # crawled cohort EO, percent
        "xpts": {"type": ["number", "null"]},           # consensus mean for xpts_gw
        "xpts_spread": {"type": ["number", "null"]},
        "n_sources": {"type": ["integer", "null"]},
        "in_squad": {"type": ["boolean", "null"]},      # null = squad unreadable
    },
}

_STALE_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "eo_top10k_pct": {"type": ["number", "null"]},
        "eo_elite_pct": {"type": ["number", "null"]},
    },
}

# `required` keeps this branch disjoint from the registry's {empty, reason}
# shape: an honest empty has no `rows`, a real result always does.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "metrics_note", "cohort_note", "gws_covered"],
    "properties": {
        "season": {"type": "string"},
        "rows": {"type": "array", "items": _ROW},
        "differentials": {"type": "array", "items": _ROW},
        "last_season": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["season", "gw", "rows"],
            "properties": {
                "season": {"type": "string"},
                "gw": {"type": "integer"},
                "rows": {"type": "array", "items": _STALE_ROW},
            },
        },
        "metrics_note": {"type": "string"},
        "cohort_note": {"type": "string"},
        # Which cohort the elite_own_pct / elite_eo_pct columns describe, and
        # the denominator behind them. Null when no crawled squad was found.
        "cohort": {"type": "string"},
        "cohort_n": {"type": ["integer", "null"]},
        "cohort_gw": {"type": ["integer", "null"]},
        "gws_covered": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["metric", "season", "gw"],
                "properties": {
                    "metric": {"type": "string"},
                    "provider": {"type": ["string", "null"]},
                    "season": {"type": "string"},
                    "gw": {"type": "integer"},
                    "players": {"type": ["integer", "null"]},
                    "latest": {"type": ["string", "null"]},
                    "live": {"type": "boolean"},
                },
            },
        },
        "xpts_gw": {"type": ["integer", "null"]},
        "squad_note": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
    },
}


def _f(x, nd: int = 1) -> float | None:
    """NaN/None-safe rounded float — the JSON boundary for pandas values."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(v, nd)


def _i(x) -> int | None:
    if x is None or x != x:
        return None
    return int(x)


def _pct(x) -> float | None:
    """A cohort fraction as a percent, or None. 0.0 stays an honest 0.0."""
    if x is None or x != x:
        return None
    return round(float(x) * 100.0, 1)


def _tables_present(wh, names: tuple[str, ...]) -> set[str]:
    df = q(
        wh,
        "SELECT table_name FROM information_schema.tables WHERE table_name IN ("
        + ", ".join("?" for _ in names) + ")",
        names,
    )
    return set(df["table_name"]) if not df.empty else set()


def _squad_codes(wh, season: str) -> tuple[set[int] | None, str]:
    """The user's 15 as player codes, or (None, why-not).

    Same read path as squad_overview (QuestionRouter._team_state): private API,
    then public picks, then the manually entered squad. Any failure — network
    down, nothing published yet — degrades to unreadable, never to a crash.
    """
    from fpl_edge.config import USER

    try:
        from fpl_edge.interfaces.qa import QuestionRouter

        router = QuestionRouter(wh, season=season, entry_id=int(USER.entry_id))
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 — a panel reports, it does not crash
        return None, f"squad unreadable ({type(exc).__name__}); coverage column blank"
    if state is None or state.picks is None:
        return None, "no squad visible for your entry yet; coverage column blank"
    source = getattr(state.provenance, "name", str(state.provenance))
    return {int(p.code) for p in state.picks}, f"your squad read via {source}"


def ownership_eo(
    wh,
    *,
    season: str,
    limit: int = 50,
    diff_max_own: float = 15.0,
    cohort: str = "elite",
    coverage: bool = True,
) -> dict[str, Any]:
    """Template and differentials from effective ownership plus consensus xPts.

    ``rows`` is the current template: ranked by the live EO metric for the
    requested season (falling back to FPL marginal ownership when no external
    feed covers it). ``differentials`` are low-ownership players with the best
    consensus xPts. ``last_season`` quarantines EO metrics recorded under a
    different season — never merged into the current table.
    """
    players = q(
        wh,
        """
        SELECT p.code, p.web_name, p.position, p.team, p.price, p.selected_by_pct
        FROM sem_players(now()) p WHERE p.season = ?
        """,
        (season,),
    )
    if players.empty:
        return empty(
            f"No {season} players in the warehouse, so neither a template nor "
            f"a differential can be named. Run `make ingest` first."
        )

    # -- what EO data exists, enumerated rather than assumed (trap 1 and 2) --
    cov = q(
        wh,
        """
        SELECT provider, metric, season, gw, count(DISTINCT code) AS players,
               max(as_of) AS latest
        FROM fact_external_ownership
        GROUP BY 1, 2, 3, 4 ORDER BY metric, season, gw
        """,
    )
    gws_covered: list[dict[str, Any]] = []
    if not cov.empty:
        for _, r in cov.iterrows():
            gws_covered.append({
                "metric": str(r["metric"]),
                "provider": str(r["provider"]),
                "season": str(r["season"]),
                "gw": int(r["gw"]),
                "players": _i(r["players"]),
                "latest": str(r["latest"]),
                "live": str(r["season"]) == season,
            })

    # -- live EO pivot: requested season only, each metric at its latest gw --
    eo = q(
        wh,
        """
        WITH latest AS (
            SELECT metric, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY metric, code ORDER BY gw DESC, as_of DESC) rn
                FROM fact_external_ownership f WHERE season = ?
            ) WHERE rn = 1
        )
        SELECT code,
               max(CASE WHEN metric = 'eo_predicted' THEN value END) AS eo_predicted,
               max(CASE WHEN metric = 'eo_top10k'   THEN value END) AS eo_top10k,
               max(CASE WHEN metric = 'eo_elite'    THEN value END) AS eo_elite
        FROM latest GROUP BY code
        """,
        (season,),
    )
    eo_pred = {}
    if not eo.empty:
        eo_pred = {int(r["code"]): r["eo_predicted"] for _, r in eo.iterrows()
                   if r["eo_predicted"] == r["eo_predicted"]}

    # -- consensus xPts for the next deadline (or the earliest gw on file) --
    gw = next_gw(wh, season)
    if gw is None:
        g = q(wh, "SELECT min(gw) AS g FROM sem_projection_consensus(now()) WHERE season = ?",
              (season,))
        gw = _i(g.iloc[0]["g"]) if not g.empty else None
    xp: dict[int, tuple] = {}
    if gw is not None:
        cons = q(
            wh,
            """
            SELECT code, xpts_mean, xpts_spread, n_sources
            FROM sem_projection_consensus(now()) WHERE season = ? AND gw = ?
            """,
            (season, gw),
        )
        if not cons.empty:
            xp = {int(r["code"]): (r["xpts_mean"], r["xpts_spread"], r["n_sources"])
                  for _, r in cons.iterrows()}

    # -- the crawled cohort: own%/EO% from the ONE definition ----------------
    # sem_elite_ownership is the canonical effective ownership (mean FPL
    # multiplier over the cohort's managers with a stored squad). This script
    # does not compute its own: the previous local query had no cohort filter
    # at all and reported a blended top1k+elite denominator as "elite".
    elite: dict[int, tuple] = {}
    elite_cohort = 0
    elite_gw = None
    rival = _tables_present(wh, ("fact_manager_pick", "dim_manager", "fact_manager_season"))
    cohorts_present: list[dict[str, Any]] = []
    if "fact_manager_pick" in rival:
        sizes = q(
            wh,
            """
            SELECT cohort, gw, any_value(n_managers) AS n_managers
            FROM sem_elite_ownership(now()) WHERE season = ?
            GROUP BY cohort, gw ORDER BY gw DESC, cohort
            """,
            (season,),
        )
        if not sizes.empty:
            cohorts_present = [
                {"cohort": str(r["cohort"]), "gw": int(r["gw"]),
                 "n": _i(r["n_managers"])}
                for _, r in sizes.iterrows()
            ]
        picks = q(
            wh,
            """
            WITH c AS (
                SELECT * FROM sem_elite_ownership(now())
                WHERE season = ? AND cohort = ?
            )
            SELECT code, gw, n_managers, own_pct, eo_pct
            FROM c
            WHERE gw = (SELECT max(gw) FROM c) AND code IS NOT NULL
            """,
            (season, cohort),
        )
        if not picks.empty:
            elite_cohort = int(picks.iloc[0]["n_managers"])
            elite_gw = int(picks.iloc[0]["gw"])
            for _, r in picks.iterrows():
                elite[int(r["code"])] = (_f(r["own_pct"], 6), _f(r["eo_pct"], 6))

    # -- squad coverage --
    squad: set[int] | None = None
    squad_note: str | None = None
    if coverage:
        squad, squad_note = _squad_codes(wh, season)
    else:
        squad_note = "coverage disabled by caller"

    def row(r) -> dict[str, Any]:
        code = int(r["code"])
        pred = eo_pred.get(code)
        e = elite.get(code)
        x = xp.get(code)
        return {
            "code": code,
            "name": str(r["web_name"]) if r["web_name"] == r["web_name"] else str(code),
            "pos": POSITION_NAME.get(_i(r["position"]) or 0),
            "team": str(r["team"]) if r["team"] == r["team"] and r["team"] is not None else None,
            "price": _f(r["price"], 1),
            "own_pct": _f(r["selected_by_pct"], 1),
            "eo_pred_pct": _f(pred * 100.0, 1) if pred is not None else None,
            "elite_own_pct": _f(e[0], 1) if e else None,
            "elite_eo_pct": _f(e[1], 1) if e else None,
            "xpts": _f(x[0], 2) if x else None,
            "xpts_spread": _f(x[1], 2) if x else None,
            "n_sources": _i(x[2]) if x else None,
            "in_squad": (code in squad) if squad is not None else None,
        }

    all_rows = [row(r) for _, r in players.iterrows()]

    have_live_eo = bool(eo_pred)
    key_live = (lambda c: (c["eo_pred_pct"] is None, -(c["eo_pred_pct"] or 0),
                           -(c["own_pct"] or 0)))
    key_marginal = (lambda c: -(c["own_pct"] or 0))
    template = sorted(all_rows, key=key_live if have_live_eo else key_marginal)[:limit]

    diffs = sorted(
        (c for c in all_rows
         if c["xpts"] is not None and (c["own_pct"] or 0) <= diff_max_own),
        key=lambda c: (-(c["xpts"] or 0), c["own_pct"] or 0),
    )[:limit]

    # -- other-season EO: quarantined, stamped with its real season and gw --
    last_season = None
    stale = q(
        wh,
        """
        WITH latest AS (
            SELECT season, gw, metric, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY season, gw, metric, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f
                WHERE season <> ?
                  AND season = (SELECT max(season) FROM fact_external_ownership
                                WHERE season <> ?)
            ) WHERE rn = 1
        )
        SELECT l.season, l.gw, l.code,
               any_value(p.web_name) AS web_name, any_value(p.position) AS position,
               any_value(p.team) AS team,
               max(CASE WHEN l.metric = 'eo_top10k' THEN l.value END) AS eo_top10k,
               max(CASE WHEN l.metric = 'eo_elite'  THEN l.value END) AS eo_elite
        FROM latest l
        LEFT JOIN sem_players(now()) p ON p.season = l.season AND p.code = l.code
        GROUP BY l.season, l.gw, l.code
        ORDER BY coalesce(max(CASE WHEN l.metric = 'eo_top10k' THEN l.value END),
                          max(CASE WHEN l.metric = 'eo_elite'  THEN l.value END),
                          0) DESC
        LIMIT ?
        """,
        (season, season, limit),
    )
    if not stale.empty:
        s_season = str(stale.iloc[0]["season"])
        s_gw = int(stale.iloc[0]["gw"])
        last_season = {
            "season": s_season,
            "gw": s_gw,
            "rows": [{
                "code": int(r["code"]),
                "name": str(r["web_name"]) if r["web_name"] == r["web_name"]
                        and r["web_name"] is not None else str(int(r["code"])),
                "pos": POSITION_NAME.get(_i(r["position"]) or 0),
                "team": str(r["team"]) if r["team"] == r["team"]
                        and r["team"] is not None else None,
                "eo_top10k_pct": _pct(r["eo_top10k"]),
                "eo_elite_pct": _pct(r["eo_elite"]),
            } for _, r in stale.iterrows()],
        }

    # -- honest notes, built from what was actually found --
    live_bits, stale_bits = [], []
    for c in gws_covered:
        label = f"{c['metric']} ({c['provider']}, {c['season']} GW{c['gw']}, {c['players']} players)"
        (live_bits if c["live"] else stale_bits).append(label)
    parts = []
    if live_bits:
        parts.append("Live EO: " + "; ".join(live_bits)
                     + ". Values are cohort fractions × captaincy — over 100% is normal.")
    else:
        parts.append(f"No external EO feed covers {season}; the template is "
                     f"ranked by FPL marginal ownership instead.")
    if stale_bits:
        parts.append("Stale (other season, shown only as \"last season's final "
                     "template\", never merged into current): " + "; ".join(stale_bits) + ".")
    parts.append("own_pct is FPL's marginal selected-by % — no captaincy weighting.")
    metrics_note = " ".join(parts)

    others = "; ".join(
        f"{c['cohort']} n={c['n']} (GW{c['gw']})"
        for c in cohorts_present if c["cohort"] != cohort
    )
    if elite_cohort:
        cohort_note = (
            f"{cohort} own%/EO% observed from {elite_cohort} crawled managers' "
            f"locked GW{elite_gw} squads (fact_manager_pick, via "
            f"sem_elite_ownership). EO is the mean FPL multiplier those "
            f"{elite_cohort} managers applied — 0 benched, 1 started, 2 "
            f"captain, 3 triple captain — so it is not ownership and can "
            f"exceed 100%. Cohorts are mutually exclusive: an entry sampled by "
            f"both crawls counts as top1k only, never in both denominators."
            + (f" Also on file: {others}." if others else "")
        )
    else:
        n_mgr = n_seasons = 0
        if "dim_manager" in rival:
            d = q(wh, "SELECT count(DISTINCT entry_id) AS n FROM dim_manager")
            n_mgr = int(d.iloc[0]["n"]) if not d.empty else 0
        if "fact_manager_season" in rival:
            d = q(wh, "SELECT count(*) AS n FROM fact_manager_season")
            n_seasons = int(d.iloc[0]["n"]) if not d.empty else 0
        cohort_note = (
            f"No {cohort} picks stored for {season} yet"
            + (f" ({n_mgr} crawled managers with {n_seasons} past-season records "
               f"on file, but fact_manager_pick is empty" if n_mgr else "")
            + ("); " if n_mgr else "; ")
            + f"{cohort} own%/EO% columns stay blank until the picks crawl runs."
            + (f" Other cohorts on file: {others}." if others else "")
        )

    return {
        "season": season,
        "rows": template,
        "differentials": diffs,
        "last_season": last_season,
        "metrics_note": metrics_note,
        "cohort_note": cohort_note,
        "cohort": cohort,
        "cohort_n": elite_cohort or None,
        "cohort_gw": elite_gw,
        "gws_covered": gws_covered,
        "xpts_gw": gw,
        "squad_note": squad_note,
        "as_of": latest_as_of(wh, "fact_player_state", season),
    }


register_script(
    name="ownership_eo",
    fn=ownership_eo,
    params_schema=PARAMS_SCHEMA,
    result_schema=RESULT_SCHEMA,
    title="Template & effective ownership",
    description="What the field owns: marginal ownership beside every "
                "external effective-ownership metric, template and "
                "differential views.",
)
