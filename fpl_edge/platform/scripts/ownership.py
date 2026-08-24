"""The template / effective-ownership panel.

STUB registered ahead of the build; honest-empty until implemented.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import empty

PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
    },
}
# Must NOT also match the registry's empty shape (oneOf), hence required.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["rows"],
    "properties": {"rows": {"type": "array"}},
}


def run(wh, *, limit: int = 50) -> dict[str, Any]:
    return empty("the ownership data script is not implemented yet")


register_script(
    name="ownership_eo",
    fn=run,
    params_schema=PARAMS_SCHEMA,
    result_schema=RESULT_SCHEMA,
    description="What the field owns: marginal ownership beside every "
                "external effective-ownership metric, template and "
                "differential views.",
)
