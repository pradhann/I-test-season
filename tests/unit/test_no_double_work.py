"""No module runs on two scheduled paths unless the code says why.

PIPELINES_AUDIT.md found four pairs of duplicated work by reading the tree by
hand, and two of them turned out to be real duplication while one did not
exist at all. This test does that reading mechanically, every run, so the next
pair is caught the day it is added instead of a month later in an audit.

It walks every scheduled path (``registry.TASKS``, with the settlement chain
read from ``jobs/post_gw.settlement_steps`` and the T-30h steps read from
``pipelines/tasks.py``), extracts every ``-m`` module target and every
in-process call into an engine module, and fails when one target sits on two
paths without an entry in :data:`ALLOWED`.

An entry in :data:`ALLOWED` is not an opinion held here. It cites the comment
or docstring in the code that documents the overlap, by file, line and the
words themselves, and the citation is checked. A deliberate overlap whose
reason is deleted from the code fails this test, and so does one whose reason
moves without the citation moving with it.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _runbook():
    """The shared inventory, loaded from ``scripts/`` (not an importable
    package, so it is loaded by path exactly as test_odds_rho_verdict does)."""
    path = REPO / "scripts" / "pipelines_runbook.py"
    spec = importlib.util.spec_from_file_location("_pipelines_runbook", path)
    assert spec and spec.loader, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


runbook = _runbook()


#: The allow-list itself lives beside the inventory in
#: scripts/pipelines_runbook.py, so the runbook prints the same rows this
#: test enforces and the doc cannot drift from the rule.
ALLOWED = runbook.ALLOWED

ALLOWED_BY_TARGET = {o.target: o for o in ALLOWED}


def _is_prose_line(path: Path, line: int) -> bool:
    """True when that line of a python file is a comment or inside a docstring.

    A citation has to point at the words that explain the overlap. Pointing at
    the argv itself would let the exemption survive the deletion of its own
    reason, which is the failure this whole file exists to prevent.
    """
    source = path.read_text()
    if source.splitlines()[line - 1].lstrip().startswith("#"):
        return True
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body or not isinstance(body[0], ast.Expr):
            continue
        doc = body[0].value
        if isinstance(doc, ast.Constant) and isinstance(doc.value, str) \
                and doc.lineno <= line <= doc.end_lineno:
            return True
    return False


# --------------------------------------------------------------------------
# The extractor found something, before anything is concluded from it
# --------------------------------------------------------------------------


def test_the_walk_reaches_every_scheduled_path() -> None:
    """An extractor that silently found nothing would pass every assertion
    below it. The settlement chain is the shape check: it is the longest path
    and its steps are read from the list both execution paths share."""
    from fpl_edge.jobs import post_gw
    from fpl_edge.pipelines import registry

    inv = runbook.inventory()
    assert set(inv) == {t.id for t in registry.TASKS}

    chain = inv["post_gw_settlement"]
    assert len(chain.commands) == len(post_gw.settlement_steps("py")), (
        "the settlement chain must be read from settlement_steps itself"
    )
    assert "fpl_edge.ingest.results" in chain.targets

    # The tasks that delegate to a helper are the ones a body-only walk would
    # report as empty, so they are named rather than counted.
    for task_id in ("content_analyse", "content_analyse_backlog",
                    "lineup_captain_check", "presser_projection_refresh"):
        assert inv[task_id].targets, f"{task_id} extracted no target"


# --------------------------------------------------------------------------
# The rule
# --------------------------------------------------------------------------


def test_no_module_runs_on_two_paths_without_a_documented_reason() -> None:
    """The rule. A target on two scheduled paths is double work until an
    :data:`ALLOWED` row cites the code that says otherwise."""
    targets = runbook.target_map()
    shared = {t: paths for t, paths in targets.items() if len(paths) > 1}

    undocumented = {t: paths for t, paths in shared.items()
                    if t not in ALLOWED_BY_TARGET}
    assert not undocumented, (
        "these targets run on more than one scheduled path with nothing in "
        f"the code documenting it: {undocumented}. Either delete the second "
        "path or add an Overlap row here citing the comment that explains it."
    )

    for target, paths in shared.items():
        allowed = ALLOWED_BY_TARGET[target]
        assert tuple(paths) == allowed.paths, (
            f"{target} now runs on {paths}; the allow-list records "
            f"{list(allowed.paths)}. A new caller of an already-deliberate "
            "overlap is still a new decision."
        )


def _a_second_results_ingest(ctx):
    """A fabricated task body that re-runs the settlement chain's results step.

    It exists to be walked, not to be run: the guard above is only worth
    anything if a second path for an already-scheduled module really does
    trip it, and asserting that on the live registry is impossible (the live
    registry is, correctly, clean).
    """
    return [ctx.python, "-m", "fpl_edge.ingest.results"]


def test_a_second_undocumented_path_for_a_module_is_caught() -> None:
    """The guard, exercised. A task that runs a module the settlement chain
    already runs shows up on two paths and is not in the allow-list, which is
    exactly the failure the rule above reports."""
    import datetime as dt

    from fpl_edge.pipelines import registry

    intruder = registry.Task(
        id="a_second_results_ingest",
        description="a fabricated duplicate, for this test only",
        due=registry.Calendar(hour_utc=4),
        stale_window=dt.timedelta(hours=6),
        run=_a_second_results_ingest,
    )
    inv = runbook.inventory(registry.TASKS + (intruder,))
    targets = runbook.target_map(inv)

    assert targets["fpl_edge.ingest.results"] == [
        "a_second_results_ingest", "post_gw_settlement"], (
        "the walk no longer sees a duplicate module on two scheduled paths, "
        "so the rule above would pass whatever anybody adds"
    )
    assert "fpl_edge.ingest.results" not in ALLOWED_BY_TARGET


def test_every_allow_list_row_still_describes_a_real_overlap() -> None:
    """An allow-list that outlives its overlap teaches the next reader that
    duplication is normal here. A row whose target no longer runs twice is
    deleted, not kept as history."""
    targets = runbook.target_map()
    stale = [o.target for o in ALLOWED
             if len(targets.get(o.target, [])) < 2]
    assert not stale, (
        f"these allow-list rows no longer describe an overlap: {stale}; the "
        "duplication is gone, so the exemption goes with it"
    )


@pytest.mark.parametrize("overlap", ALLOWED, ids=[o.target for o in ALLOWED])
def test_every_allow_list_row_cites_a_comment_that_is_really_there(overlap) -> None:
    """The citation is the whole point: the reason lives beside the code that
    does the work, and this only records where. A reason that was deleted, or
    a citation that drifted off its line, fails here."""
    assert overlap.citations, f"{overlap.target} cites nothing"
    for cite in overlap.citations:
        path = REPO / cite.file
        assert path.is_file(), f"{cite.file} is gone"
        lines = path.read_text().splitlines()
        assert 1 <= cite.line <= len(lines), (
            f"{cite.file}:{cite.line} is past the end of the file"
        )
        found = [i + 1 for i, ln in enumerate(lines) if cite.quote in ln]
        assert found, (
            f"{overlap.target}: {cite.file} no longer contains "
            f"{cite.quote!r}. The overlap is exempt because the code explains "
            "it; if the explanation is gone, so is the exemption."
        )
        assert cite.line in found, (
            f"{overlap.target}: {cite.quote!r} has moved to line "
            f"{found[0]} of {cite.file}; re-cite it as "
            f"{cite.file}:{found[0]}"
        )
        assert _is_prose_line(path, cite.line), (
            f"{cite.file}:{cite.line} is code, not the comment or docstring "
            f"line that documents the overlap: "
            f"{lines[cite.line - 1].strip()[:70]}"
        )


# --------------------------------------------------------------------------
# The runbook the same inventory prints
# --------------------------------------------------------------------------


def test_the_runbook_doc_is_in_sync_with_the_registry() -> None:
    """docs/platform/PIPELINES.md prints the registry, so a task added,
    renamed or rescheduled without the doc following is a doc that lies.
    `uv run python scripts/pipelines_runbook.py --write` fixes it."""
    text = runbook.doc_path().read_text()
    assert runbook.BEGIN in text and runbook.END in text, (
        "the runbook's generated block lost its markers"
    )
    block = text.split(runbook.BEGIN, 1)[1].split(runbook.END, 1)[0]
    assert block.strip() == runbook.runbook_tables().strip(), (
        "docs/platform/PIPELINES.md is out of date; regenerate it with "
        "`uv run python scripts/pipelines_runbook.py --write`"
    )


def test_the_runbook_documents_every_deliberate_overlap() -> None:
    """The overlaps are the part of the schedule an operator cannot read off
    the table, so the doc names each one and the allow-list is the list."""
    text = runbook.doc_path().read_text()
    for overlap in ALLOWED:
        assert overlap.target in text, (
            f"the runbook does not mention the deliberate overlap "
            f"{overlap.target}"
        )
