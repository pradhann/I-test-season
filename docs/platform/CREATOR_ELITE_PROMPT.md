# Session prompt: The Panel — elite managers and creators as an edge source

Written 2026-08-27, grounded in a three-way audit of the content pipeline, the
rivals crawl, and the chat/MCP/UI surface performed the same day. Paste the
fenced block below as the opening message of a fresh session. It is
self-contained and designed to be run **unattended and resumed after an
interruption** (usage limit, crash, context exhaustion).

Two surfaces, one thesis: the informed cohort's *convergence* is the cheapest
good estimate of the top-1k template, and the template is what the rank
objective actually consumes. Creator picks are weak signal; creator **reach**
moves ownership, so creators are a leading indicator of EO, and elite squads
are the EO itself. Neither is a source of tips.

**The headline the audit found: most of this already exists and most of it was
silently broken.** The creator track record was stuck at zero resolved claims
because outcomes could never be revised. The transfer table was empty because
the crawl could never reach the transfer stage. The elite cohort had squads for
25 of its 2,015 managers. None of it was visible from the UI or the chat.
Stage 0 exists because building tabs on top of that would render honest zeros
and look like a design failure instead of a data failure.

**Stage 0 was executed on 2026-08-27** — see `docs/platform/PANEL_LEDGER.md`
for what landed, what the repairs proved, and where this document's own §3 was
wrong. A fresh session should read the ledger first and resume from it, not
re-derive §3 from scratch.

---

```
You are working on the FPL edge engine at ~/Documents/Github/i-test-season
(branch main). The MCP toolbelt is IN this repo at `fpl_mcp/` (folded in from
the former sibling checkout on 2026-08-27; that old checkout is a dead archive
— do not edit it). It is a sibling package of `fpl_edge/`, shares the same
environment, and starts with `uv run python -m fpl_mcp`.

This session builds THE PANEL: elite-manager tracking and creator-corpus
tracking — repaired, formalised, backtested, surfaced in the UI as two tabs,
and fully reachable from chat. Do not touch the solver internals, the
projection providers, or the chat runtime's transport.

You are authorised — explicitly, by the person who wrote this prompt — to use
the Workflow tool and to fan out N subagents with adversarial verification.
Use them. The orchestration protocol is section D and it is not optional.

════════════════════════════════════════════════════════════════════════════
0. HOW TO RUN THIS (read before anything else)
════════════════════════════════════════════════════════════════════════════

This prompt may be started, interrupted mid-stage, and restarted in a fresh
session with no memory of the previous one. Therefore:

- The ledger `docs/platform/PANEL_LEDGER.md` is the single source of truth for
  progress. Create it on first run from the template in section E.
- FIRST ACTION of every run: read the ledger, run `git log --oneline -15`, and
  resume at the first stage whose ledger status is not DONE. Say which stage
  you are resuming and why.
- LAST ACTION of every stage: update the ledger (status, commit sha, what a
  fresh session needs to know, what surprised you) and commit it.
- Never mark a stage DONE because the code exists. DONE means its acceptance
  checks ran and passed in that session AND the adversarial pass (D.2) failed
  to refute them.
- Running low on context? Finish the current sub-step, write precise resumption
  notes in the ledger, commit, stop cleanly. A clean stop with a good ledger
  entry is a success. A heroic half-finished stage is not.
- Anything that needs the owner (a credential, a subscription decision, an
  entry ID that cannot be found) goes under a "NEEDS OWNER" heading at the top
  of the ledger. Then continue with the next stage — never idle waiting.

════════════════════════════════════════════════════════════════════════════
1. INVARIANTS (not up for renegotiation)
════════════════════════════════════════════════════════════════════════════

- **No fabricated data, ever.** Entry IDs are copied from a source and verified
  against the FPL API, never guessed. A creator with no verified ID stays
  unresolved and visibly so. If you cannot source a number, the answer is
  "unknown" — never a plausible figure.
- **Point-in-time correctness.** For warehouse facts, `Warehouse.snapshot_at(t)`
  and the `sem_*(t)` macros are the only sanctioned read. For creator content,
  `published_at` is load-bearing and `ContentStore.claims_visible_at`
  (fpl_edge/ingest/content/store.py:199) is the sanctioned path. Read the
  comment block at the top of content_001_claims.sql explaining why claims are
  deliberately NOT in PIT_KEYS — a claim is an immutable utterance, not a fact
  with versions — and preserve that reasoning in everything you add.
- **Identity is the stable player `code`**, never element_id. Managers are keyed
  by `entry_id`. Money in integer tenths.
- **DuckDB is single-writer XOR many-readers.** Long reads via
  `Warehouse.read_copy()`. Keep write transactions small.
- **Panel scripts are the only UI data path** (contract in section 3.4).
- **Politeness and policy on scraping.** Identify with the project User-Agent,
  rate-limit, cache raw bodies, and record the REAL http status (including 403
  and 404) in content_source. Record licensing per source in
  docs/data_sources.md. A source that forbids access does not get scraped and
  the refusal is recorded. See the robots question in 3.2 — it has already been
  decided once and you must not quietly re-decide it.
- **A test that has never failed is not a test.** For each meaningful new test:
  break the code, watch it fail, restore, and say so in the commit message.
- Small commits. Never force-push. **Never push without being asked.**
- **No Anthropic tokens on transcription.** Local ASR only.

════════════════════════════════════════════════════════════════════════════
2. READ FIRST (~40 minutes — nearly all of this exists already)
════════════════════════════════════════════════════════════════════════════

Creator side:
  fpl_edge/ingest/content/migrations/*.sql (read the header comments — they
  carry the design reasoning), then pipeline.py, loaders.py, feeds.py,
  youtube.py, claims.py, analyze.py, resolve.py, consensus.py, scoring.py,
  store.py, sources.py; fpl_edge/interfaces/creators.py; tests/unit/
  test_content_*.py; docs/platform/SECTION_PROMPTS.md §3 (the earlier prompt
  and its HISTORY WORTH KNOWING block).

Elite side:
  fpl_edge/ingest/rivals/{client,crawl,elite,elite_list,history,picks,roster,
  top1k,schema}.py; the dim_manager / fact_manager_* DDL in
  fpl_edge/store/schema.sql:353-434; sem_manager_picks / sem_manager_transfers
  / sem_elite_ownership in fpl_edge/store/views.sql:251-375;
  fpl_edge/models/field/{observed,cohorts,drift}.py;
  fpl_edge/models/copying/{template,skill}.py.

Surfaces:
  fpl_edge/platform/registry.py; fpl_edge/platform/scripts/ownership.py;
  fpl_edge/interfaces/briefing.py; fpl_edge/platform/chat_agent.py (the
  INTENT_TOOLS allowlist ~line 60); fpl_mcp/tools/{content_tools,expert_tools,
  team_tools,chat_tools,semantic_tools,edge_tools}.py; web/dist/js/{app.js,views/*.js}, web/dist/app.css, web/dist/index.html.

Context:
  docs/platform/rank_objectives.md §0 and §5; docs/platform/semantic_layer.md;
  docs/platform/DISTRIBUTIONS_PROMPT.md (if that session has run, its simulator
  consumes the same crawled cohort — do not duplicate it; if it has not run,
  nothing here blocks on it).

════════════════════════════════════════════════════════════════════════════
3. AUDITED CURRENT STATE (2026-08-27)
════════════════════════════════════════════════════════════════════════════
Every claim below was verified against the code and the live warehouse on
2026-08-27. Re-verify anything you are about to build on — if one is wrong,
correct THIS FILE, note it in the ledger, and continue.

── 3.1 What exists and works ──────────────────────────────────────────────
- Content tables live in the MAIN warehouse (data/warehouse/fpl.duckdb),
  applied by an idempotent runner in content/store.py with `content_`-prefixed
  versions. Both migrations applied 2026-08-19.
- 40 content sources configured in sources.py (hardcoded Python, no config
  file): 13 YouTube channels, 22 podcast RSS feeds, 3 blogs (excerpt_only),
  2 deliberately restricted (r/FantasyPL OAUTH_ONLY, X FORBIDDEN). 38 last
  returned HTTP 200.
- Live rows: content_item 551, content_claim 256 (216 `cue` + 40
  `llm:claude-opus-5`), claim_outcome 241, creator_score 170 across 7 nightly
  runs, transcript_segment 2,427 (from only 2 items), content_analysis 2.
- creator_score computes hit_rate and **wilson_lo95**, with earned weight zero
  below 25 scored claims. Zero creators currently carry non-zero weight — that
  is correct behaviour, not a bug. Do not loosen it.
- resolve.py never guesses: >1 candidate code returns `(None, "ambiguous")`,
  and 36 risky single tokens (rice, wood, ward, mount…) are refused as bare
  names.
- Rivals: dim_manager holds 3,498 distinct managers (1,682 snowball, 1,500
  top1k, 250 elite_list, 51 mini_league, 20 expert, 12 winner, 8 elite_named).
  fact_manager_season 13,268 rows spanning 2006/07→2025/26. ELITE_1000 in
  elite_list.py is a pinned LiveFPL "best 1000 of all time" scrape with 1,000
  verified-unique IDs and a recorded source URL + sha256 (Ben Crellin = 53517).
  elite.py:116 verifies each named manager's ID by accent-folded name match
  before writing — copy that pattern.
- Both pipelines are scheduled: deadline_dag.py runs content ingest at the
  T-30h step; post_gw.py runs score → ingest → crawl_elite → crawl_elite_named
  → crawl_top10k_sample, daily at 03:00 local.
- Panel registry (registry.py) validates params AND results at registration,
  hands each script a fresh read-copy Warehouse, closes it after, stamps
  provenance (script, repo_sha, generated_at, params, as_of), and auto-wraps
  every result schema as `{oneOf: [real, {empty:true, reason:"…"}]}`.

── 3.2 What is BROKEN (fix in stage 0; each is a live defect) ─────────────
Each item is: the symptom, the location, and the evidence.

B1. **[FIXED 2026-08-27, commit c789453 — kept for the reasoning]**
    Creator track record was permanently stuck at zero resolved claims.
    All 241 claim_outcome rows have `hit IS NULL` (232 `gameweek_not_played`,
    9 `published_after_deadline`), even though the latest scoring run settled
    162 claims with 56 hits in memory. Cause: `ContentStore.insert_outcomes` →
    `_insert_new(..., "claim_id")` (store.py:166-191) is INSERT-WHERE-NOT-
    EXISTS, so a claim first written as `gameweek_not_played` is frozen at that
    verdict forever; cmd_score never deletes prior outcomes. Consequence: the
    reader at interfaces/creators.py:188 reports "no resolved claims yet"
    permanently. This is the single most consequential defect in the repo's
    creator work — the entire deliverable is invisible.

B2. **[FIXED 2026-08-27, commit 63c9c0b — kept for the reasoning]**
    fact_manager_transfer was completely empty (0 rows). The cause was worse
    than the budget being consumed by the history sweep: `entry/history` has a
    12h TTL and the job runs daily, so every night re-fetched the SAME first
    ~370 histories the previous night had paid for, then raised
    BudgetExhausted. Picks and transfers were unreachable **by construction,
    permanently** — which is why the 08-25 and 08-26 receipts are
    byte-identical with zero cache hits. Reordering alone would not have fixed
    it (picks for 2,015 candidates also exceeds 400); the fix was reserving
    budget per stage. Compounding it, top1k.py never called ingest_transfers
    at all. **The general lesson, which applies to every later stage: a stage
    that cannot finish looked exactly like a stage that had nothing to do,
    and main() returned 0 either way.**

B3. **[ADDRESSED 2026-08-27, commit 63c9c0b]** The elite cohort had squads for
    25 of its 2,015 managers. fact_manager_
    pick holds 22,620 rows for exactly one gameweek (2026-27 GW1), 1,500 of
    them top1k. Coverage is asymmetric in exactly the wrong direction for the
    package's stated purpose.

B4. **[FIXED 2026-08-27, commit 5433f0b]** `get_expert_transfers` and
    `get_team_picks` both called `elements_df.loc.get(id)`; pandas
    `_LocIndexer` has no `.get`, so both raised AttributeError on every
    invocation and had never worked. The dishonest degradations behind the
    crash mattered more: one dropped an unresolved pick so a 15-man squad
    rendered as 14, the other priced an unknown player at 0.0, which reads as
    a free transfer. Both now name what they could not resolve.

B5. **The chat is never told creator data exists.** briefing.py builds its
    briefing from a hand-written `_MACRO_PURPOSE` dict of 11 sem_* macros.
    Manager macros ARE briefed (briefing.py:34-36); content_item,
    content_claim, claim_outcome, creator_score, content_source,
    transcript_segment and content_analysis appear nowhere. Combined with the
    honesty rule telling the agent to say when data does not exist, the chat
    will confidently deny having creator data it actually has.

B6. **There is no index or search over anything.** Zero `CREATE INDEX` repo-
    wide; no FTS, embeddings, or BM25. transcript_segment is **write-only** —
    the only references are the writes at interfaces/creators.py:387; nothing
    in fpl_edge/ or FPL-MCP/ ever reads it. Its migration header promises
    "what did they say about X and WHEN is a query, not a re-fetch"; that query
    does not exist. `fpl_player_claims` requires a numeric player_code and has
    no name resolution, so "who recommended Semenyo" is unanswerable today.

B7. **Three inconsistent EO definitions.** sem_elite_ownership (views.sql:370)
    sums raw multipliers (bench 0, start 1, cap 2, TC 3) over managers with a
    stored squad. CohortRates.eo (models/field/cohorts.py:60) is start_share +
    captain_share + tc_share. platform/scripts/ownership.py:301-331 computes
    "elite_own_pct"/"elite_eo_pct" with **no cohort filter at all** — a 1,508-
    manager denominator blending top1k and elite, labelled "elite".

B8. **Cohort membership double-counts.** views.sql:349 and observed.py:143
    both `SELECT DISTINCT` over all history, so 17 entry_ids appear in both
    cohorts and inflate both denominators. Cohort itself is not a column — it
    is derived from a `dim_manager.source` string prefix.

B9. **[FIXED 2026-08-27, commit 63c9c0b]** 20 stale EXPERT_SEEDS poisoned the pool. roster.py:86-95 documents that
    all 20 IDs now resolve to different people (e.g. "Ben Crellin" 6586 is
    actually Levi Longworth), yet they still seed the crawl and their leagues
    still drive the snowball — from which 1,682 of the 3,498 tracked managers
    derive.

B10. **[FIXED 2026-08-27, commit c789453]** `--no-transcripts` was a silent no-op (pipeline.py:115 passes it into
    `**kwargs` that loaders.load_source never reads), and **upsert_sources
    never updates** (store.py:109 is INSERT-WHERE-NOT-EXISTS on source_key), so
    a source whose policy or URL changes keeps its original row forever —
    directly contradicting its own migration comment.

B11. **[FIXED 2026-08-27, commit 5433f0b]**
    test_content_analyze.py::test_no_api_key_raises_unavailable was non-
    hermetic and fails on this machine: it deletes ANTHROPIC_API_KEY but
    analyze.py tries the Claude CLI backend first and finds the real binary at
    ~/.local/bin/claude, so the "unit" test shells out to a live CLI. It passes
    on CI (no CLI) and fails locally.

B12. **[RESOLVED 2026-08-27 — NOT a bug; this prompt's premise was WRONG]**
    fact_manager_chip records `bboost` for 1,411 managers and `3xc` for 55 at
    the GW1 deadline. This prompt asserted a 94% GW1 bench-boost rate "is not
    a plausible real distribution". That was reasoning from priors about an
    older season's chip rules, and it was wrong. **2026/27 ships TWO of each
    team chip, and both `bboost` and `3xc` have `start_event: 1`** (wildcard
    and freehit start at GW2) — verified directly from the archived
    bootstrap-static body. The 94% is a selection effect: the rate falls
    monotonically with rank (91% in the top 100, 81% at 1001–2000) because the
    cohort is selected on GW1 score and a bench boost adds points. Confirmed
    four independent ways, including `history.chips` agreeing with the picks
    payload 40/40. **Carry the lesson forward: read the data before calling
    something implausible.**

── 3.3 What is MISSING (the build stages) ────────────────────────────────
- **No transcripts in the bulk pipeline.** By text_source: 354 podcast items
  and 97 YouTube items are `description` only (~1.2-1.6 KB of show notes or
  blurb each); 95 blogs are excerpts; only **4 items** carry a real transcript,
  all from the one-at-a-time user-shared-link path. There is no Whisper, no
  ffmpeg, no audio download anywhere in the repo. claims.py still contains
  transcript-windowing machinery that fires for 5 of 551 items.
  **The policy that produced this is deliberate and you must respect it:**
  youtube.py:1-45 documents that both caption routes terminate at `/youtubei/`,
  which youtube.com/robots.txt disallows, so bulk caption fetching is gated off
  (`allow_disallowed_routes` defaults false). interfaces/creators.py:345 sets
  `respect_robots=not yt` for the single-URL owner-initiated path only. Do NOT
  flip the bulk gate. The legitimate opening is podcast audio: RSS enclosures
  are published for download by the publisher, and 22 podcast sources are
  already configured. That is where ASR belongs.
- **LLM claim extraction is orphaned from the bulk pipeline.** analyze.py
  (claude-opus-5, structured TranscriptAnalysis, conviction bands
  high 0.8 / medium 0.6 / low 0.4) is reachable only from creators.py:393 —
  hence 40 llm claims vs 216 cue claims and 2 content_analysis rows after a
  week of nightly runs. The cue extractor's confidence is a keyword-distance
  pseudo-number, which the owner has already called out as lazy.
- **No idea grain.** content_claim is player+action only. Chip timing,
  wildcard windows, fixture swings, formation talk — most of what a podcast
  actually contains — has nowhere to go.
- **No weights anywhere.** dim_manager has no weight/skill column;
  observed.py:70 counts uniformly; bootstrap() draws uniformly; CohortRates.
  standard_error assumes simple binomial sampling. models/copying/skill.py and
  13,268 rows of multi-season finishes exist but feed nothing.
- **No UI.** Seven views, seven nav links, none touching creators, claims,
  transcripts or crawled managers. template.js is fed by exactly one call —
  `runPanel("ownership_eo", {})`. models/copying/template.py already computes
  template-vs-differential (elite concentration × absence from template, ranked
  on EO gap) and nothing renders it.
- **No consumer of manager data.** No panel script reads sem_manager_picks or
  sem_manager_transfers; grep for fact_manager_transfer finds only DDL, two
  writers, the macro, and tests.
- **Ideas registry is disconnected from claims.** interfaces/migrations/
  001_idea_registry.sql has idea / idea_verdict / idea_context /
  idea_observation with no creator column and no claim FK — an idea sourced
  from a creator video is indistinguishable from one you invented.
- **INTENT_TOOLS allowlist gaps** (chat_agent.py:60-90): fpl_content_sources,
  review_ideas, track_ideas, mark_idea_acted, set_piece_changes,
  weekly_decision_report and engine_status are omitted, so chat can submit an
  idea but never review, track or settle one; it also lists a superseded
  get_player_history.

── 3.4 External sources, checked 2026-08-27 ──────────────────────────────
- **fplresearch.com** publishes a free public Top-100 FPL managers table with
  names, countries, numeric ratings, rating history, links to manager entry
  histories, plus publicly shared Google Sheets. No API, no paywall observed.
  Its **robots.txt returns 404** — no stated restriction, so politeness is
  entirely on you.
- **livefpl.net** is JS-rendered; whether it exposes true top-1k/10k tier EO
  could NOT be established from the landing page. Probe it properly and record
  what you actually find, with real status codes, in docs/data_sources.md.
  Note that elite_list.py already contains a pinned LiveFPL "best 1000" scrape,
  so a precedent for citing that site exists — follow the same pinning +
  sha256 + source-URL discipline.
- **The FPL API serves transfers for the CURRENT SEASON ONLY**
  (picks.py:17-19). Cross-season transfer history is impossible; the only
  multi-season trace is fact_manager_gw.event_transfers / _cost. Say this in
  the UI rather than letting a reader assume the history is complete.

════════════════════════════════════════════════════════════════════════════
4. SEED DATA — the curated panel (owner-supplied; non-derivable; copy verbatim)
════════════════════════════════════════════════════════════════════════════
Store as `data/panels/creator_panel_2026_27.yaml` in stage A. `top10k` =
verified top-10,000 finishes from top10k.co.uk, retrieved 26 Aug 2026.
`weight` is a JUDGEMENT INPUT set by the owner, 0..1 — never present it as a
measured quantity. `entry_id` is BLANK ON PURPOSE for every row: resolve each
by finding a published team ID and verifying it against
`https://fantasy.premierleague.com/api/entry/{id}/` (the response carries the
manager's name), exactly as elite.py:116 already does. Record `id_source_url`
and `id_verified_utc` per row. **A row whose ID cannot be verified stays blank
and renders as unresolved.** Note that four of these people are already in
ELITE_NAMED with verified IDs (Crellin 53517, Andy LTFPL 41, Mark Sutherns 252,
BigMan Bakar 5133) — reuse, do not re-derive.

  #  creator                     platform/handle        top10k  edge                                weight
   1 Mark Sutherns (FPL Mark)    FPL BlackBox              10   retrospective decision analysis       0.9
   2 Ben Crellin                 @BenCrellin / FFHub        7   fixture scheduling (blanks/doubles)   1.0
   3 FPL Salah (Abdul Rehman)    YouTube / FFHub            6   squad construction                    0.8
   4 FPL Harry                   YouTube / FFScout          4   broad analysis, high reach            0.7
   5 FPL Pras                    The FPL Wire               4   helicopter view, chip strategy        0.8
   6 Az Phillips                 FPL BlackBox / FFScout     4   reasoning quality                     0.8
   7 Andy (Let's Talk FPL)       YouTube                    4   volume, broad audience                0.5
   8 Ash (FPL Hints)             YouTube                    4   data-driven                           0.7
   9 Lee Bonfield                FPL Family                 4   official FPL Show                     0.6
  10 Big Man Bakar               FFHub                      3   data-driven                           0.6
  11 Trophy FPL (Mo)             YouTube / FFScout          2   elite-manager interviews              0.5
  12 Josh (FPL Graduates)        YouTube                    2   general                               0.4
  13 Tom (Who Got The Assist)    WGTA podcast               1   analytical podcast                    0.5
  14 Sam Bonfield                FPL Family                 1   official FPL Show                     0.4
  15 Sertalp B. Cay              GitHub / YouTube           0   optimisation / solver author           0.9
  16 Erik Ibsen                  2025/26 champion           1   reigning champion                     0.6

Caveats that must appear in the UI as visible text, not as code comments:
  - Crellin's edge is the fixture spreadsheet, not the opinions.
  - Andy and Harry have the largest reach: strong EO sensors, weak pick
    signals — they MOVE ownership, so they lead it.
  - Sertalp is not a tipster; track the code, not the team.
  - Ibsen won as a debutant; expect regression — variance, not proven skill.
  - **Calibration warning:** four top-10k finishes across a long career is a
    ~1–3% career average. Good, but not top-1k skill; the sustained elite sit
    at 0.1–1% and most of them are not creators. This panel is an ownership
    sensor, not a leaderboard of skill.

The owner's EO definitions — implement exactly these, and reconcile B7 to them:

    ownership = Σ weight[m] for m holding p       / Σ weight[all m]
    eo        = Σ weight[m] × multiplier[m,p]     / Σ weight[all m]
    captaincy = Σ weight[m] for m captaining p    / Σ weight[all m]
    eo_minus_global = panel_eo − global_ownership

  multiplier comes straight from the API: 0 bench, 1 starter, 2 captain, 3
  triple captain. Track ownership and eo separately — a benched player counts
  toward ownership but carries no scoring exposure. Compute every metric BOTH
  weighted and unweighted and show both: the weights are opinions, and a reader
  must be able to see the panel without them.

  Interpretation the UI must teach rather than assume: a large POSITIVE
  eo_minus_global is *exposure you carry by not owning him*, not an
  opportunity. A large NEGATIVE gap is where a genuine differential lives. A
  high-EO player is insurance, not upside.

════════════════════════════════════════════════════════════════════════════
5. THE WORK
════════════════════════════════════════════════════════════════════════════
Stages are ordered by dependency. Each ends with tests, an adversarial pass
(D.2), a ledger update, and at least one commit.

───────────────────────────────────────────────────────────────────────────
STAGE 0 — REPAIRS. Nothing else may start until this is DONE.
───────────────────────────────────────────────────────────────────────────
Fix the defects in 3.2. Each fix needs a regression test that fails before it
and passes after (break-watch-restore, stated in the commit).

Priority order — these three unblock everything downstream:
  0a. **B1 claim outcomes**: make settlement revisable. Prefer delete-then-
      insert for the (season, gw) being rescored, or an upsert keyed on
      claim_id; whichever you choose, an unscoreable claim must become scored
      once its gameweek finalises. Then re-run scoring over all settled
      gameweeks and confirm non-null `hit` rows appear. The test asserts a
      claim written as `gameweek_not_played` becomes settled on a later run.
  0b. **B2 transfers**: get fact_manager_transfer non-empty. Reorder the crawl
      so picks and transfers are fetched BEFORE the history sweep (or give
      history its own budget), and make top1k.py ingest transfers for its
      cohort. Add a test that fails when a budget-exhausted crawl silently
      skips the transfer stage — the current code makes that outage invisible
      to CI, which is why it ran for days unnoticed.
  0c. **B3 elite picks coverage**: the elite cohort must have squads for a
      meaningful share of its managers, not 25 of 2,015. Decide and document
      whether that means raising the budget, narrowing the cohort to those
      worth crawling, or splitting the crawl across runs — then implement it.

Then: B4 (the two `.loc.get` crashes), B7 (collapse to ONE EO definition; the
macro's multiplier-sum is the natural canonical form — whichever you pick,
every other call site must adopt it and a test must pin them equal), B8 (make
cohort explicit and mutually exclusive rather than a string prefix over all
history), B9 (retire or re-verify the stale EXPERT_SEEDS; the 1,682 snowball
managers derived from them need an honest assessment — say in the ledger
whether they are salvageable), B10, B11, B12 (investigate the bench-boost
anomaly and write the finding down either way).

Acceptance: every defect has a failing-then-passing test; the warehouse shows
non-zero settled claim outcomes and non-zero transfer rows; `uv run pytest
tests/unit -q` is fully green including the previously non-hermetic test.

───────────────────────────────────────────────────────────────────────────
STAGE A — Panel registry and identity resolution
───────────────────────────────────────────────────────────────────────────
- Seed file from section 4, with a validated loader matching the repo's
  existing contract style.
- New table `dim_panel_member(member_key PK, display_name, kind
  ('creator'|'elite'|'both'), entry_id NULLABLE, id_source_url,
  id_verified_utc, verified_entry_name, weight, weight_note, top10k_finishes,
  edge_note, active, as_of)`, owned by a `panel_00X_*.sql` migration following
  the content-migration precedent (do NOT edit store/schema.sql).
- Resolver reusing elite.py's accent-folded name-match verification. Rate
  limited, cached, and a no-op for rows already verified.
- Link member_key to content_source.creator and to dim_manager.entry_id.
  Report both kinds of orphan: a panel creator with no content source, and a
  crawled elite with no panel row.
- Ingest fplresearch.com's public Top-100 into `fact_manager_rating(source,
  entry_id NULLABLE, name, country, rating, rank, as_of)` — copy only, resolve
  to entry_id only where the site itself links one. Record the source, its
  terms and the observed status in docs/data_sources.md.
- Probe livefpl.net (and any other candidate) for true tier EO. Record what you
  actually found. If a clean public source exists, ingest it as a CROSS-CHECK
  series, never as a replacement for the crawl.

Acceptance: every panel row is verified-with-evidence or explicitly
unresolved; zero guessed IDs; unresolved rows queryable in one statement.

───────────────────────────────────────────────────────────────────────────
STAGE B — Elite: complete decision history and the weighted field
───────────────────────────────────────────────────────────────────────────
- With 0b fixed, backfill the full current season of transfers for every panel
  member and every crawled cohort manager; backfill /history/ as far back as
  the API serves. State plainly in the schema docs and the UI that cross-season
  transfers do not exist.
- New macro `sem_panel_ownership(p_as_of)` per (season, gw, panel, code):
  ownership, eo, captaincy — weighted AND unweighted — joined to global
  ownership with eo_minus_global, plus the count of members the percentages are
  of. NULL-code picks group under a NULL row rather than vanishing. Add it to
  docs/platform/semantic_layer.md and to the grow-only column contract test.
- Transfer FLOW view: per (gw, code), members transferring in and out, weighted
  and unweighted, with net. This is the leading-indicator series stage D tests.
- Give the field model a per-manager weight vector (dim_panel_member.weight,
  and optionally a skill score from models/copying/skill.py + fact_manager_
  season) without changing existing cohorts' behaviour. CohortRates.
  standard_error must stop assuming uniform sampling once weights are in — use
  an effective sample size.
- Surface models/copying/template.py's existing template-vs-differential
  computation instead of writing a second one.

Acceptance: pick one named elite manager and one gameweek and reproduce, by
hand against the FPL site, exactly what the warehouse stores for their squad
and transfers. PIT test: a transfer made after p_as_of is invisible.
Break-check both.

───────────────────────────────────────────────────────────────────────────
STAGE C — Creators: real transcripts, ideas, and a searchable corpus
───────────────────────────────────────────────────────────────────────────
- **Podcast ASR.** Wire local ASR (MLX-Whisper — already benchmarked ~5x faster
  than faster-whisper on this machine) over RSS enclosure audio, as a
  time-budgeted nightly backfill. Segments land in transcript_segment with the
  derivation recorded, exactly like caption-derived ones. Respect the robots
  decision in 3.3: podcast enclosures yes, bulk YouTube captions no. No
  Anthropic tokens on transcription, ever.
- **Wire analyze.py into the bulk pipeline** so nightly ingest produces
  semantic claims with verbatim quotes and conviction bands, not just cue
  claims — reporting the two extractor channels separately rather than
  averaging noise into signal. Validate content_analysis.model against a real
  model id (one live row is `max-plan:claude-fable-5-session`, which defeated
  the (item_id, model) primary key).
- **`content_idea`** — new immutable table with the same published_at
  discipline: idea_id, item_id, creator, source_key, topic ('chip'|'wildcard'|
  'fixture_swing'|'formation'|'price'|'strategy'|'other'), entity_kind
  ('player'|'team'|'chip'|'gameweek'|'none'), entity_ref, stance, horizon_gw,
  quote (verbatim), start_s, confidence, rationale, published_at, extractor.
  A claim answers "who should I buy"; an idea answers "should I wildcard in
  GW7" — the second is most of what a podcast contains and today it is
  discarded.
- **Search index** over transcript_segment plus claim/idea quotes, using
  DuckDB's FTS extension (`PRAGMA create_fts_index`), rebuilt incrementally
  after each ingest. If FTS proves unusable in this build, document the
  fallback and why — but a LIKE scan over the whole corpus is not an
  acceptable end state. "What has anyone said about Semenyo in the last two
  weeks" must be ONE call returning creator, published_at, gameweek, the
  verbatim quote, the timestamp and a working deep link.
- **Deep links** correct per platform (YouTube `&t=NNNs`, podcast episode +
  offset). Verify at least three by opening them in the Browser pane.
- Keep the canonical-video-ID dedup (watch?v= vs youtu.be once produced 44
  claims instead of 22) and write the regression test.

Acceptance: re-ingesting a processed item does zero network work and creates
zero duplicate claims or ideas; every claim and idea resolves to its verbatim
sentence and a working deep link; corpus search returns well under a second;
podcast items carry real transcripts rather than show notes.

───────────────────────────────────────────────────────────────────────────
STAGE D — Formalise and backtest (the stage that decides if any of this is real)
───────────────────────────────────────────────────────────────────────────
Nothing from A–C may be presented as edge until it survives this. Mirror
fpl_edge/eval/projection_scoring.py (last pre-deadline evidence vs settled
actuals, scoped metrics, backfill-safe, written to a fact table). Build
`fpl_edge/eval/panel_scoring.py` → `fact_panel_score(subject_kind, subject,
season, gw, scope, metric, value, baseline, n_obs, as_of)`.

1. **Creator record, rank-relevant.** A hit rate against a positional median is
   a start, not edge. Add per creator and action scope: mean points-above-
   replacement of buy claims over the following N gameweeks (N ∈ {1,3,6})
   versus (a) the position-and-price-band median and (b) the template
   alternative at the same price. Wilson lower bounds and n on every row. Keep
   the zero-weight-until-proven rule.
2. **The lead-lag test — the actual hypothesis.** Does a creator's mention
   PRECEDE the move in global ownership? For each (creator, player, mention)
   measure Δglobal_ownership over the following 1/3/7 days against a matched
   control (same player in weeks with no mention; same-week players with
   similar form and no mention). Report lead in days with an effect size and
   confidence interval. If it does not hold, say so plainly — the whole
   "creators are an EO sensor" thesis rests on it.
3. **Panel EO as a top-1k proxy.** Where true tier EO is obtainable, measure
   the panel's error against it. Where it is not, measure the panel's lead over
   global ownership and its stability, per gameweek.
4. **Elite differential test.** Players with high panel EO and low global
   ownership: did they outperform matched controls? And the inverse — template
   players the panel has faded.
5. **PIT throughout.** Claims via published_at, warehouse via snapshot_at.
   Plant a deliberate leakage test and watch it fail before fixing it.

Wire into post_gw.py after score_creators, with a backfill CLI that skips
already-scored (subject, gw, scope) rows.

Acceptance: numbers for every test across all settled gameweeks, each
reproducible from one command, and a write-up honest about which hypotheses
failed.

───────────────────────────────────────────────────────────────────────────
STAGE E — Chat reach: every one of these facts answerable
───────────────────────────────────────────────────────────────────────────
- Fix B5: extend briefing.py so the live briefing names the creator tables, the
  panel macros, the corpus search tool and the scoring facts, with worked
  example questions. The briefing pulls columns live via DESCRIBE so they
  cannot rot — follow that pattern rather than hardcoding column lists.
- Fix the INTENT_TOOLS allowlist gaps listed in 3.3, and drop the superseded
  entry.
- Link the ideas registry to claims: add a nullable claim_id / creator
  provenance to `idea` so a creator-sourced hypothesis is distinguishable from
  an invented one, and so review/tracking can credit the source.
- MCP tools: add `search_creator_corpus(query, since, creator, player, limit)`
  returning quotes with deep links; `panel_ownership(gw, panel, weighted)`;
  `panel_transfer_flow(gw)`; `manager_history(entry_or_name)` covering
  transfers + chips + past seasons; `creator_scoreboard(scope)` returning
  stage-D metrics with sample sizes. Give `fpl_player_claims` name resolution
  (it currently demands a numeric code). Follow the existing guarded-tool
  conventions — row caps, summaries with remediation, provenance.
- Acceptance is behavioural. Run these eleven questions end-to-end through the
  real chat and paste the answers into the ledger:
    1. Who in the panel owns Semenyo, and what is his panel EO versus global?
    2. Which players is the panel most overweight on right now?
    3. Where is the biggest genuine differential — high global, low panel?
    4. What did Ben Crellin do differently from the rest of the panel last GW?
    5. Show me every transfer the panel made last gameweek, net by player.
    6. Who recommended Semenyo, when, and what exactly did they say?
    7. Which creator has the best measured record on buy calls, at what n?
    8. Does anyone's mention actually move ownership? By how much, how fast?
    9. What chip advice has been given for the next three gameweeks?
   10. Which panel members were on Haaland before the field was?
   11. What does the panel own that I don't?
  A fluent but unsourced answer is a FAILURE. Each must cite rows.

───────────────────────────────────────────────────────────────────────────
STAGE F — The two tabs
───────────────────────────────────────────────────────────────────────────
Two new views in web/dist/js/views/ plus nav entries and index.html
registration, following the existing design language exactly (zero-build ES
modules, shared components from app.js, tokens from app.css, both themes,
no libraries). Every number carries provenance; every panel has an honest empty
state. Before any chart: no dual axis, categorical hues in fixed order,
validated palette, direct labels where possible, legend whenever ≥2 series.

**Tab "Panel" (elite managers)**
  - Header strip: managers tracked by cohort, last crawl, freshness dot,
    and — after 0c — honest coverage ("squads for N of M").
  - THE quadrant: panel EO (y) vs global ownership (x), one dot per player,
    size = price, diagonal drawn. Above = panel overweight (exposure if you
    don't own); below = genuine differential. Quadrant labels teach the
    interpretation from section 4. Hover gives the member list; click filters
    everything below.
  - Template board: the panel's consensus squad with ownership / eo /
    captaincy, weighted and unweighted side by side, eo_minus_global coloured
    by direction.
  - "What the panel owns that I don't", and its inverse, from my squad.
  - Transfer flow: per gameweek in/out/net by player across the panel, on a
    time axis so a wave is visible as it builds. Select a player to see WHO
    moved and when, with their rank at the time.
  - Manager drill-down: squad, current-season transfer history, chips, rank
    trajectory, and rating history where fplresearch supplies it — with the
    current-season-only limitation stated on the page.
  - Visible caveats: weights are opinions; the calibration warning; sample size.

**Tab "Creators"**
  - Source freshness strip: every source, last item, last http status, whether
    discovery is automatic, and whether its items are transcripts or just show
    notes — the same honesty the projections view already uses.
  - Latest episodes feed: title, creator, published_at, the structured analysis
    (summary, transfers, captaincy, chips) with conviction badges, expandable
    to verbatim quotes with working deep links.
  - Consensus board for the upcoming gameweek: who is buying/selling/captaining
    whom, agreement counts, and the weighted view — with unearned weights shown
    as zero rather than hidden.
  - Track-record leaderboard from stage D: hit rate with Wilson bounds drawn as
    error bars (never a bare percentage), n beside every row, plus the lead-lag
    effect where measured.
  - Corpus search box: player or free text → quote + creator + timestamp +
    deep link. This is the feature that turns the corpus into an asset.
  - Ideas panel: strategy-grain items grouped by topic and horizon, distinct
    from player claims.

Browser verification is mandatory: preview_start name "fpl-platform", open both
tabs, exercise the quadrant, the search and a drill-down, screenshot each,
read_console_messages for errors, check both themes. Fix what you see.

───────────────────────────────────────────────────────────────────────────
STAGE G — The verdict
───────────────────────────────────────────────────────────────────────────
Write `docs/platform/PANEL_VERDICT.md`: for each stage-D hypothesis, the
number, the sample size, and a plain verdict — holds / does not hold / too
early. State which parts of these two tabs are EVIDENCE and which are
DECORATION, and mark the decoration as such in the UI itself. Update
docs/platform/semantic_layer.md, docs/data_lineage.md, docs/data_sources.md
(new sources with real status codes and terms), and the ROADMAP.

If the panel does not lead the field, that is a real and useful result. Report
it as prominently as a positive one.

════════════════════════════════════════════════════════════════════════════
D. ORCHESTRATION PROTOCOL — how to use agents on this
════════════════════════════════════════════════════════════════════════════

D.1 FAN-OUT (start of every stage)
  Scout inline first — read the files, list the real work — then fan out.
  Independent work goes to parallel agents: separate ingest paths, separate
  panel scripts, separate views, separate scorers. Anything touching the same
  file goes to ONE agent. Give every agent the invariants from section 1
  verbatim: a subagent that has not been told "no fabricated data" will invent
  a plausible number, and a subagent that has not been told about the robots
  decision in 3.3 will helpfully flip the gate.

D.2 ADVERSARIAL VERIFICATION (mandatory before any stage is DONE)
  Spawn verifiers whose job is to REFUTE the stage's acceptance claims, each
  with a distinct lens, each told to default to "refuted" when uncertain:
    - **Leakage lens**: find any read that could see the future; try to make a
      post-deadline claim or transfer influence a pre-deadline answer.
    - **Fabrication lens**: take every number in the UI, the docs and the chat
      answers and trace it to a warehouse row. Hunt specifically for invented
      entry IDs, invented ownership figures, and demo data left in a live path.
    - **Silent-failure lens**: this codebase's characteristic bug is a stage
      that fails invisibly — a budget exhausted before the work runs, an insert
      that never updates, a flag that vanishes into **kwargs, a table written
      but never read. For each new pipeline stage, ask: if this stage silently
      did nothing, what would be different? If the answer is "nothing", add the
      assertion that would catch it.
    - **Contract lens**: panel schemas, the grow-only sem_* column contract,
      the allow-empty oneOf discipline, provenance on every response.
    - **Behavioural lens**: drive the real UI in the browser and the real chat.
      A feature that works only in a unit test does not work.
    - **Statistics lens** (harshest, for stage D): is the control matched? is n
      honest? is a lead-lag effect distinguishable from "everyone talks about
      players who just hauled"?
  Majority-refuted means not done: fix, re-run the verifiers. Record in the
  ledger what each verifier attacked, including the attacks that failed — that
  is the evidence the stage is sound.

D.3 COMPLETENESS CRITIC (once, at the end of stage F)
  One agent whose only question is: what did we not build, not verify, or
  quietly drop? Its findings become the stage-G ROADMAP entries.

D.4 WHEN NOT TO USE AGENTS
  Schema decisions, migration ordering, the ledger, the robots/policy call, and
  any judgement about honesty stay with you. Never delegate the decision about
  whether a hypothesis held.

════════════════════════════════════════════════════════════════════════════
E. LEDGER TEMPLATE (create on first run at docs/platform/PANEL_LEDGER.md)
════════════════════════════════════════════════════════════════════════════

    # Panel build ledger
    Prompt: docs/platform/CREATOR_ELITE_PROMPT.md
    | Stage | Status | Commit | Notes for a fresh session |
    |---|---|---|---|
    | 0 repairs | TODO | | |
    | A registry + identity | TODO | | |
    | B elite history + panel EO | TODO | | |
    | C corpus + ideas + search | TODO | | |
    | D backtest | TODO | | |
    | E chat reach | TODO | | |
    | F two tabs | TODO | | |
    | G verdict | TODO | | |

    ## NEEDS OWNER (anything blocked on a human decision)
    ## Decisions taken (append-only)
    ## Corrections to the prompt's AUDITED CURRENT STATE (append-only)
    ## Adversarial passes: what was attacked, what survived (append-only)

Statuses: TODO | IN PROGRESS (with a precise resume point) | DONE (acceptance
ran and the adversarial pass survived) | BLOCKED (say exactly what is needed).

════════════════════════════════════════════════════════════════════════════
F. DEFINITION OF DONE
════════════════════════════════════════════════════════════════════════════

1. `uv run pytest tests/unit -q` green, with break-watch-restore performed and
   noted for every meaningful new test.
2. Stage 0 proven by data, not by code review: claim_outcome has settled rows
   with non-null `hit`; fact_manager_transfer is non-empty and grows each run;
   elite-cohort pick coverage is materially better than 25 of 2,015; the two
   crashing MCP tools return results; one EO definition remains.
3. Every panel member verified against the FPL API or listed as unresolved —
   zero guessed IDs anywhere in the repo.
4. One command backfills the corpus, one the crawl, one the scoring — and
   re-running any of them is a no-op.
5. The eleven chat questions answered with citations, pasted into the ledger.
6. Both tabs screenshot-verified in both themes, console clean.
7. PANEL_VERDICT.md written, including the hypotheses that failed.
8. Ledger complete: every stage DONE or explicitly BLOCKED with a reason.
9. Small commits throughout. Nothing pushed.

Work the stages in order. When something in section 3 turns out to be wrong,
correct this file, note it in the ledger, and continue.
```
