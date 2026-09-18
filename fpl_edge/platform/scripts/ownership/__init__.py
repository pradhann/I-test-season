"""ownership_eo — the template and effective-ownership panel.

Effective ownership (EO) is the share of a cohort that *effectively* holds a
player once captaincy is counted: a player owned by 60% and captained by half
of them has 90% EO, so values above 100% are normal for premium captains.
Beside every EO metric this panel keeps FPL's own **marginal** ownership
(``selected_by_pct`` — no captaincy weighting) so the two are never confused.

Two data traps this script exists to not fall into (docs/platform/
data_audit.md Q6, both fired during the audit):

1. **Metric names.** The feed writes ``eo_predicted`` / ``eo_top10k`` /
   ``eo_elite`` — NOT the ``own_*`` names an old migration comment documents.
   A filter on the documented names returns zero rows silently.
2. **Season/gw split.** ``eo_top10k`` and ``eo_elite`` currently exist only
   under LiveFPL's last-resolved cohort — season 2025-26, GW38 — while
   ``eo_predicted`` is under the current season. A single "current season and
   gw" join silently drops two of the three metrics. Here, metrics from any
   *other* season are quarantined into ``last_season`` with their real
   season/gw stamped on them, and are never merged into the current rows.

Crawl data (``fact_manager_pick``) is used when it holds picks for the
requested season: the panel then adds observed cohort own%/EO% and states the
cohort size. When the pick tables are empty — the state at build time — the
columns stay null and ``cohort_note`` says exactly what is on file instead.

3. **The cohort denominator.** This script used to compute its own own%/EO%
   with a raw query over ``fact_manager_pick`` that had **no cohort filter**:
   its denominator was every crawled entry (1,508 in the live warehouse —
   top1k and elite blended), and it labelled the answer "elite". It now reads
   ``sem_elite_ownership``, the one place effective ownership is defined, and
   names the cohort it is reporting in ``cohort`` / ``cohort_note``. The
   ``elite_*`` row keys keep their names (the web view selects on them) and
   carry whatever cohort ``cohort`` names — ``elite`` by default.

THE FIELD LADDER (``fields`` + ``rows[].fields``)
-------------------------------------------------
The objective this engine optimises is P(top-1k), not expected points, and
(docs/platform/rank_objectives.md §0, §1)

    rank move ≈ Σ over players of (my multiplier − the field's EO) × points

so template holdings *cancel*. What a reader needs is therefore never one
ownership number: it is the **gap** between the field they are racing and the
game as a whole. This panel enumerates every field it can actually measure —
FPL's own marginal ownership, each external LiveFPL EO series, and each crawled
cohort — as ``fields``, and hangs the per-player measurements off
``rows[].fields[<key>]``. The UI picks which field to compare against which
baseline; the panel never picks for it and never mixes units.

Three honesty rules the ladder exists to keep:

* **Like is compared with like.** ``own`` (a head-count share) and ``eo``
  (a sum of FPL multipliers) are separate measures on every field. A field
  that cannot supply one leaves it ``null`` — never zero, never the other one.
* **Every percent names its denominator.** ``denominator`` on each field says
  in words what the number is a share *of*, and ``n`` gives the count where a
  count exists. FPL does not publish its entry count, so ``global`` carries
  ``n: null`` rather than a plausible-looking total.
* **A re-stamped feed is not a new gameweek.** LiveFPL republishes
  ``eo_top10k``/``eo_elite`` under the upcoming gw with byte-identical values
  (measured live: 600 of 600 codes unchanged from GW1 to GW2 on 2026-08-27).
  ``same_values_as_gw`` reports that — measured, not assumed — so the UI cannot
  caption last week's settled field as this week's forecast.

Note the naming collision the ladder has to survive: LiveFPL's ``eo_elite`` is
*LiveFPL's own* elite definition and is unrelated to this repo's crawled
``elite`` cohort. Both are carried; each field's ``label`` and ``provider``
keep them apart.

Cohort composition is disclosed for the same reason (``fields[].composition``):
the live elite cohort is 311 managers, and 49 of them are the owner's own
mini-league opponents. A cohort with a conflict of interest in it is still
usable — an undisclosed one is not.

SELECTABLE SUB-COHORTS (``segments`` + ``selection`` + ``fields[selected]``)
---------------------------------------------------------------------------
Disclosure was the first step; choice is the second. ``cohort:elite`` is ONE
aggregate of managers found by six different crawls, and they are not the same
evidence: a curated list of 250, twelve past overall winners, eight named
managers, forty-nine of the owner's own league-mates, and — recorded as not
salvageable in ``docs/platform/PANEL_LEDGER.md`` — a snowball pool built from
seed IDs that no longer identify anyone. The ``segments`` param names which of
those sets compose the field, and every measurement here is recomputed over
the UNION of the chosen ones.

Three rules that union has to keep:

* **The denominator is DISTINCT managers with a stored squad.** The sets
  overlap: on the live warehouse the default selection is 270 set memberships
  over 262 distinct managers, and eight entries carry two tags. A sum of set
  sizes is a denominator nobody is in.
* **An untrustworthy set is flagged, never merely omitted.** ``snowball``
  ships with ``trusted: false`` and the ledger's reason attached, and is in no
  default. A missing checkbox teaches a reader nothing; a labelled one teaches
  him why he should not click it.
* **The default is explicit.** ``selection.default`` and
  ``selection.is_default`` are in the payload, because "which managers am I
  being compared against" is the first thing a reader of this panel needs and
  a default that lives only in a schema is invisible to him.

THREE DERIVED VIEWS, ALL FROM THE SAME MEASUREMENTS
---------------------------------------------------
``diff`` is the rank identity per player: my multiplier and the field's EO in
the SAME units (I am one manager, so my EO on a player is 100 × my multiplier)
with the term itself beside them. It is built from the UNION of my squad and
the field's rows — a player I own whom the field does not is the single most
important row on the page, and taking only the field's top rows would delete
it. Where the field owns a player zero times that is a MEASURED zero over an
enumerated set of squads, not a missing value, and it is served as such.

``whatif`` carries every current-season player's field measurement so the UI
can recompute exposure for a hypothetical squad without another round trip,
and states plainly which quantities that covers (mine, and any difference of
mine and the field's) and which it does not (the field itself, under a
different segment selection or gameweek).

``momentum`` is per-gameweek EO for the selected field, and today it is
genuinely empty: a squad becomes public at its deadline and only GW1 squads
are stored, so ``available`` is false with the reason and the next deadline,
and the series is absent rather than a single point a reader would see as a
flat line.

Cohort-vs-cohort comparison needs no new surface: every row already carries
every field's measurement of that player at one instant under
``rows[].fields``, so a UI reads two keys out of one payload. A second call
per cohort would let the two halves of a comparison drift to different
``as_of`` instants — the bug it would exist to cause.
"""

from __future__ import annotations

# Importing panel is what registers the panel script, exactly as importing the
# single ownership.py module used to.
from fpl_edge.platform.scripts.ownership.panel import ownership_eo
from fpl_edge.platform.scripts.ownership.schema import (  # noqa: F401
    _DIFF_ROW,
    PARAMS_SCHEMA,
    RESULT_SCHEMA,
)

#: Every name a caller outside this package reads off `scripts.ownership`:
#:   ownership_eo    fpl_edge/platform/scripts/brief/tiles.py:29
#:   RESULT_SCHEMA   tests/unit/test_ownership_panel.py:1257
#:   _DIFF_ROW       tests/unit/test_ownership_panel.py:1271
#: PARAMS_SCHEMA is the panel's other public constant. No phase builder is
#: re-exported: they are private to the assembler.
__all__ = ["PARAMS_SCHEMA", "RESULT_SCHEMA", "ownership_eo"]
