# Dogfooding notes, GW6 (deadline 2026-10-10), started 2026-09-19 07:40 UTC, on 624dadf

## 1. Dashboard as the first read
- The page cannot answer its own question today: all four verdict rows are `stale`
  because the plan is 266 h old and the squad is GW5 public picks (FPL account not
  connected). A manager's first act is therefore "re-solve", which the transfer row's
  fold now offers. Good that the state is honest; bad that the default landing state
  three weeks before a deadline is "nothing to say". MISSING: an automatic re-solve
  once the previous deadline passes and the squad source refreshes (a scheduled
  task, not a button), so the page is never stale when opened.
- Useful at a glance: 2 FT, £0.0 bank, captain suggestion B.Fernandes over
  Gibbs-White by 0.91 xPts, João Pedro 75% flagged and benched, best XI 3-5-2 at
  46.4 xPts.
- BUG: the `solve_stale` alert reason still carries the dangling clause "a deadline
  has passed since, " (H5 fixed the solver block's copy, not the alert's).
- team_fixtures rows carry short_name and team_code (fine); my probe used the wrong key.

## 2. Horizon
- Projections (consensus of fplform, fplreview, airsenal) for GW6: Fernandes, Saka,
  Palmer, Gabriel, Haaland, Mbeumo, Gibbs-White, Szoboszlai. I own four of the top
  eight. Not owned and highly owned: Haaland 73.5%. With £0 bank and Isak 9.1 +
  João Pedro 7.8 = 16.9 to spend on two FWD slots, Haaland (15.6) plus a 4.5 FWD is
  20.1: unreachable this week without a hit or a third sale. That is the question
  the solver must answer; a manager wants to see that arithmetic on the page
  ("Haaland is 3.2 short"), not infer it.
- MISSING in the gw-mode projection payload: a horizon sum per player (the UI sums
  client-side from several calls); the API caller and the MCP tool get one GW.

## 3. The re-solve (07:45 UTC, 3 minutes, exit 0)
- Verdict after re-solve: transfer Szoboszlai (7.0) to Barnes (NEW, 6.0, 4.5% owned),
  gain over roll 8.0 xPts across GW6-10, 0 hits; captain Barnes, +1.75 over Fernandes;
  bench 2 swaps +1.4; chip hold. Unconstrained alternative: 4 moves, 3 hits (-12) for
  +27.5, selling Fernandes for Haaland.
- SEVERE: the plan was solved on the ENGINE forecast alone (transfer_plan.json:
  forecast_source engine, 2,390 rows, engine fill share 0.0). Engine GW6: Barnes 5.84,
  Fernandes 5.34, Haaland 5.89. Every provider: Fernandes 5.7 to 6.35, Barnes 3.0 to
  4.2, Haaland 4.7 to 5.4. The dashboard shows the consensus everywhere else, so the
  page now recommends captaining a 4.5%-owned midfielder the consensus ranks 30th,
  with no visible reason. The house rule (notes, 2026-09-07) is to solve against the
  consensus the owner sees and use the engine only to fill uncovered players,
  labelled. Either the runner's default flipped or the consensus artefact was absent.
  This is the single most damaging defect a manager would hit today.
- The captain row shows "+1.75" and nothing about which forecast produced it. The
  row needs the source name and, where solver and consensus disagree on the captain,
  both numbers side by side (the review's B design had this as "solver vs consensus").
- `moves` at the payload's top level is empty while the transfer row carries the
  move: two fields for one fact.
- My earlier "dangling clause" note was an artefact of my own 80-character print;
  the sentence is whole. routes_solve.py:268 can still print "GW None" when the
  calendar lacks last_gw; guard it.

## 4. Ownership / effective ownership
- The elite-cohort EO view is the right lens (Palmer 201% EO among 344 elite managers
  and I do not own him; Haaland 86%; João Pedro 106%, whom I own and who is doubtful).
- Retracted: my first probe passed coverage=false (which skips the squad read) and
  matched the wrong "Pedro". With the UI's defaults the panel marks all 15 of mine
  (João Pedro 100 percent) and the exposure gaps read Palmer 201 EO, João Pedro 106
  (owned, doubtful), Haaland 86, Konsa 69, Groß 52. selection.includes_you is
  honestly False: the field's squads are GW4, mine GW6, and the note says so.

## 5. Chat as the manager's analyst (question: best 2 FT, no hit, captain)
- WORKING: the agent read panels first, said which failed, corrected two of my
  premises with numbers (Palmer 181 percent EO at GW4 not 201; Haaland 91 EO in the
  elite cohort), laid out João Pedro's four-source disagreement including
  premierinjuries at p_appear 0.00, produced a two-leg plan with GW6 and GW6-9
  arithmetic, a chart, the budget with the sell-price rule, the 3-per-club check, and
  a captain table with spreads. That is the analyst a top manager wants.
- BROKEN: mcp tools brief, my_squad and transfer_plan fail with ParamsInvalid
  ("entry_id was unexpected"): the MCP layer passes entry_id to panels that no
  longer accept it after the multi-user change. Consequence: the agent fell back to
  sem_manager_picks at the GW4 lock and planned around Cherki, whom I sold for
  Rogers before GW5. A stale squad silently poisons every downstream number; this
  must fail loudly to the user, and the tools must pass the context, not a param.
- The agent's SQL fallback is a strength and a risk: the panels are the only data
  path for a reason. When a panel raises, the answer should carry a visible banner.

## 6. Forecast artefact
- cli/solve.py:225 writes forecast.parquet under the repo root regardless of --db, so
  any test that reaches the commit path rewrites the LIVE forecast (07:31 UTC today,
  two minutes into a gate run, engine-only, no ledger row). Readers must follow the
  run's db directory, as the house rule says for logs.

## 7. After refreshing the consensus forecast and re-solving (07:57, 3 min)
- Plan on the consensus currency: Rogers (CHE 7.7) out, E.Le Fée (SUN) in, +14.35
  over GW6-10, no hit; bench 2 swaps +1.4; chip hold. That is a defensible move a
  manager can check against the projection table.
- BUG (captain row): the plan's captain is João Pedro (code 475168, 3.99 xPts, 75
  percent doubtful, benched by the same page's bench row) yet the row prints
  "+1.75", which is the consensus delta for B.Fernandes read from suggested_xi. Two
  sources in one row; the number does not belong to the name.
- The solver's captain choice itself is suspect: one captain code for a five
  gameweek horizon, landing on a doubtful player the consensus ranks below four of
  my own starters. Either the MILP's captain is per horizon rather than per
  gameweek, or the appearance probability is not applied to the captain doubling.
  A manager would never take that pick without seeing why.
- transfer_plan.json's alternatives are four empty stubs (label "", 0 hits, 0 gain);
  the earlier engine plan carried a real 4-move unconstrained alternative.
- Fixed in this pass: forecast.parquet, transfer_plan.json and gw1_plan.json are
  written beside the warehouse the run was given, never the repo root; recommend
  reads the same place. The MCP squad, brief and transfer_plan tools no longer pass
  entry_id and go through the user context.

## 8. Captain row after the fix
- The row now carries the truth: solver pick João Pedro 3.99 xPts, my captain
  Gibbs-White 4.39 (delta -0.40), consensus captain B.Fernandes 6.14. The MILP picks
  one captain per gameweek (one_captain_{j}) on the rank_mv objective, which credits
  variance against the field's captaincy share; on the consensus currency that
  still lands on a doubtful player the consensus ranks below four of my starters.
  DECISION NEEDED: when the solver's captain trails the consensus captain by more
  than the divergence gate (1.5 here: 6.14 - 3.99 = 2.15), the row should lead with
  the consensus captain and show the solver's pick as the dissent, not the reverse.
  Today the rule precedence is solver first. home.js must also render the two new
  numbers; today it prints only the delta.

## 9. Pipelines as "is my data fresh?"
- The board answers the question: 12 ok, 3 failing, 1 stale; next-due per task; the
  two crawl failures are FPL's API returning 503 on /api/entry during yesterday's
  catch-up and will clear on the next firing. WORKING.
- GAPS: the row for content_analyse shows model None and tokens None although its
  ledger note ends with C1's `spend={...}` line: the panel parses a note only when the
  whole note is JSON (F built it before C1's format landed). last_run_age_days is
  served with 15 decimals; the panel should round. Deadline-relative tasks
  (final_solve_delivery, lineup_captain_check, presser_projection_refresh) read as
  "stale 17 days" between deadlines, which is true by their window but reads as
  alarm; the stale flag should not fire on a deadline-relative task whose next due
  is in the future.
- briefing_intel errored at 06:45 UTC: "model call timed out after 240s" (yesterday's
  run took 178 s at the same model). The limit is one bad minute away from the
  normal case; raise it or trim the panel context it sends.

## 10. Chat, second run, with the squad tools fixed
- WORKING: read my_squad and brief, noticed the squad differed from its cached
  briefing, corrected the free-transfer premise (panel says 1), refused the ambiguous
  "João Pedro" until named, and produced Rogers to Saka plus Ødegaard to Gomez
  (+8.43 XI xPts over GW6-10 by its own formation-legal enumeration; three fallback
  routes priced; sell-price caveat; the 3-per-club check), captain B.Fernandes 6.14
  with spreads and fixture ranks. It compared the solver's single move honestly
  (+0.88 on the XI total by its measure).
- GAP: the transfer_plan and projections tools exceeded the MCP payload cap, so the
  agent went to SQL for the consensus. Those two tools need a compact mode (a
  player filter, a top-N, or a horizon sum per player) so the panel path is the one
  the agent takes.
- GAP: free transfers read 2 on the dashboard header (squad_overview) and 1 in the
  solver plan; the runner reconstructs the count. One truth with provenance, and
  the authenticated read (fpl myteam auth) is the owner's step that resolves it.
- The solver's "gain over roll" (14.35) and the agent's "XI total gain" (+0.88 for
  the same move) are different measures of one move. The dashboard prints the
  first without saying what it is; the row needs the unit ("xPts over GW6-10 versus
  rolling the transfer, in the solver's currency").

## What worked, in one list
The dashboard's honesty about staleness; the re-solve fold; the fixtures board;
the projections consensus with FPLReview inside it; the elite-cohort EO view; the
pipelines board; the chat analyst once its tools work; the MCP envelope's refusals
and gaps (the ambiguity refusal in particular).

## What was missing, ranked
1. Solving on the engine currency because a test rewrote the live forecast (fixed).
2. Three MCP squad tools failing on a stale param (fixed) and the fallback hiding it.
3. The captain row mixing sources (fixed) and the solver captaining a doubtful player.
4. No automatic re-solve after a deadline, so the page opens stale.
5. Payload caps on the two tools an analyst needs most.
6. The FT count disagreement and the unconnected FPL account behind it.
7. Pipelines: spend line unparsed, deadline-relative tasks read as stale.
8. Horizon sums missing from the projection API.
