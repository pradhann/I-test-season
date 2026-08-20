# The rank-aware solver

> Implements `docs/platform/rank_objectives.md`. Every constant traces back to a
> committed study CSV in `docs/platform/`, or to a table this repo measures and
> commits. The tests in `tests/unit/test_rank_policy.py` recompute the closed
> forms against those CSVs row by row, so a drifted constant fails CI rather
> than quietly changing recommendations.
>
> Code: `fpl_edge/rank/` (state, policy, coefficients, validate),
> `ObjectiveMode.RANK_MV` in `fpl_edge/opt/`.

## 0. What changed, in four lines

1. The solver has a **state**. `RankState(D, tau)` — how far behind the running
   top-10k pace, with how many gameweeks left — is now an input to the objective
   rather than something a human reads off a report afterwards.
2. The objective is **`mu + theta(D, tau) * (1 - 2*share) * sigma^2`** per
   player, with the captaincy variable carrying the same term against *captaincy*
   share. Behind, variance is bought; ahead, it is sold.
3. `RANK_UTILITY` still raises. Putting the simulator inside the argmax loop was
   measured and rejected (§8.2); it is now a **validator** with paired common
   random numbers, reporting `Delta P(top 10k)` and admitting when it cannot
   resolve a difference.
4. Four adoptions from the public solver SOTA ship **off by default**, because
   their constants are community-tuned and we have not recalibrated them.

## 1. Why state-dependence and not a better search

The study's Monte Carlo (`rank_policy_mc.csv`, 200k seasons per cell) is
unusually blunt about where the value is:

| state (tau, D) | best static posture | myopic closed form | exact DP |
| --- | --- | --- | --- |
| 38, 0   | .713 | **.8026** | .8032 |
| 19, −40 | .199 | **.3163** | .3168 |
| 19, −20 | .358 | **.5221** | .5227 |
| 5, −15  | .276 | **.3774** | .3773 |

Adaptivity is worth **9.0 to 16.4 percentage points**. Look-ahead beyond the
weekly rule is worth **≤ 0.1pp**, inside one Monte Carlo standard error. So this
implementation is closed forms re-solved every deadline, and there is no dynamic
programme in it. That is not a shortcut; it is where the measurement pointed.

The same reasoning is why the simulator is not the search. §7.4: F1's Monte
Carlo could not separate **0 of 6** candidate swaps at 2 SE, because the
objective is a sum of indicators whose gradients are zero and whose noise swamps
the deltas that decide real transfers. Meanwhile the closed forms separate
captaincy and hit decisions analytically, from the same second moments the
simulator estimates *well*. The simulator's comparative advantage is estimating
`(m, s, Cov)` — so that is what it is used for.

## 2. The sufficient statistic (`fpl_edge/rank/state.py`)

`P(final rank <= 10,000)` reduces to two numbers plus two moments:

```
D    = my cumulative score - the top-10k pace          (negative is behind)
tau  = gameweeks remaining
m    = E[my weekly score - weekly pace increment]      edge vs the BAR
s    = SD[my weekly score - weekly pace increment]     effective volatility
```

**`s` is not own-score SD, and the difference is the whole model.** A full
template has own-score SD ≈ 15 pts/week but co-moves with the bar, so its
effective `s` is ~3; a differential decorrelates and its `s` is large at the
same own volatility. Formally:

```
s^2 = Var(mine) + Var(pace) - 2 Cov(mine, pace)
```

`deficit_moments(my_draws, pace_draws)` estimates all three from **paired**
simulator draws — my squad and the pace scored on the same Monte Carlo point
draws, which `SeasonSimulator` produces by construction. It returns the
decomposition alongside `s`, and `check_decomposition()` asserts the identity
closes, which catches the one mistake that would silently poison every `theta`
downstream: estimating the pieces on draws that were not actually paired.

This estimator is the study's stated first production task (§8, "the one number
this study could not derive from first principles"). It is tested against
synthetic bivariate draws with a covariance we wrote down, and against the
property that a template and a differential with *identical* own variance
produce effective `s` differing by more than 2×.

Moments are taken per gameweek and averaged, never pooled across a flattened
array: pooling folds week-to-week variation in fixture difficulty into `s`, and
`s` is exactly the number that must not be inflated, because the switch boundary
is linear in it.

Anchoring:

| regime | `D` | provenance |
| --- | --- | --- |
| pre-GW1 | **0**, an identity — nobody has played | `preseason:D=0,tau=38` |
| in-season | my total − the interpolated 10,000th total | `live_overall_standings` |

Provenance is a required field. A `theta` from a simulator-calibrated pace and
one from measured standings are different epistemic objects, and a report that
cannot tell them apart is lying.

## 3. The policy layer (`fpl_edge/rank/policy.py`)

### The switch boundary is a straight line

```
D* = tau * (m_a s_b - m_b s_a) / (s_a - s_b)
```

With the study's balanced and differential archetypes this is `-1.0642857 *
tau`: **gamble when more than ~1.06 points behind per remaining gameweek**.
Linearity is structural — drift scales with `tau`, dispersion with `sqrt(tau)`,
so the ratio deciding the comparison is scale-free — and it is why a weekly
re-solved closed form loses almost nothing to a DP.

`test_boundary_matches_the_committed_closed_form_at_every_tau` checks all 38
rows of `rank_switchpoint.csv`.

Sensitivity, quoted because it matters: the boundary is **linear in the edges**,
so halving the true `m` moves the rule from `-1.06 tau` to `-0.53 tau`.
Self-flattering edge (a squad optimised against the projection that also
generates its outcomes) therefore biases the solver toward gambling **too
late**, never too early.

### theta

```
theta(D, tau) = -z / (2 Sigma),   z = (D + m tau) / Sigma,   Sigma^2 = s_1^2 + s_bar^2 (tau-1)
```

from `dP/d(s^2) / dP/dm` on `P(hit) = Phi(z)`. Verified numerically by finite
differences, not merely asserted. Sign and magnitude are the content: positive
when behind, negative when ahead, growing as `Sigma` shrinks — so
late-with-a-deficit is peak variance appetite, because a deficit that edge can
close in March is closable only by luck in May.

`theta_cap` implements §2's trust region and **defaults to `None`**. No study
here has calibrated a cap, and a fabricated one would be exactly the smuggled-in
risk aversion §7.1 objects to.

### Captaincy

```
score_i = mu_i + theta * (1 - 2*c_i) * sigma_i^2
```

This is "EV × ownership × variance" with the ownership term pinned down: **the
variance credit flips sign at 50% cohort captaincy share**. A captain owned by
more than half the cohort is variance-*reducing* to pick, whatever his raw
sigma, because you are holding the bar rather than betting against it. In
`rank_captaincy.csv`, Haaland at 48% share carries relative SD 3.46 against a
3%-owned punt's 7.10, on raw sigmas of 5.8 and 6.4.

Two structural findings the rule reproduces, and which are tested:

* the captaincy punt is a **last-weeks-from-behind instrument**. Thirty weeks
  out, even 60 points behind, the max-EV captain is still correct.
* **the mid differential is dominated.** A 22%-share alternative is rank-optimal
  in no cell of the grid — it concedes EV without buying enough decorrelation.
  If you deviate, deviate properly.

### The hit

```
g* = 4 + L * (S' - S) / S,     L = D + m tau,  S = s sqrt(tau),  S'^2 = s^2(tau-h) + s'^2 h
```

Points logic says `g* = 4` always; the second term is the rank correction, and
it is large. At `tau=12, h=8`, a hit raising weekly `s` from 6.0 to 8.0:

| L | g* |
| --- | --- |
| −60 | **−9.9** — a hit *losing* 10 xP is justified |
| −30 | −3.0 |
| 0 | 4.0 |
| +30 | 11.0 |
| +60 | 17.9 |

and a variance-*shedding* hit inverts it (g* falls to 0.1 at L=+60: safety is
nearly free to buy when you are ahead). Every row of
`rank_hit_threshold.csv` is recomputed in the tests, and the MC sign checks in
`rank_hit_threshold_mc.csv` are replayed.

The hit cost comes from the verified rule registry, never a literal 4.

§5's caveat is implemented rather than noted: taking a hit forfeits the banked-FT
option, so `g*` applies to gain **net** of that option value, and
`hit_is_justified(..., ft_option_value=)` subtracts it from the gain rather than
adding it to the threshold.

### The lambda gate

`fpl_edge/sim/utility.py` carries a **constant** `lambda = 0.35` on a CVaR term.
§7.1: the objective's own risk appetite flips sign with the state, so a fixed
left-tail penalty is a template thumb on the scale precisely in the deficit
states where variance is the only route to the target.

```
lambda_effective(lambda, state) = lambda * 1[D + m tau >= 0]
```

with `lambda_effective_soft` providing §7.1's literal ramp (zero by one
season-SD of deficit). If catastrophe aversion is not a genuine preference,
§7.1's other branch applies and `lambda` should be zero, not gated.

## 4. `ObjectiveMode.RANK_MV` in the MILP

The objective is written against two coefficient matrices instead of one:

```
lineup[p, gw]  = mu_p + theta * (1 - 2 * own_share_p)     * sigma_p^2
captain[p, gw] = mu_p + theta * (1 - 2 * captain_share_p) * sigma_p^2
```

`lineup` prices the starting XI, the autosub-weighted bench and the Bench Boost
uplift — every variable meaning "this player's points enter my total once".
`captain` prices the armband, Triple Captain, and the vice (who inherits the
armband when the captain blanks). The two differ because the armband competes
with the cohort's *armbands*, not their squads, and captaincy is a far more
concentrated distribution than ownership, so the two 50% crossovers sit in very
different places.

Under `EXPECTED_POINTS` both matrices **are** `problem.xpts`, so every term is
unchanged and the mode is behaviourally identical to before.

### The F2 approximation, stated on every plan

The exact relative variance of a squad is `(w - e)^T Sigma_x (w - e)` —
quadratic in the ownership vector, an MIQP over ~600 binaries. F2 prices the
**diagonal** per player and lets **cross-player covariance enter through `Sigma`
inside `theta`**, where `Sigma` comes from the squad-level effective `s` that
`deficit_moments` measures from paired draws with every covariance already
inside it.

So the covariance channel is present at *squad* resolution, in the **price** of
variance, and absent at *pair* resolution, in the **allocation** of variance.
Every RANK_MV plan carries that sentence in `plan.notes`, along with the note
that the objective is in points and not points-above-the-bar (the pace increment
is player-independent, so it shifts the objective and never the argmax).

### Two other things the plan says out loud

* the `RankState` and the `theta` it produced, so a recommendation can be read
  back against the posture that generated it (§7.2: a verdict quoted without its
  state overreaches);
* the shares' provenance, which before GW1 is `ownership_marginals:prior` —
  because no rival squads are public yet and marginals are genuinely all there
  is.

## 5. Adoptions from the public solver SOTA

From `docs/platform/solver_state_of_art.md`. All four default **off**: their
constants are community-tuned against a different projection source and have not
been recalibrated against our simulator, and under this repo's evidence rule an
unvalidated constant may be offered but never defaulted into the objective.

| adoption | knob | note |
| --- | --- | --- |
| Telescoping banked-FT value | `ft_value_list` (`FT_VALUE_LIST_SOTA` = `{2:2.0, 3:1.6, 4:1.3, 5:1.1}`) | The FT chain is extended **one step past the horizon**; that terminal variable is the whole point. Without it, leftover transfers are worth zero and get spent in the last modelled week — a truncation artefact, not a decision. |
| Geometric discount | `decay_base` (default **1.0** = off), `decay_metrics(bases=[0.85, 1.0])` | Reported, never optimised. Their `report_decay_base` includes 1.017; a base above 1 weights the least trustworthy forecasts most heavily and is refused. |
| Locked / banned players | `locked`, `banned` | Bounds on `own`, wired into the pruning safe-list — a constraint naming a player the pool no longer contains is silently ignored, which is worse than a larger model. |
| Chip scheduling | `allowed_chip_gws`, `forced_chip_gws`, `no_chip_gws` | On top of the registry's availability windows; they only ever remove options. An unsatisfiable forced chip is `InfeasibleError`, not a shrug. |
| Alternative plans | `enumerate_plans(k=3, criterion=...)` | No-good cuts. Each returned plan differs by at least `difference` decisions and objectives are comparable because they come from one model. |

The banked-FT potential is priced by the **lower envelope of its chords** rather
than one-hot binaries. `V` is concave over the reachable range (marginal values
2.0, 1.6, 1.3, 1.1 from state 1, since the FT variable is bounded below by 1),
so the envelope is exact at integers and the branch-and-bound tree does not
grow. A non-concave table is refused rather than approximated.

**Deliberately not adopted:** their `randomized` projection-noise heuristic — an
ensemble of argmaxes of perturbed means is a weaker, biased substitute for
evaluating plans under a distribution, and we have a distribution — and their
float-pound arithmetic, against this repo's integer tenths.

## 6. F1 as validator (`fpl_edge/rank/validate.py`)

`validate_plans(simulator, plans)` scores the enumerated shortlist on common
random numbers and reports, against plan 1:

* `delta_p_top` with a **paired** standard error;
* `delta_points` with a paired standard error;
* `resolved` — `|Delta| > 2 SE`.

Pairing is not a refinement. The paired SE scales with the *disagreement rate*
between two plans while the unpaired one scales with each plan's own outcome
rate, so the advantage grows the more alike the plans are — which is exactly the
regime a solver operates in. Measured in the tests: a factor of >7 for
near-identical plans.

**`resolved = False` is a finding.** `render()` prints "unresolved at 2 SE" and,
when nothing separates, the §7.4 citation. A validator that showed the sign of
an unresolved delta would be inviting the reader to act on Monte Carlo noise.

Levels are deliberately not the headline: §7.4's model-flattering critique
applies to the level of `P(top 10k)`, not to a paired difference.

## 7. What a recommendation now carries

`TransferRecommendation` gains four fields, all additive:

* `rank_state` — the state solved at, plus `rank_summary()` naming the posture;
* `hit_verdicts` — one per hit-taking move, judged against `g*`. `s'` comes from
  the relative-variance delta the rank coefficients imply, so the verdict and
  the objective reason about one quantity rather than two. The gain compared to
  `g*` is expected points **gross of the hit**, because the RANK_MV objective has
  already priced variance and already netted the hit;
* `alternatives_with_delta_p` — F1 output when a simulator is supplied. A broken
  or universe-less validator degrades to a note; §8.2 makes F1 a check on the F2
  answer, not a precondition for it;
* `banked_ft_value`.

`RANK_MV` without coefficients refuses **before** the points model runs.

## 8. What is not built

* **No chip planner.** §6's two-stage scenario tree is not implemented. Its
  finding — waiting wins in every cell, because the cohort's chips raise the pace
  whether or not you still hold yours — is robust, but the numbers need real cup
  schedules and a cohort chip-usage model before they are quotable. The
  `forced/allowed/no_chip_gws` knobs are what let a human express a chip plan in
  the meantime.
* **No measured `(m, s)` in the shipped defaults.** `BASELINE_MENU` is the
  study's stylised menu and is labelled as such by
  `PROVENANCE_STYLISED_MENU`. Production runs should call `deficit_moments` on
  real paired draws. Until they do, every boundary this repo quotes inherits the
  stylised calibration — and §3's sensitivity result says the *shape* survives
  while the *location* moves linearly with the true edge.
* **No per-pair covariance in the objective.** See §4 above. This is the F2
  approximation, and the validator is where it gets checked.
* **Captaincy share before GW1 is a lower bound**, derived as
  `max(EO - ownership, 0)` from LiveFPL's predicted effective ownership, because
  EO nets benched owners against captains. `fpl_edge/models/field` is the seam
  for replacing it with measured cohort shares once rival squads are public;
  `shares_from_field_samples` already consumes them.

## 9. Reproducing

```
uv run pytest tests/unit/test_opt* tests/unit/test_rank* -q
uv run python scripts/rank_gw1_solve.py            # both objectives, side by side
uv run python scripts/rank_objective_study.py      # regenerates every study CSV
```

`scripts/rank_gw1_solve.py` measures per-position points moments from four
completed seasons of `fact_player_fixture` (113k rows), writes
`docs/platform/rank_points_variance.csv`, converts them to per-player
unconditional variances by the law of total variance over the appearance
channel, takes ownership from FPL's published `selected_by_pct` and captaincy
from the external EO feed, and solves GW1 in both modes. It reads the warehouse
through `Warehouse.read_copy()`, because a MILP solve holding a reader open
would block every ingest job for its whole runtime.
