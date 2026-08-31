"""python_viz: the agent writes real plotting code; the harness owns the look.

Replaces ``make_chart``'s four-kind spec plotter (CHAT_ARCHITECTURE §4). The
agent sends Python; it runs in a fresh, isolated interpreter with the house
theme pre-applied and its data pre-materialized as parquet -- the sandbox
never opens the warehouse and never sees a credential.

The sandbox is an ACCIDENT fence, not a security boundary: this is our own
agent plotting for a single local user, so the threats are a stray network
call, a runaway loop, or a memory balloon -- met with a socket stub, rlimits,
``python -I``, a scrubbed env, and a wall timeout. Argus's credential-free
runner (argus_architecture §2.4), scaled to the actual threat model.

Data flow (files, not credentials):
    datasets_json names {name, sql} pairs -> each runs through the SAME
    guarded read-only query path as the ``query`` tool -> lands as
    <sandbox>/<name>.parquet -> user code calls data("name").
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path

from fpl_mcp.server import mcp
from fpl_mcp.tools import edge_tools as _edge

_WALL_TIMEOUT_S = 90.0
_CPU_SECONDS = 45
_THEME_SRC = Path(__file__).resolve().parents[2] / "fpl_edge" / "platform" / "fpl_theme.py"


def _assets_dir() -> Path:
    return _edge._HOME / "data" / "warehouse" / "chat" / "assets"


#: Runs before user code inside the sandbox. Everything the user code may
#: rely on -- theme applied, loaders, save() -- is defined here, and the
#: accident fences go up first. ``python -I`` keeps sys.path bare, so the
#: sandbox dir (holding fpl_theme.py and the parquet files) is added
#: explicitly and nothing else ever is.
_PREAMBLE = """\
import os, sys
sys.path.insert(0, {sandbox!r})

# accident fences: no network, bounded CPU/memory. rlimit failures are
# tolerated (RLIMIT_AS is unreliable on Darwin) -- the wall timeout in the
# parent is the guarantee.
# The fence sits on connect/resolve, NOT on the socket class itself:
# replacing the class breaks `import ssl` (it subclasses socket.socket), so
# innocent imports would die with a garbage TypeError instead of this
# message. A socket you can construct but never connect teaches correctly.
import socket as _socket
def _no_net(*a, **k):
    raise RuntimeError("network is disabled in python_viz -- data arrives via data(name)")
_socket.socket.connect = _no_net
_socket.socket.connect_ex = _no_net
_socket.socket.sendto = _no_net
_socket.create_connection = _no_net
_socket.getaddrinfo = _no_net
try:
    import resource as _resource
    _resource.setrlimit(_resource.RLIMIT_CPU, ({cpu}, {cpu}))
    try:
        _resource.setrlimit(_resource.RLIMIT_AS, (2_500_000_000, 2_500_000_000))
    except (ValueError, OSError):
        pass
except Exception:
    pass

import fpl_theme
fpl_theme.apply()
import matplotlib.pyplot as plt
import pandas as pd

def data(name):
    \"\"\"A dataset named in the tool call, as a DataFrame.\"\"\"
    path = os.path.join({sandbox!r}, name + ".parquet")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"no dataset {{name!r}} -- the tool call materialized: {names}")
    return pd.read_parquet(path)

_SAVED = []
def save(fig=None, name="chart"):
    \"\"\"Save the figure as the chart this tool returns. Call once per chart.\"\"\"
    fig = fig or plt.gcf()
    out = os.path.join({sandbox!r}, "out")
    os.makedirs(out, exist_ok=True)
    base = os.path.join(out, f"{{len(_SAVED):02d}}_{{name}}")
    fig.savefig(base + ".png", format="png")
    fig.savefig(base + ".svg", format="svg")
    _SAVED.append(base)
    return base

# ---- user code follows ----
"""

_EPILOGUE = """

# ---- harness epilogue: an un-saved figure is saved rather than lost ----
if not _SAVED and plt.get_fignums():
    save(plt.gcf())
"""


@mcp.tool()
def python_viz(code: str, caption: str = "", datasets_json: str = "[]") -> str:
    """Run Python plotting code in a themed sandbox; get [chart:<id>] markers.

    You write REAL matplotlib code -- any chart, not a fixed menu. The house
    theme (Athletic/Opta grammar) is already applied when your code runs:
    horizontal-only recessive grid, no chart box, editorial title block.

    In scope for your code:
      - ``data(name)`` -> DataFrame for each dataset you passed (see below).
      - ``fpl_theme`` is imported: ``fpl_theme.title_block(fig, title, sub)``,
        ``footer_source(fig, "source · as-of ...")``, ``label_last_point``,
        ``zero_line(ax)``, ``club_color(team_code)``, palettes
        ``ACCENT/SERIES/DIVERGING/CLUB``.
      - ``save(fig)`` marks a figure as a deliverable (auto-called for a
        single un-saved figure). ``plt`` and ``pd`` are imported.
      - No network, no warehouse access: data arrives ONLY via datasets.

    datasets_json: a JSON list of {"name": str, "sql": str}. Each sql runs
    through the guarded read-only warehouse path (same as the query tool --
    table macros like sem_players(now()) work) and lands as data(name).

    House law the theme cannot enforce, so you must: ONE axis (never dual);
    colour follows the entity, never its rank; club colours only for club
    marks; diverging ramps only for signed quantities; sort in SQL; title
    via fpl_theme.title_block, never ax.set_title; always footer_source with
    the data's as-of instant.

    Returns one ``CHART_SAVED chart_id=<id>`` line per saved figure -- embed
    each as ``[chart:<id>]`` on its own line in your prose -- plus your
    code's stdout/stderr (errors come back verbatim; fix and retry).
    """
    problem = _edge._unavailable()
    if problem:
        return problem
    if not code.strip():
        return "code is empty."
    try:
        datasets = json.loads(datasets_json or "[]")
        assert isinstance(datasets, list)
    except (json.JSONDecodeError, AssertionError):
        return ("datasets_json must be a JSON LIST of {name, sql} objects, "
                "e.g. [{\"name\": \"runs\", \"sql\": \"SELECT ...\"}]")

    sandbox = Path(tempfile.mkdtemp(prefix="fplviz_"))
    try:
        shutil.copy(_THEME_SRC, sandbox / "fpl_theme.py")

        names = []
        from fpl_mcp.tools.chat_tools import _run_guarded
        for i, ds in enumerate(datasets):
            name = str(ds.get("name") or "").strip()
            sql = str(ds.get("sql") or "").strip()
            if not name.isidentifier() or not sql:
                return (f"datasets[{i}] needs an identifier-safe name and "
                        f"non-empty sql (got name={name!r}).")
            try:
                result = _run_guarded(sql)
            except Exception as exc:  # noqa: BLE001 - the agent iterates on the verbatim error
                return f"dataset {name!r} failed: {type(exc).__name__}: {exc}"
            # guarded_query returns a QueryResult (rows as dicts), and its
            # truncation is a fact the chart must not hide: a figure drawn on
            # a silently cut dataset is the confident-wrong-number bug again.
            if result.truncated:
                return (f"dataset {name!r} was truncated at "
                        f"{result.row_count} rows by the guarded query cap -- "
                        "aggregate or LIMIT in the SQL so the chart draws the "
                        "whole population it claims to.")
            import pandas as pd
            frame = pd.DataFrame(result.rows, columns=result.columns or None)
            frame.to_parquet(sandbox / f"{name}.parquet", index=False)
            names.append(f"{name} ({len(frame)} rows)")

        runner = sandbox / "_runner.py"
        preamble = _PREAMBLE.format(sandbox=str(sandbox), cpu=_CPU_SECONDS,
                                    names=", ".join(names) or "none")
        runner.write_text(preamble + textwrap.dedent(code) + _EPILOGUE)

        try:
            proc = subprocess.run(
                [sys.executable, "-I", str(runner)],
                capture_output=True, text=True, cwd=str(sandbox),
                timeout=_WALL_TIMEOUT_S,
                env={"PATH": "/usr/bin:/bin",
                     "MPLCONFIGDIR": str(sandbox / ".mpl"),
                     "FPL_THEME_MODE": "dark"},
                check=False,
            )
        except subprocess.TimeoutExpired:
            return (f"your code exceeded the {int(_WALL_TIMEOUT_S)}s wall "
                    "timeout and was killed. Simplify, or move aggregation "
                    "into the dataset SQL.")

        tail = "\n".join((proc.stdout + "\n" + proc.stderr).strip().splitlines()[-25:])
        if proc.returncode != 0:
            return f"your code failed (exit {proc.returncode}):\n{tail}"

        out_dir = sandbox / "out"
        charts = sorted(out_dir.glob("*.png")) if out_dir.exists() else []
        if not charts:
            return ("your code ran but saved no figure -- call save(fig) "
                    "(or leave exactly one open figure)."
                    + (f"\n{tail}" if tail else ""))

        assets = _assets_dir()
        assets.mkdir(parents=True, exist_ok=True)
        lines = []
        for png in charts:
            cid = uuid.uuid4().hex
            shutil.copy(png, assets / f"{cid}.png")
            svg = png.with_suffix(".svg")
            if svg.exists():
                shutil.copy(svg, assets / f"{cid}.svg")
            lines.append(f"CHART_SAVED chart_id={cid}")
        if caption:
            lines.append(caption)
        if tail:
            lines.append(tail)
        return "\n".join(lines)
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
