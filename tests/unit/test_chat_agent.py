"""The chat agent loop, exercised offline against a FAKE SDK transport.

The real CLI is never spawned here (it would cost a Max-plan turn and need a
login). The seam is the Agent SDK's own ``Transport`` interface: a fake
transport replays the SAME canned stream-json dicts the CLI would emit, so
every test exercises the REAL SDK message parsing plus this module's
translation and lifecycle -- only the subprocess is fake. What is pinned:

- persist-then-broadcast: every event lands in events.jsonl with a monotonic
  seq, and replay-after-seq returns exactly the suffix;
- turn 2 carries ``resume`` with the stored session id -- asserted on the
  actual ``ClaudeAgentOptions`` the engine built, captured at the transport
  seam (the old suite asserted CLI argv; options are the argv now);
- the tool posture: no built-ins, the toolbelt allowlisted by qualified name,
  Opus, the briefing appended to the preset prompt;
- single-flight: a second message during a turn is refused, not queued;
- the idle timeout interrupts a silent turn and leaves an honest error;
- a missing CLI / dead process / lost session cache each leave their
  remediation in the transcript, never a crash;
- SDK-message translation payloads are byte-identical to the old stream-json
  parser's (the UI and on-disk transcripts must not notice the engine swap);
- the asset route serves only uuid-hex ids -- no traversal.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from fastapi.testclient import TestClient

from fpl_edge.platform.chat_agent import (
    ChatAgent,
    TurnInFlight,
    UnknownConversation,
    events_from_message,
)

# A canned transcript in the CLI's documented stream-json shapes: init,
# partial-message deltas, an assistant text+tool_use message, the tool_result
# echoed as a user message, the final text, and the terminal result. These are
# the RAW dicts a transport yields; the SDK's own parser turns them into typed
# messages, so this fixture exercises the true wire format end to end.
TRANSCRIPT = [
    {"type": "system", "subtype": "init", "session_id": "sess-abc123",
     "model": "claude-test", "tools": ["mcp__fpl-server__query"]},
    {"type": "stream_event", "uuid": "u1", "session_id": "sess-abc123",
     "event": {"type": "content_block_delta",
               "delta": {"type": "text_delta", "text": "Salah "}}},
    {"type": "stream_event", "uuid": "u2", "session_id": "sess-abc123",
     "event": {"type": "content_block_delta",
               "delta": {"type": "text_delta", "text": "leads."}}},
    {"type": "assistant", "message": {"model": "claude-test", "content": [
        {"type": "text", "text": "Salah leads."},
        {"type": "tool_use", "id": "tu_1", "name": "mcp__fpl-server__query",
         "input": {"sql": "SELECT code FROM sem_players(now()) LIMIT 5"}},
    ]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "tu_1",
         "content": [{"type": "text", "text": "5 rows: 118748, ..."}]},
    ]}},
    {"type": "assistant", "message": {"model": "claude-test", "content": [
        {"type": "text",
         "text": "Final answer with [chart:deadbeef1234] embedded."},
    ]}},
    {"type": "result", "subtype": "success", "is_error": False,
     "duration_ms": 4200, "duration_api_ms": 4000, "num_turns": 2,
     "total_cost_usd": 0.0123, "session_id": "sess-abc123",
     "result": "Final answer."},
]


class FakeTransport:
    """Speaks the SDK's Transport interface from a canned message list.

    Control requests (initialize, interrupt) are acked generically -- the
    fake is a compliant peer, not a mock with expectations. The canned
    transcript is released only after the user message arrives on write(),
    mirroring the real CLI's request/response order. ``hold_open`` keeps the
    stream silent after the transcript (or instead of it) so timeout and
    stop paths have something to interrupt; an interrupt control request
    releases the hold, mirroring the CLI honouring an interrupt.
    """

    def __init__(self, messages: list[dict] | None = None,
                 *, hold_open: bool = False):
        self.messages = list(messages or [])
        self.hold_open = hold_open
        self.wrote: list[dict] = []
        self.interrupted = False
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._released = False

    async def connect(self) -> None:
        pass

    def is_ready(self) -> bool:
        return True

    async def write(self, data: str) -> None:
        for line in data.splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            self.wrote.append(obj)
            if obj.get("type") == "control_request":
                subtype = (obj.get("request") or {}).get("subtype")
                # Ack FIRST, end-of-stream after: the SDK awaits the ack on
                # the same read loop, so a None queued ahead of it would end
                # the stream with the interrupt response forever unread and
                # client.interrupt() blocked on a reply that cannot come.
                await self._queue.put({
                    "type": "control_response",
                    "response": {"request_id": obj.get("request_id"),
                                 "subtype": "success", "response": {}},
                })
                if subtype == "interrupt":
                    self.interrupted = True
                    await self._queue.put(None)  # release any hold
            elif not self._released:
                self._released = True
                for msg in self.messages:
                    await self._queue.put(msg)
                if not self.hold_open:
                    await self._queue.put(None)

    async def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is None:
                if self.hold_open and not self.interrupted:
                    continue  # a stop released us; drain to the end marker
                return
            yield item

    async def end_input(self) -> None:
        pass

    async def close(self) -> None:
        await self._queue.put(None)


def _agent(tmp_path: Path, transcript: list[dict] | None = None,
           *, hold_open: bool = False, **kw) -> tuple[ChatAgent, list]:
    """An agent whose turns run against FakeTransport; returns (agent,
    captured_options) where captured_options grows one entry per turn."""
    kw.setdefault("briefing_fn", lambda: "test briefing")
    # Point MCP enumeration at nothing so no server is ever spawned in tests.
    kw.setdefault("mcp_python", "/nonexistent/python")
    kw.setdefault("mcp_main", tmp_path / "no_main.py")
    agent = ChatAgent(root=tmp_path / "chat", **kw)
    captured: list = []

    def factory(options):
        captured.append(options)
        return FakeTransport(TRANSCRIPT if transcript is None else transcript,
                             hold_open=hold_open)

    agent._transport_factory = factory
    return agent, captured


def _wait_done(agent: ChatAgent, conv_id: str, timeout: float = 10.0,
               *, turns: int = 1) -> list[dict]:
    """Block until ``turns`` terminal events exist on this conversation.

    ``turns``, not "any terminal event", because ``events()`` accumulates
    across turns: on a second turn the list already holds the first turn's
    ``done``, so an any() check returns instantly and waits for nothing.
    That bug shipped once; see the git history of this file.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = agent.events(conv_id)
        if sum(e["type"] in ("done", "error") for e in events) >= turns:
            time.sleep(0.05)  # let the runner thread finish meta bookkeeping
            return agent.events(conv_id)
        time.sleep(0.05)
    raise AssertionError(
        f"turn {turns} never finished; events: {agent.events(conv_id)}")


# -- persistence + replay ----------------------------------------------------


def test_turn_persists_events_then_meta_carries_the_session_id(tmp_path):
    agent, _ = _agent(tmp_path)
    conv = agent.create_conversation()["conv_id"]
    started = agent.start_turn(conv, "who leads xg?")
    assert started["started"] and started["seq"] == 0

    events = _wait_done(agent, conv)
    types = [e["type"] for e in events]
    assert types == ["user", "init", "delta", "delta", "text",
                     "tool_use", "tool_result", "text", "done"]
    # seq is monotonic from 0 and survives on disk
    assert [e["seq"] for e in events] == list(range(len(events)))
    on_disk = [json.loads(l) for l in
               (agent.root / conv / "events.jsonl").read_text().splitlines()]
    assert [e["seq"] for e in on_disk] == [e["seq"] for e in events]
    meta = agent.meta(conv)
    assert meta["claude_session_id"] == "sess-abc123"


def test_replay_after_seq_returns_exactly_the_suffix(tmp_path):
    agent, _ = _agent(tmp_path)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    tail = agent.events(conv, after=3)
    assert [e["seq"] for e in tail] == [e["seq"] for e in events if e["seq"] > 3]


def test_subscribe_replays_persisted_events_before_live_ones(tmp_path):
    agent, _ = _agent(tmp_path)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    _wait_done(agent, conv)
    agent.heartbeat_s = 0.05
    seen = []
    for ev in agent.subscribe(conv, after=-1):
        if ev.get("event") == "eot":
            break
        seen.append(ev)
        if len(seen) >= 9:
            break
    assert [json.loads(e["data"])["type"] for e in seen[:2]] == ["user", "init"]


# -- resume + options (the argv of the SDK engine) ---------------------------


def test_second_turn_resumes_with_the_stored_session_id(tmp_path):
    agent, captured = _agent(tmp_path)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "first")
    _wait_done(agent, conv)
    agent.start_turn(conv, "second")
    _wait_done(agent, conv, turns=2)

    assert len(captured) == 2, "one options object per turn"
    assert captured[0].resume is None
    assert captured[1].resume == "sess-abc123"


def test_options_pin_the_tool_posture_and_the_model(tmp_path):
    """The security-relevant argv of the old engine, as SDK options."""
    agent, captured = _agent(tmp_path)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    _wait_done(agent, conv)
    opts = captured[0]

    assert opts.tools == [], "no built-in tool may exist for this agent"
    assert "Bash" in opts.disallowed_tools and "Write" in opts.disallowed_tools
    assert opts.allowed_tools, "the toolbelt must be allowlisted"
    assert all(n.startswith("mcp__fpl-server__") for n in opts.allowed_tools)
    assert opts.model == "opus"
    assert opts.strict_mcp_config is True
    assert "fpl-server" in opts.mcp_servers
    assert opts.include_partial_messages is True
    prompt = opts.system_prompt
    assert prompt["preset"] == "claude_code"
    assert "test briefing" in prompt["append"]
    assert "analyst" in prompt["append"]


# -- single flight -----------------------------------------------------------


def test_single_flight_second_message_is_refused(tmp_path):
    agent, _ = _agent(tmp_path, transcript=[], hold_open=True)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "slow one")
    with pytest.raises(TurnInFlight):
        agent.start_turn(conv, "impatient second")
    assert agent.running(conv)["running"] is True
    agent.stop(conv)
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    assert "stopped by user" in events[-1]["payload"]["message"]


# -- timeouts + honest failure -----------------------------------------------


def test_timeout_interrupts_a_silent_turn_and_leaves_an_honest_error(tmp_path):
    agent, _ = _agent(tmp_path, transcript=[], hold_open=True, timeout_s=0.5)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv, timeout=15.0)
    assert events[-1]["type"] == "error"
    assert "timed out" in events[-1]["payload"]["message"]
    assert agent.running(conv)["running"] is False


def test_missing_cli_is_an_error_event_with_remediation(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path)
    agent._transport_factory = None  # force the real spawn path...

    async def boom(self, *a, **k):
        raise CLINotFoundError("Claude Code not found")

    # ...which is stubbed at the SDK client so no process is ever started.
    from claude_agent_sdk import ClaudeSDKClient
    monkeypatch.setattr(ClaudeSDKClient, "connect", boom)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    msg = events[-1]["payload"]["message"]
    assert "claude login" in msg and "Install Claude Code" in msg


def test_dead_cli_with_lost_session_clears_the_stale_id(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path)
    agent._transport_factory = None

    async def boom(self, *a, **k):
        raise ProcessError("exited 1", exit_code=1,
                           stderr="No conversation found with session ID sess-x")

    from claude_agent_sdk import ClaudeSDKClient
    monkeypatch.setattr(ClaudeSDKClient, "connect", boom)
    conv = agent.create_conversation()["conv_id"]
    # seed a stale session id the failure should clear
    agent._update_meta(agent._conv(conv), claude_session_id="sess-x")
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    msg = events[-1]["payload"]["message"]
    assert "session cache lost this conversation" in msg
    assert agent.meta(conv)["claude_session_id"] is None


def test_dead_cli_with_auth_failure_names_the_fix(tmp_path, monkeypatch):
    agent, _ = _agent(tmp_path)
    agent._transport_factory = None

    async def boom(self, *a, **k):
        raise ProcessError("exited 1", exit_code=1,
                           stderr="Please log in: OAuth token revoked")

    from claude_agent_sdk import ClaudeSDKClient
    monkeypatch.setattr(ClaudeSDKClient, "connect", boom)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    assert "claude login" in events[-1]["payload"]["message"]


def test_revoked_oauth_result_names_the_fix(tmp_path):
    """An error RESULT (the CLI answered, with a failure) mid-conversation."""
    transcript = TRANSCRIPT[:1] + [
        {"type": "result", "subtype": "error_during_execution",
         "is_error": True, "duration_ms": 10, "duration_api_ms": 5,
         "num_turns": 1, "session_id": "sess-abc123",
         "result": "OAuth token has been revoked"},
    ]
    agent, _ = _agent(tmp_path, transcript=transcript)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv)
    assert events[-1]["type"] == "error"
    assert "claude login" in events[-1]["payload"]["message"]


def test_unknown_conversation_raises(tmp_path):
    agent, _ = _agent(tmp_path)
    with pytest.raises(UnknownConversation):
        agent.events("nope")


# -- SDK-message translation (payloads pinned against the old parser) --------


def test_translation_matches_the_old_parser_payloads():
    """The full canned transcript, through the REAL SDK parser via the fake
    transport, produced exactly the event stream the stream-json parser did
    (asserted in the persistence test above). Here: the pure translation of
    hand-built typed messages pins each payload shape individually."""
    ev = events_from_message(AssistantMessage(model="m", content=[
        TextBlock(text="Salah leads."),
        ToolUseBlock(id="tu_1", name="mcp__fpl-server__query",
                     input={"sql": "SELECT 1"}),
    ]))
    assert ev == [
        ("text", {"text": "Salah leads."}),
        ("tool_use", {"id": "tu_1", "name": "mcp__fpl-server__query",
                      "input_preview": '{"sql": "SELECT 1"}'}),
    ]

    ev = events_from_message(UserMessage(content=[
        ToolResultBlock(tool_use_id="tu_1",
                        content=[{"type": "text", "text": "5 rows"}],
                        is_error=None),
    ]))
    assert ev == [("tool_result", {"tool_use_id": "tu_1", "is_error": False,
                                   "preview": "5 rows"})]

    ev = events_from_message(ResultMessage(
        subtype="success", duration_ms=4200, duration_api_ms=4000,
        is_error=False, num_turns=2, session_id="s", total_cost_usd=0.01))
    assert ev == [("done", {"session_id": "s", "cost_usd": 0.01,
                            "duration_ms": 4200, "num_turns": 2})]


def test_previews_are_bounded():
    big = {"sql": "x" * 5000}
    ev = events_from_message(AssistantMessage(model="m", content=[
        ToolUseBlock(id="t", name="query", input=big)]))
    preview = ev[0][1]["input_preview"]
    assert len(preview) < 400 and "chars)" in preview

    ev = events_from_message(UserMessage(content=[
        ToolResultBlock(tool_use_id="t", content="y" * 5000)]))
    assert len(ev[0][1]["preview"]) < 600


def test_unknown_messages_produce_nothing():
    assert events_from_message(object()) == []
    assert events_from_message(AssistantMessage(model="m", content=[])) == []


# -- HTTP routes -------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    from fpl_edge.platform.app import create_app
    app = create_app(chat_root=tmp_path / "chat")
    agent = app.state.chat_agent
    agent._briefing_fn = lambda: "test briefing"
    agent.mcp_python = "/nonexistent/python"
    agent.mcp_main = tmp_path / "no_main.py"
    agent._tools_cache_ready = False
    agent._transport_factory = lambda options: FakeTransport(TRANSCRIPT)
    with TestClient(app) as tc:
        tc._agent = agent
        yield tc


def test_conversation_routes_round_trip(client, tmp_path):
    conv = client.post("/api/conversations").json()["conv_id"]
    r = client.post(f"/api/conversations/{conv}/chat",
                    json={"text": "who leads xg?"})
    assert r.status_code == 202
    _wait_done(client._agent, conv)
    events = client.get(f"/api/conversations/{conv}/events").json()["events"]
    assert events[-1]["type"] == "done"
    listing = client.get("/api/conversations").json()["conversations"]
    assert any(c["conv_id"] == conv for c in listing)


def test_second_message_mid_turn_is_a_409(client, tmp_path):
    client._agent._transport_factory = \
        lambda options: FakeTransport([], hold_open=True)
    conv = client.post("/api/conversations").json()["conv_id"]
    client.post(f"/api/conversations/{conv}/chat", json={"text": "a"})
    r = client.post(f"/api/conversations/{conv}/chat",
                    json={"text": "b"})
    assert r.status_code == 409
    client.post(f"/api/conversations/{conv}/stop")
    _wait_done(client._agent, conv)


def test_unknown_conversation_is_a_404(client):
    r = client.get("/api/conversations/nope/events")
    assert r.status_code == 404


def test_asset_route_serves_hex_ids_only(client, tmp_path):
    agent = client._agent
    (agent.assets_dir / "deadbeefdeadbeef.png").write_bytes(b"\x89PNG ok")
    ok = client.get("/api/chat/assets/deadbeefdeadbeef.png")
    assert ok.status_code == 200
    for bad in ("../meta.json", "..%2fmeta.json", "notes.txt", "a.png"):
        r = client.get(f"/api/chat/assets/{bad}")
        assert r.status_code in (400, 404), bad


def test_sse_route_replays_the_transcript(client):
    conv = client.post("/api/conversations").json()["conv_id"]
    client.post(f"/api/conversations/{conv}/chat", json={"text": "q"})
    _wait_done(client._agent, conv)
    client._agent.heartbeat_s = 0.05
    with client.stream(
            "GET", f"/api/conversations/{conv}/stream?once=1") as resp:
        body = ""
        for chunk in resp.iter_text():
            body += chunk
            if '"done"' in body:
                break
    assert '"user"' in body and '"done"' in body


# -- liveness under steady activity ------------------------------------------


def test_steady_activity_outlives_the_idle_window(tmp_path):
    """A turn that keeps producing output is never idle-killed, even when
    each gap alone is a large fraction of the window."""

    class DrippingTransport(FakeTransport):
        """Releases the transcript slowly INTO THE QUEUE -- overriding
        read_messages() itself would bypass the control-ack machinery and
        deadlock connect() on an initialize response that never comes."""

        async def write(self, data: str) -> None:
            starting = not self._released
            await super().write(data)
            if starting and self._released:
                async def drip():
                    # the parent queued everything at once; drain and re-drip
                    while not self._queue.empty():
                        self._queue.get_nowait()
                    await self._queue.put(TRANSCRIPT[0])
                    for msg in TRANSCRIPT[1:]:
                        await asyncio.sleep(0.24)
                        await self._queue.put(msg)
                    await self._queue.put(None)
                asyncio.ensure_future(drip())

    agent, _ = _agent(tmp_path, timeout_s=0.6)
    agent._transport_factory = lambda options: DrippingTransport(TRANSCRIPT)
    conv = agent.create_conversation()["conv_id"]
    agent.start_turn(conv, "q")
    events = _wait_done(agent, conv, timeout=20.0)
    assert events[-1]["type"] == "done", [e["type"] for e in events]
