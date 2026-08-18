"""Populate the warehouse from the live FPL API."""

from __future__ import annotations

from fpl_edge.ingest.fpl_api import BASE, ingest_bootstrap, ingest_fixtures, season_label
from fpl_edge.ingest.http import Fetcher
from fpl_edge.store import Warehouse


def main() -> None:
    with Warehouse() as wh, Fetcher("fpl_api", base_url=BASE) as fetcher:
        bs = ingest_bootstrap(wh, fetcher)
        season = season_label(fetcher.get_json("bootstrap-static/").body)
        fx = ingest_fixtures(wh, fetcher, season=season)
        for table, n in {**bs, **fx}.items():
            print(f"  {table:34s} {n:>6}")
        print(f"  season                             {season}")


if __name__ == "__main__":
    main()
