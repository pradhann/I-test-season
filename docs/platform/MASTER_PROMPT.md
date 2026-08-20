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
Ranked by (market gap) x (effect on rank). Each is something no
competitor does, verified by market research.
2.1 EO AS A COVARIANCE STRUCTURE, NOT A SCALAR. Every tool treats
    effective ownership as a per-player number. Owning 9/11 of the
    template plus two differentials is a completely different rank
    distribution from 11 mid-EO players with identical total EV. Build
    joint ownership from real top-10k picks and simulate rival scores as
    CORRELATED draws. LiveFPL's combination-ownership widget is the only
    hint anyone has of this, and it is a lookup, not a model input.
2.2 STATE-DEPENDENT RISK. The right risk in GW5 at rank 400k is not the
    right risk in GW32 at rank 12k. Every tool's risk setting is a static
    user preference. Yours knows rank trajectory, chips remaining, and
    weeks left, so risk appetite should be DERIVED, not configured. Your
    rank_objectives.md already has the sufficient statistic (D, tau) and
    the boundary D* ~ -1.06*tau. Wire it.
2.3 EO SEGMENTED BY RANK TIER. Top-10k EO and overall EO diverge sharply.
    Optimising against overall ownership is optimising against the wrong
    opponent. This requires Phase 3.2.
2.4 CHIP TIMING AGAINST THE FIELD'S CHIP SUPPLY. A Bench Boost is worth
    far more when 60% of the top-10k has already burned theirs. Nobody
    models the remaining-chip distribution. Your own simulation already
    found cohort chip usage dominates your own chip choice.
2.5 DISAGREEMENT AS THE VARIANCE ESTIMATE. You ingest 5 projection
    sources. Their disagreement is a better uncertainty signal than any
    single vendor's internal one — and it is free. Note projection_weight
    is correctly 0 rows until GW1 resolves; build the calibration loop
    that fills it, then the blend, in that order.
2.6 TOP-N DISTINCT PLANS, not one optimum. Near-optimal plans differ
    hugely in rank variance; you need the frontier to choose among them.
    (FPL Review ships this as "Solve Lines"; the no-good-cut enumeration
    is already substantially built in fpl_edge/rank/.)

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

**Still outstanding:** a research track on measured rank levers — what
quantitatively separates top-10k finishers, captaincy's share of rank variance,
hit thresholds, and the real value of team value — was still running when this
was written. Treat the Phase 2 ordering as evidence-informed but not
evidence-complete, and revisit once that lands.
