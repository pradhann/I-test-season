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

import datetime as dt
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
)

UTC = dt.timezone.utc

#: The owner's chosen default field: the CURATED elite only. Explicit in the
#: payload (`selection.default`, `selection.is_default`) rather than implied by
#: the absence of a param, because "what am I being compared against" is the
#: first thing a reader of this panel needs to know and a default that only
#: exists in a schema is invisible to them.
DEFAULT_SEGMENTS: tuple[str, ...] = ("elite_list", "winner", "elite_named")

#: The one prefix rule that turns the crawl's free-text ``dim_manager.source``
#: into a segment name. This mirrors ``sem_manager_segment`` in
#: ``store/views.sql`` exactly, and the two are pinned equal by
#: ``test_the_panels_segment_union_equals_the_semantic_layer_macro``.
#:
#: Why the panel carries its own copy at all: ``Warehouse.read_copy`` opens the
#: file read-only, and views.sql is only applied on a WRITABLE open. A
#: warehouse file written before this macro shipped therefore does not contain
#: it, and a panel that hard-depended on it would go dark on exactly the
#: machine that had not re-ingested yet — the day before a deadline. The macro
#: is still the canonical definition for chat, `/api/query` and the MCP server;
#: the test is what stops the two drifting.
_SEGMENT_CASE = """
        CASE WHEN source LIKE 'top1k%'       THEN 'top1k'
             WHEN source LIKE 'winner%'      THEN 'winner'
             WHEN source LIKE 'mini_league%' THEN 'mini_league'
             WHEN source LIKE 'snowball%'    THEN 'snowball'
             WHEN source LIKE 'expert%'      THEN 'expert'
             WHEN source LIKE 'elite_list%'  THEN 'elite_list'
             WHEN source LIKE 'elite_named%' THEN 'elite_named'
             ELSE source END
"""

#: segment -> (label, trusted, untrusted_reason, caveat).
#:
#: ``trusted`` is a provenance judgement, not a data quality one: every one of
#: these entries is a real FPL entry with a real stored squad. What differs is
#: whether the *selection rule* that put them in the pool means anything.
#:
#: ``caveat`` is the weaker signal — a set that is measurable and honest but
#: whose reading needs a sentence beside it. A conflict of interest disclosed
#: is usable; undisclosed it is not.
_SEGMENT_META: dict[str, tuple[str, bool, str | None, str | None]] = {
    "elite_list": (
        "Curated elite list", True, None, None,
    ),
    "winner": (
        "Past overall winners", True, None,
        "One season's champion is one season's evidence; twelve managers is a "
        "coarse denominator (each is over 8 percentage points).",
    ),
    "elite_named": (
        "Individually named managers", True, None,
        "Hand-picked by name, so the selection rule is a person's judgement "
        "rather than a reproducible filter.",
    ),
    "mini_league": (
        "Your own mini-league opponents", True, None,
        "These are people you happen to play, not a selected elite — and your "
        "own entry is one of them, so selecting this set puts you inside the "
        "field you are measuring yourself against.",
    ),
    "expert": (
        "Public experts", True, None,
        "Named public creators; the pool is small and curated by reputation.",
    ),
    "top1k": (
        "Sampled from the overall top-1k standings", True, None,
        "A different KIND of population from the curated sets: rank-sampled "
        "from the live overall table, so it measures what good managers are "
        "doing now rather than what a named roster does.",
    ),
    "snowball": (
        "Found by snowballing others' leagues", False,
        # Quoted from docs/platform/PANEL_LEDGER.md, 2026-08-27.
        "NOT trustworthy as an elite set. These entries are league-mates of "
        "twenty stale seed IDs that no longer identify the managers they "
        "claimed to, so the selection rule that produced this pool is "
        "unreproducible: source='snowball:{league}' is not evidence of skill. "
        "The finding is recorded in docs/platform/PANEL_LEDGER.md (2026-08-27, "
        "\"NOT salvageable ... must not be treated as an elite cohort in any "
        "skill, copying or EO analysis\"). The rows are kept rather than "
        "deleted — deleting real observations to tidy a taxonomy would be "
        "worse — and are offered here only so the disclosure is visible.",
        None,
    ),
}

PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        # Differentials: nobody's differential is owned by a third of the game.
        "diff_max_own": {"type": "number", "minimum": 0.5, "maximum": 100, "default": 15.0},
        # Which crawled cohort the elite_* columns report. Mutually exclusive
        # by construction (sem_manager_cohort); 'unclassified' is the crawl-bug
        # bucket — entries holding picks with no dim_manager row.
        "cohort": {"type": "string", "enum": ["elite", "top1k", "unclassified"],
                   "default": "elite"},
        # Squad coverage may touch the network (same path as squad_overview);
        # callers that must stay offline — tests — turn it off.
        "coverage": {"type": "boolean", "default": True},
        # WHICH SETS COMPOSE THE FIELD. Not an enum: the segment names are the
        # crawl's own `dim_manager.source` prefixes, so a new crawl source must
        # be selectable the day it lands rather than after a schema edit. A
        # name that matches nobody is reported back in `selection.unknown`
        # instead of being silently dropped or rejected.
        #
        # The default is the CURATED elite only — the elite list, past overall
        # winners and individually named managers. It deliberately excludes
        # `mini_league` (the owner's own opponents, people he happens to play
        # rather than a selected elite, and the set that contains his own
        # entry) and `snowball` (see _SEGMENT_META: not trustworthy).
        "segments": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
            "default": list(DEFAULT_SEGMENTS),
        },
    },
}

# One field's measurement of one player. Both measures are optional and
# independently nullable: a field that only publishes EO leaves `own` null
# rather than reusing the EO number, and the counts are present only for
# fields whose denominator is a real, countable set of managers.
_MEASURE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "own": {"type": ["number", "null"]},        # head-count share, percent
        "eo": {"type": ["number", "null"]},         # Σ multipliers / n, percent
        "cap": {"type": ["number", "null"]},        # captaincy share, percent
        "owned_by": {"type": ["integer", "null"]},
        "started_by": {"type": ["integer", "null"]},
        "benched_by": {"type": ["integer", "null"]},
        "captained_by": {"type": ["integer", "null"]},
        # The manager count behind this measurement — the denominator carried
        # ON the number, so no cohort-derived share ever travels without its
        # n. Absent for fields with no countable denominator (FPL global,
        # external EO feeds).
        "n": {"type": ["integer", "null"]},
    },
}

_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "status": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},        # FPL marginal, percent
        "eo_pred_pct": {"type": ["number", "null"]},    # LiveFPL predicted EO, percent
        "elite_own_pct": {"type": ["number", "null"]},  # crawled cohort, percent
        "elite_eo_pct": {"type": ["number", "null"]},   # crawled cohort EO, percent
        "xpts": {"type": ["number", "null"]},           # consensus mean for xpts_gw
        "xpts_spread": {"type": ["number", "null"]},
        "n_sources": {"type": ["integer", "null"]},
        "in_squad": {"type": ["boolean", "null"]},      # null = squad unreadable
        # My own FPL multiplier as the squad read reported it: 0 bench,
        # 1 start, 2 captain, 3 triple captain. null when the squad is
        # unreadable OR readable without multipliers (a manually entered 15) --
        # the two are distinguished by `in_squad`, never guessed at here.
        "your_mult": {"type": ["integer", "null"]},
        "your_role": {"type": ["string", "null"]},      # captain|start|bench
        # key -> measurement, keys are the `fields[].key` of this same result.
        "fields": {"type": "object", "additionalProperties": _MEASURE},
    },
}

# One measurable field: who it is, what it can measure, and what its
# percentages are percentages OF.
_FIELD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "label", "kind", "measures", "denominator"],
    "properties": {
        "key": {"type": "string"},
        "label": {"type": "string"},
        "short": {"type": "string"},
        "kind": {"type": "string"},                 # fpl | external | cohort
        "role": {"type": "string"},                 # baseline | field
        "measures": {"type": "array", "items": {"type": "string"}},
        "denominator": {"type": "string"},
        "provider": {"type": ["string", "null"]},
        "metric": {"type": ["string", "null"]},
        "cohort": {"type": ["string", "null"]},
        "season": {"type": ["string", "null"]},
        "gw": {"type": ["integer", "null"]},
        "n": {"type": ["integer", "null"]},         # managers behind it, if countable
        "players": {"type": ["integer", "null"]},   # players it has a value for
        "as_of": {"type": ["string", "null"]},
        "live": {"type": "boolean"},
        # Measured, not assumed: the gw whose values this field's values are
        # byte-identical to (a re-stamped feed), or null.
        "same_values_as_gw": {"type": ["integer", "null"]},
        "note": {"type": ["string", "null"]},
        # Who is actually in a crawled cohort, by crawl source tag. Tags
        # overlap (one entry can be both elite_list and mini_league), so these
        # can sum above `n` -- `overlaps` says so rather than hiding it.
        "composition": {
            "type": ["array", "null"],
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tag", "n"],
                "properties": {"tag": {"type": "string"},
                               "n": {"type": "integer"},
                               "label": {"type": ["string", "null"]}},
            },
        },
        "overlaps": {"type": ["boolean", "null"]},
        # Segment-union fields only: which selectable sets compose this field.
        "segments": {"type": ["array", "null"], "items": {"type": "string"}},
        # How many of the owner's OWN mini-league opponents are inside this
        # field's denominator. The default selection excludes that set; the
        # crawled cohorts do not — a conflict of interest that must be
        # labelled ON the field, not discovered in a composition fold.
        "mini_league_n": {"type": ["integer", "null"]},
    },
}

#: One selectable set, as a first-class descriptor. The UI renders a checkbox
#: per entry of this list and never has to know a segment name in advance.
_SEGMENT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "label", "n", "trusted"],
    "properties": {
        "key": {"type": "string"},
        "label": {"type": "string"},
        # Managers in this set WITH a stored squad for `gw` — the only count
        # that can enter a denominator. Never a pool size dressed up as one.
        "n": {"type": ["integer", "null"]},
        # Managers carrying the tag at all, squad or no squad. `n_pool` minus
        # `n` is the part of the set this panel cannot measure.
        "n_pool": {"type": ["integer", "null"]},
        "gw": {"type": ["integer", "null"]},
        "trusted": {"type": "boolean"},
        "untrusted_reason": {"type": ["string", "null"]},
        "caveat": {"type": ["string", "null"]},
        "in_default": {"type": "boolean"},
        "selected": {"type": "boolean"},
        # Which mutually-exclusive sem_manager_cohort cohorts this set's
        # members land in, so the two vocabularies can be lined up.
        "cohorts": {"type": ["array", "null"], "items": {"type": "string"}},
    },
}

#: One row of the squad-vs-field diff. Both sides of the rank identity in the
#: SAME units, and the two measures kept apart: `*_eo_pct` are sums of FPL
#: multipliers over a denominator, `*_own_pct` are head-count shares. An `edge`
#: is only ever a difference of two like quantities.
_DIFF_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "in_squad", "in_template"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "price": {"type": ["number", "null"]},
        "in_squad": {"type": ["boolean", "null"]},   # null = squad unreadable
        "in_template": {"type": "boolean"},          # present in `rows`
        "in_field_top": {"type": "boolean"},         # top of the selected field
        "your_mult": {"type": ["integer", "null"]},
        "your_role": {"type": ["string", "null"]},
        # YOUR side of the identity, expressed in the field's own units. You
        # are one manager, so your EO is your multiplier over a denominator of
        # one: 100 × multiplier. 0.0 here is a MEASURED zero (the squad read
        # carried multipliers and this player was not in it); null means the
        # multiplier was never read, and `note` says which.
        "your_eo_pct": {"type": ["number", "null"]},
        "your_own_pct": {"type": ["number", "null"]},   # 100.0 or 0.0
        "field_eo_pct": {"type": ["number", "null"]},
        "field_own_pct": {"type": ["number", "null"]},
        "field_cap_pct": {"type": ["number", "null"]},
        "field_owned_by": {"type": ["integer", "null"]},
        "field_captained_by": {"type": ["integer", "null"]},
        # The selection's denominator, on every row — a field_* share never
        # travels without its n.
        "field_n": {"type": ["integer", "null"]},
        # THE identity term: your_eo_pct − field_eo_pct, multipliers minus
        # multipliers. Positive = you are overweight the field.
        "edge_eo_pct": {"type": ["number", "null"]},
        # Head counts minus head counts. A separate number on purpose: it is
        # not the identity term and must never be plotted on the same axis.
        "edge_own_pct": {"type": ["number", "null"]},
        "xpts": {"type": ["number", "null"]},
        "note": {"type": ["string", "null"]},
    },
}

_WHATIF_PLAYER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "price": {"type": ["number", "null"]},
        "status": {"type": ["string", "null"]},
        "your_mult": {"type": ["integer", "null"]},
        "in_squad": {"type": ["boolean", "null"]},
        "field_eo_pct": {"type": ["number", "null"]},
        "field_own_pct": {"type": ["number", "null"]},
        "field_cap_pct": {"type": ["number", "null"]},
        "xpts": {"type": ["number", "null"]},
    },
}

_MOMENTUM_POINT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gw", "n_managers"],
    "properties": {
        "gw": {"type": "integer"},
        "n_managers": {"type": ["integer", "null"]},
        "own_pct": {"type": ["number", "null"]},
        "eo_pct": {"type": ["number", "null"]},
        "cap_pct": {"type": ["number", "null"]},
    },
}

_STALE_ROW = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "eo_top10k_pct": {"type": ["number", "null"]},
        "eo_elite_pct": {"type": ["number", "null"]},
    },
}

# `required` keeps this branch disjoint from the registry's {empty, reason}
# shape: an honest empty has no `rows`, a real result always does.
RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rows", "metrics_note", "cohort_note", "gws_covered"],
    "properties": {
        "season": {"type": "string"},
        "rows": {"type": "array", "items": _ROW},
        "differentials": {"type": "array", "items": _ROW},
        "last_season": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["season", "gw", "rows"],
            "properties": {
                "season": {"type": "string"},
                "gw": {"type": "integer"},
                "rows": {"type": "array", "items": _STALE_ROW},
            },
        },
        "metrics_note": {"type": "string"},
        "cohort_note": {"type": "string"},
        # Which cohort the elite_own_pct / elite_eo_pct columns describe, and
        # the denominator behind them. Null when no crawled squad was found.
        "cohort": {"type": "string"},
        "cohort_n": {"type": ["integer", "null"]},
        "cohort_gw": {"type": ["integer", "null"]},
        "gws_covered": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["metric", "season", "gw"],
                "properties": {
                    "metric": {"type": "string"},
                    "provider": {"type": ["string", "null"]},
                    "season": {"type": "string"},
                    "gw": {"type": "integer"},
                    "players": {"type": ["integer", "null"]},
                    "latest": {"type": ["string", "null"]},
                    "live": {"type": "boolean"},
                },
            },
        },
        "xpts_gw": {"type": ["integer", "null"]},
        "squad_note": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        # The ladder of fields this warehouse can actually measure today.
        "fields": {"type": "array", "items": _FIELD},
        # THE 309-vs-262 distinction, named explicitly: the measured cohort
        # (elite_* columns, rows[].fields["cohort:*"]) and the segment
        # selection (rows[].fields["selected"], diff, whatif) are DIFFERENT
        # populations under the same word "field". The UI renders this so no
        # sentence can attach one population's trend to the other's level.
        "field_distinction": {
            "type": "object",
            "additionalProperties": False,
            "required": ["measured_cohort", "selection", "note"],
            "properties": {
                "measured_cohort": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "n"],
                    "properties": {
                        "key": {"type": "string"},
                        "n": {"type": ["integer", "null"]},
                        "gw": {"type": ["integer", "null"]},
                        "includes_mini_league": {"type": ["boolean", "null"]},
                        "mini_league_n": {"type": ["integer", "null"]},
                    },
                },
                "selection": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["key", "n"],
                    "properties": {
                        "key": {"type": "string"},
                        "n": {"type": ["integer", "null"]},
                        "gw": {"type": ["integer", "null"]},
                        "includes_mini_league": {"type": ["boolean", "null"]},
                        "mini_league_n": {"type": ["integer", "null"]},
                    },
                },
                "note": {"type": "string"},
            },
        },
        # When the LiveFPL predicted-EO series was CAPTURED (the feed's own
        # as-of instant) and for which gw — the point-of-use stamp for every
        # eo_pred_pct column, distinct from this panel's read clock.
        "eo_pred_captured": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["as_of", "gw"],
            "properties": {"as_of": {"type": ["string", "null"]},
                           "gw": {"type": ["integer", "null"]}},
        },
        # What the squad read produced, so the UI can say how much of the
        # "my multiplier" side of the rank identity it is entitled to claim.
        "squad": {
            "type": "object",
            "additionalProperties": False,
            "required": ["readable"],
            "properties": {
                "readable": {"type": "boolean"},
                "source": {"type": ["string", "null"]},
                "gw": {"type": ["integer", "null"]},
                "n": {"type": ["integer", "null"]},
                "has_multipliers": {"type": "boolean"},
                # Whether the multiplier was READ from the payload or DERIVED
                # from the role by the scoring rule (bench 0, start 1, captain
                # 2, or 3 under a triple captain read from chips_used). Before
                # a deadline the public picks carry no multiplier at all, so
                # without the derivation the whole EO side of the identity goes
                # blank on the day it matters most. The UI must be able to say
                # which it is looking at, hence both flags.
                "multipliers_read": {"type": "boolean"},
                "multipliers_derived": {"type": "boolean"},
                # False when the chip could not be read, which is the only case
                # where the captain's multiplier is genuinely uncertain (2 or 3).
                "captain_multiplier_certain": {"type": "boolean"},
                "captain": {"type": ["string", "null"]},
                "note": {"type": ["string", "null"]},
            },
        },
        # -- selectable sub-cohorts ------------------------------------------
        # Every set the UI may offer, whether or not it is selected, whether or
        # not it is trustworthy. A set that must not be used is present WITH
        # its reason rather than absent: a missing checkbox teaches nobody why.
        "segments": {"type": "array", "items": _SEGMENT},
        # What the selection actually resolved to, and what it is a share OF.
        "selection": {
            "type": "object",
            "additionalProperties": False,
            "required": ["segments", "default", "is_default", "n"],
            "properties": {
                "segments": {"type": "array", "items": {"type": "string"}},
                "requested": {"type": "array", "items": {"type": "string"}},
                # Requested names that match no crawl source. Reported, never
                # silently dropped: a typo that quietly narrows the field is
                # how a reader ends up comparing against the wrong people.
                "unknown": {"type": "array", "items": {"type": "string"}},
                "default": {"type": "array", "items": {"type": "string"}},
                "is_default": {"type": "boolean"},
                # DISTINCT managers in the union with a stored squad. Never a
                # sum of set sizes — the sets overlap.
                "n": {"type": ["integer", "null"]},
                "n_sum_of_sets": {"type": ["integer", "null"]},
                "overlap": {"type": ["integer", "null"]},
                "overlaps": {"type": ["boolean", "null"]},
                "gw": {"type": ["integer", "null"]},
                "season": {"type": ["string", "null"]},
                "denominator": {"type": "string"},
                # True when the owner's own entry is inside the selected field.
                "includes_you": {"type": ["boolean", "null"]},
                "untrusted_selected": {"type": "array", "items": {"type": "string"}},
                # Distinct entries in the union holding a pick whose element
                # resolved to no player code — a hole in the crawl, counted.
                "unresolved_pick_entries": {"type": ["integer", "null"]},
                "note": {"type": ["string", "null"]},
            },
        },
        # -- tool 1: squad-vs-field diff -------------------------------------
        "diff": {"type": "array", "items": _DIFF_ROW},
        # -- tool 2: what-if exposure simulator ------------------------------
        "whatif": {
            "type": "object",
            "additionalProperties": False,
            "required": ["players", "safe_to_recompute", "not_safe_to_recompute"],
            "properties": {
                "players": {"type": "array", "items": _WHATIF_PLAYER},
                "n": {"type": ["integer", "null"]},
                "gw": {"type": ["integer", "null"]},
                "field": {"type": ["string", "null"]},
                "denominator": {"type": ["string", "null"]},
                "safe_to_recompute": {"type": "array", "items": {"type": "string"}},
                "not_safe_to_recompute": {"type": "array", "items": {"type": "string"}},
                "note": {"type": ["string", "null"]},
            },
        },
        # -- tool 3: ownership momentum --------------------------------------
        "momentum": {
            "type": "object",
            "additionalProperties": False,
            "required": ["available", "reason", "gws", "series"],
            "properties": {
                "available": {"type": "boolean"},
                "reason": {"type": "string"},
                "gws": {"type": "array", "items": {"type": "integer"}},
                "min_gws_for_a_trend": {"type": "integer"},
                "next_gw": {"type": ["integer", "null"]},
                "next_deadline_utc": {"type": ["string", "null"]},
                "series": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["code", "points"],
                        "properties": {
                            "code": {"type": "integer"},
                            "name": {"type": ["string", "null"]},
                            "points": {"type": "array", "items": _MOMENTUM_POINT},
                        },
                    },
                },
            },
        },
    },
}


def _f(x, nd: int = 1) -> float | None:
    """NaN/None-safe rounded float — the JSON boundary for pandas values."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return round(v, nd)


def _i(x) -> int | None:
    if x is None or x != x:
        return None
    return int(x)


def _pct(x) -> float | None:
    """A cohort fraction as a percent, or None. 0.0 stays an honest 0.0."""
    if x is None or x != x:
        return None
    return round(float(x) * 100.0, 1)


def _tables_present(wh, names: tuple[str, ...]) -> set[str]:
    df = q(
        wh,
        "SELECT table_name FROM information_schema.tables WHERE table_name IN ("
        + ", ".join("?" for _ in names) + ")",
        names,
    )
    return set(df["table_name"]) if not df.empty else set()


def _squad_state(wh, season: str) -> tuple[dict[int, dict] | None, dict[str, Any]]:
    """The user's 15 with their FPL multipliers, or (None, why-not).

    Same read path as squad_overview (QuestionRouter._team_state): private API,
    then public picks, then the manually entered squad. Any failure — network
    down, nothing published yet — degrades to unreadable, never to a crash.

    The multiplier is the *my multiplier* term of the rank identity, so it is
    read rather than inferred, and it is reported ONLY when the read path
    actually carried one. A manually entered 15 has no armband and no bench
    order: those rows come back with ``mult: None``, which the UI renders as
    "owned, role unknown" — never as a silent 1×.
    """
    from fpl_edge.config import USER

    try:
        from fpl_edge.interfaces.qa import QuestionRouter

        router = QuestionRouter(wh, season=season, entry_id=int(USER.entry_id))
        state = router._team_state()
    except Exception as exc:  # noqa: BLE001 — a panel reports, it does not crash
        return None, {
            "readable": False, "has_multipliers": False,
            "note": f"squad unreadable ({type(exc).__name__}); coverage column blank",
        }
    if state is None or state.picks is None:
        return None, {
            "readable": False, "has_multipliers": False,
            "note": "no squad visible for your entry yet; coverage column blank",
        }

    # A pre-deadline squad read carries NO multiplier at all -- the public
    # picks payload publishes multipliers only once the gameweek locks. That
    # used to null `mult` for all fifteen, which nulled the EO side of the rank
    # identity for every row on the page: the tab's headline measure went blank
    # on exactly the day it is most wanted.
    #
    # The multiplier is not guessed here, it is DERIVED from facts the read did
    # carry, using the scoring rule itself: a benched player scores 0x, a
    # starter 1x, the captain 2x -- or 3x when the triple-captain chip is
    # active, which `chips_used` reports for this gameweek. Only the captain
    # row is ever ambiguous, and only when the chip cannot be read; that one
    # row is marked rather than the other fourteen being thrown away.
    #
    # `mult_source` travels with every row so the UI can say which it is, and
    # a squad with no roles at all (a manually entered 15 has no armband and no
    # bench order) still yields mult=None -- derived from nothing is nothing.
    tc_active = False
    chip_read = False
    try:
        used = getattr(state, "chips_used", None) or ()
        this_gw = getattr(state, "gw", None)
        chip_read = True
        for chip, cgw in used:
            name = getattr(chip, "value", chip)
            if str(name) == "3xc" and cgw == this_gw:
                tc_active = True
    except Exception:  # noqa: BLE001 -- an unreadable chip is not a crash
        chip_read = False

    cap_mult = 3 if tc_active else 2
    roles: dict[int, dict] = {}
    for p in state.picks:
        raw = getattr(p, "multiplier", None)
        mult = None
        if isinstance(raw, (int, float)) and raw == raw:
            mult = int(raw)
        cap = bool(getattr(p, "is_captain", False) or False)
        starter = getattr(p, "is_starter", None)
        role = None
        if cap:
            role = "captain"
        elif isinstance(starter, bool):
            role = "start" if starter else "bench"
        elif mult is not None:
            role = "start" if mult >= 1 else "bench"

        src = "read" if mult is not None else None
        if mult is None and role is not None:
            mult = {"captain": cap_mult, "start": 1, "bench": 0}[role]
            src = "derived"
        roles[int(p.code)] = {"mult": mult, "role": role, "mult_source": src}

    source = getattr(state.provenance, "name", str(state.provenance))
    gw = getattr(state, "gw", None)
    cap_code = next((c for c, r in roles.items() if r["role"] == "captain"), None)
    meta = {
        "readable": True,
        "source": str(source),
        "gw": int(gw) if isinstance(gw, (int, float)) and gw == gw else None,
        "n": len(roles),
        "has_multipliers": any(r["mult"] is not None for r in roles.values()),
        "multipliers_read": any(r["mult_source"] == "read" for r in roles.values()),
        "multipliers_derived": any(
            r["mult_source"] == "derived" for r in roles.values()),
        "captain_multiplier_certain": chip_read,
        "captain": None,          # filled in by the caller, which knows names
        "note": f"your squad read via {source}",
    }
    meta["_captain_code"] = cap_code
    return roles, meta


# --------------------------------------------------------------------------
# The field ladder: what can actually be measured, enumerated from the tables.
# --------------------------------------------------------------------------

#: Human labels for the external metric names the feed writes. LiveFPL's
#: "elite" is LIVEFPL's cohort, not this repo's crawled elite pool, and the
#: label has to say so or the two silently merge in the reader's head.
_EXTERNAL_META = {
    "eo_predicted": (
        "Whole game — predicted EO", "all FPL",
        "every FPL entry, as the provider models it for the upcoming deadline",
        None,
    ),
    "eo_top10k": (
        "LiveFPL top-10k EO", "top 10k",
        "the provider's top-10,000 sample — its definition, not a crawl of ours",
        ("Sampled and defined by the provider; this engine cannot audit its "
         "denominator."),
    ),
    "eo_elite": (
        "LiveFPL elite EO", "LiveFPL elite",
        ("the provider's own 'elite' sample — unrelated to the crawled elite "
         "cohort below"),
        ("Same word, different population: this is LiveFPL's elite, not the "
         "crawled elite pool."),
    ),
}

#: Which crawl source tag put an entry in the pool. The tags overlap, and the
#: mini-league one is a disclosure, not a footnote: those managers are the
#: owner's own opponents, so a cohort that leans on them is not an
#: independent read of the field.
_TAG_LABEL = {
    "elite_list": "curated elite list",
    "mini_league": "your own mini-league opponents",
    "winner": "past overall winners",
    "elite_named": "individually named managers",
    "expert": "public experts",
    "snowball": "found by snowballing others' leagues",
    "top1k": "sampled from the overall top-1k standings",
    "(no manager row)": "picks stored with no manager row (a crawl bug)",
}


def _external_repeats(wh, season: str) -> dict[str, int]:
    """metric -> the earlier gw whose values it is byte-identical to.

    LiveFPL re-stamps its settled top10k/elite series under the upcoming gw.
    Reporting that gw as if it were a forecast is a fabrication by labelling,
    so the duplication is measured here rather than assumed either way.
    """
    df = q(
        wh,
        """
        WITH r AS (
            SELECT metric, gw, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY metric, gw, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f WHERE season = ?
            ) WHERE rn = 1
        ), mx AS (SELECT metric, max(gw) AS g FROM r GROUP BY 1),
        pv AS (
            SELECT r.metric, max(r.gw) AS g FROM r
            JOIN mx ON mx.metric = r.metric AND r.gw < mx.g GROUP BY 1
        )
        SELECT a.metric, pv.g AS prev_gw, count(*) AS n,
               sum(CASE WHEN a.value = b.value THEN 1 ELSE 0 END) AS same
        FROM r a
        JOIN mx ON mx.metric = a.metric AND a.gw = mx.g
        JOIN pv ON pv.metric = a.metric
        JOIN r b ON b.metric = a.metric AND b.code = a.code AND b.gw = pv.g
        GROUP BY 1, 2
        """,
        (season,),
    )
    out: dict[str, int] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        n, same = _i(r["n"]) or 0, _i(r["same"]) or 0
        if n and n == same:
            out[str(r["metric"])] = int(r["prev_gw"])
    return out


def _cohort_composition(wh, season: str) -> dict[tuple[str, int], list[dict]]:
    """(cohort, gw) -> [{tag, n}], counted over entries with a stored squad.

    Counted with ``count(DISTINCT entry_id)`` per tag, so an entry listed
    under two winner years is one manager, not two — but an entry carrying two
    *different* tags appears under both, which is exactly the overlap the
    caller flags rather than hides.
    """
    df = q(
        wh,
        f"""
        WITH held AS (
            SELECT DISTINCT entry_id, gw FROM fact_manager_pick WHERE season = ?
        ), coh AS (
            SELECT entry_id, cohort, sources FROM sem_manager_cohort(now())
        ), j AS (
            SELECT h.entry_id, h.gw,
                   coalesce(c.cohort, 'unclassified') AS cohort,
                   coalesce(c.sources, '(no manager row)') AS sources
            FROM held h LEFT JOIN coh c ON c.entry_id = h.entry_id
        )
        SELECT cohort, gw, {_SEGMENT_CASE} AS tag,
               count(DISTINCT entry_id) AS n
        FROM (SELECT entry_id, gw, cohort,
                     unnest(string_split(sources, '|')) AS source FROM j)
        GROUP BY 1, 2, 3 ORDER BY 4 DESC
        """,
        (season,),
    )
    out: dict[tuple[str, int], list[dict]] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        tag = str(r["tag"])
        out.setdefault((str(r["cohort"]), int(r["gw"])), []).append(
            {"tag": tag, "n": int(r["n"]), "label": _TAG_LABEL.get(tag)})
    return out


def _segment_inventory(wh, season: str, gw: int | None) -> list[dict[str, Any]]:
    """Every selectable set: its pool size, its measurable size, its cohorts.

    ``n_squad`` is the only count that may ever become a denominator — a
    manager the crawl has no squad for cannot be part of a share of squads.
    ``n_pool`` is carried beside it so the gap is visible rather than implied:
    the expert pool is 20 managers and 0 of them have a stored GW1 squad, and a
    UI that only saw "expert (20)" would offer a checkbox that measures nobody.
    """
    if gw is None:
        return []
    df = q(
        wh,
        f"""
        WITH seg AS (
            SELECT DISTINCT entry_id, {_SEGMENT_CASE} AS segment
            FROM dim_manager WHERE as_of <= now()
        ), held AS (
            SELECT DISTINCT entry_id FROM fact_manager_pick
            WHERE season = ? AND gw = ?
        ), coh AS (
            SELECT entry_id, cohort FROM sem_manager_cohort(now())
        )
        SELECT s.segment,
               count(DISTINCT s.entry_id) AS n_pool,
               count(DISTINCT h.entry_id) AS n_squad,
               string_agg(DISTINCT c.cohort, ',' ORDER BY c.cohort) AS cohorts
        FROM seg s
        LEFT JOIN held h ON h.entry_id = s.entry_id
        LEFT JOIN coh  c ON c.entry_id = s.entry_id
        GROUP BY 1 ORDER BY 3 DESC, 2 DESC, 1
        """,
        (season, gw),
    )
    out: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        raw = r["cohorts"]
        out.append({
            "segment": str(r["segment"]),
            "n_pool": _i(r["n_pool"]),
            "n_squad": _i(r["n_squad"]),
            "cohorts": sorted(str(raw).split(",")) if raw == raw and raw else None,
        })
    return out


def _segment_ownership(wh, season: str, segments: list[str]) -> dict[int, dict]:
    """gw -> {n_managers, unresolved_entries, by_code}, over the segment UNION.

    The same three formulas as ``sem_elite_ownership`` / ``sem_segment_
    ownership`` (store/views.sql) over a caller-chosen population. The panel
    carries this inline rather than calling the macro for one reason, recorded
    on ``_SEGMENT_CASE``: read copies are opened read-only, so views.sql is not
    reapplied and a warehouse file older than the macro would not have it. The
    macro remains the canonical definition and
    ``test_the_panels_segment_union_equals_the_semantic_layer_macro`` pins this
    query's numbers to it column by column.

    THE DENOMINATOR IS ``count(DISTINCT entry_id)``. The segments overlap — on
    the live warehouse elite_list(250) + winner(12) + elite_named(8) is 270 set
    memberships over 262 distinct managers — so a sum of set sizes would be a
    denominator that no one is in.
    """
    if not segments:
        return {}
    holes = ", ".join("?" for _ in segments)
    df = q(
        wh,
        f"""
        WITH sel AS (
            SELECT DISTINCT entry_id FROM (
                SELECT entry_id, {_SEGMENT_CASE} AS segment
                FROM dim_manager WHERE as_of <= now()
            ) WHERE segment IN ({holes})
        ), mp AS (
            SELECT * FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY entry_id, season, gw, element_id
                    ORDER BY as_of DESC) rn
                FROM fact_manager_pick WHERE as_of <= now() AND season = ?
            ) WHERE rn = 1
        ), base AS (
            SELECT mp.* FROM mp JOIN sel ON sel.entry_id = mp.entry_id
        ), n AS (
            SELECT gw, count(DISTINCT entry_id) AS n_managers
            FROM base GROUP BY gw
        ), dp AS (
            SELECT * FROM (
                SELECT *, row_number() OVER (
                    PARTITION BY season, element_id ORDER BY as_of DESC) rn
                FROM dim_player WHERE as_of <= now()
            ) WHERE rn = 1
        )
        SELECT b.gw, dp.code, n.n_managers,
               count(DISTINCT b.entry_id) AS owned_by,
               count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) >= 1
                                   THEN b.entry_id END) AS started_by,
               count(DISTINCT CASE WHEN coalesce(b.multiplier, 0) = 0
                                   THEN b.entry_id END) AS benched_by,
               count(DISTINCT CASE WHEN b.is_captain THEN b.entry_id END)
                   AS captained_by,
               100.0 * count(DISTINCT b.entry_id) / n.n_managers AS own_pct,
               100.0 * count(DISTINCT CASE WHEN b.is_captain THEN b.entry_id END)
                     / n.n_managers AS captain_pct,
               100.0 * sum(coalesce(b.multiplier, 0)) / n.n_managers AS eo_pct
        FROM base b
        JOIN n ON n.gw = b.gw
        LEFT JOIN dp ON dp.season = b.season AND dp.element_id = b.element_id
        GROUP BY b.gw, dp.code, n.n_managers
        """,
        (*segments, season),
    )
    out: dict[int, dict] = {}
    if df.empty:
        return out
    for _, r in df.iterrows():
        g = int(r["gw"])
        slot = out.setdefault(
            g, {"n_managers": _i(r["n_managers"]), "unresolved_entries": 0,
                "by_code": {}})
        code = _i(r["code"])
        if code is None:
            # A pick whose element resolved to no player code: a hole in the
            # crawl, counted and reported rather than dropped at the join.
            slot["unresolved_entries"] = _i(r["owned_by"]) or 0
            continue
        slot["by_code"][code] = {
            "own": _f(r["own_pct"], 1),
            "eo": _f(r["eo_pct"], 1),
            "cap": _f(r["captain_pct"], 1),
            "owned_by": _i(r["owned_by"]),
            "started_by": _i(r["started_by"]),
            "benched_by": _i(r["benched_by"]),
            "captained_by": _i(r["captained_by"]),
        }
    return out


def _selection_includes(wh, segments: list[str], entry_id: int | None) -> bool | None:
    """Is the owner's own entry inside the field he is being measured against?

    Not a footnote. If he is in it, his own transfer moves the denominator by
    1/n, so the what-if simulator's "the field does not move when I move"
    assumption is false and the panel has to say so.
    """
    if not segments or entry_id is None:
        return None
    holes = ", ".join("?" for _ in segments)
    df = q(
        wh,
        f"""
        SELECT count(*) AS n FROM (
            SELECT entry_id, {_SEGMENT_CASE} AS segment
            FROM dim_manager WHERE as_of <= now() AND entry_id = ?
        ) WHERE segment IN ({holes})
        """,
        (entry_id, *segments),
    )
    if df.empty:
        return False
    return (_i(df.iloc[0]["n"]) or 0) > 0


def ownership_eo(
    wh,
    *,
    season: str,
    limit: int = 50,
    diff_max_own: float = 15.0,
    cohort: str = "elite",
    coverage: bool = True,
    segments: list[str] | None = None,
) -> dict[str, Any]:
    """Template and differentials from effective ownership plus consensus xPts.

    ``rows`` is the current template: ranked by the live EO metric for the
    requested season (falling back to FPL marginal ownership when no external
    feed covers it). ``differentials`` are low-ownership players with the best
    consensus xPts. ``last_season`` quarantines EO metrics recorded under a
    different season — never merged into the current table.

    ``segments`` chooses which crawl sets compose the field. ``segments`` (the
    result key) lists every set on offer with its own n and its trust flag,
    ``selection`` says what the choice resolved to and what its percentages are
    a share of, and ``fields[selected]`` / ``rows[].fields.selected`` carry the
    measurements over the union. ``diff``, ``whatif`` and ``momentum`` are
    three views of those same measurements — see the module docstring.
    """
    players = q(
        wh,
        """
        SELECT p.code, p.web_name, p.position, p.team, p.team_code, p.status,
               p.price, p.selected_by_pct
        FROM sem_players(now()) p WHERE p.season = ?
        """,
        (season,),
    )
    if players.empty:
        return empty(
            f"No {season} players in the warehouse, so neither a template nor "
            f"a differential can be named. Run `make ingest` first."
        )

    # -- what EO data exists, enumerated rather than assumed (trap 1 and 2) --
    cov = q(
        wh,
        """
        SELECT provider, metric, season, gw, count(DISTINCT code) AS players,
               max(as_of) AS latest
        FROM fact_external_ownership
        GROUP BY 1, 2, 3, 4 ORDER BY metric, season, gw
        """,
    )
    gws_covered: list[dict[str, Any]] = []
    if not cov.empty:
        for _, r in cov.iterrows():
            gws_covered.append({
                "metric": str(r["metric"]),
                "provider": str(r["provider"]),
                "season": str(r["season"]),
                "gw": int(r["gw"]),
                "players": _i(r["players"]),
                "latest": str(r["latest"]),
                "live": str(r["season"]) == season,
            })

    # -- live EO pivot: requested season only, each metric at its latest gw --
    # Kept long rather than pivoted to three named columns: the metric list is
    # whatever the feed happens to write, and a hard-coded pivot is exactly how
    # eo_top10k/eo_elite went live for this season without the panel noticing.
    # Each metric is pinned to ITS OWN latest gw and nothing older is carried
    # forward: a field is "this metric at gw G", so a code the feed dropped
    # goes null rather than silently keeping a value from a different week.
    eo = q(
        wh,
        """
        WITH r AS (
            SELECT metric, gw, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY metric, gw, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f WHERE season = ?
            ) WHERE rn = 1
        ), mx AS (SELECT metric, max(gw) AS g FROM r GROUP BY 1)
        SELECT r.metric, r.gw, r.code, r.value
        FROM r JOIN mx ON mx.metric = r.metric AND r.gw = mx.g
        """,
        (season,),
    )
    external: dict[str, dict[int, float]] = {}
    external_gw: dict[str, int] = {}
    if not eo.empty:
        for _, r in eo.iterrows():
            v = r["value"]
            if v is None or v != v:
                continue
            metric = str(r["metric"])
            external.setdefault(metric, {})[int(r["code"])] = float(v)
            external_gw[metric] = int(r["gw"])
    eo_pred = external.get("eo_predicted", {})

    # -- consensus xPts for the next deadline (or the earliest gw on file) --
    gw = next_gw(wh, season)
    if gw is None:
        g = q(wh, "SELECT min(gw) AS g FROM sem_projection_consensus(now()) WHERE season = ?",
              (season,))
        gw = _i(g.iloc[0]["g"]) if not g.empty else None
    xp: dict[int, tuple] = {}
    if gw is not None:
        cons = q(
            wh,
            """
            SELECT code, xpts_mean, xpts_spread, n_sources
            FROM sem_projection_consensus(now()) WHERE season = ? AND gw = ?
            """,
            (season, gw),
        )
        if not cons.empty:
            xp = {int(r["code"]): (r["xpts_mean"], r["xpts_spread"], r["n_sources"])
                  for _, r in cons.iterrows()}

    # -- the crawled cohort: own%/EO% from the ONE definition ----------------
    # sem_elite_ownership is the canonical effective ownership (mean FPL
    # multiplier over the cohort's managers with a stored squad). This script
    # does not compute its own: the previous local query had no cohort filter
    # at all and reported a blended top1k+elite denominator as "elite".
    # EVERY cohort is read, not just the requested one: the UI compares fields
    # against each other, and a refetch per cohort would let the two halves of
    # a comparison drift to different as_of instants.
    elite: dict[int, tuple] = {}
    elite_cohort = 0
    elite_gw = None
    rival = _tables_present(wh, ("fact_manager_pick", "dim_manager", "fact_manager_season"))
    cohorts_present: list[dict[str, Any]] = []
    cohort_rows: dict[str, dict[int, dict]] = {}
    cohort_meta: dict[str, dict[str, Any]] = {}
    composition: dict[tuple[str, int], list[dict]] = {}
    if "fact_manager_pick" in rival:
        sizes = q(
            wh,
            """
            SELECT cohort, gw, any_value(n_managers) AS n_managers
            FROM sem_elite_ownership(now()) WHERE season = ?
            GROUP BY cohort, gw ORDER BY gw DESC, cohort
            """,
            (season,),
        )
        if not sizes.empty:
            cohorts_present = [
                {"cohort": str(r["cohort"]), "gw": int(r["gw"]),
                 "n": _i(r["n_managers"])}
                for _, r in sizes.iterrows()
            ]
        picks = q(
            wh,
            """
            WITH c AS (
                SELECT * FROM sem_elite_ownership(now()) WHERE season = ?
            ), mx AS (SELECT cohort, max(gw) AS g FROM c GROUP BY 1)
            SELECT c.cohort, c.code, c.gw, c.n_managers, c.own_pct, c.eo_pct,
                   c.captain_pct, c.owned_by, c.started_by, c.benched_by,
                   c.captained_by
            FROM c JOIN mx ON mx.cohort = c.cohort AND c.gw = mx.g
            WHERE c.code IS NOT NULL
            """,
            (season,),
        )
        if not picks.empty:
            for _, r in picks.iterrows():
                co = str(r["cohort"])
                cohort_rows.setdefault(co, {})[int(r["code"])] = {
                    "own": _f(r["own_pct"], 1),
                    "eo": _f(r["eo_pct"], 1),
                    "cap": _f(r["captain_pct"], 1),
                    "owned_by": _i(r["owned_by"]),
                    "started_by": _i(r["started_by"]),
                    "benched_by": _i(r["benched_by"]),
                    "captained_by": _i(r["captained_by"]),
                    # the denominator, ON the measurement
                    "n": _i(r["n_managers"]),
                }
                cohort_meta[co] = {"n": _i(r["n_managers"]), "gw": int(r["gw"])}
                if co == cohort:
                    # The legacy elite_* keys keep their original 6dp-then-1dp
                    # rounding path, from this same read — one query, not two.
                    elite[int(r["code"])] = (_f(r["own_pct"], 6),
                                             _f(r["eo_pct"], 6))
            composition = _cohort_composition(wh, season)
        if cohort in cohort_meta:
            elite_cohort = cohort_meta[cohort]["n"] or 0
            elite_gw = cohort_meta[cohort]["gw"]

    # -- the selectable sub-cohorts: which sets compose the field ------------
    # `cohort:elite` is ONE aggregate of managers found by six different
    # crawls. Which of those crawls a reader is willing to be measured against
    # is a judgement only the reader can make — an elite list and a pool of
    # league-mates-of-stale-seeds are not the same evidence — so the sets are
    # served separately and the field is the UNION of the chosen ones.
    requested = [str(s) for s in (segments if segments is not None
                                  else DEFAULT_SEGMENTS)]
    pick_gw: int | None = None
    seg_inventory: list[dict[str, Any]] = []
    if "fact_manager_pick" in rival:
        g = q(wh, "SELECT max(gw) AS g FROM fact_manager_pick WHERE season = ?",
              (season,))
        pick_gw = _i(g.iloc[0]["g"]) if not g.empty else None
        seg_inventory = _segment_inventory(wh, season, pick_gw)
    # A requested name splits three ways, and the distinction matters. A name
    # the crawl has never written AND that this panel has never heard of is a
    # typo (`unknown`) and is reported back. A name this panel knows but that
    # no manager currently carries is NOT a typo -- it is a real set that is
    # empty in this warehouse today (the expert pool has 20 managers and 0
    # stored squads) -- so it stays in the selection and contributes nobody,
    # and its descriptor carries the zero rather than vanishing from the UI.
    known = {s["segment"] for s in seg_inventory}
    unknown = [s for s in requested
               if s not in known and s not in _SEGMENT_META]
    resolved = [s for s in requested if s not in unknown]
    # No crawled squad for this season means no field to measure, whatever was
    # selected — skip the scan rather than divide by an absence.
    sel_by_gw = (_segment_ownership(wh, season, resolved)
                 if resolved and pick_gw is not None else {})
    sel_gw = max(sel_by_gw) if sel_by_gw else None
    sel_slot = sel_by_gw.get(sel_gw) if sel_gw is not None else None
    sel_n = sel_slot["n_managers"] if sel_slot else None
    sel_by_code: dict[int, dict] = sel_slot["by_code"] if sel_slot else {}

    # -- squad coverage: codes AND the multiplier behind each one --
    roles: dict[int, dict] | None = None
    if coverage:
        roles, squad_meta = _squad_state(wh, season)
    else:
        squad_meta = {"readable": False, "has_multipliers": False,
                      "note": "coverage disabled by caller"}
    squad_note = squad_meta.get("note")
    squad = set(roles) if roles is not None else None

    def row(r) -> dict[str, Any]:
        code = int(r["code"])
        pred = eo_pred.get(code)
        e = elite.get(code)
        x = xp.get(code)
        mine = roles.get(code) if roles is not None else None
        f: dict[str, dict] = {}
        own = _f(r["selected_by_pct"], 1)
        if own is not None:
            f["global"] = {"own": own}
        for metric, values in external.items():
            v = values.get(code)
            if v is not None:
                f[metric] = {"eo": _f(v * 100.0, 1)}
        for co, by_code in cohort_rows.items():
            m = by_code.get(code)
            if m is not None:
                f[f"cohort:{co}"] = m
        # The union of the selected segments, as one more rung of the same
        # ladder — so a UI comparing "selected" against "cohort:top1k" against
        # "eo_predicted" reads all three the same way.
        sel_m = sel_by_code.get(code)
        if sel_m is not None:
            f["selected"] = {**sel_m, "n": sel_n}
        return {
            "code": code,
            "name": str(r["web_name"]) if r["web_name"] == r["web_name"] else str(code),
            "pos": POSITION_NAME.get(_i(r["position"]) or 0),
            "team": str(r["team"]) if r["team"] == r["team"] and r["team"] is not None else None,
            "team_code": _i(r["team_code"]),
            "status": str(r["status"]) if r["status"] == r["status"] and r["status"] is not None else None,
            "price": _f(r["price"], 1),
            "own_pct": own,
            "eo_pred_pct": _f(pred * 100.0, 1) if pred is not None else None,
            "elite_own_pct": _f(e[0], 1) if e else None,
            "elite_eo_pct": _f(e[1], 1) if e else None,
            "xpts": _f(x[0], 2) if x else None,
            "xpts_spread": _f(x[1], 2) if x else None,
            "n_sources": _i(x[2]) if x else None,
            "in_squad": (code in squad) if squad is not None else None,
            "your_mult": mine["mult"] if mine else None,
            "your_role": mine["role"] if mine else None,
            "fields": f,
        }

    all_rows = [row(r) for _, r in players.iterrows()]

    if squad_meta.get("readable"):
        cap_code = squad_meta.pop("_captain_code", None)
        squad_meta["captain"] = next(
            (c["name"] for c in all_rows if c["code"] == cap_code), None)
    squad_meta.pop("_captain_code", None)

    have_live_eo = bool(eo_pred)
    key_live = (lambda c: (c["eo_pred_pct"] is None, -(c["eo_pred_pct"] or 0),
                           -(c["own_pct"] or 0)))
    key_marginal = (lambda c: -(c["own_pct"] or 0))
    template = sorted(all_rows, key=key_live if have_live_eo else key_marginal)[:limit]

    diffs = sorted(
        (c for c in all_rows
         if c["xpts"] is not None and (c["own_pct"] or 0) <= diff_max_own),
        key=lambda c: (-(c["xpts"] or 0), c["own_pct"] or 0),
    )[:limit]

    # -- other-season EO: quarantined, stamped with its real season and gw --
    last_season = None
    stale = q(
        wh,
        """
        WITH latest AS (
            SELECT season, gw, metric, code, value FROM (
                SELECT f.*, row_number() OVER (
                    PARTITION BY season, gw, metric, code ORDER BY as_of DESC) rn
                FROM fact_external_ownership f
                WHERE season <> ?
                  AND season = (SELECT max(season) FROM fact_external_ownership
                                WHERE season <> ?)
            ) WHERE rn = 1
        )
        SELECT l.season, l.gw, l.code,
               any_value(p.web_name) AS web_name, any_value(p.position) AS position,
               any_value(p.team) AS team,
               max(CASE WHEN l.metric = 'eo_top10k' THEN l.value END) AS eo_top10k,
               max(CASE WHEN l.metric = 'eo_elite'  THEN l.value END) AS eo_elite
        FROM latest l
        LEFT JOIN sem_players(now()) p ON p.season = l.season AND p.code = l.code
        GROUP BY l.season, l.gw, l.code
        ORDER BY coalesce(max(CASE WHEN l.metric = 'eo_top10k' THEN l.value END),
                          max(CASE WHEN l.metric = 'eo_elite'  THEN l.value END),
                          0) DESC
        LIMIT ?
        """,
        (season, season, limit),
    )
    if not stale.empty:
        s_season = str(stale.iloc[0]["season"])
        s_gw = int(stale.iloc[0]["gw"])
        last_season = {
            "season": s_season,
            "gw": s_gw,
            "rows": [{
                "code": int(r["code"]),
                "name": str(r["web_name"]) if r["web_name"] == r["web_name"]
                        and r["web_name"] is not None else str(int(r["code"])),
                "pos": POSITION_NAME.get(_i(r["position"]) or 0),
                "team": str(r["team"]) if r["team"] == r["team"]
                        and r["team"] is not None else None,
                "eo_top10k_pct": _pct(r["eo_top10k"]),
                "eo_elite_pct": _pct(r["eo_elite"]),
            } for _, r in stale.iterrows()],
        }

    # -- honest notes, built from what was actually found --
    live_bits, stale_bits = [], []
    for c in gws_covered:
        label = f"{c['metric']} ({c['provider']}, {c['season']} GW{c['gw']}, {c['players']} players)"
        (live_bits if c["live"] else stale_bits).append(label)
    parts = []
    if live_bits:
        parts.append("Live EO: " + "; ".join(live_bits)
                     + ". Values are cohort fractions × captaincy — over 100% is normal.")
    else:
        parts.append(f"No external EO feed covers {season}; the template is "
                     f"ranked by FPL marginal ownership instead.")
    if stale_bits:
        parts.append("Stale (other season, shown only as \"last season's final "
                     "template\", never merged into current): " + "; ".join(stale_bits) + ".")
    parts.append("own_pct is FPL's marginal selected-by % — no captaincy weighting.")
    metrics_note = " ".join(parts)

    others = "; ".join(
        f"{c['cohort']} n={c['n']} (GW{c['gw']})"
        for c in cohorts_present if c["cohort"] != cohort
    )
    if elite_cohort:
        cohort_note = (
            f"{cohort} own%/EO% observed from {elite_cohort} crawled managers' "
            f"locked GW{elite_gw} squads (fact_manager_pick, via "
            f"sem_elite_ownership). EO is the mean FPL multiplier those "
            f"{elite_cohort} managers applied — 0 benched, 1 started, 2 "
            f"captain, 3 triple captain — so it is not ownership and can "
            f"exceed 100%. Cohorts are mutually exclusive: an entry sampled by "
            f"both crawls counts as top1k only, never in both denominators."
            + (f" Also on file: {others}." if others else "")
        )
    else:
        n_mgr = n_seasons = 0
        if "dim_manager" in rival:
            d = q(wh, "SELECT count(DISTINCT entry_id) AS n FROM dim_manager")
            n_mgr = int(d.iloc[0]["n"]) if not d.empty else 0
        if "fact_manager_season" in rival:
            d = q(wh, "SELECT count(*) AS n FROM fact_manager_season")
            n_seasons = int(d.iloc[0]["n"]) if not d.empty else 0
        cohort_note = (
            f"No {cohort} picks stored for {season} yet"
            + (f" ({n_mgr} crawled managers with {n_seasons} past-season records "
               f"on file, but fact_manager_pick is empty" if n_mgr else "")
            + ("); " if n_mgr else "; ")
            + f"{cohort} own%/EO% columns stay blank until the picks crawl runs."
            + (f" Other cohorts on file: {others}." if others else "")
        )

    # -- the field ladder: one descriptor per thing that can actually be
    #    measured, so the UI never has to hard-code a field or a denominator --
    as_of = latest_as_of(wh, "fact_player_state", season)
    repeats = _external_repeats(wh, season) if gws_covered else {}
    cov_at_gw = {
        (c["metric"], c["gw"]): c for c in gws_covered if c["live"]
    }

    def n_with(key: str, measure: str) -> int:
        return sum(1 for c in all_rows
                   if (c["fields"].get(key) or {}).get(measure) is not None)

    fields: list[dict[str, Any]] = [{
        "key": "global",
        "label": "Whole game — FPL ownership",
        "short": "all FPL",
        "kind": "fpl",
        "role": "baseline",
        "measures": ["own"],
        "denominator": "every FPL entry — FPL publishes the share, not the "
                       "entry count, so no manager count is claimed here",
        "provider": "fpl",
        "metric": "selected_by_pct",
        "cohort": None,
        "season": season,
        "gw": None,
        "n": None,
        "players": n_with("global", "own"),
        "as_of": as_of,
        "live": True,
        "same_values_as_gw": None,
        "note": "Marginal ownership — no captaincy weighting. It is not an EO, "
                "so it never shares an axis with one.",
        "composition": None,
        "overlaps": None,
    }]

    for metric in ("eo_predicted", "eo_top10k", "eo_elite",
                   *sorted(k for k in external if k not in _EXTERNAL_META)):
        if metric not in external:
            continue
        g = external_gw.get(metric)
        c = cov_at_gw.get((metric, g), {})
        label, short, denom, note = _EXTERNAL_META.get(
            metric, (metric, metric, f"the provider's {metric} population", None))
        fields.append({
            "key": metric,
            "label": label,
            "short": short,
            "kind": "external",
            "role": "baseline" if metric == "eo_predicted" else "field",
            "measures": ["eo"],
            "denominator": denom,
            "provider": c.get("provider"),
            "metric": metric,
            "cohort": None,
            "season": season,
            "gw": g,
            "n": None,
            "players": n_with(metric, "eo"),
            "as_of": c.get("latest"),
            "live": True,
            "same_values_as_gw": repeats.get(metric),
            "note": note,
            "composition": None,
            "overlaps": None,
        })

    _COHORT_LABEL = {
        "elite": ("Crawled elite pool", "elite"),
        "top1k": ("Crawled top-1k sample", "top 1k"),
        "unclassified": ("Unclassified crawled squads", "unclassified"),
    }
    for co in ("elite", "top1k", "unclassified",
               *sorted(k for k in cohort_meta if k not in _COHORT_LABEL)):
        meta = cohort_meta.get(co)
        if meta is None:
            continue
        n, g = meta["n"], meta["gw"]
        label, short = _COHORT_LABEL.get(co, (f"Crawled {co}", co))
        comp = composition.get((co, g))
        # The conflict of interest, labelled ON the field: the crawled pool
        # can contain the owner's own mini-league opponents — a set the
        # default SELECTION deliberately excludes.
        ml_n = next((int(x["n"]) for x in comp or []
                     if x.get("tag") == "mini_league"), None)
        denom = (
            f"the {n} managers in the {co} crawl pool with a stored GW{g} "
            f"squad" if co != "unclassified" else
            f"the {n} crawled entries with a stored GW{g} squad and no "
            f"dim_manager row to classify them"
        )
        fields.append({
            "key": f"cohort:{co}",
            "label": f"{label} ({n})",
            "short": short,
            "kind": "cohort",
            "role": "field",
            "measures": ["own", "eo"],
            "denominator": denom,
            "provider": "fact_manager_pick crawl",
            "metric": None,
            "cohort": co,
            "season": season,
            "gw": g,
            "n": n,
            "players": n_with(f"cohort:{co}", "own"),
            "as_of": as_of,
            "live": True,
            "same_values_as_gw": None,
            "note": "Observed squads, not a model: EO is the mean FPL "
                    "multiplier these managers actually applied. A small "
                    "denominator makes every share coarse — one manager is "
                    + (f"{100.0 / n:.2f} percentage points." if n else "one row.")
                    + (f" Includes {ml_n} of the owner's own mini-league "
                       f"opponents — a set the default selection excludes; "
                       f"this measured cohort does not." if ml_n else ""),
            "composition": comp,
            "overlaps": bool(comp) and sum(x["n"] for x in comp) > (n or 0),
            "mini_league_n": ml_n,
        })

    # -- the selectable sets, as first-class descriptors ---------------------
    seg_selected = set(resolved)
    # Every set the UI could offer: what the crawl actually holds, plus any
    # set that is selected or in the default but currently has no members. A
    # selected set with no checkbox to unselect it would be a dead end.
    offerable = list(seg_inventory) + [
        {"segment": k, "n_pool": 0, "n_squad": 0, "cohorts": None}
        for k in list(DEFAULT_SEGMENTS) + resolved
        if k not in known and k not in {x["segment"] for x in seg_inventory}
    ]
    seen_seg: set[str] = set()
    segment_rows: list[dict[str, Any]] = []
    for s in offerable:
        key = s["segment"]
        if key in seen_seg:
            continue
        seen_seg.add(key)
        label, trusted, untrusted, caveat = _SEGMENT_META.get(
            key, (key, True, None,
                  "An unrecognised crawl source. It is offered because it "
                  "exists, but nothing is known here about how it was built."))
        segment_rows.append({
            "key": key,
            "label": label,
            "n": s["n_squad"],
            "n_pool": s["n_pool"],
            "gw": pick_gw,
            "trusted": trusted,
            "untrusted_reason": untrusted,
            "caveat": caveat,
            "in_default": key in DEFAULT_SEGMENTS,
            "selected": key in seg_selected,
            "cohorts": s["cohorts"],
        })

    sum_of_sets = sum((s["n"] or 0) for s in segment_rows if s["selected"]) or None
    overlap = (sum_of_sets - sel_n) if (sum_of_sets and sel_n is not None) else None
    try:
        from fpl_edge.config import USER

        my_entry = int(USER.entry_id)
    except Exception:  # noqa: BLE001 — a panel reports, it does not crash
        my_entry = None
    includes_you = _selection_includes(wh, resolved, my_entry)
    untrusted_selected = [s["key"] for s in segment_rows
                          if s["selected"] and not s["trusted"]]

    if sel_n:
        sel_denominator = (
            f"the {sel_n} DISTINCT managers in the union of "
            f"{', '.join(resolved)} with a stored GW{sel_gw} squad"
            + (f" — not {sum_of_sets}: the sets overlap, {overlap} manager"
               f"{'s' if overlap != 1 else ''} carry more than one tag"
               if overlap else "")
        )
    else:
        sel_denominator = (
            "no denominator: the selected sets contain no manager with a "
            "stored squad for this season, so nothing is a share of anything"
        )

    sel_note_bits: list[str] = []
    if unknown:
        sel_note_bits.append(
            f"Requested but unknown to the crawl, so not in the field: "
            f"{', '.join(unknown)}.")
    if untrusted_selected:
        sel_note_bits.append(
            f"An UNTRUSTWORTHY set is selected ({', '.join(untrusted_selected)}); "
            f"see segments[].untrusted_reason before reading any number here.")
    if includes_you:
        sel_note_bits.append(
            "Your own entry is inside this field, so you are part of the "
            "denominator you are measuring yourself against and a transfer of "
            "yours moves the field by 1/n.")
    squad_gw = squad_meta.get("gw")
    if sel_gw is not None and isinstance(squad_gw, int) and squad_gw != sel_gw:
        sel_note_bits.append(
            f"Your squad was read for GW{squad_gw} but the field's squads are "
            f"stored for GW{sel_gw}: the diff compares two different "
            f"gameweeks and the gap is not purely a difference of opinion.")
    if not resolved:
        sel_note_bits.append(
            "No set is selected, so there is no field: the diff and the "
            "what-if simulator have nothing to compare against.")

    selection = {
        "segments": resolved,
        "requested": requested,
        "unknown": unknown,
        "default": list(DEFAULT_SEGMENTS),
        # Order-insensitive: the same three sets in another order is still
        # the default field, and a UI should not have to preserve order to
        # be told so.
        "is_default": sorted(requested) == sorted(DEFAULT_SEGMENTS),
        "n": sel_n,
        "n_sum_of_sets": sum_of_sets,
        "overlap": overlap,
        "overlaps": bool(overlap) if overlap is not None else None,
        "gw": sel_gw,
        "season": season,
        "denominator": sel_denominator,
        "includes_you": includes_you,
        "untrusted_selected": untrusted_selected,
        "unresolved_pick_entries": (sel_slot or {}).get("unresolved_entries"),
        "note": " ".join(sel_note_bits) or None,
    }

    if sel_n:
        sel_comp = [{"tag": s["key"], "n": s["n"], "label": s["label"]}
                    for s in segment_rows if s["selected"]]
        fields.append({
            "key": "selected",
            "label": "Selected field (" + " + ".join(resolved) + f", {sel_n})",
            "short": "selected",
            "kind": "segments",
            "role": "field",
            "measures": ["own", "eo"],
            "denominator": sel_denominator,
            "provider": "fact_manager_pick crawl",
            "metric": None,
            "cohort": None,
            "season": season,
            "gw": sel_gw,
            "n": sel_n,
            "players": n_with("selected", "own"),
            "as_of": as_of,
            "live": True,
            "same_values_as_gw": None,
            "note": "Observed squads over the union of the selected sets. "
                    "One manager is "
                    + (f"{100.0 / sel_n:.2f} percentage points." if sel_n
                       else "one row.")
                    + (" An untrustworthy set is included; see segments[]."
                       if untrusted_selected else ""),
            "composition": sel_comp,
            "overlaps": bool(overlap) if overlap is not None else None,
            "segments": resolved,
            "mini_league_n": next(
                (s["n"] for s in segment_rows
                 if s["key"] == "mini_league" and s["selected"]), None),
        })

    # -- tool 1: squad-vs-field diff ----------------------------------------
    # Every player on EITHER side of the identity: what I hold, what the field
    # holds, and the term itself. A player I own whom the field does not is the
    # single most important row on this panel — it is what a differential IS —
    # so it is built from the union of the two sides, never from the field's
    # top rows alone.
    by_code_row = {r["code"]: r for r in all_rows}
    field_top = [c for c, _m in sorted(
        sel_by_code.items(), key=lambda kv: -(kv[1]["eo"] or 0))[:limit]]
    template_codes = {r["code"] for r in template}
    squad_codes = set(squad) if squad is not None else set()
    readable = bool(squad_meta.get("readable"))
    has_mult = bool(squad_meta.get("has_multipliers"))

    if not readable:
        your_note = ("your squad could not be read, so your side of the "
                     "identity is unknown — not zero")
    elif not has_mult:
        your_note = ("your squad read carries no multipliers, so ownership can "
                     "be compared but EO cannot — not assumed to be 1x")
    else:
        your_note = None

    diff_rows: list[dict[str, Any]] = []
    for code in sorted(squad_codes | template_codes | set(field_top)):
        r = by_code_row.get(code)
        m = sel_by_code.get(code)
        in_squad = r["in_squad"] if r else (code in squad_codes if squad is not None else None)
        your_mult = r["your_mult"] if r else None
        your_own = (100.0 if in_squad else 0.0) if in_squad is not None else None
        if your_mult is not None:
            your_eo = float(your_mult) * 100.0
        elif in_squad is False and has_mult:
            # Measured, not assumed: the read carried multipliers and this
            # player was not among them, so my multiplier on him is 0.
            your_eo = 0.0
        else:
            your_eo = None
        if sel_n:
            # A player none of the field owns is a MEASURED zero: the field is
            # a fully enumerated set of squads over a known denominator, so
            # "0 of {sel_n}" is an observation, not a missing value.
            f_own = m["own"] if m else 0.0
            f_eo = m["eo"] if m else 0.0
            f_cap = m["cap"] if m else 0.0
            f_owned = m["owned_by"] if m else 0
            f_capped = m["captained_by"] if m else 0
        else:
            f_own = f_eo = f_cap = None
            f_owned = f_capped = None
        diff_rows.append({
            "code": code,
            "name": r["name"] if r else str(code),
            "pos": r["pos"] if r else None,
            "team": r["team"] if r else None,
            "team_code": r["team_code"] if r else None,
            "price": r["price"] if r else None,
            "in_squad": in_squad,
            "in_template": code in template_codes,
            "in_field_top": code in set(field_top),
            "your_mult": your_mult,
            "your_role": r["your_role"] if r else None,
            "your_eo_pct": your_eo,
            "your_own_pct": your_own,
            "field_eo_pct": f_eo,
            "field_own_pct": f_own,
            "field_cap_pct": f_cap,
            "field_owned_by": f_owned,
            "field_captained_by": f_capped,
            "field_n": sel_n if sel_n else None,
            "edge_eo_pct": (round(your_eo - f_eo, 1)
                            if your_eo is not None and f_eo is not None else None),
            "edge_own_pct": (round(your_own - f_own, 1)
                             if your_own is not None and f_own is not None else None),
            "xpts": r["xpts"] if r else None,
            "note": " ".join(
                ([your_note] if (your_note and your_eo is None) else [])
                + ([] if sel_n else
                   ["no field is selected, so there is nothing to diff "
                    "against"])) or None,
        })
    diff_rows.sort(key=lambda d: (d["edge_eo_pct"] is None,
                                  -(d["edge_eo_pct"] or 0.0), d["code"]))

    # -- tool 2: what-if exposure simulator ---------------------------------
    whatif = {
        "players": [{
            "code": r["code"], "name": r["name"], "pos": r["pos"],
            "team": r["team"], "team_code": r["team_code"], "price": r["price"],
            "status": r["status"], "your_mult": r["your_mult"],
            "in_squad": r["in_squad"],
            "field_eo_pct": (sel_by_code.get(r["code"], {}).get("eo", 0.0)
                             if sel_n else None),
            "field_own_pct": (sel_by_code.get(r["code"], {}).get("own", 0.0)
                              if sel_n else None),
            "field_cap_pct": (sel_by_code.get(r["code"], {}).get("cap", 0.0)
                              if sel_n else None),
            "xpts": r["xpts"],
        } for r in all_rows],
        "n": sel_n,
        "gw": sel_gw,
        "field": "selected" if sel_n else None,
        "denominator": sel_denominator,
        "safe_to_recompute": [
            "Your side of the identity. You are one manager: your EO on a "
            "player is 100 × the multiplier you give him, so swapping, "
            "benching or re-captaining changes only your own numbers.",
            "edge_eo_pct = your_eo_pct − field_eo_pct, per player, for any "
            "hypothetical squad — field_eo_pct is fixed while the selection "
            "and the gameweek are fixed.",
            "Net exposure: Σ over your XI of (your_eo_pct − field_eo_pct), and "
            "any subset of it. It is a sum of per-player terms already served.",
            "Total field EO carried by your squad: Σ field_eo_pct over the "
            "players you hold.",
            "Head-count gaps: your_own_pct (100 or 0) − field_own_pct. Keep "
            "them on their own axis; they are not EO.",
        ],
        "not_safe_to_recompute": [
            "field_eo_pct / field_own_pct / field_cap_pct for a different "
            "SELECTION of segments. Those are measured over a different set of "
            "managers and a different denominator — refetch with `segments`.",
            "Anything for a different gameweek. The field's numbers are "
            f"GW{sel_gw} squads; there is no client-side way to age them.",
            "xpts under a different gameweek or a new projection run.",
            "Rank-move estimates. (my multiplier − field EO) × points needs "
            "points, and this panel serves consensus xPts, not a distribution.",
        ] + ([
            "The field itself, if YOU change your squad: your entry is inside "
            "this selection, so your own transfer moves field_eo_pct by up to "
            f"1/{sel_n}. Refetch, or deselect the set that contains you."
        ] if includes_you else []),
        "note": (
            f"Every current-season player is listed, so any swap-in resolves "
            f"without another round trip. Where the field owns a player "
            f"0 times, field_own_pct and field_eo_pct are a MEASURED 0.0 over "
            f"the same {sel_n} squads — not a missing value."
            if sel_n else
            "No field is selected, so every field_* value is null. Select at "
            "least one segment with a stored squad."
        ),
    }

    # -- tool 3: ownership momentum -----------------------------------------
    # Per-gameweek EO for the selected field. Genuinely empty today: the crawl
    # holds GW1 squads and nothing else, because a squad becomes public at its
    # deadline. Two points are the minimum that can show a direction, so one
    # point ships as no series at all rather than as a chart with a single dot
    # that a reader will read as flat.
    mom_gws = sorted(sel_by_gw)
    mom_available = len(mom_gws) >= 2
    dl = None
    if gw is not None:
        d = q(
            wh,
            "SELECT deadline_utc FROM (SELECT *, row_number() OVER ("
            "  PARTITION BY season, gw ORDER BY as_of DESC) rn "
            "FROM dim_event WHERE season = ? AND gw = ?) WHERE rn = 1",
            (season, gw),
        )
        if not d.empty and d.iloc[0]["deadline_utc"] is not None:
            dl = str(d.iloc[0]["deadline_utc"])
    if mom_available:
        mom_reason = (f"{len(mom_gws)} gameweeks of stored squads for this "
                      f"selection: GW{mom_gws[0]}–GW{mom_gws[-1]}.")
    elif mom_gws:
        mom_reason = (
            f"Only GW{mom_gws[0]} squads exist for this selection, and one "
            f"point is not a trend. A manager's squad becomes public at its "
            f"deadline, so the second point arrives after "
            + (f"the GW{gw} deadline ({dl})." if dl else "the next deadline.")
        )
    else:
        mom_reason = ("No stored squads for this selection, so there is no "
                      "series to build.")
    mom_codes = [r["code"] for r in template] + sorted(squad_codes)
    seen: set[int] = set()
    mom_series: list[dict[str, Any]] = []
    if mom_available:
        for code in mom_codes:
            if code in seen:
                continue
            seen.add(code)
            pts = []
            for g2 in mom_gws:
                slot = sel_by_gw[g2]
                m = slot["by_code"].get(code)
                pts.append({
                    "gw": g2,
                    "n_managers": slot["n_managers"],
                    "own_pct": m["own"] if m else 0.0,
                    "eo_pct": m["eo"] if m else 0.0,
                    "cap_pct": m["cap"] if m else 0.0,
                })
            mom_series.append({
                "code": code,
                "name": (by_code_row.get(code) or {}).get("name"),
                "points": pts,
            })
    momentum = {
        "available": mom_available,
        "reason": mom_reason,
        "gws": mom_gws,
        "min_gws_for_a_trend": 2,
        "next_gw": gw,
        "next_deadline_utc": dl,
        "series": mom_series,
    }

    # -- the two "fields" under one word, told apart explicitly --------------
    # R2's finding: the measured cohort (elite_* columns, the charts) and the
    # segment selection (the WHO-IS-IN-IT card, diff, whatif) are DIFFERENT
    # populations both answering to "the field". This block names both, with
    # their n and their mini-league content, so the UI links them visibly and
    # no sentence attaches one population's trend to the other's level.
    ml_measured = None
    if elite_gw is not None:
        ml_measured = next(
            (int(x["n"]) for x in composition.get((cohort, elite_gw)) or []
             if x.get("tag") == "mini_league"), None)
    field_distinction = {
        "measured_cohort": {
            "key": f"cohort:{cohort}",
            "n": elite_cohort or None,
            "gw": elite_gw,
            "includes_mini_league": (bool(ml_measured)
                                     if elite_cohort else None),
            "mini_league_n": ml_measured,
        },
        "selection": {
            "key": "selected",
            "n": sel_n,
            "gw": sel_gw,
            "includes_mini_league": "mini_league" in resolved,
            "mini_league_n": next(
                (s["n"] for s in segment_rows
                 if s["key"] == "mini_league" and s["selected"]), None),
        },
        "note": (
            f"Two populations answer to the word \"field\" in this payload: "
            f"the measured cohort (cohort:{cohort} — the elite_* columns and "
            f"rows[].fields) and the segment selection (rows[].fields"
            f"[\"selected\"], diff, whatif). They are different sets of "
            f"managers with different denominators; a level from one and a "
            f"trend from the other must never share a sentence."
        ),
    }

    # -- LiveFPL predicted-EO capture instant, at point of use ---------------
    eo_pred_captured = None
    g_pred = external_gw.get("eo_predicted")
    if g_pred is not None:
        c_pred = cov_at_gw.get(("eo_predicted", g_pred), {})
        eo_pred_captured = {"as_of": c_pred.get("latest"), "gw": g_pred}

    return {
        "season": season,
        "rows": template,
        "differentials": diffs,
        "segments": segment_rows,
        "selection": selection,
        "diff": diff_rows,
        "whatif": whatif,
        "momentum": momentum,
        "last_season": last_season,
        "metrics_note": metrics_note,
        "cohort_note": cohort_note,
        "cohort": cohort,
        "cohort_n": elite_cohort or None,
        "cohort_gw": elite_gw,
        "gws_covered": gws_covered,
        "fields": fields,
        "field_distinction": field_distinction,
        "eo_pred_captured": eo_pred_captured,
        "squad": squad_meta,
        "xpts_gw": gw,
        "squad_note": squad_note,
        "as_of": as_of,
    }


register_script(
    name="ownership_eo",
    fn=ownership_eo,
    params_schema=PARAMS_SCHEMA,
    result_schema=RESULT_SCHEMA,
    title="Template & effective ownership",
    description="What the field owns: marginal ownership beside every "
                "external effective-ownership metric, template and "
                "differential views.",
)
