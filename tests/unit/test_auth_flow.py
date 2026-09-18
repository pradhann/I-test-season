"""Google sign-in end to end, against a faked provider in this process.

The real flow needs an OAuth client that only the owner can create, so the
provider is faked at two seams and nowhere else:

* ``httpx.post`` stands in for Google's token endpoint;
* ``oauth.jwks_client`` returns a key set holding a key this test generated,
  so the id_token is signed for real and verified for real by ``pyjwt``.

Everything between those two seams is the production code: the handshake
cookie, the state comparison, the PKCE challenge, the nonce check, the
``email_verified`` check, the user upsert, the session row, and the cookie
attributes.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fpl_edge.platform.auth import oauth, sessions
from fpl_edge.platform.auth.routes import install_auth
from tests.unit import auth_fixtures

UTC = dt.UTC


@pytest.fixture
def signing_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJWKS:
    """Stands in for ``PyJWKClient``. Returns one key for any token."""

    def __init__(self, key):
        self._key = key

    def get_signing_key_from_jwt(self, token):
        del token
        return type("Key", (), {"key": self._key.public_key()})()


def make_id_token(signing_key, *, nonce: str, sub: str = "200000000000000000002",
                  email: str = auth_fixtures.OTHER_EMAIL,
                  email_verified: bool = True,
                  aud: str | None = None,
                  iss: str = "https://accounts.google.com",
                  exp_delta_s: int = 600) -> str:
    now = dt.datetime.now(UTC)
    claims = {
        "iss": iss,
        "aud": aud or auth_fixtures.CLIENT_ID,
        "sub": sub,
        "email": email,
        "email_verified": email_verified,
        "nonce": nonce,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(seconds=exp_delta_s)).timestamp()),
    }
    return jwt.encode(claims, signing_key, algorithm="RS256")


@pytest.fixture
def client(monkeypatch, tmp_path):
    auth_fixtures.configure(monkeypatch, tmp_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app)
    # follow_redirects off: every assertion here is about the 302 itself.
    return TestClient(app, follow_redirects=False)


def token_endpoint(monkeypatch, signing_key, *, nonce_from, **token_kwargs):
    """Replace ``httpx.post`` with Google's token endpoint, signing on demand."""
    import httpx

    seen: dict = {}

    class _Reply:
        status_code = 200

        def json(self):
            return {"id_token": make_id_token(signing_key,
                                              nonce=nonce_from(),
                                              **token_kwargs),
                    "access_token": "google-access-token-discarded"}

    def fake_post(url, data=None, timeout=None, **kwargs):
        del timeout, kwargs
        seen["url"] = url
        seen["data"] = dict(data or {})
        return _Reply()

    monkeypatch.setattr(httpx, "post", fake_post)
    return seen


def start_and_read(client) -> tuple[dict, str]:
    """Drive ``/auth/google/start`` and return Google's query and the cookie."""
    r = client.get("/auth/google/start", params={"next": "/#account"})
    assert r.status_code == 302
    query = {k: v[0] for k, v in
             parse_qs(urlparse(r.headers["location"]).query).items()}
    cookie = r.cookies[sessions.HANDSHAKE_COOKIE]
    client.cookies.set(sessions.HANDSHAKE_COOKIE, cookie)
    return query, cookie


# -- start -------------------------------------------------------------------

def test_start_sends_pkce_state_and_nonce_and_asks_for_two_scopes(client) -> None:
    query, cookie = start_and_read(client)
    assert query["client_id"] == auth_fixtures.CLIENT_ID
    assert query["redirect_uri"] == auth_fixtures.REDIRECT_URI
    assert query["response_type"] == "code"
    assert query["scope"] == "openid email"
    assert query["code_challenge_method"] == "S256"
    assert query["prompt"] == "select_account"

    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    assert handshake is not None
    assert query["state"] == handshake.state
    assert query["nonce"] == handshake.nonce
    # The challenge is the sha256 of the verifier, and the verifier itself
    # never appears in anything sent to Google.
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(handshake.code_verifier.encode()).digest()
    ).decode().rstrip("=")
    assert query["code_challenge"] == expected
    assert handshake.code_verifier not in json.dumps(query)


def test_start_refuses_to_bounce_a_sign_in_to_another_host(client) -> None:
    """An open redirect here would let a phishing page send a real sign-in
    somewhere else. Anything that is not a same-origin path becomes ``/``."""
    for hostile in ("https://evil.test/steal", "//evil.test/steal",
                    "javascript:alert(1)", "/\\evil.test"):
        r = client.get("/auth/google/start", params={"next": hostile})
        handshake = oauth.Handshake.from_cookie_value(
            sessions.unsign(r.cookies[sessions.HANDSHAKE_COOKIE]))
        assert handshake.next_path == "/", hostile


def test_the_handshake_cookie_is_httponly_lax_and_ten_minutes(client) -> None:
    r = client.get("/auth/google/start")
    header = [v for k, v in r.headers.raw
              if k.lower() == b"set-cookie"
              and b"itest_oauth" in v][0].decode()
    assert "HttpOnly" in header
    assert "SameSite=lax" in header.replace("SameSite=Lax", "SameSite=lax")
    assert "Path=/auth" in header
    assert "Max-Age=600" in header


def test_start_says_so_when_no_google_client_is_configured(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path, sign_in_available=False)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app)
    local = TestClient(app, follow_redirects=False)
    r = local.get("/auth/google/start")
    assert r.status_code == 302
    assert r.headers["location"] == "/?auth_error=not_configured"


# -- callback ----------------------------------------------------------------

def test_the_callback_signs_a_manager_in_and_keeps_two_claims(
        client, monkeypatch, signing_key) -> None:
    query, cookie = start_and_read(client)
    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))
    seen = token_endpoint(monkeypatch, signing_key,
                          nonce_from=lambda: handshake.nonce)

    r = client.get("/auth/google/callback",
                   params={"code": "google-code", "state": query["state"]})
    assert r.status_code == 302
    assert r.headers["location"] == "/#account"

    # The exchange carried the verifier and the secret, and nothing else.
    assert seen["url"] == oauth.TOKEN_ENDPOINT
    assert seen["data"]["code_verifier"] == handshake.code_verifier
    assert seen["data"]["grant_type"] == "authorization_code"
    assert seen["data"]["client_secret"] == auth_fixtures.CLIENT_SECRET

    with sessions.connect() as conn:
        rows = conn.execute("SELECT * FROM users").fetchall()
        assert len(rows) == 1
        stored = dict(rows[0])
        assert stored["sub"] == "200000000000000000002"
        assert stored["email"] == auth_fixtures.OTHER_EMAIL
        assert stored["is_operator"] == 0
        # Two claims of identity and two timestamps. Nothing from Google's
        # profile and no token of any kind.
        assert set(stored) == {"sub", "email", "is_operator", "entry_id",
                               "created_utc", "last_seen_utc"}
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        # The row holds the digest of the sid, so a copy of this file is not
        # a bag of live cookies.
        sid_hash = conn.execute("SELECT sid_hash FROM sessions").fetchone()[0]

    raw = r.cookies[sessions.SESSION_COOKIE]
    sid = sessions.unsign(raw)
    assert sid and sid not in sid_hash
    assert hashlib.sha256(sid.encode()).hexdigest() == sid_hash


def test_the_operator_is_the_address_the_deployment_names(
        client, monkeypatch, signing_key) -> None:
    query, cookie = start_and_read(client)
    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))
    token_endpoint(monkeypatch, signing_key,
                   nonce_from=lambda: handshake.nonce,
                   sub="100000000000000000001",
                   email=auth_fixtures.OPERATOR_EMAIL.upper())

    r = client.get("/auth/google/callback",
                   params={"code": "c", "state": query["state"]})
    assert r.status_code == 302
    with sessions.connect() as conn:
        row = conn.execute("SELECT is_operator FROM users").fetchone()
    # Case-folded, because an address that differs only in case is the same
    # account and a demoted operator is a locked-out server.
    assert row[0] == 1


def test_the_session_cookies_carry_the_attributes_the_spec_names(
        client, monkeypatch, signing_key) -> None:
    query, cookie = start_and_read(client)
    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))
    token_endpoint(monkeypatch, signing_key, nonce_from=lambda: handshake.nonce)
    r = client.get("/auth/google/callback",
                   params={"code": "c", "state": query["state"]})

    headers = [v.decode() for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    session_header = [h for h in headers if h.startswith("itest_session=")][0]
    csrf_header = [h for h in headers if h.startswith("itest_csrf=")][0]
    assert "HttpOnly" in session_header
    assert "Max-Age=2592000" in session_header
    assert "Path=/" in session_header
    assert "samesite=lax" in session_header.lower()
    # The CSRF cookie is readable by the page on purpose: it has to echo it.
    assert "HttpOnly" not in csrf_header
    assert "samesite=lax" in csrf_header.lower()
    # The handshake is cleared as soon as it has done its work.
    assert any(h.startswith("itest_oauth=") and "Max-Age=0" in h
               for h in headers)


@pytest.mark.parametrize("state_value", ["", "not-the-state-we-minted"])
def test_a_state_mismatch_is_refused_and_writes_no_user(
        client, monkeypatch, signing_key, state_value) -> None:
    query, cookie = start_and_read(client)
    del query
    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail(
        "a state mismatch must be refused before the token exchange"))
    del handshake

    r = client.get("/auth/google/callback",
                   params={"code": "c", "state": state_value})
    assert r.status_code == 302
    assert r.headers["location"] == "/?auth_error=state_mismatch"
    with sessions.connect() as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_a_missing_handshake_reads_as_expired(client, monkeypatch) -> None:
    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("no exchange"))
    r = client.get("/auth/google/callback", params={"code": "c", "state": "s"})
    assert r.headers["location"] == "/?auth_error=handshake_expired"


def test_a_handshake_older_than_ten_minutes_is_expired(
        client, monkeypatch, signing_key) -> None:
    """The cookie's Max-Age is the browser's copy of the deadline, and a
    cookie is client controlled state. The server checks the age it signed
    into the payload rather than trusting the browser to have dropped it."""
    import time as time_mod

    query, cookie = start_and_read(client)
    del cookie
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail(
        "an expired handshake must be refused before the token exchange"))
    # The module's own clock, eleven minutes on. Patched on the module rather
    # than on `time` itself, so nothing else in the process moves.
    eleven_minutes_on = time_mod.time() + 660
    monkeypatch.setattr(oauth, "time",
                        type("Clock", (), {"time": staticmethod(
                            lambda: eleven_minutes_on)})())

    r = client.get("/auth/google/callback",
                   params={"code": "c", "state": query["state"]})
    assert r.headers["location"] == "/?auth_error=handshake_expired"


def test_a_declined_consent_is_its_own_class(client) -> None:
    start_and_read(client)
    r = client.get("/auth/google/callback", params={"error": "access_denied"})
    assert r.headers["location"] == "/?auth_error=consent_declined"


@pytest.mark.parametrize("bad", [
    {"nonce": "a-different-nonce"},
    {"email_verified": False},
    {"aud": "someone-elses-client-id"},
    {"iss": "https://accounts.evil.test"},
    {"exp_delta_s": -60},
])
def test_an_id_token_that_fails_any_check_writes_no_user(
        client, monkeypatch, signing_key, bad) -> None:
    query, cookie = start_and_read(client)
    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))
    nonce = bad.pop("nonce", None) or handshake.nonce
    token_endpoint(monkeypatch, signing_key, nonce_from=lambda: nonce, **bad)

    r = client.get("/auth/google/callback",
                   params={"code": "c", "state": query["state"]})
    assert r.headers["location"] == "/?auth_error=token_invalid"
    with sessions.connect() as conn:
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 0


def test_an_id_token_signed_by_another_key_is_refused(
        client, monkeypatch, signing_key) -> None:
    """The signature is checked against Google's key set, not assumed."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    query, cookie = start_and_read(client)
    handshake = oauth.Handshake.from_cookie_value(sessions.unsign(cookie))
    monkeypatch.setattr(oauth, "jwks_client", lambda: _FakeJWKS(signing_key))
    token_endpoint(monkeypatch, other, nonce_from=lambda: handshake.nonce)

    r = client.get("/auth/google/callback",
                   params={"code": "c", "state": query["state"]})
    assert r.headers["location"] == "/?auth_error=token_invalid"


# -- logout ------------------------------------------------------------------

def test_logout_revokes_the_row_and_clears_the_cookies(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app)
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)

    assert client.get("/api/me").json()["signed_in"] is True
    r = client.post("/auth/logout", headers=who.headers())
    assert r.status_code == 204
    with sessions.connect() as conn:
        assert conn.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    cleared = [v.decode() for k, v in r.headers.raw if k.lower() == b"set-cookie"]
    assert all("Max-Age=0" in h for h in cleared)
    assert {h.split("=")[0] for h in cleared} == {
        "itest_session", "itest_csrf", "itest_oauth"}
    assert client.get("/api/me").json()["signed_in"] is False


def test_logout_leaves_the_other_device_signed_in(monkeypatch, tmp_path) -> None:
    """Signing out of a phone does not sign out the laptop."""
    auth_fixtures.configure(monkeypatch, tmp_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app)
    phone = TestClient(app)
    laptop = TestClient(app)
    auth_fixtures.sign_in(phone)
    other = auth_fixtures.sign_in(laptop)

    phone.post("/auth/logout", headers={"X-CSRF-Token":
                                        phone.cookies["itest_csrf"]})
    assert laptop.get("/api/me").json()["signed_in"] is True
    assert other.sid
