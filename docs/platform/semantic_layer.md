# The semantic layer

The stable query surface for chat, the UI and the MCP server. One vocabulary
instead of every consumer reaching into raw tables — the Argus
single-guarded-endpoint pattern applied to the warehouse itself.

Definition: `fpl_edge/store/views.sql`, applied on every writable
`Warehouse` open (`CREATE OR REPLACE`, idempotent). The macros live **in the
database file**, so every `read_copy()` carries them; the platform's guarded
`/api/query` accepts them (verified — they are `SELECT`-shaped).

## The contract

The **table macros**, each taking `p_as_of TIMESTAMPTZ` as its first (and, with one exception, only) parameter:

```sql
SELECT * FROM sem_projections(TIMESTAMPTZ '2026-08-28 17:30:00+00')
WHERE season = '2026-27' AND gw = 3 AND web_name = 'Haaland';
```

The one exception is `sem_segment_ownership(t, segments)`, which takes a
second parameter because the population it measures is the caller's question,
not a fixed one. Every macro still takes `p_as_of` first and still answers at
that instant.

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
| `sem_projection_weights(t)` | (provider) from the latest fit at `t` | which source has been most accurate, **with the evidence beside the number**: weight, loss (pooled MSE vs settled actuals), baseline_loss (the all-provider mean it had to beat), n_obs, earned flag, the holdout description, and `track_record_gws` — how many settled gameweeks deep the record is. Empty until the calibration loop has scored a settled gameweek, by design |
| `sem_player_form(t)` | (season, gw, code, fixture) | realised returns incl. official **xG/xA/xGC**, bps, defensive stats |
| `sem_ownership(t)` | (season, code) × EO metric | FPL marginal ownership beside every external EO metric (eo_predicted, eo_top10k, eo_elite) |
| `sem_fixtures(t)` | (season, fixture_id, side) | schedule unpivoted to one row per team-side, opponent named |
| `sem_player_match_stats(t)` | (source, season, code, match) | a third party's per-match xG/xA/shots/defensive read, never mistaken for the official return |
| `sem_manager_picks(t)` | (season, gw, entry, element) | every tracked manager's locked squad, named both ways (manager + player), with rank, multiplier, armband. Picks' `as_of` is the deadline, so at a deadline instant you see exactly what had just locked |
| `sem_manager_transfers(t)` | (season, gw, entry, in, out) | every tracked manager's transfers with player names both directions, prices in £m, and the private click-time (`time_utc`) beside the public-at instant |
| `sem_manager_cohort(t)` | (entry_id) | **exactly one cohort per tracked entry**, with the sources that put it there. The single definition of cohort membership; see "Cohorts" below |
| `sem_elite_ownership(t)` | (season, gw, cohort, code) | own%/captain%/EO% per player **per cohort**, plus the counts behind them (`owned_by`, `started_by`, `benched_by`, `captained_by`, `eo_units`). Cohort comes from `sem_manager_cohort`, plus `unclassified` for an entry holding picks with no manager row. Percentages are of managers in that cohort with a stored squad; a pick whose element resolves to no code groups under a NULL `code` row rather than vanishing |
| `sem_manager_segment(t)` | (entry_id, segment) | the crawl SETS an entry was found through — `elite_list`, `winner`, `mini_league`, `expert`, `elite_named`, `snowball`, `top1k`, or an unrecognised source under its own raw text. Deliberately **not** a partition: an entry legitimately belongs to several, and a consumer unions the sets it wants and counts DISTINCT entries |
| `sem_segment_ownership(t, segments)` | (season, gw, code) | the same own%/captain%/EO% definition as `sem_elite_ownership`, over the **union** of the named segments. Takes a second parameter — a VARCHAR list — because the population *is* the question. `n_managers` is `count(DISTINCT entry_id)` with a stored squad, never a sum of set sizes: the segments overlap |

## Effective ownership: one definition

EO was computed three different ways (the macro summed multipliers,
`CohortRates.eo()` reconstructed it from chip rates, and the `ownership_eo`
panel ran its own query with **no cohort filter at all** and called the blended
result "elite"). There is now one:

    ownership = Σ weight[m] for m holding p       / Σ weight[all m]
    eo        = Σ weight[m] × multiplier[m,p]     / Σ weight[all m]
    captaincy = Σ weight[m] for m captaining p    / Σ weight[all m]

`multiplier` is the FPL multiplier as the API resolved it at the deadline: 0
benched, 1 started, 2 captain, 3 triple captain — and 1 for a benched pick
under Bench Boost. Summing the *stored* multiplier is what makes this exact;
reconstructing it from armband plus chip rates mispriced both chips. Ownership
and EO are tracked **separately**: a benched player counts toward ownership and
carries no scoring exposure, so `eo_pct` is not a percentage of anything and
routinely exceeds 100 for a captained premium.

Weights are **not implemented yet**: every weight is 1, so `Σ weight[all m]`
*is* `n_managers`. A weighted variant will be a NEW macro
(`sem_cohort_ownership_weighted`), never a silent change to this one.

`sem_segment_ownership` is the same three formulas over a caller-chosen
population, so it is the same definition and not a fourth one. The
`ownership_eo` panel carries an inline copy of that query for one reason:
`Warehouse.read_copy()` opens the file **read-only**, so views.sql is not
reapplied and a warehouse file written before the macro shipped does not
contain it. A panel that hard-depended on the macro would go dark on exactly
the machine that had not re-ingested yet. The macro stays the canonical
definition for chat, `/api/query` and MCP, and
`test_the_panels_segment_union_equals_the_semantic_layer_macro` in
`tests/unit/test_ownership_panel.py` pins the two column by column — verified
two-sided: breaking either side alone fails it.

The three call sites — `store/views.sql`, `fpl_edge/models/field/cohorts.py`
and the `ownership_eo` panel — are pinned equal by
`tests/unit/test_field_eo_agreement.py`, which compares them against *each
other* on one warehouse rather than against three hand-written constants. The
one legitimate difference is documented there: the macro counts every entry
with a stored pick row, while the Python loader drops a squad failing 15-slot
validation and reports `dropped`.

## Cohorts

Cohort membership is **mutually exclusive**, resolved once by
`sem_manager_cohort(t)` (SQL) and `resolve_cohorts()` in
`fpl_edge/models/field/observed.py` (Python), which the test above pins equal:

1. **`top1k`** — the entry has any `dim_manager` row at or before `t` whose
   `source` starts `top1k`.
2. **`elite`** — otherwise, any `dim_manager` row at or before `t` (the curated
   pool: expert, elite_list, winner, mini_league, snowball, elite_named).
3. **`unclassified`** — has picks but no manager row. Surfaced by
   `sem_elite_ownership` rather than dropped at the join.

**Why `top1k` wins the tie.** The top1k crawl samples a *defined population*
(the top N of the overall table) and its shares are read as an estimate of what
that population does; dropping an entry from it because a curator also listed
them removes exactly the strongest managers and biases the estimate. The elite
pool makes no sampling claim — it is a roster — so losing an overlapping member
costs it only sample size, which is visible in `n_managers`. Membership uses
*any* source row at or before `t`, not the newest, so it never flip-flops as
`t` moves, and with this precedence it is monotone: once `top1k`, always
`top1k`. Before this rule the 17 live entries in both pools were counted in
**both** denominators and inflated both.

Cohort is still *derived* from the free-text `dim_manager.source`. That is
fragile, and `sem_manager_cohort` exists so that the day ingest writes a real
`dim_manager.cohort` column, exactly one expression changes.

## Segments: the other shape of the same question

`sem_manager_cohort` answers "which single denominator does this entry belong
in" and *must* pick one, because a manager counted in two cohorts inflates
both. `sem_manager_segment(t)` answers a different question — "which curated
sets did we find this entry through" — and there an entry legitimately belongs
to several. The two never disagree: `sem_manager_cohort`'s `top1k` entries are
exactly the entries with a `top1k` segment.

Segments exist so a reader can choose the field he is measured against rather
than accept one aggregate. On the live warehouse the sets with a stored GW1
squad are `top1k` 2548, `elite_list` 250, `mini_league` 49, `snowball` 28,
`winner` 12, `elite_named` 8 and `expert` 0 (20 managers in the pool, none
with a crawled squad).

**Overlap is the whole point of the DISTINCT.** `elite_list` + `winner` +
`elite_named` is 270 set memberships over **262 distinct managers**. Adding set
sizes would produce a denominator nobody is in, which is the same
double-counting defect the cohort precedence rule exists to prevent.

**Trustworthiness is not a property this layer can know.** The `snowball`
segment is league-mates of twenty stale seed IDs (`docs/platform/
PANEL_LEDGER.md`, 2026-08-27: not salvageable, and must not be treated as an
elite cohort in any skill, copying or EO analysis). The macro serves it like
any other segment; the *consumer* must carry the flag and the reason. The
`ownership_eo` panel does, in `segments[].trusted` /
`segments[].untrusted_reason`, and keeps it out of every default.

**On the compatibility promise.** This corrected `sem_elite_ownership` in place
rather than forking a new name. The column set only grew and no column's
*meaning* changed — `eo_pct` was already "mean multiplier × 100" — what changed
is which managers land in which denominator, which was a double-counting
defect, not a definition. Forking would have left a known-wrong macro in the
contract that every consumer had to be told to avoid. A genuine redefinition
(weights) still gets a new name.

Column-level detail: the `CONTRACT` dict in
`tests/unit/test_semantic_layer.py` is the machine-checked source of truth.

## Deliberate exclusions

- **Fixture difficulty** is not a macro: a rating is a model output with its
  own refresh cycle, not a fact. It lives in the cached artefact
  `data/warehouse/fixture_difficulty.parquet` (written by the nightly job);
  SQL consumers `read_parquet()` it, panels join it.
- **Consensus is unweighted.** `projection_weight` stays empty until settled
  actuals score the sources; a weighted blend before that would be
  fabrication (MASTER_PROMPT Phase 2.5). The calibration loop that earns the
  weights is `fpl_edge/eval/projection_scoring.py` (run by `post_gw` right
  after results settle), and the earned weights are served with their
  evidence by `sem_projection_weights(t)`. A weighted *blend* remains a
  future NEW macro, not a silent change to the consensus — and any answer
  built on the weights should quote `track_record_gws`: with one gameweek
  scored, the leaderboard is one gameweek of evidence.

## Schema note

The projection base tables (`fact_projection`, `fact_external_ownership`,
`fact_predicted_lineup`, `projection_weight`, and the `projection_normalized`
view) are now part of `store/schema.sql`, so a fresh warehouse is complete at
birth. The projections package's versioned migrations remain authoritative
for *evolution* and stay idempotent over this base.
