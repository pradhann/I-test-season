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

import datetime as dt
import json
import math
import re
import statistics
from pathlib import Path
from typing import Any

from fpl_edge.config import USER
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    PROJECTION_NAME,
    UTC,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
    source_dir,
)
from fpl_edge.platform.scripts.fixtures import fixture_board
from fpl_edge.platform.scripts.ownership import ownership_eo
from fpl_edge.platform.scripts.prices import price_radar
from fpl_edge.platform.scripts.squad import squad_overview

TRANSFER_PLAN_NAME = "transfer_plan.json"

#: The verdict's adjudication rule, echoed VERBATIM into the payload (the same
#: contract as the thresholds echo: the view renders this constant, it never
#: paraphrases it). The verdict PICKS, it does not blend: one answer per
#: question by this precedence, with every dissenting voice printed beside it
#: as data — currencies never summed.
PRECEDENCE = (
    "The solver is the only voice optimizing the season objective, so its "
    "fresh plan wins ties; a voice may only overrule it through a named "
    "rule. Transfer: the solver plan while fresh or aging; the deterministic "
    "move rules only when the plan is stale or missing. Captain: the solver "
    "plan's captain, else the mean-xPts pick; dissenting measures are "
    "printed, never blended. Bench: the bench_inversion swaps applied in the "
    "suggested XI. Chip: the solver plan's chip, else hold."
)

#: "31.66% optimality gap" in the solver's own notes — parsed, never
#: recomputed. The gap prints NEXT TO gain_over_roll, not behind a fold.
_GAP_NOTE = re.compile(r"(\d+(?:\.\d+)?)\s*%\s*optimality gap")

#: Every gate the brief applies, echoed verbatim into the payload. The view
#: renders these; it hardcodes none of them.
THRESHOLDS: dict[str, float | int] = {
    "bench_margin_xpts": 0.5,
    "own_fall_net_hr": -1500,
    "target_rise_net_hr": 2500,
    "template_own_pct": 40,
    "diff_own_pct": 10,
    "diff_xpts_margin": 1.0,
    "standout_margin_xpts": 3.0,
    "standout_horizon_gws": 4,
    "fixture_rank_move": 6,
    "solve_fresh_window_h": 4,
    "tile_cap": 6,
    # "Moves to consider" — deterministic rules, every gate echoed here.
    "move_cap": 3,
    "coverage_attack_rank_top": 3,
    "coverage_max_held": 1,
    "coverage_min_xpts_gain": 0.0,
    "recent_gws": 2,
    "recent_returns_min": 1,
    "form_returns_margin": 2,
    "form_xpts_margin": 1.0,
}

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "entry_id": {"type": ["integer", "null"], "default": None},
    },
}

_PLAYER_REF = {
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
        "own_pct": {"type": ["number", "null"]},
    },
}

_DRILL = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "focus": {"type": "string"},
        "codes": {"type": "array", "items": {"type": "integer"}},
        "drawer": {"type": "integer"},
        "tab": {"type": "string"},
    },
}

_ALERT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rule", "kind", "priority", "codes", "numbers",
                 "source_panel", "source_as_of"],
    "properties": {
        "rule": {"type": "string"},
        # BENCH and CAPTAIN are gone from this enum on purpose: bench order
        # and captaincy are fixed IN the squad via `suggested_xi`, not argued
        # about in a prose row above it.
        "kind": {"type": "string",
                 "enum": ["AVAILABILITY", "MARKET", "SOLVER", "GAP"]},
        "priority": {"type": "integer", "minimum": 0, "maximum": 1},
        "codes": {"type": "array", "items": {"type": "integer"}},
        "players": {"type": "array", "items": _PLAYER_REF},
        "numbers": {"type": "object",
                    "additionalProperties": {"type": ["number", "null"]}},
        "news": {"type": ["string", "null"]},   # verbatim FPL news, availability only
        "status": {"type": ["string", "null"]},  # verbatim FPL status letter
        "reason": {"type": ["string", "null"]},  # source panel's own reason, GAP only
        "source_panel": {"type": "string"},
        "source_as_of": {"type": ["string", "null"]},
        "drill": _DRILL,
    },
}

_TILE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "priority", "number", "gate",
                 "source_panel", "source_as_of"],
    "properties": {
        "kind": {"type": "string",
                 "enum": ["xpts_standout", "template_gap", "differential",
                          "fixture_turn", "price_rise_target",
                          "creator_shift"]},
        "priority": {"type": "integer", "minimum": 3, "maximum": 4},
        "code": {"type": ["integer", "null"]},
        "player": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "team_code": {"type": ["integer", "null"]},
        "team": {"type": ["string", "null"]},
        "number": {
            "type": "object",
            "additionalProperties": False,
            "required": ["value", "unit"],
            "properties": {
                "value": {"type": "number"},
                "unit": {"type": "string"},
                "window_h": {"type": ["number", "null"]},
            },
        },
        # The threshold echo (which gate cleared, with the numbers in it) —
        # never advice, never a forecast.
        "gate": {"type": "string"},
        "context": {"type": "object",
                    "additionalProperties": {"type": ["number", "string", "null"]}},
        "source_panel": {"type": "string"},
        "source_as_of": {"type": ["string", "null"]},
        "sources": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["panel"],
            "properties": {"panel": {"type": "string"},
                           "as_of": {"type": ["string", "null"]}},
        }},
        "drill": _DRILL,
    },
}

_WATCH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["check", "status", "detail", "source_panel", "as_of"],
    "properties": {
        "check": {"type": "string"},
        "status": {"type": "string", "enum": ["clear", "firing", "gap"]},
        "detail": {"type": "string"},
        "source_panel": {"type": "string"},
        "as_of": {"type": ["string", "null"]},
    },
}

_FLOW = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["net_per_hour", "window_h"],
    "properties": {
        "net_per_hour": {"type": "number"},
        "window_h": {"type": ["number", "null"]},
    },
}

_TRANSFER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["out", "in"],
    "properties": {
        "out": _PLAYER_REF,
        "in": _PLAYER_REF,
        "price_delta": {"type": ["number", "null"]},
        # price_radar's observed flow for the two names, when the window
        # carries them — the solver card's PRICE rail; never a prediction.
        "out_flow": _FLOW,
        "in_flow": _FLOW,
    },
}

#: One losing alternative, summarised: names only in the label, numbers in the
#: solver's own currency. The full ranked table lives in transfer_plan.json.
_ALTERNATIVE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "objective", "hits"],
    "properties": {
        "summary": {"type": "string"},   # names built from sem_players, no advice
        "objective": {"type": ["number", "null"]},
        "hits": {"type": ["integer", "null"]},
    },
}

#: The chosen move's §5 hit verdict, copied verbatim from the artefact when
#: the recommendation was solved rank-aware; null otherwise (the surrogate
#: mode carries no rank state, and making one up would be worse than nothing).
_HIT_VERDICT = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "properties": {
        "label": {"type": ["string", "null"]},
        "hits": {"type": ["integer", "null"]},
        "hit_points": {"type": ["integer", "null"]},
        "expected_gain": {"type": ["number", "null"]},
        "breakeven_gain": {"type": ["number", "null"]},
        "justified": {"type": ["boolean", "null"]},
    },
}

#: The transfer plan `fpl recommend` committed, rendered for the solver card.
#: Every number is the solver's own; the currency is named by objective_mode
#: (the expected_points surrogate today) and gain_over_roll is quoted vs the
#: solved roll in THAT currency — never summed with consensus/market numbers.
_PLAN = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["generated_at", "gw", "horizon_gws", "objective_mode",
                 "moves", "is_roll"],
    "properties": {
        "generated_at": {"type": ["string", "null"]},
        "age_hours": {"type": ["number", "null"]},
        "gw": {"type": ["integer", "null"]},
        "horizon_gws": {"type": "array", "items": {"type": "integer"}},
        "objective_mode": {"type": ["string", "null"]},
        "free_transfers": {"type": ["integer", "null"]},
        "unlimited_transfers": {"type": ["boolean", "null"]},
        "gain_over_roll": {"type": ["number", "null"]},
        "hits": {"type": ["integer", "null"]},
        "hit_points": {"type": ["integer", "null"]},
        "chip": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "bounds": {"type": ["string", "null"]},
        "solve_seconds": {"type": ["number", "null"]},
        # Parsed from the solver's own notes ("31.66% optimality gap") —
        # first-class so the UI prints it BESIDE gain_over_roll, never in a
        # fold. Null = the solve closed within tolerance (no gap note).
        "optimality_gap_pct": {"type": ["number", "null"]},
        # n_transfers == 0: banking the transfer IS the recommendation.
        "is_roll": {"type": "boolean"},
        "moves": {"type": "array", "items": _TRANSFER},
        "captain": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "your_captain": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "alternatives": {"type": "array", "items": _ALTERNATIVE},
        "hit_verdict": _HIT_VERDICT,
    },
}

_SOLVE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["state"],
    "properties": {
        "state": {"type": "string",
                  "enum": ["fresh", "aging", "stale", "missing"]},
        "reason": {"type": ["string", "null"]},
        "generated_at": {"type": ["string", "null"]},
        "age_hours": {"type": ["number", "null"]},
        "last_deadline_utc": {"type": ["string", "null"]},
        "next_deadline_utc": {"type": ["string", "null"]},
        # Null unless the plan is renderable (fresh/aging): a stale plan's
        # moves were priced against a squad you no longer have and must not
        # render as a recommendation.
        "plan": _PLAN,
    },
}

_NUMBERS = {"type": "object",
            "additionalProperties": {"type": ["number", "null"]}}

_SWAP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["in", "out", "numbers"],
    "properties": {
        "in": _PLAYER_REF,      # the bench player who starts
        "out": _PLAYER_REF,     # the starter who sits
        "numbers": _NUMBERS,    # bench_xpts / starter_xpts / swing, verbatim
    },
}

#: The bench_inversion + captain rules APPLIED, server-side: the same pairwise
#: swaps the old alert named, plus the captain measures, as a lineup the pitch
#: can render. Numbers are squad_overview's own — never recomputed.
_SUGGESTED = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["swaps", "n_changes", "xi_codes", "bench_codes",
                 "captain", "your_captain", "source_panel", "source_as_of"],
    "properties": {
        "reason": {"type": ["string", "null"]},   # why swaps/captain are absent
        "swaps": {"type": "array", "items": _SWAP},
        "n_changes": {"type": "integer"},
        "xi_codes": {"type": "array", "items": {"type": "integer"}},
        "bench_codes": {"type": "array", "items": {"type": "integer"}},
        "captain": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "captain_by_haul": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "captain_by_mean": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "captain_numbers": _NUMBERS,   # both measures printed, never blended
        "your_captain": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "swap_delta_xpts": {"type": ["number", "null"]},
        "captain_delta_xpts": {"type": ["number", "null"]},
        "total_delta_xpts": {"type": ["number", "null"]},
        "source_panel": {"type": "string"},
        "source_as_of": {"type": ["string", "null"]},
    },
}

#: One club's next fixture + horizon ranks, copied field-for-field from
#: fixture_board's opponent_only lens — the pitch's opponent chips and the
#: solver card's why-line read this, never a re-derived difficulty.
_NEXT_FIX = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["gw", "label", "opponent", "opponent_code", "is_home"],
    "properties": {
        "gw": {"type": "integer"},
        "label": {"type": "string"},          # CAPS home / lower away
        "opponent": {"type": "string"},
        "opponent_code": {"type": "integer"},
        "is_home": {"type": "boolean"},
        "kickoff_utc": {"type": ["string", "null"]},
        "attack_ease": {"type": ["number", "null"]},
        "defence_ease": {"type": ["number", "null"]},
        "attack_rank": {"type": ["integer", "null"]},
        "defence_rank": {"type": ["integer", "null"]},
        "unavailable": {"type": ["string", "null"]},
    },
}

_TEAMFIX = {
    "type": "object",
    "additionalProperties": False,
    "required": ["team_code", "short_name", "next"],
    "properties": {
        "team_code": {"type": "integer"},
        "short_name": {"type": "string"},
        "next": _NEXT_FIX,                    # null = blank next gameweek
        "next_gw_labels": {"type": "array", "items": {"type": "string"}},
        "labels": {"type": "array", "items": {"type": "string"}},
        "horizon_attack_rank": {"type": ["integer", "null"]},
        "horizon_defence_rank": {"type": ["integer", "null"]},
        # THE gameweeks the horizon ranks were computed over — fixture_board's
        # own window, carried so no rank ever prints without its horizon.
        "horizon_gws": {"type": "array", "items": {"type": "integer"}},
    },
}

#: Per-squad-player minutes columns from the provider consensus at the next
#: GW: xmins when any source serves it, p_appear alongside. Both nullable —
#: the view labels whichever exists and never fabricates minutes.
_SQPROJ = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code"],
    "properties": {
        "code": {"type": "integer"},
        "xmins": {"type": ["number", "null"]},
        "p_appear": {"type": ["number", "null"]},
        "n_sources": {"type": ["integer", "null"]},
    },
}

_SOURCE_CHIP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["panel"],
    "properties": {"panel": {"type": "string"},
                   "as_of": {"type": ["string", "null"]}},
}

#: A rule-based move suggestion. NO free text: rule id + numbers; wording
#: lives in the view's templates. Every number is a shared-helper quantity.
_MOVE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["rule", "in", "out", "numbers", "sources"],
    "properties": {
        "rule": {"type": "string", "enum": ["coverage_gap", "form_upgrade"]},
        "in": _PLAYER_REF,
        "out": _PLAYER_REF,
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "numbers": _NUMBERS,
        "gws": {"type": "array", "items": {"type": "integer"}},
        # The gameweeks any quoted fixture/attack rank was computed over —
        # fixture_board's own horizon window, so a move card can never quote
        # "#1 easiest run" without saying over WHICH gameweeks. Empty when the
        # rule quotes no rank (form_upgrade).
        "rank_gws": {"type": "array", "items": {"type": "integer"}},
        "sources": {"type": "array", "items": _SOURCE_CHIP},
        "drill": _DRILL,
    },
}

#: One dissenting voice on a verdict line: a voice id + rule id + the numbers
#: in that voice's OWN currency. Displayed beside the pick, never summed into
#: it. No free text — wording lives in view templates keyed by voice/rule.
_DISSENT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["voice", "rule", "numbers", "source_panel"],
    "properties": {
        "voice": {"type": "string",
                  "enum": ["mean_xpts", "haul_odds", "creator_armband",
                           "rule_moves", "solver"]},
        "rule": {"type": "string"},                 # rule id, never prose
        "player": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "in": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "out": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "numbers": _NUMBERS,
        "source_panel": {"type": "string"},
        "source_as_of": {"type": ["string", "null"]},
        "drill": _DRILL,
    },
}

#: One verdict line: WHICH question, WHICH named precedence rule produced the
#: pick (an id keyed to view templates — the house rule stands: no free-text
#: recommendation field), the pick as structured refs/numbers, and every
#: dissenting voice as data. Each line drills to its evidence card.
_VERDICT_LINE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["question", "rule", "numbers", "dissent",
                 "source_panel", "source_as_of"],
    "properties": {
        "question": {"type": "string",
                     "enum": ["transfer", "captain", "bench", "chip"]},
        "rule": {"type": "string",
                 "enum": ["solver_plan", "solver_roll",
                          "rule_moves_solver_stale",
                          "rule_moves_solver_missing", "no_move_named",
                          "solver_plan_captain", "mean_xpts_captain",
                          "no_captain_named",
                          "bench_inversion_applied", "bench_confirmed",
                          "no_bench_named",
                          "solver_plan_chip", "chip_hold", "no_chip_named"]},
        # The solve state behind the pick — printed ON the line, so a verdict
        # produced under a stale/missing plan can never render as fresh.
        "state": {"type": ["string", "null"]},
        "pick": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "moves": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["out", "in"],
            "properties": {"out": _PLAYER_REF, "in": _PLAYER_REF},
        }},
        "chip": {"type": ["string", "null"]},       # null = hold
        "numbers": _NUMBERS,
        "dissent": {"type": "array", "items": _DISSENT},
        "source_panel": {"type": "string"},
        "source_as_of": {"type": ["string", "null"]},
        "drill": _DRILL,
    },
}

#: The verdict block. `precedence` is the PRECEDENCE constant echoed verbatim
#: (schema-pinned with const) — the printed deterministic rule the lines were
#: produced by, disclosed on the card, never paraphrased warehouse-side.
_VERDICT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["precedence", "lines"],
    "properties": {
        "precedence": {"type": "string", "const": PRECEDENCE},
        "lines": {"type": "array", "items": _VERDICT_LINE,
                  "minItems": 4, "maxItems": 4},
    },
}

#: Header stats: the budget of the whole decision (free transfers, bank) and
#: the chip verdict, surfaced top-level instead of buried in the solver card.
#: Every number names its source clock; a stale plan's FT count says so.
_HEADER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["free_transfers", "chip"],
    "properties": {
        "free_transfers": {"type": ["integer", "null"]},
        "free_transfers_as_of": {"type": ["string", "null"]},
        # The solve state the count was read under — "stale" means the count
        # predates the last deadline and must render flagged, never bare.
        "free_transfers_state": {"type": ["string", "null"]},
        "bank_tenths": {"type": ["integer", "null"]},
        "chip": {"type": ["string", "null"]},       # null = hold
        "chip_rule": {"type": ["string", "null"]},
        "chip_state": {"type": ["string", "null"]},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "gw", "entry_id", "as_of", "sources_as_of",
                 "thresholds", "alerts", "tiles", "suppressed_counts",
                 "empty_kinds", "watch_log", "solve", "suggested_xi",
                 "team_fixtures", "squad_projection", "moves",
                 "moves_suppressed", "fixtures_scale", "verdict", "header",
                 "projection_generated"],
    "properties": {
        "season": {"type": "string"},
        "gw": {"type": ["integer", "null"]},
        "entry_id": {"type": "integer"},
        "as_of": {"type": ["string", "null"]},
        "sources_as_of": {"type": "object",
                          "additionalProperties": {"type": ["string", "null"]}},
        "deadline_utc": {"type": ["string", "null"]},
        "xi_median_xpts": {"type": ["number", "null"]},
        "thresholds": {"type": "object",
                       "additionalProperties": {"type": "number"}},
        "alerts": {"type": "array", "items": _ALERT},
        "tiles": {"type": "array", "items": _TILE},
        "suppressed_counts": {"type": "object",
                              "additionalProperties": {"type": "integer"}},
        "empty_kinds": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "reason"],
            "properties": {"kind": {"type": "string"},
                           "reason": {"type": "string"}},
        }},
        "watch_log": {"type": "array", "items": _WATCH},
        "solve": _SOLVE,
        "suggested_xi": _SUGGESTED,
        "team_fixtures": {"type": "array", "items": _TEAMFIX},
        "squad_projection": {"type": "array", "items": _SQPROJ},
        "projection_gw": {"type": ["integer", "null"]},
        "moves": {"type": "array", "items": _MOVE},
        "moves_suppressed": {"type": "integer"},
        "verdict": _VERDICT,
        "header": _HEADER,
        # The DATA-BIRTH instant of the solved projection artefact behind the
        # squad card's xPts / p_haul (the model RUN, not the panel read time),
        # plus what that source actually is — so no view can caption the
        # solved numbers "(CONSENSUS)" again.
        "projection_generated": {"type": ["string", "null"]},
        "projection_source": {"type": ["string", "null"]},
        "fixtures_scale": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["available"],
            "properties": {
                "available": {"type": "boolean"},
                "domain": {"type": ["array", "null"],
                           "items": {"type": "number"}},
                "unit": {"type": ["string", "null"]},
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def _iso(v: Any) -> str | None:
    if v is None:
        return None
    return str(v).replace(" ", "T")


def _parse_ts(s: Any) -> dt.datetime | None:
    if s is None:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace(" ", "T"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def _ref(p: dict[str, Any]) -> dict[str, Any]:
    """A player reference built from a source panel's own row — data, not prose."""
    return {
        "code": int(p["code"]),
        "name": str(p.get("name") or p["code"]),
        "pos": p.get("pos"),
        "team": p.get("team"),
        "team_code": p.get("team_code"),
        "price": p.get("price"),
        "own_pct": p.get("own_pct"),
    }


def dashboard_brief(wh, *, season: str, entry_id: int | None = None) -> dict[str, Any]:
    """Select-and-threshold over the source panels; one payload, one clock set."""
    now = dt.datetime.now(UTC)
    eid = int(entry_id) if entry_id is not None else int(USER.entry_id)

    sources_as_of: dict[str, str | None] = {}
    alerts: list[dict[str, Any]] = []
    tiles: list[tuple[float, dict[str, Any]]] = []   # (gate margin, tile)
    watch: list[dict[str, Any]] = []
    empties: list[dict[str, Any]] = []
    notes: list[str] = []

    # ---- calendar --------------------------------------------------------
    g_next = next_gw(wh, season, now)
    deadlines = q(
        wh,
        "SELECT gw, deadline_utc FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, gw ORDER BY as_of DESC) rn"
        "  FROM dim_event WHERE season = ?"
        ") WHERE rn = 1 ORDER BY deadline_utc",
        (season,),
    )
    if deadlines.empty:
        return empty(
            f"No {season} events in the warehouse — without deadlines the "
            f"brief cannot date a single claim. Run `make ingest` first."
        )
    next_deadline = None
    last_deadline = None
    for _, r in deadlines.iterrows():
        d = _parse_ts(r["deadline_utc"])
        if d is None:
            continue
        if d > now and next_deadline is None:
            next_deadline = d
        if d <= now:
            last_deadline = d

    # ---- source panels (each failure degrades its checks, never the page) --
    def call(name, fn, **kw):
        try:
            res = fn(wh, season=season, **kw)
        except Exception as exc:  # noqa: BLE001 - a brief reports, it does not crash
            res = {"empty": True,
                   "reason": f"{name} raised {type(exc).__name__}: {exc}"}
        if not res.get("empty"):
            sources_as_of[name] = _iso(res.get("as_of"))
        return res

    sq = call("squad_overview", squad_overview, entry_id=eid)
    pr = call("price_radar", price_radar, limit=200)
    own = call("ownership_eo", ownership_eo)

    def gap_alert(panel: str, reason: str) -> None:
        alerts.append({
            "rule": "source_gap", "kind": "GAP", "priority": 0,
            "codes": [], "players": [], "numbers": {},
            "news": None, "status": None, "reason": reason,
            "source_panel": panel, "source_as_of": None,
            "drill": {"tab": "pipelines"},
        })

    # ---- squad-derived checks -------------------------------------------
    xi_median = None
    starters: list[dict[str, Any]] = []
    bench: list[dict[str, Any]] = []
    squad15: list[dict[str, Any]] = []
    suggested_xi: dict[str, Any] | None = None
    if sq.get("empty"):
        gap_alert("squad_overview", str(sq.get("reason")))
        for check in ("squad_flags", "bench_order", "captaincy"):
            watch.append({"check": check, "status": "gap",
                          "detail": str(sq.get("reason"))[:200],
                          "source_panel": "squad_overview", "as_of": None})
    else:
        starters = list(sq.get("starters") or [])
        bench = list(sq.get("bench") or [])
        squad15 = starters + bench
        sq_as_of = _iso(sq.get("as_of"))
        xi_x = [p["xpts"] for p in starters if p.get("xpts") is not None]
        if len(xi_x) >= 6:
            xi_median = round(statistics.median(xi_x), 3)

        # availability: my squad's own flags, FPL status/news verbatim
        flagged = [p for p in squad15 if p.get("status") in ("i", "s", "d", "u")]
        for p in flagged:
            alerts.append({
                "rule": "availability", "kind": "AVAILABILITY", "priority": 0,
                "codes": [p["code"]], "players": [_ref(p)],
                "numbers": {}, "news": p.get("news") or None,
                "status": p.get("status"), "reason": None,
                "source_panel": "squad_overview", "source_as_of": sq_as_of,
                "drill": {"drawer": p["code"]},
            })
        watch.append({
            "check": "squad_flags",
            "status": "firing" if flagged else "clear",
            "detail": f"{len(flagged)} of {len(squad15)} flagged",
            "source_panel": "squad_overview", "as_of": sq_as_of,
        })

        # bench inversion: best bench vs weakest same-position starter,
        # APPLIED as suggested swaps rather than argued as an alert row.
        # Same-position swaps are always formation-legal. A starter already
        # swapped out cannot be swapped out twice, so the pairs compose into
        # one renderable lineup.
        inversions = 0
        margin = float(THRESHOLDS["bench_margin_xpts"])
        swaps: list[dict[str, Any]] = []
        swapped_out: set[int] = set()
        for b in bench:
            if b.get("xpts") is None:
                continue
            same = [s for s in starters
                    if s.get("pos") == b.get("pos") and s.get("xpts") is not None
                    and s["code"] not in swapped_out]
            if not same:
                continue
            weakest = min(same, key=lambda s: s["xpts"])
            swing = round(float(b["xpts"]) - float(weakest["xpts"]), 3)
            if swing >= margin:
                inversions += 1
                swapped_out.add(weakest["code"])
                swaps.append({
                    "in": _ref(b), "out": _ref(weakest),
                    "numbers": {"bench_xpts": b["xpts"],
                                "starter_xpts": weakest["xpts"],
                                "swing": swing},
                })
        best_swing = max((s["numbers"]["swing"] for s in swaps), default=None)
        watch.append({
            "check": "bench_order",
            "status": "firing" if inversions else "clear",
            "detail": (f"{inversions} inversion(s), best swing +{best_swing} "
                       f"— applied in the suggested XI"
                       if inversions else
                       f"no bench player beats his starter by ≥ {margin} xPts"),
            "source_panel": "squad_overview", "as_of": sq_as_of,
        })

        # the suggested lineup: the swaps above applied in place
        by_out_code = {s["out"]["code"]: s["in"]["code"] for s in swaps}
        by_in_code = {s["in"]["code"]: s["out"]["code"] for s in swaps}
        xi_codes = [by_out_code.get(p["code"], p["code"]) for p in starters]
        bench_codes = [by_in_code.get(p["code"], p["code"]) for p in bench]
        sq_by_code_all = {p["code"]: p for p in squad15}
        xi_players = [sq_by_code_all[c] for c in xi_codes]

        # captaincy: two measures over the SUGGESTED XI, both printed, never
        # blended. The suggestion takes the mean-xPts pick (the pitch's own
        # currency); the haul-odds pick is served beside it, disagreement
        # visible, not averaged away.
        cap = next((p for p in squad15 if p.get("is_captain")), None)
        with_ph = [p for p in xi_players if p.get("p_haul") is not None]
        with_x = [p for p in xi_players if p.get("xpts") is not None]
        sug_cap = by_haul = by_mean = None
        cap_numbers: dict[str, Any] = {}
        if cap and with_ph and with_x:
            by_haul = max(with_ph, key=lambda p: p["p_haul"])
            by_mean = max(with_x, key=lambda p: p["xpts"])
            agree = by_haul["code"] == by_mean["code"] == cap["code"]
            sug_cap = by_mean
            cap_numbers = {
                "haul_pick_p_haul": by_haul["p_haul"],
                "haul_pick_xpts": by_haul.get("xpts"),
                "mean_pick_xpts": by_mean["xpts"],
                "mean_pick_p_haul": by_mean.get("p_haul"),
                "captain_xpts": cap.get("xpts"),
                "captain_p_haul": cap.get("p_haul"),
            }
            watch.append({
                "check": "captaincy",
                "status": "clear" if agree else "firing",
                "detail": (f"both measures name {cap['name']}" if agree else
                           f"haul odds: {by_haul['name']} · mean: "
                           f"{by_mean['name']} · armband: {cap['name']}"),
                "source_panel": "squad_overview", "as_of": sq_as_of,
            })
        else:
            watch.append({
                "check": "captaincy", "status": "gap",
                "detail": "no projection artefact cached — p_haul and xPts "
                          "are null (run `make solve`)",
                "source_panel": "squad_overview", "as_of": sq_as_of,
            })

        swap_delta = (round(sum(float(s["numbers"]["swing"]) for s in swaps), 2)
                      if swaps else 0.0)
        cap_delta = None
        if sug_cap is not None and cap is not None:
            cap_delta = (0.0 if sug_cap["code"] == cap["code"]
                         else (round(float(sug_cap["xpts"])
                                     - float(cap["xpts"]), 2)
                               if cap.get("xpts") is not None else None))
        suggested_xi = {
            "reason": ("no projection artefact cached — the bench and "
                       "captain rules cannot rank players (run `make solve`)"
                       if all(p.get("xpts") is None for p in squad15)
                       else None),
            "swaps": swaps,
            "n_changes": len(swaps),
            "xi_codes": xi_codes,
            "bench_codes": bench_codes,
            "captain": _ref(sug_cap) if sug_cap else None,
            "captain_by_haul": _ref(by_haul) if by_haul else None,
            "captain_by_mean": _ref(by_mean) if by_mean else None,
            "captain_numbers": cap_numbers,
            "your_captain": _ref(cap) if cap else None,
            "swap_delta_xpts": swap_delta,
            "captain_delta_xpts": cap_delta,
            "total_delta_xpts": (round(swap_delta + (cap_delta or 0.0), 2)
                                 if swap_delta is not None else None),
            "source_panel": "squad_overview",
            "source_as_of": sq_as_of,
        }

    squad_codes = {p["code"] for p in squad15}
    squad_team_codes = {p.get("team_code") for p in squad15
                       if p.get("team_code") is not None}

    # ---- price flow (owned falls = alerts; named-target rises = tiles) ----
    watch_targets: set[int] = set()
    try:
        wl = q(wh, "SELECT DISTINCT code FROM watchlist "
                   "WHERE season = ? AND NOT resolved", (season,))
        watch_targets = {int(c) for c in wl["code"]} if not wl.empty else set()
    except Exception:  # noqa: BLE001 - watchlist may not exist in a fresh db
        watch_targets = set()

    # The transfer plan `fpl recommend` committed — the solver card's artefact
    # AND the source of the price radar's solver-named targets (the chosen
    # buys plus every alternative's buys).
    tplan_named: set[int] = set()
    tplan_path = Path(source_dir(wh)) / TRANSFER_PLAN_NAME
    tplan: dict[str, Any] | None = None
    if tplan_path.exists():
        try:
            tplan = json.loads(tplan_path.read_text())
            tplan_named = {int(c)
                           for c in (tplan.get("chosen") or {}).get("in", [])}
            for alt in tplan.get("alternatives") or []:
                tplan_named |= {int(c) for c in alt.get("in", [])}
        except (OSError, json.JSONDecodeError) as exc:
            notes.append(f"transfer plan artefact unreadable: "
                         f"{type(exc).__name__}: {exc}")
            tplan = None

    if pr.get("empty"):
        gap_alert("price_radar", str(pr.get("reason")))
        for check in ("owned_price_flow", "price_targets"):
            watch.append({"check": check, "status": "gap",
                          "detail": str(pr.get("reason"))[:200],
                          "source_panel": "price_radar", "as_of": None})
    else:
        pr_as_of = _iso(pr.get("as_of"))
        window_h = float((pr.get("window") or {}).get("hours") or 0)
        fall_thr = float(THRESHOLDS["own_fall_net_hr"])
        rise_thr = float(THRESHOLDS["target_rise_net_hr"])

        owned_falls = [r for r in pr.get("fallers", [])
                       if r["code"] in squad_codes and r["net_per_hour"] <= fall_thr]
        for r in owned_falls:
            alerts.append({
                "rule": "own_price_fall", "kind": "MARKET", "priority": 1,
                "codes": [r["code"]], "players": [_ref(r)],
                "numbers": {"net": r["net"], "net_per_hour": r["net_per_hour"],
                            "window_h": window_h},
                "news": None, "status": None, "reason": None,
                "source_panel": "price_radar", "source_as_of": pr_as_of,
                "drill": {"drawer": r["code"]},
            })
        watch.append({
            "check": "owned_price_flow",
            "status": "firing" if owned_falls else "clear",
            "detail": (" · ".join(f"{r['name']} {r['net_per_hour']:+,.0f}/hr"
                                  for r in owned_falls)
                       if owned_falls else
                       f"no owned player past {fall_thr:+,.0f}/hr in the "
                       f"{window_h}h window"),
            "source_panel": "price_radar", "as_of": pr_as_of,
        })

        named = watch_targets | tplan_named
        target_rises = [r for r in pr.get("risers", [])
                        if r["code"] in named and r["code"] not in squad_codes
                        and r["net_per_hour"] >= rise_thr]
        for r in target_rises:
            tiles.append((3, r["net_per_hour"] - rise_thr, {
                "kind": "price_rise_target", "priority": 3,
                "code": r["code"], "player": _ref(r),
                "team_code": None, "team": r.get("team"),
                "number": {"value": r["net_per_hour"], "unit": "net/hr",
                           "window_h": window_h},
                "gate": f"watchlist/solver-named ≥ {rise_thr:+,.0f}/hr "
                        f"(observed flow, not a predicted change)",
                "context": {"net": r["net"]},
                "source_panel": "price_radar", "source_as_of": pr_as_of,
                "sources": [{"panel": "price_radar", "as_of": pr_as_of}],
                "drill": {"drawer": r["code"]},
            }))
        watch.append({
            "check": "price_targets",
            "status": "firing" if target_rises else "clear",
            "detail": (f"{len(target_rises)} named target(s) past "
                       f"{rise_thr:+,.0f}/hr"
                       if target_rises else
                       f"no watchlist/solver-named player past "
                       f"{rise_thr:+,.0f}/hr ({len(named)} names watched)"),
            "source_panel": "price_radar", "as_of": pr_as_of,
        })

    # ---- ownership gates (template gap, differential) --------------------
    if own.get("empty"):
        gap_alert("ownership_eo", str(own.get("reason")))
        for check in ("template_gaps", "differentials"):
            watch.append({"check": check, "status": "gap",
                          "detail": str(own.get("reason"))[:200],
                          "source_panel": "ownership_eo", "as_of": None})
    else:
        own_as_of = _iso(own.get("as_of"))
        t_thr = float(THRESHOLDS["template_own_pct"])
        d_thr = float(THRESHOLDS["diff_own_pct"])
        d_margin = float(THRESHOLDS["diff_xpts_margin"])
        bank = (sq.get("bank_tenths") or 0) / 10.0 if not sq.get("empty") else None

        def affordable(row) -> bool:
            """Within bank + one same-position sale — sale at current price,
            the same simplification the planner states."""
            if bank is None or row.get("price") is None:
                return True   # squad unreadable: affordability cannot gate
            same = [p.get("price") or 0.0 for p in squad15
                    if p.get("pos") == row.get("pos")]
            return bool(same) and (bank + max(same)) >= float(row["price"])

        gaps_found = []
        for r in own.get("rows", []):
            if (r.get("own_pct") is not None and r["own_pct"] >= t_thr
                    and r.get("in_squad") is False and affordable(r)):
                gaps_found.append(r)
        for r in gaps_found:
            tiles.append((3, float(r["own_pct"]) - t_thr, {
                "kind": "template_gap", "priority": 3,
                "code": r["code"], "player": _ref(r),
                "team_code": r.get("team_code"), "team": r.get("team"),
                "number": {"value": r["own_pct"], "unit": "own%",
                           "window_h": None},
                "gate": f"own% ≥ {t_thr:.0f}, unowned, affordable within "
                        f"bank + one same-position sale",
                "context": {"price": r.get("price"), "xpts": r.get("xpts")},
                "source_panel": "ownership_eo", "source_as_of": own_as_of,
                "sources": [{"panel": "ownership_eo", "as_of": own_as_of}],
                "drill": {"tab": "template"},
            }))
        watch.append({
            "check": "template_gaps",
            "status": "firing" if gaps_found else "clear",
            "detail": (f"{len(gaps_found)} unowned player(s) ≥ {t_thr:.0f}% owned"
                       if gaps_found else f"none ≥ {t_thr:.0f}% threshold"),
            "source_panel": "ownership_eo", "as_of": own_as_of,
        })

        diffs_found = []
        if xi_median is not None:
            pool = {r["code"]: r for r in own.get("differentials", [])}
            for r in own.get("rows", []):
                pool.setdefault(r["code"], r)
            for r in pool.values():
                if (r.get("own_pct") is not None and r["own_pct"] <= d_thr
                        and r.get("xpts") is not None
                        and r["xpts"] >= xi_median + d_margin
                        and r["code"] not in squad_codes):
                    diffs_found.append(r)
            for r in diffs_found:
                tiles.append((3, float(r["xpts"]) - (xi_median + d_margin), {
                    "kind": "differential", "priority": 3,
                    "code": r["code"], "player": _ref(r),
                    "team_code": r.get("team_code"), "team": r.get("team"),
                    "number": {"value": r["xpts"], "unit": "xPts next GW",
                               "window_h": None},
                    "gate": f"own% ≤ {d_thr:.0f} and next-GW xPts ≥ XI median "
                            f"{xi_median} + {d_margin} — two gates, two "
                            f"sources, numbers never combined",
                    "context": {"own_pct": r.get("own_pct"),
                                "xi_median": xi_median},
                    "source_panel": "ownership_eo", "source_as_of": own_as_of,
                    "sources": [
                        {"panel": "ownership_eo", "as_of": own_as_of},
                        {"panel": "squad_overview",
                         "as_of": sources_as_of.get("squad_overview")},
                    ],
                    "drill": {"drawer": r["code"]},
                }))
            watch.append({
                "check": "differentials",
                "status": "firing" if diffs_found else "clear",
                "detail": (f"{len(diffs_found)} cleared both gates"
                           if diffs_found else
                           f"none ≤ {d_thr:.0f}% owned with xPts ≥ XI median "
                           f"+ {d_margin}"),
                "source_panel": "ownership_eo", "as_of": own_as_of,
            })
        else:
            watch.append({
                "check": "differentials", "status": "gap",
                "detail": "XI median unavailable (squad or projections "
                          "missing) — the xPts gate cannot be evaluated",
                "source_panel": "ownership_eo", "as_of": own_as_of,
            })

    # ---- consensus standouts (projection semantic view) ------------------
    standout_count = 0
    cons_as_of = None
    if not sq.get("empty") and g_next is not None:
        try:
            gws4 = list(range(g_next, g_next + int(THRESHOLDS["standout_horizon_gws"])))
            cons = q(
                wh,
                "SELECT code, gw, xpts_mean FROM sem_projection_consensus(?) "
                "WHERE season = ? AND gw >= ? AND gw <= ?",
                (now, season, gws4[0], gws4[-1]),
            )
        except Exception as exc:  # noqa: BLE001
            cons = None
            watch.append({"check": "xpts_standouts", "status": "gap",
                          "detail": f"sem_projection_consensus: "
                                    f"{type(exc).__name__}: {exc}"[:200],
                          "source_panel": "projection_table", "as_of": None})
        if cons is not None and not cons.empty:
            cons_as_of = sources_as_of.get("squad_overview")
            covered = sorted(int(g) for g in cons["gw"].unique())
            sums = cons.groupby("code")["xpts_mean"].sum().to_dict()
            m_thr = float(THRESHOLDS["standout_margin_xpts"])
            players_df = q(
                wh,
                "SELECT code, web_name, position, team, team_code, price "
                "FROM sem_players(?) WHERE season = ?",
                (now, season),
            )
            pinfo = {int(r["code"]): r for _, r in players_df.iterrows()}
            weakest_by_pos: dict[str, tuple[dict[str, Any], float]] = {}
            for pos in ("GKP", "DEF", "MID", "FWD"):
                same = [s for s in starters if s.get("pos") == pos
                        and s["code"] in sums]
                if not same:
                    continue
                w = min(same, key=lambda s: sums[s["code"]])
                weakest_by_pos[pos] = (w, float(sums[w["code"]]))
            for code_, total in sorted(sums.items(), key=lambda kv: -kv[1]):
                code_ = int(code_)
                if code_ in squad_codes or code_ not in pinfo:
                    continue
                row = pinfo[code_]
                pos = POSITION_NAME.get(
                    int(row["position"]) if row["position"] == row["position"] else 0, "?")
                if pos not in weakest_by_pos:
                    continue
                weak, weak_sum = weakest_by_pos[pos]
                if float(total) >= weak_sum + m_thr:
                    standout_count += 1
                    tiles.append((3, float(total) - (weak_sum + m_thr), {
                        "kind": "xpts_standout", "priority": 3,
                        "code": code_,
                        "player": {"code": code_,
                                   "name": str(row["web_name"]),
                                   "pos": pos,
                                   "team": None if row["team"] is None else str(row["team"]),
                                   "team_code": None if row["team_code"] != row["team_code"]
                                                else int(row["team_code"]),
                                   "price": None if row["price"] != row["price"]
                                            else float(row["price"]),
                                   "own_pct": None},
                        "team_code": None, "team": None,
                        "number": {"value": round(float(total), 2),
                                   "unit": f"xPts GW{covered[0]}–{covered[-1]}",
                                   "window_h": None},
                        "gate": f"consensus Σ GW{covered[0]}–{covered[-1]} ≥ "
                                f"weakest {pos} starter ({weak['name']} "
                                f"{weak_sum:.1f}) + {m_thr:.1f}",
                        "context": {"weakest_starter_sum": round(weak_sum, 2),
                                    "weakest_starter": weak["name"]},
                        "source_panel": "projection_table",
                        "source_as_of": cons_as_of,
                        "sources": [{"panel": "projection_table",
                                     "as_of": cons_as_of}],
                        "drill": {"drawer": code_},
                    }))
            watch.append({
                "check": "xpts_standouts",
                "status": "firing" if standout_count else "clear",
                "detail": (f"{standout_count} non-owned player(s) ≥ weakest "
                           f"same-position starter + "
                           f"{THRESHOLDS['standout_margin_xpts']}"
                           if standout_count else
                           "no non-owned player clears the +3.0 margin"),
                "source_panel": "projection_table", "as_of": cons_as_of,
            })
        elif cons is not None:
            watch.append({
                "check": "xpts_standouts", "status": "gap",
                "detail": f"no consensus projections for GW{g_next}+ in the "
                          f"warehouse — ingest projections",
                "source_panel": "projection_table", "as_of": None,
            })

    # ---- fixture turns (split board, two windows, its own fields only) ----
    team_fixtures: list[dict[str, Any]] = []
    fixtures_scale: dict[str, Any] = {"available": False, "domain": None,
                                      "unit": None}
    try:
        near = fixture_board(wh, season=season, horizon=3, from_gw=g_next,
                             include_form=False, include_calibration=False)
        far = fixture_board(wh, season=season, horizon=3,
                            from_gw=(g_next + 3) if g_next else None,
                            include_form=False, include_calibration=False)
    except Exception as exc:  # noqa: BLE001
        near = {"empty": True,
                "reason": f"fixture_board raised {type(exc).__name__}: {exc}"}
        far = near
    if near.get("empty"):
        watch.append({"check": "fixture_turns", "status": "gap",
                      "detail": str(near.get("reason"))[:200],
                      "source_panel": "fixture_board", "as_of": None})
    else:
        fb_as_of = _iso(near.get("as_of"))
        sources_as_of.setdefault("fixture_board", fb_as_of)

        # team_fixtures: the near board's opponent_only lens, copied verbatim
        # per club — the pitch's opponent chips and the solver why-line render
        # from this; no ease is ever re-derived here.
        sc = near.get("scale") or {}
        fixtures_scale = {
            "available": bool(sc.get("available")),
            "domain": ([float(x) for x in sc["domain"]]
                       if sc.get("domain") else None),
            "unit": sc.get("unit"),
        }
        first_gw = (near.get("gws") or [None])[0]
        for t in near.get("teams", []):
            nxt = None
            next_labels: list[str] = []
            all_labels: list[str] = []
            for slot in t.get("fixtures", []):
                for o in slot.get("opponents", []):
                    all_labels.append(str(o["label"]))
                    if first_gw is not None and int(slot["gw"]) == int(first_gw):
                        next_labels.append(str(o["label"]))
                        if nxt is None:
                            oo = o.get("opponent_only") or {}
                            nxt = {
                                "gw": int(slot["gw"]),
                                "label": str(o["label"]),
                                "opponent": str(o["opponent"]),
                                "opponent_code": int(o["opponent_code"]),
                                "is_home": bool(o["is_home"]),
                                "kickoff_utc": o.get("kickoff_utc"),
                                "attack_ease": oo.get("attack_ease"),
                                "defence_ease": oo.get("defence_ease"),
                                "attack_rank": oo.get("attack_rank"),
                                "defence_rank": oo.get("defence_rank"),
                                "unavailable": oo.get("unavailable"),
                            }
            h = t.get("horizon") or {}
            team_fixtures.append({
                "team_code": int(t["team_code"]),
                "short_name": str(t["short_name"]),
                "next": nxt,
                "next_gw_labels": next_labels,
                "labels": all_labels,
                "horizon_attack_rank": h.get("attack_rank"),
                "horizon_defence_rank": h.get("defence_rank"),
                # the board's own window — a rank never travels without it
                "horizon_gws": [int(g) for g in near.get("gws") or []],
            })

        if far.get("empty"):
            # the near window still feeds team_fixtures and the pitch; only
            # the near-vs-far turn comparison is a gap
            watch.append({"check": "fixture_turns", "status": "gap",
                          "detail": ("far window empty: "
                                     + str(far.get("reason")))[:200],
                          "source_panel": "fixture_board",
                          "as_of": fb_as_of})
        else:
            move_thr = int(THRESHOLDS["fixture_rank_move"])
            top_eo_teams = set()
            if not own.get("empty"):
                for r in (own.get("rows") or [])[:10]:
                    if r.get("team_code") is not None:
                        top_eo_teams.add(r["team_code"])
            relevant = squad_team_codes | top_eo_teams
            far_by = {t.get("team_code"): t for t in far.get("teams", [])}
            turns = 0
            near_gws = near.get("gws") or []
            far_gws = far.get("gws") or []
            for t in near.get("teams", []):
                tc = t.get("team_code")
                if tc not in relevant or tc not in far_by:
                    continue
                h1 = t.get("horizon") or {}
                h2 = far_by[tc].get("horizon") or {}
                for axis in ("attack_rank", "defence_rank"):
                    r1, r2 = h1.get(axis), h2.get(axis)
                    if r1 is None or r2 is None:
                        continue
                    move = int(r1) - int(r2)
                    if abs(move) >= move_thr:
                        turns += 1
                        tiles.append((4, abs(move) - move_thr, {
                            "kind": "fixture_turn", "priority": 4,
                            "code": None, "player": None,
                            "team_code": tc, "team": t.get("short_name"),
                            "number": {"value": move, "unit": "places",
                                       "window_h": None},
                            "gate": f"{axis.replace('_', ' ')} moves ≥ {move_thr} "
                                    f"places: {r1} (GW{near_gws[0]}–{near_gws[-1]})"
                                    f" → {r2} (GW{far_gws[0]}–{far_gws[-1]}) — "
                                    f"split panel's own ranks, never a blended "
                                    f"difficulty",
                            "context": {"axis": axis, "rank_near": r1,
                                        "rank_far": r2},
                            "source_panel": "fixture_board",
                            "source_as_of": fb_as_of,
                            "sources": [{"panel": "fixture_board",
                                         "as_of": fb_as_of}],
                            "drill": {"tab": "fixtures"},
                        }))
            watch.append({
                "check": "fixture_turns",
                "status": "firing" if turns else "clear",
                "detail": (f"{turns} run(s) turning ≥ {move_thr} places among "
                           f"your/top-EO clubs"
                           if turns else
                           f"no horizon rank move ≥ {move_thr} places among "
                           f"{len(relevant)} relevant club(s)"),
                "source_panel": "fixture_board", "as_of": fb_as_of,
            })

    # ---- squad minutes columns (provider consensus, next GW) -------------
    # xmins when any provider serves it, p_appear beside it — the same
    # semantic views and the same rounding projection_table uses, so the
    # pitch's minutes chip equals the projections tab's column exactly.
    squad_projection: list[dict[str, Any]] = []
    proj_as_of: str | None = None
    cons_next: dict[int, dict[str, Any]] = {}
    if g_next is not None:
        cdf = adf = None
        try:
            cdf = q(
                wh,
                "SELECT code, xpts_mean, xmins_mean, n_sources "
                "FROM sem_projection_consensus(?) WHERE season = ? AND gw = ?",
                (now, season, g_next),
            )
            adf = q(
                wh,
                "SELECT code, AVG(p_appear) AS p_appear, "
                "MAX(fetched_at) AS fetched FROM sem_projections(?) "
                "WHERE season = ? AND gw = ? AND xpts IS NOT NULL "
                "GROUP BY code",
                (now, season, g_next),
            )
        except Exception as exc:  # noqa: BLE001
            notes.append(f"provider consensus unreadable: "
                         f"{type(exc).__name__}: {exc}")
        if cdf is not None and not cdf.empty:
            pap: dict[int, Any] = {}
            if adf is not None and not adf.empty:
                pap = {int(r["code"]): r for _, r in adf.iterrows()}
                fmax = adf["fetched"].max()
                if fmax is not None:
                    proj_as_of = _iso(fmax)

            def _f(v) -> float | None:
                if v is None:
                    return None
                fv = float(v)
                return None if math.isnan(fv) else fv

            for _, r in cdf.iterrows():
                code_ = int(r["code"])
                a = pap.get(code_)
                cons_next[code_] = {
                    "xpts": _f(r["xpts_mean"]),
                    "xmins": _f(r["xmins_mean"]),
                    "n_sources": (int(r["n_sources"])
                                  if r["n_sources"] == r["n_sources"] else None),
                    "p_appear": _f(a["p_appear"]) if a is not None else None,
                }
            if proj_as_of:
                sources_as_of.setdefault("projection_table", proj_as_of)
            for p in squad15:
                c = cons_next.get(p["code"])
                squad_projection.append({
                    "code": p["code"],
                    "xmins": (round(c["xmins"], 1)
                              if c and c["xmins"] is not None else None),
                    "p_appear": (round(c["p_appear"], 3)
                                 if c and c["p_appear"] is not None else None),
                    "n_sources": c["n_sources"] if c else None,
                })

    # ---- moves to consider: deterministic rules, no free text ------------
    # coverage_gap: a top-ranked easy attacking run the squad barely holds,
    # answered by that club's best consensus attacker with recent returns.
    # form_upgrade: a same-position candidate beating a squad player on BOTH
    # last-2-GW returns and next-GW consensus xPts by the echoed margins.
    # Every number is a shared-helper quantity: fixture_board ranks,
    # sem_projection_consensus xPts, sem_player_form returns, squad prices.
    moves: list[dict[str, Any]] = []
    moves_suppressed = 0
    moves_gap_reason: str | None = None
    settled_gws: list[int] = []
    if sq.get("empty"):
        moves_gap_reason = "squad unreadable — no out-leg can be priced"
    elif g_next is None:
        moves_gap_reason = "no future deadline known"
    elif not cons_next:
        moves_gap_reason = (f"no consensus projections for GW{g_next} in the "
                            f"warehouse — the rules cannot price a candidate")
    else:
        try:
            sg = q(
                wh,
                "SELECT DISTINCT gw FROM sem_player_form(?) "
                "WHERE season = ? AND gw < ? ORDER BY gw DESC LIMIT ?",
                (now, season, g_next, int(THRESHOLDS["recent_gws"])),
            )
            settled_gws = sorted(int(g) for g in sg["gw"]) if not sg.empty else []
        except Exception as exc:  # noqa: BLE001
            settled_gws = []
            notes.append(f"sem_player_form unreadable: "
                         f"{type(exc).__name__}: {exc}")
        returns: dict[int, tuple[int, int]] = {}
        pf_as_of = None
        if settled_gws:
            ph = ", ".join("?" for _ in settled_gws)
            rdf = q(
                wh,
                f"SELECT code, SUM(goals_scored) AS g, SUM(assists) AS a "
                f"FROM sem_player_form(?) WHERE season = ? AND gw IN ({ph}) "
                f"GROUP BY code",
                (now, season, *settled_gws),
            )
            returns = {int(r["code"]): (int(r["g"] or 0), int(r["a"] or 0))
                       for _, r in rdf.iterrows()}
            pf_as_of = latest_as_of(wh, "fact_player_fixture", season)
        mdf = q(
            wh,
            "SELECT code, web_name, position, team, team_code, price, "
            "selected_by_pct FROM sem_players(?) WHERE season = ?",
            (now, season),
        )
        meta: dict[int, dict[str, Any]] = {}
        for _, r in mdf.iterrows():
            meta[int(r["code"])] = {
                "code": int(r["code"]),
                "name": str(r["web_name"]),
                "pos": POSITION_NAME.get(
                    int(r["position"]) if r["position"] == r["position"]
                    else 0, "?"),
                "team": None if r["team"] is None else str(r["team"]),
                "team_code": (None if r["team_code"] != r["team_code"]
                              else int(r["team_code"])),
                "price": (None if r["price"] != r["price"]
                          else float(r["price"])),
                "own_pct": (None if r["selected_by_pct"] != r["selected_by_pct"]
                            else float(r["selected_by_pct"])),
            }
        bank_m = ((sq.get("bank_tenths") or 0) / 10.0
                  if sq.get("bank_tenths") is not None else 0.0)
        move_sources = [
            {"panel": "projection_table", "as_of": proj_as_of},
            {"panel": "squad_overview",
             "as_of": sources_as_of.get("squad_overview")},
        ]
        if pf_as_of:
            move_sources.append({"panel": "fact_player_fixture",
                                 "as_of": _iso(pf_as_of)})
        used_in: set[int] = set()
        used_out: set[int] = set()
        r_top = int(THRESHOLDS["coverage_attack_rank_top"])
        max_held = int(THRESHOLDS["coverage_max_held"])
        ret_min = int(THRESHOLDS["recent_returns_min"])

        def cons_xpts(code: int) -> float | None:
            c = cons_next.get(code)
            if c is None or c["xpts"] is None:
                return None
            return round(float(c["xpts"]), 3)   # projection_table's rounding

        # -- coverage_gap ------------------------------------------------
        if not near.get("empty") and settled_gws:
            ranked_teams = sorted(
                (t for t in team_fixtures
                 if t.get("horizon_attack_rank") is not None),
                key=lambda t: t["horizon_attack_rank"])
            for t in ranked_teams:
                rank = int(t["horizon_attack_rank"])
                if rank > r_top:
                    break
                tc = t["team_code"]
                held = [p for p in squad15 if p.get("team_code") == tc]
                if len(held) > max_held:
                    continue
                cands = sorted(
                    (m for c_, m in meta.items()
                     if m["team_code"] == tc and m["pos"] in ("MID", "FWD")
                     and c_ not in squad_codes and c_ not in used_in
                     and cons_xpts(c_) is not None),
                    key=lambda m: -(cons_xpts(m["code"]) or 0.0))
                min_gain = float(THRESHOLDS["coverage_min_xpts_gain"])
                for cand in cands:
                    g_, a_ = returns.get(cand["code"], (0, 0))
                    if g_ + a_ < ret_min:
                        continue
                    cand_x = cons_xpts(cand["code"]) or 0.0
                    # out-leg ranked in the SAME voice (consensus xPts); a
                    # squad player the consensus does not cover cannot be
                    # ranked and is not guessed at.
                    outs = [p for p in squad15
                            if p.get("pos") == cand["pos"]
                            and p["code"] not in used_out
                            and p.get("price") is not None
                            and cand["price"] is not None
                            and cons_xpts(p["code"]) is not None
                            and bank_m + float(p["price"]) >= float(cand["price"])]
                    outs = [p for p in outs
                            if cand_x >= (cons_xpts(p["code"]) or 0.0) + min_gain]
                    if not outs:
                        continue
                    out = min(outs, key=lambda p: cons_xpts(p["code"]) or 0.0)
                    used_in.add(cand["code"])
                    used_out.add(out["code"])
                    moves.append({
                        "rule": "coverage_gap",
                        "in": cand, "out": _ref(out),
                        "team": t["short_name"], "team_code": tc,
                        "numbers": {
                            "attack_rank": rank,
                            "held_count": len(held),
                            "cand_xpts": cons_xpts(cand["code"]),
                            "cand_goals": g_, "cand_assists": a_,
                            "cand_returns": g_ + a_,
                            "out_xpts": cons_xpts(out["code"]),
                            "in_price": cand["price"],
                            "out_price": out.get("price"),
                            "bank": round(bank_m, 1),
                            "next_gw": g_next,
                        },
                        "gws": settled_gws,
                        # attack_rank is fixture_board's horizon rank — the
                        # gameweeks it was computed over travel with it, so
                        # the card can never contradict the board's default
                        # window without the difference being printed.
                        "rank_gws": [int(g) for g in near.get("gws") or []],
                        "sources": move_sources + [
                            {"panel": "fixture_board",
                             "as_of": sources_as_of.get("fixture_board")}],
                        "drill": {"drawer": cand["code"]},
                    })
                    break

        # -- form_upgrade ------------------------------------------------
        if settled_gws:
            f_ret = int(THRESHOLDS["form_returns_margin"])
            f_x = float(THRESHOLDS["form_xpts_margin"])
            form_cards: list[tuple[float, dict[str, Any]]] = []
            for c_, cand in meta.items():
                if (c_ in squad_codes or c_ in used_in
                        or cand["pos"] not in ("DEF", "MID", "FWD")):
                    continue
                cx = cons_xpts(c_)
                if cx is None or cand["price"] is None:
                    continue
                cg, ca = returns.get(c_, (0, 0))
                best = None
                for s in squad15:
                    if (s.get("pos") != cand["pos"] or s["code"] in used_out
                            or s.get("price") is None):
                        continue
                    sx = cons_xpts(s["code"])
                    if sx is None:
                        continue
                    sg_, sa_ = returns.get(s["code"], (0, 0))
                    if (cg + ca >= sg_ + sa_ + f_ret and cx >= sx + f_x
                            and bank_m + float(s["price"]) >= float(cand["price"])
                            and (best is None or sx < best[0])):
                        best = (sx, s, sg_ + sa_)
                if best is None:
                    continue
                sx, s, s_ret = best
                form_cards.append((cx - sx, {
                    "rule": "form_upgrade",
                    "in": cand, "out": _ref(s),
                    "team": cand["team"], "team_code": cand["team_code"],
                    "numbers": {
                        "cand_xpts": cx, "out_xpts": sx,
                        "cand_returns": cg + ca, "cand_goals": cg,
                        "cand_assists": ca, "out_returns": s_ret,
                        "in_price": cand["price"], "out_price": s.get("price"),
                        "bank": round(bank_m, 1),
                        "next_gw": g_next,
                    },
                    "gws": settled_gws,
                    "rank_gws": [],   # form_upgrade quotes no fixture rank
                    "sources": list(move_sources),
                    "drill": {"drawer": c_},
                }))
            for _, card_ in sorted(form_cards, key=lambda kv: -kv[0]):
                if card_["in"]["code"] in used_in or \
                        card_["out"]["code"] in used_out:
                    continue
                used_in.add(card_["in"]["code"])
                used_out.add(card_["out"]["code"])
                moves.append(card_)
        elif not moves_gap_reason:
            moves_gap_reason = ("no settled gameweek in fact_player_fixture — "
                                "the recent-returns gate cannot be evaluated")

    cap_moves = int(THRESHOLDS["move_cap"])
    moves_suppressed = max(0, len(moves) - cap_moves)
    moves = moves[:cap_moves]
    watch.append({
        "check": "move_rules",
        "status": ("gap" if moves_gap_reason
                   else ("firing" if moves else "clear")),
        "detail": (moves_gap_reason if moves_gap_reason else
                   (f"{len(moves)} move(s) cleared the gates"
                    f"{f', {moves_suppressed} suppressed' if moves_suppressed else ''}"
                    if moves else
                    "no candidate cleared the coverage or form gates")),
        "source_panel": "dashboard_brief",
        "as_of": proj_as_of,
    })
    if moves_gap_reason:
        empties.append({"kind": "moves", "reason": moves_gap_reason})

    # idea_due is GONE on purpose (owner's call: the idea registry is useless
    # in briefings) — no tile kind, no watch check, no idea_registry read.

    # ---- creator_shift: a named gap, not a silent absence ----------------
    # creator_board publishes takes and ownership, not a formation/predicted-XI
    # *change* signal. Deriving one here would be a second implementation of a
    # definition no panel owns — the exact drift the contract forbids.
    empties.append({
        "kind": "creator_shift",
        "reason": "creator_board serves no formation/predicted-XI change "
                  "delta; the gate cannot be evaluated without a second "
                  "implementation. The Creators tab has the corpus.",
    })
    watch.append({"check": "creator_shift", "status": "gap",
                  "detail": "no served change signal — see empty_kinds",
                  "source_panel": "creator_board", "as_of": None})

    # ---- the solve block -------------------------------------------------
    solve: dict[str, Any] = {"state": "missing", "reason": None,
                             "generated_at": None, "age_hours": None,
                             "last_deadline_utc": _iso(last_deadline),
                             "next_deadline_utc": _iso(next_deadline),
                             "plan": None}
    if tplan is None:
        solve["reason"] = (
            f"no transfer plan artefact at {TRANSFER_PLAN_NAME}; "
            f"POST /api/solve mode=transfers (the dashboard's solve button) "
            f"runs `fpl recommend` against your current 15 and commits one."
        )
        watch.append({"check": "solver", "status": "gap",
                      "detail": solve["reason"],
                      "source_panel": "solve_plan", "as_of": None})
        alerts.append({
            "rule": "solve_missing", "kind": "SOLVER", "priority": 0,
            "codes": [], "players": [], "numbers": {},
            "news": None, "status": None, "reason": solve["reason"],
            "source_panel": "solve_plan", "source_as_of": None,
            "drill": {"tab": "pipelines"},
        })
    else:
        gen = _parse_ts(tplan.get("generated_at"))
        solve["generated_at"] = _iso(tplan.get("generated_at"))
        solve["age_hours"] = (round((now - gen).total_seconds() / 3600.0, 1)
                              if gen else None)
        sources_as_of.setdefault("solve_plan", solve["generated_at"])
        h_gws = [int(g) for g in tplan.get("horizon_gws", [])]
        chosen = tplan.get("chosen") or {}
        alt_rows = list(tplan.get("alternatives") or [])
        out_codes = [int(c) for c in chosen.get("out", [])]
        in_codes = [int(c) for c in chosen.get("in", [])]
        cap_code = chosen.get("captain")

        if gen is not None and last_deadline is not None and gen < last_deadline:
            solve["state"] = "stale"
            solve["reason"] = (
                f"transfer plan generated {gen.date().isoformat()} for "
                f"GW{h_gws[0] if h_gws else '?'}\u2013"
                f"{h_gws[-1] if h_gws else '?'}; a deadline has passed since "
                f"\u2014 its moves were priced against a squad you no longer "
                f"have."
            )
            alerts.append({
                "rule": "solve_stale", "kind": "SOLVER", "priority": 0,
                "codes": [], "players": [],
                "numbers": {"age_hours": solve["age_hours"]},
                "news": None, "status": None, "reason": solve["reason"],
                "source_panel": "solve_plan",
                "source_as_of": solve["generated_at"],
                "drill": {"tab": "pipelines"},
            })
            watch.append({"check": "solver", "status": "gap",
                          "detail": f"transfer plan predates "
                                    f"GW{g_next if g_next else '?'} \u2014 "
                                    f"generated {gen.date().isoformat()}",
                          "source_panel": "solve_plan",
                          "as_of": solve["generated_at"]})
        else:
            fresh_h = float(THRESHOLDS["solve_fresh_window_h"])
            if (gen is not None and next_deadline is not None
                    and gen >= next_deadline - dt.timedelta(hours=fresh_h)):
                solve["state"] = "fresh"
            else:
                solve["state"] = "aging"
            watch.append({"check": "solver", "status": "clear",
                          "detail": f"transfer plan {solve['state']}, "
                                    f"{solve['age_hours']}h old",
                          "source_panel": "solve_plan",
                          "as_of": solve["generated_at"]})

            # Resolve every code the card renders through sem_players \u2014
            # the shared view, never a second name table.
            pinfo_needed = set(out_codes) | set(in_codes) | set(tplan_named)
            if cap_code:
                pinfo_needed.add(int(cap_code))
            for a in alt_rows:
                pinfo_needed |= {int(c) for c in a.get("out", [])}
                pinfo_needed |= {int(c) for c in a.get("in", [])}
            pdf = q(
                wh,
                "SELECT code, web_name, position, team, team_code, price, "
                "selected_by_pct FROM sem_players(?) WHERE season = ?",
                (now, season),
            )
            prow = {int(r["code"]): r for _, r in pdf.iterrows()
                    if int(r["code"]) in pinfo_needed}

            def plan_ref(c: int) -> dict[str, Any]:
                r = prow.get(int(c))
                if r is None:
                    return {"code": int(c), "name": str(c), "pos": None,
                            "team": None, "team_code": None, "price": None,
                            "own_pct": None}
                return {
                    "code": int(c), "name": str(r["web_name"]),
                    "pos": POSITION_NAME.get(
                        int(r["position"]) if r["position"] == r["position"] else 0, "?"),
                    "team": None if r["team"] is None else str(r["team"]),
                    "team_code": None if r["team_code"] != r["team_code"] else int(r["team_code"]),
                    "price": None if r["price"] != r["price"] else float(r["price"]),
                    "own_pct": None if r["selected_by_pct"] != r["selected_by_pct"]
                               else float(r["selected_by_pct"]),
                }

            sq_by_code = {p["code"]: p for p in squad15}

            def any_ref(c: int) -> dict[str, Any]:
                # An outgoing player is in the current 15: squad_overview's own
                # row when available, sem_players otherwise.
                return (_ref(sq_by_code[int(c)]) if int(c) in sq_by_code
                        else plan_ref(int(c)))

            flow_by_code: dict[int, dict[str, Any]] = {}
            if not pr.get("empty"):
                w_h = (pr.get("window") or {}).get("hours")
                for r in list(pr.get("risers", [])) + list(pr.get("fallers", [])):
                    flow_by_code[r["code"]] = {
                        "net_per_hour": r["net_per_hour"], "window_h": w_h}

            # The chosen move's out/in lists, paired within position by price
            # (the artefact stores the sets; single moves pair trivially).
            by_pos_out: dict[str, list[dict[str, Any]]] = {}
            by_pos_in: dict[str, list[dict[str, Any]]] = {}
            for c in out_codes:
                r = any_ref(c)
                by_pos_out.setdefault(r["pos"] or "?", []).append(r)
            for c in in_codes:
                r = plan_ref(c)
                by_pos_in.setdefault(r["pos"] or "?", []).append(r)
            plan_moves: list[dict[str, Any]] = []
            for pos in sorted(set(by_pos_out) | set(by_pos_in)):
                o_list = sorted(by_pos_out.get(pos, []),
                                key=lambda r: -(r["price"] or 0))
                i_list = sorted(by_pos_in.get(pos, []),
                                key=lambda r: -(r["price"] or 0))
                for o, i in zip(o_list, i_list):
                    plan_moves.append({
                        "out": o, "in": i,
                        "price_delta": (round(i["price"] - o["price"], 1)
                                        if i["price"] is not None
                                        and o["price"] is not None else None),
                        "out_flow": flow_by_code.get(o["code"]),
                        "in_flow": flow_by_code.get(i["code"]),
                    })

            def _alt_summary(a: dict[str, Any]) -> str:
                a_out = sorted(int(c) for c in a.get("out", []))
                a_in = sorted(int(c) for c in a.get("in", []))
                if not a_in:
                    return "roll (no move)"
                return ", ".join(
                    f"{any_ref(o)['name']} \u2192 {plan_ref(i)['name']}"
                    for o, i in zip(a_out, a_in))

            alternatives = [{
                "summary": _alt_summary(a),
                "objective": (float(a["objective"])
                              if a.get("objective") is not None else None),
                "hits": (int(a["hits"]) if a.get("hits") is not None else None),
            } for a in alt_rows[:3]]

            # The chosen move's \u00a75 verdict, verbatim from the artefact.
            # Empty under the expected_points surrogate (no rank state), and
            # only relevant when the chosen move actually costs points.
            hit_verdict = None
            verdicts = list(tplan.get("hit_verdicts") or [])
            if verdicts and int(chosen.get("hits") or 0) > 0:
                v = verdicts[0]
                hit_verdict = {k: v.get(k) for k in
                               ("label", "hits", "hit_points", "expected_gain",
                                "breakeven_gain", "justified")}

            # The optimality gap, parsed from the solver's own notes — the
            # honesty number that belongs NEXT TO gain_over_roll, not in a
            # fold. Null = no gap note = the solve closed within tolerance.
            gap_pct = None
            for note_ in tplan.get("notes") or []:
                m_gap = _GAP_NOTE.search(str(note_))
                if m_gap:
                    gap_pct = float(m_gap.group(1))
                    break

            my_cap = next((p for p in squad15 if p.get("is_captain")), None)
            solve["plan"] = {
                "generated_at": solve["generated_at"],
                "age_hours": solve["age_hours"],
                "gw": (int(tplan["gw"]) if tplan.get("gw") is not None else None),
                "horizon_gws": h_gws,
                # The currency of every objective number below \u2014 the
                # expected_points surrogate today. gain_over_roll is the
                # solver's own forecast vs the solved roll in that currency,
                # never summed or blended with consensus numbers.
                "objective_mode": tplan.get("objective_mode"),
                "free_transfers": (int(tplan["free_transfers"])
                                   if tplan.get("free_transfers") is not None
                                   else None),
                "unlimited_transfers": tplan.get("unlimited_transfers"),
                "gain_over_roll": (float(tplan["gain_over_roll"])
                                   if tplan.get("gain_over_roll") is not None
                                   else None),
                "hits": (int(chosen["hits"])
                         if chosen.get("hits") is not None else None),
                "hit_points": (int(chosen["hit_points"])
                               if chosen.get("hit_points") is not None else None),
                "chip": chosen.get("chip") or None,
                "notes": [str(n) for n in tplan.get("notes") or []],
                "bounds": tplan.get("bounds"),
                "solve_seconds": (float(tplan["solve_seconds"])
                                  if tplan.get("solve_seconds") is not None
                                  else None),
                "optimality_gap_pct": gap_pct,
                # Zero transfers is the ROLL recommendation \u2014 bank the
                # transfer \u2014 not an empty state.
                "is_roll": not in_codes,
                "moves": plan_moves,
                "captain": plan_ref(int(cap_code)) if cap_code else None,
                "your_captain": _ref(my_cap) if my_cap else None,
                "alternatives": alternatives,
                "hit_verdict": hit_verdict,
            }

    # ---- the verdict: one pick per question, by the PRINTED precedence ---
    # It PICKS, it does not blend. Rule ids + structured refs/numbers only —
    # the house rule stands (no free-text recommendation field); wording
    # lives in view templates keyed by the rule id. Every dissenting voice
    # (mean-xPts captain, haul-odds captain, creator armband count, the rule
    # moves when they differ from the solver) is served beside the pick as
    # data, currencies never summed.
    plan = solve.get("plan")
    plan_as_of = solve.get("generated_at")
    sq_as_of_v = sources_as_of.get("squad_overview")
    sq_by_code_v = {p["code"]: p for p in squad15}

    # creator armband count — creator_board's consensus, read for the dissent
    # chip only; an unreadable corpus degrades to no chip, noted, never a 500.
    creator_cap: dict[str, Any] | None = None
    try:
        from fpl_edge.platform.scripts.creators import creator_board

        cb = creator_board(wh)
        if not cb.get("empty"):
            best_row, best_n = None, 0
            for r_ in cb.get("consensus") or []:
                n_c = int(((r_.get("captain") or {}).get("n")) or 0)
                if n_c > best_n:
                    best_row, best_n = r_, n_c
            if best_row is not None:
                creator_cap = {
                    "n": best_n,
                    "ref": {"code": int(best_row["code"]),
                            "name": str(best_row.get("name")
                                        or best_row["code"]),
                            "pos": best_row.get("pos"),
                            "team": best_row.get("team"),
                            "team_code": None,
                            "price": best_row.get("price"),
                            "own_pct": best_row.get("own_pct")},
                    "as_of": _iso(cb.get("as_of")),
                }
    except Exception as exc:  # noqa: BLE001 - a dissent chip, not the page
        notes.append(f"creator_board unreadable for the armband dissent: "
                     f"{type(exc).__name__}: {exc}")

    # -- transfer ----------------------------------------------------------
    t_dissent: list[dict[str, Any]] = []
    if plan is not None:
        t_rule = "solver_roll" if plan["is_roll"] else "solver_plan"
        t_moves = [{"out": m["out"], "in": m["in"]} for m in plan["moves"]]
        t_numbers: dict[str, Any] = {
            "gain_over_roll": plan.get("gain_over_roll"),
            "optimality_gap_pct": plan.get("optimality_gap_pct"),
            "solve_seconds": plan.get("solve_seconds"),
            "age_hours": plan.get("age_hours"),
            "free_transfers": plan.get("free_transfers"),
            "hits": plan.get("hits"),
        }
        t_src, t_as_of = "solve_plan", plan_as_of
        # the rule moves dissent only where they differ from the solver
        plan_pairs = {(m["out"]["code"], m["in"]["code"])
                      for m in plan["moves"]}
        for mv in moves:
            if (mv["out"]["code"], mv["in"]["code"]) in plan_pairs:
                continue
            t_dissent.append({
                "voice": "rule_moves", "rule": mv["rule"],
                "player": None, "in": mv["in"], "out": mv["out"],
                "numbers": {"cand_xpts": mv["numbers"].get("cand_xpts"),
                            "out_xpts": mv["numbers"].get("out_xpts")},
                "source_panel": "dashboard_brief",
                "source_as_of": proj_as_of,
                "drill": mv.get("drill") or {"focus": "moves"},
            })
    elif moves:
        t_rule = ("rule_moves_solver_stale" if solve["state"] == "stale"
                  else "rule_moves_solver_missing")
        t_moves = [{"out": m["out"], "in": m["in"]} for m in moves]
        t_numbers = {"n_rule_moves": len(moves)}
        t_src, t_as_of = "dashboard_brief", proj_as_of
    else:
        t_rule, t_moves, t_numbers = "no_move_named", [], {}
        t_src, t_as_of = "dashboard_brief", None
    if plan is None and solve["state"] in ("stale", "missing"):
        # the overruled/absent solver is itself a dissent entry, dated
        t_dissent.append({
            "voice": "solver", "rule": f"solve_{solve['state']}",
            "player": None, "in": None, "out": None,
            "numbers": {"age_hours": solve.get("age_hours")},
            "source_panel": "solve_plan",
            "source_as_of": solve.get("generated_at"),
            "drill": {"focus": "solver"},
        })
    transfer_line = {
        "question": "transfer", "rule": t_rule, "state": solve["state"],
        "pick": None, "moves": t_moves, "chip": None, "numbers": t_numbers,
        "dissent": t_dissent, "source_panel": t_src, "source_as_of": t_as_of,
        "drill": {"focus": "solver" if plan is not None else "moves"},
    }

    # -- captain -----------------------------------------------------------
    cap_pick = None
    if plan is not None and plan.get("captain"):
        c_rule, cap_pick = "solver_plan_captain", plan["captain"]
        c_src, c_as_of = "solve_plan", plan_as_of
    elif suggested_xi and suggested_xi.get("captain"):
        c_rule, cap_pick = "mean_xpts_captain", suggested_xi["captain"]
        c_src, c_as_of = "squad_overview", sq_as_of_v
    else:
        c_rule, c_src, c_as_of = "no_captain_named", "dashboard_brief", None
    c_numbers: dict[str, Any] = {}
    if cap_pick is not None:
        row_v = sq_by_code_v.get(cap_pick["code"])
        if row_v is not None:
            c_numbers = {"pick_xpts": row_v.get("xpts"),
                         "pick_p_haul": row_v.get("p_haul")}
    c_dissent: list[dict[str, Any]] = []
    if suggested_xi and cap_pick is not None:
        cn = suggested_xi.get("captain_numbers") or {}
        bm = suggested_xi.get("captain_by_mean")
        if bm and bm["code"] != cap_pick["code"]:
            c_dissent.append({
                "voice": "mean_xpts", "rule": "mean_xpts_captain",
                "player": bm, "in": None, "out": None,
                "numbers": {"xpts": cn.get("mean_pick_xpts")},
                "source_panel": "squad_overview", "source_as_of": sq_as_of_v,
                "drill": {"drawer": bm["code"]},
            })
        bh = suggested_xi.get("captain_by_haul")
        if bh and bh["code"] != cap_pick["code"]:
            c_dissent.append({
                "voice": "haul_odds", "rule": "haul_odds_captain",
                "player": bh, "in": None, "out": None,
                "numbers": {"p_haul": cn.get("haul_pick_p_haul")},
                "source_panel": "squad_overview", "source_as_of": sq_as_of_v,
                "drill": {"drawer": bh["code"]},
            })
    if (creator_cap is not None and cap_pick is not None
            and creator_cap["ref"]["code"] != cap_pick["code"]):
        c_dissent.append({
            "voice": "creator_armband", "rule": "creator_armband_count",
            "player": creator_cap["ref"], "in": None, "out": None,
            "numbers": {"armband_calls": creator_cap["n"]},
            "source_panel": "creator_board",
            "source_as_of": creator_cap["as_of"],
            "drill": {"tab": "creators"},
        })
    captain_line = {
        "question": "captain", "rule": c_rule, "state": solve["state"],
        "pick": cap_pick, "moves": [], "chip": None, "numbers": c_numbers,
        "dissent": c_dissent, "source_panel": c_src, "source_as_of": c_as_of,
        "drill": {"focus": "squad"},
    }

    # -- bench (suggested_xi IS the pick; the verdict line points at it) ---
    if suggested_xi is None:
        b_rule, b_numbers = "no_bench_named", {}
        b_as_of = None
    elif suggested_xi["n_changes"]:
        b_rule = "bench_inversion_applied"
        b_numbers = {"n_changes": suggested_xi["n_changes"],
                     "swap_delta_xpts": suggested_xi.get("swap_delta_xpts")}
        b_as_of = sq_as_of_v
    else:
        b_rule, b_numbers = "bench_confirmed", {"n_changes": 0}
        b_as_of = sq_as_of_v
    bench_line = {
        "question": "bench", "rule": b_rule, "state": solve["state"],
        "pick": None, "moves": [], "chip": None, "numbers": b_numbers,
        "dissent": [], "source_panel": "squad_overview",
        "source_as_of": b_as_of, "drill": {"focus": "squad"},
    }

    # -- chip: a yes/no verdict line (null chip = hold) --------------------
    if plan is not None:
        chip_val = plan.get("chip") or None
        ch_rule = "solver_plan_chip" if chip_val else "chip_hold"
        ch_numbers: dict[str, Any] = {"age_hours": plan.get("age_hours")}
        ch_as_of = plan_as_of
    else:
        chip_val, ch_rule, ch_numbers = None, "no_chip_named", {}
        ch_as_of = solve.get("generated_at")
    chip_line = {
        "question": "chip", "rule": ch_rule, "state": solve["state"],
        "pick": None, "moves": [], "chip": chip_val, "numbers": ch_numbers,
        "dissent": [], "source_panel": "solve_plan",
        "source_as_of": ch_as_of, "drill": {"focus": "solver"},
    }

    verdict = {
        "precedence": PRECEDENCE,
        "lines": [transfer_line, captain_line, bench_line, chip_line],
    }

    # ---- header stats: FT count + chip verdict, top-level ---------------
    header = {
        "free_transfers": (int(tplan["free_transfers"])
                           if tplan is not None
                           and tplan.get("free_transfers") is not None
                           else None),
        "free_transfers_as_of": solve.get("generated_at"),
        "free_transfers_state": solve["state"],
        "bank_tenths": (sq.get("bank_tenths")
                        if not sq.get("empty") else None),
        "chip": chip_val,
        "chip_rule": ch_rule,
        "chip_state": solve["state"],
    }

    # ---- data-birth instant of the solved projection artefact -----------
    # The squad card's xPts/p_haul come from the solved artefact (via
    # squad_overview) — this is when that model RUN happened, distinct from
    # every panel read clock in sources_as_of.
    projection_generated = None
    proj_artefact = Path(source_dir(wh)) / PROJECTION_NAME
    if proj_artefact.exists():
        projection_generated = dt.datetime.fromtimestamp(
            proj_artefact.stat().st_mtime, UTC).isoformat()

    # ---- assemble --------------------------------------------------------
    alerts.sort(key=lambda a: (
        a["priority"],
        -max([abs(v) for v in a["numbers"].values() if v is not None] or [0.0]),
    ))

    # Rank: priority class first, then gate magnitude WITHIN a kind, kinds
    # interleaved — one flooding kind (31 xPts standouts on a normal Monday)
    # must not evict the rest of the catalogue from all six slots; that is the
    # squeeze the alert/tile split exists to prevent, applied inside the tiles.
    within: dict[str, int] = {}
    ranked: list[tuple[tuple[int, int, float], dict[str, Any]]] = []
    for prio, margin, tile in sorted(tiles, key=lambda t: (t[0], -t[1])):
        idx = within.get(tile["kind"], 0)
        within[tile["kind"]] = idx + 1
        ranked.append(((prio, idx, -margin), tile))
    ranked.sort(key=lambda r: r[0])
    cap_n = int(THRESHOLDS["tile_cap"])
    kept = [t for _, t in ranked[:cap_n]]
    suppressed: dict[str, int] = {}
    for _, t in ranked[cap_n:]:
        suppressed[t["kind"]] = suppressed.get(t["kind"], 0) + 1

    # Oldest load-bearing contributing clock. The solve artefact's clock is
    # excluded: both of its clocks print on the solver card itself, and a
    # stale plan is a named gap, not a contributor to the alerts' age.
    load_bearing = [v for k, v in sources_as_of.items()
                    if v and k != "solve_plan"]
    as_of = min(load_bearing) if load_bearing else None

    deadline_utc = None
    if g_next is not None:
        row = deadlines[deadlines["gw"] == g_next]
        if not row.empty and row.iloc[0]["deadline_utc"] is not None:
            deadline_utc = str(row.iloc[0]["deadline_utc"]).replace(" ", "T")

    return {
        "season": season,
        "gw": g_next,
        "entry_id": eid,
        "as_of": as_of,
        "sources_as_of": sources_as_of,
        "deadline_utc": deadline_utc,
        "xi_median_xpts": xi_median,
        "thresholds": {k: float(v) for k, v in THRESHOLDS.items()},
        "alerts": alerts,
        "tiles": kept,
        "suppressed_counts": suppressed,
        "empty_kinds": empties,
        "watch_log": watch,
        "solve": solve,
        "suggested_xi": suggested_xi,
        "team_fixtures": team_fixtures,
        "squad_projection": squad_projection,
        "projection_gw": g_next,
        "moves": moves,
        "moves_suppressed": moves_suppressed,
        "verdict": verdict,
        "header": header,
        "projection_generated": projection_generated,
        "projection_source": (
            f"solved artefact {PROJECTION_NAME} — the engine's own "
            f"simulation run, not the provider consensus"
            if projection_generated else None),
        "fixtures_scale": fixtures_scale,
        "notes": notes,
    }


register_script(
    "dashboard_brief",
    dashboard_brief,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Dashboard brief",
    description="Alerts, gated tiles, watch log and the solve state — "
                "selected and thresholded from the source panels, never "
                "recomputed. Thresholds echoed; every item cites its source.",
)
