"""The DAG's optional-module guard must not be silently satisfied.

``fpl_edge/pipelines/tasks.py:406-411`` skips the lineups ingest when
``_module_exists("fpl_edge.ingest.lineups")`` is False, and records the skip as
``Step(ok=True, "skipped")``. That was written for modules another agent had
not landed yet. The module has landed, so the guard now has one remaining
effect: a rename or move of ``fpl_edge/ingest/lineups.py`` turns the T-90m
captain check into a success that checked nothing, and the digest says the run
was fine.

ARCHITECTURE_REVIEW.md Section 5 T7. The dotted name is spelled out here rather
than imported from the task, so a refactor that changes the string fails this
test instead of moving in step with it.

Both the guard and its helper moved in group 2. ``_module_exists`` is now in
``fpl_edge/pipelines/contracts.py`` and ``_ingest_lineups_step`` in
``fpl_edge/pipelines/tasks.py``, both out of ``fpl_edge/jobs/deadline_dag.py``
(ARCHITECTURE_REVIEW.md check 6, C1 loop A). The dotted module name under
guard did not change.
"""

from __future__ import annotations

from fpl_edge.pipelines.contracts import _module_exists

LINEUPS = "fpl_edge.ingest.lineups"


def test_the_lineups_module_the_dag_guards_on_actually_exists():
    assert _module_exists(LINEUPS) is True, (
        f"{LINEUPS} is missing, so pipelines/tasks.py:406-411 turns the T-90m "
        "captain check into Step(ok=True, 'skipped') and reports success"
    )


def test_the_task_still_names_that_exact_module_in_its_guard():
    """The guard and the module have to agree. Reading the source keeps this
    test honest across the moves the refactor is making."""
    from pathlib import Path

    import fpl_edge

    source = (Path(fpl_edge.__file__).resolve().parent
              / "pipelines" / "tasks.py").read_text()
    assert f'module = "{LINEUPS}"' in source


def test_a_module_that_is_not_there_is_reported_as_absent():
    assert _module_exists("fpl_edge.ingest.no_such_module_here") is False
