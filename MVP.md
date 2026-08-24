# Your FPL tool — what it is and how to run it

Everything below was run and verified against the live warehouse on
2026-08-24. Three commands are the tool; everything else supports them.

## The weekly loop (this is the product)

```bash
uv run fpl solve
```
Fits the models, solves the coming 5 gameweeks in BOTH objectives —
expected points and rank (P(top-10k)) — prints the squads and their diff,
and commits the plan + forecast artefacts. Run live for GW2 the objectives
disagreed: 5 of 15 players differ and the captain flips. Takes ~10-15 min
at the default 300s solver limit. `--mode rank|points|both`, `--deficit`
to assert how far behind the top-10k pace you are.

```bash
uv run fpl weekly
```
The decision report: your real squad (live from FPL), the recommended
squad from the last solve, the exact transfer diff paired by position, a
ranked transfer recommendation with every alternative it beat, and what
data is missing. If a recommendation plays a chip it SAYS SO in capitals.
Add `--validate` to attach ΔP(top-10k) with paired standard errors to the
shortlist (builds the simulator; minutes, not seconds).

```bash
uv run fpl myteam auth
```
Verifies your FPL session and auto-refreshes the 8-hour token. One cookie
paste lasts ~6 months: `pbpaste | uv run fpl myteam auth --paste-cookie`
(never type it at a prompt — the terminal truncates at 1024 chars).

## The surfaces

| Surface | Command | State |
|---|---|---|
| Dashboard | `uv run fpl platform serve` → localhost:8321 | 6 panels, verified in a browser: squad pitch with real prices, projections, fixtures, price radar (risers/fallers), market watch (bookmaker clean-sheet probs with cross-method spread), ideas. Live deadline clock from the API. |
| Telegram | @fplpradhannbot (deployed via launchd) | review my team, transfers, projections, fixtures, YouTube link analysis, idea tracking |
| Deadline DAG | `make dag-status` (deployed) | fires off the real deadline: T-30h refresh, nightly price radar, T-4h solve delivery, T-90m lineup check |

## What the engine actually does

- **Copies projections, never invents them.** 5 sources ingested; their
  disagreement is the uncertainty estimate. `projection_weight` stays
  empty until GW1 actuals score them — weighting without evidence is
  fabrication.
- **Optimises rank, not points.** RANK_MV prices variance and ownership:
  behind the pace → variance is a good; ahead → it is a cost. Evidence
  assembled from measured data (4 seasons of scoring variance, FPL's own
  ownership, external EO where the feed exists) with provenance printed.
- **Point-in-time everywhere.** A backtest cannot read the future
  (`LeakageError`), cannot pick players whose availability was never
  recorded (`UnknownAvailabilityError` — opt in explicitly), and can ask
  for any historical deadline (all 5 seasons in `dim_event`).
- **Honest about gaps.** A report section with nothing to say says so; a
  panel with no data explains why; a recommendation that needs your
  wildcard declares it.

## Known limits (the honest list)

- The rank solve's deficit is an assumption until a top-10k pace series
  is ingested (pass `--deficit`); captaincy shares are zero without the
  LiveFPL EO feed for the target GW.
- No confirmed-lineup source yet. The Pulselive API is verified to carry
  free lineups (see docs/platform/MASTER_PROMPT.md Phase 3.1) — the
  highest-value next ingest.
- No top-10k ownership crawl yet (Phase 3.2) — unlocks EO-by-tier and
  chip-crowding.
- Historical backtests exclude unknown-availability players unless you
  opt in; the season replay baseline predates that and reads the raw pool.

## Where the plans live

`docs/platform/MASTER_PROMPT.md` — the phased build plan, grounded in the
audits and market research. Phases 0 (correctness) and 1 (wiring) are
done; Phase 2 (the edge: EO as covariance, state-dependent risk) and
Phase 3 (lineups, top-10k crawl) are next.
`docs/platform/AUDIT_2026-08-20.md` — every finding, with file:line.
