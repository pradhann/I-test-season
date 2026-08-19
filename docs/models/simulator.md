# The simulator and rank utility

> Code: `fpl_edge/sim/`. Tests: `tests/unit/test_sim_*.py`.
> Reproduce every number below with `uv run python -m fpl_edge.sim.experiments`.

## 1. What this component is for

The objective is not "score the most points". It is **P(final rank < threshold)**,
which is a functional of the whole distribution of my score *and* of that
distribution's joint behaviour with the field's. Two consequences follow, and
they drive the entire design:

1. A model that returns a mean cannot serve the objective. Everything here
   consumes and emits distributions.
2. My score and the field's score are not independent. A simulator that draws
   them separately gets every differential and every captaincy call wrong, in a
   direction that systematically favours the template.

## 2. The correlation model

The naive approach — a distribution for me, a distribution for the field, compare
— fails because roughly 70% of managers own Haaland. If I own him too, his blank
costs me nothing in rank terms; if I don't, his haul costs me a great deal.
Independent sampling prices both events as if the field were unaffected.

The fix is structural, not a fitted correlation coefficient:

```
for each remaining gameweek g:
    points[g]  <- PointsModel.simulate(...)          # (n_players, n_sims)
    squads[g]  <- FieldModel.sample_squads(ownership[g])  # (n_rivals, 15)
    rival_totals += score(squads[g], points[g])      # (n_rivals, n_sims)
my_total = score(my_squad, points[g] for all g)      # (n_sims,)
```

**The same `points` array is consumed by my squad and by all 10,000 rival
squads.** `Cov(my score, field score)` is therefore not estimated at all; it
emerges from how many players my squad and a rival's squad have in common. It is
automatically correct for any squad, including ones nobody has proposed yet, and
it is correct for captaincy, which no scalar correlation parameter could capture.

`tests/unit/test_sim_correlation.py` asserts the mechanism rather than the
number: correlation must be positive; it must be *higher* for a squad built to
match ownership than for one built to avoid it; and permuting the rival totals
across simulations — which leaves every marginal distribution untouched and
destroys only the joint — must materially change P(top 10k).

### Sampling the field

A rival is a 15-man squad, not a score. Three properties matter.

**Fixed size with exact marginals.** Independent Bernoulli draws at the forecast
ownership give squads of random size and miss the budget-induced negative
dependence between premiums. `_madow` implements Madow systematic PPS sampling:
first-order inclusion probabilities are exactly the forecast ownership, and the
sample size is exactly 2/5/5/3 by position. Ownership that does not sum to those
counts is renormalised explicitly by `FieldModel.target_ownership`, and
`ownership_renormalisation` reports how far it had to move — a large value means
the ownership model and the squad rules disagree and someone should look.

**Stratification (`template_alignment`).** This was the surprise. Sampling
independently at the correct marginal ownership produces a field far *more*
diverse than the real one. Real managers read the same information and converge
on the same core, so a manager who owns one premium tends to own the others.
Ordering the systematic sample by expected points stratifies each rival's squad
across the quality distribution, which reproduces that clustering while leaving
every player's marginal ownership untouched.

**Persistent skill (`skill_tilt`) and persistent squads (`churn`).** If rivals
redrew independent squads every week, season totals would concentrate by the
central limit theorem and the top 10k would collapse toward the mean. Each rival
carries a latent skill, drawn from a left-skewed distribution with a
heavy-tailed minority, that persists all season and tilts their selection.
Selection randomness is also reused across gameweeks, so a rival's squad drifts
with ownership rather than being redrawn. A proportional-fitting step
(`_fit_log_alpha`) restores the ownership marginals after the tilt, so tilting
the field toward good players does not silently inflate their ownership.

The calibrated `skill_tilt` is **small**, and that is itself a finding: most of
the spread between managers is squad-selection luck, not persistent skill. A
field model with a large skill term puts the top 10k hundreds of points further
from the mean than it really is.

## 3. Ranking

Simulating 10,000 rivals against a field of 5.9 million means one simulated rival
is worth ~590 real managers, so counting rivals gives ranks quantised to 590 —
useless for P(top 100).

Conditional on one draw of player points, the spread of scores *across managers*
is squad-selection noise: a sum of eleven-ish exchangeable contributions, close
to Gaussian and very smooth. `rank_from_scores` therefore fits the first three
moments of the rival score distribution *within each simulation* and uses a
Cornish–Fisher survival function for the deep tail, blending linearly into the
empirical count between 8 and 40 rivals beating me. `RankDistribution.summary()`
flags with `extrapolated_top_*` which thresholds depend on that extrapolation:
top 100 and top 1k do, top 100k does not.

## 4. Rank utility: the exact functional form

```
U  =  P(R ≤ T)  +  w_s · P(R ≤ S)  −  λ · CVaR_α[ L(R) ]

L(r) = clip( log(r / T) / log(N / T),  0,  1 )
```

| symbol | meaning | source | default |
| --- | --- | --- | --- |
| `T` | primary target rank | `RankUtilityConfig.target_rank` | 10,000 |
| `S` | stretch rank | `RankUtilityConfig.stretch_rank` | 1,000 |
| `λ` | catastrophe aversion | `RankUtilityConfig.risk_lambda` | 0.35 |
| `N` | field size | `RankUtilityConfig.field_size`, else API `total_players` | 5,896,644 |
| `w_s` | stretch weight | `rank_utility(stretch_weight=…)` | 0.25 |
| `α` | catastrophe tail fraction | `rank_utility(cvar_alpha=…)` | 0.10 |

`CVaR_α` is the mean of the worst `⌈αn⌉` simulations. Each choice is a claim:

* **The first term is the stated objective, verbatim.** No proxy, no surrogate.
* **The stretch term buys lottery tickets without letting them dominate.**
  `w_s = 0.25` means top 1k is worth a quarter of top 10k *on top of* the top-10k
  credit it already earns.
* **The penalty is on log rank.** 400,000th versus 2,000,000th is a real
  difference in how badly a season went; 11,000th versus 12,000th is not. Linear
  rank would treat the second as a thousand times more important than it is.
* **The penalty is a CVaR, not a variance.** Variance punishes upside, which is
  self-defeating when the objective is a small exceedance probability. CVaR
  punishes only the left tail, which is what "catastrophic" means.
* **`U` is monotone in rank.** Improving any simulation's rank weakly increases
  every term. An optimizer will find any hole in its objective, and
  `test_sim_utility.py` asserts there isn't one.

The optimizer calls `make_objective(config)`, which returns a plain
`RankDistribution -> float`. It captures no simulator state, so the optimizer
cannot reach into the simulator and the simulator does not know what a squad
search looks like. `expected_points_objective()` is the baseline it must beat.

`w_s` and `α` are not on `RankUtilityConfig`; that contract is shared and owned
elsewhere. They are keyword arguments with the documented defaults above.

`SquadPlan` is the unit of decision: a base squad, per-gameweek overrides, hits,
and chips. A transfer plan is an override plus its points hit. The simulator
never needs to know what a transfer *is*.

## 5. Counterfactuals and common random numbers

The field does not depend on my decisions, so it is sampled and scored **once**
and every candidate is ranked against an identical field on identical draws.
`Counterfactual` exploits this: `se_delta_p_top` is a *paired* standard error.
That is not a micro-optimisation. The differences that decide real transfers are
a few tenths of a percentage point; resolving those unpaired would need
hundreds of thousands of simulations, and paired it takes a few thousand.

## 6. Validation

Two tiers, mirroring `fpl_edge/rules/registry.yaml`.

**Verified anchors** (`calibration.warehouse_anchors`) are recomputed from the
warehouse on every run, from `fact_player_fixture` and `fact_player_state` for
2022-23..2025-26. Nothing is typed in from memory. The important one is
**owned-15 points per gameweek**: the expected return of a squad drawn at the
field's own marginal ownership, which is exactly what `FieldModel` samples, and
which is directly measurable because the historical `selected_by_pct` is stored
at every deadline. It has been 51–55 points per gameweek in every one of the
last four seasons.

**Unverified anchors** (`calibration.PUBLISHED_LADDER`) are the published overall
rank thresholds. FPL exposes no historical rank-vs-points through any API this
project ingests, and the engine runs offline, so they cannot be recomputed. They
are stated as *gaps* rather than levels — the level moves whenever the scoring
rules change, as defensive contributions moved it in 2025-26, while the shape of
the ladder has been far more stable — and they carry an explicit tolerance. Every
conclusion that leans on them says so.

## 7. Known simplifications

* **Rival autosubs are estimated, not run per rival.** The full autosub engine
  runs exactly on a 256-rival subsample and the mean per-simulation credit is
  applied to the whole field. Running it on nobody biases my rank upward by about
  a point a gameweek — several hundred thousand rank places over a season.
  Running it on all 10,000 rivals costs ~40x for cross-rival autosub variance
  that is second order.
* **Rival chip usage is not modelled.** Wildcards, Free Hits, Bench Boosts and
  Triple Captains are absorbed into the heavy-tailed skill component rather than
  simulated explicitly.
* **Ownership is held at the deadline forecast for all future gameweeks.** The
  ownership team's model supplies one forecast per gameweek; the simulator
  consumes whatever it is given, so this improves when they do.
* **Price changes and their effect on future squad feasibility are ignored.**
* The stand-in points model in `synthetic.py` is not a forecast and its
  `ModelCard` says so. It exists so this package could be built and tested before
  the points model landed. Nothing in the simulator imports it.
