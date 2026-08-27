"""
MCP tools over the fpl-edge semantic layer.

The engine's warehouse carries six point-in-time table macros stored in the
database file itself (defined in ``fpl_edge/store/views.sql``, documented in
``docs/platform/semantic_layer.md``): ``sem_players``, ``sem_projections``,
``sem_projection_consensus``, ``sem_player_form``, ``sem_ownership`` and
``sem_fixtures``. Each takes one ``TIMESTAMPTZ`` parameter and answers with
what was knowable at that instant. Every tool here is a thin, few-line query
over exactly one of those macros (fixture difficulty additionally joins the
cached ratings parquet that sits next to the database).

All reads go through ``Warehouse.read_copy()``: the file is copied and the
copy is read, so these tools never contend with the single DuckDB writer
(ingest jobs, the Telegram bot) and never block it. The copy carries the
macros because they live in the file.

Every tool takes an optional ``as_of`` ISO-8601 UTC instant (default: now)
and names the macro and the as-of instant in its output, so an answer is
always reproducible. User-supplied values are bound as SQL parameters, never
interpolated.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Optional

import pandas as pd

from fpl_mcp.server import mcp  # type: ignore

# The engine-locating machinery (FPL_EDGE_HOME / FPL_EDGE_DB, graceful
# degradation when the checkout is missing) lives in edge_tools; reuse it
# rather than duplicating the path logic.
from fpl_mcp.tools import edge_tools as _edge  # type: ignore

DEFAULT_SEASON = "2026-27"
POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
POS_INV = {v: k for k, v in POS.items()}

# Far-future probe instant: used only to explain empty PIT results by showing
# what the feed holds in total ("data exists but was fetched after your as_of"
# versus "no data at all").
_EVER = dt.datetime(9999, 1, 1, tzinfo=dt.timezone.utc)


# -----------------------------------------------------------------------------
# Plumbing


def _read():
    """Open a read-only private copy of the warehouse (never blocks writers)."""
    from fpl_edge.store import Warehouse  # deferred: guarded by _unavailable()

    return Warehouse.read_copy(_edge._db_path())


def _header(macro: str, t: dt.datetime) -> str:
    return f"[{macro} · as-of {t:%Y-%m-%d %H:%M}Z]"


def _table(df: pd.DataFrame) -> str:
    """Render a DataFrame as a compact markdown table."""
    if df.empty:
        return "(no rows)"
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            df[col] = s.dt.strftime("%m-%d %H:%MZ")
        elif pd.api.types.is_float_dtype(s):
            df[col] = s.map(lambda v: "–" if pd.isna(v) else f"{v:.2f}")
        else:
            df[col] = s.map(lambda v: "–" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(row[c] for c in df.columns) + " |")
    return "\n".join(lines)


def _pos_label(v: Any) -> str:
    try:
        return POS.get(int(v), str(v))
    except (TypeError, ValueError):
        return str(v)


def _resolve(wh, t: dt.datetime, player: str, season: str):
    """Resolve a player name to a code via sem_players. Never guesses.

    Returns ``(code, label, error)``: exactly one of ``code`` and ``error`` is
    set. Ambiguity returns a listing of the candidates as ``error``.
    """
    df = wh.sql(
        "SELECT code, web_name, position, team, price FROM sem_players(?::TIMESTAMPTZ) "
        "WHERE season = ? AND web_name ILIKE ? ORDER BY price DESC",
        [t, season, f"%{player}%"],
    )
    if df.empty:
        other = wh.sql(
            "SELECT season, web_name, team FROM sem_players(?::TIMESTAMPTZ) "
            "WHERE web_name ILIKE ? ORDER BY season DESC LIMIT 8",
            [t, f"%{player}%"],
        )
        if other.empty:
            return None, None, (
                f"No player matching {player!r} in sem_players as of {t:%Y-%m-%d %H:%M}Z."
            )
        hits = "; ".join(f"{r.web_name} ({r.team}, {r.season})" for r in other.itertuples())
        return None, None, (
            f"No {season} player matching {player!r}. Matches in other seasons: {hits}."
        )
    exact = df[df["web_name"].str.lower() == player.strip().lower()]
    if len(exact) == 1:
        row = exact.iloc[0]
    elif len(df) == 1:
        row = df.iloc[0]
    else:
        listing = "\n".join(
            f"  - {r.web_name} ({_pos_label(r.position)}, {r.team}, £{r.price:.1f}m)"
            for r in df.itertuples()
        )
        return None, None, (
            f"{player!r} is ambiguous in {season} — say which one you mean:\n{listing}"
        )
    label = f"{row['web_name']} ({_pos_label(row['position'])}, {row['team']}, £{row['price']:.1f}m)"
    return int(row["code"]), label, None


def _guard(as_of: Optional[str]):
    """Common preamble: engine reachable, as_of parsed. Returns (t, error)."""
    problem = _edge._unavailable()
    if problem:
        return None, problem
    try:
        return _edge._now(as_of), None
    except ValueError as exc:
        return None, str(exc)


# -----------------------------------------------------------------------------
# Tools


@mcp.tool()
def player_projections(
    player: str,
    gw: Optional[int] = None,
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
) -> str:
    """Every projection source's numbers for one player, side by side.

    Reads ``sem_projections``: one row per (gameweek, source) with xPts,
    xMins, xPts-if-appears and p(appear) — whatever each provider publishes.
    Sources differ in coverage (some publish one gameweek, some eight), so
    missing cells mean "this source does not publish that field", not zero.

    Args:
        player: Player name (web name, partial ok). Ambiguity returns the
            candidate list rather than a guess.
        gw: Optional gameweek to restrict to. Default: all gameweeks any
            source projects.
        season: Season in FPL's "2026-27" form.
        as_of: Optional ISO-8601 UTC instant (e.g. "2026-08-18T22:50:00Z").
            Default now. The answer shows only fetches at or before this.
    """
    t, err = _guard(as_of)
    if err:
        return err
    with _read() as wh:
        code, label, err = _resolve(wh, t, player, season)
        if err:
            return err
        q = (
            "SELECT gw, source, xpts, xmins, xp_if_appears, p_appear, fetched_at "
            "FROM sem_projections(?::TIMESTAMPTZ) WHERE season = ? AND code = ?"
        )
        params: list = [t, season, code]
        if gw is not None:
            q += " AND gw = ?"
            params.append(gw)
        df = wh.sql(q + " ORDER BY gw, source", params)
        head = f"{_header('sem_projections', t)} {label} — {season}" + (
            f" GW{gw}" if gw is not None else ""
        )
        if df.empty:
            ever = wh.sql(
                "SELECT min(fetched_at) first, max(fetched_at) last FROM "
                "sem_projections(?::TIMESTAMPTZ) WHERE season = ? AND code = ?",
                [_EVER, season, code],
            )
            if pd.isna(ever.iloc[0]["first"]):
                return f"{head}\nNo projection source has ever published a row for this player in {season}."
            return (
                f"{head}\nNo rows at this as_of: the feed's fetches for this player run "
                f"{ever.iloc[0]['first']:%Y-%m-%d %H:%M}Z to {ever.iloc[0]['last']:%Y-%m-%d %H:%M}Z, "
                f"all outside as_of ≤ {t:%Y-%m-%d %H:%M}Z."
            )
        return head + "\n" + _table(df)


@mcp.tool()
def projection_disagreement(
    gw: int,
    season: str = DEFAULT_SEASON,
    top_n: int = 15,
    player: Optional[str] = None,
    as_of: Optional[str] = None,
) -> str:
    """Where projection sources disagree most — or one player's consensus row.

    Reads ``sem_projection_consensus``: per player, the number of sources and
    the mean/min/max/spread/sd of their xPts. The spread IS the uncertainty
    estimate — cross-source disagreement beats any single vendor's error bar.
    Consensus is unweighted by design (no source has an earned track record
    yet).

    Args:
        gw: Gameweek number.
        season: Season in FPL's "2026-27" form.
        top_n: How many players to list, ranked by xPts spread (ignored when
            ``player`` is given). Only players covered by 2+ sources rank.
        player: Optional player name — returns that player's consensus row
            instead of the leaderboard.
        as_of: Optional ISO-8601 UTC instant. Default now.
    """
    t, err = _guard(as_of)
    if err:
        return err
    with _read() as wh:
        base = (
            "SELECT web_name, position, team, price, n_sources, xpts_mean, "
            "xpts_min, xpts_max, xpts_spread, xpts_sd "
            "FROM sem_projection_consensus(?::TIMESTAMPTZ) WHERE season = ? AND gw = ?"
        )
        if player is not None:
            code, label, err = _resolve(wh, t, player, season)
            if err:
                return err
            df = wh.sql(base + " AND code = ?", [t, season, gw, code])
            head = f"{_header('sem_projection_consensus', t)} {label} — {season} GW{gw}"
        else:
            df = wh.sql(
                base + " AND n_sources >= 2 ORDER BY xpts_spread DESC LIMIT ?",
                [t, season, gw, top_n],
            )
            head = (
                f"{_header('sem_projection_consensus', t)} biggest cross-source xPts "
                f"spreads — {season} GW{gw} (sources ≥ 2)"
            )
        if df.empty:
            cov = wh.sql(
                "SELECT gw, count(DISTINCT source) n_sources FROM sem_projections(?::TIMESTAMPTZ) "
                "WHERE season = ? GROUP BY gw ORDER BY gw",
                [t, season],
            )
            if cov.empty:
                return f"{head}\nNo projections at all for {season} at this as_of."
            gws = ", ".join(f"GW{r.gw}({r.n_sources} src)" for r in cov.itertuples())
            return f"{head}\nNo consensus rows for GW{gw}. Coverage at this as_of: {gws}."
        df["position"] = df["position"].map(_pos_label)
        return head + "\n" + _table(df)


@mcp.tool()
def xpts_aggregate(
    group_by: str,
    gw: int,
    season: str = DEFAULT_SEASON,
    position: Optional[str] = None,
    team: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    as_of: Optional[str] = None,
) -> str:
    """Aggregate consensus xPts by team, position or price band.

    Reads ``sem_projection_consensus`` and groups its per-player xPts means.
    Useful for "which team's attackers project best in GW3" or "what does the
    £6m–£7m midfield bracket look like".

    Args:
        group_by: "team", "position" or "price_band" (price_band = whole-£m
            buckets, e.g. 7 covers £7.0–7.9m).
        gw: Gameweek number.
        season: Season in FPL's "2026-27" form.
        position: Optional filter — "GKP", "DEF", "MID" or "FWD".
        team: Optional filter — team short name, e.g. "MCI".
        min_price: Optional filter — price in £m, inclusive.
        max_price: Optional filter — price in £m, inclusive.
        as_of: Optional ISO-8601 UTC instant. Default now.
    """
    t, err = _guard(as_of)
    if err:
        return err
    keys = {"team": "team", "position": "position", "price_band": "CAST(floor(price) AS INT)"}
    if group_by not in keys:
        return f"group_by must be one of {sorted(keys)}, got {group_by!r}."
    where, params = ["season = ?", "gw = ?"], [t, season, gw]
    if position is not None:
        pos = POS_INV.get(position.upper())
        if pos is None:
            return f"position must be one of {sorted(POS_INV)}, got {position!r}."
        where.append("position = ?")
        params.append(pos)
    if team is not None:
        where.append("upper(team) = upper(?)")
        params.append(team)
    if min_price is not None:
        where.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("price <= ?")
        params.append(max_price)
    with _read() as wh:
        df = wh.sql(
            f"SELECT {keys[group_by]} AS {group_by}, count(*) AS players, "
            "round(avg(xpts_mean), 2) AS avg_xpts, round(max(xpts_mean), 2) AS best_xpts, "
            "arg_max(web_name, xpts_mean) AS best_player "
            f"FROM sem_projection_consensus(?::TIMESTAMPTZ) WHERE {' AND '.join(where)} "
            f"GROUP BY 1 ORDER BY {'1' if group_by == 'price_band' else 'avg_xpts DESC'}",
            params,
        )
        head = (
            f"{_header('sem_projection_consensus', t)} consensus xPts by {group_by} — "
            f"{season} GW{gw}"
        )
        filters = [
            f"position={position}" if position else None,
            f"team={team}" if team else None,
            f"price {min_price or ''}–{max_price or ''}" if (min_price or max_price) else None,
        ]
        filters = [f for f in filters if f]
        if filters:
            head += " (" + ", ".join(filters) + ")"
        if df.empty:
            return (
                f"{head}\nNo consensus rows match. Either no source projects {season} GW{gw} "
                f"at this as_of, or the filters exclude everyone."
            )
        if group_by == "position":
            df["position"] = df["position"].map(_pos_label)
        return head + "\n" + _table(df)


@mcp.tool()
def player_form(
    player: str,
    last_k: int = 6,
    season: Optional[str] = None,
    as_of: Optional[str] = None,
) -> str:
    """A player's most recent settled gameweeks: points, minutes, xG/xA/xGC.

    Reads ``sem_player_form`` (realised per-fixture returns including the
    official expected stats) joined to ``sem_fixtures`` for the opponent.
    Rows exist only for finalised gameweeks, so early in a season the recent
    form comes from the previous season — the output names the season of
    every row rather than pretending.

    Args:
        player: Player name (web name, partial ok). Matched across seasons on
            the stable FPL code, so history follows the player.
        last_k: How many most-recent settled gameweeks to show.
        season: Optional season to restrict to ("2025-26"). Default: walk
            backwards across seasons until ``last_k`` rows are found.
        as_of: Optional ISO-8601 UTC instant. Default now — a gameweek only
            appears once its points were finalised at or before this instant.
    """
    t, err = _guard(as_of)
    if err:
        return err
    with _read() as wh:
        # Resolve in the most recent season the name appears in (codes are
        # stable across seasons, element ids are not).
        seasons = wh.sql(
            "SELECT DISTINCT season FROM sem_players(?::TIMESTAMPTZ) "
            "WHERE web_name ILIKE ? ORDER BY season DESC",
            [t, f"%{player}%"],
        )["season"].tolist()
        if not seasons:
            return f"No player matching {player!r} in sem_players as of {t:%Y-%m-%d %H:%M}Z."
        code, label, err = _resolve(wh, t, player, seasons[0])
        if err:
            return err
        q = (
            "SELECT f.season, f.gw, fx.opponent, "
            "CASE WHEN f.was_home THEN 'H' ELSE 'A' END AS venue, "
            "f.minutes, f.total_points AS pts, f.goals_scored AS g, f.assists AS a, "
            "f.expected_goals AS xg, f.expected_assists AS xa, "
            "f.expected_goals_conceded AS xgc, f.bonus, f.bps "
            "FROM sem_player_form(?::TIMESTAMPTZ) f "
            "LEFT JOIN sem_fixtures(?::TIMESTAMPTZ) fx "
            "  ON fx.season = f.season AND fx.fixture_id = f.fixture_id "
            "  AND fx.is_home = f.was_home "
            "WHERE f.code = ?"
        )
        params: list = [t, t, code]
        if season is not None:
            q += " AND f.season = ?"
            params.append(season)
        df = wh.sql(q + " ORDER BY f.season DESC, f.gw DESC LIMIT ?", params + [last_k])
        head = f"{_header('sem_player_form', t)} {label} — last {last_k} settled gameweeks"
        if df.empty:
            scope = f"in {season}" if season else "in any season"
            return (
                f"{head}\nNo settled rows for this player {scope} at this as_of. "
                "Form rows appear only after a gameweek's points are finalised."
            )
        df = df.iloc[::-1].reset_index(drop=True)  # chronological
        row_seasons = sorted(df["season"].unique())
        note = ""
        current = DEFAULT_SEASON
        if current not in row_seasons:
            note = f"\nNote: rows are from {', '.join(row_seasons)} — {current} has no settled gameweeks yet."
        elif len(row_seasons) > 1:
            note = f"\nNote: rows span {', '.join(row_seasons)}."
        return head + "\n" + _table(df) + note


@mcp.tool()
def fixture_difficulty(
    team: Optional[str] = None,
    next_k: int = 6,
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
) -> str:
    """Upcoming fixtures with model-fitted difficulty ratings.

    Reads ``sem_fixtures`` (the schedule as known at the as-of instant) and
    joins the cached ratings artefact ``fixture_difficulty.parquet`` — a
    Dixon-Coles fit written next to the warehouse by the post-gameweek job
    and the T-30h pre-deadline refresh. Difficulty is 0–1, higher = harder
    opponent at that venue. The parquet is a current-model cache, not a
    point-in-time fact: with a historical ``as_of`` the schedule is PIT but
    the ratings are today's.

    Args:
        team: Optional team short name (e.g. "MCI") — that team's next
            fixtures. Default: one summary row per team, easiest run first.
        next_k: How many upcoming fixtures per team to consider.
        season: Season in FPL's "2026-27" form.
        as_of: Optional ISO-8601 UTC instant. Default now.
    """
    t, err = _guard(as_of)
    if err:
        return err
    pq = _edge._db_path().parent / "fixture_difficulty.parquet"
    with _read() as wh:
        rated = pq.exists()
        diff_col = ", d.difficulty" if rated else ""
        diff_join = (
            "LEFT JOIN read_parquet(?) d ON d.season = fx.season "
            "AND d.fixture_id = fx.fixture_id AND d.team_code = fx.team_code "
            if rated
            else ""
        )
        q = (
            "WITH up AS ("
            "  SELECT fx.team, fx.gw, fx.kickoff_utc, fx.opponent, fx.is_home"
            f"  {diff_col}, row_number() OVER (PARTITION BY fx.team ORDER BY fx.kickoff_utc) rk "
            "  FROM sem_fixtures(?::TIMESTAMPTZ) fx "
            f"  {diff_join}"
            "  WHERE fx.season = ? AND fx.kickoff_utc > ?::TIMESTAMPTZ AND NOT fx.finished"
            ") SELECT * FROM up WHERE rk <= ?"
        )
        # Placeholder order follows the query text: sem_fixtures(t), then the
        # parquet path inside the join, then the WHERE params.
        params: list = [t] + ([str(pq)] if rated else []) + [season, t, next_k]
        df = wh.sql(q, params)
        head = f"{_header('sem_fixtures', t)} upcoming fixtures — {season}"
        if rated:
            meta = wh.sql(
                "SELECT max(fitted_at) fitted FROM read_parquet(?)", [str(pq)]
            )
            head += (
                f"\nDifficulty: 0–1, higher = harder (Dixon-Coles fit of "
                f"{meta.iloc[0]['fitted']:%Y-%m-%d %H:%M}Z, from fixture_difficulty.parquet — "
                "a current cache, not point-in-time)"
            )
        else:
            head += (
                "\nNo difficulty ratings: fixture_difficulty.parquet does not exist yet. "
                "It is written by the engine's post-gameweek job / T-30h pre-deadline "
                "refresh (fpl_edge/models/team_goals/ratings_cache.py). Schedule only:"
            )
        if df.empty:
            return f"{head}\nNo upcoming {season} fixtures after {t:%Y-%m-%d %H:%M}Z in sem_fixtures."
        df["fixture"] = df.apply(
            lambda r: f"{r['opponent']} ({'H' if r['is_home'] else 'A'})", axis=1
        )
        if team is not None:
            sub = df[df["team"].str.upper() == team.upper()]
            if sub.empty:
                teams = ", ".join(sorted(df["team"].unique()))
                return f"{head}\nNo team {team!r}. Known: {teams}."
            cols = ["gw", "kickoff_utc", "fixture"] + (["difficulty"] if rated else [])
            return f"{head}\n{team.upper()}, next {len(sub)}:\n" + _table(sub[cols])
        rows = []
        for tm, g in df.groupby("team"):
            g = g.sort_values("kickoff_utc")
            run = " ".join(
                r["fixture"]
                + (f" {r['difficulty']:.2f}" if rated and pd.notna(r["difficulty"]) else "")
                for _, r in g.iterrows()
            )
            row = {"team": tm, "fixtures": run}
            if rated:
                row["avg_difficulty"] = g["difficulty"].mean()
            rows.append(row)
        out = pd.DataFrame(rows)
        if rated:
            out = out.sort_values("avg_difficulty").reset_index(drop=True)
        return head + f"\nNext {next_k} per team" + (
            ", easiest first" if rated else ""
        ) + ":\n" + _table(out)


@mcp.tool()
def ownership_eo(
    player: Optional[str] = None,
    metric: Optional[str] = None,
    preset: Optional[str] = None,
    top_n: int = 15,
    gw: Optional[int] = None,
    season: str = DEFAULT_SEASON,
    as_of: Optional[str] = None,
) -> str:
    """Ownership and effective ownership: what the field actually holds.

    Reads ``sem_ownership``: FPL's own marginal ownership (selected_by_pct)
    beside every external EO metric present (eo_predicted, eo_top10k,
    eo_elite from LiveFPL). EO feeds are per-gameweek and per-season — when a
    metric has no rows for the requested season the output says what the feed
    last wrote instead of returning silence.

    Args:
        player: Optional player name — that player's ownership across all
            metrics.
        metric: Optional EO metric to rank by ("eo_predicted", "eo_top10k",
            "eo_elite"). Default ranking is marginal selected_by_pct.
        preset: Optional shortcut. "template" ranks by the best available
            top-10k/predicted EO; "differential" ranks high consensus-xPts
            players owned by under 10% of the field (needs projections; uses
            ``gw`` or the earliest projected gameweek).
        top_n: How many players to list.
        gw: Gameweek for the "differential" preset's xPts. Default: earliest
            gameweek with consensus rows.
        season: Season in FPL's "2026-27" form.
        as_of: Optional ISO-8601 UTC instant. Default now.
    """
    t, err = _guard(as_of)
    if err:
        return err
    with _read() as wh:
        head = _header("sem_ownership", t)

        def feed_status(m: str) -> str:
            last = wh.sql(
                "SELECT season, max(eo_gw) gw, count(*) n FROM sem_ownership(?::TIMESTAMPTZ) "
                "WHERE eo_metric = ? GROUP BY season ORDER BY season DESC",
                [t, m],
            )
            if last.empty:
                return f"the feed has never written {m}"
            r = last.iloc[0]
            return f"the feed last wrote {m} for GW{int(r['gw'])} of {r['season']}"

        if player is not None:
            code, label, err = _resolve(wh, t, player, season)
            if err:
                return err
            df = wh.sql(
                "SELECT selected_by_pct, eo_provider, eo_metric, eo_gw, eo_value "
                "FROM sem_ownership(?::TIMESTAMPTZ) WHERE season = ? AND code = ? "
                "ORDER BY eo_metric",
                [t, season, code],
            )
            own = df.iloc[0]["selected_by_pct"] if not df.empty else None
            eo = df[df["eo_metric"].notna()]
            out = f"{head} {label} — {season}\nFPL marginal ownership: {own}%"
            if eo.empty:
                return out + f"\nNo external EO rows for this player in {season}."
            return out + "\n" + _table(eo)

        if preset == "differential":
            gw_use = gw
            if gw_use is None:
                g = wh.sql(
                    "SELECT min(gw) g FROM sem_projection_consensus(?::TIMESTAMPTZ) WHERE season = ?",
                    [t, season],
                )
                gw_use = None if g.empty or pd.isna(g.iloc[0]["g"]) else int(g.iloc[0]["g"])
            if gw_use is None:
                return f"{head}\nDifferentials need projections, and no source projects {season} at this as_of."
            df = wh.sql(
                "SELECT DISTINCT o.web_name, o.position, o.team, o.price, o.selected_by_pct, "
                "c.xpts_mean, c.n_sources "
                "FROM sem_ownership(?::TIMESTAMPTZ) o "
                "JOIN sem_projection_consensus(?::TIMESTAMPTZ) c "
                "  ON c.season = o.season AND c.code = o.code AND c.gw = ? "
                "WHERE o.season = ? AND o.selected_by_pct < 10 "
                "ORDER BY c.xpts_mean DESC LIMIT ?",
                [t, t, gw_use, season, top_n],
            )
            df["position"] = df["position"].map(_pos_label)
            return (
                f"{head} differentials — {season} GW{gw_use} consensus xPts, "
                f"ownership < 10%\n" + _table(df)
            )

        if preset == "template":
            for m in ("eo_top10k", "eo_predicted"):
                df = wh.sql(
                    "SELECT web_name, position, team, price, selected_by_pct, "
                    "eo_gw, eo_value AS " + m + " "
                    "FROM sem_ownership(?::TIMESTAMPTZ) WHERE season = ? AND eo_metric = ? "
                    "ORDER BY eo_value DESC LIMIT ?",
                    [t, season, m, top_n],
                )
                if not df.empty:
                    df["position"] = df["position"].map(_pos_label)
                    note = "" if m == "eo_top10k" else (
                        f"\n(eo_top10k has no {season} rows — {feed_status('eo_top10k')}; using eo_predicted)"
                    )
                    return f"{head} template by {m} — {season}{note}\n" + _table(df)
            return (
                f"{head}\nNo EO rows for {season}: {feed_status('eo_top10k')}; "
                f"{feed_status('eo_predicted')}."
            )

        if metric is not None:
            df = wh.sql(
                "SELECT web_name, position, team, price, selected_by_pct, eo_provider, "
                "eo_gw, eo_value "
                "FROM sem_ownership(?::TIMESTAMPTZ) WHERE season = ? AND eo_metric = ? "
                "ORDER BY eo_value DESC LIMIT ?",
                [t, season, metric, top_n],
            )
            if df.empty:
                known = wh.sql(
                    "SELECT DISTINCT eo_metric FROM sem_ownership(?::TIMESTAMPTZ) "
                    "WHERE eo_metric IS NOT NULL",
                    [t],
                )["eo_metric"].tolist()
                return (
                    f"{head}\nNo {metric!r} rows for {season} — {feed_status(metric)}. "
                    f"Known metrics: {sorted(known)}."
                )
            df["position"] = df["position"].map(_pos_label)
            return f"{head} top {metric} — {season}\n" + _table(df)

        df = wh.sql(
            "SELECT DISTINCT web_name, position, team, price, selected_by_pct "
            "FROM sem_ownership(?::TIMESTAMPTZ) WHERE season = ? "
            "ORDER BY selected_by_pct DESC LIMIT ?",
            [t, season, top_n],
        )
        if df.empty:
            return f"{head}\nNo players for {season} at this as_of."
        df["position"] = df["position"].map(_pos_label)
        return f"{head} most-owned (FPL marginal ownership) — {season}\n" + _table(df)
