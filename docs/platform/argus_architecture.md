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

## 2. Scripts: the git-versioned typed-JSON contract

A **script** is a small TypeScript program that returns JSON validated against a declared
schema. It is the single reusable "data function" primitive: dashboards and monitors *pin*
scripts; nothing else fetches data. (Design rationale: docs/scripts-design.md §0–§7.)

### 2.1 Authoring — the whole loop stays in chat

The agent authors scripts through five MCP tools (`server/tools/scripts/tools.ts:104-340`):
`save_script`, `get_script`, `delete_script`, `run_script`, `get_run`, `get_run_result`.

- `save_script` validates (entrypoint `index.ts` required; manifest required on full saves;
  datasource ids checked against the live source registry, tools.ts:139-155) and forwards to
  the scripts-service. Partial saves merge changed files onto a required `base_sha`.
- **Optimistic concurrency built for a flaky agent**: the tool tracks saves whose HTTP call
  died without an answer (`uncommitted` set) and on retry re-reads head as the base —
  a lost-but-landed save becomes a no-op, a concurrent edit becomes a clean 409 instead of a
  silent clobber (tools.ts:106-117).
- `run_script` is a *test* run (`mode: "draft"`): 5-minute timeout, capped logs, blocks up to
  ~50s then hands off to `get_run` polling (tools.ts:31, 242-280). Results default to a
  `summary` view (arrays truncated to 25 items with omitted-count markers); `get_run_result`
  pages the complete JSON in character-offset chunks so full outputs never depend on one
  oversized tool response (tools.ts:318-337).
- **The 10-second budget as a tool contract**: a draft run that succeeds but exceeds 10s
  comes back `isError: true` with a `performance` verdict and concrete remediation text —
  "push filtering and aggregation into SQL… If it genuinely cannot fit the budget, say so"
  (tools.ts:36, 66-80, 100). The latency constraint of the dashboards that will pin the
  script is enforced at authoring time, on the agent, not discovered by users later.

### 2.2 Versioning: git is the store, SQLite is the index

Every save is **one serialized commit to main** of the scripts repo
(`scripts-service/git.ts:122-163`):

- Layout: `scripts/<owner-uuid>/<name>/{manifest.json, index.ts, …}` with sibling roots
  `dashboards/` and `monitors/` sharing the same commit machinery (git.ts:106-108).
- Commits are **authored as the acting user** (`--author="name <email>"`) with the
  conversation URL in the message (git.ts:145-148) — provenance chain: chat message → commit →
  run row → per-run query activity.
- Push discipline: with `SCRIPTS_REPO_URL` set, a failed push **rolls the commit back**
  (`git reset --hard HEAD~1`) and fails the save visibly — the remote repo is the source of
  truth and local state never silently diverges (git.ts:151-160). The PAT travels as an
  `http.extraheader`, never in the origin URL, so it can't leak into `.git/config` or error
  messages (git.ts:18-25).
- Saves are serialized through a promise chain because concurrent commits to one working
  tree would corrupt the index and per-script `baseSha` checks assume ordering (git.ts:97-104).
- Deletion is a removal commit; history keeps the code recoverable (git.ts:168-198). No-op
  saves (identical content) produce no commit (git.ts:137-143).
- The SQLite side (`scripts-service/store.ts`) is only registry rows and pointers: name,
  immutable resource UUID, `head_sha`, run telemetry, `avg_duration_ms` over the last 20
  successful runs (used by the dashboard loading bars).

There is deliberately **no promotion/versioning UI**: scripts run at head; *pins* (dashboards,
monitors) freeze exact SHAs, so later saves never silently change a deployed artifact
(scripts-design.md §2 "No pointers, no promotion").

### 2.3 The typed-JSON contract

The manifest (`scripts-service/manifest.ts:98-110`) is zod-validated at save:
`description`, `datasources` (the run's grant allowlist), `timeoutMinutes` (capped),
`runtime: "node"` (reserved for a future Python shim — the field exists from day one),
`params` (a full object-root JSON Schema → run forms + dispatch-time validation), and
**`resultSchema`** (JSON Schema the run output must satisfy). On completion the runner
validates the emitted JSON against `resultSchema` before storing it
(`validateRunResult`, scripts-service/runner.ts:219-221) — a script that returns the wrong
shape *fails*, so a dashboard or monitor pinned to it can trust the shape statically.

### 2.4 Execution: process-per-run, credential-free, broker-only I/O

`scripts-service/runner.ts` supervises runs:

1. **Materialize from git at the exact SHA** — `readTree(sha, owner, name)` (runner.ts:117),
   never from a working directory; the pin is what runs.
2. Compile to one bundle (esbuild via `script-compiler.ts`), copy a secret-free harness +
   loader shim into the sandbox (`runner-runtime.ts` — the child must not read the service's
   own module directory, where a computed import could inspect config).
3. `sealSandboxForRunner` chmods/chowns the tree to a dedicated runner group
   (`runner-permissions.ts`) — in production the trusted supervisor runs as root purely so it
   can drop each child to the `argus-runner` OS identity that cannot read `/data`.
4. Spawn Node **detached in its own process group** with
   `--permission --allow-fs-read=<sandbox>` and a custom ESM loader; env contains only
   `PATH`, params, limits, and the entry path — **no credentials of any kind**
   (runner.ts:147-172). Timeout/cancel `SIGTERM`s then `SIGKILL`s the whole group
   (runner.ts:50-66).
5. The child talks to the parent over **IPC only**: `ready` (reached user code — used to
   attribute a non-zero exit to the script vs. the sandbox), `result` (exactly one), and
   `query` messages (runner.ts:184-199).

`ctx.query(source, sql)` is the sole data path: the parent checks the requested source
against the **pinned manifest's `datasources`** (`handleRunQuery`,
scripts-service/runner-query.ts:16-21), then `forwardQuery` POSTs to argus's
`POST /internal/query` with the shared secret and bounded retries on 502/503/504
(scripts-service/broker.ts:5-38). Argus's endpoint (server/http/internal.ts:66-100) re-runs
the **exact same guard + client code as chat** (`runQuery` with `assertReadOnlySql` etc.) but
with script-sized caps — 100k rows / 50MB instead of chat's LIMIT-1000/100k-chars — and
records every call in the ClickHouse activity feed keyed by `runId`. Credentials therefore
exist in exactly one process (argus); the scripts service and every run child are
structurally credential-free. Membership is re-checked at query time
(`hasWriteAccess(actingUser)`, internal.ts:73-75), so an offboarded owner's scripts stop
reaching data immediately.

Concurrency: a `pump()` loop with a `launching` counter dispatches queued runs up to
`maxConcurrentRuns` (default 8) without over-committing during async launches
(runner.ts:89-114). Boot reconciliation marks orphaned queued/running rows `interrupted` and
clears sandboxes — never a silent retry (runner.ts:261-268). Logs/results live only in a
bounded in-process cache (32MiB/15min); durable state is git + light SQLite rows.

### 2.5 Why scripts are the ONLY data path for dashboards — failure modes this kills

`docs/scripts-design.md §11`: a dashboard's "**only** data path is
`runScript<Result>(name, params)` against `manifest.scripts: [{id, name, sha}]` … Direct
dashboard queries do not exist". What that single decision eliminates:

1. **Credential exposure in generated UI code.** Dashboard source is model-authored and runs
   in a browser; if it could query, it would need a credential or an open query proxy. With
   pins, the browser can only trigger pre-committed, read-only, schema-validated programs.
2. **Silent drift.** Pins are `{id, name, sha}` — an exact commit of an immutable resource
   UUID. Editing a script never changes a deployed dashboard until the pin is bumped; the
   name isn't even the identity, so renames can't repoint anything.
3. **Untyped data → runtime UI breakage.** `resultSchema` is enforced at run completion, so
   the dashboard's `runScript<Result>` generic is honest — shape errors fail the *run*, not
   the render.
4. **Dangling references.** Deleting a pinned script is refused with 409; deleting the
   dashboard cascades to its now-unpinned scripts unless another artifact still pins them
   (`scripts-service/cascade.ts`) — dependencies exist to serve what pins them.
5. **Unbounded/unvetted query cost from viewers.** Shared viewers may run only the
   dashboard's immutable pinned scripts; every execution is an ordinary attributed run with
   the same timeouts, caps, and activity trail. There is no ad-hoc SQL surface to abuse.
6. **Latency surprises.** The 10s draft budget plus per-query timings graded at authoring
   time (tools.ts:66-80) means a dashboard's data dependencies were performance-vetted
   before they could be pinned.
7. **N implementations of data access.** Chat, remote MCP, scripts, dashboards, and monitors
   all funnel to one `runQuery` + one source registry — one place for guards, retries, caps,
   and audit (docs/scripts-design.md §9: "Datasource clients or SQL guards in the scripts
   service" is on the deliberately-not-building list).

---

*(Sections 3, 4 and 6 follow — written incrementally.)*
