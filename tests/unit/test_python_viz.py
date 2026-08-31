"""python_viz: the sandbox, the theme, and the honest failure modes.

These tests run the REAL sandbox subprocess (a couple of seconds each) with
``datasets_json="[]"`` so no warehouse is touched -- the code under test is
the harness, not the data. The one test that needs a dataset fakes the
guarded query at its seam.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import fpl_mcp.tools.viz_tools as viz


@pytest.fixture(autouse=True)
def _assets(tmp_path, monkeypatch):
    """Assets land in a temp dir, and the engine is 'available'."""
    monkeypatch.setattr(viz, "_assets_dir", lambda: tmp_path / "assets")
    monkeypatch.setattr(viz._edge, "_unavailable", lambda: None)
    return tmp_path


def _asset_files(tmp_path) -> list[Path]:
    d = tmp_path / "assets"
    return sorted(d.iterdir()) if d.exists() else []


def test_a_figure_comes_back_as_png_and_svg_under_one_id(tmp_path):
    out = viz.python_viz(
        code="fig, ax = plt.subplots()\n"
             "ax.bar(['a','b'], [1,2], color=fpl_theme.ACCENT)\n"
             "fpl_theme.title_block(fig, 'T')\n"
             "save(fig)\n",
        caption="cap",
    )
    assert "CHART_SAVED chart_id=" in out and "cap" in out
    cid = out.split("chart_id=")[1].split()[0]
    exts = {p.suffix for p in _asset_files(tmp_path) if p.stem == cid}
    assert exts == {".png", ".svg"}


def test_an_unsaved_single_figure_is_rescued_not_lost(tmp_path):
    out = viz.python_viz(code="plt.plot([1, 2, 3])\n")
    assert "CHART_SAVED" in out, out


def test_the_network_fence_actually_fences(tmp_path):
    """The accident this exists for: a stray HTTP call inside plotting code.
    The error must come back verbatim so the agent learns the rule."""
    out = viz.python_viz(
        code="import urllib.request\n"
             "urllib.request.urlopen('http://example.com')\n",
    )
    assert "CHART_SAVED" not in out
    assert "network is disabled in python_viz" in out


def test_a_crash_returns_the_traceback_for_the_agent_to_iterate_on(tmp_path):
    out = viz.python_viz(code="1 / 0\n")
    assert "ZeroDivisionError" in out and "CHART_SAVED" not in out


def test_no_figure_is_an_instruction_not_a_silent_success(tmp_path):
    out = viz.python_viz(code="x = 1\n")
    assert "saved no figure" in out and "save(fig)" in out


def test_a_dataset_arrives_as_a_dataframe(tmp_path, monkeypatch):
    class FakeResult:
        rows = [{"web_name": "Salah", "own": 60.1},  # noqa: RUF012 - a test stub, not a model
                {"web_name": "Haaland", "own": 55.0}]
        columns = ["web_name", "own"]
        truncated = False
        row_count = 2

    from fpl_mcp.tools import chat_tools
    monkeypatch.setattr(chat_tools, "_run_guarded", lambda sql: FakeResult())
    out = viz.python_viz(
        code="df = data('own')\n"
             "assert list(df.columns) == ['web_name', 'own'], df.columns\n"
             "plt.bar(df['web_name'], df['own'])\n",
        datasets_json=json.dumps([{"name": "own", "sql": "SELECT 1"}]),
    )
    assert "CHART_SAVED" in out, out


def test_a_truncated_dataset_is_refused_not_drawn(tmp_path, monkeypatch):
    """A figure over a silently-cut population is the confident-wrong-number
    bug wearing a chart; the tool refuses and says how to fix the SQL."""
    class Truncated:
        rows, columns, truncated, row_count = [], [], True, 1000

    from fpl_mcp.tools import chat_tools
    monkeypatch.setattr(chat_tools, "_run_guarded", lambda sql: Truncated())
    out = viz.python_viz(
        code="plt.plot([1])\n",
        datasets_json=json.dumps([{"name": "big", "sql": "SELECT *"}]),
    )
    assert "truncated" in out and "CHART_SAVED" not in out


def test_dataset_names_must_be_identifiers(tmp_path):
    out = viz.python_viz(
        code="plt.plot([1])\n",
        datasets_json=json.dumps([{"name": "../evil", "sql": "SELECT 1"}]),
    )
    assert "identifier-safe" in out and "CHART_SAVED" not in out
