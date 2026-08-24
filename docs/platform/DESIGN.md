# i-test Platform — Design

Status: Phase 1 (research fan-out in flight). Sections marked SYNTHESIS-PENDING
are completed only after the independent research reports land; nothing below
those markers is final.

## 0. Objective and thesis

Upgrade the engine into a deployed decision platform for entry 4490171,
objective P(top-10k), stretch top-1k. Four load-bearing commitments:

1. **Fetch once.** Every source lands in DuckDB with provenance; analysis never
   refetches. (Already the warehouse's law; the platform extends it to every
   new source.)
2. **Copy projections, never invent them.** Free xMins/xPts sources are
   ingested behind one schema and blended by measured track record. The
   in-house minutes/points models become one more source to be beaten, not the
   centerpiece.
3. **Rank-aware optimization.** The solver's objective is the rank
   distribution, state-dependent (differential-seeking behind, template-
   protection ahead), with captaincy, hits and chips priced in rank terms.
4. **Discipline encoded.** A policy layer wraps recommendations: presser-day
   gating, banked-transfer value, knee-jerk detection, formal hit thresholds,
   process-over-results reporting.

## 1. What Argus is (first-hand reading)

Read: README.md, docs/scripts-design.md (199 lines, phases + shipped notes),
docs/adding-tools-and-sources.md, web/src layout. The five decisions that
carry the system:

1. **The agent is the authoring surface; artifacts are git.** Scripts (typed-
   JSON data producers) are written in chat, saved as direct commits by a bot
   author, run at head; dashboards and monitors pin exact SHAs so later saves
   never silently change what a consumer renders (scripts-design §2, §11).
2. **One data path.** Dashboards may obtain data ONLY through
   `runScript(name, params)` against pinned scripts; scripts may obtain data
   ONLY through `ctx.query` → a broker → the server's single guarded query
   endpoint (same guard code as chat, script-sized caps). No direct SQL from
   the UI, no credentials anywhere near user code (§3, §4, §11).
3. **Monitors split determinism from prose.** An alert's *trigger* is exactly
   one deterministic script returning `{triggered, title, body}`; the LLM only
   polishes fired-message copy (haiku), and generation failure falls back to
   the script's own text — an alert is never dropped for presentation (§13).
   Reports are LLM-written but only over pinned script results.
4. **Credential-agnostic build.** The same image serves a locked-down shared
   deployment and a full-权 local clone purely via env (AUTH_MODE,
   AGENT_TOOLS_PROFILE); nothing read-vs-write is hardcoded (README).
5. **Failure semantics are product.** Idempotent dispatch, interrupted-not-
   retried on restart, consecutive-failure auto-pause, overlap skipping,
   humane cron UI, Debug-in-chat from every failed run (§5, §6).

## 2. Stack decision — DECIDED: thin Python-core platform (Option B)

Decided 2026-08-20 after the Argus study (docs/platform/argus_architecture.md
§6.2 lays out both options without a verdict; this section is the verdict).

**Build `fpl_edge/platform/` (FastAPI) + `web/` (React+Vite+TS), adopting
Argus's structural rules natively rather than adapting its TS server.**

Why B over A (adapt Argus stack):
- The platform's competencies — warehouse with PIT snapshots, MILP solver,
  Monte Carlo sim, calibration, jobs, Telegram — are all Python. Under A,
  every panel, monitor trigger and solver call crosses a language bridge that
  must be built anyway (Argus's own broker pattern); the bridge IS most of B.
- Argus's "credential holder" concept maps here to *the DuckDB write lock and
  the leakage discipline*, not secrets. One Python process already owns both.
- Scripts that matter to us (projections, solver runs, sim studies) need the
  Python model stack; under A they cannot be scripts at all (Argus reserved
  `runtime: "python"` but never built it).
- A's real payoff is user-authored dashboards machinery (compiler, jsdom
  smoke, sandbox). We have ONE user and a fixed panel set: that machinery is
  the most expensive part of A and the least needed here.

What we adopt from Argus structurally (the audit checks these):
1. **Panel scripts are the only data path.** Every UI panel gets data solely
   from a registered, typed panel script (`fpl_edge/platform/scripts/*.py`,
   params/result validated by JSON Schema, versioned by this repo's git; every
   response carries {script, repo_sha, generated_at, as_of} provenance).
   The frontend has no SQL surface.
2. **One guarded query path.** `/api/query` (and the chat agent's warehouse
   tool) execute read-only, single-statement SQL via Warehouse.read_copy;
   time-scoped panel scripts route through snapshot_at. Guards live where the
   lock lives.
3. **Monitors: deterministic trigger, LLM polish only.** Triggers are Python
   scripts returning {triggered, title, body, charts?}; the LLM (Max-plan
   claude CLI) may rewrite copy after a fire and write report prose over
   pinned results; LLM failure falls back to the deterministic text; a trigger
   never calls an LLM.
4. **Durable outbox delivery.** Evaluations commit to an outbox table in the
   same transaction; a worker delivers to the in-app Inbox and Telegram
   (reply-threaded recovery), retrying until acknowledged. Canonical message
   stored once, rendered everywhere.
5. **Event-relative scheduler.** Argus's cron tick with overlap-skip and
   stale-forward, but next_due computed from dim_event deadlines (UTC) —
   the one-function seam §6.2 identifies.
6. **Chat.** v1: the existing deterministic QuestionRouter answers instantly;
   an "ask the agent" escalation runs headless `claude -p` (Max plan) with the
   fpl-server MCP attached for warehouse/codebase questions, streamed to the
   pane. No API key anywhere.

### 2.2 Frontend stack — DECIDED 2026-08-24: zero-build, superseding §2's "React+Vite+TS"

§2 named React+Vite+TS for `web/`; what shipped and what stays is
**zero-build**: plain ES modules served as-is from `web/dist/`, hash routing,
no bundler, no node_modules, no build step. Reasons, in order:

1. One user, one deploy target (this Mac, launchd). A bundler is a permanent
   deploy tax paid on every change, bought to solve problems (code-splitting
   at scale, dependency graphs, JSX) this app does not have.
2. The failure mode that matters here is "the dashboard is broken at T-30m
   before a deadline". `cp`-is-the-deploy has no build to fail.
3. The app IS app-like (planner, solver) — that argues for *modules*, not for
   a compiler. Structure: `index.html` is a shell only; `js/app.js` holds the
   API client/router/shared components; each view is one file in `js/views/`.
   `index.html` growing back into one unmaintainable file is the failure §2.2
   exists to prevent.

Revisit only if a real dependency (a charting lib, a framework) is adopted —
and the burden of proof is on the dependency.

### 2.1 Platform API contract (v1)

- `GET  /api/panels` → registered panels with their pinned script names.
- `POST /api/scripts/{name}/run` {params} → {result, provenance} (10s budget,
  Argus's draft-run rule; long jobs are monitors, not panels).
- `POST /api/query` {sql, as_of?} → guarded read-only result (row/byte caps).
- `GET  /api/inbox` / `POST /api/inbox/{id}/ack` — deliveries, newest first.
- `GET  /api/monitors` / `POST /api/monitors/{name}/run` — definitions + manual eval.
- `POST /api/chat` {text} → router answer | SSE stream for agent escalation.
- Static: serves the built web/ bundle.

## 3. Deadline DAG (schedule spine) — draft

All times computed from dim_event.deadline_utc (UTC is the only authority):

| Offset | Job | Delivery |
|---|---|---|
| T-30h | presser/projection refresh (content ingest --only presser sources; projections refetch) | Inbox + Telegram digest |
| nightly 02:00 UK | price-change radar (net-transfer velocity snapshot) | Telegram if movers |
| T-4h | final solve (ensemble → rank-aware solver → discipline layer) | Telegram + Inbox with rationale |
| T-90m | confirmed-lineup captaincy check (lineups vs picked captain) | Telegram alert only if action needed |
| post-GW (existing post_gw job) | settlement: score sources/creators/ideas, refresh elite, reports | fpl-reports commit |

Implementation: a single `fpl_edge/jobs/deadline_dag.py` runner invoked by
launchd every 10 min; computes due tasks from (now, next deadline), records
firings idempotently in a warehouse table so restarts never double-send.

## 4. Non-goals (this build)

- No bespoke projection models (thesis #2). Existing internal models remain as
  ONE source with a track record like any other.
- No multi-user auth; the platform is single-operator, local-first.
- No paid data integrations beyond schema+stub+instructions.
