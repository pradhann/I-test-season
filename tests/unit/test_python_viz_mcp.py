"""python_viz: the sandbox, the theme, the honest failures.

These assertions came over from the old toolbelt's own test unchanged,
because it is the same sandbox: the preamble, the fence text and the save
signature were carried across byte for byte, so an agent that learned the
old error messages reads the same ones.

These run the real sandbox subprocess, a couple of seconds each, with no
dataset so no warehouse is touched. The one test that needs a dataset fakes
the guarded query at its seam.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fpl_edge.mcp.tools import analysis as viz


@pytest.fixture(autouse=True)
def _assets(tmp_path, monkeypatch):
    """Assets land in a temp directory, and the warehouse is 'present'."""
    monkeypatch.setattr(viz, "_assets_dir", lambda: tmp_path / "assets")
    monkeypatch.setattr(viz.context, "warehouse_missing", lambda: None)
    return tmp_path


def _asset_files(tmp_path) -> list[Path]:
    directory = tmp_path / "assets"
    return sorted(directory.iterdir()) if directory.exists() else []


def _text(payload) -> str:
    """Everything the payload says, flattened, for a substring assertion."""
    return json.dumps(payload, default=str)


def test_a_figure_comes_back_as_png_and_svg_under_one_id(tmp_path):
    out = viz.python_viz(
        code="fig, ax = plt.subplots()\n"
             "ax.bar(['a','b'], [1,2], color=fpl_theme.ACCENT)\n"
             "fpl_theme.title_block(fig, 'T')\n"
             "save(fig)\n",
        caption="cap",
    )
    assert out["ok"] is True, out
    chart_ids = out["result"]["chart_ids"]
    assert len(chart_ids) == 1
    assert out["result"]["caption"] == "cap"
    exts = {p.suffix for p in _asset_files(tmp_path) if p.stem == chart_ids[0]}
    assert exts == {".png", ".svg"}


def test_an_unsaved_single_figure_is_rescued_not_lost(tmp_path):
    out = viz.python_viz(code="plt.plot([1, 2, 3])\n")
    assert out["ok"] is True, out
    assert out["result"]["chart_ids"]


def test_the_network_fence_actually_fences(tmp_path):
    """The accident this exists for: a stray HTTP call inside plotting code.

    The error comes back verbatim so the agent learns the rule.
    """
    out = viz.python_viz(
        code="import urllib.request\n"
             "urllib.request.urlopen('http://example.com')\n",
    )
    assert out["ok"] is False
    assert "network is disabled in python_viz" in _text(out)


def test_a_crash_returns_the_traceback_for_the_agent_to_iterate_on(tmp_path):
    out = viz.python_viz(code="1 / 0\n")
    assert out["ok"] is False
    assert "ZeroDivisionError" in _text(out)


def test_no_figure_is_an_instruction_not_a_silent_success(tmp_path):
    out = viz.python_viz(code="x = 1\n")
    assert out["ok"] is False
    assert "saved no figure" in out["reason"]
    assert "save(fig)" in out["reason"]


def test_a_dataset_arrives_as_a_dataframe(tmp_path, monkeypatch):
    class FakeResult:
        rows = [{"web_name": "Salah", "own": 60.1},  # noqa: RUF012 - a stub, not a model
                {"web_name": "Haaland", "own": 55.0}]
        columns = ["web_name", "own"]  # noqa: RUF012 - a stub
        truncated = False
        row_count = 2

    monkeypatch.setattr(viz, "_run_guarded", lambda sql: FakeResult())
    out = viz.python_viz(
        code="df = data('own')\n"
             "assert list(df.columns) == ['web_name', 'own'], df.columns\n"
             "plt.bar(df['web_name'], df['own'])\n",
        datasets_json=json.dumps([{"name": "own", "sql": "SELECT 1"}]),
    )
    assert out["ok"] is True, out
    assert out["result"]["chart_ids"]


def test_a_truncated_dataset_is_refused_not_drawn(tmp_path, monkeypatch):
    """A figure over a silently cut population is a confident wrong number.

    The tool refuses and says how to fix the SQL.
    """
    class Truncated:
        rows, columns, truncated, row_count = [], [], True, 1000

    monkeypatch.setattr(viz, "_run_guarded", lambda sql: Truncated())
    out = viz.python_viz(
        code="plt.plot([1])\n",
        datasets_json=json.dumps([{"name": "big", "sql": "SELECT *"}]),
    )
    assert out["ok"] is False
    assert "truncated" in out["reason"]


def test_dataset_names_must_be_identifiers(tmp_path):
    out = viz.python_viz(
        code="plt.plot([1])\n",
        datasets_json=json.dumps([{"name": "../evil", "sql": "SELECT 1"}]),
    )
    assert out["ok"] is False
    assert "identifier-safe" in out["reason"]


def test_datasets_json_must_be_a_list(tmp_path):
    out = viz.python_viz(code="plt.plot([1])\n", datasets_json='{"name": "x"}')
    assert out["ok"] is False
    assert "JSON LIST" in out["reason"]


def test_empty_code_is_refused_before_a_sandbox_is_built(tmp_path):
    out = viz.python_viz(code="   ")
    assert out["ok"] is False
    assert out["reason"] == "code is empty."
    assert _asset_files(tmp_path) == []
