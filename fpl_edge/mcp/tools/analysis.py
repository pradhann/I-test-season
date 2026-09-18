"""Free SQL, saved analyses, and the plotting sandbox.

The three tools here are the ones with no panel behind them, because they are
not questions about the data. ``query`` is the escape hatch for a question no
panel shapes, ``run_analysis`` replays a saved one, and ``python_viz`` renders
rather than reads.

Every read still goes through ``fpl_edge.platform.query.guarded_query``, the
same guard the web's own query endpoint uses: one statement, read verbs only,
row and byte caps, and an optional point-in-time replacement of the raw
tables. There is no second SQL path in this server.

Errors come back verbatim with one line of remediation, because the caller
reading them can fix its own SQL only if it sees what DuckDB actually said.

``python_viz`` copies ``fpl_edge/platform/fpl_theme.py`` into the sandbox by
assembled path, not by dotted import. ``tests/unit/test_fpl_theme_isolation.py``
pins that file because nothing else names it: an ``fpl_edge`` import added to
it would raise inside the sandbox and every chart would stop rendering.
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
from typing import Any

from fpl_edge.mcp import context
from fpl_edge.mcp.adapter import error, refusal, store_call
from fpl_edge.mcp.render import SCAN_ROWS, render_rows, run_with_budget
from fpl_edge.mcp.server import mcp

#: Where the theme file lives, assembled from components. Named by
#: tests/unit/test_fpl_theme_isolation.py, which is the only thing that does.
_THEME_SRC = (
    Path(__file__).resolve().parents[3]
    / "fpl_edge" / "platform" / "fpl_theme.py"
)

#: Where python_viz writes its PNGs. The chat pane serves this directory and
#: replaces the chart markers in prose with the image.
_ASSETS_SUBPATH = ("data", "warehouse", "chat", "assets")

_WALL_TIMEOUT_S = 90.0
_CPU_SECONDS = 45

_QUERY_REMEDIATION = (
    "Remediation: send exactly one read-only SELECT over the sem_ macros, for "
    "example SELECT ... FROM sem_players(now()) WHERE season = '2026-27'. "
    "DESCRIBE SELECT * FROM <macro>(now()) LIMIT 0 lists a macro's columns."
)


def _assets_dir() -> Path:
    return context.engine_home().joinpath(*_ASSETS_SUBPATH)


def _run_guarded(sql: str, params: list | tuple = (), *, as_of=None):
    """One guarded read against the warehouse, sized for a chat reply."""
    from fpl_edge.platform.query import guarded_query

    return guarded_query(
        sql, params, as_of=as_of, db=context.db_path(), max_rows=SCAN_ROWS,
    )


def _rendered(res, *, head: str) -> dict[str, Any]:
    return {
        "header": head,
        "row_count": res.row_count,
        "elapsed_ms": res.elapsed_ms,
        "as_of": res.as_of,
        "columns": list(res.columns),
        "notes": list(res.notes),
        "truncated": bool(res.truncated),
        "table": render_rows(res.columns, res.rows,
                             scan_truncated=res.truncated),
        "rows": res.rows,
    }


@mcp.tool()
def query(sql: str, as_of: str | None = None) -> dict[str, Any]:
    """Run one read-only SQL statement against the warehouse. The workhorse.

    Use this for any question the shaped tools do not answer. The query
    surface is the sem_ table macros, each taking an as-of TIMESTAMPTZ and
    answering with what was knowable at that instant:

        SELECT web_name, price, selected_by_pct
        FROM sem_players(now()) WHERE season = '2026-27'
        ORDER BY selected_by_pct DESC LIMIT 10

    The guard enforces one statement and read verbs only, and its violations
    come back in its own words. The raw fact and dim tables are queryable too
    when a macro does not carry what is needed. Aggregate and filter in SQL:
    the summary shows at most 200 rows or 50KB and then reports how many rows
    were omitted.

    Args:
        sql: One read-only statement. Write timestamps as literals, for
            example sem_projections(TIMESTAMPTZ '2026-08-22T10:00:00Z'), or
            call now().
        as_of: ISO-8601 instant carrying a timezone. When given, the raw
            point-in-time tables are additionally replaced by views filtered
            to that instant, so even SQL written without an as_of predicate
            cannot read the future. Macros still take their own argument.

    Returns:
        The envelope with result holding the row count, the elapsed time, the
        guard's notes, the rendered table and the rows themselves, or the
        error verbatim plus one remediation line.
    """
    from fpl_edge.platform.query import QueryError

    missing = context.warehouse_missing()
    if missing:
        return refusal("query", missing)
    try:
        when = context.parse_as_of(as_of)
    except ValueError as exc:
        return refusal("query", f"{exc}\n{_QUERY_REMEDIATION}")

    try:
        res = _run_guarded(sql, as_of=when)
    except QueryError as exc:
        return error("query", f"{exc}\n{_QUERY_REMEDIATION}", kind="QueryError")
    except Exception as exc:  # noqa: BLE001 - DuckDB's message is the useful part
        return error("query", f"{type(exc).__name__}: {exc}\n"
                              f"{_QUERY_REMEDIATION}", kind=type(exc).__name__)

    head = (
        f"[query, {res.row_count} rows, {res.elapsed_ms}ms"
        + (f", as-of {res.as_of}" if res.as_of else "") + "]"
    )
    return store_call("query", "fpl_edge.platform.query",
                      _rendered(res, head=head))


@mcp.tool()
def save_analysis(
    name: str,
    description: str,
    sql: str,
    params_schema: dict | None = None,
) -> dict[str, Any]:
    """Save a reusable parameterised SQL analysis and commit it. WRITES.

    An analysis is one read-only statement over the warehouse, stored as JSON
    in the engine repository and committed on its own, so "why did the bot say
    that" is answerable from git history months later. Save one when a
    question will recur; run it with run_analysis.

    Parameters are written in the SQL as $name and bound as real DuckDB
    parameters at run time, never interpolated:

        SELECT web_name, xpts_mean FROM sem_projection_consensus(now())
        WHERE season = $season AND gw = $gw ORDER BY xpts_mean DESC LIMIT 10

    run_analysis enforces a 10 second wall budget, so push filtering and
    aggregation into the SQL now, at authoring time.

    Args:
        name: Identifier: lower-case letters, digits, underscore or hyphen, at
            most 64 characters. Saving an existing name overwrites it and the
            old version stays in git.
        description: One or two sentences: what it answers and when to use it.
        sql: One read-only statement, with $param placeholders allowed.
        params_schema: Optional map of parameter to {type, description,
            default}. A default is applied when run_analysis is called without
            that parameter.

    Returns:
        The envelope with store naming the module that wrote, and result
        holding the path, the declared parameters and what git actually did,
        including the case where nothing was committed because the file was
        already identical.
    """
    import os

    from fpl_edge.interfaces import analyses

    try:
        saved = analyses.save(
            name, description, sql, params_schema,
            root=context.engine_home(),
            conversation=os.environ.get("ARGUS_CONV_ID", "").strip() or None,
        )
    except analyses.AnalysisError as exc:
        return refusal("save_analysis", f"{exc}\n{_QUERY_REMEDIATION}")

    return store_call("save_analysis", "fpl_edge.interfaces.analyses", {
        "name": saved.analysis.name,
        "path": saved.relative_path,
        "params": saved.analysis.params,
        "committed": saved.committed,
        "commit_sha": saved.commit_sha,
        "git_note": saved.git_note,
        "budget_note": "the 10 second budget applies at run time.",
    })


@mcp.tool()
def run_analysis(name: str, params: dict | None = None) -> dict[str, Any]:
    """Run a saved analysis under the 10 second budget.

    Parameters are bound as DuckDB parameters, with the saved defaults applied
    first. Output follows the same contract as query: a 200 row or 50KB
    summary with an omitted count.

    A run that exceeds the budget returns an error with remediation rather
    than a partial result. The fix is in the analysis SQL, not in the call.

    Args:
        name: The analysis name, as list_analyses shows it.
        params: Values for the statement's $params, for example
            {"season": "2026-27", "gw": 3}. A missing value with no default is
            an error, never a guess.

    Returns:
        The envelope with result holding the table, or the budget error, or
        the SQL error verbatim plus one remediation line.
    """
    from fpl_edge.interfaces import analyses
    from fpl_edge.platform.query import QueryError

    missing = context.warehouse_missing()
    if missing:
        return refusal("run_analysis", missing)
    try:
        saved = analyses.load(name, root=context.engine_home())
    except analyses.AnalysisError as exc:
        return refusal("run_analysis", str(exc))

    values = analyses.defaults_for(saved)
    values.update(params or {})
    statement, binds, absent = analyses.bind(saved.sql, values)
    if absent:
        declared = {
            key: (saved.params_schema.get(key) or {}).get(
                "description", "no description")
            for key in absent
        }
        return refusal(
            "run_analysis",
            f"missing parameter value(s) for {absent}. Declared: {declared}. "
            f"Pass them in params.",
        )

    try:
        res, budget_error = run_with_budget(
            lambda: _run_guarded(statement, binds))
    except QueryError as exc:
        return error("run_analysis", f"{exc}\n{_QUERY_REMEDIATION}",
                     kind="QueryError")
    except Exception as exc:  # noqa: BLE001
        return error("run_analysis", f"{type(exc).__name__}: {exc}\n"
                                     f"{_QUERY_REMEDIATION}",
                     kind=type(exc).__name__)
    if budget_error:
        return error(
            "run_analysis",
            f"{budget_error}\n(analysis {name!r}; edit it with save_analysis)",
            kind="BudgetExceeded",
        )

    head = (
        f"[analysis {name!r}, {res.row_count} rows, {res.elapsed_ms}ms, "
        f"params {values or 'none'}]"
    )
    payload = _rendered(res, head=head)
    payload["analysis"] = {"name": saved.name, "description": saved.description,
                           "params": values}
    return store_call("run_analysis", "fpl_edge.interfaces.analyses", payload)


@mcp.tool()
def list_analyses() -> dict[str, Any]:
    """Every saved analysis: name, parameters, description.

    Check here before writing a new query for a recurring question. Running a
    saved analysis is cheaper and its history is in git.

    Returns:
        The envelope with result holding one entry per analysis, or a reason
        when none have been saved.
    """
    from fpl_edge.interfaces import analyses

    saved = analyses.list_all(root=context.engine_home())
    return store_call("list_analyses", "fpl_edge.interfaces.analyses", {
        "analyses": [
            {"name": item.name, "description": item.description,
             "params": item.params, "saved_utc": item.saved_utc}
            for item in saved
        ],
        "reason": None if saved else (
            "no analysis has been saved yet. Create one with save_analysis."
        ),
    })


@mcp.tool()
def python_viz(
    code: str,
    caption: str = "",
    datasets_json: str = "[]",
) -> dict[str, Any]:
    """Run matplotlib code in a themed sandbox and get chart markers back.

    You write real plotting code, not a fixed menu. The house theme is applied
    before your code runs: horizontal-only recessive grid, no chart box,
    editorial title block.

    In scope for your code:
      - data(name) returns a DataFrame for each dataset you passed.
      - fpl_theme is imported: title_block(fig, title, sub), footer_source(fig,
        "source, as-of ..."), label_last_point, zero_line(ax),
        club_color(team_code), and the ACCENT, SERIES, DIVERGING and CLUB
        palettes.
      - save(fig) marks a figure as a deliverable, called automatically for a
        single unsaved figure. plt and pd are imported.
      - No network and no warehouse access. Data arrives only via datasets.

    datasets_json is a JSON list of {"name": str, "sql": str}. Each statement
    runs through the same guarded read-only path as query and lands as
    data(name). A dataset the guard truncated is refused rather than drawn: a
    figure over a silently cut dataset is a confident wrong number.

    House rules the theme cannot enforce, so your code must: one axis, never
    dual; colour follows the entity, never its rank; club colours only for
    club marks; diverging ramps only for signed quantities; sort in SQL; title
    through fpl_theme.title_block rather than ax.set_title; always
    footer_source with the data's as-of instant.

    Args:
        code: The plotting code to run.
        caption: One line to attach to the saved charts.
        datasets_json: The datasets, as a JSON list of {name, sql}.

    Returns:
        The envelope with result holding one chart id per saved figure, to
        embed as a chart marker on its own line, plus your code's output.
        Errors come back verbatim so you can fix and retry.
    """
    missing = context.warehouse_missing()
    if missing:
        return refusal("python_viz", missing)
    if not code.strip():
        return refusal("python_viz", "code is empty.")
    try:
        datasets = json.loads(datasets_json or "[]")
        if not isinstance(datasets, list):
            raise ValueError("not a list")
    except (json.JSONDecodeError, ValueError):
        return refusal(
            "python_viz",
            'datasets_json must be a JSON LIST of {name, sql} objects, for '
            'example [{"name": "runs", "sql": "SELECT ..."}].',
        )

    sandbox = Path(tempfile.mkdtemp(prefix="fplviz_"))
    try:
        shutil.copy(_THEME_SRC, sandbox / "fpl_theme.py")

        names: list[str] = []
        for i, spec in enumerate(datasets):
            name = str(spec.get("name") or "").strip()
            sql = str(spec.get("sql") or "").strip()
            if not name.isidentifier() or not sql:
                return refusal(
                    "python_viz",
                    f"datasets[{i}] needs an identifier-safe name and "
                    f"non-empty sql, got name={name!r}.",
                )
            try:
                res = _run_guarded(sql)
            except Exception as exc:  # noqa: BLE001 - the verbatim error is the fix
                return error("python_viz",
                             f"dataset {name!r} failed: {type(exc).__name__}: "
                             f"{exc}", kind=type(exc).__name__)
            if res.truncated:
                return refusal(
                    "python_viz",
                    f"dataset {name!r} was truncated at {res.row_count} rows "
                    f"by the guarded query cap. Aggregate or LIMIT in the SQL "
                    f"so the chart draws the whole population it claims to.",
                )
            import pandas as pd

            frame = pd.DataFrame(res.rows, columns=res.columns or None)
            frame.to_parquet(sandbox / f"{name}.parquet", index=False)
            names.append(f"{name} ({len(frame)} rows)")

        runner = sandbox / "_runner.py"
        runner.write_text(
            _PREAMBLE.format(sandbox=str(sandbox), cpu=_CPU_SECONDS,
                             names=", ".join(names) or "none")
            + textwrap.dedent(code) + _EPILOGUE
        )
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
            return refusal(
                "python_viz",
                f"your code exceeded the {int(_WALL_TIMEOUT_S)}s wall timeout "
                f"and was killed. Simplify, or move aggregation into the "
                f"dataset SQL.",
            )

        tail = "\n".join(
            (proc.stdout + "\n" + proc.stderr).strip().splitlines()[-25:])
        if proc.returncode != 0:
            return error("python_viz",
                         f"your code failed (exit {proc.returncode}):\n{tail}",
                         kind="SandboxExit")

        out_dir = sandbox / "out"
        charts = sorted(out_dir.glob("*.png")) if out_dir.exists() else []
        if not charts:
            return refusal(
                "python_viz",
                "your code ran but saved no figure. Call save(fig), or leave "
                "exactly one open figure."
                + (f"\n{tail}" if tail else ""),
            )

        assets = _assets_dir()
        assets.mkdir(parents=True, exist_ok=True)
        chart_ids: list[str] = []
        for png in charts:
            chart_id = uuid.uuid4().hex
            shutil.copy(png, assets / f"{chart_id}.png")
            svg = png.with_suffix(".svg")
            if svg.exists():
                shutil.copy(svg, assets / f"{chart_id}.svg")
            chart_ids.append(chart_id)
        return store_call("python_viz", "fpl_edge.platform.fpl_theme", {
            "chart_ids": chart_ids,
            "markers": [f"CHART_SAVED chart_id={cid}" for cid in chart_ids],
            "caption": caption or None,
            "output": tail or None,
            "datasets": names,
        })
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


#: Runs before user code inside the sandbox. Everything the user code may
#: rely on (theme applied, loaders, save) is defined here, and the accident
#: fences go up first. ``python -I`` keeps sys.path bare, so the sandbox dir
#: (holding fpl_theme.py and the parquet files) is added explicitly and
#: nothing else ever is.
#:
#: Carried over byte for byte from the module this replaced: the fence text,
#: the loader error and the save() signature are what
#: tests/unit/test_python_viz.py asserts on, and an agent that learned the old
#: messages should read the same ones.
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
