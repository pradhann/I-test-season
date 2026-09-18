"""What the warehouse holds: EO, the cohort, the sub-cohorts and the squad.

Every function here reads and returns; none of them shapes a payload key."""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.scripts.common import POSITION_NAME, _squad_state, next_gw, q
from fpl_edge.platform.scripts.ownership.fields import (
    _cohort_composition,
    _segment_inventory,
    _segment_ownership,
)
from fpl_edge.platform.scripts.ownership.schema import (
    _SEGMENT_META,
    DEFAULT_SEGMENTS,
    _f,
    _i,
    _pct,
    _tables_present,
)


def _eo_inventory(wh, season: str) -> list[dict[str, Any]]:
    """What EO data exists, enumerated per metric and gameweek rather than assumed."""
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

    return gws_covered


def _eo_pivot(wh, season: str) -> tuple[
    dict[str, dict[int, float]], dict[str, int], dict[int, float]]:
    """The live external EO per metric, its gameweek, and LiveFPL's prediction."""
    # -- live EO pivot: requested season only, each metric at its latest gw --
    # Kept long rather than pivoted to three named columns: the metric list is
    # whatever the feed happens to write, and a hard-coded pivot is exactly how
    # eo_top10k/eo_elite went live for this season without the panel noticing.
    # Each metric is pinned to ITS OWN latest gw and nothing older is carried
    # forward: a field is "this metric at gw G", so a code the feed dropped
    # goes null rather than silently keeping a value from a different week.
    eo = q(
        wh,
        """
        WITH r AS (
            SELECT metric, gw, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY metric, gw, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f WHERE season = ?
            ) WHERE rn = 1
        ), mx AS (SELECT metric, max(gw) AS g FROM r GROUP BY 1)
        SELECT r.metric, r.gw, r.code, r.value
        FROM r JOIN mx ON mx.metric = r.metric AND r.gw = mx.g
        """,
        (season,),
    )
    external: dict[str, dict[int, float]] = {}
    external_gw: dict[str, int] = {}
    if not eo.empty:
        for _, r in eo.iterrows():
            v = r["value"]
            if v is None or v != v:
                continue
            metric = str(r["metric"])
            external.setdefault(metric, {})[int(r["code"])] = float(v)
            external_gw[metric] = int(r["gw"])
    eo_pred = external.get("eo_predicted", {})

    return external, external_gw, eo_pred


def _consensus_xpts(wh, season: str) -> tuple[int | None, dict[int, tuple]]:
    """Consensus xPts for the next deadline, or the earliest gameweek on file."""
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

    return gw, xp


def _cohort_own_eo(wh, season: str, cohort: str) -> tuple[
    dict[int, tuple], int, int | None, dict[str, dict[int, dict]],
    dict[str, dict[str, Any]], list[dict[str, Any]],
    dict[tuple[str, int], list[dict]], bool]:
    """The crawled cohort's own EO, its size, its gameweek and its composition."""
    # -- the crawled cohort: own%/EO% from the ONE definition ----------------
    # sem_elite_ownership is the canonical effective ownership (mean FPL
    # multiplier over the cohort's managers with a stored squad). This script
    # does not compute its own: the previous local query had no cohort filter
    # at all and reported a blended top1k+elite denominator as "elite".
    # EVERY cohort is read, not just the requested one: the UI compares fields
    # against each other, and a refetch per cohort would let the two halves of
    # a comparison drift to different as_of instants.
    elite: dict[int, tuple] = {}
    elite_cohort = 0
    elite_gw = None
    rival = _tables_present(wh, ("fact_manager_pick", "dim_manager", "fact_manager_season"))
    cohorts_present: list[dict[str, Any]] = []
    cohort_rows: dict[str, dict[int, dict]] = {}
    cohort_meta: dict[str, dict[str, Any]] = {}
    composition: dict[tuple[str, int], list[dict]] = {}
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
                SELECT * FROM sem_elite_ownership(now()) WHERE season = ?
            ), mx AS (SELECT cohort, max(gw) AS g FROM c GROUP BY 1)
            SELECT c.cohort, c.code, c.gw, c.n_managers, c.own_pct, c.eo_pct,
                   c.captain_pct, c.owned_by, c.started_by, c.benched_by,
                   c.captained_by
            FROM c JOIN mx ON mx.cohort = c.cohort AND c.gw = mx.g
            WHERE c.code IS NOT NULL
            """,
            (season,),
        )
        if not picks.empty:
            for _, r in picks.iterrows():
                co = str(r["cohort"])
                cohort_rows.setdefault(co, {})[int(r["code"])] = {
                    "own": _f(r["own_pct"], 1),
                    "eo": _f(r["eo_pct"], 1),
                    "cap": _f(r["captain_pct"], 1),
                    "owned_by": _i(r["owned_by"]),
                    "started_by": _i(r["started_by"]),
                    "benched_by": _i(r["benched_by"]),
                    "captained_by": _i(r["captained_by"]),
                    # the denominator, ON the measurement
                    "n": _i(r["n_managers"]),
                }
                cohort_meta[co] = {"n": _i(r["n_managers"]), "gw": int(r["gw"])}
                if co == cohort:
                    # The legacy elite_* keys keep their original 6dp-then-1dp
                    # rounding path, from this same read — one query, not two.
                    elite[int(r["code"])] = (_f(r["own_pct"], 6),
                                             _f(r["eo_pct"], 6))
            composition = _cohort_composition(wh, season)
        if cohort in cohort_meta:
            elite_cohort = cohort_meta[cohort]["n"] or 0
            elite_gw = cohort_meta[cohort]["gw"]

    return (elite, elite_cohort, elite_gw, cohort_rows, cohort_meta,
            cohorts_present, composition, rival)


def _sub_cohorts(
    wh, season: str, segments: list[str] | None, rival: bool
) -> tuple[
    list[dict[str, Any]], list[str], list[str], set[str], list[str],
    dict[int, dict], dict[int, dict], int | None, int | None,
    dict[str, Any] | None, int | None]:
    """Which crawl sets compose the field, resolved against what was crawled."""
    # -- the selectable sub-cohorts: which sets compose the field ------------
    # `cohort:elite` is ONE aggregate of managers found by six different
    # crawls. Which of those crawls a reader is willing to be measured against
    # is a judgement only the reader can make — an elite list and a pool of
    # league-mates-of-stale-seeds are not the same evidence — so the sets are
    # served separately and the field is the UNION of the chosen ones.
    requested = [str(s) for s in (segments if segments is not None
                                  else DEFAULT_SEGMENTS)]
    pick_gw: int | None = None
    seg_inventory: list[dict[str, Any]] = []
    if "fact_manager_pick" in rival:
        g = q(wh, "SELECT max(gw) AS g FROM fact_manager_pick WHERE season = ?",
              (season,))
        pick_gw = _i(g.iloc[0]["g"]) if not g.empty else None
        seg_inventory = _segment_inventory(wh, season, pick_gw)
    # A requested name splits three ways, and the distinction matters. A name
    # the crawl has never written AND that this panel has never heard of is a
    # typo (`unknown`) and is reported back. A name this panel knows but that
    # no manager currently carries is NOT a typo -- it is a real set that is
    # empty in this warehouse today (the expert pool has 20 managers and 0
    # stored squads) -- so it stays in the selection and contributes nobody,
    # and its descriptor carries the zero rather than vanishing from the UI.
    known = {s["segment"] for s in seg_inventory}
    unknown = [s for s in requested
               if s not in known and s not in _SEGMENT_META]
    resolved = [s for s in requested if s not in unknown]
    # No crawled squad for this season means no field to measure, whatever was
    # selected — skip the scan rather than divide by an absence.
    sel_by_gw = (_segment_ownership(wh, season, resolved)
                 if resolved and pick_gw is not None else {})
    sel_gw = max(sel_by_gw) if sel_by_gw else None
    sel_slot = sel_by_gw.get(sel_gw) if sel_gw is not None else None
    sel_n = sel_slot["n_managers"] if sel_slot else None
    sel_by_code: dict[int, dict] = sel_slot["by_code"] if sel_slot else {}

    return (seg_inventory, requested, resolved, known, unknown, sel_by_code,
            sel_by_gw, sel_gw, sel_n, sel_slot, pick_gw)


def _squad_coverage(
    wh,
    *,
    season: str,
    players: Any,
    limit: int,
    diff_max_own: float,
    coverage: bool,
    elite: dict[int, tuple],
    eo_pred: dict[int, float],
    external: dict[str, dict[int, float]],
    cohort_rows: dict[str, dict[int, dict]],
    sel_by_code: dict[int, dict],
    sel_n: int | None,
    xp: dict[int, tuple],
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str,
    set | None, dict[str, Any], str | None]:
    """One row per player with every field's measures, then the template and the differentials."""
    # -- squad coverage: codes AND the multiplier behind each one --
    roles: dict[int, dict] | None = None
    if coverage:
        roles, squad_meta = _squad_state(wh, season)
    else:
        squad_meta = {"readable": False, "has_multipliers": False,
                      "note": "coverage disabled by caller"}
    squad_note = squad_meta.get("note")
    squad = set(roles) if roles is not None else None

    def row(r) -> dict[str, Any]:
        code = int(r["code"])
        pred = eo_pred.get(code)
        e = elite.get(code)
        x = xp.get(code)
        mine = roles.get(code) if roles is not None else None
        f: dict[str, dict] = {}
        own = _f(r["selected_by_pct"], 1)
        if own is not None:
            f["global"] = {"own": own}
        for metric, values in external.items():
            v = values.get(code)
            if v is not None:
                f[metric] = {"eo": _f(v * 100.0, 1)}
        for co, by_code in cohort_rows.items():
            m = by_code.get(code)
            if m is not None:
                f[f"cohort:{co}"] = m
        # The union of the selected segments, as one more rung of the same
        # ladder — so a UI comparing "selected" against "cohort:top1k" against
        # "eo_predicted" reads all three the same way.
        sel_m = sel_by_code.get(code)
        if sel_m is not None:
            f["selected"] = {**sel_m, "n": sel_n}
        return {
            "code": code,
            "name": str(r["web_name"]) if r["web_name"] == r["web_name"] else str(code),
            "pos": POSITION_NAME.get(_i(r["position"]) or 0),
            "team": str(r["team"]) if r["team"] == r["team"] and r["team"] is not None else None,
            "team_code": _i(r["team_code"]),
            "status": str(r["status"]) if r["status"] == r["status"] and r["status"] is not None else None,
            "price": _f(r["price"], 1),
            "own_pct": own,
            "eo_pred_pct": _f(pred * 100.0, 1) if pred is not None else None,
            "elite_own_pct": _f(e[0], 1) if e else None,
            "elite_eo_pct": _f(e[1], 1) if e else None,
            "xpts": _f(x[0], 2) if x else None,
            "xpts_spread": _f(x[1], 2) if x else None,
            "n_sources": _i(x[2]) if x else None,
            "in_squad": (code in squad) if squad is not None else None,
            "your_mult": mine["mult"] if mine else None,
            "your_role": mine["role"] if mine else None,
            "fields": f,
        }

    all_rows = [row(r) for _, r in players.iterrows()]

    if squad_meta.get("readable"):
        cap_code = squad_meta.pop("_captain_code", None)
        squad_meta["captain"] = next(
            (c["name"] for c in all_rows if c["code"] == cap_code), None)
    squad_meta.pop("_captain_code", None)

    have_live_eo = bool(eo_pred)
    key_live = (lambda c: (c["eo_pred_pct"] is None, -(c["eo_pred_pct"] or 0),
                           -(c["own_pct"] or 0)))
    key_marginal = (lambda c: -(c["own_pct"] or 0))
    template = sorted(all_rows, key=key_live if have_live_eo else key_marginal)[:limit]
    # What `rows` is ranked by, named on the payload so `diff[].in_panel_rows`
    # can be checked against the list it refers to. It is an EXTERNAL ranking,
    # not the crawled field, and not the page's Template XV.
    rows_ranked_by = (
        "eo_pred_pct, LiveFPL predicted effective ownership for the whole game"
        if have_live_eo else
        "own_pct, FPL marginal selected-by percent"
    )

    diffs = sorted(
        (c for c in all_rows
         if c["xpts"] is not None and (c["own_pct"] or 0) <= diff_max_own),
        key=lambda c: (-(c["xpts"] or 0), c["own_pct"] or 0),
    )[:limit]

    return (all_rows, template, diffs, rows_ranked_by, squad, squad_meta,
            squad_note)


def _other_season_eo(wh, season: str, limit: int) -> list[dict[str, Any]] | None:
    """EO recorded under a different season, quarantined rather than merged."""
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

    return last_season
