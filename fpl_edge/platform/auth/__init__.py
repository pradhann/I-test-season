"""Identity, sessions, and the per-user Anthropic key.

Built from ``docs/platform/AUTH.md``. Six modules, each with one job:

``settings``
    Every deployment variable the layer reads, under both the spec's names
    and the build brief's names.
``oauth``
    Google OAuth 2.0, authorization code with PKCE. One redirect, one POST,
    one id_token verification.
``sessions``
    The SQLite schema, the session row, and the three cookies.
``keys``
    The per-user Anthropic key, AES-256-GCM at rest and decrypted per turn.
``policy``
    The access matrix as one table, and the tier lookup that reads it.
``routes``
    The seven routes, the one dependency that applies the matrix, and
    :func:`install_auth`, which wires all of it onto an app.

TWO THINGS THIS PACKAGE DOES NOT DO.

It never writes a credential to ``os.environ``. The server runs turns from
different conversations concurrently in threads and ``os.environ`` is process
global, so two managers' turns would race and one could be billed to the
other's key. The key reaches the model through ``ClaudeAgentOptions.env``,
which is per subprocess.

It is never imported by the batch content analysis. ``analyze.py`` runs from
the scheduler with no user and no session, under the operator's own
credential, and ``tests/unit/test_user_keys.py`` walks its imports to keep
that true.
"""

from __future__ import annotations

from fpl_edge.platform.auth.routes import (
    enforce_policy,
    install_auth,
    iter_app_routes,
    route_pairs,
)

__all__ = ["enforce_policy", "install_auth", "iter_app_routes", "route_pairs"]
