"""Every module a scheduled step runs with ``python -m`` must be executable.

A module with no ``if __name__ == "__main__"`` block imports, defines its
``main()`` and exits 0 having done nothing, and ``run_step`` records that as
a successful step. That is how content ingest, transcription, claim
extraction and creator scoring ran as silent no-ops for nine hours on
2026-09-18 after a split dropped the guard from
``fpl_edge/ingest/content/pipeline.py`` (b952101).

The module list is not hand-maintained: it is read from the same inventory
the pipelines runbook and the double-work guard use, so a new ``-m`` step is
covered the moment it is scheduled.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import pipelines_runbook  # noqa: E402


def _dash_m_modules() -> list[str]:
    modules = set()
    for target in pipelines_runbook.target_map():
        if target.startswith(("callable:", "scripts/")):
            continue
        modules.add(target.split()[0])
    return sorted(modules)


MODULES = _dash_m_modules()


def test_the_inventory_found_the_scheduled_modules():
    assert "fpl_edge.ingest.content.pipeline" in MODULES
    assert len(MODULES) >= 10, MODULES


@pytest.mark.parametrize("module", MODULES)
def test_module_is_executable_with_dash_m(module):
    proc = subprocess.run(
        [sys.executable, "-m", module, "--help"],
        capture_output=True, text=True, timeout=180,
    )
    out = (proc.stdout + proc.stderr).strip()
    assert out, (
        f"python -m {module} --help produced no output: the module has no "
        f"__main__ guard, so every scheduled `-m {module}` step is a silent "
        f"no-op that run_step records as ok")
