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
    CreditBudgetExceeded,
    OddsApiClient,
    TextFetcher,
    ingest_football_data,
    ingest_football_data_fixtures,
    ingest_odds_api_gameweek,
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
    ap.add_argument("--odds-api", action="store_true",
                    help="fetch anytime-scorer + h2h/totals from The Odds API")
    ap.add_argument("--regions", default="uk", help="Odds API regions (default uk)")
    ap.add_argument("--max-credits", type=int,
                    default=OddsApiClient.DEFAULT_MONTHLY_CAP,
                    help=f"monthly credit cap; refuse to run above it "
                         f"(default {OddsApiClient.DEFAULT_MONTHLY_CAP} of 500 free)")
    ap.add_argument("--dry-run", action="store_true",
                    help="price the run against the live quota and stop, spending 0")
    args = ap.parse_args(argv)

    if not any((args.history, args.fixtures, args.match_fixtures, args.odds_api)):
        ap.error("nothing to do: pass --history, --fixtures, --odds-api "
                 "or --match-fixtures")

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

        if args.odds_api:
            _run_odds_api(wh, args)

        if args.match_fixtures:
            m = match_fixture_keys(wh, args.match_fixtures,
                                   dt.datetime.now(dt.UTC))
            matched = int(m["fixture_id"].notna().sum())
            print(f"  matched {matched}/{len(m)} odds keys to FPL fixture ids")
            for _, r in m[m["fixture_id"].isna()].head(10).iterrows():
                print(f"    unmatched: {r['fixture_key']}")

    return 0


def _run_odds_api(wh: Warehouse, args: argparse.Namespace) -> None:
    """Fetch The Odds API, reporting credit spend and name-match rate.

    Budget failures are reported and swallowed rather than raised: a run that
    would breach the cap has spent nothing, and the football-data path in the
    same invocation should still be allowed to finish.
    """
    from fpl_edge.config import secret

    try:
        report = ingest_odds_api_gameweek(
            wh, args.season, api_key=secret("ODDS_API_KEY"),
            regions=args.regions, max_monthly_credits=args.max_credits,
            dry_run=args.dry_run,
        )
    except CreditBudgetExceeded as exc:
        print(f"  odds-api REFUSED: {exc}")
        return

    tag = "dry-run (0 spent)" if args.dry_run else "live"
    print(f"  odds-api {tag}: {report.events} events, "
          f"{report.credits_spent} credits spent, "
          f"{report.credits_remaining} remaining, "
          f"{report.credits_used_month} used this month (cap {args.max_credits})")
    if args.dry_run:
        return
    print(f"  odds-api rows written {report.rows_written}, "
          f"clean sheets {report.clean_sheets_written}")
    print(f"  odds-api player match rate "
          f"{report.matched}/{report.scorer_selections} "
          f"({100 * report.match_rate:.1f}%)")
    for m in report.unmatched:
        print(f"    UNMATCHED scorer selection: {m.api_name!r} [{m.rule}]")


if __name__ == "__main__":
    sys.exit(main())
