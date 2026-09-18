# UI audit, every tab, measured

Written 2026-09-18 against the running app at `http://localhost:8321` and the
files in `web/dist/`. Every number below was measured for this document, and
the instrument is named beside it. Nothing is taken from a design document on
trust. The rules the audit scores against are in
`docs/platform/DESIGN_PRINCIPLES.md`, cited as R1 to R49.

`web/dist/js/views/home.js` and `web/dist/js/views/creators.js` are **being
rebuilt** by other agents while this was written. They are audited for the
record and their entries say so; the ranked fix list does not schedule work
inside them.

---

## 0. Instruments

1. **The live page.** One clean reload per tab at an emulated 1280x900,
   walking every element of `#view` for `getComputedStyle().fontSize`,
   `.fontFamily`, `.textAlign`, `.cursor`, and walking every `th`, `td` and
   `tr` for its text, class, `title`, `aria-sort` and handlers.
2. **The network.** `performance.getEntriesByType('resource')` after that
   same reload, split into `/api/` calls, `resources.premierleague.com`
   images and static assets, with `transferSize` for bytes and `responseEnd`
   for the moment the data landed.
3. **Layout shift.** `PerformanceObserver` on `layout-shift` with
   `buffered: true`, summed over entries with `hadRecentInput === false`.
4. **The stylesheets.** A script over `web/dist/*.css` and
   `web/dist/chat-app/assets/*.css` collecting every `font-size` value, every
   `px` length inside a `font:` shorthand, and every `px` length in a
   `padding`, `margin`, `gap`, `row-gap` or `column-gap`.
5. **The sources.** `web/dist/js/app.js`, `web/dist/js/views/*.js` and
   `web/dist/js/components/*.js`, read by line.
6. **Screenshots.** Headless Chrome, `--window-size=1280,1600`,
   `--virtual-time-budget=20000`, one file per tab under
   `/tmp/orch/ui_shots/`.

---

## 1. App-wide totals

| measure | today | rule |
|---|---:|---|
| distinct `px` font sizes declared across 12 stylesheets | **27** | R1 |
| the set | 7, 7.5, 8, 8.5, 9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 14.5, 15, 15.5, 16, 16.5, 17, 18, 19, 20, 21, 22, 36 | |
| distinct font families rendered | **2** (`-apple-system`, `ui-monospace`) | R2 |
| distinct `px` spacing values | **31** (27 positive, 4 negative: -1.5, -2, -4, -6, -8, -60) | R8 |
| drawer implementations | **4**, at 3 widths (460px, 560px, 600px) | R20 |
| sortable-header implementations | **4**, of which 3 set `aria-sort` and 1 does not | R11 |
| club-badge implementations | **2**, hitting two CDN sizes (`badges/50`, `badges/70`) | R36 |
| views not using `emptyBox` | **3** (`home.js`, `account.js`, `chat.js`) | R26 |
| JS shipped on every page load | **850,557 bytes** across `app.js`, 10 views and 4 components | R48 |
| CSS shipped on every page load | **245,189 bytes** across 11 stylesheets, all linked in `index.html` | R48 |
| chat bundle, additional | **254,772 bytes** (`index.js` 172,251, `server.browser.js` 70,435, `index.css` 12,086) | R48 |

The 27 sizes break down per stylesheet as: `dashboard.css` 16 (52
`font-size` declarations and 45 `font:` shorthands), `fixtures.css` 14,
`creators.css` 13, `template.css` 13, `app.css` 12, `chat-app/index.css` 13,
`pipelines.css` 10, `planner.css` 9, `template-tools.css` 8, `chatter.css` 7,
`account.css` 5, `clubmark.css` 3.

### 1.1 The four sortable-header implementations

| file | line | sets `aria-sort` | arrow | keyboard |
|---|---:|---|---|---|
| `views/fixtures.js` | 2256 | yes | persistent | yes |
| `views/template.js` | 2304 | yes | persistent `⇅` on inactive | yes, `tabIndex = 0` at 2307 |
| `views/pipelines.js` | 417 | yes | in the header text (`STALE ▼`) | yes |
| `views/xpoints.js` | 524-536 | **no** | `data-dir` read by `table.matrix th.sorted::after` (`app.css:293`) | **no** |

### 1.2 The four drawers

| file | line | class | width |
|---|---:|---|---|
| `js/components/playerdrawer.js` | 26 | `.drawer.pd-drawer` | `min(460px, 92vw)` from `app.css:283`, and the only one with a focus trap (line 50) |
| `views/template.js` | 317 | `.drawer` | `min(460px, 92vw)` |
| `views/fixtures.js` | 738 | `.drawer.fx-drawer` | `min(600px, 96vw)` (`fixtures.css:526`) |
| `views/creators.js` | 228 | `.drawer.cx-drawer` | `min(560px, 96vw)` (`creators.css:669`) |

There is no breadcrumb in any of them (R21).

### 1.3 Icons and emoji

Glyphs used as icons, per source file:

| file | glyphs |
|---|---|
| `views/template.js` | `× → ⇄ ⇅ − √ ∪ ≈ ≤ ≥ ▲ ▼ ○ ● ✓ ✕` |
| `views/creators.js` | `× ↗ − ≈ ► ◄ ◆ ○ ◐ ★ ✓ ✕` |
| `views/fixtures.js` | `× ↑ → ⇄ − ≤ ▲ ▶ ▼ ◀ ✕` |
| `components/chatter.js` | `× → ↗ ≤ ⊘ ▲ ▶ ▼ ▽ ◇ ★ ⚙ 🗣` |
| `views/template-tools.js` | `× → − ∪ ≈ ▲ ▼ ✓` |
| `views/pipelines.js` | `▲ ▸ ▼ ▾` |
| `views/planner.js` | `× → − ≤` |
| `views/home.js` | `× → ↓ − ≥` |
| `views/xpoints.js` | `▲ ▼ ✓` |
| `components/playerdrawer.js` | `× → ✕` |

Two true emoji, both in `components/chatter.js`: `🗣` at line 546 and `⚙` at
line 550, printed as the labels of the SAID and NOTICED rails (R40). Three
arrow families are in use for the same jobs (`↑↓→↗`, `▲▼▸▾`, `◀▶►◄`), and
three different characters serve as a close control (`×`, `✕`, and a `close`
word). Total SVG icons rendered across all nine tabs: 4 (R39, R41).

---

## 2. Per tab

### 2.1 Dashboard, `#home`, being rebuilt

Screenshot: `/tmp/orch/ui_shots/home.png`

| measure | value |
|---|---|
| document height at 1280x900 | 3,691px, `scrollWidth` 1,265 |
| distinct rendered font sizes | **14**: 11px on 68 nodes, 12.5 on 53, 10 on 51, 10.5 on 50, 7.5 on 45, 13 on 40, 11.5 on 22, 17 on 17, 12 on 15, 19.5 on 5, 22 on 4, 9.5 on 4, 13.5 on 3, 20 on 1 |
| families | `ui-monospace` 208 nodes, `-apple-system` 170 |
| header cells | 3: `CANDIDATE`, `CONSENSUS XPTS`, `OPPONENT` |
| header cells carrying metadata | `CONSENSUS XPTS` names the method in the label |
| number columns not right-aligned or not tabular | 0 of 3 |
| `aria-sort` on any `th` | **0 of 3** (R11) |
| rows that look clickable but are not | 0 |
| rows clickable with no affordance | 0 (no row on this tab opens anything) |
| ages in hours | **7** (`13h` x4, `23h` x3) against 4 in days |
| hand-rolled shared components | `ib-empty` at `home.js:1002` where `emptyBox` exists (`app.js:40`); `emptyBox` is called **0 times** in this view (R26) |
| emoji or mixed icon sets | `↓` and `→` rendered; source also carries `×`, `−`, `≥` |
| layout shift after first paint | 0.000 on a warm cache |
| mount requests and bytes | **5** `/api/` calls, **37,517 bytes**; 30 resources total |
| data complete at | **9,241ms** (`dashboard_brief/run`), the slowest tab that finishes (R45) |

The Projections defect has a twin here. One stat tile carries the label
`Σ XI XPTS, CAPTAIN NOT DOUBLED · CONSENSUS 18 SEPT`, built at
`home.js:402-404`, which renders the tile 360px wide against a 92px minimum
(`app.css:170`) and pushes a seventh tile onto a second row. The same text is
already in the tile's `title` at `home.js:405-407`. R6 puts the qualifier in
the `title` only and the label back to `Σ XI xPts`.

The stale-plan sentence still renders broken: `a deadline has passed since ,
its moves were priced against a squad you no longer have`, composed as a
literal in `fpl_edge/platform/scripts/brief.py`.

### 2.2 Planner, `#planner`

Screenshot: `/tmp/orch/ui_shots/planner.png`

| measure | value |
|---|---|
| document height | 3,981px |
| distinct rendered font sizes | **8**: 12.5 on 529 nodes, 13 on 71, 11 on 65, 11.5 on 32, 10.5 on 21, 12 on 11, 17 on 5, 9.5 on 1 |
| header cells | 23 across 3 `table.data` |
| header cells carrying metadata | `PLAN (GRID, CONSENSUS XPTS)` puts the method and the unit in the label; `OWN %` puts the unit in the label; `GW6` to `GW10` repeat across two tables with no way to tell which table a header belongs to |
| number columns not right-aligned or not tabular | 0 of 310 numeric cells |
| `aria-sort` | **0 of 23** (R11) |
| rows clickable with no affordance | 15 rows carry handlers, **0 rows** carry `tabindex`; the keyboard path is on individual cells (`planner.js:1544`, `:1587`) so a row is reachable only by tabbing through its cells |
| ages in hours | 1 |
| hand-rolled shared components | 4 separate `.filters` rows (`planner.js:284, 1190, 1402, 1627, 1668`) against R15's one; the stale-plan block uses `.err` (`planner.js:935`) for a state that is not an error (R27) |
| emoji | none rendered |
| layout shift | 0.000 |
| mount requests and bytes | **8** `/api/` calls, **281,313 bytes**, including `/api/solve/status` **five times** in one mount (`planner.js:723`, `:1790`) (R46) |

### 2.3 Projections, `#xpoints`, the tab the owner photographed

Screenshot: `/tmp/orch/ui_shots/xpoints.png`

| measure | value |
|---|---|
| document height | 4,029px |
| distinct rendered font sizes | **6**: 12.5 on 1,300 nodes, 12 on 60, 13 on 27, 11 on 25, 10 on 21, 10.5 on 4. The cleanest tab in the app on type |
| families | `ui-monospace` 1,062 nodes, `-apple-system` 375 |
| number columns not right-aligned or not tabular | **0 of 1,000** numeric cells. Alignment inside a cell is correct |
| `aria-sort` | **0 of 13** headers (R11). The sort direction is carried by `data-dir` and drawn by `table.matrix th.sorted::after` (`app.css:293`), so `table.data` alone shows nothing |
| rows that look clickable but are not | **1,200**. `table.data tr:hover td` (`app.css:145`) lights the whole row on hover, and only cell 0 of 13 opens anything |
| rows clickable with no affordance from the keyboard | **100 of 100**. `tr.tabIndex` is null on every row and the table contains **0** `button`, `a` or `[role=button]`, so the player drawer has no keyboard path (R18) |
| ages in hours | **3**, and the source chip row mixes units in one line: `fplreview · 3h 13m` beside `apex_airsenal · 18 days` (`xpoints.js:92-100`, `:124-125`) (R23) |
| emoji or mixed icon sets | `✓` rendered inside a header label; source carries `▲ ▼ ✓` |
| layout shift | 0.000 |
| mount requests and bytes | **3** `/api/` calls, **131,378 bytes**; 29 resources |
| data complete at | **3,531ms** (R45) |

**The header defect, measured.** Rendered header labels, with the column
width beside each:

| header | width | title already carries |
|---|---:|---|
| `PLAYER` | 170px | |
| `POS` | 48px | |
| `TEAM` | 54px | |
| `£` | 58px | |
| `OWN% NOW` | 77px | `share of managers owning the player at the last price ingest. A live snapshot, not a gameweek number.` |
| `GW6` | 43px | `GW6 projected points / 3 sources project GW6` |
| `GW7` | 43px | `GW7 projected points / 3 sources project GW7` |
| `GW8` | 43px | `GW8 projected points / 3 sources project GW8` |
| **`GW9 · 2 SRC`** | **99px** | `GW9 projected points / 2 sources project GW9, against 3 at GW6` |
| **`GW10 · 1 SRC`** | **106px** | `GW10 projected points / 1 source projects GW10, against 3 at GW6` |
| `SUM XPTS GW6-GW10` | 156px | `projected points added over GW6, GW7, GW8, GW9, GW10...` |
| `GW6 MAX-MIN` | 99px | `the highest source minus the lowest at GW6...` |
| `P(APPEAR) GW6` | 113px | `probability of appearing at GW6, averaged over the sources...` |

Five columns hold the same quantity, one decimal place. Three render at 43px
and two at 99px and 106px, which is 2.3x and 2.5x the width of their
neighbours for identical content. That is the misalignment in the owner's
screenshot: the heat tint fills the whole cell, so the GW9 and GW10 tint
blocks are visibly twice the width of the GW6, GW7 and GW8 blocks, and the
digits inside them sit at a right edge 56px and 63px further out.

The source count is built into the label at `xpoints.js:658-668`:

> `xpoints.js:658` reads `n_sources` for the gameweek off `gw_coverage`,
> `:660` sets `thin` when that count is below the anchor gameweek's count,
> and `:661` builds the label as `GW{g}`, then a tick when the gameweek is
> settled, then ` · {nsrc} src` when `thin` holds.

The count is **already** in the `title` on the next line
(`xpoints.js:666-668`). The label repeats what the tooltip says and pays two
columns of width for it.

Three further findings on this tab. The `P(APPEAR) GW6` column is cut off at
1280 (the screenshot shows `P(AP`), because `table.data th` is
`white-space: nowrap` (`app.css:138`) and 13 columns at these widths exceed
the content area. The heat tint is written as an inline
`color-mix(in oklab, var(--s1) N%, var(--surface))` at `xpoints.js:738`,
which is correct as a sequential ramp, but settled cells at
`xpoints.js:722-724` scale `--good` and `--bad` by the size of the delta,
which uses two reserved state colours as a diverging magnitude scale (R32).
And the row handler is attached with an inline style rather than the shared
class: `nameTd.style.cursor = "pointer"` at `xpoints.js:701`, where
`table.data td.clickable` already exists at `app.css:181-182`.

### 2.4 Ownership and EliteFPL, `#template`

Ownership has no separate route; it is served here by the `ownership_eo`
panel.

Screenshot: `/tmp/orch/ui_shots/template.png`

| measure | value |
|---|---|
| document height | 3,747px |
| distinct rendered font sizes | **13**: 12.5 on 1,777 nodes, 11 on 419, 11.5 on 180, 13 on 171, 10.5 on 108, 10 on 108, 12 on 53, 17 on 5, 14 on 4, 9 on 3, 19 on 1, 9.5 on 1, 19.5 on 1 |
| families | `ui-monospace` 1,453 nodes, `-apple-system` 1,378 |
| header cells | 3 in `table.data`, plus **1 table that is not `table.data`** (R10) |
| number columns not right-aligned or not tabular | 0 of 6 |
| `aria-sort` | **11**, set at `template.js:2304`. The best implementation in the app and the candidate to promote into `app.js` |
| rows clickable with no affordance | 198 rows, **150 with handlers**, **0 rows with `tabindex`**; the keyboard path is per cell at `template.js:1740` and `:2171` |
| ages in hours | 0 |
| hand-rolled shared components | its own drawer at `template.js:317`; 259 `.chip` elements, which is the heaviest chip use in the app |
| emoji or mixed icon sets | `→ ⇄ ⇅ − √ ∪ ≈ ≤ ≥ ▲ ▼ ○ ● ✓ ✕ ×`, 16 glyphs from 4 families |
| layout shift | 0.000 |
| mount requests and bytes | **4** `/api/` calls, **882,392 bytes**, of which `ownership_eo/run` is almost all; `/api/deadline` is fetched **twice** (R46) |
| data complete at | **3,395ms** |

### 2.5 Creators, `#creators`, being rebuilt

Screenshot: `/tmp/orch/ui_shots/creators.png`

| measure | value |
|---|---|
| document height, all folds closed | 1,912px |
| distinct rendered font sizes | **12**: 12, 11.5, 10, 9, 13, 9.5, 12.5, 15, 10.5, 11, 13.5, 16 |
| tables | **0**. The folded page has no tabular surface at all, so level 1 has no sortable columns (R10, R11) |
| ages in hours | 2 |
| hand-rolled shared components | its own drawer at `creators.js:228`; `drawerHead` at `creators.js:3405` is the helper worth promoting (R20) |
| emoji or mixed icon sets | `× ↗ − ≈ ► ◄ ◆ ○ ◐ ★ ✓ ✕`, 12 glyphs from 4 families |
| layout shift | 0.000 |
| mount requests and bytes | **40** `/api/` calls, **3,912,647 bytes** (3,821 KiB): `creator_detail/run` **29 times**, `creator_board/run` **7 times**, `creator_report_card/run` once, `/api/content/sources` once, `squad_overview/run` once, `/api/deadline` once |
| data complete at | **not within 30 seconds**. At 30s, 17 of 40 calls had returned, last `responseEnd` 29,102ms, 3,174,023 bytes transferred, summed call duration 192,781ms (R45, R46) |

### 2.6 Fixtures, `#fixtures`, the accepted quality bar

Screenshot: `/tmp/orch/ui_shots/fixtures.png`

| measure | value |
|---|---|
| document height | 2,356px |
| distinct rendered font sizes | **11**: 12.5 on 212 nodes, 11 on 86, 12 on 75, 10 on 54, 8.5 on 53, 13 on 43, 11.5 on 35, 7 on 24, 9.5 on 24, 10.5 on 19, 9 on 8 |
| tables | **0**. The board is a CSS grid, which is why the tab has no alignment complaint against it |
| `aria-sort` | set at `fixtures.js:2256` on the grid's own header row |
| ages in hours | 2 (`12h`, `23h`) against 3 in days (R23) |
| hand-rolled shared components | its own drawer at `fixtures.js:738` at a fourth width; `el("div", "empty")` at `fixtures.js:1479` instead of `emptyBox` (R26); `crest(t.code, t.short, "s16")` at `fixtures.js:2765` asks for a size class `clubmark.css` does not define, so that crest silently renders at the 20px default (R36) |
| emoji or mixed icon sets | `× ↑ → ⇄ − ≤ ▲ ▶ ▼ ◀ ✕`, 11 glyphs from 4 families |
| layout shift after first paint | **0.711 on a cold image cache**, 0.000 warm. The board renders nothing until `fixture_board/run` returns at 2,240ms, so the whole 2,356px of content is inserted after first paint (R30, R47) |
| mount requests and bytes | **3** `/api/` calls, **145,325 bytes**; 49 resources total including **20** club badges from `resources.premierleague.com` (R38) |
| data complete at | **2,588ms** (`squad_overview/run`; the board itself lands at 2,240ms) |
| what it gets right | FPL's own five FDR steps, identical in both themes (`fixtures.css:53-80`), the value printed under every colour, segmented controls in place of loose buttons, `crest()` for every club mark, a legend that names the unit |

### 2.7 Pipelines, `#pipelines`

Screenshot: `/tmp/orch/ui_shots/pipelines.png`

| measure | value |
|---|---|
| document height | 900px |
| distinct rendered font sizes | **10**: 12.5 on 48 nodes, 11.5 on 32, 11 on 23, 10 on 16, 10.5 on 6, 17 on 5, 12 on 2, 19.5 on 1, 15 on 1, 9 on 1 |
| header cells | 6: `FAMILY`, `TASK`, `LAST RUN`, `STATUS`, `STALE ▼`, `NEXT DUE` |
| header cells carrying metadata | `STALE ▼` carries the sort arrow inside the label text rather than as a separate glyph node (R6, R11) |
| `aria-sort` | **6 of 6**, at `pipelines.js:417` |
| rows clickable with affordance | **16 of 16** carry handlers and **16 of 16** carry `tabindex` (`pipelines.js:519`). The only view in the app where a row is reachable from the keyboard (R18) |
| ages in hours | **12**, the worst on any tab: `6h 33m`, `16h 3m`, `16h 48m`, `6h`, `1h 33m`, `7h 3m`, `12h 13m`, `16h 33m`, `17h 3m`, `17h 3m`, `17h 33m`, `19h 3m`, against 10 in days (R23) |
| hand-rolled shared components | **0** `.chip` elements; the status pills are local classes in `pipelines.css` |
| emoji or mixed icon sets | `▲ ▸ ▼ ▾`, two arrow families for two different jobs |
| layout shift | 0.000 |
| mount requests and bytes | **2** `/api/` calls, **88,836 bytes** |
| data complete at | **1,901ms**, the fastest tab |

### 2.8 Chat, `#chat`

Screenshot: **not captured.** Headless Chrome never reaches idle on this tab
because the view holds an open event stream, so `--virtual-time-budget` never
expires. Two attempts at budgets of 20,000ms and 6,000ms both hit a 75s wall
clock timeout. Retry once the stream is bounded, with
`--headless=new --window-size=1280,1600 --virtual-time-budget=20000
--screenshot=/tmp/orch/ui_shots/chat.png "http://localhost:8321/#chat"`.

| measure | value |
|---|---|
| distinct rendered font sizes | **11**: 12.5 on 280 nodes, 11 on 43, 12 on 39, 14 on 36, 13 on 24, 10.5 on 23, 16.5 on 13, 10 on 11, 11.5 on 10, **12.88** on 2, 21 on 2. `16.5px` and `21px` appear nowhere else in the app, and `12.88px` is a computed `em` value with no declared source (R1) |
| tables | **8**, and **none** is `table.data` (R10) |
| shared components used | **0** `.chip`, **0** `.empty`, **0** `.provenance`; 1 `.err` |
| ages in hours | 1, printed as **`603h`**, which is 25 days rendered as a raw hour count (R23) |
| emoji or mixed icon sets | `✕`, `→` |
| tokens | the bundle does use `app.css`'s custom properties: 24 references to `--line`, 14 to `--muted`, 13 to `--faint`, 12 to `--raised` and `--ink`, 11 to `--surface` |
| mount requests and bytes | **5** `/api/` calls and **162,014 bytes** on a warm bundle; a cold mount fires **33** calls for **3,460,060 bytes**, of which 22 are one `conversations/{id}/events` request per stored conversation (R46) |
| build | this is the only built artefact in `web/dist/`: 254,772 bytes from a React toolchain, against the zero-build decision in `DESIGN.md` section 2.2 |

### 2.9 Account, `#account`

Screenshot: `/tmp/orch/ui_shots/account.png`

| measure | value |
|---|---|
| document height | 900px |
| distinct rendered font sizes | **6**: 13 on 10 nodes, 12.5 on 7, 12 on 3, 14 on 1, 11.5 on 1, 11 on 1 |
| tables, chips, empty states | 0, 0, 0; 1 `.provenance` |
| `emptyBox` and `errBox` | `emptyBox` **0**, `errBox` 4 (R26) |
| ages in hours | 1 |
| emoji | none |
| layout shift | 0.000 |
| mount requests and bytes | **2** `/api/` calls, **1,684 bytes** |

### 2.10 Dossier and Players

There is no Dossier or Players route in `index.html`. The player level of the
fractal is `web/dist/js/components/playerdrawer.js`, opened from
`home.js:313`, `xpoints.js:770` and the creators consensus fold. It is the
fourth drawer (`playerdrawer.js:26`, `.drawer.pd-drawer`) and the only one
with a focus trap (`playerdrawer.js:50`), which makes it the right base for
the single drawer of R20. It has no breadcrumb, so a player opened from
Projections gives no path back to the creator or the fixture that led there
(R21).

---

## 3. The twenty highest-value fixes, in order

Effort is lines changed, counted as added plus removed. `home.js` and
`creators.js` are excluded from the schedule because they are being rebuilt;
the two entries that name them are for the rebuild agents to fold in.

| # | fix | principle | file and line | lines |
|---:|---|---|---|---:|
| 1 | Delete `· ${nsrc} src` from the gameweek header label. The count is already in the `th` `title` two lines below. Put the per-column marker on the cells instead: a 3px `--warn` dot at the top-right of every cell in a thin column, with `N sources` in the cell's `title`. The five gameweek columns then render at one width | R6, R5, R13 | `web/dist/js/views/xpoints.js:658-668` (label), `:709-742` (cell marker) | 14 |
| 2 | Delete the settled tick `✓` from the gameweek header label and draw it on the settled cells, which already carry the actual in bold | R6, R41 | `web/dist/js/views/xpoints.js:661` | 4 |
| 3 | Give the Projections row one affordance: move the handler from `nameTd` to `tr`, set `tr.tabIndex = 0`, `role="button"`, an Enter and Space handler, and replace the inline `style.cursor` with the existing `table.data td.clickable` rule | R18, R19 | `web/dist/js/views/xpoints.js:700-703`, `:747`; `app.css:181-182` | 16 |
| 4 | Promote `template.js`'s sortable header into `app.js` as `sortableTh(label, key, {dir, title})` setting `aria-sort`, `role="button"`, `tabIndex`, the Enter and Space handler and a per-column first direction; adopt it in `xpoints.js`, `pipelines.js`, `template.js` and `planner.js` | R11, R12 | new in `web/dist/js/app.js`; call sites `template.js:2295-2307`, `pipelines.js:417-430`, `xpoints.js:524-536`, `planner.js` header builders | 95 |
| 5 | Add `fmtAgeDays(iso)` beside `fmtAge` and route every view age through it; leave the hour branch of `fmtSpan` reachable only from `mountDeadline` | R23, R24 | `web/dist/js/app.js:135-160`; call sites `pipelines.js`, `xpoints.js:92-100, :124-125`, `fixtures.js`, `account.js`, `planner.js`, and the chat bundle's `603h` | 44 |
| 6 | Collapse the four drawers into one `openDrawer({title, crumb, body})` in `app.js`, built on `playerdrawer.js`'s focus trap, at `min(560px, 96vw)`, with `drawerHead` from `creators.js:3405` and a clickable breadcrumb | R20, R21, R22 | new in `web/dist/js/app.js`; `playerdrawer.js:26-60`, `template.js:317`, `fixtures.js:738`, `creators.js:228` | 130 |
| 7 | Declare the six type tokens in `app.css:13-26` and replace every `font-size` and `font:` shorthand in the 11 stylesheets with one of them | R1 | `web/dist/app.css` plus all 11 per-view stylesheets; `dashboard.css` carries 97 of the declarations | 260 |
| 8 | Declare `--sp1` to `--sp5` and replace every spacing length; the four negative values each get a comment saying what they pull against or go | R8 | `web/dist/app.css` plus all 11 per-view stylesheets | 210 |
| 9 | Serve player photos and club badges from the app at `/api/asset/player/{code}.png` and `/api/asset/club/{code}.png` with a 7 day immutable cache, and point `PHOTO`, `BADGE` and `BADGE_URL` at it. Removes 20 third-party round trips from a Fixtures mount and 100 from a Projections mount | R38, R47 | `web/dist/js/app.js:77-78`, `components/clubmark.js:23`; one route in `fpl_edge/platform/` | 60 |
| 10 | Make `crest()` the only club mark: delete the second badge builder in `playerCard` and its `onerror` removal, which is a layout shift by design | R36, R37 | `web/dist/js/app.js:106-110` | 10 |
| 11 | Render a skeleton at the final row count and row height before every panel call, so nothing moves when data lands. Fixtures measures 0.711 of layout shift on a cold cache today and is the reference case | R30, R47 | `web/dist/js/app.js` (a shared `skeleton(rows, cols)`), adopted in `fixtures.js`, `xpoints.js`, `pipelines.js`, `template.js`, `planner.js` | 80 |
| 12 | Replace the three arrow families and the fourteen geometric glyphs with one inline SVG set at 16px, `currentColor`, and delete the two emoji | R39, R40, R41 | new `web/dist/js/components/icons.js`; `chatter.js:546, :550`, and the glyph sites listed in section 1.3 | 140 |
| 13 | Load views with dynamic `import()` from the router and link each view's stylesheet beside it, instead of shipping 850,557 bytes of JS and 245,189 bytes of CSS on every page load | R48, R45 | `web/dist/index.html:7-19, :47-68`; `web/dist/js/app.js:196-213` | 40 |
| 14 | Move the Planner stale-plan block out of `.err` into the `.gap` state with a `stale · Nd` chip, so a red error box stops meaning a plan that is merely old | R27, R28, R29 | `web/dist/js/views/planner.js:933-945` | 12 |
| 15 | Collapse the Planner's four `.filters` rows into one, with the overflow behind a `more filters` disclosure, and print `N of M` under the table | R15, R16 | `web/dist/js/views/planner.js:284, 1190, 1402, 1627, 1668` | 45 |
| 16 | Fetch `/api/solve/status` once per mount instead of five times, and `/api/deadline` once instead of twice on EliteFPL | R46 | `web/dist/js/views/planner.js:723, :1790`; `web/dist/js/views/template.js` deadline call | 14 |
| 17 | Stop scaling `--good` and `--bad` by magnitude on settled Projections cells; keep the sign in the `.delta` chip and put the magnitude on the single `--s1` ramp the unsettled cells already use | R32, R34 | `web/dist/js/views/xpoints.js:717-726` | 10 |
| 18 | Give the Projections table a horizontal budget: `P(APPEAR) GW6` is truncated at 1280 wide. Once fix 1 returns 122px to the gameweek block, shorten the remaining three long headers to `SUM`, `SPREAD` and `P(APP)` with the full phrase in the `title` | R6, R7 | `web/dist/js/views/xpoints.js:673-687` | 8 |
| 19 | Wrap the Chat sub-app's 8 tables in `table.data` and route its empty, error and provenance surfaces through `emptyBox`, `errBox` and `provenance`; fold its three orphan font sizes (16.5, 21, and the `em`-derived 12.88) into the scale | R1, R10, R26, R27 | `web/chat-app/` source, rebuilt to `web/dist/chat-app/assets/` | 90 |
| 20 | For the rebuild agents: Dashboard's `Σ XI XPTS, CAPTAIN NOT DOUBLED · CONSENSUS 18 SEPT` tile label goes to the `title` that already holds it, and `ib-empty` becomes `emptyBox`; Creators' 40 calls and 3,821 KiB per mount go to one board call with the detail fetched on open | R6, R26, R46 | `web/dist/js/views/home.js:402-407`, `:1002`; `web/dist/js/views/creators.js` mount | 20 and the creators rebuild |

Fixes 1, 2, 3 and 18 together are the owner's screenshot. They are 42 lines
in one file and they return the Projections grid to five equal columns with
one obvious click target per row.

---

## 4. What the audit did not measure

Narrow widths. Every figure here is at 1280x900. The dashboard bake-off
records that a true 390px viewport is not reachable in its harness and the
creators bake-off records that it is; neither was re-tested here, so no claim
is made about the phone.

Cold-cache bytes for the FPL image CDN. `transferSize` reported 0 for the 20
Fixtures badges and the 100 Projections photos on every run after the first,
so the figures in section 2 are warm. Fix 9 removes the question by serving
them from the app.

`tests/unit/test_web_contract.py` was not run for this document. Every fix in
section 3 that deletes or renames a function needs the contract test walked
first, the way both bake-off reviews did it.
