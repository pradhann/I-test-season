"""Populate the warehouse from the live FPL API."""

from __future__ import annotations

import argparse

from fpl_edge.ingest.fpl_api import BASE, ingest_bootstrap, ingest_fixtures, season_label
from fpl_edge.ingest.http import Fetcher
from fpl_edge.store import DEFAULT_DB, Warehouse


def main(argv: list[str] | None = None) -> None:
    # --db is explicit rather than implied by the default, because this script
    # is a settlement step and the chain that runs it already knows which
    # database it is settling. A bare Warehouse() writes to DEFAULT_DB
    # whatever the caller meant.
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"warehouse to write (default {DEFAULT_DB})")
    args = ap.parse_args(argv)
    with Warehouse(args.db) as wh, Fetcher("fpl_api", base_url=BASE) as fetcher:
        bs = ingest_bootstrap(wh, fetcher)
        season = season_label(fetcher.get_json("bootstrap-static/").body)
        fx = ingest_fixtures(wh, fetcher, season=season)
        for table, n in {**bs, **fx}.items():
            print(f"  {table:34s} {n:>6}")
        print(f"  season                             {season}")


if __name__ == "__main__":
    main()
