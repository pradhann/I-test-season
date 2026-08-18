"""Pull bookmaker odds into ``fact_odds``.

Two modes, because the two football-data.co.uk files have completely different
point-in-time semantics (see ``fpl_edge.ingest.odds``):

* ``--history 2024-25 2025-26`` -- completed seasons from
  ``mmz4281/<code>/E0.csv``. Rows are stamped at each fixture's kickoff, since
  that file is published only after the matches are played. This is the
  backtest corpus.
* ``--fixtures`` -- upcoming fixtures from ``fixtures.csv``, stamped at the
  fetch instant. This is the only path whose rows may legitimately inform the
  decision at the next deadline.

Examples::

    uv run python scripts/ingest_odds.py --fixtures
    uv run python scripts/ingest_odds.py --history 2023-24 2024-25 2025-26
    uv run python scripts/ingest_odds.py --history 2025-26 --match-fixtures 2025-26
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from fpl_edge.ingest.odds import (
    FOOTBALL_DATA_BASE,
    TextFetcher,
    ingest_football_data,
    ingest_football_data_fixtures,
    match_fixture_keys,
)
from fpl_edge.store import Warehouse

#: The season this engine is currently playing. Odds for it come from
#: fixtures.csv until football-data publishes the completed-season file.
CURRENT_SEASON = "2026-27"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--history", nargs="*", metavar="SEASON",
                    help="completed seasons to backfill, e.g. 2024-25 2025-26")
    ap.add_argument("--fixtures", action="store_true",
                    help="fetch forward-looking odds for upcoming fixtures")
    ap.add_argument("--season", default=CURRENT_SEASON,
                    help=f"season label for --fixtures rows (default {CURRENT_SEASON})")
    ap.add_argument("--devig", default="shin",
                    choices=["shin", "multiplicative", "power"],
                    help="overround removal method for the derived fair lines")
    ap.add_argument("--match-fixtures", metavar="SEASON",
                    help="report how many odds keys resolve to FPL fixture ids")
    args = ap.parse_args(argv)

    if not args.history and not args.fixtures and not args.match_fixtures:
        ap.error("nothing to do: pass --history, --fixtures or --match-fixtures")

    with Warehouse() as wh, TextFetcher(
        "odds_football_data", base_url=FOOTBALL_DATA_BASE
    ) as fetcher:
        for season in args.history or []:
            got = ingest_football_data(wh, season, fetcher=fetcher,
                                       devig_method=args.devig)
            print(f"  history {season}   parsed {got['rows_parsed']:>7}  "
                  f"new {got['fact_odds']:>7}")

        if args.fixtures:
            got = ingest_football_data_fixtures(wh, args.season, fetcher=fetcher,
                                                devig_method=args.devig)
            print(f"  fixtures {args.season}  E0 rows {got['e0_fixtures_available']:>4}  "
                  f"parsed {got['rows_parsed']:>7}  new {got['fact_odds']:>7}")
            if got["e0_fixtures_available"] == 0:
                print("  note: football-data publishes E0 fixtures only a day or two "
                      "ahead of kickoff; zero rows here is normal this far out.")

        if args.match_fixtures:
            m = match_fixture_keys(wh, args.match_fixtures,
                                   dt.datetime.now(dt.UTC))
            matched = int(m["fixture_id"].notna().sum())
            print(f"  matched {matched}/{len(m)} odds keys to FPL fixture ids")
            for _, r in m[m["fixture_id"].isna()].head(10).iterrows():
                print(f"    unmatched: {r['fixture_key']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
