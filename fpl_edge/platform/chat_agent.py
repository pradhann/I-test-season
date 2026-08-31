"""The chat agent loop: conversations driven through the Claude Agent SDK.

Argus discipline (docs/platform/argus_architecture.md §1.3), transposed --
and, since the CHAT_ARCHITECTURE spec, running on ``claude-agent-sdk``
instead of a hand-parsed ``claude -p`` subprocess. The SDK spawns and owns
the same Max-plan CLI (its login is still the auth; this server still holds
no API key), but gives this module a control channel: a graceful
``interrupt()`` instead of a process-group SIGKILL, typed messages instead
of stream-json line parsing, and options instead of argv assembly.

- **A turn is a server-side job.** ``start_turn`` runs the SDK session
  detached from any HTTP request and reads its message stream to completion
  whether or not a browser is watching.
- **Persist THEN broadcast.** Every event is appended to the conversation's
  ``events.jsonl`` (flushed) before any live subscriber sees it, so a reload
  can replay from disk and re-attach mid-turn without a gap, and the
  transcript survives a server restart.
- **A dead turn leaves an honest error.** Timeout, missing CLI, failed login,
  lost session cache -- each lands in the transcript as a ``type=error``
  event with concrete remediation, and the conversation stays usable.

The server holds NO API key: the CLI's own login is the auth (DESIGN §2
item 6). This module never opens the warehouse for writing; the agent reaches
data only through the FPL MCP server's tools, allowlisted by name.

Storage layout (append-only, no warehouse writes)::

    <root>/<conv_id>/events.jsonl   {seq, ts, type, payload} per line
    <root>/<conv_id>/meta.json      {conv_id, claude_session_id, title, ...}
    <root>/assets/<id>.png          charts written by the MCP make_chart tool
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

UTC = dt.UTC

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Default conversation store. Deliberately under data/warehouse so backups
#: that cover the warehouse cover the transcripts, but it is NOT the DuckDB
#: file and nothing here takes the write lock.
CHAT_ROOT = _REPO_ROOT / "data" / "warehouse" / "chat"

#: Where the MCP toolbelt lives and the interpreter that runs it. Both are now
#: in THIS repo: fpl_mcp/ is a sibling of fpl_edge/ and shares its environment,
#: so the toolbelt cannot drift from the engine it serves. It is spawned as
#: ``python -m fpl_mcp`` from the repo root -- running the module file by path
#: would put fpl_mcp/ on sys.path instead of the root, and `import fpl_mcp`
#: would fail.
_VENV_PYTHON = _REPO_ROOT / ".venv" / "bin" / "python"
MCP_PYTHON = str(_VENV_PYTHON if _VENV_PYTHON.exists() else Path(sys.executable))
MCP_MAIN = _REPO_ROOT / "fpl_mcp" / "__main__.py"


def mcp_command(python: str, main: Path) -> list[str]:
    """The argv that starts the toolbelt.

    ``main`` names the module file so callers can probe that it exists; what is
    actually executed is the package, from the repo root.
    """
    return [python, "-m", main.parent.name]

#: The tools the agent is ALLOWED to want. What it actually gets is this list
#: intersected with what the MCP server registers at runtime -- a tool the
#: toolbelt has not shipped yet simply is not offered, and nothing outside
#: this list (no Bash, no file tools) is ever offered regardless.
INTENT_TOOLS: tuple[str, ...] = (
    "query",
    "python_viz",
    "suggest_transfers",
    "save_analysis",
    "run_analysis",
    "list_analyses",
    "watchlist_add",
    "watchlist_list",
    "watchlist_remove",
    "get_manager_by_name",
    "player_projections",
    "projection_disagreement",
    "xpts_aggregate",
    "player_form",
    "fixture_difficulty",
    "ownership_eo",
    "summarise_fpl_youtube",
    "fetch_youtube_transcript",
    "submit_idea",
    "fpl_player_claims",
    "fpl_creator_consensus",
    "fpl_creator_track_record",
    "get_team_picks",
    "get_manager_history",
    "get_player_history",
    "get_team_summary",
    "get_expert_teams_summary",
    "get_expert_transfers",
    "player_dossier",
    "player_intel",
)

#: Built-in CLI tools the agent must never use: answers come from the
#: toolbelt, not from reading this repo or running shells.
DISALLOWED_TOOLS: tuple[str, ...] = (
    "Bash", "Read", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Glob", "Grep", "WebFetch", "WebSearch", "Task", "TodoWrite",
    "BashOutput", "KillShell",
)

CHARTER = (
    "You are the FPL edge engine's analyst. Answer with data from your tools "
    "only. For any chart, use python_viz (real matplotlib under the house "
    "theme) and embed each [chart:<id>] marker it returns on its own line "
    "exactly where the figure belongs. Be direct; state as-of instants.\n"
    "When an answer is a REPORT -- a deadline brief, a multi-player "
    "comparison, a transfer plan, anything the owner would keep or share -- "
    "wrap that report in a fenced block starting ```doc and ending ``` : "
    "inside it, a # title, markdown sections, tables and [chart:<id>] "
    "markers. The pane renders it as a document with an outline and an "
    "export button. Conversational answers stay plain prose; never wrap "
    "a two-sentence answer in a doc block."
)

#: Tool families (CHAT_ARCHITECTURE §3.2): each maps to a one-line useWhen the
#: system prompt carries, DERIVED from what is actually registered -- a family
#: none of whose tools shipped simply does not appear in the prompt. Argus's
#: rule: guidance only for capabilities that exist.
TOOL_FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "squad": ("the owner's team, history and league context",
              ("get_team_picks", "get_team_summary", "get_manager_history",
               "get_manager_by_name")),
    "market": ("prices, effective ownership, and what the field is doing",
               ("ownership_eo", "get_expert_transfers")),
    "players": ("one player deeply: form, projections, history, intel",
                ("player_dossier", "player_form", "player_projections",
                 "projection_disagreement", "xpts_aggregate",
                 "get_player_history", "player_intel")),
    "fixtures": ("who plays whom and how hard, split attack/defence",
                 ("fixture_difficulty",)),
    "creators": ("what tracked creators said, and their track records",
                 ("fpl_creator_consensus", "fpl_player_claims",
                  "fpl_creator_track_record", "summarise_fpl_youtube",
                  "fetch_youtube_transcript")),
    "elite": ("what the crawled elite cohort holds and moved",
              ("get_expert_teams_summary",)),
    "analysis": ("raw SQL over the point-in-time warehouse, charts, and the "
                 "transfer solver",
                 ("query", "python_viz", "suggest_transfers")),
    "memory": ("watchlist, saved analyses, and the idea inbox",
               ("watchlist_add", "watchlist_list", "watchlist_remove",
                "save_analysis", "run_analysis", "list_analyses",
                "submit_idea")),
}


def families_prompt(registered: set[str]) -> str:
    """The tool-family guidance, derived from what actually registered."""
    lines = ["## Your tool families (use the right family first)"]
    for fam, (use_when, names) in TOOL_FAMILIES.items():
        live = sorted(set(names) & registered)
        if live:
            lines.append(f"- **{fam}** -- {use_when}: {', '.join(live)}")
    return "\n".join(lines)

#: Wall-clock budget for one turn. A stuck CLI is killed, the transcript gets
#: an honest error, and the conversation continues.
#: A turn dies only when it goes QUIET, not merely long. A real analytical
#: turn is many minutes of steady tool calls (one suggest_transfers solve is
#: ~200s of silence while the MILP runs, hence the generous idle window); the
#: wall cap is the backstop against a truly runaway session. The 300s
#: wall-clock kill this replaces cut down a healthy 20-tool-call analysis
#: mid-thought.
TURN_IDLE_TIMEOUT_S = 300
TURN_HARD_CAP_S = 1500

_ASSET_ID = re.compile(r"^[0-9a-fA-F][0-9a-fA-F-]{7,63}$")


class ChatAgentError(Exception):
    """Base for chat agent failures the routes translate to HTTP."""


class UnknownConversation(ChatAgentError):
    pass


class TurnInFlight(ChatAgentError):
    """A second message arrived while a turn is running (HTTP 409)."""

    def __init__(self, conv_id: str, since: str | None):
        self.conv_id = conv_id
        self.since = since
        super().__init__(
            f"a turn is already running in {conv_id}"
            + (f" (since {since})" if since else "")
        )


# --------------------------------------------------------------------------
# SDK message -> transcript events (pure; the tests pin it)
# --------------------------------------------------------------------------

def _compact(value: Any, limit: int) -> str:
    """A one-line preview of a tool input/result, bounded for the transcript."""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit]}… ({len(text)} chars)"
    return text


def _result_text(content: Any) -> str:
    """tool_result content arrives as a string or a list of typed blocks."""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            else:
                parts.append(_compact(block, 200))
        return "\n".join(parts)
    return "" if content is None else str(content)


def events_from_message(msg: Any) -> list[tuple[str, dict[str, Any]]]:
    """One typed SDK message -> zero or more (type, payload) transcript events.

    The payload shapes are IDENTICAL to what the stream-json parser this
    replaces produced -- the UI and the on-disk transcripts predate the SDK
    and must not notice the engine change. Unknown message kinds produce
    nothing rather than crashing the turn: the SDK's message union may grow,
    and a transcript with a gap beats a dead loop.
    """
    events: list[tuple[str, dict[str, Any]]] = []

    if isinstance(msg, SystemMessage):
        if msg.subtype == "init":
            data = msg.data or {}
            events.append(("init", {
                "session_id": data.get("session_id"),
                "model": data.get("model"),
                "tools": len(data.get("tools") or []),
            }))
    elif isinstance(msg, StreamEvent):
        ev = msg.event or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                events.append(("delta", {"text": delta["text"]}))
    elif isinstance(msg, AssistantMessage):
        for block in msg.content or []:
            if isinstance(block, TextBlock) and (block.text or "").strip():
                events.append(("text", {"text": block.text}))
            elif isinstance(block, ToolUseBlock):
                events.append(("tool_use", {
                    "id": block.id,
                    "name": block.name,
                    "input_preview": _compact(block.input, 300),
                }))
    elif isinstance(msg, UserMessage):
        content = msg.content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, ToolResultBlock):
                    events.append(("tool_result", {
                        "tool_use_id": block.tool_use_id,
                        "is_error": bool(block.is_error),
                        "preview": _compact(_result_text(block.content), 500),
                    }))
    elif isinstance(msg, ResultMessage):
        payload: dict[str, Any] = {
            "session_id": msg.session_id,
            "cost_usd": msg.total_cost_usd,
            "duration_ms": msg.duration_ms,
            "num_turns": msg.num_turns,
        }
        if msg.subtype == "success" and not msg.is_error:
            events.append(("done", payload))
        else:
            payload["message"] = str(
                msg.result or msg.subtype or "the agent turn failed"
            )
            events.append(("error", payload))
    return events


# --------------------------------------------------------------------------
# MCP tool enumeration (once per process; the allowlist is reality ∩ intent)
# --------------------------------------------------------------------------

def list_mcp_tools(python: str = "", main: Path | None = None,
                   timeout: float = 0.0) -> list[str] | None:
    """The toolbelt's registered tool names, enumerated IN PROCESS.

    The subprocess JSON-RPC prober this replaces existed because the toolbelt
    lived in another process. It no longer does: the FastMCP server is
    imported and its registry read directly. The signature keeps its old
    parameters (ignored) so callers did not all have to change in the same
    commit; returns None on any failure, and the caller falls back to the
    full intent list -- allowing a tool that does not exist allows nothing.
    """
    try:
        from fpl_mcp.server import mcp as toolbelt
        return [t.name for t in toolbelt._tool_manager.list_tools()]  # noqa: SLF001
    except Exception:  # noqa: BLE001 - enumeration is an optimisation, never a dependency
        return None


def toolbelt_instance():
    """The in-process MCP server the SDK serves to the CLI over memory.

    ``McpSdkServerConfig.instance`` accepts any ``mcp.server.Server``;
    FastMCP wraps exactly one. Importing here rather than at module top keeps
    platform startup honest about where the ~1s toolbelt import is spent and
    lets tests build a ChatAgent without the toolbelt present.
    """
    from fpl_mcp.server import mcp as toolbelt
    return toolbelt._mcp_server  # noqa: SLF001 - the documented seam


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------

def _now() -> str:
    return dt.datetime.now(UTC).isoformat()


class _Conv:
    """In-memory handle over one conversation directory."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()          # guards seq + append + fan-out
        self.subscribers: set[Queue] = set()
        self.next_seq = self._last_seq() + 1
        self.turn: _Turn | None = None

    @property
    def events_path(self) -> Path:
        return self.path / "events.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    def _last_seq(self) -> int:
        last = -1
        if self.events_path.exists():
            with self.events_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        last = max(last, int(json.loads(line)["seq"]))
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                        continue
        return last


class _Turn:
    """One in-flight SDK turn. Alive until the runner thread finishes.

    ``stopped`` and ``timed_out`` are plain attribute writes read by the
    async watchdog inside the turn's own event loop -- the GIL makes the
    flag handoff safe, and nothing here ever touches the loop from outside.
    """

    def __init__(self, text: str):
        self.text = text
        self.started = _now()
        self.finished = threading.Event()
        self.timed_out: str | bool = False
        self.last_activity = 0.0
        self.stopped = False

    def alive(self) -> bool:
        return not self.finished.is_set()


class ChatAgent:
    """Owns conversations, turns, persistence, and live fan-out."""

    def __init__(
        self,
        root: Path | str = CHAT_ROOT,
        *,
        claude_bin: str | None = None,
        timeout_s: float = TURN_IDLE_TIMEOUT_S,   # legacy name; now the IDLE window
        briefing_fn: Callable[[], str] | None = None,
        mcp_python: str = MCP_PYTHON,
        mcp_main: Path = MCP_MAIN,
        cwd: Path | None = None,
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.assets_dir = self.root / "assets"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self._claude_bin = claude_bin
        self.timeout_s = timeout_s
        self._briefing_fn = briefing_fn
        self.mcp_python = mcp_python
        self.mcp_main = Path(mcp_main)
        self.cwd = Path(cwd) if cwd else _REPO_ROOT
        #: SSE heartbeat cadence; tests shrink it so closing a stream never
        #: waits out a blocked queue read.
        self.heartbeat_s = 15.0
        self._convs: dict[str, _Conv] = {}
        self._registry_lock = threading.Lock()
        self._tools_cache: list[str] | None = None
        self._tools_cache_ready = False
        #: Test seam: build a fake Transport from the turn's options instead
        #: of letting the SDK spawn the real CLI. Production never sets it.
        self._transport_factory: Callable[[ClaudeAgentOptions], Any] | None = None

    # -- conversation store -------------------------------------------------

    def create_conversation(self, title: str | None = None) -> dict[str, Any]:
        conv_id = uuid.uuid4().hex
        path = self.root / conv_id
        path.mkdir(parents=True, exist_ok=False)
        meta = {
            "conv_id": conv_id,
            "claude_session_id": None,
            "title": title or "conversation",
            "created": _now(),
            "updated": _now(),
        }
        (path / "meta.json").write_text(json.dumps(meta, indent=2))
        with self._registry_lock:
            self._convs[conv_id] = _Conv(path)
        return meta

    def list_conversations(self) -> list[dict[str, Any]]:
        metas = []
        for meta_path in sorted(self.root.glob("*/meta.json")):
            try:
                metas.append(json.loads(meta_path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
        metas.sort(key=lambda m: m.get("updated") or "", reverse=True)
        return metas

    def meta(self, conv_id: str) -> dict[str, Any]:
        conv = self._conv(conv_id)
        try:
            return json.loads(conv.meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            return {"conv_id": conv_id}

    def _conv(self, conv_id: str) -> _Conv:
        if not re.fullmatch(r"[0-9a-f]{32}", conv_id or ""):
            raise UnknownConversation(f"malformed conversation id {conv_id!r}")
        with self._registry_lock:
            conv = self._convs.get(conv_id)
            if conv is None:
                path = self.root / conv_id
                if not path.is_dir():
                    raise UnknownConversation(f"no conversation {conv_id}")
                conv = _Conv(path)
                self._convs[conv_id] = conv
        return conv

    def _update_meta(self, conv: _Conv, **fields: Any) -> None:
        try:
            meta = json.loads(conv.meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {"conv_id": conv.path.name}
        meta.update(fields)
        meta["updated"] = _now()
        conv.meta_path.write_text(json.dumps(meta, indent=2))

    # -- events: persist THEN broadcast ------------------------------------

    def _emit(self, conv: _Conv, type_: str, payload: dict[str, Any]) -> dict[str, Any]:
        with conv.lock:
            event = {"seq": conv.next_seq, "ts": _now(),
                     "type": type_, "payload": payload}
            conv.next_seq += 1
            with conv.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            for q in list(conv.subscribers):
                q.put(event)
        return event

    def events(self, conv_id: str, after: int = -1) -> list[dict[str, Any]]:
        conv = self._conv(conv_id)
        out: list[dict[str, Any]] = []
        if not conv.events_path.exists():
            return out
        with conv.events_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and ev.get("seq", -1) > after:
                    out.append(ev)
        return out

    def running(self, conv_id: str) -> dict[str, Any]:
        conv = self._conv(conv_id)
        turn = conv.turn
        if turn is not None and turn.alive():
            return {"running": True, "since": turn.started,
                    "text": turn.text[:200]}
        return {"running": False}

    def subscribe(self, conv_id: str, after: int = -1,
                  heartbeat_s: float | None = None,
                  follow: bool = True) -> Iterator[dict[str, Any]]:
        """Replay persisted events > after, then follow live. Sync generator
        (sse-starlette iterates it in a threadpool); yields SSE-shaped dicts,
        including comment heartbeats every ``heartbeat_s`` of silence so
        idle-connection killers never reap a quiet turn (Argus chat.ts:11).

        ``follow=False`` closes after the replay -- for curl debugging and for
        tests, where an endless stream would hang a buffered client.
        """
        conv = self._conv(conv_id)
        if heartbeat_s is None:
            heartbeat_s = self.heartbeat_s
        q: Queue = Queue()
        with conv.lock:
            conv.subscribers.add(q)
        last = after
        try:
            # Replay from disk. Anything emitted while we read also landed in
            # q (registered above, under the lock); the seq guard dedupes.
            for ev in self.events(conv_id, after):
                last = max(last, ev["seq"])
                yield self._sse(ev)
            if not follow:
                return
            while True:
                try:
                    ev = q.get(timeout=heartbeat_s)
                except Empty:
                    yield {"comment": "hb"}
                    continue
                if ev["seq"] <= last:
                    continue
                last = ev["seq"]
                yield self._sse(ev)
        finally:
            with conv.lock:
                conv.subscribers.discard(q)

    @staticmethod
    def _sse(event: dict[str, Any]) -> dict[str, Any]:
        return {"event": event["type"], "id": str(event["seq"]),
                "data": json.dumps(event, ensure_ascii=False)}

    # -- assets -------------------------------------------------------------

    def asset_path(self, asset_id: str, ext: str = "png") -> Path | None:
        """Path-safe asset lookup: uuid-hex id, allowlisted extension, no
        traversal. python_viz writes svg alongside png under one id."""
        if not _ASSET_ID.fullmatch(asset_id or "") or ext not in ("png", "svg"):
            return None
        path = (self.assets_dir / f"{asset_id}.{ext}").resolve()
        if self.assets_dir.resolve() not in path.parents:
            return None
        return path if path.is_file() else None

    # -- the turn -----------------------------------------------------------

    def start_turn(self, conv_id: str, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ChatAgentError("text is required")
        conv = self._conv(conv_id)
        with self._registry_lock:
            if conv.turn is not None and conv.turn.alive():
                raise TurnInFlight(conv_id, conv.turn.started)
            turn = _Turn(text)
            conv.turn = turn
        user_ev = self._emit(conv, "user", {"text": text})
        self._update_meta(conv)
        runner = threading.Thread(
            target=self._run_turn, args=(conv, turn), daemon=True,
            name=f"chat-turn-{conv_id[:8]}",
        )
        runner.start()
        return {"started": True, "conv_id": conv_id, "seq": user_ev["seq"]}

    def stop(self, conv_id: str) -> dict[str, Any]:
        conv = self._conv(conv_id)
        turn = conv.turn
        if turn is None or not turn.alive():
            return {"stopped": False, "reason": "no turn in flight"}
        # A flag, not a kill: the turn's own watchdog task sees it within
        # 200ms, asks the SDK control channel to interrupt, and only cancels
        # the stream (tearing down the CLI) if the interrupt is ignored for
        # five seconds. The subprocess engine could only SIGKILL.
        turn.stopped = True
        return {"stopped": True}

    # -- internals ----------------------------------------------------------

    def _briefing(self) -> str:
        if self._briefing_fn is not None:
            try:
                return self._briefing_fn()
            except Exception as exc:  # noqa: BLE001 - a turn without a briefing beats no turn
                return f"(warehouse briefing unavailable: {type(exc).__name__}: {exc})"
        try:
            from fpl_edge.interfaces.briefing import warehouse_briefing
            return warehouse_briefing()
        except Exception as exc:  # noqa: BLE001
            return f"(warehouse briefing unavailable: {type(exc).__name__}: {exc})"

    def allowed_tools(self) -> list[str]:
        """Intent ∩ reality, qualified with the MCP server name. Enumerated
        once per process; on enumeration failure the full intent list is used
        (allowing a tool that does not exist allows nothing)."""
        if not self._tools_cache_ready:
            self._tools_cache = list_mcp_tools(self.mcp_python, self.mcp_main)
            self._tools_cache_ready = True
        names = (
            sorted(set(self._tools_cache) & set(INTENT_TOOLS))
            if self._tools_cache else sorted(INTENT_TOOLS)
        )
        return [f"mcp__fpl-server__{n}" for n in names]

    def _scrub_environment(self) -> None:
        """Remove auth/nesting variables the SDK child must never inherit.

        The SDK composes the CLI's environment from ``os.environ`` and can add
        but not remove, so the removal happens here, on the server's own
        environment. That is safe because this server has no legitimate use
        for any of these: the CLI's own login is the auth, and a leaked
        ANTHROPIC_BASE_URL once sent the CLI's OAuth token to a dev proxy
        that rejected it as revoked. CLAUDECODE itself is stripped by the SDK
        (its issue #573); the rest are ours to clear.
        """
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
                    "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT"):
            os.environ.pop(var, None)

    def build_options(self, conv: _Conv, session_id: str | None,
                      stderr_cb: Callable[[str], None] | None = None,
                      ) -> ClaudeAgentOptions:
        """The SDK options for one turn. Pure given its inputs; the tests pin
        that resume, the toolbelt, and the tool posture survive the engine
        swap exactly.

        - ``tools=[]`` disables EVERY built-in (Bash, Read, the lot): the
          toolbelt is the agent's only hands. ``disallowed_tools`` stays as
          belt-and-braces so the posture survives even if a future SDK
          version grows the built-in set behind a preset.
        - ``model="opus"`` -- the owner's decision is Opus always; the alias
          tracks the CLI's newest Opus rather than pinning a dated id.
        - The system prompt APPENDS to the claude_code preset, matching the
          old ``--append-system-prompt`` exactly.
        """
        return ClaudeAgentOptions(
            cwd=str(self.cwd),
            model="opus",
            # The old engine preferred ~/.local/bin/claude over PATH because
            # an nvm shim's Node 18 crashes cli.js. The SDK does its own
            # discovery, but an explicit binary from the caller still wins.
            cli_path=self._claude_bin,
            resume=session_id or None,
            tools=[],
            allowed_tools=self.allowed_tools(),
            disallowed_tools=list(DISALLOWED_TOOLS),
            system_prompt={
                "type": "preset", "preset": "claude_code",
                "append": self._briefing() + "\n\n" + CHARTER + "\n\n"
                + families_prompt(set(self._tools_cache or [])
                                  or set(INTENT_TOOLS)),
            },
            # IN-PROCESS (CHAT_ARCHITECTURE §3.2): the SDK serves the FastMCP
            # instance to the CLI over an in-memory transport. No spawned
            # process, no stdio framing, and a tool call is a function call
            # in this server. The per-conversation id the stdio config used
            # to pass as child env is now set on our own environ per turn --
            # save_analysis reads it for its commit message. Two turns in
            # DIFFERENT conversations racing could momentarily cross those
            # cosmetic attributions; a turn within one conversation is
            # single-flight, and the commit content itself is unaffected.
            mcp_servers={
                "fpl-server": {
                    "type": "sdk",
                    "name": "fpl-server",
                    "instance": toolbelt_instance(),
                }
            },
            strict_mcp_config=True,
            include_partial_messages=True,
            stderr=stderr_cb,
        )

    def _run_turn(self, conv: _Conv, turn: _Turn) -> None:
        try:
            self._run_turn_inner(conv, turn)
        except Exception as exc:  # noqa: BLE001 - the transcript gets the truth, never a crash
            self._emit(conv, "error", {
                "message": f"turn crashed: {type(exc).__name__}: {exc}",
            })
        finally:
            turn.finished.set()
            self._update_meta(conv)

    def _run_turn_inner(self, conv: _Conv, turn: _Turn) -> None:
        try:
            meta = json.loads(conv.meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
        session_id = meta.get("claude_session_id")
        self._scrub_environment()
        os.environ["ARGUS_CONV_ID"] = conv.path.name
        try:
            asyncio.run(self._async_turn(conv, turn, session_id))
        except CLINotFoundError:
            self._emit(conv, "error", {"message": (
                "claude CLI not found (looked on PATH and in the SDK's known "
                "locations). Install Claude Code and run `claude login`, "
                "then ask again."
            )})
        except ProcessError as exc:
            self._emit_process_error(conv, exc)

    def _emit_process_error(self, conv: _Conv, exc: ProcessError) -> None:
        """The CLI died without an answer. Same remediation the subprocess
        engine gave: a lost session cache clears the stale id so the next
        message starts fresh; an auth failure names `claude login`."""
        detail = " ".join(str(part) for part in
                          (exc, getattr(exc, "stderr", "") or "") if part)
        message = f"the claude CLI exited without an answer: {_compact(detail, 600)}"
        lower = detail.lower()
        if "no conversation found with session id" in lower:
            self._update_meta(conv, claude_session_id=None)
            message += (
                "\nThe CLI's session cache lost this conversation; the "
                "stale session id was cleared and the next message starts "
                "a fresh agent session (transcript here is unaffected)."
            )
        elif ("log in" in lower or "login" in lower or "authent" in lower
                or "oauth" in lower or "api key" in lower):
            message += "\nRun `claude login` to authenticate the CLI."
        self._emit(conv, "error", {"message": message})

    async def _async_turn(self, conv: _Conv, turn: _Turn,
                          session_id: str | None) -> None:
        """One SDK turn: connect, ask, translate the stream, watch the clock.

        The watchdog and the stop flag live INSIDE the event loop as a
        sibling task, so `stop()` from any thread only ever sets a flag --
        no cross-thread loop juggling. Interrupt is graceful-then-hard:
        the SDK control channel first, task cancellation after a grace
        period (the cancel tears down the transport, which kills the CLI).
        """
        stderr_tail: list[str] = []

        def _tail(line: str) -> None:
            stderr_tail.append(line.rstrip("\n"))
            del stderr_tail[:-40]

        options = self.build_options(conv, session_id, stderr_cb=_tail)
        transport = (self._transport_factory(options)
                     if self._transport_factory is not None else None)
        client = ClaudeSDKClient(options, transport=transport)

        started = time.monotonic()
        turn.last_activity = started
        idle_s = self.timeout_s
        saw_terminal = False
        interrupted_at: float | None = None
        stream_error: BaseException | None = None

        async def _consume() -> None:
            nonlocal saw_terminal
            async for msg in client.receive_response():
                turn.last_activity = time.monotonic()
                for type_, payload in events_from_message(msg):
                    if type_ == "init":
                        sid = payload.get("session_id")
                        if sid:
                            self._update_meta(conv, claude_session_id=sid)
                    elif type_ in ("done", "error"):
                        saw_terminal = True
                        sid = payload.get("session_id")
                        if sid:
                            self._update_meta(conv, claude_session_id=sid)
                        if type_ == "error":
                            msg_text = payload.get("message", "")
                            if ("authenticat" in msg_text.lower()
                                    or "oauth" in msg_text.lower()):
                                payload["message"] = (
                                    msg_text + "\nThe claude CLI's login has "
                                    "expired or been revoked: run `claude "
                                    "login` in a terminal, then ask again."
                                )
                    self._emit(conv, type_, payload)

        try:
            await client.connect()
            await client.query(turn.text)
            consumer = asyncio.ensure_future(_consume())
            try:
                while not consumer.done():
                    await asyncio.wait([consumer], timeout=0.2)
                    now = time.monotonic()
                    hard = now - started > TURN_HARD_CAP_S
                    idle = now - turn.last_activity > idle_s
                    if hard or idle:
                        turn.timed_out = "hard" if hard else "idle"
                    if (turn.stopped or turn.timed_out) and interrupted_at is None:
                        interrupted_at = now
                        try:
                            await client.interrupt()
                        except Exception:  # noqa: BLE001 - grace path; the cancel below is the guarantee
                            consumer.cancel()
                    elif interrupted_at is not None and now - interrupted_at > 5.0:
                        consumer.cancel()
                await consumer
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - reported below, transcript over traceback
                stream_error = exc
        finally:
            try:
                await client.disconnect()
            except Exception:  # noqa: BLE001, S110 - a failed teardown must not eat the post-mortem
                pass

        if turn.timed_out == "idle":
            self._emit(conv, "error", {"message": (
                f"the turn timed out after {int(idle_s)}s of silence (no "
                "output, no tool activity) and was interrupted; the "
                "conversation is still usable -- ask again or narrow the "
                "question."
            )})
        elif turn.timed_out == "hard":
            self._emit(conv, "error", {"message": (
                f"the turn timed out at the {TURN_HARD_CAP_S // 60}-minute "
                "hard cap and was interrupted; the conversation is still "
                "usable -- narrow the question or split it into steps."
            )})
        elif turn.stopped:
            self._emit(conv, "error", {"message": "stopped by user"})
        elif stream_error is not None:
            tail = "\n".join(stderr_tail[-8:]).strip()
            self._emit(conv, "error", {"message": (
                f"the agent stream failed: {type(stream_error).__name__}: "
                f"{stream_error}" + (f"\n{tail}" if tail else "")
            )})
        elif not saw_terminal:
            self._emit(conv, "error", {"message": (
                "the agent stream ended without a result event; the answer "
                "above (if any) may be incomplete."
            )})
