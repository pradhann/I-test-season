"""The post-gameweek settlement job.

Runs on a schedule (launchd, see deploy/) and after every gameweek finalises it
settles everything that was waiting on results:

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

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.steps)

    def to_json(self) -> str:
        return json.dumps(
            {
                "started_utc": self.started_utc,
                "ok": self.ok,
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


def main() -> int:
    py = sys.executable
    report = JobReport(started_utc=dt.datetime.now(dt.timezone.utc).isoformat())

    _run(report, "ingest_live", [py, "scripts/ingest_live.py"])
    _run(report, "ingest_odds_fixtures", [py, "scripts/ingest_odds.py", "--fixtures"])
    # Settle finished gameweeks into fact_player_fixture -- the audit's
    # highest-leverage gap: without this the current season never gets actuals,
    # so projection_weight can never be earned and claims are never scored.
    # A still-provisional gameweek is refused by its own gate and retried on
    # the next run.
    _run(report, "settle_results", [py, "-m", "fpl_edge.ingest.results"])
    # Providers publish on their own clocks (AIrsenal twice daily, fplform
    # hourly upstream); fetching only at T-30h left the strip amber all week.
    # Nightly + T-30h gives every feed at most a day of staleness.
    _run(report, "ingest_projections",
         [py, "-m", "fpl_edge.ingest.projections.cli", "ingest"])
    # The projection calibration loop, deliberately right behind settlement:
    # score every provider's pre-deadline projections against the gameweek
    # that just settled, then refit projection_weight from the accumulated
    # track record (inverse-MSE, n_obs floor, evidence beside every weight).
    # Idempotent -- already-scored (provider, gw) pairs are skipped -- and
    # honest before settlement: with nothing settled it reports pending and
    # writes nothing, so the weights table can never hold opinions.
    _run(report, "score_projections",
         [py, "-m", "fpl_edge.eval.projection_scoring"])
    # Refit team strength now that results have landed and cache per-fixture
    # difficulty as a parquet next to the database. The fixtures panel reads
    # the artefact instead of paying for a ~1 minute fit inside its 10s budget.
    # Reads via Warehouse.read_copy, writes only the parquet: no lock contention.
    _run(report, "fixture_difficulty",
         [py, "-m", "fpl_edge.models.team_goals.ratings_cache"])
    _run(report, "ingest_odds_props",
         [py, "scripts/ingest_odds.py", "--odds-api", "--max-credits", "30"])
    _run(report, "track_ideas", [py, "-m", "fpl_edge.cli.main", "idea", "track"])
    _run(report, "score_creators",
         [py, "-m", "fpl_edge.ingest.content.pipeline", "score"])
    _run(report, "ingest_content",
         [py, "-m", "fpl_edge.ingest.content.pipeline", "ingest", "--backfill-days", "3"])
    # Budget raised 400 -> 900 on 2026-08-27. At 400 this step spent its entire
    # allowance on the history sweep every single night -- entry/history has a
    # 12h TTL and the job runs daily, so each run re-fetched the same first ~370
    # histories the previous run had already paid for, then raised
    # BudgetExhausted before picks or transfers ran at all. fact_manager_transfer
    # held 0 rows for the life of the job while this step reported ok. The crawl
    # now runs picks and transfers FIRST, caps every stage's share, and exits
    # non-zero if a stage is starved, so the same outage would turn this step
    # red instead of green.
    #
    # 1,100 is sized against the cohort, not guessed: gating the snowball on
    # seed verification (roster.py, defect B9) drops the elite pool from 2,015
    # to ~313 real candidates, which needs ~40 pool + 313 picks + 313 transfers
    # + 313 histories. At 1,100 every stage's reserved share covers its work
    # with headroom, and the run is ~20 min at the polite 1.1s pace -- inside
    # _run's 1800s timeout. Picks for a finished gameweek are cached forever,
    # so steady-state spend is far below this.
    _run(report, "crawl_elite",
         [py, "-m", "fpl_edge.ingest.rivals.crawl", "--budget", "1100"])
    # The named elite (Crellin et al.): verified IDs, full picks + transfer
    # history. Cheap (~4 requests per manager) and cached, so a re-run after a
    # crash costs almost nothing.
    _run(report, "crawl_elite_named",
         [py, "-m", "fpl_edge.ingest.rivals.elite", "--budget", "200"])
    # Deepen the top-of-overall sample by 300 entries a night toward the full
    # top-10k. Resumable by construction: finished-gameweek picks are cached
    # forever, so only the new tail costs requests, and the budget hard-stops
    # the run rather than letting it grow silently.
    #
    # --grow lowered 500 -> 300 and --budget raised 700 -> 1200 on 2026-08-27,
    # because this sampler now also fetches season transfers. It previously
    # fetched none, which is why the only cohort in the warehouse with real
    # pick coverage had no transfer data at all.
    #
    # The arithmetic, since it is what the stage shares have to cover: ~37
    # standings requests, ~300 picks for the newly grown tail (the existing
    # sample's picks are immutable and cached forever, so they are free), and
    # 300 transfers. Transfers have a 3h TTL and so re-cost a request per
    # manager EVERY night, which is why they are capped in rank order rather
    # than run over the whole sample -- uncapped, growing toward 10,000 would
    # mean 10,000 fresh requests nightly, forever. At --budget 1200 the stage
    # caps bound the worst case to ~820 requests (~15 min), well inside _run's
    # 1800s timeout even on a completely cold cache.
    _run(report, "crawl_top10k_sample",
         [py, "-m", "fpl_edge.ingest.rivals.top1k", "--grow", "300",
          "--budget", "1200", "--transfers-top", "300"])
    _run(report, "intel", [py, "-m", "fpl_edge.intel.cli", "collect"])
    _run(report, "retro_report", [py, "scripts/retro_report.py"])
    _run(report, "weekly_idea_report", [py, "scripts/weekly_idea_report.py"])

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = LOG_DIR / f"post_gw_{stamp}.json"
    out.write_text(report.to_json())
    print(report.to_json())
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
