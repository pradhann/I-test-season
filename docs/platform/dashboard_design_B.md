# Dashboard, design B: the squad is the page

> Every number quoted below was read on 2026-09-18 from the running server:
> `POST /api/scripts/dashboard_brief/run` (provenance `generated_at`
> 2026-09-18T05:55:54Z, repo_sha 50843170) and
> `POST /api/scripts/squad_overview/run`, entry 4490171, season 2026-27,
> GW5. Line ranges are against `web/dist/js/views/home.js` at 2,344 lines and
> `fpl_edge/platform/scripts/brief.py` at 2,593 lines. Box metrics are
> computed from `web/dist/app.css` and `web/dist/dashboard.css`, not measured
> in a browser, and every such figure says so.

---

## 0. The one sentence

> **The fifteen players are the page. Every decision this gameweek is a mark
> on the player it changes, and the only thing allowed above them is the list
> of reasons the marks cannot be trusted.**

That sentence sits in the position `home.js:1` currently describes as "PAGE
ORDER, exact", and it replaces that order. A second sentence sits under it,
because the first one is only half the contract:

> A mark is drawn from a payload field or it is not drawn. The working behind
> every mark opens from the player it is on, never from a list beside him.

---

## 1. What is wrong today, measured

The owner's words are "so ugly, it is supposed to make my life easier. Proper
refresh, better alignment, fonts, intuitive usage." Each of those has a
located cause.

### 1.1 The squad is the eighth thing on the page

`home.js:233-237` appends fourteen hosts in this order: `statsRow`,
`chipLedger`, `provBanner`, `gapStrip`, `standingStrip`, `verdictCard`,
`intelCard`, `pitchCard`, `watchStrip`, `movesCard`, `solverCard`,
`tilesCard`, `watchCard`, `foot`. Seven blocks stand between the top of the
window and the first player card. Three of the seven (stats row, chip ledger,
provenance banner) carry no decision at all.

### 1.2 The same decision prints up to four times

Today's captain pick, B.Fernandes, is rendered in four places:

| place | lines | what it prints |
|---|---|---|
| verdict captain row | `home.js:738-782` | name, three candidates, lead, gates |
| `captainBlock` table | `home.js:1321-1379` | the same three candidates, as a table |
| the `C` ribbon on the pitch card | `home.js:1278-1280` | the letter C |
| solver card `sv-capline` | `home.js:1878-1889` | name, and yours when it differs |

Today's bench change (Kinsky in for Verbruggen, `+0.715` xPts) is rendered
four times as well: the verdict bench row (`home.js:685-714`), the pitch head
chip "your locked picks differ by 1" (`home.js:1424-1432`), the `db-xiline`
under the pitch (`home.js:1481-1497`), and the `bench_order` row of the watch
log (`home.js:2113-2130`). One decision, four renderings, none of them on the
two goalkeepers it moves between.

### 1.3 The squad is drawn, and the decisions are not on it

`pcard` (`home.js:1252-1312`) gives every card three chips (xPts, opponent,
minutes), two badges (availability at `:1272-1277`, price-fall at
`:1281-1288`) and one ribbon (C or V). Not one of those says "this is the man
the plan sells", "this is the swap", or "this is the chip". The pitch is a
picture of the squad with statistics on it, and every instruction about it
lives somewhere else on the page.

### 1.4 Two voices answer one question, and 35 lines exist to hide the overlap

`/api/briefing` is model-authored prose (`intelItem` prints `it.headline` and
`it.why` at `home.js:919-921`). The deterministic tiles answer the same
question in the same window. `briefingDupes` (`home.js:882-916`) exists only
to detect that the model and the rules named the same player and to suppress
one of them. Thirty-five lines of cross-reference machinery whose entire job
is to conceal a duplication the page order created.

### 1.5 The age vocabulary is hours, and the owner reads days

`ageText` and `fmtSpan` produce "Nh Mm", "Nd Nh" and "N days"
(`home.js:66-73`). Against today's payload that yields `240.3h` for the solve
plan and `5d 11h` for the squad read. Both of those facts are, in the only
unit that changes a decision, "10 days" and "5 days".

### 1.6 There is no single refresh

The page makes four calls in parallel at `home.js:238-247` and then never
re-calls any of them on demand. Three separate controls re-fetch three
separate things: `rerunButton` runs the solver (`home.js:1695-1718`),
`generateBtn` runs the briefing model pass (`home.js:823-869`), and the
consensus gap row offers a link to the Pipelines tab (`home.js:2306-2311`).
None of them repaints the page from a fresh brief except the solve poller, and
that only after a `done` state (`home.js:1684-1689`). The word the owner used
is "proper refresh", and today there is no control that means "read the
warehouse again and redraw".

### 1.7 Nineteen font sizes and nineteen spacing values

Distinct `font-size` values: seventeen in `dashboard.css`
(7.5, 9, 9.5, 10, 10.5, 11, 11.5, 12, 12.5, 13, 13.5, 15, 16, 17, 18, 20, 22)
and twelve in `app.css`; the union is nineteen. Distinct padding, margin and
gap values in `dashboard.css`: nineteen, from 1px to 64px, including 1, 2, 3,
5, 7, 9, 14, 18, 28 and 30. That is the "alignment" complaint, exactly: no
grid exists, so nothing lines up with anything.

### 1.8 The brief cannot name ten of the fifteen

This is the finding that decides design B's shape. `dashboard_brief` serves
the lineup as `best_xi.xi_codes` and `best_xi.bench_codes`, arrays of bare
integers (`brief.py:422-423`), and `squad_projection[]` carries `code`,
`xmins`, `p_appear`, `n_sources` and nothing else (`brief.py:533-543`). The
only owned players the brief names are the ones that happen to ride inside a
`_PLAYER_REF`: today that is Kinsky and Verbruggen (from
`suggested_xi.swaps[0]`), B.Fernandes, Isak (`your_captain`) and
Joao Pedro (`best_xi.captain_candidates`). Five of fifteen. Hall, Gabriel,
Calafiori, Szoboszlai, Cherki, Rogers, Odegaard, Calvert-Lewin, Davis and
Maguire exist in the brief only as integers.

A squad-first page therefore depends on a second panel, `squad_overview`, for
its primary object. §5.3 makes that an additive contract change rather than a
permanent cross-panel join.

---

## 2. What I take from other tools, and what I refuse

- **The FPL site's own team page.** The fifteen laid out as a pitch, and the
  transfer flow drawn on the shirt you are selling: click a player, he goes
  grey, the replacement lands in his slot. That direct manipulation is the
  single best idea in the genre and it is why this design puts the decision on
  the card. What the FPL site never shows is *why*, which is the whole reason
  this repo exists, so the drawer carries the working.
- **Fantasy Football Fix and LiveFPL "your team" views.** Both annotate the
  pitch with ownership, price and a rating. I take annotation-on-the-player
  and refuse their density: four to six badges per shirt turns the pitch back
  into a table that happens to be arranged in rows.
- **FPL Review's planner grid.** The strongest numeric surface in the hobby
  and the wrong primary here: a fifteen-row table is a spreadsheet of a squad,
  not a squad. It survives as the Planner tab, which already exists.
- **This repo's own `creators.js` drawer.** A memoised second call into a
  right-hand `aside.drawer`, Escape to close, blocks independently nullable,
  absent fields named in prose rather than rendered as zero. It is the best
  interaction in the application and it is the entire working layer of this
  design.
- **This repo's own `fixtures` ramp.** `--fx-d1` through `--fx-d5` in
  `fixtures.css:72-95`, with FPL's own five FDR steps and the one measured
  divergence on the neutral. The opponent chip already reuses it
  (`dashboard.css:286-290`) and keeps doing so, unchanged.

*The claims about the three external products are stated from general
knowledge of them and should be re-checked against their current sites before
any of it is quoted to a reader. Everything else here is verified in this
repo.*

---

## 3. The page

### 3.1 Above the fold, and nothing else

Two blocks, in this order:

1. **GAPS.** Every live reason the marks below cannot be trusted, one row
   each, each with the control that fixes it.
2. **SQUAD.** A band, then the eleven, then the four. Every decision is a mark
   on a card. Decisions that name no player live on the band.

Everything else moves below the fold or to a tab: the season standing, the
signal tiles, the watch log, the suppression counts, the thresholds echo, the
precedence text, the solver's notes and alternatives, the chip history, the
provenance foot.

### 3.2 Wireframe, 1280 x 800

Content width at 1280 is 1068px (1280 minus the 168px rail minus 22px padding
each side, `app.css:70,86`). The drawing below is 98 columns wide and each
card box is 17 columns, standing for a 104px card.

```
┌ 1280 ──────────────────────────────────────────────────────────────────────────────────────────┐
│ Dashboard   GW5   deadline today 18:30                    read 5 days ago   [ refresh ]        │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ GAPS                                                                                     3     │
│  SQUAD    These fifteen are the GW4 public picks, not the team you hold     [ connect ] [ cmd ]│
│  SOLVER   Plan written 10 days ago, before a deadline. No move is named.    [ re-run, 2-5 min ]│
│  XPTS     Consensus written 5 days ago, before the last deadline.           [ pipelines ]      │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ SQUAD  3-5-2   best XI by consensus xPts   these marks are worth +2.01 xPts                    │
│  transfer   no move named. 2 free transfers, read 10 days ago. Bank 0.3.                       │
│  chip       hold. No plan stands to ask.                                                       │
│ ┌────────────────────────────────────────────────────────────────────────────────────────────┐ │
│ │                              ┌ START ────────┐                                             │ │
│ │                              │ Kinsky        │                                             │ │
│ │                              │ 3.4 xPts  AVL │                                             │ │
│ │                              └───────┬───────┘                                             │ │
│ │                                      │ +0.71                                               │ │
│ │           ┌───────────────┐ ┌───────────────┐ ┌───────────────┐                            │ │
│ │           │ Hall          │ │ Gabriel       │ │ Calafiori     │                            │ │
│ │           │ 4.3 xPts  HUL │ │ 4.2 xPts  bha │ │ 3.6 xPts  bha │                            │ │
│ │           └───────────────┘ └───────────────┘ └───────────────┘                            │ │
│ │                                                                                            │ │
│ │ ┌ C ────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐  │ │
│ │ │ B.Fernandes   │ │ Szoboszlai    │ │ Cherki        │ │ Rogers        │ │ Odegaard      │  │ │
│ │ │ 5.8 xPts  ful │ │ 4.4 xPts  bou │ │ 4.2 xPts  SUN │ │ 4.1 xPts  bre │ │ 4.1 xPts  bha │  │ │
│ │ └───────┬───────┘ └───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘  │ │
│ │         │ +1.3                                                                             │ │
│ │                    ┌───────────────┐ ┌ your C ───────┐                                     │ │
│ │                    │ Joao Pedro    │ │ Isak          │                                     │ │
│ │                    │ 5.6 xPts  bre │ │ 4.5 xPts  bou │                                     │ │
│ │                    └───────────────┘ └───────────────┘                                     │ │
│ └────────────────────────────────────────────────────────────────────────────────────────────┘ │
│ BENCH                                                                                          │
│ ┌ 1 SIT ────────┐ ┌ 2 ────────────┐ ┌ 3 ────────────┐ ┌ 4 ────────────┐                       │
│ │ Verbruggen    │ │ Calvert-Lewin │ │ Davis         │ │ Maguire       │                       │
│ │ 2.7 xPts  ARS │ │ 3.5 xPts  CRY │ │ 3.4 xPts  eve │ │ 2.9 xPts  ful │                       │
│ └───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘                       │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ the fold at 800px lands here with three gap rows (§3.4)
```

The two vertical stubs are ties. In the rendered page a tie is a 1px `--accent`
rule drawn from the tagged card to its partner with the delta at its midpoint:
Kinsky to Verbruggen carrying `+0.71`
(`suggested_xi.swaps[0].numbers.swing`), B.Fernandes to Isak carrying `+1.3`
(`suggested_xi.captain_delta_xpts`). The tie is the whole bench argument and
the whole armband argument, drawn once, between the two men each moves
between.

### 3.3 Wireframe, 390 x 844

At 390px the content width is 366px (`app.css:196`, `.content` padding 12px).
Four 86px cards plus gaps measure 374px, which is the exact fit
`dashboard.css:536` records for a 375px pane, and a five-card midfield row
does not fit at any card width that can also carry a decision tag. So at
widths of 640px and below the squad stops being a pitch and becomes fifteen
full-width strips, grouped by position, with the formation printed as a word.
The trade is stated in §11.6.

```
┌ 390 ────────────────────────────────┐
│ GW5  deadline today 18:30     [ ↻ ] │
│ read 5 days ago                     │
├─────────────────────────────────────┤
│ GAPS                            3   │
│ SQUAD   GW4 public picks, not the   │
│   team you hold.       [ connect ]  │
│ SOLVER  Plan 10 days old. No move   │
│   is named.            [ re-run ]   │
│ XPTS    Consensus 5 days old.       │
│                       [ pipelines ] │
├─────────────────────────────────────┤
│ SQUAD  3-5-2   worth +2.01 xPts     │
│ transfer  no move named. 2 FT,      │
│           read 10 days ago. Bank 0.3│
│ chip      hold. No plan stands.     │
├─────────────────────────────────────┤
│ XI                                  │
│ GKP                                 │
│ START ● Kinsky     TOT  3.4   AVL   │
│ DEF                                 │
│       ● Hall       NEW  4.3   HUL   │
│       ● Gabriel    ARS  4.2   bha   │
│       ● Calafiori  ARS  3.6   bha   │
│ MID                                 │
│   C   ● B.Fernandes MUN 5.8   ful   │
│       ● Szoboszlai LIV  4.4   bou   │
│       ● Cherki     MCI  4.2   SUN   │
│       ● Rogers     CHE  4.1   bre   │
│       ● Odegaard   ARS  4.1   bha   │
│ FWD                                 │
│       ● Joao Pedro CHE  5.6   bre   │
│ yourC ● Isak       LIV  4.5   bou   │
├─────────────────────────────────────┤
│ BENCH                               │
│ 1 SIT ● Verbruggen BHA  2.7   ARS   │
└─────────────────────────────────────┘
        ▲ the fold at 844px lands here
```

Thirteen of the fifteen sit above the fold at 390px, against fourteen at
1280px. The narrow layout carries more of the squad than the wide one, because
a strip is 34px and a card is 88px.

### 3.4 The height budget, computed

From `app.css` and `dashboard.css` box metrics, with the spacing scale of §9
applied. Every figure is arithmetic on the stylesheet, not a browser
measurement.

| block | height | how |
|---|---|---|
| topbar | 44 | `.topbar h1` 19px plus `margin-bottom: 16px` |
| gaps card | 134 | label 16 + three rows at 26 + padding 2x12 + margin 16 |
| squad band | 72 | three lines at 24 (source, transfer, chip) |
| pitch, four rows | 388 | four cards at 88 + three gaps at 12 |
| tray gap + bench | 138 | 16 + label 14 + card 88 + padding 2x10 |
| squad card chrome | 24 | padding 2x12 |
| **total** | **800** | |

The design has zero slack at 1280 x 800 and that is deliberate pressure. A
fourth gap row, or a transfer decision that adds a ghost card to a row, pushes
the bench tray under the fold. That is the correct failure: four gaps means
the page cannot be trusted anyway, and the gaps are the thing that outranks
the squad.

---

## 4. The mark grammar

### 4.1 One tag per card

Each of the fifteen cards carries at most one **decision tag**, drawn in the
card's top border in `--accent`, always a word, never a hue alone:

| tag | condition | payload source |
|---|---|---|
| `SELL` | this player is an out-leg | `verdict.lines[question="transfer"].moves[].out.code` |
| `BUY` | a ghost card in the vacated slot | `verdict.lines[question="transfer"].moves[].in` |
| `C` | the captain pick | `verdict.lines[question="captain"].pick.code` |
| `close call C` | the lead is under the gate | `best_xi.close_call` with `best_xi.captain_lead_xpts` |
| `your C` | the locked armband, when it differs | `suggested_xi.your_captain.code` |
| `START` | a bench player who starts | `suggested_xi.swaps[].in.code` |
| `SIT` | a starter who benches | `suggested_xi.swaps[].out.code` |
| `1`..`4` | the bench order | index in `best_xi.bench_codes` |
| `TC` / `BB` | a chip that names players | `verdict.lines[question="chip"].chip` |

Today exactly four of the fifteen carry a tag: Kinsky `START`, Verbruggen
`1 SIT`, B.Fernandes `C`, Isak `your C`. Eleven cards are untagged, and that
is the page telling the truth about a week where the solver is stale and no
rule cleared a gate.

### 4.2 Collision precedence

One player can attract more than one decision. The tag precedence is
`transfer > captain > bench > chip`, and a suppressed decision renders as a
small square on the card's second row, counted, with its own drawer entry.
The precedence is chosen so the most expensive decision wins the most visible
channel. §11.3 records this as an untested rule.

### 4.3 Four colour channels that cannot collide

| channel | carries | tokens |
|---|---|---|
| decision tag | that a decision touches this card | `--accent` only, word carries the kind |
| xPts chip tint | consensus xPts against the XI median | `pj-m2 pj-m1 pj-n0 pj-p1 pj-p2`, on `--s1`/`--s2` |
| opponent chip tint | fixture ease on the position's axis | `--fx-d1`..`--fx-d5` |
| status pip | FPL availability, price-fall risk | `--good` `--warn` `--bad`, reserved |

`app.css:19-21` defines `--accent` as selection, "a desaturated steel blue
deliberately outside the green family so selected never reads as positive",
which is precisely what a decision tag is: this card is selected by a
decision, and whether that is good is a separate question the drawer answers.
`dashboard.css:3-4` reserves the status hues for the risk channel, so the
transfer direction is carried by the words `SELL` and `BUY` and never by red
and green.

### 4.4 What each card prints, and what it does not

Prints: face, name, the one number (consensus xPts, tinted), the opponent
label with its casing already carrying the venue, the tag when there is one,
the status pip when the player is flagged, the price-fall mark when the
`own_price_fall` alert names him.

Does not print: minutes, appearance probability, haul probability, ownership
percentage, price, vice-captaincy, source clocks, gate text. All of those are
working, they are all in the drawer, and a fifteen-card pitch with five chips
per card is the surface the owner called ugly.

Price is the one arguable removal, since a transfer decision needs it. It
returns on the `SELL` and `BUY` cards only, where it is part of the decision,
and on the transfer band line as the bank.

---

## 5. Field map: every `dashboard_brief` key, and where it lands

Rule 9 in the shared brief: nothing on the page that is not in the payload, no
free-text recommendation fields. This is the whole map. `AF` is above the
fold, `BF` is below the fold or on a tab, `drawer` opens from a player,
`dropped` is not rendered anywhere.

### 5.1 The thirty-one top-level keys

| payload key | place | element |
|---|---|---|
| `season` | drawer | drawer footer, with `provenance` |
| `gw` | AF | topbar, "GW5" |
| `entry_id` | BF | provenance foot |
| `as_of` | AF | topbar clock, "read 5 days ago" |
| `sources_as_of` | drawer, BF | each drawer block's own age; the five clocks in full on the Checks tab |
| `deadline_utc` | AF | topbar, "deadline today 18:30" |
| `xi_median_xpts` | AF | the anchor of the `pj-` tint on every card; the number itself prints in the drawer and the tint legend |
| `thresholds` | drawer, BF | three gates quoted in decision drawers (`bench_margin_xpts`, `captain_close_call_xpts`, `captain_divergence_xpts`); the other eighteen on the Checks tab |
| `alerts[].kind="AVAILABILITY"` | AF | the status pip on that player's card; `status` and `news` verbatim in his drawer |
| `alerts[].rule="own_price_fall"` | AF | the price-fall mark on that player's card; `numbers` in his drawer |
| `alerts[].kind="SOLVER"` | AF | gaps row, `reason` verbatim |
| `alerts[].kind="GAP"` | AF | gaps row, `reason` verbatim |
| `alerts[].priority` | AF | gaps row order |
| `alerts[].drill` | AF | the gaps row control target |
| `tiles[]` | BF | Signals tab, in full, unchanged. None of today's six names an owned player, so none earns a mark |
| `suppressed_counts` | BF | Signals tab head, "13 xpts_standout, 9 fixture_turn suppressed" |
| `empty_kinds[kind="moves"]` | AF | the transfer band line's reason when `moves` is empty |
| `empty_kinds[]` others | BF | Checks tab |
| `watch_log[]` | BF | Checks tab, all twelve rows, unchanged |
| `solve.state` | AF | gaps row, and the fill of every tag sourced from `solve_plan` |
| `solve.reason` | AF | gaps row, verbatim |
| `solve.generated_at` | AF | gaps row, as days |
| `solve.age_hours` | AF | divided by 24 and floored for the gaps row; raw value in the drawer |
| `solve.last_deadline_utc` | AF | the staleness comparator for every clock; printed in the drawer |
| `solve.next_deadline_utc` | dropped | `deadline_utc` is the same instant and already prints |
| `solve.plan.moves[]` | AF | the `SELL` and `BUY` tags |
| `solve.plan.*` others | drawer | the transfer drawer's working: `gain_over_roll`, `objective_mode`, `forecast_source`, `optimality_gap_pct`, `hits`, `hit_verdict`, `bank_after_tenths`, `alternatives`, `notes`, `bounds`, `solve_seconds`, `is_roll` |
| `suggested_xi.swaps[]` | AF | the `START` and `SIT` tags and the tie |
| `suggested_xi.swap_delta_xpts` | AF | the tie label, `+0.71` |
| `suggested_xi.captain_delta_xpts` | AF | the captain tie label, `+1.3` |
| `suggested_xi.total_delta_xpts` | AF | the band, "these marks are worth +2.01 xPts" |
| `suggested_xi.your_captain` | AF | the `your C` tag |
| `suggested_xi.captain` | drawer | the captain drawer, beside the pick |
| `suggested_xi.captain_by_haul` | drawer | the captain drawer, with `p_haul_generated` |
| `suggested_xi.captain_by_mean` | drawer | the captain drawer |
| `suggested_xi.captain_numbers` | drawer | six figures, both measures printed, never blended |
| `suggested_xi.n_changes` | AF | the count on the bench tie when it exceeds one |
| `suggested_xi.reason` | AF | the band, verbatim, when non-null |
| `suggested_xi.xi_codes` | dropped | the page draws one lineup, `best_xi`'s |
| `suggested_xi.bench_codes` | dropped | same |
| `suggested_xi.source_panel` / `source_as_of` | drawer | |
| `best_xi.xi_codes` | AF | the layout: which eleven, in position order |
| `best_xi.bench_codes` | AF | the tray, in served order, giving the `1`..`4` tags |
| `best_xi.formation` | AF | the band, "3-5-2" |
| `best_xi.captain` | AF | the `C` tag |
| `best_xi.close_call` | AF | the tag word becomes "close call C" |
| `best_xi.captain_lead_xpts` | drawer | with the gate beside it |
| `best_xi.captain_candidates[]` | drawer | all three, with `xpts` and `p_haul` |
| `best_xi.differs[]` | drawer | named in the captain and bench drawers |
| `best_xi.n_differs` | dropped | the difference is the `START` and `SIT` tags; a count beside them is the same fact twice |
| `best_xi.xi_xpts` | drawer | 48.26 does not change an action |
| `best_xi.reason` | AF | the band, verbatim, when non-null |
| `squad_source.label` | AF | gaps row when `live` is false, band line when true |
| `squad_source.live` | AF | decides which of those two |
| `squad_source.picks_gw` | AF | the gaps row, "GW4 public picks" |
| `squad_source.as_of` | AF | the band, as days |
| `squad_source.fix` | AF | the gaps row copy control |
| `team_fixtures[].next.label` | AF | the opponent chip on each card, casing already carries the venue |
| `team_fixtures[].next.attack_ease` | AF | the chip tint for MID and FWD cards |
| `team_fixtures[].next.defence_ease` | AF | the chip tint for GKP and DEF cards |
| `team_fixtures[].next.*` others | drawer | `opponent`, `opponent_code`, `is_home`, `kickoff_utc`, `attack_rank`, `defence_rank`, `unavailable` |
| `team_fixtures[].labels` | drawer | the three-gameweek run |
| `team_fixtures[].horizon_attack_rank` | drawer | with `horizon_gws` beside it, never alone |
| `team_fixtures[].horizon_defence_rank` | drawer | same |
| `team_fixtures[].horizon_gws` | drawer | the window every rank is quoted over |
| `team_fixtures[]` for clubs not in the fifteen | dropped | fourteen of today's twenty rows |
| `squad_projection[].xmins` | drawer | null on all fifteen today |
| `squad_projection[].p_appear` | drawer | 0.947 to 0.973 today, no served gate, so no mark |
| `squad_projection[].n_sources` | drawer | three on all fifteen today |
| `projection_gw` | drawer | the label on the xPts block |
| `moves[]` | AF | the `SELL` and `BUY` tags when the transfer verdict names them; empty today |
| `moves[].numbers` / `gate` / `gws` / `rank_gws` | drawer | the rule's working |
| `moves_suppressed` | BF | Signals tab |
| `verdict.precedence` | BF, drawer | the Checks tab, and verbatim at the foot of every decision drawer |
| `verdict.lines[transfer]` | AF | the `SELL`/`BUY` tags, or the band line when no player is named |
| `verdict.lines[captain]` | AF | the `C` tag |
| `verdict.lines[bench]` | AF | the `START`/`SIT` tags |
| `verdict.lines[chip]` | AF | the tray band when it names players, the band line when it does not |
| `verdict.lines[].rule` | AF | the tag word and the drawer's template key |
| `verdict.lines[].state` | AF | the tag fill: solid on `fresh`, outline on `aging`, `stale`, `superseded`, `missing` |
| `verdict.lines[].numbers` | AF, drawer | the tie labels above the fold, the rest in the drawer |
| `verdict.lines[].dissent[]` | AF, drawer | a counted dot on the tag above the fold, the voices in full in the drawer |
| `verdict.lines[].drill` | AF | the click target, now always a player when one is named |
| `header.free_transfers` | AF | the transfer band line |
| `header.free_transfers_as_of` | AF | the same line, as days |
| `header.free_transfers_state` | AF | the same line: `stale` prints the count as unknown |
| `header.bank_tenths` | AF | the transfer band line, "Bank 0.3" |
| `header.chip` | AF | the chip band line or the tray band |
| `header.chip_rule` | AF | the chip line's template key |
| `header.chip_state` | AF | the chip line's state chip |
| `standing.*` | BF | Season tab, unchanged |
| `xpts_source` | AF, drawer | gaps row when `xpts_as_of` predates `solve.last_deadline_utc`, drawer otherwise |
| `xpts_as_of` | AF | the gaps row, as days |
| `p_haul_source` | drawer | the captain drawer |
| `p_haul_generated` | AF, drawer | gaps row when it predates `solve.last_deadline_utc`, which it does today by 29 days |
| `fixtures_scale.available` | AF | false switches off every opponent tint |
| `fixtures_scale.domain` | AF | the tint domain, not printed |
| `fixtures_scale.unit` | drawer | printed beside every ease figure |
| `notes[]` | AF, BF | one gaps row when non-empty, full text on the Checks tab |
| `provenance.*` | BF | the foot, unchanged. `generated_at` is the instant every age is computed against |
| `duration_ms`, `performance` | dropped | panel telemetry, not a fact about the season |

### 5.2 `squad_overview` keys, which this design needs to draw anything

The page makes one further panel call. Rule 9 is satisfied by both payloads,
and §5.3 removes the need for the second one.

| key | place |
|---|---|
| `starters[].name`, `bench[].name` | AF, the card |
| `starters[].pos`, `bench[].pos` | AF, the row the card sits in |
| `starters[].team_code`, `bench[].team_code` | AF, joins the card to `team_fixtures` |
| `starters[].team` | AF at 390px only, where the strip has room |
| `starters[].xpts`, `bench[].xpts` | AF, the one number and its tint |
| `starters[].status`, `.news` | AF, the status pip; `news` verbatim in the drawer |
| `starters[].is_captain` | AF, the `your C` tag |
| `starters[].price` | AF on `SELL` and `BUY` cards only; drawer otherwise |
| `provenance_source`, `as_of` | AF, the band and the topbar clock |
| `bank_tenths` | AF, the transfer band line |
| `p_haul`, `own_pct`, `multiplier`, `is_vice`, `is_starter` | drawer |
| `squad_value_tenths`, `projected_xi_xpts`, `team_name`, `captain`, `vice`, `flags` | dropped from the face; `flags` duplicates the availability alerts |
| `chips` | BF, the chip drawer's history |
| `xpts_source`, `xpts_as_of`, `xpts_gw`, `p_haul_source`, `p_haul_generated` | drawer; the brief carries the same values |

### 5.3 One additive contract change

`dashboard_brief` already holds the fifteen fully populated in memory:
`brief.py:235` builds `squad15 = starters + bench` from `squad_overview`'s own
rows, and `_ref()` at `brief.py:98-108` is the serialiser. Add one field:

```
squad   array of {code, name, pos, team, team_code, price, own_pct,
                  xpts, p_haul, status, news, is_captain, is_starter}
```

Populated from `squad15` through the existing `_ref()` plus the four squad
fields `_PLAYER_REF` does not carry. Additive, back-compatible, no new query,
no second implementation of any metric, and it removes a cross-panel join from
the page's primary object. Without it, design B's single point of failure is a
panel the brief does not depend on (§11.1).

### 5.4 Two things this design drops entirely

- **`/api/briefing`.** Model-authored prose. Rule 9 forbids a free-text
  recommendation field, and the brief was built with that rule at its centre
  (`brief.py:14-18`). A model-written headline beside a payload-led page is
  the same rule broken through a different door, and the 35 lines of
  `briefingDupes` are the evidence that the two voices collide. The Chat tab
  keeps the model.
- **`/api/solve/status` as a page section.** The state feeds the solver gaps
  row's control and its in-flight text. Nothing else.

---

## 6. Refresh

### 6.1 Which fields carry a clock

Sixteen, and they are not interchangeable:

`as_of`, `sources_as_of.{squad_overview, ownership_eo, fixture_board,
projection_table, solve_plan}`, `deadline_utc`, `xpts_as_of`,
`p_haul_generated`, `squad_source.as_of`, `solve.generated_at`,
`solve.last_deadline_utc`, `best_xi.source_as_of`,
`suggested_xi.source_as_of`, `standing.as_of`, `alerts[].source_as_of`,
`tiles[].source_as_of` and `tiles[].sources[].as_of`,
`moves[].sources[].as_of`, `verdict.lines[].source_as_of`,
`verdict.lines[].dissent[].source_as_of`, `watch_log[].as_of`,
`header.free_transfers_as_of`, `provenance.generated_at`.

`provenance.generated_at` is the **now**. Every age is elapsed time from a
clock to that instant, not to the browser's wall time, so a payload cached for
an hour does not silently age its own contents.

### 6.2 The age format, days and never hours

```
days = floor((provenance.generated_at - clock) / 86400)
days < 0  ->  "written ahead of this read"
days == 0 ->  "today"
days == 1 ->  "yesterday"
days >= 2 ->  "N days ago"
```

Floor, never round, so an age never inflates itself. The raw value stays in
the `title` attribute for anyone who wants the hours. Against today's payload:

| clock | value | prints |
|---|---|---|
| `as_of` | 2026-09-12T18:17:08Z | read 5 days ago |
| `sources_as_of.fixture_board` | 2026-09-18T05:55:58Z | today |
| `sources_as_of.projection_table` | 2026-09-12T18:17:25Z | 5 days ago |
| `sources_as_of.solve_plan` | 2026-09-08T05:38:23Z | 10 days ago |
| `solve.age_hours` 240.3 | | 10 days ago |
| `p_haul_generated` | 2026-08-19T16:01:02Z | 29 days ago |
| `squad_source.as_of` | 2026-09-12T18:17:08Z | 5 days ago |

`deadline_utc` is a countdown, not an age, so it prints as a wall clock and a
day count and never as hours: "deadline today 18:30", "deadline tomorrow
18:30", "deadline Sat 14:00, in 3 days". Inside the last day the topbar takes
`.deadline.urgent` (`app.css:90-92`), which already exists.

### 6.3 Where the age is shown

Exactly two places.

1. **The topbar**, right-aligned: `result.as_of` as days, beside the refresh
   control. One clock for the whole page, and it is the oldest load-bearing
   one by construction (`brief.py` computes `as_of` as the minimum of
   `sources_as_of` excluding `solve_plan`).
2. **Inside a drawer**, on each block, that block's own source and age.

No card carries a date. Fifteen cards with fifteen dates is the noise the
owner named, and the fact a date would carry is already carried by the gaps
strip when it matters.

### 6.4 What the user presses

**One button, `[ refresh ]`, in the topbar.** It re-runs `dashboard_brief` and
`squad_overview` and repaints every mark. It costs one warehouse read, spends
no credits, runs no model and takes under six seconds today
(`duration_ms` 5,964 on the read above). That is what "proper refresh" means:
a control whose meaning is "read the warehouse again and redraw", with no
other consequence.

Expensive controls exist only on the gaps row that names them:

| control | where | cost | payload trigger |
|---|---|---|---|
| `[ re-run, 2-5 min ]` | solver gaps row | a solve | `solve.state` not in (`fresh`, `aging`) |
| `[ connect ]` + `[ cmd ]` | squad gaps row | a cookie paste | `squad_source.live` false, `squad_source.fix` |
| `[ pipelines ]` | consensus gaps row | a tab move | `xpts_as_of` < `solve.last_deadline_utc` |

The solve control keeps the existing polling (`home.js:1671-1694`), and on
`done` it re-calls both panels rather than only the brief, because a solve
that changes the plan changes the tags on the pitch.

### 6.5 What happens while it is in flight

Existing marks stay drawn at 60% opacity, the topbar clock reads "refreshing",
and nothing blanks. On success the page repaints in place. On failure the
previous marks return to full opacity and a gaps row appears naming the panel
and the error, with the clock relabelled to its now older age. A page that
blanks on a failed refresh has lost information the owner already had.

### 6.6 What happens to stale sections

A section is stale when its own clock predates `solve.last_deadline_utc`.
Today that is true of `squad_overview`, `projection_table`, `ownership_eo`,
`solve_plan` and the haul simulation, and false of `fixture_board`.

The rule: **a stale decision is still drawn, in outline.** Its tag loses its
`--accent` fill and becomes a 1px `--accent` outline with the same word. It
keeps its tie and its delta. It opens the same drawer. What changes is that it
can no longer be mistaken for a current instruction, and the gaps row above
says in one sentence why.

Deleting a stale mark would be worse, because the page would then be fifteen
blank cards on the week the owner most needs to see what the stale plan
believed. This is the same discipline the solver card already applies at card
level (`brief.py:384-387`: a stale plan serves no plan body), moved down to
the level of the individual mark.

Two exceptions, both because the number is meaningless rather than old:

- `p_haul` older than the last deadline renders nowhere, not even in outline.
  A haul probability from a 29-day-old simulation is a different gameweek's
  answer. The existing `haulFresh` gate (`home.js:260`) is kept exactly.
- `header.free_transfers` under `free_transfers_state == "stale"` prints as
  unknown with the read count in the sentence, which is what
  `home.js:364-373` already does and is right.

### 6.7 Auto refresh

One rule, no timer: on `visibilitychange` to visible, if `result.as_of` is
older than `solve.last_deadline_utc`, re-call once and repaint. The only
surviving interval is the solve poller, and it runs only while a run started
in this session is in flight.

---

## 7. The drawer: the working opens from the player

Same mechanism as `creators.js` and the existing `attachPlayerDrawer`
(`home.js:192`): one `aside.drawer`, `transform: translateX(100%)` to `.open`,
Escape closes, `prefers-reduced-motion` honoured (`app.css:283-289`). Blocks
are independently nullable and every null carries a named reason.

Clicking any of the fifteen cards opens it. The first block is the decision
the card's tag names, and it is the only block that differs by tag.

```
┌ B.Fernandes  MUN  MID  12.0                                          ✕ ┐
│ ── THE DECISION ─────────────────────────────────────────────────────── │
│ captain, by rule mean_xpts_captain, under a stale solve                  │
│ close call: lead 0.16 against a 0.5 gate                                │
│   B.Fernandes  5.77 xPts  17.7% haul   ful (A)                          │
│   Joao Pedro   5.61 xPts   4.9% haul   bre (A)                          │
│   Isak         4.47 xPts   8.9% haul   bou (A)   your locked armband     │
│ moving the armband is worth +1.3 xPts against Isak                      │
│ dissent: creator_armband, 4 named Haaland, not in your fifteen          │
│          creator_board, today                                           │
│ haul odds are the engine simulation of 29 days ago and are shown         │
│ for the record, not as an input                                         │
│ ── THE NUMBER ───────────────────────────────────────────────────────── │
│ 5.769 consensus xPts for GW5, provider consensus of 3 sources, the same  │
│ numbers the Projections tab shows. Written 5 days ago.                   │
│ appearance 97.1% from 3 sources. No provider serves expected minutes.    │
│ ── THE FIXTURE ──────────────────────────────────────────────────────── │
│ Fulham away, Sat 19 Sep. attack ease -0.116, defence ease +0.020,        │
│ goals per match versus a league-average fixture.                         │
│ ranks 28 attack, 19 defence of 40 (1 = easiest).                         │
│ run GW5-7: ful, TOT, lee. Horizon ranks 6 attack, 6 defence.            │
│ ── AVAILABILITY ─────────────────────────────────────────────────────── │
│ status a, nothing flagged. squad_overview, 5 days ago.                   │
│ ── MARKET ───────────────────────────────────────────────────────────── │
│ No flow window: the transfer counters reset between the two most recent   │
│ 2026-27 snapshots. price_radar serves nothing comparable yet.             │
│ ── HOW TIES BREAK ───────────────────────────────────────────────────── │
│ verdict.precedence, verbatim                                             │
└─────────────────────────────────────────────────────────────────────────┘
```

Per tag, the decision block reads:

| tag | fields |
|---|---|
| `C` | `verdict.lines[captain].rule`, `.state`, `.numbers`, `best_xi.captain_candidates[]`, `.captain_lead_xpts`, `.close_call`, `thresholds.captain_close_call_xpts`, `suggested_xi.captain_numbers`, `.captain_delta_xpts`, every `dissent[]` with voice, numbers, panel and age |
| `START`/`SIT` | `suggested_xi.swaps[i].numbers.{bench_xpts, starter_xpts, swing}`, `thresholds.bench_margin_xpts`, `verdict.lines[bench].rule`, `.numbers.n_changes` |
| `SELL`/`BUY` | `verdict.lines[transfer].moves[]`, and either `solve.plan` in full or `moves[i].numbers` with `moves[i].gate` verbatim; `bank_after_tenths`, `hit_verdict`, `optimality_gap_pct`, `alternatives[]`, `notes[]`, `bounds` |
| `TC`/`BB` | `verdict.lines[chip].chip`, `.rule`, `.state`, `header.chip_state`, and `squad_overview.chips` for the windows and what is spent |
| none | the same blocks minus the decision, plus one line naming which decisions did not touch this player and why, keyed on each `verdict.lines[].rule` |

That last row matters. Opening an untagged card today says, in four lines:
transfer, `no_move_named`, no plan stands and nothing cleared a gate; captain,
the pick is B.Fernandes; bench, one change and it is not yours; chip, hold.
An untagged player is a decision too, and the drawer says so rather than
showing an apologetic blank.

---

## 8. Empty, stale and error states

Every age in this table is in days.

| condition | what renders |
|---|---|
| `dashboard_brief` unreachable | the squad still draws from `squad_overview` with no tags at all, and one gaps row: "No decisions: dashboard_brief did not answer. `<error>`" with the refresh control. The pitch without marks is still the truth about what you hold |
| `dashboard_brief` empty with a reason | the same, with the panel's `reason` verbatim |
| `squad_overview` unreachable or empty | **the design's worst state.** Fifteen slot outlines drawn from `best_xi.xi_codes` and `bench_codes` in position-blind order, each showing its integer code. The four to five players the brief names in its own `_PLAYER_REF` fields draw normally, so today Kinsky, Verbruggen, B.Fernandes, Isak and Joao Pedro have names and ten slots do not. One gaps row names `squad_overview`, its reason, and the fix. §5.3 exists to make this state impossible |
| `best_xi` null, or `best_xi.reason` set | the pitch draws `squad_overview.starters` and `.bench` instead, and the band prints `best_xi.reason` verbatim ("no consensus projection cached; the XI below is the formation-legal fallback, not a ranking"). No `START`/`SIT`, no `C` |
| `best_xi.xi_codes` shorter than eleven | the served codes draw, the remaining slots draw as empty outlines, and the band prints `best_xi.reason` ("fewer than eleven players carry a position") |
| `suggested_xi` null, or `.reason` set | no `START`, `SIT`, `C` or `your C` tags; the band prints the reason verbatim ("no projection artefact cached; the bench and captain rules cannot rank players") |
| every `xpts` null | no xPts number, no `pj-` tint, no `C`. Cards carry identity, fixture and status only. The band says which panel served nothing |
| `solve.state` = `stale` | today's state. Transfer and chip lines print their rule template plus the `stale` chip, `solve.reason` verbatim in the gaps row, "10 days ago". No `SELL`/`BUY` ghost, because a stale plan's moves were priced against a squad you no longer have |
| `solve.state` = `superseded` | the same shape, with `solve.reason`'s count of differing players verbatim |
| `solve.state` = `missing` | gaps row with `solve.reason` and the re-run control drawn prominent |
| `moves` empty | the transfer band line prints `empty_kinds[kind="moves"].reason` verbatim, or "no candidate cleared the coverage or form gates" from `watch_log[check="move_rules"].detail` |
| `squad_source.live` false | today's state. Gaps row: "These fifteen are the GW4 public picks, not the team you hold", `squad_source.fix` as a copy control, plus a persistent one-line band across the top of the pitch, because every mark below it is computed on that fifteen |
| `squad_source` null | the band prints "the brief served no squad_source block, so the fix is not known here", which is what `home.js:2251-2256` already does |
| `team_fixtures` missing a club | the opponent chip renders a dash with the title "no fixture data served for this club". Never a colour |
| `team_fixtures[].next` null | the chip renders a dash titled "blank gameweek, no fixture in GW5" |
| `team_fixtures[].next.attack_ease` null | the chip renders the label with no tint and the drawer prints `next.unavailable` verbatim |
| `fixtures_scale.available` false | no tint on any opponent chip anywhere. Labels only, in `--raised` |
| `squad_projection` missing a code | the minutes block is absent from that player's drawer with the line "no provider minutes column for this player this gameweek". No mark on the card either way |
| `p_haul_generated` older than `solve.last_deadline_utc` | today's state, 29 days. No haul figure renders on a card. The captain drawer prints the three candidates' haul odds under one line naming the simulation and its date, labelled as a record rather than an input. One gaps row |
| `xpts_as_of` older than `solve.last_deadline_utc` | today's state, 5 days. Gaps row naming the vintage and `xpts_source` verbatim. The xPts numbers still render, tinted, because the alternative is a blank squad |
| `xpts_as_of` null | gaps row: "No consensus xPts as-of served; the numbers on this page carry no date" |
| `alerts` contains `availability` | the status pip on that card, `status` letter and `news` verbatim in the drawer. Today: zero flagged of fifteen |
| `price_radar` gap | today's state. No price-fall marks. The market block of every drawer prints the panel's own reason verbatim |
| `verdict` absent or `lines` empty | one gaps row: "the brief carries no verdict block; a backend gap, not a quiet day". The squad draws untagged |
| `header.free_transfers_state` stale or missing | the transfer line prints the count as unknown and names what was read and when |
| `notes[]` non-empty | one gaps row, count and first note, full text on the Checks tab |
| `standing.gws` empty | the Season tab prints `standing.reason` verbatim. Nothing above the fold changes |
| a panel returns HTTP non-200 | the previous payload's marks stay drawn, the topbar clock keeps ageing, and a gaps row carries the status and message with the refresh control |
| the solve run fails | the solver gaps row appends the failure date and the last log line, reported only when the failure is newer than the plan that stands, which is what `home.js:1918-1930` already gets right |

---

## 9. Type scale and spacing

### 9.1 Tokens reused, by name

From `app.css:14-25`: `--bg`, `--surface`, `--raised`, `--ink`, `--muted`,
`--faint`, `--line`, `--accent`, `--accent-ink`, `--pitch`, `--s1`, `--s2`,
`--good`, `--warn`, `--bad`, `--shadow`, `--mono`. From `fixtures.css:72-95`:
`--fx-d1` to `--fx-d5` with their `-ink` pairs and `--fx-cell-ink`. From
`dashboard.css:229-235`: the `pj-m2 pj-m1 pj-n0 pj-p1 pj-p2` ramp classes,
unchanged, anchored on `xi_median_xpts` exactly as documented at
`dashboard.css:6-13`.

No new colour token. The decision channel is `--accent` and nothing else
(§4.3).

### 9.2 New: the type scale

Six steps on `:root` in `app.css`, replacing the nineteen distinct sizes
counted in §1.7.

```
--t-tag:   10px    700  .08em  uppercase   decision tags, section labels
--t-meta:  11.5px  400                     clocks, sources, chip sublabels
--t-body:  13px    400                     the existing body size, named
--t-name:  14px    650                     player names on cards and strips
--t-num:   17px    700  var(--mono)        the one number on a card
--t-head:  20px    600  .06em  uppercase   GAPS, SQUAD, BENCH
```

`--t-body` is `app.css:59`'s current `13px/1.45` given a name, so the body
text does not move. `--t-num` is `app.css:170`'s `.stat .v` size reused for
the card's single number, which keeps the numeric register the app already
has. Everything that is currently 7.5, 9, 9.5, 10.5, 11, 12, 12.5, 13.5, 15,
16, 18, 19 or 22px on the dashboard resolves to one of the six.

`font-variant-numeric: tabular-nums` stays global (`app.css:64`), so a column
of xPts does not wobble when a value changes width.

### 9.3 New: the spacing scale

A 4px grid on `:root`, replacing the nineteen distinct padding, margin and gap
values counted in §1.7.

```
--sp-1:  4px    inside a chip, tag to border
--sp-2:  8px    between cards in a pitch row
--sp-3: 12px    card padding, pitch row gap
--sp-4: 16px    between blocks inside a card, card margin-bottom
--sp-5: 24px    between the gaps card and the squad card
--sp-6: 32px    reserved, section breaks below the fold
```

Every padding, gap and margin on the dashboard resolves to one of the six.
This is the whole of the "better alignment" answer: a grid exists, so the left
edge of a tag, a name, a number and a section label are the same left edge.

### 9.4 New: card geometry

```
--pp-w:       104px   was 86px, hardcoded in dashboard.css:203 and app.css:186
--pp-face-h:   40px   was 64px
--ring-decide: 0 0 0 2px var(--accent)
```

The card grows 18px wider and 24px shorter. Wider because a decision tag plus
a name needs the room and `text-overflow: ellipsis` on `Calvert-Lewin` at 86px
is how a squad-first page loses its primary object. Shorter because §3.4's
height budget has zero slack and a 64px photo is the least load-bearing 24px
on the page. Five cards at 104px with `--sp-2` gaps measure 552px, inside the
1068px content width with room for a `BUY` ghost.

### 9.5 The one place a number is not a token

`fixtures_scale.domain` is `[-0.6, 0.6]` today and it is served, not authored.
The opponent chip's five-way bucket divides the served domain, exactly as
`easeClass` at `home.js:274-284` already does. No domain constant enters CSS
or JavaScript.

---

## 10. What I delete from `home.js`

2,344 lines today. The deletions below total roughly 1,240 lines, a little
over half. What survives is the pitch, the gap strip, the drawer, the drill
plumbing and the solve poller.

| lines | what | why it goes |
|---|---|---|
| 194-237 (partial) | the hosts and skeletons for the ten deleted blocks: `statsRow`, `chipLedger`, `provBanner`, `standingStrip`, `intelCard`, `watchStrip`, `movesCard`, `solverCard`, `tilesCard`, `watchCard` | four hosts remain: the topbar, the gaps strip, the squad card, the foot |
| 353-417 | the topbar stat tiles | six of the seven tiles are working, not decisions: squad value, XI median and the xPts sum answer no question about this gameweek. The two load-bearing numbers, `header.free_transfers` and `bank_tenths`, move onto the transfer line where the decision they budget for lives |
| 418-449 | the chip-status strip | chip history is below-fold working. The chip verdict is a mark or a band line; the ledger opens from it |
| 450-465 | the provenance banner | it prints `provenance_source`, `picks_gw` and `as_of`, which is `squad_source` restated. The squad-source block is load-bearing and there should be exactly one of it |
| 810-1046 | the entire agent briefing: `intelProvChip`, `generateBtn`, `generateIntel`, `intelNumChip`, `briefingDupes`, `intelItem`, `intelOutdated`, `outdatedSummary`, `renderIntel` | a model-authored second voice on a payload-led page (§5.4). Deleting it also deletes the 35-line `briefingDupes` cross-reference layer, the `ib-xref` chips, the outdated fold, the rejected-items fold and a five-minute polling loop |
| 1047-1169 | `claimFor`, `alertRow`, `watchTileRow`, `renderWatchStrip` | a third list of rows between the pitch and the moves. Availability and price-fall are marks on the owned card; template gaps and rise targets are about players you do not own and belong on the Signals tab |
| 1321-1379 | `captainBlock` | the captain candidates table under the pitch. The pick is the `C` tag, the runner-up is a mark when `close_call` is true, and the table is the first block of the captain drawer |
| 1481-1520 | the four prose paragraphs under the lineup: `db-xiline`, `footLine`, two `db-legendnote` lines, the `sq.notes` loop | four explanatory paragraphs under a picture of a football team. The colour legend becomes one hover on the band; the source line is the squad-source block; `sq.notes` raises a gaps row |
| 1523-1625 | `moveFace`, `gwsText`, `moveSentence`, `moveEl`, `renderMoves` | a separate list of transfer candidates competing with the transfer verdict. Each move is a `SELL` tag and a `BUY` ghost; `moveSentence`'s rule templates move into the drawer. `moveFace` survives as the ghost renderer, about 15 of the 103 lines |
| 1626-1943 | the solver card: `fullDetailLink`, `unconstrainedLine`, `lastLogLine`, `renderSolver`, `solveRunningEl`, most of `rerunButton` | the card restates the transfer and captain verdicts in a second currency, and under today's `stale` state it renders one sentence and a button. The plan's moves are tags, its gain, gap, hits, alternatives and notes are the transfer drawer. `rerunButton` and `startSolvePolling` survive as the solver gaps row's control, about 50 of the 318 lines |
| 1944-2066 | `tileText`, `tileEl`, `renderTiles` | six tiles about players you do not own, ranked by gate margin, above the working. Moves to the Signals tab unchanged |
| 2067-2133 | `renderWatch` | twelve rows of check, status, detail. The audit of the gates, not the answer. Moves to the Checks tab unchanged |
| 2163-2231 | `renderStanding` | where the season stands is history. It belongs, and it belongs on the Season tab |
| 2333-2343 | the provenance foot's three lines | the topbar clock carries the page's age and the drawers carry each block's. Three more clock lines at the bottom is the repetition this design exists to remove |

**Kept and rewritten, not deleted:** `renderPitch` (1381-1480) becomes the mark
renderer; `pcard` (1252-1312) loses the minutes chip and gains the tag;
`renderGaps` (2233-2331) keeps its five rules and gains the refresh control;
`renderVerdict` and `verdictRow` (471-809) are replaced by four small mark
renderers keyed on `verdict.lines[].question`, and the payload block stays
load-bearing; `drillTo` (331-344), `openDrawer` (313-317), `pulsePitch`
(319-330), `oppChip` (1202-1229), `easeClass` (274-284), `pjClass`
(1176-1185), `tierWord` (1193-1201) and `riskOf` (1186-1192) survive
unchanged. `ageText` and `fmtSpan` are replaced by the day formatter of §6.2.

---

## 11. Risks

### 11.1 The squad source is the single point of failure, and the block is load-bearing

This is the design's central exposure and it is worse than it is for a
list-first page. The primary object is the fifteen. `dashboard_brief` cannot
name ten of them (§1.8), so the page depends on `squad_overview` answering.
When it does not, §8 says what renders: five named players and ten numbered
slots, which is a worse page than today's, because today's page degrades to a
list of alerts and tiles that still say something.

There are three failure modes, and they are different:

- **Unavailable.** `squad_overview` raises or returns empty. `brief.py:226-231`
  already turns this into a `source_gap` alert and three `gap` watch rows, so
  the gaps strip fills correctly. The squad does not.
- **Stale.** Today's state. `squad_source.live` is false, the fifteen are the
  GW4 public picks, `as_of` is 5 days old, and every mark on the pitch was
  computed on a team the owner may no longer hold. A drawn squad is more
  convincing than a list, so this design makes that error more dangerous, not
  less. The band and the outline tags are the mitigation and they are a weak
  one, for the same reason nobody reads the disclaimer under a chart.
- **Wrong.** `squad_overview` answers with a fifteen that is neither live nor
  the last closed gameweek. `brief.py:517-548` already detects this against
  the solver plan (`superseded`) but nothing detects it against the account.

Mitigation, in order: ship §5.3 so the brief serves the fifteen itself and the
page makes one call; keep the gaps row first and the band persistent; draw
stale marks in outline. **Abandonment test:** if §5.3 does not land, or if
`squad_overview` fails at all over a month of daily reads, this design does
not ship, because its failure state is a page of integers.

### 11.2 A stale page still tells you what to do, in outline

The judgement in §6.6 is that a stale decision is drawn rather than hidden.
That is the opposite of the choice `brief.py:384-387` makes for the solver
plan, which serves no plan body at all when stale, and it is deliberate: a
squad-first page that hides its marks is fifteen cards and no answer.

The risk is the obvious one. An outline is a weaker signal than an absence,
the owner is reading at speed before a deadline, and today four of the five
input clocks predate the last deadline. Every mark on today's page would be an
outline, which means the distinction carries no information on the day it
matters most. **Abandonment test:** if in a live week the owner acts on an
outlined mark and the action was priced against the wrong fifteen, outline
mode is replaced by suppression: stale marks are removed and the band carries
the count of decisions that could not be drawn.

### 11.3 One tag per card is an untested rule against unseen collisions

§4.2 sets the precedence `transfer > captain > bench > chip` and demotes the
losers to small marks. Today's payload has no collision: the `C` is on
B.Fernandes, the `START`/`SIT` pair is on the two keepers, no transfer is
named and no chip is spent. So the rule ships exercised only by synthetic
cases.

The collisions that will happen: a bench-boost week puts a chip mark on all
fifteen while a sell leg also sits on a benched player; a captain who is also
the out-leg of a plan; a `SIT` on a player carrying an availability flag,
where the flag is the reason for the sit and belongs beside it rather than
under it. **Abandonment test:** if in the first month a real payload produces
a card whose demoted marks change what the owner would do, the tag stops being
singular and becomes a two-slot row, at a cost of 12px of card height the
budget in §3.4 does not currently have.

### 11.4 On a quiet week the page looks like it did no work

Four verdict lines, and two of them name no player today (`no_move_named`,
`no_chip_named`). Those live on the band as sentences. On a week where the
solver rolls and the bench is confirmed, the pitch is fifteen cards, one `C`,
and three band lines. A reader who wanted a page to argue with will conclude
the page is empty rather than that the week is quiet. The mitigation is that
the band prints the rule and its reason, which is a finding, and that
`watch_log`'s twelve checks are one click away on the Checks tab under the
principle `home.js:2130-2132` already states: absence of a signal means
checked and clear, never did not look.

### 11.5 The drawer becomes the product

Every piece of working moves behind a click: the candidates table, the gates,
the dissent, the fixture ranks, the minutes, the solver's notes and
alternatives. If the drawer is slow, or a block of it is permanently empty,
the design has hidden the evidence rather than removed the clutter. Two blocks
are at risk of being permanently empty today: the market block, because
`price_radar` serves nothing comparable for this season yet, and the minutes
block, because `xmins` is null on all fifteen. Both currently print their
source panel's own reason, which is worth shipping; a third empty block would
not be.

### 11.6 The pitch does not survive 390px

§3.3 abandons the pitch below 640px and renders fifteen strips. The design's
central claim is that the squad laid out as a squad is the right primary
object, and on a phone that claim is not delivered: the formation is a word,
the ties become adjacency, and the spatial memory of where a player sits is
gone. Thirteen of the fifteen fit above the fold, which is more than the
desktop layout manages, so the narrow form is better at everything except
being the thing this design argues for.
