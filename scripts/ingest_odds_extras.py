"""Fetch extra Odds API markets and derive bookmaker-implied priors.

Two phases, runnable together or separately:

* ``--fetch`` -- spend credits on correct-score + BTTS (uk) and team-totals
  (us) for every upcoming EPL event, landing quoted prices in ``fact_odds``.
  Hard-capped by the expansion ledger (150/month, see
  ``fpl_edge.ingest.odds_markets``); refuses before spending, and logs the
  real spend per run.
* ``--derive`` -- pure computation, zero credits: read the latest quotes from
  ``fact_odds`` and write clean-sheet / team-lambda / scorer-prior rows into
  ``fact_odds_derived`` (created on first use).

Examples::

    uv run python scripts/ingest_odds_extras.py --fetch --dry-run
    uv run python scripts/ingest_odds_extras.py --fetch --derive
    uv run python scripts/ingest_odds_extras.py --derive
"""

from __future__ import annotations

import argparse
import sys

from fpl_edge.ingest.odds import CreditBudgetExceeded
from fpl_edge.ingest.odds_derived import derive_gameweek_priors
from fpl_edge.ingest.odds_markets import EXPANSION_MONTHLY_CAP, ingest_extra_markets
from fpl_edge.store import Warehouse

CURRENT_SEASON = "2026-27"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true",
                    help="spend credits fetching correct_score/btts/team_totals")
    ap.add_argument("--derive", action="store_true",
                    help="derive priors from fact_odds into fact_odds_derived (free)")
    ap.add_argument("--season", default=CURRENT_SEASON)
    ap.add_argument("--no-team-totals", action="store_true",
                    help="skip the US-region team totals calls (saves 2/event)")
    ap.add_argument("--max-credits", type=int, default=EXPANSION_MONTHLY_CAP,
                    help=f"monthly cap for this expansion "
                         f"(default {EXPANSION_MONTHLY_CAP})")
    ap.add_argument("--dry-run", action="store_true",
                    help="price the fetch against the live quota, spend 0")
    args = ap.parse_args(argv)

    if not (args.fetch or args.derive):
        ap.error("nothing to do: pass --fetch and/or --derive")

    with Warehouse() as wh:
        if args.fetch:
            from fpl_edge.config import secret

            try:
                rep = ingest_extra_markets(
                    wh, args.season, api_key=secret("ODDS_API_KEY"),
                    include_team_totals=not args.no_team_totals,
                    cap=args.max_credits, dry_run=args.dry_run,
                )
            except CreditBudgetExceeded as exc:
                print(f"  extra-markets REFUSED: {exc}")
                return 1
            tag = "dry-run (0 spent)" if args.dry_run else "live"
            print(f"  extra-markets {tag}: {rep.events} events, "
                  f"planned {rep.credits_planned}, spent {rep.credits_spent}, "
                  f"key remaining {rep.credits_remaining}, "
                  f"key used this month {rep.credits_used_month}")
            print(f"  expansion ledger this month: {rep.expansion_spent_month}"
                  f"/{args.max_credits}")
            if not args.dry_run:
                print(f"  rows written {rep.rows_written} "
                      f"by market {rep.rows_by_market}")

        if args.derive:
            rep2 = derive_gameweek_priors(wh, args.season)
            print(f"  derive: {rep2.fixtures_derived}/{rep2.fixtures_seen} fixtures, "
                  f"{rep2.rows_written} rows into fact_odds_derived")
            print(f"  by method: {rep2.by_method}")
            for s in rep2.skipped:
                print(f"    skipped: {s}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
