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

## 2. Stack decision — SYNTHESIS-PENDING (framework below)

Two candidates, decided after the Argus-study agent's mapping doc lands:

A. **Adapt the Argus TS stack**: keep server/web/scripts-service, replace the
   ClickHouse/Postgres brokers with a DuckDB query endpoint, port tools.
   - For: shipped UI machinery (chat streaming, dashboards sandbox, monitors
     pages, inbox) is ~free; scripts service semantics proven.
   - Against: the platform's competencies (warehouse w/ PIT snapshots, MILP
     solver, Monte Carlo sim, calibration, jobs, Telegram) are ALL Python.
     Every panel and monitor trigger would cross a language boundary to reach
     them; the Claude Agent SDK loop duplicates what the Max-plan CLI already
     provides; 547 TS files of surface for one user.

B. **Thin new UI over the Python core**: FastAPI + React/Vite in-repo,
   adopting Argus's four ideas natively:
   - typed-JSON **panel scripts** in `fpl_edge/platform/scripts/` (plain
     Python, registered like report sections; the repo itself is the git
     versioning; panels declare which scripts they pin);
   - one guarded **read-only query endpoint** over Warehouse.read_copy for
     chat + ad-hoc panels;
   - **monitors** = deterministic Python trigger scripts on the deadline DAG
     + LLM copy-polish via the Max-plan CLI; deliveries to in-app Inbox AND
     the existing Telegram bot;
   - **credential posture**: server holds .env; browser and panel scripts see
     nothing; chat agent runs with read-only tools by default.
   - Against: dashboards/chat UI built from scratch (mitigated: one user, a
     handful of panels, no multi-tenant auth needed).

Decision criteria: time-to-first-real-panel, honesty of empty states, reuse of
existing tested Python surfaces, deployability under launchd like the bot.

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
