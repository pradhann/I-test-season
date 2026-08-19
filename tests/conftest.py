"""Suite-wide guards.

FPL_EDGE_DISABLE_PRIVATE is set for every test because report/CLI code paths
construct their own PrivateTeamClient: without the guard, a unit test once
refreshed the developer's REAL FPL tokens over the network as a side effect of
rendering a report fixture. Tests that exercise the private path explicitly
inject fakes and are unaffected.
"""

import os

os.environ.setdefault("FPL_EDGE_DISABLE_PRIVATE", "1")
