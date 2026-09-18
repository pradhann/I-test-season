# Creators, design C: the subtraction

> Every number in this document was measured on 2026-09-18 against the running
> app at `http://localhost:8321/#creators`, the four live panel payloads, and
> the two new panels run read-only through `Warehouse.read_copy()` with the
> worktree module on the path. Pixel figures come from
> `getBoundingClientRect().top + scrollY` walks of `#view` at an emulated
> 1280x900 and again at an emulated 390x844. Network figures come from
> `performance.getEntriesByType('resource')` on one mount. Line ranges come
> from a scan of `web/dist/js/views/creators.js` that matches each declaration
> to the closing brace at its own indent, not from reading. Where a figure
> could not be measured it says so.

---

## 0. The one sentence

> **The creators tab is three lists: the creators, one creator's episodes, one
> episode. Everything the tab draws today that is none of those three is
> deleted.**

Design C adds no component. It adds two panel calls, `creator_episodes` and
`episode_summary`, and it spends the rest of its budget removing. The measured
target is `web/dist/js/views/creators.js` at 3,880 lines, a default page of
2,041 px that reaches 8,306 px when its two folds are opened, and 38 panel
runs carrying 3.92 MB on one visit.

The second sentence, printed under the level-1 header, states the one thing
the page refuses to do:

> A recording published to a podcast feed and to YouTube is two rows here,
> because nothing stored joins them. The page shows both and counts both, and
> it does not merge what the warehouse has not merged.

---

## 1. What is on the page today, measured

### 1.1 The page, region by region, at 1280 px with every fold in its default state

`innerWidth` 1280, `document.documentElement.scrollHeight` **2,041**. All seven
`<details>` elements closed, which is how they mount.

| region | selector | top | height |
|---|---|---:|---:|
| record + sources card | `section.card.cx` | 67 | **299** |
| heading | `h2` | 82 | 19 |
| honesty line | `p.cx-honest` | 105 | **65** |
| board scope | `p.cx-scope` | 179 | **46** |
| coverage | `p.cx-scope` | 234 | **65** |
| source strip | `div.cx-sources` | 306 | **44** |
| board card | `section.card.cx` | 382 | **1,451** |
| window fact row | `div.toolbar.cx-factrow` | 397 | **73** |
| gameweek picker | `div.toolbar` | 470 | **46** |
| gameweek note | `div.cx-gwnote` | 516 | 0 |
| content body | `div.cx-body` | 538 | **1,253** |
| main takes | `div.cx-sec.cx-maintakes` | 538 | **643** |
| the board chart | `div.cx-sec` | 1,204 | **316** |
| the armband | `div.cx-sec` | 1,542 | **127** |
| the matrix, folded | `div.cx-sec` | 1,691 | **46** |
| the report cards, folded | `details.cx-sec.cx-rcsec` | 1,758 | **33** |
| provenance | `div > .provenance` | 1,802 | 16 |
| add a source card | `section.card.cx` | 1,849 | **136** |

Opening the two big folds takes the document to **7,639 px**. Opening the four
inner folds as well takes it to **8,306 px**.

### 1.2 The matrix is the worst block on the page, and the measurement says so exactly

Open, `details.cx-disclose.big.cx-matrix` is **3,997 px** tall. Inside it,
`table.cx-grid` is **3,825 px** tall and **278 px** wide, sitting in a
**1,019 px** container. It holds **57** `tbody` rows, **5** header cells (one
gutter plus four players) and **84** cell marks. Its own summary reads
"Said vs owned: 4 shows, 54 people x 4 players".

So: 3,825 vertical pixels and 27% of the available width, to show 84 marks
about four players. At 390 px the same fold is **4,783 px**.

### 1.3 The report-card wall is the second worst

Open, `details.cx-sec.cx-rcsec` is **1,660 px** at 1280 and **5,082 px** at
390. It draws **28** `button.cx-rcard` tiles. With its two inner folds open it
reaches 2,101 px. `creator_report_card` serves 31 cards over 134,432 bytes and
the wall is the only consumer of 28 of them.

### 1.4 The network cost of one visit

`performance.getEntriesByType('resource')`, filtered to `/api/`, after one
mount and one player drawer:

| endpoint | calls | transferred | summed response time |
|---|---:|---:|---:|
| `scripts/creator_detail/run` | **29** | **3,193 KB** | 108,216 ms |
| `scripts/creator_board/run` | **7** | **566 KB** | 25,946 ms |
| `scripts/creator_report_card/run` | 1 | 132 KB | 312 ms |
| `content/sources` | 1 | 25 KB | 331 ms |
| `scripts/squad_overview/run` | 2 | 9 KB | 2,600 ms |
| `scripts/fixture_board/run` | 1 | 139 KB | 437 ms |
| `scripts/player_chatter/run` | 1 | 6 KB | 394 ms |
| `deadline` | 2 | 1 KB | 411 ms |
| **total** | **44** | **3.98 MB** | |

The page-load subtotal, before the drawer, is **39 requests and 3.92 MB**.

The 29 `creator_detail` calls come from `loadRecency` at `creators.js:290-309`,
which asks every creator on the board for its whole 60-day record so that the
48-hour window chip can be computed in the browser. The six extra
`creator_board` calls come from `probeGameweeks` at `creators.js:470-491`,
which asks for GW1 through GW7 so the gameweek chips can carry a count.

Timings from the same entries: the first board response lands at **1,941 ms**,
the last `creator_detail` at **7,196 ms**, the last board probe at
**10,251 ms**. The "all history" toggle is `disabled` until the 7,196 ms mark
and the gameweek chips read a single ellipsis until 10,251 ms.

### 1.5 Type and colour, rendered

Walking every element in `#view` that owns a non-empty text node:

* **12 distinct rendered font sizes**: 9px (47 nodes), 9.5px (27), 10px (104),
  10.5px (114), 11px (20), 11.5px (79), 12px (57), 12.5px (79), 13px (43),
  13.5px (3), 15px (22), 16px (1).
* **9 distinct text colours**, of which three carry 565 of the 596 nodes
  (`--faint` 258, `--ink` 165, `--muted` 142).
* **211 interactive elements** (`button, a, summary, [role=button], input,
  select, [tabindex]`).

`web/dist/creators.css` is **1,074 lines**, declares **13** distinct
`font-size` values, uses **10** `font:` shorthands, and holds **19** distinct
padding, margin and gap values: 1, 1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
13, 14, 16, 18, 20, 22 px.

Rendered text: **2,666 characters over 124 non-blank lines** with the folds
closed, **12,676 characters over 453 non-blank lines** with them open.

### 1.6 At 390 px

A true 390 CSS pixel viewport is reachable in this harness: `innerWidth` 390,
`document.documentElement.scrollWidth` 390, no page-level horizontal scroll.

| region | 1280 | 390 |
|---|---:|---:|
| document, folds closed | 2,041 | **2,919** |
| document, two folds open | 7,639 | **12,682** |
| record card | 299 | 456 |
| honesty line | 65 | **130** |
| coverage line | 65 | **102** |
| source strip | 44 | **99** |
| window fact row | 73 | **177** |
| gameweek picker | 46 | **121** |
| main takes | 643 | **953** |
| board chart | 316 | 314 |
| armband | 127 | 153 |
| matrix, open | 3,997 | **4,783** |
| report cards, open | 1,660 | **5,082** |
| add a source | 136 | 201 |

The board chart's SVG keeps a 1000-unit viewBox and scrolls inside its own
container: at 390 the scroller is **332 px** wide over **700 px** of content,
so the whole chart is behind a horizontal drag.

### 1.7 What the board actually has to draw this week

`POST /api/scripts/creator_board/run {}` returns 58,376 bytes: GW5,
`window_days` 30, **29** creators, **5** consensus rows, `scope.applied`
"panel" with 29 shows and 4 excluded names. Five players. The page spends
2,041 px and 3.92 MB on five players.

### 1.8 What the two new panels have to draw

Measured through `Warehouse.read_copy()` with the worktree module on the path.

`creator_episodes {"creator": "Let's Talk FPL", "include_untranscribed": true}`:

* `counts` = `{episodes_total: 73, transcribed: 35, analysed: 67}`.
* `transcription_state` across the 73: **transcribed 35, queued 27, none 11**.
  No row is `failed`.
* `source_kind` across the 73: **podcast 48, youtube 25**.
* **53 distinct titles over 73 rows. 20 title groups hold more than one row,
  and all 20 are one podcast row beside one YouTube row.** Eighteen of the 20
  carry an analysis on both rows. Fifteen of the 20 are transcribed on both
  rows.
* The publication gap inside a pair, in minutes: 0.3, 0.6, 0.6, 1.1, 1.4, 2.2,
  2.5, 3.1, 4.5, 4.9, 6.9, 8.1 for the first twelve, with one outlier at
  1,492.
* The 2026-09-08 pair holds **36,095** transcript characters on the podcast row
  and **33,067** on the YouTube row, and 22 collapsed claims each. One
  recording, two transcriptions, two stored analyses.
* **32 rows carry an analysis with no transcript.** **31 analysed rows
  produced zero claims.** Six rows carry no analysis at all.
* No row has a null gameweek.

`episode_summary {"item_id": "d26fac8a02a1928c1a1ea9f6"}` (FPL GW4 Transfer
Tips, podcast, 2026-09-08): 6 summary bullets, **20** players, **19**
`transfers_suggested`, **4** `captain_view`, **1** gap, GW4 by the calendar
rule, `analysis_model` `claude-opus-5`, `analysed_at` 2026-09-08T13:34:25Z.

Two shapes in that payload decide level 3:

1. **The same sentence arrives twice under two `kind` values.** Barcola carries
   a `claim` with `stored_in` `llm:claude-opus-5`, `direction` `avoid`,
   `confidence` 0.6, and a `call` with `stored_in` `transfers_out`, `direction`
   `avoid`, `conviction` `medium`, both with quote "for 8 million, I would want
   more security about his game time. So I personally wouldn't go there" and
   both with `start_s` 513.46. The `players[].claims` list is two stored
   channels, not two opinions.
2. **`captain_view` is not about this episode's gameweek.** The four calls are
   Haaland GW5, Haaland GW7, Haaland GW9 and Isak GW4, on an episode whose own
   `gameweek` is 4. Drawing them as a flat list of four captain calls for GW4
   would be wrong four times over.

Corpus-wide, for scale: `content_item` holds **1,008** rows over **27**
creators, `transcript_segment` holds **144,815** segments over **170** items,
`content_analysis` covers **799** items, and `content_transcribe_skip` holds
**6** rows (`no_captions` 4, `relevance:2` 1, `ConnectError` 1) spread over FPL
Fran, FPL Raptor and FPL Harry. So `failed` is a state that exists and is rare,
and every design has to draw it from six rows.

### 1.9 One roster fact that changes level 1

`episodes._known_creators` returns **33** names. Two of them are
"The FPL Wire" and "The FPL Wire - Fantasy Premier League".
`creator_board.scope.excluded` already holds
`["The FPL Wire - Fantasy Premier League", "X / Twitter", "r/FantasyPL",
"user-shared"]`, so the board has resolved the split and the episode panel has
not. Level 1 is built on the board's roster for that reason, and says how many
names the scope left out.

---

## 2. The field-by-field map

Six payloads. Every element on the three levels names the key it reads.
Nothing on any level is computed in the browser except a count of rows already
in the payload, and each of those is marked. Nothing is printed that no payload
carries.

### 2.1 Which panel each level calls, and what a click costs

| level | panel calls | count |
|---|---|---:|
| mount, level 1 | `creator_board {}` | **1** |
| level 1 row click, level 2 | `creator_episodes {creator}` | **1** |
| level 2 filter toggle | `creator_episodes {creator, include_untranscribed: true}` | **1** |
| level 2 record fold | `creator_report_card {creator}` | **1**, only on open |
| level 2 row click, level 3 | `episode_summary {item_id}` | **1** |
| level 3 back link | cached level 2, no call | **0** |

Mount goes from **38 panel runs and 3.92 MB** to **1 panel run and 58 KB**.
Every deeper level is one call, and every call is cached by its key for the
life of the visit.

**Panels the page stops calling.** `creator_detail` (all 29 calls),
`squad_overview`, `player_chatter` and `GET /api/content/sources`. The six
extra `creator_board` probes stop. `creator_report_card` survives with a
`creator` parameter and a lazy trigger; `creator_board` survives with one call.
`player_chatter` is still rendered by `web/dist/js/components/playerdrawer.js:19`
and `web/dist/js/views/template.js:62`, so dropping it here costs nothing.
`creator_detail` is called from nowhere else, which is a test problem, in §9.4.

### 2.2 Level 1, the roster

Source: `creator_board` only. One call, one row per `result.creators[]`, sorted
by `last_item_at` descending.

| element | payload key | note |
|---|---|---|
| card heading "Creators" | none, a literal | the only literal on level 1 |
| record sentence | `result.record_note` | printed verbatim, one line |
| coverage sentence | `result.coverage.note` | printed verbatim, one line |
| behind-feeds fold | `result.coverage.by_source[].source_key`, `.unread`, `.newest_unread` | folded, summary is the row count |
| scope line | `result.scope.applied`, `result.scope.shows.length` | "Board scope: panel, 29 shows." |
| excluded fold | `result.scope.excluded[]` | summary is the array length |
| scope reason | `result.scope.reason` | appended when not null |
| squad reason | `result.mine_reason` | appended when not null |
| read-age chip | `result.as_of` | days, with `provenance.generated_at` in the footer |
| gameweek word in the heading | `result.gw`, `result.gw_reason` | `gw_reason` on the title attribute |
| **name cell, line 1** | `creators[].creator` | |
| **name cell, kind tag** | `creators[].kinds[]` | joined, e.g. "podcast, youtube" |
| **name cell, line 2** | `creators[].latest.title` | the newest publication, verbatim, clamped to one line |
| name cell, line 2 link | `creators[].latest.url`, `creators[].latest.kind` | the verb comes from `kind`, never guessed off the extension |
| name cell fallback | `creators[].latest_reason` | drawn in place of line 2 when `latest` is null |
| **column 1, LAST** | `creators[].last_item_at` | whole days |
| **column 2, EPISODES** | `creators[].n_items_window` of `creators[].n_items` | the window is `result.window_days` |
| **column 3, CLAIMS** | `creators[].n_claims_window` | |
| **column 4, RECORD** | `creators[].record.hit_rate`, `.scored`, `.weight`, `.earned`, `.wilson_lo95` | rate and n in the cell, `wilson_lo95` and `weight` on the title |
| record absent | `creators[].record.reason` | printed when `record` is null or unscored |
| entry tag on the name | `creators[].entry.entry_id`, `.verified`, `.name` | a tag, not a column |
| entry absent | `creators[].entry_reason` | on the title attribute |

**Dropped from level 1, with the reason.** `result.consensus[]` entirely: it is
a player-first rollup and the hierarchy is creator-first. `result.panel_squads`
entirely: it exists to feed the matrix. `creators[].sources[]`,
`creators[].take`, `creators[].take_reason`: `take` is non-null for 4 of 29
creators today and the same content is served per episode by
`episode_summary`, in full, at level 3. `result.creators[].entry.people[]`
beyond the count.

**Not called at all on level 1.** `creator_report_card`, because
`creators[].record` already carries `scored`, `hits`, `hit_rate`,
`wilson_lo95`, `weight`, `earned` and `reason`. The record column needs no
second payload. That single observation removes 132 KB and 28 rendered cards
from the mount.

### 2.3 Level 2, one creator's episodes

Source: `creator_episodes`, plus `creator_report_card {creator}` behind a fold.

| element | payload key | note |
|---|---|---|
| drawer title | `result.creator` | |
| drawer subtitle | `result.entry.name`, `.entry_id`, `.verified` | |
| subtitle fallback | `result.entry_reason` | |
| record line | `result.record.hit_rate`, `.hits`, `.scored`, `.wilson_lo95`, `.weight`, `.earned` | the same `_RECORD` shape level 1 drew |
| record absent | `result.record.reason` | |
| counts line | `result.counts.transcribed` of `result.counts.episodes_total`, and `result.counts.analysed` | verbatim counts, no arithmetic |
| filter toggle label | `result.include_untranscribed` | echoed by the panel, so the page states which list it is showing |
| filter toggle badge | `counts.episodes_total - counts.transcribed` | a subtraction of two payload integers, marked as such |
| row cap note | `result.limit`, `result.episodes.length` | "30 of 73 shown" when the cap bites |
| read age | `result.as_of` | days |
| **row: state chip** | `episodes[].transcription_state` | four values, the enum is closed in the schema |
| **row: title** | `episodes[].title` | verbatim, the link target |
| **row: link verb** | `episodes[].source_kind` | podcast and link take "play audio", youtube and blog take "open episode" |
| **row: link href** | `episodes[].source_url` | null renders as an unlinked title with a reason |
| **row: age** | `episodes[].published_at` | whole days |
| **row: gameweek** | `episodes[].gameweek` | with `episodes[].gw_reason` on the title attribute |
| **row: claims** | `episodes[].claim_count` | |
| **row: transcript size** | `episodes[].transcript_chars` | drawn only when not null |
| **row: model** | `episodes[].analysis_model` | drawn only when `analysis_present` |
| **row: no analysis mark** | `episodes[].analysis_present` | false draws a hollow mark, no colour |
| row key | `episodes[].item_id` | the parameter level 3 takes |
| record fold | `creator_report_card {creator}` `cards[0]` | lazy, §8 |

**Dropped.** Nothing in `creator_episodes` is dropped. Every one of the twelve
`_EPISODE` fields and all three `_COUNTS` fields are on the row or its title
attribute.

### 2.4 Level 3, one episode

Source: `episode_summary` only.

| element | payload key | note |
|---|---|---|
| back link | `result.creator` | returns to the cached level 2 |
| title | `result.episode.title` | verbatim |
| state chip | `result.episode.transcription_state` | same chip as level 2 |
| source link | `result.episode.source_url`, `.source_kind` | same verb rule |
| age | `result.episode.published_at` | days |
| gameweek | `result.gameweek` | `result.gw_reason` printed under it, not hidden in a tooltip |
| transcript size | `result.episode.transcript_chars` | |
| claim count | `result.episode.claim_count` | |
| read-by line | `result.analysis_model`, `result.analysed_at` | "read by claude-opus-5, 10 days ago" |
| **SUMMARY** | `result.summary_bullets[]` | one list item per bullet, verbatim |
| summary absent | `result.summary_reason` | replaces the list, never sits beside it |
| **TRANSFERS, in** | `transfers_suggested[]` where `direction` is `in` | |
| **TRANSFERS, out** | `transfers_suggested[]` where `direction` is `out` | |
| transfer name | `transfers_suggested[].display_name`, `.name`, `.disambiguator`, `.resolved` | the spoken `name` on the title when `resolved` is false |
| transfer stance | `transfers_suggested[].stance` | the stored word, printed as a word |
| transfer conviction | `transfers_suggested[].conviction` | |
| transfer quote | `transfers_suggested[].quote` | the reused claim row |
| transfer link | `transfers_suggested[].deep_link`, `.start_s` | "at 4:53" when `start_s` is not null |
| transfer gameweek | `transfers_suggested[].gameweek` | drawn when not null |
| pairing note | `transfers_suggested[].paired_with` | always null, and `gaps` says why |
| **CAPTAIN**, grouped | `captain_view[]` grouped by `.gameweek` | the group heading is the gameweek, from the rows |
| captain fields | `captain_view[].display_name`, `.stance`, `.conviction`, `.quote`, `.start_s`, `.deep_link` | |
| **PLAYERS** | `players[]` in served order | most-said first, as the panel sorts them |
| player head | `players[].display_name`, `.team`, `.position`, `.code`, `.resolved`, `.disambiguator` | |
| player unresolved | `players[].resolved` false, `players[].name` | "as said: Isaac" beside the head |
| player claim count | `players[].claims.length` | a count of rows already in hand |
| claim channel | `players[].claims[].kind`, `.stored_in` | `claim` and `call` are labelled, never merged |
| claim direction | `players[].claims[].direction` | a word, no hue, see §6.2 |
| claim strength | `players[].claims[].confidence` or `.conviction` | whichever is non-null, with its unit named |
| claim quote | `players[].claims[].quote` | the reused claim row |
| claim link | `players[].claims[].deep_link`, `.start_s` | |
| claim gameweek | `players[].claims[].gameweek` | |
| **GAPS** | `gaps[].section`, `gaps[].gap` | one line each, verbatim |
| read age | `result.as_of` | |

**Dropped.** Nothing. All fourteen top-level keys of `SUMMARY_RESULT` are
drawn.

### 2.5 The rule-9 traps in this payload, and where design C refuses

Three facts a designer will want to print and no panel serves.

1. **"This analysis read the show notes."** 32 of Let's Talk FPL's 73 rows
   carry `analysis_present: true` with `transcription_state` of `queued` or
   `none`, and 31 analysed rows produced zero claims. The reason is that the
   analysis ran over `content_item.text` where `text_source` is `description`.
   `_EPISODE` does not carry `text_source`. So the page prints the state chip,
   the `analysis_present` mark, and the panel's own gap sentence, "no
   transcript segment is stored for this episode, so no quote can be located in
   time and every start_s is null", and it does not name the show notes.
   §9.1 asks for `text_source` on the row.
2. **"These two rows are one recording."** Nothing in `_EPISODE` joins them and
   the panel's own docstring says the grouping key is `urls.canonical_key`,
   which a megaphone enclosure and a YouTube watch URL do not share. Counting
   distinct titles in the browser would be a browser-computed fact. Design C
   draws both rows, adjacent because the list is newest-first, and states the
   refusal in the level-1 header sentence quoted in §0.
3. **"The transcription failed because of no_captions."** `transcription_state`
   carries `failed` and nothing carries the `content_transcribe_skip.reason`
   behind it. Six rows in the whole corpus are in this state. The chip says
   `failed` and the row says nothing more. §9.1 asks for a `state_reason`.

---

## 3. Wireframes

### 3.1 Level 1 at 1280 px

Content column is 1280 minus the 168 px rail minus 44 px of frame padding,
1,068 px; inside the card's 16 px padding, 1,036 px of usable width. Columns:
name 520, LAST 100, EPISODES 130, CLAIMS 100, RECORD 186.

```
┌ CREATORS ───────────────────────────────────────────────────────────────────────────────┐
│ No creator has beaten a coin flip yet. 21 of 22 measured creators have at least one      │ record_note
│ scored claim and none has a 95% Wilson lower bound above 0.5 over the season.            │
│ 308 of 653 items published in the last 30 days have been read. 345 are fetched and       │ coverage.note
│ still queued: the analysis budget caps how many are read per night.                      │
│ Board scope: panel, 29 shows.   ▸ 4 sources outside it, not read here                    │ scope
│ ▸ 8 feeds are behind                                                                     │ coverage.by_source
│                                                                                          │
│ A recording published to a podcast feed and to YouTube is two rows here, because         │
│ nothing stored joins them. The page shows both and counts both.                          │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ CREATOR                                          LAST      EPISODES     CLAIMS    RECORD │
│ ──────────────────────────────────────────────────────────────────────────────────────── │
│ Fantasy Football Scout        blog podcast yt   5 days    254 / 306        297   50% n 204│
│   Scout Notes: the newest title, clamped to one line                        ↗            │
│ Let's Talk FPL                    podcast yt    6 days     39 / 73         495   46% n 390│
│   FPL GW4 Final Thoughts, Team News, Triple Captain Palmer                  ▶            │
│ FPL Harry                         podcast yt    6 days     38 / 66         387   47% n 333│
│   ...                                                                                    │
│ Fantasy Football Hub              podcast yt    6 days     36 / 58         368   38% n 345│
│ FPL Raptor                        podcast yt    6 days     34 / 59         333   50% n 299│
│ AllAboutFPL                          blog       6 days     33 / 43         251   40% n 320│
│ The FPL Wire                       podcast      6 days     23 / 42         141   40% n 118│
│ FPL Fran                              yt        6 days     12 / 12          68   41% n 68 │
│ FPL BlackBox                      podcast yt    6 days      8 / 21          14   39% n 28 │
│ Planet FPL                        podcast yt    6 days     30 / 62           8   25% n 8  │
│ The 59th Minute                    podcast      6 days      7 / 11           0   no scored│
│ ...                                                                                      │
│ Always Cheating                    podcast      never       0 / 0            0   no scored│
│   nothing has been fetched from this source                                              │
├──────────────────────────────────────────────────────────────────────────────────────────┤
│ 29 creators, GW5 window, 30 days. creator_board, read today.                             │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

`▶` marks a row whose newest publication has a transcript, `↗` one whose
newest is a link only. Both are drawn from `latest.kind` and nothing else, and
both are a shape, not a hue.

### 3.2 Level 2 at 1280 px, in the existing drawer

`aside.cx-drawer` is `min(560px, 96vw)` at `creators.css:669`, measured at
**560 px**. The drawer's own 18 px padding leaves 524 px.

```
                                          ┌ ────────────────────────────────────────── ✕ ┐
                                          │ Let's Talk FPL                               │
                                          │ podcast, youtube · entry 41 · Andy LTFPL     │
                                          │                                              │
                                          │ 46% of 390 scored calls hit. Wilson lower     │
                                          │ bound 0.41, weight 0.00, not earned.         │
                                          │ ▸ How the record is measured                 │
                                          │                                              │
                                          │ 35 of 73 publications have a transcript on   │
                                          │ file. 67 carry a stored analysis.            │
                                          │ [ show the 38 without a transcript  +38 ]    │
                                          ├──────────────────────────────────────────────┤
                                          │ ● transcribed                                │
                                          │   FPL GW4 Transfer Tips, SELL Bruno,         │
                                          │   Palmer IN                                  │
                                          │   10 days · GW4 · 22 claims · 36,095 chars   │
                                          │   podcast · claude-opus-5     play audio ↗   │
                                          ├──────────────────────────────────────────────┤
                                          │ ● transcribed                                │
                                          │   FPL GW4 Transfer Tips, SELL Bruno,         │
                                          │   Palmer IN                                  │
                                          │   10 days · GW4 · 22 claims · 33,067 chars   │
                                          │   youtube · claude-opus-5   open episode ↗   │
                                          ├──────────────────────────────────────────────┤
                                          │ ● transcribed                                │
                                          │   My FPL Wildcard Team for Gameweek 4,       │
                                          │   Triple Chelsea                             │
                                          │   11 days · GW4 · 21 claims · 32,750 chars   │
                                          │   podcast · claude-opus-5     play audio ↗   │
                                          ├──────────────────────────────────────────────┤
                                          │ ...                                          │
                                          ├──────────────────────────────────────────────┤
                                          │ 30 of 73 shown, newest first.                │
                                          │ creator_episodes, read today.                │
                                          └──────────────────────────────────────────────┘
```

The two rows at the top are the duplicate pair: same title, 12 minutes apart,
36,095 and 33,067 transcript characters. They sit next to each other because
the list is newest-first, and the differing character counts are the evidence
that they are two stored transcriptions. The page asserts nothing about them.

### 3.3 Level 3 at 1280 px, same drawer

```
                                          ┌ ────────────────────────────────────────── ✕ ┐
                                          │ ‹ Let's Talk FPL                             │
                                          │ FPL GW4 Transfer Tips, SELL Bruno, Palmer IN │
                                          │ ● transcribed · podcast · 10 days            │
                                          │ 36,095 transcript characters · 22 claims     │
                                          │ play audio ↗                                 │
                                          │                                              │
                                          │ GW4. GW4 is the gameweek this episode was    │
                                          │ published into: its deadline is the first to │
                                          │ fall after the publication date. The claims  │
                                          │ below keep whatever gameweek each targets.   │
                                          │ Read by claude-opus-5, 10 days ago.          │
                                          ├ SUMMARY ─────────────────────────────────────┤
                                          │ • Chelsea's attack is the main target for    │
                                          │   GW4 with Hull at home, and Andy argues ... │
                                          │ • Liverpool's attack is a hold, not a buy:   │
                                          │   fine for Fulham at home, but ...           │
                                          │ • Isak is treated as post-Ipswich hype ...   │
                                          │ • On Arsenal, the defence is where the ...   │
                                          │ • Cheap forwards are plentiful: Barry at ... │
                                          │ • De Cuyper is a buy at 4.8m with the ...    │
                                          ├ TRANSFERS SUGGESTED · 19 ────────────────────┤
                                          │ IN                                           │
                                          │   Palmer            buy · high               │
                                          │   " ... "                        at 4:53 ↗   │
                                          │   João Pedro        buy · medium             │
                                          │   " ... "                        at 4:17 ↗   │
                                          │   Rogers            buy · medium             │
                                          │   Barry             buy · medium             │
                                          │ OUT                                          │
                                          │   Barcola           avoid · medium           │
                                          │   " ... "                        at 8:33 ↗   │
                                          │   ...                                        │
                                          │ Which sale funded which purchase is not      │
                                          │ recorded and is not inferred here.           │
                                          ├ CAPTAIN · 4 ─────────────────────────────────┤
                                          │ GW4                                          │
                                          │   Isak            captain · low              │
                                          │   " ... "                       at 12:34 ↗   │
                                          │ GW5                                          │
                                          │   Haaland         captain · medium           │
                                          │   " ... "                       at 21:33 ↗   │
                                          │ GW7                                          │
                                          │   Haaland         captain · medium           │
                                          │ GW9                                          │
                                          │   Haaland         captain · medium           │
                                          ├ PLAYERS · 20 ────────────────────────────────┤
                                          │ ▸ Haaland      MCI FWD      6 stored         │
                                          │ ▸ Isak         LIV FWD      4 stored         │
                                          │ ▾ Wirtz        LIV MID      4 stored         │
                                          │     claim  llm:claude-opus-5  avoid  0.80    │
                                          │     "I definitely wouldn't be looking to     │
                                          │      bring him in"              at 11:19 ↗   │
                                          │     claim  llm:claude-opus-5  hold   0.60    │
                                          │     "I'd probably keep for Fulham at home."  │
                                          │                                  at 11:04 ↗  │
                                          │ ▸ Barcola      LIV MID      2 stored          │
                                          │     1 claim row, 1 analysis call, one quote  │
                                          │ ...                                          │
                                          ├ GAPS ────────────────────────────────────────┤
                                          │ transfers_suggested.paired_with: transfers   │
                                          │ are stored as two independent lists, so      │
                                          │ which sale funded which purchase is not      │
                                          │ recorded and is not inferred here.           │
                                          ├──────────────────────────────────────────────┤
                                          │ episode_summary, read today.                 │
                                          └──────────────────────────────────────────────┘
```

Two decisions visible in that frame. `captain_view` is grouped by its rows'
own `gameweek`, because three of the four calls are not about this episode's
GW4. `players[].claims` prints `kind` and `stored_in` on every row, and a
player whose rows are one `claim` plus one `call` with an identical quote and
an identical `start_s` carries a one-line note saying so, built from the two
fields already in hand.

### 3.4 Level 1 at 390 px

Four columns do not fit in 390 px. The row becomes two lines and the four
columns become one metrics line, in the same order, separated by the app's
existing middle dot.

```
┌ CREATORS ──────────────────────────────┐
│ No creator has beaten a coin flip yet. │
│ 21 of 22 measured creators have at     │
│ least one scored claim and none has a  │
│ 95% Wilson lower bound above 0.5.      │
│ ▸ 345 items fetched and still queued   │
│ ▸ Board scope: panel, 29 shows         │
│                                        │
│ A recording published to a podcast     │
│ feed and to YouTube is two rows here.  │
├────────────────────────────────────────┤
│ Fantasy Football Scout                 │
│   blog, podcast, youtube               │
│   Scout Notes: the newest title ...    │
│   5 days · 254/306 · 297 · 50% n 204   │
├────────────────────────────────────────┤
│ Let's Talk FPL                         │
│   podcast, youtube                     │
│   FPL GW4 Final Thoughts, Team News,   │
│   Triple Captain Palmer                │
│   6 days · 39/73 · 495 · 46% n 390     │
├────────────────────────────────────────┤
│ FPL Harry                              │
│   podcast, youtube                     │
│   ...                                  │
│   6 days · 38/66 · 387 · 47% n 333     │
├────────────────────────────────────────┤
│ ...                                    │
├────────────────────────────────────────┤
│ 29 creators. creator_board, read today.│
└────────────────────────────────────────┘
```

No horizontal scroll, no SVG, no table. The order of the four values is the
order of the four columns at 1280, so the same reading habit carries across.

### 3.5 Level 3 at 390 px

The drawer is `min(560px, 96vw)`, so at 390 it is 374 px wide with 18 px
padding, 338 px of content. The same sections in the same order; the quote and
its timestamp stack instead of sharing a line.

```
┌ ─────────────────────────────────── ✕ ┐
│ ‹ Let's Talk FPL                      │
│ FPL GW4 Transfer Tips, SELL Bruno,    │
│ Palmer IN                             │
│ ● transcribed · podcast · 10 days     │
│ 36,095 chars · 22 claims              │
│ play audio ↗                          │
│                                       │
│ GW4. GW4 is the gameweek this episode │
│ was published into: its deadline is   │
│ the first to fall after the           │
│ publication date.                     │
│ Read by claude-opus-5, 10 days ago.   │
├ SUMMARY ──────────────────────────────┤
│ • Chelsea's attack is the main target │
│   for GW4 with Hull at home, and ...  │
│ • Liverpool's attack is a hold, not a │
│   buy: fine for Fulham at home ...    │
│ • ... four more                       │
├ TRANSFERS SUGGESTED · 19 ─────────────┤
│ IN                                    │
│   Palmer                              │
│   buy · high                          │
│   " ... "                             │
│   at 4:53 ↗                           │
│   João Pedro                          │
│   buy · medium                        │
│   ...                                 │
│ OUT                                   │
│   ...                                 │
├ CAPTAIN · 4 ──────────────────────────┤
│ GW4                                   │
│   Isak                                │
│   captain · low                       │
│   " ... "                             │
│   at 12:34 ↗                          │
│ GW5                                   │
│   Haaland                             │
│   captain · medium                    │
│ ...                                   │
├ PLAYERS · 20 ─────────────────────────┤
│ ▸ Haaland   MCI FWD    6 stored       │
│ ▸ Isak      LIV FWD    4 stored       │
│ ▸ Wirtz     LIV MID    4 stored       │
│ ...                                   │
├ GAPS ─────────────────────────────────┤
│ transfers_suggested.paired_with:      │
│ transfers are stored as two           │
│ independent lists ...                 │
├───────────────────────────────────────┤
│ episode_summary, read today.          │
└───────────────────────────────────────┘
```

---

## 4. Empty, stale, error and gap states

Ages are whole days everywhere. `app.js:137-152` `fmtSpan` returns `1h 12m`
under 48 hours and `10d 6h` under 14 days, and the live drawer prints both
("10d 6h ago" and "16 days ago" twelve pixels apart, measured). Design C adds
one helper, `ageDays(iso)`, which returns `today`, `1 day`, `N days`, and uses
it on all three levels. `relAge` survives for the one place a freshness class
is still drawn, which keeps `fmtAge` imported; §9.4 covers the test that pins
that import.

### 4.1 Level 1

| state | trigger | what is drawn |
|---|---|---|
| loading | before `creator_board` resolves | `skeleton(3)` at `creators.js:320-328`, unchanged |
| the call failed | `runPanel` throws | `errBox(e)` in the card body; the header lines are absent, not blank |
| no creators | `creators[]` empty | `emptyBox("The board returned no creator.", result.scope.reason or "creator_board.scope carries no reason.")` |
| a creator with no items | `n_items` 0 and `last_item_at` null, 4 of 29 today | LAST reads "never"; EPISODES reads "0 / 0"; the name's second line is `latest_reason`, or "no item is stored for this source" when that is null too |
| a creator with no record | `record` null or `record.scored` 0, 8 of 29 today | RECORD reads "no scored call" with `record.reason` on the title, never a zero |
| a creator under the floor | `record.earned` false | the rate is drawn and the title says `weight 0.00, not earned`; the row is not reordered |
| stale board | `as_of` older than `window_days` | the footer age changes class; the sentence stays `record_note` |
| scope excluded | `scope.excluded` non-empty, 4 today | a fold whose summary is "4 sources outside it, not read here" |
| coverage behind | `coverage.by_source[].unread` above 0, 8 today | a fold whose summary is the count; each line is `source_key`, `unread`, `newest_unread` in days |

### 4.2 Level 2

| state | trigger | what is drawn |
|---|---|---|
| loading | before `creator_episodes` resolves | `skeleton(1)` under the head |
| panel returned empty | `result.empty` with `reason` | the reason verbatim; the two `empty()` reasons this panel writes are the missing-corpus sentence and the unknown-creator sentence, and the second one lists every name on file |
| creator not on the episode roster | the board serves a name `_known_creators` does not | the unknown-creator reason, which names the 33 on file. The live case is "The FPL Wire" against "The FPL Wire - Fantasy Premier League"; §1.9 |
| **no transcribed episode** | `counts.transcribed` 0 with `episodes_total` above 0 | `emptyBox("No episode from this creator has a transcript on file.", "N publications are stored. The list below is empty because the transcribed filter is on.")`, with the toggle live and its badge showing `episodes_total` |
| **untranscribed row** | `transcription_state` `none` | grey dot, the word `none`, no transcript-size figure, and the title attribute carries the payload's own gap sentence once level 3 is opened. The row is still a link to level 3 |
| **queued row** | `transcription_state` `queued` | amber dot, the word `queued`. 27 of Let's Talk FPL's 73 rows |
| **failed row** | `transcription_state` `failed` | red dot, the word `failed`, and nothing else, because no field carries the skip reason. Six rows in the whole corpus. §9.1 asks for the field |
| **analysed with no transcript** | `analysis_present` true, state not `transcribed` | the model is drawn, the character count is absent, and the row carries no claim of a transcript. 32 of 73 today |
| **no analysis** | `analysis_present` false | a hollow mark and no model. 6 of 73 today |
| **the duplicate pair** | two adjacent rows with the same `title`, different `source_kind`, minutes apart | both drawn, both counted, nothing merged. The character counts differ (36,095 and 33,067) and that difference is the only evidence on the page. The header's second sentence explains the shape once, for the whole list |
| row cap | `episodes.length` equals `limit` and is under `counts.episodes_total` | "30 of 73 shown" and a button that re-calls with `limit: 200` |
| record unavailable | `creator_report_card` throws on the fold | `failFold(err, "The record could not be read:")`, the existing helper at `creators.js:161-173` |
| filter on | `include_untranscribed` true | the toggle reads `aria-pressed="true"` and the counts line is unchanged, because `counts` is computed before the filter and the panel says so |

### 4.3 Level 3

| state | trigger | what is drawn |
|---|---|---|
| loading | before `episode_summary` resolves | `skeleton(2)` |
| item not found | `result.empty` | the panel's reason, which distinguishes an unknown id, a discarded item, and an item published after this instant |
| **discarded item** | the discard reason | "Nothing was deleted: restore it through the link ledger to read it again", verbatim |
| **no analysis** | `summary` null, `summary_reason` set, `gaps[0].section` `analysis` | the summary section holds the reason instead of a list; the transfers, captain and players sections are absent, not empty, because the gap already names them; the claims that exist are still drawn under PLAYERS |
| empty summary list | `analysis_present` true, `summary_bullets` empty | the `summary_reason` sentence "the stored analysis carries an empty summary list" |
| no transcript | `gaps` holds the `timestamps` section | every `start_s` is null, so no row carries a timestamp and the gap line says why. The link is the episode link, never a guessed offset |
| no players | `players` empty | the `players` gap sentence |
| no transfers | `transfers_suggested` empty | the `transfers_suggested` gap sentence |
| no captain calls | `captain_view` empty | the `captain_view` gap sentence |
| unresolved name | `players[].resolved` false, `code` null | the display name is the spoken string and the row carries "as said" beside it; no team, no position, because the payload has none |
| duplicate evidence | a player's `claims` hold one `kind: claim` and one `kind: call` with equal `quote` and equal `start_s` | one line: "1 claim row, 1 analysis call, one quote". Both rows stay expandable; neither is dropped |
| captain for another week | `captain_view[].gameweek` not equal to `result.gameweek` | the gameweek group heading carries it, and the section head says nothing about the episode's own week |
| stale read | `analysed_at` older than `published_at` by many days | "Read by claude-opus-5, 10 days ago" beside "published 10 days ago", both in days |

---

## 5. The deletion list

This is the centre of design C. Thirty-four blocks, with the measured pixel
cost of each on the current page and the reason it goes. Line ranges are
inclusive and come from the declaration scan, not from reading.

Notation: **cut** removes the code. **move** takes it out of `creators.js`
without losing the capability. **fold** keeps it and changes where it opens.

### 5.1 The blocks

| # | lines | what | lines out | measured cost today | verdict and reason |
|---:|---|---|---:|---|---|
| D1 | 26-33 | `sv()`, the SVG element helper | 8 | 0 px on its own | **cut.** Its only caller is `drawBoard` (D21) |
| D2 | 64-78 | `linkKind()` | 15 | 0 px | **cut.** It guesses the verb from the URL extension because no payload said. `_EPISODE.source_kind` says, for every row |
| D3 | 96-102 | `LANES`, `laneLabel` | 7 | 0 px | **cut.** Your squad lane is a board concept, and the board goes |
| D4 | 104-119 | `canonicalKey()` | 16 | 0 px | **move** with D20. It is the paste bar's session duplicate guard |
| D5 | 201-207 | `STATE_ORDER`, `HIDDEN_STATES` | 7 | 0 px | **cut** with D18. These order the 7 source states, not the 4 transcription states |
| D6 | 284-318 | the claim recency index, `loadRecency`, `freshFor`, `agoText`, `H48` | 35 | 0 px, **29 `creator_detail` calls, 3,193 KB, 108,216 ms of summed response time, and 7,196 ms before the "all history" toggle is enabled** | **cut.** The most expensive block in the file by a wide margin. It exists to compute a 48-hour window that `episodes[].published_at` gives per row for free |
| D7 | 330-339 | `squadByCreator`, `squadsAsked`, `boardCache`, `gwCount`, `gwProbing`, `BACK`, `FWD` | 10 | 0 px | **cut** with D12 and D29 |
| D8 | 347-382 (partial) | the `squad_overview` call and the lane derivation | 16 | 0 px, 1 panel call | **cut.** Lanes feed the board wedge, the matrix "You" row and the drawer's lane word, all of which go |
| D9 | 384-441 | `grp`, `buildRows`, `byWeightOfMouth`, `inWindow`, `wantH48`, `windowed`, `currentRows` | 58 | 0 px directly | **cut.** 58 lines of browser-side derivation over `result.consensus`, which has 5 rows this week |
| D10 | 458-556 | the gameweek axis: `gwCandidates`, `probeGameweeks`, `selectGw`, `renderGwRow` | 99 | **46 px** at 1280, **121 px** at 390, **6 extra `creator_board` calls, 566 KB total for the endpoint, last probe at 10,251 ms** | **cut.** A gameweek is a property of an episode, and `_EPISODE.gameweek` with `gw_reason` carries it per row with a sentence saying how it was derived |
| D11 | 558-628 | the window fact row and `toggle()` | 71 | **73 px** at 1280, **177 px** at 390 | **cut.** Two toggles whose only job is to widen a window the level-2 list does not have |
| D12 | 630-653 | `loadSources`, `visibleSources`, `reloadSources` | 24 | 0 px, 1 GET, 25 KB | **cut** with D14 |
| D13 | 740-769 | `cardCensus`, `censusLine` | 30 | 0 px directly | **cut.** The census exists to head the report-card wall (D24) |
| D14 | 793-820 | `recordSentence`, `mainShows` | 28 | 0 px | **cut.** `recordSentence` is the fallback the file's own comment says the board writes better; `record_note` is served on every board payload measured |
| D15 | 822-1071 | `sourceStrip`, `sourceTable`, `actionCell`, `fetchSource`, `refusal`, `fetchMainShows`, `seqLine` | 250 | **44 px** closed, **1,623 px** open with a **1,571 px** 40-row table; **99 px** closed at 390 | **cut.** A 43-feed operator console on a reading surface. The one control worth keeping, "fetch this creator's feed", is 30 lines on level 2 against `creators[].sources[].key` |
| D16 | 1073-1134 | `runAnalyse`, `analyseLine` | 62 | 0 px until pressed | **cut.** The file's own comment at 1073 reads "Same route the Pipelines tab uses, same confirm gate, same poll". Two implementations of one metered confirm |
| D17 | 1136-1958 | the whole link bar: `renderLinkBar`, `failLine`, the job driver, `applyPoll`, `classifyError`, the expiry gate, `decide`, `abortJob`, `renderJob`, `againRow`, `renderPreview`, `pathLabel`, `secs`, `fmtWhen`, `renderLedger`, `renderTake`, `jobStateLabel` | 823 | **136 px** at 1280, **201 px** at 390 | **move** to `web/dist/js/components/ingest_link.js`, imported on demand by an "Add an episode" button on level 2, where pasting a link belongs. 21% of the file, no capability lost, and the move is a move, not a deletion |
| D18 | 1968-2210 | `renderBoard`'s board section, `drawBoard`, `boardLegend` | 243 | **316 px** at 1280, **314 px** at 390 with a **332 px** container over **700 px** of content | **cut.** A hand-built 1000-unit SVG scatter with its own tooltip, its own packing algorithm and its own legend, for 4 players |
| D19 | 2212-2373 | `renderMainTakes`, `decisionCard`, `countChip`, `crossLink` | 162 | **643 px** at 1280, **953 px** at 390; the two cards measure 154 and 185 px | **cut.** Player-first cards on a creator-first hierarchy. Two cards this week |
| D20 | 2422-2475 | `recordStrip` | 54 | **99 px** | **cut.** A leaderboard over `creator_report_card`, which level 1 no longer calls |
| D21 | 2477-2519 | `renderArmband` | 43 | **127 px** at 1280, **153 px** at 390 | **cut.** `episode_summary.captain_view` carries captaincy per episode with the quote and the offset |
| D22 | 2521-2550 | `renderWatching` | 30 | **0 px today**, the section returns early when no creator has a watch call, which is every creator on the live board | **cut.** Thirty lines that render nothing |
| D23 | 2552-2581 | `renderMatrix`, `redrawGrid` | 30 | **46 px** closed | **cut** with D28 |
| D24 | 2583-2691 | `renderReportCards`, `reportCard` | 109 | **1,660 px** open at 1280, **5,082 px** open at 390, 28 tiles | **fold, then cut the wall.** §8 |
| D25 | 2728-2764 | `teamLine`, `baselineWord` | 37 | part of the 1,660 | **cut.** The compact card's team line. `reportBody` draws the team channel in full and does not call `teamLine` |
| D26 | 2918-3294 | `renderGrid` | 377 | **3,997 px** open at 1280, **4,783 px** at 390; a **278 px** wide table in a **1,019 px** container, **3,825 px** tall, 57 rows, 84 marks | **cut.** The single largest function in the file and the single largest block on the page |
| D27 | 3307-3358 | `loadSquads`, `panelSquadWord` | 52 | 0 px, part of the 29 `creator_detail` calls | **cut** with D26 |
| D28 | 3417-3431 | `openCreator` | 15 | 0 px | **cut.** Level 1 opens level 2 directly by name |
| D29 | 3464-3489 | `transfersSection` | 26 | part of the drawer's 900 px | **cut.** A creator's FPL transfers are a squad fact on a reading surface; `creator_detail` goes with it |
| D30 | 3506-3511 | `heldOnBoard` | 6 | 0 px | **cut** with D31 |
| D31 | 3513-3737 | `openPerson` | 225 | drawer scroll **900 px** for a show, more for a sole host | **cut.** The person drawer answers "what do they own that they never talk about", which is a fourth level and a sideways move. §9.2 names the loss |
| D32 | 3739-3771 | `byCreatorOf`, `openPlayerByCode` | 33 | 0 px | **cut** with D31 and D33 |
| D33 | 3773-3879 | `openPlayer` | 107 | drawer scroll **2,786 px**, of which the chatter strip is **479 px**, plus 2 panel calls and 145 KB | **cut.** A player drawer reached from a creator row is a sideways move. The player view already exists on Projections and Template through `playerdrawer.js` |
| D34 | 23-24 | the import line | 0 net | | **rewrite.** `faceImg`, `fmtPrice`, `fmt1`, `postJSON` lose their last callers |

### 5.2 The arithmetic

| | lines |
|---|---:|
| `creators.js` today | **3,880** |
| cut (D1 to D3, D5 to D16, D18 to D33) | **−2,264** |
| moved to `components/ingest_link.js` (D4, D17) | **−839** |
| subtotal kept | **777** |
| level 1, the roster table and its header | +120 |
| level 2, the episode list, counts, filter and record fold | +150 |
| level 3, the episode view and its five sections | +170 |
| shared: `ageDays`, the state chip, the episode row, the claim row adapter | +60 |
| the rewritten file comment | +20 |
| **`creators.js` after** | **≈1,277** |

Under the 1,500 budget with 223 lines of headroom, which is the margin for the
things a build always finds.

### 5.3 What is kept, and why each survives

| lines | what | why it survives |
|---|---|---|
| 35-62 | `parseTs`, `relAge`, `clock`, `plural` | `clock` formats `start_s` into "at 4:53"; `plural` is pinned by a test and is correct |
| 80-94 | `tier()` | fed `claims[].stored_in`, it already names `llm:` and `cue` and says what each means |
| 121-199 | `pct`, `signed`, `COIN`, `coin`, `statusLine`, `failFold`, `unscored`, `verdict` | the record line on levels 1 and 2, and the folded error body |
| 209-241 | the shell and the one-per-visit drawer | levels 2 and 3 live in it |
| 269-282 | `dn`, `personName`, `personsOf` | `personsOf` reads `entry.people` for the level-1 entry tag |
| 320-328 | `skeleton()` | all three levels |
| 685-738 | `coverageLine`, `scopeLine` | level 1's header, shortened to one line each plus a fold |
| 771-791 | `honestyLine` | level 1's first line, reading `record_note` |
| 2375-2420 | `rcChip`, `rcTag` | the record cell on level 1 and the record line on level 2 |
| 2693-2727 | `rangeBar`, `gwBars` | `reportBody` draws both |
| 2766-2916 | `openCard`, `reportBody` | the record fold on level 2, §8 |
| 3296-3305 | `detailFor` | renamed `episodesFor`, the same promise cache keyed by creator |
| 3360-3415 | `quoteBlock`, `drawerHead` | the claim row and the drawer head, §7 |
| 3433-3462 | `recordLine` | level 2's record line |
| 3491-3504 | `methodFold` | level 2's record fold |

### 5.4 The CSS that goes with it

`web/dist/creators.css` is 1,074 lines. The deletions take these sections
whole:

| lines | section | goes with |
|---|---|---|
| 73-249 | the link bar and the preview halt, 177 lines | D17, moves to `ingest_link.css` |
| 250-311 | toolbar bits, the gameweek picker, the mark vocabulary, 62 lines | D10, D11 |
| 312-395 | the deadline board, the tooltip, the legend, 84 lines | D18 |
| 396-529 | the decisions and the armband, 134 lines | D19, D21 |
| 515-529 | watch calls, 15 lines | D22 |
| 592-666 | the said/owned grid, 75 lines | D26 |
| 688-732 | a person and their team, 45 lines | D31 |
| 773-781 | row-level recency, 9 lines | D6 |
| 791-856 | the segmented source bar and the source table, 66 lines | D15 |
| 902-975 | the report cards wall, 74 lines | D24, D25 |

That is roughly 740 lines of 1,074. The kept sections are the quotes block
(530-561), the drawer (667-687), the skeleton (752-772), the state chip and its
dots (800-841, reused for `transcription_state`), the report-card chip and its
under-floor rules (857-901), the record line (976 onward) and the narrow-width
block (733-751), which needs one new rule for the level-1 row at 390.

---

## 6. Type scale, spacing and colour

### 6.1 Type: two body sizes, no new token

Measured today: **12 distinct rendered font sizes** in `#view`, and 13 declared
in `creators.css`.

Design C introduces **no new size**. Every size on the page is an existing
`app.css` or `creators.css` token:

| role | size | token, and where it is already defined |
|---|---:|---|
| body, every table cell and every row | 12.5px | `table.data` at `app.css:136` |
| sub-line, every reason, age, count and gap | 11.5px | `.cx-drawer .sub` at `creators.css:672`, `.cx-rc` family |
| column head and section head | 11px uppercase | `table.data th` at `app.css:139` |
| drawer section head | 12px uppercase | `.cx-drawer h2` at `creators.css:670` |
| card head | 13px uppercase | `.card > h2` at `app.css:113` |

**Two body sizes**, 12.5 and 11.5. The other three are label tokens on
existing components and none of them carries a sentence. The measured distinct
rendered sizes fall from **12 to 5**.

Gone from the page with their blocks: 9px (the board tick labels and the
gameweek bar labels, 47 nodes), 9.5px (the matrix column heads, 27 nodes), 10px
(the matrix gutter and the legend, 104 nodes), 10.5px (`.tlabel` and the pitch
sub, 114 nodes), 13.5px (3 nodes), 15px (22 nodes), 16px (1 node).

### 6.2 Spacing: four values

`creators.css` holds 19 distinct spacing values today. Design C keeps four,
each already in `app.css`:

| value | role | already at |
|---:|---|---|
| 4px | the gap inside a cell, between a value and its unit | `.empty b` margin, `app.css:129` |
| 8px | the gap between siblings in a row, and the toolbar gap | `.toolbar` gap, `app.css:240` |
| 14px | the card's vertical padding and the drawer's section margin | `.card` padding, `app.css:109`; `.cx-drawer h2` margin |
| 16px | the card's horizontal padding and the gap between cards | `.card` padding and margin, `app.css:109` |

Table cells keep `table.data`'s own `5px 10px`, which is the component's
padding and not a new token. Nothing else is spaced by hand.

**One defended addition.** The level-1 row needs a `border-top: 1px solid
var(--line)` between rows at 390, where the row is three lines tall and the
table rule is the only thing separating one creator from the next. `--line`
exists; the rule is new and it is one declaration.

### 6.3 Colour: the status tokens, plus one accent

The FDR palette (`--fx-d1` to `--fx-d5`, `fixtures.css:53-80`) is not used on
this tab, because no quantity here is a fixture difficulty. The budget is
therefore one accent beyond it.

* **The state dot** uses the three reserved status tokens through the existing
  rules at `creators.css:800-841`: `--good` for `transcribed`, `--warn` for
  `queued`, `--bad` for `failed`, and a hollow ring for `none`. Those tokens
  are reserved for status by `app.css:12` and always ship with the word beside
  them, which the existing `.cx-state` markup already does.
* **The one accent is `--s1`** (#2a78d6, identical in both themes). It marks
  the selected creator on level 1, the selected episode on level 2, and the
  focus ring, which `app.css:105` already sets to `--s1`.
* **`--s2` goes.** The tab currently uses `--s1` and `--s2` as an IN and OUT
  direction pair on the board, the matrix, the decision cards and the count
  chips. All four blocks are deleted.

**Why direction is a word and not a hue at level 3.** `_EVIDENCE.direction` is
declared `{"type": "string"}` with no enum, and the live payload already holds
`captain`, `avoid`, `hold`, `buy`, `triple_captain` and `unknown`. A closed
two-hue map over an open string field would paint an unseen value with a colour
that asserts something the payload did not. The direction is printed as the
stored word, in `--ink`, at 12.5px.

That is **one accent, three reserved status tokens, and the three ink tiers**.
Measured distinct text colours fall from 9 to 4.

---

## 7. Components reused, by function name and line

Nothing new is designed. Six existing pieces carry the three levels.

| component | where it lives | level 1 | level 2 | level 3 |
|---|---|---|---|---|
| **the drawer** | `creators.js:227-240` (one per visit, Escape handler), `creators.css:667-673` (`min(560px, 96vw)`, measured 560 px) | the click target | the container | the container, same element, back link swaps the body |
| **`drawerHead(title, sub)`** | `creators.js:3405-3415`, `.dhead` at `app.css:270-274` | | the creator head | the episode head, with `‹ creator` as the `sub` |
| **`quoteBlock(text, claim, item)`** | `creators.js:3360-3395`, `.cx-quote` at `creators.css:530-561` | | | every transfer, captain call and player claim. It already reads `extractor` through `tier()`, `conviction`, `confidence`, `gameweek`, `deep_link` and `start_s`, which are the exact field names `_EVIDENCE`, `_TRANSFER_CALL` and `_CAPTAIN_CALL` use. The one change is `claim.extractor` becoming `claim.stored_in` |
| **the state chip** | `.cx-state` and `.cx-statedot` at `creators.css:800-841` | | the `transcription_state` chip on every row | the same chip in the episode head |
| **`rcChip(name, dir)` / `rcTag(name)`** | `creators.js:2381-2420`, `.cx-rc` at `creators.css:857-901` including the under-floor `.few` rules | the RECORD cell, fed `creators[].record` | the record line, fed `result.record` | |
| **`reportBody(c, host)`** | `creators.js:2786-2916`, with `rangeBar` 2696-2711 and `gwBars` 2713-2727 | | inside `methodFold` at 3493-3504 | |
| **`skeleton(cards)`** | `creators.js:320-328`, `.cx-skel` at `creators.css:752-772` | the mount | the drawer load | the drawer load |
| **`failFold(e, lead)` / `statusLine(e)`** | `creators.js:149-173`, `.cx-raw` at `creators.css` | a failed board | a failed record fold | a failed summary |
| **`emptyBox(reason, hint)` / `errBox(e)`** | `app.js:40-48` | no creators | no transcribed episode | item not found |
| **`table.data`** | `app.css:136-146` | the roster table | the episode list | |
| **`provenance(prov)`** | `app.js:50` | the card footer, unchanged | | |

The only new markup is the level-1 `<tr>` and the level-2 episode `<li>`, and
both are compositions of the above.

---

## 8. The report card: how it stays folded, and where it opens

The owner's instruction is that the report-card wall stays folded by default.
Design C goes one step further and deletes the wall, keeping the card.

**Today.** `renderReportCards` at `creators.js:2591-2655` draws a
`<details class="cx-sec cx-rcsec">` that is closed on mount. Closed it is
**33 px**. Opened it is **1,660 px** at 1280 and **5,082 px** at 390, and it
draws 28 `button.cx-rcard` tiles from a 134,432-byte payload fetched on every
mount whether or not anybody opens it.

**After.**

1. **The wall is deleted** (D24, D25). Twenty-eight tiles that rank creators
   against each other is a comparison the reading hierarchy does not make, and
   the tile is a lossy preview of the card that sits one click below it.
2. **The verdict travels as a chip**, which is how it already travels. On
   level 1 the RECORD column is `rcChip` fed `creators[].record`, so the
   verdict is on every creator row without a second payload. The existing
   `.few` rules at `creators.css:872-901` still desaturate a record under the
   floor, and `verdict(cl, floor)` at `creators.js:194-199` still refuses to
   read it as a rank.
3. **The full card opens at level 2, lazily, for one creator.** The drawer head
   carries `▸ How the record is measured`, which is `methodFold(show)` at
   `creators.js:3493-3504` unchanged. On first open it calls
   `creator_report_card {creator}` and hands `cards[0]` to `reportBody`, which
   draws all three channels in full: claims with the Wilson range, by
   gameweek, by action; team with the entry links and the repeated-entry
   warning; numeric with its reason.
4. **It is never called on mount.** `CARD_PARAMS` accepts `creator`, so the
   132 KB and the 31 cards are replaced by one card, fetched only when
   somebody asks. If nobody opens a record fold, the page never calls it.
5. **The census moves into the fold's own summary.** `cardCensus` and
   `censusLine` (D13) exist to head a wall of 31; with one card in view the
   sentence the fold needs is `cards[0].headline` and `min_scored_claims`,
   which `reportBody` already prints.

So: folded by default on level 2, never on level 1, never on mount, and one
creator at a time.

---

## 9. Risks, losses and the tests that break

### 9.1 What the payloads do not carry, and what design C asks for

Three fields, each of which would remove a refusal in §2.5.

1. **`_EPISODE.text_source`.** Thirty-two of Let's Talk FPL's 73 rows carry an
   analysis with no transcript, and 31 analysed rows produced zero claims. The
   reader cannot tell a transcript-read episode from a show-notes-read one
   except by the absence of `transcript_chars`. `content_item.text_source` is
   already read by `_representative` and `_transcription_state`; serving it on
   the row is one dictionary key.
2. **`_EPISODE.state_reason`.** `transcription_state: "failed"` has no reason
   beside it. `content_transcribe_skip.reason` is already queried by
   `_ids_in(wh, "content_transcribe_skip", ...)`; six rows in the corpus carry
   `no_captions`, `relevance:2` and `ConnectError`.
3. **A publication group id.** Twenty of Let's Talk FPL's 73 rows are ten
   recordings published twice. `_families` already computes a grouping key; if
   the key that joins a podcast enclosure to its YouTube twin does not exist,
   then serving the current `canonical_key` on the row at least lets a future
   page say "these two share a key" or "these two do not", instead of the page
   saying nothing. Without it, design C draws two rows and says so, which is
   correct but is not what the owner wants to read.

### 9.2 What a user loses that they use today

Six capabilities, named rather than waved at.

1. **The quiet-holdings view.** `openPerson` at `creators.js:3513-3737`
   answers "what does this person own that they never mention", by joining
   `creator_detail.squad` against every claim in the record. It draws "Quiet
   holdings: owns him, never mentions him" and "Talked about, does not own".
   Nothing in `creator_episodes` or `episode_summary` can rebuild it, because
   neither serves a squad. This is the largest single loss and it is a real
   one. The mitigation is that `creator_detail` is not deleted, only
   un-called, so the view can return as a fold on level 2 in a later commit
   for the cost of one 540 KB call per creator.
2. **The player-first entry point.** Today a player name anywhere on the tab
   opens a drawer with every creator's quote about him across the window.
   After the subtraction, reaching a player's quotes means knowing which
   episode said it. `player_chatter` still serves exactly that question and is
   still rendered by `playerdrawer.js` on Projections and Template, so the
   capability survives on the app, not on this tab.
3. **The panel consensus.** `result.consensus` is dropped entirely. Nobody on
   this tab will see "four people named Haaland captain" again. The
   counter-argument is that the live board has 5 consensus rows and spends
   2,041 px on them.
4. **The said-versus-owned matrix.** Deleted. It is the only surface that
   crosses what a creator said against what they hold. It is also 3,825 px of
   table at 278 px wide for 84 marks.
5. **The source console.** Fetching a feed by hand, fetching the main shows in
   sequence, and triggering `content_analyse` all leave this tab. The
   per-creator fetch button on level 2 replaces the first; the Pipelines tab
   already owns the third.
6. **The paste bar leaves this file.** It is a move, not a deletion, and the
   button that opens it sits on level 2. A reviewer who counts it as a
   deletion is right to; the 823 lines still exist, in another file.

### 9.3 The design risks

1. **Level 2 is two rows per recording.** Twenty of 73 rows for Let's Talk FPL
   are duplicates. The owner's stated pain is clutter, and a list that shows
   ten recordings twice is clutter that design C chose not to remove, because
   removing it means asserting a join the warehouse does not hold. If the
   reviewer weighs the owner's clutter complaint above rule 9, this is where
   design C loses.
2. **`counts.analysed` 67 against `counts.transcribed` 35 reads as a
   contradiction.** The page prints both numbers verbatim and cannot explain
   the gap without `text_source` (§9.1). A reader who sees "67 analysed, 35
   transcribed" and opens one of the 32 will find a summary of the show notes
   with no timestamps and one gap sentence.
3. **The stored summary bullets carry em dashes.** Two of the six live GW4
   bullets hold one, the Isak bullet and the De Cuyper bullet, each joining a
   clause about a price to a clause about minutes. That is third-party model
   output stored in `content_analysis`, rendered verbatim. The house rule
   governs authored UI strings, and
   `test_no_rendered_string_in_the_creators_tab_carries_an_em_dash` scans JS
   string literals, so neither is violated. Rewriting a stored sentence at
   render time would be editing the payload, which rule 8 forbids. The page
   prints it as stored.
4. **`creator_episodes` scans a creator's whole corpus per call.** The measured
   read for Let's Talk FPL builds 73 families and runs six side reads plus
   `claims_visible_at`. That is one call per creator click instead of 29 on
   mount, but it is not a small call, and the level-2 open needs a skeleton.
   No latency figure is quoted here because the panel is not on the running
   server and the read-copy timing is not a server timing.
5. **The level-1 name cell carries a second line.** The column budget is four
   plus the name. The newest title lives inside the name cell, which is a
   reading of "plus the name" that a reviewer may refuse. The defence is the
   owner's own sentence, "who has said what recently": the title is the what,
   and it is one payload key, `creators[].latest.title`.
6. **Four of 29 creators have zero items and eight have no scored record.**
   Level 1 draws 29 rows of which roughly a third carry "never" and "no scored
   call". A list that is a third empty is a different kind of clutter, and the
   only honest fix is fewer tracked sources, which is not a UI decision.

### 9.4 The tests that break

`tests/unit/test_web_contract.py` holds **14** creators tests at lines 1049 to
1192, plus one global test at line 65. Eleven of the fifteen break. Each one
is a rule that still matters, so each needs rewriting deliberately in the same
commit rather than deleting.

| line | test | breaks on | what the commit owes it |
|---:|---|---|---|
| **65** | `test_every_panel_script_is_rendered_by_some_view` | **both halves.** The `unknown` half fails the moment `runPanel("creator_episodes", ...)` appears, because `fpl_edge/platform/panels.py` declares 15 panels and neither new script is among them (confirmed: the worktree changes `creators/__init__.py` and `tests/unit/test_creator_panel.py` and leaves `panels.py` untouched). The `unrendered` half fails because `creator_detail` is declared at `panels.py:165-172` and `creators.js:3299` is its only caller in the whole of `web/dist/js` | add two `Panel(...)` entries for `creator_episodes` and `episode_summary`, and either keep one `creator_detail` call or remove its `Panel`. This is the trap that fails the suite on the first commit |
| 1073 | `test_an_unscored_record_falls_back_to_the_payloads_reason` | asserts `src.count("unscored(") >= 5`. The file holds exactly 5 today; four of them are in `rcChip`, `reportCard` (D24), `renderReportCards` (D24) and `recordLine` | the surviving sites are `rcChip` and `recordLine`. Lower the floor to 2 and say why in the docstring |
| 1081 | `test_the_team_verdict_is_gated_on_quotable_not_on_a_null_boolean` | asserts `const one = p => p.quotable`, which lives in `teamLine` (D25) | the rule survives in `reportBody`'s `fact("beats the baseline", p.quotable` at 2766-2916. Drop the `one` assertion, keep the `fact` one |
| 1093 | `test_the_under_floor_class_is_actually_styled` | asserts `el("button", "cx-rcard " + v.cls` and `.cx-rcard.few` in the CSS. `reportCard` is D24 | the `.few` rule still matters for `.cx-rc` and `.cx-rctag`, which both survive. Drop the `.cx-rcard` selector and the JS assertion |
| 1106 | `test_one_creator_count_is_drawn_and_it_says_what_the_others_are` | asserts `function cardCensus()`, `function censusLine()` and `censusLine()} · floor`, all D13 | the defect it pinned was three different counts for one population twelve pixels apart. With one card in view there is one population. Rewrite it to assert that level 1 prints `creators.length` once |
| 1115 | `test_the_board_scope_and_its_own_note_are_rendered` | asserts `res.scope`, `sc.excluded`, `res.record_note`, `res.mine_reason`, `function scopeLine()` | **survives.** All five are on level 1's header by design |
| 1124 | `test_the_source_button_does_not_call_a_filtered_list_all_of_them` | asserts `fetchable sources` and `const dropped = total - shown.length`, both D15 | the whole source strip goes. Delete the test and record in its place that the strip moved, or move the test to whichever surface takes the console |
| 1132 | `test_the_record_strip_draws_no_permanently_empty_group` | asserts `Interval below a coin flip`, `if (!list.length) return null;` and `of them over the floor`, all in `recordStrip` (D20) | the rule, that a heading whose only possible content is "nobody" is not drawn, still applies to level 1's empty states. Rewrite against those |
| 1143 | `test_the_compact_team_line_is_capped_at_two_people` | asserts `const SHOWN = 2;`, in `teamLine` (D25) | delete with `teamLine`; `reportBody` draws every person, which is what the card is for |
| 1160 | `test_the_age_helper_is_the_shared_one` | asserts the `fmtAge` import and the literal `fmtAge(iso)`. Design C's "days, never hours" rule replaces `fmtSpan`'s `10d 6h` with `ageDays` | `relAge` at 40-46 survives for the one freshness class still drawn, so the literal survives. Add an assertion that `ageDays` exists and that no rendered age carries an `h` unit |
| 1184 | `test_the_loading_affordance_has_one_glyph_and_one_capitalisation` | asserts `src.count("measuring the record…") >= 3`. Five occurrences today, in `honestyLine`, `recordStrip` (D20), `rcChip`, `recordLine` and `reportCard` (D24) | two survive. Lower the count to 2 |

Three survive untouched: `test_no_rendered_string_in_the_creators_tab_carries_an_em_dash` (1049), `test_plural_returns_null_on_a_null_count` (1060) and `test_a_failed_panel_body_is_folded_not_interpolated` (1172), because `plural`, `statusLine`, `failFold` and the `failFold(rcErr` call site all survive.

`test_a_repeated_fpl_entry_is_named_as_a_repeat` (1151) survives, because
`cardsByEntry` and the `also on` warning live in `reportBody`, which level 2's
record fold keeps. The map has to be built from the one card the fold fetches
instead of all 31, so the cross-card warning can only fire when more than one
fold has been opened in a session. That is a real reduction in the warning's
reach and it belongs in the commit message.

`tests/unit/test_creator_panel.py` and the worktree's new
`tests/unit/test_creator_episodes.py` test the panels, not the view, and are
untouched by this design.

### 9.5 The one number that decides it

The creators tab today spends **3.92 MB over 38 panel calls and 2,041 pixels**
to show five players, and **8,306 pixels** once its folds are open. Design C
spends **58 KB over 1 panel call** to show 29 creators, and one call each for
73 episodes and for one episode's 6 bullets, 20 players, 19 transfers and 4
captain calls. If that trade is wrong, it is wrong because of §9.2.1, the
quiet-holdings view, and nothing else in the deletion list is close.
