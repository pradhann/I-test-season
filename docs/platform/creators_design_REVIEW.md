# Creators bake-off: the compiled critique

> Adversarial review of `creators_design_A.md` (the ledger),
> `creators_design_B.md` (the reader) and `creators_design_C.md` (the
> subtraction). Written 2026-09-18 at repo sha `1e04b16`. Every number below
> that contradicts a design was re-measured for this review, and the
> instrument is named beside it. Nothing is taken from a design document on
> trust. This file supersedes any August bake-off critique at this path; the
> old text is in git.

---

## 1. How the claims were checked

Five instruments, all re-run:

1. **The live page.** `http://localhost:8321/#creators` in a real browser at
   an emulated 1280x900 and again at a fresh 390x844 mount, walking `#view`
   with `getBoundingClientRect()` and reading
   `performance.getEntriesByType('resource')` after one clean load.
2. **The live panels.** `POST /api/scripts/{creator_board, creator_detail,
   creator_report_card, player_chatter, dashboard_brief}/run` and
   `GET /api/content/sources`, sizes from `curl -w '%{size_download}'` so the
   figure is the wire body and not a re-serialisation.
3. **The two new panels.** The worktree module at
   `.claude/worktrees/agent-af3096a66be70d4ce/fpl_edge/platform/scripts/creators/episodes.py`
   run read-only through
   `Warehouse.read_copy("data/warehouse/fpl.duckdb")`, for every one of the
   29 creators the board serves, not only for the one the designs quote.
4. **The source.** `web/dist/js/views/creators.js` 3,880 lines,
   `web/dist/creators.css` 1,074 lines and 483 declaration blocks,
   `web/dist/app.css` 344 lines, `fpl_edge/platform/panels.py` 229 lines,
   `web/dist/js/app.js`. Function boundaries read out of the files.
5. **The suite.** `uv run pytest -q tests/unit/test_web_contract.py`, 60
   tests, `REAL_PYTEST_EXIT=0`. Every break named in section 7.4 is therefore
   caused by the rebuild.

One harness fact that the dashboard bake-off did not have: **a true 390 CSS
pixel viewport is reachable here.** Fresh mount at 390x844 gives
`innerWidth` 390 and `document.documentElement.scrollWidth` 390. C says so in
its section 1.6 and is right. Every narrow figure in this bake-off is a real
figure, not a floor.

### 1.1 The baseline, measured once, for all three

Live page, 1280x900, one clean load of `#creators`, no clicks.

| endpoint | calls | bytes | summed duration |
|---|---:|---:|---:|
| `scripts/creator_detail/run` | 29 | 3,269,989 | 134,890 ms |
| `scripts/creator_board/run` | 7 | 579,081 | 27,842 ms |
| `scripts/creator_report_card/run` | 1 | 134,733 | 1,139 ms |
| `GET /api/content/sources` | 1 | 25,781 | 742 ms |
| `scripts/squad_overview/run` | 1 | 4,792 | 2,061 ms |
| `GET /api/deadline` | 1 | 370 | 320 ms |
| **total** | **40** | **4,014,746** | |

4,014,746 bytes is **3,921 KiB**.

| geometry | 1280x900 | 390x844 |
|---|---:|---:|
| document, all seven `details` closed | **2,041** | **2,919** |
| document, the matrix and the card wall open | 7,639 | **12,682** |
| document, all seven open | **8,306** | **13,738** |
| the matrix fold, open | **3,997** | **4,783** |
| `table.cx-grid` inside it | 3,825 tall, 278 wide, 57 rows | |
| the report-card fold, all inner folds open | **2,101** | **5,082** |
| main takes | 643 | 984 |
| the board chart | 316 | 314 |
| the armband | 127 | 153 |
| the window fact row | **73** | **177** |
| the gameweek row | 46 | 121 |
| `document.scrollWidth` | 1265 | 390 |

Folded page census inside `#view`: **124** interactive elements
(`button, a, summary, [role=button], input, select`), **211** with
`[tabindex]` included, **1,500** nodes, **385** elements carrying a `title`,
**12** distinct rendered font sizes (9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5,
13, 13.5, 15, 16), **9** distinct text colours, **2,659** rendered characters
over **124** non-blank lines, and **5** rendered ages carrying an hour unit
against 4 carrying a day unit.

---

## 2. Claims verified, per design

### 2.1 Design A, the ledger

| # | A's claim | measured | verdict |
|---|---|---|---|
| A1 | section 0.1, the six-row network table: 29 / 3,193 KB, 7 / 566 KB, 1 / 132 KB, 1 / 25 KB, 1 / 5 KB, 1 / 0 KB, total 40 / 3,921 KB | 29 / 3,193 KiB, 7 / 566 KiB, 1 / 132 KiB, 1 / 25 KiB, 1 / 5 KiB, 1 / 0 KiB, total 40 / 3,921 KiB | **holds, every row** |
| A2 | section 0.1, 2,041 px folded, 8,306 px open, 2,919 and 13,738 at 390; matrix 3,997, card wall 2,101 | 2,041 / 8,306 / 2,919 / 13,738 / 3,997 / 2,101 | **holds, all six** |
| A3 | section 1.0, `creator_detail {days: 1, limit: 1}` is 4,525 bytes against 540,400 at its defaults | `curl -w size_download`: **4,525** and **540,400** | **holds, exactly** |
| A4 | section 1.4, live report card: 31 cards, 11 of 31 quotable, `numeric.measured` false on every card, 50 team people, 2 gaps, `min_scored_claims` 25, `min_gw_measured` 10 | 31, 11, 0 measured, 50, 2, 25, 10 | **holds, all seven** |
| A5 | section 1.6, `panel_squads` is `panel_size` 54, `with_entry` 43, `known` 43, 11 `no_entry_people` | 54, 43, 43, 11 | **holds, exactly** |
| A6 | section 4.6, the `creators.css` deletions total 311 lines: board 312-395, decisions 396-493, armband 494-514, watch 515-529, grid 592-666, orphaned roster 562-579 | the section banners sit at 312, 396, 494, 515, 530, 592, 667. Every range is right to the line, and 84+98+21+15+75+18 is 311 | **holds** |
| A7 | section 4.6, the roster rules at 562-579 are orphaned: `table.cx-roster` and `.cx-latest` match nothing in `creators.js` | a class-literal diff puts `cx-roster`, `cx-latest` and `cx-people` among the **18** CSS classes with no emitter in the view today | **holds** |
| A8 | section 5.4, "`.cx-underfloor` already exists at `creators.css:872` and is reused rather than redefined" | `grep -c "cx-underfloor"` over `creators.css` and `creators.js`: **0 and 0**. Line 872 is a section banner; the class that exists is `.few` | **does not hold**, see 4.1 |
| A9 | section 3.1, `record.scored` is null for 7 of 29; section 6.1, the `measured` filter keeps 22 of 29 | scored is null on **9** of 29 and zero on a tenth, so `measured` keeps **20** | **does not hold** |
| A10 | section 3.4, for Let's Talk FPL over 60 days: 40 rows, **23** canonical groups, **17** podcast-plus-YouTube pairs | 40 rows, **24** distinct titles, **16** pairs, all sixteen podcast plus YouTube | **does not hold**, off by one in both |
| A11 | section 3.4, the 1,492-minute title-matched pair is "two different publications that share a title" | both rows are `My FPL Team for Gameweek 3`, both GW3, one podcast at 2026-09-04T08:41Z and one YouTube at 2026-09-03T07:48Z. It is one recording with a podcast feed running a day late | **does not hold**, and it is the load-bearing premise of A's refusal to join |
| A12 | section 3.5, on the GW4 transfer-tips episode "all 22 claims and all 23 calls have `start_s: null`" | through `episode_summary`, the panel A's design calls: **42 of 45** stored positions carry a `start_s` | **does not hold**, see 4.1 |
| A13 | section 5.1, the `app.css` token table: `.card` 116-118, `.card > h2` 119-120, `.toolbar` 236-238, `.chip` 152-155, `.drawer` 287-291, `table.sticky-first` 180-184, mobile 330-339 | 112-114, 115-116, 240-241, 153-156, 283-287, 177-180, 320. `.provenance` 124 and `.empty` 126-132 are right | **does not hold**, off by 3 to 10 throughout |

A's measurement of the page it is replacing is the best in the bake-off and
reproduces to the byte. A's reading of the two new panels is the weakest,
because A did not run them.

### 2.2 Design B, the reader

| # | B's claim | measured | verdict |
|---|---|---|---|
| B1 | section 5.6, the five `unscored(` sites are 183, 2396, 2628, 2685 and 3448, and two survive B's deletions | `grep -n "unscored("`: **183, 2396, 2628, 2685, 3448**. 183 and 2685 fall inside B's kept ranges | **holds, exactly** |
| B2 | section 5.6, `measuring the record…` has three stripped sites at 774, 2430 and 3441, one of which survives | 5 raw occurrences, 2 of them inside comments, and `_strip_comments` runs first, so the test sees **3**, at **774, 2430, 3441** | **holds, exactly, including the comment-stripping** |
| B3 | section 5.6, `cardsByEntry` is at 261, 661, 666, 667 and 2842, all kept | those five lines exactly | **holds, exactly** |
| B4 | section 5.6, `const one = p => p.quotable` at 2743 and `const SHOWN = 2;` at 2741, both inside `teamLine`, both kept | 2743 and 2741, `teamLine` spans 2730 to 2760 | **holds, exactly** |
| B5 | section 5.5, `creators.css` is 1,074 lines, 249 `cx-` classes, 483 rules, and the surviving JS emits 63 | 1,074 lines, **249** classes, **483** declaration blocks. Re-running B's own surviving ranges emits **64** | **holds within one class** |
| B6 | section 1.4, for Let's Talk FPL over 60 days: 24 distinct titles, 16 appearing twice, 40 percent of the list | 24, 16, 16 of 40 | **holds, exactly** |
| B7 | section 1.2, one clean mount is **42** calls and 3.97 MB, including `fixture_board` 1 and `squad_overview` 2 | one clean mount of `#creators` is **40** calls and 4,014,746 bytes, with **one** `squad_overview` and **no** `fixture_board`. B's timeline carried two calls from another view | **does not hold** |
| B8 | section 1.1, the window fact row is 45 px at 1280 | **73** px. B's 390 figures for the same two rows, 177 and 121, are both exact | **does not hold** |
| B9 | section 1.1, 14 distinct rendered font sizes, 1,507 nodes, 386 titles, 125 interactive, 2,733 characters, 127 lines | **12** sizes, 1,500 nodes, 385 titles, 124 interactive, 2,659 characters, 124 lines | **font-size count does not hold**, the rest hold within a clock tick |
| B10 | section 9.1, the matrix is 4,804 px at 390 | **4,783** | **does not hold** |
| B11 | section 1.4 and 4.2, the podcast copy of `FPL GW4 Transfer Tips` "resolves an offset on 0 of 22 claims and 0 of 24 calls", and level 3 prints "0 of 46 stored positions carry an offset" | `creator_detail` does store 0 of 22 and 0 of 18, so B measured something real. `episode_summary`, the panel B's level 3 calls, resolves **42 of 45** on that item and **40 of 44** on the YouTube twin, because `_evidence` at `episodes.py:591-614` recomputes the offset by substring search against the stored transcript | **does not hold against the panel B calls**, see 4.2 |
| B12 | section 2.3 and 2.4, level 2 is about 18,750 bytes for 30 rows and level 3 about 19,000 bytes | measured: `creator_episodes {limit: 30}` for Let's Talk FPL is **21,467** bytes; `episode_summary` on the podcast copy is **30,108** bytes | **does not hold**, low by 13 and 37 percent |

B's reading of `creators.js` and of the contract tests is the most precise
work in the bake-off, line for line. B's reading of the two new panels is
second-hand and it is where B fails.

### 2.3 Design C, the subtraction

| # | C's claim | measured | verdict |
|---|---|---|---|
| C1 | section 1.8, `creator_episodes` for Let's Talk FPL: counts 73 / 35 / 67, states transcribed 35, queued 27, none 11, no failed, kinds podcast 48 and youtube 25 | 73 / 35 / 67; 35 / 27 / 11 / 0; 48 / 25 | **holds, every figure** |
| C2 | section 1.8, 53 distinct titles over 73 rows, 20 groups hold more than one row, and all 20 are one podcast row beside one YouTube row; 18 of 20 analysed on both, 15 of 20 transcribed on both | 53, 20, 20 of 20 podcast plus YouTube, **18**, **15** | **holds, exactly** |
| C3 | section 1.8, 32 rows carry an analysis with no transcript, 31 analysed rows produced zero claims, six rows carry no analysis, no row has a null gameweek | **32, 31, 6, 0** | **holds, exactly** |
| C4 | section 1.8, `episode_summary` on `d26fac8a02a1928c1a1ea9f6`: 6 bullets, 20 players, 19 transfers, 4 captain calls, 1 gap, GW4, `claude-opus-5`, analysed 2026-09-08T13:34:25Z; and the four captain calls are Haaland GW5, GW7, GW9 and Isak GW4 | 6, 20, 19, 4, 1, 4, `claude-opus-5`, 2026-09-08T13:34:25Z; captain gameweeks 5, 7, 9, 4 | **holds, every figure** |
| C5 | section 1.2 and 1.3, matrix open 3,997 at 1280 and 4,783 at 390, `table.cx-grid` 3,825 tall and 278 wide in a 1,019 px container with 57 rows; report wall 1,660 open and 5,082 at 390 with 28 tiles | 3,997 / 4,783 / 3,825 / 278 / 57 / 5,082 / 28 tiles, and 2,101 with the inner folds open, which C states separately and correctly | **holds** |
| C6 | section 1.5, 12 distinct rendered font sizes with that exact set, 9 distinct text colours, 211 interactive elements, 2,666 characters over 124 non-blank lines | **12** and the set matches value for value, **9**, **211**, 2,659 over **124** | **holds** |
| C7 | section 9.4, `test_every_panel_script_is_rendered_by_some_view` at line 65 breaks on both halves: `panels.py` declares 15 panels and neither new script is among them, and `creator_detail` is declared at `panels.py:165-172` with `creators.js:3299` its only caller in `web/dist/js` | `panels.py` holds **15** `Panel(` entries, `creator_detail` at **164-172**, and `grep -rn creator_detail web/dist/js` finds one `runPanel` call, at **3300**. The worktree diff touches `creators/__init__.py` and the tests and leaves `panels.py` alone | **holds**, and no other design found it |
| C8 | section 1.7, `creator_board {}` returns 58,376 bytes, GW5, 29 creators, 5 consensus rows, scope panel with 29 shows and 4 excluded | **58,376**, GW5, 29, 5, panel / 29 / 4 | **holds, exactly** |
| C9 | section 1.9, `_known_creators` returns 33 names, and the live level-2 failure case is the board serving "The FPL Wire" where the episode panel has "The FPL Wire - Fantasy Premier League" | `_known_creators` returns **33**. Running `creator_episodes` for all 29 board names resolves **29 of 29**, including "The FPL Wire" at 42 / 18 / 36 | **the 33 holds, the failure case does not exist** |
| C10 | section 9.3.3, two of the six live GW4 summary bullets carry an em dash | **three** of the six | **does not hold**, though the finding it supports is real and only C made it |
| C11 | section 1.4, the page-load subtotal is 38 or 39 requests and 3.92 MB | **40** requests and 3,921 KiB. C's per-endpoint rows for `fixture_board`, `player_chatter` and the second `squad_overview` came from another view's timeline | **the byte figure holds, the request count does not** |
| C12 | section 1.6 and 6.1, a true 390 viewport is reachable; `table.data` at `app.css:136`, `table.data th` at 139, `.cx-drawer h2` at `creators.css:670`, `.cx-drawer .sub` at 672, `.toolbar` gap at `app.css:240`, `.empty b` at 129, `--s1` identical in both themes | all seven exact. `.card > h2` is at 115, not 113, and `.card` padding at 112, not 109 | **holds, with two line numbers off by two** |

C's payload measurement reproduces exactly, including every figure about the
two panels that decide the design. C's geometry and its one live-failure
inference are where it slips.

### 2.4 The claims that did not hold, in one place

| designer | claim | measured |
|---|---|---|
| A | `.cx-underfloor` already exists at `creators.css:872` (section 5.4) | it exists nowhere; the class is `.few` |
| A | `record.scored` null on 7 of 29, `measured` filter keeps 22 (sections 3.1, 6.1) | null on 9, zero on a tenth, filter keeps 20 |
| A | 23 canonical groups and 17 duplicate pairs for Let's Talk FPL over 60 days (section 3.4) | 24 groups, 16 pairs |
| A | the 1,492-minute pair is two different recordings sharing a title (section 3.4) | one recording, podcast feed a day behind the YouTube upload, same gameweek |
| A | every `start_s` is null on the GW4 transfer-tips episode (section 3.5) | 42 of 45 through `episode_summary` |
| A | the `app.css` line table (section 5.1) | off by 3 to 10 on seven of twelve rows |
| B | 42 calls on one mount (section 1.2) | 40 |
| B | the window fact row is 45 px at 1280 (section 1.1) | 73 |
| B | 14 distinct rendered font sizes (section 1.1) | 12 |
| B | the matrix is 4,804 px at 390 (section 9.1) | 4,783 |
| B | 0 of 46 stored positions carry an offset on the podcast copy (sections 1.4, 4.2) | 42 of 45 through `episode_summary` |
| B | level 2 about 18,750 bytes, level 3 about 19,000 (sections 2.3, 2.4) | 21,467 and 30,108 |
| C | the live unknown-creator case is "The FPL Wire" (sections 1.9, 4.2) | all 29 board names resolve |
| C | two of six summary bullets carry an em dash (section 9.3.3) | three |
| C | 38 or 39 page-load requests (section 1.4) | 40 |
| C | `.card > h2` at `app.css:113` (section 6.1) | 115 |

---

## 3. The shared risk: the matrix and the consensus

All three delete both. They are two different questions and they get two
different rulings.

### 3.1 What each block answers, from the payloads

`creator_board.consensus[]` is 5 rows this week. Each row carries `code`,
`name`, `pos`, `team`, `price`, `own_pct`, `resolved`, `disambiguator`, and
four objects: `buy`, `sell` and `captain`, each `{n, creators[], n_cue,
n_llm}`; `net`; `mine` `{in_squad, multiplier, role, source}`; and
`panel_owned` `{n, of, people[]}`. Live, Haaland carries
`captain.n` 4 with the four shows named, `panel_owned` 37 of 43 with the 37
people named, and `mine.in_squad` false.

The said-versus-owned matrix reads that list on one axis and
`creator_board.panel_squads` plus `creator_detail.squad` on the other, and
draws 84 marks over 54 people and 4 players in 3,997 px.

### 3.2 Does `player_chatter` cover the per-player half

Yes, both halves of it, and better. `POST /api/scripts/player_chatter/run
{"code": 223094, "days": 30}` is **12,440 bytes** and returns:

* `owned[]`, **37 rows**, each `{person, entry_id, gw, multiplier, role,
  as_of}`. That is the matrix's ring column, per person, with the role and
  the multiplier the matrix never drew.
* `said[]`, **9 rows**, each carrying `person`, `show`, `action`,
  `confidence`, `conviction`, `quote`, `start_s`, `deep_link`, `item_title`,
  `item_url`, `gameweek`, `gameweek_basis`, `on_panel`, `extractor`. That is
  the matrix's hue column, per person, with the quote and the offset the
  matrix never drew.
* `said_by_gw[]`, `counts` with `panel_size` and `squads_known`, and a
  `owned_reason` sentence that is the same sentence `panel_squads.reason`
  carries.

`components/chatter.js` (600 lines) calls it and is imported by
`components/playerdrawer.js:19` and `views/template.js:62`.
`playerdrawer.js` (331 lines) is in turn imported by `views/home.js:52` and
`views/xpoints.js:10`, and it exports `showPlayerDetail`. So the per-player
question already has three live entry points outside this tab, and it
survives the creators view dropping its own 139-line copy at
`creators.js:3741-3879`.

**Ruling on the matrix: dropped.** It is 3,997 px and 377 lines of
`renderGrid` for a fact `player_chatter` serves in 12 KB with the quote
attached. Nothing about the grid form is worth 3,825 px of table in a 278 px
column.

### 3.3 Does anything else read `creator_detail.squad`

No. `creator_detail {creator}` returns `squad[]`, 15 rows of
`{code, name, pos, price, is_captain, multiplier}` with `squad_gw` 4 and a
`squad_reason`. `creator_board.panel_squads` carries counts and names and no
holdings. `player_chatter.owned[]` carries the transpose, so "does person X
hold player Y" is answerable one player at a time, and "what are person X's
fifteen" is not answerable without fifteen or more calls.

That is the real loss, it is the quiet-holdings view, and all three designs
name it. A keeps the path open at a measured 4,525 bytes by calling
`creator_detail {creator, days: 1, limit: 1}` behind a level-2 fold. B and C
drop the call entirely, which also removes the only caller of a declared
panel and turns the `unrendered` half of the test at line 65 red.

**Ruling: keep A's fold.** One call, 4,525 bytes, opened by hand, and it
keeps the panel declaration honest without a `panels.py` deletion.

### 3.4 Does the dashboard cover the consensus

Partly, and in a way that decides the ruling. `brief.py:2181-2209` imports
`creator_board` and reads `consensus[]` for one purpose: the highest
`captain.n` row becomes a dissent voice on the captain verdict. Live, the
served `dashboard_brief` carries

```
verdict.lines[question=captain].dissent[0] =
  {voice: creator_armband, rule: creator_armband_count,
   player: {code: 223094, name: Haaland, ...},
   numbers: {armband_calls: 4},
   source_panel: creator_board, drill: {tab: "creators"}}
```

So the armband tally is already on the dashboard, which corrects A's section
8.1.3. It also means the dashboard sends the reader **to this tab** to see
the rest, and `home.js:342` does that with `location.hash = "#" + drill.tab`.
If the creators tab drops the consensus, that drill lands on a page that
cannot answer the question that sent the reader there. No design lists this.

The buy and sell tallies, `net`, `own_pct` and `panel_owned` are served
nowhere else. And `consensus[]` is the only served list of which players the
panel is talking about this window, so without it there is no way to get a
player code from this tab and `player_chatter` is unreachable from here.

**Ruling on the consensus: folded behind level 1, not dropped.** A closed
`details` on level 1, five rows this week, each row `name`, `pos`, `team`,
`buy n`, `sell n`, `captain n`, `panel own n of 43`, and the served
`mine.in_squad` word. Zero extra calls on mount, because `consensus[]` is
already inside the 58,376 bytes level 1 fetches. The name opens
`showPlayerDetail` from `components/playerdrawer.js`, which already mounts
`chatterStrip`, so one click reaches `player_chatter` and 139 lines of local
player drawer come out. Closed the fold measures about 33 px, against the
5,083 px that main takes, the board chart, the armband and the matrix occupy
today for the same five players.

Two lines, for the record:

> The said-versus-owned matrix is dropped: `player_chatter` serves both of
> its halves per player in 12,440 bytes, with the quote and the multiplier
> the grid never drew, and it is already mounted in three views.
>
> The consensus is folded behind level 1, not dropped: `dashboard_brief`
> reads it for the captain dissent and drills back here, and it is the only
> served list that turns this tab into a player code.

---

## 4. The owner's Thursday night, run against each design

Thursday before a Saturday deadline. The owner wants to know what FPL Wire
and Let's Talk FPL said this week and whether to trust it, sometimes on a
phone.

### 4.1 The failure all three share, and none of them measured

**Twenty-one of the 29 creators on the board have `counts.transcribed` of
zero.** Measured by running `creator_episodes` for every board name:

| creator | total | transcribed | analysed |
|---|---:|---:|---:|
| Fantasy Football Scout | 306 | **0** | 187 |
| Let's Talk FPL | 73 | 35 | 67 |
| FPL Harry | 66 | 32 | 59 |
| Planet FPL | 62 | **0** | 55 |
| FPL Focal | 59 | **0** | 55 |
| FPL Raptor | 59 | 31 | 53 |
| Fantasy Football Hub | 58 | 30 | 51 |
| Gianni Buttice | 45 | **0** | 41 |
| AllAboutFPL | 43 | **0** | 33 |
| The FPL Wire | 42 | 18 | 36 |
| FPL Tom | 23 | **0** | 21 |
| FPL Mate | 21 | **0** | 19 |
| FPL BlackBox | 21 | 7 | 15 |
| Above Average FPL | 19 | **0** | 18 |
| Ignore the Template | 18 | **0** | 17 |
| FPL Fran | 12 | 8 | 9 |
| All In Football FPL | 12 | **0** | 11 |
| FML FPL | 12 | **0** | 12 |
| The 59th Minute | 11 | **0** | 0 |
| FPL Family | 11 | **0** | 11 |

Eight creators have any transcript at all. `creator_episodes` defaults to
`limit: 30, include_untranscribed: False`.

* **B's level 2 sends `{creator, limit: 30, include_untranscribed: false}`.**
  For Fantasy Football Scout, the first row of B's own level-1 wireframe,
  that call returns **0 rows in 1,662 bytes** against 306 publications and
  187 stored analyses. B has the empty state written (section 4.5) and does
  not know it is the live state for 21 of 29 shows.
* **C's level 2 sends `{creator}`**, the same defaults, the same empty list
  for the same 21.
* **A's level 2 sends `{creator, limit: 200, include_untranscribed: true}`**
  and then filters client-side with `transcribed` as the default chip
  (section 6.2), so A's table is also empty for those 21, but A has every row
  in hand and the `all 306` chip carries its own count beside the empty one.
  A is one click from right where B and C are one call from right.

The owner's sentence names FPL Wire, which has 18 of 42, so the transcribed
default works for the show he named and fails for the show at the top of the
list. **The default must be every episode, with `transcribed` as a chip.**

### 4.2 Design A: the timestamps and the join argument are both wrong

A's section 3.5 and A's level-3 wireframe print `-` in every `AT` cell on
the GW4 transfer-tips episode, sourced from "all 22 claims and all 23 calls
have `start_s: null`". That is true of `creator_detail`, which reads a stored
offset. It is not true of `episode_summary`, the panel A's level 3 calls.
`_evidence` at `episodes.py:591-614` recomputes the offset by normalised
substring search against `TranscriptIndex`, and on that exact item it
resolves **42 of 45**. A's deepest level is drawn empty where the payload is
full.

A's section 3.4 refuses to join the podcast and YouTube rows and rests the
refusal on one sentence: a title-matched pair 1,492 minutes apart "is two
different publications that share a title". Measured, that pair is
`My FPL Team for Gameweek 3`, podcast 2026-09-04T08:41Z and YouTube
2026-09-03T07:48Z, both GW3, 23 claims against 7 and 39,706 transcript
characters against 34,076. It is one recording with a slow podcast feed, the
same shape as the other fifteen. A's counter-example is a positive.

Third, unlisted: **A has no test section at all.** The words `test`,
`pytest` and `test_web_contract` do not appear in the document. A deletes
`rcChip` and `rcTag` (2381-2425) and `recordStrip` (2426-2478), trims
`cardCensus` and `censusLine` (746-816) and `sourceStrip` (822-902), and
introduces `runPanel("creator_episodes")`. That is at least six named tests,
including the one at line 65 that no design except C found, and A plans none
of them.

Fourth, unlisted: A's `.cx-underfloor` is not a reuse, it is a new class, and
the rules it would replace are the three selectors
`test_the_under_floor_class_is_actually_styled` asserts by name.

### 4.3 Design B: the hash routes land on the Dashboard

B's section 7.1 declares three routes "all under the existing `#creators`
registration":

```
#creators
#creators/c/<encodeURIComponent(name)>
#creators/e/<item_id>
```

`app.js:200` is the whole router:

```
const name = (location.hash || "#home").slice(1).split("?")[0];
const route = routes[name] || routes.home;
```

The split is on the query mark only. `#creators/c/Let's%20Talk%20FPL` gives
`name` of `creators/c/Let's%20Talk%20FPL`, which is not a registered route,
so `routes[name] || routes.home` falls through and the reader lands on the
**Dashboard**. Every `hashchange` also re-enters `navigate()`, which does
`host.textContent = ""` and re-runs the view's `load()`, so B's plan of one
`hashchange` handler inside `creators.js` owning three levels is a handler
the shell has already torn the view out from under. B's navigation, its
browser-back story and its deep-link story all need a shell change B does not
name. It is a small change and it has to be in the build order.

Second, unlisted: **B misses the test at line 65 entirely.** B's section 5.6
walks all fourteen creators tests at 1049 to 1184 and gets every one right,
and B's design breaks the global test at 65 on both halves at once: the
`unknown` half on the first `runPanel("creator_episodes", ...)`, and the
`unrendered` half because B calls `creator_detail` from no level and it is
declared at `panels.py:164-172` with its only caller at `creators.js:3300`.
The one test B does not mention is the one B's design fails first.

Third: B's level-3 count line, "0 of 46 stored positions carry an offset into
the transcript, so no quote below is a link into the recording", is a
rendered sentence built from the wrong panel. Measured through
`episode_summary`, the podcast copy is the better of the pair on offsets
(42 of 45 against 40 of 44 on the YouTube twin), and B's advice to go back
and open the other row is advice to open the worse copy.

### 4.4 Design C: a link labelled with a time that it does not honour

C's section 2.4 prints `"at 4:53" when start_s is not null` on
`transfers_suggested[]`, `captain_view[]` and `players[].claims[]`, and C's
level-3 wireframe puts an outbound arrow beside every one of them. Measured
on the podcast copy: all **42** resolved positions carry
`deep_link` equal to `episode.source_url`, the bare
`pscrb.fm/.../COMG1299028662.mp3`. `urls.deep_link` at `urls.py:70-87` says
so in its own docstring: everything that is not YouTube gets the item URL
untouched, "and `start_s` is still reported so the UI can print the offset
beside a plain link." C draws 42 links that land at second zero. **B's
section 4.6 is the only correct handling in the bake-off** and it is three
lines: nothing when `start_s` is null, an anchor when `deep_link` differs
from `episode.source_url`, plain text when it does not.

Second, unlisted: C's section 4.2 gives the unknown-creator state a live
example that does not exist. All 29 board names resolve in
`creator_episodes`. The 33-against-29 gap is real and it is the other
direction: four names the episode panel knows that the board excludes.

Third: C keeps the 560 px drawer for all three levels. Level 3 on the
measured episode is 6 bullets, 19 transfers, 4 captain calls and 20 players
carrying 45 stored positions in 30,108 bytes, inside 524 px of content. That
is the owner's "make it easy to use" at its weakest point in any of the three
designs, and C's own wireframe stacks the quote and its timestamp onto
separate lines to fit.

### 4.5 The other loop cases, per design

**A creator with 40 percent duplicate rows.** Corpus-wide, across all 29
creators and 994 rows: **112 same-title groups, every one of size 2, covering
224 rows, 22.5 percent of the archive.** 109 are podcast plus YouTube, 1 is
blog plus blog, 2 are podcast plus podcast. 13 of the 112 pairs are more than
24 hours apart, the widest at 26,223 minutes. For Let's Talk FPL it is 40 of
73 rows, 54.8 percent. All three designs print both rows and state the rule,
which is correct as far as rule 9 goes, and all three leave the owner
scrolling a list that is a fifth to a half second copies.

**An episode analysed with no transcript, 32 of 73.** C is the only design
that diagnosed it (`content_item.text_source` is `description`, the analysis
read the show notes) and the only one that asks for the field that would let
the page say so. A and B both name the 35-against-67 contradiction and
neither reaches the cause.

**An analysed episode with zero claims, 31 of 73.** C names the count. B's
level 2 makes `claim_count: 0` plus `analysis_present: false` the row's
disabled state, which is right, but 31 of these rows have
`analysis_present: true` and zero claims, so B's rule does not fire and the
row is drawn as readable with nothing in it. A's level 2 shows `READ yes` and
`CLM 0`, which is honest. C draws the model and a zero, also honest.

**A creator with `last_item_at` null, 4 of 29.** All three handle it. A sorts
them last under nulls-last and prints `latest_reason`. B folds them into
"4 shows with nothing on file". C prints "never" and "0 / 0". B's fold is the
best of the three for a phone. Note that all four of those creators also
return `episodes_total: 0` from `creator_episodes`, so level 2 for them is
the panel's own empty reason, which all three print verbatim.

**On a phone.** Today the folded page is 2,919 px at a true 390. A's level 1
puts a ten-column table behind a horizontal scroll with `table.sticky-first`,
which is the Fixtures idiom and is a drag gesture per column. B's level 1
wraps to three lines per row at 52 px with no horizontal scroll and is the
best of the three. C's level 1 wraps to a name line plus a middle-dot metrics
line, which is nearly as good and keeps the 1280 reading order.

---

## 5. Rule 9 audit

| design | element | status |
|---|---|---|
| A | `_EPISODE.transcription_reason` (section 4.7.1) | **not served.** Additive, cheap, `content_transcribe_skip` is already queried by `_ids_in`. Six rows in the whole corpus carry `no_captions`, `relevance:2` or `ConnectError` |
| A | `_COUNTS.by_gameweek` (section 4.7.2) | **not served.** Additive and low value: `episodes[].gameweek` is served on every row and the 200 cap bites on exactly one creator |
| A | the `LATEST LINE` ladder, four rungs with a leading tag | every rung is a served key and the tag names which one. Acceptable |
| A | `daysOld(iso)` | a format over a served stamp, not a new fact. Acceptable |
| A | every `AT` cell drawn as `-` on a transcribed episode | **prints an absence the payload denies.** 42 of 45 offsets are served. This is the inverse of a rule-9 violation and it is worse, because absence-with-a-reason is the one thing the page is required to get right |
| B | `_EPISODE.siblings` (section 9.2) | **not served.** See 6.2 |
| B | `_EPISODE.transcription_reason` (section 4.3) | same as A's, same ruling |
| B | the level-3 count line, "N of M stored positions carry an offset" | a count over a served array, which is reading it. Acceptable in form, wrong in the measured value |
| B | the call index, "5 transfers in, 14 transfers out, 3 captaincy, 0 differentials" | a count over `players[].claims[].stored_in`. Acceptable |
| B | the state glyph, four characters over a closed enum | a rendering of `transcription_state`, with the word in the `aria-label`. Acceptable |
| C | `_EPISODE.text_source` (section 9.1.1) | **not served.** Additive, one dictionary key, and the highest-value of the three asks: it is what separates a transcript read from a show-notes read on 32 of 73 rows |
| C | `_EPISODE.state_reason` (section 9.1.2) | same as A's `transcription_reason`, same ruling |
| C | a publication group id (section 9.1.3) | **not served.** See 6.2 |
| C | the filter badge, `counts.episodes_total - counts.transcribed` | a subtraction of two served integers, marked as such in C's own table. Acceptable |
| C | the duplicate-evidence line, "1 claim row, 1 analysis call, one quote" | a count over two served fields, `kind` and `stored_in`, plus a string equality on `quote` and `start_s`. Acceptable, and it is the only design that noticed the case |
| C | `"at 4:53"` rendered as an outbound anchor on a non-YouTube `deep_link` | **asserts a fact the payload denies.** `deep_link` equals `source_url` and the docstring says why |

**The inferred join.** All three refuse to title-match in the browser and all
three are right to. B's section 9.2 and C's section 9.1.3 both ask for the
join to be computed in the panel instead, which is a different question and
the correct one: rule 9 forbids the page inventing a fact, not the panel
serving one with its basis attached. Section 6.2 rules on it with the corpus
numbers.

**No design asks for a free-text field.** Every proposed addition is an enum,
an integer, a nullable string copied from a named column, or an array of item
ids. That much is unanimous and correct.

---

## 6. Verdict

**Build C's structure and deletion list, with A's level-1 table, A's level-2
fetch parameters and A's `creator_detail` fold, and B's timestamp rule, test
walk-through and narrow-width row.**

Three lines:

1. C is the spine, because the two new panels are the whole design and C is
   the only designer who ran them: every one of its section 1.8 figures
   reproduces exactly, it is the only design that grouped `captain_view` by
   its own gameweek (validated: GW5, GW7, GW9 and GW4 on a GW4 episode), and
   it is the only design that found the `panels.py` trap that turns the suite
   red on the first commit.
2. A supplies level 1, because A measured the page it is replacing to the
   byte, its level-1 column values are all correct against the live payload,
   and its level-2 call shape is the only one that does not render an empty
   table for 21 of 29 creators.
3. B supplies the three-line `deep_link` rule that the other two get wrong,
   the exact contract-test assertions for all fourteen creators tests, and
   the wrapped narrow-width row; B does not supply the page, because its hash
   routing lands on the Dashboard and its central level-3 number is measured
   off a panel it does not call.

### 6.1 The synthesis, by numbered section

| take | from | why |
|---|---|---|
| the three-level shape, one call per level, the level-3 section order (summary, transfers, captain grouped by its own gameweek, players, gaps) | **C** sections 0, 2.4, 3.3 | The only section order validated by the payload. Three of the four captain calls on a GW4 episode target GW5, GW7 and GW9 |
| the deletion list with a measured pixel cost per block | **C** section 5 | 34 blocks, each with its cost and its fate. The arithmetic reconciles and it is the only list with `cut`, `move` and `fold` as separate verdicts |
| the report card: delete the wall, keep the card, open it lazily at level 2 with `{creator}` | **C** section 8 | Removes 134,733 bytes and 28 tiles from the mount. `creators[].record` already carries every number the level-1 cell prints |
| the type, spacing and colour budget: two body sizes, four spacing values, three status tokens and one accent | **C** section 6 | The only census that reproduces (12 sizes, 9 colours), and the only design that refuses a hue for `direction` on the grounds that `_EVIDENCE.direction` is an open string |
| level 1 as a sortable `table.data` in the Fixtures idiom, with the column set and the null-sort rules | **A** sections 2.1, 6.1 | The brief names Fixtures as the quality bar. A's ten columns are all served keys and its measured values are correct. Take C's five-column set at 390 |
| the level-2 call: `{creator, limit: 200, include_untranscribed: true}`, every filter client-side | **A** section 6.2 | The one correction that keeps level 2 non-empty for 21 of 29 creators. Measured worst case is 141,610 bytes for Fantasy Football Scout at 200 rows |
| the level-2 "their FPL team" fold, `creator_detail {creator, days: 1, limit: 1}` | **A** section 1.5 | 4,525 bytes measured, opened by hand, and it keeps the only path to a creator's fifteen and the only caller of a declared panel |
| the wide drawer, `min(920px, 96vw)` for levels 2 and 3 | **A** sections 2.2, 5.3 | 30,108 bytes of level 3 does not fit in C's 524 px of content |
| the timestamp rule: nothing when `start_s` is null, an anchor when `deep_link` differs from `episode.source_url`, plain text when it does not | **B** section 4.6 | The only correct handling. A and C both draw a link that lands at zero |
| the contract-test table, assertion by assertion | **B** section 5.6 | Every one of B's fourteen diagnoses verified exactly, including the comment-stripping on `measuring the record…`. Add C's line 65 |
| the narrow row: wrap to three lines at 52 px, no horizontal scroll, and the "N shows with nothing on file" fold | **B** sections 3.4, 4.5 | The best phone level 1 of the three, and it handles the 4 null rows without a sort rule |
| the consensus fold on level 1, the player name opening `showPlayerDetail` | **this review**, section 3.4 | Zero extra calls, keeps the dashboard's `drill: {tab: "creators"}` honest, and replaces 139 lines of local player drawer with an existing export |

### 6.2 The two contract changes, ruled

**1. A `siblings` or release key on `_EPISODE`.** Asked for by B section 9.2
and C section 9.1.3. **Additive, buildable, and supported by the corpus.**

Measured across all 29 creators and 994 rows: **112 same-title groups, every
one of size 2.** 109 are `podcast` plus `youtube`, 1 is `blog` plus `blog`,
2 are `podcast` plus `podcast`. A rule of "same creator, same title, and the
two rows differ in `source_kind`" keeps all 109 real pairs and drops all 3
false positives, with no time window at all, which matters because 13 of the
112 pairs are more than 24 hours apart and the widest is 18.2 days. A time
window is the thing that does not work; kind inequality is the thing that
does.

Serve it as `siblings: [item_id]` plus a `siblings_basis` sentence naming the
rule, computed in `_families` where the corpus is visible. `_EPISODE` sets
`additionalProperties: False`, so both keys need a declared property; neither
goes in `required`, so the worktree's own tests keep passing. **Not required
to ship.** Level 2 renders both rows without it and states the rule once, as
all three designs already specify.

**2. The two new panels declared in `fpl_edge/platform/panels.py`.**
**Additive, and it is the first commit, not a later one.**

`panels.py` declares 15 `Panel` entries and the worktree diff touches
`creators/__init__.py` and `tests/unit/test_creator_panel.py` and leaves
`panels.py` alone. `test_every_panel_script_is_rendered_by_some_view` at
`tests/unit/test_web_contract.py:65` collects `runPanel("...")` across all of
`web/dist/js` and asserts both directions against `panels_mod.PANELS`. The
`unknown` half goes red on the first `runPanel("creator_episodes", ...)`. Two
`Panel(...)` entries is a declaration and adds no payload key, so it is
additive in the sense rule 9 cares about.

The `unrendered` half is the other half of the same trap: `creator_detail` is
declared at `panels.py:164-172` and `creators.js:3300` is its only caller
anywhere in `web/dist/js`. Keeping A's level-2 fold keeps that green without
touching `panels.py` a second time.

**Two further asks, ruled together.** `_EPISODE.text_source` (C 9.1.1) and
`_EPISODE.transcription_reason` or `state_reason` (A 4.7.1, B 4.3, C 9.1.2)
are both additive, both one dictionary key from a column the module already
reads, and both remove a refusal the page currently has to print. `text_source`
is worth building in the same change because 32 of 73 rows for one creator are
analysed with no transcript and the page cannot say why. The skip reason
covers six rows in the whole corpus and can wait. `_COUNTS.by_gameweek` (A
4.7.2) is not worth building.

**One shell change, not a payload change.** `app.js:200` splits the hash on
the query mark only, so any sub-path route falls through to `routes.home`.
Level 3 surviving a reload needs

```
const [name, ...rest] = (location.hash || "#home").slice(1).split("?")[0].split("/");
const route = routes[name] || routes.home;
await route.load(host, rest);
```

Every existing hash still resolves, because none of them contains a slash,
and every existing `load(host)` ignores the second argument.

### 6.3 The single biggest thing the build must get right

**Level 2 must ask for every episode, not for the transcribed ones.**

`creator_episodes` defaults to `limit: 30, include_untranscribed: False`, and
**21 of the 29 creators on the board have `counts.transcribed` of zero**.
Fantasy Football Scout is 0 transcribed of 306 publications with 187 stored
analyses, and it is the first row of every design's level 1 because it is the
most recent publisher. B and C both send the panel defaults. A sends
`include_untranscribed: true` and then defaults its own chip to `transcribed`,
which is the same empty table one click from the fix.

The owner's sentence is "if I click FPL Wire, it should show which episodes
were latest transcribed". FPL Wire has 18 of 42 and works. Generalised to a
default it fails for 21 of 29 shows, including the four he is most likely to
click first. The call is
`{creator, limit: 200, include_untranscribed: true}`, the default view is
every row newest first, and `transcribed` is a chip carrying its own count
beside `all`. The acceptance check in 7.5 measures it for all 29.

---

## 7. Build order

### 7.1 Land first, one commit each, reversible

| # | change | file | effect |
|---|---|---|---|
| 1 | Two `Panel(...)` entries for `creator_episodes` and `episode_summary`, id, title, script, layout and a description that says what each serves | `fpl_edge/platform/panels.py` | the test at line 65 stays green through every later commit. Without this, commit 2 turns the suite red and every diagnosis after it is noise |
| 2 | `navigate()` splits the hash on `/` as well as on the query mark and passes the tail to `route.load(host, rest)` | `web/dist/js/app.js` | level 3 survives a reload. No existing route contains a slash, so nothing else moves |
| 3 | Merge the worktree `episodes.py` and its `creators/__init__.py` exports and `tests/unit/test_creator_episodes.py` | `fpl_edge/platform/scripts/creators/` | the two panels exist on the server before any view calls them |

### 7.2 The reconciled deletion list

Ranges read out of `creators.js` for this review by matching each declaration
to the closing brace at its own indent. Where the three documents disagree by
a line or two, the number here is the measured boundary. `creators.js` is
3,880 lines.

| order | lines | count | block | fate | A | B | C |
|---|---|---:|---|---|---|---|---|
| 1 | 27-33 | 7 | `NS`, `sv()` | delete, no SVG on any level | cut | cut | cut |
| 2 | 64-78 | 15 | `linkKind()` | delete, `episodes[].source_kind` says | keep | dies with `quoteBlock` | cut |
| 3 | 96-102 | 7 | `LANES`, `laneLabel` | delete, no squad lane | cut | cut | cut |
| 4 | 104-119 | 16 | `canonicalKey()` | move with the link bar | keep | keep | move |
| 5 | 201-207 | 7 | `STATE_ORDER`, `HIDDEN_STATES` | delete with the source strip | keep | keep | cut |
| 6 | 284-318 | 35 | `claimsIdx`, `freshState`, `loadRecency`, `freshFor`, `agoText`, `H48` | **delete.** 29 calls, 3,269,989 bytes, 134,890 ms measured | cut | cut | cut |
| 7 | 330-339 | 10 | `squadByCreator`, `squadsAsked`, `boardCache`, `gwCount`, `gwProbing`, `BACK`, `FWD` | delete | cut | cut | cut |
| 8 | 347-382 | 36 | the `squad_overview` call, `sq`, `laneOf`, `mine`, `squadReady` | delete, the dashboard owns squad | cut | cut | cut |
| 9 | 384-441 | 58 | `grp`, `buildRows`, `byWeightOfMouth`, `inWindow`, `wantH48`, `windowed`, `currentRows` | delete, the consensus fold reads `consensus[]` directly | cut | cut | cut |
| 10 | 443-556 | 114 | `render`, `gwCandidates`, `probeGameweeks`, `selectGw`, `renderGwRow` | **delete.** 6 extra board calls, 566 KiB, 46 px at 1280 and 121 px at 390 | cut | cut | cut |
| 11 | 558-628 | 71 | the window fact row and `toggle()` | delete. **73 px** at 1280 and **177 px** at 390, not B's 45 | trim | cut | cut |
| 12 | 630-653 | 24 | `loadSources`, `visibleSources`, `reloadSources` | delete with the strip | trim | keep | cut |
| 13 | 656-673 | 18 | `loadReportCard` on mount | **rewrite** to lazy `{creator}` at level 2 | not fetched | lazy | lazy |
| 14 | 740-769 | 30 | `cardCensus`, `censusLine` | delete. Pinned by the test at 1106, rewrite it | trim | keep | cut |
| 15 | 793-820 | 28 | `recordSentence`, `mainShows` | delete, `record_note` is served on every board payload | trim | keep | cut |
| 16 | 822-1071 | 250 | `sourceStrip`, `sourceTable`, `actionCell`, `fetchSource`, `refusal`, `fetchMainShows`, `seqLine` | **delete.** A 43-feed operator console, 1,623 px open. Pinned by the test at 1124 | trim to 40 | keep | cut |
| 17 | 1073-1134 | 62 | `runAnalyse`, `analyseLine` | delete, the Pipelines tab owns the metered confirm | keep | keep | cut |
| 18 | 1136-1958 | 823 | the whole link bar and job driver | **move** to `web/dist/js/components/ingest_link.js`, opened from level 2. 136 px at 1280 | move | move | move |
| 19 | 1968-2210 | 243 | `renderBoard`'s board section, `drawBoard`, `showTip`, `moveTip`, `boardLegend` | delete. 316 px, a 1000-unit SVG for 4 players | cut | cut | cut |
| 20 | 2212-2373 | 162 | `renderMainTakes`, `decisionCard`, `countChip`, `crossLink` | delete. 643 px at 1280, 984 px at 390 | cut | cut | cut |
| 21 | 2375-2420 | 46 | `rcChip`, `rcTag` | **keep.** The level-1 RECORD cell and the level-2 record line. Keeping them also keeps `.cx-rc.few` and `.cx-rctag.few` alive for the test at 1093 | cut | cut | keep |
| 22 | 2422-2475 | 54 | `recordStrip` | delete. 99 px. Pinned by the test at 1132 | cut | cut | cut |
| 23 | 2477-2519 | 43 | `renderArmband` | delete, `captain_view` carries it per episode and the consensus fold carries the tally | cut | cut | cut |
| 24 | 2521-2550 | 30 | `renderWatching` | delete. Returns early and draws 0 px today | cut | cut | cut |
| 25 | 2552-2581 | 30 | `renderMatrix`, `redrawGrid` | delete with the grid | cut | cut | cut |
| 26 | 2583-2655 | 73 | `renderReportCards` | delete the wall. 2,101 px open at 1280, 5,082 px at 390, 28 tiles | cut | cut | cut |
| 27 | 2657-2691 | 35 | `reportCard` | delete the tile. Pinned by the test at 1093 through `el("button", "cx-rcard " + v.cls` | trim | keep | cut |
| 28 | 2693-2727 | 35 | `rangeBar`, `gwBars` | **keep**, `reportBody` draws both | cut | keep | keep |
| 29 | 2728-2764 | 37 | `teamLine`, `baselineWord` | delete. Pinned by the tests at 1081 and 1143 | keep | keep | cut |
| 30 | 2766-2916 | 151 | `openCard`, `reportBody` | **keep**, the level-2 record fold | trim to 90 | keep | keep |
| 31 | 2918-3294 | 377 | `renderGrid`, `gridRow` | **delete.** 3,997 px at 1280 and 4,783 px at 390 for 84 marks | cut | cut | cut |
| 32 | 3297-3305 | 9 | `detailFor` | **keep**, renamed `episodesFor`, the promise cache by creator | keep | cut | keep |
| 33 | 3307-3358 | 52 | `loadSquads`, `panelSquadWord` | delete with the grid | cut | cut | cut |
| 34 | 3360-3403 | 44 | `quoteBlock` | **keep**, one edit: `claim.extractor` becomes `claim.stored_in` | trim to 28 | cut | keep |
| 35 | 3405-3415 | 11 | `drawerHead` | **keep**, levels 2 and 3 | keep | cut | keep |
| 36 | 3417-3431 | 15 | `openCreator` | replace with `openEpisodes` | replace | cut | cut |
| 37 | 3433-3462 | 30 | `recordLine` | **keep**, the level-2 record line, and the second surviving `unscored(` site | unstated | cut | keep |
| 38 | 3465-3489 | 25 | `transfersSection` | delete | unstated | cut | cut |
| 39 | 3491-3504 | 14 | `methodFold` | **keep**, the level-2 record fold | unstated | cut | keep |
| 40 | 3506-3511 | 6 | `heldOnBoard` | delete | cut | cut | cut |
| 41 | 3513-3737 | 225 | `openPerson` | delete. The quiet-holdings view, replaced by A's `creator_detail` fold at 4,525 bytes | cut | cut | cut |
| 42 | 3739-3771 | 33 | `byCreatorOf`, `openPlayerByCode` | delete | cut | cut | cut |
| 43 | 3773-3879 | 107 | `openPlayer`, including the `chatter.js` import at 3805 | **delete and replace with `showPlayerDetail`** from `components/playerdrawer.js`, called from the consensus fold | cut | cut | cut |

Deleted outright: about **2,050 lines**. Moved to `components/ingest_link.js`:
**839**. Kept: about **990**. New code for the three levels, the consensus
fold, `ageDays`, the sort comparator and the loaders: about **600**. Target
**1,590 lines**, plus or minus 100. The three documents claim 1,758, 1,670
and 1,277; the difference is that this order keeps `rcChip`, `rcTag`,
`recordLine`, `methodFold`, `quoteBlock`, `drawerHead` and `reportBody`,
which is what keeps five contract tests green without a rewrite.

`creators.css`, 1,074 lines and 483 declaration blocks. Deleted whole: the
toolbar and gameweek picker 250-311, the deadline board 312-395, the
decisions and armband 396-529, the said-owned grid 592-666, the person and
their team 688-732, row-level recency 773-781, the source bar and table
791-856, the report-card wall 902-975, and the orphaned roster rules 562-579
that already emit nothing. The link bar block 73-249 moves to
`ingest_link.css`. **Kept:** the quotes block 530-561, the drawer 667-687
with its width raised to `min(920px, 96vw)`, the narrow block 733-751, the
skeleton 752-772, the state chip and dots 800-841 reused for
`transcription_state`, the report-card chip 857-871 and the under-floor rules
872-901, and the record line 976 onward. Target about **560 lines**.

### 7.3 Components reused

| component | where it lives | used by |
|---|---|---|
| the one-per-visit drawer and its Escape handler | `creators.js:226-241`, `.cx-drawer` at `creators.css:669` | levels 2 and 3, width raised to `min(920px, 96vw)` |
| `drawerHead(title, sub)` | `creators.js:3405-3415`, `.dhead` at `app.css:270-274` | the level-2 and level-3 heads |
| `quoteBlock(text, claim, item)` | `creators.js:3360-3403`, `.cx-quote` at `creators.css:530-561` | every claim, transfer call and captain call at level 3 |
| the state chip and its dots | `.cx-state` and `.cx-statedot` at `creators.css:800-841` | `transcription_state` on every level-2 row and in the level-3 head |
| `rcChip`, `rcTag`, `verdict`, `unscored`, `coin` | `creators.js:129-199, 2375-2420` | the level-1 RECORD cell and the level-2 record line |
| `reportBody(c, host)` with `rangeBar` and `gwBars` | `creators.js:2693-2727, 2766-2916` | the lazy level-2 record fold, through `methodFold` |
| `skeleton(cards)` | `creators.js:320-328`, `.cx-skel` at `creators.css:752-772` | all three levels |
| `failFold(e, lead)`, `statusLine(e)` | `creators.js:149-173` | a failed board, a failed record fold, a failed summary |
| `emptyBox(reason, hint)`, `errBox(e)`, `provenance(prov)` | `app.js:40-50` | every empty and error state, and each level's footer |
| `table.data`, `.scroll-x`, `table.sticky-first` | `app.css:135-145, 177-180` | level 1 at both widths, level 2 at 1280 |
| `showPlayerDetail`, `attachPlayerDrawer` | `components/playerdrawer.js`, which imports `chatterStrip` at line 19 | the consensus fold's player names, replacing `openPlayer` |
| `fmtAge` | `app.js:154` | `as_of` only. Publication dates go through a new local `ageDays` |

### 7.4 The contract tests that break, and which are real

Baseline: `tests/unit/test_web_contract.py`, **60 tests**,
`REAL_PYTEST_EXIT=0`. Fourteen read `VIEWS["creators"]` or `CREATORS_CSS`, at
1049, 1060, 1073, 1081, 1093, 1106, 1115, 1124, 1132, 1143, 1151, 1160, 1172
and 1184, plus one global test at 65.

**Real regressions.** These pin a rule the rebuild does not change. If one
fails, the change that did it is reverted before anything else happens.

| test | line | the rule |
|---|---:|---|
| `test_no_rendered_string_in_the_creators_tab_carries_an_em_dash` | 1049 | rule 4, on every string the three levels introduce. The stored summary bullets carry em dashes (three of the six on the measured episode) and are rendered verbatim; the test scans JS literals, so this stays green and rule 8 forbids rewriting the payload |
| `test_plural_returns_null_on_a_null_count` | 1060 | `plural` at line 61 survives with its null guard intact. `n_total` is still only reached through `unscored`, and no template interpolates it bare |
| `test_the_board_scope_and_its_own_note_are_rendered` | 1115 | `res.scope`, `sc.excluded`, `res.record_note`, `res.mine_reason` and `function scopeLine()`. All five are on level 1's header by design. `mine_reason` is null live, so the branch has to survive unexercised |
| `test_a_repeated_fpl_entry_is_named_as_a_repeat` | 1151 | `cardsByEntry` at 261, 661, 666, 667, 2842 and `.cx-samewho` in the CSS. With one card per fold, the map is built per fetched card and the warning can only fire once a second fold has opened. That reduction goes in the commit message |
| `test_a_failed_panel_body_is_folded_not_interpolated` | 1172 | `statusLine`, `failFold`, `failFold(rcErr`, no `errBox(rcErr)`, no `${rcErr}`, and `.cx-raw` in the CSS. All survive |
| `test_no_raw_html_from_model_or_panel_output` | 510 | not creators-specific and it covers `ALL_JS`. Every title, bullet and quote at levels 2 and 3 is third-party prose and goes through `textContent`. A single `innerHTML` in the three levels is a defect |

**Expected contract updates.** Each is a number or a clause, never a deleted
rule, and each lands in the same commit as the code with the docstring saying
what replaced it.

| test | line | why it breaks | the exact edit |
|---|---:|---|---|
| `test_every_panel_script_is_rendered_by_some_view` | **65** | both halves. `unknown` fires on the first `runPanel("creator_episodes", ...)`; `unrendered` fires if `creator_detail` loses `creators.js:3300` | **no test edit.** Commit 1 adds the two `Panel(...)` entries and A's level-2 fold keeps `creator_detail` called. The test is the gate, not the casualty |
| `test_an_unscored_record_falls_back_to_the_payloads_reason` | 1073 | `assert src.count("unscored(") >= 5`. The five sites are 183, 2396 (`rcChip`, kept), 2628 (`renderReportCards`, deleted), 2685 (`reportCard`, deleted), 3448 (`recordLine`, kept) | `>= 3`. The rule, that every site goes through the helper rather than formatting its own count, is unchanged |
| `test_the_team_verdict_is_gated_on_quotable_not_on_a_null_boolean` | 1081 | `assert "const one = p => p.quotable" in src`, which is at 2743 inside `teamLine`, deleted | drop the `const one` clause. **Keep** `'fact("beats the baseline", p.quotable' in src`, at 2865 inside `reportBody`, and keep the negative clause that bans the bare `beats_baseline` boolean fallback. The floor still decides it |
| `test_the_under_floor_class_is_actually_styled` | 1093 | asserts `.cx-rc.few`, `.cx-rctag.few` and `.cx-rcard.few` in the CSS, and `el("button", "cx-rcard " + v.cls` in the JS. `reportCard` is deleted; `rcChip` and `rcTag` are kept | drop `.cx-rcard.few` from the tuple, delete `creators.css:890-897` in the same commit, and drop the `el("button", "cx-rcard "` assertion. **Keep** `.few in CREATORS_CSS`, `.cx-rc.few`, `.cx-rctag.few` and the negative `el("button", "cx-rcard " + coin(` clause |
| `test_one_creator_count_is_drawn_and_it_says_what_the_others_are` | 1106 | asserts `function cardCensus()`, `function censusLine()` and `censusLine()} · floor`, all deleted with the wall | rewrite against level 1: assert the creator count is drawn once from `res.creators.length` and that the negative clause `'plural((rc.cards || []).length, "creator")' not in src` survives. The defect it pinned, three counts for one population twelve pixels apart, is impossible with one card in view |
| `test_the_source_button_does_not_call_a_filtered_list_all_of_them` | 1124 | asserts `fetchable sources` at 873 and `const dropped = total - shown.length` at 870, both inside `sourceStrip`, deleted | move the test to whichever surface takes the console, or delete it and record in its place that the strip left the reading view. Do not silently drop the rule: a count that names a filtered set as the whole set is the defect |
| `test_the_record_strip_draws_no_permanently_empty_group` | 1132 | asserts `Interval below a coin flip`, `if (!list.length) return null;` and `of them over the floor`, all inside `recordStrip` at 2422-2475, deleted | **keep the two negative clauses**, `"Laggards" not in src` and `"Record leaders" not in src`, drop the three positive ones, and rename the test. A leaderboard of creators is what the RECORD column must not become, and those two clauses are the whole rule |
| `test_the_compact_team_line_is_capped_at_two_people` | 1143 | asserts `const SHOWN = 2;` at 2741 and `more, in the card`, both inside `teamLine`, deleted | delete with `teamLine`. `reportBody` draws every person, which is what the card is for, and the cap existed for the tile |
| `test_the_age_helper_is_the_shared_one` | 1160 | asserts the `fmtAge` import, the literal `fmtAge(iso)`, and no `yesterday` or `mo ago`. `relAge` at 40-46 is kept for `as_of`, so all four survive | **add** a clause: `ageDays` exists, and no publication date reaches `relAge`. The boundary is one sentence, a date on a thing the creator published is days, a stamp on a thing this app did is the shared span |
| `test_the_loading_affordance_has_one_glyph_and_one_capitalisation` | 1184 | `assert src.count("measuring the record…") >= 3`. After `_strip_comments` the file has three, at 774 (`honestyLine`, kept), 2430 (`recordStrip`, deleted) and 3441 (`recordLine`, kept) | `>= 2`. The three clauses that carry the rule, no capitalised variant, no trailing three dots, one glyph, are untouched |

`tests/unit/test_creator_panel.py`, `tests/unit/test_creator_report_card.py`
and the worktree's `tests/unit/test_creator_episodes.py` test the panels, not
the view, and no panel behaviour changes under this order.

### 7.5 Acceptance checks the orchestrator runs

Run all seven. Record the number, not a pass word.

**1. Mount cost.** Fresh load of `#creators` at 1280x900, then

```
const e = performance.getEntriesByType('resource').filter(r => /\/api\//.test(r.name));
[e.length, e.reduce((a, r) => a + (r.transferSize || r.encodedBodySize || 0), 0)]
```

| measure | before | required after |
|---|---:|---|
| API calls on mount | **40** | **at most 2** (`creator_board` and `GET /api/deadline`) |
| bytes on mount | **4,014,746** | **under 70,000** |
| `creator_detail` calls on mount | 29 | **0** |
| `creator_board` calls on mount | 7 | **1** |
| `creator_report_card` calls on mount | 1 | **0** |

**2. Height, 1280x900, everything folded.**

| measure | before | required after |
|---|---:|---|
| `document.documentElement.scrollHeight` | **2,041** | **under 1,400** |
| the tallest single `details` when opened | **3,997** | **under 400** |
| `#view` interactive elements, folded | **124** | **under 60** |
| distinct rendered font sizes in `#view` | **12** | **at most 6** |
| distinct rendered text colours in `#view` | **9** | **at most 5** |

**3. Height, a fresh 390x844 mount.** Measure after navigation, never after a
resize.

| measure | before | required after |
|---|---:|---|
| `document.documentElement.scrollHeight` | **2,919** | **under 1,700** |
| `document.documentElement.scrollWidth` | 390 | **390**, no page-level horizontal scroll |
| level 3 open, same viewport | not reachable | measured and recorded, no required ceiling |

**4. The age-format grep.** Publication dates are whole days everywhere.

```
grep -c "relAge(" web/dist/js/views/creators.js        # 21 today, expect at most 2
grep -c "ageDays(" web/dist/js/views/creators.js       # 0 today, expect at least 6
```

And over the rendered page at 1280, on each of the three levels:

```
(v.innerText.match(/\b\d+(\.\d+)?h(\s\d+m)?\b/g) || []).length   // 5 today, expect 0
(v.innerText.match(/\byesterday\b|\bmo ago\b/g) || []).length    // expect 0
```

The `as_of` line is the one exception and it stays on `fmtAge`, so an hour
unit inside the provenance footer is allowed and counted separately.

**5. Level 2 is never empty for a creator who has published.** For each of
the 29 names in `creator_board.creators[]`, open level 2 and count the rows.
Twenty-five of the 29 must be non-zero; the four with `n_items: 0` must show
the panel's own empty reason. Before this change the transcribed default
returns zero rows for **21 of 29**, including Fantasy Football Scout at 0 of
306. This is the check that catches the shared failure in 4.1.

**6. Level 3 is reachable by hash reload.** Set
`location.hash = "#creators/e/d26fac8a02a1928c1a1ea9f6"`, reload the page
from cold, and assert the level-3 head renders with the episode title, that
exactly one API call fires (`episode_summary`), and that the breadcrumb reads
`Let's Talk FPL` from `result.creator` with no second call. Then press the
browser back button and assert level 2 renders. Before this change the same
hash lands on the Dashboard.

**7. The gates.** Per rules 2, 3 and 4.

```
uv run pytest -q > out.txt 2>&1; echo "REAL_PYTEST_EXIT=$?" >> out.txt
grep "^FAILED" out.txt | sort > after.txt
diff baseline.txt after.txt
```

The 11 pre-existing `tests/audit/` failures may shrink. They may not grow and
their membership may not change. `tests/unit/test_web_contract.py` was at
exit 0 with 60 tests before this work, so any failure there is attributable
and must be either an update listed in 7.4 or a revert.

```
uv run python -c "from fpl_edge.platform import prose_style as ps; \
  t=open('web/dist/js/views/creators.js').read(); print(t.count(chr(8212)), ps.slop_findings(t))"
```

Zero and an empty list, and the same on the commit message.

---

## 8. What each design contributed that the others missed

* **A** measured the page it is replacing to the byte. Its network table, its
  page heights, its `creators.css` arithmetic, its report-card census and its
  `creator_detail {days: 1, limit: 1}` figure all reproduce exactly, which is
  why this build order uses A's ranges for level 1 and A's fetch parameters
  for level 2. A never ran the two panels it is designing against, and every
  one of its level-2 and level-3 errors comes from that.
* **B** read `creators.js` and the contract tests more carefully than anyone.
  Fourteen test diagnoses, every line number exact, including the detail that
  `_strip_comments` runs before the `measuring the record…` count. B also
  owns the only correct timestamp rule in the bake-off. B's page does not
  work as specified, and the two reasons are both one commit each to fix.
* **C** ran the panels. Every figure in its section 1.8 reproduces, including
  the ones that decide the design: 32 analysed rows with no transcript, 31
  with zero claims, 20 duplicate pairs all podcast plus YouTube, and four
  captain calls on a GW4 episode of which three are not about GW4. C is also
  the only design that opened `panels.py` and found the test that would have
  turned the whole suite red on the first commit of whichever design won.
