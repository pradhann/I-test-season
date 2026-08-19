"""Mining what demonstrably skilled managers actually do.

The premise is that copying measurably good managers is a legitimate edge and an
underused one, and that the hard part is not the copying but the *demonstrably*:
separating a manager who is good from a manager who had a good season. Six
million entries guarantee that some of them finish top-1k on noise alone.

The package is split along the line of what the API will and will not tell you:

:mod:`~fpl_edge.ingest.rivals.client`
    The polite, budgeted, cached HTTP layer everything else must go through.
:mod:`~fpl_edge.ingest.rivals.roster`
    Who to evaluate, and why the obvious sampling frame is unavailable pre-season.
:mod:`~fpl_edge.ingest.rivals.history`
    Season and gameweek records -- the only multi-season skill evidence that exists.
:mod:`~fpl_edge.ingest.rivals.picks`
    Squads, transfers and chips, which become readable only after a deadline passes.
:mod:`~fpl_edge.ingest.rivals.schema`
    Warehouse tables, added additively without editing the core schema.
"""

from fpl_edge.ingest.rivals.client import (
    BudgetExhausted,
    RequestBudget,
    RivalsFetcher,
)

__all__ = ["BudgetExhausted", "RequestBudget", "RivalsFetcher"]
