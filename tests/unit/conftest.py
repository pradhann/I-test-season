"""Unit-suite guards for the *metered* ingests.

``tests/conftest.py`` already blocks the authenticated FPL path, after a unit
test once refreshed the developer's real tokens over the network while
rendering a report fixture. This file extends the same rule to the ingests that
cost money rather than merely leaking credentials.

The deadline DAG's ``odds_refresh`` task shells out to
``scripts/ingest_odds.py --odds-api``. That script reads ``ODDS_API_KEY`` from
the developer's ``.env``, so on this machine it would have *worked*: a unit
test asserting something about firing semantics would have spent 12 credits of
a 500/month allowance, silently, on every run of the suite. The task checks
this variable and reports ``no_source`` when it is set.

``setdefault``, not assignment, so an integration test that deliberately wants
the live path can export ``FPL_EDGE_DISABLE_NETWORK_INGEST=0`` and get it.
"""

import os

os.environ.setdefault("FPL_EDGE_DISABLE_NETWORK_INGEST", "1")
