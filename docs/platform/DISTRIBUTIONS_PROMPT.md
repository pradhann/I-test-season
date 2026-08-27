# Session prompt: Distributions — surface the simulator, point it at rank

Written 2026-08-27. Paste the block below as the opening message of a fresh
session. It is self-contained; nothing else is needed. Style and invariants
follow `docs/platform/SECTION_PROMPTS.md`.

**Why this session exists.** The engine already treats a projection as one
number in most user-facing places (xPoints view, weekly report deltas), but
the objective is P(top-1k finish), which is a property of the *distribution*
of scores and their joint behaviour with the field. The modelling layer for
this already exists and is good — the gap is that it is not wired to the
platform, not fed the real crawled field, not cached, and its per-player
distributions have never been scored against settled reality. This session
closes those four gaps. It builds almost nothing model-wise.

---

```
You are working on the FPL edge engine at ~/Documents/Github/i-test-season,
branch main. This session is ONLY about distributions: wiring the existing
Monte Carlo simulator into the platform, feeding it the real crawled field,
caching its runs, scoring its distributions, and surfacing all of it in the
xPoints UI and the weekly decision flow. Do not redesign the simulator, the
solver, or the chat runtime.

== INVARIANTS (not up for renegotiation) ==

- No fabricated data, ever. A distribution is only shown with its provenance
  (model, n_sims, seed, inputs) beside it. A panel with no data says why.
- Point-in-time correctness. Warehouse.snapshot_at(t) / sem_*(t) macros are
  the only sanctioned reads of mutable facts. LeakageError is a feature.
- Identity is stable player `code`, never element_id. Money in integer
  tenths. DuckDB single-writer XOR readers; long reads via
  Warehouse.read_copy() (context-managed).
- Panel scripts are the ONLY UI data path (registry in
  fpl_edge/platform/registry.py; JSON-schema params/results; every result
  carries provenance; the empty case is {empty, reason} and the real schema
  must not overlap it).
- Determinism: every simulation is seeded and its run is reproducible
  bit-for-bit from (inputs_hash, seed). Never call the clock inside scoring.
- Small commits. Never force-push. Never push without being asked.
- A test that has never failed is not a test: for each meaningful new test,
  break the code, watch it fail, restore, and say so in the commit message.

== READ FIRST (in this order; ~30 min) ==

1. docs/models/simulator.md — the design doc for fpl_edge/sim/. The
   correlation argument in §1–2 is the reason this whole session exists.
2. fpl_edge/sim/ — engine.py (rest-of-season MC loop), field.py (rival
   SQUADS not rival scores; shared points draws), rank.py, utility.py,
   live.py (wires real models: DecomposedPointsModel + OwnershipForecaster),
   calibration.py (field anchors vs 4 historical seasons), squad.py.
3. fpl_edge/models/points/model.py — DecomposedPointsModel.simulate() →
   PointsSample (codes, points (n_players, n_sims), minutes). Scoreline →
   minutes → shares → BPS/bonus → scoring map. Correlated by construction.
4. fpl_edge/models/contracts.py — PointsSample, ModelCard.
5. fpl_edge/myteam/forecast.py — the seam to the optimiser; read its
   docstring on why collapsing to means is only legal under EXPECTED_POINTS,
   and where RankUtilityProvider plugs in.
6. docs/platform/rank_objectives.md §0 and §8 — the (deficit, weeks-left)
   sufficient statistic and the recommended architecture. Your surfacing
   work must express THESE quantities, not generic stats.
7. fpl_edge/eval/projection_scoring.py — the provider calibration harness
   (last pre-deadline fetch vs settled actuals; fact_projection_score with
   scopes overall/pos:*/own_gt5/own_gt20; backfill-safe). Your distribution
   scorer mirrors this pattern exactly.
8. fpl_edge/platform/scripts/projections.py + web/dist/js/views/xpoints.js —
   the panel/view you are extending (source chips, matrix, drawer
   showDetail, renderAccuracy with scope toggle). web/dist is SOURCE
   (zero-build ES modules), committed, no bundler.
9. fpl_edge/store/views.sql + docs/platform/semantic_layer.md —
   sem_elite_ownership(t): per-cohort (top1k | elite) own% / captain% / EO%
   from the 1,500-squad crawl. sem_manager_picks(t): every tracked
   manager's actual locked squad. These are the REAL field.
10. fpl_edge/jobs/post_gw.py — settle_results → projection_scoring →
    ingest_projections chain you are appending to.

== CURRENT STATE (verify each yourself before building on it) ==

- fpl_edge/sim/live.py composes the real points model but the field comes
  from OwnershipForecaster (a model), NOT from the crawled squads — the
  top-10k crawl and sem_manager_picks/sem_elite_ownership landed after the
  simulator was written. Verify: grep sim/ for sem_manager_picks — expect
  no hits.
- No warehouse caching of simulation runs. Verify: grep -r "fact_sim"
  fpl_edge — expect nothing. (fact_solve caching for the MILP is a separate
  planned deploy-session item; do not build that here, but keep the same
  inputs-hash idea so the two rhyme.)
- No panel serves distributions. Verify: the projection_table panel result
  has consensus/matrix/accuracy but no quantiles or P(haul).
- fact_projection_score holds MAE/RMSE per provider/scope (GW1 settled;
  more GWs settle weekly). It scores POINT projections only; nothing scores
  distribution calibration (PIT/CRPS) yet.
- The sim's own tests (tests/unit/test_sim_*.py) pass today — run them
  first; if any fail, fix or flag before proceeding.
- Entry 4490171 is the user's team; GW deadlines are in fact_event /
  sem_fixtures. The current squad comes from the squad panel, not from a
  hardcoded list.

== THE WORK — five stages, in order, each with its own commit(s) ==

──────────────────────────────────────────────────────────────────────────
STAGE 1 — The real field: crawled squads replace sampled squads
──────────────────────────────────────────────────────────────────────────
Goal: when P(rank) is computed for THIS season's next gameweek, the rival
squads are the ~1,500 actually-crawled top squads (sem_manager_picks at the
last deadline, cohort top1k), not draws from an ownership model.

Build fpl_edge/sim/observed_field.py:
- ObservedField(snapshot, season, gw, cohort="top1k") loads each crawled
  manager's 15, multipliers, and armband via sem_manager_picks(t) with t =
  that gw's deadline; expose the same interface engine.py consumes from
  field.py (score every rival against a shared PointsSample).
- Weighting: 1,500 squads stand in for the top ~10k-ish field. Do NOT
  fabricate expansion (no synthetic perturbation of real squads). Score
  the crawled cohort as-is and report rank-within-cohort plus the mapping
  assumption explicitly in the result (e.g. "percentile within crawled
  top-1500 of overall top-10k sample"). The engine's model-sampled field
  remains the tool for rest-of-season/whole-6M questions; the observed
  field answers "this week, against the managers I am actually racing".
- Rivals' future transfers are unknown for gw+1: for the NEXT gameweek use
  their last locked squad (state that assumption in provenance). Do not
  model their transfers in this session.
- Free transfers/hits for rivals: ignore (assume their locked squad),
  stated in provenance.

Tests (tests/unit/test_observed_field.py):
- A crawled manager whose squad contains the top scorer in a draw ranks
  above one who doesn't (construct a 2-manager fixture with a hand-built
  PointsSample).
- PIT: a squad crawled AFTER the queried deadline is invisible.
- Break-check: remove the deadline filter, watch the PIT test fail,
  restore.

──────────────────────────────────────────────────────────────────────────
STAGE 2 — One simulation service, cached by inputs hash
──────────────────────────────────────────────────────────────────────────
Goal: one entry point the panel, chat and report all call; a run is
computed once per (inputs, seed) and read forever after.

Build fpl_edge/sim/service.py:
- run_gw_sim(season, gw, *, n_sims=5000 (pick a value that keeps the
  end-to-end run under ~60 s on this machine; measure, don't guess),
  seed=0, cohort="top1k") →
  GwSimResult with:
    per-player: code, mean, sd, q5/q25/q50/q75/q95, p_blank (≤2 pts),
    p_return (≥5), p_haul (≥10), p60 (P(≥60 min)), p_appear
    my-squad (entry 4490171 read via the squad panel's source): total
    distribution quantiles, P(beat cohort median), P(top-X-percentile of
    cohort) for X in {1, 5, 10, 25}, captain-vs-alternatives table
    (for each current-squad player with mean ≥ 4: P(captaining them beats
    captaining the current captain))
    provenance: model cards, n_sims, seed, inputs_hash, snapshot as_of,
    cohort size actually loaded.
- inputs_hash = sha256 over: season, gw, n_sims, seed, cohort, the max
  as_of of every warehouse table the run read (list them explicitly), and
  the git describe of the code. Conservative over-invalidation is fine;
  silent staleness is not.
- Persistence: new table fact_sim_run (inputs_hash PK, season, gw, params
  JSON, result JSON, computed_at, as_of) in store/schema.sql, written under
  the single-writer lock. Reads go through read_copy. Cache hit = return
  stored JSON, never rerun. Add a `force=True` escape.
- CLI: `uv run python -m fpl_edge.sim.service --season 2026-27 --gw 3`
  prints the summary and says HIT or COMPUTED.

Tests (tests/unit/test_sim_service.py):
- Same inputs twice → second call is a cache hit (assert no recompute:
  monkeypatch DecomposedPointsModel.simulate to raise on second call).
- Changing seed or any warehouse max-as_of changes the hash.
- Quantiles are monotone; p_blank + p_return bracket sanity on a
  hand-built PointsSample.
- Break-check: drop as_of from the hash, watch the staleness test fail,
  restore.

──────────────────────────────────────────────────────────────────────────
STAGE 3 — Distribution calibration in post_gw (PIT + CRPS)
──────────────────────────────────────────────────────────────────────────
Goal: the same discipline projection point-estimates get, applied to the
in-house distributions. Without this stage the drawer's fan chart is an
unfalsifiable decoration — this stage is what makes Stage 4 honest.

Build fpl_edge/eval/distribution_scoring.py, mirroring
projection_scoring.py's structure (same backfill-safety pattern, same
fact-table idiom):
- For each settled gw: reconstruct the LAST PRE-DEADLINE simulation — call
  run_gw_sim with snapshot_at(deadline) (the cache makes reruns free; if
  no cached pre-deadline run exists, computing one from the deadline
  snapshot is legitimate PIT, not leakage — say so in a comment).
- Metrics, written to fact_distribution_score (provider='sim_v1', season,
  gw, scope, metric, value, baseline, n_obs):
    crps      — empirical CRPS from the draws vs actual points
    pit_ks    — Kolmogorov–Smirnov distance of PIT values from uniform
                (use randomized PIT for the discrete point masses)
    cov50/cov90 — fraction of actuals inside the central 50%/90% intervals
                  (targets 0.50/0.90; store the fraction, the target is
                  implied by the metric name)
    brier_haul — Brier score of p_haul vs (actual ≥ 10)
  Scopes: overall, own_gt5, own_gt20 (same definitions as
  projection_scoring — reuse its ownership-join helper by extracting it,
  don't copy-paste it).
  Baseline for crps: a naive normal around the all-provider consensus mean
  with the pooled residual sd from fact_projection_score — i.e. "what a
  point projection plus a generic error bar would give you". The sim must
  beat this to have earned the fan chart.
- Wire into fpl_edge/jobs/post_gw.py after projection_scoring.
- CLI backfill over all settled gws, skipping (provider, gw, scope) rows
  that already exist.

Tests (tests/unit/test_distribution_scoring.py):
- A hand-built PointsSample that exactly matches a known discrete truth
  scores CRPS ≈ 0 and uniform-ish PIT; a badly-shifted one scores worse
  than baseline.
- Backfill idempotence: second run appends nothing.
- Break-check: score with the POST-deadline snapshot, watch a
  deliberately-planted leakage test fail, restore.

──────────────────────────────────────────────────────────────────────────
STAGE 4 — Surfacing: panel + xPoints UI + weekly report
──────────────────────────────────────────────────────────────────────────
Panel (fpl_edge/platform/scripts/): a new `gw_sim` panel script wrapping
run_gw_sim (params: gw, optional force; result mirrors GwSimResult; empty
case explains what is missing, e.g. "no crawled cohort for this season
yet"). Extend the projection_table panel per-row payload with the sim's
q5/q50/q95/p_haul/p_blank when a cached run exists for that gw (LEFT-join
semantics: absence of a sim never hides a provider row).

xPoints UI (web/dist/js/views/xpoints.js — keep its existing structure and
design language; zero-build, no libraries):
- Matrix rows: under each player's Σ cell, nothing changes by default; add
  a "distribution" column toggle next to the existing "measured accuracy"
  toggle which, when on, replaces the per-GW consensus number with a
  compact inline range "q50 (q5–q95)" and colours p_haul ≥ 0.15 cells.
- Drawer (showDetail): a fan strip per selected GW — a horizontal bar from
  q5 to q95 with a marker at q50 and ticks at q25/q75, pure CSS (no
  canvas), with p_blank / p_return / p_haul printed as three labelled
  chips. Below it, the captain table when the player is in the squad.
  Settled GWs overlay the actual as a dot on the strip — the honest "did
  the fan contain reality" view.
- Accuracy panel: the existing scope-toggled table gains a second section
  "In-house distribution (sim_v1)" showing CRPS vs baseline, cov50, cov90,
  brier_haul for the selected scope, with the same one-line explainer
  style (e.g. "cov90 0.84 — the 90% band caught 84% of outcomes; under
  0.90 means overconfident"). Empty until Stage 3 has scored a gw; say so.
- Provenance line stays visible: "sim_v1 · 5,000 draws · seed 0 · computed
  <when> · cohort top1k (1,500 squads)".

Weekly decision flow (~/Documents/Github/FPL-MCP/tools/edge_tools.py,
weekly_decision_report): add a "rank view" block per candidate decision:
Δmean AND ΔP(beat cohort median) AND ΔP(top-10%-of-cohort), computed by
pairing draws (same PointsSample for both branches — never two independent
runs; the paired difference is the whole point). Captaincy section becomes
P(A beats B) with the EO-weighted field captain named.

Tests: extend tests/unit/test_projection_panel.py + test_web_contract.py
for the new panel fields (allow-empty oneOf discipline); a paired-draws
test proving ΔP uses common random numbers (two branches over the same
seed have zero variance on shared players).

Browser verification (required, not optional): restart the fpl-platform
preview, open #xpoints, toggle distributions on, open a drawer, screenshot;
check read_console_messages for errors. Fix what you see. The fan strip
must be legible in BOTH themes (the app has a Theme toggle — check dark).

──────────────────────────────────────────────────────────────────────────
STAGE 5 — Report the verdict honestly
──────────────────────────────────────────────────────────────────────────
Run the full backfill (Stages 2–3) over every settled gw. Then write
docs/platform/DISTRIBUTIONS_VERDICT.md: one page stating (a) CRPS vs the
naive baseline per scope, (b) coverage numbers, (c) whether the fan charts
are currently trustworthy or decorative, (d) with N settled gameweeks the
evidence is thin and firms up weekly via post_gw — quote N. If the sim
LOSES to the naive baseline, say so in the UI explainer too ("distribution
is wider/narrower than reality so far") — do not quietly ship a losing
model as decoration. Update docs/platform/semantic_layer.md if any new
macro was added, docs/data_lineage.md for fact_sim_run and
fact_distribution_score, and the ROADMAP.

== RUNTIME / OPERATIONAL NOTES ==

- Respect the DuckDB lock discipline: the sim service WRITES its cache row,
  so it must not hold a write connection while the panel server holds
  readers — write via the same Warehouse single-writer path every ingest
  uses, and keep the write transaction tiny (result JSON insert only).
- Panels must never block the UI for a fresh 60 s simulation: the gw_sim
  panel returns {empty, reason: "computing"} after kicking a background
  compute IF a run is missing, OR (simpler, acceptable) computes eagerly
  in post_gw/nightly for the next gw so the UI always hits cache. Choose
  one, implement it fully, and say which in the commit.
- n_sims: measure the wall-clock at 1k/5k/10k and pick the largest that
  keeps nightly compute under a minute per gw; record the choice and the
  measurement in the verdict doc.
- The user's entry is 4490171. GW deadlines come from the warehouse.
- Preview server: use the Browser pane's preview_start with name
  "fpl-platform" (already in .claude/launch.json). Never run servers via
  Bash.

== DEFINITION OF DONE ==

1. `uv run pytest tests/unit -q` green, including every new test, with
   break-watch-restore performed and noted per meaningful test.
2. `uv run python -m fpl_edge.sim.service --season 2026-27 --gw <next>`
   twice: COMPUTED then HIT, under the runtime budget.
3. Backfill of fact_distribution_score over all settled gws; numbers
   visible in the xPoints accuracy panel under both themes.
4. Screenshot-verified: matrix distribution toggle, drawer fan strip with
   settled-GW actual dot, accuracy section, weekly report rank view.
5. DISTRIBUTIONS_VERDICT.md written with the honest comparison vs the
   naive baseline.
6. Small commits along the way; nothing pushed.

Work through the stages strictly in order — each later stage consumes the
earlier one, and Stage 3 is what licenses Stage 4's visuals. If a stage
reveals that a prerequisite claim in CURRENT STATE was wrong, stop, state
what you found, adjust the plan in this file, and continue.
```
