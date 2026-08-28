# Creators, redesigned — Proposal B: PEOPLE-FIRST

*Design proposal only. No implementation code. Every number below was read out of
`data/warehouse/fpl.duckdb` or the panel YAML on 2026-08-27; nothing is estimated.*

---

## 0. The one sentence

> **A show cannot own a player. A person can — so this page is sixteen people, and
> the thing it shows about each of them is the gap between what they SAID and what
> they actually OWN.**

That is the whole organising idea, and it is stated at the top of the page in those
words, the way `template.js` states `rank move ≈ Σ (your multiplier − field EO) × points`
before it draws anything.

The identity, rendered as a teach band in the masthead:

```
        what they SAID          ×          what they OWN
        ─────────────                      ─────────────
        a claim, timestamped,              a squad, locked at a deadline,
        with a verbatim quote              a public and settled fact

        both  →  BACKED     said only  →  TALK
        own only  →  QUIET  neither  →  silent
```

### Why this is the right frame, and not a hedge

The brief asks me to justify the person as the organising unit *even though the owner
will use this to make transfer decisions*. The justification is not sentimental. It is
that **the person is the only unit on this page that carries a consequence.**

`creator_score` today holds 5 scope rows per show and **every single `weight` is `0.0`** —
I checked all of them; the best `wilson_lo95` in the table is `0.2087` (AllAboutFPL, n=46),
against a 0.50 bar at n≥25. So the page cannot rank by "who is right". It has no measured
authority to sell, and Proposal B does not pretend otherwise.

What it has instead is **skin in the game**. Fifteen of sixteen panel people have a
verified FPL entry id (`entry_confidence` `conclusive` or `high`), and **seven of those
fifteen already have their GW1 fifteen crawled into `fact_manager_pick`** — entries
41 (Andy), 124 (Jonny Currie), 252 (Mark Sutherns), 3054 (FPL Harry), 3315 (Pras),
5133 (BigMan Bakar), 53517 (Ben Crellin). A squad is not an opinion. It is a settled,
public, un-retractable bet, and it is the one creator datum in this warehouse that
does not need a track record to be worth something.

A show cannot supply it. "The FPL Wire" has no entry id, has never made a pick, and its
four hosts — Pras (3315), Zophar (2177), Lateriser (6816), BigMan Bakar (5133) — hold four
different teams. The newest Wire episode in the corpus is literally titled
*"Free Hit or Wildcard? — **Zophar** Gameweek 2 Team"*. Filing that under "The FPL Wire"
and counting it as the Wire's opinion, which is what today's page does, inflates
agreement between people who never agreed. Same for Fantasy Football Hub (4 people),
same for BlackBox (2), same for Solio (5, one of which is a company and not a person at all).

And the person grain is what makes the page *usable for transfers*, not what gets in the way:
"Pras is talking Semenyo up and does not own him; Harry owns him and never mentioned him"
is a transfer-grade sentence. "The FPL Wire mentioned Semenyo" is not.

---

## 1. What I read first, and what I took from it

`web/dist/js/views/xpoints.js`, `web/dist/js/views/template.js`, `web/dist/template.css`.
The five habits that make those pages work, which B inherits without negotiation:

1. **The governing idea is stated in plain words above the first control.** Template
   prints the rank-move identity and then says "template holdings cancel out of that sum".
   xPoints says "numbers are copied from ingested providers, never modelled here."
2. **Toolbar rows are labelled.** `el("span","tlabel","Sources")`, `"Gameweeks"`,
   `"Filter"`, `"Measure"`, `"Field"`, `"Sets"`, `"Who is in it"`. Every row of chips
   says what it controls. Today's Creators page has `Window`, `Lens`, `Sort & filter` —
   and then a loose pile of unlabelled kind-chips, an unlabelled toggle and a bare
   search box hanging off the end of one row. That is the "cluttered" the owner named.
3. **A strong central visual makes the answer spatial.** The field map's y=x diagonal
   turns "template / neutral / fade" into three regions of a plane. The beeswarm turns
   "what does the template look like" into spikes over a floor.
4. **Empty states explain.** `same_values_as_gw` is printed as "the feed re-stamped a
   settled gameweek, it is not a fresh forecast." The momentum card draws an
   *observation ledger* rather than a one-point line.
5. **Provenance is on the page, not in a doc.** Freshness dots with a shared vocabulary
   (`<36h` good, `<72h` warn, else bad), `n=`, denominators, `prices as of Nh ago`.

B keeps the freshness vocabulary, the `--s1`/`--s2` validated diverging pair, the
`chip`/`tlabel`/`seg`/`stat` primitives and the drawer pattern verbatim. It is the same
app, not a second one.

---

## 2. What I throw away from `web/dist/js/views/creators.js`

The current file is 1,228 lines. B deletes roughly 700 of them. Specifically:

| Cut | Why it will not be missed |
|---|---|
| **The three-lens segmented control** (`wire` / `agreement` / `record`) | Three lenses over one payload is three pages sharing a URL. The owner has to *know which lens answers his question* before he can ask it. B has one page with a fixed vertical argument and a per-person route; nothing is hidden behind a mode. |
| **The whole `renderAgreement()` consensus board** (~140 lines) | It counts show names. `board.consensus[].buy.creators` is a list of strings like `"The FPL Wire"`. Four Wire hosts collapse into one vote, so the count is wrong in the direction that matters, and the page then spends a paragraph telling you not to trust it. Person-level agreement is a *column* of the new grid; it does not need a lens. |
| **The `takesOnly` toggle** | A filter whose entire job is to hide the honest empty states the same page just worked hard to write. Delete the filter, keep the reasons. |
| **`sort = recent \| claims \| items`** | Sorting people by *output volume* rewards the daily-upload channel over the once-a-week one. Andy (Let's Talk FPL) has 47 items in the corpus; Mark Sutherns' BlackBox has 16. That ordering is a fact about upload cadence, not about the person. B orders by a stated, defensible key (see §4). |
| **The `n_items_window · n_claims_window` counter line** | "12 items · 24 calls in 30d" is volume theatre. It reads as a score and is not one. |
| **The `statRow` six-tile block** | `creators tracked / items / player calls / summarised / freshest / beat chance`. Five of the six are corpus-health metrics — they belong in a footer, not above the fold. The sixth ("beat chance") is permanently `0` and deserves a sentence, not a tile. |
| **The show-grain `cx-whochip` creator chips inside consensus rows** | Same defect as the board they sit in. |
| **`kinds` multi-select as a primary control** | Filtering people by whether they publish podcasts or YouTube is a filter on the *pipe*, not the person. It survives as a secondary chip; it stops being one of only three toolbar rows. |
| **`recordBadge` on every wire row** | Sixteen copies of "unmeasured / not above chance", one per row, is sixteen repetitions of one fact. Say it once, at the top, in a sentence. |

**What I explicitly keep** — this file did several things right and B would be worse
without them:

- `extractorMeta()`: `cue` vs `llm:` as visually distinct evidence, hatched vs solid.
  This is the single best decision in the current page and B hardens it (§6).
- `TEXT_SOURCE` badges (`full transcript` / `article text` / `show notes only`). Load-bearing:
  **473 of 594 items are `description`, 111 are `article`, and only 10 are `transcript`.**
- `reasonBox()` and the `expected:true` variant — an explanation styled as information,
  not as breakage. B uses it more, not less.
- `panelSafe()` — "the panel is not registered yet" rendered as a deployment state
  rather than a red HTTP box.
- The deep-link contract: the panel builds the URL, the browser only follows it.

---

## 3. The wireframe

Two routes, one tab.

- `#creators` — the **roster**: sixteen people, shallow, comparative.
- `#creators/p/<person_key>` — the **person**: one person, deep. A real route, not a
  drawer, because the owner will want to link to it and reload it.

### 3.1 Roster — above the fold

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  Sixteen people. Not eight shows.                                            ║
║                                                                              ║
║      what they SAID   ×   what they OWN                                      ║
║      ───────────────      ─────────────                                      ║
║      both → BACKED    said only → TALK    own only → QUIET    neither → ·     ║
║                                                                              ║
║  Nobody here has beaten a coin flip. Every earned weight in creator_score     ║
║  is 0.0 and the best lower bound is 0.21 at n=46, against a 0.50 bar at       ║
║  n≥25. So this page never sorts by authority. It sorts by what someone        ║
║  actually staked — a squad is a settled fact, a take is not.                 ║
║                                                                              ║
║  People    [✓ all 16] [The FPL Wire 4] [Hub 4] [BlackBox 2] [Solio 5]        ║
║            [solo hosts 3]                        ● 11 of 16 have a squad     ║
║  Window    [7d] [✓14d] [30d] [60d]      ● snapshot 2h ago · takes about GW3   ║
║  Evidence  [✓ considered takes] [keyword matches] [show notes]               ║
║  Add       [ paste a YouTube / podcast / article link            ] [ Add ▸ ]  ║
╠══════════════════════════════════════════════════════════════════════════════╣
║                                                                              ║
║   THE CONVICTION GRID          said × owned, GW3 window, GW1 squads           ║
║                                                                              ║
║              Haal  Seme  Sala  Fode  Pali  Rogr  ONei  Sarr  Wirt  Mbeu  …    ║
║   Pras        ▣     ▣     ◻     ·     ▽     ·     ◻     ·     ▲     ·        ║
║   Zophar      ▲     ▲     ·     ·     ·     ◆     ·     ▼     ·     ·        ║
║   Lateriser   ▲     ·     ·     ·     ·     ·     ·     ·     ·     ·        ║
║   BigMan      ▣     ◻     ▣     ·     ▲     ·     ·     ·     ·     ·        ║
║   FPL Harry   ⊚     ▣     ◻     ◻     ·     ◆     ◻     ·     ·     ◻        ║
║   Andy LTFPL  ▣     ▲     ◻     ·     ·     ·     ◻     ▽     ·     ·        ║
║   Ben Crellin ◻     ◻     ◻     ·     ·     ·     ·     ·     ·     ◻        ║
║   M Sutherns  ▣     ·     ◻     ·     ·     ·     ·     ·     ·     ·        ║
║   J Currie    ◻     ◻     ◻     ·     ·     ·     ·     ·     ·     ·        ║
║   ─────────── ╌╌╌╌╌ squad not crawled ╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌      ║
║   FPL Raptor  ▲     ·     ·     ·     ·     ·     ·     ·     ·     ◆        ║
║   Az Phillips ·     ·     ·     ·     ·     ·     ·     ·     ·     ·        ║
║   FPL Salah   ·     ·     ·     ·     ·     ·     ·     ·     ·     ·        ║
║   ─────────── ═══ said by a SHOW, no host established ══════════════════      ║
║   FPL BlackBox▲     ·     ▼     ·     ·     ·     ·     ·     ◆     ·        ║
║   ─────────── ━━━ YOU ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━       ║
║   Your squad  ⊚     ◻     ▣     ◻     ·     ·     ·     ·     ·     ·        ║
║                                                                              ║
║   ▣ backed (said + owns)  ▲▼ talk only  ◻ quiet holding  ⊚ captain           ║
║   ◆ watch — an insight, no buy/sell direction   ▽ hatched = keyword match     ║
║   · nothing said, not owned      ╌ dashed row = squad unreadable, see below   ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

Everything above is above the fold on a 1440×900 laptop. Below it, in order:

### 3.2 Roster — below the fold, in argument order

```
  ┌── WORTH WATCHING ─────────────────────────────── the insight grain ───┐
  │  Not a buy. Not a sell. Something a person told you to look at.       │
  │                                                                       │
  │   Nico O'Reilly     3 people   ◆ Pras ◆ Harry ◆ Zophar                │
  │     “he's playing left back but he's getting into the box”            │
  │     — Pras, The FPL Wire, GW2 pod pt.1 ▶ 14:22       [ xPoints ↗ ]    │
  │   Promise David     3 people   …                                      │
  │   Phil Foden        3 people   …                                      │
  └───────────────────────────────────────────────────────────────────────┘

  ┌── WHAT EACH PERSON SAID LAST ───────────────── the wire, demoted ─────┐
  │  ● 3h  Zophar      Free Hit or Wildcard? — Zophar GW2 Team            │
  │        The FPL Wire · 🎙 podcast · show notes only · title-attributed │
  │        no summarised take — only show notes exist for this episode    │
  │        [ 4 raw calls → ]                                              │
  │  ● 5h  FPL Harry   MY GW2 TRANSFER PLANS …           ▶ play audio     │
  │  …                                                                    │
  └───────────────────────────────────────────────────────────────────────┘

  ┌── THE RECORD ────────────────────────────────────── collapsed ────────┐
  │  Nobody has beaten a coin flip.  ▸ show the numbers                   │
  └───────────────────────────────────────────────────────────────────────┘

  ┌── WHAT THIS PAGE IS READING ────────────────────── footer ────────────┐
  │  16 people · 7 shows · 208 of 594 items on a panel show               │
  │  15 verified entry ids · 7 squads crawled · 10 items transcribed      │
  │  provenance: creator_people @ …                                       │
  └───────────────────────────────────────────────────────────────────────┘
```

### 3.3 The person route

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  ‹ all people                                                                ║
║                                                                              ║
║  ZOPHAR                      Utkarsh Dalmia · @Zophar666                     ║
║  The FPL Wire · Fantasy Football Hub                                         ║
║  8 top-10k finishes · best ever 17 · 17 seasons                              ║
║                                                                              ║
║  entry 2177 “Z”   ◆ high confidence                              [ why? ▾ ]  ║
║  ↳ Member of both invite-only FPL Wire leagues; account holder “Utkarsh D”;  ║
║    team named “Z”; published bio claims a 17th-place best and                 ║
║    /api/entry/2177/history/ shows best_ever_rank = 17.                        ║
║  Statistics-led decision making; the Wire's numbers voice.                    ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HIS SQUAD                                          ┌ SAID vs OWNED ───────┐ ║
║                                                     │ BACKED   3           │ ║
║      ┌────────────── GW1 ──────────────┐            │  Haaland ▲ high      │ ║
║      │   [pitch, 11 + 3 bench, (C)]    │            │  Semenyo ▲ medium    │ ║
║      └─────────────────────────────────┘            │  Sarr    ▼ sold      │ ║
║      not crawled yet                                │ TALK     5           │ ║
║      ↳ entry 2177 is verified but is not in the     │  …                   │ ║
║        2,859-manager crawl pool. Nothing is         │ QUIET    –            │ ║
║        inferred from his co-hosts' teams.           │  needs a squad       │ ║
║                                                     └──────────────────────┘ ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HIS TRANSFERS                                                               ║
║  Nothing yet, and that is correct — a gameweek's transfers become public      ║
║  only after its deadline, and fact_manager_transfer holds 0 rows for any      ║
║  manager. This fills in at the next deadline.                                ║
╠══════════════════════════════════════════════════════════════════════════════╣
║  HIS VOICE                    [ all 28 ] [ considered 6 ] [ keyword 22 ]     ║
║                               filter by player: [ Haaland ▾ ]                ║
║                                                                              ║
║  🎙 Free Hit or Wildcard? — Zophar Gameweek 2 Team    3h ago                  ║
║     The FPL Wire · show notes only · attributed by TITLE                      ║
║     ↳ evidence: the episode title contains “Zophar”, matched whole-word       ║
║       against an alias declared on the panel, and only against aliases of      ║
║       people who are on this show.                       ▶ play audio          ║
║     ┌ considered take ─────────────────────────────────────────────┐          ║
║     │ ▲ Haaland   ●●● high                                          │          ║
║     │   “I think he's just too good to be without this week”        │          ║
║     │                                        ▶ open at 13:32        │          ║
║     └───────────────────────────────────────────────────────────────┘          ║
║     ┌ keyword match — a search hit, not a stated opinion ──────────┐ (hatched) ║
║     │ ▽ Gordon    ●○○ 0.35    “…gordon…”         ▶ open the source │          ║
║     └───────────────────────────────────────────────────────────────┘          ║
╚══════════════════════════════════════════════════════════════════════════════╝
```

Note three things this page does that today's drawer cannot:

- The **entry evidence is on the page**, expandable, verbatim from `entry_evidence`.
  The owner's own YAML says a wrong id "does not 404 — it resolves to a different real
  person", and that twenty fabricated ids already shipped once. The UI that renders a
  stranger's squad under a creator's name should show its working.
- **"filter by player"** — the brief's "every quote they have on a player", as a control.
- **Every item states how it was attributed to this person** (`sole_host` / `title` /
  `stated` / `manual`) with the verbatim `evidence` span. See §7.

---

## 4. The central visualisation: the Conviction Grid

**What it is.** Rows = people (ordered: people with a crawled squad first, by
`top10k_finishes` desc as a *stated, non-authority* tiebreak; then people without a
squad; then a separately-banded row per show that has un-attributed items; then your own
squad, pinned, with a rule above it). Columns = players, ordered by how many *distinct
people* touched them (said or owned), capped at ~30 with a "show all" expansion.

**The two channels, deliberately orthogonal:**

| Channel | Encodes | Values |
|---|---|---|
| **Cell fill (hue)** | what they SAID | `--s1` tint = positive (buy/hold/captain), `--s2` tint = negative (sell/avoid/bench), neutral grey = `watch`, none = silent. Tint opacity = conviction band. Hatched = `cue` extractor. |
| **Cell outline (shape)** | what they OWN | solid ring = in squad, double ring = captained, no ring = not owned, **dashed row** = squad unreadable |

Hue never carries ownership and shape never carries opinion, so the two facts stay
readable independently and the whole grid survives colour-blindness — ownership is a
shape channel. The `--s1`/`--s2` pair is the one already validated all-pairs in both
themes for this app (ΔE 25.9 dark / 24.7 light under CVD, per `creators.css`).

**What it makes obvious that a table cannot:**

1. **Rows acquire a character.** A person who is all tint and no rings *talks and does
   not bet*. A person whose tint and rings sit on the same cells *does what he says*.
   Across sixteen rows that is a shape you read in one second. In a table it is a join
   you perform sixteen times in your head.
2. **Hype columns become visible.** A player with four tinted cells and zero rings is a
   column of talk with nobody's money behind it. Today's page renders exactly this
   situation as "4 buy" and then spends a paragraph of prose warning you it is only
   popularity. The grid makes the warning structural instead of textual: you can *see*
   that no ring appears under the talk.
3. **QUIET holdings — rings with no tint — pop out.** These are players a panel member
   owns and has never mentioned on air. **No podcast, transcript or claim extractor can
   ever surface them.** They are the single most valuable thing in this dataset, they are
   invisible in every version of this page that has ever existed, and in the grid they
   are the only marks that are outline-only. If this design is worth building, it is
   worth building for this row of pixels.
4. **Your row is on the same axis.** "How far am I from the people I follow" is a row
   comparison, not a mental join against another tab.

**Interaction.** Hover a cell → the tooltip is the verbatim quote, the conviction band,
the extractor, and the ownership fact ("in his GW1 squad, ×1"). Click a cell → the
person route, scrolled to that quote. Click a row label → the person route. Click a
column header → cross-tab jump (§9).

**What I refuse to draw.** A composite "conviction score" per cell. Combining a stated
conviction band with an ownership boolean into one number would be inventing a metric,
and the two facts have completely different epistemic standing — one is a model's read of
a sentence, the other is a locked FPL squad. They stay two channels.

---

## 5. Requirement 1 — the insight grain

**The finding that settles the design: the data already exists and is thrown away.**

`analyze.PlayerCall.stance` is `Literal["buy","sell","hold","captain","avoid","bench","watch"]`.
`models.Action` — the closed set that becomes a `content_claim` row — has **no `WATCH`
member**. So every `watch` call the model extracts is stored inside `content_analysis.analysis_json`
and then dropped on the floor at the claim layer, and no view renders it.

I counted them: **56 `watch` calls across the 120 stored analyses, and all 56 carry a
verbatim quote.** Distribution: user-shared 32, Fantasy Football Scout 13, FPL Harry 4,
Let's Talk FPL 3, AllAboutFPL 3, FPL Raptor 1. Most-watched: Phil Foden (3), Promise
David (3), Nico O'Reilly (3), then Schär, Rogers, van Hecke, Ndiaye, Semenyo (2 each).

That is the owner's "which player to watch, who's playing where", already in the
warehouse, already quoted, already timestamped where a transcript exists.

**Where it lives.** Two places, and only two:

1. **In the grid**, as a third, hueless mark (`◆`, neutral grey). It is deliberately not
   a colour: a watch is not a direction, and giving it the third series hue would put it
   on the same axis as buy and sell.
2. **In the "Worth watching" ledger**, immediately below the grid — a short list of
   players ranked by *distinct people who flagged them*, each with the verbatim quote,
   the person, the item, and a timestamped link.

**What makes it different from a claim, on the page and in the pipeline:**

- A watch is **never scored**. It is not a prediction with a resolvable outcome, so it
  must never enter `creator_score`, and the ledger says so in one line: *"A watch is an
  observation, not a call. It is not scored and never earns anyone weight."*
- A watch is **never counted as agreement**. Three people watching Foden is three people
  noticing the same thing, which is genuinely useful, and is labelled "3 people flagged
  him" rather than "3 buy".
- A watch's value is **the reason, not the count**, so the ledger leads with the quote
  and the count is a small chip. The reverse of how the current consensus board works.

**Pipeline consequence** (a design requirement, not code): `watch` needs to become a
first-class extract with the same lineage guarantees as a claim — a stable id, a
`published_at`, a `deep_link`. The cleanest route is a `content_insight` table
(`insight_id, item_id, person_key, code, surface_form, kind, quote, start_s, extractor,
published_at`) rather than adding `WATCH` to `Action`, because adding it to `Action`
would let it leak into `creator_score` on the next scoring run and quietly decalibrate
the channel that exists to be calibrated.

**And the honest caveat, printed on the ledger:** Solio Analytics — the show the owner
named as the source of exactly this kind of insight — has **zero items in the corpus**.
Five panel people are attached to a show that has never been ingested. The ledger says
that where the ledger is, not in a doc.

---

## 6. Requirement 3 — paste a link

### The flow

There is already a precedent to copy exactly: `POST /api/solve` → `GET /api/solve/status`
→ `GET /api/solve/plan`, polled at a fixed interval by `solver.js`. Links get the same
shape: `POST /api/links {url}` → `{job_id}`; `GET /api/links/{job_id}` → a job record;
the view polls it.

There is also already a *pipe*: `source_key = 'user_link'`, `creator = 'user-shared'`,
`kind = 'link'`. Five items are in there now. It is not wired to a UI and its results are
orphaned (see failure state F5).

```
  paste ──▶ RESOLVE ──▶ FETCH ──▶ TRANSCRIBE ──▶ ANALYSE ──▶ ATTRIBUTE ──▶ render
            ~instant    1–20s     4s or ~2min     10–90s      instant
```

**The waiting state is a real design object, not a spinner.** It is a five-step ledger
that fills in, in place, above the grid, and it is honest about which path it took —
because the two transcription paths differ by two orders of magnitude and I measured
both in `transcript_provenance`:

- **YouTube captions** (`derivation = 'captions'`, engine `youtube_captions`): 3 rows,
  mean wall **4.7s** for ~1,070s of speech ≈ **286× realtime**.
- **Local ASR** (`derivation = 'asr'`, `mlx-community/whisper-large-v3-turbo`): 3 rows,
  mean **95.6s** wall for a mean **1,099s** of audio ≈ **11.5× realtime**.

So a 20-minute video is ~4 seconds if captions exist and ~105 seconds if they do not.
The brief's "~2 min" is the ASR path plus analysis. **The UI must say which path it is on
before the wait starts**, because a 4-second wait and a 2-minute wait need different
affordances:

```
  ┌ Adding  youtube.com/watch?v=EU4sZAAL7w8 ────────────────────────────┐
  │  ✓ Resolved      YouTube video EU4sZAAL7w8                          │
  │                  ↳ recognised as FPL Harry's channel → FPL Harry     │
  │  ✓ Fetched       “MY FPL GW2 TRANSFER PLANS 🚨” · 22:14 · 2026-08-24 │
  │  ◐ Transcribing  published captions found — about 5 seconds          │
  │  ○ Analysing                                                         │
  │  ○ Attributing                                                       │
  │                                                       [ cancel ]     │
  └──────────────────────────────────────────────────────────────────────┘
```

and on the slow path, step 3 reads:
`◐ Transcribing  no captions — local ASR at ~11× realtime, about 1m 55s of 22:14 · [====----] 9:40`
with a real progress bar driven by `covered_seconds / audio_seconds`, which the ASR side
already computes.

On success the take renders **inline, in place, immediately above the grid**, in exactly
the same card shape the person route uses, and then — the part that matters — **the grid
re-renders with that person's row updated**. Pasting a Wire link changes Zophar's row.
That is what makes this a workflow rather than a converter.

### The failure states, each with its own copy

| # | Failure | What the panel returns | What the page says |
|---|---|---|---|
| F1 | Not a URL / unsupported host | `rejected`, `reason: unsupported_host` | *"That is not a link this app can fetch. It handles YouTube videos, podcast episode pages, direct audio files and article URLs."* Input keeps the text; nothing is stored. |
| F2 | **Not content at all** | `rejected`, `reason: not_an_item` | Real and already in the warehouse: `link_5080f43ab77d47d6` is `https://fantasy.premierleague.com/leagues/auto-join/a6fgym`, ingested as an *article* titled `a6fgym`. A league invite code is not an episode. **The resolver must reject it before storage**, and the page says *"That is an FPL league invite link, not a piece of content."* |
| F3 | **Already held** | `duplicate`, with the existing `item_id` | Canonicalise on the YouTube video id, which `creators.py::youtube_id()` already does. `link_04dfb94e32cf04ca` and `link_280d525f5fb46a24` are the same Andy video stored twice. Page: *"Already held — analysed 3 days ago."* and jumps to it. It does not re-transcribe. |
| F4 | No audio and no transcript | `partial`, `reason: no_audio_no_captions` | The item is stored, the title and link render, and the take slot says *"No audio and no captions. The link is saved; nothing was heard."* |
| F5 | **Cannot tell whose it is** | `stored`, `person_key: null`, `attribution_reason` | The default failure of the whole page. See §7 — the item lands in the show band, or in a "your links" band, never guessed onto a person. |
| F6 | ASR backend missing | `blocked`, with `asr.backend_status().install_hint` verbatim | The install hint already exists and is already written for a human. Print it. |
| F7 | Analysis backend unavailable | `partial`, `reason: analysis_unavailable` | Transcript is stored and searchable; the take slot says the model was unreachable and offers *retry analysis* without re-transcribing. |
| F8 | Fetch timeout / 4xx / 5xx | `failed`, with the status | *"The host returned 403. Nothing was stored."* Plus the raw status, because the owner debugs his own pipeline. |
| F9 | User navigates away mid-job | job survives | The job is server-side. Returning to the tab shows it still running; a completed job that finished while away shows a *"1 link finished while you were away"* chip. |

**One rule the flow must not break:** a pasted link's `published_at` is the item's own
publication instant, never the paste instant. `models.ContentItem` already refuses to be
built without a tz-aware `published_at`, and the whole point-in-time discipline rests on
it. A pasted 2024 episode must not become today's take.

---

## 7. The attribution problem, made readable

The brief is right that this is where the design either earns trust or loses it. Here is
the honest state, verified:

- `item_person` **exists as a table and holds 0 rows.** `panel_person` also holds 0 rows.
  The curated YAML has never been upserted into this warehouse. So **step zero of building
  this page is running `panel.upsert_panel` + `attribute_items`** — and the page must
  render correctly before that has happened (state S8).
- Of 594 items, **208 are on a show that has a panel person**; 386 are not. Eighteen
  corpus shows have no panel person at all (Fantasy Football Scout with 144 items,
  Planet FPL, FPL Focal, Gianni Buttice, …). A people-first page that hides 65% of the
  corpus would be a worse page, so it does not hide it — it **bands** it.
- **Solio Analytics has 0 items.** Five panel people, no corpus.

### Four bands, one grid, no guessing

The grid is one visual with four horizontally-ruled bands, each labelled in the row gutter:

1. **People with a squad** (7 today) — full rows, both channels live.
2. **People without a crawled squad** (8 today) — rows drawn with a **dashed row rule**
   and the ownership channel visibly absent, not blank. Gutter: *"squad not crawled"*.
3. **Shows with un-attributed items** — one row per show, drawn in a **different row
   style** (heavier rule, italic label, no face). Gutter reads *"said by the show — no
   host established"*. **FPL BlackBox is the honest case the brief names**: its
   round-table episodes ("Jerk It Out | Gameweek 2 | EP.209") genuinely belong to the
   show, and no attribution basis can honestly resolve them.
4. **Your squad**, pinned last under a rule.

### Every attribution states its basis, inline

Wherever an item is shown under a person, the basis is printed next to it, not in a
tooltip, using the four-value closed set and the stored confidence:

| Basis | Rendered as | Confidence | Shown evidence |
|---|---|---|---|
| `sole_host` | "the only host on this show" | 1.00 | none — it is structural, and the UI says so rather than showing an empty quote |
| `title` | "attributed by title" | 0.90 | the matched span, verbatim |
| `stated` | "he says so in the episode" | 0.95 | the transcript sentence, with `▶` timestamp |
| `manual` | "you assigned this" | 1.00 | who and when |

And the corroboration case is rendered, not collapsed: `item_person` is keyed
`(item_id, person_key, basis)` precisely so an item can carry both `title` and `stated`.
Two badges, not one — that is the corroboration and discarding it would throw away the
strongest attribution the system can produce.

**BlackBox specifically.** Its titles *do* often name a person — *"Az's Team Selection"*,
*"Az's GW1 Team Selection"*, *"How are Az and Pras looking for GW2?"* — so title-basis
attribution will resolve some BlackBox episodes to Az Phillips and leave the round-tables
in band 3. That is the correct, mixed outcome and the page shows both without averaging.

**The alias trap, and why the design must not "fix" it.** BlackBox also publishes
*"Andy's GW1 Team Selection"*. "Andy (Let's Talk FPL)" is on the panel, and is **not** on
BlackBox. `panel_person_show` is the join that stops that title from attributing a
BlackBox episode to the wrong Andy — exactly as `panel.py` says "Harry" in an FPL Focal
title is not FPL Harry. That episode correctly lands in the show band. **A reviewer will
look at it and think the page is broken.** So the show band carries a one-line
explanation: *"the title names someone who is not on this show's panel roster — no
attribution was made."*

---

## 8. Requirement 2 — cross-tab reach

One shared module, `web/dist/js/views/chatter.js`, exporting **one function**:

```
mountChatter(hostNode, playerCode, { compact: bool })
```

backed by **one new panel, `player_chatter`** (§10.3). It renders a compact strip:

```
   CREATOR CHATTER                                              4 people · 14d
   ▣ Pras     ▲ buy   high   “too good to be without”     ▶ 13:32
   ◻ Harry    ◆ watch        “playing left back but…”     ▶ 21:04
   ▣ Andy     ▲ buy   med    …
   ─ owned by 3 of 7 panel members with a crawled squad
```

Mounted in exactly three places, each chosen because the reader is already asking about
one specific player:

1. **`xpoints.js` → `showDetail(r)` drawer**, appended after the per-source pivot. The
   reader is looking at six providers disagreeing about one player; "and here is what
   three humans with verified teams said about him, with timestamps" is the same question
   from the other side.
2. **`template.js` → `showDetail(r)` drawer**, after "Your position on him". The reader is
   deciding whether a differential is real; whether the people he follows *own* him is
   directly on point, and the ownership half of the strip is the half that matters there.
3. **The planner / solver row hover**, as a single-line `n people` chip that expands.

Plus two links in the other direction, so the reach is genuinely bidirectional:

- Every player **column header in the Conviction Grid** links to that player in xPoints.
- Every player in the **Worth Watching ledger** carries an `[ xPoints ↗ ]` action.

**The rule that keeps this from becoming noise:** the strip renders **nothing at all**
when there is no considered take — no empty header, no "0 mentions". A `cue`-only result
renders collapsed as one line: *"2 keyword matches, no considered take — open Creators"*.
A tab that is not about creators does not get to grow a permanent empty creator box.

---

## 9. Every state

| # | State | What renders | Copy |
|---|---|---|---|
| S1 | **Nothing tracked** — panel empty *and* no items | Masthead + a single explain box; no grid frame | *"No people are on the panel. The roster is a curated file, `data/panels/creator_panel_2026_27.yaml`, because an FPL entry id is a claim about a real person and is not derivable from the corpus."* |
| S2 | **Nothing said this week** by a person | Their grid row renders **fully**, with rings (if squad known) and no tint | *"Nothing published in 14 days."* on the row. **This is a feature of the person grain**: a silent person with a known squad is still informative — the row is all QUIET. A show-grain page would have dropped them. |
| S3 | **No transcript** (473 of 594 items) | The item renders with a `show notes only` badge; the take renders with a hatched left border and a caveat | *"Summarised from the episode description, not the episode. `is_scoreable()` refuses to turn show-note calls into claims, so this take is context and never a vote."* |
| S4 | **No verified team** | Row in band 2, ownership channel visibly absent | Two distinct sub-cases, never merged: **(a)** `entry_id` is null *by design* — Solio Analytics, `is_brand: true`: *"A company, not a manager. Four co-founders with four teams; picks attach to the named people below."* **(b)** verified id, not crawled (8 people): *"Entry 2177 is verified but is not in the 2,859-manager crawl pool. Nothing is inferred from his co-hosts."* |
| S5 | **No measured record** — the permanent case | One sentence in the masthead, one collapsed section | *"Nobody has beaten a coin flip. Every earned weight is 0.0; the best Wilson lower bound in the table is 0.21 at n=46, against a 0.50 bar at n≥25. This is a measured result, not a missing one."* Expanding shows the full table, sorted by lower bound, with the `wilson_lo95` vs 0.50 track from today's page (which is good and is kept). |
| S6 | **Panel unavailable** — script not registered | `panelSafe()` path, kept verbatim | *"The `creator_people` panel is not registered on this server yet. This view fills in from it and only from it; nothing is cached or invented."* |
| S7 | **Item attributed to a show, not a person** | Band 3 row, distinct row style | *"Said on FPL BlackBox with no host established. A round-table episode belongs to the show, and there is no default-to-the-first-host rule — a name here would be a guess that downstream code would then trust."* |
| S8 | **`item_person` empty** (today's literal state) | Grid renders with band 1 + 2 rows carrying only ownership, all items in band 3 | *"The panel roster has not been loaded into this warehouse: `panel_person` holds 0 rows. Run the panel upsert and the attribution pass; until then every item belongs to its show."* This is the state a reviewer will actually see first. |
| S9 | **The URL is the audio** (353 of 594 items) | `▶ play audio` and an inline `<audio>` element, not `open episode ↗` | Driven by `content_item_asset.url_basis = 'enclosure'`. 353 items resolve this way; 34 asset rows have a null basis (19 of those still have an enclosure). Copy: *"This feed publishes no episode page — the link is the audio file."* |
| S10 | **No URL at all** | Title renders unlinked with the stored reason | `url_reason` verbatim: `feed_item_has_no_link_alternate_permalink_or_enclosure` → *"The feed offered no link, no alternate, no permalink GUID and no enclosure."* |
| S11 | **Squad crawled but for an older GW** | Rings render with a stamp | *"GW1 squad — GW2 picks have not been crawled."* `fact_manager_pick` holds GW1 only today. The grid header states the squad gameweek, always, next to the take gameweek. |
| S12 | **A show on the panel with no corpus** | Its people render in band 2 with a distinct note | *"Solio Analytics is on the panel but no item from it has ever been ingested. Five people are attached to a show this app does not yet fetch."* |
| S13 | **Grid has no columns** — window too tight | Grid frame renders with axes and an inline message | *"Nobody named a player in 7 days. Widen the window."* — with the widen chips right there, not a blank card. |
| S14 | **Unresolved spoken name** (`code` null) | Cell cannot exist (no column), so it renders in the person route only, as the creator's own words | *"He named someone this app could not resolve to a player: “the Brentford left back”."* Never dropped. |

---

## 10. Panel contract additions, field by field

Today's contract (`CREATOR_PANEL_CONTRACT.md`) is keyed on `creator: str`, a **show name**.
`creator_board.creators[]` is a list of shows. `creator_detail` takes `creator` as its
required param. `creator_score.creator` is a show name. `creator_entry` holds 29 rows and
**every `entry_id` is NULL** because every roster name is a channel name.

**None of that can serve a people-first page**, so B replaces both scripts rather than
extending them. The old ones stay registered until the tab cuts over.

### 10.1 `creator_people` — the roster payload (replaces `creator_board`)

Params: `{ days: int = 14, gw: int|null, as_of: iso|null }`
*(`as_of` is the gap the contract already admits to and this design needs it: "what did
the panel look like before the GW3 deadline" is a question the owner will ask.)*

```jsonc
{
  "as_of": "<iso>",
  "window_days": 14,
  "gw": 3,                         // gameweek the takes are about
  "squad_gw": 1,                   // NEW, REQUIRED. gameweek the OWN channel is from.
  "squad_gw_reason": "fact_manager_pick holds GW1 only; GW2 not yet crawled",
  "panel": {                       // NEW. the roster's own health, for S1/S8
    "loaded": true,
    "n_people": 16, "n_shows": 7,
    "source_file": "data/panels/creator_panel_2026_27.yaml",
    "curated_as_of": "<iso>",
    "reason": null                 // set when loaded=false, e.g. "panel_person is empty"
  },
  "people": [{
    "person_key": "zophar",                    // NEW. the row identity. never a show name.
    "display_name": "Zophar",
    "legal_name": "Utkarsh Dalmia",            // NEW, nullable
    "is_brand": false,                         // NEW. true => never expect an entry
    "handles": { "x": "@Zophar666" },          // NEW
    "own_channel": null,                       // NEW, nullable
    "shows": [{ "creator": "The FPL Wire", "role": "host",
                "n_items_window": 4 }],        // NEW. a person is on N shows.
    "edge_note": "Statistics-led decision making; the Wire's numbers voice.",
    "top10k_finishes": 8, "best_ever_rank": 17, "seasons_played": 17,  // NEW ×3

    "entry": {                                 // EXTENDED
      "entry_id": 2177, "team_name": "Z", "api_name": "Utkarsh D",
      "verified": true,
      "confidence": "high",                    // NEW. conclusive|high|medium|low
      "evidence": "Member of both invite-only FPL Wire leagues; …",  // NEW. verbatim.
      "source_url": "https://fantasy.premierleague.com/api/entry/2177/",
      "checked_utc": "<iso>"
    },
    "entry_reason": null,

    "squad": {                                 // NEW. drives the OWN channel.
      "readable": false,
      "reason": "entry 2177 is verified but not in the crawl pool (2,859 entries)",
      "gw": null,
      "picks": [{ "code": 223094, "name": "Haaland", "pos": "FWD",
                  "multiplier": 2, "is_captain": true, "is_bench": false }]
    },

    "said": [{                                 // NEW. the SAID channel, person-grained.
      "code": 223094, "name": "Haaland",
      "surface_form": "Haaland",               // verbatim, for the code=null case
      "direction": "pos",                      // pos|neg|watch — NOT the raw action
      "action": "buy",
      "conviction": "high",                    // nullable
      "extractor": "llm:claude-opus-5",        // cue vs llm MUST survive to the cell
      "quote": "I think he's just too good to be without this week",
      "start_s": 812.0,
      "deep_link": "https://youtube.com/watch?v=…&t=812s",
      "item_id": "…",
      "basis": "title",                        // NEW. how THIS item reached this person.
      "basis_confidence": 0.9,                 // NEW.
      "published_at": "<iso>"
    }],

    "latest": { "item_id": "…", "title": "…", "url": "…",
                "published_at": "<iso>", "kind": "podcast",
                "text_source": "description",
                "url_basis": "enclosure",      // NEW. drives "play audio" vs "open episode"
                "enclosure_url": "https://…mp3",  // NEW
                "url_reason": null },          // NEW
    "latest_reason": null,

    "record": { "scored": 0, "hits": 0, "hit_rate": null, "wilson_lo95": null,
                "weight": 0.0, "earned": false,
                "grain": "show",               // NEW, REQUIRED. "show" | "person"
                "grain_reason": "creator_score is keyed on show name; this is The FPL Wire's record, not Zophar's",
                "shows_scored": ["The FPL Wire", "Fantasy Football Hub"] }  // NEW
  }],

  "shows_unattributed": [{                     // NEW. band 3 of the grid.
    "creator": "FPL BlackBox",
    "n_items_window": 3,
    "reason": "round-table episodes with no host established in the title or transcript",
    "panel_people": ["mark_sutherns", "az_phillips"],
    "said": [ /* same shape as people[].said, person_key absent */ ],
    "near_miss": [{ "item_id": "…", "matched_alias": "Andy",
                    "reason": "names someone who is not on this show's roster" }]  // NEW
  }],

  "you": {                                     // NEW. the pinned bottom row.
    "readable": true, "gw": 3,
    "picks": [ /* same shape as squad.picks */ ],
    "reason": null
  },

  "insights": [{                               // NEW. requirement 1.
    "code": 251999, "name": "Nico O'Reilly",
    "n_people": 3,
    "by": [{ "person_key": "pras", "quote": "he's playing left back but he's getting into the box",
             "start_s": 862.0, "deep_link": "…", "item_id": "…",
             "extractor": "llm:claude-opus-5", "published_at": "<iso>" }],
    "scoreable": false                          // always false. stated, not implied.
  }],
  "insights_reason": null,                      // e.g. "no analysis carries a watch call in this window"

  "corpus": {                                   // NEW. the footer, from real counts.
    "n_items": 594, "n_items_on_panel_shows": 208,
    "n_transcribed": 10, "n_analyses": 120,
    "n_shows_off_panel": 18,
    "shows_with_no_items": ["Solio Analytics"]
  }
}
```

**Field-by-field rationale for the ones that are load-bearing:**

- `squad_gw` **must be separate from `gw`.** The takes are about GW3; the squads are GW1.
  A grid that shows both channels without stating that they are from different gameweeks
  is lying by adjacency. This is the single most important new field.
- `record.grain` is required because `creator_score` is show-keyed and the page is
  person-keyed. Rendering a show's record on a person's row without saying so would be
  the exact category error B exists to fix. Until `creator_score` grows a `person_key`,
  the badge reads *"The FPL Wire's record — not measured per host"*.
- `basis` + `basis_confidence` travel **on the call**, not just on the item, because the
  grid cell is the unit the reader hovers and the tooltip must be able to say "attributed
  to Zophar by title" without a second fetch.
- `url_basis` / `enclosure_url` / `url_reason` are already in `content_item_asset` and are
  simply not exposed today. 353 items need "play audio".
- `surface_form` beside `code` handles S14 without dropping the call.
- `near_miss` exists so the alias trap (§7) can be explained rather than looking like a bug.
- `panel.loaded` is what makes S8 renderable.

### 10.2 `creator_person` — one person (replaces `creator_detail`)

Params: `{ person_key: str (required), days: int = 90, limit: int = 60, code: int|null }`

- **`person_key`, not `creator`.** A required rename; the old param cannot express
  "Zophar" at all.
- **`code`** — NEW. The brief's "every quote they have on a player". Server-side because
  the person route must not fetch 60 items to filter 3 client-side.

Result adds, over `creator_detail`:

```jsonc
{
  "person": { /* the full people[] entry above */ },
  "reconciliation": {                    // NEW. the person-level say×own summary.
    "backed":  [{ "code": …, "name": "…", "direction": "pos", "multiplier": 1,
                  "quote": "…", "deep_link": "…" }],
    "talk":    [ … ],                    // said, not owned
    "quiet":   [ … ],                    // owned, never said — NO quote, by definition
    "reason":  null                      // set when squad unreadable → talk/quiet impossible
  },
  "items": [{
    "item_id": "…", "title": "…", "url": "…", "url_basis": "enclosure",
    "enclosure_url": "…", "published_at": "<iso>", "kind": "podcast",
    "show": "The FPL Wire",              // NEW. which of this person's shows.
    "text_source": "description",
    "attribution": [{                    // NEW. an ARRAY — corroboration is not collapsed.
      "basis": "title", "confidence": 0.9,
      "evidence": "Free Hit or Wildcard? - Zophar Gameweek 2 Team",
      "attributed_utc": "<iso>"
    }],
    "transcript": {                      // NEW. from transcript_provenance.
      "available": true, "derivation": "asr",
      "engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-turbo",
      "covered_seconds": 1213.6, "audio_seconds": 1214.1,
      "reason": null
    },
    "analysis": { … , "evidence": { "text_source": "description", "thin": true,
                                    "scoreable": false, … } },
    "analysis_reason": "…",
    "claims": [ … as today, plus "basis" … ],
    "insights": [ … ]                    // NEW. the watch calls for this item.
  }]
}
```

`transcript.derivation` matters on the page: an ASR transcript and publisher captions are
different evidence, the same way `cue` and `llm:` are, and `transcript_provenance` already
records which.

### 10.3 `player_chatter` — NEW panel, the cross-tab seam

Params: `{ code: int (required), days: int = 21, as_of: iso|null }`

```jsonc
{
  "code": 223094, "name": "Haaland",
  "said": [{ "person_key": "pras", "display_name": "Pras", "show": "The FPL Wire",
             "direction": "pos", "action": "buy", "conviction": "high",
             "extractor": "llm:claude-opus-5", "quote": "…", "start_s": 812.0,
             "deep_link": "…", "published_at": "<iso>", "basis": "title" }],
  "owned_by": { "n": 3, "of": 7,
                "people": [{ "person_key": "fpl_harry", "multiplier": 2 }],
                "gw": 1,
                "reason": "7 of 15 verified entries are in the crawl pool" },
  "insights": [ … ],
  "empty": false, "reason": null
}
```

One panel, one shape, three call sites. Must be cheap — it is called from a drawer that
opens on a click, so it is a single indexed read per player, not a board rebuild.

### 10.4 Link ingestion — REST, not a panel

Panels are read-only by design (`POST /api/query` is a *guarded read-only* SQL path).
Ingestion mutates, so it follows the `/api/solve` precedent instead:

- `POST /api/links` `{url}` → `202 {job_id, canonical_url, existing_item_id|null}`
- `GET /api/links/{job_id}` → `{state, step, pct, item_id, person_key, attribution_reason,
  error, install_hint}` where `state ∈ queued|resolving|fetching|transcribing|analysing|
  attributing|done|duplicate|rejected|failed|blocked` and `step` carries the human string
  the ledger prints.
- `GET /api/links` → recent jobs, so S/F9 ("finished while you were away") works.

### 10.5 Warehouse-side prerequisites this design assumes

Stated so nobody builds the UI against a promise:

1. `panel.upsert_panel` must be run — `panel_person` holds 0 rows today.
2. `attribute_items` must be run — `item_person` holds 0 rows today.
3. The eight uncrawled verified entries (2177, 6816, 199, 246, 70, 3333334, 2843, 1000001)
   need adding to the manager crawl. Seven of fifteen is a thin OWN channel.
4. `content_insight` (or equivalent) for the 56 `watch` calls (§5).
5. Optional but wanted: `creator_score` grown a `person_key` scope, so `record.grain`
   can eventually say `"person"`.

---

## 11. The three biggest risks, and what would make me abandon this

### Risk 1 — The OWN channel is half-empty, and it is the half the design rests on

**The exposure.** 7 of 15 verified entries are crawled. 8 of 16 rows would render dashed.
`fact_manager_transfer` holds **0 rows for anyone**, so "their transfers" — one of the
brief's explicit deep-dive items — is currently an empty section on every person page.
And `fact_manager_pick` holds **GW1 only**, so on a GW3 page the ownership channel is two
gameweeks stale.

**Why I am taking it anyway.** The gap is a crawl-list edit, not a research problem: the
ids are verified and sitting in the YAML. Seven rows is enough to prove the grid reads
correctly, and every one of the seven is a genuinely useful person (Harry, Andy, Pras,
BigMan, Crellin, Sutherns, Currie).

**What would make me abandon.** If the FPL API will not serve these entries' picks — a
private-account or rate-limit wall — the grid loses one of its two channels and collapses
into a prettier version of today's wire. **Trigger: if fewer than 10 of 16 rows have a
squad within two gameweeks of shipping, cut the grid and ship the person route alone.**
The person route is still a large improvement and does not depend on the OWN channel.

### Risk 2 — Attribution coverage could be so thin that the show band swallows the page

**The exposure.** `item_person` is empty today, so I am designing against a projection.
The bases available are `sole_host` (works cleanly for Harry, Raptor, Andy — 125 items
across three shows) and `title` (works for the Wire and for solo-team BlackBox episodes,
on the evidence of real stored titles). But `stated` requires transcripts and **only 10 of
594 items have one**. Fantasy Football Hub has four people and titles that mostly do not
name them; a lot of its 39 items may resolve to nobody.

**Why I am taking it anyway.** The band-3 design means low coverage degrades gracefully
instead of failing — the page still renders every item, just attributed to the show, which
is *true*. And 386 items are off-panel anyway, so the page was always going to have a
show band.

**What would make me abandon.** If, after `attribute_items` runs, **fewer than a third of
the 208 panel-show items resolve to a person**, the grid's person rows are mostly empty
and the show band is the page. At that point the honest move is to invert: ship a
show-first page with a person *facet*, and revisit when transcript coverage grows enough
for `stated` to carry real weight. **Trigger: person-attribution coverage < 33% of
panel-show items.**

### Risk 3 — Sixteen rows is not a "followed-people reader", it is a spreadsheet with faces

**The exposure.** The owner's verdict on the current page was *"ugly data that's not
presented well"*. A 16×30 grid of glyphs is a defensible answer to that, or it is exactly
the same crime with a nicer legend. Worse: four bands, dashed rows, hatched cells, three
mark types and a captain ring is a lot of encoding to teach in one legend, and legend
complexity is the failure mode a grid dies of. The current page's own consensus board
already carries solid-vs-hatched and it is not obviously legible.

**Mitigation in the design.** The legend is four named quadrants in *words* (BACKED /
TALK / QUIET / silent), not a key of shapes. Hue means one thing (what they said), outline
means one thing (what they own), and no third channel is ever added — that is why watch is
grey and captain is a ring rather than a colour, and it is the same discipline
`creators.css` already documents for its s2↔s4 rejection.

**What would make me abandon.** If, on real data, the grid needs a scroll in both axes to
show the default view, it has failed — a spatial visual you have to pan is a table. **Trigger:
if the default 16 rows × 30 columns does not fit above the fold at 1440×900 without
horizontal scroll, drop the grid to a *ranked list of quadrant memberships* — "3 backed,
5 talk, 2 quiet" per person, expandable — and keep the four-quadrant vocabulary.** The
vocabulary is the idea; the grid is only its best rendering.

---

## 12. Build order

1. `panel.upsert_panel` + `attribute_items` against the warehouse — nothing renders until
   `panel_person` and `item_person` have rows.
2. `creator_people` panel to the §10.1 shape, with `panel.loaded=false` handled first.
3. The person route (`#creators/p/<key>`) — it is useful before the grid exists, and it is
   the fallback if Risk 1 or 3 fires.
4. The Conviction Grid.
5. `player_chatter` + `chatter.js`, mounted in xPoints and Template.
6. `content_insight` + the Worth Watching ledger.
7. `POST /api/links` and the paste flow.

Steps 3, 5, 6 and 7 each stand alone. That is deliberate: if the grid dies, the page is
still a large improvement over what is there now.
