# Free projection providers: the inventory

**What this serves.** The platform does not build a bespoke minutes model. It
**copies** free xMins/xPts opinions and blends them by measured track record.
This document is therefore the constraint on everything downstream: the ensemble
can only weight what this inventory made reachable.

**Every status code, byte count and field name below was observed by this repo**
on 2026-08-19/20 from a UK-routed connection with the honest User-Agent
`fpl-edge/0.1 (personal research; contact via repo owner)`. Nothing here is
recalled, inferred, or copied from a provider's marketing page. Re-measure with:

```
uv run python -m fpl_edge.ingest.projections.cli probe     # live status per URL
uv run python -m fpl_edge.ingest.projections.cli report    # the verdict table
uv run python -m fpl_edge.ingest.projections.cli ingest    # the run itself
uv run python -m fpl_edge.ingest.projections.cli providers # what actually landed
```

The machine-readable version of this table is
`fpl_edge/ingest/projections/providers.py`; it and this document are two
renderings of one registry, and the registry is the one that runs.

**24 candidates evaluated. 9 ingested, 2 on the watchlist, 3 paywalled, 1
blocked by obfuscation, 6 forbidden by a crawl policy or a licence, 3 dead.**
(Counts updated 2026-08-24: AIrsenal-via-fpl-apex and FPL-Core-Insights
ingested; ClubElo measured dead. Their statuses were observed 2026-08-24;
everything else 2026-08-19/20.)

---

## 1. The normalised contract

Everything ingested lands behind one shape, exposed as the view
`projection_normalized` (migration `003_xmins.sql`):

```sql
SELECT source, player_code, gw, xmins, xpts, fetched_at FROM projection_normalized
```

with `season`, `xp_if_appears` and `p_appear` riding along, because `gw` alone
is not a key across seasons — there is a GW1 every August.

Three column decisions carry weight:

| Column | Means | Never |
|---|---|---|
| `xpts` | Expected FPL points, already appearance-weighted | Points *if he plays* |
| `xmins` | Expected minutes, 0–90ish | A probability |
| `p_appear` | P(any minutes), 0–1 | An expectation of minutes |

`xmins` and `p_appear` are separate columns and neither is derived from the
other. A source publishing 78.4 xmins is saying something 0.92 p_appear cannot
say — it separates a 90-minute lock from a starter who gets hooked on the hour.
A source publishing 0.92 p_appear is saying something 78.4 xmins cannot — it is
a probability, not an expectation. **Converting between them requires a minutes
model, and building one is precisely what this platform refuses to do.** Both
stay NULL for sources that publish neither. A NULL is an absence; a zero would
be a claim.

Predicted lineups live in their own table, `fact_predicted_lineup`, because
"a journalist wrote this human's name on a team sheet" and "a model thinks 85%"
fail in different ways and downstream weighting needs them separable.
External ownership lives in `fact_external_ownership`, because the unit is a
fraction of managers rather than points and a units bug that crosses those two
would be invisible.

### Identity: `code`, never `element_id`

Every provider that keys on FPL's `element_id` is remapped to the stable
cross-season `code` through `dim_player` at the fetch instant. `element_id` is
a per-season row number — element 449 was Bruno Fernandes in 2025-26 and is
Lewis Hall in 2026-27. Name-keyed providers resolve through
`normalize_name()`, which folds diacritics **and** stroke letters (`Ødegaard`
becomes `odegaard`, not `degaard`), scoped to a single club's roster.

**Anything that does not resolve is dropped and counted. Never guessed.** The
per-provider unresolved count is printed on every run and is reported in §4.

### Point-in-time: `fetched_at` is the fetch instant

Not the deadline, and not when the provider computed the number. We cannot
observe when FPL Form ran its model; we can only observe when we read it.
Stamping anything earlier would leak a provider's post-deadline revision
backwards into a pre-deadline decision. A revised projection gets a *later*
`fetched_at` and both rows survive, because "the projection as it stood at the
deadline" has to mean the last one we actually held.

---

## 2. The ranked inventory

Ranked on coverage × freshness × reliability × licence-cleanliness.

### Ingested — free, working, in the warehouse today

| # | Source | xPts | xMins | P(play) | Coverage | Freshness | Licence | Interface |
|---|---|---|---|---|---|---|---|---|
| 1 | **FPL Form** | ✅ | ✕ | ✅ | 595 players × GW1–8 | Recomputed on request; export is live | Personal use explicitly permitted, publishing forbidden | `POST /export-fpl-form-data.php` → `text/csv` |
| 2 | **fplbench** (GitHub) | ✅ | ✅ | ✕ | 587 players, next GW | Per-GW file committed pre-deadline, CI-driven | **MIT** + "research and personal modelling use only" | raw.githubusercontent CSV |
| 3 | **blueladd11** (GitHub) | ✅ | ✅ | ✕ | 469 players × 6 GW horizon | **Hourly**, GitHub Action, archived snapshots | **None** — public, unlicensed | raw.githubusercontent CSV, discovered path |
| 4 | **FPL `ep_next`** | ✅ | ✕ | ✅ | 595 players, next GW | Continuous — every price poll sees a fresh value | The official public API | `bootstrap-static` JSON |
| 5 | **Rotowire lineups** | ✕ | proxy | proxy | 20 team sheets: 220 starters + 88 injury-list names | Upgrades to *confirmed* ~60–75 min before kickoff | robots-permitted + `llms.txt` welcoming bots | Server-rendered HTML |
| 6 | **Premier Injuries** | ✕ | ✕ | ✅ **explicit %** | 91 flagged players, all 20 clubs | Continuous editorial | `User-agent: * / Disallow:` — fully open | Server-rendered HTML table |
| 7 | **LiveFPL** | ✕ | ✕ | ✕ | 592 players (ownership, not points) | Per-GW static JSON | `Allow: /`, no restriction | Static JSON |
| 8 | **AIrsenal via fpl-apex** (GitHub) | ✅ | ✕ | ✕ | 604 elements × GW2–9 horizon | Per Apex workflow run; `generated_at` + AIrsenal commit sha on every row | **MIT** carrier running the **MIT** Turing-Institute model, pinned in `upstreams.lock.json` | raw.githubusercontent CSV, fixed path |
| 9 | **FPL-Core-Insights** (GitHub) | ✕ | ✕ | ✕ | Per-player **per-match xG/xA/xGOT** + DEFCON actions, realised not forecast | Twice daily, 07:30/17:30 UTC | **No LICENSE**; README grants use verbatim, asks for a link back | raw.githubusercontent CSV per tournament/GW → `fact_player_match_stats` |

Why this order:

1. **FPL Form** is first on coverage × horizon: eight gameweeks, every player,
   and it decomposes into `xp_if_appears × p_appear`, so its minutes opinion
   and its points opinion are separately scoreable. Its licence explicitly
   names *"comparing the accuracy of FPL Form predictions against other
   sources"* as an intended use of the export — which is exactly what the
   ensemble does.
2. **fplbench** is the cleanest thing in the inventory: an MIT licence, keys on
   the stable `player_code` so nothing can be mis-mapped, publishes real
   `pred_minutes`, and **scores itself in public** after each gameweek.
3. **blueladd11** ranks third only because it has no licence. On freshness it
   is first by a distance — rebuilt hourly, with every pre-deadline snapshot
   kept in git *with its timestamp*. That is the only feed whose past claims
   can be audited backwards; every other free provider overwrites itself, so a
   track record can only be built forward from the day we started fetching.
4. **`ep_next`** is crude and form-derived, but it is free, universal, instant,
   and published by the party that also sets prices. It is the baseline every
   other provider must beat, and it must be *stored at fetch time* to be
   scoreable at all.
5. **Rotowire** is the best free xMins *proxy* that exists pre-deadline: a
   named starter is the strongest available evidence of 60+ minutes.
6. **Premier Injuries** is the only free source that publishes P(plays) as a
   **number** rather than as a label a consumer must convert by guessing.
   Ranked below Rotowire only on breadth — 91 flagged players against 220 named
   starters — not on quality.
7. **LiveFPL** is not a points projection at all. It answers the other half of
   a rank-utility question (what the field will own and captain), which is why
   it is last here and not excluded.
8. **AIrsenal via fpl-apex** (added 2026-08-24) is the breadth pick: the only
   feed whose model family is public and genuinely different — a Bayesian
   match-simulation model (bpl-next), against FPL Form's regression, fplbench's
   decomposed heads and FPL's own form heuristic. Its quirks (Maguire at 5.4
   xp) are the point: an ensemble learns from disagreement, not from five
   copies of one method. First live run appended 4,832 rows (GW2–9). The
   repo's own blended `apex_latest.json` is deliberately NOT ingested — a
   blend of our inputs voting as a new source double-counts.
9. **FPL-Core-Insights** (added 2026-08-24) is not a projection either: it is
   the only reachable, licence-tolerable **per-match xG** source found —
   Sofascore 403s its own robots.txt, FotMob disallows `/api/*`, FBref 403s,
   Understat robots-forbids. Realised match stats land in
   `fact_player_match_stats` under `source='fpl_core_insights'`, never in
   `fact_projection`, so another party's read of a match can never be mistaken
   for a forecast or for the official FPL numbers. First live run: 313 rows,
   GW1 2026-27, 8 matches, 0 unresolved. Caution: fpl-apex pins this same
   dataset as its enrichment source — shared upstream ⇒ not independent.

### Watchlist — reachable and permitted, not yet ingested

| Source | Measured | Why not yet |
|---|---|---|
| **SportsGambler lineups** | 200, 174,590 B; robots permits and publishes `sitemap-lineups.xml` — but every "view lineups" toggle is `href="#"`, every `div.toggle-content` is empty, and the sitemap lists only the league index | **No XI is in the bytes.** The only route is an undocumented AJAX endpoint found by reading the site's own scripts — nearer the FPL Review line than the Rotowire line. Rotowire gives the same signal, server-rendered, in one request. Re-check if the site ever server-renders the XIs. |
| **fpl-projections-site** (Juz92backup) | 200 on every data file; `manifest.json` run_at 2026-08-18T03:00Z, season 2026-27 | Monte-Carlo xPts percentiles, genuinely current — but anonymous, unlicensed, and the account name ("…backup") suggests it may vanish. |

### Paywalled — schema and integration notes only, no scraping around the paywall

**FPL Review** — the single most valuable source we could not take.

- Measured: `/`, `/free-planner/`, `/massive-data-planner/`, `/team-planner/`,
  `/terms/` all HTTP 200 and all return the **same 2,111-byte shell** with an
  empty `<div id="root">`. No server-rendered data, no documented API.
- The 3.48 MB bundle at `/assets/index-*.js` is run through a string-array
  obfuscator: `_0x`-mangled identifiers, every URL literal split into an
  indexed table, and the only readable network call is
  `xb+"/session",{credentials:...}` — a cookie-authenticated session endpoint.
- `robots.txt` allows `User-agent: *` at `Allow: /`, but carries an explicit
  `Disallow: /` for ClaudeBot, GPTBot, CCBot, Bytespider, Amazonbot,
  Applebot-Extended, Google-Extended and meta-externalagent, plus
  `Content-Signal: search=yes,ai-train=no,use=reference`. The letter of the
  file permits our honest agent; the intent is plainly to keep automated AI
  consumption out.
- **Recovering the data URLs would mean deobfuscating a bundle that was
  obfuscated on purpose, and reaching them would mean holding an account
  session. Both are circumvention. We stopped.**
- **Integration instruction:** take it by hand. Sign up, use the free planner
  as a human, export, and drop the CSV in. Land it as
  `(provider='fplreview', season, gw, code, xp, fetched_at)` through
  `ProjectionStore.append("fact_projection", …)` with `fetched_at` set to the
  moment *you* exported it, never to the deadline. Expected schema, from the
  planner UI: one row per player per gameweek with a points column and an
  `xMins` column.

**Fantasy Football Hub** — `/` 200 (84,419 B) advertising the predictions;
`/pricing` 404; predictions require an account. No price is quoted on any page
reachable without one, so none is stated here.

**Fantasy Football Fix** — `/` 200 (95,519 B), `robots.txt` 404 (i.e. no policy
published), `/plans/` 404. Freemium; the projections are in the premium tier.

**Fantasy Football Scout** — `robots.txt` fully permissive, `/team-news/` HTTP
200 at 563,862 bytes titled "FPL 2026/27 – Predicted Line-ups", but only ~15
player-name nodes across 20 clubs: one teaser per club, with the full XIs
member-only. Rotowire supplies the same signal free, so there is nothing here
worth paying-and-scraping for.

For all four: **paying for a subscriber product and then scraping it is not
something this engine does.** The paid path is a human export into the same
table, with the same point-in-time rule.

### Refused — and the reason each refusal is a rule, not a failure

| Source | Measured | Verdict |
|---|---|---|
| **Sofascore** | `403` on `/robots.txt` **itself**, and 403 on `/api/v1/...` | Unreadable policy. An unreadable "no" is still a no, and getting past the 403 means impersonating a browser. |
| **Fantasy Football Pundit** | `403` on `/robots.txt`, `403` on `/fpl-team-news/` (52-byte body) | Same rule. |
| **FBref** | `403` on `/robots.txt`, `403` on the stats page | Same rule; already settled elsewhere in this repo. |
| **FotMob** | `robots.txt` **200** and explicit: `User-agent: *` carries `Disallow: /api/*`, re-allowed only for Googlebot, Bingbot, Qwantbot, AmazonAdBot | Explicitly forbidden for us. |
| **WhoScored** | `robots.txt` 200, disallows `/Predictions/` for everyone | The part we want is exactly the part they close. |
| **derekkuang/Fantasy-Premier-League** | LICENSE 200: *"All rights reserved… Viewing this repository does not grant any right to reuse its contents."* | The author wrote down that we may not. Listed so nobody rediscovers a live free feed in three months and wonders why it is missing. |
| **FPL Statistics** | `ConnectTimeout` on every attempt, three host forms, three sessions. No TCP handshake | Dead, not blocked — nothing answered. Price prediction is in any case the one thing here we do not need from a third party: `fact_player_state` already carries `transfers_in_event`/`transfers_out_event` every poll, which is the input those sites model from. |
| **elfootball** | `NXDOMAIN` for `.com`, `www.`, `.co.uk`, `.net`, `el-football.com` | No host to ask. |

Two policy notes that cost real work to get right:

- **404 on `/robots.txt` is not the same as unreachable.** RFC 9309 §2.3.1.3 is
  explicit that a 4xx means the crawler *may* access any resource. A timeout or
  a 403 means we do not know the policy, and not knowing is a no. Both occur
  here — fantasyfootballfix.com serves 404, api.sofascore.com serves 403 on the
  policy file itself — and collapsing them would either lock us out of a site
  that permits us or let us into one whose rules we cannot read.
- **Python's `urllib.robotparser` fails open and is not used.** It matches rule
  paths with `str.startswith`, gives `*` and `$` no special meaning, and
  applies first-match-wins rather than RFC 9309 §2.2.2's longest-match-wins. It
  answers `True` for FotMob's `Disallow: /api/*`. Trusting the stdlib would
  have produced exactly the fetch the operator forbade. `robots.py` writes the
  matcher out for that reason.

**And check the host you actually fetch from.** `www.livefpl.net/robots.txt` is
a 404 because that host redirects to `plan.livefpl.net`. The data is on
`livefpl.us`, whose `robots.txt` is served and permissive. The policy that
governs a fetch is the policy of the host being fetched, not the host in the
brand name.

---

## 3. Exact parse paths

**FPL Form** — `POST https://fplform.com/export-fpl-form-data.php` with
`firstgw`, `lastgw`, `all=1`. Returns `text/csv`, wide:
`ID,Name,Team,Pos,Price,1_pts_no_prob,1_prob,1_with_prob,2_…`. Melted to long
on the `(\d+)_(pts_no_prob|prob|with_prob)` pattern →
`xp_if_appears`, `p_appear`, `xp`. `tba_*` columns are **dropped**, not coerced
to a gameweek: guessing which gameweek a postponed fixture lands in is the
provider's job, and a wrong guess scores their projection against the wrong
result. `ID` is `element_id` and is remapped.

**fplbench** — `GET raw.githubusercontent.com/PascalAI2024/fplbench/main/outputs/predictions/gw{gw}_{season}.csv`.
Columns used: `player_code` (stable code, validated against `dim_player`),
`pred_minutes` → `xmins`, `e_points_final` → `xpts`, `event` → `gw`. Their
`ep_next` column is deliberately **not** taken — we read it first-hand as
`fpl_ep`, and second-hand would let one number vote twice.
`pred_points_decomposed` is likewise not stored: two heads from one publisher
under two provider names is one source voting twice.

**blueladd11** — `GET api.github.com/repos/blueladd11-commits-tocode/fpl-projections/contents/out`
to discover the newest `projections_gw{gw}_{YYYYMMDDTHHMMSSZ}_gw{gw}.csv`, then
the raw file. Columns: `element` → remapped, `xp` → `xpts`, `xmins` → `xmins`,
`xp_next` (semicolon-joined, six values) → **six rows**, `gw` = base + index.
`p_start` is **not** written to `p_appear` — it is P(starts), and a 60-minute
substitute has `p_start` near 0 with `p_appear` near 1.

The horizon expansion is guarded: `xp_next[0]` must equal the file's own `xp`
to within 0.011 (their rounding), or the two columns do not mean what the
mapping assumes and the run falls back to a single gameweek. Six confidently
wrong rows are worse than one right one. `xmins` is written on the base
gameweek only; copying it forward would invent a claim the publisher never made.

**FPL `ep_next`** — `GET /api/bootstrap-static/`. `ep_next` is a string per
element, already scaled by the game's own availability factor (so it is an
`xp`, not an `xp_if_appears`). `chance_of_playing_next_round` is an integer
percentage or `null`; `null` **with `status == 'a'`** means "no flag" and is
stored as `p_appear = 1.0`, but `null` with any other status is genuine
ignorance and stays NULL. `ep_this` is deliberately not ingested: once a
gameweek kicks off it describes the past, and at every instant before a
deadline `ep_next` is the number that refers to the decision being made. The
target gameweek is the event flagged `is_next`; if no event is, the module
raises rather than writing rows about no gameweek.

**Rotowire** — `GET /soccer/lineups.php`. `div.lineup__box` per fixture,
`.lineup__abbr` team codes (home first), `.lineup__status is-expected` or
`is-confirmed`, `ul.lineup__list.is-home` / `.is-visit`, `li.lineup__player >
a[title]` for the full name (display text is abbreviated: "R. Calafiori"), and
`li.lineup__title` "Injuries" as the boundary after which entries are the
OUT/QUES/SUS list.

Two refusals are hard-wired: a team sheet that does not parse to exactly eleven
starters aborts the whole page, and every `(home, away)` pair on the page is
checked against `fact_fixture` for the target gameweek — Rotowire shows "the
next matchday", which a midweek cup slate or a blank GW would desynchronise
from the next FPL gameweek.

**Premier Injuries** — `GET /injury-table.php`. `tr.heading` names the club;
the `tr.sub-head` that follows carries the `team_<id>` class; every
`tr.player-row.team_<id>` is one flagged player with six cells (Player, Reason,
Further Detail, Potential Return, Condition, Status), each prefixed by a
`div.mob-title` repeating its own column name for the mobile layout, which must
be stripped or every value arrives prefixed with its own header. `Status` maps
`Ruled Out → 0.0`, `25% → 0.25`, `50% → 0.50`, `75% → 0.75`, `100% → 1.0` into
`p_appear`. Club attribution goes through the shared `team_<id>` class rather
than "the last heading seen while walking the document" — the two agree until
the site nests a table, and then one club's injuries become another's.

**LiveFPL** — static JSON on `livefpl.us`: `/predictedEOs/{gw}.json`,
`/top10k.json`, `/elite.json`, `/planner/all_player_info.json`. All three
ownership files are **effective** ownership (ownership × captain multiplier),
so exactly one player exceeds 1.0 and each file sums to ~12. Clipping to 1
would destroy the information the files exist to carry; the parser
range-checks instead.

---

## 4. Live smoke run — 2026-08-20 06:31 UTC

```
fplform          ok       4,736 appended   4,760 parsed     3 unresolved  HTTP 200, 595 players x GW1-8
livefpl          ok       2,272 appended   2,275 parsed     3 unresolved  predicted_eo@2026-27/gw1:592 top10k@2025-26/gw38:840 elite@2025-26/gw38:840
fpl_ep           ok         595 appended     595 parsed     0 unresolved  HTTP 200, ep_next for GW1
rotowire         ok         298 appended     308 parsed     6 unresolved  HTTP 200, 20 team sheets for GW1, 217 starters
premierinjuries  ok          86 appended      91 parsed     5 unresolved  HTTP 200, 20 clubs for GW1, 46 ruled out, p_appear only
gh_fplbench      ok         587 appended     587 parsed     0 unresolved  HTTP 200, gw1_2026-27.csv, gw=[1], xmins=yes
gh_blueladd      ok       2,814 appended     469 parsed     0 unresolved  HTTP 200, projections_gw1_20260818T174829Z, gw=[1..6], xmins=yes

7/7 providers ok, 11,388 rows appended, 17 names/ids unresolved
```

What that buys, per fetch — the stable shape, rather than the cumulative row
count, which grows by design on every run:

| source | table | rows/fetch | distinct players | gw range | xmins | xpts | p_appear |
|---|---|---|---|---|---|---|---|
| `fplform` | `fact_projection` | 4,736 | 592 | 1–8 | — | all | all |
| `gh_blueladd` | `fact_projection` | 2,814 | 469 | 1–6 | gw1 only (469) | all | — |
| `gh_fplbench` | `fact_projection` | 587 | 587 | 1 | all | all | — |
| `fpl_ep` | `fact_projection` | 595 | 595 | 1 | — | all | all |
| `premierinjuries` | `fact_projection` | 86 | 86 | 1 | — | — | all |
| `rotowire` | `fact_predicted_lineup` | 298 | 298 | 1 | 217 starters | — | — |
| `livefpl` | `fact_external_ownership` | 2,272 | 971 | 1, 38 | — | — | — |

**Cumulative rows exceed one run's, and that is the design, not duplication.**
`fetched_at` is the fetch instant, so each re-fetch of a revised projection is a
new fact and the earlier number survives — which is the only way "the projection
as it stood at the deadline" can be answered later. `ProjectionStore.as_of()`
returns the latest row per entity at any instant, and re-appending an
*unchanged* body under its original instant appends zero rows, so the raw
archive can be replayed to rebuild the warehouse without doubling it.

Read the live numbers with
`uv run python -m fpl_edge.ingest.projections.cli providers`.

### Every unresolved name, and why

| Provider | n | Names | Why |
|---|---|---|---|
| `fplform` | 3 | Dedić (NEW), David (BHA), Gozo (CRY) | element_ids 593–595: signed since our last `dim_player` refresh. |
| `livefpl` | 3 | same three | LiveFPL knows 595 elements; `dim_player` holds 592. |
| `rotowire` | 6 | Djordje Petrovic (BOU), Ferdi Kadioglu (BHA), Amar Dedic (NEW), Ryan McAidoo (MCI), Teddy Sharman-Lowe (CHE), Caleb Wiley (CHE) | Not in `dim_player` for 2026-27 at all. |
| `premierinjuries` | 5 | Eli/Junior Kroupi, Issahaku/Abdul Fatawu, Thomas/Tom Heaton, Ryan Christie ×2 | Given-name forms our ladder refuses to bridge, plus one player listed twice with two different statuses. |
| `gh_fplbench` | 0 | — | Keys on stable `player_code`. |
| `gh_blueladd` | 0 | — | Every `element` mapped. |

**17 of 11,388 rows.** Every one is dropped, counted, and printed. None is
guessed. The three `fplform`/`livefpl` names are the *same* three humans, which
is the useful diagnostic: the gap is a stale `dim_player`, not a broken parser,
and it closes on the next squad refresh by the team that owns that table.

---

## 5. Failure isolation

Every provider runs inside its own `try`. This is not defensive habit, it is
the deadline requirement: the run that matters happens in the ninety minutes
before a Friday 17:30 deadline, and a provider that has changed its HTML, let a
certificate lapse, or gone dark must cost us *that provider's* rows and nothing
else. A bare loop converts one site's bad afternoon into a blind transfer.

A failed provider is reported with its real exception type and message and
recorded as `ok=False`. It is never retried into silence and never replaced
with a stale copy, a zero, or an interpolation. A partial run exits **0** — four
of five sources is a successful deadline run, and paging someone for every
wobble produces an alert that is ignored by November. Only a total wipe-out
exits non-zero.

Four failures the first live run actually produced, and what each one taught:

1. **`fplform` → `NOT NULL constraint failed: fact_projection.gw`.** The cause
   was three columns away: an all-`None` `xmins` Series built with a fresh
   `RangeIndex` against a *filtered* frame, which pandas **aligned** rather than
   rejected, manufacturing phantom rows whose every key was NaN.
2. **`livefpl` wrote 595 rows of 2026-27 GW1 ownership under
   `season='2022-23', gw=38`.** Season inference picked by *containment*, and
   2026-27 was not a superset because LiveFPL knew three players `dim_player`
   did not — so it fell through to a season four years old whose larger id set
   happened to contain them all. Every id was then remapped onto whoever held
   it in 2022. **Nothing raised.** Those rows were deleted by hand. Inference is
   now scored by mismatched-id count, where being 183 ids too *big* counts
   against a season exactly as much as being 3 too small.
3. **`rotowire` aborted the entire page on a new `SUS` flag.** An unrecognised
   *player-level label* is one player's problem; only a changed *page shape* —
   a missing `<ul>`, a nine-man XI — is allowed to abort.
4. **`rotowire` silently dropped four predicted starters** (Garnacho, Abraham,
   Fatawu, Florentino Luis) because each was named twice: once in the XI and
   once on the doubtful list. That pair is not a contradiction, it is a
   *stronger* claim than either half, and it now collapses to
   `predicted_start=True` with `certainty='questionable'`. XI-plus-OUT still
   refuses, because that one really is a contradiction.

---

## 6. Offline tests

Four files, 61 tests, no network:

| File | Covers |
|---|---|
| `tests/unit/test_projections_parse.py` | Parse correctness for all seven providers against trimmed real responses |
| `tests/unit/test_projections_resolve.py` | The match ladder, its refusals, season inference |
| `tests/unit/test_projections_store.py` | Idempotent re-ingest, contradiction refusal, the normalised view |
| `tests/unit/test_projections_isolation.py` | Per-provider isolation, exit codes, run idempotency |

Every fixture in `tests/fixtures/projections/` is a **trimmed copy of a real
response** this repo received on 2026-08-20, not a hand-written approximation.
That distinction is the point of the directory: a hand-written fixture tests
the parser against the shape we *believe* the site has — which is the belief the
parser already encodes, so it can only ever agree with itself. A trimmed real
response tests it against the shape the site actually had.

---

## 7. The calibration loop: how weights are earned

`projection_weight` had 0 rows by design until the season's first gameweek
settled — weighting sources with no track record is fabrication (MASTER_PROMPT
Phase 2.5). The loop that earns the rows is
`fpl_edge/eval/projection_scoring.py`, run by `fpl_edge.jobs.post_gw`
immediately after `settle_results`:

1. **Score** (`score_gameweek`): for every provider with projections for a
   settled gameweek **fetched at or before its deadline**
   (`dim_event.deadline_utc`, read through `ProjectionStore.as_of` — a
   post-deadline revision is not a claim the provider staked and is never
   scored), per-player error against the official `fact_player_fixture`
   actuals. MAE and RMSE overall and per position; Brier on `p_appear` vs
   played-any-minutes where a provider publishes it. A projected player whose
   team played but who never featured counts as an actual **0** — dropping
   him would flatter fringe-heavy projections; players at clubs with no
   fixture that gameweek are excluded. One measurement per (provider, gw)
   lands in `fact_projection_score` (migration `004_projection_scores.sql`)
   with the all-provider-mean baseline on the same observations beside it,
   and is never re-measured (idempotent skip).
2. **Fit** (`fit_weights`): inverse-MSE weights over the *accumulated*
   scores, observation-pooled across gameweeks. A provider earns a nonzero
   weight only with `n_obs >= 200` player-gameweek observations — at 200 the
   standard error of an MSE estimate is ~10% of the MSE, so smaller
   differences are noise a weight must not encode; one fully-covered settled
   gameweek (~590 projected players) clears the floor, a partial feed (the
   91-player injury list; a provider that missed the deadline) must
   accumulate. Everyone else gets an explicit `earned = FALSE, weight = 0`
   row with the reason in `holdout` — including Premier Injuries permanently
   (`p_appear` only, no xp to score). Weights are normalised over earning
   providers and written via `ProjectionStore.record_weights` under a
   deterministic `fit_id` (`{season}:invmse:thru-gw{N}`), so a refit over
   unchanged scores replaces itself.
3. **Read** (`sem_projection_weights(t)` in the semantic layer): the latest
   fit at the instant, with loss / baseline_loss / n_obs / holdout beside
   every weight and `track_record_gws` saying how deep the record is. With
   one gameweek scored the weights are *earned but shallow*, and the surface
   says so — any consumer quoting the leaderboard must quote the depth.

The weights are **not** blended into the solver by this loop. That is a
later, explicit step; this loop ends at weights-with-evidence.

Tests: `tests/unit/test_projection_scoring.py` — the deadline filter is
verified to be load-bearing (breaking it makes three tests fail), plus the
n_obs floor, idempotent rescoring, weight normalisation, zero-fill, and every
`earned = FALSE` path.

## 8. What to add next, in order

1. **A second predicted-lineup source.** SportsGambler is measured, permitted
   and viable at eleven requests. Worth it as soon as the ensemble can score
   whether it disagrees with Rotowire *usefully* rather than just noisily.
2. **Premier Injuries' return dates.** The page carries "Potential Return"
   (Kroupi: 07/11/2026), which is a real signal for GW2..N. It is not expanded
   today because doing so needs a recovery model and a fixture-date mapping —
   an opinion the publisher never stated. If it is ever added, it must be a
   *separate provider key*, so the copied claim and the derived one are scored
   apart.
3. **A hand-exported FPL Review lane.** The one high-value source we cannot
   take automatically, and the ingest path already accepts a CSV from disk.
4. **Backfill blueladd's archive.** `out/archive/` holds pre-deadline snapshots
   with their timestamps in git history. That is the only free feed whose track
   record can be reconstructed *backwards* instead of accumulated forwards, and
   a season of history is worth more to the ensemble than a season of waiting.
5. **Re-probe ClubElo.** `api.clubelo.com` is the best free team-Elo API and a
   team-strength opinion independent of our Dixon-Coles — but on 2026-08-24 it
   accepted TCP and sent 0 bytes in 25–45 s on four attempts. Dead until it
   answers; the fallback carrier is FPL-Core-Insights' `matches.csv`
   `home_team_elo`/`away_team_elo`, which were empty when measured. Whichever
   wakes first gets a `fact_team_rating` table.
6. **FPL-Core-Insights' other tournaments.** Cups and friendlies (GW0) ship in
   the same layout the Premier League ingest already parses; pre-season
   minutes are the cheapest xMins prior that exists in August.
