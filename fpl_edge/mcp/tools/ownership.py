"""``ownership``: what the field holds, and what that costs you against them.

Reads the crawl's cohorts rather than fetching a handful of named managers
live. The cohort is larger and better defined than a curated list, and it is
as of the last crawl rather than as of now, which the payload states.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp.adapter import panel_call
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"

#: The cohorts the crawl maintains. Each is a different population and they
#: are not interchangeable.
SEGMENTS = ("elite_list", "winner", "elite_named")


@mcp.tool()
def ownership(
    season: str = SEASON_DEFAULT,
    limit: int = 50,
    cohort: str = "elite",
    diff_max_own: float = 15.0,
    coverage: bool = True,
) -> dict[str, Any]:
    """Template, differentials and effective ownership across the crawled field.

    What the field owns, and what each holding is worth against it. Effective
    ownership is ownership times the multiplier the field applies, so a
    captained player is owned more heavily than his ownership says, and the
    payload carries the two numbers separately rather than one blended one.

    The cohorts are the crawl's: the elite list, past winners, and the named
    elite. They are different populations and the payload names which one each
    number came from. Every figure is as of the last crawl, and the payload
    says when that was.

    Args:
        season: FPL season, for example "2026-27".
        limit: Rows to return.
        cohort: Which crawled cohort to measure the field with.
        diff_max_own: The ownership ceiling below which a player counts as a
            differential, as a percentage.
        coverage: Whether to include the column saying which of these the
            user's own squad covers.

    Returns:
        The envelope: result, provenance, budget, and gap when the crawl holds
        nothing for this season. If gap is present, quote its reason.
    """
    return panel_call("ownership", "ownership_eo", {
        "season": season, "limit": limit, "cohort": cohort,
        "diff_max_own": diff_max_own, "coverage": coverage,
        "segments": list(SEGMENTS),
    })
