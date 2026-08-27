# Panel build ledger

Prompt: `docs/platform/CREATOR_ELITE_PROMPT.md`

| Stage | Status | Commit | Notes for a fresh session |
|---|---|---|---|
| 0 repairs | IN PROGRESS | | Started 2026-08-27. Audited state re-verified before starting (see below). MCP fold done first because B4 lives in files that move. |
| A registry + identity | TODO | | |
| B elite history + panel EO | TODO | | |
| C corpus + ideas + search | TODO | | |
| D backtest | TODO | | |
| E chat reach | TODO | | |
| F two tabs | TODO | | |
| G verdict | TODO | | |

## NEEDS OWNER (anything blocked on a human decision)

_(nothing yet)_

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
Left alone as out of scope; worth fixing when Stage E touches that file.
Second follow-up: `rivals/roster.py` keeps a copy of `expert_tools.EXPERTS` as
a literal, justified by the two repos being separate. That justification died
with the fold, and the two mappings can now drift.

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

_(pending — stage 0 not yet complete)_
