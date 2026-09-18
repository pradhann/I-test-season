"""dashboard_brief — the dashboard's aggregator, under the anti-drift contract.

THE CONTRACT (the ledger's "reader of the definition, never a second
implementation"): this panel only *selects and thresholds* numbers computed by
the same shared code its source panels use. Concretely, it CALLS the source
panel functions (``squad_overview``, ``price_radar``, ``ownership_eo``,
``fixture_board``) and the shared semantic views
(``sem_projection_consensus``, ``sem_players``) — it re-implements no metric,
no squad read, no flow window, no EO definition. Every item carries
``source_panel`` + ``source_as_of`` so drift is auditable, and a contract test
asserts the brief's numbers equal the source panel's numbers for the same key.

Thresholds are echoed in the payload — the view contains no magic numbers.
There is NO free-text recommendation field: wording lives in view templates
keyed by ``rule``/``kind``, so blended prose cannot be smuggled in
warehouse-side. Strings this panel does carry are either verbatim source
fields (FPL ``news``), threshold echoes (``gate``), or measured facts
(``watch_log[].detail``).

Four blocks serve the pitch and the move cards, all under the same contract:

* ``suggested_xi`` — the bench_inversion rule's pairwise swaps APPLIED as a
  renderable lineup plus the captain measures (both printed, never blended);
  every number is squad_overview's own. Bench/captain alert rows are gone:
  the fix happens in the squad, not in prose above it.
* ``team_fixtures`` / ``fixtures_scale`` — fixture_board's opponent_only lens
  and scale copied verbatim per club for the pitch's opponent chips.
* ``squad_projection`` — xmins/p_appear per squad player from the provider
  consensus (same semantic views and rounding as projection_table); nulls
  where no provider serves the column, never a fabricated minute.
* ``moves`` — the deterministic move rules (``coverage_gap``,
  ``form_upgrade``): rule ids + numbers only, thresholds echoed, capped and
  suppression-disclosed. No free text — templates live in the view.

Two assembly blocks answer R1's "the user is the only join", still under the
contract:

* ``verdict`` — one pick per question (transfer / captain / bench / chip) by
  the PRINTED deterministic precedence (:data:`PRECEDENCE`, echoed verbatim
  and schema-pinned with ``const``). Rule ids + structured refs/numbers
  only; every dissenting voice (mean-xPts captain, haul-odds captain,
  creator armband count, the rule moves when they differ from the solver)
  is served beside the pick as data, currencies never summed, each line
  with a drill ref to its evidence card.
* ``header`` — the free-transfer count and the chip verdict at top level
  (they are the budget and the gate of the whole decision), each with the
  solve state it was read under.

The solve block renders ``transfer_plan.json`` — the artefact ``fpl
recommend`` commits: the real transfer recommendation for the CURRENT 15
(free optimum vs roll vs screened candidate moves, one MILP and one
objective). Every number in it is the solver's own, in the currency
``objective_mode`` names (``expected_points`` surrogate today), and
``gain_over_roll`` is never summed or blended with consensus or market
numbers. A plan generated before the most recent deadline is a named gap
(state ``stale`` — priced against a squad you no longer have), never a
recommendation; a roll (zero transfers) IS a recommendation, flagged
``is_roll``, not an empty state.
"""

from __future__ import annotations

# Importing build is what registers the panel, exactly as importing the single
# brief.py module used to.
from fpl_edge.platform.scripts.brief.build import USER, dashboard_brief
from fpl_edge.platform.scripts.brief.schema import (
    PARAMS,
    PRECEDENCE,
    RESULT,
    THRESHOLDS,
    TRANSFER_PLAN_NAME,
)
from fpl_edge.platform.scripts.brief.tiles import best_legal_xi

#: Every name a caller outside this package reads off `scripts.brief`:
#:   THRESHOLDS      fpl_edge/platform/app/helpers.py:280
#:   PRECEDENCE      tests/unit/test_dashboard_brief.py:501
#:   best_legal_xi   tests/unit/test_dashboard_brief.py:1188,1217
#:   USER            tests/unit/test_dashboard_brief.py:1255 (read for the
#:                   entry id a fixture seeds; never patched here, so a
#:                   re-export cannot go inert)
#:   RESULT          tests/unit/test_dashboard_brief.py:1438
#: PARAMS and TRANSFER_PLAN_NAME are the panel's other two public constants.
#: No block builder is re-exported: they are private to the assembler.
__all__ = [
    "PARAMS",
    "PRECEDENCE",
    "RESULT",
    "THRESHOLDS",
    "TRANSFER_PLAN_NAME",
    "USER",
    "best_legal_xi",
    "dashboard_brief",
]
