"""On-demand Understat player profiles: fetch, strict-resolve, cache append-only.

This is the ONE sanctioned fetch path for Understat data (CHAT_ARCHITECTURE §6).
Panels never call anything in this module -- they read the tables it writes and
report absence honestly. The chat tool and ``POST /api/players/{code}/
fetch_profile`` call :func:`fetch_player_profile` on first demand; every read
after that is warehouse-local.

The real shape, verified 2026-08-31 against Erling Haaland (understat id 8260)
and pinned in ``tests/fixtures/understat/``:

* The player PAGE (``/player/{id}``) no longer embeds ``matchesData`` in a
  script tag as the folklore says. It embeds only
  ``var player = JSON.parse('{"id":"8260","name":"Erling Haaland"}')`` and its
  ``player.min.js`` loads everything from
  ``GET https://understat.com/getPlayerData/{id}`` -- gzip JSON with keys
  ``player / matches / groups / positionsList / minMaxPlayerStats / shots /
  lastMatch``. Every numeric field arrives as a STRING (``"xG":
  "0.6867040395736694"``) and must be coerced.
* Per-match rows carry ``goals, shots, xG, time, position, h_team, a_team,
  h_goals, a_goals, date, id, season, roster_id, xA, assists, key_passes, npg,
  npxG, xGChain, xGBuildup`` across ALL seasons of the player's career;
  Understat labels a season by its starting year, so our ``"2026-27"`` is
  their ``"2026"``.
* Name search is ``GET https://understat.com/main/getPlayersName/{query}`` ->
  ``{"response": {"success": true, "players": [{"id", "player", "team"}]}}``.

Name resolution is exact-then-containment over :func:`fpl_edge.ingest.rivals.
names.norm` -- the repo's ONE name matcher -- and **never edit distance**. The
counter-examples that rule exists for are in this repo's own data
(``ingest/content/clubs.py``: "forester" -> Brentford at d=6; "hull" TIED
between Fulham and Hull City at d=4). A name this module cannot place is
REFUSED with the candidates listed; a wrong profile written under a real
player's code is a fabrication, a refusal is one honest fetch button.

Storage follows ``ingest/projections/store.py``: this module owns its own DDL
(``understat_migrations/*.sql``) and its own append path rather than mutating
``store.PIT_KEYS``. ``as_of`` is the FETCH INSTANT; Understat re-runs its model
and revises xG after the fact, so a re-fetch that disagrees with a stored row
is a new fact at a later ``as_of``, never an overwrite.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import Fetcher
from fpl_edge.ingest.rivals.names import norm as _name_norm
from fpl_edge.store.warehouse import DEFAULT_DB, Warehouse

UTC = dt.timezone.utc

UNDERSTAT_BASE = "https://understat.com"

MIGRATIONS_DIR = Path(__file__).parent / "understat_migrations"

#: Entity keys per table, excluding ``as_of``. Same shape as
#: ``projections.store.PROJECTION_KEYS`` and owned here for the same reasons.
UNDERSTAT_KEYS: dict[str, tuple[str, ...]] = {
    # One logical entity is one player in one Understat match. ``match_id`` is
    # THEIR match key on purpose (mirrors fact_player_match_stats' rationale in
    # store.PIT_KEYS): the same real-world match seen by two sources is two
    # entities, and no join to our fixture ids is smuggled in at write time.
    "understat_player_match": ("understat_id", "season", "match_id"),
    # One mapping per player code. Append-only: a corrected mapping is a new
    # row at a later as_of, and reads take the latest.
    "understat_player_map": ("code",),
}


class UnderstatError(RuntimeError):
    """Anything this ingest could not do, said plainly."""


class UnresolvedPlayerError(UnderstatError):
    """The strict resolver refused to place a name. Carries the candidates.

    This is the resolver WORKING, not failing: every candidate Understat
    offered is listed so a human (or the owner in chat) can decide, and
    nothing was guessed or written.
    """

    def __init__(self, message: str, candidates: list[dict[str, str]]) -> None:
        super().__init__(message)
        self.candidates = candidates


class ConflictingUnderstatError(ValueError):
    """Two different values claim the same entity at the same instant.

    Mirrors ``store.ConflictingFactError``. Understat revising a number is a
    re-fetch at a later ``as_of``; identical keys AND identical as_of with
    different payloads is a bug in the caller, never silently resolvable.
    """


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


def understat_season(season: str) -> str:
    """Our ``"2026-27"`` label -> Understat's starting-year ``"2026"``."""
    m = re.fullmatch(r"(\d{4})-\d{2}", season)
    if not m:
        raise ValueError(
            f"season must look like '2026-27', got {season!r}; refusing to guess"
        )
    return m.group(1)


# ---------------------------------------------------------------------------
# parsing -- pinned against tests/fixtures/understat/player_8260_playerdata.json
# ---------------------------------------------------------------------------


def parse_player_matches(
    payload: dict[str, Any],
    *,
    code: int,
    season: str,
    as_of: dt.datetime,
) -> pd.DataFrame:
    """``getPlayerData`` JSON -> our per-match rows for ONE of our seasons.

    Only the ``matches`` list is consumed: it already carries the per-match
    aggregates the FPL lens wants (shots, xG, xA, key passes, minutes), so the
    694-row shot list is left with Understat. Every numeric field is a string
    in the source and coerced here; a row that will not coerce is a hard error,
    not a skipped row -- a silently shorter season reads as a rotation risk.
    """
    as_of = _require_utc(as_of, "as_of")
    player = payload.get("player") or {}
    if "id" not in player:
        raise UnderstatError(
            "getPlayerData payload has no player.id; the endpoint shape has "
            "changed -- re-verify against a live page before trusting a parse."
        )
    understat_id = int(player["id"])
    u_season = understat_season(season)
    matches = payload.get("matches")
    if not isinstance(matches, list):
        raise UnderstatError(
            "getPlayerData payload has no matches list; the endpoint shape has "
            "changed -- re-verify against a live page before trusting a parse."
        )
    rows: list[dict[str, Any]] = []
    for m in matches:
        if str(m.get("season")) != u_season:
            continue
        try:
            rows.append({
                "understat_id": understat_id,
                "code": int(code),
                "season": season,
                "match_id": int(m["id"]),
                "date": dt.date.fromisoformat(str(m["date"])),
                "minutes": int(m["time"]),
                "shots": int(m["shots"]),
                "goals": int(m["goals"]),
                "assists": int(m["assists"]),
                "key_passes": int(m["key_passes"]),
                "npg": int(m["npg"]),
                "xg": float(m["xG"]),
                "xa": float(m["xA"]),
                "npxg": float(m["npxG"]),
                "position": str(m.get("position") or ""),
                "h_team": str(m.get("h_team") or ""),
                "a_team": str(m.get("a_team") or ""),
                "h_goals": int(m["h_goals"]),
                "a_goals": int(m["a_goals"]),
                "as_of": as_of,
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise UnderstatError(
                f"unparseable Understat match row {m.get('id')!r} for "
                f"understat_id {understat_id}: {type(exc).__name__}: {exc}"
            ) from exc
    return pd.DataFrame(rows)


def parse_search_players(payload: dict[str, Any]) -> list[dict[str, str]]:
    """``main/getPlayersName`` JSON -> ``[{id, player, team}]``, verbatim."""
    resp = payload.get("response") or {}
    if not resp.get("success"):
        return []
    out = []
    for p in resp.get("players") or []:
        if "id" in p and "player" in p:
            out.append({"id": str(p["id"]), "player": str(p["player"]),
                        "team": str(p.get("team") or "")})
    return out


# ---------------------------------------------------------------------------
# strict name resolution -- exact, then containment, NEVER edit distance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPlayer:
    understat_id: int
    understat_name: str
    understat_team: str
    basis: str  # 'exact' | 'containment', plus which of our names matched


def _tokens(name: str) -> list[str]:
    """names.norm plus punctuation-fold, same recipe as _resolve_call_name."""
    return re.sub(r"[^a-z0-9 ]", " ", _name_norm(name)).split()


def _contained(a: list[str], b: list[str]) -> bool:
    """Token-sequence containment either way: 'ezri konsa' ⊂ 'ezri konsa ngoyo'."""
    if not a or not b:
        return False
    sa, sb = f" {' '.join(a)} ", f" {' '.join(b)} "
    return sa in sb or sb in sa


def resolve_understat_player(
    candidates: list[dict[str, str]],
    *,
    web_name: str,
    first_name: str | None,
    second_name: str | None,
) -> ResolvedPlayer:
    """Place OUR player among Understat's candidates, or refuse loudly.

    Two tiers, in order, each requiring a UNIQUE winner:

    1. **exact** -- a candidate whose normalised name equals our full name
       (``first second``), our ``second_name`` alone, or our ``web_name``.
    2. **containment** -- one normalised token sequence written inside the
       other ("Konsa" inside "Ezri Konsa"; "William Osula" containing
       "Osula").

    There is no third tier. "Cristian" for "Cristhian" needs a typo forgiven
    and is refused -- an edit-distance pass would accept it, and the same pass
    accepts "forester" -> Brentford. Refusal raises
    :class:`UnresolvedPlayerError` carrying every candidate, so the caller can
    show them instead of guessing.
    """
    ours: list[list[str]] = []
    full = " ".join(x for x in [first_name or "", second_name or ""] if x).strip()
    for name in (full, second_name or "", web_name):
        toks = _tokens(name)
        if toks and toks not in ours:
            ours.append(toks)
    if not ours:
        raise UnresolvedPlayerError("our player has no usable name", candidates)

    def _refuse(why: str) -> UnresolvedPlayerError:
        listing = "; ".join(
            f"{c['player']} ({c['team']}, understat id {c['id']})" for c in candidates
        ) or "(none)"
        return UnresolvedPlayerError(
            f"cannot place {web_name!r} on Understat: {why}. "
            f"Candidates offered: {listing}. Nothing was written.",
            candidates,
        )

    cand_tokens = [(c, _tokens(c["player"])) for c in candidates]

    exact = [c for c, toks in cand_tokens if any(toks == o for o in ours)]
    if len(exact) == 1:
        c = exact[0]
        return ResolvedPlayer(int(c["id"]), c["player"], c["team"], "exact")
    if len(exact) > 1:
        raise _refuse(f"{len(exact)} candidates match exactly")

    contained = [c for c, toks in cand_tokens if any(_contained(toks, o) for o in ours)]
    if len(contained) == 1:
        c = contained[0]
        return ResolvedPlayer(int(c["id"]), c["player"], c["team"], "containment")
    if len(contained) > 1:
        raise _refuse(f"{len(contained)} candidates match by containment")
    raise _refuse("no candidate matches exactly or by containment")


# ---------------------------------------------------------------------------
# storage -- migrations, idempotent append, as-of reads
# ---------------------------------------------------------------------------


class UnderstatStore:
    """Migrations, appends and as-of reads for the two understat tables.

    The clone of ``ProjectionStore``, deliberately: same shared
    ``schema_migration`` registry (version stems are prefixed ``*_understat``
    so they cannot collide), same duplicate-skip, same contradiction refusal.
    """

    def __init__(self, warehouse: Warehouse) -> None:
        self.wh = warehouse
        self.applied_migrations: list[str] = self.migrate()

    def migrate(self) -> list[str]:
        self.wh.sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                version VARCHAR PRIMARY KEY,
                applied_utc TIMESTAMPTZ NOT NULL,
                sha256 VARCHAR NOT NULL
            )
            """
        )
        applied = set(
            self.wh.sql("SELECT version FROM schema_migration")["version"].astype(str)
        )
        run: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in applied:
                continue
            body = path.read_text()
            self.wh.sql(body)
            self.wh.sql(
                "INSERT INTO schema_migration VALUES (?, ?, ?)",
                [path.stem, dt.datetime.now(UTC),
                 hashlib.sha256(body.encode("utf-8")).hexdigest()],
            )
            run.append(path.stem)
        return run

    def append(self, table: str, df: pd.DataFrame) -> int:
        """Append rows, skipping exact duplicates, refusing contradictions."""
        if table not in UNDERSTAT_KEYS:
            raise KeyError(f"unknown understat table {table!r}")
        if df.empty:
            return 0
        if "as_of" not in df.columns:
            raise ValueError(f"{table}: every row must carry as_of")
        if df["as_of"].isna().any():
            raise ValueError(f"{table}: as_of contains nulls")
        raw = pd.to_datetime(df["as_of"])
        if getattr(raw.dtype, "tz", None) is None:
            raise ValueError(
                f"{table}: as_of must be timezone-aware UTC; got naive timestamps. "
                "Localise explicitly rather than letting pandas assume UTC."
            )
        df = df.assign(as_of=raw.dt.tz_convert("UTC")).drop_duplicates()

        keys = [*UNDERSTAT_KEYS[table], "as_of"]
        payload = [c for c in df.columns if c not in keys]
        self.wh.sql("SET TimeZone='UTC'")
        con = self.wh._con  # noqa: SLF001 -- this class is the writer for these tables
        con.register("_incoming_us", df)
        try:
            on = " AND ".join(f"t.{k} IS NOT DISTINCT FROM i.{k}" for k in keys)
            if payload:
                differs = " OR ".join(f"t.{c} IS DISTINCT FROM i.{c}" for c in payload)
                clash = con.execute(
                    f"SELECT count(*) FROM {table} t JOIN _incoming_us i ON {on} "
                    f"WHERE {differs}"
                ).fetchone()[0]
                if clash:
                    sample = con.execute(
                        f"SELECT {', '.join('i.' + k for k in keys)} FROM {table} t "
                        f"JOIN _incoming_us i ON {on} WHERE {differs} LIMIT 3"
                    ).df()
                    raise ConflictingUnderstatError(
                        f"{table}: {clash} incoming row(s) contradict stored values "
                        f"at the same as_of. A revised number needs a later as_of.\n"
                        f"{sample}"
                    )
            before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            cols = ", ".join(df.columns)
            con.execute(
                f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming_us i "
                f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {on})"
            )
            after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.unregister("_incoming_us")
        return int(after - before)

    def as_of(
        self,
        table: str,
        instant: dt.datetime,
        *,
        where: str | None = None,
        params: list[object] | None = None,
    ) -> pd.DataFrame:
        """Latest row per entity with ``as_of <= instant``. The only read path."""
        if table not in UNDERSTAT_KEYS:
            raise KeyError(f"unknown understat table {table!r}")
        instant = _require_utc(instant, "instant")
        keys = ", ".join(UNDERSTAT_KEYS[table])
        clause = f"AND ({where})" if where else ""
        return self.wh.sql(
            f"""
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY as_of DESC) rn
                FROM {table} WHERE as_of <= ? {clause}
            ) WHERE rn = 1 ORDER BY {keys}
            """,
            [instant, *(params or [])],
        )


# ---------------------------------------------------------------------------
# the one sanctioned fetch path
# ---------------------------------------------------------------------------


def _network_allowed() -> None:
    if os.environ.get("FPL_EDGE_DISABLE_NETWORK_INGEST", "") not in ("", "0"):
        raise UnderstatError(
            "network ingest is disabled (FPL_EDGE_DISABLE_NETWORK_INGEST is "
            "set); the Understat fetch was not attempted."
        )


def _fetcher() -> Fetcher:
    # X-Requested-With mirrors what player.min.js sends; the identified
    # User-Agent comes from http.Fetcher and is never overridden.
    return Fetcher("understat", base_url=UNDERSTAT_BASE,
                   headers={"X-Requested-With": "XMLHttpRequest"})


def _player_names(db: Path, code: int, season: str) -> dict[str, Any]:
    wh = Warehouse.read_copy(db)
    try:
        df = wh.sql(
            "SELECT web_name, first_name, second_name FROM dim_player "
            "WHERE season = ? AND code = ? "
            "QUALIFY ROW_NUMBER() OVER (ORDER BY as_of DESC) = 1",
            [season, int(code)],
        )
    finally:
        wh.close()
    if df.empty:
        raise UnderstatError(
            f"no player with code {code} in dim_player for {season}; "
            "nothing to resolve against, nothing fetched."
        )
    r = df.iloc[0]
    return {
        "web_name": str(r["web_name"]),
        "first_name": None if r["first_name"] is None else str(r["first_name"]),
        "second_name": None if r["second_name"] is None else str(r["second_name"]),
    }


def _search_queries(names: dict[str, Any]) -> list[str]:
    """Deterministic search order: family name, web_name, full name."""
    out: list[str] = []
    for q in (names.get("second_name"), names.get("web_name"),
              " ".join(x for x in [names.get("first_name") or "",
                                   names.get("second_name") or ""] if x)):
        q = (q or "").strip()
        if len(q) >= 3 and q not in out:
            out.append(q)
    return out


def fetch_player_profile(
    code: int,
    season: str = "2026-27",
    *,
    db: Path | str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Resolve, fetch and store one player's current-season Understat matches.

    One search request (two more only if the first returns nothing) plus one
    ``getPlayerData`` request -- never a bulk crawl. Returns a summary dict
    ``{code, understat_id, understat_name, resolved_basis, rows_appended,
    rows_total, as_of}``. Raises :class:`UnresolvedPlayerError` with the
    candidate list when the strict resolver refuses, and never writes anything
    in that case.
    """
    _network_allowed()
    db_path = Path(db) if db is not None else DEFAULT_DB
    as_of = _require_utc(now or dt.datetime.now(UTC), "now")
    names = _player_names(db_path, code, season)

    # -- resolve (map cache first; the network is a last resort) -------------
    wh = Warehouse.read_copy(db_path)
    try:
        store_ro = UnderstatStore(wh)
        cached = store_ro.as_of("understat_player_map", as_of,
                                where="code = ?", params=[int(code)])
    finally:
        wh.close()

    resolved: ResolvedPlayer | None = None
    if not cached.empty:
        r = cached.iloc[0]
        resolved = ResolvedPlayer(int(r["understat_id"]), str(r["understat_name"]),
                                  str(r["understat_team"]), str(r["resolved_basis"]))

    with _fetcher() as http:
        if resolved is None:
            candidates: list[dict[str, str]] = []
            for query in _search_queries(names):
                fetched = http.get_json(f"main/getPlayersName/{query}")
                candidates = parse_search_players(fetched.body)
                if candidates:
                    break
            resolved = resolve_understat_player(
                candidates,
                web_name=names["web_name"],
                first_name=names["first_name"],
                second_name=names["second_name"],
            )
        data = http.get_json(f"getPlayerData/{resolved.understat_id}")

    rows = parse_player_matches(data.body, code=code, season=season, as_of=as_of)

    # -- store (writer lock held only for the append) ------------------------
    wh = Warehouse(db_path)
    try:
        store = UnderstatStore(wh)
        if cached.empty:
            store.append("understat_player_map", pd.DataFrame([{
                "code": int(code),
                "understat_id": resolved.understat_id,
                "understat_name": resolved.understat_name,
                "understat_team": resolved.understat_team,
                "resolved_basis": resolved.basis,
                "as_of": as_of,
            }]))
        appended = store.append("understat_player_match", rows)
        total = int(wh.sql(
            "SELECT count(DISTINCT match_id) AS n FROM understat_player_match "
            "WHERE code = ? AND season = ?", [int(code), season],
        ).iloc[0]["n"])
    finally:
        wh.close()

    return {
        "code": int(code),
        "season": season,
        "understat_id": resolved.understat_id,
        "understat_name": resolved.understat_name,
        "resolved_basis": resolved.basis,
        "rows_appended": int(appended),
        "rows_total": total,
        "as_of": as_of.isoformat(),
    }
