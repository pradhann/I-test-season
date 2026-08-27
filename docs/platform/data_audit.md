# Data-layer audit: can the warehouse answer the six semantic-layer questions today?

Audit date **2026-08-23** (GW1 matches played 21–23 Aug; GW1 points not yet final).
Every query below was actually executed against a `Warehouse.read_copy()` of
`data/warehouse/fpl.duckdb` and the output pasted verbatim (truncated). Code
references are to this repo. Paths written `FPL-MCP/...` below are historical:
the toolbelt was folded into this repo as `fpl_mcp/` on 2026-08-27, so read
`FPL-MCP/tools/x.py` as `fpl_mcp/tools/x.py`. The audit's findings are
unaffected; only the prefix moved.

Row counts at audit time: `fact_player_fixture` 113,260 (all pre-2026-27) ·
`fact_odds` 115,865 · `fact_projection` 59,813 · `fact_predicted_lineup` 1,779 ·
`fact_odds_derived` 1,720 · `fact_external_ownership` 14,822 · `content_claim` 224 ·
`fact_manager_season` 12,854 · `projection_weight` **0**.

Reading rule observed throughout: heavy reads go through `Warehouse.read_copy()`
(`fpl_edge/store/warehouse.py:439`) so the single writer is never blocked.

---

## Q1. xPts and xG for one player across all sources, side by side, for GW N

**xPts: ANSWERABLE.** Haaland is `code = 223094` in every season 2022-23 → 2026-27
(element_id drifts 318→355→351→430→411, which is why nothing may key on element).

```sql
SELECT source, gw, xpts, xmins, xp_if_appears, p_appear, fetched_at
FROM projection_normalized
WHERE player_code = 223094 AND season = '2026-27' AND gw = 1
QUALIFY ROW_NUMBER() OVER (PARTITION BY source ORDER BY fetched_at DESC) = 1
ORDER BY source
```

```
        source  gw      xpts      xmins  xp_if_appears  p_appear                       fetched_at
0       fpl_ep   1  4.000000        NaN            NaN   1.00000 2026-08-20 18:16:02+00:00
1      fplform   1  4.821280        NaN        5.94299   0.81126 2026-08-20 18:15:53+00:00
2  gh_blueladd   1  4.960000  75.700000            NaN       NaN 2026-08-20 18:16:13+00:00
3  gh_fplbench   1  7.444282  84.371429            NaN       NaN 2026-08-20 18:16:10+00:00
```

Four xPts opinions side by side. `premierinjuries` is absent by design — it
publishes `p_appear` only, and only for flagged players (Haaland is not flagged).
The `QUALIFY … fetched_at DESC` dedup is mandatory: the table is append-only and
holds every historical fetch. Point-in-time variant: add
`AND fetched_at <= TIMESTAMPTZ '2026-08-21 17:30:00+00'` — verified to return the
same four rows at the GW1 deadline.

**xG: MISSING as a projection.** `projection_normalized` carries exactly
`source, player_code, season, gw, xmins, xpts, xp_if_appears, p_appear, fetched_at`
(verified via `DESCRIBE`) — **no source in the inventory publishes projected xG**
(see `docs/platform/projection_providers.md`). Two adjacent things exist:

1. *Odds-implied* xG from `fact_odds_derived` (`xg_share` × the player's own
   team's `team_lambda`). The join must anchor to the player's `team_code` via
   `dim_player`, otherwise both teams' lambdas match and the row doubles:

```sql
WITH latest AS (
  SELECT * FROM fact_odds_derived
  QUALIFY ROW_NUMBER() OVER (PARTITION BY fixture_key, entity_type, entity_code,
                             market, method ORDER BY as_of DESC) = 1),
pl AS (SELECT code, team_code FROM dim_player WHERE season='2026-27'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of DESC) = 1)
SELECT s.fixture_key, s.value xg_share, l.value team_lambda,
       round(s.value*l.value,3) implied_xg
FROM latest s
JOIN pl ON pl.code = s.entity_code
JOIN latest l ON l.fixture_key = s.fixture_key AND l.market='team_lambda'
   AND l.method='team_totals' AND l.entity_type='team' AND l.entity_code=pl.team_code
WHERE s.entity_code = 223094 AND s.market = 'xg_share'
```
```
                                      fixture_key  xg_share  team_lambda  implied_xg
0  2026-27:2026-08-23:manchester-city:bournemouth   0.30517      2.35786        0.72
```

2. *Realised* xG in `fact_player_fixture.expected_goals` — historical seasons only
   (Q4), nothing for 2026-27.

So "xPts across sources" works today; "xG across sources" has exactly one source
(odds-derived, one method, GW1 fixtures only).

---

## Q2. Which sources disagree most — about player X, and overall

**ANSWERABLE.** Pairwise, Haaland GW1:

```
            s1           s2       x1        x2      diff
0       fpl_ep  gh_fplbench  4.00000  7.444282  3.444282
1      fplform  gh_fplbench  4.82128  7.444282  2.623002
2  gh_blueladd  gh_fplbench  4.96000  7.444282  2.484282
3       fpl_ep  gh_blueladd  4.00000  4.960000  0.960000
4       fpl_ep      fplform  4.00000  4.821280  0.821280
5      fplform  gh_blueladd  4.82128  4.960000  0.138720
```

fplbench is the outlier on Haaland by ~2.5–3.4 pts. Overall (stddev of latest
xpts per player, GW1, ≥3 sources):

```sql
WITH latest AS (
  SELECT source, player_code, xpts FROM projection_normalized
  WHERE season='2026-27' AND gw=1 AND xpts IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY source, player_code ORDER BY fetched_at DESC)=1)
SELECT l.player_code, any_value(p.web_name) web_name, count(*) n_sources,
       round(stddev_samp(xpts),3) sd, round(min(xpts),2) lo, round(max(xpts),2) hi
FROM latest l
LEFT JOIN (SELECT code, web_name FROM dim_player WHERE season='2026-27'
           QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of DESC)=1) p
  ON p.code = l.player_code
GROUP BY 1 HAVING count(*) >= 3 ORDER BY sd DESC LIMIT 8
```
```
   player_code      web_name  n_sources     sd    lo    hi
0       221632        Romero          3  2.403  0.00  4.16
1       432720      Trafford          4  2.095  0.34  5.46
2       424876    Szoboszlai          4  2.016  2.50  7.15
3       221389          John          4  1.997  0.19  4.55
4       485055        Kinsky          4  1.943  0.52  5.15
5       121709       Benitez          4  1.890  0.09  4.40
6       109745  Arrizabalaga          4  1.734  0.10  3.54
7       240514      Scherpen          3  1.661  0.00  3.31
```

The disagreement leaderboard is dominated by goalkeepers with unresolved #1 shirts
— exactly the xMins question the ensemble exists for. Caveats a semantic layer
must encode: only 4 of 5 sources emit xpts; a raw AVG/STDDEV without the
latest-per-(source,player) dedup silently averages every historical fetch.

---

## Q3. Aggregate xPts by team / position / price band, filterable

**ANSWERABLE — but only via a four-table hand-rolled join.** No view exists;
each table needs its own latest-per-entity dedup before joining
(`projection_normalized` × `dim_player` × `fact_player_state` × `dim_team`,
all on `(season, code)` / `team_code`):

```sql
WITH latest AS (... as Q2 ...),
per_player AS (SELECT player_code, avg(xpts) xpts_mean FROM latest GROUP BY 1),
pl AS (SELECT code, position, team_code FROM dim_player WHERE season='2026-27'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of DESC)=1),
st AS (SELECT code, price_tenths FROM fact_player_state WHERE season='2026-27'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY code ORDER BY as_of DESC)=1),
tm AS (SELECT team_code, short_name FROM dim_team WHERE season='2026-27'
       QUALIFY ROW_NUMBER() OVER (PARTITION BY team_code ORDER BY as_of DESC)=1)
SELECT tm.short_name team,
       CASE pl.position WHEN 1 THEN 'GKP' WHEN 2 THEN 'DEF' WHEN 3 THEN 'MID' ELSE 'FWD' END pos,
       CASE WHEN st.price_tenths<55 THEN '<5.5' WHEN st.price_tenths<80 THEN '5.5-7.9' ELSE '8.0+' END band,
       count(*) players, round(avg(xpts_mean),2) avg_xpts
FROM per_player pp JOIN pl ON pl.code=pp.player_code
JOIN st ON st.code=pp.player_code JOIN tm ON tm.team_code=pl.team_code
GROUP BY 1,2,3 ORDER BY avg_xpts DESC
```
```
  team  pos     band  players  avg_xpts  max_xpts
0  MCI  FWD     8.0+        1      5.31      5.31
1  MUN  MID     8.0+        3      4.09      5.34
2  BRE  FWD     8.0+        1      4.04      4.04
3  ARS  GKP  5.5-7.9        1      3.96      3.96
```

Filterable by any of team/pos/band/source with WHERE clauses. What is missing is
not data but **ergonomics**: ~40 lines of CTE boilerplate that every consumer
must reproduce correctly (the dedup, the position decode, the tenths→£
conversion). One `player_current` view + one `projection_latest` view collapses
this to a 5-line query.

---

## Q4. A player's underlying-stats trend over the last K gameweeks

**ANSWERABLE for 2022-23 → 2025-26. EMPTY for 2026-27.**

Underlying columns that actually exist in `fact_player_fixture`
(`fpl_edge/store/schema.sql:96-125`): `minutes, goals_scored, assists,
clean_sheets, goals_conceded, own_goals, penalties_saved, penalties_missed,
yellow_cards, red_cards, saves, bonus, bps, starts, tackles,
clearances_blocks_interceptions, recoveries, defensive_contribution,
expected_goals, expected_assists, expected_goals_conceded, total_points, was_home`.

Measured non-null coverage — the four defensive columns are honest NULLs before
2025-26 (the stats did not exist), never zeros:

```
    season  rows_  n_minutes  n_bps  n_tackles  n_recoveries  n_defcon   n_xg
0  2022-23  26505      26505  26505          0             0         0  26505
1  2023-24  29725      29725  29725          0             0         0  29725
2  2024-25  27283      27283  27283          0             0         0  27283
3  2025-26  29747      29747  29747      29747         29747     29747  29747
```

Haaland, last 5 fixtures of 2025-26 (note DGW/blank rows: GW36 has two fixture
rows, GW38 is a 0-minute blank — a trend query must group per fixture, not per gw):

```
   gw  minutes  goals_scored  assists  expected_goals  expected_assists  bps  bonus  total_points
0  38        0             0        0            0.00              0.00    0      0             0
1  37       90             1        0            0.23              0.52   36      3             9
2  36       90             1        1            1.22              0.09   39      2            11
3  36        0             0        0            0.00              0.00    0      0             0
4  35       90             1        0            0.53              0.01   32      1             7
```

**2026-27 is 0 rows, and — more important — there is no job that will ever fill
it.** Traced:

* The only writer of `fact_player_fixture` is the vaastav historical ingest
  (`fpl_edge/ingest/vaastav.py` via `scripts/ingest_history.py`), which loads
  *completed* seasons 2022-23…2025-26; vaastav's `data/2026-27/` has no `gws/`.
* The live path (`scripts/ingest_live.py`, `fpl_edge/ingest/fpl_api.py`) writes
  `dim_*`, `fact_player_state`, `fact_fixture` — never `fact_player_fixture`.
* The settlement job `fpl_edge/jobs/post_gw.py:91-101` runs `ingest_live`,
  odds fixtures/props, `idea track`, `content score`, content ingest, elite
  crawl, intel, and two reports. **No step ingests per-player results.**

Downstream consequences already visible: `projection_weight` is 0 rows and will
stay 0 (the ensemble's earned weights need GW1 actuals to score providers);
`score_creators` (content scoring reads `fact_player_fixture`,
`fpl_edge/ingest/content/scoring.py`) no-ops for 2026-27;
`scripts/weekly_idea_report.py:266` reads `max(gw)` from the table and gets 0.
The current-season trend question therefore cannot be answered from the
warehouse until a results-settlement step is added (the FPL element-summary
endpoint has the data now; the FPL-MCP `get_player_history` tool proves it is
reachable — it just never lands in DuckDB).

---

## Q5. Fixture difficulty for the next K gameweeks from OUR ratings

**PARTIAL — one GW of odds-derived ratings exists; the fitted-ratings cache is
built but has never been run.**

What exists today:

1. **Odds-derived team lambdas** in `fact_odds_derived` — three methods per
   fixture, GW1's 10 fixtures only (odds are only quoted for imminent fixtures):

```
SELECT fixture_key, entity_code, round(value,3) team_lambda FROM fact_odds_derived
WHERE market='team_lambda' AND method='dixon_coles'
QUALIFY ROW_NUMBER() OVER (PARTITION BY fixture_key, entity_code ORDER BY as_of DESC)=1

                                      fixture_key  entity_code  team_lambda
0        2026-27:2026-08-21:arsenal:coventry-city            3        2.651
1        2026-27:2026-08-21:arsenal:coventry-city            9        0.509
2  2026-27:2026-08-22:brentford:tottenham-hotspur           94        1.529
...
methods: poisson_indep / team_totals / dixon_coles, 10 fixtures each, as_of 2026-08-20
```

   Plus `clean_sheet_prob` (3 methods × 40 team-fixtures). This is difficulty
   for **K=1** only, keyed by natural-string `fixture_key`, not `fixture_id`.

2. **`fpl_edge/models/team_goals/ratings_cache.py`** — a complete, documented
   module that fits `DixonColesModel` at the latest snapshot and writes
   `data/warehouse/fixture_difficulty.parquet` (one row per upcoming
   (fixture, team), `difficulty` normalised to [0,1] over the league). **The
   parquet does not exist on disk** (`ls data/warehouse/*.parquet` shows only
   `forecast.parquet` and `gw1_projection.parquet`), and nothing in
   `post_gw.py` or `deadline_dag.py` invokes it — its only consumer is
   `fpl_edge/platform/scripts/fixtures.py`, which will find no file.

3. **Live fits**: `fpl_edge/interfaces/dossier.py:349,403` fits
   `DixonColesModel()` per dossier call (~1 min) — correct but unqueryable:
   nothing is persisted, so "difficulty for the next 6 GWs for all 20 clubs" is
   not a SQL question today.

The fixture *list* for all of GW1–38 is in `fact_fixture` (380 fixtures,
verified), and deadlines GW1–38 are in `dim_event` — so the horizon scaffolding
exists; only the rating column is missing. **Smallest fix: run
`uv run python -m fpl_edge.models.team_goals.ratings_cache` from the post-GW /
T-30h jobs** (or land the same frame in a `fact_team_rating` PIT table so it is
joinable in SQL and replayable at past as-ofs).

---

## Q6. Ownership and effective ownership, template vs differential

**ANSWERABLE — with two traps that both fired during this audit.**

Metrics actually present in `fact_external_ownership` (enumerated, not assumed):

```
  provider        metric   season  gw  rows_  players                    latest
0  livefpl      eo_elite  2025-26  38   5040      840 2026-08-20 18:16:01+00:00
1  livefpl  eo_predicted  2026-27   1   4147      595 2026-08-20 18:15:58+00:00
2  livefpl     eo_top10k  2025-26  38   5040      840 2026-08-20 18:15:59+00:00
```

Trap 1 — **the migration documents different metric names than the data
carries**: `fpl_edge/ingest/projections/migrations/001_projections.sql` comments
say `'own_top10k'`/`'own_elite'`; the ingest
(`fpl_edge/ingest/projections/livefpl.py:64-65`) writes `eo_top10k`/`eo_elite`,
and all three are *effective* ownership (× captaincy), not plain ownership. A
query filtered on the documented names returns zero rows silently.

Trap 2 — **top10k/elite are stored under `season='2025-26', gw=38`** (LiveFPL's
last-resolved cohort), while `eo_predicted` is under `2026-27 gw1`. A join on
"current season and gw" for all three metrics silently drops two of them.

Working template/differential query (official ownership from
`fact_player_state.selected_by_pct`, in percent; `eo_predicted` a fraction that
may exceed 1.0):

```
      web_name  selected_by_pct  eo_pred_pct    bucket
0      Haaland             69.2        120.8  template   <- EO > 100%: ownership x captaincy
1  B.Fernandes             51.3         76.2  template
2   João Pedro             64.1         64.5  template
3   Szoboszlai             41.7         41.7       mid
4         Raya             37.7         35.6       mid
```

`fact_player_state` for 2026-27: 604 players × 7 polls, latest
2026-08-23 10:04Z — ownership is fresh. Historical `selected_by_pct` exists back
to 2022-23 (derived from vaastav counts; see `docs/data_lineage.md` §4).
Ownership *forecasting* has no out-of-sample score
(`docs/known_weaknesses.md` §10), but that is a model gap, not a data gap.

---

## Cross-cutting traps a semantic layer must handle

### A. The identity join — verified clean

`projection_normalized.player_code`, `fact_external_ownership.code`,
`fact_predicted_lineup.code`, `content_claim.player_code` and
`fact_odds_derived.entity_code` (player rows) all carry the same value domain as
`dim_player.code`. Measured overlap:

```
projection_normalized 2026-27:  599/599 codes match dim_player  (100.00%)
fact_external_ownership:      1,435/1,435   fact_predicted_lineup: 305/305
content_claim: 66/66           fact_odds_derived players: 370/370
```

100% by construction — every ingest resolves to `code` at fetch time and *drops
and counts* what it cannot resolve (17 of 11,388 rows in the last smoke run;
`docs/platform/projection_providers.md` §4). The join itself is safe. The two
residual hazards: `dim_player` is versioned, so it must be deduped
latest-per-`(season, code)` before any join (forgetting this fans out rows), and
`fact_odds_derived.entity_code` is a **team_code for team rows and a player code
for player rows** in one column — filter `entity_type` first, and anchor player
xG to the player's own team's lambda (Q1).

### B. Point-in-time discipline — three regimes, one of them unnamed

`PIT_KEYS` (`fpl_edge/store/warehouse.py:36-49`) covers exactly 8 tables:
`dim_event, dim_team, dim_player, fact_player_state, fact_fixture,
fact_player_fixture, fact_odds, fact_odds_derived`. Verified live:
`Snapshot.table()` raises `KeyError` for **all** of `fact_projection`,
`fact_external_ownership`, `fact_predicted_lineup`, `content_claim`,
`transcript_segment`, `fact_manager_season`.

So the warehouse has three PIT regimes:

1. **`as_of` + Snapshot** — the 8 tables above. Filter and dedup handled by
   `Snapshot.table()`.
2. **`fetched_at`/`as_of` by convention, no Snapshot** — `fact_projection`
   (view renames its `as_of` to `fetched_at`), `fact_external_ownership`,
   `fact_predicted_lineup`, `projection_weight`. PIT reads must hand-write
   `fetched_at <= deadline` + latest-per-entity (`ProjectionStore.as_of()` does
   it in Python; there is no SQL-side helper).
3. **`published_at`** — content tables. Sanctioned read is
   `content.store.claims_visible_at`; `content_item.published_at` is the only
   permitted filter column. **`transcript_segment` has no time column at all**
   (`item_id, seq, start_s, text`) — its PIT status is inherited via a join to
   `content_item`, which nothing enforces.

A semantic layer that applies one uniform `as_of <= t` rule will crash on
family 2/3 (no such column via Snapshot) or, worse, read family 2 unfiltered.
`fact_manager_season` rows are all stamped at crawl time (one instant,
2026-08-19), which is honest — final historical ranks — but means the table has
no usable within-season PIT story.

### C. Season / gw / key conventions — two live inconsistencies

Measured `SELECT DISTINCT season` per table:

* Dash form `'2026-27'` everywhere **except `fact_manager_season`, which uses
  slash form `'2006/07'…'2025/26'`** (from the FPL entry-history API). Any join
  or UNION across the manager tables and the rest of the warehouse on `season`
  matches zero rows, silently. (The registry already hit this class of bug once:
  `docs/known_weaknesses.md` §4 "Season labels".)
* `fact_odds` / `fact_odds_derived` have **no season or gw columns**; season is
  the prefix of the natural `fixture_key`
  (`2026-27:2026-08-21:arsenal:coventry-city`). Despite the schema comment
  "season:fixture_id once matched", **0 of 115,865 rows use the fixture_id
  form** — the `--match-fixtures` path is documented as never exercised
  end-to-end (`docs/data_sources.md` §8). Worse, the two odds tables use
  **different slug vocabularies for the same clubs in the same season**:
  football-data slugs in `fact_odds` (`man-city`, `nott-m-forest`, `hull`)
  vs Odds-API slugs in `fact_odds_derived` (`manchester-city`, `hull-city`).
  The only bridge is Python-side alias logic in
  `fpl_edge/models/ensemble/sources.py:73-149` (`odds_with_fixture_keys`);
  no SQL join between odds and `fact_fixture` is possible today.
* `fact_external_ownership.gw` means "the GW being predicted" for
  `eo_predicted` but "the last finished GW" for `eo_top10k`/`eo_elite` (trap 2
  in Q6).
* Append-only means every naive `SELECT` double-counts: the latest-per-key
  `QUALIFY` idiom appears in every working query above and must be part of any
  generated SQL.

### D. Operational note on locks

`Warehouse.read_copy()` is the sanctioned heavy-read path. The FPL-MCP server
instead opens the **live file** with `Warehouse(path, read_only=True)`
(`FPL-MCP/tools/edge_tools.py:103`, `dossier_tools.py:191`,
`content_tools.py:134`) — DuckDB is one-writer-XOR-many-readers, so a dossier
call held open during an ingest window blocks the writer for its duration.

---

## What the MCP server can answer TODAY

Warehouse-backed (read-only open of the live DuckDB):

* `player_dossier` (`dossier_tools.py:123`) — one player's price, ownership/EO,
  cached projection, minutes risk, fixture difficulty via a **live** Dixon-Coles
  fit, set-piece duty, scorer odds, injury news; PIT via `snapshot_at(as_of)`;
  gaps are named, not hidden. Closest existing answer to Q1/Q5/Q6 — one player
  at a time, no cross-source table.
* `player_intel` (`dossier_tools.py:214`) — `intel_item` rows filtered by
  `published_at`.
* `set_piece_changes` (`dossier_tools.py:329`) — `set_piece_change` table.
* `fpl_creator_consensus` / `fpl_player_claims` (`content_tools.py:161,335`) —
  claims via the PIT-correct `claims_visible_at(published_at)`.
* `fpl_creator_track_record` (`content_tools.py:244`) — **raw SQL** over
  `claim_outcome` / `creator_score`; empty for 2026-27 until results settle (Q4).
* `fpl_content_sources` (`content_tools.py:410`) — raw SQL over
  `content_source` / `content_item`.
* `submit_idea` / `review_ideas` / `track_ideas` / `mark_idea_acted` /
  `weekly_decision_report` / `engine_status` (`edge_tools.py`) — idea-registry
  tables; `engine_status` uses `snapshot_at(now)` for next deadline + player count.

Live-FPL-API-backed (no warehouse at all — `FPL-MCP/utils/fpl_data.py` JSON
cache of bootstrap/fixtures/element-summary):

* `query_fpl_players` (`query_tools.py:22`) — filter bootstrap elements.
* `query_fpl_data` (`general_tools.py:61`) — players/fixtures/teams with
  eq/lt/gt/contains filters.
* `get_team_summary` (`general_tools.py:236`) — last-N team form from API.
* `get_player_history` (`general_tools.py:274`) — current-season per-GW history
  from element-summary — **can already see GW1 stats the warehouse cannot**.
* `get_team_picks`, `get_expert_teams_summary`, `get_expert_transfers`,
  `get_manager_history` — live entry API.
* `summarise_fpl_youtube`, `fetch_youtube_transcript` — YouTube, no warehouse.

Against the six questions: **no MCP tool reads `projection_normalized`,
`fact_external_ownership`, `fact_odds_derived`, or `fact_player_fixture`** —
Q1–Q3 (cross-source), Q4 (warehouse trend), Q5 (all-club difficulty) and Q6
(aggregate EO) are answerable only by the SQL in this document, not by any
existing tool. The dossier answers the single-player slice of Q1/Q5/Q6.

---

## Ranked: smallest schema/view changes to make all six answerable

1. **Settle live-season results into `fact_player_fixture`** (new
   `scripts/ingest_results.py` reading FPL element-summary per finished GW,
   `as_of` = 09:00-UK-next-day points-finalisation per `docs/data_lineage.md`
   §3; add one `_run(...)` line to `fpl_edge/jobs/post_gw.py:91`). Unblocks Q4
   for 2026-27, `projection_weight` earning, creator scoring, and the weekly
   report — the single highest-leverage change in this audit.
2. **`CREATE VIEW projection_latest`** — latest row per
   `(source, season, gw, player_code)` from `projection_normalized` — and
   **`projection_consensus`** — per `(season, gw, player_code)`: n_sources,
   mean/sd/min/max xpts. Q1 and Q2 become one-liners; every consumer stops
   re-implementing the dedup. (10 lines of SQL next to `003_xmins.sql`.)
3. **`CREATE VIEW player_current`** — deduped `dim_player` × latest
   `fact_player_state` × `dim_team` with decoded position and price band.
   Q3 and Q6 become single joins; it is also the missing base for any MCP
   "aggregate xPts" tool.
4. **Run `ratings_cache` from the DAG** (one job line in `post_gw.py` /
   `deadline_dag.py`), or better, land the same frame in a `fact_team_rating`
   PIT table `(season, gw, fixture_id, team_code, difficulty, as_of)` so Q5 is
   SQL-queryable and replayable at past as-ofs.
5. **Materialize the odds↔fixture bridge**: a `fixture_key_map(fixture_key,
   season, fixture_id, source)` table populated by the alias logic already in
   `fpl_edge/models/ensemble/sources.py:73-149`, covering both slug
   vocabularies. Makes `fact_odds`/`fact_odds_derived` joinable to
   `fact_fixture`, `dim_team`, and the projection views.
6. **Fix the `fact_external_ownership` contract**: correct the metric names in
   `001_projections.sql`'s comment (`eo_top10k`/`eo_elite`, all effective), and
   once GW1 settles, ingest top10k/elite under the current `(season, gw)` so
   "this week's template" is a same-key join.
7. **Normalise `fact_manager_season.season`** to dash form at ingest (or add a
   `season_alias` view) so manager history joins the rest of the warehouse.
8. **Second PIT registry for fetch-stamped tables**: a
   `FETCHED_KEYS = {fact_projection: (...), fact_external_ownership: (...),
   fact_predicted_lineup: (...)}` map plus a `Snapshot.provider_table()` that
   applies `fetched_at <= as_of` + dedup — closing the gap where a semantic
   layer (or the MCP server) reads provider tables unfiltered. Give
   `transcript_segment` PIT status by documenting the mandatory
   `content_item` join.
