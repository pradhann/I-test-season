# Creators, design B: the reader

> Design proposal B for the Creators rebuild, written 2026-09-18. Every pixel
> figure below was measured in a real browser against `localhost:8321` at an
> emulated 1280x900 and 390x844, every payload figure came from a live
> `POST /api/scripts/.../run`, and every line range came from a brace-matching
> pass over the file rather than from reading a comment banner. The two new
> panels are not on the running server, so their shapes come from
> `EPISODES_RESULT` and `SUMMARY_RESULT` in the worktree file and their volumes
> from a measured proxy that is named where it is used. Where a number is an
> estimate it says so in the same sentence.
>
> This document supersedes the August 2026 file at this path, which described
> the surface that shipped and is now the thing being replaced. The old text is
> in git at `13952c6`.

---

## 0. The one sentence

> **A creator is a name, a creator's work is a list of episodes, and an episode
> is something you read. Three pages, one click apart, and the page you are on
> is the only page on the screen.**

The owner asked for exactly this and then said what it was for: "If I click FPL
Wire, it should show which episodes were latest transcribed, with link and
title. If I click the episode I should see the main summary relevant to FPL."
The second sentence of the brief is the constraint that makes it hard: the tab
"is too cluttered", and 3,880 lines of `creators.js` are the reason.

Design B optimises for reading one episode well. It does not optimise for
scanning many, and section 9 names what that costs.

---

## 1. What is on the page today, measured

### 1.1 The page

`http://localhost:8321/#creators` at an emulated 1280x900, after the full load
settles (about 130 seconds, section 1.2). Heights from
`getBoundingClientRect().height`, document height from
`document.body.scrollHeight`.

| region | class | height, folded | height, opened |
|---|---|---:|---:|
| top card: honesty line, scope, coverage, source strip | `.card.cx` | 299 | 494 |
| main card | `.card.cx` | 1,451 | 7,521 |
| `-` fact row | `.toolbar.cx-factrow` | 45 | 45 |
| `-` gameweek row | `.toolbar` | 46 | 46 |
| `-` main takes | `.cx-sec.cx-maintakes` | 643 | 643 |
| `-` panel intent chart | `.cx-sec` | 316 | 316 |
| `-` the armband | `.cx-sec` | 127 | 127 |
| `-` said vs owned matrix | `.cx-sec` | 46 | 3,997 |
| `-` report cards | `details.cx-rcsec` | 33 | 2,101 |
| `-` provenance | `div` | 16 | 16 |
| add a source card | `.card.cx` | 136 | 136 |
| **document** | | **2,041** | **8,307** |

At 390x844 the same page is **2,919 px** folded and **13,738 px** with the
matrix and the card wall open. The two toolbar rows alone are **298 px** at that
width (fact row 177, gameweek row 121), so a phone reader scrolls a third of a
screen of controls before the first fact.

Other counts inside `.content`, folded, at 1280:

| what | measured |
|---|---:|
| interactive elements (`button, a, summary, [role=button], input, select`) | 125 |
| DOM nodes | 1,507 |
| elements carrying a `title` attribute | 386 |
| distinct rendered font sizes | 14 |
| rendered characters | 2,733 |
| non-blank rendered lines | 127 |

386 tooltips over 1,507 nodes means roughly one element in four carries a fact
that is only reachable by hovering, which a phone cannot do at all.

### 1.2 What one visit costs

`performance.getEntriesByType('resource')` after a single clean load at 1280,
no clicks:

| endpoint | calls | transferred | total duration |
|---|---:|---:|---:|
| `creator_detail` | 29 | 3.12 MB | 103,026 ms |
| `creator_board` | 7 | 0.55 MB | 24,646 ms |
| `creator_report_card` | 1 | 0.13 MB | 596 ms |
| `squad_overview` | 2 | 0.01 MB | 2,888 ms |
| `fixture_board` | 1 | 0.14 MB | 451 ms |
| `GET /api/content/sources` | 1 | 0.02 MB | 405 ms |
| `GET /api/deadline` | 1 | 0.00 MB | 153 ms |
| **total** | **42** | **3.97 MB** | **132,165 ms** |

The 29 `creator_detail` runs are `loadRecency()` at `creators.js:290-309`, which
fetches every creator's full 60-day corpus to learn one thing: the newest
`published_at` per player code, so the fact row can offer a 48-hour window. One
of those payloads, `creator_detail {creator: "Let's Talk FPL"}`, is **540,016
bytes** uncompressed. The seven `creator_board` runs are `probeGameweeks()` at
`:470-491`, which speculatively runs the board for four gameweeks back and two
forward so the gameweek chips can carry counts.

**The page spends 127.7 seconds of server time and 3.67 MB on two toolbar
affordances.** Neither of them answers the owner's question.

### 1.3 What clicking a creator gives you today

Clicking `Let's Talk FPL` in the matrix gutter opens
`aside.cx-drawer`, measured at **560 px wide, 4,054 px of scroll inside a
900 px viewport**, 4,849 characters:

| block | height |
|---|---:|
| head, name and entry | 42 |
| record line | 71 |
| squad, locked GW4, 15 photo cards | 435 + 37 |
| quiet holdings | 91 |
| talked about, does not own, **91 players** | 2,659 |
| transfers | 483 |
| method fold | 32 |

There is no episode in it. No title, no link, no summary, no transcription
state. The one thing the owner asked for by name is the one thing the creator
drawer does not contain, and a 2,659 px list of 91 player names sits where it
would go.

### 1.4 Two facts the surface cannot serve today

**Nothing on the page reads an episode.** `creator_detail.items[]` carries
`title`, `url`, `published_at`, `kind`, `text_source`, `analysis` and `claims`
for 40 items. `creators.js` reads `items[].claims[]` in three places
(`loadRecency` at :290, `openPerson` at :3600, `openPlayer` at :3812) and reads
`items[].title` only as a caption beside a quote at :3855-3862. No surface lists
them, orders them, or opens one.

**The corpus holds each recording twice and the page never says so.** Over Let's
Talk FPL's 40 items in the 60-day window there are **24 distinct titles**, and
**16 of them appear twice**, once as `kind: podcast` and once as
`kind: youtube`. Measured publication gaps between the members of a pair:
2m 13s, 6m 56s, 12m 00s, 14m 46s, 17m 08s and 1d 0h 52m. Sixteen of forty rows,
**40 percent of the list**, are a second copy of a recording already in it.

One pair matters more than the rest. `FPL GW4 Transfer Tips` is stored as
`d26fac8a02a1928c1a1ea9f6` (podcast, 422 transcript segments, 1,196-character
stored summary) and `6cbfeff43e49daa407899c81` (youtube, 891 segments,
1,428-character summary). Each carries 22 stored claims. In the live
`creator_detail` payload the YouTube copy resolves an offset on **15 of its 22
claims and 18 of its 23 stored calls**, and carries deep links of the form
`https://www.youtube.com/watch?v=I79r-zM_Zt0&t=225s`. The podcast copy resolves
an offset on **0 of 22 claims and 0 of 24 calls**, and every `deep_link` is the
bare MP3 URL. Same recording, same words, one copy readable in time and one
not.

---

## 2. Field map: six payloads, three levels

### 2.1 Which panel each level calls, and what a click costs

| level | panels called | requests | payload measured or estimated |
|---|---|---:|---|
| 1, the directory | `creator_board {mine: false}`, `creator_report_card {}`, `GET /api/content/sources` | 3 | 57,983 + 132,922 + 25,481 bytes uncompressed, all three measured |
| 2, one creator's episodes | `creator_episodes {creator, limit: 30}` | 1 | about 18,750 bytes, estimated, section 2.3 |
| 3, one episode | `episode_summary {item_id}` | 1 | about 19,000 bytes, estimated, section 2.4 |
| 2, record fold opened | `creator_report_card {creator}` | 1, lazy | one card, measured at 4,286 bytes inside the 132,922-byte all-cards payload |

**A click costs exactly one request.** Opening the record fold at level 2 costs
one more, only when it is opened, and never on a page that did not ask.

Reading one episode from cold is **5 requests**. Today's page is 42 requests
before a click and cannot reach the episode at all.

`creator_board` is called with `{mine: false}` because no level of design B
reads `consensus[].mine`. That parameter is the one thing on the board that
leaves the warehouse (`BOARD_PARAMS.mine`, "tries the private API, then public
picks, then the manually entered 15"), and turning it off removes a network
round trip from the first paint. `_my_roles` at `board.py:74-76` then returns

> the squad read was disabled by the caller (`mine: false`), so no ownership is
> claimed either way

as `mine_reason`, which level 1 prints in the scope fold. The field is therefore
always populated, which is stronger than today, where it is null whenever the
read succeeds.

`creator_detail` and `player_chatter` are called by **no level**. Section 9 says
what that costs.

### 2.2 Level 1, the compact directory

One line per creator, sorted by `last_item_at` newest first. Source:
`creator_board.creators[]`, one row each. `creator_report_card` feeds the folded
honesty block only.

| element on screen | payload key | note |
|---|---|---|
| creator name, the click target | `creators[].creator` | verbatim, the same string `creator_episodes.creator` takes |
| age, in days | `creators[].last_item_at` | days only, section 4.4. Null renders as `no items on file` |
| publications on file | `creators[].n_items` | labelled `on file`, never `episodes`: it counts stored rows, and `creator_episodes.counts.episodes_total` counts publications |
| claims in the window | `creators[].n_claims_window` | with `window_days` in the header, once |
| newest title | `creators[].latest.title` | verbatim third-party prose, one line, clipped |
| newest title missing | `creators[].latest_reason` | printed in the title slot when `latest` is null |
| page heading age | `as_of` | `relAge`, hours allowed here, section 4.4 |
| window, once in the header | `window_days` | |
| scope line | `scope.applied`, `scope.shows`, `scope.excluded`, `scope.reason` | inside the fold |
| squad not read | `mine_reason` | inside the fold, one sentence |
| coverage | `coverage.note`, `coverage.items`, `coverage.analysed`, `coverage.newest_unread` | inside the fold |
| per source backlog | `coverage.by_source[]` | inside the fold, inside the source strip |
| honesty line | `record_note` | inside the fold |
| card census | `creator_report_card.cards[].claims.measured`, `.n_scored`, `.vs_coin_flip`, `min_scored_claims`, `note`, `as_of` | inside the fold, via the existing `cardCensus()` and `censusLine()` |
| report card gaps | `creator_report_card.gaps[]` | inside the fold |
| source strip | `GET /api/content/sources` rows: `key`, `kind`, `state`, `tier`, `last_item_at`, `last_probe_utc`, `last_status`, `last_error`, `can_fetch_now`, `newest_published`, `discovery`, `policy` | unchanged from today |

Nothing else. In particular level 1 does **not** print a transcription count per
creator, because the only panel that knows one is `creator_episodes` and it
takes a single `creator`. Printing "35 of 73 transcribed" on 29 rows would cost
29 calls, which is the mistake `loadRecency` already makes. The count appears at
level 2, where one call answers it.

### 2.3 Level 2, one creator's episodes

Source: `creator_episodes {creator, limit: 30, include_untranscribed: false}`.

**Header:**

| element | payload key |
|---|---|
| creator name | `creator` |
| `73 on file, 35 transcribed, 67 analysed` | `counts.episodes_total`, `counts.transcribed`, `counts.analysed` |
| which list is showing | `include_untranscribed`, `limit`, `episodes.length` |
| the person and their entry | `entry.name`, `entry.entry_id`, `entry.verified`, `entry.source_url` |
| several hosts | `entry.people[].person`, `.entry_id`, `.name`, `.verified`, `.source_url`, `.reason` |
| no entry | `entry_reason` |
| record, collapsed line | `record.hit_rate`, `.scored`, `.hits`, `.wilson_lo95`, `.weight`, `.earned`, `.reason` |
| read time | `as_of` |

**One row, five fields and a glyph:**

| element | payload key | rendering |
|---|---|---|
| state glyph | `episodes[].transcription_state` | one character, section 4.1 |
| title | `episodes[].title` | verbatim, one line, clipped at the 760 px list width |
| the link | `episodes[].source_url` | an anchor on a small `open` affordance at the row end, `target="_blank" rel="noopener noreferrer"`. Null renders as `no link stored` |
| age, in days | `episodes[].published_at` | days only |
| kind | `episodes[].source_kind` | the word, `podcast`, `youtube`, `blog` or `link` |
| transcript size | `episodes[].transcript_chars` | `74,231 chars`, section 4.1 on why it is not minutes |
| claims | `episodes[].claim_count` | `40 claims` |
| gameweek | `episodes[].gameweek` | `GW4`, or `no gameweek` when null |

`analysis_present` and `analysis_model` are read but not printed as their own
column: `claim_count` of zero together with `analysis_present: false` is what
the row means by having nothing to read, and the two are combined into the row's
disabled state (section 4.3). `analysis_model` prints at level 3 where there is
room for the model name beside the analysis it wrote.

`gw_reason` is read at level 2 and **not printed**. It is a 230-character
sentence and there are 30 rows. It prints in full at level 3, once.

Volume: a single row serialised with the real `item_id`, `title`, `source_url`
and a real `gw_reason` measures **625 bytes**, so 30 rows is about 18.8 KB. That
is an estimate built from a measured row, not a measurement of the panel.

### 2.4 Level 3, one episode

Source: `episode_summary {item_id}`.

**Header:**

| element | payload key |
|---|---|
| title | `episode.title` |
| creator, the middle crumb | `creator` |
| age, in days | `episode.published_at` |
| kind | `episode.source_kind` |
| the link | `episode.source_url` |
| gameweek and why | `gameweek`, `gw_reason`, printed in full |
| who wrote the analysis | `analysis_model`, `analysed_at` |
| transcript size and state | `episode.transcript_chars`, `episode.transcription_state` |
| claims on file | `episode.claim_count` |
| read time | `as_of` |

**The summary, first:**

| element | payload key |
|---|---|
| the stored bullets, as a list | `summary_bullets[]` |
| no summary | `summary_reason` |

`summary` is the same content joined by newlines. Level 3 renders
`summary_bullets` and ignores `summary`, because a list of stored bullets is a
list and splitting a joined string to get it back is a round trip through a
lossy format.

The block renders the list it is given and never six fixed slots.
`TranscriptAnalysis.summary` is documented as 3 to 6 bullets, the FPL Family
analysis in the live warehouse carries 6, and the YouTube copy of the Let's Talk
FPL episode carries **7**. A layout built for six would drop one of them.

**The call index, one line under the bullets:**

| element | payload key |
|---|---|
| `5 transfers in, 14 transfers out, 3 captaincy, 0 differentials` | counted over `players[].claims[].stored_in` |

This is a count of what the payload holds, not a new fact. It exists so a reader
who wants the transfer calls can find them without scrolling 20 player blocks,
and each count is an in-page jump to the first player carrying that
`stored_in`.

**Players, grouped, in prose type:**

| element | payload key |
|---|---|
| player heading | `players[].display_name` |
| the spoken name when it differs | `players[].name` |
| two players share the name | `players[].disambiguator` |
| the name did not resolve | `players[].resolved`, rendered as `name as spoken, not resolved to a player` |
| club and position | `players[].team`, `players[].position`, null when `resolved` is false |
| how many stored positions | `players[].claims.length` |
| what was said | `players[].claims[].direction` |
| which channel | `players[].claims[].kind` and `.stored_in` |
| how sure, numeric | `players[].claims[].confidence`, for `kind: claim` |
| how sure, in words | `players[].claims[].conviction`, for `kind: call` |
| the quote | `players[].claims[].quote`, inline, at reading size |
| the offset | `players[].claims[].start_s`, as `mm:ss` |
| the offset as a link | `players[].claims[].deep_link`, section 4.6 |
| which gameweek the call targets | `players[].claims[].gameweek` |

**What is not here:**

| element | payload key |
|---|---|
| each empty section and why | `gaps[].section`, `gaps[].gap` |

**Read and deliberately not rendered as their own sections:**
`transfers_suggested[]` and `captain_view[]`. Every object in both arrays is
already in `players[].claims[]`: `_players()` in `episodes.py:616-679` walks
`_CALL_LISTS`, which is exactly `transfers_in`, `transfers_out`, `captaincy` and
`differentials`, and files each call under its player with `stored_in` naming
the list it came from. Rendering the two arrays again prints the same stored
call twice, twelve hundred pixels apart, with `stance` in one place and
`direction` in the other for the same value.

Measured cost of the duplication: the two sections came to **1,517 px** in the
prototype (transfers 1,302, captains 215) out of a 4,130 px page. Dropping them
takes level 3 to **2,626 px** and loses one field, `paired_with`, whose schema
says "Always null: nothing stored records which sale funded which purchase". The
`gaps` entry `transfers_suggested.paired_with` carries the same statement in a
sentence and is printed.

`differentials` has no top-level array at all, so a design that reads the three
lists and not `players` would silently drop a fourth channel that `players`
already carries.

### 2.5 Dropped, with the reason

| payload | field | why it is not on any level |
|---|---|---|
| `creator_board` | `consensus[]` entirely: `code`, `name`, `pos`, `team`, `price`, `own_pct`, `buy`, `sell`, `captain`, `net`, `mine`, `panel_owned` | the per-player consensus is a player question, and the hierarchy is creator, episode, claim. `player_chatter` answers it in one call and is already mounted on the Projections tab through `components/chatter.js` |
| `creator_board` | `panel_squads` | it describes 54 people's squads, which is the matrix's data. Its `reason` string prints in the level 1 fold so the fact that 43 of 43 are crawled is still reachable |
| `creator_board` | `gw`, `gw_reason` | the board's gameweek is the gameweek of the consensus, and the consensus is gone. Each episode carries its own `gameweek` and `gw_reason` |
| `creator_board` | `creators[].take`, `take_reason` | `take` is the newest item's stored analysis, which is level 3 for one episode. `take_reason` prints in the level 1 title slot when `latest` is null, because then it is the only sentence about that creator |
| `creator_board` | `creators[].sources[]` | the same rows arrive better from `GET /api/content/sources`, which carries `state`, `tier` and `can_fetch_now` as well. The strip already reads that endpoint |
| `creator_board` | `creators[].kinds[]` | level 2 prints `source_kind` per episode, which is the same fact at the row that owns it |
| `creator_board` | `creators[].record` | superseded at level 2 by `creator_episodes.record`, which is the identical `_RECORD` object built by the identical `_record(weights.get(creator))` call |
| `creator_board` | `creators[].entry` | same, superseded by `creator_episodes.entry` |
| `creator_detail` | the whole panel | 540 KB for one creator, and every field it carries that design B needs is in `creator_episodes` at 3 percent of the size |
| `player_chatter` | the whole panel | not reachable from a creator-first hierarchy without a sideways move |
| `creator_report_card` | `cards[].numeric` | printed inside `reportBody`, which design B keeps, so it is reachable at level 2 behind the fold. It is not dropped, only never on a first screen |
| `episode_summary` | `summary` | the joined string; `summary_bullets` is the same content in the shape it was stored in |
| `episode_summary` | `transfers_suggested[]`, `captain_view[]` | section 2.4 |
| `episode_summary` | `players[].claims[].deep_link` when it equals `episode.source_url` | the clock is then plain text, section 4.6 |
| `creator_episodes` | `episodes[].gw_reason` at level 2 | 230 characters times 30 rows. It prints at level 3 |
| `creator_episodes` | `episodes[].analysis_model` at level 2 | prints at level 3 |

### 2.6 Nothing on the page that is not in a payload

Four things a UI of this shape is tempted to invent, and where design B refuses
each:

1. **A duration in minutes.** `transcript_chars` is a character count. There is
   no stored duration on an episode row, and characters divided by a speaking
   rate is a model in the browser. The row prints `74,231 chars`.
2. **"These two rows are the same recording."** Nothing stored links them
   (section 4.2). Both rows appear, each with its own `source_kind`.
3. **A creator ranking.** `record.earned` is false for all 29 and
   `record_note` says every earned weight is 0.0. Level 1 sorts on
   `last_item_at` and nothing else, and the sort is stated in the header.
4. **A gameweek for an episode with none.** `gameweek` is nullable and
   `gw_reason` is always a sentence. The row reads `no gameweek` and level 3
   prints the sentence.

---

## 3. Wireframes

All three were built as real DOM in the running page with the real payloads and
measured, then removed. Heights are given per level in section 3.6.

### 3.1 Level 1 at 1280

```
+--------------------------------------------------------------------------------------+
| Creators                                                                              |
| 29 shows on the panel, newest publication first. Read 1m ago.                         |
| > how much has been read, which sources are behind, and the record            (fold)  |
|--------------------------------------------------------------------------------------|
| Fantasy Football Scout      5d    306 on file    297 claims                            |
|   Spurs v Everton team news: Porro + Udogie out, Grealish starts                       |
| Let's Talk FPL              6d     73 on file    495 claims                            |
|   FPL GW4 Final Thoughts (fire) Team News (siren) Triple Captain Palmer                |
| FPL Harry                   6d     66 on file    387 claims                            |
|   FPL GW4 COMPLETE GUIDE FINAL THOUGHTS ON CHELSEA & ARSENAL                           |
| Fantasy Football Hub        6d     58 on file    368 claims                            |
|   MY FPL GW4 WILDCARD TEAM SELECTION! | With Ben Crellin                               |
| FPL Raptor                  6d     59 on file    333 claims                            |
|   MY FPL GW4 FINAL DECISIONS | ISAK & ODEGAARD                                         |
| AllAboutFPL                 6d     43 on file    251 claims                            |
|   FPL GW4 Team Selection- Drafts, Transfer Plans                                       |
| The FPL Wire                6d     42 on file    141 claims                            |
|   UCL Roundup & Revised WC - Zophar's Gameweek 4                                       |
| ...                                                                                    |
| FPL General                56d      1 on file      0 claims                            |
|   FPL 2026/27 is live! - My Reaction to Player Prices                                  |
| > 4 shows with nothing on file                                                (fold)  |
+--------------------------------------------------------------------------------------+
| Add a source                                          [ paste a link ]      [ Add ]    |
| Preview first; nothing is transcribed until you accept.                                |
+--------------------------------------------------------------------------------------+
```

The title sits on the row, not on a second line, in the real layout: the ASCII
above wraps it for legibility at this document's width. The measured row is one
line of 34 px at 1280, with the name at a 190 px column, the age right-aligned
in 40 px, the two counts right-aligned in 86 px each, and the title taking the
rest with `text-overflow: ellipsis`.

There is no table element, no header row, no zebra striping and no border except
a 1 px `--line` rule under each row.

### 3.2 Level 2 at 1280

```
+--------------------------------------------------------------------------------------+
| Creators  >  Let's Talk FPL                                                           |
|                                                                                       |
| Let's Talk FPL                                                                        |
| 73 on file, 35 transcribed, 67 analysed. Showing the 30 newest transcribed.           |
| Andy LTFPL, entry 41, verified.                                                       |
| > 46% of 390 scored claims hit, coin flip, weight 0.0 and unearned          (fold)    |
|                                                                                       |
|                                              [ show every episode, transcribed or not ]|
|--------------------------------------------------------------------------------------|
| (dot) FPL GW4 Transfer Tips SELL Bruno / Palmer IN                          open >    |
|       10d . youtube . 74,231 chars . 40 claims . GW4                                  |
|--------------------------------------------------------------------------------------|
| (dot) FPL GW4 Transfer Tips SELL Bruno / Palmer IN                          open >    |
|       10d . podcast . 41,903 chars . 40 claims . GW4                                  |
|--------------------------------------------------------------------------------------|
| (dot) My FPL Wildcard Team for Gameweek 4 Triple Chelsea                     open >   |
|       11d . youtube . 68,004 chars . 38 claims . GW4                                  |
|--------------------------------------------------------------------------------------|
| (ring) FPL Gameweek 4 Preview Free Hit 4 Team Palmer v Joao Pedro           open >    |
|        9d . podcast . no transcript . 1 claim . GW4                                   |
|--------------------------------------------------------------------------------------|
| (x)   Pressing Buttons                                                      open >    |
|       13d . youtube . no transcript . 0 claims . GW3                                  |
|       no_captions                                                                     |
|--------------------------------------------------------------------------------------|
| ... 25 more                                                                           |
+--------------------------------------------------------------------------------------+
```

The list is capped at 760 px and left aligned. The glyph column is 16 px. The
row is 54 px: a 14 px title line and an 11.5 px meta line. `open >` is a
separate anchor to `source_url`; the rest of the row is the button that goes to
level 3.

The two `FPL GW4 Transfer Tips` rows are the real duplicate pair, shown as the
payload serves them. Section 4.2.

### 3.3 Level 3 at 1280

```
+--------------------------------------------------------------------------------------+
| Creators  >  Let's Talk FPL  >  this episode                                          |
|                                                                                       |
| FPL GW4 Transfer Tips SELL Bruno / Palmer IN                                           |
| Let's Talk FPL . 10d . youtube . GW4 . 40 claims . claude-opus-5, written 9d ago       |
| open the source >                                                                     |
|                                                                                       |
|   GW3 review episode from FPL Family covering Arsenal's 2-1 win over   |<-- 504 px -->|
|   Chelsea at the Emirates.                                                            |
|                                                                                       |
|   Discusses whether Rogers, Palmer and Joao Pedro should be bought,                    |
|   kept or sold.                                                                       |
|                                                                                       |
|   Asks whether Haaland's single goal was enough of a return for                       |
|   managers who used the Triple Captain chip.                                          |
|                                                                                       |
|   Covers Isak's brace and his transfer prospects, plus Hull's clean                   |
|   sheet record.                                                                       |
|                                                                                       |
|   Rounds up GW3 winners and losers, early transfer thoughts, captaincy                |
|   options and differentials, with viewer questions taken live.                        |
|                                                                                       |
|   Notes include the mini-league code, Patreon links and social plugs.                 |
|                                                                                       |
| Calls stored in this episode: 5 transfers in, 14 transfers out,                       |
| 3 captaincy, 0 differentials. Each one sits under its player below.                   |
|                                                                                       |
| Palmer   MID . CHE . 3 stored                                                         |
|   buy . llm . confidence 0.6 . GW4 . 3:45                                             |
|   | "I'm instantly drawn to Cole Palmer because of what we've seen                    |
|   |  in the past"                                                                     |
|   captain . llm . confidence 0.6 . GW4 . 4:31                                         |
|   | "you've got Palmer Hull at home, Bournemouth at home, Spurs at                    |
|   |  home in four, six, and eight"                                                    |
|   transfers_in . call . conviction medium . GW4 . 3:45                                |
|   | "I'm instantly drawn to Cole Palmer because of what we've seen                    |
|   |  in the past"                                                                     |
|                                                                                       |
| Wirtz    2 stored, name resolved, no club on file                                     |
|   sell . llm . confidence 0.4 . GW4 . 9:54                                            |
|   | "I'd probably keep for Fulham at home, but I'd maybe be looking                   |
|   |  when to get rid after that soonish"                                              |
|   avoid . llm . confidence 0.8 . GW4 . 10:07                                          |
|   | "but I definitely wouldn't be looking to bring him in"                            |
|                                                                                       |
| ... 15 more players                                                                   |
|                                                                                       |
| What is not here                                                                      |
|   transfers are stored as two independent lists, so which sale funded                 |
|   which purchase is not recorded and is not inferred here.                            |
+--------------------------------------------------------------------------------------+
```

Every string above is from the live payloads. Palmer's two claims are at
`start_s` 225.68 and 271.52, which is 3:45 and 4:31; Wirtz's are at 594.24 and
607.68. The clocks are anchors to `deep_link` here because this is the YouTube
copy and `deep_link` carries `&t=225s`. On the podcast copy of the same
recording they are plain text. Section 4.6.

Wirtz's heading shows the `resolved: true` and `team: null` case, which happens
when the code resolves and `sem_players` has no club row at this instant.
Palmer's shows the populated case, and his `disambiguator` is `C. Palmer (CHE)`
in the live payload, so the heading would read that rather than the bare
surname.

Every quote and every bullet sits inside a 504 px column. Measured: 65 lowercase
characters of the app's font stack at 15px occupy **504.0 px** (7.75 px per
character, measured over a 60-character string at 464.9 px).

### 3.4 Level 1 at 390

```
+----------------------------------------+
| Creators                               |
| 29 shows, newest first. Read 1m ago.   |
| > how much has been read              |
|----------------------------------------|
| Fantasy Football Scout    5d           |
| 306 on file  297 claims                |
| Spurs v Everton team news: Porro +...  |
|----------------------------------------|
| Let's Talk FPL            6d           |
| 73 on file  495 claims                 |
| FPL GW4 Final Thoughts Team News Tr... |
|----------------------------------------|
| FPL Harry                 6d           |
| 66 on file  387 claims                 |
| FPL GW4 COMPLETE GUIDE FINAL THOUGH... |
|----------------------------------------|
| ...                                    |
| > 4 shows with nothing on file        |
+----------------------------------------+
```

At 640 px and below the row wraps to three lines and measures 52 px: name and
age on the first, the two counts on the second, the clipped title on the third.
No horizontal scroll at 390 (`document.documentElement.scrollWidth` equals
`innerWidth`, measured).

### 3.5 Level 3 at 390

```
+----------------------------------------+
| Creators > Let's Talk FPL > episode    |
|                                        |
| FPL GW4 Transfer Tips SELL Bruno /     |
| Palmer IN                              |
| Let's Talk FPL . 10d . youtube . GW4   |
| 40 claims . claude-opus-5, 9d ago      |
| open the source >                      |
|                                        |
|   GW3 review episode from FPL Family   |
|   covering Arsenal's 2-1 win over      |
|   Chelsea at the Emirates.             |
|                                        |
|   Discusses whether Rogers, Palmer     |
|   and Joao Pedro should be bought,     |
|   kept or sold.                        |
|                                        |
|   ... four more bullets                |
|                                        |
| Calls stored: 5 in, 14 out,            |
| 3 captaincy, 0 differentials.          |
|                                        |
| Palmer   MID . CHE . 3 stored          |
|   buy . llm . 0.6 . GW4 . 3:45         |
|   | "I'm instantly drawn to Cole       |
|   |  Palmer because of what we've      |
|   |  seen in the past"                 |
|   captain . llm . 0.6 . GW4 . 4:31     |
|   | "you've got Palmer Hull at home,   |
|   |  Bournemouth at home, Spurs at     |
|   |  home in four, six, and eight"     |
|                                        |
| ... 16 more players                    |
|                                        |
| What is not here                       |
|   transfers are stored as two          |
|   independent lists, so which sale     |
|   funded which purchase is not         |
|   recorded and is not inferred here.   |
+----------------------------------------+
```

At 390 the reading column is the full content width, **366 px**, which is 47
characters at 15 px. That is under the 65-character cap rather than over it, so
the rule is `max-width: min(var(--cr-measure), 100%)` and the cap never causes a
horizontal scroll.

### 3.6 Measured heights

Built in the live page with the real `creator_board` and `creator_detail`
payloads and measured with `getBoundingClientRect()`.

| level | contents | 1280 | 390 |
|---|---|---:|---:|
| 1 | 25 creator rows plus a fold holding 4 more | **957** | **1,432** |
| 2 | header plus 30 episode rows | **1,709** | **2,311** |
| 3 | header, 6 bullets, call index, 17 players carrying 22 stored positions, 2 gap sentences | **2,626** | **2,944** |
| today, folded | fact row, gameweek row, main takes, chart, armband, two folds | 2,041 | 2,919 |
| today, opened | the same with the matrix and the card wall | 8,307 | 13,738 |

Level 1 is **47 percent** of today's folded height and shows 25 creators instead
of 4 players. Level 3 is a long page and is meant to be: it is one episode read
end to end, and the six bullets are inside the first 390 px of it at 1280.

Row measurements, for anyone checking the arithmetic:

| row | 1280 | 390 |
|---|---:|---:|
| level 1 row | 34 | 52 |
| level 2 row | 54 | 74 |
| level 3 evidence block, one quote | 42 | varies with wrap |
| level 3 six-bullet block | 319 | 366 |
| level 3 header to the first bullet | 70 | 109 |

---

## 4. Empty, stale, error and gap states

### 4.1 The state glyph

`transcription_state` is a closed enum of four values, each read off named
columns by `_transcription_state` at `episodes.py:320-335`. One character each,
with the colour from an existing app token and an `aria-label` rather than a
`title`:

| value | glyph | colour | label, and the sentence beside a row that needs one |
|---|---|---|---|
| `transcribed` | filled dot | `--ink` | `transcript on file` |
| `queued` | hollow ring | `--muted` | `audio on file, not transcribed yet` |
| `failed` | times | `--bad` | `transcription refused` |
| `none` | en dash, the app's null glyph | `--faint` | `nothing to transcribe and nothing queued` |

The glyph is the only colour on a level 2 row. Nothing else on the row carries a
hue, so the four states are distinguishable at a glance without reading.

`transcript_chars` renders as a grouped integer with the unit attached:
`74,231 chars`. Null renders as `no transcript`. It is never divided by a
speaking rate, never called a duration and never called minutes. The row's meta
line is the only place it appears, and a reader who wants relative length gets
it from comparing two numbers in the same column.

### 4.2 The duplicate podcast and YouTube pair

Measured for Let's Talk FPL: 40 stored items over the 60-day window, 24 distinct
titles, 16 titles appearing twice, gaps from 2m 13s to 1d 0h 52m.

`creator_episodes` groups on `urls.canonical_key`, which is `yt:<video id>` when
the URL is a YouTube form and `url:<url>` otherwise
(`urls.py:58-67`). A podcast MP3 URL and a YouTube watch URL for one recording
produce two different keys, so the pair stays two publications. The panel is
explicit that it collapses the case it can see, the same video stored twice, and
the live evidence is that it collapses nothing for this creator: the board
serves `n_items: 73` and the brief's `counts.episodes_total` is 73, so no stored
row merged with another.

**Design B shows both rows and states the grouping rule, once, in the level 2
header fold:**

> Episodes are grouped by their link. A recording published to a podcast feed
> and to YouTube has two links and appears twice, once per feed. Each row's
> kind says which one it is.

That sentence describes how `creator_episodes` groups, which is the panel's own
documented contract rather than a claim about any particular pair of rows. No
row is merged, no row is marked as a copy of another row, and nothing on screen
asserts that two titles being equal makes two recordings the same recording.
Merging them would print a fact nothing serves.

**The consequence a reader can act on is served, and it is printed at level 3.**
For the `FPL GW4 Transfer Tips` pair, the YouTube copy resolved 33 of its 45
stored positions in time and the podcast copy 0 of its 46. Level 3 counts the
non-null `start_s` values in `players[].claims[]` and prints, under the header:

> 33 of 45 stored positions carry an offset into the transcript.

or, on the podcast copy:

> 0 of 46 stored positions carry an offset into the transcript, so no quote
> below is a link into the recording.

Counting the nulls in an array the payload served is reading it. A reader who
lands on the podcast copy sees that line, goes back one level, and opens the row
above it, which is the same recording with working timestamps. That is the
honest path to the same outcome as merging, and it costs one back and one click.

**A hole in the payload, named.** `_gaps` at `episodes.py:743-786` raises the
`timestamps` gap only when `has_transcript` is false. The podcast copy of this
pair has 422 stored transcript segments, so `has_transcript` is true, so no gap
fires, and every `start_s` is null with nothing in `gaps` explaining it. The
count line above is design B's answer inside the UI. The durable fix belongs in
`episodes.py`: raise the `timestamps` gap when a transcript exists and no quote
resolved against it. Design B does not print a reason it does not have, and the
count line is a count, not a reason.

### 4.3 An episode with no analysis

Three distinguishable cases, all from stored fields, and the row and the page
must not merge them.

**Level 2.** `claim_count: 0` and `analysis_present: false`. The row is drawn,
never hidden, and the title is not a link to level 3. Instead the row carries,
in the meta line, the reason built from the state it is in:

| `transcription_state` | meta line tail |
|---|---|
| `transcribed` | `no analysis yet` |
| `queued` | `no transcript yet, so nothing to analyse` |
| `failed` | `transcription refused`, plus nothing further: the reason lives in `content_transcribe_skip` and no field on the row carries it |
| `none` | `nothing stored to read` |

The `failed` row is the one case where a reader can see that a reason exists and
cannot read it. `EPISODES_RESULT` has no field for the skip reason, and the
module docstring names the column (`content_transcribe_skip.reason`, values like
`no_captions`, `ConnectError`, `relevance:2`). Design B does not invent one and
does not print a guess. The honest ask is one nullable string on `_EPISODE`,
`transcription_reason`, populated from that column. Until it exists the row says
`transcription refused` and stops.

**Level 3, reached anyway** through a pasted URL or a stale bookmark.
`episode_summary` still answers: it serves the header, an empty `summary`, a
`summary_reason` and a `gaps` entry for `analysis` that names which of the two
cases it is in its own words:

> no row in content_analysis for this episode. Its transcription state is
> queued, so there is no speech on file to analyse.

The page prints the header, then `summary_reason`, then the players list if
`claim_count` is not zero (a cue extractor can produce claims with no stored
analysis), then `gaps`. Every one of the seven possible `gaps[].section` values
gets the same treatment: `analysis`, `summary`, `players`,
`transfers_suggested`, `transfers_suggested.paired_with`, `captain_view`,
`timestamps`. They render as a `What is not here` block at the foot, each a
`section` heading and the `gap` sentence, at reading size inside the measure.

### 4.4 Ages, in days

**Publication dates are days. Read times keep the shared span.**

Every `published_at` and `last_item_at` on every level renders through one local
helper that floors the difference to whole days and appends `d`:
`5d`, `6d`, `56d`. Under one day it reads `today`. Null reads `no items on
file` at level 1 and `no publication date stored` at levels 2 and 3. There is no
`3h 12m`, no `yesterday` and no `2mo ago` anywhere a publication date appears.

`as_of` and `analysed_at` are different facts. `as_of` answers how stale the
panel read is, and a panel read six hours old is different from one read six
minutes ago, so those two keep `relAge`, which wraps the shared `fmtAge` from
`app.js`. That keeps `tests/unit/test_web_contract.py:1160`
(`test_the_age_helper_is_the_shared_one`) green, which asserts the literal
`fmtAge(iso)` and the shared import both survive.

The boundary in one line: **a date on a thing the creator published is days, a
stamp on a thing this app did is the shared span.**

Measured today, every creator's newest publication is 5 to 56 days old, so the
days-only vocabulary loses nothing that is currently on screen.

### 4.5 Every empty and degraded state, per level

| level | condition | what is drawn |
|---|---|---|
| 1 | `creator_board` returns `{empty, reason}` | `emptyBox(reason)`, and the source strip still loads, because the strip's data comes from a different endpoint and is the thing that says why the corpus is empty |
| 1 | `creator_board` throws | `errBox`, with the body folded through the existing `failFold` |
| 1 | `creator_report_card` throws | the fold's summary reads `record unavailable: <status line>` and the body is `failFold(rcErr, ...)`. The 25 creator rows are unaffected, because they do not read the card |
| 1 | `GET /api/content/sources` throws | the strip prints the error; the rows are unaffected |
| 1 | a creator with `last_item_at: null` | 4 of 29 today. Age reads `no items on file`, counts read `0 on file` and `0 claims`, and the title slot carries `latest_reason` when it exists. All such rows sort to the bottom behind the fold `4 shows with nothing on file` |
| 1 | `scope.excluded` non-empty | 4 today. Named in the fold, with `scope.reason` |
| 1 | `mine_reason` non-null | printed in the fold as one sentence |
| 2 | creator name not tracked | `creator_episodes` returns `{empty, reason}` naming every creator on file. Printed verbatim, with the crumb still pointing back to level 1 |
| 2 | corpus tables missing | the panel's `_missing_corpus` sentence, which names the tables and the command that fills them |
| 2 | `counts.transcribed` is 0 | the list is empty with the default filter. The header says `73 on file, 0 transcribed, 67 analysed` and the empty box reads `no episode of this creator has a transcript on file`, with the `show every episode` control as the hint |
| 2 | `episodes.length < counts.transcribed` | the `limit` truncated it. The header says `showing the 30 newest of 35 transcribed` and the control offers 200 |
| 2 | a row with `source_url: null` | the `open` anchor is replaced by the text `no link stored`. The row still opens level 3 |
| 2 | a `failed` row | section 4.3 |
| 2 | a `queued` row | ring glyph, `no transcript yet, so nothing to analyse`, still clickable: `episode_summary` answers with a header and gaps |
| 2 | the record fold, still loading | `measuring the record…`, the app's one loading glyph and one capitalisation |
| 3 | `item_id` not in the warehouse | `{empty, reason}`: `no content_item ... is in this warehouse`. Printed with the crumb back to level 1, because the creator is unknown |
| 3 | `item_id` discarded from the corpus | the panel's own sentence, which says nothing was deleted and names the link ledger as the way back |
| 3 | `item_id` published after this instant | the panel's sentence naming the publication time |
| 3 | `summary_bullets` empty | `summary_reason` in its place, at reading size, and the `summary` gap in the foot block |
| 3 | `players` empty | the `players` gap sentence, and the call index line is omitted rather than printed as four zeroes |
| 3 | every `start_s` null | the count line from section 4.2, and the `timestamps` gap where the panel raises it |
| 3 | `analysis_model` null | `no analysis stored` in the header slot, and the `analysis` gap in the foot |
| 3 | `gameweek` null | `no gameweek` in the header and `gw_reason` in full underneath, which in this case is the sentence naming all three rungs that came up empty |
| 3 | a player with `resolved: false` | the heading is the spoken name and reads `as spoken, not resolved to a player`. `team` and `position` are null and their slots are absent, never `-` |
| 3 | a player with a `disambiguator` | the heading is the disambiguator, `C. Palmer (CHE)`, per the shared naming contract |
| 3 | a claim with `quote: null` | the evidence line prints and the quote block is replaced by `no quote was stored with this position` |

Staleness is one sentence per level, in the header, built from `as_of` through
`relAge`, plus the coverage note at level 1. There is no freshness dot at level
2 or 3: an episode is dated by its publication, which is already on the row.

### 4.6 The timestamp, and when it is a link

`deep_link` at `urls.py:70-87` returns a `watch?v=...&t=NNNs` URL only for
YouTube. For every other platform it returns the item URL unchanged, and the
docstring says why: a podcast URL here is an episode page or the enclosure
itself, and a `#t=` fragment on it lands at the top.

The rule on screen:

- `start_s` is null: nothing is printed in the clock slot.
- `start_s` is a number and `deep_link` differs from `episode.source_url`: the
  clock `mm:ss` is an anchor to `deep_link`.
- `start_s` is a number and `deep_link` equals `episode.source_url`: the clock
  is plain text, and the header's `open the source` anchor is the only link.

The third case is the podcast copy. Comparing two strings the payload served is
not a claim about the world, and the alternative, printing every clock as a link
that silently lands at zero, is.

---

## 5. What comes out of `creators.js`

Line ranges from a brace-matching pass over `web/dist/js/views/creators.js` at
3,880 lines, cross-checked against the section banners at 26, 121, 209, 212,
242, 284, 341, 372, 383, 443, 458, 558, 630, 1136, 1202, 1384, 1500, 1968, 1970,
2212, 2375, 2477, 2521, 2552, 2583, 3295 and 3397.

### 5.1 Deleted

| lines | count | what | why |
|---|---:|---|---|
| 96-105 | 10 | `LANES`, `laneLabel` | the owner's squad lane vocabulary. No level reads the owner's squad |
| 226-241 | 16 | the drawer element, its escape handler, `closeDrawer` | each level replaces the previous one. There is no drawer |
| 284-318 | 35 | `claimsIdx`, `freshState`, `loadRecency`, `freshFor`, `agoText`, `H48` | the 29 `creator_detail` calls: 3.12 MB and 103.0 s, section 1.2 |
| 372-441 | 70 | `laneOf`, `mine`, `squadReady`, `grp`, `buildRows`, `byWeightOfMouth`, `inWindow`, `wantH48`, `windowed`, `currentRows` | the consensus row model. The consensus is dropped, section 2.5 |
| 443-628 | 186 | `render`, `gwCandidates`, `probeGameweeks`, `selectGw`, `renderGwRow`, `renderFactRow`, `toggle` | the gameweek axis and the two window toggles: 7 `creator_board` runs, 24.6 s, and 91 px of toolbar at 1280 that becomes 298 px at 390 |
| 1968-2581 | 614 | `renderBoard`, `drawBoard`, `showTip`, `moveTip`, `boardLegend`, `renderMainTakes`, `decisionCard`, `countChip`, `crossLink`, `rcChip`, `rcTag`, `recordStrip`, `renderArmband`, `renderWatching`, `renderMatrix`, `redrawGrid` | the board chart, the main takes, the armband, the watch list and the matrix shell. All of them are player-level views of the consensus |
| 2583-2655 | 73 | `renderReportCards` | the card wall. Replaced by one card at level 2, section 8 |
| 2918-3294 | 377 | `renderGrid`, `gridRow` | the said-versus-owned matrix: 3,997 px at 1280 and 4,804 px at 390 when opened, 54 people by 4 players |
| 3295-3395 | 101 | `detailFor`, `loadSquads`, `panelSquadWord`, `quoteBlock` | `creator_detail` is called by no level; `quoteBlock` reads `claim.extractor` and `item.url_basis`, neither of which exists on an `episode_summary` evidence object |
| 3397-3739 | 343 | `POS_ORDER`, `drawerHead`, `openCreator`, `recordLine`, `transfersSection`, `methodFold`, `heldOnBoard`, `openPerson`, `byCreatorOf` | the creator drawer, 4,054 px of scroll with no episode in it, section 1.3. Level 2 is its replacement |
| 3741-3879 | 139 | `openPlayerByCode`, `openPlayer` | the player drawer. `components/playerdrawer.js` (331 lines) and `components/chatter.js` (600 lines) already serve this on the Dashboard, Projections and Template tabs |
| | **1,964** | | |

### 5.2 Moved, not deleted

| lines | count | what | where it goes |
|---|---:|---|---|
| 1136-1966 | 831 | `LINK_STAGES`, `renderLinkBar`, `failLine`, `stopJob`, `startJob`, `poll`, `errState`, `applyPoll`, `classifyError`, `expiryLeft`, `expiryText`, `startExpiryTicker`, `confirmExpiry`, `decide`, `abortJob`, `repaste`, `renderJobs`, `renderJob`, `againRow`, `renderPreview`, `pathLabel`, `secs`, `fmtWhen`, `renderLedger`, `renderTake`, `jobStateLabel` | a new `web/dist/js/components/addsource.js`, mounted by level 1 and available to the Pipelines tab |

Paste a link, preview, accept or decline, watch the stages, read the ledger: a
capability the owner uses and none of it is a reading surface. It is 21 percent
of the file serving a 136 px card. Moving it whole, with a ten-line mount, keeps
it working and takes it out of the reading view's way. Levels 2 and 3 do not
mount it.

### 5.3 Kept

1,085 lines survive:

| lines | count | what |
|---|---:|---|
| 1-95, 106-225 | 215 | the module header, `sv`, `parseTs`, `relAge`, `clock`, `plural`, `canonicalKey`, `tier`, `pct`, `signed`, `sleep`, `COIN`, `coin`, `statusLine`, `failFold`, `unscored`, `verdict`, `STATE_ORDER`, `HIDDEN_STATES` |
| 242-283, 319-371 | 95 | the shell, the state block, `skeleton`, the load sequence, `personName`, `personsOf` |
| 629-1135 | 507 | `loadSources`, `visibleSources`, `reloadSources`, `loadReportCard`, `renderTop`, `coverageLine`, `scopeLine`, `cardCensus`, `censusLine`, `honestyLine`, `recordSentence`, `mainShows`, `sourceStrip`, `sourceTable`, `actionCell`, `fetchSource`, `refusal`, `fetchMainShows`, `seqLine`, `runAnalyse`, `analyseLine` |
| 2656-2917 | 262 | `reportCard`, `rangeBar`, `gwBars`, `teamLine`, `baselineWord`, `openCard`, `reportBody` |
| misc | 6 | closing braces and banners between the deleted blocks |

`linkKind` at 68-78 dies with `quoteBlock`, its only caller. It is inside the
kept range and comes out as part of the same edit, 11 lines, counted in the 215
above as surviving and then removed; the arithmetic below treats it as kept, so
the target is a ceiling.

### 5.4 New code

| what | estimate |
|---|---:|
| level 1 renderer, one row builder and the two folds | 95 |
| level 2 renderer, header, row builder, the filter control | 135 |
| level 3 renderer, header, bullets, call index, player blocks, evidence, quote block, gaps | 200 |
| the router, the breadcrumb, hash parsing, scroll memory | 75 |
| the record fold wiring at level 2 | 25 |
| loaders and state for the two new panels | 45 |
| mounting `addsource.js` | 10 |
| | **585** |

### 5.5 The target

```
3,880  today
-1,964  deleted (section 5.1)
  -831  moved to components/addsource.js (section 5.2)
--------
 1,085  surviving
  +585  new (section 5.4)
--------
 1,670  target
```

**Target: 1,670 lines, 43 percent of today.** The two largest surviving blocks
are the source strip at 507 lines and the report card at 262, both of which the
owner uses and neither of which this brief asks to change.

`web/dist/creators.css` is 1,074 lines and defines **249** `cx-` classes. The
1,085 surviving JS lines emit **63** of them. **345 of the file's 483 rules lose
every emitter**, which is 71 percent of the stylesheet. Deleting them is
mechanical and is part of the same commit, because a rule with no emitter is the
thing that made `.cx-rc.few` a test assertion in the first place.

### 5.6 The contract tests, one by one

`tests/unit/test_web_contract.py` holds 60 tests and 14 of them read
`VIEWS["creators"]` or `CREATORS_CSS`, at lines 1049, 1060, 1073, 1081, 1093,
1106, 1115, 1124, 1132, 1143, 1151, 1160, 1172 and 1184. Ten survive untouched.
Four need an edit, and each edit is a number or a clause, never a deletion of
the rule.

**Survive:**

| line | test | why it holds |
|---|---|---|
| 1049 | no rendered string carries an em dash | a constraint on the new strings, not on the deletions |
| 1060 | `plural` refuses a null count | `plural` is at line 62, kept, and `n_total` is still only reached through `unscored` |
| 1081 | the team verdict is gated on `quotable` | `const one = p => p.quotable` is at 2743 inside `teamLine`, kept; `fact("beats the baseline", p.quotable` is at 2865 inside `reportBody`, kept |
| 1106 | one creator count is drawn | `cardCensus()` at 746 and `censusLine()` at 761 are kept. The literal `censusLine()} · floor` is at 2604 inside `renderReportCards`, which is deleted, **and moves verbatim into the level 1 honesty fold**, which is the only place a census now belongs. This is why level 1 still calls `creator_report_card` |
| 1115 | scope and note are rendered | `res.scope` at 719, `sc.excluded` at 724, `res.record_note` at 787, `res.mine_reason` at 720 and 736, all inside `scopeLine` and `honestyLine`, all kept. `{mine: false}` makes `mine_reason` always populated, which strengthens the test rather than breaking it |
| 1124 | the source button does not call a filtered list all of them | `sourceStrip` is kept whole |
| 1143 | the compact team line is capped at two people | `const SHOWN = 2;` at 2741 inside `teamLine`, kept |
| 1151 | a repeated FPL entry is named as a repeat | `cardsByEntry` at 261, 661, 666, 667 and 2842, all kept |
| 1160 | the age helper is the shared one | `fmtAge(iso)` at line 41, kept, section 4.4 |
| 1172 | a failed panel body is folded | `failFold(rcErr` at 772 inside `honestyLine`, kept |

**Need an edit, with the reason for each:**

| line | test | what breaks | the edit |
|---|---|---|---|
| 1073 | `test_an_unscored_record_falls_back_to_the_payloads_reason` asserts `src.count("unscored(") >= 5` | the five sites are 183 (the definition), 2396 (`rcChip`, deleted), 2628 (`renderReportCards`, deleted), 2685 (`reportCard`, kept), 3448 (`recordLine`, deleted). Two survive | `>= 2`. The rule the test encodes, every site goes through the helper rather than formatting its own count, is unchanged; only the number of sites changes |
| 1093 | `test_the_under_floor_class_is_actually_styled` asserts `.cx-rc.few`, `.cx-rctag.few` and `.cx-rcard.few` in the CSS | `cx-rc` is emitted only at 2390 in `rcChip` and `cx-rctag` only at 2417 in `rcTag` and 3446 in `recordLine`, all deleted. `.cx-rcard.few` at 2668 in `reportCard` survives | drop the two dead selectors from the assertion and delete their rules from the stylesheet in the same commit. The JS half of the test, `el("button", "cx-rcard " + v.cls`, is untouched |
| 1132 | `test_the_record_strip_draws_no_permanently_empty_group` asserts `"Interval below a coin flip" in src`, `"if (!list.length) return null;" in src` and `"of them over the floor" in src` | all three are inside `recordStrip` at 2426-2475, deleted. The two negative clauses, `"Laggards" not in src` and `"Record leaders" not in src`, are the part that encodes the rule | keep the two negative clauses, drop the three positive ones, and rename the test to say the strip is gone. A leaderboard of creators by record is exactly what design B refuses, so the rule is stronger after the edit, not weaker |
| 1184 | `test_the_loading_affordance_has_one_glyph_and_one_capitalisation` asserts `src.count("measuring the record…") >= 3` | the three stripped sites are 774 (`honestyLine`, kept), 2430 (`recordStrip`, deleted), 3441 (`recordLine`, deleted). One survives, and the level 2 record fold adds the second | `>= 2`. The three clauses that matter, no capitalised variant, no trailing three dots, one glyph, are untouched |

No test forbids a string design B introduces. The em-dash test at 1049 is the
one that constrains the new prose, and every string in sections 3 and 4 of this
document is written to pass it.

`tests/unit/test_creator_panel.py` and `tests/unit/test_creator_report_card.py`
test the panels, not the view, and design B changes no panel. The two new panels
arrive with their own tests in the worktree.

---

## 6. Type scale and spacing

### 6.1 What already exists in `app.css`

`web/dist/app.css` is 344 lines and defines 12 distinct font sizes across 27
`font-size` declarations and 4 `font:` shorthands. `web/dist/creators.css` holds
13 across 141 declarations. The union is **16 distinct sizes**: 9, 9.5, 10,
10.5, 11, 11.5, 12, 12.5, 13, 13.5, 14, 15, 15.5, 16, 17, 19. At runtime today
the folded page renders **14** of them inside `.content`.

The union of `padding`, `margin` and `gap` values across both files is **21
distinct** lengths, from -1.5 px to 40 px.

Design B uses seven of the sixteen sizes and adds none:

| role | value | where it already is |
|---|---|---|
| level 3 reading body and every quote | 15px | `app.css:74`, `.brand`, and six rules in `creators.css` |
| level 2 row title | 14px | `app.css:94`, `.deadline.urgent b` |
| level 1 row, level 3 player heading, page body | 13px | `body` in `app.css:58` |
| breadcrumb, header sub | 12px | `app.css:117`, `.card > .sub` |
| meta lines, counts, gap sentences | 11.5px | `creators.css` |
| provenance footer | 11px | `app.css:124`, `.provenance` |
| level 2 and 3 page heading | 17px | `app.css:276`, `.dhead .dname` |

Colours are the existing tokens with no additions: `--ink`, `--muted`,
`--faint`, `--line`, `--bad`, `--surface`, `--raised`, `--accent`. The four
series hues `--s1` to `--s4` are used by no level, because nothing on any level
encodes a direction as a hue any more.

### 6.2 New tokens

Eight, all scoped to the creators view:

```
--cr-read:    15px     /* level 3 body and every quote */
--cr-lead:    1.55     /* line height on --cr-read only */
--cr-measure: 504px    /* 65 lowercase characters at 15px, measured */
--cr-title:   14px     /* level 2 row title */
--cr-meta:    11.5px   /* every meta line, every count, every gap sentence */
--cr-crumb:   12px     /* the breadcrumb */
--cr-rowy:    7px      /* vertical padding inside a level 1 or level 2 row */
--cr-block:   20px     /* the gap between two blocks at level 3 */
```

**Seven of the eight are names for values the stylesheets already hold.** 15px,
14px, 11.5px and 12px are in the sixteen-value font union above. `line-height:
1.55` is already in `creators.css`, one of its five line heights, and it exists
because 1.45 at 15px is 21.75 px of leading, which is tight for a 65-character
measure. `20px` is already one of the 21 spacing values, used twice in
`creators.css` today. 7px is in both files.

**`--cr-measure: 504px` is the only new value in the design, and it is a
measurement rather than a choice.** The same commit removes 345 rules, so the
count of distinct values in the stylesheets moves down, not up.

`--cr-measure` was measured rather than guessed. A 60-character lowercase string
in `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
sans-serif` renders at:

| size | 60 characters | per character | 65 characters |
|---|---:|---:|---:|
| 14px | 438.1 px | 7.30 | 475 px |
| 15px | 464.9 px | 7.75 | **504 px** |
| 16px | 492.1 px | 8.20 | 533 px |

The rule is `max-width: min(var(--cr-measure), 100%)`, so at 390 the measure is
the 366 px content width, which is 47 characters, and there is no horizontal
scroll (measured, section 3.4).

### 6.3 Spacing, per level

| level | rule |
|---|---|
| 1 | row `padding: var(--cr-rowy) 0`, `border-bottom: 1px solid var(--line)`, columns separated by `gap: 10px`. No card, no zebra, no outer border |
| 2 | the same row rhythm at `--cr-rowy`, glyph column 16px, list `max-width: 760px` |
| 3 | `margin-bottom: var(--cr-block)` between the bullets, the call index, each player and the gaps block. Inside a player, `margin-bottom: 10px` between one stored position and the next; a quote is `padding-left: 10px` behind a 2px `--line` rule |

`--cr-rowy` at 7px gives a 34 px level 1 row at 13px body (measured) and a 54 px
level 2 row (measured). Both are above the 44 px touch target at level 2 and
below it at level 1, which is the trade a one-line directory makes; at 390 the
level 1 row wraps to 52 px and clears it.

---

## 7. Navigation

### 7.1 The hash

Three routes, all under the existing `#creators` registration:

```
#creators                                level 1
#creators/c/<encodeURIComponent(name)>   level 2
#creators/e/<item_id>                    level 3
```

`Let's Talk FPL` becomes `#creators/c/Let's%20Talk%20FPL`. One panel creator name
in the live corpus ends in a question mark, and `encodeURIComponent` turns it
into `%3F` so it can never be parsed as the start of a query string. The router
decodes with `decodeURIComponent` and passes the result to
`creator_episodes {creator}` unchanged, because that panel takes the name
verbatim and answers `no creator named ... is tracked` with the full list when
it does not match.

**The episode route carries no creator segment.** `episode_summary` takes only
`item_id` and serves `creator` back, so the URL is self-sufficient, reload-safe
and impossible to make internally inconsistent. A `#creators/c/X/e/Y` form could
carry a creator that disagrees with the payload, and there would be no rule for
which one wins.

### 7.2 Reload and deep links

On mount the view parses the hash once and renders that level directly. Level 3
from cold calls `episode_summary {item_id}` and nothing else: it does not load
level 2, does not load the board, and does not need either. Level 2 from cold
calls `creator_episodes {creator}` and nothing else. Each level is complete
without the levels above or below it, which is the hierarchy rule stated as a
loading rule.

The middle crumb at level 3 is built from `episode_summary.creator`, which
arrives with the payload, so the crumb is correct on a cold deep link with no
extra call. Until the payload lands the crumb reads `Creators > loading…` and
the title area holds a skeleton.

### 7.3 Back

`location.hash = ...` on every forward move, which pushes a history entry. The
browser back button therefore walks levels 3, 2, 1 in order, and so does the
back gesture on a phone.

A breadcrumb crumb is an anchor whose `href` is the shallower hash. Clicking it
pushes rather than pops, so `#creators/e/X` then crumb to `#creators/c/Y` then
back lands on the episode again. That is the behaviour of every breadcrumb on
the web and it is what a reader expects from a link.

One `hashchange` handler owns all three levels. It reads the hash, renders the
matching level, and is the same code path the initial mount uses, so a
back-navigated level 2 and a freshly loaded level 2 cannot differ.

### 7.4 Scroll

A `Map` from hash to `scrollY`, held in module state for the life of the visit.
Forward moves record the current position and then `scrollTo(0, 0)`. A
`hashchange` that lands on a hash the map holds restores it. Nothing is written
to `sessionStorage` or `localStorage`: a remembered scroll position across a
browser restart is worse than the top of the page, because the list it indexed
into has moved.

### 7.5 What never happens

No level opens a drawer. No level opens a modal. No click at level 2 lands on
another creator, and no click at level 3 lands on another episode. The only
sideways affordance on the whole surface is the `open the source` anchor, which
leaves the application entirely and says so with an external-link glyph and
`target="_blank" rel="noopener noreferrer"`.

---

## 8. The report card

The wall is gone and the card is not. The distinction matters because the card
answers a real question, "should I weight what this person says", and the wall
answered it 28 times at once in 2,101 px.

**Level 1 calls `creator_report_card {}` and prints no card.** The payload feeds
three things inside one fold, all of which exist today and all of which are
census rather than per-creator judgment:

- the honesty line from `record_note`, which today reads "No creator has beaten
  a coin flip yet. 21 of 22 measured creators have at least one scored claim and
  none has a 95% Wilson lower bound above 0.5 over 25+ claims, so every earned
  weight is 0.0. That is a measured result, not missing data."
- the census through the existing `cardCensus()` and `censusLine()`, which is
  the reason level 1 makes this call at all and the reason the test at line 1106
  stays green;
- `gaps[]`, which names what the measurement cannot reach.

The fold is closed on load. Folded it is one summary line; today's equivalent
block measures 33 px closed and 2,101 px open.

**Level 2 opens one card, lazily.** The header carries a collapsed line built
from `creator_episodes.record`, which is the same `_RECORD` object
`creator_board` serves through the same `_record(weights.get(creator))` call, so
it costs nothing:

```
> 46% of 390 scored claims hit, coin flip, weight 0.0 and unearned
```

Opening it calls `creator_report_card {creator}` once and renders the existing
`reportCard(c)` as the summary tile and `reportBody(c, host)` as the body: the
claims channel with its Wilson range through `rangeBar`, the by-gameweek bars
through `gwBars`, the team channel through `teamLine` with its two-person cap,
the numeric channel with its reason, the repeated-entry sentence through
`cardsByEntry`, and the headline. 262 lines of working, tested rendering with a
single call site instead of 28.

`CARD_PARAMS.creator` is documented as "A name means one card, which is what the
player drawer and a chat answer ask for", so this is the parameter's intended
use and not a filter applied in the browser.

**Level 3 shows no record at all.** An episode is a thing somebody said on a
date. Whether that person has beaten a coin flip over 390 claims is a fact about
the person, it is one click up, and putting it beside a quote invites the reader
to score a sentence with a number that was never about that sentence.

---

## 9. Risks, and what a reader loses

### 9.1 What the owner loses that the current tab gives them

**The consensus.** Today's board answers "who does the panel want in, and do I
own him" for every player named in the window, against the owner's own squad,
with `panel_owned.n` of `panel_owned.of` beside each name. Design B has no
player-level view. The owner reads the Creators tab before a deadline and that
is the question they are reading it for.

The mitigation is real and it is partial. `player_chatter` serves the same
question per player in one call, and `components/chatter.js` already mounts it
in the Projections and Dashboard player drawers. The owner reaches it from a
player, not from a creator. The loss is that they can no longer see all four
consensus players at once, which today is 643 px of main takes plus a 316 px
chart, and which measured **4 players and 9 takes** at the moment this was
written because the 48-hour window was empty and the fallback window is 30 days.

If this design is picked, the honest sequencing is to move the consensus to the
Projections tab or to its own tab in the same change, not to delete it and
promise it later.

**The said-versus-owned matrix.** 54 people by 4 players, hue for what they
said, a ring for what they own. It is the only surface in the application that
shows the gap between what a creator says and what they hold, and it was built
against an explicit owner question. It measures 3,997 px at 1280 and is folded
by default, and 377 lines of `renderGrid` go with it. Nothing in design B
replaces it. That is the single largest capability loss in this document.

**The armband and the watch list.** 127 px and a fold. `captain_view` at level 3
carries the same calls per episode, so the facts survive; the cross-creator roll
up does not.

**The quiet-holdings view.** `openPerson` answers "what does this person own and
never talk about", which needs `creator_detail.squad` joined against every claim
in the record. Design B calls neither. Today it renders as 91 players over
2,659 px in a 560 px drawer, which is a poor answer to a good question, and
design B's answer is no answer.

### 9.2 Risks in the design itself

**Level 2 shows the same recording twice and cannot say so.** 40 percent of Let's
Talk FPL's 40-item window is a second copy. Section 4.2 is the honest handling
and it is worse than merging for a reader who does not read the note. The
mitigation that would actually fix it is a payload change: a `siblings` array on
`_EPISODE` carrying the other publication ids that share a title and a
publication window, computed in the panel where the corpus is visible. Until
that exists, a reader scrolling level 2 will open the wrong copy some of the
time and find no timestamps.

**Level 3 is 2,626 px.** One episode with 17 players and 22 stored positions.
The real payload the brief describes carries 20 players, and a busy preview
episode will be longer. The six bullets are in the first 390 px, which is the
design's answer, and a reader who wants only the bullets never scrolls. A reader
who wants a specific player scrolls or uses the call index. There is no search
and no filter at level 3, deliberately, and that is a bet that will be wrong for
the longest episodes.

**Two claims about the same player render twice.** `_players` files a
`content_claim` row and a `PlayerCall` from the stored analysis under the same
player, and for Palmer in the measured episode the `buy` claim at 3:45 and the
`transfers_in` call at 3:45 carry the identical quote. Both are stored, the
panel is explicit that neither is converted into the other, and design B prints
both with `kind` and `stored_in` naming the channel. A reader will read it as
the creator saying the same thing twice. The alternative, collapsing on quote
equality, throws away the distinction between a numeric confidence and a
conviction word, which is the thing `_players` exists to preserve.

**The `failed` state has a reason nobody can read.** Section 4.3. A reader sees
that a transcription was refused and cannot find out why without a shell.

**A podcast copy's null offsets have no served explanation.** Section 4.2. The
`timestamps` gap does not fire when a transcript exists, and design B prints a
count rather than a reason. The count is true and the reason is missing.

**The four test edits are four chances to weaken a rule by accident.** Section
5.6 names each one and what part of the assertion must survive. The one to watch
is line 1132: the temptation on deleting `recordStrip` is to delete the whole
test, and the two negative clauses in it, `Laggards` and `Record leaders`, are
the rule that stops a creator leaderboard coming back.

**Moving 831 lines out is a second change riding in the same commit.** Section
5.2 keeps the code intact and only changes its home, but the paste-a-link flow
has expiry timers, polling and a decision gate, and it is the one part of this
surface that writes. If the move is not done in its own commit with its own
verification, a broken preview gate will be blamed on the redesign.

### 9.3 What would make me abandon this design

If the owner reads the Creators tab primarily before a deadline to decide a
transfer, the consensus is the page and the episode archive is the appendix, and
a design that deletes the first to build the second is the wrong trade. The
owner's words in this brief point the other way, at the archive. If both are
true, the answer is two tabs, not one design, and the honest version of design B
is the second of them.

---

## 10. Every number in this document, and where it came from

| number | source |
|---|---|
| 2,041 / 8,307 / 2,919 / 13,738 px page heights | `document.body.scrollHeight` at 1280x900 and 390x844 in the running app |
| the region table in 1.1 | `getBoundingClientRect().height` on `.card.cx` and `.cx-sec` children |
| 125 interactive, 1,507 nodes, 386 titles, 14 sizes, 2,733 chars, 127 lines | `querySelectorAll` and `innerText` inside `.content`, folded, at 1280 |
| 42 calls, 3.97 MB, 132,165 ms | `performance.getEntriesByType('resource')` after one clean load |
| 540,016 / 57,983 / 18,850 / 625 bytes | `new TextEncoder().encode(JSON.stringify(...)).length` on the live payloads |
| 560 px drawer, 4,054 px scroll, 2,659 px player list | `getBoundingClientRect()` on `aside.cx-drawer` after clicking the gutter name |
| 29 creators, 994 items, 2,433 claims, 4 with nothing on file | `POST /api/scripts/creator_board/run {}` |
| 40 items, 24 titles, 16 duplicated, the six gaps | `POST /api/scripts/creator_detail/run {"creator": "Let's Talk FPL"}` |
| 891 and 422 transcript segments | `duckdb(read_only=True)` over `transcript_segment` for the two item ids |
| 7 of 7 and 0 of 7 resolved offsets | the `analysis` objects on the two items in the same `creator_detail` payload |
| 957 / 1,709 / 2,626 and 1,432 / 2,311 / 2,944 px | the three levels built as real DOM in the running page with the real payloads, measured, then removed |
| 34 / 52 / 54 / 74 / 42 / 319 / 366 px rows and blocks | the same prototype |
| 504 px for 65 characters | a 60-character lowercase string measured at 14, 15 and 16px in the app's font stack |
| 3,880 lines, every line range in section 5 | a brace-matching pass over `web/dist/js/views/creators.js` |
| 1,074 CSS lines, 249 classes, 63 with a surviving emitter, 345 of 483 rules dead | a class-literal extraction from the surviving JS lines, intersected with the selectors in `creators.css` |
| 16 font sizes, 21 spacing values | regular-expression extraction over `app.css` and `creators.css` |
| 60 tests in the contract file, 14 on creators, at lines 1049 to 1184 | `grep -n "^def test_" tests/unit/test_web_contract.py` |
