"""The transfer-planner panel: one payload for the multi-gameweek grid.

STUB registered ahead of the build so the shell and registry are stable while
the view is developed. Returns an honest empty result; the real
implementation replaces `run` without touching registration.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import empty

PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "horizon": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
    },
}
# Must NOT also match the registry's empty shape (oneOf), hence required.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["gws"],
    "properties": {"gws": {"type": "array"}},
}


def run(wh, *, horizon: int = 5) -> dict[str, Any]:
    return empty("the planner data script is not implemented yet")


register_script(
    name="planner_grid",
    fn=run,
    params_schema=PARAMS_SCHEMA,
    result_schema=RESULT_SCHEMA,
    description="Squad, per-GW consensus projections, prices and transfer "
                "rules in one payload for the planner grid.",
)
