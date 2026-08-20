# Odds derivation: from quoted prices to model priors

`fact_odds` holds what a bookmaker *quoted*. This document is the arithmetic
that turns those quotes into the things the projection ensemble actually
consumes — a per-team clean-sheet probability, a per-team goal rate, a
per-player scoring rate — and the measurements that decide which derivation to
believe when two of them disagree.

Every number below is measured. Historical claims come from
`scripts/backtest_clean_sheets.py` over the football-data.co.uk closing
consensus for 2022-23, 2023-24 and 2024-25 (380 matches each). Live claims come
from the real GW1 2026-27 cards ingested on 2026-08-20 for all ten fixtures
(`scripts/ingest_odds_extras.py --fetch`). Nothing here is illustrative.

Code: `fpl_edge/ingest/odds_derived.py` (maths + pipeline),
`fpl_edge/ingest/odds_markets.py` (extra-market ingestion + budget),
`fpl_edge/ingest/odds.py` (de-vig primitives).
Outputs: `cs_brier.csv`, `cs_calibration.csv`, `cs_rho_grid.csv` in this
directory.

---

## 1. De-vigging: the choice is made per market *type*, not per market

A book's quoted implied probabilities `q_i = 1 / o_i` do not sum to 1. What you
do about that depends entirely on whether the selections are **mutually
exclusive and exhaustive** or **independent yes/no propositions**. Getting this
wrong is not a tuning error, it is a category error.

### 1a. Mutually exclusive and exhaustive → Shin

1X2, totals, correct score, BTTS: exactly one outcome occurs, so the fair
probabilities must sum to 1 and the excess `S − 1` is margin. Shin (1993)
models that margin as the book's protection against insider order flow and
solves for the insider share `z` that makes the fair probabilities sum to one:

```
p_i = ( sqrt( z² + 4(1 − z) q_i² / S ) − z ) / ( 2(1 − z) ),    S = Σ q_i
```

Shin is the default (`devig(..., "shin")`) because books do not spread margin
uniformly — they load it onto longshots — and the multiplicative de-vig
(`p_i = q_i / S`) therefore understates favourites. Measured on the ten GW1
1X2 consensus cards (20–21 books each, mean overround **1.0597**, mean Shin
`z` = **0.0302**):

| | Shin − multiplicative |
|---|---|
| on the favourite | **+0.0080** mean, +0.0185 max (Arsenal, at p ≈ 0.83) |
| on the longshot | **−0.0049** mean |

The gap scales with how lopsided the market is: on the near-even
Ipswich–Sunderland card it is +0.0012, on Arsenal–Coventry +0.0185. That is
1.9 percentage points of clean-sheet-relevant probability on exactly the
fixtures where a defender pick is being decided, so the choice is not cosmetic.

Measured overrounds on GW1, for reference:

| market | overround | note |
|---|---|---|
| 1X2 (uk consensus) | 1.0597 | 20–21 books |
| Over/Under 2.5 (uk) | 1.0595 | |
| BTTS (uk consensus) | 1.0639 | 10–11 books |
| team totals, per line (us) | 1.0712 | 368 complete two-way lines, 6 books |
| correct score, William Hill | **1.5250** | 44–47 cells — margin *plus* truncated tail |
| correct score, Betfair exchange | **0.9556** | 16 cells — the missing tail exceeds the margin |

The last two rows are the reason correct score gets its own treatment
(§2).

### 1b. Independent yes/no props → per selection, never across selections

Anytime goalscorer is **not** a book. Eleven players can all score in the same
match, so the fair probabilities have no reason to sum to anything in
particular, and normalising them to 1 would be arithmetic nonsense dressed up
as a de-vig. Measured on the real GW1 cards, 58 resolved team cards across
three UK books:

* per **team** card: Σ q_i = **3.09** mean (min 0.62 for Coventry's 10-man
  card at Arsenal, max 5.65 for William Hill's 19-man Arsenal card);
* per **match** (both cards, one book): **6.19** mean, range 4.21–8.11.

So a whole-match anytime card sums to roughly six. Normalising it would divide
every player's probability by about six.

Worse, the UK books quote only the **Yes** side — every outcome comes back with
`name == "Yes"` and there is no matching `No`. With one leg of the two-way
market missing there is no overround to measure at all, so the margin has to be
*estimated* against an external constraint rather than removed. The constraint
is the totals market, via the additivity of Poisson rates (§4).

The rule, enforced in code: `devig()` is only ever called on a complete
mutually-exclusive set; `devig_anytime_scorer()` is the separate estimator for
one-sided prop cards and takes a team goal rate as its anchor.

---

## 2. Clean sheets, method A: sum a correct-score grid

Scores are mutually exclusive and a home clean sheet is exactly the event
"away score = 0", so given a *fair* grid the estimator is not a model at all,
it is a definition:

```
P(home CS) = Σ_x P(x−0)        P(away CS) = Σ_y P(0−y)
```

`clean_sheets_from_correct_score()` de-vigs the quoted cells and sums the
relevant row and column. No distributional assumption enters: whatever score
dependence the book prices in is carried through. That is this method's whole
advantage — and the reason it disagrees with §3.

Two real defects, both measured rather than assumed.

**Truncation.** Books quote a finite grid with no "any other score" bucket.
William Hill quotes 44–47 cells; the Betfair exchange quotes a 4×4 = 16-cell
grid. The de-vig renormalises over the quoted cells only, so the missing tail
mass is pushed back onto the quoted cells roughly in proportion. `quoted_sum`
records how much was removed, and its sign is the diagnostic: William Hill's
1.5250 is margin *minus* a small tail, while Betfair's **0.9556 — below one —**
means the missing tail outweighs the exchange's tiny margin, and renormalising
that card *inflates* every cell it kept.

The test is whether a de-vigged grid can reproduce the same book-consensus 1X2
and Over 2.5 that the featured markets quote. Over all ten GW1 fixtures:

| grid | de-vig | mean \|ΔP\| home win | mean \|ΔP\| over 2.5 | signed ΔP over 2.5 |
|---|---|---|---|---|
| William Hill, 44–47 cells | power | **0.019** | **0.027** | +0.027 |
| William Hill, 44–47 cells | multiplicative | 0.032 | 0.059 | +0.059 |
| Betfair exchange, 16 cells | power | 0.063 | 0.062 | **−0.062** |

The exchange's signed −6.2pp on Over 2.5 is the truncation signature: a 4×4
grid cannot express a total above 6, and renormalising relocates that mass into
the low-score corner. So the pipeline refuses truncated cards outright —
`CS_GRID_MIN_CELLS = 24` — rather than averaging a 16-cell grid in beside a
47-cell one. Before that guard, averaging the two books moved GW1 clean sheets
by up to 4.0pp on a single fixture (mean between-book spread 1.5pp).

**Longshot loading.** Correct-score margin concentrates in the tail; quotes of
101.0–151.0 are price floors, not opinions. `method="power"` (solve
`p = q^k`) shrinks exactly those cells hardest and is the default. The table
above is the justification: on the full grid, power halves the Over 2.5
reproduction error relative to multiplicative (2.7pp vs 5.9pp). The two methods
differ on the derived clean sheet itself by 0.44pp mean and 3.7pp max across
the 36 GW1 book-cards, so `multiplicative` is kept available only for
sensitivity analysis.

---

## 3. Clean sheets, method B: invert 1X2 (+ totals)

Most fixtures have no correct-score card. For those, take the de-vigged match
probabilities and solve for the goal rates that reproduce them under the
Dixon-Coles score matrix (independent Poisson when `rho = 0`):

```
P(i, j)  =  tau(i, j; λ, μ, ρ) · Poisson(i; λ) · Poisson(j; μ) / Z

P(home win) = Σ_{i>j} P(i,j)   P(draw) = Σ_{i=j} P(i,j)   P(away) = Σ_{i<j} P(i,j)
P(over 2.5) = Σ_{i+j≥3} P(i,j)
```

Two unknowns `(λ, μ)` against three constraints (four with totals), so the
system is overdetermined and `invert_match_odds()` solves it in the
least-squares sense over `log λ, log μ`. Clean sheets then come off the matrix,
never off λ directly:

```
P(home CS) = Σ_i P(i, 0) = column 0        P(away CS) = Σ_j P(0, j) = row 0
```

**Pass the totals leg whenever one exists.** The 1X2 triple pins the supremacy
`λ − μ` tightly but the overall level `λ + μ` only weakly, because a raised
level moves the home and away win probabilities in nearly compensating ways.
The backtest measures the cost of omitting it: pooled out-of-sample Brier
**0.16526** from 1X2 alone versus **0.16198** with the totals leg, and the mean
predicted clean sheet falls from 0.2849 to 0.2480 against a realised base rate
of 0.2204. Without totals the inversion is systematically over-predicting clean
sheets by 6.4pp; with it, by 2.8pp.

---

## 4. How far apart are A and B? (measured, GW1 2026-27)

All ten fixtures, both sides, William Hill grid vs the 1X2+totals inversion at
`rho = 0`:

| fixture | side | `cs_grid#power` | `poisson_indep` | grid − poisson |
|---|---|---|---|---|
| Arsenal v Coventry | Arsenal | 0.5521 | 0.6045 | **−0.0524** |
| Arsenal v Coventry | Coventry | 0.0818 | 0.0700 | +0.0118 |
| Hull v Man Utd | Man Utd | 0.4351 | 0.4744 | **−0.0393** |
| Hull v Man Utd | Hull | 0.1197 | 0.1082 | +0.0115 |
| Ipswich v Sunderland | Ipswich | 0.2552 | 0.2705 | −0.0153 |
| Ipswich v Sunderland | Sunderland | 0.2627 | 0.2764 | −0.0137 |
| Fulham v Chelsea | Fulham | 0.1797 | 0.1650 | +0.0147 |
| Fulham v Chelsea | Chelsea | 0.2766 | 0.2907 | −0.0141 |
| Man City v Bournemouth | Man City | 0.3279 | 0.3388 | −0.0110 |
| Man City v Bournemouth | Bournemouth | 0.1039 | 0.0919 | +0.0120 |
| *(remaining ten rows in the same range)* | | | | |

**Summary over all 20 team-fixtures: mean −0.0068, mean absolute 0.0138, max
absolute 0.0524, correlation 0.9987.**

The disagreement is not noise, it is systematic and signed: on the four heavy
favourites (predicted CS above 0.32) the grid sits **1.1 to 5.2pp below** the
inversion, and on their opponents it sits **1.2pp above**. The correct-score
card compresses extreme clean sheets toward the middle relative to a Poisson
fitted to the same match odds. Two mechanisms both point that way — residual
longshot loading that the power de-vig does not fully remove from the
`5-0`/`6-0` cells, and the finite grid's inability to express a 7-0.

Which is right? The honest answer from this data is that **it is not
established**. There is no correct-score history to backtest against (§7), and
the two methods differ by less than the between-book spread on the same
fixture (1.5pp mean) for six of the ten fixtures. The practical rule:

* Use `poisson_indep` as the default. It is available for every fixture, it is
  what the validation in §7 actually scored, and it consumes the two most
  liquid markets on the card (20+ books on 1X2 vs one usable correct-score
  book).
* Read `cs_grid#power` as a **disagreement flag**, not a replacement. A gap
  above ~3pp means the correct-score card is pricing a tail the Poisson is not,
  and a defender pick resting on that 3pp deserves a second look.
* Never average them. They are not two noisy readings of one quantity; they are
  two different estimators with different biases, and the mean would hide the
  signal that their gap carries.

---

## 5. The rho verdict: the low-score correction does not pay

This was the open question when the previous pass stopped. It is now closed,
and the answer is negative.

Dixon-Coles multiplies the four low-score cells by

```
tau(0,0) = 1 − λμρ    tau(0,1) = 1 + λρ    tau(1,0) = 1 + μρ    tau(1,1) = 1 − ρ
```

with `tau = 1` elsewhere. The first fit attempt grid-searched rho by minimum
clean-sheet Brier on 2022-23 over `[−0.16, +0.04]` and returned **+0.04 — its
own right-hand endpoint**, which is the classic symptom of a boundary
"optimum" on a flat surface rather than a fit. The grid was therefore widened
to `[−0.30, +0.30]` in 31 steps and scored on two objectives at once
(`cs_rho_grid.csv`, and the middle of it reproduced here):

| rho | clean-sheet Brier | scoreline log-likelihood |
|---|---|---|
| −0.30 | 0.186674 | −1148.121 |
| −0.12 | 0.186411 | −1134.918 |
| −0.02 | 0.186336 | −1132.293 |
| **0.00** | **0.186326** | **−1132.072** |
| **+0.04** | 0.186311 | **−1131.913** ← MLE |
| **+0.12** | **0.186295** ← min Brier | −1132.721 |
| +0.20 | 0.186299 | −1135.093 |
| +0.30 | 0.186329 | −1140.707 |

**Finding 1 — the Brier surface is flat.** Across the entire widened grid the
clean-sheet Brier spans **0.000379**, i.e. 0.20% of its own level (0.186326 at
rho = 0). The minimum at +0.12 is shared to six decimal places with +0.14 and
+0.16. Rho is simply **not identified by clean-sheet accuracy** once the totals
market has pinned the goal level: the correction reshuffles mass *within* the
low-score corner, and a clean sheet is a whole row or column, so most of the
reshuffling cancels inside the quantity being scored.

**Finding 2 — the likelihood does have an interior maximum, and it is not
significant.** The scoreline log-likelihood peaks at **rho = +0.04**, genuinely
interior (the grid extends 0.26 further in both directions and the surface
falls away on each side). The likelihood-ratio statistic against `rho = 0` is
**2 × (−1131.913 − (−1132.072)) = 0.317 on 1 df, p = 0.57**. Three-quarters of
a season of realised scorelines cannot distinguish this rho from zero.

**Finding 3 — the sign is wrong for Dixon-Coles anyway.** The published
Dixon-Coles rho for English league data is negative (roughly −0.03 to −0.13):
independent Poisson under-predicts 0-0 and 1-1. The fit here is *positive*.
That is not a contradiction of Dixon & Coles — they fit rho jointly with team
strengths from results, whereas here the lambdas are inverted from the closing
market at each candidate rho. The market's own prices already contain whatever
low-score structure exists, so by the time the inversion has matched 1X2 and
Over 2.5 there is nothing left for tau to explain, and what remains is noise
that happens to lean slightly positive.

**Finding 4 — out-of-sample it makes things very slightly worse.** Fitting on
2022-23 and scoring 2023-24 + 2024-25 (1520 team-fixtures, never seen by the
fit):

| method | pooled Brier | pooled scoreline loglik |
|---|---|---|
| `poisson_h2h_ou` (rho = 0) | **0.16198** | **−2271.256** |
| `dc_mle_rho=+0.04` | 0.16199 | −2272.071 |
| `dc_brier_rho=+0.12` | 0.16202 | −2275.667 |

The independent inversion wins on both objectives out of sample. The margin
(1e-5 in Brier) is itself noise — which is exactly the point. **The correction
buys nothing and costs a parameter, so the simpler independent-Poisson
inversion is preferred.** `PREFERRED_CS_METHOD = "poisson_indep"`.

**Finding 5 — the live market disagrees with the history, and it still does
not matter.** The BTTS market is a direct read on low-score dependence, so it
can be asked the same question. Holding each GW1 fixture's 1X2 + totals fixed
and solving for the rho that reproduces the Shin-de-vigged BTTS quote:

| | value |
|---|---|
| fixtures with an identified root | 8 of 10 |
| implied rho | median **−0.122**, mean −0.140, range −0.113 to −0.237 |
| BTTS gap at rho = 0 (quoted − model) | **+0.0187** mean, positive on 10/10, max +0.0331 |

The two lopsided fixtures (Arsenal–Coventry, Hull–Man Utd) have no root: with
one λ near 0.5 the gap function is non-monotone in rho and never crosses zero,
because tau has almost no leverage on BTTS when one side is barely expected to
score. So the market prices a *negative* rho of about −0.12 — the classic
Dixon-Coles sign — where three seasons of realised scorelines fit +0.04 and
reject −0.12 mildly (its 2×Δloglik against rho = 0 on the fit season is −5.69).

And even taking the market's own number at face value changes nothing that
matters: re-deriving all twenty GW1 clean sheets at `rho = −0.12` versus
`rho = 0` moves them by **0.43pp mean, 1.21pp max** — a quarter of the 1.38pp
mean gap between the correct-score grid and the inversion, and a twentieth of
that gap's 5.2pp maximum. The rho argument is a rounding error next to the
choice of derivation method.

`RHO_STAR = +0.04` is retained only so `dixon_coles` rows sit beside
`poisson_indep` in the table for anyone re-checking this comparison; the
measured difference between the two on GW1 is 0.12pp mean, 0.34pp max.

---

## 6. Team goal rates

### 6a. From a team-totals ladder (exact, where quoted)

Team totals *is* a complete two-way market per line, so each line can be
de-vigged exactly against its own under leg — no cross-selection
normalisation, unlike anytime scorer. Under `G ~ Poisson(λ)` and a half-goal
line `k + 0.5`:

```
P(over)  =  P(G ≥ k+1)  =  1 − CDF(k; λ)
```

One line inverts exactly by `brentq`; a ladder is overdetermined and
`team_lambda_from_totals()` returns the least-squares λ. Integer and quarter
lines are rejected: a push/void leg breaks the two-outcome identity, and none
of the observed books quote them here.

The ladder is worth having. A single 0.5 line barely separates λ = 1.6 from
λ = 2.0; the GW1 US cards quote a mean of **4.7 distinct lines per fixture-side**
(368 complete two-way lines over 10 fixtures, 6 books, per-line overround
1.0712), which pins the mean tightly.

**Availability, measured against the live catalogue on 2026-08-19:**
`team_totals` and `alternate_team_totals` are **not offered in the `uk`
region** at all. They are reachable on the free tier only through a per-event
call with `regions=us`, from US books (FanDuel, DraftKings, BetMGM, BetRivers,
Bovada, MyBookie).

### 6b. Cross-check against the 1X2 inversion

The two routes to a team goal rate are independent — different markets,
different books, different continents — so their agreement is a real test of
both. Over all 20 GW1 team-fixtures:

**team_totals − poisson_indep: mean +0.0118 goals, mean absolute 0.0195, max
absolute 0.0566, correlation 0.9992.**

Two decimal places of agreement between a UK 1X2 consensus and a US team-totals
ladder is about as good as this gets, and the signed +0.012 says the US books
were priced marginally hotter on the day. When the two disagree by more than
~0.1 goals, suspect a stale line rather than a signal.

---

## 7. Anytime scorer → per-player xG prior

If player *i* scores `Poisson(λ_i)` goals in the match then

```
P(anytime_i) = 1 − exp(−λ_i)      ⟺      λ_i = −ln(1 − p_i)
```

`scorer_rate()` is that inversion, and it is doing more work than it looks.
Probabilities are not additive across a team; **rates are**:
`Σ_i λ_i = λ_team`. That identity is the entire reason a one-sided card can be
de-vigged at all, because it supplies the external constraint that the missing
`No` leg would otherwise have provided.

`devig_anytime_scorer()` therefore solves for the transform making the card's
implied rates add up to the team's own de-vigged expected goals:

```
find k such that  Σ_i −ln(1 − q_i^k)  =  coverage · λ_team
```

`method="power"` (`p_i = q_i^k`) is the default over a uniform rate scaling
because books load margin onto longshots, so a uniform scale over-shrinks the
favourite. `coverage = 0.95` is the share of team goals attributable to listed
players — own goals and unlisted deep substitutes make up the rest. **It is a
stated assumption, not a measurement.**

Verified end to end on the GW1 rows: `Σ_i λ_i / λ_team` = **0.9503 mean, range
0.9457–0.9553** across all 20 team cards, i.e. the solver hits its 0.95 anchor
to within half a percent. The residual spread is the truncation of players the
book did not quote.

The derived rows are `anytime_prob` (the fair probability) and `xg_share`
(`λ_i / λ_team`, the quantity the projection ensemble actually wants, since it
is invariant to how the team's goal rate is later adjusted for lineup news).

**Treat these as the least reliable output in this document.** The shrink is
large — the raw match card implies about six goals against a market total of
2.97 mean across these ten fixtures — and it cannot be validated against realised results until enough matches
have been played. The raw quoted prices are always written to `fact_odds`
alongside, so nothing is lost if the estimate proves badly calibrated.

---

## 8. Validation

`scripts/backtest_clean_sheets.py`, offline, against football-data.co.uk
closing market-average prices plus full-time scores. Fit on 2022-23; 2023-24
and 2024-25 are genuinely out of sample. Full results in `cs_brier.csv`.

| method | season | n | Brier | base-rate Brier | mean pred | realised |
|---|---|---|---|---|---|---|
| `poisson_h2h` | 2023-25 pooled | 1520 | 0.16526 | 0.17182 | 0.2849 | 0.2204 |
| `poisson_h2h_ou` | 2023-25 pooled | 1520 | **0.16198** | 0.17182 | 0.2480 | 0.2204 |
| `dc_mle_rho=+0.04` | 2023-25 pooled | 1520 | 0.16199 | 0.17182 | 0.2486 | 0.2204 |
| `dc_brier_rho=+0.12` | 2023-25 pooled | 1520 | 0.16202 | 0.17182 | 0.2496 | 0.2204 |

Read the base-rate column first: predicting the constant 0.2204 for every team
scores 0.17182. The market-derived prior scores 0.16198 — a **5.7% Brier skill
score**. That is a real edge and a small one; a clean-sheet prior is a modest
input, not an oracle.

Calibration, pooled out-of-sample, `poisson_h2h_ou` (`cs_calibration.csv`):

| bin | n | mean predicted | realised |
|---|---|---|---|
| 0.0–0.1 | 175 | 0.0702 | 0.0629 |
| 0.1–0.2 | 408 | 0.1522 | 0.1373 |
| 0.2–0.3 | 453 | 0.2498 | 0.2163 |
| 0.3–0.4 | 304 | 0.3472 | 0.3289 |
| 0.4–0.5 | 139 | 0.4403 | 0.3813 |
| 0.5–0.6 | 36 | 0.5452 | 0.4167 |
| 0.6–0.7 | 5 | 0.6207 | 0.4000 |

**The prior is biased high in every single bin.** The bias is small below 0.4
(0.7–3.4pp) and large above it (5.9pp at 0.4–0.5, 12.9pp at 0.5–0.6), though
the top two bins hold only 41 of 1520 observations. Two candidate causes, not
separated by this data: the 0.95-of-a-clean-sheet quotes at the top of the card
are exactly where correct-score truncation and longshot loading bite hardest,
and the 2023-25 seasons realised fewer clean sheets (0.2066, 0.2342) than
2022-23 (0.2724), so part of the gap is a genuine regime shift the closing odds
lagged. **A downstream consumer should shade a market-derived clean sheet above
0.40 downward**; the shrinkage is not applied here, because this module's
contract is to report what the market implies, not to correct it.

**What is not validated.** The correct-score derivation cannot be backtested:
football-data.co.uk carries no correct-score history and The Odds API's
historical snapshots are paid-only. Its live disagreement with the inversion is
quantified in §4 and that is all the evidence there is. Likewise `team_totals`
(§6b, agreement only, no realised scoring) and `anytime_prob` / `xg_share`
(§7, internal consistency only).

---

## 9. Storage

Derived numbers go to `fact_odds_derived`, created by
`ensure_derived_schema()` — an additive, idempotent migration owned by
`fpl_edge/ingest/odds_derived.py`. It is deliberately **not** in
`fpl_edge/store/schema.sql`: that file describes quoted facts, and these are
modelling choices about those facts.

```
PRIMARY KEY (fixture_key, entity_type, entity_code, market, method, as_of)
```

`method` is part of the key on purpose. Competing derivations coexist rather
than overwrite, so the ensemble can be re-pointed without re-ingesting and a
backtest can score them against each other. The table registers itself in
`PIT_KEYS` at import, which is what gives it the same point-in-time discipline
(idempotent append, contradiction refusal, `snapshot_at`) as every other fact
table.

| market | entity | methods |
|---|---|---|
| `clean_sheet_prob` | team | `cs_grid#power`, `poisson_indep`, `dixon_coles` |
| `team_lambda` | team | `poisson_indep`, `dixon_coles`, `team_totals` |
| `anytime_prob` | player | `scorer_power` |
| `xg_share` | player | `scorer_power` |

---

## 10. Ingestion: what the free tier actually exposes, and what it cost

Checked against `GET /v4/sports/soccer_epl/events/{id}/markets` on 2026-08-19,
with costs read off the vendor's own `x-requests-last` header rather than
assumed:

| market | region | available on the free tier | books seen |
|---|---|---|---|
| `h2h`, `totals` | uk | yes (featured endpoint) | 20–21 |
| `correct_score` | uk | **yes** | William Hill (44–47 cells), Betfair exchange (16) |
| `btts` | uk | **yes** | 10–11 |
| `team_totals`, `alternate_team_totals` | uk | **no** | — |
| `team_totals`, `alternate_team_totals` | us | **yes** | FanDuel, DraftKings, BetMGM, BetRivers, Bovada, MyBookie |

Credit costs, all confirmed live:

* the markets-catalogue call: **1 credit**, the same whether one or two regions
  were requested;
* per-event odds: **markets × regions**. `correct_score,btts` in one region = 2.
  `team_totals,alternate_team_totals` in one region = 2.

**Real spend, 2026-08-20 full GW1 run** (`--fetch`, 10 events):

```
planned 42 = 2 (uk h2h+totals refresh) + 10x2 (correct_score,btts @ uk) + 10x2 (team_totals @ us)
spent   42   (x-requests-last summed over the run)
key headers after the run: x-requests-remaining 433, x-requests-used 67
expansion ledger: 42 / 150 for the month
rows written: 2279  (correct_score 591, team_totals 747, h2h 627, btts 216, totals 98)
derived:      860 rows over 10/10 fixtures
              (scorer_power 740, poisson_indep 40, dixon_coles 40,
               cs_grid#power 20, team_totals 20)
```

The expansion enforces its own hard cap (`EXPANSION_MONTHLY_CAP = 150`, inside
the key's 500/month) *before* spending anything, and accounts every run in
`data/odds_expansion_ledger.json`. The ledger exists because the vendor's
`x-requests-used` header counts every consumer of the key, so "how much has
this expansion spent" is a question only we can answer. A weekly GW refresh at
42 credits fits three times over inside the cap; dropping the US team-totals
leg (`--no-team-totals`) halves it to 22.

---

## 11. Reproducing

```bash
uv run python scripts/backtest_clean_sheets.py            # offline, no credits
uv run python scripts/ingest_odds_extras.py --fetch --dry-run   # prices the run, spends 0
uv run python scripts/ingest_odds_extras.py --fetch --derive    # 42 credits for a full GW
uv run python scripts/ingest_odds_extras.py --derive            # free, recompute from fact_odds
uv run pytest tests/unit/test_odds_derived.py tests/unit/test_odds_markets_budget.py \
              tests/unit/test_odds_rho_verdict.py
```

The tests run entirely offline from the real GW1 payloads committed under
`tests/fixtures/odds/`, and `test_odds_rho_verdict.py` re-derives the §5
verdict from the committed `cs_rho_grid.csv` so the claims in this document
fail loudly if the numbers behind them ever change.
