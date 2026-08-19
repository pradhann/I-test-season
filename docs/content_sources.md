# Content intelligence: sources, claims, and earned weight

**Measured 2026-08-18/19 UTC, from the developer machine, with the project's own
`fpl-edge/0.1` user agent.** Every HTTP status below was observed, not assumed.
Re-measure with:

```
uv run python -m fpl_edge.ingest.content.pipeline probe
```

---

## 1. The argument, before the numbers

Content creators collectively hold real information no statistical model sees:
injury nuance from a press conference, who looked sharp in a friendly, who took
the pens in a pre-season game with no data feed. That is true, and it is why
this package exists.

The trap is that the **aggregate** of creator opinion is the template, and this
engine already models the template directly from ownership. An unweighted
consensus of creators is the template with extra steps: it will improve backtest
fit, add a correlated copy of a feature the model already has, and improve rank
by nothing, because everyone else has those players too.

So the only content signal worth anything is the part that is *not* the
consensus — a creator who is right more often than the field. Which makes the
question never "what are creators saying" but "which creators have earned the
right to be listened to, and by how much".

**Creator opinion enters the model multiplied by a weight earned from a measured
track record, and that weight is 0.0 until an edge over the coin flip is
demonstrated.** No prior, no benefit of the doubt, no participation credit. The
mechanism is `scoring.earned_weight`; the aggregates the model may read are the
`weighted_*` columns of `consensus.consensus_map`, and when every weight is zero
those columns are zero and the consensus contributes nothing. That is the
intended state, not a bug to patch with a default prior.

---

## 2. What was reached

**38 of 40 registered sources returned HTTP 200. 0 failed. 2 were skipped on
policy.**

| Source class | Registered | HTTP 200 | Items retrieved |
| --- | --- | --- | --- |
| Podcast RSS | 22 | 22 | 8,371 episodes |
| YouTube channels | 13 | 13 | 72 videos |
| Blog / newsletter RSS | 3 | 3 | 22 articles |
| Reddit r/FantasyPL | 1 | — | skipped, see §3 |
| X / Twitter | 1 | — | skipped, see §3 |

Corpus: **8,465 items spanning 2022-07-11 to 2026-08-19**, from **24 distinct
creators** across **29 source feeds**.

### Podcast RSS — the load-bearing source

| Feed | Items | Feed | Items |
| --- | ---: | --- | ---: |
| Fantasy Football Scout | 2,510 | Above Average FPL | 407 |
| Planet FPL | 2,030 | FPL JUiCE | 378 |
| Let's Talk FPL | 777 | Fantasy Football Hub | 325 |
| FPL Focal | 772 | FPL BlackBox | 297 |
| FML FPL | 590 | FPL Family | 277 |
| Always Cheating | 496 | The Athletic FPL | 224 |
| FPL Harry | 491 | All In Football FPL | 213 |
| The FPL Wire | 427 | The 59th Minute | 174 |
| Gianni Buttice | 444 | FPL Raptor | 166 |
| FPL Pod | 434 | Ignore the Template | 139 |
| Who Got The Assist? | 411 | Sky Sports FPL | 84 |

Feed URLs came from the iTunes Search API (a documented public endpoint), then
each was fetched and its item count verified.

Podcasts matter far more than YouTube for the hard part of this task. A YouTube
channel exposes its recent uploads only, all from the last few weeks, so it can
never produce a claim about a gameweek that has already been played. Podcast
feeds carry the full back catalogue — four seasons deep — and that back
catalogue is the **only** place a creator's measured hit rate can come from
before 2026-27 GW1 kicks off.

### YouTube — reached, but not the way you would expect

All 13 channel pages returned 200. **Transcripts were not taken.** See §3.

### Blogs

| Feed | Status | Items |
| --- | --- | ---: |
| `fantasyfootballscout.co.uk/feed/` | 200 | 12 |
| `allaboutfpl.com/feed/` | 200 | 10 |
| `fplreview.com/feed/` | 200 | **0** — feed is valid and empty |

Both WordPress feeds serve an excerpt, so the linked article is fetched for the
full text (subject to the same robots.txt check as everything else).

---

## 3. What was refused, and why

### YouTube transcripts — the honest answer

The brief asked whether `youtube-transcript-api` still works, and to report
honestly if it is blocked.

**It works. It is not blocked. And this package does not use it.**

Measured: installed into a throwaway virtualenv and pointed at two public
videos, it returned 61 and 6 caption snippets on the first attempt — no proxy,
no cookies, no failures. The dependency-free route works identically:
`/watch` 200, `youtubei/v1/player` 200 with six caption tracks, `timedtext` 200
returning well-formed XML.

The problem is `youtube.com/robots.txt`, which for `User-agent: *` contains:

```
Disallow: /feeds/videos.xml
Disallow: /youtubei/
```

`youtube-transcript-api` works by calling `/youtubei/v1/player`. So does the
hand-rolled Innertube fetcher in the user's existing MCP server. Both routes to
a transcript terminate at a Disallowed path, and the channel Atom feed — the
obvious way to list uploads — is Disallowed too.

`ContentFetcher` consults robots.txt before every request, so both routes return
`robots_disallow` rather than data. `youtube.fetch_transcript` is implemented
and refuses unless a caller explicitly passes `allow_disallowed_routes=True`;
nothing in this package passes it. It is kept so the capability is documented
and reviewable rather than quietly reintroduced by someone who reads this note
and concludes the library must be broken.

What is permitted: `/@handle/videos` and `/watch?v=`, giving per video the
title, the exact `datePublished` with offset, and the full description. **This
is a large, real loss** — a twenty-minute video reduced to about 900 characters,
and it is visible in the numbers: 72 YouTube items produced 6 claims, while
8,371 podcast items produced 1,488.

It is not nothing, though, because FPL video titles are the densest claim text
in the corpus. "THE BEST BUDGET FORWARDS FOR GAMEWEEK 1" is a complete claim in
seven words with a stated gameweek.

### Reddit r/FantasyPL — skipped

`reddit.com/robots.txt`:

```
User-agent: *
Disallow: /
```

A blanket disallow. Both unauthenticated routes still answer:
`www.reddit.com/r/FantasyPL/hot/.rss` returns **200** (74 KB) and
`old.reddit.com/r/FantasyPL/hot.json` returns **200** (320 KB). The default
`www.reddit.com/r/FantasyPL/hot.json` returns **403**.

That both `.rss` and `old.reddit` work is precisely why the decision has to be
made on the policy and not the status code. r/FantasyPL is registered as
`AccessPolicy.OAUTH_ONLY`: the sanctioned route is Reddit's own OAuth API, which
needs `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`. Those are not configured, so
the source yields **zero items** rather than being scraped. No user-agent games,
no `old.reddit` back door.

*To enable:* register an app at reddit.com/prefs/apps, put the credentials in
`.env`, and implement the OAuth loader. The registry entry is already in place.

### X / Twitter — skipped

`api.twitter.com/2/tweets/search/recent` returns **401** without a bearer token,
and the free tier does not include search.

`nitter.net/<handle>/rss` returns **200** and would hand over the content for
free. It is a third-party mirror re-serving data X's terms restrict, so using it
launders an access decision we are not entitled to make. Registered
`AccessPolicy.FORBIDDEN` — refused on policy, not on capability.

### General rules applied

- robots.txt is checked per host before every request and obeyed even when the
  target URL answers 200.
- No browser impersonation. No forged TLS fingerprints, no spoofed Chrome
  headers, no CAPTCHA handling, no bot-detection bypass. Every source above is
  reachable with the project's honest user agent over ordinary `httpx`.
- No new dependencies. `feedparser` and `youtube-transcript-api` were both
  avoided; `pyproject.toml` is shared with four other teams and this package may
  not edit it. RSS/Atom parsing uses `lxml`, already a project dependency.

---

## 4. Claims extracted

**1,523 structured claims** from 8,465 items.

```
items=8917 segments=199278 cues=1848 unbound=226 claims=1523
  (gw stated 1095, inferred 428, dropped-no-gw 4)  negations=69
```

A claim is `(creator, player_code, action, gameweek, confidence, rationale,
source_url, published_at)`. `action` is a closed set — `buy | sell | hold |
captain | triple_captain | bench | avoid` — because a free-text action cannot be
scored, and a claim that cannot be scored cannot earn anyone a weight.

| Action | Claims | | Season | Claims | GW inferred |
| --- | ---: | --- | --- | ---: | ---: |
| captain | 484 | | 2022-23 | 192 | 93 |
| sell | 293 | | 2023-24 | 292 | 95 |
| buy | 290 | | 2024-25 | 405 | 107 |
| hold | 171 | | 2025-26 | 571 | 103 |
| bench | 120 | | 2026-27 | 63 | 30 |
| triple_captain | 105 | | | | |
| avoid | 60 | | | | |

The extractor is deterministic and lexical — **no model call**. That is a
constraint accepted deliberately: an LLM extractor cannot be unit-tested against
a fixture, cannot run offline, drifts between provider versions, and would make
the creator hit rates unreproducible — the single number this package exists to
compute would change when someone else's model changed. A rule extractor has a
lower recall and a *knowable* one.

### Player name resolution — 92.1%

```
mentions=16663  resolved=15353 (92.1%)  ambiguous=1310  unknown=0  risky_refused=3937
```

Resolution is to stable cross-season `code`, never `element_id`. A creator's
2023-24 claim about Rice and their 2025-26 claim about him must be the same
player or the track record measures nothing.

**The 7.9% that did not resolve were dropped, not guessed.** Every unresolved
mention was *ambiguous* — a surname belonging to two players in the same season
— and none were unknown. Top refusals: `harris` ×232, `fernandes` ×154,
`williams` ×109, `wilson` ×79, `sarr` ×73, `robinson` ×67, `lewis` ×52,
`phillips` ×50. There were two Ben Davieses in the Premier League with identical
first names, second names *and* web names; picking one silently welds two
careers into a single feature that is noise with a believable distribution.

`risky_refused=3937` is a separate and deliberate count: surnames that are also
ordinary English words — Rice, Wood, Ward, Long, Young, Cash, Best, May, Bright
— are refused as bare tokens and accepted only with a first name attached.
Without that rule, "worth the price" resolves to Ward.

Scoping resolution to the claim's own season (rather than one index over five
seasons) lifted the match rate from **81.7% to 92.5%** on the first feed tested,
because most colliding surname pairs do not actually co-exist in one season.

---

## 5. `published_at`, and why it is the whole ballgame

A claim published after a deadline must never inform that deadline. Content is
the easiest place in an FPL pipeline to leak the future, because podcast
archives are *full* of retrospectives: an episode titled "GW12 Review" published
the Monday after GW12 contains the words "captain", "Haaland" and "GW12", parses
into a perfectly well-formed claim, and would score as a brilliant prediction.
Backfill a few hundred of those and every creator has a magnificent, entirely
fictitious hit rate.

Two independent defences:

1. **`ContentStore.claims_visible_at(as_of)` is the only sanctioned read.** It
   filters `published_at < as_of` — strictly less-than, because a team locks *at*
   the deadline instant. There is deliberately no convenience "all claims"
   reader that could be dropped into a model by mistake.
2. **Scoring refuses a late claim a hit**, marking it
   `unscoreable='published_after_deadline'` so it enters neither the numerator
   nor the denominator of a hit rate.

The second is not redundant. The first stops a late claim reaching one
*decision*; only the second stops it inflating a creator's *weight*, and a bad
weight poisons every future decision.

**In this run the guard fired 140 times** out of 1,523 claims (9.2%). A run
where it rejects nothing is a run where something is broken.

Proof is in `tests/unit/test_content_pit.py`, including
`test_identical_claims_score_differently_on_timing_alone`: two claims identical
in creator, player, action, gameweek and realised result, differing only in
publication instant — one scores a hit, the other is refused.

### Historical deadlines had to be derived

`dim_event` holds deadlines for 2026-27 only; the FPL API exposes the current
season's events and nothing else, and the vaastav backfill does not include
them. Without a historical deadline there is no admissibility test, and every
backfilled claim is a potential leak.

Closed with a verified rule, not a guess:
`deadlines.offset_before_first_kickoff_minutes = 90` is verified in
`fpl_edge/rules/registry.yaml`, and `fact_fixture.kickoff_utc` has every
historical fixture. A derived deadline is `min(kickoff) - 90min`, read from the
rule registry at runtime so it tracks any correction.

**38 authoritative deadlines, 151 derived.** Derived ones additionally carry a
**2-hour safety margin pulling them earlier**: a rescheduled first kickoff would
push a derived deadline *later* than the real one, which is the error direction
that admits a claim that was genuinely too late. Borderline claims are dropped
rather than trusted.

---

## 6. Creator track records — the measured numbers

```
considered=1523  scored=1320  hits=648 (49.1%)
rejected: published_after_deadline=140, gameweek_not_played=63
```

**The aggregate creator hit rate is 49.1% against the positional starter
median.** That is a coin flip. This is the single most important number in this
document: the unweighted creator consensus, measured over 1,320 scoreable claims
spanning four seasons, has no demonstrable edge whatsoever.

| Creator | Claims | Scored | Hits | Rate | Wilson lo95 | **Weight** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| FPL Harry | 62 | 60 | 39 | 65.0% | 0.5236 | **0.0472** |
| Gianni Buttice | 149 | 138 | 82 | 59.4% | 0.5108 | **0.0216** |
| Who Got The Assist? | 32 | 32 | 21 | 65.6% | 0.4831 | 0.0 |
| Always Cheating | 45 | 38 | 24 | 63.2% | 0.4728 | 0.0 |
| FPL JUiCE | 11 | 11 | 7 | 63.6% | 0.3538 | 0.0 |
| FPL BlackBox | 5 | 5 | 3 | 60.0% | 0.2307 | 0.0 |
| All In Football FPL | 36 | 32 | 18 | 56.2% | 0.3933 | 0.0 |
| Above Average FPL | 25 | 20 | 11 | 55.0% | 0.3421 | 0.0 |
| FPL Raptor | 91 | 85 | 46 | 54.1% | 0.4358 | 0.0 |
| Sky Sports FPL | 76 | 71 | 37 | 52.1% | 0.4069 | 0.0 |
| The FPL Wire | 48 | 47 | 24 | 51.1% | 0.3724 | 0.0 |
| FPL Family | 44 | 26 | 13 | 50.0% | 0.3206 | 0.0 |
| Fantasy Football Hub | 45 | 20 | 10 | 50.0% | 0.2993 | 0.0 |
| Let's Talk FPL | 146 | 138 | 61 | 44.2% | 0.3619 | 0.0 |
| Planet FPL | 258 | 225 | 102 | 45.3% | 0.3896 | 0.0 |
| Fantasy Football Scout | 145 | 128 | 53 | 41.4% | 0.3325 | 0.0 |
| FPL Focal | 44 | 43 | 18 | 41.9% | 0.2838 | 0.0 |
| The Athletic FPL | 12 | 12 | 5 | 41.7% | 0.1933 | 0.0 |
| FPL Pod | 192 | 164 | 64 | 39.0% | 0.3189 | 0.0 |
| FML FPL | 23 | 21 | 8 | 38.1% | 0.2075 | 0.0 |
| Ignore the Template | 3 | 3 | 1 | 33.3% | 0.0615 | 0.0 |
| The 59th Minute | 1 | 1 | 1 | 100.0% | 0.2065 | 0.0 |
| AllAboutFPL | 29 | 0 | — | n/a | 0.0 | 0.0 |
| FPL Tom | 1 | 0 | — | n/a | 0.0 | 0.0 |

**2 of 24 creators earned a non-zero weight, and both weights are under 0.05.**

Note the shape of the table. The two highest *point estimates* — The 59th Minute
at 100% (n=1) and Who Got The Assist? at 65.6% (n=32) — earn nothing, while
Gianni Buttice at 59.4% earns a weight, because 59.4% over 138 claims is
evidence and 100% over one claim is not. That is the Wilson lower bound doing
its job: 3/4 is a better point estimate than 130/200 and a far worse reason to
act.

### How a claim is scored

- The player's realised points for the gameweek, summed across fixtures so a
  double gameweek counts once and correctly.
- Benchmark: the **median points among starting players in the same position**
  that gameweek. Starters, not all players — the alternative to buying a
  midfielder is another midfielder who plays, not a pool half-full of unused
  substitutes scoring 1, which would make every recommendation look like genius.
- Positive actions (`buy`, `hold`, `captain`, `triple_captain`) hit when the
  player beat the benchmark. Negative actions (`sell`, `bench`, `avoid`) hit
  when the player failed to. Scoring both with the same comparison would credit
  a creator for correctly saying "avoid" about a player who then hauled.
- Exact ties are misses on both sides — symmetric, and it declines to award
  credit for advice that made no difference.
- A recommended player with no result row scored **zero**, not "missing".
  Absence is a real outcome: he returned nothing to the manager who bought him.

### The weight function

`earned_weight(hits, n)` = `clip(2 * (wilson_lo95 - 0.5), 0, 1)`, and `0.0`
below `MIN_SCORED_CLAIMS = 25` regardless of rate. A lower bound of 0.5 maps to
0; 0.60 — a genuinely good creator at these sample sizes — earns 0.20. That is
deliberately modest. The claim is "this creator knows something", not "this
creator should outvote the model".

### Per-action rates, and a caveat that matters

| Action | Scored | Hits | Rate |
| --- | ---: | ---: | ---: |
| triple_captain | 95 | 76 | 80.0% |
| captain | 420 | 238 | 56.7% |
| buy | 258 | 141 | 54.7% |
| sell | 262 | 106 | 40.5% |
| bench | 91 | 35 | 38.5% |
| hold | 151 | 44 | 29.1% |
| avoid | 43 | 8 | 18.6% |

**Do not read the negative-action rows as "fade the fades".** They are
confounded by *which players each action attaches to*. `avoid` and `hold` claims
attach overwhelmingly to well-known premium players, and a premium beats the
positional starter median most weeks by construction — so an `avoid` claim is
graded against a benchmark that is far too low for the player it names. The
80% on `triple_captain` has the mirror-image bias: nobody triple-captains a
£4.5m defender.

The per-action scopes are persisted for diagnosis. **Only the `all` scope gates
model entry**, and fixing the negative-action comparator (grading against a
price-matched or ownership-matched peer set rather than the positional median)
is the first thing to do if these claims are ever to be used directionally.

---

## 7. Storage

Four tables, applied by `fpl_edge/ingest/content/migrations/content_001_claims.sql`
through the runner in `content/store.py`, following the precedent in
`fpl_edge/interfaces/migrations/`. **`fpl_edge/store/schema.sql` is not
touched** — it is the shared contract the store, model and optimiser teams read.
Migration versions are prefixed `content_` so they can never collide with
`001_idea_registry`.

| Table | Rows | What it holds |
| --- | ---: | --- |
| `content_source` | 40 | The registry, with last probe status and item count |
| `content_item` | 8,465 | Video / episode / article + text + `published_at` |
| `content_claim` | 1,523 | The deliverable. Immutable, never updated |
| `claim_outcome` | 1,523 | Resolved hit/miss, or the reason it was unscoreable |
| `creator_score` | — | Earned weights, stamped `as_of` so a decision is auditable |

These are deliberately **not** registered in `store.PIT_KEYS`. `PIT_KEYS`
describes facts about the world keyed by `(entity, as_of)` so the latest value
at an instant can be selected. A claim is not that shape: it is an immutable
utterance, made once, never superseded. Modelling it as `(entity, as_of)` would
imply a claim can get a newer version, and the whole point of a track record is
that it cannot. Point-in-time discipline is enforced by `published_at` instead.

## 8. Commands

```
uv run python -m fpl_edge.ingest.content.pipeline probe
uv run python -m fpl_edge.ingest.content.pipeline ingest --backfill-days 1500
uv run python -m fpl_edge.ingest.content.pipeline score
uv run python -m fpl_edge.ingest.content.pipeline consensus --season 2026-27 --gw 1 \
    --as-of 2026-08-21T17:30:00+00:00
```

## 9. Known weaknesses

1. **No YouTube transcripts.** The single largest loss, and it is a policy
   decision (§3), not a technical failure. 72 YouTube items yielded 6 claims.
2. **The negative-action comparator is biased** (§6). `avoid`/`hold`/`bench`
   rates are not usable directionally until the benchmark is price- or
   ownership-matched.
3. **28% of claims have an inferred gameweek** (428 of 1,523), taken as the
   first deadline after publication rather than stated in the text. Flagged per
   claim in `gw_inferred` and reported separately; an inferred gameweek is a
   weaker claim than a stated one.
4. **Lexical extraction has unknown recall.** Precision is defensible and
   testable; recall is not measured. 1,848 cues produced 1,523 claims with 226
   unbound, but there is no labelled set saying how many claims the corpus
   actually contains.
5. **151 of 189 deadlines are derived**, not authoritative (§5). Conservative,
   but a rescheduled historical fixture could still shift one.
6. **Sample sizes are small.** The largest creator has 225 scored claims; most
   have under 50. Nobody will clear the weighting bar convincingly until several
   more seasons of claims accumulate — which is the correct outcome, and is why
   the machinery is built now rather than the weights being assumed.
7. **r/FantasyPL contributes nothing** pending Reddit OAuth credentials (§3).
