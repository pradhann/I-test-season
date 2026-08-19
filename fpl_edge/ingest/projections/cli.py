"""Run the projection ingest.

::

    uv run python -m fpl_edge.ingest.projections.cli ingest --season 2026-27
    uv run python -m fpl_edge.ingest.projections.cli probe
    uv run python -m fpl_edge.ingest.projections.cli report

Deliberately in this package rather than in ``scripts/``: this team owns
``fpl_edge/ingest/projections/**`` and nothing else, and an entry point that
lives outside the directory it belongs to is how two teams end up editing one
file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

import pandas as pd

from fpl_edge.ingest.projections import fplform, livefpl
from fpl_edge.ingest.projections.providers import PROVIDERS, probe_all
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

SEASON = "2026-27"


def element_catalogs(warehouse: Warehouse, as_of: dt.datetime) -> dict[str, set[int]]:
    """Every season's element_id set, so a feed can be attributed to one of them."""
    frame = warehouse.sql(
        "SELECT DISTINCT season, element_id FROM dim_player WHERE as_of <= ?", [as_of]
    )
    return {s: set(g["element_id"].astype(int))
            for s, g in frame.groupby("season", sort=True)}


def element_id_to_code(warehouse: Warehouse, season: str,
                       as_of: dt.datetime) -> dict[int, int]:
    """The FPL API's own mapping, read point-in-time.

    Every provider keys on ``element_id``. Resolving through ``dim_player``
    rather than through the provider's own name strings means a rename, a
    transfer or a duplicate surname cannot move a projection onto the wrong
    player.
    """
    frame = warehouse.snapshot_at(as_of).table(
        "dim_player", where="season = ?", params=[season]
    )
    if frame.empty:
        raise RuntimeError(f"no dim_player rows for {season} at {as_of:%Y-%m-%dT%H:%M:%SZ}")
    return dict(zip(frame["element_id"].astype(int), frame["code"].astype(int)))


def ingest(season: str = SEASON, *, first_gw: int = 1, last_gw: int = 8,
           db: str | None = None) -> dict[str, int]:
    """Fetch every reachable provider and land it in the warehouse."""
    written: dict[str, int] = {}
    with Warehouse(db) if db else Warehouse() as warehouse:
        store = ProjectionStore(warehouse)
        applied = store.migrate()
        if applied:
            print(f"applied migrations: {', '.join(applied)}")

        # -- FPL Form ------------------------------------------------------
        got = fplform.fetch_csv(first_gw=first_gw, last_gw=last_gw)
        warehouse.record_fetch(
            source="projections_fplform", endpoint=fplform.EXPORT_PATH,
            params=f"firstgw={first_gw}&lastgw={last_gw}&all=1",
            fetched_at=got.fetched_at, sha256=got.sha256,
            body_path=str(got.body_path), http_status=got.http_status,
        )
        parsed = fplform.parse_csv(got.body)
        id_to_code = element_id_to_code(warehouse, season, got.fetched_at)
        rows, unresolved = fplform.to_projection_rows(
            parsed, season=season, as_of=got.fetched_at, id_to_code=id_to_code
        )
        written["fplform"] = store.append("fact_projection", rows)
        print(f"fplform: HTTP {got.http_status}, {len(parsed):,} parsed rows "
              f"({parsed['element_id'].nunique()} players x GW{first_gw}-{last_gw}), "
              f"{len(rows):,} resolved, {len(unresolved)} unresolved element_ids, "
              f"{written['fplform']:,} appended")
        if not unresolved.empty:
            print("  unresolved:",
                  unresolved[["element_id", "provider_name", "provider_team"]]
                  .drop_duplicates().head(10).to_dict("records"))

        # -- LiveFPL -------------------------------------------------------
        info = livefpl.fetch("player_info")
        warehouse.record_fetch(
            source="projections_livefpl", endpoint="/planner/all_player_info.json",
            params=None, fetched_at=info.fetched_at, sha256=info.sha256,
            body_path=str(info.body_path), http_status=info.http_status,
        )
        provider_map = livefpl.parse_code_map(info.body)
        agree = sum(1 for k, v in provider_map.items() if id_to_code.get(k) == v)
        print(f"livefpl: code map {len(provider_map)} entries, {agree} agree with "
              f"dim_player, {len(provider_map) - agree} differ")

        catalogs = element_catalogs(warehouse, info.fetched_at)
        total = 0
        for kind in ("predicted_eo", "top10k", "elite"):
            got_own = livefpl.fetch(kind, gw=first_gw)
            warehouse.record_fetch(
                source="projections_livefpl", endpoint=livefpl._path(kind, first_gw),
                params=None, fetched_at=got_own.fetched_at, sha256=got_own.sha256,
                body_path=str(got_own.body_path), http_status=got_own.http_status,
            )
            parsed_own = livefpl.parse_ownership(got_own.body, kind)
            # Which season's element_ids is this file keyed on? Asked of the data,
            # never assumed: top10k.json and elite.json still described 2025-26
            # when predictedEOs/1.json had already rolled over to 2026-27.
            file_season = livefpl.infer_season(
                set(parsed_own["element_id"].astype(int)), catalogs
            )
            file_gw = first_gw if file_season == season else _last_finished_gw(
                warehouse, file_season, got_own.fetched_at
            )
            file_map = (id_to_code if file_season == season
                        else element_id_to_code(warehouse, file_season, got_own.fetched_at))
            own_rows, own_unres = livefpl.to_ownership_rows(
                parsed_own, kind=kind, season=file_season, gw=file_gw,
                as_of=got_own.fetched_at, id_to_code=file_map,
            )
            n = store.append("fact_external_ownership", own_rows)
            total += n
            flag = "" if file_season == season else "  <- NOT the current season"
            print(f"  {kind}: HTTP {got_own.http_status}, {len(parsed_own)} entries, "
                  f"season={file_season} gw={file_gw}, {len(own_rows)} resolved, "
                  f"{len(own_unres)} unresolved, {n} appended{flag}")
        written["livefpl"] = total
    return written


def _last_finished_gw(warehouse: Warehouse, season: str, as_of: dt.datetime) -> int:
    """The last gameweek of ``season`` with results visible at ``as_of``."""
    frame = warehouse.sql(
        "SELECT max(gw) AS gw FROM fact_player_fixture WHERE season = ? AND as_of <= ?",
        [season, as_of],
    )
    gw = frame.iloc[0]["gw"]
    if pd.isna(gw):
        raise RuntimeError(f"no finished gameweek for {season} at {as_of}")
    return int(gw)


def probe() -> None:
    results = probe_all()
    width = max(len(r.url) for r in results)
    for r in results:
        status = r.error or f"{r.status} {r.bytes_:,}B {r.content_type[:30]}"
        print(f"{r.provider:20} {r.url:<{width}} robots={str(r.robots_allows):5} {status}")


def report() -> None:
    rows = [{
        "provider": p.name, "verdict": p.verdict,
        "2026-27": {True: "yes", False: "no", None: "?"}[p.covers_2026_27],
        "interface": p.interface.split(".")[0][:48], "cost": p.cost.split(".")[0][:28],
    } for p in PROVIDERS]
    print(pd.DataFrame(rows).to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["ingest", "probe", "report"])
    parser.add_argument("--season", default=SEASON)
    parser.add_argument("--first-gw", type=int, default=1)
    parser.add_argument("--last-gw", type=int, default=8)
    parser.add_argument("--db", default=None)
    args = parser.parse_args(argv)
    if args.command == "probe":
        probe()
    elif args.command == "report":
        report()
    else:
        ingest(args.season, first_gw=args.first_gw, last_gw=args.last_gw, db=args.db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
