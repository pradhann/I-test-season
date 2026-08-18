# Minutes model

Minutes are the largest single driver of FPL variance. A premium midfielder's
gameweek is bimodal - twelve points or two - and which mode you get is decided
before kickoff, by the team sheet. Every downstream quantity in this engine
(expected points, clean-sheet correlation, captaincy, autosub value) is a
function of the minutes distribution, so a mis-priced 20% rotation risk is not a
rounding error; it is the difference between a captain and a bench player.

Public projections handle this badly, usually with a single hand-set "chance to
start" multiplier applied to a points-per-90. This package produces the whole
three-way distribution instead:

| bucket | meaning | why the boundary is there |
| --- | --- | --- |
| `p_unavailable` | 0 minutes | no appearance points, autosub triggers |
| `p_cameo` | 1-59 minutes | 1 appearance point, no clean sheet |
| `p_full` | 60+ minutes | 2 appearance points, clean sheet eligible |

The 60-minute edge is an FPL scoring boundary (`scoring.minutes_long`, verified
in `docs/rules.md`), so it is a bucket edge rather than something a point
estimate has to straddle.

## Two approaches, compared on evidence

Both implement `MinutesModel` and both are scored on identical rows.

### (a) Hierarchical / empirical Bayes - `hierarchical.py`

A player's own record is the best evidence about his minutes and there is never
enough of it. Nine games in, a fringe centre-back has nine observations. So the
recency-weighted bucket counts are shrunk toward a prior for the
`(position, within-club depth rank)` cell, with a Dirichlet-multinomial
concentration estimated **by moments from the training seasons** rather than
chosen: cells with real between-player spread (fourth-choice midfielders) shrink
weakly, homogeneous cells (third-choice goalkeepers) shrink hard.

Fourteen fitted parameters sit on top, all interpretable and all fitted by
minimising training log loss:

* three evidence weights (last five fixtures, season to date, previous season)
  and a scale on the concentration;
* an **availability gate** per published-status bucket - what a flag is
  empirically worth, since FPL's "75% chance" is not 75%;
* a **staleness time constant**: at a cold start a GW6 prediction is reading a
  five-week-old injury note, and the gate decays toward "tells us nothing";
* a **congestion** offset for clubs likely to be playing midweek in Europe;
* a **market tilt** on within-position price and ownership z-scores;
* a three-way vector scaling.

### (b) Gradient-boosted classifier - `gbm.py`

Multiclass histogram gradient boosting over the same feature frame. It imposes
almost nothing and finds interactions the parametric model cannot express -
"flagged AND fourth in the depth chart AND the club plays Thursdays" is a leaf.

The backend is scikit-learn's `HistGradientBoostingClassifier`, not LightGBM.
LightGBM is supported (`backend="lightgbm"`) but is not the default and is not
what the committed numbers refer to: on this machine the wheel cannot load at
all (`libomp.dylib` missing), and a score that depends on which optional native
library happens to import is not a score.


## Measured, out of sample

Walk-forward: train on every season strictly before the test season, predict
each test gameweek from a Snapshot taken at that gameweek's own deadline. Metric
is multiclass log loss (lower is better) with multiclass Brier alongside, since
log loss can be dominated by a few confident misses and Brier cannot.

### Real Premier League history (`docs/models/minutes_eval.csv`)

Warehouse seasons 2022-23 to 2025-26, ~29,700 player-fixtures per test season.
Headline fold: **train 2022-23 + 2023-24 + 2024-25, test 2025-26**.

| model | n | log loss | Brier | ECE `p_full` |
| --- | ---: | ---: | ---: | ---: |
| baseline (i) base rate | 29,747 | 0.9106 | 0.5401 | 0.0190 |
| baseline (ii) previous-season rate | 29,747 | 0.7989 | 0.4624 | 0.0509 |
| baseline (iii) FPL `chance_of_playing_next_round` | 29,747 | 0.9106 | 0.5401 | 0.0190 |
| **hierarchical EB** | 29,747 | 0.5215 | 0.2890 | 0.0123 |
| **GBM** | 29,747 | 0.4165 | 0.2340 | 0.0042 |

The **GW1 cold-start slice** of the same fold - the case that is live today:

| model | n | log loss | Brier | ECE `p_full` |
| --- | ---: | ---: | ---: | ---: |
| baseline (i) base rate | 690 | 0.9472 | 0.5698 | 0.0256 |
| baseline (ii) previous-season rate | 690 | 0.8021 | 0.4654 | 0.0334 |
| baseline (iii) FPL `chance_of_playing_next_round` | 690 | 0.9472 | 0.5698 | 0.0256 |
| **hierarchical EB** | 690 | 0.7349 | 0.4249 | 0.0549 |
| **GBM** | 690 | 0.6330 | 0.3632 | 0.0333 |

All three folds, log loss, every row (`slice = all`):

| test season | training seasons | base_rate | prior_season | fpl_chance | hierarchical | gbm |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2023-24 | 2022-23 | 0.9069 | 0.9322 | 0.9069 | 0.5704 | 0.4563 |
| 2024-25 | 2022-23 + 2023-24 | 0.9495 | 0.8451 | 0.9495 | 0.5652 | 0.4717 |
| 2025-26 | 2022-23 + 2023-24 + 2024-25 | 0.9106 | 0.7989 | 0.9106 | 0.5215 | 0.4165 |

And the GW1 slice of each fold:

| test season | base_rate | prior_season | fpl_chance | hierarchical | gbm |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023-24 | 0.9739 | 0.8735 | 0.9739 | 0.8637 | 0.7859 |
| 2024-25 | 1.0234 | 0.8683 | 1.0234 | 0.7932 | 0.7415 |
| 2025-26 | 0.9472 | 0.8021 | 0.9472 | 0.7349 | 0.6330 |

### The verdict, stated plainly

* **Both approaches beat all three baselines on every fold, by a lot.** On the
  headline fold the GBM cuts log loss from the best baseline's 0.7989 to 0.4165
  (-48%) and the hierarchical model to 0.5215 (-35%).
* **The GBM is the better model on real data, everywhere** - warm, cold, and on
  Brier as well as log loss. It is the one to ship.
* **The hierarchical model loses by ~0.10 nats and is kept anyway**, because its
  fourteen parameters are readable, it degrades gracefully when a feature block
  is missing, and it is the fallback if the GBM ever has to be retired. This is
  a measured second place, not a tie.
* **Baseline (iii) is uninformative on this data and scores exactly the base
  rate.** The historical archive carries no availability at all: `status` and
  `chance_of_playing_next_round` are NULL on 100% of pre-2026-27
  `fact_player_state` rows. That is a *data* result, not a model result, and it
  is the single most valuable thing this evaluation found - see "What is
  missing" below.
* **Baseline (ii) is not monotone in training data.** With only 2022-23 to learn
  from it is *worse than the base rate* (0.9322 vs 0.9069): one season of
  per-player rates, smoothed, is noise. It becomes the strongest baseline once
  two seasons are available.

### Synthetic fixture warehouse (`docs/models/minutes_eval_synthetic.csv`)

The same walk-forward on the committed synthetic league in
`tests/fixtures/minutes/`, whose generator is
`tests/fixtures/minutes/generate.py`. This exists because it was built before
the historical ingest landed, and it is kept because it is the only dataset here
that **has availability data** (status on 100% of state rows,
`chance_of_playing_next_round` on 14.5%), so it is the only place the
availability machinery can currently be scored at all.

Fold: train 2023-24 + 2024-25, test 2025-26.

| model | n | log loss | Brier | ECE `p_full` |
| --- | ---: | ---: | ---: | ---: |
| baseline (i) base rate | 5,544 | 1.0615 | 0.6408 | 0.0033 |
| baseline (ii) previous-season rate | 5,544 | 0.9118 | 0.5381 | 0.0537 |
| baseline (iii) FPL `chance_of_playing_next_round` | 5,544 | 0.9641 | 0.5782 | 0.0440 |
| **hierarchical EB** | 5,544 | 0.7032 | 0.4079 | 0.0147 |
| **GBM** | 5,544 | 0.6851 | 0.4039 | 0.0139 |

GW1 cold-start slice:

| model | n | log loss | Brier | ECE `p_full` |
| --- | ---: | ---: | ---: | ---: |
| baseline (i) base rate | 252 | 1.0528 | 0.6346 | 0.0201 |
| baseline (ii) previous-season rate | 252 | 0.8772 | 0.5180 | 0.0897 |
| baseline (iii) FPL `chance_of_playing_next_round` | 252 | 1.0452 | 0.6294 | 0.0276 |
| **hierarchical EB** | 252 | 0.7959 | 0.4642 | 0.0589 |
| **GBM** | 252 | 0.8117 | 0.4768 | 0.0625 |

Two things change when availability is present:

* baseline (iii) becomes informative (0.9641 vs the base rate's 1.0615) but is
  still the *weakest* of the three - reading `chance_of_playing_next_round`
  literally is worse than reading last season's minutes;
* **the cold-start ranking flips**: the hierarchical model wins GW1 here
  (0.7959 vs 0.8117) and loses it on real data (0.7349 vs 0.6330). With
  availability flags in play its explicit gate is worth more than the trees'
  flexibility; without them, the trees win. Ranking a model on one dataset is
  how you get this wrong.

The hierarchical model's fitted availability gate on the synthetic data, i.e.
what a published flag is empirically worth as a multiplier on P(appears):

| published state | fitted multiplier |
| --- | ---: |
| nothing published (`status = a`) | 1.105 |
| 75% chance | 0.917 |
| 50% chance | 0.710 |
| 25% chance or less | 0.005 |
| injured / suspended | 0.005 |

A "75% chance of playing" is worth a 9% haircut, not 25%. That is the kind of
thing the gate exists to learn, and the kind of thing that cannot be learned at
all from the current real archive.

## Calibration

`docs/models/minutes_calibration.csv` (real) and
`minutes_calibration_synthetic.csv` hold the reliability curves: ten equal-width
bins of predicted probability for `p_full` and `p_unavailable`, per model, per
test season, with bin population, mean prediction and observed frequency. ECE is
the population-weighted mean absolute gap.

On the headline real fold the GBM is calibrated to 0.0042 ECE on `p_full`
(0.0057 on `p_unavailable`) and the hierarchical model to 0.0123 (0.0216) -
both well inside the noise a squad decision cares about. Both baselines that
carry per-player information are visibly *worse* calibrated than either model
(previous-season rate: 0.0509), which is the expected shape: an unshrunk
per-player rate is overconfident at both ends.

## Features

| group | features | why |
| --- | --- | --- |
| rotation history | `full_rate_5/season`, `cameo_rate_*`, `unavail_rate_*`, `start_rate_3/5/season`, `mean_min_*`, `minutes_trend` | the player's own record, at two horizons so a recent change of role is visible |
| substitution pattern | `sub_off_rate`, `sub_on_rate` | being hooked on 55 and being a 70th-minute sub are both cameos, and they persist |
| bench drift | `team_fixtures_since_start`, `days_since_last_appearance` | distinguishes "rested once" from "out of the side" |
| club depth | `depth_rank`, `depth_surplus`, `squad_size_pos` | rank within (club, position); the nailed/squad distinction the whole model turns on |
| manager tendency | `team_rotation_index`, `team_subs_per_game` | measured XI churn between consecutive fixtures, per club per season |
| congestion | `days_rest`, `is_midweek`, `is_season_opener`, `team_fixtures_next_14d`, `euro_club`, `euro_congestion` | European and cup fixtures are not in the warehouse; prior-season league position is a public, strictly historical proxy for who is playing midweek |
| availability | `status_known`, `status_flagged/injured/doubtful/suspended`, `chance_next`, `has_chance` | three partly-redundant published channels with different lags and reliabilities |
| news text | `news_len`, `news_injury`, `news_suspension`, `news_doubt`, `news_return` | regex flags over the free-text note, which often says more than the status code |
| market | `price_tenths`, `selected_by_pct`, `transfers_net_frac` | the market prices expected involvement, and at a cold start it is one of the few live signals |
| carryover | `prev_*` rates, `is_new_signing`, `is_unseen` | keyed on the stable cross-season `code`, so a transfer does not reset a career |

The opportunity denominator is **team fixtures, not appearances**. A player who
has not featured for six weeks has six pieces of evidence, not zero; the grid is
expanded to every squad player of every club that played, and a missing result
row is a zero-minute observation rather than a missing value.

## The GW1 cold start

Today is 2026-08-18. The GW1 deadline is 2026-08-21T17:30Z and the season has
zero rows of current-season data. A model that needs five gameweeks of form is
not a model yet, so the cold start is a first-class code path rather than a
degenerate case of the warm one.

`is_cold_start` is set per row when the player's club has no visible fixture
this season. Cold rows are routed to a separately fitted stage using
`COLD_FEATURE_COLUMNS` only - the current-season columns are *structurally*
absent at GW1, not missing at random, and imputing them would invent evidence.
What remains is real: previous-season bucket rates keyed on the stable player
`code`, whether the player is new to the club, within-club depth rank computed
from last season, the club's rotation index from last season, preseason
availability flags and news text, price and ownership, and the published
fixture calendar.

Training rows for the cold stage are generated by **replaying the season-start
snapshot forward**: features for GW1-6 built from the snapshot at the season's
first deadline, labelled with what actually happened. That is the identical
feature distribution a real GW1 faces, and it multiplies the cold training set
by six without leaking anything - the snapshot genuinely knows nothing.

The hierarchical stage additionally decays the availability gate with horizon:
a preseason injury note is worth a lot for GW1 and progressively less for GW6.

## Leakage discipline

Every feature is read through a `Snapshot`. There is no path from this package
to a `Warehouse`, a CSV of "current" data, or a caller-supplied frame - only
`snapshot.table(...)`, `snapshot.players(...)` and the fixture list, all filtered
to `as_of <= deadline`.

* Training data is assembled by minting **one Snapshot per historical deadline**
  (`training.TrainingSetBuilder`), not by loading a season and slicing it.
* Labels come from a **separate** snapshot taken after the gameweek settled, and
  are joined only onto rows whose features were already built.
* The evaluation predicts each test gameweek from a snapshot at that gameweek's
  own deadline.
* Historical deadlines are reconstructed from the published fixture list
  (kickoff minus 90 minutes, per `deadlines.offset_before_first_kickoff_minutes`)
  because `dim_event` only carries the live season. Schedules are public in
  advance, so this is not a leak.
* The opportunity grid is built from each season's own `dim_player` rows, never
  intersected with the current season's player list, so players who retired or
  left the league stay in the training data. Excluding them is the classic
  survivorship bug and would systematically overstate minutes.
* An unknown availability is `NaN`, never `"a"`. Reading a missing status as
  "fit" is the optimistic direction, and the optimistic direction is the one
  that buys the injured player.

## What is missing, and what it is worth

1. **Availability history.** The single biggest gap. Every pre-2026-27
   `fact_player_state` row has NULL `status`, `chance_of_playing_next_round` and
   `news`, so eleven of the model's features are dead on historical data and
   baseline (iii) cannot be evaluated as intended. The synthetic run above is
   the estimate of what recovering it is worth: the availability gate is what
   lets the hierarchical model win a cold start, and no amount of model work
   substitutes for the data. Live snapshots from 2026-27 onward do carry it, so
   this improves by itself over the season - but a backfill would improve it
   now.
2. **`can_select` / `removed`.** The warehouse gained these columns
   (FPL's own authority on whether a player may be picked) but
   `Snapshot.players()` does not return them, so the model cannot see that a
   player has left the league. Historical rows are NULL, so this changes the
   live prediction rather than the backtest.
3. **Real midweek fixtures.** European and domestic-cup ties are not in the
   warehouse at all; congestion is proxied by prior-season league position plus
   days of rest. A real UEFA/EFL fixture feed would replace a proxy with an
   observation.
4. **Lineup and press-conference signals.** Predicted-XI feeds and manager
   quotes move minutes probabilities more than anything in this feature set on
   the day before a deadline. None are ingested.

## Reproducing

```bash
# real warehouse, three walk-forward folds (~10 min)
uv run python -m fpl_edge.models.minutes.evaluate \
    --warehouse data/warehouse/fpl.duckdb \
    --catalog-at 2026-08-19T00:00:00+00:00 --write-docs

# committed synthetic fixtures, fully offline (~40 s)
uv run python -m fpl_edge.models.minutes.evaluate --tag synthetic --write-docs

# regenerate the synthetic league itself (deterministic, seed 20260818)
uv run python tests/fixtures/minutes/generate.py

# tests
uv run pytest tests/unit/test_minutes_features.py tests/unit/test_minutes_models.py \
              tests/unit/test_minutes_coldstart.py tests/unit/test_minutes_eval.py -q
```

`fpl_edge/models/minutes/measured.py` holds the numbers the ModelCards report;
`tests/unit/test_minutes_eval.py` fails if they drift from the committed CSVs,
so regenerating the evaluation and not updating the cards is a test failure
rather than a silent inconsistency.
