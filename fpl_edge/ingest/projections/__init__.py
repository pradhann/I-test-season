"""Third-party FPL projection and ownership feeds.

An ensemble of independent estimates beats any single one, and the places where
they disagree are where an edge can exist at all. This package finds the feeds
that are legitimately reachable, lands them in the warehouse with ``as_of`` set
to the fetch instant, and records -- in :mod:`.providers` -- exactly what every
candidate returned, including the ones that returned a refusal.
"""

from fpl_edge.ingest.projections.providers import BY_KEY, PROVIDERS, Provider, probe_all
from fpl_edge.ingest.projections.robots import RobotsDisallowed, RobotsPolicy, fetch_policy
from fpl_edge.ingest.projections.store import ConflictingProjectionError, ProjectionStore

__all__ = [
    "BY_KEY", "PROVIDERS", "Provider", "probe_all",
    "RobotsDisallowed", "RobotsPolicy", "fetch_policy",
    "ConflictingProjectionError", "ProjectionStore",
]
