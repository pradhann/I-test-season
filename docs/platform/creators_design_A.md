# Creators, redesigned — Proposal A: **decision-first**

Status: design proposal, 2026-08-27. No implementation code here.
Angle: the tab is a *transfer decision instrument*, not a content directory.
Companion proposals B and C work the same brief from other angles.

Everything factual below was read out of `data/warehouse/fpl.duckdb`
(read-only copy), `data/panels/creator_panel_2026_27.yaml`,
`fpl_edge/platform/scripts/creators.py` and
`docs/platform/CREATOR_PANEL_CONTRACT.md` on 2026-08-27. Numbers that are
counts are counts. Where the warehouse contradicts the brief, the warehouse
wins and I say so.

---

## 0. The verdict this replaces

The current page (`web/dist/js/views/creators.js`, 1,228 lines +
`web/dist/creators.css`, 407 lines) is a well-built **directory of a corpus**.
It has three top-level lenses — *The wire*, *Agreement*, *Track record* — a
four-chip time window, medium chips, a creator search box, six ingestion stat
tiles, and one row per creator sorted by recency.

It is honest, careful work. Its extractor distinction (`llm:` vs `cue`), its
verbatim quotes, its deep links and its refusal to render a blank where a
reason exists are the best things on the page and **all of them survive into
this design.** What does not survive is the *organising principle*. The page
answers "what is in the corpus". The owner is standing 18 hours from the GW2
deadline (`dim_event`: 2026-08-28 10:30 local) with 15 picks on file and one
free transfer, and the question he actually has is:

> **What is the panel doing before the deadline, and where does that differ
> from what I have?**

Nothing on the current page answers that. It cannot even be assembled from the
page, because the page never once mentions his squad.

---

## 1. The one sentence

Printed at the top of the page, in the position where xPoints prints "Numbers
are copied from ingested providers, never modelled here" and Template prints
its rank-move identity:

> **No creator here has earned a weight, so this is not a forecast — it is the
> field's intent. The only rows that matter are the ones where their intent
> and your squad disagree.**

And under it, the governing expression, in the Template idiom:

```
    decision(player)  =  panel intent          ✕   your exposure
                         (buy − sell, deduped,     (captain / start /
                          split by evidence tier)    bench / not owned)

    agreement  →  nothing to do, and it is collapsed to a count
    mismatch   →  a decision, and it is a row with a quote and a timestamp
```

Two consequences shape every later choice:

1. **Agreement is compressed, disagreement is expanded.** Seven people saying
   buy Haaland is the least informative row on the page — you already knew, and
   xPoints/Template already price it. Four saying buy and three saying sell is
   the row that pays. The current page sorts the other way round (`net` desc).
2. **The panel's weight is 0.0 and will stay 0.0 for a while**
   (`creator_score`, scope `all`, latest `as_of`: 810 claims scored, 280 hits,
   34.6%, every `weight` = 0.0, best Wilson lower bound 0.333 — *below* chance
   in aggregate). So the page must never present the panel as an oracle. It
   presents it as **the field's pre-deadline behaviour**, which moves price,
   ownership and therefore rank whether or not it is correct. That reframing is
   what makes a zero-weight signal legitimately actionable.

---

## 2. What the data actually is (verified, with corrections to the brief)

| Claim in the brief | What the warehouse says |
|---|---|
| 16 people across 8 shows | **16 rows, 7 distinct shows.** Solio Analytics 5, The FPL Wire 4, Fantasy Football Hub 4, FPL BlackBox 2, FPL Harry 1, FPL Raptor 1, Let's Talk FPL 1. |
| each with a verified FPL entry id | **15 of 16.** The 16th is the `Solio Analytics` *brand* row, deliberately `entry_id: null` with a written reason (a four-founder company, no house team). 10 `conclusive`, 5 `high`. |
| tables `panel_person`, `panel_person_show`, `item_person` | **All three exist and all three are EMPTY (0 rows).** The YAML has never been loaded. |
| `creator_entry` feeds `entry`/`entry_reason` | 29 rows, **every `entry_id` NULL, every `verified` false**, all with reason "no FPL entry whose API-reported name equals this creator name". |
| `content_item` 594 items, all real http urls | True — 594/594 `https`. 546 in the last 30 days. |
| `content_item_asset` carries the audio enclosure | 387 rows. **353 are `url_basis='enclosure'` and in all 353 `content_item.url == enclosure_url`** — i.e. the item URL *is* an `.mp3` (e.g. `pscrb.fm/rss/p/traffic.megaphone.fm/COMG3848299641.mp3`). 207 items have **no asset row at all**. |
| `transcript_segment` growing | 5,140 segments over **8 items**. 6 have `transcript_provenance` (3 `asr`/mlx-whisper, 3 `captions`/innertube); 2 more are user-pasted links. |
| `content_claim` cue vs llm | 487 claims: **241 `cue`, 246 `llm:claude-opus-5`**. No null `player_code`. `llm` mean confidence 0.61, `cue` 0.47. |
| `content_analysis` structured take | 120 rows. **119 carry `evidence`; of those, 94 are `depth: notes, thin: true`**, 16 `article`, 9 `transcript`. |
| every earned weight is 0.0 | Confirmed, and it is worse than the brief implies — the aggregate hit rate is 34.6%, below a coin flip. |

Three findings that change the design and are not in the brief:

**(a) `user-shared` is a fake creator polluting every aggregate.** It is the
top claim producer in the last 14 days (148 claims, 103 of them `llm`) and sits
at the top of the track-record table (85 claims, 52 scored). It is not a
creator. It is five links the owner pasted, four of which are Andy / Let's Talk
FPL videos — and two of those are *the same video ingested twice*
(`link_04dfb94e32cf04ca` and `link_280d525f5fb46a24`, both
`youtube.com/watch?v=-G6t3PtT4S0`). Any paste-a-link design that does not fix
attribution at paste time makes this worse every time it is used.

**(b) The one creator the owner named for insights is not registered.**
`Solio Analytics` appears in `fpl_edge/ingest/content/sources.py` as
`yt_solioanalytics` and on the panel roster with 5 named people — and has
**zero rows in `content_source` and zero `content_item`s.** The source registry
in code has drifted ahead of the warehouse. Seven other registered sources
(The Athletic FPL, FPL Review, Always Cheating, FPL JUiCE, Fantasy Football
Scout's YouTube, r/FantasyPL, X) probe 200 and yield nothing.

**(c) `creator_score` has per-action scopes and the panel throws them away.**
Scopes present: `all`, `buy`, `sell`, `captain`, `bench`, `avoid`, `hold` —
330 rows across 16 creators and 10 `as_of` snapshots. `creators.py:379` reads
`WHERE scope = 'all'` only. The discarded structure is real: scope `sell` is
35 hits / 60 scored (58%), scope `all` is 280/810 (35%). "This person is a
better seller than buyer" is a sentence the warehouse can already support and
the UI has never shown.

---

## 3. Wireframe

Target: a 1440×900 laptop. **Above the fold** = the deadline strip, the board,
and the first two decision cards. Everything below the fold is evidence and
administration.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  Creators — the panel's deadline                                             │  ← h2
│  No creator here has earned a weight, so this is not a forecast — it is the   │  ← .sub
│  field's intent. The only rows that matter are the ones where their intent    │
│  and your squad disagree.                              [ how weight works ▾ ] │
├──────────────────────────────────────────────────────────────────────────────┤
│ Deadline   ● GW2 · 17h 42m   Heard from  11 of 15   Read  9 transcripts,      │  ← ONE strip.
│                              Silent 4 ⓘ             94 items notes-only ⓘ     │    replaces 6 tiles
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   THE DEADLINE BOARD          ← panel says OUT      panel says IN →           │
│                          −5   −4   −3   −2   −1    0   +1  +2  +3  +4  +5     │
│                          ─────────────────────────┼─────────────────────────  │
│  You captain (1)   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │
│                              ● Haaland ────────────────────────────► ●        │
│                                                   │              (+4)         │
│  You start  (10)   ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓  │
│           ◄─ ● Groß (−3)                          │  ● ● ● ●  ●    ● Bruno    │
│                                                   │  (7 agreed, unlabelled)   │
│  You bench  (4)    ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│░░░░░░░░░░░░░░░░░░░░░░░░░  │
│                                                   │        ● Schade (+2)      │
│  Not owned  (23)   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓│░░░░░░░░░░░░░░░░░░░░░░░░░  │
│              ● ● (13 agreed)                      │      ● Mbeumo (+3)   ● JP │
│                                                   │                      (+5) │
│                                                   │                           │
│   ▓ shaded = you and the panel already agree, nothing to do (20 players)      │
│   ● solid = considered take   ◌ hollow = keyword match only                   │
│   Your lane is your GW1 XI, read live from my-team at 09:14 ⓘ                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  YOUR DECISIONS  (4)                                    sorted by gap size    │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ ▲ BUY   João Pedro  FWD BHA £7.6  12.4% own   xPts GW2 5.1 ↗           │  │
│  │ 5 of 11 panellists · 5 considered, 0 keyword · nobody said sell        │  │
│  │                                                                        │  │
│  │ “I would lean towards probably starting with him, cuz I think you'll   │  │
│  │  want him sooner rather than later”                                    │  │
│  │   — Andy · Let's Talk FPL · full transcript · ▶ 21:14                   │  │
│  │                                                                        │  │
│  │ Also: FPL Harry (high) ▶ 08:02 · Zophar (medium) ▶ 14:55 · +2          │  │
│  │ ⚠ Dissent — none recorded                                              │  │
│  │ [ see him in xPoints ]  [ see him in Template ]  [ all 5 quotes ▾ ]    │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────────────────┐  │
│  │ ▼ SELL  Pascal Groß  MID BHA £5.5  you start him                       │  │
│  │ 3 say sell / 1 says hold — SPLIT                                       │  │
│  │  ... quote, dissenting quote, links ...                                │  │
│  └────────────────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────────────────┤
│  THE ARMBAND                                            8 panellists named C  │
│   Haaland   ████████████████████  5   ← you  · 68.1% own · 121% EO           │
│   Bruno     ████████             2      "If it was at home, 100% I'd be on   │
│                                          Fernandes" — Andy ▶ 32:10           │
│   Semenyo   ████                 1                                            │
│   ⓘ Captaincy is a one-of-N choice, not a buy/sell. Counted, never coloured. │
├──────────────────────────────────────────────────────────────────────────────┤
│  WATCHLIST — what they noticed                          14 observations       │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐         │
│  │ ROLE  MCI    │ │ MINUTES BHA  │ │ SET-PIECE BHA│ │ FITNESS MUN  │         │
│  │ Savinho on   │ │ Doku out ~3wk│ │ Promise David│ │ De Ligt back │         │
│  │ the right    │ │ Foden bump   │ │ may take pens│ │ in training  │         │
│  │ while Doku   │ │              │ │ off Groß     │ │              │         │
│  │ is out       │ │              │ │              │ │              │         │
│  │ 2 sources ▶  │ │ 1 source  ▶  │ │ 3 sources ▶  │ │ 1 source  ▶  │         │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘         │
│  ⓘ Not buy/sell calls. Not scored, never weighted. Context for xPoints.      │
├──────────────────────────────────────────────────────────────────────────────┤
│  ADD A SOURCE                                                                 │
│  [ paste a YouTube or podcast link…………………………………… ]  [ Add ]                  │
│  ⓘ ~2 min for a 20-minute video, measured at 12× realtime. It keeps          │
│    running if you navigate away.                                             │
├──────────────────────────────────────────────────────────────────────────────┤
│  THE PANEL (15 people · 7 shows)                                    [ open ▾ ]│  ← collapsed
│    ...roster, verified entry ids, per-action track record, source health...   │
├──────────────────────────────────────────────────────────────────────────────┤
│  provenance: repo sha · as_of · panel creator_decision v1                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### Section by section

**1. Title + governing sentence.** Fixed prose, always shown, never data-driven.
Plus one disclosure toggle, *how weight works*, which expands the honest
paragraph the current page dedicates a whole lens to.

**2. The deadline strip.** One row, four facts, all decision-relevant:
- **Which gameweek and how long left** (`dim_event.deadline_utc`, live
  countdown). This is the page's clock and everything else is scoped to it.
- **Heard from N of M panellists** since the last deadline. Not "creators
  tracked" — *heard from*, which is the only number that affects whether the
  board is trustworthy tonight. The silent ones are named on hover, because a
  silent Zophar 17 hours before a deadline is itself information.
- **What was actually read**: transcripts vs notes-only. Today that reads
  "9 transcripts, 94 items notes-only" and that ratio is the single most
  important caveat on the page. It goes at the top, not in a tooltip.

**3. The Deadline Board.** See §4.

**4. Your decisions.** The board's off-diagonal, unrolled into cards, sorted by
gap size. Each card is one player and one verb. Contents, in order:
- The verb (`BUY` / `SELL` / `BENCH` / `HOLD, they disagree`), the player
  identity line, and — critically — **`xPts GW2` joined in from
  `projection_table`.** This is the cross-tab hook in the direction the owner
  described ("along with xPoints to find transfers"). A creator saying buy a
  player xPoints prices at 2.1 is a different card from one it prices at 5.1.
- The count line, always split by evidence tier: `5 considered, 0 keyword`.
- **One quote, verbatim, large, with attribution and a timestamped link.** Not
  a summary. The quote is the unit of evidence.
- The corroboration line: everyone else who said it, with conviction and their
  own timestamps, one line.
- **The dissent line, always present.** If nobody disagreed, it says
  "none recorded" — an explicit absence, because "nobody pushed back" is a real
  finding and a blank looks like a bug.
- Two outbound buttons into xPoints and Template, deep-linked to that player.

**5. The armband.** Separate from the board because captaincy is a different
question — a one-of-N choice where *ownership share* enters the arithmetic
(`docs/platform/rank_objectives.md`: captaincy score = μ + θ(1−2·share)σ²).
So the armband strip shows EO beside each count, and the currently-captained
player is marked. Bars, one hue, counts as text — never a colour-only encoding,
matching the current page's own (correct) rule.

**6. Watchlist.** The new grain — §5.

**7. Add a source.** Persistent, not a modal — §7.

**8. The panel.** Collapsed. This is the *entire current page*, demoted to an
accordion: the roster of 15 verified people and 7 shows, their entry ids and
evidence, the per-action track-record table, source probe health, and the
notes-only tray. It is reference material. It is opened once a month.

---

## 4. The central visual: the Deadline Board

**One axis, four lanes, one shaded region.**

- **X axis: panel intent.** Units are *panellists*, deduplicated to one opinion
  per (person, player, action, gameweek) — the existing
  `ingest/content/consensus.deduplicate` already does this and the panel
  already calls it. Left is net sell/avoid, right is net buy. Zero is the
  centre, marked.
- **Y: four categorical lanes** — `You captain`, `You start`, `You bench`,
  `Not owned` — from `squad_overview`.
- **The shaded wedge is the "no action" region**: the right half of the
  captain/start lanes and the left half of the not-owned lane. Being in the
  shade means you and the panel already agree.
- **Marks**: solid = a considered (`llm:`) take, hollow = keyword-only (`cue`).
  Labels are drawn **only outside the shade**. Inside the shade, dots are
  dimmed and unlabelled and the lane carries a count: "7 agreed".

### Why this and not a table

A table of `player | buy_n | sell_n | net | you own?` contains identical
information and is worse at the job in three specific ways:

1. **It makes "nothing to do" cost as much attention as "do something".** In a
   table, the 20 agreed players occupy 20 rows of equal visual weight. On the
   board they occupy shaded background and one number. That *is* the
   decluttering the owner asked for, done structurally rather than by hiding
   columns.
2. **It cannot show a mismatch as a shape.** The alarm state — a player in the
   `You captain` lane with a left-of-zero mark — is a single glance on the
   board. In a table it is a conjunction of two columns the reader has to
   perform. This is the same argument Template makes for its y=x line: the
   answer becomes *spatial* rather than *arithmetic*.
3. **It cannot show the panel's centre of gravity.** Whether tonight's panel is
   broadly buying or broadly selling is visible as the distribution's lean
   across all four lanes. A table has no such gestalt, and that lean is the
   thing that predicts price rises overnight.

### Doctrine compliance

Template's page states the rule I am bound by: *"Like is only ever compared
with like… the page will refuse to plot one against the other."* Panel intent
(count of people) and my exposure (a role) are **not the same measure**, so
they do not share an axis. Exposure is categorical and is encoded
categorically, as lanes. The x axis carries one measure with one unit and one
baseline. This is deliberately *not* a 2-D scatter.

### Degenerate cases

- **Fewer than 4 decision players**: the board collapses to a single labelled
  strip; the lanes stay as row labels with counts. It never renders an empty
  chart.
- **No squad** (`squad_overview` empty): lanes collapse to one lane, `Everyone`,
  and the strip says so. The board still works; it just cannot personalise.
- **Every player agreed**: the board draws entirely inside the shade and the
  headline reads "The panel has nothing you don't already have." That is a
  legitimate, useful, and *correct-looking* answer.

---

## 5. Insights → the **Watchlist** grain

The owner: *"some like SolioAnalytics looks at game and gives insights like
which player to watch, who's playing where — these key information should also
be layered somewhere."*

**Today this is discarded structurally but present textually.** The
`analysis_json` schema has exactly six keys — `summary`, `transfers_in`,
`transfers_out`, `captaincy`, `chip_advice`, `differentials`, plus `evidence`.
There is no home for a non-action observation, so the model dumps them into
`summary` bullets. From a real stored row:

> *"Doku is expected to miss GW1 with a calf injury and possibly several weeks;
> the knock-on is slightly better minutes for Savinho and a small bump for
> Foden."*

That is three distinct, structured, cross-referable facts (a fitness fact about
Doku, a role fact about Savinho, a minutes fact about Foden) trapped in a
sentence that renders as a grey blockquote and joins to nothing.

### The design

Add a third grain beside `content_claim` (an action) and `content_analysis`
(a take): **`content_observation`** — a *stated fact about the game* with a
subject and no action.

```
content_observation
  obs_id          text  pk
  item_id         text  → content_item
  creator         text
  person_key      text  nullable → panel_person
  kind            text  role | minutes | set_piece | tactical
                        | fitness | fixture | price
  subject_code    int   nullable  → player code
  subject_team    int   nullable  → team code
                        (CHECK: at least one of the two is non-null)
  statement       text  ≤ 140 chars, model-written, one fact
  horizon_gw      int   nullable  (null = ongoing)
  quote           text  verbatim
  start_s         real  nullable
  extractor       text  llm:<model>   (cue extraction is NOT valid here)
  published_at    timestamptz
```

Hard rules, each of which exists to stop a specific lie:

- **An observation is never scoreable and never weighted.** It does not enter
  `creator_score`, it does not enter consensus counts, it does not enter the
  board. "Savinho plays right" is not a claim that can be right or wrong in the
  hit/miss sense the scorer uses, and admitting it would corrupt a track record
  that is already the most fragile number in the system.
- **Observations are only extracted from `evidence.depth in (transcript,
  article)`.** 94 of 120 current analyses are `thin: true` show notes; a
  "tactical insight" mined from marketing copy is a hallucination with a
  citation. The thin tray gets zero observations and says why.
- **`cue` may not produce observations.** A keyword window cannot establish a
  fact. Only `llm:` extraction, and always with the verbatim quote.
- **Corroboration is counted, not merged.** Three people saying Promise David
  takes Brighton's penalties is `corroboration_n: 3` with three retrievable
  quotes, never one blended statement.

### How it reads

A card grid grouped by `subject_team`, ordered by
`corroboration_n desc, published_at desc`, filtered to teams that appear in the
upcoming fixture set. Each card: kind badge, team, the ≤140-char statement,
source count, and a `▶` that opens the strongest quote at its timestamp. Kind
badges are typographic, not coloured — the page's colour budget is already
spent on buy/sell and adding a seventh hue would break the validated palette.

Crucially the **same observations render inside the player drawer in xPoints
and Template** (§6), which is the "layered somewhere" the owner asked for. The
Creators tab is where you browse them; the other tabs are where you meet them
at the moment they matter.

### Backfill

The observations are already sitting in 120 stored `summary` arrays. A one-off
re-analysis pass over items with `depth in (transcript, article)` — 25 items —
extracts them at negligible cost. Going forward the analyzer emits
`observations[]` natively. **And re-seed `content_source`**: Solio Analytics,
the show the owner named for exactly this, is in `sources.py` and absent from
the warehouse. This grain is half-pointless until that source ingests.

---

## 6. Cross-tab reach

The owner: *"Every data must connect with everything."* Today the connection
count is zero: `creators.js` imports nothing from `xpoints.js`, and neither
`xpoints.js` nor `template.js` mentions creators.

### One panel, one component, three mount points

**New panel script: `player_chatter`.** Params `{code, gw?, days?}`. Small,
single-player, cheap enough to call on drawer-open. Returns intent counts split
by extractor, up to five quotes (considered first, each with `deep_link` and
`evidence_depth`), the observations for that player *and for his team*, and —
when there is nothing — a written `silence_reason`.

**New shared component: `web/dist/js/components/chatter.js`**, exporting two
functions with no page-specific assumptions:

- `chatterStrip(code)` → a single inline line, for use in a table cell or a
  drawer header:
  `▲3 in · ▼1 out · ★2 C · 2 notes`, with `cue`-only counts rendered hollow.
  Clicking expands in place.
- `chatterSection(code)` → the full block: quotes with attribution, conviction,
  timestamped links, and the team/player observations.

**Mount points, concretely:**

| Where | What | Why there |
|---|---|---|
| `xpoints.js` → `showDetail(r)` drawer, appended after the per-source matrix and before the `p(appear)` line | `chatterSection(r.code)` | You are already staring at six providers' numbers for one player. "Andy is buying him at 21:14" is the exact missing column, and it is *qualitative* — it belongs below the numbers, not in them. |
| `xpoints.js` matrix, one new optional column `chatter` (off by default, a toolbar chip `show creator chatter`) | `chatterStrip(code)` | Lets the owner sort/scan 100 projected players for "who is being talked about", which is a price-rise proxy. Off by default so the matrix stays clean. |
| `template.js` → `showDetail(r)` drawer, under "Your position on him" | `chatterSection(r.code)` | Template's question is "is the field going to own him". Creator chatter is *leading* ownership; EO is *trailing* it. Putting them adjacent is the whole point. |

**And the reverse direction**, which matters as much: every decision card and
board mark on Creators carries `[ see him in xPoints ]` / `[ see him in
Template ]`, routing to `#/xpoints?code=<code>` and `#/template?code=<code>`
with the drawer pre-opened. The loop closes both ways or it is not a loop.

**On caching**: the obvious move is a denormalised `player_chatter_gw` table
refreshed by the consensus step. **Do not build it yet.** This repo's
characteristic bug is "a table written nightly and never read"
(`docs/platform/PANEL_LEDGER.md`), and the live join over 487 claims is
trivially fast. Add the table only when a measured drawer-open latency demands
it, and delete it if the panel stops reading it.

---

## 7. Paste-a-link

Partially built, entirely un-surfaced. `content_item` already holds five
`user_link` items with `link_<hash>` ids; four have transcripts; one is a
failure case; `pipeline.py` has `cmd_transcribe` and `cmd_analyze`. What is
missing is an HTTP entry point (the platform exposes
`/api/scripts/{name}/run` and `/api/query` and nothing that writes content),
a job model, and a UI.

### The control

A persistent input in the *Add a source* section — **not a modal.** A modal
implies a short synchronous action; this is a two-minute background job and the
control should look like one.

### The waiting state: a stage ledger, not a spinner

On submit, a row inserts at the **top of Your decisions**, above the fold,
and stays there until it resolves. It shows five named stages:

```
┌────────────────────────────────────────────────────────────────────┐
│ ADDING  youtube.com/watch?v=EU4sZAAL7w8                    [ ✕ ]   │
│                                                                    │
│  ✓ resolve      video EU4sZAAL7w8 · not already held · 0.4s        │
│  ✓ fetch        no captions published → audio, 34.2 MB · 6.1s      │
│  ◐ transcribe   ████████████░░░░░░░  680s of 1,214s                │
│                 mlx-whisper large-v3-turbo · ~48s left             │
│                 (measured 12× realtime over the last 3 runs)       │
│  ○ analyse      waiting                                            │
│  ○ attribute    waiting                                            │
│                                                                    │
│  This keeps running if you leave the page.                         │
└────────────────────────────────────────────────────────────────────┘
```

Three things make this honest rather than decorative:

- **The progress bar is real.** `transcript_provenance` stores
  `audio_seconds` and `covered_seconds`; the bar is their ratio, not a timer.
- **The ETA is measured, not guessed, and says so.** Stored runs give
  1214.1s audio in 104.5s wall and 1396.7s in 121.2s — ≈11.6× realtime. The
  page states the multiplier and the sample size it came from. Captions, when
  they exist, take ~4-6s and the stage says "captions" so the user knows why it
  was instant.
- **It survives navigation.** `POST /api/content/link` returns a `job_id`
  immediately; the row polls `GET /api/content/link/{job_id}`. A two-minute job
  that dies on a route change is a two-minute job you will never run twice.

### Attribution: the step that fixes bug (a)

Between `analyse` and done sits **`attribute`**, and it is not optional. The
resolver matches the channel id / feed host against `content_source` and
`panel_person_show`. Three outcomes:

- **Matched to a registered show** → attributed, enters the board normally.
- **Matched to a show with one known host** (`panel_person_show` role
  `sole_host`) → attributed to the person, `item_person.basis = 'sole_host'`.
- **No match** → the row asks, once, with a `<select>` seeded with the 15
  panellists plus *"not a panellist — keep it out of the board"*. Choosing a
  person writes `item_person` with `basis = 'manual'`. Declining files it under
  an **Unattributed tray** where it is fully readable, fully quoted, and
  **excluded from the board, the consensus and `creator_score`**.

This retires the `user-shared` pseudo-creator. Its five existing items get
back-attributed as part of the migration (four are Let's Talk FPL, one is the
league-invite junk that should be deleted).

### Failure states, each with its own sentence and its own next action

| Condition | Detected by | What it says | Next action offered |
|---|---|---|---|
| Not a media URL | `too_thin` skip; **real case** — `link_5080f43ab77d47d6` is a `leagues/auto-join` URL with **3 substantive chars** | "That link has 3 characters of text behind it — it's an FPL league invite, not an episode." | Paste a different link |
| Duplicate | canonical YouTube id already in `content_item`; **real case** — the same Andy video is stored twice today | "Already held, ingested 3 days ago. Opened it below." | Jumps to the existing take; offers *re-transcribe* only if the held copy has no transcript |
| No captions and no audio | no `content_item_asset` enclosure and no innertube captions (207 items today have no asset row) | "No captions published and no audio file behind this page. If it's a podcast, paste the episode's YouTube link instead." | — |
| Partial transcription | `covered_seconds << audio_seconds` | "Transcribed 680s of a 1,214s file. Everything below comes from the first 11 minutes." | *Resume* — and the take renders, flagged, never silently as complete |
| Analysed, no positions | `content_analysis_skip.reason = 'no_positions'` | "Read in full; he named no players to buy or sell." | Shows observations only — often the *point* for a Solio-style video |
| Model error / timeout | analyse stage throws | "Transcript saved. The summariser failed: `<verbatim error>`." | *Retry analysis* — the transcript is the expensive artifact and is never discarded |
| Private / age-gated / 403 | fetch stage HTTP | "YouTube returned 403 for this video — it's private or age-gated." | — |

Two rules across all of them: **the transcript is never thrown away because a
later stage failed**, and **a partial result is always shown with its
boundary stated**, never padded to look whole.

---

## 8. Every state

| State | Data condition | What renders |
|---|---|---|
| **Nothing tracked** | `content_source` empty or `content_item` empty | The strip and board are replaced by one panel: *"No source is registered. The registry lives in `fpl_edge/ingest/content/sources.py` and is loaded by the pipeline's `probe` step."* The paste-a-link box stays live — one pasted URL is a working page. |
| **Nothing said this week** | items exist; zero claims/analyses with `gameweek = next_gw` | The board is not drawn. *"11 panellists published since the GW1 deadline and none of them has named a player for GW2 yet. The GW2 previews land in the 24 hours before Friday."* Below it: the last gameweek's board, clearly stamped **GW1 — settled**, plus a *notify me* hook into the existing outbox. This is a likely real state: GW3 has 28 `llm` claims total, GW5 has 1. |
| **No transcript** (dominant: 8 of 594 items) | `evidence.depth = 'notes'`, `thin: true` | The item **never reaches the board or a decision card.** It is counted once in the deadline strip ("94 items notes-only") and lives in the Panel accordion's tray, fully readable, badged *show notes only*. The current page's mistake is putting a reason-box on every row until the page is mostly apologies; here the absence is aggregated into one number, stated once, at the top. |
| **No verified team** | `creator_entry.entry_id` null (all 29 today) / `panel_person` empty | The panel row shows the person's name and the written reason, verbatim. Their takes **still count on the board** — an unverified entry id affects nothing about what they said. If `panel_person` is empty entirely, the strip degrades from "11 of 15 panellists" to "11 shows heard from" and says *"the panel roster in `data/panels/creator_panel_2026_27.yaml` has not been loaded; showing shows, not people."* |
| **No measured record** (permanent, today) | every `weight` = 0.0 | Not a lens, not a table, not a leaderboard. **One sentence under the title**: *"Measured over 810 scored calls, the panel's aggregate hit rate is 34.6% — below chance. No creator's Wilson 95% lower bound clears 0.50 at n ≥ 25, so every earned weight is 0.0. This page is the field's intent, not a signal."* The full per-creator and per-action table lives in the Panel accordion. A measured 34.6% is a *finding*, not a gap, and stating it once with a number is more honest than a screen of zeros. |
| **Panel unavailable** | script unregistered / HTTP 4xx-5xx | Keep the current page's `panelSafe` behaviour verbatim — it is genuinely good. Distinguish *not deployed* (a deployment state, explained in prose) from *errored* (a red box with the message). Add: the paste-a-link box stays enabled and queues, because ingestion and the read path are different services. |
| **Squad unknown** | `squad_overview` empty | Lanes collapse to one, labelled `Everyone`. The strip says *"Your squad could not be read, so the board is not personalised — it shows what the panel is doing, not what it means for you."* **This will happen**: `fact_manager_pick` holds only GW1 for entry 4490171 (15 rows). |
| **Squad stale** | picks are from an earlier GW than `next_gw` | Lanes are drawn from the last known XI and the lane header says which: *"Your lane is your GW1 XI"*. Never silently present last week's team as this week's. |
| **Board fully agreed** | zero off-shade players | *"The panel has nothing you don't already have."* Board still drawn, entirely shaded. Decisions section shows the sentence, not an empty box. |
| **A source is silent** | registered, `last_http_status = 200`, zero items (7 sources today) | Named in the strip's *Silent 4* tooltip and listed in the Panel accordion with its probe status. Never dropped — a source answering 200 with nothing is a live failure and hiding it is how the silent-failure bug happens. |

---

## 9. Panel contract additions, field by field

### 9.1 New script: `creator_decision`

The board's payload. A **new script rather than an extension of
`creator_board`**, because the grain is different: `creator_board` is
one-row-per-creator, this is one-row-per-player-decision. `creator_board`
survives unchanged as the feed for the Panel accordion.

Params: `{ gw: int|null, as_of: iso|null, window: "since_last_deadline"|"days",
days: int = 30 }`.

*(`as_of` closes the contract's own "known gap, deliberately not closed" — the
internals already thread a single `moment`.)*

```jsonc
{
  "as_of": "<iso>",
  "gw": 2,
  "deadline_utc": "<iso>",            // NEW — the page is a clock
  "hours_to_deadline": 17.7,          // NEW
  "window": { "from_utc": "<iso>", "to_utc": "<iso>",
              "basis": "since_last_deadline", "reason": "..." },

  "coverage": {                        // NEW — replaces six stat tiles
    "n_panellists": 15, "n_heard": 11,
    "silent": [{ "person_key": "zophar", "display_name": "Zophar",
                 "last_item_at": "<iso>|null", "reason": "..." }],
    "n_items": 41, "n_read": 9, "n_notes_only": 94,
    "n_transcribed": 8, "transcript_share": 0.02,
    "reason": "..."                    // when any count is unavailable
  },

  "squad": {                           // NEW — the page's other half
    "available": true, "source": "my_team"|"fact_manager_pick",
    "gw_of_picks": 1,
    "reason": "picks on file are GW1; GW2 picks become public at the deadline",
    "picks": [{ "code": 223094, "name": "Haaland", "pos": "FWD",
                "multiplier": 2, "lane": "captain" }],
    "bank": 0.4, "free_transfers": 1
  },

  "players": [{
    "code": 223094, "name": "Haaland", "pos": "FWD", "team": "MCI",
    "price": 15.5, "own_pct": 68.1,
    "xpts_next": 6.2,                  // NEW — joined from projection_table
    "xpts_source": "consensus of 5",   // NEW — never an unattributed number
    "lane": "captain", "lane_reason": "from your GW1 XI, multiplier 2",

    "intent": {
      "net": 4,
      "buy":     { "n": 5, "n_llm": 5, "n_cue": 0,
                   "people": [{ "person_key": "andy_ltfpl",
                                "display_name": "Andy",
                                "show": "Let's Talk FPL",
                                "basis": "sole_host",      // NEW: attribution basis
                                "conviction": "medium" }] },
      "sell":    { ... }, "hold": { ... }, "avoid": { ... },
      "captain": { ... }
    },

    "agreement": true,                 // NEW — in the shaded wedge?
    "agreement_reason": "you captain him and the panel is net buying",
    "split": { "n_for": 5, "n_against": 0, "is_split": false },

    "top_quote": {                     // NEW — one canonical quote per player
      "text": "...", "creator": "Let's Talk FPL",
      "person_key": "andy_ltfpl", "display_name": "Andy",
      "conviction": "medium", "extractor": "llm:claude-opus-5",
      "evidence_depth": "transcript",  // from analysis_json.evidence.depth
      "item_id": "...", "start_s": 1274.0,
      "deep_link": "https://youtube.com/watch?v=...&t=1274s",
      "match_basis": "normalised"      // NEW — see 9.4
    },
    "dissent": { ...same shape... } | null,
    "dissent_reason": "no panellist argued the other side",   // NEW
    "observations_n": 2
  }],

  "captaincy": [{ "code": ..., "name": "...", "n": 5, "n_llm": 5, "n_cue": 0,
                  "people": [...], "own_pct": 68.1, "eo": 121.4,
                  "is_yours": true, "top_quote": { ... } }],

  "observations": [{                   // NEW GRAIN — §5
    "obs_id": "...", "kind": "role", "subject_code": 219847,
    "subject_name": "Savinho", "subject_team": "MCI",
    "statement": "Moves to the right while Doku is out",
    "horizon_gw": null, "corroboration_n": 2,
    "sources": [{ "creator": "...", "person_key": "...", "quote": "...",
                  "start_s": 402.0, "deep_link": "...", "item_id": "...",
                  "evidence_depth": "transcript" }]
  }],

  "record_note": "...", "weights_all_zero": true
}
```

### 9.2 New script: `player_chatter`

Params `{ code: int (required), gw: int|null, days: int = 30 }`. Returns
`{ code, name, gw, as_of, intent{…}, quotes[≤5], observations[], silence_reason }`
— the same quote shape as `top_quote` above. Deliberately narrow so xPoints and
Template can call it on drawer-open without loading the whole board.

### 9.3 New endpoints (not scripts — these write)

- `POST /api/content/link` → `{ job_id, canonical_url, duplicate_of|null }`
- `GET  /api/content/link/{job_id}` →
  `{ job_id, url, item_id|null, stage, stages[{name, state, detail,
     elapsed_s, progress{done,total,unit}|null}], error|null,
     needs_attribution{candidates[]}|null }`
- `POST /api/content/link/{job_id}/attribute` → `{ person_key | "none" }`
- `POST /api/content/link/{job_id}/retry` → `{ from_stage }`

### 9.4 Amendments to the existing contract

| Field | Change | Why |
|---|---|---|
| `latest.url_basis`, `latest.url_reason` | **ADD** (from `content_item_asset`) | 353 items' `url` *is* an `.mp3`. The UI must render "▶ play audio (34 MB)" not "open episode". The contract has no field for this today and the current page mislabels all 353. |
| `latest.enclosure_type`, `latest.enclosure_length_bytes` | **ADD** | So the UI can warn before a 34 MB tap on mobile. |
| `creators[].people[]` | **ADD** `{person_key, display_name, role, entry_id, entry_verified, entry_confidence, entry_source_url, top10k_finishes, edge_note}` from `panel_person` + `panel_person_show` | The whole person-level premise. **Blocked**: both tables are empty; the YAML loader must run first. |
| `creators[].record_by_action[]` | **ADD** `[{scope, claims_scored, hits, hit_rate, wilson_lo95, weight}]` | `creator_score` already stores 7 scopes; `creators.py:379` reads only `scope='all'`. Discarding it hides that scope `sell` runs at 58% against `all` at 35%. |
| `creators[].silent_since`, `silent_reason` | **ADD** | Powers the coverage strip's *Silent 4*. A silent panellist before a deadline is information. |
| `claims[].person_key`, `claims[].attribution_basis` | **ADD** (`sole_host`/`title`/`stated`/`manual`/`show`) | `basis = 'show'` (no `item_person` row) is legitimate and must be *visible*, not inferred from a null. |
| `top_quote.match_basis` | **ADD** — `exact` / `normalised` / `unmatched` | `content_claim` has no `start_s`; timestamps come from fuzzy quote-matching against `transcript_segment` at render time (`creators.py` `TranscriptIndex.find`). That match can fail and today fails *silently* into `start_s: null`. The UI must distinguish "found at 14:22" from "not located — opening at the start". This is exactly the codebase's characteristic silent-failure bug. |
| `take.observations[]` | **ADD** | §5. |
| `take.evidence.depth` | **PROMOTE** to a required first-class field on every take and every quote | 94 of 120 are `notes`. It is the page's most important caveat and must travel with the quote, not sit in a tooltip. |
| `sources[].discovery` | **DELETE** | The panel's own docstring: "has no backing column anywhere… carries almost no information". |
| `consensus[]` | **DEPRECATE** in favour of `players[]` | Superset. Kept for one release so nothing breaks. |

### 9.5 Warehouse work this design depends on

Ordered by blocking-ness. Items 1–3 are prerequisites, not nice-to-haves.

1. **Load `data/panels/creator_panel_2026_27.yaml` into `panel_person` /
   `panel_person_show`.** All three person tables are empty. Without this the
   "panel of 15 verified people" is a file nobody reads, `creator_board`'s
   existing `scope: "panel"` parameter is inert, and this design degrades to
   show-level.
2. **Re-seed `content_source` from `sources.py`.** Solio Analytics — the show
   the owner named for insights — is in the code registry and absent from the
   warehouse.
3. **Populate `item_person`** for at least `sole_host` shows (Let's Talk FPL,
   FPL Harry, FPL Raptor are 1-person shows in the YAML — that alone attributes
   a large share of current claims for free).
4. Add `content_observation` (§5) + the analyzer schema change + a 25-item
   backfill over `depth in (transcript, article)`.
5. Back-attribute the five `user_link` items and retire the `user-shared`
   pseudo-creator from `creator_score`.

---

## 10. What is cut, and why it will not be missed

| Cut | Lines | Why the reader will not miss it |
|---|---|---|
| **The three-lens segmented control** (`wire` / `agreement` / `record`) | ~40 | Three lenses is three pages pretending to be one. There is one question before a deadline; there is one scroll. |
| **The wire as the primary object** — 29 creator rows sorted by recency, each with kinds, counts, latest link, take, record badge, entry badge | ~180 | It is a CRM for a podcast library. That Gianni Buttice published 31 episodes is not a fact that changes a transfer. The evidence it carried (quote, conviction, link) moves onto decision cards where it is attached to a *choice*. |
| **The six stat tiles** (creators tracked / items 30d / calls 30d / summarised / freshest / beat chance) | ~35 | Five of six are ingestion telemetry. Replaced by one strip whose every number is decision-relevant. "Freshest: 2h ago" tells you nothing; "Heard from 11 of 15, silent: Zophar, Lateriser…" tells you whether to trust tonight's board. |
| **The window chips 14 / 30 / 60 / 90** | ~15 | A 90-day window on a GW2 decision is nonsense. The window is *since the last deadline*, derived from `dim_event`, not chosen. One "show older" escape remains in the Panel accordion. |
| **Medium chips (youtube / podcast / blog) and the creator search box** | ~30 | Medium is not a reason to include or exclude an opinion. Search-for-a-creator is a directory operation and moves to the Panel accordion. |
| **The Agreement lens and its diverging popularity bars over all named players** | ~130 | The page's own copy says *"Agreement is popularity. It is not evidence of being right"* — and then gives it a top-level tab and sorts by `net` descending, putting the *least* informative rows first. Replaced by splits-only and by the board's shaded wedge, which encodes agreement as background. |
| **The Track record lens** as a top-level tab | ~120 | Right content, wrong prominence. Sixteen rows of `0.00` and a Wilson bar is a screen that says "nothing here" in the most elaborate way available. Becomes one sentence with one number (34.6%) at the top, and a full per-creator *and per-action* table inside the accordion. |
| **`take_reason` boxes on every row** | ~25 | With 94 of 120 analyses thin, most rows today are apologies. Aggregate the absence into one number in the strip. An honest empty state explains once; it does not repeat itself 29 times. |
| **`sources[].discovery`** | contract | Documented as informationless by its own author. |

Net: roughly 575 of 1,228 lines of view logic retired. **Nothing cut answers
"what do I do before 10:30 tomorrow."** Everything cut is *about the corpus*,
and the corpus is infrastructure — it belongs in an accordion labelled
`The panel`, opened when something looks wrong.

**Explicitly kept**, because they are the current page's real achievements:
the `llm:`/`cue` visual distinction and its hatch encoding; verbatim quotes as
blockquotes; panel-built `deep_link`s (never browser-constructed); written
reasons instead of blanks; `evidence.depth` badges; `panelSafe`'s
not-deployed-vs-errored distinction; the two-hue validated palette with
captaincy as a neutral ★; the conviction pip meter.

---

## 11. Three risks, and what would make me abandon this

### Risk 1 — The board is empty most of the week

Claim volume is spiky. GW1 has 267 claims, GW2 has 164, **GW3 has 35, GW4 has
19, GW5 has 1.** Panellists publish their team-selection content in the ~36
hours before a deadline. At T-96h the decision board plausibly has two rows,
and a page whose central visual is a nearly-empty chart is worse than the
directory it replaced — a directory at least always has content.

Mitigations designed in: the board degrades to a labelled strip below four
players; the "nothing said this week" state shows the *last* board, stamped and
settled; the Watchlist grain is less spiky than claims because observations
attach to news, not to deadlines.

**Abandon if**: measured over four consecutive gameweeks, the median number of
off-shade decision rows at T-72h is under 5. At that point the honest page is a
wire with a decision *section*, not a decision *instrument*, and proposal B or
C is right.

### Risk 2 — The lanes depend on squad data that is not reliably there

The entire personalisation rests on knowing the owner's XI.
`fact_manager_pick` holds **only GW1** for entry 4490171 — 15 rows, one
gameweek. FPL picks are public only after a deadline, so mid-week the only live
source is the authenticated my-team read, which depends on a self-renewing
OAuth token the harness cannot refresh (per the operational notes, the *user*
must run `fpl myteam auth`). A board whose y-axis is silently last week's team
is exactly this repo's characteristic bug: it looks right and is wrong.

Mitigations: the lane header always states the provenance and gameweek of the
picks; a stale read degrades loudly, never silently; `squad.available: false`
collapses to one lane and says so.

**Abandon the lane framing if** the live squad read is unavailable more often
than occasionally. A decision board that cannot see your squad is a consensus
table with extra chrome, and a consensus table should be drawn as a table.

### Risk 3 — Attribution to a show is not attribution to a person

This design speaks of "11 of 15 panellists". The warehouse cannot currently
support that sentence at all: `panel_person`, `panel_person_show` and
`item_person` are **all empty**, and `creator_entry` is 29 rows of NULL. Today
everything is show-level, and The FPL Wire is four people with four different
teams — so "The FPL Wire says buy Mbeumo" is, at best, one of four people. The
contract legitimises `basis = show` (no `item_person` row), which means a large
share of claims may *never* resolve to a person even after the loaders run.

If that share stays high, the page's person-level framing is a fiction it keeps
asserting, and asserting a fiction is worse than the current page's honest
vagueness.

**Abandon the person-level framing** — falling back to show-level with a
visible, permanent caveat — **if, after the YAML loader and `item_person`
population run, fewer than ~40% of in-window claims carry a person-level
attribution.** The coverage strip must report this share from day one so the
decision is made on a number, not a feeling.

### Secondary risks, noted not elaborated

- **`llm` extraction covers only 6 creators** in the last 14 days. If the
  considered-take tier stays that narrow, the board is really "what 6 shows
  think" wearing a 15-person label.
- **Timestamp resolution is fuzzy substring matching** against 8 transcripts.
  `match_basis` (§9.4) makes the failure visible; without it, every unmatched
  quote silently becomes a link to 0:00.
- **`xpts_next` on the decision card creates a join dependency** between two
  panels with different refresh cadences. If projections are stale the card
  must say so or drop the number — never show a stale projection next to a
  fresh quote.

---

## 12. Build order

1. Load the panel YAML; re-seed `content_source`; populate `item_person` for
   sole-host shows. *(Nothing else here is honest without this.)*
2. `creator_decision` panel + the coverage strip + the decision cards. Ship
   without the board — cards alone already beat the current page.
3. The Deadline Board.
4. `player_chatter` + `components/chatter.js` + the two drawer mounts.
5. `content_observation` + analyzer schema + backfill + the Watchlist.
6. Paste-a-link: endpoints, job model, stage ledger, attribution step.
7. Retire the old view; move its surviving parts into the Panel accordion.

Steps 2 and 4 are independently shippable and independently valuable, which is
the test that this is a design and not a rewrite.
