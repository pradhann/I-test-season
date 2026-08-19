# The hypothesis registry

Every belief in this project becomes a versioned markdown file that is
automatically graded after each gameweek. The point is compounding: a season of
takes, tips and model calls turns into a measured track record per source, and
that track record feeds the oracle's source weights directly. Being wrong on
the record is the product.

## Layout

```
theses/
  open/        one .md per unresolved thesis
  resolved/    graded theses, moved here by `make resolve-gw`
  scoreboard/  sources.json (current record), history.csv (record over time)
```

The files are the database. `git log theses/` is the audit trail; nothing about
a thesis lives anywhere else.

## File format

`theses/open/2026-08-18-rashford-minutes.md`:

```markdown
---
id: 2026-08-18-rashford-minutes
created: '2026-08-18T23:05:00Z'
source: user_chat            # user_chat | creator | elite_manager | model | llm_scout
creator: null                # named person/model, for per-creator accuracy
raw_input: Rashford is nailed now, he starts most weeks
player: Rashford
player_code: 176297          # stable cross-season FPL code, never element_id
season: 2026-27
gw_start: 1
gw_end: 6
horizon_gws: 6
claim_type: minutes          # buy | avoid | watch | out_of_position | minutes | captain
falsifiable_prediction: starts in 4+ of GW1-GW6
comparator_codes: []         # frozen at creation for set-based claims
comparator_label: ''
model_verdict_at_creation:   # captured from a Snapshot at the creation instant
  as_of: '2026-08-18T23:05:00Z'
  price: 7.0
  ownership_pct: 4.1
  status: a
  is_supported_club: true
  season_ppg: null           # season not started: no history exists yet, honestly
  xpts: 3.1                  # projection numbers when one covers the window
acted: false
status: open
---

Free prose: why the belief was held. Anything goes below the front matter.
```

Rules the code enforces:

* **`player_code` is the stable FPL code** (`fpl_edge.types.PlayerCode`), never
  the per-season `element_id`. Names are resolved through the same
  point-in-time player universe the idea inbox uses; an ambiguous name is a
  refusal, not a guess.
* **`model_verdict_at_creation` is written once, at creation, from a Snapshot
  at that instant.** It is never backfilled — that would be leakage — and
  resolution never touches it. `tests/unit/test_theses_leakage.py` proves a
  thesis created at time T carries only data visible at T, against a warehouse
  that contains later prices and later results.
* **`falsifiable_prediction` must come from the grammar below, or be absent.**
  An idea that cannot be made falsifiable is stored as `claim_type: watch`
  with a note. `Thesis` refuses to construct anything else.

## The claim grammar

Each template is one canonical sentence with one grader
(`fpl_edge/theses/grammar.py`). Ties on strict comparisons are a `push`,
excluded from hit rates.

| template | sentence | graded as |
| --- | --- | --- |
| `beats_peer_median` | `outscores positional price-peer median over GW{a}-GW{b}` | window total > median of the frozen peers' window totals |
| `beats_peer_median_by` | `outscores positional price-peer median by {n}+ pts over GW{a}-GW{b}` | margin over frozen peer median >= n |
| `trails_peer_median` | `scores fewer pts than positional price-peer median over GW{a}-GW{b}` | the avoid call, inverted comparison |
| `beats_named_player` | `outscores {name} (code {code}) over GW{a}-GW{b}` | head-to-head totals; the code makes it exact |
| `beats_top_captain` | `outscores the most-captained player {name} (code {code}) in GW{k}` | one-week captaincy call; rival frozen at creation |
| `beats_captain_pool_median` | `outscores the median of the frozen captain pool over GW{a}-GW{b}` | vs the frozen most-owned outfield pool |
| `starts_at_least` | `starts in {n}+ of GW{a}-GW{b}` | gameweeks with a start (`starts` column; 60-minute fallback on archives) |
| `total_points_at_least` | `scores {n}+ pts over GW{a}-GW{b}` | window total >= n |
| `attacking_returns_at_least` | `returns {n}+ goal involvements over GW{a}-GW{b}` | goals + assists >= n |

Comparator sets (peer medians, captain pools) are **frozen at creation** into
`comparator_codes`. Resolution reads only the realised points of players
already in the set; it never rebuilds the set, so the yardstick cannot chase
the outcome.

Defaults per claim type: `buy`/`out_of_position` → `beats_peer_median`;
`avoid` → `trails_peer_median`; `captain` → `beats_top_captain` (against the
most-owned outfielder at creation); `minutes` → `starts_at_least` with
n = ⌈⅔·horizon⌉; `watch` → nothing.

## Creating theses

Three paths, one implementation (`fpl_edge/theses/create.py`):

```bash
# CLI — user takes, creator claims, model calls
fpl thesis add "Rashford is nailed now" --claim-type minutes --player rashford
fpl thesis add "Haaland is the GW1 captain" --source creator --creator "FPL Harry" \
    --claim-type captain --player haaland
fpl thesis add "model top xpts pick" --source model --creator points_ensemble \
    --claim-type buy --player semenyo --acted
```

```python
# API — the content team files creator claims, elite-mining files cohort ideas
from fpl_edge.theses import create_thesis, ThesisSource
thesis, path = create_thesis(
    warehouse,
    raw_input="Harry: Semenyo best premium captain for GW1",
    source=ThesisSource.CREATOR, creator="FPL Harry",
    player="semenyo", claim_type="captain", gw_start=1,
)
```

```bash
# Telegram / idea inbox — ideas logged by the bot are mirrored idempotently
fpl theses sync     # also runs automatically at the start of `make resolve-gw`
```

The bridge (`thesis_from_idea` / `sync_from_registry`) preserves the idea's own
frozen comparator semantics and links the file back via `idea_id`, so the same
belief is never double-created.

## Resolution

```bash
make resolve-gw                      # the weekly ritual
fpl theses resolve --dry-run         # show everything, write nothing
fpl theses resolve --season 2025-26 --as-of 2026-06-01T00:00:00Z --dir /tmp/replay
```

For every open thesis whose full window has finalised **as visible at the run
instant** (a Snapshot read — a half-finished gameweek is invisible):

1. the grader settles the claim: `correct` / `incorrect` / `push` / `void`,
   and watches expire as `unscored` with a record of what happened;
2. a `resolution` block is appended to the front matter — outcome, points,
   margin, and the **counterfactual**: what following an un-acted call would
   have been worth, the raw material for the cost-of-hesitancy ledger;
3. the file moves `open/` → `resolved/`;
4. the scoreboard is recomputed from all resolved files (per source channel
   and per named creator) and `history.csv` gets one row per entity;
5. one git commit is made (as Nripesh) whose message lists what was right and
   wrong. `--dry-run` prints the exact message instead.

`fpl theses review` prints the scoreboard, the summed cost of hesitancy, and a
supported-club accuracy split (club affinity is a measured bias here, not an
assumption).

## Feeding the oracle

`theses/scoreboard/sources.json` rows carry exactly the fields
`fpl_edge.oracle.signals.SourceWeight` needs:

```python
from fpl_edge.theses import source_weights
weights = source_weights(Path("theses/scoreboard/sources.json"))
verdicts = fpl_edge.oracle.signals.aggregate(signals, weights, as_of=deadline)
```

A source with no resolved sample gets `hit_rate=None` → weight 0.0: no track
record, no influence. Shrinkage in `SourceWeight` keeps a lucky 2/2 from
mattering.

## Known gaps

* `p_start` is absent from `model_verdict_at_creation` until the minutes model
  publishes per-player start probabilities; the dict is open-ended and new
  numbers join at creation time only.
* The "most-captained" rival is proxied by ownership until the ownership
  team's captaincy-share model lands (the same proxy, and the same caveat, as
  the ideas inbox).
