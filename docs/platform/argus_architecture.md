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

## 3. Dashboards: compile server-side, run in a hostile-code sandbox, feed via pins

A dashboard is React + TS + Tailwind source under `dashboards/<owner>/<name>/` in the same
scripts repo (same commit machinery, same optimistic concurrency). Pipeline:

### 3.1 Compilation (`scripts-service/dashboards-compiler.ts`)

`compileDashboard(files)` produces **one self-contained `index.html`** and enforces the
sandbox statically before anything runs:

- **Typecheck** with the real TS compiler against a generated `argus-dashboards.d.ts`
  (dashboards-compiler.ts:159-188) — 8-error cap, path:line messages the agent can act on.
- **Sandbox lint via AST walk** (dashboards-compiler.ts:78-157): `<form>`/`onSubmit`,
  `localStorage`/`sessionStorage`/`document.cookie`, `fetch`/`XMLHttpRequest`/`WebSocket`/
  `EventSource`, `alert/confirm/prompt`, `window.open`/`target="_blank"`,
  `navigator.clipboard` are all *compile errors* with messages that name the sanctioned
  alternative ("no network; all data comes from runScript", dashboards-compiler.ts:24-32).
  The runtime CSP would block these anyway — linting turns silent runtime failure into an
  authoring-time fix.
- **Import allowlist**: only `react`, `react-dom/client`, jsx runtimes, `recharts`, relative
  imports inside `src/`, and the virtual `@argus/dashboards` SDK module resolve; anything
  else, or a path escaping the source dir, is a build error (dashboards-compiler.ts:15-21,
  190-224). The SDK module is injected from an in-memory string (`APP_SDK_RUNTIME`) — it
  ships `runScript`, `WindowSelector`/`TimeWindow`, `ThemeToggle`, `dashboardStorage`,
  `copyText`, `openLink`.
- esbuild → minified IIFE; Tailwind compiled over the dashboard's own sources *plus* the SDK
  runtime (so shared component classes survive the content scan), with the argus theme
  appended last so a dashboard cannot redefine `:root`/`.dark`
  (dashboards-compiler.ts:253-286). Output is inlined into one HTML string, size-capped
  (dashboards-compiler.ts:287-291).

### 3.2 Save-time smoke check

Before a dashboard save commits, the compiled bundle is rendered in **jsdom inside a forked
child with an empty environment and a capped heap** (`scripts-service/dashboards-smoke.ts`;
README.md:101-103). An empty render, console error, page error, unhandled rejection, timeout,
or excessive button inventory fails the save; a passing run reports the buttons found and the
`runScript` calls their clicks produced (scripts-design.md §11). Model-authored code gets
*executed* at save time, so the fork holds no repo token, shared key, or datasource
credential, and a bundle that never settles is killed rather than wedging the service. The
child stubs a fixed viewport + `ResizeObserver` because jsdom does no layout — without them
every Recharts dashboard would measure itself as 0×0 and fail its own smoke check.

### 3.3 The sandboxed iframe + postMessage bridge

Rendering (`web/src/dashboards/DashboardFrame.tsx`, `bridge.ts`):

- `<iframe sandbox="allow-scripts" srcDoc=…>` — no `allow-same-origin`, so the frame is an
  **opaque origin**: no cookies, no argus API access, nothing another window can match
  (DashboardFrame.tsx:232-240, comment at 60-64).
- A CSP is injected into the compiled HTML at display time:
  `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:
  blob:; …; connect-src 'none'` (bridge.ts:4-6) — belt over the lint's braces: zero network.
- `buildSrcdoc` injects the CSP plus an ES5 bridge runtime into `<head>` (bridge.ts:82-96).
  The bridge exposes `window.argus.runScript(name, params)` → postMessage to the parent, and
  forwards uncaught errors / unhandled rejections / `console.error` up as structured error
  messages the host displays (bridge.ts:9-80); theme flows down the same channel.

### 3.4 Data injection via pinned scripts

The parent side of the bridge is the *only* data plane (DashboardFrame.tsx:115-197): it
validates `ev.source` is its own iframe's contentWindow, calls the authenticated dashboards
API (`api.runDashboardScript(dashboardName, scriptName, params, owner)`) — where the server
resolves the script through the dashboard's **pinned `{id, name, sha}` manifest**, refusing
anything unpinned — polls the run to terminal state, and posts `{ok, result}` back into the
frame. Every call is recorded to a call log that powers both the Data inspection view and the
host-owned progress overlay (per-script bars paced against each script's saved
`avg_duration_ms`, linear to 90% then asymptotic decay — `script-progress.ts`,
scripts-design.md §11). The dashboard never holds a token, never sees SQL, and can request
nothing its manifest didn't pin at an exact commit.

---

## 4. Monitors: deterministic triggers, LLM copy-polish, cron, Inbox + Slack

A monitor = pinned script(s) + a cron schedule + delivery. One entity, two kinds
(shared/monitors-types.ts, scripts-design.md §13):

- **Report** — delivers on every run; an LLM *writes* the report from pinned-script results
  under the owner's `prompt.md` ("the prompt is the product"). 1–10 pins.
- **Alert** — exactly one pinned script decides. No `prompt.md`.

### 4.1 Exactly where the deterministic/LLM boundary sits

**Alert triggering never calls an LLM.** The boundary is a schema contract: an alert's
pinned script must declare `resultSchema` containing `triggered: boolean`, `title: string`,
`body: string` (charts optional). The scheduler reads `{triggered, title, body}` **directly
from the validated script result** — the fire/no-fire decision, the episode state machine,
and recovery detection are all pure data (scripts-design.md §13 "Evaluation flow";
`scripts-service/scheduler.ts`). Historical prompt-driven alerts were force-paused until
edited into the deterministic contract — the migration deliberately removed LLM judgment
from triggering.

The LLM appears in exactly two places, both argus-side (the scripts service holds no LLM
credentials, mirroring how `ctx.query` brokers data):

1. `POST /internal/generate` (server/http/internal.ts:106-137) → `generateMonitorOutput`
   (server/agent/generate.ts): writes **report** copy from prompt + script results using
   `MONITOR_MODEL` (default claude-sonnet-5), forced tool use, prompt-injection hardening,
   ~150k-char result budget. No fabricated fallback: an LLM failure is a *failed evaluation*
   that counts toward auto-pause (internal.ts:103-105).
2. `POST /internal/alert-message` (internal.ts:142-170) → `generateAlertMessage`
   (server/agent/alert-message.ts): after a **fired edge only**, `ALERT_MESSAGE_MODEL`
   (default claude-haiku-4-5) rewrites the script's title/body into a one-headline,
   1–3-sentence incident summary. **Best-effort**: on failure the script's own bounded
   title/body is delivered — "an alert is never dropped for presentation polish"
   (scripts-design.md §13).

Why this split: triggering must be reproducible, auditable, cheap, and immune to model
drift/hallucination — a paged human must be able to read the script and know why it fired;
copy quality is the only thing an LLM adds, so it is confined to prose, applied after the
decision, and allowed to fail without consequence. Cost follows the same gradient (Haiku for
alert copy, Sonnet for reports, Opus only for interactive chat — README.md:120-122).

### 4.2 Cron scheduling and failure containment (`scripts-service/scheduler.ts`)

Note: the shipped scheduler evolved past the design doc in three ways worth knowing —
confirmation before firing, widening repeat notifications, and *no* auto-pause.

- 30s tick (`TICK_MS`, scheduler.ts:29) over `next_due_at`, computed with `croner` —
  timezone-aware, DST-correct; schedules store an explicit timezone (`nextDueAt`,
  scheduler.ts:39-42). `STALE_DUE_MS = 1h`: a due pointer older than that is recomputed
  forward rather than fired, so an outage never burst-fires (scheduler.ts:30).
- A scheduled tick **advances `next_due_at` before dispatch** so a slow evaluation can't
  double-fire; a manual "Run now" must not postpone the next regular delivery
  (scheduler.ts:232-234). Overlap → the evaluation is recorded `skipped` with "previous
  evaluation still running" (idempotent `tryCreate`, scheduler.ts:227-231).
- Each pin runs through the **ordinary runner** (normal run rows, normal concurrency);
  the evaluation awaits all pins within the sum of pinned timeouts plus queue slack
  (`awaitRuns`, scheduler.ts:122-144).
- **Confirmation before firing** (flap suppression): an episode opens only after
  `FIRE_AFTER = 2` consecutive triggering evaluations — but the wait is taken only when the
  confirming run is due within 65 minutes (`confirmationDueSoon`, scheduler.ts:32-33,
  95-100); a slow-cadence monitor fires on first sighting as it always did.
- **Widening repeat notifications**: an open episode re-delivers on a cadence equal to half
  the episode's age, clamped to [1h, 24h] — "no counter, no stored state"
  (`repeatNotifyDue`, scheduler.ts:102-116). A three-day-old condition cannot have been
  silent for three days.
- **No auto-pause** (design doc §5/§13 said 5-failure auto-disable; the code deliberately
  reversed it): "A monitor never disables itself: failures streak on the row as evidence
  for whoever reads it, but the schedule keeps running, so a check that broke because its
  infrastructure broke starts working again on its own the moment that infrastructure does"
  (scheduler.ts:24-27). Infra faults are still distinguished from monitor-actionable ones
  (shared/monitor-failure.ts) so the streak measures the monitor, not the platform.
- **The deterministic alert protocol is validated in code**, not just schema:
  `alertOutput()` (scheduler.ts:174-211) requires boolean `triggered`, string title/body
  (non-empty on a delivered edge), ≤2 validated chart specs, and an optional `observations`
  object of finite numbers — the tuning series stored on *every* run, quiet ones included,
  "exactly the ones a threshold needs" (scheduler.ts:148-169). Its synthesized "model" is
  literally `"code"` with `costUsd: 0` (scheduler.ts:206-207).
- Alert episodes: registry row carries `alert_state` (`healthy`/`alerting`),
  `alerting_since`, `alerting_eval_id`. While alerting, `triggered=true` → `still_alerting`
  (suppressed); the first `triggered=false` delivers the recovery note and closes the
  episode. Each evaluation records its edge as `outcome ∈ {fired, quiet, still_alerting,
  recovered}`. Manual fires are quiet test runs — Inbox only, no state change, no Slack.

### 4.3 Delivery: durable outbox → Inbox + Slack

- `deliver()` (`scripts-service/deliver.ts`) commits each hand-off to a **durable SQLite
  outbox in the same transaction as evaluation completion**; a worker retries
  `POST /internal/delivered` until argus acknowledges (internal.ts:175-193 — a 5xx keeps the
  item pending, and the handler `handleDelivered` in server/triggers/delivered.ts is
  idempotent). Delivery survives either service dying mid-hand-off.
- **Inbox**: the `evaluations` table *is* the inbox — one row per delivered evaluation,
  newest first, bounded (90 days / 500 per monitor; delivered report editions retained 365
  days as the report's archive). Unseen badge is a client-side localStorage high-water mark —
  deliberately no server-side read tracking. Each delivered edition keeps a durable
  `/inbox/:id` route where the underlying script data can be inspected.
- **Slack**: manifests may carry up to ten channel IDs; only *scheduled* evaluations publish.
  The bot posts the same canonical title/body stored in history. Recovery posts a ✅
  **threaded reply in every fired destination** — argus records each root thread so
  recovery/investigation fan back to the same places (server/triggers/report-delivery.ts).
- **Portable charts**: script results may include fenced chart specs
  (shared/chart-spec.ts) that render interactively in the Inbox and are re-rendered per
  destination: a fired alert maps up to two specs onto native Slack line/bar blocks (with
  Slack's stricter constraints validated when the result is read, and a text-only retry if
  Slack rejects them); a report — whose charts routinely exceed what those blocks accept —
  is rendered through the shared headless renderer (shared/chart-render.ts) and uploaded as
  images into the message's thread. Images are best-effort; the message must read without
  them. One spec, three renderers (web/src/chat/ChartBlock.tsx, Slack blocks, headless PNG) —
  the spec, not any renderer, is the contract.
- Optional `investigate` flag opens a pre-seeded conversation from a delivery — the alert →
  investigation loop stays in-product.

---

### 4.4 Delivery mechanics worth copying verbatim

`scripts-service/deliver.ts` is a textbook durable outbox in ~130 lines:
`INSERT OR IGNORE` keyed by evaluation id, enqueued **in the same SQLite transaction as
evaluation completion** ("neither record can commit without the other", deliver.ts:125-128);
a 30s worker retries `POST /internal/delivered` with exponential backoff capped at 1h
(deliver.ts:51-53); the receiver must return `{delivered: true}` — a 2xx without the
acknowledgement body still retries (deliver.ts:76-88); non-retryable 4xx marks `failed`
instead of looping (deliver.ts:89-93); delivered/failed rows are pruned after 30 days.

---

## 6. Mapping Argus onto the FPL platform

The FPL side's existing Python core (`fpl_edge/`) already has the *engine* half of what
Argus pairs with a product shell. Its surfaces, briefly:

- **Warehouse** (`fpl_edge/store/warehouse.py`): DuckDB, chosen for native `ASOF JOIN` /
  `QUALIFY` point-in-time reads (module docstring, warehouse.py:1-16). Single-writer by
  design (`WarehouseLockedError`, warehouse.py:50-51); readers open read-only.
- **`snapshot_at(as_of)`** (warehouse.py:404): the *only* sanctioned read path for model
  inputs. `Snapshot` raises `LeakageError` if you touch `.warehouse` directly, and
  unfiltered access requires `escape_hatch_unfiltered(reason)` with a ≥20-char justification
  so intent is greppable (warehouse.py:73-113). `ConflictingFactError` refuses two different
  values for one entity at one instant (warehouse.py:54-64).
- **QA router** (`fpl_edge/interfaces/qa.py`): `QuestionRouter.route(text)` — regex-intent
  conversational access over Telegram ("review my team", "top mids", creator queries,
  fixture targets…), fully offline-testable, deliberately not an LLM (qa.py:1-14).
- **Idea inbox** (`fpl_edge/interfaces/inbox.py`): `IdeaInbox.submit(text)` is "the seam
  every surface goes through" — Telegram, CLI, and the MCP server all call it; ordering is
  load-bearing (parse → persist thesis → persist context → *then* verdict, so a slow/failed
  model can never lose the thought, inbox.py:10-22).
- **Report** (`fpl_edge/interfaces/report.py`): assembled from `register_section(name,
  render, priority)` — sections owned by other subsystems arrive by registration, and the
  report *states which parts are missing* rather than looking falsely complete
  (report.py:1-17, 61-68, 145-147).
- **Jobs** (`fpl_edge/jobs/post_gw.py`): the post-gameweek settlement job — refresh
  snapshot, pull odds, settle idea observations, score creator claims, crawl elite picks,
  re-render retro report — every step isolated and idempotent, sequential because DuckDB is
  single-writer (post_gw.py:1-23). Scheduled by launchd (deploy/).
- Plus `opt/` (solver), `sim/`, `oracle/`, `intel/`, `myteam/`, and an MCP server exposing
  the engine's tools to coding agents.

### 6.1 Concept-by-concept mapping

| Argus concept | FPL analog | Notes / impedance |
|---|---|---|
| ClickHouse/Postgres **query tools** (source registry, one adapter per transport, `assertReadOnlySql`) | **DuckDB warehouse query tools** — one source over `fpl.duckdb`, plus sources for the intel/ideas SQLite stores and the FPL API | Direct fit, with one FPL-specific twist Argus doesn't have: leakage. The warehouse's `snapshot_at` discipline must survive the tool boundary — the query tool should take an `as_of` and route through `Snapshot`, or expose the raw SQL surface only for present-time analysis. Argus's "guard code lives with the creds, every surface funnels through one `runQuery`" (server/http/internal.ts:17-21) is the pattern: one Python query function with the leakage guard, called by chat, scripts, and dashboards alike. |
| **Agent loop** (SDK session per conversation, resume, SSE turn streaming, replayable buffer) | The platform's chat pane over the FPL engine | Direct fit; the Agent SDK has both TS and Python variants. Argus's turn-as-server-side-job + replay-buffer + boot recovery (server/agent/turns.ts) is stack-independent design. Single-user drops the membership machinery entirely. |
| **Scripts** (git-versioned typed-JSON data functions, pinned by SHA) | Named, versioned warehouse queries/analyses: "next-6-GW projections for my squad", "price-change candidates", "creator consensus deltas" — authored in chat, pinned into dashboards and jobs | The contract maps cleanly (params JSON Schema → run form; resultSchema → typed result). The runtime is the mismatch — see §6.2. Argus reserved `runtime:` in the manifest from day one for exactly this (scripts-design.md §4, manifest.ts:107). |
| **Internal query endpoint / broker** (creds live in one process; scripts-service and children are credential-free) | A thin engine-API seam: whatever executes scripts brokers reads to the process that owns the DuckDB file | For FPL the "credential" is mostly *the write lock and leakage discipline*, not secrets. A broker keeps single-writer DuckDB semantics honest: script children never open the file, they ask the one owner. |
| **Dashboards** (compiled React, sandboxed iframe, data only via pinned scripts) | Squad dashboard, price radar board, fixture ticker, mini-league tracker | The whole §3 pipeline is reusable as-is if the platform UI is TS. The "scripts are the only data path" rule matters just as much here: a dashboard that could `SELECT` freely would bypass `snapshot_at` and the single-writer lock. |
| **Monitors: alerts** (one deterministic script → `{triggered, title, body}`; LLM only polishes fired copy) | **Price radar** (predicted rises/falls on my targets), injury-news flags, deadline reminders | Near-perfect fit. Price-change detection *must* be deterministic and auditable; an LLM has no business deciding whether Haaland's price is about to rise. The `observations` tuning series (scheduler.ts:148-169) maps to storing the ownership-delta numbers every quiet run, which is exactly what threshold-tuning a price model needs. |
| **Monitors: reports** (cron + pinned scripts + `prompt.md` → LLM-written edition) | The **weekly decision report** and pre-deadline brief | fpl_edge already has the deterministic half (`register_section`); Argus shows how to add an LLM-written layer *on top of* pinned deterministic data without letting the LLM near the data collection. Its "no fabricated fallback — an LLM failure is a failed evaluation" rule (internal.ts:103-105) matches the report's own "admitting the gap beats looking complete" philosophy (report.py:14-17). |
| **Cron scheduler** (croner, tz, overlap-skip, stale-forward) | **Deadline-DAG jobs** — post_gw settlement, pre-deadline T-24h/T-1h passes | Impedance: Argus schedules are pure cron; FPL scheduling is *event-relative* (deadlines move, BGW/DGW). The seam is small — `nextDueAt()` (scheduler.ts:39-42) is one function; an FPL version computes next-due from `dim_event` deadlines instead of a cron string. Everything downstream (tick loop, overlap skip, outcome rows) transfers unchanged. post_gw.py's isolated-idempotent-steps discipline stays regardless. |
| **Inbox + Slack delivery** (durable outbox, canonical message, threaded recovery) | **Telegram + in-app inbox** | The outbox (deliver.ts) and canonical-message design transfer directly; Telegram replaces Slack as the destination adapter — `deliver()` was explicitly designed as the multi-destination seam (scripts-design.md §12). Threaded recovery replies map to Telegram reply-to-message. The existing Telegram bot's security stance (exact-token command table, text-is-data — telegram.py:16-30) must survive the upgrade. |
| **Portable chart spec** (one fenced spec → interactive web, Slack blocks, headless PNG) | Same spec → web inbox, Telegram photo (headless PNG render → `sendPhoto`) | shared/chart-spec.ts + chart-render.ts are the reusable pieces; Telegram's constraints slot in where Slack's stricter block rules do. |
| **Provenance/audit** (commit author + conversation URL; ClickHouse activity with runId) | Idea/thesis provenance already exists (ideas keyed to submission context); extend to scripts: which chat produced this pinned query | Lighter-weight for one user, but the chain chat → commit → run → query log is what makes "why did the bot tell me to sell?" answerable months later. |
| **AUTH_MODE / membership / sharing** | Mostly drops away (single user); keep `AUTH_MODE=none` posture + Telegram chat-id allowlist as the only identity gate | Argus proves the same build can serve both postures; the FPL platform only needs the local one, but keeping the seam costs nothing. |
| **GitHub/Linear per-user write-back** | No analog needed | Skip; the pattern (structural denial over policy: withhold the credential that would enable the dangerous verb) is still worth remembering. |

### 6.2 The stack question — facts both ways, no verdict

The core mismatch: **fpl_edge is Python** (pandas/DuckDB/httpx, launchd jobs, a Telegram
bot), **Argus is TypeScript end-to-end** (Fastify + React + Node script runner, three
deployables in one npm workspace with shared wire types in `shared/`).

**Option A — adopt/adapt the Argus TS stack as the platform shell.** What it costs:

- A **bridge to the Python engine** becomes mandatory. The natural shape is exactly Argus's
  own broker pattern: a small HTTP surface on the Python side (`/internal/query` with
  snapshot semantics, plus endpoints for solver/sim/report calls) that the TS shell and
  script runner call — Argus already proves this seam works and keeps credentials/locks in
  one process (scripts-service/broker.ts → server/http/internal.ts). Cost: defining and
  maintaining that API; every new engine capability needs a bridge endpoint.
- **Scripts execute in Node.** Pure-SQL scripts work immediately (DuckDB SQL over the
  broker is expressive — ASOF joins etc. live in SQL, not pandas). But anything needing
  the Python model stack (projections, solver, sim) can't be *authored as a script* — it
  becomes a broker endpoint instead, or waits for the `runtime: "python"` shim Argus
  designed for but never built (scripts-design.md §4: "A future Python runtime reuses the
  same broker protocol with a different shim"). The sandbox story (Node `--permission`,
  process groups, runner UID) would need a Python equivalent for that shim.
- What it buys: §1's turn machinery, §3's entire dashboard pipeline (compiler, smoke check,
  iframe bridge), §4's scheduler/outbox/inbox, and the chart spec — all shipped, tested,
  and hardened (the details in this doc — optimistic-concurrency retries, push rollback,
  jsdom viewport stubs, ack-required outbox — are months of accumulated correctness).
- Duplication risk: two languages, two lint/test/CI stacks, wire types defined twice
  (Argus's `shared/` types would need Python mirrors or codegen for the bridge).

**Option B — build a thin new UI over the Python core.** What it costs:

- Rebuild, in Python (FastAPI/Starlette + the Python Agent SDK), the parts of Argus you
  want: SSE turn streaming with replay buffers, in-process tool registration, the scripts
  contract (git commits, pins, runner), the scheduler + outbox, the inbox. None is
  individually hard; §1–§4 of this doc is effectively the spec. The genuinely expensive
  piece to re-create is the **dashboard pipeline** — server-side TS typecheck + sandbox
  lint + esbuild + jsdom smoke is intrinsically a TS toolchain even if the host is Python
  (you'd shell out to Node for compilation, which is workable but a new seam of its own).
- What it buys: the engine's surfaces stay native — scripts *are* Python, with direct
  (broker-mediated) access to Snapshot, the solver, and sim; no bridge API to maintain; one
  language, one test suite; the leakage guard stays a Python type-system-and-convention
  property instead of crossing a serialization boundary.
- Loss: everything in Argus's `web/` and `scripts-service/` is reference material rather
  than running code; a React (or HTMX/lighter) front end still has to exist for dashboards
  regardless, so "one language" is never fully true.

**Facts that cut across both options:**

- The *decisions* transfer either way and are stack-free: scripts as the only dashboard
  data path; pins by immutable-id + SHA; deterministic triggers with LLM-copy-only;
  durable outbox with required acknowledgement; explicit registries over auto-discovery;
  turn-as-server-side-job; latency budgets enforced at authoring time.
- Argus itself is single-instance, SQLite-backed, and one-box — the same operational class
  as fpl_edge today. Neither option changes deployment complexity much.
- fpl_edge already exposes an MCP server; in Option A the TS shell's agent can consume it
  remotely (Argus's own remote-MCP path, server/tools/mcp.ts, shows the shape), which is a
  lower-commitment bridge than a bespoke HTTP API — at chat-shaped rather than
  script-shaped result caps (the distinction internal.ts:17-24 exists to fix).

---

## Reading map (where to look first when building)

| Concern | Files |
|---|---|
| SDK session, options, resume, recovery | server/agent/run.ts, session-recovery.ts |
| Turn lifecycle, SSE, drain, boot recovery | server/agent/turns.ts, server/http/routes/chat.ts |
| Tool families + registration | server/agent/tool-registry.ts, mcp-servers.ts, server/tools/agent-tools.ts, docs/adding-tools-and-sources.md |
| Query sources + guards | server/tools/query/{source-registry,run,family}.ts, adapters/, sources/ |
| Readonly enforcement | server/agent/readonly.ts, server/config.ts:43-50 |
| Scripts contract + tools | server/tools/scripts/tools.ts, shared/authoring-contract.ts |
| Git store | scripts-service/git.ts, store.ts |
| Runner + sandbox + broker | scripts-service/{runner,runner-runtime,runner-permissions,runner-query,broker}.ts, server/http/internal.ts |
| Dashboards | scripts-service/{dashboards-compiler,dashboards-smoke,dashboards-sdk}.ts, web/src/dashboards/{DashboardFrame,bridge}.tsx/ts |
| Monitors | scripts-service/{scheduler,deliver,monitors-store}.ts, server/agent/{generate,alert-message}.ts, server/triggers/ |
| Charts | shared/{chart-spec,chart-render,chart-theme}.ts |
| Design rationale | README.md, docs/scripts-design.md |
