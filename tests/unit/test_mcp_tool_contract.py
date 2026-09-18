"""The MCP server's structural contract: the roster, the writes, the fences.

Five rules from ``docs/platform/MCP.md`` section 2, each enforced here rather
than trusted to a reviewer.

``test_the_registered_tools_are_exactly_the_ones_the_spec_names``
    The roster is a decision, not an accident. A tool added without a spec row
    fails here, and so does one that stops registering.

``test_every_write_tool_is_one_the_spec_lists``
    Rule 3. The writes this server can perform are eight named actions across
    seven tools. A new tool whose name says it writes, or an existing one
    growing a write, fails this.

``test_no_tool_module_reaches_the_network`` and
``test_no_tool_module_imports_the_ingest_layer``
    Rule 4, as an import-graph property rather than a cache-path assertion. A
    rule enforced by the import graph cannot regress the way a cache location
    can, which is what the deleted ``test_mcp_fpl_data.py`` was guarding by
    hand. The one sanctioned crossing is the Understat fetch in
    ``tools/dossier.py``, which is function-local, named in the spec's write
    table, and is the same call the web route makes.

``test_nothing_outside_the_package_imports_it_at_module_scope``
    Rule from section 6.3. ``fpl_edge/mcp/`` sits above ``platform``, and
    ``platform/chat_agent.py`` imports it back from inside two functions. That
    function-local pair is the whole allowlist, named here by file and
    function, and a module-scope import from anywhere else is the cycle the
    sibling layout was chosen to avoid.

``test_no_tool_takes_an_entry_id``
    Rule 5 and MULTI_USER section 4. The entry id comes from the resolved user
    context; a caller cannot select a different one.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import fpl_edge
from fpl_edge.mcp import server

PACKAGE = Path(fpl_edge.__file__).resolve().parent
MCP_DIR = PACKAGE / "mcp"
TOOLS_DIR = MCP_DIR / "tools"

#: Every tool named in docs/platform/MCP.md section 3.2, module by module.
#: Written out rather than read from the server, so the server is checked
#: against the spec and not against itself.
SPEC_TOOLS: dict[str, tuple[str, ...]] = {
    "squad": ("my_squad", "brief"),
    "fixtures": ("fixtures", "fixture", "club_form"),
    "projections": ("projections",),
    "dossier": ("player", "player_form", "player_profile", "player_radar",
                "player_intel"),
    "ownership": ("ownership",),
    "creators": ("creator_consensus", "creator_record", "player_claims",
                 "episodes", "episode"),
    "manager": ("manager",),
    "pipelines": ("pipelines", "pipeline_log"),
    "ideas": ("ideas", "idea_review", "submit_idea", "mark_idea_acted"),
    "watchlist": ("watchlist_add", "watchlist_list", "watchlist_remove"),
    "analysis": ("query", "run_analysis", "list_analyses", "save_analysis",
                 "python_viz"),
    "solve": ("transfer_plan", "solve_start", "solve_status"),
}

#: Every tool that writes, and the store it writes through. Section 1.4 of the
#: spec, minus track_ideas, which the settlement chain owns.
WRITE_TOOLS: dict[str, str] = {
    "submit_idea": "fpl_edge.interfaces.inbox",
    "mark_idea_acted": "fpl_edge.interfaces.store",
    "watchlist_add": "fpl_edge.interfaces.watchlist",
    "watchlist_remove": "fpl_edge.interfaces.watchlist",
    "save_analysis": "fpl_edge.interfaces.analyses",
    "solve_start": "fpl_edge.platform.solve_runner",
    "player_profile": "fpl_edge.ingest.understat",
}

#: Which registered tool reads which panel. Every one of these must resolve in
#: the panel registry, or the tool is adapting something that is not there.
TOOL_PANELS: dict[str, str] = {
    "my_squad": "squad_overview",
    "brief": "dashboard_brief",
    "fixtures": "fixture_board",
    "fixture": "fixture_detail",
    "club_form": "fixture_board",
    "projections": "projection_table",
    "player": "player_dossier",
    "player_form": "player_form",
    "player_profile": "player_profile",
    "player_radar": "player_radar",
    "player_intel": "player_intel",
    "ownership": "ownership_eo",
    "creator_consensus": "creator_board",
    "creator_record": "creator_report_card",
    "player_claims": "player_chatter",
    "episodes": "creator_episodes",
    "episode": "episode_summary",
    "manager": "manager_lookup",
    "pipelines": "pipeline_board",
    "pipeline_log": "pipeline_run_log",
    "ideas": "idea_registry",
    "idea_review": "idea_review",
    "transfer_plan": "planner_grid",
}

#: Modules a tool may never import: the fetch stack and the ingest layer.
#: ``fpl_edge.ingest.understat`` is the one exception and it is checked
#: separately, by call site rather than by name.
FORBIDDEN_ROOTS = ("httpx", "requests", "urllib3", "aiohttp")
FORBIDDEN_DOTTED = (
    "fpl_edge.ingest.http",
    "fpl_edge.ingest.rivals.client",
    "fpl_edge.ingest.content.youtube",
)

#: The only module-scope importer allowed to be absent from this rule, and
#: only because its two imports are inside functions.
CHAT_AGENT_ALLOWLIST = (
    ("fpl_edge/platform/chat_agent.py", "list_mcp_tools"),
    ("fpl_edge/platform/chat_agent.py", "toolbelt_instance"),
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _module_scope_imports(tree: ast.Module) -> list[str]:
    """Dotted names imported at module scope only, never inside a function."""
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def _all_imports(tree: ast.Module) -> list[str]:
    """Every dotted name imported anywhere, at any nesting depth.

    ``from fpl_edge.ingest import understat`` yields both ``fpl_edge.ingest``
    and ``fpl_edge.ingest.understat``: the first is what was written, the
    second is what was actually pulled in, and the rules below are about the
    second.
    """
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
            out.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return out


def _tool_modules() -> list[Path]:
    return sorted(p for p in TOOLS_DIR.glob("*.py") if p.name != "__init__.py")


# ---------------------------------------------------------------------------
# the roster
# ---------------------------------------------------------------------------


def test_the_registered_tools_are_exactly_the_ones_the_spec_names():
    expected = sorted(
        name for names in SPEC_TOOLS.values() for name in names
    )
    assert server.tool_names() == expected
    assert len(expected) == len(set(expected)), "a name is registered twice"


def test_each_module_registers_the_tools_the_spec_assigns_to_it():
    import importlib

    for module_name, names in SPEC_TOOLS.items():
        module = importlib.import_module(f"fpl_edge.mcp.tools.{module_name}")
        for name in names:
            assert hasattr(module, name), (module_name, name)


def test_every_read_tool_names_a_panel_that_is_registered():
    from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
    from fpl_edge.platform.registry import registered

    known = set(registered())
    for tool, panel in TOOL_PANELS.items():
        assert panel in known, (tool, panel, sorted(known))


def test_the_server_is_named_what_the_allowlist_expects():
    """chat_agent builds mcp__fpl-server__{name}; the name is load-bearing."""
    assert server.SERVER_NAME == "fpl-server"


# ---------------------------------------------------------------------------
# rule 3: the writes
# ---------------------------------------------------------------------------


def test_every_write_tool_is_one_the_spec_lists():
    """THE write gate. A tool that writes and is not on the list fails here.

    Detected structurally: a write goes through a store module, and a store
    module is reached by an import inside the tool's own module. Any tool
    module importing a writing store must register only write tools the spec
    names.
    """
    import importlib

    writing_stores = {
        "fpl_edge.interfaces.inbox",
        "fpl_edge.interfaces.store",
        "fpl_edge.interfaces.watchlist",
        "fpl_edge.interfaces.analyses",
        "fpl_edge.platform.solve_runner",
        "fpl_edge.ingest.understat",
    }
    for path in _tool_modules():
        imports = set(_all_imports(_tree(path)))
        touched = imports & writing_stores
        if not touched:
            continue
        module = importlib.import_module(f"fpl_edge.mcp.tools.{path.stem}")
        registered_here = set(SPEC_TOOLS[path.stem])
        writers = registered_here & set(WRITE_TOOLS)
        assert writers, (
            f"{path.name} imports {sorted(touched)} but registers no tool the "
            f"spec lists as a write. Either it writes and the spec's table "
            f"needs the name, or the import is unnecessary."
        )
        for name in writers:
            assert WRITE_TOOLS[name] in imports or name == "player_profile", (
                name, WRITE_TOOLS[name], sorted(imports),
            )
        assert module is not None


def test_no_write_tool_exists_outside_the_named_seven():
    """The inverse: nothing registered writes unless it is on the list."""
    suspicious = {
        name for name in server.tool_names()
        if any(verb in name for verb in ("add", "remove", "save", "submit",
                                         "mark", "start", "delete", "track"))
    }
    assert suspicious <= set(WRITE_TOOLS), sorted(suspicious - set(WRITE_TOOLS))


def test_track_ideas_is_not_registered():
    """Settling every resolvable idea belongs to the settlement chain.

    A tool that did it on request was a second writer racing the first.
    """
    assert "track_ideas" not in server.tool_names()


# ---------------------------------------------------------------------------
# rule 4: no tool fetches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", _tool_modules(), ids=lambda p: p.name)
def test_no_tool_module_reaches_the_network(path):
    imports = _all_imports(_tree(path))
    roots = {name.split(".")[0] for name in imports}
    offending = roots & set(FORBIDDEN_ROOTS)
    assert not offending, (
        f"{path.name} imports {sorted(offending)}. The ingest layer fetches, "
        f"the warehouse holds, panels read. A tool that opens a socket is a "
        f"second source of truth."
    )


@pytest.mark.parametrize("path", _tool_modules(), ids=lambda p: p.name)
def test_no_tool_module_imports_the_ingest_layer(path):
    imports = set(_all_imports(_tree(path)))
    offending = imports & set(FORBIDDEN_DOTTED)
    assert not offending, (path.name, sorted(offending))
    ingest = {
        name for name in imports
        if name.startswith("fpl_edge.ingest")
        and name not in ("fpl_edge.ingest", "fpl_edge.ingest.understat")
    }
    assert not ingest, (
        f"{path.name} imports {sorted(ingest)}. The one sanctioned crossing is "
        f"the Understat profile fetch, which is named in the spec's write "
        f"table and is the same call the web route makes."
    )


def test_the_one_sanctioned_fetch_is_function_local_and_in_one_module():
    """The Understat fetch is an exception, kept visibly narrow."""
    holders = [
        path.name for path in _tool_modules()
        if "fpl_edge.ingest.understat" in _all_imports(_tree(path))
    ]
    assert holders == ["dossier.py"], holders
    tree = _tree(TOOLS_DIR / "dossier.py")
    assert "fpl_edge.ingest.understat" not in _module_scope_imports(tree), (
        "the sanctioned fetch must stay a function-local import, so importing "
        "the module cannot pull the fetch stack in."
    )


@pytest.mark.parametrize("path", _tool_modules(), ids=lambda p: p.name)
def test_no_tool_module_calls_run_script_itself(path):
    """One tool call is one panel run, and the adapter is the only path."""
    source = path.read_text(encoding="utf-8")
    assert "run_script" not in source, (
        f"{path.name} names run_script. Every panel read goes through "
        f"adapter.panel_call, which is what makes one tool call one panel run."
    )


# ---------------------------------------------------------------------------
# rule 5: no tool takes an entry id
# ---------------------------------------------------------------------------


def test_no_tool_takes_an_entry_id():
    """The panels that need one get it from the context, never from a caller."""
    import importlib

    for module_name, names in SPEC_TOOLS.items():
        module = importlib.import_module(f"fpl_edge.mcp.tools.{module_name}")
        for name in names:
            params = inspect.signature(getattr(module, name)).parameters
            assert "entry_id" not in params, (module_name, name)
            assert "user_id" not in params, (module_name, name)


def test_the_context_is_the_only_place_the_user_singleton_is_read():
    """One line to change when D2's UserContext lands, not a search."""
    readers = [
        path.relative_to(PACKAGE).as_posix()
        for path in MCP_DIR.rglob("*.py")
        if "fpl_edge.config" in _all_imports(_tree(path))
    ]
    assert readers == ["mcp/context.py"], readers


# ---------------------------------------------------------------------------
# section 6.3: the import direction
# ---------------------------------------------------------------------------


def test_nothing_outside_the_package_imports_it_at_module_scope():
    """THE cycle guard. platform -> mcp -> platform must stay function-local."""
    offenders: list[str] = []
    for path in PACKAGE.rglob("*.py"):
        if MCP_DIR in path.parents or path == MCP_DIR / "__init__.py":
            continue
        try:
            names = _module_scope_imports(_tree(path))
        except SyntaxError:  # pragma: no cover - a file that cannot parse
            continue
        if any(name == "fpl_edge.mcp" or name.startswith("fpl_edge.mcp.")
               for name in names):
            offenders.append(path.relative_to(PACKAGE).as_posix())
    assert not offenders, (
        f"{offenders} import fpl_edge.mcp at module scope. The package sits "
        f"above platform; the only edge running the other way is the pair of "
        f"function-local imports in {[f for f, _ in CHAT_AGENT_ALLOWLIST]}."
    )


def test_the_package_init_stays_empty_of_imports():
    """``import fpl_edge.mcp`` must cost nothing: chat_agent relies on it."""
    tree = _tree(MCP_DIR / "__init__.py")
    assert _all_imports(tree) == []
    assert all(
        isinstance(node, (ast.Expr,)) for node in tree.body
    ), "only the docstring belongs in fpl_edge/mcp/__init__.py"


def test_the_tools_package_init_stays_empty_of_imports():
    tree = _tree(TOOLS_DIR / "__init__.py")
    assert _all_imports(tree) == []


def test_the_entry_point_is_guarded():
    """An unguarded __main__ hangs the audit suite's import walk."""
    source = (MCP_DIR / "__main__.py").read_text(encoding="utf-8")
    assert 'if __name__ == "__main__":' in source
    tree = _tree(MCP_DIR / "__main__.py")
    calls = [
        node for node in tree.body
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
    ]
    assert not calls, "no call may run at module scope in __main__.py"


def test_the_module_answers_help_without_building_the_server():
    """What the desktop client's command does when it is asked for help.

    A module with no guard imports, defines its main and exits zero having
    done nothing, which a scheduler reads as success. This is the inverse
    check: the guard is there and the module still answers.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "fpl_edge.mcp", "--help"],
        capture_output=True, text=True, timeout=180, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "fpl-mcp" in proc.stdout
    assert "--list-tools" in proc.stdout


def test_list_tools_prints_every_registered_name_and_exits_zero():
    """What M2.13's console script does, through the same entry point."""
    proc = subprocess.run(
        [sys.executable, "-m", "fpl_edge.mcp", "--list-tools"],
        capture_output=True, text=True, timeout=300, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert lines[0] == f"{server.SERVER_NAME}: {len(server.tool_names())} tools"
    assert lines[1:] == server.tool_names()
