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
ceil(n/50) standings pages + n picks requests + min(n, ``--transfers-top``)
transfer requests. n=750 with the default 500-manager transfer cap is 1,266
requests, ~23 minutes at the enforced 1.1s spacing. The ``RequestBudget`` makes
overrunning that impossible rather than impolite, and each stage runs under a
reserved slice of it so picks cannot starve transfers.

Growing toward the full top-10k
-------------------------------
A single 10,000-entry run is ~3 hours of polite crawling; nobody wants that in
one sitting, and nothing requires it. Picks for a finished gameweek are cached
effectively forever and standings pages for six hours, so re-running with a
larger ``--n`` re-serves everything already fetched from cache and spends the
budget only on the *new* tail -- a stopped or budget-exhausted run resumes for
free. ``--grow K`` automates the nightly deepening: it reads how many entries
the sample already holds for the latest locked gameweek and targets that count
plus K, capped at 10,000, so the scheduled job widens the sample every night
without anyone editing a number.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals import picks as picks_mod
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget, RivalsFetcher
from fpl_edge.ingest.rivals.crawl import STAGE_SHARE, _incomplete, _season_and_deadlines, _stage, _write

#: The "Overall" classic league that every entry is auto-enrolled in.
OVERALL_LEAGUE_ID = 314

#: Entries per standings page in the official API.
PAGE_SIZE = 50

#: dim_manager.source prefix; fpl_edge.models.field.observed matches on it.
SOURCE_PREFIX = "top1k"

#: Stages this sampler is expected to complete. Same contract as
#: :data:`fpl_edge.ingest.rivals.crawl.STAGES`: declared up front so a stage
#: that never runs is reported as ``not_reached`` rather than being invisible.
STAGES: tuple[str, ...] = ("standings", "picks", "transfers")

#: How many of the sampled entries get their transfers fetched, best-ranked
#: first.
#:
#: Until 2026-08-27 this module fetched no transfers at all, which meant the
#: 1,500-manager top-1k cohort -- the *only* cohort in the warehouse with real
#: pick coverage -- had no transfer data by construction, and
#: ``fact_manager_transfer`` was empty warehouse-wide.
#:
#: It is capped rather than run over the whole sample because transfers are the
#: one endpoint here with a short TTL: picks for a finished gameweek are
#: immutable and cached effectively forever, so a grown sample re-serves them
#: free, but ``entry/{id}/transfers/`` is cumulative and re-costs a request per
#: manager on every run. Uncapped, growing the sample toward 10,000 would mean
#: 10,000 fresh requests a night forever. The cap is applied in rank order
#: because transfer *behaviour* is what this is for, and the marginal
#: information in the 4,000th-ranked manager's transfers is small next to the
#: 40th's.
TRANSFERS_TOP_DEFAULT = 500


def plan(n_entries: int, *, transfers_top: int = TRANSFERS_TOP_DEFAULT) -> dict[str, int]:
    """The request arithmetic for a run, so a human can approve the number."""
    if not 1 <= n_entries <= 10_000:
        raise ValueError("n_entries out of the sane range for a cohort sample")
    pages = math.ceil(n_entries / PAGE_SIZE)
    transfers = min(n_entries, max(0, transfers_top))
    total = 1 + pages + n_entries + transfers
    return {
        "n_entries": n_entries,
        "standings_pages": pages,
        "requests_bootstrap": 1,
        "requests_standings": pages,
        "requests_picks": n_entries,
        "requests_transfers": transfers,
        "requests_total": total,
        "minutes_at_polite_pace": round(total * 1.1 / 60, 1),
    }


def collect(
    fetcher: RivalsFetcher,
    *,
    n_entries: int = 750,
    gw: int | None = None,
    now: dt.datetime | None = None,
    transfers_top: int = TRANSFERS_TOP_DEFAULT,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch the sample. Network only -- no warehouse lock is held here.

    Same split as the elite crawl and for the same reason: DuckDB permits one
    writer, and a fetch phase that sleeps politely between hundreds of
    requests must not hold the lock while it does.

    Returns ``(frames, summary)``. Frames may be empty: pre-GW1 the standings
    are empty and that is reported as a skip, not raised as a failure. Every
    stage's outcome is recorded in ``summary["stages"]`` whether it ran or not.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    frames: dict[str, pd.DataFrame] = {}
    summary: dict[str, Any] = {"plan": plan(n_entries, transfers_top=transfers_top)}
    stages: dict[str, str] = {name: "not_reached" for name in STAGES}
    summary["stages"] = stages

    season, deadlines = _season_and_deadlines(fetcher)

    def _skip_all(reason: str) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
        """A legitimate no-op: mark every stage skipped, not not_reached.

        The distinction is the difference between "there is nothing to sample
        yet" (fine, exit 0) and "we ran out of budget before the transfer
        stage" (an outage). Both used to look identical from outside.
        """
        summary["skipped"] = reason
        for name in STAGES:
            stages[name] = f"skipped: {reason}"
        return frames, summary

    summary["season"] = season
    locked = [g for g, d in deadlines.items() if d <= now]
    if gw is None:
        if not locked:
            return _skip_all(
                "no gameweek deadline has passed; the overall standings are empty "
                "and every picks endpoint returns 404. Re-run after the first "
                "deadline locks."
            )
        gw = max(locked)
    elif gw not in deadlines or deadlines[gw] > now:
        return _skip_all(f"GW{gw} has not locked; its picks are private")
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
                # The standings row carries the gameweek score and running
                # total for free; recording them here saves one
                # entry/{id}/history/ request per manager.
                "event_total": r.get("event_total"),
                "total": r.get("total"),
            })
        if len(entry_rows) >= n_entries or not standings.get("has_next"):
            break
    entry_rows = entry_rows[:n_entries]
    summary["standings"] = {"pages": pages_fetched, "entries": len(entry_rows)}
    stages["standings"] = "ok"
    if empty_standings:
        return _skip_all(
            "overall standings returned no results (the league is populated at "
            "first scoring, not at rollover); nothing to sample yet"
        )
    if not entry_rows:
        return _skip_all("standings pages yielded no entries")

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

    # Per-gameweek facts straight from the standings page: points, running
    # total and the overall rank that DEFINES this cohort. bank/value/hits are
    # deliberately left NULL rather than spending one history request per
    # manager to fill them -- the sample exists for ownership, and a thousand
    # extra requests to decorate it would treble the crawl. as_of is the crawl
    # instant: a rank is published when the gameweek is scored, and we observe
    # it now, not at the deadline.
    frames["fact_manager_gw"] = pd.DataFrame([
        {
            "entry_id": r["entry_id"], "season": season, "gw": gw,
            "points": r["event_total"], "total_points": r["total"],
            "overall_rank": r["rank"], "bank_tenths": None,
            "value_tenths": None, "event_transfers": None,
            "event_transfers_cost": None, "points_on_bench": None,
            "as_of": as_of,
        }
        for r in entry_rows
    ])

    # -- picks, then transfers, via the existing (deadline-stamped) ingests ---
    # Both run under a reserved slice of the budget, so a picks stage that
    # overruns cannot silently consume the transfer stage -- the exact failure
    # that left fact_manager_transfer empty in the elite crawl.
    entry_ids = [r["entry_id"] for r in entry_rows]
    with _stage(fetcher.budget, "picks", stages, STAGE_SHARE["picks"]):
        p, c, stats = picks_mod.ingest_picks(
            fetcher, entry_ids, [gw], season=season, deadlines=deadlines, now=now
        )
        summary["picks"] = stats
        if not p.empty:
            frames["fact_manager_pick"] = p
        if not c.empty:
            frames["fact_manager_chip"] = c

    if transfers_top <= 0:
        stages["transfers"] = "skipped: --transfers-top 0"
    else:
        # entry_rows arrive in rank order from the standings pages, so the
        # slice is "the best-ranked N", not an arbitrary N. See
        # TRANSFERS_TOP_DEFAULT for why this is capped at all.
        transfer_ids = entry_ids[:transfers_top]
        with _stage(fetcher.budget, "transfers", stages, STAGE_SHARE["transfers"]):
            t, tstats = picks_mod.ingest_transfers(
                fetcher, transfer_ids, season=season, deadlines=deadlines
            )
            tstats["cohort"] = len(transfer_ids)
            summary["transfers"] = tstats
            if not t.empty:
                frames["fact_manager_transfer"] = t
    return frames, summary


def _sampled_so_far(db_path: str | None) -> int:
    """How many entries the sample already holds for the latest sampled GW.

    Reads a throwaway copy of the database (``Warehouse.read_copy``) so the
    single-writer lock stays free for whoever holds it; a growth decision must
    never block, or be blocked by, an ingest in progress. Returns 0 when the
    warehouse or the table does not exist yet -- growth from nothing is just a
    first run.
    """
    from fpl_edge.store import Warehouse

    try:
        wh = Warehouse.read_copy() if db_path is None else Warehouse.read_copy(db_path)
    except FileNotFoundError:
        return 0
    try:
        gw_df = wh.sql(
            "SELECT max(gw) AS g FROM fact_manager_gw WHERE entry_id IN "
            "(SELECT entry_id FROM dim_manager WHERE source LIKE ?)",
            [f"{SOURCE_PREFIX}:%"],
        )
        g = gw_df.iloc[0]["g"] if not gw_df.empty else None
        gw_like = "%" if g is None or g != g else f"%:gw{int(g)}:%"
        df = wh.sql(
            "SELECT count(DISTINCT entry_id) AS n FROM dim_manager "
            "WHERE source LIKE ? AND source LIKE ?",
            [f"{SOURCE_PREFIX}:%", gw_like],
        )
        return int(df.iloc[0]["n"]) if not df.empty else 0
    except Exception:  # noqa: BLE001 - absent table means an empty sample
        return 0
    finally:
        wh.close()


def run(
    *,
    n_entries: int = 750,
    budget_limit: int | None = None,
    gw: int | None = None,
    db_path: str | None = None,
    offline: bool = False,
    dry_run: bool = False,
    grow: int | None = None,
    transfers_top: int = TRANSFERS_TOP_DEFAULT,
) -> dict[str, Any]:
    """One sampling run: declared budget, fetch, single short-lived write.

    ``dry_run`` performs no network I/O and no writes: it reports the request
    arithmetic so the budget can be approved before it is spent. ``grow``
    overrides ``n_entries`` with (already sampled) + grow, capped at 10,000 --
    the shape a nightly job wants: each run deepens the sample by a declared
    amount and the cache makes the already-held prefix free.
    """
    if grow is not None:
        n_entries = min(10_000, _sampled_so_far(db_path) + grow)
    declared = plan(n_entries, transfers_top=transfers_top)
    if dry_run:
        return {"dry_run": True, "plan": declared}
    budget = RequestBudget(
        limit=budget_limit if budget_limit is not None
        else declared["requests_total"] + 10
    )
    fetcher = RivalsFetcher(budget, offline=offline)
    summary: dict[str, Any] = {"stages": {name: "not_reached" for name in STAGES}}
    frames: dict[str, pd.DataFrame] = {}
    try:
        frames, summary = collect(
            fetcher, n_entries=n_entries, gw=gw, transfers_top=transfers_top
        )
    except BudgetExhausted as exc:
        # Reachable only outside a stage (bootstrap or the standings pages).
        # Partial samples are still samples -- 400 real squads beat 0 -- but
        # the cohort share SEs scale with what was actually fetched, so the
        # truncation is reported, not smoothed over.
        summary["budget_exhausted"] = str(exc)
    finally:
        fetcher.close()

    if frames:
        summary["write"] = _write(frames, db_path, summary)
    summary["incomplete_stages"] = _incomplete(summary.get("stages") or {})
    summary["requests"] = {
        "limit": budget.limit, "spent": budget.spent,
        "cache_hits": budget.cache_hits, "receipt": budget.receipt(),
    }
    return summary


def main() -> int:
    """Sample, write, and exit non-zero if a stage was starved.

    Same contract as :func:`fpl_edge.ingest.rivals.crawl.main`, and for the
    same reason: a sampler that fetched no transfers should not be able to
    report success.
    """
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
    ap.add_argument("--grow", type=int, default=None,
                    help="deepen the existing sample by this many entries "
                         "(overrides --n; capped at 10000)")
    ap.add_argument("--transfers-top", type=int, default=TRANSFERS_TOP_DEFAULT,
                    help="fetch season transfers for the best-ranked N of the "
                         "sample (0 disables the stage)")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="exit 0 even if a stage was starved; for manual "
                         "partial runs, never for the scheduled job")
    args = ap.parse_args()
    out = run(n_entries=args.n, budget_limit=args.budget, gw=args.gw,
              db_path=args.db, offline=args.offline, dry_run=args.dry_run,
              grow=args.grow, transfers_top=args.transfers_top)
    print(json.dumps(out, indent=2, default=str))
    incomplete = out.get("incomplete_stages") or []
    if incomplete and not args.allow_incomplete:
        print(
            f"FAILED: stages did not complete: {', '.join(incomplete)}. "
            f"Raise --budget, lower --grow, or lower --transfers-top.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
