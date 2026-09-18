# Creators: the ledger

Design A of three. The constraint this design was given, and does not hedge
on: every level is a dense sortable table in the Fixtures tab's idiom. Level 1
is one row per show. Level 2 is one row per episode. Level 3 is one row per
claim, grouped by player, with the quote in a cell that expands in place.
Drawers, never pages. No cards, no avatars, no charts, no SVG.

---

## 0. The one sentence

The creators tab is an archive of 1,100-odd published items, and an archive is
read by sorting it, so the page is three tables: who spoke and how long ago,
what one show published, and what one episode said about which player.

---

## 0.1 What the page costs today, measured

Measured on the running server at `http://localhost:8321`, repo sha
`5084317`, board `as_of 2026-09-18T10:58:55Z`, viewport 1280x900, at the
default theme. The creators tab renders; nothing errored.

**Network, on arrival, before any click.**

| call | n | transferred | wall time summed |
|---|---|---|---|
| `POST /api/scripts/creator_detail/run` | 29 | 3,193 KB | 111,576 ms |
| `POST /api/scripts/creator_board/run` | 7 | 566 KB | 25,617 ms |
| `POST /api/scripts/creator_report_card/run` | 1 | 132 KB | 362 ms |
| `GET /api/content/sources` | 1 | 25 KB | 334 ms |
| `POST /api/scripts/squad_overview/run` | 1 | 5 KB | 1,815 ms |
| `GET /api/deadline` | 1 | 0 KB | 244 ms |
| **total** | **40** | **3,921 KB** | |

The 29 `creator_detail` calls come from `loadRecency` (`creators.js:290-309`),
which fetches every creator's full 60-day item list so the board can age a
consensus row. One of those payloads, for `Let's Talk FPL`, is 540,400 bytes
on its own. The six extra `creator_board` calls come from `probeGameweeks`
(`creators.js:470-492`), which asks the panel for GW1 through GW7 so the
gameweek chips can carry a count badge.

**Page height.**

| state | 1280x900 | 390x844 |
|---|---|---|
| as it loads, five disclosures folded | 2,041 px | 2,919 px |
| every disclosure open | 8,306 px | 13,738 px |

Of the 8,306 px, the said-vs-owned matrix is 3,997 px and the report-card wall
is 2,101 px. The matrix summary reads `Said vs owned: 4 shows, 54 people x 4
players`, so 3,997 px of page draws a 54 by 4 grid.

**What the payload actually contains, against what the page emphasises.**

- `creator_board` serves 29 creators. 4 of them carry a non-null `take`
  (`Sky Sports FPL`, `FML FPL`, `FPL Family`, `FPL General`). 25 carry
  `take: null` with a `take_reason`.
- The section the page leads with is `Main takes`, 643 px at 1280, built from
  those 4 takes.
- `consensus` has 5 rows.
- `coverage.note`: 308 of 653 items published in the last 30 days have been
  read, 345 are fetched and still queued.
- `record_note`: no creator has beaten a coin flip, 21 of 22 measured
  creators have at least one scored claim, every earned weight is 0.0.
- 4 of 29 creators have `last_item_at: null` and `n_items: 0`.

**Source sizes.** `web/dist/js/views/creators.js` 3,880 lines.
`web/dist/creators.css` 1,074 lines. The Python package
`fpl_edge/platform/scripts/creators/` is 4,131 lines across seven modules.

**Row geometry**, measured by inserting a `table.data` with a header and five
rows into the live page at 1280 and reading `getBoundingClientRect`:
`th` 26.4 px, `tr` 29.1 px, `td` font-size 12.5 px, `th` font-size 11 px.
Every height in section 3 is computed from those two numbers.

---

## 1. The field map: six payloads, three levels

### 1.0 Which panel each level calls, and what a click costs

| level | panels called | calls per entry | bytes |
|---|---|---|---|
| 1, creators | `creator_board` | 1 | 58 KB measured |
| 2, one creator's episodes | `creator_episodes` | 1 | not measured, see note |
| 3, one episode | `episode_summary` | 1 | not measured, see note |
| the HIT column's definition | `creator_report_card` | 1, first open only | 134 KB measured |
| level 2's optional "their FPL team" fold | `creator_detail` | 1, on click only | 4,525 bytes measured |

The deepest path, level 1 to level 2 to level 3, is **three panel calls and
one payload per level**. Today's arrival is 40 calls and 3,921 KB.

Two notes on honesty. `creator_episodes` and `episode_summary` are not
registered on the running server, so their byte sizes were not measured; the
field map below is read off `EPISODES_RESULT` and `SUMMARY_RESULT` in
`.claude/worktrees/agent-af3096a66be70d4ce/fpl_edge/platform/scripts/creators/episodes.py`
and off the live sample in the brief. The `creator_detail` figure is measured:
called with `{creator, days: 1, limit: 1}` it returns 4,525 bytes carrying the
full 15-man squad and 15 transfers, against 540,400 bytes at its defaults. The
item list is what makes that payload large, and level 2 does not need it.

### 1.1 `creator_board` (`schema.py:316-378`)

Level 1 calls it once with `{}`, which applies `scope: panel`, `days: 30`,
`gw: null`, `mine: true`.

| payload key | level | where it renders |
|---|---|---|
| `as_of` | 1 | provenance footer |
| `window_days` | 1 | the `ITEMS 30D` and `CLAIMS 30D` column headers, which print the served number, not a constant |
| `gw` | 1 | the gameweek chip marked on |
| `gw_reason` | 1 | one line under the chip row, verbatim |
| `scope.applied` | 1 | header line, and the chip that toggles it |
| `scope.shows` | 1 | header line, as a count |
| `scope.excluded` | 1 | header line fold, one row per excluded show |
| `scope.reason` | 1 | header line fold |
| `creators[].creator` | 1 | column `SHOW`. The cell is the button that opens level 2 |
| `creators[].kinds` | 1 | column `FEEDS`, joined with `+` |
| `creators[].n_items` | 1 | column `ARCHIVE` |
| `creators[].n_items_window` | 1 | column `ITEMS 30D` |
| `creators[].n_claims_window` | 1 | column `CLAIMS 30D` |
| `creators[].last_item_at` | 1 | column `LATEST`, in days |
| `creators[].latest.title` | 1 | column `LATEST LINE`, rung 2 of the ladder in 1.7 |
| `creators[].latest.url` | 1 | the `LATEST LINE` cell's outbound link |
| `creators[].latest.item_id` | 1 | the `LATEST LINE` cell's second action, which opens level 3 directly on that episode |
| `creators[].latest.kind` | 1 | tag inside the `LATEST LINE` cell |
| `creators[].latest.text_source` | 1 | tag inside the `LATEST LINE` cell, reusing `.cx-src` |
| `creators[].latest.published_at` | 1 | title attribute of the `LATEST` cell |
| `creators[].latest_reason` | 1 | the `LATEST LINE` cell when `latest` is null |
| `creators[].take.summary_bullets[0]` | 1 | `LATEST LINE`, rung 1 |
| `creators[].take.model` | 1 | tag beside rung 1 |
| `creators[].take_reason` | 1 | `LATEST LINE`, rung 3 |
| `creators[].record.hit_rate` | 1 | column `HIT` |
| `creators[].record.scored` | 1 | column `N` |
| `creators[].record.hits` | 1 | `HIT` cell title |
| `creators[].record.wilson_lo95` | 1 | `HIT` cell title |
| `creators[].record.earned` | 1 | when false, the `HIT` cell draws faint through `.cx-underfloor` |
| `creators[].record.weight` | 1 | `HIT` cell title |
| `creators[].record.reason` | 1 | `HIT` cell when `scored` is null |
| `creators[].entry.name` | 1 | column `TEAM` |
| `creators[].entry.entry_id` | 1 | the `TEAM` cell link target |
| `creators[].entry.verified` | 1 | the `TEAM` cell prints the word the payload gives, never a tick alone |
| `creators[].entry.people[]` | 1 | the `TEAM` cell when `entry_id` is null: the count, and the names in the title |
| `creators[].entry.source_url` | 1 | `TEAM` cell link |
| `creators[].entry_reason` | 1 | `TEAM` cell when `entry` is null |
| `creators[].sources[]` (all eight keys) | 1, folded | the feeds fold, which is `sourceTable` unchanged |
| `record_note` | 1 | header sentence, verbatim |
| `coverage.note` | 1 | header sentence, verbatim |
| `coverage.items`, `.analysed`, `.window_days` | 1 | already inside `note`; not printed twice |
| `coverage.newest_unread` | 1 | header sentence, in days |
| `mine_reason` | 1 | header sentence, only when non-null. Live value is null |
| **`consensus[]`** | **dropped** | see 1.6 |
| **`coverage.by_source[]`** | **dropped** | see 1.6 |
| **`panel_squads`** | **dropped** | see 1.6 |
| **`take.transfers_in/out/captain/differentials/watching/chips`** | **dropped at level 1** | the same positions arrive at level 3 from `episode_summary`, with the quote and the episode they were said in. Level 1 shows one line, not nine lists |

### 1.2 `creator_episodes` (`episodes.py:989-1011`)

Level 2 calls it once with `{creator, limit: 200, include_untranscribed:
true}`. Every level 2 filter is then client-side over what came back.

| payload key | level | where it renders |
|---|---|---|
| `creator` | 2 | drawer head |
| `as_of` | 2 | drawer footer |
| `entry.name`, `.entry_id`, `.verified`, `.people[]` | 2 | one line under the head, so level 2 reads without level 1 |
| `entry_reason` | 2 | that line, when `entry` is null |
| `record.hit_rate`, `.scored`, `.hits`, `.wilson_lo95`, `.earned`, `.reason` | 2 | one line under the head, same wording as the level 1 `HIT` column |
| `include_untranscribed` | 2 | the filter row states which list is on screen, from the echoed value rather than from what the page asked for |
| `limit` | 2 | the cap line under the table, beside `counts.episodes_total` |
| `counts.episodes_total` | 2 | counts line |
| `counts.transcribed` | 2 | counts line, and the default filter's denominator |
| `counts.analysed` | 2 | counts line |
| `episodes[].item_id` | 2 | the row key, and what level 3 is opened with |
| `episodes[].title` | 2 | column `TITLE`, verbatim, one line, ellipsised, full string in the title attribute |
| `episodes[].published_at` | 2 | column `PUBLISHED` (date) and column `AGE` (days) |
| `episodes[].source_url` | 2 | column `LINK`, an outbound anchor |
| `episodes[].source_kind` | 2 | column `WHERE` |
| `episodes[].transcription_state` | 2 | column `TEXT` |
| `episodes[].transcript_chars` | 2 | column `CHARS` |
| `episodes[].analysis_present` | 2 | column `READ` |
| `episodes[].analysis_model` | 2 | the `READ` cell's tag and title |
| `episodes[].claim_count` | 2 | column `CLAIMS` |
| `episodes[].gameweek` | 2 | column `GW` |
| `episodes[].gw_reason` | 2 | the `GW` cell title, and the expanded row beneath when the cell is clicked |

### 1.3 `episode_summary` (`episodes.py:1175-1222`)

Level 3 calls it once with `{item_id}`.

| payload key | level | where it renders |
|---|---|---|
| `creator` | 3 | back row, as the label of the level above |
| `as_of` | 3 | footer |
| `episode.*` (all twelve keys) | 3 | the header block, the same twelve fields the level 2 row carried, as a two-column fact grid. The two levels cannot disagree because it is the same object |
| `summary_bullets[]` | 3 | the summary block, one list item each |
| `summary` | 3 | not rendered; it is `summary_bullets` joined, and rendering both prints every sentence twice |
| `summary_reason` | 3 | replaces the summary block when there is no summary |
| `players[].display_name` | 3 | the group row's name |
| `players[].disambiguator` | 3 | the group row's name when non-null, per the naming contract in `schema.py:30-41` |
| `players[].name` | 3 | the group row title attribute, as the spoken string |
| `players[].resolved` | 3 | when false the group row carries the word `unresolved`, and the row is still shown |
| `players[].code` | 3 | the group row's data key. Not printed |
| `players[].team`, `.position` | 3 | the group row's second and third cells |
| `players[].claims[].kind` | 3 | claim row column `CHANNEL` |
| `players[].claims[].stored_in` | 3 | claim row column `STORED IN` |
| `players[].claims[].direction` | 3 | claim row column `SAYS` |
| `players[].claims[].confidence` | 3 | claim row column `CONF` |
| `players[].claims[].conviction` | 3 | claim row column `CONF`, the word when the number is null |
| `players[].claims[].quote` | 3 | claim row column `QUOTE`, the expandable cell |
| `players[].claims[].start_s` | 3 | claim row column `AT`, formatted by `clock()` |
| `players[].claims[].deep_link` | 3 | the `AT` cell's anchor |
| `players[].claims[].gameweek` | 3 | claim row column `GW` |
| `transfers_suggested[]` (all fourteen keys) | 3 | the second table, one row per call, columns `DIR`, `PLAYER`, `STANCE`, `CONV`, `GW`, `AT`, `QUOTE` |
| `transfers_suggested[].paired_with` | 3 | never a cell. It is always null by contract, and a column of nulls is a column. Its sentence is printed once, under the table, from `gaps` |
| `captain_view[]` (all eleven keys) | 3 | the third table |
| `gameweek`, `gw_reason` | 3 | the header block |
| `analysis_model`, `analysed_at` | 3 | the footer |
| `gaps[].section`, `gaps[].gap` | 3 | one row per gap, placed under the section it names |

### 1.4 `creator_report_card` (`report_card.py:423-437`)

Not called on arrival. It is the definition of the level 1 `HIT` column and
opens from that column header, or from any `HIT` cell.

| payload key | where |
|---|---|
| `min_scored_claims`, `min_gw_measured` | the drawer's first line. Live: 25 and 10 |
| `baseline.kind`, `.label`, `.reason` | the drawer's second line |
| `note` | the drawer's third line, verbatim |
| `cards[].creator` | one section head per card. Live: 31 cards |
| `cards[].headline` | the section's one sentence, verbatim |
| `cards[].quotable` | whether the section's numbers are drawn faint. Live: 11 of 31 |
| `cards[].claims.*` (18 keys) | the claims table, one row per key that is non-null |
| `cards[].claims.by_gw[]`, `.by_action[]` | two nested tables, rows not bars |
| `cards[].numeric.*` (13 keys) | the numeric table. Live: measured false on every card |
| `cards[].team.people[].*` (13 keys) | the team table, one row per person. Live: 50 people across 31 cards |
| `cards[].team.baseline`, `.min_gw_measured`, `.reason` | the team table's caption |
| `cards[].people[]` | the section head's second line |
| `gaps[].key`, `.what`, `.fix` | the drawer's last block. Live: 2 gaps |
| `as_of`, `season` | the drawer footer |

### 1.5 `creator_detail` (`schema.py:465-487`)

Almost entirely dropped, because `creator_episodes` answers the same question
in a payload two orders of magnitude smaller. What survives is the one thing
`creator_episodes` does not carry: the creator's own FPL team.

| payload key | where |
|---|---|
| `squad[]`, `squad_reason`, `squad_gw`, `squad_gws` | level 2, inside a fold titled from `squad_gw`, called with `{creator, days: 1, limit: 1}` |
| `transfers[]`, `transfers_reason` | the same fold |
| `entry`, `entry_reason`, `record` | not read from here; level 2 already has them from `creator_episodes` |
| `items[]`, `items[].analysis`, `items[].claims[]` | **dropped**. This array is why the payload is 540 KB, and `creator_episodes` plus `episode_summary` serve the same rows one level at a time |
| `as_of`, `window_days`, `creator` | the fold's own provenance line |

### 1.6 Everything dropped, with the reason

**`creator_board.consensus[]`**, 5 rows live, and the `_CONSENSUS` object it
carries (`buy`, `sell`, `captain`, `net`, `mine`, `panel_owned`, `own_pct`,
`price`, `pos`, `team`). Dropped because it is a player-first question inside
a creator-first hierarchy, and every click into it goes sideways. The five
players it names are reachable through the player drawer that `xpoints.js`,
`template.js` and `components/playerdrawer.js` already mount. This is the
largest deliberate loss in the design and it is listed again in section 8.

**`creator_board.coverage.by_source[]`.** The obvious level 1 column is a
per-creator queued count, summed over that creator's `sources[].key`. It is
refused. `board.py:337-346` ends the query with `LIMIT 8`, so the array holds
the eight busiest source keys and no others. Live, those eight account for 245
of the 345 unread items across 41 source keys; the other 100 unread items sit
under 33 keys that never appear. Summing the array per creator would print
`0 queued` for 21 of 29 shows, which reads as "fully read" and is not what the
payload says. The board-wide sentence in `coverage.note` is printed instead,
and the per-creator version arrives at level 2 where
`creator_episodes.counts` measures it exactly, over the whole corpus, with no
cap.

**`creator_board.panel_squads`** (`panel_size` 54, `with_entry` 43, `known`
43, `no_entry_people` 11 names). It exists to explain the said-vs-owned
matrix, and the matrix is deleted. Nothing else on any level reads a panel
member's squad.

**`player_chatter`** entirely, all of `CHATTER_RESULT`: `owned`, `said`,
`said_by_gw`, `noticed`, `counts`. It answers "what has everyone said about
this player", which is the player axis, not the creator axis. Dropping it from
this tab costs nothing globally: `components/chatter.js` is mounted by
`views/xpoints.js`, `views/template.js` and `components/playerdrawer.js`, so
the question still has three entry points. The `import()` at
`creators.js:3805` goes.

**Not printed anywhere, and named rather than invented.**

- Episodes in the current gameweek, per creator, at level 1. No key in
  `BOARD_RESULT` carries it. `n_items_window` is a 30-day count and the column
  header says `ITEMS 30D` using the served `window_days`. The per-gameweek
  count exists at level 2, where every `episodes[].gameweek` is served, and
  the level 2 counts line prints it from the rows in hand.
- Transcribed share, per creator, at level 1. `counts.transcribed` is a
  `creator_episodes` field and fetching it for 29 creators is 29 calls, which
  is the defect this design exists to remove.
- Why a transcription failed. `transcription_state: failed` is served;
  the `content_transcribe_skip.reason` behind it is not. The cell says
  `failed` and stops. See the additive contract change in 4.4.
- Which podcast row and which YouTube row are the same recording. See 3.4.

### 1.7 The `LATEST LINE` ladder, stated once

Live, 4 of 29 creators have a `take` and 25 do not, so this cell is a ladder
and the cell says which rung it is on with a leading tag.

1. `take.summary_bullets[0]`, tagged with `take.model`. 4 rows live.
2. `latest.title`, tagged with `latest.kind` and `latest.text_source`. 21
   rows live.
3. `take_reason`, drawn in `--faint`. Rows where `latest` exists but no
   analysis has run land here only when `latest` is also null.
4. `latest_reason`, when `latest` is null. 4 rows live, the creators with
   `n_items: 0`.

---

## 2. Wireframes

Geometry. At 1280, `.frame` gives the rail 168 px, `.content` pads 22 px each
side, `.card` pads 16 px each side, so the table is **1,036 px** wide. At 390
the mobile rule at `app.css:330-339` drops `.content` padding to 12 px and the
rail goes horizontal, so the table box is **334 px** and the table itself
scrolls inside `.scroll-x` with `table.sticky-first` holding the first column.

Values in the level 2 and level 3 frames are shaped from the RESULT schemas
and the brief's live sample, because neither panel is registered on the
running server. Level 1 values are read off the live `creator_board` payload
captured at `2026-09-18T10:58:55Z`.

### 2.1 Level 1, 1280 px

```
┌─ CREATORS ─────────────────────────────────────────────────────────────────────────────────┐
│ Board scope: panel, 29 shows. 4 sources outside it, not read here ▸                        │ scope.*
│ 308 of 653 items published in the last 30 days have been read. 345 are fetched and still   │ coverage.note
│ queued: the analysis budget caps how many are read per night, so a take can be older than  │
│ the show it came from. Newest unread: 6 days old.                                          │ coverage.newest_unread
│ No creator has beaten a coin flip yet. 21 of 22 measured creators have at least one scored │ record_note
│ claim and none has a 95% Wilson lower bound above 0.5 over 25+ claims.                     │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ GAMEWEEK  [1][2][3][4][ 5 ][6][7]      SCOPE [panel][all]    SHOW [all][measured][spoke 7d] │ toolbar
│ GW5 is the next gameweek: it is the first whose deadline has not passed at this instant.    │ gw_reason
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ SHOW                  FEEDS    LATEST▲ ITEMS CLAIMS ARCH  HIT    N   TEAM      LATEST LINE  │ <- th, 26 px
│                                   (d)   30D    30D                                          │
│ Fantasy Football Scout blog+pod+yt   5   254    297   306  50%  204  (3 people) Scout Notes… │ <- tr, 29.1 px
│ Let's Talk FPL         pod+yt        6    39    495    73  46%  390  Andy LTFPL  FPL GW4 Fi… │
│ FPL Harry              pod+yt        6    38    387    66  47%  333  FPL Harry   GW4 Team R… │
│ Fantasy Football Hub   pod+yt        6    36    368    58  38%  345  (4 people)  Hub Podcas… │
│ FPL Raptor             pod+yt        6    34    333    59  50%  299  Raptor      Raptor GW4… │
│ AllAboutFPL            blog          6    33    251    43  40%  320  (2 people)  GW4 Prices… │
│ The FPL Wire           pod           6    23    141    42  40%  118  (4 people)  Wire GW4 P… │
│ FPL Fran               yt            6    12     68    12  41%   68  Fran        GW4 Wildca… │
│ FPL BlackBox           pod+yt        6    21     14    21  39%   28  (3 people)  BlackBox G… │
│ Planet FPL             pod+yt        6    30      8    62  25%    8  (2 people)  Planet FPL… │
│ The 59th Minute        pod           6     7      0    11    -    -  (2 people)  59th Minut… │
│ Ignore the Template    pod           6     6      0    18    -    -  no entry     Ignore th… │
│ Gianni Buttice         pod           7    22     11    45  22%   18  Gianni       GW4 Diffe… │
│ Above Average FPL      pod+yt        7     9      2    19   0%    1  (2 people)  Above Aver… │
│ FPL Pod                pod           7     4      2     7 100%    2  (2 people)  FPL Pod GW… │
│ FPL Focal              pod+yt        7    28      0    59    -    -  (2 people)  Focal GW4 … │
│ FPL Mate               yt            7    16      0    21    -    -  FPL Mate    Mate GW4 W… │
│ Solio Analytics        link+yt       8     4     35     4  50%   22  no entry     Solio mod… │
│ All In Football FPL    pod           9     7      5    12  17%    6  (2 people)  All In GW4… │
│ Sky Sports FPL         pod           9     1      4     1 100%    2  (3 people) ✦Sky GW4: c… │
│ FPL Tom                yt            9    17      3    23  75%    4  FPL Tom     Tom GW4 Tr… │
│ … 4 rows elided for width …                                                                │
│ FML FPL                pod          10     3      0    12   0%    2  (2 people) ✦FML GW4: t… │
│ FPL Family             pod+yt       11     6      8    11    -    0  (2 people) ✦Family GW4… │
│ FPL General            yt           56     0      0     1    -    -  FPL General✦General on… │
│ Always Cheating        pod       never     0      0     0    -    -  (2 people)  no items s… │
│ FPL JUiCE              pod       never     0      0     0    -    -  no entry     no items … │
│ FPL Review             blog      never     0      0     0    -    -  no entry     no items … │
│ The Athletic FPL       pod       never     0      0     0    -    -  (2 people)  no items s… │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│ ▸ feeds and fetch state (41 sources, 40 fetchable, last probe 6d)                          │ sourceTable
│ HIT is the share of this creator's scored claims that hit. 25 scored claims is the floor    │ header link
│ for a rank; below it the number is drawn faint. ▸ how this is scored                        │ opens 1.4
│ creator_board · 5084317 · read 1m ago                                                       │ provenance
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

`✦` marks a row whose `LATEST LINE` is on rung 1, a model-written bullet, and
carries `take.model` in its title attribute. Every other row is on rung 2, 3
or 4.

**Height, at 1280x900.**

| band | px | running |
|---|---|---|
| app topbar and content padding | 53 | 53 |
| card padding top | 14 | 67 |
| `h2` CREATORS | 21 | 88 |
| header sentences, 7 lines at 12px/1.6 plus margins | 152 | 240 |
| toolbar plus `gw_reason` | 46 | 286 |
| table header | 26 | 312 |
| 29 rows at 29.1 | 844 | 1,156 |
| feeds fold summary, HIT note, provenance | 69 | 1,225 |
| card padding bottom and margin | 30 | 1,255 |

**1,255 px**, against 2,041 px folded and 8,306 px open today. The first
**20 of 29 rows** sit above the fold at 900 px tall. One scroll of 355 px
reaches the last row, and there is nothing below it except the feeds fold.

### 2.2 Level 2, 1280 px

A drawer at `--cx-drawer-wide`, 920 px, right side, `Escape` closes, the same
`aside.drawer` node the tab already creates once per visit at
`creators.js:226-229`. The table box inside is 884 px.

```
                          ┌─ DRAWER 920px ───────────────────────────────────────────────────┐
                          │ Let's Talk FPL                                          [close]  │ creator
                          │ Andy LTFPL, entry 41, verified                                   │ entry.*
                          │ 179 of 390 scored claims hit, 46%, 95% CI 0.4102-0.5086. Under    │ record.*
                          │ the 25-claim floor this would not be a rank; it is over it.       │
                          │ 73 publications · 35 with a transcript · 67 with an analysis      │ counts
                          ├─────────────────────────────────────────────────────────────────┤
                          │ SHOW  [transcribed 35][queued][failed][none][all 73]             │ client-side
                          │ SORT  published ▼                                                │
                          ├─────────────────────────────────────────────────────────────────┤
                          │ PUBLISHED▼  AGE TITLE                    WHERE  TEXT   CHARS READ│
                          │                (d)                                          CLM │
                          │ 2026-09-11    6  FPL GW4 Final Thought…  pod    none      -   no │
                          │                                                            1    │
                          │ 2026-09-11    6  FPL GW4 Final Thought…  yt     none      -   no │
                          │                                                            3    │
                          │ 2026-09-10    7  My FPL Team for Gamew…  pod    none      -   no │
                          │                                                            0    │
                          │ 2026-09-10    7  My FPL Team for GW4 …   yt     none      -   no │
                          │                                                            0    │
                          │ 2026-09-08   10  FPL GW4 Transfer Tip…   pod    transcr  62k yes │
                          │                                                           22    │
                          │ 2026-09-08   10  FPL GW4 Transfer Tip…   yt     transcr  62k yes │
                          │                                                           22    │
                          │ 2026-09-06   12  My FPL Wildcard Team…   pod    transcr  71k yes │
                          │                                                           21    │
                          │ 2026-09-06   12  My FPL Wildcard Team…   yt     transcr  70k yes │
                          │                                                           23    │
                          │ 2026-09-05   13  FPL Gameweek 4 Early…   pod    queued    -   no │
                          │                                                            7    │
                          │ 2026-09-05   13  Transfer tonight Che…   yt     failed    -   no │
                          │                                                           12    │
                          │ … 63 more rows …                                                 │
                          ├─────────────────────────────────────────────────────────────────┤
                          │ 73 of 73 publications, under the panel's 200-row cap.            │ limit, counts
                          │ counts.episodes_total counts publications keyed on the stored    │
                          │ URL. One recording published to a podcast feed and to YouTube is │
                          │ two URLs, so it is two rows. Nothing stored links them.          │
                          │ ▸ their FPL team for GW4 (one more call)                         │ creator_detail
                          │ creator_episodes · read just now                                 │
                          └─────────────────────────────────────────────────────────────────┘
```

The `CLAIMS` count sits on a second line inside the last cell rather than in a
column of its own, so the title column keeps 300 px. The header prints it as
`READ / CLM`.

**Height** with the default `transcribed` filter, 35 rows: head 60, entry 19,
record 38, counts 19, filter rows 66, table header 40, 35 rows at 29.1 =
1,019, footer 92, drawer padding 32. **1,385 px** inside a 900 px drawer, so
the drawer scrolls and the page behind it does not move. With `all 73`:
**2,490 px**.

### 2.3 Level 3, 1280 px

The same drawer node, contents replaced, with a back row. One click deeper,
never a second drawer.

```
                          ┌─ DRAWER 920px ───────────────────────────────────────────────────┐
                          │ ← 73 episodes for Let's Talk FPL                         [close] │ creator
                          ├─────────────────────────────────────────────────────────────────┤
                          │ FPL GW4 Transfer Tips SELL Bruno, Palmer IN                      │ episode.title
                          │ published  2026-09-08 08:00     where     podcast                │ episode.*
                          │ text       transcribed, 62,104   read     yes, claude-opus-5     │
                          │ claims     22                    gameweek GW4                    │
                          │ link       pscrb.fm/rss/p/traffic.megaphone.fm/COMG12990…  ↗     │
                          │ GW4 is the gameweek this episode was published into: its         │ gw_reason
                          │ deadline is the first to fall after the publication date.        │
                          ├─ SUMMARY, 6 bullets, stored ────────────────────────────────────┤
                          │ • Chelsea's attack is the main target for GW4 with Hull at home, │ summary_bullets
                          │   and Andy argues the fixture run afterwards is good enough that │
                          │   Palmer, João Pedro and Morgan Rodgers all have easy jump-off   │
                          │   points later.                                                  │
                          │ • Liverpool's attack is a hold, not a buy: fine for Fulham at    │
                          │   home, but Bournemouth away and City at home straight after.    │
                          │ • … 4 more …                                                     │
                          ├─ PLAYERS, 20 ───────────────────────────────────────────────────┤
                          │ PLAYER          TM  POS  CLM  CHANNEL STORED IN    SAYS   CONF   │
                          │                                        GW  AT  QUOTE            │
                          │ ▾ Palmer        CHE MID    4                                     │ players[]
                          │    ·                       call  transfers_in  buy    high      │ claims[]
                          │                            4   -   "I'm instantly drawn to Col…│
                          │    ·                       claim llm:claude-…  buy    0.72      │
                          │                            4   -   "Palmer preferred over Rodg…│
                          │ ▾ Haaland       MCI FWD    3                                     │
                          │    ·                       call  captaincy     captain medium   │
                          │                            5   -   "Game week five, Haaland."   │
                          │    ·                       claim llm:claude-…  captain 0.60     │
                          │                            5   -   "Game week five, Haaland."   │
                          │ ▾ Isak          NEW FWD    2                                     │
                          │    ·                       claim llm:claude-…  avoid   0.60     │
                          │                            4   -   "personally I've got no int…│
                          │ ▾ B.Fernandes   MUN MID    1                                     │
                          │    ·                       claim cue           sell    0.53     │
                          │                            4   -   "FPL GW4 Transfer Tips SELL…│
                          │ … 16 more players …                                              │
                          ├─ TRANSFERS SUGGESTED, 19 ───────────────────────────────────────┤
                          │ DIR PLAYER       STANCE  CONV    GW  AT   QUOTE                  │
                          │ in  Palmer       buy     high     4   -   "I'm instantly drawn…│ transfers_suggested
                          │ in  João Pedro   buy     medium   4   -   "I do like Jao Pedro…│
                          │ out Mbeumo       sell    medium   4   -   "you're only willing…│
                          │ out Cherki       sell    low      4   -   "So Cherky to Rodger…│
                          │ … 15 more …                                                      │
                          │ transfers are stored as two independent lists, so which sale     │ gaps
                          │ funded which purchase is not recorded and is not inferred here.  │
                          ├─ CAPTAIN VIEW, 4 ───────────────────────────────────────────────┤
                          │ PLAYER    STANCE   CONV    GW  AT   QUOTE                        │
                          │ Haaland   captain  medium   5   -   "Game week five, Haaland."   │ captain_view
                          │ Haaland   captain  medium   7   -   "Game week seven is Haalan…│
                          │ … 2 more …                                                       │
                          ├─ GAPS ──────────────────────────────────────────────────────────┤
                          │ timestamps  no transcript segment is stored for this episode, so │ gaps[]
                          │             no quote can be located in time and every start_s    │
                          │             is null.                                             │
                          ├─────────────────────────────────────────────────────────────────┤
                          │ analysed 2026-09-09 by claude-opus-5 · episode_summary           │ analysed_at
                          └─────────────────────────────────────────────────────────────────┘
```

The `QUOTE` cell is one line with `-webkit-line-clamp: 1`. Clicking the cell
removes the clamp and the row grows; clicking again restores it. No modal, no
tooltip, no second drawer. The quote is written with `textContent`, never
`innerHTML`, because `episodes.py:69-72` states that titles, summaries and
quotes are untrusted third-party prose.

**Height**: back row 26, head 60, fact grid 132, gw_reason 38, summary 300,
players table 1,772, transfers 579 plus its gap line 40, captain 142, gaps 80,
footer 16, padding 32. **3,217 px**, inside the drawer's own scroll.

### 2.4 Level 1, 390 px

`.scroll-x` around the table, `table.sticky-first` pins `SHOW` with its
1 px right shadow, both already in `app.css:180-186`. No column is hidden,
because a hidden column is a column the reader cannot sort by, and sorting is
the whole interaction.

```
┌────────────────────────────────────────┐ 390
│ CREATORS                               │
│ Board scope: panel, 29 shows. 4        │
│ sources outside it, not read here ▸    │
│ 308 of 653 items published in the last │
│ 30 days have been read. 345 are        │
│ fetched and still queued: the analysis │
│ budget caps how many are read per      │
│ night, so a take can be older than the │
│ show it came from.                     │
│ No creator has beaten a coin flip yet. │
│ 21 of 22 measured creators have at     │
│ least one scored claim.                │
├────────────────────────────────────────┤
│ GW  [3][4][ 5 ][6][7]                  │
│ SCOPE [panel][all]                     │
│ SHOW  [all][measured][spoke 7d]        │
│ GW5 is the next gameweek: it is the    │
│ first whose deadline has not passed.   │
├────────────────────────────────────────┤
│ SHOW           │FEEDS   LATEST▲ ITEM…  │ ← sticky   scroll →
│                │            (d)  30D   │
│ Fantasy Footb…▕│blog+p…      5   254   │
│ Let's Talk FPL▕│pod+yt       6    39   │
│ FPL Harry     ▕│pod+yt       6    38   │
│ Fantasy Footb…▕│pod+yt       6    36   │
│ FPL Raptor    ▕│pod+yt       6    34   │
│ AllAboutFPL   ▕│blog         6    33   │
│ The FPL Wire  ▕│pod          6    23   │
│ FPL Fran      ▕│yt           6    12   │
│ FPL BlackBox  ▕│pod+yt       6    21   │
│ Planet FPL    ▕│pod+yt       6    30   │
│ … 19 more rows …                       │
├────────────────────────────────────────┤
│ ▸ feeds and fetch state (41 sources)   │
│ ▸ how HIT is scored                    │
│ creator_board · 5084317 · read 1m ago  │
└────────────────────────────────────────┘
```

**Height**: header 53, card padding 12, `h2` 21, header sentences 12 lines at
19.2 = 230, three toolbar rows plus reason 134, table header 40, 29 rows at
29.1 = 844, folds and provenance 76, padding 26. **1,436 px**, against 2,919
px folded and 13,738 px open today. The table scrolls sideways inside its own
box; `body` never exceeds 390 px wide, which the current page also achieves
(`bodyScrollW` measured at 390).

### 2.5 Level 3, 390 px

The drawer is `min(920px, 96vw)`, which resolves to 374 px, leaving 338 px of
content. The three tables lose their horizontal form and each row becomes two
lines: the keys on line one, the quote beneath, still in an expandable cell.
The header fact grid drops from two columns to one, the same way
`creators.css:242-249` already collapses `.cx-pv-facts` at 430 px.

```
┌──────────────────────────────────────┐ 374
│ ← 73 episodes                [close] │
├──────────────────────────────────────┤
│ FPL GW4 Transfer Tips SELL Bruno,    │
│ Palmer IN                            │
│ published   2026-09-08 08:00         │
│ where       podcast                  │
│ text        transcribed, 62,104      │
│ read        yes, claude-opus-5       │
│ claims      22                       │
│ gameweek    GW4                      │
│ link        pscrb.fm/rss/p/traff… ↗  │
│ GW4 is the gameweek this episode was │
│ published into: its deadline is the  │
│ first to fall after the publication  │
│ date.                                │
├─ SUMMARY, 6 bullets ─────────────────┤
│ • Chelsea's attack is the main       │
│   target for GW4 with Hull at home,  │
│   and Andy argues the fixture run    │
│   afterwards is good enough that     │
│   Palmer, João Pedro and Morgan      │
│   Rodgers all have easy jump-off     │
│   points later.                      │
│ • … 5 more …                         │
├─ PLAYERS, 20 ────────────────────────┤
│ SORT  claims ▼                       │
│ ▾ Palmer · CHE MID · 4 claims        │
│   call · transfers_in · buy · high   │
│   GW4 · at -                         │
│   "I'm instantly drawn to Cole Pal…  │
│   claim · llm:claude-opus-5 · buy    │
│   0.72 · GW4 · at -                  │
│   "Palmer preferred over Rodgers o…  │
│ ▾ Haaland · MCI FWD · 3 claims       │
│   call · captaincy · captain · med   │
│   GW5 · at -                         │
│   "Game week five, Haaland."         │
│ … 18 more players …                  │
├─ TRANSFERS SUGGESTED, 19 ────────────┤
│ in · Palmer · buy · high · GW4       │
│   "I'm instantly drawn to Cole Pal…  │
│ out · Mbeumo · sell · medium · GW4   │
│   "you're only willing to sell one…  │
│ … 17 more …                          │
│ transfers are stored as two          │
│ independent lists, so which sale     │
│ funded which purchase is not         │
│ recorded and is not inferred here.   │
├─ CAPTAIN VIEW, 4 ────────────────────┤
│ Haaland · captain · medium · GW5     │
│   "Game week five, Haaland."         │
│ … 3 more …                           │
├─ GAPS ───────────────────────────────┤
│ timestamps · no transcript segment   │
│ is stored for this episode, so no    │
│ quote can be located in time and     │
│ every start_s is null.               │
├──────────────────────────────────────┤
│ analysed 2026-09-09 by claude-opus-5 │
└──────────────────────────────────────┘
```

**Height** about **4,900 px**, entirely inside the drawer's own
`overflow-y: auto`. The 390 px tables keep their sort controls, which is why
the two-line row is a row and not a card: the `SORT` chip above the group
list is the same control as the column header at 1280.

---

## 3. Empty, stale, error and gap states

Ages are printed in **days**, everywhere, on every level. `relAge`
(`creators.js:40-46`) produces "3h 12m ago" through the shared `fmtAge`; this
tab stops calling it and calls a new `daysOld(iso)` that returns an integer,
`0` rendered as `today`, and `null` rendered as `never`. `clock()`
(`creators.js:48-55`) stays, because `start_s` is an offset inside an episode
and not an age.

### 3.1 Level 1

| condition | what renders |
|---|---|
| `creator_board` returns `{empty, reason}` | `emptyBox` with the reason verbatim, no table, no toolbar |
| `creator_board` throws | `errBox` with the message verbatim; the feeds fold still renders from `GET /api/content/sources`, which is a different call |
| `creators` is a non-empty array and every `last_item_at` is null | the table renders, the `LATEST` column is `never` in every row, and a line above it states the count from the rows in hand |
| one creator with `last_item_at: null` | `LATEST` cell `never`, `ITEMS 30D` and `CLAIMS 30D` `0`, `LATEST LINE` shows `latest_reason`. Sorted last under the default ascending sort, never first. Live: 4 of 29 |
| `record.scored` is null | `HIT` cell shows `-`, `N` cell shows `-`, the cell title carries `record.reason`. Never `0%`. Live: 7 of 29 |
| `record.earned` is false | `HIT` and `N` are drawn through `.cx-underfloor`, which `creators.css:872` already defines as faint italic. Live: 29 of 29 |
| `record.scored` is below `min_scored_claims` | the same faint treatment. The floor number is not on `BOARD_RESULT`, so the level 1 note says "25 scored claims is the floor" only after the report drawer has been opened once and `min_scored_claims` is in hand; before that the note says "the floor is stated in the scoring method" and links the fold |
| `entry` is null | `TEAM` cell shows `entry_reason`, ellipsised, full text in the title |
| `entry.entry_id` is null but `entry.people` is non-empty | `TEAM` cell shows `(n people)` and the names in the title, because `schema.py:140-144` says the flat fields are populated only for a single verified person and a show's team does not exist otherwise |
| `coverage` is null | the coverage sentence is absent, and nothing is substituted for it |
| `mine_reason` non-null | a fourth header sentence, verbatim. Live: null |
| `scope.excluded` non-empty | the fold under the scope line, one row per name. Live: 4 |
| a gameweek chip is pressed | `creator_board` is re-called with `{gw}`, the table is replaced, the chip shows a pending state. No chip carries a count badge, because a count badge costs one call per gameweek |

### 3.2 Level 2: the transcription states

The four values and what each cell shows. The sentences are the panel's own,
from the `transcription_state` description in `episodes.py:924-932`.

| `transcription_state` | `TEXT` cell | `CHARS` | row visible by default |
|---|---|---|---|
| `transcribed` | `transcr` | `transcript_chars` with a thousands separator | yes |
| `queued` | `queued` | `-` | no, the row is in hand and the default filter hides it |
| `failed` | `failed` | `-` | no |
| `none` | `none` | `-` | no |

The cell title for `queued` is the schema's sentence: the audio URL is stored
and the transcription step has not reached it. For `failed` the cell title is
the schema's sentence and no more, because the `content_transcribe_skip.reason`
that would say `no_captions` or `relevance:2` is not a served field. A reader
who needs it gets the additive field in 4.4 or reads the table.

`analysis_present: false` with `transcription_state: transcribed` is the
ordinary backlog state and the `READ` cell says `no`. `analysis_present: true`
with `transcription_state: none` is also real, because an analysis can be
written from a description; the two columns are independent and the header
does not imply one is a subset of the other. The brief's live sample for
`Let's Talk FPL` has `transcribed: 35` and `analysed: 67` out of 73, which is
exactly that case, 32 times over.

### 3.3 Level 2: other states

| condition | what renders |
|---|---|
| `creator_episodes` returns `{empty, reason}` | the drawer shows the reason verbatim and keeps its head. `episodes.py:544-548` returns the tracked-creator list inside that reason, so the reader learns which names exist |
| `episodes` is empty but `counts.episodes_total` is greater than zero | the table is replaced by one line: `counts.transcribed` of `counts.episodes_total` have a transcript, and the current filter matches none of them, with the chip that clears it |
| `counts.episodes_total` exceeds `limit` | the cap line reads "200 of 306 publications, capped by the panel's 200-row limit". Live, `Fantasy Football Scout` has `n_items: 306`, so this is not hypothetical |
| `source_url` is null | the `LINK` cell is empty and the title says no URL is stored for this item, which is what the schema's null means |
| `published_at` is null | `PUBLISHED` shows `-`, `AGE` shows `-`, and the row sorts last under the default |
| `gameweek` is null | `GW` shows `-` and the cell title carries `gw_reason`, which in that case is the panel's sentence naming the link ledger, `dim_event` and the claim stamps |
| the creator has an entry but no squad | the "their FPL team" fold, when opened, prints `squad_reason` and nothing else |
| `creator_detail` for the fold throws | the fold shows the error and stays open; the episode table above it is untouched |

### 3.4 The duplicate podcast and YouTube pair

`canonical_key` (`urls.py:58-67`) returns `yt:<id>` for a YouTube URL and
`url:<url>` otherwise, so a recording published to a podcast feed and to
YouTube has two keys and `creator_episodes` serves two rows. The design shows
two rows and does not join them.

Measured, on the live `creator_detail` for `Let's Talk FPL`, 60-day window:
40 stored rows, 23 canonical groups, and 17 of those 23 are a podcast row and
a YouTube row of one recording. The publication gap between the two is 0.3,
0.6, 0.6, 1.4, 2.2, 2.5, 4.5, 4.9, 6.9, 8.1, 10.8, 11.2, 12.0, 14.8, 17.1 and
18.5 minutes, median 6.9. The brief's 12 minutes sits inside that spread.

Joining them on title plus a time window is refused, and the reason is in the
same measurement: one title-matched pair in that corpus is **1,492 minutes
apart**, 24.9 hours, which is two different publications that share a title.
A join rule that catches the 17 real pairs also catches that one, and the
result would be a row claiming a recording exists that does not.

What the page does instead:

- Both rows render, with `WHERE` reading `pod` and `yt`.
- Under the default sort, `published_at` descending, they land adjacent, so
  the duplication is visible without being asserted.
- The claim counts differ, measurably: the GW4 transfer-tips pair is 22 and
  22, the wildcard pair is 21 and 23. Merging would have to pick one, and
  picking one throws away claims that were extracted from the other.
- The footnote under the table states the rule in one sentence, from the
  payload's own vocabulary: `counts.episodes_total` counts publications keyed
  on the stored URL.

### 3.5 Level 3

| condition | what renders |
|---|---|
| `episode_summary` returns `{empty, reason}` | the drawer shows the reason and the back row stays, so one click returns to level 2 |
| `analysis_present: false` | `gaps` carries exactly one entry, `section: analysis`, and the panel returns early. Level 3 renders the header block, the gap sentence, and nothing else. No empty tables, no zero counts |
| `summary_bullets` is empty | the summary block is replaced by `summary_reason`, which the panel writes as "the stored analysis carries an empty summary list" |
| `players` is empty | the players table is replaced by its gap row |
| `transfers_suggested` is empty | the transfers table is replaced by its gap row |
| `transfers_suggested` is non-empty | the `paired_with` gap row is printed under the table. The panel always emits it in this case, per `episodes.py:768-771` |
| `captain_view` is empty | the captain table is replaced by its gap row |
| no transcript stored | the `timestamps` gap row, and every `AT` cell is `-`. Measured on the live GW4 transfer-tips analysis: all 22 claims and all 23 calls have `start_s: null`, including the ones on an item whose `text_source` is `transcript` |
| `start_s` is null but `deep_link` is non-null | the `AT` cell is `-` and the link rides on the player group row instead, because a link labelled with a time that does not exist is a link that lands somewhere it was not asked to |
| `resolved: false` | the group row prints `players[].name`, the spoken string, and the word `unresolved`. The row is not dropped and no code is guessed |
| `disambiguator` non-null | the group row prints it instead of `display_name`, per `schema.py:34-36`. Live, `Palmer` carries `C. Palmer (CHE)` |
| `confidence` null and `conviction` null | `CONF` shows `-` |
| `quote` null | the cell prints the reason already in `creators.js:3868-3870`, that a keyword window has no sentence to show, and the cell does not expand |

### 3.6 Staleness, on every level

There is no green, amber and red dot. The `LATEST` column is the staleness
display, in days, sortable, and a reader sorts on it to find the stale rows.
`freshdot` and the `relAge` class ladder at `creators.js:40-46` go with it.
`as_of` is printed once per level in the provenance line, in days where the
number is an age and as a stamp where it is an instant.

---

## 4. What is deleted from `creators.js`

3,880 lines today. Line ranges are inclusive and read against the file at sha
`5084317`.

### 4.1 Deleted outright

| lines | n | what | why |
|---|---|---|---|
| 27-34 | 8 | `NS`, `sv()` | no SVG on any level |
| 40-46 | 7 | `relAge` | ages are days, from `daysOld` |
| 96-106 | 11 | `LANES`, `laneLabel` | the lane vocabulary exists for the matrix |
| 288-318 | 31 | `claimsIdx`, `loadRecency`, `freshFor`, `agoText`, `H48` | this is the 29 `creator_detail` calls and 3,193 KB. The fact it computes, claim recency per player, is a consensus-row fact and consensus is gone |
| 331-333 | 3 | `squadByCreator`, `squadsAsked`, `squadsPending` | the matrix's own channel |
| 337-339 | 3 | `gwCount`, `gwProbing`, `BACK`, `FWD` | the probe's state |
| 348 | 1 | the `squad_overview` call | the ledger has no squad lane |
| 367-383 | 17 | `sq`, `laneOf`, `mine`, `squadReady` | the same |
| 384-447 | 64 | `grp`, `buildRows`, `byWeightOfMouth`, `inWindow`, `wantH48`, `windowed`, `currentRows` | builds consensus rows and the 48-hour window |
| 460-492 | 33 | `gwCandidates`, `probeGameweeks` | six `creator_board` calls and 566 KB, spent on a count badge |
| 1977-2017 | 41 | `renderBoard` | replaced by `renderLedger` |
| 2018-2183 | 166 | `drawBoard` | the SVG deadline board |
| 2184-2213 | 30 | `boardLegend` | its legend |
| 2214-2286 | 73 | `renderMainTakes` | cards |
| 2287-2358 | 72 | `decisionCard` | cards |
| 2359-2367 | 9 | `countChip` | card furniture |
| 2368-2380 | 13 | `crossLink` | card furniture |
| 2381-2425 | 45 | `rcChip`, `rcTag` | the report card stops travelling as a per-row chip |
| 2426-2478 | 53 | `recordStrip` | replaced by the `HIT` and `N` columns |
| 2479-2522 | 44 | `renderArmband` | bars |
| 2523-2553 | 31 | `renderWatching` | cards |
| 2554-2590 | 37 | `renderMatrix`, `redrawGrid` | the matrix shell |
| 2696-2729 | 34 | `rangeBar`, `gwBars` | bars inside the report card |
| 2918-3294 | 377 | `renderGrid` | the matrix itself, 3,997 px of page |
| 3312-3346 | 35 | `loadSquads` | panel squads, matrix only |
| 3347-3359 | 13 | `panelSquadWord` | the same |
| 3508-3512 | 5 | `heldOnBoard` | board only |
| 3513-3738 | 226 | `openPerson` | a panel member's own squad, which is one click sideways from a creator row |
| 3745-3772 | 28 | `openPlayerByCode` | the player axis |
| 3773-3880 | 108 | `openPlayer` | the player axis, including the `chatter.js` import at 3805 |

**Deleted outright: 1,620 lines.**

### 4.2 Moved out of the file

| lines | n | what | where |
|---|---|---|---|
| 1136-1967 | 832 | the link bar: `LINK_STAGES`, `renderLinkBar`, `failLine`, `stopJob`, `startJob`, `poll`, `ERR_STATE`, `errState`, `applyPoll`, `classifyError`, `expiryLeft`, `expiryText`, `startExpiryTicker`, `confirmExpiry`, `decide`, `abortJob`, `repaste`, `renderJobs`, `renderJob`, `againRow`, `renderPreview`, `pathLabel`, `secs`, `fmtWhen`, `renderLedger`, `TAKE_BUCKETS`, `renderTake`, `jobStateLabel` | a new module `web/dist/js/views/creators_link.js`, mounted from `creators.js` in 6 lines |

This is a job-polling state machine for pasting a URL. It shares nothing with
the three levels except the card it sits in. Moving it removes **826 net
lines** from `creators.js` and leaves the capability untouched. The repo gains
a file of about 840 lines, so the honest repo-level saving from this row is
zero; the saving is that the creators view stops being a 3,880-line file with
two unrelated programs in it.

Note the name collision: the existing `renderLedger` at `creators.js:1866`
draws a paste job's storage ledger. The new level 1 table takes the name
`renderLedger`, so the moved function is renamed `renderStorageLedger` in its
new module.

### 4.3 Trimmed

| lines | now | after | what |
|---|---|---|---|
| 516-557 | 42 | 18 | `renderGwRow`, without the count badges the probe fed it |
| 558-629 | 72 | 20 | `renderFactRow`, `toggle`: the window fact row becomes the header sentence block |
| 746-816 | 71 | 25 | `cardCensus`, `censusLine`, `honestyLine`, `recordSentence`: one sentence from `record_note`, one from `coverage.note` |
| 822-902 | 81 | 40 | `sourceStrip` becomes the summary line of the feeds fold |
| 2591-2656 | 66 | 20 | `renderReportCards` becomes the drawer opener |
| 2657-2695 | 39 | 25 | `reportCard` loses its chips and bars |
| 2786-2917 | 132 | 90 | `reportBody` keeps its three tables, loses `rangeBar` and `gwBars` |
| 3360-3402 | 43 | 28 | `quoteBlock` becomes the expandable cell |
| 3421-3434 | 14 | 0 | `openCreator` is replaced by `openEpisodes` |

**Trimmed away: 274 lines.**

### 4.4 Added

| what | lines |
|---|---|
| `daysOld(iso)` | 8 |
| `renderLedger`, the level 1 table | 130 |
| sort state, comparator, header click handling, shared by all three levels | 40 |
| the level 1 filter row | 45 |
| `openEpisodes`, level 2 | 110 |
| `openEpisode`, level 3 | 180 |
| the expandable quote cell | 20 |
| the header sentence block | 30 |
| the "their FPL team" fold, calling `creator_detail` with `{days: 1, limit: 1}` | 35 |
| **total added** | **598** |

### 4.5 The arithmetic

```
3,880  today
-1,620  deleted outright
  -826  moved to creators_link.js
  -274  trimmed
  +598  added
-------
 1,758  target
```

**Target: 1,758 lines, plus or minus 80.** A 55% reduction in the file, and a
33% reduction across the two files together. The `+/- 80` is honest: the
trimmed rows are estimates from reading each function, not from writing the
replacement.

### 4.6 `creators.css`, 1,074 lines today

Deleted with their section comments: the deadline board 312-395 (84), the
decisions 396-493 (98), the armband 494-514 (21), watch calls 515-529 (15),
the said-owned grid 592-666 (75), and the orphaned roster rules 562-579 (18),
whose `table.cx-roster` and `.cx-latest` selectors match nothing in
`creators.js` today. **311 lines deleted.** About 90 lines are added for the
ledger's four rules in section 5. The link bar's own block, 73-249, moves to a
new `creators_link.css` or stays where it is; either is fine and neither
changes the tab.

**Target: `creators.css` 853 lines.**

### 4.7 Two additive payload changes, neither required to ship

Both are additive, both keep `additionalProperties: False` honest by
declaring the new key, and the page renders correctly without either.

1. `_EPISODE.transcription_reason`, `{"type": ["string", "null"]}`: the
   `content_transcribe_skip.reason` behind `transcription_state: failed`, the
   column the module docstring already names (`no_captions`, `ConnectError`,
   `relevance:2`), null for every other state. Without it the `TEXT` cell says
   `failed` and the reader goes to the warehouse.
2. `_COUNTS.by_gameweek`, an array of `{gw, n}`: how many of this creator's
   publications fall in each gameweek, counted over the same corpus as
   `episodes_total` rather than over the returned page. The level 2 counts
   line currently derives it from the rows in hand, which is correct only
   while `episodes_total` is under `limit`, and `Fantasy Football Scout` is
   not.

---

## 5. Type scale and spacing

### 5.1 What exists, with values, from `app.css`

| token or rule | value | line |
|---|---|---|
| `body` font | `13px/1.45` system stack, `font-variant-numeric: tabular-nums` globally | 57-66 |
| `table.data` | `font-size: 12.5px`, `border-collapse: collapse`, `width: 100%` | 136 |
| `table.data th, td` | `padding: 5px 10px`, `white-space: nowrap`, 1px bottom rule | 137-138 |
| `table.data th` | `11px`, weight 600, `letter-spacing: .05em`, uppercase, `position: sticky; top: 0` | 139-141 |
| `table.data th.sorted` | colour `--ink` | 142 |
| `table.data td.num` | right aligned, `var(--mono)`, tabular | 143-144 |
| `table.data tr:hover td` | `background: var(--raised)` | 145 |
| `table.sticky-first` | first column sticky at `left: 0`, `min-width: 170px`, 1px right shadow | 180-184 |
| `.scroll-x` | `overflow-x: auto` | 135 |
| measured row heights | `th` 26.4 px, `tr` 29.1 px | measured |
| `.card` | `padding: 14px 16px`, `border-radius: 10px`, `margin-bottom: 16px` | 116-118 |
| `.card > h2` | `13px`, uppercase, `letter-spacing: .06em`, `--muted` | 119-120 |
| `.card > .sub` | `12px`, `--faint` | 121 |
| `.toolbar` | `gap: 8px`, `padding: 8px 0`, 1px top rule between siblings | 236-238 |
| `.tlabel` | `10.5px`, `letter-spacing: .09em`, uppercase, `min-width: 76px`, weight 650 | 239-240 |
| `.chip` | `11px`, weight 600, `padding: 2px 9px`, `border-radius: 999px` | 152-155 |
| `.chip.gw`, `.chip.src` | `12px`, `padding: 5px 12px` | 248-249 |
| `.drawer` | `width: min(460px, 92vw)`, `padding: 16px 18px`, `overflow-y: auto`, `z-index: 40` | 287-291 |
| `.cx-drawer` | overrides width to `min(560px, 96vw)` | `creators.css:669` |
| `.provenance` | `11px`, `var(--mono)`, `--faint` | 124 |
| `.empty`, `.err` | dashed and tinted boxes, `padding: 14px` and `10px 12px` | 126-132 |
| colour | `--ink --muted --faint --line --raised --surface --s1 --good --warn --bad --accent --mono` | 13-26 |
| mobile | under 820px, rail goes horizontal, `.content` padding drops to `12px 12px 32px` | 330-339 |

The ledger uses that vocabulary and adds no font size. Every number is in a
`td.num`, which already means mono and tabular, so the `LATEST`, `ITEMS`,
`CLAIMS`, `ARCH`, `HIT` and `N` columns line up by the rules that exist.

### 5.2 What does not exist

`app.css` has **no spacing token**. A search for `--sp`, `--space` and
`--gap` returns zero. `creators.css` alone carries **322 margin, padding and
gap declarations across 18 distinct pixel values**: 8px used 63 times, 6px 53,
10px 28, 2px 27, 4px 22, 3px 19, 7px 18, 9px 17, 1px 15, 5px 14, 12px 14,
14px 13, 11px 10, 13px 3, 20px 2, 16px 2, 22px 1, 18px 1.

### 5.3 New tokens, with values

Declared on `:root` in `app.css` beside the colour tokens, because two of
them are the app's scale and not this tab's.

```css
--sp-1: 4px;    /* inside a cell: the gap between a value and its tag */
--sp-2: 8px;    /* between controls in a toolbar row */
--sp-3: 14px;   /* between a table and the sentence above it */
--sp-4: 22px;   /* between two sections inside one card */
--row-h: 29px;  /* one table.data row, measured at 29.1 */
```

Declared on `.cx-*` in `creators.css`, because they are this tab's:

```css
--cx-drawer-wide: min(920px, 96vw);  /* levels 2 and 3 */
--cx-col-num: 64px;    /* minimum numeric column: 5 mono digits at 12.5px plus 20px padding */
--cx-col-show: 190px;  /* the SHOW column; the longest live name is 22 characters */
--cx-col-line: 300px;  /* the LATEST LINE and TITLE columns, ellipsised */
--cx-quote-lines: 1;   /* -webkit-line-clamp on an unexpanded quote cell */
```

The five spacing steps are the only spacing the ledger uses. The 18 values in
`creators.css` today fall out with the sections that carry them.

### 5.4 The four new rules

```css
.cx-led td.cx-line      { max-width: var(--cx-col-line); white-space: nowrap;
                          overflow: hidden; text-overflow: ellipsis; }
.cx-led td.num          { min-width: var(--cx-col-num); }
.cx-led .cx-underfloor  { color: var(--faint); font-style: italic; }
.cx-q                   { display: -webkit-box; -webkit-line-clamp: var(--cx-quote-lines);
                          -webkit-box-orient: vertical; overflow: hidden; cursor: pointer; }
.cx-q.open              { -webkit-line-clamp: none; overflow: visible; }
```

`.cx-underfloor` already exists at `creators.css:872` and is reused rather
than redefined.

---

## 6. Sorting and filtering

Every sort is client-side over the array in hand. Two filters are panel
parameters and are named as such, because they change what the panel serves;
everything else is a predicate over rows already downloaded.

### 6.1 Level 1

| column | sorts | key | null handling |
|---|---|---|---|
| `SHOW` | yes, alphabetical | `creator` | none possible |
| `FEEDS` | no | `kinds` | |
| `LATEST` | yes, **default, ascending** | days from `last_item_at` | nulls last in both directions |
| `ITEMS 30D` | yes | `n_items_window` | integer, never null |
| `CLAIMS 30D` | yes | `n_claims_window` | integer, never null |
| `ARCH` | yes | `n_items` | integer, never null |
| `HIT` | yes | `record.hit_rate` | nulls last in both directions |
| `N` | yes | `record.scored` | nulls last in both directions |
| `TEAM` | no | `entry` | |
| `LATEST LINE` | no | the ladder | |

Default: `LATEST` ascending, tie-broken by `n_claims_window` descending. Live
that puts `Fantasy Football Scout` first at 5 days, then the six shows at 6
days ordered 495, 387, 368, 333, 251, 141 claims, and the four shows with no
items last.

Nulls last in both directions is deliberate. A creator with no items is not
the freshest creator and is not the stalest either; it is a creator with no
measurement, and the sort keeps it out of both ends.

`HIT` sorts, and the column is drawn faint for every row where
`record.earned` is false, which live is every row. A faint column still
sorts: the reader is allowed to rank an unranked number as long as the page
never calls the result a rank.

Filters:

| filter | side | effect |
|---|---|---|
| gameweek chips | **panel param** `gw` | re-calls `creator_board`. The panel windows claims by gameweek, so the browser cannot do it |
| scope `panel` / `all` | **panel param** `scope` | re-calls `creator_board`. `scope.applied` is echoed and the header reads it back |
| `all` / `measured` / `spoke 7d` | client | `measured` keeps `record.scored != null`, 22 of 29 live. `spoke 7d` keeps `daysOld(last_item_at) <= 7`, 17 of 29 live |
| text box over `SHOW` | client | substring, case-insensitive |

The gameweek chips carry no count badge. The badge cost six `creator_board`
calls and 566 KB, and section 8 lists what the reader loses with it.

### 6.2 Level 2

The call is always `{creator, limit: 200, include_untranscribed: true}`, so
the whole archive is in hand and every filter is client-side. `counts` comes
from the payload and is never recomputed from the visible rows.

| column | sorts | key |
|---|---|---|
| `PUBLISHED` | yes, **default, descending** | `published_at`, nulls last |
| `AGE` | yes | the same key, inverted |
| `TITLE` | yes, alphabetical | `title` |
| `WHERE` | yes, grouped | `source_kind` |
| `TEXT` | yes, in the order `transcribed, queued, failed, none` | `transcription_state` |
| `CHARS` | yes | `transcript_chars`, nulls last |
| `READ` | yes | `analysis_present`, then `analysis_model` |
| `CLM` | yes | `claim_count` |
| `GW` | yes | `gameweek`, nulls last |
| `LINK` | no | `source_url` |

Filters: four state chips over `transcription_state`, each showing its own
count from the rows in hand, plus `all`. The default is `transcribed`, which
is the owner's sentence about every transcribed episode being in the view,
and `all` is one click away with its own count beside it. A text box filters
`title`.

The reason `include_untranscribed` is sent as `true` and then filtered in the
browser: the alternative is a second panel call every time the reader presses
`failed`, and the whole archive for the largest creator is 200 rows.

### 6.3 Level 3

`players` arrives sorted most-talked-about first and that is the default. The
group rows sort by `PLAYER` alphabetical, `CLM` descending, `TM` and `POS`.
Claim rows inside a group keep payload order and do not sort, because their
order is the order the analysis stored them and reordering them implies a
ranking the payload does not carry.

`transfers_suggested` sorts by `DIR`, `PLAYER`, `CONV` and `GW`.
`captain_view` sorts by `PLAYER`, `CONV` and `GW`.

Filters at level 3, all client-side over the served arrays:

- channel chips, `claim` and `call`, from the distinct `kind` values present
- direction chips, from the distinct `direction` values present, which live
  on the GW4 transfer-tips episode are `buy`, `sell`, `avoid` and `captain`
- `has quote`, keeping rows where `quote` is non-null
- `this gameweek`, keeping rows where `gameweek` equals `episode.gameweek`,
  which on that episode drops the GW5, GW7 and GW9 Haaland captaincy rows and
  is the filter that makes the distinction in `episodes.py:370-380` visible:
  the header gameweek is where the episode was published, the claim gameweeks
  are what each call targets

### 6.4 One sort implementation, three levels

The comparator, the header click handler, the arrow glyph and the
nulls-last rule are 40 lines written once and called by all three tables.
`table.data th` is already `cursor: pointer` with a `.sorted` state at
`app.css:139-142`, so the three tables inherit the Fixtures tab's sort
affordance without a new class.

---

## 7. The report card

### 7.1 How it stays folded

It is not folded. It is not fetched.

Today `loadReportCard` (`creators.js:656-673`) runs on arrival, 134 KB and one
call, and paints 31 cards into a `details` that measures 2,101 px when opened.
In the ledger, level 1 carries the record from a field that is already in the
level 1 payload: `creator_board.creators[].record` holds `scored`, `hits`,
`hit_rate`, `wilson_lo95`, `weight`, `earned` and `reason`, which is every
number the `HIT` and `N` columns print. The report card is not needed to draw
level 1 and is not requested to draw it.

### 7.2 Where it opens

Two entry points, both leading to the same drawer, both costing one call the
first time and zero after:

1. The line under the level 1 table: `how this is scored`. Opens the drawer
   at the top, showing `note`, `baseline.label`, `baseline.reason`,
   `min_scored_claims`, `min_gw_measured` and `gaps`, then the 31 cards.
2. A row's `HIT` cell. Opens the same drawer scrolled to that creator's card.

The drawer is the same `aside.drawer` node at its default `min(560px, 96vw)`,
not the wide one, because a report card is prose and three narrow tables.

### 7.3 What it is, once open

Per card: the creator name, `headline` verbatim as one sentence, then three
tables that never merge.

- **claims**: `n_scored`, `hits`, `hit_rate`, `wilson_lo95`, `wilson_hi95`,
  `vs_coin_flip`, `weight`, `earned`, `min_scored_claims`, `first_claim_utc`,
  `last_claim_utc`, and the two nested tables `by_gw` and `by_action` as rows
- **team**: one row per person in `team.people`, with `n_gw`, `points`,
  `baseline_points`, `mean_delta`, `delta_ci95`, `beats_baseline`,
  `latest_overall_rank`, and `team.reason` as the caption
- **numeric**: `provider`, `mae`, `rmse`, `baseline_mae`, `baseline_rmse`,
  `n_obs`, `n_gw`, and `reason`. Live, `measured` is false on all 31 cards
  and the table is one row saying so

No bars. `rangeBar` drew a confidence interval as a bar and `gwBars` drew a
gameweek series as bars; both are deleted and the numbers they encoded are
printed. A 95% interval of 0.4102 to 0.5086 is two numbers, and a 200 px bar
carries them less precisely than the digits do.

### 7.4 The one exception to the depth rule, stated

The report drawer is opened from level 1 and is not level 2. It is the
definition of a column, reached from that column's header, and it contains no
link that goes deeper. A reader in the report drawer has exactly one way out,
which is closing it, and closing it returns them to level 1 with their sort
and filters intact. That is a footnote, not a layer, and the design says so
rather than pretending the hierarchy has four levels.

---

## 8. Risks, and what a reader loses

### 8.1 What the owner can do today and cannot do after this

1. **See which panel member owns which consensus player.** The said-vs-owned
   matrix is 3,997 px and draws 54 people against 4 players, and it is
   deleted. `panel_squads` says 43 of 54 people have a known squad, and
   `creator_detail.squad` is the only place those squads are readable.
   Nothing else in the app shows them. If the owner uses that grid weekly,
   this design is wrong for them and design B or C should win on that ground
   alone.
2. **See the cross-creator view of one player.** `consensus[]` and the main
   takes cards answered "five people are on Palmer, two are off him" on the
   creators tab. After this, that question is asked from the player drawer
   that `xpoints.js`, `template.js` and `playerdrawer.js` mount, which is a
   different tab. The question survives; the entry point moves, and moving an
   entry point is a real cost to somebody who knows where it was.
3. **See the captaincy tally across creators.** `renderArmband` drew it as
   bars from `consensus[].captain.n`. Level 3 shows one episode's captain
   calls. No level shows the tally, because no panel serves it except
   `consensus[]`.
4. **See how many items each gameweek has, on the chip.** The badge cost six
   panel calls. A reader who wants that count now presses the chip and reads
   the table.
5. **See their own squad's exposure to what was said.** `squad_overview` is
   no longer called, so `mine`, the lane colours and the "against your squad"
   framing go. The dashboard owns squad.
6. **The 48-hour window toggle.** Replaced by the `LATEST` column's sort and
   the `spoke 7d` filter, which is a coarser tool.

### 8.2 Risks in the design itself

1. **It depends on two panels that are not on `main`.** `creator_episodes`
   and `episode_summary` are in a worktree. If they do not land, level 2 has
   no cheap payload and the fallback is `creator_detail` at 540 KB per
   creator, which is the defect being removed. This design is not shippable
   in stages: level 1 alone is a smaller page with no answer to the owner's
   actual question.
2. **Ten numeric columns and no colour.** Fixtures earns its colour from
   FPL's own FDR scale, which has an external referent. Creator counts have
   none, and inventing a ramp for "number of claims" would be a hue that says
   something the payload does not. So level 1 is monochrome, and a reader
   scanning for "who is worth opening" gets no preattentive cue. They get a
   sort. That is the trade, and a reader who wants a glance rather than a
   sort is worse off.
3. **The `HIT` column invites a ranking the payload forbids.** Live, every
   `earned` is false and `record_note` says no creator has beaten a coin
   flip. A sortable column of percentages is a leaderboard whatever the note
   above it says. The faint treatment and the note are the mitigation and
   they are weaker than the instinct to sort.
4. **`transcribed` and `analysed` are not nested, and the columns imply they
   are.** The brief's live sample has 35 transcribed and 67 analysed of 73.
   A reader who sees "35 transcribed, 67 analysed" will read it as a
   contradiction before they read it as two independent columns. The counts
   line states it in words, and words lose to a number.
5. **The 200-row cap bites on the largest creator.** `Fantasy Football Scout`
   has `n_items: 306`. Level 2 shows 200 and says so. The reader who wants
   episode 250 cannot reach it without a panel change.
6. **Two rows for one recording, 17 times out of 23 for one creator.** The
   design refuses to merge them and explains why in a footnote. A reader who
   does not read footnotes sees a duplicated archive and concludes the data
   is dirty.
7. **The quote cell renders untrusted third-party prose.** Podcast titles and
   transcript fragments are rendered in the page. Every one goes through
   `textContent`. A single `innerHTML` anywhere in the three levels is a
   defect, and the review should grep for it.
8. **Drawer depth and the browser back button.** Three levels in one drawer
   node with a back row means the browser's back button leaves the tab
   instead of going up a level. The current tab has the same property, so
   this is inherited, not introduced, and it is still wrong.
9. **The line-count target is an estimate.** 1,758 plus or minus 80 is
   arithmetic over ranges read by eye. The deletions are exact; the trims and
   the additions are not.
10. **`creators_link.js` is a move, not a saving.** The repo keeps the 832
    lines. What changes is that the creators view stops carrying a
    job-polling state machine that has nothing to do with reading an archive.

### 8.3 What would make me abandon this design

If the owner's weekly loop turns out to be "open the tab, look at the five
consensus players, decide", then the ledger is the wrong shape and the matrix
was the point. The evidence against that is in the payload: 4 of 29 creators
have a take, 5 players are on the consensus, and 653 items were published in
30 days of which 345 have never been read. An archive of 1,100 items
summarised into five players is a page that throws away almost everything it
fetched. The owner's own words, that every transcribed episode should be
available in the view, say the archive is the object. If that reading is
wrong, everything above it is wrong with it.
