# Fixtures — design C

> Every number in this document was verified read-only against the live
> warehouse and the live panel API on 2026-08-27. The Dixon-Coles numbers come
> from a fresh fit through `Warehouse.read_copy()` at that instant
> (intercept 0.2386, home advantage 0.1890, rho −0.0739, half-life 400 days).
> Where a claim is someone else's, it is cited. Where I could not verify
> something, it says so.

---

## 0. The one sentence

> **Every fixture is two fixtures — one for your attackers, one for your
> defenders — and this page never averages them.**

That sentence goes at the top of the page in the slot where xPoints prints
"Numbers are copied from ingested providers, never modelled here" and Template
prints its rank-move identity. Everything below is a consequence of it.

The second sentence, printed underneath, states the assumption the colour
makes, because the colour is the page's biggest claim:

> Colour holds your own club at league average and asks only what the
> *opponent* does, at that venue. Arsenal's cell and Coventry's cell against
> the same opponent are identical on purpose — this is a fixture view, not a
> power ranking. The fixture-specific number, with your own club's strength in
> it, is one click away in every cell.

---

## 1. What is actually wrong today (measured, not asserted)

Six defects. Five are real; one item in the brief did not reproduce and I say
so rather than inheriting it.

**1.1 — The difficulty number is a subtraction that destroys the thing the
owner asked for.** `ratings_cache.opponent_difficulty()` computes, for each
(opponent, venue) pair:

```
lam_O = exp(c + g·[O home] + attack_O + mean_defence)   # what O scores off you
mu_O  = exp(c + g·[O away] + mean_attack + defence_O)   # what you score off O
strength = lam_O − mu_O                                  # ← the split dies here
difficulty = minmax(strength)
```

Both halves of the split already exist, on the line above the one that throws
them away. **The whole model change in this document is: stop subtracting.**

How much is lost is measurable. Over the 40 (club, venue) pairs in the current
fit, attack-ease and defence-ease correlate at only **ρ = 0.70 (Spearman
0.63)** — they share half their variance. Mean |attack-ease − defence-ease| is
**0.156** on the 0–1 scale, max **0.489**. Ranked separately out of 40:

| opponent, venue | λ_O | μ_O | blended FDR | rank as a **defensive** fixture | rank as an **attacking** fixture | gap |
|---|---|---|---|---|---|---|
| **Hull (h)** | 1.210 | 1.017 | 0.594 → "15th, average" | **28th** (easy: they won't score) | **4th** (brutal: you won't either) | **24** |
| Everton (h) | 1.236 | 1.127 | 0.561 | 27th | 6th | 21 |
| Sunderland (h) | 1.198 | 1.174 | 0.527 | 29th | 8th | 21 |
| **Newcastle (a)** | 1.437 | 1.510 | 0.489 → "16th, average" | **15th** (hard: they will score) | **31st** (kind: so will you) | **−16** |
| Liverpool (a) | 1.646 | 1.402 | 0.614 | 7th | 22nd | −15 |

Hull at home and Newcastle away are 8 places apart on the blended number —
i.e. indistinguishable — and are *mirror images*. One is a defender fixture
and an attacker trap; the other is the reverse. The blend reports both as
"average", which is the one thing neither of them is. This is the single
strongest empirical argument in this document and it comes from the repo's own
fitted model.

**1.2 — The colour scale is not comparable between weeks.** `difficulty` is
min-max normalised over whichever clubs happen to be in the season, so the
darkest cell means "the worst fixture currently available", not a fixed
quantity. If Coventry improve, every club's colour changes. Pinning the domain
is standard heatmap advice and the failure mode is documented
(<https://technofile.substack.com/p/beware-of-algorithmically-generated>).

**1.3 — The hue is a single sequential ramp on a quantity that has a real
zero.** `color-mix(in oklab, var(--s1) pct%, var(--surface))`. Difficulty
relative to an average fixture is signed; a sequential ramp cannot show sign.
Diverging is *correct here* precisely because the midpoint is meaningful — and
only because of that (see §5).

**1.4 — Odds are 91 hours stale and nothing on the page says so.** Verified:
`max(as_of)` on `fact_odds` is 2026-08-24 03:00 PT, **91 hours** before the
check. `provenance()` prints `generated_at`, which is when the panel *ran* —
always "seconds ago". A panel that reads a 91-hour-old table and stamps itself
fresh is lying by omission. Worse, **no fixture after 2026-08-24 is priced at
all**: the latest priced date in 116,285 odds rows is 2026-08-24. The market
leg is not stale, it is *absent* for every fixture the ticker shows.

**1.5 — Three fixture-key vocabularies that do not join.** All 116,285
`fact_odds` rows use `season:date:slug:slug`. But `h2h` uses short slugs
(`2026-27:2026-08-22:hull:man-united`) while `team_totals` uses long ones
(`2026-27:2026-08-22:hull-city:manchester-united`) — **overlap between the two
market families is 10 fixture keys out of 1,538**. And
`models/team_goals/odds.py::fixture_key()` builds a third form,
`"2026-27:11"`, which matches **zero** rows, so `MarketImpliedModel` currently
finds nothing at all. `match_fixture_keys()` computes the mapping and returns
it without persisting it.

**1.6 — The one claim in the brief that did not reproduce.** The brief says
`fixture_ticker` returns `rows: 0`. It does not, as of now:
`POST /api/scripts/fixture_ticker/run {"horizon":5}` returns `row_count: 20`,
GW2–GW6, with a `difficulty` on every opponent.
`fixture_difficulty.parquet` holds 740 rows fitted **today at 11:33 UTC**. The
tab is ugly and half-blind, not empty. I have designed for what is there.

**Also true, and load-bearing for §7:** `content_insight` holds 0 rows because
`insights_from_analysis()` in `ingest/content/analyze.py` **has no caller
anywhere outside its own module**. But 121 `content_analysis` rows are stored,
and one of them already carries 40 extracted insights, **12 of which are
`entity_kind='team'`** — real team talk, already paid for, never written:

> `tactical` · Hull — "Hull switched to a back five that was hard to break
> down against Man United." · `injury_return` · Everton — "Everton have Garner
> returning, which should improve their defence." · `fixture_swing` · Forest —
> "Forest have a good fixture against Coventry in gameweek five."

Note the spoken names in that payload: **"Forester", "Suddenland",
"Ipsswitch"**. `normalise_entity_ref()` lowercases and folds accents and
explicitly documents that it is *not* a resolver. So the wiring gap is two
things, not one (§9).

---

## 2. What I took from other people, and why

I looked at the serious tools before designing. Six ideas are borrowed; two are
deliberately refused.

**Borrowed — the attack / defence / overall split, from Fantasy Football
Scout's Season Ticker.** FFS offer three presets: Attack ("how likely you are
to score, judged against opponents' defences"), Defence ("clean-sheet
likelihood, judged against opponents' attacks") and Overall, each 1–5 in 0.5
increments, over an FFS Elo on a ~1000–1400 scale.
(<https://www.fantasyfootballscout.co.uk/how-to-use-the-season-ticker-feature-in-members-area>)
It works because the two questions have different answers, which §1.1 measures
for this repo's own model. I go further than FFS in one respect: their split is
still displayed as a 1–5 band, which throws away the rating that produced it. I
keep goals.

**Borrowed — Standard vs Relative colouring, also FFS.** Standard colours by
opponent strength alone (what official FDR does); Relative subtracts *your own*
club's strength, so Man City at Brentford and Burnley at Brentford are not the
same cell. FFS ship both. So do I (§5.4), and shipping both is what lets me
default to Standard without amputating the other question.

**Borrowed — "Sort by Rotation", FFS's best and least-copied idea.** Pin a
club; rank every other club by how well its run *complements* the pinned one.
This is the actual planning question ("who covers my Arsenal defender's bad
weeks") and nobody else surfaces it. My scoring function is different and
better suited to FPL (§5.5).

**Borrowed — real quantities in the cell, not bands.** FFS put odds-derived
Clean Sheet % and Projected Goals in each ticker cell. That is proven UI and it
is exactly what `scoreline.py::score_matrix()` already produces here for free.

**Borrowed — sum over the horizon, not mean.** The near-universal FPL
convention is average FDR = sum ÷ fixtures, specifically so double gameweeks
are comparable; the acknowledged bias is that at equal average, *more fixtures
is more chances*, so DGW clubs are under-rewarded
(<https://onsidearena.com/tips/fpl-fixture-difficulty-rating-2026-27>). I sort
on the **sum** by default — it handles DGW and BGW natively — and offer
per-game average as the secondary sort with the trade-off written on the
control.

**Borrowed — the colour-blind constraint, from ColorBrewer.** Exactly three of
ColorBrewer's diverging schemes are flagged *not* colour-blind safe: **RdYlGn,
RdGy, Spectral** (<https://colorbrewer2.org/>). FPL's standard green→red ticker
is literally the palette ColorBrewer flags, and the green/yellow boundary — the
one a ticker uses to separate "good fixture" from "neutral" — is the boundary
that collapses under deuteranomaly. Blue↔orange preserves the highest contrast
across protan, deutan and tritan simultaneously
(<https://davidmathlogic.com/colorblind/>). FFS's legacy blue→red scheme is,
not coincidentally, the accessible one. This repo's validated series palette is
already blue (`--s1 #2a78d6`) and orange (`--s2 #c25322` dark / `#eb6834`
light), re-validated in `creators.js` at CVD ΔE 25.9 protan. The accessible
palette is the *only* palette here, not an option in a menu.

**Refused — a hand-draggable rating slider.** FFS let users drag each team's
Elo and recolour the grid live. It is fun and it is wishful thinking: this
app's discipline is that every claim is traceable to a row, and a dragged Elo
is traceable to a mood. The "what if" this app permits is a *different fit*
(§3.4), which is auditable.

**Refused — matchup/style terms inside the difficulty number.** The owner asked
for tactics, styles and matchups. I show them (§7) and I refuse to model them,
because I could find no published study showing that opponent-specific
interaction terms beat a plain attack × defence model out of sample. The
strongest evidence is classification-level: KPI→outcome coefficients differ
between possession and counter-attacking clusters
(<https://pmc.ncbi.nlm.nih.gov/articles/PMC12954490/>), which is interaction
evidence but not an out-of-sample win. Every model in the Dixon-Coles lineage
is additive in log space with no interaction, and that is the state of the art.
PPDA in particular is entangled with territory — Stats Perform themselves call
it "somewhat one-dimensional", because a dominant side accumulates high
defensive actions as a *by-product* of possession
(<https://www.statsperform.com/insights/how-we-measure-pressure/>) — so it is
not an independent style axis. Style goes in the drilldown as *explanation*,
never in the colour.

**The bar to clear is lower than it looks.** Opta — the most credible data
company in the sport — publishes its fixture-difficulty visual as the
**unweighted arithmetic mean Opta Power Rating of a club's next N opponents**,
rendered as one horizontal bar chart across 20 clubs: no venue adjustment, no
recency weighting, no own-strength adjustment, and they say in prose that "the
order of those fixtures does matter" without modelling it
(<https://theanalyst.com/articles/premier-league-fixture-difficulty-2026-27-first-five-games>).
Opta Power Rankings are also a **single number with no attack/defence
components** — the claim in some secondary summaries that they publish separate
Attack and Defence power ratings is not supported by Opta's own explainer
(<https://theanalyst.com/articles/power-rankings-your-club-ranked>).

**And FPL themselves already have the split and hide it.** `bootstrap-static`
exposes `strength_attack_home/away` and `strength_defence_home/away` per club.
FDR collapses them to one 1–5 band. We do not even ingest them: `dim_team` is
`(season, team_code, team_id, name, short_name, as_of)` and nothing else. Two
columns of ingest give us a free external baseline to disagree with (§9.3).

*Could not verify:* Fantasy Football Fix publish no FDR methodology (their
"Why Fix" page is marketing). LiveFPL publish no fixture-difficulty methodology
at all — do not cite it as one. Fantasy Football Hub's ticker is a JS app that
would not render; reported behaviour is attack-rank/defence-rank/overall-rank
sorting over a chosen GW range. The only academic attempt at quantifying FDR's
predictiveness I found is an Aalto thesis whose repository 403s; its R² is
unextracted. **The "FDR is bad" consensus is argued qualitatively in public,
not measured** — which is a reason to ingest FPL's FDR and measure it here
(§9.3), not a reason to repeat the claim.

---

## 3. The difficulty model

Two numbers per (fixture, team), both in **goals per match**, both about the
opponent, both compared with the same league-average anchor.

### 3.1 The two quantities

For our club facing opponent *O*, with *O* at venue *v*:

```
defence_side:  λ_O(v) = exp(c + g·[O home] + attack_O  + mean_defence)
               "goals O is expected to score against a league-average defence"
               HIGH  ⇒ your GK/DEF are in trouble ⇒ hard defensive fixture

attack_side:   μ_O(v) = exp(c + g·[O away] + mean_attack + defence_O)
               "goals a league-average attack is expected to take off O"
               HIGH  ⇒ easy to score ⇒ easy attacking fixture
```

Identical to the existing code up to the point where it subtracts. Sign
convention in the fit is already the right one for this: `attack_O` positive
means O scores more than average, `defence_O` positive means O concedes more
than average (a leakiness parameter), which is why the promoted prior "reads
the way you would say it out loud".

**Display scale.** Both are expressed as a signed deviation from a fixed league
anchor and mapped to a diverging ramp:

```
anchor_def = mean of λ_O over the 40 (club, venue) pairs = 1.373 goals
anchor_att = mean of μ_O over the same population        = 1.368 goals
domain     = ±0.60 goals  (= ±2 SD; the population SD is 0.305 / 0.284)
```

Clipping at ±0.60 saturates 5 of 40 pairs (Arsenal and Man City at home;
Coventry and Ipswich away, plus Ipswich at home marginally) — a deliberate
choice: the four best and worst clubs in the league should read as off-scale,
and everyone else should use the full ramp rather than being crushed into the
middle by two outliers. The anchor and domain are recomputed only when the
league membership changes, i.e. at promotion, and they are **published in the
payload** so the legend is payload-led and never hardcoded (Template's rule).
This is what fixes §1.2: a cell's colour means the same thing in GW2 and GW32.

For reference, the fit's own league constants: neutral-venue average goal rate
`exp(c + g/2 + ā + d̄) = 1.339`; home 1.472, away 1.218; home-advantage
multiplier `exp(g) = 1.208`.

### 3.2 The market leg — one dedicated market per axis

The market's job is to update *ratings*, not fixtures. This is the part of the
design most people get wrong: if you drop a fixture-specific market λ straight
into a ticker cell, the ticker silently becomes a power ranking, because that λ
contains our own club's strength.

Pipeline, per priced fixture:

1. **De-vig.** Shin on 1X2 and totals (mutually exclusive, exhaustive), per
   `odds_derivation.md` §1a — already implemented, already measured (mean
   overround 1.0597, mean Shin *z* 0.0302, +0.0080 on the favourite vs
   multiplicative, +0.0185 max). The literature says this choice is
   second-order — Clarke, Kovalchik & Ingram (2017) found power best on tennis
   and additive best on horse racing, and penaltyblog measured multiplicative
   0.19724 vs logarithmic 0.19730 RPS over 380 EPL matches, i.e. negligible
   (<https://pena.lt/y/2025/09/14/from-biased-odds-to-fair-probabilities/>).
   **Do not re-litigate the de-vig.** It is not where the error lives.
2. **Invert to goal rates.** `invert_match_odds()` least-squares over
   `(log λ, log μ)` against three or four constraints. **Always pass the totals
   leg**: measured out-of-sample Brier 0.16198 with it vs 0.16526 without, and
   the mean predicted clean sheet falls from 0.2849 to 0.2480 against a
   realised 0.2204 (`odds_derivation.md` §3). ρ = 0: the repo's own rho verdict
   (§5 of that document) is that the correction buys nothing out of sample.
   Our fresh fit gives ρ = −0.0739, comfortably inside the published range
   (Dixon & Coles-lineage fits on EPL typically land near −0.13; dashee87
   −0.1285, `goalmodel`'s worked example −0.13), and it still does not pay.
3. **Where a dedicated market exists, prefer it — each axis has one.**
   - *Attack axis*: `team_totals` is a complete two-way market per line and
     inverts exactly. GW1 carried **4.7 distinct lines per fixture-side**
     (368 two-way lines, 6 US books, per-line overround 1.0712), and it agreed
     with the 1X2 inversion to **mean +0.0118 goals, max 0.0566, correlation
     0.9992**. A ladder pins the mean much harder than one 0.5 line does.
   - *Defence axis*: **the posted clean-sheet market is a direct, invertible
     read on the opponent's goal rate** — `P(CS) = P(opponent scores 0)`, so
     under independent Poisson `μ = −ln P(CS)`, one-dimensional, no
     supremacy/total decomposition needed. Every major book prices it as a
     standalone binary (typically 2.20–4.00). **We do not ingest it.** All
     3,140 `clean_sheet` rows in `fact_odds` have `bookmaker = 'derived#poisson'`
     — they are our own inversion written back, not a market. Fetching the real
     one gives the defence axis an independent market read, which is the single
     highest-value ingest addition in this document (§9.2). Caveat, stated on
     the page: clean sheet is a secondary market with a fatter margin than 1X2,
     so the de-vig error is larger; and **I could find no study comparing
     bookmaker CS probabilities against model-derived CS out of sample.** This
     repo can settle that itself with three seasons of stored odds — until it
     does, posted CS is a *cross-check and shrinkage target*, not the primary.
4. **Lift fixture rates back to ratings.** Fit the same DC parameterisation to
   the market-implied (λ, μ) as targets — a ~41-parameter linear least squares
   in log space, seconds not minutes — yielding `attack_O^mkt`,
   `defence_O^mkt` on the same scale as the fitted ones. This is the
   "Betting Odds Rating System" idea that `goalmodel` implements as
   `expg_from_probabilities()` → refit
   (<https://www.r-bloggers.com/2019/01/expected-goals-from-bookmaker-odds/>);
   its author flags those fitting modes as "experimental and a bit unstable",
   so the residual of that refit is surfaced, not swallowed (see 3.5).
5. **Blend in log space**, exactly as `blend.py` already argues (errors are
   roughly symmetric in log rates):

```
log λ_O = (1 − w)·log λ_O^fit + w·log λ_O^mkt          (same for μ_O)

w = 0.5 · coverage · freshness
    coverage  = share of the opponent's fixtures in the window with a usable quote
    freshness = 1        for age ≤ 12h
                linear   12h → 72h
                0        beyond 72h
```

`w_max = 0.5` is `blend.py`'s value and it has **not been tuned out of
sample** — its own model card says so. The page says so too, on the control.

**As of today `w = 0.00` for every fixture** (odds are 91h old, past the 72h
cutoff, and no fixture after 2026-08-24 is priced at all). The page must
therefore render honestly as a pure-model ticker with a red market chip reading
`market weight 0.00 — newest quote 91h old, cutoff 72h`, not silently fall
back. That is the design's most-exercised path on day one and it has to be the
best-looking one.

**Why 72h for odds but 7 days for the fit.** A fit moves only when matches
finish, so a week-old fit is at most one round out of date — the existing
`DIFFICULTY_STALE_DAYS = 7` is right. Odds move on team news, so a 91-hour
price predates the press conferences that decide the fixture. Different decay
laws, different cutoffs, both stated on their own chip.

### 3.3 Form

Form is already in the model: the fit weights each match `exp(−ξ·age)` with a
**400-day half-life**, tuned out of sample
(`docs/models/team_goals_half_life.csv`). That sits squarely inside the
practically evidenced range — dashee87's optimum on five EPL seasons was
ξ ≈ 0.00325/day (half-life ≈ 213 days), and `goalmodel` documents "good values
of xi is usually somewhere between 0.001 and 0.003" per day, i.e. half-lives
of roughly 230–690 days
(<https://dashee87.github.io/football/python/predicting-football-results-with-statistical-modelling-dixon-coles-and-time-weighting/>).
**I am not re-tuning it from a UI brief.**

What the page adds is a **residual, never a blend**: for each club, over its
last 6 matches, team xG for and xG against per game (aggregated from
`fact_player_fixture.expected_goals` / `expected_goals_conceded` joined through
`dim_player.team_code`) minus what the fitted rating expected. Shown as two
small numbers and a sparkline in the rail and the drilldown, labelled
"over/under its rating". It is a *diagnostic that the colour might be wrong*,
which is a different and more honest object than a third input. Note the
aggregation trap: `expected_goals_conceded` is written per player, so every
outfielder on the pitch carries the team's value — take one representative
value per team-match, never a sum (naïve summing gives ~30 xGC per fixture).

Caveat printed with it: this season has **one** completed gameweek (GW1: 10 of
380 fixtures finished). Until roughly GW6 the residual is noise and the panel
says so with the match count rather than drawing a confident sparkline over
n = 1.

### 3.4 The "what if" that is allowed

Instead of a draggable slider, one auditable alternative: a **second fit at a
200-day half-life** (dashee87's measured optimum, not an invented number),
computed by the same job and stored as a second artefact. The toggle shows the
*difference* between the two tickers — which clubs move and by how much — never
replaces the default. If a club's run changes materially under a shorter
memory, that is worth knowing and is a fact about the fit, not a wish.

### 3.5 Missing, degraded, refused

| condition | what happens |
|---|---|
| no `fixture_difficulty.parquet` | schedule only, no colour at all, empty state naming the job. Existing behaviour, correct, keep. |
| parquet older than 7 days | colour shown, chip red, fit date printed. Existing behaviour, keep. |
| fixture absent from the parquet (rescheduled after the fit) | hollow hatched cell, "added after the last fit" |
| no odds for the horizon | `w = 0`, market chip grey "not fetched", model ticker |
| odds present, > 72h | `w = 0`, market chip red **and the number the market would have given is still shown in the drilldown, greyed, with its age.** Stale market data is useful as a *contrast*; it is never in the colour. |
| odds present for some fixtures | `w` scaled by coverage; the coverage % is printed on the chip |
| market refit residual above threshold | the market leg is dropped for that club and the drilldown says "market prices for X are not consistent with any bivariate Poisson (residual 0.09)". A large residual is a data-quality signal `market.py` already computes and nobody reads. |
| club with < 3 matches in the fit window | dotted underline on the row; tooltip gives `rating_prior_share`. **Three of this season's twenty clubs are in this state** (Coventry, Hull, Ipswich are newly promoted) and today nothing shows it. |
| any source missing | **nothing is imputed, ever.** Hollow cell, named reason. |

---

## 4. The page, in order

```
┌───────────────────────────────────────────────────────────────────────────────┐
│  Fixtures                                                     GW2 · 3d 4h      │
│  Every fixture is two fixtures — one for your attackers, one for your          │
│  defenders — and this page never averages them.                                │
│  Colour holds your own club at league average and asks only what the opponent  │
│  does, at that venue. The fixture-specific number is one click away.           │
│                                                                                │
│  Inputs   ●fitted model 11h   ●market odds 91h STALE (w=0.00)  ●lineups 19h    │
│           ●team news 12h      ○creator team-talk — not wired (0 rows)          │
│           [ Refresh prices · 2 cr ]  [ Refresh full card · 42 cr ]  108/150 left│
│                                                                                │
│  Horizon  [3] [5] (6) [8] [10]      Lens  ( Both ) [Attack] [Defence]          │
│  Basis    (Opponent only) [Relative to my club]   Blend [model] (model+market) │
│  Sort     (Attack sum) [Defence sum] [Divergence] [Pair with: ARS ▾]           │
├──────────────┬────────────────────────────────────────────────────────────────┤
│              │  GW2     GW3     GW4     GW5     GW6     GW7                    │
│  ██████ 8.9  │ ┌────┐  ┌────┐  ┌────┐  ┌────┐  ┌────┐  ┌────┐                  │
│  ████   7.6  │ │ ██ │  │ ▓▓ │  │ ░░ │  │ ██ │  │ ░░ │  │ ▓▓ │  ← attack band   │
│  MCI         │ │bou │  │TOT │  │lee │  │HUL │  │eve │  │CRY │                  │
│              │ │ ▓▓ │  │ ██ │  │ ░░ │  │ ▓▓ │  │ ░░ │  │ ██ │  ← defence band  │
│              │ └────┘  └────┘  └────┘  └────┘  └────┘  └────┘                  │
│  ██████ 8.9  │ ...                                                             │
│  ██     5.1  │                                                                 │
│  EVE  ⚡     │   ⚡ = attack and defence disagree by 10+ ranks somewhere        │
│  ...  (20 rows)                                                                │
├───────────────────────────────────────────────────────────────────────────────┤
│  Legend   easy ██ ▓▓ ░░ ▒▒ ██ hard  ·  ±0.60 goals vs a league-average fixture │
│           upper band = your attackers · lower band = your defenders            │
│           CAPS = home, lower = away · hollow = blank · split = double          │
├───────────────────────────────────────────────────────────────────────────────┤
│  WHAT THE BLEND HIDES                                     (6 fixtures)         │
│  GW5 · you host Hull — 4th-hardest attack fixture of 40, 28th-hardest          │
│        defensive one. Buy the clean sheet, not the goals.        [open]        │
│  GW2 · you visit Newcastle — 15th-hardest defensively, 31st for attackers.     │
│        Goals both ways.                                          [open]        │
├───────────────────────────────────────────────────────────────────────────────┤
│  LEAGUE SHAPE  (scatter: attack rating × defence rating, crosshair at average) │
├───────────────────────────────────────────────────────────────────────────────┤
│  provenance · fixture_ticker · a1b2c3d · inputs listed above                   │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Above the fold**: the governing sentence, the freshness strip *with the fetch
buttons*, the toolbar, and the first ~8 rows of the grid. Freshness is above
the numbers, not below them, because the owner made it first-class and because
the reader should know how old a claim is before reading it.

**Below the fold**: "What the blend hides", the league-shape scatter, and the
drilldown drawer (opened from any cell or any club).

Toolbar rows are labelled with `.tlabel` spans in the xPoints idiom
(`Sources` / `Gameweeks` / `Filter` → here `Inputs` / `Horizon` / `Basis` /
`Sort`), because unlabelled chip rows are the thing xPoints fixed.

---

## 5. The central visualisation: the split-cell ticker

Rows are clubs, columns are gameweeks, and **each cell is one rectangle divided
horizontally into two bands**: the upper 55% is the attack lens, the lower 45%
the defence lens, both coloured on the *same* diverging scale in the same unit.
The opponent's short name sits across the seam, cased for venue.

### 5.1 What it makes obvious that a table cannot

1. **Torn cells.** A cell whose upper band is deep orange and lower band deep
   blue is visually *broken* — a horizontal seam of colour discontinuity. Those
   are exactly the fixtures FDR erases. In a table of two numbers you would
   have to read 400 numbers and do 200 subtractions to find them; here they
   pop pre-attentively out of a 20×6 field, and the eye finds them without
   being told what to look for.
2. **Run shape along a row.** Left-to-right you read whether a club's run is
   uniformly kind, front-loaded, or hits a wall at GW6 — and whether the two
   bands agree along the whole run or diverge in the middle of it. That is
   "what run of games" answered spatially, which is what the owner asked for.
3. **Vertical coherence down a column.** A gameweek where most of the grid is
   orange in the upper band is a low-scoring week — a bench-boost signal you
   cannot get from any single row.
4. **Two-club comparison without arithmetic.** Two rows next to each other,
   scanned across, answer "does his good run cover my bad one" — which is the
   rotation question (§5.5).

### 5.2 Every encoding channel, justified

| channel | carries | why this channel |
|---|---|---|
| **row position** | club, ordered by the horizon aggregate under the selected sort | Position is the strongest quantitative channel and the page's *primary* question is a ranking ("who has the easiest run"), so the ranking gets position. |
| **column position** | gameweek, left to right | Time on x. There is no defensible alternative. |
| **within-cell vertical split** | which lens: attack above, defence below, fixed order, never swapped | The two lenses are the *same measure on two subjects*, not two categories, so they get a *subdivision*, not a second hue. Fixed order because a reader must be able to learn "top = my forwards" once. |
| **colour (hue + lightness)** | difficulty vs a league-average fixture, diverging, blue = easy → neutral → orange = hard | Colour is a weak quantitative channel *on purpose*. Difficulty is a soft, uncertain estimate; encoding it in a channel that reads approximately is honest. Precise values live in the tooltip and drilldown. Diverging is licensed here and only here: the midpoint is a real quantity (a league-average fixture), not the middle of whatever is on screen. |
| **text in the cell, cased** | opponent, home/away | The existing house convention (the Telegram grid and the current panel both use UPPER = home). Costs zero extra channel. |
| **cell outline: hairline dashed** | this fixture is also priced by a fresh market (`w > 0`) | A *data-state* mark, deliberately non-quantitative and nearly invisible, so provenance is present without competing with the data. |
| **cell hollow / hatched** | blank gameweek, or fixture missing from the fit | Absence must not look like a value. |
| **cell split into two half-height stacks** | double gameweek, both opponents coloured | A DGW is two decisions; it gets two marks. |
| **row dotted underline** | the club's rating is substantially prior, not data (promoted) | Uncertainty as a *texture*, not as opacity — opacity reads as "unimportant", which is wrong. |
| **⚡ glyph on the row label** | this club has ≥1 fixture in the horizon where the two lenses disagree by ≥10 ranks | A pointer, not a measure; it earns its place because §1.1 shows the disagreement is the finding. |
| **left rail: two small bars** | horizon sums for attack and defence, same axis, same scale | Length is the strongest quantitative channel and the *summary* is what the row sort is on. Two bars on one shared axis is small multiples, **not** a dual axis — there is one scale and one unit. |

**Not encoded, deliberately**: cell size (a fixture is a fixture); a third hue
(the palette is two series hues plus neutral, and a third would break the fixed
categorical order the repo enforces); opacity-as-confidence; and any team-brand
colour (20 club colours is not a palette, it is a collision).

Marks are thin: 1px cell gaps, grid lines at `var(--line)` and nothing heavier,
no cell borders except the dashed provenance hairline, no drop shadows. The
grid recedes; the colour field is the figure.

### 5.3 Why this beats the alternatives I considered

- **vs one blended ticker (FFS Overall, FPL FDR, Opta's opponent-rating bar
  chart)** — this is the brief. And §1.1 measures the cost: ρ = 0.70, a 24-rank
  gap on Hull at home, mirror-image fixtures reported as identical.
- **vs two tickers side by side (attack ticker, defence ticker)** — honest but
  it puts the comparison 400px apart. Divergence becomes a memory task instead
  of a perceptual one, and divergence is the whole product. The split cell puts
  it inside 20 pixels.
- **vs an attack/defence quadrant scatter of clubs** (Opta team-styles idiom;
  Experimental 361's canonical attack/defence scatters with league-average
  crosshairs, <https://www.experimental361.com/p/scatter-graphics-visualising-attacking>)
  — a scatter shows *team quality* and throws away *sequence*, and "what run of
  games" is a sequence question. It also duplicates an idiom `template.js`
  already owns. It earns a place as a secondary panel, below the fold, where I
  do use it — with the crosshair at league average and, per that source's own
  warning, **the defensive axis inverted** so "good at both" is one corner
  rather than two.
- **vs 20 overlaid difficulty-vs-gameweek lines** — spaghetti, and it needs 20
  distinguishable hues.
- **vs a bump chart of run-quality rank across the horizon** — answers "who
  improves", not "who is good", and has the same 20-hue problem.
- **vs a table with attack and defence columns per gameweek** — 240 numbers.
  The reader's task is lookup-and-compare across two dimensions, which is
  precisely what a matrix does and a table does not. The table exists: it is
  the CSV export and the drilldown.

### 5.4 The Basis toggle — the design's most contested call

Default: **opponent-only**. Alternative: **relative to my club** (subtract our
own attack/defence rating, FFS's "Relative" mode).

I default to opponent-only because a ticker whose colour includes our own
strength paints Arsenal's entire row blue and Coventry's entire row orange —
which tells you who is good, not which fixtures are good, and you already knew
who is good. Holding our club at league average is the one thing FPL's FDR gets
right. But relative is a real question — for choosing between a City asset and
a Brentford asset it is *the* question — and FFS ship both for that reason, so
this ships both, one click, with the assumption printed under the title in
whichever mode is active.

Corollary, and it is load-bearing: **the drilldown is always fixture-specific.**
The cell is a fixture view; the drawer is a match preview.

### 5.5 "Pair with" — the rotation sort

Pin a club; every other club is scored by how well its run *covers* the pinned
club's bad weeks. FFS pioneered the control; my scoring function is different
and, I think, better for FPL, because an FPL manager does not average two
players — they *play the better one*:

```
pair_score(A, B) = Σ over the horizon of  max( ease_A(gw), ease_B(gw) )
```

evaluated under whichever lens is selected, with blanks contributing the other
club's value and doubles contributing both. Correlation of difficulty — the
obvious choice — is wrong here: two clubs can be perfectly anti-correlated and
both mediocre. Best-of-two is what the rotation actually delivers.

---

## 6. Freshness and triggers

The owner's words: "think how often you pull these odds, it should be fresh and
make sure we don't use [stale] data. we need to have a pipeline as well as a
trigger mechanism to fetch latest data." Four parts.

### 6.1 The page tells you how old every input is, separately

One chip per input, using the existing `freshdot` component from `xpoints.js`,
in the strip above the grid. Each chip: name · age · what staleness *does*.

```
● fitted model 11h        green   fitted 2026-08-27 11:33Z · rebuilt after every result
● market odds 91h STALE   red     past the 72h cutoff → market weight 0.00
● predicted lineups 19h   green   rotowire · GW2 · 20 clubs · 587 rows
● team news 12h           green   32 availability items in the last 7 days
○ creator team-talk       grey    0 rows: extraction built, never wired (§9.1)
```

Colour thresholds are **per input**, from the payload, because staleness means
different things per source — the DAG already reasons this way with per-task
`STALE_WINDOWS` and the same reasoning belongs on the page.

Contract consequence: `provenance()` prints `generated_at` today, which is
always "seconds ago" and therefore worthless. Every panel result gains
`inputs[]` (§9.1). I would argue this belongs on every panel in the app, not
only this one, and it is a small enough change that the fixtures rebuild is a
reasonable place to introduce it.

### 6.2 The pipeline

Today `presser_projection_refresh` fires once per gameweek at T-30h and runs
`ingest_odds.py --fixtures` (football-data.co.uk, free) then rebuilds the
difficulty parquet. Two consequences, both visible in the data:

- football-data publishes E0 fixtures only a day or two ahead — the script's own
  note says so — so a single weekly pull cannot cover a 6-gameweek horizon.
  Hence: newest priced date 2026-08-24, nothing beyond.
- `ingest_odds_extras.py` — the script that fetches the *real* extra markets and
  writes `fact_odds_derived` — **is not in the DAG at all.** Verified: no
  reference to it outside `scripts/` and `tests/`. That is why
  `fact_odds_derived` holds exactly 1,720 rows over 10 fixtures, all GW1, all
  stamped 2026-08-19.

Proposed schedule, deadline-relative like everything else in `DEADLINE_OFFSETS`:

| task | fires | cost | refreshes | stale window |
|---|---|---|---|---|
| `odds_prices` | T-72h, T-30h, T-6h, T-2h | **2 credits** (uk h2h + totals, featured endpoint, all 10 fixtures in one call) | `fact_odds` h2h/totals | 12h |
| `odds_extras` | T-30h, gated by the ledger | **22 cr** without the US team-totals leg, **42 cr** with it | correct_score, btts, team_totals, `fact_odds_derived` | 20h |
| `fixture_ratings` | on every result landing, and T-30h | free | `fixture_difficulty.parquet` (both lenses, both half-lives) | 20h |
| `market_ratings` | immediately after `odds_prices` | free | market-implied attack/defence + blend weights | 12h |

**The budget is the design constraint and must be stated, not footnoted.** The
key allows 500/month; the expansion enforces its own `EXPANSION_MONTHLY_CAP =
150`; the August ledger shows 42 spent in one run. Four price pulls plus a full
extras card is 4×2 + 42 = **50 credits per gameweek = 200/month**, over the cap.
Therefore: `odds_prices` every gameweek unconditionally (8 credits/month for
four pulls × 4 gameweeks = 32), and `odds_extras` at the **cheap 22-credit
setting** by default (88/month), for **120 of 150** — inside the cap with
headroom for one manual full card. The expensive US team-totals leg is spent
only where it changes a decision: a planned chip week, or a week where the two
λ estimates disagree by more than 0.1 goals (the repo's own "suspect a stale
line" threshold from `odds_derivation.md` §6b). The page prints the month's
spend and remaining cap next to the fetch buttons, from the ledger, so the
constraint is visible where the spending happens.

### 6.3 The success-lie, which has to be fixed first

`presser_projection_refresh` returns `TaskResult(outcome="delivered", ...)`
regardless of whether its steps succeeded; failures appear only inside a
free-text `detail` string. So `dag_firing` records a success for a run whose
odds step failed — which is precisely the reported symptom, "the nightly job
hit a credit cap and reported success anyway".

Fix, and it is small: `outcome` becomes `delivered` only when every step a
consumer depends on succeeded, and `degraded` otherwise, with a per-step row
written so the page can read *which input last failed, when, and why*. A red
freshness chip must be able to mean two different things — "the data is old"
and "the job that refreshes it is broken" — and today it can express neither.

### 6.4 What "fetch now" does

Two buttons, never one, because they cost two orders of magnitude apart.

**`Refresh prices` — 2 credits.** Always available. On click:

1. Server-side dry run first (`ingest_odds_extras.py --fetch --dry-run` already
   prices a run and spends nothing). Confirm sheet: *"2 credits: h2h + totals,
   10 fixtures, uk region. 108 of 150 remain this month. Last successful fetch
   91h ago."* **Nothing is spent on a click alone.**
2. On confirm, the button becomes a step ledger in the `renderJobs()` idiom
   from `creators.js` — one line per step, each with its own tick, seconds and
   row count.
3. On completion: re-devig, re-invert, re-fit the market ratings, recompute
   `w`, re-render. **It does not refit Dixon-Coles** — that is ~1 minute and
   has its own trigger on results landing. The panel budget is 10s and a fetch
   must respect it.
4. On failure it says which step failed *and what that costs the page*:
   "team_totals unavailable in the uk region — the attack axis falls back to the
   1X2 inversion, which agreed with the ladder to within 0.02 goals across GW1."

**`Refresh full card` — 42 credits.** Disabled, with the reason printed, when
the ledger says the cap would be breached. Same confirm-then-ledger flow, plus
an explicit line naming what the extra spend buys: correct-score cells for the
clean-sheet cross-check, BTTS, and the team-totals ladder.

**Rate limit**: one manual full card per hour, enforced server-side. A
frustrated click loop is how a 500-credit month evaporates.

**After either**, the freshness chips update in place and the grid re-renders
with the new `w`. If `w` moves off zero for the first time, the page says so
in one line — the colour just changed meaning and the reader deserves to know.

---

## 7. The drilldown: one fixture, expanded

Any cell opens the drawer (the `aside.drawer` component xPoints and Creators
already share, Escape to close). Sections in order, each with its own age.

**1 · Head.** `GW5 · Sat 19 Sep 14:00 · Everton (h) v Hull`. Two bars, same
scale as the grid: *your attackers: 0.35 goals below an average fixture (4th
hardest of 40)* / *your defenders: 0.16 goals better than average (28th of
40)*. The rank pair is the headline because the rank pair is the finding.

**2 · Where the number comes from.** Three rows — fitted model (λ, μ, weight),
market (λ, μ, weight, age, book count, refit residual), blended result — and
when the market weight is zero, the row says why in words. This is the
"every claim traceable to a row" rule applied to a modelled number.

**3 · The match, as probabilities.** Straight off `score_matrix()` with the
blended rates: P(clean sheet) both sides, P(concede 2+), P(over 2.5), 1X2.
Where a market version exists, it sits beside the model version, and where they
disagree by more than **3 percentage points** the pair is flagged rather than
averaged — the rule `odds_derivation.md` §4 already established for
`cs_grid#power` vs `poisson_indep` ("they are two different estimators with
different biases, and the mean would hide the signal that their gap carries").

**4 · Team news.** `fact_player_state` (status, `chance_of_playing_next_round`,
`news`, `news_added`) for both clubs — currently 57 injured, 21 doubtful, 42
unavailable across the league — plus `intel_item` where `kind='availability'`
(32 items in the last 7 days). Ordered by ownership so the ones that move
decisions lead. Every row carries its own timestamp; a 3-day-old injury note is
not the same claim as a 3-hour-old one.

**5 · Predicted XI.** `fact_predicted_lineup` — rotowire, GW2, 587 rows,
20 clubs, `as_of` 19h — drawn as a formation, `certainty` shown per player,
provider and age named. Where the gameweek has no prediction yet: "rotowire
publishes around T-48h; nothing for GW5 yet", not a blank box.

**6 · Set pieces.** `set_piece_duty` covers all 20 clubs
(corners_indirect 85, penalties 73, direct_freekicks 57, freshest 2026-08-25)
and `set_piece_change` carries `delta_goals_per_game` with a headline. Nothing
in the UI renders either. This is the highest-value team-level intel in the
warehouse. **Framed as duty, not as a team trait**, because that is what the
evidence supports: set-piece goals-over-expected has season-to-season
correlation ≈ 0.12, essentially noise, while *shots* over expected persists at
≈ 0.63 (<https://www.expectinggoals.com/p/the-set-piece-revolution>). So "who
takes them" is durable and shown; "this team over-performs on set pieces" is
not durable and is not shown.

**7 · Previous meetings.** From `fact_fixture`, both orientations, four
seasons — Arsenal v Aston Villa returns 8 completed matches, with scores. Where
`fact_player_fixture` covers them, the two clubs' xG in those meetings sits
beside the scoreline, so the reader sees whether the result matched the
process. **The section's own sentence carries the caution**: eight matches
across four seasons, with different managers and mostly different players, is
not evidence about Saturday. Head-to-head is the most over-read object in
fixture analysis and the page should say so *where it shows it*, not in a
footnote.

**8 · Creator team-talk.** `content_insight` where `entity_kind='team'` and the
resolved club is either side, restricted to topics that are about the fixture:
`tactical`, `fixture_swing`, `injury_return`, `set_pieces`, `minutes`. Verbatim
quote, creator, published timestamp, deep link to `start_s`, and the evidence
tier shown the way Creators shows it (an `llm:` considered take is drawn solid;
a keyword window is drawn hollow — three keyword hits must never look like
three opinions). Today the table has 0 rows, and the section renders as a
named gap, not whitespace: *"Creator team-talk is extracted but never written:
`insights_from_analysis()` has no caller. 121 analyses stored, 1 carries
insights, 12 of those are team-level."*

**9 · Style, as explanation only.** What can honestly be computed from
`fact_player_fixture`: team xG for and against per game, goals vs xG, clean
sheet rate, and defensive-contribution volume, home and away split. What cannot:
PPDA, field tilt, sequence types, defensive line height, crossing profile —
none of that event data is in this warehouse, and pretending otherwise would be
the worst failure this page could commit. The section says what it has and
names what it does not. Per §2 it never enters the number.

**10 · Cross-links.** xPoints filtered to this club and gameweek; Template for
the club's assets' EO; Creators for the source of any quote. The rank-relevance
point belongs here in one line, from `rank_objectives.md` §0: the engine
optimises P(top-1k), and rank move ≈ Σ (my multiplier − field EO) × points, so
**an easy run that everyone can see is priced into the field's transfers.** An
easy run on a 2%-owned club is an edge; the same run on a 60%-owned club is
insurance. The drilldown shows EO next to the fixture number so that
distinction is visible. It is not encoded in the ticker — that would be a third
channel on a cell that already carries two — and it is not silently folded into
difficulty, because difficulty is a fact about football and EO is a fact about
managers.

---

## 8. Every empty and degraded state

Model and market states are in §3.5. The rest:

| state | what renders |
|---|---|
| no fixtures for the horizon | existing `empty()` naming `make ingest`. Correct, keep. |
| every deadline passed | existing behaviour: start at the last known GW with a note. Keep. |
| blank gameweek | hollow cell, the word `blank`, and the rail's per-game average divides by the *actual* fixture count |
| double gameweek | two half-height cells; the horizon **sum** counts both, which is the point of the sort being a sum |
| a club not in this season | dropped entirely, as today — never rendered as five blank weeks |
| promoted club (Coventry, Hull, Ipswich) | dotted row underline; tooltip gives `rating_prior_share` and the match count |
| < 6 completed matches this season | the form residual renders as "n = 1, not yet meaningful" rather than a sparkline over one point. **This is the state today.** |
| `content_insight` empty | named gap with the counts and the missing caller (§7.8) |
| no predicted lineup for the GW | named gap with the publisher's usual lead time |
| no historical meetings | "Coventry and Brighton have not met in the Premier League. There is nothing to show and nothing to infer." |
| the fetch is refused by the budget | the button is disabled and the reason is on it: "42 credits needed, 30 remain this month (cap 150). Next reset 1 Sep." |
| a DAG step failed since the last render | the affected chip goes red with the *job* failure, distinct from age (§6.3) |
| panel exceeds its 10s budget | must not happen: the grid is a parquet read plus one fixture query. If the market refit is slow, it is dropped and `w = 0` with a note. **A fit never runs inside a panel.** |

An honest count, so the build is not surprised: on today's data, sections 3
(market half), 8 (creator talk) and 9 (style) of the drilldown render as named
gaps, and the market chip is red. **The drilldown is roughly 40% empty states
on day one.** That is a real risk (§10.3) and the mitigation is that the gaps
name the specific missing wiring rather than shrugging.

---

## 9. Contract additions, field by field

### 9.1 `fixture_ticker` result

Added on `teams[].fixtures[].opponents[]`:

| field | type | meaning |
|---|---|---|
| `attack_difficulty` | number 0–1 | display-scale position on the diverging ramp, attack lens |
| `defence_difficulty` | number 0–1 | same, defence lens |
| `attack_xg` | number | μ_O — goals a league-average attack takes off this opponent at this venue |
| `defence_xg` | number | λ_O — goals this opponent scores against a league-average defence |
| `attack_rank` / `defence_rank` | int | rank out of the 2N (club, venue) population |
| `basis` | enum | `model` \| `model+market` |
| `market_weight` | number 0–1 | the `w` actually applied |
| `market_age_hours` | number\|null | age of the newest quote used |
| `n_books` | int\|null | books behind the de-vigged consensus |
| `market_residual` | number\|null | the refit residual; above threshold the leg was dropped |
| `rating_prior_share` | number 0–1 | how much of the opponent's rating is prior rather than data |
| `p_clean_sheet` | number\|null | model, off the score matrix |
| `p_clean_sheet_market` | number\|null | market, where a real CS quote exists |
| `difficulty` | number | **kept, unchanged, deprecated in the description** so nothing downstream breaks |

Added on `teams[]`:

| field | meaning |
|---|---|
| `horizon` | `{attack_xg_sum, defence_xg_sum, attack_per_game, defence_per_game, attack_rank, defence_rank, rank_gap, n_fixtures, n_blanks, n_doubles}` |
| `rating` | `{attack, defence, attack_pct, defence_pct}` — the fitted parameters, so the drilldown can show its own inputs |
| `form` | `{window_matches, xg_for_pg, xg_against_pg, xg_for_resid, xg_against_resid}` — the residual, never blended |

Added at the top level:

| field | meaning |
|---|---|
| `inputs[]` | `{name, as_of, age_hours, rows, state: fresh\|stale\|missing\|failed, refresh_job, effect_when_stale, last_job_outcome}` — **the freshness contract.** I would put this on every panel in the app. |
| `scale` | `{kind:"diverging", unit:"goals per match vs a league-average fixture", domain:[-0.6,0.6], anchor_attack, anchor_defence, clipped_pairs}` — so the legend is payload-led, per Template's "nothing is labelled from a hard-coded string" |
| `divergent[]` | `{gw, fixture_id, team_code, opponent_code, attack_rank, defence_rank, gap, sentence}` — the "what the blend hides" strip |
| `credits` | `{month, spent, cap, remaining, last_fetch_at, cost_prices, cost_full, blocked_reason}` |
| `basis` | echo of the requested `opponent_only` \| `relative` so the subtitle describes the numbers, never the request |

New params: `lens` (`both`\|`attack`\|`defence`), `basis`, `blend`
(`model`\|`model+market`), `pair_with` (team_code), `half_life` (`400`\|`200`).

### 9.2 Two new scripts

- **`fixture_detail(fixture_id)`** → the §7 payload: provenance rows, score
  matrix, team news, predicted XI, set-piece duties and changes, previous
  meetings with xG, creator team-talk, style summary, EO cross-link data.
- **`fixture_refresh(scope: "prices"|"full", dry_run: bool)`** → the trigger.
  Dry run returns `{planned_credits, remaining, cap, allowed, reason, steps[]}`
  and spends nothing; a real run streams the step ledger. Server-side rate
  limit lives here, not in the client.

### 9.3 Warehouse and ingest

1. **Persist the fixture-key mapping.** `match_fixture_keys()` already computes
   `fixture_key → fixture_id` and returns it without storing it. Materialise it
   as an artefact next to `fixture_difficulty.parquet` (same read-copy
   discipline, same "this is a cache not a fact" argument), refreshed by
   `market_ratings`. Without it the market leg is unbuildable: 100% of
   `fact_odds` uses slug keys and `MarketImpliedModel` looks up `"2026-27:11"`.
2. **Reconcile the two slug vocabularies.** `hull` vs `hull-city`,
   `man-united` vs `manchester-united`, `ipswich` vs `ipswich-town`,
   `coventry` vs `coventry-city`. Overlap between the h2h family and the
   extra-markets family is 10 keys out of 1,538. One canonical slug, applied at
   ingest, with the alias table in `FD_TEAM_ALIASES`.
3. **Ingest the real clean-sheet market.** Today all 3,140 `clean_sheet` rows
   are `bookmaker='derived#poisson'` — our own inversion, not a market. A posted
   CS quote is a direct, one-dimensional read on the opponent's goal rate and
   gives the defence axis an independent source. Store it beside the derived
   one, never overwriting: `fact_odds_derived`'s primary key already includes
   `method` precisely so competing derivations coexist.
4. **Ingest FPL's own strength fields and FDR.** `bootstrap-static` exposes
   `strength_attack_home/away` and `strength_defence_home/away` per club, and
   the fixtures endpoint exposes `team_h_difficulty`/`team_a_difficulty`.
   Neither is stored: `dim_team` has no strength columns and `fact_fixture` has
   no difficulty columns. Four columns and two integers give a free external
   baseline — and make "our number beats FDR" a *measurable* claim in this repo
   rather than a received opinion, which §2 notes nobody has actually measured
   in public.
5. **`content_insight.team_code`, nullable int, plus a resolver.** The column
   is missing and `normalise_entity_ref()` explicitly declines to be a
   resolver. The resolver must survive ASR: the stored analyses contain
   "Forester", "Suddenland", "Ipsswitch". Reuse `ingest/rivals/names.norm` as
   the base and add a club alias table with edit-distance matching **for clubs
   only** — a 20-way closed vocabulary makes fuzzy matching safe in a way it is
   never safe for players, which is why the player path refuses it.
6. **Call `insights_from_analysis()`.** It has no caller. Wiring it into
   `ingest/content/pipeline.py` costs nothing at analysis time. A backfill over
   the 121 stored `content_analysis` rows recovers 40 insights (12 team-level)
   with **no new LLM spend**; older analyses predate the `insights` field, so
   the backfill is honest about recovering little and the value is forward.
7. **`fixture_difficulty.parquet` gains `attack_xg`, `defence_xg`,
   `rating_prior_share`, `half_life_days`,** and is written twice (400d and
   200d). `difficulty` stays for compatibility.

---

## 10. The three biggest risks, and what would make me abandon this

### 10.1 The split may not survive horizon aggregation

Per fixture the split is large: ρ = 0.70, rank gaps up to 24 places. But
schedules average out. Over the GW2–GW7 window, aggregating each club's six
fixtures, the largest attack-vs-defence rank gap across all 20 clubs falls to
**9** (Brighton: 4th-best defensive run, 13th-best attacking) and 13 of 20
clubs sit within 3 places of themselves. The grid's split is clearly justified;
the **rail's** split may be decoration.

*What would make me abandon it:* measure the distribution of
|attack_rank − defence_rank| on the 5-gameweek rail across a full completed
season (2025-26 is in the warehouse: 380 fixtures, all finished). If the 90th
percentile is under 4 places, collapse the rail to one bar, keep the split only
inside the cells, and say plainly on the page that over long horizons the two
questions converge. That is a cheap experiment and it should be run *before*
the rail is built, not after.

### 10.2 The market leg may never actually be fresh, which makes a third of this design theatre

The honest current state: every odds row is 91–206 hours old; **no fixture after
2026-08-24 is priced at all**; the extras job is not in the DAG; 100% of odds
rows cannot be joined to a fixture id; the expansion cap is 150 credits/month
against a natural spend of ~50/gameweek. The design leans on "model + market",
and on day one the page renders as "model, and a red chip".

*What would make me abandon it:* run the §6.2 schedule for one month and
measure coverage — the share of horizon fixtures with a usable quote under 24
hours old. If that is below ~60%, cut the market from the ticker entirely,
make it a drilldown-only disclosure, and relabel the page as a model ticker in
its governing sentence. A blend that is `w = 0` three weeks in four is worse
than no blend: it is a control that implies a capability the pipeline does not
have.

### 10.3 Opponent-only is a deliberate amputation, and the drilldown that compensates for it is 40% empty

Holding our club at league average is what makes the ticker a *fixture* view.
It also means Arsenal's cell against Hull and Coventry's cell against Hull are
identical, which is false as a prediction. The design absorbs that by making
every cell one click from a fixture-specific drawer — but on today's data that
drawer's market section, creator section and style section are all named gaps,
and its head-to-head section is 8 matches that the page itself tells you not to
trust. If the compensating drilldown is mostly empty, the amputation is not
compensated; it is just an amputation.

*What would make me abandon it:* if, after §9's wiring lands, the drilldown is
still more than a third empty states, then opponent-only is not affordable and
the ticker should go fixture-specific by default (FFS's Relative mode as the
default rather than the alternative) — accepting that Arsenal's row goes blue,
that the page becomes partly a power ranking, and that the governing sentence
has to change to say so. I would rather ship a page that is honestly a power
ranking than one that is a fixture view with nothing behind the click.

---

## 11. What I would build first

1. `ratings_cache` stops subtracting; the parquet gains `attack_xg`,
   `defence_xg`, `rating_prior_share`. One function, no new maths, and it makes
   the whole page possible. *(half a day)*
2. `inputs[]` on the panel result and the freshness strip on the page. Nothing
   else should ship before the page can tell the truth about its own age.
3. The split-cell grid, pure model, `w = 0`, honest red market chip. This is the
   real product.
4. `fixture_detail` with the sections that have data today: score matrix, team
   news, predicted XI, set pieces, previous meetings.
5. The §9.3 wiring — key mapping, slug reconciliation, `insights_from_analysis`
   caller, `content_insight.team_code` — which unblocks the market leg and the
   creator talk together.
6. The DAG tasks and the fetch buttons.

Steps 1–3 alone answer the brief's core complaint. Everything after that is the
difference between a good fixtures page and this repo's best one.
