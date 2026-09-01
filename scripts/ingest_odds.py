"""Pull bookmaker odds into ``fact_odds``, and say honestly what did not land.

Modes, because the sources have completely different point-in-time semantics
(see ``fpl_edge.ingest.odds``):

* ``--history 2024-25 2025-26`` -- completed seasons from
  ``mmz4281/<code>/E0.csv``. Rows are stamped at each fixture's kickoff, since
  that file is published only after the matches are played. This is the
  backtest corpus, and as of 2026-08-28 it also carries per-match ``HxG``/
  ``AxG`` for the current season -- free post-match xG, no key.
* ``--fixtures`` -- upcoming fixtures from ``fixtures.csv``, stamped at the
  fetch instant. Free and forward-looking, but **empirically empty for E0
  until roughly a day before kickoff**: measured 2026-08-28T06:00Z, 13 hours
  before a Friday-evening kickoff, that file carried 5 rows and not one of
  them was Premier League. It cannot be relied on to price a deadline.
* ``--odds-api`` -- The Odds API. The only source that carries anytime
  scorer, and the only one that reliably prices a fixture *before* it kicks
  off. Costs credits; see the arithmetic below.

Exit code, and why this file was rewritten
------------------------------------------
This script used to return 0 no matter what happened. On 2026-08-19 the
nightly ``ingest_odds_props`` step began refusing every run -- a monthly cap of
30 against a run that needs 22, with 67 already used -- and because the refusal
was caught, printed and swallowed, ``post_gw`` recorded ``ok=true`` every night
for nine days. By the GW2 deadline the anytime-scorer market was 206 hours old
and nothing had gone red. ``ingest_odds_fixtures`` was doing the same thing
more quietly still, reporting "rows 0, parsed 0, new 0" and exiting 0.

So this script now follows the vocabulary the crawl settled on
(``fpl_edge.ingest.rivals.crawl``), for the same reason and with the same
words:

* every stage the invocation asked for is **named up front** as
  ``not_reached``, so a stage that never executes is visible by name rather
  than invisible by absence;
* ``skipped:`` is reserved for a stage that genuinely had nothing to do, and
  is the ONLY non-``ok`` status that is not an outage;
* ``incomplete_stages`` lists everything else, and makes :func:`main` exit
  non-zero;
* ``failures`` lists the silent-nothings that no stage status can express --
  a refusal, an impossible cap, credits spent for zero rows -- and
  ``--allow-incomplete`` deliberately does NOT forgive those.

The rule the stages share, taken verbatim from the crawl: for every stage, ask
"if this silently did nothing, what would be different?", and if the answer is
"nothing", that is the assertion that is missing.

Credit arithmetic (The Odds API free tier, 500/month measured)
--------------------------------------------------------------
One ``--odds-api`` refresh costs ``2 + one per fixture priced``:

    /events                       0   (free, and returns the quota headers)
    /odds  h2h+totals, uk         2
    /events/{id}/odds  scorer     1 per fixture

Restricted to the fixtures before the next deadline (``--horizon``, on by
default) that is **12 credits** for a 10-fixture gameweek. Unrestricted it is
22, because ``/events`` returns two gameweeks of fixtures.

    3 ladder runs a gameweek (T-36h, T-12h, T-5h)      36
    nightly top-up, only when >48h stale, ~2x a week   24
                                                       --
    per gameweek                                       60
    per 4.3-gameweek month                            258
    + extra-markets expansion's own ceiling           150
                                                      ---
                                                      408  of 500

Examples::

    uv run python scripts/ingest_odds.py --fixtures
    uv run python scripts/ingest_odds.py --odds-api
    uv run python scripts/ingest_odds.py --odds-api --max-age-hours 48
    uv run python scripts/ingest_odds.py --odds-api --dry-run
    uv run python scripts/ingest_odds.py --history 2023-24 2024-25 2025-26
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fpl_edge.ingest.odds import (
    FOOTBALL_DATA_BASE,
    FREE_TIER_MONTHLY_CREDITS,
    CreditBudgetExceeded,
    OddsApiClient,
    TextFetcher,
    freshness_summary,
    ingest_football_data,
    ingest_football_data_fixtures,
    match_fixture_keys,
    odds_freshness,
    refresh_odds_api,
)
from fpl_edge.store import Warehouse

#: The season this engine is currently playing. Odds for it come from
#: fixtures.csv until football-data publishes the completed-season file.
CURRENT_SEASON = "2026-27"

#: How far past the next deadline a fixture may kick off and still be priced.
#: A gameweek's own fixtures spread over four days, so the horizon has to reach
#: past the deadline itself or a Monday-night game is never given a scorer card.
#: Four days covers Friday-evening deadline through Monday-night kickoff and
#: stops short of the following gameweek.
HORIZON_AFTER_DEADLINE = dt.timedelta(days=4)


@contextmanager
def _stage(stages: dict[str, str], name: str) -> Iterator[None]:
    """Run one named stage, recording ``ok`` or the exception that stopped it.

    An exception is contained here rather than aborting the process, because
    the stages are independent sources: a football-data outage must not stop
    the Odds API refresh eleven hours before a deadline. The containment is
    only safe *because* the status is recorded and :func:`main` exits non-zero
    on it -- swallowing without recording is the bug this file is fixing.
    """
    stages[name] = "running"
    try:
        yield
    except CreditBudgetExceeded as exc:
        stages[name] = f"refused: {exc}"
    except Exception as exc:  # noqa: BLE001 - the stage status is the error channel
        stages[name] = f"failed: {type(exc).__name__}: {exc}"
    else:
        if stages[name] == "running":
            stages[name] = "ok"


def _incomplete(stages: dict[str, str]) -> list[str]:
    """Stages that did not finish, excluding legitimate no-op skips."""
    return sorted(
        name for name, status in stages.items()
        if not (status == "ok" or status.startswith("skipped:"))
    )


def _next_deadline(season: str) -> dt.datetime | None:
    """The next deadline in ``dim_event``, read from a copy: no lock taken."""
    try:
        with Warehouse.read_copy() as wh:
            got = wh.sql(
                "SELECT min(deadline_utc) AS d FROM dim_event "
                "WHERE season = ? AND deadline_utc > ?",
                [season, dt.datetime.now(dt.UTC)],
            )
            if got.empty or got.iloc[0]["d"] is None:
                return None
            return got.iloc[0]["d"].to_pydatetime().astimezone(dt.UTC)
    except Exception:  # noqa: BLE001 - no horizon is a wider run, not a failure
        return None


def _freshness(season: str) -> dict[str, Any]:
    """Per-market age of ``fact_odds``. Read-only, from a copy."""
    try:
        with Warehouse.read_copy() as wh:
            return freshness_summary(odds_freshness(wh, season=season))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "markets": []}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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
                    help=f"monthly credit cap; refuse to run above it (default "
                         f"{OddsApiClient.DEFAULT_MONTHLY_CAP} of "
                         f"{FREE_TIER_MONTHLY_CREDITS} free)")
    ap.add_argument("--max-age-hours", type=float, default=None,
                    help="skip the Odds API refresh when every market it writes "
                         "is younger than this. The top-up gate: makes a nightly "
                         "run a cheap no-op during a week the ladder already "
                         "covered, and a real refresh in a quiet one.")
    ap.add_argument("--no-horizon", action="store_true",
                    help="price every fixture /events returns, not just the ones "
                         "before the next deadline. Roughly doubles the cost.")
    ap.add_argument("--dry-run", action="store_true",
                    help="price the run against the live quota and stop, spending 0")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="exit 0 even if a stage did not complete; for manual "
                         "partial runs, never for the scheduled job. It does NOT "
                         "forgive a refusal or a zero-row write.")
    ap.add_argument("--json", action="store_true",
                    help="print the receipt as JSON instead of prose")
    return ap


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Execute the requested stages and return the receipt.

    The odds-api stage runs through :func:`refresh_odds_api`, which fetches
    with **no warehouse handle open** and takes the write lock only once every
    body is in memory. Twelve HTTP round trips at T-5h must not sit on the lock
    the solver and the Telegram bot are waiting for.
    """
    requested: list[str] = []
    if args.history:
        requested.append("history")
    if args.fixtures:
        requested.append("fixtures")
    if args.odds_api:
        requested.append("odds_api")
    if args.match_fixtures:
        requested.append("match_fixtures")

    # Named up front, so a stage that never executes is visible as
    # "not_reached" instead of being invisible by absence.
    stages: dict[str, str] = {name: "not_reached" for name in requested}
    #: Failures no stage status can express. --allow-incomplete never forgives
    #: these, because a run that fetched nothing is not a partial anything.
    failures: list[str] = []
    summary: dict[str, Any] = {
        "started_utc": dt.datetime.now(dt.UTC).isoformat(),
        "season": args.season,
        "stages": stages,
        "freshness_before": _freshness(args.season),
    }

    if {"history", "fixtures"} & set(requested):
        with Warehouse() as wh, TextFetcher(
            "odds_football_data", base_url=FOOTBALL_DATA_BASE
        ) as fetcher:
            if args.history:
                with _stage(stages, "history"):
                    got = {}
                    for season in args.history:
                        got[season] = ingest_football_data(
                            wh, season, fetcher=fetcher, devig_method=args.devig)
                    summary["history"] = got
            if args.fixtures:
                with _stage(stages, "fixtures"):
                    got = ingest_football_data_fixtures(
                        wh, args.season, fetcher=fetcher, devig_method=args.devig)
                    summary["fixtures"] = got
                    _judge_fixtures(got, stages, failures)

    if args.odds_api:
        with _stage(stages, "odds_api"):
            summary["odds_api"] = _run_odds_api(args, stages, failures)

    if args.match_fixtures:
        with _stage(stages, "match_fixtures"), Warehouse.read_copy() as wh:
            m = match_fixture_keys(wh, args.match_fixtures, dt.datetime.now(dt.UTC))
            matched = int(m["fixture_id"].notna().sum())
            summary["match_fixtures"] = {
                "matched": matched, "keys": len(m),
                "unmatched": [str(r["fixture_key"])
                              for _, r in m[m["fixture_id"].isna()].head(10).iterrows()],
            }

    summary["freshness_after"] = _freshness(args.season)
    summary["incomplete_stages"] = _incomplete(stages)
    summary["failures"] = failures
    return summary


def _judge_fixtures(
    got: dict[str, Any], stages: dict[str, str], failures: list[str]
) -> None:
    """Decide whether a zero-row football-data fixtures pass is fine or an outage.

    This is the distinction the old script did not draw, and the reason it could
    report "rows 0, parsed 0, new 0" as success forever. Two different worlds
    produce zero rows:

    * football-data has not published any E0 fixtures yet. Measured on
      2026-08-28 at T-11h30m it had 5 fixture rows and none of them were E0.
      That is the feed behaving normally and there is nothing to do, so the
      stage is ``skipped:`` -- an honest no-op, not a success.
    * football-data published E0 fixtures and we parsed **none** of them. That
      is a parser or schema break, and it is exactly what a green tick would
      hide. It goes in ``failures``.
    """
    available = int(got.get("e0_fixtures_available") or 0)
    parsed = int(got.get("rows_parsed") or 0)
    if available == 0:
        stages["fixtures"] = (
            "skipped: football-data publishes E0 fixtures only a day or two "
            "ahead of kickoff, and has published none yet")
        return
    if parsed == 0:
        stages["fixtures"] = (
            f"failed: football-data published {available} E0 fixture rows and "
            f"the parser produced 0")
        failures.append(
            f"fixtures: {available} E0 rows were available and 0 were parsed; "
            f"this is a parser or schema break, not an empty feed")


def _run_odds_api(
    args: argparse.Namespace, stages: dict[str, str], failures: list[str]
) -> dict[str, Any]:
    """The Odds API stage. Every way of fetching nothing is named and reported.

    A refusal used to be caught here, printed, and returned as success. It is
    now the stage's status *and* a failure, because a refused run has exactly
    the same effect on the warehouse as a crashed one: nothing arrives, and the
    number the solver reads at the deadline is however old it already was.
    """
    from fpl_edge.config import secret

    out: dict[str, Any] = {"max_credits": args.max_credits}

    if args.max_age_hours is not None:
        fresh = odds_api_markets_fresh(args.season, args.max_age_hours)
        out["age_gate"] = fresh
        if fresh["fresh"]:
            stages["odds_api"] = (
                f"skipped: every market it writes is younger than "
                f"{args.max_age_hours:g}h (oldest {fresh['oldest_age_hours']}h)")
            return out

    horizon = None
    if not args.no_horizon:
        deadline = _next_deadline(args.season)
        if deadline is not None:
            horizon = deadline + HORIZON_AFTER_DEADLINE
            out["horizon_utc"] = horizon.isoformat()
            out["next_deadline_utc"] = deadline.isoformat()

    if args.dry_run:
        from fpl_edge.ingest.odds import ingest_odds_api_gameweek
        with Warehouse.read_copy() as wh:
            report = ingest_odds_api_gameweek(
                wh, args.season, api_key=secret("ODDS_API_KEY"),
                regions=args.regions, max_monthly_credits=args.max_credits,
                dry_run=True, horizon=horizon,
            )
        out.update(events=report.events, credits_spent=0,
                   credits_remaining=report.credits_remaining,
                   credits_used_month=report.credits_used_month)
        stages["odds_api"] = "skipped: --dry-run priced the run and spent nothing"
        return out

    report = refresh_odds_api(
        args.season, api_key=secret("ODDS_API_KEY"), regions=args.regions,
        max_monthly_credits=args.max_credits, horizon=horizon,
    )
    out.update(
        events=report.events,
        credits_spent=report.credits_spent,
        credits_remaining=report.credits_remaining,
        credits_used_month=report.credits_used_month,
        rows_written=report.rows_written,
        clean_sheets_written=report.clean_sheets_written,
        scorer_selections=report.scorer_selections,
        matched=report.matched,
        match_rate=round(report.match_rate, 4),
        unmatched=[{"api_name": m.api_name, "rule": m.rule} for m in report.unmatched],
    )

    # A run that spent credits and wrote nothing is the silent-nothing this
    # module's whole failure mode is made of. The credits are gone and the
    # warehouse is exactly as stale as before; that is not a success, and no
    # stage status would have said so.
    if report.rows_written == 0:
        stages["odds_api"] = (
            f"failed: spent {report.credits_spent} credits over {report.events} "
            f"events and wrote 0 rows to fact_odds")
        failures.append(
            f"odds_api: {report.credits_spent} credits spent for zero rows "
            f"written; the fetch succeeded and the landing did not")
    return out


def odds_api_markets_fresh(season: str, max_age_hours: float) -> dict[str, Any]:
    """Is every market this ingest writes younger than ``max_age_hours``?

    The gate that makes a nightly top-up idempotent and cheap. Only the markets
    this path actually writes are consulted: the extra-markets expansion owns
    correct score, BTTS and team totals on its own ledger, and letting its
    staleness force a scorer refresh would spend the wrong budget.
    """
    from fpl_edge.ingest.odds import (
        MARKET_ANYTIME_SCORER,
        MARKET_CLEAN_SHEET,
        MARKET_H2H,
        MARKET_TOTALS,
    )

    written = [MARKET_H2H, MARKET_TOTALS, MARKET_CLEAN_SHEET, MARKET_ANYTIME_SCORER]
    try:
        with Warehouse.read_copy() as wh:
            rows = odds_freshness(wh, season=season, markets=written)
    except Exception as exc:  # noqa: BLE001 - unreadable freshness never skips
        return {"fresh": False, "reason": f"{type(exc).__name__}: {exc}"}

    rows = [r for r in rows if r.market in set(written)]
    ages = [r.age_hours for r in rows]
    # An absent market has age None and must NEVER read as fresh: "we have no
    # anytime-scorer rows at all" is the strongest possible reason to fetch.
    if not rows or any(a is None for a in ages):
        missing = [r.market for r in rows if r.age_hours is None]
        return {"fresh": False, "reason": f"no rows for {missing or written}",
                "oldest_age_hours": None}
    oldest = max(ages)  # type: ignore[type-var]
    return {
        "fresh": bool(oldest < max_age_hours),
        "oldest_age_hours": round(float(oldest), 2),
        "max_age_hours": float(max_age_hours),
        "markets": {r.market: round(float(r.age_hours or 0.0), 2) for r in rows},
    }


def render(summary: dict[str, Any]) -> str:
    """The prose receipt. Every stage on its own line, status first."""
    lines: list[str] = []
    for name, status in summary["stages"].items():
        lines.append(f"  {name}: {status}")
    for name in ("history", "fixtures", "odds_api", "match_fixtures"):
        got = summary.get(name)
        if isinstance(got, dict) and got:
            lines.append(f"    {name} -> {json.dumps(got, default=str)[:400]}")
    before = summary.get("freshness_before", {})
    after = summary.get("freshness_after", {})
    lines.append(f"  freshness before: oldest {before.get('oldest_market')} "
                 f"{before.get('oldest_age_hours')}h, "
                 f"stale {before.get('stale_markets')}")
    lines.append(f"  freshness after:  oldest {after.get('oldest_market')} "
                 f"{after.get('oldest_age_hours')}h, "
                 f"stale {after.get('stale_markets')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Run the requested stages; exit non-zero if any of them did not complete.

    The exit code is the whole point. ``post_gw`` and the deadline DAG judge a
    step purely on its return code, so a script that returns 0 after refusing
    to spend a single credit is telling the scheduler that the odds are fresh.
    They were 206 hours old.
    """
    ap = build_parser()
    args = ap.parse_args(argv)

    if not any((args.history, args.fixtures, args.match_fixtures, args.odds_api)):
        ap.error("nothing to do: pass --history, --fixtures, --odds-api "
                 "or --match-fixtures")

    # Refuse an unsatisfiable cap at the FLAG, before a run is attempted. The
    # outage this file documents was a --max-credits value below one run's
    # cost: every run refused, correctly and forever, and the refusal was the
    # only place the mistake was visible. An operator error should be rejected
    # where it is made.
    if args.odds_api and args.max_credits < OddsApiClient.MIN_SANE_MONTHLY_CAP:
        ap.error(
            f"--max-credits {args.max_credits} is below "
            f"{OddsApiClient.MIN_SANE_MONTHLY_CAP}, which is less than a single "
            f"refresh costs (2 + one per fixture, so 12 for a normal gameweek). "
            f"A cap this low cannot ever be satisfied: every run would refuse, "
            f"in this month and every month after it. The free tier allows "
            f"{FREE_TIER_MONTHLY_CREDITS}/month and this repo's default cap is "
            f"{OddsApiClient.DEFAULT_MONTHLY_CAP}."
        )

    summary = run(args)
    print(json.dumps(summary, indent=1, default=str) if args.json else render(summary))

    failures = summary.get("failures") or []
    incomplete = summary.get("incomplete_stages") or []
    if failures:
        print(f"FAILED: {'; '.join(failures)}", file=sys.stderr)
        return 1
    if incomplete and not args.allow_incomplete:
        detail = "; ".join(f"{n}: {summary['stages'][n]}" for n in incomplete)
        print(f"FAILED: stages did not complete: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
