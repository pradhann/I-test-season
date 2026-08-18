# Historical data lineage

Season 2026/27 GW1 has not been played. There is zero current-season match data,
so every model in this engine is trained on history, and history is only worth
training on if two things hold: each row is attached to a stable player, and each
row is stamped with the instant it became knowable. This document records how
both are done, what was measured, and where the honest gaps are.

Owned by the historical-data component:
`fpl_edge/ingest/vaastav.py`, `fpl_edge/ingest/player_mapping.py`,
`scripts/ingest_history.py`, `tests/unit/test_vaastav.py`,
`tests/unit/test_player_mapping.py`, `tests/fixtures/vaastav/`.

## 1. Source

[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League),
read as raw CSV from `raw.githubusercontent.com`. The layout was **fetched and
verified**, not recalled — it changes between seasons. As of 2026-08-18 the repo
carries `data/2016-17` … `data/2026-27`, and the files this ingest reads are:

| Path | What it gives us | Notes |
| --- | --- | --- |
| `data/<season>/players_raw.csv` | `id` (element) **and** `code` (stable), `element_type`, `team`, `team_code`, names | The authority for a season's element → code map |
| `data/<season>/teams.csv` | `id`, `code`, `name`, `short_name` | 20 rows |
| `data/<season>/fixtures.csv` | `id`, `event`, `kickoff_time`, `team_h`, `team_a`, scores, `finished` | 380 rows; also carries a multi-kilobyte `stats` blob we do not read |
| `data/<season>/gws/merged_gw.csv` | one row per player per fixture | Keyed on `element`. **No `code` column in any season 2016-17 → 2025-26** |

Seasons loaded: **2022-23, 2023-24, 2024-25, 2025-26** — the last three completed
seasons plus one more. As of 2026-08-18, 2025-26 is complete: all 38 gameweeks
are present in `data/2025-26/gws/`, so there is no partial-season handling to do.

`data/2026-27/` exists upstream and holds `players_raw.csv`, `teams.csv`,
`fixtures.csv` and `player_idlist.csv` for the coming season, but **no `gws/`
directory** — consistent with GW1 not having been played. It is deliberately not
ingested here: pre-season squad data belongs to the live-API path, which can
stamp `as_of` at its own fetch instant.

Column sets drift between seasons and the loader tolerates it explicitly:

* 2024-25 adds `mng_*` columns and `element_type == 5`;
* 2025-26 adds `tackles`, `clearances_blocks_interceptions`, `recoveries`,
  `defensive_contribution`;
* 2024-25 onward adds `has_temporary_code`;
* 2018-19 carries a stray `id` column in `merged_gw.csv`.

Anything absent is written as **NULL, never zero**. "This statistic did not exist
in 2022-23" and "this player recorded none of it" are different facts, and a
model that cannot tell them apart will learn that defenders stopped making
tackles before 2025.

Every file read over the network is written to `data/raw/vaastav/<season>/…`,
mirroring the upstream layout, and recorded in `raw_fetch` with its sha256. A
load is therefore replayable offline (`--offline`) and traceable to bytes.

## 2. Cross-season identity

FPL's `element` id is a per-season row number, reassigned every summer. Declan
Rice is element **467 → 540 → 16 → 21** across the four loaded seasons. The
stable key is `code`: Rice is **204480** in all of them, before and after his
West Ham → Arsenal move. `fpl_edge/types.py` makes these distinct `NewType`s so
confusing them is a type error; this module makes sure the historical rows
actually reach the right one.

`merged_gw.csv` — the only file with per-gameweek returns — carries `element` and
no `code`. `player_idlist.csv` carries `first_name, second_name, id` and no code
at all, so it is useless for this and is not read. The bridge is
`players_raw.csv`, which carries both.

Resolution order in `PlayerCodeIndex.resolve_frame`, with nothing ever imputed:

1. an explicit `code` column, when a file happens to have one;
2. `(season, element)` → code, from that season's `players_raw.csv`;
3. `(season, normalised name)` → code, **only when that name is unique within the
   season**.

Step 3 refuses to guess. In 2022-23 the Premier League fielded two players called
Ben Davies simultaneously — Liverpool's (code 152898) and Tottenham's (115556) —
with identical first names, identical second names and identical `web_name`. A
name-keyed join merges them. So does a `web_name` join on Cole Palmer (244851)
and Alex Palmer (112520). Ambiguous names return unresolved.

Name normalisation folds diacritics (`Gyökeres` → `gyokeres`) and punctuation
(`Kesler-Hayden` → `kesler hayden`) but deliberately does **not** reorder tokens:
vaastav renders some names surname-first in one season and given-name-first in
another (`Tomiyasu Takehiro` / `Takehiro Tomiyasu`). That is handled by a
shared-token test rather than by sorting, so that two genuinely different people
never look alike.

### Measured result

Over 2022-23 … 2025-26, resolving all 113,582 `merged_gw` rows:

| Season | merged_gw rows | manager rows stripped | eligible | resolved | unmatched dropped | match rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2022-23 | 26,505 | 0 | 26,505 | 26,505 | 0 | 100.0000% |
| 2023-24 | 29,725 | 0 | 29,725 | 29,725 | 0 | 100.0000% |
| 2024-25 | 27,605 | 322 | 27,283 | 27,283 | 0 | 100.0000% |
| 2025-26 | 29,747 | 0 | 29,747 | 29,747 | 0 | 100.0000% |
| **Total** | **113,582** | **322** | **113,260** | **113,260** | **0** | **100.0000%** |

Every row resolved through step 2. The name fallback exists for robustness
against upstream layout changes and fired zero times.

Manager rows are counted **separately** from failures, because a dropped manager
is a decision and a dropped unknown is a bug; merging the counters would let a
real mapping failure hide inside an expected exclusion.

Integrity checks, all clean on the loaded seasons:

* **element → code is injective within a season.** Two elements claiming one code
  raises `IdentityCollisionError` at index time. 0 occurrences.
* **No code carries two identities.** A code whose recorded names share no token
  is a merged career. 0 occurrences over 1,672 distinct codes.
* **Codes flagged `has_temporary_code`: 0.** FPL uses this to say "we will
  reissue this code once the PL registry catches up", which splits a career. The
  flag is read where present (2024-25 onward) and reported, never assumed away.

### Known limitation: code reissues

`PlayerCodeIndex.split_identities()` detects the inverse failure — one name,
unambiguous in every season, mapping to different codes *between* seasons. That
is what a code reissue looks like from the outside, and nothing errors, because
both halves are valid codes.

**Measured: exactly 1 across four seasons.** Kaine Kesler-Hayden is 537043 in
2022-23 and 465390 from 2023-24 onward. His pre-2023 history is therefore
attached to a code that no later season uses.

This is **reported, not repaired.** Merging on name equality is precisely the
mistake that turns the two Ben Davieses into one player. One split career out of
1,672 codes is a smaller error than a name-based merge would introduce, and the
count is published so a downstream model can decide for itself.

## 3. `as_of` semantics

`as_of` is the instant a fact became **publicly observable**. Not kickoff, not
the file's git commit date, not now. `docs/rules.md` (`deadlines.points_final_at`,
verified against the FPL rules page) is the governing statement:

> points are final at **09:00 UK the day after the final match of the Gameweek**

| Table | `as_of` | Reasoning |
| --- | --- | --- |
| `fact_player_fixture` | 09:00 UK on the day after the gameweek's last kickoff | Bonus points and the final BPS ranking are provisional until then. A gameweek that has kicked off is genuinely unknown. |
| `fact_fixture` (schedule row) | the season's GW1 deadline | The fixture list is public in advance; storing it is not leakage. Scores are NULL and `finished` is false on this row. |
| `fact_fixture` (result row) | kickoff + 2h ≈ full time | A scoreline is public at the final whistle — earlier than points finalisation, and correctly so. |
| `dim_player` | the gameweek's deadline | The club/position/name that held at that deadline. |
| `dim_team` | the season's GW1 deadline | Static for the season. |
| `fact_player_state` | the gameweek's deadline | vaastav's `value` is the price for that gameweek; the honest instant is the deadline at which that was the price you would have paid. |

Two conversions matter and are easy to get wrong:

**09:00 UK is not a fixed UTC offset.** It is 08:00Z under BST and 09:00Z under
GMT. The conversion goes through `ZoneInfo("Europe/London")`, and the "day after"
is computed from the last kickoff's **UK** calendar date, so a 20:00 UK Monday
kickoff finalises 09:00 UK Tuesday. There is a test on both sides of the October
clock change.

**Deadlines are derived, because vaastav ships none.** The rule registry has
`deadlines.offset_before_first_kickoff_minutes: 90` (verified), so a gameweek's
deadline is its first kickoff minus 90 minutes. Spot-checked against known real
values:

* 2022-23 GW1 → `2022-08-05T17:30Z` ✓
* 2025-26 GW1 → `2025-08-15T17:30Z` ✓

### Leakage proof

`tests/unit/test_vaastav.py::test_snapshot_at_gw_deadline_sees_gw_minus_one_but_not_gw`
runs for every committed season × GW ∈ {2, 3, 4}: standing at GW *k*'s deadline,
`Snapshot.results_before` returns GW *k−1* and everything earlier, and
`max(visible gw) == k − 1`. On the full load, `snapshot_at(2025-26 GW2 deadline)`
sees 690 GW1 rows and nothing from GW2.

A second test pins the middle case that a naive implementation gets wrong: at
21:30Z on 2025-08-18 the last GW1 match has finished, but points are not final
until 08:00Z the next morning, so the snapshot is still empty.

## 4. Deliberate exclusions and honest NULLs

**Manager elements.** `element_type == 5` ("Manager", `position == "AM"` in
`merged_gw`) existed in **2024-25 only** — 20 elements, 322 gameweek rows, first
appearing in GW23. They cannot score under the 2026/27 rules
(`misc.manager_scoring_removed`, verified), so they are stripped, not mapped.

> The brief located these in 2025-26. In the archive as fetched they are in
> **2024-25**: `data/2025-26/players_raw.csv` contains no `element_type == 5` and
> `data/2025-26/gws/merged_gw.csv` contains no `AM` rows. The stripping logic keys
> on the element type and position string rather than on a season allow-list, so
> it is correct either way.

Every element type is routed through `Position.from_api`, which raises on 5 by
design; the exception is caught and the element recorded as excluded. Two tests
guard this: one asserts `Position.from_api(5)` still raises, and one asserts the
manager codes (Arteta 100051017, Emery 100037568) appear in no warehouse table
and that their element ids were not remapped onto some other player.

**Ownership is derived, and refused when it cannot be.** vaastav stores
`selected`, an absolute count of squads, while the schema holds
`selected_by_pct`. The denominator is recoverable exactly from the data, because
every manager picks 15 players: `total squads = Σ selected / 15` over all players
in that gameweek. Sanity check — 2022-23 GW1 → 7.81m squads, 2025-26 GW38 →
13.11m, both consistent with FPL's published entry counts. This requires the
whole player pool, so a gameweek with fewer than 300 distinct players present
gets NULL ownership and a warning rather than a plausible-looking wrong number.

**Fields with no historical source stay NULL:**

| Column | Why |
| --- | --- |
| `fact_player_state.status`, `news`, `news_added`, `chance_of_playing_next_round` | vaastav's per-gameweek archive has no availability or injury data. Defaulting `status` to `"a"` would fabricate the single most decision-relevant field in the table. |
| `fact_player_state.cost_change_start` | Not in `merged_gw`. |
| `fact_player_fixture.tackles`, `clearances_blocks_interceptions`, `recoveries`, `defensive_contribution` | The statistics did not exist before 2025-26. |

**Exact duplicate rows.** `data/2025-26/gws/merged_gw.csv` contains 10 byte-identical
duplicate rows (Junior Kroupi ×9, Ben Gannon-Doak ×1). They are dropped by
`drop_duplicates()` and counted in the load report.

## 5. Known limitations

* **Fixture reschedules are not reconstructable.** `fixtures.csv` is a
  final snapshot, so only the *final* kickoff time is available. The schedule row
  is stamped at the season's GW1 deadline, which means a postponed match's
  rearranged date appears earlier than it was actually announced. This is
  lookahead on *scheduling only* — no result column is affected, because results
  are stamped separately. A fixture-difficulty or rest-days feature built from
  `kickoff_utc` should treat this as a small optimism.
* **Derived deadlines assume the gameweek's first kickoff did not move.** A match
  rescheduled *into* a gameweek at an earlier time than the original opener would
  pull the derived deadline earlier than the real one. Deadlines are
  monotonically increasing across gameweeks in all four loaded seasons, which is
  consistent with this not having happened.
* **Prices are per-gameweek, not per-day.** vaastav records the value at each
  gameweek. Intra-week price changes are not recoverable, so a price-change model
  cannot be trained from this source.
* **`dim_event` is not written by this ingest.** Deadlines are derived internally
  to stamp `as_of` but are not persisted, since gameweek metadata for the live
  season comes from the API path.
* **`fact_player_state` is written by this ingest** even though the brief
  enumerated four tables. Historical price and ownership are training signal no
  other component can produce, the schema is unchanged, and the rows are confined
  to seasons the live path does not touch. `--no-player-state` turns it off.

## 6. Rows loaded

`uv run python scripts/ingest_history.py` (2026-08-18, seasons 2022-23 … 2025-26):

| Season | `dim_team` | `dim_player` | `fact_fixture` | `fact_player_fixture` | `fact_player_state` |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2022-23 | 20 | 807 | 760 | 26,505 | 24,957 |
| 2023-24 | 20 | 890 | 760 | 29,725 | 28,742 |
| 2024-25 | 20 | 815 | 760 | 27,283 | 26,919 |
| 2025-26 | 20 | 870 | 760 | 29,747 | 29,338 |
| **Total** | **80** | **3,382** | **3,040** | **113,260** | **109,956** |

`fact_fixture` is 2 × 380 per season: one schedule row and one result row, with
different `as_of`. `dim_player` exceeds the player count per season because a row
is emitted whenever club, position, element id or name changes — for example
David Raya appears at Brentford at the 2023-24 GW1 deadline and at Arsenal from
the GW2 deadline, which is when that transfer became public.
`fact_player_state` is below `fact_player_fixture` because price and ownership
are gameweek-level facts and a double gameweek does not produce two of them.

## 7. Reproducing

```bash
uv run python scripts/ingest_history.py                  # fetch + load, default seasons
uv run python scripts/ingest_history.py --offline        # replay the local mirror
uv run python scripts/ingest_history.py --build-fixtures # regenerate tests/fixtures/vaastav
uv run pytest tests/unit/test_vaastav.py tests/unit/test_player_mapping.py -q
uv run pytest tests/unit/test_vaastav.py tests/unit/test_player_mapping.py -q -m network
```

The unit suite runs entirely offline from ~124 KB of committed slices in
`tests/fixtures/vaastav/`, which keep the real column drift and the real identity
hazards: both 2022-23 Ben Davieses, both Palmers, the Kesler-Hayden code reissue,
2024-25's manager elements, and 2025-26's defensive-contribution columns. Two
network-marked tests assert the upstream repo still has the shape this loader
expects.
