"""The access matrix, driven from the table that enforces it.

Every case here is generated from ``fpl_edge/platform/auth/policy.py``, which
is the same table the dependency reads. One source for the matrix, one source
for the test: a route added later with no row fails
:func:`test_every_route_carries_a_tier` on the commit that adds it, and a
route whose tier changes changes both the enforcement and this suite at once.

The three callers are the three tiers of the matrix. Anonymous holds no
cookie. The signed-in caller is an ordinary manager. The operator is the one
account whose verified address matches ``OPERATOR_EMAIL``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from fpl_edge.platform.app.factory import create_app
from fpl_edge.platform.auth import policy
from fpl_edge.platform.auth.routes import route_pairs
from tests.unit import auth_fixtures


@pytest.fixture
def app(monkeypatch, tmp_path):
    auth_fixtures.configure(monkeypatch, tmp_path)
    return create_app(db=tmp_path / "warehouse.duckdb")


@pytest.fixture
def anon(app):
    return TestClient(app)


def _request(client, method: str, path: str, headers=None):
    """Drive one matrix row. Path parameters get placeholder values that no
    handler can find, because the tier is decided before the handler runs."""
    url = (path.replace("{name}", "squad_overview")
               .replace("{conv_id}", "0" * 32)
               .replace("{delivery_id}", "1")
               .replace("{task_id}", "price_radar")
               .replace("{source_key}", "nosuchsource")
               .replace("{job_id}", "nosuchjob")
               .replace("{item_id}", "1")
               .replace("{code}", "1")
               .replace("{asset_id}.{ext}", "0" * 32 + ".png"))
    return client.request(method, url, json={} if method != "GET" else None,
                          headers=headers or {})


REFUSALS = (401, 403)

#: A 403 a handler raised for its own reasons is not a tier refusal. The
#: solver answers one to a manager with no FPL login of their own, which is
#: the named gap from fpl_edge/platform/users.py and the correct answer to a
#: signed-in caller who has connected nothing.
def refused_by_tier(response) -> bool:
    """Whether the access matrix, rather than a handler, refused this."""
    if response.status_code == 401:
        return True
    if response.status_code != 403:
        return False
    try:
        body = response.json()
    except ValueError:
        return True
    if body.get("error") == "no_api_key":
        return True
    detail = str(body.get("detail") or "")
    return ("is an operator route" in detail
            or detail == "csrf token missing or wrong")


#: Driven by hand rather than in the table sweep. The SSE route builds its
#: generator lazily, so an unknown conversation id raises inside the stream
#: rather than through the handler's own 404, and that is a handler bug
#: unrelated to the tier. Its row is asserted below instead.
DRIVEN_BY_HAND = {("GET", "/api/conversations/{conv_id}/stream")}


def matrix_rows():
    """(method, path, tier) for every classified route except the bearer two.

    The Mac ASR worker's routes authenticate with a shared secret inside the
    handler and have no session to present, so they are asserted separately.
    """
    for (method, path), tier in sorted(policy.ROUTES.items()):
        if tier == policy.BEARER or (method, path) in DRIVEN_BY_HAND:
            continue
        yield pytest.param(method, path, tier, id=f"{method} {path}")


# -- the matrix is complete --------------------------------------------------

def test_every_route_carries_a_tier(app) -> None:
    """Walk the app and assert that the table classifies every route.

    This is the only mechanism that keeps a matrix this long true. A route
    added with no row lands here rather than in production, and the failure
    names it.
    """
    missing = policy.unclassified(route_pairs(app))
    assert not missing, (
        f"these routes have no tier in fpl_edge/platform/auth/policy.py: "
        f"{missing}. Add a row rather than leaving them to the fail-closed "
        f"default."
    )


def test_every_registered_panel_script_carries_a_tier() -> None:
    """The one route that carries twenty-five, from the second table."""
    import fpl_edge.platform.scripts  # noqa: F401 - importing registers them
    from fpl_edge.platform.registry import registered

    missing = sorted(set(registered()) - set(policy.SCRIPT_TIERS))
    assert not missing, (
        f"these panel scripts have no tier: {missing}. POST /api/scripts/"
        f"{{name}}/run takes the script's own tier, so a new script needs a "
        f"row beside the others."
    )
    stray = sorted(set(policy.SCRIPT_TIERS) - set(registered()))
    assert not stray, f"these tiers name scripts that no longer exist: {stray}"


def test_the_table_only_uses_the_five_tiers() -> None:
    assert set(policy.ROUTES.values()) <= set(policy.TIERS)
    assert set(policy.SCRIPT_TIERS.values()) <= {
        policy.ANONYMOUS, policy.SESSION, policy.OPERATOR}


# -- anonymous ---------------------------------------------------------------

@pytest.mark.parametrize("method,path,tier", list(matrix_rows()))
def test_anonymous(anon, method, path, tier) -> None:
    r = _request(anon, method, path)
    if tier in (policy.ANONYMOUS, policy.SCRIPT):
        # squad_overview is the anonymous script, so the one script row is
        # reachable here too.
        assert not refused_by_tier(r), r.text
        return
    assert r.status_code == 401, r.text


def test_an_anonymous_chat_turn_is_401_and_creates_nothing(anon, tmp_path) -> None:
    r = anon.post("/api/conversations/abc/chat", json={"text": "hi"})
    assert r.status_code == 401
    # A 401 that still created a conversation directory would be a leak of
    # another kind.
    chat_dirs = list((tmp_path / "data").rglob("meta.json"))
    assert chat_dirs == []


def test_an_operator_script_is_refused_for_an_anonymous_caller(anon) -> None:
    r = anon.post("/api/scripts/pipeline_board/run", json={})
    assert r.status_code == 401
    r = anon.post("/api/scripts/planner_grid/run", json={})
    assert r.status_code == 401


# -- signed in, not the operator ---------------------------------------------

@pytest.mark.parametrize("method,path,tier", list(matrix_rows()))
def test_signed_in_non_operator(app, method, path, tier) -> None:
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    r = _request(client, method, path, headers=who.headers())
    if tier == policy.OPERATOR:
        assert r.status_code == 403, r.text
        return
    if policy.needs_key(method, path):
        # No key stored, so the key tier refuses with the named remediation.
        assert r.status_code == 403
        assert r.json()["error"] == "no_api_key"
        return
    assert not refused_by_tier(r), r.text


@pytest.mark.parametrize("method,path", sorted(policy.KEY_REQUIRED))
def test_signed_in_with_a_credential_passes_the_key_tier(app, method, path) -> None:
    """The other direction of the same row.

    Every route that spends the caller's credential is reachable once they
    have stored one. What the handler then does with a placeholder path
    parameter is the handler's business; the tier is done refusing.
    """
    from fpl_edge.platform.auth import keys

    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    keys.store(who.user_id, auth_fixtures.SENTINEL)
    r = _request(client, method, path, headers=who.headers())
    assert not refused_by_tier(r), r.text


def test_the_operator_refusal_names_the_account_and_the_route(app) -> None:
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    r = client.post("/api/query", json={"sql": "select 1"},
                    headers=who.headers())
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert "/api/query" in detail
    assert auth_fixtures.OTHER_EMAIL in detail
    assert "operator" in detail


# -- the operator ------------------------------------------------------------

@pytest.mark.parametrize("method,path,tier", list(matrix_rows()))
def test_the_operator(app, method, path, tier) -> None:
    del tier
    client = TestClient(app)
    who = auth_fixtures.sign_in(client, email=auth_fixtures.OPERATOR_EMAIL,
                                is_operator=True)
    r = _request(client, method, path, headers=who.headers())
    assert not refused_by_tier(r), r.text


def test_the_operator_needs_no_stored_key_for_a_chat_turn(app) -> None:
    """The operator's model credential is the Claude CLI login on the machine
    the server runs on, which is the arrangement the engine has today. A
    signed-in manager who is not the operator is refused instead, so there is
    no path from a stranger's message to the operator's subscription."""
    client = TestClient(app)
    who = auth_fixtures.sign_in(client, email=auth_fixtures.OPERATOR_EMAIL,
                                is_operator=True)
    r = client.post(f"/api/conversations/{'0' * 32}/chat", json={"text": "hi"},
                    headers=who.headers())
    assert r.status_code == 404, r.text


# -- the generated schema ----------------------------------------------------

def test_the_schema_routes_are_operator_only(app) -> None:
    """The schema names every operator route, so it is gated with them."""
    anon = TestClient(app)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert anon.get(path).status_code == 401, path
    client = TestClient(app)
    auth_fixtures.sign_in(client, email=auth_fixtures.OPERATOR_EMAIL,
                          is_operator=True)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 200, path


# -- csrf --------------------------------------------------------------------

def test_a_state_changing_request_without_the_token_is_refused(app) -> None:
    client = TestClient(app)
    auth_fixtures.sign_in(client)
    r = client.post("/api/conversations", json={})
    assert r.status_code == 403
    assert r.json()["detail"] == "csrf token missing or wrong"


def test_the_check_covers_every_unsafe_method_with_no_exceptions(app) -> None:
    """A read-only POST is still a POST. "This one only reads" is a fact
    about today's handler, not something a check can verify."""
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    for method, path in (("POST", "/api/scripts/squad_overview/run"),
                         ("DELETE", "/api/account/key"),
                         ("PUT", "/api/account/key")):
        bare = client.request(method, path, json={})
        assert bare.status_code == 403, f"{method} {path}"
        with_token = client.request(method, path, json={"key": "x"},
                                    headers=who.headers())
        assert with_token.status_code != 403 or method == "PUT", f"{method} {path}"


def test_an_anonymous_post_needs_no_token(anon) -> None:
    """There is no session to ride on, so there is nothing to forge. This is
    what keeps the public panels callable by POST without one."""
    r = anon.post("/api/scripts/squad_overview/run", json={})
    assert r.status_code != 403


def test_the_sse_stream_is_a_get_and_needs_a_session(anon) -> None:
    """EventSource cannot set headers, which is why the stream is a GET and
    carries no CSRF token. It is still signed-in only, on the cookie."""
    assert policy.ROUTES[("GET", "/api/conversations/{conv_id}/stream")] == (
        policy.SESSION)
    assert "GET" not in policy.UNSAFE_METHODS
    r = anon.get(f"/api/conversations/{'0' * 32}/stream")
    assert r.status_code == 401


# -- the anonymous-is-owner flag ---------------------------------------------

def test_the_flag_makes_an_unauthenticated_caller_the_operator(
        monkeypatch, tmp_path) -> None:
    """How the server runs on the owner's own machine, and how every test in
    this repo that builds an app without configuring sign-in runs."""
    auth_fixtures.configure(monkeypatch, tmp_path, anon_is_owner=True)
    client = TestClient(create_app(db=tmp_path / "warehouse.duckdb"))
    for method, path in sorted(policy.ROUTES):
        if (policy.ROUTES[(method, path)] == policy.BEARER
                or (method, path) in DRIVEN_BY_HAND):
            continue
        r = _request(client, method, path)
        assert not refused_by_tier(r), f"{method} {path}: {r.text}"


# -- the two payloads the tiers trim ------------------------------------------

def test_anonymous_health_is_the_reduced_body(anon, monkeypatch,
                                              tmp_path) -> None:
    """AUTH.md row 1. The status code is the healthcheck's signal and does
    not move; the warehouse path, boot report, migrations and scheduler
    state describe this deployment and are operator fields."""
    body = anon.get("/api/health").json()
    assert set(body) == {"ok", "now"}, body


def test_the_operator_health_keeps_every_field(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path, anon_is_owner=True)
    client = TestClient(create_app(db=tmp_path / "warehouse.duckdb"))
    body = client.get("/api/health").json()
    for field in ("repo_sha", "warehouse", "warehouse_present", "boot",
                  "scheduler"):
        assert field in body, field


def test_panels_are_filtered_by_the_callers_tier(anon, app) -> None:
    """AUTH.md 6.2 rule 2: an anonymous visitor's UI must not render a tab
    that 401s on click, so the catalogue lists only what it may run."""
    payload = anon.get("/api/panels").json()
    listed = {row["name"] for row in payload["scripts"]}
    assert listed, payload
    for name in listed:
        assert policy.SCRIPT_TIERS.get(name) == policy.ANONYMOUS, name
    for name, tier in policy.SCRIPT_TIERS.items():
        if tier != policy.ANONYMOUS:
            assert name not in listed, name
    for panel in payload["panels"]:
        assert policy.SCRIPT_TIERS.get(panel["script"]) == policy.ANONYMOUS


def test_a_signed_in_manager_sees_the_session_panels(app, monkeypatch,
                                                     tmp_path) -> None:
    client = TestClient(app)
    auth_fixtures.sign_in(client)
    listed = {row["name"] for row in client.get("/api/panels").json()["scripts"]}
    assert "dashboard_brief" in listed
    assert "planner_grid" in listed
    assert "pipeline_board" not in listed


def test_the_operator_sees_every_panel(app) -> None:
    client = TestClient(app)
    auth_fixtures.sign_in(client, email=auth_fixtures.OPERATOR_EMAIL,
                          is_operator=True)
    listed = {row["name"] for row in client.get("/api/panels").json()["scripts"]}
    for name in policy.SCRIPT_TIERS:
        assert name in listed, name


def test_the_bearer_routes_keep_their_own_secret(anon) -> None:
    """Machine to machine, with no browser and therefore no session. The
    handler compares a shared secret with hmac.compare_digest, and the tier
    says so rather than leaving the route unclassified."""
    assert policy.ROUTES[("POST", "/api/transcripts")] == policy.BEARER
    assert policy.ROUTES[("GET", "/api/transcripts/queue")] == policy.BEARER
    r = anon.get("/api/transcripts/queue")
    assert r.status_code in (401, 403, 503)
