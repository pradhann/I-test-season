# Dashboard, design C: the subtraction

> Every number in this document was measured against the running app on
> 2026-09-18. Region heights and font sizes come from
> `getBoundingClientRect()` and `getComputedStyle()` on
> `http://localhost:8321/#home` at an emulated 1280x800 viewport. The payload
> figures come from `POST /api/scripts/dashboard_brief/run {}` at the same
> instant (GW5, entry 4490171, `as_of` 2026-09-12T18:17:08Z). Line ranges are
> `web/dist/js/views/home.js` at 2,344 lines and `web/dist/dashboard.css` at
> 558 lines. Where I could not measure something, it says so.

---

## 0. The one sentence

> **The dashboard already contains the answer, four rows of it, and puts it
> 806 pixels below the fold. This design deletes what sits on top.**

Measured: at 1280x800 the verdict card, the block that names the transfer,
the captain, the bench and the chip, begins at y=806. The viewport ends at
y=800. The first pixel of the answer to "what do I do this gameweek" is six
pixels out of sight, and the 800 pixels above it hold seven stat tiles, a
chip inventory, a monospace provenance line, a 307-pixel gap strip and a
223-pixel season-history card. None of those five things is a decision.

The design adds nothing. Every element it keeps is already rendered somewhere
in `home.js` today and carries the payload key it reads. The work is the
deletion list in section 5.

---

## 1. What is actually wrong today, measured

Seven defects. All seven are numbers off the running page, not opinions.

### 1.1 The page is 3,466 pixels tall and the answer starts at 806

Region tops and heights at 1280x800, in DOM order, from `#view`'s children:

| region | class | top | height | is it a decision |
|---|---|---|---|---|
| stat tiles | `.stats` | 67 | 115 | no |
| chip inventory | `.db-chipledger` | 190 | 19 | no |
| provenance banner | `.db-prov` | 213 | 17 | no |
| gaps | `.card.db-gaps` | 244 | 307 | gap, yes |
| season history | `.card.db-standing` | 567 | 223 | no |
| **the verdict** | `.card.db-verdict` | **806** | 383 | **yes, all four** |
| briefing | `.card.db-intel` | 1206 | 95 | no |
| squad pitch | `.card` | 1316 | 1205 | working |
| watch strip | `.db-watchstrip` | 2529 | 78 | no |
| moves to consider | `.card` | 2625 | 100 | no, it printed "No candidate cleared the gates" |
| solver | `.card.solver` | 2741 | 119 | working |
| signals | `.card` | 2876 | 499 | no |
| watch log | `.card` | 3391 | 44 | no |
| foot | `.db-foot` | 3451 | 82 | no |

Fourteen top-level regions, appended in one statement at `home.js:222-224`.
Total 3,466 pixels, 4.3 screens. The owner has to scroll one full screen to
reach the four lines the page exists to print.

At the narrowest width the harness would render, 446 CSS pixels, the same
page is **5,687 pixels tall and the verdict begins at y=1,685**, two and a
half screens down. At a true 390 pixels it is taller still, so 1,685 is a
floor, not a ceiling.

### 1.2 Fourteen distinct font sizes inside one view

Computed `font-size` over every leaf text node in `#view`, with the count of
nodes at each size:

```
 7.5px  45    9.5px   1   10px    5   10.5px  55   11px   65
11.5px  21   12px    14   12.5px 42   13px    38   13.5px  5
17px    11   19.5px   5   20px    1   22px    4
```

Eight of those (10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5) are body text. They
differ by half a pixel in four places. A half-pixel step is not a hierarchy,
it is a hierarchy that failed to be one, and it is the visible part of the
owner's word "fonts". `dashboard.css` carries 52 `font-size` declarations and
33 `font:` shorthands to produce them.

### 1.3 Eighteen distinct spacing values in one stylesheet

Every `padding`, `margin` and `gap` value in `dashboard.css`:

```
1px 2px 3px 4px 5px 6px 7px 8px 9px 10px 12px 14px 16px 18px 24px 28px 30px 60px
```

Eighteen values, ten of them under 11 pixels. Nothing aligns to anything
because nothing shares a grid. That is the owner's word "alignment", and it
is a stylesheet fact before it is a taste question.

### 1.4 The same sentence prints up to four times

Counted as substring occurrences in `#view`'s rendered `innerText`:

| string | times |
|---|---|
| `public picks` | 4 |
| `its moves were priced against a squad you no longer have` | 3 |
| `a deadline has passed since ,` | 3 |
| `best XI by consensus xPts` | 3 |
| `haul odds` | 3 |
| `Regenerate` | 3 |
| `B.Fernandes 5.8` | 3 |
| `Briefing outdated (written 8 Sept from 7 Sept data` | 2 |
| `close call` | 2 |
| `Re-run solve` | 2 |
| `5d 12h` | 6 |
| `18:17Z` | 8 |

The solver-stale sentence renders in the gap strip (`home.js:2261-2275`), in
the watch strip's utility row (`home.js:1136-1147`), and on the solver card
(`home.js:1787-1792`). The squad source renders in the provenance banner
(`home.js:450-464`), the gap strip (`home.js:2243-2258`), the pitch head chip
(`home.js:1443-1449`) and the pitch footer (`home.js:1502-1508`). The captain
close call renders in the verdict row (`home.js:610-614`) and again in the
captain table (`home.js:1325-1337`). Total rendered text: 5,992 characters
over 242 non-blank lines.

### 1.5 One broken sentence, printed three times

The page literally says:

> `transfer plan generated 2026-09-08 for GW4-8; a deadline has passed since ,  its moves were priced against a squad you no longer have.`

A dangling "since" with a comma and two spaces after it. The cause is
`noDash()` at `home.js:155-160`, which rewrites em-dashes at print time
because `brief.py` serves a reason string containing one. The house rule
(`fpl_edge/platform/prose_style.py`) forbids em-dashes in authored prose, and
the view is patching the payload instead of the payload obeying the rule.
The patch produces a broken clause, and prints it three times.

### 1.6 A 15-key payload block that nothing reads

`dashboard_brief` serves `suggested_xi` with 15 keys: `swaps`, `n_changes`,
`xi_codes`, `bench_codes`, `captain`, `captain_by_haul`, `captain_by_mean`,
`captain_numbers`, `your_captain`, `swap_delta_xpts`, `captain_delta_xpts`,
`total_delta_xpts`, `reason`, `source_panel`, `source_as_of`.

`grep -n suggested_xi web/dist/js/views/home.js` returns nothing. The block
is computed, serialised, shipped and ignored, because the pitch draws
`best_xi` instead. Three more keys are in the same state: `season`,
`entry_id`, `projection_gw`.

`thresholds` carries 21 gates. The view reads four of them
(`captain_close_call_xpts`, `move_cap`, `form_returns_margin`,
`form_xpts_margin`). The other 17 travel for nothing.

### 1.7 Thirteen chromatic tokens on one page

`dashboard.css` uses `--s1` (14 times), `--s2` (5), `--warn` (15), `--bad`
(10), `--good` (1), `--pitch` (1) and the five `--fx-d1..5` steps. Two of
those are diverging ramps that mean different things and sit four pixels
apart on the same card: the `pj-` ramp (blue above your XI median, orange
below) and the `fx-` FDR ramp (green easy, red hard) both appear inside one
`.pp-row` on every one of fifteen player cards. The page then has to print a
legend explaining that the first one does not mean what it looks like:

> "GK and budget defenders sit below the median by construction; the tier
> says who is cheap to upgrade, not who is failing." (`home.js:1516-1518`)

A colour that needs a disclaimer is a colour that failed.

**What did not reproduce.** The page does not blank on a failed panel: the
`tryPanel` memo at `home.js:100-114` and the `namedGap` helper at
`home.js:91-97` are correct and stay. The verdict's precedence text is served
verbatim and rendered verbatim (`home.js:802-806`), which is the contract
working. Neither is a defect and neither is deleted.

---

## 2. The budget, and what the other designs are allowed that this one is not

This design is the subtraction. It starts from `home.js` as it stands and
removes. It introduces no new payload field, no new panel, no new chart and
no new interaction idiom. Three hard caps, and what each one buys:

| cap | today | here | what it forces |
|---|---|---|---|
| visual regions above the fold at 1280px | 6 (`.stats`, `.db-chipledger`, `.db-prov`, `.db-gaps`, `.db-standing`, and 0 pixels of the verdict) | **4** | the answer is region 2, not region 6 |
| chromatic tokens beyond the FDR palette | 6 (`--s1`, `--s2`, `--good`, `--warn`, `--bad`, `--pitch`) | **1** (`--warn`) | colour means one thing: this is not what it looks like |
| body type sizes | 8 | **2** (13px, 11px) | emphasis comes from weight and ink tier, not size |

The five-region allowance is spent as four. A subtraction design that spends
its whole budget has not subtracted.

**What I refuse, and why.** No new visualisation. Design A and design B can
argue for one; this one cannot, because every pixel it spends is a pixel it
has not saved. No sparkline of rank, no captain scatter, no fixture ticker
inline. The Fixtures tab owns fixture shape, the Projections tab owns xPts
distribution, the Planner owns the horizon. The dashboard owns four
sentences.

**What I borrow, and from inside this repo only.** The `fx-` FDR ramp from
`fixtures.css:72-90`, unchanged and still the only multi-hue scale on the
page. The `namedGap` idiom from `home.js:91-97`. The `.tlabel` uppercase
label recipe from `app.css` (the xPoints toolbar), reused as the answer
column. The `cite` chip from `dashboard.css:33-35`, reduced to one per
region instead of 26 per page.

---

## 3. The field map: every payload key, and where it lands

Rule 9 applies without exception. Nothing on the page is computed in the
browser, and nothing is printed that the payload does not serve. The three
tables below account for all 31 keys of `dashboard_brief.result`, plus the
`squad_overview` fields the pitch reads and the two side calls.

### 3.1 Above the fold

| payload key | renders today at | region | what it prints |
|---|---|---|---|
| `result.gw` | `home.js:357` (stat tile) | 1 | `GW5` in the line |
| `result.deadline_utc` | not read by `home.js`; the shell fetches `/api/deadline` (`app.js:175-192`) | 1 | `deadline in 2 days`, read from the brief so the countdown and the numbers share one clock |
| `result.header.free_transfers` | `home.js:362-379` | 1 | `2 free transfers` |
| `result.header.free_transfers_state` | `home.js:363-364` | 1 | when `stale` or `missing`, the count is replaced by the word `unknown` and the reason, never by a digit |
| `result.header.free_transfers_as_of` | `home.js:372-373` | 1 | the age, in days, inside that reason |
| `result.header.bank_tenths` | served; `home.js:359` reads `squad_overview.bank_tenths` instead | 1 | `bank 0.3` |
| `result.as_of` | `home.js:2228` (cite chip) | 1 | `read 6 days ago`, the page's own age |
| `result.verdict.lines[]` | `home.js:537-784` | 2 | the four answers, one row each |
| `result.verdict.lines[].question` | `home.js:539` | 2 | the label column: TRANSFER, CAPTAIN, BENCH, CHIP |
| `result.verdict.lines[].rule` | `home.js:545-679` | 2 | selects the wording template; the payload carries no prose by contract |
| `result.verdict.lines[].pick` | `home.js:598-602` | 2 | the named player |
| `result.verdict.lines[].moves[]` | `home.js:547-556` | 2 | out to in, with faces |
| `result.verdict.lines[].chip` | `home.js:668-670` | 2 | the chip the plan spends |
| `result.verdict.lines[].numbers` | `home.js:542, 618-656` | 2 | at most three, in the order the rule names them |
| `result.verdict.lines[].state` | `home.js:706-724` | 2 | `stale` or `aging` as a `--warn` word at the end of the row |
| `result.verdict.lines[].dissent[]` | `home.js:478-520, 733` | 2 | one chip per dissenting voice, in its own currency, never summed |
| `result.verdict.lines[].source_as_of` | `home.js:706` | 2 | the row's own age in days, printed only when it differs from `result.as_of` by a day or more |
| `result.best_xi.n_differs` | `home.js:645-651` | 2 | the bench row's count |
| `result.best_xi.differs[]` | `home.js:646-655` | 2 | the names in and the names out |
| `result.best_xi.captain_candidates[]` | `home.js:604-628` and again at `home.js:1320-1379` | 2 | the top three, once |
| `result.best_xi.close_call` | `home.js:610` | 2 | the word `close call` in place of a pick |
| `result.best_xi.captain_lead_xpts` | `home.js:622-624` | 2 | the lead |
| `result.thresholds.captain_close_call_xpts` | `home.js:630, 1333` | 2 | the gate beside the lead |
| `result.alerts[]` where `kind` is `GAP` or `SOLVER` | `home.js:1134-1147, 2261-2275` | 3 | one row per gap |
| `result.alerts[].reason` | `home.js:1082-1084` | 3 | the served reason, verbatim, with the em-dash fixed in `brief.py` rather than at print |
| `result.alerts[].source_panel` | `home.js:1083` | 3 | which panel is behind |
| `result.alerts[].source_as_of` | `home.js:1083` | 3 | its age, in days |
| `result.alerts[].drill` | `home.js:1101, 2274` | 3 | the fix control |
| `result.solve.state` | `home.js:1751-1760, 2260` | 3 | the solver row's headline word |
| `result.solve.age_hours` | `home.js:1757` | 3 | converted to whole days for print, never shown in hours |
| `result.solve.generated_at` | `home.js:2262-2264` | 3 | the date behind the age |
| `result.solve.last_deadline_utc` | `home.js:259, 2311` | 3 | the comparison that makes a source stale |
| `result.xpts_as_of` | `home.js:2305-2312` | 3 | the consensus row, when it predates the last deadline |
| `result.xpts_source` | `home.js:2311` | 3 | the verbatim source sentence on that row |
| `result.squad_source.label` | `home.js:1444, 2245` | 4 | which fifteen this is |
| `result.squad_source.live` | `home.js:1443, 2244` | 4 | selects the live wording or the public-picks wording |
| `result.squad_source.picks_gw` | `home.js:1446, 2245` | 4 | the gameweek those picks are from |
| `result.squad_source.as_of` | `home.js:2251` | 4 | its age in days |
| `result.squad_source.fix` | `home.js:1448, 2252` | 4 | the one control on the row |

### 3.2 Below the fold

| payload key | renders today at | where it goes | why not above |
|---|---|---|---|
| `result.best_xi.xi_codes` / `bench_codes` / `formation` / `xi_xpts` | `home.js:1390-1478` | the lineup | the working behind the bench row |
| `result.best_xi.captain` | `home.js:1409-1411` | the lineup | the armband ribbon; the pick itself is region 2 |
| `result.xi_median_xpts` | `home.js:251, 407-413` | the lineup footer | an anchor for a ramp that section 5 deletes; it stays as one printed number |
| `result.team_fixtures[]` | `home.js:266, 1229-1251` | the lineup, opponent chips | fixture shape is the Fixtures tab's question |
| `result.fixtures_scale.domain` / `.unit` | `home.js:269-272` | the lineup legend | the `fx-` ramp domain |
| `result.squad_projection[]` | `home.js:268, 1252-1273` | the lineup, minutes chips | minutes are evidence, not a decision |
| `result.solve.plan.*` | `home.js:1731-1942` | the solver card | the working behind the transfer row |
| `result.verdict.precedence` | `home.js:802-806` | disclosure under region 2's own card, collapsed | it explains the four rows; it is not one of them |
| `result.standing.*` | `home.js:2165-2230` | deleted from the dashboard, see 5.1 | history, not a decision |
| `result.tiles[]` | `home.js:1944-2065` | deleted from the dashboard, see 5.3 | candidates, not decisions |
| `result.moves[]` | `home.js:1523-1624` and `home.js:547-556` | the verdict's transfer row only | it already renders there |
| `result.moves_suppressed` | `home.js:1615-1617` | the solver card footer | a count of things not shown |
| `result.suppressed_counts` | `home.js:2084-2098` | the solver card footer | same |
| `result.watch_log[]` | `home.js:2100-2126` | Pipelines tab | twelve check rows behind a disclosure nobody opens |
| `result.empty_kinds[]` | `home.js:1598-1600, 2099-2103` | the solver card footer | a served absence with a reason, worth keeping, not worth the fold |
| `result.sources_as_of` | `home.js:2337-2341` | the foot | five clocks; region 1 carries the one that governs |
| `result.notes` | not read | the foot | serve it or stop serving it |
| `result.p_haul_source` / `p_haul_generated` | `home.js:257-262, 2317-2324` | the foot | the page already omits every haul number when the simulation is old; a gap row for a number that never prints is a gap about nothing |
| `squad_overview.starters[]` / `.bench[]` | `home.js:1391` | the lineup | the working |
| `squad_overview.chips[]` | `home.js:418-448` | the lineup footer | inventory |
| `squad_overview.provenance_source` | `home.js:458, 1503` | region 4 already says it | one place |
| `GET /api/solve/status` | `home.js:248, 1735` | the solver card | running state and log tail |

### 3.3 Dropped

| payload key | renders today at | why |
|---|---|---|
| `result.suggested_xi` (15 keys) | nowhere | dead on arrival; `best_xi` supersedes it. Stop serving it, or serve it and draw it, but not both halves of neither |
| `result.season` | nowhere | never rendered |
| `result.entry_id` | nowhere | never rendered |
| `result.projection_gw` | nowhere | duplicates `result.gw` |
| `result.thresholds`, 17 of 21 keys | nowhere | the gate strings the payload already composes carry their own numbers; only the four the view interpolates need shipping |
| `result.tiles[].context` | `home.js:1955-1990` | the wording templates it feeds are deleted with the tiles |
| `result.tiles[].gate` | `home.js:2020-2026` | same |
| `result.alerts[].news` / `.status` | `home.js:1077-1080` | the availability letter on the player card carries the FPL `news` string in its title; the alert row that repeated it is deleted |
| `result.standing.gws[].bench_points` / `.hit_cost` / `.field` | `home.js:2213-2218` | dropped with the standing card |
| `GET /api/briefing` (the whole call) | `home.js:245-247, 810-1045` | see 5.2 |
| `GET /api/solve/transfer-plan` | `home.js:1639-1662` | see 5.9 |

Four network calls become two: `squad_overview` and `dashboard_brief`, plus
`/api/solve/status` only while a solve is running. `/api/briefing` and
`/api/solve/transfer-plan` leave the page.

---

## 4. The page

### 4.1 Above the fold at 1280

Content area is 1280 minus the 168px rail minus 44px of padding, 1068px wide.
The shell's topbar (`index.html:38-41`) is unchanged and occupies y=0 to 67.

```
┌ rail ─┐┌─ content, 1068px ───────────────────────────────────────────────────────────────────┐
│i-test ││ Dashboard        GW5 deadline in 2 days                            [Theme]           │  y 0
│Dashb. │├──────────────────────────────────────────────────────────────────────────────────────┤
│Planner││ GW5   2 free transfers, unknown: counted by a plan 10 days old   bank 0.3            │  region 1
│Projec.││ read 6 days ago                                          [ Refresh ]                 │  y 67, h 48
│Elite  │├──────────────────────────────────────────────────────────────────────────────────────┤
│Creator││ THIS WEEK                                                                            │  region 2
│Fixture││                                                                                      │  y 131, h 226
│Pipelin││ TRANSFER   no move named. No plan stands and nothing cleared a gate.                  │
│Chat   ││            [solver stale, 10 days]                                                   │
│Account││ ....................................................................................  │
│       ││ CAPTAIN    close call: B.Fernandes 5.8 vs Joao Pedro 5.6 consensus xPts               │
│       ││            lead 0.2, close-call gate 0.5    [creators: 4 named Haaland, not owned]    │
│       ││ ....................................................................................  │
│       ││ BENCH      1 locked starter not in the best XI: Verbruggen out, Kinsky in             │
│       ││            best XI 48.3 xPts                                                          │
│       ││ ....................................................................................  │
│       ││ CHIP       hold. No plan stands to ask.                                   [stale]     │
│       ││                                                              > how ties break         │
│       │├──────────────────────────────────────────────────────────────────────────────────────┤
│       ││ WHAT IS MISSING                                                                      │  region 3
│       ││ SOLVER     plan 10 days old, written 8 Sept for GW4-8       [ Re-run solve ]          │  y 373, h 128
│       ││ CONSENSUS  xPts 6 days old, read before the GW5 deadline    [ Pipelines ]             │
│       ││ PRICES     price_radar served nothing: counters reset at the deadline                 │
│       │├──────────────────────────────────────────────────────────────────────────────────────┤
│       ││ THIS FIFTEEN                                                                         │  region 4
│       ││ GW4 public picks, 6 days old. Transfers since are not in it.                          │  y 517, h 62
│       ││ The four rows above are computed on it.          [ Connect your FPL account ]         │
│       │├──────────────────────────────────────────────────────────────────────────────────────┤
│       ││ YOUR ELEVEN     3-5-2, best XI by consensus xPts, 48.3                                │  fold at 800
│       ││  ┌────┐                                                                               │
└───────┘└──────────────────────────────────────────────────────────────────────────────────────┘
```

Region tops and heights, estimated from the type scale in section 6 (13px at
1.45 line height is 19px per line, rows carry 8px of padding, cards carry
16px of padding and 16px of margin):

| region | content | height |
|---|---|---|
| 1, the line | one row, two lines at 13px and 11px | 48 |
| 2, this week | title 19, four rows at 2 lines each 4x46, disclosure 19, card padding 32 | 226 |
| 3, what is missing | title 19, three rows at 1 line each 3x27, card padding 32 | 128 |
| 4, this fifteen | two lines at 13px, card padding 32 | 62 |
| margins | 3 x 16 | 48 |
| **total** | | **512** |

512 of the 733 pixels between the topbar and the fold. The remaining 221
pixels are the first row of the lineup, which is the intended overflow cue:
the reader sees that the working continues and does not have to guess.

### 4.2 Above the fold at 390

At 390 the rail collapses to a horizontal strip (`app.css`, the 820px
breakpoint), content padding drops to 12px, so the content column is 366px.
The four regions stack in the same order. The answer rows lose their label
column and the label becomes a line above the answer, which is what
`dashboard.css:119-122` already does for `.vd-row` under 720px.

```
┌────────────────────────────────────┐
│ i-test  Dash Plan Proj Elite ...   │  rail strip
│ Dashboard   GW5 deadline in 2 days │  topbar
├────────────────────────────────────┤
│ GW5   2 FT unknown   bank 0.3      │  region 1
│ read 6 days ago       [ Refresh ]  │  h 66
├────────────────────────────────────┤
│ THIS WEEK                          │  region 2
│ TRANSFER                           │  h 322
│ no move named. No plan stands and  │
│ nothing cleared a gate.            │
│ [solver stale, 10 days]            │
│ ..................................  │
│ CAPTAIN                            │
│ close call: B.Fernandes 5.8 vs     │
│ Joao Pedro 5.6 consensus xPts      │
│ lead 0.2, gate 0.5                 │
│ [creators: 4 named Haaland]        │
│ ..................................  │
│ BENCH                              │
│ 1 locked starter not in the best   │
│ XI: Verbruggen out, Kinsky in      │
│ ..................................  │
│ CHIP                               │
│ hold. No plan stands to ask.       │
│ > how ties break                   │
├────────────────────────────────────┤
│ WHAT IS MISSING                    │  region 3
│ SOLVER                             │  h 186
│ plan 10 days old, 8 Sept, GW4-8    │
│ [ Re-run solve ]                   │
│ CONSENSUS                          │
│ xPts 6 days old, read before the   │
│ GW5 deadline        [ Pipelines ]  │
│ PRICES                             │
│ counters reset at the deadline     │
├────────────────────────────────────┤
│ THIS FIFTEEN                       │  fold at 844
│ GW4 public picks, 6 days old.      │
└────────────────────────────────────┘
```

At 390 the budget is spent on three regions above the fold and the fourth
sits at the fold line. The order is what protects it: a reader who scrolls
one thumb-length has all four. The comparison is the measured 1,685 pixels
they scroll today, and that measurement was taken at 446, so the real figure
at 390 is worse.

No horizontal scroll at any width. Measured today: `document.scrollWidth`
equals the viewport at 446 and four elements scroll inside their own
`.scroll-x` containers, which is correct and stays.

### 4.3 The order, and why it is not the current order

The owner named the order: transfer, captain, bench, chip, then the gaps.
Today the gaps render at y=244 and the verdict at y=806, so the page states
its excuses before its answer. The payload already carries the four
questions in the right order (`verdict.lines[0..3]` are transfer, captain,
bench, chip, set at `brief.py:2169-2422`), so the reordering is a move of
`host.append`, not a change of logic.

The risk of putting region 4 last is real and is in section 9.2.

---

## 5. The deletion list

This is the design. Fifteen named blocks, 880 lines of `home.js`, plus about
40 lines of declarations and wiring at `home.js:179-247`, which is 39 percent
of the file. Each entry gives the range, the measured cost, and the reason a
reader loses nothing they act on.

| # | block | lines | count | rendered height at 1280 | disposition |
|---|---|---|---|---|---|
| 5.1 | `renderStanding` | 2163-2230 | 68 | 223 | delete |
| 5.2 | the agent briefing | 810-1045 | 236 | 95 | delete |
| 5.3 | `renderTiles` and its templates | 1944-2065 | 122 | 499 | delete |
| 5.4 | `renderMoves` and its templates | 1523-1624 | 102 | 100 | delete |
| 5.5 | `renderWatchStrip` | 1109-1168 | 60 | 78 | delete |
| 5.6 | `renderWatch`, the watch log | 2067-2132 | 66 | 44 | delete |
| 5.7 | the stat tiles | 353-416 | 64 | 115 | reduce to a text line |
| 5.8 | `captainBlock` | 1321-1379 | 59 | 118 inside the pitch | delete |
| 5.9 | `unconstrainedLine` | 1639-1662 | 24 | 1 line | delete |
| 5.10 | the provenance banner | 450-464 | 15 | 17 | delete |
| 5.11 | `pjClass` and `tierWord` | 1176-1185, 1193-1201 | 19 | 0, colour only | delete |
| 5.12 | `pulsePitch` | 320-329 | 10 | 0, behaviour | replace with an anchor |
| 5.13 | `skeleton` and its eight calls | 164-175, 236-243 | 20 | 0, transient | replace with one line |
| 5.14 | `noDash` | 155-160 | 6 | 0 | delete, fix `brief.py` |
| 5.15 | the two legend paragraphs | 1510-1518 | 9 | 38 | delete |

### 5.1 The season-history card, `home.js:2163-2230`, 68 lines, 223 pixels

It renders `result.standing`: overall rank 336,710, four gameweek cells, and
a footer. It sits at y=567, directly above the verdict, and it is the second
largest thing above the fold.

It answers "how has the season gone". The dashboard's question is "what do I
do this gameweek". Those are different questions, and the history one is
answered better by the Planner's own horizon view.

It is also currently wrong in a way that matters above a decision. The GW4
cell prints a bold `0` with the subtitle "field average not published yet".
The manager scored zero points in GW4 by the page's own reading, directly
above the row that names this week's captain. A number that large and that
misleading has no business being in the reader's eye while they choose an
armband.

`result.standing` stays in the payload and moves to the Planner, which
already owns rank and horizon. The dashboard stops calling `renderStanding`.

### 5.2 The agent briefing, `home.js:810-1045`, 236 lines

The largest single block on the page, and today it renders 95 pixels
containing one summary line and zero items, because the briefing is outdated
and folds itself to history (`home.js:1002-1013`).

Three reasons, in order of weight.

**It is a second voice on a page whose job is one answer.** The file's own
header comment says so: "TWO VOICES, never merged" (`home.js:33-35`). Two
voices is a correct design for a research surface. On a decision surface it
is the reader doing adjudication the page was supposed to do. The verdict
block already adjudicates, by a printed precedence, deterministically.

**It costs a network call and a model pass.** `getJSON("/api/briefing")` at
`home.js:245-247` is one of four parallel calls. `generateIntel` at
`home.js:830-870` triggers `briefing_intel`, which the shared brief records
as one of exactly three token-spending paths in the repo. A button that
spends model tokens sitting 1,206 pixels up a decision page, next to a
button that spends two to five minutes of solver time, is two expensive
clicks in the reader's peripheral vision.

**It drags 39 lines of dedupe with it.** `briefingDupes` at `home.js:878-916`
exists only because the briefing and the signal tiles name the same players,
so the page cross-references them into "also a rule signal" chips. Delete the
second voice and the reconciliation machinery goes too, along with
`intelItem` (45 lines), `intelProvChip`, `intelNumChip`, `intelOutdated`,
`outdatedSummary`, `generateBtn` and `generateIntel`.

The briefing itself is not deleted from the app. `/api/briefing` and the
Chat tab keep it. What leaves is its slot on the front page.

### 5.3 The signal tiles, `home.js:1944-2065`, 122 lines, 499 pixels

Six tiles, capped by `thresholds.tile_cap`, with 22 more suppressed. Today
they print: Palmer 20.8 xPts over GW5-8 against Cherki's 13.2; Gibbs-White
5.6 next GW at 9.5 percent owned; Saka 20.3; Mbeumo 18.7.

Every one of those is a candidate, and the page says so in its own subtitle:
"rule-based voice; deterministic gates over the panels". A candidate is not
an answer to "what do I do this gameweek". It is an answer to "who should I
look at", which is the Projections tab's entire purpose, at full depth, with
sorting, with every player rather than six.

The tiles also carry the heaviest meta furniture on the page: a gate string
per tile kind (`home.js:2020-2026`), a cite chip per source
(`home.js:2015-2018`), a fold count, and a suppression count. That is four
layers of provenance on a card that recommends nothing.

`result.tiles` stays in the payload. Two consumers keep it: the Projections
tab, and the drawer, which is unchanged.

### 5.4 Moves to consider, `home.js:1523-1624`, 102 lines, 100 pixels

Today this card renders the sentence "No candidate cleared the coverage or
form gates today." and nothing else, because `result.moves` is empty.

When it is not empty, it renders `result.moves[]` as move cards. Those same
moves already render inside the verdict's transfer row: `brief.py:2242-2251`
sets the rule to `rule_moves_solver_stale` or `rule_moves_solver_missing` and
copies the move pairs into `verdict.lines[0].moves`, and `home.js:571-583`
draws them with the same out-to-in face strip. When the solver plan does
stand, the rule moves appear as dissent chips on the same row
(`brief.py:2229-2240`).

So the card is the third rendering of the same payload. Delete the card,
keep `moveFace` (used by the verdict and the solver card) and `gwsText`.
`moveSentence` and `moveEl` go with it, along with the suppression footer.

### 5.5 The watch strip, `home.js:1109-1168`, 60 lines, 78 pixels

Two parts, both duplicates.

Its utility row (`home.js:1134-1147`) prints the `GAP` and `SOLVER` alerts,
which is exactly what region 3 does, one screen higher. Measured: the
solver-stale sentence appears here for the third time.

Its watch list (`home.js:1150-1161`) filters `result.tiles` to
`template_gap` and `price_rise_target`, which 5.3 already deleted.

`claimFor` at `home.js:1048-1092` stays: it is the wording table keyed by
rule id that region 3 reads. `alertRow` at `home.js:1093-1108` stays in
reduced form as region 3's row. `watchTileRow` at `home.js:1109-1129` goes.

### 5.6 The watch log, `home.js:2067-2132`, 66 lines

Twelve check rows behind a `<details>` summary that reads "+22 cleared gates
suppressed, 12 checks, 3 clear, 5 firing, 4 gap, 05:59Z".

The four `gap` rows (`owned_price_flow`, `price_targets`, `creator_shift`,
`solver`) are the only ones a reader acts on, and three of the four already
surface in region 3 through `result.alerts`. The eight `clear` and `firing`
rows tell the reader that a check ran, which is a pipeline fact, and the
Pipelines tab is where pipeline facts belong.

The comment defending it is right about the principle and wrong about the
venue: "Absence of a signal above means checked-and-clear, never
didn't-look" (`home.js:2128-2130`). Region 3 keeps that promise by printing
the count of checks that ran in its own footer, one line, from
`result.watch_log.length`.

### 5.7 The stat tiles, `home.js:353-416`, 64 lines, 115 pixels

Seven tiles: GW5, bank 0.3, free transfers (printing a placeholder glyph
because the plan is stale), chip verdict hold, squad value 100.8, XI xPts
47.5, XI median 4.2.

Deleted outright:

- **GW5.** The shell already prints "GW5 deadline in 2 days" 40 pixels above
  it (`index.html:40`, `app.js:175-192`).
- **squad value 100.8.** It changes no decision this week. It is a season
  statistic and belongs with the standing card in the Planner.
- **XI xPts 47.5 and XI median 4.2.** Two numbers in the same unit at two
  different scales, side by side, that the page has to disambiguate with a
  label reading "captain not doubled" and a two-sentence tooltip ending "The
  Planner grid doubles it." (`home.js:402-409`). When the fix for a pair of
  adjacent numbers is a sentence explaining that one of them is eleven times
  the other, the pair is the problem. The median survives as one printed
  number in the lineup footer, where the thing it anchors lives.
- **chip verdict "hold".** It is `verdict.lines[3]`. Region 2 prints it as a
  sentence with its rule. A tile that says `hold` above a row that says
  `hold. No plan stands to ask.` is the same word twice.

Surviving into region 1 as text: `header.free_transfers` with
`free_transfers_state`, and `header.bank_tenths`. Two facts, one line, no
tiles. They are a budget, and a budget is a clause, not a dashboard widget.

### 5.8 The captain table, `home.js:1321-1379`, 59 lines

A three-row table under the pitch: candidate, consensus xPts, opponent. Its
rows are B.Fernandes 5.8 FUL (A), Joao Pedro 5.6 BRE (A), Isak 4.5 BOU (A).

The verdict's `mean_xpts_captain` branch at `home.js:604-628` already prints
all three names, all three numbers and all three opponents, from the same
`best_xi.captain_candidates` array, as one wrapped line of `numBits`. The
table adds a header row, a source paragraph and a close-call lead sentence
that `home.js:610-614` has already printed verbatim 500 pixels above.

Delete the table. Keep `oppText` at `home.js:1313-1319`, which the verdict
row calls.

### 5.9 The unconstrained line, `home.js:1639-1662`, 24 lines

One line, "if hits were free: N changes, N hits, +N xPts vs rolling", fetched
from a fifth endpoint, `/api/solve/transfer-plan`, on every view load.

It is a counterfactual about a plan the reader cannot execute. Its own title
string says where it belongs: "the Planner tab draws it into the grid with
one click" (`home.js:1658-1659`). Send it there, and the page drops an
endpoint.

### 5.10 The provenance banner, `home.js:450-464`, 15 lines, 17 pixels

`squad: public picks (published after the deadline) · GW4 picks · as of 18:17Z`

Region 4 says the same thing, in words, with the fix control attached, and
region 4 is load-bearing. Two renderings of one fact, 330 pixels apart, is
the defect in 1.4 in its purest form. The banner goes.

### 5.11 The `pj-` ramp, `home.js:1176-1185` and `1193-1201`, 19 lines

`pjClass` bins a player's xPts against the XI median into five classes, and
`tierWord` writes the same bin as words for the card's `aria-label`. The
classes are `dashboard.css:229-235`, blue above the median, orange below,
documented at `dashboard.css:5-13`.

Three reasons it goes, and the colour budget is only the third.

**It collides with the FDR ramp.** Both ramps sit inside the same 86-pixel
`.pp-row` on every card. The xPts chip is blue-to-orange and the opponent
chip is green-to-red, and neither legend is visible while you read the card.

**It encodes a comparison nobody acts on.** Being below your own XI median is
the definition of the bottom half of your XI. Six players are below it by
construction, every week, in every squad.

**The page knows it and apologises in print.** `home.js:1516-1518`: "GK and
budget defenders sit below the median by construction; the tier says who is
cheap to upgrade, not who is failing."

The xPts number stays on every card. It loses its tint. The median stays in
the footer as a printed number.

### 5.12 `pulsePitch`, `home.js:320-329`, 10 lines

Smooth-scrolls to the pitch and flashes up to fifteen cards for 1.6 seconds.
With the lineup below the fold and the verdict above it, a click on the bench
row scrolls the reader away from the answer they just read and flashes a
photo at them. Replace with a plain `#` anchor to the lineup section, which
moves the page without taking the reader's place away from them and needs no
`prefers-reduced-motion` branch.

### 5.13 The skeleton shells, `home.js:164-175` and `236-243`, 20 lines

Eight animated grey placeholders plus 16 lines of CSS and a keyframe
(`dashboard.css:37-52`). Measured panel duration for `dashboard_brief`:
`duration_ms` 4,590.

Region 1 prints one line instead: `reading squad_overview and
dashboard_brief`. It says which calls are in flight, which the grey blocks
never did, and it costs no animation frame.

### 5.14 `noDash`, `home.js:155-160`, 6 lines

Defect 1.5. The function exists because `brief.py` serves an em-dash inside
`solve.reason`, and the view rewrites it to a comma at print, producing "a
deadline has passed since ,  its moves were priced". Fix the string in
`brief.py` so it obeys `prose_style.py` at source, and the view stops
rewriting payload prose. This is the one change outside `home.js` and
`dashboard.css` that this design requires, and it is a one-line contract fix,
described in section 8.

### 5.15 The two legend paragraphs, `home.js:1510-1518`, 9 lines

The badge legend ("C captain, V vice, down-arrow price-fall risk, ! an FPL
availability flag") becomes the badges' own `title` attributes, which
`home.js:1288-1298` already sets. The median disclaimer goes with the ramp in 5.11.

### 5.16 What the deletions leave, and what leaves with them

`dashboard.css` loses these blocks, by the section comments at lines 15, 37,
151, 185, 326, 348, 444, 459 and 524: skeleton shells (37-52), the briefing
(151-183), alert rows (185-201), the watch strip (326-347), moves (348-356),
tiles (444-458), the watch log (459-473), the standing card (524-558), the
`pj-` bins (229-235) and the ramp documentation (5-13). That is roughly 210
of 558 lines. The pizza block (474-523) belongs to the drawer component and
is untouched.

Network calls: four to two. `/api/briefing` and `/api/solve/transfer-plan`
leave the page. `/api/solve/status` is called only when the solver card
renders a running state.

Interactive elements: 42 measured today, down to nine. Region 1 has Refresh.
Region 2 has four drillable rows plus the precedence disclosure. Region 3 has
at most three fix controls. Region 4 has one.

Cite chips: 26 today. One per region, four in total, each naming the region's
governing panel and its age in days.

---

## 6. Type and spacing

### 6.1 Two body sizes, and nothing else

| role | size | weight | colour token | source |
|---|---|---|---|---|
| answer, gap text, squad source | 13px / 1.45 | 400, or 650 for the pick | `--ink` | `app.css` `body` rule, unchanged |
| label, age, number strip, source name | 11px / 1.35 | 650, uppercase, `letter-spacing: .08em` for labels; 400 for ages | `--faint` for labels, `--muted` for ages | the existing `.provenance` and `table.data th` sizes in `app.css` |

Two sizes. Every other distinction is weight (400 against 650) or ink tier
(`--ink`, `--muted`, `--faint`, all of which hold 4.5:1 or better on
`--raised` by `app.css`'s own note at lines 15-17).

The card heading recipe is unchanged and collapses into the body size:
`app.css` `.card > h2` is already `13px`, uppercase, `--muted`. So THIS WEEK,
WHAT IS MISSING and THIS FIFTEEN cost no new size.

Outside the body scale and outside this design's scope: the shell's
`.topbar h1` at 19px and `.deadline` at inherited 13px, both in `app.css`,
both unchanged.

Numbers keep `font-variant-numeric: tabular-nums`, which `app.css` sets
globally on `body` at line 63, and the `--mono` family stays on the 11px meta
line only, never on the 13px answer. An answer a person reads is prose; a
figure a person compares is monospace. Today the page mixes them inside one
row (`.vd-nums` is `600 11.5px var(--mono)` at `dashboard.css:107`).

New tokens, two:

```
--t-body: 13px;    /* the app.css body size, named so it can be referenced */
--t-meta: 11px;    /* the app.css .provenance size, same */
```

Both go in `app.css` `:root` beside the colour tokens. They name values the
app already uses, so nothing changes visually at the moment they land, and
after that no stylesheet can invent 12.5px again.

### 6.2 A four-step spacing scale

Defect 1.3 is eighteen values. The replacement is four:

```
--sp-1: 4px;    /* inside a chip, between a label and its value */
--sp-2: 8px;    /* between rows, inside a row's padding */
--sp-3: 16px;   /* card padding, card margin-bottom, between regions */
--sp-4: 24px;   /* above a region title, and nothing else */
```

Every `padding`, `margin` and `gap` in the rewritten `dashboard.css` is one
of those four. `app.css` `.card` already uses `14px 16px` and
`margin-bottom: 16px`; the 14 becomes 16, which is the only visual change the
scale forces on a shared component, and it is one pixel per side.

The label column in region 2 is 84px wide, which `dashboard.css:92` already
uses for `.vd-row`, and 84 is not on the 4px scale but is a grid track rather
than a space, measured to fit the word TRANSFER at 11px uppercase with
`letter-spacing: .08em`. It stays as a named track width, `--db-label: 84px`.

Three new tokens in `dashboard.css` scope, four in `app.css`. Seven in total,
against eighteen ad-hoc spacing values and eight ad-hoc font sizes removed.

### 6.3 One accent

The FDR palette, `--fx-d1` through `--fx-d5` with their inks, from
`fixtures.css:72-90`, unchanged. It appears in one place on this page: the
opponent chip on each lineup card, below the fold.

Beyond it, one chromatic token: `--warn`. It means one thing, everywhere:
**this is not what it looks like.** It is the left rail of region 3, the word
`stale` at the end of a verdict row, and the availability letter on a player
card. Nothing else on the page is coloured.

Deleted from the page: `--s1` (the verdict's left rail at
`dashboard.css:85`, the `pj-` blue, the "fresh" chip), `--s2` (the `pj-`
orange), `--good` (the standing card's up-delta), `--bad` (the stale chip,
the injury letter), `--pitch` (the striped pitch background; the `.pitch`
class stays in `app.css` for the Planner, the dashboard's lineup sits on
plain `--surface`).

Severity that `--bad` used to carry moves to the word. A `stale` chip and a
`missing` chip are different words already; they do not also need different
hues. An injured player's letter is `i` and its title is FPL's own `news`
string, which is the only severity statement that can be checked.

---

## 7. Refresh

The owner's word was "proper refresh". Today the page has no refresh at all.
It has two buttons, and both of them are the expensive ones: `Re-run solve`,
whose own label says "takes ~2-5 min" (`home.js:1925`), and `Regenerate`,
which spends model tokens. The only way to get current panel numbers is a
browser reload, which re-runs all four calls, redraws 558 DOM nodes and loses
the reader's scroll position.

### 7.1 Which fields carry an `as_of`

Measured in the live payload:

| field | value at the measured instant | governs |
|---|---|---|
| `result.as_of` | 2026-09-12T18:17:08Z | the page, the oldest load-bearing source clock, computed at `brief.py:2543-2546` with `solve_plan` deliberately excluded |
| `result.sources_as_of.squad_overview` | 2026-09-12T18:17:08Z | the lineup, the bench row, the captain row |
| `result.sources_as_of.ownership_eo` | 2026-09-12T18:17:08Z | the ownership gates, all below the fold |
| `result.sources_as_of.fixture_board` | 2026-09-18T05:56:11Z | the opponent chips |
| `result.sources_as_of.projection_table` | 2026-09-12T18:17:25Z | every xPts on the page |
| `result.sources_as_of.solve_plan` | 2026-09-08T05:38:23Z | the transfer row and the chip row |
| `result.solve.generated_at` and `.age_hours` | 2026-09-08T05:38:23Z, 240.3 | the solver card |
| `result.solve.last_deadline_utc` | 2026-09-12T12:30:00Z | the staleness test for every other clock |
| `result.xpts_as_of` | 2026-09-12T18:17:33Z | the consensus gap row |
| `result.p_haul_generated` | 2026-08-19T16:01:02Z | the haul omission, now in the foot |
| `result.squad_source.as_of` | 2026-09-12T18:17:08Z | region 4 |
| `result.alerts[].source_as_of` | per alert | region 3 rows |
| `result.verdict.lines[].source_as_of` | per line | region 2 rows |
| `result.verdict.lines[].dissent[].source_as_of` | per voice | the dissent chips |
| `result.tiles[].source_as_of` and `.sources[].as_of` | per tile | deleted with the tiles |
| `result.watch_log[].as_of` | per check | moved to Pipelines |
| `result.best_xi.source_as_of` | 2026-09-12T18:17:08Z | the lineup header |
| `provenance.generated_at` | 2026-09-18T05:56:09Z | the foot only; it is when the panel ran, which is always "now" and therefore says nothing about the data |

### 7.2 Where the age is shown, and in what words

**Once per region, in days, never in hours.**

```
today                      the stamp falls on this UTC date
1 day ago                  exactly one whole day
N days ago                 N whole days, floor
no as-of served            the field is null
stamped ahead of this read the stamp is in the future
```

`fmtSpan` (`app.js:137-151`) is not called on this page. It produces "10d",
"5d 12h" and "9d 23h", three shapes, all of which appear on the live page at
once (measured in 1.4). A single shape in a single unit is the point: the
reader is deciding whether a number is old enough to distrust, and that
decision has never turned on twelve hours.

`solve.age_hours` of 240.3 prints as `10 days`. `xpts_as_of` prints as
`6 days`. `squad_source.as_of` prints as `6 days`.

The one remaining time figure in another unit is the deadline countdown in
the shell, "deadline in 2 days", which is time remaining rather than age. It
keeps hours inside the last day, because at that point hours are the
decision.

Placement:

- Region 1 carries `result.as_of` as `read N days ago`. This is the page's
  age and the reader's first and usually only freshness fact.
- Region 2 rows print their own `source_as_of` age **only when it differs
  from `result.as_of` by a whole day or more**. On the measured payload that
  means the transfer row and the chip row print `10 days` (they read
  `solve_plan`) and the captain and bench rows print nothing (they read
  `squad_overview`, which is the page clock). Four ages became one, and the
  one that prints is the one that differs.
- Region 3 rows print the age of the source that is behind, in the row.
- Region 4 prints `squad_source.as_of`.
- The foot prints all five `sources_as_of` clocks, in days, for anyone who
  wants them.

### 7.3 What the user presses

**One control: `Refresh`, in region 1.** It re-runs exactly the two panel
calls the page depends on, `squad_overview` and `dashboard_brief`, and
repaints all four regions in place. It does not reload the document, does not
lose scroll position, and does not touch the solver or the model.

While it runs, the button reads `refreshing` and region 1's second line reads
`reading squad_overview and dashboard_brief`. Measured duration for
`dashboard_brief` alone: 4,590 ms, so a spinner earns nothing a word does not.

On completion, region 1's age line updates and any region whose content
changed gets a one-frame `--line` border transition, which `app.css` already
defines under `prefers-reduced-motion: no-preference`.

On failure, region 1 prints `refresh failed: <the error message>` and the
regions keep the numbers they already have, with their existing ages. A
failed refresh never blanks a region and never silently shows old numbers
without their age, because every region already prints its age.

**The two expensive actions stay expensive and stay named.** `Re-run solve`
appears in region 3's solver row and on the solver card, with the words
`takes 2 to 5 minutes` beside it, which `home.js:1925` already does. The
briefing's `Regenerate` leaves the page with the briefing.

**What refresh cannot fix, it says so.** Pressing Refresh when the solver
plan is 10 days old returns a plan that is 10 days old, because
`dashboard_brief` reads an artefact. Region 3's solver row therefore carries
`Re-run solve` and not `Refresh`, and region 1's `Refresh` does not claim to
fix it. A refresh control that appears to promise fresh data and returns the
same stale artefact is worse than no control, which is the trap this design
is most at risk of falling into and the reason the two controls are in
different regions with different words.

### 7.4 What happens to a stale section

A source is **stale** when its `as_of` is earlier than
`result.solve.last_deadline_utc`. That test is already in the file at
`home.js:2306` for the consensus and `home.js:262` for the haul simulation,
and it is the right test: a deadline is the event that invalidates a squad, a
price and a projection at once.

A region whose governing source is stale:

1. keeps rendering its numbers, with the age in days printed on the row,
2. gains a `--warn` left rail, three pixels, the same rail region 3 carries,
3. gains a row in region 3 naming the panel, the age and the control,
4. never recomputes, substitutes or hides a number.

A **pick** whose source is stale keeps its `state` word at the end of the row
(`stale`, `aging`, `superseded`, `missing`), which `home.js:706-724` already
renders with a definition in its `title`. Those definitions are good and stay
verbatim.

---

## 8. Empty, stale and error states

Every state below is served by the payload or by a call failure. Nothing is
inferred in the browser.

### 8.1 Region 1, the line

| condition | what prints |
|---|---|
| `dashboard_brief` returns `{empty: true}` | `the brief answered with no data: <result.reason>` and the Refresh button. Regions 2, 3 and 4 print their own named gaps. |
| the call throws | `dashboard_brief did not answer: <message>` plus Refresh. The `tryPanel` 404 memo at `home.js:100-114` is unchanged, so a missing script is not retried on every render. |
| `header` absent | free transfers and bank print `unknown`, with `the brief served no header block` |
| `header.free_transfers_state` is `stale` or `missing` | the count is replaced by the word `unknown` and the clause `counted by a plan N days old`, never by a digit. This is today's behaviour at `home.js:362-379`, except that today it prints a placeholder glyph, which reads as a rendering failure rather than as a statement. |
| `deadline_utc` null | `no deadline served for GW5` |
| `result.as_of` null | `no as-of served` in place of the age |
| `result.as_of` ahead of now | `stamped ahead of this read` |

### 8.2 Region 2, the four answers

| condition | what prints |
|---|---|
| `verdict` absent | `No verdict served. The brief carries no verdict block; a backend gap, not a quiet day.` Today's wording at `home.js:797-800`, kept verbatim. |
| `verdict.lines` empty | the same named gap |
| `rule` is `no_move_named` | `no move named. No plan stands and nothing cleared a gate.` |
| `rule` is `no_captain_named` | `no captain named. Neither a plan nor a projection stands.` |
| `rule` is `no_bench_named` | `no bench read. The squad is unreadable.` |
| `rule` is `no_chip_named` | `hold. No plan stands to ask.` |
| `rule` is unrecognised | the rule id prints, bare. `home.js:679` already does this and it is correct: an unknown rule is a contract change, and printing its id is how the reader finds out. |
| `state` is not `fresh` | the state word at the end of the row, with its definition in the title |
| a line's `source_as_of` is a whole day or more older than `result.as_of` | the age in days on that row |
| `dissent` empty | no chips. Absence of dissent is not printed as "no dissent", because the row is already the pick. |

The card never hides a question. All four rows render every time, including
when all four say the same thing, because "nothing to do this week" is an
answer and a missing row is not.

### 8.3 Region 3, what is missing

Rows are built from the payload only, in this fixed order, capped at three:

1. `solve.state` not in (`fresh`, `aging`) becomes the SOLVER row.
2. `xpts_as_of` earlier than `solve.last_deadline_utc` becomes the CONSENSUS
   row.
3. each `alerts[]` entry with `kind` in (`GAP`, `SOLVER`) becomes a row, via
   `claimFor` (`home.js:1048-1092`).

| condition | what prints |
|---|---|
| no rows | the region is absent, and region 1 appends `no gaps` to its age line. A card that renders only to say it is empty is a card that should not render. |
| more than three rows | the first three, then `+N more, all listed below` linking to the solver card footer |
| `alerts[].source_as_of` null | `no as-of served` in place of the age, which the live `price_radar` gap needs: its `source_as_of` is null |
| `squad_source.fix` present | the fix control, which is the Account link plus the copyable command that `home.js:2234-2241` already builds |
| `empty_kinds[]` present | not a region 3 row. It is a served absence with a reason (`creator_shift`: "creator_board serves no formation change delta"), which belongs in the footer of the working, not in front of a decision. |

### 8.4 Region 4, this fifteen

| condition | what prints |
|---|---|
| `squad_source.live` true | `your live team, read N days ago` and no control |
| `squad_source.live` false | `GW<picks_gw> public picks, N days old. Transfers since are not in it. The four rows above are computed on it.` plus the fix control |
| `squad_source` absent but `sq.gw` is behind `brief.gw` | `The lineup shows GW<sq.gw> picks for a GW<brief.gw> decision. The brief served no squad_source block, so the fix is not known here.` This is today's fallback at `home.js:2253-2258` and it is correct. |
| `squad_overview` unreadable | `No squad read: <reason>`, and regions 2 and the lineup print their own named gaps |

Region 4 renders in **every** state, including the healthy one. It is not a
gap row. The reader has to know which fifteen the four answers were computed
on before they act on them, and that is as true when the answer is "your
live team" as when it is "GW4 public picks".

### 8.5 Errors

| failure | what the page does |
|---|---|
| a panel returns HTTP 404 | `tryPanel` memoises it (`home.js:100-114`), the region prints its named gap once, and no further calls are made for that script |
| a panel throws any other error | the region prints `<script> did not answer: <message>` with the Refresh control |
| both panels fail | regions 2, 3 and 4 each print their own named gap. The page never blanks, which is today's behaviour and the file's stated law at `home.js:45-46`. |
| `/api/solve/status` fails | the solver card below the fold prints `solve state unknown`; the four answers are unaffected, because `verdict` carries the solve state itself |
| a player photo 404s | the monogram fallback, unchanged (`home.js:1281-1286`) |

### 8.6 The one contract fix outside the view

`brief.py` serves `solve.reason` containing an em-dash, which produces the
broken sentence in 1.5. The fix: the reason string is composed without a dash
in `brief.py`, so it satisfies `prose_style.normalize_prose` at source. Then
`noDash` (5.14) is deleted rather than kept as a patch. This is the only
change this design asks of the panel layer, and it removes a defect rather
than adding a field.

---

## 9. Risks, and what a reader loses

### 9.1 Somebody uses the signal tiles, and this design takes them away

The strongest objection. The tiles are the only place on the dashboard where
a player the manager does not own is named with a number and a gate. Today
they name Palmer at 20.8 over GW5-8 against Cherki's 13.2, and 22 more are
suppressed. A manager who opens the dashboard on a Monday to see who is worth
looking at loses that, and the replacement (open the Projections tab, sort,
apply the same comparison by eye) costs a tab switch and a sort.

I accept the cost. The honest version is: this design makes browsing worse to
make deciding better, and if the owner's real weekly loop is browse-then-
decide rather than decide-then-verify, design C is the wrong design and A or
B will suit better. The measurement that would settle it is which tab the
owner opens second. I do not have it.

What softens it and does not remove it: `result.tiles` stays in the payload
and the Projections tab gets it, so nothing is lost from the app, only from
the front page.

### 9.2 The squad-source block is last, and it governs everything above it

Region 4 says the four answers were computed on GW4 public picks, six days
old, and it sits below all four of them. A reader in a hurry acts on a
captain pick before learning it was chosen for a fifteen they may no longer
hold.

I considered three placements and none is free:

- **First**, above the answers: it is the correct logical order, and it
  reinstates the defect this design exists to remove, which is furniture
  before answer.
- **Inside region 1**, as a clause: it fits, and it disappears. Region 1 is
  scanned, not read.
- **Last, at the fold**: chosen. The reader sees the answer, then sees what
  it was computed on, without scrolling.

The mitigation is weak and I will name it as weak: when `squad_source.live`
is false, region 4 takes the `--warn` rail, the same rail as region 3, so it
reads as a caution rather than as a footnote. That is one hue doing two jobs,
which the colour budget permits and does not make comfortable.

The honest failure mode: a manager connects their FPL account, the block
turns to "your live team", and it becomes furniture at the bottom of the
page that never changes. At that point the right move is to fold it into
region 1 as a clause, and a design that needs a different layout once the
data is healthy is a design with a seam in it.

### 9.3 Four answers that all say "nothing stands" look like a broken page

On the measured payload, region 2 reads: no move named, close call, one bench
change, hold. Two of the four are non-answers and a third is a hedge. With
the tiles, the briefing and the standing card deleted, the page above the
fold is four rows of "the data is not good enough to tell you", three gap
rows saying why, and one row saying which fifteen it used.

That is honest, and it may read as a dead page. Today the same payload fills
3,466 pixels, and the fullness is doing work: it makes the surface feel alive
while it answers nothing. Deleting it removes the comfort along with the
noise.

I think the honesty is correct and I am not certain the owner will. The test
is one week where the solver is fresh: region 2 then reads as a named
transfer with faces, a named captain with a number, a bench delta and a chip
call, and the page is four confident lines. If it still feels thin in that
state, the design is wrong.

### 9.4 One accent colour may be too few for the risk channel

Collapsing `--bad` into `--warn` means an injured starter and a six-day-old
projection carry the same hue, distinguished only by the word and the
position on the page. FPL status `i` on a starting player is a different
order of urgency from a stale consensus, and the current design encodes that
with `--bad` against `--warn` (`dashboard.css:192-193`, the P0 and P1 rails).

The defence is that severity survives in the word and the letter, and the
app's own rule is that a chip never carries meaning by colour alone
(`app.css:153`). The risk is that a reader scanning fast now has to read to
sort urgent from stale, which is exactly the work colour was doing.

If this breaks in use, the cheapest repair is to spend the second accent on
`--bad` and reserve it for FPL availability only. That is a one-token change
and it would not disturb anything else in the design, which is why I am
willing to ship the tighter version first.

### 9.5 The lineup below the fold makes bench inversions harder to check

The bench row says `1 locked starter not in the best XI: Verbruggen out,
Kinsky in`. To see the two goalkeepers side by side with their numbers, the
reader scrolls. Today they are in the same 1,205-pixel card.

The cost is one scroll. The benefit is that the other three answers are above
the fold at all. I take the trade, and I note that it is the only one of the
five risks where the loss is a real capability rather than a comfort.

### 9.6 What I could not verify

I did not measure the page at a true 390 pixels. The harness clamped to 446,
so every narrow-viewport figure in this document is a floor. I did not
measure a fresh-solver payload, because the stored plan is 10 days old, so
section 9.3's "four confident lines" state is reasoned from the rule ids in
`brief.py:2169-2422` and not observed. I did not measure how long a reader
spends on any region, so the claim that the stat tiles and the standing card
are not read is an argument about what they contain, not an observation of
use.

---

## 10. Build order

1. **The `brief.py` reason string** (5.14, 8.6). One line, removes a broken
   sentence that renders three times, and is independently correct whether or
   not this design ships.
2. **Reorder `host.append`** (`home.js:222-224`) so the verdict precedes the
   gaps and the standing card. One line. It moves the answer above the fold
   before anything is deleted, and it is the single highest-value change in
   this document.
3. **Delete 5.1 through 5.6**, the six whole sections. This is 654 of the 880
   lines and roughly 1,039 of the 3,466 pixels.
4. **Collapse the stat tiles into region 1** (5.7) and delete the provenance
   banner (5.10).
5. **The type and spacing tokens** (section 6), applied to what is left.
6. **The `Refresh` control** (section 7).
7. **The remaining deletions**, 5.8 through 5.15, which are cleanup once the
   page is short.

Steps 1 and 2 are reversible in one commit each and can land before the rest
of the design is agreed. If the bake-off picks A or B, both are still worth
having.
