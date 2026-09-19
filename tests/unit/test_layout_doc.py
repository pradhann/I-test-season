"""docs/platform/LAYOUT.md describes the tree that is actually on disk.

A layout page that drifts is worse than no layout page, because a reader
trusts it. The generated block is rewritten with::

    uv run python scripts/module_layout.py --write
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _module_layout():
    spec = importlib.util.spec_from_file_location(
        "module_layout", REPO / "scripts" / "module_layout.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["module_layout"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def layout():
    return _module_layout()


def test_generated_block_matches_the_tree(layout):
    """The table in the doc is the table the tree produces, byte for byte."""
    doc = layout.doc_path()
    assert doc.exists(), f"{doc} is missing"
    fresh = layout.rendered()
    assert fresh == doc.read_text(), (
        "docs/platform/LAYOUT.md is out of sync with fpl_edge/. Run "
        "`uv run python scripts/module_layout.py --write`."
    )


def test_every_module_is_listed(layout):
    """No module under fpl_edge/ is missing from the table."""
    listed = {(m.package, m.module) for m in layout.walk()}
    body = layout.doc_path().read_text()
    for package, module in sorted(listed):
        assert f"| `{package}` | `{module}` |" in body, (
            f"{package}.{module} is not in the layout table")


def test_line_counts_are_the_real_ones(layout):
    """The Lines column is read off disk, not carried in the doc."""
    for m in layout.walk():
        path = REPO / m.package.replace(".", "/") / m.module
        assert m.lines == len(path.read_text().splitlines())


def test_layer_rules_are_hand_written_above_the_block(layout):
    """The import direction is a decision, so it lives outside the markers."""
    text = layout.doc_path().read_text()
    head = text.split(layout.BEGIN)[0]
    assert "## Layer rules" in head
    for package in ("jobs", "pipelines", "platform", "interfaces", "ingest",
                    "store", "rules", "mcp"):
        assert package in head, f"the layer rules do not mention {package}"
