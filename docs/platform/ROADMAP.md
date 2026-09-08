# i-test platform — what works, what's next

Written 2026-08-20, last checked against the warehouse and the code on
2026-09-08. Everything under "Shipped" was observed working and is committed;
everything under "Next" is honestly not done. Row counts are real reads from
the warehouse, not estimates, and carry the date they were read.

---

## Shipped (MVP — usable today)

### The spine
| Piece | State | How to see it |
|---|---|---|
| **Platform API** | 5 panel scripts, guarded query, inbox, chat route | `uv run fpl platform serve` → `localhost:8321` |
| **Web UI** | one self-contained page, zero build step | open `localhost:8321` |
| **Deadline DAG** | deployed under launchd, 10-min ticks | `make dag-status` |
| **Telegram bot** | deployed, question router + idea inbox + link analysis | text @fplpradhannbot |
| **Nightly settlement** | deployed (post_gw) | `data/warehouse/jobs/` |

Panels are the **only** data path to the UI: each is a registered Python script
with params/result JSON Schemas, executed against `Warehouse.read_copy()`, and
every response carries provenance (script, repo sha, generated_at). The frontend
has no SQL surface. A panel with no data renders its own honest reason, never a
plausible-looking number.

### Data in the warehouse (real counts, 2026-08-20)
| Table | Rows | Notes |
|---|---:|---|
| `fact_player_fixture` | 113,260 | 4 seasons; scoring map reproduces `total_points` exactly on all of them |
| `fact_odds` | 115,025 | football-data history + the-odds-api live |
| `fact_projection` | **52,141** | 5 free providers, normalised schema |
| `projection_normalized` | 52,141 | one row per (provider, code, gw) |
| `fact_predicted_lineup` | 1,476 | Rotowire predicted XIs (xMins proxy) |
| `fact_odds_derived` | 1,720 | anytime→xG-share, clean-sheet, team-lambda priors |
| `content_claim` | 144 | creator claims, player-resolved |
| `fact_manager_season` | 12,854 | elite-manager skill panel |
| `projection_weight` | **0** | correct: no track record exists until GW1 resolves |

Re-read 2026-09-08, after three gameweeks settled:

| Table | Rows | Notes |
|---|---:|---|
| `content_item` | 890 | 505 descriptions, 234 articles, 151 transcripts |
| `content_claim` | 2,545 | 306 of the 890 items analysed; the rest are a budgeted backlog |
| `fact_manager_gw` | 23,799 managers | 3,409 with picks read; the top-1k crawl grows the tail nightly |
| `fact_manager_pick` | 62,790 | |
| `fact_player_match_stats` | 6,229 | third-party per-match reads, now write-on-change |
| `projection_weight` | 18 | three fits, `thru-gw1` to `thru-gw3`; earned, not assumed |
| `fact_projection_score` | 177 | per-provider MAE and RMSE against the all-provider mean |
| `dim_event.avg_entry_score` | 3 gameweeks | FPL's own field average: 50, 81, 51 |

**Projection providers integrated** (the thesis: copy, never invent):

| Provider | Rows | Players | GWs |
|---|---:|---:|---|
| fplform | 28,416 | 592 | 1–8 |
| gh_blueladd | 16,884 | 469 | 1–6 |
| gh_fplbench | 3,522 | 587 | 1 |
| fpl_ep (FPL's own `ep_next`) | 2,975 | 595 | 1 |
| premierinjuries | 344 | 86 | 1 |

### The deadline DAG (live)
Event-relative, computed from `dim_event.deadline_utc` — never cron guesses.
Firing rows are claimed *before* a task runs, so restarts and overlapping ticks
cannot double-send; outcomes distinguish `quiet` / `skipped_stale` / `no_source`
/ `error` rather than collapsing into "success". Triggers are pure arithmetic;
the LLM may only polish copy after a fire, and is off by default under launchd.

Next-due for GW1 (deadline 2026-08-21T17:30Z):
- presser/projection refresh — 2026-08-20T11:30Z
- price radar — 2026-08-21T01:00Z
- final solve delivery — 2026-08-21T13:30Z
- lineup captaincy check — 2026-08-21T16:00Z (records `no_source`; no lineup feed yet)

### Decision theory (derived, simulated, committed)
`docs/platform/rank_objectives.md` + six result CSVs. The load-bearing results:
- Sufficient statistic is **(D, τ)** — deficit vs the running top-10k pace, and
  weeks remaining. Effective variance is SD(my score − pace increment), **not**
  own-score SD: a template co-moves with the bar (own SD ~15/wk → effective ~3).
- Gamble iff `D + mτ < 0`; the myopic boundary is linear, `D* ≈ −1.06·τ` at
  baseline calibration.
- **Adaptive-vs-static posture is worth 9–16pp of P(top-10k); look-ahead beyond
  the weekly rule adds ≤0.1pp.** The switching rule is the whole prize.
- Captaincy: `score = μ + θ(1−2·share)σ²` — the variance credit flips sign at 50%
  field share.
- Hits in rank terms: `g* = 4 + L(S′−S)/S`. Behind by 30, a **−3.0 point** hit is
  rank-positive; ahead by 30 the same move needs +11.
- Chip timing is dominated by *cohort* chip usage raising the bar, not by your own
  chip — waiting wins in every simulated cell.

### Research committed
`argus_architecture.md` (650 lines, path:line evidence), `solver_state_of_art.md`
(the public SOTA MIP formulation, its exact settings semantics, and where it
stops), `projection_providers.md`, `odds_derivation.md`, `field_model.md`.

---

## Next (in priority order)

### 1. Rank-aware solver — WIRED (2026-08-24)
`uv run fpl solve` runs the horizon in both objectives against the live
warehouse and persists the plan + forecast artefacts the weekly report reads.
Run live for GW2: the objectives genuinely disagree (5/15 squad players, the
captain flips), with the evidence provenance printed. `fpl weekly --validate`
supplies the F1 paired simulator so the shortlist carries ΔP(top-10k) with
paired SEs. Evidence assembly lives in `fpl_edge/rank/assemble.py`.
Remaining, honestly:
- the deficit is an assumption until a top-10k pace series exists (the command
  says so and takes `--deficit`); captaincy shares are zero without the EO feed
- Σ-from-paired-simulator-draws estimator into `RankState` (still open)
- state-dependent risk (master prompt Phase 2.3) is NOT wired: `fpl solve`
  uses the stylised balanced archetype

### 2. Projection ensemble — SHIPPED (2026-09-07)
`score_projections` runs nightly in `post_gw`, scoring every provider against
settled actuals and refitting inverse-MSE weights. Three fits exist
(`thru-gw1`, `thru-gw2`, `thru-gw3`) and `sem_projection_consensus_weighted`
serves the blend beside the equal-weight consensus. The Projections tab shows
both and defaults to equal weights, which is the honest default while the
track record is three gameweeks deep. `premierinjuries` still earns 0 with
`n_obs` 0, which is the oracle rule working, not a gap.
Remaining: the solver reads the equal-weight consensus, not the earned blend.
That is a deliberate hold until the weights have more than three gameweeks
behind them.

### 3. Discipline layer
Presser-day gating, banked-transfer valuation (the solver's telescoping FT value
is in; the *policy* wrapper is not), knee-jerk detection against underlying
stats, every hit judged against `g*`, and process-over-results phrasing in every
delivered recommendation.

### 4. Calibration loop
Weekly job scoring every projection source, creator, and idea against actuals
(per-position RMSE for xPts, start-rate accuracy for xMins, hit-rate for claims),
feeding `SourceWeight` and the ensemble. The claim/idea half already exists; the
projection half is new.

### 5. Field model completion
`fpl_edge/models/field/` has the samplers and the EO-vs-inclusion separation.
Pending: the top-1k sampler run **after GW1 locks** (picks are only public
post-deadline — pre-GW1 the honest answer is labelled ownership marginals), and
cohort captaincy/chip rates measured from the crawl.

### 6. Fixture difficulty from our own ratings — SHIPPED (2026-09-07)
`fixture_ticker` is deleted, not retired. `fixture_ratings.parquet` is written
by the scheduled `fixture_ratings_refit` task and read by the Fixtures board,
which colours a fixed-domain diverging scale with a published unit, states its
clip count, and shows the market beside the model rather than blended into it.
The calibration is disclosed on the page: over six gameweeks the best-minus-
worst schedule is worth 2.8 to 6.4 points to an attacker and 4.6 to 6.0 to a
defender.

### 7. UI depth — SHIPPED, and rebuilt twice since (2026-09-08)
Nine tabs, each with its own panel set: Dashboard, Planner (solver and grid in
one tab), Projections, EliteFPL, Creators, Fixtures, Pipelines, Chat, Account.
Multi-source projection comparison with source selection and measured accuracy,
the effective-ownership and template board, creator consensus with backtested
report cards, and the chat pane are all in. The Dashboard states where the
season stands against FPL's published field average, and refuses to render a
solver plan that was solved against a squad the manager no longer holds.
Remaining: a watchlist with triggers, and a team-news feed.

### 8. ASR for podcasts — SHIPPED (2026-09-07)
`content_transcribe` runs nightly under a time budget, `audio_retention` sweeps
the cache behind it, and 151 of the 890 content items now carry a real
transcript rather than a description. Remaining: the press-conference source
inventory, which is item 9's neighbour.

### 9. Confirmed lineups
No source ingested. The T-90m task exists and honestly records `no_source`; a
test proves the wiring wakes up the moment a feed lands. This is the single
highest-latency edge still missing.

---

### 0. Correctness fixes shipped 2026-08-24 (see AUDIT_2026-08-20.md)
Historical availability is no longer fabricated at read time (NULL status =
unknown; backtests must opt into optimism in writing). `read_copy` owns and
deletes its temp copies (462 orphans, 5.4GB, cleaned). `dim_event` is
backfilled for 2022-26 so `Snapshot.deadline()` answers for every season.
Report sections cannot vanish silently. The four UI bugs that discarded real
data are fixed and the bundle has contract tests. `fact_odds_derived` has a
reader (`market_watch` panel), core-schema DDL, and a store-registered PIT key.
A chip-funded recommendation now declares the chip it plays.

## Known gaps and honest caveats

- **`projection_weight` is three gameweeks deep, which is not a track record.**
  It is earned rather than assumed, and the weights it produces are real, but
  three observations is far too few to act on. The consensus the solver reads
  stays equal-weighted for that reason, and the Projections tab hides measured
  accuracy behind a fold rather than leading with it.
- **Squad panel needs a token refresh.** `fpl myteam auth` (or letting the
  refresh run) repopulates it; until then it renders its honest empty state.
  Access tokens last 8h, the refresh token ~6 months.
- **Two upstream feeds are down or partial, and the pages say so.**
  football-data.co.uk returns 503 on every path, so the derived odds markets
  are 19 days stale and every market cell renders "stale" or "unpriced" with
  its reason rather than a colour. The content analysis queue is budget-capped
  by design, so 116 items published in the last 10 days are fetched but not
  yet analysed.
- **Rank constants are calibration-dependent.** `D* ≈ −1.06τ` comes from a
  specific edge/variance calibration; re-derive per season rather than treating
  it as a law of nature.
- **Chip advice is horizon-limited.** The 5-GW solve is structurally biased
  toward spending chips early because it cannot see later doubles. Season-long
  chip planning is item 3's neighbour and is not built.
