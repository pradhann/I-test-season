"""The JSON Schemas, the threshold table and the precedence the brief echoes.

Constants only: no warehouse read, no import from the rest of this package.
``THRESHOLDS`` is read back by ``platform/app/helpers.py:280`` as well as by
the panel, which is why it lives at the bottom of the import order."""

from __future__ import annotations

import re
from typing import Any

from fpl_edge.platform.scripts.common import season_param

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
    "plan's captain while fresh or aging, else the highest consensus xPts "
    "in the best XI, and a lead under captain_close_call_xpts is a close "
    "call, not a pick; dissenting measures are printed, never blended. "
    "Bench: the best formation-legal XI by consensus xPts, drawn on the "
    "pitch; the locked picks are named where they differ. Chip: the solver "
    "plan's chip, else hold."
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
    # How old a plan may be and still read "fresh". This measures the plan's
    # AGE, which is the question the word answers. It used to measure the
    # plan's distance to the next deadline, so a plan solved 36 minutes ago
    # read "aging" for the four days before the deadline came within four
    # hours of it, no matter how current it was. Twelve hours, because prices
    # settle nightly and team news moves daily: a plan that has lived through
    # one of those has not seen it.
    "solve_fresh_window_h": 12,
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
    # A solver captain whose OWN forecast is this far from the provider
    # consensus for the same gameweek is resting on a number the market
    # does not share. The verdict still takes the solver (precedence), but
    # the disagreement is printed — spending a triple captain on a private
    # 2x forecast is exactly the bet that should be made knowingly.
    "captain_divergence_xpts": 1.5,
    # The captain line asserts a pick only when the best consensus xPts in
    # the best XI leads the runner-up by at least this; closer is printed as
    # a close call, never as a confident armband.
    "captain_close_call_xpts": 0.5,
}

#: ``entry_id`` is NOT a param. It stays a RESULT key, because the payload has
#: to say whose team it describes; it is no longer an input, because the id and
#: the FPL login must come from the same object, which is the request's user
#: context.
PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
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
        "forecast_source": {"type": ["string", "null"]},
        "forecast_engine_fill_share": {"type": ["number", "null"]},
        "hits": {"type": ["integer", "null"]},
        "hit_points": {"type": ["integer", "null"]},
        # What the plan leaves in the bank once its moves are made. The
        # dashboard printed each transfer's price change and never the net or
        # the balance, so a plan that cannot be executed read as a normal
        # recommendation and would have been rejected at the FPL site.
        # Negative means the plan is not affordable.
        "bank_after_tenths": {"type": ["integer", "null"]},
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
                  "enum": ["fresh", "aging", "stale", "superseded", "missing"]},
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

#: The best formation-legal XI from the 15 by consensus xPts: the ONE lineup
#: the pitch draws. No swap arrows, no "as picked" toggle: the locked picks
#: differ by ``n_differs`` players, named, and that is the whole comparison.
#: Captain = highest consensus xPts in that XI; the top three are served
#: with their numbers so the captain line can print the runner-up and call
#: a close one a close call (gate: thresholds.captain_close_call_xpts).
_CAP_CANDIDATE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["player", "xpts", "p_haul"],
    "properties": {
        "player": _PLAYER_REF,
        "xpts": {"type": ["number", "null"]},
        "p_haul": {"type": ["number", "null"]},
    },
}
_BEST_XI = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["xi_codes", "bench_codes", "formation", "captain",
                 "captain_candidates", "close_call", "differs",
                 "n_differs", "xi_xpts", "reason", "source_panel",
                 "source_as_of"],
    "properties": {
        "xi_codes": {"type": "array", "items": {"type": "integer"}},
        "bench_codes": {"type": "array", "items": {"type": "integer"}},
        "formation": {"type": ["string", "null"]},
        "captain": {"anyOf": [_PLAYER_REF, {"type": "null"}]},
        "captain_candidates": {"type": "array", "items": _CAP_CANDIDATE},
        # lead of the top candidate over the second, in consensus xPts
        "captain_lead_xpts": {"type": ["number", "null"]},
        "close_call": {"type": "boolean"},
        # the locked starters who are NOT in the best XI (and vice versa)
        "differs": {"type": "array", "items": _PLAYER_REF},
        "n_differs": {"type": "integer"},
        "xi_xpts": {"type": ["number", "null"]},
        "reason": {"type": ["string", "null"]},
        "source_panel": {"type": "string"},
        "source_as_of": {"type": ["string", "null"]},
    },
}

#: WHERE the 15 came from, as a fact the page can act on. ``live`` is False
#: for public picks (published after a deadline: the last GW's team, not the
#: one being built) and for the manual /setsquad entry; ``fix`` is the one
#: command that turns the read live.
_SQUAD_SOURCE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["label", "live", "picks_gw", "as_of", "fix"],
    "properties": {
        "label": {"type": "string"},
        "live": {"type": "boolean"},
        "picks_gw": {"type": ["integer", "null"]},
        "as_of": {"type": ["string", "null"]},
        "fix": {"type": ["string", "null"]},
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
#: Where the season actually stands, gameweek by gameweek, against FPL's own
#: published average for that gameweek. The objective is P(top-1k) and the
#: dashboard could not say what rank the season was at: GW3 scored 23 against a
#: field average of 51 and the overall rank fell from 141,593 to 769,533 with
#: nothing on the page reporting it. ``points`` is the manager's own crawled
#: gameweek; ``field`` is dim_event.avg_entry_score, NULL for a gameweek FPL
#: has not settled; ``delta`` is the difference and is null whenever either
#: side is.
_STANDING = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["entry_id", "gws", "reason"],
    "properties": {
        "entry_id": {"type": ["integer", "null"]},
        "overall_rank": {"type": ["integer", "null"]},
        "rank_move": {"type": ["integer", "null"]},
        "total_points": {"type": ["integer", "null"]},
        "vs_field_total": {"type": ["number", "null"]},
        "as_of": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
        "gws": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["gw", "points", "field", "delta"],
            "properties": {
                "gw": {"type": "integer"},
                "points": {"type": ["integer", "null"]},
                "field": {"type": ["number", "null"]},
                "delta": {"type": ["number", "null"]},
                "bench_points": {"type": ["integer", "null"]},
                "hit_cost": {"type": ["integer", "null"]},
                "overall_rank": {"type": ["integer", "null"]},
            },
        }},
    },
}

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
                 "xpts_source"],
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
        "best_xi": _BEST_XI,
        "squad_source": _SQUAD_SOURCE,
        "team_fixtures": {"type": "array", "items": _TEAMFIX},
        "squad_projection": {"type": "array", "items": _SQPROJ},
        "projection_gw": {"type": ["integer", "null"]},
        "moves": {"type": "array", "items": _MOVE},
        "moves_suppressed": {"type": "integer"},
        "verdict": _VERDICT,
        "header": _HEADER,
        "standing": _STANDING,
        # Sources of the squad card's two projection columns, threaded from
        # squad_overview: xPts is the provider consensus (the Projections tab's
        # own numbers, so the two surfaces cannot disagree); p_haul is the
        # engine simulation with its OWN data-birth instant, which can be
        # weeks older — the two clocks are never conflated.
        "xpts_source": {"type": ["string", "null"]},
        "xpts_as_of": {"type": ["string", "null"]},
        "p_haul_source": {"type": ["string", "null"]},
        "p_haul_generated": {"type": ["string", "null"]},
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
