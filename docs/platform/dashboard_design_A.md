# Dashboard, design A: the decision ledger

> Design proposal A for the Dashboard tab. Every pixel measurement below was
> taken in a real browser against the running server on 2026-09-18 at
> 06:00 UTC, at the viewport sizes named. Every payload value was read from one
> `POST /api/scripts/dashboard_brief/run` at the same time. Every line range
> was read out of `web/dist/js/views/home.js` at 2,344 lines. Where a statement
> is a design claim rather than a measurement, it says so.

---

## 0. The one sentence

> **The page is a ledger of four decisions in decision order, each one row:
> the question, the answer the payload supports, what the answer stands on,
> and the working folded underneath.**

That sentence replaces the current page's structure, not its contract. The
brief already computes one pick per question by a printed precedence. The
verdict block is the page. Everything else on the dashboard today is either
the working for one of those four rows, a blocker that stops a row answering,
or a number that belongs on a different tab.

A second sentence, because the first one is only half the design:

> **Density comes from four columns that line up down the page, not from
> panels beside each other.** One column, one reading order, five type sizes,
> one age format.

---

## 1. What is wrong today, measured

The owner's words: "so ugly, it is supposed to make my life easier. Proper
refresh, better alignment, fonts, intuitive usage." Each of the five
complaints has a measurement behind it.

### 1.1 The answer starts 806 px below the top of the page

Measured at 1280x800 against the live server, walking the dashboard host's
children and reading `getBoundingClientRect().top + scrollY`:

| order | element | top (px) | height (px) |
|---|---|---:|---:|
| 1 | `.stats` | 67 | 115 |
| 2 | `.db-chipledger` | 190 | 19 |
| 3 | `.db-prov` | 213 | 17 |
| 4 | `.card.db-gaps` | 244 | 307 |
| 5 | `.card.db-standing` | 567 | 223 |
| 6 | **`.card.db-verdict`** | **806** | 383 |
| 7 | `.card.db-intel` | 1206 | 95 |
| 8 | `.card` (squad, the pitch) | 1316 | **1205** |
| 9 | `.db-watchstrip` | 2529 | 78 |
| 10 | `.card` (moves to consider) | 2625 | 100 |
| 11 | `.card.solver` | 2741 | 119 |
| 12 | `.card` (signals) | 2876 | 499 |
| 13 | `.card` (watch log) | 3391 | 44 |
| 14 | `.db-foot` | 3451 | 82 |

Document height 3,573 px, which is 4.5 viewport heights at 800. The fold at
800 px lands inside `.db-standing`, six pixels above the verdict's first
pixel. **Not one of the four answers is above the fold.** The order in the
file's own header comment is honest about this: the verdict is fourth in the
declared page order, after the stat tiles, the chip ledger, the provenance
banner and the gaps.

Above that fold today: six stat tiles, a chip ledger, a provenance banner that
repeats the squad source, four gap rows, and a season-standing card with four
gameweek cells. 806 px of context in front of the decision.

### 1.2 The page is fourteen sections and one column with no shared grid

`web/dist/js/views/home.js` is 2,344 lines and 101,940 bytes, mounting
fourteen top-level children. The sections use five different row grammars:

- `.stat` tiles (flex, `.v` 17px mono over `.k` 10.5px uppercase)
- `.gap-row` (grid `84px minmax(0,1fr) auto`)
- `.vd-row` (grid `84px minmax(0,1fr)`, with `.vd-side` re-entering column 2)
- `.al.wl` (flex, `.al-kind` fixed at 72px)
- `.tile` (flex column inside a `repeat(auto-fill, ...)` grid)

Four of those five put a label in a fixed left column, and the widths are 84,
84, 72 and none. Nothing lines up between sections because nothing was asked
to. That is the alignment complaint, with numbers.

### 1.3 Twenty-seven time expressions in three vocabularies

Scanning the rendered `.content` text for time strings:

- 14 ages, in five shapes: `11h 32m`, `10d`, `5d 12h`, `9d 23h`, `1m`
- 11 bare UTC clocks, three distinct values: `18:17Z`, `05:58Z`, `05:38Z`
- 2 short dates: `18 Sep`, `19 Aug`

The age ladder is `app.js:137 fmtSpan`, deliberately shared, and it switches
format at 1 hour, at 48 hours and at 14 days. On a page where the load-bearing
ages are 5 days, 10 days and 29 days, the ladder spends its resolution where
the reader has no decision to make. `5d 12h` and `10d` are the same fact at
different precisions, printed one line apart.

### 1.4 There is no refresh

`home.js` has no refresh control. It has three job triggers:

- `rerunButton` at line 1783, `POST /api/solve` with `mode: transfers`, 2 to 5
  minutes
- `generateBtn` at line 823, `POST /api/pipelines/briefing_intel/run`, a model
  pass
- `cmdFix` at line 2145, which copies `uv run fpl myteam auth` to the
  clipboard and prints "then reload"

Every other number on the page changes only on a full page reload, and the
only instruction for that is the four words "then reload" beside a copied
shell command. The four panel calls at line 238 run once per view mount.

### 1.5 The pitch costs 1,205 px and 353 lines, and the bench row says the same thing in nine words

The squad card is the tallest element on the page by a factor of two and a
half. It renders `best_xi.xi_codes`, `best_xi.bench_codes`,
`squad_projection`, `team_fixtures`, `fixtures_scale`, the `own_price_fall`
alerts and `best_xi.captain_candidates`, across `home.js:1170-1522`.

The decision inside it, from today's payload, is
`verdict.lines[2]`: `rule: bench_inversion_applied`, `n_changes: 1`,
`swap_delta_xpts: 0.71`, and `suggested_xi.swaps[0]` names it exactly:
Kinsky in, Verbruggen out, bench 3.381 against starter 2.666, swing 0.715.
That is one row of a ledger. The pitch is 1,205 px of drawing around it.

### 1.6 Two voices, one dedupe pass run three times, one decision printed up to four times

`briefingDupes()` (`home.js:882-916`) exists to stop the model briefing and
the rule tiles printing the same player twice. It is called from
`renderIntel`, `renderTiles` and `renderMoves`, so it runs three times per
render and walks `intel.items x brief.tiles` plus `intel.items x brief.moves`
each time. A machine that removes duplication is evidence of duplication: the
same player can currently appear on the verdict row, on a signal tile, in the
briefing, and on a pitch card, four separate cards for one decision.

---

## 2. The ledger

### 2.1 The row

Five columns, one grid, every row identical. Widths are fixed so that the eye
reads down a column, which is the whole density argument.

| col | width | content | type |
|---|---|---|---|
| 1 | `--ledger-q` 104px | the question, one word, uppercase | `--ds-label` |
| 2 | `minmax(0,1fr)` | the answer, then one clause of qualification | `--ds-answer` then body |
| 3 | `--ledger-num` 208px, right-aligned | the numbers the answer rests on, one per line | `--ds-num` mono |
| 4 | `--ledger-conf` 156px | confidence word, the field that decided it, the age of the source in days | `--ds-meta` mono |
| 5 | 24px | the disclosure caret for the working | |

Column 3 is right-aligned and tabular (`font-variant-numeric: tabular-nums` is
already global at `app.css:65`), so `5.8`, `0.20` and `+1.3` sit on a shared
decimal edge down the page. Column 4 is left-aligned monospace, so
`no_move_named`, `best_xi.close_call` and `solve_plan 10 days` start on a
shared left edge. Those two edges are the alignment the owner asked for, and
they are the reason no second panel sits beside the ledger.

### 2.2 Reading order equals decision order

Five rows, in the order the owner named:

1. **TRANSFER** (`verdict.lines[0]`)
2. **CAPTAIN** (`verdict.lines[1]`)
3. **BENCH** (`verdict.lines[2]`)
4. **CHIP** (`verdict.lines[3]`)
5. **BLOCKERS** (the gaps strip, promoted to a ledger row)

The four decision rows come from `verdict.lines` in served order, never
re-sorted in the browser. The brief already emits them in this order
(`brief.py:2409`, `lines: [transfer_line, captain_line, bench_line,
chip_line]`), and the precedence that produced them is served verbatim in
`verdict.precedence`.

The blockers row is last because the owner put it last, and it is a row rather
than a banner because a blocker is a decision about the page: press a button
or accept that rows 1 through 4 are computed on old inputs. One exception
rides above the ledger, in the header line: `squad_source` when
`squad_source.live` is false, because that fact changes the meaning of all
four rows and cannot wait until after them.

### 2.3 Confidence, and the field that decides it

Four words. Each is decided by served fields, never by a score invented in the
browser, and the deciding field is printed next to the word.

| word | fires when | printed field |
|---|---|---|
| `none` | `verdict.lines[].rule` starts with `no_` | the rule id |
| `stale` | `verdict.lines[].state` in `stale`, `superseded`, `aging`, or `source_as_of` older than `solve.last_deadline_utc` | `<source_panel> N days` |
| `contested` | `verdict.lines[].dissent` non-empty, or `best_xi.close_call` is true | `best_xi.close_call` or the dissenting `voice` |
| `firm` | none of the above, and `squad_source.live` is true | `<source_panel> N days` |

Precedence when several apply: `none` beats `stale` beats `contested` beats
`firm`. Absence of an answer outranks a dated answer, and a dated answer
outranks a disputed one.

The word is a label over served facts, not a probability, and the row prints
the field beneath it so the reader can check the label against the payload in
one glance. §10 proposes serving the word from `brief.py` instead of deriving
it in the view, with the derivation kept as a fallback.

On today's payload the four rows read:

| row | rule | word | deciding field |
|---|---|---|---|
| transfer | `no_move_named` | `none` | `no_move_named`, `solve_plan 10 days` |
| captain | `mean_xpts_captain` | `contested` | `best_xi.close_call`, plus `creator_armband` dissent |
| bench | `bench_inversion_applied` | `stale` | `squad_overview 5 days`, `squad_source.live false` |
| chip | `no_chip_named` | `none` | `no_chip_named`, `solve_plan 10 days` |

### 2.4 The working block

One `<details>` per row, closed on load, opened by the caret or by clicking
the row. It renders inside the same column grid: a definition list whose
term column is `--ledger-q` wide, so the working lines up under the row it
belongs to rather than starting a new layout.

The working carries every payload field the row's answer was derived from,
each with its `source_panel` and its age in days. Nothing in a working block
is computed in the browser. Contents per row are listed in §3.

---

## 3. The field map: every key in the payload, placed

Rule 9 in both directions: nothing on the page that the payload does not
serve, and every key the payload serves is either placed or dropped with a
reason.

### 3.1 Above the fold

| payload key | where it renders | what it renders as |
|---|---|---|
| `gw` | header line | `GW5` |
| `deadline_utc` | header line | `deadline 18 Sep 17:30Z, in 11h 32m` (a countdown, see §6.4) |
| `squad_source.label` | header line | `public picks (published after the deadline)` |
| `squad_source.picks_gw` | header line | `GW4 picks` |
| `squad_source.live` | header line | decides whether the header line renders at all, and forces `stale` on every row that cites `squad_overview` |
| `squad_source.as_of` | header line | `5 days` |
| `squad_source.fix` | header line | the Connect control's title and its command fallback |
| `as_of` | header line, right | the ledger's own age, `5 days` |
| `verdict.lines[].question` | column 1 | `TRANSFER`, `CAPTAIN`, `BENCH`, `CHIP` |
| `verdict.lines[].rule` | column 2 wording key, column 4 when `none` | fixed templates keyed by rule id |
| `verdict.lines[].pick` | column 2 | the player name, with opponent from `team_fixtures` |
| `verdict.lines[].moves[]` | column 2 | `out.name` struck, arrow, `in.name` |
| `verdict.lines[].chip` | column 2 of the chip row | `Triple Captain` etc., or `hold` |
| `verdict.lines[].numbers.*` | column 3 | one number per line, each labelled with its unit |
| `verdict.lines[].state` | column 4 | feeds the confidence word |
| `verdict.lines[].dissent[]` | column 4 count, working block detail | `contested, 1 voice` |
| `verdict.lines[].source_panel` | column 4 | `solve_plan`, `squad_overview` |
| `verdict.lines[].source_as_of` | column 4 | `10 days` |
| `verdict.lines[].drill` | the row's click target | drawer, or tab hash |
| `header.free_transfers` | column 3 of the transfer row | `2 FT` |
| `header.free_transfers_state` | column 3 of the transfer row | when `stale`, prints `2 FT, plan stale` and the number is struck |
| `header.bank_tenths` | column 3 of the transfer row | `£0.3 bank` |
| `header.chip` / `header.chip_rule` / `header.chip_state` | chip row | the answer and its confidence |
| `best_xi.captain_candidates[]` | captain row column 3 (top one), working block (all three) | name, `xpts`, `p_haul` |
| `best_xi.captain_lead_xpts` | captain row column 3 | `lead 0.20` |
| `best_xi.close_call` | captain row column 4 | the `contested` decider |
| `best_xi.n_differs` / `differs[]` | bench row column 2 | `1 locked starter not in the best XI: Verbruggen` |
| `best_xi.formation` | bench row column 3 | `3-5-2` |
| `best_xi.xi_xpts` | bench row column 3 | `Σ 48.3 xPts` |
| `best_xi.reason` | bench row column 2 | printed when non-null, replaces the answer |
| `suggested_xi.n_changes` | bench row column 3 | `1 change` |
| `suggested_xi.swap_delta_xpts` | bench row column 3 | `+0.71 xPts` |
| `suggested_xi.captain_delta_xpts` | captain row column 3 | `+1.3 vs your armband` |
| `suggested_xi.your_captain` | captain row column 2 clause | `your armband is Isak` |
| `moves[]` | transfer row column 2, when the rule is `rule_moves_solver_*` | the move strip and its rule id |
| `moves_suppressed` | transfer row column 3 | `+N more cleared the gates` |
| `thresholds.captain_close_call_xpts` | captain row column 3 | `gate 0.50` beside the lead |
| `thresholds.solve_fresh_window_h` | transfer row working block | names what `fresh` means |
| `team_fixtures[].next.opponent` / `.is_home` | captain row column 2 | `FUL (A)` |
| `alerts[]` where `kind` is `SOLVER` or `GAP` | blockers row | one sub-row each, with `reason` and `drill` |
| `alerts[]` where `rule` is `availability` | blockers row | the flagged player, `status` and FPL's `news` verbatim |
| `watch_log[]` where `status` is `gap` | blockers row | one sub-row each, `check` and `detail` |
| `empty_kinds[]` | blockers row | one sub-row each, `kind` and `reason` |
| `solve.state` / `.reason` / `.generated_at` / `.age_hours` | blockers row and the transfer row's column 4 | the state word, the served reason, `10 days` |
| `solve.last_deadline_utc` | the staleness test in §2.3 | never printed on its own |
| `solve.next_deadline_utc` | header countdown fallback when `deadline_utc` is null | |
| `sources_as_of.*` | column 4 of the row that cites each panel | `N days` |
| `squad_source.fix` | blockers row | the Connect control |

Above-fold budget at 1280x800: header line 2 lines, four decision rows at
three lines each, blockers row with up to four sub-rows. §4 shows it measured
against the 800 px fold.

### 3.2 Below the fold, same page, same column grid

| payload key | section | reason it is below |
|---|---|---|
| `tiles[]` where `kind` is `xpts_standout`, `differential`, `fixture_turn` | SIGNALS | candidates for a decision, not a decision |
| `tiles[].gate` | SIGNALS | the gate string, printed once per kind as today |
| `tiles[].sources[]`, `.source_panel`, `.source_as_of`, `.context` | SIGNALS | each tile's citation |
| `tiles[].number.value` / `.unit` / `.window_h` | SIGNALS | the tile's number in its served unit |
| `tiles[].drill` | SIGNALS | opens the player drawer |
| `tiles[]` where `kind` is `template_gap`, `price_rise_target` | WATCH | a watch item, never this week's move |
| `alerts[]` where `rule` is `own_price_fall` | WATCH | price flow is a timing note on a move already decided |
| `suppressed_counts` | WATCH LOG summary | one line, as today |
| `watch_log[]` where `status` is `clear` or `firing` | WATCH LOG | absence-of-signal evidence, checked and clear |
| `standing.*` (`overall_rank`, `rank_move`, `total_points`, `vs_field_total`, `gws[]`) | WHERE THE SEASON STANDS | season context, no action this week |
| `xi_median_xpts` | the captain and bench working blocks | the anchor of a comparison, meaningless alone |
| `squad_projection[]` (`xmins`, `p_appear`, `n_sources`) | the bench and captain working blocks, for the named players only | minutes evidence for a swap |
| `projection_gw` | the captain working block | labels which gameweek the xPts are |
| `xpts_source` / `xpts_as_of` | the captain and bench working blocks, one line | the currency of every xPts on the page |
| `p_haul_source` / `p_haul_generated` | the captain working block | the reason a haul number is absent |
| `thresholds` (all 28) | the working block of the row each gate belongs to | gates belong beside the number they gated |
| `verdict.precedence` | one disclosure under the ledger, verbatim | the tie-break rule, read once |
| `solve.plan.*` (moves, `gain_over_roll`, `objective_mode`, `forecast_source`, `optimality_gap_pct`, `hits`, `hit_verdict`, `bank_after_tenths`, `free_transfers`, `alternatives`, `notes`, `bounds`, `captain`, `chip`) | the transfer row's working block, only when `solve.state` is `fresh` or `aging` | the solver's own currency, never beside the consensus numbers |
| `/api/solve/transfer-plan` `plan.unconstrained` | the transfer row's working block | the hit-capped alternative, as today |
| `/api/solve/status` | the blockers row | the running or failed state of a re-solve |
| `notes[]` | provenance foot | panel self-reports |
| `provenance.*` (`script`, `repo_sha`, `generated_at`, `params`, `as_of`) | provenance foot | as today |
| `season`, `entry_id` | provenance foot | identity, not a decision |
| `sources_as_of.*` (full list) | provenance foot | the clock list, as today |

### 3.3 Moved to its own tab

| key | where it goes | why |
|---|---|---|
| `GET /api/briefing` (the whole model briefing: `items[]`, `numbers[]`, `severity`, `outdated`, `outdated_reasons`, `rejected_n`, `rejected_reasons`, `model`, `generated_at`, `input_as_of`) | a Briefing tab | It is a second voice with its own age and its own failure mode. It produced one of today's four gap rows by being outdated, it forces `briefingDupes()` to exist, and its items are already folded as history when stale. On a decision ledger it is the working for a decision the ledger already states. |

### 3.4 Dropped from this page, with the reason

| key or element | reason |
|---|---|
| `best_xi.xi_codes` / `best_xi.bench_codes` | The pitch graphic is gone. The bench decision is `n_changes` plus the named swap; drawing eleven cards to say "one change" costs 1,205 px. The fifteen live on the Planner tab and in the player drawer. |
| `suggested_xi.xi_codes` / `.bench_codes` / `.swaps[].numbers` beyond the swing | Same. `swaps[0].in`, `.out` and `.numbers.swing` survive in the bench row's working block; the rest fed the drawing. |
| `team_fixtures[].horizon_attack_rank` / `.horizon_defence_rank` / `.horizon_gws` / `.labels[]` / `.next_gw_labels[]` | The Fixtures tab owns the horizon. The dashboard needs one opponent, for the captain row. |
| `team_fixtures[].next.attack_ease` / `.defence_ease` / `.attack_rank` / `.defence_rank` | These existed to colour the pitch's opponent chip. With no pitch there is no chip, and a ledger row that printed a goals-per-match ease number beside an xPts number would be two currencies on one line. |
| `fixtures_scale.*` | The domain of the ease ramp. Dropped with the ramp. The dashboard stops depending on `fixtures.css` tokens entirely. |
| `squad_overview.chips[]` | Folded into the chip row's working block: window, played, remaining. As a permanent top-of-page strip it says nothing in the weeks when no chip is playable. |
| `squad_overview.projected_xi_xpts`, `.squad_value_tenths`, `.bank_tenths`, `.gw`, `.provenance_source` as stat tiles | The six-tile `.stats` row is deleted. Bank moves onto the transfer row (it is the transfer budget), free transfers onto the transfer row, chip onto the chip row, `Σ XI xPts` and the XI median into the bench working block. A number that no row uses does not get a tile. |
| the `.db-prov` provenance banner | It prints `squad: <source> · GW4 picks · as of 18:17Z`, which is `squad_source` printed a second time, four pixels of new information. |
| `alerts[].priority` | The ledger's order is the decision order, not a priority order. Priority survives only as the sub-row order inside the blockers row. |
| `alerts[].players[]` / `.codes[]` avatars | Photos on an alert row are decoration; the name is the fact. Faces stay in the player drawer, which is where a face helps. |
| `suggested_xi.reason`, `.source_panel`, `.source_as_of` | Superseded by `best_xi`, which the bench row reads. Served, unused, and saying so here is the point of the table. |

Nothing in the payload is unaccounted for. The four calls become three:
`squad_overview`, `dashboard_brief`, `GET /api/solve/status`. The briefing call
leaves with the briefing.

---

## 4. Wireframe, 1280 px

Content column at 1280 is 1,068 px (1280 minus the 168 px rail minus 44 px of
`.content` padding). The fold at 800 px is drawn.

```
┌─ THIS GAMEWEEK ───────────────────────────────────────────────────────────────────────────────┐
│ GW5   deadline 18 Sep 17:30Z, in 11h 32m                          ledger read 5 days [Refresh]│
│ squad: public picks, GW4 picks, 5 days. Not the fifteen you hold for GW5.          [Connect]  │
└───────────────────────────────────────────────────────────────────────────────────────────────┘

 TRANSFER   No move named                             2 FT plan stale    none                  ▸
            No plan stands and nothing cleared        £0.3 bank          no_move_named
            a gate.                                                      solve_plan 10 days
────────────────────────────────────────────────────────────────────────────────────────────────
 CAPTAIN    B.Fernandes   FUL (A)                     5.77 xPts          contested             ▾
            Close call: 0.20 clear of Joao Pedro,     lead 0.20          best_xi.close_call
            under the 0.50 gate.                      gate 0.50          squad_overview 5 days
                                                      +1.3 vs Isak
   ┌ the working ─────────────────────────────────────────────────────────────────────────────┐
   │ candidate        consensus xPts   opponent    haul odds                                   │
   │ B.Fernandes               5.77    FUL (A)     not shown, simulation 29 days               │
   │ Joao Pedro                5.57    BRE (A)     not shown, simulation 29 days               │
   │ Isak                      4.47    BOU (A)     not shown, simulation 29 days               │
   │                                                                                           │
   │ your armband   Isak, 4.47 consensus xPts. Switching is worth +1.3, no transfer, no hit.   │
   │ dissent        creator_armband: 4 creators named Haaland, not in your fifteen.            │
   │                creator_board, today. Its own measure, printed, never summed.              │
   │ minutes        B.Fernandes p_appear 0.97, 3 sources. projection_table, 5 days.            │
   │ xPts source    provider consensus of 3 sources, GW5, the Projections tab's numbers.       │
   │                projection_table, 5 days.                                                  │
   │ haul odds      engine simulation, gw1_projection.parquet, 29 days. Older than the last    │
   │                deadline, so no haul number renders. No provider publishes one.            │
   │ gate           captain_close_call_xpts 0.50                                               │
   └───────────────────────────────────────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────────────────────────────────
 BENCH      Kinsky starts, Verbruggen benched        1 change           stale                  ▸
            One locked starter is not in the best     +0.71 xPts        squad_overview 5 days
            XI by consensus xPts.                     3-5-2  Σ 48.3
────────────────────────────────────────────────────────────────────────────────────────────────
 CHIP       Hold                                                        none                   ▸
            No plan stands to ask.                                      no_chip_named
                                                                        solve_plan 10 days
════════════════════════════════════════════════════════════════════════════════════════════════
 BLOCKERS   4 things stop this page answering. Each one names its fix.
            squad      GW4 public picks, 5 days. Rows above are computed on it.     [Connect]
            solver     Plan stale, written 8 Sept, 10 days. A deadline has passed
                       since, its moves were priced against a squad you no longer
                       have.                                                     [Re-solve]
            price      price_radar: the transfer counters reset between the two most
                       recent snapshots, so no comparable window exists yet.     [Pipelines]
            creators   creator_shift: creator_board serves no formation or
                       predicted-XI change delta, so the gate cannot be evaluated. [Creators]
- - - - - - - - - - - - - - - - - - - - - - fold at 800 px - - - - - - - - - - - - - - - - - - -
  how ties break  ▸                                              (verdict.precedence, verbatim)

┌─ SIGNALS ─────────────────────────────────────────────────────────────────────────────────────┐
│ 6 gates cleared. Rule-based, deterministic, every gate printed.                                │
│ xpts_standout   Palmer 20.84 xPts GW5-8        Cherki holds 13.19       projection_table 5 days│
│ ...                                                                                            │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ WATCH ───────────────────────────────────────────────────────────────────────────────────────┐
│ template gaps, price falls on players you hold, rise targets                                   │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ WHAT WAS CHECKED ────────────────────────────────────────────────────────────────────────────┐
│ 12 checks, 3 clear, 5 firing, 4 gap. +22 cleared gates suppressed at the cap of 6.  ▸          │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
┌─ WHERE THE SEASON STANDS ─────────────────────────────────────────────────────────────────────┐
│ 336,710 overall.  GW1 75 (+25)  GW2 107 (+26)  GW3 46 (-5)  GW4 0 (field not published)        │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
  dashboard_brief · 5084317 · 2026-09-18T05:55Z · source clocks: squad_overview 18:17Z ...
```

Above the fold, in the order read: what gameweek it is and when the deadline
is, whose squad this is computed on, the four answers, and the four things
that stop those answers being better. The ledger's four rows plus the header
occupy roughly 440 px at three lines a row; the blockers row with four
sub-rows adds about 200 px; the disclosure line closes it under 700 px, which
leaves the fold falling inside the blockers row rather than inside the
answers.

That is a design estimate from the type scale in §9, not a measurement: the
measured numbers in §1.1 are of the page as it stands today.

## 5. Wireframe, 390 px

At 390 px the rail already collapses to a horizontal strip
(`app.css:315`, `@media (max-width: 820px)`), and `.content` padding drops to
12 px, leaving 366 px. The five-column grid collapses to two lines per row:
line one is answer plus confidence word, line two is the numbers, wrapped and
still right-aligned to the same edge. Columns 1 and 5 become a single 88 px
gutter holding the question and the caret.

The measured baseline for comparison: at the narrowest viewport the preview
pane would render (446 CSS px, so a lower bound for 390), today's page is
5,897 px tall and the verdict's first pixel is at 1,685. The squad card alone
is 1,734 px.

```
┌──────────────────────────────────────────┐
│ GW5  deadline 18 Sep 17:30Z              │
│ in 11h 32m                     [Refresh] │
│ squad: public picks, GW4, 5 days         │
│ Not the fifteen you hold.     [Connect]  │
└──────────────────────────────────────────┘
 TRANSFER                                 ▸
   No move named                     none
   No plan stands and nothing
   cleared a gate.
                       2 FT plan stale
                             £0.3 bank
                     solve_plan 10 days
──────────────────────────────────────────
 CAPTAIN                                  ▸
   B.Fernandes  FUL (A)         contested
   Close call: 0.20 clear of
   Joao Pedro, under the 0.50 gate.
                            5.77 xPts
                            lead 0.20
                            gate 0.50
                         +1.3 vs Isak
                 squad_overview 5 days
──────────────────────────────────────────
 BENCH                                    ▸
   Kinsky starts, Verbruggen     stale
   benched.
   One locked starter is not in
   the best XI by consensus xPts.
                            1 change
                         +0.71 xPts
                  3-5-2   Σ 48.3 xPts
                 squad_overview 5 days
──────────────────────────────────────────
 CHIP                                     ▸
   Hold                           none
   No plan stands to ask.
                       no_chip_named
                  solve_plan 10 days
══════════════════════════════════════════
 BLOCKERS   4                             ▾
   squad    GW4 public picks, 5 days.
            Rows above are computed
            on it.            [Connect]
   solver   Plan stale, 10 days. A
            deadline has passed since.
                            [Re-solve]
   price    price_radar serves no
            comparable window yet.
                           [Pipelines]
   creators creator_board serves no
            change delta.   [Creators]
- - - - - - fold at 844 px - - - - - - - -
  how ties break                          ▸
┌─ SIGNALS ────────────────────────────────┐
...
```

At 390 the four answers and the first blocker clear the 844 px fold. The
numbers column keeps its right edge by becoming a right-aligned block under
the answer rather than a side column, so the decimal alignment survives the
collapse. That is the one place where this design's density argument is
weakest, and §11.6 says so.

---

## 6. Refresh

"Proper refresh" is three different verbs today wearing no labels. The design
separates them, names them, and makes each one report its own outcome.

### 6.1 Three verbs, three costs

| verb | control | what it calls | cost | what changes |
|---|---|---|---|---|
| **Re-read** | `[Refresh]` in the ledger header, and per-row | `POST /api/scripts/squad_overview/run`, `POST /api/scripts/dashboard_brief/run` | about 5 seconds (the brief took 4,926 ms on the run measured for this document) | the panels re-read the warehouse |
| **Re-run** | a blocker row's button, named for its task | `POST /api/pipelines/{task_id}/run`, polled with `GET /api/pipelines/{task_id}/run_state` | task-dependent, minutes | the warehouse gains new rows |
| **Re-solve** | `[Re-solve]` on the solver blocker | `POST /api/solve` with `mode: transfers`, polled with `GET /api/solve/status` | 2 to 5 minutes | `transfer_plan.json` is rewritten |

The distinction matters because of rule 5: panels are read-only over the
warehouse. A re-read moves a number only when a job has written since the last
read. Today the page offers no re-read at all, and the instruction for one is
the words "then reload".

### 6.2 A re-read always reports its outcome

The failure this prevents: a button that appears to work, spins, and leaves
every number identical, because nothing had written.

On press, the ledger header's age chip enters `re-reading`, and each row's
column 4 dims to 55% opacity. On settle, every row whose `source_as_of` moved
prints its new age; every row whose `source_as_of` did not move prints, for
six seconds, in place of the age:

```
re-read 06:04Z, squad_overview unchanged, still 5 days
```

then returns to `squad_overview 5 days`. The panels' own `as_of` values are
the evidence, so the message is a comparison of two served values, never a
claim about the server.

### 6.3 Which fields carry `as_of`, and where the age shows

The brief serves eight distinct clocks. Each has exactly one home:

| field | home |
|---|---|
| `as_of` (the oldest load-bearing contributing clock) | the ledger header, right |
| `sources_as_of.squad_overview` | column 4 of the captain and bench rows |
| `sources_as_of.solve_plan` | column 4 of the transfer and chip rows |
| `sources_as_of.projection_table` | the captain and bench working blocks |
| `sources_as_of.ownership_eo` | the SIGNALS section, per tile |
| `sources_as_of.fixture_board` | the SIGNALS section, per `fixture_turn` tile |
| `xpts_as_of` | one line in the captain and bench working blocks |
| `p_haul_generated` | the captain working block, as the reason haul odds are absent |
| `provenance.generated_at` | the provenance foot |

Five ages above the fold, against 27 time expressions on the page today.

### 6.4 The age format: days, never hours

One helper, added beside `fmtSpan` in `web/dist/js/app.js`, so the vocabulary
stays shared (the rule `tests/unit/test_web_contract.py:923` already pins for
the planner):

```
fmtAgeDays(iso) ->
  under 24 hours            "today"
  24 to 48 hours            "yesterday"
  2 days and over           "N days"   (whole days, rounded down)
  unparseable or null       "age unknown"
```

On today's payload: `fixture_board` today, `squad_overview` 5 days,
`projection_table` 5 days, `solve_plan` 10 days, `p_haul_generated` 29 days.
No `5d 12h`, no `9d 23h`, no `240.3h`.

The exact instant stays available in the element's `title`, as
`2026-09-12T18:17:08.996298+00:00`, so precision is one hover away and never
on the page.

**One deliberate exception, stated so it cannot read as drift.** The deadline
is a countdown, not an age, and the decision it governs is measured in hours:
`in 11h 32m`. It keeps `fmtSpan`, which is what the shell's deadline chip
already uses (`app.js:188`). Ages look backwards at data and round to days;
the countdown looks forward at a deadline and does not. `solve.age_hours`
(240.3 today) is converted to days for display and never printed as hours.

### 6.5 What happens to a stale section

Nothing is hidden and nothing is emptied. A row whose confidence word is
`stale`:

- keeps its answer in column 2, at full weight
- gains a 3 px left border in `--warn`
- prints the deciding field and age in column 4
- gains one clause under the answer, keyed by the reason: `computed on GW4
  public picks, 5 days` or `the plan predates the last deadline`

A row whose word is `none` gains a 3 px left border in `--bad` and prints the
served absence sentence keyed by its rule id, never blank space.

The existing precedent is right and survives: `brief.py` serves no plan body
at all when `solve.state` is `stale`, and `home.js:1852` renders state, reason,
age and a button in its place. The ledger does the same thing one row at a
time, which is why the solver card stops needing to be a card.

### 6.6 What the user presses, in order of cost

1. `[Refresh]` in the header. Five seconds. Always safe, always reports what
   moved.
2. `[Connect]` on the squad blocker, which is a link to the Account tab. The
   payload's own `squad_source.fix` command rides beside it for terminal use,
   with the copy control that exists today at `home.js:2145`.
3. `[Re-solve]` on the solver blocker. Two to five minutes, with the log tail
   ticker that exists today at `home.js:1830`, and a solve that survives a
   reload resumes its polling.
4. A named pipeline button on any other blocker, when the payload serves a
   task id for it (§10.1). Without a served task id the blocker offers a link
   to the Pipelines tab and nothing more, because the view must not guess
   which job writes which panel.

---

## 7. Every empty, stale and error state

Ages in days throughout, per §6.4.

### 7.1 The whole brief is unavailable

`dashboard_brief` returns non-200, or `{empty: true}`.

```
 THIS GAMEWEEK
   No ledger. dashboard_brief did not answer: HTTP 500.
   The four answers, the blockers and every number on this page ride in it.
   Nothing below was checked, which is different from every check coming back
   clear.                                                        [Refresh]
```

The SIGNALS, WATCH and WATCH LOG sections each print their own named gap with
the same reason, as `home.js` already does at lines 2029 and 2072. The season
standing and the provenance foot render if `squad_overview` answered.

### 7.2 The brief answered, the verdict block is absent

`brief.verdict` missing or `lines` empty. This is a backend gap, and the row
grid stays so the page does not reflow into something unrecognisable.

```
 TRANSFER   ,                                                     none
 CAPTAIN    ,                                                     none
 BENCH      ,                                                     none
 CHIP       ,                                                     none
            The brief carries no verdict block. A backend gap.
            dashboard_brief, 5 days.
```

### 7.3 A single row has no answer

Served today on rows 1 and 4. The rule id is the state.

| rule | row 2 renders |
|---|---|
| `no_move_named` | `No move named. No plan stands and nothing cleared a gate.` |
| `no_captain_named` | `No captain named. Neither a plan nor a projection stands.` |
| `no_bench_named` | `No bench read. The squad is unreadable.` |
| `no_chip_named` | `Hold. No plan stands to ask.` |

### 7.4 The squad is not the live team

`squad_source.live` is false. The header line renders, every row citing
`squad_overview` is forced to `stale`, and the blockers row carries the fix.
Today: `public picks (published after the deadline)`, `picks_gw 4`, 5 days.

### 7.5 The solver plan is stale, superseded or missing

Three states, three sentences, all served. No plan body renders in any of
them, which is the brief's own contract.

| state | transfer row | blockers row |
|---|---|---|
| `stale` | the served `solve.reason`, plus `solve_plan 10 days` | `Plan stale, written 8 Sept, 10 days.` with `[Re-solve]` |
| `superseded` | the served reason naming how many players differ | same, with `[Re-solve]` |
| `missing` | `No transfer plan artefact.` plus the served reason | the served reason, which already names the fix, with `[Re-solve]` |

When `solve.state` is `fresh` or `aging`, the transfer row's answer is the
plan's moves, the working block carries the plan body, and
`gain_over_roll` prints with `objective_mode` and `forecast_source` beside it,
never summed with a consensus number. That rule is `home.js:1877` today and it
is correct.

### 7.6 A number's source is older than the last deadline

`xpts_as_of`, `p_haul_generated` or any `sources_as_of` value older than
`solve.last_deadline_utc`. The number does not render; the absence does, with
the date.

Today: `p_haul_generated` is 2026-08-19, 29 days, older than the 12 Sept
deadline. The captain working block's haul column reads
`not shown, simulation 29 days` on every candidate row, and the working block
carries the served `p_haul_source` sentence.

### 7.7 A panel call errors or 404s

The memoised `tryPanel` pattern survives unchanged (`home.js:104-117`): a 404
is remembered so a missing panel is asked for once. The affected section
prints a named gap carrying the error text. The ledger itself only fails when
`dashboard_brief` fails.

### 7.8 A re-solve or a pipeline run fails

`GET /api/solve/status` returns `failed` or `error`. The blockers row's solver
sub-row gains a second line with the served log tail, and the failure is
reported only when it is newer than the plan that stands, which is the
existing rule at `home.js:1928`.

```
   solver   Plan stale, 10 days.                                [Re-solve]
            Last re-solve 18 Sept failed: <log tail, verbatim>
```

### 7.9 Nothing is wrong

`squad_source.live` true, `solve.state` fresh, no gap alerts, no gap
`watch_log` rows, no `empty_kinds`. The blockers row does not render at all,
and the ledger is four rows and a header. The section is absent rather than
printing a reassurance, which is the discipline the gaps strip already keeps
(`home.js:2321`, `gapStrip.hidden = true`).

### 7.10 The clipboard is unavailable

`navigator.clipboard.writeText` throws, as it does over plain HTTP on some
origins. The button text becomes `select the command and copy it`, which
`home.js:2154` already does and which stays.

---

## 8. What I delete from `home.js`, with line ranges

Current file: 2,344 lines. Deleted: 1,144 lines, 48.8%.

| lines | count | section | why it goes |
|---|---:|---|---|
| 353-417 | 65 | topbar stat row | Six tiles restating numbers that belong on the row that uses them. Bank and free transfers move to the transfer row, chip to the chip row, `Σ XI xPts` and the XI median into the bench working block. Gameweek and squad value move to the header line. |
| 418-449 | 32 | chip ledger strip | Folds into the chip row's working block. As a permanent strip it prints four counters in every week where no chip is playable. |
| 450-465 | 16 | provenance banner | Prints `squad_source` a second time. The header line already carries source, picks gameweek and age. |
| 810-1046 | 237 | the agent briefing | Moves to its own tab (§3.3). Two voices on one page is what `briefingDupes` exists to manage. |
| 878-916 | 39 | `briefingDupes` (inside the range above) | Deleted with the briefing. The dedupe machinery is a symptom of the collision, not a feature. |
| 1047-1169 | 123 | watch-strip wording and `renderWatchStrip` | `own_price_fall` alerts and the `template_gap` / `price_rise_target` tiles move into the below-fold WATCH section. The `SOLVER` and `GAP` utility row becomes the blockers row. `claimFor` survives, moved into the blockers renderer, because its templates are the alert contract. |
| 1170-1522 | 353 | the pitch | No pitch graphic. 1,205 px measured at 1280x800 to say what the bench row says in one line and one number. `pcard`, `oppChip`, `minChip`, `pjClass`, `tierWord`, `riskOf`, `captainBlock`, `renderPitch` all go; the captain candidates table survives as the captain working block's table, rebuilt on the ledger grid. |
| 1626-1943 | 318 | the solver card | The plan is the transfer row's answer and its working block. The card's state chip, stale sentence, re-run button and log ticker move to the blockers row. `rerunButton`, `solveRunningEl`, `startSolvePolling`, `lastLogLine` and `unconstrainedLine` survive as functions, relocated; `renderSolver` as a card renderer goes. |

Kept, relocated, or rewritten in place:

| lines | count | section | disposition |
|---|---:|---|---|
| 466-809 | 344 | the verdict | The survivor. Rewritten as five ledger rows on the shared grid, with the wording templates keyed by rule id unchanged. |
| 1523-1625 | 103 | moves to consider | Becomes the transfer row's working block when the rule is `rule_moves_solver_stale` or `rule_moves_solver_missing`. `moveSentence` and `moveEl` keep their rule-keyed templates. |
| 1944-2066 | 123 | signal tiles | Moves below the fold as SIGNALS, minus the two watch kinds. `tileEl` and `tileText` unchanged. |
| 2067-2133 | 67 | watch log | Moves below the fold, unchanged. |
| 2134-2162, 2232-2332 | 130 | gaps strip | Promoted to the blockers row. The row grammar changes to the ledger grid, the age format changes to days, the content stays exactly what `renderGaps` derives today. |
| 2163-2231 | 69 | season standing | Moves below the fold, unchanged. |
| 2333-2344 | 12 | provenance foot | Unchanged. |

### 8.1 The contract tests each deletion breaks

Six tests in `tests/unit/test_web_contract.py` pin structure that this design
removes. Each deletion carries its test edit in the same change, and the
suite gate diffs against the eleven known `tests/audit/` failures, which may
not grow.

| test | line | what it pins | replacement assertion |
|---|---:|---|---|
| `test_the_pitch_reads_only_fields_the_squad_schema_carries` | 94 | `pcard` reads only `squad_overview` fields | Retarget at the captain and bench row renderers, same subset check against the same schema. |
| `test_the_pitch_fallback_is_the_clubmark_discipline` | 294 | `pcard` flips one class, `.pp-face` is CSS-sized at 64px | Delete. The drawer keeps the photo discipline and has its own test. |
| `test_the_pitch_draws_one_lineup_with_no_toggle_and_no_arrows` | 528 | `best_xi`, `captain_candidates`, `close_call`, `n_differs`, `captain_close_call_xpts` all appear in `home.js` | Keep every one of those reads; the bench and captain rows read the same five fields. The test survives with its docstring rewritten. |
| `test_a_stale_or_missing_plan_renders_no_plan_content` | 551 | a nested `renderSolver` with the exact `fresh`/`aging` guard | Retarget at the transfer row's working-block renderer, keeping the guard verbatim. This is the most valuable test on the page and the rule must not move. |
| `test_an_outdated_briefing_folds_and_never_renders_items_as_current` | 604 | `renderIntel` in `home.js` | Move to a new briefing-view test, unchanged in substance. |
| `test_the_gaps_strip_is_payload_derived_and_names_every_fix` | 587 | `renderGaps` reads `squadSource.live`, `S.state`, `solveStatus`, `intelOutdated()`, `xpts_as_of`, `haulFresh`, and never types the fix command | Keep, minus `intelOutdated()` which leaves with the briefing. The "never types the fix command" assertion is the important half and stays. |

Two tests pass unchanged and should be read as constraints on the rebuild:
`test_the_dashboard_hardcodes_no_gate_thresholds` (line 317) and
`test_no_em_dashes_in_dashboard_strings` (line 613).

---

## 9. Type scale and spacing

### 9.1 Five type sizes, all already in `app.css`

No new sizes are invented. Each token names a size the app already uses, so
the ledger reads as the same product as the Fixtures and Pipelines tabs.

| new token | value | precedent in `app.css` | used for |
|---|---|---|---|
| `--ds-answer` | `600 17px/1.25 system` | `.stat .v` (17px, line 172) | column 2, the answer |
| `--ds-body` | `13px/1.45 system` | `body` (line 60) | column 2, the qualifying clause |
| `--ds-num` | `12.5px var(--mono)`, tabular | `table.data` (line 138) | column 3, every number |
| `--ds-meta` | `11px var(--mono)` | `.provenance` (line 120) | column 4, confidence and age |
| `--ds-label` | `650 10.5px/1.2`, `letter-spacing: .09em`, uppercase | `.tlabel` (line 243) | column 1, the question |

`.card > h2` (13px uppercase, `letter-spacing: .06em`) stays for the
below-fold section headings. `--ds-label` uses the `.tlabel` tracking rather
than the `h2` tracking because it is a row label, not a section title, and the
two must not look interchangeable.

Colour tokens: unchanged. `--ink`, `--muted`, `--faint`, `--line`,
`--surface`, `--raised` carry the whole ledger. `--warn` is the `stale` left
border, `--bad` the `none` left border, `--s1` the focus ring that
`app.css:107` already sets. No new colour is introduced, and the `--fx-d1`
through `--fx-d5` ramp leaves the dashboard with the pitch, so
`dashboard.css` stops depending on `fixtures.css`.

### 9.2 Spacing

Five steps, all multiples of four, all already present in the stylesheet as
literals.

| new token | value | precedent |
|---|---|---|
| `--sp-1` | 4px | `.stats` margin, `.gap-row` gap |
| `--sp-2` | 8px | `.toolbar` padding, `.chip` gap |
| `--sp-3` | 12px | `.al` padding, `.ib-item` gap |
| `--sp-4` | 16px | `.card` padding-inline (line 113), `.grid` gap |
| `--sp-6` | 24px | `.pitch.db-pitch2` gap |

Row rhythm: `padding: var(--sp-3) 0`, a `1px solid var(--line)` rule between
rows, `var(--sp-1)` between the lines inside a cell. The header line and the
blockers row are separated from the four decision rows by a 2px `--line` rule
rather than extra space, so the ledger stays one block.

### 9.3 Column tokens, the only new values in the set

| new token | value | why this value |
|---|---|---|
| `--ledger-q` | 104px | The longest question is `TRANSFER`, 8 characters at `--ds-label`, about 74px with tracking, plus `--sp-4`. The existing 84px columns (`.vd-q`, `.gap-kind`) clip at `TRANSFER` in uppercase tracking. |
| `--ledger-num` | 208px | The longest served number line is `lead 0.20 gate 0.50` at `--ds-num`, about 132px, plus `+1.3 vs your armband` at 170px. 208 holds both without wrapping at 1280. |
| `--ledger-conf` | 156px | The longest column-4 string is `squad_overview 5 days`, 21 characters at 11px mono, about 139px, plus `--sp-2`. |
| `--ledger-rule` | 3px | The left border width for `stale` and `none` rows, matching `.db-gaps` (`border-left: 3px`) and `.al.p0`. |

Grid: `grid-template-columns: var(--ledger-q) minmax(0, 1fr) var(--ledger-num)
var(--ledger-conf) 24px`. Below 820px, where `app.css` already restacks the
rail, it becomes `88px minmax(0, 1fr) 24px` with columns 3 and 4 wrapping to a
second grid line, right-aligned to the content edge.

---

## 10. Contract additions

All additive, all back-compatible, all small. The view falls back to the
behaviour described above when a field is absent.

### 10.1 `sources_refresh`, so the refresh buttons are payload-led

```
sources_refresh: { <panel name>: <registry task id> | null }
```

Today the view would have to guess which pipeline writes which panel, which is
exactly the guess rule 9 forbids. `fpl_edge/pipelines/registry.py` holds the
task ids and `POST /api/pipelines/{task_id}/run` already accepts any of them.
The brief knows which panel it called; the registry knows which task writes
it. Serving the map means a blocker row's button is named by the payload, and
a panel with no writer serves `null` and the row offers only a Pipelines link.

### 10.2 `verdict.lines[].confidence` and `.confidence_field`

```
confidence: "firm" | "contested" | "stale" | "none"
confidence_field: "<the payload path that decided it>"
```

The lattice in §2.3 is a threshold decision over served fields, and every
other threshold decision on this page already lives in `brief.py` with its
gate echoed. Deriving it in the browser puts one rule in the wrong file. The
view keeps the derivation as a fallback so the field can land after the
rebuild.

### 10.3 `squad_source.fix_kind`

```
fix_kind: "account_paste" | "command"
```

Today the view infers from the shape of `squad_source.fix` whether to offer
the Account link or only the copyable command. One enum removes the inference.

### 10.4 Nothing else

No new numbers, no new prose, no recommendation field. The brief's house rule
holds: wording lives in view templates keyed by `rule` and `kind`, and the
only strings this page prints from the payload are the ones the contract
already allows, which are verbatim source fields (`alerts[].news`), threshold
echoes (`tiles[].gate`), measured facts (`watch_log[].detail`,
`solve.reason`, `empty_kinds[].reason`), and the precedence constant.

---

## 11. Risks, and what would make me abandon this

### 11.1 Removing the pitch removes the only picture of the fifteen

The bench decision is spatial in the FPL client: managers drag cards. The
ledger replaces that with a sentence and a number, `Kinsky starts, Verbruggen
benched, +0.71 xPts`. If the owner cannot act on that without seeing eleven
cards, the design fails at its central claim, and it fails on the one row
where the answer is most often non-trivial.

The falsifiable version: after two gameweeks on the ledger, count the times
the Planner tab is opened immediately after reading the bench row. If it is
most weeks, the pitch was carrying the decision and it belongs back on the
page, which would mean design B or C.

### 11.2 On today's payload the ledger is mostly absence

Transfer `none`, chip `none`, captain `contested` and close, bench one change
against a five-day-old squad. Four rows, two of which say no answer stands. A
ledger makes that state loud and short, and 806 px of stat tiles and standing
cards currently soften it.

The honest reading is that the page has nothing to say this week because the
solver plan is 10 days old and the squad read is 5 days old, and the blockers
row says exactly that with two buttons. The risk is that the owner reads a
short page as a broken page. Mitigation is the blockers row's count in the
header of that row, `4 things stop this page answering`, which frames the
shortness as a cause rather than an emptiness.

### 11.3 Four confidence words are a new vocabulary to learn

`firm`, `contested`, `stale`, `none` are mine, not the payload's. The page
already asks the reader to hold `fresh`, `aging`, `stale`, `superseded`,
`missing` for the solver, plus `clear`, `firing`, `gap` for the watch log.
Adding a fourth vocabulary risks the words being skimmed as decoration.

Two mitigations, both cheap: the deciding field prints under every word, so
the word is checkable rather than trusted; and `stale` is deliberately the
same word the solve state uses, because it means the same thing.

If §10.2 is not built, the words are derived in the browser from served
fields, which is the kind of second implementation the brief's own contract
warns against. I would rather serve them.

### 11.4 Days-only ages lose a distinction that matters on deadline day

`fixture_board` was written 11 hours before this reading and `squad_overview`
5 days before it. Under `fmtAgeDays` the first reads `today`, and so would a
read from 23 hours ago. At 15:00 on a deadline day, a projection set written
this morning and one written yesterday evening are different things, and the
page would call both `today`.

The mitigation is the `title` carrying the exact instant, which is one hover.
The alternative is a fourth format for the under-48-hour band, which is the
ladder the owner asked to be removed. I take the loss and keep the single
format, and I will say plainly that this is the one place where the rule costs
information.

### 11.5 The rebuild is half a test rewrite

1,144 deleted lines break six contract tests (§8.1), and four of those tests
are pinning real rules rather than incidental structure. The most dangerous is
`test_a_stale_or_missing_plan_renders_no_plan_content`: its guard is what stops
a 10-day-old plan's moves rendering as advice, and retargeting it at a new
renderer is where a real regression could hide. The suite gate must run the
full pytest with the real exit code and diff against the eleven known
`tests/audit/` failures before and after, and the membership of that set must
not change.

### 11.6 The column alignment, the design's whole density argument, is weakest at 390 px

At 1280 the four aligned edges do the work of a grid. At 390 columns 3 and 4
wrap under the answer, and the right-aligned numeric edge survives only
because the numbers block is right-aligned to the content edge. If a number
line wraps inside itself, for example `lead 0.20 gate 0.50` breaking after
`0.20`, the decimal edge breaks and the ledger reads as a list of sentences
with numbers in them.

The fix if it happens is to drop the gate echo to the working block on narrow
viewports, which costs a served threshold from the row. I would take that
trade, and it is worth naming now rather than discovering it on a phone at
16:00 on a Friday.

### 11.7 Moving the briefing to its own tab may mean it is never read

The model briefing costs tokens (`briefing_intel`, `model="opus"`). Moving it
off the front page either saves that spend, because the owner stops
regenerating it out of habit, or wastes it, because the artefact is written and
never opened. Both outcomes are measurable from the pipeline ledger and the
route's own access, and either one is a clearer signal than the current state,
where the briefing is the third card on the page and one of its four gap rows.

---

## 12. What I borrowed

- **The brief's own precedence.** `verdict.precedence` already states the
  adjudication rule in one paragraph, and `verdict.lines` already emits one
  pick per question in the owner's order. This design is mostly the decision
  to believe that block and delete what disagrees with it.
- **The Fixtures tab's named-gap discipline.** `fx-gap` prints which data is
  missing and why, never whitespace. The blockers row is that pattern promoted
  from a footnote to a decision.
- **A bank statement.** Fixed columns, one row per event, the balance always in
  the same place, the detail on request. Nobody redesigns a bank statement into
  panels, because the alignment is the product.
- **`git log --oneline`.** A left column of fixed-width identifiers and a right
  column of prose, scanned down the left edge. Column 4 of the ledger is that
  column, holding rule ids and source names in mono.
