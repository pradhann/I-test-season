"""FPL-Core-Insights: third-party per-player per-match stats, xG included.

Source: https://github.com/olbauday/FPL-Core-Insights -- the data engine behind
fplcore.com. CSVs on ``raw.githubusercontent.com``, refreshed twice daily at
07:30 and 17:30 UTC, covering 2024-25 onwards including the current 2026-27
season. Measured live 2026-08-24: repo pushed_at 2026-08-23T23:47Z, GW1
``playermatchstats.csv`` HTTP 200 with 313 player-match rows across 8 matches.

Why this source earns a table of its own
----------------------------------------
Nothing else in the warehouse carries a PER-MATCH xG opinion. The official API
gives per-fixture ``expected_goals`` only after points settle, and every
projection provider publishes a *forecast*; this repo publishes an Opta-like
*read of the match that happened* -- xG, xGOT, shots, chances created, the
defensive actions that score under the 2026-27 DEFCON rule -- per player, per
match, including cups and friendlies the FPL API does not score at all. That is
a genuinely different kind of fact, so it lands in
``fact_player_match_stats``, not in ``fact_projection`` and not merged into
``fact_player_fixture`` where it could be mistaken for the official numbers.

Copied, never modelled: every number is the publisher's. Blank cells stay NULL.

Licensing, verbatim
-------------------
The repo has NO LICENSE FILE. Its README says: "Feel free to use the data from
this repository in whatever way works best for you -- whether for your website,
blog posts, or other projects. If possible, I'd greatly appreciate it if you
could include a link back to this repository as the data source." That is an
explicit informal grant with an attribution request, but it is not a licence,
and the underlying match statistics are derived from data whose ultimate
owners (the Premier League and its data providers) granted nothing. Rows land
in a private warehouse for one manager's own decisions and are never
republished. The ambiguity is recorded in docs/data_sources.md.

Identity
--------
The repo carries its own ``players.csv`` mapping its per-season ``player_id``
(the official FPL element id -- verified: all ids resolve against
``dim_player`` for 2026-27) to ``player_code``, FPL's cross-season stable
code. We resolve through THEIR map, then validate every resulting code against
``dim_player`` at the fetch instant; a code the snapshot has never seen is
dropped and counted, never guessed.

``match_id`` is the publisher's own slug ('26-27-prem-arsenal-vs-coventry-city')
and is stored verbatim. Remapping it onto ``fact_fixture.fixture_id`` is a
join someone can compute later; collapsing it at write time would destroy the
publisher's key for the sake of an opinion.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import time
from urllib.parse import quote

import pandas as pd

from fpl_edge.ingest.http import RAW_ROOT, USER_AGENT, Fetched
from fpl_edge.ingest.projections.robots import require_allowed
from fpl_edge.store import Warehouse

SOURCE = "fpl_core_insights"
REPO = "olbauday/FPL-Core-Insights"
REF = "main"
RAW_BASE = "https://raw.githubusercontent.com"

#: Self-imposed floor between requests. The per-GW files are ~60KB; a full
#: season pass is 38 requests, and one second between them keeps that polite.
POLITE_DELAY_S = 1.0

#: The only tournament ingested for now. Cups and friendlies exist in the repo
#: under the same layout and can be added as separate runs later; the Premier
#: League files are the ones every FPL decision needs.
TOURNAMENT = "Premier League"

#: Publisher CSV column -> our table column. Names are kept identical on
#: purpose (the publisher's vocabulary IS the provenance); this mapping exists
#: so the required-column check and the row shaping cannot drift apart.
STAT_COLUMNS: tuple[str, ...] = (
    "minutes_played", "start_min", "finish_min",
    "goals", "assists", "penalties_scored", "penalties_missed",
    "total_shots", "shots_on_target", "xg", "xa", "xgot",
    "chances_created", "touches_opposition_box",
    "tackles", "tackles_won", "interceptions", "recoveries",
    "blocks", "clearances", "defensive_contributions",
    "saves", "goals_conceded", "xgot_faced", "goals_prevented",
)


class FplCoreInsightsError(RuntimeError):
    """The repo's files are not the shape this module knows how to read."""


def season_dir(season: str) -> str:
    """``'2026-27'`` -> the repo's ``'2026-2027'`` directory name."""
    start, end2 = season.split("-")
    return f"{start}-{int(start[:2])}{end2}"


def players_path(season: str) -> str:
    return f"data/{season_dir(season)}/players.csv"


def playermatchstats_path(season: str, gw: int,
                          tournament: str = TOURNAMENT) -> str:
    return (f"data/{season_dir(season)}/By Tournament/{tournament}/"
            f"GW{gw}/playermatchstats.csv")


def _raw_url(path: str) -> str:
    # The repo's paths contain spaces ('By Tournament'); quote the path but
    # keep the slashes that structure it.
    return f"{RAW_BASE}/{REPO}/{REF}/{quote(path)}"


def fetch(path: str, *, timeout: float = 60.0,
          delay_s: float = POLITE_DELAY_S) -> Fetched:
    """Fetch one repo file, archiving the exact bytes under ``data/raw/``."""
    import httpx

    url = _raw_url(path)
    require_allowed(url)
    time.sleep(delay_s)
    fetched_at = dt.datetime.now(dt.timezone.utc)
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        resp = client.get(url)
    if resp.status_code != 200:
        raise FplCoreInsightsError(f"{url} returned HTTP {resp.status_code}")
    payload = resp.content
    digest = hashlib.sha256(payload).hexdigest()
    out_dir = RAW_ROOT / SOURCE
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = path.replace("/", "_").replace(" ", "-").rsplit(".", 1)[0]
    dest = out_dir / f"{stem}_{fetched_at:%Y%m%dT%H%M%SZ}_{digest[:8]}.csv"
    if not dest.exists():
        dest.write_bytes(payload)
    return Fetched(body=resp.text, fetched_at=fetched_at, sha256=digest,
                   body_path=dest, http_status=resp.status_code,
                   from_cache=False)


def parse_players(text: str) -> pd.DataFrame:
    """The repo's own element-id -> stable-code map, checked for shape."""
    frame = pd.read_csv(io.StringIO(text))
    needed = {"player_code", "player_id"}
    missing = needed - set(frame.columns)
    if missing:
        raise FplCoreInsightsError(
            f"players.csv is missing {sorted(missing)}; columns present: "
            f"{list(frame.columns)}. The publisher has changed schema."
        )
    if frame.empty:
        raise FplCoreInsightsError("players.csv parsed to zero rows")
    dup = frame["player_id"].duplicated()
    if dup.any():
        raise FplCoreInsightsError(
            f"players.csv maps {int(dup.sum())} player_id(s) twice; the map "
            f"is ambiguous and resolving through it would be a coin flip."
        )
    return frame


def parse_playermatchstats(text: str) -> pd.DataFrame:
    """One gameweek's player-match rows.

    ZERO ROWS IS VALID: the repo pre-creates every gameweek's file, so a
    header-only file means 'not played / not processed yet', which is an
    absence and not an error. A missing COLUMN is schema drift and raises.
    """
    frame = pd.read_csv(io.StringIO(text))
    needed = {"player_id", "match_id", *STAT_COLUMNS}
    missing = needed - set(frame.columns)
    if missing:
        raise FplCoreInsightsError(
            f"playermatchstats.csv is missing {sorted(missing)}; columns "
            f"present: {list(frame.columns)}. The publisher has changed "
            f"schema; re-read the file against STAT_COLUMNS rather than "
            f"filling the gap with nulls."
        )
    return frame


def to_stat_rows(
    pms: pd.DataFrame,
    players: pd.DataFrame,
    *,
    season: str,
    gw: int,
    as_of: dt.datetime,
    valid_codes: set[int],
    tournament: str = TOURNAMENT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve identities and shape rows for ``fact_player_match_stats``.

    Returns ``(rows, unresolved)``. Unresolved ids are never written and never
    silently dropped: they come back so a mapping gap is a number in the run
    log, not a quietly shorter table.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    if pms.empty:
        empty = pd.DataFrame(columns=["player_id", "match_id", "reason"])
        return pd.DataFrame(), empty

    id_to_code = dict(zip(players["player_id"].astype(int),
                          players["player_code"].astype(int)))
    raw_id = pd.to_numeric(pms["player_id"], errors="coerce")
    code = raw_id.map(lambda v: id_to_code.get(int(v)) if pd.notna(v) else None)
    # Validate the publisher's code against OUR dim_player: their map is how a
    # row finds its code, our snapshot is what makes that code a real player.
    # pandas renders the misses above as NaN, not None, so test with notna.
    code = code.map(lambda c: c if pd.notna(c) and int(c) in valid_codes else None)

    frame = pms.assign(code=code)
    bad = frame["code"].isna() | frame["match_id"].isna()
    unresolved = frame.loc[bad, ["player_id", "match_id"]].copy()
    if not unresolved.empty:
        unresolved["reason"] = "player_id not resolvable to a dim_player code"
        unresolved.loc[frame.loc[bad, "match_id"].isna(),
                       "reason"] = "row has no match_id"
    keep = frame.loc[~bad]

    rows = pd.DataFrame({
        "source": SOURCE,
        "season": season,
        "code": keep["code"].astype(int),
        "match_id": keep["match_id"].astype(str),
        "tournament": tournament,
        "gw": gw,
    })
    for col in STAT_COLUMNS:
        rows[col] = pd.to_numeric(keep[col], errors="coerce").astype("float64").values
    rows["as_of"] = pd.to_datetime([as_of] * len(rows), utc=True)
    rows = rows.reset_index(drop=True)

    # One publisher, one row per player per match. A duplicated key inside one
    # file is the publisher contradicting itself; writing either would be a
    # coin flip, so both go to unresolved.
    dup = rows.duplicated(["code", "match_id"], keep=False)
    if dup.any():
        clashed = rows.loc[dup, ["match_id"]].copy()
        clashed["player_id"] = pd.NA
        clashed["reason"] = "duplicate (code, match_id) within one file"
        unresolved = pd.concat(
            [unresolved, clashed[["player_id", "match_id", "reason"]]],
            ignore_index=True,
        )
        rows = rows.loc[~dup].reset_index(drop=True)

    return rows, unresolved.reset_index(drop=True)


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def _started_gws(warehouse: Warehouse, season: str, as_of: dt.datetime) -> list[int]:
    """Gameweeks whose deadline has passed -- the only ones that can have data."""
    frame = warehouse.sql(
        "SELECT DISTINCT gw FROM dim_event WHERE season = ? AND deadline_utc <= ? "
        "AND as_of <= ? ORDER BY gw",
        [season, as_of, as_of],
    )
    return [int(g) for g in frame["gw"]]


def ingest(season: str = "2026-27", *, db: str | None = None,
           gws: list[int] | None = None) -> int:
    """Fetch the repo's Premier League player-match stats and land them.

    ``gws`` defaults to every gameweek whose deadline has passed: a file for a
    future gameweek exists but is header-only, and fetching it would archive
    bytes that say nothing.
    """
    total = 0
    with Warehouse(db) if db else Warehouse() as warehouse:
        got_players = fetch(players_path(season))
        warehouse.record_fetch(
            source=SOURCE, endpoint=players_path(season), params=None,
            fetched_at=got_players.fetched_at, sha256=got_players.sha256,
            body_path=str(got_players.body_path),
            http_status=got_players.http_status,
        )
        players = parse_players(got_players.body)

        snap = warehouse.snapshot_at(got_players.fetched_at)
        valid_codes = set(
            snap.table("dim_player", where="season = ?", params=[season])
            ["code"].astype(int)
        )
        if not valid_codes:
            raise RuntimeError(f"no dim_player rows for {season}; run the "
                               f"bootstrap ingest first")

        run_gws = gws if gws is not None else _started_gws(
            warehouse, season, got_players.fetched_at
        )
        for gw in run_gws:
            path = playermatchstats_path(season, gw)
            got = fetch(path)
            warehouse.record_fetch(
                source=SOURCE, endpoint=path, params=f"gw={gw}",
                fetched_at=got.fetched_at, sha256=got.sha256,
                body_path=str(got.body_path), http_status=got.http_status,
            )
            pms = parse_playermatchstats(got.body)
            if pms.empty:
                print(f"  GW{gw}: file present, no processed matches yet")
                continue
            rows, unresolved = to_stat_rows(
                pms, players, season=season, gw=gw,
                as_of=got.fetched_at, valid_codes=valid_codes,
            )
            n = warehouse.append("fact_player_match_stats", rows)
            total += n
            note = ""
            if not unresolved.empty:
                note = (f", {len(unresolved)} unresolved: "
                        f"{unresolved.head(5).to_dict('records')}")
            print(f"  GW{gw}: {len(pms)} parsed, {n} appended, "
                  f"{rows['match_id'].nunique()} matches, "
                  f"{int(rows['xg'].notna().sum())} with xg{note}")
    print(f"{SOURCE}: {total} rows appended")
    return total


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="2026-27")
    parser.add_argument("--db", default=None)
    parser.add_argument("--gws", default="",
                        help="comma-separated gameweeks; default is every "
                             "gameweek whose deadline has passed")
    args = parser.parse_args(argv)
    gws = [int(g) for g in args.gws.split(",") if g] or None
    ingest(args.season, db=args.db, gws=gws)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
