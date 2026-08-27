"""The crawl entry point. One command, one declared budget, one receipt.

    uv run python -m fpl_edge.ingest.rivals.crawl --budget 900

Everything about this run is meant to be boring and repeatable. It builds the
candidate pool, fetches the squads and transfers of every candidate whose
gameweek has locked, and then sweeps season histories with whatever budget is
left. Re-running it the next day costs almost nothing because the cache holds,
which is the behaviour you want from something scheduled.

Stages, and why the order and the caps are load-bearing
-------------------------------------------------------
The run is divided into named stages (:data:`STAGES`), each with a reserved
slice of the budget (:data:`STAGE_SHARE`). Two things follow from that, and
both were bought with an outage:

* **No stage can starve the ones after it.** History used to run first and
  uncapped; on a 2,015-candidate pool with a 400-request budget it spent every
  request and raised before picks or transfers were ever called, so
  ``fact_manager_transfer`` sat at zero rows indefinitely.
* **A stage that does not finish is named in the receipt and fails the
  process.** ``summary["stages"]`` starts with every expected stage marked
  ``not_reached``, ``summary["incomplete_stages"]`` lists whatever is not ``ok``
  or a legitimate skip, and :func:`main` exits non-zero when that list is
  non-empty. The previous behaviour -- exit 0 while doing a quarter of the job
  -- is what let the outage run for days.
* **A stage that had nothing to do does not get to say "ok".** The stage
  statuses only mean anything if they are true, and two ways of doing nothing
  used to read as success: a pool of zero candidates (every later stage runs
  over an empty list, spends nothing, raises nothing, reports ``ok``), and a
  write that never committed (``_write`` returns ``{"status": "locked"}`` after
  six failed attempts and nobody read it). Both now land in
  ``summary["failures"]``, which :func:`main` treats as fatal and which
  ``--allow-incomplete`` deliberately does NOT forgive -- see
  :func:`_write_failures`. The rule the two share: for every stage, ask "if
  this silently did nothing, what would be different?", and if the answer is
  "nothing", that is the assertion that is missing.

The receipt printed at the end is the number to quote when someone asks how hard
we hit the API. It is itemised by endpoint kind, and cache hits are reported
separately so a cheap re-run is visibly cheap rather than merely claimed to be.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals import history as history_mod
from fpl_edge.ingest.rivals import picks as picks_mod
from fpl_edge.ingest.rivals import roster, schema
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget, RivalsFetcher
from fpl_edge.store import Warehouse
from fpl_edge.store.warehouse import WarehouseLockedError

#: Every stage this crawl is expected to complete, in the order it runs them.
#: The list is declared rather than inferred so that a stage which never
#: executes at all is still *named* in the receipt as "not_reached" -- the
#: failure mode this module spent two days in was a stage that was silently
#: absent, and something absent cannot be noticed unless it was expected.
STAGES: tuple[str, ...] = ("pool", "picks", "transfers", "history")

#: Share of the run's request budget each stage may spend, applied in order.
#:
#: **Why picks and transfers come before the history sweep, and why history is
#: capped at all.** History is by far the most expensive stage (one request per
#: candidate) and by far the least time-critical: a completed season's final
#: rank is immutable and can be read any week, and the current-season gameweek
#: rows are re-derivable from the standings the top-1k sampler already pages.
#: Picks and transfers are the opposite -- they become readable at the deadline
#: and they are what every differential and copying model in the repo actually
#: consumes this week.
#:
#: Before 2026-08-27 history ran FIRST and uncapped, against a 2,015-candidate
#: pool on a 400-request budget with a 12-hour history TTL. That combination is
#: not "slow", it is *unreachable*: each nightly run re-fetched the same first
#: ~370 histories the previous run had already fetched (their cache entries
#: having expired), exhausted the budget, and raised BudgetExhausted before
#: ingest_picks and ingest_transfers were ever called. fact_manager_transfer
#: therefore held zero rows, for every day the job had ever run, while the job
#: reported success.
#:
#: Reordering alone does not fix it -- it just moves the starvation one stage
#: down, since picks for 2,015 candidates also exceeds 400. What fixes it is
#: that no stage may spend the whole budget. Each gets a reserved share, and a
#: stage that hits its share is *contained and reported* rather than allowed to
#: consume the run. History deliberately takes the remainder, so on a run where
#: picks are already cached (they are immutable and cached forever) history
#: gets nearly the whole budget and the sweep completes.
#: The shares are sized against the cohort they have to cover, not picked to
#: look tidy. With the snowball gated on seed verification (see roster.py) the
#: elite pool is ~313 candidates -- mini-league rivals, published winners, and
#: the pinned LiveFPL list -- so at the scheduled budget of 1,100 the picks
#: stage may spend 385 and the transfer stage 330, both comfortably above 313,
#: and history takes the ~430 left over. If the pool grows, these numbers are
#: the thing to re-derive; a share that no longer covers its stage shows up
#: immediately as that stage in ``incomplete_stages``.
STAGE_SHARE: dict[str, float] = {
    "pool": 0.10,
    "picks": 0.35,
    "transfers": 0.30,
    "history": 1.00,   # the remainder: whatever the three above did not spend
}


@contextmanager
def _stage(
    budget: RequestBudget, name: str, stages: dict[str, str], share: float
) -> Iterator[None]:
    """Run one stage under a reserved slice of the budget.

    Implemented by temporarily lowering ``budget.limit`` rather than by adding a
    second counter, so the enforcement is the *same* enforcement as everywhere
    else -- there is one place that can refuse a request, and it is
    :meth:`RequestBudget.charge`. The original limit is restored on the way out
    so the receipt still reports the run's real budget.

    A :class:`BudgetExhausted` raised inside a stage is caught here and recorded
    against that stage. It does not abort the run, because the stages after it
    have their own reserved requests and a partial crawl of four stages beats a
    complete crawl of one.
    """
    prior = budget.limit
    budget.limit = min(prior, budget.spent + max(1, int(round(prior * share))))
    stages[name] = "running"
    try:
        yield
    except BudgetExhausted as exc:
        stages[name] = f"incomplete: {exc}".split(". Spent")[0]
    else:
        stages[name] = "ok"
    finally:
        budget.limit = prior


def _incomplete(stages: dict[str, str]) -> list[str]:
    """Stages that did not finish, excluding legitimate no-op skips.

    A stage that the schedule genuinely has nothing for -- picks before any
    deadline has passed -- reports ``skipped:`` and is fine. A stage that ran
    out of budget, raised, or was never reached at all is an outage, and the
    whole point of naming it here is that :func:`main` can then exit non-zero
    and the scheduled job can go red.
    """
    return sorted(
        name for name, status in stages.items()
        if not (status == "ok" or status.startswith("skipped:"))
    )


def _write_failures(summary: dict[str, Any], *, skipped: bool = False) -> list[str]:
    """Reasons the commit step must not be allowed to present as success.

    ``_write`` is the one stage whose failure leaves *no other trace*. It
    returns ``{"status": "locked"}`` when the DuckDB write lock never frees,
    and until 2026-08-27 nothing read that: all four stages reported "ok",
    ``incomplete_stages`` was empty, the process exited 0, and post_gw recorded
    ``crawl_elite ok=true`` -- for a run that had written zero rows. That is
    the same outage the stage accounting was built to stop, reached through a
    door the stage accounting does not watch.

    Three distinct silent-nothings are named here, because each one asks the
    same question -- *if this step did nothing, what would be different?* --
    and each one used to answer "nothing":

    * ``write`` absent entirely: the commit was never even attempted (top1k
      skipped the call whenever ``frames`` was empty, so the receipt simply had
      no ``write`` key and no reader noticed the difference).
    * ``status != "ok"``: the lock never freed, or the write raised.
    * ``status == "ok"`` with an empty ``rows`` map: the warehouse was opened,
      migrated and closed without a single row being appended. A crawl that
      built a real pool always writes at least ``dim_manager``, so zero rows
      means the frames were empty and the "success" is vacuous.

    ``skipped`` marks a run that legitimately had nothing to commit -- the
    top-1k sampler before the first gameweek is scored -- where an empty write
    is the correct outcome rather than an outage.
    """
    write = summary.get("write")
    if write is None:
        return ["write: the commit step never ran, so nothing was written"]
    status = str(write.get("status") or "unknown")
    rows = write.get("rows") or {}
    if status == "ok" and rows:
        return []
    if skipped and not rows:
        return []
    if status != "ok":
        detail = str(write.get("error") or "").strip()
        return [f"write: {status}" + (f": {detail}" if detail else "")]
    return ["write: committed zero rows; the frames were all empty"]


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
    # Named up front, so a stage that never executes is visible as
    # "not_reached" instead of being invisible by absence.
    stages: dict[str, str] = {name: "not_reached" for name in STAGES}
    summary["stages"] = stages
    # Fetch everything BEFORE opening the warehouse. DuckDB permits a single
    # writer, and a crawl that holds the lock for the fifteen minutes it spends
    # sleeping politely between requests would block every other ingest in the
    # repo for no reason. Collect first, hold the lock for the seconds it takes
    # to write.
    frames: dict[str, Any] = {}
    entry_ids: list[int] = []
    chip_frames: list[pd.DataFrame] = []
    #: Failures that no stage status can express -- an empty pool, a write that
    #: never committed. Kept separate from ``incomplete_stages`` because
    #: ``--allow-incomplete`` forgives a starved stage and must never forgive
    #: these.
    failures: list[str] = []
    try:
        season, deadlines = _season_and_deadlines(fetcher)
        summary["season"] = season

        with _stage(budget, "pool", stages, STAGE_SHARE["pool"]):
            candidates, pool_report, managers, memberships = roster.build_pool(
                fetcher, max_candidates=max_candidates
            )
            summary["pool"] = vars(pool_report)
            frames["dim_manager"] = managers
            frames["dim_manager_league"] = memberships
            entry_ids = [c.entry_id for c in candidates]

        # A pool of zero candidates is not a cheap run, it is a run that cannot
        # do anything -- and until 2026-08-27 it was the quietest failure in
        # the module. ingest_picks([]), ingest_transfers([]) and
        # ingest_histories([]) each spend nothing, raise nothing and return
        # empty frames, so all three stages left `_stage` through its
        # ``else: stages[name] = "ok"`` branch. The receipt read: four stages
        # ok, no incomplete stages, one request spent, exit 0. PoolReport
        # already counted the candidates; nothing read the count.
        #
        # It is not hypothetical either. Gating the snowball on seed
        # verification (roster.py) now rejects all twenty expert seeds, so the
        # pool is mini-league + published winners + ELITE_1000 and nothing
        # else. One more tightening there and it is empty.
        if stages["pool"] == "ok" and not entry_ids:
            stages["pool"] = ("failed: build_pool returned zero candidates; "
                              "there is nobody to crawl")
            failures.append(
                "pool: build_pool returned zero candidates, so every stage "
                "after it would have been a no-op reporting success"
            )

        now = dt.datetime.now(dt.timezone.utc)
        live_gws = [gw for gw, d in deadlines.items() if d <= now]

        # -- picks, then transfers, then history. See STAGE_SHARE. ------------
        # Everything downstream consumes ``entry_ids``, so if the pool stage did
        # not deliver one, the honest status for those stages is "not_reached",
        # not "ok". Running them anyway is what produced a receipt in which
        # three of four stage statuses were actively false.
        if stages["pool"] != "ok":
            unreached = f"not_reached: the pool stage did not deliver a cohort ({stages['pool']})"
            for name in ("picks", "transfers", "history"):
                stages[name] = unreached
        else:
            if not with_picks:
                stages["picks"] = "skipped: --no-picks"
                stages["transfers"] = "skipped: --no-picks"
            elif not live_gws:
                skip = ("skipped: no gameweek deadline has passed; the picks "
                        "endpoint returns 404 for every entry")
                stages["picks"] = skip
                summary["picks"] = {"skipped": skip}
                # Transfers do NOT depend on a deadline having passed -- the
                # endpoint answers 200 with [] all pre-season -- but with no
                # locked gameweek there is nothing datable to record, so this
                # is a real no-op rather than an outage.
                stages["transfers"] = (
                    "skipped: no locked gameweek to date transfers against")
            else:
                with _stage(budget, "picks", stages, STAGE_SHARE["picks"]):
                    p, c, stats = picks_mod.ingest_picks(
                        fetcher, entry_ids, live_gws, season=season,
                        deadlines=deadlines, now=now,
                    )
                    summary["picks"] = stats
                    frames["fact_manager_pick"] = p
                    if not c.empty:
                        chip_frames.append(c)
                with _stage(budget, "transfers", stages, STAGE_SHARE["transfers"]):
                    t, tstats = picks_mod.ingest_transfers(
                        fetcher, entry_ids, season=season, deadlines=deadlines
                    )
                    summary["transfers"] = tstats
                    frames["fact_manager_transfer"] = t

            with _stage(budget, "history", stages, STAGE_SHARE["history"]):
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
                if not chips.empty:
                    chip_frames.append(chips)
    except BudgetExhausted as exc:
        # Reachable only outside a stage (the bootstrap request). Everything
        # fetched is still committed and the next run resumes from cache.
        summary["budget_exhausted"] = str(exc)
    finally:
        fetcher.close()

    if chip_frames:
        frames["fact_manager_chip"] = pd.concat(
            chip_frames, ignore_index=True).drop_duplicates()

    summary["write"] = _write(frames, db_path, summary)

    # The line a scheduled job and a human both read first. An empty list is
    # the only acceptable steady state.
    summary["incomplete_stages"] = _incomplete(stages)
    # ...and the line that catches everything a stage status cannot express.
    # A locked write and an empty pool both used to leave `incomplete_stages`
    # empty while the run had done nothing at all.
    summary["failures"] = failures + _write_failures(summary)
    summary["requests"] = {
        "limit": budget.limit,
        "spent": budget.spent,
        "cache_hits": budget.cache_hits,
        "by_kind": budget.by_kind,
        "receipt": budget.receipt(),
    }
    return summary


def main() -> int:
    """Run the crawl; exit non-zero if any expected stage did not complete.

    The exit code is the whole point. This job ran for days consuming its
    entire budget on the history sweep and never reaching transfers, and
    reported ``ok: true`` in every receipt, because "the process did not
    crash" was the only thing being checked. A stage that fails invisibly is
    not a degraded success; it is an outage that nobody has been told about.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=900,
                    help="hard cap on network requests; the run stops rather than exceed it")
    ap.add_argument("--max-candidates", type=int, default=600)
    ap.add_argument("--db", default=None)
    ap.add_argument("--no-picks", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="serve only from cache; raises if something is missing")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="exit 0 even if a stage was starved; for manual "
                         "partial runs, never for the scheduled job")
    args = ap.parse_args()
    out = run(
        budget_limit=args.budget,
        max_candidates=args.max_candidates,
        db_path=args.db,
        with_picks=not args.no_picks,
        offline=args.offline,
    )
    print(json.dumps(out, indent=2, default=str))
    incomplete = out.get("incomplete_stages") or []
    # Hard failures -- an empty pool, a write that never committed -- are NOT
    # covered by --allow-incomplete. That flag exists so a human can take a
    # deliberate partial crawl; it was never meant to say "it is fine that we
    # wrote nothing", and a run that wrote nothing is not a partial anything.
    failures = out.get("failures") or []
    if failures:
        print(
            f"FAILED: {'; '.join(failures)}. "
            f"Nothing was committed; re-run to replay the archived bodies "
            f"for zero requests.",
            file=sys.stderr,
        )
        return 1
    if incomplete and not args.allow_incomplete:
        print(
            f"FAILED: stages did not complete: {', '.join(incomplete)}. "
            f"Raise --budget or narrow --max-candidates.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
