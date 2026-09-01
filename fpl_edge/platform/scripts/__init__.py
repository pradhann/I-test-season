"""Panel scripts. Importing this package registers every one of them.

Registration is by explicit import, not filesystem discovery: adding a data
surface to the platform should be a visible one-line diff in this file that a
reviewer can see, which is the same rule Argus applies to its tool registry
(argus_architecture.md §1.2 -- "Adding authority should always produce an
obvious one-line registry change in code review").
"""

from __future__ import annotations

from fpl_edge.platform.scripts import (
    brief, creators, fixtures, ideas, market, ownership, pipelines_panel,
    planner, player_profile, prices, projections, radar, squad,
)

__all__ = ["brief", "creators", "fixtures", "ideas", "market",
           "pipelines_panel", "player_profile", "prices", "projections",
           "radar", "squad"]
