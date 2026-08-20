# Rank objectives: what P(top 10k) actually asks the weekly solver to do

> Study: `scripts/rank_objective_study.py`. Result CSVs in this directory.
> Reproduce every number with `uv run python scripts/rank_objective_study.py`
> (deterministic seeds; reruns are bit-identical).
>
> Written independently of `fpl_edge/sim/`; that package and
> `docs/models/simulator.md` were read as one input and are critiqued in §7.

## 0. The answer in four lines

1. **The objective has a two-dimensional sufficient statistic**: the deficit
   `D` against the running top-10k pace, and remaining weeks `τ`. Everything
   rank-aware a weekly solver should do is a function of `(D, τ)`.
2. **The switch from mean-seeking to variance-seeking is a straight line**:
   gamble when the deficit exceeds ~1 point per remaining gameweek (exact
   constant depends on the edge menu; derivation and table in §3).
3. **A weekly re-solved closed-form rule captures essentially all of the
   dynamic-programming value** — DP beats the myopic rule by ≤ 0.1pp of
   P(top 10k) everywhere we measured, while *any* fixed risk posture gives up
   9–19pp against the adaptive one. State-dependence is worth an order of
   magnitude more than look-ahead sophistication.
4. Recommended architecture (§8): deficit-ladder DP sets this week's risk
   coefficient; a mean–variance–covariance score ranks candidates under it;
   the full simulator validates the final shortlist with paired draws; EO
   heuristics only break ties.

## 1. The sufficient statistic, and what "variance" must mean

The objective is `P(final rank ≤ 10,000)` over N ≈ 5.9M managers (stretch
1,000). Rank is a monotone function of one scalar: my season total minus the
season total of the 10,000th-ranked manager. Define the **pace process**
`Q_t` = cumulative score of the (interpolated) 10,000th rank at week `t`, and
the **deficit** `D_t = X_t − Q_t`. The objective is exactly `P(D_38 ≥ 0)`.

The decision-relevant weekly quantities are therefore *relative*:

- `m = E[my weekly score − weekly pace increment]` — edge **vs the bar**, not
  vs the average manager. Even a genuinely good model has small positive `m`
  here, because the bar is set by the field's right tail.
- `s = SD[my weekly score − weekly pace increment]` — **effective volatility**.

This second definition is the load-bearing one. A full-template squad has
own-score SD ≈ 15 pts/week but co-moves almost perfectly with `Q_t`, so its
*effective* `s` is a few points; a differential squad decorrelates from the
bar, so its effective `s` is large even if its own-score SD is similar. "Risk"
in rank terms is `Var(mine − field bar)`, i.e. it is dominated by the
covariance channel:

```
s² = σ_mine² + σ_pace² − 2·Cov(mine, pace)
```

The in-repo simulator gets this structurally right by scoring my squad and all
rivals on the same point draws; this study takes the reduced form `(m, s)` as
primitive and asks what the *objective* does with it. Calibration is anchored
to the live run recorded in `docs/models/simulator.md` §9 (xP-optimal squad:
season mean 2,217, bar ≈ 2,196 ⇒ `m ≈ +0.55/wk`; own-score weekly SD
94/√38 ≈ 15.2). The archetype menu used throughout:

| archetype | `m` (pts/wk vs bar) | effective `s` (pts/wk) | season deficit SD |
| --- | --- | --- | --- |
| template  | −0.30 | 3.0  | 18.5 |
| balanced (xP-optimal) | +0.55 | 6.0 | 37.0 |
| diff (differential tilt) | +0.25 | 9.5 | 58.6 |
| punt (heavy) | −0.60 | 13.5 | 83.2 |

These are stylised; §3 gives the sensitivity of every boundary to them in
closed form, which is why the *shape* of the results survives calibration
error even though the exact numbers move.

## 2. Four formulations a weekly solver can optimize

### F1. Threshold exceedance on simulated season totals (simulation–optimization)

Maximize `P(R ≤ T) + w_s·P(R ≤ S) − λ·CVaR_α[L(R)]` over candidate squads,
estimated by Monte Carlo with common random numbers (this is what
`fpl_edge/sim` implements).

- **Inputs**: full joint points model (all players × sims × 38 GWs), ownership
  forecasts, field/rival model, rank extrapolation for sub-resolution
  thresholds.
- **Cost**: the field is scored once (~`n_rivals × n_sims × 38`); each
  candidate re-scores only my squad, but the *search* is combinatorial —
  practical only as local search over a shortlist. Minutes per decision.
- **Failure modes**: (i) the objective is a sum of indicators, so gradients
  are zero and Monte Carlo noise swamps exactly the small deltas that decide
  real transfers — the in-repo GW1 run itself resolved **0 of 6** candidate
  swaps at 2 SE; (ii) levels are model-flattering (my squad optimised against
  the projection that also generates outcomes; frozen rival squads), so only
  paired *relative* numbers are usable; (iii) sub-10k-resolution tails lean on
  a Cornish–Fisher extrapolation.
- **Degradation under a crude field model**: worst-case. The entire value of
  F1 over plain xP is the covariance channel; with a wrong ownership/field
  model it degenerates into a noisy, expensive estimate of expected points.

### F2. Mean–variance with a state-dependent risk coefficient (certainty-equivalent local rule)

Let this week's candidate have relative mean `m₁` and effective variance
`s₁²`, with the rest of the season at baseline `(m̄, s̄)`. With Gaussian season
totals (CLT over ≥ a handful of weeks),

```
P(hit) = Φ(z),   z = (D + m₁ + m̄(τ−1)) / Σ,   Σ² = s₁² + s̄²(τ−1)
∂P/∂m₁ = φ(z)/Σ,   ∂P/∂(s₁²) = −φ(z)·z / (2Σ²)
```

so to first order every candidate can be scored by the **certainty-equivalent
weekly score**

```
score = m₁ + θ·s₁²,    θ(D, τ) = −z / (2Σ)
```

`θ` is the state-dependent risk coefficient: **positive when behind the pace
(z < 0), negative when ahead, magnitude growing as Σ shrinks** — i.e. late
season with a deficit is maximal variance appetite. Equivalently, along an
iso-P curve the solver should pay `|z|/√τ` points of weekly mean per point of
weekly SD when behind.

- **Inputs**: per-candidate `m₁` (xP minus pace increment) and `s₁²`, which
  needs a player covariance matrix and ownership (to compute covariance with
  the bar) — no simulation.
- **Cost**: one covariance matrix; the squad problem becomes an MIQP or a
  greedy swap search. Milliseconds to seconds.
- **Failure modes**: local — valid for candidates near the incumbent; a huge
  `θ` near the deadline turns the argmax into "maximize variance", which must
  be trust-regioned; Gaussianity understates one-week tail skew (captaincy,
  chips).
- **Degradation**: graceful. It consumes only second moments; a crude
  ownership model gets `Cov(mine, pace)` wrong in level but usually right in
  *ordering* (owning more of the template ⇒ higher covariance), and ordering
  is what the argmax uses.

### F3. Dynamic programming on the deficit ladder (strategic layer)

State `(D, τ)` (extendable with chips-in-hand and free transfers), action =
risk archetype, transition `D' = D + m(a) + s(a)·Z`, terminal reward
`1{D ≥ 0}`. Solved by backward induction in `study_state_dependence()`:
2,401-point grid × 38 weeks × 4 actions × 41-node Gauss–Hermite quadrature,
< 1 s total.

- **Inputs**: only the archetype menu `(m, s)` and a pace model for `Q_t`. It
  never sees a player.
- **Cost**: negligible; can be re-solved every deadline.
- **Failure modes**: the archetype abstraction answers *how much* risk, never
  *which player*; assumes week-to-week independence of shocks; deficit vs a
  *quantile* pace is treated as a random walk, which ignores that the bar
  itself accelerates when variance across the field is high (second-order).
- **Degradation**: the most robust of the four — a mis-calibrated menu shifts
  boundaries (closed-form sensitivity in §3) but cannot change their shape,
  and §3's MC shows the myopic closed form loses ≤0.1pp to the exact DP
  anyway, so the strategic layer survives crude inputs almost untouched.

### F4. EO-leverage heuristics: covariance-adjusted expected rank gain (tactical tie-break)

For small moves, expected rank gain from owning player `i` at effective
ownership `e_i` is `≈ ρ(D)·(own_i − e_i)·xp_i` where `ρ(D)` is the local
density of managers around my score; F2's derivation upgrades it with a
variance credit `θ·(1 − 2e_i)·σ_i²` (see §4 — the credit flips sign at 50%
EO).

- **Inputs**: EO forecasts, xP, per-player variances. No simulation, no
  covariance matrix.
- **Cost**: a sort.
- **Failure modes**: the density weight is only locally valid (it prices a
  10-place gain and a 100k-place gain identically wrong); ignores intra-week
  player covariance (double-punting one match is not two independent punts);
  no season dynamics at all.
- **Degradation**: worst. It is a pure function of EO, which is the crudest
  forecast in the platform. Use only to order candidates that F2 scores as
  equal.

## 3. The state-dependence theorem and the simulation study

**Claim.** Holding a strategy `(m, s)` for the remaining `τ` weeks,
`P(hit) = Φ((D + mτ)/(s√τ))`, so

```
∂P/∂s > 0  ⇔  D + mτ < 0
```

Variance helps **iff you are behind on expectation** — the gambler's-ruin
logic: from behind, low variance makes missing the target *certain*; the only
route to the tail is volatility, and its value grows as `τ` shrinks because
the drift term scales with `τ` while dispersion scales with `√τ` (a deficit
that edge can close in March is closable only by luck in May).

**The myopic switch boundary is a straight line through the origin.** Strategy
`d` (differential) beats `b` (balanced) iff their z-scores cross:

```
D* = τ · (m_b·s_d − m_d·s_b) / (s_b − s_d)
```

With the §1 menu, `D* = −1.064·τ`: **gamble when more than ~1.06 points behind
per remaining gameweek**. At τ=19 that is −20.2; at τ=4, −4.3; at τ=1, −1.1.
The numerically-found boundaries in `rank_switchpoint.csv` match the closed
form to grid resolution at every τ (e.g. τ=19: −20.125 found vs −20.221
closed-form). The other two boundaries are also near-linear: punt overtakes
diff at ≈ −2.27·τ, and template overtakes balanced (lock in a lead) at
≈ +1.15·τ.

**Sensitivity — the boundary is linear in the edges.** If the model's edge is
self-flattering (my squad is optimised against the projection that also
generates outcomes — see §7), the real `m`s are smaller and the boundary
tightens proportionally: halving both edges moves the rule from −1.06·τ to
−0.53·τ. **Overstated edge is a systematic bias toward gambling too late.**

**Dynamic programming bends the line only slightly** (`rank_switchpoint.csv`):
far from the deadline the DP gambles a little later than myopic (−45.1 vs
−40.4 at τ=38 — the option to gamble later is worth something), and near it a
little earlier (−1.6 vs −2.1 at τ=2). The bend is worth almost nothing:

**Monte Carlo (200k seasons/cell, paired shocks, `rank_policy_mc.csv`):**

| state (τ, D) | template | balanced | diff | punt | myopic rule | exact DP |
| --- | --- | --- | --- | --- | --- | --- |
| 38, 0   | .268 | .713 | .563 | .392 | **.8026** | .8032 |
| 19, −40 | .000 | .130 | .199 | .193 | **.3163** | .3168 |
| 19, −20 | .024 | .358 | .357 | .298 | **.5221** | .5227 |
| 19, 0   | .331 | .656 | .547 | .423 | **.7656** | .7660 |
| 19, +20 | .863 | .878 | .725 | .558 | **.9396** | .9399 |
| 10, −30 | .000 | .098 | .180 | .199 | **.2939** | .2940 |
| 5, −15  | .007 | .181 | .259 | .276 | **.3774** | .3773 |

(MC SE ≤ 0.0011 per cell.) Two readings:

1. **Adaptivity is the whole prize.** The best *static* posture loses 9.0pp
   from an even start (.713 → .803), 11.8pp from (19, −40), 16.4pp from
   (19, −20), 10.2pp from (5, −15). A solver with a fixed risk coefficient —
   including a fixed `λ` on a CVaR term — is leaving this on the table.
2. **The closed-form weekly rule is enough.** DP’s edge over re-solved myopic
   is ≤ 0.1pp everywhere, within ~1 MC SE. Implement the linear boundary and
   re-evaluate weekly; save the DP for chip/hit state extensions.

## 4. Captaincy: the rank-optimal rule and where it leaves max-EV

Let candidate captains have increments `x_i ~ (μ_i, σ_i²)` and let `c_j` be
the captaincy shares of the near-threshold cohort. My relative gain from
captaining `i` is `G_i = x_i − Σ_j c_j x_j`, so

```
E[G_i]  = μ_i − Σ_j c_j μ_j
Var(G_i) = (e_i − c)ᵀ Σ_x (e_i − c)   [independence: (1−c_i)²σ_i² + Σ_{j≠i} c_j²σ_j²]
```

Scoring with F2 (`score = E[G_i] + θ·Var(G_i)`) and dropping i-independent
terms under independence gives the **rank-captaincy rule**:

```
score_i ≈ μ_i + θ·(1 − 2c_i)·σ_i²
```

This *is* "EV × ownership × variance", with the exact form of the ownership
term: **the variance credit is scaled by (1 − 2·share) — a captain owned by
more than half the cohort is variance-reducing to pick**, whatever his σ.
And θ carries the state: the credit pays only when behind.

**Worked example** (`rank_captaincy.csv`; menu: Haaland μ=8.6 σ=5.8 share 48%,
Palmer μ=7.4 σ=5.2 share 22%, punt μ=6.8 σ=6.4 share 3%, residual field mix
share 27% μ=6.9; premiums correlated 0.10 same-slate). Relative moments that
result: Haaland **+0.78** (SD 3.46), Palmer **−0.42** (SD 4.92), punt
**−1.02** (SD 7.10) — Haaland at 48% share has *less than half* the relative
volatility of a 3%-owned punt, despite similar raw σ.

P(hit) by captain and state (analytic; MC at 400k sims agrees within ~1 SE):

| τ (weeks left) | deficit where punt overtakes Haaland | example |
| --- | --- | --- |
| 1  | between −4 and −8 | at −15: punt .048 vs Haaland .024 — **2.0×** |
| 4  | between −15 and −25 | at −25: punt .0438 vs Haaland .0389; at −40: .0027 vs .0015 — 1.8× |
| 12 | ≈ −60 | at −60: punt .00661 vs Haaland .00625 |
| 30 | never (≥ −60) | at −60: Haaland .0980 vs punt .0927 — max-EV still right |

So max-EV differs from rank-optimal exactly on the §3 boundary’s schedule:
**the captaincy punt is a last-weeks-from-behind instrument**; thirty weeks
out, even 60 points behind, the max-EV captain is correct because edge still
has time to compound. Two structural findings beyond the rule itself:

- **The mid differential is dominated.** Palmer (22% share) is the rank-optimal
  captain in *no cell of the grid*: he concedes EV to Haaland without buying
  enough decorrelation. If you deviate, deviate properly.
- **Covariance matters with the right sign.** A punt negatively correlated
  with the field's captain (opposing fixture) buys extra Var(G); one on
  Haaland's own team buys less than his σ suggests. The Σ_x form above prices
  this; the independence shortcut misses it.

## 5. The hit: when is −4 justified in rank terms?

A hit costs 4 points **with certainty**; it buys `g = δ·h` expected points
(δ/week over a holding period `h`) and changes effective weekly SD from `s`
to `s'` over those weeks. With `L = D + m·τ` the expected final margin
without the hit, `S = s√τ`, and `S'² = s²(τ−h) + s'²h`, equalising z-scores
gives the **break-even total gain**

```
g* = 4 + L · (S' − S) / S
```

Points logic says `g* = 4` always; the second term is the rank correction.
From `rank_hit_threshold.csv` (baseline balanced, τ=12, h=8):

| expected final margin L | hit raises weekly s 6.0→8.0 (differential) | hit lowers s 6.0→5.4 (template cover) |
| --- | --- | --- |
| −60 | **−9.9** (a hit *losing* up to 10 xP is justified) | 7.9 |
| −30 | **−3.0** | 6.0 |
| 0   | 4.0 | 4.0 |
| +30 | 11.0 | 2.0 |
| +60 | 17.9 | **0.1** (safety is nearly free to buy) |

Monte Carlo sign checks (400k sims, `rank_hit_threshold_mc.csv`): at L=−30,
s'=8, the analytic break-even is g\*=−2.97, and a hit gaining only −1.97
points (i.e. *losing* ~2 xP) still raises P(hit) from .0744 to .0802;
1 point below break-even it lowers it (.0691). All eight checks bracket
correctly.

Reading: **behind, hits for variance-buying differentials are justified below
— sometimes far below — the 4-point rule; ahead, the same move must clear a
much higher bar, while variance-shedding hits get cheap.** With a longer
horizon the correction shrinks (τ=26, h=8, s'=6.8: g\* ranges only 1.4–6.6
across L∈[−60,+60]) — early season, the points rule is nearly right, which is
consistent with §3. One caveat the scalar model omits: taking a hit forfeits
the banked-FT option, worth a state-dependent fraction of a free transfer —
so treat `g*` as the threshold on gain *net* of that option value.

## 6. Chip timing as stochastic planning over DGW/BGW scenarios

**Formulation.** Chips are a one-shot resource whose value concentrates in
double gameweeks, whose existence is resolved by cup progress. The honest
formalisation is a scenario tree: nodes = (GW, revealed cup outcomes), branch
probabilities from a cup-progression model (or bookmaker outright odds), and a
non-anticipativity constraint — the policy may condition only on outcomes
already revealed. The decision variable is a mapping from tree nodes to chip
plays; the objective is E over scenarios of P(hit) given the play, evaluated
with the §2 machinery (a chip is a one-week `(Δm, Δs)` injection whose Δm is
*relative*: the near-threshold cohort's chip plays enter the pace).

**Data it needs**: FA Cup and EFL Cup round dates and PL reschedule history
(which cup round collides with which GW); the rule change that FA Cup replays
were abolished from 2024–25 (fewer stochastic rearrangements, so the tree is
smaller and better-behaved than pre-2024 intuition suggests); UEFA midweek
calendars; a per-team cup-progression probability; and — the piece most
platforms skip — **chip-usage forecasts for the near-threshold cohort**,
because a chip window everyone plays is defensive, not offensive.

**Smallest honest implementation** (`study_chip_timing()`, two-stage tree,
τ=12): play TC now on a single (my gain N(8.6, 5.8²), 5% cohort usage), or
wait: with prob `p_dgw` a DGW materialises in 4 weeks (TC worth N(14.5, 9.5²),
**35% cohort usage**), else fall back to a late single (N(8.2, 5.7²), 10%).
From `rank_chip_timing.csv` (analytic; MC at 400k sims agrees to 3 decimals):

| state | p_dgw | P(hit) play now | wait | clairvoyant |
| --- | --- | --- | --- | --- |
| D=−40 | .75 | .090 | **.137** | .138 |
| D=−15 | .55 | .441 | **.497** | .500 |
| D=0   | .35 | .720 | **.743** | .747 |
| D=+40 | .35 | .9924 | **.9933** | .9935 |

Three results:

1. **Waiting wins in every cell — including from ahead.** The dominant term is
   defensive: in the DGW scenario the cohort's chips raise the pace whether or
   not I have a chip left; having already spent mine converts their window
   into my pure loss. Chip timing is first about *matching the field's chip
   windows*, only second about my own EV.
2. **The value of waiting is state-dependent in the §3 direction**: +4.7pp at
   (−40, p=.75) versus +0.09pp at (+40, p=.35) — behind, the DGW's doubled
   variance is itself part of the prize.
3. **The clairvoyant gap is tiny** (≤ .005): resolving cup uncertainty early
   is worth little because the wait-policy's in-branch action is almost always
   right anyway. Money spent on better cup forecasts is mostly wasted;
   money spent on cohort chip-usage forecasts is not.

## 7. Where the in-repo reasoning breaks

Read as one input and stress-tested; four specific findings.

1. **The fixed-λ CVaR term fights the stated objective from behind.**
   `fpl_edge/sim/utility.py` maximises `P(R≤T) + 0.25·P(R≤S) −
   0.35·CVaR₀.₁[log-rank loss]` with λ constant. §3 shows the objective's own
   risk appetite flips sign with the state; a constant penalty on the left
   tail is a template thumb on the scale precisely in the deficit states where
   variance is the only route to the target (the −4.7pp-to-−16.4pp static-vs-
   adaptive gaps in §3 are what a fixed posture costs). If catastrophe
   aversion is a genuine preference, gate λ on the state (λ ≈ 0 once
   `D + mτ < 0` by more than one season-SD); if it is not, it is smuggled-in
   risk aversion and should be zero.
2. **The GW1 verdict ("the premise is FALSE") overreaches its state.**
   `docs/models/simulator.md` §9 measured `D + mτ > 0` (mean 2,217 vs bar
   2,196) and correctly found mean-domination — that is the `D* > −1.06τ`
   branch of §3's law, not a refutation of rank-seeking. The doc's own §9.1
   concedes conditionality; the boundary table here makes it quantitative.
3. **Self-flattering edge biases the solver template-ward.** The measured
   `m = +0.55/wk` is computed against outcomes generated by the same
   projection the squad was optimised on. §3: the switch boundary is linear
   in `m`, so halving the true edge halves the deficit at which gambling
   becomes right (−1.06τ → −0.53τ). The frozen-field simplification pushes the
   same way: real rivals transfer, so the real pace drifts upward and live `D`
   is overstated. Both errors delay the switch; neither ever accelerates it.
4. **Resolution mismatch.** F1's Monte Carlo could not separate 0 of 6
   candidate swaps at 2 SE, yet the closed forms in §4–§5 separate captaincy
   and hit decisions analytically under the same second-moment assumptions
   the simulator itself estimates well. The simulator's comparative advantage
   is estimating `(m, s, Cov)` — not being the argmax loop.

Also noted: `rank_utility()`'s reported `se_utility` excludes the CVaR term's
sampling variance (flagged in-code as a lower bound) — fine, but any
2-SE decision rule using it is slightly anti-conservative.

## 8. Recommendation, ranked

1. **F3 → F2 layered solver (high confidence).** Each deadline: update
   `(D, τ)` from live standings and a pace model; get the risk posture from
   the myopic closed-form boundaries (they capture the DP to ≤0.1pp, §3);
   convert to `θ(D, τ) = −z/2Σ`; rank all candidate squads/captains/hits by
   `m + θ·s²` with `s²` computed against the bar (covariance channel). This
   is the only formulation that is simultaneously cheap enough to run in the
   argmax loop and state-aware enough to capture the 9–19pp adaptivity prize.
2. **F1 as validator, not searcher (high confidence).** Run the full
   simulator on the F2 shortlist (≤10 candidates) with common random numbers
   and report paired ΔP(top 10k); it is the only layer that prices squad-level
   covariance exactly. Drop the fixed-λ CVaR term or gate it on state (§7.1).
3. **Captaincy and hit rules as first-class outputs (high confidence in form,
   medium in constants).** Ship `score_i = μ_i + θ(1−2c_i)σ_i²` and
   `g* = 4 + L(S'−S)/S` directly — they are exact under the Gaussian
   reduction, MC-verified here, and independently useful even if the archetype
   calibration moves.
4. **Chip planner as a two-stage tree with cohort chip-usage in the pace
   (medium confidence).** The wait-premium and its state-dependence are
   robust; the specific probabilities need real cup schedules and a cohort
   chip model before the numbers are quotable.
5. **F4 EO heuristics: tie-breaks only (high confidence).**

Main threat to validity: the archetype menu's `(m, s)` are stylised from one
live simulator run. The boundary *slopes* scale linearly with the true edges
(§3 sensitivity), so the first production task is estimating effective `s`
per squad from the simulator's own paired draws — the one number this study
could not derive from first principles.
