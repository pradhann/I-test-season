"""The access matrix, as one table, and the check that reads it.

``docs/platform/AUTH.md`` section 6 is the specification; this module is its
executable form. Every route on the app appears in :data:`ROUTES` with one
tier, every panel script appears in :data:`SCRIPT_TIERS` with one tier, and
``tests/unit/test_auth_routes_matrix.py`` generates its cases from these two
tables. A route added later with no row fails that test on the commit that
adds it, which is the only way a matrix this long stays true.

THE TIERS

``anonymous``
    No session needed. The public panels, the health check, the shell, and
    the two sign-in routes.
``session``
    Any signed-in person, acting on their own data.
``operator``
    The one account whose verified Google address matches ``OPERATOR_EMAIL``.
``bearer``
    Machine to machine, authenticated inside the handler by a shared secret
    compared with ``hmac.compare_digest``. The Mac ASR worker's two routes,
    which have no browser and therefore no session to present.
``script``
    The tier is the panel script's own, from :data:`SCRIPT_TIERS`, read from
    the ``name`` path parameter.

``KEY_REQUIRED`` marks the routes that additionally need a stored Anthropic
key, and it applies to a signed-in person who is not the operator. The
operator's own model credential is the Claude Code CLI login on the machine
the server runs on, which is the arrangement the engine has today and the one
``briefing_intel`` and the content pipeline keep using.

WHY ONE DEPENDENCY RATHER THAN FOUR

The specification describes four per-route dependencies. One dependency
reading one table gives the same guarantee and does not require editing eight
route modules that other workstreams own in the same merge. The property that
matters, that a route with no tier is a failing test rather than an open
door, is a property of the table and holds either way.
"""

from __future__ import annotations

from typing import Any

ANONYMOUS = "anonymous"
SESSION = "session"
OPERATOR = "operator"
BEARER = "bearer"
SCRIPT = "script"

TIERS = (ANONYMOUS, SESSION, OPERATOR, BEARER, SCRIPT)

#: The methods the CSRF double submit check applies to. Every state changing
#: method, with no allowlist of exceptions: "this POST only reads" is a fact
#: about today's handler, not something a check can verify, and the exception
#: list would rot.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: (METHOD, path template) -> tier. The path template is the string FastAPI
#: registered, so a parameterised route appears once.
ROUTES: dict[tuple[str, str], str] = {
    # -- the shell and the public reads ------------------------------------
    ("GET", "/api/health"): ANONYMOUS,
    ("GET", "/api/deadline"): ANONYMOUS,
    ("GET", "/api/panels"): ANONYMOUS,
    ("POST", "/api/scripts/{name}/run"): SCRIPT,

    # -- the guarded query path and the operator's inbox -------------------
    ("POST", "/api/query"): OPERATOR,
    ("GET", "/api/inbox"): OPERATOR,
    ("POST", "/api/inbox/{delivery_id}/ack"): OPERATOR,
    ("GET", "/api/monitors"): OPERATOR,
    ("POST", "/api/monitors/{name}/run"): OPERATOR,

    # -- the caller's own squad and plan -----------------------------------
    ("POST", "/api/solve"): SESSION,
    ("GET", "/api/solve/status"): SESSION,
    ("GET", "/api/solve/plan"): SESSION,
    ("GET", "/api/solve/transfer-plan"): SESSION,

    # -- shared caches that one caller spends network to fill --------------
    ("POST", "/api/players/{code}/fetch_profile"): SESSION,
    ("GET", "/api/players/{code}/fetch_profile"): SESSION,

    # -- operator infrastructure -------------------------------------------
    ("POST", "/api/pipelines/{task_id}/run"): OPERATOR,
    ("GET", "/api/pipelines/{task_id}/run_state"): OPERATOR,
    ("GET", "/api/content/sources"): OPERATOR,
    ("POST", "/api/content/sources/{source_key}/fetch"): OPERATOR,
    ("GET", "/api/content/sources/{source_key}/fetch_state"): OPERATOR,
    ("POST", "/api/ingest/link"): OPERATOR,
    ("GET", "/api/ingest/link/{job_id}"): OPERATOR,
    ("POST", "/api/ingest/link/{job_id}/accept"): OPERATOR,
    ("POST", "/api/ingest/link/{job_id}/decline"): OPERATOR,
    ("DELETE", "/api/ingest/link/{job_id}"): OPERATOR,
    ("POST", "/api/content/items/{item_id}/discard"): OPERATOR,
    ("POST", "/api/content/items/{item_id}/restore"): OPERATOR,
    ("POST", "/api/content/items/{item_id}/gameweek"): OPERATOR,

    # -- the briefing artefact ---------------------------------------------
    # Read only. The salience pass that writes it is the briefing_intel task,
    # which runs under the operator's own credential and is triggered through
    # POST /api/pipelines/{task_id}/run, an operator row above.
    ("GET", "/api/briefing"): SESSION,

    # -- chat ---------------------------------------------------------------
    ("POST", "/api/conversations"): SESSION,
    ("GET", "/api/conversations"): SESSION,
    ("DELETE", "/api/conversations/{conv_id}"): SESSION,
    ("POST", "/api/conversations/{conv_id}/chat"): SESSION,
    ("GET", "/api/conversations/{conv_id}/stream"): SESSION,
    ("GET", "/api/conversations/{conv_id}/events"): SESSION,
    ("POST", "/api/conversations/{conv_id}/stop"): SESSION,
    ("GET", "/api/chat/assets/{asset_id}.{ext}"): SESSION,

    # -- the account tab ----------------------------------------------------
    # The FPL token store writes one .env, which is process global and cannot
    # hold two managers' tokens, so the three credential routes stay operator
    # only until that store is per-user. The team id routes write the
    # caller's own profile directory and are signed-in today.
    ("GET", "/api/account/status"): OPERATOR,
    ("POST", "/api/account/connect"): OPERATOR,
    ("POST", "/api/account/verify"): OPERATOR,
    ("GET", "/api/account/entry"): SESSION,
    ("POST", "/api/account/entry"): SESSION,
    ("GET", "/api/account/key"): SESSION,
    ("PUT", "/api/account/key"): SESSION,
    ("DELETE", "/api/account/key"): SESSION,

    # -- identity -----------------------------------------------------------
    ("GET", "/api/me"): ANONYMOUS,
    ("GET", "/auth/google/start"): ANONYMOUS,
    ("GET", "/auth/google/callback"): ANONYMOUS,
    ("POST", "/auth/logout"): SESSION,

    # -- the Mac ASR worker -------------------------------------------------
    ("GET", "/api/transcripts/queue"): BEARER,
    ("POST", "/api/transcripts"): BEARER,

    # -- the generated schema, which names every operator route -------------
    ("GET", "/openapi.json"): OPERATOR,
    ("GET", "/docs"): OPERATOR,
    ("GET", "/docs/oauth2-redirect"): OPERATOR,
    ("GET", "/redoc"): OPERATOR,
}

#: Routes that also need a decryptable stored Anthropic key, for a caller who
#: is not the operator.
KEY_REQUIRED: frozenset[tuple[str, str]] = frozenset({
    ("POST", "/api/conversations/{conv_id}/chat"),
})

#: Panel script -> tier, the tier for ``POST /api/scripts/{name}/run``.
SCRIPT_TIERS: dict[str, str] = {
    # League wide and public corpus reads.
    "squad_overview": ANONYMOUS,
    "fixture_board": ANONYMOUS,
    "fixture_detail": ANONYMOUS,
    "projection_table": ANONYMOUS,
    "ownership_eo": ANONYMOUS,
    "player_radar": ANONYMOUS,
    "player_profile": ANONYMOUS,
    "player_chatter": ANONYMOUS,
    "player_dossier": ANONYMOUS,
    "player_form": ANONYMOUS,
    "player_intel": ANONYMOUS,
    "creator_report_card": ANONYMOUS,
    "creator_board": ANONYMOUS,
    "creator_detail": ANONYMOUS,
    "creator_episodes": ANONYMOUS,
    "episode_summary": ANONYMOUS,
    "market_watch": ANONYMOUS,
    "price_radar": ANONYMOUS,
    # The caller's own squad, solve state and watch log.
    "dashboard_brief": SESSION,
    "planner_grid": SESSION,
    # Spends network on a named person's public entry, like fetch_profile.
    "manager_lookup": SESSION,
    # The operator's ledger and the operator's infrastructure, including
    # captured stderr.
    "idea_registry": OPERATOR,
    "idea_review": OPERATOR,
    "pipeline_board": OPERATOR,
    "pipeline_run_log": OPERATOR,
}

#: Paths served by the static bundle mount, which is the shell every visitor
#: needs in order to see the sign-in button.
STATIC_MOUNT = "/"


def tier_for(method: str, path: str,
             path_params: dict[str, Any] | None = None) -> str | None:
    """The tier one request must meet, or None when the route is unclassified.

    A ``script`` row resolves to the script's own tier here, so the caller
    never has to know that one route carries twenty-five.
    """
    tier = ROUTES.get((method.upper(), path))
    if tier != SCRIPT:
        return tier
    name = str((path_params or {}).get("name") or "")
    # An unknown script name is answered by the handler's own 404. Requiring a
    # session first would turn "no such panel" into "sign in", so an unknown
    # name takes the most permissive tier and the 404 that follows.
    return SCRIPT_TIERS.get(name, ANONYMOUS)


def needs_key(method: str, path: str) -> bool:
    """Whether the route spends model tokens on the caller's behalf."""
    return (method.upper(), path) in KEY_REQUIRED


def unclassified(routes: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (method, path) pairs from an app that no row in :data:`ROUTES`
    covers. Empty is the passing state."""
    return [pair for pair in routes if (pair[0].upper(), pair[1]) not in ROUTES]
