"""``_gw_mode``: provider projections for one gameweek, through the views.

Seven functions: the inputs (gameweek, sources, consensus flag, coverage), the
frame, the aggregates, the rows, the meta, the annotations, and the assembler
that returns the payload. The four blocks the annotations build
(``_latest_scores_sql``, ``_weights_block``, ``_annotate_applied_weights``,
``_provider_accuracy_block``) and the per-player detail sit here too: nothing
outside this mode calls them."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from fpl_edge.eval.projection_scoring import N_OBS_FLOOR
from fpl_edge.platform.scripts.common import POSITION_NAME, UTC, empty, next_gw, q
from fpl_edge.platform.scripts.projections.schema import DETAIL_HORIZON, _num, _rnd

# ---------------------------------------------------------------------------
# gameweek mode: provider projections through the semantic layer
# ---------------------------------------------------------------------------

def _gw_inputs(
    wh,
    *,
    season: str,
    gw: int | str | None,
    source: str | None,
    subset: list[str] | None,
    weighting: str,
) -> dict[str, Any] | tuple[
    dt.datetime, list[str], bool, list[str] | None, str | None, int,
    Any, list[str], bool, list[int], Callable[[], str], list[dict[str, Any]]]:
    """Resolve the gameweek, the sources, the consensus flag and the coverage.

    Returns the ``empty()`` payload as a bare dict when it has nothing to
    work with; the assembler returns that dict unchanged."""
    now = dt.datetime.now(UTC)
    notes: list[str] = []
    if weighting not in ("equal", "earned"):
        raise ValueError(f"weighting must be 'equal' or 'earned', got {weighting!r}")
    earned = weighting == "earned"
    # A one-name subset is just `source`; 2+ names is a subset consensus.
    if subset:
        subset = [str(x) for x in subset]
        if len(subset) == 1 and source is None:
            source = subset[0]
            subset = None

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
    except Exception as e:  # noqa: BLE001 - narrow re-raise below
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
    if subset:
        unknown = [x for x in subset if x not in sources]
        if unknown:
            return empty(
                f"Unknown source(s) {', '.join(unknown)} at GW{gw}. "
                f"Sources: {', '.join(sources)}."
            )
        notes.append(f"Consensus restricted to: {', '.join(sorted(subset))}.")

    return (now, notes, earned, subset, source, gw, sources_df, sources,
            consensus, covered, coverage_text, cov_rows)


def _gw_frame(
    wh,
    *,
    season: str,
    gw: int,
    source: str | None,
    subset: list[str] | None,
    consensus: bool,
    coverage_text: Callable[[], str],
    earned: bool,
    notes: list[str],
    now: dt.datetime,
) -> dict[str, Any] | Any:
    """One row per player for the chosen gameweek, consensus or single source.

    Returns the ``empty()`` payload as a bare dict when it has nothing to
    work with; the assembler returns that dict unchanged."""
    if consensus and subset and earned:
        # The subset blend with earned weights: the SAME arithmetic as
        # sem_projection_consensus_weighted (SUM(x*w)/SUM(w) over w > 0,
        # renormalised over the providers present), restricted to the subset.
        ph = ", ".join("?" for _ in subset)
        frame = q(
            wh,
            f"""
            WITH w AS (
                SELECT provider, weight FROM sem_projection_weights(?)
            ), pr AS (
                SELECT p.*, COALESCE(w.weight, 0) AS weight
                FROM sem_projections(?) p
                LEFT JOIN w ON w.provider = p.source
                WHERE p.season = ? AND p.gw = ? AND p.xpts IS NOT NULL
                  AND p.source IN ({ph})
            ), c AS (
                SELECT code, any_value(web_name) web_name,
                       any_value(position) "position",
                       any_value(team) team, any_value(price) price,
                       COUNT(DISTINCT source) n_sources,
                       CASE WHEN SUM(weight) > 0
                            THEN SUM(xpts * weight) / SUM(weight) END xpts,
                       MIN(xpts) xpts_min, MAX(xpts) xpts_max,
                       MAX(xpts) - MIN(xpts) spread, stddev_samp(xpts) sd,
                       AVG(xmins) xmins, AVG(p_appear) p_appear,
                       AVG(xp_if_appears) xp_if_appears,
                       COUNT(DISTINCT CASE WHEN weight > 0 THEN source END)
                           n_weighted_sources
                FROM pr GROUP BY code
            ), pl AS (
                SELECT code, selected_by_pct, status, team_code
                FROM sem_players(?) WHERE season = ?
            )
            SELECT c.*, pl.selected_by_pct AS own_pct, pl.status, pl.team_code
            FROM c LEFT JOIN pl USING (code)
            """,
            (now, now, season, gw, *subset, now, season),
        )
    elif consensus and subset:
        ph = ", ".join("?" for _ in subset)
        frame = q(
            wh,
            f"""
            WITH pr AS (
                SELECT * FROM sem_projections(?)
                WHERE season = ? AND gw = ? AND xpts IS NOT NULL
                  AND source IN ({ph})
            ), c AS (
                SELECT code, any_value(web_name) web_name,
                       any_value(position) "position",
                       any_value(team) team, any_value(price) price,
                       COUNT(DISTINCT source) n_sources,
                       AVG(xpts) xpts, MIN(xpts) xpts_min, MAX(xpts) xpts_max,
                       MAX(xpts) - MIN(xpts) spread, stddev_samp(xpts) sd,
                       AVG(xmins) xmins, AVG(p_appear) p_appear,
                       AVG(xp_if_appears) xp_if_appears
                FROM pr GROUP BY code
            ), pl AS (
                SELECT code, selected_by_pct, status, team_code
                FROM sem_players(?) WHERE season = ?
            )
            SELECT c.*, pl.selected_by_pct AS own_pct, pl.status, pl.team_code
            FROM c LEFT JOIN pl USING (code)
            """,
            (now, season, gw, *subset, now, season),
        )
    elif consensus and earned:
        frame = q(
            wh,
            """
            WITH c AS (
                SELECT * FROM sem_projection_consensus_weighted(?)
                WHERE season = ? AND gw = ?
            ), ap AS (
                SELECT code, AVG(p_appear) AS p_appear,
                       AVG(xp_if_appears) AS xp_if_appears
                FROM sem_projections(?)
                WHERE season = ? AND gw = ? AND xpts IS NOT NULL
                GROUP BY code
            ), pl AS (
                SELECT code, selected_by_pct, status, team_code
                FROM sem_players(?) WHERE season = ?
            )
            SELECT c.code, c.web_name, c.position, c.team, c.price,
                   pl.selected_by_pct AS own_pct, pl.status, pl.team_code,
                   c.n_sources, c.xpts_mean AS xpts, c.xpts_min, c.xpts_max,
                   c.xpts_spread AS spread, c.xpts_sd AS sd,
                   c.xmins_mean AS xmins, ap.p_appear, ap.xp_if_appears,
                   c.n_weighted_sources
            FROM c
            LEFT JOIN ap USING (code)
            LEFT JOIN pl USING (code)
            """,
            (now, season, gw, now, season, gw, now, season),
        )
    elif consensus:
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
                SELECT code, selected_by_pct, status, team_code
                FROM sem_players(?) WHERE season = ?
            )
            SELECT c.code, c.web_name, c.position, c.team, c.price,
                   pl.selected_by_pct AS own_pct, pl.status, pl.team_code,
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
                SELECT code, selected_by_pct, status, team_code
                FROM sem_players(?) WHERE season = ?
            )
            SELECT pr.code, pr.web_name, pr.position, pr.team, pr.price,
                   pl.selected_by_pct AS own_pct, pl.status, pl.team_code,
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
    if consensus and earned:
        # A player no earned provider covers has NO weighted number. Saying so
        # beats quietly serving the equal-weight mean under an "earned" label.
        unweighted = frame["xpts"].isna()
        if unweighted.any():
            notes.append(
                f"{int(unweighted.sum())} player(s) at GW{gw} have no provider "
                f"with an earned weight and are omitted from the earned-weight "
                f"view (they are in the equal-weight view)."
            )
            frame = frame[~unweighted]
        if frame.empty:
            return empty(
                f"No provider with an earned weight covers GW{gw}; switch to "
                f"equal weights or wait for the calibration loop to fit."
            )
    if "n_weighted_sources" not in frame.columns:
        frame["n_weighted_sources"] = None

    frame["value"] = frame["xpts"] / frame["price"].clip(lower=0.1)

    # Aggregates over the FULL gameweek board, before player filters: the
    # strip answers "which teams/positions look best this GW", and a price
    # filter should not quietly reshape that answer.
    return frame


def _gw_aggregates(
    *,
    frame: Any,
    consensus: bool,
    gw: int,
    limit: int,
    max_price: float | None,
    min_p_appear: float | None,
    notes: list[str],
    position: int | None,
    sort: str,
    sources_df: Any,
    team: str | None,
) -> dict[str, Any] | tuple[Any, list[dict[str, Any]], list[dict[str, Any]]]:
    """The per-team and per-position averages, then the filters and the sort.

    Returns the ``empty()`` payload as a bare dict when it has nothing to
    work with; the assembler returns that dict unchanged."""
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

    return frame, by_team, by_position


def _gw_rows(
    wh,
    *,
    season: str,
    frame: Any,
    gw: int,
    detail_code: int | None,
    notes: list[str],
    now: dt.datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """The served rows, and the per-source breakdown for one player when asked."""
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
            "team_code": None if r.get("team_code") is None
                         or r["team_code"] != r["team_code"]
                         else int(r["team_code"]),
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
            "n_weighted_sources": (
                None if r.get("n_weighted_sources") is None
                or r["n_weighted_sources"] != r["n_weighted_sources"]
                else int(r["n_weighted_sources"])),
            "xmins": _rnd(r["xmins"], 1),
            "p_appear": _rnd(r["p_appear"]),
            "xp_if_appears": _rnd(r["xp_if_appears"]),
            "value": _rnd(r["value"]),
        })

    detail = None
    if detail_code is not None:
        detail = _player_detail(wh, now, season, gw, int(detail_code), notes)

    return rows, detail


def _gw_meta(
    wh,
    *,
    season: str,
    gw: int,
    consensus: bool,
    covered: list[int],
    earned: bool,
    now: dt.datetime,
    source: str | None,
    span: int,
    subset: list[str] | None,
) -> tuple[list[int], list[int], dict[str, Any], dict[str, Any], dict[str, Any], str | None]:
    """The gameweek axis, the settled actuals, the source matrix and the clocks."""
    fetched = q(
        wh,
        "SELECT max(fetched_at) AS f FROM sem_projections(?) "
        "WHERE season = ? AND gw = ?",
        (now, season, gw),
    )
    as_of = None
    if not fetched.empty and fetched.iloc[0]["f"] is not None:
        as_of = str(fetched.iloc[0]["f"])

    # Per-source freshness: what each feed covers and when it last fetched.
    # Data-driven so a newly registered provider (a paid FPL Review feed, say)
    # appears here, and therefore in the UI, with no further change.
    meta = q(
        wh,
        "SELECT source, MIN(gw) gw_min, MAX(gw) gw_max, "
        "MAX(fetched_at) last_fetched, COUNT(*) n_rows, "
        "COUNT(xmins) n_xmins, COUNT(p_appear) n_pappear "
        "FROM sem_projections(?) WHERE season = ? AND xpts IS NOT NULL "
        "GROUP BY source ORDER BY n_rows DESC",
        (now, season),
    )
    source_meta = [{
        "source": str(r["source"]),
        "gw_min": int(r["gw_min"]), "gw_max": int(r["gw_max"]),
        "last_fetched": str(r["last_fetched"]),
        "n_rows": int(r["n_rows"]),
        "has_xmins": bool(r["n_xmins"]),
        "has_p_appear": bool(r["n_pappear"]),
    } for _, r in meta.iterrows()]

    # The matrix window: per-player per-GW values for gw..gw+span-1, in the
    # SAME selection (consensus or one source) as the rows.
    gws = [g for g in range(int(gw), int(gw) + int(span)) if g in covered]
    matrix: dict[str, dict[str, float]] = {}
    if gws:
        if consensus and subset and earned:
            ph = ", ".join("?" for _ in subset)
            mrows = q(
                wh,
                f"""
                WITH w AS (
                    SELECT provider, weight FROM sem_projection_weights(?)
                ), pr AS (
                    SELECT p.code, p.gw, p.xpts, COALESCE(w.weight, 0) AS weight
                    FROM sem_projections(?) p
                    LEFT JOIN w ON w.provider = p.source
                    WHERE p.season = ? AND p.gw >= ? AND p.gw <= ?
                      AND p.xpts IS NOT NULL AND p.source IN ({ph})
                )
                SELECT code, gw,
                       CASE WHEN SUM(weight) > 0
                            THEN SUM(xpts * weight) / SUM(weight) END AS v
                FROM pr GROUP BY code, gw
                """,
                (now, now, season, gws[0], gws[-1], *subset),
            )
        elif consensus and subset:
            ph = ", ".join("?" for _ in subset)
            mrows = q(
                wh,
                f"SELECT code, gw, AVG(xpts) AS v FROM sem_projections(?) "
                f"WHERE season = ? AND gw >= ? AND gw <= ? "
                f"AND xpts IS NOT NULL AND source IN ({ph}) GROUP BY code, gw",
                (now, season, gws[0], gws[-1], *subset),
            )
        elif consensus and earned:
            mrows = q(
                wh,
                "SELECT code, gw, xpts_mean AS v "
                "FROM sem_projection_consensus_weighted(?) "
                "WHERE season = ? AND gw >= ? AND gw <= ?",
                (now, season, gws[0], gws[-1]),
            )
        elif consensus:
            mrows = q(
                wh,
                "SELECT code, gw, xpts_mean AS v FROM sem_projection_consensus(?) "
                "WHERE season = ? AND gw >= ? AND gw <= ?",
                (now, season, gws[0], gws[-1]),
            )
        else:
            mrows = q(
                wh,
                "SELECT code, gw, xpts AS v FROM sem_projections(?) "
                "WHERE season = ? AND source = ? AND gw >= ? AND gw <= ? "
                "AND xpts IS NOT NULL",
                (now, season, source, gws[0], gws[-1]),
            )
        for _, r in mrows.iterrows():
            v = r["v"]
            if v is None or v != v:
                continue
            matrix.setdefault(str(int(r["code"])), {})[str(int(r["gw"]))] = (
                round(float(v), 3))

    # Official actuals for any settled gameweek in the window: the matrix
    # shows projection vs reality side by side once a week completes.
    actuals: dict[str, dict[str, float]] = {}
    settled_gws: list[int] = []
    if gws:
        arows = q(
            wh,
            "SELECT gw, code, SUM(total_points) AS pts FROM sem_player_form(?) "
            "WHERE season = ? AND gw >= ? AND gw <= ? GROUP BY gw, code",
            (now, season, gws[0], gws[-1]),
        )
        for _, r in arows.iterrows():
            g = int(r["gw"])
            if g not in settled_gws:
                settled_gws.append(g)
            actuals.setdefault(str(int(r["code"])), {})[str(g)] = float(r["pts"])
        settled_gws.sort()

    return gws, settled_gws, actuals, matrix, source_meta, as_of


def _gw_annotations(
    wh,
    *,
    season: str,
    gw: int,
    consensus: bool,
    earned: bool,
    gws: list[int],
    notes: list[str],
    now: dt.datetime,
    source: str | None,
    source_meta: dict[str, Any],
    subset: list[str] | None,
) -> tuple[str | None, Any, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Prices, the weights actually applied, the weights block and the accuracy."""
    prices_df = q(
        wh, "SELECT MAX(as_of) AS a FROM fact_player_state WHERE season = ?",
        (season,))
    prices_as_of = (None if prices_df.empty or prices_df.iloc[0]["a"] is None
                    else str(prices_df.iloc[0]["a"]))

    # Measured accuracy: the calibration loop's earned weights beside the MAE
    # they were earned from. Empty until a gameweek settles; never invented.
    accuracy: list[dict[str, Any]] = []
    try:
        acc = q(
            wh,
            """
            WITH w AS (
                SELECT provider, weight, earned,
                       n_obs AS w_n_obs, track_record_gws
                FROM sem_projection_weights(?)
            ), m AS (
                SELECT provider, scope,
                       AVG(CASE WHEN metric='mae' THEN value END) mae,
                       AVG(CASE WHEN metric='mae' THEN baseline END) baseline_mae,
                       AVG(CASE WHEN metric='rmse' THEN value END) rmse,
                       AVG(CASE WHEN metric='rmse' THEN baseline END) baseline_rmse,
                       MAX(n_obs) n_obs
                FROM fact_projection_score
                WHERE season = ? AND scope IN ('overall','own_gt5','own_gt20')
                GROUP BY provider, scope
            )
            SELECT w.provider, m.scope, m.mae, m.baseline_mae, m.rmse,
                   m.baseline_rmse, COALESCE(m.n_obs, 0) n_obs, w.weight,
                   w.earned, w.track_record_gws
            FROM w LEFT JOIN m USING (provider)
            ORDER BY w.weight DESC, m.scope
            """,
            (now, season),
        )

        def _n(v, nd=3):
            return None if v is None or v != v else round(float(v), nd)

        for _, r in acc.iterrows():
            accuracy.append({
                "provider": str(r["provider"]),
                "scope": str(r["scope"]) if r["scope"] == r["scope"]
                         and r["scope"] is not None else "overall",
                "mae": _n(r["mae"]),
                "rmse": _n(r["rmse"]),
                "baseline_mae": _n(r["baseline_mae"]),
                "baseline_rmse": _n(r["baseline_rmse"]),
                "n_obs": int(r["n_obs"] or 0),
                "weight": round(float(r["weight"]), 3),
                "earned": bool(r["earned"]),
                "track_record_gws": None if r["track_record_gws"] is None
                                    or r["track_record_gws"] != r["track_record_gws"]
                                    else int(r["track_record_gws"]),
            })
    except Exception:  # noqa: BLE001 - no scores yet is a normal state
        pass

    applied = ("single_source" if not consensus
               else ("earned" if earned else "equal"))
    if not consensus and earned:
        notes.append(
            f"weighting='earned' does not apply to a single source: these are "
            f"{source}'s raw numbers, unblended."
        )
    weights_block = _weights_block(wh, now, season)
    _annotate_applied_weights(
        wh, now, season, weights_block,
        gws=gws, anchor_gw=gw, source_meta=source_meta,
        selection=(set(subset) if subset
                   else (None if consensus else {str(source)})),
    )
    provider_accuracy = _provider_accuracy_block(wh, season)

    return prices_as_of, applied, weights_block, provider_accuracy, accuracy


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
    span: int = 5,
    subset: list[str] | None = None,
    weighting: str = "equal",
) -> dict[str, Any]:
    inputs = _gw_inputs(wh, season=season, gw=gw, source=source, subset=subset,
                        weighting=weighting)
    if isinstance(inputs, dict):
        return inputs
    (now, notes, earned, subset, source, gw, sources_df, sources, consensus,
     covered, coverage_text, cov_rows) = inputs

    frame = _gw_frame(wh, season=season, gw=gw, source=source, subset=subset,
                      consensus=consensus, coverage_text=coverage_text,
                      earned=earned, notes=notes, now=now)
    if isinstance(frame, dict):
        return frame

    aggregates = _gw_aggregates(
        frame=frame, consensus=consensus, gw=gw, limit=limit,
        max_price=max_price, min_p_appear=min_p_appear, notes=notes,
        position=position, sort=sort, sources_df=sources_df, team=team)
    if isinstance(aggregates, dict):
        return aggregates
    frame, by_team, by_position = aggregates

    rows, detail = _gw_rows(wh, season=season, frame=frame, gw=gw,
                            detail_code=detail_code, notes=notes, now=now)
    gws, settled_gws, actuals, matrix, source_meta, as_of = _gw_meta(
        wh, season=season, gw=gw, consensus=consensus, covered=covered,
        earned=earned, now=now, source=source, span=span, subset=subset)
    (prices_as_of, applied, weights_block, provider_accuracy,
     accuracy) = _gw_annotations(
        wh, season=season, gw=gw, consensus=consensus, earned=earned, gws=gws,
        notes=notes, now=now, source=source, source_meta=source_meta,
        subset=subset)

    return {
        "mode": "consensus" if consensus else "source",
        "season": season,
        "gw": gw,
        "source": None if consensus else source,
        "weighting": applied,
        "weighting_requested": weighting,
        "blocks_weighting": {
            "rows": applied, "matrix": applied,
            "by_team": applied, "by_position": applied,
            "detail": "raw",
        },
        "weights": weights_block,
        "provider_accuracy": provider_accuracy,
        "active_sources": (sorted(subset) if subset
                           else ([source] if not consensus else sources)),
        "sort": sort,
        "row_count": len(rows),
        "rows": rows,
        "gw_coverage": cov_rows,
        "sources": sources,
        "by_team": by_team,
        "by_position": by_position,
        "detail": detail,
        "as_of": as_of,
        "source_meta": source_meta,
        "prices_as_of": prices_as_of,
        "accuracy": accuracy,
        "actuals": actuals,
        "settled_gws": settled_gws,
        "gws": gws,
        "matrix": matrix,
        "notes": notes,
    }


def _latest_scores_sql(scope: str = "overall") -> str:
    """fact_projection_score, latest scoring per (provider, gw, metric).

    ``as_of`` is the scoring instant and a re-score appends rather than
    overwrites, so a cell can have two rows; the newest is the one to read."""
    return f"""
        SELECT * EXCLUDE (rn) FROM (
            SELECT *, row_number() OVER (
                PARTITION BY provider, season, gw, scope, metric
                ORDER BY as_of DESC) rn
            FROM fact_projection_score
            WHERE season = ? AND scope = '{scope}'
        ) WHERE rn = 1
    """


def _weights_block(wh, now: dt.datetime, season: str) -> dict[str, Any] | None:
    """The latest fit's weights beside the evidence that earned them: n_obs,
    the provider's mean per-GW MAE and the equal-weight consensus's MAE on
    the same players. None when no fit exists: an absent table, not an
    invented one."""
    try:
        df = q(
            wh,
            f"""
            WITH w AS (
                SELECT * FROM sem_projection_weights(?)
            ), m AS (
                SELECT provider,
                       AVG(CASE WHEN metric = 'mae' THEN value END) mae,
                       AVG(CASE WHEN metric = 'mae' THEN baseline END) baseline_mae
                FROM ({_latest_scores_sql("overall")}) GROUP BY provider
            )
            SELECT w.provider, w.weight, w.n_obs, w.loss, w.baseline_loss,
                   w.earned, w.holdout, w.fit_id, w.fitted_at,
                   m.mae, m.baseline_mae
            FROM w LEFT JOIN m USING (provider)
            ORDER BY w.weight DESC, w.provider
            """,
            (now, season),
        )
        gws_df = q(
            wh,
            f"SELECT DISTINCT gw FROM ({_latest_scores_sql('overall')}) ORDER BY gw",
            (season,),
        )
    except Exception:  # noqa: BLE001 - tables absent = no fit yet
        return None
    if df.empty:
        return None
    first = df.iloc[0]
    return {
        "as_of": str(first["fitted_at"]),
        "fit_id": str(first["fit_id"]),
        "scored_gws": [int(g) for g in gws_df["gw"]],
        "n_floor": int(N_OBS_FLOOR),
        "rows": [{
            "provider": str(r["provider"]),
            "weight": round(float(r["weight"]), 3),
            "n_obs": int(r["n_obs"] or 0),
            "mae": _rnd(r["mae"]),
            "baseline_mae": _rnd(r["baseline_mae"]),
            "loss": _rnd(r["loss"]),
            "baseline_loss": _rnd(r["baseline_loss"]),
            "earned": bool(r["earned"]),
            "holdout": None if r["holdout"] is None or r["holdout"] != r["holdout"]
                       else str(r["holdout"]),
        } for _, r in df.iterrows()],
    }


def _annotate_applied_weights(
    wh,
    now: dt.datetime,
    season: str,
    weights_block: dict[str, Any] | None,
    *,
    gws: list[int],
    anchor_gw: int,
    selection: set[str] | None,
    source_meta: list[dict[str, Any]],
) -> None:
    """Write the weights the visible columns actually use into the block.

    The blend is SUM(x*w)/SUM(w) over the providers present in each gameweek,
    so a fitted weight is not the applied one. A provider whose coverage stops
    at GW4 drops out of GW5 and every other provider's share rises. This fills
    ``applied_by_gw`` for the matrix window and ``applied_weight`` on each row
    for the anchor gameweek, so a reader is never shown a fitted number as if
    it were the one in the column.

    ``selection`` is the provider subset the caller asked for, or None for
    every provider.
    """
    if weights_block is None:
        return
    fitted = {r["provider"]: float(r["weight"]) for r in weights_block["rows"]}
    publishes = {m["source"] for m in source_meta}
    present: dict[int, set[str]] = {}
    if gws:
        try:
            df = q(
                wh,
                "SELECT gw, source FROM sem_projections(?) "
                "WHERE season = ? AND gw >= ? AND gw <= ? AND xpts IS NOT NULL "
                "GROUP BY gw, source",
                (now, season, gws[0], gws[-1]),
            )
        except Exception:  # noqa: BLE001 - no projection rows is a normal state
            df = None
        if df is not None:
            for _, r in df.iterrows():
                present.setdefault(int(r["gw"]), set()).add(str(r["source"]))

    def applied_at(g: int) -> dict[str, float]:
        here = present.get(g, set())
        if selection is not None:
            here = here & selection
        total = sum(fitted.get(s, 0.0) for s in here)
        if total <= 0:
            return {}
        return {s: round(fitted.get(s, 0.0) / total, 4)
                for s in sorted(here) if fitted.get(s, 0.0) > 0}

    anchor = applied_at(int(anchor_gw))
    anchor_present = present.get(int(anchor_gw), set())
    if selection is not None:
        anchor_present = anchor_present & selection
    weights_block["anchor_gw"] = int(anchor_gw)
    weights_block["applied_by_gw"] = {str(g): applied_at(g) for g in gws}
    for r in weights_block["rows"]:
        provider = r["provider"]
        r["applied_weight"] = anchor.get(provider, 0.0)
        r["covers_anchor"] = provider in anchor_present
        r["publishes_xpts"] = provider in publishes


def _provider_accuracy_block(wh, season: str) -> dict[str, Any]:
    """Per provider per settled GW: overall MAE vs the equal-weight baseline.
    ``meets_floor`` travels with every cell so the UI ranks nothing that sits
    under the house n_obs floor."""
    rows: list[dict[str, Any]] = []
    gws: list[int] = []
    try:
        df = q(
            wh,
            f"""
            SELECT provider, gw,
                   MAX(CASE WHEN metric = 'mae' THEN value END) mae,
                   MAX(CASE WHEN metric = 'mae' THEN baseline END) baseline_mae,
                   MAX(CASE WHEN metric = 'rmse' THEN value END) rmse,
                   MAX(CASE WHEN metric = 'rmse' THEN baseline END) baseline_rmse,
                   MAX(n_obs) n_obs
            FROM ({_latest_scores_sql("overall")})
            GROUP BY provider, gw ORDER BY gw, provider
            """,
            (season,),
        )
    except Exception:  # noqa: BLE001 - no scores yet is a normal state
        df = None
    if df is not None:
        for _, r in df.iterrows():
            if r["mae"] is None or r["mae"] != r["mae"]:
                continue
            g = int(r["gw"])
            if g not in gws:
                gws.append(g)
            n = int(r["n_obs"] or 0)
            rows.append({
                "provider": str(r["provider"]),
                "gw": g,
                "mae": round(float(r["mae"]), 3),
                "baseline_mae": _rnd(r["baseline_mae"]),
                "rmse": _rnd(r["rmse"]),
                "baseline_rmse": _rnd(r["baseline_rmse"]),
                "n_obs": n,
                "meets_floor": n >= N_OBS_FLOOR,
            })
    return {"scope": "overall", "scored_gws": sorted(gws),
            "n_floor": int(N_OBS_FLOOR), "rows": rows}


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
