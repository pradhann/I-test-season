"""fpl_theme.py must stay importable outside the package.

``fpl_mcp/tools/viz_tools.py:159`` copies this one file into a ``python -I``
sandbox and the sandboxed script imports it as a top-level module. Nothing else
from ``fpl_edge`` is copied, so an ``fpl_edge`` import added to it raises
ModuleNotFoundError inside the sandbox and every chart stops rendering. The
importer assembles the path from components (``parents[2] / "fpl_edge" /
"platform" / "fpl_theme.py"``), so no import graph, grep for the dotted name,
or coverage report names this file at all.

ARCHITECTURE_REVIEW.md Section 5 T6. The test parses the source rather than
importing it, because importing it would pull matplotlib into the unit suite
and would not tell us what a fresh interpreter sees.
"""

from __future__ import annotations

import ast
from pathlib import Path

import fpl_edge

THEME = Path(fpl_edge.__file__).resolve().parent / "platform" / "fpl_theme.py"


def _imported_roots(tree: ast.Module) -> set[str]:
    """Top-level package name of every import, at any nesting depth."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # a relative import is an fpl_edge import
                roots.add("fpl_edge")
            elif node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_the_theme_file_is_where_the_sandbox_copier_looks_for_it():
    assert THEME.exists(), THEME
    from fpl_mcp.tools import viz_tools

    assert viz_tools._THEME_SRC.resolve() == THEME


def test_the_theme_file_parses_on_its_own():
    ast.parse(THEME.read_text(), filename=str(THEME))


def test_the_theme_file_imports_nothing_from_fpl_edge():
    tree = ast.parse(THEME.read_text(), filename=str(THEME))
    roots = _imported_roots(tree)
    assert "fpl_edge" not in roots, (
        "fpl_theme.py is copied alone into a python -I sandbox by "
        "fpl_mcp/tools/viz_tools.py:159; an fpl_edge import here breaks every "
        f"chart. Imported roots: {sorted(roots)}"
    )


def test_the_theme_file_names_no_fpl_edge_module_in_a_string_either():
    """``importlib.import_module("fpl_edge...")`` would evade the AST check
    above, so the source text is scanned for the package name as well."""
    source = THEME.read_text()
    tree = ast.parse(source, filename=str(THEME))
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    offenders = [s for s in strings if "fpl_edge." in s]
    assert offenders == [], offenders
