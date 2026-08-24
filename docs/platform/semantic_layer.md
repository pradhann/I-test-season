# The semantic layer

The stable query surface for chat, the UI and the MCP server. One vocabulary
instead of every consumer reaching into raw tables — the Argus
single-guarded-endpoint pattern applied to the warehouse itself.

Definition: `fpl_edge/store/views.sql`, applied on every writable
`Warehouse` open (`CREATE OR REPLACE`, idempotent). The macros live **in the
database file**, so every `read_copy()` carries them; the platform's guarded
`/api/query` accepts them (verified — they are `SELECT`-shaped).

## The contract

The **table macros**, each taking one parameter `p_as_of TIMESTAMPTZ`:

```sql
SELECT * FROM sem_projections(TIMESTAMPTZ '2026-08-28 17:30:00+00')
WHERE season = '2026-27' AND gw = 3 AND web_name = 'Haaland';
```

**Point in time is the whole point.** Each macro answers with the newest row
per entity observed **at or before** `p_as_of` — the same semantics as
`Snapshot.table()`. Pass now for "current knowledge"; pass a deadline to see
exactly what was knowable then. Enforced by
`tests/unit/test_semantic_layer.py` (a fact recorded after `p_as_of` is
invisible; verified to fail when the filter is removed).

**Compatibility promise:** columns may be **added, never renamed or
removed**; a behaviour change gets a new macro name. The promised column sets
are pinned by `test_the_column_contract_only_ever_grows`.

| Macro | Grain | Answers |
|---|---|---|
| `sem_players(t)` | (season, code) | identity + price, ownership %, status, news, team |
| `sem_projections(t)` | (source, season, gw, code) | every provider's xPts/xMins/p_appear side by side, with names |
| `sem_projection_consensus(t)` | (season, gw, code) | n_sources, mean/min/max/**spread**/sd of xPts — source disagreement IS the uncertainty |
| `sem_player_form(t)` | (season, gw, code, fixture) | realised returns incl. official **xG/xA/xGC**, bps, defensive stats |
| `sem_ownership(t)` | (season, code) × EO metric | FPL marginal ownership beside every external EO metric (eo_predicted, eo_top10k, eo_elite) |
| `sem_fixtures(t)` | (season, fixture_id, side) | schedule unpivoted to one row per team-side, opponent named |
| `sem_player_match_stats(t)` | (source, season, code, match) | a third party's per-match xG/xA/shots/defensive read, never mistaken for the official return |
| `sem_manager_picks(t)` | (season, gw, entry, element) | every tracked manager's locked squad, named both ways (manager + player), with rank, multiplier, armband. Picks' `as_of` is the deadline, so at a deadline instant you see exactly what had just locked |
| `sem_manager_transfers(t)` | (season, gw, entry, in, out) | every tracked manager's transfers with player names both directions, prices in £m, and the private click-time (`time_utc`) beside the public-at instant |
| `sem_elite_ownership(t)` | (season, gw, cohort, code) | own%/captain%/EO% per player **per cohort** — `top1k` (the top-of-overall standings sample, growable toward 10k) vs `elite` (the named/curated crawl pool), matching the cohort rule in `fpl_edge/models/field/observed.py`. Percentages are of managers in that cohort with a stored squad; a pick whose element resolves to no code groups under a NULL `code` row rather than vanishing |

Column-level detail: the `CONTRACT` dict in
`tests/unit/test_semantic_layer.py` is the machine-checked source of truth.

## Deliberate exclusions

- **Fixture difficulty** is not a macro: a rating is a model output with its
  own refresh cycle, not a fact. It lives in the cached artefact
  `data/warehouse/fixture_difficulty.parquet` (written by the nightly job);
  SQL consumers `read_parquet()` it, panels join it.
- **Consensus is unweighted.** `projection_weight` is empty until GW1 actuals
  score the sources; a weighted blend before that would be fabrication
  (MASTER_PROMPT Phase 2.5). When weights are earned, the weighted view will
  be a NEW macro, not a silent change to this one.

## Schema note

The projection base tables (`fact_projection`, `fact_external_ownership`,
`fact_predicted_lineup`, `projection_weight`, and the `projection_normalized`
view) are now part of `store/schema.sql`, so a fresh warehouse is complete at
birth. The projections package's versioned migrations remain authoritative
for *evolution* and stay idempotent over this base.
