# Panel build ledger

Prompt: `docs/platform/CREATOR_ELITE_PROMPT.md`

| Stage | Status | Commit | Notes for a fresh session |
|---|---|---|---|
| 0 repairs | IN PROGRESS | c789453, 63c9c0b, 5433f0b, 43c2837 | All 12 defects repaired and the full unit suite is green — but adversarial pass 1 REFUTED four stated guarantees, so this is NOT done. Fixes for findings 1-7 in flight; finding 8 (cohort hindsight selection) is carried into Stage B as a blocking design item. Re-run the adversarial pass before closing. |
| A registry + identity | TODO | | |
| B elite history + panel EO | TODO | | **BLOCKING DESIGN ITEM inherited from adversarial pass 1 finding 8:** cohort membership must become per-gameweek. Today's `sem_manager_cohort` selects the top-1k from crawls that ran 3-6 days AFTER the GW1 deadline, so EO for GW1 is reported for a cohort chosen because of its GW1 result. Stage D's proxy and differential tests are invalid until this is fixed. The per-gw rank is already in `dim_manager.source`. |
| C corpus + ideas + search | TODO | | |
| D backtest | TODO | | Do not start before Stage B's cohort fix: tests 3 and 4 would measure a survivorship-selected cohort. |
| E chat reach | TODO | | |
| F two tabs | TODO | | |
| G verdict | TODO | | |

## NEEDS OWNER (anything blocked on a human decision)

**The `elite` cohort contains 49 of the owner's own mini-league opponents.**
Live composition of the 311-manager elite cohort: 250 `elite_list`, **49
`mini_league`**, 12 `winner`, 8 `elite_named`. So ~16% of the "elite" EO
denominator is people the owner happens to play against, not a selected elite.
Nothing discloses this. Options: exclude `mini_league` from the elite cohort,
give it its own cohort, or keep and disclose. This is a judgement about what
"elite" should mean here, so it is the owner's call rather than mine.

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

**Still open from this pass:** the EO guard rail. 15 of 20 mutations survived
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
