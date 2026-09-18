# Architecture map: `fpl_edge/`

Generated 2026-09-17 by the script in Appendix A, run over the working tree at
`main`. Every count in this document comes from that script (Python `ast` over
all 250 `.py` files under `fpl_edge/`, plus a literal-string scan of the rest of
the repo and of `~/Library/LaunchAgents/com.fpledge.*.plist`). Nothing here is
an estimate.

## Orientation

`fpl_edge` is a single-operator Fantasy Premier League decision engine. Data
flows one way. `ingest/` pulls the FPL API, bookmaker odds, third-party
projection feeds, rival managers' teams and creator content into `store/`, a
single append-only DuckDB warehouse with point-in-time reads. `models/`, `sim/`,
`opt/` and `rank/` read the warehouse through `Snapshot` seams and produce
forecasts, simulated rank distributions and a multi-gameweek transfer plan.
`platform/scripts/` holds the panel scripts, which are the only path from the
warehouse to a screen: each one is registered in `platform/registry.py`,
validated against a JSON Schema in both directions, pinned to a panel in
`platform/panels.py`, and served over HTTP by `platform/app.py` to the
JavaScript in `web/`. Writes are never done by panels; they are done by tasks
declared in `pipelines/registry.py` and by `jobs/deadline_dag.py`, which a
launchd tick drives every 600 seconds. `interfaces/`, `myteam/`, `cli/`,
`intel/` and `theses/` are the operator-facing surfaces that sit across the
same seams.

Two structural facts to know before reading the tables. `store` is imported by
85 modules and imports nothing inside `fpl_edge`, which is the shape a warehouse
layer should have. `platform` and `pipelines` import each other, and that
mutual import is the largest cycle in the tree (Table 5, C1).

Two directories are Python packages without an `__init__.py` and therefore
resolve as namespace packages: `fpl_edge/oracle/` and `fpl_edge/models/points/`.
Neither is an intentional namespace package. Adding the two files is a
one-line-each fix and is not counted as a verdict below.

## Table 1: packages

Twenty-nine packages. "files" counts `.py` files directly in the package
directory, not in sub-packages, so the numbers sum to 250.

| package | responsibility | files | lines | imported by (packages) | imports (packages) |
| --- | --- | ---: | ---: | --- | --- |
| `fpl_edge` | Package root: user configuration and the core identity and value types every other package speaks in. | 3 | 318 | cli, eval, ingest, ingest.content, ingest.rivals, intel, interfaces, jobs, models, models.copying, models.minutes, models.ownership, models.points, models.team_goals, myteam, opt, platform.scripts, rank, sim, theses | models |
| `fpl_edge.cli` | The `fpl` Typer command line and its two heaviest subcommands, recommend and solve. | 4 | 1593 | none | root, intel, interfaces, models.copying, models.minutes, models.points, models.team_goals, myteam, opt, platform, rank, sim, store, theses |
| `fpl_edge.eval` | Scoring the engine against settled reality: baselines it must beat, calibration, season replay and provider scoring. | 7 | 1877 | models.ensemble, myteam, platform.scripts | root, ingest.content, ingest.projections, rules, store |
| `fpl_edge.ingest` | Pulling outside sources into the point-in-time warehouse: the FPL API, bookmaker odds, lineups, history. | 12 | 6406 | ingest.content, ingest.projections, ingest.rivals, interfaces, jobs, models.ensemble, models.ownership, myteam, platform, platform.scripts | root, ingest.projections, ingest.rivals, models.team_goals, store |
| `fpl_edge.ingest.content` | Creator content end to end: fetch, transcribe, extract claims, resolve players, dedupe, score track record, persist. | 19 | 9940 | eval, interfaces, pipelines, platform, platform.scripts | root, ingest, ingest.rivals, interfaces, platform.scripts, rules, store |
| `fpl_edge.ingest.projections` | Third-party projection and ownership feeds, one adapter per provider behind a shared robots and store layer. | 11 | 3976 | eval, ingest | ingest, store |
| `fpl_edge.ingest.rivals` | Crawling other managers' teams under a declared budget, and the curated cohorts worth crawling. | 12 | 4381 | ingest, ingest.content, interfaces, models.copying, models.field | root, ingest, store |
| `fpl_edge.intel` | News and tactical intel with the timestamp at which each fact became public, so nothing leaks backwards. | 11 | 2899 | cli, interfaces | root, models.points, store |
| `fpl_edge.interfaces` | User-facing surfaces: the idea inbox, the Telegram bot, the weekly report, the player dossier and the question router. | 19 | 9115 | cli, ingest.content, jobs, myteam, platform, platform.scripts, theses | root, ingest, ingest.content, ingest.rivals, intel, models, models.minutes, models.ownership, models.points, models.team_goals, myteam, rules, store |
| `fpl_edge.jobs` | Scheduled background work: the deadline DAG, the post-gameweek settlement job and the delivery outbox. | 4 | 2056 | pipelines, platform | root, ingest, interfaces, myteam, pipelines, store |
| `fpl_edge.models` | The interfaces every model implements, and nothing else. | 2 | 205 | root, interfaces, models.minutes, models.ownership, models.points, models.team_goals, oracle, sim | root, store |
| `fpl_edge.models.copying` | Separating manager skill from luck, and measuring whether copying a skilled manager actually paid. | 8 | 2283 | cli | root, ingest.rivals, store |
| `fpl_edge.models.ensemble` | Projection-ensemble research, declared by its own docstring to sit outside the production import closure. | 5 | 959 | platform.scripts | eval, ingest, models.minutes, models.points, models.team_goals, store |
| `fpl_edge.models.field` | The joint distribution of rival squads per cohort, with provenance attached to every sample. | 7 | 1908 | none | ingest.rivals, models.ownership, sim, store |
| `fpl_edge.models.minutes` | The three-way distribution over how long a player is on the pitch, plus the baselines it has to beat. | 10 | 2013 | cli, interfaces, models.ensemble, sim | root, models, store |
| `fpl_edge.models.ownership` | Forecasting what the field will own and captain, and the effective-ownership algebra on top. | 13 | 3237 | interfaces, models.field, sim | root, ingest, models, store |
| `fpl_edge.models.points` | Turning simulated match events into FPL points, including bonus and per-player scoring rates. | 4 | 662 | cli, intel, interfaces, models.ensemble, sim | root, models, rules, store |
| `fpl_edge.models.team_goals` | The Dixon-Coles team goal model, the market-implied baseline it competes with, and the walk-forward evaluation. | 14 | 3263 | cli, ingest, interfaces, models.ensemble, platform.scripts, sim | root, models, rules, store |
| `fpl_edge.myteam` | The owner's own squad: reconstructing it from public data, capturing it from the authenticated endpoint, and acting on it. | 14 | 5400 | cli, interfaces, jobs, platform | root, eval, ingest, interfaces, opt, rank, rules, store |
| `fpl_edge.opt` | Multi-gameweek squad optimisation as a MILP, with an independent recomputation of the declared objective. | 7 | 2728 | cli, myteam, rank | root, rules, store |
| `fpl_edge.oracle` | A thin evidence-signal layer that turns each data source into typed signals. | 2 | 395 | theses | models |
| `fpl_edge.pipelines` | The task registry, the single execution path every scheduled run goes through, and the derived health rules. | 4 | 1670 | jobs, platform, platform.scripts | ingest.content, jobs, platform, store |
| `fpl_edge.platform` | The HTTP surface, the panel-script registry, the guarded query path, the chat agent and the link-ingest job runner. | 13 | 6799 | cli, pipelines, platform.scripts | ingest, ingest.content, interfaces, jobs, myteam, pipelines, platform.scripts, store |
| `fpl_edge.platform.scripts` | The panel scripts, which are the only data path the UI has. | 15 | 15439 | ingest.content, platform | root, eval, ingest, ingest.content, interfaces, models.ensemble, models.team_goals, pipelines, platform, rules, store |
| `fpl_edge.rank` | The rank-aware decision layer: objective coefficients, closed-form policy, and a simulator-based validator. | 6 | 1743 | cli, myteam | root, opt, rules, sim |
| `fpl_edge.rules` | Verified access to the FPL rule registry, so no rule is hard-coded twice. | 2 | 141 | eval, ingest.content, interfaces, models.points, models.team_goals, myteam, opt, platform.scripts, rank, sim | none |
| `fpl_edge.sim` | The rest-of-season Monte Carlo engine, the rival field it samples, and the rank utility the optimiser maximises. | 10 | 3551 | cli, models.field, rank | root, models, models.minutes, models.ownership, models.points, models.team_goals, rules, store |
| `fpl_edge.store` | The DuckDB warehouse: point-in-time-correct reads, read copies, and the fetch ledger. | 3 | 940 | cli, eval, ingest, ingest.content, ingest.projections, ingest.rivals, intel, interfaces, jobs, models, models.copying, models.ensemble, models.field, models.minutes, models.ownership, models.points, models.team_goals, myteam, opt, pipelines, platform, platform.scripts, sim, theses | none |
| `fpl_edge.theses` | The versioned hypothesis registry: every belief becomes a file that gets graded. | 9 | 2289 | cli | root, interfaces, oracle, store |

## Table 2: module verdicts

One row per `.py` file under `fpl_edge/`, 250 rows. Columns:

- **importers**: modules inside `fpl_edge` that import it, counted from the AST
  (including imports inside function bodies).
- **tests**: distinct files under `tests/` that name it.
- **registry/panel/doc refs**: `registry` means `fpl_edge/pipelines/registry.py`
  names it; `panel` means `fpl_edge/platform/panels.py` names it; `docs` means a
  file under `docs/` names it; `-m` means some file invokes it as
  `python -m <dotted>`, which is how the settlement chain and the deadline DAG
  reach most ingest modules and is load-bearing despite zero Python importers.
- **verdict**: one of keep, rename to X, merge into X, split into X and Y,
  delete.

Verdict totals: 227 keep, 11 split, 7 merge, 2 rename, 3 delete.

| module | lines | importers | tests | registry/panel/doc refs | verdict | evidence |
| --- | ---: | ---: | ---: | --- | --- | --- |
| `fpl_edge/__init__.py` | 1 | 0 | 162 | registry, panel, docs, -m | keep | 0 importers, 162 test files, 302 other references. |
| `fpl_edge/cli/__init__.py` | 1 | 1 | 3 | registry, -m | keep | 1 importers, 3 test files, 7 other references. |
| `fpl_edge/cli/main.py` | 642 | 0 | 3 | registry, -m | keep | 0 importers, 3 test files, 9 other references. |
| `fpl_edge/cli/recommend.py` | 458 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/cli/solve.py` | 492 | 1 | 2 | none | keep | 1 importers, 2 test files, 0 other references. |
| `fpl_edge/config.py` | 149 | 19 | 4 | docs | keep | 19 importers, 4 test files, 6 other references. |
| `fpl_edge/eval/__init__.py` | 1 | 0 | 11 | -m | keep | 0 importers, 11 test files, 19 other references. |
| `fpl_edge/eval/baselines.py` | 206 | 0 | 3 | none | keep | 0 importers, 3 test files, 3 other references. |
| `fpl_edge/eval/calibration.py` | 152 | 2 | 1 | none | keep | 2 importers, 1 test files, 2 other references. |
| `fpl_edge/eval/creator_report_card.py` | 557 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/eval/projection_scoring.py` | 434 | 1 | 2 | docs, -m | keep | 1 importers, 2 test files, 8 other references. |
| `fpl_edge/eval/replay.py` | 306 | 3 | 3 | none | keep | 3 importers, 3 test files, 7 other references. |
| `fpl_edge/eval/scoring.py` | 221 | 4 | 5 | none | keep | 4 importers, 5 test files, 5 other references. |
| `fpl_edge/ingest/__init__.py` | 1 | 1 | 66 | registry, docs, -m | keep | 1 importers, 66 test files, 88 other references. |
| `fpl_edge/ingest/content/__init__.py` | 24 | 5 | 24 | registry, docs, -m | keep | 5 importers, 24 test files, 24 other references. |
| `fpl_edge/ingest/content/analyze.py` | 1190 | 2 | 4 | none | keep | 2 importers, 4 test files, 0 other references. |
| `fpl_edge/ingest/content/asr.py` | 938 | 4 | 0 | none | keep | 4 importers, 0 test files, 1 other references. |
| `fpl_edge/ingest/content/calendar.py` | 101 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/ingest/content/claims.py` | 394 | 5 | 5 | none | keep | 5 importers, 5 test files, 0 other references. |
| `fpl_edge/ingest/content/clubs.py` | 109 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/ingest/content/consensus.py` | 146 | 2 | 2 | none | keep | 2 importers, 2 test files, 2 other references. |
| `fpl_edge/ingest/content/feeds.py` | 394 | 4 | 2 | none | keep | 4 importers, 2 test files, 0 other references. |
| `fpl_edge/ingest/content/fetch.py` | 226 | 5 | 2 | none | keep | 5 importers, 2 test files, 1 other references. |
| `fpl_edge/ingest/content/loaders.py` | 595 | 2 | 2 | none | keep | 2 importers, 2 test files, 1 other references. |
| `fpl_edge/ingest/content/models.py` | 137 | 9 | 8 | none | keep | 9 importers, 8 test files, 0 other references. |
| `fpl_edge/ingest/content/panel.py` | 955 | 1 | 1 | docs | keep | 1 importers, 1 test files, 5 other references. |
| `fpl_edge/ingest/content/pipeline.py` | 1808 | 1 | 6 | registry, docs, -m | split into 4 modules, see Table 4 | 1808 lines, 11 argparse subcommands in one file. |
| `fpl_edge/ingest/content/resolve.py` | 278 | 3 | 2 | none | keep | 3 importers, 2 test files, 0 other references. |
| `fpl_edge/ingest/content/scoring.py` | 471 | 3 | 4 | docs | keep | 3 importers, 4 test files, 5 other references. |
| `fpl_edge/ingest/content/source_state.py` | 349 | 1 | 0 | docs | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/ingest/content/sources.py` | 463 | 9 | 9 | registry, docs | keep | 9 importers, 9 test files, 2 other references. |
| `fpl_edge/ingest/content/store.py` | 558 | 6 | 14 | docs | keep | 6 importers, 14 test files, 2 other references. |
| `fpl_edge/ingest/content/youtube.py` | 804 | 5 | 3 | docs | keep | 5 importers, 3 test files, 5 other references. |
| `fpl_edge/ingest/fpl_api.py` | 214 | 0 | 6 | docs | keep | 0 importers, 6 test files, 5 other references. |
| `fpl_edge/ingest/fpl_core_insights.py` | 357 | 0 | 0 | registry, docs, -m | keep | 0 importers, 0 tests; invoked as `-m` from `pipelines/registry.py:602`. No test is a real gap. |
| `fpl_edge/ingest/http.py` | 113 | 23 | 6 | docs | keep | 23 importers, 6 test files, 4 other references. |
| `fpl_edge/ingest/lineups.py` | 580 | 0 | 0 | docs | keep | 0 importers, 0 tests; invoked by `jobs/deadline_dag.py:971` through a dotted string, so it is load-bearing without a Python importer. No test is a real gap on a 580-line module. |
| `fpl_edge/ingest/odds.py` | 1966 | 7 | 6 | docs | split into 5 modules, see Table 4 | 1966 lines covering price maths, two vendors, name matching and freshness. |
| `fpl_edge/ingest/odds_derived.py` | 661 | 1 | 2 | docs, -m | keep | 1 importers, 2 test files, 4 other references. |
| `fpl_edge/ingest/odds_markets.py` | 328 | 0 | 3 | docs | keep | 0 importers, 3 test files, 3 other references. |
| `fpl_edge/ingest/player_mapping.py` | 540 | 6 | 4 | docs | keep | 6 importers, 4 test files, 3 other references. |
| `fpl_edge/ingest/projections/__init__.py` | 18 | 1 | 13 | docs, -m | keep | 1 importers, 13 test files, 13 other references. |
| `fpl_edge/ingest/projections/cli.py` | 515 | 0 | 0 | docs, -m | keep | 0 importers, 0 tests; invoked as `-m` from `deadline_dag.py:654` and `post_gw.py:211`. No test is a real gap. |
| `fpl_edge/ingest/projections/fpl_ep.py` | 120 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/ingest/projections/fplform.py` | 219 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/ingest/projections/github_csv.py` | 519 | 1 | 1 | docs | keep | 1 importers, 1 test files, 1 other references. |
| `fpl_edge/ingest/projections/livefpl.py` | 257 | 1 | 1 | docs | keep | 1 importers, 1 test files, 1 other references. |
| `fpl_edge/ingest/projections/premierinjuries.py` | 341 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/ingest/projections/providers.py` | 1060 | 2 | 1 | docs | keep | 2 importers, 1 test files, 1 other references. |
| `fpl_edge/ingest/projections/robots.py` | 236 | 8 | 0 | none | keep | 8 importers, 0 test files, 0 other references. |
| `fpl_edge/ingest/projections/rotowire.py` | 446 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/ingest/projections/store.py` | 245 | 3 | 11 | none | keep | 3 importers, 11 test files, 0 other references. |
| `fpl_edge/ingest/results.py` | 274 | 0 | 1 | -m | keep | 0 importers, 1 test files, 1 other references. |
| `fpl_edge/ingest/rivals/__init__.py` | 28 | 5 | 12 | registry, docs, -m | keep | 5 importers, 12 test files, 21 other references. |
| `fpl_edge/ingest/rivals/client.py` | 338 | 8 | 6 | none | keep | 8 importers, 6 test files, 1 other references. |
| `fpl_edge/ingest/rivals/crawl.py` | 481 | 3 | 1 | -m | keep | 3 importers, 1 test files, 3 other references. |
| `fpl_edge/ingest/rivals/elite.py` | 263 | 2 | 2 | docs, -m | merge into fpl_edge/ingest/rivals/crawl.py | Duplicate pair D4: identical `collect`/`run`/`main` triple over the shared `crawl` module, differing only in cohort and budget. |
| `fpl_edge/ingest/rivals/elite_list.py` | 1049 | 2 | 0 | none | split into fpl_edge/ingest/rivals/elite_list.py, data/reference/elite_1000.json | 1049 lines of which roughly 1000 are a pinned literal tuple. One function, `top(n)`. |
| `fpl_edge/ingest/rivals/history.py` | 162 | 3 | 1 | none | keep | 3 importers, 1 test files, 1 other references. |
| `fpl_edge/ingest/rivals/names.py` | 55 | 6 | 1 | docs | keep | 6 importers, 1 test files, 2 other references. |
| `fpl_edge/ingest/rivals/panel_picks.py` | 464 | 0 | 0 | registry, -m | merge into fpl_edge/ingest/rivals/crawl.py | Duplicate pair D4. Also 0 importers, 0 tests; reached only as `-m` from post_gw.py:288 and registry.py:634, which are themselves the duplicated `crawl_panel` / `panel_picks_crawl` tasks. |
| `fpl_edge/ingest/rivals/picks.py` | 256 | 4 | 2 | none | keep | 4 importers, 2 test files, 1 other references. |
| `fpl_edge/ingest/rivals/roster.py` | 547 | 2 | 2 | none | keep | 2 importers, 2 test files, 5 other references. |
| `fpl_edge/ingest/rivals/schema.py` | 242 | 3 | 5 | none | keep | 3 importers, 5 test files, 1 other references. |
| `fpl_edge/ingest/rivals/top1k.py` | 496 | 0 | 1 | -m | merge into fpl_edge/ingest/rivals/crawl.py | Duplicate pair D4. 0 importers; reached only as `-m` from post_gw.py:310. |
| `fpl_edge/ingest/understat.py` | 584 | 1 | 3 | docs | keep | 1 importers, 3 test files, 3 other references. |
| `fpl_edge/ingest/vaastav.py` | 788 | 0 | 3 | docs | keep | 0 importers, 3 test files, 4 other references. |
| `fpl_edge/intel/__init__.py` | 60 | 2 | 4 | -m | keep | 2 importers, 4 test files, 13 other references. |
| `fpl_edge/intel/availability.py` | 139 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/intel/bootstrap.py` | 298 | 6 | 0 | none | keep | 6 importers, 0 test files, 0 other references. |
| `fpl_edge/intel/cli.py` | 183 | 1 | 0 | -m | keep | 1 importers, 0 test files, 3 other references. |
| `fpl_edge/intel/collect.py` | 239 | 2 | 0 | -m | keep | 2 importers, 0 test files, 1 other references. |
| `fpl_edge/intel/formations.py` | 169 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/intel/items.py` | 306 | 11 | 2 | none | keep | 11 importers, 2 test files, 1 other references. |
| `fpl_edge/intel/oop.py` | 263 | 3 | 1 | none | keep | 3 importers, 1 test files, 1 other references. |
| `fpl_edge/intel/setpieces.py` | 404 | 2 | 1 | none | keep | 2 importers, 1 test files, 1 other references. |
| `fpl_edge/intel/sources.py` | 296 | 1 | 0 | none | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/intel/store.py` | 542 | 4 | 3 | none | keep | 4 importers, 3 test files, 1 other references. |
| `fpl_edge/interfaces/__init__.py` | 69 | 0 | 20 | docs | keep | 0 importers, 20 test files, 42 other references. |
| `fpl_edge/interfaces/bias.py` | 406 | 4 | 3 | none | keep | 4 importers, 3 test files, 2 other references. |
| `fpl_edge/interfaces/briefing.py` | 256 | 1 | 0 | docs | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/interfaces/creators.py` | 1378 | 4 | 3 | docs | keep | 4 importers, 3 test files, 7 other references. |
| `fpl_edge/interfaces/dossier.py` | 1674 | 2 | 0 | docs | split into 3 modules, see Table 4 | 1674 lines, 14 section builders plus loaders plus three output adapters. |
| `fpl_edge/interfaces/features.py` | 439 | 7 | 2 | none | keep | 7 importers, 2 test files, 0 other references. |
| `fpl_edge/interfaces/ideas.py` | 373 | 11 | 3 | docs | keep | 11 importers, 3 test files, 1 other references. |
| `fpl_edge/interfaces/inbox.py` | 533 | 3 | 6 | docs | keep | 3 importers, 6 test files, 3 other references. |
| `fpl_edge/interfaces/parsing.py` | 593 | 7 | 4 | none | keep | 7 importers, 4 test files, 0 other references. |
| `fpl_edge/interfaces/qa.py` | 654 | 4 | 4 | docs | keep | 4 importers, 4 test files, 4 other references. |
| `fpl_edge/interfaces/registry.py` | 494 | 8 | 2 | none | rename to fpl_edge/interfaces/idea_store.py | Third module named `registry.py` in the tree, alongside `pipelines/registry.py` (task registry) and `platform/registry.py` (panel-script registry). This one persists ideas and verdicts. |
| `fpl_edge/interfaces/render.py` | 182 | 1 | 0 | none | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/interfaces/report.py` | 226 | 5 | 2 | docs | keep | 5 importers, 2 test files, 5 other references. |
| `fpl_edge/interfaces/squad_section.py` | 184 | 1 | 1 | docs | keep | 1 importers, 1 test files, 1 other references. |
| `fpl_edge/interfaces/telegram.py` | 580 | 2 | 5 | none | keep | 2 importers, 5 test files, 3 other references. |
| `fpl_edge/interfaces/testing.py` | 339 | 0 | 7 | none | keep | 0 importers, 7 test files, 0 other references. |
| `fpl_edge/interfaces/tracking.py` | 200 | 3 | 3 | none | keep | 3 importers, 3 test files, 2 other references. |
| `fpl_edge/interfaces/verdict.py` | 399 | 2 | 1 | docs | keep | 2 importers, 1 test files, 1 other references. |
| `fpl_edge/interfaces/watchlist.py` | 136 | 1 | 1 | none | keep | 1 importers, 1 test files, 1 other references. |
| `fpl_edge/jobs/__init__.py` | 1 | 6 | 9 | registry, docs, -m | keep | 6 importers, 9 test files, 11 other references. |
| `fpl_edge/jobs/deadline_dag.py` | 1455 | 4 | 2 | registry, docs, -m | keep | 4 importers, 2 test files, 8 other references. |
| `fpl_edge/jobs/outbox.py` | 257 | 3 | 0 | none | keep | 3 importers, 0 test files, 0 other references. |
| `fpl_edge/jobs/post_gw.py` | 343 | 1 | 0 | docs | keep | 1 importers, 0 test files, 8 other references. |
| `fpl_edge/models/__init__.py` | 1 | 0 | 35 | docs, -m | keep | 0 importers, 35 test files, 78 other references. |
| `fpl_edge/models/contracts.py` | 204 | 19 | 9 | docs | keep | 19 importers, 9 test files, 5 other references. |
| `fpl_edge/models/copying/__init__.py` | 24 | 1 | 1 | none | keep | 1 importers, 1 test files, 5 other references. |
| `fpl_edge/models/copying/attribution.py` | 258 | 0 | 0 | none | keep | 0 importers, 0 tests, 0 registry/panel refs; referenced only by the `models/copying/__init__.py` docstring. Research branch, kept because the package is a coherent unit reached from `fpl copy`. |
| `fpl_edge/models/copying/effects.py` | 236 | 1 | 0 | none | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/models/copying/features.py` | 398 | 1 | 0 | none | keep | 1 importers, 0 test files, 2 other references. |
| `fpl_edge/models/copying/minileague.py` | 249 | 0 | 0 | none | keep | 0 importers, 0 tests, 0 registry/panel refs; referenced only by the `models/copying/__init__.py` docstring. Same research branch. |
| `fpl_edge/models/copying/report.py` | 284 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/models/copying/skill.py` | 597 | 1 | 0 | none | keep | 1 importers, 0 test files, 4 other references. |
| `fpl_edge/models/copying/template.py` | 237 | 0 | 0 | none | keep | 0 importers, 0 tests, 0 registry/panel refs; referenced only by the `models/copying/__init__.py` docstring. Same research branch. |
| `fpl_edge/models/ensemble/__init__.py` | 8 | 1 | 0 | none | keep | Package docstring declares the package research and outside the production closure. Kept as the marker; its two live consumers are `scripts/fixtures.py` and `ensemble/backtest.py`. |
| `fpl_edge/models/ensemble/backtest.py` | 212 | 0 | 0 | none | delete | 0 importers, 0 tests, 0 registry/panel refs, 0 doc refs. Its `walk_forward` duplicates `eval/projection_scoring.py`, which the settlement chain actually runs. |
| `fpl_edge/models/ensemble/frame.py` | 113 | 0 | 0 | none | delete | 0 importers, 0 tests, 0 registry/panel refs, 0 doc refs. |
| `fpl_edge/models/ensemble/sources.py` | 385 | 2 | 0 | docs | keep | 2 importers, 0 test files, 2 other references. |
| `fpl_edge/models/ensemble/weights.py` | 241 | 1 | 0 | none | delete | Only importer is `ensemble/backtest.py`, itself a delete. 0 tests, 0 registry/panel refs, 0 doc refs. |
| `fpl_edge/models/field/__init__.py` | 98 | 0 | 5 | none | keep | 0 importers, 5 test files, 8 other references. |
| `fpl_edge/models/field/cohorts.py` | 131 | 2 | 1 | docs | keep | 2 importers, 1 test files, 1 other references. |
| `fpl_edge/models/field/contracts.py` | 123 | 2 | 1 | none | keep | 2 importers, 1 test files, 1 other references. |
| `fpl_edge/models/field/drift.py` | 358 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/models/field/hybrid.py` | 402 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/models/field/observed.py` | 391 | 3 | 2 | docs | keep | 3 importers, 2 test files, 6 other references. |
| `fpl_edge/models/field/share.py` | 405 | 2 | 2 | none | merge into fpl_edge/models/ownership/eo.py | Duplicate pair D2: both define `effective_ownership` over the same algebra. `models/field` has no importer outside itself. |
| `fpl_edge/models/minutes/__init__.py` | 50 | 7 | 5 | docs, -m | keep | 7 importers, 5 test files, 8 other references. |
| `fpl_edge/models/minutes/base.py` | 91 | 4 | 0 | none | keep | 4 importers, 0 test files, 1 other references. |
| `fpl_edge/models/minutes/baselines.py` | 178 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/models/minutes/dataset.py` | 46 | 1 | 5 | none | keep | 1 importers, 5 test files, 0 other references. |
| `fpl_edge/models/minutes/evaluate.py` | 313 | 0 | 3 | docs, -m | keep | 0 importers, 3 test files, 3 other references. |
| `fpl_edge/models/minutes/features.py` | 540 | 5 | 5 | docs | keep | 5 importers, 5 test files, 1 other references. |
| `fpl_edge/models/minutes/gbm.py` | 176 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/models/minutes/hierarchical.py` | 396 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/models/minutes/measured.py` | 66 | 3 | 0 | docs | keep | 3 importers, 0 test files, 1 other references. |
| `fpl_edge/models/minutes/training.py` | 157 | 5 | 1 | none | keep | 5 importers, 1 test files, 0 other references. |
| `fpl_edge/models/ownership/__init__.py` | 59 | 4 | 5 | docs, -m | keep | 4 importers, 5 test files, 10 other references. |
| `fpl_edge/models/ownership/backtest.py` | 344 | 1 | 2 | none | merge into fpl_edge/models/ownership/evaluate.py | Duplicate pair D1. Both define `mae_pp`; `loso_inseason`/`loso_coldstart`/`evaluate_captaincy` pair one-to-one with `walk_forward_inseason`/`walk_forward_coldstart`/`walk_forward_captaincy`. Only importer is `ownership/fit.py`, which has 0 importers itself. |
| `fpl_edge/models/ownership/baselines.py` | 72 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/models/ownership/captaincy.py` | 330 | 3 | 2 | none | keep | 3 importers, 2 test files, 0 other references. |
| `fpl_edge/models/ownership/drift.py` | 494 | 4 | 3 | none | keep | 4 importers, 3 test files, 0 other references. |
| `fpl_edge/models/ownership/elite.py` | 280 | 2 | 3 | docs | keep | 2 importers, 3 test files, 3 other references. |
| `fpl_edge/models/ownership/eo.py` | 237 | 3 | 1 | none | keep | 3 importers, 1 test files, 1 other references. |
| `fpl_edge/models/ownership/evaluate.py` | 467 | 0 | 0 | docs, -m | keep | Absorbs `backtest.py` and `fit.py`. 0 tests today, which is the gap the merge should close. |
| `fpl_edge/models/ownership/field.py` | 129 | 3 | 1 | none | keep | 3 importers, 1 test files, 0 other references. |
| `fpl_edge/models/ownership/fit.py` | 110 | 0 | 0 | docs, -m | merge into fpl_edge/models/ownership/evaluate.py | 0 importers, 0 tests, 0 registry/panel refs; one doc ref in docs/models/ownership.md. 110 lines that only drive `backtest.py`. |
| `fpl_edge/models/ownership/model.py` | 437 | 4 | 4 | none | keep | 4 importers, 4 test files, 0 other references. |
| `fpl_edge/models/ownership/panel.py` | 171 | 2 | 1 | docs | keep | 2 importers, 1 test files, 1 other references. |
| `fpl_edge/models/ownership/simulate.py` | 107 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/models/points/bps.py` | 109 | 1 | 3 | none | keep | 1 importers, 3 test files, 0 other references. |
| `fpl_edge/models/points/model.py` | 258 | 5 | 0 | docs | keep | 5 importers, 0 test files, 5 other references. |
| `fpl_edge/models/points/scoring_map.py` | 120 | 1 | 2 | none | keep | 1 importers, 2 test files, 0 other references. |
| `fpl_edge/models/points/shares.py` | 175 | 6 | 0 | none | keep | 6 importers, 0 test files, 6 other references. |
| `fpl_edge/models/team_goals/__init__.py` | 71 | 6 | 11 | docs, -m | keep | 6 importers, 11 test files, 21 other references. |
| `fpl_edge/models/team_goals/base.py` | 135 | 5 | 2 | none | keep | 5 importers, 2 test files, 1 other references. |
| `fpl_edge/models/team_goals/baselines.py` | 265 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/models/team_goals/blend.py` | 93 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/models/team_goals/data.py` | 129 | 6 | 6 | docs | keep | 6 importers, 6 test files, 1 other references. |
| `fpl_edge/models/team_goals/dixon_coles.py` | 395 | 6 | 4 | docs | keep | 6 importers, 4 test files, 1 other references. |
| `fpl_edge/models/team_goals/evaluate.py` | 636 | 0 | 6 | docs, -m | keep | 0 importers, 6 test files, 1 other references. |
| `fpl_edge/models/team_goals/market.py` | 159 | 6 | 2 | none | keep | 6 importers, 2 test files, 1 other references. |
| `fpl_edge/models/team_goals/metrics.py` | 92 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/models/team_goals/odds.py` | 264 | 6 | 4 | docs | keep | 6 importers, 4 test files, 3 other references. |
| `fpl_edge/models/team_goals/promoted.py` | 296 | 2 | 4 | docs | keep | 2 importers, 4 test files, 1 other references. |
| `fpl_edge/models/team_goals/ratings_cache.py` | 207 | 0 | 1 | docs, -m | merge into fpl_edge/platform/scripts/fixtures.py | Duplicate pair D3: the `fixture_difficulty` step runs this to write the blended artefact, `fixture_ratings_refit` runs `scripts/fixtures --build` to write the split artefact the UI reads. Two writers, one model fit. |
| `fpl_edge/models/team_goals/scoreline.py` | 155 | 10 | 5 | none | keep | 10 importers, 5 test files, 2 other references. |
| `fpl_edge/models/team_goals/synthetic.py` | 366 | 1 | 6 | none | keep | 1 importers, 6 test files, 0 other references. |
| `fpl_edge/myteam/__init__.py` | 97 | 2 | 15 | -m | keep | 2 importers, 15 test files, 18 other references. |
| `fpl_edge/myteam/__main__.py` | 11 | 0 | 0 | none | keep | 0 importers, 0 tests, 0 registry/panel refs, 0 doc refs, but it is the `python -m fpl_edge.myteam` shim. Nothing in the repo invokes that path today, so it is a delete candidate on counts alone; 11 lines. |
| `fpl_edge/myteam/account.py` | 427 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/myteam/bot.py` | 259 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/myteam/cli.py` | 631 | 2 | 2 | none | keep | 2 importers, 2 test files, 0 other references. |
| `fpl_edge/myteam/forecast.py` | 192 | 7 | 2 | docs | keep | 7 importers, 2 test files, 2 other references. |
| `fpl_edge/myteam/manual.py` | 700 | 5 | 2 | none | keep | 5 importers, 2 test files, 1 other references. |
| `fpl_edge/myteam/private.py` | 262 | 6 | 2 | none | keep | 6 importers, 2 test files, 1 other references. |
| `fpl_edge/myteam/recommend.py` | 899 | 4 | 4 | docs | keep | 4 importers, 4 test files, 2 other references. |
| `fpl_edge/myteam/report.py` | 273 | 4 | 1 | none | keep | 4 importers, 1 test files, 1 other references. |
| `fpl_edge/myteam/sources.py` | 390 | 8 | 4 | none | keep | 8 importers, 4 test files, 0 other references. |
| `fpl_edge/myteam/state.py` | 754 | 8 | 8 | none | keep | 8 importers, 8 test files, 3 other references. |
| `fpl_edge/myteam/store.py` | 182 | 6 | 2 | none | keep | 6 importers, 2 test files, 0 other references. |
| `fpl_edge/myteam/tokens.py` | 323 | 5 | 2 | none | keep | 5 importers, 2 test files, 0 other references. |
| `fpl_edge/opt/__init__.py` | 104 | 6 | 14 | docs | keep | 6 importers, 14 test files, 13 other references. |
| `fpl_edge/opt/config.py` | 292 | 4 | 1 | none | keep | 4 importers, 1 test files, 2 other references. |
| `fpl_edge/opt/interfaces.py` | 185 | 8 | 1 | docs | keep | 8 importers, 1 test files, 2 other references. |
| `fpl_edge/opt/milp.py` | 1170 | 1 | 0 | none | keep | 1 importers, 0 test files, 2 other references. |
| `fpl_edge/opt/plan.py` | 111 | 3 | 1 | none | keep | 3 importers, 1 test files, 3 other references. |
| `fpl_edge/opt/problem.py` | 384 | 4 | 0 | docs | keep | 4 importers, 0 test files, 4 other references. |
| `fpl_edge/opt/scoring.py` | 482 | 2 | 1 | docs | keep | 2 importers, 1 test files, 4 other references. |
| `fpl_edge/oracle/adapters.py` | 135 | 0 | 0 | none | keep | 0 importers, 0 tests, 0 registry/panel refs, 0 doc refs inside fpl_edge; used by `scripts/oracle_gw1.py`. |
| `fpl_edge/oracle/signals.py` | 260 | 2 | 1 | docs | keep | 2 importers, 1 test files, 2 other references. |
| `fpl_edge/pipelines/__init__.py` | 39 | 5 | 5 | none | keep | 5 importers, 5 test files, 2 other references. |
| `fpl_edge/pipelines/health.py` | 450 | 3 | 0 | none | keep | 3 importers, 0 test files, 0 other references. |
| `fpl_edge/pipelines/registry.py` | 926 | 5 | 0 | none | keep | 5 importers, 0 test files, 3 other references. |
| `fpl_edge/pipelines/runner.py` | 255 | 5 | 0 | none | keep | 5 importers, 0 test files, 1 other references. |
| `fpl_edge/platform/__init__.py` | 19 | 3 | 27 | registry, panel, -m | keep | 3 importers, 27 test files, 25 other references. |
| `fpl_edge/platform/app.py` | 1508 | 1 | 8 | docs | split into 4 modules, see Table 4 | 1508 lines, of which `create_app` alone is 908. |
| `fpl_edge/platform/briefing_intel.py` | 897 | 2 | 1 | none | keep | 2 importers, 1 test files, 0 other references. |
| `fpl_edge/platform/chat_agent.py` | 925 | 1 | 2 | docs | keep | 1 importers, 2 test files, 2 other references. |
| `fpl_edge/platform/fpl_theme.py` | 170 | 0 | 0 | none | keep | 0 importers, 0 tests, 0 registry/panel refs, 0 doc refs by dotted name, but `fpl_mcp/tools/viz_tools.py:36` loads it by filesystem path and copies it into the chart sandbox. Load-bearing; the dotted-name scan cannot see it. |
| `fpl_edge/platform/inbox.py` | 131 | 1 | 0 | none | rename to fpl_edge/platform/outbox_view.py | It reads and acknowledges the delivery outbox, not an inbox, and the name collides with `interfaces/inbox.py`, which is the idea inbox. Different concept, same basename. |
| `fpl_edge/platform/link_jobs.py` | 1697 | 1 | 0 | none | split into 3 modules, see Table 4 | 1697 lines mixing preflight, take-building and a job runner. |
| `fpl_edge/platform/panels.py` | 229 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/platform/prose_style.py` | 134 | 3 | 1 | docs | keep | 3 importers, 1 test files, 1 other references. |
| `fpl_edge/platform/query.py` | 338 | 6 | 2 | docs | keep | 6 importers, 2 test files, 3 other references. |
| `fpl_edge/platform/registry.py` | 310 | 16 | 15 | panel, docs | keep | 16 importers, 15 test files, 4 other references. |
| `fpl_edge/platform/routes_account.py` | 108 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/platform/scripts/__init__.py` | 19 | 2 | 16 | registry, -m | keep | 2 importers, 16 test files, 17 other references. |
| `fpl_edge/platform/scripts/brief.py` | 2593 | 2 | 1 | none | split into 4 modules, see Table 4 | 2593 lines, of which `dashboard_brief` alone is 1725. |
| `fpl_edge/platform/scripts/common.py` | 98 | 14 | 0 | none | keep | 14 importers, 0 test files, 0 other references. |
| `fpl_edge/platform/scripts/creators.py` | 3994 | 4 | 3 | docs | split into 5 modules, see Table 4 | 3994 lines, the largest module in the package. |
| `fpl_edge/platform/scripts/fixtures.py` | 2463 | 2 | 0 | registry, docs, -m | split into 4 modules, see Table 4 | 2463 lines mixing a build job with two panel scripts. |
| `fpl_edge/platform/scripts/ideas.py` | 283 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/platform/scripts/market.py` | 155 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/platform/scripts/ownership.py` | 2172 | 3 | 0 | docs | split into 3 modules, see Table 4 | 2172 lines, of which `ownership_eo` alone is 975 and the schemas are 400. |
| `fpl_edge/platform/scripts/pipelines_panel.py` | 385 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/platform/scripts/planner.py` | 448 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/platform/scripts/player_profile.py` | 345 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/platform/scripts/prices.py` | 287 | 2 | 0 | none | keep | 2 importers, 0 test files, 0 other references. |
| `fpl_edge/platform/scripts/projections.py` | 1574 | 1 | 0 | docs | split into 3 modules, see Table 4 | 1574 lines, two independent panel modes plus a 325-line schema. |
| `fpl_edge/platform/scripts/radar.py` | 294 | 1 | 1 | none | keep | 1 importers, 1 test files, 0 other references. |
| `fpl_edge/platform/scripts/squad.py` | 329 | 3 | 0 | none | keep | 3 importers, 0 test files, 1 other references. |
| `fpl_edge/platform/solve_runner.py` | 333 | 1 | 0 | docs | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/rank/__init__.py` | 120 | 2 | 6 | none | keep | 2 importers, 6 test files, 10 other references. |
| `fpl_edge/rank/assemble.py` | 152 | 1 | 0 | docs | keep | 1 importers, 0 test files, 1 other references. |
| `fpl_edge/rank/coefficients.py` | 329 | 1 | 1 | none | keep | 1 importers, 1 test files, 4 other references. |
| `fpl_edge/rank/policy.py` | 415 | 4 | 1 | docs | keep | 4 importers, 1 test files, 5 other references. |
| `fpl_edge/rank/state.py` | 448 | 4 | 0 | docs | keep | 4 importers, 0 test files, 5 other references. |
| `fpl_edge/rank/validate.py` | 279 | 2 | 1 | docs | keep | 2 importers, 1 test files, 5 other references. |
| `fpl_edge/rules/__init__.py` | 23 | 14 | 8 | none | keep | 14 importers, 8 test files, 6 other references. |
| `fpl_edge/rules/loader.py` | 118 | 4 | 1 | none | keep | 4 importers, 1 test files, 0 other references. |
| `fpl_edge/sim/__init__.py` | 58 | 0 | 14 | docs, -m | keep | 0 importers, 14 test files, 19 other references. |
| `fpl_edge/sim/calibration.py` | 195 | 2 | 1 | docs | keep | 2 importers, 1 test files, 3 other references. |
| `fpl_edge/sim/engine.py` | 410 | 4 | 5 | docs | keep | 4 importers, 5 test files, 6 other references. |
| `fpl_edge/sim/experiments.py` | 769 | 0 | 0 | docs, -m | keep | 0 importers, 0 tests; referenced by docs/models/simulator.md as a `-m` entry and by a comment in `sim/engine.py`. 769 lines of one-off studies. |
| `fpl_edge/sim/field.py` | 492 | 7 | 8 | none | keep | 7 importers, 8 test files, 2 other references. |
| `fpl_edge/sim/live.py` | 375 | 2 | 1 | docs | keep | 2 importers, 1 test files, 2 other references. |
| `fpl_edge/sim/rank.py` | 217 | 5 | 5 | none | keep | 5 importers, 5 test files, 0 other references. |
| `fpl_edge/sim/squad.py` | 358 | 12 | 6 | none | keep | 12 importers, 6 test files, 1 other references. |
| `fpl_edge/sim/synthetic.py` | 491 | 1 | 8 | none | keep | 1 importers, 8 test files, 1 other references. |
| `fpl_edge/sim/utility.py` | 186 | 2 | 3 | docs | keep | 2 importers, 3 test files, 3 other references. |
| `fpl_edge/store/__init__.py` | 25 | 85 | 79 | none | keep | 85 importers, 79 test files, 35 other references. |
| `fpl_edge/store/fetch_ledger.py` | 227 | 4 | 0 | none | keep | 4 importers, 0 test files, 0 other references. |
| `fpl_edge/store/warehouse.py` | 688 | 14 | 30 | docs | keep | 14 importers, 30 test files, 4 other references. |
| `fpl_edge/theses/__init__.py` | 63 | 2 | 4 | docs | keep | 2 importers, 4 test files, 8 other references. |
| `fpl_edge/theses/cli.py` | 254 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/theses/create.py` | 481 | 3 | 2 | docs | keep | 3 importers, 2 test files, 1 other references. |
| `fpl_edge/theses/grammar.py` | 431 | 4 | 1 | docs | keep | 4 importers, 1 test files, 1 other references. |
| `fpl_edge/theses/model.py` | 320 | 7 | 4 | none | keep | 7 importers, 4 test files, 0 other references. |
| `fpl_edge/theses/report.py` | 50 | 1 | 0 | none | keep | 1 importers, 0 test files, 0 other references. |
| `fpl_edge/theses/resolve.py` | 349 | 2 | 2 | none | keep | 2 importers, 2 test files, 0 other references. |
| `fpl_edge/theses/scoreboard.py` | 213 | 4 | 1 | none | keep | 4 importers, 1 test files, 0 other references. |
| `fpl_edge/theses/store.py` | 128 | 5 | 3 | none | keep | 5 importers, 3 test files, 0 other references. |
| `fpl_edge/types.py` | 168 | 53 | 34 | docs | keep | 53 importers, 34 test files, 12 other references. |

### Delete candidates, with the four counts

Four modules score zero on all four counts. Three are recommended for deletion.

| module | importers | tests | registry/panel refs | doc refs | recommended |
| --- | ---: | ---: | ---: | ---: | --- |
| `fpl_edge/models/ensemble/frame.py` | 0 | 0 | 0 | 0 | delete |
| `fpl_edge/models/ensemble/backtest.py` | 0 | 0 | 0 | 0 | delete |
| `fpl_edge/myteam/__main__.py` | 0 | 0 | 0 | 0 | keep, see below |
| `fpl_edge/platform/fpl_theme.py` | 0 | 0 | 0 | 0 | keep, see below |

`fpl_edge/models/ensemble/weights.py` is the third delete. It does not score
four zeros because `ensemble/backtest.py` imports it, and that module is itself
a delete; with both gone, `weights.py` has no reachable caller.

`fpl_edge/platform/fpl_theme.py` must not be deleted. The dotted-name scan
cannot see its only consumer: `fpl_mcp/tools/viz_tools.py:36` builds the path
`fpl_edge/platform/fpl_theme.py` out of path components and copies the file into
the chart sandbox, where it is imported as top-level `fpl_theme`. This is the
one place in the tree where a four-zero count is wrong, and it is the reason the
method needs a path-based scan as well as a dotted-name one.

`fpl_edge/myteam/__main__.py` is 11 lines and is the `python -m fpl_edge.myteam`
shim. Nothing in the repo invokes that path; the only two mentions are
docstrings in `myteam/__main__.py` itself and `myteam/cli.py`. It is a delete on
counts, kept here because deleting it removes a documented CLI entry point for
no gain.

## Table 3: duplicate pairs

Five pairs, each with the evidence that made it a pair rather than a
coincidence of naming.

| id | pair | evidence |
| --- | --- | --- |
| D1 | `fpl_edge/models/ownership/backtest.py` and `fpl_edge/models/ownership/evaluate.py` | Both docstrings describe out-of-sample evaluation of the ownership forecast. Both define `mae_pp`. The other public functions pair one-to-one: `loso_inseason` with `walk_forward_inseason`, `loso_coldstart` with `walk_forward_coldstart`, `evaluate_captaincy` with `walk_forward_captaincy`. 344 lines against 467. |
| D2 | `fpl_edge/models/field/share.py` and `fpl_edge/models/ownership/eo.py` | Both define a public `effective_ownership` over the same algebra. `share.py` additionally imports `eo.py`, so one file both wraps and reimplements the other. |
| D3 | `fpl_edge/models/team_goals/ratings_cache.py` and `fpl_edge/platform/scripts/fixtures.py` | Two writers of fixture difficulty from one model fit. `jobs/post_gw.py:227` runs `-m fpl_edge.models.team_goals.ratings_cache` as step `fixture_difficulty` and writes the blended artefact; `pipelines/registry.py:587` runs `-m fpl_edge.platform.scripts.fixtures --build` as task `fixture_ratings_refit` and writes the split artefact the UI reads. `scripts/fixtures.py` keeps `load_legacy_difficulty` solely to read the first one back. |
| D4 | `fpl_edge/ingest/rivals/{elite,panel_picks,top1k}.py` | All three expose the same `collect`, `run`, `main` triple around the shared `rivals/crawl.py`, differing only in cohort and budget. Jaccard overlap of public names 0.43 for each pair. `panel_picks.py` and `top1k.py` have zero Python importers and are reached only as `-m` from `post_gw.py` and `pipelines/registry.py`. |
| D5 | `fpl_edge/models/ensemble/backtest.py` and `fpl_edge/eval/projection_scoring.py` | Both walk forward over settled gameweeks scoring projection sources against realised points. `ensemble/backtest.py` has zero importers, zero tests and zero references; `eval/projection_scoring.py` is step `score_projections` in the settlement chain. |

Two further duplicates are at task level rather than module level and belong to
the pipelines workstream, but they are named here because they are the reason
D3 and D4 exist:

- `crawl_panel` inside the settlement chain and `panel_picks_crawl` as a
  standalone task both run `-m fpl_edge.ingest.rivals.panel_picks` over the same
  roster (`post_gw.py:288`, `registry.py:634`).
- `ingest_projections` runs inside the settlement chain (`post_gw.py:211`) and
  again as the first step of `forecast_refresh` (`registry.py:543`).

### Basename collisions that are not duplicates

Twenty-eight basenames are used by more than one module. Most are a healthy
convention (`baselines.py` appears four times, once per model package, and each
one holds the baselines that model must beat). Three collisions are worth
resolving because the name means something different in each place:

| basename | modules | note |
| --- | --- | --- |
| `registry.py` | `interfaces/registry.py` (idea and verdict persistence), `pipelines/registry.py` (scheduled task registry), `platform/registry.py` (panel-script registry) | Three unrelated registries. Verdict in Table 2 renames the first. |
| `inbox.py` | `interfaces/inbox.py` (the idea inbox), `platform/inbox.py` (reads the delivery outbox) | The second one is an outbox reader with an inbox name. |
| `creators.py` | `interfaces/creators.py` (1378 lines, conversational access), `platform/scripts/creators.py` (3994 lines, the Creators tab payload) | Not duplicates: different public surfaces, no shared function names. Both are large and both are the owner's stated pain point. |

## Table 4: modules over 1,500 lines

Ten modules. The proposed split names the new modules and says which top-level
functions move.

| module | lines | proposed split | what moves |
| --- | ---: | --- | --- |
| `fpl_edge/platform/scripts/creators.py` | 3994 | `creators/identity.py`, `creators/board.py`, `creators/detail.py`, `creators/chatter.py`, `creators/report_card.py` | `identity.py`: `youtube_id`, `canonical_key`, `deep_link`, `TranscriptIndex`, the resolver and display-index helpers (lines 122 to 812), plus the shared loaders. `board.py`: `creator_board`, `BOARD_PARAMS`, `BOARD_RESULT`, `_consensus`, `_analysis_coverage`, `_panel_shows`, `_my_roles` (1129 to 2080). `detail.py`: `creator_detail`, `DETAIL_PARAMS`, `DETAIL_RESULT`, `_claim_row`, `_squad`, `_squad_gw`, `_transfers` (2081 to 2437). `chatter.py`: `player_chatter`, `CHATTER_PARAMS`, `CHATTER_RESULT`, `_said`, `_noticed`, `_conviction`, `_coverage` (2438 to 3253). `report_card.py`: `creator_report_card`, `CARD_PARAMS`, `CARD_RESULT`, `_card_scores`, `_card_outcomes`, `_card_people` (3254 to end). The file already carries these five boundaries as comment banners at lines 136, 1129, 1610, 2082, 3255; the split is cutting on lines that are drawn. |
| `fpl_edge/platform/scripts/brief.py` | 2593 | `brief/schema.py`, `brief/tiles.py`, `brief/plan.py`, `brief/build.py` | `schema.py`: `THRESHOLDS` and every `_*` schema fragment plus `RESULT` (lines 114 to 776). `tiles.py`: `_ALERT`, `_TILE`, `_STANDING`, `_HEADER` construction and `best_legal_xi`. `plan.py`: `_PLAN`, `_SOLVE`, `_SUGGESTED`, `_MOVE`, `_DISSENT`, `_VERDICT_LINE` construction. `build.py`: `dashboard_brief`, which is one 1725-line function (857 to 2581) and has to be cut into per-block builders that each return one schema fragment before any file split is real. |
| `fpl_edge/platform/scripts/fixtures.py` | 2463 | `fixtures/ratings.py`, `fixtures/board.py`, `fixtures/detail.py`, `fixtures/build.py` | `ratings.py`: `build_board_ratings`, `_Ratings`, `_read_parquet`, `load_ratings`, `load_legacy_difficulty`, `build_calibration`, `model_calibration` (249 to 723). `board.py`: `fixture_board`, `_lens`, `_blank_lens`, `_team_form`, `_calibration_block`, `_resolved_odds`, `_market_state` (724 to 1622). `detail.py`: `fixture_detail` and the eight `_*_block` builders (1623 to 2390). `build.py`: `write_artefacts` and `main`, the job path that a panel must never call and that the file itself comments on at line 2392. |
| `fpl_edge/platform/scripts/ownership.py` | 2172 | `ownership/schema.py`, `ownership/fields.py`, `ownership/panel.py` | `schema.py`: `_SEGMENT_META`, `PARAMS_SCHEMA`, `_MEASURE`, `_ROW`, `_FIELD`, `_SEGMENT`, `_DIFF_ROW`, `_WHATIF_PLAYER`, `RESULT_SCHEMA` (187 to 735, of which `RESULT_SCHEMA` alone is 209 lines). `fields.py`: `_EXTERNAL_META`, `_external_repeats`, `_cohort_composition`, `_segment_inventory`, `_segment_ownership`, `_selection_includes` (877 to 1185). `panel.py`: `ownership_eo`, one 975-line function, plus `_squad_state` and the numeric helpers. |
| `fpl_edge/ingest/odds.py` | 1966 | `odds/prices.py`, `odds/devig.py`, `odds/football_data.py`, `odds/odds_api.py`, `odds/matching.py` | `prices.py`: `american_to_decimal`, `implied_prob`, `overround`, `GoalRates`, `fit_goal_rates`, `clean_sheet_probs` (133 to 376). `devig.py`: `devig_multiplicative`, `devig_shin`, `shin_z`, `devig_power`, `devig`, `devig_independent`, `devig_anytime_scorer` (168 to 296 and 1101 to 1185). `football_data.py`: `TextFetcher`, `fd_season_code`, `natural_fixture_key`, `parse_football_data_csv`, `ingest_football_data`, `ingest_football_data_fixtures` (377 to 738). `odds_api.py`: `OddsApiError`, `CreditBudgetExceeded`, `OddsApiQuota`, `CreditPlan`, `OddsApiFetcher`, `OddsApiClient`, `parse_odds_api_events`, `resolve_team_name`, and the scorer ingest at 1541 onward. `matching.py`: `match_fixture_keys`, `NameMatch`, `fold_name`, `match_player_names`, `squad_for_fixture`, `MarketFreshness`, `odds_freshness`, `freshness_summary`. All five boundaries already exist as comment banners. |
| `fpl_edge/ingest/content/pipeline.py` | 1808 | `content/cli.py`, `content/analyse_cmd.py`, `content/transcribe_cmd.py`, `content/maintenance_cmd.py` | `cli.py`: `main`, `build_resolver`, `cmd_probe`, `cmd_ingest`, `cmd_score`, `cmd_consensus`. `analyse_cmd.py`: `cmd_analyze`, `rank_candidates`, `relevance_score`, `cmd_reextract`, `cmd_backfill_insights`, and the `_writer`/`_write_with_retry`/`_is_contention` DuckDB write-lock helpers those two share. `transcribe_cmd.py`: `cmd_transcribe`, `_asr_fetcher`, `cmd_retention`. `maintenance_cmd.py`: `cmd_repair_index`, `_index_is_healthy`, `_rebuild_table`, `cmd_link_identities`. The eleven `cmd_*` functions are already independent of each other; only the write-retry helpers are shared. |
| `fpl_edge/platform/link_jobs.py` | 1697 | `link_jobs/preflight.py`, `link_jobs/take.py`, `link_jobs/runner.py` | `preflight.py`: `Preflight`, `preflight`, `_preflight_youtube`, `_preflight_article`, `_reader_context`, `_gw_preview`, `_default_fetcher` (252 to 679). `take.py`: `build_take`, `_ledger`, `_attribution`, `_existing_item`, `_rank_items`, `_item_for_url`, `discard_item`, `restore_item`, `correct_gameweek`, `ingest_with_retry` (680 to 1024). `runner.py`: `_Job`, `_Cancelled`, `UnknownJob`, `JobAlreadyFinished`, `NotAwaitingDecision`, `LinkJobs` (1025 to end). |
| `fpl_edge/interfaces/dossier.py` | 1674 | `dossier/load.py`, `dossier/sections.py`, `dossier/render.py` | `load.py`: `_Ctx`, `resolve`, `_load_rates`, `_load_fixtures`, `_load_ownership`, `_load_projection`, `_simulate_projection`, `_load_intel`. `sections.py`: `Section`, `_ok`, `_gap`, and the fourteen section builders `_identity` through `_disagreement` (450 to 1433). `render.py`: `Dossier`, `build`, `build_text`, `register_cli`, `telegram_addendum`, `mcp_payload`. |
| `fpl_edge/platform/scripts/projections.py` | 1574 | `projections/schema.py`, `projections/artefact.py`, `projections/gw.py` | `schema.py`: `PARAMS`, `_ARTEFACT_RESULT`, `_GW_ROW`, `_GW_RESULT` (52 to 530, of which `_GW_RESULT` alone is 325 lines). `artefact.py`: `_artefact_mode` and `projection_table`'s artefact branch. `gw.py`: `_gw_mode` (590 lines), `_latest_scores_sql`, `_weights_block`, `_annotate_applied_weights`, `_provider_accuracy_block`, `_player_detail`. The two modes share only `projection_table`, which stays as a 40-line dispatcher. |
| `fpl_edge/platform/app.py` | 1508 | `app/factory.py`, `app/routes_panels.py`, `app/routes_solve.py`, `app/helpers.py` | `factory.py`: `create_app` reduced to wiring plus the Pydantic request models (90 to 172). `routes_panels.py`: the panel, query, chat and monitor routes out of `create_app`, plus `_monitor_definitions` and `_player_lookup`. `routes_solve.py`: the solve and transfer-plan routes, plus `_solve_plan`, `_plan_codes`, `_transfer_plan`, `_held_squad`, `_solve_diff_lines`. `helpers.py`: `_deadline_calendar`, `_brief_thresholds`, `serve`. `create_app` is one 908-line function and has to be broken into router registrations before the file split is real. |

## Table 5: import cycles

Nine strongly connected components of size greater than one, found by Tarjan's
algorithm over the AST import graph (Appendix A, `strongconnect`). No
self-loops. Seven of the nine are a package `__init__` re-exporting from its own
submodules, which is benign; two are genuine cross-package cycles and are marked
as such.

| id | cycle | size | kind |
| --- | ---: | --- | --- |
| C1 | `jobs.deadline_dag` to `pipelines` to `pipelines.health` to `pipelines.registry` to `pipelines.runner` to `platform.briefing_intel` to `platform.scripts` to `platform.scripts.pipelines_panel` and back | 8 | Cross-package. `pipelines` and `platform` import each other. This is the one to break: `pipelines/registry.py` imports `jobs.deadline_dag` at module level, `jobs/deadline_dag.py` imports `pipelines` inside functions, and `platform/scripts/pipelines_panel.py` imports the registry to render the board. |
| C2 | `ingest.content.loaders` to `ingest.content.panel` to `ingest.content.pipeline` to `ingest.content.store` to `interfaces.creators` to `interfaces.qa` to `platform.scripts.creators` to `platform.scripts.ownership` and back | 8 | Cross-package. `ingest.content` imports `platform.scripts` and `platform.scripts` imports `ingest.content`. A panel script being inside an ingest package's import closure is a layering break, and it is the same pair of files Table 4 splits. |
| C3 | `models.minutes` to `models.minutes.baselines` to `models.minutes.gbm` to `models.minutes.hierarchical` | 4 | Package `__init__` re-export. |
| C4 | `myteam.manual` to `myteam.state` | 2 | Within-package, mutual import. |
| C5 | `intel` to `intel.collect` | 2 | Package `__init__` re-export. |
| C6 | `ingest.content.sources` to `ingest.content.youtube` | 2 | Within-package, mutual import. |
| C7 | `models.ownership` to `models.ownership.model` | 2 | Package `__init__` re-export. |
| C8 | `theses.grammar` to `theses.model` | 2 | Within-package, mutual import. |
| C9 | `theses` to `theses.resolve` | 2 | Package `__init__` re-export. |

Method: Tarjan's strongly-connected-components over the directed graph whose
nodes are the 250 modules and whose edges are every `import`/`from ... import`
statement anywhere in the AST, including inside function bodies, with each
dotted target resolved down to the nearest real module. Relative imports are
resolved against the importing module's own package. The implementation is in
Appendix A.

## Appendix A: the graph script

Run as `python3 graph.py /path/to/repo > graph.json`. It writes one JSON object
with `modules`, `packages`, `cycles`, `self_loops`, `dups` and `totals`. It
reads nothing outside the repo except `~/Library/LaunchAgents/com.fpledge.*.plist`,
and it writes nothing. It is reproduced here rather than committed, so that
rerunning it is a deliberate act.

Two known limits, both hit in this repo. It matches external references by
dotted name and by slash path, so a consumer that assembles the path out of
components is invisible to it: that is why `platform/fpl_theme.py` shows four
zeros while `fpl_mcp/tools/viz_tools.py` depends on it. And its duplicate
detector compares public top-level names only, so it finds D1, D4 and D5 but
not D2 or D3, which were found by reading the docstrings of the biggest files.

```python
#!/usr/bin/env python3
"""Build the fpl_edge import graph mechanically.

Usage: python3 graph.py /path/to/repo > graph.json
Emits one JSON object with: modules, packages, cycles, dup_candidates.
"""
import ast, json, os, re, sys
from collections import defaultdict

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
PKG = "fpl_edge"
PKG_DIR = os.path.join(ROOT, PKG)


def modname(path):
    rel = os.path.relpath(path, ROOT)
    assert rel.endswith(".py")
    rel = rel[:-3]
    parts = rel.split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve(node, cur_mod, cur_is_pkg):
    """Return list of fpl_edge.* dotted targets referenced by an import node."""
    out = []
    if isinstance(node, ast.Import):
        for a in node.names:
            if a.name == PKG or a.name.startswith(PKG + "."):
                out.append(a.name)
    elif isinstance(node, ast.ImportFrom):
        if node.level:
            base = cur_mod.split(".")
            if not cur_is_pkg:
                base = base[:-1]
            up = node.level - 1
            if up:
                base = base[:-up] if up <= len(base) else []
            prefix = ".".join(base)
            mod = prefix + ("." + node.module if node.module else "")
        else:
            mod = node.module or ""
        if mod == PKG or mod.startswith(PKG + "."):
            out.append(mod)
            for a in node.names:
                if a.name != "*":
                    out.append(mod + "." + a.name)
    return out


files = []
for dirpath, dirnames, filenames in os.walk(PKG_DIR):
    dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".venv")]
    for fn in filenames:
        if fn.endswith(".py"):
            files.append(os.path.join(dirpath, fn))
files.sort()

known = {}
for p in files:
    known[modname(p)] = p

modules = {}
for p in files:
    m = modname(p)
    src = open(p, encoding="utf-8", errors="replace").read()
    lines = src.count("\n") + (0 if src.endswith("\n") else 1)
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        modules[m] = dict(path=os.path.relpath(p, ROOT), lines=lines,
                          imports=[], funcs=[], classes=[], doc=None,
                          parse_error=str(e))
        continue
    cur_is_pkg = os.path.basename(p) == "__init__.py"
    targets = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for t in resolve(node, m, cur_is_pkg):
                # map a dotted target down to the nearest real module
                cand = t
                while cand and cand not in known:
                    cand = cand.rsplit(".", 1)[0] if "." in cand else ""
                if cand and cand != m:
                    targets.add(cand)
    funcs = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    classes = [n.name for n in tree.body if isinstance(n, ast.ClassDef)]
    modules[m] = dict(path=os.path.relpath(p, ROOT), lines=lines,
                      imports=sorted(targets), funcs=funcs, classes=classes,
                      doc=(ast.get_docstring(tree) or "").strip().split("\n")[0][:200])

importers = defaultdict(set)
for m, d in modules.items():
    for t in d["imports"]:
        importers[t].add(m)
for m in modules:
    modules[m]["importers"] = sorted(importers.get(m, ()))

# ---- external reference scan -------------------------------------------------
def scan(paths):
    blob = {}
    for p in paths:
        if os.path.isfile(p):
            try:
                blob[p] = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                pass
    return blob

test_blob = {}
for dp, dn, fns in os.walk(os.path.join(ROOT, "tests")):
    dn[:] = [d for d in dn if d != "__pycache__"]
    for fn in fns:
        if fn.endswith(".py"):
            test_blob[os.path.join(dp, fn)] = open(os.path.join(dp, fn), encoding="utf-8", errors="replace").read()

ref_paths = []
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "data", "tests"}
for dp, dn, fns in os.walk(ROOT):
    dn[:] = [x for x in dn if x not in SKIP_DIRS]
    for fn in fns:
        if fn.endswith((".py", ".md", ".sh", ".js", ".html", ".toml", ".json", ".plist")) or fn == "Makefile":
            ref_paths.append(os.path.join(dp, fn))
la = os.path.expanduser("~/Library/LaunchAgents")
if os.path.isdir(la):
    for fn in os.listdir(la):
        if fn.startswith("com.fpledge.") and fn.endswith(".plist"):
            ref_paths.append(os.path.join(la, fn))
ref_blob = scan(ref_paths)

registry_src = ref_blob.get(os.path.join(ROOT, "fpl_edge/pipelines/registry.py"), "")
panels_src = ref_blob.get(os.path.join(ROOT, "fpl_edge/platform/panels.py"), "")

for m, d in modules.items():
    dotted = m
    slashed = m.replace(".", "/") + ".py"
    leaf = m.rsplit(".", 1)[-1]
    pats = [dotted, slashed]
    d["tests"] = sorted(os.path.relpath(p, ROOT) for p, s in test_blob.items()
                        if any(x in s for x in pats))
    own = os.path.join(ROOT, d["path"])
    importer_files = {os.path.join(ROOT, modules[i]["path"]) for i in d["importers"]}
    hits = []
    execs = []
    for fp, txt in ref_blob.items():
        if fp == own or fp in importer_files:
            continue
        if any(x in txt for x in pats):
            hits.append(os.path.relpath(fp, ROOT) if fp.startswith(ROOT) else fp)
            if re.search(r'-m["\s,]+["\s]*' + re.escape(dotted) + r'\b', txt):
                execs.append(os.path.relpath(fp, ROOT) if fp.startswith(ROOT) else fp)
    d["refs"] = sorted(hits)
    d["exec_refs"] = sorted(execs)
    d["registry_ref"] = any(x in registry_src for x in pats)
    d["panel_ref"] = any(x in panels_src for x in pats)
    d["doc_ref"] = any(r.startswith("docs/") for r in d["refs"])

# ---- cycles (Tarjan) ---------------------------------------------------------
index = {}
low = {}
onstack = {}
stack = []
counter = [0]
sccs = []

def strongconnect(v):
    work = [(v, 0)]
    while work:
        node, pi = work[-1]
        if pi == 0:
            index[node] = low[node] = counter[0]
            counter[0] += 1
            stack.append(node)
            onstack[node] = True
        recurse = False
        succs = modules[node]["imports"]
        for i in range(pi, len(succs)):
            w = succs[i]
            if w not in modules:
                continue
            if w not in index:
                work[-1] = (node, i + 1)
                work.append((w, 0))
                recurse = True
                break
            elif onstack.get(w):
                low[node] = min(low[node], index[w])
        if recurse:
            continue
        if low[node] == index[node]:
            comp = []
            while True:
                w = stack.pop()
                onstack[w] = False
                comp.append(w)
                if w == node:
                    break
            sccs.append(sorted(comp))
        work.pop()
        if work:
            parent = work[-1][0]
            low[parent] = min(low[parent], low[node])

for m in sorted(modules):
    if m not in index:
        strongconnect(m)

cycles = [c for c in sccs if len(c) > 1]
self_loops = [m for m in modules if m in modules[m]["imports"]]

# ---- duplicate detection -----------------------------------------------------
dups = []
names = sorted(modules)
for i, a in enumerate(names):
    fa = set(n for n in modules[a]["funcs"] if not n.startswith("_"))
    ca = set(modules[a]["classes"])
    sa = fa | ca
    if len(sa) < 3:
        continue
    for b in names[i + 1:]:
        fb = set(n for n in modules[b]["funcs"] if not n.startswith("_"))
        cb = set(modules[b]["classes"])
        sb = fb | cb
        if len(sb) < 3:
            continue
        inter = sa & sb
        j = len(inter) / len(sa | sb)
        if len(inter) >= 3 and j >= 0.3:
            dups.append(dict(a=a, b=b, shared=sorted(inter), jaccard=round(j, 3)))
dups.sort(key=lambda d: -d["jaccard"])

# ---- packages ----------------------------------------------------------------
def pkg_of(m):
    parts = m.split(".")
    return ".".join(parts[:-1]) if len(parts) > 1 else m

pkgs = defaultdict(lambda: dict(files=0, lines=0, imports=set(), importers=set(), modules=[]))
for m, d in modules.items():
    p = pkg_of(m) if os.path.basename(d["path"]) != "__init__.py" else m
    pkgs[p]["files"] += 1
    pkgs[p]["lines"] += d["lines"]
    pkgs[p]["modules"].append(m)
for m, d in modules.items():
    p = pkg_of(m) if os.path.basename(d["path"]) != "__init__.py" else m
    for t in d["imports"]:
        tp = pkg_of(t) if os.path.basename(modules[t]["path"]) != "__init__.py" else t
        if tp != p:
            pkgs[p]["imports"].add(tp)
            pkgs[tp]["importers"].add(p)

out = dict(
    modules=modules,
    packages={k: dict(files=v["files"], lines=v["lines"],
                      imports=sorted(v["imports"]), importers=sorted(v["importers"]),
                      modules=sorted(v["modules"])) for k, v in pkgs.items()},
    cycles=cycles, self_loops=self_loops, dups=dups,
    totals=dict(modules=len(modules), packages=len(pkgs),
                lines=sum(d["lines"] for d in modules.values())),
)
json.dump(out, sys.stdout, indent=1, sort_keys=True)
```
