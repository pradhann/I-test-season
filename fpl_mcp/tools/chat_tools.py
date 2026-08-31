"""The chat toolbelt: free SQL, charts, real transfer advice, saved analyses,
the watchlist and named-manager lookup.

These are the tools the Argus-style chat agent leans on when the pre-shaped
tools (semantic_tools, dossier_tools, content_tools) do not fit the question.
The design rules, inherited from the engine's platform layer and from Argus:

* Every read goes through a private read copy of the warehouse or through
  ``fpl_edge.platform.query.guarded_query`` — the one sanctioned free-SQL path
  (read-only verbs enforced, row/byte caps, optional point-in-time views).
  The single DuckDB writer (ingest, the Telegram bot) is never blocked.
* Errors come back VERBATIM with one line of remediation, because the agent
  reading them can fix its own SQL only if it sees what DuckDB actually said.
* Summary views are capped (200 rows / 50KB) with an explicit omitted-count
  marker; the fix is always "aggregate or filter in SQL", never pagination of
  an unaggregated dump.
* User text (notes, descriptions) is bound as SQL parameters, never
  interpolated, and never executed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Optional

from fpl_mcp.server import mcp  # type: ignore

from fpl_mcp.tools import edge_tools as _edge  # engine location + guards
from fpl_mcp.tools import semantic_tools as _sem  # _read(), _resolve(), _table()
from fpl_mcp.tools.chat_core import (
    SCAN_ROWS,
    param_names,
    render_rows,
    run_with_budget,
    substitute_params,
    valid_name,
)

UTC = dt.timezone.utc
DEFAULT_SEASON = "2026-27"

#: Where saved analyses live, inside the engine repo so git versions them.
def _analyses_dir() -> Path:
    return _edge._HOME / "analyses"


#: Where make_chart writes PNGs. The chat pane serves this directory and
#: replaces [chart:<id>] markers in the agent's prose with the image.
def _assets_dir() -> Path:
    return _edge._HOME / "data" / "warehouse" / "chat" / "assets"


_QUERY_REMEDIATION = (
    "Remediation: send exactly one read-only SELECT over the sem_* macros "
    "(e.g. SELECT ... FROM sem_players(now()) WHERE season = '2026-27'); "
    "DESCRIBE SELECT * FROM <macro>(now()) LIMIT 0 lists a macro's columns."
)


def _resolve_player(wh, t: dt.datetime, player: str, season: str):
    """semantic_tools._resolve, plus a team-suffix tie-breaker.

    Two 2026-27 players are both web-named "Palmer"; the plain resolver can
    only list them. "Palmer CHE" (the team short name from that listing)
    resolves the tie deterministically without ever guessing.
    """
    code, label, err = _sem._resolve(wh, t, player, season)
    if err is None:
        return code, label, err
    parts = player.strip().rsplit(None, 1)
    if len(parts) == 2 and 2 <= len(parts[1]) <= 3:
        name, team = parts
        df = wh.sql(
            "SELECT code, web_name, position, team, price FROM sem_players(?::TIMESTAMPTZ) "
            "WHERE season = ? AND web_name ILIKE ? AND upper(team) = upper(?) "
            "ORDER BY price DESC",
            [t, season, f"%{name}%", team],
        )
        if len(df) == 1:
            row = df.iloc[0]
            label = (
                f"{row['web_name']} ({_sem._pos_label(row['position'])}, "
                f"{row['team']}, £{row['price']:.1f}m)"
            )
            return int(row["code"]), label, None
    if "ambiguous" in err:
        err += "\nTip: retry with the team short name appended, e.g. 'Palmer CHE'."
    return code, label, err


def _run_guarded(sql: str, params: list | tuple = (), *, as_of=None):
    """guarded_query against the engine warehouse, chat-sized scan cap."""
    from fpl_edge.platform.query import guarded_query

    return guarded_query(
        sql, params, as_of=as_of, db=_edge._db_path(), max_rows=SCAN_ROWS
    )


def _render_result(res, *, head: str) -> str:
    lines = [head]
    lines += [f"note: {n}" for n in res.notes]
    lines.append(render_rows(res.columns, res.rows, scan_truncated=res.truncated))
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# 1. query — the workhorse
# -----------------------------------------------------------------------------


@mcp.tool()
def query(sql: str, as_of: Optional[str] = None) -> str:
    """Run one read-only SQL statement against the engine warehouse. THE workhorse.

    Use this for any question the pre-shaped tools do not answer. The query
    surface is the sem_* table macros from your briefing — each takes an as-of
    TIMESTAMPTZ and answers with what was knowable at that instant:

        SELECT web_name, price, selected_by_pct
        FROM sem_players(now()) WHERE season = '2026-27'
        ORDER BY selected_by_pct DESC LIMIT 10

    Rules the guard enforces (violations come back as the guard's own words):
    one statement, read verbs only (no INSERT/UPDATE/CREATE/…), and the raw
    fact_/dim_ tables are also queryable when a macro does not carry what you
    need. Aggregate and filter IN SQL: the summary view shows at most 200 rows
    / 50KB and then reports how many rows were omitted.

    Args:
        sql: One read-only statement. Write timestamps as literals, e.g.
            sem_projections(TIMESTAMPTZ '2026-08-22T10:00:00Z'), or now().
        as_of: Optional ISO-8601 UTC instant (e.g. "2026-08-18T22:50:00Z").
            When given, the raw point-in-time tables are additionally replaced
            by views filtered to that instant, so even SQL written without an
            as_of predicate cannot read the future. Macros still take their
            own timestamp argument regardless.

    Returns:
        A header (row count, elapsed ms, as-of), any guard notes, and a
        markdown table — or the error text verbatim plus one remediation line.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    t = None
    if as_of:
        try:
            t = _edge._now(as_of)
        except ValueError as exc:
            return f"{exc}\n{_QUERY_REMEDIATION}"
    from fpl_edge.platform.query import QueryError

    try:
        res = _run_guarded(sql, as_of=t)
    except QueryError as exc:
        return f"{exc}\n{_QUERY_REMEDIATION}"
    except Exception as exc:  # noqa: BLE001 - DuckDB's message is the useful part
        return f"{type(exc).__name__}: {exc}\n{_QUERY_REMEDIATION}"
    head = f"[query · {res.row_count} rows · {res.elapsed_ms}ms" + (
        f" · as-of {res.as_of}" if res.as_of else ""
    ) + "]"
    return _render_result(res, head=head)


# -----------------------------------------------------------------------------
# 2. make_chart
# -----------------------------------------------------------------------------

# The engine's validated reference palette (fpl_edge/interfaces/render.py), so
# chat charts, Telegram photos and the weekly report read as one system.
_S1, _S2, _S3, _S4 = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
_INK, _INK2, _LINE = "#171a16", "#565b52", "#dde1d8"
_BG = "#fbfcfa"
_SERIES_COLORS = (_S1, _S2, _S3, _S4)


# make_chart is DELETED (CHAT_ARCHITECTURE §4): python_viz in viz_tools.py
# replaced the four-kind spec plotter with real themed matplotlib code.

# -----------------------------------------------------------------------------
# 3. suggest_transfers
# -----------------------------------------------------------------------------


@mcp.tool()
def suggest_transfers(
    max_hits: int = 0,
    horizon: int = 5,
    must_keep: Optional[str] = None,
    notes: Optional[str] = None,
) -> str:
    """The engine's real transfer recommendation for the user's own team.

    SLOW: expect one to five minutes — tell the user you are solving before
    you call it. It reconstructs the current squad, loads the committed points
    forecast (data/warehouse/forecast.parquet — the exact artefact the weekly
    report's Transfers section reads), and runs the real optimiser: the free
    optimum, the roll, and every screened candidate move each solved by the
    same MILP (capped at 30s per solve; a capped solve reports "best found,
    not a proven optimum" in its notes) and scored by the same objective. The answer
    ranks the chosen move against the alternatives it beat, with free-transfer
    /hit/bank arithmetic and a provenance line. Do not paraphrase the numbers
    away — the deltas against rolling are the content.

    If no forecast artefact has been committed, this returns the engine's own
    configuration message (naming `uv run fpl solve` as the fix) rather than
    inventing a projection.

    Args:
        max_hits: Most points-hits (-4s) the CHOSEN move may take. Default 0:
            moves costing points are still solved and shown, but the headline
            pick stays inside the free transfers unless you raise this.
        horizon: How many gameweeks ahead the objective sums over. Default 5.
        must_keep: Optional comma-separated player names that must not be
            sold. Names resolve through sem_players; an ambiguous name returns
            the candidate list instead of a guess.
        notes: Optional free text echoed back in the answer for context (e.g.
            "user is considering wildcard"). Never interpreted or executed.

    Returns:
        The squad state, the ranked recommendation with alternatives, the hit
        verdict, and a provenance line — or an honest message naming exactly
        what is missing.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    import pandas as pd

    from fpl_edge.myteam.forecast import (
        PointsForecastUnavailableError,
        TablePointsForecast,
    )
    from fpl_edge.myteam.recommend import NoSquadError, recommend
    from fpl_edge.myteam.report import current_state
    from fpl_edge.myteam.state import PlayerIndex
    from fpl_edge.opt import ObjectiveMode, OptimizerConfig, SolverConfig
    from fpl_edge.opt.interfaces import RankUtilityUnavailableError

    now = dt.datetime.now(UTC)
    season = DEFAULT_SEASON
    horizon = max(1, min(int(horizon), 8))
    max_hits = max(0, int(max_hits))

    fc_path = _edge._HOME / "data" / "warehouse" / "forecast.parquet"
    points_forecast = None
    if fc_path.exists():
        points_forecast = TablePointsForecast(
            frame=pd.read_parquet(fc_path), name="table:forecast.parquet"
        )

    with _sem._read() as wh:
        # Resolve must_keep first: an ambiguity should cost seconds, not a solve.
        keep_codes: dict[int, str] = {}
        if must_keep:
            for raw in (p.strip() for p in must_keep.split(",")):
                if not raw:
                    continue
                code, label, err = _resolve_player(wh, now, raw, season)
                if err:
                    return err
                keep_codes[int(code)] = label

        try:
            state = current_state(wh, season, now)
        except Exception as exc:  # noqa: BLE001 - a dead endpoint must not raise
            return (
                "Could not reconstruct your squad from the FPL endpoints: "
                f"{type(exc).__name__}: {exc}\nNo recommendation is offered — "
                "guessing at the squad would make every line below it fiction."
            )
        snapshot = wh.snapshot_at(now)
        index = PlayerIndex.from_snapshot(snapshot, season)
        try:
            gw = int(snapshot.next_gw(season))
        except Exception:  # noqa: BLE001 - no calendar; use the state's own gw
            gw = int(state.gw)
        gws = list(range(gw, gw + horizon))

        # The same machinery and forecast configuration as the weekly report's
        # Transfers section, with one chat-sized difference: a 30s cap per MILP
        # solve (the engine default is 300s, and ~15 solves at that limit is an
        # hour — fine for a cron report, unusable in a conversation). A solve
        # that hits the cap returns its best incumbent and the plan's notes say
        # "best found, not a proven optimum", so the trade is visible.
        cfg = OptimizerConfig(
            mode=ObjectiveMode.EXPECTED_POINTS,
            # 25/position (the report uses 40) keeps the MILP small enough to
            # answer in chat time; the recommendation's own render reports how
            # bounded the search was.
            max_candidates_per_position=25,
            solver=SolverConfig(time_limit_s=60.0, mip_gap_rel=5e-3),
        )
        try:
            rec = recommend(
                snapshot,
                state,
                season=season,
                gws=gws,
                points_forecast=points_forecast,
                # The surrogate, stated in writing — the same configuration the
                # weekly report uses until the rank simulator ships a provider.
                mode=ObjectiveMode.EXPECTED_POINTS,
                config=cfg,
                candidates=8,
            )
        except PointsForecastUnavailableError as exc:
            return (
                "No transfer recommendation: no points forecast is configured.\n\n"
                f"{exc}\n\n"
                "Fix: run `uv run fpl solve` in the engine repo — it fits the "
                "models and commits data/warehouse/forecast.parquet, the exact "
                "artefact the weekly report's Transfers section reads."
            )
        except RankUtilityUnavailableError as exc:
            return str(exc)
        except NoSquadError as exc:
            return str(exc)

    lines = [state.render(), "", rec.render(index)]

    # Constraint verdicts. The solver ranked everything; these lines only say
    # which ranked move satisfies what the caller asked for.
    ranked = [rec.chosen, *rec.alternatives]

    def _ok(m) -> bool:
        return m.hits <= max_hits and not (set(m.out) & set(keep_codes))

    if not _ok(rec.chosen):
        best = next((m for m in ranked if _ok(m)), None)
        why = []
        if rec.chosen.hits > max_hits:
            why.append(f"takes {rec.chosen.hits} hit(s) > max_hits={max_hits}")
        sold = set(rec.chosen.out) & set(keep_codes)
        if sold:
            why.append("sells " + ", ".join(keep_codes[c] for c in sorted(sold)))
        lines.append("")
        lines.append(
            "Constraint check: the optimiser's top move " + " and ".join(why) + "."
        )
        if best is not None:
            delta = best.objective - rec.chosen.objective
            lines.append(
                f"Best move WITHIN your constraints: {best.describe(index)} "
                f"(objective {best.objective:.2f}, {delta:+.2f} vs the "
                f"unconstrained winner, -{best.hit_points} hit)."
            )
        else:
            lines.append(
                "No solved move satisfies the constraints; rolling the "
                "transfer is the constrained answer."
            )
    if notes:
        lines += ["", f"Your note (recorded in this answer only): {notes}"]

    lines += [
        "",
        (
            f"Provenance: forecast {points_forecast.name} ({fc_path}); "
            f"objective {rec.mode.value} over GW{gws[0]}-{gws[-1]}; squad via "
            f"{state.provenance}; warehouse read-copy at {now:%Y-%m-%d %H:%M}Z; "
            f"{rec.n_candidates_solved}/{rec.n_candidates_screened} candidates "
            f"solved in {rec.solve_seconds:.1f}s."
        ),
    ]
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# 4. saved analyses
# -----------------------------------------------------------------------------


def _analysis_path(name: str) -> Path:
    return _analyses_dir() / f"{name}.json"


def _git(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(_edge._HOME), *args],
        capture_output=True, text=True, check=False,
    )


@mcp.tool()
def save_analysis(
    name: str,
    description: str,
    sql: str,
    params_schema: Optional[dict] = None,
) -> str:
    """Save a reusable, parameterised SQL analysis and commit it to git.

    An analysis is one read-only statement over the warehouse (sem_* macros
    and raw tables), stored as JSON in the engine repo's analyses/ directory
    and committed individually, so "why did the bot say that" is answerable
    from git history months later. Save an analysis when a question will
    recur; run it with run_analysis.

    Parameters are written in the SQL as ``$name`` and bound as real DuckDB
    parameters at run time — never string-interpolated — e.g.:

        SELECT web_name, xpts_mean FROM sem_projection_consensus(now())
        WHERE season = $season AND gw = $gw ORDER BY xpts_mean DESC LIMIT 10

    Budget contract: run_analysis enforces a 10-second wall budget. Push
    filtering and aggregation into the SQL now, at authoring time.

    Args:
        name: Identifier, lower-case [a-z0-9_-], max 64 chars. Saving an
            existing name overwrites it (the old version stays in git).
        description: One or two sentences: what it answers and when to use it.
        sql: One read-only statement, ``$param`` placeholders allowed.
        params_schema: Optional {param: {"type": ..., "description": ...,
            "default": ...}} — defaults are applied when run_analysis is
            called without that parameter.

    Returns:
        Confirmation with the declared parameters and the git commit, or the
        validation error verbatim.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    if not valid_name(name):
        return (
            f"invalid analysis name {name!r}.\nRemediation: use lower-case "
            "letters, digits, _ or -, starting with a letter or digit, max 64 chars."
        )
    from fpl_edge.platform.query import (
        QueryError,
        assert_read_only,
        assert_single_statement,
    )

    try:
        assert_single_statement(sql)
        assert_read_only(sql)
    except QueryError as exc:
        return f"{exc}\n{_QUERY_REMEDIATION}"

    declared = param_names(sql)
    schema = params_schema or {}
    undeclared = [p for p in schema if p not in declared]
    if undeclared:
        return (
            f"params_schema declares {undeclared} but the SQL contains no "
            f"${undeclared[0]}.\nRemediation: the SQL's $params are {declared or 'none'}; "
            "make the schema match."
        )

    _analyses_dir().mkdir(parents=True, exist_ok=True)
    path = _analysis_path(name)
    payload = {
        "name": name,
        "description": description,
        "sql": sql,
        "params_schema": schema,
        "saved_utc": dt.datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    rel = str(path.relative_to(_edge._HOME))
    add = _git(["add", rel])
    if add.returncode != 0:
        return f"Saved {rel} but `git add` failed:\n{add.stderr.strip()}"
    conv = os.environ.get("ARGUS_CONV_ID", "").strip()
    msg = f"analysis: {name}" + (f"\n\nconversation: {conv}" if conv else "")
    commit = _git([
        "commit", "--author", "Nripesh <nripeshpradhan@gmail.com>",
        "-m", msg, "--", rel,
    ])
    if commit.returncode != 0:
        out = (commit.stdout + commit.stderr).strip()
        if "nothing to commit" in out or "no changes added" in out:
            committed = "unchanged — identical to the committed version, no new commit"
        else:
            return f"Saved {rel} but `git commit` failed:\n{out}"
    else:
        sha = _git(["rev-parse", "--short", "HEAD"]).stdout.strip()
        committed = f"committed {sha} ({msg.splitlines()[0]})"
    return (
        f"Saved analysis {name!r} → {rel}; {committed}.\n"
        f"Parameters: {declared or 'none'}. Run it with "
        f"run_analysis(name={name!r}"
        + (", params={...})" if declared else ")")
        + f". The 10s budget applies at run time."
    )


@mcp.tool()
def run_analysis(name: str, params: Optional[dict] = None) -> str:
    """Run a saved analysis under the 10-second budget.

    Parameters are bound as DuckDB parameters (the SQL's ``$name``
    placeholders), with defaults from the saved params_schema applied first.
    Output follows the same contract as `query`: a 200-row/50KB summary with
    an omitted-count marker; aggregate in the SQL if you need more.

    A run that exceeds the 10s wall budget returns an error with remediation
    rather than a partial result — the fix is in the analysis SQL, not here.

    Args:
        name: The analysis name, as listed by list_analyses.
        params: Values for the SQL's $params, e.g. {"season": "2026-27",
            "gw": 3}. Missing values with no default are an error, never
            guessed.

    Returns:
        The result table, the budget error, or the SQL error verbatim plus
        one remediation line.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    path = _analysis_path(name) if valid_name(name) else None
    if path is None or not path.exists():
        have = sorted(p.stem for p in _analyses_dir().glob("*.json"))
        return (
            f"No analysis named {name!r}. Saved analyses: {have or 'none yet'}."
        )
    saved = json.loads(path.read_text())
    schema: dict = saved.get("params_schema") or {}
    values = {
        k: v.get("default") for k, v in schema.items()
        if isinstance(v, dict) and "default" in v
    }
    values.update(params or {})

    sub_sql, binds, missing = substitute_params(saved["sql"], values)
    if missing:
        detail = {
            p: (schema.get(p) or {}).get("description", "no description")
            for p in missing
        }
        return (
            f"Missing parameter value(s) for {missing}.\nDeclared: {detail}\n"
            "Remediation: pass them in `params`, e.g. "
            f"run_analysis(name={name!r}, params={{{missing[0]!r}: ...}})."
        )

    from fpl_edge.platform.query import QueryError

    try:
        res, err = run_with_budget(lambda: _run_guarded(sub_sql, binds))
    except QueryError as exc:
        return f"{exc}\n{_QUERY_REMEDIATION}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}\n{_QUERY_REMEDIATION}"
    if err:
        return f"{err}\n(analysis {name!r}; edit it with save_analysis)"
    head = (
        f"[analysis {name!r} · {res.row_count} rows · {res.elapsed_ms}ms · "
        f"params {values or 'none'}]"
    )
    return _render_result(res, head=head)


@mcp.tool()
def list_analyses() -> str:
    """List every saved analysis: name, parameters, description.

    Check here before writing a new query for a recurring question — running
    a saved analysis is cheaper and its history is in git.

    Returns:
        One line per analysis, or a note that none exist yet.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    d = _analyses_dir()
    files = sorted(d.glob("*.json")) if d.exists() else []
    if not files:
        return "No saved analyses yet. Create one with save_analysis."
    lines = [f"Saved analyses ({len(files)}):"]
    for p in files:
        try:
            saved = json.loads(p.read_text())
        except Exception:  # noqa: BLE001 - a corrupt file is reported, not fatal
            lines.append(f"- {p.stem}: (unreadable JSON)")
            continue
        ps = param_names(saved.get("sql", ""))
        lines.append(
            f"- {saved.get('name', p.stem)}"
            + (f" (params: {', '.join('$' + x for x in ps)})" if ps else "")
            + f": {saved.get('description', '')}"
        )
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# 5. watchlist
# -----------------------------------------------------------------------------


@mcp.tool()
def watchlist_add(player: str, note: Optional[str] = None) -> str:
    """Put a player on the user's watchlist, with an optional note.

    Use this when the user says they want to keep an eye on someone without
    making a falsifiable claim ("keep an eye on Palmer", "remind me about
    Semenyo before the deadline"). Every open item is surfaced in the T-30h
    pre-deadline digest, so the reminder reaches the user's Telegram before
    every deadline until the item is removed. For an actual belief with a
    testable claim ("I like Palmer this week"), use submit_idea instead — that
    is tracked and scored; the watchlist only reminds.

    Adding a player already on the list replaces their note (history kept).
    An ambiguous name returns the candidate list rather than a guess.

    Args:
        player: Player name (web name, partial ok), resolved via sem_players.
        note: Optional free text stored verbatim with the item, echoed back in
            the digest ("You wanted: Palmer — 'liking him'").

    Returns:
        Confirmation with the item id, or the ambiguity/not-found listing.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    from fpl_edge.interfaces.watchlist import Watchlist

    now = dt.datetime.now(UTC)
    with _sem._read() as wh:
        code, label, err = _resolve_player(wh, now, player, DEFAULT_SEASON)
        if err:
            return err
        web_name = str(
            wh.sql(
                "SELECT web_name FROM sem_players(?::TIMESTAMPTZ) "
                "WHERE season = ? AND code = ? LIMIT 1",
                [now, DEFAULT_SEASON, code],
            ).iloc[0]["web_name"]
        )
    try:
        with _edge._open() as whw:
            item_id = Watchlist(whw).add(
                code=int(code), player_name=web_name, season=DEFAULT_SEASON,
                note=note, source="mcp", now=now,
            )
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            return _edge._locked_message(exc)
        raise
    return (
        f"Watching {label} ({item_id}, from {now:%Y-%m-%d %H:%M}Z)"
        + (f" — note: {note!r}" if note else "")
        + ". It will appear in every T-30h pre-deadline digest until "
        "watchlist_remove."
    )


@mcp.tool()
def watchlist_list() -> str:
    """The user's open watchlist items, oldest first.

    Returns:
        One line per open item with when it was added and any note, or a note
        that the list is empty.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    with _sem._read() as wh:
        try:
            items = wh.sql(
                "SELECT item_id, created_utc, player_name, note FROM watchlist "
                "WHERE season = ? AND NOT resolved ORDER BY created_utc",
                [DEFAULT_SEASON],
            )
        except Exception:  # noqa: BLE001 - table absent = nothing ever added
            return "The watchlist is empty — nothing has ever been added."
    if items.empty:
        return "The watchlist is empty (no open items)."
    lines = [f"Watchlist — {len(items)} open item(s), surfaced in every T-30h digest:"]
    for r in items.itertuples(index=False):
        note = r.note if isinstance(r.note, str) and r.note else None
        lines.append(
            f"- {r.player_name} (added {r.created_utc:%d %b %H:%M}Z)"
            + (f" — '{note}'" if note else "")
        )
    return "\n".join(lines)


@mcp.tool()
def watchlist_remove(player: str) -> str:
    """Take a player off the watchlist (the item is resolved, never deleted).

    Args:
        player: Player name (web name, partial ok), resolved via sem_players.

    Returns:
        Confirmation, or a note that the player was not on the list.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    from fpl_edge.interfaces.watchlist import Watchlist

    now = dt.datetime.now(UTC)
    with _sem._read() as wh:
        code, label, err = _resolve_player(wh, now, player, DEFAULT_SEASON)
        if err:
            return err
    try:
        with _edge._open() as whw:
            n = Watchlist(whw).resolve(code=int(code), season=DEFAULT_SEASON, now=now)
    except Exception as exc:  # noqa: BLE001
        if "lock" in str(exc).lower():
            return _edge._locked_message(exc)
        raise
    if n == 0:
        return f"{label} was not on the watchlist."
    return f"Removed {label} from the watchlist (resolved {n} item(s), kept in history)."


# -----------------------------------------------------------------------------
# 6. get_manager_by_name
# -----------------------------------------------------------------------------


@mcp.tool()
def get_manager_by_name(name: str) -> str:
    """Resolve a known FPL manager by NAME to their entry id and record.

    "What did Ben Crellin do?" works through this: the name is matched against
    the engine's curated elite list (verified against the live API, because
    entry ids rot every season) and against every crawled manager in
    dim_manager. The answer carries the entry id — use it with `query` over
    sem_manager_picks / sem_manager_transfers / sem_elite_ownership for
    squads, moves and cohort ownership, or get_manager_history for the raw
    API view.

    Args:
        name: The manager's real name (accent/case-insensitive, partial ok),
            e.g. "Crellin", "Finn Sollie", "Mark Hurst".

    Returns:
        Entry id, identity, current-season standing, past record and where to
        query details — or the candidate list when several managers match.
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    from fpl_edge.ingest.rivals.elite import ELITE_NAMED, _norm

    q = _norm(name)
    if len(q) < 3:
        return f"{name!r} is too short to match safely — give at least 3 characters."

    matches: dict[int, dict] = {}
    for e in ELITE_NAMED:
        if q in _norm(e.name) or _norm(e.name) in q:
            matches[e.entry_id] = {
                "entry_id": e.entry_id, "player_name": e.name,
                "origin": f"curated elite list ({e.note.split('.')[0]})",
            }
    season = DEFAULT_SEASON
    with _sem._read() as wh:
        try:
            crawled = wh.sql(
                "SELECT DISTINCT entry_id, player_name, entry_name, source "
                "FROM dim_manager WHERE player_name IS NOT NULL "
                "AND length(player_name) >= 4"
            )
        except Exception:  # noqa: BLE001 - no rival tables ingested yet
            crawled = None
        if crawled is not None:
            for r in crawled.itertuples(index=False):
                nn = _norm(str(r.player_name))
                if nn and (q in nn or nn in q):
                    matches.setdefault(int(r.entry_id), {
                        "entry_id": int(r.entry_id),
                        "player_name": str(r.player_name),
                        "origin": f"crawled ({r.source}), team '{r.entry_name}'",
                    })
        if not matches:
            curated = ", ".join(e.name for e in ELITE_NAMED)
            return (
                f"No tracked manager matches {name!r}. The curated elite are: "
                f"{curated}. Crawled cohorts (top-of-overall sample, snowball, "
                "mini-leagues) are searchable too, but only by the name FPL "
                "displays."
            )
        # One PERSON can appear once curated and once crawled under the same
        # id (the dict already merged that); different ids are different people.
        if len(matches) > 1:
            listing = "\n".join(
                f"  - {m['player_name']} (entry {m['entry_id']}; {m['origin']})"
                for m in matches.values()
            )
            return (
                f"{name!r} matches {len(matches)} tracked managers — say which "
                f"one you mean:\n{listing}"
            )
        m = next(iter(matches.values()))
        entry_id = m["entry_id"]

        lines = [f"{m['player_name']} — entry id {entry_id} ({m['origin']})."]
        try:
            ident = wh.sql(
                "SELECT entry_name, region, years_active FROM dim_manager "
                "WHERE entry_id = ? ORDER BY as_of DESC LIMIT 1", [entry_id]
            )
            if not ident.empty:
                r = ident.iloc[0]
                lines.append(
                    f"Team '{r['entry_name']}', region {r['region']}, "
                    f"{r['years_active']} seasons played."
                )
            cur = wh.sql(
                "SELECT gw, total_points, overall_rank FROM fact_manager_gw "
                "WHERE entry_id = ? AND season = ? "
                "QUALIFY row_number() OVER (ORDER BY gw DESC, as_of DESC) = 1",
                [entry_id, season],
            )
            if not cur.empty:
                r = cur.iloc[0]
                lines.append(
                    f"{season} through GW{int(r['gw'])}: {int(r['total_points'])} "
                    f"pts, overall rank {int(r['overall_rank']):,}."
                )
            past = wh.sql(
                "SELECT count(DISTINCT season) AS n, min(overall_rank) AS best, "
                "arg_min(season, overall_rank) AS best_season "
                "FROM fact_manager_season WHERE entry_id = ?", [entry_id]
            )
            if not past.empty and int(past.iloc[0]["n"] or 0):
                r = past.iloc[0]
                lines.append(
                    f"Past record on file: {int(r['n'])} season(s), best finish "
                    f"{int(r['best']):,} ({r['best_season']})."
                )
            tr = wh.sql(
                "SELECT count(*) AS n FROM fact_manager_transfer "
                "WHERE entry_id = ? AND season = ?", [entry_id, season]
            )
            lines.append(
                f"{int(tr.iloc[0]['n'])} transfer(s) stored for {season}."
            )
        except Exception as exc:  # noqa: BLE001 - identity already answered
            lines.append(f"(record summary unavailable: {type(exc).__name__}: {exc})")
        lines.append(
            "Details: query sem_manager_picks(now()) / sem_manager_transfers(now()) "
            f"WHERE entry_id = {entry_id} — or get_manager_history({entry_id}) "
            "for the live API view."
        )
        return "\n".join(lines)
