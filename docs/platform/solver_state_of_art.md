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
