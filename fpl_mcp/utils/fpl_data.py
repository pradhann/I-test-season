"""
FPL reference data for the toolbelt: warehouse first, engine fetcher second.

WHAT THIS MODULE USED TO BE, AND WHY IT CHANGED (PIPELINES.md §3 defect 2)
--------------------------------------------------------------------------
The first version was a second fetch stack: bare ``requests`` with no retries,
no archive and no User-Agent policy, writing plain-JSON caches
(``bootstrap_static.json``, ``fixtures.json``, one file per bootstrap key)
INTO ``data/raw/fpl_api/`` -- the directory where the engine's hash-named,
timestamped provenance archive lives. Two conventions in one directory, and
the mutable one overwrote itself silently.

The unification, per call site:

* **Lookups read the WAREHOUSE.** ``get_elements_df``, ``get_teams_df``,
  ``get_fixtures_df`` and ``current_gameweek`` answer "what is this element /
  team / fixture / gameweek" -- reference lookups against data the engine
  already ingests on a schedule (``dim_player``, ``fact_player_state``,
  ``dim_team``, ``fact_fixture``, ``dim_event``). A second live fetch for a
  table the warehouse refreshes regularly buys nothing except a second cache
  to go stale, so the warehouse (via a read-only private copy that never
  blocks the single writer) is the primary source.
* **Live fetches go through the engine's** :class:`fpl_edge.ingest.http.Fetcher`
  (retries, project UA, hash-named archive into ``data/raw/fpl_api/`` -- the
  SAME convention as the engine's own ingest, so the archive dir stays one
  convention). They run only as the fallback when no warehouse exists (fresh
  checkout, ``FPL_EDGE_DB`` pointing nowhere) or on ``force_refresh=True``.
* **Entry endpoints go through the rivals client.** ``entry_json`` routes
  ``/entry/{id}/...`` calls through
  :class:`fpl_edge.ingest.rivals.client.RivalsFetcher`: enforced politeness
  interval, per-endpoint TTL cache shared with the crawl, transport-only
  retries (a 404 is an answer, not a failure), the same archive, and a hard
  per-process request budget that fails loudly instead of growing silently.
* **The one remaining plain-JSON convenience cache moved** to
  ``data/cache/fpl_mcp/`` (gitignored), OUT of the provenance archive. It
  exists only for the live-fallback path and expires after
  :data:`CACHE_TTL_S`.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

UTC = dt.timezone.utc

# Base URL for the Fantasy Premier League API
FPL_BASE_URL = "https://fantasy.premierleague.com/api"

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: The toolbelt's own convenience cache -- deliberately NOT under data/raw/,
#: which is the engine's content-addressed provenance archive. Plain, mutable,
#: safe to delete at any time.
CACHE_DIR = _REPO_ROOT / "data" / "cache" / "fpl_mcp"

#: How long a cached live-fallback body is trusted. Matches the rivals
#: client's own TTL for bootstrap-static (see
#: ``fpl_edge.ingest.rivals.client.TTL_S``).
CACHE_TTL_S = 3600.0

#: element_type -> the bootstrap's singular_name_short, so warehouse-shaped
#: frames carry the same position strings the live API produces.
_POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

#: Hard ceiling on live entry-endpoint requests per process. The toolbelt is
#: interactive -- a chat session that legitimately needs hundreds of entry
#: fetches is a crawl, and crawls belong to the engine's budgeted pipelines.
#: Exhaustion raises the client's own BudgetExhausted with an itemised receipt.
ENTRY_BUDGET_LIMIT = 200

_entry_fetcher = None  # lazy singleton; keeps pacing + budget across calls


# ---------------------------------------------------------------------------
# The warehouse (primary source for lookups)


def _db_path() -> Path:
    configured = os.environ.get("FPL_EDGE_DB")
    if configured:
        return Path(configured).expanduser()
    home = os.environ.get("FPL_EDGE_HOME")
    root = Path(home).expanduser() if home else _REPO_ROOT
    return root / "data" / "warehouse" / "fpl.duckdb"


def _read_warehouse():
    """A read-only private copy of the warehouse, or None when there is none.

    ``Warehouse.read_copy`` copies the file and reads the copy, so this can
    never block or be blocked by the single writer (the bot, the DAG, an
    ingest). None -- not an exception -- so every caller falls back to the
    live route instead of dying with the engine.
    """
    path = _db_path()
    if not path.exists():
        return None
    try:
        from fpl_edge.store import Warehouse

        return Warehouse.read_copy(path)
    except Exception:  # noqa: BLE001 - a broken engine degrades to live fetch
        return None


def _latest(table: str, keys: str) -> str:
    """SQL for the newest row per entity in an append-only PIT table."""
    return (
        f"SELECT * FROM (SELECT *, row_number() OVER ("
        f"PARTITION BY {keys} ORDER BY as_of DESC) AS rn FROM {table}) "
        f"WHERE rn = 1"
    )


def _elements_from_warehouse() -> Optional[pd.DataFrame]:
    """Bootstrap-elements shape from dim_player + fact_player_state + points.

    Columns are the ones the tools actually consume (id, names, team,
    team_name, element_type, position, now_cost, total_points,
    selected_by_percent), in the same units the live API uses -- ``now_cost``
    stays in tenths so ``now_cost / 10`` keeps meaning £m everywhere.
    """
    wh = _read_warehouse()
    if wh is None:
        return None
    try:
        with wh:
            df = wh.sql(f"""
                WITH p AS ({_latest("dim_player", "season, code")}),
                     s AS ({_latest("fact_player_state", "season, code")}),
                     t AS ({_latest("dim_team", "season, team_code")}),
                     season AS (SELECT max(season) AS season FROM p),
                     f AS ({_latest("fact_player_fixture",
                                    "season, code, fixture_id")}),
                     pts AS (SELECT season, code,
                                    sum(coalesce(total_points, 0)) AS total_points
                             FROM f GROUP BY season, code)
                SELECT p.element_id            AS id,
                       p.code                  AS code,
                       p.first_name, p.second_name, p.web_name,
                       t.team_id               AS team,
                       t.name                  AS team_name,
                       p.position              AS element_type,
                       s.price_tenths          AS now_cost,
                       s.selected_by_pct       AS selected_by_percent,
                       coalesce(pts.total_points, 0) AS total_points
                FROM p
                JOIN season USING (season)
                JOIN s   ON s.season = p.season AND s.code = p.code
                LEFT JOIN t   ON t.season = p.season AND t.team_code = p.team_code
                LEFT JOIN pts ON pts.season = p.season AND pts.code = p.code
                ORDER BY p.element_id
            """)
    except Exception:  # noqa: BLE001 - missing tables on a young warehouse
        return None
    if df.empty:
        return None
    df["position"] = df["element_type"].map(_POSITIONS).fillna("")
    return df


def _teams_from_warehouse() -> Optional[pd.DataFrame]:
    wh = _read_warehouse()
    if wh is None:
        return None
    try:
        with wh:
            df = wh.sql(f"""
                WITH t AS ({_latest("dim_team", "season, team_code")}),
                     season AS (SELECT max(season) AS season FROM t)
                SELECT t.team_id AS id, t.team_code AS code,
                       t.name, t.short_name
                FROM t JOIN season USING (season)
                ORDER BY t.team_id
            """)
    except Exception:  # noqa: BLE001
        return None
    return None if df.empty else df


def _fixtures_from_warehouse() -> Optional[pd.DataFrame]:
    """fact_fixture in the live /fixtures/ shape (per-season team ids)."""
    wh = _read_warehouse()
    if wh is None:
        return None
    try:
        with wh:
            df = wh.sql(f"""
                WITH fx AS ({_latest("fact_fixture", "season, fixture_id")}),
                     t  AS ({_latest("dim_team", "season, team_code")}),
                     season AS (SELECT max(season) AS season FROM fx)
                SELECT fx.fixture_id  AS id,
                       fx.gw          AS event,
                       fx.kickoff_utc AS kickoff_time,
                       th.team_id     AS team_h,
                       ta.team_id     AS team_a,
                       fx.home_score  AS team_h_score,
                       fx.away_score  AS team_a_score,
                       fx.finished
                FROM fx
                JOIN season USING (season)
                LEFT JOIN t th ON th.season = fx.season
                               AND th.team_code = fx.home_team_code
                LEFT JOIN t ta ON ta.season = fx.season
                               AND ta.team_code = fx.away_team_code
                ORDER BY fx.fixture_id
            """)
    except Exception:  # noqa: BLE001
        return None
    if df.empty:
        return None
    df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], errors="coerce", utc=True)
    return df


# ---------------------------------------------------------------------------
# The live fallback: the engine's Fetcher plus a small TTL'd cache


def _live_json(endpoint: str) -> Any:
    """One live FPL API fetch through the engine's archiving Fetcher.

    Retries, the project User-Agent and a hash-named body under
    ``data/raw/fpl_api/`` all come from :class:`fpl_edge.ingest.http.Fetcher`
    -- the same class, source name and on-disk convention the engine's own
    bootstrap ingest uses, so the archive directory keeps exactly one layout.
    """
    from fpl_edge.ingest.http import Fetcher

    with Fetcher("fpl_api", base_url=FPL_BASE_URL) as fetcher:
        return fetcher.get_json(endpoint).body


def _cache_path(name: str) -> Path:
    return CACHE_DIR / name


def _cached_json(name: str) -> Optional[Any]:
    path = _cache_path(name)
    try:
        if not path.exists():
            return None
        age = dt.datetime.now(UTC).timestamp() - path.stat().st_mtime
        if age > CACHE_TTL_S:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(name: str, body: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(body), encoding="utf-8")
        tmp.replace(path)  # atomic: a concurrent reader never sees half a file
    except OSError:
        pass  # the cache is a convenience; the fetch already succeeded


def get_bootstrap_data(force_refresh: bool = False) -> Dict[str, Any]:
    """The live bootstrap-static body, fetched politely and cached briefly.

    This is the LIVE primitive, kept for the callers that genuinely need the
    raw bootstrap shape when no warehouse is available. The fetch goes through
    the engine's Fetcher (archive + retries); the convenience cache lives in
    ``data/cache/fpl_mcp/`` with a :data:`CACHE_TTL_S` lifetime -- never in
    the raw archive, and never one file per bootstrap key.
    """
    if not force_refresh:
        cached = _cached_json("bootstrap_static.json")
        if cached is not None:
            return cached
    data = _live_json("bootstrap-static/")
    _write_cache("bootstrap_static.json", data)
    return data


def _elements_from_bootstrap(data: Dict[str, Any]) -> pd.DataFrame:
    """The live bootstrap shaped exactly as the warehouse path shapes it."""
    elements = pd.DataFrame(data["elements"])
    teams_df = pd.DataFrame(data["teams"])[["id", "name"]].rename(
        columns={"id": "team", "name": "team_name"}
    )
    positions_df = pd.DataFrame(data["element_types"])[
        ["id", "singular_name_short"]
    ].rename(columns={"id": "element_type", "singular_name_short": "position"})
    elements = elements.merge(teams_df, on="team", how="left")
    elements = elements.merge(positions_df, on="element_type", how="left")
    if "selected_by_percent" in elements.columns:
        elements["selected_by_percent"] = pd.to_numeric(
            elements["selected_by_percent"], errors="coerce"
        )
    return elements


def get_elements_df(force_refresh: bool = False) -> pd.DataFrame:
    """All players, with team_name and position attached.

    CHOICE: warehouse. Every consumer of this frame is a lookup -- "which
    player is element 233, what club, what price" -- against reference data
    the engine ingests on a schedule (``dim_player`` / ``fact_player_state``
    are refreshed by the bootstrap ingest, ``fact_player_fixture`` supplies
    season points). The live API is only consulted when no warehouse can be
    read, or on ``force_refresh=True``, and then through the engine's Fetcher.
    """
    if not force_refresh:
        df = _elements_from_warehouse()
        if df is not None:
            return df
    return _elements_from_bootstrap(get_bootstrap_data(force_refresh=force_refresh))


def get_teams_df(force_refresh: bool = False) -> pd.DataFrame:
    """All teams (id, name, short_name).

    CHOICE: warehouse (``dim_team``), same reasoning as
    :func:`get_elements_df` -- a pure identity lookup. Live fallback only.
    """
    if not force_refresh:
        df = _teams_from_warehouse()
        if df is not None:
            return df
    data = get_bootstrap_data(force_refresh=force_refresh)
    return pd.DataFrame(data["teams"])


def get_fixtures_df(force_refresh: bool = False) -> pd.DataFrame:
    """All fixtures for the season, with kickoff times and final scores.

    CHOICE: warehouse (``fact_fixture``). The consumer
    (:func:`compute_team_summary`) reads FINISHED fixtures -- results, which
    the engine's fixtures ingest records and which do not change after full
    time -- so the regularly-ingested table is authoritative and a live pull
    adds nothing but staleness ambiguity. Live fallback through the Fetcher,
    cached briefly in ``data/cache/fpl_mcp/``.
    """
    if not force_refresh:
        df = _fixtures_from_warehouse()
        if df is not None:
            return df
    data = None if force_refresh else _cached_json("fixtures.json")
    if data is None:
        data = _live_json("fixtures/")
        _write_cache("fixtures.json", data)
    df = pd.DataFrame(data)
    if "kickoff_time" in df.columns:
        df["kickoff_time"] = pd.to_datetime(df["kickoff_time"], errors="coerce")
    return df


def current_gameweek() -> int:
    """The gameweek in progress (or, pre-season, the next one). Never 0.

    CHOICE: warehouse (``dim_event`` carries every deadline). "Which gameweek
    is it" is a function of the deadline calendar and the clock, both of which
    the warehouse already holds; the live bootstrap's ``is_current`` flag is
    the fallback for a warehouse-less checkout.
    """
    wh = _read_warehouse()
    if wh is not None:
        try:
            with wh:
                rows = wh.sql(f"""
                    WITH e AS ({_latest("dim_event", "season, gw")}),
                         season AS (SELECT max(season) AS season FROM e)
                    SELECT e.gw, e.deadline_utc
                    FROM e JOIN season USING (season) ORDER BY e.gw
                """)
        except Exception:  # noqa: BLE001
            rows = pd.DataFrame()
        if not rows.empty:
            deadlines = pd.to_datetime(rows["deadline_utc"], utc=True)
            started = rows[deadlines <= pd.Timestamp.now(tz="UTC")]
            if not started.empty:
                return int(started.iloc[-1]["gw"])
            return int(rows.iloc[0]["gw"])

    data = get_bootstrap_data()
    events = data.get("events", [])
    for event in events:
        if event.get("is_current"):
            return int(event.get("id"))
    for event in events:
        if event.get("is_next"):
            return int(event.get("id"))
    return 1


# ---------------------------------------------------------------------------
# Entry endpoints: the rivals client, not a third fetch stack


def entry_json(endpoint: str) -> Any:
    """One ``/entry/{id}/...`` fetch under the crawl's politeness rules.

    CHOICE: :class:`fpl_edge.ingest.rivals.client.RivalsFetcher`, because
    entry endpoints are exactly the workload it was built to discipline:
    per-manager fan-out. It brings the enforced minimum interval between
    requests, per-endpoint TTL caching shared with the crawl (a finished
    gameweek's picks are immutable and served from disk), transport-only
    retries -- a 404 here means "deadline not passed" or "no such entry" and
    is returned as ``None``, not retried -- the provenance archive, and a
    hard request budget (:data:`ENTRY_BUDGET_LIMIT` per process) that raises
    ``BudgetExhausted`` with an itemised receipt instead of quietly becoming
    a crawl.

    The fetcher is a module-level singleton so the pacing and the budget span
    every tool call in the process, not just one loop.
    """
    global _entry_fetcher
    if _entry_fetcher is None:
        from fpl_edge.ingest.rivals.client import RequestBudget, RivalsFetcher

        _entry_fetcher = RivalsFetcher(RequestBudget(limit=ENTRY_BUDGET_LIMIT))
    return _entry_fetcher.get_json(endpoint).body


# ---------------------------------------------------------------------------
# Pure helpers over the frames above (no I/O of their own)


def get_team_id_by_name(team_name: str) -> Optional[int]:
    """Return the team ID corresponding to a case-insensitive team name.

    Args:
        team_name: Team name (e.g. "Manchester United" or "MAN UTD").

    Returns:
        The team ID if found, otherwise None.
    """
    teams = get_teams_df()
    normalized = team_name.strip().casefold()
    for _, row in teams.iterrows():
        if row["name"].casefold() == normalized or str(row.get("short_name", "")).casefold() == normalized:
            return int(row["id"])
    for _, row in teams.iterrows():
        if normalized in row["name"].casefold() or normalized in str(row.get("short_name", "")).casefold():
            return int(row["id"])
    return None


def compute_team_summary(team: int, last_n_games: int = 5) -> Dict[str, Any]:
    """Compute a summary of a team's recent performance.

    This examines completed fixtures involving the specified team and
    returns aggregate statistics for the most recent ``last_n_games``.

    Args:
        team: Team ID.
        last_n_games: Number of completed games to include.

    Returns:
        A dictionary with keys ``games``, ``wins``, ``draws``, ``losses``,
        ``goals_scored``, ``goals_conceded`` and ``points``. If the
        team has not played any completed games, the dictionary will
        contain zeros.
    """
    fixtures = get_fixtures_df()
    mask = (
        fixtures["finished"].fillna(False).astype(bool)
        & ((fixtures["team_h"] == team) | (fixtures["team_a"] == team))
    )
    team_fixtures = fixtures[mask].copy()
    if "kickoff_time" in team_fixtures.columns:
        team_fixtures.sort_values(by="kickoff_time", ascending=False, inplace=True)
    else:
        team_fixtures.sort_values(by="event", ascending=False, inplace=True)
    team_fixtures = team_fixtures.head(last_n_games)
    summary = {
        "games": 0,
        "wins": 0,
        "draws": 0,
        "losses": 0,
        "goals_scored": 0,
        "goals_conceded": 0,
        "points": 0,
    }
    for _, row in team_fixtures.iterrows():
        summary["games"] += 1
        if row["team_h"] == team:
            goals_for, goals_against = row.get("team_h_score", 0), row.get("team_a_score", 0)
        else:
            goals_for, goals_against = row.get("team_a_score", 0), row.get("team_h_score", 0)
        summary["goals_scored"] += int(goals_for or 0)
        summary["goals_conceded"] += int(goals_against or 0)
        if goals_for > goals_against:
            summary["wins"] += 1
            summary["points"] += 3
        elif goals_for == goals_against:
            summary["draws"] += 1
            summary["points"] += 1
        else:
            summary["losses"] += 1
    return summary
