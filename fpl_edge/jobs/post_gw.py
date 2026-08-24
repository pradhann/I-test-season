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
    _run(report, "crawl_elite",
         [py, "-m", "fpl_edge.ingest.rivals.crawl", "--budget", "400"])
    # The named elite (Crellin et al.): verified IDs, full picks + transfer
    # history. Cheap (~4 requests per manager) and cached, so a re-run after a
    # crash costs almost nothing.
    _run(report, "crawl_elite_named",
         [py, "-m", "fpl_edge.ingest.rivals.elite", "--budget", "200"])
    # Deepen the top-of-overall sample by 500 entries a night toward the full
    # top-10k. Resumable by construction: finished-gameweek picks are cached
    # forever, so only the new tail costs requests, and the budget hard-stops
    # the run rather than letting it grow silently.
    _run(report, "crawl_top10k_sample",
         [py, "-m", "fpl_edge.ingest.rivals.top1k", "--grow", "500",
          "--budget", "700"])
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
