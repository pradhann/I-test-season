"""The task vocabulary: what a task is handed, what it returns, when it is due.

A leaf. It imports the standard library and ``fpl_edge.store`` and nothing
else, which is the whole point of its existence.

``fpl_edge/pipelines/registry.py`` needed these eight names out of
``fpl_edge/jobs/deadline_dag.py`` (``registry.py:52-53``, ``health.py:41``,
``runner.py:38``), and ``deadline_dag`` needed ``registry.stale_window_of`` and
``registry.registry_due`` back, so it imported the registry lazily inside two
function bodies with a comment explaining the cycle. That was C1 loop A
(ARCHITECTURE_REVIEW.md check 6). Pulling the vocabulary down here inverts the
dependency: both packages now depend on this module and neither on the other.

Import direction after the move, top to bottom, no edge upward::

    jobs -> pipelines.{registry,runner,health} -> pipelines.tasks
         -> pipelines.contracts -> store

Nothing in this module may import ``fpl_edge.jobs`` or
``fpl_edge.pipelines.registry``. Either one puts the cycle back.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import re
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from fpl_edge.store import Warehouse

UTC = dt.UTC

SEASON = "2026-27"


#: The odds refresh task, and the only task in this module that fires MORE THAN
#: ONCE per deadline. It gets its own ladder rather than a slot in
#: :data:`DEADLINE_OFFSETS` because that dict answers "when does this task
#: happen", one instant per task, and odds are the one input whose answer is
#: "repeatedly, accelerating toward the deadline".
ODDS_TASK = "odds_refresh"

#: The one wall-clock task. FPL's price run lands around 01:30 UK; 02:00 local
#: is after it and before anybody is awake to act on stale numbers.
NIGHTLY_TASK = "price_radar"
NIGHTLY_LOCAL_HOUR = 2

#: A firing due longer ago than its window is recorded and skipped, never run.
#:
#: The window is PER TASK because staleness means different things per task, and
#: one global value gets one of them wrong. This was found in production: the
#: machine (a laptop) slept through the T-30h refresh, and a flat 2h window
#: discarded a task whose whole job is to ingest data that is still perfectly
#: useful 7 hours late and 23 hours before the deadline.
#:
#: The rule: a task whose value DECAYS WITH THE DEADLINE gets a tight window; a
#: task that is an idempotent refresh gets a generous one, bounded only by the
#: point at which running it would no longer inform the decision.
STALE_WINDOWS: dict[str, dt.timedelta] = {
    # Pure ingest + digest. Running it late still refreshes projections, odds and
    # injury news; the only true expiry is the deadline itself.
    "presser_projection_refresh": dt.timedelta(hours=20),
    # Price changes resolve nightly. A radar delivered at breakfast is still
    # actionable; one delivered a day late is describing yesterday's prices.
    "price_radar": dt.timedelta(hours=8),
    # A plan delivered after the deadline is worthless, but the artefact it
    # reads is timestamped, so a late delivery is honest about its own age.
    "final_solve_delivery": dt.timedelta(hours=3),
    # Confirmed XI vs picked captain. Worthless the moment the deadline passes.
    "lineup_captain_check": dt.timedelta(minutes=75),
    # An odds refresh is an idempotent, credit-metered fetch whose value decays
    # with the deadline but never inverts: prices fetched two hours late are
    # still the current prices. The window is deliberately SHORTER THAN THE
    # SMALLEST GAP BETWEEN RUNGS (6h < the 7h from T-12h to T-5h) so a
    # slept-through rung is recorded skipped_stale and the next rung does the
    # work. With a wider window a laptop that woke at T-5h would fire T-12h
    # and T-5h back to back and pay twice for the same cards.
    "odds_refresh": dt.timedelta(hours=6),
}

#: Fallback for a task not named above.
STALE_WINDOW = dt.timedelta(hours=2)

#: How far back a tick looks for firings it never saw. Bounds how many
#: skipped_stale rows a week-long outage can write, while still leaving an
#: honest record that the machine was down through a deadline.
LOOKBACK = dt.timedelta(hours=36)

#: How long one subprocess step may run before ``run_step`` kills it.
STEP_TIMEOUT_S = 900


# --------------------------------------------------------------------------
# Time: due-instant arithmetic. Pure functions, no I/O -- this is the seam.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Due:
    """One firing that the schedule says should have happened by ``now``."""

    task: str
    season: str
    gw: int
    due_utc: dt.datetime
    deadline_utc: dt.datetime | None
    stale: bool

    def key(self) -> tuple[str, str, int, dt.datetime]:
        return (self.task, self.season, self.gw, self.due_utc)

# --------------------------------------------------------------------------
# Task results and the isolated-subprocess step runner
# --------------------------------------------------------------------------


#: What a step's detail says when it exited 0 and printed nothing at all.
#: A silent success is a real outcome and not a failure, but it is also the
#: shape a step takes when it has stopped doing any work, so the record says
#: so instead of leaving the panel with an empty cell.
NO_OUTPUT = "no output"


@dataclass
class Step:
    name: str
    ok: bool
    seconds: float
    detail: str = ""
    #: True when the step exited 0 with nothing on stdout or stderr.
    quiet: bool = False


@dataclass
class TaskResult:
    """What a task decided. ``outcome`` is one of the migration's six values."""

    outcome: str
    detail: str = ""
    title: str = ""
    body: str = ""
    kind: str = "digest"
    steps: list[Step] = field(default_factory=list)
    #: (code, metric, value) rows written to dag_observation on EVERY run,
    #: including quiet ones -- the series a threshold is tuned against.
    observations: list[tuple[int, str, float]] = field(default_factory=list)
    #: Counts a task wants carried into its fetch_run ledger row (the tick
    #: writes one per executed task; PIPELINES.md §4.2). rows_written /
    #: rows_unchanged in the ledger's vocabulary -- e.g. the audio-retention
    #: sweep reports files deleted here.
    ledger_written: int = 0
    ledger_unchanged: int = 0

    @property
    def delivers(self) -> bool:
        """Does this result have an alert the outbox should send?

        ``error`` is here alongside ``delivered`` because those are two
        different questions and one flag was answering both. A failed task
        that wants to alert used to have to return ``delivered``, which
        ``pipelines.runner.LEDGER_STATUS`` maps to ledger status ``ok`` -- so
        every nightly transcription failure since 2026-09-01 sits in
        ``fetch_run`` as a successful run. Outcome now says what happened;
        the presence of a title says whether to tell anyone.
        """
        return self.outcome in ("delivered", "error") and bool(self.title)

    @property
    def quiet_steps(self) -> list[Step]:
        """Steps that succeeded and printed nothing.

        The Pipelines panel shows this count beside the ok count, because a
        chain whose steps all pass while saying nothing is the shape a chain
        takes when it has quietly stopped doing work. It is reported, not
        failed: a step is allowed to have nothing to say.
        """
        return [s for s in self.steps if s.quiet]


def run_step(name: str, argv: list[str], *, timeout: float = STEP_TIMEOUT_S) -> Step:
    """One step as its own process, exactly as post_gw.py does it.

    Isolation is not tidiness: these steps open the warehouse for writing, and a
    hung lock or a segfault inside one must not take the scheduler with it.
    """
    t0 = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        lines = (proc.stdout + proc.stderr).strip().splitlines()
        tail = lines[-3:]
        # A Python traceback or a native crash ends in the least useful lines
        # (the C stack bottom: "Py_RunMain | Py_BytesMain | start"). Keep the
        # first line that names the error as well, so a failed step's 300
        # characters say WHAT failed and not only where the interpreter died.
        if proc.returncode != 0:
            head = next((ln for ln in lines
                         if re.search(r"Error|Exception|Traceback|usage:|Fatal", ln)), None)
            if head and head not in tail:
                tail = [head, "...", *tail[-2:]]
        ok = proc.returncode == 0
        quiet = ok and not lines
        return Step(
            name=name, ok=ok, seconds=round(time.monotonic() - t0, 1),
            detail=NO_OUTPUT if quiet else " | ".join(tail)[-300:],
            quiet=quiet,
        )
    except subprocess.TimeoutExpired:
        return Step(name=name, ok=False, seconds=round(time.monotonic() - t0, 1),
                    detail=f"timed out after {timeout:.0f}s")
    except Exception:  # noqa: BLE001 - the step record is the error channel
        return Step(name=name, ok=False, seconds=round(time.monotonic() - t0, 1),
                    detail=traceback.format_exc()[-300:])


def _module_exists(dotted: str) -> bool:
    """Is this CLI entry point actually here yet?

    The projections providers are being built in parallel. A DAG task that hard-
    depends on a module another agent has not landed would fail the whole
    pre-deadline refresh over a missing import; instead the step is skipped and
    the digest says so, which is the same "admitting the gap beats looking
    complete" rule the report layer uses.
    """
    try:
        return importlib.util.find_spec(dotted) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


# --------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------


@dataclass
class TaskContext:
    season: str
    gw: int
    due_utc: dt.datetime
    deadline_utc: dt.datetime | None
    now: dt.datetime
    db_path: Path
    python: str = sys.executable

    def read(self):
        return Warehouse.read_copy(self.db_path)

    def write(self):
        return Warehouse(self.db_path, lock_timeout_s=180.0)

