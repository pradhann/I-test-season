"""The JSON Schemas, the segment vocabulary and the scalar coercions.

A1 put ``_f``, ``_i``, ``_pct`` and ``_tables_present`` in ``panel.py`` with
``ownership_eo``. Three of the five modules call them, so that placement makes
``load`` and ``fields`` import ``panel``, which imports them back. They sit
here instead, the way ``fixtures/constants.py`` took its shared helpers: this
module imports nothing else in the package."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.platform.scripts.common import q, season_param

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
        "These are people you happen to play, not a selected elite. Your "
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
        "deleted, because deleting real observations to tidy a taxonomy "
        "would be worse, and are offered here only so the disclosure is "
        "visible.",
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
        # The triple-captain part of `eo`, in the same percentage points, and
        # `eo` with that part taken out. A 3x multiplier is a chip that cannot
        # repeat, so an EO read as a forward-looking baseline needs the two
        # numbers side by side: eo = eo_ex_tc + eo_tc_pp, and eo_ex_tc is the
        # one that reconciles with own + cap. Present only on fields measured
        # from stored squads here; null on cohort and external feeds, which
        # publish an EO with the chip already folded in and no way to split it.
        "eo_tc_pp": {"type": ["number", "null"]},
        "eo_ex_tc": {"type": ["number", "null"]},
        "owned_by": {"type": ["integer", "null"]},
        "started_by": {"type": ["integer", "null"]},
        "benched_by": {"type": ["integer", "null"]},
        "captained_by": {"type": ["integer", "null"]},
        "tripled_by": {"type": ["integer", "null"]},   # managers at 3x
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
    "required": ["code", "name", "in_squad", "in_panel_rows"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "price": {"type": ["number", "null"]},
        "in_squad": {"type": ["boolean", "null"]},   # null = squad unreadable
        # Present in this payload's own `rows` array, which is the top
        # `limit` players by `rows_ranked_by` (LiveFPL predicted EO where that
        # feed covers the season, FPL marginal ownership otherwise). It was
        # called `in_template` and that name was wrong twice over: the ranking
        # is an external EO, not a crawl, and the page's Template XV is a
        # different thing built from the field's own start counts. The list it
        # refers to is `rows`, so a reader can check every flag.
        "in_panel_rows": {"type": "boolean"},
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
        # field_eo_pct with the triple-captain third unit removed, and the
        # size of that unit. See `_MEASURE` for why both are served.
        "field_eo_ex_tc_pct": {"type": ["number", "null"]},
        "field_eo_tc_pp": {"type": ["number", "null"]},
        "field_own_pct": {"type": ["number", "null"]},
        "field_cap_pct": {"type": ["number", "null"]},
        "field_owned_by": {"type": ["integer", "null"]},
        "field_captained_by": {"type": ["integer", "null"]},
        "field_tripled_by": {"type": ["integer", "null"]},
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
        "field_eo_ex_tc_pct": {"type": ["number", "null"]},
        "field_eo_tc_pp": {"type": ["number", "null"]},
        "field_own_pct": {"type": ["number", "null"]},
        "field_cap_pct": {"type": ["number", "null"]},
        "field_tripled_by": {"type": ["integer", "null"]},
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
        # The triple-captain part of eo_pct for that week, so a spike in the
        # series can be read as a chip week rather than as a trend.
        "eo_tc_pp": {"type": ["number", "null"]},
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
        # The metric `rows` is ordered by. `diff[].in_panel_rows` points at
        # this list, so the reader can audit the flag against it.
        "rows_ranked_by": {"type": ["string", "null"]},
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
