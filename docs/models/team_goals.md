# Team goal model

A bivariate Dixon-Coles goal model, a market-implied baseline, and the
walk-forward evaluation that decides between them.

Code: `fpl_edge/models/team_goals/`. Reproduce every number below with

```
uv run python -m fpl_edge.models.team_goals.evaluate            # synthetic, has odds
uv run python -m fpl_edge.models.team_goals.evaluate --real-eval  # real warehouse, no odds
uv run pytest tests/unit/ -q -k team_goals
```

## Headline

**On real data the Dixon-Coles model beats both naive baselines by a wide,
statistically clear margin. It has never been compared to a real market, because
there are no odds in the warehouse. On synthetic data where a market does exist,
it loses to it.** Both statements are measurements; the second is the one worth
acting on.

| | log loss | RPS (1X2) | RPS (goal diff) | clean-sheet Brier |
| --- | --- | --- | --- | --- |
| home advantage only | 1.07346 | 0.23262 | 0.06406 | 0.17854 |
| last-season table | 1.03778 | 0.21977 | 0.06125 | 0.17814 |
| **Dixon-Coles** | **0.98184** | **0.20086** | **0.05768** | **0.17022** |
| market-implied | *no odds in the warehouse* | | | |

Real warehouse, walk-forward over 2023-24, 2024-25 and 2025-26, refit at every
one of the 114 gameweek deadlines, 1140 fixtures. Clean-sheet base rate 0.23202.
Full table: `team_goals_metrics_real.csv`.

Paired bootstrap over fixtures, 2000 resamples (`team_goals_deltas_real.csv`):

| vs Dixon-Coles | delta log loss | 95% CI | delta Brier | 95% CI |
| --- | --- | --- | --- | --- |
| home advantage only | +0.09162 | [+0.06817, +0.11342] | +0.00832 | [+0.00430, +0.01258] |
| last-season table | +0.05594 | [+0.04093, +0.07219] | +0.00792 | [+0.00523, +0.01078] |
| no promoted prior | +0.00194 | [-0.00219, +0.00578] | -0.00022 | [-0.00096, +0.00050] |

Positive means worse than Dixon-Coles. Both baselines are beaten decisively.
The promoted prior is **not** distinguishable from noise on real data; see below.

## The measured loss to the market

`fact_odds` is empty. The market baseline therefore cannot be measured on real
data at all, and no claim is made about it. To measure it anyway, the synthetic
league (below) includes a bookmaker. Over 1067 commonly-covered fixtures:

| | log loss | RPS (1X2) | RPS (goal diff) | clean-sheet Brier |
| --- | --- | --- | --- | --- |
| home advantage only | 1.06525 | 0.22635 | 0.06033 | 0.20760 |
| last-season table | 1.05763 | 0.22492 | 0.05961 | 0.20008 |
| Dixon-Coles, no promoted prior | 1.02979 | 0.21509 | 0.05778 | 0.19384 |
| Dixon-Coles | 1.02862 | 0.21457 | 0.05769 | 0.19302 |
| blend (½ market, ½ model) | 1.01199 | 0.20915 | 0.05650 | 0.19034 |
| **market-implied** | **1.00647** | **0.20722** | **0.05617** | **0.19023** |

Paired bootstrap vs the market: Dixon-Coles is **+0.02214 nats worse**, 95% CI
[+0.01004, +0.03532], and worse on clean-sheet Brier by +0.00278, CI [+0.00014,
+0.00530]. The model does not beat the market. That is the finding.

The half-market blend closes almost all of it: +0.00552 against the market, CI
[-0.00084, +0.01220] — statistically indistinguishable from the market while
also covering the 6.4% of fixtures nobody priced. If real odds land, the blend
is the model to ship, and its weight should then be tuned out of sample (it is
currently pinned at 0.5 and has not been).

**Read the synthetic market result with the caveat attached.** The synthetic
bookmaker prices off the *true* goal rates with log-normal noise of 0.08 and a
proportional overround, and the de-vigger inverts a proportional overround
exactly. Its margin over the fit is therefore a design parameter of the
simulator, not evidence about real bookmakers. What the synthetic result does
establish is that the machinery works and that a sharp market is not beaten by
this model — which is the prior anyone should have held anyway.

## The model

For a match between home club *h* and away club *a*:

```
log lambda_home = c + home_adv + attack[h] + defence[a]
log lambda_away = c + attack[a] + defence[h]
```

`attack` positive means "scores more than average"; `defence` positive means
"concedes more than average", i.e. it is a leakiness parameter. That sign
convention makes the promoted prior read the way you would say it aloud.

Goals are independent Poisson multiplied by the Dixon-Coles `tau` correction on
the four low-score cells — independent Poisson under-predicts 0-0 and 1-1 and
over-predicts 1-0 and 0-1 — and `rho` is estimated jointly, not pinned. Fitted
on the real warehouse at the 2026-27 GW1 deadline: `home_adv = 0.177`,
`rho = -0.081`, effective sample size 508 weighted matches out of 1520.

Each match is weighted `exp(-ln2 * age_days / 400)`. The half-life was selected
on the synthetic *validation* season (2022-23), outside the evaluation window,
so the reported numbers are not tuned on themselves. A sensitivity sweep on the
real warehouse puts the optimum at 240-400 days with 1e-4 nats between them —
the objective is flat and the exact value does not matter much
(`team_goals_half_life.csv`).

The fit is MAP rather than plain MLE: each club carries a Gaussian prior on its
attack and defence. Established clubs get mean 0 with sd 0.50, which the data
overwhelms within a season. Promoted clubs get the prior below. Plain MLE has
nothing to say about a club with no matches, and a plain ridge would shrink it
to the league average, which is wrong in a known direction.

The gradient is analytic; `test_analytic_gradient_matches_finite_differences`
checks it against finite differences, because a 40x speedup bought with a silent
gradient bug is not a speedup.

### Everything comes out of the score matrix

`score_matrix(fixture_id)` returns the joint `P(home = i, away = j)` on a 9x9
grid, renormalised to sum to exactly 1. `p_clean_sheet`, `exp_goals_for` and
`exp_goals_against` in the contract frame are read *out of that matrix*, never
computed alongside it, so they cannot disagree with the distribution the
simulator samples from. The home side's clean-sheet probability is column 0 (the
away side failed to score). `test_score_matrix_agrees_with_predict` asserts the
equality to 1e-12 rather than trusting it.

Truncating at 8 goals loses ~6e-4 of the mass at a heavy 2.2 v 1.9; the matrix
is renormalised, so this appears as a proportional reallocation, well inside the
sampling error of anything downstream.

## The promoted-club prior

Three clubs come up every August with no rows in the warehouse. The prior for
them is estimated, not assumed. For every promotion observable in the training
window, the club's first-season goal rates are measured against that season's
league average:

```
attack_offset  = log(goals scored per match / league goals per team-match)
defence_offset = log(goals conceded per match / league goals per team-match)
```

These are method-of-moments estimates of the same additive log-rate offsets the
model parameterises. Pooling them gives a mean and a spread; the spread becomes
the prior sd, so the model says "weaker than average, and here is how unsure we
are" rather than picking a point. Where a prior-division strength covariate is
available (promotion route: 1 champions, 2 runner-up, 3 play-off winner) the
pooled mean is replaced by a regression on it. Without it the regression
collapses to its intercept and records `covariate = "none"`.

**Fitted on the real warehouse at the 2026-27 GW1 deadline** (5 observable
promotions across 2023-24 to 2025-26):

```
attack  -0.354  (sd 0.163)      defence  +0.276  (sd 0.223)      covariate: none
```

A promoted club is therefore rated to score `exp(-0.354) = 0.70x` and concede
`exp(0.276) = 1.32x` the league average — a long way from the league average a
ridge would have given it. Per season the fitted values were `-0.380 / +0.355`
(2024-25) and `-0.391 / +0.364` (2025-26).

Three things are refused rather than guessed:

* With fewer than three observable promotions, `fit_promoted_prior` **raises**.
  It does not fall back to the league average. A documented assumed prior
  (-0.18 / +0.20) exists but is opt-in via `allow_fallback=True` and is tagged
  `source="assumed_fallback"` so a card built on it cannot pretend otherwise.
  The 2023-24 evaluation season is the only place it is used.
* A club with zero matches lands *exactly* on its prior mean — that is the MAP
  guarantee when the likelihood contributes nothing, and it is asserted in
  `test_a_club_with_no_matches_lands_exactly_on_its_prior`.
* "Promoted" means "no prior top-flight match on record", not "won the
  Championship". A club with history from three seasons ago is rated from its
  data.

**Known bias, uncorrected:** promoted clubs never play themselves, so their
opponent pool is marginally stronger than an established club's, biasing the
prior slightly too pessimistic. Second order (2 of 38 matches).

**Missing input:** no second-tier data exists in the warehouse, so the promotion
route covariate is unavailable on the real path and every promoted club receives
the same rating. Supplying `(season, team_code, route)` to `DixonColesModel`
turns the regression on; on synthetic data it recovers the generating slope
(0.07 per route step) to within 0.10.

### Is the prior worth anything? Partly.

Measured by ablation (`use_promoted_prior=False` reproduces the league-average
fallback exactly):

* **Real data, all fixtures:** +0.00194 log loss without it, CI [-0.00219,
  +0.00578]. **Not significant.** Clean-sheet Brier -0.00022, CI [-0.00096,
  +0.00050]. **Not significant, and marginally favours dropping it.**
* **Real data, promoted fixtures only** (184): with the prior 0.91035 log loss
  vs 0.92270 without; clean-sheet Brier 0.15325 vs 0.15182. Mixed — better on
  outcomes, slightly worse on clean sheets.
* **Synthetic data, all fixtures:** +0.00117 log loss without it, CI [-0.00366,
  +0.00593] — again not significant — but clean-sheet Brier +0.00082, CI
  [+0.00010, +0.00155], **significant**.

The honest summary: the prior's measured value is small, concentrated in
clean-sheet calibration, and only statistically clear on synthetic data where it
is correctly specified. It is kept because the alternative is a fallback that is
wrong in a known direction, because it is the only thing that gives a promoted
club a defensible GW1 rating at all, and because it never measurably hurts — not
because the ablation proved it earns its keep on real data. With 5 promotion
events observed, it cannot.

## Consuming odds

This package does not fetch odds. `fpl_edge/models/team_goals/odds.py` defines
the consumer side only:

| provider | path | notes |
| --- | --- | --- |
| `SnapshotOddsProvider` | **REAL** | reads `fact_odds` through a `Snapshot` |
| `FrameOddsProvider` | **OFFLINE** | same long schema from a committed CSV |
| `NullOddsProvider` | — | zero coverage, reported as such |

`odds_for(fixture_keys, as_of)` — `as_of` is mandatory on every implementation,
including the offline one. That is not decoration: the offline provider
originally ignored it, which would have let the backtest price fixtures with
quotes published after the deadline it was standing at, and made the market look
unbeatable for reasons having nothing to do with the market.
`test_offline_provider_hides_quotes_published_after_the_as_of` pins it.

Prices are de-vigged per bookmaker before averaging across books (averaging raw
prices mixes margin into the consensus). Two methods: `proportional` (default)
and `power` (solves `sum p^k = 1`, which handles favourite-longshot bias). Goal
rates are then recovered by least squares against a Dixon-Coles score matrix —
two unknowns against three or four constraints, so the residual is informative
and a price set no bivariate Poisson can produce is flagged rather than
absorbed. `rho` is not identified by these markets; it defaults to 0 and can be
borrowed from the fit, which matters for clean sheets specifically.

Fixture key convention: `"{season}:{fixture_id}"`.

## Evaluation protocol

For every gameweek of every evaluation season: snapshot at that gameweek's
deadline, refit every model on what is visible at that instant, predict that
gameweek. 114 refits per full run. Nothing is predicted twice and nothing is
predicted with knowledge of itself.

* Historical seasons have no `dim_event` rows, so their deadlines are
  reconstructed from the published fixture schedule using the verified rule
  `deadlines.offset_before_first_kickoff_minutes` (90). Only `gw` and
  `kickoff_utc` are read; the schedule is public months ahead.
* Every input arrives through `Snapshot`. `read_finished_matches` additionally
  asserts that no finished match has kickoff at or after the as-of instant and
  raises `LeakageError` if one does, because the SQL filter guarantees fact
  *visibility* ordering, not event-time ordering.
* Models are compared on the intersection of fixtures they all predicted, with
  coverage reported beside it. The market is never credited with predictions it
  did not make.
* `fixture_id` is reused across seasons in FPL, so the score-matrix cache is
  scoped to the season of the last `predict` call and cleared when it changes.

## Committed data

| file | what |
| --- | --- |
| `team_goals_metrics_real.csv` | real-warehouse walk-forward, all scopes |
| `team_goals_calibration_real.csv` | clean-sheet reliability, real data |
| `team_goals_deltas_real.csv` | paired bootstrap deltas, real data |
| `team_goals_metrics.csv` | synthetic walk-forward, all scopes |
| `team_goals_calibration.csv` | clean-sheet reliability, synthetic |
| `team_goals_deltas.csv` | paired bootstrap deltas, synthetic |
| `team_goals_half_life.csv` | decay half-life sweep on the validation season |

`tests/fixtures/team_goals/` holds the synthetic league (6 seasons, 2280
matches, 5335 odds rows, seed 20260818). It is regenerated deterministically by
`synthetic.generate_league`; two runs with the same seed produce identical CSVs.
The tests read it and never touch the network or the real warehouse.

## Clean-sheet calibration

`team_goals_calibration_real.csv`. Aggregate mean predicted 0.25449 against a
base rate of 0.23202, so the model is mildly over-generous with clean sheets
overall. Given clean sheets are worth 4 points to a defender, that is a bias
worth watching in the points model rather than a rounding error.

## What this model is not

* It has no team-news, lineup, injury or rest input. Those are precisely what a
  market prices and this model does not, and are the most likely explanation for
  the synthetic gap being smaller than a real one would be.
* It has no expected-goals input. `fact_player_fixture` carries `expected_goals`
  and `expected_goals_conceded`; a shot-quality-based attack estimate would
  converge faster than one built on realised goals and is the obvious next step.
* The blend weight is not tuned.
* Promoted clubs are all rated identically for want of second-tier data.

## GW1 2026-27, produced

At the GW1 deadline (2026-08-21T17:30:00Z), 1520 real matches visible, zero from
the current season. Two clubs have no top-flight history in the warehouse and
are rated from the prior: **Hull City** and **Coventry City**, both at
attack -0.354, defence +0.276. Ipswich Town is *not* among them — 2024-25 is in
the warehouse, so it is rated from its own data (attack -0.278, defence +0.330),
which is what the definition of "promoted" used here is supposed to do.

Sharpest ratings: Man City (attack +0.413), Arsenal (defence -0.565), Liverpool
(attack +0.347). The most one-sided GW1 fixture is Arsenal v Coventry at
2.82 - 0.51 expected goals, giving Arsenal a 0.599 clean-sheet probability
against Coventry's 0.059.
