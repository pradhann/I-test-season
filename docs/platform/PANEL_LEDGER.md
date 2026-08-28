# Panel build ledger

Prompt: `docs/platform/CREATOR_ELITE_PROMPT.md`

| Stage | Status | Commit | Notes for a fresh session |
|---|---|---|---|
| 0 repairs | **DONE** | c789453, 63c9c0b, 5433f0b, 43c2837, c507c73, 13358f0, b732d8d, 0f617a3, b2f5af0 | All 12 defects repaired; TWO adversarial passes run, both refuted the work, all refutations fixed and re-verified against the auditors' own probes. Full suite green (0 failures) on an uncontended machine. Two items deliberately carried forward, not silently closed: the transfer ROW COUNT cannot be checked until the GW2 deadline passes, and the cohort hindsight-selection problem is a blocking design item on Stage B. |
| A registry + identity | **PARTIAL** | 46ec15c | The creator->entry half was attempted and correctly yielded ZERO links: all 29 creator names checked against 12,276 crawled managers under exact AND containment matching, no hits, because every roster name is a channel name. Reasons stored per creator in `creator_entry`. The panel-member seed file and `dim_panel_member` are still unbuilt. Ready to continue. Nothing blocks it. Note Stage 0 already deleted the toolbelt's 20 fake identities, so Stage A's resolver has a clean field — reuse `elite.verify()` and `names.norm`, both now single-implementation. Four panel members already have verified ids on ELITE_NAMED (Crellin 53517, Andy LTFPL 41, Sutherns 252, Bakar 5133): reuse, do not re-derive. |
| B elite history + panel EO | TODO | | **BLOCKING DESIGN ITEM inherited from adversarial pass 1 finding 8:** cohort membership must become per-gameweek. Today's `sem_manager_cohort` selects the top-1k from crawls that ran 3-6 days AFTER the GW1 deadline, so EO for GW1 is reported for a cohort chosen because of its GW1 result. Stage D's proxy and differential tests are invalid until this is fixed. The per-gw rank is already in `dim_manager.source`. |
| C corpus + ideas + search | **PARTIAL** | 46ec15c | LLM analysis wired into the pipeline and backfilled: content_analysis 2 -> 118, llm claims 40 -> 162, 22 of 23 recent creators now have a take. Still unbuilt: podcast ASR, the `content_idea` grain, and the FTS search index. |
| D backtest | TODO | | Do not start before Stage B's cohort fix: tests 3 and 4 would measure a survivorship-selected cohort. |
| E chat reach | TODO | | |
| F two tabs | **PARTIAL** | 9858d3b, c2a3901, 48fdbab, 46ec15c, e332148 | Both tabs BUILT and browser-verified, full suite green — but NOT done: no adversarial pass has been run over them, and the Panel tab still lacks the manager drill-down and the transfer-flow timeline (blocked on transfers, which are empty until a deadline passes). Do not mark DONE without D.2. |
| G verdict | TODO | | |

## NEEDS OWNER (anything blocked on a human decision)

**The `elite` cohort contains 49 of the owner's own mini-league opponents.**
Live composition of the 311-manager elite cohort: 250 `elite_list`, **49
`mini_league`**, 12 `winner`, 8 `elite_named`. So ~16% of the "elite" EO
denominator is people the owner happens to play against, not a selected elite.
Nothing discloses this. Options: exclude `mini_league` from the elite cohort,
give it its own cohort, or keep and disclose. This is a judgement about what
"elite" should mean here, so it is the owner's call rather than mine.

**The panel and the macro pick their gameweek by different rules.**
Surfaced 2026-08-28, ~26 minutes after the GW2 deadline. `ownership_eo` falls
back to the last gameweek that HAS data and labels it (`cohort_gw: 1`, and its
`cohort_note` says so); `measure_cohort` insists on the last LOCKED gameweek
and returns `n_managers=0` with the reason "the crawl has not run since that
deadline". Both are defensible. Both are documented. But
`test_the_panel_reports_the_same_eo_as_the_macro`'s own docstring says the
panel must be "a reader of the definition, never a second implementation" --
and "which gameweek" is now implemented twice.

Live state at the time of writing: GW2's deadline has passed, `fact_manager_pick`
holds GW1 only, so the macro reports nothing while the panel serves GW1 EO. **No
wrong number reaches the UI** -- the panel labels the gameweek in both the field
and the note -- so this is not urgent. It matters when a caller compares the two
layers without noticing they answered about different gameweeks.

Options: (a) the panel adopts last-locked-gw and reports `{empty, reason}` until
the crawl runs, which makes the tab go blank for the window between a deadline
and its crawl; (b) the macro adopts last-gw-with-data; (c) keep both and give
the panel an explicit `gw` parameter so the caller chooses. Related and worth
deciding together: **`ownership_eo` has no `as_of` parameter at all**, so it
cannot be evaluated at a fixed instant -- it is the one layer here that is not
point-in-time, and that is why its agreement test had to read the real clock.

## Decisions taken (append-only)

**2026-08-27 — Re-verification of the prompt's AUDITED CURRENT STATE.**
Every load-bearing claim in §3 was re-checked against the live warehouse
read-only before any work started, and all confirmed:
`claim_outcome` 241 rows, every one `hit IS NULL`; `fact_manager_transfer`
0 rows; `fact_manager_pick` 22,620 rows for 2026-27 GW1 only across 1,508
managers; `fact_manager_chip` bboost for 1,411 managers; `content_claim`
216 cue + 40 llm; `content_item` 354 podcast + 97 youtube as `description`,
95 blog excerpts, only 4 real transcripts. No corrections needed.

**2026-08-27 — MCP folded into this repo as a root-level `fpl_mcp/` package.**
Owner instruction: "fold everything into i-test-season and feel free to change
and build your own MCP". Chosen layout and why:

- `fpl_mcp/` is a SIBLING of `fpl_edge/`, not a subpackage. The dependency
  runs one way — the MCP imports the engine, never the reverse — and a sibling
  package makes that direction obvious and unbreakable. A `fpl_edge/mcp/`
  subpackage would invite the reverse import.
- Copied (not submoduled/subtreed) from `~/Documents/Github/FPL-MCP` at its
  commit `c5054a8`, recorded here because a plain copy loses that history. The
  old repo is left untouched as an archive; it is no longer the live copy and
  must not be edited.
- `mcp[cli]` added to this repo's dependencies so the toolbelt runs on the
  SAME interpreter as the engine. Previously `chat_agent.MCP_PYTHON` pointed at
  a pyenv 3.11.2 that happened to have both `mcp` and a path to `fpl_edge`;
  that second environment is now gone, and with it a whole class of "works in
  chat, fails in tests" drift.
- The three `sys.path.insert(_HOME)` shims in `tools/{edge,content,dossier}_
  tools.py` become unnecessary once the packages are siblings. Removed rather
  than left as no-ops, because a dormant path shim is a trap for the next
  reader.

**2026-08-27 — B1 fixed and applied to the live warehouse (commit `c789453`).**
`claim_outcome` went from 241 rows / 0 settled to **256 rows: 56 hits, 106
misses, 94 legitimately unscoreable** (82 gameweek-not-played, 12 published
after the deadline). The 15 extra rows are claims that had never been scored
at all because they postdated the last run.

**The honest headline, which the UI must not dress up:** settling those claims
unlocked no signal. Best measured hit rates are 32–46% across 15 creators, so
**every earned weight is still 0.0** — the Wilson lower bound at n≥25 has not
been cleared by anyone. That is the gate working, not a residual bug. Stage F
must show this as "no creator has beaten a coin flip yet", not as an empty
leaderboard that implies missing data.

**2026-08-27 — B2/B3/B9/B12 rivals crawl (commit `63c9c0b`).** The crawl now
reserves budget per stage instead of letting one stage eat it all, ordered
pool → picks → transfers → history. `main()` exits non-zero when a stage does
not finish, which is the actual reason this went unnoticed: `post_gw` judges
steps purely on return code, and a starved crawl returned 0.

**The generalisable lesson, worth applying to every later stage:** a stage
that could not finish looked exactly like a stage with nothing to do. That is
this codebase's characteristic failure and the adversarial "silent-failure
lens" exists for it.

**2026-08-27 — the 1,682 snowball managers are NOT salvageable.** They are
league-mates of twenty arbitrary strangers: the seed IDs no longer identify
the people they claim to, so the selection rule that produced this pool is
unreproducible. Their `dim_manager` rows are real entries, but
`source='snowball:{lid}'` is not evidence of anything. **They must not be
treated as an elite cohort in any skill, copying or EO analysis.** Not
deleted — deleting real observations to tidy a taxonomy would be worse — but
gated out of the crawl pool, which is why the elite candidate count drops from
2,015 to ~313. Stage B and Stage F must not quietly count them.

**2026-08-27 — MCP folded and B4/B11 fixed (commit `5433f0b`).** Also
recorded: `expert_tools.get_expert_teams_summary` has a sibling of the B4 bug
— it guards with `if pid not in elements_df.index: continue`, so it does not
crash but silently drops an unresolvable player from an ownership summary.
Left alone as out of scope — **and that was the wrong call**: adversarial pass
1 found the same file also shipped 20 invented identities. Both are now fixed
(commit `13358f0`). The duplication is resolved too: the toolbelt's copy of the
map is deleted outright, so `roster.EXPERT_SEEDS` is the single copy in the
repo, kept deliberately as the provenance record for the already-crawled
snowball and gated by `verify_expert_seeds`.

**The lesson, since it recurs:** "out of scope" is a judgement about effort,
not about truth. A known-dishonest output left in place because it sat outside
a ticket boundary is still a dishonest output.

**2026-08-27 — B7/B8 EO collapsed to one definition (commit `43c2837`).**
New `sem_manager_cohort(t)` macro is the single SQL definition of cohort
membership, with `resolve_cohorts()` as its Python twin and a test pinning
them identical. `tests/unit/test_field_eo_agreement.py` compares SQL, model
and panel against *each other* on one warehouse rather than against three
hand-written constants — they can now only agree or fail together.

The agent was killed by a usage limit before reporting its break-checks, so
**I ran them myself**: replacing the multiplier sum with a plain holder count
failed the macro/model and macro/panel agreement tests; collapsing the cohort
rule so every entry is 'elite' failed those two plus both cohort-assignment
tests. Restored, green.

**2026-08-27 — B2's acceptance criterion CANNOT be met before the GW2
deadline, and this is correct behaviour, not an outstanding bug.**
The crawl now demonstrably reaches the transfer stage: 637 transfer bodies
fetched (against 8 before the fix) with `incomplete_stages: []`. Every one is
empty, and `fact_manager_transfer` is still 0. That is not a second bug:

- GW1 is the season's FIRST gameweek, so no transfers can exist for it.
- A squad and its transfers become **public at the deadline** (see the
  Observability note in `ingest/rivals/picks.py`), and the GW2 deadline is
  2026-08-28 17:30 UTC — tomorrow.
- Confirmed directly against the API, not inferred: `/entry/{id}/transfers/`
  returns `[]` for crawled entries **and for the owner's own team 4490171**.

So the fix is proven at the pipeline level (fetch count, stage completion,
non-zero exit on starvation, unit tests) but cannot be proven by row count
until the next post_gw run after Friday's deadline. **A future session must
re-check this rather than assume it passed** — and if the table is still empty
after the GW2 deadline has passed, that IS a real bug and the pipeline-level
evidence above does not excuse it.

**2026-08-27 — a concurrency scare worth recording.** One fix agent ran
`git stash --include-untracked` followed by `git stash pop` while three other
agents were editing the same tree. It restored cleanly — stash list empty, no
stash reflog entries, every concurrent file still present and modified — and
the agents that owned those files re-ran their own tests green afterwards. No
loss found. **The rule stands and should be stated to every future agent: no
mutating git commands, and `git stash` is a mutating git command.** A parallel
fan-out over one working tree has no safe stash.

**2026-08-27 — a claim in my own brief was imprecise.** I told an agent "zero
players have more than one position in dim_player". True at the (season, code)
grain, which is the grain the lookup uses — but 44 codes carry more than one
position ACROSS seasons, which is ordinary reclassification. The agent caught
and corrected it rather than taking it on trust; recorded because the same
loose phrasing could mislead a future reader into thinking position is
immutable.

### 2026-08-27 — Pass 2, silent-failure lens. STAGE 0 REFUTED AGAIN.

The harsher of the two passes. It refuted the crawl repair through **two doors
neither the fix nor its tests had closed**, and showed the EO guard rail was
weak enough to be decorative.

**REFUTED and now fixed (commit `0f617a3`):**
- *A crawl that wrote nothing exited 0.* `_write` returned
  `{"status": "locked"}` when the write lock never freed, and nothing read it:
  all stages `ok`, `incomplete_stages` empty, zero rows, exit 0, post_gw green.
  The original outage, reached through a different door.
- *An empty pool reported every stage ok.* Live-relevant, not theoretical —
  B9 now rejects all twenty stale seeds, so the pool is thinner than it was.
- *Stages after a starved pool reported `ok` while doing nothing*, so three of
  four statuses in the receipt were actively false.
- *`except Exception: return 0` in `top1k._sampled_so_far`* silently retargeted
  `--grow 300` from existing+300 down to 300. **That blanket except was hiding
  a live bug the audit did not name:** `g != g` on a pandas NA raises
  TypeError, so on any warehouse whose tables exist but hold no top-1k rows,
  the broken-read path was taken every time.
- *"post_gw will notice a failed crawl" was false.* It wrote to a JSON file
  nothing in the repo reads, and launchd discards the exit code. Failures now
  enqueue one alert on the existing outbox — the deadline DAG's own mechanism.

**The independent check that matters:** the auditor's `attack_crawl.py`, whose
assertions assert the holes exist, now fails all four attacks. Verified by me
directly, not taken from the fixer's report.

**[FIXED, commit `b2f5af0`] The EO guard rail.** 15 of 20 mutations survived
it; 8 survived the entire wider suite, including inverting the PIT sort order
and changing `as_of <=` to `<` in the cohort macro — literal SQL-vs-Python
drift the fixture could never catch because every fixture row sits strictly
before the snapshot. The structural cause matters more than the eight cases:
the fixture is 8 managers with no NULL multipliers, no unresolvable element
ids, no duplicate picks and no Bench Boost, and the five columns commit
`43c2837` ADDED were never compared against Python at all.

**Recurring lesson across both passes:** every refutation was the same shape —
success and doing-nothing being indistinguishable. Four of the repairs' own
claims were wrong in exactly the way this codebase specialises in. The
silent-failure lens is not one lens among five; for this repo it is the one
that finds things.

**2026-08-27 — the auditability fix proven on the LIVE warehouse.** A second
`score` run against the real database: the 256 pre-existing rows kept their
original `resolved_utc` of 03:00:55 (not restamped), only the 21 genuinely new
claims took a fresh stamp, `claim_outcome_revision` exists and correctly logged
zero revisions. Live settled state is now 56 hits / 106 misses / 115
unscoreable — the unscoreable count rose from 94 because 21 further claims have
been ingested for gameweeks that have not been played.

Note for whoever reads the auditor's scratchpad reproductions: `attack_crawl.py`
asserts that the holes EXIST, so a correct fix makes it FAIL (it now fails all
four). `test_settlement_audit.py` is a demonstration that passed before the fix
too, so its passing proves nothing — the live check above is the evidence for
that finding, not that script.

**2026-08-27 — the guard-rail repair, and the lesson in it.** Tests 3 -> 18,
and the fix was to the FIXTURE, not to the assertions. The eight surviving
mutations were not missed through carelessness; the boundary mutation
(`as_of <=` -> `<`) was *invisible by construction* because every fixture row
sat strictly before the snapshot. A fixture too clean to fail is not a test
world. Two of the new tests are structural rather than case-by-case: all 14
macro columns are compared row-by-row against Python twins and the column SET
is asserted, so a column added without a twin fails on purpose; and a second
test fails if the fixture ever goes boring again.

21 of 22 mutations verified caught. **I re-applied the two sharpest myself
rather than trust the report** — `as_of <=` -> `<` and `DESC` -> `ASC` each now
fail four tests. The one survivor is equivalent, not a gap: the squad-length
check is redundant against the slot-sequence check that precedes it.

Three details where my own brief to the agent was wrong, all corrected by it:
the stale "17" was in `observed.py:63`, not `cohorts.py:62`; `views.sql` does
not contain the word "inclusive" (it says "at or before"); and the
thread-ordering bug needed NINE sources in reverse lexical order to catch
deterministically — with two it caught the bug about 2 runs in 3, and a flaky
catcher is not a guard rail.

**2026-08-27 — both auditors' probe sets re-run after all fixes.** The leakage
probes (6) pass, meaning PIT holds. The crawl attacks (4) all fail, meaning the
holes are closed. Verified by me directly.

**2026-08-27 — STAGE 0 CLOSED. Acceptance, item by item:**

| Criterion | Evidence |
|---|---|
| Full unit suite green | 0 failures on an uncontended machine |
| Settled claim outcomes non-zero | 56 hits / 106 misses / 115 unscoreable, live |
| Crawl reaches AND completes transfers | 637 bodies fetched (was 8), `incomplete_stages: []` |
| Elite pick coverage improved | 311 of 1,978, was 25 of 2,015 |
| Crashing MCP tools return results | both invoked live |
| One EO definition | 21 of 22 mutations caught; 2 re-verified by me |
| Adversarial pass survived | 2 passes, 10 refutations, all fixed and re-checked |

**One honest caveat on the suite.** An earlier full run failed
`test_chat_agent.py::test_second_turn_resumes_with_the_stored_session_id` with
`IndexError` on `lines[1]` — the second turn had not produced output yet. It
passes in isolation, in-file, and in a clean full run; it failed while five
pytest processes and four agents contended for the machine. **It is a
load-sensitive flake, not a regression, and it is not fixed.** A future session
that sees it under parallel load should not treat it as damage from this work —
but it is real test debt and worth hardening when someone is next in that file.

**Two items are carried forward rather than closed:**
1. `fact_manager_transfer` row count — provably uncheckable before the GW2
   deadline (2026-08-28 17:30 UTC). **The first post_gw run after that deadline
   must be checked.** If it is still empty then, that IS a bug and the
   pipeline-level evidence does not excuse it.
2. Cohort hindsight selection (pass 1, finding 8) — blocking design item on
   Stage B, and Stage D must not start before it.

**2026-08-27 — UI round: both tabs built, out of stage order at the owner's
request.** The owner asked for the Template redesign and the Creators tab
directly, so F ran ahead of A–E. Four agents, file-disjoint, against a contract
(`CREATOR_PANEL_CONTRACT.md`) written first so the panel and the view could be
built simultaneously.

**A misattribution bug found by one agent while building against another's
work.** `claims_from_analysis` resolved a structured player NAME with the prose
scanner: `find_mentions("Martin Ødegaard")` tokenises on `[a-z0-9]+`, loses the
stroke letter, falls back to the bare token `martin`, and returns **David Raya
Martín**. Auditing the 162 stored llm claims found zero wrong rows — the bug's
usual effect was silently DROPPING accented names — but it was one call away
from writing one. Fixed to exact alias, then containment, then given-name
prefix, never an edit distance; that rule also refuses three real
misattributions the scanner was making (Louie Barry→Thierno Barry, Mohammed
Vuskovic→Luka Vuskovic, Trent Hume→Trai Hume, each a different footballer
sharing a surname). Commit `e332148`.

**Two live external fields were being computed and discarded.** `eo_top10k`
and `eo_elite` are current for 2026-27 (600 players each); the old ownership
panel pivoted them and threw the result away, so the UI could not see them.
Now first-class in a `fields[]` ladder where every field names its denominator
in words and carries `n=null` where the population is genuinely uncountable.

**My own spec was wrong on units, and the fix is better than what I asked for.**
I specified `eo_minus_global = cohort EO − global ownership`, which subtracts a
head-count share from a sum of multipliers; my worked examples were
ownership-vs-ownership. It is now two like-for-like comparisons.
**Note for the owner: the same formula appears in the creator/elite tracker
document, so the error is in the source spec, not only in my brief.**

**Measured, not assumed:** LiveFPL's GW2 rows are byte-identical to GW1 for all
600 codes — a republished settled week, not a forecast — and the page shows a
warning chip rather than implying two gameweeks of evidence.

**Honest limits both tabs now surface rather than hide:** no creator has a
verified team; 100 of 118 takes derive from show notes rather than transcripts,
and only 4 creators produce takes carrying actual player calls. The second is
the highest-value next step for the Creators tab and podcast ASR is the fix,
not more model calls.

**2026-08-28 — Fixtures: the view and the panel were built to different names.**
The rebuilt tab looked identical to the old one, and the reason was not the
design: `fixture_board` published `opponent_only.attack_xg`, `market.age_hours`,
`scale.anchor_attack_xg`, `team_news.by_team{code: [...]}`, `calibration.model`;
the view read `attack_xg`, `market_age_hours`, `anchor_attack`, `team_news[]`,
`calibration.sentence`. Every lookup returned `undefined`, so the page drew 20
rows of blank club names, greyed every cell "no fit", and printed "The split is
not in this payload" while holding the split. The panel had never returned zero
rows -- it returned 20 in ~95ms throughout.

Fixed with two flattening adapters (`flatten`, `flattenDetail`) rather than
edits at ~40 call sites, so the two contracts meet in one auditable place.
This is the third time this class has shipped here: a view that degrades
*gracefully* and *honestly* on missing fields is indistinguishable, from the
outside, from one whose fields are all missing. The named-gap discipline that
makes this codebase trustworthy is exactly what hid the defect -- every message
on screen was true, and the conclusion drawn from them was false.
**Guard worth building:** a contract test that asserts each view's field reads
are a subset of its panel's published schema. Nothing today would catch this.

**2026-08-28 — A 13-man Crystal Palace XI, and why per-player "latest" is wrong.**
Rendering the predicted XI surfaced a real defect: `_lineups_block` deduped with
`row_number() OVER (PARTITION BY provider, season, gw, code ORDER BY as_of DESC)`
-- latest row per PLAYER. rotowire drops a player from the XI by ceasing to emit
a row for them, never by writing `predicted_start = false`, so a dropped
player's own latest row says `true` forever. Palace's GW2 XI came back as the
real eleven plus two players dropped the day before.
Now the latest SNAPSHOT per `(provider, team_code)`; absence from it is absence
from the XI. Safe **only** because `ProjectionStore.append` includes `as_of` in
its dedupe key, so every poll writes its complete emitted set -- verified before
shipping, because if unchanged rows were skipped this fix would silently delete
starters. Break-watch-restored: 12 with the old query, 11 with the new.
Chip filed to audit `fact_player_state` and `set_piece_duty` for the same
pattern (removal-by-omission vs removal-by-explicit-row).

**2026-08-28 — Calibration is a bracket, not a number.**
The model says the six-gameweek fixture swing is worth 2.4 pts to an attacker;
the empirical fit on 28,353 starts says 5.4 for an outfielder. Neither is "the"
answer: the model carries no estimation noise and is therefore a floor, and
best-minus-worst across twenty estimated effects is biased upward by sampling
noise and is therefore a ceiling. The page prints both ends and says which is
which. Printing the midpoint would invent a precision neither has.

## Corrections to the prompt's AUDITED CURRENT STATE (append-only)

**2026-08-27 — §3.2 B1's "241 rows" becomes 256 on a rescore.** Fifteen claims
had never been scored at all (they postdated the last nightly run), so the
repaired `insert_outcomes` reports them as new alongside the 162 revised. The
defect description was otherwise exact.

**2026-08-27 — §3.3's "`--no-transcripts` is a silent no-op" understates it.**
Nothing in the bulk ingest path fetches transcripts *at any point*:
`youtube.py:fetch_transcript` refuses unless `allow_disallowed_routes=True`
and no caller in the package passes it. So there was no fetching for the flag
to suppress, and making it "work" would have required flipping the robots
gate. The flag was removed rather than faked. This sharpens rather than
contradicts §3.3's "no transcripts in the bulk pipeline".

**2026-08-27 — §3.2 B2 was understated: the starvation was PERMANENT.**
`entry/history` has a 12h TTL and the job runs daily, so each night re-fetched
the same first ~370 histories the previous night had already paid for, then
raised. Picks and transfers were unreachable by construction, not merely
delayed — which is why the 08-25 and 08-26 receipts are byte-identical with
zero cache hits. Reordering alone would not have fixed it: picks for 2,015
candidates also exceeds 400. The fix had to be per-stage budget reservation.

**2026-08-27 — §3.2 B12's premise was WRONG, and this is the instructive one.**
The prompt asserted a 94% GW1 bench-boost rate "is not a plausible real
distribution". It is real. 2026/27 ships **two of each team chip**, with
`bboost` and `3xc` both at `start_event: 1` (wildcard and freehit start at
GW2) — read directly from the archived bootstrap-static body, and independently
re-confirmed. The 94% is a selection effect: the rate falls monotonically with
rank (91% in the top 100 → 81% at 1001–2000) because the cohort is selected on
GW1 score and a bench boost adds points. Confirmed four ways, including
`history.chips` agreeing with the picks payload 40/40.
**The lesson: that claim was reasoning from priors about an older season's
rules. Read the data before calling something implausible.**

**2026-08-27 — this prompt's Stage 0 acceptance criterion "fact_manager_
transfer is non-empty" was unachievable when written.** It did not account for
the season being one gameweek old: GW1 admits no transfers at all, and GW2's
are not public until its deadline. The criterion should have been "the crawl
reaches and completes the transfer stage", which is what the fix and its tests
actually establish. Corrected in the prompt's Definition of Done.

**2026-08-27 — pre-existing failure outside this work's scope:**
`tests/audit/test_static_leakage_audit.py` fails with 211 unreviewed findings
across `fpl_edge/theses/`, `cli/main.py` and others. Zero are in
`fpl_edge/ingest/content/` or any file touched here. Flagged so a future
session does not mistake it for damage from this build. NOT fixed here.

## Adversarial passes: what was attacked, what survived (append-only)

### 2026-08-27 — Pass 1, fabrication + leakage lens. STAGE 0 REFUTED.

Six findings, four of them refuting stated guarantees. **Stage 0 was NOT
marked DONE on the strength of the repairs; this pass sent it back.** Per D.2
that is the protocol working, not a setback.

**SURVIVED the attack (verified safe, with the attack described):**
- *Post-deadline facts are invisible.* Five purpose-built probes: a
  `fact_manager_pick`, a `fact_manager_transfer` and a `dim_manager` row each
  stamped deadline+6h were invisible to `sem_manager_picks` /
  `sem_manager_transfers` / `sem_manager_cohort` at the deadline and visible
  only at the later instant. `resolve_cohorts()` agreed with the SQL at both
  instants. Entries holding picks with no manager row land in `unclassified`
  rather than being folded into `elite`.
- *Cohort assignment is deterministic.* `sem_manager_cohort(now())` returns
  exactly one row per tracked entry (1,978 elite + 3,581 top1k = 5,559 =
  `count(distinct entry_id)`). The tie-break is an aggregate over a set with
  no ORDER BY / DISTINCT ON / window, re-run byte-identical at threads=1, 2
  and 8, and is monotone in `as_of` so it cannot flip-flop.
- *The ownership panel's denominator.* It reads only `sem_elite_ownership`,
  and its formatters return `None` rather than 0.0 for a missing value, so it
  cannot report a percentage of a denominator it did not use.
- *A suspected fabricated rank was investigated and cleared.* 597 entries
  share `source='top1k:2026-27:gw1:rank2147'`; within one `as_of` those 597
  have exactly one distinct `overall_rank` and one distinct `total_points` —
  genuine FPL tied ranking after one gameweek, copied verbatim.

**REFUTED — being fixed before Stage 0 can close:**
1. **[FIXED, commit `13358f0`] (severe, fabrication) 20 invented creator
   identities live in three MCP tools.** `fpl_mcp/tools/expert_tools.py` ships an unverified `EXPERTS`
   map; the engine's own copy at `roster.py:87-96` documents all 20 IDs as
   stale, and B9 gated them in the crawl — but the toolbelt has no gate.
   `get_manager_history("Holly Shand")` prints Caleb Stevens's ranks as fact.
   The single worst violation of the no-fabrication invariant found so far.
2. **[FIXED, commit `13358f0`] (fabrication) The B4 "degrade honestly" fix was
   applied to one function and not its sibling in the same file.** `get_expert_teams_summary` still
   prints `£0.0m` for an unresolved player — verbatim the bug the neighbouring
   docstring claims to have fixed — and still silently drops unresolvable
   players from an ownership cross-tab. This is the follow-up already noted in
   this ledger as "out of scope"; the audit shows out-of-scope was the wrong
   call.
3. **[FIXED, commit `13358f0`] (fabrication, minor)** `team_tools.py` defaults a missing multiplier to
   1 (started). The repo's own standard is the opposite: `observed.py`
   refuses the squad, commenting "a missing one is a hole in the crawl, not a
   zero".
4. **[FIXED, commit `b732d8d`] (LEAK) Creator weights are not point-in-time.**
   `fpl_mcp/tools/content_tools.py:_weights()` takes today's `creator_score`
   with no `as_of` filter, while the same tools correctly filter the *claims*
   through `claims_visible_at(moment)`. Claims from the past, weighted by the
   future, in a payload that echoes `as_of` back and reads as PIT. Currently
   masked because every weight is 0.0 — it fires the moment one is not.
5. **[FIXED, commit `b732d8d`] (auditability) The settlement rewrite's own argument fails three ways.**
   `resolved_utc` is restamped on every touched row including unchanged ones,
   destroying the only pointer to the state that produced a verdict;
   `dim_player.position` is an undocumented fourth input that selects the
   benchmark bucket and is re-ingested daily; and `OutcomeWrite.revised` is
   printed to stdout, never persisted, so two verdicts flipping in opposite
   directions leaves the append-only aggregate byte-identical. Latent today
   (no restatements, no reclassifications yet) but real.
6. **(minor) `string_agg(DISTINCT source, '|')` in `sem_manager_cohort` has no
   ORDER BY** — 379 of 5,559 rows return a different provenance string at
   threads=8 vs threads=1. This is the exact trap `warehouse.py:173-175`
   documents and fixed for `Snapshot.table`, reintroduced in a new macro.
7. **(minor) A stated fact went stale within 15 minutes.** `views.sql` and
   `cohorts.py:62` both say "17 of them in the live warehouse"; a crawl that
   ran before the commit made it 37.

**8. NOT A BUG BUT A METHODOLOGICAL PROBLEM — needs a decision in Stage B.**
`sem_manager_cohort`'s any-row rule is PIT-clean (monotone, pure function of
rows at or before `t`) but it *launders a hindsight selection*. Every
`fact_manager_pick.as_of` is the GW1 deadline; the `top1k` rows in
`dim_manager` have `as_of` 3–6 days LATER. So `sem_elite_ownership(now())`
reports "what the top-1k owned in GW1" for a cohort selected **because of
their GW1 result**. It also contradicts the crawl's own design note at
`top1k.py:205-207`, which says cohort membership is itself point-in-time
("this week's top-1k is not last week's") — the macro discards the per-gw rank
in the source string and makes membership a cumulative union.
**This matters most for Stage D**, whose "panel EO as a top-1k proxy" and
"elite differential" tests would be measuring a survivorship-selected cohort.
Carried into Stage B as a blocking design item: cohort membership should be
per-gameweek, and the rank is already in the source string to do it with.



_(pending — stage 0 not yet complete)_
