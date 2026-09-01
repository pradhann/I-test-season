"""Pipelines: the product's edge, organised as a first-class package.

PIPELINES.md §4.1/§5, owner-elevated 2026-08-31: "pipelines is the edge,
organize it properly". Three modules, one boundary:

* :mod:`fpl_edge.pipelines.registry` -- the authority list. Every scheduled
  pipeline is one reviewable ``Task`` row; adding authority is one line.
* :mod:`fpl_edge.pipelines.runner` -- the ONE execution path. Every run,
  scheduled or manual, goes through it: ledger row, timing, captured logs.
* :mod:`fpl_edge.pipelines.health` -- derived health and the control-panel
  payload (``pipeline_status``). Pure reads, no stored aggregates, no LLM.

The deadline mathematics (event-relative due instants, the firing ledger,
stale windows) stay in :mod:`fpl_edge.jobs.deadline_dag`, which delegates
task execution here.
"""

from fpl_edge.pipelines.health import (  # noqa: F401
    avg_duration_ms,
    describe_due,
    pipeline_status,
    task_health,
)
from fpl_edge.pipelines.registry import (  # noqa: F401
    NO_GW,
    TASKS,
    Calendar,
    DeadlineRelative,
    Interval,
    OnDemand,
    Task,
    by_id,
    due_instants,
    registry_due,
    runner_for,
    stale_window_of,
    validate,
)
from fpl_edge.pipelines.runner import RunOutcome, execute, record, run_task  # noqa: F401
