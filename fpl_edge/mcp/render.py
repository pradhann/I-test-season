"""Pure helpers for the toolbelt. No server import, by design.

Everything here is a plain function over plain data, so the contract tests
exercise the rendering caps, the ``$param`` substitution and the analysis
budget without constructing a FastMCP instance. ``tools/analysis.py`` wraps
these in tool registrations.

The ``$param`` substitution and the name validation live in
``fpl_edge/interfaces/analyses.py``, because they belong to the saved-analysis
store rather than to rendering; they are re-exported here so one import keeps
working for callers that want both.
"""

from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable
from typing import Any

from fpl_edge.interfaces.analyses import bind as substitute_params
from fpl_edge.interfaces.analyses import param_names, valid_name

__all__ = [
    "ANALYSIS_BUDGET_S",
    "BUDGET_ERROR",
    "SCAN_ROWS",
    "SUMMARY_BYTES",
    "SUMMARY_ROWS",
    "param_names",
    "render_rows",
    "run_with_budget",
    "substitute_params",
    "valid_name",
]

# -- output caps --------------------------------------------------------------

#: Rows shown in a summary view. Beyond this the output carries a truncation
#: marker naming how many rows were omitted.
SUMMARY_ROWS = 200

#: Bytes of rendered table allowed before rows are dropped to fit.
SUMMARY_BYTES = 50 * 1024

#: How many rows the guard is asked for. Fetching more than is shown is what
#: makes the omitted count in the truncation marker a real number instead of
#: "some". Bounded so a cartesian mistake still cannot produce a 5MB frame.
SCAN_ROWS = 5_000

#: Wall-clock budget for a saved analysis, seconds.
ANALYSIS_BUDGET_S = 10.0

BUDGET_ERROR = (
    f"ERROR: exceeded the {ANALYSIS_BUDGET_S:.0f}s analysis budget. Push "
    "filtering and aggregation into SQL; if it cannot fit, say so"
)

#: What an absent value renders as. A dash, not a zero: zero reads as a
#: measured number and this is the absence of one.
_ABSENT = "-"


def _cell(value: Any) -> str:
    if value is None:
        return _ABSENT
    if isinstance(value, float):
        if value != value:  # NaN
            return _ABSENT
        return f"{value:.2f}"
    return str(value)


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
    budget = max_bytes - sum(len(line) + 1 for line in lines)
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
            f"...{count} more rows omitted. Aggregate or filter in SQL; the "
            f"summary shows at most {max_rows} rows or {max_bytes // 1024}KB."
        )
    return "\n".join(lines)


def run_with_budget(
    fn: Callable[[], Any], *, budget_s: float = ANALYSIS_BUDGET_S
) -> tuple[Any, str | None]:
    """Run ``fn`` under a wall-clock budget.

    Returns ``(result, None)`` on success within budget and ``(None, error)``
    otherwise, including the case where the call finished but took longer than
    the budget. A slow success is still a failed authoring run, because the
    next consumer of that saved analysis hits the same wall. The worker thread
    is not joined on timeout: the query runs against a private read copy, so
    an abandoned one holds no lock anybody needs.
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    started = time.monotonic()
    future = pool.submit(fn)
    try:
        result = future.result(timeout=budget_s)
    except concurrent.futures.TimeoutError:
        return None, BUDGET_ERROR
    except Exception:
        raise
    finally:
        pool.shutdown(wait=False)
    if time.monotonic() - started > budget_s:
        return None, BUDGET_ERROR
    return result, None
