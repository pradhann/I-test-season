"""Sampling the top of the overall league, once there is a top to sample.

    uv run python -m fpl_edge.ingest.rivals.top1k --n 750 --budget 800
    uv run python -m fpl_edge.ingest.rivals.top1k --dry-run

The elite crawl (:mod:`fpl_edge.ingest.rivals.crawl`) selects managers on
*long-run records*; that is the right pool for skill questions and the wrong
one for "what does the top-1k own THIS season" -- this season's top-1k is
mostly people having a hot start, and the differential math plays against
them, not against last decade's grandmasters. This module samples the actual
standings: ``/api/leagues-classic/314/standings/?page_standings=N`` pages the
overall league 50 entries at a time, so pages 1..20 are the live top-1,000.

Timing, which is everything here: the standings are EMPTY until the first
gameweek is scored (verified against the live API on 2026-08-18), and an
entry's picks return 404 until that gameweek's deadline passes. So this
sampler produces nothing before GW1 locks -- by design, loudly, for one
request -- and becomes runnable the moment GW1 is scored. Rows it writes are
distinguishable forever: ``dim_manager.source = 'top1k:{season}:gw{gw}'``,
which is what :mod:`fpl_edge.models.field.observed` selects the top-1k cohort
on. Picks are stamped ``as_of = deadline`` like every other pick row, so the
point-in-time story is inherited, not re-invented.

Budget arithmetic, declared rather than discovered: 1 bootstrap request +
ceil(n/50) standings pages + n picks requests. n=750 is 767 requests, ~14
minutes at the enforced 1.1s spacing. The ``RequestBudget`` makes overrunning
that impossible rather than impolite.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals import picks as picks_mod
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget, RivalsFetcher
from fpl_edge.ingest.rivals.crawl import _season_and_deadlines, _write

#: The "Overall" classic league that every entry is auto-enrolled in.
OVERALL_LEAGUE_ID = 314

#: Entries per standings page in the official API.
PAGE_SIZE = 50

#: dim_manager.source prefix; fpl_edge.models.field.observed matches on it.
SOURCE_PREFIX = "top1k"


def plan(n_entries: int) -> dict[str, int]:
    """The request arithmetic for a run, so a human can approve the number."""
    if not 1 <= n_entries <= 10_000:
        raise ValueError("n_entries out of the sane range for a cohort sample")
    pages = math.ceil(n_entries / PAGE_SIZE)
    return {
        "n_entries": n_entries,
        "standings_pages": pages,
        "requests_bootstrap": 1,
        "requests_standings": pages,
        "requests_picks": n_entries,
        "requests_total": 1 + pages + n_entries,
        "minutes_at_polite_pace": round((1 + pages + n_entries) * 1.1 / 60, 1),
    }


def collect(
    fetcher: RivalsFetcher,
    *,
    n_entries: int = 750,
    gw: int | None = None,
    now: dt.datetime | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch the sample. Network only -- no warehouse lock is held here.

    Same split as the elite crawl and for the same reason: DuckDB permits one
    writer, and a fetch phase that sleeps politely between hundreds of
    requests must not hold the lock while it does.

    Returns ``(frames, summary)``. Frames may be empty: pre-GW1 the standings
    are empty and that is reported as a skip, not raised as a failure.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    frames: dict[str, pd.DataFrame] = {}
    summary: dict[str, Any] = {"plan": plan(n_entries)}

    season, deadlines = _season_and_deadlines(fetcher)
    summary["season"] = season
    locked = [g for g, d in deadlines.items() if d <= now]
    if gw is None:
        if not locked:
            summary["skipped"] = (
                "no gameweek deadline has passed; the overall standings are empty "
                "and every picks endpoint returns 404. Re-run after the first "
                "deadline locks."
            )
            return frames, summary
        gw = max(locked)
    elif gw not in deadlines or deadlines[gw] > now:
        summary["skipped"] = f"GW{gw} has not locked; its picks are private"
        return frames, summary
    summary["gw"] = gw

    # -- standings pages -----------------------------------------------------
    entry_rows: list[dict[str, Any]] = []
    pages_fetched = 0
    empty_standings = False
    for page in range(1, math.ceil(n_entries / PAGE_SIZE) + 1):
        got = fetcher.get_json(
            f"leagues-classic/{OVERALL_LEAGUE_ID}/standings/",
            {"page_standings": page},
        )
        pages_fetched += 1
        body = got.body or {}
        standings = body.get("standings") or {}
        results = standings.get("results") or []
        if not results:
            empty_standings = page == 1
            break
        for r in results:
            entry_rows.append({
                "entry_id": int(r["entry"]),
                "player_name": r.get("player_name"),
                "entry_name": r.get("entry_name"),
                "rank": r.get("rank"),
            })
        if len(entry_rows) >= n_entries or not standings.get("has_next"):
            break
    entry_rows = entry_rows[:n_entries]
    summary["standings"] = {"pages": pages_fetched, "entries": len(entry_rows)}
    if empty_standings:
        summary["skipped"] = (
            "overall standings returned no results (the league is populated at "
            "first scoring, not at rollover); nothing to sample yet"
        )
        return frames, summary
    if not entry_rows:
        summary["skipped"] = "standings pages yielded no entries"
        return frames, summary

    # A rank-ordered identity for the cohort: the same entry sampled in a later
    # gameweek gets a fresh row with a fresh as_of, so cohort membership is
    # itself point-in-time (this week's top-1k is not last week's).
    as_of = now
    frames["dim_manager"] = pd.DataFrame([
        {
            "entry_id": r["entry_id"], "player_name": r["player_name"],
            "entry_name": r["entry_name"], "region": None, "years_active": None,
            "favourite_team_id": None, "started_event": None,
            "source": f"{SOURCE_PREFIX}:{season}:gw{gw}:rank{r['rank']}",
            "as_of": as_of,
        }
        for r in entry_rows
    ])

    # -- picks, via the existing (deadline-stamped) ingest --------------------
    entry_ids = [r["entry_id"] for r in entry_rows]
    p, c, stats = picks_mod.ingest_picks(
        fetcher, entry_ids, [gw], season=season, deadlines=deadlines, now=now
    )
    summary["picks"] = stats
    if not p.empty:
        frames["fact_manager_pick"] = p
    if not c.empty:
        frames["fact_manager_chip"] = c
    return frames, summary


def run(
    *,
    n_entries: int = 750,
    budget_limit: int | None = None,
    gw: int | None = None,
    db_path: str | None = None,
    offline: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One sampling run: declared budget, fetch, single short-lived write.

    ``dry_run`` performs no network I/O and no writes: it reports the request
    arithmetic so the budget can be approved before it is spent.
    """
    if dry_run:
        return {"dry_run": True, "plan": plan(n_entries)}
    declared = plan(n_entries)
    budget = RequestBudget(
        limit=budget_limit if budget_limit is not None
        else declared["requests_total"] + 10
    )
    fetcher = RivalsFetcher(budget, offline=offline)
    summary: dict[str, Any] = {}
    frames: dict[str, pd.DataFrame] = {}
    try:
        frames, summary = collect(fetcher, n_entries=n_entries, gw=gw)
    except BudgetExhausted as exc:
        # Partial samples are still samples -- 400 real squads beat 0 -- but
        # the cohort share SEs scale with what was actually fetched, so the
        # truncation is reported, not smoothed over.
        summary["budget_exhausted"] = str(exc)
    finally:
        fetcher.close()

    if frames:
        summary["write"] = _write(frames, db_path, summary)
    summary["requests"] = {
        "limit": budget.limit, "spent": budget.spent,
        "cache_hits": budget.cache_hits, "receipt": budget.receipt(),
    }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=750,
                    help="entries to sample from the top of the overall league")
    ap.add_argument("--budget", type=int, default=None,
                    help="hard request cap; defaults to the declared plan + 10")
    ap.add_argument("--gw", type=int, default=None,
                    help="gameweek to sample (default: latest locked)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the request arithmetic and exit; zero requests")
    args = ap.parse_args()
    out = run(n_entries=args.n, budget_limit=args.budget, gw=args.gw,
              db_path=args.db, offline=args.offline, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
