"""Who a request is: the operator, a signed-in manager, or a public visitor.

``resolve_identity`` has three answers and this file pins all three, plus the
two things that decide between them: ``OPERATOR_EMAIL`` and
``FPL_EDGE_ANON_IS_OWNER``.

The property that matters most is the one in
:func:`test_a_signed_in_manager_gets_their_own_directory`: two managers on one
server read and write different directories, so a second person on this
deployment cannot see the owner's squad, plan or chat history.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from fpl_edge.platform import users
from fpl_edge.platform.auth import oauth, sessions, settings
from fpl_edge.platform.auth.routes import install_auth
from tests.unit import auth_fixtures


def bare_request(cookies: dict[str, str] | None = None) -> Request:
    """A request object with no app behind it, for the direct calls below."""
    headers = []
    if cookies:
        raw = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers.append((b"cookie", raw.encode()))
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": headers, "query_string": b"",
                    "client": ("127.0.0.1", 1234)})


# -- the operator ------------------------------------------------------------

def test_the_operator_is_the_configured_address_and_nothing_else(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    assert oauth.is_operator(auth_fixtures.OPERATOR_EMAIL) is True
    assert oauth.is_operator(auth_fixtures.OPERATOR_EMAIL.upper()) is True
    assert oauth.is_operator("  " + auth_fixtures.OPERATOR_EMAIL + " ") is True
    assert oauth.is_operator(auth_fixtures.OTHER_EMAIL) is False


def test_a_server_nobody_has_claimed_has_no_operator(monkeypatch, tmp_path) -> None:
    """Until OPERATOR_EMAIL is set and that account signs in, every operator
    route answers 403 for everyone, which is the correct state for it."""
    auth_fixtures.configure(monkeypatch, tmp_path)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    assert settings.operator_email() is None
    assert oauth.is_operator(auth_fixtures.OPERATOR_EMAIL) is False


def test_the_operator_address_is_read_under_either_name(
        monkeypatch, tmp_path) -> None:
    """The spec names OPERATOR_EMAIL and the build brief names OWNER_EMAIL.
    Both are read, and two names holding different values is a mistake rather
    than a silent winner."""
    auth_fixtures.configure(monkeypatch, tmp_path)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    monkeypatch.setenv("OWNER_EMAIL", auth_fixtures.OPERATOR_EMAIL)
    assert settings.operator_email() == auth_fixtures.OPERATOR_EMAIL

    monkeypatch.setenv("OPERATOR_EMAIL", "someone@example.test")
    with pytest.raises(RuntimeError, match="two names for the same value"):
        settings.operator_email()


def test_the_signed_in_operator_resolves_to_the_owner_context(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    with sessions.connect() as conn:
        user = sessions.upsert_user(conn, sub="100000000000000000001",
                                    email=auth_fixtures.OPERATOR_EMAIL,
                                    is_operator=True)
        sid, _, _ = sessions.create_session(conn, sub=user.sub)

    request = bare_request({sessions.SESSION_COOKIE: sessions.sign(sid)})
    identity = users.resolve_identity(request)
    assert identity.user_id == users.OWNER_USER_ID
    ctx = users.context_for(identity)
    assert ctx.is_owner is True
    assert ctx.can_read_private is True
    assert ctx.data_root.name == users.OWNER_USER_ID


# -- a signed-in manager -----------------------------------------------------

def test_a_signed_in_manager_gets_their_own_directory(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    with sessions.connect() as conn:
        user = sessions.upsert_user(conn, sub="200000000000000000002",
                                    email=auth_fixtures.OTHER_EMAIL,
                                    is_operator=False)
        sid, _, _ = sessions.create_session(conn, sub=user.sub)

    identity = users.resolve_identity(
        bare_request({sessions.SESSION_COOKIE: sessions.sign(sid)}))
    assert identity.user_id == "g200000000000000000002"
    ctx = users.context_for(identity)
    assert ctx.is_owner is False
    assert ctx.can_read_private is False
    assert ctx.data_root != users.owner_context().data_root
    # The named gap, never the owner's data.
    with pytest.raises(users.PrivateReadDenied):
        ctx.token_env_path()


def test_a_user_id_is_always_a_directory_name(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    for sub in ("123", "a subject with spaces", "../../etc/passwd",
                "MiXeDcAsE", "9" * 200, ""):
        user_id = sessions.user_id_for(sub, is_operator=False)
        assert users.validate_user_id(user_id) == user_id
        assert user_id != users.OWNER_USER_ID


def test_two_subjects_never_share_a_directory(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    made = {sessions.user_id_for(str(sub), is_operator=False)
            for sub in ("1", "2", "one", "two", "../one", "one/")}
    assert len(made) == 6


def test_a_forged_or_unsigned_cookie_is_no_session(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    with sessions.connect() as conn:
        user = sessions.upsert_user(conn, sub="2", email="x@example.test",
                                    is_operator=False)
        sid, _, _ = sessions.create_session(conn, sub=user.sub)

    for forged in (sid, f"{sid}.", f"{sid}.wrongsignature", "", "."):
        identity = users.resolve_identity(
            bare_request({sessions.SESSION_COOKIE: forged}))
        assert identity.user_id == users.PUBLIC_USER_ID, forged


def test_rotating_the_session_secret_invalidates_every_cookie(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    with sessions.connect() as conn:
        user = sessions.upsert_user(conn, sub="2", email="x@example.test",
                                    is_operator=False)
        sid, _, _ = sessions.create_session(conn, sub=user.sub)
    signed = sessions.sign(sid)
    assert users.resolve_identity(
        bare_request({sessions.SESSION_COOKIE: signed})).user_id == "g2"

    monkeypatch.setenv("SESSION_SECRET", "a-different-session-secret-entirely")
    assert users.resolve_identity(
        bare_request({sessions.SESSION_COOKIE: signed})).user_id == (
            users.PUBLIC_USER_ID)


def test_an_expired_session_reads_as_absent(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    with sessions.connect() as conn:
        user = sessions.upsert_user(conn, sub="2", email="x@example.test",
                                    is_operator=False)
        sid, _, _ = sessions.create_session(conn, sub=user.sub)
        conn.execute("UPDATE sessions SET expires_utc = ?",
                     ("2020-01-01T00:00:00+00:00",))
        assert sessions.read_session(conn, sid) is None


# -- anonymous ---------------------------------------------------------------

def test_the_flag_makes_an_anonymous_request_the_owner(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path, anon_is_owner=True)
    assert settings.anon_is_owner() is True
    assert users.resolve_identity(bare_request()) is None
    assert users.current_user(bare_request()).is_owner is True


def test_without_the_flag_an_anonymous_request_is_a_public_visitor(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    monkeypatch.setenv("PUBLIC_ENTRY_ID", "4490171")
    identity = users.resolve_identity(bare_request())
    assert identity.user_id == users.PUBLIC_USER_ID
    assert identity.entry_id == 4490171
    ctx = users.context_for(identity)
    assert ctx.is_owner is False
    assert ctx.can_read_private is False
    # The published entry comes from the deployment and never from the
    # request, which is what keeps the public view one published team rather
    # than a facility for reading any team.
    assert ctx.entry_id == 4490171


def test_a_deployment_with_no_google_client_answers_as_the_owner(
        monkeypatch, tmp_path) -> None:
    """The default while the owner has not configured sign-in. A deployment
    nobody can sign in to has no other workable answer, and this is how the
    server runs on the owner's Mac and in every other suite in this repo."""
    auth_fixtures.configure(monkeypatch, tmp_path, sign_in_available=False)
    monkeypatch.delenv("FPL_EDGE_ANON_IS_OWNER", raising=False)
    assert settings.sign_in_configured() is False
    assert settings.anon_is_owner() is True
    assert users.resolve_identity(bare_request()) is None


def test_configuring_sign_in_flips_the_default_to_signed_out(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    monkeypatch.delenv("FPL_EDGE_ANON_IS_OWNER", raising=False)
    assert settings.sign_in_configured() is True
    assert settings.anon_is_owner() is False
    assert users.resolve_identity(bare_request()).user_id == users.PUBLIC_USER_ID


def test_the_flag_wins_over_the_derived_default(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path, sign_in_available=False)
    monkeypatch.setenv("FPL_EDGE_ANON_IS_OWNER", "0")
    assert settings.anon_is_owner() is False
    assert users.resolve_identity(bare_request()).user_id == users.PUBLIC_USER_ID


# -- what the shell sees -----------------------------------------------------

def test_me_reports_the_three_states(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_auth(app)

    anon = TestClient(app)
    assert anon.get("/api/me").json() == {
        "signed_in": False, "sign_in_available": True, "anon_is_owner": False}

    manager = TestClient(app)
    auth_fixtures.sign_in(manager)
    body = manager.get("/api/me").json()
    assert body["signed_in"] is True
    assert body["email"] == auth_fixtures.OTHER_EMAIL
    assert body["is_operator"] is False
    assert body["key_set"] is False

    operator = TestClient(app)
    auth_fixtures.sign_in(operator, email=auth_fixtures.OPERATOR_EMAIL,
                          is_operator=True)
    assert operator.get("/api/me").json()["is_operator"] is True
