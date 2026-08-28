# Fixtures: the split ticker

> Design proposal A. Every number below was read out of this warehouse on
> 2026-08-27 with `duckdb(read_only=True)` / `Warehouse().read_copy()`, and the
> Dixon-Coles figures come from a live fit at today's snapshot. Where a number
> is a claim about the world rather than about the warehouse, it says so.

---

## 0. The one sentence

> **Every fixture has two difficulties — how easy it is to score in, and how
> easy it is to keep clean — and the run worth acting on is the one where
> those two disagree.**

That sentence is printed under the page title, in the position `xpoints.js`
prints "Numbers are copied from ingested providers, never modelled here" and
`template.js` prints its rank-move identity. It is the whole argument, and
every card below it exists to serve it.

A second sentence sits under it, because the first one is only half honest:

> Fixture swing is worth about **2–3 points per asset over six gameweeks**.
> Team quality is worth roughly **four times that**. Use this page to break
> ties between similar assets, not to pick them.

Both sentences are measured, not asserted. §2 shows the arithmetic.

---

## 1. What is actually wrong today, verified

The brief says the fixtures tab is "just an ugly graph". It is worse and
better than that.

**It draws no graph at all.** `web/dist/js/views/fixtures.js` is 69 lines: one
`section.card`, an `h2`, a caption, and a `div.scroll-x > table.data`. Against
`template.js` (7 cards, 4 toolbar rows, 5 inline-SVG charts, a shared tooltip)
it is the thinnest view in the application.

**The panel is not empty.** `POST /api/scripts/fixture_ticker/run` against the
running server returns `row_count: 20`, five gameweeks per club, kickoff times,
and a fitted `difficulty` on every opponent. The data is there.

**The view reads three keys the result schema forbids.** `fixtures.js:39`
reads `t.team`; the schema (`fpl_edge/platform/scripts/fixtures.py:73`) has
`short_name`. `fixtures.js:26` and `:28` read `res.ratings_note`; the schema
has `notes[]`. So the tab renders twenty rows of blank team names under a
caption that never gets its staleness sentence. That is the "rows: 0"
sensation. It is a contract bug of exactly the class `panels.py:78-85` already
documents having shipped once before, and the contract test at
`tests/unit/test_web_contract.py:130` checks the caption logic and the optional
`difficulty` field but never the row-level key.

**And the difficulty number itself is structurally incapable of answering the
question.** `data/warehouse/fixture_difficulty.parquet` holds 740 rows and
exactly **40 distinct values** — 20 opponents × 2 venues. Every one of the 19
other clubs facing a given opponent at a given venue receives the *same*
number. Grouping by `(team_code, opponent_code, is_home)` yields zero
combinations with more than one distinct difficulty:

```
select count(*) from (
  select team_code, opponent_code, is_home, count(distinct difficulty) c
  from 'fixture_difficulty.parquet' group by 1,2,3 having c > 1)
-- 0
```

`ratings_cache.py` says why, and says it deliberately:

> `strength(O, venue) = lam_O - mu_O` … "It is a function of the opponent and
> venue only — deliberately, like FPL's own FDR, so a leaky defence does not
> paint a club's whole ticker red."

The intent is right and the execution throws away the answer. **`lam_O` and
`mu_O` are both computed, then subtracted into one scalar, then min-max
normalised.** Those two quantities *are* the split the owner asked for:

- `mu_O = exp(c + g·[we are home] + mean_attack + defence_O)` — goals a
  league-average attack expects to score against O. **This is attack ease.**
- `lam_O = exp(c + g·[O is home] + attack_O + mean_defence)` — goals O expects
  to score against a league-average defence. **This is defence difficulty.**

The split costs nothing to build. It is one deleted subtraction and two extra
parquet columns. Everything else in this document is about what to *do* with
them.

---

## 2. The evidence that the split matters

A live `DixonColesModel().fit()` at today's snapshot:

```
intercept c = 0.2386   home_adv g = 0.1890   rho = -0.0739
n_matches = 1530       effective_n = 512.2   half_life = 400d   converged
```

Fitted club ratings (`fit.table()`; attack positive = scores more, defence
positive = **leakier**):

| club | attack | defence |
|---|---|---|
| MCI | +0.406 | −0.389 |
| LIV | +0.345 | −0.133 |
| ARS | +0.338 | **−0.565** |
| TOT | +0.074 | **+0.086** |
| HUL *(promoted)* | −0.153 | **−0.266** |
| IPS | −0.232 | +0.322 |
| COV *(promoted)* | −0.444 | +0.252 |

Spurs are an above-average attack with a below-average defence. Hull, newly
promoted, have a fitted defence better than eleven established clubs. A single
blended number cannot say either thing.

### Run quality over GW2–GW7, split

Computing, for a league-average side, the ease of each club's next six
opponents on both tracks and ranking them:

| club | attack rank | defence rank | divergence |
|---|---|---|---|
| **BHA** | 13th | **4th** | **+9** |
| CHE | 19th | 13th | +6 |
| HUL | 10th | 6th | +4 |
| **EVE** | **2nd** | 8th | **−6** |
| LIV | 7th | 12th | −5 |
| LEE | 14th | 18th | −4 |

The blended parquet ranks Brighton **7th** over that window (mean difficulty
0.486). The split says their run is the **4th best in the league for a
defence** and the **13th best for an attack**. Everton's blended rank is 4th;
the split says 2nd for attackers, 8th for defenders. Those are two different
transfers, and today's ticker cannot tell them apart. This is the single
strongest argument for the whole page.

### And the honest size of the effect

Converted to FPL points against a league-average fixture (§5 defines the
conversion), over the GW2–7 window:

```
attack track: spread across the 20 clubs = 0.373 pts per fixture  → 2.24 pts over 6 GW
defence track: spread across the 20 clubs = 0.509 pts per fixture → 3.05 pts over 6 GW
```

Where the same arithmetic is run team-specifically — using each club's *own*
attack and defence rather than a league-average side — the spreads are
**1.707** and **1.904** points per fixture. Who you are is worth about **4×**
who you play.

This is why the second governing sentence exists. `rank_objectives.md` §1
defines the objective as `P(final rank ≤ 10,000)` (with 1,000 as a stated
stretch), and shows that what moves it is `m = E[my weekly score − the pace
increment]` — edge *against the bar*, not against the average manager. A
2-point fixture edge on a club the field already owns contributes almost
nothing to `m`, because it moves the bar with you. That is not a footnote; it
is a third encoding channel on the primary chart (§4).

*(Note on the citation: `template.js:3` currently attributes "P(top-1k)" to
`rank_objectives.md` §0. §0 says top-10k throughout; 1,000 appears once, in
§1, as a parenthetical stretch target. This page should cite §1 and say
top-10k.)*

---

## 3. Wireframe

Sections in order. The order is an argument, in the manner of
`template.js:310-325`: state the claim, prove the inputs are fresh enough to
believe, show the whole league at once, name the handful of fixtures where the
claim bites, then let the reader open any one of them.

```
┌─ FIXTURES ─────────────────────────────────────────────────────────────────┐
│ Every fixture has two difficulties — how easy it is to score in, and how    │  <- .sub, governing sentence
│ easy it is to keep clean — and the run worth acting on is the one where     │
│ those two disagree. Fixture swing is worth ~2-3 pts per asset over six      │
│ gameweeks; team quality is worth ~4x that.                                  │
│                                                                            │
│ ● Schedule 19h   ● Ratings 18h   ● Market 91h — EXCLUDED   ● Intel 20h      │  <- freshness strip
│                                        [ fetch odds · 2 credits ] [ re-fit ]│
├────────────────────────────────────────────────────────────────────────────┤
│ HORIZON    [3][5][6][8][10]  from GW2      deadline in 1d 4h                │  <- .toolbar rows,
│ LENS       [Attack][Defence][Split]                                        │     .tlabel each
│ SORT       [Split][Attack run][Defence run][Alphabetical]                  │
│ SHOW       [Template load] [Uncertainty] [Model vs market]                 │
├────────────────────────────────────────────────────────────────────────────┤
│  THE SPLIT TICKER                                                          │
│                                                                            │
│  club   A ├──●───┼────┤  GW2    GW3    GW4    GW5    GW6    GW7            │
│         D ├───────●─┼─┤ ◄ deadline                                         │
│  ────────────────────────────────────────────────────────────────────────  │
│  BHA    A ├───●─┼────┤   che    ful    NEW    ars    tot    LEE            │
│    ⬤    D ├─────┼─●──┤  -0.1   +0.2   +0.1   -0.4   +0.0   +0.3           │
│  ────────────────────────────────────────────────────────────────────────  │
│  EVE    A ├─────┼──●─┤   BOU    ips    sun    IPS    cov    hul            │
│    ○    D ├───●─┼────┤  +0.3   +0.9   +0.2   +0.9   +0.4   +0.1           │
│  ...                                                                       │
│                        ▲ zero = a league-average fixture                    │
├────────────────────────────────────────────────────────────────────────────┤
│  WHERE THE TWO TRACKS DISAGREE            (top 6, both directions)         │
│  BHA  4th-easiest defensive run, 13th attacking. The blend calls it 7th.   │
│  EVE  2nd-easiest attacking run, 8th defensive. The blend calls it 4th.    │
│  ...                                                                       │
├────────────────────────────────────────────────────────────────────────────┤
│  THIS WEEK — GW2, ten fixtures       [expand all]                          │
│  cards, one per fixture, each opening the same drawer as a grid cell        │
├────────────────────────────────────────────────────────────────────────────┤
│  provenance: fixture_ticker · a1b2c3d · 2026-08-27T22:58Z                   │
└────────────────────────────────────────────────────────────────────────────┘
                                                    ┌──────────────────────┐
   clicking any cell or card opens →                │  DRAWER (§7)         │
   (the same `aside.drawer` pattern creators.js     │  right, 460px        │
    uses; Escape closes; handler self-detaches)     └──────────────────────┘
```

**Above the fold** (1440×900, the machine this is built on): the governing
sentences, the freshness strip with its two trigger buttons, all four toolbar
rows, and roughly the first **eight to ten club rows** of the ticker. That is
deliberate — sorted by split, the eight rows above the fold are precisely the
clubs where the two tracks disagree most, i.e. the page's thesis is visible
without scrolling. The remaining twelve rows are the ones where a blended
number would have been fine.

The freshness strip is **above** the chart, not in the footer, because on
today's warehouse the honest headline is "the market layer is 91 hours old and
excluded", and a reader who scrolls past that has been misled.

---

## 4. The central visualisation

The card is one component with two halves that share a row: a **run rail**
(left, precise, both tracks at once) and a **ticker grid** (right, scannable,
one track at a time). One row per club, so the eye never has to re-find a club
between the two.

### 4.1 The run rail — the thing that is actually new

Per club, two stacked lollipops on **one shared horizontal axis**, in points
against a league-average fixture, with true zero in the middle:

```
        harder  ←─────────── 0 ───────────→  easier
  BHA  A  ├──────────●──────┼───────────────┤     -0.02
   ⬤   D  ├─────────────────┼────●──────────┤     +0.16
                            └────┘
                          the tie = the split
```

- **Position on a common scale** carries each track's value. Cleveland–McGill
  put position-along-a-common-scale at the top of the accuracy ordering and
  colour-saturation near the bottom; the quantity a reader will actually act
  on gets the best channel available.
- **The horizontal tie between the two dots is the divergence**, drawn
  directly. This is the whole point. A table of two numbers requires the
  reader to perform twenty subtractions to find Brighton; the tie makes the
  subtraction a length, scanned in one saccade. Sorting by tie length sorts
  the league by "fixtures a blended ticker hides".
- **Zero is a real zero** — the mean over every remaining fixture in the
  season, `--line` weight, labelled "league-average fixture". Not a
  horizon-relative mean, so the rail does not silently re-centre when the
  reader changes the horizon.
- **No hue at all in the rail.** Dots are `--ink`, rules are `--faint`, the
  tie is `--faint`. The two tracks are distinguished by lane and by the "A"/"D"
  gutter label. Hue is spent entirely on the grid (§4.2); spending it twice
  with two different meanings on one page is how a chart system stops reading
  as one system.
- **Fill carries template load**, reusing `template.css:99-104`'s existing
  convention (`.mark.mine` filled with a 2px `--surface` ring; hollow = not
  owned). A filled club marker means the top-10k field already holds ~1+
  players from this club; hollow means it does not. This is the rank-relevance
  gate from §2 rendered as shape, not as a fourth number. It uses
  `fact_external_ownership` `metric='eo_top10k'`, summed to club, which is
  fresh (`as_of` 2026-08-27 04:33) — the sum is the *expected number of this
  club's players in a top-10k squad* (MUN 3.1, ARS 2.3, MCI 1.7 at GW2), and
  the tooltip says exactly that, denominator named, per `template.js:21-23`.
- **A whisker on each dot** carries method disagreement (§5.5), shown only
  when there is more than one method to disagree. A single-source dot has no
  whisker and its tooltip says why.

### 4.2 The ticker grid — the calendar

Twenty rows × horizon columns. Each cell: the opponent's `short_name`,
UPPER-case for home and lower-case for away (the convention `fixtures.py`
already establishes and the Telegram grid already uses), plus the cell's value
printed in `--mono` at 10px, plus a background tint.

- **Tint is a diverging scale with a neutral grey midpoint**: `--s1` (blue,
  easier) ↔ `--faint` (grey, a league-average fixture) ↔ `--s2` (orange,
  harder). That is the pair with the recorded six-checks validation in
  `creators.css:17-22` — dark `#16181b` CVD ΔE 25.9 protan / 29.3 tritan,
  light `#ffffff` 24.7 / 32.7, contrast PASS on both. Grey at the midpoint,
  never a third hue, per `template.css:10-16`.
- **The number is always printed in the cell.** Colour is never the only
  encoding; this is the contrast-relief obligation `app.css:6-11` records and
  the reason `xpoints.js:378-386` prints its value beside every tint.
- **The lens toggle decides which track the grid is showing** — Attack,
  Defence, or Split. Because only one track is tinted at a time, the diverging
  hue pair has exactly one meaning on the page at any moment.
- **The Split lens is the thesis as a heatmap**: blue = this run favours your
  defence, orange = it favours your attack, grey = the two agree and a blended
  number would have been fine. Roughly two-thirds of the grid should be grey.
  That is not a failure of the design; it is the design telling the truth
  about how often the split matters.
- **The next deadline's column gets a `--raised` background** and a header
  reading "deadline in 1d 4h". It is the only column whose drawer can show
  lineups and prices, and it should look different for that reason.
- **Blanks** are an explicit cell reading "—" with `title="no fixture in
  GW{n}"`, never a missing `<td>`. **Doubles** stack two glyphs in one cell
  with a hairline between them and the rail sums both. `fixture_ticker`
  already emits `blank` and `double` booleans; nothing new is needed. (Neither
  occurs in the published 2026-27 schedule yet — every GW1–38 has exactly ten
  fixtures and twenty team-slots — so both paths ship untested against real
  data and need synthetic coverage.)

### 4.3 Why this beats what I considered

**Two side-by-side heat grids** (the Fantasy Football Scout shape — separate
attacking and defensive tickers). Honest, and it is where the idea comes from.
Rejected as the primary: it doubles the vertical budget, pushes the second
grid below the fold, and forces an eye-jump between two grids to compare two
numbers for one club. The divergence — the reason the split exists — is never
drawn, only inferable. My grid holds one lens and the rail holds both, so the
comparison happens inside one row.

**One grid, cells split diagonally into two coloured triangles.** Maximally
compact. Rejected outright: it encodes both quantities in colour alone, at
about 8×8 device pixels per triangle. It fails the repo's "never colour alone"
rule, it fails CVD, and it cannot carry a printed number.

**An attack-vs-defence quadrant scatter** (the Opta/The Analyst team-quality
idiom). This is genuinely good at what it does — distance from the diagonal
*is* the divergence, and it is the clearest possible picture of league shape.
Rejected as the primary because it destroys the calendar. The owner's first
sentence asks "which teams are playing whom"; a scatter cannot say when a run
starts, whether it is front- or back-loaded, or who the opponent is. It
survives as a **secondary toggle** on the Divergence card, where the calendar
is not the question.

**A cumulative-ease line chart, one line per club.** Twenty lines is
spaghetti; five selected lines is a different, smaller product.

**A plain table of two numbers per club.** It is the null hypothesis and it is
not stupid — it is exact, sortable, and accessible. It loses on exactly one
thing: the reader must subtract twenty times to find the divergence, and
because that is work, they will not do it, and the page's thesis dies. Length
is the argument for the rail.

### 4.4 Every channel, justified

| channel | carries | why |
|---|---|---|
| horizontal position (rail) | track value, pts vs neutral | best quantitative channel for the number acted on |
| length of the tie (rail) | divergence | the derived quantity that matters most deserves a direct encoding, not a mental subtraction |
| lane (rail, A above D) | which track | free; leaves hue for the grid |
| fill vs hollow (rail) | template load | shape carries identity, hue is left to the datum — `template.css:99-104` |
| whisker (rail) | method disagreement | uncertainty is a length, not a hue |
| diverging tint (grid) | ease on the selected lens | pre-attentive scanning across ~160 cells is what a ticker is *for*; validated pair, grey midpoint |
| printed number (grid) | ease, exactly | colour is never alone |
| case of the label (grid) | home / away | already the app's convention; costs no ink |
| `--raised` column (grid) | the next deadline | the only column with a richer drawer |
| row order | the sort key | the sort control is itself an analytical statement |

No dual axis anywhere. The rail's two lanes share one axis because they are
in the same unit — that is the point of §5's points conversion, not an
exception to `creators.js:25-29`. Marks are 1.5px rules and 3.5px dots; the
grid is `--line` hairlines only, no fills; type is `--mono` with
`tabular-nums`, per the existing scale.

---

## 5. The difficulty model

### 5.1 Two numbers per team per fixture, in points

For team **T** playing opponent **O**, with `h = 1` when T is at home, using
the fitted Dixon-Coles parameters (`c`, `g`, `attack[]`, `defence[]`, `rho`)
and the league means `ā = mean(attack)`, `d̄ = mean(defence)`:

```
mu = exp(c + g·h      + ā          + defence_O)     goals an AVERAGE attack scores here
lam = exp(c + g·(1−h) + attack_O   + d̄        )     goals O scores vs an AVERAGE defence
M   = score_matrix(GoalRates(mu, lam, rho))          9×9, from scoreline.py
```

Everything else is read *out of* `M`, never computed in parallel with it —
the invariant `base.py` already enforces:

```
xg    = Σ_i i · M[i,:].sum()          expected goals for
p_cs  = M[:,0].sum()                  clean-sheet probability   (clean_sheet_probs)
e_pen = Σ_k M[:,k].sum() · −⌊k/2⌋     expected goals-conceded penalty
```

Then, against a league neutral (`REF_*` = the mean of each quantity over
**every remaining fixture in the season**, so the zero does not move when the
horizon changes):

```
ATTACK_EASE  = (xg   − REF_XG)  × SHARE × 4
DEFENCE_EASE = (p_cs − REF_CS)  × 4  +  (e_pen − REF_PEN)
```

`SHARE = 0.30` is the reference attacker's goal-involvement share of team
goals; `× 4` is FPL's midfielder goal value and the clean-sheet value. Today's
fitted neutral: `REF_XG = 1.277`, `REF_CS = 0.234`, `REF_PEN = −0.518`.

**Why points and not 1–5, or 0–100.** A 1–5 integer invites the reader to add
five of them up as though the sum were meaningful, which is exactly the error
FPL's FDR encourages. Points have a true zero (a league-average fixture), they
are the unit the reader acts in, and — decisively — they make the honest
smallness of the effect *visible* rather than hiding it behind a rank. A rank
scale would have told me Brighton were 4th and 13th and let me feel that was
enormous; the points scale says the whole league spans 0.37 pts/fixture on the
attack track and tells the truth.

**Why the ease is opponent-and-venue-only, like the artefact it replaces.**
Keeping T's own strength out of the cell is what makes rows comparable and
what stops a leaky defence painting a club's whole ticker orange — the
property `ratings_cache.py` was right to want. **T's own strength is not
discarded; it moves to the row header**, as a two-value badge carrying that
club's fitted `attack` and `defence` from `fit.table()`. So a row reads: *who
you are* (header) × *who you play* (cells). The drilldown multiplies them
(§7); the ticker never does, because the moment it does, the rows stop being
comparable.

### 5.2 Where each input enters

| layer | source | role |
|---|---|---|
| **model** | `DixonColesModel().fit()` at the latest snapshot | the spine. Always present. |
| **market** | `fact_odds` h2h + totals → `invert_odds` → λ,μ; and `fact_odds_derived` `team_lambda` / `clean_sheet_prob` directly | an *anchor*, weighted by freshness and coverage |
| **form** | a second DC fit at a short half-life | a *separate displayed channel*, never blended |

**Blend, market into model** — geometric in log-rate space, matching
`blend.py`'s existing formula (`out = dc**(1−w) · mkt**w`), but with `w` a
function rather than the file's untuned `0.5` constant:

```
w(fixture) = W_MAX · coverage(fixture) · exp(−ln2 · age_hours / 12)
W_MAX = 0.5      coverage = fraction of tracked books quoting this fixture
```

A 12-hour half-life on market weight is a deliberate, defensible choice: it
means a price older than about a day contributes under 25%, and today's
91-hour prices contribute `2^-7.6 ≈ 0.5%`. **On today's warehouse the market
weight is effectively zero on every fixture, and the page must say so in
words, not just render a pale pip.** `W_MAX = 0.5` is inherited from
`blend.py` and is explicitly *not tuned out of sample*; the row header of the
model card should say so until somebody tunes it.

**Why form is not blended.** The production fit already carries exponential
time decay at a 400-day half-life, tuned over `HALF_LIFE_GRID = (90, 150, 240,
400, 700, 1200)` in `evaluate.py`. Re-weighting recent results *again* on top
of a fit whose decay was tuned to maximise out-of-sample log loss makes the
forecast worse, not better — the tuning already answered "how much should
recency count". So: fit a **second** model at a 60-day half-life, and expose
`ATTACK_EASE(60d) − ATTACK_EASE(400d)` as an explicit **form tilt** arrow in
the drilldown, drawn in `--faint`, labelled "hot/cold vs the tuned fit". It
answers a different question ("who is hot") and it is not allowed to move the
headline number. Two fits at ~60s each is affordable in a cached batch job.

### 5.3 Missing and stale, per source

| condition | what happens |
|---|---|
| no market price for a fixture | model only. The market pip is drawn **hollow** with `title="no book quoted this fixture"`. Never imputed. |
| market present but > 24h old | **excluded from the blend entirely**, and still drawn, as a ghost mark with its age. "The last price we saw" is information even when it is not an input; deleting it hides that a source has died. |
| market present but only one book | blended at `coverage = 1/N_tracked`, so a lone book barely moves the number. Tooltip names the book. |
| `clean_sheet` rows | **never labelled "market".** All 3,140 rows carry `bookmaker = 'derived#poisson'` — they are synthesised from h2h+totals, not quoted. The page calls this channel `derived`. Calling a Poisson inversion a bookmaker price is the single most misleading thing this page could do. |
| DC fit unavailable (`< MIN_MATCHES_TO_FIT = 200`) | whole page degrades to schedule-only — who plays whom, where, when — exactly as `fixture_ticker` does today. No tints, no rail, and a stated reason. |
| promoted club | the fit already handles it via `PromotedPrior`; the row header carries a "promoted — prior-driven" badge and both rail dots get a widened whisker from `prior.attack_sd` / `prior.defence_sd`. Two clubs qualify today (HUL, COV). |
| `fixture_difficulty` artefact older than `DIFFICULTY_STALE_DAYS = 7` | numbers still served, with the existing note — a week-old fitted rating beats a made-up fresh one. Keep this behaviour; only fix the view that never renders the note. |

### 5.4 Two contract mismatches that must be fixed first

`fpl_edge/models/team_goals/odds.py` cannot read the live `fact_odds` at all,
for two independent reasons. Any market layer is vapour until both are fixed:

1. **Selection casing.** `devig_frame` requires `("home","draw","away")` and
   `s.startswith("over")`. Live rows are `HOME` / `DRAW` / `AWAY` and
   `OVER_2.5` / `UNDER_2.5`. Nothing matches.
2. **Key form.** `fixture_key(season, fixture_id)` builds `"2026-27:11"`. Live
   `fact_odds` holds **zero** rows in that form; all 1,538 distinct keys are
   natural keys like `2026-27:2026-08-21:arsenal:coventry-city`.
   `fpl_edge/models/ensemble/sources.py:76 odds_with_fixture_keys()` is the
   existing read-time resolver and should be the one path.

De-vigging: the ingest path already writes a `fair#shin` book (Shin 1993
handles the favourite–longshot bias that proportional normalisation leaves in,
which is why it is the right default for extracting a *fair* line). The model
path defaults to `"proportional"`. Use `fair#shin` where it exists and say
which method produced each number.

### 5.5 Uncertainty, from data that already exists

`fact_odds_derived` persists the *same* quantity under several methods —
`clean_sheet_prob` under `poisson_indep`, `dixon_coles` and `cs_grid#power`;
`team_lambda` under `poisson_indep`, `dixon_coles` and `team_totals`. For
Arsenal v Coventry the three CS methods give 0.6045 / 0.6010 / 0.5521. That
spread is a free, honest uncertainty estimate and it becomes the whisker. Where
only one method has a value there is no whisker, and the tooltip says
"one method only".

---

## 6. Freshness and triggers

This is the section the brief says is first-class, and today's warehouse is
the argument for it.

### 6.1 The measured state, right now

| source | newest row | age | consequence |
|---|---|---|---|
| `fact_fixture` | 2026-08-27 03:00 | 19h | fine |
| `fixture_difficulty.parquet` (`fitted_at`) | 2026-08-27 04:33 | 18h | fine |
| `fact_odds` h2h / totals / clean_sheet | 2026-08-24 03:00 | **91h** | excluded |
| `fact_odds` team_totals / correct_score / btts / anytime_scorer | 2026-08-19 23:41 | **191–206h** | excluded |
| `fact_odds_derived` | 2026-08-19 23:47 | **191h** | excluded |
| `intel_item` press_conference / out_of_position | 2026-08-27 03:00 | 20h | fine |
| `fact_predicted_lineup` (GW2, rotowire) | 2026-08-27 04:33 | 18h | fine |
| `content_insight` | — | — | **0 rows** |

And worse than stale: **the market has no forward coverage at all.** Every
2026-27 odds row belongs to a fixture dated 2026-08-21 to 2026-08-24 — that
is GW1, which has already been played. There are **zero** priced fixtures for
GW2 (kickoff 2026-08-28) or beyond:

```
select market, min(split_part(fixture_key,':',2)), max(split_part(fixture_key,':',2))
from fact_odds where fixture_key like '2026-27%' group by 1
-- every market: 2026-08-21 .. 2026-08-24
```

So a market-first design would render an empty page. This design is model-
first with a market overlay that lights up where prices exist, and that is not
a hedge — it is the only shape that survives contact with this warehouse.

### 6.2 Why the job said it succeeded

`fpl_edge/jobs/post_gw.py:99` — `ok = proc.returncode == 0`. A step is
"successful" if its process exits zero. `scripts/ingest_odds.py --odds-api
--max-credits 30` exits zero when it declines to spend past its cap. So the
job reports green while writing nothing, forever. **The success criterion is
exit code, not recency.**

The fix is a contract change, not a patch:

> **A pipeline step succeeds only if it advanced a freshness watermark it
> declared in advance, or explicitly recorded a no-op with a reason.**

Add one table:

```sql
CREATE TABLE freshness_watermark (
  source_key   VARCHAR NOT NULL,   -- 'odds.h2h', 'ratings.dixon_coles', 'intel.availability'
  season       VARCHAR,
  watermark_at TIMESTAMPTZ,        -- newest data instant this source now holds
  observed_at  TIMESTAMPTZ NOT NULL,
  rows_written INTEGER NOT NULL,
  run_id       VARCHAR NOT NULL,
  ok           BOOLEAN NOT NULL,
  reason       VARCHAR,            -- required when ok=false or rows_written=0
  PRIMARY KEY (source_key, season, observed_at)
);
```

Every ingest step writes exactly one row. `post_gw._run` gains an
`expects_watermark: str | None` argument and marks the step failed when the
watermark did not move and no reason was recorded. Then `alert_text` fires,
which is the behaviour that was missing for the four days that produced this
brief. This table also becomes the single source of truth behind every "as of"
on the page — the panel reads it rather than each script re-deriving `max(as_of)`
from a different table.

### 6.3 What the page shows

A **freshness strip** immediately under the governing sentences, before any
number, with one pill per input class: Schedule · Ratings · Market · Intel.
Each pill carries the existing `span.freshdot.{good|warn|bad}` vocabulary from
`xpoints.js:79-85` / `template.js:98`, **plus the age printed as text** — the
dot is never the only signal. Hovering gives the exact watermark and the
`source_key`. Thresholds live in the panel, not in CSS, and are declared per
source, because 19 hours is fine for a fixture list and catastrophic for a
price:

```
schedule   good < 36h    warn < 96h    bad otherwise
ratings    good < 48h    warn < 7d     bad otherwise    (matches DIFFICULTY_STALE_DAYS)
market     good < 12h    warn < 24h    bad otherwise → EXCLUDED FROM EVERY NUMBER
intel      good < 24h    warn < 72h    bad otherwise
```

When any pill is `bad`, the strip grows a sentence naming the consequence in
plain words. Today that sentence reads:

> Market prices are 91 hours old (last seen 24 Aug 10:00 UTC) and cover only
> GW1, which has been played. They are excluded from every number on this
> page. Everything below is model-only.

That is the honest empty state the quality bar demands, and it is the actual
state today.

### 6.4 The pipeline

Two existing schedulers, both launchd; neither refreshes prices before a
deadline.

- `deploy/com.fpledge.postgw.plist` → `fpl_edge.jobs.post_gw`, daily at
  03:00 local. Its `ingest_odds_props` step is capped at 30 credits and is the
  step that quietly did nothing.
- `deploy/com.fpledge.dag.plist` → `fpl_edge.jobs.deadline_dag --once` every
  600s, firing on `DEADLINE_OFFSETS` at T−30h (`presser_projection_refresh`,
  which already re-runs `fixture_difficulty`), T−4h, T−90m. **None of these
  pull odds from the API** — T−30h runs `ingest_odds.py --fixtures`, which is
  the free football-data path.

Add two deadline-relative firings:

| task | offset | what it does | credits |
|---|---|---|---|
| `odds_refresh_early` | T−26h | `/events` (0) then `/odds?markets=h2h,totals&regions=uk` for the next deadline's fixtures (2), then re-derive `fact_odds_derived` (0) | **2** |
| `odds_refresh_late` | T−3h | same, after team news | **2** |

4 credits per gameweek, 152 per season, against a `DEFAULT_MONTHLY_CAP` of
400 of the 500-credit free tier. The measured costs are in
`docs/data_sources.md` §5A.1: `/events` 0, `/odds` with two markets in one
region 2, per-event scorer props 1. There is no budget argument against this;
the current gap is that nobody scheduled it.

Also: `scripts/ingest_odds_extras.py` (correct_score, btts, team_totals) is
**scheduled by nothing** and its ledger
(`data/odds_expansion_ledger.json`) shows one run, 42 credits, 2026-08-20.
Those markets should stay manual until something on this page consumes them —
`team_totals` is the one that would most directly improve the attack track, and
it should be measured against the free derivation before it is scheduled.

### 6.5 What "fetch now" does

Two buttons in the freshness strip, both narrow, both honest about cost.

**`[ fetch odds · 2 credits ]`**
1. Reads the credit ledger and **states the cost before spending**: "This
   spends 2 of the 358 credits remaining this month. Continue?" A page that
   can silently burn a monthly quota is a page that will burn it.
2. `POST /api/triggers/odds_refresh` → returns `{run_id}` immediately. The
   panel budget is 10s (`registry.BUDGET_S`); a network fetch does not belong
   inside it.
3. Server-side: `/events` → `/odds?markets=h2h,totals&regions=uk` for the next
   deadline's fixtures only → resolve natural keys via
   `odds_with_fixture_keys` → write `fact_odds` → re-run
   `derive_gameweek_priors` (0 credits) → write `freshness_watermark`.
4. The page polls `GET /api/triggers/{run_id}` and re-renders the strip and
   every number when it flips. The market pips fill in; the blend weight rises
   from ~0 to whatever the fresh age gives.
5. **On failure it fails loudly.** Cap hit, HTTP error, zero rows — the pill
   goes red and prints the reason verbatim. It never silently keeps the old
   green state. This is the whole lesson of §6.2.
6. It is rate-limited to once per 10 minutes per user, and the button is drawn
   dead (`.chip.off`, the existing convention) with a countdown rather than
   hidden.

**`[ re-fit ratings · ~60s ]`** runs
`python -m fpl_edge.models.team_goals.ratings_cache` and rewrites the parquet.
Separate button because it costs a minute and zero credits — a completely
different decision from spending quota. It is the right button after results
land, and useless before them; the tooltip says so.

Neither button ever writes to `fpl.duckdb` from the web process — both enqueue
work for the job runner, because DuckDB is one-writer-XOR-many-readers and the
panel layer reads through `Warehouse.read_copy()`.

---

## 7. The drilldown

Same mechanism as `creators.js`: one `aside.drawer` appended to `document.body`,
`transform: translateX(100%)` → `.open`, Escape closes, the handler
self-detaches when the drawer leaves the DOM, and a `prefers-reduced-motion`
opt-out. Detail comes from a **second panel call**, `fixture_detail`,
memoised in a `Map<fixture_id, Promise>` exactly like `detailCache`, so a
double-click shares one in-flight request and an error is captured *into* the
cached value as `__error` rather than thrown — one failing block degrades, the
drawer survives.

Every cell in the grid opens it. Cells in the next-deadline column open the
full this-week detail; later cells open the same drawer with the this-week
blocks in their honest empty state, naming what does not exist yet. That is
how "planning first, any fixture expands into this-week detail" degrades
truthfully instead of pretending.

```
┌ BRIGHTON  v  chelsea  (a)  ·  Sat 5 Sep 15:00  ·  GW3  ·  in 8d 16h  ─── ✕ ┐
│ ATTACK  ├────●──┼───────┤  −0.10 pts     DEFENCE ├──────┼──●────┤ +0.21    │
│         one method only              three methods, spread 0.05             │
├─────────────────────────────────────────────────────────────────────────────┤
│ WHY THIS NUMBER                                                             │
│ A league-average attack scores 1.19 here:                                   │
│   exp(0.2386 + 0·0.1890 + (−0.0086) + (−0.0829)) = 1.19                     │
│         c        away      mean attack   CHE defence                        │
│ Chelsea score 1.42 against a league-average defence, so a clean sheet runs   │
│ at 24.2%. Both read off one 9×9 score matrix, rho = −0.0739.                │
│ Form tilt (60d fit vs the tuned 400d fit): attack ▲ +0.04, defence ▼ −0.02  │
├─────────────────────────────────────────────────────────────────────────────┤
│ MODEL vs MARKET                                                             │
│   team λ      model 1.19   market —      no book quoted this fixture         │
│   P(CS)       model 0.242  derived —     `derived#poisson`, not a price      │
│   ⚠ No market price exists for any fixture after GW1. See freshness.        │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▾ PREDICTED LINEUP           rotowire · 18h old · 29 players                 │
│   XI, then bench, each with `certainty`. Absent provider → named, not blank. │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▾ AVAILABILITY               intel_item · 13 rows for BHA · newest 32h       │
│   headline · confidence · source_url. Only rows published since the last     │
│   fixture, so a two-month-old knock does not read as news.                   │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▾ SET PIECES                 set_piece_duty · team-keyed · 670 rows          │
│   duty, ord, note, source. Plus intel_item kind='set_piece' changes.         │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▾ PREVIOUS MEETINGS          fact_fixture · 8 finished, 4 seasons            │
│   season · gw · venue · score. No trend line drawn over 8 points.            │
├─────────────────────────────────────────────────────────────────────────────┤
│ ▾ WHAT CREATORS SAID         content_insight — 0 rows, extractor unwired     │
│   `.cx-provline.warn` naming the table and the gate. Collapsed by default.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Sources, exactly.**

- *Predicted lineup* — `fact_predicted_lineup(provider, season, gw, code, team_code, predicted_start, certainty, as_of)`. Real on day one: 587 rows for GW2 from `rotowire`, `as_of` 2026-08-27 04:33. **No panel exposes this table today**; it is the highest-value unused data in the warehouse.
- *Availability* — `intel_item` where `kind='availability'`. **Correction to the brief: these rows are player-keyed, and `team_code` is NULL on all 174 of them.** Attributing an injury to a club requires `intel_item.player_code → dim_player.code → dim_player.team_code`, which resolves cleanly (HUL 19, TOT 16, BHA 13, MUN 12…). The panel must own that join; no view does it today.
- *Set pieces* — `set_piece_duty(season, code, duty, ord, note, team_code, source, as_of)`, 670 rows and genuinely team-keyed, plus `intel_item` `kind='set_piece'` (215 rows across 25 clubs) for *changes*.
- *Previous meetings* — `fact_fixture` where `finished`, both orderings of the two codes, deduped by `row_number() OVER (PARTITION BY season, fixture_id ORDER BY as_of DESC)`. Four seasons are loaded; Arsenal–Villa returns eight meetings. Scores only — eight observations do not support a trend line and none is drawn.
- *Creator team-talk* — `content_insight` where `entity_kind IN ('team','fixture')` and `topic IN ('fixture_swing','tactical','role_change')`. `INSIGHT_TOPICS` already contains `fixture_swing`, which is precisely this block. Each item renders through the existing `quoteBlock` shape: verbatim `quote`, `start_s` → "play audio at 12:34", `extractor` → the `.cx-tier.{llm|cue}` badge, deep link with `linkKind`.

**What I need from `content_insight` before this block can work.** The table
holds 0 rows and no panel reads it. Three asks, in order:

1. **Wire the extractor into the content pipeline.** `analyze.py` writes these
   rows and nothing schedules the write. Without this the block is permanently
   empty.
2. **A resolved `team_code`.** `entity_ref` / `entity_name` are stored
   *verbatim as spoken* and deliberately not canonicalised — "Spurs", "the
   Spurs", "Tottenham" are three values. I cannot join a team take to a row.
   Add a nullable `team_code INTEGER` populated by an alias resolver in the
   same spirit as `SUPPLEMENTARY_TEAM_ALIASES` in `ensemble/sources.py`,
   written at insert time so the raw text stays untouched and auditable.
3. **Populated `horizon_gw` / `horizon_gw_end`.** A fixture-swing take is
   about specific gameweeks; without the horizon I cannot decide which cell it
   belongs to and would have to smear it across the whole row.

Until (1) and (2) land, the block ships collapsed with a `.cx-provline.warn`
that names `content_insight` in `<code>` and states the gate — the pattern
`creators.js:2720-2745` already uses for absent payload fields. A section that
says "this is built and not switched on" is worth shipping; a blank one is not.

---

## 8. Every empty and degraded state

| condition | what renders |
|---|---|
| no `dim_team` rows for the season | `empty("No 2026-27 clubs in the warehouse. Run `make ingest`…")` — the existing text, unchanged |
| no fixtures in the window | existing `empty()` with the GW range named |
| every deadline passed | existing note: ticker starts at the last known GW, stated |
| `fixture_difficulty.parquet` absent or corrupt | schedule-only: opponents, venue, kickoff. No tints, no rail. Caption: "No fitted ratings artefact — schedule only. Run `python -m fpl_edge.models.team_goals.ratings_cache`." |
| artefact present, `fitted_at` > 7d | numbers served **with** the existing staleness note — which today is computed and then dropped on the floor because the view reads `res.ratings_note` instead of `notes[]` |
| artefact present, this fixture missing | that cell is grey with `title="no fitted value for this fixture"`. A missing key stays missing; no colour is invented — `fixtures.py:244-248` already gets this right |
| no market anywhere | market pips hollow, blend weight 0, freshness strip states it in a sentence. **This is today's state.** |
| market stale > 24h | ghost pip with its age, excluded from the blend, sentence in the strip |
| market covers some fixtures | per-cell: priced cells get a filled pip, unpriced get a hollow one. Never a coverage average passed off as full coverage |
| `fact_odds_derived` empty | no whiskers anywhere; rail tooltip reads "one method only — no disagreement estimate" |
| `content_insight` 0 rows | collapsed block, `.cx-provline.warn`, names the table and the gate |
| no predicted lineup for this GW | "No lineup prediction from rotowire for GW{n} (newest: GW{m})." Names the provider and what does exist |
| no availability rows for a club | "Nothing noticed for BHA since GW1." A finding, not a blank — the same rule `creators.js:1945-1954` applies to its dissent line |
| no previous meetings (promoted club) | "First top-flight meeting on record." True for several COV and HUL pairings this season |
| blank gameweek | "—" with `title="no fixture in GW{n}"`; the rail's horizon mean divides by the fixtures that exist and the tooltip names the denominator |
| double gameweek | two stacked glyphs, hairline between, rail sums both, cell badge "×2" |
| `fit` did not converge / < 200 matches | whole page degrades to schedule-only with the reason; `DixonColesFit.converged` is already on the fit object and nothing reads it |
| trigger rate-limited | button drawn dead (`.chip.off`) with a countdown, not hidden |
| trigger failed | pill red, reason printed verbatim, previous numbers kept and re-labelled with their old age |

---

## 9. Panel contract additions

### 9.1 `fixture_ticker` — additive, back-compatible

Existing keys unchanged. `additionalProperties: False` means each new field
must be declared; the `oneOf` with `EMPTY_SCHEMA` stays unambiguous because
the real branch keeps its `required` list (the discipline `CHATTER_RESULT`
demonstrates).

**New top-level fields**

| field | type | meaning |
|---|---|---|
| `neutral` | object | `{xg, p_cs, gc_penalty}` — the league reference the two tracks are measured against. Named so the chart's zero is auditable |
| `scale` | object | `{attack: [min,max], defence: [min,max]}` — league-wide points range, fixed across horizons so the rail does not re-centre |
| `freshness` | array | one object per input class: `{source_key, watermark_at, age_hours, band: "good"\|"warn"\|"bad", threshold_hours, consequence}`. `consequence` is the plain sentence the strip prints. Read from `freshness_watermark` |
| `market_weight_max` | number | the `W_MAX` in force, so the page can say it is untuned |
| `fit` | object | `{fitted_at, snapshot_as_of, half_life_days, n_matches, effective_n, converged, rho, home_adv, intercept}` — every parameter the drilldown's "why this number" prints |
| `as_of` | string | already present; `registry.py:285-293` promotes it into provenance automatically |

**New per-team fields**

| field | type | meaning |
|---|---|---|
| `strength` | object | `{attack, defence, is_promoted}` from `fit.table()` — the row header badge |
| `run` | object | `{attack, defence, split, n_fixtures}` — horizon means in points and their difference. `n_fixtures` is the denominator, named |
| `template_load` | number \| null | expected players from this club in a top-10k squad, from `fact_external_ownership` `metric='eo_top10k'`. **Nullable**, because coverage is partial today (seven clubs read 0.0 at GW2) — null renders as a third, un-filled state, never as zero |

**New per-opponent fields**

| field | type | meaning |
|---|---|---|
| `attack_ease` | number | points vs neutral. Replaces nothing; `difficulty` stays for compatibility |
| `defence_ease` | number | points vs neutral |
| `xg` / `p_cs` | number | the raw quantities, so a reader can check the conversion |
| `attack_band` / `defence_band` | integer −3..+3 | pre-binned for the tint, so the client never re-derives a colour scale from a float |
| `sources` | array | `["model"]`, or `["model","market"]` — which layers fed this cell |
| `market` | object \| null | `{team_lambda, p_cs, age_hours, n_books, method, weight}`; **null** when unpriced. Never an imputed default |
| `spread` | object \| null | `{attack, defence}` method disagreement → the whiskers. Null when one method |

### 9.2 `fixture_detail` — new panel

`params: {season, fixture_id, team_code}`. `layout: "detail"`. Result:

```
fixture      {fixture_id, gw, kickoff_utc, team_code, opponent_code, is_home, deadline_utc}
tracks       {attack: {value, xg, spread, sources}, defence: {value, p_cs, gc_penalty, spread, sources}}
derivation   {c, g, attack_opp, defence_opp, mean_attack, mean_defence, rho, mu, lam}
form_tilt    {attack, defence, half_life_days}          -- the 60d fit; null when absent
market       {rows: [{market, method, value, bookmaker, as_of, age_hours}], coverage} | null
lineup       {provider, as_of, players: [{code, web_name, position, predicted_start, certainty}]} | null
availability {rows: [{player_code, web_name, kind, headline, confidence, source, source_url, published_at}], since_gw}
set_pieces   {duties: [{code, web_name, duty, ord, note, source}], changes: [...intel_item...]}
history      {meetings: [{season, gw, is_home, goals_for, goals_against, kickoff_utc}], n}
creator_talk {items: [{creator, topic, claim_text, quote, start_s, source_url, published_at, confidence}], n} | null
notes        [string]
as_of        string
```

Every block is independently nullable and every null carries a reason in
`notes`, so one dead source cannot empty the drawer.

### 9.3 Two things outside the panel layer

- **Fix the view's key names** (`t.team` → `t.short_name`,
  `res.ratings_note` → `res.notes`) and extend
  `tests/unit/test_web_contract.py` to assert that every key a view reads
  exists in the script's `result_schema`. The current test checks the caption
  and misses the row.
- **Extract `sv()` into `web/dist/js/components/svg.js`.** The eight-line SVG
  factory is copy-pasted in `template.js:85`, `creators.js:69` and
  `template-tools.js:63`; this page would make it four. Nothing else about the
  zero-build stance changes — it is one more ES module and one more `<link>`.

---

## 10. What I borrowed, and why

- **A ticker as the spine, opponent codes cased for venue.** Fantasy Football
  Scout's Season Ticker made the club × gameweek grid the default idiom for a
  reason: it answers "who plays whom" and "how does this run look" in one
  glance, and it scales to 20 rows without scrolling. I keep the grid and the
  casing convention `fixtures.py` already implements.
- **Separating attacking from defensive fixture difficulty.** This is the
  central borrowing, and it is not novel — FFScout has published separate
  attacking and defensive tickers, and the reason is the same one this
  warehouse demonstrates: the clubs whose two runs disagree are exactly the
  clubs a blended number hides. What I add is drawing the *disagreement*
  rather than leaving it to be inferred across two grids.
- **The attack-vs-defence plane as the mental model.** Opta/The Analyst's
  team-quality visuals put attacking output on one axis and defensive record
  on the other precisely because a single "team rating" is unreadable. Their
  version is a scatter; my rail is the same plane collapsed into one row so it
  can sit beside a calendar. The scatter survives as the secondary toggle.
- **Dixon–Coles rather than a bespoke rating.** Dixon & Coles (1997), building
  on Maher (1982): independent attack and defence parameters per club, a home
  advantage term, a low-score dependence correction `tau` on the 0-0/1-0/0-1/1-1
  cells, and exponential time-decay weighting of past matches. The repo already
  implements all four, with the decay half-life *tuned out of sample* over
  `(90, 150, 240, 400, 700, 1200)` days and landing on 400 — a 1X2 log loss of
  0.98184 against 1.03778 for a last-season-table baseline, and a clean-sheet
  Brier of 0.17022 against a 0.23202 base rate. The point of citing this is
  that the model is already better than FPL's FDR and already validated; the
  page's job is to stop discarding its output.
- **Market-implied strength as an anchor, not a replacement.** The standard
  practice in football modelling is to treat the closing line as the best
  available estimate and to de-vig before using it. The ingest path already
  writes a `fair#shin` book — Shin (1993) rather than proportional
  normalisation, because proportional de-vigging leaves the favourite–longshot
  bias in and systematically overstates longshots. I use `fair#shin` where it
  exists, say which method produced each number, and weight by freshness. What
  I explicitly do *not* borrow is the common practice of quoting a Poisson
  inversion as though it were a price: all 3,140 `clean_sheet` rows here are
  `derived#poisson` and the page labels them `derived`.
- **The drilldown pattern.** Straight from this repo's own `creators.js` — a
  memoised second panel call into a right-hand drawer, verbatim quotes with
  timestamps, a tier badge for extractor provenance, absent fields named in
  prose rather than rendered as zero, and errors captured per-block. It is the
  best thing in the application and it should be the template for every detail
  view.

*The specific published-source claims above (which tools split their tickers,
the exact form of each competitor's algorithm) are stated from general
knowledge of these tools and should be re-checked against their current sites
before any of it is quoted back to a reader. The in-repo numbers, the model
formulation and the warehouse facts are all verified here.*

---

## 11. Risks, and what would make me abandon this

**1. The edge may be too small to justify the surface.** Measured: 2.24 points
on the attack track and 3.05 on the defence track across the full league over
six gameweeks, against roughly four times that from team quality alone. A
reader who treats this page as an asset-picker rather than a tie-breaker will
be *actively misled*, and a beautiful chart makes that more likely, not less.
The second governing sentence is the mitigation and it is a weak one — nobody
reads the disclaimer under the chart. **Abandonment test:** backtest transfers
selected by split-ticker rank against transfers selected by the existing
blended parquet over 2023-24 to 2025-26. If the split does not beat the blend
on realised points-per-transfer, the split is a story rather than an edge, and
this page should be a two-column table.

**2. The market layer may never light up, leaving half the design hollow.**
Today: zero priced fixtures beyond GW1, 91-hour-old prices, and a model-side
odds reader that cannot parse the live `fact_odds` at all because of the
casing and key-form mismatches in §5.4. The design is model-first precisely so
it survives this — but "market" appears in the freshness strip, in the blend,
in the whiskers and in the drawer, and a permanently hollow pip in four places
is worse than no pip. **Abandonment test:** after the T−26h/T−3h refreshes
ship, measure coverage for two gameweeks. If fewer than 80% of the next
deadline's fixtures carry a price under 24 hours old, delete the market
channel from the page entirely and keep it as a model input only.

**3. Two of the drilldown's most valuable blocks are unbacked.**
`content_insight` holds 0 rows, has no panel reader, is written by code
nothing schedules, and stores its entity verbatim-as-spoken with no team
resolution — three separate things must land before a single creator quote
appears. `fact_predicted_lineup` has real rows but no panel has ever exposed
it. If the creator block ships empty and stays empty, the drawer teaches the
reader that opening a fixture is not worth the click, and that kills the
drilldown pattern for this page. **Abandonment test:** if `content_insight`
is still empty when the ticker ships, cut the block entirely rather than
shipping a permanently apologetic empty state — one honest gap is credible,
a second one in the same drawer reads as an unfinished product.

**Two smaller ones, recorded rather than argued.** The in-cell rail glyph may
be illegible below about 1280px, in which case the rail collapses to a
sortable numeric column and the chart lives only in the Divergence card. And
blanks and doubles — the two states an operator most needs the ticker for —
do not occur anywhere in the published 2026-27 schedule, so both code paths
will ship exercised only by synthetic fixtures.
