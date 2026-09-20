# Layout

Where the code lives and which package may import which. The table at the
bottom is generated from the tree by `scripts/module_layout.py`, so it cannot
drift from the files. The layer rules above it are a decision and are written
by hand.

Related pages: [ARCHITECTURE.md](ARCHITECTURE.md) for what the system does,
[ARCHITECTURE_REVIEW.md](ARCHITECTURE_REVIEW.md) Section 3 for the frozen
target this tree was refactored towards, [PIPELINES.md](PIPELINES.md) for what
runs on a schedule, [MCP.md](MCP.md) for the toolbelt, and
[DEPLOYMENT.md](DEPLOYMENT.md) for how it ships.

## Keeping the table honest

```
uv run python scripts/module_layout.py            # print the table
uv run python scripts/module_layout.py --write    # rewrite the block below
uv run python scripts/module_layout.py --check    # exit 1 when stale
```

`tests/unit/test_layout_doc.py` runs the same comparison, so a module added,
renamed, moved or deleted without rewriting this page fails the suite. The
responsibility column is the first sentence of the module docstring. A module
that prints `(no module docstring)` is a module nobody has said the purpose
of, which is the one thing this page is meant to surface.

## Layer rules

Imports run one way, top to bottom. No edge runs upward.

```
jobs, mcp
   |
pipelines
   |
platform
   |
interfaces
   |
ingest
   |
models, sim, opt, rank
   |
store, rules
```

`pipelines.contracts` is a leaf. It holds `Step`, `TaskContext`,
`TaskResult`, `run_step` and the schedule vocabulary, and it imports nothing
from the layers above it, so `platform` and `jobs` can both depend on the task
vocabulary without depending on the registry that uses it.

Three edges inside a layer are named and allowed:

* `platform.scripts.pipelines_panel -> pipelines`, so the Pipelines tab reads
  the same registry the scheduler runs. One way.
* `platform.scripts.ownership -> interfaces.qa`, so the ownership panel reports
  the same data-quality findings the CLI does. One way.
* `platform/chat_agent.py -> fpl_edge.mcp.server`, inside `list_mcp_tools` and
  `toolbelt_instance`. At package granularity this is `platform -> mcp ->
  platform`, which the tier list above forbids. It is allowed by name because
  both imports are function local and `fpl_edge/mcp/__init__.py` is empty, so
  importing the package costs nothing and no import cycle can form at module
  scope. `tests/unit/test_mcp_tool_contract.py` fails on a module-scope import
  of `fpl_edge.mcp` from anywhere outside the package, which is the guard that
  keeps this the only such edge.

## What each top-level package is

| Package | Role |
|---|---|
| `store` | The DuckDB warehouse, the append-only writes, the point-in-time reads. |
| `rules` | The FPL rulebook as data: squad legality, chips, prices, scoring. |
| `models`, `sim`, `opt`, `rank` | The models, the field simulator, the solver, and the rank-utility layer. Pure functions over frames. |
| `ingest` | Everything that reads the outside world: the FPL API, odds, projections, creator content, rivals. Writes facts, never opinions. |
| `interfaces` | The operator surfaces that are not the web app: the CLI reports, the dossier, ideas, Telegram. |
| `platform` | The web server, the panels, the chat agent. Panels are read-only over the warehouse. |
| `pipelines` | The task registry, the run contracts, the health view, the runner. |
| `jobs` | The scheduled chains: the deadline DAG and the post-gameweek settlement. |
| `mcp` | The MCP toolbelt, a second consumer of the same panels. |
| `cli` | The `fpl` entry point that binds the layers into commands. |
| `eval`, `intel`, `myteam`, `oracle`, `theses` | Scoring and backtests, the availability and set-piece intel, the owner's squad, the signal adapters, and the thesis ledger. |

## The tree

<!-- BEGIN GENERATED: scripts/module_layout.py -->

342 modules, 114,052 lines.

| Package | Module | Responsibility | Lines |
|---|---|---|---:|
| `fpl_edge` | `__init__.py` | fpl-edge | 1 |
| `fpl_edge.cli` | `__init__.py` | fpl-edge | 1 |
| `fpl_edge.cli` | `main.py` | The `fpl` command line | 643 |
| `fpl_edge.cli` | `recommend.py` | ``fpl recommend`` -- the transfer recommendation for YOUR fifteen | 512 |
| `fpl_edge.cli` | `solve.py` | ``fpl solve`` -- the horizon solve, reachable at last | 494 |
| `fpl_edge` | `config.py` | User configuration | 254 |
| `fpl_edge.eval` | `__init__.py` | fpl-edge | 1 |
| `fpl_edge.eval` | `baselines.py` | Baseline strategies the engine must beat before it can be trusted | 206 |
| `fpl_edge.eval` | `calibration.py` | Calibration scoring for probabilistic outputs | 152 |
| `fpl_edge.eval` | `creator_report_card.py` | The creator report card: three evidence channels that are never one number | 542 |
| `fpl_edge.eval` | `projection_scoring.py` | The projection calibration loop: score providers against settled actuals, then let the scores earn the ensemble weights | 454 |
| `fpl_edge.eval` | `replay.py` | Walk-forward season replay | 305 |
| `fpl_edge.eval` | `scoring.py` | Exact gameweek scoring: autosubs, captaincy fallback and chips | 221 |
| `fpl_edge.ingest` | `__init__.py` | fpl-edge | 1 |
| `fpl_edge.ingest.content` | `__init__.py` | Content intelligence: what FPL creators are saying, and who has earned a say | 24 |
| `fpl_edge.ingest.content` | `analyse_cmd.py` | Claim extraction and the relevance gate: the commands that spend tokens | 802 |
| `fpl_edge.ingest.content` | `analyze.py` | Semantic transcript analysis: Claude reads the episode, not a keyword window | 1339 |
| `fpl_edge.ingest.content` | `asr.py` | Local speech-to-text, so the corpus holds what was *said* | 938 |
| `fpl_edge.ingest.content` | `calendar.py` | Gameweek deadlines for every season the warehouse holds | 101 |
| `fpl_edge.ingest.content` | `claims.py` | Turning what a creator said into something that can be proved wrong | 394 |
| `fpl_edge.ingest.content` | `clubs.py` | Resolve a club name as spoken into a ``dim_team.team_code``, or refuse | 109 |
| `fpl_edge.ingest.content` | `consensus.py` | Deduplication and the consensus map -- with its own limitations attached | 146 |
| `fpl_edge.ingest.content` | `feeds.py` | RSS/Atom parsing without a new dependency | 394 |
| `fpl_edge.ingest.content` | `fetch.py` | Polite HTTP for content, with the archive and the honesty this package needs | 244 |
| `fpl_edge.ingest.content` | `identity.py` | Linking a creator name to an FPL entry, and refusing to guess when it cannot | 157 |
| `fpl_edge.ingest.content` | `link_ledger.py` | The pasted-link ledger: one annotation row per item the owner pasted | 394 |
| `fpl_edge.ingest.content` | `loaders.py` | Source kind -> :class:`ContentItem` list, with every failure reported | 595 |
| `fpl_edge.ingest.content` | `maintenance_cmd.py` | Index repair and creator-identity linking: the commands that fix the store | 259 |
| `fpl_edge.ingest.content` | `models.py` | The two records this package produces, and the one field that matters most | 137 |
| `fpl_edge.ingest.content` | `panel.py` | People, not shows | 955 |
| `fpl_edge.ingest.content` | `pipeline.py` | End to end: fetch -> extract -> resolve -> dedupe -> score -> persist | 446 |
| `fpl_edge.ingest.content` | `pipeline_common.py` | The three helpers the dispatcher and all three command modules need | 38 |
| `fpl_edge.ingest.content` | `resolve.py` | Creator text -> stable :class:`~fpl_edge.types.PlayerCode` | 278 |
| `fpl_edge.ingest.content` | `scoring.py` | Creator track record: the only thing that lets an opinion into the model | 471 |
| `fpl_edge.ingest.content` | `source_state.py` | One honest STATE per source, so nothing working is reported as excluded | 349 |
| `fpl_edge.ingest.content` | `sources.py` | The creator registry: who we listen to, where, and under what permission | 463 |
| `fpl_edge.ingest.content` | `store.py` | Persistence, and the one read path that is safe to use | 558 |
| `fpl_edge.ingest.content` | `transcribe_cmd.py` | Transcription and the audio cache sweep | 557 |
| `fpl_edge.ingest.content` | `urls.py` | URL grammar for content items: canonical identity and deep links | 87 |
| `fpl_edge.ingest.content` | `youtube.py` | YouTube, and the honest answer about transcripts | 800 |
| `fpl_edge.ingest` | `fpl_api.py` | Ingest the official FPL API into the point-in-time warehouse | 214 |
| `fpl_edge.ingest` | `fpl_core_insights.py` | FPL-Core-Insights: third-party per-player per-match stats, xG included | 357 |
| `fpl_edge.ingest` | `http.py` | Cached, polite HTTP with a permanent raw archive | 113 |
| `fpl_edge.ingest` | `lineups.py` | Confirmed starting lineups from the Premier League's own Pulselive API | 576 |
| `fpl_edge.ingest.odds` | `__init__.py` | Odds: prices in, de-vigged probabilities and goal rates out | 161 |
| `fpl_edge.ingest.odds` | `devig.py` | Removing the bookmaker's margin, four ways, and never silently | 233 |
| `fpl_edge.ingest.odds` | `football_data.py` | football-data.co.uk: the free historical closing-odds archive | 385 |
| `fpl_edge.ingest.odds` | `freshness.py` | How old the market data is, per market, and what that costs the reader | 171 |
| `fpl_edge.ingest.odds` | `matching.py` | Joining two sources that disagree about names | 199 |
| `fpl_edge.ingest.odds` | `odds_api.py` | the-odds-api.com: the live, credit-metered feed | 847 |
| `fpl_edge.ingest.odds` | `prices.py` | Prices to probabilities, and goal rates to scorelines | 128 |
| `fpl_edge.ingest` | `odds_derived.py` | Derived bookmaker-implied probabilities: the calibrated prior layer | 661 |
| `fpl_edge.ingest` | `odds_markets.py` | Extra Odds API markets: correct score, BTTS, team totals | 328 |
| `fpl_edge.ingest` | `player_mapping.py` | Cross-season player identity for historical FPL data | 540 |
| `fpl_edge.ingest.projections` | `__init__.py` | Third-party FPL projection and ownership feeds | 18 |
| `fpl_edge.ingest.projections` | `cli.py` | Run the projection ingest | 600 |
| `fpl_edge.ingest.projections` | `fpl_ep.py` | FPL's own ``ep_next``: the zero-cost baseline every other provider must beat | 120 |
| `fpl_edge.ingest.projections` | `fplform.py` | FPL Form: free per-player, per-gameweek expected points | 219 |
| `fpl_edge.ingest.projections` | `github_csv.py` | Community projection feeds published as CSV on raw.githubusercontent.com | 519 |
| `fpl_edge.ingest.projections` | `livefpl.py` | LiveFPL: predicted effective ownership, and top-10k / elite ownership | 318 |
| `fpl_edge.ingest.projections` | `local_csv.py` | Paid projection exports the owner drops on disk by hand | 641 |
| `fpl_edge.ingest.projections` | `premierinjuries.py` | Premier Injuries: a free, explicit P(plays) for every flagged player | 341 |
| `fpl_edge.ingest.projections` | `providers.py` | The third-party projection providers we evaluated, and what each one is | 1089 |
| `fpl_edge.ingest.projections` | `robots.py` | Fail-closed robots.txt checking, shared by every provider in this package | 236 |
| `fpl_edge.ingest.projections` | `rotowire.py` | Rotowire predicted Premier League lineups: a free xMins signal | 579 |
| `fpl_edge.ingest.projections` | `store.py` | Warehouse access for third-party projections | 245 |
| `fpl_edge.ingest` | `results.py` | Settle a finished gameweek's results into ``fact_player_fixture`` -- live | 290 |
| `fpl_edge.ingest.rivals` | `__init__.py` | Mining what demonstrably skilled managers actually do | 28 |
| `fpl_edge.ingest.rivals` | `client.py` | A deliberately slow, cached, budgeted FPL client for scraping other people's teams | 338 |
| `fpl_edge.ingest.rivals` | `crawl.py` | The crawl entry point | 481 |
| `fpl_edge.ingest.rivals` | `elite.py` | The named elite: a curated, verified list of well-known managers, tracked fully | 263 |
| `fpl_edge.ingest.rivals` | `elite_list.py` | The LiveFPL "Best 1000 Managers of All Time" list, captured once and pinned | 94 |
| `fpl_edge.ingest.rivals` | `history.py` | Season and gameweek histories: the only long-run evidence of manager skill | 163 |
| `fpl_edge.ingest.rivals` | `names.py` | Accent-folded name matching, used wherever a curated entry ID is verified | 55 |
| `fpl_edge.ingest.rivals` | `panel_picks.py` | The creator panel's own teams: picks, transfers, chips and gameweek history | 464 |
| `fpl_edge.ingest.rivals` | `picks.py` | Squads and transfers, gameweek by gameweek | 256 |
| `fpl_edge.ingest.rivals` | `roster.py` | Building the candidate pool: who is worth evaluating in the first place | 547 |
| `fpl_edge.ingest.rivals` | `schema.py` | Warehouse tables for other people's teams, added without touching schema.sql | 242 |
| `fpl_edge.ingest.rivals` | `top1k.py` | Sampling the top of the overall league, once there is a top to sample | 496 |
| `fpl_edge.ingest` | `understat.py` | On-demand Understat player profiles: fetch, strict-resolve, cache append-only | 584 |
| `fpl_edge.ingest` | `vaastav.py` | Ingest vaastav/Fantasy-Premier-League history into the point-in-time warehouse | 788 |
| `fpl_edge.intel` | `__init__.py` | News and tactical intel: what changed, when it became public, and who says so | 60 |
| `fpl_edge.intel` | `availability.py` | The leak-proof injury feed: FPL's own ``news`` with FPL's own timestamp | 139 |
| `fpl_edge.intel` | `bootstrap.py` | Reading the intel FPL already gives us, out of the raw archive | 298 |
| `fpl_edge.intel` | `cli.py` | ``fpl intel ...`` -- collect, inspect and alert on news and tactical signals | 183 |
| `fpl_edge.intel` | `collect.py` | One pass of the intel collector: archive in, dated intel rows out | 239 |
| `fpl_edge.intel` | `formations.py` | Formation, counted from who actually started -- in FPL's classification | 169 |
| `fpl_edge.intel` | `items.py` | The value types the intel layer moves around | 306 |
| `fpl_edge.intel` | `oop.py` | Out-of-position detection: FPL's classification against the player's profile | 263 |
| `fpl_edge.intel` | `setpieces.py` | Set-piece and penalty order, and the change that is worth knowing about | 404 |
| `fpl_edge.intel` | `sources.py` | Reaching external intel sources honestly, and recording what happened | 296 |
| `fpl_edge.intel` | `store.py` | Persistence for intel, with point-in-time reads that cannot leak | 542 |
| `fpl_edge.interfaces` | `__init__.py` | User-facing surfaces: the idea inbox, the Telegram bot, the weekly report | 69 |
| `fpl_edge.interfaces` | `analyses.py` | Saved analyses: one read-only SQL statement, named, versioned in git | 308 |
| `fpl_edge.interfaces` | `bias.py` | Measuring the user's biases from their own idea history | 406 |
| `fpl_edge.interfaces` | `briefing.py` | The warehouse briefing: what the chat agent knows before its first query | 264 |
| `fpl_edge.interfaces` | `creators.py` | Conversational access to the content-creator pipeline | 889 |
| `fpl_edge.interfaces.dossier` | `__init__.py` | Everything we know about one player at one instant | 51 |
| `fpl_edge.interfaces.dossier` | `load.py` | Everything the dossier reads, loaded once | 239 |
| `fpl_edge.interfaces.dossier` | `render.py` | Assembling the dossier, and the four surfaces that serve it | 421 |
| `fpl_edge.interfaces.dossier` | `sections.py` | The sixteen section builders, one per key in ``EXPECTED`` | 1053 |
| `fpl_edge.interfaces` | `features.py` | Snapshot-derived features: what was true about a player when the user spoke | 439 |
| `fpl_edge.interfaces` | `ideas.py` | The Idea: a thought the user had, turned into a falsifiable claim | 373 |
| `fpl_edge.interfaces` | `inbox.py` | The idea inbox: raw text in, a tracked falsifiable claim out | 533 |
| `fpl_edge.interfaces` | `parsing.py` | Turning what the user typed into what the user meant | 593 |
| `fpl_edge.interfaces` | `qa.py` | The question router: conversational access to everything the engine knows | 694 |
| `fpl_edge.interfaces` | `render.py` | Images for the Telegram interface: the squad as a pitch, charts as PNGs | 182 |
| `fpl_edge.interfaces` | `report.py` | The weekly decision report, and the hook other teams plug into | 226 |
| `fpl_edge.interfaces` | `squad_section.py` | The recommended-squad section of the weekly report | 184 |
| `fpl_edge.interfaces` | `store.py` | Persistence for ideas, verdicts, context and the tracking trail | 514 |
| `fpl_edge.interfaces` | `telegram.py` | Telegram long-polling bot: the phone end of the idea inbox | 580 |
| `fpl_edge.interfaces` | `testing.py` | A seeded warehouse and idea generators, for exercising the interfaces offline | 339 |
| `fpl_edge.interfaces` | `tracking.py` | Settling ideas against what actually happened | 200 |
| `fpl_edge.interfaces` | `verdict.py` | The model seam: what the engine says about an idea, the moment it is had | 399 |
| `fpl_edge.interfaces` | `watchlist.py` | The watchlist: players the user wants kept in view until they say stop | 149 |
| `fpl_edge.jobs` | `__init__.py` | Scheduled background jobs | 1 |
| `fpl_edge.jobs` | `deadline_dag.py` | The deadline DAG: an event-relative scheduler for the pre-deadline passes | 627 |
| `fpl_edge.jobs` | `outbox.py` | Durable delivery outbox: the seam between "decided" and "sent" | 257 |
| `fpl_edge.jobs` | `post_gw.py` | The post-gameweek settlement job | 405 |
| `fpl_edge.mcp` | `__init__.py` | The MCP server, as a second surface over the panels | 9 |
| `fpl_edge.mcp` | `__main__.py` | Run the MCP server over stdio | 93 |
| `fpl_edge.mcp` | `adapter.py` | The envelope every tool returns, and the only path to ``run_script`` | 306 |
| `fpl_edge.mcp` | `context.py` | Who this server is answering for, and where its data lives | 125 |
| `fpl_edge.mcp` | `prompts.py` | One prompt, derived from what actually registered | 81 |
| `fpl_edge.mcp` | `render.py` | Pure helpers for the toolbelt | 137 |
| `fpl_edge.mcp` | `server.py` | The one FastMCP instance, and the explicit imports that populate it | 53 |
| `fpl_edge.mcp.tools` | `__init__.py` | Tool modules | 5 |
| `fpl_edge.mcp.tools` | `analysis.py` | Free SQL, saved analyses, and the plotting sandbox | 535 |
| `fpl_edge.mcp.tools` | `creators.py` | The five creator tools: consensus, measured record, chatter, and the archive | 177 |
| `fpl_edge.mcp.tools` | `dossier.py` | The five player tools: the dossier, form, the Understat profile, the radar and the intel feed | 272 |
| `fpl_edge.mcp.tools` | `fixtures.py` | ``fixtures``, ``fixture`` and ``club_form``: the schedule, both lenses | 118 |
| `fpl_edge.mcp.tools` | `ideas.py` | The four idea tools: two reads over panels, two writes through the registry | 245 |
| `fpl_edge.mcp.tools` | `manager.py` | ``manager``: one tracked manager, resolved only through a verifier | 65 |
| `fpl_edge.mcp.tools` | `ownership.py` | ``ownership``: what the field holds, and what that costs you against them | 59 |
| `fpl_edge.mcp.tools` | `pipelines.py` | ``pipelines`` and ``pipeline_log``: what has run, what is due, what failed | 52 |
| `fpl_edge.mcp.tools` | `projections.py` | ``projections``: projected points, where the sources disagree, one player | 130 |
| `fpl_edge.mcp.tools` | `solve.py` | The three solver tools: read the plan, start a solve, poll it | 159 |
| `fpl_edge.mcp.tools` | `squad.py` | ``my_squad`` and ``brief``: the user's own team, and the dashboard | 65 |
| `fpl_edge.mcp.tools` | `watchlist.py` | The three watchlist tools | 210 |
| `fpl_edge.models` | `__init__.py` | fpl-edge | 1 |
| `fpl_edge.models` | `contracts.py` | Interfaces every model implements | 203 |
| `fpl_edge.models.copying` | `__init__.py` | Copying demonstrably skilled managers, and measuring whether it worked | 24 |
| `fpl_edge.models.copying` | `attribution.py` | Whether copying them worked, measured after the fact, per decision | 258 |
| `fpl_edge.models.copying` | `effects.py` | Effect sizes between cohorts of managers, with the multiplicity honestly paid for | 236 |
| `fpl_edge.models.copying` | `features.py` | Strategy features: what a manager *did*, measured per season, never asserted | 398 |
| `fpl_edge.models.copying` | `minileague.py` | Mini-league mode: a different game, played against people you can name | 249 |
| `fpl_edge.models.copying` | `report.py` | The run that produces the numbers, from the warehouse, with nothing invented | 284 |
| `fpl_edge.models.copying` | `skill.py` | Separating skill from luck in a manager's finishing record | 597 |
| `fpl_edge.models.copying` | `template.py` | What the skilled own that the field does not, ranked by how copyable it is | 237 |
| `fpl_edge.models.ensemble` | `__init__.py` | Projection sources: one adapter per estimate, all reduced to one shape | 14 |
| `fpl_edge.models.ensemble` | `sources.py` | Adapters that turn each estimate -- ours, the market's, a stranger's -- into one long projection frame: one row per ``(provider, season, gw, code)`` with an ``xp`` column | 387 |
| `fpl_edge.models.field` | `__init__.py` | The FIELD: the joint distribution of rival squads, per cohort, with provenance | 98 |
| `fpl_edge.models.field` | `cohorts.py` | Cohort behaviour measured from crawled picks: captaincy, chips, ownership | 132 |
| `fpl_edge.models.field` | `contracts.py` | What a field sample IS, and what it must admit about itself | 124 |
| `fpl_edge.models.field` | `drift.py` | Transfer-flow drift: carrying GW k-1 squads forward to the GW k deadline | 358 |
| `fpl_edge.models.field` | `hybrid.py` | The field sampler the engine should actually use: empirical when the world permits it, marginal when it does not, and honest about which it was | 402 |
| `fpl_edge.models.field` | `observed.py` | Observed rival squads, read point-in-time and resampled with their joint structure intact | 391 |
| `fpl_edge.models.field` | `share.py` | Two quantities that are both called "ownership", and never the same number | 405 |
| `fpl_edge.models.minutes` | `__init__.py` | Minutes model: the three-way distribution over how long a player is on the pitch | 50 |
| `fpl_edge.models.minutes` | `base.py` | Shared machinery for every minutes model and baseline | 91 |
| `fpl_edge.models.minutes` | `baselines.py` | The three baselines a minutes model has to beat to be worth running | 172 |
| `fpl_edge.models.minutes` | `dataset.py` | Loading a committed CSV fixture warehouse into a real DuckDB warehouse | 46 |
| `fpl_edge.models.minutes` | `evaluate.py` | Walk-forward evaluation of the minutes models against the required baselines | 313 |
| `fpl_edge.models.minutes` | `features.py` | Feature engineering for the minutes model | 540 |
| `fpl_edge.models.minutes` | `gbm.py` | Gradient-boosted classifier over the engineered minutes features | 176 |
| `fpl_edge.models.minutes` | `hierarchical.py` | Empirical-Bayes hierarchical minutes model | 396 |
| `fpl_edge.models.minutes` | `measured.py` | The measured numbers that go on every ModelCard in this package | 66 |
| `fpl_edge.models.minutes` | `training.py` | Assembling training data without reaching around the Snapshot | 157 |
| `fpl_edge.models.ownership` | `__init__.py` | Ownership, captaincy share and effective ownership | 59 |
| `fpl_edge.models.ownership` | `backtest.py` | Out-of-sample evaluation of the ownership forecast | 441 |
| `fpl_edge.models.ownership` | `baselines.py` | The two baselines an ownership forecast has to beat to be worth running | 72 |
| `fpl_edge.models.ownership` | `captaincy.py` | Who the field captains | 330 |
| `fpl_edge.models.ownership` | `drift.py` | How ownership moves between one deadline and the next | 495 |
| `fpl_edge.models.ownership` | `elite.py` | Ownership among the top-10k, which is the only ownership that decides whether a differential pays | 280 |
| `fpl_edge.models.ownership` | `eo.py` | Effective ownership algebra | 237 |
| `fpl_edge.models.ownership` | `evaluate.py` | Walk-forward evaluation of the ownership forecast | 463 |
| `fpl_edge.models.ownership` | `field.py` | Field size: how many managers there are, and how fast that is changing | 129 |
| `fpl_edge.models.ownership` | `metrics.py` | The two measurement primitives both ownership evaluators report | 29 |
| `fpl_edge.models.ownership` | `model.py` | The OwnershipModel implementation: what the field will own and captain next | 437 |
| `fpl_edge.models.ownership` | `panel.py` | Committed evaluation fixtures, and the network job that rebuilds them | 171 |
| `fpl_edge.models.ownership` | `simulate.py` | A simulated field, for the parts of the forecast reality will not score | 107 |
| `fpl_edge.models.points` | `__init__.py` | Points: a match simulated end to end, then scored by the official rules | 37 |
| `fpl_edge.models.points` | `bps.py` | Bonus points from simulated match events | 109 |
| `fpl_edge.models.points` | `model.py` | Decomposed, correlated points simulation | 256 |
| `fpl_edge.models.points` | `scoring_map.py` | Deterministic map from a match stat line to FPL points | 120 |
| `fpl_edge.models.points` | `shares.py` | Per-player scoring rates, shrunk toward a position prior | 175 |
| `fpl_edge.models.team_goals` | `__init__.py` | Team goal model: Dixon-Coles, market-implied baseline, and the evaluation | 71 |
| `fpl_edge.models.team_goals` | `base.py` | Shared plumbing for everything implementing :class:`TeamStrengthModel` | 135 |
| `fpl_edge.models.team_goals` | `baselines.py` | The two naive baselines the Dixon-Coles model is required to beat | 265 |
| `fpl_edge.models.team_goals` | `blend.py` | Blend of the statistical fit and the market, in log-rate space | 93 |
| `fpl_edge.models.team_goals` | `data.py` | Snapshot-mediated reads for the goal model | 125 |
| `fpl_edge.models.team_goals` | `dixon_coles.py` | Dixon-Coles bivariate goal model, fitted by penalised maximum likelihood | 395 |
| `fpl_edge.models.team_goals` | `evaluate.py` | Walk-forward out-of-sample evaluation | 636 |
| `fpl_edge.models.team_goals` | `market.py` | Market-implied goal rates: the baseline the statistical model has to beat | 159 |
| `fpl_edge.models.team_goals` | `metrics.py` | Scoring rules for goal-model evaluation | 92 |
| `fpl_edge.models.team_goals` | `odds.py` | Consuming bookmaker odds | 264 |
| `fpl_edge.models.team_goals` | `promoted.py` | Priors for clubs with no top-flight history | 296 |
| `fpl_edge.models.team_goals` | `scoreline.py` | Bivariate scoreline distribution: the Dixon-Coles low-score correction | 155 |
| `fpl_edge.models.team_goals` | `synthetic.py` | A synthetic league with known ground truth, and a warehouse to serve it from | 366 |
| `fpl_edge.myteam` | `__init__.py` | The manager's own team: reconstructing it, capturing it, acting on it | 97 |
| `fpl_edge.myteam` | `__main__.py` | ``python -m fpl_edge.myteam`` -- the same sub-app the `fpl` CLI mounts | 11 |
| `fpl_edge.myteam` | `account.py` | Connect, verify and describe the FPL account credential, in one place | 471 |
| `fpl_edge.myteam` | `bot.py` | Squad commands for the Telegram bot, without forking it | 266 |
| `fpl_edge.myteam` | `cli.py` | `fpl myteam ...` -- see the reconstructed team, enter it once, act on it | 631 |
| `fpl_edge.myteam` | `forecast.py` | The seam between the points model and the optimiser | 192 |
| `fpl_edge.myteam` | `manual.py` | The GW1 gap: asking the manager for their 15, once, and confirming it back | 700 |
| `fpl_edge.myteam` | `private.py` | The authenticated my-team reader, driven by a session cookie the manager sets themselves | 265 |
| `fpl_edge.myteam` | `recommend.py` | What to do at the upcoming deadline, what it costs, and what lost | 901 |
| `fpl_edge.myteam` | `report.py` | The ``transfers`` section of the weekly report | 281 |
| `fpl_edge.myteam` | `sources.py` | The public FPL entry endpoints, and the one we are forbidden to touch | 390 |
| `fpl_edge.myteam` | `state.py` | Reconstructing the manager's squad state from public data alone | 789 |
| `fpl_edge.myteam` | `store.py` | Where the manually-entered squad lives | 182 |
| `fpl_edge.myteam` | `tokens.py` | Self-renewing FPL authentication via the standard OAuth refresh grant | 331 |
| `fpl_edge.opt` | `__init__.py` | Multi-gameweek squad optimisation | 104 |
| `fpl_edge.opt` | `config.py` | Optimizer configuration | 292 |
| `fpl_edge.opt` | `interfaces.py` | What the optimiser consumes from other teams | 185 |
| `fpl_edge.opt` | `milp.py` | Multi-gameweek FPL squad MILP | 1170 |
| `fpl_edge.opt` | `plan.py` | The optimiser's output | 111 |
| `fpl_edge.opt` | `problem.py` | The optimiser's input: a fully-specified multi-gameweek decision problem | 384 |
| `fpl_edge.opt` | `scoring.py` | The declared objective, and an independent recomputation of it | 482 |
| `fpl_edge.oracle` | `__init__.py` | The oracle: many weak signals, one traceable verdict per player | 23 |
| `fpl_edge.oracle` | `adapters.py` | Turn each data source into :class:`Signal` objects | 135 |
| `fpl_edge.oracle` | `signals.py` | The oracle's evidence layer | 260 |
| `fpl_edge.pipelines` | `__init__.py` | Pipelines: the product's edge, organised as a first-class package | 39 |
| `fpl_edge.pipelines` | `contracts.py` | The task vocabulary: what a task is handed, what it returns, when it is due | 256 |
| `fpl_edge.pipelines` | `health.py` | Derived pipeline health: one set of rules, returned as data | 458 |
| `fpl_edge.pipelines` | `registry.py` | The task registry: every scheduled pipeline is one reviewable row here | 1352 |
| `fpl_edge.pipelines` | `runner.py` | The one execution path every pipeline run goes through | 268 |
| `fpl_edge.pipelines` | `tasks.py` | The five deadline-relative task bodies | 708 |
| `fpl_edge.platform` | `__init__.py` | The decision platform: panel scripts, one guarded query path, an HTTP app | 19 |
| `fpl_edge.platform.app` | `__init__.py` | The platform HTTP surface, DESIGN.md §2.1, implemented exactly | 78 |
| `fpl_edge.platform.app` | `factory.py` | ``create_app``: seven route groups wired onto one FastAPI instance, with the account router and the static bundle behind them | 215 |
| `fpl_edge.platform.app` | `helpers.py` | Shared by every route group: the request models, the dependency bundle they are handed, and the three warehouse readers two of them call | 324 |
| `fpl_edge.platform.app` | `routes_chat.py` | The briefing artefact and the agent conversations, including the chart assets a turn produced | 173 |
| `fpl_edge.platform.app` | `routes_content.py` | Content source state and the per-source fetch, the pasted-link job with its preview gate, and the three item annotation routes | 350 |
| `fpl_edge.platform.app` | `routes_core.py` | Health, the deadline clock, the panel registry, the script runner and the guarded query path | 285 |
| `fpl_edge.platform.app` | `routes_inbox.py` | Deliveries, and the monitor definitions read back off the deadline DAG | 56 |
| `fpl_edge.platform.app` | `routes_pipelines.py` | The browser's one way to start a registry pipeline, plus its poller | 229 |
| `fpl_edge.platform.app` | `routes_players.py` | The on-demand Understat profile fetch: one POST to start it, one GET to poll it | 78 |
| `fpl_edge.platform.app` | `routes_solve.py` | The solve routes, and the two persisted plan artefacts they serve | 399 |
| `fpl_edge.platform.app` | `routes_transcripts.py` | The two routes the Mac ASR worker talks to | 507 |
| `fpl_edge.platform.auth` | `__init__.py` | Identity, sessions, and the per-user Anthropic key | 44 |
| `fpl_edge.platform.auth` | `keys.py` | The per-user Anthropic API key: encrypted at rest, decrypted per turn | 321 |
| `fpl_edge.platform.auth` | `oauth.py` | Google OAuth 2.0, authorization code with PKCE, in one file | 279 |
| `fpl_edge.platform.auth` | `policy.py` | The access matrix, as one table, and the check that reads it | 239 |
| `fpl_edge.platform.auth` | `routes.py` | The sign-in routes, the key routes, and the one check that reads the matrix | 464 |
| `fpl_edge.platform.auth` | `sessions.py` | The auth database, the session row, and the three cookies | 413 |
| `fpl_edge.platform.auth` | `settings.py` | Every deployment variable the auth layer reads, in one place | 147 |
| `fpl_edge.platform` | `boot.py` | The boot sequence a container runs before it answers a single request | 418 |
| `fpl_edge.platform` | `briefing_intel.py` | briefing_intel, the model-authored salience pass OVER the panels | 1049 |
| `fpl_edge.platform` | `chat_agent.py` | The chat agent loop: conversations driven through the Claude Agent SDK | 1059 |
| `fpl_edge.platform` | `deliveries.py` | Reading and acknowledging the delivery outbox | 131 |
| `fpl_edge.platform` | `fpl_theme.py` | The house chart style: Athletic/Opta grammar, enforced by import | 170 |
| `fpl_edge.platform.link_jobs` | `__init__.py` | Pasted links: preflight, approve, ingest, take | 68 |
| `fpl_edge.platform.link_jobs` | `preflight.py` | Reading a pasted link without writing anything | 513 |
| `fpl_edge.platform.link_jobs` | `runner.py` | The job machine: submit, poll, approve, decline, cancel | 719 |
| `fpl_edge.platform.link_jobs` | `take.py` | Building the take, and the owner's annotations on what was ingested | 362 |
| `fpl_edge.platform` | `panels.py` | Panels: what the UI shows, and which script feeds each one | 241 |
| `fpl_edge.platform` | `prose_style.py` | The house prose rule for model-authored analysis, and its enforcement | 134 |
| `fpl_edge.platform` | `query.py` | The one guarded query path | 337 |
| `fpl_edge.platform` | `registry.py` | The panel-script registry: the only data path the UI has | 410 |
| `fpl_edge.platform` | `routes_account.py` | ``/api/account/*``: connect the FPL account from the browser | 194 |
| `fpl_edge.platform` | `scheduler.py` | The in-process scheduler, and the one writer lock the whole process shares | 340 |
| `fpl_edge.platform.scripts` | `__init__.py` | Panel scripts | 36 |
| `fpl_edge.platform.scripts.brief` | `__init__.py` | dashboard_brief, the dashboard's aggregator, under the anti-drift contract | 90 |
| `fpl_edge.platform.scripts.brief` | `build.py` | ``dashboard_brief``: the assembler, and the panel registration | 184 |
| `fpl_edge.platform.scripts.brief` | `plan.py` | The three blocks that carry the solver's own numbers | 984 |
| `fpl_edge.platform.scripts.brief` | `schema.py` | The JSON Schemas, the threshold table and the precedence the brief echoes | 762 |
| `fpl_edge.platform.scripts.brief` | `tiles.py` | The alert, tile, watch and empty accumulators, and the blocks that fill them | 1147 |
| `fpl_edge.platform.scripts` | `common.py` | Shared helpers for panel scripts | 218 |
| `fpl_edge.platform.scripts.creators` | `__init__.py` | creator_board / creator_detail, the Creators tab's data path | 148 |
| `fpl_edge.platform.scripts.creators` | `board.py` | `creator_board`: who is saying what this gameweek, and the consensus | 522 |
| `fpl_edge.platform.scripts.creators` | `chatter.py` | `player_chatter`: the panel on one player, from what creators said | 865 |
| `fpl_edge.platform.scripts.creators` | `detail.py` | `creator_detail`: one creator expanded into their items, squad and record | 404 |
| `fpl_edge.platform.scripts.creators` | `episodes.py` | `creator_episodes` / `episode_summary`: the archive, opened one layer at a time | 1296 |
| `fpl_edge.platform.scripts.creators` | `identity.py` | Who said it, where it came from, and the loaders every panel shares | 973 |
| `fpl_edge.platform.scripts.creators` | `report_card.py` | `creator_report_card`: measured track record, never blended into one score | 738 |
| `fpl_edge.platform.scripts.creators` | `schema.py` | The JSON Schemas for the board and the detail panels | 488 |
| `fpl_edge.platform.scripts.fixtures` | `__init__.py` | The fixtures data path: a horizon ticker, a per-fixture drilldown, and the cached ratings artefact both of them read | 181 |
| `fpl_edge.platform.scripts.fixtures` | `__main__.py` | ``python -m fpl_edge.platform.scripts.fixtures --build`` | 17 |
| `fpl_edge.platform.scripts.fixtures` | `board.py` | `fixture_board`: the horizon ticker, both lenses per cell | 943 |
| `fpl_edge.platform.scripts.fixtures` | `build.py` | The build job, which a panel must never call | 126 |
| `fpl_edge.platform.scripts.fixtures` | `constants.py` | Artefact names, staleness thresholds and the input-provenance row | 150 |
| `fpl_edge.platform.scripts.fixtures` | `detail.py` | `fixture_detail`: one fixture expanded into its ten blocks | 799 |
| `fpl_edge.platform.scripts.fixtures` | `ratings.py` | The cached fit: reading `fixture_ratings.parquet` back, and building it | 631 |
| `fpl_edge.platform.scripts` | `idea_review.py` | ``idea_review``: how the logged ideas did, and what they say about the user | 391 |
| `fpl_edge.platform.scripts` | `ideas.py` | idea_registry, your theses, the engine's verdict on each, and the outcome | 283 |
| `fpl_edge.platform.scripts` | `manager_lookup.py` | ``manager_lookup``: one tracked manager, resolved only through a verifier | 590 |
| `fpl_edge.platform.scripts` | `market.py` | market_watch, the bookmaker-derived priors, finally read by something | 155 |
| `fpl_edge.platform.scripts.ownership` | `__init__.py` | ownership_eo, the template and effective-ownership panel | 148 |
| `fpl_edge.platform.scripts.ownership` | `fields.py` | The fields on offer, their denominators, and the notes that qualify them | 774 |
| `fpl_edge.platform.scripts.ownership` | `load.py` | What the warehouse holds: EO, the cohort, the sub-cohorts and the squad | 401 |
| `fpl_edge.platform.scripts.ownership` | `panel.py` | ``ownership_eo``: the assembler, and the panel registration | 157 |
| `fpl_edge.platform.scripts.ownership` | `schema.py` | The JSON Schemas, the segment vocabulary and the scalar coercions | 641 |
| `fpl_edge.platform.scripts.ownership` | `tools.py` | The three views of the selected field: diff, what-if and momentum | 281 |
| `fpl_edge.platform.scripts` | `pipelines_panel.py` | The pipelines control panel's data path: the board, and one run's log | 593 |
| `fpl_edge.platform.scripts` | `planner.py` | planner_grid, one payload for the multi-gameweek transfer-planner grid | 563 |
| `fpl_edge.platform.scripts` | `player_dossier.py` | ``player_dossier``: the sixteen-section player view, as a panel | 271 |
| `fpl_edge.platform.scripts` | `player_form.py` | ``player_form``: one player's settled gameweeks, with the season of each row | 362 |
| `fpl_edge.platform.scripts` | `player_intel.py` | ``player_intel``: news, press coverage and set-piece moves, with timestamps | 427 |
| `fpl_edge.platform.scripts` | `player_profile.py` | The player profile panel: one player's Understat season, read FPL-first | 345 |
| `fpl_edge.platform.scripts` | `prices.py` | price_radar, net-transfer velocity between consecutive state snapshots | 287 |
| `fpl_edge.platform.scripts.projections` | `__init__.py` | projection_table: the player board, joined to live price and ownership | 124 |
| `fpl_edge.platform.scripts.projections` | `artefact.py` | ``_artefact_mode``: the solved artefact, one row per player | 149 |
| `fpl_edge.platform.scripts.projections` | `gw.py` | ``_gw_mode``: provider projections for one gameweek, through the views | 1067 |
| `fpl_edge.platform.scripts.projections` | `schema.py` | The JSON Schemas for both modes, and the two rounding helpers | 562 |
| `fpl_edge.platform.scripts` | `radar.py` | player_radar, one player's per-90 percentiles vs same-position peers | 294 |
| `fpl_edge.platform.scripts` | `squad.py` | squad_overview, your actual 15, priced, flagged and projected | 348 |
| `fpl_edge.platform` | `solve_runner.py` | Start and observe `fpl solve` runs from the platform, without owning them | 333 |
| `fpl_edge.platform` | `users.py` | Whose team a request is about, and where that manager's own files live | 475 |
| `fpl_edge.rank` | `__init__.py` | Rank-aware decision layer: the F3 -> F2 solver, with F1 as validator | 120 |
| `fpl_edge.rank` | `assemble.py` | Assemble RANK_MV's evidence from a live warehouse | 152 |
| `fpl_edge.rank` | `coefficients.py` | Per-player objective coefficients for ``ObjectiveMode.RANK_MV`` | 329 |
| `fpl_edge.rank` | `policy.py` | The closed forms | 415 |
| `fpl_edge.rank` | `state.py` | Where the manager stands, in the only numbers the objective needs | 448 |
| `fpl_edge.rank` | `validate.py` | F1 as a validator: paired common-random-number checks on the shortlist | 279 |
| `fpl_edge.rules` | `__init__.py` | Verified FPL rule access | 23 |
| `fpl_edge.rules` | `loader.py` | Load and access the verified FPL rule registry | 118 |
| `fpl_edge.sim` | `__init__.py` | Monte Carlo simulator and rank utility | 58 |
| `fpl_edge.sim` | `calibration.py` | Anchors the simulated field has to reproduce, and the code that checks them | 195 |
| `fpl_edge.sim` | `engine.py` | The rest-of-season Monte Carlo engine | 410 |
| `fpl_edge.sim` | `experiments.py` | The experiments that decide whether this engine is worth running | 769 |
| `fpl_edge.sim` | `field.py` | The field: a distribution over rival *squads*, not over rival *scores* | 492 |
| `fpl_edge.sim` | `live.py` | Wiring the simulator to the real models instead of the development stand-ins | 376 |
| `fpl_edge.sim` | `rank.py` | Turning simulated scores into a rank distribution | 217 |
| `fpl_edge.sim` | `squad.py` | Squads, formations and the autosub engine | 358 |
| `fpl_edge.sim` | `synthetic.py` | Stand-in points and ownership models, so the simulator is never blocked | 491 |
| `fpl_edge.sim` | `utility.py` | Rank utility: the objective the optimizer actually maximises | 186 |
| `fpl_edge.store` | `__init__.py` | Point-in-time warehouse | 25 |
| `fpl_edge.store` | `backup.py` | Snapshots of the warehouse file, and the restore that puts one back | 1085 |
| `fpl_edge.store` | `fetch_ledger.py` | The fetch ledger and write-on-change: what ran, and what actually changed | 360 |
| `fpl_edge.store` | `warehouse.py` | DuckDB warehouse with point-in-time-correct reads | 724 |
| `fpl_edge.theses` | `__init__.py` | The hypothesis registry: every belief becomes a versioned file that gets graded | 63 |
| `fpl_edge.theses` | `cli.py` | `fpl thesis add` and `fpl theses ...`, the terminal surface of the registry | 254 |
| `fpl_edge.theses` | `create.py` | Creation paths: CLI, API for other teams, and the ideas-pipeline bridge | 481 |
| `fpl_edge.theses` | `grammar.py` | The closed grammar of falsifiable predictions, and one grader per template | 431 |
| `fpl_edge.theses` | `model.py` | The thesis file format: YAML front matter over prose, round-trippable | 320 |
| `fpl_edge.theses` | `report.py` | The theses section of the weekly report, via the interfaces registry hook | 50 |
| `fpl_edge.theses` | `resolve.py` | Resolution: grade what the gameweek settled, move the files, commit the truth | 349 |
| `fpl_edge.theses` | `scoreboard.py` | Per-source and per-creator accuracy, in the shape the oracle already eats | 213 |
| `fpl_edge.theses` | `store.py` | The on-disk registry: theses/open, theses/resolved, theses/scoreboard | 128 |
| `fpl_edge` | `types.py` | Core identities and value types | 168 |

<!-- END GENERATED -->
