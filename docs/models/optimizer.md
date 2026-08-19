# The optimiser

Multi-gameweek squad selection as a single mixed-integer program. Entry point:

```python
from fpl_edge.opt import ObjectiveMode, OptimizerConfig, solve_horizon

plan = solve_horizon(problem, OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS))
```

`fpl_edge/opt/` layout:

| Module | What lives there |
| --- | --- |
| `config.py` | `ObjectiveMode`, autosub weights, solver settings |
| `interfaces.py` | What the optimiser consumes: price, points, rank utility |
| `problem.py` | `HorizonProblem`, `SquadState`, `Ruleset` (read from the registry) |
| `milp.py` | The model and the solve |
| `scoring.py` | The declared objective, recomputed independently, plus validation |
| `plan.py` | `HorizonPlan` / `GwDecision`, decisions only |

## The objective is a choice, and it is logged

`OptimizerConfig.mode` has no default.

* **`RANK_UTILITY`** is what this engine is for, and it currently **raises**.
  `E[U(rank)]` is a functional of the joint distribution of your score and the
  field's; it is not separable across players and cannot be written as a sum of
  per-player coefficients. `fpl_edge.opt.interfaces.RankUtilityProvider` defines
  the contract the simulator has to satisfy — a `linear_coefficients()` local
  linearisation for the MILP to optimise, and an `evaluate_plan()` that returns
  the true simulated value — and the intended shape is a trust-region loop:
  linearise, solve, re-evaluate, re-linearise, stop when `evaluate_plan` stops
  improving, and always report the simulated number rather than the surrogate.
  Until that exists, asking for this mode raises `RankUtilityUnavailableError`
  (a `NotImplementedError`). It does not fall back to means. A component that
  silently optimises expected points while reporting a rank objective is the
  specific failure this project exists to avoid.
* **`EXPECTED_POINTS`** maximises discounted expected points net of hits. It is
  implemented, and it is a surrogate. It ignores ownership, variance and
  covariance with the field, which is to say it ignores everything that makes a
  differential a differential.

Running both and diffing the squads is the intended way to measure what the
rank machinery buys, which is why the switch exists at all.

## What the model contains

All constraint values come from `fpl_edge/rules/registry.yaml` through
`Ruleset.from_registry()`, which raises on unverified rules. Nothing is
hardcoded — change the registry and the model changes.

**Squad.** 15 players, 2/5/5/3 by position, £100.0m, at most 3 per club, every
gameweek.

**Starting XI.** Exactly 11, with per-position play limits from the registry
(GKP 1–1, DEF 3–5, MID 2–5, FWD 1–3). Those bounds generate exactly the eight
legal formations; there is no formation enumeration anywhere in the code.

**Bench and autosubs.** The reserve keeper plus three ordered outfield slots.
Bench order is modelled with two nested binaries per outfield player
(`b1` = slot 1, `b12` = slots 1–2, bench membership = squad − XI), which is one
fewer binary than one-hot slot assignment and gives a tighter relaxation.

**Transfers.** A flow constraint per player per gameweek links ownership to
buys and sells. Free transfers accrue at 1 per gameweek, bank to 5, and extra
transfers cost −4 each. The 20-per-gameweek cap is enforced but cannot bind for
a 15-player squad.

**Prices and the sell-on fee.** Prices are integer tenths, always, and come
from a `PriceForecast` — the optimiser has no price model of its own.
`StaticPriceForecast` is the explicit null forecast.

Purchase prices follow the player: set to that gameweek's price on purchase,
carried unchanged while held, zero when not owned. The sale value is an
**integer** variable bounded by

```
sale <= price                       (a fall is borne in full)
2 * sale <= purchase + price        (you keep half the rise)
```

For integer tenths those two together give exactly
`min(price, purchase + floor((price - purchase) / 2))`, which is the FPL rule
with its floor-to-0.1m rounding, expressed without a rounding step and without
a single float. Bought at 7.5, worth 7.8, sells at 7.6 —
`tests/unit/test_opt_transfers.py` drives that through an actual transfer and
checks that a 7.6m replacement is affordable and a 7.7m one is not.

**Chips.** Availability windows, per-half counts, one chip per gameweek, and
the Free-Hit-not-consecutive rule all come from the registry. The GW1 lockout
falls out of the windows: Wildcard and Free Hit start at GW2, Bench Boost and
Triple Captain at GW1.

Free Hit is modelled with two squads per gameweek: `own`, which you carry, and
`play`, the fifteen that score. They are the same variable except in gameweeks
where Free Hit is reachable. Because Free Hit forbids persistent buys and
sells, the squad reverting the following week falls straight out of the
transfer flow constraint rather than needing a special case.

**Captaincy.** Captain and vice are distinct starters. The captain uplift is
exactly one extra copy of expected points — doubling a player who blanks
doubles zero, so no minutes term is needed. The vice-captain term is
`P(captain plays no minutes) x xPts(vice)`, a continuous expression times a
binary, linearised exactly.

## Approximations, stated

Three, all declared in `fpl_edge/opt/scoring.py` and applied identically by the
model and by the independent scorer, so the objective-agreement test still
means something:

1. **Autosub slot weights are constants.** Whether bench slot 2 comes on
   depends on how many starters blanked and on whether the resulting formation
   is legal — a joint distribution that lives in the simulator, not in a linear
   constraint. `AutosubWeights` holds fixed per-slot activation probabilities
   and its defaults are crude placeholders; `AutosubWeights.from_blank_rate`
   derives a sanity-checkable set, and the simulator should replace both.
2. **The vice-captain term ignores Triple Captain.** Exactly right would be a
   product of three binaries for a term of order `P(no play) x P(TC)`.
3. **Expected points are per gameweek.** Double gameweeks must arrive
   pre-aggregated in `xpts`.

Chip uplifts are collapsed to one variable per gameweek rather than one per
player when every `xpts` is non-negative — the same optimum, a much smaller
tree. With a negative `xpts` anywhere the model falls back to the per-player
linearisation and says so in `plan.notes`.

## Objective agreement

`scoring.score_plan` recomputes the objective from the returned decisions
alone, sharing no code with the encoding. Every solve in the test suite asserts
the two agree to 1e-6. This is the guard against the classic MILP bug where the
constraints drift from the formula and the solver reports a number nobody
checks.

The money and free-transfer ledger on a returned plan is likewise recomputed in
exact integer tenths by `replay_finances`, using `fpl_edge.types.selling_price`
rather than anything from the model. The MILP's own bank variables are only
pushed to their true values when the budget binds, so they are used for
feasibility and never reported.

## Determinism

`SolverConfig` is single-threaded with a fixed seed by default. Parallel MIP
races workers to find incumbents, so ties between equally-optimal squads break
differently run to run, and a recommendation that changes on re-run cannot be
audited or backtested. `tests/unit/test_opt_determinism.py` pins this.

## Solver and performance

PuLP with HiGHS via `highspy`. If HiGHS fails to initialise the model falls
back to CBC and records that in `plan.notes`; this has not been observed here.

Measured on the committed 592-player 2026/27 fixture, single-threaded HiGHS on
an Apple-silicon laptop, from GW1 pre-season with no existing squad:

| Instance | Variables | Binaries | Constraints | Build | Solve | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 592 players, 5 GW, all four chips | 36,091 | 25,411 | 66,008 | 2.5 s | 183 s | optimal |

Two things about that number worth saying plainly:

* It is sensitive to the big-M constants. An earlier version used one global
  big-M for purchase-price tracking and bounded chip uplifts by the whole
  universe's expected points; it hit a 600-second limit at a **64% gap**.
  Per-player big-Ms and chip uplifts bounded by what a chip can actually be
  worth — one player's points for Triple Captain, four for Bench Boost — took
  the same instance to a proven optimum in under a fifth of the time.
* Chip *scheduling* is where the difficulty lives. Chip variables are what let
  the LP relaxation buy fractional free transfers and fractional Free Hit
  squads, and the dual bound suffers accordingly.

Knobs, in the order worth reaching for:

* `allowed_chips` — drop chips you have already decided about.
* `SolverConfig.time_limit_s` and `mip_gap_rel` — stop early. A plan returned
  at a non-zero gap is still legal and still honestly scored, and
  `plan.mip_gap` plus a note in `plan.notes` say it was not proven.
* `max_candidates_per_position` — keep the top N per position by horizon xPts
  plus everything owned. This changes the answer, so it is off by default.

## Horizon artifacts

Two effects come from truncating the horizon rather than from the rules, and
both are visible in the tests:

* In the final gameweek of a horizon, Free Hit and Wildcard are worth the same,
  because nothing after the horizon is modelled to care that a Free Hit squad
  reverts.
* Squad *value* at the horizon's end has no worth in the objective, so the
  model will happily sell into a worse squad on the last gameweek if it buys a
  point. Extend the horizon, or add a terminal value term, before trusting the
  last gameweek of a plan.

## What is not modelled

* Rank utility (see above).
* Price *changes caused by your own transfers*. Prices are exogenous.
* Autosub interaction with formation legality (see approximations).
* Double and blank gameweeks as separate structure; they arrive folded into
  `xpts`.
