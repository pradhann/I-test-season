# Pipelines audit

Agent F1, read-only investigation. Two questions: what ran the unattributed
projections ingest on 2026-09-09, and how much duplicated work the scheduler
is actually doing.

All ledger evidence below comes from `data/warehouse/fpl.duckdb` opened with
`Warehouse.read_copy`. Times are UTC. The machine's local zone is UTC-7.

---

## 1. The second scheduler

**Conclusion: there is no second scheduler. The seven rows were written by
`fpl_edge/ingest/projections/cli.py`, running as a subprocess step of a
`post_gw_settlement` chain that a human started from the Pipelines panel, with
`trigger` defaulting to `'scheduler'` because that code path never sets it.**

### The parent run

One `fetch_run` row sits 11 seconds ahead of the seven:

```
started 2026-09-09 16:25:49.000610+00  finished 16:56:25.136280+00
pipeline post_gw_settlement   source deadline_dag
trigger  ui                   status error
run_id   77d3a20b5fc94a0aa081653e05b6578d
note     error: 16/17 steps ok; failed: ingest_odds_fixtures
```

`trigger='ui'` is written only at `fpl_edge/pipelines/runner.py:147`, reached
from `fpl_edge/platform/app.py:558`:

```python
outcome = pipe_runner.run_task(
    task_id, db_path=db_path, trigger="ui", run_id=run_id)
```

That is the `POST /api/pipelines/{task_id}/run` route. Someone clicked Run on
`post_gw_settlement` in the Pipelines tab at 09:25 local.

### The log that was said not to exist

The brief records that no `pipeline_logs` entry was written. One was:
`data/warehouse/pipeline_logs/77d3a20b5fc94a0aa081653e05b6578d.log`, 42,066
bytes, mtime Sep 9 09:56 local. It is filed under the parent run's id, not
under any of the seven ingest run ids, which is why a search keyed on those
ids found nothing. Its header and step lines close the case:

```
# task=post_gw_settlement trigger=ui run_id=77d3a20b5fc94a0aa081653e05b6578d
# started=2026-09-09T16:25:49.000610+00:00 finished=2026-09-09T16:56:25.136280+00:00
step ingest_live: ok (1.2s)
step ingest_odds_fixtures: FAILED (9.4s) ... 503 ... football-data.co.uk/fixtures.csv
step settle_results: ok (0.4s)
step ingest_projections: ok (25.0s) ... 6/7 providers ok, 636 rows appended
step score_projections: ok (0.7s)
step fixture_difficulty: ok (1.3s) wrote 700 rows (GW4-GW38, 20 clubs) to
    data/warehouse/fixture_difficulty.parquet in 0.2s
```

Steps 1 to 3 took 11 seconds, which is why the ingest starts at 16:26:00. The
ingest step ran 25 seconds, which matches the seven rows spanning 16:26:00.414
to 16:26:24.976. The `fixture_difficulty` step is step 6 and it wrote the
parquet at 16:26:26. That is the rewrite the brief flagged as a settlement
chain step, and it was one.

### Why the rows say `scheduler`

`fetch_ledger.RunRecord.__init__` at `fpl_edge/store/fetch_ledger.py:94`:

```python
self.trigger: str = "scheduler"
```

Across the whole repo there is exactly one assignment that changes it,
`fpl_edge/pipelines/runner.py:147`. Every other `record_run` call site keeps
the default. `fpl_edge/ingest/projections/cli.py` has two such call sites,
lines 160 and 177, and sets nothing.

The chain does not call the ingest in-process. `post_gw.settlement_steps` at
`fpl_edge/jobs/post_gw.py:211` launches it as a subprocess:

```python
("ingest_projections",
 [py, "-m", "fpl_edge.ingest.projections.cli", "ingest"]),
```

The subprocess builds its own `RunRecord` and knows nothing about the parent's
trigger. The parent's `'ui'` never reaches it.

### The column is not an observation, it is a constant

Grouping the entire ledger by `(pipeline, source, trigger)`:

| pipeline | sources | triggers seen | rows |
|---|---|---|---|
| `ingest_projections` | 8 provider names | `scheduler` only | 158 |
| everything else | `deadline_dag` | `scheduler`, `ui`, `cli` | 113 |

Not one `ingest_projections` row in the ledger's history carries `ui` or `cli`,
including runs we can independently prove were UI-triggered. **The `trigger`
column is untrustworthy for every pipeline whose row is written outside
`pipelines/runner.py`.** For those rows it records a hardcoded default, and a
reader who treats it as provenance will conclude a scheduler exists that does
not. That is precisely what happened here.

### Everything else ruled out

- `dag_firing` holds **zero rows** for 2026-09-09. No tick fired that day.
  This is consistent, not contradictory: `runner.run_task` deliberately claims
  no firing row for a manual run.
- Only two plists in `~/Library/LaunchAgents` name this repo,
  `com.fpledge.dag.plist` and `com.fpledge.postgw.plist`. `/Library/LaunchDaemons`
  has none. `launchctl list` returns no `com.fpledge` entry, so both are
  unloaded as stated.
- `fpl_mcp` exposes no tool that runs an ingest. Its only references to the
  ingest modules are imports of pure helpers and error strings telling the user
  to run `make ingest` by hand.
- The Telegram bot (`fpl_edge/interfaces/telegram.py`) has no subprocess call
  and no `run_task` path. It reaches the idea registry only.
- `fpl_edge/platform/app.py` has one other pipeline-adjacent trigger,
  `POST /api/content/sources/{key}/fetch` at line 704. It fetches content
  sources, not projections, so it cannot produce these rows.

### What F2 must change

1. `fetch_ledger.RunRecord.trigger` must not default to a real trigger value.
   Default it to `"unknown"` and add `"unknown"` to `TRIGGERS`, so a row that
   nobody attributed says so instead of impersonating the scheduler.
2. Attribution has to cross the subprocess boundary. The chain steps are
   separate processes, so the parent's trigger and run id need to be passed
   down, either as an environment variable that `record_run` reads or as an
   explicit flag on each step's argv. Without this, every subprocess step in
   both chains stays unattributable no matter what the default becomes.
3. Child rows need a parent pointer. A `parent_run_id` column on `fetch_run`
   would have made this a one-query answer rather than a day of elimination.

---

## 2. Double work named in the brief

`due=` values are from `fpl_edge/pipelines/registry.py`. Observed cadence is
from the `fetch_run` ledger.

| pair | same source | cadence A / cadence B | writes A / writes B | readers | resolution |
|---|---|---|---|---|---|
| **A** `crawl_panel` (step 16 of `post_gw.settlement_steps`) vs **B** `panel_picks_crawl` (registry task) | Yes. Both run `python -m fpl_edge.ingest.rivals.panel_picks` over the same `panel_person` query, 43 people. Identical module, identical default budget 700. | A: whenever the chain runs, observed 10:00 and 10:33 daily. B: `Calendar(hour_utc=11, minute=15)`, `stale_window` 23h, observed 11:15 on 09-07 and 11:19 on 09-08, roughly 42 minutes after A. | Both write `dim_manager`, `fact_manager_season`, `fact_manager_gw`, `fact_manager_pick`, `fact_manager_transfer`, `fact_manager_chip`. Identical, not a superset. | `platform/scripts/creators.py`, `platform/scripts/ownership.py`, `models/field/observed.py`, `models/copying/features.py`, `eval/creator_report_card.py`, `interfaces/briefing.py`. All read the tables, neither run specifically. | Delete the `crawl_panel` step from `post_gw.settlement_steps` and keep the standalone task. B is the one with its own ledger row, its own scheduled slot and its own red state in the Pipelines panel. A is invisible in the ledger. |
| **B** `fixture_difficulty` (step 6 of the chain) vs **C** `fixture_ratings_refit` (registry task) | Same model, same input. Both fit Dixon-Coles over the same warehouse snapshot. A runs `models.team_goals.ratings_cache`, B runs `platform.scripts.fixtures --build`, which calls `DixonColesModel().fit` at `fixtures.py:276`. | A: with every chain run, observed twice daily. B: `Calendar(hour_utc=11, minute=0)`, `stale_window` 23h, observed 11:05 on 09-07 and 11:09 on 09-08, about 30 minutes after A. | A writes `fixture_difficulty.parquet`, the blended scalar `lam_O - mu_O` min-max normalised. B writes `fixture_ratings.parquet` plus `fixture_calibration.parquet`, the per-club attack and defence split with the fit's scalars. B is a strict superset: the blended number is recoverable from the split, and `fixtures.py:47-55` records that the reverse is not. | `fixture_ratings.parquet` is read by `fixtures.py:509` (`_Ratings`), which serves the whole Fixtures board. `fixture_difficulty.parquet` is read at exactly one place, `fixtures.py:535` `load_legacy_difficulty`, served under the key `legacy_difficulty` and marked deprecated in the schema. Nothing else reads it. `chat_agent.py:115` and `:174` name an MCP tool called `fixture_difficulty`, not the file. | Delete the `fixture_difficulty` step from the chain and from `deadline_dag.presser_projection_refresh`, drop `load_legacy_difficulty` and its `legacy_difficulty` payload key, and let `fixture_ratings_refit` be the only fit. Two fits of one model, 30 minutes apart, for a file with one deprecated reader. |
| **C** `ingest_projections` in the chain vs `forecast_refresh` | **No. This pair does not exist.** `run_forecast_refresh` at `registry.py:563` runs `fpl_edge.cli.main solve --forecast-only --forecast-source consensus --horizon 5`. `fpl_edge/cli/solve.py` contains no `subprocess` import and no call into any ingest module. It reads projections already in the warehouse. | `forecast_refresh`: `Calendar(hour_utc=11, minute=30)`, `stale_window` 23h. | `forecast_refresh` writes `forecast.parquet` and its sidecar (`solve.py:227-228`). It performs no fetch. | `forecast.parquet` is read by `cli/recommend.py:300`, `cli/main.py:549` and `platform/scripts/brief.py:2291`. | Keep both. There is no duplicated fetch. The real overlap here is a third Dixon-Coles fit, which is item 3.2 below. Correct the brief. |
| **D** `ingest_content` (step 13 of the chain) vs `content_fast_rss` (registry task) | Yes, partially. The chain runs `content.pipeline ingest --backfill-days 3` over all 40 fetchable sources. The task runs the same module with `--backfill-days 1 --only <13 fast-tier keys>`. The 13 fast keys are a subset of the 40. | A: with every chain run. B: `Interval(hours=4)`, `stale_window` 3h, observed firing cleanly every 4 hours, 54 `dag_firing` rows and the highest-volume task in the ledger. | Both write `content_source`, `content_item`, `content_claim` through the same pipeline. A is a superset by source count (40 vs 13) and by window (3 days vs 1). | `platform/scripts/creators.py`, `fpl_mcp/tools/content_tools.py`, the Creators tab. | Keep both, with one change. The tiers are a real decision: 13 creator feeds at 4-hourly and 27 slower sources nightly. But `fpl_edge/ingest/content/fetch.py` caches only robots.txt. There is no ETag, no `If-Modified-Since` and no body TTL, so the chain re-downloads all 13 fast feeds that the 4-hourly task fetched at most 4 hours earlier. Add conditional fetch to `fetch.py` and the overlap costs nothing. |

Three of the four pairs are confirmed. The third is not real as described.

---

## 3. Overlaps the brief did not list

### 3.1 The whole 17-step chain runs twice a day

This is larger than every pair in section 2 combined, and it explains the
duplicate `ingest_projections` timestamps the brief noticed.

`~/Library/LaunchAgents/com.fpledge.postgw.plist` runs
`python -m fpl_edge.jobs.post_gw` at `StartCalendarInterval Hour 3 Minute 0`.
With no `TimeZone` key that is local time, so 10:00 UTC. The registry task
`post_gw_settlement` is `Calendar(hour_utc=10, minute=30)`. Both iterate
`post_gw.settlement_steps`, which `registry.py:384` states outright so the two
paths cannot drift.

The ledger shows both, every day the agent was loaded:

| date | launchd chain (projections step) | registry chain (projections step) |
|---|---|---|
| 2026-09-01 | 10:00 | 10:33, 10:34 |
| 2026-09-02 | 10:00 | 10:33 |
| 2026-09-03 | 10:00 | 10:39, 10:40 (plus 11:34) |
| 2026-09-05 | 10:00 | 10:33 |
| 2026-09-08 | 10:00 | 10:38, 10:39 |

`data/warehouse/jobs/post_gw_*.json` confirms the launchd side independently,
with end stamps at 10:24 to 10:43 on the same dates. `post_gw.main` writes no
`fetch_run` row at all, which is why the Pipelines panel shows one settlement
run a day when two are happening.

The `post_gw.py` docstring calls this a deliberate parity window and says the
doubled run is safe because every step is idempotent. Idempotent is not free.
The doubled run costs a second `crawl_elite` (730s, 661 network requests), a
second `crawl_top10k_sample` (784s, 707 requests), a second `ingest_content`
(138s) and a second pass over every provider, daily. Both plists are currently
unloaded, so this is dormant, not fixed. Reloading the postgw agent restores it.

### 3.2 Three Dixon-Coles fits per day

Beyond the two in pair B, `fpl_edge/cli/solve.py:345` fits a third:

```python
goals = DixonColesModel()
goals.fit(snap, season)
```

That runs inside `forecast_refresh` at 11:30, a third fit of the same model
over the same snapshot, half an hour after `fixture_ratings_refit` at 11:00 and
an hour after the chain's. None of the three reads another's output. The
solve's fit is in-process and feeds the MILP rather than an artefact, so it is
the hardest to remove, but `fixture_ratings.parquet` already holds exactly the
fitted parameters it recomputes.

### 3.3 `presser_projection_refresh` is a fourth copy of the chain's head

`fpl_edge/jobs/deadline_dag.py:636-660` runs `ingest_live`,
`ingest_odds_fixtures`, `ingest_content --backfill-days 2`, `fixture_difficulty`
and `ingest_projections`. Every one of those five is also a step of
`post_gw.settlement_steps`. It fires at `DeadlineRelative(hours_before=30)`, so
on a deadline day these run a third and fourth time. This is defensible (fresh
team news before a deadline is the point) but it means `fixture_difficulty`,
already duplicated by `fixture_ratings_refit`, has three separate callers.

### 3.4 External hosts hit by more than one task

`fantasy.premierleague.com` is reached from 11 modules. Inside one chain run,
`ingest_live`, `settle_results`, `ingest_projections` (via `fpl_ep.py`) and the
four rivals crawls all hit it. This one is mostly already handled:
`fpl_edge/ingest/rivals/client.py` shares a disk cache with per-endpoint TTLs
(`entry/picks` effectively infinite, `entry/` and `entry/history` 12h,
`entry/transfers` 3h, `leagues` 6h, `bootstrap-static/` 1h), and the 09-09 log
shows 940 and 3,746 cache hits against 661 and 707 real requests. The four
crawls are deduplicated at the HTTP layer already.

The cache is also why pair A is cheaper than it looks. At a 42-minute gap every
endpoint the standalone crawl needs is inside its TTL, so the second run is a
near-total replay. Its cost is roughly 2 minutes of process time and a second
full write burst contending for the single DuckDB writer, not network traffic.

`raw.githubusercontent.com` is reached by `fpl_core_insights.py`, `vaastav.py`,
`projections/github_csv.py` and `models/ownership/panel.py`. These fetch
different repositories, so it is a shared host and not shared work.

### 3.5 Artefacts with two writers

There are none. Every parquet, JSON and HTML artefact in `data/warehouse/` has
exactly one writing module. `fixture_difficulty.parquet` has three callers but
they all invoke the same writer, `ratings_cache.write_fixture_difficulty`.

---

## 4. What the build half must do

1. **Change the trigger default.** `fpl_edge/store/fetch_ledger.py:94`, set
   `self.trigger = "unknown"` and add `"unknown"` to `TRIGGERS` on line 72 and
   to `runner.TRIGGERS` at `fpl_edge/pipelines/runner.py:61`. A row nobody
   attributed must say so. Until this lands, no reasoning from the `trigger`
   column is sound for `ingest_projections` or any other subprocess-written row.
2. **Propagate attribution into subprocess steps.** `fpl_edge/jobs/post_gw.py:96`
   (`_run`) and `fpl_edge/pipelines/registry.py` (`run_step`) are the two places
   that launch steps. Pass the parent trigger and run id down, and have
   `fetch_ledger.record_run` read them. Add a `parent_run_id` column to
   `fetch_run` in the `_DDL` and the `ALTER TABLE` block at
   `fetch_ledger.py:70-76`.
3. **Delete the `crawl_panel` step** from `post_gw.settlement_steps`
   (`fpl_edge/jobs/post_gw.py:277`). `panel_picks_crawl` in
   `fpl_edge/pipelines/registry.py:831` covers it, 42 minutes later, with a
   ledger row of its own. Update the `run_panel_picks_crawl` docstring at
   `registry.py:620`, which currently documents the duplication as intended.
4. **Delete the `fixture_difficulty` step** from `post_gw.settlement_steps`
   (`fpl_edge/jobs/post_gw.py:226`) and from
   `deadline_dag.presser_projection_refresh` (`fpl_edge/jobs/deadline_dag.py:649`).
   Then remove `load_legacy_difficulty` (`fpl_edge/platform/scripts/fixtures.py:528`),
   its `legacy_difficulty` payload key, and `DIFFICULTY_NAME` on line 117.
   `fpl_edge/models/team_goals/ratings_cache.py` becomes unreferenced by any
   scheduled path and can go with them. `fixture_ratings_refit` is the survivor.
5. **Add conditional fetch to `fpl_edge/ingest/content/fetch.py`.** Store the
   ETag and `Last-Modified` per source alongside the body and send
   `If-None-Match` and `If-Modified-Since`. This removes the pair D overlap
   without touching the tier design, and it also removes the 4-hourly task's
   own repeat cost on feeds that have not moved.
6. **Resolve the post_gw parity window.** Either unload and delete
   `~/Library/LaunchAgents/com.fpledge.postgw.plist` and drop
   `fpl_edge/jobs/post_gw.py:main` (keeping `settlement_steps`, which the
   registry task needs), or delete the `post_gw_settlement` registry task at
   `fpl_edge/pipelines/registry.py:813`. Keeping both means the full 17-step
   chain, including two multi-hundred-request crawls, runs twice a day. The
   registry task is the better survivor: it writes a ledger row, it captures a
   log, and it is visible in the Pipelines panel. The launchd CLI does none of
   those things.
7. **Decide whether `solve --forecast-only` should read
   `fixture_ratings.parquet`.** `fpl_edge/cli/solve.py:345` refits Dixon-Coles
   from scratch when `fixture_ratings_refit` wrote those exact parameters half
   an hour earlier. This is the only one of the three fits with a real argument
   for staying, since the solve needs the model object and not just its
   parameters, so treat it as a question rather than a deletion.
8. **Correct the brief.** The `ingest_projections` / `forecast_refresh` pair is
   not real. `forecast_refresh` performs no fetch. Whoever acts on the brief's
   list should not spend time removing an ingest that is not there.
