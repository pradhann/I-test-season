"""The DAG's optional-module guard must not be silently satisfied.

``deadline_dag.py:971-974`` skips the lineups ingest when
``_module_exists("fpl_edge.ingest.lineups")`` is False, and records the skip as
``Step(ok=True, "skipped")``. That was written for modules another agent had
not landed yet. The module has landed, so the guard now has one remaining
effect: a rename or move of ``fpl_edge/ingest/lineups.py`` turns the T-90m
captain check into a success that checked nothing, and the digest says the run
was fine.

ARCHITECTURE_REVIEW.md Section 5 T7. The dotted name is spelled out here rather
than imported from the DAG, so a refactor that changes the string in
``deadline_dag.py`` fails this test instead of moving in step with it.
"""

from __future__ import annotations

from fpl_edge.jobs.deadline_dag import _module_exists

LINEUPS = "fpl_edge.ingest.lineups"


def test_the_lineups_module_the_dag_guards_on_actually_exists():
    assert _module_exists(LINEUPS) is True, (
        f"{LINEUPS} is missing, so deadline_dag.py:971-974 turns the T-90m "
        "captain check into Step(ok=True, 'skipped') and reports success"
    )


def test_the_dag_still_names_that_exact_module_in_its_guard():
    """The guard and the module have to agree. Reading the source keeps this
    test honest when the constant moves during the split of the DAG's task
    bodies into pipelines/tasks.py."""
    from pathlib import Path

    import fpl_edge

    source = (Path(fpl_edge.__file__).resolve().parent
              / "jobs" / "deadline_dag.py").read_text()
    assert f'module = "{LINEUPS}"' in source


def test_a_module_that_is_not_there_is_reported_as_absent():
    assert _module_exists("fpl_edge.ingest.no_such_module_here") is False
