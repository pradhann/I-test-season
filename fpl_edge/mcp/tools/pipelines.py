"""``pipelines`` and ``pipeline_log``: what has run, what is due, what failed.

Read only. Starting a pipeline is a POST to the engine's own route, not a tool
here, so nothing in this server can trigger an ingest by being asked a
question about one.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp.adapter import panel_call
from fpl_edge.mcp.server import mcp


@mcp.tool()
def pipelines() -> dict[str, Any]:
    """Every registered pipeline: health with its reason, last run, next due.

    The Pipelines board: each pipeline's health and why it is that colour, its
    last run and how long it took on average, when it is next due, the month's
    credits, and a sparkline of recent runs. Use it for "is the data fresh",
    "did the ingest run" and "why is this number stale".

    Health is a judgement with its reason attached. Quote the reason rather
    than the colour.

    This answers what engine_status and the content-sources listing used to
    answer, with the run ledger behind it instead of a path check.

    Returns:
        The envelope: result, provenance, budget, and gap when the ledger is
        empty. If gap is present, quote its reason.
    """
    return panel_call("pipelines", "pipeline_board", {})


@mcp.tool()
def pipeline_log(run_id: str) -> dict[str, Any]:
    """The tail of one pipeline run's captured log, by its ledger run id.

    What the run actually printed, which is how a failed step is diagnosed
    rather than guessed at. Run ids come from the pipelines board.

    Args:
        run_id: The ledger run id, as the pipelines board carries it.

    Returns:
        The envelope: result, provenance, budget, and gap when no log was
        captured for that run. If gap is present, quote its reason.
    """
    return panel_call("pipeline_log", "pipeline_run_log", {"run_id": run_id})
