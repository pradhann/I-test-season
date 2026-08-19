# Ownership, captaincy and effective ownership

The engine maximises `P(rank < threshold)`, not expected points. Rank is decided
by my score against the field's, and the field's mean score is

```
E[field score] = sum over players of  EO_p * points_p
```

so the effective-ownership vector is not a report, it is half the objective.
Without it "this player is a differential" is an unfalsifiable sentence.

---

## 1. What effective ownership is

EO is the **mean FPL multiplier the field applies to a player**:

| manager's relationship to player p | multiplier |
| --- | --- |
| does not own | 0 |
| owns, benches | 0 |
| starts | 1 |
| starts and captains | 2 |
| starts and triple-captains | 3 |

Collecting terms, with `start_share` the share of the whole field starting p,
`captain_share` the share captaining p (**including** Triple Captain users), and
`triple_captain_share` the subset using the chip:

```
EO_p = start_share_p + captain_share_p + triple_captain_share_p
```

Worked examples, each of which is a test in `tests/unit/test_ownership_eo.py`:

| case | start | captain | TC | EO |
| --- | --- | --- | --- | --- |
| universally owned, never captained | 1.00 | 0.00 | 0.00 | **1.00** |
| universally owned and captained | 1.00 | 1.00 | 0.00 | **2.00** |
| universal triple captain | 1.00 | 1.00 | 1.00 | **3.00** |
| 70% owned, 92% of owners start, 45% captain | 0.644 | 0.450 | 0.00 | **1.094** |
| 40% start, 20% captain, none TC | 0.40 | 0.20 | 0.00 | **0.60** |
| same but all captains use TC | 0.40 | 0.20 | 0.20 | **0.80** |

### The four sign errors this module exists to prevent

1. **`EO = ownership - captaincy`.** Captaincy is additive, always. A player the
   field captains is *harder* to gain on, not easier.
2. **`EO = ownership * (1 + captaincy)`.** Captaincy share is a share of the
   whole field, not of the player's owners, so it is added, not multiplied.
3. **Triple captain added twice.** A TC contributes 3 = 1 started + 1 captained
   + **1** extra. The extra term is added once more, not twice more.
4. **Using `selected_by_percent` as start share.** Ownership counts the four
   bench players, who score nothing. `start_share <= ownership`, always.

Two structural identities are enforced rather than hoped for:

* `sum_p ownership_p == 15` — every squad holds 15 players. Observed in the live
  bootstrap as `sum(selected_by_percent) == 1499.5`.
* `sum_p captain_share_p == 1` and `sum_p start_share_p == 11` — one armband and
  eleven starters per manager, so `sum_p EO_p == 12`.

`rank_edge(my_multiplier, EO, points) = sum((mult - EO) * points)` is the
quantity the optimiser actually cares about. Owning a 100%-EO player at
multiplier 1 has a rank edge of exactly zero however many points they score.

---

## 2. Measured results

All ownership numbers are **out-of-sample, leave-one-season-out**, against real
realised ownership. MAE is in percentage points of ownership.

### 2.1 In-season, one deadline ahead

Five seasons (2021-22 .. 2025-26), 78,258 player-transitions.

| | MAE (pp) | vs model |
| --- | --- | --- |
| **model** | **0.3869** | — |
| persistence (ownership unchanged) | 0.4818 | **-19.7%** |
| naive transfer momentum | 0.5211 | **-25.8%** |

Per held-out season — the model wins all five:

| season | n | model | persistence | momentum |
| --- | --- | --- | --- | --- |
| 2021-22 | 15,021 | 0.4882 | 0.6296 | 0.6807 |
| 2022-23 | 14,740 | 0.3861 | 0.5202 | 0.5656 |
| 2023-24 | 14,941 | 0.4351 | 0.5104 | 0.5526 |
| 2024-25 | 16,240 | 0.3231 | 0.3975 | 0.4326 |
| 2025-26 | 17,316 | 0.3181 | 0.3751 | 0.4006 |

Calibration of the predictive interval, pooled: **50.4% / 79.9% / 94.8%** against
nominal 50 / 80 / 95.

### 2.1b The same thing under a strict walk-forward

Leave-one-season-out lets the 2023-24 fold learn from 2024-25, which had not
happened. `fpl_edge/models/ownership/evaluate.py` runs the protocol an operator
would actually have had: for the transition out of gameweek `g` in season `s`,
fit on every transition from a season before `s` plus every transition in `s`
that ends at or before `g`. The gameweek being predicted is never in the fit.
Cold start folds by season, because GW1 has no earlier gameweek of its own.

**173 in-season folds, 76,103 player-transitions:**

| | MAE (pp) | vs model |
| --- | --- | --- |
| **model** | **0.3950** | — |
| persistence | 0.4801 | **-17.7%** |
| naive transfer momentum | 0.5192 | **-23.9%** |

Slightly worse than the leave-one-season-out 0.3869, which is the expected
direction: the early folds are fitted on far less data.

**A second negative result, and it changes how the number should be read.** The
model beats persistence on MAE by 17.7%, but it is closer than persistence on
only **43.3% of individual rows**. The two are not in conflict: the model earns
its whole margin on the minority of players whose ownership moves a lot, and
pays a small toll on the majority that barely move — the dilution and
mean-reversion terms nudge a static 0.4%-owned player that persistence leaves
alone. For the rank objective that trade is the right way round, because the
players who move are the ones with enough effective ownership to matter. But
"beats persistence" should not be read as "is closer more often than not",
because it is not.

**Cold start, 5 folds, 3,072 player-snapshots:**

| horizon | n | model | persistence | momentum |
| --- | --- | --- | --- | --- |
| all | 3,072 | 0.5489 | 0.5736 (-4.3%) | 0.5571 (-1.5%) |
| near deadline (T-1.0d, T-1.4d) | 1,210 | 0.1997 | 0.2380 (-16.1%) | **0.1963 (+1.7%)** |
| far (T-11.2d, T-14.4d) | 1,862 | 0.7759 | 0.7916 (-2.0%) | 0.7916 (-2.0%) |

Row-by-row, the model is closer than persistence on 58.4% of cold-start rows
overall, 63.8% far from the deadline and exactly 50.0% near it.

**A negative result, stated plainly.** At the near horizon — the only one that
matters operationally, because that is when decisions get made — the fitted
model is *worse than naive drift extrapolation* by 1.7% of MAE, on 1,210 rows
across two folds. Leave-one-season-out reported the model ahead here; under the
strict protocol it is not. Two folds is not enough to call this either way, and
that is the finding: **the near-horizon cold-start block is not demonstrably
better than extrapolating the observed drift**, and anyone quoting it should
quote the drift baseline next to it. The far-horizon and in-season wins are real
and are not affected.

`2022-23` cannot be scored at all — it is the first season in the panel and
there is no earlier pre-deadline snapshot anywhere. Its 1,574 rows are reported
as untestable rather than dropped.

### 2.1c Cold-start coefficient signs, audited

`evaluate.coefficient_signs` renders every fitted cold-start coefficient against
the sign its mechanism dictates, and `main()` exits non-zero if any escaped:

| block | level | concentration | projection ≥0 | flagged ≤0 | drift ≥0 |
| --- | --- | --- | --- | --- | --- |
| near | -0.0084 | +0.0289 | +0.0035 | **-0.1556** | +1.9562 |
| far | -0.0148 | -0.0120 | +0.0066 | **-0.1556** | +1.6644 |
| near, no drift | +0.0006 | +0.0164 | +0.0056 | **-0.2036** | 0 |
| far, no drift | -0.0145 | -0.0085 | +0.0059 | **-0.2036** | 0 |

The constraint is load-bearing and `tests/unit/test_ownership_walk_forward.py`
proves it every run: an unconstrained least-squares fit of the near block on its
two snapshots puts the availability coefficient at **+0.239**, condition number
110. If a future fixture change makes the unconstrained fit well-behaved, that
test fails and asks for the constraint to be re-derived rather than deleted.

### 2.2 GW1 cold start

Against real pre-deadline snapshots recovered from the upstream dataset's git
history, four seasons, 4,646 player-snapshots:

| | MAE (pp) |
| --- | --- |
| **model** | **0.5936** |
| persistence | 0.6368 |
| drift momentum | 0.6162 |

Per snapshot — the model wins all eight:

| season | horizon | n | model | persistence | momentum |
| --- | --- | --- | --- | --- | --- |
| 2024-25 | T-1.01d | 594 | 0.1250 | 0.1885 | 0.1513 |
| 2023-24 | T-1.39d | 616 | 0.2354 | 0.2858 | 0.2398 |
| 2022-23 | T-4.05d | 536 | 0.2921 | 0.4097 | 0.3256 |
| 2023-24 | T-11.17d | 610 | 0.7387 | 0.7607 | — |
| 2025-26 | T-14.36d | 668 | 0.8346 | 0.8499 | — |
| 2024-25 | T-14.38d | 584 | 0.7472 | 0.7571 | — |
| 2022-23 | T-20.04d | 519 | 0.8932 | 0.9296 | — |
| 2022-23 | T-20.05d | 519 | 0.9135 | 0.9526 | — |

Momentum is unavailable at the long horizons because no earlier snapshot exists
in that season; there it falls back to persistence.

Restricted to the horizon the engine is actually at today (T-1 to T-4 days,
three snapshots, 1,746 players):

| | all players | players at or above 1% ownership |
| --- | --- | --- |
| **model** | **0.2152** | **0.5757** |
| persistence | 0.2907 | 0.7868 |
| drift momentum | 0.2360 | — |

Calibration, pooled over all horizons: **50.2% / 79.5% / 94.9%** against nominal
50 / 80 / 95.

### 2.3 Captaincy share — simulated, and labelled as such

**No public per-player captaincy-share series exists.** The API publishes only
`events[].most_captained`, one player id per gameweek, and the archived
community datasets carry ownership and transfers but no armband. So the
captaincy number is measured against a simulated field whose managers use a
*different* decision rule from the model's (see `simulate.py`), 4,000 managers,
three seasons of real ownership and price vectors, 1,746 players scored:

| | MAE (pp) |
| --- | --- |
| model, GW1 case (no previous gameweek to lean on) | 0.0758 |
| model, in-season case (blended with last gameweek) | **0.0571** |
| persistence of last gameweek's shares | 0.0597 |
| proportional to ownership | 0.2397 |

Read this honestly. Against the naive "the field captains whoever it owns"
baseline the model is **68% better**. Against simply repeating last gameweek's
captaincy it is **27% worse** on its own, and only 4.3% better once blended with
that observation — captaincy is extremely sticky. **At GW1 the sticky
observation does not exist**, so the standalone 0.0758 figure is the one that
applies right now, and it is a simulated number, not an empirical one.

### 2.4 Top-10k ownership — a prior, not a measurement

Verified against the live API on 2026-08-18:

* `/api/leagues-classic/314/standings/` returns `standings.results == []`. The
  overall league has no ordering because no gameweek has been scored.
* `/api/entry/{id}/event/1/picks/` returns HTTP 404 for every entry. Picks are
  private until the deadline passes.

So elite ownership **cannot be measured before the first deadline**, and the
`eo_top10k` column is produced by a prior (`elite.elite_tilt`) with
`eo_top10k_is_prior = True` on every row.

---

## 3. Top-10k sampling methodology

Once the deadline passes, `elite.ElitePicksSampler` measures it directly:

1. Page `/leagues-classic/314/standings/?page_standings=k`, 50 entries per page,
   in rank order. 200 top managers costs 4 requests.
2. Fetch `/entry/{id}/event/{gw}/picks/` for each, 0.35s apart, archived through
   the `Fetcher` so every squad the estimate rests on is reproducible from bytes
   on disk. 200 requests.
3. Aggregate into ownership, start share, captaincy and triple-captain share of
   the sampled cohort.
4. Quote `EliteSample.standard_error(share)`: a 200-squad sample puts +/- 3.5
   percentage points around a 50% share. Elite ownership figures without that
   band are not usable for the comparisons people make with them.

A **pre-deadline fallback** exists and was exercised: sample entry ids and
classify them by past-season finishing rank from `/entry/{id}/`. Measured on 300
uniformly random entries drawn from the 5,905,855 that currently exist
(entry ids are dense from 1 to that bound, established by bisection):

| | count | share |
| --- | --- | --- |
| entries with any past-season history | 250 | 83.3% |
| best past rank at or inside 10,000 | 4 | 1.3% |
| best past rank at or inside 100,000 | 24 | 8.0% |
| best past rank at or inside 500,000 | 63 | 21.0% |

At a 1.3% hit rate, building a 200-manager elite frame by random sampling needs
roughly 15,000 requests. That is not a polite way to use somebody else's free
API, and it is why the standings endpoint is the method and this is only the
fallback for identifying strong managers whose picks can be sampled the moment
they become public.

---

## 4. How the model works

### 4.1 In-season

Ownership moves by an accounting identity plus behaviour:

```
o_p(g+1) = w * o_p(g) + (1 - w) * q_p + netflow_p / N(g+1)
```

with `w = N(g)/N(g+1)`. The dilution term is the part persistence cannot see and
it is **large early**. Median field growth, measured across five real seasons:

| transition | w = N(g)/N(g+1) | incumbent share lost |
| --- | --- | --- |
| GW1 -> GW2 | 0.887 | **11.3%** |
| GW2 -> GW3 | 0.951 | 4.9% |
| GW3 -> GW4 | 0.971 | 2.9% |
| GW20+ | 0.998 | 0.2% |

An 70%-owned player loses about 8 percentage points of ownership between GW1 and
GW2 without a single manager selling them. The spread across seasons is tight
(0.880 to 0.912), so this is structure, not noise.

Fitted coefficients (`params.json`, all five seasons):

| feature | coefficient | reading |
| --- | --- | --- |
| dilution `(w-1)*own` | 0.639 | 64% of the mechanical dilution comes through |
| flow `netflow/N` | 0.433 | 43% of last window's net flow repeats |
| form `pts*sqrt(own)` | 0.0055 | the crowd chases last week's points |
| price move `dvalue*own` | 0.067 | price rises pull further buying |
| overshoot `own - own_prev` | -0.189 | 19% of the last move reverses |
| level `own` | -0.0757 | reversion toward a flatter field |

### 4.2 GW1 cold start

Before the first deadline there is **no transfer flow at all**:
`transfers_in_event` is identically zero for all 592 players, and
`cost_change_start` is zero too because prices do not move before the season
starts (`rules.prices.no_change_before_season`). What is left:

* the ownership level, which is already formed — 5.9M teams registered;
* the **drift rate**, measured by polling the ownership series;
* availability flags;
* the registration count, still climbing;
* expected points, if the points model supplies them (the `expected_points`
  seam; the warehouse schema does not carry `ep_next`).

Measured structure across four real seasons, all four agreeing on the sign:

* **far from the deadline (11-20 days) ownership disperses** — correlation of
  drift with ownership level is -0.09 to -0.15;
* **close to it (1-4 days) the template consolidates** — the same correlation is
  +0.12 to +0.20.

That sign flip is why the cold-start block has two coefficient sets with a blend
across a 3-day knot, rather than one linear model.

Drift magnitude follows a clean empirical law. Regressing log(persistence MAE)
on log(days-to-deadline) over the eight real pre-deadline snapshots gives

```
drift ~ days ** 0.505      R-squared 0.98
```

Concretely, the persistence error — which is what "how much does ownership still
move" means — measured on real snapshots:

| horizon | all players | players at or above 1% |
| --- | --- | --- |
| T-1.0d | 0.19 pp | 0.51 pp |
| T-1.4d | 0.29 pp | 0.88 pp |
| T-4.1d | 0.41 pp | 0.99 pp |
| T-11.2d | 0.76 pp | 2.31 pp |
| T-14.4d | 0.76-0.85 pp | 2.09-2.58 pp |
| T-20.0d | 0.93-0.95 pp | 2.02-2.09 pp |

i.e. ownership diffuses like a square-root-of-time random walk. The structural
features are scaled by `days ** 0.5` so a block fitted at a one-day horizon is
not applied unchanged at three days.

Two guards worth knowing about:

* **Drift needs a long enough window.** The API quantises ownership to 0.1%, so a
  20-minute poll window measures one rounding step; extrapolating it three days
  forward manufactures a 16-point ownership swing out of nothing. The drift term
  is used only when the measurement window is at least half the forecast
  horizon, and the model refits without it otherwise.
* **Mass leaks to players who do not exist yet.** New players are added to the
  game between now and the deadline (the live list holds 592 against 567 two
  weeks ago), so currently-known players' shares are shrunk by a fitted
  0.079%/day. Without it the forecast is systematically high on everybody.

### 4.3 Captaincy

A multinomial logit over owned players:

```
c_p  proportional to  own_p * exp(kappa * appeal_p) * available_p
```

with `appeal` built from price (£m), a position offset (GKP -6.0, DEF -3.0,
MID 0, FWD +0.3) and home advantage (+0.45) — everything a Snapshot carries. The
result is projected onto `{c >= 0, sum c = 1, c <= start_share}`, because a
captain must be in the XI and every manager names exactly one.

`kappa = 0.35` is a **stated prior, not a fitted value**. The simulated field
implies 0.40-0.46; hand-reasoning against a top-heavy but non-degenerate
captaincy distribution suggests 0.25-0.35. Sensitivity, on the current live
ownership vector:

| kappa | top captain share |
| --- | --- |
| 0.20 | 0.57 |
| 0.28 | 0.69 |
| 0.35 | 0.78 |
| 0.45 | 0.86 |

Under-concentrating captaincy is the *dangerous* direction: it understates the
template captain's EO and therefore overstates the value of owning them.
`calibrate_kappa` replaces the prior with a solved value from a single observed
captaincy share, which the API supplies via `events[].most_captained` once a
gameweek is under way.

---

## 5. Fixtures and provenance

Committed under `tests/fixtures/ownership/`, derived from
[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League);
`manifest.json` records the exact derivation and the pinned git revisions.

| file | contents |
| --- | --- |
| `inseason_panel.parquet` | 78,258 transitions, 2021-22 .. 2025-26 |
| `coldstart_pairs.parquet` | 4,646 pre-deadline snapshot / realised GW1 pairs |
| `field_size.parquet` | field size by season and gameweek |

Derivation notes that matter:

* Field size comes from the simplex identity, `N = sum(selected)/15`, not from an
  external constant.
* Double gameweeks put a player in two rows; ownership and transfers are taken
  once, points summed.
* `element_type == 5` (Manager) existed only in 2024-25 and is dropped, never
  coerced to a position.
* The in-season panel keeps rows where the player was at or above **0.1%**
  ownership at some point in the transition. That drops 37% of rows while
  retaining **99.7%** of all ownership movement, and the rows it drops have
  effective ownership indistinguishable from zero.

The cold-start pairs are the interesting ones. The finished-season files cannot
tell you what ownership looked like *before* a deadline — but the dataset's own
git history can, because `players_raw.csv` was committed repeatedly through each
pre-season. Reading those revisions recovers real snapshots at T-1.0, T-1.4,
T-4.1, T-11.2, T-14.4 and T-20.0 days, which is exactly the observation a
cold-start model gets.

GW1 deadlines are derived, not remembered: first GW1 kickoff minus 90 minutes,
per `rules.deadlines.offset_before_first_kickoff_minutes`.

---

## 6. Known limitations

* **Top-10k is a prior until the first deadline passes.** `lead = 1.6` and
  `concentration = 1.12` are declared, not estimated. The sampler that will
  replace them is built and tested.
* **Captaincy is validated only structurally.** See 2.3.
* **The cold-start blocks are fitted on eight snapshots.** That is all the
  pre-deadline ownership data that exists anywhere. Three of the five
  coefficients therefore carry sign constraints from the mechanism, and the
  availability coefficient is shared across horizons rather than estimated
  twice. Without those, the unconstrained fit put the injury coefficient at
  +0.24 and forecast a knee injury as a 24% ownership *rise*.
* **`ep_next` is not in the warehouse schema**, so the cold-start projection
  feature contributes nothing in production unless the points model injects
  expected points through the `expected_points` seam.
* **Field size is not in the schema either.** In-season flow is a share of the
  field, so `OwnershipForecaster` refuses to run the in-season path without an
  explicit `field_size` rather than guessing the denominator.
* **The realised-ownership target carries a lag.** The upstream per-gameweek
  files are scraped a few days after each gameweek, so the "realised" ownership
  includes a little of the next window's transfer flow. Persistence and momentum
  are scored against exactly the same target, so the comparison is fair, but the
  absolute MAEs are mildly optimistic against a true at-the-deadline target.
* **Reading an earlier Snapshot needs the escape hatch.** Measuring a drift rate
  needs two observations, and there is no sanctioned way to rewind a Snapshot.
  A `Snapshot.rewind(delta)` on the warehouse would remove the one
  `escape_hatch_unfiltered` call in this package.

## 7. Reproducing

```bash
uv run python -m fpl_edge.models.ownership.fit       # refits, rewrites params.json and measured.json
uv run python -m fpl_edge.models.ownership.evaluate  # strict walk-forward + sign audit
uv run pytest tests/unit/test_ownership_eo.py tests/unit/test_ownership_forecast.py \
              tests/unit/test_ownership_backtest.py tests/unit/test_ownership_walk_forward.py -q
```

All offline and deterministic. `evaluate` exits non-zero if any cold-start
coefficient escapes its domain sign constraint.

## 8. Live status at GW1 2026-27

Recorded from the warehouse and the live API on 2026-08-18, T-2.76d from the
17:30Z deadline on 2026-08-21:

| | value |
| --- | --- |
| forecast path taken | `cold_start` — **not** `cold_start+drift` |
| ownership polls in the warehouse | 2, thirty minutes apart |
| drift term | refused: a 0.02-day window against a 2.76-day horizon is 1.3% of the required ratio |
| `total_players` | 5,896,644 at rule capture, 5,950,733 on the live API the same day |
| top-10k ownership | prior; `eo_top10k_is_prior = True` on all 509 rows |
| overall league standings | `results: []` |
| `entry/4490171/event/1/picks/` | HTTP 404 |

The drift refusal is the guard in `MIN_DRIFT_WINDOW_RATIO` working as designed,
and it costs real accuracy: on the historical snapshots the drift term is worth
more than every structural feature combined. **The single highest-value action
before the deadline is to poll `bootstrap-static` again, at least ~33 hours
before it, so a usable drift window exists.** Everything else in this package is
already as good as the data allows.

Field size is not in the warehouse schema, so `extrapolate_total_players` cannot
be run automatically; the two observations above exist only in this document.
Registrations grew 54,089 in one day, so the deadline field is likely ~6.0M
rather than 5.90M — about a 1.7% tightening of every rank threshold.
