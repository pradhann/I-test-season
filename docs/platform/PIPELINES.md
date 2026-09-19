# Data pipelines: the runbook, the map, and the closed spec

Status: SPEC CLOSED 2026-08-31; SCOPE ELEVATED 2026-09-01. The owner's
direction: "pipelines are the most important thing here... take this as the
main product, pipelines is the edge, organize it properly, best in class."
Concretely: a first-class `fpl_edge/pipelines/` package (registry, runner,
health) where every run is timed, logged (per-run log files with tails in the
ledger), and health-scored ({ok, failing, stale, running, never_ran,
disabled} with a reason, consecutive-failure counts, avg duration over the
last 20 ok runs); one `pipeline_status()` payload feeding a control-panel UI
with per-pipeline health, last run, average time, next due, and
trigger-with-confirm for metered tasks. BUILT 2026-09-01: all six phases
shipped and live-verified the day after the spec closed. Commits: 6bbd4eb
(ledger + write-on-change) · d843416 (pipelines package + scheduler + new
schedules) · b8cd61e (control panel) · 8f64520 (one fetch discipline +
receipts). Collateral fixes from the GW2 investigation: b0943ca (transfer
entity key), ee16f16 (paid-means-kept). Open by design: the post_gw parity
window (retirement procedure in the plist) and the deep router/Telegram
untangling (its own task chip).

---

## 0 · The runbook

Everything in this section is the operating manual: what runs, when, what it
writes, what breaks when it stops, and how to run one by hand. The table and
the commands are generated from `fpl_edge/pipelines/registry.py` by
`scripts/pipelines_runbook.py`, so a task added or rescheduled in the registry
and not regenerated here fails `tests/unit/test_no_double_work.py`. Sections 1
to 6 below are the closed spec that produced the system and are cited from the
code by section number; they are history, not the operating manual.

**Where the facts come from.** The schedule, the stale window, the family and
the description are registry columns. Health, last run and next due are
computed by `fpl_edge/pipelines/health.py` over the `fetch_run` ledger and
served by the `pipeline_board` panel. Nothing in this file is a second
opinion about any of them.

**Reading the stale column.** Two different windows share the word. The
registry's `stale_window` is the scheduler's rule: a firing that is due
longer ago than this is recorded `skipped_stale` and dropped rather than run
late. Health's own staleness is about the data: it asks how long ago the last
run succeeded, against 30h for a daily task and twice the interval for an
interval one. The panel serves both, named apart.

**The one overlap this table cannot show.** `deploy/com.fpledge.postgw.plist`
runs `python -m fpl_edge.jobs.post_gw`, which iterates the same
`settlement_steps` list as the `post_gw_settlement` task, half an hour
earlier. Loading that agent means the whole chain, including two crawls of
several hundred requests each, runs twice a day. `post_gw.main` writes no
`fetch_run` row, so the second run is invisible to the panel and to the
overlap test below. Retiring the plist is the scheduler workstream's item.

**Both launchd agents are unloaded today**, which is why every row in the
panel is past its own window: nothing has fired on a schedule since the tick
stopped. The board reports that honestly rather than smoothing it, and a hand
run or a click on Run is what moves a row back to fresh until the in-process
scheduler lands.

**When a task has gone red.** Open the Pipelines tab, sort by stale, expand
the row. The expandable carries the last run's note and the path to its
captured log; the log is the full stdout and stderr of every step. Re-running
from the row is the same seam the scheduler uses, so a manual run and a
scheduled run leave identical ledger rows.

<!-- BEGIN GENERATED: scripts/pipelines_runbook.py -->

| Task | Family | Schedule | Writes | Stale after | If it stops |
|---|---|---|---|---|---|
| `presser_projection_refresh` | core | T-30h before each deadline | fact_player_state, fact_fixture, content_item, the three fixture parquets, fact_projection | 20h 0m | the pre-deadline injury digest and the projections behind it are a day old |
| `price_radar` | core | daily 02:00 Europe/London | dag_observation rows and one alert | 8h 0m | price rises and falls land without warning |
| `final_solve_delivery` | core | T-4h before each deadline | nothing; it reads the newest plan artefact | 3h 0m | no plan is delivered before the deadline |
| `lineup_captain_check` | core | T-1.5h before each deadline | fact_lineup, and one alert | 1h 15m | a benched captain is not caught |
| `odds_refresh` | odds | deadline ladder T-36h/T-12h/T-5h | fact_odds and the derived market tables | 6h 0m | the odds strip ages and the derived markets with it |
| `post_gw_settlement` | settlement | daily 10:30 UTC | fact_player_fixture, fact_projection, projection_weight, the fixture parquets, fact_odds, content tables, the rivals tables, the retro and weekly reports | 23h 0m | no actuals, so projection weights, creator scores and the cohort crawls all freeze |
| `fpl_core_insights` | results | daily 11:30 UTC | fact_match_xg | 12h 0m | per-match xG stops at the last settled gameweek |
| `panel_picks_crawl` | settlement | daily 11:15 UTC | dim_manager, fact_manager_season, fact_manager_gw, fact_manager_pick, fact_manager_transfer, fact_manager_chip | 23h 0m | the Creators board shows last week's squads |
| `content_transcribe` | content | daily 12:00 UTC | content_transcript and transcript_provenance | 6h 0m | new episodes hold audio and no text |
| `content_analyse` | content | daily 13:30 UTC | content_analysis and content_claim | 23h 0m | stored text produces no claims, so the Creators tab ages while the feed keeps filling |
| `content_analyse_backlog` | content | daily 01:30 UTC | content_analysis and content_claim | 23h 0m | the never-analysed backlog stops draining |
| `content_fast_rss` | content | every 4h | content_source, content_item, content_transcript | 3h 0m | creator feeds go unread for the day |
| `forecast_refresh` | core | daily 11:30 UTC | forecast.parquet and its sidecar | 23h 0m | forecast.parquet covers a horizon the solver has already passed and the solve refuses to score it |
| `auto_resolve` | core | T+26h after each deadline | transfer_plan.json | 20h 0m | the dashboard opens onto a plan solved for a gameweek that has already been played, so every verdict row reads stale until somebody presses Re-solve |
| `fixture_ratings_refit` | core | daily 11:00 UTC | fixture_ratings.parquet, fixture_difficulty.parquet, fixture_calibration.parquet | 23h 0m | the Fixtures board colours an older fit |
| `briefing_intel` | core | daily 07:40 Europe/London | briefing_intel.json | 23h 0m | the dashboard loses its salience pass |
| `audio_retention` | maintenance | weekly | nothing; it deletes swept audio files | 24h 0m | the ASR audio cache grows without a sweep |

### Running one by hand

Every task runs through the same seam the scheduler and the Pipelines tab use, so a hand run leaves the same ledger row and the same log file:

```
uv run python -c "from fpl_edge.pipelines import runner; o = runner.run_task('<task id>'); print(o.result.outcome, o.log_path)"
```

The commands each task runs, in order, for running one step on its own:

**`presser_projection_refresh`**

```
uv run python scripts/ingest_live.py --db <db_path>
uv run python scripts/ingest_odds.py --fixtures --db <db_path>
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> ingest --backfill-days 2
uv run python -m fpl_edge.platform.scripts.fixtures --build --season <season> --db <db_path>
uv run python -m fpl_edge.ingest.projections.cli ingest --season <season> --first-gw <gw> --last-gw <gw plus 5>
# in process: fpl_edge.interfaces.watchlist.digest_lines
```

**`price_radar`**

```
# in process: fpl_edge.myteam.store.MyTeamStore
# in process: fpl_edge.config.owner_entry_id
```

**`final_solve_delivery`**

```
# no subprocess: the task reads the warehouse in process
```

**`lineup_captain_check`**

```
uv run python -m fpl_edge.ingest.lineups --season <season> --db <db_path>
```

**`odds_refresh`**

```
uv run python scripts/ingest_odds.py --odds-api --season <season> --db <db_path>
uv run python scripts/ingest_odds_extras.py --season <season>
# in process: fpl_edge.ingest.odds.freshness_summary
# in process: fpl_edge.ingest.odds.odds_freshness
```

**`post_gw_settlement`**

```
uv run python scripts/ingest_live.py --db <db_path>
uv run python scripts/ingest_odds.py --fixtures --db <db_path>
uv run python -m fpl_edge.ingest.results --db <db_path>
uv run python -m fpl_edge.ingest.projections.cli ingest --db <db_path> --first-gw <gw> --last-gw <gw plus 5>
uv run python -m fpl_edge.eval.projection_scoring --db <db_path>
uv run python -m fpl_edge.platform.scripts.fixtures --build --db <db_path>
uv run python scripts/ingest_odds.py --odds-api --max-age-hours 48 --db <db_path>
uv run python -m fpl_edge.cli.main idea track --db <db_path>
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> score
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> ingest --backfill-days 3
uv run python -m fpl_edge.ingest.rivals.crawl --budget 1100 --db <db_path>
uv run python -m fpl_edge.ingest.rivals.elite --budget 200 --db <db_path>
uv run python -m fpl_edge.ingest.rivals.top1k --grow 300 --budget 1200 --transfers-top 300 --db <db_path>
uv run python -m fpl_edge.intel.cli collect --db <db_path>
uv run python scripts/retro_report.py --db <db_path>
uv run python scripts/weekly_idea_report.py --db <db_path>
```

**`fpl_core_insights`**

```
uv run python -m fpl_edge.ingest.fpl_core_insights --db <db_path> --season <season>
```

**`panel_picks_crawl`**

```
uv run python -m fpl_edge.ingest.rivals.panel_picks --db <db_path>
```

**`content_transcribe`**

```
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> transcribe --budget-s <budget>
# in process: fpl_edge.ingest.content.asr.backend_status
```

**`content_analyse`**

```
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> analyze --since <since_days> --budget-s <budget> --token-budget <tokens> --summary-json <summary_path>
```

**`content_analyse_backlog`**

```
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> analyze --since <since_days> --budget-s <budget> --token-budget <tokens> --summary-json <summary_path>
```

**`content_fast_rss`**

```
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> ingest --backfill-days 1 --only <keys>
uv run python -m fpl_edge.ingest.content.pipeline --db <db_path> transcribe --kinds youtube --since 2 --budget-s 300
# in process: fpl_edge.ingest.content.sources.fast_tier
```

**`forecast_refresh`**

```
uv run python -m fpl_edge.cli.main solve --db <db_path> --forecast-only --forecast-source consensus --horizon 5
```

**`auto_resolve`**

```
uv run python -m fpl_edge.cli.main recommend --db <db_path> --season <season> --horizon 5 --max-hits 0 --no-chips --commit
```

**`fixture_ratings_refit`**

```
uv run python -m fpl_edge.platform.scripts.fixtures --build --db <db_path>
```

**`briefing_intel`**

```
uv run python -m fpl_edge.platform.briefing_intel --db <db_path> --season <season> --now <isoformat>
```

**`audio_retention`**

```
# in process: fpl_edge.ingest.content.asr.sweep_audio_cache
```

### Deliberate overlaps

One module on two scheduled paths is double work until the code says why. `tests/unit/test_no_double_work.py` walks every path, extracts every target, and fails on an overlap that is not listed here; each row cites the comment that documents it, and the citation is checked against the file.

| Target | Runs on | Why | Documented at |
|---|---|---|---|
| `fpl_edge.ingest.projections.cli ingest` | `post_gw_settlement`, `presser_projection_refresh` | Providers publish on their own clocks, so the nightly pull bounds every feed at a day old and the T-30h pull catches what moved before the deadline. Section 6 of ARCHITECTURE_REVIEW.md keeps the nightly one: it is a documented fix, not double work. | `fpl_edge/jobs/post_gw.py:264`, `fpl_edge/pipelines/tasks.py:74` |
| `fpl_edge.platform.scripts.fixtures --build` | `fixture_ratings_refit`, `post_gw_settlement`, `presser_projection_refresh` | One writer, three callers. Refactor group 6 merged models/team_goals/ratings_cache.py into fixtures/build.py, so the two fits of one model became one fit writing all three artefacts. The 11:00 UTC refit is the daily one, settlement rebuilds after results land, and T-30h rebuilds after midweek rescheduling. | `fpl_edge/jobs/post_gw.py:282`, `fpl_edge/pipelines/tasks.py:86`, `fpl_edge/pipelines/registry.py:800` |
| `fpl_edge.ingest.content.pipeline ingest` | `content_fast_rss`, `post_gw_settlement`, `presser_projection_refresh` | The content tiers are a decision: 13 creator feeds every four hours, the other 27 sources once a night over a wider window, and a two-day catch-up before each deadline. | `fpl_edge/ingest/content/sources.py:267`, `fpl_edge/pipelines/tasks.py:74` |
| `fpl_edge.ingest.content.pipeline transcribe` | `content_fast_rss`, `content_transcribe` | Captions and audio are different costs. The 4-hourly rung takes captions only and never downloads audio; the nightly task runs the GPU under a wall-clock budget. | `fpl_edge/pipelines/registry.py:876` |
| `fpl_edge.ingest.content.pipeline analyze` | `content_analyse`, `content_analyse_backlog` | Same budgeted, resumable pass over two different queues: the daily one covers the last 21 days, the overnight one drops the window and eats the never-analysed backlog. | `fpl_edge/pipelines/registry.py:660` |
| `scripts/ingest_odds.py --odds-api` | `odds_refresh`, `post_gw_settlement` | The nightly top-up is a no-op whenever the deadline ladder has already priced the week. --max-age-hours 48 is what makes it one, and it reports the skip rather than a fake ok. | `fpl_edge/jobs/post_gw.py:303` |
| `scripts/ingest_live.py` | `post_gw_settlement`, `presser_projection_refresh` | The bootstrap snapshot before a deadline is the point of the T-30h task: prices, injuries and news move after the nightly run. | `fpl_edge/pipelines/tasks.py:74` |
| `scripts/ingest_odds.py --fixtures` | `post_gw_settlement`, `presser_projection_refresh` | Forward fixtures are free and reschedules land midweek, so the T-30h task refetches them alongside the rest of the chain's head. | `fpl_edge/pipelines/tasks.py:74` |

<!-- END GENERATED -->

---

## 1 · What exists: 29 fetch surfaces, 6 groups

The full file:line inventory lives in the session record; this is the
operating map. **Bold** = scheduled today; *italics* = manual-only today.

### A · Core FPL loop
| Pipeline | Source | Schedule today | Writes |
|---|---|---|---|
| **bootstrap + fixtures** | fantasy.premierleague.com API | nightly post_gw + DAG T-30h | dim_event/team/player, fact_player_state, fact_fixture |
| **results settlement** | FPL live API | nightly post_gw (refuses un-finalised GWs) | fact_player_fixture |
| my-team (public/private) | FPL API (+OAuth for private) | manual / on-demand | data/myteam/*.json (not warehouse) |
| **fixture ratings refit** (Dixon-Coles club split) | local warehouse only | **daily 11:00 UTC** (`fixture_ratings_refit`, ~2.5s) | fixture_ratings.parquet, fixture_calibration.parquet. Was hand-run only and went 227h stale while the nightly job refreshed the deprecated blend |
| **points forecast refresh** (model fit -> forecast.parquet) | local warehouse only | **daily 11:30 UTC** (`forecast_refresh`, points objective, 30s MILP cap) | forecast.parquet for the next open horizon. Only `fpl solve` writes it and nothing rolled it forward: on 2026-09-07 it covered GW3-7 while the squad-anchored solver wanted GW4-8 and refused to score 32 unprojected players. Its first scheduled run failed because `--forecast-only` did not exist yet and the task ran the whole MILP; the flag landed the same day and the task has been green since |

### B · Odds (metered: 500 credits/month)
| Pipeline | Schedule today | Freshness gate |
|---|---|---|
| **Odds API featured** (h2h/totals/CS/scorer) | DAG ladder **T-36h / T-12h / T-5h** + nightly top-up | `MARKET_MAX_AGE_H` registry + skip-if-all-fresh; zero rows never counts as fresh |
| **Odds API extras** (CS-correct/BTTS/team totals) | T-36h rung only, ~42 credits/GW | own monthly cap 150, local spend ledger |
| **football-data forward fixtures** | post_gw + DAG T-30h | none (free) |
| *football-data history* | manual backfill | as_of = kickoff |

### C · Projections (6 providers, one CLI, nightly)
| Provider | Serves | Measured cadence (2026-27) |
|---|---|---|
| fplform | xp, p_appear, **xp_if_appears** | revises ~daily (values change most days) |
| fpl_ep | FPL's own ep_next | static for days at a time |
| gh_fplbench | xp + **xmins on every row** | repo pushes, roughly per-GW |
| gh_blueladd | RETIRED 2026-09-08 | not fetched; lost to its own baseline |
| gh_apex_airsenal | xp | static 3-4 days between pushes |
| premierinjuries | **p_appear only** (never xp/xmins) | site updates |
| livefpl (same CLI) | EO predicted/top10k/elite | daily |

We pull all of them once daily (post_gw). **Measured: most pulls re-store
value-identical rows**. fplform is 60k rows over 13 pulls and it is the
*most* volatile provider; fpl_ep/airsenal re-store unchanged numbers for days.

### D · Content (creators)
| Pipeline | Schedule today | Gate |
|---|---|---|
| **RSS/blog/YouTube-page ingest** (22 podcasts, 3 blogs, 14 channels) | nightly post_gw (backfill 3d) + DAG T-30h (2d) | GUID-keyed content-addressed item_id; description-only until transcribed |
| **transcription** (MLX-Whisper ASR 11.5×; panel captions 286×) | **daily 12:00 UTC** (`content_transcribe`, 1h budget) | queue skips done/skipped items; 80% coverage or nothing stored; a failed run records `error`, never `ok` |
| **analysis** (Opus reads stored text → claims/insights) | **daily 13:30 UTC** (`content_analyse`, last 21d, 30m budget) + **daily 01:30 UTC** (`content_analyse_backlog`, no window) | per-item, resumable across runs. Was manual-only until 2026-09-04: discovery ran every 4h while 651 items sat unanalysed |
| paste-a-link | UI, preview-gated (park → accept/decline, 30-min TTL) | decline stores nothing |

**Stale windows.** A task whose value does not decay within the day keeps a 23h window, so a tick that slept through the due instant runs late instead of skipping the day (the Mac lid was down through 2026-08-31 and 2026-09-06; both days' settlement and analysis were `skipped_stale`). Time-sensitive tasks (prices, odds, deadline-relative rungs) keep tight windows.

### E · Cohort crawls (elite field)
**elite snowball** (budget 1100), **named elite** (identity-verified),
**top10k sampler** (grow +300/night), all nightly post_gw, all budgeted with
per-endpoint HTTP TTL caches (finished-GW picks cached ~forever; transfers 3h).

### F · On-demand / manual
Understat player profile (UI click), Pulselive confirmed lineups (DAG T-90m),
intel (offline replay of archived bootstraps, nightly), *FPL-Core-Insights
per-match xG (manual, worth scheduling)*, *vaastav history (one-off)*.

### The scheduler that already exists
`deadline_dag` is launchd-ticked every 600s and is **already the Argus
scheduler transposed**: event-relative due times computed from `dim_event`
deadlines (T-30h presser, T-4h solve, T-90m lineups, the odds ladder),
per-task stale windows so a slept-through rung is dropped rather than
double-fired, an idempotent `dag_firing` ledger claimed before running, and
honest outcome rows (`delivered/quiet/skipped_stale/no_source/error`).
post_gw is the calendar-daily settlement chain. The design question is not
"build a scheduler", it is "promote the one we have to own everything".

**How an outcome becomes a health state (2026-09-08).** The board used to
count only `error` toward the failure streak, so a run that correctly
refused for lack of a source came back green with the sentence "last run
succeeded inside its cadence", which was false. `no_source` and `refused`
now map to their own health state with their own dot and plain words;
`skipped_fresh` stays healthy but says the fetch was skipped because the
data was already fresh, rather than claiming a success. No ledger enum
reaches the reader. The health reason is the first line of the run's own
detail, taken before the appended log tail, so a Python traceback can no
longer be sliced mid-path into the one string the row exists to carry;
the full tail stays one click away in the drawer.

---

## 2 · The owner's questions, answered with facts

**xPoints: how often do providers publish?** Measured above (§1C). Daily
pulls are right for fplform; wasteful-but-harmless for the static ones. The
real waste is *storage semantics*, not fetch count; see change detection.

**xMins: do we fetch it?** Yes: `fact_projection.xmins` from gh_fplbench
(every row) and gh_blueladd (partial); `xp_if_appears` from fplform;
`p_appear` from fplform + premierinjuries; rotowire predicted XI kept
separate as the journalist-proxy (`fact_predicted_lineup`). The migration
that added the column forbids squashing p_appear↔xmins into each other,
deriving one from the other is a minutes model, which we refuse to fake.

**Transcription "on disk"?** Two-tier: panel-creator YouTube captions at
~286× realtime (cheap), MLX-Whisper on-GPU ASR at ~11.5× for podcast audio.
Audio downloads are content-addressed to `data/raw/content/asr_audio/` so a
re-run never re-downloads, which is the "disk" part, and it is correct.
Two real problems: **transcription is manual-only** (nothing schedules it),
and **the audio cache has no cleanup** (grows forever; episodes are
20-400MB).

**Injuries: source, store, surface?** Three paths, deliberately ranked:
1. **FPL API bootstrap** → `fact_player_state` (status, chance_of_playing,
   news, `news_added`). Chosen as PRIMARY because `news_added` is an honest
   published-at, exactly what a PIT store needs. 119k rows.
2. `intel_item`, the same facts replayed as dated AVAILABILITY items
   (content-hashed, so 300 polls of one injury = one row).
3. premierinjuries.com → `fact_projection.p_appear`.
Surfaced today: fixtures drawer team news, DAG T-30h digest, player_intel
tool. Gap: no single "availability" panel that merges the three with
provenance.

**RSS first-pass gate for creators?** The structure already exists: RSS
ingest is cheap and stores description-only items; transcription is the
expensive step and is separate. What is missing is (a) a faster RSS cadence
for the top creators than nightly, and (b) a *relevance gate* between
description and transcription so GPU/LLM spend follows worth.

**Manual link: don't fetch if not relevant?** Built: the paste-a-link job
parks at a preview (publisher, title, GW, description, duration, ETA) and
transcribes only on explicit accept; decline/expiry stores nothing.

**Duplicates?** The content store already survived this war: item_id =
sha256(source|GUID) after link-keying collapsed 378 episodes into one row;
claims are content-addressed; intel hashes content so re-observation is a
no-op; every warehouse append is a PIT anti-join that refuses contradictions.
The dedup religion is sound. The one soft spot is *value-identical re-writes
under new as_of* (next section).

---

## 3 · Defects found during inventory (fix regardless of design)

1. ~~`make deploy` copies the deleted telegram plist~~, fixed (fe8ad79).
2. **`fpl_mcp` is a second, worse fetch surface**: bare `requests`, no
   archive, no budget, overwrites plain-JSON caches inside the hash-named
   archive dir, and worst, `video_transcript.py` uses the youtubei route
   `fpl_edge` explicitly refuses as robots-disallowed, with no robots check
   and silent-empty failures. Must be unified onto the fpl_edge fetchers.
3. **Audio cache unbounded**, needs a post-transcription retention rule.
4. **paste-a-link writes `transcript_segment` without a
   `transcript_provenance` row**, the one transcript path with no receipt.
5. Stale prose: T-3h appears in two comments; the ladder is T-5h.
6. Manual-only pipelines that should be scheduled: transcription,
   FPL-Core-Insights (per-match xG for the fixtures form window!).

---

## 4 · Proposed architecture (for discussion)

### 4.1 One scheduler, one registry
Promote `deadline_dag` to THE scheduler. Every pipeline becomes one row in an
explicit registry (Argus rule: adding authority = one reviewable line):

```python
Task(id="ingest_projections",
     due=Daily(hour_utc=10) | DeadlineRelative(hours=-30) | OnDemand,
     stale_window=..., budget=..., freshness=skip_if_fresh_fn,
     run=..., enabled=True)
```

post_gw's steps fold in as calendar tasks; the ladder tasks stay
event-relative; today's manual-only pipelines get schedules (transcription
nightly with a budget; fpl_core_insights daily post-kickoff). One launchd
tick drives everything; launchd's only job is "wake the scheduler".

### 4.2 The fetch ledger + change detection (the "already latest" mechanism)
New table `fetch_run(pipeline, source, started, finished, status,
rows_written, rows_unchanged, http_status, credits_spent, note)` written by
every pipeline through one helper. Then the append path gains
**write-if-changed**: compare incoming rows to each entity's latest stored
values; write only rows whose payload differs; count the rest as
`rows_unchanged` in the ledger.

What this buys, in the owner's words:
- "don't rerun the whole thing if already latest": the freshness gate reads
  the ledger: last successful run + rows_unchanged says *confirmed current*.
- "should not restore the same thing": value-identical pulls stop writing
  fact rows at all.
- The PIT ambiguity dies: today "no new as_of" cannot distinguish *not
  refetched* from *refetched, unchanged*. Ledger + unchanged-count makes
  "confirmed unchanged at T" a first-class fact without bloating fact tables.
- MCP stability: fact-table schemas stop churning under storage-size
  pressure, because the pressure is gone.

### 4.3 UI: a Pipelines panel + safe triggering
One panel listing every registered task: last run, outcome, rows
written/unchanged, next due, freshness state (unifying today's four disjoint
staleness registries into one module the panel, the DAG gates, and the
drawers all read). Trigger buttons POST `/api/pipelines/{id}/run` (the
fetch_profile route is the precedent), with metered pipelines either
excluded or confirm-gated (open question).

### 4.4 Content cadence + the relevance gate
- Top-creator RSS polled on its own faster schedule (open question: cadence);
  full-roster nightly as today. RSS polls are conditional-fetch cheap.
- New scheduled `transcribe` task with a nightly wall-clock budget:
  captions-first for panel creators (286×), ASR queue for podcasts ordered
  by a **relevance gate**: score the stored description (players named ×
  resolver hits, gameweek terms, panel status, recency) and transcribe above
  threshold; below-threshold items stay description-only with the score
  recorded (a named reason, not silence). No LLM in the gate at first: the
  scorer is deterministic and auditable; an LLM pass is a v2 refinement.
- Audio retention: delete audio after a stored transcript+provenance (the
  sha stays in provenance, so integrity survives the file).

### 4.5 What deliberately does NOT change
`as_of` PIT discipline, append-only contradiction refusal, content-addressed
ids, robots discipline (extended TO fpl_mcp, never relaxed), credit budgets
with refusal-before-spend, honest outcome rows.

---

## 5 · Decisions (closed with owner, 2026-08-31)

1. **One scheduler.** deadline_dag becomes THE scheduler with an explicit
   task registry: calendar tasks (post_gw's steps fold in), deadline-relative
   tasks (the ladders stay), on-demand tasks. The postgw plist retires once
   parity is proven; launchd's only job is waking the tick.
2. **Fetch ledger + write-on-change.** `fetch_run` records every pull;
   appends write only changed rows; "refetched, unchanged" becomes a
   first-class fact and the skip gate reads the ledger.
3. **4h top-creator RSS + nightly budgeted ASR.** Panel captions
   auto-transcribe on arrival; podcast ASR under a nightly wall-clock budget,
   queue ordered by a deterministic relevance score on the description;
   below-threshold stays description-only with the score recorded.
4. **All pipelines UI-triggerable; metered ones confirm-gated** with credit
   cost and month-to-date spend shown before the click.

## 6 · Build order (each step shippable)

1. **fetch_run ledger + write-on-change** in the three append paths
   (Warehouse.append, ProjectionStore, UnderstatStore) behind one helper.
   The PIT contract note: an entity's absent new as_of now means "no change
   observed OR not fetched", and the ledger is the disambiguator: every
   consumer that cares reads fetch_run. Tests must pin: unchanged rows not
   written but counted; changed rows written; the skip gate honours
   ledger+freshness; contradiction refusal unchanged.
2. **Scheduler registry.** Task dataclass + registry module; post_gw steps
   become calendar tasks executed by the DAG tick with the same firing
   ledger; parity run (both paths side by side, outcomes compared) before
   the postgw plist is retired.
3. **Schedule the missing**: transcribe (nightly budget, captions-first,
   relevance gate), fpl_core_insights (daily post-kickoff), top-creator RSS
   at 4h (a `content_tier` on sources), audio retention sweep (delete after
   stored transcript+provenance; sha survives in provenance).
4. **Pipelines panel + trigger routes** + the unified freshness module
   replacing the four disjoint registries; metered confirm flow shows
   credits and month spend.
5. **fpl_mcp fetch unification**: kill the youtubei route and bare requests;
   route through fpl_edge fetchers/archive.
6. **Small repairs**: paste-a-link writes transcript_provenance; T-3h prose
   corrected to T-5h.
