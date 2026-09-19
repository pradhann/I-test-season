"""The three helpers the dispatcher and all three command modules need.

A leaf, and it exists only because those four modules would otherwise import
each other. ``build_resolver`` is the warehouse-reading counterpart of
``resolve.resolver_for``; ``_now`` and ``UTC`` are the one clock the whole
pipeline stamps rows with.

Split out with the command modules (ARCHITECTURE_REVIEW.md Section 4 row 16).
``pipeline.py`` re-exports ``build_resolver``, so
``fpl_edge/interfaces/creators.py`` and the test seam that patches it are
unchanged.
"""

from __future__ import annotations

import datetime as dt

from fpl_edge.ingest.content.resolve import SeasonResolvers
from fpl_edge.store import Warehouse

UTC = dt.UTC


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def build_resolver(warehouse: Warehouse) -> SeasonResolvers:
    """One index per season, built from the latest dim_player row per player.

    Per-season because a claim is resolved against the squad that existed when
    it was published; see :class:`SeasonResolvers` for what that recovers.
    """
    players = warehouse.sql(
        "SELECT * EXCLUDE (rn) FROM (SELECT *, ROW_NUMBER() OVER "
        "(PARTITION BY season, code ORDER BY as_of DESC) rn FROM dim_player) WHERE rn = 1"
    )
    return SeasonResolvers(players)
