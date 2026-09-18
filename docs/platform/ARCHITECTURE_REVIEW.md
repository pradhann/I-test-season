# Adversarial review of `docs/platform/ARCHITECTURE.md`

Agent A2, 2026-09-17. Read-only pass over the working tree at `main`. Every
claim below carries a `file:line` from the code, not from A1's document. Where
A1 is right I say so once and move on.

A1's map is structurally sound: the package table, the cycle detection and the
line counts all reproduce. Three things in it are wrong in ways that would cost
A3 a day or a green suite.

1. **The `tests` column of Table 2 is systematically under-counted.** A1's
   scanner matched a module by its full dotted name (`fpl_edge.ingest.lineups`)
   or its slash path. The dominant import style in `tests/` is
   `from fpl_edge.ingest import lineups`, which matches neither. Four of the
   modules A1 calls untested have dedicated test files.
2. **Three of the five duplicate pairs are not duplicates.** D1, D2 and D4
   each pair modules that the code itself documents as deliberately different,
   and in two cases the "duplicate" imports and calls the thing it is said to
   reimplement.
3. **Two proposed splits do not cut at function boundaries as written.** The
   `odds.py` split claims overlapping line ranges for two new modules, and the
   `creators.py` split assigns one schema block to two new modules at once.

---

## Section 1: verdicts I overturn

Fourteen verdicts. Four more evidence corrections where the verdict stands are
in Section 2, check 8.

| module | A1 verdict | my verdict | evidence |
| --- | --- | --- | --- |
| `fpl_edge/models/field/share.py` | merge into `models/ownership/eo.py` | keep | `share.py:55` is `from fpl_edge.models.ownership.eo import effective_ownership as _eo_algebra` and `share.py:340` calls it. It adapts a field sample to the algebra; it does not reimplement it. `share.py:56` imports `fpl_edge.sim.squad`, and `sim/live.py:232` imports `models.ownership.model`, so the merge creates a new cycle `models.ownership -> sim -> models.ownership`. It also orphans `tests/unit/test_field_share.py:15`. |
| `fpl_edge/models/ownership/backtest.py` | merge into `models/ownership/evaluate.py` | keep | `evaluate.py:3-7` names `backtest.py` and states the difference: leave-one-season-out against strict walk-forward, with the reason the first is not the operator's question. Two protocols, not two copies. `tests/audit/test_walk_forward.py:56` imports `fpl_edge.models.<family>.evaluate` by computed name and requires a `walk_forward` attribute on it, so dropping LOSO code into `evaluate.py` puts non-temporal folds inside the module that audit reads. The shared `mae_pp` is one metric helper, not a merge case. |
| `fpl_edge/models/ownership/fit.py` | merge into `models/ownership/evaluate.py` | merge into `models/ownership/backtest.py` | `fit.py:22` is `from fpl_edge.models.ownership import backtest, panel`. It drives `backtest.py` and never touches `evaluate.py`. |
| `fpl_edge/ingest/rivals/elite.py` | merge into `ingest/rivals/crawl.py` | keep | It exports `ELITE_NAMED`, `EliteEntry` (`elite.py:68`) and `verify` (`elite.py:107`) outside the `collect`/`run`/`main` triple. Consumers: `interfaces/creators.py:138`, `interfaces/qa.py:515`, `tests/unit/test_rivals_elite.py:20`, `tests/unit/test_mcp_team_and_expert_tools.py:47`. The shared machinery is already factored out: `elite.py:56` imports `_season_and_deadlines, _write` from `crawl.py`. |
| `fpl_edge/ingest/rivals/panel_picks.py` | merge into `ingest/rivals/crawl.py` | keep | `panel_picks.py:14-15` states that the other three crawlers never read `panel_person`. It has eleven definitions the others have no counterpart for, including `select_entries` (`:111`), `_person_record` (`:145`), `_refusal` (`:169`) and `_collect_one` (`:241`). `tests/unit/test_panel_picks_crawl.py:25` imports it. The duplication the owner complained about is the task pair `post_gw.py:288` and `registry.py:634`, both of which run this same module. Delete the task, not the module. |
| `fpl_edge/ingest/rivals/top1k.py` | merge into `ingest/rivals/crawl.py` | keep | `top1k.py:100` `plan`, `:296` `SampleSizeUnavailable`, `:309` `_sampled_so_far` are a resumable-sampling planner with no counterpart in the other three. `tests/unit/test_field_top1k.py` imports it. `top1k.py:57` already imports the shared writer from `crawl.py`. |
| `fpl_edge/models/team_goals/ratings_cache.py` | merge into `platform/scripts/fixtures.py` | merge into the split-out `platform/scripts/fixtures/build.py`, and delete the `fixture_difficulty` step | A1's own Table 4 splits `build.py` out of `fixtures.py` precisely because the build job "a panel must never call" does not belong in a panel module, and brief rule 5 says a panel that writes an artefact is a bug. Merging a writer into the panel module is the opposite move. `ratings_cache` is invoked at `post_gw.py:227`; the artefact it writes is read back only by `fixtures.py:529 load_legacy_difficulty`. Deleting the step and that loader is the real fix; the merge is a way of keeping both. `tests/unit/test_ratings_cache.py` pins it and must move with it. |
| `fpl_edge/interfaces/registry.py` | rename to `interfaces/idea_store.py` | rename to `interfaces/store.py` | The house convention is already five `store.py` modules (`intel/`, `myteam/`, `theses/`, `ingest/content/`, `ingest/projections/`), which is the same kind of repeated-basename-with-one-meaning A1 accepts for `baselines.py`. `idea_store.py` would sit beside the existing `interfaces/ideas.py` as a near-synonym, and it leaves the exported class named `IdeaRegistry` (`cli/main.py:31`), so the module name and the thing it exports still disagree. |
| `fpl_edge/platform/inbox.py` | rename to `platform/outbox_view.py` | rename to `platform/deliveries.py` | It reads the `platform_delivery` table (`inbox.py:36`). `inbox.py:4` names `fpl_edge.jobs.outbox` as the owner of the table and the write path, so calling the reader `outbox_view` re-creates the same basename collision in the other direction. One importer, `platform/app.py:69`. |
| `fpl_edge/platform/scripts/creators.py` | split into 5 | split into 6, adding `creators/schema.py` | Lines 1129 to 1608 are one banner-delimited block of nineteen JSON Schema fragments. A1 assigns that whole range to `board.py` by line number while also listing `DETAIL_PARAMS` and `DETAIL_RESULT` under `detail.py` by name. Those two are at `creators.py:1565` and `:1583`, inside the range. As written the split is self-contradictory and would make `detail.py`, `chatter.py` and `report_card.py` all import their own schemas back out of `board.py`. |
| `fpl_edge/ingest/odds.py` | split into 5 | split into 6, adding `odds/freshness.py` | Two defects. First, A1 gives `prices.py` lines "133 to 376" and `devig.py` lines "168 to 296", and the second range is inside the first; the function lists are disjoint but the line ranges are not, so a range-driven cut does not compile. Second, `matching.py` is given both name matching (`fold_name:1302`, `match_player_names:1316`) and market staleness (`MarketFreshness:1425`, `odds_freshness:1464`, `freshness_summary:1522`), which is two responsibilities in one module by A1's own criterion. `_shin_from_q:201` and `_match_probs:311` are named in neither list and would be left behind. |
| `fpl_edge/platform/scripts/ownership.py` | split into 3 | split into 5 | A three-way split leaves `ownership_eo`, one 975-line function, whole inside `panel.py`, which is the thing that needed splitting. Its sixteen internal banners fall cleanly into a load phase, a descriptor phase and three tools. See check 5. |
| `fpl_edge/ingest/content/pipeline.py` | split into 4, `main` moving to `content/cli.py` | split into 4, `pipeline.py` staying as the `-m` entry point | `python -m fpl_edge.ingest.content.pipeline` is invoked from `Makefile:27`, `Makefile:28`, `registry.py:415`, `registry.py:501`, `registry.py:665`, `registry.py:670`, `post_gw.py:251` and `post_gw.py:253`. Keeping `pipeline.py` as a thin dispatcher that imports the three command modules changes zero call sites. Renaming it to `cli.py` changes eight, in three files plus the Makefile, for no gain. |
| `fpl_edge/interfaces/creators.py` | keep | keep the module, but extract the link ledger | `creators.py:626-904` is a self-contained warehouse ledger under its own banner ("the pasted-link ledger"). `ingest/content/store.py:505` imports `discarded_item_ids` out of it from inside the sanctioned read path, and that single import is one of the two edges that close cycle C2. The ledger is a warehouse table, not a user-facing surface, and it belongs beside `ingest/content/store.py`. |

---

## Section 2: the nine checks

### 1. `interfaces/creators.py` against `platform/scripts/creators.py`

**Distinct.** Forty-three definitions against seventy, and exactly one
shared name: `_norm`.

`interfaces/creators.py` is the conversational and ingest surface: creator
identity resolution (`verified_manager_index:95`, `link_creator_entries:120`,
`match_creators:210`), summarisation (`summarize_creator:268`), gameweek
attribution for pasted content (`resolve_gameweek:481`), the pasted-link ledger
(`626-904`) and the link ingest (`ingest_link:1051`). `platform/scripts/creators.py`
is four registered panel scripts and their schemas: `creator_board:1653`,
`creator_detail:2084`, `player_chatter:3114`, `creator_report_card:3739`,
registered at `3948-3990`. No panel function appears in the other file and no
ledger function appears in the panel file.

Three things below the public surface are worth A3 knowing, and none of them
makes the pair a duplicate.

- The shared `_norm` is a false friend. `interfaces/creators.py:51` is
  `re.sub(r"[^a-z0-9]", "", text.lower())`, an identity squash. `platform/scripts/creators.py:212`
  is NFKD accent folding to space-separated tokens, for matching an LLM quote
  against a punctuation-free auto-caption. Same name, different contract. Do
  not let a merge or a shared-helper pass collapse them.
- Video-id extraction exists three times. `platform/scripts/creators.py:144 youtube_id`
  is the authority, and `platform/link_jobs.py:35` says so in prose and imports
  it at `:444`, `:702`, `:1426` and `:1617`. `interfaces/creators.py:376 _YT_ID_RE`
  is a third, weaker copy: it matches `watch?v=`, `shorts/`, `live/` and
  `youtu.be/` with exactly eleven id characters, where `youtube_id` also handles
  `/embed/` and ids of six to twenty characters. Used at `interfaces/creators.py:1101`
  and `:1246`. This is the duplicate pair the detector missed (see check 6, move M1).
- Gameweek attribution exists three times. `ingest/content/claims.py:181 GwCalendar.next_after`
  is the original. `interfaces/creators.py:497` calls it. `platform/scripts/creators.py:902-939`
  reimplements it as `_gw_calendar` plus `_gw_after`, and its own docstring at
  `:903` says it is "the rule the INGESTER already uses" and names
  `ingest/content/claims.py::GwCalendar.next_after`. The panel copy exists
  because the ingest one is not importable from a panel without closing cycle
  C2; after C2 is broken it can be deleted.

### 2. `myteam/__main__.py`

**Not reachable by any documented or scripted invocation, and keep it anyway.**

Grep over the whole tree for `fpl_edge.myteam` as an execution target returns
two hits, both docstrings: `myteam/__main__.py:1` and `myteam/cli.py:3-4`. No
Makefile target (`Makefile` has no `myteam` line), no plist (the only two are
`com.fpledge.dag.plist` and `com.fpledge.postgw.plist`, targeting
`fpl_edge.jobs.deadline_dag` and `fpl_edge.jobs.post_gw`), nothing under
`scripts/`, nothing under `docs/`, no `[project.scripts]` entry beyond
`fpl = "fpl_edge.cli.main:app"` at `pyproject.toml:46`.

A1's stated reason for keeping it is wrong: it is not "a documented CLI entry
point", because the only documentation of it is itself. The real reason to keep
it is cheaper to state. `fpl myteam ...` reaches the same Typer app through
`cli/main.py:36` and `cli/main.py:49`, but `cli/main.py:29-37,53` imports
`interfaces.bias`, `interfaces.ideas`, `interfaces.inbox`, `interfaces.registry`,
`interfaces.report`, `interfaces.tracking` and `theses.cli` at module level.
`python -m fpl_edge.myteam` imports only `myteam.cli`. Eleven lines that buy a
squad CLI which still works when the interfaces closure is broken is not worth
a refactor step in either direction. Keep, and leave the docstrings alone.

### 3. Path-assembled and string-assembled module references

`fpl_mcp/tools/viz_tools.py:36` is confirmed verbatim:

```
_THEME_SRC = Path(__file__).resolve().parents[2] / "fpl_edge" / "platform" / "fpl_theme.py"
```

and `viz_tools.py:159` is `shutil.copy(_THEME_SRC, sandbox / "fpl_theme.py")`.
A1 is right that `platform/fpl_theme.py` is load-bearing and invisible to a
dotted-name scan.

The full sweep for other references of this shape found six more. Each row says
whether the module is reached **only** that way.

| reference | module reached | sole path | trap |
| --- | --- | --- | --- |
| `fpl_mcp/tools/viz_tools.py:36`, copied at `:159` | `fpl_edge/platform/fpl_theme.py` | yes | Delete-candidate trap. Also: the file is copied into a `python -I` sandbox and imported as top-level `fpl_theme`, so it must keep zero `fpl_edge` imports or the sandbox import fails. |
| `tests/unit/test_creator_panel.py:1693` | `fpl_edge/platform/scripts/creators.py` | no | Split trap, not a delete trap. The test builds the path and runs `ast.parse(path.read_text())` (`:1701`) to enforce the no-em-dash rule over every runtime string constant. Turning `creators.py` into `creators/` makes that path a directory and the test errors. A3 must update this line in the same diff as the split, and widen it to all six new files or the prose guard silently stops covering five of them. |
| `tests/audit/test_walk_forward.py:56` | `fpl_edge/models/<family>/evaluate.py`, computed name | no | Rename trap. Any `models/*/evaluate.py` is reached by `importlib.import_module(f"fpl_edge.models.{family}.evaluate")` over `pkgutil.iter_modules` of `fpl_edge.models` (`:52-54`). Renaming or merging `models/ownership/evaluate.py` changes what this audit sees. |
| `tests/audit/test_walk_forward.py:202` and `tests/audit/test_recency_chasing.py:240` | every module under `fpl_edge` | no | Both do `importlib.import_module(mod_info.name)` over `pkgutil.walk_packages(fpl_edge.__path__, "fpl_edge.")`. Every module in the tree is imported by the audit suite, inside a bare `except Exception: continue`. A module that stops importing after a refactor does not fail these tests, it silently drops out of discovery. This is why the suite gate cannot be the only check on a split. |
| `fpl_edge/jobs/deadline_dag.py:971` with `:550` | `fpl_edge/ingest/lineups.py` | no (it is a literal) | Rename trap with a silent failure mode. `deadline_dag.py:971` is `module = "fpl_edge.ingest.lineups"` and `:550` is `importlib.util.find_spec(dotted)`. If the module is renamed or moved, `_module_exists` returns False and the step returns `Step(ok=True, detail="skipped: no lineups module in this build yet")` at `:973-974`. The T-90m lineup and captain check stops running and the digest reports success. |
| `tests/unit/test_odds_rho_verdict.py:143` | `scripts/backtest_clean_sheets.py` | yes | Outside `fpl_edge`, noted for completeness. `spec_from_file_location` by path because `scripts/` is not a package. |
| `tests/audit/conftest.py:46` | `scripts/audit_leakage.py` | yes | Same shape. `scripts/audit_leakage.py:428` then scans the `fpl_edge` tree by package name. |

One-line answer: **`fpl_edge/platform/fpl_theme.py` is the only module in
`fpl_edge/` reached solely by an assembled path, and A1's line reference for it
is correct; the six other assembled references are rename traps rather than
delete traps, and the worst of them, `deadline_dag.py:971`, fails silently.**

### 4. The three deletes

| module | zero importers incl. strings | test fixture or `docs/models/*.md` on its output | verdict |
| --- | --- | --- | --- |
| `fpl_edge/models/ensemble/backtest.py` | Yes. Repo-wide grep for `ensemble.backtest` and `ensemble/backtest` outside `ARCHITECTURE.md` returns nothing. | No. It writes nothing: no `to_parquet`, `to_csv`, `open(` or `write` anywhere in the file. | delete, agreed |
| `fpl_edge/models/ensemble/frame.py` | Yes, same grep, nothing. | No writes. Its schema (`COLUMNS`, `OPTIONAL`, `XP_BOUNDS`) is the live projection schema, but the live path carries its own copy: `ingest/projections/github_csv.py:500`, `premierinjuries.py:319`, `fplform.py:65` all produce `xp`/`xp_if_appears`/`p_appear` without importing `frame.validate`. `ProjectionFrameError` and `XP_BOUNDS` appear nowhere else in `fpl_edge/` or `tests/`. | delete, agreed |
| `fpl_edge/models/ensemble/weights.py` | Only importer is `ensemble/backtest.py:42`. The four other grep hits for "ensemble weights" are prose in `ingest/projections/migrations/001_projections.sql:1`, `ingest/projections/fpl_ep.py:26`, `eval/projection_scoring.py:2` and `store/views.sql:129`; the `sem_projection_weights` view is fed by `eval/projection_scoring.py`, not by this module. | No writes. `weights.py:5` cites `docs/projections.md`, which does not exist. | delete, agreed |

Two things A1 should have said.

First, the verdicts are inconsistent with A1's own treatment of the other
research package. `models/ensemble/__init__.py:4-7` names "sources, weights and
the blend frame" as the declared Phase 2.5 raw material, in the same way
`models/copying/__init__.py` names `attribution.py`, `minileague.py` and
`template.py`, which A1 keeps with the words "research branch, kept because the
package is a coherent unit". Same evidence, opposite verdict. I still recommend
the deletes, because unlike `models/copying` the ensemble modules are
contradicted by their own package docstring (see below) and their scoring job
has a live replacement in `eval/projection_scoring.py`. But A3 should apply one
rule, and if the orchestrator prefers the conservative rule then all three stay
and `models/copying` stays with them.

Second, `models/ensemble/__init__.py:3` claims the package is "wholly outside
the production import closure". That is false: `platform/scripts/fixtures.py:745`
imports `models.ensemble.sources.odds_with_fixture_keys`, and that is the
fixtures panel, which is production. The docstring must be corrected in the same
diff or A3 leaves a lie in the tree.

### 5. The ten modules over 1,500 lines

A1's three headline function sizes reproduce exactly: `dashboard_brief` is 1,725
lines (`brief.py:857-2581`), `ownership_eo` is 975 (`ownership.py:1186-2160`),
`create_app` is 908 (`app.py:173-1080`).

| module | coherent responsibilities | cuts at function boundaries | giant function decomposed |
| --- | --- | --- | --- |
| `platform/scripts/creators.py` | No. Five modules for six blocks. The schema block at `1129-1608` has no home. | No. `DETAIL_PARAMS:1565` and `DETAIL_RESULT:1583` are assigned to `board.py` by line range and to `detail.py` by name. | N/A, largest function is `_said` at 319 lines (`2782-3100`), which is fine. |
| `platform/scripts/brief.py` | Yes. | Almost. `_parse_ts:785`, `_ref:797` and `best_legal_xi:816` sit between A1's `schema.py` (ends 776) and `build.py` (starts 857) and are named only for `best_legal_xi`. Put all three in `tiles.py`. | No. A1 says only "has to be cut into per-block builders". Decomposition below. |
| `platform/scripts/fixtures.py` | Yes, and the `build.py` split is the right fix for the brief rule 5 violation the module carries today (`write_artefacts:2396`, `main:2431`, registered as a panel and invoked as `-m ... --build` at `registry.py:587`). | Yes, the schemas already sit adjacent to their panels (`BOARD_RESULT:855-1023`, `DETAIL_RESULT:1659-1686`). Unassigned: `_hours_since:168`, `_iso:183`, `_input_row:192` and the constants at `117-160`; they are shared and belong in a `fixtures/constants.py`. Note `SEASON_DEFAULT` is defined at both `fixtures.py:160` and `platform/scripts/common.py:21`. | N/A, largest is `fixture_board` at 397 lines. |
| `platform/scripts/ownership.py` | No. Three modules leaves the 975-line function intact. | Yes for the named symbols. | No. Decomposition below. |
| `ingest/odds.py` | No. `matching.py` carries name matching and market freshness. | No. `prices.py` "133 to 376" contains `devig.py` "168 to 296". `_shin_from_q:201`, `_match_probs:311`, `_kickoff_utc:481`, `_slugify:507`, `_clean_prices:591`, `_row:608`, `_derived_rows:616`, `_fpl_name_keys:1311`, `_name_rule:1378`, `_surname_initial:1393`, `_odds_api_selection:1251` are named in no list. Cut by function name, never by line range. | N/A, largest is `OddsApiClient` at 123 lines. |
| `ingest/content/pipeline.py` | Yes. | Yes. The write-retry helpers A1 names are real and shared. | Partly. `cmd_transcribe` is 382 lines (`936-1317`) and `cmd_analyze` 300 (`500-799`); A1 proposes no cut for either. Each is one command, so leaving them whole is defensible for this refactor. |
| `platform/link_jobs.py` | Yes. | Almost. `_table_exists:684`, `_ingest_once:943` and `_annotate:961` fall in A1's `take.py` range but are named in no list. `discard_item:978`, `restore_item:985` and `correct_gameweek:991` are three-line delegators to `interfaces/creators.py`; after move M2 they delegate to `ingest/content/link_ledger.py` instead. | N/A, largest is the `LinkJobs` class at 526 lines (`1172-1697`), which is one coherent state machine. |
| `interfaces/dossier.py` | Yes. | Yes. `_squash:238`, `_normalise_selections:897`, `_club_similarity:916`, `_odds_key_for:942`, `_match_selection:986`, `_table_exists:1149`, `_parse_as_of:1601` and `_unused_guard:1672` are unnamed; the first belongs in `render.py`, the middle five in `sections.py`, the last two in `render.py`. | N/A, largest is the `Dossier` class at 113 lines. |
| `platform/scripts/projections.py` | Yes. | Yes. | No. `_gw_mode` is 590 lines (`734-1323`) with no internal banners at all, the hardest function in the ten, and A1 says nothing. Decomposition below. |
| `platform/app.py` | Yes. | Yes. | No. Decomposition below. |

**`dashboard_brief` (`brief.py:857-2581`).** It already carries seventeen
banners. Give it a `BriefCtx` dataclass holding the fetched panels plus the
`alerts` and `tiles` accumulators, then sixteen `_block_*(ctx)` functions, one
per banner, each returning one schema fragment:

| new function | lines today |
| --- | --- |
| `_calendar` | 869-894 |
| `_source_panels` (keeps the nested `call:896` and `gap_alert:910`) | 895-918 |
| `_squad_checks` | 919-1072 |
| `_best_xi` | 1073-1140 |
| `_price_flow` | 1141-1231 |
| `_ownership_gates` | 1232-1329 |
| `_consensus_standouts` | 1330-1424 |
| `_fixture_turns` | 1425-1557 |
| `_squad_minutes` | 1558-1621 |
| `_moves` | 1622-1860 |
| `_creator_shift` | 1861-1874 |
| `_solve_block` | 1875-2158 |
| `_projection_provenance` | 2159-2168 |
| `_verdict` (with `_verdict_transfer:2211`, `_verdict_captain:2270`, `_verdict_bench:2383`, `_verdict_chip:2402`) | 2169-2422 |
| `_header_stats` | 2423-2437 |
| `_standing` | 2438-2512 |

`dashboard_brief` then becomes the assembler at `2513-2581`, roughly 70 lines.
Only after that is the file split into `schema.py`, `tiles.py`, `plan.py`,
`build.py` real.

**`ownership_eo` (`ownership.py:1186-2160`).** Sixteen banners, which group into
three phases. Five modules rather than A1's three:

| new module | what moves |
| --- | --- |
| `ownership/schema.py` | A1's list, unchanged: `_SEGMENT_META`, `PARAMS_SCHEMA`, `_MEASURE`, `_ROW`, `_FIELD`, `_SEGMENT`, `_DIFF_ROW`, `_WHATIF_PLAYER`, `RESULT_SCHEMA` (`187-735`). |
| `ownership/load.py` | `_eo_inventory` (`1226-1248`), `_eo_pivot` (`1249-1282`), `_consensus_xpts` (`1283-1302`), `_cohort_own_eo` (`1303-1373`), `_sub_cohorts` (`1374-1408`), `_squad_coverage` (`1409-1491`, keeps the nested `row:1419`), `_other_season_eo` (`1492-1539`). |
| `ownership/fields.py` | A1's list (`_EXTERNAL_META`, `_external_repeats`, `_cohort_composition`, `_segment_inventory`, `_segment_ownership`, `_selection_includes`, `877-1185`) plus `_notes` (`1540-1593`), `_field_ladder` (`1594-1717`, keeps `n_with:1602`), `_selectable_sets` (`1718-1860`), `_two_fields` (`2090-2128`), `_livefpl_instant` (`2129-2160`). |
| `ownership/tools.py` | `_tool_squad_diff` (`1861-1951`), `_tool_whatif` (`1952-2022`), `_tool_momentum` (`2023-2089`). |
| `ownership/panel.py` | `ownership_eo` as a roughly 120-line assembler. `_squad_state` (`774-874`) does **not** stay here: see move M4 in check 6. |

**`create_app` (`app.py:173-1080`).** Already banner-delimited by route group.
Seven `APIRouter` factories, each `def router(deps) -> APIRouter`:

| new module | routes, by line |
| --- | --- |
| `app/routes_core.py` | `/api/health:197`, `/api/deadline:207`, `/api/panels:231`, `_panel_error:243`, `/api/scripts/{name}/run:260`, `/api/query:283` |
| `app/routes_inbox.py` | `/api/inbox:305`, `/api/inbox/{id}/ack:312`, `/api/monitors:319`, `/api/monitors/{name}/run:323` |
| `app/routes_solve.py` | `/api/solve:341`, `/api/solve/status:364`, `/api/solve/plan:370`, `/api/solve/transfer-plan:374`, plus `_solve_plan:1240`, `_plan_codes`, `_transfer_plan:1329`, `_held_squad`, `_solve_diff_lines` |
| `app/routes_players.py` | the Understat profile fetch block, `380-433` |
| `app/routes_pipelines.py` | the pipeline trigger block, `434-613` |
| `app/routes_content.py` | content sources `614-815`, link paste `830-858`, the preview gate `859-903`, item discard/restore/gameweek `904-944` |
| `app/routes_chat.py` | briefing `816-829`, conversations and chat assets `945-1046` |

`app/factory.py` keeps the Pydantic request models (`90-172`), the seven
`include_router` calls, `routes_account.router` (`1047`), the middleware
(`1054`) and the index route (`1066`). `app/helpers.py` takes
`_deadline_calendar`, `_brief_thresholds`, `_monitor_definitions:1083`,
`_player_lookup` and `serve`.

**`_gw_mode` (`projections.py:734-1323`).** No banners, so the cut is by
statement group:

| new function | lines today |
| --- | --- |
| `_gw_inputs` | 751-840 (gw resolution, sources, consensus flag, coverage; keeps `coverage_text:791`) |
| `_gw_frame` | 842-1011 (the 145-line source branch at `842-986` plus the value column) |
| `_gw_aggregates` | 1016-1061 (keeps `agg:1016`; `by_team`, `by_position`, sorting) |
| `_gw_rows` | 1063-1099 |
| `_gw_meta` | 1101-1213 |
| `_gw_annotations` | 1215-1288 (prices, applied weights, weights block, provider accuracy) |
| `_gw_mode` | 1290-1323, the return dict only |

### 6. The two real cycles

Both are real, and A1's description of each is right as far as it goes. The
edges that close them are fewer than the cycle listing suggests.

**C1, `pipelines` and `platform` and `jobs`.** It is two loops, not one.

| loop | edges, with lines |
| --- | --- |
| A: `jobs` and `pipelines` | `pipelines/registry.py:52-53` imports `deadline_dag as dag` and `Step, TaskContext, TaskResult, run_step`; `pipelines/health.py:41` and `pipelines/runner.py:38` import from it too. Back: `jobs/deadline_dag.py:182` imports `pipelines.registry` lazily, with the comment at `:178` "lazy because registry imports this module for its building blocks", and `:1327-1328` do the same for `registry` and `runner`. |
| B: `pipelines` and `platform` | Closed by exactly one edge: `pipelines/registry.py:702`, `from fpl_edge.platform import briefing_intel`, inside the `briefing_intel` task body. The return path is `platform/briefing_intel.py:129 -> platform.scripts`, `platform/scripts/__init__.py:12 -> pipelines_panel`, `platform/scripts/pipelines_panel.py:32-33 -> pipelines, pipelines.health, pipelines.runner`. |

Loop B is the cheap one. `registry.py:702` is the only place in `pipelines/`
that reaches into `platform/`, and it is the odd one out: eight sibling tasks in
the same file already shell out (`registry.py:415, 501, 564, 587, 602, 634, 665,
670`). Replace the in-process call with
`run_step("briefing_intel", [ctx.python, "-m", "fpl_edge.platform.briefing_intel", ...])`
and add a `__main__` guard to `platform/briefing_intel.py`. That removes the
last `pipelines -> platform` edge; `pipelines_panel -> pipelines` then runs one
way only.

Loop A is not breakable by moving one function, because the dependency is real
in both directions: `registry.py` needs five task implementations out of
`deadline_dag` (`dag.presser_projection_refresh:770`, `dag.price_radar:778`,
`dag.final_solve_delivery:786`, `dag.lineup_captain_check:794`,
`dag.odds_refresh:802`) as well as `dag.SEASON:279`, `dag.LOOKBACK:245,280`,
`dag.Due:281,286,291` and `dag.STALE_WINDOWS:769,777,785,793,801`; and
`deadline_dag` needs `registry.stale_window_of` (`:182`) and
`registry.registry_due` (`:1327`). Invert it by pulling both halves down:

| step | move |
| --- | --- |
| A | New leaf `fpl_edge/pipelines/contracts.py`: `Step`, `TaskContext`, `TaskResult`, `run_step`, `SEASON`, `LOOKBACK`, `Due`, `STALE_WINDOWS`, moved out of `jobs/deadline_dag.py`. It imports `store` and stdlib only. |
| B | New module `fpl_edge/pipelines/tasks.py`: the five task bodies listed above, moved out of `jobs/deadline_dag.py`. It imports `pipelines.contracts` plus `ingest`/`myteam`/`opt`, never `pipelines.registry`. |
| C | `pipelines/registry.py`, `health.py`, `runner.py` import `pipelines.contracts` and `pipelines.tasks`. The `from fpl_edge.jobs import deadline_dag` lines at `registry.py:52-53`, `health.py:41` and `runner.py:38` all go. |
| D | `jobs/deadline_dag.py` imports `pipelines.contracts`, `pipelines.registry` and `pipelines.runner` at module level. The lazy imports at `:182`, `:1327` and `:1328` become ordinary ones and the comment at `:178` is deleted. |

Direction afterwards: `jobs -> pipelines.{registry,runner,health} -> pipelines.tasks -> pipelines.contracts -> store`. Acyclic. The
launchd target `fpl_edge.jobs.deadline_dag` in `com.fpledge.dag.plist` is
unchanged, which matters because the owner would otherwise have to reload a
plist.

**C2, `ingest.content` and `platform.scripts`.** The package-level cycle is
closed by exactly one edge, and the module-level SCC by two more.

| edge | line | what it imports |
| --- | --- | --- |
| `ingest.content -> platform.scripts` | `ingest/content/pipeline.py:1172` | `youtube_id` |
| `ingest.content.store -> interfaces.creators` | `ingest/content/store.py:505` | `discarded_item_ids` |
| `ingest.content.pipeline -> interfaces.creators` | `ingest/content/pipeline.py:1529` | `link_creator_entries` |

`pipeline.py:1172` is the only import of `fpl_edge.platform` anywhere under
`fpl_edge/ingest/` or `fpl_edge/interfaces/`. Four moves, each small:

| move | what | fixes |
| --- | --- | --- |
| M1 | New leaf `fpl_edge/ingest/content/urls.py` holding `youtube_id`, `canonical_key`, `deep_link` (`platform/scripts/creators.py:144-203`). Rewire `ingest/content/pipeline.py:1172`, `platform/link_jobs.py:444,702,1426,1617`, and `platform/scripts/creators.py`. Delete `interfaces/creators.py:376 _YT_ID_RE` and rewire its uses at `:1101` and `:1246`. | The `ingest.content -> platform.scripts` edge, and the three-way duplication in check 1. |
| M2 | New leaf `fpl_edge/ingest/content/link_ledger.py` holding `interfaces/creators.py:626-904` (`ensure_link_ledger`, `record_link_item`, `link_item_row`, `_public_ledger_row`, `UnknownLinkItem`, `_item_exists`, `_ensure_ledger_row`, `discard_item`, `restore_item`, `correct_gameweek`, `discarded_item_ids`, `drop_discarded`). Rewire `ingest/content/store.py:505`, `platform/link_jobs.py:978-995`. | The `interfaces.creators <-> ingest.content.store` two-cycle. |
| M3 | New leaf `fpl_edge/ingest/content/identity.py` holding `interfaces/creators.py:85-186` (`_person_shaped`, `verified_manager_index`, `link_creator_entries`). Rewire `ingest/content/pipeline.py:1529`, `interfaces/creators.py:138`, `interfaces/qa.py:515`. | The `ingest.content.pipeline -> interfaces.creators` edge. |
| M4 | Move `_squad_state` (`platform/scripts/ownership.py:774-874`) to `platform/scripts/common.py`. Rewire `platform/scripts/creators.py:1645`, which currently imports a private name out of a sibling panel. | Not required for C2, but it is the only panel-to-panel import in the package and it removes one edge from the SCC for free. |

After M1 to M3 the remaining direction is
`platform.scripts.ownership -> interfaces.qa -> interfaces.creators -> ingest.content.{loaders,pipeline,store}`,
with no edge back into `platform.scripts` or `interfaces` from `ingest.content`.
C2 is gone. The cycle-free target is stated in Section 3.

### 7. The seven merges and two renames

Neither proposed new name collides. `idea_store` and `outbox_view` appear
nowhere in `fpl_edge/`, `tests/`, `web/` or `docs/`. Both are still the wrong
names for other reasons, given in Section 1.

Merge targets, one row each:

| merge | right owner |
| --- | --- |
| `elite.py`, `panel_picks.py`, `top1k.py` into `crawl.py` | No, on all three. The shared part is already in `crawl.py` and imported by each (`elite.py:56`, `panel_picks.py:81`, `top1k.py:57`). The measured overlap is three CLI entry points with the same three verb names, not three copies of one body. The merge would produce a roughly 1,700-line module with a cohort flag, break three `-m` targets at `registry.py:634`, `post_gw.py:288` and `post_gw.py:310`, and orphan three test files. |
| `models/field/share.py` into `models/ownership/eo.py` | No. It merges the caller into the callee, and creates a cycle. |
| `models/ownership/backtest.py` into `evaluate.py` | No. Two evaluation protocols the target module's own docstring distinguishes at `evaluate.py:3-7`. |
| `models/ownership/fit.py` into `evaluate.py` | Wrong target. `fit.py:22` imports `backtest`. |
| `models/team_goals/ratings_cache.py` into `platform/scripts/fixtures.py` | No. It merges a writer into a panel, against brief rule 5 and against A1's own Table 4. |

Nothing in the seven survives unchanged, which is the single largest divergence
between this review and A1's map.

### 8. Untested code on the critical path

A1's three named modules are all tested. The `tests` column of Table 2 counts
only files that spell the full dotted name or the slash path; `from fpl_edge.ingest import lineups`
matches neither, and that is the dominant style in this suite.

| module | A1 "tests" | actual | file |
| --- | ---: | ---: | --- |
| `fpl_edge/ingest/lineups.py` | 0 | 1 | `tests/unit/test_lineups_ingest.py:19` |
| `fpl_edge/ingest/fpl_core_insights.py` | 0 | 1 | `tests/unit/test_fpl_core_insights.py:19` |
| `fpl_edge/ingest/projections/cli.py` | 0 | 1 | `tests/unit/test_projections_isolation.py:19` |
| `fpl_edge/models/ownership/evaluate.py` | 0 | 1 | `tests/unit/test_ownership_walk_forward.py:28` |
| `fpl_edge/ingest/rivals/panel_picks.py` | 0 | 1 | `tests/unit/test_panel_picks_crawl.py:25` |

The real gap is elsewhere, and it is the plan's largest risk: **five of the ten
modules A1 proposes to split have no test file at all.** Counted by AST import
resolution over `tests/`:

| split target | lines | test files |
| --- | ---: | ---: |
| `platform/scripts/creators.py` | 3994 | 3 |
| `platform/scripts/brief.py` | 2593 | 1 |
| `platform/scripts/fixtures.py` | 2463 | 1 |
| `platform/scripts/ownership.py` | 2172 | 1 |
| `ingest/odds.py` | 1966 | 9 |
| `ingest/content/pipeline.py` | 1808 | 6 |
| `platform/link_jobs.py` | 1697 | 2 |
| `interfaces/dossier.py` | 1674 | **0** |
| `platform/scripts/projections.py` | 1574 | **0** |
| `platform/app.py` | 1508 | 8 |

Plus, outside the ten, `fpl_edge/opt/milp.py` is 1,170 lines with zero test
files and sits on the solve path. A1 marks it keep, which is right, but it is
the largest untested module in the tree and it is not named anywhere
in the map. It is not being touched by this refactor, so it needs no test now.
Say so rather than leaving it unsaid.

The minimal pinning tests are in Section 5.

### 9. The two namespace packages

`fpl_edge/oracle/` and `fpl_edge/models/points/` have no `__init__.py`.

**`fpl_edge/oracle/`: adding `__init__.py` is safe.** Its two modules hold no
`ModelCard` and no `*Strategy` class, so neither audit discovery walk changes
behaviour when the directory becomes visible to `pkgutil`.

**`fpl_edge/models/points/`: adding `__init__.py` is not free, and A1's "one-line-each
fix, not a verdict" is wrong here.** `tests/audit/test_walk_forward.py:52-54`
builds the model-family set from
`pkgutil.iter_modules(fpl_edge.models.__path__)` filtered on `m.ispkg`.
CPython's `pkgutil._iter_file_finder_modules` skips a directory with no
`__init__` in it, so `points` is not in that set today. Adding the file adds it,
and `models/points/` has no `evaluate.py`, so `points` joins the `missing` list
at `:59`. That test already fails, because `copying`, `ensemble` and `field`
have no `evaluate.py` either, and it is one of the eleven baseline failures. So
the FAILED set does not change membership and brief rule 3 is not violated. The
assertion message changes from three families to four.

Two smaller consequences of making `models/points/` visible:
`tests/audit/test_walk_forward.py:202` and `tests/audit/test_recency_chasing.py:240`
begin importing its four modules during discovery. Nothing is exposed: the one
`ModelCard` in the package is an instance attribute built in
`DecomposedPointsModel.__post_init__` (`models/points/model.py:49-58`), not a
module-level object, so `_discover_cards` does not pick it up, and there is no
`*Strategy` name in the package.

Recommendation: add both files, and add `models/points/evaluate.py` with a
`walk_forward` in the same change, or accept that the audit message grows by one
family. Do not add `models/points/__init__.py` in the same diff as anything
else, so the baseline diff attributes cleanly.

Unrelated but adjacent: `platform/scripts/__init__.py:17-19` lists `__all__`
without `ownership` and `planner`, both of which are imported at `:13-14`. One
line, pre-existing.

---

## Section 3: the frozen target layout

Cycle-free. Every rename, merge, split and delete from Sections 1 and 2 is
resolved here. Packages with no change are given as a count.

```
fpl_edge/
  __init__.py, config.py, types.py                     (unchanged)
  cli/                                                 (unchanged, 4)
  eval/                                                (unchanged, 7)
  ingest/
    __init__.py, fpl_api.py, fpl_core_insights.py, http.py, lineups.py,
    odds_derived.py, odds_markets.py, player_mapping.py, results.py,
    understat.py, vaastav.py                           (unchanged, 11)
    odds/                                              (SPLIT of odds.py, 6)
      __init__.py          re-exports the current public surface
      prices.py            american_to_decimal, implied_prob, overround,
                           GoalRates, _match_probs, fit_goal_rates,
                           clean_sheet_probs
      devig.py             devig_multiplicative, devig_shin, _shin_from_q,
                           shin_z, devig_power, devig, devig_independent,
                           devig_anytime_scorer
      football_data.py     TextFetcher, fd_season_code, _kickoff_utc, _slugify,
                           natural_fixture_key, parse_football_data_csv,
                           _clean_prices, _row, _derived_rows,
                           ingest_football_data, ingest_football_data_fixtures
      odds_api.py          OddsApiError, CreditBudgetExceeded, OddsApiQuota,
                           CreditPlan, OddsApiFetcher, OddsApiClient,
                           resolve_team_name, parse_odds_api_events,
                           _odds_api_selection, ScorerIngestReport, _consensus,
                           _fixture_goal_rates, commence_utc, events_within,
                           OddsApiFetch, fetch_odds_api_gameweek,
                           land_odds_api_gameweek, ingest_odds_api_gameweek,
                           refresh_odds_api, _derive_live_rows
      matching.py          match_fixture_keys, NameMatch, fold_name,
                           _fpl_name_keys, match_player_names, _name_rule,
                           _surname_initial, squad_for_fixture
      freshness.py         MarketFreshness, odds_freshness, freshness_summary
    content/
      __init__.py, analyze.py, asr.py, calendar.py, claims.py, clubs.py,
      consensus.py, feeds.py, fetch.py, loaders.py, models.py, panel.py,
      resolve.py, scoring.py, source_state.py, sources.py, store.py,
      youtube.py                                       (unchanged, 18)
      urls.py              NEW leaf (move M1): youtube_id, canonical_key,
                           deep_link
      link_ledger.py       NEW leaf (move M2): the pasted-link ledger
      identity.py          NEW leaf (move M3): _person_shaped,
                           verified_manager_index, link_creator_entries
      pipeline.py          KEEPS its name and its argparse main, so every
                           `-m fpl_edge.ingest.content.pipeline` call site is
                           untouched; the eleven cmd_* bodies move out
      analyse_cmd.py       cmd_analyze, rank_candidates, relevance_score,
                           cmd_reextract, cmd_backfill_insights, _writer,
                           _write_with_retry, _is_contention
      transcribe_cmd.py    cmd_transcribe, _asr_fetcher, cmd_retention
      maintenance_cmd.py   cmd_repair_index, _index_is_healthy, _rebuild_table,
                           cmd_link_identities
    projections/                                       (unchanged, 11)
    rivals/                                            (unchanged except one split, 12)
      elite.py, panel_picks.py, top1k.py, crawl.py      ALL KEPT
      elite_list.py        SPLIT: the pinned tuple moves to
                           data/reference/elite_1000.json, `top(n)` stays
  intel/                                               (unchanged, 11)
  interfaces/
    __init__.py, bias.py, briefing.py, features.py, ideas.py, inbox.py,
    parsing.py, qa.py, render.py, report.py, squad_section.py, telegram.py,
    testing.py, tracking.py, verdict.py, watchlist.py  (unchanged, 16)
    store.py               RENAME of registry.py (was: idea_store.py)
    creators.py            KEPT, minus the three blocks moved by M1, M2, M3
    dossier/               (SPLIT of dossier.py, 3)
      load.py              _Ctx, resolve, _load_rates, _load_fixtures,
                           _load_ownership, _load_projection,
                           _simulate_projection, _load_intel
      sections.py          Section, _ok, _gap, the fourteen builders _identity
                           through _disagreement, plus _normalise_selections,
                           _club_similarity, _odds_key_for, _match_selection,
                           _table_exists
      render.py            Dossier, _squash, build, build_text, register_cli,
                           _parse_as_of, telegram_addendum, mcp_payload,
                           _unused_guard
  jobs/
    __init__.py, outbox.py, post_gw.py                 (unchanged, 3)
    deadline_dag.py        KEEPS its name and its `-m` target. Loses the task
                           vocabulary (to pipelines/contracts.py) and the five
                           registry task bodies (to pipelines/tasks.py). Its
                           lazy imports at :182, :1327, :1328 become
                           module-level.
  models/
    __init__.py, contracts.py                          (unchanged)
    copying/                                           (unchanged, 8)
    ensemble/
      __init__.py          docstring corrected: it is NOT outside the
                           production closure, fixtures.py:745 imports sources
      sources.py                                       (unchanged)
      backtest.py, frame.py, weights.py                DELETED
    field/                                             (unchanged, 7; share.py KEPT)
    minutes/                                           (unchanged, 10)
    ownership/
      __init__.py, baselines.py, captaincy.py, drift.py, elite.py, eo.py,
      evaluate.py, field.py, model.py, panel.py, simulate.py  (unchanged, 11)
      backtest.py          KEPT
      fit.py               MERGED into backtest.py
      metrics.py           NEW: mae_pp and COVERAGES, shared by backtest and
                           evaluate (this is the only thing D1 actually found)
    points/
      __init__.py          NEW (see check 9; separate diff)
      bps.py, model.py, scoring_map.py, shares.py      (unchanged)
    team_goals/                                        (unchanged, 13)
      ratings_cache.py     MERGED into platform/scripts/fixtures/build.py,
                           and the post_gw.py:227 `fixture_difficulty` step
                           deleted with it
  myteam/                                              (unchanged, 14)
  opt/                                                 (unchanged, 7)
  oracle/
    __init__.py            NEW
    adapters.py, signals.py                            (unchanged)
  pipelines/
    __init__.py            re-exports unchanged
    contracts.py           NEW leaf: Step, TaskContext, TaskResult, run_step,
                           SEASON, LOOKBACK, Due, STALE_WINDOWS
    tasks.py               NEW: presser_projection_refresh, price_radar,
                           final_solve_delivery, lineup_captain_check,
                           odds_refresh
    registry.py            imports contracts and tasks, never jobs. The
                           briefing_intel task becomes a `-m` step. The
                           duplicate panel_picks_crawl / crawl_panel pair is
                           resolved by deleting the post_gw.py:288 step.
    health.py, runner.py   import contracts and registry, never jobs
  platform/
    __init__.py, briefing_intel.py, chat_agent.py, fpl_theme.py, panels.py,
    prose_style.py, query.py, registry.py, routes_account.py, solve_runner.py
                                                       (unchanged, 10)
    deliveries.py          RENAME of inbox.py (was: outbox_view.py)
    app/                   (SPLIT of app.py, 8)
      factory.py, helpers.py, routes_core.py, routes_inbox.py,
      routes_solve.py, routes_players.py, routes_pipelines.py,
      routes_content.py, routes_chat.py
    link_jobs/             (SPLIT of link_jobs.py, 3)
      preflight.py         LinkRefused, _now, _iso, _substantive, Preflight,
                           _reader_context, _gw_preview, _yt_watch_url,
                           _default_fetcher, preflight, _preflight_youtube,
                           _preflight_article
      take.py              _table_exists, _existing_item, _rank_items,
                           _item_for_url, build_take, _ledger, _attribution,
                           _ingest_once, _annotate, discard_item, restore_item,
                           correct_gameweek, ingest_with_retry
      runner.py            _Job, _Cancelled, UnknownJob, JobAlreadyFinished,
                           NotAwaitingDecision, LinkJobs
    scripts/
      __init__.py          the explicit-import registry, plus ownership and
                           planner added to __all__
      common.py            gains _squad_state (move M4)
      ideas.py, market.py, pipelines_panel.py, planner.py, player_profile.py,
      prices.py, radar.py, squad.py                    (unchanged, 8)
      creators/            (SPLIT, 6)
        identity.py, schema.py, board.py, detail.py, chatter.py,
        report_card.py
      brief/               (SPLIT, 4)
        schema.py, tiles.py, plan.py, build.py
      fixtures/            (SPLIT, 5)
        constants.py, ratings.py, board.py, detail.py, build.py
      ownership/           (SPLIT, 5)
        schema.py, load.py, fields.py, tools.py, panel.py
      projections/         (SPLIT, 3)
        schema.py, artefact.py, gw.py
  mcp/                   NEW (workstream M): the MCP toolbelt, rewritten as a
                         second consumer of the panels. Sits in the same tier
                         as jobs/: it imports platform and interfaces and
                         nothing below imports it.
    __init__.py          empty, so `import fpl_edge.mcp` costs nothing
    __main__.py          guarded main(): --help, --list-tools, --check, stdio
    server.py            the one FastMCP instance, named fpl-server
    context.py           the user context, the db path, the as_of parser
    adapter.py           run_script to the envelope. No tool bypasses it
    render.py            the pure helpers fpl_mcp/tools/chat_core.py held
    prompts.py           one prompt, derived from what registered
    tools/               (12) analysis, creators, dossier, fixtures, ideas,
                         manager, ownership, pipelines, projections, solve,
                         squad, watchlist
  rank/, rules/, sim/, store/, theses/                  (unchanged, 6+2+10+3+9)
```

Import direction after the seven cycle-breaking moves, top to bottom, no edge
upward:

```
jobs, mcp  ->  pipelines  ->  platform  ->  interfaces  ->  ingest  ->  models/sim/opt/rank  ->  store, rules
                                   \______  pipelines.contracts (leaf)
```

`platform.scripts.pipelines_panel -> pipelines` and
`platform.scripts.ownership -> interfaces.qa` both survive and both run one way.

One edge runs the other way and is allowed by name. `platform/chat_agent.py`
imports `fpl_edge.mcp.server` inside `list_mcp_tools` and `toolbelt_instance`,
which is `platform -> mcp -> platform` at package granularity. Both imports are
function-local, `fpl_edge/mcp/__init__.py` is empty so importing the package
pulls in nothing, and `tests/unit/test_mcp_tool_contract.py` fails on a
module-scope import of `fpl_edge.mcp` from anywhere outside the package. The
2026-08-27 fold-in commit chose the sibling `fpl_mcp/` layout to avoid this
edge; the owner's instruction for the MCP rewrite reverses that call, so the
mitigation is the import test rather than the layout.

---

## Section 4: execution order for A3

Renames first, then merges, then splits, then deletions, as the brief asks.
Cycle-breaking moves are interleaved where a later step depends on them. Each
row is one commit, one logical change, and each is reviewable on its own. Every
row assumes the gate from brief rule 2 and a baseline diff from rule 3.

| # | diff | modules touched |
| --- | --- | --- |
| 1 | Add `fpl_edge/oracle/__init__.py`. Nothing else. | 1 new file |
| 2 | Add `fpl_edge/models/points/__init__.py`. Nothing else, so the audit message change is attributable. | 1 new file |
| 3 | Rename `interfaces/registry.py` to `interfaces/store.py`. | that file plus `cli/main.py:31` and seven other importers, `tests/unit/test_ideas_registry.py`, `tests/unit/test_interfaces_e2e.py` |
| 4 | Rename `platform/inbox.py` to `platform/deliveries.py`. | that file, `platform/app.py:69` |
| 5 | **Move M1.** New `ingest/content/urls.py`; delete `interfaces/creators.py:376 _YT_ID_RE`. | `platform/scripts/creators.py`, `ingest/content/pipeline.py:1172`, `platform/link_jobs.py:444,702,1426,1617`, `interfaces/creators.py:1101,1246` |
| 6 | **Move M2.** New `ingest/content/link_ledger.py` from `interfaces/creators.py:626-904`. | `interfaces/creators.py`, `ingest/content/store.py:505`, `platform/link_jobs.py:978-995` |
| 7 | **Move M3.** New `ingest/content/identity.py` from `interfaces/creators.py:85-186`. | `interfaces/creators.py`, `ingest/content/pipeline.py:1529`, `interfaces/qa.py:515` |
| 8 | **Move M4.** `_squad_state` to `platform/scripts/common.py`. Delete `platform/scripts/creators.py:902-939` (`_gw_calendar`, `_gw_after`) in favour of `ingest/content/claims.py:181`, now importable. | `platform/scripts/ownership.py`, `platform/scripts/common.py`, `platform/scripts/creators.py` |
| 9 | **C1 loop B.** `briefing_intel` becomes a `-m` step; add a `__main__` guard. | `pipelines/registry.py:694-714`, `platform/briefing_intel.py` |
| 10 | **C1 loop A, step 1.** New leaf `pipelines/contracts.py`. | `jobs/deadline_dag.py`, `pipelines/{registry,health,runner}.py` |
| 11 | **C1 loop A, step 2.** New `pipelines/tasks.py` with the five task bodies; `deadline_dag`'s lazy imports become module-level and the `:178` comment goes. | `jobs/deadline_dag.py`, `pipelines/{registry,tasks}.py` |
| 12 | Merge `models/ownership/fit.py` into `backtest.py`; extract `mae_pp` and `COVERAGES` to `models/ownership/metrics.py` and point both `backtest.py` and `evaluate.py` at it. | 4 files, `tests/unit/test_ownership_walk_forward.py` |
| 13 | Delete the `crawl_panel` step at `post_gw.py:284-288` (keep the standalone `panel_picks_crawl`). Delete the `ingest_projections` step at `post_gw.py:211` (it re-runs at `registry.py:543`). | `jobs/post_gw.py` |
| 14 | Split `ingest/rivals/elite_list.py`: the pinned tuple to `data/reference/elite_1000.json`, `top(n)` reads it. | `elite_list.py`, `rivals/roster.py:74` |
| 15 | Split `ingest/odds.py` into `ingest/odds/` (6 modules), `__init__.py` re-exporting the current surface. Cut by function name, not line range. | 1 file becomes 7; nine test files unchanged if the re-export is complete |
| 16 | Split `ingest/content/pipeline.py`: three `*_cmd.py` modules, `pipeline.py` stays the dispatcher. No call site changes. | 1 file becomes 4 |
| 17 | Split `platform/link_jobs.py` into `platform/link_jobs/` (3 modules). | 1 file becomes 4, `platform/app.py` |
| 18 | Split `interfaces/dossier.py` into `interfaces/dossier/` (3 modules). **Blocked on test T3.** | 1 file becomes 4 |
| 19 | Decompose `create_app` into seven `APIRouter` factories inside `app.py`, unsplit. Suite must stay green on the eight `platform.app` test files. | `platform/app.py` |
| 20 | Split `platform/app.py` into `platform/app/` (9 modules). | 1 file becomes 9 |
| 21 | Split `platform/scripts/fixtures.py` into `fixtures/` (5 modules), and move `models/team_goals/ratings_cache.py` into `fixtures/build.py` in the same diff. Delete `fixture_difficulty` at `post_gw.py:227` and `load_legacy_difficulty` at `fixtures.py:529`. **Blocked on test T1.** | `fixtures.py`, `ratings_cache.py`, `post_gw.py`, `registry.py:587`, `tests/unit/test_ratings_cache.py`, `tests/unit/test_fixture_board_panel.py:27` |
| 22 | Split `platform/scripts/creators.py` into `creators/` (6 modules), with `schema.py` taking `1129-1608`. Update `tests/unit/test_creator_panel.py:1693` to walk all six files. | `creators.py`, `platform/scripts/__init__.py`, `test_creator_panel.py` |
| 23 | Decompose `dashboard_brief` into sixteen block builders inside `brief.py`, unsplit. **Blocked on test T2.** | `platform/scripts/brief.py` |
| 24 | Split `platform/scripts/brief.py` into `brief/` (4 modules). | 1 file becomes 5 |
| 25 | Decompose `ownership_eo` into the phases in check 5, inside `ownership.py`, unsplit. **Blocked on test T4.** | `platform/scripts/ownership.py` |
| 26 | Split `platform/scripts/ownership.py` into `ownership/` (5 modules). | 1 file becomes 6 |
| 27 | Decompose `_gw_mode` into seven functions inside `projections.py`, unsplit. **Blocked on test T5.** | `platform/scripts/projections.py` |
| 28 | Split `platform/scripts/projections.py` into `projections/` (3 modules). | 1 file becomes 4 |
| 29 | Delete `models/ensemble/{backtest,frame,weights}.py` and correct `models/ensemble/__init__.py:3`. | 4 files |
| 30 | Add `ownership` and `planner` to `platform/scripts/__init__.py:17-19`. | 1 file |

Rows 19, 23, 25 and 27 are separated from their splits on purpose. A function
decomposition that keeps the file intact is reviewable against a diff; a
decomposition bundled with a file split is not.

---

## Section 5: pre-refactor tests that must land first

Five modules are about to be cut open with no test naming them. Each needs one
characterisation test that pins the payload shape, not the numbers, so that the
split is provably behaviour-preserving. Each is a single file and none needs
network.

| id | blocks | module | minimal test |
| --- | --- | --- | --- |
| T1 | diff 21 | `platform/scripts/fixtures.py` | `tests/unit/test_fixtures_build.py`. `tests/unit/test_fixture_board_panel.py` already covers the two panels through `run_script`; what is uncovered is the build job. Seed a tmp warehouse, call `write_artefacts`, assert the three artefact names (`DIFFICULTY_NAME:117`, `RATINGS_NAME:120`, `CALIBRATION_NAME:123`) exist with the columns `RATINGS_COLUMNS:252` and `CALIBRATION_COLUMNS:551` declare. This is the only thing standing between A3 and silently changing what `fixture_ratings_refit` writes. |
| T2 | diff 23 | `platform/scripts/brief.py` | Extend `tests/unit/test_dashboard_brief.py` with one test that runs `dashboard_brief` over a seeded warehouse and asserts the exact top-level key set of the payload against `RESULT`, plus the `alerts` and `tiles` list lengths. Sixteen extracted builders must produce the same key set; nothing else pins that today. |
| T3 | diff 18 | `interfaces/dossier.py` | `tests/unit/test_dossier.py`. Zero test files today for 1,674 lines. Seed a tmp warehouse, call `build(code)` and assert one `Section` per builder with `ok` or `gap` set and a non-empty reason on every gap. That is brief rule 9 in test form and it is what a fourteen-way section split can break invisibly. |
| T4 | diff 25 | `platform/scripts/ownership.py` | Extend `tests/unit/test_ownership_panel.py` to run `ownership_eo` through `run_script` against `RESULT_SCHEMA` and assert the three tool blocks (`squad-vs-field diff`, `what-if`, `momentum`) are each present with their declared keys. The 975-line function currently has no assertion on its own output shape. |
| T5 | diff 27 | `platform/scripts/projections.py` | `tests/unit/test_projection_gw_mode.py`. Zero test files today for 1,574 lines. Run `projection_table` in both modes against a seeded warehouse, assert `_ARTEFACT_RESULT` and `_GW_RESULT` validate, and assert the `mode` field is `consensus` when several sources are present and `source` when one is. The mode branch at `projections.py:842-986` is the single largest untested branch in the plan. |

Two more that are not blockers but are cheap and prevent a known silent failure:

| id | module | test |
| --- | --- | --- |
| T6 | `platform/fpl_theme.py` | One test asserting the file parses with `ast.parse` and contains no `fpl_edge` import. It is copied into a `python -I` sandbox by `fpl_mcp/tools/viz_tools.py:159` and imported as a top-level module; an `fpl_edge` import added to it breaks every chart and nothing in the suite would notice. |
| T7 | `jobs/deadline_dag.py` | One test asserting `_module_exists("fpl_edge.ingest.lineups")` is True. `deadline_dag.py:971-974` turns a missing module into `Step(ok=True, "skipped")`, so a rename of `ingest/lineups.py` disables the T-90m captain check and reports success. |

Nothing else in the plan moves code that has no test naming it.

## 6. Frozen by the orchestrator (2026-09-17)

The target layout in Section 3 and the diff order in Section 4 are adopted as
written, with two amendments and three notes. Where this review overturns
`ARCHITECTURE.md`, this review wins: every reversal above is cited to a line
that the original table did not read, and the original's seven merges each
put a writer, a leaf, or a test pin in the wrong module.

Amendment 1, row 13. Delete `crawl_panel` from the settlement chain; keep
`ingest_projections`. The row's citation is wrong: `registry.py:543` is
`run_forecast_refresh`, which rolls the forecast forward and ingests nothing.
The step does also run inside the T-30h task (`registry.py:767`), but
`jobs/post_gw.py:206-209` records the reason for the second run: fetching only
at T-30h left every feed a week stale, and the nightly run is the fix.
`PIPELINES_AUDIT.md` cleared this pair for the same reason. Removing it would
revert a documented repair to save one HTTP fetch a night.

Amendment 2, ordering. Section 5's tests T1 to T7 land as group 0, before row
1, and are committed on their own so the refactor diffs can be read against a
green characterisation suite instead of alongside it.

Note 1. Rows 13 (`crawl_panel`) and 21 (`fixture_difficulty` step and
`load_legacy_difficulty`) overlap workstream F's build. The refactor executor
owns them; F's builder must not touch `jobs/post_gw.py` for those two steps.

Note 2. Row 30 adds `models/points/__init__.py`. The walk-forward audit test
already fails in the frozen baseline; the new package appears in that same
test's list under the same FAILED id, so membership is unchanged. Confirmed
against `tests/audit/test_walk_forward.py` before freezing.

Note 3. Execution runs in the main tree, one group at a time, with the full
suite and a baseline diff after each group and a commit only on a clean gate.
Groups: 0 (T1 to T7), 1 (rows 1 to 4), 2 (rows 5 to 11), 3 (rows 12 to 14 as
amended), 4 (rows 15 to 18), 5 (rows 19 to 20), 6 (rows 21 to 22), 7 (rows 23
to 28), 8 (rows 29 to 30). No other builder edits the tree until group 8 is
committed.
