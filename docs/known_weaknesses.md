# Known weaknesses

Maintained by the adversarial audit team. This is not a roadmap and not a
disclaimer. It is the list of ways this engine can be wrong while looking right.

Every claim here is backed by an executable test in `tests/audit/`. Run them:

```
uv run pytest tests/audit -q
uv run python scripts/audit_leakage.py
```

A **failing** audit test is a live defect, not a broken test. The suite is
written so that a red line names the failure mode and the file that causes it.

Audit performed against `main` on 2026-08-18, before the 2026-27 GW1 deadline
(2026-08-21T17:30:00Z), at commit `49a7d82`. Several teams are committing
concurrently and a number of the defects below were fixed while this was being
written -- those are marked FIXED and kept, because the regression test is the
point. Trust the test names over the line numbers.

At the time of writing: **70 audit tests, 14 failing.** Each failure is a live
defect named in the section below.

---

## 0. The GW1 cold start

**This is the largest weakness in the system and it cannot be engineered away.**

At the moment the engine must produce its first recommendation, the 2026-27
season has zero minutes played. Concretely, at the GW1 deadline:

- **No current-season form.** `Snapshot.results_before("2026-27")` is empty.
  Every form, momentum and minutes-share feature is undefined or falls back to a
  prior. `last_weeks_best_scorer` returns a flat zero for all 592 players, and
  the greedy squad builder then picks on whatever its tie-break happens to be
  (`tests/audit/test_recency_chasing.py::test_at_gw1_there_is_no_last_week_to_chase`).
- **No price signal.** `prices.no_change_before_season` is a verified rule:
  prices are frozen until the season starts, so `cost_change_start` is 0 for
  every player. Price-momentum features carry exactly zero information
  (`test_price_is_flat_before_the_season_starts`).
- **Ownership is measured against a moving denominator.** The rule registry
  records `total_players_at_fetch: 5,896,644` with the explicit note that the
  field is still growing; the archived bootstrap from the same day already says
  5,898,206. Every `selected_by_percent` is a share of a number that will be
  materially larger by Friday. Effective ownership — the input to the entire
  rank-utility objective — is therefore calibrated against a denominator known
  to be wrong at precisely the moment it matters most.
- **Preseason ownership is not the field's real position.** A large fraction of
  managers do not finalise their squad until the last hours before the deadline.
  Ownership at T-3 days is the position of the early, engaged, non-representative
  minority. Modelling the field from it is modelling a different field.
- **Everything is extrapolation across a transfer window.** New signings have no
  Premier League minutes. Managers have changed. Set-piece and penalty duties are
  unresolved. Promoted clubs have no top-flight matches at all — the model has an
  explicitly fitted prior for them
  (`fpl_edge/models/team_goals/promoted.py`), which is the honest treatment, but
  a prior is not data.
- **No amount of historical data fixes this.** Historical seasons have now been
  loaded, which helps with team strength and player-level rates. It does not tell
  you who starts for Sunderland on 21 August 2026.

**What this means in practice.** Any GW1 confidence interval produced by this
engine is a statement about parameter uncertainty inside a model whose
assumptions are least supported at GW1. It is not a statement about how likely
the recommendation is to be right. Treat GW1 output as a structured prior, not
as a forecast, and expect the model's edge to be smallest in GW1–GW4 and to grow
as current-season data accumulates.

---

## 1. The Snapshot boundary is a convention, not an enforcement

`Warehouse.snapshot_at`'s docstring says it "guarantees the caller cannot see a
price, ownership figure, injury update, lineup or result that was not public at
that moment". It guarantees no such thing.

`Snapshot` is a frozen dataclass whose first field is the `Warehouse` itself. Any
model handed a Snapshot can write:

```python
snapshot.warehouse.sql("SELECT * FROM fact_player_fixture")
```

and read the entire future, including gameweeks that have not been played at the
snapshot instant. `LeakageError` is declared in `fpl_edge/store/warehouse.py` and
is never raised anywhere in the store layer.

`Snapshot` is also directly constructible: `Snapshot(wh, naive_datetime)` builds
one with no timezone validation at all, bypassing the check that `snapshot_at`
performs.

- Tests: `test_leakage_pit.py::test_snapshot_does_not_expose_the_raw_warehouse`,
  `::test_snapshot_cannot_be_handed_a_future_as_of_through_its_constructor`
- Mitigation in place: `scripts/audit_leakage.py` rule `SNAPSHOT_ESCAPE` fails CI
  on `snapshot.warehouse.*` in a model module. That is a lint, not a boundary.
- The lint already has to grant an exception. `models/ownership/model.py`
  legitimately reaches `snapshot.warehouse.snapshot_at(as_of - lookback)` to read
  an *earlier* state, which cannot leak because an earlier as-of reveals strictly
  less. That is a real need with no sanctioned API, so it is baselined. **The fix
  is on `Snapshot`: give it a `rewind(timedelta)` method.** Until it has one,
  this rule cannot be enforced without exceptions, and a rule with exceptions is
  a rule people learn to add themselves to.

## 2. `as_of` is never checked against the event it describes

The whole point-in-time design rests on `as_of` meaning "first publicly
observable". Nothing validates it. `Warehouse.append` checks that `as_of` is
present, non-null and (loosely) tz-aware, and stops there.

A historical loader that stamps every 2024-25 result with the backfill date —
which is the natural thing to write — makes an entire season's results visible at
a single instant, and the `as_of <= t` filter waves it through because the filter
only compares `as_of` to the snapshot instant, never to the kickoff.

Specifically, the warehouse will accept:

- a `fact_player_fixture` row whose `as_of` precedes its own fixture's kickoff;
- a `fact_fixture` row carrying a final score with an `as_of` two weeks before
  the match.

`fpl_edge/models/team_goals/data.py` defends against this at *read* time for
fixtures and raises `LeakageError` — good, and the only such defence in the tree.
Nothing defends `fact_player_fixture` at all, and no defence exists on the write
path where it would catch the bug once rather than per-consumer.

- Tests: `test_leakage_pit.py::test_result_stamped_before_its_own_kickoff_is_refused`,
  `::test_finished_score_stamped_before_kickoff_is_refused`

## 3. Timezones

**Fixed while this audit was being written, now regression-tested.** DuckDB
renders `TIMESTAMPTZ` in the connection's session timezone, which it takes from
the host OS. Before `d450316` pinned the session to UTC, `Snapshot.deadline` on a
US/Pacific machine returned the GW1 deadline as
`2026-08-21 10:30:00-07:00` — which formats as **"Fri 21 Aug 10:30"**, the exact
string the rule registry records as the already-caught rules-page bug. The
`Deadline` type rejected it outright. The instant was always correct; every
human-readable rendering of it was not.

The regression guard runs the read in a subprocess under
`TZ=America/Los_Angeles`, so it fails on any machine if the pin is ever removed.

**Still open:**

- `Warehouse.append` validates tz-awareness for `as_of` and for *nothing else*.
  `deadline_utc`, `kickoff_utc` and `news_added` are `TIMESTAMPTZ` columns; a
  naive value written to one is silently interpreted in the session timezone.
  This is accidentally correct today only because the session is pinned. **Still
  open** for every column except `as_of`.
- `fpl_edge/ingest/fpl_api.py` `_ts()` returns a **naive** datetime for any
  timestamp string without a `Z` suffix. That is the live entry path for the bug
  above, and FPL's `news_added` has historically been served both ways. **Still
  open.**
- FIXED: the write path used to be laxer than the read path — `append` ran a
  naive `as_of` through `pd.to_datetime(..., utc=True)`, which *localises*
  rather than converts and never complained, so the awareness check could not
  fire. It now checks awareness before converting.

**BST/GMT is real in this season's data.** GW30's deadline is
`2027-03-20T13:30Z` and GW31's is `2027-04-10T12:30Z`. Both are 13:30 UK local —
BST begins 2027-03-28. Any feature keyed on the UTC hour of a deadline or kickoff
sees a one-hour discontinuity that is not a football fact. Similarly, the
verified rule `points_final_at` is *"09:00 UK"*: 08:00Z in summer, 09:00Z in
winter. Implemented as a fixed 08:00Z it makes results visible an hour early for
two-thirds of the season, which is leakage.

- Tests: `test_time_and_deadlines.py` (10 tests)

## 4. Cross-season identity

The obvious bug is absent and well defended: `element_id` appears in no
point-in-time key, `fpl_edge/types.py` documents the distinction at length, and
`fpl_edge/ingest/player_mapping.py` is a careful, collision-fatal element→code
resolver that refuses ambiguous name joins rather than guessing.

What is still open:

- **`has_temporary_code`** — FIXED while this was being written. Ingest now
  records the flag, counts the affected players and raises a `RuntimeWarning`
  naming them. The underlying hazard is unchanged: when FPL reissues the code,
  that player's history still splits in two, and the warning is the only thing
  that will tell you to re-map. It fires in January, not in August.
- **Season labels** — FIXED. `registry.yaml` now declares `season: "2026-27"`,
  matching `ingest.fpl_api.season_label()`. Previously the registry said
  `"2026/27"` and any filter using it matched zero rows and returned an empty
  frame rather than an error.
- **Two players can silently become one.** `Warehouse.append` deduplicates on the
  primary key, not on row equality, despite its docstring saying "dropping exact
  duplicates already present". Append a second `dim_player` row with the same
  `(season, code, as_of)` but a different `element_id` and a different name and it
  returns `0` and reports success. Two careers merge and nothing is raised,
  logged or counted. `player_mapping.py` raises `IdentityCollisionError` for this
  — but only for data flowing through its own index, and every other loader
  writes straight to the warehouse.
- **A corrected fact is silently discarded.** Same mechanism. FPL reassigns
  assists and rescinds red cards. A corrected row arriving with the same `as_of`
  as the original — which any date-granular backfill produces — is matched on the
  PK, treated as "already present", and the **wrong** value is kept. `append`
  returns 0, which the caller reads as successful idempotency.
- **The NewType defence is documentation.** `PlayerCode` and `ElementId` are
  "deliberately distinct NewTypes so a mix-up is a type error". At runtime both
  are `int` and the mix-up is free. The defence is real only where mypy runs, and
  `make lint` runs mypy over `fpl_edge` only — not tests, not scripts.

- Tests: `test_cross_season_identity.py` (8 tests)

## 5. Survivorship, in both directions

- **Departed players never leave.** The warehouse is append-only and
  `Snapshot.table` returns the latest row per entity with no upper bound on its
  age. FPL removes an element from the bootstrap when a player leaves the league;
  his last observed row then remains "the latest known value" forever.
  `Snapshot.players()` at GW33 will therefore offer a January departure at his
  January price with `status='a'`, and the optimizer will buy him. There is no
  staleness column, no maximum age, and no `last_seen` for a caller to check.
- **`players()` is not filtered to selectable players.** It is documented as
  returning "squad-selectable players" and applies no selectability filter. In
  the live 2026-27 bootstrap, 32 of 592 elements carry `can_select: false` and
  `status: 'u'`. Neither `can_select` nor `removed` is ingested, so a caller
  cannot reconstruct the filter even if they know to. `Availability.is_selectable`
  exists in `fpl_edge/types.py` and nothing in the read path calls it.
- **Not a problem, and worth saying:** relegated clubs keep their history
  (`dim_team` is keyed per season), and "promoted" is defined as "no prior
  top-flight match on record" computed from the snapshot's own match history
  rather than from an external list of who is in the league now. That is the
  version that survives a walk-forward replay.

- Tests: `test_survivorship.py` (5 tests)

## 6. Fabricated values

The rule is not "never `fillna`". It is "a fabricated value must not be
indistinguishable from an observed one". Where the codebase gets this wrong:

- **Ownership.** FIXED, regression-tested. `fpl_edge/ingest/fpl_api.py` used to
  do `float(el.get("selected_by_percent") or 0.0)`, collapsing *absent field*,
  *empty string* and *a genuine 0.0%* into the same number. Ownership is the
  input to the whole rank-utility objective, and a player whose ownership failed
  to parse was scored as a 0%-owned differential — the most aggressive possible
  reading — rather than as unknown. A missing value now stores as NULL.
- **Availability assumed fit.** `fpl_edge/models/minutes/features.py` does
  `status.fillna("a")`, so an unknown availability reads as AVAILABLE and every
  derived flag (`status_flagged`, `status_injured`, `status_doubtful`) reports the
  optimistic answer. The error direction is the expensive one: the optimizer buys
  him. In the same file, `days_rest.fillna(7.0)` invents a full week of rest for a
  player whose previous fixture is unknown.
- **Injury probability defaults to certainty.** `fpl_edge/ingest/injuries.py`
  falls back to `play_prob = 1.0` when neither an explicit
  `chance_of_playing_next_round` nor a status prior is available. An unknown
  player is assumed certain to start.
- **Unknown ownership becomes a differential.** `fpl_edge/sim/engine.py` `_align`
  reindexes a column onto the player universe and zero-fills. For expected points
  that is conservative. For effective ownership it makes any player the field
  model has no row for into a pure differential, which is exactly backwards if
  the reason he is missing is that he is heavily owned by a segment the panel
  does not cover.
- **`dropna` on FPL data is never random.** `chance_of_playing_next_round` is NULL
  for every fit player and populated only for the flagged ones, so a bare
  `dropna()` over a feature frame keeps *only* the injury doubts. The reviewed
  uses in the tree are on summary columns rather than training rows; the static
  rule exists so a future one is not.

- Tests: `test_nan_and_imputation.py` (7 tests), plus the `FILL_CONST`,
  `LEAKY_IMPUTE` and `SILENT_DROPNA` rules in `scripts/audit_leakage.py`

## 7. Bonus and manager elements

Both currently correct, both one edit away from being wrong, both pinned:

- `total_points` already contains `bonus`. A BPS model that predicts bonus and
  adds it to a points model fitted on `total_points` inflates every premium
  captain by roughly two points a week — large enough to change every captaincy
  decision, small enough to look plausible. Guarded statically
  (`BONUS_DOUBLE_COUNT`) and arithmetically.
- Bonus allocation is **positional**, not distinct-value:
  `bonus = max(0, 3 - #players strictly above)`. The distinct-value version gets
  the tie-for-second and tie-for-third examples right and the tie-for-first
  example wrong, awarding 2 instead of 1 to the third player. All three official
  worked examples are now pinned as a test.
- `element_type == 5` (Manager, 2024-25 only) is refused by `Position.from_api`
  and dropped-with-a-count by bootstrap ingest.

**Still open:** `Snapshot.results_before` returns `fact_player_fixture`
unfiltered and joins nothing, so a result row whose `code` has no `dim_player`
entry — which is exactly the footprint a dropped manager element leaves — comes
back as a normal player-fixture result. Manager scoring in 2024-25 was
substantial and cannot be earned in 2026-27
(`misc.manager_scoring_removed` is verified true), so a model trained through
those rows learns a scoring system that no longer exists.

- Tests: `test_bonus_and_managers.py` (8 tests)

## 8. Determinism

Seeding discipline is good: no module uses the global `np.random` state. The
problem is elsewhere.

`Snapshot.table` issues a windowed query with **no `ORDER BY`**. SQL guarantees
nothing about the resulting row order, and DuckDB's actually varies with the
degree of parallelism, which defaults to the machine's core count. Measured on
this tree at 200k rows: `threads=1` and `threads=8` return the same rows in
different orders.

That is load-bearing. `PointsSample` is an array of codes plus an
`(n_players, n_sims)` matrix whose rows are bound to codes **by position**. Draw
from a seeded RNG over a differently-ordered player list and every player gets
somebody else's simulated season. Same seed, same warehouse, different squad, on
a machine with a different core count.

The fix is one `ORDER BY` on the point-in-time keys.

- Tests: `test_determinism.py` (6 tests)

## 9. Recency chasing

There is a real, working detector for this, and it is validated in both
directions before it is used: it must flag `LastWeeksBestStrategy` (which *is*
the bug, shipped deliberately as a baseline) and must not flag an
ownership-only strategy. The measurement is a Spearman rank correlation between
what a strategy values at a deadline and what each player scored in the
immediately preceding gameweek, with a threshold of 0.80.

No non-baseline strategy currently exposes a valuation, so the audit skips rather
than passes. It begins guarding the moment the optimizer lands one.

The residual risk is not covered by the detector: a points model fitted on recent
form will reproduce recent form with rho well below 0.80 and still be adding
nothing beyond the lag. The correlation test catches the crude version. The only
defence against the subtle version is a walk-forward score against the
`LastWeeksBest` baseline, and that baseline is in the tree for exactly that
reason.

- Tests: `test_recency_chasing.py` (5 tests)

## 10. Evaluation

Both model families ship a `walk_forward` evaluator and nothing in the tree uses
a shuffled split. What is weak:

- **Cards are not populated.** `ModelCard.beats_baseline` returns `None` when
  either score is missing, and `None` is falsy — a caller writing
  `if card.beats_baseline:` reads an unmeasured model as a failing one, and a
  report template renders it as a blank rather than as "unmeasured". Several
  cards currently carry no score and no note saying so.
- **Synthetic evaluation flatters the model it evaluates.** The team-goal
  synthetic league samples from a Dixon-Coles process, so the Dixon-Coles model
  is fitting a correctly specified model. The synthetic bookmaker prices off the
  true rates with configurable noise, so the market baseline's margin is a design
  parameter of the simulator rather than evidence about real bookmakers. The
  generator's own docstring says this; it is repeated here because a number in a
  report loses its caveat.
- **Simulator anchors are calibrated in-sample.** `fpl_edge/sim/calibration.py`
  aggregates a *whole season* of `fact_player_fixture` directly — no snapshot,
  no `as_of` — to recompute the constants the field model samples from. That is
  defensible for a one-off constant and indefensible the moment the calibration
  season overlaps a backtest window, because the anchors then encode the answer
  the backtest is trying to find. Nothing currently records which season the
  live anchors were fitted on.
- **The ownership model family ships no walk-forward evaluator.**
  `fpl_edge/models/ownership/` has no `evaluate` module, so the ownership
  forecast — the input to every effective-ownership and rank-utility figure —
  has no out-of-sample score at all.
- **A hardcoded as-of default.** `models/minutes/evaluate.py` defaults
  `--catalog-at` to `2026-08-18T12:00:00+00:00`, silently freezing the evaluation
  to the day this audit was written for anyone who omits the flag.

- Tests: `test_walk_forward.py` (7 tests)

## 11. Data the point-in-time discipline cannot police

Two sources enter the model outside the warehouse and therefore outside `as_of`:

- `fpl_edge/models/ownership/panel.py` reads a committed ownership panel parquet.
- `fpl_edge/models/team_goals/odds.py` reads a committed odds CSV.

And one that is worse than either: `fpl_edge/models/ownership/elite.py` builds an
`EliteSample` stamped with `as_of = datetime.now()`. That is correct for a live
sample of what the top 10k own right now. It is a hard leak in any backtest,
because **FPL does not publish a manager's picks until after the deadline has
passed**. A walk-forward replay that reads elite ownership for GW n is reading
information that did not exist when the decision was made — and because the
`as_of` is the scrape time rather than the publication time, the snapshot filter
cannot catch it.

Committed fixtures are good practice for reproducibility, but neither file
carries a per-row `as_of`, so nothing can check *when* each ownership figure or
bookmaker quote was observable. For odds this matters a lot: a closing price
contains team-news information that was not available at the deadline, and a
model calibrated against closing prices will look better than it can be in
production. Both are baselined in the static audit with this reason attached.

## 12. Things this engine structurally cannot see

Stated plainly, because a model that cannot see something will confidently
predict as though it does not exist:

- **Team news before it is published.** Press conferences, warm-up injuries,
  travelling-squad omissions. The engine's information set is the FPL API's
  `status` and `news` fields, which lag the press by hours and lag reality by
  days.
- **Rotation intent.** European fixtures, cup rounds and manager preference for a
  particular opponent are not in the data. `euro_congestion` in the minutes
  features is a proxy built from a league-rank heuristic, not a fixture list.
- **In-play events.** Everything is decided at the deadline. A red card in the
  Saturday early kickoff changes nothing about the Sunday captain.
- **Set-piece and penalty duty changes.** The API exposes an ordering, updated
  irregularly, and it is not currently ingested.
- **Managerial change.** A new manager invalidates every minutes prior for that
  club, and the model has no feature that represents it.
- **The field's actual behaviour.** Effective ownership is modelled from
  `selected_by_percent` plus a panel. Real managers herd, panic and follow the
  same three podcasts. Modelling them as a smooth distribution understates the
  correlation between their squads, which is precisely the quantity the
  rank-utility objective depends on.
- **Its own effect.** None. This is a single-manager tool, which is the one
  reflexivity problem it does not have.

---

## How to break this model, if you wanted to

A short adversarial checklist, because the failure modes above are easier to
recognise as attacks:

1. **Feed it a backfill with a uniform `as_of`.** Every historical result becomes
   visible at once. Backtest returns go up. Nothing raises. (§2)
2. **Run the backtest on a machine with a different core count.** Get a different
   squad from the same seed. (§8)
3. **Send a `news_added` without a `Z`.** Move an injury announcement by the
   host's UTC offset, across a deadline. (§3)
4. **Re-ingest a corrected match with the same `as_of`.** The correction is
   silently discarded and the wrong stat line is kept forever. (§4)
5. **Wait for a January transfer to reissue a player's temporary code.** His
   history splits into two players and both halves look valid. (§4)
6. **Let a player leave the league.** He stays selectable at his last price with
   `status='a'` for the rest of the season. (§5)
7. **Serve one player's `selected_by_percent` as an empty string.** He becomes a
   0%-owned differential and the objective rewards captaining him. (§6)
8. **Write `snapshot.warehouse.sql(...)` in a model.** Read the entire season.
   Only a lint stands in the way. (§1)
