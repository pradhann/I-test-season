"""Pure helpers for the chat toolbelt. No MCP server import, by design.

Everything here is a plain function over plain data so the engine repository's
contract tests can import and exercise it (rendering caps, the ``$param``
substitution, the 10-second analysis budget) without constructing a FastMCP
server. ``tools/chat_tools.py`` wraps these in ``@mcp.tool()`` registrations.
"""

from __future__ import annotations

import concurrent.futures
import re
from typing import Any, Callable, Optional

# -- output caps (Argus's analysis-script contract, scaled for chat) ---------

#: Rows shown in a summary view. Beyond this the output carries a truncation
#: marker naming how many rows were omitted.
SUMMARY_ROWS = 200
#: Bytes of rendered table allowed before rows are dropped to fit.
SUMMARY_BYTES = 50 * 1024
#: How many rows the guard is asked for. Fetching more than we show is what
#: makes the omitted-count in the truncation marker a real number instead of
#: "some". Bounded so a cartesian mistake still cannot produce a 5MB frame.
SCAN_ROWS = 5_000

#: Wall-clock budget for a saved analysis, seconds. Argus enforces the same
#: number at authoring time so a slow query is the author's problem, not a
#: consumer's surprise.
ANALYSIS_BUDGET_S = 10.0

BUDGET_ERROR = (
    f"ERROR: exceeded the {ANALYSIS_BUDGET_S:.0f}s analysis budget — push "
    "filtering and aggregation into SQL; if it genuinely cannot fit, say so"
)


def _cell(v: Any) -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        if v != v:  # NaN
            return "–"
        return f"{v:.2f}"
    return str(v)


def render_rows(
    columns: list[str],
    rows: list[dict[str, Any]],
    *,
    max_rows: int = SUMMARY_ROWS,
    max_bytes: int = SUMMARY_BYTES,
    scan_truncated: bool = False,
) -> str:
    """Rows as a markdown table under both a row cap and a byte cap.

    ``scan_truncated`` says the caller's own fetch was cut at its scan cap, so
    the omitted count is a floor, rendered as ``N+``.
    """
    if not rows:
        return "(no rows)"
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join("---" for _ in columns) + "|",
    ]
    budget = max_bytes - sum(len(l) + 1 for l in lines)
    shown = 0
    for row in rows[:max_rows]:
        line = "| " + " | ".join(_cell(row.get(c)) for c in columns) + " |"
        if budget - (len(line) + 1) < 0:
            break
        budget -= len(line) + 1
        lines.append(line)
        shown += 1
    omitted = len(rows) - shown
    if omitted > 0 or scan_truncated:
        count = f"{omitted}{'+' if scan_truncated else ''}"
        lines.append(
            f"...{count} more rows omitted — aggregate or filter in SQL "
            f"(summary shows at most {max_rows} rows / {max_bytes // 1024}KB)."
        )
    return "\n".join(lines)


# -- $param substitution ------------------------------------------------------

_PARAM_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def substitute_params(
    sql: str, values: dict[str, Any]
) -> tuple[str, list[Any], list[str]]:
    """Replace every ``$name`` in ``sql`` with a ``?`` placeholder.

    Returns ``(sql_with_placeholders, bind_values_in_order, missing_names)``.
    Values are BOUND as DuckDB parameters, never interpolated into the text —
    a note or a season string in a parameter can therefore never become SQL.
    A ``$name`` with no value is reported in ``missing_names`` rather than
    guessed at.
    """
    binds: list[Any] = []
    missing: list[str] = []

    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            if name not in missing:
                missing.append(name)
            return m.group(0)
        binds.append(values[name])
        return "?"

    out = _PARAM_RE.sub(repl, sql)
    return out, binds, missing


def param_names(sql: str) -> list[str]:
    """The distinct ``$name`` parameters a saved analysis declares in its SQL."""
    seen: list[str] = []
    for m in _PARAM_RE.finditer(sql):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


# -- the 10-second budget ------------------------------------------------------


def run_with_budget(
    fn: Callable[[], Any], *, budget_s: float = ANALYSIS_BUDGET_S
) -> tuple[Any, Optional[str]]:
    """Run ``fn`` under a wall-clock budget.

    Returns ``(result, None)`` on success within budget and ``(None, error)``
    otherwise — including the case where the call *finished* but took longer
    than the budget, which is Argus's contract: a slow success is still a
    failed authoring run, because the consumer of the saved analysis will hit
    the same wall. The worker thread is not joined on timeout (DuckDB reads a
    private copy, so an abandoned query holds no lock anyone needs).
    """
    import time

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    started = time.monotonic()
    fut = ex.submit(fn)
    try:
        result = fut.result(timeout=budget_s)
    except concurrent.futures.TimeoutError:
        return None, BUDGET_ERROR
    except Exception:
        raise
    finally:
        ex.shutdown(wait=False)
    if time.monotonic() - started > budget_s:
        return None, BUDGET_ERROR
    return result, None


# -- analysis names ------------------------------------------------------------

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def valid_name(name: str) -> bool:
    """Filesystem- and git-safe analysis names: lower snake/kebab, <=64 chars."""
    return bool(NAME_RE.match(name))
