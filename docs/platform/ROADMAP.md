# i-test platform — what works, what's next

Written 2026-08-20, branch `platform`. Everything under "Shipped" was observed
working and is committed; everything under "Next" is honestly not done. Row
counts are real reads from the warehouse, not estimates.

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

### 1. Rank-aware solver — finish and wire (highest value)
`fpl_edge/rank/` and the `RANK_MV` objective in `fpl_edge/opt/milp.py` are
substantially built (no-good-cut plan enumeration, locked/banned, chip-week
forcing all landed) but were mid-flight when the model quota hit. Remaining:
- verify `RANK_MV` end-to-end and run a real GW1 solve in both modes side by side
- Σ-from-paired-simulator-draws estimator wired into `RankState`
- the F1 paired-CRN validator attaching ΔP(top-10k) with paired SE to each plan
- `make solve` refresh so the T-4h delivery carries a fresh plan, not "no fresh solve"

### 2. Projection ensemble
Tables and providers exist; the blend does not. Needs: per-provider calibration
against GW1 actuals → `projection_weight` (currently 0 rows, correctly), then a
blended projection selectable per solver run. Until a track record exists the
user picks the source explicitly — the oracle rule (weight 0 without evidence)
already holds.

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

### 6. Fixture difficulty from our own ratings
`fixture_ticker` currently returns opponents and home/away only — no difficulty
— so the UI renders those cells neutral rather than inventing a colour. The
Dixon-Coles fit that would supply real difficulty takes ~1 minute, well past a
panel's 10s budget, so this needs a **cached ratings artefact** written by the
nightly job and read by the panel. That is the single cheapest upgrade to the
dashboard's usefulness.

### 7. UI depth
Current page renders every panel the API declares. Next: multi-source projection
comparison with source selection, elite/template EO panel, watchlist with
triggers, creator consensus with track records, team-news feed, and the chat pane
(the API route exists and answers through the deterministic router today).

### 8. ASR for podcasts
Benchmarking found MLX-Whisper ~5× faster than faster-whisper on this machine.
Pending: the transcription module, the nightly time-budgeted backfill, and the
press-conference source inventory.

### 9. Confirmed lineups
No source ingested. The T-90m task exists and honestly records `no_source`; a
test proves the wiring wakes up the moment a feed lands. This is the single
highest-latency edge still missing.

---

## Known gaps and honest caveats

- **`projection_weight` is empty and should be.** Nothing has a measured track
  record before GW1 resolves. Any blend before then is a guess wearing a number.
- **Squad panel needs a token refresh.** `fpl myteam auth` (or letting the
  refresh run) repopulates it; until then it renders its honest empty state.
  Access tokens last 8h, the refresh token ~6 months.
- **The UI was not visually verified in a browser.** It is served (HTTP 200),
  its JS parses, and every layout the API declares has a renderer — but a
  sign-in popup blocked browser automation at build time. Open
  `localhost:8321` and confirm.
- **Rank constants are calibration-dependent.** `D* ≈ −1.06τ` comes from a
  specific edge/variance calibration; re-derive per season rather than treating
  it as a law of nature.
- **Chip advice is horizon-limited.** The 5-GW solve is structurally biased
  toward spending chips early because it cannot see later doubles. Season-long
  chip planning is item 3's neighbour and is not built.
