# Creators redesign — Proposal C: **the player is the index**

Status: design proposal, 2026-08-27. No code. Every number below was read out of
`data/warehouse/fpl.duckdb` and `data/panels/creator_panel_2026_27.yaml` while
writing this; where the brief and the warehouse disagree, the warehouse wins and
the disagreement is called out.

---

## 0. The one sentence

> **Nothing a creator says is about a creator — it is about a player, so the
> player is the row key, and "Creators" stops being a page you go to and becomes
> a strip that appears wherever a player already is.**

Everything downstream follows from that: the corpus is indexed by `player_code`,
the deliverable is a shared component mounted in three places, and the tab
shrinks to the residue that genuinely has no player to hang on.

---

## 1. Ground truth (verified, not assumed)

Read on 2026-08-27 from the live warehouse. These numbers are the design's
constraints, not decoration.

| Fact | Measured |
|---|---|
| `content_item` | **594** items, 24 creators |
| …text source | `description` **473**, `article` **111**, `transcript` **10** |
| …kind | podcast 372, blog 110, youtube 107, link 5 |
| `content_source` registry | 40 sources across 29 creators |
| `content_claim` | **487**; extractor `cue` **241** / `llm:claude-opus-5` **246** |
| …distinct players named | **119**; with at least one *considered* (llm) claim: **73** |
| …shape of coverage | 54 players have exactly **one** claim; the top two have 64 (Haaland) and 58 (Bruno) |
| `content_analysis` | **120** rows, all `claude-opus-5`; **317** individual calls, **317 of 317 carry a verbatim quote** |
| …stances actually emitted | buy 116 · **watch 56** · captain 36 · hold 37 · sell 16 · avoid 23 · bench 2 |
| …`gameweek` on a call | present on 237 of 317 |
| `transcript_segment` | **5,140** segments over **8** items |
| `transcript_provenance` | 6 rows — **2 of the 8 transcribed items have segments and no provenance row** |
| …ASR wall clock | 61.1s for 685s audio · 104.5s for 1,214s · 121.2s for 1,397s → **≈11× realtime**, mlx-whisper large-v3-turbo |
| …captions wall clock | **4.1–5.8s** (youtube innertube) |
| `content_item_asset` | 387 rows, 353 `url_basis='enclosure'`; **353 items where `content_item.url` IS the mp3** |
| `creator_score` | **330** rows across 7 scopes (all/buy/sell/avoid/hold/bench/captain). **Max `weight` = 0.0 in every single row.** Best `wilson_lo95` anywhere = 0.444 |
| `creator_entry` | 29 rows, **every `entry_id` NULL** |
| `panel_person` / `panel_person_show` / `item_person` / `source_probe` | **0 / 0 / 0 / 0 rows.** The curated roster exists in YAML and has **never been loaded into this warehouse** |
| Roster YAML | **16 people**, **7 distinct shows** (not 8), **15 of 16 with a verified entry id** (10 `conclusive`, 5 `high`) |
| …The FPL Wire | 4 of the 16 people |
| …Solio Analytics | **5 of the 16 people, zero registered sources, zero ingested items** |
| `fact_manager_pick` | **7 of the 15 panel entry ids have GW1 picks** (105 rows) |
| `fact_manager_transfer` | **0 rows, for anybody** |
| `intel_item` | **784** rows over **286** distinct players — kinds `out_of_position` 324, `set_piece` 215, `availability` 174, `press_conference` 71 |
| Player pool | **614** distinct codes for 2026-27 |

Four of these decide the whole design:

1. **119 of 614 players (19%) have any claim; 73 (12%) have a considered one.**
   Whatever we build, *"nobody has said anything about this player"* is the modal
   state — roughly four drawers in five. A design that treats emptiness as an
   edge case is a design that is wrong 80% of the time.
2. **`intel_item` already covers 286 players — 2.4× the creator corpus.** The
   "insights" grain the owner is asking for is *already in the warehouse,
   already player-keyed, and completely unreachable from the UI.*
3. **Every earned weight is 0.0, in all 330 rows.** Aggregating across people is
   the one thing a player-indexed page does by construction, and it is the one
   thing this data cannot support. §6 is entirely about that.
4. **The roster is not loaded.** 16 verified people exist in a YAML file and in
   nothing else. Today the app cannot say "Pras said this" — only "The FPL Wire
   said this", which is four different people with four different teams.

---

## 2. The argument

The current tab treats the **creator** as the entity. Open it and you get a wire
of 24 creator cards; to learn anything about a player you read them all and hold
the result in your head.

But look at what the app already is. `xpoints.js` is a matrix whose row key is
`player.code`. `template.js` is a field map whose row key is `player.code`. The
squad, the planner, the solver — all keyed on `code`. And look at the corpus:
every single one of the 317 analysis calls has a `player` field, and 487 of 487
claims carry a `player_code`. **The content corpus is already player-keyed. The
UI is the only layer that isn't.**

The owner said the architecture out loud: *"Every data must connect with
everything — so data must be highly accessible across the different tabs."* His
two example questions are both player-first (*"who's transferred Haaland"*) or
person-first-but-answered-in-a-player-context (*"what's the haul rate of Ben
Crellin"*). And his stated workflow — *use it alongside xPoints to find
transfers* — is a workflow where the Creators tab is not open. He is looking at a
projections matrix, his eye lands on a row, and the question is **"does anybody
have anything on this guy?"**

Answering that by making him navigate to another tab, find the creator who
mentioned the player, and open a creator drawer, is three clicks and a memory
task to retrieve one paragraph. That is the actual reason the page feels
"cluttered": it is not that there are too many pixels, it is that the pixels are
sorted by the wrong key, so every read is a scan.

So: **invert the index.** The corpus becomes a player-keyed read. The delivery
vehicle becomes a component, not a page. And the tab has to justify itself
against what a drawer can already do — §9.

### 2.1 The correction that makes this honest

There is a second inversion hiding in the numbers, and it is the best idea in
this proposal.

The panel's **words** carry weight 0.0 — measured, 330 rows, no exceptions. The
panel's **teams** are hard data: 15 verified entry ids, 10 of them conclusive by
league-admin evidence, and 7 of them already have GW1 picks in
`fact_manager_pick`. Ask "does Mark Sutherns own Haaland" and the warehouse says
yes, at multiplier 2, at GW1 — a fact, not an opinion, with no epistemic problem
at all.

So the player strip does not lead with what they *said*. It leads with what they
**did**:

> **DID → SAID → NOTICED**

- **DID** — of the verified panel entries, how many own him, at what multiplier,
  who captained him. Measured picks. No caveat needed.
- **SAID** — statements, with quote, timestamp and deep link. Weight 0.0,
  labelled as such, forever.
- **NOTICED** — insights: spoken ones ("he's playing as a false nine") and the
  machine-derived `intel_item` ones (out-of-position, set-piece, availability,
  press conference).

That ordering is the whole editorial position. It puts the strongest evidence
first, and it means the strip is useful even for the many players nobody has
talked about, because `intel_item` covers 286 players and picks cover all of
them.

---

## 3. The shared component: `PlayerVoice`

One module, mounted from three hosts. This is the deliverable; the tab is
secondary.

**Mount points**
1. `xpoints.js` → `showDetail()` drawer, below "Projected points by source".
2. `template.js` → `showDetail()` drawer, below "Your position on him".
3. The Desk (§8) → the player-picker column, at full width.

Also mountable, later and for free, from the planner and the solver's swap
explanations. That is the point of making it a component.

### 3.1 Wireframe — collapsed (the default in a drawer)

```
┌───────────────────────────────────────────────────────────────┐
│ THE PANEL ON HAALAND                                          │
│                                                               │
│ DID   ●●○○○○○  2 of 7 panel teams read · 1 captained          │
│       Mark Sutherns (C) · FPL Harry            [see teams →]  │
│                                                               │
│ SAID  11 people · 64 statements · last 6h ago                 │
│       ▲8 buy  ★7 captain  ◇3 watch  ▼1 sell                   │
│       ├──●─●●●───────────●─────────────●●●●●●●●─┤  30d        │
│       ⚠ weight 0.00 — nobody here has beaten a coin flip      │
│                                                               │
│ NOTICED  set-piece: 1st-choice pens (FPL, 2d ago)             │
│                                                    [open ▾]   │
└───────────────────────────────────────────────────────────────┘
```

Three lines of substance, ~150px, no scrolling, readable in one saccade. This
is what appears by default in an xPoints drawer while the reader is scanning a
matrix.

### 3.2 Wireframe — expanded

```
┌───────────────────────────────────────────────────────────────┐
│ THE PANEL ON HAALAND                        30d ▾    [× ]     │
├───────────────────────────────────────────────────────────────┤
│ DID — measured picks, not opinions                            │
│  ┌──────────┬──────────┬──────────┬──────────┬─────────────┐  │
│  │ Sutherns │ Harry    │ Pras     │ Andy LTF │ +3 read     │  │
│  │  ★ ×2    │   ×1     │    —     │    —     │  no Haaland │  │
│  └──────────┴──────────┴──────────┴──────────┴─────────────┘  │
│  7 of 15 verified panel teams have a GW1 read. The other 8    │
│  have a verified entry id but no stored picks yet.            │
│  Transfers: none stored for anyone — see why ⓘ                │
├───────────────────────────────────────────────────────────────┤
│ SAID — 64 statements, 11 people, 8 shows          [all ▾]     │
│                                                               │
│  GW1 ────────────── GW2 ────────────── GW3 ──────── now       │
│   ▲  ▲▲★         ★  ▲★★  ▲          ▽      ▲▲★★★▲▲★          │
│   ·  ···         ·  ···  ·          ·      ········           │
│   ○     ○○         ○         ○○            ○○○  ← keyword     │
│                                                               │
│  ⓘ 7 of the 11 spoke inside the same 26 hours, after the      │
│    same press conference. Read this as one wave, not seven    │
│    independent reads. Creators are not independent draws.     │
│                                                               │
│  ▲ BUY · high conviction · Pras (The FPL Wire) · 2d ago       │
│  ┃ "he's just a really, really good pick"                     │
│  ┃ considered take · claude-opus-5 · GW3 horizon              │
│  ┗━ ▶ play audio at 13:32                                     │
│                                                               │
│  ★ CAPTAIN · medium · Andy (Let's Talk FPL) · 3d ago          │
│  ┃ "I'll be wondering once again why I didn't just start…"    │
│  ┃ considered take · claude-opus-5                            │
│  ┗━ ▶ open episode at 08:11                                   │
│                                                               │
│  ░ keyword match · Fantasy Football Scout · 5d ago            │
│  ░ "…00:18 Man United news 03:46 Doku potential injury…"      │
│  ░ a search hit inside show notes. Not a stated opinion.      │
│  ░ ▶ open episode (no timestamp)                              │
│                                        [3 more · 41 keyword]  │
├───────────────────────────────────────────────────────────────┤
│ NOTICED — insights, not calls                                 │
│  ⚙ set-piece  1st-choice penalties        FPL bootstrap · 2d  │
│  ⚙ out-of-position  —                                         │
│  🗣 "playing him off the left when Foden starts" — Zophar,     │
│     The FPL Wire, 4d ago  ▶ 22:04                             │
└───────────────────────────────────────────────────────────────┘
```

### 3.3 What is deliberately absent

- **No net score.** No "+7", no consensus arrow, no "the panel is bullish". The
  panel contract forbids the panel from computing one (§10). Reason in §6.
- **No creator leaderboard, no weight column.** One fixed sentence carries it.
- **No sentiment colour on the aggregate.** Direction is coloured per statement
  (`--s1` buy / `--s2` sell / neutral ★ captain — inherited from the current
  view's validated pairing), never on the summary line.

### 3.4 Data contract

```jsonc
// panel: player_voice — one call, many players
// params: { codes: int[], days: 30, gw: int|null, per_player: 8 }
{
  "as_of": "<iso>",
  "window_days": 30,
  "gw": 3,
  "corpus": {                       // properties of the whole read
    "n_items_window": 61,
    "n_people": 0,                  // panel_person row count — TODAY: 0
    "roster_loaded": false,
    "roster_reason": "panel_person is empty in this warehouse; the curated
                      16-person roster in data/panels/creator_panel_2026_27.yaml
                      has never been upserted. Statements are attributed to the
                      SHOW, which is correct but coarser.",
    "players_with_statement": 119,
    "players_in_pool": 614,
    "weights_note": "<the single fixed sentence, from creator_score>"
  },
  "players": {
    "223094": {
      "code": 223094, "name": "Haaland", "resolved": true,

      "did": {                      // measured picks — never opinions
        "entries_total": 15,        // verified entry ids on the roster
        "entries_read": 7,          // of those, with stored picks
        "owners": [
          { "person_key": "sutherns", "person": "Mark Sutherns",
            "show": "Fantasy Football Scout", "entry_id": 252,
            "gw": 1, "multiplier": 2, "is_captain": true,
            "verified": true, "confidence": "conclusive",
            "source_url": "https://fantasy.premierleague.com/api/entry/252/" }
        ],
        "not_owned_by": ["Pras", "Andy (Let's Talk FPL)", "..."],
        "unread": ["Zophar", "Lateriser", "..."],
        "unread_reason": "verified entry id, no picks stored for GW1 yet",
        "transfers_in": [], "transfers_out": [],
        "transfers_reason": "fact_manager_transfer holds 0 rows for any entry.
                             A gameweek's transfers are public only after its
                             deadline — AND this stage has silently produced
                             nothing before (see PANEL_LEDGER). Treat an empty
                             list as unmeasured, not as 'nobody moved'."
      },

      "said": {
        "n_statements": 64, "n_people": 11, "n_shows": 8,
        "n_llm": 21, "n_cue": 43,
        "first_at": "<iso>", "last_at": "<iso>",
        "by_action": {              // counts only, split by extractor
          "buy":     { "n": 8, "n_llm": 6, "n_cue": 2, "shows": ["..."] },
          "sell":    { "n": 1, "n_llm": 1, "n_cue": 0, "shows": ["..."] },
          "captain": { "n": 7, "n_llm": 7, "n_cue": 0, "shows": ["..."] },
          "watch":   { "n": 3, "n_llm": 3, "n_cue": 0, "shows": ["..."] },
          "hold": {...}, "avoid": {...}, "bench": {...}
        },
        // NO `net`. NO `score`. Deliberate — see §6.
        "independence": {
          "n_shows": 8,
          "max_share_one_show": 0.31,
          "burst": { "n_in_window": 7, "window_hours": 26,
                     "of_total": 11 },
          "echo_flag": true,
          "echo_note": "7 of 11 spoke inside 26 hours"
        },
        "statements": [{
          "statement_id": "...",
          "item_id": "...", "show": "The FPL Wire",
          "person_key": null, "person": null,
          "person_basis": "show",   // "person" | "show" | "unattributed"
          "said_at": "<iso>",
          "action": "buy",          // buy sell hold avoid bench captain
                                    // triple_captain watch  ← watch is FIRST CLASS
          "horizon_gw": 3,
          "conviction": "high",     // llm only
          "confidence": 0.8,        // cue and llm
          "extractor": "llm:claude-opus-5",
          "quote": "he's just a really, really good pick",
          "reasoning": "Great goal threat, nailed minutes, no Europe…",
          "start_s": 812.0,
          "deep_link": "https://www.youtube.com/watch?v=…&t=812s",
          "link_kind": "episode",   // "episode" | "audio" | "article" | "page"
          "link_verb": "open episode at 13:32",   // panel writes the WORDS
          "text_source": "transcript",
          "evidence": { "depth": "transcript", "thin": false,
                        "scoreable": true, "chars": 41208 }
        }],
        "more": 53
      },

      "noticed": {
        "spoken": [{                // NEW grain — see §7
          "insight_id": "...", "item_id": "...",
          "kind": "role", "scope": "player",
          "headline": "playing off the left when Foden starts",
          "quote": "...", "start_s": 1324.0, "deep_link": "...",
          "link_kind": "episode", "link_verb": "open episode at 22:04",
          "show": "The FPL Wire", "person": "Zophar", "said_at": "<iso>",
          "extractor": "llm:claude-opus-5"
        }],
        "machine": [{               // straight from intel_item
          "kind": "set_piece", "headline": "...", "body": "...",
          "source": "fpl_api:bootstrap-static",
          "source_url": "...", "published_at": "<iso>", "confidence": 0.9
        }]
      },

      "empty_reasons": {            // only present when the section is empty
        "said": null,
        "noticed": null,
        "did": null
      }
    },
    "999999": {                     // a player nobody has mentioned
      "code": 999999, "name": "Kluivert", "resolved": true,
      "did": { ... },
      "said": { "n_statements": 0, "statements": [],
                "empty_reason": "no ingested item in the last 30 days names
                                 this player. 495 of 614 players are in the
                                 same position; this is normal, not a gap." },
      "noticed": { "spoken": [], "machine": [ ... ] }
    }
  },
  "unresolved": [                   // spoken names that matched no code
    { "surface_form": "Sangaré", "show": "FPL Raptor", "item_id": "...",
      "quote": "...", "reason": "no player in the 2026-27 pool matches" }
  ]
}
```

**Batch by design.** `codes: int[]` because xPoints renders 100 rows; the host
prefetches the visible page in one call and every drawer opens from memory. A
per-player round trip would make the strip feel like a page load and it would
never get used.

### 3.5 Size budget

| Budget | Limit | Why |
|---|---|---|
| Collapsed height | ≤ 160px | Must not push xPoints' per-source table below the fold |
| Expanded height | ≤ 480px, internal `overflow-y` | The drawer already scrolls; two scroll contexts is one too many |
| Statements rendered | 6, then "N more · M keyword" | The 64-statement player is one player in 614 |
| Quote clamp | 2 lines / ~180 chars, expand in place | Quotes are the point; truncating to one line kills them |
| Module size | ≤ 12KB JS + ≤ 4KB CSS, zero dependencies | Zero-build app; a `<script type=module>` and one `<link>` |
| Panel calls on open | **0** when the host prefetched; 1 otherwise | |
| Time to first paint | Skeleton immediately, never blocks the host drawer | The host's own content must render even if `player_voice` 500s |

The component owns `web/dist/js/components/player-voice.js` and
`web/dist/player-voice.css`, and imports only from `/js/app.js` (`el`,
`faceImg`, `emptyBox`, `runPanel`, `fmt1`, `fmt2`). It reuses the app's existing
`.chip`, `.freshdot`, `.seg`, `.data`, `.sub` vocabulary so a dot means the same
thing here as it does on xPoints — that consistency is half of the "Athletic
vibe" the owner liked.

---

## 4. The tab, redesigned: **The Desk**

Renamed. "Creators" names an entity that stops being the index; "The Desk" names
what is left: the place you bring things in and check the state of the room.

```
┌──────────────────────────────────────────────────────────────────────┐
│ The Desk                                                             │
│ Everything the panel has said, indexed by player. Their words carry  │
│ zero weight in the engine — 0 of 16 have beaten a coin flip. Their   │
│ TEAMS are hard data. Read accordingly.                               │
│                                                                      │
│ ┌────────────────────────────────────┐                               │
│ │ ▸ Paste a link                     │  youtube / podcast / article  │
│ │ [ https://…                      ] │  [ Analyse ]                  │
│ └────────────────────────────────────┘                               │
│                                                                      │
│ ──── PLAYERS IN PLAY ─────────────────────────────── last 30d ─────  │
│                                                                      │
│   who               said   people  shows  panel own   xPts   ⟶       │
│   ● Haaland          64      11      8     2/7 ★1     8.4    ›       │
│   ● Bruno Fernandes  58       9      7     4/7        6.1    ›       │
│   ● Mbeumo           22       6      5     1/7        5.2    ›       │
│   ○ Sangaré          19       4      4     0/7        4.8    ›       │
│   … 115 more named · 495 of 614 players unmentioned                  │
│                                                                      │
│   [click a row → the PlayerVoice component, full width, right]       │
│                                                                      │
│ ──── NOT ABOUT A PLAYER ──────────────────────────────────────────   │
│   ⚑ FIXTURE SWING · "Everton's run from GW4 is the best in the      │
│     game" — Lateriser, The FPL Wire, 2d ago  ▶ 31:12    [Fixtures →] │
│   ⚑ META · "wildcard after the international break, GW6" —          │
│     FPL Raptor, 3d ago  ▶ 18:40                                      │
│   ⚑ TACTICAL · "Arsenal are playing a back three at home" —         │
│     Zophar, 4d ago  ▶ 09:55                                          │
│                                                                      │
│ ──── THE ROOM ────────────────────────────────────────── details ▾   │
│   ⚠ Roster not loaded. 16 verified people are in the YAML and 0 in   │
│     the warehouse, so every statement above is attributed to a SHOW.│
│     The FPL Wire is four people with four different teams.          │
│   ⚠ Solio Analytics: 5 people on the roster, 0 sources, 0 items.    │
│   ⚠ Transcripts: 10 of 594 items. 473 are show notes only.          │
│   ⚠ Transfers: 0 rows stored for any panel entry.                   │
│   Sources: 40 registered · last probe status … · [expand]           │
└──────────────────────────────────────────────────────────────────────┘
```

Four blocks. The player list is a **directory into the component**, not a
dataset: five columns, no diverging bars, no tiles. The three residual blocks are
the answer to §9.

---

## 5. The central visualisation: the echo timeline

There is exactly one chart in this proposal and it lives inside the component:
**a single horizontal time axis over the window, one mark per statement.** Marks
above the line are positive (buy/captain/watch), below are negative
(sell/avoid/bench), hollow-and-hatched marks are `cue` keyword hits, solid are
`llm` considered takes. Gameweek deadlines are ticks on the axis.

**What it makes obvious that a table cannot.** A table says *8 people said buy*.
The timeline says *7 of the 11 statements landed inside the same 26 hours* — a
dense clump immediately after a press conference, then silence. That is not eight
independent reads converging on a truth; it is one piece of news echoing through
eight microphones, and it is the single most important thing to know about a
"consensus" among people who watch each other's videos. A count cannot show
clustering. A bar chart of buy-vs-sell actively hides it — which is precisely
what the current Agreement lens does.

Secondary readings the same mark set gives away for free:
- **Staleness.** A tight cluster three weeks back with nothing since = a stale
  narrative. On a bar chart it looks identical to live conviction.
- **Reversal.** Buy marks early, sell marks late, on the same axis = the panel
  turned. A net score of +5 hides a complete reversal.
- **Noise ratio.** The hollow row sits visually below the solid row, so the
  reader sees *at a glance* that 43 of Haaland's 64 statements are keyword
  windows scraped out of show-note boilerplate.

That last point is not hypothetical. Here is an actual stored `cue` claim
rationale for a *buy* on João Pedro:

> `andy@letstalkfpl.com ━━━ Join my league 👉 glojzb ━━━ 00:00 Intro 00:18 Man
> United news 03:46 Doku potential injury…`

That is the "evidence". 241 of 487 claims are of this kind. Any encoding that
lets them sit level with a quoted, timestamped, conviction-banded take is lying.

Colour discipline: two hues only, `--s1` positive / `--s2` negative, both already
validated all-pairs in both themes in the current view's header comment; captain
is a neutral ★, never a third hue; weak evidence is neutral grey + hatch, never a
colour. Inherited unchanged — that part of the current build is right.

---

## 6. The trap: making "5 people said buy" not read as authority

This is the sharpest risk in a player-indexed design, because aggregation across
people is what the design *does*. Six mechanisms, all mandatory:

1. **The panel refuses to compute a net.** No `net`, no `score`, no
   `consensus_direction` field exists in the `player_voice` contract. You cannot
   render a number the payload does not contain, and a future contributor cannot
   quietly add one to the view because the contract says why it is absent.
2. **The verb is "said", never "recommend".** Column header is `said`. Summary
   line is `11 people · 64 statements`. Never "11 buy signals".
3. **The fixed sentence, always visible, generated from `creator_score`:**
   > *Weight 0.00. Across 330 scored rows, no person or show has a 95% Wilson
   > lower bound above 0.50 at n ≥ 25. The best anywhere is 0.44. This is a
   > measured result, not a missing one.*
4. **The independence object.** `echo_flag` + `burst` + `max_share_one_show`, and
   the rendered line *"7 of the 11 spoke inside 26 hours — read this as one wave,
   not seven independent reads."* Counting people assumes independent draws;
   these people are not independent draws, and the UI says so on the player where
   the count appears.
5. **Extractor split at every level.** Every `by_action` bucket carries
   `n_llm`/`n_cue`, and the timeline separates them spatially. The headline count
   `64` is always immediately followed by its split.
6. **DID sits above SAID.** The first thing under the header is measured picks.
   The reader's eye lands on evidence before it lands on opinion.

What we do *not* do: hide the counts, or refuse to aggregate. He asked for
cross-cutting access and he should get it. The counts are honest; the framing
does the work.

---

## 7. The new grain: insights

The owner: *"some like SolioAnalytics looks at game and gives insights like which
player to watch, who's playing where — these key information should also be
layered somewhere."*

### 7.1 Half of it already exists and is invisible

`intel_item` holds **784 rows over 286 players** — `out_of_position` (324),
`set_piece` (215), `availability` (174), `press_conference` (71), with headline,
body, source, source_url, confidence and published_at. `out_of_position` is
*literally* "who's playing where": *"O'Reilly is classified DEF but performs like
a MID (attacking full-back — scoring as a defender, producing as a midfielder)"*,
with percentile evidence in the body.

It is reachable from the MCP (`player_intel`) and from **no panel and no view**.
That is the cheapest large win available anywhere in this proposal: surface
`intel_item` in `noticed.machine[]` and 286 players gain content on day one,
against 119 for the entire creator corpus.

### 7.2 The spoken half, and the `watch` stance already in the data

`content_analysis` already emits **56 calls with `stance: "watch"`** (41 in
`transfers_in`, 12 in `differentials`, 3 in `transfers_out`). These are *not*
buy/sell calls — they are "keep an eye on him", which is exactly the insight
grain. Today `_take()` in `creators.py` drops `stance` on the floor and the view
renders every `transfers_in` entry as **"▲ transfer in"**, so 41 "watch him"
statements are currently displayed as buy recommendations. That is a live
mis-render, not a design opinion.

Fix: `watch` becomes a first-class action with its own glyph (◇) and its own
word, and `stance` is carried through `_call()` instead of being inferred from
which bucket the call arrived in.

### 7.3 The genuinely new part

Add a `content_insight` grain, extracted by the same analysis pass:

```jsonc
{ "insight_id", "item_id",
  "scope": "player" | "fixture" | "team" | "meta",
  "player_code": int | null,        // required when scope = "player"
  "team_code":   int | null,        // required when scope in (fixture, team)
  "kind": "role" | "minutes" | "set_piece" | "tactical" | "fixture_swing"
        | "price" | "chip_meta" | "other",
  "headline", "quote", "reasoning", "start_s", "horizon_gw", "confidence" }
```

`scope="player"` rows join the player strip alongside the machine intel, visually
distinguished: 🗣 spoken (someone's read, weight 0.00) vs ⚙ measured (derived
from FPL data). Never merged into one list.

### 7.4 Insights that are not about a player

The honest answer to the brief's question. Three destinations, by scope:

| scope | Where it goes | Why |
|---|---|---|
| `fixture` | **The Fixtures tab**, as a quote pinned to the fixture/GW it names, plus a card on The Desk | "Everton's run from GW4" is a statement about a fixture row. Fixtures is already keyed by fixture. |
| `team` | **All players of that team** — fans out to every affected player strip, labelled `via team` so it is never mistaken for a statement about that individual | "Arsenal are playing a back three" changes how you read every Arsenal defender |
| `meta` | **The Desk only**, "NOT ABOUT A PLAYER" block | "Wildcard after the international break" has no player and no fixture. It is the one content type with nowhere else to live |

`meta` is small and it is one of the three things keeping the tab alive.

---

## 8. Paste-a-link

The owner's third demand, and the flow already half-exists:
`fpl_edge/interfaces/creators.py::ingest_link()` is a working implementation with
**5 items and 148 claims already in the warehouse under creator `user-shared`** —
more claims than any real creator has. He is already doing this; it just has no
UI.

### 8.1 Where it lives

**Two places, one implementation.**

1. **The Desk**, as the first block on the page. It is a page-level action with a
   queue and a history; that needs a page.
2. **Globally, as a keyboard-invoked paste bar** (`⌘V` on a URL anywhere in the
   app, or `⌘K`), because the natural moment to paste a link is while looking at
   xPoints. The bar submits and drops a toast; results land on The Desk and in
   every affected player strip.

The global bar is what makes the thin tab bearable. Without it, "paste a link" is
the one reason to navigate to The Desk, and that alone would justify the tab for
the wrong reason.

### 8.2 The flow, with measured timings

Panels are the read path and are read-only, so ingestion needs an endpoint:
`POST /api/links {url}` → `{job_id}`, plus a read panel `link_job {job_id}` for
polling (2s interval) or an SSE stream reusing the pattern already built for
`/api/conversations/{id}/stream`.

Four named stages, with **measured** medians from `transcript_provenance`:

```
  ┌ Resolving ─── Transcribing ─── Reading ─── Indexing ┐
    1–3s          5s | ~1/11 dur     ~20s        <1s
```

| Stage | Measured | Shown as |
|---|---|---|
| Resolve | 1–3s | "Found: *Locked & Loaded — GW1 Pod, The FPL Wire*" + title as soon as it is known |
| Transcribe (captions) | **4.1–5.8s** | "Published captions — 819 segments" |
| Transcribe (ASR) | **≈11× realtime** (61s/685s, 105s/1214s, 121s/1397s) | "No captions. Transcribing audio — ~1m50s for a 20-minute video", with a progress bar driven by `covered_seconds / audio_seconds`, which the ASR path already records |
| Analyse | ~15–25s | "Reading it — claude-opus-5" |
| Index | <1s | "Resolved 9 players · 2 unmatched" |

So a 20-minute video: **~10s on the captions route, ~2m15s on ASR** — the brief's
~2 min is the ASR case, and the UI must say which route it took, because a
10-second result and a 2-minute result are different products.

**The waiting state is not a spinner.** It streams: the title appears at ~2s, the
transcript's first segments appear as they land (the ASR path emits segments
incrementally), and the analysis fills in section by section. There is something
to read within three seconds of pasting. And it is **abandonable** — navigate
away, the job keeps running, the result appears on The Desk and in the player
strips. A two-minute modal that holds the app hostage will not get used twice.

### 8.3 Result render

The finished analysis renders inline on The Desk in exactly the `PlayerVoice`
statement vocabulary — quote, conviction, timestamp, deep link — plus one thing
the corpus view does not need: **"9 players · [Haaland] [Bruno] [Mbeumo] …"** as
chips, each one a jump straight into that player's strip. That is the moment the
cross-cutting index pays off visibly: paste a video, get nine player entry points.

### 8.4 Failure states

Every one of these is a real branch in the existing `ingest_link`:

| Failure | Render |
|---|---|
| Not a URL | Inline, before submit. No round trip. |
| URL is not a content page (his own `link_5080f43ab77d47d6` is a *league auto-join link*, already stored as an item) | "That is an FPL league invite, not a video or article. Nothing to transcribe." Refuse before ingesting. |
| YouTube, no captions, no audio | "No transcript is available for this video and its audio could not be fetched." Item is **not** stored. Current code returns exactly this. |
| ASR unavailable (no mlx / no model) | "Captions are unavailable and local transcription is not set up on this machine." Names the missing piece. |
| Analysis unavailable (`AnalysisUnavailable`, no API key) | Show the transcript with timestamps — **it is stored and searchable and that alone is worth the paste** — plus "not analysed: `<reason>`". Never silently fall back to keyword extraction without saying so. |
| Analysis threw | Transcript kept, "semantic analysis failed (`<Type>`); keyword extraction used — treat as leads only", claims rendered with the hatched `cue` styling. |
| Unresolved names | `unresolved[]`, rendered as the creator's own words: *"Sangaré — no player in the 2026-27 pool matches this name"*. Not dropped. |
| Duplicate paste | Canonicalisation already dedupes `watch?v=` / `youtu.be/` / `shorts/` to one `item_id`. UI says "Already analysed 4 days ago" and jumps to the stored result rather than re-billing an LLM call. |
| Article behind robots | "This site's robots.txt disallows fetching. Paste the text instead → [textarea]" — the escape hatch, because the bulk crawler's robots gate is deliberate and must stay. |

---

## 9. Does the tab deserve to exist?

**As "Creators", no.** Delete it.

The wire is a creator-ordered feed that answers a question nobody asks
("what has FPL Focal been up to?"). The agreement board is a popularity chart
built on people with zero measured edge. The track record table is seven columns
whose weight column is `0.00` in all 330 warehouse rows. All three are better
served — or better deleted — elsewhere.

**Three things survive that a player drawer structurally cannot do**, and they
are the tab's entire remaining mandate:

1. **Ingest.** A drawer is a read surface bound to one player. Pasting a link
   creates a job, needs a queue, a history and a failure log, and produces
   *multiple* players. It needs a page. (The global paste bar handles the
   *invocation*; the page handles the *state*.)
2. **`scope="meta"` insights.** "Wildcard after the international break" has no
   player_code and no fixture_id. There is no drawer it can appear in. If it does
   not live on this page it does not exist.
3. **Corpus provenance and health.** *Whose* voices are these, whose team is
   verified and how, which feeds are dead, what fraction of items are show notes.
   A player strip can carry a caveat about one statement; it cannot tell you the
   roster table is empty, that Solio Analytics has five people and zero sources,
   or that only 10 of 594 items are transcribed. That is a property of the
   corpus, and it is the difference between the reader trusting the strip and the
   reader trusting nothing.

A fourth candidate — "what did *this person* say lately" — I am deliberately
**not** keeping as a tab. It is a filter on a player-indexed corpus (`person` chip
in the strip's header), and where it genuinely matters is the question the owner
actually asked: *"what's the haul rate of Ben Crellin"*. That is a **manager**
question about entry 53517, answered from `fact_manager_gw`, and it belongs in a
manager/rivals surface next to the mini-league — not in a content tab. Ben
Crellin's *record* has nothing to do with what he said on a podcast.

**Kill condition, stated up front.** If the global paste bar ships, and `meta`
insights turn out to be fewer than ~2 per week (they may well be — 56 `watch`
calls exist but nobody has counted the non-player ones because the grain doesn't
exist yet), and the corpus health block collapses to four green ticks, then The
Desk is three widgets that belong on the Dashboard and in a Settings pane, and it
should be deleted then. I would rather ship a tab with an expiry condition
written down than defend it in two months out of sunk cost.

---

## 10. What I cut, and why it will not be missed

| Cut | Currently | Why it will not be missed |
|---|---|---|
| **The three-lens segmented control** (wire / agreement / record) | 3 lenses × 1 payload, each with its own filter row | Three ways to look at the same 594 rows *is* the clutter. One index, one component. |
| **The wire** (24 creator cards, recency-ordered) | ~180 lines | It answers "what has X been up to". The owner's questions are all player-first. Recency survives as the timeline axis inside the strip. |
| **The Agreement diverging bar** | ~120 lines | Counting people who all carry weight 0.00, in a chart that structurally hides clustering. Replaced by the echo timeline, which shows the *same counts* plus the thing that invalidates them. |
| **The Track record table** (7 cols × 24 rows, weight column all `0.00`) | ~90 lines | A table that exists to be a table. Compressed into one sentence (§6.3) plus a `details ▾` in THE ROOM for the day a weight goes non-zero. The moment one does, it comes back — as an alert, not a table. |
| **The six stat tiles** | creators tracked / items / calls / summarised / freshest / beat chance | Ingestion telemetry rendered as a scoreboard. Four of the six move into THE ROOM as warnings; "beat chance = 0" becomes the fixed sentence. |
| **The filter row** — sort segment, kind chips, "summarised only", creator search | ~60 lines | Filters over an index the reader has stopped using. The player list has one search box (player name) and one window control. |
| **The creator-scoped evidence drawer** (`openCreator`) | ~300 lines, the biggest single block | Its content survives entirely — items, claims, quotes, deep links — re-keyed by player. The `person` filter chip covers the residual "just show me Pras". |
| **The creator squad grid + transfer table inside the drawer** | ~70 lines | Re-keyed into `did` on the player strip, which is where the question is actually asked ("who owns *him*", not "what is *his* team"). The full-squad view belongs in a rivals surface, not here. |
| **`creator_board.consensus[]`** | panel-side | Superseded by `player_voice`. The `n_cue`/`n_llm` split it introduced is kept and generalised. |
| **The word "creator"** | everywhere | Replaced by **person** (when `panel_person` is loaded) and **show** (when it is not), with `person_basis` making the difference explicit. "The FPL Wire said" is four people; the app should stop saying it. |

Net: `creators.js` goes from **1,228 lines to roughly 350** (The Desk), plus a
new **~400-line component** that three tabs share. Fewer total lines, and the
lines that remain are reachable from where the work happens.

**Kept unchanged, because it is right:** the extractor distinction and its
hatched treatment; `reasonBox` (explaining absence rather than showing a blank);
server-built `deep_link`; the two-hue validated palette and neutral captain; the
freshness dot vocabulary shared with xPoints and Template.

---

## 11. Every state

**The corpus**

| State | Render |
|---|---|
| Panel/script not registered | The existing `panelSafe` behaviour, kept: "the `player_voice` panel is not registered on this server yet". The host drawer still renders its own content — the strip degrades to one grey line. |
| Panel 500s | One line inside the strip: "the panel errored"; never an error box that eats the drawer. |
| `creator_score` empty | "Unmeasured record, not a measured zero" (the panel's existing `_record_note` handles this; keep it verbatim). |
| Nothing tracked at all (0 items) | The Desk shows only the paste box and THE ROOM. Player strips render `DID` (picks exist independently) and `NOTICED.machine` (intel exists independently) and say "no content has been ingested". |
| Roster not loaded (**today's state**) | Every attribution reads as a show, `person_basis: "show"`, and THE ROOM carries the warning verbatim. The strip never invents a person. |

**The player**

| State | Render |
|---|---|
| Nothing said about this player (**~495 of 614**) | Not an error, not an empty box. `SAID — nothing in 30 days. 495 of 614 players are the same; the panel covers 19% of the game.` The DID and NOTICED sections still render, so the strip is *never* empty. |
| Player named but no code resolved | Appears in `unresolved[]` at corpus level and on The Desk's list as "Sangaré — unmatched, 4 mentions", rendered as the creator's own words. Never silently dropped. |
| Only `cue` claims | The whole SAID block renders hatched, with "43 keyword matches, 0 considered takes — these are search hits inside show notes, not stated opinions." Counts are NOT shown in the collapsed summary line for a cue-only player; the summary reads `— keyword hits only`. |
| No transcript for the item | Statement renders with its quote (analysis quotes exist regardless) but `start_s: null`, `link_verb: "open episode"`, and an `evidence.depth: "description"` badge reading **show notes only**. |
| No verified team for any speaker | `DID` shows `0 of 15 verified entries own him` **only if** entries were actually read; otherwise "no panel team has been read for this gameweek" with `unread_reason`. Zero-owned and unread must never render the same. |
| Verified entry, no picks stored (**8 of 15 today**) | Listed under `unread` with the reason. Not counted as "does not own him". |
| No measured record | The fixed sentence. Same sentence for everyone, because it is true for everyone. |
| Transfers unavailable (**all of them, today**) | "Transfers: none stored for anyone" + the two-part reason: public-only-after-deadline **and** this stage has silently produced nothing before. Never "nobody transferred him". |
| Insight not about a player | Never appears in a player strip. `fixture` → Fixtures; `team` → fans out to that team's players labelled `via team`; `meta` → The Desk only. |
| Statement's link is an mp3 (**353 of 594 items**) | `link_kind: "audio"`, `link_verb: "play audio at 13:32"`. Never "open episode". The panel writes the verb; the view never guesses it. |
| Item has no asset row (**207 items**) | `link_kind: "page"`, verb "open source". |

**Paste-a-link** — §8.4, all nine branches.

---

## 12. Panel contract additions, field by field

Amendments to `docs/platform/CREATOR_PANEL_CONTRACT.md`.

### 12.1 NEW script: `player_voice`

The core of this proposal. Nothing existing serves a player-keyed read — the
capability exists at the MCP layer (`fpl_player_claims`) but panels are the only
UI data path, so the view cannot reach it. This closes that gap.

- `params.codes: int[]` — **required**, batch. The reason the strip can live in a
  100-row matrix.
- `params.days: int = 30`, `params.gw: int|null`, `params.per_player: int = 8`.
- `params.as_of: iso|null` — **add it here** rather than repeating the contract's
  known gap. The internals already thread one `moment`; a strip that cannot
  reconstruct "what was being said before the GW3 deadline" is half a tool.

Result fields, all specified in §3.4. The load-bearing ones:

| Field | Contract obligation |
|---|---|
| `corpus.roster_loaded` / `roster_reason` | Truthful today: `false`, `panel_person` is empty. The view must render the reason. |
| `players[code].did.entries_total / entries_read / unread / unread_reason` | Three-way: owns / does not own / not read. Collapsing "not read" into "does not own" is forbidden. |
| `did.transfers_reason` | Must name both causes (deadline visibility **and** the empty-table risk). An empty array is unmeasured, never "no moves". |
| `said.by_action[*].n_llm` / `n_cue` | Generalises amendment 2 from the existing contract to every bucket. |
| `said.independence` | **New and mandatory.** `{n_shows, max_share_one_show, burst:{n_in_window, window_hours, of_total}, echo_flag, echo_note}`. The panel computes it because only the panel has the timestamps. |
| `said.net` | **Must not exist.** Explicitly forbidden by the contract, with the reason recorded, so nobody adds one later. |
| `statement.action` | Must include `watch` as a first-class value. `stance` from `analysis_json` is carried through `_call()` instead of being inferred from the bucket. Fixes 56 currently mis-rendered calls. |
| `statement.person_key` / `person` / `person_basis` | `person_basis ∈ {person, show, unattributed}`. `person` requires an `item_person` row; **no row means the show, which is legitimate and must be said, not hidden.** |
| `statement.link_kind` / `link_verb` | **New.** `link_kind ∈ {episode, audio, article, page}` from `content_item_asset.url_basis`; `link_verb` is the rendered words, written server-side for the same reason `deep_link` is. 353 of 594 items are `audio`. |
| `statement.evidence` | The existing amendment-3 object, promoted to per-statement. `None` = unrecorded, never defaulted to deep. |
| `statement.reasoning` | **New.** `analysis_json` carries a `reasoning` string per call that the current contract drops entirely. It is the most useful prose in the corpus and it is being thrown away. |
| `statement.horizon_gw` | From the call's own `gameweek` (present on 237 of 317). A GW5 call read as a GW3 call is a wrong answer. |
| `noticed.spoken[]` | The new `content_insight` grain, `scope="player"` only. |
| `noticed.machine[]` | Straight passthrough of `intel_item` for this code. Zero new extraction; 286 players covered on day one. |
| `unresolved[]` | Corpus-level. Named-but-unmatched surface forms with their quotes. |

### 12.2 `creator_board` → `desk_board`

Repointed, not extended. Drops `creators[]` and `consensus[]`; keeps and adds:

| Field | Purpose |
|---|---|
| `players_in_play[]` | `{code, name, n_statements, n_people, n_shows, panel_owned, panel_read, last_at}` — the directory list. Sorted by `n_statements`; **no net, no score.** |
| `unmentioned` | `{n: 495, of: 614}` — coverage, stated as a fact on the page |
| `insights_meta[]` | `scope="meta"` insights: the only content with no other home |
| `insights_fixture[]` | `scope="fixture"`, with `fixture_id`/`gw`, mirrored into the Fixtures tab |
| `room.roster` | `{people_expected: 16, people_loaded: 0, shows: 7, verified_entries: 15, reason}` |
| `room.corpus` | `{n_items: 594, by_text_source: {...}, n_transcribed: 10, n_analysed: 120}` |
| `room.sources[]` | The existing `sources[]`, plus `has_ever_yielded: bool` — Solio Analytics must be visibly present-and-empty, not absent |
| `room.warnings[]` | `{severity, text}` — the panel writes the warnings; the view renders them. Silent failure is this codebase's characteristic bug; this is where it becomes visible. |

Delete `sources[].discovery` — the existing contract already flags it as having
no backing column and carrying no information. Make the decision.

### 12.3 `creator_detail` → `person_detail`

Kept, demoted, re-keyed on `person_key` where the roster is loaded and on `show`
where it is not. It is what the `person` filter chip opens. Unchanged shape apart
from the key and the `link_kind`/`link_verb`/`reasoning` additions.

### 12.4 Not a panel: `POST /api/links` + `link_job`

Panels are read-only, so ingestion is an endpoint and polling is a panel.
`POST /api/links {url}` → `{job_id, dedupe_of: item_id|null}`.
`link_job {job_id}` → `{stage, stage_started_at, pct, title, route:
"captions"|"asr"|"article", eta_s, transcript_head[], result: {...}|null, error:
{code, message, remedy}|null}`. `eta_s` is computed from the measured constants
in §8.2, not guessed.

### 12.5 Backing work the panel needs

1. **Run `upsert_panel`.** The loader exists in
   `fpl_edge/ingest/content/panel.py`; `panel_person` has 0 rows. Until it runs,
   `person_key` is null everywhere and the FPL Wire is one voice instead of four.
2. **Populate `creator_entry.entry_id` from the YAML's 15 verified ids.** All 29
   rows currently have `entry_id NULL` with a correct-at-the-time reason
   ("every roster name is a channel name"); the YAML solved that by keying on
   *people*, and the table has not been told.
3. **Extract `content_insight`.** One new grain from the same analysis pass.
4. **Carry `stance` and `reasoning` through `_call()`.** Two fields currently
   dropped; 56 calls currently mis-rendered.

---

## 13. Three design risks, and what would make me abandon this

### Risk 1 — the corpus is too thin for a player index to feel alive

**19% of the pool has any statement; 12% has a considered one; 54 players have
exactly one claim.** Open ten xPoints drawers at random and eight say "nothing
said in 30 days". A component that is usually empty gets ignored, and once it is
ignored the cross-tab reach — the entire thesis — is worth nothing.

*Mitigation:* the DID → SAID → NOTICED ordering exists largely for this. `DID`
renders for every player (7 panel teams read, growing), and `NOTICED.machine`
renders for 286. The strip has content for roughly half the pool on day one even
though the creators cover a fifth of it.

*Abandon if:* after wiring `intel_item` and the panel picks, the median player
drawer still shows nothing in all three sections. Then the honest move is a
compact "content" column on the xPoints matrix (a dot: has a considered take /
keyword only / nothing) and no component at all.

### Risk 2 — the echo timeline is too clever, and he reads it as consensus anyway

I am replacing a bar chart everybody can read with a mark plot that needs a
sentence of explanation, on the argument that the bar chart lies. If the sentence
does not land, I have shipped something harder to read *and* still misleading —
the worst outcome available. And the deeper risk is that the ordering of the
sections quietly re-legitimises the words: putting SAID under a header called
"THE PANEL ON HAALAND", next to measured picks, lends the words the picks'
credibility by adjacency.

*Mitigation:* the count is never a verdict (`64 statements` not `+7`), the
`echo_note` is prose not iconography, and `net` is absent from the payload so it
cannot creep back.

*Abandon if:* he reads the strip and says "so the panel likes him". Then the
timeline goes and SAID collapses to a quote list with no aggregate whatsoever —
lose the cross-cutting summary and keep the honesty.

### Risk 3 — the thin tab strands the ingest and health surfaces

If the paste bar lives everywhere and the strip lives in three drawers, The Desk
becomes a page he visits once a fortnight. Corpus warnings on a page nobody opens
are warnings nobody reads — and this codebase's characteristic bug is precisely
the failure that reports itself to something nothing reads. I would have rebuilt
the bug in the UI layer.

*Mitigation:* `room.warnings[]` with a severity, surfaced on the **Dashboard**,
not only on The Desk. A dead feed is a dashboard event.

*Abandon if:* the warnings can be routed to the existing Inbox/monitor outbox
instead. Then THE ROOM stops being a reason for the tab, and combined with the
§9 kill condition, The Desk should be deleted and its paste box moved to the
global bar.

### And the honest answer on the tab

**The tab as it exists should not survive, and I am not going to defend it.** The
question "who is saying what" is a worse question than "what is anyone saying
about *this* player", and the current page is built around the worse one. What
survives is a component the whole app shares, and a small page with three jobs
and a written expiry condition. If in two months the paste bar has moved to the
global shortcut, `meta` insights are rare, and the warnings are in the Inbox,
then The Desk has no job left and deleting it will be the right call — and this
document says so in advance so that nobody has to relitigate it.
