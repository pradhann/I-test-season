"""The scheduled paths, extracted from the code, printed as the runbook table.

One inventory, two readers. ``tests/unit/test_no_double_work.py`` asserts that
no module target sits on two scheduled paths without a documented reason, and
``docs/platform/PIPELINES.md`` prints the same inventory as the operator's
table. Both call :func:`inventory`, so the doc and the test can never describe
two different systems.

A SCHEDULED PATH is one row of :data:`fpl_edge.pipelines.registry.TASKS`. What
a path runs is read out of the code rather than restated here:

* ``post_gw_settlement`` runs ``jobs/post_gw.settlement_steps``, which is
  called for its actual (name, argv) rows.
* every other task's ``run`` callable is parsed. Each ``[ctx.python, "-m",
  module, ...]`` argv literal in its body, and in the same-module helpers it
  calls, is one subprocess target; each call into an ``fpl_edge`` module
  outside the scheduling plumbing is one in-process callable.

A TARGET is the module plus its first argument, because
``content.pipeline ingest`` and ``content.pipeline analyze`` are two different
jobs that happen to share an entry point. Without the argument every content
task collides with every other one and the map says nothing.

Usage::

    uv run python scripts/pipelines_runbook.py            # the runbook tables
    uv run python scripts/pipelines_runbook.py --targets  # target -> paths
"""

from __future__ import annotations

import argparse
import ast
import datetime as dt
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from fpl_edge.jobs import post_gw  # noqa: E402
from fpl_edge.pipelines import health, registry  # noqa: E402

#: Packages whose functions are the scheduling plumbing itself. A call into
#: one of these says nothing about what work a task does.
PLUMBING = ("fpl_edge.pipelines", "fpl_edge.jobs", "fpl_edge.store")

#: The placeholder the argv reader prints where the interpreter goes, so a
#: command is readable without carrying this machine's python path.
PY = "uv run python"

#: Flags that select WHICH JOB an entry point does, so they belong in the
#: target the way a subcommand does. Every other flag is a parameter of one
#: job (--db, --budget, --season) and is left out, so a second caller that
#: passes different parameters still collides with the first and has to be
#: either removed or allow-listed.
MODE_FLAGS = ("--fixtures", "--odds-api", "--build")


# --------------------------------------------------------------------------
# Reading argv literals and callables out of a function
# --------------------------------------------------------------------------


def _module_ast(fn) -> tuple[ast.Module, str]:
    """The parsed module a function was defined in, and its dotted name."""
    mod = inspect.getmodule(fn)
    source = Path(inspect.getsourcefile(fn)).read_text()
    return ast.parse(source), mod.__name__


def _string_bindings(tree: ast.AST) -> dict[str, str]:
    """Every ``name = "literal"`` in one scope, for argv entries that are
    held in a variable (``projections_cli``, ``module``)."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out[t.id] = node.value.value
    return out


def _import_bindings(tree: ast.AST) -> dict[str, str]:
    """Local name -> dotted path, for both import forms, at any depth.

    Function-local imports are the house idiom here (``run_audio_retention``
    imports ``asr`` inside its body), so this walks the whole tree rather than
    only the module's top level.
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                out[a.asname or a.name] = f"{node.module}.{a.name}"
    return out


def _is_interpreter(node: ast.expr) -> bool:
    """True for the ``py`` / ``ctx.python`` that opens every argv literal."""
    if isinstance(node, ast.Name):
        return node.id == "py"
    return isinstance(node, ast.Attribute) and node.attr == "python"


def _element_text(e: ast.expr, strings: dict[str, str]) -> str:
    """One argv element as a person would read it.

    A literal prints itself. A value the code computes prints as its own name
    in angle brackets (``<season>``, ``<db_path>``, ``<budget>``), so the
    printed command says what to substitute instead of hiding it behind an
    ellipsis. The target is always the module and its first argument, and both
    of those are literals in every argv in this tree.
    """
    if isinstance(e, ast.Constant) and isinstance(e.value, str):
        return e.value
    if isinstance(e, ast.Name):
        return strings.get(e.id, f"<{e.id}>")
    if isinstance(e, ast.Attribute):
        return f"<{e.attr}>"
    if isinstance(e, ast.Call):
        if isinstance(e.func, ast.Name) and e.func.id == "str" and e.args:
            return _element_text(e.args[0], strings)
        if isinstance(e.func, ast.Attribute):
            return f"<{e.func.attr}>"
    if isinstance(e, ast.BinOp) and isinstance(e.op, ast.Add) \
            and isinstance(e.right, ast.Constant):
        return f"<{_element_text(e.left, strings).strip('<>')} plus {e.right.value}>"
    return "..."


def _argv_literals(fn_node: ast.AST, strings: dict[str, str]) -> list[list[str]]:
    """Every ``[ctx.python, ...]`` list literal in one function, as strings."""
    out: list[list[str]] = []
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.List) or not node.elts:
            continue
        if not _is_interpreter(node.elts[0]):
            continue
        out.append([PY] + [_element_text(e, strings) for e in node.elts[1:]])
    return out


def _called_names(fn_node: ast.AST) -> tuple[set[str], set[str]]:
    """(bare names called, dotted names called) inside one function."""
    bare: set[str] = set()
    dotted: set[str] = set()
    for node in ast.walk(fn_node):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            bare.add(f.id)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            dotted.add(f"{f.value.id}.{f.attr}")
    return bare, dotted


def _is_engine_call(dotted: str | None) -> bool:
    """True for a call into the engine's own work modules.

    The standard library is not work a pipeline could duplicate, and the
    scheduling packages are the machinery that runs the work rather than the
    work itself.
    """
    return bool(dotted) and dotted.startswith("fpl_edge.") \
        and not dotted.startswith(PLUMBING)


def _find_def(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def command_of(argv: list[str]) -> str:
    """The argv as a person would type it."""
    return " ".join(argv)


def target_of(argv: list[str]) -> str:
    """The module (or script) plus its first argument.

    ``["uv run python", "-m", "pkg.mod", "ingest", "--only", "x"]`` is
    ``pkg.mod ingest``. A command with no subcommand is the module alone,
    unless its first flag is in :data:`MODE_FLAGS`: ``ingest_odds.py
    --fixtures`` and ``--odds-api`` fetch different things from different
    vendors and are not one target.
    """
    rest = argv[1:]
    if rest and rest[0] == "-m":
        module, rest = rest[1], rest[2:]
    elif rest:
        module, rest = rest[0], rest[1:]
    else:
        return "python"
    for arg in rest:
        if arg == "..." or arg.startswith("<"):
            continue
        if arg.startswith("-"):
            return f"{module} {arg}" if arg in MODE_FLAGS else module
        return f"{module} {arg}"
    return module


# --------------------------------------------------------------------------
# The inventory
# --------------------------------------------------------------------------


@dataclass
class PathSteps:
    """What one registry task runs, in order."""

    task_id: str
    commands: list[str] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    callables: list[str] = field(default_factory=list)


def _walk(fn, path: PathSteps, seen: set[str]) -> None:
    """Collect one function's argv literals and calls, then its helpers.

    The recursion follows calls to module-level functions defined in the same
    module, because the registry's tasks delegate (``run_content_analyse`` ->
    ``_run_analyse``, ``lineup_captain_check`` -> ``_ingest_lineups_step``)
    and the argv literal lives in the helper.
    """
    tree, module_name = _module_ast(fn)
    node = _find_def(tree, fn.__name__)
    if node is None or f"{module_name}.{fn.__name__}" in seen:
        return
    seen.add(f"{module_name}.{fn.__name__}")

    strings = dict(_string_bindings(tree))
    strings.update(_string_bindings(node))
    imports = _import_bindings(tree)
    imports.update(_import_bindings(node))

    for argv in _argv_literals(node, strings):
        cmd = command_of(argv)
        if cmd not in path.commands:
            path.commands.append(cmd)
            path.targets.append(target_of(argv))

    bare, dotted = _called_names(node)
    for name in sorted(dotted):
        head, attr = name.split(".", 1)
        dotted_path = imports.get(head)
        if _is_engine_call(dotted_path):
            entry = f"{dotted_path}.{attr}"
            if entry not in path.callables:
                path.callables.append(entry)
    for name in sorted(bare):
        dotted_path = imports.get(name)
        if _is_engine_call(dotted_path):
            if dotted_path not in path.callables:
                path.callables.append(dotted_path)

    module = sys.modules.get(module_name)
    for name in sorted(bare):
        helper = getattr(module, name, None)
        if inspect.isfunction(helper) and helper.__module__ == module_name:
            _walk(helper, path, seen)


def steps_for(task: registry.Task) -> PathSteps:
    """Every command one registry task runs, in order."""
    path = PathSteps(task_id=task.id)
    if task.id == "post_gw_settlement":
        # THE settlement chain, read from the list both execution paths share
        # rather than from a copy of it (jobs/post_gw.py settlement_steps).
        for _name, argv in post_gw.settlement_steps(PY):
            path.commands.append(command_of(argv))
            path.targets.append(target_of(argv))
        return path
    _walk(task.run, path, set())
    return path


def inventory(tasks: tuple[registry.Task, ...] | None = None) -> dict[str, PathSteps]:
    """task id -> what it runs, for every scheduled path."""
    tasks = tasks if tasks is not None else registry.TASKS
    return {t.id: steps_for(t) for t in tasks}


def target_map(inv: dict[str, PathSteps] | None = None) -> dict[str, list[str]]:
    """target -> the scheduled paths that run it, sorted."""
    inv = inv if inv is not None else inventory()
    out: dict[str, list[str]] = {}
    for task_id, path in inv.items():
        for target in list(path.targets) + [f"callable:{c}" for c in path.callables]:
            out.setdefault(target, [])
            if task_id not in out[target]:
                out[target].append(task_id)
    return {k: sorted(v) for k, v in sorted(out.items())}


# --------------------------------------------------------------------------
# The deliberate overlaps
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Citation:
    """One file:line that documents an overlap, plus the words to find there."""

    file: str
    line: int
    quote: str


@dataclass(frozen=True)
class Overlap:
    """One target that deliberately runs on more than one scheduled path."""

    target: str
    paths: tuple[str, ...]
    why: str
    citations: tuple[Citation, ...]


#: Every deliberate overlap, with the code that documents it.
#:
#: Adding a row here is the only way to put a module on a second scheduled
#: path, and it costs a comment in the code that says why.
ALLOWED: tuple[Overlap, ...] = (
    Overlap(
        target="fpl_edge.ingest.projections.cli ingest",
        paths=("post_gw_settlement", "presser_projection_refresh"),
        why="Providers publish on their own clocks, so the nightly pull bounds "
            "every feed at a day old and the T-30h pull catches what moved "
            "before the deadline. Section 6 of ARCHITECTURE_REVIEW.md keeps "
            "the nightly one: it is a documented fix, not double work.",
        citations=(
            Citation("fpl_edge/jobs/post_gw.py", 209,
                     "Nightly + T-30h gives every feed at most a day of staleness."),
            Citation("fpl_edge/pipelines/tasks.py", 74,
                     "T-30h: refetch what press conferences and projection "
                     "sites just changed."),
        ),
    ),
    Overlap(
        target="fpl_edge.platform.scripts.fixtures --build",
        paths=("fixture_ratings_refit", "post_gw_settlement",
               "presser_projection_refresh"),
        why="One writer, three callers. Refactor group 6 merged "
            "models/team_goals/ratings_cache.py into fixtures/build.py, so the "
            "two fits of one model became one fit writing all three artefacts. "
            "The 11:00 UTC refit is the daily one, settlement rebuilds after "
            "results land, and T-30h rebuilds after midweek rescheduling.",
        citations=(
            Citation("fpl_edge/jobs/post_gw.py", 227,
                     "This ran `models.team_goals.ratings_cache` until that "
                     "module was"),
            Citation("fpl_edge/pipelines/tasks.py", 82,
                     "Refresh the cached fixture artefacts so the ticker's "
                     "colours reflect"),
            Citation("fpl_edge/pipelines/registry.py", 686,
                     "Refit the Dixon-Coles club split the Fixtures board "
                     "colours from."),
        ),
    ),
    Overlap(
        target="fpl_edge.ingest.content.pipeline ingest",
        paths=("content_fast_rss", "post_gw_settlement",
               "presser_projection_refresh"),
        why="The content tiers are a decision: 13 creator feeds every four "
            "hours, the other 27 sources once a night over a wider window, "
            "and a two-day catch-up before each deadline.",
        citations=(
            Citation("fpl_edge/ingest/content/sources.py", 267,
                     'Content tiers (PIPELINES.md '),
            Citation("fpl_edge/pipelines/tasks.py", 74,
                     "T-30h: refetch what press conferences and projection "
                     "sites just changed."),
        ),
    ),
    Overlap(
        target="fpl_edge.ingest.content.pipeline transcribe",
        paths=("content_fast_rss", "content_transcribe"),
        why="Captions and audio are different costs. The 4-hourly rung takes "
            "captions only and never downloads audio; the nightly task runs "
            "the GPU under a wall-clock budget.",
        citations=(
            Citation("fpl_edge/pipelines/registry.py", 761,
                     "Podcast ASR stays on the nightly task"),
        ),
    ),
    Overlap(
        target="fpl_edge.ingest.content.pipeline analyze",
        paths=("content_analyse", "content_analyse_backlog"),
        why="Same budgeted, resumable pass over two different queues: the "
            "daily one covers the last 21 days, the overnight one drops the "
            "window and eats the never-analysed backlog.",
        citations=(
            Citation("fpl_edge/pipelines/registry.py", 645,
                     "The second pass, overnight: no window at all, so it eats "
                     "the backlog."),
        ),
    ),
    Overlap(
        target="scripts/ingest_odds.py --odds-api",
        paths=("odds_refresh", "post_gw_settlement"),
        why="The nightly top-up is a no-op whenever the deadline ladder has "
            "already priced the week. --max-age-hours 48 is what makes it one, "
            "and it reports the skip rather than a fake ok.",
        citations=(
            Citation("fpl_edge/jobs/post_gw.py", 247,
                     "``--max-age-hours 48`` makes this a genuine no-op"),
        ),
    ),
    Overlap(
        target="scripts/ingest_live.py",
        paths=("post_gw_settlement", "presser_projection_refresh"),
        why="The bootstrap snapshot before a deadline is the point of the "
            "T-30h task: prices, injuries and news move after the nightly run.",
        citations=(
            Citation("fpl_edge/pipelines/tasks.py", 74,
                     "T-30h: refetch what press conferences and projection "
                     "sites just changed."),
        ),
    ),
    Overlap(
        target="scripts/ingest_odds.py --fixtures",
        paths=("post_gw_settlement", "presser_projection_refresh"),
        why="Forward fixtures are free and reschedules land midweek, so the "
            "T-30h task refetches them alongside the rest of the chain's head.",
        citations=(
            Citation("fpl_edge/pipelines/tasks.py", 74,
                     "T-30h: refetch what press conferences and projection "
                     "sites just changed."),
        ),
    ),
)


# --------------------------------------------------------------------------
# The runbook table
# --------------------------------------------------------------------------

#: Where the generated block starts and ends inside PIPELINES.md. Everything
#: between the markers is this script's output; everything outside is written
#: by hand.
BEGIN = "<!-- BEGIN GENERATED: scripts/pipelines_runbook.py -->"
END = "<!-- END GENERATED -->"

#: What goes stale when a task stops running, per task. The registry carries
#: the schedule and the description; the consequence is an operator fact and
#: lives here, beside the table that prints it.
BREAKS_IF_STALE: dict[str, str] = {
    "presser_projection_refresh": "the pre-deadline injury digest and the "
                                  "projections behind it are a day old",
    "price_radar": "price rises and falls land without warning",
    "final_solve_delivery": "no plan is delivered before the deadline",
    "lineup_captain_check": "a benched captain is not caught",
    "odds_refresh": "the odds strip ages and the derived markets with it",
    "post_gw_settlement": "no actuals, so projection weights, creator scores "
                          "and the cohort crawls all freeze",
    "fpl_core_insights": "per-match xG stops at the last settled gameweek",
    "panel_picks_crawl": "the Creators board shows last week's squads",
    "content_transcribe": "new episodes hold audio and no text",
    "content_analyse": "stored text produces no claims, so the Creators tab "
                       "ages while the feed keeps filling",
    "content_analyse_backlog": "the never-analysed backlog stops draining",
    "content_fast_rss": "creator feeds go unread for the day",
    "forecast_refresh": "forecast.parquet covers a horizon the solver has "
                        "already passed and the solve refuses to score it",
    "fixture_ratings_refit": "the Fixtures board colours an older fit",
    "briefing_intel": "the dashboard loses its salience pass",
    "audio_retention": "the ASR audio cache grows without a sweep",
}

#: The artefacts and tables a task writes, in the operator's words. Derived
#: from the step commands, which name the writer but not what it writes.
WRITES: dict[str, str] = {
    "presser_projection_refresh": "fact_player_state, fact_fixture, "
                                  "content_item, the three fixture parquets, "
                                  "fact_projection",
    "price_radar": "dag_observation rows and one alert",
    "final_solve_delivery": "nothing; it reads the newest plan artefact",
    "lineup_captain_check": "fact_lineup, and one alert",
    "odds_refresh": "fact_odds and the derived market tables",
    "post_gw_settlement": "fact_player_fixture, fact_projection, "
                          "projection_weight, the fixture parquets, "
                          "fact_odds, content tables, the rivals tables, "
                          "the retro and weekly reports",
    "fpl_core_insights": "fact_match_xg",
    "panel_picks_crawl": "dim_manager, fact_manager_season, fact_manager_gw, "
                         "fact_manager_pick, fact_manager_transfer, "
                         "fact_manager_chip",
    "content_transcribe": "content_transcript and transcript_provenance",
    "content_analyse": "content_analysis and content_claim",
    "content_analyse_backlog": "content_analysis and content_claim",
    "content_fast_rss": "content_source, content_item, content_transcript",
    "forecast_refresh": "forecast.parquet and its sidecar",
    "fixture_ratings_refit": "fixture_ratings.parquet, "
                             "fixture_difficulty.parquet, "
                             "fixture_calibration.parquet",
    "briefing_intel": "briefing_intel.json",
    "audio_retention": "nothing; it deletes swept audio files",
}


def span_words(delta: dt.timedelta) -> str:
    """A timedelta in the app's own span vocabulary (health.span_words)."""
    return health.span_words(delta.total_seconds() / 3600.0)


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def runbook_tables(tasks: tuple[registry.Task, ...] | None = None) -> str:
    """The generated half of the runbook: one row per task, then the commands."""
    tasks = tasks if tasks is not None else registry.TASKS
    inv = inventory(tasks)
    lines = [
        "| Task | Family | Schedule | Writes | Stale after | If it stops |",
        "|---|---|---|---|---|---|",
    ]
    for t in tasks:
        lines.append(
            f"| `{t.id}` | {t.family} | {_cell(health.describe_due(t.due))} "
            f"| {_cell(WRITES.get(t.id, 'see the commands below'))} "
            f"| {span_words(t.stale_window)} "
            f"| {_cell(BREAKS_IF_STALE.get(t.id, 'not recorded'))} |"
        )
    lines.append("")
    lines.append("### Running one by hand")
    lines.append("")
    lines.append("Every task runs through the same seam the scheduler and "
                 "the Pipelines tab use, so a hand run leaves the same ledger "
                 "row and the same log file:")
    lines.append("")
    lines.append("```")
    lines.append("uv run python -c \"from fpl_edge.pipelines import runner; "
                 "o = runner.run_task('<task id>'); "
                 "print(o.result.outcome, o.log_path)\"")
    lines.append("```")
    lines.append("")
    lines.append("The commands each task runs, in order, for running one step "
                 "on its own:")
    lines.append("")
    for t in tasks:
        path = inv[t.id]
        lines.append(f"**`{t.id}`**")
        lines.append("")
        if not path.commands and not path.callables:
            lines.append("```")
            lines.append("# no subprocess: the task reads the warehouse "
                         "in process")
            lines.append("```")
            lines.append("")
            continue
        lines.append("```")
        for cmd in path.commands:
            lines.append(cmd)
        for call in path.callables:
            lines.append(f"# in process: {call}")
        lines.append("```")
        lines.append("")
    lines.extend(overlap_table())
    return "\n".join(lines).rstrip() + "\n"


def overlap_table() -> list[str]:
    """The deliberate overlaps, from the same list the test enforces."""
    lines = [
        "### Deliberate overlaps",
        "",
        "One module on two scheduled paths is double work until the code says "
        "why. `tests/unit/test_no_double_work.py` walks every path, extracts "
        "every target, and fails on an overlap that is not listed here; each "
        "row cites the comment that documents it, and the citation is checked "
        "against the file.",
        "",
        "| Target | Runs on | Why | Documented at |",
        "|---|---|---|---|",
    ]
    for o in ALLOWED:
        where = ", ".join(f"`{c.file}:{c.line}`" for c in o.citations)
        paths = ", ".join(f"`{p}`" for p in o.paths)
        lines.append(f"| `{o.target}` | {paths} | {_cell(o.why)} | {where} |")
    lines.append("")
    return lines


def doc_path() -> Path:
    return REPO / "docs" / "platform" / "PIPELINES.md"


def rewrite_doc() -> bool:
    """Put the generated tables back between the markers. True when changed."""
    path = doc_path()
    text = path.read_text()
    before, marker, rest = text.partition(BEGIN)
    body, end_marker, after = rest.partition(END)
    if not marker or not end_marker:
        raise SystemExit(f"{path} has no generated block; add the markers")
    fresh = f"{before}{BEGIN}\n\n{runbook_tables()}\n{END}{after}"
    if fresh == text:
        return False
    path.write_text(fresh)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--targets", action="store_true",
                    help="print target -> scheduled paths instead")
    ap.add_argument("--write", action="store_true",
                    help="rewrite the generated block in PIPELINES.md")
    args = ap.parse_args(argv)
    if args.targets:
        for target, paths in target_map().items():
            mark = "  <-- more than one path" if len(paths) > 1 else ""
            print(f"{target}: {', '.join(paths)}{mark}")
        return 0
    if args.write:
        print("rewrote" if rewrite_doc() else "already in sync")
        return 0
    print(runbook_tables())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
