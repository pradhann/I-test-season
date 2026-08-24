# Data sources: what we can reach, what it costs, and what to pay for

**All measurements taken 2026-08-18**, from a UK-routed connection, using the
project's honest User-Agent:

```
fpl-edge/0.1 (personal research; contact via repo owner)
```

Every status code below is one this repo actually received. Where a claim comes
from a vendor's documentation rather than from a response we saw, it is marked
**(documented)**. Where we could not establish something, it says so.

Context: today is 2026-08-18, the 2026-27 GW1 deadline is
2026-08-21T17:30:00Z, and the account holder currently has **no paid data
subscriptions**.

---

## 1. The headline findings

1. **No free source publishes anytime-goalscorer odds except The Odds API.**
   It is the only source found that carries `player_goal_scorer_anytime` for
   the EPL at all — and its free tier does cover it, now confirmed live.
   Pinnacle, Betfair and football-data.co.uk do not offer the market in any
   tier.
2. **The Odds API's *free* tier is sufficient for live anytime-scorer
   ingestion — now confirmed by running it.** A complete gameweek (10 fixtures,
   h2h + totals + anytime scorer) costs **12 credits** against a 500/month
   allowance: `/events` is free, `/odds` is 2, and each event's scorer card is
   1. That is ~48 credits a month, under 10% of the free allowance. The paid
   tiers buy *history*, not live coverage. This inverts the obvious assumption
   and is the single most consequential finding here.
3. **Clean-sheet probabilities do not need to be bought.** They can be derived
   from free 1X2 + Over/Under 2.5 odds. Measured against 2,280 realised
   Premier League team-matches, the derivation scores a Brier of 0.1671 against
   a 0.1782 base rate, with a consistent +2.1pp optimistic bias. That is a
   usable signal, for free, back to 2005-06.
4. **Understat has no 2026-27 data and will not until matches are played** —
   confirmed two independent ways. Nothing can change that.
5. **The FPL API is the injury feed.** Every third-party alternative is
   bot-blocked, down, paywalled, or forbidden by terms.

---

## 2. Odds sources

### 2.1 Evidence table

| Source | Cost | Anytime scorer? | Other markets | Historical depth | Rate limit | Reliability | ToS / robots | Verdict |
|---|---|---|---|---|---|---|---|---|
| **football-data.co.uk** | Free | **No** | 1X2, O/U 2.5, Asian handicap, from ~20 books incl. Pinnacle + Betfair Exchange | **1993-94 → 2025-26**; O/U 2.5 from 2005-06 | None observed (9 sequential fetches, no 429, no throttling) | HTTP 200 every request; 203KB for a full season in ~1s | `robots.txt` = `User-agent: * / Disallow:` (**allow all**) | ✅ **Implemented and wired in** |
| **The Odds API** | Free 500 credits/mo; $30/mo 20K; $59/mo 100K; $119/mo 5M; $249/mo 15M | **Yes** — `player_goal_scorer_anytime`, 3 UK books | h2h, totals, spreads, btts, draw_no_bet, team_totals, alternate lines | Featured markets from 2020-06-06; player props from 2023-05-03 — **paid plans only** (documented) | Measured: `/events` **0 credits**, `/odds` **2**, `/events/{id}/odds` **1** | HTTP 200 on the free tier; 401 without a key | Commercial API, key required | ✅ **Live on the free tier and wired in** |
| **Pinnacle (public API)** | Free | **No** — only "Tournament Top Goalscorer" | moneyline, spreads, totals, **team totals incl. 0.5 line** (= direct clean sheet), correct score, BTTS | Live only | Not measured | `guest.api.arcadia.pinnacle.com` → **HTTP 200**, live prices, 101 markets for one fixture; 519 EPL matchups | `api.pinnacle.com` → **HTTP 451** "URL unavailable for legal reasons". Guest API is undocumented/internal; its `robots.txt` → 404; ToS page → **HTTP 502**, could not be read | ❌ **Not used — permission unverifiable** |
| **Betfair Exchange API** | Delayed app key free; live app key £299 one-off **(documented, not verified)** | Not verified | Not verified | n/a | n/a | `api.betfair.com/exchange/betting/rest/v1.0/` → **Cloudflare 403** unauthenticated | Requires funded account + certificate login | ❌ **Not reachable without an account** |
| **OddsPortal** | Free | Not reachable | — | Site claims deep history | — | — | `robots.txt` **disallows** `*/ajax-*` (where odds load from) **and every historical year page** (`*-2024*` … `*-1998*`) | ❌ **Scraping explicitly forbidden** |
| **oddschecker** | Free | Would be here | — | — | — | — | `robots.txt` **disallows** `*/view-all-markets/*` — precisely the scorer pages | ❌ **Scraping explicitly forbidden** |

### 2.2 Overround by bookmaker — measured, not assumed

Mean 1X2 overround across 380 Premier League fixtures, 2025-26 closing lines,
computed from the ingested data:

| Bookmaker | Fixtures | Mean overround | Median |
|---|---|---|---|
| `fair#shin` (our de-vig output) | 380 | **1.5 × 10⁻¹⁶** | 0.000% |
| Betfair Exchange | 358 | 0.56% | 0.55% |
| Best price across all books | 380 | 1.88% | 1.96% |
| Pinnacle | 210 | 2.95% | 2.87% |
| Betfair Sportsbook | 372 | 4.50% | 4.44% |
| BetMGM | 380 | 4.97% | 4.39% |
| BetVictor | 372 | 5.55% | 5.52% |
| Bet365 | 380 | 5.59% | 5.56% |
| Market average | 380 | 5.67% | 5.59% |
| bwin | 380 | 5.86% | 5.85% |
| Ladbrokes | 281 | 6.51% | 6.48% |

The first row is the de-vig contract holding to floating-point precision on
every fixture. The rest is why the Betfair Exchange and Pinnacle columns are
worth more than the high-street books for calibration — and both are in the
free football-data feed.

Note the coverage gaps: Pinnacle appears for only 210 of 380 fixtures and
Ladbrokes 281. Bet365, BetMGM and the market aggregates are the only columns
present for all 380.

### 2.3 What football-data.co.uk actually contains

Verified by fetching and parsing the real files:

| Season file | HTTP | Rows | Columns | O/U 2.5? |
|---|---|---|---|---|
| `mmz4281/9394/E0.csv` | 200 | 553 | 28 | No |
| `mmz4281/0001/E0.csv` | 200 | 381 | 45 | No |
| `mmz4281/0506/E0.csv` | 200 | 381 | 68 | Yes |
| `mmz4281/1516/E0.csv` | 200 | 381 | 65 | Yes |
| `mmz4281/2021/E0.csv` | 200 | 381 | 106 | Yes |
| `mmz4281/2425/E0.csv` | 200 | 381 | 120 | Yes |
| `mmz4281/2526/E0.csv` | 200 | 381 | 132 | Yes |
| `mmz4281/2627/E0.csv` | **300** | — | — | **Not yet published** |
| `fixtures.csv` | 200 | 3 (1,484 bytes) | 74 | Yes — **0 of them `E0`** |

Two operational consequences:

* **2026-27 has no completed-season file yet**, so all live odds must come from
  `fixtures.csv`.
* `fixtures.csv` carried `E2` and `SP1` rows for 19–20 August but **no `E0`
  rows** on 18 August. football-data publishes a fixture roughly one to two days
  ahead. For a 2026-08-21T17:30Z deadline, Premier League rows should appear
  around the 20th — before the deadline, but with under 48 hours of margin.
  `scripts/ingest_odds.py --fixtures` reports zero rows as a normal state rather
  than an error, and should be re-run daily from the 19th.

### 2.4 The Odds API — credit arithmetic

This is the calculation that drives the recommendation. Costs are from the v4
documentation, confirmed by reading the endpoint reference:

* `/v4/sports/{sport}/events` — **0 credits**.
* `/v4/sports/{sport}/odds` — `markets × regions` credits, returns all events.
* `/v4/sports/{sport}/events/{id}/odds` — `markets × regions` credits, **one
  event**. Player props are only available here.
* Historical endpoints — `10 × regions × markets`, **paid plans only**.
* Responses with empty data do not count against the quota.

A realistic live gameweek:

| Call | Frequency | Credits |
|---|---|---|
| `h2h,totals` for all EPL events, 1 region | 3× per gameweek | 3 × 2 = 6 |
| `player_goal_scorer_anytime`, 1 event, 1 region | 10 fixtures × 1 poll | 10 |
| **Per gameweek** | | **16** |
| **Per month (~4 gameweeks)** | | **~64** |

Against a **500 credit/month free allowance**. Live anytime-scorer ingestion
fits inside the free tier roughly eight times over, with room to poll scorer
markets three times per gameweek if wanted.

One caveat the docs are explicit about: soccer player props are available for
the EPL but *"coverage is currently limited to US bookmakers"*. US books price
soccer scorer markets with wide margins, so the de-vig matters more here than
anywhere else — see `devig_independent` in `fpl_edge/ingest/odds.py`, which
scales scorer legs to an expected-distinct-scorers total rather than
normalising them to 1.0.

---

## 3. Advanced stats

| Source | Cost | Coverage | Latency | History | Reliability | ToS / robots | Verdict |
|---|---|---|---|---|---|---|---|
| **FPL API** (already ingested) | Free | Opta `expected_goals`, `expected_assists`, `expected_goals_conceded` per player per fixture | Same-day | Current season | HTTP 200 | Public game API | ✅ **The baseline. Covers the core rates.** |
| **Understat** | Free | Team + shot-level xG | Post-match | 2014-15 → **2025-26 only** | HTTP 200 | `robots.txt` = **`User-agent: * / Disallow: /`** — all crawling forbidden | ❌ **No 2026-27 data; crawling forbidden** |
| **FBref / StatsBomb** | Free to view | npxG, xAG, per-90 detail | Post-match | Deep | **HTTP 403** to an honest client — including for `/robots.txt` itself | Crawl policy unreadable behind a Cloudflare interactive challenge | ❌ **Not reachable without impersonating a browser** |
| **StatsBomb open data** | Free (CC BY-NC-SA 4.0) | Event-level | n/a | Selected competitions | Not measured | Clear open licence | ➖ **Clean licence, but no Premier League coverage** |

### 3.1 Understat — three measurements

1. `GET /robots.txt` → **HTTP 200**, body is exactly:
   ```
   User-agent: *
   Disallow: /
   ```
   A blanket prohibition. No crawl-delay, no carve-out.

2. `GET /league/EPL/2026` → **HTTP 200**, but the page returned is titled *"EPL
   xG Table and Scorers for the 2025/2026 season"* and its season `<select>`
   offers 2014–2025 only. **Requesting 2026 or 2027 silently serves the 2025-26
   page.** A caller trusting the 200 would have ingested last season's numbers
   as if they were this season's. `soccerdata.Understat(seasons="2627")
   .read_schedule()` independently returned a DataFrame of shape `(3, 0)` — no
   columns, no data.

3. `GET /match/28778` → **HTTP 200**, 30,639 bytes, but the page now embeds
   **only** `var match_info = JSON.parse(...)` — 26 match-level fields. The
   `shotsData` variable that used to carry shot-by-shot xG **is gone**
   (`grep -c shotsData` = 0). Shot-level xG is no longer in the match HTML at
   all.

So Understat cannot inform the GW1 decision under any licence or technique, and
"shot-level xG from the match page" describes the site as it was, not as it is.

### 3.2 On `soccerdata`

`soccerdata==1.9.1` works. It is worth being precise about *why*, because the
reason is the decision:

it ships **`tls_requests`**, which loads a platform-native shared library
(`tls-client-darwin-arm64-1.13.1.dylib`) to impersonate a real browser's TLS
fingerprint. That is exactly the mechanism Cloudflare's challenge exists to
detect. Using it is circumventing bot detection, not being permitted by it.

Measured in an isolated environment:

| Call | Result |
|---|---|
| `Understat(...).read_schedule()` 2025-26 | 380 rows × 17 cols in **0.8s** |
| `Understat(...).read_shot_events()` 2025-26 | 9,524 rows × 16 cols in **250.7s** |
| `Understat(...).read_schedule()` **2026-27** | **shape (3, 0) — empty** |
| `FBref(...).read_schedule()` 2025-26 | 380 rows × 13 cols in **269.4s** |

It is not installed and is not a dependency of this repo. `fpl_edge/ingest/fbref.py` (module deleted 2026-08-24; the negative result lives here and in git history)
records the refusal and the reasoning rather than silently omitting the source.
If the account holder wants FBref, that is their call to make knowingly — but it
should not arrive as a transitive dependency.

---

## 4. Injuries and press-conference team news

| Source | Status | Verdict |
|---|---|---|
| **FPL API** (`status`, `chance_of_playing_next_round`, `news`, `news_added`) | HTTP 200, already ingested | ✅ **Use this** |
| premierinjuries.com | **HTTP 403** on `/robots.txt` *and* `/injury-table.php` — Cloudflare interactive challenge | ❌ Crawl policy unreadable |
| physioroom.com | `robots.txt` **200** and permissive, but `/injury-table/premier-league` → **HTTP 522** (Cloudflare origin timeout) | ❌ Site was down |
| fantasyfootballscout.co.uk | `robots.txt` **200**, `Disallow:` (allow all); `/team-news` serves 542KB but markup carries a `premium members` gate | ❌ Substance is subscriber content |
| bbc.co.uk | `robots.txt` **200**, and states: no *"scraping, crawling, or systematic extraction"*, no datasets, no TDM, no RAG or agentic use | ❌ Unambiguously forbidden |

Of five candidate feeds: one bot-blocked, one down, one paywalled in substance,
one forbidden in terms. None is both reachable and clearly licensed.

**The FPL API is not a consolation prize here.** It has three properties no
aggregator can match:

* It is the **scoring authority** — `chance_of_playing_next_round` is what the
  game itself uses.
* It is **timestamped**. `news_added` gives the instant an availability change
  became public, which is exactly the `as_of` that `fact_player_state` needs. A
  scraped injury table carries no such stamp, so a backtest built on one
  silently applies today's injury list to a deadline three weeks ago. That is a
  leak, and it is invisible.
* It is **already ingested** on every poll.

The genuine gap is **press-conference lead time**: managers give team news
around Friday lunchtime, and FPL's flags often follow by some hours. For a
Saturday-morning deadline that gap is real but small, and it is the only thing a
paid feed would buy. `fpl_edge/ingest/injuries.py` surfaces it explicitly via
`news_is_fresh`, flagging players whose availability changed within 24 hours of
the deadline — the exact set where a human glance at a press conference adds
something the API has not caught up with.

---

## 5. What is implemented

`fpl_edge/ingest/odds.py`, landing in `fact_odds` via `scripts/ingest_odds.py`.

Verified end-to-end against the live network:

```
2024-25:  24,810 rows in 2.1s     (HTTP 200)
2025-26:  32,134 rows in 0.9s     (HTTP 200)
fixtures.csv: 0 E0 fixtures available  (HTTP 200 — expected this far out)

fact_odds:  h2h 39,486 | totals 15,938 | clean_sheet 1,520  across 760 fixtures
```

Three kinds of row, all as decimal prices so they share one column, and all
distinguishable by `bookmaker`:

* **Quoted** — `bet365`, `pinnacle`, `betfair_exchange`, … (closing) and
  `bet365#open`, … (opening). The `#open` suffix is load-bearing: both are
  stamped at kickoff, so without it the closing line would collide with the
  opening line on the primary key and silently fail to insert.
* **De-vigged consensus** — `fair#shin`, priced at `1/p` so a fair probability
  round-trips through the schema's single price column.
* **Derived** — `derived#poisson` for `clean_sheet`, named so nobody can mistake
  a modelled number for a price a book actually hung.

### 5.1 Point-in-time discipline

The subtle part, and the easiest place in this repo to leak.

`mmz4281/<season>/E0.csv` is only *published* after its matches are played, but
the odds inside it were publicly quoted *before* kickoff. Rows are therefore
stamped at **each fixture's kickoff instant** — the last moment the closing line
was observable. This deliberately makes closing odds invisible at an FPL
deadline 90+ minutes earlier, which is correct: you could not have bet the close
when you picked your team.

`fixtures.csv` is forward-looking, so its rows are stamped at the **fetch
instant**. It is the only path whose rows may legitimately inform an upcoming
deadline.

Asserted end-to-end in `tests/unit/test_odds_football_data.py`: with two seasons
loaded, a snapshot at 2025-01-01 sees 188 of 760 fixtures.

### 5.2 De-vigging

Three methods, because they disagree materially on longshots and scorer markets
live in the tail. Verified on real 1X2 books:

Liverpool v Bournemouth closing line, 1.31 / 6.13 / 9.34 (3.36% overround):

| Method | Favourite (1.31) | Draw (6.13) | Longshot (9.34) |
|---|---|---|---|
| Multiplicative | 0.7386 | 0.1578 | 0.1036 |
| Shin (default) | 0.7487 | 0.1534 | 0.0979 |
| Power | 0.7538 | 0.1498 | 0.0964 |

The spread between methods on the longshot is 0.72 percentage points — 7% of
its own value — against 1.5pp on the favourite. That is why the method is named
in the `bookmaker` column rather than left implicit.

Proportional de-vigging under-states favourites because books load margin onto
longshots; Shin and the power method both correct in that direction. The
direction is asserted as a test, since a sign error there would bias every
derived clean sheet.

`devig_independent` is kept separate and used for scorer markets, which are
**not** mutually exclusive — normalising them to 1.0 would halve every striker.

### 5.3 Derived clean sheets — measured calibration

Fitted by inverting an independent-Poisson forward model against de-vigged 1X2
and Over/Under 2.5 probabilities. Validated against every realised Premier
League team-match in 2023-24, 2024-25 and 2025-26 (**n = 2,280**):

| | |
|---|---|
| Mean predicted | 0.2532 |
| Mean realised | 0.2320 |
| **Bias** | **+2.1pp (optimistic)** |
| Brier score | **0.1671** |
| Brier, base rate only | 0.1782 |

By bucket:

| Predicted | n | Predicted | Actual | Diff |
|---|---|---|---|---|
| 0.00–0.15 | 491 | 0.1036 | 0.0733 | +3.0pp |
| 0.15–0.20 | 324 | 0.1745 | 0.1852 | −1.1pp |
| 0.20–0.25 | 364 | 0.2253 | 0.2170 | +0.8pp |
| 0.25–0.30 | 350 | 0.2762 | 0.2543 | +2.2pp |
| 0.30–0.35 | 268 | 0.3246 | 0.2724 | +5.2pp |
| 0.35–0.45 | 355 | 0.3923 | 0.3746 | +1.8pp |
| 0.45–1.00 | 128 | 0.5077 | 0.4609 | +4.7pp |

**Read this honestly.** The derivation carries real skill — a 6% Brier
improvement over the base rate, and the ordering is broadly monotone. But it is
consistently optimistic, worst in the tails. The downstream model should shrink
it rather than treat it as unbiased. This is free, and it goes back to 2005-06.

---

## 5A. The Odds API live path (anytime scorer)

The free key is active. Everything below is measured against it.

### 5A.1 Credit costs — confirmed against `x-requests-last`

| Call | Documented | **Observed** |
|---|---|---|
| `/v4/sports/{sport}/events` | 0 | **0** |
| `/v4/sports/{sport}/odds?markets=h2h,totals&regions=uk` | markets × regions | **2** |
| `/v4/sports/{sport}/events/{id}/odds?markets=player_goal_scorer_anytime&regions=uk` | markets × regions | **1** |

A full gameweek is therefore `0 + 2 + (10 × 1)` = **12 credits**. At four
gameweeks a month that is ~48 of 500.

Because `/events` costs nothing *and* returns the quota headers, the remaining
balance is readable for free. Every run prices itself against the vendor's own
counter before spending anything, and `CreditPlan.check()` aborts the whole run
rather than stopping halfway. The cap defaults to **400**, deliberately below
the 500 limit so a failed run can be retried inside the same month.

### 5A.2 Two things that silently corrupt this data

**The `"Yes"` trap.** For player props the player's name is in
`outcome["description"]`; `outcome["name"]` is the literal string `"Yes"` for
every outcome — verified across all three UK books. Keying the selection on
`name` produces 17 identical rows called "Yes" per book, which collide on
`fact_odds`'s primary key: 16 vanish silently and the survivor is attributed to
nobody. The selection is therefore taken from `description`, and a test asserts
17 distinct selections per bookmaker.

**Anytime scorer is not a mutually exclusive book.** Eleven players can score in
the same match, so the implied probabilities do not sum to 1 — the real Arsenal
card summed to **4.748**. Normalising it to 1, as one would a 1X2 market, would
divide every striker's probability by nearly five. The 1X2 de-vig functions are
not reachable from this path; `devig_anytime_scorer` is separate and a test
asserts its output does *not* sum to 1.

### 5A.3 De-vigging a one-sided card — and its honest limits

The UK books quote **only the Yes side**. With one side of a two-way market
missing there is no overround to measure, so the margin cannot be removed
exactly — it has to be *estimated* against an external constraint.

The constraint used is the totals market. If player *i* scores
`Poisson(λ_i)` goals then `p_i = 1 - exp(-λ_i)` and, critically,
`Σλ_i = E[team goals]` — **goal rates are additive where probabilities are
not**. So we solve for the transform making the card's implied rates sum to the
team's de-vigged expected goals.

One structural detail that matters and was only visible in the data: **the
cards are per-club**. All 17 selections for Arsenal v Coventry were Arsenal
players. Anchoring such a card to the *match* total (3.156) instead of the
*team* total (2.649) would inflate every rate by the opponent's share, so the
anchor is per club.

Against the real Arsenal card, anchored to 2.998 expected goals:

| Selection | Quoted | Uniform | **Power (default)** |
|---|---|---|---|
| Gyökeres (1.78) | 0.5629 | 0.3431 | **0.4121** |
| Havertz (2.13) | 0.4688 | 0.2748 | **0.3108** |
| Ødegaard (3.80) | 0.2632 | 0.1437 | **0.1276** |
| Mosquera (12.00) | 0.0833 | 0.0432 | **0.0216** |

Power is the default because books load margin onto longshots far more heavily
than onto favourites, so a uniform scale over-shrinks the favourite.

**This estimate should be treated as uncertain.** The quoted card implies
`Σλ = 5.90` against a market expectation of 2.65 — the raw prices overstate
team goals by roughly 2.2×, and the resulting shrink is large. It cannot be
validated against realised results until matches are played. **The raw quoted
prices are always written to `fact_odds` alongside the estimate**, so if the
estimate proves badly calibrated nothing is lost and it can be recomputed.

### 5A.4 Player-name resolution

Bookmaker names are resolved to FPL `code` — the cross-season stable key — using
`player_mapping.normalize_name`, with candidates narrowed to the two clubs in the
fixture. That club constraint does most of the work: it removes any chance of a
common surname matching the wrong club.

Rules are tried in order, and **each must be unique among the candidates**. An
ambiguous name is reported unmatched rather than resolved by picking one — the
same principle `player_mapping` applies to the two Ben Davieses.

| Rule | Real case from GW1 |
|---|---|
| `exact_full` | "Martin Zubimendi Ibanez" = "Martín" + "Zubimendi Ibáñez" |
| `exact_web` | bookmaker used FPL's short name |
| `api_subset` | "Gabriel Martinelli" ⊂ "Gabriel Martinelli Silva" |
| `fpl_subset` | "Kepa Arrizabalaga Revuelta" ⊃ FPL "Kepa Arrizabalaga" |
| `surname_initial` | "Ben White" vs FPL "Benjamin White" |

Two bugs found and fixed by running this against the live card:

* **`Ø` does not NFKD-decompose.** It is a stroked letter, not a base letter
  plus a combining mark, so the shared normaliser strips it as punctuation and
  "Ødegaard" became "degaard" — the bookmaker's "Odegaard" then never matched.
  `fold_name` folds `ø æ œ ß đ ð ł þ` before normalising. This was a real
  1-in-17 failure.
* **The surname rule was too loose.** Keyed on any shared token, "Martin
  Odegaard" collided with "Martin Zubimendi Ibanez" on the token "martin" and
  came back ambiguous. It is now anchored on the *last* FPL token.

Club names also differ between the two feeds (7 of 20), and are mapped through
an explicit table rather than fuzzy-matched — a wrong club mapping would
silently attribute an entire squad to the wrong team, so an unknown club raises.

---

## 6. RECOMMENDATION

### Pay for nothing yet. Take the free Odds API key.

**Expected edge from paying today: approximately zero.** Here is the reasoning,
not just the conclusion.

#### What you would be buying, and what it is worth

| Candidate | Price | What it adds | Honest assessment |
|---|---|---|---|
| The Odds API **free** | $0 | Live `player_goal_scorer_anytime` for the EPL, **12 credits per gameweek** measured, ~48/month against a 500 allowance | **Taken — now live.** The only route to the market that matters, and it costs nothing. |
| The Odds API **$30/mo** | $30/mo | Historical snapshots: featured markets from 2020-06-06, player props from 2023-05-03 | **Not yet.** See the test below. |
| The Odds API **$59+/mo** | $59–249/mo | More credits | **No.** You are nowhere near the 20K tier's limits, let alone 100K. |
| Betfair live app key | £299 one-off | Exchange prices in real time | **No.** football-data already carries Betfair Exchange closing odds free, at a measured 0.56% overround. The £299 buys latency you cannot act on — FPL decisions are made once a week, not in-play. |
| FantasyFootballScout | ~£25/season | Press-conference team news, predicted lineups | **Genuinely the most defensible paid option**, and still not yet — see below. |

#### Why the historical odds tier is not worth $30 *yet*

The case for buying history is calibration: you cannot score your scorer model
out-of-sample without historical scorer lines. Three reasons to wait:

1. **You already have free historical calibration for the larger half of the
   problem.** Clean sheets and team goal rates drive every GKP and DEF, plus the
   team-level component of attacking returns. Section 5.3 shows the free
   derivation has measurable skill over 2,280 observations back to 2005-06.
2. **Player-props history only reaches 2023-05-03** — about three seasons. That
   is a thin sample for calibrating a per-player, per-fixture rate, and squad
   turnover means much of it describes players who have moved.
3. **You are already generating the history for free.** The free tier covers
   live scorer ingestion from GW1, at a measured 12 credits per gameweek. Poll
   it every gameweek and by Christmas you own ~17 gameweeks of scorer lines you
   archived yourself, with `raw_fetch` provenance the vendor's own history
   cannot give you — for 48 credits a month out of 500.

#### The decision rule

Spend nothing now. Instead run this test, which the free tier fully supports:

1. Get the free Odds API key. Ingest `player_goal_scorer_anytime` every
   gameweek from GW1.
2. In parallel, have the points model emit its own anytime-scorer probability
   for every player.
3. After **10 gameweeks**, compare Brier scores on the same set of
   player-fixtures.

The comparison has a second job: it is also the only way to find out whether
`devig_anytime_scorer` is well calibrated. Its shrink is large and currently
unvalidated (§5A.3), so score the *raw* quoted prices and the de-vigged
estimates separately. If the raw card scores better, the de-vig is
over-correcting and the anchor or the `coverage` assumption needs revisiting.

Then:

* **If the market beats the model** — the market has information the model
  lacks, and $30 for the 2023-2026 history to calibrate against is well spent.
  One or two months at that tier is enough to pull the backfill; it does not
  need to be a standing subscription.
* **If the model beats the market** — which is plausible, because US-book scorer
  markets carry wide margins and the model sees FPL-specific information like
  rotation risk and set-piece duty that a bookmaker does not price — then paying
  for history buys nothing, and the free live feed is all you ever needed.

Either way you will know, from your own data, in ten weeks. That is a far better
basis than buying a subscription on the assumption it must help.

#### The one thing genuinely worth paying for, later

**Press-conference lead time.** It is the only gap free sources cannot close:
the FPL API's availability flags trail Friday press conferences by hours, and
for a Saturday-morning deadline that is a real information deficit. Sizing it
requires knowing how often it actually costs you points, which
`injuries.py`'s `news_is_fresh` flag is built to measure — count how many
gameweeks a flag flipped between your decision and the deadline, and what it cost.

If that count is material after half a season, ~£25 for a FantasyFootballScout
subscription is the best-value purchase available. Note that it must be *used*
as a subscriber, read by a human. Their `robots.txt` permits crawling but the
content is subscriber material, so scraping it is not something this engine will
do.

### Summary

| Question | Answer |
|---|---|
| What should you pay for **now**? | **Nothing.** |
| What should you sign up for now? | Done — The Odds API **free** tier. Measured at 12 credits/gameweek, ~10× inside the 500/month allowance. |
| What is the free stack? | football-data.co.uk (history + derived clean sheets) + The Odds API free (live scorer) + FPL API (injuries, xG, prices). |
| When would you pay? | $30 one or two months for scorer history — **only if** the market out-scores your model over 10 gameweeks. ~£25/season for team news — **only if** late flag flips prove materially costly. |
| What can no amount of money buy? | Understat 2026-27 data before matches are played. |

---

## 7. Being a good citizen

* Every fetch goes through `Fetcher`/`TextFetcher`, which archives the raw body
  under `data/raw/<source>/` with its sha256 and records it in `raw_fetch`. The
  warehouse can be rebuilt offline, so re-running a backtest costs a source zero
  requests.
* One honest User-Agent identifying the project. No browser impersonation, no
  TLS fingerprint forgery, no cookie replay.
* `understat.py` (deleted 2026-08-24 — understat serves `Disallow: /` and has no 2026-27 season; its tested parsers live in git history should that change) read the live `robots.txt` and **failed closed** — an
  unreachable policy means "no", never "assume yes". The override is an explicit
  argument a human sets; it does not change how we identify ourselves and it
  does not skip the delay.
* Sources that forbid scraping are **not scraped**: OddsPortal, oddschecker,
  BBC, Understat by default.
* Sources behind bot detection are **not circumvented**: FBref, premierinjuries.
  `soccerdata` is deliberately not a dependency.
* Paywalled content is **not extracted**: FantasyFootballScout is a
  subscribe-and-read-it-yourself recommendation, not a scraping target.
* Pinnacle's guest API returns live prices and would be the best free
  clean-sheet source available — team totals include the 0.5 line, which *is* a
  clean sheet — but we could not read their terms (HTTP 502) and their
  documented API geo-blocks us (HTTP 451). Absent confirmed permission, we do
  not build on it. If the account holder obtains written permission, that is the
  single highest-value upgrade to this pipeline and it costs nothing.

## 8. Reproducing these measurements

```bash
# Odds ingestion, offline tests
uv run pytest tests/unit/test_odds_devig.py tests/unit/test_odds_football_data.py \
              tests/unit/test_understat_parse.py -q     # 84 passed

# Live ingestion
uv run python scripts/ingest_odds.py --history 2024-25 2025-26
uv run python scripts/ingest_odds.py --fixtures
```

**Known gap:** `--match-fixtures`, which resolves natural odds keys onto FPL
`fixture_id`s via the team-name alias map, is unit-tested but has **not** yet
been exercised end-to-end on live data. It cannot be until football-data
publishes 2026-27 `E0` rows, because the FPL API only serves the current season
and the odds history we hold is 2025-26 and earlier. Re-check it on the first
run that returns a non-zero `e0_fixtures_available`, and expect the three
promoted clubs to be the alias map's first real test.

Raw bodies for every fetch land in `data/raw/odds_football_data/` with their
sha256 in the filename, so any number in this document can be recomputed from
the bytes we actually received.
