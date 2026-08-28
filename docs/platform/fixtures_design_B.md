# Fixtures, design B: the two-number ticker

> Every number in this document was measured against the live warehouse on
> 2026-08-27 (read-only, `Warehouse.read_copy` / `duckdb read_only=True`).
> Where I re-fitted the model to check something I say so. Nothing here is
> illustrative.

---

## 0. The one sentence

> **Every fixture on this page carries two numbers — how easy it is to score
> in, and how easy it is to keep clean — because the fixtures worth planning
> around are exactly the ones where those two disagree.**

That sentence is printed at the top of the page in the position where xPoints
prints *"Numbers are copied from ingested providers, never modelled here"* and
Creators prints *"Nobody on this page has earned a weight."* It is the whole
design. Everything below is either a consequence of it or an honest admission
about the data underneath it.

The corollary, stated on the page directly under it:

> A single difficulty number is an average of two answers to two different
> questions. The average is never the answer to either one.

---

## 1. What is actually wrong today

Not opinion. Six defects, each verified.

### 1.1 The team column renders empty

`web/dist/js/views/fixtures.js:44` does

```js
row.appendChild(el("td", null, t.team));
```

The panel's `RESULT` schema declares `short_name` and `name` and is
`additionalProperties: false`, so `t.team` is **always `undefined`** and
`el()` renders an empty cell. The tab is a 20-row grid of coloured chips with
a blank first column. That is most of "it's just an ugly graph".

### 1.2 The subtitle literally ends in the word "undefined"

`fixtures.js:24-28` appends `res.ratings_note`. The schema has no
`ratings_note`; staleness text goes to `notes[]`. So the governing line reads:

> Colour = our fitted Dixon-Coles difficulty (dark = hard). undefined

### 1.3 "rows: 0" is a contract mismatch, not missing data

I ran the panel:

```
run_script('fixture_ticker', {'season':'2026-27','horizon':6})
  → KEYS: ['season','gws','row_count','teams','as_of','notes']
    row_count 20, 'rows' in result → False
```

Twenty clubs, six gameweeks, difficulty attached to every cell. The payload is
healthy. But every other substantive panel (`market_watch`, `ownership_eo`,
`projection_table`, `idea_registry`) declares a top-level `rows` array, and
`fixture_ticker`'s schema **forbids** one. Any inventory or generic renderer
counting `result.rows?.length` reports 0 over live data. `panels.py:80-83`
already records this exact failure for `price_radar`:

```python
# Not "table": the result is {risers, fallers, window}, and the table
# renderer reads `rows` -- a key this script's schema FORBIDS. Pinned
# to table, the panel rendered "No data." over real data forever.
```

Fixtures has the same disease and no cure applied.

### 1.4 The colour scale is sequential over data that is diverging

`fixtures.js:49-51` tints with `color-mix(in oklab, var(--s1) ${pct}%, ...)`
where `pct = difficulty * 100`. Two problems compound:

* One hue, no midpoint. There is no visual difference in *kind* between "a bit
  easier than average" and "a bit harder than average" — only in amount of
  blue.
* `difficulty = 0.5` is **not league-average**. It is the midpoint of the
  min–max normalisation over the 40 (club, venue) pairs. Today the league mean
  of that field is not 0.5 and the neutral point is nowhere marked.

### 1.5 The difficulty number throws away the split before the UI ever sees it

`fpl_edge/models/team_goals/ratings_cache.py` computes, per opponent `O` and
venue:

```
lam_O = exp(c + g*[O at home] + attack_O + mean_defence)   # O scores vs an average defence
mu_O  = exp(c + g*[O away]    + mean_attack + defence_O)   # an average attack scores vs O
strength(O, venue) = lam_O - mu_O
difficulty         = (strength - min) / (max - min)
```

`lam_O` **is** defensive difficulty. `mu_O` **is** attacking ease. The module
computes both and then subtracts one from the other. The information the owner
is asking for is destroyed on the line `lam - mu`, one statement before the
artefact is written. Both halves already exist; nobody has to build a model.

### 1.6 Team-keyed intel and creator team-talk are rendered nowhere

`intel_item` holds 784 rows — `set_piece` 215 across 25 team codes,
`press_conference` 71 across 19, `out_of_position` 324, `availability` 174 —
and no view reads any of it at the team grain. `content_insight` has the
columns for exactly this (`topic ∈ {fixture_swing, tactical, …}`,
`entity_kind ∈ {team, fixture, …}`, `horizon_gw`, `horizon_gw_end`) and holds
**0 rows**, because nothing calls `write_insights`.

---

## 2. What the serious tools do, and what I take from each

*(Research section — see §2.7 for the parts I could not verify.)*

### 2.1 FFScout's Season Ticker — the grid is the right spine

The thing FFScout got right two decades ago and everyone copied: **clubs on the
y-axis, gameweeks on the x-axis, one cell per fixture, sortable by the
aggregate of the next N.** It works because fixture planning is a temporal
question and time deserves a spatial axis; because a run is a *horizontal*
pattern the eye reads as one object; and because sorting the rows converts
"who has the best run" from a computation into a look.

**Taken:** the grid, the sortable next-N aggregate, blanks and doubles shown in
place rather than footnoted.

**Rejected:** the 1–5 traffic-light scale. It is a single blended number, it is
red-green (the single worst choice for the ~8% of men with a red-green
deficiency), and its five bins are coarse enough that half the league sits in
bin 3.

### 2.2 FPL's own FDR — the baseline, and why it is not enough

FPL's FDR is derived from the club strength fields FPL publishes and is a
single 1–5 integer per fixture, a function of the opponent and venue only. The
standard critique in the FPL analytics community is threefold: it is coarse
(5 bins for 20 clubs × 2 venues), it is sticky (it barely moves within a
season), and it is blended (a leaky attacking side and a dour defensive side
can share a rating).

**Taken:** the *shape* of the claim — difficulty is a property of the opponent
and the venue, not of your own club. Our fitted artefact keeps that property
deliberately, and I keep it too: colouring Arsenal's whole ticker easy because
Arsenal are good is a category error.

**Rejected:** the blending, the integer bins, and the staleness.

Worth noting: **`dim_team` in this warehouse carries no strength columns at
all** (`season, team_code, team_id, name, short_name, as_of`). We could not
reproduce FPL's FDR today even if we wanted it as a comparison column, and I
am not proposing to ingest it. Our fitted model is strictly better on every
axis and a second, worse number on the page would only invite argument.

### 2.3 The tools that already split attack from defence

The split is not my invention — the better subscription tools (Fantasy Football
Fix's algorithmic FDR, Fantasy Football Hub's Opta-backed ratings) have offered
separate attack- and defence-facing difficulty for years, usually as **two
tickers you toggle between**.

**Taken:** the split itself, and the vocabulary ("attack" / "defence" rather
than "offence" / "clean sheet", because it maps onto how a manager thinks about
the two halves of their squad).

**Rejected — and this is the design's central bet — the toggle.** Two tickers
you switch between makes the *divergence* the hardest thing on the page to see:
you must hold a club's row position and colour in working memory while the
whole grid repaints. The divergent fixtures are the entire reason to split.
Putting them one keystroke apart hides them. §4 puts both numbers in one cell.

### 2.4 Opta / The Analyst — the attack-vs-defence quadrant

The Analyst's team-quality visuals repeatedly use a two-axis scatter (attack
quality against defence quality, with league-average crosshairs) rather than a
one-dimensional power ranking. It works because the quadrant a club sits in
*is* the description — "good going forward, leaky at the back" is a position,
not a sentence you have to write.

**Taken:** the quadrant, as the page's **second** visual (§4.4), applied to
runs rather than clubs: each club plotted at (mean attack-ease of its next H
fixtures, mean defence-ease). Distance from the y = x diagonal is exactly the
divergence the grid shows as texture — the same fact, twice, in two registers.

The repo already owns this idiom: `template.js` builds a scatter with a y = x
diagonal (`.diag { stroke: var(--muted); opacity: .6 }`), so it is a house
component, not a new one.

### 2.5 Dixon–Coles, and the market

The modelling side is settled inside this repo, by measurement, and I defer to
it rather than re-litigating from the literature:

* Dixon & Coles (1997) add a low-score correction `tau` on the four cells
  `(0,0), (0,1), (1,0), (1,1)` plus exponential time-decay weighting.
  `fpl_edge/models/team_goals/dixon_coles.py` implements both and fits `rho`
  jointly (bounded `[-0.30, 0.15]`), with a 400-day half-life tuned
  out-of-sample.
* `docs/platform/odds_derivation.md` §5 closes the `rho` question **negatively**
  for our purposes: out of sample over 1,520 team-fixtures the independent
  Poisson inversion beats both DC variants (pooled clean-sheet Brier 0.16198 vs
  0.16199 and 0.16202), the Brier surface across `rho ∈ [-0.30, +0.30]` spans
  0.000379, and the likelihood-ratio test against `rho = 0` gives p = 0.57.
  `PREFERRED_CS_METHOD = "poisson_indep"`.
* De-vig: `docs/platform/odds_derivation.md` §1 makes the choice per market
  *type*, not per market — Shin (1993) for mutually exclusive and exhaustive
  selections (1X2, totals, correct score, BTTS), per-selection for independent
  yes/no props. `devig_shin` is implemented in `fpl_edge/ingest/odds.py:184`.

**Taken:** the fit as the spine; Shin for the de-vig; `rho = 0` for the market
inversion, against `blend.py`'s `borrow_rho=True` default, because the repo's
own out-of-sample measurement says the borrowed `rho` costs a parameter and
buys nothing.

### 2.6 Market-implied strength, and how far ahead it exists

Rather than cite a blog on how far ahead books quote, I measured it in our own
`fact_odds` at 2026-08-27 23:10 UTC-7, one day before the GW2 deadline:

| kickoff window | gameweek | books quoting `h2h` | books quoting `totals` |
|---|---|---|---|
| 28–31 Aug | GW2 | **20–21** | 5 |
| 4–6 Sep | GW3 | **14–17** | 4 |
| 12 Sep onward | GW4+ | **0** | 0 |

That table is the single most important input to §5. **The market prices two
gameweeks and then stops.** A planning page with a six-to-eight gameweek
horizon cannot be market-driven; it can only be market-*anchored* at the front.
Anyone who proposes a market-weighted ticker across the horizon is proposing to
extrapolate a price that does not exist.

The measured skill is also modest, which sets the ceiling on how much the
market should be allowed to move a cell. From `docs/platform/odds_derivation.md`
§8, out of sample on 1,520 team-fixtures:

| method | Brier | base-rate Brier | skill |
|---|---|---|---|
| `poisson_h2h_ou` (1X2 + totals inversion) | 0.16198 | 0.17182 | **5.7%** |

Its own conclusion: *"a clean-sheet prior is a modest input, not an oracle."*

And it is **biased high above 0.40** — measured 5.9pp too high in the 0.40–0.50
bin and 12.9pp too high in 0.50–0.60 — with the explicit note that
*"a downstream consumer should shade a market-derived clean sheet above 0.40
downward"*, deliberately not applied there. **This page is that downstream
consumer.** §5.5 applies it.

### 2.7 What I could not verify, and am not claiming

I have not personally inspected the current FFScout, Fantasy Football Fix,
Fantasy Football Hub or LiveFPL interfaces while writing this, so §2.1–2.4 are
stated as design *patterns* I am borrowing and arguing for on their merits, not
as a screenshot-accurate audit of any product. Where a claim mattered enough to
build on — how far ahead markets quote, how accurate a market clean-sheet prior
is, whether the low-score correction pays — I replaced the citation with a
measurement against this warehouse or this repo's own backtests, which is
better evidence anyway. Nothing in §4–§11 rests on an unverified claim about a
competitor's UI.

---

## 3. The page

### 3.1 Wireframe

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ FIXTURES                                                                     │
│ Every fixture carries two numbers — how easy it is to score in, and how      │
│ easy it is to keep clean. The fixtures worth planning around are the ones    │
│ where those two disagree.                                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│ INPUTS   ●schedule 1h  ●ratings 19h  ●odds 1m (GW2–3)  ●rich odds 8d         │
│          ●team news 1h  ●lineups 19h  ○creator talk none    [ Fetch now ]    │
│          Odds API: 42 of 150 expansion credits used in 2026-08.              │
├─────────────────────────────────────────────────────────────────────────────┤
│ HORIZON  [GW2][GW3][GW4][GW5][GW6][GW7]  ▸ from GW2, 6 ahead                 │
│ READ AS  ⟨ Both │ Attack │ Defence ⟩                                        │
│ SORT     ⟨ Attack run │ Defence run │ Divergence │ Club ⟩                   │
│ FIELD    [ ] only clubs I own   [✓] show template exposure                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   THE RUN GRID                       ← the central visualisation (§4)        │
│                                                                              │
│          run   EO   GW2      GW3      GW4      GW5      GW6      GW7        │
│                     m+mkt    m+mkt    model    model    model    model      │
│   ┌────┬─────┬────┬────────┬────────┬────────┬────────┬────────┬────────┐   │
│   │MCI │▓▓▓▓ │███ │▄▄COV▄▄ │▀▀wat▀▀ │  ██bur │▄▄FUL▄▄ │  eve   │▄▄LEE▄▄ │   │
│   │TOT │▓▓▓░ │██  │  new   │▄▄NFO▄▄ │  ips   │  ▀CRY▀ │▄▄bou▄▄ │  hul   │   │
│   │HUL │░▓▓▓ │▏   │▀▀cov▀▀ │  AVL   │▄▄sun▀▀ │  BRE   │  tot   │▄▄IPS▄▄ │   │
│   │ ⋮  │     │    │        │        │        │        │        │        │   │
│   └────┴─────┴────┴────────┴────────┴────────┴────────┴────────┴────────┘   │
│   ▄ top band = attack-ease   ▀ bottom band = defence-ease                    │
│   warm ▶ easier than a league-average opponent  │  grey = league average     │
│   cool ▶ harder     UPPERCASE = home, lowercase = away, ░ = thin evidence    │
├─────────────────────────────────────────────────────────────────────────────┤
│   THE RUN MAP  — attack-run (x) vs defence-run (y), y=x diagonal (§4.4)      │
│   Off-diagonal = a specialist run. On-diagonal = a run that is just good     │
│   or just bad.                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│   WHERE THEY DISAGREE  — the ranked divergence table (§4.5)                  │
│   one row per club, one plain sentence each, sorted by |divergence|          │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▸ Provenance, the fit, and what each input is                    (details)   │
└─────────────────────────────────────────────────────────────────────────────┘

  click any cell ──▶  the drilldown drawer (§7), right side, 560px
```

### 3.2 Above the fold, and why

Above the fold: **the governing sentence, the Inputs row, the toolbars, and the
first four or five gameweek columns of the grid.**

The Inputs row is above the grid, not below it and not in a `details` fold.
That is a deliberate break with the house pattern — xPoints and Template put
provenance in the footer. The owner's third requirement is that freshness is
first-class, and a freshness strip under a 20-row grid is a footnote. It also
earns its position honestly: today the row would show a green dot on the model
and an eight-day-old dot on three markets, and **that discrepancy changes how
you should read the grid**, so it has to be read first.

Below the fold, in order: the rest of the grid (a 6-gameweek horizon is 20 rows
and fits; 10 scrolls), the run map, the divergence table, the provenance fold.

### 3.3 Toolbars

Four labelled `div.toolbar` rows, `span.tlabel` first child, exactly the
xPoints/Creators vocabulary.

| label | control | server or client |
|---|---|---|
| `INPUTS` | freshness chips + `Fetch now` button | its own endpoint (§6.4) |
| `HORIZON` | GW chips (`chip gw`), plus a "from GW" select | **server** — refetch |
| `READ AS` | `span.seg`: Both / Attack / Defence | **client** — re-render only |
| `SORT` | `span.seg`: Attack run / Defence run / Divergence / Club | **client** |
| `FIELD` | `label.chk` checkboxes | **client** |

Only `HORIZON` refetches. Everything else is a re-render over the payload
already in hand, which is the xPoints split (`teamSel.onchange → fetchPanel()`,
`searchIn.oninput → renderBody()`) and it keeps the page instant under the
controls a reader actually flicks.

`READ AS` is not a cosmetic toggle: it is the escape hatch for §11.3. Both is
the default; Attack and Defence collapse to a single full-height band per cell,
which is always legible even if the two-band cell turns out to be too dense.

---

## 4. The central visualisation: the two-band run grid

### 4.1 What it is

A 20 × H matrix. Rows are clubs (sortable). Columns are gameweeks. Each cell is
one fixture, and each cell is **split horizontally into two bands**:

```
        ┌──────────────┐
        │▄▄▄▄▄▄▄▄▄▄▄▄▄▄│  top band, 9px   — ATTACK-EASE
        │      CHE     │  label overlays both bands
        │▀▀▀▀▀▀▀▀▀▀▀▀▀▀│  bottom band, 9px — DEFENCE-EASE
        └──────────────┘
          2px of --surface between the bands, the house "ring" idiom
```

Both bands use the **same** diverging ramp, so a cell where the two bands match
reads as a single solid block and a cell where they diverge reads as a stripe.

### 4.2 Every encoding channel, justified

| channel | encodes | why this channel |
|---|---|---|
| **x position** | gameweek | Planning is temporal. Left-to-right time is the only ordering a reader never has to learn. Non-negotiable. |
| **y position** | club, re-sortable | Sorting the rows turns "who has the best run" from a computation into a look. This is the single most valuable interaction on the page and no colour channel can do it. |
| **hue** (2 hues + neutral) | *sign* of ease vs a league-average opponent | Diverging data needs a diverging scale. `--s2` warm = easier, `--faint` grey = exactly league average, `--s1` cool = harder. Two hues plus a neutral is the repo's stated rule and blue↔orange is the standard colour-vision-safe diverging pair; red↔green is not. |
| **saturation within hue** | *magnitude* of ease | Ordered magnitude on an ordered visual variable. Ramp is symmetric about the neutral point, clipped at ±0.45 log (§5.6). |
| **horizontal split** | which of the two questions | The whole design. Divergence becomes **texture**, and texture is pre-attentive — you find every two-tone cell in one sweep without reading a single number. Two separate grids cannot do this (§2.3). |
| **text case** | venue (UPPER = home, lower = away) | Venue is worth ±0.0945 log-goals in the fit — about a fifth of the opponent effect's span (§5.3). It does not deserve a channel that competes with the opponent. Case is free, it is already the panel's `label` convention, and it matches the Telegram fixture grid. |
| **texture (dotted overlay)** | thin evidence | House rule: *"weak evidence = hollow, never a different hue."* Uses the existing `repeating-linear-gradient` idiom from `.cx-cell.cue`. |
| **gutter bar length** | run aggregate, template exposure | Length compares well within a column and does not compete with the cell hues. Deliberately **not** folded into the cell colour — see §5.7. |
| **cell stacking** | double gameweek | Two cells in one column, not a "(2)" badge. A double is two fixtures and should occupy two fixtures' worth of space. |

Deliberately unused: shape, size, opacity, and any third hue. The repo's
palette validation covers `--s1`/`--s2` on both surfaces; a third series colour
in a dense matrix would not survive the CVD checks and there is nothing left to
encode.

### 4.3 What it makes obvious that a table cannot

Three things, in descending order of how much they matter:

1. **The divergent fixtures, found by texture.** A 20 × 6 grid is 120 cells,
   240 numbers. In a table you would compare two numeric columns per cell to
   find the disagreements. In the grid every disagreement is a stripe and every
   agreement is a block; you find all of them in one sweep. This is the reason
   the design exists and it is the reason the split is *within* the cell rather
   than across two grids.

2. **Run shape over time.** A club whose top band warms across GW5–GW7 while
   its bottom band stays cool is telling you to buy the attacker in three
   weeks and never the defender. That is a horizontal gradient — an object the
   eye assembles for free and a table cannot express at all.

3. **Structural rows.** When a whole row is warm-on-top and cool-on-bottom, the
   fact is about that club's schedule, not about any one fixture. Row-level
   pattern is a property of the grid, not of its cells.

Verified example, from the live fit (§5.2 has the full table): **Hull City** and
**Tottenham** currently sit at opposite corners and today's blended artefact
calls them both "middling". Facing Hull at home, a league-average side expects
**1.23 goals** (a *hard* attacking fixture) and a **36.7%** clean sheet (an
*easy* defensive one). Facing Spurs at home: **1.75 goals** and **28.5%** —
the exact mirror. The blend renders Hull 0.594/0.428 and Spurs 0.546/0.325,
which reads as "Spurs slightly easier". The two-band cell shows Hull as
cool-over-warm and Spurs as warm-over-cool, and the answer is immediate.

### 4.4 Alternatives considered, and why they lose

| alternative | why not |
|---|---|
| **Two side-by-side or toggled grids** (the subscription-tool pattern) | Divergence — the entire point — becomes the hardest thing to see. Requires holding a row position and a colour in working memory across a repaint or a saccade. Also doubles horizontal space and forces a scroll at H = 6. |
| **One grid, blended colour, small divergence glyph** | Keeps the blend as the primary read, which is precisely the failure being fixed. The glyph would be the important information rendered as a footnote. |
| **Bivariate 3×3 choropleth** (the classic bivariate-map scheme) | The most information-dense option and genuinely tempting. Rejected: nine colours cannot be decoded without constant legend reference, the corner mixtures fail CVD separation, and the repo's rule is two hues plus a neutral. The horizontal split gets the same information from two independently legible scales. |
| **Scatter / quadrant as the spine** | Collapses the time axis. Planning is about *when*, and the owner's first ask is "which teams are playing whom". Kept as the second visual (below). |
| **Slope or parallel-coordinates chart of run quality** | Cannot show who you play. Half the brief. |
| **Small multiples: one sparkline per club** | 20 sparklines is 20 separate coordinate systems; cross-club comparison, which is the whole task, becomes impossible. |

### 4.5 The two supporting visuals

**The run map** (`svg.fixmap`, the `template.js` scatter idiom). x = mean
attack-ease over the horizon, y = mean defence-ease, y = x diagonal drawn in
`--muted` at 0.6 opacity, crosshairs at (0, 0) labelled "league-average run".
Mark = club short name, `--ink` if you own a player there, `--muted` otherwise.
Distance from the diagonal is the divergence; the quadrant is the description.
From the live fit over GW2–GW7, the extremes are Brighton (attack −0.016,
defence +0.081 — a clean-sheet run, not an attacking one) and Chelsea
(−0.114 / −0.024).

**The divergence table.** One row per club, sorted by |attack-run − defence-run|
descending, each with one generated sentence traceable to the row:

> **BHA** — six fixtures. The defence run is 0.097 better than the attack run:
> buy the defender, not the forward. Template exposure 62.0%.

No new data; the same numbers as the grid, in the register the owner asked for
("important information should be highlighted"). It is the page's answer to
"who has easier runs" for a reader who wants a list rather than a picture.

---

## 5. The difficulty model

### 5.1 The core move: stop subtracting

`ratings_cache.py` already computes both halves. The change is to publish them
instead of their difference. For team `T` facing opponent `O`:

```
attack_ease (T, O, venue) = (defence_O − mean_defence) + venue_term
defence_ease(T, O, venue) = −(attack_O  − mean_attack ) + venue_term

venue_term = +g/2  if T is at home
             −g/2  if T is away          (g = fit.home_adv)
```

Both are in **log-goal-ratio** units, both centred on zero, both signed so that
**positive = easier for me**. The sign flip on `defence_ease` is because the
Dixon–Coles `defence` parameter is a *leakiness* parameter (positive = concedes
more), stated in the module docstring; `attack_O` is the opponent's threat, and
threat is the thing that makes a clean sheet hard.

Derivation of the venue term, so nothing is invented: in the fit,

```
log lambda_home = c + g + attack_h + defence_a
log lambda_away = c +     attack_a + defence_h
```

so being at home multiplies my goals by `exp(g)` and divides my goals conceded
by `exp(g)`, symmetrically. Measured against the average team-match (which
carries `g/2` by symmetry) that is `+g/2` on both eases at home and `−g/2`
away. There is no free constant.

A consequence worth stating on the page: **the two eases are venue-invariant
and the venue is a single global constant.** The model does not estimate a
club-specific home advantage. That is a real limitation (Old Trafford and the
Amex are not the same building) and the drilldown says so.

### 5.2 The numbers this produces today

Re-fitted read-only at 2026-08-27: `c = 0.2386`, `g = 0.1890`
(`exp(g) = 1.208`), `rho = −0.0739`, half-life 400 days, 1,530 matches,
effective n 512.2, converged. League-average goals: 1.472 at home, 1.218 away.

Facing each club, what a **league-average side** expects (natural units, from
the score matrix):

| opponent | away at them: xGF / xGA / CS% | home to them: xGF / xGA / CS% | attack-ease | defence-ease |
|---|---|---|---|---|
| MCI | 0.90 / 2.11 / 12.1% | 1.09 / 1.75 / 17.4% | −0.304 | −0.362 |
| ARS | 0.75 / 1.98 / 13.9% | 0.91 / 1.64 / 19.5% | −0.481 | −0.295 |
| LIV | 1.16 / 1.99 / 13.7% | 1.40 / 1.65 / 19.3% | −0.048 | −0.301 |
| NEW | 1.25 / 1.74 / 17.6% | 1.51 / 1.44 / 23.8% | +0.025 | −0.165 |
| **TOT** | 1.44 / 1.52 / 21.9% | **1.75** / 1.26 / 28.5% | **+0.171** | −0.030 |
| LEE | 1.30 / 1.38 / 25.1% | 1.57 / 1.14 / 31.9% | +0.064 | +0.064 |
| CRY | 1.24 / 1.28 / 27.8% | 1.50 / 1.06 / 34.7% | +0.018 | +0.139 |
| EVE | 1.13 / 1.24 / 29.0% | 1.36 / 1.02 / 35.9% | −0.078 | +0.174 |
| SUN | 1.17 / 1.20 / 30.2% | 1.42 / 0.99 / 37.1% | −0.037 | +0.206 |
| **HUL** | 1.02 / 1.21 / 29.8% | **1.23** / 1.00 / **36.7%** | **−0.181** | **+0.196** |
| IPS | 1.83 / 1.12 / 32.7% | 2.21 / 0.92 / 39.7% | +0.406 | +0.276 |
| COV | 1.71 / 0.90 / 40.5% | 2.06 / 0.75 / 47.3% | +0.336 | +0.488 |

*(the remaining eight clubs sit between NEW and LEE and are omitted for space)*

**Why the split is not decoration.** Across the 20 clubs the fitted attack and
defence parameters correlate at **r = −0.635** (good attacks tend to have good
defences), so attack-ease and defence-ease correlate at **+0.635**. That is
`r² = 0.40`: **60% of the variance in one is not explained by the other.** A
blended number discards it. Hull is the case in point — a *hard* team to score
against (−0.181) and a *safe* team to defend against (+0.196), the exact
"leaky AND hard to score against" shape inverted, and today's ticker paints it
a single mid-blue.

Aggregated over GW2–GW7 the two runs correlate more (Spearman 0.856 — averaging
six fixtures washes out club-level idiosyncrasy), which is itself worth saying
on the page: **the divergence lives in individual fixtures more than in whole
runs.** That is an argument for the cell-level split, not against it.

### 5.3 Scale of the venue effect, stated so nobody over-reads it

`exp(g) = 1.208` — home is worth **+20.8%** goals in total, `±9.9%` each way.
Attack-ease spans 0.887 across the league (ARS −0.481 to IPS +0.406) — **4.7×
the whole home-away swing.** Defence-ease spans 0.850. Who you play matters
about five times as much as where. The legend says so in one line.

### 5.4 The market layer: anchored at the front, absent behind

The horizon constraint is measured, not assumed (§2.6): 20–21 books on GW2,
14–17 on GW3, **zero** on GW4+. So:

```
ease_final = ease_model + w_market · delta_market
```

where `delta_market` is a **per-fixture** correction, never a change to a club
rating. Given the market's implied goal rates for one fixture,

```
delta_attack (T)  = log( lambda_market_T / lambda_model_T )
delta_defence(T)  = −log( mu_market_T / mu_model_T )
```

Applying it per fixture and not per club is the point: one bookmaker's read on
one match must not repaint six columns.

**How the market rates are obtained.** Read `fact_odds` for the fixture's
`h2h` and `totals`, de-vig **each book separately with Shin**
(`fpl_edge/ingest/odds.py:184`; the repo's own rule for mutually exclusive and
exhaustive selections), average the fair probabilities across books, then
invert to `(lambda, mu)` with `market.invert_odds` against the score matrix at
**`rho = 0`** — overriding `blend.py`'s `borrow_rho=True`, on the authority of
`odds_derivation.md` §5's out-of-sample result that the borrowed correction
costs a parameter and buys nothing.

**The weight.**

```
w_market = w0(gw) · f_books · f_stale · f_residual
```

| factor | definition | today's value |
|---|---|---|
| `w0(gw)` | 0.50 for the next deadline's GW, 0.25 for the one after, **0 beyond** | GW2 0.50, GW3 0.25, GW4+ 0 |
| `f_books` | `min(1, n_books / 12)` — measured liquidity, not a guess | GW2 21/12 → 1.0; GW3 15/12 → 1.0 |
| `f_stale` | `exp(−age_hours / 48)` | odds re-landed 1 minute ago → 1.00 |
| `f_residual` | 1 below `Inversion.residual` 0.02, tapering to 0 at 0.06 | per fixture |

`f_books` is the honest expression of the fact that a price quoted by 21 books
is evidence and a price quoted by 4 is a rumour. `f_stale` is what makes the
freshness requirement *mechanical* rather than decorative: when the odds go
stale the market silently stops moving the grid, and the column header stops
saying "+ market", so the reader sees the mechanism rather than a warning
badge.

The ceiling of 0.50 is set by measured skill, not taste: the market
clean-sheet prior scores a **5.7% Brier skill** over the base rate out of
sample (§2.6). A modest edge earns a modest weight.

### 5.5 Two things about the market data that must be said out loud

**There is no clean-sheet market. There never has been.** All **3,160** rows in
`fact_odds` with `market = 'clean_sheet'` carry `bookmaker = 'derived#poisson'`:

```sql
select count(*) from fact_odds
 where market='clean_sheet' and bookmaker <> 'derived#poisson';   -- 0
```

Those are our own Poisson derivation written back into the quotes table. The
owner asked for clean-sheet odds; the honest answer is that the warehouse has
never held one and what looks like one is our model wearing a bookmaker's name.
**`derived#*` and `fair#*` books are excluded from `n_books` and can never
contribute to `delta_market`.** Counting them would be the model marking its own
homework, and it would make `f_books` a lie.

**Market clean sheets above 0.40 are shaded down.** `odds_derivation.md` §8
measures the market prior as biased high in every bin — 5.9pp at 0.40–0.50,
12.9pp at 0.50–0.60 — and explicitly leaves the correction to a downstream
consumer. We are it. Where a market-anchored `p_clean_sheet` exceeds 0.40, it
is shrunk toward the measured realised rate by the fitted bin bias, the shaded
value is what the page shows, and the drilldown prints both with the reason.

### 5.6 Form

An EWMA over completed matches this season, half-life 5 matches:

```
delta_form_attack = k · ( log(xG_against_pg observed) − log(model expected) )
k = n_eff / (n_eff + 6)
```

applied to the **club rating**, not the fixture — form is a claim about a club.

**Today `k` ≈ 0.14 for every club and the form correction is effectively zero,
because exactly one league match has been played.** The page says that in the
Inputs row rather than showing a form column that looks meaningful. Form
becomes a real input around GW6–GW8 and the weight rises on its own; nothing
needs changing when it does.

Two traps for whoever builds this:

* **Team xGA is NOT the sum of your own players' `expected_goals_conceded`.**
  That column in `fact_player_fixture` is a per-player defensive-exposure
  metric; summing it over a squad gives Tottenham an "xGA" of **42.59 over two
  matches**. Team xGA is the *opponent's* summed `expected_goals`. I hit this
  while checking the data and it would have shipped silently.
* `fact_player_match_stats` (313 rows, 2026-27 only) carries richer inputs —
  `xg`, `xgot`, `touches_opposition_box`, `chances_created` — but has no
  history. Use `fact_player_fixture` (113,870 rows across four seasons) for
  anything that needs a baseline.

### 5.7 What is deliberately *not* blended in

Per `docs/platform/rank_objectives.md` §0, the engine optimises `P(rank ≤ 10k)`,
not points, and the decision-relevant quantities are relative to the top-10k
pace: `m = E[my score − pace]` and `s = SD[my score − pace]`, with
`s² = σ_mine² + σ_pace² − 2·Cov(mine, pace)`. A great fixture run at a club the
field is already loaded on co-moves with the pace and adds almost nothing to
`m`. **Fixture ease is only an edge to the extent the field is not already
holding it.**

So the page shows template exposure — summed `selected_by_pct` per club,
computed fresh from `fact_player_state` (today: ARS 221.4, MCI 184.3, MUN
165.8, CHE 149.7 … COV 32.6, NEW 21.6) — as a **gutter bar** and a column in
the divergence table.

It is **not** folded into the difficulty number. Difficulty is a physical claim
about goals; ownership is a strategic modifier; blending them would repeat
exactly the mistake this page exists to fix. **Every blend destroys the
information that makes a decision possible** — that is the argument for
splitting attack from defence, and it is the same argument for keeping
ownership beside the number instead of inside it.

### 5.8 The display scale

Colour is driven by the log-ratio (symmetric, so the diverging ramp is honest);
every number the reader *sees* is in natural units (xGF, xGA, clean-sheet %),
because "1.75 goals" is a thing you can picture and "+0.171 log" is not.

Ramp clipped at **±0.45 log** (`×0.64` to `×1.57`). Today that puts Arsenal's
attack-ease (−0.481) and Coventry's defence-ease (+0.488) at full saturation
and everything else inside the ramp. The clip is a display constant,
recomputed once per season as the 97.5th percentile of |ease| over the league,
carried in the payload as `scale.clip`, and printed in the legend. It is never
recomputed per horizon — the scale must be a property of the league, which is
the one thing the current min–max normalisation gets right.

### 5.9 Source-missing and source-stale rules

| condition | behaviour |
|---|---|
| No market quote for the fixture | `w_market = 0`; column header shows "model"; drilldown says no book quotes this yet |
| Market older than 96h | `f_stale = exp(−2) = 0.135`, so `w_market ≤ 0.07` — effectively off, and visibly so |
| Only `derived#*` / `fair#*` books | treated as **no market**, with the sentence in §5.5 |
| `Inversion.residual` > 0.06 | market dropped for that fixture; drilldown prints the residual and says the quotes are not consistent with any bivariate Poisson |
| `fixture_difficulty.parquet` absent | schedule-only grid, no bands, neutral cells, honest empty copy |
| parquet older than 7 days (`DIFFICULTY_STALE_DAYS`) | served with the existing note **and** confidence dimmed league-wide |
| parquet present but without the split columns | fall back to single-band blended cells + banner (§8.5) |
| opponent with < 4 decay-weighted matches | dotted texture; drilldown names the prior |
| fewer than 3 league matches played all season | form section suppressed entirely |

---

## 6. Freshness and triggers

This is the section the owner said was first-class, so it gets a diagnosis
before it gets a design.

### 6.1 What is actually stale, right now, and why

Measured at 2026-08-27 23:10 (UTC−7):

| input | source | age | state |
|---|---|---|---|
| Schedule (`fact_fixture`) | `fpl_api` | ~19h | fresh |
| Fitted ratings (parquet) | `ratings_cache` | ~19h | fresh |
| `h2h`, `totals` | `odds_api` + `odds_football_data` | **1 minute** | fresh, GW2–GW3 |
| `clean_sheet` | `derived#poisson` only | — | **not a market at all** |
| `correct_score`, `btts`, `team_totals` | `odds_api` expansion | **8 days** | stale |
| Team news (`fact_player_state`) | `fpl_api` | ~19h | fresh |
| Predicted lineups | `rotowire` | ~19h, GW2 only | fresh |
| Creator team-talk (`content_insight`) | — | — | **0 rows, never wired** |

Note the warehouse moved *while I was reading it* — the h2h/totals pull landed
mid-audit. Which is itself a design requirement: **staleness must be computed
per market at read time, never cached into a banner.**

### 6.2 Three located defects

**(a) A credit-capped odds run reports success.**
`scripts/ingest_odds.py:104-121`, `_run_odds_api`, catches
`CreditBudgetExceeded`, prints `"  odds-api REFUSED: …"` and **returns None**.
`main()` still `return 0`. `deadline_dag.run_step` records
`ok = proc.returncode == 0` → **True**. A green step that fetched nothing. This
is precisely the failure mode the brief describes.

*Fix:* `_run_odds_api` returns a status; `main()` exits non-zero when
`--odds-api` was requested and spent nothing for budget reasons. A step that
fetched nothing is not `ok`.

**(b) The DAG never asks for the expansion markets at all.**
`deadline_dag.presser_projection_refresh` runs
`scripts/ingest_odds.py --fixtures` — the free football-data path — and never
passes `--odds-api`. So `correct_score`, `btts` and `team_totals` are refreshed
by nothing. Fixing (a) alone would not have helped.

*Fix:* add the `--odds-api` leg to `presser_projection_refresh` (T−30h), and add
a second odds task at **T−6h**, after lineups leak and markets sharpen.

**(c) It is not a money problem.** `data/odds_expansion_ledger.json`:

```json
{"2026-08": {"spent": 42, "runs": [{"at": "2026-08-20T06:41:46Z", "credits": 42,
   "note": "extra markets, 10 events, 2279 rows"}]}}
```

`EXPANSION_MONTHLY_CAP = 150` inside the key's 500/month. **42 spent, 108
sitting unused**, and a measured full-gameweek expansion run costs 42
(`odds_derivation.md` §10) — 22 with `--no-team-totals`. Two more full runs
this month are already paid for. The markets are stale because nobody asked,
not because the budget ran out.

### 6.3 How the page tells you

**Per-input chips, not one page timestamp.** A labelled `INPUTS` toolbar row
with one `chip` per input, each carrying the existing `span.freshdot` and the
existing `ageInfo()` thresholds verbatim — good < 36h, warn < 72h, bad beyond —
because a dot has to mean one thing across the app. Reusing `ageInfo` rather
than writing a fixtures-specific one is deliberate.

Three additions to the vocabulary:

* **`○` hollow dot = absent, not old.** `content_insight` has 0 rows; that is a
  different state from stale and colouring it red would be a lie about what
  went wrong.
* **The chip names the source key**, matching `raw_fetch.source`, so a reader
  who wants to chase it has the string.
* **The credit line**, because it is the binding constraint and hiding it makes
  "Fetch now" feel free when it is not:
  `Odds API: 42 of 150 expansion credits used in 2026-08; a full refresh costs 42.`

**Freshness is wired into the model, not just displayed.** `f_stale` (§5.4)
means a stale market stops moving the grid on its own, and the GW column
headers drop the `+ mkt` badge when it does. The reader can see *that the
number changed shape*, not merely that a dot went orange.

### 6.4 "Fetch now"

Follows the existing `POST /api/solve` + `GET /api/solve/status` pattern in
`fpl_edge/platform/solve_runner.py` exactly — detached child in its own
session, wrapped as `sh -c '… ; echo $? > <log>.exit'` so completion is durable
across a server restart, one at a time, liveness checked against the recorded
pid rather than the status file's word for it, logs and status under
`data/warehouse/jobs/`.

```
POST /api/refresh/fixtures        → 202 {job_id}   (409 + status if one runs)
GET  /api/refresh/fixtures/status → {state, steps[], skipped[], credits, log_tail}
```

Steps, in order, cheapest first, each its own process:

| # | step | cost | skip condition |
|---|---|---|---|
| 1 | `scripts/ingest_live.py` | free | never |
| 2 | `scripts/ingest_odds.py --fixtures` | free | never |
| 3 | `scripts/ingest_odds.py --odds-api --max-credits N` | **42 credits** | skipped if ledger headroom < 42, or the expansion markets are < 6h old |
| 4 | `python -m fpl_edge.ingest.odds_derived` | free | skipped if step 3 skipped and nothing new landed |
| 5 | `python -m fpl_edge.models.team_goals.ratings_cache --season …` | ~1 min CPU | never — it is the spine |

Four rules that make it honest rather than a spinner:

1. **It refuses loudly.** If step 3 is skipped, the status names the step, the
   reason and the ledger arithmetic. The market chip stays orange and **the
   button does not turn green.** A refresh that did not refresh must not look
   like one that did.
2. **Rate-limited to one paid pull per 6h**, enforced against the ledger, so the
   owner cannot burn 108 credits with an itchy finger before a deadline.
3. **Idempotent.** Every step is an append into a PIT table or an overwrite of
   a cache; running it twice costs credits, not correctness.
4. **The panel re-fetches on completion** and the Inputs row re-renders. No
   manual reload — the whole point is to close the loop.

### 6.5 The pipeline, as it should stand

| when | task | what | cost |
|---|---|---|---|
| nightly 02:00 UK | `price_radar` *(exists)* | prices | free |
| nightly 03:00 | **new** `odds_free_refresh` | football-data fixtures CSV + FPL bootstrap | free |
| T−30h | `presser_projection_refresh` *(exists, extended)* | + `--odds-api` leg, + refit | **42** |
| **T−6h** | **new** `odds_sharpen` | `--odds-api` again after lineups leak, + `odds_derived`, + refit | **42** |
| post-gameweek | `post_gw` *(exists)* | refit `fixture_difficulty.parquet` | free |
| on demand | `POST /api/refresh/fixtures` | §6.4 | 0 or 42 |

Two paid pulls per gameweek = 84 credits. Against `EXPANSION_MONTHLY_CAP = 150`
that fits a gameweek and a half per month, which is **not enough** — so the
recommendation is to raise the expansion cap to 400 (still inside the key's
500) or run T−6h with `--no-team-totals` at 22 credits, giving 64/gameweek and
comfortable headroom. State the arithmetic on the page; do not let the cap be
discovered by a silent skip.

**One monitor, not a dashboard.** Register a `fixtures_freshness` monitor in
the existing registry (`GET /api/monitors`, `POST /api/monitors/{name}/run`):
per-input maximum age, breach → one inbox delivery naming the input, its age,
and the command that fixes it. This is the alarm that would have caught defects
(a) and (b) eight days ago.

---

## 7. The drilldown

Click any cell → the house right-side drawer (`aside.drawer.fx-drawer`,
`width: min(560px, 96vw)`, `.open` transform, Escape to close, `drawerHead()`),
identical in mechanics to `creators.js::openPlayer`. Data comes from a separate
memoised panel call (`fixture_detail`, cached by `fixture_id` in a `Map`, with
the error stored *as a value* the way `detailFor` does it), so opening a drawer
never blocks or refetches the grid.

Sections, most decision-relevant first:

**1. Head.** `Arsenal v Chelsea — GW3 · Sun 6 Sep 15:30 · Arsenal at home`

**2. The two answers, in natural units, on the same ramp as the grid.**

> **Attacking Arsenal assets** — a league-average attack expects **1.47 goals**
> here. League average at home is 1.47. `[ ▓▓▓▓▓█▓▓▓ ]` **average**
>
> **Arsenal defensive assets** — **25.3%** clean sheet. League average at home
> is 26.5%. `[ ▓▓▓▓█▓▓▓▓ ]` **slightly harder than average**

The little ramp is literally the grid's scale, so the drawer and the grid are
the same object at two zoom levels.

**3. Where the number comes from.** The house `div.identity` idiom, as an
audit trail:

```
log xGF  =   base      +  Arsenal attack  +  Chelsea leakiness  +  venue
           (−0.146)         (+0.295)            (+0.002)          (+0.094)
```

Every term traceable to a fitted parameter. This is the "reasoning" the brief
asks for, and it is why a reader can disagree with the number specifically
rather than generally.

**4. Market.** Per-book de-vigged 1X2 and Over 2.5 for this fixture, the
overround before de-vig, `n_books`, the inversion residual, the age, and the
applied `w_market`. If the only "book" is `derived#poisson`, the section says
so in the words of §5.5 rather than showing a number.

**5. Availability.** From `fact_player_state` (fresh, ~19h): every player at
either club with `status <> 'a'`, showing `news`, `chance_of_playing_next_round`
and the age of `news_added`, **ordered by `selected_by_pct` descending** so the
ones that move the field come first. Today Nott'm Forest carry 10.7pp of
flagged ownership and Chelsea 5.8pp — that is a fixture-level fact.

Deliberately preferred over `intel_item.kind='availability'` (174 rows), whose
`team_code` is **NULL on every row** — it must be joined
`player_code → dim_player.team_code` — and which is a re-statement of the same
FPL bootstrap field one hop further from the source.

**6. Predicted lineup.** `fact_predicted_lineup` (rotowire, refreshed ~19h ago,
587 rows for GW2), the XI with its `certainty`, plus how many of the club's
high-ownership players are predicted to start. Empty state: *"Rotowire has not
published a GW3 XI yet; they usually land 24–48h before kickoff."*

**7. Set pieces.** `set_piece_duty` for 2026-27 — 215 rows across all 20 clubs,
as of 2026-08-25: penalties 73, indirect corners 85, direct free kicks 57 —
plus the 17 `set_piece_change` rows detected this season, each with its
`delta_goals_per_game`.

Labelled accurately: this is **FPL's own stated order**, not scout observation.
And a warning for whoever builds it: `intel_item.kind='set_piece'` looks like
the right table and is not — **209 of its 215 rows come from
`vaastav:players_raw.csv`**, i.e. end-of-season snapshot diffs from *previous*
seasons, dated to a season boundary with the exact date unknown. Rendering
those as this week's news would be a fabrication. Use `set_piece_duty` and
`set_piece_change`.

**8. Shape.** `formation_observation` — 20 rows, one per club, GW1 only. Show
both clubs' observed shape (`shape`, `n_def`, `n_mid`, `n_fwd`) with "n = 1
match" printed beside it. Thin, honest, and it is the only tactical
observation the warehouse holds.

**9. Previous meetings.** `fact_fixture` finished rows for the pairing across
the four stored seasons — Arsenal–Chelsea has eight, with scores and dates.
Labelled **context, not evidence**, in one line:

> The model has no pairing term. Dixon–Coles parameterises a fixture as
> (home attack, away defence, venue); eight meetings between four different
> squads is noise, and none of it moved the numbers above.

Including it and then saying it does not count is better than omitting it,
because the owner asked for it and the alternative is a reader assuming it
secretly counts.

**10. Creator team-talk.** `content_insight` where
`topic IN ('fixture_swing','tactical')` and
`entity_kind IN ('team','fixture')` for either club, rendered with the Creators
tab's exact `quoteBlock()` primitive — verbatim quote, evidence-tier badge,
timestamped source link, `start_s` deep-link into the video. Same component,
same file, not a copy.

**Today this section is always empty and must say why**, not just "nothing
found":

> Creator team-talk extraction is built but not wired: `content_insight` holds
> 0 rows. Nothing is missing here because nobody said it — nothing has been
> asked for yet.

### 7.1 What I need from `content_insight`, field by field

Three changes, all small, none in my file:

1. **`team_code INTEGER NULL`, resolved.** Today only `player_code` is
   resolved. `entity_ref` is explicitly documented as *"a grouping key … NOT a
   foreign key"* and `analyze.py` states *"there is no team-code resolver in
   this package and this function does not pretend to be one."* I need one:
   fold `entity_name` through `fpl_edge.ingest.rivals.names.norm` against
   `dim_team.name` and `short_name`, plus a small alias table (Spurs, Man U,
   Wolves, the Toffees…). Without it, team talk cannot join to a row of this
   grid.
2. **The pipeline wired.** `fpl_edge/ingest/content/pipeline.py` never mentions
   insights and `write_insights` has no caller. One call in the `ingest`
   command turns 0 rows into a populated table.
3. **`horizon_gw` / `horizon_gw_end` actually populated** when a speaker names a
   range. These already exist in the schema and are **the highest-value fields
   on this page**: they are what lets *"Brighton's fixtures turn from GW7"*
   attach to a **column range**, not merely to a club. A team-level claim with
   a horizon is a fixture-grid annotation; without one it is a sticky note.

Nothing else. `quote`, `confidence`, `start_s`, `published_at`, `extractor`,
`creator` all already exist and are what the drawer renders.

---

## 8. Every empty and degraded state

| # | condition | what the page does |
|---|---|---|
| 1 | No `dim_team` for the season | existing `empty()`: *"Run `make ingest` …"* |
| 2 | No deadlines and no played fixtures | existing `empty()` |
| 3 | No fixtures in GW*a*–GW*b* | existing `empty()` |
| 4 | No club has a fixture in the window | existing `empty()` |
| 5 | Parquet absent | Schedule-only grid: opponent labels, venue case, **no bands, no colour**. Sub: *"No fitted ratings — this is the schedule, which is a fact, and nothing else."* (and the `undefined` bug of §1.2 is fixed) |
| 6 | Parquet present, older than 7 days | Numbers served with the existing note; confidence dimmed league-wide; ratings chip orange |
| 7 | Parquet present **without the split columns** | Single-band blended cells + banner: *"This artefact predates the attack/defence split; re-run the refit."* Matters because the parquet is overwritten wholesale, so a rollback produces exactly this |
| 8 | No market for a fixture | `w_market = 0`; header reads "model"; drawer says which books are missing |
| 9 | Market stale (> 96h) | `f_stale` collapses the weight; header drops `+ mkt`; chip red with the age |
| 10 | Market present but only `derived#*` / `fair#*` | Treated as absent, with §5.5's sentence in full |
| 11 | Inversion residual too high | Market dropped for that fixture; drawer prints the residual |
| 12 | Opponent with < 4 decay-weighted matches | Dotted texture on the cell; drawer names the prior and its SDs |
| 13 | < 3 league matches played all season **(today)** | Form section suppressed: *"One match played. Form is not yet a signal and is not being used."* |
| 14 | Blank gameweek | Explicit slot, em-dash, hairline border — never a missing column |
| 15 | Double gameweek | Two stacked cells in one column; run aggregate sums both; gutter shows n = 2 |
| 16 | `content_insight` empty **(today)** | The §7.10 sentence, which explains a build gap rather than implying silence |
| 17 | No predicted lineup for the GW | *"Rotowire publishes 24–48h before kickoff."* |
| 18 | Ownership unreadable | Gutter EO bars **omitted**, never drawn as zero |
| 19 | Panel over the 10s budget | Existing `performance: "over_budget"` + advisory note |
| 20 | Refresh job skipped a step | Status names step, reason, ledger arithmetic; chip stays orange; button does not go green |

**Untested path, stated as such:** I checked every gameweek of 2026-27 and
**there are no blanks and no doubles** — all 38 gameweeks have exactly 20
team-slots. Rows 14 and 15 are therefore correct-by-construction code with no
live data behind them. Do not assume they work; make a synthetic fixture list a
test case.

---

## 9. Panel contract additions, field by field

Two panels. Rename nothing that would break a caller; add the missing `rows`.

### 9.1 `fixture_ticker` — the grid payload

**Structural change:** add a top-level **`rows`** array, flat at the
(team, gameweek, fixture) grain, and let the view group client-side (it already
builds a `byGw` map). This fixes §1.3 and makes the panel legible to the
generic table renderer as a fallback layout. `teams[]` is **dropped** — the
nesting was the only thing it added and the view can rebuild it in four lines.
`clubs[]` replaces it with genuinely different data: club-grain aggregates that
have no place on a fixture row.

`rows[]` — one per (team_code, gw, fixture_id):

| field | type | notes |
|---|---|---|
| `team_code` | int | |
| `short_name` | str | *the field `fixtures.js` should have been reading* |
| `gw` | int | |
| `fixture_id` | int | |
| `opponent_code` | int | |
| `opponent` | str | |
| `label` | str | existing UPPER/lower venue convention, unchanged |
| `is_home` | bool | |
| `kickoff_utc` | str \| null | |
| **`attack_ease`** | number \| null | log-ratio, **+ = easier to score** |
| **`defence_ease`** | number \| null | log-ratio, **+ = easier to keep clean** |
| `xgf` | number \| null | natural units, league-average attack |
| `xga` | number \| null | natural units, league-average defence |
| `p_clean_sheet` | number \| null | league-average defence, post-shading (§5.5) |
| `difficulty` | number \| null | the legacy 0–1 blend, **kept one release, marked deprecated in the schema `description`** |
| `market_weight` | number | 0–1, the weight actually applied |
| `market_books` | int | real books only; `derived#*`/`fair#*` excluded |
| `market_as_of` | str \| null | |
| `sources` | array[str] | every input that moved this cell |
| `confidence` | str | `high` \| `thin_history` \| `prior_only` |
| `n_matches_opponent` | number | decay-weighted, drives `confidence` |
| `blank` | bool | |
| `double` | bool | |

`clubs[]` — one per team_code:

| field | type | notes |
|---|---|---|
| `team_code`, `short_name`, `name` | | |
| `attack_rating`, `defence_rating` | number | centred fitted values; **`defence` positive = leaky** |
| `attack_run`, `defence_run` | number | horizon means of the eases |
| `divergence` | number | `attack_run − defence_run` — the run map's y-offset |
| `n_fixtures` | int | |
| `template_exposure` | number | Σ `selected_by_pct` (§5.7) |
| `flagged_exposure` | number | same, restricted to `status <> 'a'` |
| `n_matches` | number | decay-weighted |
| `xg_for_pg`, `xg_against_pg`, `matches_played` | number \| null | **null when `matches_played < 3`** — today, always null |

`gws[]` — one per column:

| field | type |
|---|---|
| `gw`, `deadline_utc`, `n_fixtures` | |
| `market_covered` | bool — drives the `+ mkt` header badge |
| `market_books`, `market_as_of` | |

`inputs[]` — one per input, drives the Inputs row:

| field | type | notes |
|---|---|---|
| `key` | str | `schedule` \| `fit` \| `odds_core` \| `odds_rich` \| `team_news` \| `lineups` \| `insights` |
| `label` | str | |
| `source` | str | matches `raw_fetch.source` |
| `as_of` | str \| null | |
| `age_hours` | number \| null | |
| `state` | str | `fresh` \| `warn` \| `stale` \| `absent` |
| `refreshable` | bool | whether **Fetch now** can move it |
| `cost_credits` | int \| null | 42 for `odds_rich`, else null |

`scale`: `{clip, unit: "log_goal_ratio", neutral_label, legend}`
`fit`: `{fitted_at, snapshot_as_of, home_adv, rho, half_life_days, n_matches, effective_n, converged, promoted_codes[], prior:{attack_mean, defence_mean, attack_sd, defence_sd, n_clubs, n_seasons, source}}`
`credits`: `{spent_month, cap, month}` — straight from the ledger.
Unchanged: `season`, `gws`, `row_count`, `as_of`, `notes`.

### 9.2 `fixture_detail` — new panel, the drawer payload

Params `{season, fixture_id}`. Returns `{fixture, sides[2], decomposition,
market, availability[], lineups[], set_pieces[], shape[], meetings[],
insights[], notes[]}` — one section per §7 heading, each independently
nullable so a missing source collapses one section rather than the drawer.

### 9.3 Artefact change

`ratings_cache.COLUMNS` grows: `attack_ease`, `defence_ease`, `xgf`, `xga`,
`p_clean_sheet`, `n_matches_opponent`. `difficulty` stays. `_load_difficulty`'s
`required` set stays as it is, so an old artefact still loads and degrades into
state 7 rather than crashing — which is the whole reason that guard exists.

### 9.4 Two one-line frontend fixes, worth landing before anything else

```js
row.appendChild(el("td", null, t.team));          // → t.short_name
sub.textContent = "…" + (res.ratings_note || ""); // → (res.notes || []).join(" ")
```

These are §1.1 and §1.2. They are a five-minute fix that makes the current tab
legible, and they should not wait for this design.

---

## 10. Build order

1. §9.4 — two-line frontend fix. The tab stops lying today.
2. `ratings_cache` emits the split columns. No model work; delete a subtraction.
3. Panel gains `rows`, `clubs`, `inputs`, `scale`, `fit`. Grid renders two
   bands. **This alone delivers the brief's core.**
4. §6.2 (a) and (b) — the pipeline defects. One afternoon, and it is what makes
   §5.4's market layer real instead of aspirational.
5. Drawer + `fixture_detail`, sections 1–9. Everything is already in the
   warehouse.
6. `content_insight` wiring + team resolver (§7.1); drawer section 10 lights up.
7. `POST /api/refresh/fixtures`, the monitor, the run map, the divergence table.

---

## 11. The three biggest risks, and what would make me abandon this

### 11.1 The split is real, but the ratings behind it are thinnest exactly where the page will shout loudest

Coventry and Hull have **one decay-weighted match each** (every established club
has ~51). The promoted prior is fitted from **7 clubs over 4 seasons** with
`covariate = "none"` and SDs of **0.484 (attack) and 0.622 (defence)** — so wide
it barely constrains anything. Hull's fitted defence moved from the prior mean
of +0.162 to **−0.181** on the strength of a single game, which currently
renders Hull as a top-five defence and makes their column read "hard to score
against". That is probably wrong, and it is a cell a reader would act on.

*Mitigation:* the `thin_history` texture, the prior named with its SDs in the
drawer, and `f_books`-weighted market anchoring, which for GW2–GW3 is exactly
where a promoted club's rating needs the most correction.

*Abandon condition:* run the split eases walk-forward over 2023-24, 2024-25 and
2025-26. If, in the first six gameweeks of a season, the split does **not**
predict per-team goals-for and goals-against better than the blend does, then
the split is decoration and I should ship the blend with a better legend and a
neutral midpoint instead. This is a one-script check and it should gate the
build at step 3, not follow it.

### 11.2 The market layer may never carry weight, and a permanently grey layer is worse than none

Today the honest `w_market` on GW2 is 0.50 only because a pull landed a minute
before I looked; for the eight days before that it was ~0. There is **no
clean-sheet market at all** (§5.5) and never has been, so the owner's explicit
"use clean sheet odds" is, on today's data, unanswerable except by our own
model. Two paid pulls per gameweek costs 84 credits against a cap of 150. If the
cap is not raised and the DAG defects are not fixed, the market layer is a
promise the page cannot keep, and a `+ mkt` badge that never appears is worse
than never having offered one.

*Abandon condition:* if, after §6.2's fixes, the `odds_core` chip is not green
at the deadline in two consecutive gameweeks, delete the market layer, delete
`market_weight` from the contract, and ship a pure-model page with one line
saying the market is not currently reachable. Half a market layer is worse than
none.

### 11.3 The two-band cell may not survive 160 cells at real size

Two 9px bands, a 2px gap, a text label and a texture overlay inside roughly
52 × 24px, twenty rows deep, in both themes, under the repo's CVD checks. I have
not built it. If the bands read as one muddy block, the whole design collapses
into "grey grid with letters" and I have shipped something worse than the
current single-hue grid, because at least that one had a legible ramp.

*Mitigation:* `READ AS` (§3.3) is not a convenience. Attack-only and
Defence-only collapse each cell to a single full-height band, which is always
legible; two bands is the *default*, not the only mode. The design degrades to a
better version of the subscription-tool toggle rather than to nothing.

*Abandon condition:* if a rendered prototype at 1280px cannot show 20 clubs ×
6 gameweeks without horizontal scroll **and** keep both bands distinguishable in
light and dark under the six-checks validator, drop to a single-band default
with a small divergence glyph in the cell corner, and move the split to the run
map and the divergence table where space is not scarce.

### 11.4 An honest fourth

Putting template exposure on a fixtures page is arguably scope creep, and it
duplicates a number the Template tab owns. My defence is that
`rank_objectives.md` §0 makes it load-bearing — a fixture run the field already
holds is not an edge against the bar — and that it costs one gutter bar and one
table column, not a section. If it reads as clutter in review, it is the first
thing I would cut, and cutting it costs the page nothing structural.
