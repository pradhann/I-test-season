"""The analysis brief as an on-demand, per-user action.

The decision this pins: each manager runs their own brief, on their own
credential, into their own artefact, and nothing runs on a schedule for them.
The operator's scheduled pass is unchanged and still runs on the CLI login of
the machine the server runs on.

Nothing here spawns the real CLI. ``claude_agent_sdk.query`` is replaced with
an async generator that records the options it was handed, which is how the
suite can assert the thing that matters: the credential reached
``ClaudeAgentOptions.env`` for that one subprocess and ``os.environ`` stayed
clean, so a run whose credential failed to arrive gets an auth error rather
than somebody else's plan.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from fpl_edge.platform import briefing_intel as bi
from fpl_edge.platform.app import create_app
from fpl_edge.platform.users import UserContext, owner_context
from fpl_edge.store.fetch_ledger import CallUsage, parse_spend, spend_note
from fpl_edge.store.warehouse import Warehouse
from tests.unit import auth_fixtures

UTC = dt.UTC

SENTINEL = auth_fixtures.SENTINEL
OAUTH_SENTINEL = "sk-ant-oat01-ZZZTESTSENTINELZZZ0000000000"

#: Every variable the run must not inherit from the server's own process.
SCRUBBED = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN")


@pytest.fixture(autouse=True)
def isolated_runs(monkeypatch, tmp_path):
    """A clean run registry and a per-user tree under tmp for every test."""
    monkeypatch.setenv("FPL_EDGE_DATA_DIR", str(tmp_path / "data"))
    with bi._RUNS_LOCK:
        bi._RUNS.clear()
    yield
    with bi._RUNS_LOCK:
        bi._RUNS.clear()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


def user_ctx(tmp_path, name: str) -> UserContext:
    return UserContext(user_id=name, entry_id=111, display_name=None,
                       data_root=tmp_path / "users" / name, is_owner=False)


def fake_panels(monkeypatch, *, own_pct: float = 55.0):
    results = {
        "ownership_eo": {
            "as_of": "2026-08-31 08:00:00",
            "rows": [{"code": 200, "name": "Haaland", "own_pct": own_pct}],
        },
    }
    monkeypatch.setattr(
        bi, "collect_panels",
        lambda wh, season, panels=bi.INPUT_PANELS, ctx=None: results)
    return results


def model_answer(own_pct: float = 55.0) -> str:
    return "```json\n" + json.dumps({"items": [
        {"headline": "Haaland is a template gap",
         "why": f"ownership_eo has him at {own_pct} own% and he is not owned",
         "severity": 1,
         "numbers": [{"value": own_pct, "unit": "own%",
                      "source_panel": "ownership_eo",
                      "as_of": "2026-08-31T08:00:00"}],
         "codes": [200],
         "drill": {"drawer": 200},
         "source_panels": ["ownership_eo"]},
    ]}) + "\n```"


def fake_sdk(monkeypatch, captured: dict, *, answer: str | None = None,
             gate: threading.Event | None = None) -> None:
    """Replace the SDK's query with one that records what it was handed."""
    import claude_agent_sdk

    text = answer if answer is not None else model_answer()
    captured["entered"] = threading.Event()

    async def query(*, prompt, options, **kw):
        del prompt, kw
        captured["options"] = options
        captured["env"] = dict(getattr(options, "env", None) or {})
        captured["environ"] = dict(os.environ)
        captured.setdefault("calls", 0)
        captured["calls"] += 1
        captured["entered"].set()
        if gate is not None:
            gate.wait(20)
        yield claude_agent_sdk.AssistantMessage(
            content=[claude_agent_sdk.TextBlock(text=text)],
            model="claude-opus-5")
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1,
            is_error=False, num_turns=1, session_id="test-session",
            usage={"input_tokens": 11, "output_tokens": 7},
            model_usage={"claude-opus-5-20260401": {"outputTokens": 7}},
            result="ok")

    monkeypatch.setattr(claude_agent_sdk, "query", query)


def assert_environ_was_clean(captured: dict) -> None:
    """The credential lived in the options and nowhere else."""
    environ = captured["environ"]
    for var in SCRUBBED:
        assert var not in environ, f"{var} was left in os.environ for the run"
    assert not [v for v in environ.values() if SENTINEL in str(v)]


# -- whose credential --------------------------------------------------------

def test_a_managers_brief_runs_on_their_own_credential(db, tmp_path,
                                                       monkeypatch):
    """The whole point. The credential reaches options.env, and nothing else
    about the process is touched."""
    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "the-operators-own-key")

    ctx = user_ctx(tmp_path, "alice")
    artefact = bi.generate_for_user(
        db, season="2026-27", ctx=ctx,
        credential_env={"ANTHROPIC_API_KEY": SENTINEL})

    assert captured["env"] == {"ANTHROPIC_API_KEY": SENTINEL}
    assert_environ_was_clean(captured)
    assert len(artefact["items"]) == 1
    assert bi.artefact_path(db, ctx).exists()


def test_a_subscription_token_rides_in_its_own_variable(db, tmp_path,
                                                        monkeypatch):
    """The two credential kinds go in different variables, and the pairing is
    the key store's, not this module's."""
    from fpl_edge.platform.auth import keys

    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured)

    bi.generate_for_user(db, season="2026-27",
                         ctx=user_ctx(tmp_path, "alice"),
                         credential_env=keys.credential_env(OAUTH_SENTINEL))
    assert captured["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": OAUTH_SENTINEL}


def test_the_scheduled_pass_hands_the_sdk_no_credential(monkeypatch):
    """No user, no session, no key: the empty env is the whole of the rule,
    and it is visible in the call rather than inferred."""
    captured: dict = {}
    fake_sdk(monkeypatch, captured, answer="hello")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "the-operators-own-token")

    answer = bi._run_model("say something")
    assert answer.text == "hello"
    assert captured["env"] == {}
    assert_environ_was_clean(captured)


def test_a_manager_with_no_credential_is_refused_before_a_model_is_called(
        db, tmp_path, monkeypatch):
    fake_panels(monkeypatch)

    def forbidden(prompt):
        raise AssertionError("a run with no credential must call no model")

    with pytest.raises(bi.BriefingIntelError, match="no fallback"):
        bi.generate_for_user(db, season="2026-27",
                             ctx=user_ctx(tmp_path, "alice"),
                             credential_env={}, run_model=forbidden)
    assert not bi.artefact_path(db, user_ctx(tmp_path, "alice")).exists()


def test_the_operator_keeps_the_machines_own_login(db, monkeypatch):
    """An empty credential is legal for exactly one person, and it is the
    same posture the scheduled pass runs on."""
    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured)

    artefact = bi.generate_for_user(db, season="2026-27", ctx=owner_context(),
                                    credential_env={})
    assert captured["env"] == {}
    assert len(artefact["items"]) == 1


def test_starting_a_run_with_no_credential_is_refused_before_a_thread_exists(
        db, tmp_path, monkeypatch):
    fake_panels(monkeypatch)
    ctx = user_ctx(tmp_path, "alice")
    with pytest.raises(bi.BriefingIntelError, match="no fallback"):
        bi.start_for_user(db, season="2026-27", ctx=ctx, credential_env={},
                          run_model=lambda p: model_answer())
    assert bi.run_state(ctx) == {"state": "idle"}


def test_a_per_user_run_without_a_context_is_refused(db, monkeypatch):
    fake_panels(monkeypatch)
    with pytest.raises(bi.BriefingIntelError, match="user context"):
        bi.generate_for_user(db, season="2026-27", ctx=None,
                             credential_env={"ANTHROPIC_API_KEY": SENTINEL},
                             run_model=lambda p: model_answer())


# -- one artefact each -------------------------------------------------------

def test_two_managers_write_separate_artefacts(db, tmp_path, monkeypatch):
    alice, bob = user_ctx(tmp_path, "alice"), user_ctx(tmp_path, "bob")

    fake_panels(monkeypatch, own_pct=55.0)
    bi.generate_for_user(db, season="2026-27", ctx=alice,
                         credential_env={"ANTHROPIC_API_KEY": SENTINEL},
                         run_model=lambda p: model_answer(55.0))
    fake_panels(monkeypatch, own_pct=22.0)
    bi.generate_for_user(db, season="2026-27", ctx=bob,
                         credential_env={"ANTHROPIC_API_KEY": SENTINEL},
                         run_model=lambda p: model_answer(22.0))

    a_path, b_path = bi.artefact_path(db, alice), bi.artefact_path(db, bob)
    assert a_path != b_path
    a_body = bi.briefing_response(db, ctx=alice)
    b_body = bi.briefing_response(db, ctx=bob)
    assert "55.0 own%" in a_body["items"][0]["why"]
    assert "22.0 own%" in b_body["items"][0]["why"]
    # Neither can name the other's file: the context refuses a path that
    # leaves its own directory.
    from fpl_edge.platform.users import UserIdInvalid

    with pytest.raises(UserIdInvalid):
        alice.path("..", "bob", "briefing_intel.json")


def test_a_manager_with_no_artefact_reads_an_honest_gap(db, tmp_path):
    body = bi.briefing_response(db, ctx=user_ctx(tmp_path, "alice"))
    assert body["empty"] is True
    assert body["task"] == "briefing_intel"
    assert body["run"] == {"state": "idle"}


# -- what it cost ------------------------------------------------------------

def test_the_artefact_records_what_the_run_cost(db, tmp_path, monkeypatch):
    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured)

    artefact = bi.generate_for_user(
        db, season="2026-27", ctx=user_ctx(tmp_path, "alice"),
        credential_env={"ANTHROPIC_API_KEY": SENTINEL})

    assert artefact["model"] == bi.MODEL
    assert artefact["model_reported"] == "claude-opus-5-20260401"
    assert artefact["tokens_in"] == 11
    assert artefact["tokens_out"] == 7
    # The same five facts the scheduled task writes to the ledger, in the
    # same vocabulary, so the two paths cannot word spend differently.
    ledger_shape = parse_spend(spend_note(
        CallUsage(model_reported="claude-opus-5-20260401", tokens_in=11,
                  tokens_out=7),
        calls=1, duration_s=artefact["duration_s"]))
    assert artefact["spend"] == ledger_shape


# -- the route ---------------------------------------------------------------

@pytest.fixture()
def app(monkeypatch, tmp_path, db):
    auth_fixtures.configure(monkeypatch, tmp_path)
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    return create_app(db=db)


def wait_for_run(client, state: str, *, timeout: float = 20.0) -> dict:
    """Poll the read route the way the page does, until the run lands."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get("/api/briefing").json().get("run") or {}
        if run.get("state") == state:
            return run
        time.sleep(0.02)
    raise AssertionError(
        f"the run never reached {state}: {client.get('/api/briefing').json()}")


def test_an_anonymous_caller_cannot_start_a_brief(app):
    r = TestClient(app).post("/api/briefing", json={})
    assert r.status_code == 401


def test_a_signed_in_manager_with_no_credential_gets_the_named_403(app):
    from fpl_edge.platform.auth import keys

    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    r = client.post("/api/briefing", json={}, headers=who.headers())
    assert r.status_code == 403
    assert r.json() == keys.NO_KEY_BODY


def test_a_signed_in_manager_runs_their_brief_on_their_own_credential(
        app, monkeypatch, db):
    from fpl_edge.platform.auth import keys

    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured)
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    keys.store(who.user_id, SENTINEL)

    r = client.post("/api/briefing", json={}, headers=who.headers())
    assert r.status_code == 202
    assert r.json()["started"] is True
    assert r.json()["run"]["state"] == "running"

    run = wait_for_run(client, "done")
    assert run["items_n"] == 1
    assert captured["env"] == {"ANTHROPIC_API_KEY": SENTINEL}
    assert_environ_was_clean(captured)

    body = client.get("/api/briefing").json()
    assert body.get("empty") is None
    assert body["spend"]["tokens_in"] == 11
    # Written under that manager's own directory, not beside the warehouse.
    assert not (db.parent / bi.ARTEFACT_NAME).exists()


def test_a_second_call_while_one_is_running_is_refused(app, monkeypatch):
    from fpl_edge.platform.auth import keys

    fake_panels(monkeypatch)
    captured: dict = {}
    gate = threading.Event()
    fake_sdk(monkeypatch, captured, gate=gate)
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    keys.store(who.user_id, SENTINEL)

    try:
        first = client.post("/api/briefing", json={}, headers=who.headers())
        assert first.status_code == 202
        assert captured["entered"].wait(20)
        second = client.post("/api/briefing", json={}, headers=who.headers())
        assert second.status_code == 409
        assert second.json()["error"] == "briefing_in_flight"
        assert second.json()["run"]["state"] == "running"
    finally:
        gate.set()
    wait_for_run(client, "done")
    assert captured["calls"] == 1


def test_a_failed_run_says_why_on_the_read_route(app, monkeypatch):
    from fpl_edge.platform.auth import keys

    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured, answer="no json in this answer")
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    keys.store(who.user_id, SENTINEL)

    assert client.post("/api/briefing", json={},
                       headers=who.headers()).status_code == 202
    run = wait_for_run(client, "error")
    assert "parse" in run["error"]
    assert client.get("/api/briefing").json()["empty"] is True


def test_the_kill_switch_refuses_the_request_path(app, monkeypatch):
    from fpl_edge.platform.auth import keys

    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "1")
    client = TestClient(app)
    who = auth_fixtures.sign_in(client)
    keys.store(who.user_id, SENTINEL)

    r = client.post("/api/briefing", json={}, headers=who.headers())
    assert r.status_code == 503
    assert r.json()["error"] == "network_disabled"
    assert bi.run_state(owner_context())["state"] == "idle"


def test_the_owners_own_deployment_needs_no_key(monkeypatch, tmp_path, db):
    """An unauthenticated caller on the owner's machine is the operator, and
    the brief runs on the machine's own login with no key stored anywhere."""
    auth_fixtures.configure(monkeypatch, tmp_path, anon_is_owner=True)
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    fake_panels(monkeypatch)
    captured: dict = {}
    fake_sdk(monkeypatch, captured)

    client = TestClient(create_app(db=db))
    assert client.post("/api/briefing", json={}).status_code == 202
    wait_for_run(client, "done")
    assert captured["env"] == {}
