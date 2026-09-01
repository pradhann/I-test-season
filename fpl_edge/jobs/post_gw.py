"""The post-gameweek settlement job.

Runs on a schedule (launchd, see deploy/) and after every gameweek finalises it
settles everything that was waiting on results.

PARITY NOTE (PIPELINES.md §6.2): the same chain now also runs as the
``post_gw_settlement`` registry task inside the deadline-DAG tick
(fpl_edge/pipelines/registry.py), iterating :func:`settlement_steps` -- THE
step list -- so the two paths cannot drift. This CLI and its plist stay live
until the owner has watched a few days of side-by-side outcomes; every step
is idempotent, so the doubled run is safe. The chain:

1. refresh the live FPL snapshot (prices, ownership, availability)
2. pull forward-looking odds for the next fixtures
3. write per-(idea, gameweek) observation rows and resolve finished theses
4. score content creators' claims against what actually happened
5. crawl elite managers' now-public picks and transfers
6. re-render the retro-analysis report

Design rules:

* **Every step is isolated.** One failure is reported and the rest still run;
  a partial settlement beats none, and the failure lands in the summary rather
  than a dead process.
* **Idempotent.** Every step re-run produces the same state (appends dedupe,
  resolutions key on (idea, gw)), so a crashed run is fixed by running again.
* **Single writer.** Steps run sequentially in one process precisely because
  DuckDB allows one writer; parallelising them would deadlock the schedule.
* **A failure reaches a human.** The report below is written to
  ``data/warehouse/jobs/`` and printed, and until 2026-08-27 that was the end
  of it: nothing in the repo read either, the launchd plist has no ``KeepAlive``
  and no failure action, so "post_gw will notice a failed crawl" was true and
  useless -- it noticed, and told nobody. A run with a failed step now enqueues
  one message on the same durable outbox the deadline DAG uses
  (:mod:`fpl_edge.jobs.outbox`) and flushes it. A clean run enqueues nothing,
  because an alert that arrives nightly is an alert nobody reads.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

LOG_DIR = Path("data/warehouse/jobs")

#: ``platform_delivery.monitor`` for this job's failure alerts. Shares the
#: outbox with the deadline DAG rather than growing a second delivery path:
#: one table, one flush, one dedupe key.
ALERT_MONITOR = "post_gw"

#: How many failed step names go in the alert title before it is elided. The
#: body always lists every one of them.
_TITLE_STEPS = 4


@dataclass
class StepResult:
    name: str
    ok: bool
    seconds: float
    detail: str = ""


@dataclass
class JobReport:
    started_utc: str
    steps: list[StepResult] = field(default_factory=list)
    #: What happened to the failure alert. Recorded in the report so that
    #: "we tried to tell you and Telegram was not configured" is visible,
    #: rather than being a second silence layered on the first.
    alert: str = ""

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def failed(self) -> list[StepResult]:
        return [s for s in self.steps if not s.ok]

    def to_json(self) -> str:
        return json.dumps(
            {
                "started_utc": self.started_utc,
                "ok": self.ok,
                "alert": self.alert,
                "steps": [vars(s) for s in self.steps],
            },
            indent=1,
        )


def _run(report: JobReport, name: str, argv: list[str]) -> None:
    """One step as a subprocess: a segfault or lock hang cannot kill the job."""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=1800, check=False,
        )
        ok = proc.returncode == 0
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        report.steps.append(StepResult(
            name=name, ok=ok, seconds=round(time.monotonic() - t0, 1),
            detail=" | ".join(tail)[-400:],
        ))
    except Exception:  # noqa: BLE001 - the report is the error channel
        report.steps.append(StepResult(
            name=name, ok=False, seconds=round(time.monotonic() - t0, 1),
            detail=traceback.format_exc()[-400:],
        ))


def alert_text(report: JobReport) -> tuple[str, str]:
    """The (title, body) a human gets. Named steps, not a count.

    The title has to be readable on a lock screen, so it names the failed steps
    up to :data:`_TITLE_STEPS`; the body carries every failure with the tail of
    its output, which is the part that says *which* stage was starved or that
    the warehouse write was locked.
    """
    failed = report.failed
    names = [s.name for s in failed]
    shown = ", ".join(names[:_TITLE_STEPS])
    if len(names) > _TITLE_STEPS:
        shown += f" (+{len(names) - _TITLE_STEPS} more)"
    title = f"post_gw FAILED: {shown}"
    body = "\n".join(
        f"- {s.name} ({s.seconds}s): {s.detail or 'no output'}" for s in failed
    )
    return title, f"{len(failed)}/{len(report.steps)} steps failed.\n\n{body}"


def notify_failures(
    report: JobReport,
    *,
    db_path: str | None = None,
    transport=None,
    config=None,
    now: dt.datetime | None = None,
) -> str:
    """Put a failed run where the owner will actually see it. Returns a note.

    Deliberately the DAG's mechanism and not a new one: :func:`outbox.deliver`
    writes one row, :func:`outbox.flush_outbox` pushes it to the allowlisted
    Telegram chats and stamps it, and a send that fails leaves the row pending
    for the next flush -- including the DAG's, which runs every few minutes.
    So a Telegram outage delays the alert rather than eating it.

    Two things this must never do, both of which would make it worse than
    nothing. It must not alert on success (a nightly "all fine" trains you to
    ignore the channel, and then the one that matters is ignored too), and it
    must not be able to fail the job it is reporting on -- a broken alert
    channel is recorded in the returned note and in the report, never raised.
    """
    if report.ok:
        return "alert: not sent (every step ok)"

    title, body = alert_text(report)
    try:
        from fpl_edge.jobs import outbox
        from fpl_edge.store import Warehouse

        wh = (Warehouse(lock_timeout_s=180.0) if db_path is None
              else Warehouse(db_path, lock_timeout_s=180.0))
        try:
            outbox.deliver(
                wh, monitor=ALERT_MONITOR, kind="alert",
                title=title, body=body, now=now,
            )
            flush = outbox.flush_outbox(
                wh, transport=transport, config=config, now=now
            )
        finally:
            wh.close()
    except Exception:  # noqa: BLE001 - the alert must not fail the job
        return f"alert: enqueue failed: {traceback.format_exc()[-200:]}"
    return f"alert: enqueued {title!r}; {flush.render()}"


def settlement_steps(py: str) -> list[tuple[str, list[str]]]:
    """THE ordered settlement chain, as (name, argv) rows.

    This list is the single source of truth for both execution paths during
    the parity window (PIPELINES.md §6.2): ``main`` below (the launchd CLI)
    and the ``post_gw_settlement`` registry task
    (:func:`fpl_edge.pipelines.registry.run_post_gw_settlement`) iterate this
    same list, so the two cannot drift. Order is a dependency statement --
    settlement and scoring before the crawls and reports that read them --
    and stays a flat sequence in one process because DuckDB permits exactly
    one writer.
    """
    return [
        ("ingest_live", [py, "scripts/ingest_live.py"]),
        ("ingest_odds_fixtures", [py, "scripts/ingest_odds.py", "--fixtures"]),
        # Settle finished gameweeks into fact_player_fixture -- the audit's
        # highest-leverage gap: without this the current season never gets
        # actuals, so projection_weight can never be earned and claims are
        # never scored. A still-provisional gameweek is refused by its own
        # gate and retried on the next run.
        ("settle_results", [py, "-m", "fpl_edge.ingest.results"]),
        # Providers publish on their own clocks (AIrsenal twice daily, fplform
        # hourly upstream); fetching only at T-30h left the strip amber all
        # week. Nightly + T-30h gives every feed at most a day of staleness.
        ("ingest_projections",
         [py, "-m", "fpl_edge.ingest.projections.cli", "ingest"]),
        # The projection calibration loop, deliberately right behind
        # settlement: score every provider's pre-deadline projections against
        # the gameweek that just settled, then refit projection_weight from
        # the accumulated track record (inverse-MSE, n_obs floor, evidence
        # beside every weight). Idempotent -- already-scored (provider, gw)
        # pairs are skipped -- and honest before settlement: with nothing
        # settled it reports pending and writes nothing, so the weights table
        # can never hold opinions.
        ("score_projections", [py, "-m", "fpl_edge.eval.projection_scoring"]),
        # Refit team strength now that results have landed and cache
        # per-fixture difficulty as a parquet next to the database. The
        # fixtures panel reads the artefact instead of paying for a ~1 minute
        # fit inside its 10s budget. Reads via Warehouse.read_copy, writes
        # only the parquet: no lock contention.
        ("fixture_difficulty",
         [py, "-m", "fpl_edge.models.team_goals.ratings_cache"]),
        # The nightly odds top-up. Two things here were an outage until
        # 2026-08-28.
        #
        # 1. ``--max-credits 30``. One refresh costs 22 credits unrestricted,
        #    and the cap was 30 against a month that had already spent 67.
        #    Every run from 2026-08-19 onward refused before spending
        #    anything, the script caught the refusal and exited 0, and this
        #    step recorded ok=true for nine consecutive nights. By the GW2
        #    deadline anytime_scorer was 206 hours old and team_totals 191.
        #    The script now refuses a cap below MIN_SANE_MONTHLY_CAP at the
        #    flag, and exits non-zero on a refusal, so neither half of that
        #    can happen silently again.
        # 2. It ran unconditionally and priced two gameweeks. With the
        #    deadline DAG firing a T-36h/T-12h/T-5h ladder, a nightly full
        #    refresh would re-buy cards the ladder just bought.
        #    ``--max-age-hours 48`` makes this a genuine no-op (an explicit
        #    ``skipped:``, not a fake ok) whenever the ladder has already
        #    covered the week, and a real refresh in a quiet one -- about
        #    twice a week, 24 credits.
        ("ingest_odds_props",
         [py, "scripts/ingest_odds.py", "--odds-api", "--max-age-hours", "48"]),
        ("track_ideas", [py, "-m", "fpl_edge.cli.main", "idea", "track"]),
        ("score_creators",
         [py, "-m", "fpl_edge.ingest.content.pipeline", "score"]),
        ("ingest_content",
         [py, "-m", "fpl_edge.ingest.content.pipeline", "ingest",
          "--backfill-days", "3"]),
        # Budget raised 400 -> 900 on 2026-08-27. At 400 this step spent its
        # entire allowance on the history sweep every single night --
        # entry/history has a 12h TTL and the job runs daily, so each run
        # re-fetched the same first ~370 histories the previous run had
        # already paid for, then raised BudgetExhausted before picks or
        # transfers ran at all. fact_manager_transfer held 0 rows for the
        # life of the job while this step reported ok. The crawl now runs
        # picks and transfers FIRST, caps every stage's share, and exits
        # non-zero if a stage is starved, so the same outage would turn this
        # step red instead of green.
        #
        # 1,100 is sized against the cohort, not guessed: gating the snowball
        # on seed verification (roster.py, defect B9) drops the elite pool
        # from 2,015 to ~313 real candidates, which needs ~40 pool + 313
        # picks + 313 transfers + 313 histories. At 1,100 every stage's
        # reserved share covers its work with headroom, and the run is ~20
        # min at the polite 1.1s pace -- inside _run's 1800s timeout. Picks
        # for a finished gameweek are cached forever, so steady-state spend
        # is far below this.
        ("crawl_elite",
         [py, "-m", "fpl_edge.ingest.rivals.crawl", "--budget", "1100"]),
        # The named elite (Crellin et al.): verified IDs, full picks +
        # transfer history. Cheap (~4 requests per manager) and cached, so a
        # re-run after a crash costs almost nothing.
        ("crawl_elite_named",
         [py, "-m", "fpl_edge.ingest.rivals.elite", "--budget", "200"]),
        # Deepen the top-of-overall sample by 300 entries a night toward the
        # full top-10k. Resumable by construction: finished-gameweek picks
        # are cached forever, so only the new tail costs requests, and the
        # budget hard-stops the run rather than letting it grow silently.
        #
        # --grow lowered 500 -> 300 and --budget raised 700 -> 1200 on
        # 2026-08-27, because this sampler now also fetches season transfers.
        # It previously fetched none, which is why the only cohort in the
        # warehouse with real pick coverage had no transfer data at all.
        #
        # The arithmetic, since it is what the stage shares have to cover:
        # ~37 standings requests, ~300 picks for the newly grown tail (the
        # existing sample's picks are immutable and cached forever, so they
        # are free), and 300 transfers. Transfers have a 3h TTL and so
        # re-cost a request per manager EVERY night, which is why they are
        # capped in rank order rather than run over the whole sample --
        # uncapped, growing toward 10,000 would mean 10,000 fresh requests
        # nightly, forever. At --budget 1200 the stage caps bound the worst
        # case to ~820 requests (~15 min), well inside _run's 1800s timeout
        # even on a completely cold cache.
        ("crawl_top10k_sample",
         [py, "-m", "fpl_edge.ingest.rivals.top1k", "--grow", "300",
          "--budget", "1200", "--transfers-top", "300"]),
        ("intel", [py, "-m", "fpl_edge.intel.cli", "collect"]),
        ("retro_report", [py, "scripts/retro_report.py"]),
        ("weekly_idea_report", [py, "scripts/weekly_idea_report.py"]),
    ]


def main() -> int:
    py = sys.executable
    report = JobReport(started_utc=dt.datetime.now(dt.timezone.utc).isoformat())

    for name, argv in settlement_steps(py):
        _run(report, name, argv)

    # Every step has run and released the write lock by now, so this is the
    # safe point to take it for the one row the alert needs.
    report.alert = notify_failures(report)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = LOG_DIR / f"post_gw_{stamp}.json"
    out.write_text(report.to_json())
    print(report.to_json())
    if not report.ok:
        print(
            f"FAILED: {', '.join(s.name for s in report.failed)} | {report.alert}",
            file=sys.stderr,
        )
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
