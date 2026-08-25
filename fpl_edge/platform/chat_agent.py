"""The chat agent loop: conversations that drive a headless `claude -p`.

Argus discipline (docs/platform/argus_architecture.md §1.3), transposed:

- **A turn is a server-side job.** ``start_turn`` spawns the Max-plan
  ``claude`` CLI detached from any HTTP request and reads its stream-json
  output to completion whether or not a browser is watching.
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

import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Callable, Iterator

UTC = dt.timezone.utc

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: Default conversation store. Deliberately under data/warehouse so backups
#: that cover the warehouse cover the transcripts, but it is NOT the DuckDB
#: file and nothing here takes the write lock.
CHAT_ROOT = _REPO_ROOT / "data" / "warehouse" / "chat"

#: Where the MCP toolbelt lives and the interpreter that runs it.
MCP_PYTHON = "/Users/nripeshpradhan/.pyenv/versions/3.11.2/bin/python"
MCP_MAIN = Path("/Users/nripeshpradhan/Documents/Github/FPL-MCP/main.py")

#: The tools the agent is ALLOWED to want. What it actually gets is this list
#: intersected with what the MCP server registers at runtime -- a tool the
#: toolbelt has not shipped yet simply is not offered, and nothing outside
#: this list (no Bash, no file tools) is ever offered regardless.
INTENT_TOOLS: tuple[str, ...] = (
    "query",
    "make_chart",
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
    "only; embed [chart:<id>] markers where make_chart tells you; be direct; "
    "state as-of instants."
)

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
# stream-json parsing (pure; the tests pin it against a canned transcript)
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


def parse_stream_json_line(line: str) -> list[tuple[str, dict[str, Any]]]:
    """One CLI stream-json line -> zero or more (type, payload) events.

    Unknown message shapes produce nothing rather than crashing the turn:
    the CLI's format may grow fields, and a transcript with a gap beats a
    dead loop. Non-JSON lines return nothing; the caller keeps them in the
    stderr tail for the post-mortem.
    """
    line = line.strip()
    if not line:
        return []
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []

    kind = obj.get("type")
    events: list[tuple[str, dict[str, Any]]] = []

    if kind == "system" and obj.get("subtype") == "init":
        events.append(("init", {
            "session_id": obj.get("session_id"),
            "model": obj.get("model"),
            "tools": len(obj.get("tools") or []),
        }))
    elif kind == "stream_event":
        ev = obj.get("event") or {}
        if ev.get("type") == "content_block_delta":
            delta = ev.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                events.append(("delta", {"text": delta["text"]}))
    elif kind == "assistant":
        for block in (obj.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and (block.get("text") or "").strip():
                events.append(("text", {"text": block["text"]}))
            elif block.get("type") == "tool_use":
                events.append(("tool_use", {
                    "id": block.get("id"),
                    "name": block.get("name"),
                    "input_preview": _compact(block.get("input"), 300),
                }))
    elif kind == "user":
        for block in (obj.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                events.append(("tool_result", {
                    "tool_use_id": block.get("tool_use_id"),
                    "is_error": bool(block.get("is_error")),
                    "preview": _compact(_result_text(block.get("content")), 500),
                }))
    elif kind == "result":
        payload = {
            "session_id": obj.get("session_id"),
            "cost_usd": obj.get("total_cost_usd"),
            "duration_ms": obj.get("duration_ms"),
            "num_turns": obj.get("num_turns"),
        }
        if obj.get("subtype") == "success" and not obj.get("is_error"):
            events.append(("done", payload))
        else:
            payload["message"] = str(
                obj.get("result") or obj.get("error") or obj.get("subtype")
                or "the agent turn failed"
            )
            events.append(("error", payload))
    return events


# --------------------------------------------------------------------------
# MCP tool enumeration (once per process; the allowlist is reality ∩ intent)
# --------------------------------------------------------------------------

def list_mcp_tools(python: str, main: Path, timeout: float = 25.0) -> list[str] | None:
    """Ask the FPL MCP server, over stdio JSON-RPC, which tools exist.

    Returns None on any failure -- the caller falls back to the full intent
    list, which is safe because ``--allowedTools`` naming a tool that does
    not exist merely allows nothing.
    """
    if not Path(python).exists() or not main.exists():
        return None
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "fpl-edge-platform", "version": "1.0"},
        }},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    try:
        proc = subprocess.Popen(
            [python, str(main)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, cwd=str(main.parent),
            start_new_session=True, text=True,
        )
    except OSError:
        return None

    found: list[str] | None = None

    def _read() -> None:
        nonlocal found
        assert proc.stdout is not None
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # the server prints a banner line before JSON-RPC
            if msg.get("id") == 2 and "result" in msg:
                tools = (msg["result"] or {}).get("tools") or []
                found = [t.get("name") for t in tools if t.get("name")]
                return

    try:
        assert proc.stdin is not None
        for req in requests:
            proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        reader = threading.Thread(target=_read, daemon=True)
        reader.start()
        reader.join(timeout)
    except (OSError, ValueError):
        pass
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        proc.wait()
    return found


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
        self.turn: "_Turn | None" = None

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
    """One in-flight CLI run. Alive until the runner thread finishes."""

    def __init__(self, text: str):
        self.text = text
        self.started = _now()
        self.proc: subprocess.Popen | None = None
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

    def asset_path(self, asset_id: str) -> Path | None:
        """Path-safe asset lookup: uuid-hex (plus dashes) only, no traversal."""
        if not _ASSET_ID.fullmatch(asset_id or ""):
            return None
        path = (self.assets_dir / f"{asset_id}.png").resolve()
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
        turn.stopped = True
        self._kill(turn)
        return {"stopped": True}

    # -- internals ----------------------------------------------------------

    def _find_cli(self) -> str | None:
        if self._claude_bin:
            return self._claude_bin if Path(self._claude_bin).exists() else None
        # The native install is preferred over whatever PATH finds: on this
        # machine PATH resolves to an nvm shim whose Node 18 crashes cli.js
        # ("TypeError: Object not disposable" -- the dispose polyfill needs a
        # newer Node), while ~/.local/bin/claude is a self-contained binary.
        native = Path.home() / ".local" / "bin" / "claude"
        if native.exists():
            return str(native)
        return shutil.which("claude")

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

    def _mcp_config(self, conv: _Conv) -> Path:
        config = {
            "mcpServers": {
                "fpl-server": {
                    "command": self.mcp_python,
                    "args": [str(self.mcp_main)],
                    "env": {"ARGUS_CONV_ID": conv.path.name},
                }
            }
        }
        path = conv.path / "mcp.json"
        path.write_text(json.dumps(config, indent=2))
        return path

    def _build_command(self, conv: _Conv, turn: _Turn,
                       binary: str, session_id: str | None) -> list[str]:
        system_prompt = self._briefing() + "\n\n" + CHARTER
        cmd = [
            binary, "-p", turn.text,
            "--output-format", "stream-json",
            "--include-partial-messages",
            "--verbose",  # -p + stream-json requires it
            "--mcp-config", str(self._mcp_config(conv)),
            "--strict-mcp-config",  # only OUR toolbelt, not ambient servers
            "--append-system-prompt", system_prompt,
            "--allowedTools", ",".join(self.allowed_tools()),
            "--disallowedTools", ",".join(DISALLOWED_TOOLS),
        ]
        if session_id:
            cmd += ["--resume", session_id]
        return cmd

    def _kill(self, turn: _Turn) -> None:
        proc = turn.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass

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
        binary = self._find_cli()
        if binary is None:
            self._emit(conv, "error", {"message": (
                "claude CLI not found on PATH (looked for `claude` and "
                "~/.local/bin/claude). Install Claude Code and run "
                "`claude login`; the deterministic router still works."
            )})
            return

        try:
            meta = json.loads(conv.meta_path.read_text())
        except (OSError, json.JSONDecodeError):
            meta = {}
        session_id = meta.get("claude_session_id")

        env = dict(os.environ)
        # The CLI's own login is the auth: no key, no proxy, no base-url
        # override may leak in from whatever launched this server (a dev
        # harness sets ANTHROPIC_BASE_URL, which sends the CLI's OAuth token
        # to a proxy that rejects it as revoked).
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
                    "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"):
            env.pop(var, None)
        # If the platform server was itself launched from inside a Claude
        # Code session (dev previews), the child CLI would refuse to start
        # ("cannot be launched inside another Claude Code session"). This
        # headless -p child is the designed escalation path, not a nested
        # interactive session; scrub the markers.
        env.pop("CLAUDECODE", None)
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        env.pop("CLAUDE_CODE_SSE_PORT", None)

        cmd = self._build_command(conv, turn, binary, session_id)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,  # -p reads a piped stdin to EOF;
                                           # an inherited open pipe = hang
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(self.cwd), env=env, text=True,
                start_new_session=True,
            )
        except OSError as exc:
            self._emit(conv, "error", {
                "message": f"could not start the claude CLI: {exc}",
            })
            return
        turn.proc = proc
        if turn.stopped:  # stop() raced the spawn; honour it immediately
            self._kill(turn)

        started = time.monotonic()
        turn.last_activity = started

        idle_s = self.timeout_s                     # test-overridable
        poll_s = max(0.1, min(10.0, idle_s / 3))

        def _watchdog() -> None:
            while proc.poll() is None:
                time.sleep(poll_s)
                now = time.monotonic()
                if now - started > TURN_HARD_CAP_S:
                    turn.timed_out = "hard"
                    self._kill(turn)
                    return
                if now - turn.last_activity > idle_s:
                    turn.timed_out = "idle"
                    self._kill(turn)
                    return

        watchdog = threading.Thread(target=_watchdog, daemon=True)
        watchdog.start()

        stderr_tail: list[str] = []

        def _drain_stderr() -> None:
            assert proc.stderr is not None
            for line in proc.stderr:
                stderr_tail.append(line.rstrip("\n"))
                del stderr_tail[:-40]

        err_thread = threading.Thread(target=_drain_stderr, daemon=True)
        err_thread.start()

        saw_terminal = False
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                turn.last_activity = time.monotonic()
                for type_, payload in parse_stream_json_line(raw):
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
                            msg = payload.get("message", "")
                            if ("authenticat" in msg.lower()
                                    or "oauth" in msg.lower()):
                                payload["message"] = (
                                    msg + "\nThe claude CLI's login has "
                                    "expired or been revoked: run `claude "
                                    "login` in a terminal, then ask again."
                                )
                    self._emit(conv, type_, payload)
        finally:
            proc.wait()
            err_thread.join(timeout=5)

        if turn.timed_out == "idle":
            self._emit(conv, "error", {"message": (
                f"the turn timed out after {int(idle_s)}s of silence (no "
                "output, no tool activity) and was killed; the conversation "
                "is still usable -- ask again or narrow the question."
            )})
        elif turn.timed_out == "hard":
            self._emit(conv, "error", {"message": (
                f"the turn timed out at the {TURN_HARD_CAP_S // 60}-minute "
                "hard cap and was killed; the conversation is still usable "
                "-- narrow the question or split it into steps."
            )})
        elif turn.stopped:
            self._emit(conv, "error", {"message": "stopped by user"})
        elif proc.returncode != 0 and not saw_terminal:
            tail = "\n".join(stderr_tail[-8:]).strip()
            message = (
                f"the claude CLI exited {proc.returncode} without an answer."
                + (f"\n{tail}" if tail else "")
            )
            lower = tail.lower()
            if "no conversation found with session id" in lower:
                self._update_meta(conv, claude_session_id=None)
                message += (
                    "\nThe CLI's session cache lost this conversation; the "
                    "stale session id was cleared and the next message starts "
                    "a fresh agent session (transcript here is unaffected)."
                )
            elif "log in" in lower or "login" in lower or "authent" in lower \
                    or "api key" in lower:
                message += "\nRun `claude login` to authenticate the CLI."
            self._emit(conv, "error", {"message": message})
        elif not saw_terminal:
            self._emit(conv, "error", {"message": (
                "the claude CLI stream ended without a result event; the "
                "answer above (if any) may be incomplete."
            )})
