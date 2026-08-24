# Section prompts

One focused session per section. Each prompt below is self-contained — paste it
as the opening message of a fresh session and nothing else is needed.

**Suggested order.** Data first: the UI's filters and aggregates, the chat's
answers, and every backtest all read from it, and building those on a schema
that is about to change means building twice. Content Creator is independent of
Data and can run in parallel. UI after Data. Backtesting after Data. Telegram
last — you explicitly deferred it.

---

## Shared context — true for every section

Repo `~/Documents/Github/i-test-season`, branch `main`. Companion repo
`~/Documents/Github/FPL-MCP` is the MCP server; it already reads this
warehouse via `FPL_EDGE_DB` (see `tools/edge_tools.py`).

Invariants that are not up for renegotiation:

- **No fabricated data, ever.** Projections are *copied* from sources, scored,
  and blended — never invented. A panel or answer with no data says why; it
  never shows a plausible number.
- **Point-in-time correctness.** `Warehouse.snapshot_at(deadline)` is the only
  sanctioned read of mutable facts. `LeakageError` exists to stop backtests
  reading the future. `escape_hatch_unfiltered(reason)` requires a reason.
- **Identity is the stable player `code`**, never `element_id` (which is
  reassigned each season).
- **Money is integer tenths**, never floats — the 50% sell-on fee floors.
- **DuckDB is single-writer XOR many-readers.** Long reads use
  `Warehouse.read_copy()`.
- Secrets live in `.env` (gitignored). The user runs auth refreshes.
- Scraping is polite: identify, rate-limit, cache raw responses, record
  licensing per source in `docs/data_sources.md`.
- Small commits on a feature branch. Never force-push. **Never push without
  being asked.**
- **A test that has never failed is not a test.** For each meaningful test,
  break the code, watch it fail, restore it. Say so in the commit.

---

## 1. Data

```
You are working on the data layer of the FPL edge engine at
~/Documents/Github/i-test-season. This session is ONLY about data. Do not
touch the UI, the Telegram bot, or the solver.

READ FIRST
- docs/data_sources.md, docs/data_lineage.md, docs/known_weaknesses.md
- docs/platform/projection_providers.md, docs/platform/odds_derivation.md
- fpl_edge/store/ (Warehouse, snapshot_at, read_copy, LeakageError)
- fpl_edge/ingest/
- ~/Documents/Github/FPL-MCP/tools/edge_tools.py — the MCP server that already
  exposes this warehouse. Understand what it can and cannot currently answer.

CURRENT STATE (verify these counts yourself; they are from 2026-08-20)
  fact_player_fixture   113,260   4 seasons; the scoring map reproduces
                                  total_points exactly on every row
  fact_odds             115,025   football-data history + the-odds-api live
  fact_projection        52,141   5 providers, normalised
  projection_normalized  52,141   one row per (provider, code, gw)
  fact_predicted_lineup   1,476   Rotowire predicted XIs
  fact_odds_derived       1,720   anytime->xG-share, clean sheet, team lambda
  content_claim             144   creator claims, player-resolved
  fact_manager_season    12,854
  projection_weight           0   CORRECT: no track record exists pre-GW1
Providers: fplform, gh_blueladd, gh_fplbench, fpl_ep (FPL's own ep_next),
premierinjuries.

GOAL
Make the warehouse something a chat interface and an analytics UI can be
pointed at without a human translating every question into SQL.

SCOPE — in
1. AUDIT the schema against the questions we actually want answered. Write
   docs/platform/data_audit.md listing, for each question below, whether it is
   answerable today and with what query, or what is missing:
     - xPts and xG for player X across all sources, side by side, for GW N
     - which sources disagree most about player X this week
     - aggregate xPts by team / position / price band, filterable
     - a player's underlying stats trend over the last K gameweeks
     - fixture difficulty for the next K gameweeks from OUR ratings
     - ownership and effective ownership, template vs differential
2. BUILD A SEMANTIC LAYER: a small set of documented, stable DuckDB views that
   the MCP server and the UI both query, instead of both reaching into raw
   tables. Name them, document each column, and treat them as an API with a
   compatibility promise. This is the Argus "one guarded query endpoint"
   pattern — see docs/platform/argus_architecture.md.
3. EXPAND SOURCES. Add at least two more projection/xG sources. Priority is
   breadth of independent opinion, not more of the same. Record licensing and
   rate limits in docs/data_sources.md. Copy, never model.
4. FIX the fixture-difficulty gap: the Dixon-Coles fit takes ~1 minute, far
   past a panel's 10s budget, so it must become a CACHED RATINGS ARTEFACT
   written by the nightly job and read cheaply. This is ROADMAP item 6 and the
   single cheapest upgrade to the UI's usefulness.
5. EXTEND THE MCP SERVER in ~/Documents/Github/FPL-MCP so every question in (1)
   is answerable through a tool, against the views from (2).

SCOPE — out
Do not build the projection ENSEMBLE or fill projection_weight. Zero rows is
the honest state until GW1 actuals exist; weighting without a measured track
record is exactly the fabrication this project forbids. Note it as the next
step and stop.

ACCEPTANCE
- docs/platform/data_audit.md exists and every listed question is answerable,
  or explicitly marked missing with the reason.
- The views exist, are documented, and have tests covering point-in-time
  correctness (a query at deadline T must not see facts recorded after T).
- Two new sources are ingested with row counts reported and licensing recorded.
- Fixture difficulty is served from cache within a panel's budget; measure and
  report the actual latency.
- The MCP tools answer the audit's questions; demonstrate each with real output.
```

---

## 2. UI

```
You are rebuilding the web UI of the FPL edge engine at
~/Documents/Github/i-test-season. This session is ONLY about the UI and the
API routes it needs. Do not change the warehouse schema or the solver's maths.

READ FIRST
- web/dist/index.html — the entire current UI, one 12KB file, zero build step
- fpl_edge/platform/app.py — routes: /api/health, /api/panels,
  /api/scripts/{name}/run, /api/query, /api/inbox, /api/monitors, /api/chat,
  /api/chat/stream
- fpl_edge/platform/scripts/{squad,projections,fixtures,prices,ideas}.py
- fpl_edge/platform/{registry,panels,query,inbox}.py
- docs/platform/DESIGN.md sections 2 and 2.1 (the stack decision and the
  pinned v1 API contract)
- docs/platform/argus_architecture.md — the chat and panel patterns to mirror

Run it: `uv run fpl platform serve` then open localhost:8321. It has never
been visually verified in a browser; do that first and report what you find.

REFERENCE PRODUCTS — study what each does well and name the specific
borrowing, do not generically "take inspiration":
- fplreview.com — the transfer planner grid and the solver output format
- livefpl.net — live rank, effective ownership, what the field is doing
- fplai.net and fantasyfootballfix.com — projection presentation
- ffscout / Hub — team news and predicted lineups

GOAL
Go from one page to a multi-view application with, at minimum:
  a. TRANSFER PLANNER — a multi-gameweek grid in the fplreview idiom: plan
     moves across a horizon, see xPts and cost implications update, respect
     bank/FT/hit rules. This is the flagship view.
  b. XPOINTS — the projection table with source comparison, filters (team,
     position, price band, minutes risk) and aggregates.
  c. TOP MANAGERS AND TEMPLATE — what the elite own, template vs differential,
     effective ownership.
  d. FIXTURES — the ticker with real difficulty from our own ratings.
  e. SOLVER — run a solve from the UI, show the plan and why, in the
     fplreview idiom.
  f. CHAT — Argus-style: ask a question, get an answer with tables and charts,
     backed by /api/chat. Deterministic retrieval, LLM for phrasing only.

ARCHITECTURE DECISION (make it explicitly, record it in DESIGN.md)
The current UI is deliberately build-free. A planner and a solver view are
genuinely app-like, so decide: stay zero-build with ES modules, import maps
and hash routing, or adopt a bundler. Recommendation is zero-build — this is a
single-user tool and a build step is a permanent deploy tax — but make the
call in writing with reasons, and do not let index.html grow into one
unmaintainable file either way.

CONSTRAINTS
- Panels remain the ONLY data path. No SQL in the frontend. Every response
  carries provenance (script, repo sha, generated_at) and every panel with no
  data renders its own honest reason.
- Load the `dataviz` skill before building any chart, and RUN its palette
  validator rather than eyeballing colour. Load `artifact-design` for the
  visual system. Both themes must work.
- Verify in the browser with the preview tools: console clean, network clean,
  and screenshot each view. Do not ask the user to check manually.

ACCEPTANCE
- Each view above is reachable, renders real warehouse data, and degrades
  honestly when a source is missing.
- The planner can express a real multi-week plan and its numbers reconcile
  with the solver's for the same inputs.
- Screenshots of every view, at desktop and mobile widths, in both themes.
- No horizontal page scroll anywhere; wide tables scroll inside their own
  container.
```

---

## 3. Content Creator

```
You are working on the content-creator pipeline of the FPL edge engine at
~/Documents/Github/i-test-season. This session is ONLY about ingesting,
storing, indexing and surfacing what creators say. Do not touch the solver,
the UI shell, or the warehouse's match-fact tables.

READ FIRST
- docs/content_sources.md
- fpl_edge/interfaces/creators.py, fpl_edge/interfaces/dossier.py
- the content_claim table (144 rows; the gameweek column is `gameweek`)
- ~/Documents/Github/FPL-MCP — tools fetch_youtube_transcript,
  summarise_fpl_youtube, fpl_creator_consensus, fpl_creator_track_record,
  fpl_player_claims, fpl_content_sources

HISTORY WORTH KNOWING
- Transcription via youtube-transcript-api works; a robots gate must stay
  scoped to the user-shared-link branch only.
- Video URLs must be canonicalised on the video ID — watch?v= and youtu.be
  hashed differently once and produced 44 duplicate claims instead of 22.
- Confidence scores were once keyword-window pseudo-numbers. That was
  correctly called lazy. Conviction must come from semantic analysis of what
  was actually said, with bands (high 0.8 / medium 0.6 / low 0.4) as
  calibration targets, not from keyword counting.
- ASR benchmarking is done: MLX-Whisper measured ~5x faster than
  faster-whisper on this machine. Use the local Python API — do NOT spend
  Anthropic tokens on transcription.

GOAL
Transcripts are currently fetched and thrown away. Make the corpus a durable,
searchable asset.

SCOPE — in
1. DURABLE TRANSCRIPT STORE. One row per (source, video/episode, segment) with
   speaker where available, start/end timestamps, gameweek, creator, and the
   raw text. Never re-fetch what is already stored. Decide and document the
   storage choice (DuckDB alongside everything else is the default; justify
   any deviation).
2. SEARCH AND INDEX. Full-text search across the corpus that returns the
   creator, the gameweek, the timestamp, and a deep link to that moment in the
   video. "What has anyone said about Haaland this week" must be one query.
3. CLAIM EXTRACTION, properly. From stored transcript -> structured claims:
   player, direction (buy/sell/hold/captain/avoid), conviction from semantic
   analysis, the verbatim quote it came from, and a timestamp. Deduplicate on
   video ID. A claim must always be traceable back to the sentence that
   produced it.
4. SCOREBOARD. Per creator, per gameweek: what they claimed, what happened,
   and a track record that accumulates. This is what makes the corpus worth
   more than a summary.
5. PODCAST ASR. Wire MLX-Whisper for sources without transcripts, as a
   time-budgeted nightly backfill. Build the press-conference source
   inventory.
6. SURFACING. Make all of the above reachable through the MCP server so chat
   can answer creator questions, and expose a consensus view (who agrees with
   whom, and who has earned the right to be listened to).

ACCEPTANCE
- Re-running ingestion on the same video does zero network work and creates
  zero duplicate claims. Prove it.
- A search for a player name returns quotes with creator, gameweek and
  timestamp, and the deep links actually open at the right moment.
- Every claim links to its verbatim source sentence.
- The track record is computed from stored claims against stored actuals, with
  the scoring rule written down in docs/.
- No transcription spends Anthropic tokens.
```

---

## 4. Backtesting

```
You are working on the backtesting and idea-evaluation layer of the FPL edge
engine at ~/Documents/Github/i-test-season. This session is ONLY about
evaluating strategies and ideas historically. Do not change the UI or the
ingestion layer.

READ FIRST
- fpl_edge/theses/, fpl_edge/eval/, fpl_edge/oracle/
- docs/theses.md, docs/platform/rank_objectives.md and its result CSVs
- docs/models/simulator.md, docs/models/rank_solver.md
- fpl_edge/store/ — especially snapshot_at, LeakageError,
  escape_hatch_unfiltered
- scripts/oracle_gw1.py, scripts/rank_gw1_solve.py
- the idea inbox (fpl idea) and the MCP tools submit_idea, review_ideas,
  track_ideas, mark_idea_acted

WHAT ALREADY EXISTS
Ideas can be filed and tracked. Theses are versioned and machine-graded. The
rank-objective work is derived and simulated with committed result CSVs. What
is missing is the harness that runs a STRATEGY across history and tells you
whether it would have worked.

GOAL
Turn "I have a hunch" into "here is what that would have scored, and here is
the confidence interval".

SCOPE — in
1. REPLAY HARNESS. Given a strategy (a decision rule: transfers, captaincy,
   chips), replay it across historical gameweeks with strict point-in-time
   correctness — at every decision point the strategy sees only what was
   knowable then. Any leak must raise LeakageError, not silently pass. This
   correctness property is the entire value of the harness; test it adversarially.
2. STRATEGY INTERFACE. A small, documented protocol so a new idea becomes a
   new strategy object without touching the harness.
3. COMPARISON. Run N strategies over the same history with common random
   numbers, and report paired differences with standard errors. Paired CRN is
   what makes small edges measurable; unpaired comparison will drown them in
   season variance. Report rank outcomes (P(top-10k)), not just points —
   the goal is finishing position, not expected points.
4. IDEA -> STRATEGY -> GRADE loop. An idea in the inbox should be expressible
   as a testable strategy, run, and graded, with the result written back
   against the idea so the inbox accumulates evidence.
5. SURFACING. Results as committed artefacts (CSV plus a written finding),
   reachable from the MCP server and ready for the UI. Follow the existing
   pattern in docs/platform/ — a claim, the evidence, and the CSV behind it.

SCOPE — out
Do not tune the projection models to improve backtest results. That is fitting
the past. The harness measures strategies; it is not a model-search loop.

ACCEPTANCE
- A deliberately leaky strategy raises LeakageError; demonstrate it.
- Two strategies compared on the same history report a paired difference with
  a standard error, and the numbers are reproducible from a committed seed.
- At least one real idea from the inbox is run end to end and graded.
- Every reported finding has a committed CSV behind it.
```

---

## 5. Chat — the Argus-grade analyst

```
You are building the CHAT layer of the FPL edge engine at
~/Documents/Github/i-test-season. This session is ONLY about chat: the
agent loop, its tools, and the chat UI pane. Do not change the warehouse
schema, the solver, or the other UI views.

READ FIRST
- docs/platform/argus_architecture.md — THE blueprint. Especially §1 (the
  agent loop: turn-runs-to-completion server-side, persist-then-broadcast,
  SSE re-attach, session recovery from the durable transcript), §1.2
  (explicit typed tool registry, in-process MCP, tools closed over the
  conversation context), §2 (scripts as the typed-JSON primitive the agent
  AUTHORS from chat: params schema + enforced result schema, git-versioned
  with the conversation in the commit message, the 10s budget returned as
  an error WITH remediation text, summary-view results with paged full
  output), and §5 (the credential posture: the agent's environment holds
  no datasource credentials; tools broker all I/O).
- fpl_edge/interfaces/qa.py — the deterministic router (the FAST path;
  it stays: instant answers for known intents, and it now covers manager/
  elite questions). Its Answer type is text + PNG charts.
- fpl_edge/platform/app.py — /api/chat (router) and /api/chat/stream (the
  SSE escalation stub); DESIGN.md §2 point 6: escalation runs headless
  `claude -p` on the MAX PLAN with the fpl-server MCP attached — the
  server holds NO API key. That constraint is absolute.
- fpl_edge/store/views.sql + docs/platform/semantic_layer.md — the ten
  sem_* macros are the agent's query vocabulary.
- fpl_edge/platform/{query,registry}.py — guarded_query (read-only,
  row/byte caps) and the panel registry.
- web/dist/js/views/chat.js and js/app.js — the current pane and shell.
- ~/Documents/Github/FPL-MCP/tools/ — the MCP server the headless agent
  attaches; semantic_tools.py already speaks the macros.

THE ARCHITECTURE (Argus folded onto what exists)
Two speeds, one contract:
1. FAST PATH (unchanged): the deterministic router answers known intents
   in milliseconds. Extend it only where a question below maps cleanly to
   one macro query.
2. AGENT PATH (the build): anything unrouted escalates to a real agent
   session — headless claude on the Max plan with a TOOLBELT, streaming
   SSE to the pane. Argus rules apply:
   - The turn runs to completion server-side; events are persisted to a
     conversation table THEN broadcast; a reload re-attaches to the
     stream; the transcript survives restarts.
   - The agent gets TOOLS, never credentials: every warehouse read goes
     through guarded_query over the sem_* macros (the FPL-MCP server
     already provides six semantic tools + query — attach it, extend it
     where the questions below need more).
   - CHARTS ARE A TOOL: make_chart(spec) -> PNG rendered server-side with
     matplotlib from a small declarative spec (bar/line/scatter, one
     series colour system reused from the router's existing chart code).
     The agent never emits raw image bytes itself. Load the `dataviz`
     skill's rules into the tool's docstring: one hue for magnitude,
     labels always, no rainbow.
   - ANALYSIS SCRIPTS, the Argus crown jewel, v1-simple: a save_analysis /
     run_analysis tool pair that stores small parameterised SQL-over-
     macros scripts (name, description, params schema, the SQL) in a
     repo-committed registry (analyses/*.json or .sql files, committed
     with the conversation id in the message). run enforces the 10s
     budget and returns Argus-style summary views (25 rows + omitted
     count) with a paged full-result tool. "Ask once, keep forever":
     the router can then serve a saved analysis by name instantly.
   - IDEAS: the agent can call submit_idea (the inbox exists) when the
     user says "track that" — never silently.
   - EXTERNAL data: the agent may use the FPL-MCP live-API tools (team/
     entry endpoints) that already exist. No new scraping in this session.

THE SEVEN QUESTIONS — the acceptance suite, each with its enabling path
(verify each end to end in the pane, screenshot the answers):
1. "which player has the highest xPoints in next 3 gws" →
   sem_projection_consensus summed over gws [next..next+2]. Router-able:
   add a deterministic intent (pattern: highest/top xpoints ... N gws).
2. "which top-10-owned midfielder took the most shots in last 4 games" →
   sem_player_match_stats (shots) joined to sem_players (position,
   ownership). HONESTY TRAP: 2026-27 has 1 gameweek of shots data; the
   answer must say "last 4 games spans 2025-26 via official form which
   has no shots — here is GW1 shots + last-4 xG instead" or equivalent
   truthful framing. Agent path.
3. "what are the recommendations from FPLHarry, FPLAndy, FPLWire" →
   content_claim/creator tables (creators are rostered; claims carry
   direction + conviction + quote). Router-able; group by creator.
4. "which players are injured or close to suspension" → sem_players
   (status, news, chance_of_playing) for injuries; suspension proximity =
   accumulated yellow cards from sem_player_form vs the 5-by-GW19 rule —
   READ the rule from the rules registry, never hardcode 5.
5. "plot graph of points per price of all strikers" → agent path:
   guarded_query (sem_player_form totals ÷ price from sem_players,
   position FWD) then make_chart scatter, points-per-£ labelled, value
   printed. The chart returns inline in the pane.
6. "which defenders hit defcon most consistently last 6 games" →
   sem_player_form.defensive_contribution: consistency = share of last-6
   appearances at/above the DEF threshold — thresholds come from the
   rules registry (DEF 10 CBIT, MID/FWD 12), never hardcoded.
7. "what transfers did Ben Crellin make?" → sem_manager_transfers
   (already answers; keep green).

CONSTRAINTS
- No API key on the server, ever: the agent runs through the Max-plan
  claude CLI exactly as DESIGN.md §2.6 states. If the CLI is absent the
  escalation degrades to a clear message, not a crash.
- Answers carry provenance like panels do: which macro/analysis produced
  the numbers, as-of instant.
- The pane renders agent output safely: textContent + the existing table
  parser; images as data URIs; never raw innerHTML (the contract test
  pins this — keep it green).
- A turn that dies (CLI crash, timeout) leaves an honest error in the
  transcript, and the conversation survives to the next question.
- Tests: tool registry (every tool schema-valid), analysis save/run round
  trip with budget enforcement (a slow analysis returns the remediation
  error — Argus's exact pattern), transcript persistence across a
  simulated reconnect, and the seven questions as integration tests
  against a seeded warehouse (router ones exact, agent ones asserting the
  data reached the prompt). Break each meaningful test once, watch it
  fail, restore, and say so.

ACCEPTANCE
- All seven questions answered correctly in the real pane with real
  warehouse data — screenshots of each, charts inline where asked.
- A reload mid-answer re-attaches to the stream (demonstrate).
- One analysis authored BY THE AGENT in chat, saved, re-run by name
  instantly, and visible as a committed file with the conversation id in
  the commit message.
- The router still answers its existing intents in milliseconds; the
  full unit suite is green.
```

## 6. Telegram — deferred, do later

```
You are working on the Telegram interface of the FPL edge engine at
~/Documents/Github/i-test-season. This session is ONLY about Telegram.

Do this AFTER the UI's chat view is working. The point is parity: the bot
should answer with the same retrieval and the same numbers as the web chat,
differing only in rendering. Building it first means building it twice.

READ FIRST
- the deployed bot (launchctl label com.fpledge.telegram; log at
  data/warehouse/jobs/telegram.log)
- fpl_edge/platform/app.py — /api/chat and /api/chat/stream
- docs/platform/argus_architecture.md — the chat pattern
- fpl_edge/jobs/deadline_dag.py — the deadline-relative scheduler and its
  per-task staleness windows

GOAL
Argus-style conversation in Telegram: ask anything the web chat can answer and
get it back as clear text, tables, and charts as images.

CONSTRAINTS
- Deterministic retrieval; the LLM polishes phrasing only. Never let the model
  invent a number.
- The bot must share the web chat's retrieval path, not reimplement it.
- The bot is a long-running process. It holds code in memory: after any change
  to shared modules it MUST be restarted, or it keeps running the old code.
  This has already caused one live auth outage.
- Token refreshes are serialised across processes by a lock on .env.lock. Do
  not add a second refresh path.

ACCEPTANCE
- The same question asked in the web chat and in Telegram returns the same
  numbers.
- Tables and charts render legibly on a phone.
- The bot is restarted and verified after deployment, not assumed.
```
