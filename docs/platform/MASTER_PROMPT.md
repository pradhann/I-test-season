# Master prompt — what to build, and why

Grounded in three read-only code audits and three research tracks, 2026-08-20.
Findings marked **VERIFIED** were confirmed by running code or fetching the
endpoint during this session. Supporting evidence is in the appendix and in
`docs/platform/AUDIT_2026-08-20.md`.

---

## The three facts that should reorder your priorities

**1. Your backtests are silently invalid. VERIFIED.**
`ingest/vaastav.py:589-594` correctly writes NULL availability because the
historical archive carries none — confirmed, 109,956 historical
`fact_player_state` rows with *zero* non-null `status` or `news`, against 2,378
fully populated 2026-27 rows. Then `ingest/injuries.py:136` does `fillna("a")`
→ `play_prob = 1.00`, and `store/warehouse.py:206` treats NULL as selectable.
**Every injured and suspended player across four seasons is pickable.** Any
historical evaluation optimises a squad the game would have rejected. Until
this is fixed, no backtest result means anything.

**2. The rank machinery this project is about is not wired to anything.**
A reachability trace over the import closure from every production entrypoint
found `SeasonSimulator` is **never instantiated in production** — the only path
into `sim/` is through `_paired_validation` (`myteam/recommend.py:805`), whose
`validator` argument no caller supplies. `strategies/` and
`models/ensemble/` are wholly orphaned packages. 14 modules have zero
production reach. Meanwhile `fact_odds_derived` holds 1,720 rows of real
derived odds with **no reader anywhere**.

**3. Nobody in the market optimises the actual objective — and you almost do.**
Every commercial tool maximises `Σ EV` and bolts on a risk dial. FPL Review's
"Risk Position" (`risk ≈ EV × EO`) is the *only* shipped rank-aware objective
term in the entire market. Your `docs/platform/rank_objectives.md` already
derives further than that. **The edge is real and it is close — it is just not
plugged in.**

The theme: the foundations are strong (point-in-time enforcement with
`LeakageError`, append-only history, 100% identity resolution across four
seasons, honest-gap patterns in `dossier.py`). What is missing is **connection
and honesty**, not sophistication.

---

## The master prompt

```
You are working on a private FPL decision engine at
~/Documents/Github/i-test-season. The objective is P(final rank <= 10k) —
NOT expected points. Every decision below is judged against rank.

Read docs/platform/AUDIT_2026-08-20.md and docs/platform/MASTER_PROMPT.md
first. They contain verified findings with file:line; do not re-derive them.

INVARIANTS — not negotiable:
- No fabricated data. Projections are COPIED from sources, scored and
  blended, never invented. A panel or answer with no data says why.
- Point-in-time: Warehouse.snapshot_at(deadline) is the only sanctioned
  read of mutable facts. LeakageError exists to stop backtests reading
  the future.
- Identity is the stable player `code`, never `element_id`.
- Money is integer tenths, never floats.
- DuckDB is single-writer XOR many-readers.
- Secrets in .env (gitignored). Never push without being asked.
- A test that has never failed is not a test. Break the code, watch it
  fail, restore it, and say so in the commit.

Work the phases in order. Do not start a later phase while an earlier one
is unfinished — each is a precondition for the next being measurable.

PHASE 0 — STOP THE BLEEDING (correctness; nothing else counts until done)
0.1 Historical availability is fabricated at read time. Fix
    ingest/injuries.py:136 and store/warehouse.py:206 so a NULL status is
    treated as UNKNOWN, not as "available, p=1.0". Decide explicitly what
    a backtest should do with unknown availability — the honest options
    are to exclude those rows, or to carry an explicit unknown state
    through the minutes model. Do NOT silently pick a default. Then state,
    in writing, how much this changed historical results.
0.2 Warehouse.read_copy() never cleans up. There are 457 orphaned temp
    database copies on this machine right now (5.1GB), growing once per
    DAG tick. Fix it in warehouse.py:379-402 — not per-caller — and
    delete the existing orphans.
0.3 dim_event is empty for every historical season (152 rows, all
    2026-27), so Snapshot.deadline() raises for 2022-26 and a backtest
    cannot ask when a historical deadline was. vaastav.ingest_season
    computes the calendar (build_calendar, :286) and discards it. Persist it.
0.4 Report sections vanish silently. interfaces/squad_section.py:25-26
    returns None with a comment falsely claiming it is declared as a gap.
    Adopt the contract that already exists in interfaces/dossier.py:101-107
    — a section must have a body or a gap, and raises otherwise — across
    the whole report layer.
0.5 Fix the UI bugs that discard real data: the price radar can never
    render (schema has no `rows`, renderTable requires it); every squad
    card shows £NaN (`price_tenths` vs `price`); fixture difficulty
    colouring is unreachable while its caption claims it is live; sorting
    a table deletes the provenance line. Add a frontend test — there is
    currently none of any kind, which is why all four survived.

PHASE 1 — WIRE WHAT ALREADY EXISTS
1.1 Make the rank objective reachable from a CLI command and the API.
    Today RANK_MV and the whole sim/ package are unreachable in
    production. A user must be able to run a rank-objective solve and see
    it differ from the points-objective solve.
1.2 Supply the `validator` that myteam/recommend.py:805 expects so paired
    simulation actually runs, and attach ΔP(top-10k) with a paired
    standard error to each candidate plan.
1.3 Give fact_odds_derived a reader. 1,720 rows of derived clean-sheet,
    xG-share and team-lambda priors currently feed nothing. Also move its
    PIT_KEYS registration out of an import-time side effect
    (ingest/odds_derived.py:145-147) — Snapshot.table() on it currently
    raises KeyError in any process that has not imported that module.
1.4 Wire a PointsForecast into the weekly report's transfer section. It
    currently reports "no points forecast is configured" while the squad
    section renders a full plan from a cached artefact — the two halves
    read different sources of truth.
1.5 Delete or explicitly quarantine the orphaned packages (strategies/,
    models/ensemble/, and the 14 zero-reach modules). Dead code that
    looks live is how the audit findings above came to exist. If a module
    is kept as research, say so in its docstring.

PHASE 2 — THE EDGE NOBODY SHIPS
READ THIS FIRST — it invalidates the obvious approach:

  Your rank-relevant contribution from player i is p_i*x_i - p_i*o_i,
  where o_i is ownership and x_i is your holding. The ownership term is
  CONSTANT in your decision variables. Therefore EO-adjusted expected-
  points maximisation has the IDENTICAL argmax to plain expected-points
  maximisation. Bolting an ownership term onto a linear objective is a
  no-op. EO can only enter through VARIANCE, or through the probability
  functional itself. (alpscode.com/blog/optimal-in-fpl/)

  This means FPL Review's EV x EO "Risk Position" — the only rank-aware
  term shipped by anyone — cannot be doing what its name suggests unless
  it acts on variance. Do not copy it uncritically.

2.1 BUILD THE ACTUAL OBJECTIVE: maximise P(final_points >= threshold_10k).
    Two published DFS papers are the blueprint and neither has been
    applied to FPL:
    - Hunter, Vielma & Zaman, arXiv:1604.01455. Maximises
      P(union of {X_i >= t}). Two ideas transfer directly: (a) induce
      variance STRUCTURALLY via stacking constraints rather than a
      quadratic penalty, which keeps the model a MILP; (b) diversify with
      a LINEAR overlap cut against each previously built lineup. They won
      real money with forecast R^2 of only 0.024-0.086 — the edge was
      portfolio construction, not forecasting.
    - Haugh & Singal, Management Science 67(1) 2021. Models the OPPONENT
      FIELD explicitly: Dirichlet-multinomial over opponents' selections
      with copulas for cross-position dependence, then Monte-Carlo. Your
      P(top-10k) is their formulation with a 0.14% quantile threshold.
    Note the ceiling: only ~6.5% of top-10k finishers repeat, and even
    world-class skill converts at roughly 50-55%. JUDGE THE ENGINE ON
    CALIBRATION, NOT OUTCOMES. A season that misses top-10k is not
    evidence the model is wrong.
2.2 EO AS A COVARIANCE STRUCTURE, NOT A SCALAR — this is the mechanism
    by which 2.1 actually bites. Owning 9/11 of the template plus two
    differentials is a completely different rank distribution from 11
    mid-EO players with identical total EV. Build joint ownership from
    real top-10k picks (Phase 3.2) and simulate rival scores as
    CORRELATED draws. LiveFPL publishes ownership COMBINATIONS, not just
    marginals — that is the right input and nobody models it.
2.3 STATE-DEPENDENT RISK, derived not configured. Let z = shortfall to
    the top-10k threshold divided by the SD of your remaining-season
    score. z < -0.5 (ahead): converge to template, minimise tracking
    error. |z| <= 0.5: plain EV. z > +0.5: raise variance monotonically
    in z. The key term is that the SD shrinks as weeks remain falls, so
    the same deficit demands MORE variance later — the formal version of
    "punts in the last 10 weeks". Your rank_objectives.md already has the
    sufficient statistic (D, tau) and D* ~ -1.06*tau; reconcile the two
    parameterisations and wire it.
2.4 CHIPS: CONSTRUCTION BEATS TIMING, and the field's chip calendar
    beats both. Measured (PLOS ONE, n~900k): in the SAME chip in the SAME
    gameweek, strong managers returned 23.2 vs 13.8 points — a 68% edge
    from squad construction alone. Separately, 79.4% of top managers used
    BB in the key DGW vs 28.9% of the field. But crowding matters: an
    FFScout survey (n~3,700) has 97.5% of managers planning Bench Boost
    in GW1-2 this season, which makes an early BB close to rank-neutral
    and moves the entire differential to bench QUALITY. Value a chip as
    E[return | your squad] - E[return | cohort's squad], never as a flat
    prior. Hard constraint: the first chip set expires at the GW19
    deadline with no carryover.
2.5 DISAGREEMENT AS THE VARIANCE ESTIMATE. You ingest 5 projection
    sources. Their disagreement is a better uncertainty signal than any
    single vendor's internal one — and it is free. Note projection_weight
    is correctly 0 rows until GW1 resolves; build the calibration loop
    that fills it, then the blend, in that order.
2.6 TOP-N DISTINCT PLANS, not one optimum. Near-optimal plans differ
    hugely in rank variance; you need the frontier to choose among them.
    (FPL Review ships this as "Solve Lines"; the no-good-cut enumeration
    is already substantially built in fpl_edge/rank/.) Every recent
    champion reports overriding tools with judgement — so output RANKED
    CANDIDATES WITH REASONS, not a single prescription.
2.7 MINUTES ARE THE MEASURED COMMERCIAL EDGE. OpenFPL (arXiv:2508.09992)
    matches FPL Review on Tickers and Haulers but loses clearly on Zeros
    (RMSE 0.818 vs 0.689) and Blanks. The paper's own explanation is that
    FPL Review has expected-minutes derived from team news and odds while
    OpenFPL uses only FPL availability tags. The entire measured edge of
    the best commercial model is knowing who starts. Multiply xPts by
    P(start) explicitly — a 6.0 xPts player at 60% start probability is a
    3.6 xPts asset.
2.8 CHEAP MEASUREMENTS NOBODY HAS PUBLISHED, all computable from the
    public API, each worth more than another model tweak:
    - The DISTRIBUTION of chip returns. Every published figure is a mean;
      for a tail objective the percentiles and skew are the whole point.
    - How much the top-10k points bar RISES in heavy-chip gameweeks.
    - FT value under the 5-transfer banking rule. The industry constant
      (1.5-1.75) is unvalidated and predates the rule, which mechanically
      raises roll option value.
    - Any empirical hit threshold at all. No distribution of realised hit
      outcomes exists anywhere. Then build the rank-space version: a -4
      that raises variance can be rank-positive while points-negative.
    - Team value causality: regress final rank on team value CONTROLLING
      for points-to-date. One regression, never run, settles whether it
      is a lever or an artefact. Current evidence says overrated — the
      elite gap is only ~£1.5m, worth ~33 points.

PHASE 3 — DATA THAT BUYS RANK
3.1 CONFIRMED LINEUPS AT T-60m — the highest-latency edge in FPL, and the
    engine has NO source. VERIFIED THIS SESSION: the Premier League's own
    Pulselive API serves them free and unauthenticated.
      GET https://footballapi.pulselive.com/football/fixtures/{id}
      headers: Origin/Referer https://www.premierleague.com
    Confirmed on a completed fixture: teamLists has 2 sides, each with an
    11-player lineup, 9 substitutes, and a formation label ("4-2-3-1").
    Each player carries name.display, a PL id, altIds.opta (e.g.
    "p231416"), and a birth date for disambiguation. Note `playerId` on
    the lineup entry was 0 — use the nested player `id` instead.
    UNVERIFIED and worth measuring first: exactly when teamLists flips
    non-null before kickoff. Poll one fixture from T-120m and log it.
    There is no bulk endpoint (/football/teamlists returns 404), so poll
    per fixture. The T-90m DAG task already exists and honestly records
    no_source; a test proves it wakes when a feed lands.
3.2 TOP-10K OWNERSHIP by crawling. There is no API. leagues-classic/314
    (the Overall league) paginates 50 entries/page, so top-10k is 200
    pages to enumerate entry ids, then 10,000 entry/{id}/event/{n}/picks/
    calls per gameweek — roughly 3 hours at a polite 1 req/sec. Key
    scheduling insight: ranks from GW n-1 define the top-10k for GW n, so
    enumerate ids BEFORE the deadline and fetch only picks after it. This
    unlocks 2.1, 2.3 and 2.4 — it is the single highest-value ingestion
    project.
3.3 FPL's OWN PRICE PREDICTOR. Announced for 2026/27: rise/drop progress
    as a percentage for every player, refreshed every 15 minutes, live
    after the GW1 deadline. api/price-changes/ returns 404 today. Capture
    the real endpoint from the network tab once the page ships, and drop
    third-party predictors to fallback.
3.4 SET-PIECE NOTES. api/team/set-piece-notes/ is live and valid today
    but every team carries a placeholder. Poll daily and diff — it is the
    only official set-piece source.
3.5 LIVE BONUS. 2026/27 adds projected bonus from minute 20, adjusting
    during the match. If it surfaces in event/{n}/live/, live-bonus
    modelling becomes free.
3.6 xG WITH A CLEAN LICENCE. fbref (403/Cloudflare) and understat
    (Disallow: /) are correctly rejected and nothing found changes that;
    vaastav's understat mirror does not launder it. Real options:
    olbauday/FPL-Core-Insights (free, 2026/27, refreshed twice daily,
    keyed on official FPL element ids, but NO formal licence file —
    record the ambiguity), or paid and contractually clean (Sportmonks xG
    add-on EUR24/mo on top of EUR29/mo; API-Football $19/mo).
    DO NOT INGEST SofaScore — its ToS bans automated extraction outright,
    including via paid intermediaries like Apify.

PHASE 4 — THE UI
Only after Phase 0.5. Multi-view: a transfer planner in the fplreview
idiom (rows = players, columns = gameweeks, editable assumptions inline,
solver returns a multi-GW path and N distinct plans); xPoints with source
comparison and the disagreement spread; top-10k template and EO by tier;
fixtures with real difficulty; a solver view; and Argus-style chat.
Keep probability-of-appearing as a SEPARATE column from EV rather than
silently multiplying them — FPLForm does this deliberately and it lets
the rank layer treat minutes risk and scoring risk with different
variance penalties.
Load the `dataviz` skill and RUN its palette validator. Verify in a
browser and screenshot every view; do not ask the user to check.
```

---

## Appendix — evidence

**Market position.** The only rank-aware objective term shipped anywhere is FPL
Review's Risk Position (`risk ≈ EV × EO`), documented at
docs.fplreview.com/the-model/solvers/settings/. The best public write-up of
rank-optimal vs points-optimal is alpscode.com/blog/optimal-in-fpl/, which
formalises net benefit as `p·x − p·o` and proposes a weighted-sum multiobjective
`w·E[Gain] − (1−w)·E[Loss]` where `w = 0.5` reproduces the points-optimal
solution — a working v1 of a rank objective in a day. `open-fpl-solver`
(ex-sertalpbilal, now Apache-2.0, HiGHS-based, bring-your-own-CSV) is the
natural chassis if you ever want to swap solvers.

**Prices, where verified.** FFScout £10/mo or £50/yr (Mega Bundle £100/yr,
includes ad-free LiveFPL). Fantasy Football Fix £2.57–£2.94/mo, £295 lifetime.
FPL Review requires an active Patreon, base tier $5/mo. FPLForm is free and
exports CSV. LiveFPL is free (10 saved plans with an account).

**Six market gaps a private tool wins**, because a commercial product cannot
serve a 2M-rank user and a 500th-rank user with the same defaults, and must
return in seconds: nobody optimises P(rank ≤ N); EO is never a covariance
matrix; chip timing ignores the field's chip supply; nobody ensembles competing
paid+free projections; distributions are point estimates almost everywhere; and
nothing is state-dependent across the season.

**What is genuinely strong in the codebase** and should not be disturbed:
`PIT_KEYS` + `Snapshot.table()`'s `ROW_NUMBER() … WHERE as_of <= ?`;
`Snapshot.warehouse` raising `LeakageError` with a ≥20-char escape hatch (three
production uses); `append()` as the sole write path with tz-awareness checked
*before* `pd.to_datetime`; `ConflictingFactError` on contradictory replays;
semantically differentiated timestamps (points finalise 09:00 UK the day after
the last match; fixtures get two rows, schedule at deadline and result at
kickoff+2h); 100% identity resolution with zero conflicts across four seasons.

**Measured rank levers, in effect-size order.** Strong evidence: chip
*construction* (+9.4 pts on the same chip in the same gameweek, PLOS ONE
n≈900k); chip deployment in the right DGW (79.4% of top managers vs 28.9%);
expected-minutes accuracy (the sole measured edge of the best commercial model
over the best open one); captaincy (21–29% of a champion's points, but the
elite-vs-elite edge is only ~25 pts/season and comes from avoiding disasters,
not hitting hauls); not taking hits (elite ≈0.1 pts/GW vs 0.6 for top-1M);
team value (+21.8 pts per £1m but R²=0.169 and mostly reverse-causal).
Everything in Phase 2.1–2.3 is *inferred* — mechanism sound, magnitude
unmeasured in FPL. That is precisely why it is unoccupied ground.

**Convergent elite behaviour**, across multiple independent top managers:
near-zero hits except in blank/double gameweeks and GW38; transfers made late
on Friday/Saturday after team news, explicitly trading price movement for
information (74.3% of players the top-50 sold were fully available, so these
are planned upgrades, not injury reactions); high-EV near-template captaincy;
a template-heavy core differentiated by *timing of entry* rather than by owning
off-template players; a 5–6 gameweek horizon with an explicit fixed/flexible
squad split; and deliberate decision-fatigue management.

**Two cautions on the evidence.** No overall winner in the last five seasons
reports using a solver — but winners are a maximum-variance sample of one draw
from ~9M, so discount this heavily; the relevant reference class is *consistent*
finishers. And treat differential-captaincy-by-rank rules with suspicion: every
circulating "captain below X% EO when rank is Y" figure traces to uncited
AI-generated content, and the recent champions' stated practice contradicts it.

**Simulation trap worth guarding against:** Joshua Bull's random-squad generator
made 5-4-1 look strong purely because a £4.0m defender has fewer price rungs
below the premium ceiling than a £4.0m forward. Any simulation-based engine
needs a check for sampler bias masquerading as football insight.

**Access gaps in the research:** reddit.com blocked every agent, so r/FantasyPL
data posts are unchecked; fplreview.com's main domain 403s (docs subdomain
works); the Wiley-gated Mlčoch et al. 2024 generative-opponent-model paper was
not read. Worth a human pass.
