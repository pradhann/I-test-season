"""``manager``: one tracked manager, resolved only through a verifier.

Its own module because the identity rules it carries are the whole point of
it. FPL entry ids are assigned per season in registration order, so a curated
name-to-id map rots every August and rots silently: a stale id resolves to a
different real person rather than to a 404. The rules live in the
``manager_lookup`` panel; this tool is the thirty lines that call it and the
description that tells a reader what the refusal means.

This reads the warehouse only. The two live-API tools it replaces could answer
for an entry the crawl has never fetched, and this cannot. That is a real loss
for long-tail entry ids and the payload says so by name rather than returning
silence.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.mcp.adapter import panel_call
from fpl_edge.mcp.server import mcp

SEASON_DEFAULT = "2026-27"


@mcp.tool()
def manager(
    manager: str,
    season: str = SEASON_DEFAULT,
    transfers_limit: int = 20,
    as_of: str | None = None,
) -> dict[str, Any]:
    """One tracked manager: identity, standing, past record and stored transfers.

    A name is matched only against sources that verified it: the curated elite
    list, every member of which was checked against the entry endpoint, and
    dim_manager, which holds the account-holder name the crawl read back from
    the API. A name neither source knows is refused with the reason, and the
    refusal is the answer. Do not work around it by guessing an entry id.

    A number is read as given, because the id is then the caller's own claim
    rather than this engine's, and the payload says the account holder is
    unverified unless the crawl has read a name for it.

    An empty transfer list means the crawl holds nothing for that entry, not
    that the manager made no transfers. The payload states which.

    Args:
        manager: The manager's real name, accents and case ignored, partial
            accepted. A numeric FPL entry id is read as given.
        season: FPL season, for example "2026-27".
        transfers_limit: How many transfers to return, newest first.
        as_of: ISO-8601 instant carrying a timezone. A transfer becomes
            visible at the deadline of the gameweek it applied to. Omit for
            now.

    Returns:
        The envelope: result, provenance, budget, and a refusal inside the
        result when no verified source ties the name to an entry. Quote that
        refusal rather than summarising it.
    """
    return panel_call("manager", "manager_lookup", {
        "manager": manager, "season": season,
        "transfers_limit": transfers_limit, "as_of": as_of,
    })
