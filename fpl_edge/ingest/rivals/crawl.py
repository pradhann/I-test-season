"""The crawl entry point. One command, one declared budget, one receipt.

    uv run python -m fpl_edge.ingest.rivals.crawl --budget 900

Everything about this run is meant to be boring and repeatable. It builds the
candidate pool, fetches each candidate's season history, and -- only if a
gameweek deadline has actually passed -- their squads and transfers. Re-running
it the next day costs almost nothing because the cache holds, which is the
behaviour you want from something scheduled.

The receipt printed at the end is the number to quote when someone asks how hard
we hit the API. It is itemised by endpoint kind, and cache hits are reported
separately so a cheap re-run is visibly cheap rather than merely claimed to be.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals import history as history_mod
from fpl_edge.ingest.rivals import picks as picks_mod
from fpl_edge.ingest.rivals import roster, schema
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget, RivalsFetcher
from fpl_edge.store import Warehouse
from fpl_edge.store.warehouse import WarehouseLockedError


def _season_and_deadlines(fetcher: RivalsFetcher) -> tuple[str, dict[int, dt.datetime]]:
    """Season label and every gameweek deadline, from one bootstrap request.

    Deadlines are needed to stamp picks with the instant they became public, and
    to avoid spending a request per manager on a gameweek that has not started.
    One request buys both.
    """
    got = fetcher.get_json("bootstrap-static/")
    bs = got.body
    events = bs["events"]
    first = min(events, key=lambda e: e["deadline_time"])
    year = dt.datetime.fromisoformat(first["deadline_time"].replace("Z", "+00:00")).year
    season = f"{year}-{str(year + 1)[-2:]}"
    deadlines = {
        int(e["id"]): dt.datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
        for e in events
    }
    return season, deadlines


def _write(
    frames: dict[str, Any],
    db_path: str | None,
    summary: dict[str, Any],
    *,
    attempts: int = 6,
    lock_timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Commit the crawled frames, waiting out other writers rather than dying.

    DuckDB permits one writer, and this repo has several ingests that can be
    running at once. Losing a completed crawl to a lock held by a concurrent job
    would mean the network spend was wasted -- the one cost here that cannot be
    recovered from cache. So the write retries with backoff, and reports the
    wait rather than hiding it.

    If the lock never frees, the fetched bodies are still archived under
    ``data/raw/`` and indexed in the cache, so re-running the crawl replays them
    for zero requests. That is the property that makes failing here survivable.
    """
    import time

    last_error = ""
    for attempt in range(attempts):
        try:
            wh = (Warehouse(lock_timeout_s=lock_timeout_s) if db_path is None
                  else Warehouse(db_path, lock_timeout_s=lock_timeout_s))
        except WarehouseLockedError as exc:
            last_error = str(exc).splitlines()[0]
            time.sleep(min(2 ** attempt, 30))
            continue
        try:
            schema.migrate(wh)
            written: dict[str, int] = {}
            for table, df in frames.items():
                if df is not None and not df.empty:
                    written[table] = wh.append(table, df)
            return {"status": "ok", "attempts": attempt + 1, "rows": written}
        finally:
            wh.close()
    return {
        "status": "locked",
        "attempts": attempts,
        "error": last_error,
        "recovery": "every fetched body is archived and cached; re-run the same "
                    "command to commit it for zero network requests",
    }


def run(
    *,
    budget_limit: int = 900,
    max_candidates: int = 600,
    db_path: str | None = None,
    with_picks: bool = True,
    offline: bool = False,
) -> dict[str, Any]:
    budget = RequestBudget(limit=budget_limit)
    fetcher = RivalsFetcher(budget, offline=offline)
    summary: dict[str, Any] = {}
    # Fetch everything BEFORE opening the warehouse. DuckDB permits a single
    # writer, and a crawl that holds the lock for the fifteen minutes it spends
    # sleeping politely between requests would block every other ingest in the
    # repo for no reason. Collect first, hold the lock for the seconds it takes
    # to write.
    frames: dict[str, Any] = {}
    try:
        season, deadlines = _season_and_deadlines(fetcher)
        summary["season"] = season

        candidates, pool_report, managers, memberships = roster.build_pool(
            fetcher, max_candidates=max_candidates
        )
        summary["pool"] = vars(pool_report)
        frames["dim_manager"] = managers
        frames["dim_manager_league"] = memberships

        entry_ids = [c.entry_id for c in candidates]
        past, current, chips, missing = history_mod.ingest_histories(
            fetcher, entry_ids, season=season
        )
        summary["history"] = {
            "entries_requested": len(entry_ids),
            "entries_404": len(missing),
            "past_season_rows": int(len(past)),
            "current_gw_rows": int(len(current)),
            "chip_rows": int(len(chips)),
        }
        frames["fact_manager_season"] = past
        frames["fact_manager_gw"] = current
        frames["fact_manager_chip"] = chips

        if with_picks:
            now = dt.datetime.now(dt.timezone.utc)
            live_gws = [gw for gw, d in deadlines.items() if d <= now]
            if not live_gws:
                summary["picks"] = {
                    "skipped": "no gameweek deadline has passed; "
                               "the picks endpoint returns 404 for every entry"
                }
            else:
                p, c, stats = picks_mod.ingest_picks(
                    fetcher, entry_ids, live_gws, season=season,
                    deadlines=deadlines, now=now,
                )
                summary["picks"] = stats
                frames["fact_manager_pick"] = p
                if not c.empty:
                    frames["fact_manager_chip"] = pd.concat(
                        [frames["fact_manager_chip"], c], ignore_index=True
                    ).drop_duplicates()
                t, tstats = picks_mod.ingest_transfers(
                    fetcher, entry_ids, season=season, deadlines=deadlines
                )
                summary["transfers"] = tstats
                frames["fact_manager_transfer"] = t
    except BudgetExhausted as exc:
        # Not a failure. The pool is partial, everything fetched is committed,
        # and the next run resumes from the cache. Say so rather than dying.
        summary["budget_exhausted"] = str(exc)
    finally:
        fetcher.close()

    summary["write"] = _write(frames, db_path, summary)

    summary["requests"] = {
        "limit": budget.limit,
        "spent": budget.spent,
        "cache_hits": budget.cache_hits,
        "by_kind": budget.by_kind,
        "receipt": budget.receipt(),
    }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=900,
                    help="hard cap on network requests; the run stops rather than exceed it")
    ap.add_argument("--max-candidates", type=int, default=600)
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-picks", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="serve only from cache; raises if something is missing")
    args = ap.parse_args()
    out = run(
        budget_limit=args.budget,
        max_candidates=args.max_candidates,
        db_path=args.db,
        with_picks=not args.no_picks,
        offline=args.offline,
    )
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
