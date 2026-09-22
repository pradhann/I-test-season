# FPL Desk: the rebuild specification

Written 2026-09-21. One document, seventeen parts. Feed it to a fresh Claude Code session in an empty repository and build one step per session in the order in Part 0.

## Contents

- [Part 0. How to use this document](#part-0-how-to-use-this-document)
- [Part 1. What we are building](#part-1-what-we-are-building)
- [Part 2. The rules](#part-2-the-rules)
- [Part 3. Architecture](#part-3-architecture)
- [Part 4. Build order](#part-4-build-order)
- [Part 5. Step S0: platform skeleton](#part-5-step-s0-platform-skeleton)
- [Part 6. Step S1: FPL data sync, settlement, squads, backfill](#part-6-step-s1-fpl-data-sync-settlement-squads-backfill)
- [Part 7. Step S8: UI shell, design tokens, component kit, Account, Admin, Dashboard v0](#part-7-step-s8-ui-shell-design-tokens-component-kit-account-admin-dashboard-v0)
- [Part 8. Step S2: fixture projections](#part-8-step-s2-fixture-projections)
- [Part 9. Step S3: score projections from providers plus consensus](#part-9-step-s3-score-projections-from-providers-plus-consensus)
- [Part 10. Step S4: the solver](#part-10-step-s4-the-solver)
- [Part 11. Step S6: Creators pipeline, eight shows, discover to claims](#part-11-step-s6-creators-pipeline-eight-shows-discover-to-claims)
- [Part 12. Step S9: Creators settle, score, consensus and the three-level screen](#part-12-step-s9-creators-settle-score-consensus-and-the-three-level-screen)
- [Part 13. Step S7: chat on the user's own Console key](#part-13-step-s7-chat-on-the-users-own-console-key)
- [Part 14. Step S5: Dashboard v1, the brief, operations, the light audit](#part-14-step-s5-dashboard-v1-the-brief-operations-the-light-audit)
- [Part 15. Migration from the old repository](#part-15-migration-from-the-old-repository)
- [Part 16. External facts, verified 2026-09-21](#part-16-external-facts-verified-2026-09-21)
- [Part 17. Appendix](#part-17-appendix)

# Part 0. How to use this document

This document is the complete brief for building the FPL decision tool from scratch in a new repository. It was written on 2026-09-21 from a review of the previous attempt (the repository at `/Users/nripeshpradhan/Documents/Github/i-test-season`, 114,443 lines of Python, 391 commits in 33 days), which is now treated as a finished exercise. Nothing from that repository is imported wholesale; Part 15 lists the data and the few files worth carrying over by hand.

**Who reads this.** A Claude Code session working in the new, empty repository, and the owner. The session reads the whole document once, then builds exactly one step per session.

**The protocol for every session.**

1. Read Part 0 to Part 4 in full, then the part for the step you are building, then the parts of the steps it depends on.
2. Write the step's acceptance tests first (section 10 of the step). They fail. Then build until they pass.
3. A step is done only when every line of its definition of done (section 11) is met: tests green, CI green, deployed to Railway, the feature check green for this step and all previous steps, the owner's demo script performed on the deployed URL, docs updated.
4. Never start the next step while the current one is unmerged or its feature check is red.
5. Never widen the step. If something outside the step is broken and blocks it, fix the smallest thing that unblocks it and record the rest in `docs/BACKLOG.md`.
6. If the spec is silent on something you need, decide, write the decision under a `DECISION:` line in `docs/adr/` and continue. Do not stop to ask unless proceeding under any assumption would be unsafe or wasted.
7. When the owner interrupts, stop cleanly and report the consolidated state. Do not defend the detour.
8. Prose in code comments, UI strings, docs and analysis output follows Part 2 rule group "UI and prose": no em-dashes, no rhetorical framing, lead with the number. `scripts/prose_check.py` runs in CI.

**The session-start checklist.**

- `git status` clean on `main`, CI green, `/api/health` on the deployed URL reports the same `git_sha` as `main`.
- `scripts/feature_check.py <url>` green for every completed step.
- The step's part of this document open; the `docs/BACKLOG.md` read.

**The session-end report** (under 15 lines, to the owner):

```
Step Sn: <name>. Done | Blocked on <what>.
Runs: <the commands and URLs the owner can use now>
Deployed: <git sha> at <url>, feature check <green|red: line>
Tests: <n> passed, CI <green|red>
Demo: <the exact clicks from section 11>
Left out: <anything from the spec not done, and why>
Next: Sm <name>
```

**Step ids and where they live.** The architecture (Part 3) numbers the steps S0 to S9. Their build order and the part of this document that holds each spec:

| Order | Step | Part | Name |
|---|---|---|---|
| 1 | S0 | Part 5 | platform skeleton |
| 2 | S1 | Part 6 | FPL data sync, settlement, squads, backfill |
| 3 | S8 | Part 7 | UI shell, design tokens, component kit, Account, Admin, Dashboard v0 |
| 4 | S2 | Part 8 | fixture projections |
| 5 | S3 | Part 9 | score projections from providers plus consensus |
| 6 | S4 | Part 10 | the solver |
| 7 | S6 | Part 11 | Creators pipeline, eight shows, discover to claims |
| 8 | S9 | Part 12 | Creators settle, score, consensus and the three-level screen |
| 9 | S7 | Part 13 | chat on the user's own Console key |
| 10 | S5 | Part 14 | Dashboard v1, the brief, operations, the light audit |

Any reference in a spec to `SPEC_Sn` or "step Sn" means the part listed here.


# Part 1. What we are building

**The owner's brief, 2026-09-21, verbatim.**

> I will treat this whole repo as an exercise. We will do a fresh start one by one. What's useful: fixture projections; score projections from multiple; content creators, let's limit to 5-10 best; chat feature (needs Claude login, API); running solver, think if we can replicate the solver of FPLReview and how to do so; dashboard idea, surfacing the important info from each section. We want to make the prod build CLEAN and step by step. Doing multiple development lines you struggle, so we will keep it one by one with a clear SPEC guidance. Review it all and come with the plan of what we want to do: CLEAN BUILD and best UI, little clutter as possible, interactive, filtering, strong visuals so easy to understand data. Come with a doc that I can feed and we will start from scratch in a new repo and deploy there. Think of all our learnings on how to structure modules, where to host pipeline runners, transcribers etc. Code must be easy to maintain and extend. How to ask users for Claude API for chat and how to login; for pipelines and all maybe I can have an admin view and the rest can have a user view.

**The six features.**

| Feature | What it is | Step |
|---|---|---|
| Fixture projections | Team attack and defence ratings from a Dixon-Coles fit on results and official xG, rendered as a fixture ticker with FPL's five difficulty colours, per gameweek, with clean-sheet probability and expected goals on click | S2 |
| Score projections | Five providers in one long table (fplform, the FPL API's own ep_next, airsenal, the owner's FPLReview member CSV, Solio's public JSON), an unweighted consensus with spread, provider scoring against settled points | S3 |
| Creators | Eight named shows, podcast audio transcribed on Groq, claims extracted with Claude, settled against results, shown as a consensus board with a three-level drill-down: player, then the snippets by show, then the source at the offset | S6, S9 |
| Solver | A port of the open-fpl-solver formulation (the public FPLReview replica) on HiGHS, run on the consensus, with FPLReview's published defaults, chips held, the hit-taking alternative shown beside the headline | S4 |
| Chat | Claude over the same panels the screens use, on the user's own Anthropic Console API key, cost printed under every reply | S7 |
| Dashboard | One landing screen answering transfer, captain, bench and chip in that order, plus the on-demand brief | S8 (v0), S5 (v1) |

**Two roles.** A user signs in with Google, enters an FPL entry id, and sees Dashboard, Fixtures, Projections, Solver, Creators, Chat and Account. An admin (the owner, by email allow-list) also sees Admin: every job with its last run, counts, cost, next due, a Run-now button, the spend ledger and the user list. A user never sees a job, a ledger or another user's data.

**The FPL calendar constraint.** Season 2026-27, gameweek 5 at the time of writing. Each step has a target gameweek in Part 4. The order puts data and the ticker before the solver because the solver is useless without projections, and the creators screen after the creators pipeline because the board is empty without claims.

**Decisions taken before the design, from the review.** These are settled. Each is grounded in a measured fact from the old repository or a source verified on 2026-09-21 (Part 16).

D1. Chat credentials are bring-your-own Anthropic Console API key (sk-ant-api...) ONLY. R7_web.md verified on code.claude.com/docs/en/legal-and-compliance that third-party apps may not collect, store or intermediate Claude.ai credentials or session tokens, so the old setup-token (sk-ant-oat) path is dropped and 'sign in with Claude' does not exist. The spec must explain this to the owner in one paragraph with the URL, and the Account screen copy must tell users where to create a key (console.anthropic.com) and that it is billed to them.
D2. Scheduled model work (claim extraction, the brief) runs on the operator's API key held only by the worker, on claude-sonnet-5, with a monthly USD cap in the ledger. Chat defaults to claude-sonnet-5 with claude-opus-5 as a per-user choice, because the old Opus chat measured a median 0.83 USD and 102 s per turn (R5).
D3. Postgres for everything a request touches (users, sessions, credentials, jobs, ledgers, projections, claims, artefacts as JSONB). No DuckDB in the serving path. Railway managed backups. Any offline backtest may use parquet plus DuckDB locally, outside the deployed services.
D4. No Mac in the pipeline. R7 verified that YouTube blocks datacentre IPs for captions and audio. Therefore: podcast RSS audio is the primary creator source (unblocked; the eight shortlisted creators all publish podcasts or have RSS), transcribed on Groq whisper-large-v3-turbo from the worker; YouTube captions are secondary and fetched through a rotating residential proxy (Webshare, the library's own recommendation) with a monthly cap; if the proxy is not configured the YouTube step records skipped_no_proxy in its ledger row and the screen shows it.
D5. No own points model in v1. Providers: fplform, fpl_ep (the FPL API's own ep_next), airsenal (GitHub), FPLReview member CSV uploaded by the owner through an upload screen (paid, private, never republished), Solio's free public JSON (top-N only, attributed). Consensus is the unweighted mean over non-retired providers with spread and n_sources shown. The solver runs on the consensus.
D6. Solver: port the open-fpl-solver (solioanalytics, Apache-2.0, formerly sertalpbilal) semantics on HiGHS via highspy in our own Python 3.12 code (do not depend on the package; R7 found it requires Python 3.14). Defaults follow FPLReview's published settings (R7): decay 0.85, FT value 1.75 with the banked-FT ladder, bench weights {0.03, 0.21, 0.06, 0.002}, ITB value 0.10 per 1.0m, hit cost 4, 300 s, 3 solve lines. Chips held by default; headline within free transfers; the hit-taking best shown beside it. Rank utility is v2.
D7. Creators shortlist for v1: the eight in R4_creators.md (FPL Harry, FPL Raptor, Let's Talk FPL, FPL BlackBox, FPL Fran, Fantasy Football Hub, The FPL Wire, plus Fantasy Football Scout as the article source), identity by id. Declutter means fold, never delete: consensus, buy/sell takes, ratings and the said-versus-owned matrix stay, folded.
D8. UI stack: React 19 + TypeScript strict + Vite + Tailwind 4 + shadcn/ui + TanStack Table and Query + Recharts, custom SVG for the fixture heatmap and pitch, types generated from FastAPI's OpenAPI. Copy the old token set (six type sizes, five spacing steps, status colours for state only, accent #3e6c91). Dark is the default theme; light works through the same tokens and is audited in the dashboard step. Navigation in decision order: Dashboard, Fixtures, Projections, Solver, Creators, Chat; Account in the header; Admin only when the session is an admin.
D9. Railway: separate services from one repo, each with its own build (R7: services never share a build): web, worker, Postgres, one bucket for audio, uploads and exports. Scheduler is in the worker (APScheduler or a jobs table claimed with SKIP LOCKED), never in web. Health reports the SDK and CLI versions. A feature check hits the deployed URL after every deploy.
D10. Not in v1: ownership and effective-ownership crawls, odds ingestion, elite-manager mining, Telegram, the rank layer, per-user FPL login (private my-team reads). Squads come from the public picks endpoint with the autosub reversal.

**The one the owner asked for that cannot be built.** "I want people to sign in with their Claude so they can use their own Claude credits." Anthropic's legal and compliance page for developers (Part 16, section 1) states that third-party applications may not offer Claude.ai login and may not collect, store or intermediate Claude.ai credentials or session tokens. The old repository's setup-token path did exactly that. The rebuild asks each user for an Anthropic Console API key instead, billed to them, stored encrypted, shown back as its last four characters. If Anthropic ships a sanctioned OAuth for third-party apps, it replaces the key field in the Account screen and nothing else changes.


# Part 2. The rules

Every rule below was paid for by an incident in the old repository. Each has the mechanical check that enforces it; none relies on care.

### The five rules that matter most

1. **Rule 9, a run that wrote nothing is an error.** 14 of the 41 entries are this shape; every other rule in Pipelines is a special case of it.
2. **Rule 17, artefacts live beside the warehouse the run was given.** One repo-relative path put an engine forecast on the live dashboard during a test run.
3. **Rule 22, a view reads only what the schema publishes.** Four instances in one cycle, each rendering plausible and false, each caught only by a person looking.
4. **Rule 26, verify features against the deployed URL.** Every component check passed while three of six product features were dead.
5. **Rule 40, per-user model credential with no fallback.** The scheduled briefing starved on the owner's subscription, and a fallback would bill the operator for strangers.

### Data

**1. RULE:** Every fact table is append-only with an `as_of`, every read goes through one point-in-time macro, and an unfiltered read needs a written reason.
**INCIDENT:** `b732d8d` (2026-08-27): creator weights were read with no `as_of`, so claims from the GW1 deadline were scored by today's track record inside a payload that echoed `as_of` back (unbounded: 24 creators, 52 scored claims; bounded: 13 creators, 0 scored). `AUDIT_2026-08-20.md` records the PIT core as the one part that held: zero `UPDATE` or `DELETE` on any fact table across four seasons.
**ENFORCEMENT:** `Snapshot.warehouse` is private; `escape_hatch_unfiltered(reason)` demands a 20-character reason (`5b623ae`); `LeakageError` on a raw read; `tests/audit/test_static_leakage_audit.py` plus the write-time refusal of a result stamped before its own kickoff, tested as a pair (`a7ed16a`).

**2. RULE:** A NULL means unknown; a feed's NULL is filled with zero only after a reconciliation proves it.
**INCIDENT:** `da7be61` (2026-09-07): the planner read xG from a feed that omits the column when the count is zero, so 252 of 400 GW1 rows were NULL for players who took no shot. Filled after checking that the publisher's goals summed that way reconcile with official settled goals for 400 of 400 players. `49a7d82` (2026-08-18): unknown ownership had been ingested as 0.0%.
**ENFORCEMENT:** a reconciliation test per third-party feed (sum of the filled column equals the official column for one settled gameweek); `fillna(0)` banned in ingest modules by a grep test; `projection_providers.md:61` rule "A NULL is an absence; a zero would" read as measured.

**3. RULE:** Unrecorded availability is unknown, and a season with none recorded raises rather than guesses.
**INCIDENT:** `AUDIT_2026-08-20.md`: 109,956 historical `fact_player_state` rows had NULL status; `injuries.py:136` did `fillna("a")` and `warehouse.py:206` kept NULL rows in `selectable()`, so every injured and suspended player in four seasons was pickable and certain to start. Fixed `0a8c8b3` (2026-08-23).
**ENFORCEMENT:** `UnknownAvailabilityError` from `selectable()`; a backtest opts in with `assume_available_when_unknown=True` in its own source, where it can be grepped.

**4. RULE:** One metric has one SQL definition, a Python twin pinned equal, and a test fixture dirty enough to catch a boundary mutation.
**INCIDENT:** `43c2837` (2026-08-27): effective ownership was computed three ways under one name, and the panel's version had no cohort filter and labelled 1,508 managers "elite". `b2f5af0` (2026-08-27): the guard rail let 15 of 20 mutations through, including `as_of <=` to `<`, because the 8-manager fixture had no NULL multipliers, no Bench Boost and no row on the snapshot instant. Tests went 3 to 18 by changing the fixture.
**ENFORCEMENT:** `tests/unit/test_field_eo_agreement.py` compares macro, model and panel on one warehouse; the macro's column set is asserted; a test fails if the fixture loses its complications.

**5. RULE:** An entity key carries every dimension that can legitimately repeat, and a key column is NOT NULL.
**INCIDENT:** `b0943ca` (2026-08-31): FPL's transfer log repeats an (in, out) pair inside one gameweek (transfer, undo, redo). With the pair as the key, entry 46827's redo raised a PRIMARY KEY violation that killed both nightly crawls, which is why `fact_manager_transfer` sat at 0 rows for the gameweek the ledger was watching.
**ENFORCEMENT:** `time_utc` in the key at both DDL sites and the PIT registry; the regression test replays do-undo-redo into a real warehouse and is proven against both DDL sites broken together.

**6. RULE:** A feed that removes by omission is deduped as the latest snapshot per publisher, never the latest row per entity.
**INCIDENT:** `06ae257` (2026-08-28): Rotowire drops a player by ceasing to emit him, so "latest row per player" gave Crystal Palace a 13-man XI. Safe only because `ProjectionStore.append` keys on `as_of`, so every poll writes its complete set.
**ENFORCEMENT:** snapshot-per-`(provider, team_code)` dedupe; a test that returns 11 with the new query and 12 with the old; the chip filed in `PANEL_LEDGER.md` to audit `fact_player_state` and `set_piece_duty` for the same pattern.

**7. RULE:** Names resolve by exact alias, then containment, then given-name prefix, and never by edit distance.
**INCIDENT:** `e332148` (2026-08-27): a structured player name went through the prose scanner, so "Martin Ødegaard" resolved to David Raya Martín (154561 for 184029). `PANEL_LEDGER.md` 2026-08-28: edit distance over twenty clubs sent `forester` to BRENTFORD (d=6) and tied `hull` with FULHAM (d=4). `13358f0`: twenty invented creator identities printed a stranger's ranks under Holly Shand's name.
**ENFORCEMENT:** one resolver (`ingest/rivals/names.norm`); `tests/unit/test_club_resolver.py` fails 11 of 20 with edit distance installed; the shared-surname refusals (Louie Barry, Trent Hume) pinned as tests; an unverified entry renders as "entry 135 (account holder not verified)".

**8. RULE:** Every timestamp is tz-aware UTC at the boundary, the DuckDB session is pinned to UTC, and every read has an ORDER BY.
**INCIDENT:** `d450316` (2026-08-18): the GW1 deadline read back as 10:30 Los Angeles. `49a7d82`: `pd.to_datetime(utc=True)` localised naive input silently. `b2f5af0`: `string_agg(DISTINCT source)` without ORDER BY gave 379 of 5,559 rows a different provenance string at threads=8; the fixture needed nine sources in reverse order to catch it every run.
**ENFORCEMENT:** `_require_utc` raises on naive input (`warehouse.py:139`); a test runs the macro at threads 1, 2 and 8 and asserts byte-identical output.

---

### Pipelines and jobs

**9. RULE:** A run that wrote nothing is an error unless it names why; every run reports rows written, rows unchanged and stages not reached.
**INCIDENT:** `0f617a3` (2026-08-27): `_write` returned `{"status": "locked"}` and nothing read it, so all stages reported ok, zero rows landed and `post_gw` recorded success. `63c9c0b`: the crawl's history sweep on a 12h TTL re-fetched the same 370 histories nightly and raised, so picks and transfers were unreachable by construction and the 08-25 and 08-26 receipts were byte-identical. `c789453`: `INSERT WHERE NOT EXISTS` froze 241 claim outcomes at their first verdict for a week.
**ENFORCEMENT:** `fetch_run` ledger row per run with rows written and rows unchanged (`6bbd4eb`); `OutcomeWrite(inserted, revised, unchanged)`; `_write_failures` names three silent-nothings; the auditor's `attack_crawl.py` must fail all four attacks; `main()` exits non-zero on `incomplete_stages`.

**10. RULE:** A refusal is an outcome of its own, never the detail string of a success.
**INCIDENT:** `9cbe749` (2026-08-28): the odds credit cap was 30, one run needs 22, so the job could never succeed and reported ok for four nights while markets aged 91 to 206 hours. `cdf24a3` (2026-09-06): `run_transcribe_nightly` returned `delivered` on failure, so three failed runs showed green. Orchestrator note: `no_source` runs were counted green in the board.
**ENFORCEMENT:** outcome enum `{ok, quiet, no_source, skipped_stale, error}` in `pipelines/runner.py:70`; a test that no failing step can land as an ok row; the board colours `no_source` amber with its reason.

**11. RULE:** Every `python -m` target answers `--help` with output before it is scheduled.
**INCIDENT:** `fdbf916` (2026-09-18): refactor group 4 (`b952101`) dropped the `__main__` guard from `ingest/content/pipeline.py`, so content ingest, transcription, claim extraction and creator scoring were exit-0 no-ops from 08:20 to 18:00 UTC and migration 007 never reached the warehouse. The group's `--help` smoke check passed vacuously, although `ARCHITECTURE_REVIEW.md` section 6 had frozen a full-suite gate after every refactor group.
**ENFORCEMENT:** `tests/unit/test_subprocess_entry_points.py` derives the module list from the runbook inventory and asserts non-empty `--help` output; `run_step` records exit 0 with no output as its own state (`e23fe81`); the refactor gate runs `feature_check.py` too.

**12. RULE:** A keyword the callee cannot honour is a `TypeError`; no `**kwargs` sink on any CLI or loader.
**INCIDENT:** `c789453`: `--no-transcripts` was a silent no-op because nothing fetched transcripts. `c507c73` (2026-08-27): `max_items` reached only the feed branch, so a YouTube source ignored the cap one function below the docstring saying it could not happen.
**ENFORCEMENT:** explicit signatures on every loader; one test per flag asserting the receipt count changes; `ruff` rule against `**kwargs` in `cli/` and `ingest/`.

**13. RULE:** Every artefact a reader depends on has a registered writer task, a stale window, and at least one reader, and the board lists all three.
**INCIDENT:** `9a59a37` (2026-09-07): `forecast.parquet` covered GW3-7 while the solve wanted GW4-8, 32 players unprojected. `38e8b1e`: `fixture_ratings.parquet` 227 h stale. `cdf24a3`: claim extraction had only ever run by hand, 651 items unanalysed. `AUDIT_2026-08-20.md`: `fact_odds_derived` had 1,720 rows and no reader. `PANEL_LEDGER.md` 2026-08-28: `content_insight` held 0 rows for its whole existence, with extraction, storage and read all built and tested.
**ENFORCEMENT:** `tests/unit/test_no_double_work.py` walks every scheduled path (33 targets); a sibling test asserts every parquet under `data/warehouse/` has one writer task and one importing reader; `pipelines_runbook.py` generates the runbook table and a sync test pins it.

**14. RULE:** Request budgets are reserved per stage, and a fetch that was paid for is kept before the shortfall is raised.
**INCIDENT:** `63c9c0b`: one stage ate the 400-request budget every night. `ee16f16` (2026-08-31): the GW2 crawl paid for 270 `entry/transfers` requests and wrote zero rows because `BudgetExhausted` unwound past the frame assignment, on the endpoint whose 3 h TTL makes a re-run a real re-spend.
**ENFORCEMENT:** `STAGE_SHARE` lowers `budget.limit` inside a `_stage()` context manager; a test with a fetcher that dies mid-loop asserts the partial frame was saved and the stage still reads incomplete.

**15. RULE:** Attribution crosses the subprocess boundary: `trigger` has no default, and a child run carries `parent_run_id`.
**INCIDENT:** `PIPELINES_AUDIT.md` section 1: 158 `ingest_projections` ledger rows all said `scheduler`, including runs provably clicked in the UI, because `RunRecord.trigger` defaulted to that string; a day of elimination to attribute one 16:26 ingest. `e7ce1d1` (2026-09-09) committed an artefact with "I cannot attribute the run."
**ENFORCEMENT:** `trigger` required at construction and exported to subprocess steps (`9dbe02d`); `parent_run_id` column on the ledger; a test that a UI-triggered chain's child rows read `ui`.

**16. RULE:** One scheduler, inside the serving process, single flight, one writer lease, one log line per tick.
**INCIDENT:** `PIPELINES_AUDIT.md` 3.1: the 17-step chain ran twice a day (launchd 10:00, registry 10:33), `crawl_elite` at 730 s and 661 requests both times. `38e8b1e`: the launchd tick slept with the lid and lost settlement on Aug 31 and Sep 6. `2aabca6`: the DAG agent died 539 consecutive spawns (EX_CONFIG 78) because macOS revoked its log path under `~/Documents`. `47e456e`: on Railway a quiet tick and a dead loop were indistinguishable.
**ENFORCEMENT:** `railway.toml` `replicas = 1`; `FPL_EDGE_SCHEDULER=1` gate; `scheduler.py` single-flight skip recorded; tick line with number, duration, fired and skipped ids; `create_app` configures the logger (`04fd866`); no launchd plist in the repo.

**17. RULE:** Every writer, run log and cache lives beside the warehouse the run was given.
**INCIDENT:** `4835004` (2026-09-19): `cli/solve.py` wrote `forecast.parquet` under the repo root regardless of `--db`, so a test rewrote the live forecast engine-only at 07:31 UTC and the dashboard captained a 4.5 percent owned midfielder. `6f9564a`: 694 test logs in the real `pipeline_logs/`. `4f88537`: 312 test runs swept the real ASR cache. `10db093`: the fingerprint guard tripped six times on the owner's Mac against unrelated tests because the server writes the file all day.
**ENFORCEMENT:** `Warehouse.__init__` refuses `DEFAULT_DB` when `PYTEST_CURRENT_TEST` is set (`warehouse.py:119-136`); `tests/conftest.py` fingerprints every path under `data/warehouse/` after every test, strict under `make test`; log and cache paths derived from `db_path`.

**18. RULE:** A step exits non-zero on any incomplete stage, and the parent judges children on the receipt, never on the return code alone.
**INCIDENT:** `63c9c0b`: `post_gw` judged steps purely on return code, which is why a starved crawl ran green for days. `0f617a3`: `post_gw` wrote failures to a JSON file nothing read, and launchd discarded the exit code.
**ENFORCEMENT:** `run_step` reads the child's receipt JSON; a failed chain enqueues one alert on the outbox; a test that a stage receipt with `not_reached` cannot roll up to ok.

---

### Artefacts and state

**19. RULE:** An artefact records what it was computed against, and a reader refuses to render it beside a different input.
**INCIDENT:** `da7be61` (2026-09-07): `transfer_plan.json` recorded moves without the fifteen they were diffed from, so a plan solved before the account connected sat beside the live squad; its XI named Ndiaye, whom the squad card did not list, and the +18.3 headline was inflated by the phantom. `DOGFOOD_GW6.md` section 3: a plan solved on the engine forecast rendered under a page showing the consensus everywhere else.
**ENFORCEMENT:** `squad_before`, `forecast_source` and fill share are required sidecar fields; the serializer refuses a plan whose XI is not inside its own post-transfer squad; `test_a_stale_or_missing_plan_renders_no_plan_content` in `test_web_contract.py:625`; `auto_resolve` refuses when `forecast.meta.json` is not consensus (`d04e828`).

**20. RULE:** A state word measures the quantity the word asks about, and the schema names that measurement.
**INCIDENT:** `da7be61`: "fresh" measured distance to the deadline, so a plan solved 36 minutes ago read "aging" for four days. `DOGFOOD_GW6.md` section 9: deadline-relative tasks read "stale 17 days" between deadlines because stale was computed by age rather than by next due.
**ENFORCEMENT:** the enum `{fresh, aging, stale, superseded, missing}` in `brief/schema.py:320` with the measurement written at `schema.py:54`; stale computed per task kind (`d04e828`); one test per state value.

**21. RULE:** Absence is a named gap carrying the reason and the fix; never a blank, a 0, or a plausible default.
**INCIDENT:** `AUDIT_2026-08-20.md`: the price radar printed "No data." over a full set of risers because its renderer read a key the schema forbade; every squad card printed `£NaN`. `13358f0`: a `£0.0m` fallback read as a free transfer; a missing multiplier defaulted to "started". `1926276`: a report section returning `None` vanished with no trace.
**ENFORCEMENT:** 23 panel result schemas with `additionalProperties: false`; the `{empty, reason}` envelope; the MCP gap object with a constant `say` instruction (`MCP.md` 5.3); an unknown layout is an explicit error, never a fallback to the table renderer (`b1a15cf`); the dossier contract that a section has a body or a gap.

**22. RULE:** A view reads only the fields its panel's schema publishes, and the two contracts meet in one adapter.
**INCIDENT:** `06ae257` (2026-08-28): seven renamed fields (`o.attack_xg` for `o.opponent_only.attack_xg`, and six more) left the rebuilt fixtures tab drawing 20 blank club names while printing "The split is not in this payload" and holding the split. `PANEL_LEDGER.md` counts four instances of this shape in one cycle.
**ENFORCEMENT:** `tests/unit/test_web_contract.py` (66 tests) parses the JS and holds every field read against the registered schema; `flatten()` is the one meeting point and the guard strips comments first (`1250359`).

**23. RULE:** Two surfaces that show one fact read one panel, and every number names its source and unit at the point of use.
**INCIDENT:** `d9e4ef9` (2026-09-02): Gabriel read 5.6 on the dashboard and 4.7 on Projections. `DOGFOOD_GW6.md` section 7: the captain row named João Pedro and printed +1.75, which was B.Fernandes's delta. Section 10: free transfers read 2 in the header and 1 in the plan.
**ENFORCEMENT:** a contract test compares the whole squad across the two panels and fails on any divergence; the captain row carries `pick_xpts`, the consensus captain's code and xPts beside it (`e11ef01`); `test_the_solve_plan_gain_travels_with_its_currency_label`.

**24. RULE:** The warehouse has a daily backup with a restore that has been run, and a staged upload is validated before it is promoted.
**INCIDENT:** `9108ba9` (2026-09-19): four seasons on one volume with no copy anywhere; `grep backup DEPLOYMENT.md` returned nothing. `7a6511e`: DuckDB replays a WAL against whatever file carries the name, so a good upload over a running service can destroy a good database.
**ENFORCEMENT:** `warehouse_backup` task at 14:30 UTC, manifest with sha256 written last; the restore test asserts every row and a byte-identical size (35,403,474 bytes compressed in 4.1 s); boot step 3 promotes `fpl.duckdb.incoming` only after reading rows from `dim_player`.

---

### Deployment

**25. RULE:** One service, one replica, one DuckDB writer; a long read copies the file and the copy owns its own deletion.
**INCIDENT:** `951c0e0` (2026-08-18): an odds team reported 56,944 rows written while `fact_odds` was empty, because six concurrent writers held the lock. `3bd92fb`: 462 orphaned `fpl-read-*` copies, 5.4 GB. `2523de8`: a migration ran only on a write-mode open, so readers under a held lock hit binder errors. `DEPLOYMENT.md` section 2 calls the single writer the scaling cap and rejects Railway Cron on that ground.
**ENFORCEMENT:** `WarehouseLockedError` names the holder; `read_copy` records its tmpdir and a weakref finalizer reaps it; readers select optional columns only when present; `replicas = 1` explicit.

**26. RULE:** A deployment is verified by a feature check against its URL, one line per thing a user does, in addition to the boot check.
**INCIDENT:** `f2e865a` (2026-09-19): `make deploy-check` passed (image, volume, migrations, 18 panels at 200, one tick) while chat, the analysis brief and claim extraction were dead on the live service.
**ENFORCEMENT:** `scripts/feature_check.py` with a `FEATURES` table of ten user actions; `/api/health` reports `model_runtime` in its public fields.

**27. RULE:** The model runtime is the one the SDK ships; nothing looks for a binary on `PATH` or under the home directory.
**INCIDENT:** `61b5da6` (2026-09-19): claim extraction shelled out on its own and searched `PATH`, finding nothing in the container, while `claude_agent_sdk/_bundled/claude` (234 MB, version 2.1.277) sat in the wheel.
**ENFORCEMENT:** one resolver function with the bundled binary as its documented fallback; the feature check asserts `model_runtime` is present on the deployed service.

**28. RULE:** Steady-state disk is bounded: no raw body archive in the container, and every cache has a retention task with a ledger row.
**INCIDENT:** `14a335d` (2026-09-19): content ingest archived 836 MB a day of HTTP bodies, 11 GB on the Mac, and nothing in the repo read one. A 10 GB volume fills in under a week, and the boot check cannot see a steady-state property.
**ENFORCEMENT:** `FPL_EDGE_ARCHIVE_BODIES=0` in the Railway environment; provenance from `raw_fetch` (source, endpoint, sha256, status, instant) and `content_item` text (1,070 items in 10.4 MB); `audio_retention` deletes only files whose transcript and receipt exist; health reports volume usage.

**29. RULE:** Static assets revalidate on every load, and the UI has one toolchain.
**INCIDENT:** `defba17` (2026-09-07): the zero-build UI served yesterday's module until a hard refresh; two review agents and the owner each hit it. `UI_AUDIT.md` 2.8: the chat sub-app was a 254,772-byte React build inside a codebase whose `DESIGN.md` 2.2 decided zero-build.
**ENFORCEMENT:** `Cache-Control: no-cache` on HTML, CSS and JS with ETag; a test that no `package.json` exists under `web/` unless the design doc names the toolchain (opinion: pick one, either way).

---

### Testing and process

**30. RULE:** Nothing commits to main while a full-suite gate runs, and no process prunes pytest temp trees by age while suites run.
**INCIDENT:** Orchestrator note: 17 phantom failures from a commit during a gate. `71211c3` (2026-09-18): six worktree interpreters each retained three 160 MB trees, the disk filled twice, and a prune during a live run errored 1,258 tests.
**ENFORCEMENT:** `tmp_path_retention_policy = failed`, `tmp_path_retention_count = 1` in `pyproject.toml`; one gate runner that writes a lock file the commit hook honours.

**31. RULE:** Every fix commit records a break-watch-restore: the new test was run against the reverted code and failed there.
**INCIDENT:** `07264b1` (2026-08-28): a chat test passed 25/25 with the bug restored because it measured whether a subprocess beat a 50 ms sleep. `57902b1` (2026-08-19): "Fix a test that never actually tested its rule." `b2f5af0`: 8 mutations survived the whole suite.
**ENFORCEMENT:** a required `Break-watch:` trailer in the commit template naming the tests that failed; the reviewer re-applies the two sharpest mutations (`PANEL_LEDGER.md` 2026-08-27 did exactly this).

**32. RULE:** No test touches real credentials, the network, a metered API, or the operator's own binaries.
**INCIDENT:** `0744d63` (2026-08-19): rendering a report fixture refreshed the developer's real FPL tokens over the network. `9cbe749`: a DAG unit test would have spent 12 of 500 monthly odds credits per suite run. `5433f0b`: a "no API key" test found the operator's real `claude` binary and got an answer. `b9d778d`: two processes racing burned a single-use refresh token.
**ENFORCEMENT:** `tests/conftest.py` sets `FPL_EDGE_DISABLE_PRIVATE=1`; `tests/unit/conftest.py` sets `FPL_EDGE_DISABLE_NETWORK_INGEST=1`; `live_feeds()` is a function so isolation tests can patch it (`abd0b67`); an `flock` around token refresh with a two-subprocess test asserting one redemption.

**33. RULE:** A test fixture never carries a wall-clock date that can expire.
**INCIDENT:** `39d888a` (2026-08-28): `GW2_DEADLINE = 2026-08-28 17:30Z` meant "not yet happened"; the test went red at 17:30:01 UTC with nothing in the diff.
**ENFORCEMENT:** fixture instants are computed at least 30 days ahead of `now()`; a grep test for ISO dates in `tests/fixtures/`.

**34. RULE:** A generated document is pinned by a sync test, and a documented command exists.
**INCIDENT:** `AUDIT_2026-08-20.md`: `MVP.md:16-20` documented `make deploy-platform`, which did not exist, while `deploy/com.fpledge.platform.plist` existed and nothing installed it. `b2f5af0`: "17 of them in the live warehouse" was 37 within 15 minutes.
**ENFORCEMENT:** `tests/unit/test_layout_doc.py` and the runbook sync test; a test that every `make` target named in `docs/` exists in the Makefile; live counts in docs replaced by the query that produces them.

**35. RULE:** Declutter means fold; a rebuild lists every existing surface and where it went, and deletes none without the owner's word.
**INCIDENT:** `3126eea` (2026-09-19): the creators rebuild read "too cluttered" as "delete" and removed the consensus block, buy and sell takes, ratings and the said-versus-owned matrix, which are the parts the owner uses. `creators.js` went 3,880 to 1,850 lines and was reverted.
**ENFORCEMENT:** a rebuild PR carries a surface inventory table (old location, new location); the web-contract assertion count cannot fall without a named reason per deleted test.

---

### UI and prose

**36. RULE:** One shared component per job (table, sortable header, drawer, gap, stale chip, empty and error state, day-based age formatter, icon set), a header carries the column name only, and a second implementation is a test failure.
**INCIDENT:** `UI_AUDIT.md` section 1: 4 drawers at 3 widths, 4 sortable-header implementations, 27 font sizes, 31 spacing values, 850,557 bytes of JS on every page load; 2.3: `GW9 · 2 SRC` rendered at 99 px beside `GW6` at 43 px, the misalignment the owner photographed; 2.8: Chat printed `603h` for 25 days. `c6b9628`: fifteen helpers had been written two to four times across views.
**ENFORCEMENT:** `DESIGN_PRINCIPLES.md` R1 to R49, of which 18 are static tests in `test_web_contract.py` (`grep -c 'el("aside", "drawer'` returns 1; R6 regex over every `th`; R23 regex `\b\d+\s*h\b` over `innerText`) and 8 are a browser measurement script.

**37. RULE:** A mount fires at most three panel calls and 250 KiB, paints data within 1,500 ms, and a sort or filter issues no request.
**INCIDENT:** `UI_AUDIT.md` 2.5: Creators fired 40 calls for 3,912,647 bytes and had not finished at 30 s; 2.2: Planner fetched `/api/solve/status` five times per mount; 2.1: Dashboard data complete at 9,241 ms.
**ENFORCEMENT:** R45, R46, R49 measured by the tab-walk script; `5c08ffb` shows the target reached (2 calls, 59,703 bytes).

**38. RULE:** Model-authored prose is normalised and rejected by code before storage: no em-dashes, no rhetorical constructions, every sentence carries a number, a player or a source.
**INCIDENT:** `d9e4ef9` (2026-09-02): the owner's verbatim report on a chat answer ("Your process is idle", "not a hit, not a penny"). `da7be61`: em-dash sweep across every panel payload and rendered string.
**ENFORCEMENT:** `fpl_edge/platform/prose_style.py` `slop_findings` and `normalize_prose`, shared by chat and briefing; `STYLE_RULES` injected into both prompts; `test_no_em_dashes_in_dashboard_strings` and `test_no_rendered_string_in_the_creators_tab_carries_an_em_dash`; 9 tests in `test_prose_style.py`.

---

### Model usage and cost

**39. RULE:** Every spend site passes a pinned model id from one config module, every result row stores the reported model and token counts, and every task carries a token budget.
**INCIDENT:** `9dbe02d` (2026-09-18): `analyze.py` ran `claude -p` with no `--model` flag, so it used the machine default, the owner's most expensive model, for weeks, and stamped `claude-opus-5` from a constant regardless; `"opus"` as an alias named a different model after a CLI upgrade.
**ENFORCEMENT:** `tests/unit/test_model_pins.py` walks the AST and asserts `config.py` is the only module holding a model id; `content_analysis.model_reported`, `tokens_in`, `tokens_out`; `Task.token_budget` stops analysis at 1.5M tokens.

**40. RULE:** Scheduled model work never runs on an interactive subscription, and a user's credential lives only in that turn's environment, with no fallback to the operator.
**INCIDENT:** `dd5e9a7` (2026-09-07): the Sep 7 briefing failed on the Max-plan session limit it shared with interactive use. `AUTH.md` 7.4: a fallback "would mean the operator paying for a stranger's chat, silently." `e2ca164`: `_scrub_environment` now clears `CLAUDE_CODE_OAUTH_TOKEN` too.
**ENFORCEMENT:** 403 `no_api_key` before the turn starts; key decrypted inside the runner thread into `ClaudeAgentOptions.env` only; a test that the key is absent from every log and response; `test_auth_routes_matrix.py` with 170 generated cases and fail-closed on an unclassified route (`policy.py:222`).

**41. RULE:** No model call on an input that cannot yield output, and a preview is shown before the expensive step.
**INCIDENT:** `9dbe02d`: 410 of 799 analyses ran on show notes with no transcript and could never yield a claim. `804feb8` (2026-08-27): the owner asked to see the summary before transcribing. `91082ee`: a 300 s wall clock killed a healthy 20-tool-call turn; `DOGFOOD_GW6.md` section 9: a 240 s timeout sat one bad minute above a 178 s normal run.
**ENFORCEMENT:** the `no_body` gate; the parked preview state where the only writer is never called before accept (test: all four content tables empty after decline); an idle-based watchdog with a test that streams past the window and survives; model timeouts set at 600 s from measured runs (`d04e828`).

---


# Part 3. Architecture

Synthesised 2026-09-21 from proposals A, B and C, judgements J1 to J3 and digests R1 to R8. The base is proposal B, chosen by two of three judges. Every graft the judges asked for is applied; where two judges pulled apart, section 13 records the choice. Decisions D1 to D10 are built on as given; section 14 lists the digest facts that contradict one.

Names: repo `fpl-desk`, Python package `desk`, frontend under `web/`. Season 2026-27, current gameweek 5, next deadline GW6. The owner is entry 4490171 and the first admin. Spelling is British.

### 1. Summary of the approach

1. One repo, one Dockerfile, two Railway services built from it (web, worker), Railway Postgres and one bucket.
2. Four registries in code carry every feature: `PROVIDERS`, `PANELS`, `JOBS`, `ARTEFACT_KINDS`, each with a walk test in both directions.
3. A panel is one typed function with pydantic params and result, both `extra="forbid"`; one row serves the route, the generated TypeScript type, the chat tool and the contract snapshot.
4. Every job is one row in `job`, queue and ledger at once. The runner counts `rows_written` itself. Work seen, nothing written and no reason lands as `error`.
5. Every plan, brief and fixture rating is a row in `artefact` with `inputs` JSONB; readers derive `fresh | aging | stale | superseded | missing` from `inputs` alone.
6. Chat is the Anthropic Messages API called from web with the user's own Console key on a per-turn client; no Agent SDK, no subprocess. Tools are the session-tier panels; cost per turn is printed under every reply.
7. Scheduled model work runs in the worker on `OPERATOR_ANTHROPIC_API_KEY`, claude-sonnet-5, synchronous, under a monthly USD cap. Web refuses to boot with any `ANTHROPIC_*` variable; the worker refuses to boot with the credential decryption key.
8. Five projection providers in one long table, unweighted consensus with `fpl_ep` inside behind a `consensus_member` flag. The solver is a port of open-fpl-solver semantics on highspy, run in a subprocess on its own worker lane, on the consensus.
9. Eight creators by id. Podcast RSS audio on Groq is the primary source; YouTube captions through Webshare are secondary and skip visibly without a proxy.
10. Ten build steps, one Claude Code session each, each ending with a deploy, a demo the owner does that week and a green feature-check line on the deployed URL.

### 2. Repository layout

Python 3.12 backend, TypeScript strict frontend, SQL inside Alembic migrations, YAML for the rules registry.

```
fpl-desk/
  README.md                 every command in it exists in desk/cli.py (test_layout_doc)
  docs/                     SPEC.md; runbook.md (generated from jobs/registry.py); adr/
  pyproject.toml, uv.lock   exact pins; the image installs from the lock
  Dockerfile                node stage builds web/dist; python stage runs uv sync --frozen; ARG GIT_SHA
  deploy/                   web.railway.toml, worker.railway.toml: one config per service
  .github/workflows/        ci.yml; deploy-check.yml (waits for /api/health git_sha == HEAD, runs feature_check.py; also every 6 h)
  desk/
    core/        settings.py (env reads, boot assertions), models.py (the only module with a model id), prices.py, time.py, prose_style.py
    db/          engine.py (SQLAlchemy Core, psycopg 3, UTC per connection), pit.py (latest_at), bucket.py (boto3)
    migrations/  Alembic versions, one per step that adds tables, never edited after merge
    rules/       registry.yaml, loader.py, scoring_map.py
    fplapi/      client.py (9 endpoints, UA, backoff, 404 is data), parsers.py, ingest.py, settle.py, squads.py
    providers/   base.py (Provider, PROVIDERS), one module per provider, names.py, importer.py, consensus.py, scoring.py
    fixtures/    dixon_coles.py, difficulty.py, fit.py
    solver/      model.py (HiGHS MILP), settings.py, squad.py, scorer.py, artefact.py, run.py (subprocess entry)
    creators/    registry.py (CREATORS by id), rss.py, audio.py, transcribe.py, captions.py, extract.py, prompt.md, settle.py, score.py, consensus.py
    panels/      registry.py (Panel, PANELS, envelope), one module per panel
    artefacts/   registry.py (ArtefactKind, ARTEFACT_KINDS), store.py
    jobs/        registry.py, runner.py (ledger helper), worker.py (tick, two lanes, reaper), health.py, spend.py, tasks/
    auth/        google.py, sessions.py, credentials.py, policy.py, middleware.py, csrf.py
    chat/        loop.py (Messages API tool loop), tools.py, cost.py, charter.md
    web/         app.py (factory), routers/, sse.py, static.py
    cli.py       desk migrate | seed | run-task | mint-check-session | export-openapi
  web/src/
    api/         schema.d.ts (generated, committed, CI-checked), client.ts, usePanel.ts
    app/         router, Shell, Nav, ThemeProvider, urlState
    tokens.css   the token block, values in section 9
    components/  ui/ (shadcn) plus the shared kit of section 9
    charts/      FixtureTicker.tsx, Pitch.tsx (custom SVG), Sparkline.tsx, BarDiverging.tsx (Recharts)
    screens/     dashboard, fixtures, projections, solver, creators, chat, account, admin
  contracts/     openapi.json and panels/<name>.json snapshots, regenerated by CI
  scripts/       feature_check.py, export_old_repo.py (Mac only), backfill.py, gen_types.sh, prose_check.py
  tests/         unit/, registry/, contract/, solver/, fixtures/
```

What must never live where:

| Folder | Never holds |
|---|---|
| `desk/core`; `desk/db/pit.py` | a model id outside `models.py`, an upstream URL, a secret value; anything except `latest_at` (no other module writes `SELECT` against a series table) |
| `desk/panels`; `desk/jobs/tasks`; `desk/chat` | a write, an upstream call, a model call, a user id in provenance; a route, a response model, an import of `desk.web`; a write outside `conversation` and `chat_message`, the operator variable name |
| `desk/web` | an `ANTHROPIC_*` or `GROQ_*` read; a scheduler; a subprocess |
| `web/src`; `tests/`; the repo | a hand-typed API shape, a second table, drawer or age formatter, a raw `font-size`; an expiring date, a real key, a network call outside the allow list; parquet over 10 MB, audio, raw bodies, `.env` |

Libraries and why:

| Library | Why |
|---|---|
| FastAPI, uvicorn, pydantic 2, pydantic-settings; SQLAlchemy 2 Core (sync), psycopg 3, Alembic | typed routes produce the OpenAPI the frontend compiles against, `extra="forbid"` replaces `additionalProperties: false`; no ORM, every read one visible statement, one migration head the health payload reports |
| httpx; anthropic `>=1.7,<2` | one client for every upstream, wrapped by the test guard; the chat runtime, 1.0 was breaking (R7) |
| highspy `>=1.11`, numpy, pandas | open-fpl-solver is HiGHS only; pandas confined to `providers` and `fixtures` |
| cryptography, pyjwt[crypto], feedparser, boto3, youtube-transcript-api `>=1.2` | AES-GCM; Google id tokens; RSS; the bucket (the old hand-signed SigV4 was 1,085 lines); `WebshareProxyConfig` |
| React 19, TypeScript 5, Vite 8, Tailwind 4, shadcn/ui, TanStack Table 9 and Query 5, Recharts 3, lucide-react, openapi-typescript, vitest, playwright; ruff, mypy `--strict`, pytest, testcontainers | per D8; the compiler replaces the 12 "view reads a key the panel does not serve" tests (R8); the CI gate |

### 3. Data store

Postgres 16 on Railway, one database. Alembic migrations, one per step that adds tables, never edited after merge. Both services run `alembic upgrade head` at boot under `pg_advisory_xact_lock(7231)`; the second arrival finds nothing to do. Code at a head the database lacks refuses to serve and health returns 503.

Naming: singular snake_case tables; every timestamp `timestamptz` named `_at`; the session pinned to UTC; a naive datetime raises at the boundary (incident `d450316`). Season is text `2026-27`, converted from the slash form at parse. Money is integer `_tenths`. Every key column is `NOT NULL`.

Keys: player `code` (Haaland is 223094 across five seasons while `element_id` ran 318 to 411), `team_code`, `entry_id`. `player` holds the per-season mapping; `creator_pick` and `user_squad` carry the raw `element_id` beside the resolved `code`; `test_element_id` asserts no other table has that column.

Point-in-time. Three series tables, `player_state`, `projection` and `creator_score`, are append-only with `as_of` in the key; a write equal to the latest row is dropped and counted as unchanged. Every read goes through `desk.db.pit.latest_at(table, keys, t)`, a `DISTINCT ON ... WHERE as_of <= t` query; `test_pit` fails on any other `SELECT` from a series table. `player_fixture` is a current row with `settled_at`; a write stamped before kickoff is refused. Every other table is a current row with `updated_at`.

27 tables:

| Group | Table, key | Notable columns |
|---|---|---|
| platform | `app_user` (user_id); `session` (sid_hash); `credential` (user_id); `job` (job_id); `spend` (meter, period) | email unique, role in {user, admin}, entry_id, entry_name, chat_model; expires_at, reissued_at; ciphertext, nonce, key_version, last4, verified_at; section 5; amount, cap |
| platform | `artefact` (artefact_id); `worker_heartbeat` (worker_id) | kind, user_id nullable, job_id, created_at, inputs jsonb, payload jsonb; last_tick_at, git_sha |
| fpl | `event` (season, gw); `team` (season, team_code); `player` (season, code) | deadline_at, finished, finished_provisional, data_checked, avg_entry_score NULL until finished, is_next; team_id unique per season; element_id unique per season, web_name, position, team_code, has_temporary_code |
| fpl | `player_state` (season, code, as_of) | price_tenths, selected_by_pct NULL when blank, status, chance_next, can_select, ep_next, news |
| fpl | `fixture` (season, fixture_id); `player_fixture` (season, code, fixture_id); `user_squad` (user_id, season, gw); `raw_payload` (payload_id) | gw, kickoff_at, team codes, scores, finished flags; 22 stat columns incl. xg, xa, xgc, defensive_contribution, total_points, settled_at; picks jsonb after autosub reversal, bank_tenths, free_transfers, ft_source, chips_played; source, endpoint, sha256, bucket_key, fetched_at |
| projections | `projection` (provider, season, gw, code, as_of); `provider_score` (provider, season, gw, scope, metric); `upload` (upload_id) | xp, xmins, p_appear, file_sha256; value, baseline, n_obs; user_id, sha256, bucket_key, as_of from the filename epoch, status, unresolved |
| creators | `creator` (creator_id); `source` (source_id) | name, active, people jsonb (name, entry_id, evidence_url); kind, url, policy, last_status |
| creators | `item` (item_id = sha256 of source and canonical url) | creator_id, kind, title, url, published_at required, text_source, text, segments jsonb, transcript jsonb (engine, model, audio_sha256, seconds, covered, usd), enclosure_url, analysed_at |
| creators | `claim` (claim_id) | item_id, creator_id, code, action, gw, gw_inferred, confidence, quote, start_s, extractor, published_at; outcome columns player_points, benchmark_points, hit, unscoreable, resolved_at, revision |
| creators | `creator_score` (creator_id, scope, as_of); `creator_pick` (entry_id, season, gw, code); `creator_gw` (entry_id, season, gw) | claims_scored, hits, hit_rate, wilson_lo95; element_id, slot, multiplier; points, overall_rank, bank_tenths, chip |
| chat | `conversation` (conversation_id); `chat_message` (conversation_id, seq) | user_id, title, model, usd_total; role, blocks jsonb, model, tokens, cache_read, usd, duration_ms, style_findings jsonb |

A row is anything a request reads; a bucket file is anything a request never reads whole:

| Thing | Where | Retention |
|---|---|---|
| raw FPL and provider bodies; podcast audio | bucket `raw/{source}/{date}/{sha}.json.gz` indexed by `raw_payload`; `audio/{sha}.mp3` | 30 days; deleted the night after `item.transcript` is set |
| uploaded CSVs; `pg_dump` copies | `uploads/{user_id}/{sha}.csv` plus an `upload` row; `backups/{date}.sql.gz` plus a sha256 manifest written last | kept, served to no other user; keep 8 |
| seed parquet | bucket `seed/2026-09-21/` | kept |
| job log tail | `job.log_tail`, last 64 KB | with the row |
| `artefact` rows; `job` rows | tables | solver_plan 30 per user, brief all, fixture_ratings 30; 180 days except `solve` (30 per user) and `brief` (all) |

Seed parquet lives under `seed/2026-09-21/`; the job log tail is `job.log_tail` (64 KB). The daily `retention` job writes one ledger row with bytes freed and remaining per bucket prefix; `admin_spend` shows both (rule 28). Backfill on first deploy from section 12: 115,809 `player_fixture` rows across five seasons, 135,391 `player_state` rows, the four kept providers, 236 provider scores, 1,070 items, 3,381 claims.

### 4. Hosting on Railway

Four services in the existing Railway project from one repo. Services never share a build (R7 section 7): web and worker each build the same Dockerfile from the lock, and each names its own config file under `deploy/`.

| Service | Runs | Replicas | Start |
|---|---|---|---|
| `web` | FastAPI, `web/dist`, auth, panels, chat turns, SSE, entry validation, asset proxy | 1 in v1 (Hobby allows 6) | `uvicorn desk.web.app:create_app --factory` |
| `worker` | the 30 s tick, both lanes, every upstream and operator-key call, solves, retention, backups | 1, `overlapSeconds = 0` | `python -m desk.jobs.worker` |
| `postgres`, `bucket` | Postgres 16 with managed backups on from S0; the bucket for raw bodies, audio, uploads, dumps, seed | managed | |

No volume anywhere. Web deploys overlap freely; the worker deploys with `overlapSeconds = 0` plus a unique index on `job (task, due_at)`, so a second tick during a deploy cannot double-insert a slot (rule 16). Web calls upstream for two things only: `entry/{id}/` to validate a team id, and the FPL CDN behind `/api/assets/{player,club}/{code}.png` with a 7 day cache.

Env vars, one name each, validated at boot against a fixed list:

| Variable | web | worker | Note |
|---|---|---|---|
| `DATABASE_URL`, the five bucket variables, `APP_ENV` (`local` or `railway`), `GIT_SHA`, `PUBLIC_URL`, `ADMIN_EMAILS` | yes | yes | Railway references |
| `SESSION_SECRET`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | yes | refused | `GOOGLE_CLIENT_ID` required when `APP_ENV` is not `local` |
| `CREDENTIAL_ENC_KEY`, `CREDENTIAL_ENC_KEY_PREV`, `FEATURE_CHECK_TOKEN` | yes | refused | AES-256-GCM with rotation; the check user's session mint |
| `OPERATOR_ANTHROPIC_API_KEY`, `GROQ_API_KEY` | refused | yes | a name no SDK reads by default |
| `WEBSHARE_PROXY_USERNAME`, `WEBSHARE_PROXY_PASSWORD` | refused | optional | absent means `skipped_no_proxy` |
| `MONTHLY_CAP_ANTHROPIC_USD` 25, `DAILY_CAP_GROQ_AUDIO_S` 28800, `MONTHLY_CAP_PROXY_MB` 2000 | no | yes | |
| `DEV_LOGIN_EMAIL` | local only | no | refused under `APP_ENV=railway` |

Boot assertions, tested: web refuses to start if any `ANTHROPIC_*`, `CLAUDE_CODE_*`, `GROQ_*`, `OPERATOR_*` or `WEBSHARE_*` variable is present; the worker refuses `GOOGLE_*`, `SESSION_SECRET` and `CREDENTIAL_ENC_KEY*`. Web cannot reach the operator's key; the worker cannot decrypt a user's (rule 40). Web outside `local` refuses to start without `GOOGLE_CLIENT_ID`, so a public domain with no client cannot make every visitor the owner again.

Health, `GET /api/health`, anonymous:

```
{ ok, now, git_sha,
  db: { reachable, alembic_head, expected_head },
  worker: { last_tick_at, age_s, ok },
  runtime: { anthropic_sdk, highspy, cli: "none" },
  jobs: { failing: [], stale: [], never_ran: [] } }
```

503 when the database is unreachable or `alembic_head` differs from `expected_head`; 200 with `worker.ok: false` when the heartbeat is older than 180 s, so a dead worker is visible without failing the web deploy. Healthcheck timeout 60 s. There is no CLI, so the D9 field says `none`.

Backups: Railway managed daily and weekly from S0, plus the weekly `pg_dump_to_bucket` job (gzipped dump, then a sha256 manifest, keep 8) and the monthly `db_restore_check` job, which restores the newest dump into a scratch database, compares row counts per table with production, writes them on its ledger row and drops the scratch database (rule 24).

Secrets: Railway variables only; the image build greps itself for `sk-ant`, `AKIA` and `.env` and fails on a hit. Deploys: Railway deploys `main` on push with the check-suites setting on, so a red push is not deployed; if that setting is unavailable, CI fast-forwards a `deploy` branch on green and Railway watches it.

Cost per month on Hobby at R7 unit prices ($20 per vCPU, $10 per GB RAM):

| Item | Assumption | USD |
|---|---|---|
| Hobby plan | $5 usage included | 5.0 |
| web; worker | 0.15 vCPU, 0.5 GB; 0.25 vCPU with solve bursts, 1 GB | 8.0; 15.0 |
| postgres; bucket | 0.1 vCPU, 0.5 GB, 2 GB disk; 5 GB | 7.3; 0.1 |
| Groq turbo | 3.2 audio hours a day (R4) | 3.9 |
| Sonnet, synchronous | 5 items a day at 6,210 in and 2,000 out, plus one brief a day | 6.5 |
| Webshare proxy | 1 GB plan; price unverified in R7 | 6.0 |
| Total after the included $5 | | about 47 |

### 5. Jobs and pipelines

One table, `job`, is queue and ledger; a queued row becomes the run record when claimed, and a task that fires ten times has ten rows.

Columns: `job_id`, `task`, `lane` (pipeline or solve), `trigger` (schedule, admin, user, chain, catchup or cli), `user_id`, `parent_job_id`, `params` jsonb, `priority`, `due_at`, `state` (queued, running, done), claim and heartbeat stamps, `started_at`, `finished_at`, `status` (ok, nothing_to_do, refused, error, skipped_overlap, skipped_no_proxy, skipped_no_body), `items_seen`, `items_done`, `items_skipped` jsonb by reason, `rows_written`, `rows_unchanged`, `audio_seconds`, `tokens_in`, `tokens_out`, `usd`, `model`, `note`, `log_tail`, `git_sha`.

`trigger` has no default (158 old rows all said `scheduler`, rule 15). `nothing_to_do` and every `skipped_*` status require a note. The ledger helper in `jobs/runner.py` wraps every task, counts `rows_written` as `count(*)` before and after on the tables the registry row names, catches everything, and writes status, counts and the log tail. `items_seen > 0` with `rows_written = 0` and empty `items_skipped` lands as `error`, note `wrote_nothing_unexplained` (rule 9, the shape behind 14 of the 41 rules).

The registry row `Job` is a frozen dataclass: `name`, `lane`, `due` (Calendar, DeadlineRelative, Interval, After or OnDemand), `stale_window`, `writes` (tables or artefact kinds the helper counts), `read_by`, `meters`, `budget_s`, `main`. `test_jobs_walk` asserts per row: `--help` prints output (rule 11); every `writes` name exists and has a reader panel (rule 13); `stale_window` is set; a unit test file exists. Stale is measured against `next_due` for deadline-relative jobs (rule 20). `docs/runbook.md` is generated from `JOBS` and a sync test pins it (rule 34).

The worker, about 250 lines: boot (migrate under the lock, assert env, heartbeat); a 30 s tick that computes `due_at` per registry row and inserts a queued row if none exists for that slot, with a 36 h lookback on restart as `trigger = catchup`; two claim threads, one per lane, claiming with `FOR UPDATE SKIP LOCKED`; a heartbeat every 30 s; a reaper returning `running` rows with a heartbeat older than 5 minutes to `queued` (the 8 stuck rows that answered the UI with 409); one log line per tick. A `solve` runs as `python -m desk.solver.run --job <id>` in a subprocess killed at `seconds + 30`, so a 300 s HiGHS run never delays a T-4h poll and a crash cannot take the worker down.

`spend` holds one row per meter per period: `anthropic_usd` (month), `groq_audio_s` (day), `proxy_mb` (month). A metered task calls `reserve(meter, estimate)` first; a refusal writes `status refused` with the cap and the period total, amber on the board (rule 10, incident `9cbe749`). Admin Run-now, `POST /api/admin/jobs/{task}/run`, inserts a row with `trigger = admin`; a metered task without `confirm: true` returns `{needs_confirm, estimate, month_spend, month_cap}` and inserts nothing. Chains carry `parent_job_id` and `trigger = chain`.

The v1 schedule, UTC. `T` is the next `event.deadline_at`:

| Job | Due | Writes (meter) |
|---|---|---|
| `fpl_poll`, `fpl_settle` | 06:00, 12:00, 18:00, T-30h, T-4h, T-30m; daily 10:30, gated | event, team, player, player_state, fixture, projection (fpl_ep), raw_payload; player_fixture, event |
| `squad_refresh` | T+90m; every 12 h; on demand with a 1 h TTL | user_squad, creator_pick, creator_gw |
| `fixture_fit`; `provider_pull` | daily 11:00; fplform 08:00, 20:00, T-4h, airsenal 09:00, solio every 4 h | artefact fixture_ratings; projection, raw_payload |
| `provider_import_upload`, `provider_score` | on upload; after fpl_settle | projection, upload; provider_score |
| `solve` (solve lane) | on demand; T+2h for users with a plan in the last 14 days | artefact solver_plan |
| `creators_discover`; `creators_transcribe`, `creators_captions` | every 4 h; after discover | source, item, raw_payload, audio to bucket; item.segments, item.transcript, item.text (groq_audio_s; proxy_mb) |
| `creators_extract` | after transcribe and captions | claim, item.analysed_at (anthropic_usd) |
| `creators_settle`, `creators_score` | after fpl_settle; after settle | claim outcome columns; creator_score |
| `brief` | daily 07:00 | artefact brief (anthropic_usd) |
| `pg_dump_to_bucket`, `db_restore_check`, `retention` | Sunday 03:00; first Monday 03:30; daily 04:00 | backups/; counts on the row; bucket deletes, raw_payload, job, artefact |

The settlement gate, in full: `fpl_settle` writes a gameweek only when every fixture in it is at least provisionally finished, and either every fixture is `finished` with `bonus_added` true on `event-status`, or the instant is past 09:00 Europe/London on the day after the last kickoff. Provisional numbers are never written; `settled_at` is stored on every row.

Transcription: Groq `whisper-large-v3-turbo` at $0.04 per audio hour, free tier 28,800 audio seconds a day (R7). Audio comes from the podcast RSS enclosure (D4), cached in the bucket under its URL sha, decoded to 16 kHz mono PCM and cut into 600 s chunks of 19.2 MB, timestamps offset by the sample index. The coverage guard refuses a transcript under 80 percent of measured duration with more than 90 s missing; a 429 raises `HostedRefused` and leaves the item queued. Captions run through `youtube-transcript-api` with `WebshareProxyConfig` when both proxy variables are set, metered on `proxy_mb`; otherwise `skipped_no_proxy`. A podcast plus YouTube pair is one `item` through the canonical key.

Extraction runs in the worker on `OPERATOR_ANTHROPIC_API_KEY`, `claude-sonnet-5` from `core/models.py`, synchronous `messages.create`, one call per item with a body, `max_tokens` 16,000, one cache breakpoint on a prompt over 1,024 tokens. The prompt and schema come from the old `analyze.py` (R4 section 3); `start_s` is found by quote search against `item.segments` at write time. Description-only items count as `skipped_no_body` before any call (410 of 799 old analyses ran on show notes). No Batch API: 5 items a day cost about $0.08 and submit-and-poll is a second code path with its own silent-nothing shape. A user's chat credential never runs a pipeline.

### 6. The query surface

The registry row `Panel` is a frozen dataclass: `name`, `params` and `result` (pydantic models, `extra="forbid"`), `tier` (anonymous, session or admin), `fn(params, ctx) -> Result | Empty`, `budget_ms`, `description`, `example_params`, and `headline`, the truncated view for chat tools. `Ctx` carries `user_id` and `role` from the session and is never a param, so a user id never rides into provenance or a tool argument. The envelope every consumer receives:

```
{ ok: true, panel, result | null, empty: null | { reason, fix },
  provenance: { computed_at, git_sha, params, inputs: [ { kind, id, as_of } ],
                budget: { ms, elapsed_ms, over } } }
```

`inputs` names the artefacts and series rows the panel read; the age chip under every block reads it. A panel that raises on an empty table returns `empty` with a reason; on a populated table it is a 500 naming the panel.

Three consumers. `GET /api/panels/{name}` with params as query parameters, one route per row with `response_model` set; `openapi-typescript` produces `schema.d.ts` and `usePanel("projections_table", params)` is typed by name, so a screen reading an undeclared key is a compile error. Chat tools, one per session-tier panel named `panel_<name>` with `input_schema` from `params.model_json_schema()`; a result over 20 KB is replaced by `headline(result)` with `truncated: true`. Contract snapshots in `contracts/`, diffed in CI.

`test_panels_walk` asserts per row: both models forbid extras; `example_params` validates; `fn` on an empty database returns `Empty` and never raises; the name appears in `web/src` as `usePanel("<name>")` or the tier is `admin`; the route tier equals the policy tier.

| Panel | Tier | Params | Result |
|---|---|---|---|
| `me` (anonymous); `deadline`, `me_squad` | see left | none | signed_in, email, role, entry_id, entry_name, has_key, last4, chat_model, next_step; gw, deadline_at, seconds_left, state; picks[15] with price, sell price, multiplier, status, bank, free_transfers, ft_source, chips_played |
| `dashboard` | session | none | rows[4] (transfer, captain, bench, chip): answer, numbers, confidence, deciding_field, working; gaps[]; brief |
| `fixtures_board`, `fixture_detail` | session | horizon, lens, basis; team_code, gw | 20 clubs by N cells with attack_ease, defence_ease, p_cs, ranks, source, as_of; verdict; freshness; the club-specific numbers |
| `projections_table`, `player_detail`, `provider_accuracy` | session | gws[] (max 8), providers[], position, team_code, max_price, min_p_appear, squad_only, q; code; season | per-gw {mean, n_sources, min, max, actual}, sum, spread, p_appear, providers_meta; per-provider pivot, price series, snippets; MAE and Brier per provider per GW with n |
| `solver_plan`, `solver_status` | session | job_id or latest | the R3 artefact plus one state word; queued, running, done, log tail |
| `creators_board`, `player_chatter`, `creator_episodes`, `episode_summary` | session | gw, position, creators[], stance; code, gw; creator_id; item_id | consensus bars, creator rows with wilson_lo, wilson_hi, n; snippets by show with start_s; every episode with has_transcript; bullets, calls with offsets, audio_url |
| `conversations`, `conversation` (session); `admin_jobs`, `admin_job_log`, `admin_spend`, `admin_users` (admin) | see left | none; conversation_id; none; job_id; none; none | stored chat rows; health per task, last run, next due; log tail; meters against caps and bucket bytes per prefix; users with role, entry, last4, month chat usd |

Two surfaces that show one fact read one panel: the dashboard captain row reads `solver_plan` and prints the plan captain beside the consensus captain (rule 23).

### 7. Auth and roles

Identity is Google OAuth 2.0 authorization code with PKCE, scope `openid email`, httpx and pyjwt only; the existing Google client is reused with the redirect `${PUBLIC_URL}/auth/callback` added. Handshake cookie 600 s with state, nonce and verifier; the id token is verified against Google's JWKS. Sessions: `session` stores `sha256(sid)`; cookie `desk_session = sid.hmac`, HttpOnly, Lax, Secure, 30 days absolute, re-issued after 24 h. CSRF is double submit. Role is `admin` when the email is in `ADMIN_EMAILS`.

Policy: `auth/policy.py` is one table `(METHOD, path template) -> tier` plus the panel tiers; one middleware reads it in the order session, CSRF, tier; an unclassified route fails closed to admin; `test_routes_matrix` walks `app.routes` with three cases per route. Development: `APP_ENV=local` plus `DEV_LOGIN_EMAIL` creates a session on `GET /auth/dev`, 404 under `railway`. There is no anonymous-is-owner and no public entry id fallback: a stranger sees the sign-in screen and `/api/health`.

Development: `APP_ENV=local` plus `DEV_LOGIN_EMAIL` creates a session on `GET /auth/dev`; under `railway` the route is 404. There is no anonymous-is-owner and no public entry id fallback: a stranger sees the sign-in screen and `/api/health`.

Arrival, computed server side by `policy.next_step()` and returned by `me`:

1. `sign_in`: one button and one sentence on what the app reads.
2. `set_team`: one field with the help text "Your FPL team id, from the URL of your points page: fantasy.premierleague.com/entry/4490171/event/5". Saving calls `entry/{id}/`, shows the account name and overall rank, and asks for a "This is me" click before storing, because a stale id belongs to a stranger. Storing enqueues `squad_refresh`; Dashboard and Solver show a gap card until then.
3. `ready`.
4. `add_credential` only when the user opens Chat; the composer is replaced by the credential card, which appears nowhere else.

The credential card, verbatim on Account and in the Chat banner:

> Chat runs on your own Anthropic API key, billed to your Anthropic account at API prices. Create one at console.anthropic.com under API Keys (it starts with `sk-ant-api`) and paste it here. This app shows the cost of every reply. The key is stored encrypted, shown back as its last four characters, used only for your chat turns, and removable here at any time. Removing it here does not revoke it at Anthropic. Claude.ai subscriptions (Free, Pro, Max) and `claude setup-token` values cannot be used here: Anthropic does not permit third-party apps to store or route them.

The paragraph for the owner (D1): Anthropic's legal page at https://code.claude.com/docs/en/legal-and-compliance, read 2026-09-21, states that third-party developers may not offer Claude.ai login, may not route requests through Free, Pro or Max plan credentials on behalf of their users, and may not collect, store or intermediate Claude.ai credentials or session tokens; it tells Agent SDK developers to use Console API keys. The old repo's 2026-09-20 correction accepting `sk-ant-oat` tokens stored exactly such a credential, and server-side enforcement rejects those tokens outside first-party apps (R7 section 1). So this app has one credential kind. The owner's Max subscription is for his own Claude Code sessions and never touches the deployed app.

Credential handling in `auth/credentials.py`:

| Rule | Mechanism |
|---|---|
| Accept | `^sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}$`; an `sk-ant-oat` prefix returns 400: "That is a Claude Code token and cannot be used here. Paste a Console API key." |
| Verify on paste | `messages.create(max_tokens=1)` on `claude-sonnet-5`; failure is 400 with the API error class, never the key |
| At rest, shown back | AES-256-GCM, fresh nonce, associated data `user_id`; `key_version` with `_PREV` rotation; `last4` only; the PUT body has no pydantic model so a 422 cannot echo it |
| Point of use, env mapping | decrypted inside the turn handler in web and passed as `AsyncAnthropic(api_key=key)`, a client scoped to the turn; under the Agent SDK the one kind would map to `ANTHROPIC_API_KEY` in `ClaudeAgentOptions.env`; in this plan no credential maps to an env var, argv or a log |
| Scrub, no fallback | web refuses to boot with any `ANTHROPIC_*` or `CLAUDE_CODE_*` variable and the client is always built with an explicit `api_key`, so nothing falls back to the operator; a grep test proves `desk/chat` never names the operator variable; no decryptable credential is 403 `{error: "no_api_key", detail, remediation_url: "/account"}` before any model call, printed verbatim by the composer |
| Sentinel | `test_credential_sentinel`: a stored key appears in no response, header, log record, repr or database byte outside the ciphertext; a row moved between users fails to decrypt |

Admin sees the jobs board, the spend meters with bucket bytes per prefix, the users list (email, role, entry id, last4, month chat spend), the FPLReview upload card and the feature check's last result; never chat content or a credential. Users see the six sections and Account.

### 8. Chat

Runtime: the Anthropic Messages API from web, in `desk/chat/loop.py`, about 150 lines over `messages.stream` with the pinned `anthropic` package. The claude-agent-sdk is rejected: it wraps a 234 MB CLI whose version drifted between machines and whose PATH probe broke health, it needs a subprocess, session files on one container's disk and an env scrub, and every built-in tool would be disallowed anyway, the one case R7 recommendation 2 reserves the SDK for. A chat lane in the worker is rejected because it would put the user's decryption key beside the operator's key in one process.

Model: `claude-sonnet-5` by default, `claude-opus-5` per user on Account and per conversation, shown with the measured numbers ("Opus: median 0.83 USD and 102 s per reply in the old app"). The system prompt is frozen text over 1,024 tokens with one `cache_control` breakpoint, read at $0.20 per MTok after the first turn.

Tools: every session-tier panel except `me`, `conversations` and `conversation`, as `panel_<name>`, plus two. `propose_solve(settings)` returns a validated settings block and an estimate that the client renders as a card with a Run button; the button calls `POST /api/solver/run` with CSRF like any click, and the tool inserts nothing. `chart(spec)` returns `{type: bar | line | scatter, series, unit, source_panel}` for Recharts on the client; no code execution. The loop caps a turn at 12 tool calls and 20 KB per result. There is no raw SQL tool: on 2026-09-19 the agent planned from SQL around a player already sold.

The charter in `chat/charter.md` holds the role, `STYLE_RULES`, the panel fallback rule (if any tool returned `empty`, `ok: false` or `truncated`, the first line of the reply says so and names the panel), the user's entry id and next deadline, and the rule that every gain names its currency and horizon ("xPts over GW6 to GW10 versus rolling, consensus"). A test feeds a failing tool and asserts the first line names it.

Streaming: `POST /api/chat/{conversation_id}/turn` returns SSE. Each completed block is written to `chat_message.blocks` before the next event is sent, so a reconnect replays from `?after_seq=`. Idle watchdog 120 s, hard cap 600 s (measured p90 206 s, max 319 s, rule 41). Stop is a per-turn flag in the web process. At most 3 concurrent turns per process and 1 per user. A web redeploy ends every in-flight turn; the client shows it as ended by a deploy with the blocks that landed, and the runbook says so.

Cost visibility: usage times the price table is written to the assistant row as `usd` and rendered under every reply ("0.14 USD, 38 s, 4 tools, Sonnet"); the conversation header sums the thread; Account shows the month per model.

Prose gate: `normalize_prose` then `slop_findings` over the final text. With findings, one follow-up turn quotes them with "rewrite without these constructions", tools disabled, and the footer reads "style: 1 rewrite"; a second failure stores the text with `style_findings` and a visible chip. Em-dashes never reach storage.

Chat never writes to any table other than `conversation` and `chat_message`, starts a job, reads another user's rows (`ctx.user_id` is the session's), uses any credential except the one decrypted for this turn, or runs SQL.

### 9. UI

Stack per D8: React 19, TypeScript strict, Vite 8, Tailwind 4 over the token block, shadcn/ui (Sheet, Tabs, Tooltip, Dialog, Badge, Skeleton, Table, Command, Select, Toggle Group), TanStack Table 9 for every filterable table, TanStack Query 5 for every fetch, Recharts 3 for bars, lines and sparklines, custom SVG for `FixtureTicker`, `Pitch` and `Interval`, lucide-react as the one icon set. No webfont: system UI stack, `ui-monospace` for numbers, `tabular-nums` on `body`.

Design tokens, frozen in S8 before any feature screen, in `web/src/tokens.css`:

| Token group | Values |
|---|---|
| type | 19, 17, 13, 12.5, 11, 10.5 px; weights 400, 600, 700; spacing 4, 8, 12, 16, 24 px |
| surfaces | dark bg #101214, surface #16181b, ink #e8eaed; light bg #f7f8f9, surface #ffffff, ink #1a1d21; muted and faint ink derived per theme in S8 and asserted at 4.5:1 by a test |
| accent (selection only); status (state only, always with text) | #3e6c91; good #2fa463, warn #c77f00, bad #d64545 |
| series | #2a78d6, #c25322, #1baf7a, #b08000 |
| FDR ramp | FPL's five steps #375523, #01fc7a, #e7e7e7, #ff1751, #80072d; step 3 in dark is #3a3e45 |
| motion and layout | 120 ms state, 180 ms drawer, off under reduced motion; rail 168 px at 820 px and above, bottom tab bar under 820 px, drawer 560 px, content max 1,280 px |

Theme: `data-theme` on `<html>` wins, then `prefers-color-scheme`, then dark. Navigation in decision order: Dashboard, Fixtures, Projections, Solver, Creators, Chat. Account, theme and sign out in the header menu. Admin is a seventh entry only when `me.role` is `admin`.

Shared components, one per job; a second implementation is a test failure (rule 36):

| Component | Job |
|---|---|
| `DataTable`, `FilterRow`, `RowCount` | TanStack wrapper: every header sorts with `aria-sort`; one filter row; "N of M"; sort and filter state in the URL; sticky first column; thin-evidence dot; a row is clickable with a tab stop only when it opens something; no request on sort or filter |
| `Drawer`, `Breadcrumb`; `StateBox` | the one Sheet at 560 px, focus trap, Escape closes, one level per click, each level a URL; five kinds: empty (reason and fix from the payload), skeleton at final row count, error for a thrown request only, gap for a fact the payload lacks, stale with the age in days |
| `PlayerCell`, `ClubMark`, `Age`, `Number`, `CiteChip` | photo and crest in fixed boxes with fallbacks from `/api/assets/`; ages in days with the instant in a tooltip; mono, tabular, right aligned; the panel name, `as_of` and git sha behind every headline number |
| `Fold`, `ConfirmButton`, `Interval` | a closed disclosure whose summary states the finding and a count; the one confirm pattern; a Wilson interval drawn against 0.5 with n beside it, never a bare percentage |
| `FixtureTicker`, `Pitch`, `BarDiverging` | 20 clubs by horizon, two bands per cell on the FDR ramp, value under the colour, `@` for away; 15 cards in formation with price and xPts; buys right, sells left, captain marker, creator initials, own-squad rail |

Creators, level 1: the mention bar board for the next GW, a `BarDiverging` per player; under it one row per creator with latest episode title, age in days, claims and `Interval`. Level 2, a player: snippets grouped by show with a stance chip, the verbatim quote, episode title, age and a `source at m:ss` link; filter chips persist. Level 3: a creator drawer listing every episode with `transcribed` as a chip and never a default filter; an episode drawer with bullets, calls by player with offsets, and audio at the offset. Consensus, buy and sell takes, report cards and the said-versus-owned matrix stay as closed folds with counts.

Mobile: every screen renders at 390 px with no horizontal scroll; under 820 px tables hide `secondary` columns into the row drawer, the ticker scrolls inside its own box, the filter row collapses to one button and the drawer is full width.

Anti-clutter rules, each enforced by a test, the feature check or the prose check: one primary visual above the fold, under 1,400 px folded at 1,280; a header is the column name only, units once; six type sizes, no raw px; ages in days; nulls as a short dash and absence as the payload's reason; status colours mean state, magnitude uses one sequential hue, selection the accent; one icon set, no emoji; at most three panel calls and 250 KB per mount, data within 1,500 ms; no em-dash in any UI string; declutter means fold, and a screen rebuild carries a surface inventory (old location, new location) in its PR with no deletion without the owner's word (rule 35, incident `3126eea`).

The fractal: level 1 is the screen's summary; a click on any entity opens the one Drawer at level 2; a click on a source inside it opens level 3 in the same drawer with a Breadcrumb (the episode at the audio offset, the provider row with its `as_of`, the fixture's inputs, the job with its log). Every level is a URL that reloads to the same state, for example `/creators/p/223094/i/<item_id>` for level 3 and `/fixtures/t/<team_code>/gw/<gw>`, `/projections/p/<code>`, `/solver/run/<job_id>` for level 2.

### 10. Testing and gates

Unit tests per module in `tests/unit/`, no network, no credentials, no metered API: `conftest.py` sets `DESK_DISABLE_NETWORK=1` and the shared httpx client raises on any host outside an allow list. Fixture instants are computed 30 days ahead of `now()`; a grep test refuses ISO dates under `tests/fixtures/`. Postgres in tests comes from testcontainers locally and a service container in Actions.

The first tests in the repo, each of which caught a real outage: `test_entry_points` (every task answers `--help` with output); `test_model_pins` (AST walk, `core/models.py` is the only module matching `claude-`); `test_layout_doc` and `test_runbook_sync`; `test_routes_matrix` (three cases per route, unclassified fails closed); `test_env_split` (each service refuses the other's variables; `desk/chat` never names the operator variable); `test_ledger` (an unexplained nothing lands as `error`; a refusal is never `ok`; `rows_written` is counted by the helper on a task that lies); `test_pit`, `test_element_id`, `test_credential_sentinel`, `test_prose_style`; `test_time` (naive refused, Z-less parsed as UTC, the deadline reads 17:30Z under a hostile process timezone).

Registry walks in `tests/registry/`: `test_providers_walk` (every row has `key`, `licence`, `cadence`, `consensus_member`, `retired_on` and a sample under `tests/fixtures/providers/<key>/`; `parse()` yields records with `xp` in [0, 25], `xmins` in [0, 96], `p_appear` in [0, 1]; a null `gw` or `code` rejects the frame; identical bytes re-imported write 0 rows), `test_panels_walk` (section 6), `test_jobs_walk` (section 5), `test_artefacts_walk` (every kind declares `inputs_model`, `payload_model`, `state()`, a reader panel and a retention rule; `state()` returns each of the five words).

Contract tests, one per panel: example params on a seeded Postgres; the result validates with `extra="forbid"`; `provenance.inputs` names every table read; the empty envelope validates on an empty database; the response is under the byte budget; the committed snapshot makes any change a reviewed diff; `schema.d.ts` is regenerated in CI and compared.

Solver: the 14 acceptance tests from R3 section 6.5 on a committed 592-player fixture pruned to 20 per position so the suite runs under 60 s; the objective recomputed from decisions to 1e-6; determinism with chips in play; `NoIncumbentError` on a 1 s limit. Frontend: `tsc --noEmit`, eslint, `vite build`, vitest for `DataTable`, `StateBox` and `Age`, Playwright over every screen at 1,280 and 390 in both themes. Prose: `scripts/prose_check.py` over `docs/`, the two prompts and every string literal under `web/src/screens`; a finding fails CI.

Feature check, `scripts/feature_check.py`, runs after every deploy and every 6 hours against `PUBLIC_URL` with a session minted by `desk mint-check-session` for the check user (entry 4490171, its own low-limit Console key in Actions secrets). One line per feature; a failure names it (rule 26: the old deploy check passed while chat, the brief and extraction were dead):

| Feature | Assertion |
|---|---|
| health, me | 200; `git_sha` equals the deploy; `alembic_head` equals expected; `worker.age_s` under 180; `anthropic_sdk` equals the lock; `next_step: ready` |
| fixtures, projections | 20 clubs, newest `fixture_ratings` under 168 h; next GW has `n_sources >= 2` for 500 or more players, newest fplform `as_of` under 36 h |
| solver | latest plan in `fresh` or `aging`, or a run at horizon 2 and 30 s completes within 90 s |
| creators | newest transcript and claim under 48 h for 5 of 8 creators; captions `ok` or `skipped_no_proxy` |
| chat | "reply with the single word ok" reaches `done` under 60 s with `cost_usd` under 0.05 |
| admin, assets | Run-now on `fixture_fit` lands `status ok` within 2 minutes; no task in `failing` or `never_ran`; `/api/assets/club/{code}.png` is 200 with a 7 day cache header |
| ui budget | per screen, in a headless browser: at most 3 panel calls, under 250 KB, layout shift 0, no hour-unit age, no horizontal scroll at 390 |

CI on every push and PR, about 4 minutes: `uv sync --frozen`; ruff with a rule banning `**kwargs` in `cli.py` and `fplapi/` (rule 12); `mypy --strict desk`; pytest against a Postgres service container; the prose checker; `npm ci`, type regeneration with a diff check, `tsc --noEmit`, eslint, `vite build`; contract drift.

Main is always green: branch protection requires `ci.yml`; there is no frozen baseline of failing tests, no `xfail` without an issue link and a date, no merge while red; a test that cannot be fixed in the same PR is deleted in that PR with the reason in the commit; every fix commit carries a `Break-watch:` trailer naming the test that failed against the reverted code (rule 31).

### 11. Build order

Ten steps in build order; ids are stable labels for the spec writers. Each step is one Claude Code session with `docs/SPEC.md` and its own block as the brief, ending with a push to main, green CI, a Railway deploy and its feature-check line green. File counts exclude tests. A step does not start until the previous feature check is green. One target deadline per step from GW6; a week may hold two steps.

| Order | Id | Goal | Depends on | Target |
|---|---|---|---|---|
| 1 | S0 | platform skeleton: auth, roles, Postgres, Railway web and worker, jobs runner, CI, feature check | none | before GW6 |
| 2 | S1 | FPL data sync, settlement, squads, backfill | S0 | before GW7 |
| 3 | S8 | UI shell, tokens, component kit, Account, Admin, Dashboard v0 | S1 | before GW8 |
| 4 | S2 | fixture projections | S1, S8 | before GW9 |
| 5 | S3 | score projections from providers plus consensus | S1, S8 | before GW10 |
| 6 | S4 | solver | S3 | before GW11 |
| 7 | S6 | creators pipeline: discover, transcribe, captions, extract | S1 | before GW12 |
| 8 | S9 | creators settle, score, consensus, the three-level screen | S6, S8 | before GW13 |
| 9 | S7 | chat | S3, S4, S9 | before GW14 |
| 10 | S5 | dashboard v1, the brief, operations, the final light audit | S2, S3, S4, S9 | before GW15 |

**S0. Platform skeleton.** Goal: an empty app as two services with sign-in, roles, a job runner and a green feature check. Demo, before GW6: sign in, paste 4490171, confirm "This is me" against the name and rank shown back, Run-now `noop` and read its row, read the sha and a heartbeat under 60 s on `/api/health`. Done when: three feature-check lines green; the S0 tests of section 10 pass; `/auth/dev` is 404 on Railway; a second Google account sees its own empty account; `entry/4490171/` answered from Railway's egress, recorded in `docs/adr/0001-fpl-egress.md` with the Webshare fallback if refused. About 36 files.

**S1. FPL data sync.** Goal: the FPL tables filling on schedule, settlement behind the gate, every user's squad, five seasons of history. Demo, before GW7: Run-now `fpl_poll` and read rows written, then 0 on a second run; read `me_squad` with 15 picks and `ft_source`; after GW6, watch `fpl_settle` refuse on Sunday and write 600 or more `player_fixture` rows on Monday. Done when: parsers pass on the archived bodies; the gate has a test per clause; the scoring map reproduces 113,260 `total_points` with 0 mismatches; `test_pit` and `test_element_id` pass; a second backfill writes 0 rows; the T-4h poll fires before GW7. About 18 files.

**S8. UI shell, component kit, Dashboard v0.** Goal: the design system and every shared component before any feature screen. Demo, before GW8: see the fifteen with prices and the GW8 countdown; switch to light; use the bottom tab bar on a phone. Done when: types generate from OpenAPI in CI; `test_panels_walk` passes; a grep test finds one Drawer, one DataTable and one Age; three screens in both themes at 1,280 and 390 pass 4.5:1 with no horizontal scroll; Dashboard mounts in 2 calls under 100 KB. About 30 files.

**S2. Fixture projections.** Goal: the Dixon-Coles fit as a daily job and the ticker. Demo, before GW9: read the verdict line for GW9 to GW14; switch the lens to defence; click a Man Utd cell and read p(CS), xG against and the input ages. Done when: the fit converges under 10 s and matches the old 2026-09-19 fit within 0.01 (intercept 0.186, home advantage 0.173, rho minus 0.094) on the same 1,566-match window; a fit over 168 h renders the stale state; mount is 1 call under 100 KB, layout shift 0; the fixtures line is green. About 14 files.

**S3. Score projections from providers plus consensus.** Goal: five providers, one long table, the consensus with spread, the FPLReview upload, provider scoring. Demo, before GW10: drop `fplreview_<epoch>.csv` and read the preview (on the GW6 sample: 656 resolved, 19 dropped); commit and see the column with its age and a thin-source dot where `n_sources` is 1; read MAE per provider per GW with `fpl_ep` beside the consensus. Done when: `test_providers_walk` passes for all five; the nine R2 validation checks each have a test; a re-upload of identical bytes writes 0 rows; the position-mean warning fires on a provider 40 percent hot; 13 columns fit 1,280 px; the projections line is green. About 20 files.

**S4. Solver.** Goal: the HiGHS port on the consensus, run as a subprocess job, with the plan ledger and the pitch. Defaults with each value's source: horizon 5 (this plan: the consensus thins to 2 sources at GW10 and 1 at GW12, R2); from FPLReview's published settings decay 0.85, FT value 1.75, ITB 0.10 per 1.0m, 300 s, 3 solve lines by no-good cuts, and bench weights sub 1 0.30, sub 2 0.10, sub 3 0.03, GK 0.03, vice-captain 0.05 (open-fpl-solver's {0.03, 0.21, 0.06, 0.002} as a named preset); from open-fpl-solver the banked ladder `ft_value_list` {2: 2.0, 3: 1.6, 4: 1.3, 5: 1.1}; hit cost 4 from the game rules; chips held; 20 candidates per position plus held, locked and banned; threads 1, seed 0. Request bounds: horizon 1 to 8, hits 0 to 8, seconds 30 to 300, candidates 10 to 60, locked and banned under 30 each. Demo, before GW11: press Solve; read a headline of the form "Rogers out, Le Fée in, +14.35 xPts over GW11 to GW15 versus rolling, consensus, 0 hits"; read the hit-taking best under Alternatives; lock Haaland and Solve again; make a real transfer and see the plan superseded. Done when: the 14 acceptance tests pass; a held-squad 5-GW solve at 20 per position finishes under 300 s on the worker; the writer refuses an XI outside `squad_before - out + in`; the artefact carries `squad_before`, `forecast_source` and `free_transfers_source`; one test per state word; the solver line is green. About 13 files.

**S6. Creators pipeline.** Goal: eight creators discovered, transcribed on Groq, captions behind the proxy check, claims extracted on Sonnet, visible on the Admin board. Demo, before GW12: read items seen, audio minutes, tokens and USD per run on the board; see `skipped_no_proxy` on the captions row; set a cap of 0 in a test environment and read `refused`. Done when: a transcript fixture extracts the expected claims with `start_s`; the `no_body` gate blocks a show-notes item and counts it; a cap refusal lands as `refused`; a 429 leaves the item queued; audio is deleted the night after its transcript lands; newest transcript under 48 h for 5 of 8 creators. About 14 files.

**S9. Creators settle, score, consensus, the screen.** Goal: hit rates with intervals and the three-level board. Demo, before GW13: read the bars for GW13; click a player and read snippets by show with quotes and `source at m:ss`; click a source and hear the audio at the offset under six bullets; click The FPL Wire and see every episode with a `transcribed` chip. Done when: an unscoreable claim becomes scored on a later run (incident `c789453`); a claim published after its deadline is `unscoreable`; a podcast plus YouTube pair counts once; mount is 1 call under 70 KB and under 1,400 px folded; the PR carries a surface inventory of the four folds; the creators line is green. About 16 files.

**S7. Chat.** Goal: BYOK chat over the panels with cost visibility and the prose gate. Demo, before GW14: paste a Console key; ask "captain Fernandes or Haaland in GW14"; watch four panels fold in; read a reply that leads with numbers and a footer of the form "0.19 USD, 41 s, 4 tools, Sonnet"; remove the key and read the 403 copy. Done when: the sentinel test passes; `sk-ant-oat` is rejected with the card sentence; a tool returning `empty` is named in the first line (tested); a prose finding triggers one rewrite; stop ends the stream within 5 s; a reconnect replays from `after_seq`; the check turn costs under 0.05 USD. About 16 files.

**S5. Dashboard v1, the brief, operations, the final audit.** Goal: one landing screen that answers transfer, captain, bench and chip, plus the season operations. Demo, before GW15: on Friday read transfer, captain, bench and chip in that order with confidence word and deciding field; see the plan captain beside the consensus captain from one payload; read the brief; repeat on a phone in light; read bucket bytes per prefix and the last restore check's counts on Admin. Done when: four rows sit within the first 700 px at 1,280; a contract test compares squad and captain across `dashboard`, `me_squad` and `solver_plan`; the brief's findings count is 0; a restore check has run with counts equal to production; every bucket prefix has a retention row; all feature-check lines green for seven days. About 14 files.

### 12. Migration from the old repo

Export once on the Mac from a read-only copy of `data/warehouse/fpl.duckdb` with `scripts/export_old_repo.py`, each dataset as `COPY (<query>) TO '<name>.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)` after `SET TimeZone='UTC'`:

```
cp data/warehouse/fpl.duckdb /tmp/wh.duckdb
uv run python scripts/export_old_repo.py --db /tmp/wh.duckdb --out /tmp/export
```

| Dataset | Query | Rows, size | Target |
|---|---|---|---|
| player fixtures 2022-23 to 2026-27 | `fact_player_fixture` | 115,809; 0.81 MB | `player_fixture` |
| identity and schedule | `dim_player`, `dim_team`, `fact_fixture`, `dim_event` | 13,781; 420; 9,586; 806; 0.2 MB | `player`, `team`, `fixture`, `event` |
| price and ownership series | `fact_player_state` | 135,391; 1.99 MB | `player_state` |
| projections; provider scores | `fact_projection WHERE source IN ('fplform','fpl_ep','gh_apex_airsenal','fplreview')`; `fact_projection_score` | about 150,000, 1.6 MB; 236 | `projection` with `gh_apex_airsenal` renamed `airsenal`; `provider_score` |
| items and transcripts | `content_item` with `transcript_segment` aggregated to JSONB per item | 1,070; 6.05 MB | `item` |
| claims, outcomes, scores | `content_claim` joined to `claim_outcome`; `creator_score` | 3,381; 3,355; 2,369; 0.37 MB | `claim`, `creator_score` |
| creators, people, their picks and history | `content_source`, `panel_person` as CSV pruned to the eight; `fact_manager_pick`, `fact_manager_gw` for verified creator entries | 42, 54; small | `creator`, `source` seed; `creator_pick`, `creator_gw` |
| FPLReview sample; rules; API bodies | `data/projections/fplreview/fplreview_1789744594.csv`; `fpl_edge/rules/registry.yaml`; 3 bootstrap, 1 live, 1 picks from `data/raw/fpl_api` | 74 KB; 207 lines; 5 MB | `tests/fixtures/providers/fplreview_csv/`; `desk/rules/registry.yaml` pruned of odds and ownership; `tests/fixtures/fpl/` |

Under 12 MB in total. Upload to `seed/2026-09-21/` and run `scripts/backfill.py` once from the worker shell in S1; it converts slash-form seasons, drops `element_type` 5 rows, keys on `code`, and writes one `job` row named `backfill` with counts. Left behind: 16 GB of audio, 240 MB of rivals bodies, 172 MB of API bodies, odds, understat, intel, ideas, the auth SQLite, the chat JSON store.

Code worth copying by hand, then adapted to Postgres: `fpl_edge/platform/prose_style.py` (verbatim); `platform/auth/keys.py`, `oauth.py`, `sessions.py`, `policy.py` (drop the `sk-ant-oat` branch, SQLite, per-user directories and `anon_is_owner`); `ingest/fpl_api.py` and `ingest/results.py` lines 78 to 118; `myteam/state.py` lines 425 to 475 and 517 to 527; `ingest/player_mapping.py` lines 100 to 135 (NFKD plus the explicit map for Ø Đ Ł Æ Œ ß Þ Ð and dotless ı); `models/points/scoring_map.py` with its history test and `rules/loader.py`; `models/team_goals/dixon_coles.py` and `platform/scripts/fixtures/ratings.py` lines 236 to 290; `ingest/projections/local_csv.py` lines 90 to 170, `fplform.py`, `github_csv.py` from line 232 and `eval/projection_scoring.py`; `ingest/content/analyze.py` lines 207 to 424 (schema, prompt, `_INSIGHT_RULES`, `_NOTES_PREAMBLE`, `validate_model_id`, `claims_from_analysis`), `scoring.py` and `consensus.py`; worktree `agent-a8716a774fd2d3aca/.../asr_hosted.py`; `opt/scoring.py` (`score_plan`, `replay_finances`); `pipelines/health.py`; the six unit tests named in section 10, `tests/audit/test_time_and_deadlines.py` and `scripts/feature_check.py`.

Rewrite from this spec: `fpl_edge/opt/milp.py` (the port follows open-fpl-solver, keeping the old exact integer sell-price arithmetic as a test), `chat_agent.py`, `fpl_edge/mcp/`, the rest of `fpl_edge/platform/` (32,863 lines), all of `web/dist` (19,129 lines), `pipelines/registry.py`, `scheduler.py`, `store/warehouse.py`, `views.sql`, `store/backup.py`, `content/{fetch,youtube,sources,pipeline}.py`.

### 13. Decisions and rejected alternatives

| Decision | Rejected | Reason |
|---|---|---|
| D1 Console API key only | stored `claude setup-token` | the legal page forbids storing Claude.ai credentials (R7) |
| D2 operator key, Sonnet, capped for jobs; Sonnet default and Opus per user for chat | the owner's Max login; Opus always | the briefing starved on the shared session limit; 0.83 USD and 102 s median per Opus turn |
| D3 Postgres for everything a request touches | DuckDB on a volume | one writer forced one replica and 173 MB read copies (R5) |
| D4 podcast RSS on Groq from the worker; captions through Webshare | the Mac ASR worker; captions only (R1) | the Mac slept; YouTube blocks datacentre IPs (R7) |
| D5 five providers, unweighted consensus, no own model | the engine forecast | 37 to 71 percent hot, never scored (R2) |
| D6 open-fpl-solver semantics on highspy, FPLReview defaults | the old PuLP model; the upstream package | the owner asked for FPLReview's solver; the package needs Python 3.14 |
| D6 fix: bench weights 0.30, 0.10, 0.03, GK 0.03, vice 0.05 under FPLReview's name; the FT ladder named as open-fpl-solver's | {0.03, 0.21, 0.06, 0.002} and the ladder labelled FPLReview | R7 section 4 gives those to open-fpl-solver; R7 section 3 gives FPLReview the first set and FT Value 1.75 only |
| D7 eight creators by id, Scout among them | 42 sources; "eight plus Scout" | 18 sources had zero transcripts; D7's eight include Scout |
| D8 React 19 with generated types | vanilla zero-build; SvelteKit | 66 regex contract tests replaced by a compiler |
| D9 one Dockerfile, scheduler in the worker, health with `cli: none` | scheduler in web; Railway Cron | a dead loop was invisible in web; Cron floors at 5 minutes with no ledger |
| D10 no crawls, odds, elite, Telegram, rank, FPL login | shipping them folded | none serves the six features (R1) |
| Chat as a Messages API loop in web (J1, J2) | the Agent SDK in web (J3); B's chat lane in the worker | two judges against one; every built-in tool would be disallowed; one pinned package; the worker never decrypts a user key |
| One `job` table as queue and ledger (A) with B's `artefact` beside it | B's `job` plus `job_run` | one row per run; the artefact row gives rule 19 a schema |
| `rows_written` counted by the helper; solve in a subprocess on its own lane; worker dead over 180 s (A) | task-reported counts; solve on the pipeline lane; 120 s (C) | ten old transcribe rows said 0 while transcripts landed; a 300 s solve must not delay a T-4h poll; six missed 30 s ticks |
| Synchronous Sonnet extraction; one shared brief a day | the Batch API; a brief per active user (C) | 5 items a day cost about $0.08; operator spend must not scale with strangers |
| Segments as JSONB on `item`; outcome columns on `claim` | a segment table; a `claim_outcome` table | 167,597 rows nobody joins on; an upsert with a `revision` counter answers the frozen-verdict incident |
| `fpl_ep` in the consensus behind `consensus_member` with its MAE shown | silent exclusion (A, R2) | D5 lists it; the flag makes the R2 change one row |
| Deploy `main` on push with check suites; feature check every 6 hours | a `deploy` branch (C); daily | one fewer moving part, the branch is the fallback; rule 26 wants the deployed URL watched between deploys |

### 14. Risks

**1. A run that wrote nothing reads as success.** 14 of the 41 learnings are this shape, from the 9 h exit-0 no-op (`fdbf916`) to four green nights on an odds cap no run could meet (`9cbe749`). Mitigation: the ledger helper counts rows itself and writes `error` on an unexplained nothing from S0; the closed status enum; `trigger` without a default; the `--help` test; the reader test per written table; the feature check asserts newest-row ages every 6 hours.

**2. The FPL API refuses Railway's egress IPs.** R7 section 6 left this unverified; the old app polled bootstrap 41 times from the same project. Mitigation: S0's done condition includes the `entry/4490171/` proof and an ADR; if refused, `fplapi/client.py` takes the Webshare pool behind `FPL_PROXY_URL`, metered on `proxy_mb`, at about 320 MB a month for six polls a day.

**3. The seams erode.** The old repo had the panel pattern and still grew a 3,880 line creators view, four drawers and a `raw_fetch` table with 0 readers (rules 13, 22, 36). Mitigation: the four registry walks, the routes matrix, `test_pit` and the one-component grep tests land in S0 and S8; the `contracts/` snapshots make a silent schema change a visible diff; a screen rebuild carries a surface inventory.

**4. A plan stalls or renders beside a squad it was not solved against.** R3 recorded a 60 s cap with no incumbent and an empty XI at zero objective; incident `da7be61` put an XI naming Ndiaye beside a squad card that did not list him. Mitigation: the solve runs in a subprocess on its own lane with the 300 s limit and 20 candidates per position; `NoIncumbentError` is shown as a gap; the artefact carries `squad_before` and `forecast_source`, the writer refuses an XI outside its own squad, `solver_plan` derives one state word from `inputs`, and a contract test compares squad and captain across three panels. If the deployed solve exceeds 300 s, the worker's RAM and CPU are raised first.

**5. Operator spend runs away, or the credential rule leaks.** The old briefing starved on the Max session limit (`dd5e9a7`), extraction ran on the most expensive model for weeks (`9dbe02d`), and a fallback to the operator's credential would have billed him for strangers (rule 40). Mitigation: three meters with caps and `reserve()` before spend, refusal as its own status, admin confirm on metered Run-now, one shared brief, the `no_body` gate, the operator variable under a name no SDK reads, the boot assertions, the grep and sentinel tests, and 403 before any model call when a user has no key.

Digest facts that contradict a decision, and the resolution: R1 item 7 (captions only) and R7 item 8 (audio on the Mac) against D4, resolved by R7's verified datacentre blocking and the Mac outage. R2 (exclude `fpl_ep`) against D5, resolved by the `consensus_member` flag. R7 item 2 (Messages API direct) against B, followed here. R7 item 9 (ECharts) against D8; the ticker is under 200 lines of SVG. R7 section 3 against D6 on the bench weights and the FT ladder; section 13 records the fix. R6 rule 16 (scheduler in the serving process) against D9; the heartbeat row makes a dead loop visible. D9 (SDK and CLI versions in health) against the Messages API choice; health reports `cli: none`. R3 section 6.2 (decay 1.0, FT ladder off) against D6; the settings fold exposes both.


# Part 4. Build order

The build order is decided in Part 3 section 11 and repeated here so it can be read on its own. The part number for each step is in the table in Part 0.

### 11. Build order

Ten steps in build order; ids are stable labels for the spec writers. Each step is one Claude Code session with `docs/SPEC.md` and its own block as the brief, ending with a push to main, green CI, a Railway deploy and its feature-check line green. File counts exclude tests. A step does not start until the previous feature check is green. One target deadline per step from GW6; a week may hold two steps.

| Order | Id | Goal | Depends on | Target |
|---|---|---|---|---|
| 1 | S0 | platform skeleton: auth, roles, Postgres, Railway web and worker, jobs runner, CI, feature check | none | before GW6 |
| 2 | S1 | FPL data sync, settlement, squads, backfill | S0 | before GW7 |
| 3 | S8 | UI shell, tokens, component kit, Account, Admin, Dashboard v0 | S1 | before GW8 |
| 4 | S2 | fixture projections | S1, S8 | before GW9 |
| 5 | S3 | score projections from providers plus consensus | S1, S8 | before GW10 |
| 6 | S4 | solver | S3 | before GW11 |
| 7 | S6 | creators pipeline: discover, transcribe, captions, extract | S1 | before GW12 |
| 8 | S9 | creators settle, score, consensus, the three-level screen | S6, S8 | before GW13 |
| 9 | S7 | chat | S3, S4, S9 | before GW14 |
| 10 | S5 | dashboard v1, the brief, operations, the final light audit | S2, S3, S4, S9 | before GW15 |

**S0. Platform skeleton.** Goal: an empty app as two services with sign-in, roles, a job runner and a green feature check. Demo, before GW6: sign in, paste 4490171, confirm "This is me" against the name and rank shown back, Run-now `noop` and read its row, read the sha and a heartbeat under 60 s on `/api/health`. Done when: three feature-check lines green; the S0 tests of section 10 pass; `/auth/dev` is 404 on Railway; a second Google account sees its own empty account; `entry/4490171/` answered from Railway's egress, recorded in `docs/adr/0001-fpl-egress.md` with the Webshare fallback if refused. About 36 files.

**S1. FPL data sync.** Goal: the FPL tables filling on schedule, settlement behind the gate, every user's squad, five seasons of history. Demo, before GW7: Run-now `fpl_poll` and read rows written, then 0 on a second run; read `me_squad` with 15 picks and `ft_source`; after GW6, watch `fpl_settle` refuse on Sunday and write 600 or more `player_fixture` rows on Monday. Done when: parsers pass on the archived bodies; the gate has a test per clause; the scoring map reproduces 113,260 `total_points` with 0 mismatches; `test_pit` and `test_element_id` pass; a second backfill writes 0 rows; the T-4h poll fires before GW7. About 18 files.

**S8. UI shell, component kit, Dashboard v0.** Goal: the design system and every shared component before any feature screen. Demo, before GW8: see the fifteen with prices and the GW8 countdown; switch to light; use the bottom tab bar on a phone. Done when: types generate from OpenAPI in CI; `test_panels_walk` passes; a grep test finds one Drawer, one DataTable and one Age; three screens in both themes at 1,280 and 390 pass 4.5:1 with no horizontal scroll; Dashboard mounts in 2 calls under 100 KB. About 30 files.

**S2. Fixture projections.** Goal: the Dixon-Coles fit as a daily job and the ticker. Demo, before GW9: read the verdict line for GW9 to GW14; switch the lens to defence; click a Man Utd cell and read p(CS), xG against and the input ages. Done when: the fit converges under 10 s and matches the old 2026-09-19 fit within 0.01 (intercept 0.186, home advantage 0.173, rho minus 0.094) on the same 1,566-match window; a fit over 168 h renders the stale state; mount is 1 call under 100 KB, layout shift 0; the fixtures line is green. About 14 files.

**S3. Score projections from providers plus consensus.** Goal: five providers, one long table, the consensus with spread, the FPLReview upload, provider scoring. Demo, before GW10: drop `fplreview_<epoch>.csv` and read the preview (on the GW6 sample: 656 resolved, 19 dropped); commit and see the column with its age and a thin-source dot where `n_sources` is 1; read MAE per provider per GW with `fpl_ep` beside the consensus. Done when: `test_providers_walk` passes for all five; the nine R2 validation checks each have a test; a re-upload of identical bytes writes 0 rows; the position-mean warning fires on a provider 40 percent hot; 13 columns fit 1,280 px; the projections line is green. About 20 files.

**S4. Solver.** Goal: the HiGHS port on the consensus, run as a subprocess job, with the plan ledger and the pitch. Defaults with each value's source: horizon 5 (this plan: the consensus thins to 2 sources at GW10 and 1 at GW12, R2); from FPLReview's published settings decay 0.85, FT value 1.75, ITB 0.10 per 1.0m, 300 s, 3 solve lines by no-good cuts, and bench weights sub 1 0.30, sub 2 0.10, sub 3 0.03, GK 0.03, vice-captain 0.05 (open-fpl-solver's {0.03, 0.21, 0.06, 0.002} as a named preset); from open-fpl-solver the banked ladder `ft_value_list` {2: 2.0, 3: 1.6, 4: 1.3, 5: 1.1}; hit cost 4 from the game rules; chips held; 20 candidates per position plus held, locked and banned; threads 1, seed 0. Request bounds: horizon 1 to 8, hits 0 to 8, seconds 30 to 300, candidates 10 to 60, locked and banned under 30 each. Demo, before GW11: press Solve; read a headline of the form "Rogers out, Le Fée in, +14.35 xPts over GW11 to GW15 versus rolling, consensus, 0 hits"; read the hit-taking best under Alternatives; lock Haaland and Solve again; make a real transfer and see the plan superseded. Done when: the 14 acceptance tests pass; a held-squad 5-GW solve at 20 per position finishes under 300 s on the worker; the writer refuses an XI outside `squad_before - out + in`; the artefact carries `squad_before`, `forecast_source` and `free_transfers_source`; one test per state word; the solver line is green. About 13 files.

**S6. Creators pipeline.** Goal: eight creators discovered, transcribed on Groq, captions behind the proxy check, claims extracted on Sonnet, visible on the Admin board. Demo, before GW12: read items seen, audio minutes, tokens and USD per run on the board; see `skipped_no_proxy` on the captions row; set a cap of 0 in a test environment and read `refused`. Done when: a transcript fixture extracts the expected claims with `start_s`; the `no_body` gate blocks a show-notes item and counts it; a cap refusal lands as `refused`; a 429 leaves the item queued; audio is deleted the night after its transcript lands; newest transcript under 48 h for 5 of 8 creators. About 14 files.

**S9. Creators settle, score, consensus, the screen.** Goal: hit rates with intervals and the three-level board. Demo, before GW13: read the bars for GW13; click a player and read snippets by show with quotes and `source at m:ss`; click a source and hear the audio at the offset under six bullets; click The FPL Wire and see every episode with a `transcribed` chip. Done when: an unscoreable claim becomes scored on a later run (incident `c789453`); a claim published after its deadline is `unscoreable`; a podcast plus YouTube pair counts once; mount is 1 call under 70 KB and under 1,400 px folded; the PR carries a surface inventory of the four folds; the creators line is green. About 16 files.

**S7. Chat.** Goal: BYOK chat over the panels with cost visibility and the prose gate. Demo, before GW14: paste a Console key; ask "captain Fernandes or Haaland in GW14"; watch four panels fold in; read a reply that leads with numbers and a footer of the form "0.19 USD, 41 s, 4 tools, Sonnet"; remove the key and read the 403 copy. Done when: the sentinel test passes; `sk-ant-oat` is rejected with the card sentence; a tool returning `empty` is named in the first line (tested); a prose finding triggers one rewrite; stop ends the stream within 5 s; a reconnect replays from `after_seq`; the check turn costs under 0.05 USD. About 16 files.

**S5. Dashboard v1, the brief, operations, the final audit.** Goal: one landing screen that answers transfer, captain, bench and chip, plus the season operations. Demo, before GW15: on Friday read transfer, captain, bench and chip in that order with confidence word and deciding field; see the plan captain beside the consensus captain from one payload; read the brief; repeat on a phone in light; read bucket bytes per prefix and the last restore check's counts on Admin. Done when: four rows sit within the first 700 px at 1,280; a contract test compares squad and captain across `dashboard`, `me_squad` and `solver_plan`; the brief's findings count is 0; a restore check has run with counts equal to production; every bucket prefix has a retention row; all feature-check lines green for seven days. About 14 files.


# Part 5. Step S0: platform skeleton

Step 1 of 10 in `docs/SPEC.md`. Follows `ARCH.md` in full. Every choice ARCH left open is marked `DECISION:` so the assembler can lift it. Repo `fpl-desk`, package `desk`, frontend `web/`. Spelling is British.

### 1. Goal

When S0 is done the owner can open the Railway URL, sign in with Google, store entry 4490171 after seeing his own name and rank shown back, and press Run-now on a job and read its ledger row, while a stranger sees a sign-in screen and `/api/health` and nothing else. The repo has two deployed services, a Postgres with managed backups, a job runner that records a wrote-nothing run as an error, CI that blocks a red push, and a feature check with three green lines against the deployed URL.

### 2. Depends on

None. S0 is the first step and creates the repo. Every later step depends on these S0 deliverables by name: tables `app_user`, `session`, `credential`, `job`, `spend`, `worker_heartbeat`; modules `desk.core.settings`, `desk.core.models`, `desk.core.time`, `desk.db.engine`, `desk.db.bucket`, `desk.jobs.runner.ledger`, `desk.jobs.registry.Job`, `desk.panels.registry.Panel`, `desk.auth.policy`; endpoints `GET /api/health`, `GET /api/panels/{name}`, `POST /api/admin/jobs/{task}/run`; the `me` panel; the CLI `desk`; `scripts/feature_check.py`; the two workflows.

### 3. User stories

User role:

1. A stranger opens `PUBLIC_URL`. Result: the sign-in screen with one button and one sentence; every `/api/panels/*` call except `me` answers 401.
2. A user signs in with Google. Result: a `session` row, the `desk_session` cookie, `me.next_step = set_team`, and the Account screen.
3. A user types 4490171 and presses "Look up". Result: a preview card with the manager name, team name and overall rank from `entry/4490171/`, buttons "This is me" and "Not me"; nothing stored yet.
4. A user presses "This is me". Result: `entry_id`, `entry_name` and `entry_confirmed_at` set, `me.next_step = ready`, the team line with a "Change" button.
5. A user signs in from a second Google account. Result: a second `app_user` row with `entry_id` null and `next_step = set_team`; nothing from the first account is visible.
6. A user presses "Sign out". Result: the `session` row deleted, cookies cleared, the sign-in screen.
7. A user opens `/admin`. Result: 403 from the API and the shell shows the Account screen; no Admin entry in the header.

Admin role:

8. The owner (email in `ADMIN_EMAILS`) signs in. Result: `role = admin`, an Admin entry in the header, `/admin` renders the jobs board with 2 rows, `noop` and `retention`.
9. The admin presses Run-now on `noop`. Result: 202 with a `job_id`, a queued row, and within 60 s `done`, `status ok`, `trigger admin`, `rows_written 0`, note `noop slept 0 s`.
10. The admin clicks the row. Result: the drawer at `/admin/jobs/{job_id}` with the counts, git sha and the last 64 KB of log.
11. The admin opens `/api/health`. Result: 200 with `git_sha` equal to the deployed commit, the two heads equal, `worker.age_s` under 60 and `worker.ok: true`.
12. The admin stops the worker on Railway and waits 3 minutes. Result: `/api/health` still 200 with `worker.ok: false` and `age_s` over 180; the board's worker strip turns amber.
13. The admin reads the Spend tab. Result: 3 meters, `anthropic_usd` 0 of 25 for the month, `groq_audio_s` 0 of 28,800 for the day, `proxy_mb` 0 of 2,000 for the month.

### 4. Data

Postgres 16, one database, Alembic migration `0001_platform`. Both services run `alembic upgrade head` at boot on a dedicated connection holding `pg_advisory_lock(7231)` for the duration; the second arrival finds nothing to do. Code whose `expected_head` differs from the database's `alembic_version` refuses to serve and health returns 503. Every timestamp is `timestamptz` and ends `_at`; the connection runs `SET TIME ZONE 'UTC'` on checkout; a naive datetime raises `NaiveDatetimeError` in `desk.core.time.require_aware` before any statement. Every key column is `NOT NULL`. Money is `numeric(12,4)` USD in `spend` and `job.usd`.

DECISION: `user_id` is `uuid` from `gen_random_uuid()`, never the Google `sub` (the old `g<sub>` directory key was the wrong shape, R5 section 1.4). The Google `sub` lives in its own unique column.

#### 4.1 `app_user`

Why: one row per person who has signed in, plus the feature-check user. Read by `me`, `admin_users`, every `ctx.user_id` lookup.

| Column | Type | Constraint |
|---|---|---|
| `user_id` | uuid | PK, default `gen_random_uuid()` |
| `google_sub` | text | unique, NULL only for the check user |
| `email` | text | NOT NULL, unique, stored lower-cased |
| `role` | text | NOT NULL, `CHECK (role IN ('user','admin'))` |
| `entry_id` | integer | NULL until confirmed, `CHECK (entry_id > 0)` |
| `entry_name` | text | NULL until confirmed |
| `entry_confirmed_at` | timestamptz | NULL until confirmed |
| `chat_model` | text | NOT NULL, default `'claude-sonnet-5'`, `CHECK (chat_model IN ('claude-sonnet-5','claude-opus-5'))` |
| `created_at`, `last_seen_at`, `updated_at` | timestamptz | NOT NULL |

Role is recomputed on every sign-in from `ADMIN_EMAILS` (comma-separated, lower-cased, exact match); removing an email demotes the account at its next sign-in.

#### 4.2 `session`

Why: server-side sessions so a copy of the table is a list of who signed in, never a bag of live cookies. Read by the middleware on every request.

| Column | Type | Constraint |
|---|---|---|
| `sid_hash` | text | PK, `sha256(sid)` hex |
| `user_id` | uuid | NOT NULL, FK `app_user` ON DELETE CASCADE |
| `csrf_hash` | text | NOT NULL, `sha256(csrf)` hex |
| `created_at`, `expires_at` | timestamptz | NOT NULL |
| `reissued_at` | timestamptz | NULL until the first 24 h re-issue |
| `user_agent` | text | NULL, first 200 characters |

Index `session_user_id (user_id)`. Expired rows are deleted by the `retention` job. Lifetimes: 30 days absolute, re-issued after 24 h with the same sid and a new `expires_at`.

#### 4.3 `credential`

Why: the user's Anthropic Console key at rest, one row per user. S0 ships the table, `desk/auth/credentials.py`, the regex, the card copy and the sentinel test; the routes and the Account card land in S7. DECISION: table and module land in S0 because the env split and the sentinel test are S0 boot assertions.

| Column | Type | Constraint |
|---|---|---|
| `user_id` | uuid | PK, FK `app_user` ON DELETE CASCADE |
| `ciphertext` | bytea | NOT NULL, AES-256-GCM output |
| `nonce` | bytea | NOT NULL, 12 bytes, fresh per write |
| `key_version` | smallint | NOT NULL, 1 for `CREDENTIAL_ENC_KEY`, 0 for `_PREV` |
| `last4` | text | NOT NULL, `CHECK (char_length(last4) = 4)` |
| `verified_at` | timestamptz | NOT NULL, the instant `messages.create(max_tokens=1)` succeeded |
| `created_at`, `updated_at` | timestamptz | NOT NULL |

The paragraph for the owner (D1): Anthropic's legal page at https://code.claude.com/docs/en/legal-and-compliance, read 2026-09-21, states that third-party developers may not offer Claude.ai login, may not route requests through Free, Pro or Max plan credentials on behalf of their users, and may not collect, store or intermediate Claude.ai credentials or session tokens; it directs developers to Console API keys. The old repo's 2026-09-20 correction accepting `sk-ant-oat` tokens stored exactly such a credential, and Anthropic enforces the rule server side (R7 section 1). This app therefore has one credential kind, `^sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}$`, and a paste starting `sk-ant-oat` answers 400 with "That is a Claude Code token and cannot be used here. Paste a Console API key." The owner's Max subscription is for his own Claude Code sessions and never touches the deployed app.

#### 4.4 `job`

Why: queue and ledger in one table; a queued row becomes the run record when claimed; a task that fires ten times has ten rows. Read by `admin_jobs`, `admin_job_log`, `jobs/health.py`, the tick, the reaper.

| Column | Type | Constraint |
|---|---|---|
| `job_id` | bigint | PK, generated always as identity |
| `task` | text | NOT NULL, a name in `JOBS` |
| `lane` | text | NOT NULL, `CHECK (lane IN ('pipeline','solve'))` |
| `trigger` | text | NOT NULL, no default, `CHECK (trigger IN ('schedule','admin','user','chain','catchup','cli'))` |
| `user_id` | uuid | NULL, FK `app_user` ON DELETE SET NULL |
| `parent_job_id` | bigint | NULL, FK `job` |
| `params` | jsonb | NOT NULL, default `'{}'` |
| `priority` | integer | NOT NULL, default 100, lower runs first |
| `due_at` | timestamptz | NOT NULL |
| `state` | text | NOT NULL, `CHECK (state IN ('queued','running','done'))` |
| `claimed_by` | text | NULL, `worker_id` |
| `claimed_at`, `heartbeat_at`, `started_at`, `finished_at` | timestamptz | NULL |
| `status` | text | NULL, `CHECK (status IN ('ok','nothing_to_do','refused','error','skipped_overlap','skipped_no_proxy','skipped_no_body'))` |
| `items_seen`, `items_done`, `rows_written`, `rows_unchanged` | integer | NULL until done, then NOT NULL by the helper |
| `items_skipped` | jsonb | NULL until done; `{reason: count}` |
| `audio_seconds` | numeric(12,2) | NULL |
| `tokens_in`, `tokens_out` | integer | NULL |
| `usd` | numeric(12,4) | NULL |
| `model` | text | NULL, a bare model id |
| `note` | text | NULL |
| `log_tail` | text | NULL, last 65,536 bytes |
| `git_sha` | text | NOT NULL, the worker's `GIT_SHA` at claim time; the inserting process's sha until then |
| `created_at` | timestamptz | NOT NULL |

Table constraints: `CHECK (status IS NULL OR state = 'done')`; `CHECK (status IS NULL OR status NOT IN ('nothing_to_do','refused','skipped_overlap','skipped_no_proxy','skipped_no_body') OR note IS NOT NULL)`. Indexes: `job_claim (lane, state, priority, due_at)`; `job_task_finished (task, finished_at DESC)`; unique partial `job_slot (task, due_at) WHERE trigger IN ('schedule','catchup')` so a second tick during a deploy cannot double-insert a slot. DECISION: the unique index is partial, because two admin Run-now clicks are two legitimate rows.

Sample rows (columns not shown are NULL):

```
job_id 41  task noop       lane pipeline  trigger admin     due_at 2026-09-22T09:14:03Z
  state done  status ok  items_seen 1  items_done 1  items_skipped {}  rows_written 0
  rows_unchanged 0  note "noop slept 0 s"  git_sha 3f9c1a2  started_at ...:14:07Z  finished_at ...:14:07Z
job_id 42  task retention  lane pipeline  trigger catchup   due_at 2026-09-22T04:00:00Z
  state done  status nothing_to_do  items_seen 0  items_done 0  items_skipped {}  rows_written 0
  rows_unchanged 0  note "0 job rows older than 180 d; 0 sessions expired"  git_sha 3f9c1a2
```

#### 4.5 `spend`

Why: one row per meter per period; `reserve()` reads and updates it before any metered call. Read by `admin_spend`, `jobs/spend.py`.

| Column | Type | Constraint |
|---|---|---|
| `meter` | text | PK part, `CHECK (meter IN ('anthropic_usd','groq_audio_s','proxy_mb'))` |
| `period` | text | PK part, `YYYY-MM` for monthly meters, `YYYY-MM-DD` for `groq_audio_s` |
| `amount` | numeric(12,4) | NOT NULL, default 0 |
| `cap` | numeric(12,4) | NOT NULL, copied from the cap variable when the row is created |
| `updated_at` | timestamptz | NOT NULL |

Rows are created on first `reserve()` and by `desk seed`, from `MONTHLY_CAP_ANTHROPIC_USD` 25, `DAILY_CAP_GROQ_AUDIO_S` 28,800 and `MONTHLY_CAP_PROXY_MB` 2,000. No S0 task is metered; S6 adds one by naming a meter in a registry row.

#### 4.6 `worker_heartbeat`

Why: a dead worker must be visible on health without failing the web deploy. Read by `GET /api/health` and `admin_jobs`.

| Column | Type | Constraint |
|---|---|---|
| `worker_id` | text | PK, `hostname:pid` |
| `started_at`, `last_tick_at` | timestamptz | NOT NULL |
| `tick_count` | bigint | NOT NULL |
| `git_sha` | text | NOT NULL |
| `running` | jsonb | NOT NULL, `{pipeline: job_id or null, solve: job_id or null}` |

Rows older than 24 h are deleted by `retention`. Health reads `max(last_tick_at)`.

Sources and cadence: `app_user` and `session` change on sign-in, sign-out and confirmation; `job` every tick and run; `spend` on reserve; `worker_heartbeat` every 30 s. No series table exists yet, so `desk/db/pit.py` ships `latest_at` with a test on a temporary table; `test_pit` grows teeth in S1.

### 5. Jobs

Registry row `Job` in `desk/jobs/registry.py`, a frozen dataclass: `name`, `lane`, `due` (one of `Calendar(hours, minute)`, `Interval(seconds)`, `DeadlineRelative(offsets_s)`, `After(task)`, `OnDemand()`), `stale_window_s`, `writes` (table names or artefact kinds), `read_by` (panel names), `meters`, `budget_s`, `main` (a callable `(ctx: RunCtx) -> Receipt`). `JOBS` is a tuple of 2 rows in S0. `docs/runbook.md` is generated from it by `desk runbook` and pinned by `test_runbook_sync`.

The ledger helper `desk/jobs/runner.py::ledger(job_row, registry_row)` wraps every task: marks `running`, records `started_at` and `git_sha`, counts `count(*)` on each `writes` table before and after (on `job` it counts `state = 'done'` rows), catches every exception, and writes `status`, the counts, the note and the log tail. `rows_written` is the sum of `|after - before|` over the `writes` tables. DECISION: absolute delta, so a retention delete is a written row for the purpose of the silent-nothing rule. The rule: `items_seen > 0`, `rows_written = 0`, `items_skipped` empty and status `ok` lands as `error` with note `wrote_nothing_unexplained`. DECISION: the rule applies to tasks with a non-empty `writes`; `test_jobs_walk` asserts `noop` is the only task allowed an empty `writes`.

A task returns a `Receipt`: `items_seen`, `items_done`, `items_skipped: dict[str, int]`, `rows_unchanged`, `note`, `status` in `{ok, nothing_to_do, refused, skipped_no_proxy, skipped_no_body}`. A raised exception is `error` with the traceback tail in `log_tail`.

#### 5.1 `noop`

| Field | Value |
|---|---|
| trigger | `OnDemand`; inserted by `POST /api/admin/jobs/noop/run` (trigger `admin`) or `desk run-task noop` (trigger `cli`) |
| inputs | `params.sleep_s` integer 0 to 5, default 0 |
| outputs | none; `writes = ()`, `read_by = ("admin_jobs",)` |
| ledger row | `items_seen 1`, `items_done 1`, `rows_written 0`, `status ok`, note `noop slept {n} s` |
| cost cap | none; `meters = ()` |
| timeout | `budget_s = 10`; the runner kills a task at `budget_s + 30` |
| failure | `params.sleep_s` outside 0 to 5 raises `ValueError` and lands as `error` |
| if it silently did nothing | the row stays `queued` or lands without `status ok`; the feature check's `admin` line prints `admin: job {id} not done after 120 s`, and `admin_jobs.rows[noop].last_run` is null on the board |

#### 5.2 `retention`

| Field | Value |
|---|---|
| trigger | `Calendar(hours=(4,), minute=0)`, cron `0 4 * * *` UTC; `catchup` on boot when the newest missed slot is within 36 h |
| inputs | `job` rows with `state = 'done'` and `finished_at < now - 180 d`; `session` rows with `expires_at < now`; `worker_heartbeat` rows with `last_tick_at < now - 24 h` |
| outputs | those rows deleted; `writes = ("job", "session", "worker_heartbeat")`, `read_by = ("admin_jobs", "admin_users")` |
| ledger row | `items_seen` = candidates across the three tables, `items_done` = deleted, `rows_written` = absolute delta, `status ok`; with 0 candidates `status nothing_to_do` and note `0 job rows older than 180 d; {n} sessions expired; {m} heartbeats stale` |
| cost cap | none |
| timeout | `budget_s = 60` |
| failure | any exception lands as `error`; the next slot retries tomorrow at 04:00; two consecutive errors put the task in `failing` on health |
| if it silently did nothing | after 36 h with no `ok` or `nothing_to_do` row the health state is `stale`, `GET /api/health` lists `retention` under `jobs.stale`, and the feature check `admin` line fails on it |

S1 adds `raw_payload` and bucket prefixes to this job; S5 adds `artefact` and the bucket byte counts.

#### 5.3 The worker process

`python -m desk.jobs.worker`, about 250 lines, one replica, `overlapSeconds = 0`. Boot: migrate under the lock, assert the env split, insert the heartbeat row. Every 30 s a tick computes the current slot per `Calendar` and `Interval` row and inserts a `queued` row with `trigger = schedule` when absent (the unique index makes this idempotent); on the first tick after boot the newest missed slot within 36 h per task is inserted as `trigger = catchup`, at most one per task; `DeadlineRelative` rows log `no event table yet` and are skipped until S1. Two claim threads, one per lane, run `SELECT ... FROM job WHERE lane = $1 AND state = 'queued' AND due_at <= now() ORDER BY priority, due_at FOR UPDATE SKIP LOCKED LIMIT 1`. A heartbeat thread updates `worker_heartbeat` and `job.heartbeat_at` of the running rows every 30 s. A reaper on every tick returns `running` rows with `heartbeat_at < now - 5 min` to `queued` with note `reaped` appended to `log_tail`. One log line per tick: `tick 412 fired [retention] claimed [] reaped [] 12 ms`. The `solve` lane is idle in S0; S4 fills it.

Admin Run-now: `POST /api/admin/jobs/{task}/run` inserts a `queued` row with `trigger = admin`, `user_id` the admin's, `due_at = now()`; when the task names a meter and the body lacks `confirm: true` it returns `{needs_confirm, meter, estimate, period_spend, period_cap}` and inserts nothing.

### 6. API and panels

The envelope every panel consumer receives, from `desk/panels/registry.py`:

```json
{ "ok":true,"panel":"me","result":{},"empty":null,
  "provenance":{ "computed_at":"2026-09-22T09:14:07Z","git_sha":"3f9c1a2",
                  "params":{},"inputs":[ { "kind":"table","id":"app_user","as_of":null } ],
                  "budget":{ "ms":200,"elapsed_ms":4,"over":false } } }
```

`empty` is `{ reason, fix }` with `result: null`. Errors share one shape: `{ "error": "<code>", "detail": "<sentence>", "remediation_url": "<path or null>" }`. Codes in S0: `not_signed_in` (401, remediation `/auth/login`), `forbidden` (403), `csrf` (403), `invalid_params` (400, plus `field`), `entry_not_found` (404), `fpl_unreachable` (502), `fpl_timeout` (504), `unknown_task` (404), `already_queued` (409, plus `job_id`), `panel_failed` (500, plus `panel`), `db_unavailable` (503). FastAPI's 422 is converted to `invalid_params` by one exception handler. The budget is 10 s per request; every panel declares `budget_ms` under that and reports `over` when exceeded. Two exceptions: `POST /api/account/entry/preview` and `PUT /api/account/entry` call FPL with a 6 s timeout and 2 retries and may take 20 s.

Routes and tiers, the whole `desk/auth/policy.py` table for S0:

| Method, path | Tier | Params | Result |
|---|---|---|---|
| `GET /api/health` | anonymous | none | section 6.1 |
| `GET /api/panels/me` | anonymous | none | section 6.2 |
| `GET /auth/login` | anonymous | `next` (path, optional) | 302 to Google with the handshake cookie |
| `GET /auth/callback` | anonymous | `code`, `state` | 302 to `next` or `/`, sets `desk_session` and `desk_csrf` |
| `GET /auth/dev` | anonymous | none | 302 to `/` with a session for `DEV_LOGIN_EMAIL`; 404 unless `APP_ENV = local` |
| `POST /auth/check` | token | header `Authorization: Bearer <FEATURE_CHECK_TOKEN>` | `{ "user_id", "email", "cookie": "desk_session=...", "csrf": "..." }`; 404 when the variable is unset |
| `POST /auth/logout` | session | none | 204, cookies cleared |
| `POST /api/account/entry/preview` | session | `{ "entry_id": int }` | `{ "entry_id", "player_name", "entry_name", "overall_rank", "started_event", "region" }` |
| `PUT /api/account/entry` | session | `{ "entry_id": int }` | the `me` result |
| `GET /api/panels/admin_jobs` | admin | none | section 6.3 |
| `GET /api/panels/admin_job_log` | admin | `job_id` int | section 6.3 |
| `GET /api/panels/admin_spend` | admin | none | section 6.3 |
| `GET /api/panels/admin_users` | admin | none | section 6.3 |
| `POST /api/admin/jobs/{task}/run` | admin | `{ "params": {}, "confirm": bool }` | 202 `{ "job_id", "task", "trigger": "admin", "due_at" }` or 200 `{ "needs_confirm": true, "meter", "estimate", "period_spend", "period_cap" }` |
| `GET /openapi.json` | anonymous | none | the schema; `/docs` and `/redoc` are disabled |
| `GET /{path:path}` | anonymous | none | `web/dist/index.html` with `Cache-Control: no-cache`; hashed assets `max-age=31536000, immutable` |

DECISION: a fourth tier `token` exists for exactly one route, `POST /auth/check`; the middleware compares the bearer with `hmac.compare_digest` and creates or reuses the check user (`email check@fpl-desk.invalid`, `google_sub` NULL, `role user`, `entry_id 4490171`, `entry_name "Feature check"`, `entry_confirmed_at now()`), then mints a session and returns the cookie value in the body. The route is refused with 404 when `FEATURE_CHECK_TOKEN` is unset. `desk mint-check-session` does the same from a shell with database access and prints the cookie for local runs.

Middleware order: session, CSRF, tier. CSRF applies to `POST`, `PUT`, `PATCH`, `DELETE` under a `desk_session` cookie: `X-CSRF-Token` must hash to `session.csrf_hash`. An unclassified route fails closed to admin and fails `test_routes_matrix`. The entry preview is limited to 10 calls per minute per session; the 11th answers 429 `rate_limited`.

PUT `/api/account/entry` re-fetches `entry/{id}/` so the stored name is FPL's; preview and PUT share a 60 s in-process cache per id. Both call `desk/fplapi/client.py::entry(entry_id)`, the client's one S0 endpoint: `User-Agent: fpl-desk/{git_sha} (+{PUBLIC_URL})`, timeout 6 s, retries on transport errors and 5xx at 1.5 s then 3 s, 404 returned as `None`. S1 adds the other 8 endpoints.

#### 6.1 Health

```json
{ "ok":true,"now":"2026-09-22T09:14:07Z","git_sha":"3f9c1a2",
  "db":{ "reachable":true,"alembic_head":"0001_platform","expected_head":"0001_platform" },
  "worker":{ "last_tick_at":"2026-09-22T09:13:41Z","age_s":26,"ok":true },
  "runtime":{ "anthropic_sdk":"1.7.0","highspy":null,"cli":"none" },
  "jobs":{ "failing":[],"stale":[],"never_ran":[] } }
```

503 with `ok: false` when the database is unreachable or the heads differ; 200 with `worker.ok: false` when `age_s` exceeds 180. `highspy` is null until S4 adds the dependency. `jobs` lists task names from `desk/jobs/health.py` (section 7.4). Budget 2,000 ms.

#### 6.2 `me`

```json
{ "signed_in":true,"email":"owner@example.com","role":"admin",
  "entry_id":4490171,"entry_name":"Nripesh XI","has_key":false,"last4":null,
  "chat_model":"claude-sonnet-5","next_step":"ready","sign_in_url":"/auth/login" }
```

Anonymous: `signed_in false`, every nullable field null, `role null`, `next_step "sign_in"`. `next_step` is `policy.next_step(user)`: `sign_in` without a session, `set_team` while `entry_confirmed_at` is null, else `ready`. `add_credential` is the Chat screen's own check on `has_key` in S7. `me` never returns `empty`. Budget 200 ms, tier anonymous, so it is never a chat tool.

#### 6.3 Admin panels

`admin_jobs`, tier admin, no params, budget 1,000 ms:

```json
{ "worker":{ "worker_id":"web-7f3:12","last_tick_at":"...","age_s":26,"ok":true,"git_sha":"3f9c1a2" },
  "counts":{ "ok":1,"failing":0,"stale":0,"never_ran":0,"refused":0,"running":0 },
  "rows":[ { "task":"retention","lane":"pipeline","due":"daily 04:00 UTC","next_due_at":"2026-09-23T04:00:00Z","stale_window_s":129600,"writes":["job","session","worker_heartbeat"],"read_by":["admin_jobs","admin_users"],"meters":[],
      "health":{ "state":"ok","reason":"last run ok 5 h ago","consecutive_failures":0 },
      "last_run":{ "job_id":42,"trigger":"catchup","status":"nothing_to_do","started_at":"...","finished_at":"...","duration_ms":31,"items_seen":0,"items_done":0,"rows_written":0,"rows_unchanged":0,"usd":null,"model":null,"note":"0 job rows older than 180 d; 0 sessions expired; 0 heartbeats stale" },
      "recent":[ { "job_id":42,"status":"nothing_to_do","started_at":"...","duration_ms":31 } ],"running_job_id":null } ] }
```

`admin_job_log`, params `{ job_id: int }`: every `job` column except `claimed_by`, plus `log_tail`; `empty` reason `no job with id {n}`, fix `open the board and click a row`.

`admin_spend`: `{ "meters": [ { "meter": "anthropic_usd", "period": "2026-09", "amount": 0, "cap": 25, "unit": "USD", "fraction": 0 } ... ], "prefixes": [] }`; `prefixes` fills in S5.

`admin_users`: `{ "rows": [ { "user_id", "email", "role", "entry_id", "entry_name", "last4", "month_chat_usd": 0, "created_at", "last_seen_at" } ] }`, ordered by `last_seen_at` desc; `month_chat_usd` is 0 until S7 writes `chat_message`.

Provenance `inputs` for each: the tables read, `as_of` null (no series table yet). Chat tools later: none of the S0 panels; `me` is anonymous and the four admin panels are admin tier, and ARCH section 8 excludes both.

### 7. Algorithms

#### 7.1 Sign-in, PKCE

`GET /auth/login`: mint `state`, `nonce` and `code_verifier` with `secrets.token_urlsafe(32)`; `code_challenge = base64url(sha256(verifier))` unpadded, method `S256`; store `{s, n, v, next}` as JSON, signed, in cookie `desk_oauth` (HttpOnly, Lax, path `/auth`, 600 s); redirect to `https://accounts.google.com/o/oauth2/v2/auth` with `client_id`, `redirect_uri = {PUBLIC_URL}/auth/callback`, `response_type=code`, `scope=openid email`, `state`, `nonce`, `code_challenge`, `code_challenge_method`, `prompt=select_account`. `GET /auth/callback`: `hmac.compare_digest(state, handshake.s)`; exchange at `https://oauth2.googleapis.com/token` with the verifier and `GOOGLE_CLIENT_SECRET`; verify the id token with `PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", cache_keys=True, lifespan=3600)`, algorithms `RS256`, audience `GOOGLE_CLIENT_ID`, issuer in `{https://accounts.google.com, accounts.google.com}`, required claims `exp iss aud sub`, then `nonce` by `compare_digest` and `email_verified is True`; upsert `app_user` by `google_sub`, recompute `role`; mint a session. `safe_next` accepts only a path starting `/` and not `//`. All of this is lifted from `fpl_edge/platform/auth/oauth.py` lines 31 to 262, with `is_operator` replaced by the `ADMIN_EMAILS` role.

#### 7.2 Sessions and cookies

`sid = secrets.token_urlsafe(32)`; cookie `desk_session = sid + "." + base64url(hmac_sha256(SESSION_SECRET, sid))`; the row stores `sha256(sid)`. `csrf = secrets.token_urlsafe(32)` in cookie `desk_csrf` (readable, Lax) with `sha256(csrf)` on the row. Both cookies carry `Secure` when `APP_ENV = railway`. Constants: absolute 2,592,000 s, re-issue after 86,400 s, handshake 600 s. Lifted from `fpl_edge/platform/auth/sessions.py` `sign`, `unsign`, `needs_reissue`, with SQLite replaced by the `session` table. Validation: `unsign` rejects a bad signature before any database read; a forged sid costs one HMAC and no query.

#### 7.3 Credential encryption

`AESGCM(key)` with the 32-byte key from base64 `CREDENTIAL_ENC_KEY`; nonce 12 random bytes per write; associated data `user_id` as UTF-8, so a row moved between users fails to decrypt; `key_version` 1 for the current key, 0 for `CREDENTIAL_ENC_KEY_PREV`, and a row read under version 0 is re-encrypted under version 1 on first use. Regex `^sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}$`; the `sk-ant-oat` branch of `fpl_edge/platform/auth/keys.py` is not ported. Validation: `test_credential_sentinel` stores a key with a sentinel substring and asserts it appears in no response body, header, log record, `repr` or database byte outside `ciphertext`.

#### 7.4 Job due slots and health state

Slot for `Calendar(hours, minute)` at instant `t`: the latest `(day, hour, minute)` at or before `t` over the listed UTC hours. Slot for `Interval(seconds)`: `floor(epoch(t) / seconds) * seconds`. `next_due_at` is the following slot. `DeadlineRelative` is undefined until S1 and the tick logs and skips it. Catch-up on boot: for each row, the slot at `t` if it is within `t - 36 h` and no row exists for it, else nothing. Validation: a test at a frozen clock 03:59:59Z and 04:00:00Z asserts one row at exactly 04:00:00Z and none before; a second boot inserts nothing.

Health state per task, in this order, from `desk/jobs/health.py`:

| State | Condition |
|---|---|
| `running` | a row in `state = 'running'` |
| `never_ran` | no `done` row and `due` is `Calendar`, `Interval` or `DeadlineRelative` |
| `failing` | the newest `done` row has `status = 'error'`; `consecutive_failures` counts back from it |
| `refused` | the newest `done` row has `status` in `{refused, skipped_no_proxy, skipped_no_body}` |
| `stale` | the newest `done` row with status in `{ok, nothing_to_do}` finished more than `stale_window_s` ago, measured for `DeadlineRelative` against `next_due_at` from S1 |
| `ok` | otherwise |

`stale_window_s` in S0: `retention` 129,600 (36 h), `noop` none (OnDemand is never stale or never_ran). `GET /api/health.jobs` is the three lists `failing`, `stale`, `never_ran`. The old `health.py` states are kept minus `disabled` (no `enabled` flag exists). Validation: one test per state word on a seeded `job` table.

#### 7.5 The reaper and the kill

A `running` row with `heartbeat_at` older than 300 s returns to `queued` with `claimed_by` null. The runner joins a task at `budget_s + 30` and on timeout writes `status error`, note `timed out after {budget_s + 30} s`. Validation: test 15.

### 8. UI

S0 ships the Vite scaffold, `tokens.css` with the values from ARCH section 9, the router, a header shell, `StateBox` and `Age`, and three screens. DECISION: `StateBox` (five kinds) and `Age` (days, tooltip instant) land in S0 because every S0 screen needs them; S8 adds the rest of the kit and the one-implementation grep tests. The rail with the six sections is S8's; in S0 the header carries the app name, the user's email, `Admin` when `me.role` is `admin`, and `Sign out`. Dark is the default; `data-theme` on `<html>` wins, then `prefers-color-scheme`. Every string under `web/src/screens` passes `scripts/prose_check.py`.

#### 8.1 Sign-in, route `/signin`

Primary visual: one card. Heading `FPL Desk`. Sentence: `This app reads your Google email to identify you, and nothing else.` Button `Sign in with Google` (a link to `/auth/login?next=`). No filters, no drill-down. Loading: the card renders at once. Error: `?error=<code>` from the callback renders one line above the button, `Sign-in did not complete ({code}). Try again.` Empty state: none. Mobile at 390 px: the card is full width with 16 px gutters. Any protected route with no session redirects here with `next`.

#### 8.2 Account, route `/account`

Primary visual: the arrival card, two rows.

Row 1, identity: email, role as a `Badge` (`admin` only when true), button `Sign out` (POST with CSRF, then `/signin`).

Row 2, team. In `set_team`: label `FPL team id`, help text `Your FPL team id, from the URL of your points page: fantasy.premierleague.com/entry/4490171/event/5`, an `Input` of type number, button `Look up`. After a preview: a card reading `{player_name}`, `{entry_name}`, `Overall rank {overall_rank}` (mono, right aligned), buttons `This is me` and `Not me`. Errors: 404 `No FPL team with id {id}. Check the number in your points page URL.`; 502 or 504 `FPL did not answer. Try again in a minute.`; 429 `Too many look-ups. Wait a minute.` In `ready`: one line `{entry_name} ({entry_id}), confirmed {Age}` and button `Change`, which returns to the input. Loading on `Look up`: the button reads `Looking up` and is disabled. Empty state: none. Mobile: rows stack, buttons full width.

The credential card copy, shipped as `CREDENTIAL_CARD_COPY` in `desk/auth/credentials.py` and rendered by S7 on Account and in the Chat banner, verbatim:

> Chat runs on your own Anthropic API key, billed to your Anthropic account at API prices. Create one at console.anthropic.com under API Keys (it starts with `sk-ant-api`) and paste it here. This app shows the cost of every reply. The key is stored encrypted, shown back as its last four characters, used only for your chat turns, and removable here at any time. Removing it here does not revoke it at Anthropic. Claude.ai subscriptions (Free, Pro, Max) and `claude setup-token` values cannot be used here: Anthropic does not permit third-party apps to store or route them.

#### 8.3 Admin, routes `/admin`, `/admin/jobs/{job_id}`

Primary visual: the jobs board, a `Table` with columns `Task`, `State`, `Last run`, `Status`, `Rows`, `Next due`, `Run`. `State` is a `Badge` with status colour for `failing` (bad), `stale` and `refused` (warn), `ok` (good), `never_ran` and `running` (muted), always with the word. `Last run` is an `Age`. `Rows` is `rows_written` mono right aligned. `Run` is a `Button` reading `Run now`; while a row for that task is `queued` or `running` it reads `Queued` or `Running` and is disabled. Above the table: one worker strip, `Worker {worker_id}, tick {Age}, sha {git_sha}` in good or, when `ok` is false, `Worker silent for {age_s} s` in bad. Filter: one `ToggleGroup` over `All, Failing, Stale, Never ran, Ok`, default `All`, state in the URL as `?state=`. Sort: by task name ascending, fixed in S0 (`DataTable` sorting arrives in S8). Drill-down: a click on a row opens the `Sheet` at `/admin/jobs/{last_run.job_id}` with every field of `admin_job_log` in a two-column definition list and the `log_tail` in a `pre` block; Escape closes and returns focus to the row. A metered Run-now (none in S0; the pattern is built) opens a `Dialog`: `This run is estimated at {estimate} {unit}. {period} so far: {period_spend} of {period_cap}.`, buttons `Run`, `Cancel`.

Secondary content: `Tabs` under the board, `Spend` (three rows: meter, `{amount} of {cap} {unit}`, a 4 px bar in the sequential hue) and `Users` (a `Table` with `Email`, `Role`, `Entry`, `Key`, `Chat USD`, `Last seen`). Both default closed behind the tab strip; `Jobs` is the open tab.

States: loading is a `Skeleton` of 2 rows (the registry count is known); error is `StateBox kind=error` with the request's `detail`; a never-run job's drawer is `StateBox kind=empty`: `This job has not started. Its log will appear here when the worker claims it.`; a `job_id` with no row shows the panel's `empty.reason`. Mobile at 390 px: columns `Task`, `State`, `Last run`, `Run`; the rest move into the drawer, which is full width; the tab strip scrolls in its own box; no horizontal page scroll.

Shared components used: shadcn `Button`, `Card`, `Input`, `Badge`, `Table`, `Sheet`, `Dialog`, `Tabs`, `ToggleGroup`, `Skeleton`; new from the ARCH kit: `StateBox`, `Age`. `web/src/api/schema.d.ts` is generated by `scripts/gen_types.sh` from `desk export-openapi`, committed and diffed in CI; `usePanel("admin_jobs")` is typed by name.

### 9. Admin view

The admin sees, in S0: the jobs board with 2 rows and their health, last run, counts and next due; the worker strip; Run-now per row with the confirm pattern; the job drawer with every ledger column and the 64 KB log tail; the Spend tab with three meters against caps; the Users tab with email, role, entry id, last4 and month chat USD; the feature check's last result lands on this board in S5.

A user must never see: any `/api/panels/admin_*` payload (403), the Admin header entry, another user's email or entry id, a job's `params` or `log_tail`, any meter, any `last4`. The admin must never see: a credential in any form other than `last4`, chat content (none exists yet), a user's session id. `test_routes_matrix` proves the first list; `test_credential_sentinel` the second.

### 10. Acceptance tests

In the order to write them.

1. `test_env_split_web_refuses_operator_and_worker_vars`: `create_app()` under `ANTHROPIC_API_KEY`, `OPERATOR_ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `WEBSHARE_PROXY_USERNAME` or `CLAUDE_CODE_OAUTH_TOKEN` raises `BootRefused` naming the variable.
2. `test_env_split_worker_refuses_web_secrets`: `desk.jobs.worker.boot()` under `GOOGLE_CLIENT_ID`, `SESSION_SECRET` or `CREDENTIAL_ENC_KEY` raises `BootRefused`; a grep over `desk/chat` and `desk/web` finds no `OPERATOR_` literal.
3. `test_web_outside_local_requires_google_client`: `APP_ENV=railway` without `GOOGLE_CLIENT_ID` refuses to boot; `DEV_LOGIN_EMAIL` under `railway` refuses to boot.
4. `test_time_boundary`: `require_aware` raises on a naive datetime; `parse_fpl_ts("2026-08-21T17:30:00")` returns 17:30 UTC; the same under `TZ=America/Los_Angeles` returns 17:30 UTC.
5. `test_model_pins`: an AST walk over `desk/` finds the regex `claude-` only in `desk/core/models.py`, and each pin is a bare id, never an alias.
6. `test_migrations_head_and_lock`: on a fresh testcontainer `expected_head == alembic_head` after boot; two boots in parallel threads both return and leave one `alembic_version` row; a database at a fake older head makes `GET /api/health` return 503.
7. `test_sessions_sign_unsign_reissue`: a tampered signature returns `None` before any query; a session 25 h old is re-issued with a new `expires_at` and the same `sid_hash`; a session 31 days old is refused.
8. `test_google_callback_refusals`: a wrong `state`, a wrong `nonce`, a token with `email_verified false` and a token for another audience each answer 302 to `/signin?error=` with the code and create no row; a good token creates one `app_user` and one `session`, and a second sign-in with the same `sub` updates `last_seen_at` without a second user.
9. `test_routes_matrix`: for every route in `app.routes`, three callers (anonymous, user, admin) get the tier's expected status; a route added to the app without a policy row fails the test; `POST` without `X-CSRF-Token` under a session answers 403 `csrf`.
10. `test_dev_login_is_404_under_railway`: `GET /auth/dev` is 302 under `local` with `DEV_LOGIN_EMAIL` and 404 under `railway`.
11. `test_credential_sentinel`: a stored key containing `SENTINEL7Q` appears in no response, header, caplog record, `repr(Credential)` or `pg_dump` byte outside `ciphertext`; a row copied to another `user_id` raises `InvalidTag`; an `sk-ant-oat` paste is refused with the card sentence.
12. `test_ledger_unexplained_nothing_is_error`: a fake task with `writes=("session",)` that returns `items_seen 3`, writes nothing and skips nothing lands as `error` with note `wrote_nothing_unexplained`; the same task with `items_skipped {"already_present": 3}` lands as `ok`.
13. `test_ledger_refusal_and_counts`: a task returning `status refused` without a note raises at write time; a task that reports `rows_written 50` while the helper counts 0 lands with `rows_written 0`; a raised exception lands as `error` with the traceback tail in `log_tail` under 65,536 bytes.
14. `test_worker_tick_slots_and_catchup`: at a frozen 04:00:00Z the tick inserts exactly one `retention` row with `trigger schedule`; a second tick inserts nothing; a boot at 12:00Z with no row for 04:00Z inserts one `trigger catchup` row; a boot 40 h after the slot inserts nothing.
15. `test_reaper_and_timeout`: a `running` row with `heartbeat_at` 6 min old is `queued` after one tick with `reaped` in its log; a task sleeping 3 s under `budget_s 1` lands as `error` with note `timed out after 31 s`.
16. `test_jobs_walk_and_entry_points`: every `JOBS` row has a `stale_window_s` or an `OnDemand` due, every `writes` name is a table in the migration with a panel in `read_by` that exists in `PANELS`, `noop` is the only row with empty `writes`; `python -m desk.jobs.worker --help`, `python -m desk.cli --help` and `python -m desk.jobs.tasks.{noop,retention} --help` each print output; `docs/runbook.md` equals `desk runbook` output.
17. `test_panels_walk_and_contract`: every `PANELS` row forbids extras on both models, validates its `example_params`, returns `Empty` on an empty database without raising, appears in `web/src` as `usePanel("<name>")` or is admin tier, and matches its route tier; the `me` and `admin_jobs` results on a seeded database validate and equal `contracts/panels/<name>.json`; `schema.d.ts` regenerated equals the committed file.
18. `test_admin_job_log_empty_state`: `admin_job_log(job_id=999999)` returns `empty.reason == "no job with id 999999"` and `result null`; the drawer test in vitest renders that reason.
19. `test_health_states`: `worker.ok` is false at `age_s 181` and true at 179; `jobs.never_ran` lists `retention` before its first row and is empty after a `nothing_to_do` row; `jobs.stale` lists it 37 h after that row; `noop` never appears in any list.
20. `test_feature_check_s0`: `scripts/feature_check.py --lines health,me,admin` against `PUBLIC_URL` with `FEATURE_CHECK_TOKEN` prints `health ok`, `me ok`, `admin ok`, asserting `git_sha == GITHUB_SHA`, heads equal, `worker.age_s < 180`, `runtime.anthropic_sdk` equal to the lock, `me.next_step == "ready"`, a Run-now on `noop` at `status ok` within 120 s, and `jobs.failing`, `jobs.never_ran` and `jobs.stale` empty. DECISION: the `admin` line asserts `stale` too.

Every fix commit carries a `Break-watch:` trailer naming the test that failed against the reverted code.

### 11. Definition of done

- [ ] Tests 1 to 19 green locally with `uv run pytest` against a testcontainer; `tsc --noEmit`, eslint, vitest and `vite build` green.
- [ ] CI green on `main`: `uv sync --frozen`, ruff (with the `**kwargs` ban in `cli.py` and `fplapi/`), `mypy --strict desk`, pytest on the Postgres service container, `scripts/prose_check.py` over `docs/` and `web/src/screens`, `npm ci`, type regeneration diff, `tsc`, eslint, `vite build`, contract snapshot diff.
- [ ] Branch protection on `main` requires `ci.yml`; Railway deploys `main` on push with check suites on, or the `deploy` branch fallback is documented in `docs/runbook.md`.
- [ ] Deployed: `web` and `worker` from their `deploy/*.railway.toml`, `postgres` with daily and weekly managed backups on, `bucket` with its five variables on both services; the Google client lists `{PUBLIC_URL}/auth/callback`; every ARCH section 4 variable set on its service and on no other.
- [ ] `deploy-check.yml` green: waits for `git_sha == HEAD` on `/api/health`, runs `feature_check.py`, and also runs every 6 hours.
- [ ] Feature check lines `health`, `me`, `admin` green on the deployed URL.
- [ ] `docs/adr/0001-fpl-egress.md` written after `POST /api/account/entry/preview` with 4490171 from the deployed web service: the HTTP status, the response bytes and the date; if FPL refused, the ADR names the Webshare fallback (`FPL_PROXY_URL` in `fplapi/client.py`, metered on `proxy_mb`) as the S1 first task.
- [ ] The owner's demo script, in order: open `PUBLIC_URL` in a private window, read the sign-in card; sign in; type 4490171, press `Look up`, read the name and rank, press `This is me`; read the confirmed line on `Account`; open `Admin`, press `Run now` on `noop`, watch `Queued`, `Running`, `ok` within 60 s; click the row, read `trigger admin`, `rows_written 0`, the sha and the log; open `/api/health`, read `git_sha` and `worker.age_s` under 60; sign in from a second Google account, read `set_team` with nothing of the first account visible; open `/auth/dev`, read 404.
- [ ] Docs updated: `README.md` (every command exists in `cli.py`, `test_layout_doc`), `docs/SPEC.md` (this step's block marked done with the deploy date), `docs/runbook.md` (generated), `docs/adr/0001-fpl-egress.md`.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src scripts` returns nothing.
- [ ] The image self-check passes: no `sk-ant`, `AKIA` or `.env` in the built image.

### 12. Out of scope

| Not in S0 | Step |
|---|---|
| FPL polling, `event`, `team`, `player`, `fixture`, `player_state`, `raw_payload`, settlement, `squad_refresh`, `DeadlineRelative` slots, the 8 other client endpoints, the backfill, `test_pit` and `test_element_id` with real series tables | S1 |
| The rail, `DataTable`, `FilterRow`, `Drawer` with `Breadcrumb`, `PlayerCell`, `ClubMark`, `Number`, `CiteChip`, `Fold`, `ConfirmButton`, the theme toggle, the light audit, the asset proxy `/api/assets/`, Dashboard v0, the full Account and Admin screens on the kit, the one-implementation grep tests | S8 |
| `PUT` and `DELETE /api/account/credential`, the verify call, the Account credential card, `chat_model` selection, `has_key` becoming true | S7 |
| `artefact` table, `ARTEFACT_KINDS` rows and `store.py` (S0 ships the empty registry and its walk test) | S2 |
| `PROVIDERS` rows, `upload` (S0 ships the empty registry and its walk test) | S3 |
| The `solve` lane's subprocess runner | S4 |
| Metered tasks, `reserve()` in anger, `skipped_no_proxy` | S6 |
| `pg_dump_to_bucket`, `db_restore_check`, bucket clauses in `retention`, `admin_spend.prefixes`, the feature check result on the board | S5 |
| Any chat, any model call on any key | S7 |

### 13. Size and session plan

About 52 source files and 5,600 lines excluding tests; 22 test files and about 2,400 lines (ARCH's 36 excluded the Vite scaffold).

Order of writing:

1. Repo root: `pyproject.toml` (Python 3.12; the libraries of ARCH section 2 minus highspy, numpy, pandas, feedparser and youtube-transcript-api), `uv.lock`, `README.md`, `.github/workflows/ci.yml`.
2. `desk/core/`: `settings.py` with the fixed variable list per service and `BootRefused`; `models.py`; `prices.py` (the R7 table: Opus 5 in 5, out 25, cache read 0.5, write 6.25; Sonnet 5 in 2, out 10, 0.2, 2.5, per MTok); `time.py`; `prose_style.py` copied verbatim from `fpl_edge/platform/prose_style.py`.
3. `desk/db/engine.py`, `desk/migrations/` with `0001_platform`, `desk/db/pit.py`, `desk/db/bucket.py` (put_bytes, get_bytes, list_prefix, delete; tested with botocore `Stubber`).
4. `desk/auth/`: `sessions.py`, `google.py`, `credentials.py`, `policy.py`, `csrf.py`, `middleware.py`.
5. `desk/jobs/`: `registry.py`, `runner.py`, `spend.py`, `health.py`, `tasks/noop.py`, `tasks/retention.py`, `worker.py`.
6. `desk/panels/` (registry plus the five panels); `desk/providers/base.py` and `desk/artefacts/registry.py` as empty registries with walk tests.
7. `desk/fplapi/client.py` with `entry()`; `desk/web/app.py`, `routers/{health,auth,account,panels,admin}.py`, `static.py`; `desk/cli.py`.
8. `tests/` in the order of section 10.
9. `web/`: scaffold, `tokens.css`, `api/client.ts`, `api/usePanel.ts`, `app/router.tsx`, `app/Shell.tsx`, `components/StateBox.tsx`, `components/Age.tsx`, `screens/signin`, `screens/account`, `screens/admin`; `scripts/gen_types.sh`; `scripts/prose_check.py`.
10. `Dockerfile`, `deploy/*.railway.toml`, `scripts/feature_check.py`, `.github/workflows/deploy-check.yml`, `docs/runbook.md`, `docs/adr/0001-fpl-egress.md`, the Railway setup, the ADR run, `docs/SPEC.md` marked.

Where a session is most likely to go wrong, with the guard:

1. Auth enforcement drifts from the policy table. The old repo learned that a FastAPI dependency must be installed before any `include_router` (R5 section 1.4). Guard: enforcement is one Starlette middleware reading `policy.ROUTES` and `PANELS` tiers; no route carries its own auth dependency; `test_routes_matrix` walks `app.routes` including the SPA catch-all, and an unclassified route is an admin-only route and a red test.
2. The ledger helper counts `job` while the tick inserts into it, or the session forgets that a delete lowers the count. Guard: the helper counts `job` with `state = 'done'` only and uses the absolute delta; `test_ledger_refusal_and_counts` runs a tick thread during a fake task and asserts the counts are unaffected; `noop` is the one task allowed empty `writes`.
3. Deploy plumbing that passes locally. Three known traps: `uvicorn --factory` never calls `logging.basicConfig`, so `create_app` configures logging first (R5 section 5.3 item 6); the worker service has no HTTP port, so its Railway config has no `healthcheckPath` and health is the heartbeat row; the migration lock must be a session-level advisory lock on a connection Alembic does not use, released in `finally`. Guard: `test_migrations_head_and_lock` boots twice in parallel, the feature check runs after every deploy, and `docs/runbook.md` names all three.

Digest facts that contradict a decision and bear on S0, resolved as ARCH section 14 records: R6 rule 16 and R5 put the scheduler in the serving process; D9 puts it in the worker and the heartbeat row shows a dead loop on health. R5 and R7 want SDK and CLI versions on health; there is no CLI, so `runtime.cli` is `none`. R7 recommendation 7 names a `cron` service; D9 uses the `job` table. R5 section 2 describes the `sk-ant-oat` path; D1 drops it, section 4.3 has the URL.


# Part 6. Step S1: FPL data sync, settlement, squads, backfill

Step 2 of 10 in ARCH.md section 11. Depends on S0. Target: deployed and green before the GW7 deadline. Spelling is British; paths are relative to the repo root `fpl-desk/`, package `desk`. Where ARCH.md is silent, this spec decides and marks the choice `DECISION:`.

### 1. Goal

When this step is done the owner can watch the six FPL tables fill on schedule from Railway's worker, see a finished gameweek settle behind FPL's own gate the morning after the last match, and read his fifteen with purchase price, sell price, bank and free transfers from a panel, with five seasons of history behind it. Before this step the deployed app held users, sessions and an empty job ledger, and knew nothing about the game.

### 2. Depends on

S0 delivered the platform skeleton. If a named module is absent from the repo as S0 left it, create it here to the signature given.

| From S0 | What S1 uses |
|---|---|
| migration `0001` | `app_user (user_id, email, role, entry_id, entry_name)`, `job` (every column of ARCH section 5, unique index on `(task, due_at)`), `worker_heartbeat`, `spend` |
| `desk/core/settings.py`, `desk/core/time.py` | env reads and boot assertions; `parse_utc(value: str) -> datetime` (attaches UTC to a Z-less string), `require_utc(dt)`, `now()` |
| `desk/db/engine.py`, `desk/db/bucket.py` | `connect()` pinned to UTC; `put_bytes(key, data, content_type)`, `get_bytes(key)`, `list_keys(prefix)` |
| `desk/jobs/registry.py`, `runner.py`, `worker.py` | the frozen `Job` dataclass and `JOBS`; the due kinds `Calendar`, `DeadlineRelative`, `Interval`, `After`, `OnDemand`; the ledger helper that counts `rows_written` and `rows_unchanged` over `Job.writes` and lands an unexplained nothing as `error`; the 30 s tick, the pipeline lane, the reaper, the 36 h catch-up |
| `desk/panels/registry.py`, `desk/auth/policy.py`, `desk/web/routers/panels.py` | `Panel`, `PANELS`, `Ctx`, `Empty`, the envelope; the tier table; `GET /api/panels/{name}` |
| `desk/fplapi/client.py` | `FplClient.get_json(path) -> Fetched(body, sha256, fetched_at, http_status, bytes)` and `entry(entry_id)`; S1 adds eight endpoint methods |
| `desk/cli.py`, `scripts/feature_check.py`, `docs/adr/0001-fpl-egress.md` | `desk run-task <name> [--param k=v]`; the `FEATURES` table (S1 appends three rows); whether the FPL API answers from Railway, else the client reads `FPL_PROXY_URL` |
| endpoints | `GET /api/health`, `GET /api/panels/me`, `GET /api/panels/admin_jobs`, `POST /api/admin/jobs/{task}/run` |

S0 stubbed `DeadlineRelative.next_due()` to return `None`; S1 replaces the stub (section 5).

### 3. User stories

User role:

1. I saved team id 4490171 in S0. Within 90 minutes of the next deadline, `GET /api/panels/me_squad` returns 15 picks with price, purchase price, sell price, captain and vice marks, bank, free transfers and chips played.
2. I open `me_squad` on the Monday after a gameweek. The order is the fifteen I selected before the deadline; an automatic substitution FPL made at the weekend is reversed and marked on both picks.
3. I open `GET /api/panels/deadline`. It returns the next gameweek, its deadline instant, the seconds left and the state of the gameweek in play.
4. I call `POST /api/me/squad/refresh` twice in one hour. The first call queues a job and returns its id; the second returns `queued: false` with the next allowed instant.
5. I saved a team id a minute ago. `me_squad` returns the empty envelope with reason "Squad not fetched yet" and fix "A refresh is queued; it lands within 2 minutes of saving a team id".
6. Another signed-in user calls `me_squad` and gets their own squad or their own empty envelope; my rows are absent.

Admin role:

7. I press Run-now on `fpl_poll`. Within 2 minutes the row reads `ok`, `rows_written` over 600. A second press reads `nothing_to_do`, note `unchanged: 1,343 rows compared`, `rows_unchanged` over 600.
8. On the Sunday of a gameweek I press Run-now on `fpl_settle`: `nothing_to_do`, note `gate: GW6 not final: provisionally finished 8/10`. On Monday after 10:30 UTC the scheduled row reads `ok`, `rows_written` 600 or more, `event.settled_at` set for GW6.
9. I run `desk run-task backfill --seed seed/2026-09-21` from the worker shell: `ok` with six per-table counts summing to 275,398. A second run: `nothing_to_do`, note `seed already applied: 0 of 275,398 rows differ`.
10. I press Run-now on `squad_refresh`. `items_seen` equals the users with an entry id, `items_done` the rows written, `items_skipped` names every 404.
11. `GET /api/health` lists the three scheduled tasks under `never_ran` until each has one row, and under `stale` when a window passes with no `ok` row.

### 4. Data

One migration, `desk/migrations/versions/0002_s1_fpl_tables.py`, creates nine tables; it runs at boot under the S0 advisory lock and is not edited after merge. Conventions from ARCH section 3: singular snake_case; timestamps are `timestamptz` ending in `_at` (`as_of` on series tables excepted); money is integer `_tenths`; key columns are `NOT NULL`; season is text `2026-27`.

#### 4.1 `event`

Why: the calendar; deadline-relative jobs, the `deadline` panel and the settlement gate read it.

| Column | Type | Note |
|---|---|---|
| season, gw | text, smallint NOT NULL | `gw` checked 1 to 38 |
| deadline_at | timestamptz NOT NULL | `deadline_time`, the only time authority (R1 gotcha 1) |
| finished, data_checked, is_current, is_next | boolean NOT NULL | from `events[]` |
| finished_provisional | boolean NOT NULL | derived by `fpl_poll`: every fixture of the gw is provisionally finished |
| avg_entry_score, highest_score; ranked_count | smallint NULL; bigint NULL | NULL until `finished` (R1 gotcha 4) |
| settled_at | timestamptz NULL | set by `fpl_settle` |
| updated_at | timestamptz NOT NULL | |

Primary key `(season, gw)`. Partial unique index `event_one_next ON event (season) WHERE is_next`.

#### 4.2 `team`

Why: club identity by the stable `team_code`; `team_id` is a per-season 1 to 20. Columns: `season text`, `team_code integer`, `team_id smallint`, `name text`, `short_name text`, `updated_at timestamptz`, all NOT NULL. Primary key `(season, team_code)`, unique `(season, team_id)`. Ratings live in S2's `fixture_ratings` artefact.

#### 4.3 `player`

Why: the per-season map from `element_id` to the cross-season `code` (Haaland is 223094 in five seasons; his element id ran 318, 355, 351, 430, 411).

| Column | Type | Note |
|---|---|---|
| season, code | text, integer NOT NULL | `elements[].code` |
| element_id | smallint NOT NULL | `elements[].id` |
| web_name, first_name, second_name | text NOT NULL | empty string when absent |
| position | text NOT NULL | check in `('GKP','DEF','MID','FWD')` from `element_type` 1 to 4; type 5 is dropped and counted (R1 gotcha 7) |
| team_code | integer NOT NULL | resolved through `teams[]`; foreign key to `team` |
| has_temporary_code | boolean NOT NULL | counted on the ledger row (R1 gotcha 6) |
| photo | text NOT NULL | for S8's asset proxy |
| updated_at | timestamptz NOT NULL | |

Primary key `(season, code)`, unique `(season, element_id)`. `test_element_id` allows the column on `player` and `creator_pick` only.

#### 4.4 `player_state`

Why: the price, ownership, availability and `ep_next` series, append-only.

| Column | Type | Note |
|---|---|---|
| season, code, as_of | text, integer, timestamptz NOT NULL | `as_of` is the fetch instant |
| price_tenths | smallint NOT NULL | `now_cost` |
| selected_by_pct | numeric(5,1) NULL | NULL when the string is empty (R1 gotcha 5) |
| status | text NOT NULL | one of `a d i s u n` |
| chance_next | smallint NULL | `chance_of_playing_next_round`; NULL means unflagged |
| can_select, can_transact | boolean NOT NULL | `can_select` is the field the game enforces (R1 gotcha 8) |
| news | text NOT NULL | empty when absent |
| news_added | timestamptz NULL | |
| ep_next | numeric(5,1) NULL | parsed from the string |
| transfers_in_event, transfers_out_event | integer NOT NULL | |
| cost_change_start | smallint NOT NULL | |

Primary key `(season, code, as_of)`; index `(season, code, as_of DESC)`. Write-on-change over every non-key column (7.3). The transfer counters move every poll: about 660 rows per poll, 1.2 million a season, under 200 MB. The backfill lands 135,391 rows. Every read goes through `latest_at`.

#### 4.5 `fixture`

Why: schedule and result per match; the gate, S2's ticker and S4's horizon read it.

| Column | Type | Note |
|---|---|---|
| season, fixture_id | text, integer NOT NULL | |
| gw | smallint NULL | NULL when unscheduled |
| kickoff_at | timestamptz NULL | NULL until scheduled |
| home_team_code, away_team_code | integer NOT NULL | foreign keys to `team` |
| started, finished_provisional, finished | boolean NOT NULL | provisional flips at the whistle, finished when FPL processes (R1 gotcha 3) |
| home_score, away_score | smallint NULL | NULL until started |
| home_difficulty, away_difficulty | smallint NOT NULL | FPL's own FDR, kept so S2 shows the model beside it |
| pulse_id | integer NOT NULL | |
| updated_at | timestamptz NOT NULL | |

Primary key `(season, fixture_id)`; index `(season, gw)`.

#### 4.6 `player_fixture`

Why: the settled stat line per player per match, read by the scoring-map gate, S2, S3 and S9.

| Column | Type | Note |
|---|---|---|
| season, code, fixture_id, gw | text, integer, integer, smallint NOT NULL | foreign key `(season, fixture_id)` to `fixture` |
| was_home | boolean NOT NULL | `player.team_code = fixture.home_team_code` |
| minutes, goals_scored, assists, clean_sheets, goals_conceded, own_goals, penalties_saved, penalties_missed, yellow_cards, red_cards, saves, bonus, starts | smallint NOT NULL | 13 columns present in every season |
| bps | smallint NULL | NULL per fixture in a double gameweek (7.6) |
| tackles, cbi, recoveries, defensive_contribution | smallint NULL | NULL before 2025-26 in the archive: absent, not zero (R1 section 4) |
| xg, xa, xgc | numeric(5,2) NULL | NULL per fixture in a double gameweek |
| total_points | smallint NOT NULL | |
| settled_at | timestamptz NOT NULL | the observation instant |

Primary key `(season, code, fixture_id)`; index `(season, gw)`. Trigger `player_fixture_after_kickoff BEFORE INSERT OR UPDATE` raises `settled_before_kickoff` when `settled_at < fixture.kickoff_at`.

#### 4.7 `projection`

Why: the long table of provider forecasts; ARCH section 5 lists `projection (fpl_ep)` under `fpl_poll.writes`, so S1 creates it.

| Column | Type | Note |
|---|---|---|
| provider, season, gw, code, as_of | text, text, smallint, integer, timestamptz NOT NULL | `fpl_ep` in S1 |
| xp | numeric(6,2) NOT NULL | check 0 to 25 |
| xmins | numeric(5,1) NULL | check 0 to 96; NULL for `fpl_ep` |
| p_appear | numeric(4,3) NULL | check 0 to 1 |
| file_sha256 | char(64) NULL | set by S3's upload importer |

Primary key `(provider, season, gw, code, as_of)`; index `(season, gw, code, as_of DESC)`. Write-on-change over `xp, xmins, p_appear`. Read through `latest_at` only.

DECISION: `projection` is created in S1 with every ARCH column; S3 adds no column, only the `PROVIDERS` registry, four providers, `provider_score` and `upload`.

#### 4.8 `raw_payload`

Why: the index of raw bodies in the bucket; provenance for every fetch with no body archive on any disk (R6 rule 28).

| Column | Type | Note |
|---|---|---|
| payload_id | bigint identity | primary key |
| source, endpoint | text NOT NULL | `fpl`; the path, for example `event/6/live/` |
| params | jsonb NOT NULL | `{}` when none |
| sha256 | char(64) NOT NULL | of the uncompressed body; indexed |
| bucket_key | text NOT NULL | `raw/fpl/2026-09-22/<sha256>.json.gz` |
| bytes, http_status | integer, smallint NOT NULL | |
| fetched_at | timestamptz NOT NULL | index `(source, endpoint, fetched_at DESC)` |
| job_id | bigint NULL | the ledger row of the fetch |

A body whose sha256 already has a row today is not uploaded again; the new row points at the existing key. Retention of `raw/` is S5's job (30 days). DECISION: `raw_payload` lands in S1; S0's only upstream call keeps no body.

#### 4.9 `user_squad`

Why: one row per user per gameweek holding the fifteen as selected, the finances and the free-transfer ledger, from the public picks endpoint after the autosub reversal.

| Column | Type | Note |
|---|---|---|
| user_id, season, gw | bigint, text, smallint NOT NULL | foreign key to `app_user`; `gw` is the gameweek the picks were for |
| entry_id, entry_name | integer, text NOT NULL | copied at fetch time |
| picks | jsonb NOT NULL | 15 objects, shape below |
| active_chip | text NULL | one of `wildcard freehit bboost 3xc` |
| chips_played | jsonb NOT NULL | `[{"name": "bboost", "gw": 1}, {"name": "3xc", "gw": 5}]` from `history.chips` |
| bank_tenths, value_tenths, event_transfers, event_transfers_cost, points | smallint NOT NULL | from `entry_history` |
| total_points; overall_rank | integer NOT NULL; integer NULL | rank NULL before GW1 is scored |
| free_transfers | smallint NOT NULL | entering `gw + 1` (7.9) |
| ft_source | text NOT NULL | check in `('accrual', 'accrual_mismatch')` |
| ft_checks | jsonb NOT NULL | one object per played gameweek, shape below |
| picks_as_of | timestamptz NOT NULL | equals `event.deadline_at` for `gw` (R1 gotcha 15) |
| fetched_at, updated_at | timestamptz NOT NULL | |

Primary key `(user_id, season, gw)`; index `(user_id, season, gw DESC)`.

Sample `picks` element and `ft_checks` element:

```
{"slot": 1, "element_id": 411, "code": 223094, "multiplier": 2, "is_captain": true,
 "is_vice": false, "purchase_price_tenths": 140, "purchase_source": "transfer_in",
 "autosub_reversed": false}
{"gw": 4, "entering": 2, "transfers": 3, "predicted_hit": 4, "observed_hit": 4,
 "ok": true, "chip": null}
```

`slot` is the selected order 1 to 15 after reversal; `multiplier` is FPL's; `purchase_source` is `transfer_in` or `start_price`; `autosub_reversed` is true on both players of a reversed pair.

DECISION: with no private `my-team` read (D10), transfers made after the last deadline are invisible until the next deadline passes. `user_squad.gw` is the newest gameweek whose deadline has passed and `free_transfers` is the count entering the next one. S4's solver records this row as `squad_before`.

DECISION: `creator_pick` and `creator_gw` are not created here. ARCH lists them under `squad_refresh.writes`, and `test_jobs_walk` requires a reader panel per written table; none exists before S9. S6 creates both, appends them to `writes`, and extends the entry list to `app_user` rows plus `CREATORS` people.

#### 4.10 Sources and cadence

Sources: `bootstrap-static/` and `fixtures/` feed the first five tables and `fixture` at 06:00, 12:00, 18:00 UTC plus T-30h, T-4h and T-30m; `event/{gw}/live/` with `fixtures/?event={gw}` and `event-status/` feed `player_fixture` daily at 10:30 UTC behind the gate; the four `entry/{id}/...` endpoints feed `user_squad` at T+90m, every 12 h and on demand; the bucket seed feeds the six FPL tables once. Every fetch writes a `raw_payload` row. Section 5 holds the job rows.

#### 4.11 Point-in-time rules

1. `player_state` and `projection` are append-only with `as_of` in the key; a write equal to the latest row for the key is dropped and counted as `rows_unchanged`.
2. `desk/db/pit.py` holds `latest_at(table, keys: dict, t: datetime)`, one `DISTINCT ON ... WHERE as_of <= t` statement; a naive `t` raises. `test_pit` greps `desk/` for `FROM player_state` and `FROM projection` outside `pit.py`.
3. `player_fixture` is a current row with `settled_at`; the trigger refuses a row stamped before kickoff.
4. `event`, `team`, `player`, `fixture`, `user_squad` are current rows overwritten in place; an unchanged overwrite is skipped and counted.
5. `backfill` refuses a `player_state` parquet with a NULL `status` (R6 rule 3).

### 5. Jobs

Four registry rows, each a `Job` with `main` in `desk/jobs/tasks/<name>.py`. Every `main` answers `--help` with output. `trigger` has no default. The helper counts `rows_written` and `rows_unchanged` over `writes`; the task adds per-table counts to `note`; `nothing_to_do` without a note raises in the helper.

`DeadlineRelative(offset).next_due()` runs `SELECT deadline_at FROM event WHERE season = current AND is_next` and returns `deadline_at + offset`, or `None` with no row (the tick skips the slot). The tick inserts a queued row when `now >= due_at` and none exists for `(task, due_at)`. Stale for a deadline-relative job is measured against `next_due` (R6 rule 20).

#### 5.1 `fpl_poll`

| Field | Value |
|---|---|
| lane; triggers | pipeline; schedule, admin, catchup, cli |
| due | `Calendar("06:00,12:00,18:00")`, `DeadlineRelative(-30h)`, `DeadlineRelative(-4h)`, `DeadlineRelative(-30m)` |
| stale_window; budget_s | 14 h; 120 |
| inputs | `bootstrap-static/` (1.8 MB), `fixtures/` (220 KB); 2 requests |
| writes | event, team, player, player_state, fixture, projection, raw_payload |
| read_by | deadline, me_squad; later fixtures_board, projections_table |
| meters, cap | none |
| note | `event 0, team 0, player 2, player_state 611, fixture 3, projection 640; dropped element_type_5 0; temporary_codes 1` |

Behaviour: fetch both bodies, upload and index each; parse (7.1, 7.2); upsert `team`, `player`, `event`, `fixture` with change skipping; append `player_state` and `projection` with write-on-change (7.3, 7.11); derive `event.finished_provisional` from the fixture rows. 0 rows written lands `nothing_to_do`, note `unchanged: N rows compared`.

Failure: transport errors and 5xx retry 4 times with backoff 1, 2, 4, 8 s, then `error`. A 403 or 429 lands `error`, note `fpl_refused: <status>`. A parse failure lands `error` naming the element or fixture id; the run is one transaction and rolls back.

If this silently did nothing: `/api/health` lists `fpl_poll` under `stale` after 14 h; the feature check's `fpl_sync` line fails when the newest `player_state.as_of` is over 14 h old; the Admin board's last-run age passes 14 h.

#### 5.2 `fpl_settle`

| Field | Value |
|---|---|
| lane; triggers | pipeline; schedule, admin, catchup, cli |
| due | `Calendar("10:30")` |
| stale_window; budget_s | 8 days from the last kickoff of the newest unsettled gameweek; 180 |
| inputs | per event with `deadline_at < now` and `settled_at IS NULL`, oldest first: `fixtures/?event={gw}`, `event-status/`, then `event/{gw}/live/` only when the gate passes; at most 3 requests per gameweek |
| writes | player_fixture, event |
| read_by | deadline; later provider_accuracy, creators_board |
| meters, cap | none |
| note | `GW6: rows 612, dgw_elements 0, unmapped 0, settled_at 2026-09-29T10:30:14Z`; or `gate: GW6 not final: provisionally finished 8/10, finished 8/10, bonus_added false, final_at 2026-09-29T08:00Z`; or `no unsettled gameweek` |

Behaviour: the gate (7.5) runs per gameweek. A refusal lands `nothing_to_do` with the gate note. A pass builds rows (7.6) and writes them with `event.settled_at = now` and `event.finished = true` in one transaction.

Failure: a raise rolls back the gameweek and lands `error`; the next run retries it. `unmapped > 0` (a live element with no `player` row) stays on the note with status `ok`, and the feature check's `settle` line fails on it.

If this silently did nothing: `event.settled_at` stays NULL for a finished gameweek, so `deadline` prints `current.state: settling` for days and the feature check's `settle` line fails 36 h after the last kickoff; the Admin board shows 0 `rows_written` for the gameweek.

#### 5.3 `squad_refresh`

| Field | Value |
|---|---|
| lane; triggers | pipeline; schedule, user, admin, catchup |
| due | `DeadlineRelative(+90m)`, `Interval(12h)`, `OnDemand` |
| stale_window; budget_s | 13 h; 60 plus 6 per user, capped at 300 |
| inputs | `params.user_id` (optional; absent means every user with an `entry_id`); per user, in order: `entry/{id}/`, `entry/{id}/history/`, `entry/{id}/transfers/`, `entry/{id}/event/{gw}/picks/`; 4 requests per user, 1.1 s minimum gap (the old `MIN_INTERVAL_S`) |
| writes | user_squad |
| read_by | me_squad; later dashboard, solver_plan |
| meters, cap | none |
| note | `users 3, written 2, unchanged 1, skipped {"picks_404": 0, "entry_404": 0, "no_gameweek": 0, "error": 0}` |

Behaviour per user: `entry` gives `name` and `summary_overall_rank`; `history.current[-1].event` is the newest played gameweek `g` (an empty `current` lands under `items_skipped.no_gameweek`); the `picks` body for `g` is parsed, `automatic_subs` reversed (7.8), purchase prices reconstructed (7.10), the ledger derived (7.9); the row for `(user_id, season, g)` is upserted with change skipping. A 404 on `picks` (deadline not passed, R1 gotcha 10) is data and lands under `picks_404`; a 404 on `entry` lands under `entry_404`.

On demand: `POST /api/me/squad/refresh` inserts `{task: squad_refresh, trigger: user, user_id, params: {user_id}}` unless a row for that user finished under 1 h ago; S0's team-id save inserts the same row.

Failure: one user's raise is caught, counted under `items_skipped.error` with the class name in the log tail, and the run continues. `error` only when every user failed; `ok` when one row or more was written; `nothing_to_do` when every row was unchanged, note `all N squads unchanged`.

If this silently did nothing: the check user's `me_squad.picks_as_of` stays older than the current `deadline_at`, failing the feature check's `me_squad` line after T+2h; the Admin board shows a last run older than 13 h.

#### 5.4 `backfill`

| Field | Value |
|---|---|
| lane; triggers | pipeline; cli, admin |
| due | `OnDemand` (`stale_window = None`, which `test_jobs_walk` accepts for this due kind only) |
| budget_s | 900 |
| inputs | `params.seed` (bucket prefix), six parquet files under it |
| writes | event, team, player, player_state, fixture, player_fixture |
| read_by | deadline, me_squad; every later panel |
| meters, cap | none |
| note | `event 190, team 100, player 3,930, player_state 135,391, fixture 1,900, player_fixture 115,809; dropped element_type_5 20; fdr_defaulted 1,520` first run; `seed already applied: 0 of 275,398 rows differ` second run |

DECISION: the parquet comes from `scripts/export_old_repo.py`, run once on the Mac against a read-only copy of the old DuckDB file with the eleven `COPY ... TO parquet` statements of ARCH section 12; `scripts/backfill.py --upload /tmp/export` uploads every file to `seed/2026-09-21/`. S1 imports the six FPL datasets; S3, S6 and S9 add theirs to the same task under `params.datasets`.

Mapping from the old DDL (`fpl_edge/store/schema.sql` lines 22 to 136):

| Old table | Rule |
|---|---|
| `dim_event` | newest `as_of` per `(season, gw)`; `finished = data_checked = finished_provisional = is_finished`; `is_current`, `is_next` false (the next `fpl_poll` sets them); `settled_at = as_of` when `fact_player_fixture` has rows for the gw, else NULL; `updated_at = as_of` |
| `dim_team`, `dim_player` | newest per key; position 1 GKP, 2 DEF, 3 MID, 4 FWD; `has_temporary_code = false`; `photo = code || '.jpg'`; NULL names become empty strings |
| `fact_player_state` | every row kept; `chance_next = chance_of_playing_next_round`; `ep_next = NULL`; a NULL `status` refuses the file |
| `fact_fixture` | newest per key; `kickoff_at = kickoff_utc`; `started = finished_provisional = finished`; difficulties 3 before 2026-27, counted as `fdr_defaulted`; `pulse_id = 0` |
| `fact_player_fixture` | newest per key; `cbi = clearances_blocks_interceptions`; `xg, xa, xgc = expected_*`; `settled_at = as_of`; `was_home` from `player.team_code` |

Slash-form seasons are converted at parse; `element_type` 5 rows are dropped and counted. Every insert is `ON CONFLICT DO UPDATE WHERE row IS DISTINCT FROM excluded`, so a second run writes 0 rows. The trigger applies here too; an archive row stamped before its kickoff refuses the file with the row named.

If this silently did nothing: `player_fixture` counts under 3,000 rows against 115,809 expected; test 8 skips where it should check 113,260 rows; the Admin board has no `backfill` row.

### 6. API and panels

Two panels and one route. Both panels are session tier and become chat tools in S7 as `panel_deadline` and `panel_me_squad`. Every response is the ARCH section 6 envelope; both budgets sit inside the 10 s rule.

#### 6.1 `GET /api/panels/deadline`

Tier session. Params: none (an empty model with `extra="forbid"`). Budget 500 ms. Result:

```
{ "season": "2026-27", "gw": 6, "deadline_at": "2026-09-26T10:00:00Z",
  "seconds_left": 341880, "state": "open",
  "current": { "gw": 5, "state": "settled", "settled_at": "2026-09-22T10:30:14Z",
               "fixtures_total": 10, "fixtures_provisional": 10, "fixtures_finished": 10 } }
```

`state` is `open` when `now < deadline_at`, else `locked` (the window before FPL flips `is_next`), with `seconds_left` 0. `current.state` is `live` (a fixture not provisionally finished), `settling` (all provisional, `settled_at` NULL) or `settled`. Provenance inputs: both `event` rows and the current gameweek's `fixture` rows (`as_of: max updated_at`). Empty: `{reason: "No gameweek loaded", fix: "Run fpl_poll from Admin"}` when `event` has no row for the season.

#### 6.2 `GET /api/panels/me_squad`

Tier session. Params: none. `Ctx.user_id` selects the row; the user id is not a param. Budget 800 ms. Result:

```
{ "season": "2026-27", "gw": 5, "entry_id": 4490171, "entry_name": "...",
  "picks_as_of": "2026-09-20T10:00:00Z", "fetched_at": "2026-09-21T11:30:02Z",
  "picks": [ { "slot": 1, "code": 223094, "web_name": "Haaland", "position": "FWD",
               "team_code": 43, "short_name": "MCI", "price_tenths": 141,
               "purchase_price_tenths": 140, "sell_price_tenths": 140,
               "purchase_source": "transfer_in", "multiplier": 2, "is_captain": true,
               "is_vice": false, "autosub_reversed": false, "status": "a",
               "chance_next": null, "news": "" } ],
  "bank_tenths": 3, "value_tenths": 1003, "squad_sell_value_tenths": 1000,
  "free_transfers": 2, "ft_source": "accrual", "ft_checks_failed": 0,
  "active_chip": null, "chips_played": [ { "name": "bboost", "gw": 1 } ],
  "overall_rank": 412233, "total_points": 312, "autosubs_reversed": 1 }
```

`price_tenths`, `status`, `chance_next` and `news` come from one `latest_at("player_state", {season, codes}, now)` call; `sell_price_tenths` from 7.10; sorted by `slot`. Provenance inputs: the `user_squad` row (`as_of: fetched_at`) and `player_state` (`as_of`: the newest read).

Empty, in precedence order: `{reason: "No team id saved", fix: "Save your FPL team id on Account"}` when `app_user.entry_id` is NULL; `{reason: "Squad not fetched yet", fix: "A refresh is queued; it lands within 2 minutes of saving a team id"}` when no row exists; `{reason: "Season not started", fix: "Picks appear after the GW1 deadline"}` when the newest `squad_refresh` row for the user skipped with `picks_404`.

#### 6.3 `POST /api/me/squad/refresh`

Tier session, CSRF double submit, no body. 202 `{"queued": true, "job_id": 1234}`; 200 `{"queued": false, "reason": "refreshed 23 minutes ago", "next_allowed_at": "2026-09-21T12:30:02Z"}`; 409 `{"error": "no_entry_id", "detail": "Save a team id first", "remediation_url": "/account"}`. The route inserts the job row through S0's enqueue helper; web runs no task.

#### 6.4 Errors, shared

401 `{"error": "unauthenticated"}`; 403 `{"error": "forbidden"}`; 422 the pydantic body; 500 `{"error": "panel_failed", "panel": "me_squad"}` with no traceback. `test_routes_matrix` gains the new route: anonymous 401, session 202 or 200, admin as session.

### 7. Algorithms

#### 7.1 Season label

Take the event with the smallest `deadline_time`, read its year `Y`, return `f"{Y}-{str(Y+1)[-2:]}"`; the API states no season (R1 gotcha 17). Lifted from `fpl_edge/ingest/fpl_api.py` lines 25 to 34. Validation: the archived 2026-27 bootstrap yields `2026-27`.

#### 7.2 Timestamps

Every FPL timestamp passes through `core.time.parse_utc`: replace a trailing `Z` with `+00:00`, `fromisoformat`, attach UTC when tzinfo is absent (R1 gotcha 2), convert any other offset to UTC. A naive datetime reaching a write raises. Validation: one case per Z-less field (`news_added`, `kickoff_time`).

#### 7.3 Write-on-change for series tables

```
WITH latest AS (
  SELECT DISTINCT ON (season, code) * FROM player_state
  WHERE season = :season ORDER BY season, code, as_of DESC)
INSERT INTO player_state (...)
SELECT n.* FROM new_rows n LEFT JOIN latest l USING (season, code)
WHERE l.code IS NULL
   OR (n.price_tenths, n.selected_by_pct, ..., n.cost_change_start)
      IS DISTINCT FROM (l.price_tenths, l.selected_by_pct, ..., l.cost_change_start)
```

`new_rows` is a temp table; the insert's row count is `rows_written`, the rest `rows_unchanged`. The same shape serves `projection` keyed on `(provider, season, gw, code)`. Validation: insert 3 rows twice, assert 3 then 0; change one price, assert 1.

#### 7.4 `latest_at`

```
SELECT DISTINCT ON (season, code) * FROM player_state
WHERE season = :season AND code = ANY(:codes) AND as_of <= :t
ORDER BY season, code, as_of DESC
```

`keys` may omit `codes` to read every player. Validation: rows at `as_of` 1, 2, 3; `t = 2` returns row 2, `t = 1.5` row 1; a naive `t` raises `TypeError`.

#### 7.5 The settlement gate

Lifted from `fpl_edge/ingest/results.py` lines 78 to 118. For gameweek `gw`, its fixtures `F`, the `event-status` body `S` and the instant `now` (a required argument, no default):

1. `not_played = [f for f in F if not (f.finished or f.finished_provisional)]`. Non-empty refuses: `gate: GW{gw} not final: provisionally finished {n}/{len(F)}`. No time override exists for this clause; an abandoned match blocks settlement.
2. `final_at` = 09:00 Europe/London on the day after `max(kickoff_time over F)`, in UTC (08:00Z in summer, 09:00Z in winter). `POINTS_FINAL_HOUR = 9` from `registry.yaml deadlines.points_final_at`.
3. `bonus_done` = `S.status` has at least one day with `event == gw` and every such day has `bonus_added` true.
4. `fully_finished = all(f.finished for f in F)`.
5. Pass when `(bonus_done and fully_finished) or now >= final_at`; else refuse with `finished {k}/{len(F)}, bonus_added {bonus_done}, final_at {final_at}`.

Validation, one test per clause: a fixture not provisionally finished refuses even after `final_at`; all provisional, none finished, before `final_at` refuses; all finished with bonus added passes before `final_at`; all provisional past `final_at` passes; BST and GMT give 08:00Z and 09:00Z. `now` is the body's last kickoff plus an offset.

#### 7.6 Rows from the live body

Lifted from `results.py` `build_rows`. For each element with `explain` blocks carrying `stats`: `code = player.code` for `(season, element_id)`, an unmapped element counted and skipped; one row per block. With one block every column comes from `stats` (`xg, xa, xgc` from `expected_*`). With two or more blocks (a double gameweek) the raw stats come from the block's `stats` list (an absent identifier is 0), `total_points` is the block's summed `points`, and `bps, xg, xa, xgc` are NULL because FPL publishes them as gameweek totals only (R1 gotcha 16). Validation: the archived live body yields 610 rows with 0 NULL `xg`; a synthetic two-block element yields two rows with NULL `xg` whose points sum to the total.

#### 7.7 The scoring map

`desk/rules/scoring_map.py` is the old `models/points/scoring_map.py` verbatim, reading `registry.yaml` through the copied `loader.py`; the registry loses only `misc.total_players_at_fetch`. The map: 2 for 60 or more minutes, 1 for 1 to 59; goals 10, 6, 5, 4 by position; assist 3; clean sheet 4, 4, 1, 0; minus 1 per 2 conceded for GKP and DEF; 1 per 3 saves; penalty save 5; penalty miss minus 2; yellow minus 1; red minus 3; own goal minus 2; bonus as given; defensive contribution 2 at 10 or more for DEF, 12 or more for MID and FWD, 0 for GKP, no stacking. An unverified rule read raises `UnverifiedRuleError`; the one unverified rule (`prices.in_season_change_time_utc`) has no reader.

Validation: `tests/integration/test_scoring_map_vs_history.py` joins `player_fixture` to `player.position` for every row with `minutes` set and asserts 0 mismatches over 113,260 rows or more, printing the first 15 mismatches. In CI the rows come from the seed parquet loaded by `backfill` into the service container, about 6 s.

#### 7.8 Autosub reversal

Lifted from `fpl_edge/myteam/state.py` lines 517 to 527. `slot = {pick.element: pick.position}`; for each `(element_out, element_in)` in `automatic_subs` with both present, swap their slots; sort by slot; mark both `autosub_reversed`. The multiplier does not decide the XI, since under Bench Boost every multiplier is 1. Validation: the archived picks body with two subs places captain and vice in slots 1 to 11 and marks four picks; a body with none keeps the payload order.

#### 7.9 Free-transfer accrual

Lifted from `state.py` lines 425 to 475. Constants from the registry: `free_per_gw 1`, `max_banked 5`, `hit_cost 4`. Walk `history.current` in order with `free = 0` entering GW1 (pre-deadline transfers are unlimited):

```
for row in current:
    entering[gw] = free; n = row.event_transfers
    if gw == 1:                        predicted = 0; free = 1
    elif chip in (wildcard, freehit):  predicted = 0            # banked FTs retained
    else:
        predicted = 4 * max(0, n - free)
        free = min(5, max(0, free - min(n, free)) + 1)
    check(gw, entering[gw], n, predicted, row.event_transfers_cost)
entering[last + 1] = free
```

`free_transfers = entering[last + 1]`; `ft_source = accrual` when every check has `predicted == observed`, else `accrual_mismatch`. A chip gameweek is one whose `gw` appears in `history.chips`. Validation: the entry 4490171 history body reproduces every `event_transfers_cost`; 3 transfers against 1 FT predicts 8; five quiet weeks cap at 5; a wildcard week retains the bank.

#### 7.10 Purchase price and sell price

Purchase price: walk `entry/{id}/transfers/` in `time` order up to the picks gameweek; `paid[code_in] = element_in_cost`; `paid.pop(code_out)`. A held player absent from `paid` was in the initial squad: his price is `latest_at("player_state", code, deadline_at of the user's first played gameweek).price_tenths`, `purchase_source = start_price`. A missing price lands the user under `items_skipped.error`.

Sell price in integer tenths (`registry.yaml prices.sell_on_fee_fraction 0.5`, `sell_rounding floor_to_0.1m`): `sell = now if now <= purchase else purchase + (now - purchase) // 2`; `squad_sell_value_tenths = sum(sell)`. Validation: `(75, 78) -> 76`, `(75, 79) -> 77`, `(75, 74) -> 74`, `(75, 75) -> 75`; a transfer, undo and redo of one pair in one gameweek (R1 gotcha 14) yields the last `element_in_cost`.

#### 7.11 `fpl_ep` rows

For each element with a non-empty `ep_next`: `xp = float(ep_next)` for the `is_next` gameweek; `xmins = NULL`; `p_appear = chance_next / 100`, NULL when `chance_next` is NULL (R2 measured the old `null -> 1.0` rule at twice the baseline Brier). An `xp` outside 0 to 25 refuses the frame with the element named. Validation: the archived bootstrap yields 640 or more rows with `p_appear` NULL for every unflagged player.

### 8. UI

S1 ships no screen: S8 builds the kit before any feature screen (ARCH section 11), and S0's Admin page renders this step's jobs through the registry with no change. During S1 the owner reads the two panels as JSON at `/api/panels/deadline` and `/api/panels/me_squad`, signed in. What follows fixes the copy and states S8 must honour.

#### 8.1 Dashboard v0, route `/` (built in S8, fed by S1)

Primary visual: `Pitch`, 15 cards in formation from `me_squad.picks`; each card a `PlayerCell` with `web_name`, `short_name` and the price as `14.1`; `C` on the captain, `V` on the vice; a `sub` chip on an `autosub_reversed` card, tooltip "FPL substituted this player during the gameweek. Shown as you selected him." Above the pitch: "GW6 deadline in 3 days 22 h" from `deadline`, an `Age` on `picks_as_of`, and one finances line "Bank 0.3 · Sell value 100.0 · 2 FT (accrual)". Secondary: one `Fold`, summary "Chips played: 2". v0 has 0 filters, 0 sorts and 0 drill-downs; a card click opens nothing until S3's `player_detail` exists.

Empty: `StateBox` kind empty with the panel's `reason` and `fix` verbatim (6.2). Loading: a skeleton pitch of 15 grey cards. Error: `StateBox` kind error, "Squad could not be loaded." with a "Retry" button. Stale: when `picks_as_of` is older than the current `event.deadline_at`, `StateBox` kind stale, "Picks are from GW4; GW5 picks land within 90 minutes of the deadline", with a "Refresh now" button calling `POST /api/me/squad/refresh`; a 200 refusal prints inline as "Refreshed 23 minutes ago. Next refresh at 12:30."

Mobile at 390 px: the pitch scales to width in four rows; the finances line wraps; no horizontal scroll.

#### 8.2 Admin jobs, route `/admin/jobs` (S0's page, no change)

The four tasks appear as rows because the board reads `JOBS`: last status, `rows_written`, `rows_unchanged`, `items_skipped`, `note` verbatim, age in days, `next_due`, Run-now. None is metered, so Run-now has no confirm dialog.

Shared components used by 8.1: `Pitch`, `PlayerCell`, `Age`, `Number`, `Fold`, `StateBox`, `CiteChip`. New components from this step: none.

### 9. Admin view

On the S0 board the admin sees four task rows with health state (`ok`, `stale`, `failing`, `never_ran`), last run status and instant, `rows_written`, `rows_unchanged`, `items_seen`, `items_done`, `items_skipped` by reason, `note`, `next_due` and Run-now; the log tail of any run through `admin_job_log`; and the three scheduled tasks on `/api/health` when failing, stale or never run.

A user must not see: any job row or log tail, another user's `user_squad` row or entry id, the raw payload index, any bucket key. `me_squad` is scoped by `Ctx.user_id`; test 17 proves it.

### 10. Acceptance tests

In writing order. Upstream bodies live under `tests/fixtures/upstream/fpl/` (section 13).

1. `test_fplapi_parsers_bootstrap`: the archived 2026-27 bootstrap parses to 38 events, 20 teams, 662 or more players, every `deadline_at` tz-aware, `avg_entry_score` NULL for every unfinished event, `selected_by_pct` NULL for an empty string.
2. `test_fplapi_parsers_fixtures`: 380 rows, every team code resolves, `kickoff_at` and `gw` NULL for an unscheduled fixture.
3. `test_fpl_poll_writes_then_writes_nothing`: a stub client returns the archived bodies on an empty database; `rows_written > 600`, status `ok`; the same bodies again; `nothing_to_do`, `rows_written == 0`, `rows_unchanged > 600`, note begins `unchanged:`.
4. `test_fpl_poll_lying_task_lands_error`: a monkeypatched task reports success while writing nothing on non-empty input; the helper lands `error`, note `wrote_nothing_unexplained`.
5. `test_settle_gate_each_clause`: the five cases of 7.5, `now` derived from the body.
6. `test_settle_rows_single_and_double`: 610 rows from the archived live body; two rows with NULL `xg` and summed points from a synthetic two-block element.
7. `test_player_fixture_trigger_refuses_pre_kickoff`: `settled_at` one hour before `kickoff_at` raises `settled_before_kickoff`.
8. `test_scoring_map_reproduces_historical_total_points`: 0 mismatches over every backfilled row with `minutes` set.
9. `test_backfill_twice_writes_zero`: the 200-row-per-table sample under `tests/fixtures/seed/`; first run writes the sample size, second run 0 with note `seed already applied`.
10. `test_pit_no_raw_select`: zero hits for `FROM player_state` and `FROM projection` under `desk/` outside `pit.py`.
11. `test_element_id_columns`: `information_schema.columns` shows `element_id` on `player` only.
12. `test_autosub_reversal`: the archived picks body with two subs places captain and vice in slots 1 to 11 and marks four picks.
13. `test_free_transfer_ledger`: the four cases of 7.9; `accrual_mismatch` when one check fails.
14. `test_sell_price`: the four pairs of 7.10 and the do-undo-redo transfers body.
15. `test_panel_deadline_contract`: on the seeded database the result validates with `extra="forbid"`, `provenance.inputs` names `event` and `fixture`, the snapshot matches; on an empty database the envelope carries "No gameweek loaded".
16. `test_panel_me_squad_contract_and_empty`: 15 picks sorted by slot with `sell_price_tenths`; the three empty reasons of 6.2 in precedence order; the snapshot matches.
17. `test_me_squad_isolation`: two users with rows; each call returns its own `entry_id` alone.
18. `test_squad_refresh_ttl`: 202 then 200 with `queued: false` inside an hour; 409 `no_entry_id` without a team id.
19. `test_deadline_relative_due`: an `is_next` event at `T` gives `next_due() == T - 4h` for `DeadlineRelative(-4h)`; no event gives `None`; two ticks at one instant insert one `job` row.
20. `feature_check.py` rows against `PUBLIC_URL`: `fpl_sync` (38 event rows; newest `player_state.as_of` under 14 h); `settle` (every event whose last kickoff is over 36 h old has `settled_at`; the newest run has `unmapped 0`); `me_squad` (the check user has 15 picks, and `picks_as_of` equals the current event's `deadline_at` once T+2h has passed).

### 11. Definition of done

- [ ] Tests 1 to 19 green locally on testcontainers Postgres and in CI on the service container; test 8 checks 113,260 or more rows in CI.
- [ ] Every S0 test (the four registry walks, the routes matrix, `test_ledger`, `test_time`) green with the new rows.
- [ ] CI green on `main`: ruff, `mypy --strict desk`, pytest, prose check, `tsc --noEmit`, contract drift on the two new snapshots and `contracts/openapi.json`.
- [ ] Deployed to Railway; `/api/health` shows the deploy's `git_sha`, `alembic_head` 0002, and the three scheduled tasks absent from `never_ran` after their first runs.
- [ ] `feature_check.py` green for S0's lines (health, me, admin) and S1's three lines.
- [ ] `backfill` run once from the worker shell with status `ok` and six counts; a second run landed `nothing_to_do`.
- [ ] The T-4h `fpl_poll` row for GW7 exists with `trigger = schedule` before the GW7 deadline.
- [ ] Owner's demo: sign in; Admin, Run-now `fpl_poll`, read `rows_written` on the `ok` row; press again, read `nothing_to_do`; open `/api/panels/me_squad`, count 15 picks, read `ft_source` and `free_transfers`; open `/api/panels/deadline`, read `seconds_left`; on the Sunday of GW6 Run-now `fpl_settle` and read the gate note; on Monday after 10:30 UTC read the `ok` row and `current.state: settled`.
- [ ] Docs: `docs/runbook.md` regenerated from `JOBS`; `docs/SPEC.md` marks this block done; `docs/adr/0002-projection-table-in-s1.md` records the DECISION in 4.7; `README.md` gains `desk run-task fpl_poll`, `desk run-task backfill --seed <prefix>` and `python scripts/export_old_repo.py --db <copy> --out <dir>`.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk/ web/src scripts/` returns nothing.

### 12. Out of scope

| Not done here | Done in |
|---|---|
| the `PROVIDERS` registry, fplform, airsenal, Solio, the FPLReview upload, consensus, `provider_score`, `upload` | S3 |
| the Dixon-Coles fit, `fixture_ratings`, the ticker | S2 |
| `Pitch`, `PlayerCell`, `Age`, `StateBox`, the Dashboard v0 screen, the asset proxy | S8 |
| `creator_pick`, `creator_gw`, creator entry ids in `squad_refresh` | S6 (tables, writes), S9 (readers) |
| `retention` of `raw/`, `pg_dump_to_bucket`, `db_restore_check` | S5 |
| `price_change_*`, `scout_risks`, `opta_code`; private `my-team` reads, FPL login, transfers since the last deadline (D10); `element-summary`, `leagues-classic`, crawls | none in v1 |

### 13. Size and session plan

About 18 source files and 2,400 lines, plus 14 test files and 1,300 lines. Write in this order; each group's tests pass before the next starts.

| Order | Files | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0002_s1_fpl_tables.py` | 260 |
| 2 | `desk/rules/registry.yaml`, `loader.py`, `scoring_map.py` (copied) | 207, 118, 120 |
| 3 | `desk/db/pit.py` | 60 |
| 4 | `desk/fplapi/client.py` (eight endpoint methods, the 1.1 s gap, body upload and `raw_payload` insert), `desk/fplapi/parsers.py` | 180, 260 |
| 5 | `desk/fplapi/ingest.py` (upserts, write-on-change), `desk/jobs/tasks/fpl_poll.py`, `DeadlineRelative.next_due` in `registry.py` | 220, 90, 30 |
| 6 | `desk/fplapi/settle.py`, `desk/jobs/tasks/fpl_settle.py` | 200, 80 |
| 7 | `desk/fplapi/squads.py`, `desk/jobs/tasks/squad_refresh.py`, `desk/web/routers/me.py` | 240, 120, 50 |
| 8 | `desk/panels/deadline.py`, `desk/panels/me_squad.py`, `contracts/panels/{deadline,me_squad}.json` | 90, 140 |
| 9 | `scripts/export_old_repo.py` (Mac only), `scripts/backfill.py` (uploader), `desk/jobs/tasks/backfill.py` | 120, 60, 260 |
| 10 | `scripts/feature_check.py` (three rows), `docs/runbook.md`, `docs/adr/0002-*.md`, `README.md` | 60, generated, 30, 10 |
| tests | 10 files under `tests/unit/` (parsers, write-on-change, pit, settle gate, settle rows, trigger, squads, deadline-relative, poll task, backfill); `tests/contract/test_panel_{deadline,me_squad}.py`; `tests/integration/test_scoring_map_vs_history.py`; `tests/fixtures/upstream/fpl/` (bootstrap at GW5, `fixtures`, `event/4/live`, `fixtures?event=4`, `event-status`, `entry/4490171` with `history`, `transfers`, `event/5/picks`, copied from the old `data/raw/fpl_api/`); `tests/fixtures/seed/` (200 rows per table cut from the parquet) | 1,300 |

DECISION: S0's grep test that refuses ISO dates under `tests/fixtures/` exempts `tests/fixtures/upstream/`, because archived bodies carry the dates the parsers are tested against; every test that compares an instant to `now` passes an explicit `now` derived from the body.

The three places a session is most likely to go wrong, with the guard:

1. A task reports counts itself and the helper's count disagrees, or returns early with no note. Guard: test 4 runs before any real task; the session reads `desk/jobs/runner.py` before writing `fpl_poll.py`.
2. A plain `SELECT` on `player_state` inside `me_squad` or `squads.py`, caught late by the grep. Guard: `pit.py` and test 10 land in group 3 before any reader exists; `latest_at` takes a `codes` list so the squad read is one call.
3. Testing the gate against wall-clock `now` with week-old bodies, or a literal date in a test that goes red at that instant (R6 rule 33). Guard: `assert_final(now=...)` has no default; parser tests compute `now` from the body.

A fourth trap: the FPL API refusing Railway's egress. If `docs/adr/0001-fpl-egress.md` says refused, the client reads `FPL_PROXY_URL` and `fpl_poll` gains `meters = (proxy_mb,)` at 2 MB per run.

Risks against the decisions: no fact read for this step contradicts D1 to D10. Two ordering tensions inside ARCH are resolved by the DECISIONs in 4.7 (`projection (fpl_ep)` under `fpl_poll.writes` against projection work in S3) and 4.9 (`creator_pick, creator_gw` under `squad_refresh.writes` against the reader-per-table walk).


# Part 7. Step S8: UI shell, design tokens, component kit, Account, Admin, Dashboard v0

Step 3 of 10 in the build order of ARCH.md section 11. Depends on S0 and S1. Target: deployed and green before the GW8 deadline. Spelling is British. Paths are relative to the repo root `fpl-desk/`; the Python package is `desk`, the frontend is `web/`. ARCH sections 6, 9 and 10 fix the surface; where ARCH is silent the choice is marked `DECISION:` so the assembler can lift it.

### 1. Goal

When this step is done the owner opens `/` and sees his fifteen on a pitch with prices, captain and vice marks, the GW8 countdown and his bank, switches to the light theme from the header menu, and uses a bottom tab bar on his phone, where before he read `me_squad` as JSON. Every later screen is built from the kit this step freezes: one table, one drawer, one age, one empty state, one token file, and the compiler refuses a screen that reads a key a panel does not serve.

### 2. Depends on

| Step | What S8 uses |
|---|---|
| S0 | tables `app_user`, `session`, `job`, `spend`, `worker_heartbeat`; panels `me`, `admin_jobs`, `admin_job_log`, `admin_spend`, `admin_users`; routes `GET /api/health`, `GET /api/panels/{name}`, `POST /api/admin/jobs/{task}/run`, `POST /auth/logout`, `POST /api/account/entry/preview`, `PUT /api/account/entry`; `desk/auth/policy.py` (the tier table), `desk/panels/registry.py` (`Panel`, `PANELS`, the envelope), `desk/artefacts/registry.py` (the empty `ARTEFACT_KINDS` and its walk test); the Vite scaffold, `web/src/tokens.css` (partial), `web/src/api/{client,usePanel}.ts`, `web/src/app/{router,Shell}.tsx`, `web/src/components/{StateBox,Age}.tsx`, screens `signin`, `account`, `admin`; `scripts/gen_types.sh`, `scripts/prose_check.py`, `scripts/feature_check.py`, `.github/workflows/{ci,deploy-check}.yml`; the check user and `POST /auth/check` |
| S1 | tables `event`, `team`, `player` (column `photo`), `player_state`, `user_squad`; panels `deadline` and `me_squad` with the schemas in SPEC S1 sections 6.1 and 6.2; `POST /api/me/squad/refresh` with its 202, 200 and 409 shapes; the Dashboard v0 copy in SPEC S1 section 8.1 |

If a named S0 module is absent from the repo as S1 left it, S8 creates it to the signature given here.

### 3. User stories

User role:

1. I sign in and land on `/`. Result: a pitch with 15 cards in formation, each with photo, club mark, name and price as `14.1`, `C` on the captain, `V` on the vice, the bench in slot order under the XI; above it `GW8 deadline in 3 days 22 h`, an age on the picks and one finances line.
2. I open `/` before my squad has been fetched. Result: the empty state with the panel's own reason and fix, no fabricated card.
3. I open `/` after a deadline passed but before `squad_refresh` ran. Result: the stale state naming the gameweek the picks are from, and a `Refresh now` button that queues a refresh or prints when the next one is allowed.
4. I open the header menu and choose `Light`. Result: every screen re-renders on the light tokens at once, the choice survives a reload on this browser, and every muted and faint text still reads at 4.5:1.
5. I open the app on a 390 px phone. Result: a bottom tab bar with six entries, no horizontal scroll on any screen, the pitch in four rows plus a bench row.
6. I open `/fixtures`, `/projections`, `/solver`, `/creators` or `/chat`. Result: one empty state per screen saying the section is not built yet, and no panel call.
7. I open `/account`. Result: my email, my confirmed team line with `Change`, and an `Appearance` row with `Dark`, `Light`, `System`.
8. I hover or focus any headline number. Result: a chip naming the panel, its age and the git sha it was computed from.

Admin role:

9. I open `/admin`. Result: the jobs board as a sortable table, sorted by state severity then task, with a filter row (`State`, `Task`) and `6 of 6` under it; a sort or filter fires no request.
10. I click the `retention` row. Result: the drawer at `/admin/jobs/{job_id}` with a breadcrumb `Admin / retention / run 42`, every ledger column and the log tail; Escape closes it and returns focus to the row; a reload of that URL reopens it.
11. I read a row whose last run landed `error` with note `wrote_nothing_unexplained`. Result: a `failing` badge in the status colour with the word, and the note in the row.
12. I open the `Spend` and `Users` tabs. Result: three meters with a bar and `amount of cap unit`; a users table sortable by any column, `last4` or a dash, never a key.
13. I load `/api/assets/club/1.png` in a new tab. Result: the Manchester United crest, `Cache-Control: public, max-age=604800, immutable`.

### 4. Data

One migration, `desk/migrations/versions/0003_s8_artefact.py`. S8 alters no existing table.

DECISION: the `artefact` table lands in S8. S0 section 12 assigned it to S2; SPEC S2 section 2 lists it under S0 and creates no table; SPEC S4 section 2 lists it under S8. S8 sits between S1 and S2 in the build order and is the first step that can fill the gap without a second migration in S2. S2's planned index is created here, so S2 adds no migration.

#### 4.1 `artefact`

Why: every plan, brief and fixture rating is one row with the inputs it was computed against, so a reader derives `fresh | aging | stale | superseded | missing` from `inputs` alone (ARCH section 3, R6 rule 19). Read by `solver_plan`, `dashboard`, `fixtures_board` and `fixture_detail` in later steps; written by `store.write` only.

| Column | Type | Constraint |
|---|---|---|
| `artefact_id` | bigint | PK, generated always as identity |
| `kind` | text | NOT NULL; a name in `ARTEFACT_KINDS`, checked in `store.write`, never a DB enum |
| `user_id` | uuid | NULL for shared kinds (`fixture_ratings`, `brief`); FK `app_user` ON DELETE CASCADE |
| `job_id` | bigint | NULL; FK `job` ON DELETE SET NULL |
| `created_at` | timestamptz | NOT NULL, default `now()` |
| `inputs` | jsonb | NOT NULL; validated by the kind's `inputs_model` |
| `payload` | jsonb | NOT NULL; validated by the kind's `payload_model` |
| `bytes` | integer | NOT NULL; `length(payload::text)`, `CHECK (bytes <= 2097152)` |
| `git_sha` | text | NOT NULL; the writer's `GIT_SHA` |

Indexes: `artefact_kind_created (kind, created_at DESC)`; `artefact_user_kind_created (user_id, kind, created_at DESC)`. DECISION: `artefact_id` is a bigint identity like `job_id`; SPEC S2's sample rows print a ULID-like string and SPEC S4 prints an integer, and the integer form is kept so `/solver/run/{job_id}` and `/admin/jobs/{job_id}` share one id type.

Sample row, written by a test-only kind `probe` that lives under `tests/`:

```
artefact_id 1  kind probe  user_id NULL  job_id 41  created_at 2026-10-01T11:00:04Z
inputs  {"as_of": "2026-10-01T10:30:00Z", "n": 3}
payload {"values": [1, 2, 3]}
bytes 20  git_sha 3f9c1a2
```

Sources and cadence: S8 writes no artefact outside tests. S2 writes `fixture_ratings` daily, S4 `solver_plan` on demand, S5 `brief` daily. Retention: 30 rows per kind for shared kinds, 30 per user for per-user kinds, deleted by the S5 `retention` job; the `Retention` field on each registry row records it from S8 so the walk test can demand it. Point-in-time: none; an artefact is immutable and the newest row of a kind is a current read, not a `latest_at` read.

#### 4.2 No other table

Theme choice is a per-browser convenience and lives in `localStorage` under `desk.theme` with values `dark` or `light`; absent means `System`. DECISION: no `app_user.theme` column, because the choice belongs to a device (a phone in light, a desk in dark) and nothing on the server reads it. Column and drawer state live in the URL. The asset proxy keeps no table.

### 5. Jobs

S8 registers 0 jobs and alters no `JOBS` row. Web never runs a scheduler (ARCH section 4) and the one new server behaviour, the asset proxy, runs per request. `docs/runbook.md` regenerates with no diff, and `test_runbook_sync` proves it.

Two S8 surfaces answer the silent-nothing question without a job of their own:

| Surface | If it silently did nothing, what is different |
|---|---|
| Asset proxy | every `ClubMark` box flips to its monogram and every `PlayerCell` to its fallback figure; the feature check's `assets` line fails on a non-200 or a missing 7 day cache header |
| Admin board on the kit | a task whose newest row is `error` with note `wrote_nothing_unexplained` is the first row after the default sort with a `failing` badge; `counts.failing` on the panel is over 0 and the header tile prints it |

S4 fills the `solve` lane; S5 extends `retention` to `artefact`. The `ARTEFACT_KINDS` registry row gains the fields S2 and S4 need (section 7.3), and `test_jobs_walk` accepts `artefact:<kind>` in `Job.writes` from this step, counted by the helper as `count(*) WHERE kind = <kind>`.

### 6. API and panels

S8 adds two routes and no panel. Every panel S8 renders exists from S0 or S1 with its schema unchanged; `contracts/panels/*.json` shows no diff in this step.

#### 6.1 `GET /api/assets/player/{code}.png` and `GET /api/assets/club/{code}.png`

Tier session (an `<img>` sends the session cookie; a stranger cannot use the app as a CDN mirror). Params: `code`, an integer 1 to 9,999,999 in the path; anything else is 400 `invalid_params` with `field: code`. Result: the PNG body from `https://resources.premierleague.com/premierleague/photos/players/110x140/p{code}.png` or `.../badges/70/t{code}.png`, `Content-Type: image/png`, `Cache-Control: public, max-age=604800, immutable`, `X-Desk-Source: fpl-cdn`, `X-Desk-Fetched-At: <instant>`. Provenance is those two headers.

| Upstream outcome | Response |
|---|---|
| 200 under 200 KB | 200 with the body and the headers above |
| 404 | 404 `{error: "asset_not_found", detail: "No image for code {code}", remediation_url: null}`, `Cache-Control: public, max-age=86400`; the client flips to its fallback |
| timeout after 3 s, 5xx, or a body over 200 KB | 502 `fpl_unreachable`, `Cache-Control: no-store` |

Budget: 3 s upstream timeout, one attempt, inside the 10 s rule. DECISION: an in-process LRU of 512 bodies (about 4 MB) sits in front of the CDN call so a Projections mount in S3 with 100 rows costs at most 100 CDN calls per web process lifetime, not per user; the browser's 7 day cache does the rest. Web calls upstream for nothing else new (ARCH section 4 names exactly these two and `entry/{id}/`). Chat tool: no.

#### 6.2 Panels consumed, unchanged

| Panel | Screen | Params | Budget |
|---|---|---|---|
| `me` | shell, Account | none | 200 ms |
| `deadline` | shell header, Dashboard | none | 500 ms |
| `me_squad` | Dashboard | none | 800 ms |
| `admin_jobs`, `admin_spend`, `admin_users` | Admin | none | 1,000 ms |
| `admin_job_log` | Admin drawer | `job_id` | 1,000 ms |

Call budget per mount, measured by the `ui_budget` feature-check line: the shell fetches `me` once with a TanStack Query `staleTime` of 300 s and `deadline` once with 60 s; a screen fires at most 3 further panel calls. A cold load of `/` fires 3 calls (`me`, `deadline`, `me_squad`) and the Dashboard screen itself fires 1 more than the shell, so ARCH's "2 calls under 100 KB" is met. Sort, filter, tab and fold changes fire 0 calls; `test_datatable_no_request_on_sort` counts them.

#### 6.3 Typed client

`scripts/gen_types.sh` runs `desk export-openapi > contracts/openapi.json` then `openapi-typescript contracts/openapi.json -o web/src/api/schema.d.ts`. `usePanel` is typed by name:

```ts
type PanelName = keyof PanelResults;             // derived from schema.d.ts
export function usePanel<N extends PanelName>(name: N, params: PanelParams[N])
  : UseQueryResult<Envelope<PanelResults[N]>>;
```

`Envelope<T>` is `{ ok: true; panel: string; result: T | null; empty: { reason: string; fix: string } | null; provenance: Provenance }`. A screen that reads `data.result.foo` where `foo` is absent from the panel's result model fails `tsc --noEmit`, which replaces the old repo's 12 regex tests (R8 section 4). `client.ts` adds `X-CSRF-Token` from the `desk_csrf` cookie on every unsafe method and turns a non-2xx into `ApiError { status, error, detail, remediation_url }`.

Error shapes: unchanged from S0 section 6. A 401 from any panel redirects the shell to `/signin?next=`; a 403 renders `StateBox kind=error` with the `detail`.

### 7. Algorithms

#### 7.1 Theme resolution

`data-theme` on `<html>` wins, then `prefers-color-scheme`, then dark. An inline script in `index.html`, 6 lines, runs before the first paint: read `localStorage.desk.theme`; if `dark` or `light`, set `data-theme`; else remove the attribute. `ThemeProvider` exposes `theme: "dark" | "light" | "system"` and `setTheme`, writes `localStorage` in a `try` block, and sets `<meta name="color-scheme">` to `dark light`. Validation: `test_theme_resolution` (vitest) asserts the resolved theme for the 6 cases of stored value in `{absent, dark, light}` times OS preference in `{dark, light}`; absent plus OS dark is dark, absent plus OS light is light, stored wins in both directions.

#### 7.2 Contrast

`scripts/contrast_check.mjs` parses `web/src/tokens.css`, computes the WCAG 2.1 relative luminance and contrast ratio for `--muted` and `--faint` against `--bg`, `--surface` and `--raised` in both themes, and for `#ffffff` against `--accent`, and fails under 4.5:1. Expected ratios from the old validated block (`web/dist/app.css` lines 15 to 16 and 51 to 52 of the old repo):

| Pair | Dark | Light |
|---|---|---|
| muted on raised | 8.9 | 7.2 |
| faint on raised | 6.4 | 5.4 |
| white on accent | 5.6 | 5.6 |

The script runs in CI before `vite build`. A token change that drops a pair under 4.5 is a red push.

#### 7.3 Artefact store and state

`desk/artefacts/registry.py` extends the S0 dataclass:

```python
@dataclass(frozen=True)
class ArtefactKind:
    kind: str
    inputs_model: type[BaseModel]      # extra="forbid"
    payload_model: type[BaseModel]     # extra="forbid"
    per_user: bool
    read_by: tuple[str, ...]           # panel names
    retention: Retention               # keep: int (30), per_user: bool
    fresh_h: float                     # age under which state() says fresh
    aging_h: float                     # age under which state() says aging, else stale
    state: Callable[[ArtefactRow, Live], str]
```

`desk/artefacts/store.py`:

```
write(kind, inputs, payload, user_id, job_id) -> artefact_id
  validate both models; bytes = len(json); raise ArtefactTooLarge over 2,097,152
latest(kind, user_id=None) -> ArtefactRow | None
  newest by created_at; user_id filters when the kind is per_user
get(artefact_id, user_id) -> ArtefactRow
  raise NotFound when the row is absent or belongs to another user
age_state(created_at, now, fresh_h, aging_h) -> "fresh" | "aging" | "stale"
```

`state()` per kind composes `age_state` with `superseded` (a newer row of the kind exists for the same scope) and `missing` (no row); S2 and S4 supply the squad and gameweek clauses. Validation: `test_artefact_store` (section 10) and `test_artefacts_walk`, which for every registered kind asserts both models forbid extras, `read_by` names exist in `PANELS`, `retention.keep` is 30, a `Job` names `artefact:<kind>` in `writes`, and `state()` returns each of the five words on five constructed rows. The registry is empty in S8, so the walk runs over the test-only `probe` kind injected by the test.

#### 7.4 Age and countdown vocabulary

`Age` prints, for a past instant, `today` under 24 h, `yesterday` under 48 h, else `{n} days`; for a future instant, `today`, `tomorrow`, else `in {n} days`; a null prints the en dash (U+2013). The exact instant is the tooltip, `2026-10-01 11:00 UTC`. Hours never appear (R6 rule 36; the old chat printed `603h`). Lifted from `fmtAgeDays` and `absInstant`, `web/dist/js/app.js` lines 473 to 521 of the old repo.

`fmtCountdown(seconds_left)` is the one hours-and-minutes surface: over 172,800 s prints `{d} days {h} h`; over 3,600 s prints `{h} h {m} min`; over 0 prints `{m} min`; 0 prints `locked`. The header chip reads `GW6 in {countdown}` and the Dashboard line `GW6 deadline in {countdown}` from the same function. The element carries `data-countdown` so the `ui_budget` hour-unit check skips it. Validation: `test_age_vocabulary` and `test_countdown` in vitest, 9 cases each.

#### 7.5 Pitch formation

Input: `me_squad.picks` sorted by `slot`. Slots 1 to 11 are the XI; group by `position` in the order GKP, DEF, MID, FWD into four rows; slots 12 to 15 are the bench row in slot order. A pick with `multiplier` 2 or 3 gets `C`; `is_vice` gets `V`; `autosub_reversed` gets the `sub` chip; `status` in `{d, i, s, u, n}` gets the status dot (warn for `d`, bad otherwise) with `news` in the tooltip. The formation is derived from the picks, never from a preset; a body whose XI counts do not sum to 11 renders `StateBox kind=gap` with the reason `XI has {n} players in slots 1 to 11` and the fix `Refresh now`. Card width 86 px at 820 px and above, 68 px with a 4 px gap under it, so 5 cards fit 358 px inside a 390 px viewport with 16 px gutters. Validation: `test_pitch_formation` builds a 3-4-3, a 5-3-2 and a Bench Boost body (every multiplier 1, captain still marked from `is_captain`).

#### 7.6 Sort and filter state in the URL

`useUrlState(key, parse, serialise, default)` reads and writes one query parameter with `history.replaceState`, so a sort is `?sort=last_run.desc`, a filter `?state=failing&q=fpl`, and a tab `?tab=spend`. `DataTable` owns `sort`; `FilterRow` owns each filter key; `Drawer` owns nothing, because its level is the path. Every state change is a render from data already held. Validation: `test_url_state_round_trip` renders the Admin table, clicks a header, reads the URL, remounts from that URL and asserts the same `aria-sort`.

### 8. UI

Stack per D8 and ARCH section 9. `npx shadcn@4 init` then `add button card input badge table sheet dialog tabs toggle-group skeleton tooltip select dropdown-menu popover command`; every generated file under `web/src/components/ui/` is edited so its colours and sizes come from the token utilities and nothing else. Icons: lucide-react only. No webfont.

#### 8.1 Tokens, `web/src/tokens.css`

The complete block, lifted from the old `web/dist/app.css` lines 13 to 77 and frozen here. Tailwind 4 reads it through `@theme inline`, so `bg-surface`, `text-muted`, `text-cell` and `p-3` resolve to the variables and switch with the theme.

| Token | Dark | Light |
|---|---|---|
| `--bg`, `--surface`, `--raised` | #101214, #16181b, #1d2024 | #f7f8f9, #ffffff, #f1f3f5 |
| `--ink`, `--muted`, `--faint`, `--line` | #e8eaed, #b3bac2, #9aa3ad, #2a2e33 | #1a1d21, #4c5560, #5b6470, #e3e6ea |
| `--accent`, `--accent-ink`, `--pitch` | #3e6c91, #8fb8d8, #274d38 | #3e6c91, #33597a, #3a7d57 |
| `--s1`, `--s2`, `--s3`, `--s4` | #2a78d6, #c25322, #1baf7a, #b08000 | #2a78d6, #eb6834, #1baf7a, #eda100 |
| `--good`, `--warn`, `--bad` | #2fa463, #c77f00, #d64545 | #21924f, #b57500, #c73e3e |
| `--shadow` | 0 1px 3px rgb(0 0 0 / .4) | 0 1px 3px rgb(20 24 28 / .08) |
| `--fdr-1` to `--fdr-5` | #375523, #01fc7a, #3a3e45, #ff1751, #80072d | #375523, #01fc7a, #e7e7e7, #ff1751, #80072d |

Theme-independent: type `--t-title 19px`, `--t-figure 17px`, `--t-body 13px`, `--t-cell 12.5px`, `--t-label 11px`, `--t-micro 10.5px`; weights 400, 600, 700; spacing `--sp1 4px` to `--sp5 24px` in the steps 4, 8, 12, 16, 24; `--mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace`; `--dur-state 120ms`, `--dur-drawer 180ms`, both 0 under `prefers-reduced-motion`; layout `--rail 168px`, `--drawer 560px`, `--content-max 1280px`, breakpoint 820 px. `body` sets `font-variant-numeric: tabular-nums`. Status colours mean state and always ship with a word; the accent means selection; magnitude uses `--s1` alone; the FDR ramp is used by S2's ticker and nowhere else.

#### 8.2 The shell

Rail at 820 px and above, 168 px wide, six entries in decision order with a lucide icon and label: Dashboard `/`, Fixtures `/fixtures`, Projections `/projections`, Solver `/solver`, Creators `/creators`, Chat `/chat`; the active entry carries the accent as a 3 px left rule and `aria-current="page"`. Under 820 px the rail becomes a bottom tab bar, six entries at 64 px each, icon over a `--t-micro` label, fixed to the viewport foot with `env(safe-area-inset-bottom)`.

Header, 44 px: the wordmark `FPL Desk` (`--t-title`), the deadline chip `GW6 in 3 days 22 h` reading `deadline` (a `Skeleton` of 96 px while loading, nothing when `empty`), and the menu button showing the first letter of the email in a 24 px disc. The menu is a `DropdownMenu`: `Account`, a `Theme` group with radio items `Dark`, `Light`, `System`, `Admin` when `me.role` is `admin`, `Sign out` (POST with CSRF, then `/signin`). DECISION: Admin lives in the menu on every width and as a seventh rail entry at 820 px and above; the bottom bar stays at six because 7 by 64 px exceeds 390.

Content column: max 1,280 px, 16 px side gutters, 24 px between blocks. Screens are lazy `React.lazy` chunks; the shell chunk is under 220 KB gzipped, asserted by `test_bundle_size` on `vite build` output.

#### 8.3 The kit, `web/src/components/` and `web/src/charts/`

One component per job; a second implementation fails `test_one_implementation`.

| Component | Props | Behaviour |
|---|---|---|
| `DataTable<T>` | `columns` (TanStack `ColumnDef` with `meta: {numeric, secondary, firstSort: "asc" or "desc", unit, thin: (row) => number or null}`), `data`, `rowKey`, `onRowOpen?`, `urlKey`, `defaultSort`, `skeletonRows`, `empty` | every header sorts on click, Enter and Space with `aria-sort`; the arrow is a 16 px icon of fixed width on every column; the first column is sticky; numeric cells are `Number`; a `thin` cell shows a 3 px warn dot with the count in its title; a row with `onRowOpen` has a pointer, `tabIndex 0`, Enter opens; without it, none of those; `RowCount` under it; sort in the URL under `urlKey`; under 820 px columns with `secondary` hide and their values render in the row's drawer; the skeleton has `skeletonRows` rows at final height |
| `FilterRow` | `children`, `activeCount` | one row of controls above a table; under 820 px one button `Filters ({n})` opening a `Popover` with the same controls; each control writes its own URL key |
| `RowCount` | `shown`, `total`, `removedBy: string[]` | `{shown} of {total}`, then `filtered by {a}, {b}` when any |
| `Drawer` | `open` (derived from the route), `onClose`, `title`, `crumbs`, `width` fixed 560 | the one `Sheet`, right side, 180 ms, focus trapped, Escape and outside click close, focus returns to the opener; full width under 820 px; one level per click |
| `Breadcrumb` | `segments: {label, href}[]` | every segment except the last is a link to that level's URL; separator `/` |
| `StateBox` | `kind: empty, skeleton, error, gap, stale`, `reason`, `fix`, `rows`, `action?` | empty prints `reason` then `fix` verbatim; skeleton renders `rows` bars at final height; error is used only for a thrown request and shows `detail` with `Retry`; gap has a 3 px warn left rule; stale is a warn chip `stale · {Age}` with `source` and `last good {instant}` in the title |
| `PlayerCell` | `code`, `web_name`, `short_name`, `team_code`, `size: 24, 64 or 56x68`, `mine?`, `sub?` | photo from `/api/assets/player/{code}.png` in a box sized before load; the 24 px fallback is two initials in mono, the two larger a grey jersey figure (the old `FACE_FALLBACK` SVG, `app.js` lines 324 to 334); a failed load flips one class and moves nothing; `mine` draws a 2 px accent ring |
| `ClubMark` | `team_code`, `short_name`, `size: 14, 20 or 34` | crest from `/api/assets/club/{team_code}.png`; on error a disc with the club colour and a 3 letter monogram, the 20 codes from the old `clubmark.css` lines 30 to 49 as `[data-club]` rules, unknown codes on `--raised`; a crest is never placed inside a data cell |
| `Age` | `at`, `verb?` | section 7.4 |
| `Number` | `value`, `digits`, `signed?`, `unit?`, `tenths?` | mono, tabular, right aligned; `tenths` divides by 10 and prints one decimal so `141` reads `14.1`; a null prints the en dash; the true minus sign for negatives |
| `CiteChip` | `provenance`, `label?` | a button reading `{panel} · {Age(as_of)}`; its tooltip lists `computed_at`, `git_sha` and each `inputs` row as `{kind} {id} {as_of}`; placed beside every headline number |
| `Fold` | `summary`, `count`, `children`, `defaultOpen false` | a `details` element whose summary states the finding and the count, `{summary} ({count})`; chevron rotates in 120 ms |
| `ConfirmButton` | `label`, `title`, `body`, `confirmLabel`, `onConfirm` | the one confirm pattern: a `Dialog` with `body` and buttons `{confirmLabel}` and `Cancel`; used by metered Run-now |
| `Interval` | `lo`, `hi`, `point`, `n`, `width 96` | an SVG bar from `lo` to `hi` against a 0.5 rule with the point marked and `n {n}` beside it; never a bare percentage |
| `Pitch` | `picks`, `onCard?` | section 7.5; cards are `PlayerCell` 64 with name and `Number tenths` under it, `C` ribbon in warn, `V` ribbon in faint, `sub` chip, status dot; the surface uses `--pitch` with the old 44 px stripe gradient (`app.css` lines 279 to 288); a card is a button only when `onCard` is passed |
| `Sparkline` | `points: {t, v}[]`, `unit`, `width 120`, `height 28` | one Recharts `LineChart` with no axes, stroke `--s1`, a tooltip with `v {unit}` and the date |

`urlState.ts`, `ThemeProvider.tsx`, `Nav.tsx` and `Shell.tsx` live under `web/src/app/`. `BarDiverging` is S9's and `FixtureTicker` is S2's; both files are absent after S8.

#### 8.4 Dashboard v0, route `/`

Primary visual: `Pitch`. Above it, one line: `GW6 deadline in 3 days 22 h` from `deadline`, then `Picks from GW5, read {Age(picks_as_of)}` with a `CiteChip` for `me_squad`, then the finances line `Bank 0.3 · Sell value 100.0 · 2 FT (accrual)` from `bank_tenths`, `squad_sell_value_tenths`, `free_transfers` and `ft_source`; `accrual_mismatch` prints `2 FT (accrual, one check failed)`. Secondary, under the pitch: one `Fold`, summary `Chips played`, count from `chips_played.length`, body one line per chip `{name} in GW{gw}`; when `chips_played` is empty the fold reads `Chips played (0)` and opens to `None yet`. Filters 0, sorts 0, drill-down 0: a card is not a button in v0, since `player_detail` does not exist before S3; S5 moves this pitch into the Lineup fold and deletes nothing.

Empty: `StateBox kind=empty` with the panel's `reason` and `fix` verbatim, in the three precedences of SPEC S1 section 6.2. Loading: a skeleton pitch of 15 grey cards in a 4-4-2 plus bench, at final card size. Error: `StateBox kind=error`, `Squad could not be loaded.` with `Retry`, which refetches. Stale: when `picks_as_of` is older than the `deadline` panel's `current.gw` deadline, `StateBox kind=stale` above the pitch reading `Picks are from GW{gw}; GW{gw+1} picks land within 90 minutes of the deadline` with a button `Refresh now` calling `POST /api/me/squad/refresh`; a 202 turns the button into `Queued` for 60 s and refetches; a 200 refusal prints inline `Refreshed 23 minutes ago. Next refresh at 12:30.`; a 409 prints its `detail` with a link to `/account`.

Mobile at 390 px: cards at 68 px, 4 px gaps, rows wrap by formation, the finances line wraps to two lines, no horizontal scroll. Components: `Pitch`, `PlayerCell`, `ClubMark`, `Age`, `Number`, `CiteChip`, `Fold`, `StateBox`, shadcn `Button`, `Skeleton`.

#### 8.5 Account, route `/account`

Primary visual: one card with three rows. Row 1 identity: the email, a `Badge` `admin` when true, button `Sign out`. Row 2 team: S0's behaviour and copy unchanged (`FPL team id`, `Look up`, the preview card, `This is me`, `Not me`, `Change`, the three error sentences), with the rank through `Number` and the confirmed instant through `Age`. Row 3 appearance: label `Appearance`, a `ToggleGroup` of `Dark`, `Light`, `System`, default `System`, help text `System follows your device. Dark is used when the device has no preference.` The row writes `localStorage` and nothing to the server. No filters, no drill-down, no fold. Loading: `Skeleton` of three rows. Error: `StateBox kind=error` with the request's `detail`. Empty: none. Mobile: rows stack, buttons full width. The credential card and `chat_model` land in S7 as row 4; S8 renders no placeholder for them.

#### 8.6 Admin, routes `/admin`, `/admin/jobs/{job_id}`

Primary visual: the jobs board as a `DataTable`. Columns and first sort direction: `Task` (text, asc, sticky), `State` (severity desc: failing, refused, stale, never_ran, running, ok), `Last run` (`Age`, newest first), `Status` (text, secondary), `Rows` (`Number` of `rows_written`, desc, secondary), `Note` (text, secondary, truncated at 80 characters with the full note in the title), `Next due` (`Age`, soonest first, secondary), `Run` (a `Button` `Run now`, disabled and reading `Queued` or `Running` while a row for the task is in that state; a metered task wraps it in `ConfirmButton` with S0's copy `This run is estimated at {estimate} {unit}. {period} so far: {period_spend} of {period_cap}.`). Default sort: `State` then `Task`. `State` is a `Badge` with the status colour and the word: `failing` bad, `stale` and `refused` warn, `ok` good, `never_ran` and `running` muted.

Above the table: the worker strip from S0 (`Worker {worker_id}, tick {Age}, sha {git_sha}` in good, or `Worker silent for {age_s} s` in bad) and four tiles from `counts`: `ok`, `failing`, `stale`, `never_ran`, each a `--t-figure` number with its label. `FilterRow`: `State` as a `ToggleGroup` over `All, Failing, Stale, Refused, Never ran, Ok` (default `All`, URL key `state`) and `Task` as a text `Input` (default empty, URL key `q`, matches the task name). `RowCount` under the table.

Drill-down: a row opens the `Drawer` at `/admin/jobs/{last_run.job_id}` with `Breadcrumb` `Admin / {task} / run {job_id}`, a two-column definition list of every `admin_job_log` field except `log_tail`, `Number` for the counts, `Age` for the instants, and the `log_tail` in a `pre` block with the mono token. A row whose task has never run opens the drawer with `StateBox kind=empty`: `This job has not started. Its log will appear here when the worker claims it.` A `job_id` with no row shows the panel's `empty.reason`. Escape closes and focus returns to the row.

Secondary content: `Tabs` under the board with URL key `tab`: `Jobs` (default), `Spend` (three rows: meter, `{amount} of {cap} {unit}` through `Number`, a 4 px bar in `--s1` sized by `fraction`), `Users` (a `DataTable`: `Email` asc, `Role`, `Entry`, `Key` as `last4` or the en dash, `Chat USD` desc, `Last seen` newest first). Loading: `DataTable` skeleton at the registry row count (6 after S1). Error: `StateBox kind=error`. Mobile at 390 px: columns `Task`, `State`, `Last run`, `Run` stay; the secondary columns move into the drawer; the tab strip scrolls in its own box; the drawer is full width.

Surface inventory for the PR (R6 rule 35): every S0 Admin surface (board, worker strip, filter, drawer, Spend, Users, confirm dialog) maps to a location above; nothing is deleted.

#### 8.7 Placeholders, routes `/fixtures`, `/projections`, `/solver`, `/creators`, `/chat`

Each renders `StateBox kind=empty` with reason `{Section} is not built yet.` and fix `It lands in a later step. Dashboard shows your squad and the deadline now.` No panel call; `test_placeholders_fire_no_request` asserts 0 fetches. S2, S3, S4, S9 and S7 replace them.

#### 8.8 Sign-in, route `/signin`

S0's card on the tokens, unchanged copy. The theme script runs here too, so a stranger in light sees light.

### 9. Admin view

The admin sees the jobs board as a sortable, filterable table with the four count tiles, the worker strip, Run-now with the confirm pattern, the drawer with every ledger column and the 64 KB log tail at a URL, the Spend tab with three meters and bars, the Users tab with email, role, entry id, `last4` and month chat USD. On every screen the admin also sees a seventh rail entry and the `Admin` menu item.

A user must never see: any `admin_*` payload (403 from the policy table, unchanged), the Admin rail entry or menu item (rendered only on `me.role === "admin"`, and the API refuses regardless), another user's squad (`me_squad` is scoped by `Ctx.user_id`), a job's `params`, `note` or `log_tail`, any meter, any `last4`. The admin must never see a credential beyond `last4` or another user's session id. `test_routes_matrix` from S0 gains the two asset routes and proves the first list; the Playwright walk as a `user` asserts no element with `data-admin` renders.

### 10. Acceptance tests

In the order to write them. Python tests under `tests/`, vitest under `web/src/**/*.test.tsx`, Playwright under `web/e2e/`.

1. `test_tokens_contrast`: `node scripts/contrast_check.mjs` exits 0 on the shipped `tokens.css` and exits 1 when `--faint` is set to `#7a828c` in a temp copy.
2. `test_one_implementation`: a grep over `web/src` finds exactly one `<Sheet` (in `Drawer.tsx`), one `useReactTable(` (in `DataTable.tsx`), one `function Age` and one `fmtCountdown`; zero `font-size:` and zero `text-[` outside `tokens.css`; zero characters in the emoji ranges; zero em-dashes in any `.tsx` string; zero matches of `\b\d+\s*h\b` in string literals under `screens/` outside `fmtCountdown`.
3. `test_schema_types_regenerate_clean`: `scripts/gen_types.sh` produces a `schema.d.ts` byte-equal to the committed file; `contracts/openapi.json` contains both asset routes.
4. `test_panels_walk_use_panel_names`: every session-tier `PANELS` row name appears in `web/src` as `usePanel("<name>")`; every `usePanel("<x>")` literal names a row.
5. `test_artefact_store`: `write` on the `probe` kind returns an id, `bytes` equals the JSON length, `latest("probe")` returns it, a second `write` makes the first `superseded` under `state()`, `get` for another `user_id` raises `NotFound`, a payload over 2,097,152 bytes raises `ArtefactTooLarge`, an extra key in `inputs` raises `ValidationError`.
6. `test_artefacts_walk`: with `probe` injected, every field of section 7.3 is present, `state()` returns each of `fresh, aging, stale, superseded, missing` on five constructed rows, and `test_jobs_walk` accepts a fake `Job` with `writes=("artefact:probe",)` and counts 1 row written after one `write`.
7. `test_assets_proxy`: with the CDN stubbed, `club/1.png` is 200 `image/png` with `max-age=604800, immutable` and `X-Desk-Source: fpl-cdn`; a 404 upstream is 404 `asset_not_found` with `max-age=86400`; a 3 s stall is 502 `fpl_unreachable` with `no-store`; `club/abc.png` is 400 `invalid_params`; a second call for `club/1.png` hits the LRU and the stub records one upstream request; anonymous is 401.
8. `test_routes_matrix_gains_assets`: S0's matrix passes with the two routes classified as session.
9. `DataTable.test.tsx`: every header has `aria-sort`; a click on `Rows` sorts desc first and `Task` asc first; the URL carries `sort=rows.desc`; `fetch` is called 0 times across three sorts and two filters; a row with `onRowOpen` has `tabIndex 0` and opens on Enter; a row without has neither; the thin dot renders with the count in its title.
10. `Drawer.test.tsx`: opens when the route matches, traps Tab, closes on Escape and returns focus to the opener, renders `Breadcrumb` links to each level's URL.
11. `Age.test.tsx` and `countdown.test.ts`: the 9 cases of section 7.4 each, the tooltip instant in `YYYY-MM-DD HH:MM UTC`, the en dash on null.
12. `Pitch.test.tsx`: a 3-4-3 body renders rows of 1, 3, 4, 3 and a bench of 4; a 5-3-2 body rows of 1, 5, 3, 2; a Bench Boost body marks `C` from `is_captain`; an XI of 10 in slots 1 to 11 renders `StateBox kind=gap` with `XI has 10 players in slots 1 to 11`.
13. `StateBox.test.tsx`: each of the five kinds renders; empty prints `reason` then `fix` verbatim; error renders only when given an `ApiError`.
14. `Dashboard.test.tsx`: the `me_squad` empty envelope with `No team id saved` renders that reason and `Save your FPL team id on Account`; a `picks_as_of` older than the current deadline renders the stale box with `Refresh now`; a mocked 200 refusal prints `Refreshed 23 minutes ago. Next refresh at 12:30.`; the finances line reads `Bank 0.3 · Sell value 100.0 · 2 FT (accrual)` from the S1 contract snapshot.
15. `Admin.test.tsx`: a seeded `admin_jobs` payload whose `fpl_poll` last run is `error` with note `wrote_nothing_unexplained` renders that row first with a `failing` badge and the note in the row; the `failing` tile reads 1; filter `Failing` leaves `1 of 6`.
16. `e2e/screens.spec.ts`: against `vite preview` with panel routes answered from `contracts/panels/*.json`, for each of `/`, `/account`, `/admin`, `/fixtures` at 1,280 and 390 in dark and light: `document.documentElement.scrollWidth <= innerWidth`, at most 3 `/api/panels/` requests after navigation, panel response bytes under 250 KB (under 100 KB on `/`), cumulative layout shift under 0.005 via `PerformanceObserver`, no `\b\d+\s*h\b` in `innerText` outside `[data-countdown]`, a screenshot per case saved as a CI artefact.
17. `test_contract_snapshots_unchanged`: `contracts/panels/{me,deadline,me_squad,admin_jobs,admin_job_log,admin_spend,admin_users}.json` are byte-equal to S1's; the Dashboard compiles under `tsc --noEmit` against `schema.d.ts`, which is the panel schema contract for the screen.
18. `test_feature_check_s8`: `scripts/feature_check.py --lines assets,ui_budget` against `PUBLIC_URL` prints `assets ok` (`/api/assets/club/1.png` 200 with the 7 day header under the check session) and `ui_budget ok` (`scripts/ui_budget.py` walks `/`, `/account`, `/admin` in headless Chromium with the check session at 1,280 and 390 and asserts the test 16 numbers on live data). DECISION: `ui_budget.py` uses Playwright for Python, installed in `deploy-check.yml` with `playwright install chromium`; the check user is in `ADMIN_EMAILS` on Railway, which S0's `admin` line already requires for its Run-now.

Every fix commit carries a `Break-watch:` trailer naming the test that failed against the reverted code.

### 11. Definition of done

- [ ] Tests 1 to 17 green locally: `uv run pytest`, `npm test` (vitest), `npx playwright test`, `node scripts/contrast_check.mjs`, `tsc --noEmit`, eslint, `vite build` with the shell chunk under 220 KB gzipped.
- [ ] Every S0 and S1 test green, including `test_routes_matrix`, `test_panels_walk`, `test_jobs_walk`, `test_artefacts_walk`, `test_runbook_sync` and `test_layout_doc`.
- [ ] CI green on `main` with `contrast_check.mjs`, vitest and Playwright added to `ci.yml`; the prose checker passes over `web/src/screens` and `docs/ui.md`.
- [ ] Deployed to Railway; `/api/health` reports the deploy's `git_sha` and `alembic_head` `0003_s8_artefact`.
- [ ] Feature check green for `health`, `me`, `admin`, `fpl_sync`, `settle`, `me_squad`, `assets` and `ui_budget` on the deployed URL; `deploy-check.yml` installs Chromium and runs the `ui_budget` line.
- [ ] Owner's demo, in order: sign in; on `/` count 15 cards, read the price on Haaland's card, the `C` and `V`, the line `GW8 deadline in {countdown}`, the finances line and the picks age; open the `Chips played` fold; open the header menu, choose `Light`, read the same screen, reload, read it still in light; open `/account`, read the `Appearance` row; open the app on a phone at 390 px, tap `Solver` on the bottom bar, read the placeholder, tap `Dashboard`; open `Admin`, click the `Last run` header, read the sort arrow, choose `Failing` in the filter, read `0 of 6`, choose `All`, click the `fpl_poll` row, read the breadcrumb and the log, press Escape, read the focus back on the row; open `/api/assets/club/1.png`.
- [ ] Docs updated: `docs/ui.md` (new: the token table, the kit table with props, the anti-clutter rules and the test enforcing each, the surface inventory of the Admin rebuild), `docs/SPEC.md` (this block marked done with the deploy date), `docs/adr/0003-artefact-table-in-s8.md` (the DECISION of section 4), `README.md` (`npm run dev`, `npm test`, `npm run e2e`, `node scripts/contrast_check.mjs`, `python scripts/ui_budget.py`), `docs/runbook.md` regenerated with no diff.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src scripts` returns nothing.

### 12. Out of scope

| Not in S8 | Step |
|---|---|
| `FixtureTicker`, `fdr.ts`, the Fixtures screen, `fixture_ratings` kind | S2 |
| the Projections screen, `ChipRow`, `HeatCell`, the FPLReview upload card on Admin, `player_detail` and a card click on the pitch | S3 |
| the Solver screen, the `solve` lane, `solver_plan` kind, `solver_status` polling | S4 |
| the creators pipeline and its Admin rows | S6 |
| `BarDiverging`, the three-level Creators screen, `Interval` in anger | S9 |
| the Chat screen, the credential card and `chat_model` on Account, `has_key` | S7 |
| Dashboard v1 (the four decision rows, the brief), the Lineup fold that absorbs this pitch, `artefact` retention, `admin_spend.prefixes`, the light-theme re-audit of every screen | S5 |
| a server-side theme preference, per-user saved table views, a webfont | none in v1 |

### 13. Size and session plan

About 34 source files and 4,300 lines excluding tests; 18 test files and about 1,700 lines. Write in this order; each group's tests pass before the next starts.

| Order | Files | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0003_s8_artefact.py`; `desk/artefacts/registry.py` (extend), `desk/artefacts/store.py`; `tests/unit/test_artefact_store.py`, `tests/registry/test_artefacts_walk.py` (extend) | 60, 80, 120 |
| 2 | `desk/web/routers/assets.py`, `desk/auth/policy.py` (2 rows), `desk/web/app.py` (include); `tests/unit/test_assets_proxy.py` | 110, 6 |
| 3 | `web/src/tokens.css` (the full block), `web/index.html` (the theme script), `web/src/app/ThemeProvider.tsx`; `scripts/contrast_check.mjs` | 140, 12, 60, 90 |
| 4 | `web/src/components/ui/*` (shadcn, edited to tokens); `web/src/components/{Age,Number,StateBox,CiteChip,Fold,ConfirmButton,ClubMark,PlayerCell,RowCount,Breadcrumb,Drawer,FilterRow,DataTable,Interval}.tsx` | 900, 1,100 |
| 5 | `web/src/charts/{Pitch,Sparkline}.tsx`; `web/src/app/{urlState.ts,Nav.tsx,Shell.tsx,router.tsx}`; `web/src/api/{client,usePanel}.ts` (extend) | 260, 320, 80 |
| 6 | `web/src/screens/dashboard/DashboardScreen.tsx`, `countdown.ts`; `screens/account/AccountScreen.tsx` (rebuild on the kit); `screens/admin/{AdminScreen,columns,JobDrawer,SpendTab,UsersTab}.tsx`; `screens/placeholders/Placeholder.tsx` | 220, 40, 180, 520, 40 |
| 7 | vitest files of section 10; `web/e2e/screens.spec.ts` with the snapshot route mocks; `tests/unit/test_one_implementation.py`, `test_bundle_size.py` | 900, 160, 80 |
| 8 | `scripts/ui_budget.py`, `scripts/feature_check.py` (2 rows), `.github/workflows/{ci,deploy-check}.yml`; `docs/ui.md`, `docs/adr/0003-*.md`, `README.md`, `docs/SPEC.md` | 140, 30, 40, 260, 30 |

The three places a session is most likely to go wrong, with the guard:

1. shadcn's generated files carry their own colours and sizes (`text-sm`, `bg-zinc-900`, `text-[13px]`), and a screen written in a hurry adds a `style={{fontSize}}`. The old repo reached 27 font sizes this way (R8 section 1). Guard: group 4 edits every `components/ui/*` file before any screen is written, `test_one_implementation` greps `components/ui` too, and Tailwind's theme in `tokens.css` defines only the six text sizes so `text-sm` does not exist as a utility.
2. Sort, filter or tab state triggers a refetch, or a drawer is built inline in a screen because the shared one is a step away. The old Planner fetched `/api/solve/status` five times per mount and had four drawers (R8 section 1). Guard: `DataTable.test.tsx` counts `fetch` across sorts and filters; `useUrlState` uses `replaceState` and never touches the query key; the grep for one `<Sheet` runs in CI.
3. The Admin rebuild on the kit drops an S0 surface, or the Dashboard prints a fabricated `0.0` for a missing `bank_tenths`. The creators rebuild was reverted for deleting what the owner used (R6 rule 35) and the old squad card printed `£NaN` (rule 21). Guard: the PR carries the surface inventory of section 8.6; `Number` prints the en dash on null and the Dashboard renders `StateBox` from the payload's own `empty`; `test_contract_snapshots_unchanged` proves S8 changed no panel.

Risks against the decisions and the digests: no fact read for this step contradicts D1 to D10. Three ordering tensions inside the specs are resolved here: the `artefact` table's owner (S0 said S2, S2 said S0, S4 said S8; section 4 decides S8 and the ADR records it), `artefact_id`'s type (S2's string sample against S4's integer; section 4.1 decides bigint), and the check user's role (S0's `admin` feature-check line runs a Run-now, so the check user must be in `ADMIN_EMAILS`; test 18 depends on it and names it). R7 recommendation 9 asks for ECharts on the heatmaps; D8 and ARCH keep custom SVG, and S8 ships no heatmap. R8 open question 6 asks whether the system font stack stays; it stays, and `tokens.css` declares no `@font-face`.


# Part 8. Step S2: fixture projections

Step 4 of 10 in the build order of `docs/SPEC.md` (ARCH section 11). Depends on S0, S1 and S8. Target: deployed and green before the GW9 deadline. Season `2026-27`. Spelling is British. Every decision ARCH did not make is marked `DECISION:` so the assembler can lift it.

### 1. Goal

When this step is done the owner opens `/fixtures` and reads, for a horizon of 3, 5, 6 or 8 gameweeks, a 20-club ticker with two difficulties per cell (attackers and defenders) from a Dixon-Coles fit that a worker job refits daily at 11:00 UTC and records as an artefact with its inputs. He can click any cell and read p(clean sheet), xG for and against with his own club's strength folded in, the age of every input and the fit parameters behind the number, where the old app served a hand-run parquet that went 227 hours stale.

### 2. Depends on

| Step | What S2 uses from it |
|---|---|
| S0 | `job` table and the ledger helper in `desk/jobs/runner.py`; `JOBS` registry and the worker tick; `artefact` table (`artefact_id`, `kind`, `user_id`, `job_id`, `created_at`, `inputs` jsonb, `payload` jsonb) and `ARTEFACT_KINDS`; `PANELS` registry, the envelope, `GET /api/panels/{name}`; `Ctx`; `POST /api/admin/jobs/{task}/run`; `/api/health`; `scripts/feature_check.py`; `desk/core/time.py`; `desk/db/engine.py` |
| S1 | `fixture` (season, fixture_id, gw, kickoff_at, home_team_code, away_team_code, home_score, away_score, finished, finished_provisional, updated_at) with five seasons backfilled; `team`; `event`; `player`; `player_fixture` (xg, xgc, minutes, settled_at); `user_squad` (picks jsonb); `fpl_settle` at 10:30 UTC so the 11:00 fit sees settled scores |
| S8 | `web/src/tokens.css` including the FDR ramp; `Drawer`, `Breadcrumb`, `StateBox`, `Fold`, `ClubMark`, `Age`, `Number`, `CiteChip`, `DataTable`, `FilterRow`, `RowCount`; `usePanel(name, params)` typed from `schema.d.ts`; `urlState`; the Nav entry `Fixtures` routed to a placeholder; `/api/assets/club/{code}.png` |

`DECISION:` `web/src/charts/FixtureTicker.tsx` is built in S2, since S8 builds the kit and J2 item 11 leaves the Fixtures screen as "column definitions and one SVG". ARCH section 2 lists the file; S8 leaves it absent.

### 3. User stories

User role:

1. I open `/fixtures` and within 1,500 ms see 20 clubs by 6 gameweeks, each cell an attack band over a defence band on the five FDR colours with the signed value under the colour, and a verdict strip naming the three best attacking runs, the three best defensive runs and the three highest expected clean-sheet counts over GW9 to GW14.
2. I switch the horizon to 8 and the grid, the verdict and the ranks recompute for GW9 to GW16; the URL now reads `?h=8`, and a reload lands on the same view.
3. I switch the lens to Defence and every cell shows one band; the attack numbers are gone from the page and the verdict strip shows the defensive list only.
4. I switch the basis to Club and the printed values change to my club's own strength (Arsenal away at a promoted side reads p(CS) 0.49 where the opponent-only basis read 0.30); the colour does not change, and the legend says why.
5. I click the Man Utd GW9 cell and the drawer opens at `/fixtures/t/1/gw/9` with the opponent, venue, kickoff, xG for and against, p(clean sheet), p(concede 2 or more), the goals-conceded penalty, both bases side by side, both clubs' ratings in goals per game, and a freshness row per input in days.
6. Inside the drawer I click "Fit inputs" and at `/fixtures/t/1/gw/9/fit` I read fitted_at, 1,566 matches, effective n 528, converged, intercept 0.186, home advantage 0.173, rho minus 0.094, the promoted prior, the job id and the git sha.
7. I open "Torn fixtures (3)" and read three sentences of the form "GW11, you visit BRE: 6 of 40 as an attacking fixture, 31 as a defensive one."
8. I open "Club shape" and read last-6 xG for and against per club with the residual against the fitted rating, sortable by every header.
9. I toggle "My clubs" and clubs I hold nobody from dim; the rail shows "held 3" beside Man Utd.
10. On a 390 px phone the ticker scrolls sideways inside its own box with the club column pinned; the page itself does not scroll horizontally.
11. With no fit written I see the empty state "No fixture ratings yet. The fixture_fit job writes them daily at 11:00 UTC. An admin can run it now from Admin." and no number anywhere.
12. With the newest fit older than 7 days the grid still renders, with a stale chip "Fit is 9 d old" on the verdict strip and in every drawer.

Admin role:

13. On `/admin` I see the `fixture_fit` row: last run, status, `n_matches=1566 effective_n=528 converged=true` in the note, next due 11:00 UTC, the stale window 36 h, and a Run-now button with no cost confirm because the job spends nothing.
14. I press Run-now and within 2 minutes the row reads `ok`, `rows_written 1`, `items_seen 1566`, and the board's fit age resets to 0 d.
15. I read the artefact count for kind `fixture_ratings` (30 kept) and the log tail of the last run.

### 4. Data

S2 creates no table. It registers one artefact kind, defines the JSONB shapes of that kind, adds one index, and reads eight tables.

#### 4.1 Index

`DECISION:` migration `s2_artefact_kind_created` adds `CREATE INDEX artefact_kind_created_idx ON artefact (kind, created_at DESC)` so "latest row of a kind" is one index scan. If S0 already has that index under any name, S2 adds no migration.

#### 4.2 Artefact kind `fixture_ratings`

One row per fit. `user_id` NULL (shared). `job_id` is the `fixture_fit` run that wrote it. Retention: 30 rows, older rows deleted by the S5 `retention` job; S2 declares the rule on the `ArtefactKind` row.

`inputs` (pydantic `FixtureRatingsInputs`, `extra="forbid"`):

| Field | Type | Meaning |
|---|---|---|
| `season` | str | `2026-27`, the season whose clubs are rated |
| `fitted_at` | datetime (UTC) | the instant the fit ran; every state word derives from this |
| `window` | object | `n_matches` (int), `effective_n` (float, sum of decay weights), `oldest_kickoff_at`, `newest_kickoff_at` (datetime), `seasons` (list[str], ascending) |
| `fixture_updated_at` | datetime | `max(updated_at)` over `fixture` at fit time |
| `half_life_days` | float | 400.0 |
| `prior` | object | `attack_mean`, `defence_mean`, `attack_sd`, `defence_sd`, `n_clubs`, `n_seasons` |
| `git_sha` | str | the worker image |

`payload` (pydantic `FixtureRatingsPayload`, `extra="forbid"`):

| Field | Type | Meaning |
|---|---|---|
| `intercept`, `home_adv`, `rho` | float | the three globals |
| `converged` | bool | always true in a stored row (section 5) |
| `neg_log_lik` | float | the objective at the optimum |
| `mean_attack`, `mean_defence` | float | means over the season's 20 clubs, the anchor |
| `clubs` | list[20] | `team_code`, `short_name`, `attack`, `defence`, `is_promoted`, `matches_seen` |
| `population` | list[40] | per (opponent_code, we_are_home): `xg`, `xg_against`, `p_clean_sheet`, `p_concede_2plus`, `e_concede_penalty`, `attack_ease`, `defence_ease`, `attack_rank`, `defence_rank` |
| `anchors` | object | `attack_xg`, `defence_xg`, `clean_sheet`, `concede_penalty`, `clipped_pairs` |

Sample row, abbreviated:

```json
{ "artefact_id": "01J9...", "kind": "fixture_ratings", "user_id": null, "job_id": "01J9...",
  "created_at": "2026-09-22T11:00:04Z",
  "inputs": { "season": "2026-27", "fitted_at": "2026-09-22T11:00:04Z",
              "window": { "n_matches": 1576, "effective_n": 531.2,
                          "oldest_kickoff_at": "2022-08-05T19:00:00Z",
                          "newest_kickoff_at": "2026-09-21T15:00:00Z",
                          "seasons": ["2022-23","2023-24","2024-25","2025-26","2026-27"] },
              "fixture_updated_at": "2026-09-22T10:31:10Z", "half_life_days": 400.0,
              "prior": { "attack_mean": -0.354, "defence_mean": 0.276,
                         "attack_sd": 0.163, "defence_sd": 0.223, "n_clubs": 9, "n_seasons": 3 },
              "git_sha": "ab12cd3" },
  "payload": { "intercept": 0.186, "home_adv": 0.173, "rho": -0.094, "converged": true,
               "neg_log_lik": 1412.7, "mean_attack": 0.0, "mean_defence": 0.0,
               "clubs": [ { "team_code": 1, "short_name": "MUN", "attack": 0.12, "defence": -0.05,
                            "is_promoted": false, "matches_seen": 5 } ],
               "population": [ "40 objects with the fields of the table above" ],
               "anchors": { "attack_xg": 1.37, "defence_xg": 1.37, "clean_sheet": 0.28,
                            "concede_penalty": -0.40, "clipped_pairs": 5 } } }
```

State words, derived from `inputs.fitted_at` alone by `ArtefactKind.state()`:

| State | Rule |
|---|---|
| `fresh` | age under 36 h |
| `aging` | 36 h to 168 h |
| `stale` | over 168 h |
| `superseded` | a newer `fixture_ratings` row exists (only when a specific `artefact_id` is read) |
| `missing` | no row of the kind |

#### 4.3 Tables read

| Table | Columns used | Read rule |
|---|---|---|
| `fixture` | all | window: `finished = true AND home_score IS NOT NULL AND away_score IS NOT NULL AND kickoff_at < now`; schedule: `season = '2026-27' AND gw BETWEEN from_gw AND from_gw + horizon - 1` |
| `team` | `team_code`, `short_name`, `name` | `season = '2026-27'` |
| `event` | `gw`, `deadline_at`, `is_next` | the next GW is the row with `is_next`; fallback: the lowest `gw` with `deadline_at > now` |
| `player_fixture` | `code`, `fixture_id`, `xg`, `xgc`, `minutes` | current rows with `settled_at` set; `season = '2026-27'` |
| `player` | `code`, `team_code` | `season = '2026-27'` |
| `user_squad` | `picks` | latest `gw` row for `ctx.user_id` |
| `artefact` | `kind = 'fixture_ratings'` | newest by `created_at`; a current row, so `latest_at` does not apply |

Point-in-time: the fit reads matches with `kickoff_at < fitted_at`; a finished match at or after `fitted_at` raises `LeakageError` and the job lands as `error` (the old `read_finished_matches` guard, `fpl_edge/models/team_goals/data.py` lines 56 to 66). No backdated fits in v1. Cadence: `fixture` rows come from `fpl_poll` (six times a day) and `fpl_settle` (10:30 UTC, gated); the fit runs at 11:00 UTC; `player_fixture` xG lands with settlement.

### 5. Jobs

One job.

**`fixture_fit`**

| Item | Value |
|---|---|
| Registry row | `Job(name="fixture_fit", lane="pipeline", due=Calendar("0 11 * * *"), stale_window=timedelta(hours=36), writes=("artefact:fixture_ratings",), read_by=("fixtures_board", "fixture_detail"), meters=(), budget_s=60, main=desk.jobs.tasks.fixture_fit.main)` |
| Trigger | cron `0 11 * * *` UTC; `trigger = admin` from Run-now; `trigger = catchup` after a worker restart inside the 36 h lookback |
| Inputs | the match window from `fixture` (all seasons), the 2026-27 fixture list for the club set, `team` for short names |
| Outputs | one `artefact` row of kind `fixture_ratings` |
| Ledger row | `items_seen` = matches in the window (1,566 on the 2026-09-19 window); `items_done` = clubs rated (20); `rows_written` = 1, counted by the helper on `artefact WHERE kind = 'fixture_ratings'`; `rows_unchanged` 0; `note` = `n_matches=1566 effective_n=528 converged=true intercept=0.186 home_adv=0.173 rho=-0.094 promoted=3 prior_n=9`; `model` NULL; `usd` 0 |
| Cost cap | none; no metered API |
| Timeout | 60 s; the fit measured 2.5 s on 1,566 matches and the acceptance test caps it at 10 s |
| Failure | each lands as `status error` with no write, so the board keeps the last good row and ages it: under 200 matches, note `insufficient_history n=<n>`; optimiser not converged, note `did_not_converge iters=<n>`; fewer than 3 promotion events with 19 or more matches, note `promoted_prior_insufficient n=<n>`; a finished match stamped at or after `fitted_at`, note `leak <fixture_id>`; any other exception, the helper's log tail |
| Silent nothing | A run that wrote nothing without a note lands as `error wrote_nothing_unexplained`. A job that never fires shows as `fit.age_days` past 1 on the verdict strip, `fixture_fit` past its 36 h stale window on Admin and in `/api/health` `jobs.stale`, and a red `fixtures` feature-check line at 168 h |

`DECISION:` the job writes a new row on every run even when no match has finished since the last run, because the decay weights move with the clock and the refit is 2.5 s; `nothing_to_do` is never used by this job. Retention keeps 30 rows.

`DECISION:` `scipy>=1.14` is added to `pyproject.toml` for `scipy.optimize.minimize` (L-BFGS-B with an analytic gradient). It is imported only under `desk/fixtures/`; a grep test (`test_scipy_confined`) fails on an import anywhere else, including `desk/panels`.

### 6. API and panels

Two panels, both session tier, served as `GET /api/panels/{name}` by the S0 route factory with `response_model` set, both exposed as chat tools in S7 (`panel_fixtures_board`, `panel_fixture_detail`). Errors follow S0: 401 `{error: "unauthenticated"}`; 422 `{ok: false, error: "invalid_params", detail: [...]}`; 500 `{ok: false, error: "panel_failed", panel: "<name>"}` on a raise against a populated database. `budget_ms` is 1,500 for the board and 800 for the detail, inside the 10 s budget.

#### 6.1 `fixtures_board`

Params (`FixturesBoardParams`, `extra="forbid"`):

| Param | Type | Default | Rule |
|---|---|---|---|
| `horizon` | int | 6 | one of 3, 5, 6, 8 |
| `from_gw` | int or null | null | 1 to 38; null means the next GW |
| `lens` | enum | `both` | `attack`, `defence`, `both` |
| `basis` | enum | `both` | `opponent`, `club`, `both` |

`DECISION:` the screen always sends `lens=both&basis=both` and toggles client side, so a lens or basis switch issues no request (ARCH section 9). A horizon change refetches; TanStack Query caches by key. Chat tools pass a narrow lens or basis to keep a result under 20 KB. `lens=attack` nulls every `defence_*` field in cells, horizon blocks and the verdict and sets `verdict.defence` to `[]`; symmetric for `defence`. `basis=opponent` nulls every `club` object; `basis=club` nulls every `opponent_only` object.

`example_params`: `{"horizon": 6, "lens": "both", "basis": "both"}`.

Result (`FixturesBoardResult`, `extra="forbid"`):

```json
{ "season": "2026-27", "gws": [9,10,11,12,13,14], "from_gw": 9, "horizon": 6,
  "lens": "both", "basis": "both", "state": "fresh",
  "fit": { "artefact_id": "01J9...", "fitted_at": "2026-09-22T11:00:04Z", "age_days": 0.4,
           "n_matches": 1576, "effective_n": 531.2, "converged": true },
  "scale": { "domain": 0.60, "unit": "goals per match versus a league-average fixture",
             "steps": [0.36, 0.12, -0.12, -0.36], "colour_basis": "opponent_only" },
  "verdict": { "attack": [ { "team_code": 43, "short_name": "MCI", "ease_sum": 1.84, "rank": 1 } ],
               "defence": [ { "team_code": 3, "short_name": "ARS", "ease_sum": 1.51, "rank": 1 } ],
               "clean_sheets": [ { "team_code": 3, "short_name": "ARS", "cs_sum": 2.61 } ],
               "torn_count": 3 },
  "clubs": [ { "team_code": 1, "short_name": "MUN", "name": "Man Utd",
               "held": 3, "held_reason": null,
               "rating": { "the nine fields shown under fixture_detail.team.rating": 0 },
               "horizon": { "attack_ease_sum": 0.42, "defence_ease_sum": -0.10,
                            "attack_rank": 7, "defence_rank": 12, "rank_gap": -5,
                            "cs_sum": 1.71, "n_rated": 6 },
               "form": { "the six fields shown under fixture_detail.team.form": 0 },
               "cells": [ { "gw": 9, "blank": false, "double": false,
                            "fixtures": [ { "fixture_id": 81, "opponent_code": 94, "opponent": "BRE",
                                            "is_home": false, "label": "@BRE",
                                            "kickoff_at": "2026-10-17T14:00:00Z", "source": "model",
                                            "opponent_only": { "attack_ease": 0.21, "defence_ease": -0.14,
                                                               "attack_step": 2, "defence_step": 4,
                                                               "xg": 1.58, "xg_against": 1.51,
                                                               "p_clean_sheet": 0.22,
                                                               "attack_rank": 12, "defence_rank": 27,
                                                               "rank_gap": -15 },
                                            "club": { "attack_ease": 0.33, "defence_ease": -0.09,
                                                      "xg": 1.70, "xg_against": 1.46, "p_clean_sheet": 0.23 },
                                            "unavailable": null } ] } ] } ],
  "torn": [ { "gw": 11, "fixture_id": 104, "team_code": 1, "short_name": "MUN", "opponent": "BRE",
              "is_home": false, "attack_rank": 6, "defence_rank": 31, "gap": -25,
              "sentence": "GW11, you visit BRE: 6 of 40 as an attacking fixture, 31 as a defensive one." } ],
  "calibration": { "headline": "...", "horizon_gws": 6, "fixture_swing_attack_pts": 2.24,
                   "fixture_swing_defence_pts": 3.05, "team_quality_attack_pts": 9.1,
                   "team_quality_defence_pts": 11.6, "ratio_attack": 4.1, "ratio_defence": 3.8 },
  "freshness": [ { "name": "fitted ratings", "source": "artefact fixture_ratings",
                   "as_of": "2026-09-22T11:00:04Z", "age_days": 0.4, "rows": 20, "state": "fresh",
                   "stale_after_hours": 168, "effect_when_stale": "...", "detail": "..." } ] }
```

`calibration.headline` reads "Over 6 gameweeks the fixture run is worth about 2.2 points to an attacker and 3.1 to a defender, best club to worst. Which club you own is worth about 4x that. Use this page to break ties between similar assets." `freshness` has three rows: `fitted ratings` (stale after 168 h; effect: "the split is still shown; a week-old fit beats a made-up fresh one, and the fit predates recent results"), `schedule` (source `fixture`, stale after 48 h; effect: "a rescheduled fixture may still show at its old date") and `team form (xG)` (source `player_fixture`, no threshold; effect: "nothing: the residual is a diagnostic and never enters a difficulty"). `detail` is one sentence with counts.

Field rules: `held` counts the user's 15 picks whose `player.team_code` matches; null with `held_reason` "No squad stored yet. Set your team id on Account." when `user_squad` has no row for the session user. `label` is the opponent's short name, upper case at home, prefixed `@` away. `attack_step` and `defence_step` are 1 to 5 from section 7.4 on the opponent-only basis; the `club` object carries no step because colour never follows the club basis. `unavailable` is a sentence when the opponent is absent from the fit ("BRE is not in the stored fit. The fixture was added after the last ratings build.") and every number in the cell is null. A club with 0 fixtures in the window is absent from `clubs`.

Provenance `inputs`: `[{kind: "artefact", id: "<artefact_id>", as_of: fitted_at}, {kind: "table", id: "fixture", as_of: max(updated_at)}, {kind: "table", id: "player_fixture", as_of: max(settled_at)}, {kind: "table", id: "user_squad", as_of: <row updated_at or null>}]`. No user id anywhere in the envelope.

Empty: `{reason: "No fixture ratings yet.", fix: "The fixture_fit job writes them daily at 11:00 UTC. An admin can run it now from Admin."}` when no artefact exists; `{reason: "No 2026-27 fixtures from GW9.", fix: "fpl_poll fills the fixture table."}` when the schedule is empty. A `stale` fit is served with `state: "stale"`, never as empty.

Budget: 1,500 ms; target under 200 ms. 20 clubs by 8 gameweeks by two bases is about 45 KB; the contract test caps the envelope at 100 KB.

`headline(result)` for chat: `verdict`, `fit`, `state`, and per club `short_name`, `held`, `horizon`; no cells.

#### 6.2 `fixture_detail`

Params (`FixtureDetailParams`, `extra="forbid"`): `team_code` int (must exist in `team` for the season), `gw` int 1 to 38. `example_params`: `{"team_code": 1, "gw": 9}`.

Result (`FixtureDetailResult`, `extra="forbid"`):

```json
{ "season": "2026-27", "gw": 9, "state": "fresh",
  "team": { "team_code": 1, "short_name": "MUN", "name": "Man Utd",
            "rating": { "attack": 0.12, "defence": -0.05, "attack_rank": 6, "defence_rank": 8,
                        "is_promoted": false, "matches_seen": 5,
                        "scores_pg": 1.52, "concedes_pg": 1.30, "league_pg": 1.37 },
            "form": { "window_matches": 5, "xg_for_pg": 1.61, "xg_against_pg": 1.22,
                      "xg_for_resid": 0.09, "xg_against_resid": -0.08, "unavailable": null } },
  "blank": false,
  "fixtures": [ { "fixture_id": 81, "kickoff_at": "2026-10-17T14:00:00Z", "is_home": false,
                  "label": "@BRE",
                  "opponent": { "team_code": 94, "short_name": "BRE", "name": "Brentford",
                                "rating": { "same nine fields as team.rating": 0 } },
                  "opponent_only": { "attack_ease": 0.21, "defence_ease": -0.14, "attack_step": 2,
                                     "defence_step": 4, "xg": 1.58, "xg_against": 1.51,
                                     "p_clean_sheet": 0.22, "p_opponent_clean_sheet": 0.21,
                                     "p_concede_2plus": 0.44, "e_concede_penalty": -0.47,
                                     "attack_pts": 0.25, "defence_pts": -0.31,
                                     "attack_rank": 12, "defence_rank": 27, "rank_gap": -15 },
                  "club": { "the same fields without steps and ranks": 0 },
                  "result": null, "unavailable": null } ],
  "fit": { "artefact_id": "01J9...", "job_id": "01J9...", "git_sha": "ab12cd3",
           "fitted_at": "2026-09-22T11:00:04Z", "age_days": 0.4, "state": "fresh",
           "n_matches": 1576, "effective_n": 531.2, "converged": true,
           "intercept": 0.186, "home_adv": 0.173, "rho": -0.094, "half_life_days": 400.0,
           "seasons": ["2022-23","2023-24","2024-25","2025-26","2026-27"],
           "prior": { "the six prior fields of section 4.2": 0 } },
  "freshness": [ ] }
```

`result` is `{home_score, away_score, finished}` when finished, else null. `blank: true` with `fixtures: []` on a blank gameweek. Empty envelope on no artefact, same reason as the board. Provenance inputs as the board minus `user_squad`.

#### 6.3 Routes summary

| Method, path | Tier | Params | Result | Budget |
|---|---|---|---|---|
| `GET /api/panels/fixtures_board` | session | section 6.1 | `FixturesBoardResult` in the envelope | 1,500 ms |
| `GET /api/panels/fixture_detail` | session | section 6.2 | `FixtureDetailResult` in the envelope | 800 ms |
| `POST /api/admin/jobs/fixture_fit/run` | admin | none; the S0 route | `{job_id, state: "queued"}`; no `needs_confirm` because the job has no meter | S0 |

No other route is added. `contracts/panels/fixtures_board.json` and `contracts/panels/fixture_detail.json` are committed snapshots.

### 7. Algorithms

Source of every formula: `fpl_edge/models/team_goals/dixon_coles.py`, `promoted.py`, `scoreline.py` and `fpl_edge/platform/scripts/fixtures/ratings.py` lines 236 to 290 in the old repo, lifted into `desk/fixtures/`.

#### 7.1 Dixon-Coles fit (`desk/fixtures/dixon_coles.py`)

For a match with home club h and away club a:

```
log lambda_home = c + g + attack[h] + defence[a]
log lambda_away = c     + attack[a] + defence[h]
```

`attack` positive scores more; `defence` positive concedes more (leakiness). The joint probability is independent Poisson times the Dixon-Coles tau on four cells:

```
tau(0,0) = 1 - lambda*mu*rho    tau(0,1) = 1 + lambda*rho
tau(1,0) = 1 + mu*rho           tau(1,1) = 1 - rho
```

Match weight `w = exp(-ln 2 * age_days / 400)`, age measured from `kickoff_at` to `fitted_at`, clipped at 0. Penalised log-likelihood:

```
L = sum_i w_i * [ log tau_i + x_i log lambda_i - lambda_i + y_i log mu_i - mu_i ]
    - 0.5 * sum_t [ (attack_t - a_mean_t)^2 / a_sd_t^2 + (defence_t - d_mean_t)^2 / d_sd_t^2 ]
```

Minimise `-L` with L-BFGS-B and the analytic gradient (the old `_objective`, copied). Parameter vector `[c, g, rho, attack[0..n), defence[0..n)]`.

Constants:

| Constant | Value | Reason |
|---|---|---|
| `HALF_LIFE_DAYS` | 400.0 | out-of-sample sweep; objective flat between 240 and 400 (R2 section 4) |
| `RHO_BOUNDS` | (-0.30, 0.15) | negative rho is the observed direction; the upper bound keeps tau(0,0) positive |
| `LOG_RATE_CLIP` | (-4.0, 2.5) | goal rates in [0.02, 12] |
| `TAU_FLOOR` | 1e-9 | keeps the likelihood finite |
| bounds, start, options | c in [-2, 2], g in [-1, 1], attack and defence in [-2, 2]; start c = log(mean goals per side), g = 0.20, rho = -0.03, attack and defence at the prior means; `maxiter 500, ftol 1e-10, gtol 1e-7` | the old fit, deterministic with no random start |
| `MIN_MATCHES_TO_FIT` | 200 | below it the job refuses |
| `ESTABLISHED_PRIOR_SD` | 0.50 | wide enough that a season of matches overwhelms it |

Club set: every club in the window plus every club in the 2026-27 fixture list. Promoted clubs are 2026-27 clubs with no match in the window before 2026-27 (the old `promoted_team_codes`): a returning club with history is established.

Validation: tests 2, 3, 5 and 6 of section 10; the reference fit is intercept 0.186, home advantage 0.173, rho minus 0.094, effective n 528 on the 1,566-match window at `2026-09-19T11:00:00Z`.

#### 7.2 Promoted prior (`desk/fixtures/promoted.py`)

For every season in the window after the first, for every club with no earlier match, with at least 19 matches played in that season:

```
attack_offset  = log((scored + 0.5) / played / league_rate)
defence_offset = log((conceded + 0.5) / played / league_rate)
league_rate    = total goals / (2 * matches) in that season
```

Prior mean is the mean over these observations, prior sd the sample sd (ddof 1) floored at 0.05. `MIN_PRIOR_MATCHES = 19` (Hull's 3-match run once pushed the defence sd from 0.22 to 0.93). `MIN_CLUBS = 3`; fewer observations is a job error, and the old assumed fallback is dropped. No promotion-route covariate in v1. On the committed window the prior is attack minus 0.354 (sd 0.163) and defence plus 0.276 (sd 0.223) from 9 clubs over 3 seasons; test 4 allows 0.02.

#### 7.3 Score matrix and quantities (`desk/fixtures/scoreline.py`, numpy only)

`score_matrix(lambda, mu, rho)` is the 9 by 9 outer product of Poisson pmfs for 0 to 8 goals, times tau (floored at 1e-9), renormalised to 1. Every scalar is read off the matrix:

| Quantity | Formula |
|---|---|
| `xg` | sum over i of i * P(home = i) (row marginal) |
| `xg_against` | column marginal mean |
| `p_clean_sheet` | sum of column 0 (the opponent scores 0) |
| `p_opponent_clean_sheet` | sum of row 0 |
| `p_concede_2plus` | sum of conceded[2..8] |
| `e_concede_penalty` | sum over k of conceded[k] * -(k // 2), FPL's minus 1 per 2 goals |

The matrix is oriented so "our goals" are rows: for our club at home, lambda is the home rate; away, lambda is the away rate and mu the home rate.

#### 7.4 From ratings to difficulty (`desk/fixtures/difficulty.py`)

Rates, with `abar = mean_attack`, `dbar = mean_defence` over the season's 20 clubs, `h = 1` when we are home:

```
opponent-only:  ours   = exp(c + g*h       + abar        + defence[opp])
                theirs = exp(c + g*(1 - h) + attack[opp] + dbar)
club:           ours   = exp(c + g*h       + attack[us]  + defence[opp])
                theirs = exp(c + g*(1 - h) + attack[opp] + defence[us])
```

Population: the 40 (opponent, venue) pairs on the opponent-only basis. `anchor_att = mean xg`, `anchor_def = mean xg_against`, `ref_cs = mean p_clean_sheet`, `ref_pen = mean e_concede_penalty` over the 40.

```
attack_ease  = xg - anchor_att            (positive: easier to score)
defence_ease = anchor_def - xg_against    (positive: easier to keep a clean sheet)
attack_pts   = attack_ease * 0.30 * 4     (ATTACKER_SHARE 0.30, FPL_GOAL_POINTS 4)
defence_pts  = (p_clean_sheet - ref_cs) * 4 + (e_concede_penalty - ref_pen)
attack_rank  = rank of attack_ease descending over the 40, method min, 1 easiest
defence_rank = rank of defence_ease descending over the 40
rank_gap     = attack_rank - defence_rank
```

Colour steps on `SCALE_DOMAIN = 0.60` (two population sds; 5 of 40 pairs saturate on purpose): `t = clip(ease / 0.60, -1, 1)`; step 1 when `t >= 0.6`, step 2 when `t >= 0.2`, step 3 when `t > -0.2`, step 4 when `t > -0.6`, else step 5. In goals: boundaries at +0.36, +0.12, -0.12, -0.36. The same function lives once in Python (`step_of`) and once in TypeScript (`web/src/charts/fdr.ts`); a fixture of 9 values pins both.

Club rating in goals per game: `scores_pg = mean over h in {1, 0} of exp(c + g*h + attack[us] + dbar)`, `concedes_pg = mean over h of exp(c + g*(1-h) + abar + defence[us])`, `league_pg` the same with `abar`. `rating.attack_rank` ranks `attack` descending; `rating.defence_rank` ranks `defence` ascending (lowest leakiness is best).

Horizon per club: sum of opponent-only `attack_ease` and `defence_ease` over rated fixtures in the window (a double counts twice, a blank adds 0); `cs_sum` is the sum of club-basis `p_clean_sheet`, since a clean sheet is a fact about a specific defence. Horizon ranks over clubs with `n_rated > 0`, descending, method min. Torn: `|rank_gap| >= 10` (`DIVERGENCE_RANKS`, a quarter of the population), sorted by `|gap|` descending then gw; sentence template in section 6.1.

Form: per finished 2026-27 fixture and club, `xg_for = sum(player_fixture.xg)` over the club's players, `xg_against = max(player_fixture.xgc)` (xgc is stamped per player with the team value, so a sum would give 30 plus). Side resolved through `player.team_code`. `FORM_WINDOW 6`, `FORM_MIN_MATCHES 3` for the residual: `xg_for_resid = mean(xg_for) - mean(club-basis xg over the same fixtures)`.

Calibration over the requested horizon (the old `model_calibration`): per fixture compute attack and defence points on both bases; per club average over the window; spread = max minus min across clubs, times the number of gameweeks. `fixture_swing_*` uses the opponent-only basis, `team_quality_*` the club basis, `ratio = team / fixture`, printed with one decimal and the ratio as an integer.

Validation: tests 7 and 8 of section 10, plus a check that recomputing the payload's `population` from `clubs` and the globals matches to 1e-6.

### 8. UI

One screen and one drawer with two levels. Route `/fixtures`. Primary visual: the `FixtureTicker` heatmap, 20 clubs by `horizon` gameweeks. Nothing else sits above the fold except the control row and the verdict strip; the folded page is under 1,400 px at 1,280.

#### 8.1 `/fixtures`

Top to bottom at 1,280 px:

1. Control row (one `FilterRow`): `Gameweeks` toggle group `3 | 5 | 6 | 8` (default 6); `Lens` toggle group `Attack | Defence | Both` (default Both); `Basis` toggle group `Opponent | Club` (default Opponent); `Sort` select `Attack rank | Defence rank | A to Z` (default Attack rank); `My clubs` toggle (default off). URL state `?h=6&lens=both&basis=opponent&sort=att&mine=0`, written by every control and read on mount. At the right end: the fit `Age` in days with a `CiteChip` (panel, `fitted_at`, git sha), and a stale `StateBox` chip "Fit is 9 d old" when `state` is `stale`.
2. Verdict strip, three rows of three chips: `Best for attackers` (club mark, short name, ordinal rank, `+1.84`), `Best for defenders`, `Clean sheets` (short name, `2.61`). A chip click scrolls the ticker to that row and flashes it 120 ms; it opens nothing. `lens=attack` hides the defenders row, `lens=defence` the attackers row; clean sheets always shows.
3. `FixtureTicker`: rail 168 px with `ClubMark` (crest from `/api/assets/club/{code}.png`, monogram fallback), short name, `held N` chip when `held` is a number, two 3 px rank bars (attack and defence rank over the horizon). Cells 44 px tall, equal width; two bands, attack over defence, filled with the FDR step colour, the opponent label (`BRE` at home, `@BRE` away) on a surface plate, the signed value at 10.5 px under each band with one decimal. A double gameweek splits the cell into two fixtures; a blank cell is hatched with `--fx-hatch` and prints `blank`. Under `lens=attack` or `defence` the cell has one band. Under `basis=club` the printed value is the club-basis ease, the colour stays the opponent-only step, and the legend reads "Printed: your club's own strength. Colour: opponent only." A dashed seam between the bands marks `|rank_gap| >= 10`. Legend at the foot: five swatches `1 easiest` to `5 hardest`, the unit "goals per match versus a league-average fixture", the colour basis.
4. Folds (`Fold`, closed, summary states the finding and the count): `Torn fixtures (3)`, a `DataTable` with GW, Club, Opponent, Attack rank, Defence rank, Gap, Sentence; `Club shape (20)`, a `DataTable` with Club, Matches, xG for, xG against, Residual for, Residual against, a short dash where `unavailable` is set; `How much a fixture is worth`, the calibration headline plus its six numbers in a two-column list.

Drill-down levels:

| Element | Opens |
|---|---|
| a cell, single or double | level 2 drawer at `/fixtures/t/<team_code>/gw/<gw>`, listing both fixtures on a double |
| a rail club or a club-shape row | the drawer at the club's next GW |
| a torn-fold row | the drawer for that club and gw |
| a verdict chip | scrolls to the row; opens nothing |

Loading: a skeleton of 20 rail rows and `horizon` cells per row at the final 44 px height, rail 168 px, verdict strip reserved at 3 rows of 24 px, so layout shift is 0. Data paints within 1,500 ms from one panel call.

Empty: `StateBox` empty with the payload's reason and fix ("No fixture ratings yet. The fixture_fit job writes them daily at 11:00 UTC. An admin can run it now from Admin."). The control row stays; the folds do not render.

Error: `StateBox` error "fixtures_board failed. Reload, or read the job log on Admin." only when the request threw; a stale payload is never an error.

Mobile at 390 px: the control row collapses to one `Filters` button that opens the shared `Drawer` with the five controls; the ticker scrolls sideways inside its own box with the rail sticky at 96 px (crest and short name; `held` becomes a dot); cells stay 44 px wide; the verdict strip wraps one chip per line; the two `DataTable` folds hide secondary columns into the row drawer; the drawer is full width. No horizontal scroll on the page itself.

Every button and label string above is the exact copy; the prose checker runs over `web/src/screens/fixtures`.

#### 8.2 The drawer, level 2: `/fixtures/t/<team_code>/gw/<gw>`

`Drawer` at 560 px with `Breadcrumb` `Fixtures / MUN / GW9`. Content:

1. Header: `ClubMark`, club name, `GW9`, the opponent label, kickoff as a local date with the UTC instant in a tooltip; a result line "Won 2 to 1" when finished.
2. Two columns, `Opponent only` and `Club`, seven rows: xG for, xG against, p(clean sheet), p(concede 2+), goals-conceded penalty, attack points, defence points. `Number` mono, right aligned, two decimals; probabilities as whole percent; the unit once per row label.
3. A ranks line: "12 of 40 as an attacking fixture, 27 as a defensive one", plus the torn sentence when `|rank_gap| >= 10`.
4. Club and opponent ratings: two `ClubMark` rows with scores per game, concedes per game, the league figure, attack and defence rank of 20, a `promoted` chip, `matches_seen`.
5. Form: last 6 xG for and against with residuals, or the `unavailable` sentence.
6. Freshness rows: one per input with `Age` in days and `effect_when_stale` in a tooltip; a `stale` row is warn-coloured with the word `stale`.
7. A button `Fit inputs` that pushes level 3.

A double gameweek renders items 1 to 3 twice. A blank gameweek renders the header, "No fixture in GW9", and items 4 to 7. Empty and error states use `StateBox` inside the drawer. Escape closes, returns focus to the cell and restores `/fixtures` with its query string.

#### 8.3 The drawer, level 3: `/fixtures/t/<team_code>/gw/<gw>/fit`

Breadcrumb `Fixtures / MUN / GW9 / Fit`. A definition list from `fixture_detail.fit`: fitted at (`Age` plus the instant), matches, effective n, seasons, half-life, converged, intercept, home advantage, rho, the promoted prior, artefact id, job id, git sha. `CiteChip` on the header. No further level. Admins see one extra link "Open job on Admin" to `/admin/jobs/<job_id>`.

#### 8.4 Components

Shared, from S8: `FilterRow`, `Drawer`, `Breadcrumb`, `StateBox`, `Fold`, `ClubMark`, `Age`, `Number`, `CiteChip`, `DataTable`, `RowCount`, shadcn `ToggleGroup`, `Select`, `Tooltip`, `Skeleton`.

New in S2: `FixtureTicker` (`web/src/charts/FixtureTicker.tsx`, custom SVG under 200 lines, props `clubs`, `gws`, `lens`, `basis`, `sortKey`, `mine`, `onCell`, `onClub`, `scrollTo`); `fdr.ts` (`stepOf(ease)`, `STEP_TOKENS`, legend labels). FDR tokens from S8's `tokens.css`: steps 1, 2, 4, 5 are `#375523`, `#01fc7a`, `#ff1751`, `#80072d` in both themes; step 3 is `#e7e7e7` in light and `#3a3e45` in dark; inks `#ffffff`, `#37003c`, theme ink, `#37003c`, `#ffffff`. A grep test asserts `stepOf` is defined once under `web/src`.

### 9. Admin view

On `/admin`, S8's jobs board; S2 adds no screen:

| Admin sees | Source |
|---|---|
| `fixture_fit` row: last run `Age`, status chip, `rows_written`, `items_seen`, the note (`n_matches`, `effective_n`, `converged`, the three globals, promoted count), next due, stale window 36 h, `trigger` | `admin_jobs` |
| Run-now: inserts `trigger = admin`, no confirm dialog (no meter); the row goes `queued`, `running`, `ok` within 2 minutes | `POST /api/admin/jobs/fixture_fit/run` |
| the log tail of any run, including the note on an `error` row | `admin_job_log` |
| artefact count for `fixture_ratings` (30 kept) and bytes | `admin_spend` (S5 adds the retention row) |
| the "Open job on Admin" link at drawer level 3 | `me.role == admin` |

A user never sees: the jobs board, Run-now, any log tail, the `error` notes, the artefact count, the job link. A user does see the fit parameters at drawer level 3, because they are provenance. The feature check user runs as `user` for the fixtures line and as `admin` for the Run-now line.

### 10. Acceptance tests

In the order to write them. Backend tests run against the testcontainers Postgres with the S1 schema; no network, no scipy outside `desk/fixtures`.

1. `test_score_matrix_sums_to_one_and_reads_cs_off_column_zero`: for lambda 1.6, mu 1.1, rho minus 0.1 the matrix sums to 1 within 1e-12 and `p_clean_sheet` equals the column-0 sum.
2. `test_decay_weights_halve_at_the_half_life`: 400 days weighs 0.5 within 1e-9; 0 days weighs 1.
3. `test_analytic_gradient_matches_finite_differences`: on a 60-match synthetic window, central differences at 1e-5 relative on every coordinate.
4. `test_promoted_prior_on_the_committed_window`: the prior from `tests/fixtures/fixtures/matches_thru_2026-09-19.parquet` reads attack minus 0.354 and defence plus 0.276 within 0.02, 9 clubs, 3 seasons, and a club with 18 matches is excluded.
5. `test_fit_reproduces_the_2026_09_19_fit`: the parquet holds 1,566 rows; fitted at `2026-09-19T11:00:00Z` the fit converges under 10 s with intercept 0.186, home advantage 0.173, rho minus 0.094 within 0.01 and effective n within 2 of 528; a second run is byte-identical.
6. `test_fit_refuses_under_200_matches_and_a_leaked_result`: 150 matches raise `InsufficientHistory`; a finished match with kickoff at the fit instant raises `LeakageError`.
7. `test_population_ranks_and_away_is_harder`: 40 pairs, ranks 1 to 40 without gaps, and for every opponent the away pair's `attack_ease` is below the home pair's; the best-defence club has the lowest `defence` value.
8. `test_step_of_edges`: eases 0.36, 0.12, 0.119, 0, minus 0.119, minus 0.12, minus 0.36 map to 1, 2, 3, 3, 3, 4, 5 and null maps to null (the same fixture feeds the vitest in test 17).
9. `test_fixture_fit_writes_one_artefact_with_counts`: `desk run-task fixture_fit` on the seeded database lands `status ok`, `rows_written 1`, `items_seen 1566`, `items_done 20`, and `inputs` validates against `FixtureRatingsInputs`.
10. `test_fixture_fit_that_writes_nothing_lands_as_error`: with the artefact store patched to a no-op the run lands `status error`, note `wrote_nothing_unexplained` (the silent-nothing guard).
11. `test_fixture_fit_refusals_write_nothing`: `maxiter 1` and a 150-match window each land `status error` with their note and leave the artefact count unchanged.
12. `test_fixtures_board_empty_on_no_artefact`: on a database with fixtures and no artefact the envelope is `empty` with the reason "No fixture ratings yet." and the fix naming `fixture_fit`; the panel does not raise.
13. `test_fixtures_board_contract`: on the seeded database with the example params the result validates with `extra="forbid"`, `provenance.inputs` names `artefact`, `fixture`, `player_fixture` and `user_squad`, 20 clubs return, the envelope is under 100 KB, and the snapshot `contracts/panels/fixtures_board.json` matches (the schema contract test).
14. `test_fixtures_board_state_words`: a fit stamped 10 h ago reads `fresh`, 100 h `aging`, 200 h `stale` with numbers still served; `lens=attack` nulls every `defence_*` field and empties `verdict.defence`; `basis=club` nulls every `opponent_only`.
15. `test_fixtures_board_held_counts`: a `user_squad` row with three Man Utd picks gives `held 3` for team_code 1 and `held 0` elsewhere; no row gives null and the reason sentence.
16. `test_fixture_detail_club_basis_differs`: a strong club away at a promoted club has club-basis `p_clean_sheet` above the opponent-only value; `fit` carries the three globals equal to the payload; a blank gw returns `blank: true`.
17. `fdr.test.ts` (vitest): `stepOf` agrees with the fixture of test 8; the cell class derives from `opponent_only.attack_step` under `basis=club`.
18. `fixtures.spec.ts` (Playwright): at 1,280 and 390 in both themes, 1 panel call under 100 KB, layout shift 0, 20 rail rows, no horizontal page scroll at 390, a cell click sets `/fixtures/t/1/gw/9`, Escape restores it, no rendered string carries an em-dash or an hour-unit age.
19. `test_registry_walks_include_fixture_fit`: `test_jobs_walk`, `test_panels_walk` and `test_artefacts_walk` pass with the new rows; `docs/runbook.md` regenerates with no diff.
20. `feature_check.py` line `fixtures` (the deployed probe): with the check session, `GET /api/panels/fixtures_board?horizon=6` returns 20 clubs, `state` in `fresh` or `aging`, `fit.age_days * 24 < 168`; and the admin probe: Run-now on `fixture_fit` lands `status ok` within 2 minutes.

### 11. Definition of done

- [ ] Tests 1 to 19 green under `uv run pytest` and `npm test`; test 20 green in `deploy-check.yml` against `PUBLIC_URL`.
- [ ] CI green on main: ruff, mypy strict, pytest, the prose check, `tsc --noEmit`, eslint, `vite build`, `schema.d.ts` regenerated with no diff, contract snapshots with no unreviewed diff.
- [ ] Deployed to Railway: web and worker at the same `git_sha`; `/api/health` shows `alembic_head == expected_head` and `fixture_fit` absent from `never_ran`, `stale` and `failing`.
- [ ] Feature check green for S2 (`fixtures`, admin Run-now) and for every earlier line.
- [ ] Owner's demo, before the GW9 deadline: open `/fixtures`; read the verdict strip for GW9 to GW14; click `Defence`; click `8` and read GW9 to GW16; click the Man Utd GW9 cell; read p(CS), xG against and the three input ages in the drawer; click `Fit inputs` and read 0.186, 0.173, minus 0.094 and the fitted-at age; press Escape; open `Torn fixtures`; on a phone at 390 scroll the ticker sideways and confirm the page does not.
- [ ] Docs updated: `docs/SPEC.md` S2 block marked done with the deployed sha; `docs/runbook.md` regenerated with the `fixture_fit` row; `README.md` gains `desk run-task fixture_fit`; `docs/adr/0003-fixture-difficulty.md` records the constants table of section 7 and the opponent-only colour basis.
- [ ] No `TODO`, `FIXME` or `XXX` in code; no `xfail` added.
- [ ] Surface inventory in the PR: the old tab's controls, verdict chips, torn fixtures, club shape, calibration line, cell drawer and inputs inspector, each with its new location; the market odds block is the one deletion, cited to D10.

### 12. Out of scope

| Left out | Where it goes |
|---|---|
| Market odds per cell (the Odds API ladder, Shin de-vig, the 0.40 clean-sheet shading) | D10; `source` stays in the schema at `model` so a later step can add `market` without a contract change |
| Predicted lineups and their freshness row | D10 |
| The empirical calibration regression over 28,353 starts | no step; the model calibration is served |
| Backdated fits for backtests | offline parquet plus DuckDB per D3 |
| A promotion-route covariate for the prior | no step |
| Per-player fixture-adjusted xPts | S3 serves provider xPts, which already contain fixtures; S4 solves on the consensus |
| The Dashboard's fixture tile; the chat tools | S5 reads `fixtures_board`; S7 registers `panel_fixtures_board` and `panel_fixture_detail` |
| Retention of `fixture_ratings` rows | S5's `retention` job; S2 declares the 30-row rule |

### 13. Size and session plan

About 17 files outside tests, 2,300 lines; 10 test files, 900 lines; one parquet fixture of 1,566 rows, about 40 KB.

| Order | File | Lines |
|---|---|---|
| 1 | `desk/fixtures/scoreline.py` | 80 |
| 2 | `desk/fixtures/promoted.py` | 120 |
| 3 | `desk/fixtures/dixon_coles.py` | 260 |
| 4 | `tests/fixtures/fixtures/matches_thru_2026-09-19.parquet`, from `scripts/export_old_repo.py --matches --before 2026-09-19T11:00Z` (S1's script gains the flag) | data |
| 5 | `tests/unit/fixtures/test_scoreline.py`, `test_promoted.py`, `test_dixon_coles.py` | 260 |
| 6 | `desk/fixtures/difficulty.py` | 240 |
| 7 | `desk/fixtures/fit.py` (window read, club set, artefact write) | 120 |
| 8 | `desk/artefacts/kinds/fixture_ratings.py` (`ArtefactKind` row, both pydantic models, `state()`) | 90 |
| 9 | `desk/jobs/tasks/fixture_fit.py` (`main`, `--help`) | 60 |
| 10 | `desk/migrations/versions/0004_s2_artefact_kind_created.py` | 20 |
| 11 | `tests/unit/fixtures/test_difficulty.py`, `tests/unit/jobs/test_fixture_fit.py` | 200 |
| 12 | `desk/panels/fixtures_board.py`, `desk/panels/fixture_detail.py` | 400 |
| 13 | `tests/contract/test_fixtures_board.py`, `test_fixture_detail.py`, the two snapshots | 200 |
| 14 | `scripts/gen_types.sh` run; `web/src/charts/fdr.ts`, `fdr.test.ts` | 60 |
| 15 | `web/src/charts/FixtureTicker.tsx` | 200 |
| 16 | `web/src/screens/fixtures/FixturesScreen.tsx`, `controls.tsx`, `folds.tsx`, `FixtureDrawer.tsx` | 700 |
| 17 | `tests/e2e/fixtures.spec.ts` | 120 |
| 18 | `scripts/feature_check.py` (two lines), `docs/runbook.md`, `docs/SPEC.md`, `README.md`, `docs/adr/0003-fixture-difficulty.md` | 80 |

Where a session goes wrong, and the guard:

1. The sign of `defence`. It is leakiness: higher concedes more, so the best defence is the lowest value, `defence_ease` is `anchor minus xg_against`, and `rating.defence_rank` sorts ascending. Guard: test 7; the drawer's row labels say "concedes per game".
2. Naive datetimes. A `kickoff_at` without tzinfo, or `fitted_at` from `datetime.now()` without `UTC`, shifts every decay weight and the leak guard. Guard: `desk.core.time` refuses naive input; test 6 plants a match at the fit instant; the session is pinned to UTC.
3. Fitting inside the panel, or importing scipy there. A panel that fits is a model run inside a request budget. Guard: `test_scipy_confined`; the panel reads the newest artefact and `fixture` only; `test_panels_walk` expects `Empty` on an empty database, never a fit.
4. Colouring the club basis on the opponent-only domain, which clips about 13 percent of cells and turns a strong club's whole row green. Guard: test 17; the legend names the colour basis; `club` objects carry no `step` field, so the compiler refuses the mistake.


# Part 9. Step S3: score projections from providers plus consensus

Step 5 of 10 in `docs/SPEC.md` build order. Repo `fpl-desk`, package `desk`, frontend `web/`. Season `2026-27`. British spelling. Every decision this step takes where ARCH.md is silent carries the prefix DECISION.

### 1. Goal

When this step is done the owner sees one matrix of expected points per player per gameweek, computed as the unweighted mean of up to five providers, with the source count, the spread and the age of every source visible, and can upload his FPLReview export into it. He can also read, per provider and per settled gameweek, how far each source was from the real points, with the consensus scored on the same rows.

### 2. Depends on

| Step | What S3 uses |
|---|---|
| S0 | `job` table and the ledger helper `desk/jobs/runner.py`; `JOBS` registry row type with `due`, `stale_window`, `writes`, `read_by`, `budget_s`; the worker tick and the pipeline lane; `POST /api/admin/jobs/{task}/run`; `desk/db/pit.py` `latest_at(table, keys, t)`; `desk/db/bucket.py`; `raw_payload` table; `auth/policy.py` tiers; `scripts/feature_check.py`; `desk/core/time.py` (naive datetimes refused) |
| S1 | `event` (`deadline_at, is_next, finished`); `team` (`team_code, short_name, name`); `player` (`code, element_id, web_name, first_name, second_name, position, team_code`); `player_state` (`price_tenths, selected_by_pct, status, chance_next, ep_next`); `fixture` (`gw, home_team_code, away_team_code, finished`); `player_fixture` (`total_points, minutes, settled_at`); `user_squad.picks`; the `fpl_poll` job and the bootstrap parse; `fpl_settle` as a chain parent; the shared httpx client |
| S8 | tokens; `DataTable`, `FilterRow`, `RowCount`, `Drawer`, `Breadcrumb`, `StateBox`, `PlayerCell`, `ClubMark`, `Age`, `Number`, `CiteChip`, `Fold`, `ConfirmButton`, `Sparkline`; `usePanel` and the `schema.d.ts` CI diff; the Admin shell with the jobs board; panels `me` and `me_squad`; `/api/assets/` |

If S1 already created the `projection` table (ARCH section 5 lists `projection (fpl_ep)` among `fpl_poll`'s writes), the S3 migration adds only the columns and indexes of section 4.1 that are missing, with `ADD COLUMN IF NOT EXISTS`. If it did not, the migration creates the table. Both paths end at the same DDL and `test_projection_ddl` asserts the column set.

### 3. User stories

User role:

1. I open `/projections` and see every player with the consensus xPts for the next 5 gameweeks, sorted by the 5-GW sum. Result: 600 or more rows, a 3 px dot on every cell whose `n_sources` is 1, and the row count "662 of 662".
2. I switch the chips to GW8 to GW12 and untick `airsenal`. Result: one request, the columns re-label, the sum and spread recompute, the URL carries `gws=8,9,10,11,12&providers=fplform,fpl_ep,fplreview,solio`.
3. I set position DEF, team Arsenal, max price 6.0 and type "gab". Result: no request; 1 row; "1 of 662".
4. I tick "My squad". Result: no request; 15 rows; "15 of 662".
5. I click Gabriel. Result: the drawer opens at `/projections/p/223094` with the provider pivot (one row per provider plus consensus, one column per selected GW), each provider's age in days, and the price sparkline.
6. I click the `fplform` row in the pivot. Result: level 3 at `/projections/p/223094/s/fplform` shows `as_of`, `xp`, `xmins`, `p_appear` per GW, the licence sentence and the cadence.
7. I open the fold "Provider accuracy". Result: MAE per provider per settled GW with `n`, the consensus row, and `fpl_ep` beside it.
8. After GW6 settles I look at the GW6 column. Result: the actual points in bold with a signed delta against the consensus.
9. On a phone I open the same screen. Result: 3 GW columns, a sticky first column, no page-level horizontal scroll, Own%, Spread and P(app) in the row drawer.

Admin role:

10. I drop `fplreview_1789744594.csv` on the Admin upload card and press Preview. Result: "675 rows, 656 resolved, 19 unresolved, GW5 to GW14, as_of 2026-09-18 15:16 UTC" and the 19 names; nothing is written to `projection`.
11. I press "Import 656 players". Result: a job row `provider_import_upload` with `rows_written 6560`, and a `fplreview` chip on `/projections` showing its age.
12. I drop the same file again. Result: the preview says "already imported on 2026-09-18, sha 3f1a..., 0 new rows" and the Import button is disabled.
13. I read the Providers table on Admin and press Run now on `provider_pull_fplform`. Result: one row per provider with last run status, rows written, rows unchanged, newest `as_of` age, players covered at the next GW and any position-mean warning; a new job row lands within 2 minutes with a non-zero `items_seen`.

### 4. Data

#### 4.1 `projection` (series, append only)

Why: one long table holding every provider's opinion with the instant it became observable, so a consensus and a track record can both be read at any past instant.

| Column | Type | Rule |
|---|---|---|
| `provider` | text NOT NULL | a key in `PROVIDERS` |
| `season` | text NOT NULL | `2026-27` |
| `gw` | smallint NOT NULL | 1 to 38, CHECK |
| `code` | integer NOT NULL | FPL cross-season code |
| `as_of` | timestamptz NOT NULL | fetch instant; upload epoch; bootstrap fetch instant |
| `xp` | double precision NOT NULL | appearance-weighted expected points |
| `xp_if_appears` | double precision NULL | conditional points where published (fplform only) |
| `xmins` | double precision NULL | expected minutes; never derived from `p_appear` |
| `p_appear` | double precision NULL | 0 to 1; never derived from `xmins`; a null chance stays null |
| `file_sha256` | text NOT NULL | sha256 of the bytes that produced the row |
| `job_id` | bigint NULL | the job row that wrote it |

Primary key `(provider, season, gw, code, as_of)`. Index `projection_read_idx (season, gw, code, as_of DESC)`. CHECK `xp >= 0`, `p_appear BETWEEN 0 AND 1`, `xmins >= 0`. Every read goes through `latest_at`; `test_pit` fails on any other `SELECT`.

Sample row: `('fplreview', '2026-27', 5, 154561, '2026-09-18T15:16:34Z', 3.98, NULL, 94.0, NULL, '3f1a...', 812)`.

#### 4.2 `provider_score` (current row)

Why: the measured error of every provider and of the consensus per settled gameweek, so the accuracy strip and any later weighting read one table.

| Column | Type | Rule |
|---|---|---|
| `provider` | text NOT NULL | a provider key or the literal `consensus` |
| `season` | text NOT NULL | |
| `gw` | smallint NOT NULL | |
| `scope` | text NOT NULL | one of `overall`, `pos:GKP`, `pos:DEF`, `pos:MID`, `pos:FWD`, `own_gt5`, `own_gt20`, `p_appear` |
| `metric` | text NOT NULL | `mae`, `rmse` or `brier` |
| `value` | double precision NOT NULL | |
| `baseline` | double precision NOT NULL | the consensus on the same observations |
| `n_obs` | integer NOT NULL | |
| `deadline_at` | timestamptz NOT NULL | the instant the scored rows were read at |
| `updated_at` | timestamptz NOT NULL | |

Primary key `(provider, season, gw, scope, metric)`. Upsert on re-run; a changed value updates the row (incident `c789453`: a first verdict must never freeze).

Sample row: `('fplform', '2026-27', 3, 'overall', 'mae', 1.115, 1.181, 652, '2026-09-13T10:00:00Z', '2026-09-15T10:31:02Z')`.

#### 4.3 `upload` (current row)

Why: the ledger of hand-dropped member exports, one row per file, so a preview can be committed later and a re-drop is recognised.

| Column | Type | Rule |
|---|---|---|
| `upload_id` | bigint identity PK | |
| `user_id` | bigint NOT NULL | uploader, FK `app_user` |
| `provider` | text NOT NULL | `fplreview` in v1 |
| `filename` | text NOT NULL | as dropped |
| `sha256` | text NOT NULL | unique per provider: `UNIQUE (provider, sha256)` |
| `bucket_key` | text NOT NULL | `uploads/{user_id}/{sha256}.csv` |
| `as_of` | timestamptz NOT NULL | the epoch in the filename |
| `status` | text NOT NULL | `preview`, `committed`, `discarded`, `refused` |
| `rows_total` | integer NOT NULL | file rows |
| `rows_resolved` | integer NOT NULL | |
| `unresolved` | jsonb NOT NULL | `[{"key": 671, "name": "Youth GK", "reason": "element_id not in player for 2026-27"}]` |
| `summary` | jsonb NOT NULL | see below |
| `job_id` | bigint NULL | set on commit |
| `created_at`, `committed_at` | timestamptz | |

`summary` shape: `{"gw_min": 5, "gw_max": 14, "position_means": {"GKP": [1.92, 1.88], "DEF": [2.31, 2.40], "MID": [2.55, 2.49], "FWD": [2.70, 2.61]}, "warnings": []}`, each pair being `[provider, consensus_of_others]` for the first GW in the file. A known filename with a different sha lands as `refused`, warning "same filename, different bytes; re-export under a new epoch".

#### 4.4 The provider registry (code, `desk/providers/base.py`)

`Provider` is a frozen dataclass: `key`, `name`, `source` (`pull`, `poll`, `upload`), `key_kind` (`code`, `element_id`, `name`), `licence` (one sentence), `attribution` (text or None), `cadence_h` (expected hours between snapshots), `stale_h`, `consensus_member`, `private`, `retired_on` (date or None), `fetch()` (pull only), `parse(body: bytes, ctx) -> Frame`. Five rows:

| key | source | key_kind | cadence_h / stale_h | consensus_member | private | attribution |
|---|---|---|---|---|---|---|
| `fplform` | pull, `POST https://fplform.com/export-fpl-form-data.php` | element_id | 12 / 36 | true | false | none; licence forbids republishing the file |
| `fpl_ep` | poll, from the bootstrap body `fpl_poll` already fetches | code | 6 / 36 | true | false | none |
| `airsenal` | pull, `https://raw.githubusercontent.com/mcnuggets651/fpl-apex/main/data/generated/airsenal.csv` | element_id | 24 / 168 | true | false | "AIrsenal via fpl-apex, MIT" |
| `fplreview` | upload | element_id | 168 / 240 | true | true | none |
| `solio` | pull, `https://fpl.solioanalytics.com/api/data/latest.json` | name | 4 / 24 | true | false | "Solio Analytics", required by the publisher |

`private: true` means per-player values are shown to admins only; the value still enters the consensus and `n_sources` (DECISION: an unweighted mean is a derived aggregate; the pivot row reads "member export, values shown to the uploader only" for users). `consensus_member` is the one-row switch for the R2 finding on `fpl_ep`; it ships `true` per D5.

Refresh: fplform 08:00, 20:00 and T-4h; airsenal 09:00; solio every 4 hours; fpl_ep on every `fpl_poll`; fplreview when the owner drops a file (once so far).

Sample fixtures under `tests/fixtures/providers/<key>/`: `fplform/sample.csv` (12 synthetic rows under the real header `ID,Name,Team,Pos,Price,1_pts_no_prob,1_prob,1_with_prob,...`; the licence forbids re-sharing a real file), `fpl_ep/bootstrap.json` (the S1 fixture trimmed to 40 elements), `airsenal/sample.csv` (header `player_id,gw,xp,generated_at,source_version,prediction_tag`, 24 rows), `fplreview/fplreview_1789744594.csv` (the 676-line GW6 export from ARCH section 12), `solio/latest.json` (captured with `curl` on the first day of the build). DECISION: the S0 grep test that refuses ISO dates under `tests/fixtures/` exempts archived upstream bodies under `providers/` and `fpl/`; tests over them pass `now` explicitly.

Point-in-time rules: a read at `t` takes each provider's latest row per `(gw, code)` with `as_of <= t`. Scoring reads at `t = event.deadline_at`; the screen at `now()`. An upload's `as_of` is the filename epoch, never the drop instant.

### 5. Jobs

All five run on the pipeline lane with no metered API: cost cap none, `usd` 0. Each provider pull is its own registry row so the unique index on `job (task, due_at)` holds and each has its own stale window.

| Job | Trigger | Inputs | Writes | Budget / timeout | Stale window |
|---|---|---|---|---|---|
| `provider_pull_fplform` | cron `0 8,20 * * *` plus `DeadlineRelative(-4h)` | next GW from `event`; `first_gw = next`, `last_gw = min(next + 7, 38)` | `projection`, `raw_payload` | 120 s | 36 h |
| `provider_pull_airsenal` | cron `0 9 * * *` | none | `projection`, `raw_payload` | 60 s | 168 h |
| `provider_pull_solio` | cron `0 */4 * * *` | none | `projection`, `raw_payload` | 30 s | 24 h |
| `provider_import_upload` | `OnDemand`, enqueued by the commit route with `trigger = admin`, `params {"upload_id": n}` | the `upload` row and the bucket file | `projection`, `upload` | 120 s | 240 h |
| `provider_score` | `After("fpl_settle")`, also Run-now | `params {"season": "2026-27"}` | `provider_score` | 300 s | measured against next due, 48 h after the settle slot |

`fpl_ep` is not a job. S3 adds one call inside `fpl_poll` after the bootstrap parse: `importer.import_frame(conn, PROVIDERS["fpl_ep"], fpl_ep.parse(body, ctx), as_of=fetched_at, sha=body_sha, job_id=job_id)`. `fpl_poll`'s `writes` already names `projection`. If S1 wrote `fpl_ep` rows through its own code, S3 replaces that path with the provider module and `test_fpl_ep_written_by_poll` proves rows land from the fixture body.

Ledger row per job: `items_seen` (rows parsed), `items_done` (rows resolved), `items_skipped` by reason (`unresolved`, `out_of_range`, `duplicate_key`, `tba_column`, `ambiguous`), `rows_written`, `rows_unchanged`, `note`, `log_tail`.

Failure behaviour, all five: a transport error or a parse refusal lands as `status error` with the exception class and the first 200 bytes of the body in `note`. A body whose sha256 equals the newest `raw_payload` row for that provider lands as `nothing_to_do`, note `body sha unchanged since <as_of>`, and parse is skipped. A robots disallow on the fplform export path lands as `refused` with the robots line quoted (`https://fplform.com/robots.txt` is read once per run). Rows parsed with 0 written and no skip reason land as `error`, note `wrote_nothing_unexplained`, by the S0 helper.

If it silently did nothing, what changes on a screen:

| Job | Visible difference |
|---|---|
| `provider_pull_fplform` | the `fplform` chip age passes 1 day and the chip is `stale` at 36 h; the feature check line `projections` fails on "newest fplform as_of under 36 h" |
| `provider_pull_airsenal` | `n_sources` drops by 1 from GW7 onward and the dot count on the matrix rises |
| `provider_pull_solio` | the `solio` chip age exceeds 4 h; stale at 24 h |
| `provider_import_upload` | the `upload` row stays `preview`, the Import button stays enabled, no `fplreview` chip appears |
| `provider_score` | the accuracy fold reads "0 of N settled gameweeks scored"; `admin_jobs` lists `provider_score` under `stale` 48 h after the settle |

### 6. API and panels

Every panel is one `Panel` row with pydantic `params` and `result`, both `extra="forbid"`, in the S0 envelope `{ok, panel, result, empty, provenance}`, served at `GET /api/panels/{name}`. Budgets: 1,500 ms for `projections_table`, 800 ms for the other two. All three session-tier panels become chat tools `panel_<name>` in S7; the `headline` for `projections_table` is the top 25 rows by `sum`.

#### 6.1 `projections_table`

Params:

```
{ gws: int[] (1 to 8 items, each 1..38; default: next GW and the 4 after),
  providers: string[] (keys; default: all rows with retired_on null),
  position: "GKP"|"DEF"|"MID"|"FWD"|null, team_code: int|null,
  max_price_tenths: int|null, min_p_appear: float 0..1 default 0,
  squad_only: bool default false, q: string max 40 default "" }
```

Result:

```
{ season, gws: [6,7,8,9,10], next_gw: 6,
  rows: [ { code, web_name, position, team_code, price_tenths, selected_by_pct,
            cells: [ [mean|null, n_sources, min|null, max|null, actual|null] ... one per gw ],
            sum, sum_gws, spread|null, p_appear|null, in_squad } ],
  providers_meta: [ { key, name, consensus_member, private, retired_on|null,
                      as_of|null, age_days|null, state: "fresh"|"stale"|"missing",
                      n_players_next_gw, attribution|null } ],
  settled_gws: [5] }
```

`cells` is an array of 5-element arrays so 662 rows by 8 GWs stays under 250 KB (estimate 205 KB). The filter params are honoured server side for chat and deep links; the screen sends only `gws` and `providers` and filters client side. `squad_only` with no `entry_id` returns `empty {reason: "no team id on this account", fix: "set it on Account"}`. Provenance `inputs`: one `{kind: "projection", id: <provider>, as_of}` per provider present, `{kind: "player_state", id: season, as_of}`, and one `{kind: "player_fixture", id: "2026-27:gw5", as_of: max settled_at}` per settled GW in `gws`.

#### 6.2 `player_detail`

Params `{ code: int, gws: int[] (same bounds; same default) }`. Result:

```
{ player: { code, web_name, first_name, second_name, position, team_code, team_short,
            price_tenths, selected_by_pct, status, chance_next, news },
  gws, pivot: [ { provider, name, private, values_visible: bool,
                  as_of|null, age_days|null,
                  cells: [ { gw, xp|null, xmins|null, p_appear|null } ] } ],
  consensus: [ { gw, mean|null, n_sources, min|null, max|null, sd|null, actual|null } ],
  price_series: [ [as_of, price_tenths] ... last 60 days ],
  snippets: [] }
```

`values_visible` is false for a `private` provider when `ctx.role != "admin"`, and its `cells` carry nulls. `snippets` is empty until S9. Provenance as above plus `{kind: "player_state", id: code, as_of}`.

#### 6.3 `provider_accuracy`

Params `{ season: string default current, scope: string default "overall" }`. Result:

```
{ season, scope, metric: "mae"|"brier", gws_scored: [1,2,3,4],
  rows: [ { provider, name, consensus_member,
            per_gw: [ { gw, value|null, n|null } ],
            pooled: { value|null, n, gws } } ],
  consensus_row: { per_gw: [...], pooled: {...} },
  note: "pre-deadline fetch only, players whose team played, 0 for no appearance" }
```

For `scope: "p_appear"` the metric is `brier`. Provenance: `{kind: "provider_score", id: season, as_of: max updated_at}`.

#### 6.4 `admin_providers` (admin tier, DECISION: added to the ARCH admin panel list)

Params none. Result: `rows: [ { key, name, source, licence, cadence_h, consensus_member, private, retired_on, last_job: { job_id, status, finished_at, items_seen, rows_written, rows_unchanged, note }|null, newest_as_of|null, age_days|null, state, n_players_next_gw, warnings: string[] } ]`. `warnings` carries the position-mean text from the newest run.

#### 6.5 Upload routes (admin tier, CSRF, not panels)

| Method, path | Body | Result | Errors |
|---|---|---|---|
| `POST /api/admin/uploads` | multipart `file`, form `provider=fplreview` | the `upload` row as JSON plus `preview: { rows_total, rows_resolved, unresolved: [...], gw_min, gw_max, as_of, position_means, warnings, already_imported: bool }` | 400 `{error: "upload_refused", detail}` for a bad filename, a bad epoch, a missing column pair, over 5 percent unresolved, or a file over 2 MB; 409 `{error: "filename_reused", detail}` for the same name with different bytes |
| `POST /api/admin/uploads/{upload_id}/commit` | none | `{ job_id }` | 409 `{error: "upload_state", status}` when not `preview` |
| `POST /api/admin/uploads/{upload_id}/discard` | none | `{ status: "discarded" }` | 409 as above |
| `GET /api/admin/uploads` | none | `{ rows: [upload rows, newest 20] }` | |

The preview runs in web: parse, resolve, summarise, store the file in the bucket, insert the `upload` row; nothing is written to `projection`. The commit inserts a `job` row and the worker writes the rows, so the ledger counts them. The 676-row sample parses in under 2 s against the 10 s route budget. A file body never appears in a log line.

### 7. Algorithms

#### 7.1 Name normalisation (`desk/providers/names.py`, lifted from `fpl_edge/ingest/player_mapping.py` lines 100 to 135)

NFKD, strip combining marks, translate the letters NFKD leaves alone (`Ø→O, ø→o, Đ→D, đ→d, Ł→L, ł→l, Æ→AE, æ→ae, Œ→OE, œ→oe, ß→ss, Þ→Th, þ→th, Ð→D, ð→d, ı→i`), lower, replace non-alphanumerics with a space, collapse spaces. "Ødegaard" normalises to "odegaard"; without the table it became "degaard" and matched nothing. Never edit distance (R6 rule 7).

#### 7.2 Key resolution

`element_id` providers resolve through `player (season, element_id) -> code`. DECISION: the current `player` row is the mapping; FPL appends new element ids within a season and does not reuse them. An id absent from `player` is dropped and counted as `unresolved` with the provider's name string beside it.

`name` providers (solio) resolve by `(normalised name, team)`: the name equals the normalised `web_name`, `second_name` or `first_name second_name`; the team equals `team.short_name` case-insensitively, or the normalised `team.name` contains the provider's team string. Two candidates left are split by `price_tenths` equal to the provider's price; still ambiguous is dropped and counted `ambiguous`. The captured `solio/latest.json` shows the team string format; if over 5 percent of rows fail the team match the parse refuses and names the first three team strings.

`code` providers (fpl_ep) check the code exists in `player` for the season; an unknown code is dropped and counted.

#### 7.3 Validation (`desk/providers/validate.py`), the nine R2 checks plus one

| # | Check | Outcome |
|---|---|---|
| 1 | Upload filename matches `^fplreview_(?P<epoch>\d{9,10})\.csv$`, epoch in [2020-01-01, 2040-01-01) | else 400; same filename with a different sha is 409 |
| 2 | Wide headers: fplreview `^(\d{1,2})_(xMins\|Pts)$`, fplform `^(?:tba\|(\d+))_(pts_no_prob\|prob\|with_prob)$`; `Pts` without `xMins` keeps `xmins` null; no pair at all refuses the frame; `tba_*` columns are dropped and counted |
| 3 | Unresolved rows dropped and counted; over 5 percent unresolved refuses the frame (GW6 sample: 19 of 675, 2.8 percent) |
| 4 | `season` is an argument of every parse; nothing infers it from ids |
| 5 | A null `gw` or `code` after resolution refuses the frame; the importer builds rows from `itertuples`, never from aligned Series (the old fplform NOT NULL failure) |
| 6 | Per row `0 <= xp <= 25 * k`, `0 <= xmins <= 96 * k`, `0 <= p_appear <= 1`, with `k = max(1, fixtures of the player's team in that gw)` (DECISION: a double gameweek doubles the bound; FPLReview sums both matches into one `xMins` cell). Out of range is dropped and counted `out_of_range`, never clipped; over 1 percent refuses the frame |
| 7 | A provider declaring a `horizon_column` must have its first horizon value within 0.011 of the single-GW column; no v1 provider declares one; tested on a synthetic provider |
| 8 | Position-mean warning, 7.7 |
| 9 | Each provider is its own job row; a failure is one `error` row and the others are unaffected |
| 10 | Duplicate `(gw, code)` in one frame with differing values: both dropped and counted `duplicate_key` |

#### 7.4 The importer and write-on-change (`desk/providers/importer.py`, the one writer)

```
import_frame(conn, provider, frame, as_of, sha, job_id) -> ImportResult
  validate(frame) per 7.3
  latest = latest_at("projection", {season, provider, gw in frame.gws}, t=as_of)   # one query
  for row in frame: 
     prev = latest.get((gw, code))
     if prev and equal(prev, row, tol=1e-9) on (xp, xp_if_appears, xmins, p_appear): unchanged += 1; continue
     insert (provider, season, gw, code, as_of, ..., sha, job_id); written += 1
  return ImportResult(written, unchanged, skipped: dict, warnings: list)
```

A row equal to the provider's latest row for that key is dropped and counted, so 13 identical fplform pulls write 0 rows (the old repo wrote 60,000 duplicates). A body sha equal to the provider's newest `raw_payload.sha256` short-circuits to `nothing_to_do` before parse. The insert is `INSERT ... ON CONFLICT DO NOTHING` in batches of 5,000; the helper's before-and-after `count(*)` is the ledger's `rows_written`, and a test asserts the importer's own count equals it.

#### 7.5 Consensus (`desk/providers/consensus.py`)

One statement over the subquery `latest_at("projection", {season, gw = ANY(gws), provider = ANY(members)}, t)`, where `members` is every registry key with `retired_on IS NULL OR retired_on > t::date` and `consensus_member` true, intersected with the caller's `providers` param:

```
SELECT gw, code,
       count(xp)                 AS n_sources,
       avg(xp)                   AS mean,
       min(xp) AS min, max(xp) AS max,
       max(xp) - min(xp)         AS spread,
       stddev_samp(xp)           AS sd,
       avg(xmins)                AS xmins_mean,
       avg(p_appear)             AS p_appear_mean, count(p_appear) AS n_p_appear
FROM latest WHERE xp IS NOT NULL
GROUP BY gw, code
```

`latest_at` returns the `DISTINCT ON (provider, gw, code) ... ORDER BY provider, gw, code, as_of DESC` subquery; if S0's `latest_at` returns rows instead, the same aggregate runs in pandas. A pandas twin `consensus_py(rows)` lives beside it and `test_consensus_twin` pins the two equal on a fixture with a retired provider, a non-member, a provider missing one player, a null `xmins`, and a newer row after `t`.

Derived per row for the table: `sum = Σ mean over gws with a value`, `sum_gws = count`; `spread = mean of per-GW (max - min) over GWs with n_sources >= 2`, null when none (DECISION); `p_appear = p_appear_mean` for the first GW in `gws`, null when no member published one (never derived from `xmins`); `actual = Σ total_points` from `player_fixture` for `(season, code, gw)` where the GW is settled, else null.

Measured expectation at GW6 (R2): 3.92 sources per player on average; at GW12 to 14 FPLReview alone, so the dot shows on every cell there.

#### 7.6 Provider scoring (`desk/providers/scoring.py`, lifted from `fpl_edge/eval/projection_scoring.py` and moved to SQL over Postgres)

For each settled `gw` of the season (every fixture of the GW has `player_fixture` rows with `settled_at` set):

1. `T = event.deadline_at`. `P = latest_at("projection", {season, gw}, T)` over all providers, members or not, so `fpl_ep` is scored.
2. Actuals: `A(code) = Σ total_points, M(code) = Σ minutes` from `player_fixture` for the GW.
3. Universe `U`: `player` rows whose `team_code` is home or away in a fixture of the GW; `own(code) = latest_at("player_state", {season, code}, T).selected_by_pct`. Players at clubs with no fixture are excluded. `A` and `M` are 0 for a universe player with no row.
4. Baseline `C(code)` = mean `xp` over providers in `P` with `consensus_member` true and `xp` not null, on the same rows the screen would have served at `T`.
5. Per provider `p`, per scope: `obs = P[p] ⋈ U`; `mae = mean |xp - A|`, `rmse = sqrt(mean (xp - A)^2)`, `n_obs`; `baseline` is the same formula over `C` on the same `obs`. Scopes `pos:*` split on `player.position`; `own_gt5` and `own_gt20` on `own`; `p_appear` uses `brier = mean (p_appear - [M > 0])^2` with the baseline over the members' mean `p_appear`.
6. One extra provider row `consensus`: `value = mae` of `C` against `A` over every code with a `C`; `baseline = value`.
7. Upsert every row keyed `(provider, season, gw, scope, metric)`; a value changed by more than 1e-9 counts as written, an equal value as unchanged.

A provider with no row in `P` for the GW gets no score for it. A GW not settled is skipped with the reason in `items_skipped`. `N_OBS_FLOOR = 200` is stored for the v2 weighting and not applied in v1. Validation: on the migrated 2026-27 rows the job reproduces the R2 GW1 to GW4 overall MAE table (fplform 1.413, 1.214, 1.115, 1.214; fpl_ep 1.623, 1.464, 1.295, 1.245) within 0.005, since the input rows and deadline instants are the same.

#### 7.7 Position-mean warning (check 8)

After any import for provider `p`, take the first GW in the frame. `m_p(pos)` = mean `xp` of `p` over resolved rows in position `pos`; `m_c(pos)` = the other members' consensus at `as_of` over the same codes. Run only when 2 or more other members cover 200 or more of those codes; else record `position_check_skipped: <n>`. When `|m_p / m_c - 1| > 0.25` for any position, append "position mean check: MID 1.97 vs 1.44 (+37 percent)" to the run's `note`, to `upload.summary.warnings`, and to `admin_providers.warnings`. A warning never blocks a write. The old engine ran 37 to 71 percent hot and would have tripped it.

#### 7.8 Provider freshness state

`age_h = now - newest as_of` for the provider in the season. `state = "missing"` with no rows, `"stale"` when `age_h >= stale_h`, else `"fresh"`. The chip always prints the age in days through `Age`; the state changes the chip's dot only, with the word in the tooltip.

#### 7.9 Provider parse details

| Provider | Parse |
|---|---|
| fplform | POST form `firstgw, lastgw, all=1, submit=submit`, the shared `User-Agent`, timeout 90 s, 3 s sleep first; `N_with_prob -> xp`, `N_pts_no_prob -> xp_if_appears`, `N_prob -> p_appear`, `xmins` null; `ID` is element_id; `as_of` is the response instant; body archived as `raw/fplform/{date}/{sha}.csv.gz` |
| fpl_ep | bootstrap elements with `element_type` 1 to 4 and a non-empty `ep_next`; `gw` = the event with `is_next` (none refuses the frame, reason "between seasons"); `xp = float(ep_next)`; `p_appear = chance/100` when `chance_of_playing_next_round` is set, `1.0` when it is null and `status == 'a'`, else null; `code` from the element |
| airsenal | GET the raw CSV; keep the newest `prediction_tag` by `generated_at`; `as_of = max(generated_at)` of that tag; `player_id` is element_id; `xp` only |
| fplreview | CSV with `utf-8-sig`; pairs from check 2; `Pts -> xp`, `xMins -> xmins`; `BV`, `SV`, `Elite%` not stored; `as_of` from the epoch |
| solio | JSON; rows from the eight top lists (`topProjected` to `topDefCon`) deduplicated on `(name, team)`; `gw = gameweek`; `xp = prPoints`; `as_of = generatedAt`; `price` (tenths) is the tie-breaker of 7.2; other fields not stored |

### 8. UI

#### 8.1 `/projections`, the matrix

Primary visual: one `DataTable`, 13 columns at 1,280 px: Player (sticky, `PlayerCell`), Pos, Team (`ClubMark`), £, Own%, five GW columns, Sum, Spread, P(app). Every header is the column name only (`GW6`); the source count lives in the cell title and the dot. Each GW column is 72 px. Cell: the mean to one decimal in mono, right aligned; a sequential tint on series colour `#2a78d6` at alpha 0.08 to 0.55 over 0 to 8 xPts (DECISION); a 3 px `warn` dot at the top right when `n_sources <= 1`; the title "fplform 4.1, fplreview 3.9, fpl_ep 4.4, min 3.9, max 4.4" (private sources by name only for users). A settled GW shows `actual` in weight 600 with the signed delta in muted ink, "7 (+2.9)", no colour. Null renders as a short dash.

Controls above the table, one row: GW chips (14 from the next GW, 5 on by default, 1 to 8 selectable) and provider chips (one per non-retired provider, all on by default, label "fplform · 1 d" through `Age`, a dot for `stale` or `missing`; the `solio` tooltip reads "Solio Analytics, top lists only"). DECISION: GW and provider chips are query controls that change the computed numbers, so a change refetches; the filter row and every sort never do, which is what R8 rule 13 measures.

`FilterRow`: Position (All, GKP, DEF, MID, FWD; default All), Team (select of 20 `short_name`; default All), Max £ (default empty), Min P(app) (0 to 1 step 0.05, default 0), Search (placeholder "Player", matches `web_name` and `second_name`), My squad (toggle, default off, disabled with the tooltip "Set your team id on Account" when `me.entry_id` is null). `RowCount` under it: "662 of 662". Default sort Sum descending; every header sorts with `aria-sort`, numbers descending first, text ascending first. URL state: `?gws=6,7,8,9,10&providers=...&pos=DEF&team=3&maxp=60&minapp=0.5&q=gab&squad=1&sort=sum:desc`.

Drill-down: a row click opens the `Drawer` at `/projections/p/{code}` (level 2); a provider row click inside it opens level 3 at `/projections/p/{code}/s/{provider}` with `Breadcrumb` "Projections / Gabriel / fplform". Under the table, one `Fold` with the summary "Provider accuracy: fplform leads on MAE over 4 scored gameweeks" (or "Provider accuracy: 0 gameweeks scored yet"); opening it calls `provider_accuracy` once and renders 8.3.

Mount: 2 panel calls (`projections_table`, and `me_squad` only when `me.entry_id` is set); under 250 KB; data within 1,500 ms on the seeded database.

States: empty, "No projections for GW6 yet. The next fplform pull is due at 20:00 UTC. An admin can run it now from Admin."; skeleton at 20 rows and 13 columns, so layout shift is 0; error, "projections_table failed: <class>." with a "Retry" button; gap, a settled GW with no actual for a row shows the dash with the title "no settled row".

Mobile at 390 px: 3 GW columns (the first three selected), Player sticky at 120 px, the table scrolls horizontally inside its own box and the page does not; Own%, Spread and P(app) become `secondary` columns in the row drawer; the filter row collapses to one "Filters (2)" button opening a Sheet; the chips scroll in one line.

Copy: buttons "Retry", "Filters", "Clear filters"; chip tooltips "Included in the consensus" and "Reference only, excluded from the mean" (the latter only when `consensus_member` is off); Sum header tooltip "Sum of the consensus over the selected gameweeks; a dot means fewer gameweeks were covered than selected".

#### 8.2 `/projections/p/{code}`, the player drawer (level 2)

Header: `PlayerCell` at 64 px, price, ownership, status and news when present, a `CiteChip` naming `player_detail`. Primary content: the pivot table, one row per provider plus a bold `Consensus` row, one column per selected GW, cell = `xp` to one decimal with `xmins` and `p_appear` in the title, an `Age` per provider row; a private row for a user reads "member export, values shown to the uploader only". Under it the `Sparkline` of `price_series` (60 days) and the actuals row for settled GWs. `snippets` renders nothing in S3. Level 3 replaces the body with that provider's per-GW `xp`, `xmins`, `p_appear`, `as_of`, the licence sentence, the cadence, the attribution, and for admins a link to `/admin/jobs/{job_id}`.

#### 8.3 The accuracy strip (inside the fold)

A `DataTable`: rows = providers plus `Consensus`; columns = one per scored GW then "Pooled"; cell = MAE to three decimals with `n` in the title; a scope select (Overall, GKP, DEF, MID, FWD, Owned over 5 percent, Owned over 20 percent, P(appear) Brier; default Overall). The best value per column is weight 700. Note line: "Pre-deadline fetch only. Players whose team played, 0 points for no appearance. Consensus = unweighted mean of members." Empty: "No settled gameweek scored yet. Scoring runs after each settlement."

#### 8.4 Shared components used and added

Used: every S8 component named in section 2 plus shadcn `Sheet`, `Select`, `Toggle Group`, `Tooltip`. Added under `web/src/components/`: `ChipRow` (a scrolling row of toggle chips with an optional `Age` and state dot per chip; reused by Creators in S9) and `HeatCell` (the tinted mono cell with the thin dot; reused by the solver's per-GW ledger in S4). A second implementation of either is a test failure.

### 9. Admin view

On `/admin`, below the jobs board, two new blocks.

Upload card (`UploadCard.tsx`): copy "Drop the FPLReview export here. Name it fplreview_<unix epoch>.csv; the epoch is the export instant and becomes the as_of of every row. The file stays private to this app and is never republished." A file input and a "Preview" button. After preview: "675 rows, 656 resolved, 19 unresolved, GW5 to GW14, as_of 2026-09-18 15:16 UTC", a closed `Fold` "19 unresolved" with the names, the position-mean pairs in a 4-row table, any warning in amber, and two buttons: "Import 656 players" (`ConfirmButton`, "Write 6,560 rows under as_of 2026-09-18 15:16 UTC?") and "Discard". A repeated file shows "Already imported on 18 Sep, sha 3f1a...; 0 new rows" with Import disabled. After Import: "Job 812 queued" linking to the job row. Under the card, the newest 20 uploads: filename, as_of, status, resolved, job.

Providers table (`ProvidersTable.tsx`, from `admin_providers`): columns Provider, Source, Member, Last run (status chip, `Age`), Seen, Written, Unchanged, Newest as_of (`Age`), Players next GW, Warnings, and a "Run now" button per pulled provider calling `POST /api/admin/jobs/provider_pull_<key>/run` (no confirm; nothing is metered). A `stale` row shows the amber dot with the age; `airsenal` is expected to sit there until its upstream publishes again.

A user must never see: the upload card, any filename or sha, the unresolved names, the job log tail, per-player values of a `private` provider, or another user's `entry_id`. The routes matrix test covers the three upload routes and `admin_providers`.

### 10. Acceptance tests

In the order to write them.

1. `test_projection_ddl`: the migration leaves `projection`, `provider_score`, `upload` with exactly the columns of section 4 whether or not S1 created `projection`.
2. `test_providers_walk_five_rows`: every `PROVIDERS` row has the fields of 4.4 and a sample under `tests/fixtures/providers/<key>/`; `parse()` on the sample yields over 0 records with `xp` in [0, 25], `xmins` in [0, 96], `p_appear` in [0, 1].
3. `test_fplreview_gw6_sample_resolves_656_drops_19`: the committed export resolves 656 of 675 rows against the seeded `player` table and names the 19 unresolved.
4. `test_upload_epoch_and_filename_rules`: no epoch, a millisecond epoch, an epoch in 2019 and a reused filename with new bytes are each refused with the section 7.3 reason; check 1.
5. `test_header_pairs_and_tba_columns`: a frame with no `<gw>_Pts` refuses; `tba_*` columns are dropped and counted; check 2.
6. `test_unresolved_over_5_percent_refuses_frame`: 40 of 675 unresolved refuses, 19 passes; check 3.
7. `test_null_gw_or_code_refuses_and_season_is_required`: an aligned-Series frame with a null `gw` refuses; `parse()` without `season` is a `TypeError`; checks 4 and 5.
8. `test_ranges_refuse_never_clip_with_dgw_bound`: `xp` 26 on a single fixture is dropped and counted; `xmins` 150 on a double gameweek passes; 2 percent out of range refuses; check 6.
9. `test_identical_bytes_write_zero_rows`: a second import of the same sha is `nothing_to_do` with the note; a re-parse of equal values writes 0 and counts `rows_unchanged` equal to the row count.
10. `test_silent_pull_lands_as_error`: 600 parsed records with the writer stubbed to 0 and no skip reason land as `status error`, note `wrote_nothing_unexplained`; a real write lands `ok` with `rows_written` counted by the helper.
11. `test_position_mean_warning_fires_at_40_percent_hot`: a synthetic provider at 1.40 times the consensus writes the warning into the job note; at 1.10 it does not; check 8.
12. `test_consensus_twin_on_a_dirty_fixture`: SQL and pandas agree on the 7.5 fixture; the retired provider and the non-member are absent from `n_sources`.
13. `test_pit_grep_projection_reads`: no module except `desk/db/pit.py` contains `SELECT` or `select(` against `projection` or `provider_score`.
14. `test_provider_score_rules`: a row fetched after the deadline is not scored; a universe player with no `player_fixture` row scores 0; a club with no fixture is excluded; the `consensus` row exists; a re-run after a changed actual updates the row; migrated GW1 to GW4 overall MAE matches R2 within 0.005.
15. `test_projections_table_contract`: example params validate with `extra="forbid"`; the empty database returns the `empty` envelope; 8 GWs by 662 rows is under 250 KB; the snapshot under `contracts/panels/` matches; `provenance.inputs` names every provider read.
16. `test_player_detail_hides_private_values_from_users`: role `user` gets `values_visible false` and null cells for `fplreview`; `admin` gets the values; the consensus row is identical for both.
17. `test_upload_preview_writes_nothing_and_commit_writes_rows`: preview leaves the `projection` count unchanged and inserts one `upload` row in `preview`; commit inserts a job with `trigger admin`; running it writes 6,560 rows and sets `committed` and `job_id`; a second commit is 409.
18. `test_projections_screen_states` (vitest): the empty envelope renders the 8.1 reason and fix; the skeleton renders 20 rows and 13 columns; a filter change fires 0 requests and a GW chip change fires 1.
19. `test_projections_layout` (Playwright): at 1,280 px the table's `scrollWidth` equals its `clientWidth` with 13 columns; at 390 px the document width is 390, the first column is sticky and 3 GW columns render; both themes; every header has `aria-sort`.
20. `test_feature_check_projections_line`: against `PUBLIC_URL` with the check session, `projections_table` for the next GW returns 500 or more rows with `n_sources >= 2` and `providers_meta` shows `fplform` with `age_days` under 1.5; the line prints `projections ok` or names the failing clause.

### 11. Definition of done

- [ ] The 20 tests of section 10 pass locally against the testcontainers Postgres.
- [ ] CI green on `main`: ruff, `mypy --strict desk`, pytest, prose check over `docs/`, `tsc --noEmit`, eslint, `vite build`, `schema.d.ts` regenerated with no diff, contract snapshots for `projections_table`, `player_detail`, `provider_accuracy`, `admin_providers` committed.
- [ ] Deployed to Railway: web and worker at the same `git_sha`; `/api/health` reports `alembic_head` equal to the S3 head; no task in `never_ran` after the first tick fires the three pulls.
- [ ] Feature check green for `projections` and for every earlier line (health, me, fixtures, admin, assets, ui budget).
- [ ] Owner's demo, before GW10: (1) on `/admin` drop `fplreview_<epoch>.csv`, read "656 resolved, 19 dropped"; (2) press "Import 656 players", open the job row, read `rows_written 6560`; (3) on `/projections` read the `fplreview` chip age and the dots on GW12 to GW14; (4) set GW10 to GW14, untick `fpl_ep`, read the sum change; (5) click Fernandes, read the pivot and sparkline, click `fplform`, read `as_of`; (6) open the accuracy fold, read MAE per provider GW1 to GW9 with `fpl_ep` beside the consensus; (7) on a phone, filter DEF and tick My squad.
- [ ] Docs updated: `docs/SPEC.md` S3 block marked done with the deploy sha; `docs/runbook.md` regenerated from `JOBS`; `docs/adr/0004-private-provider-visibility.md` recording the `private` flag; `README.md` lists `desk run-task provider_pull_fplform` and `desk run-task provider_score --season 2026-27`.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src` returns nothing.
- [ ] The PR carries a surface inventory of the old Projections tab (source chips, stale banner, weighting disclosure, GW chips, filter row, matrix, per-source pivot, pizza, Understat profile, chatter strip) with the new location of each; the pizza and the Understat profile are listed as not carried (no reader among the six features, R1 section 3) for the owner's word.

### 12. Out of scope

| Not in S3 | Where |
|---|---|
| The weighted consensus (inverse pooled MSE, `n_obs >= 200` floor, opt-in after 8 scored GWs) | v2; `provider_score` rows are ready for it |
| An own points model or `p_appear` classifier | never in v1 (D5) |
| Premier Injuries, fplbench, blueladd | dropped (R2) |
| Creator snippets in the player drawer | S9 |
| The solver reading the consensus, `forecast_source` in the plan | S4 |
| The consensus captain beside the plan captain | S5 |
| Chat tools over the three panels | S7 |
| Solio member CSV, FPLReview automation, any scrape of a member page | never |
| `Elite%` or any external ownership metric | D10 |
| Uploads by non-admins | not planned |

### 13. Size and session plan

About 26 files excluding tests, 3,400 lines of Python and 1,300 of TypeScript.

| Order | Files | Lines | Tests |
|---|---|---|---|
| 1 | `desk/migrations/versions/0003_projections.py` | 90 | 1 |
| 2 | `desk/providers/base.py`, `names.py`, `fplreview.py` plus its fixture | 380 | 2, 3 |
| 3 | `desk/providers/validate.py`, `importer.py` | 360 | 4 to 9 |
| 4 | `desk/providers/fplform.py`, `fpl_ep.py`, `airsenal.py`, `solio.py` and fixtures (capture `solio/latest.json` with `curl` first) | 520 | 2 |
| 5 | `desk/providers/consensus.py`, `scoring.py` | 420 | 12, 13, 14 |
| 6 | `desk/jobs/tasks/provider_pull.py` (three registry rows, one `main` bound per provider), `provider_import_upload.py`, `provider_score.py`; the `fpl_ep` call in `fplapi/ingest.py` | 330 | 10, 11 |
| 7 | `desk/panels/projections_table.py`, `player_detail.py`, `provider_accuracy.py`, `admin_providers.py`; `desk/web/routers/uploads.py`; policy rows; `contracts/` snapshots; `gen_types.sh` | 740 | 15, 16, 17 |
| 8 | `web/src/components/ChipRow.tsx`, `HeatCell.tsx`; `web/src/screens/projections/ProjectionsScreen.tsx`, `columns.tsx`, `PlayerDrawer.tsx`, `AccuracyFold.tsx`, `urlState.ts` | 960 | 18, 19 |
| 9 | `web/src/screens/admin/UploadCard.tsx`, `ProvidersTable.tsx`; `scripts/feature_check.py` projections line; `docs/` | 360 | 20 |

Where a session is most likely to go wrong, and the guard:

1. The task counts its own rows and reports `ok` on a run that wrote nothing. Guard: the importer's counts go to the note only; the S0 helper's `count(*)` is the ledger number; test 10 stubs the writer to 0 and asserts `error`.
2. A panel or the scorer writes its own `SELECT` over `projection` and reads a post-deadline revision. Guard: test 13 greps; the scorer takes `t = deadline_at` and test 14 plants a later row.
3. The screen refetches on a filter, or the payload passes the budget at 8 GWs. Guard: test 18 counts requests; test 15 measures bytes; `cells` stays an array of arrays.
4. A double gameweek refuses a whole upload on `xmins` over 96. Guard: the `k` factor in check 6 and test 8's DGW row.

Contradictions between digest facts and the decisions, with the resolution taken here:

- R2 recommends excluding `fpl_ep` from the mean (worse than the baseline in 3 of 4 weeks, Brier about twice the baseline). D5 includes it. Kept in per D5 behind `consensus_member: true`; its MAE sits on the accuracy strip beside the consensus; the exclusion is a one-row change the owner can ask for.
- R1 item 6 and R2 name four launch providers plus Premier Injuries; D5 names five with Solio. Followed D5; Solio covers about 100 players (top lists only, R7 section 5) and its chip says so.
- D5 says the FPLReview export is "never republished"; a multi-user app serves a consensus containing it to every signed-in user. Resolved by the `private` flag: per-player values reach admins only, the mean reaches everyone. The owner should confirm this reading of FPLReview's terms before the first non-admin signs in; the fallback is `consensus_member: false` for `fplreview` on non-admin sessions, applied from `ctx.role`.
- ARCH section 12 names the sample folder `fplreview_csv/`; the providers walk requires `<key>/`. The folder is `fplreview/` and the export script's target line is edited to match.
- R2 check 6 bounds `xmins` at 96; a double gameweek export exceeds it. Resolved by the per-team fixture count `k`.


# Part 10. Step S4: the solver

Step 6 of 10 in `docs/SPEC.md`. Repo `fpl-desk`, package `desk`, frontend `web/`. Season 2026-27. Written 2026-09-21 against ARCH.md sections 3, 5, 6, 9, 11 (the S4 block) and 13, digests R3, R2, R7, R8 and R6, and the old repo at `/Users/nripeshpradhan/Documents/Github/i-test-season` (cited as `old:<path>`). British spelling. Where ARCH is silent the choice is marked `DECISION:`.

### 1. Goal

The owner presses Solve and reads, within 300 s, a transfer plan for the next 5 gameweeks solved on the consensus he sees on Projections, with the hit-taking best and 2 alternative plans beside it. S3 gave him the numbers; S4 gives him the decision in the idiom of FPLReview's Linear Optimiser, every gain labelled by currency and horizon, every plan tied to the 15 it was solved against.

### 2. Depends on

| Step | What S4 uses |
|---|---|
| S0 | `job` and the ledger helper in `desk/jobs/runner.py`; the worker's solve lane with its subprocess kill at `budget_s + 30`; `POST /api/admin/jobs/{task}/run`; `auth/policy.py`; `scripts/feature_check.py`; `desk/db/pit.py` `latest_at` |
| S1 | `event`, `player`, `player_state`, `fixture`; `user_squad` with `picks` jsonb after autosub reversal, `bank_tenths`, `free_transfers`, `ft_source`, `chips_played`; `desk/rules/registry.yaml` and `rules/loader.py`; the `me_squad` panel; the `squad_refresh` job |
| S8 | tokens, the shared kit of ARCH section 9, `usePanel`, `schema.d.ts` generation, the Admin jobs board, the `artefact` table and `ARTEFACT_KINDS` with `state()` |
| S3 | `projection`; `desk/providers/consensus.py` giving the unweighted mean per (season, gw, code) over non-retired `consensus_member` providers with `n_sources`, `xmins`, `p_appear`; the `player_detail` panel |

`user_squad.picks` rows must carry `purchase_tenths` per pick, which S1's `me_squad` sell price column already needs. `test_squad_inputs` asserts it on every seeded pick. A pick without it is handled in section 7.2 and labelled.

### 3. User stories

User role:

1. I open `/solver` with a squad and no plan, read one empty state naming the horizon, press Solve, watch a progress strip name the phase and seconds, and a plan appears without a reload.
2. I read the headline "Rogers out, Le Fee in, +14.35 xPts over GW11 to GW15 versus rolling, consensus, 0 hits" and a per-gameweek ledger under it: XI xPts, captain, moves, free transfers, hits, bank.
3. I open Alternatives and read the hit-taking best beside the headline with its hits and gain over the headline, then 2 more distinct plans.
4. I lock Haaland, press Solve again, and the new plan owns him in every gameweek. I ban a player and he is sold in GW1.
5. I make a real transfer on the FPL site. After the next squad refresh the plan shows a superseded banner naming the 2 players that differ and a Solve again button.
6. I click a pitch card and a drawer opens with that player's per-provider projections; the URL `/solver/p/<code>` reloads to the same drawer.
7. On a 390 px phone I press Settings, the form opens full width, I press Solve, and the ledger scrolls inside its own box with no page scroll.
8. Two hours after a deadline the plan for the next gameweek is already on the page, solved by the scheduler.

Admin role:

9. On Admin I read the `solve` row: last run, status, seconds, MIP gap, rows written; expanded, the last 20 runs by user email with horizon, seconds, status, gap and log tail.
10. I press Run-now on `solve`; it queues a solve for my own entry with `trigger = admin` and lands `ok` with 1 row written within 5 minutes.
11. I see no other user's plan content, only counts, status, seconds and the note.

### 4. Data

S4 creates no table. It alters `job` with one index, registers one artefact kind, and reads 7 tables. Migration `desk/migrations/versions/0006_solver.py` (the number follows the head left by S3; the session takes the next free number).

#### 4.1 Alteration: single flight per user on `job`

```sql
CREATE UNIQUE INDEX job_solve_single_flight
  ON job (task, user_id)
  WHERE task = 'solve' AND state IN ('queued', 'running');
```

Why: a second Solve click while one is queued or running returns 409 and inserts nothing. `DECISION:` single flight is per user; the solve lane runs one subprocess at a time and later rows wait in `queued` with a visible position.

#### 4.2 Artefact kind `solver_plan`

One row in `artefact` per completed solve: `kind = 'solver_plan'`, `user_id` set, `job_id` set, `created_at`, `inputs` jsonb, `payload` jsonb. Retention 30 per user (ARCH section 3); the daily `retention` job deletes older rows and counts them.

`inputs` (pydantic `SolverPlanInputs`, `extra="forbid"`):

```json
{
  "season": "2026-27",
  "gw": 11,
  "horizon_gws": [11, 12, 13, 14, 15],
  "generated_at": "2026-10-30T09:14:02Z",
  "squad": {
    "as_of": "2026-10-29T20:30:11Z",
    "codes": [118748, 154561, 184029, "... 15 sorted codes"],
    "bank_tenths": 13, "free_transfers": 2, "ft_source": "accrual",
    "chips_played": {"bboost": [1], "3xc": [5]}
  },
  "consensus": {
    "as_of": "2026-10-30T08:02:44Z",
    "providers": [{"provider": "fplform", "as_of": "2026-10-30T08:02:44Z"}, {"provider": "fplreview", "as_of": "2026-10-28T15:16:34Z"}],
    "rows": 3105, "players": 621
  },
  "prices_as_of": "2026-10-30T06:00:12Z",
  "settings_sha256": "9f2c...",
  "git_sha": "ab12cd3"
}
```

`squad.codes` is sorted. `state()` in `desk/artefacts/registry.py` reads this block plus three live facts (the user's current `user_squad.picks` codes, `event.is_next`, `now()`) and returns one word; section 7.6 gives the rule.

`payload` (pydantic `SolverPlanPayload`, `extra="forbid"`), the R3 section 6.3 shape with the fields below. Money is integer tenths. Every xPts is a float rounded to 2 decimals.

| Field | Type | Note |
|---|---|---|
| `squad_before[15]` | `{code, element_id, web_name, position, team_code, price_tenths, purchase_tenths, sell_tenths, purchase_price_source}` | source in `{transfer_log, assumed_current}` |
| `squad_source`, `currency` | text | `public_picks`; `consensus` |
| `season`, `gw`, `horizon_gws[]`, `generated_at` | | |
| `forecast_source`, `forecast_fill_share`, `forecast_filled_rows` | text, float, int | `consensus`; share of (candidate, gw) cells filled with 0 for lack of a row |
| `free_transfers`, `free_transfers_source` | int, text | from `user_squad` |
| `settings` | `SolveSettings` | echoed, preset resolved to numbers |
| `chosen` | `Line` | the headline at `hits_max` |
| `unconstrained` | `Line` or null | at `hits_max = 8`; null when the headline ran at 8 |
| `roll` | `{objective, per_gw_xpts[]}` | headline settings plus `no_transfer_gws = [gw]` |
| `alternatives[]` | `Line[]`, at most 2 | no-good cuts on the headline |
| `solver` | `{highspy, n_vars, n_bin, n_cons, candidates, seconds_total}` | per-line status inside `Line.solver` |
| `notes[]`, `bounds` | text[], `{candidates_per_position, universe_size, price_forecast: "static", horizon_cap_by_coverage}` | |

`Line`:

```
{ label: "headline" | "unconstrained" | "roll" | "alt_1" | "alt_2",
  objective, objective_undiscounted, gain_over_roll, gain_over_headline,
  n_transfers, hits, hit_points, chip: null | {name, gw},
  out[]: {code, web_name, sell_tenths, gw}, in[]: {code, web_name, price_tenths, gw},
  bank_after_tenths, captain: {code, web_name, xp}, vice: {code, web_name, xp},
  best_xi_captain: {code, web_name, xp}, starting_xi[]: code,
  per_gw[]: { gw, squad[15]: code, xi[11]: code, bench[4]: code, captain, vice,
              chip, in[], out[], ft_available, ft_used, hits, bank_after_tenths,
              xpts_xi, xpts_captain_extra, xpts_bench_weighted, xpts_total },
  cut: null | {criterion, difference},
  solver: {status, gap, seconds, incumbent: true} }
```

`gain_over_roll` is `chosen.objective - roll.objective`, always with the currency and horizon printed beside it in the UI. `best_xi_captain` is the highest consensus xPts in the GW1 XI, printed beside `captain` (rule 23; incident DOGFOOD section 7).

#### 4.3 Job params for `solve`

`job.params` holds the validated `SolveSettings` (section 6.1) plus nothing else; `job.user_id` names the user; `job.trigger` is `user`, `admin`, `schedule` or `catchup`. Sample:

```json
{"horizon": 5, "hits_max": 0, "preset": "fplreview", "decay": 0.85, "ft_value": 1.75,
 "itb_value": 0.10, "vcap_weight": 0.05, "bench_weights": {"gk": 0.03, "1": 0.30, "2": 0.10, "3": 0.03},
 "ft_ladder": {"2": 2.0, "3": 1.6, "4": 1.3, "5": 1.1}, "hit_cost": 4,
 "chips": {"wildcard": {"allowed_gws": []}, "freehit": {"allowed_gws": []}, "bboost": {"allowed_gws": []}, "tc": {"allowed_gws": []}},
 "locked": [223094], "banned": [], "no_transfer_gws": [], "seconds": 300, "lines": 3,
 "candidates_per_position": 20, "mip_rel_gap": 0.01}
```

#### 4.4 Tables read, with the point-in-time rule

| Table | Read | Rule |
|---|---|---|
| `event` | `is_next` row; `deadline_at` | current row |
| `player` | position, team_code, web_name, element_id | current row |
| `player_state` | `price_tenths`, `status`, `can_select` | `latest_at(player_state, (season, code), t)`, `t` the solve instant |
| `projection` via `consensus.py` | mean xp, xmins, p_appear, n_sources per (gw, code) | `latest_at` per provider at `t` |
| `user_squad` | newest row for (user_id, season) | current row; `updated_at` becomes `inputs.squad.as_of` |
| `fixture` | teams with a fixture per horizon GW | current row; the blank-GW note only |
| `job` | queue position, last params | current row |

Nothing in S4 refreshes on a clock. Every read happens inside one solve at instant `t`, written to `inputs`. A solve stamped after `event.deadline_at` of its own `gw` is refused before spawn.

### 5. Jobs

One job. Registry row in `desk/jobs/registry.py`:

```python
Job(name="solve", lane="solve", due=DeadlineRelative(hours=+2, per_user="plan_in_last_14d") | OnDemand,
    stale_window=timedelta(days=8), writes=("artefact:solver_plan",), read_by=("solver_plan", "dashboard"),
    meters=(), budget_s="params.seconds + 30", main="desk.solver.run:main")
```

| Item | Value |
|---|---|
| Trigger | `user`: `POST /api/solver/run`. `admin`: Run-now on the board, for the admin's own entry. `schedule`: at `deadline_at + 2 h` of the gameweek that just locked, one row per user who has a `solver_plan` artefact newer than 14 days, with that user's last `job.params`. `catchup` on worker restart within 36 h. |
| Inputs | `job.params` (section 4.3), `job.user_id`, the tables of section 4.4 at instant `t` |
| Outputs | one `artefact` row, kind `solver_plan`; `job.log_tail` with one line per phase |
| Ledger row | `items_seen = 1`; `rows_written` counted by the helper on `artefact WHERE kind = 'solver_plan'` (expected 1); `items_skipped` by reason from `{no_squad, gw_locked, coverage_short, in_flight}`; `note` carries `status=<highs status> gap=<g> lines=<n found> of <n asked> seconds=<s>`; `model = null`, `usd = 0` |
| Cost cap | none metered; CPU only. Wall clock bounded by `seconds` in 30 to 300 |
| Timeout | the worker kills the subprocess at `params.seconds + 30` and writes `status = error`, note `killed_at_budget` |
| Failure | any exception lands `error` with the class name in `note` and the traceback in `log_tail`. `NoIncumbentError` lands `error`, note `no_incumbent seconds=<s>`. A refusal before spawn (`no_squad`, `gw_locked`, `coverage_short`) lands `refused` with the reason in `note`, and for a user trigger the same reason is the 400 body. |
| Silent nothing | A run that wrote no artefact shows `rows_written 0` with `status error, note wrote_nothing_unexplained` on the Admin row. A schedule that inserted nothing for an eligible user fails the feature check's solver line: the check user's latest plan is `superseded` or over 8 days old. A tick with no eligible user lands `nothing_to_do`, note `no_user_with_plan_in_14d`, amber on the board. |

Phases, one `log_tail` line each with elapsed seconds: `inputs`, `build`, `headline`, `roll`, `unconstrained`, `alt_1`, `alt_2`, `score`, `write`. The worker copies subprocess stdout into `job.log_tail` every 5 s so `solver_status` shows the live phase.

### 6. API and panels

All three panels are session tier and go through `GET /api/panels/{name}` with the ARCH section 6 envelope. Every panel answers under 10 s; the solve itself is asynchronous and is the one exception, bounded by `seconds + 30`.

#### 6.1 `POST /api/solver/run` (session, CSRF)

Body: `SolveSettings`, `extra="forbid"`. Every field optional; a missing field takes the default. `preset` fills `bench_weights`, `vcap_weight` and `ft_ladder` unless the body sets them.

| Field | Type | Default | Bounds | Source of the default |
|---|---|---|---|---|
| `horizon` | int | 5 | 1 to 8, capped by coverage | ARCH S4; consensus thins at GW10 (R2) |
| `hits_max` | int | 0 | 0 to 8 | ARCH S4; the unconstrained line runs at 8 |
| `preset` | `fplreview` or `open_fpl_solver` | `fplreview` | | ARCH section 13 |
| `decay` | float | 0.85 | 0.80 to 1.00 | FPLReview Time Decay (R7 s3) |
| `ft_value` | float | 1.75 | 0.0 to 3.0 | FPLReview FT Value (R7 s3) |
| `itb_value` | float | 0.10 | 0.0 to 0.20 | FPLReview Bank Value per 1.0m (R7 s3) |
| `vcap_weight` | float | 0.05 fplreview; 0.10 open_fpl_solver | 0.0 to 0.5 | R7 s3 and s4 |
| `bench_weights` | `{gk, 1, 2, 3}` | fplreview `{0.03, 0.30, 0.10, 0.03}`; open_fpl_solver `{0.03, 0.21, 0.06, 0.002}` | each 0 to 1, non-increasing over 1 to 3 | R7 s3 and s4 |
| `ft_ladder` | `{2, 3, 4, 5}` | `{2.0, 1.6, 1.3, 1.1}` | each 0 to 3, non-increasing | open-fpl-solver `ft_value_list` (R7 s4); scaled per section 7.3 |
| `hit_cost` | int | 4 | 4 only | game rules |
| `chips` | per chip `{allowed_gws: int[]}` | all empty | inside the horizon and the window; a chip spent this half is refused | D6, chips held |
| `locked`, `banned` | int[] codes | `[]` | at most 30 each, disjoint | ARCH S4 |
| `no_transfer_gws` | int[] | `[]` | inside the horizon | the roll; chat's `propose_solve` |
| `seconds` | int | 300 | 30 to 300 | FPLReview Time Limit |
| `lines` | int | 3 | 1 to 3 | FPLReview Solve Lines |
| `candidates_per_position` | int | 20 | 10 to 60 | ARCH S4 |
| `mip_rel_gap` | float | 0.01 | 0.0 to 0.05 | `DECISION:` gaps sat at 5 percent at 150 s (R3) |

Responses:

| Code | Body |
|---|---|
| 202 | `{job_id, queued_ahead: int, estimate_s: int, gw: int, horizon_gws: int[]}` |
| 400 | `{error: "invalid_settings", detail: [{field, msg}]}`; or `{error: "coverage_short", detail: "consensus covers GW11 to GW13; horizon asks GW11 to GW15", max_horizon: 3}`; or `{error: "gw_locked", detail}`; or `{error: "chip_spent", detail: "bboost was played in GW1; the first set expires after GW19"}` |
| 403 | `{error: "no_squad", detail: "No squad on file", remediation_url: "/account"}` |
| 409 | `{error: "solve_in_flight", job_id}` |

`estimate_s` sums `seconds` over the `solve` rows ahead plus this run's. Coverage: every horizon GW needs consensus rows for at least 300 players; the first GW that fails caps `max_horizon`.

#### 6.2 Panel `solver_form` (session)

`DECISION:` a third panel, so the screen mounts with 2 calls. Params: none. Result:

```
{ next_gw, deadline_at, seconds_left,
  defaults: SolveSettings, bounds: { horizon: [1, 8], hits_max: [0, 8], seconds: [30, 300],
            candidates_per_position: [10, 60], decay: [0.8, 1.0], ft_value: [0, 3], itb_value: [0, 0.2] },
  presets: { fplreview: {bench_weights, vcap_weight, label: "FPLReview Linear Optimiser"},
             open_fpl_solver: {bench_weights, vcap_weight, label: "open-fpl-solver"} },
  last_used: SolveSettings | null,
  coverage: [ {gw, players_with_consensus, n_sources_mean} ],  # next_gw .. next_gw + 7
  max_horizon: int,
  chips: [ {name, half, available: bool, reason: null | "played_gw_1" | "outside_window"} ],
  squad: [ {code, web_name, position, team_code, price_tenths, sell_tenths} ] | null,
  free_transfers, ft_source, bank_tenths,
  in_flight: null | {job_id, state, queued_ahead} }
```

Empty: `{reason: "No squad on file", fix: "Set your FPL team id on Account"}` when `user_squad` has no row. Budget 800 ms. Chat tool `panel_solver_form`.

#### 6.3 Panel `solver_plan` (session)

Params: `{job_id: int | null}`; null means the user's latest artefact. Result: `{artefact_id, job_id, state: "fresh" | "aging" | "stale" | "superseded", state_detail: {age_h, squad_diff: {missing: code[], extra: code[]}, gw_moved: bool}, inputs: SolverPlanInputs, payload: SolverPlanPayload}`. Empty: `{reason: "No plan yet", fix: "Press Solve to plan GW11 to GW15 on the consensus"}`; another user's `job_id` is 404. `provenance.inputs` lists `{kind: "solver_plan", id: artefact_id, as_of: generated_at}` and `{kind: "user_squad", id: user_id, as_of}`. Budget 500 ms; under 120 KB at horizon 8. Chat tool: yes; `headline(result)` returns `state`, `chosen` and `unconstrained` without `per_gw`, and the `alternatives` labels with `gain_over_headline`.

#### 6.4 Panel `solver_status` (session)

Params: `{job_id: int}`. Result: `{job_id, state: "queued" | "running" | "done", status: null | "ok" | "error" | "refused", phase: null | "inputs" | "build" | "headline" | "roll" | "unconstrained" | "alt_1" | "alt_2" | "score" | "write", elapsed_s, budget_s, queued_ahead, note, log_tail: string (last 4 KB), artefact_id: int | null}`. Another user's job is 404. Budget 200 ms. The screen polls every 3 s while `state != "done"`. Chat tool: yes.

#### 6.5 Admin

`admin_jobs` (S0) already lists `solve`. `DECISION:` `admin_job_log` gains no new field; the expanded `solve` row on the board calls `admin_jobs` with `task=solve&limit=20`, which S0's panel supports through its existing `task` filter, and shows `user email, trigger, horizon (from params), seconds, status, note, elapsed`. Run-now on `solve` inserts a row for the admin's own `user_id` with `trigger = admin` and the admin's `last_used` settings or the defaults.

### 7. Algorithms

Code lives in `desk/solver/`. `settings.py` (the pydantic model, presets, bounds), `inputs.py` (reads and the candidate universe), `model.py` (the HiGHS MILP), `scorer.py` (recompute and finance replay), `artefact.py` (writer with its refusals, `state()`), `run.py` (the subprocess entry, `--job <id>` and `--help`). No PuLP, no `open-fpl-solver` package import; `test_solver_deps` greps `pyproject.toml` and the module for both.

#### 7.1 Rules

Read from `desk/rules/registry.yaml` through `rules/loader.py` at build time. Nothing in `model.py` holds a rule value. The values the tests pin:

| Rule | Value |
|---|---|
| squad, XI, budget, max per club | 15, 11, 1000 tenths, 3 |
| per position (GKP, DEF, MID, FWD); XI min/max | 2, 5, 5, 3; 1/1, 3/5, 2/5, 1/3 |
| free transfers per GW, max banked, hit cost, transfer cap | 1, 5, 4, 20 |
| sell-on | keep 50 percent of the rise, floored to 0.1m |
| chips | WC and FH GW 2 to 19 and 20 to 38; BB and TC GW 1 to 19 and 20 to 38; one of each per half; one per GW; FH never consecutive |
| captain, triple captain | x2, x3 |

#### 7.2 Inputs and the candidate universe

`t = now()`. `gw0 = event.is_next.gw`; `H = horizon`; `gws = [gw0, ..., gw0 + H - 1]`. Refuse if `t >= deadline_at(gw0)`.

Squad: newest `user_squad` for the user. Each pick gives `code` and `purchase_tenths`. Sell price is `sell(pp, px) = min(px, pp + floor((px - pp) / 2))` in integer tenths when `px > pp`, else `px` (`old:fpl_edge/opt/milp.py:18-24`). A pick with no `purchase_tenths` takes `price_tenths` with `purchase_price_source = "assumed_current"`, and `notes` says "N purchase prices assumed at current price; sell values may be high". `ft0 = free_transfers`, `ft_source` copied; `chips_played` decides which chips remain this half.

Prices: `latest_at(player_state, t)` for every selectable `code`; static over the horizon (`bounds.price_forecast = "static"`).

Projections: `consensus(season, gws, t)` from S3, one row per (gw, code) with `xp`, `n_sources`. A candidate cell with no row gets `xp = 0` and counts in `forecast_filled_rows`; `forecast_fill_share = filled / (candidates x H)`. Coverage under 300 players in any horizon GW refuses with `coverage_short` before spawn.

Universe: per position, the top `candidates_per_position` codes by `sum_j xp[code, j]` among selectable players, plus every held, locked and banned code. A held player with `can_select = false` stays and can be sold. `bounds.universe_size` records the count; pruning changes the answer and the artefact says so.

#### 7.3 The MILP

Sets: players `i` in the universe, gameweeks `j = 0..H-1`, chips `c` in `{wildcard, freehit, bboost, tc}`, bench slots `k = 1..3`. Data: `xp[i,j]`, `px[i]` (tenths), `pos[i]`, `club[i]`, `held[i]`, `pp[i]` (purchase tenths for held), `d[j] = decay ** j`.

Variables (open-fpl-solver names where they exist):

| Name | Type | Meaning |
|---|---|---|
| `squad[i,j]`; `play[i,j]` | bin | held after GW j transfers; the 15 that score (equals `squad` unless FH is allowed in j) |
| `lineup[i,j]`; `bench_gk[i,j]`, `bench[i,j,k]` | bin | in the XI; benched GK, outfield bench slot k |
| `captain[i,j]`, `vicecap[i,j]`; `transfer_in[i,j]`, `transfer_out[i,j]` | bin | armband; flow |
| `purchase[i,j]`; `sale[i,j]` | cont in [0, px_i]; int in [0, px_i] | price paid, follows the player; selling value when sold in j |
| `bank[j]`; `paid[j]`; `nohit[j]` | cont [0, 4000]; int [0, 15]; bin | tenths after GW j; hits in GW j; big-M switch |
| `ft[j]`, j = 1..H | int in [1, 5] | free transfers entering GW j; `ft[H]` is the count left behind the horizon |
| `chip[c,j]` | bin | created only inside the window, an unspent half and `allowed_gws` |
| `tcv[j]`, `bbv[j]`, `ftv[j]` | cont | chip uplifts and the FT potential |

Constraints, with `M = 15`, `own(i,-1) = held[i]`, `pp` for held else 0:

```
squad shape (every j, for squad and for play):  sum_i squad = 15;  sum_{i in pos} squad = quota(pos);  sum_{i in club} squad <= 3
XI:            sum_i lineup = 11;  min(pos) <= sum_{i in pos} lineup <= max(pos);  lineup <= play
bench:         bench_gk = play - lineup for GK i;  sum_k bench[i,j,k] = play - lineup for outfield i;  sum_i bench[i,j,k] = 1 for each k
armband:       sum_i captain = 1;  sum_i vicecap = 1;  captain <= lineup;  vicecap <= lineup;  captain + vicecap <= 1
free hit:      play = squad when chip[freehit, j] does not exist;  otherwise play is free, and transfer_in + transfer_out <= 1 - chip[freehit, j]
flow:          squad[i,j] - squad[i,j-1] = transfer_in[i,j] - transfer_out[i,j];  transfer_in + transfer_out <= 1
purchase:      purchase[i,j] <= px_i * squad[i,j]
               px_i - px_i (1 - transfer_in) <= purchase[i,j] <= px_i + px_i (1 - transfer_in)
               |purchase[i,j] - purchase[i,j-1]| <= px_i (1 - keep),  keep = squad[i,j] - transfer_in[i,j]
sale:          sale[i,j] <= px_i * squad[i,j-1];  2 sale[i,j] <= purchase[i,j-1] + px_i;  sale[i,j] <= px_i * transfer_out[i,j]
money:         bank[j] = bank[j-1] + sum_i sale[i,j] - sum_i px_i transfer_in[i,j]
FH budget:     sum_i px_i play[i,j] <= bank[j-1] + sum_i sale_cap[i,j] + 4000 (1 - chip[freehit, j]),  sale_cap = min(px_i, pp_i + floor((px_i - pp_i)/2)) on the persistent squad
transfers:     n[j] = sum_i transfer_in[i,j];  free[j] = chip[wildcard,j] + chip[freehit,j] + [j = 0 and preseason]
               n[j] <= 20 + M free[j];  paid[j] <= M (1 - free[j])
               paid[j] >= n[j] - ft[j] - M free[j];  paid[j] <= n[j] - ft[j] + M nohit[j];  paid[j] <= M (1 - nohit[j])
               ft[j+1] <= ft[j] - n[j] + paid[j] + 1 + M free[j];  ft[j+1] <= ft[j] + 1;  ft[0] = ft0
hits cap:      sum_j paid[j] <= hits_max
no transfers:  n[j] = 0 for j in no_transfer_gws
chips:         sum_c chip[c,j] <= 1;  sum_{j in half} chip[c,j] <= remaining(c, half);  chip[freehit,j] + chip[freehit,j+1] <= 1
locked, banned: squad[i,j] = 1 for locked i, every j;  squad[i,j] = 0 for banned i, every j (a held banned player is sold in j = 0 by the flow)
```

The two `sale` inequalities give exactly `min(px, pp + floor((px - pp)/2))` at integers (`old:fpl_edge/opt/milp.py:18-24`), so the sell-on fee is exact for a player bought mid-horizon. `ft` in [1, 5] caps banking at 5; `ft[j+1] <= ft[j] + 1` makes WC and FH retain and never mint. Pre-season (`gw0 = 1`, no picks) sets `ft0 = 0` and `free[0] = 1`.

Objective, maximised:

```
sum_j d[j] * [ sum_i xp[i,j] lineup[i,j]
             + sum_i xp[i,j] captain[i,j]                        (captain doubles)
             + tcv[j]                                            (triple captain adds one more captain xp)
             + vcap_weight * sum_i xp[i,j] vicecap[i,j]
             + sum_i xp[i,j] (w_gk bench_gk[i,j] + sum_k w_k bench[i,j,k])
             + bbv[j]                                            (bench boost: bench at full weight)
             - hit_cost * paid[j]
             + itb_value * bank[j] / 10
             + ftv[j+1] - ftv[j] ]
```

with `tcv[j] <= max_cap_j * chip[tc,j]`, `tcv[j] <= sum_i xp[i,j] captain[i,j]`, `max_cap_j = max_i xp[i,j]`; `bbv[j] <= max_bench_j * chip[bboost,j]`, `bbv[j] <= sum_i xp[i,j] (1 - w_i) benched_i`, `max_bench_j` the sum of the top 4 `xp` in j (`old:fpl_edge/opt/milp.py:802-812`: bounding by the whole universe lets the LP relaxation play every chip fractionally). Every `xp` is at least 0, so the aggregate form is exact.

`DECISION:` the FT potential. `V(1) = 0`, `V(s) = V(s-1) + m(s)` for s = 2..5, with `m(s) = ft_value * ft_ladder[s] / ft_ladder[2]`. At the defaults `m = {2: 1.75, 3: 1.40, 4: 1.14, 5: 0.96}`: FPLReview's FT Value is the lever, open-fpl-solver's ladder the shape. `V` is concave, so `ftv[j]` is its lower chord envelope, `ftv[j] <= V(s) + (V(s+1) - V(s)) (ft[j] - s)` for s = 1..4 (`old:fpl_edge/opt/milp.py:907-963`); `ftv[0]` is the constant `V(ft0)`. Undiscounted the term telescopes to `V(ft[H]) - V(ft0)`, the horizon-truncation correction; without it the model spends every FT in the last GW.

Three approximations, each written into `notes`: the vice term is the fixed `vcap_weight` of the Linear Optimiser; autosub weights are fixed; DGWs and BGWs arrive pre-summed in `xp`.

HiGHS through `highspy >= 1.11`, the modelling API (`Highs()`, `addVariable`, `addConstr`, `maximize`). Options: `threads = 1`, `random_seed = 0`, `time_limit = slice`, `mip_rel_gap`, `output_flag = false`, `presolve = "on"`. After `run()`: read `getInfo().primal_solution_status`; anything other than feasible raises `NoIncumbentError(seconds)` before any extraction (with no incumbent every column reads 0 and the XI is empty). Read `getInfo().mip_gap` and `getModelStatus()` into `Line.solver`. Armband repair: when `sum captain = 1` holds only at tolerance with every value under 0.5, the captain is the XI player with the highest `xp`, vice the next, ties by lower code (`old:fpl_edge/opt/milp.py:1027-1040`).

#### 7.4 The lines and the time budget

`seconds` is the whole budget. `DECISION:` slices, in run order:

| Line | Share of `seconds` | At 300 s | Settings delta |
|---|---|---|---|
| headline | 45 percent | 135 | as requested |
| roll | 15 percent | 45 | `no_transfer_gws += [gw0]` |
| unconstrained | 20 percent | 60 | `hits_max = 8`; skipped when the headline had 8 |
| alt_1, alt_2 | 10 percent each | 30, 30 | a no-good cut per prior line; only when `lines > 1` |

A slice with no incumbent records `{status: "no_incumbent", incumbent: false}` on its line and the run continues; only the headline is fatal. Time left by an early proven optimum rolls into the next slice. The model is built once; each line changes bounds and adds rows.

No-good cut, criterion `this_gw_transfer_in_out` (open-fpl-solver's default), difference 1: with `A` the `transfer_in[i,0]` and `transfer_out[i,0]` the incumbent set to 1, add `sum(A) <= |A| - 1`. When `A` is empty (the incumbent rolls), fall back to `this_gw_lineup` over `lineup[i,0]` and record `cut.criterion` (`old:fpl_edge/opt/milp.py:898-947`). Alternatives are ordered by objective, deduplicated on GW1 moves, each with its cut.

`gain_over_roll = headline.objective - roll.objective`; `gain_over_headline = line.objective - headline.objective`. The hit verdict, UI text only: a line with hits and gain over roll at or below 0 reads "does not beat rolling"; under 1.0 reads "inside the forecast's own error"; else "paid for in this objective" (`old:fpl_edge/myteam/recommend.py:232-271`).

#### 7.5 Validation the run must pass before writing

`scorer.py`:

1. `score_line(inputs, line)` recomputes the objective from the decisions alone (XI, captain, vice, bench slots, chips, hits, bank, ft path) and must agree with HiGHS to 1e-6 (`old:fpl_edge/opt/scoring.py:100-141`).
2. `replay_finances(inputs, line)` re-runs bank and free transfers in integer tenths with `sell()`; a negative bank, an FT outside [1, 5], an FH squad over its pot, or a hit count differing from `4 * max(0, n - ft)` is a `PlanInvalidError` and the run lands `error` (`old:fpl_edge/opt/scoring.py:395-482`).
3. Every `per_gw.xi` is inside `per_gw.squad`; every squad is 15 with the quotas and the 3-per-club cap; captain and vice start.
4. `artefact.py` refuses a GW1 XI outside `squad_before - out + in` (incident `da7be61`).
5. `objective_undiscounted` is the same recompute at `decay = 1.0`, reported only.

#### 7.6 The state word

`state(inputs, live)` in `desk/artefacts/registry.py`, the one function every reader calls (rule 20):

| Word | Rule, tested in this order |
|---|---|
| `missing` | no artefact for the user |
| `superseded` | `live.next_gw > inputs.gw`, or `sorted(live.squad_codes) != inputs.squad.codes` |
| `fresh` | age under 12 h |
| `aging` | age 12 h to 48 h |
| `stale` | age 48 h or more |

Age is `now - inputs.generated_at`, never distance to the deadline (incident `da7be61`).

### 8. UI

#### 8.1 `/solver`

Primary visual: `PlanLedger`, a grid with one column per horizon gameweek and 7 rows: XI xPts, captain, moves (out then in as `MoveRow` chips), free transfers (used of available), hits, bank after, chip. Above it the headline card: one sentence of the exact form "Rogers out, Le Fee in, +14.35 xPts over GW11 to GW15 versus rolling, consensus, 0 hits", with `Age` and `CiteChip` (panel, `generated_at`, git sha), and a second line "Captain Fernandes 6.14; consensus captain Fernandes 6.14" in the warn colour when the two differ by 1.5 or more. Under the ledger, the `Pitch` for the gameweek chosen in a Toggle Group (default GW1): 15 cards in formation with price and xPts, captain marker, `in` cards outlined in the accent.

Secondary content:

| Content | Where it folds |
|---|---|
| Settings form | left rail, 240 px, at 820 px and above; a full-width `Drawer` under 820 px behind the "Settings" button |
| Alternatives | Tab beside "Plan": a `DataTable` with rows unconstrained, alt_1, alt_2; columns Label, Moves, Hits, Chip, Objective, versus headline; default sort Objective desc; a row click opens the `Drawer` at `/solver/run/<job_id>/line/<label>` with that line's `PlanLedger` and pitch |
| Run log | Tab "Log": phases with elapsed seconds and `log_tail` in mono |
| Notes and bounds | `Fold` under the ledger, summary "3 notes, 84 candidates, 2 percent filled" |
| Hit verdict | one line under the headline when it carries hits; otherwise a `Fold` titled "Hit-taking best: +27.5 for 3 hits" |

Settings form fields, with defaults from `solver_form.defaults` and `last_used` when present:

| Field | Control | Default |
|---|---|---|
| Horizon | Select 1 to `max_horizon` | 5 |
| Hits allowed | Select 0 to 8 | 0 |
| Chips | 4 Toggles, each disabled with a tooltip reason when `available = false`; on, a Select of allowed GWs inside the horizon | all off |
| Lock; Ban | Command over the held 15; Command over every selectable player (names from S3's `projections_table`, loaded on open); chips of selected | none |
| Preset | Toggle Group "FPLReview", "open-fpl-solver" | FPLReview |
| Advanced (`Fold`) | Decay 0.80 to 1.00 step 0.01; FT value 0 to 3 step 0.05; Bank value 0 to 0.20 step 0.01; Vice weight; Seconds 30 to 300 step 30; Candidates 10 to 60 step 5; Lines 1 to 3; bench weights shown read-only per preset | per section 6.1 |

Buttons and copy, verbatim: "Solve"; "Solve again" (when a plan exists); "Settings" (under 820 px); "Reset to defaults"; "Show hit-taking best" (jumps to the Alternatives tab); "Open in Projections" (in the player drawer). Prompt under Solve: "Up to 5 minutes. You can leave this page; the plan also lands on the Dashboard." Superseded banner: "Your squad changed since this plan: 2 players differ. Solve again." Stale chip text: "Plan is 3 days old".

Filters and sorts: the ledger has none. Alternatives sorts as above. The Ban command filters by name and club as you type. URL state: `?gw=12` for the selected pitch gameweek, `?tab=alternatives`.

Drill-down: a pitch card or a `MoveRow` chip opens the `Drawer` level 2 at `/solver/p/<code>` showing S3's `player_detail` (per-provider pivot over the horizon, price series) with a `Breadcrumb` "Solver / Le Fee"; a line row opens level 2 at `/solver/run/<job_id>/line/<label>`; the job id in the headline's `CiteChip` opens level 2 at `/solver/run/<job_id>` with the log. Each level reloads to itself.

States:

| State | Rendering |
|---|---|
| Empty, no plan | `StateBox` empty: "No plan yet. Press Solve to plan GW11 to GW15 on the consensus." |
| Gap, no squad | `StateBox` gap: "No squad on file. Set your FPL team id on Account." with the button "Go to Account" |
| Loading | skeleton of the ledger at 7 rows by `horizon` columns and the pitch outline; layout shift 0 |
| In flight | `RunProgress` strip above the ledger: "Solving: headline, 42 of 135 s" with a bar, "2 ahead, about 6 minutes" when queued, the last log line; the previous plan stays under it with its state chip |
| Error, refused | `StateBox` error only for a thrown request. A job `error` is a gap card with the note: `no_incumbent` reads "No plan found in 300 s. Try horizon 3." with the button "Solve at horizon 3"; `killed_at_budget` reads "The run exceeded its time and was stopped." A 400 prints its detail under the Solve button |
| Stale, aging | `Age` chip on the headline; stale adds the warn colour and the text |
| Superseded | the banner above with "Solve again" |

Mobile at 390 px: the rail becomes the "Settings" button; the ledger scrolls inside its own box with sticky row labels; the pitch scales to the width; the drawer is full width; no horizontal page scroll.

Panel calls on mount: `solver_form` and `solver_plan(latest)`, 2 of 3, under 200 KB. `solver_status` polls only while a run is in flight.

Shared components: `DataTable`, `Drawer`, `Breadcrumb`, `StateBox`, `PlayerCell`, `ClubMark`, `Age`, `Number`, `CiteChip`, `Fold`, `ConfirmButton` (on "Solve again" while a plan is `fresh`: "A fresh plan exists from 2 hours ago. Solve again?"), `Pitch`, plus shadcn Tabs, Toggle Group, Select, Command, Badge, Skeleton, Tooltip. New in `web/src/components/`: `PlanLedger` (the GW grid; S5's transfer row reuses its one-column form), `MoveRow` (out and in chips with prices and the xPts delta), `RunProgress` (phase, seconds, queue position). Each has one implementation and a vitest.

### 9. Admin view

On `/admin`, the jobs board row `solve`: last run as `Age`, status, `rows_written`, seconds, gap (from the note), next due (next deadline plus 2 h) and the count of users eligible for the scheduled solve. Expanded: the last 20 runs with user email, trigger, horizon, seconds, status, gap, elapsed and a "Log" link opening `admin_job_log` in the drawer. "Run now" queues a solve for the admin's own entry with no confirm, since `solve` has no meter. Errors show the note and the last 4 KB of log.

A user never sees the board, another user's job or artefact (404 on a foreign `job_id`), the eligible-user count, or any log except their own run's.

### 10. Acceptance tests

Fixture: `tests/fixtures/solver/` holds the 592-player 2026-27 universe exported from `old:tests/fixtures/opt` (codes, positions, clubs, prices, 8 GWs of consensus xp) plus a held squad, bank 0, ft 1, chips played `{bboost: [1], tc: [5]}`. Tests prune to 20 per position so the suite runs under 60 s. Instants are computed 30 days ahead of `now()`.

1. `test_solver_deps_no_pulp_no_upstream_package`: `pyproject.toml` and `desk/solver/` name neither `pulp` nor `open_fpl_solver`; `highspy` is at least 1.11.
2. `test_rules_come_from_registry`: `model.py` holds no literal 15, 11, 1000, 4 or 20; `max_per_club = 2` in a temp registry changes the built row count.
3. `test_held_squad_with_no_positive_move_rolls`: an optimal squad returns 0 transfers in every GW and `gain_over_roll = 0`.
4. `test_sell_price_keeps_half_the_rise`: bought 75, now 78, sells 76; a 76 replacement is affordable, a 77 one is not.
5. `test_hit_taken_only_above_four_plus_ft_value`: a +3 upgrade is not bought on a hit, a +7 one is; the unconstrained line reports hits and `gain_over_headline`.
6. `test_two_upgrades_banked_then_bought_free`: two moves that pay from GW2 are banked in GW1 and made with 2 FTs in GW2, 0 hits.
7. `test_ft_accrues_and_caps_and_chips_retain`: FT path 1, 2, 3, 4, 5, 5 with no moves; a WC week keeps the count and mints none.
8. `test_ft_potential_defaults`: `m = {2: 1.75, 3: 1.40, 4: 1.14, 5: 0.96}` and the term telescopes to `V(ft[H]) - V(ft0)` at `decay = 1.0` within 1e-9.
9. `test_chip_windows_and_halves`: WC and FH cannot fire in GW1; `bboost` spent in GW1 is unavailable until GW20; one chip per GW; FH never consecutive.
10. `test_free_hit_reverts_and_wildcard_persists`: FH fields a one-week squad and the persistent squad returns; WC moves are free and persist.
11. `test_objective_recomputed_to_1e6`: `score_line` agrees with HiGHS on every line, including hits, captain, vice, bench slots, ITB and the FT potential.
12. `test_determinism_with_chips`: same inputs and slices give byte-identical payloads over 3 runs with all chips allowed.
13. `test_no_incumbent_is_named`: a 1 s headline slice on the 592-player instance raises `NoIncumbentError`; the run lands `error`, note `no_incumbent`; no artefact row exists.
14. `test_locked_banned_survive_pruning`: a locked 40th-ranked forward is owned every GW; a held banned player is sold in GW1; both survive 10 per position.
15. `test_alternatives_distinct_ordered_with_cut`: 3 lines pairwise distinct on GW1 moves, ordered by objective, each with its `cut`; a rolling headline falls back to `this_gw_lineup`.
16. `test_writer_refuses_xi_outside_squad_and_reader_marks_superseded`: an XI outside `squad_before - out + in` is refused; after the seeded `user_squad` swaps one code, `state()` returns `superseded` with the diff.
17. `test_state_words`: one case per word at ages 1 h, 13 h, 49 h, plus `gw_moved`, plus no artefact.
18. `test_solve_job_silent_nothing_is_error`: a task stub that writes nothing lands `error, wrote_nothing_unexplained`; a tick with no eligible user lands `nothing_to_do` with its note; a tick with one eligible user inserts exactly one row with `trigger = schedule` and that user's last params.
19. `test_solver_panels_contract_and_empty`: the three panels validate their `contracts/panels/` snapshots with `extra="forbid"`; on an empty database each returns the section 6 `empty` envelope; a foreign `job_id` is 404; `POST /api/solver/run` is 409 on a second click and 400 with `max_horizon` on short coverage.
20. `test_feature_check_solver_line`: against `PUBLIC_URL` with the check session, `solver_plan(latest)` is `fresh` or `aging`, or a run at `horizon 2, seconds 30, lines 1` reaches `done, ok` within 90 s with `squad_before` equal to `me_squad`.

### 11. Definition of done

- [ ] Tests 1 to 20 green locally and in CI; `mypy --strict desk/solver`; ruff; `tsc --noEmit` with the regenerated `schema.d.ts` committed.
- [ ] The three panel snapshots under `contracts/panels/` committed; `contracts/openapi.json` diff reviewed.
- [ ] The registry walks pass with the new rows; `docs/runbook.md` regenerated and its sync test green.
- [ ] Deployed to Railway on `main`; `/api/health` reports the deploy sha and `runtime.highspy` equal to the lock.
- [ ] Feature check green for the solver line and every earlier line (health, me, fixtures, projections, admin, assets, ui budget).
- [ ] A held-squad 5-GW solve at 20 per position, 300 s, lands `ok` on the worker with the headline gap under 5 percent, read from the Admin row.
- [ ] Owner's demo, before GW11: open `/solver`; press Solve; watch the strip reach `write`; read the headline with its currency and horizon; open Alternatives and read the hit-taking best; lock Haaland and press Solve again; read the captain line beside the consensus captain; make one real transfer, wait for the T+90m refresh, reload and read the superseded banner; press Solve again. Repeat the first Solve at 390 px.
- [ ] `docs/SPEC.md` S4 block marked done with the deploy sha; `docs/adr/0004-solver-defaults.md` records each default with its source (the section 6.1 table).
- [ ] Surface inventory in the PR: the old Planner's rail controls, grid rows, stat tiles, pitch and per-player matrix, each mapped to section 8 or to "S5 dashboard" (the stat tiles).
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk/solver web/src/screens/solver` returns nothing.

### 12. Out of scope

| Not in S4 | Where, if anywhere |
|---|---|
| Rank utility, `P(top-10k)`, variance terms, EO-aware captaincy | v2, after a measured top-10k pace series exists (R3 section 4) |
| Sensitivity analysis (randomised reruns scored 3/2/1), scenario files, PSB tables | unscheduled |
| Price change forecasts over the horizon | later; `bounds` says prices are static |
| `ft_use_penalty`, `weekly_hit_limit`, booked transfers, `no_gk_rotation_after`, opposing-play penalties | later; the settings model rejects unknown keys |
| Own points model, engine fill for uncovered rows | never in v1 (D5); uncovered cells are 0 and counted |
| Cancel a running solve | later; the bound is 300 s |
| Dashboard transfer and captain rows reading `solver_plan` | S5 |
| `propose_solve` chat tool over `SolveSettings` and `POST /api/solver/run` | S7 |
| Private my-team reads for purchase prices and the account's FT count | D10; `ft_source` and `purchase_price_source` label the gap |
| Retention of `solver_plan` rows | S5's operations pass runs the `retention` job |

### 13. Size and session plan

About 21 files outside tests, about 3,900 lines with tests.

| Order | File | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0006_solver.py` | 25 |
| 2 | `desk/solver/settings.py` (model, presets, bounds, `resolve()`) | 160 |
| 3 | `desk/solver/inputs.py` (reads, refusals, universe, `SolveInputs`) | 220 |
| 4 | `desk/solver/model.py` (HiGHS build, solve, extract, cuts) | 620 |
| 5 | `desk/solver/scorer.py` (recompute, finance replay, structural checks) | 260 |
| 6 | `desk/solver/artefact.py` (payload models, writer refusals, `state()`) plus the `ARTEFACT_KINDS` row | 200 |
| 7 | `desk/solver/run.py` (`--job`, `--help`, phases, slices, stdout lines) and `desk/jobs/tasks/solve.py` (registry row, the T+2h eligibility query) | 180 |
| 8 | `desk/panels/solver_form.py`, `solver_plan.py`, `solver_status.py`; `desk/web/routers/solver.py` and the policy rows | 330 |
| 9 | `tests/solver/` (fixture loader, tests 1 to 18), `tests/contract/test_solver_panels.py` (19) | 1,100 |
| 10 | `web/src/screens/solver/SolverScreen.tsx`, `SettingsRail.tsx`, `Alternatives.tsx`, `useSolve.ts`; `web/src/components/PlanLedger.tsx`, `MoveRow.tsx`, `RunProgress.tsx` with vitests | 750 |
| 11 | `scripts/feature_check.py` solver line (20), `docs/runbook.md` regen, `docs/adr/0004-solver-defaults.md`, `docs/SPEC.md` | 80 |

Write in that order: model and scorer green on the fixture before any panel, panels green before any screen.

Where a session goes wrong, and the guard:

1. The time limit with no incumbent. HiGHS returns every column at 0 and an empty XI; a session that extracts first writes a plan with no players. Guard: `primal_solution_status` before extraction, test 13 at 1 s, and the writer refuses an XI under 11 codes.
2. The FT potential. Dropping `ft[H]` spends every FT in the last GW; adding `V` as a standing bonus instead of the telescoping difference makes banking free points. Guard: test 8 pins the marginal values and the telescoped sum; test 6 fails on both mistakes.
3. A plan beside the wrong squad or currency. Reading `projection` directly, taking post-autosub slots, or skipping `purchase_tenths` renders a plausible false plan. Guard: `test_pit`; `test_squad_inputs`; test 16; test 20 comparing `squad_before` with `me_squad` on the deployed URL.
4. Mislabelled defaults. `{0.03, 0.21, 0.06, 0.002}` and the ladder are open-fpl-solver's; FPLReview publishes 0.30, 0.10, 0.03, GK 0.03, vice 0.05, FT Value 1.75, decay 0.85, bank 0.10, 300 s, 3 lines (R7 s3 and s4; ARCH section 13). Guard: `test_presets_match_sources` pins both sets under their names; the ADR lists each value with its source.

Risks against the decisions: D6 attributes the bench weights and the FT ladder to FPLReview; R7 section 3 gives FPLReview a different set, and ARCH section 13 resolves it as above. D6 names "hits 0 to 8" while R3 used `-1` for the unconstrained solve; this spec keeps 0 to 8 and always runs the unconstrained line at 8. R3 section 6.2 kept decay at 1.0 and the ladder off on an evidence rule; the advanced fold exposes both and `objective_undiscounted` is reported on every line.


# Part 11. Step S6: Creators pipeline, eight shows, discover to claims

Step 7 of 10. Follows ARCH.md; where ARCH is silent, a line prefixed `DECISION:` records the choice. Spelling is British. Season 2026-27, target deadline GW12. Migration `0007_s6_creators` (S0 to S4 took 0001 to 0006 in build order).

### 1. Goal

When this step is done the worker reads 8 named creators every 4 hours, transcribes their podcast audio on Groq, fetches YouTube captions only when a residential proxy is configured, extracts player calls with quotes and audio offsets on claude-sonnet-5 under a monthly cap, and the owner reads on Admin, per run and per creator, what was seen, transcribed, extracted, skipped and spent. Before this step no creator text exists in the new app and the old Mac transcriber is gone.

### 2. Depends on

| Step | Used by S6 |
|---|---|
| S0 | `job`, `spend` with `reserve(meter, estimate)`, the ledger helper, `Job` and `JOBS` with the `Interval`, `Calendar` and `After` due shapes, `POST /api/admin/jobs/{task}/run` with `confirm` and `params`, `admin_jobs` and `admin_spend`, `desk/db/bucket.py` (put, get, head, delete, presign, list), `raw_payload`, `core/settings.py`, `core/models.py`, `core/prices.py`, `core/prose_style.py`, `scripts/feature_check.py` |
| S1 | `event`, `player`, `pit.latest_at`, the `creator` and `source` rows from the backfill if S1 created them (4.1) |
| S3 | `desk/providers/names.py`: `resolve(name, season) -> int | None`; exact alias, then containment, then given-name prefix, never edit distance |
| S8 | the Admin screen, `DataTable`, `Drawer`, `Breadcrumb`, `StateBox`, `Fold`, `Age`, `Number`, `CiteChip`, `ConfirmButton`, `PlayerCell`, `usePanel`, the `/creators` placeholder |

If S0's Run-now lacks `params`, S6 adds it: a JSON object validated per task with `extra="forbid"`, stored on `job.params`.

### 3. User stories

User role:

1. I open `/creators` and read one sentence saying the board arrives in the next step. Result: a `StateBox` empty state with the copy in section 8.3 and no panel call.
2. Nothing else. A user sees no feed URL, job row, spend figure, transcript or claim in this step; S9 builds the user screen on the rows S6 writes.

Admin role:

3. I open `/admin` and read a Creators card with 8 rows: creator, a status dot per source, newest item, transcript and claim ages, items with a body over total, claims this season. Result: 8 rows, ages in days, warn over 4 days.
4. I read a Feeds table with 15 sources. Result: kind, host, active, last probe age, status and error per row, verbatim.
5. I read a Backlog line and a meters strip. Result: three backlog numbers with units; audio seconds today against 28,800, Anthropic USD this month against 25.00, proxy MB against 2,000, with "no proxy configured" when absent.
6. I read five job rows, `creators_discover`, `creators_transcribe`, `creators_captions`, `creators_extract`, `retention`. Result: status, items seen, done, skipped by reason, rows written, audio seconds, tokens and USD per row; the captions row reads `skipped_no_proxy` without the proxy variables.
7. I click a creator row. Result: the Drawer lists every item newest first, 50 per page, with a `transcribed` chip that is never a default filter.
8. I click an item. Result: transcript provenance, summary bullets, the calls table with quote and `m:ss` offset, the names the resolver refused, an audio player at the offset while the object exists, breadcrumb "Admin / Creators / FPL Harry / episode".
9. I press "Re-extract". Result: `{needs_confirm, estimate, month_spend, month_cap}` first, then a `creators_extract` row with `trigger admin` and `params {item_id, force: true}`.
10. I press "Transcribe now" on a podcast item. Result: a job row with `audio_seconds` over 0 and a USD figure.
11. I switch a feed off. Result: the next discover run counts `items_skipped {inactive: 1}`.
12. I set `MONTHLY_CAP_ANTHROPIC_USD=0` in a test environment and run extract. Result: `refused` with the cap and period total in the note, no model call.

### 4. Data

Migration `0007_s6_creators`: four tables, one bucket prefix. Every timestamp `timestamptz`, session UTC, every key column `NOT NULL`. Player identity is `code`; no table here has an `element_id` column.

#### 4.1 If S1 already created `creator` and `source`

S1's backfill names `creator` and `source` as seed targets. The S6 session runs `alembic history` and `\d creator` first. If the tables exist, migration 0007 alters them to the columns below with `ADD COLUMN IF NOT EXISTS` and keeps the rows; otherwise it creates them. The seed in 4.7 runs at the end either way with `ON CONFLICT (creator_id) DO UPDATE`.

#### 4.2 `creator`

Why: the eight identities by id, so a display string can never become a ninth creator (the old "The FPL Wire - Fantasy Premier League" row).

| Column | Type | Notes |
|---|---|---|
| `creator_id` | text PK | slug, `^[a-z0-9]{4,16}$` |
| `name` | text NOT NULL | display name |
| `initials` | text NOT NULL | 1 to 2 chars for the S9 bars. DECISION: stored, not derived |
| `active` | bool NOT NULL default true | |
| `people` | jsonb NOT NULL | `[{name, entry_id, verified, evidence}]`; `entry_id` null for a brand |
| `updated_at` | timestamptz NOT NULL | |

#### 4.3 `source`

Why: one row per feed or channel with the probe state that makes a dead feed visible.

| Column | Type | Notes |
|---|---|---|
| `source_id` | text PK | `pod_`, `yt_` or `blog_` plus the creator slug |
| `creator_id` | text NOT NULL FK | |
| `kind` | text NOT NULL | check in `('podcast', 'youtube', 'blog')` |
| `url` | text NOT NULL | feed URL, or `https://www.youtube.com/@{handle}/videos` |
| `channel_id` | text NULL | YouTube `externalId`, for identity only |
| `policy` | text NOT NULL | check in `('open', 'excerpt_only')`; `excerpt_only` fetches the article body from the link |
| `active` | bool NOT NULL default true | admin toggle |
| `etag`, `last_modified` | text NULL | conditional GET |
| `last_probe_at` | timestamptz NULL | |
| `last_status` | text NULL | `200`, `304`, `403`, `429`, `timeout`, `blocked`, `parse_error` |
| `last_error` | text NULL | first 300 chars |
| `newest_published_at` | timestamptz NULL | |
| `updated_at` | timestamptz NOT NULL | |

Index `(creator_id)`.

#### 4.4 `item`

Why: one row per publication with its text, transcript and analysis, so level 3 is one read.

| Column | Type | Notes |
|---|---|---|
| `item_id` | text PK | `sha256(source_id + "|" + canonical_key)` hex, 64 chars |
| `source_id`, `creator_id` | text NOT NULL FK | |
| `kind` | text NOT NULL | `podcast`, `youtube`, `article` |
| `title` | text NOT NULL | |
| `url` | text NOT NULL | an `http(s)` link, never a bare GUID |
| `canonical_key` | text NOT NULL | `yt:{video_id}` or `url:{url}` |
| `pair_item_id` | text NULL FK item | the other half of a podcast plus YouTube pair (7.1) |
| `published_at` | timestamptz NOT NULL | from the feed; an unreadable or naive date drops the entry |
| `fetched_at` | timestamptz NOT NULL | |
| `text_source` | text NOT NULL | check in `('description', 'transcript', 'article')` |
| `text`, `text_sha256` | text NOT NULL | description at discover; replaced by the transcript or article |
| `segments` | jsonb NULL | `[{seq, start_s, end_s, text}]` |
| `transcript` | jsonb NULL | `{engine, model, audio_sha256, seconds, covered, calls, usd, language, request_ids}` |
| `enclosure_url` | text NULL | podcast media URL |
| `audio_seconds` | numeric(8,1) NULL | measured at decode |
| `transcribe_attempts` | int NOT NULL default 0 | a 429 does not count |
| `transcribe_error`, `captions_error` | text NULL | last non-429 failure; `no_captions` stops retries, `blocked` does not |
| `analysis` | jsonb NULL | DECISION: the parsed `TranscriptAnalysis` plus `dropped_names` and `style_findings`; S9's `episode_summary` reads `summary` here |
| `analysed_at` | timestamptz NULL | |
| `analysis_model` | text NULL | check `~ '^claude-'` |
| `updated_at` | timestamptz NOT NULL | |

Indexes: unique `(creator_id, canonical_key)`; `(creator_id, published_at DESC)`; partial `(published_at DESC) WHERE analysed_at IS NULL`; partial `(published_at DESC) WHERE kind = 'podcast' AND transcript IS NULL`.

Sample row, a transcribed and extracted podcast episode, long fields cut:

```
source_id: pod_fplharry, kind: podcast, title: "GW12 WILDCARD DRAFT | Salah OUT?",
canonical_key: "url:https://feeds.megaphone.fm/BLU5639728837/ep-412", published_at: 2026-11-10T06:02:00Z,
text_source: transcript, audio_seconds: 2214.6,
segments: [{"seq": 0, "start_s": 0.0, "end_s": 4.2, "text": "Welcome back to the show"}, ...],
transcript: {"engine": "groq-whisper", "model": "whisper-large-v3-turbo", "seconds": 2214.6, "covered": 2201.3,
             "calls": 4, "usd": 0.0246, "language": "en", "request_ids": ["req_01", "req_02", "req_03", "req_04"]},
analysis: {"summary": ["Salah 4.9 xPts GW12 against Arsenal"], "dropped_names": ["the Brighton lad"], "style_findings": 0},
analysed_at: 2026-11-10T10:31:08Z, analysis_model: claude-sonnet-5
```

#### 4.5 `claim`

Why: one row per (item, player, action, gameweek) with the quote and offset; outcome columns are NULL until S9 settles them.

| Column | Type | Notes |
|---|---|---|
| `claim_id` | text PK | `sha256(item_id + "|" + code + "|" + action + "|" + gw)` first 32 hex |
| `item_id`, `creator_id` | text NOT NULL FK | |
| `code` | int NOT NULL | player code |
| `action` | text NOT NULL | check in `('buy', 'sell', 'hold', 'captain', 'avoid', 'bench')`; `watch` is never a claim |
| `season` | text NOT NULL | `2026-27` |
| `gw` | int NOT NULL | stated, else the next unfinished gameweek at `published_at` |
| `gw_inferred` | bool NOT NULL | |
| `confidence` | numeric(2,1) NOT NULL | 0.8, 0.6, 0.4 |
| `quote`, `reasoning` | text NOT NULL | max 400 chars each; reasoning normalised |
| `start_s` | numeric(8,1) NULL | quote search against `item.segments` at write time |
| `extractor` | text NOT NULL | `llm:claude-sonnet-5`; check `~ '^llm:claude-'` |
| `published_at` | timestamptz NOT NULL | copied from the item; the instant S9 scores against |
| `created_at` | timestamptz NOT NULL | |
| `player_points` int, `benchmark_kind` text, `benchmark_points` numeric(5,2), `hit` bool, `unscoreable` text, `resolved_at` timestamptz | NULL | S9 writes them |
| `revision` | int NOT NULL default 0 | S9 |

Indexes: `(creator_id, season, gw)`; `(code, season, gw)`; `(item_id)`.

Sample row:

```
claim_id: 9c4e[28 more hex], item_id: 3f1c[60 more hex], creator_id: fplharry, code: 223094, action: captain,
season: 2026-27, gw: 12, gw_inferred: false, confidence: 0.8,
quote: "Haaland is nailed on for the armband this week, Wolves at home",
reasoning: "Home fixture against the weakest defence in the league.", start_s: 812.4,
extractor: llm:claude-sonnet-5, published_at: 2026-11-10T06:02:00Z, revision: 0
```

#### 4.6 Bucket and `raw_payload`

| Object | Key | Written by | Retention |
|---|---|---|---|
| feed or page body | `raw/creators/{YYYY-MM-DD}/{sha256}.gz` plus a `raw_payload` row (`source = creators`, `endpoint = source_id`, `content_type`) | `creators_discover` | 30 days, deleted by S5's `retention`; S6's counts them |
| podcast audio | `audio/{sha256(enclosure_url)}.mp3` | `creators_discover` | deleted by `retention` when `item.transcript` is set and `analysed_at` is before today 00:00 UTC |

DECISION: the raw suffix is `.gz` with `content_type` on the row, because feeds are XML and watch pages HTML; ARCH's `.json.gz` names API bodies.

#### 4.7 Seed: eight creators, 15 sources

Lifted from the old `fpl_edge/ingest/content/sources.py` lines 137 to 232 and `docs/platform/creator_identity_research.md` (entry ids verified 2026-08-27 against `/api/entry/{id}/` through league-admin evidence). `CREATORS` in `desk/creators/registry.py` is the code copy; the migration seeds from it; `test_creators_walk` asserts table and tuple agree.

| creator_id | name | initials | podcast RSS | YouTube `channel_id`, handle | blog | people (name, entry_id) |
|---|---|---|---|---|---|---|
| `fplharry` | FPL Harry | H | `https://feeds.megaphone.fm/BLU5639728837` | `UCcPWnCj5AKC19HaySZjb25g`, `FPLHarry` | | Harry Daniels 3054 |
| `fplraptor` | FPL Raptor | R | `https://feeds.megaphone.fm/COMG8319298159` | `UC54QLWzsMifTRjNQ02z5pCw`, `FPLRaptor` | | Ross Dowsett 199 |
| `letstalkfpl` | Let's Talk FPL | LT | `https://feeds.megaphone.fm/COMG4898871165` | `UCxeOc7eFxq37yW_Nc-69deA`, `LetsTalkFPL` | | Andy 41 |
| `fplblackbox` | FPL BlackBox | BB | `https://feeds.megaphone.fm/BLU4752868283` | `UCGJ8-xqhOLwyJNuPMsVoQWQ`, `FPLBlackBox` | | Mark Sutherns 252, Az Phillips 246 |
| `fplfran` | FPL Fran | F | none registered | `UCVLnEmwu-Ajei-wvk9SA8rw`, `FPLFran` | | none verified |
| `ffhub` | Fantasy Football Hub | FH | `https://feeds.megaphone.fm/COMG9112865919` | `UCcqEr3DfrRwtoF2a1yW8qgQ`, `FantasyFootballHub` | | Ben Crellin 53517, FPL Salah 70 |
| `fplwire` | The FPL Wire | W | `https://feeds.megaphone.fm/BLU9598812574` | none registered | | Pras 3315, Zophar 2177, Lateriser 6816, BigMan Bakar 5133 |
| `ffscout` | Fantasy Football Scout | SC | `https://feeds.megaphone.fm/BLU3712102693` | `UCVEKeBG9nbkufZxHWVtDHzQ`, `FantasyFootballScout` | `https://www.fantasyfootballscout.co.uk/feed/`, `excerpt_only` | none |

15 sources: 7 podcast, 7 YouTube, 1 blog. `people.evidence` holds the league evidence string, for example `admins league 7735`. Source ids: `pod_fplharry`, `yt_fplharry`, `blog_ffscout`.

Cadence: `creator` and `source` change by migration or the admin toggle; `item` rows arrive every 4 hours and are updated by transcribe, captions and extract; `claim` rows are written by extract and updated by S9. Point-in-time rule: `claim.published_at`, copied from `item.published_at`, is the only instant S9 scores against, and it comes from the feed, never from now.

### 5. Jobs

Five registry rows on the pipeline lane. Four run as one chain, `creators_discover` then `creators_transcribe` then `creators_captions` then `creators_extract`, each `After` the previous with `parent_job_id` and `trigger = chain`; `retention` has its own calendar. DECISION: a linear chain, because captions must know whether a paired podcast was transcribed in the same cycle before spending proxy bytes. The S0 helper counts `rows_written` on the `writes` tables; `items_seen > 0` with `rows_written = 0` and empty `items_skipped` lands as `error`, note `wrote_nothing_unexplained`. Every task answers `desk run-task <name> --help`.

#### 5.1 `creators_discover`

| Field | Value |
|---|---|
| Trigger | `Interval(4h)` aligned to 00:00 UTC; admin Run-now, no confirm; `stale_window` 10 h |
| Inputs | every `source` row; proxy variables optional |
| Podcast and blog | conditional GET with `etag` and `last_modified`; `feedparser`; a missing or naive `published_parsed` drops the entry as `no_date`; link in the order link, atom alternate, guid when `isPermaLink` and `http(s)`, enclosure URL, else `no_link`; `canonical_key = url:{link}`; new rows get `text_source = description` and the stripped summary. Under `excerpt_only`, fetch the linked page (robots.txt per host, cached 24 h, 1.0 s delay), take the text of `<article>` or else every `<p>` under `<main>` or `<body>` with the stdlib `html.parser`; over 800 chars is `kind = article`, `text_source = article`, else `description` with `article_thin` |
| YouTube | GET `https://www.youtube.com/@{handle}/videos`, the route robots.txt permits, through the Webshare proxy when configured, else direct; regex `"videoId":"([\w-]{11})"`, first 15 ids; per new id GET `/watch?v={id}` for `<title>`, `itemprop="datePublished"` and the description; `canonical_key = yt:{id}`. A 403, 429, timeout or "Sign in to confirm" body sets `last_status = blocked` and counts `youtube_blocked: 1`, no retry in the run |
| Audio | per new podcast item: HEAD then GET; content type must start `audio/`, `video/` or `application/octet-stream` else `not_audio`; over 300 MB is `audio_too_large`; stream to `audio/{sha256(enclosure_url)}.mp3`; an existing object is not re-fetched |
| Window | first run per source: the last 30 days; later runs: newer than `newest_published_at` minus 24 h; the unique index makes a repeat `rows_unchanged` |
| Pairing | after inserts, 7.1 sets `pair_item_id` on new rows |
| Outputs | `source` probe columns, `item`, `raw_payload`, audio objects |
| Ledger | `items_seen` = entries parsed; `items_done` = items inserted; `items_skipped` by `no_date`, `no_link`, `inactive`, `youtube_blocked`, `not_audio`, `audio_too_large`, `article_thin`, `robots_disallow`; `rows_written` on `source`, `item`, `raw_payload` |
| Cost cap | none on feeds; `proxy_mb` at 0.5 MB per YouTube page through the proxy, `reserve` before each |
| Timeout | `budget_s` 900; per request 30 s |
| Failure | a failed source is recorded on its row and the run continues; `error` only when every source failed; 304 everywhere is `nothing_to_do`, note `all_sources_304` |
| Silent nothing | the newest item age per creator passes 4 days and turns warn; the feature check fails when `newest_item_at` is over 48 h for 4 or more creators; `admin_jobs` lists the row stale after 10 h |

#### 5.2 `creators_transcribe`

| Field | Value |
|---|---|
| Trigger | `After(creators_discover)`; admin Run-now with confirm, `params {item_id}` optional; `stale_window` 30 h |
| Candidates | `kind = podcast AND transcript IS NULL AND enclosure_url IS NOT NULL AND transcribe_attempts < 3`, newest first, 12 per run; with `item_id`, that item only |
| Per item | get the audio object; decode with PyAV to 16 kHz mono float32 and measure `audio_seconds` (DECISION: `av` is added to `pyproject.toml`; it bundles the FFmpeg libraries); `reserve("groq_audio_s", audio_seconds)`; chunk by 7.2; POST each chunk to `https://api.groq.com/openai/v1/audio/transcriptions` as `audio/wav` with `model=whisper-large-v3-turbo`, `response_format=verbose_json`, `language=en`; coverage guard; write `segments`, `transcript`, `text`, `text_source = transcript`, `text_sha256`, `audio_seconds`, `transcribe_attempts + 1` |
| Outputs | `item.segments`, `item.transcript`, `item.text`, `item.text_source` |
| Ledger | `items_seen`, `items_done`, `items_skipped` by `rate_limited`, `partial_coverage`, `decode_failed`, `audio_missing`, `gave_up`; `audio_seconds` = seconds sent; `usd = audio_seconds / 3600 * 0.04`; `rows_written` on `item` |
| Cost cap | `DAILY_CAP_GROQ_AUDIO_S` 28,800 on meter `groq_audio_s` (day); a mid-run refusal stops the run with `status refused`, note `groq_audio_s cap 28800 reached: today 27140 + 2214 requested; 3 items done this run` |
| Timeout | `budget_s` 3,600; per chunk request 300 s |
| Failure | a 429 raises `HostedRefused`, stores nothing, leaves attempts alone, counts `rate_limited`, sleeps 20 s; three in a row stop the run, note `rate_limited_x3`. Any other 4xx, 5xx, decode or coverage failure increments attempts, sets `transcribe_error`, stores nothing |
| Silent nothing | the newest transcript age per creator; the backlog's audio hours never fall; `audio_seconds 0` with `items_seen` over 0 and no skip reason is `error`; the feature check fails when `newest_transcript_at` is over 48 h for 4 or more creators |

#### 5.3 `creators_captions`

| Field | Value |
|---|---|
| Trigger | `After(creators_transcribe)`; admin Run-now with confirm; `stale_window` 30 h |
| Candidates | `kind = youtube AND text_source = 'description' AND captions_error IS NULL`, last 14 days, newest first, 20 per run; an item whose `pair_item_id` row has a transcript is counted `paired` and not fetched |
| Proxy gate | when `WEBSHARE_PROXY_USERNAME` or `WEBSHARE_PROXY_PASSWORD` is absent the run writes `status skipped_no_proxy`, note `WEBSHARE_PROXY_USERNAME unset`, `items_seen` = candidates, `items_skipped {no_proxy: n}`, no request |
| Per item | `reserve("proxy_mb", 0.25)` (DECISION: 0.25 MB per fetch; the library reports no byte count); `YouTubeTranscriptApi(proxy_config=WebshareProxyConfig(username, password)).fetch(video_id, languages=["en"])`; segments from snippets `{start, duration}`; write `segments`, `transcript {engine: "youtube-captions", model: null, seconds: null, covered: last end, calls: 1, usd: 0}`, `text`, `text_source` |
| Outputs | `item.segments`, `item.transcript`, `item.text`, `item.text_source`, `item.captions_error` |
| Ledger | `items_seen`, `items_done`, `items_skipped` by `paired`, `no_proxy`, `blocked`, `no_captions`; `rows_written` on `item` |
| Cost cap | `MONTHLY_CAP_PROXY_MB` 2,000 on meter `proxy_mb` (month); refusal as in 5.2 |
| Timeout | `budget_s` 900; per fetch 60 s |
| Failure | `RequestBlocked` or `IpBlocked` counts `blocked`, leaves `captions_error` NULL for a retry next cycle, three in a row stop the run with note `blocked_x3`; `NoTranscriptFound` or `TranscriptsDisabled` sets `captions_error = no_captions` |
| Silent nothing | the captions row shows `skipped_no_proxy` or `items_done 0`; the feature check asserts the newest captions row is `ok` or `skipped_no_proxy` |

#### 5.4 `creators_extract`

| Field | Value |
|---|---|
| Trigger | `After(creators_captions)`; admin Run-now with confirm, `params {item_id, force, window_days}` optional; `stale_window` 30 h |
| Candidates | `analysed_at IS NULL AND published_at > now() - window_days` (default 21, max 400), any `text_source`, newest first, 20 per run; `force: true` with `item_id` re-runs one analysed item |
| Gate | `text_source = 'description'` counts `no_body` before any call; `analysed_at` stays NULL so the item is seen again once a transcript lands |
| Per item | estimate `usd = (len(text) / 4 + 1500) * 2e-6 + 2000 * 10e-6`; `reserve("anthropic_usd", estimate)`; one call per 7.3; `normalize_prose` over `summary`, `reasoning`, `claim_text`; `slop_findings` count into `analysis.style_findings`; claims per 7.4; `start_s` per 7.5; write `analysis`, `analysed_at`, `analysis_model`, claims |
| Re-extract | with `force`, claims with `resolved_at IS NULL` are deleted and rewritten; settled claims stay and a colliding `claim_id` is `rows_unchanged` |
| Outputs | `claim` rows, `item.analysis`, `item.analysed_at`, `item.analysis_model` |
| Ledger | `items_seen`, `items_done`, `items_skipped` by `no_body`, `parse_failed`, `api_error`; `tokens_in` (input plus cache read plus cache write), `tokens_out`, `usd` from `core/prices.py`, `model`; `rows_written` on `claim` and `item`; note carries `claims_written` and `names_dropped` |
| Cost cap | `MONTHLY_CAP_ANTHROPIC_USD` 25 on meter `anthropic_usd` (month), shared with the S5 brief; a mid-run refusal stops the run with `status refused`, note `anthropic_usd cap 25.00 reached: month 24.98 + 0.04 requested; 6 items done this run` |
| Timeout | `budget_s` 1,800; per call 600 s |
| Failure | an API error counts `api_error`, writes nothing, retries next cycle; a body failing the schema counts `parse_failed` and sets `analysed_at` with `analysis {parse_failed: true, raw: <first 2000 chars>}` so it waits for `force`; zero claims from a transcript is `items_done + 1` with the analysis stored |
| Silent nothing | the newest claim age and claims this season per creator; the backlog's extract count never falls; `tokens_in 0` with `items_seen` over 0 and no skip reason is `error`; the feature check fails when `newest_claim_at` is over 48 h for 4 or more creators |

#### 5.5 `retention` (S6 scope)

| Field | Value |
|---|---|
| Trigger | Calendar `0 4 * * *` UTC; `stale_window` 26 h |
| Rule | delete `audio/{sha}.mp3` where the owning item has `transcript IS NOT NULL AND analysed_at < date_trunc('day', now())`; count `raw/creators/` objects and bytes without deleting (S5 extends this same registry row) |
| Outputs | bucket deletes; note `{"audio": {"deleted", "bytes_freed", "remaining", "bytes_remaining"}, "raw_creators": {"objects", "bytes"}}` |
| Ledger | `items_seen` = audio objects listed; `items_done` = deleted; `items_skipped {retained: n}` always written; `rows_written` 0 by construction |
| Cost cap, timeout | none; `budget_s` 600 |
| Failure | a listing error is `error`; a partial delete records what was freed |
| Silent nothing | the backlog line's "audio held" bytes; the feature check fails when the newest `retention` row is older than 26 h |

### 6. API and panels

Every panel is a `PANELS` row served at `GET /api/panels/{name}` in the standard envelope `{ok, panel, result | null, empty, provenance}`. Errors: 400 `{error: "invalid_params", field, detail}`; 401 `{error: "not_signed_in"}`; 403 `{error: "forbidden"}`; 404 `{error: "not_found", kind, id}`; 500 `{error: "panel_failed", panel}`. Budget 10 s; each panel declares `budget_ms` and reports `over`. All three panels are admin tier, so none is a chat tool. S9 adds the session-tier `creators_board`, `player_chatter`, `creator_episodes` and `episode_summary` over these tables; those become tools in S7.

#### 6.1 `admin_creators` (admin)

Params: none. `budget_ms` 2,000. Reads `creator`, `source`, `item`, `claim`, `job`, `spend`; no bucket call at request time (audio bytes come from the newest `retention` note).

```
{ as_of: str,
  meters: [ { meter: "groq_audio_s" | "anthropic_usd" | "proxy_mb", period: "day" | "month",
              used: number, cap: number, configured: bool } ],
  creators: [ { creator_id, name, initials, active,
                sources: [ { source_id, kind, active, last_status, last_probe_at, last_error } ],
                items_total: int, items_with_body: int,
                awaiting_transcript: int, awaiting_captions: int, awaiting_extract: int,
                newest_item_at: str | null, newest_transcript_at: str | null, newest_claim_at: str | null,
                claims_season: int, people: [ { name, entry_id: int | null, verified: bool } ] } ],
  backlog: { transcribe_items: int, transcribe_audio_s: number, transcribe_usd_est: number,
             captions_items: int, extract_items: int, extract_usd_est: number,
             audio_held_bytes: int | null, audio_held_as_of: str | null },
  runs: [ { task, job_id, trigger, status, started_at, finished_at, items_seen, items_done,
            items_skipped: object, rows_written, audio_seconds, tokens_in, tokens_out, usd, note } ] }
```

`runs` holds the newest row per S6 task. `transcribe_usd_est = transcribe_audio_s / 3600 * 0.04`; `extract_usd_est` sums the 5.4 estimate over items with a body. Provenance `inputs`: `creator`, `source`, `item`, `claim`, `job:{job_id}` per run row. Empty only on a database with no `creator` row: `Empty(reason="no_creators", fix="Run migration 0007")`.

#### 6.2 `admin_creator_items` (admin)

Params: `creator_id` (required), `limit` (default 50, max 200), `before` (timestamptz, paging), `kind` (podcast, youtube, article). `budget_ms` 1,500.

```
{ creator: { creator_id, name },
  items: [ { item_id, source_id, kind, title, url, published_at, text_source,
             has_transcript: bool, transcript_engine: str | null, audio_seconds: number | null,
             analysed_at: str | null, claims_n: int, pair_item_id: str | null,
             transcribe_attempts: int, transcribe_error: str | null, captions_error: str | null } ],
  total: int, next_before: str | null }
```

Unknown `creator_id` is 404; a creator with no items returns `items: []`, `total: 0`, not `empty`. Provenance `inputs`: `creator`, `item`, `claim`.

#### 6.3 `admin_item` (admin)

Params: `item_id` (64 hex, required). `budget_ms` 1,500.

```
{ item: { item_id, creator_id, creator_name, source_id, kind, title, url, published_at, fetched_at,
          text_source, text_chars: int, enclosure_url, audio_seconds, pair_item_id,
          transcribe_attempts, transcribe_error, captions_error, analysed_at, analysis_model },
  transcript: { engine, model, audio_sha256, seconds, covered, calls, usd, language, segments_n: int } | null,
  analysis: { summary: [str], chip_advice: [ { chip, stance, gameweek, reasoning, quote } ],
              insights: [ { topic, entity_kind, entity_name, claim_text, quote, horizon_gw, horizon_gw_end, conviction } ],
              dropped_names: [str], style_findings: int, parse_failed: bool } | null,
  claims: [ { claim_id, code, web_name, position, team_code, action, gw, gw_inferred, confidence,
              quote, reasoning, start_s: number | null, deep_link: str | null, resolved_at: str | null } ],
  audio_url: str | null, audio_expires_at: str | null }
```

`audio_url` is a presigned GET on `audio/{sha}.mp3` valid 3,600 s while the object exists. `deep_link` is `{url}&t={int(start_s)}s` for YouTube items and null otherwise (a podcast page ignores a fragment). Unknown `item_id` is 404. Provenance `inputs`: `item:{item_id}`, `claim`, `player`.

#### 6.4 Writes

| Method, path | Role | Body | Result |
|---|---|---|---|
| `POST /api/admin/creators/sources/{source_id}` | admin, CSRF | `{active: bool}` | `{source_id, active, updated_at}`; 404 on an unknown id |
| `POST /api/admin/jobs/creators_transcribe/run` | admin, CSRF | `{confirm: bool, params: {item_id?}}` | without `confirm`: `{needs_confirm: true, estimate, meter: "groq_audio_s", day_spend, day_cap, month_spend, month_cap}`; with: `{job_id}` |
| `POST /api/admin/jobs/creators_extract/run` | admin, CSRF | `{confirm, params: {item_id?, force?, window_days? (1 to 400)}}` | as above with `meter: "anthropic_usd"` |
| `POST /api/admin/jobs/creators_captions/run` | admin, CSRF | `{confirm}` | as above with `meter: "proxy_mb"`; without the proxy: `{needs_confirm: false, will_skip: "skipped_no_proxy"}` and the job still runs so the row is visible |
| `POST /api/admin/jobs/creators_discover/run`, `.../retention/run` | admin, CSRF | `{}` | `{job_id}` |

Estimates: transcribe uses `transcribe_usd_est`, or `audio_seconds / 3600 * 0.04` for one item, or 0.03 when unknown; extract sums the 5.4 formula over candidates. A bad `params` body is 400.

### 7. Algorithms

#### 7.1 Pairing a podcast with its YouTube upload

The old `canonical_key` (`urls.py`) gives `yt:{id}` for YouTube and `url:{link}` for podcasts, so it cannot join a pair. DECISION: two rows linked by `pair_item_id`, matched after each discover run:

```
for each new item n of creator c:
  candidates = items of c with kind != n.kind, |published_at - n.published_at| <= 48 h, pair_item_id IS NULL
  t(x) = tokens of lower(title) with [^a-z0-9 ] removed, minus {fpl, gw, gameweek, the, a, of, and, ep, episode, podcast, live, show, vs, v}
  score(m) = size(intersection(t(n), t(m))) / size(union(t(n), t(m)))
  pair when best >= 0.6 and no second candidate reaches 0.6; set pair_item_id both ways
```

48 h because episodes reach YouTube and RSS on the same day; 0.6 because two shared player names plus a gameweek reach 0.6 on old corpus titles while a different episode of the same week sits under 0.4. Validation: 12 old-corpus titles, 6 true pairs and 6 same-week non-pairs, give 6 pairs and 0 false pairs. S9 counts distinct votes on `(creator_id, code, action, gw)`, so a missed pair costs a duplicate vote at worst.

#### 7.2 Audio chunking and the coverage guard

Lifted from the worktree `asr_hosted.py` and `asr.py`:

```
SAMPLE_RATE = 16000; CHUNK_SECONDS = 600; MAX_UPLOAD_BYTES = 24 * 1024 * 1024
span = min(CHUNK_SECONDS * SAMPLE_RATE, (MAX_UPLOAD_BYTES - 44) // 2)   # 9,600,000 samples, 19.2 MB
bounds = [(s, min(s + span, n)) for s in range(0, n, span)]
for (s, e) in bounds: POST 16-bit PCM WAV of samples[s:e]; each segment start_s, end_s += s / SAMPLE_RATE
covered = max(end_s); duration = n / SAMPLE_RATE
refuse when covered < 0.80 * duration and duration - covered > 90
```

600 s and 24 MiB sit under Groq's 25 MB free-tier ceiling (R7 section 8). 0.80 and 90 s are the old `MIN_COVERAGE` and `MAX_UNCOVERED_S`: a 3 minute clip with a 45 s outro passes, a transcript stopping at 40 percent is refused. Offsets come from the sample index, never from the provider's `duration`. Validation: tests 9 and 10.

#### 7.3 The extraction call

`desk/creators/prompt.md` is the old `_SYSTEM` plus `_INSIGHT_RULES` (`analyze.py` lines 302 to 388) verbatim, plus `STYLE_RULES` from `core/prose_style.py` under a heading `STYLE`. `_NOTES_PREAMBLE` is not carried; the `no_body` gate means show notes are never sent. The user turn is `Creator: {name}\nTitle: {title}\n\n{Transcript | Article}:\n{text}`, `text` cut at 120,000 chars (the longest old episode, 107 minutes, fits). The response model is the old `TranscriptAnalysis` (lines 207 to 300) copied to `desk/creators/extract.py`: `summary` 3 to 6 strings; `transfers_in`, `transfers_out`, `captaincy`, `differentials` as `PlayerCall {player, stance, conviction, gameweek, reasoning, quote}`; `chip_advice` as `ChipCall`; `insights` as `Insight`.

```
client = anthropic.Anthropic(api_key=OPERATOR_ANTHROPIC_API_KEY, timeout=600)
resp = client.messages.parse(
  model=MODELS.extract,      # "claude-sonnet-5"; core/models.py is the only module naming it
  max_tokens=16000,
  system=[{"type": "text", "text": PROMPT, "cache_control": {"type": "ephemeral"}}],
  messages=[{"role": "user", "content": user_turn}],
  output_format=TranscriptAnalysis)
```

DECISION: `messages.parse` with `output_format` is the structured-output call the old repo used on the 1.x SDK. If the pinned SDK exposes no `parse`, use `messages.create` with one tool `record_analysis` whose `input_schema` is `TranscriptAnalysis.model_json_schema()` and `tool_choice = {"type": "tool", "name": "record_analysis"}`, validated by the same model. The prompt exceeds 1,024 tokens, so the cache breakpoint is live on Sonnet (R7 section 2). Prices in `core/prices.py`: input 2.00, output 10.00, cache read 0.20, cache write 2.50 USD per MTok. Validation: `test_model_pins`; a stub raising `anthropic.APIStatusError` lands `api_error` with `analysed_at` NULL.

#### 7.4 Calls to claims

Lifted from `claims_from_analysis` (`analyze.py` lines 888 to 960):

```
STANCE_TO_ACTION = {buy: buy, sell: sell, hold: hold, captain: captain, avoid: avoid, bench: bench}   # watch dropped
CONVICTION_CONF  = {high: 0.8, medium: 0.6, low: 0.4}
calls = transfers_in + transfers_out + captaincy + differentials
for call in calls:
  if call.stance not in STANCE_TO_ACTION: continue
  code = names.resolve(call.player, season)     # None on ambiguity; never a guess
  if code is None: dropped_names.append(call.player); continue
  gw = call.gameweek or next_unfinished_gw_at(item.published_at); gw_inferred = call.gameweek is None
  key = (code, action, gw); skip when seen
  claim_id = sha256(f"{item_id}|{code}|{action}|{gw}")[:32]
```

`next_unfinished_gw_at(t)` is the smallest `event.gw` with `deadline_at > t`. A stated `gameweek` below the current or above 38 counts as absent. Validation: test 15; "Martin Odegaard" resolves to 184029 and never 154561.

#### 7.5 `start_s` by quote search

```
norm(s) = lower(s) with [^a-z0-9 ] -> " " and space runs collapsed
text = " ".join(norm(seg.text)); offsets[i] = char position where segment i starts
probes = [first 60 chars of norm(quote), first 40 chars, longest 6-token window]
pos = first text.find(probe) that is not -1; start_s = segments[bisect_right(offsets, pos) - 1].start_s
none found -> NULL
```

Validation: the old measured episode found 42 of 45; the fixture asserts 8 of 9 within 0.1 s and the ninth NULL. An article has no segments, so every `start_s` is NULL.

#### 7.6 Reservations

`reserve(meter, estimate)` runs before every metered request; the real amount replaces the estimate on the job row. Groq: 0.04 USD per audio hour, seconds, day, cap 28,800. Anthropic: the 5.4 formula, USD, month, cap 25. Proxy: 0.25 MB per caption fetch, 0.5 MB per YouTube page, month, cap 2,000. Validation: tests 12 and 16.

### 8. UI

Shared components: `DataTable`, `FilterRow`, `RowCount`, `Drawer`, `Breadcrumb`, `StateBox`, `Fold`, `Age`, `Number`, `CiteChip`, `ConfirmButton`, `PlayerCell`, shadcn `Badge`, `Switch`, `Tooltip`, `Skeleton`. New in the design system: none. The audio player is the browser `<audio controls>` element with `currentTime` set from `start_s`, wrapped once in `web/src/screens/admin/AudioAt.tsx`; a grep test allows one `<audio` under `web/src`.

#### 8.1 Admin, `/admin` (extended)

Primary visual: the S0 jobs board, unchanged, with five more rows. Under it one new `Fold` "Creators", open by default, summary when closed: "Creators: 8 shows, newest transcript 1 d, newest claim 1 d, 14 items awaiting extraction". Inside, in order:

1. Meters strip: "Audio today 3,120 / 28,800 s", "Anthropic this month 3.12 / 25.00 USD", "Proxy this month 0 / 2,000 MB", with a `Badge` "no proxy configured" when `configured` is false. Warn token at 80 percent, bad at 100.
2. Backlog line: "3 podcast items await transcription (2.1 audio h, 0.08 USD). 14 items await extraction (0.53 USD). 6 YouTube items await captions. Audio held 2.1 GB as of 1 d."
3. Creators table, `DataTable`, 8 rows. Columns: Creator (name, initials `Badge`); Sources (one dot per source: good for `200` or `304`, warn for `blocked` or `timeout`, bad for `403`, `429`, `parse_error`, muted when inactive; `source_id` and status in a `Tooltip`); Newest item, Newest transcript, Newest claim (`Age` each); Body ("38 / 71"); Claims. Default sort: Newest claim descending, nulls last. No filter row. `secondary` under 820 px: Sources, Body. A row opens the creator drawer.
4. Feeds table in a closed `Fold`, summary "Feeds: 15, 14 ok, 1 blocked". Columns: Source, Creator, Kind, Host, Active (`Switch`), Last probe (`Age`), Status, Error (first 80 chars, full in a `Tooltip`). Default sort: Status ascending so failures lead. The `Switch` calls the toggle route and shows a `Skeleton` on the cell until the response.

Run-now copy, exact: transcribe, "Transcribe 3 items now? Estimate 0.08 USD, 2.1 audio hours. Today 3,120 of 28,800 s." with "Run" and "Cancel"; extract, "Extract 14 items now? Estimate 0.53 USD. This month 3.12 of 25.00 USD."; captions without a proxy, "No proxy configured. The run will record skipped_no_proxy." with "Run anyway" and "Cancel".

Level 2, the creator drawer at `/admin/creators/{creator_id}`: `Breadcrumb` "Admin / Creators / FPL Harry"; the people line "Harry Daniels, entry 3054, verified"; a `DataTable` from `admin_creator_items` with Title, Kind, Published (`Age`), Text (`Badge`), Transcribed (`Badge` with the engine, blank when none), Analysed (`Age` or blank), Claims. Default sort: Published descending. Filter row: Kind (all, podcast, youtube, article; default all), Text (all, has body; default all). "Load 50 more" when `next_before` is set. `secondary` under 820 px: Kind, Transcribed. A row opens level 3.

Level 3, the item drawer at `/admin/creators/{creator_id}/i/{item_id}`: `Breadcrumb` "Admin / Creators / FPL Harry / episode"; the title linking to `url`; a provenance line "podcast, published 1 d, transcript groq-whisper whisper-large-v3-turbo, 36.9 min, 99 percent covered, 0.025 USD, 4 calls" with a `CiteChip`; `ConfirmButton`s "Transcribe now" (podcast items without a transcript) and "Re-extract" (items with a body); the audio element when `audio_url` is set; "Summary" as bullets; "Calls, 9 players" as a `DataTable` with Player (`PlayerCell`), Action (`Badge`), GW, Conf, Quote, At (an `m:ss` button that seeks the audio, or the `deep_link` for YouTube); "Names not resolved, 1" and "Chips and insights, 4" as closed `Fold`s. Every level is a URL that reloads to the same state.

Empty states, exact copy: no creator row, "No creators seeded. Run migration 0007."; no items, "No items yet for FPL Harry. Discover runs every 4 hours."; no analysis, "Not extracted yet. Extract runs after each discover cycle."; no transcript, "No transcript. Podcast audio is transcribed after discover; YouTube captions need the proxy." Loading: `Skeleton` at final geometry, 8 rows of 40 px for creators, 10 for items. Error: `StateBox` error only on a thrown request, "Creators card failed to load ({status}). Retry" with a "Retry" button. Stale: any `Age` over 4 days carries a warn token.

Mobile at 390 px: the fold and drawers are full width; the meters strip stacks; the creators table hides Sources and Body into the row drawer; the feeds table hides Host and Error; no horizontal scroll.

#### 8.2 The jobs board (S0, unchanged)

The five rows arrive through the registry with no S6 UI code. The captions row prints `skipped_no_proxy` in the warn token with its note in a `Tooltip`.

#### 8.3 `/creators` (user, placeholder)

S8's `StateBox` empty state with this copy: "The creators board lands in the next step. Eight shows are being read now." No panel is called. S9 replaces it and its PR carries the surface inventory.

### 9. Admin view

The admin sees the five job rows with last run, next due, status, counts, audio seconds, tokens and USD; the Creators fold with meters, backlog, the creators and feeds tables; the creator and item drawers with transcripts, analyses, claims, dropped names and the audio player; Run now with confirm on the three metered tasks and without on discover and retention; the log tail per job; the active toggle per feed.

A user never sees a job row, a spend figure or cap, a feed URL or probe status, a transcript, an analysis, a claim, a presigned audio URL, a people entry id, or the operator variable name. All three panels are admin tier; `test_routes_matrix` asserts 403 for a session user and 401 for a stranger on each and on the toggle. S9 decides what becomes user-visible, through session-tier panels serving quotes, offsets and summaries.

### 10. Acceptance tests

In the order to write them.

1. `test_creators_migration`: upgrade on an empty database creates the four tables with the section 4 columns and indexes, seeds 8 creators and 15 sources, and `downgrade -1` removes them; where S1 created `creator` and `source`, upgrade keeps the rows and adds the missing columns.
2. `test_creators_walk`: `CREATORS` has 8 rows with unique ids, initials of 1 or 2 chars, every URL starting `https://`, every `people.entry_id` an int or null, and the table matches the tuple row for row.
3. `test_jobs_walk_s6`: the five tasks are registry rows with `writes`, `stale_window`, a reader panel per written table, a unit test file each, and `--help` output.
4. `test_discover_feed_fixture`: the FPL Harry feed fixture (12 entries, 3 without a readable date) inserts 9 items with `no_date: 3`, every `url` `http(s)`; a second run on identical bytes writes 0 rows with `rows_unchanged 9`.
5. `test_discover_naive_date_dropped`: an RFC 822 date without an offset is dropped and counted; no row carries `published_at` equal to now.
6. `test_discover_article_body`: the Scout feed plus a stubbed 2,000-char page yields `kind = article`; a 400-char page yields `description` and `article_thin: 1`.
7. `test_discover_youtube_blocked`: a stubbed `/videos` body containing "Sign in to confirm" sets `last_status = blocked`, counts `youtube_blocked: 1`; the run is `ok` because podcast sources wrote rows.
8. `test_pairing_titles`: the 12-title fixture yields 6 pairs and 0 false pairs, `pair_item_id` set both ways.
9. `test_chunk_bounds_and_offsets`: a 1,250 s array gives bounds of 600, 600 and 50 s; a stub returning `start 1.0` on the second chunk stores `start_s 601.0`.
10. `test_coverage_guard`: segments to 700 s on 1,250 s raise `PartialTranscript`, nothing stored, `transcribe_attempts 1`; to 1,170 s stores `covered 1170.0`.
11. `test_transcribe_429_leaves_item_queued`: a stubbed 429 counts `rate_limited: 1`, leaves `transcript` NULL and `transcribe_attempts 0`; the run is `ok` with the count visible.
12. `test_transcribe_refused_under_cap`: `DAILY_CAP_GROQ_AUDIO_S=0` gives `refused` with the cap and day total in `note`, zero transport calls, never `ok`.
13. `test_captions_skipped_no_proxy`: with the proxy variables unset the run is `skipped_no_proxy`, `items_seen 6`, `items_skipped {no_proxy: 6}`, the library never imported; with both set and a stubbed `fetch`, one item lands `engine = youtube-captions` and a paired item counts `paired: 1`.
14. `test_extract_no_body_gate`: three description-only items and one transcript give `items_seen 4`, `items_done 1`, `no_body: 3`, one model call; the three keep `analysed_at` NULL.
15. `test_extract_fixture_claims_with_start_s`: the Harry GW12 fixture yields the 9 claims in `expected_claims.json` with `code`, `action`, `gw`, `confidence`, `extractor llm:claude-sonnet-5`, 8 `start_s` within 0.1 s, one NULL, `dropped_names ["the Brighton lad"]`, `style_findings 0`; the row reads `tokens_in > 0`, `usd > 0`, `model claude-sonnet-5`.
16. `test_extract_refused_under_cap`: `MONTHLY_CAP_ANTHROPIC_USD=0` gives `refused`, the note names the cap and month total, the stub records zero calls.
17. `test_extract_silent_nothing_is_error`: a task stub that sees 4 items with bodies, makes no call and records no skip reason lands `error`, note `wrote_nothing_unexplained`.
18. `test_retention_audio_rule`: three audio objects, one analysed yesterday, one analysed today, one untranscribed; only the first is deleted, `items_skipped {retained: 2}`, `bytes_freed` over 0.
19. `test_admin_creators_contract`: on the seeded Postgres `admin_creators` validates with `extra="forbid"`, 8 creators, 3 meters, 5 runs, `provenance.inputs` naming the five sources, snapshot `contracts/panels/admin_creators.json` matching; on an empty database the envelope is `empty {reason: "no_creators"}`; a session user gets 403 and a stranger 401 on all three panels and the toggle.
20. `test_admin_screen_creators` (Playwright): `/admin` mounts with at most 3 panel calls under 250 KB; the fold shows 8 rows; a click opens `/admin/creators/fplharry`; a second opens `/admin/creators/fplharry/i/{item_id}` with the breadcrumb; reload keeps the state; no horizontal scroll at 390; no `\b\d+\s*h\b` age string; both themes.
21. `test_feature_check_s6` (probe against `PUBLIC_URL`): line `creators` asserts 8 creators; `newest_transcript_at`, or `newest_item_at` with `text_source = article`, under 48 h for 5 of 8; `newest_claim_at` under 48 h for 5 of 8; the newest captions row `ok` or `skipped_no_proxy`; the newest `retention` row under 26 h; `provenance.git_sha` equals the deploy.

### 11. Definition of done

- [ ] Tests 1 to 21 green locally and in CI; `ruff`, `mypy --strict desk`, `tsc --noEmit`, eslint, `vite build`, contract drift and the prose checker green, the checker now covering `desk/creators/prompt.md`.
- [ ] `docs/runbook.md` regenerated with the five rows; sync test green.
- [ ] Migration `0007_s6_creators` applied on Railway; `/api/health` reports `alembic_head == expected_head`; `admin_creators` shows 8 creators and 15 sources.
- [ ] `GROQ_API_KEY` set on the worker; `WEBSHARE_PROXY_*` set, or absent with the captions row reading `skipped_no_proxy`.
- [ ] `scripts/backfill.py --only creators` has mapped the 1,070 old items and 3,381 old claims, `item_id` recomputed from `(source_id, canonical_key)`, old `claim_id` values kept; a second run writes 0 rows.
- [ ] Deployed on `main`; `deploy-check.yml` green on every line: health, me, fixtures, projections, solver, admin, assets, ui budget, creators.
- [ ] One full chain has run on schedule on Railway: discover `ok`, transcribe `ok` with `audio_seconds > 0`, captions `ok` or `skipped_no_proxy`, extract `ok` with `tokens_in > 0`; one audio object deleted by `retention` the next night.
- [ ] Owner's demo, before GW12: sign in as admin; open `/admin`; read the meters and backlog line; read the job rows' items seen, audio minutes, tokens and USD; read `skipped_no_proxy` on the captions row; click FPL Harry, read the item list with `transcribed` chips; click the newest transcribed episode, read the bullets and calls table, press an `m:ss` button and hear the audio from that second; press "Re-extract", read the estimate and month spend, cancel; switch `blog_ffscout` off and on; in a local shell run `MONTHLY_CAP_ANTHROPIC_USD=0 desk run-task creators_extract` and read `refused`.
- [ ] Docs: `docs/SPEC.md` S6 block marked done with the sha; `docs/runbook.md`; `docs/adr/0004-creators-sources-and-pairing.md` (the eight by id, the two absent sources, the pairing rule, the YouTube discovery route and proxy fallback, the iTunes probe result for FPL Fran); `README.md` env section listing `GROQ_API_KEY` and the proxy variables.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src scripts` returns nothing.

### 12. Out of scope

| Not in S6 | Where, if anywhere |
|---|---|
| Settlement, hit rates, Wilson intervals, `creator_score`, consensus, the said-versus-owned matrix, the three-level user screen, session-tier creator panels and their chat tools | S9 |
| Creators' own picks and history (`creator_pick`, `creator_gw`) | S1's `squad_refresh` writes them from `creator.people`; S6 reads nothing from them |
| A ninth creator, AllAboutFPL, Planet FPL, or any source outside the 15 | none; D7 fixes the eight; adding one is a migration plus an ADR |
| Pasting a YouTube link by hand | v2 after S9; it needs the proxy and a user form |
| The lexical cue extractor | dropped; 537 cue claims measured 0.490 and titles no longer vote |
| Insights as a table or screen | folded into `item.analysis.insights`; S9 shows them in the episode fold |
| Person-level attribution inside multi-host shows | v2; R4 section 5.3 |
| The Batch API for extraction | rejected in ARCH section 13; 5 items a day |
| Diarisation, word timestamps, overlapping chunks | none; a 60-char probe found 42 of 45 |
| Deleting `raw/` bodies and the other prefixes | S5's `retention`, which extends this row |
| FPL egress through the proxy | S0's ADR 0001; S6 meters proxy use for YouTube only |

Digest facts that pull against a decision, and the resolution. D4 says all eight creators publish podcasts or have RSS; the old registry has no podcast for FPL Fran and no YouTube channel for The FPL Wire, so Fran is captions-only until a feed exists and the 5-of-8 feature check tolerates her; the S6 session runs one iTunes Search query (`https://itunes.apple.com/search?media=podcast&term=FPL+Fran`), records the answer in ADR 0004, and a real feed becomes a 16th source by migration. ARCH section 5 says a podcast plus YouTube pair is one item through the canonical key; the old key cannot join them, so 7.1 pairs two rows and S9 counts distinct votes. The old `feeds.py` refused `feedparser` over defaulted dates; ARCH picks it and 5.1 keeps the two rules that mattered. R7 recommendation 8 puts audio on the Mac; D4 and ARCH put it on Groq from the worker, which this spec builds.

### 13. Size and session plan

About 16 source files and 2,300 lines, plus 14 test files and 1,300 lines. In this order:

| # | File | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0007_s6_creators.py` (four tables, the S1 branch, the seed) | 160 |
| 2 | `desk/creators/registry.py` (`CREATORS`, `SOURCES`, dataclasses) | 120 |
| 3 | `desk/creators/rss.py` (feedparser wrap, link resolution, article body, robots cache) | 180 |
| 4 | `desk/creators/youtube.py` (DECISION: new module; `/videos` listing, watch page, blocked detection) | 120 |
| 5 | `desk/creators/pairing.py` (DECISION: new module; 7.1) | 60 |
| 6 | `desk/creators/audio.py` (bucket get, PyAV decode, `chunk_bounds`, `wav_bytes`) | 110 |
| 7 | `desk/creators/transcribe.py` (Groq transport, response reader, coverage guard, `HostedRefused`) | 170 |
| 8 | `desk/creators/captions.py` (proxy gate, library wrap) | 80 |
| 9 | `desk/creators/prompt.md`; `desk/creators/extract.py` (models, the call, claims, `start_s`) | 90, 260 |
| 10 | `desk/jobs/tasks/creators_discover.py`, `creators_transcribe.py`, `creators_captions.py`, `creators_extract.py`, `retention.py` | 160, 150, 100, 170, 80 |
| 11 | `desk/panels/admin_creators.py`, `admin_creator_items.py`, `admin_item.py` | 180, 90, 120 |
| 12 | `desk/web/routers/admin_creators.py` (the toggle; Run-now `params` models if S0 lacks them) | 60 |
| 13 | `web/src/screens/admin/CreatorsFold.tsx`, `FeedsTable.tsx`, `CreatorDrawer.tsx`, `ItemDrawer.tsx`, `AudioAt.tsx` | 180, 90, 120, 200, 40 |
| 14 | `scripts/feature_check.py` (`creators` line), `scripts/backfill.py` (`--only creators`), `pyproject.toml` (`av`, `feedparser`, `youtube-transcript-api>=1.2`), `README.md` | +50, +80, +3, +15 |

Fixtures under `tests/fixtures/creators/`: `pod_fplharry.xml` (12 entries, 3 bad dates, written 30 days ahead of a frozen clock by the fixture builder, never literal), `blog_ffscout.xml` with `article_long.html` and `article_short.html`, `videos_page.html` and `videos_blocked.html`, `titles_pairs.json`, `harry_gw12.json` (segments plus the stubbed analysis), `expected_claims.json`. No audio file is committed; chunk tests build arrays in memory. Regenerate `contracts/` and `web/src/api/schema.d.ts` after step 11, before any screen file.

Where a session goes wrong, and the guard:

1. A test reaches the network, or hits Groq or Anthropic with a real key. Guard: `DESK_DISABLE_NETWORK=1` in `conftest.py` with the shared httpx client refusing every host outside the allow list; every transport in `transcribe.py`, `captions.py` and `extract.py` is an injected callable and tests 9 to 16 pass a stub; `test_env_split` proves the worker variables are absent under pytest.
2. The `no_body` gate is loosened so description-only items yield claims, because `items_done 0` looks like failure. Guard: test 14 pins `no_body: 3` with one model call; `items_skipped` makes a loosened gate visible as `no_body` falling to 0 while `tokens_in` rises; 410 of 799 old analyses ran on show notes and 354 produced nothing.
3. The task reports `rows_written` itself, or a 429 increments `transcribe_attempts` and three rate-limited nights push an item to `gave_up`. Guard: the S0 helper counts rows and test 17 proves a lying task lands `error`; test 11 asserts `transcribe_attempts 0` after a 429.
4. The migration re-creates `creator` and `source` where S1 seeded them and the people rows vanish. Guard: test 1 runs both branches; `ADD COLUMN IF NOT EXISTS` and `ON CONFLICT (creator_id) DO UPDATE` on `name`, `initials`, `people`.


# Part 12. Step S9: Creators settle, score, consensus and the three-level screen

Step 8 of 10. Follows ARCH.md; where ARCH is silent, a line prefixed `DECISION:` records the choice. Spelling is British. Season 2026-27, target deadline GW13. Migration `0008_s9_creator_scores` (S6 left the head at 0007; S7 takes 0009).

### 1. Goal

When this step is done the owner opens `/creators` and reads, for the next gameweek, who the eight shows are buying, selling and captaining as one bar per player, each creator's hit rate as an interval with its sample size, and, two clicks down, the verbatim quote with the audio at that second. Before this step the S6 claims sit unscored behind an admin panel and `/creators` shows one sentence.

### 2. Depends on

| Step | Used by S9 |
|---|---|
| S0 | `job`, the ledger helper `desk/jobs/runner.py::ledger`, `Job` and `JOBS` with `After` and `OnDemand`, `POST /api/admin/jobs/{task}/run` with `params`, `admin_jobs`, `Panel` and `PANELS` with `Ctx`, the envelope, `desk/db/pit.py::latest_at`, `desk/db/bucket.py::presign`, `scripts/feature_check.py` |
| S1 | `event` (`deadline_at`, `settled_at`), `player` (`position`, `web_name`, `team_code`), `player_fixture` (`total_points`, `starts`, `minutes`), `user_squad.picks`, `desk/fplapi/squads.py` (the picks parser with autosub reversal) and `desk/jobs/tasks/squad_refresh.py`, which S9 extends |
| S3 | `desk/providers/names.py`, the `player_detail` panel (S9 fills its `snippets`), `ChipRow`, `HeatCell` |
| S6 | `creator`, `source`, `item` (`analysis`, `segments`, `enclosure_url`, `pair_item_id`), `claim` with its NULL outcome columns and `revision`, `CREATORS`, `admin_creators` (S9 adds fields), `AudioAt.tsx` |
| S8 | the component kit named in section 8, `usePanel`, `urlState`, the `/creators` Nav entry |

Two S0 mechanisms S9 needs and adds if absent. First, the S0 helper counts `count(*)` per table, so an update-only task reads `rows_written 0` and lands as `wrote_nothing_unexplained`. DECISION: `writes` accepts `Counted(table, stamp)` beside a bare table name; its after-count is `count(*) WHERE stamp >= job.started_at`, its before-count 0. `creators_settle` declares `Counted("claim", "resolved_at")`. If S6 shipped `creators_transcribe` and `creators_captions` with a bare `"item"`, the S9 session changes them to `Counted("item", "updated_at")` in the same PR. Second, `After(task)` fires when the parent lands `ok` or `nothing_to_do`, never on `error` or `refused`; a daily `fpl_settle` between gameweeks lands `nothing_to_do` and settle must still run.

### 3. User stories

User role:

1. I open `/creators` and read one bar per player for GW13: buys right, sells left, a captain marker, show initials on each side, a rail on players I own. Result: one panel call, `creators_board`, under 70 KB, bars sorted by net votes.
2. I read one row per show under the bars: name, latest episode with its age in days, calls for GW13, the record as an interval against 0.5 with n beside it. Result: 8 rows; a show under 25 scored calls still shows its interval, wider.
3. I filter to midfielders and to FPL Harry and Let's Talk FPL. Result: bars and folds narrow with no request; the URL reads `?gw=13&pos=MID&c=fplharry,letstalkfpl`.
4. I switch the gameweek chip to GW14. Result: one new panel call; chips show votes per gameweek.
5. I click Haaland. Result: the drawer at `/creators/p/223094` reads "Haaland, 9 snippets from 4 shows for GW13", grouped by show; each snippet has a stance chip, a hit or miss chip once settled, the verbatim quote, the episode title, its age and `source at 13:32`.
6. I click `source at 13:32`. Result: the same drawer at `/creators/p/223094/i/{item_id}`, breadcrumb "Creators / Haaland / FPL Harry / episode", audio cued to 812 s, six summary bullets, then "What was said, 14 players".
7. I click The FPL Wire in the creators table. Result: the drawer at `/creators/c/fplwire` lists every episode newest first, a `transcribed` chip where a transcript exists, the count in the header, nothing filtered by default.
8. I open "Consensus". Result: a table with buy, sell, captain, hold, avoid and bench counts per player, net, and "held by 6 of 11" from the shows' own squads.
9. I open "Report cards". Result: per show, one interval per action with n; under 25 scored calls the words "under 25 scored, weight 0".
10. I open "Said versus owned". Result: a grid of the board's players by the shows' verified managers; a cell is a stance chip when said, a ring when held, both when both.
11. I open Projections and click a player. Result: up to 5 creator snippets under the pivot from `player_detail.snippets`, each linking to `/creators/p/{code}`.

Admin role:

12. I read two new rows on the jobs board, `creators_settle` and `creators_score`. Result: status, claims seen, scored, revised, unscoreable by reason, rows written; for score, the creators whose record changed.
13. I press Run-now on `creators_settle` with `{gw: 12, force: true}`. Result: every GW12 claim is re-evaluated; `revised` counts changed verdicts; no confirm dialog, because no meter.
14. I read the Creators fold. Result: each creator row gains a Record cell (interval, n) and a line "Settled 412 claims, 31 unscoreable: published_after_deadline 24, player_not_in_season 5, no_positional_median 2".
15. I read a `squad_refresh` row. Result: the note gains `creators 11, written 9, unchanged 2, skipped {"picks_404": 0}`.

### 4. Data

Migration `0008_s9_creator_scores`: two new tables, one new series table, one altered table. Every timestamp `timestamptz`, session UTC, every key column `NOT NULL`. Player identity is `code`; `creator_pick` and `player` are the only tables allowed an `element_id` column (`test_element_id` names both).

#### 4.1 `claim` (altered)

Why: the outcome lives on the claim so level 2 reads one row per snippet and the frozen-verdict incident (`c789453`) has a revision counter.

| Column | Change |
|---|---|
| `duplicate_of` | text NULL, foreign key `claim(claim_id)`; set on every claim that repeats an earlier `(creator_id, code, action, season, gw)` vote; NULL on the vote itself |
| `unscoreable` | check in `('published_after_deadline', 'gameweek_not_settled', 'player_not_in_season', 'no_position', 'no_positional_median', 'no_deadline_known')` |
| `benchmark_kind` | check `~ '^(GKP|DEF|MID|FWD)_starter_median$'` |
| `resolved_at` | meaning fixed: the instant the stored verdict last changed; untouched when a run re-evaluates and finds the same verdict |
| index | `claim_open ON claim (season, gw) WHERE hit IS NULL`; `claim_vote ON claim (creator_id, code, action, season, gw, published_at)` |

Constraint `claim_verdict_shape`: `(hit IS NULL) <> (unscoreable IS NULL)` whenever `resolved_at IS NOT NULL`; both NULL before the first evaluation. A settled hit reads `player_points 13, benchmark_kind FWD_starter_median, benchmark_points 4.00, hit true, unscoreable NULL, revision 1`.

#### 4.2 `creator_score` (new, series)

Why: the record in force at any instant, so a GW8 board rendered in March shows the interval a reader saw in October (R6 rule 1, incident `b732d8d`).

| Column | Type | Notes |
|---|---|---|
| `creator_id` | text NOT NULL FK | |
| `scope` | text NOT NULL | check in `('all', 'buy', 'sell', 'hold', 'captain', 'avoid', 'bench')` |
| `as_of` | timestamptz NOT NULL | the score run's `started_at` |
| `claims_total`, `claims_scored`, `hits` | int NOT NULL | distinct votes; votes with `hit` set; hits |
| `hit_rate` | numeric(5,4) NULL | NULL when `claims_scored = 0` |
| `wilson_lo95`, `wilson_hi95` | numeric(5,4) NOT NULL | 0 and 1 when `claims_scored = 0` |
| `weight` | numeric(5,4) NOT NULL | 7.2; 0 under 25 scored |
| `unscoreable` | jsonb NOT NULL | `{"published_after_deadline": 24}` by reason |
| `first_claim_at`, `last_claim_at` | timestamptz NULL | |

Primary key `(creator_id, scope, as_of)`. Append-only; a write equal to the latest row on `(claims_total, claims_scored, hits)` is dropped and counted `rows_unchanged`. Every read goes through `latest_at("creator_score", {creator_id, scope}, t)`; `test_pit` covers it from this step.

#### 4.3 `creator_pick` (new)

Why: the shows' own fifteen, for the said-versus-owned fold and `player_chatter.owned`; ARCH lists it under `squad_refresh.writes`.

| Column | Type | Notes |
|---|---|---|
| `entry_id`, `season`, `gw`, `code` | int, text, smallint, int NOT NULL | primary key |
| `element_id` | smallint NOT NULL | raw, beside the resolved `code` |
| `slot` | smallint NOT NULL | 1 to 15 after autosub reversal |
| `multiplier` | smallint NOT NULL | FPL's; 0 bench, 1, 2, 3 |
| `is_captain`, `is_vice` | bool NOT NULL | |
| `picks_as_of` | timestamptz NOT NULL | `event.deadline_at` for `gw` |
| `fetched_at` | timestamptz NOT NULL | |

Index `(season, code, gw)`.

#### 4.4 `creator_gw` (new)

Why: one row per verified manager per gameweek, so the creator drawer prints "Harry Daniels, rank 41,209".

| Column | Type | Notes |
|---|---|---|
| `entry_id`, `season`, `gw` | int, text, smallint NOT NULL | primary key |
| `points`, `total_points` | smallint, int NOT NULL | |
| `overall_rank` | int NULL | NULL before GW1 is scored |
| `bank_tenths`, `value_tenths` | smallint NOT NULL | |
| `event_transfers`, `event_transfers_cost` | smallint NOT NULL | |
| `chip` | text NULL | `wildcard`, `freehit`, `bboost`, `3xc` |
| `fetched_at`, `updated_at` | timestamptz NOT NULL | |

#### 4.5 Sources, cadence, point in time

`creators_settle` writes the `claim` outcome columns and `duplicate_of` after every `fpl_settle` and on Run-now; `creators_score` writes `creator_score` after every settle, on change only; `squad_refresh` writes `creator_pick` and `creator_gw` at T+90m and every 12 h.

Point-in-time rules: `claim.published_at`, from the feed, is the only instant a claim is judged against; the verdict uses `event.deadline_at` for the claim's `gw`; the Record cell is `latest_at(creator_score, now)`; `claim` outcome columns carry `revision` and `resolved_at` and need no `as_of`. `creator_pick.picks_as_of` equals the deadline of its gameweek. The seed loads no old `claim_outcome` or `creator_score` rows; the first settle recomputes every verdict from `player_fixture`, so the old double-counted totals (3,381 rows against 1,909 votes, R4 section 1.6) are not carried.

### 5. Jobs

Two registry rows on the pipeline lane plus one extension. Every task answers `desk run-task <name> --help`.

#### 5.1 `creators_settle`

| Field | Value |
|---|---|
| Trigger | `After(fpl_settle)`; admin Run-now, no confirm, `params {gw?: int, force?: bool}`; `stale_window_s` 108,000 (30 h) |
| Inputs | `claim` rows for the season where `hit IS NULL` (the open set), plus every claim of `params.gw` when `force`; `event`; `player`; `player_fixture` for each settled gameweek touched |
| Step 1, votes | per `(creator_id, code, action, season, gw)` group among claims of gameweeks settled within 7 days or unsettled, the earliest `published_at` (ties by `claim_id`) is the vote; every other row gets `duplicate_of = vote.claim_id`; a changed `duplicate_of` stamps `resolved_at` |
| Step 2, verdicts | per 7.1; a verdict differing from the stored columns is written with `revision + 1` and `resolved_at = now`; an identical one is `rows_unchanged` |
| Outputs | `claim` outcome columns, `duplicate_of` |
| Ledger | `items_seen` = candidates; `items_done` = rows with `hit` set after the run; `items_skipped` by unscoreable reason; note `scored 412, revised 3, newly_scored 88, duplicates_marked 12, unchanged 300`; `rows_written` by `Counted("claim", "resolved_at")` |
| Cost cap | none; `meters = ()` |
| Timeout | `budget_s` 300 |
| Failure | a raise rolls back the gameweek's transaction and lands `error` |
| Silent nothing | `items_done` on the row; the Record cells and level 2 hit chips never fill; the feature check fails when the newest settle row is over 30 h old or, once GW6 is settled, `claims_scored` is 0 for every creator |

`nothing_to_do` when the open set is empty, note `0 open claims; last settled gw 12`. With open claims on an unsettled gameweek the run is `ok`, every one under `gameweek_not_settled`, and `rows_unchanged` equals `items_seen` after the first pass.

#### 5.2 `creators_score`

| Field | Value |
|---|---|
| Trigger | `After(creators_settle)`; admin Run-now, no confirm; `stale_window_s` 108,000 |
| Inputs | every `claim` with `duplicate_of IS NULL` for the eight creators, any season |
| Per creator | 7 rows, scope `all` plus one per action, per 7.2; each compared with `latest_at(creator_score, {creator_id, scope}, now)` on `(claims_total, claims_scored, hits)` |
| Outputs | `creator_score` |
| Ledger | `items_seen` = 56 (8 creators by 7 scopes); `items_done` = rows inserted; `rows_unchanged` = rows dropped; note `changed: fplharry, fplraptor` |
| Cost cap, timeout | none; `budget_s` 60 |
| Failure | a raise lands `error`; the next settle chain retries |
| Silent nothing | no `creator_score` row for a creator; the Record cell reads the gap "not scored yet"; the feature check asserts one `all` row per active creator once GW6 is settled |

`nothing_to_do` when all 56 rows are unchanged, note `56 of 56 unchanged`, the normal state between settlements.

#### 5.3 `squad_refresh` (extended)

S1's task gains a second loop: every person in `CREATORS` with a verified `entry_id` (11 in the seed). Per entry, `entry/{id}/history/` gives `current[]` and `chips[]`, and the newest played gameweek `g` gets `entry/{id}/event/{g}/picks/`, parsed by S1's parser with autosub reversal; `creator_gw` for every gameweek in `current[]` and `creator_pick` for `g` are upserted with change skipping. 22 requests at a 1.1 s gap; `budget_s` adds 3 per entry. `writes` gains `creator_pick`, `creator_gw`; `read_by` gains the three reader panels. The note gains `creators 11, written 9, unchanged 2, skipped {"picks_404": 0, "entry_404": 0}`; a 404 counts and the loop continues. If this silently did nothing, `panel_squads.known` reads 0 with the reason "no creator squads fetched", the matrix shows no rings, and the feature check asserts `known >= 6` after T+2h.

### 6. API and panels

Four session-tier panels, one extended session panel and one extended admin panel, each a `PANELS` row served at `GET /api/panels/{name}` in the S0 envelope. Errors: 400 `{error: "invalid_params", field, detail}`; 401 `{error: "not_signed_in"}`; 404 `{error: "not_found", kind, id}`; 500 `{error: "panel_failed", panel}`. Every panel sits inside the 10 s budget with its own `budget_ms`. The four session panels become chat tools `panel_creators_board`, `panel_player_chatter`, `panel_creator_episodes`, `panel_episode_summary` in S7; each declares `headline`.

#### 6.1 `creators_board` (session)

Params, all optional: `gw` int 1 to 38 (default: the smallest `event.gw` with `deadline_at > now`), `position` in `GKP DEF MID FWD`, `creators` list of `creator_id` (max 8), `stance` list in `buy sell captain hold avoid bench`. The screen sends `gw` only and filters the rest client side; chat may send all four. `budget_ms` 1,500.

```
{ as_of, gw, gw_reason: "requested" | "next_deadline", deadline_at,
  players: [ { code, web_name, position, team_code, price_tenths,
               buy, sell, captain, hold, avoid, bench: each {n, creators: [creator_id]},
               net: int, mean_confidence: number,
               mine: { in_squad: bool | null, multiplier: int | null },
               panel_owned: { n, of, people: [ { person, creator_id, entry_id, multiplier } ] } } ],
  concentration: { buy: number, sell: number, captain: number },
  creators: [ { creator_id, name, initials, active,
                latest: { item_id, title, kind, published_at, has_transcript } | null,
                votes_gw: int, votes_season: int,
                record: { scope: "all", claims_scored, hits, hit_rate, wilson_lo95, wilson_hi95, weight, as_of } | null,
                record_by_action: [ { scope, claims_scored, hits, wilson_lo95, wilson_hi95 } ],
                takes: { buy: [ { code, web_name, confidence } ], sell: [...], captain: [...] },
                people: [ { person, entry_id, verified, overall_rank, gw } ] } ],
  coverage: { items_window: int, analysed: int, newest_unread_at: str | null, window_days: 21 },
  panel_squads: { with_entry: int, known: int, gw: int | null, reason: str | null },
  mine_reason: str | null,
  freshness: { newest_claim_at, newest_settle_at, newest_score_as_of } }
```

`players` holds every player with a vote for `gw`, sorted by `net` descending then `captain.n`, capped at 60. `mine` reads the session user's newest `user_squad` row; `in_squad` is NULL with `mine_reason` when none exists. `panel_owned.of` is `panel_squads.known`. `takes` holds at most 12 per stance per creator, confidence descending. Provenance `inputs`: `claim`, `creator`, `creator_score` with `as_of`, `item`, `user_squad` with `as_of`, `creator_pick` with `gw`, `event`. Empty: `Empty(reason="no_creators", fix="Run migration 0007")` on an empty `creator` table; `Empty(reason="no_claims", fix="Extraction has not run; open Admin and run creators_extract")` when `claim` has no row. A gameweek without votes returns `players: []` with `coverage` filled. `headline`: the top 5 players as `Haaland: buy 4, captain 3, sell 0`.

#### 6.2 `player_chatter` (session)

Params: `code` int (required), `gw` int (optional; absent means every gameweek in the window), `days` int 1 to 120 (default 30). `budget_ms` 800.

```
{ player: { code, web_name, position, team_code, price_tenths },
  gw: int | null, window_days,
  said: [ { claim_id, creator_id, creator_name, initials, item_id, item_title, item_kind, published_at,
            action, confidence, gw, gw_inferred, quote, start_s: number | null, deep_link: str | null,
            hit: bool | null, unscoreable: str | null, duplicate_of: str | null } ],
  by_show: [ { creator_id, name, n } ],
  owned: [ { person, creator_id, entry_id, gw, multiplier, is_captain, picks_as_of } ],
  owned_reason: str | null,
  counts: { said, shows, owned, of } }
```

`said` is every claim (votes and duplicates) for the code in the window, `gw` matching when given, newest first, capped at 200. `deep_link` is `{url}&t={int(start_s)}s` for YouTube items, else NULL. Unknown `code` is 404; a known player with nothing said returns `said: []`. Provenance `inputs`: `claim`, `item`, `creator_pick` with `gw`, `player`. `headline`: the first 5 of `said` as `FPL Harry, captain, 0.8: "..."` cut at 120 chars.

#### 6.3 `creator_episodes` (session)

Params: `creator_id` (required), `limit` 1 to 200 (default 50), `before` timestamptz (paging). `budget_ms` 800.

```
{ creator: { creator_id, name, initials, people: [ { person, entry_id, verified, overall_rank, total_points, gw } ] },
  record: { claims_scored, hits, wilson_lo95, wilson_hi95, weight, as_of } | null,
  episodes: [ { item_id, kind, title, url, published_at, text_source, has_transcript, transcript_engine,
                audio_seconds, analysed: bool, claims_n, pair_item_id } ],
  total, transcribed_total, next_before: str | null }
```

Unknown id is 404; a creator with no items returns `episodes: []`. No probe status, attempt count or error string leaves this panel. Provenance `inputs`: `creator`, `item`, `claim`, `creator_score` with `as_of`, `creator_gw`. `headline`: the first 10 titles with ages.

#### 6.4 `episode_summary` (session)

Params: `item_id` (64 hex, required). `budget_ms` 800.

```
{ item: { item_id, creator_id, creator_name, initials, kind, title, url, published_at, text_source,
          audio_seconds, has_transcript, pair_item_id },
  summary: [str],
  calls: [ { claim_id, code, web_name, position, team_code, action, gw, gw_inferred, confidence,
             quote, start_s, deep_link, hit, unscoreable } ],
  chips: [ { chip, stance, gameweek, quote } ],
  insights: [ { topic, entity_kind, entity_name, claim_text, quote, horizon_gw, horizon_gw_end, conviction } ],
  audio: { url, source: "bucket" | "enclosure", expires_at: str | null } | null,
  not_extracted_reason: str | null }
```

`calls` is sorted by `start_s`, NULLs last. DECISION: `audio.url` is a presigned bucket GET valid 3,600 s while `audio/{sha}.mp3` exists (retention deletes it the night after extraction), otherwise the public `enclosure_url` with `source: "enclosure"` and `expires_at` NULL; podcast hosts honour range requests, so seeking works. `not_extracted_reason` is "Not extracted yet" when `analysed_at` is NULL, "No transcript; show notes are never analysed" when `text_source = description`. Provenance `inputs`: `item:{item_id}`, `claim`, `player`. `headline`: the summary bullets.

#### 6.5 `player_detail` (S3, extended)

`snippets` becomes the first 5 rows of `player_chatter.said` over 30 days as `{creator_name, initials, action, confidence, quote, published_at, item_id}`, from the shared `desk/creators/chatter.py::snippets(code, days, limit)`. Provenance gains `claim` and `item`; the snapshot diff is reviewed.

#### 6.6 `admin_creators` (S6, extended)

`creators[]` gains `record: { claims_scored, hits, wilson_lo95, as_of } | null`; the top level gains `settle: { scored, unscoreable: object, newest_resolved_at, open }`; `runs` includes the two new tasks. Provenance gains `creator_score`.

#### 6.7 Writes

`POST /api/admin/jobs/creators_settle/run` (admin, CSRF) takes `{params: {gw?: 1..38, force?: bool}}` and returns `{job_id}`; `force` without `gw` is 400 `force_needs_gw`. `POST /api/admin/jobs/creators_score/run` takes `{}` and returns `{job_id}`. No user-facing write exists in this step; probe status, transcribe attempts and errors stay in S6's admin panels.

### 7. Algorithms

#### 7.1 The verdict (`desk/creators/settle.py`, lifted from `fpl_edge/ingest/content/scoring.py` lines 300 to 400)

```
POSITIVE = {buy, hold, captain}; NEGATIVE = {sell, avoid, bench}
for claim in candidates:
  deadline = event.deadline_at[(season, gw)]      -> None: unscoreable no_deadline_known
  if claim.published_at >= deadline:               unscoreable published_after_deadline
  if event.settled_at[(season, gw)] is None:       unscoreable gameweek_not_settled
  if (season, code) not in player:                 unscoreable player_not_in_season
  position = player.position[(season, code)]      -> None: unscoreable no_position
  bench = median[(season, gw, position)]            -> None: unscoreable no_positional_median
  points = sum(player_fixture.total_points for (season, gw, code)) or 0
  hit = points > bench if action in POSITIVE else points < bench
  benchmark_kind = f"{position}_starter_median"
```

The median per `(season, gw, position)` is over players with `sum(starts) > 0 OR sum(minutes) >= 60` across the gameweek's fixtures, joined to `player.position`; a double gameweek sums to one figure per player. A player with no `player_fixture` row scored 0; absence is the outcome a buyer got. Ties miss on both sides. DECISION: position comes from the current `player` row, because `player_state` carries no position; a reclassification changes the bucket on the next force run, visible as `revision + 1` with a new `benchmark_kind`. Constants: `STARTER_MIN_MINUTES = 60`; `DEDUPE_LOOKBACK_DAYS = 7`, since both halves of a pair arrive within one discover cycle.

Validation: tests 3 to 6; on the seeded 2025-26 archive one gameweek settles under 5 s and the `MID` median in a normal gameweek lies in [2, 4].

#### 7.2 Wilson interval and weight (`desk/creators/score.py`, lifted from `scoring.py` lines 78 to 112)

```
Z = 1.96; MIN_SCORED = 25
wilson(hits, n): phat = hits/n; d = 1 + Z²/n; c = phat + Z²/(2n); m = Z*sqrt((phat(1-phat) + Z²/(4n))/n)
  lo = max(0, (c - m)/d); hi = min(1, (c + m)/d); n = 0 -> (0, 1)
weight(hits, n) = 0 if n < MIN_SCORED else round(min(1, max(0, 2*(lo - 0.5))), 4)
```

25 is the old floor; at n 8 with 6 hits `lo` is 0.41. The interval is drawn whatever n; only the weight has the floor, and the weight never multiplies a count in v1. Validation: `wilson(130, 200)` gives `lo` 0.5817 and `hi` 0.7136 to 4 places; `wilson(3, 4)` gives `lo` 0.3006; `weight(20, 24)` is 0; `weight(140, 200)` is 0.2570.

#### 7.3 Votes and consensus (`desk/creators/consensus.py`, lifted from `consensus.py` lines 46 to 130)

```
votes = claims where duplicate_of IS NULL and season, gw match
per (action, code): n = count distinct creator_id; creators = sorted ids; mean_confidence
net = buy.n - sell.n
share(action, code) = n / sum(n over codes for that action); hhi(action) = sum(share²)
```

One vote per `(creator_id, code, action, season, gw)`, earliest published wins, so a podcast plus YouTube pair (S6 7.1) counts once. Counts are distinct creators, never claim rows, never weighted. Validation: tests 7 and 8.

#### 7.4 The board gameweek and the window

`gw` defaults to the smallest `event.gw` with `deadline_at > now` (`gw_reason: next_deadline`). The gameweek chips carry `gw - 2` to `gw + 3` with a vote count per chip from one grouped query. `coverage.window_days` is 21, S6's extraction window; `newest_unread_at` is the newest item with a body and no `analysed_at`.

#### 7.5 Said versus owned

For each board player and each verified person in `CREATORS`: `said` is the stance that person's show voted; `owned` is a `creator_pick` row for `(entry_id, season, panel_squads.gw, code)`. The grid is client side from `players[].{buy,sell,captain}.creators`, `players[].panel_owned.people` and `creators[].people`; no extra call. Multi-host shows attribute the show's stance to every host, and the header says so: "a show's call is drawn under each of its hosts".

### 8. UI

Shared components: `DataTable`, `FilterRow`, `RowCount`, `Drawer`, `Breadcrumb`, `StateBox`, `Fold`, `Age`, `Number`, `CiteChip`, `Interval`, `PlayerCell`, `ClubMark`, `BarDiverging`, `ChipRow` (S3), shadcn `Badge`, `Tooltip`, `Skeleton`, `Toggle Group`. `AudioAt.tsx` moves from `screens/admin/` to `components/`; the grep test still allows one `<audio` under `web/src`. New in the design system: `StanceChip` (a `Badge` variant with the six stance words and, when scored, a leading hit or miss mark in the good or bad token, never colour alone) and `OwnedRing` (a 2 px accent ring around a `PlayerCell` or grid cell). A second implementation of either is a test failure.

#### 8.1 Level 1, `/creators`

Primary visual: the board, a `BarDiverging` titled "Who they are talking about, GW13". One row per player: `PlayerCell` at 24 px, buys right in series token 1, sells left in series token 2, the captain count as a filled marker at the right end, show initials as `Badge`s on each side, an `OwnedRing` rail on players in the reader's squad. Sorted by `net` descending, ties by captain count; 20 rows, "Show all 41" expands.

Header line: "GW13, deadline in 2 d 4 h", the one hours surface, then a `CiteChip` for `creators_board` with `newest_claim_at` as an `Age`.

One `FilterRow`: Gameweek as a `ChipRow` (`gw - 2` to `gw + 3` with vote counts, default the next deadline; a change refetches); Position as a `Toggle Group` (All, GKP, DEF, MID, FWD; default All); Shows as multi-select chips of the 8 initials (default all); Stance as multi-select (Buy, Sell, Captain, Hold, Avoid, Bench; default Buy, Sell, Captain). Position, Shows and Stance filter client side with no request. `RowCount`: "41 of 41 players". URL state: `?gw=13&pos=MID&c=fplharry,letstalkfpl&stance=buy,sell`.

Secondary, under the board: the creators table, `DataTable`, 8 rows. Columns: Show (name, initials `Badge`); Latest (title cut at 60 chars, `Age`; a dash when none); Calls (votes for the selected gameweek, `Number`); Record (`Interval` of `wilson_lo95` to `wilson_hi95` against 0.5 with `claims_scored` beside it; the gap "not scored yet" when `record` is NULL); Rank (the first verified person's `overall_rank`, or a dash). Default sort: Calls descending. `secondary` under 820 px: Latest, Rank. A row opens level 3 at `/creators/c/{creator_id}`.

Four closed `Fold`s, each summary a finding and a count:

| Fold | Summary when closed | Inside |
|---|---|---|
| Consensus | "Consensus: 41 players, Haaland net +4" | `DataTable`: Player, Buy, Sell, Captain, Hold, Avoid, Bench, Net, Mine (multiplier or dash), Held ("6 of 11"); default sort Net descending; a row opens level 2 |
| Takes by show | "Takes by show: 8 shows, 96 calls" | one block per show with Buy, Sell and Captain lists, each name a `PlayerCell` with the confidence as `Number`; a name opens level 2 |
| Report cards | "Report cards: 8 shows, best lower bound 0.49" | one card per show: the `all` interval, then one `Interval` per action with n; under 25 scored, "under 25 scored, weight 0" |
| Said versus owned | "Said versus owned: 41 players by 11 managers" | a grid, rows players, columns people grouped by show; a `StanceChip` when said, an `OwnedRing` when held, both when both; the 7.5 header sentence; horizontal scroll inside the fold only |

Drill-down: a player name or bar opens level 2; a show name opens the level 3 creator view.

States. Empty (no claims for the gameweek): `StateBox` in the board's place, "No calls for GW13 yet. 8 shows read; newest item {Age}. Try GW12.", with the creators table still rendered. Empty (no creators or no claims at all): the payload's `reason` and `fix` verbatim. Loading: `Skeleton` at 20 bar rows of 28 px and 8 table rows of 40 px. Error, only on a thrown request: "Creators board failed to load ({status})." with a "Retry" button. Stale: `newest_claim_at` over 4 days sets a warn `Age`.

Mobile at 390 px: the board is full width with initials folded into a count `Badge` per side; the filter row collapses to one "Filters" button opening a `Drawer`; the table hides its `secondary` columns; the matrix scrolls inside its fold; no page-level horizontal scroll.

Mount budget: 1 call, under 70 KB, under 1,400 px folded at 1,280 px.

#### 8.2 Level 2, `/creators/p/{code}`

The one `Drawer`. Header: `PlayerCell` at 64 px, price, "9 snippets from 4 shows for GW13" (or "over 30 days" without a gameweek), a `CiteChip` for `player_chatter`. Shows and Stance chips persist from level 1 through the URL and filter client side. Body: snippets grouped by show, newest first. Each snippet: `StanceChip` (hit or miss once `hit` is set; a muted "unscoreable: published after deadline" chip when set), confidence as `Number`, the verbatim quote at 13 px, the episode title, `Age`, and a button `source at 13:32` (`source` when `start_s` is NULL; the `deep_link` for YouTube). A closed `Fold` "Held by 6 of 11 managers" lists person, show, multiplier and the `Age` of `picks_as_of`, or `owned_reason` when `of` is 0.

Empty: "Nothing said about Haaland in the last 30 days. 8 shows read." Loading: `Skeleton`, 6 cards of 96 px. Error: "Snippets failed to load ({status})." with "Retry". The source button opens level 3 at `/creators/p/{code}/i/{item_id}`; Escape closes the drawer and returns focus to the bar.

#### 8.3 Level 3, episode, `/creators/p/{code}/i/{item_id}` and `/creators/c/{creator_id}/i/{item_id}`

Same `Drawer`, `Breadcrumb` "Creators / Haaland / FPL Harry / episode" or "Creators / FPL Harry / episode". Header: the title linking to `url`, kind, `Age`, minutes, a `Badge` `transcribed` or `captions` when present. `AudioAt` when `audio` is set, cued to the `start_s` the reader arrived from; a muted "audio from the publisher" line when `source` is `enclosure`. Then "Summary" as bullets, then "What was said, 14 players" as a `DataTable` with Player (`PlayerCell`), Stance (`StanceChip` with the verdict), GW, Conf, Quote, At (an `m:ss` button that seeks the audio, or the YouTube `deep_link`), default sort At ascending, and "Chips and insights, 4" as a closed `Fold`. When `not_extracted_reason` is set, a `StateBox` gap with that sentence replaces the summary and table.

#### 8.4 Level 3, creator, `/creators/c/{creator_id}`

Same `Drawer`, `Breadcrumb` "Creators / FPL Harry". Header: name, initials, "Harry Daniels, entry 3054, rank 41,209 at GW12" per verified person, the `all` `Interval`. Body: `DataTable` of every episode newest first, 50 per page: Title, Kind, Published (`Age`), Transcribed (`Badge`, header "Transcribed, 24 of 45"), Analysed (`Age` or dash), Calls (`Number`). No default filter; the filter row offers Kind (All, podcast, youtube, article; default All) and an off "Transcribed only" toggle. "Load 50 more" when `next_before` is set. `secondary` under 820 px: Kind, Analysed. A row opens 8.3 at `/creators/c/{creator_id}/i/{item_id}`.

Empty: "No episodes yet for FPL Harry. Discover runs every 4 hours." Loading: `Skeleton`, 10 rows of 40 px.

#### 8.5 Projections drawer (S3, extended)

Under the price sparkline, a closed `Fold` "Creator snippets, 5" lists `player_detail.snippets` with `StanceChip`, quote and `Age`, and links "All snippets" to `/creators/p/{code}`.

#### 8.6 Surface inventory

The PR description carries this table (rule 35).

| Old surface (`creators.js`) | New location |
|---|---|
| Source console, live/quiet/stale pills, fetch button | Admin, Creators fold (S6) |
| Deadline window, 7 GW chips | level 1 filter row, `ChipRow` with vote counts |
| Main takes cards with in/out/hold counts | Consensus fold, table form |
| Record pill "50 percent, n 204, coin flip" | creators table Record column, `Interval` with n |
| "Interval below a coin flip" strip | Report cards fold, the words "under 25 scored, weight 0" |
| Panel intent dot plot | the board, `BarDiverging` |
| The armband bars | the captain marker and count on each bar |
| Said versus owned matrix | Said versus owned fold |
| Report cards wall, 28 tiles | Report cards fold, 8 cards |
| Watch cards | not carried, `watch` is not a claim (S6); the PR asks the owner's word |
| Player row, snippets, episode summary (G8) | levels 2 and 3 |

### 9. Admin view

The admin sees the two new job rows with last run, next due, status, every count and the note; Run-now on each, with `gw` and `force` fields for settle; the log tail per job; the Creators fold's Record column and settle line; the `squad_refresh` note with creator counts; and every user screen.

A user never sees a job row, a log tail, a probe status, a transcribe attempt or error, a presigned raw body, the operator variable name, or another user's squad. `test_routes_matrix` asserts 401 for a stranger on the four session panels and 403 for a session user on the two Run-now routes.

### 10. Acceptance tests

In the order to write them.

1. `test_s9_migration`: upgrade on the S6 head creates `creator_score`, `creator_pick`, `creator_gw`, adds `duplicate_of`, the checks and the two indexes to `claim`, and `downgrade -1` removes them; `test_element_id` still passes with `creator_pick` allowed.
2. `test_wilson_and_weight`: the four values in 7.2 to 4 places; `wilson(0, 0)` is `(0, 1)`; `weight` is 0 below 25 and monotone in hits at fixed n.
3. `test_verdict_fixture`: a seeded gameweek with 30 players (10 per position, a double for two, one with no fixture row) gives the expected `benchmark_points` per position, `player_points` summed across the double, 0 for the absent player, the expected `hit` for one claim per action; ties land `hit false`.
4. `test_published_after_deadline_is_unscoreable`: a claim stamped 1 s after its deadline lands `unscoreable published_after_deadline`, `hit NULL`, counted in `items_skipped`; 1 s before, it lands a verdict.
5. `test_unscoreable_becomes_scored_on_a_later_run` (incident `c789453`): a claim on an unsettled gameweek lands `gameweek_not_settled`, `revision 0`; after `event.settled_at` and `player_fixture` rows land, the next run writes `hit`, `unscoreable NULL`, `revision 1`, a later `resolved_at`, and the ledger reads `revised 1`.
6. `test_settle_unchanged_leaves_resolved_at`: a third run writes nothing, `rows_unchanged` equals `items_seen`, `resolved_at` is byte-equal, and the row lands `ok`. DECISION: the helper treats `rows_unchanged > 0` as an explanation.
7. `test_pair_counts_once`: two claims from a paired podcast and YouTube item, same `(creator, code, action, gw)`, 3 h apart: the later gets `duplicate_of`, the board reads `n 1`, `creator_score.claims_total` is 1, `player_chatter.said` lists both.
8. `test_consensus_counts_distinct_creators`: three claims from one creator on one player and gameweek give `buy.n 1`; two creators give 2; `net` is `buy.n - sell.n`; `hhi` is 1.0 when one player holds every buy vote.
9. `test_creator_score_write_on_change`: the first run inserts 56 rows; a second run with no new verdicts inserts 0 and lands `nothing_to_do`, note `56 of 56 unchanged`; one new hit inserts exactly the `all` row and that action's row for one creator.
10. `test_score_silent_nothing_is_error`: a task stub that sees 56 scopes, inserts nothing and reports no skip or unchanged count lands `error`, note `wrote_nothing_unexplained`.
11. `test_squad_refresh_creator_entries`: two verified entries stubbed from `tests/fixtures/fpl/` write 2 sets of 15 `creator_pick` rows and their `creator_gw` rows; a second run writes 0; a 404 on one picks body counts `picks_404 1` and the run stays `ok`.
12. `test_creators_board_contract_and_empty`: on the seeded Postgres `creators_board` with `example_params` validates with `extra="forbid"`, matches `contracts/panels/creators_board.json`, sorts `players` by `net` descending, has `panel_owned.of` equal to `panel_squads.known`, `mine.in_squad` NULL with `mine_reason` for a user without a squad, and `provenance.inputs` naming seven sources; on an empty database the envelope is `empty {reason: "no_creators"}`; a stranger gets 401.
13. `test_chatter_episodes_summary_contracts`: the three panels validate their snapshots; `player_chatter` is 404 on an unknown code and returns `said: []` on a known code with nothing said; `creator_episodes` for `fplwire` lists every item with `has_transcript` and `transcribed_total` correct; `episode_summary.calls` sorts by `start_s` with NULLs last, `audio.source` is `enclosure` when the bucket object is absent, and no admin-only field name appears in any result.
14. `test_player_detail_snippets`: `player_detail` for a code with 7 snippets returns 5, newest first, equal to the first 5 of `player_chatter.said` on the shared fields.
15. `test_admin_creators_gains_record`: `admin_creators.creators[].record` and `settle` validate and the snapshot diff is committed.
16. `test_creators_screen` (Playwright): `/creators` mounts with 1 panel call under 70 KB, under 1,400 px folded at 1,280, 20 bars, 8 table rows, 4 closed folds; `?pos=MID` issues no request and narrows the bars; a bar click opens `/creators/p/223094` with grouped snippets; `source at m:ss` opens `/creators/p/223094/i/{item_id}` with the breadcrumb and an `<audio>` whose `currentTime` is within 1 s of `start_s`; a table row opens `/creators/c/fplwire` with `transcribed` chips and no default filter; every level reloads to itself; no `\b\d+\s*h\b` age string outside the deadline line; no horizontal scroll at 390; both themes pass 4.5:1.
17. `test_creators_empty_gw_state` (vitest): a payload with `players: []` renders the board `StateBox` "No calls for GW13 yet." and 8 creator rows; `record: null` renders "not scored yet" in the Record cell.
18. `test_prose_creators_strings`: `scripts/prose_check.py` over `web/src/screens/creators/` and the four panel modules finds nothing.
19. `test_feature_check_s9` (probe against `PUBLIC_URL`): the `creators` line also asserts `creators_board` for the next gameweek returns 200 with `provenance.git_sha` equal to the deploy and 8 `creators`; the newest `creators_settle` and `creators_score` rows are `ok` or `nothing_to_do` and under 30 h old; once GW6 is settled, every active creator has an `all` `creator_score` row and at least 5 have `claims_scored >= 1`; `panel_squads.known >= 6` after T+2h.

### 11. Definition of done

- [ ] Tests 1 to 19 green locally and in CI; `ruff`, `mypy --strict desk`, `tsc --noEmit`, eslint, `vite build`, contract drift and the prose checker green.
- [ ] `docs/runbook.md` regenerated with the two new rows and the extended `squad_refresh`; sync test green.
- [ ] Migration `0008_s9_creator_scores` applied on Railway; `/api/health` reports `alembic_head == expected_head`.
- [ ] `test_pit` covers `creator_score`; `test_element_id` allows `creator_pick`; `test_jobs_walk` finds a reader for the three new tables.
- [ ] One settle chain has run on Railway after a real `fpl_settle`: settle `ok` with `newly_scored > 0`, score `ok` with `items_done > 0`; the next day both land `nothing_to_do`.
- [ ] `squad_refresh` has written `creator_pick` rows for at least 6 of the 11 verified entries.
- [ ] Deployed on `main`; `deploy-check.yml` green on every line, creators included.
- [ ] Owner's demo, before GW13: open `/creators`; read the GW13 bars and the deadline line; click the MID toggle and the Harry and Let's Talk chips, watch the bars narrow with no spinner; open Consensus and read net votes; open Report cards and read one interval with its n; open Said versus owned and find a cell with a chip and a ring; click Haaland, read snippets by show with quotes and `source at m:ss`; click one source, hear the audio from that second under six bullets; press Escape; click The FPL Wire, see every episode with `transcribed` chips and the count in the header; on a phone, open Filters and repeat the MID toggle; on Admin, read the settle row's counts and press Run-now on settle with `gw 12, force true`.
- [ ] Docs: `docs/SPEC.md` S9 block marked done with the sha; `docs/runbook.md`; `docs/adr/0005-creator-record-and-votes.md` (distinct votes, the positional starter median, position from the current `player` row, the 25 floor on weight only, the enclosure audio fallback); no new env variable.
- [ ] The PR description carries the surface inventory of 8.6 and the sentence asking the owner's word on the watch cards.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src scripts` returns nothing.

### 12. Out of scope

| Not in S9 | Where, if anywhere |
|---|---|
| Weighted consensus, creator weight in any model or the solver | v2; `weight` is stored and shown, never multiplied |
| Buy calls judged against the same-price band (R4 opinion) | v2; `benchmark_kind` admits a second kind |
| Position at the deadline from a series table | an S1 change, `position` on `player_state`, if the owner wants it; ADR 0005 |
| Person-level attribution inside multi-host shows | v2; 7.5 draws the show's stance under each host and says so |
| Pasting a YouTube link, a ninth creator | v2; a migration plus an ADR |
| Chat tools over the four panels | S7, reading the `headline` functions here |
| The Dashboard's dissent voice from `creators_board` | S5 |
| Ownership crawls beyond the 11 verified entries | D10, never |
| Old `claim_outcome` and `creator_score` rows | never loaded; settle recomputes |
| Deleting the watch cards | waits for the owner's word on the PR |

Digest facts that pull against a decision, and the resolution. ARCH lists `creator_pick` and `creator_gw` under `squad_refresh.writes`; S1 deferred them to S6 and S6 created neither, so S9 creates both. R4 section 1.6 resolved position at the deadline; the new schema has no position series, so 7.1 reads the current row and records the bucket in `benchmark_kind`. ARCH says a podcast plus YouTube pair is one `item`; S6 made two rows with `pair_item_id`, so 7.3 counts votes. R4 section 4.2's review dropped the said-versus-owned matrix; D7 and rule 35 keep it folded. The S0 helper counts `count(*)`, which reads every update-only task as a silent nothing; section 2 adds `Counted`.

### 13. Size and session plan

About 15 source files and 2,100 lines, plus 12 test files and 1,100 lines, in this order:

| # | File | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0008_s9_creator_scores.py` | 110 |
| 2 | `desk/creators/score.py` (`wilson`, `weight`, the 7-scope aggregate) | 90 |
| 3 | `desk/creators/settle.py` (candidates, votes, medians, verdicts, the upsert with revision) | 220 |
| 4 | `desk/creators/consensus.py` (votes, per-action counts, net, hhi) | 90 |
| 5 | `desk/creators/chatter.py` (`snippets`, `said`, `owned`, `deep_link`) | 120 |
| 6 | `desk/jobs/tasks/creators_settle.py`, `creators_score.py`; `desk/jobs/runner.py` (`Counted`); `desk/jobs/registry.py` (`After`, if needed) | 120, 80, +30, +10 |
| 7 | `desk/fplapi/squads.py` (creator entry branch), `desk/jobs/tasks/squad_refresh.py` (second loop) | +60, +50 |
| 8 | `desk/panels/creators_board.py`, `player_chatter.py`, `creator_episodes.py`, `episode_summary.py`; `player_detail.py`, `admin_creators.py` edits | 240, 110, 110, 130, +20, +40 |
| 9 | regenerate `contracts/` and `web/src/api/schema.d.ts` |  |
| 10 | `web/src/components/StanceChip.tsx`, `OwnedRing.tsx`; move `AudioAt.tsx` | 40, 20 |
| 11 | `web/src/screens/creators/CreatorsScreen.tsx`, `Board.tsx`, `CreatorsTable.tsx`, `ConsensusFold.tsx`, `TakesFold.tsx`, `ReportCards.tsx`, `SaidOwnedGrid.tsx`, `PlayerDrawer.tsx`, `EpisodeDrawer.tsx`, `CreatorDrawer.tsx`, `filters.ts` | 160, 140, 90, 80, 70, 80, 120, 140, 150, 110, 60 |
| 12 | `web/src/screens/projections/PlayerDrawer.tsx` (snippets fold) | +30 |
| 13 | `scripts/feature_check.py` (`creators` line), `docs/adr/0005-creator-record-and-votes.md`, `docs/SPEC.md` | +40, 60, +10 |

Fixtures under `tests/fixtures/creators/`: `settle_gw.json` (30 players, the double, the absent player, one claim per action, one late claim, one pair) and `expected_verdicts.json`; two creator entry bodies under `tests/fixtures/fpl/`; every instant computed 30 days ahead of a frozen clock.

Where a session goes wrong, and the guard:

1. Settle and the board count claim rows, so a podcast plus YouTube pair votes twice and n climbs with publication volume. Guard: test 7 pins `n 1` and `claims_total 1` on a pair; `duplicate_of` is a stored column the test reads back.
2. The verdict freezes at its first write, or `resolved_at` is stamped on every run so `rows_written` inflates. Guard: test 5 asserts the revision advances on new results, test 6 asserts `resolved_at` is byte-equal on an unchanged run, test 10 proves the helper still catches an unexplained nothing.
3. The screen fetches per filter or per fold, or calls `player_chatter` per bar to draw the matrix, and the mount passes 3 calls (the old tab fired 40). Guard: test 16 counts requests on mount and on a filter change; 7.5 names the fields the matrix reads.
4. Level 1 puts toolbars and a chart before the first fact again, or drops a fold to meet the height budget. Guard: test 16 pins 1,400 px folded and four folds; 8.6 lists every old surface with its new location, and the PR cannot merge without it.


# Part 13. Step S7: chat on the user's own Console key

Step 9 of 10 in the build order of ARCH.md section 11. Depends on S0, S1, S8, S2, S3, S4, S9. Target: deployed and green before the GW14 deadline. Spelling is British. Every path is relative to the repo root `fpl-desk/`; the Python package is `desk`. ARCH sections 7 and 8 fix the runtime. Where ARCH is silent the choice is marked `DECISION:`.

### 1. Goal

A signed-in user pastes an Anthropic Console API key on Account and asks the app questions in plain English; the reply is built from the same panels the screens read, streams in, names any panel that failed on its first line, and prints its cost in USD under every reply. The owner can do this from a phone at a deadline and remove the key in one click.

### 2. Depends on

| Step | Used by S7 |
|---|---|
| S0 | tables `app_user` (`chat_model`, `entry_id`, `entry_name`), `credential`, `session`; modules `desk.auth.credentials` (regex, AES-GCM, `CREDENTIAL_CARD_COPY`, the sentinel test), `desk.auth.policy`, `desk.core.models`, `desk.core.prices`, `desk.core.prose_style`, `desk.panels.registry` (`Panel`, `PANELS`, `Ctx`, `Empty`, the envelope); routes `GET /api/panels/{name}`; the `me` panel (`has_key`, `last4`, `chat_model`); `scripts/feature_check.py`; the `token` tier and `POST /auth/check` for the check user |
| S1 | panels `deadline`, `me_squad`; table `event` (`deadline_at`, `is_next`) for the volatile system block |
| S8 | the rail with the Chat entry, `Drawer`, `StateBox`, `Age`, `Number`, `CiteChip`, `Fold`, `ConfirmButton`, `DataTable`, the Account screen on the kit, `usePanel`, `schema.d.ts` generation, the prose check over `web/src/screens` |
| S2 | panels `fixtures_board`, `fixture_detail` |
| S3 | panels `projections_table`, `player_detail`, `provider_accuracy` |
| S4 | panels `solver_form`, `solver_plan`, `solver_status`; `SolveSettings` (pydantic, `extra="forbid"`) and `POST /api/solver/run` for the `propose_solve` card |
| S9 | panels `creators_board`, `player_chatter`, `creator_episodes`, `episode_summary` |

The tool list is computed from `PANELS` at import time, so S5's `dashboard` becomes a tool with no S7 code change.

### 3. User stories

User role:

1. I open Chat with no key stored. The composer is replaced by the credential card with the S0 copy and a link to Account. Result: no model call happens and the screen says why.
2. I paste `sk-ant-api03-...` on Account and press Save. The app makes one 1-token call, stores the key encrypted, and shows `Key ending 7Q2f, verified 0 d ago`. Result: `me.has_key` is true and the Chat composer appears.
3. I paste a `sk-ant-oat01-...` value. Result: 400 with the sentence "That is a Claude Code token and cannot be used here. Paste a Console API key." and nothing stored.
4. I ask "captain Fernandes or Haaland in GW14". The reply streams; four tool folds appear; the answer leads with the two xPts numbers and their spread; the footer reads `0.19 USD, 41 s, 4 tools, Sonnet`. Result: one `chat_message` row per API message with `usd` set, summed in the conversation header.
5. A tool returns `empty` (the solver has no plan). Result: the first line of the reply reads `panels that failed: panel_solver_plan (empty: No plan yet)` and the fold shows the reason.
6. I ask for a chart of Haaland's xPts by provider. Result: a bar chart rendered by Recharts inside the reply, with `source_panel` printed under it.
7. I ask "plan my transfers with Haaland locked". Result: a solve card with the validated settings and an estimate; nothing runs until I press `Run solve`, which calls `POST /api/solver/run` and links to `/solver/run/{job_id}`.
8. I press Stop mid-reply. Result: the stream ends within 5 s, the blocks that landed stay, the footer reads `stopped` and the cost so far.
9. I reload the page mid-reply. Result: the thread replays from the stored rows and continues live from the last block.
10. I switch the model to Opus for one conversation. Result: the header shows `Opus: median 0.83 USD and 102 s per reply in the old app` before the first Opus turn, and every footer on that thread says `Opus`.
11. I press Remove on Account. Result: the row is gone, `me.has_key` is false, the Chat composer is replaced by the card again, and any turn attempt answers 403 `no_api_key`.
12. A reply contains "Your process is idle". Result: the loop asks for one rewrite with tools off; the footer reads `style: 1 rewrite`; a second failure stores the text with a `style` chip listing the findings.

Admin role:

13. I open Admin, Users tab. Result: each user row shows `last4`, `month_chat_usd`, `turns_30d`; no conversation content anywhere on Admin.
14. I run the feature check. Result: the `chat` line reads `chat ok` with the check turn's USD and seconds, or names the failing assertion.

### 4. Data

One migration, `desk/migrations/versions/0009_s7_chat.py` (the session takes the next free number after S9's head). It creates two tables and alters one. Money is `numeric(12,4)` USD; every timestamp is `timestamptz`.

#### 4.1 `conversation`

Why: one row per thread so the list screen, the header total and the admin count come from one place. Read by panels `conversations`, `conversation`, `admin_users`; written only by `desk/chat/store.py`.

| Column | Type | Constraint |
|---|---|---|
| `conversation_id` | uuid | PK, default `gen_random_uuid()` |
| `user_id` | uuid | NOT NULL, FK `app_user` ON DELETE CASCADE |
| `title` | text | NOT NULL, `CHECK (char_length(title) BETWEEN 1 AND 80)` |
| `model` | text | NOT NULL, `CHECK (model IN ('claude-sonnet-5','claude-opus-5'))` |
| `turns` | integer | NOT NULL, default 0 |
| `usd_total` | numeric(12,4) | NOT NULL, default 0 |
| `last_turn_at` | timestamptz | NULL until the first turn |
| `in_flight_turn` | integer | NULL; the turn number while a turn runs, cleared at finalise |
| `created_at`, `updated_at` | timestamptz | NOT NULL |

Index `conversation_user_updated (user_id, updated_at DESC)`. `title` is the first 60 characters of the first user message, cut at a word boundary, until renamed. `DECISION:` no model-authored titles, because a title call would bill the user.

#### 4.2 `chat_message`

Why: one row per Messages API message, in API order, so history replay to the model and replay to the browser read the same rows. Read by `conversation`, `admin_users` (sums only); written only by `desk/chat/store.py`.

| Column | Type | Constraint |
|---|---|---|
| `conversation_id` | uuid | PK part, FK `conversation` ON DELETE CASCADE |
| `seq` | integer | PK part, 1-based, dense |
| `turn` | integer | NOT NULL, the user message count that started this exchange |
| `role` | text | NOT NULL, `CHECK (role IN ('user','assistant'))` |
| `kind` | text | NOT NULL, `CHECK (kind IN ('user_text','assistant','tool_results','rewrite'))` |
| `blocks` | jsonb | NOT NULL, default `'[]'`, an array of typed blocks (section 4.3) |
| `complete` | boolean | NOT NULL, default false; true once the last block is written |
| `model` | text | NULL on user rows, a bare model id on assistant rows |
| `tokens_in`, `tokens_out`, `cache_read`, `cache_write` | integer | NULL on user rows, NOT NULL on complete assistant rows |
| `usd` | numeric(12,4) | NULL on user rows; this call's cost |
| `duration_ms` | integer | NULL on user rows |
| `stop_reason` | text | NULL, one of `end_turn`, `tool_use`, `max_tokens`, `refusal` |
| `ended_by` | text | NULL except on the last row of a turn; `CHECK (ended_by IN ('end_turn','stop','idle_timeout','hard_cap','tool_cap','deploy','error'))` |
| `error` | jsonb | NULL; `{code, detail}` when `ended_by = 'error'` |
| `style_findings` | jsonb | NULL; the list from `slop_findings` that survived the rewrite |
| `style_rewrites` | smallint | NOT NULL, default 0 |
| `created_at`, `updated_at` | timestamptz | NOT NULL |

Table constraint: `CHECK (role = 'assistant' OR usd IS NULL)`. Index: the PK serves every read (`WHERE conversation_id = $1 AND seq > $2 ORDER BY seq`). A block is written whole when it completes; deltas are not stored.

#### 4.3 Block shapes

```
{"type":"text","text":"Haaland 7.9 xPts, Fernandes 6.4 ..."}
{"type":"thinking","signature":"EqQBCk...","thinking":""}
{"type":"tool_use","id":"toolu_01A","name":"panel_player_detail","input":{"code":223094,"gws":[14,15,16]}}
{"type":"tool_result","tool_use_id":"toolu_01A","name":"panel_player_detail","ok":true,
 "empty":null,"truncated":false,"is_error":false,"elapsed_ms":212,"bytes":9140,
 "preview":"{\"player\":{\"code\":223094,...","inputs":[{"kind":"projection","id":"fplform","as_of":"2026-11-27T08:02:11Z"}]}
{"type":"chart","spec":{"type":"bar","title":"Haaland xPts GW14 by provider","unit":"xPts",
 "series":[{"name":"GW14","points":[["fplform",7.9],["airsenal",7.1],["fplreview",8.2]]}],"source_panel":"panel_player_detail"}}
{"type":"solve_proposal","settings":{"horizon":5,"locked":[223094],"hits_max":0},"estimate_s":300,"max_horizon":5}
```

`preview` is the first 4,096 bytes of the JSON sent to the model; the full result is never stored, because past turns replay as text only (section 7.3). `thinking.thinking` is empty under the default display; the block is kept for the same-turn replay the API requires. A `chart` or `solve_proposal` block sits beside its `tool_result` block and is what the screen renders.

Sample rows for one turn with one tool call:

```
seq 5  turn 3  role user       kind user_text     complete t  blocks [text]
seq 6  turn 3  role assistant  kind assistant     complete t  blocks [thinking, text, tool_use]  stop_reason tool_use  usd 0.0121  tokens_in 5812 cache_read 4900
seq 7  turn 3  role user       kind tool_results  complete t  blocks [tool_result]
seq 8  turn 3  role assistant  kind assistant     complete t  blocks [thinking, text]  stop_reason end_turn  ended_by end_turn  usd 0.0388  duration_ms 14210
```

#### 4.4 `credential` (alter)

`DECISION:` add `last_used_at timestamptz NULL` and `last_error text NULL`. `last_used_at` is stamped at the start of every turn; `last_error` holds the Anthropic error class name (`authentication_error`, `permission_error`, `rate_limit_error`) from the newest failed call and is cleared by the next successful call; a message body is never stored. Account shows both. The ciphertext path is S0's.

#### 4.5 Tables read

`app_user` for `chat_model`, `entry_id`, `entry_name`; `credential` for the ciphertext; `event` for the next deadline; every table a called panel reads, through the panel. Chat writes to no table other than `conversation`, `chat_message` and the two new `credential` columns; `test_chat_writes` greps `desk/chat` for table names and fails on any other.

### 5. Jobs

S7 registers no job in `JOBS`. A chat turn runs in the web process on the user's key, has no operator meter and never touches the worker. The unit of accounting is the turn, with the visibility of a job row:

| Property | Where it lives |
|---|---|
| trigger | the user's `POST .../turn`; `turn` on every row |
| counts | `tool_calls` (count of `tool_use` blocks in the turn), `tokens_in`, `tokens_out`, `cache_read`, `cache_write` on each assistant row |
| cost | `usd` per assistant row from `desk/core/prices.py`; `conversation.usd_total` and `admin_users.month_chat_usd` are sums |
| timeout | idle 120 s, hard 600 s, tool 10 s, 12 tool calls; each has its own `ended_by` word |
| failure | `ended_by = 'error'` with `error.code`; the SSE `error` event; the footer prints the code |
| silent nothing | a turn that produced no assistant row is impossible: the user row and an assistant row are inserted before the first model call, and a finalise that finds zero completed blocks writes `ended_by = 'error'`, `error.code = 'no_output'`. The feature check `chat` line fails when the check turn's last row lacks `usd` |

S5's `retention` counts `chat_message` rows in its ledger note and deletes none.

### 6. API and panels

Every route below has a tier row in `desk/auth/policy.py`; CSRF applies to every non-GET route; errors use the S0 shape `{error, detail, remediation_url}`. The budget is 10 s per request; the turn route is the one exception, bounded by the 600 s hard cap.

#### 6.1 Credential and model routes (session)

| Method, path | Body | Result | Errors |
|---|---|---|---|
| `PUT /api/account/credential` | raw JSON `{"key": "sk-ant-api03-..."}` read with `await request.json()`, no pydantic model, so a 422 cannot echo it | 200 `{"last4":"7Q2f","verified_at":"2026-11-27T09:14:07Z","model_checked":"claude-sonnet-5"}` | 400 `credential_format` "Paste a Console API key. It starts with sk-ant-api."; 400 `credential_is_claude_code_token` with the S0 sentence; 400 `credential_rejected` "Anthropic rejected this key ({error_class})." for 401 and 403 from the API; 503 `verify_unavailable` "Anthropic did not answer the check ({error_class}). Try again in a minute." for 429, 5xx and connection errors, nothing stored |
| `DELETE /api/account/credential` | none | 204 | 404 `no_api_key` when no row |
| `PUT /api/account/chat-model` | `{"chat_model":"claude-opus-5"}` | the `me` result | 400 `invalid_params` outside the two ids |

The verify call: `Anthropic(api_key=key, max_retries=0, timeout=15.0).messages.create(model=VERIFY_MODEL, max_tokens=1, messages=[{"role":"user","content":"ok"}])`. The key lives in one local variable, is encrypted, and is dropped. A key that verifies replaces any existing row.

#### 6.2 Conversation routes

| Method, path | Tier | Body or params | Result | Errors |
|---|---|---|---|---|
| `POST /api/chat/conversations` | session | `{"model": "claude-sonnet-5" \| "claude-opus-5" \| null}` (null takes `app_user.chat_model`) | 201 `{"conversation_id","title":"New conversation","model"}` | 403 `no_api_key` |
| `PATCH /api/chat/conversations/{id}` | session | `{"title": str 1..80}` or `{"model": id}` | the conversation row | 404; 409 `turn_in_flight` for a model change mid-turn |
| `DELETE /api/chat/conversations/{id}` | session | none | 204 | 404; 409 `turn_in_flight` |
| `POST /api/chat/{id}/turn` | session | `{"text": str 1..4000}` | `text/event-stream` (section 6.4) | 403 `no_api_key` `{detail: "No Anthropic API key on this account.", remediation_url: "/account"}` before any decrypt; 404; 409 `turn_in_flight` `{turn}`; 429 `chat_busy` "3 replies are streaming on this server. Try again in a minute."; 400 `invalid_params` |
| `POST /api/chat/{id}/stop` | session | none | 200 `{"stopping": true, "turn"}` or `{"stopping": false}` when nothing runs | 404 |
| `GET /api/chat/{id}/events` | session | `after_seq` int default 0 | `text/event-stream`: every row with `seq > after_seq` as `row` events, then live events while a turn runs, then `done` or a plain close | 404 |

Another user's conversation is 404, so ids do not leak; every read joins on the session's `user_id`.

#### 6.3 Panels

Both are session tier, `extra="forbid"` on params and result, served by the S0 route factory, never chat tools (ARCH section 8).

`conversations`: params none. Budget 300 ms. Result:

```
{ "rows": [ { "conversation_id", "title", "model", "turns", "usd_total", "last_turn_at", "created_at", "in_flight": false } ],
  "month": { "period": "2026-11", "usd": 3.4120, "turns": 41, "by_model": { "claude-sonnet-5": 2.9010, "claude-opus-5": 0.5110 } },
  "chat_model": "claude-sonnet-5", "has_key": true, "last4": "7Q2f", "last_used_at": "...", "last_error": null }
```

Ordered by `updated_at` desc, newest 100. `DECISION:` `conversations` returns `rows: []` and never `Empty`, because the key state is needed to draw the composer; the screen renders the empty box from `rows.length`. Provenance inputs: `conversation`, `credential`.

`conversation`: params `{conversation_id: uuid}`. Budget 500 ms, under 250 KB (the `preview` cap keeps 20 turns near 180 KB). Result:

```
{ "conversation": { ...the row... },
  "rows": [ { "seq", "turn", "role", "kind", "complete", "blocks": [...], "model", "usd", "duration_ms",
              "tokens_in", "tokens_out", "cache_read", "cache_write", "stop_reason", "ended_by", "error",
              "style_findings", "style_rewrites", "created_at" } ],
  "turn_footers": [ { "turn", "usd", "duration_ms", "tool_calls", "model", "style_rewrites", "style_findings", "ended_by" } ] }
```

`turn_footers` is computed server side so the screen and the SSE `done` event print one string. A conversation with no rows returns `rows: []`. Another user's id: 404. Provenance inputs: `conversation`, `chat_message`.

`admin_users` (S0, extended): each row gains `turns_30d` and `conversations_30d`; `month_chat_usd` becomes `SUM(chat_message.usd)` for the calendar month. No content field is added. `me` (S0) gains nothing; `has_key` and `last4` already exist.

#### 6.4 The turn stream

`POST /api/chat/{id}/turn` answers `200 text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. Events, each `event:` line then one `data:` line of JSON, `id:` set to `{seq}.{block_index}`:

| Event | Data | When |
|---|---|---|
| `row` | `{seq, turn, role, kind, complete, blocks}` | a row is inserted, and on replay for every stored row |
| `delta` | `{seq, index, text}` | a text delta; not stored |
| `block` | `{seq, index, block}` | a block completed and was written; sent after the write commits |
| `tool_start` | `{seq, index, name, input}` | a tool begins; the fold opens in its running state |
| `done` | `{turn, seq_last, usd, duration_ms, tool_calls, model, style_rewrites, style_findings, ended_by}` | the turn finalised |
| `error` | `{error, detail, remediation_url}` | the turn ended by error; followed by `done` |
| `: ping` | comment | every 15 s of silence |

Reconnect: the client calls `GET /api/chat/{id}/events?after_seq=N` with the last `seq` it saw complete. The server replays rows above `N` and, when this process owns the in-flight turn, subscribes to the live fan-out. `DECISION:` when the turn is owned by another process (a second web replica, allowed but not run in v1) the server replays and then polls `chat_message` every 2 s until `in_flight_turn` is null, emitting `row` events for changed rows. Web runs 1 replica in v1, so the poll path runs only in test 13.

Shutdown: the lifespan handler sets a flag; every in-flight turn finalises with `ended_by = 'deploy'` and the blocks that landed, and the client prints `This reply ended when the app was redeployed. The blocks above are kept.`

#### 6.5 Chat tools

Every session-tier row in `PANELS` except `me`, `conversations` and `conversation` becomes a tool `panel_<name>`, `description` from the row, `input_schema` from `params.model_json_schema()`. At S7 that is 15 panels: `deadline`, `me_squad`, `fixtures_board`, `fixture_detail`, `projections_table`, `player_detail`, `provider_accuracy`, `solver_form`, `solver_plan`, `solver_status`, `creators_board`, `player_chatter`, `creator_episodes`, `episode_summary`, plus `dashboard` once S5 lands. Two more tools live in `desk/chat/tools.py`:

| Tool | Input | Returns | Never |
|---|---|---|---|
| `propose_solve` | `SolveSettings` (S4's model, every field optional) | `{settings: the validated block with defaults filled, estimate_s, max_horizon, coverage_note}`; the loop writes a `solve_proposal` block | inserts a job, calls the worker, or bypasses `POST /api/solver/run` |
| `chart` | `{type: "bar" \| "line" \| "scatter", title: str 1..80, unit: str 1..20, series: [ {name: str, points: [[x: str \| number, y: number]] } ] (1 to 4 series, 2 to 60 points each), source_panel: str}` | `{ok: true}`; the loop writes a `chart` block | runs code, reads a table, or accepts a `source_panel` that was not called earlier in this turn (returns `is_error` "chart cites panel_x, which this turn did not call") |

Tool definitions are sorted by name so the tools prefix is byte-stable for the cache. The list is passed on every call; after the tool cap the call uses `tool_choice: {"type": "none"}` so history with `tool_use` blocks stays valid.

### 7. Algorithms

#### 7.1 The loop (`desk/chat/loop.py`, about 180 lines)

Constants, each in `desk/chat/limits.py`:

| Constant | Value | Reason |
|---|---|---|
| `MAX_TOKENS` | 8,000 | a two-table answer is under 2,000 tokens; the cap bounds a runaway |
| `EFFORT` | `medium` | the skill's guidance for chat routes; the analyst's work is in the panels |
| `TOOL_CALL_CAP` | 12 | ARCH section 8; the old median was 13 sub-turns on Opus with 36 tools |
| `RESULT_BYTES_CAP` | 20,480 | ARCH section 8; the old MCP cap overflow sent the agent to SQL |
| `TOOL_WALL_S` | 10 | the panel budget rule |
| `IDLE_S` | 120 | ARCH section 8; the old 300 s idle hid a dead CLI for five minutes |
| `HARD_CAP_S` | 600 | measured p90 206 s, max 319 s (R5) |
| `PER_PROCESS` | 3; `PER_USER` 1 | ARCH section 8 |
| `HISTORY_TURNS` | 20 | 20 turns of text is under 30,000 tokens |
| `STYLE_REWRITES_MAX` | 1 | ARCH section 8 |

Pseudocode:

```
turn(conv, user_text):
  key = decrypt(credential row) or raise NoApiKey (403 before this point in the route)
  client = AsyncAnthropic(api_key=key, max_retries=2, timeout=HARD_CAP_S)
  insert user row (kind user_text, complete)
  messages = history_as_text(conv, HISTORY_TURNS) + [user_text]
  calls = 0; started = now(); tool_calls = 0
  loop:
    row = insert assistant row (complete false)
    tool_choice = {"type":"none"} if tool_calls >= TOOL_CALL_CAP else {"type":"auto"}
    stream = client.messages.stream(model, max_tokens, system=[frozen(cache_control), volatile],
                                    tools=TOOLS, tool_choice, thinking={"type":"adaptive"},
                                    output_config={"effort": EFFORT}, messages)
    for event in stream, each awaited under wait_for(IDLE_S):
      text delta -> send delta
      content_block_stop -> write block to row.blocks, send block
      stop flag set -> break with ended_by stop
      now() - started > HARD_CAP_S -> break with ended_by hard_cap
    final = stream.get_final_message(); write usage, usd, stop_reason; mark row complete
    messages.append(assistant content)
    if final.stop_reason == "tool_use":
      results = gather(run_tool(b) for tool_use blocks b, each under wait_for(TOOL_WALL_S))
      insert user row (kind tool_results) with every tool_result block in one message
      tool_calls += len(results); continue
    break
  final_text = join text blocks of the last assistant row
  text2 = normalize_prose(final_text); findings = slop_findings(text2)
  if findings and rewrites < STYLE_REWRITES_MAX:
      insert user row (kind rewrite) "rewrite without these constructions: ..." ; one more call with tool_choice none
      repeat normalise and findings on the new text; rewrites = 1
  write style_findings, style_rewrites, ended_by on the last assistant row
  update conversation (turns, usd_total, last_turn_at, in_flight_turn null); send done
```

`normalize_prose` runs before storage on every text block, so an em-dash never reaches a row. The `rewrite` user row is stored; the screen folds it into the footer as `style: 1 rewrite`. Stop: `POST .../stop` sets a per-turn `asyncio.Event`; the loop checks it on every stream event and after every tool, and the stream exits on the next event, under 5 s. A turn ended by `stop`, `idle_timeout`, `hard_cap` or `tool_cap` still runs the prose gate and writes usage from the partial message when the SDK has it, else zeros with `error.code = 'usage_unavailable'`.

Error mapping inside the loop: `AuthenticationError` and `PermissionDeniedError` end the turn with `error.code = 'credential_rejected'`, write `credential.last_error`, and the composer shows the card sentence `Anthropic rejected the stored key ({class}). Replace it on Account.`; `RateLimitError` ends with `rate_limited` and the detail `Anthropic rate limit on your key. Wait a minute.`; `APIConnectionError` and `APIStatusError` 5xx end with `anthropic_unavailable`; `stop_reason == "refusal"` ends with `refused` and the text `Anthropic declined this request.`; `stop_reason == "max_tokens"` appends the text `[reply cut at 8,000 tokens]` and ends normally. The chain is caught most specific first.

#### 7.2 System prompt and cache

Two system blocks. Block 1 is `desk/chat/charter.md`, frozen text, with `cache_control: {"type": "ephemeral"}`; the loop reads it once at import. It holds: the role ("You are the analyst behind fpl-desk. Every number you print comes from a tool result in this turn."); `STYLE_RULES` from `desk/core/prose_style.py` verbatim; the panel fallback rule lifted from `fpl_edge/platform/chat_agent.py` `PANEL_FALLBACK_RULE`, reworded to: "if any tool returned `ok:false`, `empty` or `truncated`, the FIRST line of the answer is `panels that failed: <tool>(<reason>), ...`"; the currency rule ("every gain names its unit, horizon and source, as in `+14.35 xPts over GW14 to GW18 versus rolling, consensus`"); the tool guide (one line per tool family, squad first, solver last; `propose_solve` returns a card the user runs); the chart rule (only from numbers already in this turn's tool results); the no-guess rule (an ambiguous player name gets a question back). Block 2 is volatile and sits after the breakpoint: `Entry {entry_id} ({entry_name}). Next deadline GW{gw} at {deadline_at}, {hours_left} h away. Today is {date}.` at day granularity.

Sonnet 5 caches a prefix of 1,024 tokens or more, Opus 5 512 (R7 section 2). `test_charter_cacheable` asserts the charter has at least 1,100 words, a floor above 1,024 tokens, and that the tools list is sorted. The frozen block and the tools prefix never contain a timestamp or the user id; `test_charter_frozen` renders the system block twice for two users and asserts block 1 is byte-identical.

#### 7.3 History and tool results

Past turns replay to the model as text: for each turn under the cap, the `user_text` row's text and the final assistant row's text blocks joined, with `[stopped]` appended when `ended_by` was not `end_turn`. `tool_use`, `tool_result`, `thinking` and `rewrite` rows from past turns are not sent. The current turn's chain is sent whole, with thinking blocks unchanged, as the API requires. A conversation past `HISTORY_TURNS` shows a chip `Older turns are not sent to the model` and the composer offers `New conversation`.

A tool result to the model is the JSON of `{ok, result, empty, truncated, provenance: {computed_at, inputs}}`. Over `RESULT_BYTES_CAP` it becomes `{ok, headline: Panel.headline(result), truncated: true, bytes}`. A pydantic `ValidationError` on the input, a raised panel, or a tool wall-clock timeout returns `tool_result` with `is_error: true` and the class name; the loop never drops a result, and every result of one assistant message goes back in one user message.

#### 7.4 Cost

From `desk/core/prices.py`, USD per MTok, R7 section 2: Sonnet 5 in 2.00, out 10.00, cache read 0.20, cache write 2.50; Opus 5 in 5.00, out 25.00, cache read 0.50, cache write 6.25. Per call `usd = (input_tokens * in + output_tokens * out + cache_read_input_tokens * read + cache_creation_input_tokens * write) / 1e6`, rounded to 4 places at storage, summed unrounded for the footer. `test_cost` feeds `usage` of 5,812 in, 410 out, 4,900 cache read, 0 cache write on Sonnet and asserts 0.0158. The footer prints two decimals: `0.02 USD`.

#### 7.5 Concurrency and the watchdogs

A module-level `asyncio.Semaphore(PER_PROCESS)` and a `dict[user_id, turn]` guard the route; the dict entry is set before the first insert and cleared in `finally`. The idle watchdog is `asyncio.wait_for(anext(stream), IDLE_S)` per event; a `TimeoutError` ends the turn with `idle_timeout`. `test_idle_watchdog_survives_long_streams` streams 40 events 4 s apart through a fake client and asserts the turn ends `end_turn`, then streams one event 121 s later under a frozen clock and asserts `idle_timeout`.

### 8. UI

#### 8.1 `/chat` and `/chat/c/{conversation_id}`

Primary visual: the thread, one column at most 760 px wide, centred in the 1,280 px content width. A user message is a right-aligned surface card. An assistant turn is Markdown rendered by `react-markdown` with `remark-gfm` (`DECISION:` both packages are added; ARCH names no Markdown renderer), tables through the shared `Table`, numbers through `Number`. The first line of a reply that starts `panels that failed:` renders inside a `StateBox` of kind `gap` above the prose.

Secondary content and where it folds:

| Content | Where |
|---|---|
| Tool calls | one `ToolFold` per `tool_use` block, closed by default, summary `panel_player_detail · 212 ms · 9 KB · ok`; states `running` (spinner word `running`), `ok`, `empty` (warn badge with the reason), `error` (bad badge with the class), `truncated` (warn badge `truncated to headline`); expand shows the params as a two-column list and the preview in a `pre` block, and a `CiteChip` per provenance input |
| Charts | inline, a Recharts `BarChart`, `LineChart` or `ScatterChart` at 100 percent width and 240 px, series colours from the four series tokens, `unit` on the axis, `source_panel` in a caption |
| Solve proposals | inline `SolveCard`: the settings that differ from the S4 defaults as a list, `Estimated {estimate_s} s`, button `Run solve` (calls `POST /api/solver/run` with CSRF; on 202 the card reads `Queued, job {job_id}` with a link to `/solver/run/{job_id}`; on 400 or 409 the card prints the error detail); button `Edit on Solver` (link to `/solver` with the settings in the URL) |
| Footer | under every reply, muted 11 px: `0.19 USD, 41 s, 4 tools, Sonnet`; with rewrites `style: 1 rewrite`; with surviving findings a warn `Badge` `style` whose tooltip lists them; with `ended_by` not `end_turn` the word (`stopped`, `idle timeout`, `hard cap`, `tool cap`, `ended by deploy`, `error: {code}`) |
| Conversation list | a 240 px left rail at 820 px and above, the `Drawer` under 820 px opened by a `Conversations` button in the header; rows show title, `Age`, `usd_total`; a `New conversation` button at the top; rename on double click, delete through `ConfirmButton` `Delete this conversation` |
| Thread header | title, model `Select` (`Sonnet` or `Opus`; picking Opus shows one line under it: `Opus: median 0.83 USD and 102 s per reply in the old app`), thread total `{usd_total} USD, {turns} turns`, a `Stop` button while a turn runs |

Filters and sorts: none on the thread. The list sorts by `updated_at` desc, fixed. Drill-down: a `CiteChip` inside a fold opens the source panel's own level-2 URL in the same tab (`/projections/p/{code}`, `/fixtures/t/{team_code}/gw/{gw}`, `/creators/p/{code}`, `/solver/run/{job_id}`); a `SolveCard` link opens the solver. Level 1 is the thread, level 2 is the fold, level 3 is the panel's own screen.

Composer: a `Textarea` up to 6 lines, placeholder `Ask about your squad, a player, a fixture or a plan`, button `Send` (Enter sends, Shift+Enter breaks a line), replaced by `Stop` while a turn runs. With `has_key` false the composer is replaced by the credential card: the S0 `CREDENTIAL_CARD_COPY` verbatim, one password `Input` with placeholder `sk-ant-api03-...`, button `Save key`, link `Manage on Account`. The card appears nowhere else on the screen.

Empty state, no conversations: `StateBox` kind `empty`, reason `No conversations yet`, fix `Ask a question below`; a conversation with no rows shows the composer with focus. Loading: the list skeleton at 6 rows, the thread skeleton at 3 blocks; a streaming reply shows a 2 px accent bar under the last block. Error: a thrown request renders `StateBox` kind `error` with the S0 error detail; a `403 no_api_key` swaps in the card; a `429 chat_busy` renders the detail above the composer with the composer enabled; a `409 turn_in_flight` reconnects to the running turn instead of showing an error.

Mobile at 390 px: the list is behind the header button; the thread is full width with 16 px gutters; the composer is sticky at the foot above the bottom tab bar; folds and charts are full width; tables inside a reply scroll inside their own box; no horizontal page scroll. The header keeps the model select and the Stop button; the thread total moves into the list drawer.

Mount budget: 2 panel calls (`conversations`, `conversation`) under 250 KB. No request on fold, expand or scroll.

#### 8.2 Account, credential card (extends S0's `/account`)

Row 3 of the arrival card. With no key: the S0 copy verbatim, a password `Input`, button `Save key`. On Save: the button reads `Checking` and is disabled; success renders `Key ending {last4}, verified {Age}, last used {Age or never}` and buttons `Replace` and `Remove` (the latter through `ConfirmButton` `Remove the key from this app`); the errors of section 6.1 render as one line under the input, verbatim from the response `detail`. With `last_error` set: a warn `Badge` `last call failed: {last_error}` beside the key line.

Why one credential kind (D1). Anthropic's legal page at https://code.claude.com/docs/en/legal-and-compliance forbids third-party apps from offering Claude.ai login or storing, routing or intermediating Claude.ai credentials and session tokens, and points developers to Console API keys. The app therefore accepts `sk-ant-api` keys only and has no "sign in with Claude" button. The card copy names console.anthropic.com as the place to create a key and says every reply is billed to the user's Anthropic account. The owner's Max subscription never touches the deployed app.

Row 4, model: label `Chat model`, a `ToggleGroup` `Sonnet` and `Opus`, default from `me.chat_model`, with the Opus line from section 8.1 shown under the group whenever Opus is selected. Saving calls `PUT /api/account/chat-model` at once; a new conversation takes it, an open one keeps its own.

Row 5, usage: `This month: {usd} USD over {turns} replies` from `conversations.month`, with `by_model` as two mono numbers. Empty when zero: `No replies this month`.

#### 8.3 Components

Shared, used: `Drawer`, `StateBox`, `Age`, `Number`, `CiteChip`, `Fold`, `ConfirmButton`, `Table`, shadcn `Button`, `Input`, `Textarea`, `Select`, `ToggleGroup`, `Badge`, `Skeleton`, `Tooltip`. New, added to the kit under `web/src/components/`: `ToolFold` (a `Fold` with the five tool states), `TurnFooter` (the one footer string, also used by S5 for the brief's cost line), `ChartBlock` (Recharts over the `chart` spec), `SolveCard`, `Markdown` (the one `react-markdown` wrapper, with the `Table` and `Number` mappings). `test_one_markdown_renderer` greps `web/src` for `react-markdown` imports and fails on more than one file.

### 9. Admin view

The admin sees, on the Users tab: `last4`, `month_chat_usd`, `turns_30d`, `conversations_30d`, `last_used_at` per user, sortable. On the Spend tab one line under the meters reads `Chat runs on each user's own key; see Users for per-user USD.` On the feature check card (S5): the `chat` line with its USD and seconds.

A user never sees: another user's conversation (404 by join), the `admin_users` payload, any `last4` but his own, the operator variable name (grep test), or any panel above his tier through a tool (tools are built from session-tier rows only; `test_tools_walk` asserts no admin-tier name is in `TOOLS`).

The admin never sees: any `chat_message` row, any `blocks`, any conversation title, or any credential beyond `last4`. `test_admin_never_reads_chat_content` greps `desk/panels/admin_*.py` for `blocks`, `title` and `chat_message.*text` and allows only the `SUM(usd)` and `COUNT(*)` reads.

### 10. Acceptance tests

In the order to write them. Every model call in a test goes through a fake `AsyncAnthropic` in `tests/unit/chat/fake_client.py` that yields scripted stream events.

1. `test_credential_put_rejects_and_stores`: `sk-ant-oat01-...` answers 400 with the S0 sentence and no row; `sk-ant-api03-...` with the fake verify answering 200 stores a row with `last4`, `verified_at` and `key_version 1`; a fake 401 answers 400 `credential_rejected` naming `authentication_error` and stores nothing; a fake 429 answers 503 `verify_unavailable` and stores nothing.
2. `test_credential_sentinel_through_chat` (extends S0's): a stored key containing `SENTINEL7Q` runs one fake turn; the substring appears in no SSE event, response body, header, caplog record, `chat_message` byte or `conversation` byte.
3. `test_turn_403_before_decrypt`: with no credential row, `POST .../turn` answers 403 `no_api_key` with `remediation_url /account` and the fake client constructor is never called.
4. `test_chat_writes_only_chat_tables`: an AST and grep walk over `desk/chat` finds writes only to `conversation`, `chat_message` and `credential.last_used_at`, `credential.last_error`; finds no `OPERATOR_` literal, no `os.environ` read, no `subprocess`, no `desk.web` import.
5. `test_tools_walk`: `TOOLS` equals `panel_<name>` for every session-tier `PANELS` row except `me`, `conversations`, `conversation`, plus `propose_solve` and `chart`; sorted by name; every `input_schema` has `additionalProperties: false`; no admin-tier name is present.
6. `test_turn_rows_and_cost`: a scripted turn with one tool call writes rows seq 1 to 4 with the kinds of section 4.3, `usd` 0.0158 on the first assistant row from the scripted usage, `complete` true on all four, `conversation.turns 1` and `usd_total` equal to the sum.
7. `test_silent_turn_is_error`: a fake stream that ends with zero content blocks lands `ended_by error`, `error.code no_output`, and the `done` event carries it; the row count is still 2 (user plus assistant).
8. `test_panel_fallback_first_line`: with `panel_solver_plan` scripted to return `Empty("No plan yet", ...)`, the charter and a fake model that echoes its tool results produce a reply whose first line starts `panels that failed: panel_solver_plan`; the test also asserts the charter text contains the rule sentence verbatim.
9. `test_result_cap_and_headline`: a panel result of 60 KB reaches the model as `{headline, truncated: true}` under 20,480 bytes and the stored `tool_result` block has `truncated true` and a `preview` of 4,096 bytes.
10. `test_tool_cap`: a fake model that always calls a tool stops after 12 calls; the 13th call is made with `tool_choice none`; `ended_by tool_cap`.
11. `test_prose_gate_rewrite`: a scripted first text "Your process is idle" triggers one `rewrite` row and a second call with `tool_choice none`; a clean second text stores `style_rewrites 1` and `style_findings null`; a dirty second text stores the findings; an em-dash in either is a comma in storage.
12. `test_stop_and_watchdogs`: a stop during a 30-event stream ends within 5 s of the flag with `ended_by stop` and the blocks so far; a 121 s gap under a frozen clock ends `idle_timeout`; a 601 s turn ends `hard_cap`.
13. `test_reconnect_replays_after_seq`: mid-turn, `GET .../events?after_seq=2` replays seq 3 and the incomplete row 4, then receives the live `block` and `done` events; a second call after the turn replays everything and closes.
14. `test_history_is_text_only`: the messages sent on turn 3 contain, for turns 1 and 2, one user text and one assistant text each and no `tool_use`, `tool_result` or `thinking` blocks; turn 21 drops turn 1.
15. `test_propose_solve_and_chart_tools`: `propose_solve({"locked":[223094]})` returns the S4 defaults filled and inserts no `job` row; `chart` with a `source_panel` not called this turn returns `is_error`; a valid chart writes a `chart` block.
16. `test_conversation_panels_contract`: `conversations` and `conversation` validate with `extra="forbid"`, match `contracts/panels/<name>.json`, stay under 250 KB at 20 turns, and answer 404 for another user's id; `conversations` on a user with no rows returns `rows: []` with `has_key`.
17. `test_deploy_ends_turns`: the lifespan shutdown with one turn running finalises it with `ended_by deploy` and the client message string is in the `done` event.
18. `test_chat_screen_states` (vitest): the composer is replaced by the card when `has_key` is false; a reply with `ended_by stop` prints `stopped`; a footer renders `0.19 USD, 41 s, 4 tools, Sonnet` from the `turn_footers` row; the empty list renders `No conversations yet`.
19. `test_prose_check_chat_strings`: `scripts/prose_check.py` over `web/src/screens/chat`, `desk/chat/charter.md` and the error strings in `desk/web/routers/chat.py` finds nothing.
20. `test_feature_check_chat`: `scripts/feature_check.py --lines chat` against `PUBLIC_URL` with the check user's session and its Console key from Actions secrets creates a conversation, posts `reply with the single word ok`, reads `done` within 60 s, asserts `ended_by end_turn`, `usd` under 0.05, `tool_calls` 0, and deletes the conversation; a failure names the assertion.

Every fix commit carries a `Break-watch:` trailer naming the test that failed.

### 11. Definition of done

- [ ] Tests 1 to 19 green locally with `uv run pytest` against a testcontainer; `tsc --noEmit`, eslint, vitest and `vite build` green; Playwright screenshots of `/chat` and `/account` at 1,280 and 390 in both themes with no horizontal scroll.
- [ ] CI green on `main`, including the prose check over the charter and the chat strings, the contract snapshot diff for `conversations` and `conversation`, and the regenerated `schema.d.ts`.
- [ ] `test_env_split` still green: web boots with no `ANTHROPIC_*` variable and `desk/chat` names no operator variable.
- [ ] Deployed to Railway on `main`; `deploy-check.yml` green with the feature-check lines `health`, `me`, `admin`, `fixtures`, `projections`, `solver`, `creators`, `chat`, `assets`, `ui budget` all green. The check user's key is a Console key with a 5 USD monthly limit set at console.anthropic.com, stored only in Actions secrets.
- [ ] The owner's demo script, before GW14: open Account, paste a Console key, read `Key ending ..., verified 0 d ago`; open Chat, type `captain Fernandes or Haaland in GW14`, press Send; watch four folds open and close; read a first line with two xPts numbers; read the footer `0.19 USD, 41 s, 4 tools, Sonnet`; expand a fold, click its age chip and land on `/projections/p/223094`; type `plan my transfers with Haaland locked`, read the solve card, press `Run solve`, land on `/solver/run/{job_id}`; type `chart Haaland xPts by provider`, read the bar chart; press Stop mid-reply, read `stopped`; reload mid-reply on another question and watch it continue; switch to Opus, read the median line, run one turn, read `Opus` in the footer; on Account press Remove and confirm; on Chat read the card in place of the composer; on Admin read `month_chat_usd` and `turns_30d` on his own row and no conversation text.
- [ ] Docs updated: `docs/SPEC.md` (this step's block marked done with the deploy date), `docs/runbook.md` (the chat section: limits, the `ended_by` words, the redeploy behaviour, how to rotate `CREDENTIAL_ENC_KEY` with `_PREV`), `README.md` (the two new packages and the chat env facts), `docs/adr/0007-chat-messages-api.md` (why the Messages API and not the Agent SDK, with the R5 and R7 facts).
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src scripts` returns nothing.
- [ ] The image self-check passes: no `sk-ant`, `AKIA` or `.env` in the built image.

### 12. Out of scope

| Not in S7 | Step or reason |
|---|---|
| `panel_dashboard` as a tool | S5 registers the panel; the tool appears through the walk |
| The brief and any scheduled model work on the operator key | S5 (`brief`) and S6 (`creators_extract`); D2 |
| Any credential kind other than a Console API key; any Claude.ai login | D1, never |
| Chat starting a job, writing a watchlist, saving an analysis, running SQL | never; the old `query`, `watchlist_*`, `save_analysis` tools are not ported |
| Server-side compaction, context editing, message batches | not in v1; the 20-turn text history is the bound |
| Model-authored conversation titles, conversation search, sharing a conversation | v2 |
| Thinking display (`summarized`) in the thread | v2; the block is stored with empty text |
| Per-user monthly chat caps enforced by the app | the user sets limits at console.anthropic.com; the app shows the month total |

### 13. Size and session plan

About 21 source files and 2,600 lines excluding tests; 12 test files and about 1,500 lines.

| Order | File | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0009_s7_chat.py` | 70 |
| 2 | `desk/chat/limits.py`, `desk/chat/cost.py` | 30, 30 |
| 3 | `desk/chat/charter.md` | 1,100 words |
| 4 | `desk/chat/store.py` (inserts, block append, finalise, history_as_text) | 180 |
| 5 | `desk/chat/tools.py` (the walk, `propose_solve`, `chart`, `run_tool`) | 160 |
| 6 | `desk/chat/loop.py` | 180 |
| 7 | `desk/chat/prose_gate.py` | 40 |
| 8 | `desk/web/sse.py`, `desk/web/routers/chat.py`, `desk/web/routers/credential.py` | 50, 220, 90 |
| 9 | `desk/panels/conversations.py`, `desk/panels/conversation.py`; `admin_users` extension | 70, 80, 20 |
| 10 | `desk/auth/credentials.py` (verify, the two new columns), `desk/auth/policy.py` rows | 40, 10 |
| 11 | `web/src/screens/chat/` (`ChatScreen.tsx`, `Thread.tsx`, `Composer.tsx`, `ConversationList.tsx`, `useTurnStream.ts`), `web/src/components/` (`ToolFold.tsx`, `TurnFooter.tsx`, `ChartBlock.tsx`, `SolveCard.tsx`, `Markdown.tsx`), `web/src/screens/account/` (`CredentialCard.tsx`, `ModelRow.tsx`, `UsageRow.tsx`) | 1,100 |
| 12 | `scripts/feature_check.py` (the `chat` line), `docs/runbook.md`, `docs/adr/0007-chat-messages-api.md`, `docs/SPEC.md` | 120 |

The three places a session goes wrong, with the guard:

1. **The key leaks through an error path.** A pydantic body model on `PUT /api/account/credential` echoes the key in a 422; an SDK exception `repr` carries the header; a log line prints the request. Guard: read the body raw, log the SDK class name only, write test 2 before the route, and pass the key nowhere except `AsyncAnthropic(api_key=...)`.
2. **Tool history breaks the API contract.** Dropping a `tool_result` from a parallel batch, sending results in two user messages, omitting `tools` when history holds `tool_use` blocks, or sending another model's thinking block each answer 400 mid-turn. Guard: one user message per batch built by `gather`, `tool_choice none` in place of an empty tools list, thinking blocks passed back unchanged within the turn and dropped between turns, and test 14 on the replayed shape.
3. **The stream sends before it writes.** A `block` event sent before the row commits means a reconnect after a crash replays a row missing a block the client already showed. Guard: `store.append_block` commits, then the event goes on the fan-out queue; test 13 kills the fake stream after a `block` event and asserts the row holds it.

Contradictions between digests and decisions: R7 recommendation 3 puts chat on `claude-opus-5`; D2 sets Sonnet as the default with Opus per user, and this spec follows D2. R5 section 2 keeps the `sk-ant-oat` path; D1 retires it. R7 asks for a frozen system prompt over 512 tokens; Sonnet's cache floor is 1,024, so the charter floor is 1,100 words. R5 section 3 recorded a 300 s idle window and a 1,500 s cap; ARCH section 8 sets 120 s and 600 s and wins.


# Part 14. Step S5: Dashboard v1, the brief, operations, the light audit

Step 10 of 10. Follows ARCH.md; where ARCH is silent, a line prefixed `DECISION:` records the choice. Spelling is British. Season 2026-27, target deadline GW15.

### 1. Goal

On Friday the owner opens the app and reads, in the first 700 px, what to do about transfer, captain, bench and chip, each with a confidence word, the field that decided it, and the plan captain beside the consensus captain from one payload. Under it he reads a daily model-authored brief whose every number was checked against the panel it cites, and on Admin he reads bucket bytes per prefix, the last backup and the last restore check's counts.

### 2. Depends on

| Step | Used by S5 |
|---|---|
| S0 | `job`, `spend`, `artefact`, `app_user`, `worker_heartbeat`; the ledger helper `jobs/runner.py`; `reserve(meter, estimate)`; `POST /api/admin/jobs/{task}/run`; `admin_jobs`, `admin_spend` panels; `scripts/feature_check.py`; `desk/db/bucket.py` |
| S1 | `event`, `player`, `team`, `player_state`, `user_squad`, `fixture`; `me_squad` and `deadline` panels; `squad_refresh` job; `desk/db/pit.latest_at` |
| S8 | the shell, `tokens.css`, `DataTable`, `Drawer`, `StateBox`, `PlayerCell`, `ClubMark`, `Age`, `Number`, `CiteChip`, `Fold`, `ConfirmButton`, `Pitch`, `usePanel`, the Dashboard v0 screen at `/` (the fifteen and the countdown), the Admin screen at `/admin` |
| S2 | `fixtures_board` panel (attack_ease, defence_ease, ranks per club per GW); the `fixture_ratings` artefact kind and its `state()` |
| S3 | `projection` table, `providers/consensus.py` (mean, n_sources, spread, p_appear per code per GW), `provider_accuracy` panel |
| S4 | `solver_plan` and `solver_status` panels; the `solver_plan` artefact kind with `squad_before`, `chosen`, `per_gw`, `roll`, `unconstrained`; its `state()` over `inputs`; the T+2h re-solve |
| S6 | `creators_transcribe` and its audio deletion rule; `desk/core/models.py` (`claude-sonnet-5`); `desk/core/prices.py`; `desk/core/prose_style.py` (`slop_findings`, `normalize_prose`, `STYLE_RULES`, `ungrounded`) |
| S9 | `creators_board` panel (consensus bars per player for the next GW, with creator counts) |
| S7 | the chat tool loop, which picks up `panel_dashboard` from the registry with no S5 code |

If S6 shipped a retention task under any name, S5 extends it and the final registry name is `retention`. There is one retention task.

### 3. User stories

User role:

1. I open `/` before a deadline and read four rows, transfer, captain, bench, chip, in that order, each with an answer, its numbers, a confidence word and the deciding field printed beside the word. Result: four rows within the first 700 px at 1,280 px wide, each carrying a rule id I can trace to a panel.
2. The captain row shows the plan captain and the consensus captain with both xPts from one payload; when the consensus captain leads by more than 1.5 xPts the row leads with the consensus captain and the plan pick becomes the dissent line. Result: two names and two numbers, and a rule id that says which order was taken.
3. I click a row and the working opens inline: the candidate table for transfers, the XI comparison for bench, the chip windows for chip. Result: one expanded block per row, no request fired.
4. I read the gap strip above the rows when something blocks an answer, with the fix beside each gap. Result: "No plan yet" with a Solve button; "Squad not read yet" with the squad_refresh age; nothing rendered when there are no gaps.
5. I read the brief under the rows: at most 8 items, each a headline with numbers, the panels it cites, and an "in your squad" mark. Result: a "model-authored" chip, the age in days, and the cost of the run.
6. I click a brief item and land on the screen it cites at the right level. Result: `/projections/p/223094` or `/fixtures/t/1/gw/15` or `/creators/p/223094` opens.
7. I open the Lineup fold and see the fifteen as a pitch with price and next-GW xPts, the captain marked, the bench in order. Result: the pitch from S8 v0 lives here; nothing from v0 is deleted.
8. I open the app on a phone at 390 px in light theme and read the same four rows. Result: no horizontal scroll, contrast at 4.5:1 on muted text.
9. I ask chat "what does the dashboard say about captain" and the reply cites `panel_dashboard`. Result: the same rule id and numbers as the screen.

Admin role:

10. I open `/admin` and read a Storage card with bytes and object counts per bucket prefix and the bytes freed by the last retention run. Result: six prefix rows with an age in days.
11. I read a Backups card with the newest dump's date, bytes and sha256, and a Restore check card with per-table counts, dump versus restored, and a status word. Result: `ok` with 27 table rows, or `error` naming the table that differed.
12. I press Run now on `brief` and read the estimate, the month's spend and the cap before confirming. Result: `{needs_confirm, estimate, month_spend, month_cap}` first; after confirm a job row with `trigger admin` and `usd` on it.
13. I read on the jobs board the brief row's items seen, items kept, items rejected by reason, tokens and USD. Result: a row where `items_seen 8, items_done 5, items_skipped {number_unknown: 2, style: 1}` is visible.

### 4. Data

S5 creates no table. It alters one and adds one artefact kind.

#### 4.1 `job.result` (alter)

DECISION: migration `0010_s5_job_result` adds `result jsonb NULL` to `job`. Why: `retention`, `pg_dump_to_bucket` and `db_restore_check` produce structured per-run output (bytes per prefix, counts per table) that `admin_spend` reads; `note` is a sentence and `log_tail` is text. The ledger helper writes `result` from the task's return value when it is a dict; a non-dict return leaves it NULL. Index: none. Size guard: the helper refuses a `result` over 64 KB with status `error`, note `result_too_large`.

Sample row (columns beyond the new one elided):

```
task: db_restore_check, status: ok, rows_written: 0,
result: {"dump_key": "backups/2026-11-29.dump.gz", "dump_job_id": 4412,
         "tables": [{"table": "player_fixture", "dump": 118201, "restored": 118201},
                    {"table": "projection", "dump": 231044, "restored": 231044}],
         "mismatched": [], "scratch_db": "desk_restore_20261201", "seconds": 214}
```

#### 4.2 `artefact` kind `brief`

Registered in `desk/artefacts/registry.py` as `ArtefactKind(name="brief", inputs_model=BriefInputs, payload_model=BriefPayload, reader_panel="dashboard", retention="all")`. `user_id` is NULL: one shared brief a day (ARCH section 13). Rows live in the existing `artefact` table.

`inputs` JSONB, `BriefInputs`:

| Field | Type | Meaning |
|---|---|---|
| `season`, `gw` | text, int | the next unfinished gameweek at run time |
| `panels` | list of `{name, as_of, params}` | the six panel reads the context was built from |
| `model`, `prompt_sha256` | text, text | `claude-sonnet-5`; sha of `desk/brief/prompt.md` plus `STYLE_RULES` |
| `context_bytes` | int | size of the JSON handed to the model |

`payload` JSONB, `BriefPayload`:

| Field | Type | Meaning |
|---|---|---|
| `items` | list, max 8 | kept items, see below |
| `rejected` | `{number_unknown, code_unknown, style, ungrounded, no_source}` ints | items dropped by the validator, by reason |
| `usage` | `{tokens_in, tokens_out, cache_read, usd, seconds}` | from the API response times `core/prices.py` |
| `findings` | int | `slop_findings` count over the kept text after normalisation; 0 on a healthy run |

A `BriefItem`:

| Field | Type | Rule |
|---|---|---|
| `kind` | enum `xpts_gap, creator_shift, availability, fixture_turn, price_pressure, disagreement, accuracy` | the salience catalogue, one word each |
| `headline` | text, 140 chars max | leads with a number; every number appears in a cited panel |
| `why` | text, 400 chars max | same rule |
| `source_panels` | list of `{name, as_of}` | at least one; names from `inputs.panels` only |
| `codes` | list of int | player codes present in the context; may be empty for a club item |
| `team_code` | int or null | for fixture items |
| `drill` | text | a route: `/projections/p/{code}`, `/fixtures/t/{team_code}/gw/{gw}`, `/creators/p/{code}` |

State from `inputs` alone, `state()`: `fresh` under 30 h, `aging` 30 to 48 h, `stale` over 48 h, `missing` when no row exists. `superseded` never applies to a brief. Refresh: daily 07:00 UTC by the `brief` job. Retention: all rows kept.

Sample kept item:

```
{"kind": "xpts_gap", "headline": "Semenyo 6.4 xPts GW15, 4 sources, above every MID you hold (best 5.1, Fernandes)",
 "why": "projections_table as of 2026-11-27T08:02Z: Semenyo 6.4, spread 0.9. creators_board: 5 of 8 shows buy for GW15.",
 "source_panels": [{"name": "projections_table", "as_of": "2026-11-27T08:02:11Z"}, {"name": "creators_board", "as_of": "2026-11-27T04:10:40Z"}],
 "codes": [232413, 223094], "team_code": null, "drill": "/projections/p/232413"}
```

#### 4.3 Reads, no writes

The `dashboard` panel reads `event`, `user_squad`, `player`, `player_state` (through `latest_at`), the consensus over `projection` (through `providers/consensus.py`), the newest `solver_plan` artefact for the session user, the newest `brief` artefact, and `fixtures_board` for the opponent chips on the pitch. It writes nothing. Point-in-time: every series read passes `t = now()`.

### 5. Jobs

All four run in the worker on the pipeline lane. Each is a `Job` row in `jobs/registry.py` with `writes` set so the helper counts rows.

#### 5.1 `brief`

| Field | Value |
|---|---|
| Trigger | Calendar `0 7 * * *` UTC; admin Run-now with confirm; `trigger` never defaults |
| Inputs | six panel calls in process with fixed params: `projections_table` (gws next 5, top 80 by consensus sum, every position), `fixtures_board` (horizon 6, lens both, basis club), `creators_board` (next GW, every creator), `provider_accuracy` (season), `deadline`, plus a player-state delta list: players whose `status`, `chance_next` or `price_tenths` changed in the last 7 days, max 60 rows |
| Context | pruned to 40 KB; over that, tables are cut from the tail and `context_bytes` records the size |
| Model | `claude-sonnet-5` from `core/models.py`; `messages.create`, `max_tokens` 4,000, one `cache_control` breakpoint on the prompt; temperature 0 |
| Outputs | one `artefact` row kind `brief` |
| Ledger | `items_seen` = items the model returned; `items_done` = kept; `items_skipped` by reason; `rows_written` 1; `tokens_in`, `tokens_out`, `usd`, `model` |
| Cost cap | `reserve("anthropic_usd", 0.06)` before the call; refusal lands as `status refused` with the period total in `note`; `MONTHLY_CAP_ANTHROPIC_USD` 25 |
| Timeout | model call 300 s; `budget_s` 420; the subprocess-free task is killed by the helper at `budget_s + 30` |
| Failure | API error or parse failure is `status error` with the exception class in `note`; the previous brief stays and the dashboard shows its age; 0 kept items with 1 or more seen is `status ok` with `items_done 0` and note `all_items_rejected` |
| Silent nothing | the Dashboard brief block shows "Brief: 2 days old" in the `Age` chip; `admin_jobs` shows the row stale against a 30 h window; the feature check line `dashboard` fails on `brief.created_at` older than 30 h |

#### 5.2 `retention`

| Field | Value |
|---|---|
| Trigger | Calendar `0 4 * * *` UTC |
| Inputs | the bucket listing per prefix; `raw_payload`, `job`, `artefact`, `item` |
| Rules | `raw/`: delete objects older than 30 days and their `raw_payload` rows. `audio/`: delete objects whose `item.transcript` is set and `analysed_at` is before today 00:00 UTC (S6's rule, kept). `backups/`: keep the newest 8 `.dump.gz` plus their `.sha256`. `uploads/`, `seed/`: never deleted, counted only. `job` rows: delete `finished_at` older than 180 days except `task = solve` (keep 30 per user) and `task = brief` (keep all). `artefact`: `solver_plan` keep 30 per user, `fixture_ratings` keep 30, `brief` keep all |
| Outputs | deletes; `result` = `{prefixes: [{prefix, objects_deleted, bytes_freed, objects_remaining, bytes_remaining}], tables: [{table, rows_deleted}]}` |
| Ledger | `items_seen` = objects listed plus rows examined; `items_done` = deleted; `rows_written` 0 by construction, so the helper's unexplained-nothing rule is satisfied through `items_skipped {retained: n}` which the task always writes |
| Cost cap | none (bucket ops are free on Railway) |
| Timeout | `budget_s` 600 |
| Failure | a bucket listing error is `status error`; a partial delete records what was freed before the error |
| Silent nothing | the Storage card on Admin shows `freed_last_run` and a `last run` age; the feature check line `operations` fails when any prefix's retention row is older than 26 h |

#### 5.3 `pg_dump_to_bucket`

| Field | Value |
|---|---|
| Trigger | Calendar `0 3 * * 0` UTC (Sunday) |
| Inputs | `DATABASE_URL` |
| Method | open a connection, `BEGIN ISOLATION LEVEL REPEATABLE READ`, `SELECT pg_export_snapshot()`, count every table in that transaction, run `pg_dump --format=custom --snapshot=<id> --no-owner` piped through `gzip -6` to `backups/{YYYY-MM-DD}.dump.gz` with multipart upload, then write `backups/{YYYY-MM-DD}.sha256` last, then commit. DECISION: the snapshot pins the counts and the dump to one instant so the restore check can assert equality |
| Outputs | two bucket objects; `result` = `{key, bytes, sha256, tables: [{table, rows}], seconds}` |
| Ledger | `items_seen` = tables counted (27 plus `alembic_version`), `items_done` same; `rows_written` 0; `items_skipped {}`; the helper accepts this because `writes` names the bucket prefix and the task reports `bytes` over 0 in `result`; `bytes` 0 is `status error` note `empty_dump` |
| Cost cap | none |
| Timeout | `budget_s` 900 |
| Failure | upload error is `status error`; a partial object is deleted before the row is written; the manifest is written last so a listing never shows a dump without its sha |
| Silent nothing | the Backups card shows the newest dump's age; the feature check line `operations` fails when the newest `.sha256` is older than 8 days |

DECISION: the Dockerfile's python stage installs `postgresql-client-16` so `pg_dump` and `pg_restore` match the server's major version; `test_pg_client_version` asserts `pg_dump --version` starts with `pg_dump (PostgreSQL) 16`.

#### 5.4 `db_restore_check`

| Field | Value |
|---|---|
| Trigger | Calendar `30 3 1-7 * 1` UTC (first Monday); admin Run-now |
| Inputs | the newest `backups/*.dump.gz` and its manifest; the dump's `job.result.tables` |
| Method | verify sha256 against the manifest; `CREATE DATABASE desk_restore_{YYYYMMDD}` on the same server; `pg_restore --no-owner --dbname=<scratch>`; `count(*)` per table in the scratch; compare with `dump.result.tables`; `DROP DATABASE` in a `finally` |
| Outputs | `result` = `{dump_key, dump_job_id, tables: [{table, dump, restored}], mismatched: [table], scratch_db, seconds}` |
| Ledger | `items_seen` = tables compared; `items_done` = tables equal; `items_skipped {}`; `rows_written` 0 |
| Cost cap | none |
| Timeout | `budget_s` 1,800 |
| Failure | any mismatch is `status error` with note `restore_mismatch: player_fixture`; sha mismatch is `status error` note `manifest_mismatch`; the scratch database is dropped on every path, and a leftover `desk_restore_*` database from a crash is dropped at the start of the next run and counted in `items_skipped {stale_scratch_dropped: 1}` |
| Silent nothing | the Restore check card on Admin shows the last run's age and status; the feature check line `operations` fails when the newest `db_restore_check` row with `status ok` is older than 35 days |

### 6. API and panels

Every panel is one row in `PANELS` and gets its route `GET /api/panels/{name}` from the registry, `response_model` set, params as query parameters, the standard envelope `{ok, panel, result | null, empty, provenance}`. Errors: 400 `{error: "invalid_params", field, detail}`; 401 `{error: "not_signed_in"}`; 403 `{error: "forbidden"}`; 500 `{error: "panel_failed", panel}`. Budget 10 s; each panel below declares `budget_ms` and reports `over` in provenance.

#### 6.1 `dashboard` (session)

Params: none (`extra="forbid"`). `budget_ms` 1,500. Chat tool: yes, as `panel_dashboard`; `headline` returns `rows[].{question, rule, answer, confidence}` and `gaps[].text` only.

Result schema:

```
{ gw: int, deadline_at: str, seconds_left: int,
  squad_source: { kind: "public_picks", gw: int, as_of: str, autosub_reversed: bool,
                  free_transfers: int, ft_source: "account" | "accrual",
                  bank_tenths: int, chips_played: [str] } | null,
  gaps: [ { id: str, text: str, fix: str,
            action: { kind: "link" | "solve" | "none", target: str | null } } ],
  rows: [ DecisionRow, DecisionRow, DecisionRow, DecisionRow ],
  lineup: { formation: str, xi: [PitchCard x11], bench: [PitchCard x4],
            captain: int, vice: int, xi_xp_sum: float, source_as_of: str } | null,
  brief: { artefact_id: int, created_at: str, model: str, usd: float,
           items: [ BriefItem + { in_squad: bool } ], rejected: {...}, findings: int } | null,
  thresholds: { captain_divergence_xpts: 1.5, captain_close_call_xpts: 0.5,
                bench_margin_xpts: 0.5, plan_fresh_h: 12, plan_aging_h: 48,
                brief_fresh_h: 30, ratings_stale_h: 168 } }

DecisionRow:
{ question: "transfer" | "captain" | "bench" | "chip",
  rule: str, answer: str, unit: str | null,
  numbers: { <name>: number | null },
  confidence: "none" | "stale" | "contested" | "firm",
  deciding_field: str,
  state: "fresh" | "aging" | "stale" | "superseded" | "missing" | null,
  source_panel: str, source_as_of: str | null,
  players: [PlayerRef], dissent: [ { text: str, source_panel: str, numbers: {...} } ],
  working: TransferWorking | CaptainWorking | BenchWorking | ChipWorking,
  drill: str }

PlayerRef: { code, web_name, position, team_code, price_tenths, xp_next: float | null, status: str }
PitchCard: PlayerRef + { multiplier: int, slot: int, opponent: { team_code, home: bool, ease: float } | null }
TransferWorking: { plan_job_id: int | null, moves: [ { out: PlayerRef, in: PlayerRef, gw: int } ],
                   gain_over_roll: float | null, hits: int, hit_points: int, horizon_gws: [int],
                   forecast_source: str, unconstrained: { n_transfers, hits, gain_over_roll } | null }
CaptainWorking: { candidates: [ { player: PlayerRef, xp: float, p_appear: float, n_sources: int } ], plan_captain: PlayerRef | null, consensus_captain: PlayerRef | null }
BenchWorking: { current_xi: [int], best_xi: [int], swaps: [ { in: PlayerRef, out: PlayerRef, swing: float } ] }
ChipWorking: { chip: str | null, gw: int | null, remaining: [str], windows: [ { chip, gws: [int, int] } ] }
```

Provenance `inputs` names: `user_squad` with `as_of`, `projection` consensus with the newest `as_of` per provider, `artefact:solver_plan` with `artefact_id` and `created_at`, `artefact:brief` with `artefact_id`, `fixture_ratings` with `fitted_at`. Empty: when the session has no `entry_id`, `Empty(reason="no_team", fix="Save your FPL team id on Account")`.

#### 6.2 `admin_spend` (admin, extended)

Params: none. `budget_ms` 2,000. Not a chat tool (admin tier). S5 adds three keys to the S0 result, the old keys unchanged:

```
{ meters: [...unchanged...],
  bucket: [ { prefix: "raw/" | "audio/" | "uploads/" | "backups/" | "seed/" | "exports/",
              objects: int, bytes: int, freed_last_run: int, deleted_last_run: int,
              retention_run_at: str | null, retention_status: str | null } ],
  backup: { key: str, bytes: int, sha256: str, created_at: str, job_id: int } | null,
  restore_check: { job_id: int, ran_at: str, status: str, dump_key: str,
                   tables: [ { table, dump, restored } ], mismatched: [str] } | null }
```

Reads `job.result` of the newest `retention`, `pg_dump_to_bucket` and `db_restore_check` rows; no bucket call at request time.

#### 6.3 Existing endpoints used, unchanged

`POST /api/admin/jobs/brief/run` (admin, CSRF) returns `{needs_confirm: true, estimate: 0.06, month_spend, month_cap}` without `confirm: true`, else `{job_id}`. `POST /api/solver/run` (session, CSRF) is what the gap strip's Solve button calls. `GET /api/panels/solver_status` is polled at 5 s while a solve is running.

### 7. Algorithms

#### 7.1 The four rows

Precedence and rule ids, lifted from the old `brief/plan.py` lines 725 to 984 and re-sourced to the new panels. `P` is the newest `solver_plan` artefact for the user with its `state()`; `plan` is its `chosen` block, read only when the state is `fresh` or `aging`. `C` is the consensus for the next GW over the user's fifteen: `xp`, `p_appear`, `n_sources` per code, `fpl_ep` included only when `consensus_member` is true. `S` is `me_squad`.

Transfer row:

| Condition, in order | rule | answer template | numbers |
|---|---|---|---|
| no `S` | `no_squad` | "Squad not read yet" | {} |
| no `P` | `no_plan` | "No plan yet" | {} |
| `P.state` is `superseded` | `plan_superseded` | "Plan solved against a different fifteen" | {age_h} |
| `P.state` is `stale` | `plan_stale` | "Plan is {age_days} days old" | {age_h} |
| `plan.n_transfers` is 0 | `solver_plan_roll` | "Roll. {ft} free transfer(s) banked" | {gain_over_roll: 0, ft} |
| else | `solver_plan_moves` | "{out} out, {in} in, {gain:+.2f} xPts over GW{a} to GW{b} versus rolling, consensus, {hits} hit(s)" | {gain_over_roll, hits, hit_points, n_transfers} |

`unit` is "xPts over GW{a} to GW{b} versus rolling, consensus". Dissent: when `P.unconstrained` exists and its `gain_over_roll` exceeds `plan.gain_over_roll` by 4 or more, one dissent line "Hit-taking best: {n} moves, {hits} hits, {gain:+.2f}".

Captain row, with `cons` the argmax of `C.xp` over the fifteen and `solver_cap` = `plan.captain`:

```
if plan and solver_cap and cons.code != solver_cap.code
   and C.xp[cons] - C.xp[solver_cap] > 1.5:            rule consensus_captain_over_solver, pick cons, dissent = solver pick
elif plan and solver_cap:                               rule solver_plan_captain, pick solver_cap
elif cons:                                              rule mean_xpts_captain, pick cons
else:                                                   rule no_captain_named
if pick and C.xp[pick] - C.xp[runner_up] < 0.5:         numbers.close_call = true
```

Both names and both xPts are in `numbers` on every path: `pick_xp`, `consensus_xp`, `solver_xp`, `runner_up_xp`, `p_appear`. The 1.5 gate and the 0.5 close call come from the old `THRESHOLDS` (`schema.py` lines 60 to 70) and are echoed in `thresholds`.

Bench row: `best_xi(C, S)` picks the XI. For each of the 8 legal formations (1-3-4-3, 1-3-5-2, 1-4-3-3, 1-4-4-2, 1-4-5-1, 1-5-3-2, 1-5-4-1, 1-5-2-3), take the top-k per position by `xp` and sum; the maximum sum wins, ties by fewer changes from the current XI. Bench order: outfield bench sorted by `xp` descending, GK last. Compare with `S.picks` multipliers: swaps are the players that move between XI and bench. Rule `bench_inversion_applied` with `numbers {n_changes, swap_delta_xpts}` when any swap's swing exceeds 0.5 xPts; `bench_confirmed` with `{n_changes: 0}` otherwise; `no_bench_named` when `C` covers fewer than 11 of the fifteen. `answer`: "Start {in} for {out}, +{swing:.1f} xPts" or "Bench order holds".

Chip row: `plan.chip` when the plan renders; rule `solver_plan_chip` with `{chip, gw}` and answer "{Chip} in GW{gw} per plan"; `chip_hold` with answer "Hold. {n} chips left: {list}" when the plan has none; `no_chip_named` when no plan. `remaining` is the four chips minus `S.chips_played`, each with its window from `rules/registry.yaml`.

Confidence lattice, from served fields only, in this precedence (lifted from the old `home.js` lines 17 to 25):

| Word | Condition | deciding_field |
|---|---|---|
| `none` | `rule` starts with `no_` | `rule` |
| `stale` | `state` in {stale, superseded, aging}, or `squad_source.as_of` older than 24 h | `state` or `squad_source.as_of` |
| `contested` | `dissent` non-empty, or `numbers.close_call` true | `dissent` or `close_call` |
| `firm` | none of the above | `rule` |

The panel computes the word and the field; the screen prints both and computes nothing.

Gaps, each with `id`, `text`, `fix`, `action`:

| id | when | fix, action |
|---|---|---|
| `no_team` | `me.entry_id` null | "Save your team id", link `/account` |
| `squad_pending` | no `user_squad` row for the season | "Reading your squad, usually under a minute", none |
| `no_plan` | no `solver_plan` artefact | "Run the solver", solve |
| `plan_superseded` | state superseded | "Solve again on the current fifteen", solve |
| `consensus_thin` | fewer than 2 sources for 11 or more of the fifteen | "Waiting on providers, newest fplform {age}", link `/projections` |
| `ratings_stale` | `fixture_ratings` older than 168 h | "Fixture fit is {age} old", link `/admin` when admin, else none |
| `brief_missing` | no brief row, or older than 48 h | "Brief runs daily at 07:00 UTC", none |

Answer templates live in `desk/panels/dashboard_copy.py`, one dict keyed by rule id, and `scripts/prose_check.py` covers that file. DECISION: answers are server-composed so the chat tool and the screen print one string.

#### 7.2 Brief validation

Lifted from the old `briefing_intel.py` (`prose_numbers`, `known_values`, `known_codes`, `item_problem`) and reduced to five checks, run in order per item; the first failure names the rejection reason:

1. `no_source`: `source_panels` empty, or a name not in the context.
2. `code_unknown`: a code not in the set of codes present in the cited panels.
3. `number_unknown`: any number in `headline` or `why` (regex `-?\d+(?:\.\d+)?`, percent signs stripped, gameweek numbers after "GW" excluded) that does not appear in a cited panel's values at the same number of decimals, tolerance 0.05 for one-decimal values. Integers under 10 are exempt (counts such as "5 of 8 shows" are checked against the panel's counts when the panel has a count column, else exempt).
4. `style`: `slop_findings(normalize_prose(text))` non-empty for headline or why.
5. `ungrounded`: `ungrounded(headline)` true (no digit anywhere).

Kept items are capped at 8 in the model's order. `payload.findings` is the count of `slop_findings` over the kept text after normalisation and is 0 by construction; the field exists so the done condition "the brief's findings count is 0" is a number on Admin.

The prompt, `desk/brief/prompt.md`, is the old `docs/platform/briefing_meta_prompt.md` with three edits: the objective line reads "P(top-10k)" for entry 4490171 and the brief is shared, so no item may name the owner's squad (the dashboard marks `in_squad` per viewer); the elite-lens and cohort-n rules are deleted because ownership crawls are out of v1 (D10); the seven salience kinds map one-to-one onto `BriefItem.kind` with `accuracy` added for a provider whose MAE moved more than 0.2 against the consensus in the last scored GW. `STYLE_RULES` is appended verbatim. The model must answer with a JSON array only; a fenced block is stripped; anything else is `status error` note `parse_failed`.

#### 7.3 Retention and restore

Retention keeps per-user solver plans by `ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY created_at DESC) > 30`; deletes run in batches of 500 rows with a commit per batch so a timeout leaves a consistent state. The restore comparison is `dump == restored` per table; `alembic_version` is compared too. Validation: a test seeds two tables, runs dump, inserts 3 more rows into production, runs the check, and asserts `ok` with the dump-time counts, proving the snapshot pin.

### 8. UI

Shared components used: `StateBox`, `Fold`, `Age`, `Number`, `CiteChip`, `PlayerCell`, `ClubMark`, `Pitch`, `ConfirmButton`, `Drawer`, shadcn `Badge`, `Tooltip`, `Skeleton`. New in S5, added to the kit with a grep test allowing one implementation each: `DecisionRow` (label, answer, numbers column, confidence word with deciding field, chevron, inline working) and `GapStrip` (a list of gap lines with one action each).

#### 8.1 Dashboard, `/`

Primary visual: the decision board, four `DecisionRow` blocks in the order transfer, captain, bench, chip, within the first 700 px at 1,280 px. Above it, `GapStrip` when `gaps` is non-empty, else nothing. A header line: "GW15, deadline in 2 d 4 h" (the one hours surface, from the shell's `deadline` query) and "Fifteen from public picks, 3 h ago, 1 free transfer (account)" from `squad_source`.

Row anatomy at 1,280 px: label 96 px in 11 px caps; answer in 17 px; numbers column right-aligned mono 13 px, 240 px wide, up to four `name value` pairs from `numbers` with `unit` once under the column; confidence word 12.5 px in caps with `deciding_field` in 10.5 px muted under it; a chevron. The word's colour is a status token: `none`, `stale` and `contested` warn, `firm` good, always with the text. Click anywhere on the row toggles the working block inline (no request): transfer as a 4-column table (out, in, GW, gain), captain as a candidate table (player, xP, p(app), sources) with both captains marked, bench as two eleven lists side by side with swaps highlighted, chip as the remaining chips with windows. A dissent line prints under the answer in 12.5 px with its `source_panel` as a `CiteChip`.

Secondary content and folds, in order under the board:

| Block | Fold | Summary line when closed |
|---|---|---|
| Brief | open by default, closable, state in `localStorage` key `desk.dashboard.brief` | "Brief: 5 items, 2 touch your squad, 14 h old, 0.04 USD" |
| Lineup | closed | "Lineup: 4-4-2, 52.3 xPts GW15, captain Haaland" |
| Working log | closed | "Inputs: squad 3 h, consensus 6 h, plan 14 h, fit 1 d" |

Brief items render as a list: kind as an 11 px caps label, headline 13 px, why 12.5 px muted, `CiteChip` per source panel, an "in your squad" `Badge` when `in_squad`, a "model-authored" `Badge` once in the block header with the model name and cost. Click on an item navigates to `drill`. Lineup renders `Pitch` with price and `xp_next` per card, captain marker, opponent chip on the fixtures ramp (attack ease for MID and FWD, defence ease for GKP and DEF, `@` for away). Working log lists `provenance.inputs` with an `Age` each.

Filters and sorts: none on this screen. Drill-downs: a player name anywhere opens the Drawer at `/p/{code}` (the S3 `player_detail` level); the transfer row's "Open plan" link goes to `/solver/run/{job_id}`; the captain row's candidates link to `/projections/p/{code}`; a gap's link action navigates; the `solve` action calls `POST /api/solver/run` with defaults and then polls `solver_status`, printing "Solving, 40 s" in the gap line and replacing the row on `done`.

Empty state: with `empty.reason == "no_team"`: "No team saved yet. Save your FPL team id on Account to see your decisions." with a button "Go to Account". Loading: `Skeleton` at the final geometry, four rows of 72 px and a brief block of 3 lines. Error: `StateBox` error only on a thrown request, "Dashboard failed to load ({status}). Retry" with a "Retry" button. Stale: the row's confidence word already carries it; the brief header shows `Age` with a warn token over 48 h.

Buttons and prompts, exact copy: "Solve", "Solving, {n} s", "Open plan", "Go to Account", "Retry", "Show working", "Hide working", "Brief", "Lineup", "Working log", "model-authored", "in your squad".

Mobile at 390 px: rows stack vertically, the numbers column drops under the answer as a two-column grid, the confidence word sits right of the label; the working block is full width with the transfer table hiding the GW column; the pitch scales to width; the brief list is unchanged; no horizontal scroll. Height under 3,200 px with every fold closed.

#### 8.2 Admin, `/admin` (extended)

Primary visual is the jobs board from S0, unchanged. S5 adds three cards under it, each a `Fold` open by default:

| Card | Content | Actions |
|---|---|---|
| Storage | a 6-row table: prefix, objects, bytes, freed last run, last run `Age`, status word | none |
| Backups | newest dump key, bytes, sha256 (first 12), created `Age` | "Run now" on `pg_dump_to_bucket` via `ConfirmButton` |
| Restore check | ran `Age`, status word, dump key, a table with 28 rows (table, dump, restored, equal mark); `mismatched` rows first with a bad token | "Run now" on `db_restore_check` via `ConfirmButton` |

The brief row on the jobs board shows `items_seen`, `items_done`, `items_skipped` by reason, `tokens_in`, `tokens_out`, `usd` in the row drawer, as every metered row does. Run now on `brief` shows the confirm dialog with the copy "Run brief now? Estimate 0.06 USD. This month 3.12 of 25.00 USD." and buttons "Run" and "Cancel". Empty states: "No retention run yet", "No dump yet", "No restore check yet", each with "Runs {schedule}" from the registry row. Mobile: the restore table hides `dump` into the row drawer; the Storage table hides `freed last run`.

#### 8.3 The light audit

Every screen (`/`, `/fixtures`, `/projections`, `/solver`, `/creators`, `/chat`, `/account`, `/admin`) is screenshotted by Playwright at 1,280 and 390 in light and dark. A vitest reads `tokens.css` and asserts muted and faint ink at 4.5:1 or better against both surfaces in both themes, the FDR ramp step 3 legible on the light surface, and the four series colours at 3:1 against both surfaces. Findings are fixed in token values only; a component-level colour is a test failure. The PR carries the 32 screenshots under `docs/ui/2026-11-light/` and a table of any token changed with before and after values.

### 9. Admin view

The admin sees on this step: the `brief`, `retention`, `pg_dump_to_bucket` and `db_restore_check` rows on the jobs board with last run, next due, status, counts, tokens and USD; the three cards above; Run now with confirm on the two metered or slow tasks and without confirm on `retention`; the log tail per job in the row drawer; the month's Anthropic spend against the 25 USD cap on the meters strip.

A user never sees: any job row, the log tail, the bucket listing, the dump key or sha, the restore counts, the operator spend, another user's plan or squad, the brief's `rejected` counts or `usage` (the dashboard passes `usd` and `created_at` only; `rejected` and `usage` are stripped in the `dashboard` panel and served only through `admin_jobs`). The `dashboard` panel reads `ctx.user_id` from the session and takes no user parameter, so no route can address another user's dashboard.

### 10. Acceptance tests

In the order to write them.

1. `test_job_result_column_migrates`: `alembic upgrade head` on an empty database adds `job.result jsonb NULL` and `downgrade -1` removes it.
2. `test_artefacts_walk_includes_brief`: the registry walk finds kind `brief` with `inputs_model`, `payload_model`, `state()` returning `fresh`, `aging`, `stale`, `missing` at 1 h, 36 h, 72 h and no row, reader panel `dashboard`, retention `all`.
3. `test_dashboard_contract`: on the seeded Postgres, `dashboard` with example params validates against `DashboardResult` with `extra="forbid"`, `provenance.inputs` names `user_squad`, `projection`, `artefact:solver_plan`, `artefact:brief`, `fixture_ratings`; the response is under 60 KB; the committed snapshot in `contracts/panels/dashboard.json` matches.
4. `test_dashboard_empty_no_team`: a session user with no `entry_id` receives `empty {reason: "no_team", fix}` and no exception.
5. `test_dashboard_squad_and_captain_agree_across_panels`: for the seeded user, the fifteen in `dashboard.lineup`, `me_squad.picks` and the newest `solver_plan.squad_before` are the same set, and `dashboard.rows[1].numbers.solver_xp` equals the plan's `captain_xpts`.
6. `test_captain_divergence_gate`: with consensus 6.14 for Fernandes and 3.99 for the plan's captain, the row's rule is `consensus_captain_over_solver`, the pick is Fernandes, the dissent names the plan captain; at a 1.4 gap the rule is `solver_plan_captain`.
7. `test_bench_best_xi_formations`: a fifteen whose best XI is 3-5-2 by consensus returns that formation; a fifteen with only two fit defenders returns `no_bench_named` (fewer than 11 covered) and no XI.
8. `test_confidence_lattice_precedence`: four fixtures, one per word, assert the word and `deciding_field`; a row that is both `no_` and contested reads `none`.
9. `test_gap_ids_and_actions`: each of the seven gap conditions yields its id, text and action; a user with everything present yields an empty list.
10. `test_brief_validator_rejects_by_reason`: five items, one per failure, land in `rejected` under the right key; a clean item is kept; a headline with 6.4 when the panel served 6.5 is `number_unknown`.
11. `test_brief_job_ledger`: with the model client stubbed to return 8 items of which 3 fail, the job row reads `items_seen 8, items_done 5, items_skipped {number_unknown: 2, style: 1}, rows_written 1, usd > 0`; with the stub returning an empty array the row is `status ok, items_done 0, note all_items_rejected`; with the stub raising, `status error` and the previous artefact is still the newest.
12. `test_brief_refused_under_cap`: `MONTHLY_CAP_ANTHROPIC_USD` 0 in the environment gives `status refused` with the cap and period total in `note`, and no model call is made (the stub asserts zero calls).
13. `test_brief_silent_nothing_is_visible`: a brief artefact 31 h old makes `dashboard.brief` render with `state aging`, `admin_jobs` list `brief` under `stale`, and `scripts/feature_check.py --only dashboard` exit non-zero naming `brief_age_h`.
14. `test_retention_rules`: seeded bucket stub and tables with objects at 29 and 31 days, 35 solver plans for one user, an audio object whose item has a transcript and one without, 9 backups; after the run the 31-day object, 5 plans, the transcribed audio and 1 backup are gone, `result.prefixes` has six rows with `bytes_freed` over 0 for `raw/`, `audio/` and `backups/`, and `items_skipped.retained` counts the survivors.
15. `test_pg_dump_snapshot_pin`: dump on a testcontainer, insert 3 rows into `player_state`, run `db_restore_check`; `status ok`, `tables` shows `player_state` equal at the dump-time count, the scratch database is absent afterwards.
16. `test_restore_check_mismatch_is_error`: a tampered dump (one row deleted from the restored scratch through a monkeypatched restore step) gives `status error`, note `restore_mismatch: player_state`, `mismatched ["player_state"]`, and the scratch is still dropped.
17. `test_admin_spend_cards`: `admin_spend` result carries `bucket` with 6 prefixes, `backup` from the newest dump job and `restore_check` from the newest check job; a user session gets 403.
18. `test_dashboard_screen_budget` (Playwright): `/` mounts with at most 3 panel calls, under 250 KB, layout shift 0, four `DecisionRow` elements whose bottom edge is under 700 px at 1,280, no horizontal scroll at 390, no `\b\d+\s*h\b` age string outside the header countdown, in both themes.
19. `test_light_contrast_tokens` (vitest): every ink token against both surfaces in both themes at 4.5:1 or better; series colours at 3:1.
20. `test_feature_check_s5` (probe against `PUBLIC_URL`, run by `deploy-check.yml`): line `dashboard` asserts `rows` length 4 with rule ids not starting with `no_` for the check user, `brief.created_at` under 30 h, `provenance.git_sha` equals the deploy; line `operations` asserts the newest `backups/*.sha256` under 8 days, the newest `db_restore_check` with `status ok` under 35 days, and a `retention` row under 26 h with six prefixes in `result`.

### 11. Definition of done

- [ ] Tests 1 to 20 green locally and in CI; `ruff`, `mypy --strict desk`, `tsc --noEmit`, eslint, `vite build`, contract drift and the prose checker green.
- [ ] `docs/runbook.md` regenerated from `JOBS` with the four rows and the sync test green.
- [ ] Migration `0010_s5_job_result` applied on Railway; `/api/health` reports `alembic_head == expected_head`.
- [ ] Deployed to Railway on `main`; `deploy-check.yml` green with every feature-check line green: health, me, fixtures, projections, solver, creators, chat, admin, assets, ui budget, dashboard, operations.
- [ ] `brief` has run on schedule at least once on Railway with `status ok` and `findings 0`; `retention` has a row with six prefixes; `pg_dump_to_bucket` has produced one dump with a manifest; `db_restore_check` has been run by admin Run-now with `status ok` and 28 equal tables.
- [ ] Feature check green for seven consecutive days (the ARCH S5 done condition).
- [ ] Owner's demo script, on Friday before GW15: sign in; read the four rows in order with the confidence word and deciding field of each; click the captain row and read the plan captain beside the consensus captain with both xPts; click "Show working" on the transfer row and read the moves table; read the brief, click one item and land on its drill route; open Lineup and see the pitch; switch to light in the header menu; open the same screen on a phone at 390 px; open `/admin`, read the Storage card's six rows, the Backups card's dump age and the Restore check card's counts; press Run now on `brief`, read the estimate and cap, confirm, and watch the row land `ok` with a USD figure.
- [ ] Docs updated: `docs/SPEC.md` S5 block marked done with the deploy sha; `docs/runbook.md` regenerated; `docs/adr/0005-brief-shared-and-checked.md` (one shared brief a day, the five validator checks); `docs/adr/0006-dump-snapshot-pin.md`; `docs/ui/2026-11-light/` screenshots and the token change table; `README.md` demo section.
- [ ] The PR carries the surface inventory for Dashboard v0 to v1: the fifteen (v0 top card) to the Lineup fold; the countdown (v0 header) to the header line; nothing deleted.
- [ ] `grep -rn "TODO\|FIXME\|XXX" desk web/src scripts` returns nothing.

### 12. Out of scope

| Not in S5 | Where, if anywhere |
|---|---|
| A brief per user, or brief items about the viewer's squad | v2; operator spend must not scale with strangers (ARCH section 13); `in_squad` is the v1 substitute |
| Season standing (overall rank series) | v2 with the rank layer (D10); no step creates an entry history table |
| Signals tiles from the old `tiles.py` | v2; the market ones need ownership crawls (D10); the brief's `price_pressure` and `availability` kinds cover the rest |
| A "Refresh" button on the dashboard | none; TanStack Query refetches on focus and every 60 s |
| Automatic re-solve when the fifteen change | S4's T+2h re-solve stays; a change-triggered solve is v2 |
| Push or Telegram delivery of the brief | D10 |
| Retention of `uploads/` and `seed/` | never deleted by design; counted only |
| Point-in-time restore or a second region | Railway managed backups cover it; the dump is the owner-held copy |
| Dark-mode audit | done in S8; S5 re-screenshots dark only to prove no regression |

### 13. Size and session plan

About 15 source files and 1,900 lines, plus 12 test files and about 1,100 lines. In this order:

| # | File | Lines |
|---|---|---|
| 1 | `desk/migrations/versions/0010_s5_job_result.py` | 25 |
| 2 | `desk/jobs/runner.py` (edit: write `result`, the 64 KB guard) | +30 |
| 3 | `desk/artefacts/brief.py` (`BriefInputs`, `BriefPayload`, `BriefItem`, `state`) | 90 |
| 4 | `desk/brief/context.py` (the six panel reads, pruning, `known_values`, `known_codes`) | 160 |
| 5 | `desk/brief/validate.py` (five checks) | 120 |
| 6 | `desk/brief/prompt.md` | 80 |
| 7 | `desk/jobs/tasks/brief.py` | 140 |
| 8 | `desk/panels/dashboard_copy.py` (answer templates by rule id) | 60 |
| 9 | `desk/panels/dashboard.py` (rows, lattice, gaps, best_xi, brief strip) | 320 |
| 10 | `desk/jobs/tasks/retention.py` | 180 |
| 11 | `desk/jobs/tasks/pg_dump.py`, `desk/jobs/tasks/restore_check.py` | 120, 140 |
| 12 | `desk/panels/admin_spend.py` (edit: three keys) | +60 |
| 13 | `web/src/components/DecisionRow.tsx`, `GapStrip.tsx` | 140, 50 |
| 14 | `web/src/screens/dashboard/Dashboard.tsx`, `BriefList.tsx`, `LineupFold.tsx`, `Working.tsx` | 160, 70, 60, 120 |
| 15 | `web/src/screens/admin/StorageCard.tsx`, `BackupCards.tsx` | 60, 90 |
| 16 | `scripts/feature_check.py` (edit: `dashboard`, `operations`), `Dockerfile` (edit: `postgresql-client-16`), `deploy/worker.railway.toml` (no change) | +60, +2 |

Regenerate `contracts/` and `web/src/api/schema.d.ts` after step 9 and again after step 12, before writing any screen.

Where a session goes wrong, and the guard:

1. The screen computes a confidence word or a captain comparison from raw fields. Guard: `DecisionRow` takes `confidence` and `deciding_field` as props and has no numeric branch; a vitest renders the four fixture rows and asserts the printed word equals the prop; the contract test asserts the word and field on every row. The rule is the old `home.js` line 25: no score is invented in the browser.
2. The brief validator is loosened to make a model answer pass. Guard: test 10 pins each rejection reason with an item one character off; `prompt_sha256` rides into `inputs` so a prompt edit is visible on every artefact; the job row's `items_skipped` makes a loosened check visible as a rejected count falling to 0 while items rise.
3. The restore check compares against production now, so it flaps after every write. Guard: test 15 inserts rows between dump and check and asserts equality at the dump-time counts; a grep test asserts `--snapshot` appears in `pg_dump.py`.

A fourth trap: retention deletes an audio object before its transcript lands. Guard: one statement with the predicate `item.transcript IS NOT NULL AND item.analysed_at < today`, and test 14 keeps the untranscribed object.


# Part 15. Migration from the old repository

The old repository is at `/Users/nripeshpradhan/Documents/Github/i-test-season` on the owner's Mac. Its warehouse is a DuckDB file that permits one writer, so every export runs against a copy. The commands and datasets are decided in Part 3 section 12 and repeated here.

### 12. Migration from the old repo

Export once on the Mac from a read-only copy of `data/warehouse/fpl.duckdb` with `scripts/export_old_repo.py`, each dataset as `COPY (<query>) TO '<name>.parquet' (FORMAT PARQUET, COMPRESSION ZSTD)` after `SET TimeZone='UTC'`:

```
cp data/warehouse/fpl.duckdb /tmp/wh.duckdb
uv run python scripts/export_old_repo.py --db /tmp/wh.duckdb --out /tmp/export
```

| Dataset | Query | Rows, size | Target |
|---|---|---|---|
| player fixtures 2022-23 to 2026-27 | `fact_player_fixture` | 115,809; 0.81 MB | `player_fixture` |
| identity and schedule | `dim_player`, `dim_team`, `fact_fixture`, `dim_event` | 13,781; 420; 9,586; 806; 0.2 MB | `player`, `team`, `fixture`, `event` |
| price and ownership series | `fact_player_state` | 135,391; 1.99 MB | `player_state` |
| projections; provider scores | `fact_projection WHERE source IN ('fplform','fpl_ep','gh_apex_airsenal','fplreview')`; `fact_projection_score` | about 150,000, 1.6 MB; 236 | `projection` with `gh_apex_airsenal` renamed `airsenal`; `provider_score` |
| items and transcripts | `content_item` with `transcript_segment` aggregated to JSONB per item | 1,070; 6.05 MB | `item` |
| claims, outcomes, scores | `content_claim` joined to `claim_outcome`; `creator_score` | 3,381; 3,355; 2,369; 0.37 MB | `claim`, `creator_score` |
| creators, people, their picks and history | `content_source`, `panel_person` as CSV pruned to the eight; `fact_manager_pick`, `fact_manager_gw` for verified creator entries | 42, 54; small | `creator`, `source` seed; `creator_pick`, `creator_gw` |
| FPLReview sample; rules; API bodies | `data/projections/fplreview/fplreview_1789744594.csv`; `fpl_edge/rules/registry.yaml`; 3 bootstrap, 1 live, 1 picks from `data/raw/fpl_api` | 74 KB; 207 lines; 5 MB | `tests/fixtures/providers/fplreview_csv/`; `desk/rules/registry.yaml` pruned of odds and ownership; `tests/fixtures/fpl/` |

Under 12 MB in total. Upload to `seed/2026-09-21/` and run `scripts/backfill.py` once from the worker shell in S1; it converts slash-form seasons, drops `element_type` 5 rows, keys on `code`, and writes one `job` row named `backfill` with counts. Left behind: 16 GB of audio, 240 MB of rivals bodies, 172 MB of API bodies, odds, understat, intel, ideas, the auth SQLite, the chat JSON store.

Code worth copying by hand, then adapted to Postgres: `fpl_edge/platform/prose_style.py` (verbatim); `platform/auth/keys.py`, `oauth.py`, `sessions.py`, `policy.py` (drop the `sk-ant-oat` branch, SQLite, per-user directories and `anon_is_owner`); `ingest/fpl_api.py` and `ingest/results.py` lines 78 to 118; `myteam/state.py` lines 425 to 475 and 517 to 527; `ingest/player_mapping.py` lines 100 to 135 (NFKD plus the explicit map for Ø Đ Ł Æ Œ ß Þ Ð and dotless ı); `models/points/scoring_map.py` with its history test and `rules/loader.py`; `models/team_goals/dixon_coles.py` and `platform/scripts/fixtures/ratings.py` lines 236 to 290; `ingest/projections/local_csv.py` lines 90 to 170, `fplform.py`, `github_csv.py` from line 232 and `eval/projection_scoring.py`; `ingest/content/analyze.py` lines 207 to 424 (schema, prompt, `_INSIGHT_RULES`, `_NOTES_PREAMBLE`, `validate_model_id`, `claims_from_analysis`), `scoring.py` and `consensus.py`; worktree `agent-a8716a774fd2d3aca/.../asr_hosted.py`; `opt/scoring.py` (`score_plan`, `replay_finances`); `pipelines/health.py`; the six unit tests named in section 10, `tests/audit/test_time_and_deadlines.py` and `scripts/feature_check.py`.

Rewrite from this spec: `fpl_edge/opt/milp.py` (the port follows open-fpl-solver, keeping the old exact integer sell-price arithmetic as a test), `chat_agent.py`, `fpl_edge/mcp/`, the rest of `fpl_edge/platform/` (32,863 lines), all of `web/dist` (19,129 lines), `pipelines/registry.py`, `scheduler.py`, `store/warehouse.py`, `views.sql`, `store/backup.py`, `content/{fetch,youtube,sources,pipeline}.py`.


# Part 16. External facts, verified 2026-09-21

Every row below was read on 2026-09-21 (UTC evening) unless the date column says otherwise. "Measured" means I ran a request and recorded the response. "Unverified" means I could not find a primary source today. Old-repo paths are under `/Users/nripeshpradhan/Documents/Github/i-test-season/`.

Confidence scale: High = primary source (vendor docs, live API, package registry). Medium = secondary source (GitHub issue, community write-up, search snippet). Low = inference.

### 1. Anthropic credentials for a third-party web app

| Fact | Source | Read | Confidence |
|---|---|---|---|
| No "Sign in with Claude" for third parties exists. Legal page: "Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users." | https://code.claude.com/docs/en/legal-and-compliance | 2026-09-21 | High |
| Same page: "developers may not collect, store, or intermediate Claude.ai credentials or session tokens." Developers using the Agent SDK "should use API key authentication through Claude Console or a supported cloud provider." | https://code.claude.com/docs/en/legal-and-compliance | 2026-09-21 | High |
| Agent SDK overview and quickstart both carry: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK." | https://code.claude.com/docs/en/agent-sdk/overview and https://code.claude.com/docs/en/agent-sdk/quickstart | 2026-09-21 | High |
| `claude setup-token` mints a 1-year OAuth token for `CLAUDE_CODE_OAUTH_TOKEN`. Requires Pro, Max, Team or Enterprise. "It can only make model requests." Bare mode ignores it. | https://code.claude.com/docs/en/authentication | 2026-09-21 | High |
| CLI credential precedence (the SDK wraps the CLI): 1 cloud provider env (`CLAUDE_CODE_USE_BEDROCK`/`_VERTEX`/`_FOUNDRY`), 2 `ANTHROPIC_AUTH_TOKEN`, 3 `ANTHROPIC_API_KEY`, 4 `apiKeyHelper`, 5 `CLAUDE_CODE_OAUTH_TOKEN`, 6 Anthropic profiles/WIF, 7 `/login` subscription OAuth. | https://code.claude.com/docs/en/authentication | 2026-09-21 | High |
| Agent SDK quickstart lists the accepted auth: `ANTHROPIC_API_KEY`; or `CLAUDE_CODE_USE_BEDROCK=1`; `CLAUDE_CODE_USE_ANTHROPIC_AWS=1` + `ANTHROPIC_AWS_WORKSPACE_ID`; `CLAUDE_CODE_USE_VERTEX=1`; `CLAUDE_CODE_USE_FOUNDRY=1`. The SDK does not load `.env`. | https://code.claude.com/docs/en/agent-sdk/quickstart | 2026-09-21 | High |
| Server-side enforcement exists. Consumer OAuth tokens outside first-party apps return "This credential is only authorized for use with Claude Code" (issue dated 2026-09-07). | https://github.com/JetBrains/thinkrail/issues/437 | 2026-09-21 | Medium |
| Second enforcement string, 400 `invalid_request_error`: "Third-party apps now draw from your extra usage, not your plan limits." Reported 2026-04-08. | https://github.com/anthropics/claude-code/issues/45016 | 2026-09-21 | Medium |
| Agent SDK use is governed by the Commercial Terms of Service. Branding: "Claude Agent" allowed, "Claude Code" not allowed in your product. | https://code.claude.com/docs/en/agent-sdk/overview | 2026-09-21 | High |
| `claude-agent-sdk` 0.2.157, released 2026-09-18, Python 3.10+. Platform wheels bundle the Claude Code binary; the sdist (for example ARM64 Windows) does not. `cli_path` overrides. | https://pypi.org/project/claude-agent-sdk/ and quickstart | 2026-09-21 | High |
| `anthropic` Python SDK 1.7.0 released 2026-09-18. 1.0.0 (2026-08-20) was a breaking release: "upgrade to httpx2 and some minor breaking changes. See MIGRATION.md". | https://pypi.org/pypi/anthropic/json and https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/CHANGELOG.md line 239 | 2026-09-21 | High |

What the old repo assumed. `docs/platform/AUTH.md` section 1 first said no subscription path exists, then a "Correction, 2026-09-20" paragraph said users can paste a `claude setup-token` value. `fpl_edge/platform/auth/keys.py:60` accepts `sk-ant-oat..` tokens and `keys.py:75-76` maps them to `CLAUDE_CODE_OAUTH_TOKEN`. The legal page read today forbids exactly that: a third party storing and intermediating subscription credentials. The correction was wrong. `pyproject.toml:27` pins `anthropic>=0.40`, which spans the 1.0 breaking change.

### 2. Claude model ids, prices, caching

| Model | API id | Context | Max out | Input $/MTok | Output $/MTok | Cache read | Cache write 5m | Retirement |
|---|---|---|---|---|---|---|---|---|
| Claude Opus 5 | `claude-opus-5` | 1M | 128K | 5 | 25 | 0.50 | 6.25 | not before 2027-07-24 |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | 128K | 2 | 10 | 0.20 | 2.50 | not before 2027-06-30 |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` (alias `claude-haiku-4-5`) | 200K | 64K | 1 | 5 | 0.10 | 1.25 | not before 2026-10-15 |

Sources: https://platform.claude.com/docs/en/about-claude/models/overview.md and https://platform.claude.com/docs/en/about-claude/pricing.md, read 2026-09-21. Confidence High.

| Fact | Source | Confidence |
|---|---|---|
| Sonnet 5's $2/$10 "introductory" price is now permanent. The planned 2026-09-01 rise to $3/$15 "will not occur." | pricing.md | High |
| Cache multipliers: 5-minute write 1.25x, 1-hour write 2x, read 0.1x. Max 4 explicit breakpoints. Top-level `cache_control` auto-caching exists. | pricing.md, prompt-caching.md | High |
| Minimum cacheable prefix: 512 tokens on Opus 5, 1,024 on Sonnet 5, 4,096 on Haiku 4.5. Below that, caching silently does nothing. | https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md | High |
| Cache invalidation order: tools, then system, then messages. Changing tool definitions invalidates everything. | prompt-caching.md | High |
| Batch API: 50% off. Opus 5 $2.50/$12.50, Sonnet 5 $1/$5, Haiku 4.5 $0.50/$2.50. | pricing.md | High |
| Tool-use system prompt overhead: Opus 5 286 tokens, Sonnet 5 354, Haiku 4.5 496 (`auto`). | pricing.md | High |
| Haiku 4.5 uses extended thinking with `budget_tokens`; `effort` is "Not supported." Opus 5 and Sonnet 5 use adaptive thinking, default effort `high`. | models overview | High |
| Haiku 4.5 retirement window opens 2026-10-15, 24 days from today. | models overview | High |

Old repo: `fpl_edge/config.py:187-199` pins `claude-sonnet-5` for analysis and briefing and `claude-opus-5` for chat. Both ids are valid today.

### 3. FPLReview planner and solver

| Fact | Source | Confidence |
|---|---|---|
| Two solvers. Transfer Solver: "chess engine-style heuristic search", no optimality guarantee, probability-based autosubs from xMins, handles same-team GK. Linear Optimiser: MILP on HiGHS, "guarantees optimal solution within the linear framework", fixed sub weights. | https://docs.fplreview.com/the-model/solvers/solver-comparison/ | High |
| Common settings and defaults: Transfer Depth 6 (recommended: a couple short of loaded horizon), FT Value 1.75, Time Decay 0.85 (0.80 to 0.95), Bank Value 0.10 per £1M (0.00 to 0.20). | https://docs.fplreview.com/the-model/solvers/settings/ | High |
| Transfer Solver extras: Solve Speed 5, Same Team GK/DF Limit 3, Max GK Cost £7.0, Sub Weight 1.00, Future Info Value 0.00, Risk Position 0.00 (-0.15 to +0.15), Burn FT Value = FT Value. | same | High |
| Linear Optimiser extras: Solve Lines (top N distinct plans) 3, Time Limit 300 s, Vice-Captain Weight 0.05, Sub 1 0.30, Sub 2 0.10, Sub 3 0.03, Sub GK 0.03. | same | High |
| Sensitivity analysis: default 20 runs (20 to 100 recommended), noise level 0 to 5, default 3. Varies team and player ratings, xMins (coherent: nailed players stable), and FT Value, Time Decay, Bank Value. Scores moves 3/2/1 per run; WC/FH solves report weighted proportion per player. | https://docs.fplreview.com/the-model/solvers/sensitivity-analysis/ | High |
| xMins: "average minutes a player might play across 1,000 simulations". Editable 0 to 95. EV is not linear in xMins. | https://docs.fplreview.com/the-model/projections/xmins/ | High |
| Chips: the GW for each chip is chosen in Team Settings. Time decay example: 0.85 gives GW2 85%, GW3 72%, GW4 61%. | search snippets of docs.fplreview.com | Medium |
| CSV export: FPL ID, name, team, position, buying and selling price, EV, xMins, one column set per loaded GW. Exports the current table view. | https://docs.fplreview.com/the-model/planner-interface/export_projections/ | High |
| CSV upload contract: minimum `ID` and `GW_Pts`; recommended `Pos, ID, Name, BV, SV, Team, GW_xMins, GW_Pts, Elite%`. Overwrites by `ID`. | https://docs.fplreview.com/the-model/planner-interface/upload_projections/ | High |
| Concrete column names in the wild: `{gw}_Pts` and `{gw}_xMins`, with `ID, Name, Pos, Team, Value` (and `BV`/`SV`). | open-fpl-solver `dev/data_parser.py` lines 72-96 and 309 | High |
| Membership: Premium $5/month, Dev Supporter $8.50/month (currency as shown on Patreon today). A search snippet lists Premium as: Massive Data Model, downloadable projection data, advanced solver, sensitivity analysis, 14 GW horizon, 10 savable drafts, deadline access. | https://www.patreon.com/fplreview/membership (join page returned 403) | Medium |
| Free planner exists at app.fplreview.com/free. Its horizon and export rights are JS-rendered and unverified. | https://app.fplreview.com/free | Unverified |

### 4. open-fpl-solver (the public FPLReview replica)

| Fact | Source | Confidence |
|---|---|---|
| `sertalpbilal/FPL-Optimization-Tools` now redirects to `solioanalytics/open-fpl-solver`. 191 stars, 62 forks, Apache-2.0, created 2021-03-05, last push 2026-09-15. | `gh api repos/...` run 2026-09-21 | High |
| Solver: HiGHS only, via `highspy>=1.11.0`. No PuLP, no CBC. `requires-python = ">=3.14"`. Deps: pandas, numpy, requests, fuzzywuzzy, python-Levenshtein, matplotlib, tabulate. | raw `pyproject.toml` | High |
| Layout: `run/` (solve.py, sensitivity.py, simulations.py, run_parallel.py, binary_file_generator.py), `dev/` (solver.py, data_parser.py, visualization.py), `data/` (README.md, user_settings.json, comprehensive_settings.json, team.json.sample, binary_fixtures.md). | `gh api .../contents` | High |
| Input: `data/<datasource>.csv` (readers `read_solio`, `read_fplreview`, `read_mikkel`), plus team via `team_id` or `team.json` from `fantasy.premierleague.com/api/my-team/<id>/`. `datasource: "mixed"` averages sources by `data_weights`. | `dev/data_parser.py:33-58`, `data/README.md` | High |

Defaults from `data/comprehensive_settings.json` (read 2026-09-21):

| Setting | Default | Setting | Default |
|---|---|---|---|
| `horizon` | 8 | `decay_base` | 0.9 |
| `ft_value` | 1.5 | `ft_value_list` | {2: 2, 3: 1.6, 4: 1.3, 5: 1.1} |
| `bench_weights` | {0: 0.03, 1: 0.21, 2: 0.06, 3: 0.002} | `vcap_weight` | 0.1 |
| `ft_use_penalty` | 0.2 | `itb_value` | 0.08 |
| `itb_loss_per_transfer` | 0 | `hit_cost` | 4 |
| `hit_limit` | null | `weekly_hit_limit` | 0 |
| `num_transfers` | null | `no_transfer_last_gws` | 2 |
| `no_future_transfer` | false | `future_transfer_limit` | null |
| `xmin_lb` | 300 | `ev_per_price_cutoff` | 30 |
| `keep_top_ev_percent` | 5 | `banned` / `locked` | [] |
| `use_wc/use_bb/use_fh/use_tc` | [] | `chip_limits` | {bb: 0, wc: 0, fh: 0, tc: 0} |
| `allowed_chip_gws` / `forced_chip_gws` / `no_chip_gws` | empty | `num_iterations` | 1 |
| `iteration_criteria` | this_gw_transfer_in_out | `iteration_difference` | 1 |
| `secs` | 600 | `gap` | 0 |
| `solver` | highs | `report_decay_base` | [0.85, 1.0, 1.017] |
| `randomized` | false | `randomization_strength` | 1.0 |
| `max_defenders_per_team` | 3 | `double_defense_pick` | false |
| `no_opposing_play` | false (or "penalty", 0.5) | `opposing_play_group` | position |
| `transfer_itb_buffer` | null | `booked_transfers` | [] |
| `preseason` | false | `team_data` | json |

Other keys in the full list: `banned_next_gw`, `locked_next_gw`, `keep`, `price_changes`, `pick_prices`, `no_transfer_by_position`, `no_transfer_gws`, `no_trs_except_wc`, `only_booked_transfers`, `force_ft_state_lb/ub`, `no_gk_rotation_after`, `iteration_target`, `override_next_gw`, `team_json`, `data_weights`, `export_data`, `binary_file_weights`, `binary_fixture_settings`, `generate_binary_files`, `solutions_file`, `save_squads`, `solutions_file_player_type`, `print_*`, `dataframe_format`, `hide_transfers`, `export_image`, `solve_name`, `delete_tmp`, `single_solve`, `verbose`. `iteration_criteria` options: `this_gw_transfer_in`, `this_gw_transfer_out`, `this_gw_transfer_in_out`, `chip_gws`, `target_gws_transfer_in`, `this_gw_lineup`. Source: `data/README.md`.

Old repo contrast: `fpl_edge/opt/config.py:196` sets `decay_base` default 1.0 (no discounting) and `FT_VALUE_DEFAULT = 1.5` (line 65); `ft_value_list` defaults to None. `pyproject.toml:16-17` pulls both `pulp>=2.9` and `highspy>=1.7`. The public replica needs one.

### 5. Solio Analytics

| Fact | Source | Confidence |
|---|---|---|
| Public, unauthenticated data endpoint refreshed every 4 hours: `/api/data/latest.json`, `.md`, and HTML. "For citation, please attribute as Solio Analytics." | https://fpl.solioanalytics.com/ (page text) | High |
| Measured JSON (200, 27,047 bytes): keys `generatedAt`, `gameweek`, `deadlineIso`, `source`, `topProjected` (30 rows), `topCaptains` (10), `topDifferentials` (15), `topGoals` (15), `topAssists` (15), `bestCleanSheets` (10), `topBonus` (15), `topDefCon` (15), `bestAttackingFixtures`, `topTransfersIn`, `topTransfersOut`. | curl https://fpl.solioanalytics.com/api/data/latest.json at 2026-09-21T22:29Z | High |
| Row fields: `name, team, position, price` (int tenths, 120 = £12.0m), `opponents [{opponent, isHome}]`, `ownership`, `prPoints` (float), plus `captainProjPoints`, `leverage`, `prGoals`, `prAssists`, `prBonusPoints`, `prDefConProb`, `prDefConPoints`, `csProb`, `prGoalsFor`, `prGoalsAgainst`. Top rows today: B.Fernandes 6.40, Saka 6.30, Palmer 5.95. | same | High |
| The endpoint is top-N lists only. No full 667-player table, no xMins, no multi-GW columns. | same | High |
| Planner free "for the next 5 gameweeks"; full-season optimisation for members. Membership price not published on the pages fetched. | https://fpl.solioanalytics.com/ | High (scope), Unverified (price) |
| Member CSV export exists in practice: the solver reads `data/solio.csv` with `{gw}_Pts/{gw}_xMins`. Whether export is member-only: unverified. | `dev/data_parser.py:43-46` | Medium |
| Launched 2025-08-05, market-odds model with stochastic minutes, goals, assists, CS and bonus; 12-week horizon at launch. | https://jakethom95.substack.com/p/exploring-solio-analytics-fpls-newest (2025-08-08) | Medium |

### 6. FPL public API

Measured 2026-09-21 22:25 UTC from a residential Mac with `User-Agent: Mozilla/5.0`:

| Endpoint | HTTP | Notes |
|---|---|---|
| `/api/bootstrap-static/` | 200, 1,778,755 bytes | 667 elements, 20 teams, 38 events; current GW 5, next GW 6; keys `chips, events, game_settings, game_config, phases, teams, total_players, element_stats, element_types, elements` |
| `/api/fixtures/` | 200 | 380 rows; `team_h_difficulty`, `team_a_difficulty`, `stats`, `pulse_id` |
| `/api/element-summary/{id}/` | 200 | `fixtures, history, history_past`; history rows carry `defensive_contribution, clearances_blocks_interceptions, recoveries, tackles, expected_*` |
| `/api/event/{gw}/live/` | 200 | 610 elements with per-GW `stats` including `defensive_contribution` |
| `/api/entry/{id}/` | 200 | `summary_overall_rank`, `last_deadline_bank`, `leagues` |
| `/api/entry/{id}/history/` | 200 | `current, past, chips`. Entry 4490171 has used `bboost` GW1 and `3xc` GW5 |
| `/api/entry/{id}/event/{gw}/picks/` | 200 | `active_chip, automatic_subs, entry_history, picks` |
| `/api/entry/{id}/transfers/` | 200 | list, 4 rows |
| `/api/leagues-classic/{id}/standings/` | 200 | `league, standings, last_updated_data, new_entries` |
| `/api/event-status/` | 200 | `bonus_added` per day, `leagues: Updated` |
| `/api/bootstrap-static/` with no User-Agent | 200 | Worked today. The community rule that a browser UA is required did not bite from this IP |

Rules read from `game_config` in the live bootstrap (High):

| Rule | Value |
|---|---|
| `max_extra_free_transfers` | 4 (so 5 banked FTs) |
| `transfers_cap` | 20 per GW |
| `squad_total_spend` / `squad_team_limit` / `squad_squadsize` | 1000 / 3 / 15 |
| `transfers_sell_on_fee` | 0.5 |
| `scoring.defensive_contribution` | DEF 2, MID 2, FWD 2, GKP 0 |
| `scoring.goals_scored` | GKP 10, DEF 6, MID 5, FWD 4 |
| `scoring.clean_sheets` | GKP 4, DEF 4, MID 1, FWD 0 |
| `scoring.mng_*` (assistant manager) | all 0 |
| `chips` | 8 entries: wildcard 2-19 and 20-38; freehit 2-19 and 20-38; bboost 1-19 and 20-38; 3xc 1-19 and 20-38. No `manager` chip |
| `price_change_deadlines` | 23:00Z daily; `static_content_url` ends `/2026_27/` |
| New element fields | `defensive_contribution`, `defensive_contribution_per_90`, `price_change_projections`, `price_change_percent`, `price_change_hourly_rate`, `scout_risks`, `scout_news_link`, `opta_code`, `has_temporary_code` |

2026/27 rule changes (High, two sources):

| Change | Source |
|---|---|
| Banking stays at 5 FTs; DefCon stays; two chip sets (WC, FH, TC, BB), first set expires at 13:30 GMT Saturday 2 January | https://www.premierleague.com/en/news/4679873/... (2026-07-31) |
| BPS: no -1 for being tackled; CBI 1 BPS per 3 actions (was per 2); GK saves 2-3 BPS, big-chance save 1, penalty save 8; projected bonus shown after 20 minutes; lockdown 09:00 UK the day after the last match; no AFCON extra FTs | https://www.fantasyfootballscout.co.uk/2026/07/20/... |
| DefCon thresholds (unchanged from 2025/26): DEF 10 CBIT, MID/FWD 12 CBIRT, 2 points per match. "There will be no Assistant Manager chip this season" (2025/26), and the 2026/27 chip list above has none | https://www.premierleague.com/en/news/4362211/... |
| Live mini-league ranks, price-change predictor (00:00 UK), FDR filters in squad view, rookie league | PL article 2026-07-31 |

Rate limits and terms: no official rate-limit document exists (three searches, no hit). Premier League Terms of Use, Intellectual Property section: the site "must not be used in any other way, including for commercial purposes, and you may not otherwise reproduce, re-utilise or redistribute it (including ... creating a database ... that includes material downloaded ...)" (https://www.premierleague.com/terms-and-conditions, High). Whether Railway egress IPs are blocked: unverified. Old repo sent a UA and slept between calls (`fpl_edge/ingest/fpl_core_insights.py:125-127`).

### 7. Railway

| Fact | Source | Confidence |
|---|---|---|
| Plans: Free $0 (1 vCPU, 0.5 GB RAM, 0.5 GB volume, 1 replica); Trial $5 one-time; Hobby $5/month with $5 usage included (48 vCPU, 48 GB RAM, 5 GB volume, 6 replicas); Pro $20/month with $20 included (1,000 vCPU, 1 TB RAM, 42 replicas, volume "1 TB*" self-serve). Image retention Hobby 72 h, Pro 120 h. | https://docs.railway.com/pricing/plans | High |
| Unit prices: $20/vCPU/month, $10/GB RAM/month, $0.05/GB egress, $0.15/GB/month volume. | same | High |
| Volumes: one per service, no replicas with a volume, no downsizing. Volumes page lists Pro at 50 GB, which conflicts with the plans page. | https://docs.railway.com/reference/volumes | High (limits), conflict noted |
| Backups: daily (kept 6 days), weekly (27 days), monthly (89 days), stackable; incremental copy-on-write; billed at volume rate. Covers Postgres. Plan gating not stated. | https://docs.railway.com/reference/backups | High |
| Postgres: SSL-enabled image from the official Postgres image; private by default with `DATABASE_URL`; public TCP proxy adds `DATABASE_PUBLIC_URL` and bills egress; optional HA cluster (Patroni, etcd, HAProxy). | https://docs.railway.com/guides/postgresql | High |
| Cron: 5-field crontab, UTC, minimum interval 5 minutes, next run skipped if the previous is still running, process must exit and close connections. | https://docs.railway.com/reference/cron-jobs | High |
| Buckets: GA (changelog 2025-11-28), S3-compatible (put/get/list/copy/presign/multipart; no versioning, lifecycle or SSE yet), $0.015/GB-month, egress and API ops free, env vars `BUCKET, ACCESS_KEY_ID, SECRET_ACCESS_KEY, REGION, ENDPOINT`. Free plan: 10 GB after trial (search snippet). | https://docs.railway.com/storage-buckets, https://railway.com/changelog/2025-11-28-buckets-ga | High (Medium for 10 GB) |
| Many services from one repo: set root directory, a custom start command per service, and watch paths. Services do not share a build; each builds its own image. Alternative: build once in CI, push to a registry, deploy from image (private registries need Pro). | https://docs.railway.com/guides/monorepo, https://docs.railway.com/services, https://station.railway.com/questions/how-to-split-the-build-process-from-1-re-95d0d205 | High |

### 8. Groq speech-to-text and alternatives

| Provider / model | Price | Limits | Source | Confidence |
|---|---|---|---|---|
| Groq `whisper-large-v3-turbo` | $0.04 per audio hour, 10 s minimum bill, 216x realtime | 25 MB free tier, 100 MB dev tier; URL input allowed; formats flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm | https://console.groq.com/docs/speech-to-text, https://console.groq.com/docs/model/whisper-large-v3-turbo | High |
| Groq `whisper-large-v3` | $0.111 per hour | same file limits; also translations | same | High |
| Groq free tier, both models | 20 RPM, 2,000 RPD, 7,200 audio-seconds/hour (2 h), 28,800/day (8 h) | | https://console.groq.com/docs/rate-limits | High |
| Groq developer tier | Third-party sites quote 300 RPM and 200k audio-sec/hour (v3), 400 RPM and 400k (turbo). Not in the official page text. | | search snippets | Unverified |
| Deepgram Nova-3 pre-recorded | $0.0043/min = $0.258/h (multilingual $0.0052/min); Whisper Large $0.0048/min; $200 free credit | | https://deepgram.com/pricing | High |
| OpenAI | `gpt-4o-mini-transcribe` $0.003/min = $0.18/h; `gpt-transcribe` $0.0045/min = $0.27/h; `gpt-4o-transcribe` and Whisper $0.006/min = $0.36/h | | https://developers.openai.com/api/docs/pricing | High |

Cost model: 10 creators, 3 videos per week, 20 minutes each = 10 audio hours per week. Groq turbo $0.40/week, about $15 for 38 GWs. OpenAI mini $1.80/week. Deepgram $2.58/week. The old repo ran mlx-whisper locally (`fpl_edge/ingest/content/asr.py:81`, model `whisper-large-v3-turbo`) to spend nothing; the same model on Groq costs cents.

### 9. youtube-transcript-api and yt-dlp

| Fact | Source | Confidence |
|---|---|---|
| `youtube-transcript-api` 1.2.4, released 2026-01-29. README: "YouTube has started blocking most IPs that are known to belong to cloud providers"; raises `RequestBlocked` or `IpBlocked`; cookie auth "currently not available"; recommended fix is Webshare rotating residential proxies via `WebshareProxyConfig`, or `GenericProxyConfig`. | https://pypi.org/pypi/youtube-transcript-api/json, https://github.com/jdepoix/youtube-transcript-api | High |
| Old repo pins `youtube-transcript-api>=0.6` (`pyproject.toml:26`) and calls the 1.x `api.fetch()` API (`fpl_edge/ingest/content/youtube.py:587-588`), with "no proxy, no cookies" (`youtube.py:8`). A pre-1.0 install would break the call. | old repo | High |
| `yt-dlp` 2026.8.19 (PyPI). README: "A JavaScript runtime/engine like deno (recommended), node.js, bun, or QuickJS is also required to run yt-dlp-ejs" for YouTube. | https://pypi.org/pypi/yt-dlp/json, https://github.com/yt-dlp/yt-dlp | High |
| PO tokens: "yt-dlp cannot generate them and they must be provided externally." GVS tokens required for `web, mweb, web_safari, android, ios`; not for `tv, android_vr, web_embedded`; not required for YouTube Premium accounts. Provider plugin: `bgutil-ytdlp-pot-provider`. | https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide, wiki/Extractors | High |
| Cookie export: private window, visit youtube.com/robots.txt, export, close window (stops rotation). | wiki/Extractors | High |
| Datacenter IPs trigger "Sign in to confirm you're not a bot"; cookies get invalidated faster from datacenter IPs; no flag fixes it. Official yt-dlp docs do not say this; four community guides agree. | ytdlp.org, dev.to, dalvo.io guides | Medium |

### 10. UI stack

| Package | Latest | Released | Source | Note |
|---|---|---|---|---|
| vite | 8.3.0 | 2026-09-10 | registry.npmjs.org | |
| react | 19.3.0 | 2026-09-09 | same | |
| @tanstack/react-table | 9.2.4 | 2026-08-28 | same | 9.0.0 shipped 2026-08-04 |
| recharts | 3.10.1 | 2026-07-25 | same | |
| tailwindcss | 4.3.3 | 2026-07-16 | same | |
| shadcn (CLI) | 4.21.0 | 2026-09-04 | same | |
| echarts | 6.1.0 | 2026-05-19 | same | |
| @visx/heatmap | 4.0.0 | | jsdelivr package.json | peer React 18 or 19 |

| Fact | Source | Confidence |
|---|---|---|
| shadcn/ui ships a data-table guide, not a component. It wraps TanStack Table v9 and demonstrates sorting, column filtering, column visibility, pagination, row selection and row actions. Install: `shadcn add table` plus `@tanstack/react-table`. | https://ui.shadcn.com/docs/components/data-table | High |
| ECharts 6.1.0 has `HeatmapSeriesOption` with `coordinateSystem: 'cartesian2d' | 'geo' | 'calendar' | 'matrix'` and uses `visualMap` for colour. | https://cdn.jsdelivr.net/npm/echarts@6.1.0/types/dist/shared.d.ts lines 12118-12120 | High |
| Old repo: `fpl_edge/interfaces` exists; front-end stack in the old repo was not checked here. | | |

### For the rebuild

1. Chat auth is BYOK Anthropic API key, nothing else. Accept only Console keys, reject `sk-ant-oat` at the input boundary, and delete the `oauth_token` branch from the port of `keys.py`. Storing a user's subscription token is what the legal page forbids. The owner's own admin pipelines may use the owner's key or a Bedrock/Vertex/Foundry env; users never touch those.
2. Call the Messages API directly for chat (anthropic 1.7.0, pin `>=1.7,<2`). Reach for `claude-agent-sdk` 0.2.157 only if the chat needs the file and bash harness; if so, pass the user's key through `ClaudeAgentOptions.env["ANTHROPIC_API_KEY"]` and nothing else.
3. Models: chat on `claude-opus-5` with a frozen system prompt over 512 tokens and one cache breakpoint; batch and pipeline work on `claude-sonnet-5` through the Batch API at $1/$5. Do not build on Haiku 4.5; its retirement window opens 2026-10-15.
4. Solver: port open-fpl-solver semantics, not the old `fpl_edge/opt`. HiGHS via `highspy>=1.11`, drop PuLP. Use its settings JSON as the API contract and its names in the UI, mapped to FPLReview labels: horizon 8, decay 0.85 to 0.9, ft_value 1.5 to 1.75, `ft_value_list`, bench weights {0.03, 0.21, 0.06, 0.002}, itb 0.08 to 0.10, hit 4, `secs` 300, `num_iterations` for alternative plans, `randomized` runs (20, scored 3/2/1) for sensitivity. Note `requires-python >=3.14` on the upstream; vendor the model file rather than depending on the package.
5. Projections interchange format is `ID, Name, Pos, Team, Value, {gw}_Pts, {gw}_xMins` in a long table keyed by provider and pulled-at time. Provider 1: Solio public JSON (free, top-N only, every 4 hours, attribute "Solio Analytics"). Provider 2: FPLReview or Solio member CSV uploaded by the user through the upload contract. Provider 3: FPL `ep_next` as the baseline. Do not scrape member pages.
6. FPL API client: one module, browser UA, exponential backoff, on-disk cache, and rules read from `game_config` and `chips` at ingest rather than hard-coded. Ingest `defensive_contribution`, `price_change_*`, `scout_risks` from day one. Test from a Railway shell on day one whether the API answers from Railway's egress IPs; that is the one unverified risk. Keep the app non-commercial; the PL terms bar commercial reuse and database creation.
7. Railway layout: three services from one repo (web, worker, cron), each with its own build and start command; Postgres plugin with daily and weekly backups enabled; one bucket for raw JSON, CSV uploads and audio at $0.015/GB-month; no volumes unless a service needs local disk. Cron floors at 5 minutes and runs UTC, so schedule against deadline times from `bootstrap-static`. Hobby ($5) covers a small web plus Postgres; move to Pro only for private registry images or larger volumes.
8. Transcription: Groq `whisper-large-v3-turbo` at $0.04/hour, about $15 per season for 10 creators. Audio and captions are fetched on the owner's Mac (residential IP, Deno installed, `yt-dlp` current, `youtube-transcript-api>=1.2`) and pushed to the bucket; the server never calls YouTube. Add `WebshareProxyConfig` only if a server-side fetch becomes necessary.
9. UI: Vite 8, React 19, Tailwind 4, shadcn CLI 4.21, TanStack Table 9 for every filterable table, ECharts 6 for the fixture-difficulty and xMins heatmaps, Recharts 3 for simple line and bar charts.

Open questions: FPLReview free-tier horizon and export rights; Solio membership price and whether CSV export is member-only; Groq developer-tier audio limits; Railway backup plan gating and the 50 GB versus 1 TB Pro volume cap; whether Railway egress IPs reach the FPL API.


# Part 17. Appendix

## A. The creators shortlist and the numbers behind it

### 2. The corpus per creator, and who to keep

Latest `creator_score` row per creator at scope `all`, `as_of` 2026-09-19. Transcripts are podcast or YouTube items with `text_source = 'transcript'`. Sorted by scored claims.

| creator | items | transcripts | 30 d items | claims (llm / cue) | scored | hits | rate | Wilson lo |
|---|---:|---:|---:|---|---:|---:|---:|---:|
| Let's Talk FPL | 77 | 39 | 39 | 597 (554 / 43) | 552 | 294 | 0.533 | 0.491 |
| FPL Harry | 71 | 36 | 39 | 482 (463 / 19) | 444 | 240 | 0.541 | 0.494 |
| FPL Raptor | 63 | 35 | 34 | 442 (398 / 44) | 409 | 221 | 0.540 | 0.492 |
| Fantasy Football Hub | 62 | 38 | 28 | 444 (422 / 22) | 388 | 162 | 0.418 | 0.370 |
| AllAboutFPL (blog) | 49 | 0, articles | 34 | 379 (256 / 123) | 357 | 157 | 0.440 | 0.389 |
| Fantasy Football Scout | 323 | 0, 237 articles | 221 | 308 (149 / 159) | 252 | 136 | 0.540 | 0.478 |
| user-shared (pasted) | 8 | 7 | 4 | 212 (167 / 45) | 195 | 92 | 0.472 | 0.403 |
| The FPL Wire | 45 | 24 | 21 | 166 (163 / 3) | 144 | 63 | 0.438 | 0.359 |
| FPL Fran | 13 | 9 | 13 | 124 (124 / 0) | 124 | 59 | 0.476 | 0.390 |
| FPL BlackBox | 23 | 10 | 9 | 64 (64 / 0) | 64 | 36 | 0.563 | 0.441 |
| Solio Analytics | 6 | 3 | 6 | 57 (55 / 2) | 56 | 30 | 0.536 | 0.407 |
| Gianni Buttice | 47 | 0 | 20 | 22 (0 / 22) | 20 | 5 | 0.250 | 0.112 |
| Planet FPL | 65 | 0 | 28 | 10 (0 / 10) | 8 | 2 | 0.250 | 0.072 |
| FPL Focal | 62 | 0 | 26 | 1 | 0 | | | |
| FPL Tom, FPL Mate, Ignore the Template, Above Average, FML FPL, All In, 59th Minute, Family, Pod, WGTA, Sky, General | 8 to 23 each | 0 | | 0 to 9, all cue | | | | |

No creator clears the floor; the best Wilson lower bound is 0.494 at n 444. Ranking on record alone is a ranking of noise around 0.5, so the order below is record within the set that has a body. Multi-host shows sit lowest (Hub 0.370, Wire 0.359), which is what blending four people's calls under one label predicts (`ingest/content/panel.py:1-30`).

Recommendation, 8 creators: FPL Harry, FPL Raptor, Let's Talk FPL, FPL BlackBox, Solio Analytics, FPL Fran, Fantasy Football Hub, The FPL Wire. Reasons: these are the only 8 with transcripts (193 episodes), each has n >= 56 scored, they are the `PANEL_CREATORS` the owner named on 2026-08-27 and 2026-09-03 (`youtube.py:146-174`), and their people have verified entry ids with league-admin evidence (`creator_identity_research.md:20-37`). Optional 9th: the Fantasy Football Scout blog feed, 237 articles with bodies at zero transcription cost, n 252, rate 0.540. Drop the other 18 sources: zero transcripts, at most 22 cue claims each, and every one costs a nightly fetch. Solio needs a feed before it produces anything (6 items, 3 pasted).

## B. The claim extraction prompt and schema, as measured in the old repository

### 3. The extraction prompt and schema

System prompt, `analyze.py:302-315`, verbatim essentials:

> You are an FPL (Fantasy Premier League) analyst. You are given the transcript of an FPL podcast or video. Extract ONLY positions the speakers actually take -- never infer a recommendation from a neutral mention, and never add players who are merely listed or joked about.
> Rules: conviction reflects the SPEAKER'S language: "nailed on", "definitely" = high; "I quite like", "leaning towards" = medium; "maybe", "could do worse" = low. quote must be verbatim from the transcript (light truncation allowed). If two speakers disagree about a player, include both calls. gameweek only when explicitly stated or unambiguous from context. summary bullets are the episode's actual key ideas, not a table of contents.

Appended `_INSIGHT_RULES` (`analyze.py:325-388`): the two-question test, "does the sentence tell the listener what to DO" (call) versus "does it describe how the world IS or WILL BE" (insight); seven worked examples; "IF YOU CANNOT QUOTE IT, DO NOT RECORD IT"; always emit the `insights` key. `_NOTES_PREAMBLE` (`analyze.py:404-424`) is prepended for show notes: "a title such as 'MY GW2 TEAM' names a TOPIC, not a stance", "Empty lists are the CORRECT and expected answer". User turn: `Creator: {creator}\nTitle: {title}\n\nTranscript:\n{body}`. SDK call: `max_tokens=16000` (`analyze.py:652`).

Schema (`analyze.py:207-300`):

```
PlayerCall { player: str, stance: buy|sell|hold|captain|avoid|bench|watch,
             conviction: high|medium|low, gameweek: int|null,
             reasoning: str, quote: str }
ChipCall   { chip: bench_boost|triple_captain|wildcard|free_hit,
             stance: play_now|hold|considering, gameweek: int|null,
             reasoning: str, quote: str }
Insight    { topic: role_change|set_pieces|minutes|fixture_swing|tactical|
                    injury_return|price|chip_strategy|other,
             entity_kind: player|team|fixture|gameweek|none, entity_name: str,
             claim_text: str, quote: str, horizon_gw: int|null,
             horizon_gw_end: int|null, conviction: high|medium|low }
TranscriptAnalysis { summary: [str] (3-6), transfers_in: [PlayerCall],
             transfers_out: [PlayerCall], captaincy: [PlayerCall],
             chip_advice: [ChipCall], differentials: [PlayerCall],
             insights: [Insight] }
```

To a claim row (`claims_from_analysis`, `analyze.py:888-960`): stance maps to `Action` (watch dropped), `gameweek or default_gw` with `gw_inferred` set, confidence from the band, `rationale = reasoning | quote`, `extractor = llm:<model>`, one claim per (player, action, gw) per item. Stored columns: `claim_id, item_id, creator, source_key, player_code, player_name, surface_form, action, season, gameweek, confidence, rationale, source_url, published_at, gw_inferred, extractor`. No timestamp on the claim; `episode_summary._evidence` recovers `start_s` by quote search against `transcript_segment`, 42 of 45 on the measured episode (REVIEW 4.2). Store `start_s` at extraction time in the rebuild.

## C. The solver formulation as built in the old repository

The port in step S4 follows open-fpl-solver semantics (Part 16 section 4). The old formulation below is the reference for the exact integer sell-price arithmetic, which S4 keeps as a test.

### 1. The MILP as built

One model over the whole horizon, PuLP over HiGHS. Entry point `solve_horizon` in `fpl_edge/opt/milp.py:93`. Rules come from `fpl_edge/rules/registry.yaml` through `Ruleset.from_registry` (`fpl_edge/opt/problem.py:59`). Nothing in the model hardcodes a rule value.

#### 1.1 Rule constants (registry.yaml, all `verified: true`)

| Rule | Value | Line |
|---|---|---|
| squad, XI, budget | 15, 11, 1000 tenths | 30-33 |
| per position | GKP 2, DEF 5, MID 5, FWD 3 | 34 |
| XI min / max | GKP 1/1, DEF 3/5, MID 2/5, FWD 1/3 | 35-36 |
| max per club | 3 | 33 |
| free transfers per GW, max banked | 1, 5 | 72-73 |
| hit cost | -4 per extra transfer | 74 |
| transfer cap per GW | 20 (not under WC/FH) | 75 |
| before first deadline | unlimited and free | 76 |
| WC/FH retain banked FTs | true | 77 |
| sell-on fee | keep 50% of the rise, floor to 0.1m | 80-81 |
| chip windows | WC, FH: [2,19],[20,38]; BB, TC: [1,19],[20,38] | 91-95 |
| chips per half, one per GW, FH not consecutive | 2 each (one per half), true, true | 90, 102-103 |
| captain, triple captain | x2, x3 | 108-109 |

#### 1.2 Sets and indices

Players `i` in a pruned universe, gameweeks `j = 0..T-1`, chips `c` in {wildcard, freehit, bboost, 3xc}. Prices `px[i,j]` are integer tenths (dtype-checked, `problem.py:184`). Inputs per (i,j): `xpts`, `p_play`, `ownable` (`problem.py:159-175`).

#### 1.3 Decision variables (`milp.py`)

| Variable | Type | Meaning | Line |
|---|---|---|---|
| `own[i,j]` | bin | held after GW j transfers | 502 |
| `play[i,j]` | bin, only in GWs where Free Hit is reachable; else aliases `own` | the 15 that score | 548-557 |
| `start[i,j]` | bin | in the XI | 585 |
| `b1[i,j]`, `b12[i,j]` | bin, outfield only | bench slot 1; slots 1 or 2 (nested) | 607-620 |
| `cap[i,j]`, `vice[i,j]` | bin | armband | 625-626 |
| `buy[i,j]`, `sell[i,j]` | bin | transfer flow | 636-637 |
| `purchase[i,j]` | cont, 0..MP_i | price paid, follows the player | 638-640 |
| `sale_value[i,j]` | int, 0..px | selling price | 647-650 |
| `sale_used[i,j]` | cont | cash realised this GW | 651-654 |
| `bank[j]` | cont, 0..4000 | bank after GW j | 655-658 |
| `paid[j]` | int 0..15 | hits taken | 713-716 |
| `ft[j]` | int, 1..5 | free transfers entering GW j (j = 1..T, plus T+1 when the FT term is on) | 725-728 |
| `nohit[j]` | bin | big-M switch for the hit count | 729 |
| `chip[c,j]` | bin, created only inside window, unspent half, allowed set | chip played | 389-405 |

Per-player big-M `MP_i = max(price path, purchase price)` (`milp.py:296-298`). A single global big-M made the same instance hit a 600 s limit at a 64% gap (`docs/models/optimizer.md:157-163`).

#### 1.4 Constraints, in plain math

Squad shape, every GW, for `own` and (in FH weeks) `play`: `sum_i own = 15`; `sum_{i in pos} own = quota(pos)`; `sum_{i in club} own <= 3` (`milp.py:565-583`).

XI: `sum_i start = 11`; `min(pos) <= sum_{i in pos} start <= max(pos)`; `start <= play` (`milp.py:589-602`). Those bounds generate the eight legal formations with no enumeration.

Bench order: `sum b1 = 1`, `sum b12 = 2`, `b1 <= b12 <= play - start` (`milp.py:611-620`). Valid only because bench weights are non-increasing (`config.py:106-110`).

Armband: `sum cap = 1`, `sum vice = 1`, `cap <= start`, `vice <= start`, `cap + vice <= 1` (`milp.py:628-634`).

Flow and money (`milp.py:660-707`):
```
own[i,j] - own[i,j-1] = buy[i,j] - sell[i,j]
buy + sell <= 1
buy, sell <= 1 - fh[j]                      (no persistent moves in a Free Hit week)
purchase <= MP_i * own
px - MP_i (1 - buy) <= purchase <= px + MP_i (1 - buy)
|purchase[i,j] - purchase[i,j-1]| <= MP_i (1 - keep),   keep = own - buy
sale_value <= px * own[i,j-1]
2 * sale_value <= purchase[i,j-1] + px      (exactly min(px, pp + floor((px-pp)/2)) at integers)
sale_used <= sale_value ;  sale_used <= px * sell
bank[j] = bank[j-1] + sum sale_used - sum px * buy
FH budget:  sum px * play <= bank[j-1] + sum sale_value + maxcost_j (1 - fh[j])
```

Transfers and hits (`milp.py:731-752`), with `free_j = wc[j] + fh[j] + [j = 0 and preseason]`, `M = 15`:
```
n_j = sum_i buy[i,j]
n_j <= 20 + M free_j
paid_j <= M (1 - free_j)
paid_j >= n_j - ft_j - M free_j
paid_j <= n_j - ft_j + M nohit_j ;  paid_j <= M (1 - nohit_j)
ft_{j+1} <= ft_j - n_j + paid_j + 1 + M free_j
ft_{j+1} <= ft_j + 1                       (WC/FH retain, never mint)
```
`ft` is bounded in [1, 5] so banking caps at 5 (`milp.py:726`). Pre-season `ft_0 = 0` and GW1 is free (`problem.py:224-232`).

Chips (`milp.py:406-449`): one per GW; `sum_{j in half} chip[c,j] <= remaining_in_half`; `fh[j] + fh[j+1] <= 1` and blocked if FH was played the GW before the horizon. Caller windows only remove options: `allowed_chip_gws`, `forced_chip_gws` (exactly one of the named GWs), `no_chip_gws` (`config.py:194-207`).

Locked and banned: bounds, `own[i,j].lowBound = 1` for locked, `upBound = 0` for banned; a held banned player is sold in GW1 by the flow constraint (`milp.py:521-546`). Both survive pruning via a safe list (`milp.py:106-108`, `problem.py:254-266`).

No-good cuts for alternatives (`milp.py:898-947`): with `A` the incumbent's chosen binaries, add `sum(A) <= |A| - difference`. Criteria: `this_gw_transfer_in_out`, `this_gw_lineup`, `chip_gws`; an empty set falls back to the lineup.

#### 1.5 Objective (`milp.py:777-905`, mirrored in `scoring.py:100-141`)

With discount `d_j = decay_base ** j` (default 1.0, `config.py:167`), captain multiplier 2, TC 3, hit cost -4:
```
sum_j d_j [ sum_i xp start
          + (2-1) sum_i xp cap
          + (3-2) * xp(captain) * tc_j                        (exact linearisation)
          + (2-1) sum_i (1 - p_play(captain)) xp_i vice_i     (exact bilinear)
          + 0.06 xp(bench GK) + 0.19 xp(slot1) + 0.06 xp(slot2) + 0.015 xp(slot3)
          + bb_j * (full bench xp - weighted bench xp)
          - 4 paid_j ]
+ sum_j d_j [ V(ft_{j+1}) - V(ft_j) ]                         (off unless ft_value_list set)
```
Bench weights are `AutosubWeights(gk=0.06, outfield=(0.19, 0.06, 0.015))` (`config.py:97-99`), declared placeholders. `V` is the telescoping potential built from `FT_VALUE_LIST_SOTA = {2: 2.0, 3: 1.6, 4: 1.3, 5: 1.1}` and `FT_VALUE_DEFAULT = 1.5`, represented by concave chords so no one-hot binaries are needed (`config.py:63-72`, `milp.py:907-963`). Three approximations are declared: constant autosub weights, the vice term ignores TC, DGWs arrive pre-summed in `xpts` (`scoring.py:12-22`). `score_plan` recomputes the objective from decisions alone and the tests pin agreement to 1e-6 (`tests/unit/test_opt_objective.py:57`). `replay_finances` re-runs the ledger in integer tenths with `selling_price` (`scoring.py:395-482`).

Chip uplifts collapse to one variable per GW when all coefficients are non-negative, bounded by one player's points (TC) or four (BB); a naive big-M let the LP relaxation play every chip fractionally for free (`milp.py:802-812`).

## D. Projection providers as measured in the old repository

### 1. Providers the old repo knew

7 providers wrote `fact_projection` in 2026-27. 24 candidates were evaluated (`docs/platform/projection_providers.md:27`). The engine's own model never wrote to `fact_projection`; it wrote `forecast.parquet`.

| provider key | obtained | format and key columns | cadence (measured) | 2026-27 coverage in warehouse | cost / licence | status |
|---|---|---|---|---|---|---|
| `fplform` | `POST fplform.com/export-fpl-form-data.php` (`fplform.py:6`) | wide CSV, `ID,Name,Team,Pos,Price,{gw}_pts_no_prob,{gw}_prob,{gw}_with_prob`; ID is element_id | revises most days; 28 snapshots | 662 players, GW1 to 11, 100,152 rows | free; personal use permitted, republishing forbidden | live |
| `fpl_ep` | `bootstrap-static` JSON `ep_next` + `chance_of_playing_next_round` (`fpl_ep.py:6`) | per-element strings; next GW only | every price poll; 25 snapshots | 662 players, GW1 to 6, 9,795 rows | free, official API | live, baseline |
| `gh_fplbench` | `raw.githubusercontent.com/PascalAI2024/fplbench/.../gw{gw}_{season}.csv` (`github_csv.py:136`) | `player_code`, `pred_minutes`, `e_points_final` | per-GW commit | 587 players, **GW1 only**, last row 2026-08-31 | MIT | live but dead in practice |
| `gh_apex_airsenal` | `mcnuggets651/fpl-apex` `data/generated/airsenal.csv` (`github_csv.py:232`) | long CSV, 8-GW horizon, `gw` column | 7 snapshots, **last 2026-08-31** | 612 players, GW2 to 9, 34,208 rows | MIT | live but stale 3 weeks |
| `gh_blueladd` | GitHub contents API discovery + raw CSV | `element`, `xp`, `xmins`, `xp_next` (6 values ; joined) | hourly GH Action | 554 players, GW1 to 8, 41,272 rows | no licence | **retired 2026-09-08** (`views.sql:60-66`) |
| `premierinjuries` | HTML `injury-table.php` | Status text mapped to `p_appear` 0/0.25/0.5/0.75/1 | site edits; 27 snapshots | 84 to 105 flagged players per GW, `p_appear` only | robots fully open | live |
| `fplreview` | hand export from the owner's paid account, dropped as `data/projections/fplreview/fplreview_<epoch>.csv` (`local_csv.py:1-60`) | wide CSV `Pos,ID,Name,BV,SV,Team,5_xMins,5_Pts,...,14_xMins,14_Pts,Elite%`; ID is element_id | manual; **1 drop** (epoch 1789744594 = 2026-09-18 15:16:34 UTC) | 656 of 675 file rows resolved, GW5 to 14, 6,560 rows, xMins on every row | PAID; amount not recorded anywhere in the repo (`providers.py:224-228`) | live |

`rotowire` (predicted XIs) and `livefpl` (effective ownership) run through the same CLI into their own tables (`projection_providers.md:100-108`). Rejected (`:186-227`): FF Hub, FF Fix, FF Scout (paywalled); Sofascore, FotMob, WhoScored, FF Pundit, FBref, derekkuang (forbidden); FPL Statistics, elfootball (dead); SportsGambler, fpl-projections-site (watchlist). Solio Analytics is the named template for a second hand drop (`local_csv.py:44-58`) and was never added.

The engine's own forecast: `data/warehouse/forecast.meta.json` on disk reads `forecast_source: "engine"`, generated 2026-09-20T11:17 UTC, 2,405 rows, 481 players, GW6 to 10. The scheduled `forecast_refresh` writes consensus at 11:30 UTC (`pipelines/registry.py:691-695`). Something wrote an engine forecast 13 minutes earlier. Unresolved.

FPLReview mapping check: element 1 (Raya) in the CSV reads `5_xMins 94, 5_Pts 3.98`; the warehouse row for code 154561 GW5 reads `xp 3.98, xmins 94.0`.

### 2. Consensus and measured accuracy

**Consensus definition** (`fpl_edge/store/views.sql:111-127`): `sem_projection_consensus` is the unweighted `AVG(xpts)` over every non-retired provider that published a non-null `xpts` for the `(season, gw, code)`, with `n_sources`, `MIN`, `MAX`, `spread`, `stddev_samp`, and a plain `AVG(xmins)`. The comment at `:105-109` states the choice: "consensus is unweighted by design. projection_weight stays empty until settled actuals score the sources; weighting without a track record would be fabrication". A second macro `sem_projection_consensus_weighted` (`:196-229`) blends by inverse-MSE weights, renormalised over the providers present for the player. Every screen and the solver default to `equal` (`scripts/projections/__init__.py:16-24`, `xpoints.js:48-52`).

At GW6 the consensus averages 3.92 sources per player; at GW7 to 9 it is 2.92; GW10 to 11 is 1.99; GW12 to 14 is FPLReview alone (query on `sem_projection_consensus(now())`).

**Scores** (`fact_projection_score`, scope `overall`, metric `mae`, baseline = all-provider mean on the same observations, only pre-deadline fetches scored; `eval/projection_scoring.py:1-45`):

| GW | n | fplform | fpl_ep | gh_apex_airsenal | gh_fplbench | gh_blueladd | baseline (mean) |
|---|---|---|---|---|---|---|---|
| 1 | 599 | **1.413** | 1.623 | not yet fetched | 1.569 | 1.802 (n 476) | 1.472 |
| 2 | 616 | 1.214 | 1.464 | **1.182** (n 612) | none | 1.559 (n 534) | 1.251 |
| 3 | 652 | **1.115** | 1.295 | 1.245 | none | 1.527 | 1.181 |
| 4 | 654 | **1.214** | 1.245 | 1.398 | none | 1.654 | 1.262 |

Same table for the `own_gt5` scope (players over 5 percent owned, n 68 to 73):

| GW | fplform | fpl_ep | apex | fplbench | blueladd | baseline |
|---|---|---|---|---|---|---|
| 1 | 2.641 | **2.372** | none | 2.934 | 2.759 | 2.618 |
| 2 | 3.104 | 3.244 | **2.840** | none | 3.432 | 3.086 |
| 3 | **2.298** | 3.534 | 2.399 | none | 2.551 | 2.487 |
| 4 | **2.822** | 3.416 | 2.904 | none | 2.936 | 2.867 |

`p_appear` Brier (scope `p_appear`):

| GW | fplform | fpl_ep | premierinjuries | baseline |
|---|---|---|---|---|
| 1 | **0.138** | 0.329 | 0.149 | 0.176 |
| 2 | 0.108 | 0.322 | **0.077** | 0.161 |
| 3 | **0.093** | 0.284 | 0.144 | 0.142 |
| 4 | 0.097 | 0.285 | **0.093** | 0.143 |

Pooled inverse-MSE fit through GW4 (`projection_weight`, fit_id `2026-27:invmse:thru-gw4`):

| provider | weight | pooled MSE | baseline MSE | n_obs |
|---|---|---|---|---|
| fplform | 0.240 | 4.82 | 4.96 | 2,521 |
| gh_apex_airsenal | 0.236 | 4.90 | 4.59 | 1,836 |
| fpl_ep | 0.197 | 5.87 | 4.96 | 2,521 |
| gh_blueladd | 0.176 | 6.58 | 5.77 | 2,118 |
| gh_fplbench | 0.152 | 7.58 | 6.49 | 587 |
| fplreview | 0 | none | none | 0 (no pre-deadline settled GW yet) |
| premierinjuries | 0 | none | none | p_appear only |

Three findings:

1. **fpl_ep is worse than the consensus.** Overall MAE sits above the baseline by 0.15, 0.21 and 0.11 in GW1 to 3, level in GW4; on owned players worse by 0.16, 1.05 and 0.55 in GW2 to 4; `p_appear` Brier about twice the baseline every week. Cause at `fpl_ep.py:20-25`: `null` chance with status `a` is stored as 1.0.
2. **fplform is the only provider that beats the baseline on pooled MSE** (4.82 vs 4.96), by 3 percent. Apex is best in one GW and worse than baseline in GW3 and 4.
3. **The fit still carries gh_blueladd at 0.176** after its 2026-09-08 retirement (`views.sql:163-166`). The weighted consensus filters retired sources at read time so it does no harm, but the rebuild should not repeat it.

**The engine model ran hot** (`cli/solve.py:43-48`, `pipelines/registry.py:683-686`, `:745-748`): on 2026-09-07 the GW4 position means were GK 1.42 vs consensus 0.97, DEF 2.00 vs 1.46, MID 1.97 vs 1.44, FWD 2.28 vs 1.33, 37 to 71 percent above the providers. It rated a GK away at Chelsea at 29.2 xPts over GW4 to 8 against a four-provider 12.1 and sold a GK for him; on 2026-09-19 it captained a 4.5 percent owned midfielder the consensus ranked thirtieth. The engine never wrote to `fact_projection`, so it has no score row and no MAE. The fix was to solve in consensus (`registry.py:691-695`); the sidecar on disk says the forecast has since regressed to engine.

Cross-check at GW6 (query on `sem_projections(now())` vs the equal-weight mean, n 612 to 662): correlation with consensus fplform 0.948, fplreview 0.933, fpl_ep 0.904, apex 0.847; mean bias fpl_ep minus 0.089, fplreview plus 0.059.

### 3. Verdict: own points model or not

Opinion, argued from the numbers above.

**Do not build an own points model in the rebuild.** Three measurements:

- The only engine run compared to providers was 37 to 71 percent hot in every position and produced two documented bad transfers. It was never scored because it never entered the scoring loop.
- Dixon-Coles lost to a synthetic market by 0.022 nats (95 percent CI 0.010 to 0.035) and 0.0028 clean-sheet Brier (`docs/models/team_goals.md:52-60`). It was never scored against a real market.
- The equal-weight mean beats every single provider on pooled MSE except fplform, and beats fplform on 2 of 4 weeks. The repo's own thesis, "copies free xMins/xPts opinions and blends them by measured track record" (`projection_providers.md:3-5`), is the one the numbers support.

**Minimal own model worth keeping: none in v1.** The strongest own-model result is the minutes GBM: log loss 0.4165 vs the best baseline 0.7989 on 29,747 player-fixtures, 0.6330 vs 0.8021 on the GW1 cold-start slice (`docs/models/minutes.md:71-92`), a 48 percent cut. FPLReview `xMins` and FPLForm `p_appear` (Brier 0.093 to 0.138) already cover that quantity for every player. If a later phase wants an own model, build only a `p_appear` classifier, score it as a provider, and weight it only when it earns a weight. Never let it write xPts.

Fixture difficulty is separate (section 4): the own Dixon-Coles fit stays as a fallback because odds cover only the next one or two gameweeks.

## E. Glossary

| Term | Meaning |
|---|---|
| GW | Gameweek. 38 per season. Each has a deadline (UTC in the API) after which squads lock. |
| xPts | Expected FPL points for a player in a gameweek, from a provider or the consensus. |
| xMins | Expected minutes. p_play is the probability of any appearance. |
| FT | Free transfer. One accrues per gameweek, banked up to 5 in 2026-27. Extra transfers cost 4 points each (a hit). |
| ITB | In the bank. Money not spent on the 15. |
| WC, FH, BB, TC | Wildcard, Free Hit, Bench Boost, Triple Captain. Two sets in 2026-27: GW2-19 and GW20-38. |
| DC | Defensive contribution: 2 points at 10 CBIT for defenders, 12 CBIRT for midfielders and forwards. |
| CBIT, CBIRT | Clearances, blocks, interceptions, tackles (and recoveries). |
| Consensus | The unweighted mean of xPts over the non-retired providers for a player and gameweek. |
| Panel | One typed read function with pydantic params and result, served as an HTTP route, a TypeScript type and a chat tool. |
| Artefact | A stored result of a computation (plan, brief, ratings) that records its inputs so a reader can judge whether it is fresh. |
| Ledger | The `job` table: one row per run with the trigger, counts, cost and outcome. |
| Settlement | Writing a gameweek's final points once FPL marks it finished and bonus is added. |
| Autosub | FPL's automatic substitution of a non-playing starter. The picks endpoint returns post-autosub order after a gameweek ends. |
| Player code | FPL's stable cross-season player id. element_id changes each season. |

