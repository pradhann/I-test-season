# The MCP server, rewritten as a second surface over the panels

Workstream M, spec half. This is the contract for agent M2, who builds it.

The web UI reaches data through one path: a panel script, registered in
`fpl_edge/platform/registry.py`, run by `run_script`, returning a typed result
plus provenance. The MCP server today reaches data through six other paths:
raw warehouse SQL, semantic macros, the live FPL API, parquet artefacts, the
engine's interface modules, and a subprocess sandbox. A number Claude reads and
a number the dashboard draws can therefore differ, and on fixture difficulty
they already do: the MCP tool at `fpl_mcp/tools/semantic_tools.py:424` reads
the deprecated blended `fixture_difficulty.parquet`, while the Fixtures page
reads the split `fixture_ratings.parquet` that replaced it.

The rewrite makes the MCP server the second consumer of the same panels. Where
a panel does not exist for something a tool must serve, the panel gets built
first and the web gets it too.

Scope of the package being replaced: `fpl_mcp/` is 5,835 lines of Python across
19 modules, plus a 101-line `README.md` that documents a `main.py`, a vendored
`mcp_sdk/` and a folder layout none of which exist in this repo. It registers
37 tools and 3 prompts.

---

> Count note (2026-09-18): the prose in this document said 26 tools; section 3.2's own table names 35 distinct tools (three rows carry several names), and the build followed the table. The server registers 35 and `tests/unit/test_mcp_tool_contract.py` pins the set by name.

## 1. Inventory

### 1.1 What the 37 tools do today

`chat` names the line in `fpl_edge/platform/chat_agent.py` where the tool
appears in `INTENT_TOOLS` (lines 101 to 132). A blank means the chat agent
never offers it, so the tool exists only for Claude desktop and the CLI.

| # | Tool | Module:line | Reaches | Params | chat | Panel that already serves it | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | `player_projections` | semantic_tools.py:147 | macro `sem_projections`, `sem_players` | player, gw, season, as_of | :112 | `projection_table` (gw mode, `detail_code`) | keep, adapter over `projection_table` |
| 2 | `projection_disagreement` | semantic_tools.py:205 | macro `sem_projection_consensus` | gw, season, top_n, player, as_of | :113 | `projection_table` (`sort="spread"`) | merge into tool 1 |
| 3 | `xpts_aggregate` | semantic_tools.py:268 | macro `sem_projection_consensus`, GROUP BY | group_by, gw, season, position, team, min_price, max_price, as_of | :114 | none, no panel has a group-by mode | delete, `query` covers it |
| 4 | `player_form` | semantic_tools.py:349 | macros `sem_player_form`, `sem_fixtures` | player, last_k, season, as_of | :115 | none | keep, adapter over NEW panel `player_form` |
| 5 | `fixture_difficulty` | semantic_tools.py:424 | macro `sem_fixtures` plus `fixture_difficulty.parquet` (deprecated blend) | team, next_k, season, as_of | :116 | `fixture_board` | keep, adapter over `fixture_board` |
| 6 | `ownership_eo` | semantic_tools.py:523 | macros `sem_ownership`, `sem_projection_consensus` | player, metric, preset, top_n, gw, season, as_of | :117 | `ownership_eo` | keep, adapter over `ownership_eo` |
| 7 | `submit_idea` | edge_tools.py:136 | WRITE, `interfaces/inbox.py IdeaInbox.submit` | text, acted, season, as_of | :120 | `idea_registry` reads the result | keep, write tool |
| 8 | `review_ideas` | edge_tools.py:191 | `interfaces/bias.py review`, read-only warehouse | season, limit, include_ideas | | `idea_registry` covers ideas, not the bias probes | keep, adapter over NEW panel `idea_review` |
| 9 | `track_ideas` | edge_tools.py:274 | WRITE, `interfaces/tracking.py track` | season, as_of | | none, and `track_ideas` is already a step of the settlement chain | delete, duplicated by the pipeline |
| 10 | `weekly_decision_report` | edge_tools.py:303 | `interfaces/report.py weekly_report` | season, gw, as_of | | `dashboard_brief` | merge into a `brief` tool over `dashboard_brief` |
| 11 | `mark_idea_acted` | edge_tools.py:335 | WRITE, `interfaces/store.py IdeaRegistry.mark_acted` | idea_id, acted | | none | keep, write tool |
| 12 | `engine_status` | edge_tools.py:365 | paths, `IdeaRegistry.count`, `Snapshot` | none | | `pipeline_board` | merge into a `pipelines` tool |
| 13 | `player_dossier` | dossier_tools.py:123 | `interfaces/dossier/` build, read-only warehouse, and a live model refit when `simulate=True` | name, season, as_of, gw, horizon_gws, simulate, text | :130 | none | keep, adapter over NEW panel `player_dossier`, `simulate` deleted |
| 14 | `player_intel` | dossier_tools.py:214 | `intel/store.py IntelStore.items` | name, kind, hours, season, as_of, limit | :131 | `fixture_detail` carries team news per fixture, nothing per player | keep, adapter over NEW panel `player_intel` |
| 15 | `set_piece_changes` | dossier_tools.py:329 | `IntelStore.changes` | season, as_of, min_goals_per_game, limit | | none | merge into tool 14 |
| 16 | `fpl_creator_consensus` | content_tools.py:193 | `ContentStore.claims_visible_at`, `consensus_map`, `creator_score` | gameweek, season, as_of, action, top | :122 | `creator_board` | keep, adapter over `creator_board` |
| 17 | `fpl_creator_track_record` | content_tools.py:276 | `creator_score`, `claim_outcome` | min_scored, as_of | :123 | `creator_report_card` | keep, adapter over `creator_report_card` |
| 18 | `fpl_player_claims` | content_tools.py:391 | `ContentStore.claims_visible_at` filtered by code | player_code, as_of, season, limit | :121 | `player_chatter` | keep, adapter over `player_chatter` |
| 19 | `fpl_content_sources` | content_tools.py:466 | raw SQL over `content_source`, `content_item` | none | | `pipeline_board` for ingest health | delete, `pipeline_board` plus `query` |
| 20 | `query` | chat_tools.py:117 | `platform/query.py guarded_query` | sql, as_of | :102 | the web's `/api/query` uses the same guard | keep, unchanged path |
| 21 | `suggest_transfers` | chat_tools.py:191 | the MILP solver, `forecast.parquet`, and the FPL entry endpoints for the squad. One to five minutes | max_hits, horizon, must_keep, notes | :104 | `planner_grid` plus `platform/solve_runner.py` | merge into `transfer_plan` and `solve_start` |
| 22 | `save_analysis` | chat_tools.py:391 | WRITE, `analyses/*.json` plus `git add` and `git commit` | name, description, sql, params_schema | :105 | none | keep, write tool |
| 23 | `run_analysis` | chat_tools.py:497 | `guarded_query` under a 10 s budget | name, params | :106 | none | keep |
| 24 | `list_analyses` | chat_tools.py:565 | filesystem read of `analyses/` | none | :107 | none | keep |
| 25 | `watchlist_add` | chat_tools.py:603 | WRITE, `interfaces/watchlist.py Watchlist.add` | player, note | :108 | none | keep, write tool |
| 26 | `watchlist_list` | chat_tools.py:661 | raw SQL over the `watchlist` table | none | :109 | none | keep, rerouted through `Watchlist.open_items` |
| 27 | `watchlist_remove` | chat_tools.py:693 | WRITE, `Watchlist.resolve` | player | :110 | none | keep, write tool |
| 28 | `get_manager_by_name` | chat_tools.py:730 | `ELITE_NAMED` plus raw SQL over `dim_manager`, `fact_manager_gw`, `fact_manager_season`, `fact_manager_transfer` | name | :111 | none | keep, adapter over NEW panel `manager_lookup` |
| 29 | `get_team_picks` | team_tools.py:106 | LIVE FPL API `entry/{id}/event/{gw}/picks/` plus the bootstrap elements table | gw, team_id | :124 | `squad_overview` | keep, adapter over `squad_overview` |
| 30 | `get_team_summary` | general_tools.py:25 | warehouse `fact_fixture` through `utils/fpl_data.py`, live fallback | team, last_n_games | :127 | `fixture_board` with `include_form` | merge into the `fixtures` tool |
| 31 | `get_expert_teams_summary` | expert_tools.py:391 | LIVE FPL API, one entry-picks call per manager | gw, experts | :128 | `ownership_eo` (cohort and segment ownership from the crawl) | delete, the warehouse holds it |
| 32 | `get_expert_transfers` | expert_tools.py:522 | LIVE FPL API `entry/{id}/transfers/` | expert, last_n | :129 | none, macro `sem_manager_transfers` holds it | merge into the `manager` tool |
| 33 | `get_manager_history` | expert_tools.py:585 | LIVE FPL API `entry/{id}/history/` | manager | :125 | none, `fact_manager_season` and `fact_manager_gw` hold it | merge into the `manager` tool |
| 34 | `player_profile` | profile_tools.py:102 | `run_script("player_profile")` plus one sanctioned ingest fetch | player, season, fetch_if_missing | | `player_profile` | keep, it is already the pattern every other adapter copies |
| 35 | `fetch_youtube_transcript` | transcript_tools.py:33 | YouTube, through `ingest/content/youtube.py` panel captions | url | :119 | `episode_summary` serves what was stored | delete, ingest owns fetching |
| 36 | `summarise_fpl_youtube` | video_tools.py:245 | the same captions plus a keyword heuristic and the bootstrap elements | url | :118 | `episode_summary` (stored analysis, quotes, timestamps) | delete, duplicated and worse |
| 37 | `python_viz` | viz_tools.py:113 | a `python -I` sandbox over datasets from `guarded_query` | code, caption, datasets_json | :103 | none, it renders rather than reads | keep, unchanged |

Three prompts sit beside them: `fpl_query_guidance` (prompts.py:19),
`video_summary_guidance` (prompts.py:74) and `transcript_summary_guidance`
(prompts.py:129). The first is rewritten against the new tool names. The other
two are deleted with tools 35 and 36.

### 1.2 Verdict counts

| Verdict | Count |
|---|---|
| Keep, as an adapter over a named panel or unchanged | 23 |
| Merge into another tool | 8 |
| Delete | 6 |
| Total registered today | 37 |

Of the 23 keeps, 5 are write tools (7, 11, 22, 25, 27) and 18 are reads. Of the
6 deletes, 2 reach the live FPL API for data the warehouse already holds (31,
and the transcript half of 35), 2 are duplicated by a pipeline or a panel
(9, 36), and 2 are covered by `query` or `pipeline_board` (3, 19).

The resulting server registers 35 tools: 18 read adapters, 5 writes, plus
`query`, `run_analysis` and `python_viz` carried over unchanged. Section 3 names
each one.

### 1.3 Five panels that do not exist yet

Every read tool must sit on a panel. Five do not have one, so M2 registers five
new scripts before touching the MCP package. Each is also a surface the web
lacks today, which is why they are built rather than excused.

| New panel | Feeds tools | Reads | Notes |
|---|---|---|---|
| `player_form` | 4 | macros `sem_player_form`, `sem_fixtures` | the settled-gameweek log per player, spanning seasons, naming the season of every row |
| `player_dossier` | 13 | `fpl_edge/interfaces/dossier/` | `build(...)` already returns `{sections, gaps, warnings}` through `to_dict()`; the result schema is that shape. `simulate=True` does not survive the 10 s budget and is dropped |
| `player_intel` | 14, 15 | `fpl_edge/intel/store.py` | items and set-piece changes for one player or league-wide, every row carrying `published_at` and `observed_at` |
| `manager_lookup` | 28, 32, 33 | `ELITE_NAMED`, `dim_manager`, `fact_manager_gw`, `fact_manager_season`, macro `sem_manager_transfers` | carries the verified-identity rule from `expert_tools.py:203-304` verbatim, including the twenty stale seeds refusal |
| `idea_review` | 8 | `fpl_edge/interfaces/bias.py review` | the scoreboard, the acted against skipped split, calibration, and the bias probes with their statistics |

### 1.4 Every write the server performs

Panels do not write. Rule 5 of the shared brief makes that a property of the
panel layer, so writes need their own path and their own list. There are seven
write actions across five surviving tools plus two that the rewrite adds.

| Tool | What it writes | Store it writes through | Keeps |
|---|---|---|---|
| `submit_idea` | an `Idea`, its `Verdict` and its `IdeaContext` | `fpl_edge/interfaces/inbox.py IdeaInbox.submit`, which calls `interfaces/store.py IdeaRegistry.insert_idea`, `insert_verdict`, `insert_context`, `put_pending` | yes |
| `mark_idea_acted` | the `acted` flag on one idea | `IdeaRegistry.mark_acted` (store.py:251) | yes |
| `watchlist_add` | one watchlist item | `fpl_edge/interfaces/watchlist.py Watchlist.add` (watchlist.py:47) | yes |
| `watchlist_remove` | resolves an item, never deletes | `Watchlist.resolve` (watchlist.py:81) | yes |
| `save_analysis` | a JSON file in `analyses/` and a git commit authored as the owner | the filesystem and `git`, through a `subprocess.run` at chat_tools.py:384 | yes, moved behind a named store module |
| `track_ideas` | settles every resolvable idea | `interfaces/tracking.py track` | no, deleted; the settlement chain owns it |
| `solve_start` | starts a solver job and writes its log, status and plan | `fpl_edge/platform/solve_runner.py start` | new, replacing the in-call solve in `suggest_transfers` |
| `player_profile` fetch | appends Understat match rows | `fpl_edge/ingest/understat.py fetch_player_profile`, the same call `routes_players.py:49` makes | yes |

`save_analysis` is the one write with no store module behind it. M2 adds
`fpl_edge/interfaces/analyses.py`: `save`, `load`, `list_all`, holding the
path rules, the name validation and the git commit, so the MCP tool becomes a
caller rather than an implementation.

---

## 2. The rule set

Nine rules. Each is testable, and section 4 names the test that pins it.

1. **A read tool calls `run_script` and returns the panel's typed result plus
   its provenance unchanged.** No reshaping, no renaming, no rounding: the
   bytes Claude reads are the bytes the browser reads.
2. **A tool runs no SQL of its own except through a semantic macro named in
   `fpl_edge/store/views.sql`,** and the only tool permitted to do even that is
   `query`, which runs `guarded_query` under the same guard as `/api/query`.
3. **Write tools go through `fpl_edge/interfaces/` or
   `fpl_edge/platform/solve_runner.py` only, and are the eight listed in
   section 1.4 and nowhere else;** a test enumerates the registered write tools
   and fails on a name not in that list.
4. **No tool reaches the FPL API, YouTube, Understat or a bookmaker directly.**
   The ingest layer fetches; the warehouse holds; panels read. The one call
   that crosses the boundary is the `player_profile` on-demand fetch, which
   goes through `fpl_edge/ingest/understat.py`, is named in section 1.4, and is
   the same call the web route already makes.
5. **Per-user context comes from the `UserContext` the web uses**
   (`docs/platform/MULTI_USER.md` section 2, built by agent D2 in
   `fpl_edge/platform/user_context.py`), never from `fpl_edge.config.USER`; a
   stdio MCP session has no HTTP request, so it resolves `owner_context()` at
   startup and passes `ctx` into every panel that takes one.
6. **No tool spends model tokens except none of them.** No tool in the new
   server calls a model. The three token-spending paths in this repo stay where
   they are: `ingest/content/analyze.py:458`, `platform/briefing_intel.py` and
   `platform/chat_agent.py`, and each reads its model id from
   `fpl_edge/config.py`.
7. **The server holds no key.** It reads no `ANTHROPIC_*` variable, opens no
   FPL token file of its own, and logs no credential; the only secret material
   it can reach is the per-user store behind `UserContext`, which decrypts on
   demand and never lands in a payload.
8. **A tool call is one panel run.** One `run_script`, inside the registry's
   10 s budget, with the budget outcome reported rather than hidden.
9. **Absence is a named gap, never a blank.** A panel returning
   `{empty: true, reason: ...}` reaches Claude as a `gap` field the tool
   description tells it to quote.

---

## 3. The target layout

### 3.1 `fpl_edge/mcp/`

```
fpl_edge/mcp/
  __init__.py       empty, so `import fpl_edge.mcp` costs nothing
  __main__.py       `main()`: build the server, run stdio, exit non-zero on a bad config
  server.py         the one FastMCP instance, plus the explicit tool-module imports
  context.py        owner_context() at startup, the db path, the UserContext handed to panels
  adapter.py        run_script -> the envelope: result, provenance, gap, budget. No tool bypasses it
  render.py         the pure helpers chat_core.py holds today: row rendering caps, $param binding, the analysis budget
  prompts.py        one prompt, `fpl_tool_guidance`, derived from what registered
  tools/
    __init__.py     empty
    squad.py        my_squad, brief
    fixtures.py     fixtures, fixture, club_form
    projections.py  projections
    dossier.py      player, player_form, player_profile, player_radar, player_intel
    ownership.py    ownership
    creators.py     creator_consensus, creator_record, player_claims, episodes, episode
    manager.py      manager
    pipelines.py    pipelines, pipeline_log
    ideas.py        submit_idea, mark_idea_acted, ideas, idea_review
    watchlist.py    watchlist_add, watchlist_list, watchlist_remove
    analysis.py     query, run_analysis, list_analyses, save_analysis, python_viz
    solve.py        transfer_plan, solve_start, solve_status
```

`manager.py` is split out of `ownership.py` because the identity rules it
carries (verified sources only, the twenty stale seeds refused by name) are
340 lines of their own and have their own test. `render.py` keeps `chat_core.py`
under a name that says what it does; its four helpers are pure and the contract
test imports them without constructing a server.

### 3.2 Every tool, and what it adapts

35 tools. `panel` names the registered script it runs through `run_script`.

| Module | Tool | Panel or store | Replaces |
|---|---|---|---|
| squad.py | `my_squad` | panel `squad_overview` | 29 |
| squad.py | `brief` | panel `dashboard_brief` | 10 |
| fixtures.py | `fixtures` | panel `fixture_board` | 5 |
| fixtures.py | `fixture` | panel `fixture_detail` | new, the web has it and Claude did not |
| fixtures.py | `club_form` | panel `fixture_board`, form block | 30 |
| projections.py | `projections` | panel `projection_table`, modes `xpts`, `spread`, `player` | 1, 2 |
| dossier.py | `player` | NEW panel `player_dossier` | 13 |
| dossier.py | `player_form` | NEW panel `player_form` | 4 |
| dossier.py | `player_profile` | panel `player_profile`, plus the ingest fetch | 34 |
| dossier.py | `player_radar` | panel `player_radar` | new, the drawer has it and Claude did not |
| dossier.py | `player_intel` | NEW panel `player_intel` | 14, 15 |
| ownership.py | `ownership` | panel `ownership_eo` | 6 |
| creators.py | `creator_consensus` | panel `creator_board` | 16 |
| creators.py | `creator_record` | panel `creator_report_card` | 17 |
| creators.py | `player_claims` | panel `player_chatter` | 18 |
| creators.py | `episodes` | panel `creator_episodes` | 36, in part |
| creators.py | `episode` | panel `episode_summary` | 35, 36 |
| manager.py | `manager` | NEW panel `manager_lookup` | 28, 32, 33 |
| pipelines.py | `pipelines` | panel `pipeline_board` | 12, 19 |
| pipelines.py | `pipeline_log` | panel `pipeline_run_log` | new |
| ideas.py | `ideas` | panel `idea_registry` | new |
| ideas.py | `idea_review` | NEW panel `idea_review` | 8 |
| ideas.py | `submit_idea` | store `interfaces/inbox.py` | 7 |
| ideas.py | `mark_idea_acted` | store `interfaces/store.py` | 11 |
| watchlist.py | `watchlist_add` / `_list` / `_remove` | store `interfaces/watchlist.py` | 25, 26, 27 |
| analysis.py | `query` | `platform/query.py guarded_query` | 20 |
| analysis.py | `run_analysis` / `list_analyses` / `save_analysis` | `interfaces/analyses.py` plus `guarded_query` | 22, 23, 24 |
| analysis.py | `python_viz` | the sandbox, datasets from `guarded_query` | 37 |
| solve.py | `transfer_plan` | panel `planner_grid` plus the committed plan | 21, read half |
| solve.py | `solve_start` / `solve_status` | `platform/solve_runner.py` | 21, compute half |

Six of them (`fixture`, `player_radar`, `pipeline_log`, `ideas`, `episodes`,
`episode`) are new capability: panels the browser already renders that Claude
could not reach. Nine tools the chat agent offers today disappear by name, and
section 6 says what that costs.

### 3.3 Entry points

`pyproject.toml` gains one line under `[project.scripts]`, beside `fpl`:

```toml
[project.scripts]
fpl = "fpl_edge.cli.main:app"
fpl-mcp = "fpl_edge.mcp.__main__:main"
```

`[tool.hatch.build.targets.wheel] packages = ["fpl_edge"]` at pyproject.toml:61
already covers the new location. It does not cover `fpl_mcp/`, so any
non-editable build of this repo today ships the engine without a toolbelt; the
move fixes that as a side effect.

Both of these work:

```bash
uv run --project /Users/nripeshpradhan/Documents/Github/i-test-season python -m fpl_edge.mcp
uv run --project /Users/nripeshpradhan/Documents/Github/i-test-season fpl-mcp
```

The Claude desktop entry the owner pastes into
`~/Library/Application Support/Claude/claude_desktop_config.json`, replacing the
`fpl-server` block that points at `/Users/nripeshpradhan/Documents/Github/FPL-MCP/main.py`:

```json
{
  "mcpServers": {
    "fpl-server": {
      "command": "/Users/nripeshpradhan/Documents/Github/i-test-season/.venv/bin/python",
      "args": ["-m", "fpl_edge.mcp"],
      "env": {
        "FPL_EDGE_HOME": "/Users/nripeshpradhan/Documents/Github/i-test-season"
      }
    }
  }
}
```

Three details that are load-bearing:

* The server name stays `fpl-server`. `chat_agent.allowed_tools` builds
  `mcp__fpl-server__{name}` at chat_agent.py:695, and the owner's saved Claude
  desktop conversations refer to those qualified names.
* The command is the project venv's interpreter, not `uv`. Claude desktop
  launches the process with an arbitrary working directory and a minimal
  environment; `uv run` would have to resolve a project from that directory.
  The venv python has `fpl_edge` installed editable
  (`.venv/lib/python3.11/site-packages/_editable_impl_fpl_edge.pth`), so
  `-m fpl_edge.mcp` resolves from any directory.
* `FPL_EDGE_HOME` is the only variable set, and it is optional: `context.py`
  defaults to the checkout root. No `ANTHROPIC_*`, no FPL token, no
  `PYTHONPATH`.

### 3.4 Deleted outright

| Path | Lines | Why |
|---|---|---|
| `fpl_mcp/fpl_token.py` | 156 | A Playwright browser driver that captures the owner's FPL bearer token. Nothing imports it, `server.py` does not register it, and `playwright` is not a declared dependency, so the module cannot import in this environment. The owner-facing path is `fpl myteam auth` (`fpl_edge/myteam/cli.py:533`), the cookie paste flow the shared brief rule 7 reserves for the owner |
| `fpl_mcp/utils/fpl_data.py` | 502 | The live FPL API layer: `_live_json`, `get_bootstrap_data`, `entry_json`, the `data/cache/fpl_mcp/` cache and the 200-request budget. Its warehouse-shaped helpers are replaced by panels; its live reads are what rule 4 forbids |
| `fpl_mcp/utils/video_transcript.py` | 192 | Fetches YouTube captions. Deleted with tools 35 and 36; `fpl_edge/ingest/content/youtube.py` keeps the policy and the pipeline keeps the fetching |
| `fpl_mcp/tools/video_tools.py` | 311 | A keyword-frequency summariser, superseded by the stored, quoted, timestamped `episode_summary` panel |
| `fpl_mcp/tools/transcript_tools.py` | 70 | Tool 35 |
| `fpl_mcp/tools/general_tools.py` | 60 | Tool 30, merged |
| `fpl_mcp/tools/team_tools.py` | 219 | Tool 29 becomes a 30-line adapter over `squad_overview` |
| `fpl_mcp/tools/expert_tools.py` | 638 | Tools 31, 32, 33. The identity resolution (lines 136 to 304) moves into the `manager_lookup` panel rather than being deleted with them |
| `fpl_mcp/README.md` | 101 | Describes `python main.py`, a vendored `mcp_sdk/` and a folder layout that does not exist |
| **Total** | **2,249** | of which 2,148 are Python |

Four more deletions are duplication rather than whole files. `_engine_home`,
`_db_path`, `_unavailable` and `_now` are copied in `edge_tools.py:44-129`,
`dossier_tools.py:55-116`, `content_tools.py:76-140` and
`utils/fpl_data.py:84-110`, roughly 200 lines of four slightly different
answers to one question. They collapse into `context.py`.

No dependency leaves `pyproject.toml`: `playwright` was never declared, and
`mcp[cli]>=1.2,<2` at pyproject.toml:32 stays, because the FastMCP to MCPServer
rename in v2 is its own change.

Deleting `fixture_difficulty` (tool 5) as a parquet reader leaves
`fixture_difficulty.parquet` with exactly one reader,
`fixture_board`'s per-cell `legacy_difficulty`, which the orchestrator has
decided stays. The comment naming the MCP tool as the second reader
(`platform/scripts/fixtures/constants.py:20` and `ratings.py:312`) is corrected
in the same step.

Estimated size of the new package: about 1,400 lines, against 5,835. The
reduction comes from the adapters, which are 20 to 40 lines each because the
panel already did the work, and from the four collapsed copies of the engine
locator.

---

## 4. Migration order for M2

Two constraints set the order.

`fpl_edge/platform/chat_agent.py` imports the toolbelt in process at
chat_agent.py:345 and chat_agent.py:359, and serves the FastMCP instance to the
CLI over an in-memory transport (chat_agent.py:758-764). The chat breaks the
moment those names stop resolving. Agent D2 is editing the same file for
per-user `CHAT_ROOT` and assets. So the new package is built beside the old one
and nothing in `chat_agent.py` moves until one late step, after D2 merges.

Every step is a commit the orchestrator gates, and the gate is the real pytest
exit plus an unchanged set of the 11 known `tests/audit/` failures.

| Step | Change | Test | Chat still works |
|---|---|---|---|
| M2.1 | Register panel `player_form` | new `tests/unit/test_player_form_panel.py`: PIT filter, season spanning, empty reason | yes, no MCP change |
| M2.2 | Register panel `player_dossier`, wrapping `interfaces/dossier/` with `simulate` removed | new test: every `EXPECTED` section present with a body or a gap, result schema validates | yes |
| M2.3 | Register panel `player_intel`, items and set-piece changes | new test: `published_at` after `as_of` is invisible, the empty case names the filter | yes |
| M2.4 | Register panel `manager_lookup` | new test, carrying the two load-bearing assertions from `test_mcp_team_and_expert_tools.py`: no stale seed name reaches an answer, and no name-to-id map exists in the module | yes |
| M2.5 | Register panel `idea_review` | new test: the bias probes and their caveats survive, the zero-ideas case | yes |
| M2.6 | Add `fpl_edge/interfaces/analyses.py` (save, load, list_all, git commit) | move the relevant half of `test_chat_tools_contract.py`; add a name-validation case | yes |
| M2.7 | Create `fpl_edge/mcp/` with `__init__`, `server`, `context`, `adapter`, `render`, `__main__` and an empty `tools/`. Zero tools registered | `python -m fpl_edge.mcp` starts and lists zero tools; a new import-direction test asserts no module under `fpl_edge/` imports `fpl_edge.mcp` at module scope | yes |
| M2.8 | `tools/analysis.py`: query, run_analysis, list_analyses, save_analysis, python_viz | copy `test_python_viz.py` to the new path and keep both green; `test_fpl_theme_isolation.py` gains the second path string | yes, `fpl_mcp` still serves it |
| M2.9 | `tools/squad.py`, `tools/fixtures.py`, `tools/projections.py` | one registration and one faked-`run_script` test per module | yes |
| M2.10 | `tools/dossier.py`, `tools/ownership.py`, `tools/creators.py` | same | yes |
| M2.11 | `tools/manager.py`, `tools/pipelines.py`, `tools/ideas.py`, `tools/watchlist.py`, `tools/solve.py` | same, plus the write-tool enumeration test for rule 3 | yes |
| M2.12 | `prompts.py`, and the parity gate | a test asserting the registered names equal the 26 in section 3.2, and that every read tool's panel name resolves in `registry.registered()` | yes |
| M2.13 | `pyproject.toml`: the `fpl-mcp` console script | `uv run fpl-mcp --list-tools` exits 0 and prints 26 names | yes |
| M2.14 | **The switch, one commit, only after D2 has merged.** chat_agent.py:86, :89, :345, :359 repoint to `fpl_edge.mcp`; `INTENT_TOOLS` and `TOOL_FAMILIES` are rewritten against the 26 names | `test_chat_agent_*`, plus a test that `families_prompt` names no tool the server does not register | yes, and the allowlist is now exact |
| M2.15 | Delete `fpl_mcp/`, repoint the remaining tests | see below | yes |
| M2.16 | Replace `fpl_mcp/README.md` with the desktop-config section of this document, and correct the two comments in `platform/scripts/fixtures/` | doc only | yes |

`INTENT_TOOLS` carries one name no tool has ever registered,
`get_player_history` at chat_agent.py:126, which also appears in the `players`
family at chat_agent.py:173. It allows nothing, because `allowed_tools`
intersects intent with reality at chat_agent.py:692, and it advertises a
capability in the system prompt that does not exist. M2.14 removes it.

### The eight tests that import `fpl_mcp`

| Test | Today | After M2.15 |
|---|---|---|
| `tests/unit/test_python_viz.py` | imports `fpl_mcp.tools.viz_tools` and `fpl_mcp.tools.chat_tools` | repointed to `fpl_edge.mcp.tools.analysis`. Every assertion survives unchanged: the sandbox, the theme copy, the network fence and the truncation refusal are the same code |
| `tests/unit/test_mcp_fpl_data.py` | pins `fpl_mcp/utils/fpl_data.py`: the cache location, the warehouse-first lookups, the absence of a second fetch stack | deleted with the module. The defect it guards against is replaced by a stronger, structural test in M2.7: no module under `fpl_edge/mcp/` may import `httpx`, `requests`, `fpl_edge.ingest.http` or `fpl_edge.ingest.rivals.client`. A rule enforced by the import graph cannot regress the way a cache path can |
| `tests/unit/test_mcp_team_and_expert_tools.py` | pins tools 29, 32, 33: bootstrap lookups, unknown element ids, missing multipliers, and the twenty stale seed names | split at M2.4. The identity assertions (`test_no_stale_seed_name_can_reach_an_answer`, `test_expert_tools_ships_no_name_to_id_map`) move to the `manager_lookup` panel test and must stay green there. The bootstrap-lookup assertions are deleted with `fpl_data.py`, because `squad_overview` resolves players from `dim_player` and cannot receive an unknown element id |
| `tests/unit/test_fpl_theme_isolation.py` | T6, always green, and names `fpl_mcp/tools/viz_tools.py:159` as a path string | the path string becomes `fpl_edge/mcp/tools/analysis.py`. The A3 handoff's warning applies: a mechanical deletion scan would miss this file, because `fpl_theme.py` is reached by assembled path rather than by dotted import |
| `tests/unit/test_chat_tools_contract.py` | imports `fpl_mcp.tools.chat_core` | repointed to `fpl_edge.mcp.render`, with the analyses half moved to M2.6 |
| `tests/unit/test_content_weights_pit.py` | imports `content_tools._scores_as_of`, the point-in-time creator weighting | repointed to the `creator_report_card` panel, which `platform/scripts/creators/__init__.py:40` already names as the home of that logic |
| `tests/unit/test_player_profile_tool.py` | imports `fpl_mcp.server` and `profile_tools` | repointed to `fpl_edge.mcp.tools.dossier` |
| `tests/unit/test_mcp_video_transcript.py` | pins `video_transcript.py`, `transcript_tools` and `video_tools` | deleted with them. Before deleting, M2 checks that the off-panel refusal is pinned against `fpl_edge/ingest/content/youtube.py` itself. Four other tests touch that module (`test_content_relevance_gate`, `test_transcription_spends_nothing`, `test_transcribe_metal_gate`, `test_content_asr`); if none asserts `OffPanelRefused`, M2 adds that assertion to the engine's own test before removing this file |

---

## 5. The tool contract

### 5.1 Input

One JSON schema style for every tool: the panel's own `params_schema`, minus
what the server supplies.

```json
{
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "season": {"type": "string", "default": "2026-27", "minLength": 4},
    "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38, "default": null},
    "as_of": {"type": ["string", "null"], "default": null}
  }
}
```

Three rules on top of the panel's schema:

* `entry_id` never appears. The panels that take it (`squad_overview`,
  `dashboard_brief`, `planner_grid`) get it from `UserContext`, per rule 5 and
  the MULTI_USER migration, and an MCP caller cannot select a different one.
* A tool that takes a player takes `player` as free text, resolves it through
  the one strict resolver, and returns the candidate list rather than guessing.
  Resolution happens in the adapter, before the panel run, so an ambiguity
  costs a round trip and not a panel budget. The panel itself takes `code`,
  the stable cross-season `PlayerCode`, never `element_id`.
* `as_of` is an ISO-8601 instant that must carry a timezone. A naive value is
  rejected with the reason, matching `edge_tools.py:116-129`.

### 5.2 Output, and the provenance envelope

Every tool returns the same object. `result` is the panel's payload verbatim.

```json
{
  "ok": true,
  "tool": "fixtures",
  "panel": "fixture_board",
  "result": {},
  "provenance": {
    "script": "fixture_board",
    "repo_sha": "2134bdf...",
    "generated_at": "2026-09-18T09:14:02.331000+00:00",
    "as_of": "2026-09-18T09:14:02+00:00",
    "params": {}
  },
  "budget": {"duration_ms": 412, "performance": "ok", "notes": []},
  "gap": null
}
```

`provenance` is `ScriptRun.provenance` unchanged (registry.py:357-365), so
`repo_sha` and `generated_at` are identical to the ones the browser receives
for the same run. `as_of` is present when the panel's result carries one.

The write tools return the same envelope with `panel: null`, a `store` field
naming the module that performed the write, and `result` holding whatever the
store returned (an item id, an idea id, a commit sha).

### 5.3 A panel gap

`run_script` admits `{empty: true, reason: "..."}` for every script
(registry.py:144-152), and `fpl_edge/interfaces/dossier/` reports per-section
gaps inside a populated result. Both reach Claude as a `gap`:

```json
{
  "ok": true,
  "tool": "player_form",
  "panel": "player_form",
  "result": {"empty": true, "reason": "no settled gameweeks for Semenyo in 2026-27 at this as_of; form rows appear only after a gameweek's points are finalised."},
  "gap": {
    "kind": "empty",
    "reason": "no settled gameweeks for Semenyo in 2026-27 at this as_of; form rows appear only after a gameweek's points are finalised.",
    "say": "Report this as not available and quote the reason. Do not substitute another source, another season or an estimate."
  }
}
```

`kind` is `empty` for a whole-panel gap and `sections` for a result whose
`gaps` array is non-empty, in which case `reason` lists the named sections. The
`say` field is constant text, present so the instruction travels with the data
rather than living only in a tool docstring the model may have compacted away.
Each tool's docstring repeats it in one line, matching what
`dossier_tools.py:148-152` already tells the model.

An exception from the panel is a different thing and stays a different thing.
`run_script` re-raises unless the warehouse is unseeded (registry.py:331-352),
so the envelope comes back as `{"ok": false, "error": {...}}` with the
exception type and message, and Claude is told to report a failure rather than
a gap. The distinction the web draws between "no data" and "could not load"
is the same distinction here, from the same code.

### 5.4 Budget

The registry's soft budget is 10 s (`BUDGET_S`, registry.py:53). Over it, the
run still returns, marked `performance: "over_budget"` with a note
(registry.py:367-374).

* A tool call is one `run_script`. Two panel runs in one tool is a defect, and
  the adapter enforces it by being the only path to `run_script`.
* Work that is known to exceed the budget is refused before it starts, with the
  reason and the route that does it asynchronously. That is why `simulate=True`
  leaves `player_dossier` (about 95 s, it refits the minutes model) and why
  `suggest_transfers` becomes `solve_start` plus `solve_status` plus
  `transfer_plan` over `planner_grid`. Its solve took one to five minutes inside
  a single tool call.
* A run that overruns unexpectedly returns its payload with
  `budget.performance = "over_budget"` and the registry's own note, rather than
  being converted into a refusal.

That last bullet is a deliberate divergence from the brief's wording, which
asked for a refusal past 10 s. The reason is rule 1. If the MCP server refuses
what the browser renders, the two surfaces disagree at exactly the moment the
warehouse is slow, which is the deadline. `registry.py:31-35` made the same
call for the same reason. The orchestrator should overrule this if the
divergence matters more than the agreement; the code change is one branch in
`adapter.py`.

---

## 6. Risks

### 6.1 What the chat agent loses

Nine of the 29 real names in `INTENT_TOOLS` stop existing. Seven are covered by
a replacement the agent can reach in the same turn, and two are real losses.

| Name removed | What the agent does instead | Cost |
|---|---|---|
| `xpts_aggregate` | writes a GROUP BY over `sem_projection_consensus` through `query` | one extra reasoning step, and the agent must get the grouping right. The old tool validated `group_by` against three keys |
| `projection_disagreement` | `projections(mode="spread")` | none, same rows |
| `get_team_summary` | `club_form` | none |
| `get_manager_history`, `get_expert_transfers` | `manager` | the warehouse holds only what the crawl has fetched. For a manager the crawl has never read, the live API answered and the warehouse will not. This is a real loss for long-tail entry ids, and the tool says so by name rather than returning silence |
| `get_expert_teams_summary` | `ownership` with the elite cohort | the panel reports the crawl's cohort, which is a larger and better-defined set than the curated handful the old tool fetched live, but it is as of the last crawl rather than as of now |
| `summarise_fpl_youtube`, `fetch_youtube_transcript` | `episode` over `episode_summary` | a video the corpus has not ingested cannot be summarised in chat at all. The paste-a-link flow (`POST /api/ingest/link`) is the route, and it halts at a human preview by design. **This is the second real loss** |
| `suggest_transfers` | `solve_start`, then `solve_status`, then `transfer_plan` | three turns instead of one, and the answer arrives after the solve rather than inside the call. The numbers are the same numbers, and they are now the same numbers the Planner tab draws |

The agent also gains six panels it could not reach: `fixture_detail`,
`player_radar`, `pipeline_run_log`, `idea_registry`, `creator_episodes` and
`episode_summary`.

### 6.2 What the owner loses in Claude desktop

The owner's desktop `fpl-server` currently launches
`/Users/nripeshpradhan/Documents/Github/FPL-MCP/main.py`, last committed
2026-08-24, which registers 36 tools including a `make_chart` that this repo
deleted in favour of `python_viz` and excluding `player_profile` that this repo
added. Every number the owner has read in Claude desktop since 2026-08-27 came
from that stale checkout, not from this one.

Tools the owner calls in Claude desktop that go by name: `xpts_aggregate`,
`track_ideas`, `weekly_decision_report`, `engine_status`,
`fpl_content_sources`, `get_expert_teams_summary`, `get_manager_history`,
`get_expert_transfers`, `get_team_summary`, `summarise_fpl_youtube`,
`fetch_youtube_transcript`, `suggest_transfers`, `projection_disagreement`,
`set_piece_changes`, and `make_chart`, which exists only in the stale checkout.

Of those, four change the owner's habits rather than the data. `track_ideas`
becomes something the settlement chain does without being asked.
`weekly_decision_report` becomes `brief`, which reads `dashboard_brief` and so
prints the same alerts and tiles as the dashboard. `engine_status` becomes
`pipelines`, which answers the same question with the ledger behind it.
`make_chart` was already gone before the rewrite.

The migration cost is one paste of the JSON in section 3.3 and one restart of
Claude desktop. The owner does that themselves; no agent edits that file.

### 6.3 The import cycle

`docs/platform/ARCHITECTURE_REVIEW.md` Section 3 freezes the direction:

```
jobs -> pipelines -> platform -> interfaces -> ingest -> models/sim/opt/rank -> store, rules
```

`fpl_edge/mcp/` sits above `platform`, in the same tier as `jobs`: it imports
`platform.registry`, `platform.scripts`, `platform.query`, `platform.solve_runner`
and `interfaces.*`, and nothing below imports it.

One edge runs the other way. `platform/chat_agent.py` imports the toolbelt at
chat_agent.py:345 and chat_agent.py:359. After M2.14 those become
`from fpl_edge.mcp.server import mcp`, which is `platform -> mcp -> platform`.

It does not deadlock, because both imports are function-local, inside
`list_mcp_tools` and `toolbelt_instance`, and the docstring at
chat_agent.py:355-358 already gives the reason ("Importing here rather than at
module top keeps platform startup honest about where the ~1s toolbelt import is
spent"). The same shape exists today across the two top-level packages.

It is still a cycle at package granularity, and the 2026-08-27 fold-in commit
(5433f0b) chose the sibling layout specifically to avoid it: "a `fpl_edge/mcp/`
subpackage would have invited the reverse import." The owner's instruction for
this rewrite reverses that call, so the mitigation has to be structural rather
than a note:

* M2.7 adds a test that walks every module under `fpl_edge/` and fails on a
  module-scope import of `fpl_edge.mcp` from anywhere outside `fpl_edge/mcp/`.
  The two function-local imports in `chat_agent.py` are the allowlist, named in
  the test by file and function.
* `fpl_edge/mcp/__init__.py` stays empty, so `import fpl_edge.mcp` pulls in no
  tools and no panels. The cost of the cycle is paid only by
  `fpl_edge.mcp.server`, which is what `toolbelt_instance` imports.
* Section 3 of the review is updated with an `mcp/` entry in the same commit,
  so the frozen layout and the tree agree.

### 6.4 The biggest risk

`player_dossier` is the tool the chat agent and the owner both lean on hardest,
and it is the one with no panel behind it. Its builder,
`fpl_edge/interfaces/dossier/`, is 1,728 lines across three modules and returns
sixteen sections, each with a body or a named gap, assembled from the odds
tables, the intel store, the fitted ratings, the Monte Carlo projection and the
set-piece detector. Writing a `result_schema` that admits every shape those
sixteen builders produce, and that `validate_result` will not reject at the
deadline, is the hardest single item in this migration. A schema too tight
turns a working dossier into a `ResultInvalid`, which registry.py:79-84 makes
deliberately loud; a schema too loose gives up the property that makes a panel
worth adapting.

The mitigation is order. M2.2 lands the panel and its test before any MCP
module exists, so a schema failure surfaces against the panel alone, with the
chat agent still running on `fpl_mcp` and the owner still on the stale desktop
checkout. If the schema proves unwritable inside one step, the fallback is to
register `player_dossier` with the section array typed as
`{key, title, body, gap}` and the bodies as free-form objects, which keeps the
gap contract, keeps one implementation behind both surfaces, and gives up only
the per-section shape validation. That fallback is a decision for the
orchestrator, and it is written down here so it is not made silently at 2am.
