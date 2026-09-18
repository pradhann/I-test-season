"""``/api/account/*`` over a scratch FastAPI app, with every network call faked.

What is pinned here:

* the connect route runs the CLI's store-prove-read sequence and reports the
  same failure classes, including the exact refusal the owner hit
  (``invalid_grant; Refresh token does not exist``);
* a malformed paste writes nothing and a refused refresh clears nothing;
* a success stores the ROTATED pair, records the entry name, and flips the
  squad source to ``private`` with no restart (a fresh TokenManager reads
  the file on every request);
* a stranger cannot reach these routes: no session is 401, a signed-in
  non-operator is 403 on the credential routes, and the operator is 200;
* no token value appears in any response body, ever.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fpl_edge.config import load_env
from fpl_edge.myteam.private import PrivateTeamClient
from fpl_edge.platform.routes_account import build_router
from tests.unit import auth_fixtures

ISSUER = "https://auth.example.test/env/as"
CLIENT = "client-123"
ENTRY = 4490171
LOOPBACK = ("127.0.0.1", 50123)


def make_jwt(exp_in_s: int, tag: str, **claims) -> str:
    now = int(dt.datetime.now(dt.UTC).timestamp())
    payload = {"iat": now, "exp": now + exp_in_s, "tag": tag, **claims}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.SIG{tag}"


PASTED_ACCESS = make_jwt(-60, "PASTEDACCESS", iss=ISSUER, client_id=CLIENT)
PASTED_REFRESH = make_jwt(3600 * 24 * 150, "PASTEDREFRESH", iss=ISSUER)
ROTATED_ACCESS = make_jwt(3600 * 8, "ROTATEDACCESS", iss=ISSUER, client_id=CLIENT)
ROTATED_REFRESH = make_jwt(3600 * 24 * 180, "ROTATEDREFRESH", iss=ISSUER)
ALL_TOKENS = (PASTED_ACCESS, PASTED_REFRESH, ROTATED_ACCESS, ROTATED_REFRESH)

FULL_COOKIE = (
    f"req_language=en; pl_profile=SESSIONSECRET; access_token={PASTED_ACCESS}; "
    f"refresh_token={PASTED_REFRESH}; other=1"
)


def patch_get(monkeypatch, handler) -> None:
    """Fake only FPL GETs. TestClient is itself an httpx.Client, so a blanket
    patch of ``httpx.Client.get`` would swallow the test's own requests."""
    original = httpx.Client.get

    def get(self, url, *args, **kwargs):
        if isinstance(self, TestClient):
            return original(self, url, *args, **kwargs)
        return handler(url, kwargs.get("headers") or {})

    monkeypatch.setattr(httpx.Client, "get", get)


def no_network(*a, **k):
    raise AssertionError("no network call may run in this test")


def my_team_body() -> dict:
    picks = [
        {"element": 100 + s, "position": s, "selling_price": 50 + s,
         "purchase_price": 49 + s, "multiplier": 1 if s <= 11 else 0,
         "is_captain": s == 3, "is_vice_captain": s == 4}
        for s in range(1, 16)
    ]
    return {
        "picks": picks,
        "chips": [{"name": "wildcard", "status_for_entry": "available"}],
        "transfers": {"bank": 15, "value": 1002, "limit": 2, "made": 1, "cost": 4},
    }


@pytest.fixture
def paths(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    # The suite disables private reads globally; this router's status must
    # describe the real decision, so lift the guard for these tests only.
    monkeypatch.delenv("FPL_EDGE_DISABLE_PRIVATE", raising=False)
    return {"env": tmp_path / ".env", "record": tmp_path / "record.json"}


def make_client(paths, *, client=LOOPBACK, guard=False) -> TestClient:
    """A scratch app with this router on it.

    ``guard=True`` also installs the access matrix, which is what the real
    app does and what the two tier tests below need. The other tests leave it
    off so they exercise the handlers rather than the check in front of them.
    """
    app = _app(guard)
    app.include_router(build_router(
        env_path=paths["env"], record_path=paths["record"], entry_id=ENTRY,
        entry_lookup=lambda eid: ("Fable XI", "N. Pradhan"),
        # cookie="" pins the legacy FPL_SESSION_COOKIE fallback off, so the
        # developer's real .env cannot leak into these tests.
        client_factory=lambda m: PrivateTeamClient(cookie="", tokens=m),
    ))
    return TestClient(app, client=client)


def _app(guard: bool) -> FastAPI:
    """A scratch app, with the access matrix on it when the test wants one.

    install_auth runs before any router is included, which is the order
    create_app uses and the only order that works: FastAPI copies the parent
    router's dependencies into each include, so a check appended afterwards
    would never reach the routes it is meant to guard.
    """
    app = FastAPI()
    if guard:
        from fpl_edge.platform.auth.routes import install_auth

        install_auth(app)
    return app


#: The team-id routes get their own client: they take a user context, so the
#: data root has to be a temp directory or a save would write into the repo.
def make_entry_client(paths, monkeypatch, tmp_path, *, check=None,
                      guard=False) -> TestClient:
    from fpl_edge.myteam import account as account_mod
    from fpl_edge.platform import users as users_mod

    monkeypatch.setenv(users_mod.DATA_ROOT_ENV, str(tmp_path / "data"))
    monkeypatch.delenv("FPL_ENTRY_ID", raising=False)
    found = check if check is not None else (
        lambda eid: account_mod.EntryCheck(found=True, status=200,
                                           team_name="Fable XI",
                                           manager_name="N. Pradhan"))
    app = _app(guard)
    app.include_router(build_router(
        env_path=paths["env"], record_path=paths["record"],
        entry_lookup=lambda eid: ("Fable XI", "N. Pradhan"),
        client_factory=lambda m: PrivateTeamClient(cookie="", tokens=m),
        entry_check=found,
    ))
    return TestClient(app, client=LOOPBACK)


def test_the_team_id_round_trips_through_the_account_tab(
        paths, monkeypatch, tmp_path) -> None:
    """Save an id, read it back, and see the panels' id change with it."""
    client = make_entry_client(paths, monkeypatch, tmp_path)

    before = client.get("/api/account/entry").json()
    assert before["entry_id"] == ENTRY
    assert before["saved"] is False
    assert "address bar" in before["where_is_my_id"]

    saved = client.post("/api/account/entry", json={"entry_id": 1234567})
    assert saved.status_code == 200
    body = saved.json()
    assert body["entry_id"] == 1234567
    assert body["team_name"] == "Fable XI"
    assert body["saved"] is True

    after = client.get("/api/account/entry").json()
    assert after["entry_id"] == 1234567
    assert after["saved"] is True

    from fpl_edge.platform.users import owner_context

    assert owner_context().entry_id == 1234567


def test_an_unknown_team_id_is_refused_and_nothing_is_saved(
        paths, monkeypatch, tmp_path) -> None:
    from fpl_edge.myteam import account as account_mod

    client = make_entry_client(
        paths, monkeypatch, tmp_path,
        check=lambda eid: account_mod.EntryCheck(found=False, status=404))
    r = client.post("/api/account/entry", json={"entry_id": 999999999})
    assert r.status_code == 404
    assert "no team with id 999999999" in r.json()["detail"]
    assert "address bar" in r.json()["detail"]
    assert client.get("/api/account/entry").json()["saved"] is False


def test_an_unreachable_check_does_not_call_the_id_wrong(
        paths, monkeypatch, tmp_path) -> None:
    """A timeout is not evidence. Nothing is saved and nothing is judged."""
    from fpl_edge.myteam import account as account_mod

    client = make_entry_client(
        paths, monkeypatch, tmp_path,
        check=lambda eid: account_mod.EntryCheck(
            found=False, status=None, error="ReadTimeout: "))
    r = client.post("/api/account/entry", json={"entry_id": 1234567})
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "could not be checked" in detail
    assert "does not mean the id is wrong" in detail
    assert client.get("/api/account/entry").json()["saved"] is False


def test_an_id_that_is_not_a_number_is_refused_before_any_request(
        paths, monkeypatch, tmp_path) -> None:
    def never(eid):
        raise AssertionError("the endpoint must not be called")

    client = make_entry_client(paths, monkeypatch, tmp_path, check=never)
    for bad in ("", "abc", -1, 0, None):
        r = client.post("/api/account/entry", json={"entry_id": bad})
        assert r.status_code == 400, bad


def test_the_team_id_routes_need_a_session_not_a_loopback_address(
        paths, monkeypatch, tmp_path) -> None:
    """What the loopback guard used to pin, pinned by the session check.

    The guard compared ``request.client.host``, which behind a platform proxy
    is the proxy's address rather than the visitor's. The address is now
    irrelevant: the same non-loopback client is refused without a session and
    answered with one.
    """
    auth_fixtures.configure(monkeypatch, tmp_path)
    client = make_entry_client(paths, monkeypatch, tmp_path, guard=True)
    stranger = TestClient(client.app, client=("10.0.0.9", 51000))
    assert stranger.get("/api/account/entry").status_code == 401
    assert stranger.post("/api/account/entry",
                         json={"entry_id": 1234567}).status_code == 401

    # A signed-in manager reaches their own team id from the same address.
    who = auth_fixtures.sign_in(stranger)
    assert stranger.get("/api/account/entry").status_code == 200
    saved = stranger.post("/api/account/entry", json={"entry_id": 1234567},
                          headers=who.headers())
    assert saved.status_code == 200, saved.text


def assert_no_tokens(body_text: str) -> None:
    for tok in ALL_TOKENS:
        assert tok not in body_text
        assert tok.split(".")[1] not in body_text  # not even the payload segment
    assert "SESSIONSECRET" not in body_text


# -- status -----------------------------------------------------------------


def test_status_with_no_token_says_public_and_why(paths) -> None:
    r = make_client(paths).get("/api/account/status")
    assert r.status_code == 200
    body = r.json()
    assert body["refresh_stored"] is False
    assert body["access"] is None and body["refresh"] is None
    assert body["squad_source"] == "public"
    assert "public picks" in body["squad_source_reason"]
    assert "no API login" in body["why_hard"]
    assert body["cli"] == "pbpaste | uv run fpl myteam auth --paste-cookie"
    assert body["last_ok"] is None
    assert_no_tokens(r.text)


# -- connect: failures ------------------------------------------------------


def test_malformed_paste_writes_nothing(paths, monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", no_network)
    patch_get(monkeypatch, no_network)
    r = make_client(paths).post("/api/account/connect",
                                json={"cookie": "pl_profile=abc; req_language=en"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["stage"] == "paste"
    assert body["error_class"] == "malformed_paste"
    assert "no refresh_token" in body["message"]
    assert "pbpaste" in body["message"], "the CLI's remediation travels with it"
    assert not paths["env"].exists(), "nothing may be stored from a bad paste"
    assert body["status"]["refresh_stored"] is False
    assert_no_tokens(r.text)


def test_undecodable_refresh_token_is_refused_before_storing(paths, monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("no network"))
    r = make_client(paths).post("/api/account/connect",
                                json={"cookie": "refresh_token=not-a-jwt"})
    body = r.json()
    assert body["ok"] is False and body["error_class"] == "malformed_paste"
    assert not paths["env"].exists()
    assert "not-a-jwt" not in r.text, "the pasted value is never echoed"


def test_issuer_refused_refresh_is_the_owners_exact_error(paths, monkeypatch) -> None:
    """The failure the owner saw: HTTP 400 invalid_grant, token does not exist."""
    calls = []

    def refuse(url, data=None, headers=None, timeout=None):
        calls.append(url)
        return httpx.Response(
            400,
            json={"error": "invalid_grant",
                  "error_description": "Refresh token does not exist"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", refuse)
    patch_get(monkeypatch, no_network)

    r = make_client(paths).post("/api/account/connect", json={"cookie": FULL_COOKIE})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["stage"] == "refresh"
    assert body["error_class"] == "refresh_refused"
    assert "refused the refresh grant (HTTP 400)" in body["message"]
    assert "invalid_grant; Refresh token does not exist" in body["message"]
    assert "Re-copy the cookie from a browser session you have just used" in body["remediation"]
    assert calls == [f"{ISSUER}/token"]

    # The CLI stores first and proves second; a refusal clears nothing.
    env = load_env(paths["env"])
    assert env["FPL_REFRESH_TOKEN"] == PASTED_REFRESH
    assert env["FPL_ACCESS_TOKEN"] == PASTED_ACCESS
    assert env["FPL_OAUTH_ISSUER"] == ISSUER and env["FPL_OAUTH_CLIENT_ID"] == CLIENT

    st = body["status"]
    assert st["refresh_stored"] is True
    assert st["squad_source"] == "public"
    assert st["last_attempt"]["error_class"] == "refresh_refused"
    assert st["last_ok"] is None
    assert_no_tokens(r.text)


def test_session_rejected_after_a_good_refresh_is_its_own_class(paths, monkeypatch) -> None:
    def grant(url, data=None, headers=None, timeout=None):
        return httpx.Response(200, json={"access_token": ROTATED_ACCESS,
                                         "refresh_token": ROTATED_REFRESH},
                              request=httpx.Request("POST", url))

    def forbidden(url, headers):
        return httpx.Response(403, json={"detail": "Authentication credentials were not provided."},
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "post", grant)
    patch_get(monkeypatch, forbidden)
    body = make_client(paths).post("/api/account/connect", json={"cookie": FULL_COOKIE}).json()
    assert body["ok"] is False
    assert body["stage"] == "fetch"
    assert body["error_class"] == "session_rejected"
    assert "HTTP 403" in body["message"]


# -- connect: success -------------------------------------------------------


def _happy_network(monkeypatch, *, grants: list) -> None:
    def grant(url, data=None, headers=None, timeout=None):
        grants.append(dict(data))
        return httpx.Response(200, json={"access_token": ROTATED_ACCESS,
                                         "refresh_token": ROTATED_REFRESH},
                              request=httpx.Request("POST", url))

    def get(url, headers):
        if "/my-team/" in str(url):
            assert headers.get("X-API-Authorization") == f"Bearer {ROTATED_ACCESS}"
            return httpx.Response(200, json=my_team_body(), request=httpx.Request("GET", url))
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(httpx, "post", grant)
    patch_get(monkeypatch, get)


def test_success_stores_rotated_pair_and_flips_squad_source(paths, monkeypatch) -> None:
    grants: list = []
    _happy_network(monkeypatch, grants=grants)
    client = make_client(paths)

    before = client.get("/api/account/status").json()
    assert before["squad_source"] == "public"

    r = client.post("/api/account/connect", json={"cookie": FULL_COOKIE})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["stage"] == "done"
    assert body["entry_id"] == ENTRY
    assert body["entry_name"] == "Fable XI"
    assert body["squad"]["picks"] == 15
    assert body["squad"]["bank_tenths"] == 15
    assert body["squad"]["chips"] == {"wildcard": "available"}
    assert body["message"].startswith("Session OK.")

    # Exactly one redemption, of the pasted refresh token, at the JWT's issuer.
    assert len(grants) == 1
    assert grants[0]["grant_type"] == "refresh_token"
    assert grants[0]["client_id"] == CLIENT

    # What is stored is the rotated pair: the next renewal starts from a live token.
    env = load_env(paths["env"])
    assert env["FPL_ACCESS_TOKEN"] == ROTATED_ACCESS
    assert env["FPL_REFRESH_TOKEN"] == ROTATED_REFRESH
    assert paths["env"].stat().st_mode & 0o777 == 0o600

    # No restart: the very next status request, from a fresh manager, sees it.
    after = client.get("/api/account/status").json()
    assert after["refresh_stored"] is True
    assert after["squad_source"] == "private"
    assert after["last_ok"]["entry_name"] == "Fable XI"
    assert after["refresh"]["expired"] is False and after["refresh"]["days_left"] >= 179
    assert after["access"]["expired"] is False
    assert json.loads(paths["record"].read_text())["last_ok"]["entry_id"] == ENTRY

    assert_no_tokens(r.text)
    assert_no_tokens(json.dumps(after))


def test_a_bare_refresh_token_paste_is_accepted(paths, monkeypatch) -> None:
    """The CLI takes just `refresh_token=...`; so does the route. With no
    access token in the paste the client id must come from somewhere, and the
    refresh JWT carries none here, so the route reports that as a paste
    problem rather than a mystery."""
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("no client_id: no grant"))
    body = make_client(paths).post(
        "/api/account/connect", json={"cookie": f"refresh_token={PASTED_REFRESH}"}
    ).json()
    assert body["ok"] is False
    assert body["stage"] == "refresh"
    assert "FPL_OAUTH_CLIENT_ID" in body["message"]
    assert load_env(paths["env"])["FPL_REFRESH_TOKEN"] == PASTED_REFRESH


def test_verify_without_a_token_is_not_configured(paths, monkeypatch) -> None:
    monkeypatch.setattr(httpx, "post", no_network)
    patch_get(monkeypatch, no_network)
    body = make_client(paths).post("/api/account/verify").json()
    assert body["ok"] is False
    assert body["error_class"] == "not_configured"
    assert body["status"]["squad_source"] == "public"


def test_verify_reads_the_team_with_the_stored_pair(paths, monkeypatch) -> None:
    grants: list = []
    _happy_network(monkeypatch, grants=grants)
    paths["env"].write_text(
        f"FPL_ACCESS_TOKEN={PASTED_ACCESS}\nFPL_REFRESH_TOKEN={PASTED_REFRESH}\n"
        f"FPL_OAUTH_ISSUER={ISSUER}\nFPL_OAUTH_CLIENT_ID={CLIENT}\n"
    )
    client = make_client(paths)
    # Stored by the CLI, never checked from here: an unexpired JWT is not proof.
    before = client.get("/api/account/status").json()
    assert before["squad_source"] == "unverified"
    assert "Verify again" in before["squad_source_reason"]
    r = client.post("/api/account/verify")
    body = r.json()
    assert body["ok"] is True
    assert body["entry_name"] == "Fable XI"
    assert len(grants) == 1, "the expired access token forces one refresh"
    assert body["status"]["squad_source"] == "private"
    assert_no_tokens(r.text)


# -- guards -----------------------------------------------------------------


def test_bad_body_is_a_400_that_echoes_nothing(paths) -> None:
    client = make_client(paths)
    r = client.post("/api/account/connect", json={"cookie": 12345})
    assert r.status_code == 400
    assert "12345" not in r.text
    r = client.post("/api/account/connect", json={"nope": "x"})
    assert r.status_code == 400
    assert not paths["env"].exists()


def test_the_credential_routes_are_operator_only(paths, monkeypatch, tmp_path) -> None:
    """The FPL token store writes one .env, which cannot hold two managers'
    tokens, so these three answer the operator and nobody else. No session is
    401; a signed-in manager who is not the operator is 403 and the paste is
    never read."""
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("no network"))
    auth_fixtures.configure(monkeypatch, tmp_path)
    client = make_client(paths, client=("192.168.1.20", 4242), guard=True)

    assert client.get("/api/account/status").status_code == 401
    assert client.post("/api/account/connect",
                       json={"cookie": FULL_COOKIE}).status_code == 401

    who = auth_fixtures.sign_in(client)
    assert client.get("/api/account/status").status_code == 403
    r = client.post("/api/account/connect", json={"cookie": FULL_COOKIE},
                    headers=who.headers())
    assert r.status_code == 403
    assert client.post("/api/account/verify",
                       headers=who.headers()).status_code == 403
    assert not paths["env"].exists(), "a refused connect must not store anything"
    assert_no_tokens(r.text)


def test_the_operator_reaches_the_credential_routes_from_anywhere(
        paths, monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    client = make_client(paths, client=("203.0.113.7", 4242), guard=True)
    auth_fixtures.sign_in(client, email=auth_fixtures.OPERATOR_EMAIL,
                          is_operator=True)
    assert client.get("/api/account/status").status_code == 200


def test_token_values_never_leave_through_any_route(paths, monkeypatch) -> None:
    """Belt and braces over the whole surface, with a fully stored pair."""
    grants: list = []
    _happy_network(monkeypatch, grants=grants)
    client = make_client(paths)
    texts = [
        client.post("/api/account/connect", json={"cookie": FULL_COOKIE}).text,
        client.get("/api/account/status").text,
        client.post("/api/account/verify").text,
        client.post("/api/account/connect", json={"cookie": "junk"}).text,
    ]
    for t in texts:
        assert_no_tokens(t)
        assert "FPL_REFRESH_TOKEN=" not in t and "FPL_ACCESS_TOKEN=" not in t


def test_a_refusal_that_fell_through_the_cookie_path_is_still_a_refusal() -> None:
    """The owner's .env also holds a legacy FPL_SESSION_COOKIE, so the client
    reported 'Bearer auth failed first: ...refused the refresh grant... The
    cookie fallback then also failed (HTTP 403)'. The headline must name the
    issuer's refusal, not the cookie nobody relied on."""
    from fpl_edge.myteam.account import _classify
    from fpl_edge.myteam.private import StaleSessionError

    exc = StaleSessionError(
        "Bearer auth failed first: The token endpoint at https://auth.example "
        "refused the refresh grant (HTTP 400): invalid_grant; Refresh token does "
        "not exist. Log in once...\nThe cookie fallback then also failed (HTTP 403).\n"
    )
    error_class, remediation = _classify(exc)
    assert error_class == "refresh_refused"
    assert "Re-copy the cookie" in remediation
    plain, _ = _classify(StaleSessionError("FPL rejected the session cookie (HTTP 403)."))
    assert plain == "session_rejected"
