"""The self-renewing auth flow, offline.

Every network interaction is faked; what is tested is the logic that decides
WHEN to refresh, what gets persisted, and that no credential can leak through
status text or errors.
"""

from __future__ import annotations

import base64
import datetime as dt
import json

import httpx
import pytest

from fpl_edge.myteam.tokens import (
    AuthNotConfiguredError,
    RefreshRefusedError,
    TokenManager,
    extract_tokens_from_cookie,
    jwt_expiry,
)


def make_jwt(exp_in_s: int, **claims) -> str:
    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    payload = {"iat": now, "exp": now + exp_in_s, **claims}
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.FAKESIG"


ISSUER = "https://auth.example.test/env/as"
CLIENT = "client-123"


def env_file(tmp_path, *, access_exp: int, refresh_exp: int = 3600 * 24 * 90):
    access = make_jwt(access_exp, iss=ISSUER, client_id=CLIENT)
    refresh = make_jwt(refresh_exp, iss=ISSUER)
    p = tmp_path / ".env"
    p.write_text(
        f"FPL_ACCESS_TOKEN={access}\nFPL_REFRESH_TOKEN={refresh}\n"
        f"FPL_OAUTH_ISSUER={ISSUER}\nFPL_OAUTH_CLIENT_ID={CLIENT}\n"
    )
    return p, access, refresh


def test_fresh_access_token_is_returned_without_any_network(tmp_path, monkeypatch) -> None:
    p, access, _ = env_file(tmp_path, access_exp=3600)

    def no_network(*a, **k):
        raise AssertionError("a fresh token must not trigger a refresh")

    monkeypatch.setattr(httpx, "post", no_network)
    assert TokenManager(env_path=p).access_token() == access


def test_expired_access_token_triggers_the_refresh_grant(tmp_path, monkeypatch) -> None:
    p, _, old_refresh = env_file(tmp_path, access_exp=-60)
    new_access = make_jwt(3600 * 8, iss=ISSUER, client_id=CLIENT)
    new_refresh = make_jwt(3600 * 24 * 180, iss=ISSUER)
    seen = {}

    def fake_post(url, data=None, headers=None, timeout=None):
        seen["url"] = url
        seen["data"] = data
        return httpx.Response(
            200, json={"access_token": new_access, "refresh_token": new_refresh},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    got = TokenManager(env_path=p).access_token()
    assert got == new_access
    assert seen["url"] == f"{ISSUER}/token"
    assert seen["data"]["grant_type"] == "refresh_token"
    assert seen["data"]["client_id"] == CLIENT
    # Rotation: BOTH tokens persisted, or the next refresh replays a consumed one.
    text = p.read_text()
    assert new_access in text and new_refresh in text
    assert old_refresh not in text


def test_near_expiry_counts_as_expired(tmp_path, monkeypatch) -> None:
    """A token with 2 minutes left dies mid-request-sequence; refresh early."""
    p, _, _ = env_file(tmp_path, access_exp=120)
    called = {"n": 0}

    def fake_post(url, **k):
        called["n"] += 1
        return httpx.Response(
            200, json={"access_token": make_jwt(3600, iss=ISSUER, client_id=CLIENT)},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    TokenManager(env_path=p).access_token()
    assert called["n"] == 1


def test_refused_grant_names_the_recovery_not_the_token(tmp_path, monkeypatch) -> None:
    p, _, refresh = env_file(tmp_path, access_exp=-60)

    def deny(url, **k):
        return httpx.Response(400, json={"error": "invalid_grant"},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", deny)
    with pytest.raises(RefreshRefusedError, match="paste-cookie") as exc:
        TokenManager(env_path=p).access_token()
    assert refresh not in str(exc.value)


def test_expired_refresh_token_fails_before_any_network(tmp_path, monkeypatch) -> None:
    p, _, _ = env_file(tmp_path, access_exp=-60, refresh_exp=-3600)

    def no_network(*a, **k):
        raise AssertionError("an expired refresh token must not be replayed")

    monkeypatch.setattr(httpx, "post", no_network)
    with pytest.raises(RefreshRefusedError, match="expired"):
        TokenManager(env_path=p).access_token()


def test_unconfigured_manager_gives_setup_steps(tmp_path) -> None:
    p = tmp_path / ".env"
    p.write_text("OTHER=1\n")
    with pytest.raises(AuthNotConfiguredError, match="One-time setup"):
        TokenManager(env_path=p).access_token()


def test_cookie_extraction_finds_both_tokens() -> None:
    a = make_jwt(100, iss=ISSUER, client_id=CLIENT)
    r = make_jwt(1000, iss=ISSUER)
    cookie = f"_ga=x; access_token={a}; foo=bar; refresh_token={r}; _fbp=y"
    got_a, got_r = extract_tokens_from_cookie(cookie)
    assert got_a == a and got_r == r


def test_ingest_from_cookie_stores_issuer_and_client_from_the_jwt(tmp_path) -> None:
    a = make_jwt(100, iss=ISSUER, client_id=CLIENT)
    r = make_jwt(1000, iss=ISSUER)
    p = tmp_path / ".env"
    p.write_text("KEEP=me\n")
    status = TokenManager(env_path=p).ingest_from_cookie(
        f"access_token={a}; refresh_token={r}"
    )
    text = p.read_text()
    assert "KEEP=me" in text                      # existing vars survive
    assert f"FPL_OAUTH_ISSUER={ISSUER}" in text
    assert f"FPL_OAUTH_CLIENT_ID={CLIENT}" in text
    assert "refresh token valid" in status
    assert a not in status and r not in status    # status never leaks tokens


def test_status_reports_expiry_without_the_credential(tmp_path) -> None:
    p, access, refresh = env_file(tmp_path, access_exp=-10)
    status = TokenManager(env_path=p).status()
    assert "expired" in status and "days left" in status
    assert access not in status and refresh not in status


def test_jwt_expiry_reads_exp() -> None:
    tok = make_jwt(3600)
    delta = jwt_expiry(tok) - dt.datetime.now(dt.timezone.utc)
    assert 3500 < delta.total_seconds() <= 3600


def test_the_auth_command_exists_and_is_wired() -> None:
    """A documented command that does not exist is worse than no docs.

    MVP.md told the user to run `fpl myteam auth`; the command had been lost in
    an edit and the docs were never re-checked against the CLI. This asserts the
    two agree.
    """
    from typer.testing import CliRunner

    from fpl_edge.myteam.cli import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("auth", "cookie", "show", "set", "sync", "transfers", "whynot"):
        assert command in result.output, f"`fpl myteam {command}` is missing"


def test_every_myteam_command_named_in_the_docs_exists() -> None:
    """Docs and CLI must not drift apart again."""
    import re
    from pathlib import Path

    from typer.testing import CliRunner

    from fpl_edge.myteam.cli import app

    listed = CliRunner().invoke(app, ["--help"]).output
    for doc in (Path("MVP.md"), Path("docs/platform/ROADMAP.md")):
        if not doc.exists():
            continue
        for cmd in set(re.findall(r"fpl myteam ([a-z-]+)", doc.read_text())):
            assert cmd in listed, f"{doc} references `fpl myteam {cmd}`, which does not exist"
