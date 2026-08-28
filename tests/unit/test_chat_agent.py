"""The chat agent loop, exercised offline against a FAKE claude CLI.

The real CLI is never invoked here (it would cost a Max-plan turn and need a
login); every test drives ``ChatAgent`` with a shell script that speaks the
CLI's documented stream-json format. What is pinned:

- persist-then-broadcast: every parsed event lands in events.jsonl with a
  monotonic seq, and replay-after-seq returns exactly the suffix;
- single-flight: a second message during a turn is a 409, not a second CLI;
- the timeout path kills a stuck CLI and leaves an honest error event;
- a missing CLI is an error event with remediation, never a crash;
- stream-json parsing against a canned transcript;
- the asset route serves only uuid-hex ids -- no traversal.
"""

from __future__ import annotations

import itertools
import json
import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fpl_edge.platform.chat_agent import (
    ChatAgent,
    TurnInFlight,
    UnknownConversation,
    parse_stream_json_line,
)

# A canned transcript in the CLI's documented stream-json shapes: init,
# partial-message deltas, an assistant text+tool_use message, the tool_result
# echoed as a user message, the final text, and the terminal result.
TRANSCRIPT = [
    {"type": "system", "subtype": "init", "session_id": "sess-abc123",
     "model": "claude-test", "tools": ["mcp__fpl-server__query"]},
    {"type": "stream_event", "event": {"type": "content_block_delta",
     "delta": {"type": "text_delta", "text": "Salah "}}},
    {"type": "stream_event", "event": {"type": "content_block_delta",
     "delta": {"type": "text_delta", "text": "leads."}}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Salah leads."},
        {"type": "tool_use", "id": "tu_1", "name": "mcp__fpl-server__query",
         "input": {"sql": "SELECT code FROM sem_players(now()) LIMIT 5"}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_1",
         "content": [{"type": "text", "text": "5 rows: 118748, ..."}]},
    ]}},
    {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Final answer with [chart:deadbeef1234] embedded."},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "total_cost_usd": 0.0123, "duration_ms": 4200, "num_turns": 2,
     "session_id": "sess-abc123", "result": "Final answer."},
]


def _fake_cli(tmp_path: Path, body: str, name: str = "claude") -> str:
    script = tmp_path / name
    script.write_text("#!/bin/bash\n" + body + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def _transcript_cli(tmp_path: Path) -> str:
    fixture = tmp_path / "transcript.jsonl"
    fixture.write_text("\n".join(json.dumps(m) for m in TRANSCRIPT) + "\n")
    return _fake_cli(tmp_path, f'cat "{fixture}"')


def _agent(tmp_path: Path, cli: str, **kw) -> ChatAgent:
    kw.setdefault("briefing_fn", lambda: "test briefing")
    # Point MCP enumeration at nothing so no server is ever spawned in tests.
    kw.setdefault("mcp_python", "/nonexistent/python")
    kw.setdefault("mcp_main", tmp_path / "no_main.py")
    return ChatAgent(root=tmp_path / "chat", claude_bin=cli, **kw)


def _wait_done(agent: ChatAgent, conv_id: str, timeout: float = 10.0,
               *, turns: int = 1) -> list[dict]:
    """Block until ``turns`` terminal events exist on this conversation.

    ``turns``, not "any terminal event", because ``events()`` accumulates across
    turns: on a second turn the list already holds the first turn's ``done``,
    so an any() check returns instantly and waits for nothing. That is not
    hypothetical -- it made
    ``test_second_turn_resumes_with_the_stored_session_id`` pass only when the
    50ms bookkeeping sleep happened to outrun the CLI subprocess, and it failed
    under full-suite load with an IndexError on the argv log's missing second
    line. A helper that waits for an event that has already happened is
    indistinguishable from one that works.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = agent.events(conv_id)
        if sum(e["type"] in ("done", "error") for e in events) >= turns:
            # let the runner thread finish its meta bookkeeping
            time.sleep(0.05)
            return agent.events(conv_id)
        time.sleep(0.05)
    raise AssertionError(
        f"turn {turns} never finished; events: {agent.events(conv_id)}")


# -- persistence + replay ----------------------------------------------------


def test_turn_persists_events_then_meta_carries_the_session_id(tmp_path):
    agent = _agent(tmp_path, _transcript_cli(tmp_path))
    conv = agent.create_conversation()["conv_id"]
    started = agent.start_turn(conv, "who leads xg?")
    assert started["started"] and started["seq"] == 0

    events = _wait_done(agent, conv)
    types = [e["type"] for e in events]
    assert types == ["user", "init", "delta", "delta", "text",
                     "tool_use", "tool_result", "text", "done"]
    # seq is monotonic from 0 and survives on disk
    assert [e["seq"] for e in events] == list(range(len(events)))
    on_disk = (agent.root / conv / "events.jsonl").read_text().strip().split("\n")
    assert len(on_disk) == len(events)
    # the tool trace is compact, not a dump
    tu = next(e for e in events if e["type"] == "tool_use")
    assert tu["payload"]["name"] == "mcp__fpl-server__query"
    assert "SELECT" in tu["payload"]["input_preview"]
    # multi-turn resume: the stream's session id lands in meta
    assert agent.meta(conv)["claude_session_id"] == "sess-abc123"
    done = events[-1]["payload"]
    assert done["cost_usd"] == 0.0123 and done["duration_ms"] == 4200


def test_replay_after_seq_returns_exactly_the_suffix(tmp_path):
    agent = _agent(tmp_path, _transcript_cli(tmp_path))
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    n = len(events)
    assert [e["seq"] for e in agent.events(conv, after=3)] == list(range(4, n))
    assert agent.events(conv, after=n - 1) == []
    assert agent.events(conv, after=-1) == events


def test_subscribe_replays_persisted_events_before_live_ones(tmp_path):
    agent = _agent(tmp_path, _transcript_cli(tmp_path))
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    # a late subscriber (the reload case) replays the whole transcript
    frames = list(itertools.islice(agent.subscribe(conv, after=2,
                                                   heartbeat_s=0.1),
                                   len(events) - 3))
    assert [json.loads(f["data"])["seq"] for f in frames] == \
        [e["seq"] for e in events[3:]]
    assert frames[0]["event"] == events[3]["type"]


def test_second_turn_resumes_with_the_stored_session_id(tmp_path):
    # The fake CLI records its argv; turn 2 must carry --resume sess-abc123.
    argv_log = tmp_path / "argv.log"
    fixture = tmp_path / "transcript.jsonl"
    fixture.write_text("\n".join(json.dumps(m) for m in TRANSCRIPT) + "\n")
    # one log line per invocation even though the system prompt is multiline
    # The sleep is load-bearing, not padding. Without it this test passed 40/40
    # with a broken _wait_done that returned before turn 2 had run at all -- it
    # was measuring whether the subprocess beat a 50ms sleep, and under
    # full-suite load it sometimes didn't (IndexError on lines[1], twice in four
    # full runs). With it, the assertion below fails deterministically if the
    # helper stops waiting. Do not delete it to save 0.4s.
    cli = _fake_cli(
        tmp_path,
        'sleep 0.4\n'
        f'printf \'%s\' "$*" | tr \'\\n\' \' \' >> "{argv_log}"\n'
        f'printf \'\\n\' >> "{argv_log}"\n'
        f'cat "{fixture}"')
    agent = _agent(tmp_path, cli)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "first")
    _wait_done(agent, conv)
    agent.start_turn(conv, "second")
    _wait_done(agent, conv, turns=2)
    lines = argv_log.read_text().strip().split("\n")
    assert len(lines) == 2, (
        f"expected one argv line per invocation, got {lines!r} -- the second "
        "turn had not run when the log was read")
    assert "--resume" not in lines[0]
    assert "--resume sess-abc123" in lines[1]
    # and the toolbelt is offered while Bash and file tools are denied
    assert "--allowedTools" in lines[0] and "mcp__fpl-server__" in lines[0]
    assert "--disallowedTools" in lines[0] and "Bash" in lines[0]


# -- single flight -----------------------------------------------------------


def test_single_flight_second_message_is_refused(tmp_path):
    agent = _agent(tmp_path, _fake_cli(tmp_path, "sleep 5"))
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "slow one")
    with pytest.raises(TurnInFlight):
        agent.start_turn(conv, "impatient second")
    assert agent.running(conv)["running"] is True
    agent.stop(conv)
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    assert "stopped by user" in events[-1]["payload"]["message"]


# -- failure honesty ---------------------------------------------------------


def test_timeout_kills_the_cli_and_leaves_an_honest_error(tmp_path):
    agent = _agent(tmp_path, _fake_cli(tmp_path, "sleep 30"), timeout_s=0.5)
    conv = agent.create_conversation()["conv_id"]
    t0 = time.time()
    agent.start_turn(conv, "hang forever")
    events = _wait_done(agent, conv)
    assert time.time() - t0 < 10, "the kill must not wait out the sleep"
    assert events[-1]["type"] == "error"
    assert "timed out" in events[-1]["payload"]["message"]
    # the conversation stays usable: a fresh turn starts cleanly
    assert agent.running(conv)["running"] is False


def test_missing_cli_is_an_error_event_with_remediation(tmp_path):
    agent = _agent(tmp_path, "/nonexistent/claude")
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "hello?")
    events = _wait_done(agent, conv)
    assert [e["type"] for e in events] == ["user", "error"]
    msg = events[-1]["payload"]["message"]
    assert "claude CLI not found" in msg and "claude login" in msg


def test_revoked_oauth_result_names_the_fix(tmp_path):
    # Seen live: the CLI exits 0 but its result event carries the 401. The
    # transcript must tell the operator what to run, not just echo the API.
    result = {"type": "result", "subtype": "success", "is_error": True,
              "total_cost_usd": 0, "duration_ms": 5, "num_turns": 1,
              "session_id": "s-auth",
              "result": ("Failed to authenticate. API Error: 401 "
                         "OAuth access token has been revoked.")}
    fixture = tmp_path / "auth.jsonl"
    fixture.write_text(json.dumps(result) + "\n")
    agent = _agent(tmp_path, _fake_cli(tmp_path, f'cat "{fixture}"'))
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "hi")
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    assert "claude login" in events[-1]["payload"]["message"]


def test_cli_login_failure_names_the_fix(tmp_path):
    cli = _fake_cli(tmp_path, 'echo "Invalid API key. Please run /login" >&2\nexit 1')
    agent = _agent(tmp_path, cli)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "hi")
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    assert "claude login" in events[-1]["payload"]["message"]


def test_lost_session_cache_clears_the_stale_id(tmp_path):
    cli = _fake_cli(
        tmp_path,
        'echo "No conversation found with session ID: sess-gone" >&2\nexit 1')
    agent = _agent(tmp_path, cli)
    conv = agent.create_conversation()["conv_id"]
    (agent.root / conv / "meta.json").write_text(json.dumps(
        {"conv_id": conv, "claude_session_id": "sess-gone"}))
    agent.start_turn(conv, "resume me")
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    assert agent.meta(conv)["claude_session_id"] is None


def test_unknown_conversation_raises(tmp_path):
    agent = _agent(tmp_path, "/nonexistent/claude")
    with pytest.raises(UnknownConversation):
        agent.start_turn("f" * 32, "hello")
    with pytest.raises(UnknownConversation):
        agent.events("../../../etc/passwd")


# -- stream-json parsing -----------------------------------------------------


def test_parse_stream_json_against_the_canned_transcript():
    parsed = [ev for line in TRANSCRIPT
              for ev in parse_stream_json_line(json.dumps(line))]
    assert [t for t, _ in parsed] == [
        "init", "delta", "delta", "text", "tool_use", "tool_result",
        "text", "done",
    ]
    by_type = dict(parsed)  # last of each type is fine for these asserts
    assert by_type["init"]["session_id"] == "sess-abc123"
    assert by_type["tool_result"]["tool_use_id"] == "tu_1"
    assert by_type["tool_result"]["is_error"] is False
    assert "[chart:deadbeef1234]" in by_type["text"]["text"]


def test_parse_tolerates_noise_and_reports_failures():
    assert parse_stream_json_line("") == []
    assert parse_stream_json_line("Starting FPL MCP server...") == []
    assert parse_stream_json_line('{"type": "unknown_future_thing"}') == []
    [(t, p)] = parse_stream_json_line(json.dumps(
        {"type": "result", "subtype": "error_during_execution",
         "is_error": True, "session_id": "s", "total_cost_usd": 0.5}))
    assert t == "error" and "error_during_execution" in p["message"]


def test_previews_are_bounded():
    big = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t", "name": "query",
         "input": {"sql": "SELECT " + "x, " * 5000}}]}}
    [(_, payload)] = parse_stream_json_line(json.dumps(big))
    assert len(payload["input_preview"]) < 400
    assert "chars)" in payload["input_preview"]


# -- HTTP surface ------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    from fpl_edge.platform.app import create_app

    app = create_app(tmp_path / "fpl.duckdb", chat_root=tmp_path / "chat")
    agent = app.state.chat_agent
    agent._claude_bin = _transcript_cli(tmp_path)
    agent._briefing_fn = lambda: "test briefing"
    agent.mcp_python = "/nonexistent/python"
    agent.mcp_main = tmp_path / "no_main.py"
    agent.heartbeat_s = 0.2   # closing a stream must never block on a get()
    return TestClient(app)


def test_conversation_routes_round_trip(client, tmp_path):
    conv = client.post("/api/conversations").json()["conv_id"]
    listed = client.get("/api/conversations").json()["conversations"]
    assert conv in {m["conv_id"] for m in listed}

    r = client.post(f"/api/conversations/{conv}/chat", json={"text": "hi"})
    assert r.status_code == 202 and r.json()["started"] is True

    deadline = time.time() + 10
    while time.time() < deadline:
        page = client.get(f"/api/conversations/{conv}/events?after=-1").json()
        if any(e["type"] == "done" for e in page["events"]):
            break
        time.sleep(0.05)
    else:
        raise AssertionError(f"turn never finished: {page}")
    assert page["running"] is False
    assert page["meta"]["claude_session_id"] == "sess-abc123"
    # non-SSE replay honours after=
    tail = client.get(f"/api/conversations/{conv}/events?after=5").json()["events"]
    assert all(e["seq"] > 5 for e in tail) and tail


def test_second_message_mid_turn_is_a_409(client, tmp_path):
    client.app.state.chat_agent._claude_bin = _fake_cli(
        tmp_path, "sleep 5", name="claude-slow")
    conv = client.post("/api/conversations").json()["conv_id"]
    assert client.post(f"/api/conversations/{conv}/chat",
                       json={"text": "one"}).status_code == 202
    r = client.post(f"/api/conversations/{conv}/chat", json={"text": "two"})
    assert r.status_code == 409
    assert r.json()["running"] is True
    assert client.post(f"/api/conversations/{conv}/stop").json()["stopped"] is True


def test_unknown_conversation_is_a_404(client):
    assert client.get(f"/api/conversations/{'a' * 32}/events").status_code == 404
    assert client.post(f"/api/conversations/{'a' * 32}/chat",
                       json={"text": "x"}).status_code == 404


def test_asset_route_serves_hex_ids_only(client, tmp_path):
    assets = tmp_path / "chat" / "assets"
    (assets / "deadbeefcafe.png").write_bytes(b"\x89PNG fake")
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"private")

    ok = client.get("/api/chat/assets/deadbeefcafe.png")
    assert ok.status_code == 200 and ok.content.startswith(b"\x89PNG")
    # traversal shapes: rejected by the id regex or unroutable outright
    for bad in ("../secret", "..%2Fsecret", "zz$(reboot)zz", "deadbeef' OR 1"):
        assert client.get(f"/api/chat/assets/{bad}.png").status_code == 404
    # and the agent-level check agrees
    agent = client.app.state.chat_agent
    assert agent.asset_path("../secret") is None
    assert agent.asset_path("deadbeefcafe") is not None


def test_sse_route_replays_the_transcript(client):
    """TestClient buffers the whole ASGI response, so the endless live
    stream cannot be exercised here (the pane and curl do that); ``once=1``
    pins the SSE framing and the replay-from-seq contract. Live follow is
    covered at the agent level in test_subscribe_replays_persisted_events."""
    conv = client.post("/api/conversations").json()["conv_id"]
    client.post(f"/api/conversations/{conv}/chat", json={"text": "hi"})
    deadline = time.time() + 10
    while time.time() < deadline:
        events = client.get(f"/api/conversations/{conv}/events").json()["events"]
        if any(e["type"] == "done" for e in events):
            break
        time.sleep(0.05)
    r = client.get(f"/api/conversations/{conv}/stream?after=-1&once=1")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    body = r.text
    assert "event: user" in body and "event: done" in body
    assert '"seq": 0' in body
    # replay honours after= on the SSE surface too
    tail = client.get(f"/api/conversations/{conv}/stream?after=7&once=1").text
    assert '"seq": 7' not in tail and '"seq": 8' in tail


def test_steady_activity_outlives_the_idle_window(tmp_path):
    """The 300s wall-clock kill this replaced cut down a healthy 20-tool-call
    analysis mid-thought. The timeout is IDLE-based now: a turn that keeps
    producing output must survive far past the idle window; only silence dies.
    """
    body = (
        'for i in $(seq 1 14); do\n'
        '  echo \'{"type":"assistant","message":{"content":[{"type":"text",'
        '"text":"tick"}],"model":"m"},"session_id":"s1"}\'\n'
        '  sleep 0.2\n'
        'done\n'
        'echo \'{"type":"result","subtype":"success","is_error":false,'
        '"duration_ms":1,"num_turns":1,"session_id":"s1","total_cost_usd":0}\'\n'
    )
    # idle window 0.8s < total runtime ~2.8s: wall-clock logic would kill it,
    # idle logic must not — the script never goes quiet for 0.8s.
    agent = _agent(tmp_path, _fake_cli(tmp_path, body), timeout_s=0.8)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "keep talking")
    events = _wait_done(agent, conv)
    types = [e["type"] for e in events]
    assert "done" in types, f"a steadily-active turn was killed: {types[-3:]}"
    assert not any(e["type"] == "error" for e in events)
