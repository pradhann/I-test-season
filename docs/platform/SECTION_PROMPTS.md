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
schema, the solver's maths, or the other UI views.

THE GOAL, STATED PLAINLY
An analyst you can ask ANYTHING. Not a menu of templated intents — the
current router's fixed patterns ("review my team", "find ideas") are
exactly the ceiling this session removes. The user must be able to ask a
question nobody anticipated, spanning any of the data the engine holds,
and get a correct, data-backed answer with tables and charts as needed —
or an honest "that data does not exist, here is the nearest thing". The
agent must also be able to SUGGEST TRANSFERS INDEPENDENTLY, reasoning
over projections, ownership, fixtures, form and the user's actual squad,
and grounding itself in the solver's machinery rather than vibes.

THE ANTI-TEMPLATING RULE (absolute)
You may NOT satisfy any acceptance question by adding a regex intent,
keyword pattern, or per-question handler. The existing router stays as a
millisecond cache for its current intents and gains NOTHING new. Every
new capability must come from the AGENT + TOOLS, generalising. The test
of success is questions you never saw — see acceptance.

READ FIRST
- docs/platform/argus_architecture.md — THE blueprint. §1: the agent
  loop (turn runs to completion server-side, persist-then-broadcast, SSE
  re-attach, session recovery from the durable transcript). §1.2: an
  explicit typed tool registry, tools closed over conversation context.
  §2: scripts as the typed-JSON primitive the agent AUTHORS from chat —
  params schema + enforced result schema, git provenance back to the
  conversation, the 10s budget returned as an ERROR with remediation
  text, summary views with paged full results. §5: the credential
  posture — the agent environment holds no datasource credentials; tools
  broker all I/O.
- fpl_edge/interfaces/qa.py (the router that stays as-is),
  fpl_edge/platform/app.py (/api/chat, /api/chat/stream stub; DESIGN.md
  §2.6: escalation runs headless `claude -p` on the MAX PLAN with MCP
  attached — the server holds NO API key, ever).
- fpl_edge/store/views.sql + docs/platform/semantic_layer.md — the ten
  sem_* macros. fpl_edge/platform/{query,registry}.py — guarded_query
  and the panels. fpl_edge/myteam/recommend.py + scripts/gw1_squad.py —
  the transfer recommendation machinery the agent will call as a tool.
- ~/Documents/Github/FPL-MCP/tools/ — the MCP server the headless agent
  attaches (semantic_tools.py already speaks six macros).
- web/dist/js/views/chat.js + js/app.js — the pane and shell.

WHAT MAKES IT INTELLIGENT (build these; this is the session)

1. THE WAREHOUSE BRIEFING — the agent cannot query what it does not know
   exists. Generate, at session start, a compact live briefing injected
   into the agent's system prompt: every sem_* macro with its columns and
   grain, current coverage (which seasons/GWs actually have rows, per
   feed), the metrics vocabulary (xG vs xPts vs EO vs DEFCON, and which
   source each comes from), the rules registry highlights (hit cost,
   suspension threshold, DEFCON thresholds), known honest gaps (no
   forward xMins, shots only since 2026-27 GW1, transfers empty until
   GW2), and the user's entry id + current squad summary. Build it as a
   function that reads the live warehouse — never a hardcoded string
   that rots. This briefing is the difference between an agent that
   guesses table names and one that writes correct queries first try.

2. THE TOOLBELT (typed registry, Argus-style; each tool's docstring
   teaches its correct use):
   - query(sql, as_of?) — guarded_query over the macros: read-only,
     row/byte-capped, errors carry remediation ("filter in SQL, the cap
     is N rows"). This is the workhorse; everything is reachable from it.
   - make_chart(spec) — server-side matplotlib from a declarative spec
     (bar/line/scatter/heatmap, one series-colour system shared with the
     router's charts; dataviz rules in the docstring: one hue for
     magnitude, values labelled, no rainbow). Returns a PNG the pane
     renders inline. The agent NEVER emits image bytes itself.
   - run_panel(name, params) — the registered panels, for anything a
     panel already answers well (squad, difficulty, radar).
   - suggest_transfers(constraints?) — wraps the myteam recommend
     machinery: the user's real squad, bank, FTs, the consensus forecast,
     rank-aware scoring; returns the ranked candidate moves WITH the
     alternatives each beat. Optional constraints (max hits, must-keep,
     horizon). The agent composes this with its own analysis (ownership,
     fixtures, creator sentiment) into a reasoned recommendation — the
     tool grounds the numbers so the agent never invents an xPts.
   - save_analysis / run_analysis / list_analyses — the Argus crown
     jewel, v1: parameterised SQL-over-macros scripts with a name,
     description, params schema; committed to the repo with the
     conversation id in the message; run enforces the 10s budget
     (over-budget = error + remediation text, Argus's exact pattern);
     results come back as summary views (25 rows + omitted count) with a
     paged full-result tool. Ask once, keep forever.
   - submit_idea(text) — the existing inbox, only when the user asks to
     track something. list_creator_claims(creator?, player?) — the
     content tables. get_entry(entry_or_name) — live FPL API via the
     existing FPL-MCP tools for any public manager.
   - NO other network access. No credentials in the agent environment.

3. THE LOOP — Argus discipline on our stack: headless Max-plan claude
   with the toolbelt via MCP; conversation + events persisted to the
   warehouse-adjacent store BEFORE broadcast; SSE streaming with
   re-attach after reload; session recovery from the transcript; a dead
   turn (CLI crash, timeout) leaves an honest error and the conversation
   survives. Multi-turn: follow-ups keep context ("now only defenders",
   "chart that instead").

4. THE PANE — the existing chat.js grows: streamed tokens, inline
   charts, collapsible tool-call traces (Argus's CommandTrace idea: the
   user can see WHICH queries produced the answer — provenance is the
   product), and a stop button. Rendering stays injection-safe
   (textContent + the table parser; the contract test pins it).

EXAMPLE QUESTIONS — calibration probes, NOT the spec. The build must
generalise far beyond these; do not special-case any of them:
- "which player has the highest xPoints in next 3 gws"
- "which top-10-owned midfielder took the most shots in last 4 games"
  (honesty trap: shots exist only since 2026-27 GW1 — the answer says so)
- "what are FPLHarry / FPLAndy / FPLWire recommending"
- "which players are injured or close to suspension" (suspension = the
  yellows rule READ from the rules registry, never hardcoded)
- "plot points per price of all strikers" (chart inline)
- "which defenders hit DEFCON most consistently last 6 games"
- "what transfers did Ben Crellin make"
- "should I sell Isak? who replaces him?" (suggest_transfers + analysis)
- "compare my squad's fixtures to the template's next 4"
- "make me an analysis I can rerun each week: xG overperformers"

CONSTRAINTS
- No API key server-side; absent CLI degrades to a clear message.
- Every numeric claim in an answer must be traceable to a tool call in
  the trace — the agent never free-recalls FPL numbers from training.
- Honest gaps stated, never papered over.
- Full unit suite green; router's existing intents still answer in ms.

ACCEPTANCE — generalisation is the bar
1. All ten probes answered correctly in the real pane (screenshots,
   charts inline where asked).
2. THE HELD-OUT TEST: when done, spawn a fresh reviewer agent that has
   NOT seen this prompt and have it compose 5 NEW analytical questions
   spanning at least three data domains each (e.g. projections ×
   ownership × fixtures). At least 4 of 5 must come back correct and
   data-backed on the first try, with the tool trace proving the numbers
   came from queries. Include the transcript in the report.
3. A transfer recommendation produced end to end in chat: grounded in
   suggest_transfers, enriched with ownership/fixture/creator context,
   with every number traceable.
4. One analysis authored by the agent in chat, saved with conversation
   provenance, re-run by name instantly.
5. Reload mid-answer re-attaches to the stream (demonstrate).
```

## 6. Deploy — online for friends, solver served from the store

```
You are deploying the FPL edge engine at ~/Documents/Github/i-test-season
so a few invited friends can reach the web UI like fplreview, and making
solve results a stored, cache-served asset. This session is ONLY about
deployment, auth, and solve persistence. Do not change the solver's
maths, the warehouse's PIT semantics, or the chat agent's loop.

CONTEXT AND CONSTRAINTS
- The whole stack is Mac-resident BY DESIGN: launchd jobs, the DuckDB
  file, the Max-plan claude CLI for chat. v1 keeps it that way and
  exposes it through a tunnel; a VPS migration is a later session and
  needs the warehouse + jobs story rethought. Do not start it here.
- The chat agent spends the OWNER'S Max plan. Friends must not be able
  to burn it. The solver holds the write lock and takes ~10 min. Friends
  must not be able to queue solves. myteam data (the owner's squad,
  bank, token-derived reads) is the owner's business.

BUILD, in order:

1. SOLVE RESULTS IN THE STORE (do this first; it is also what makes the
   UI serve instantly). New table fact_solve in store/schema.sql:
   (solve_id, season, gw_from, horizon, mode, objective_value, chip,
   squad codes + captain/vice as a JSON column or child table
   fact_solve_pick, bank_after, n_sims, solver_status, inputs_hash,
   started_utc, finished_utc, repo_sha, as_of) — append-only like every
   fact. inputs_hash = sha256 over (mode, horizon, gw, the forecast
   artefact's content hash, squad codes + bank + FTs, rules version):
   the exact inputs that make a solve reproducible.
   - solve_runner persists the parsed plan into fact_solve on
     completion (it already parses gw1_plan.json; the artefact stays
     for backwards compat).
   - POST /api/solve computes inputs_hash FIRST: an existing row with
     the same hash returns {cached: true, solve} immediately — the
     "don't rerun" the user asked for, with a force=true escape.
   - GET /api/solve/history — recent solves from the store; the Solver
     view gains a history list (click an old solve → its plan renders).
   - Tests: hash stability (same inputs same hash, any input changes
     it), cache hit returns without spawning, force bypasses. Break the
     hash once, watch the cache test fail, restore.

2. AUTH — Cloudflare Tunnel + Cloudflare Access (chosen; reasons: free
   tier covers this, TLS and stable hostname with no port forwarding,
   auth happens BEFORE traffic reaches the Mac, friends log in with an
   emailed one-time code, zero passwords to manage, and the app needs
   almost no auth code of its own).
   - cloudflared as a launchd service tunneling to localhost:8321.
   - Access application over the hostname: an allowlist policy with the
     owner's email + the friends' emails.
   - The app TRUSTS the Cf-Access-Authenticated-User-Email header ONLY
     when a shared-secret header from the tunnel config is also present
     (documented setup step), else treats the request as local-owner.
   - Role middleware in app.py: OWNER (the configured email + all
     localhost traffic) vs VIEWER (everyone else authenticated).
     VIEWER gets: all GET panels/views, solve HISTORY, fixtures,
     projections, template/EO. VIEWER is DENIED: POST /api/solve (403
     with a friendly "ask Nripesh to run a solve"), the chat agent
     escalation (router fast-path is fine — it is cheap and local),
     /api/query free SQL, watchlist writes, idea writes, myteam panels
     (squad panel returns an honest "owner-only" empty state for
     viewers; the planner loads with the TEMPLATE squad instead of the
     owner's, clearly labelled).
   - Every denial is a clean styled message, not a 500.
3. DEPLOY MECHANICS: make deploy-platform installs the platform +
   cloudflared launchd services (the com.fpledge.platform.plist exists,
   orphaned — wire it, don't rewrite it). make undeploy-platform
   removes both. Document the one-time Cloudflare steps (tunnel create,
   DNS, Access policy emails) in docs/platform/DEPLOY.md with exact
   commands. Caffeinate note: the Mac sleeping takes the site down;
   document `sudo pmset -a sleep 0` as the owner's choice, do not run it.
4. VERIFY: role tests (viewer 403s on every denied route, owner passes;
   header spoofing without the tunnel secret gets viewer treatment at
   most — break the secret check, watch the spoof test fail, restore),
   solve cache end to end, and a real tunnel smoke test IF the owner has
   run the one-time Cloudflare setup — otherwise verify locally with
   forged headers and list the owner's remaining manual steps precisely.

ACCEPTANCE
- A solve triggered from the UI lands in fact_solve; re-requesting with
  unchanged inputs returns the stored plan instantly (demonstrate both).
- Solver view shows history; old plans render.
- Viewer/owner roles enforced with tests; chat and solve-trigger and
  myteam are owner-only; viewers get honest denials.
- make deploy-platform installs everything; DEPLOY.md lists the exact
  one-time Cloudflare steps and which emails to allowlist.
- Full unit suite green.
```

## 7. Telegram — deferred, do later

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
