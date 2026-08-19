"""The manager's own team: reconstructing it, capturing it, acting on it.

Read :mod:`fpl_edge.myteam.state` first. The single fact that shapes this whole
package is that FPL publishes a manager's picks only *after* a gameweek has
kicked off, and the endpoint that would show them beforehand
(``/api/my-team/{id}/``) requires the account password. This engine does not have
that password, will not ask for one, and will not store a session cookie, so the
pre-deadline squad is genuinely unobtainable rather than merely inconvenient.

Everything follows from that:

* :mod:`~fpl_edge.myteam.sources` reads only public endpoints and *raises* on any
  URL that would need a login, so the temptation to fix a 403 with credentials
  fails a test rather than passing review.
* :mod:`~fpl_edge.myteam.manual` fills the pre-GW1 hole the only honest way --
  asking once, parsing forgivingly, refusing to guess between two players who
  match equally well, and confirming the squad back before saving anything.
* :mod:`~fpl_edge.myteam.state` reconstructs everything else from public data and
  labels each number *observed*, *derived* or *unavailable*. Derived numbers are
  cross-checked against observed ones wherever the game publishes something that
  would catch an error -- the free-transfer count against the hits actually paid,
  the squad value against FPL's own.
* :mod:`~fpl_edge.myteam.recommend` consumes the optimiser and the points model
  through their existing interfaces and refuses to substitute expected points
  for rank utility.

Importing this package registers the ``transfers`` section of the weekly report.
"""

from fpl_edge.myteam.forecast import (
    PointsForecastUnavailableError,
    SampledPointsForecast,
    TablePointsForecast,
)
from fpl_edge.myteam.manual import (
    ManualEntryError,
    ManualSquadRecord,
    Reconciliation,
    SquadDraft,
    build_draft,
    reconcile,
    split_fragments,
)
from fpl_edge.myteam.recommend import (
    Move,
    NoSquadError,
    TransferRecommendation,
    recommend,
)
from fpl_edge.myteam.report import configure, register
from fpl_edge.myteam.sources import (
    AuthenticatedEndpointError,
    PublicEntryClient,
    forbid_authenticated_url,
)
from fpl_edge.myteam.state import (
    ChipStatus,
    LedgerCheck,
    MyTeamState,
    PlayerIndex,
    Provenance,
    ReconstructionError,
    reconstruct,
)
from fpl_edge.myteam.store import MyTeamStore

register()

__all__ = [
    "AuthenticatedEndpointError",
    "ChipStatus",
    "LedgerCheck",
    "ManualEntryError",
    "ManualSquadRecord",
    "Move",
    "MyTeamState",
    "MyTeamStore",
    "NoSquadError",
    "PlayerIndex",
    "PointsForecastUnavailableError",
    "Provenance",
    "PublicEntryClient",
    "Reconciliation",
    "ReconstructionError",
    "SampledPointsForecast",
    "SquadDraft",
    "TablePointsForecast",
    "TransferRecommendation",
    "build_draft",
    "configure",
    "forbid_authenticated_url",
    "recommend",
    "reconcile",
    "reconstruct",
    "register",
    "split_fragments",
]
