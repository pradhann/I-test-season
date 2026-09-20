"""The per-user Anthropic key: round trip, and every place it must not appear.

The sentinel is a key-shaped string that is not a key. Every assertion below
looks for it in a place a key could leak: a response body, a response header,
a log record, a repr, the SQLite file's raw bytes, ``os.environ``, and the
stored conversation transcript. The one place it is allowed to appear is
``ClaudeAgentOptions.env`` for the turn that spends it.
"""

from __future__ import annotations

import json
import logging
import os

import pytest
from fastapi.testclient import TestClient

from fpl_edge.platform.app.factory import create_app
from fpl_edge.platform.auth import keys
from fpl_edge.platform.auth.routes import build_router
from tests.unit import auth_fixtures

SENTINEL = auth_fixtures.SENTINEL


@pytest.fixture
def app(monkeypatch, tmp_path):
    auth_fixtures.configure(monkeypatch, tmp_path)
    return create_app(db=tmp_path / "warehouse.duckdb")


@pytest.fixture
def signed_in(app):
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    return client, who


# -- the round trip ----------------------------------------------------------

def test_encrypt_and_decrypt_round_trip(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    assert keys.state("someone").set is False

    state = keys.store("someone", SENTINEL)
    assert state.set is True
    assert state.last4 == SENTINEL[-4:]
    loaded = keys.load("someone")
    assert loaded.value == SENTINEL
    assert loaded.last4 == SENTINEL[-4:]

    assert keys.remove("someone").set is False
    assert keys.load("someone") is None


def test_a_ciphertext_moved_to_another_row_does_not_decrypt(
        monkeypatch, tmp_path) -> None:
    """The user id is the associated data, so a row copied between managers
    fails to decrypt rather than silently authorising the wrong account."""
    auth_fixtures.configure(monkeypatch, tmp_path)
    keys.store("manager-one", SENTINEL)
    from fpl_edge.platform.auth.sessions import connect

    with connect() as conn:
        row = conn.execute("SELECT * FROM user_keys").fetchone()
        conn.execute(
            "INSERT INTO user_keys (user_id, ciphertext, nonce, last4, "
            "key_version, created_utc) VALUES (?, ?, ?, ?, ?, ?)",
            ("manager-two", row["ciphertext"], row["nonce"], row["last4"],
             row["key_version"], row["created_utc"]),
        )
    with pytest.raises(keys.KeyUnreadable):
        keys.load("manager-two")


def test_the_sqlite_file_holds_no_plaintext(monkeypatch, tmp_path) -> None:
    """Catches an encryption path that accidentally stored the paste."""
    auth_db = auth_fixtures.configure(monkeypatch, tmp_path)
    keys.store("someone", SENTINEL)
    from fpl_edge.platform.auth.sessions import connect

    with connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = b"".join(p.read_bytes() for p in auth_db.parent.iterdir()
                    if p.is_file())
    assert SENTINEL.encode() not in blob
    # The last four are kept on purpose and are the only plaintext fragment.
    assert SENTINEL[-4:].encode() in blob


def test_a_rotation_without_the_previous_secret_says_so(
        monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    keys.store("someone", SENTINEL)
    from fpl_edge.platform.auth.sessions import connect

    with connect() as conn:
        conn.execute("UPDATE user_keys SET key_version = 0")
    with pytest.raises(keys.KeyUnreadable) as caught:
        keys.load("someone")
    assert str(caught.value) == keys.ROTATED_OUT


def test_a_key_at_the_previous_secret_is_re_encrypted_on_first_use(
        monkeypatch, tmp_path) -> None:
    import base64

    previous = base64.b64encode(b"previous-key-encryption-secret32").decode()
    auth_fixtures.configure(monkeypatch, tmp_path)
    monkeypatch.setenv("USER_KEY_ENC_SECRET", previous)
    keys.store("someone", SENTINEL)
    # The owner rotates: the old secret moves to _PREV and a new one arrives.
    monkeypatch.setenv("USER_KEY_ENC_SECRET", auth_fixtures.KEY_SECRET)
    monkeypatch.setenv("USER_KEY_ENC_SECRET_PREV", previous)
    from fpl_edge.platform.auth.sessions import connect

    with connect() as conn:
        conn.execute("UPDATE user_keys SET key_version = 0")

    assert keys.load("someone").value == SENTINEL
    # Re-encrypted at the current secret, so the rotation window closes on its
    # own and the previous secret can be removed.
    monkeypatch.delenv("USER_KEY_ENC_SECRET_PREV", raising=False)
    assert keys.load("someone").value == SENTINEL


# -- validation --------------------------------------------------------------

@pytest.mark.parametrize("bad,expected", [
    ("", keys.EMPTY_PASTE),
    ("   ", keys.EMPTY_PASTE),
    (None, keys.EMPTY_PASTE),
    ("hunter2", keys.BAD_SHAPE),
    ("sk-ant-short", keys.BAD_SHAPE),
    ("sk-proj-not-anthropic-0000000000000", keys.BAD_SHAPE),
])
def test_a_paste_that_is_not_a_key_is_refused_with_the_owners_words(
        bad, expected) -> None:
    with pytest.raises(keys.KeyInvalid) as caught:
        keys.validate(bad)
    assert str(caught.value) == expected


def test_the_put_route_refuses_a_bad_paste_without_echoing_it(signed_in) -> None:
    client, who = signed_in
    r = client.put("/api/account/key", json={"key": "hunter2-not-a-key"},
                   headers=who.headers())
    assert r.status_code == 400
    assert r.json()["detail"] == keys.BAD_SHAPE
    assert "hunter2" not in r.text


def test_a_key_anthropic_refuses_is_not_stored(signed_in, monkeypatch) -> None:
    from fpl_edge.platform.auth import routes as auth_routes

    monkeypatch.setenv("FPL_EDGE_VERIFY_USER_KEY", "1")
    monkeypatch.setattr(auth_routes, "verify_key",
                        lambda value: (False, "AuthenticationError: invalid x-api-key"))
    client, who = signed_in
    r = client.put("/api/account/key", json={"key": SENTINEL},
                   headers=who.headers())
    assert r.status_code == 400
    assert "invalid x-api-key" in r.json()["detail"]
    assert SENTINEL not in r.text
    assert client.get("/api/account/key").json()["set"] is False


# -- the routes --------------------------------------------------------------

def test_the_key_routes_never_return_the_key(signed_in, caplog) -> None:
    client, who = signed_in
    caplog.set_level(logging.DEBUG, logger="")

    put = client.put("/api/account/key", json={"key": SENTINEL},
                     headers=who.headers())
    assert put.status_code == 200
    assert put.json()["last4"] == SENTINEL[-4:]

    get = client.get("/api/account/key")
    assert get.json()["set"] is True
    assert get.json()["last4"] == SENTINEL[-4:]

    me = client.get("/api/me")
    assert me.json()["key_set"] is True
    assert me.json()["last4"] == SENTINEL[-4:]

    delete = client.delete("/api/account/key", headers=who.headers())
    assert delete.json() == {"set": False, "last4": None,
                             "created_utc": None, "rotated_utc": None}

    for response in (put, get, me, delete):
        assert SENTINEL not in response.text
        assert SENTINEL not in json.dumps(dict(response.headers))
    for record in caplog.records:
        assert SENTINEL not in record.getMessage()
        assert SENTINEL not in str(record.args)
        assert SENTINEL not in logging.Formatter().format(record)


def test_a_second_put_rotates_in_place_and_keeps_no_history(signed_in) -> None:
    client, who = signed_in
    client.put("/api/account/key", json={"key": SENTINEL}, headers=who.headers())
    second = SENTINEL[:-4] + "9999"
    out = client.put("/api/account/key", json={"key": second},
                     headers=who.headers()).json()
    assert out["last4"] == "9999"
    assert out["rotated_utc"] is not None
    from fpl_edge.platform.auth.sessions import connect

    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM user_keys").fetchone()[0] == 1


def test_one_manager_cannot_read_another_managers_key(app) -> None:
    first = TestClient(app)
    one = auth_fixtures.sign_in(first, email="one@example.test", sub="1111")
    first.put("/api/account/key", json={"key": SENTINEL}, headers=one.headers())

    second = TestClient(app)
    auth_fixtures.sign_in(second, email="two@example.test", sub="2222")
    state = second.get("/api/account/key").json()
    assert state["set"] is False
    assert SENTINEL not in second.get("/api/account/key").text


def test_the_dataclass_repr_shows_four_characters(monkeypatch, tmp_path) -> None:
    auth_fixtures.configure(monkeypatch, tmp_path)
    keys.store("someone", SENTINEL)
    loaded = keys.load("someone")
    assert SENTINEL not in repr(loaded)
    assert SENTINEL not in str(loaded)
    assert SENTINEL not in f"a log line about {loaded}"
    assert loaded.last4 in repr(loaded)


# -- the turn ----------------------------------------------------------------

def test_the_key_reaches_options_env_for_exactly_one_turn(
        monkeypatch, tmp_path) -> None:
    """The one place the value is allowed to appear, and only there.

    ``ClaudeAgentOptions.env`` is the environment the SDK hands to the CLI
    subprocess. os.environ is process global and the server runs turns from
    different conversations concurrently, so writing it there would let two
    managers' turns race and one be billed to the other's key.
    """
    from fpl_edge.platform.chat_agent import ChatAgent
    from fpl_edge.platform.users import UserContext

    auth_fixtures.configure(monkeypatch, tmp_path)
    keys.store("g2222", SENTINEL)
    ctx = UserContext(user_id="g2222", entry_id=1, display_name=None,
                      data_root=tmp_path / "u", is_owner=False)

    agent = ChatAgent(root=tmp_path / "chat")
    meta = agent.create_conversation()
    conv = agent._conv(meta["conv_id"])

    seen: list = []

    def fake_transport(options):
        seen.append(options)
        raise RuntimeError("stop before the SDK connects")

    agent._transport_factory = fake_transport
    monkeypatch.setattr(agent, "allowed_tools", list)
    os.environ["ANTHROPIC_API_KEY"] = "operator-key-that-must-be-scrubbed"

    agent.start_turn(meta["conv_id"], "hello", user=ctx)
    conv.turn.finished.wait(timeout=20)

    assert seen, "the turn never built its options"
    assert seen[0].env == {"ANTHROPIC_API_KEY": SENTINEL}
    # Scrubbed from the server's own environment, so a bug that forgot
    # options.env fails loudly with an auth error rather than
    # silently spending somebody else's plan.
    assert "ANTHROPIC_API_KEY" not in os.environ
    # And the transcript the turn wrote holds neither key.
    transcript = (conv.path / "events.jsonl").read_text()
    assert SENTINEL not in transcript
    assert "operator-key-that-must-be-scrubbed" not in transcript


def test_the_owner_runs_on_the_cli_login_with_no_key_in_the_options(
        monkeypatch, tmp_path) -> None:
    from fpl_edge.platform.chat_agent import ChatAgent

    auth_fixtures.configure(monkeypatch, tmp_path, anon_is_owner=True)
    agent = ChatAgent(root=tmp_path / "chat")
    meta = agent.create_conversation()
    conv = agent._conv(meta["conv_id"])
    seen: list = []

    def fake_transport(options):
        seen.append(options)
        raise RuntimeError("stop before the SDK connects")

    agent._transport_factory = fake_transport
    monkeypatch.setattr(agent, "allowed_tools", list)
    agent.start_turn(meta["conv_id"], "hello")
    conv.turn.finished.wait(timeout=20)
    assert seen[0].env == {}


def test_a_stderr_line_carrying_the_key_is_redacted_before_it_is_stored() -> None:
    key = keys.UserKey(value=SENTINEL, last4=SENTINEL[-4:])
    line = f"Error: ANTHROPIC_API_KEY={SENTINEL} rejected"
    cleaned = keys.redact(line, key)
    assert SENTINEL not in cleaned
    assert "sk-ant-[redacted]" in cleaned


# -- the batch path stays the operator's -------------------------------------

def test_the_batch_analysis_never_imports_the_key_store() -> None:
    """``analyze.py`` runs from the scheduler with no user and no session,
    under the operator's own credential. A future edit that reaches for a
    manager's key fails here rather than in review."""
    import ast
    import pathlib

    for name in ("fpl_edge/ingest/content/analyze.py",
                 "fpl_edge/platform/briefing_intel.py"):
        tree = ast.parse(pathlib.Path(name).read_text())
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not [m for m in imported if "auth" in m], (
            f"{name} imports {[m for m in imported if 'auth' in m]}. The batch "
            f"and briefing paths run under the operator's own credential and "
            f"never read the per-user key store."
        )


def test_the_key_routes_are_the_only_surface_that_names_the_store() -> None:
    """Nothing outside the auth package and the user context reads user_keys."""
    import pathlib

    offenders = []
    for path in pathlib.Path("fpl_edge").rglob("*.py"):
        if "platform/auth" in str(path):
            continue
        text = path.read_text()
        if "user_keys" in text or "auth.keys" in text:
            offenders.append(str(path))
    assert offenders == ["fpl_edge/platform/users.py"], offenders


def test_the_no_key_body_is_the_text_the_ui_prints() -> None:
    """A reworded remediation is a failing test rather than a silent UI
    regression: the composer prints this string verbatim."""
    assert keys.NO_KEY_BODY == {
        "error": "no_api_key",
        "detail": (
            "No Anthropic API key is stored for this account. Open the "
            "Account tab, paste a key from console.anthropic.com, and send "
            "the message again."
        ),
        "remediation_url": "/#account",
    }
    assert build_router() is not None


def _agent(tmp_path):
    """An agent that spawns nothing: MCP enumeration points at no interpreter."""
    from fpl_edge.platform import chat_agent

    return chat_agent.ChatAgent(
        root=tmp_path / "chat",
        mcp_python="/nonexistent/python",
        mcp_main=tmp_path / "no_main.py",
    )


# --- two kinds of credential, one per user ---------------------------------
# A user spends their own Anthropic account (an API key) or their own Claude
# subscription (a token from `claude setup-token`). Anthropic publishes no
# OAuth flow that would let this server ask for the second grant on a
# visitor's behalf, so both arrive the same way: the user mints the value on
# their own machine and pastes it. The two go in different environment
# variables and the SDK fails if they are swapped.

def test_the_two_credential_kinds_are_told_apart_by_shape():
    from fpl_edge.platform.auth import keys

    api = "sk-ant-api03-" + "a" * 40
    oat = "sk-ant-oat01-" + "b" * 40
    assert keys.credential_kind(api) == "api_key"
    assert keys.credential_kind(oat) == "oauth_token"


def test_each_kind_names_the_variable_the_sdk_reads():
    from fpl_edge.platform.auth import keys

    assert keys.CREDENTIAL_ENV["api_key"] == "ANTHROPIC_API_KEY"
    assert keys.CREDENTIAL_ENV["oauth_token"] == "CLAUDE_CODE_OAUTH_TOKEN"


def test_a_subscription_token_reaches_the_sdk_in_its_own_variable(tmp_path):
    """The whole point: the user's own subscription pays for their turn."""
    from fpl_edge.platform import chat_agent

    agent = _agent(tmp_path)
    conv = agent.create_conversation()
    oat = "sk-ant-oat01-" + "c" * 40
    opts = agent.build_options(conv, None, anthropic_key=oat)
    assert opts.env.get("CLAUDE_CODE_OAUTH_TOKEN") == oat
    assert "ANTHROPIC_API_KEY" not in opts.env

    api = "sk-ant-api03-" + "d" * 40
    opts = agent.build_options(conv, None, anthropic_key=api)
    assert opts.env.get("ANTHROPIC_API_KEY") == api
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in opts.env


def test_a_subscription_token_in_the_server_environment_is_scrubbed(monkeypatch, tmp_path):
    """One left in this process would spend the operator's own plan."""
    import os

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "e" * 40)
    agent = _agent(tmp_path)
    agent._scrub_environment()
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ
