# Solver State of the Art: sertalpbilal/FPL-Optimization-Tools and its ecosystem

*Written 2026-08-19 from a fresh clone of `github.com/sertalpbilal/FPL-Optimization-Tools`
(HEAD `136c39c`, 2026-08-14, "Skip whitespace-only paths in load_config_files (#64)").
Every variable name, setting name and column name below was read out of their code
(`dev/solver.py`, `dev/data_parser.py`, `run/solve.py`, `run/sensitivity.py`,
`run/simulations.py`, `data/comprehensive_settings.json`, `data/README.md`), not recalled.*

## 0. The ecosystem in one paragraph

Sertalp B. Cay ("Alpescan" / alpscode.com, operations researcher) built the public
reference implementation of multi-period FPL optimization and documented it in a
YouTube playlist ("FPL Optimization", linked from their README:
`youtube.com/playlist?list=PLrIyJJU8_viOags1yudB_wyafRuTNs1Ed`). The repo is now
community-maintained — its own README instructs cloning
`github.com/solioanalytics/open-fpl-solver` (maintainer contact chris.musson@hotmail.com),
i.e. the project has migrated under Solio Analytics, whose paid projections ("solio")
are now the default `datasource`. The solve prints a license banner: free for
personal use, commercial license via `info@fploptimized.com` (fploptimized.com is
Sertalp's daily-optimal-squads site, repo `sertalpbilal/fpl_optimized`). A Google
Colab notebook wraps the same code for browser use. Results can be exported as
`fpl.team` planner deep links (`get_fplteam_link` in `run/solve.py`). Related repos:
`fpl_hindsight_optimization` (perfect-information season solves), `fpl_optimized_data_archive`.
The whole stack is ~3,200 lines of Python: `dev/solver.py` (1,145 lines, the MIP),
`dev/data_parser.py` (336, projection ingestion), `run/solve.py` (374, CLI),
`run/sensitivity.py` (453, plan aggregation), `run/simulations.py` (147, Monte Carlo
driver), `run/run_parallel.py` (52, chip what-ifs). Modeling is **highspy directly**
(not PuLP): `m = highspy.Highs()`, `m.addVariables(...)`, `m.qsum`, with an
optional Gurobi path via MPS file export.

## 1. The multi-period MIP formulation

Everything below is from `solve_multi_period_fpl(data, options)` in `dev/solver.py`.

### 1.1 Sets and indexing

- `players` — index of the (filtered) merged projection frame, keyed by FPL element id.
- `gws = range(next_gw, last_gw + 1)`, `last_gw = next_gw + horizon - 1`, capped at
  `MAX_GAMEWEEK = 38`. `all_gw = [next_gw - 1, *gws]` — the extra week holds initial conditions.
- `order = [0, 1, 2, 3]` — bench slots (0 = bench GK).
- `ft_states = [0, 1, 2, 3, 4, 5]` — one-hot encoding of the banked-FT count (post-2024 rules, max 5).
- `price_modified_players` — the subset of the current squad whose `selling_price != now_cost`
  (i.e. players carrying a sell-on-fee discount).

### 1.2 Decision variables (their exact names)

| Variable | Type | Meaning |
|---|---|---|
| `squad[p, w]` | bin, over `all_gw` | player owned entering/at GW w |
| `squad_fh[p, w]` | bin | one-week Free Hit squad (empty unless `use_fh[w]=1`) |
| `lineup[p, w]` | bin | starts (11, or 15 under Bench Boost) |
| `captain[p, w]`, `vicecap[p, w]` | bin | armband |
| `bench[p, w, o]` | bin | bench slot o ∈ {0,1,2,3} |
| `transfer_in[p, w]` | bin | bought this GW |
| `transfer_out_first[p, w]` | bin, only `price_modified_players` | sold **at selling_price** (first sale) |
| `transfer_out_regular[p, w]` | bin | sold at current price |
| `transfer_out[p, w]` | expression | `transfer_out_regular + transfer_out_first` |
| `in_the_bank[w]` (`itb`) | continuous ≥ 0 | bank, in £m floats |
| `fts[w]` (`ft`) | integer 0..5 | free transfers available at GW w |
| `ft_above[w]`, `ft_below[w]` | bin | big-M indicators for the FT clamp |
| `ft_state[w, s]` | bin | one-hot: `fts[w] == s` |
| `pt[w]` (`penalized_transfers`) | integer ≥ 0 | hits taken |
| `trc[w]` (`transfer_count`) | integer 0..15 | transfers excluding wildcard weeks |
| `use_wc[w]`, `use_bb[w]`, `use_fh[w]` | bin | chip played this GW |
| `use_tc[p, w]` | bin | triple captain **on player p** (note: per-player, `use_tc[p,w] <= captain[p,w]`) |
| `aux[w]`, `no_transfer[w]`, `cp_v[...]`, `daux[t,w]`, `gw_with_tr[w]` | bin | helpers for optional constraints |

HiGHS has no binary type, so binaries are `kInteger` with bounds [0,1] (`BIN = highspy.HighsVarType.kInteger`).

### 1.3 Objective — the famous decay formulation

Per gameweek:

```python
gw_xp[w] = sum( points_player_week[p, w] * ( lineup[p,w] + captain[p,w]
             + vcap_weight * vicecap[p,w] + use_tc[p,w]
             + sum(bench_weights[o] * bench[p,w,o] for o in order) ) for p in players)

gw_total[w] = gw_xp[w] - hit_cost * penalized_transfers[w] + gw_ft_gain[w]
              - ft_penalty[w] + itb_value * in_the_bank[w] - cp_penalty[w]
```

and the horizon objective is either `"regular"` (plain sum) or the default `"decay"`:

```python
objective_expr = sum( gw_total[w] * pow(decay_base, w - next_gw) for w in gws )
```

Semantics of each knob (defaults from `comprehensive_settings.json`):

- **`decay_base`** (default in code 0.84; shipped settings 0.9): geometric discount on
  future GWs. Captain counts as `lineup + captain` = 2x points; TC adds a third multiple.
- **`bench_weights`** `{"0": 0.03, "1": 0.21, "2": 0.06, "3": 0.002}`: fixed per-slot
  probabilities that a benched player's points count (slot 0 = bench GK). Pure constants —
  no autosub model behind them.
- **`vcap_weight`** 0.1: vice-captain gets a flat 10% of xPts — *not* a
  P(captain blanks) term.
- **`ft_value`** 1.5 / **`ft_value_list`** `{"2": 2, "3": 1.6, "4": 1.3, "5": 1.1}`:
  marginal value (in points) of rolling into each banked-FT state. Implemented as a
  telescoping potential: `ft_state_value[s] = ft_state_value[s-1] + ft_value_list.get(str(s), ft_value)`,
  `gw_ft_value[w] = sum(ft_state_value[s] * fts_state[w,s])`, and the objective adds
  `gw_ft_gain[w] = gw_ft_value[w] - gw_ft_value[w-1]`. So banking a transfer earns the
  marginal state value once, and spending it pays it back — a terminal-value correction
  for horizon truncation.
- **`ft_use_penalty`** (0.2 in comprehensive settings, `None` in code default): flat
  penalty times `transfer_count[w]`, "prevents trivial scheduled transfers".
- **`hit_cost`** 4: points per hit; `penalized_transfers[w] >= num_transfers[w] - fts[w] - 15*use_wc[w]`.
- **`itb_value`** 0.08: points per £1.0m left in the bank, per GW (so it compounds over
  the horizon through the decayed sum).
- **`itb_loss_per_transfer`** (default 0): drains the bank by this much per scheduled
  future transfer — a robustness haircut for future budget flexibility.
- **`report_decay_base`** `[0.85, 1.0, 1.017]`: after solving, the same `gw_total` is
  re-scored under alternative decay bases (`decay_metrics`) — note 1.017 > 1, i.e. a
  future-weighted metric, reported but never optimized.

### 1.4 Squad / lineup constraints

Standard FPL rules, with FPL's own `element_types` metadata rather than hardcoded
numbers for positional bounds: `squad_select`, `squad_min_play`, `squad_max_play`
come from `bootstrap-static` (`type_data`). Key lines:

- `squad_count[w] == 15`; `lineup` sums to `11 + 4*use_bb[w]` (Bench Boost lifts the XI to 15).
- `sum(bench[p,w,0] for GKs) == 1 - use_bb[w]`; slots 1-3 similarly vanish under BB.
- `lineup[p,w] <= squad[p,w] + use_fh[w]` and `lineup[p,w] <= squad_fh[p,w] + 1 - use_fh[w]`
  — the lineup draws from `squad` normally and from `squad_fh` in a Free Hit week.
- 3-per-club (`MAX_PLAYERS_PER_TEAM = 3`) — with a clever escape hatch: if the *current*
  squad already violates it (`max_players_from_team > 3`, possible after ownership
  migrations), the constraint is relaxed in any week with zero transfers via a
  `no_transfer[w]` indicator.
- `captain + vicecap <= 1`, both `<= lineup`; `lineup + sum(bench slots) <= 1`.

### 1.5 Transfer flow, budget, and the sell-price treatment

- Flow conservation: `squad[p,w] == squad[p,w-1] + transfer_in[p,w] - transfer_out[p,w]`.
- Bank: `in_the_bank[w] == in_the_bank[w-1] + sold_amount[w] - bought_amount[w]` where
  `sold_amount` uses `sell_price[p]` for `transfer_out_first` (initial squad, discounted
  sell value) and `buy_price[p]` for `transfer_out_regular`.
- **Prices are static floats.** `buy_price = merged_data["now_cost"].div(10)` — pounds as
  floats, constant across the whole horizon. A player bought mid-horizon sells at exactly
  his buy price. Sell-on fees only exist for the initial squad, imported from the team
  JSON's `selling_price`. Future price changes are out of model (there is a
  `price_changes` what-if setting that just perturbs `now_cost` before the solve).
- **Multiple-sell fix**: because a `price_modified_player` could in principle be sold at
  the discounted price, re-bought at market, and sold again, three constraints force
  `transfer_out_first` to be usable at most once and only before any regular sale:
  `transfer_out_first + transfer_out_regular <= 1` per week,
  `sum(transfer_out_first) <= 1` over the horizon, and an ordering constraint
  `horizon * sum(first sales up to wbar) >= sum(regular sales from wbar)`.
- Free Hit budget: the FH squad must be affordable at the previous squad's *sell* values:
  `sum(fh_sell_price[p] * squad[p,w-1]) + itb[w-1] >= sum(fh_sell_price[p] * squad_fh[p,w])`,
  and `transfer_in/out` are zeroed during the FH week so the owned squad passes through.

### 1.6 Free-transfer carryover (the 0-5 banked-FT state machine)

```python
raw_gw_ft[w] = fts[w] - transfer_count[w] + 1 - use_wc[w] - use_fh[w]
```

(a WC or FH cancels the +1, so FTs carry unchanged through a chip week), then
`fts[w+1] = clamp(raw_gw_ft[w], 1, 5)` implemented with big-M (`big_m = 20`) indicator
pairs `ft_above[w]` / `ft_below[w]`. Initial condition:
`fts[next_gw] == initial_ft * (1 - use_wc[next_gw]) + ft_base * use_wc[next_gw]`, and
`fts[w] >= 1` for all later weeks. The FT count feeds the one-hot `ft_state[w,s]` used
by the `ft_value_list` objective term. The *current* FT count is reconstructed from
the transfer history API in `calculate_fts()` (walks every past GW: `-transfers`,
`max(.,0)`, `+1`, `min(.,5)`, chips skip the decrement).

### 1.7 Chips as binaries

- Mutual exclusion: `use_wc[w] + use_fh[w] + use_bb[w] + use_tc_gw[w] <= 1`.
- Budget: `sum(use_X[w]) <= chip_limits[X]` — all default **0**; a chip must be enabled.
- Four ways to control scheduling (all in settings):
  - `use_wc/use_bb/use_fh/use_tc: [gw, ...]` — **force** the chip in exactly those GWs
    (equality constraints; also bumps `chip_limits`).
  - `allowed_chip_gws: {"wc": [25, 27], ...}` — chip *may only* fire in those GWs
    (zeroed elsewhere, limit set to 1) — the solver picks which.
  - `forced_chip_gws: {"wc": [25, 27], ...}` — chip *must* fire in exactly one of them.
  - `no_chip_gws: [w, ...]` — no chip at all in those weeks.
- If the live team API reports `"status_for_entry": "active"` for a wildcard, the solve
  auto-forces `use_wc = [next_gw]`.
- TC is per-player (`use_tc[p,w] <= captain[p,w]`), giving the captain a third xPts multiple.

## 2. Projection input: the "Review format" CSV

`dev/data_parser.py::read_data` dispatches on `datasource` and funnels **everything**
into one schema — the format FPL Review (fplreview.com, the paid projections the
community standardized on) exports, hence `read_fplreview` is a bare `pd.read_csv`.

**The expected frame, one row per player:**

| Column | Meaning |
|---|---|
| `ID` | FPL element id (`bootstrap-static` `elements[].id`) — the merge key: `pd.merge(elements_team, data, left_on="id_x", right_on="ID")` |
| `Name` | display name (informational) |
| `Pos` | position — canonical single letters `G/D/M/F`; the parser normalizes `GKP/GK→G, DEF→D, MID→M, FWD→F` |
| `Value` | price in £m (e.g. `11.5`) |
| `Team` | team name, must match FPL team names (used by binary-fixture generator and mixed-source fill) |
| `{gw}_Pts` | expected points for that GW, e.g. `8_Pts` … `15_Pts` (missing column for a GW in the horizon is a hard error) |
| `{gw}_xMins` | expected minutes for that GW |
| `fpl_id` | optional duplicate of ID (present in solio/mixed exports) |

Supported sources: `review` (as-is), `solio` (as-is, "TODO more complex parsing"),
`mikkel` (Mikkel Tokvam's "Transfer Algorithm" spreadsheet — a 200-line
`convert_mikkel_to_review` does fuzzywuzzy `token_set_ratio` name matching against
`web_name`/full name within (team, position) blocks, `;`-vs-`,` delimiter sniffing,
`BCV` column cleanup, and writes `mikkel_cleaned.csv`), and `mixed` — a weighted
ensemble: per-source `{gw}_weight` columns, EV and xMins multiplied by weight, summed
per `real_id` via `groupby.agg`, divided by the weight sum, missing players appended
with zeros (`"Missing": 1`), exported to `export_data` (default `mixed.csv`).

**Implication for our free-source ensemble:** emitting
`ID, Name, Pos, Value, Team, {gw}_Pts, {gw}_xMins` makes our projections a drop-in
`datasource` for the entire community toolchain (their solver, fpl.team, every
sensitivity script people have built) and gives us free A/B comparability against
Review/Solio consensus. Note their loss of information relative to ours: minutes as a
point estimate `xMins` (they derive nothing from it in-model except pool filtering and
the randomization heuristic), no `p_play`, no distributions.

**Team state input** (`team_data`: `"id" | "json" | "json_string"`): the
`my-team/{id}` JSON shape — `picks[].element / purchase_price / selling_price` (integer
tenths!), `transfers.bank / limit / made`, `chips[].status_for_entry`. When only a
public `team_id` is given, `generate_team_json` reconstructs purchase prices by
replaying the full transfer history from GW1 (`now_cost - cost_change_start` start
prices, then each transfer's `element_in_cost`), and computes the sell price as
`purchase_price + diff // 2` — integer-floored in tenths, correctly — before the
solver divides everything by 10 into floats.

## 3. Where the public SOTA stops (the gap that is our thesis)

The README's own words: "solving **deterministic** Fantasy Premier League (FPL)
optimization problems." Precisely:

1. **The objective is mean expected points.** There is no rank objective anywhere —
   no notion of overall rank, mini-league opponents, or a utility over outcomes. The
   only "field" awareness in the entire repo is cosmetic (`sensitivity.py` prints a
   column literally named `PSB` — "percent selected by" *plans*, i.e. how often the
   solver's own runs picked a player — not effective ownership of real managers).
2. **No variance, no covariance, no distributions.** Projections are consumed as a
   single point estimate per (player, GW). `xMins` is carried but never used to model
   appearance risk in the objective; the vice-captain weight is a flat 0.1 and the
   bench weights are four constants. Captaincy is chosen on E[points] alone — a
   variance-seeking captain pick (the actually-rank-optimal behavior when behind) is
   inexpressible.
3. **Uncertainty is handled by rerunning the solver, not by modeling it.** The two
   mechanisms are (a) `randomized: true` — additive noise
   `Pts * (92 - xMins) / 134 * N(0,1) * randomization_strength` injected into the
   projections before each solve (an ad-hoc heuristic: uncertainty scales with expected
   *absence*), and (b) "binary files" — hand-authored alternative fixture-calendar CSVs
   with scenario weights (`binary_file_weights: {"binary1.csv": 0.6, ...}`) for
   BGW/DGW rearrangement risk. Both produce a *pile of plans* whose agreement
   frequencies (`PSB`, `ITER_SCORING = {0: 10, 1: 9, 2: 8}`) are read as robustness.
   No plan is ever evaluated against a distribution; the ensemble is of argmaxes, not
   of outcomes.
4. **No autosub model.** Bench slot activation probabilities are user-set constants,
   never derived from anything, and there is no formation-legality condition on subs.
5. **No price dynamics.** Prices are frozen at `now_cost` for the whole horizon;
   `price_changes` is a manual what-if, not a forecast. Value captured from buying
   pre-rise / selling pre-fall is out of scope.
6. **No point-in-time discipline.** The solver fetches `bootstrap-static` live and
   caches per-day (`cached_request`); there is no snapshotting, no leakage guard, no
   backtest harness in the repo (hindsight solves live in a separate repo and use
   *actual* points — perfect information, explicitly not a backtest of a policy).
7. **Solution honesty is partial.** `gap` and `secs` are settings, but a timeout with
   no incumbent is not distinguished from a solved model when reading out values
   (`BINARY_THRESHOLD = 0.5` reads whatever is in the solution vector).

Everything in 1-3 is exactly the territory `fpl_edge` is built for: rank utility as a
functional of the joint (our score, field score) distribution, simulator-derived
autosub/vice terms, and PIT-snapshotted inputs. The public SOTA is a *planning UI over
paid point estimates*; it is genuinely excellent at the MIP mechanics and genuinely
absent on decision theory under uncertainty.

## 4. Practical solver lessons worth stealing

- **Solver choice**: HiGHS by default, via `highspy` directly (not a modeling layer).
  `parallel: "on"`, `presolve: "on"`, `mip_rel_gap` (default **0** — they solve to
  proven optimality by default), `time_limit` default 600s in settings (20min in code).
  `random_seed` is a setting — they know parallel MIP is seed-sensitive. The
  `solver: "gurobi"` path writes an `.mps` file and shells out to `gurobi_cl`,
  reading the `.sol` file back by variable name — no gurobipy dependency.
- **Candidate pruning is aggressive and default-on** (`prep_data`): drop players with
  `total_min < xmin_lb` (default 300 expected minutes over the horizon), drop the bottom
  `ev_per_price_cutoff` percentile of EV-per-price (default 30%), but *always protect*
  a `safe_players` list = initial squad + `locked` + `keep` + `locked_next_gw` + players
  at `pick_prices` price points + top `keep_top_ev_percent` (5%) by total EV + anyone in
  `booked_transfers`. Prints "Filtered player pool from X to Y". This is the main reason
  8-GW horizons solve in minutes: the pool typically drops from ~700 to 100-200.
- **Alternative-solution enumeration**: `num_iterations` re-solves after adding a
  no-good cut on the incumbent; `iteration_criteria` picks the cut:
  `this_gw_transfer_in`, `this_gw_transfer_out`, `this_gw_transfer_in_out`, `chip_gws`,
  `target_gws_transfer_in`, `this_gw_lineup` (with `iteration_difference` = min Hamming
  distance). This is how users get "top 5 plans" tables. Auto-fallback: if next GW is a
  forced FH, transfer-based criteria switch to `this_gw_lineup` (no transfers exist to cut on).
- **What-if workflows users actually run**:
  - `run_parallel.py`: cartesian product of chip weeks (e.g. `use_bb: [None, 1, 2]` x
    `use_fh: [None, 2, 3, 4]`), one solve each on a `ProcessPoolExecutor`, results
    concatenated and ranked by objective `score` → "which chip schedule wins".
  - `simulations.py` + `sensitivity.py`: N randomized solves (optionally over weighted
    fixture-scenario binaries), then pivot tables of buy/sell/move frequency across
    plans — decision by plan-consensus rather than single argmax.
  - Single-knob what-ifs via CLI: every setting is a `--flag` (argparse is generated
    *from the settings dict*), plus `--config a.json;b.json` layering. `booked_transfers`
    (`[{"gw": 5, "transfer_in": 427}]`), `price_changes`, `override_next_gw`, `preseason`.
- **Read-out hygiene**: one `m.getSolution().col_value` copy per solve (they note
  `m.val` re-copies per call); binaries read with `> 0.5`.
- **Objective-shaping constants beat constraints** for soft preferences: FT state value,
  ITB value, FT-use penalty, opposing-play penalty (`opposing_play_penalty` 0.5 with
  `cp_v` product binaries) — all tunable without touching feasibility.
- **Output artifacts**: per-GW picks CSV (`{source}_{timestamp}_{run_id}_{iter}.csv`
  with columns `id, week, name, pos, type, team, buy_price, sell_price, xP, xMin, squad,
  lineup, bench, captain, vicecaptain, transfer_in, transfer_out, multiplier, xp_cont,
  chip, iter, ft, transfer_count`), an append-only `solutions_file` log
  (`run_id, iter, user_id, datasource, wc, bb, fh, tc, p1..p15, cap, vcap, sell, buy,
  score, datetime`), squad-timeline images, and fpl.team deep links.

## 5. Comparison: FPL-Optimization-Tools vs `fpl_edge/opt`

Ours read from `fpl_edge/opt/problem.py`, `milp.py`, `config.py`, `interfaces.py`.

| Dimension | sertalpbilal / open-fpl-solver | fpl_edge/opt |
|---|---|---|
| Modeling layer | highspy direct | PuLP → HiGHS (CBC fallback) |
| Objective | decayed E[pts] + FT/ITB potentials (`decay_base^(w-next_gw)`) | `ObjectiveMode` mandatory: `EXPECTED_POINTS` surrogate or `RANK_UTILITY` (raises until provider wired — refuses to lie) |
| Future discounting | `decay_base` geometric, + `report_decay_base` re-scoring | `gw_discount` tuple exists but no geometric convenience, no default, no re-scoring |
| FT carryover | 0-5 state machine, big-M clamp, `ft_value_list` marginal state values in objective | exact `ft`/`paid` integer carryover with `ft_retain` cap; **no value on banked FTs or ITB** — horizon-truncation bias unaddressed |
| Hits | `hit_cost * penalized_transfers`, `hit_limit`, `weekly_hit_limit` | `hit_cost * paid[j]` from rule registry; no hit-limit knobs |
| Sell prices | floats, initial squad only, static over horizon; multiple-sell fix | **integer tenths throughout** (`price_tenths` dtype-checked); `purchase[i,j]` tracked per GW so mid-horizon buys sell correctly under forecast price paths; exact `2*sale <= purchase + price` linearization; independent ledger replay (`_with_exact_ledger`) |
| Price dynamics | none (static `now_cost`; manual `price_changes`) | `PriceForecast` protocol — per-GW price paths are first-class inputs |
| Chips | binaries with force/allow/forbid GW lists; TC per-player; WC auto-detect from API | binaries with availability windows *per season half* from rule registry, played-chip state, FH-not-consecutive; TC/BB exact linearizations with tight per-GW ceilings; **no user-facing force/allow/what-if knobs** |
| Bench | 4 constant `bench_weights` | simulator-shaped `AutosubWeights` (+ `from_blank_rate` binomial), nested `b1`/`b12` slot binaries with monotonicity guarantee, toggleable |
| Vice-captain | flat `vcap_weight = 0.1` | exact `P(captain blanks) x xPts(vice)` bilinear linearization using `p_play` |
| Uncertainty | projection noise reruns + weighted fixture binaries | `p_play` in-model; rank-utility/simulator hooks (`RankUtilityProvider`); no scenario reruns yet |
| Pool pruning | xMins + EV/price percentile + safe list, default on | `prune(max_per_position)` top-N by horizon xPts + owned, **off by default** ("pruning changes the answer") |
| Locked/banned players | `locked`, `banned`, `locked_next_gw`, `banned_next_gw`, `keep`, `pick_prices`, positional bans | **none** |
| Booked transfers | `booked_transfers`, `only_booked_transfers`, `num_transfers`, `no_transfer_gws`, `future_transfer_limit` | **none** |
| Alternative plans | `num_iterations` + 6 no-good-cut criteria | **none** |
| Sensitivity tooling | simulations + PSB pivot tables + parallel chip sweeps | **none** |
| Solution honesty | reads solution vector at 0.5 threshold regardless of status | `NoIncumbentError` vs `InfeasibleError` distinction, HiGHS `primal_solution_status` check, true `mip_gap` surfaced with a note, armband repair for tolerance-rounded incumbents |
| Data discipline | live API + day-cache; projections = paid CSVs | PIT `Snapshot`, injected forecast protocols, `selectable()` universe, NaN/shape validation, rule registry (no hardcoded rules) |
| Team-state import | full transfer-history replay → purchase/sell prices + FT count | `SquadState` must be supplied by caller (no FPL-API importer in `opt/`) |
| Determinism | `random_seed` setting, parallel on | single-threaded by default *because* parallel MIP is non-deterministic |
| Fixture-move scenarios | binary CSV generator + weighted sims | none (would live upstream in forecasts) |
| Opposing-play / GK-rotation / double-defense style prefs | yes (constraints + penalties) | none |

## 6. Recommendations: shortest adoption list for `fpl_edge/opt`

Ordered by value-per-line-of-code. 1-4 are objective/constraint changes inside the
existing MILP; 5-6 are thin layers above it.

1. **Terminal-value potentials: banked-FT value and ITB value.** Their single most
   important objective idea. Without it, our model spends every FT and every tenth by
   the last GW of the horizon because leftover resources are worth 0 — a pure
   truncation artifact. Add a `ft_state_value` telescoping term (we already have the
   `ft[j]` integer; one-hot it or use a concave piecewise bound) and an
   `itb_value * bank[j]` term, both under `OptimizerConfig` with their community-tuned
   defaults (`{2: 2, 3: 1.6, 4: 1.3, 5: 1.1}`, 0.08) as starting points to recalibrate
   against our own sims. Cheap: reuses existing variables.
2. **Geometric discount convenience + report-decay re-scoring.** `gw_discount` already
   exists; add `OptimizerConfig.decay_base` sugar producing `(1, b, b^2, ...)` and score
   the returned plan under `report_decay_base`-style alternates via our existing
   independent scorer. Rationale: forecast trust decays; the community default 0.84-0.9
   encodes that, and rescoring makes horizon-sensitivity visible for free.
3. **Locked / banned / booked-transfer constraints.** Three fields on the problem or
   config (`locked: {code}`, `banned: {code}`, `booked: [(gw, in_code?, out_code?)]`),
   each a one-line bound or equality on existing `own`/`buy`/`sell` variables. This is
   the entire "what-if" UX their users live in (`locked`/`banned` are the two settings
   in *user*_settings.json), and it is a precondition for any interactive planner on
   our platform. Must feed the pruning safe-list like theirs does.
4. **Chip-schedule control: forced/allowed chip GW sets.** We model chips correctly but
   give the caller no way to ask "WC in 28-31, where exactly?" — their
   `allowed_chip_gws` / `forced_chip_gws` / `no_chip_gws` triple, implemented as bounds
   on our existing `chip[c, j]` binaries. Enables recommendation 5.
5. **Parallel what-if sweeps + alternative-plan enumeration.** A `run_parallel`-style
   driver (chip-week cartesian products; we hold the model immutable so
   `ProcessPoolExecutor` is trivial) and `num_iterations` no-good cuts — start with the
   two criteria that cover most usage: `this_gw_transfer_in_out` and `chip_gws`. The
   deliverable users actually want is "top-k plans and how often each move appears",
   which our weekly report can consume directly. Their PSB tables are the
   interpretability layer our rank-sim outputs should imitate (but weighted by utility,
   not plan-count).
6. **Review-format CSV export of our ensemble projections** (`ID, Name, Pos, Value,
   Team, {gw}_Pts, {gw}_xMins` — Pos in G/D/M/F, Value in £m, xMins = 90*p60-ish
   summary of our minutes model). Not a solver feature but the highest-leverage
   compatibility move: makes our free projections drop into their solver, fpl.team and
   every community sensitivity script, and gives us like-for-like benchmarking against
   Review/Solio.

**Deliberately not adopting**: their `randomized` noise heuristic
(`Pts * (92 - xMins)/134 * N(0,1)`) — our uncertainty belongs in the simulator with a
real joint distribution, and resampling argmaxes of perturbed means is a weaker,
biased substitute for evaluating plans under the distribution; their float-pound
arithmetic; static horizon prices; flat vice-captain weight; and constant bench
weights — in each case what we already have is strictly more exact, and the
comparison table is the evidence.

