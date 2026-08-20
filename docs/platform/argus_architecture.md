# Argus Architecture — Study Notes for the FPL Platform

Source: `code_bases/argus` (Argus — an agent-in-a-container webapp that gives an engineering
team a Claude agent over their production infra). This document explains how Argus is built,
with file:line references into the codebase, and closes with a mapping of each concept onto
the FPL platform. Written so a senior engineer could rebuild the relevant parts without
re-reading the whole codebase.

Argus in one paragraph: a Fastify backend drives a Claude Agent SDK session per conversation;
production data is reached only through per-source read-only **query tools** registered as an
in-process MCP server; durable artifacts are built on **Scripts** (git-versioned, typed-JSON
data programs executed by a separate headless **scripts-service**), which are the *only* data
path for **Dashboards** (compiled React in a sandboxed iframe) and **Monitors** (cron-scheduled
reports and deterministic alerts delivered to an in-app Inbox and Slack). Three deployables from
one repo: `server/` (argus web), `scripts-service/`, and a credential-free dashboard smoke
worker; `web/` is the React SPA; `shared/` holds the wire types both sides import.

---

## 1. The agent loop

### 1.1 Driving the Claude Agent SDK

One conversation = one long-lived SDK session; one user message = one **turn**. The stack is:

```
POST /api/conversations/:id/chat        server/http/routes/chat.ts:56
  → startTurn()                         server/agent/turns.ts:117
    → runChat() async generator         server/agent/run.ts:282
      → streamOnce() → query({...})     server/agent/run.ts:166-179  (SDK entry)
```

`buildOptions()` (server/agent/run.ts:15-84) assembles the SDK `Options` per turn:

- `cwd: config.sourcecodeDir` — the agent's working directory is the runtime-cloned source
  repos, not the app's own tree (run.ts:25).
- `systemPrompt: buildSystemPrompt(enabledDatasources, worktreeRoot, …)` — the prompt is
  *derived from the conversation's enabled datasources*, so guidance only appears for
  capabilities that are actually registered (run.ts:28).
- `settingSources: []` — deliberately loads no ambient `~/.claude` or project settings/skills
  (run.ts:31); the deployment fully owns the agent's configuration.
- `resume: sessionId` — multi-turn continuity. The conversation row stores the SDK
  `session_id`; each follow-up passes `resume` so the SDK reloads its own transcript files
  from `DATA_DIR` (run.ts:36). This is why the deploy is single-instance: session files are
  machine-local (README.md:94-97).
- **Session-loss recovery**: if resume fails with "No conversation found with session ID",
  `runChat` transparently restarts with a `sessionRecoveryPrompt` rebuilt from the durable
  SQLite transcript (run.ts:275-297, server/agent/session-recovery.ts). The SDK's session
  files are treated as a cache; SQLite (`messages` table) is the source of truth.
- **Prompt as a controlled stream**: the prompt is yielded through an async generator that
  stays open until the terminal result (`controlledPromptStream`, run.ts:126-142) — keeping
  stdin open is what lets `Query.interrupt()` use the SDK control channel for graceful stops.
  A user "Stop" first tries `sdkQuery.interrupt()` and falls back to a hard
  `AbortController.abort()` after a 5s grace (`armGracefulInterrupt`, run.ts:144-160).

`streamOnce` then iterates the SDK message stream and normalizes it into a small
`AgentEvent` union (run.ts:86-93): `session | assistant | tool_use | tool_result | usage |
result | error`. Tool results arrive as SDK *user* messages containing `tool_result` blocks
and are re-paired to their `tool_use` id (run.ts:205-221); result payloads shown to the UI are
capped at 6,000 chars (run.ts:95). Per-model usage/cost is taken from the terminal SDK result
message (`modelUsage`, `total_cost_usd`) (run.ts:223-258).

### 1.2 Tool registration: in-process MCP, explicit registries

Tools reach the SDK as **in-process MCP servers** built per turn:

- `buildMcpServers()` (server/agent/mcp-servers.ts:18-35) walks the explicit ordered registry
  `AGENT_TOOL_FAMILIES` (server/agent/tool-registry.ts:12-21): source, github-pr, linear,
  scripts, dashboards, monitors, query, slack. Each family is an `AgentToolFamily` descriptor
  owning its MCP `serverName`, an `enabled(context)` access predicate, provenance metadata,
  and a `buildTools(context)` builder. Disabled families/sources simply produce no server —
  "a disabled source's tools are simply not registered" (README.md:146).
- Each enabled family becomes `createSdkMcpServer({ name, tools })` (mcp-servers.ts:28-31) —
  no separate MCP process, no sockets; the tools are plain functions in the argus process, so
  they can close over the caller's identity and the conversation id (`AgentToolContext`).
- Registration is deliberately **not** auto-discovered: "Adding authority should always
  produce an obvious one-line registry change in code review; there is no filesystem
  auto-discovery" (docs/adding-tools-and-sources.md:12-13). Registry invariant tests reject
  duplicate ids/names (tool-registry.ts:23-27, adding-tools-and-sources.md:123-125).

Query tools specifically: each datasource is one self-contained module under
`server/tools/query/sources/`, exported from the single `source-registry.ts`; a source owns
its stable id, MCP tool name, model guidance (`useWhen` + description), picker/trace
presentation, and one typed transport adapter from `server/tools/query/adapters/`
(adding-tools-and-sources.md:22-32). Everything else — datasource picker, embedded and remote
MCP registration, script grants, audit provenance, prompt sections, UI labels — is *derived*
from that one entry. The shared execution policy (`runQuery`, server/tools/query/run.ts:60-107)
owns what adapters don't: missing-env errors, failure classification with bounded retry
(2 retries with jittered backoff, only for failures classified `retryable` — safe because
`assertReadOnlySql` already proved the statement is a read, run.ts:76-84), an optional
sandboxed `post_processing` pipeline (jq/grep/sed/awk, postproc.ts), and a 100k-char output cap.

Profile gating (server/agent/run.ts:54-83):

- `readonly`: SDK gets exactly `tools: ["Read","Glob","Grep"]`, `allowedTools` = those plus
  the qualified MCP tool names, `disallowedTools: ["Write","Edit","MultiEdit","NotebookEdit"]`,
  `permissionMode: "dontAsk"`, plus a `PreToolUse` hook (below).
- `full` (local/operator): `tools: { preset: "claude_code" }` and
  `permissionMode: "bypassPermissions"` — the operator's own credentials define the blast
  radius, not the app.

### 1.3 Turn lifecycle and streaming to the UI

`startTurn` (server/agent/turns.ts:117-272) is the key design: **a turn runs to completion on
the server regardless of any client connection**. The turn holds an in-memory event `buffer`
plus a `subscribers` set (turns.ts:16-28); every `AgentEvent` is persisted-then-broadcast.

- The HTTP layer streams over **SSE**: `POST /chat` starts the turn and hijacks the reply into
  a `text/event-stream`; `GET /stream` re-attaches after a reload (chat.ts:14-45, 137-141).
  `subscribe()` first *replays* the whole buffer, then follows live (turns.ts:279-289) — so
  reconnect/refresh never loses mid-turn output. A 20s comment-frame heartbeat keeps
  intermediaries from killing long silent tool runs (chat.ts:11, 25).
- Client disconnect only detaches the viewer; only an explicit `POST /stop` aborts
  (turns.ts:22, chat.ts:129-134). Completed turns keep their buffer 30s for late attachers,
  then rely on persisted history (turns.ts:263-267).
- Every `tool_use` is classified to a **provenance** id (`classifyProvenance`, turns.ts:189)
  and recorded to the OpenTelemetry activity feed with the verified user identity
  (turns.ts:190-196); tool traces are persisted per assistant message (`ToolTrace[]`), which
  is what the UI's command/source trace renders.
- **Deploy drain**: SIGTERM flips `isShuttingDown` so new turns 503 with Retry-After
  (chat.ts:59-64), and `awaitTurnsIdle(graceMs)` lets in-flight turns finalize
  (turns.ts:35-81). A durable `turn_started_at` marker plus `recoverInterruptedTurns()`
  (turns.ts:317-…) auto-resumes turns interrupted <1h ago via `resume` + a RECOVERY_PROMPT,
  with a one-attempt guard so a crashing resume can't loop forever (turns.ts:303-309).

The web side (`web/src/chat/ChatPane.tsx`, `MessageList.tsx`, `CommandTrace.tsx`) mirrors the
event union: assistant markdown accumulates, tool_use/tool_result pairs render as a collapsible
command trace tagged by provenance, and artifact-shaped tool calls (dashboards/monitors) get
dedicated cards (`DashboardCard.tsx`, `MonitorCard.tsx`).

**Takeaway for a rebuild**: the load-bearing ideas are (a) turn-as-server-side-job with
replayable buffer + SSE attach, (b) one explicit registry from which *all* tool surfaces are
derived, (c) durable transcript in your own DB with SDK sessions treated as a resumable cache,
(d) prompt/tooling derived per conversation from enabled capabilities.

---

## 5. Security / credential posture

(Numbered per the study brief; placed early because sections 2–4 lean on it.)

The stance: **the build is credential-agnostic; the deployment decides everything**
(README.md:21-31). One image serves both a locked-down shared instance and a full-power local
clone; "Nothing about read-vs-write is hardcoded."

- **`AUTH_MODE`** (`cloudflare` | `none`, server/config.ts:43-45): `cloudflare` verifies the
  Cloudflare Access JWT server-side and attributes every conversation and tool call to the
  verified identity (server/http/auth.ts); `none` is local. `BIND_HOST` defaults follow the
  mode — loopback without auth, `0.0.0.0` behind Cloudflare (config.ts:147). The scripts
  service link fails closed without `SCRIPTS_SHARED_SECRET` under cloudflare mode
  (README.md:99-100).
- **`AGENT_TOOLS_PROFILE`** (`readonly` | `full`, config.ts:48-50) gates the SDK toolbox as in
  §1.2. In readonly mode the SDK child process receives an **explicit env allowlist** of ~11
  vars (ANTHROPIC_API_KEY, PATH, proxy/cert vars… — `readonlyAgentEnvironment`,
  server/agent/readonly.ts:78-97): datasource and application credentials are *absent from the
  child's environment*, so even a hypothetical shell escape has nothing to read. In full mode
  the env passes through minus creds of explicitly disabled datasources
  (`disabledCredVars`, run.ts:46-51).
- **Filesystem fence**: a `PreToolUse` hook (readonly.ts:58-76) denies Bash outright, rejects
  absolute/`..` glob patterns, and canonicalizes every Read/Grep/Glob path via
  "deepest existing real ancestor" resolution (readonly.ts:21-44) so neither traversal nor a
  symlink can escape the approved roots (source clones, per-conversation worktrees, uploads,
  downloads).
- **Credentials decide read-vs-write**: the shared instance is handed read-only replica creds
  and viewer keys; the same code with write creds is a local power tool (README.md:280-282).
  Query safety is *also* enforced in code (`assertReadOnlySql`, `assertSingleStatement`,
  row caps — server/tools/query/run.ts:8-12) — defense in depth, not the only line.
- **Write-back is per-user OAuth, never a bot**: GitHub/Linear/Slack tools act with the asking
  member's own linked token, so "authority becomes the user's own" — someone who can't push
  to a repo can't make Argus do it (README.md:221-223). Tokens are AES-256-GCM sealed with the
  owner's email (and provider) folded into the AAD so rows can't be moved between users
  (README.md:253-255, 273-274). Structural denials over policy denials: no GitHub App private
  key in the env at all (minting installation tokens is *impossible*, not forbidden,
  README.md:249-251); the API surface simply has no merge/close/approve verb (README.md:236-238).
- **Membership gate**: viewing is link-based for anyone through Access; *writing* (chatting,
  keys, scripts/dashboards/monitors) requires membership managed by admins; shared read-only
  dashboard/monitor links opt out explicitly via `config: { sharedRead: true }`
  (README.md:128-137, server/http/routes/write-access.ts).
- **Audit**: prompts/replies/tool traces in local SQLite (`DATA_DIR/audit.db`); structured
  MCP/auth/tool/script/monitor activity exported to ClickHouse via OpenTelemetry with the
  verified identity (README.md:286-288).

The FPL-relevant lesson: separate the three axes — *identity* (AUTH_MODE), *tool surface*
(AGENT_TOOLS_PROFILE + explicit registries), and *credential possession* (what the deployment
env contains, what child processes inherit) — and make each one an environment decision, so
the same build runs solo-local and shared without forking code paths.

---

*(Sections 2–4 and 6 follow — written incrementally.)*
