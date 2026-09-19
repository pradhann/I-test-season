"""The JSON Schemas for both modes, and the two rounding helpers.

``_GW_RESULT`` alone is 325 lines: the gameweek payload carries every provider
column separately because a consensus and a single source are different
claims. ``_num`` and ``_rnd`` sit here because both modes call them; this
module imports nothing else in the package."""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.scripts.common import season_param

#: How many gameweeks past the chosen one the player detail covers (gw..gw+4).
DETAIL_HORIZON = 4

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "position": {
            "type": ["integer", "null"],
            "enum": [1, 2, 3, 4, None],
            "default": None,
            "description": "1 GKP, 2 DEF, 3 MID, 4 FWD; null for all.",
        },
        "sort": {
            "type": "string",
            "enum": ["xpts", "p_haul", "value", "price", "own",
                     "spread", "p_appear", "xmins"],
            "default": "xpts",
            "description": "spread/p_appear/xmins apply to gameweek mode; "
                           "p_haul applies to artefact mode.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 800, "default": 50},
        "max_price": {"type": ["number", "null"], "default": None},
        "gw": {
            "default": None,
            "oneOf": [
                {"type": "integer", "minimum": 1, "maximum": 38},
                {"const": "next"},
                {"type": "null"},
            ],
            "description": "Gameweek for provider-projection mode. 'next' "
                           "resolves the first future deadline. null keeps "
                           "the original solved-artefact behaviour.",
        },
        "source": {
            "type": ["string", "null"],
            "default": None,
            "description": "'all' (or null) = consensus across sources; a "
                           "specific source name shows that vendor alone.",
        },
        "team": {
            "type": ["string", "null"],
            "default": None,
            "description": "Team short_name filter, e.g. 'ARS'. Gameweek mode.",
        },
        "min_p_appear": {
            "type": ["number", "null"],
            "minimum": 0,
            "maximum": 1,
            "default": None,
            "description": "Drop players whose consensus appearance "
                           "probability is below this (or unknown).",
        },
        "detail_code": {
            "type": ["integer", "null"],
            "default": None,
            "description": "Player code: include a per-source breakdown for "
                           "the chosen GW and the next four.",
        },
        "sources": {
            "type": ["array", "null"], "items": {"type": "string"},
            "default": None,
            "description": "Restrict the consensus to this subset of "
                           "providers (2+ names). One name behaves like "
                           "`source`; omitted/null means every provider.",
        },
        "span": {
            "type": "integer", "minimum": 1, "maximum": 8, "default": 5,
            "description": "How many gameweeks the matrix covers from the "
                           "anchor gw.",
        },
        "codes": {
            "type": ["array", "null"], "items": {"type": "integer"},
            "maxItems": 60,
            "default": None,
            "description": "Restrict the served rows to these player codes. "
                           "The aggregates, the matrix and the coverage "
                           "blocks are unaffected, so a filtered board still "
                           "carries the same scale and the same clocks.",
        },
        "compact": {
            "type": "boolean",
            "default": False,
            "description": "Serve the decision fields only: the rows (code, "
                           "name, pos, team, price, own_pct, status, xpts, "
                           "spread, p_appear, xp_sum, xp_sum_gws), the "
                           "gameweek axis and the clocks. The matrix, the "
                           "per-team and per-position aggregates, the weights "
                           "table, the provider-accuracy blocks, the settled "
                           "actuals and the source matrix are omitted, and "
                           "`omitted_blocks` names every one of them. For a "
                           "caller under a payload cap; the browser never "
                           "sets it.",
        },
        "weighting": {
            "type": "string",
            "enum": ["equal", "earned"],
            "default": "equal",
            "description": "Consensus blend for gameweek mode. 'equal' = the "
                           "unweighted mean (sem_projection_consensus). "
                           "'earned' = the calibration loop's inverse-MSE "
                           "weights (sem_projection_consensus_weighted). "
                           "Never blended; the result names which applied.",
        },
    },
}

_ARTEFACT_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "rows", "row_count", "sort", "projection_generated"],
    "properties": {
        "season": {"type": "string"},
        "sort": {"type": "string"},
        "row_count": {"type": "integer"},
        "projection_generated": {"type": ["string", "null"]},
        "state_as_of": {"type": ["string", "null"]},
        "as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "name", "pos", "price", "own_pct", "xpts"],
                "properties": {
                    "code": {"type": "integer"},
                    "name": {"type": "string"},
                    "pos": {"type": "string"},
                    "team": {"type": ["string", "null"]},
                    "price": {"type": "number"},
                    "own_pct": {"type": ["number", "null"]},
                    "xpts": {"type": "number"},
                    "p10": {"type": ["number", "null"]},
                    "p90": {"type": ["number", "null"]},
                    "p_haul": {"type": ["number", "null"]},
                    "value": {"type": ["number", "null"]},
                    "status": {"type": ["string", "null"]},
                },
            },
        },
    },
}

_GW_ROW: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "xpts", "n_sources", "p_appear"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": "string"},
        "team": {"type": ["string", "null"]},
        "team_code": {"type": ["integer", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},
        "status": {"type": ["string", "null"]},
        "xpts": {"type": "number",
                 "description": "consensus mean, or the single source's value"},
        "xpts_min": {"type": ["number", "null"]},
        "xpts_max": {"type": ["number", "null"]},
        "spread": {"type": ["number", "null"],
                   "description": "xpts_max - xpts_min across sources"},
        "sd": {"type": ["number", "null"]},
        "n_sources": {"type": "integer"},
        "n_weighted_sources": {
            "type": ["integer", "null"],
            "description": "earned weighting only: how many of n_sources "
                           "carried weight > 0 in the blend; null under "
                           "equal weighting or a single source"},
        "xmins": {"type": ["number", "null"]},
        "p_appear": {"type": ["number", "null"],
                     "description": "separate from xpts by design; never "
                                    "multiplied in"},
        "xp_if_appears": {"type": ["number", "null"]},
        "value": {"type": ["number", "null"]},
        # The horizon sum the browser used to compute for itself: this
        # player's matrix cells added over the gameweeks in `gws`, in the same
        # selection (consensus or one source) as `xpts`. Null when the
        # providers cover none of them.
        "xp_sum": {"type": ["number", "null"],
                   "description": "sum of this player's matrix cells over the "
                                  "served `gws`, same selection as xpts"},
        # How many of those gameweeks actually contributed. Less than
        # len(gws) means the sum is partial and nothing was filled in for the
        # rest.
        "xp_sum_gws": {"type": ["integer", "null"]},
    },
}

_GW_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["mode", "season", "gw", "source", "sort", "row_count", "rows",
                 "gw_coverage", "sources", "by_team", "by_position", "detail",
                 "notes"],
    "properties": {
        "mode": {"enum": ["consensus", "source"]},
        "season": {"type": "string"},
        "gw": {"type": "integer"},
        "source": {"type": ["string", "null"],
                   "description": "null in consensus mode"},
        "active_sources": {
            "type": "array", "items": {"type": "string"},
            "description": "exactly which providers drive rows and matrix"},
        "sort": {"type": "string"},
        "row_count": {"type": "integer"},
        "as_of": {"type": ["string", "null"],
                  "description": "latest provider fetch instant at this GW"},
        "notes": {"type": "array", "items": {"type": "string"}},
        "rows": {"type": "array", "items": _GW_ROW},
        # Present only under compact=true: the blocks this view left out, by
        # name. An empty matrix or an empty by_team would otherwise read as
        # "no data", which is the one thing this payload must never say by
        # accident. Absent under the full view, where nothing was omitted.
        "omitted_blocks": {
            "type": "array", "items": {"type": "string"},
            "description": "compact mode only: the result keys this view "
                           "emptied. Ask again without compact for them.",
        },
        "compact": {"type": "boolean"},
        # compact mode only: the codes the caller asked for that the board
        # does not carry, so a filtered request never silently returns fewer
        # rows than it named.
        "codes_not_found": {"type": "array", "items": {"type": "integer"}},
        "gw_coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gw", "n_sources", "n_players"],
                "properties": {
                    "gw": {"type": "integer"},
                    "n_sources": {"type": "integer"},
                    "n_players": {"type": "integer"},
                },
            },
        },
        "sources": {"type": "array", "items": {"type": "string"}},
        "source_meta": {
            "type": "array",
            "description": "Per provider: what it covers and how fresh it is. "
                           "Data-driven, so a newly registered feed (e.g. a "
                           "paid FPL Review subscription) appears with no UI "
                           "change.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "gw_min", "gw_max", "last_fetched",
                             "n_rows"],
                "properties": {
                    "source": {"type": "string"},
                    "gw_min": {"type": "integer"},
                    "gw_max": {"type": "integer"},
                    "last_fetched": {"type": "string"},
                    "n_rows": {"type": "integer"},
                    "has_xmins": {"type": "boolean"},
                    "has_p_appear": {"type": "boolean"},
                },
            },
        },
        "prices_as_of": {"type": ["string", "null"],
            "description": "when player prices/ownership were last ingested"},
        "accuracy": {
            "type": "array",
            "description": "measured per-provider accuracy vs settled actuals "
                           "(the calibration loop's output; empty until a "
                           "gameweek has settled)",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["provider", "scope", "mae", "n_obs", "weight",
                             "earned"],
                "properties": {
                    "provider": {"type": "string"},
                    "scope": {"enum": ["overall", "own_gt5", "own_gt20"]},
                    "mae": {"type": ["number", "null"]},
                    "rmse": {"type": ["number", "null"]},
                    "baseline_mae": {"type": ["number", "null"]},
                    "baseline_rmse": {"type": ["number", "null"]},
                    "n_obs": {"type": "integer"},
                    "weight": {"type": "number"},
                    "earned": {"type": "boolean"},
                    "track_record_gws": {"type": ["integer", "null"]},
                },
            },
        },
        "weighting": {
            "enum": ["equal", "earned", "single_source"],
            "description": "which blend actually drove rows, matrix and the "
                           "team/position aggregates. 'single_source' when "
                           "one vendor's raw numbers are shown (no blend)."},
        "weighting_requested": {"enum": ["equal", "earned"]},
        "blocks_weighting": {
            "type": "object",
            "description": "the same answer per row-bearing block, so no "
                           "consumer has to assume: rows, matrix, by_team, "
                           "by_position, detail. detail is always 'raw', "
                           "which means per-source numbers with nothing "
                           "blended.",
            "additionalProperties": False,
            "required": ["rows", "matrix", "by_team", "by_position", "detail"],
            "properties": {
                "rows": {"enum": ["equal", "earned", "single_source"]},
                "matrix": {"enum": ["equal", "earned", "single_source"]},
                "by_team": {"enum": ["equal", "earned", "single_source"]},
                "by_position": {"enum": ["equal", "earned", "single_source"]},
                "detail": {"const": "raw"},
            },
        },
        "weights": {
            "type": ["object", "null"],
            "description": "the earned weights with the evidence that "
                           "earned them: the latest fit at query time, "
                           "whatever `weighting` was asked for, so the equal "
                           "view can still show what earned weighting would "
                           "use. null when no fit exists yet.",
            "additionalProperties": False,
            "required": ["as_of", "fit_id", "scored_gws", "n_floor", "rows"],
            "properties": {
                "as_of": {"type": "string",
                          "description": "when the fit was written"},
                "fit_id": {"type": "string"},
                "anchor_gw": {
                    "type": "integer",
                    "description": "the gameweek `applied_weight` is "
                                   "renormalised for"},
                "applied_by_gw": {
                    "type": "object",
                    "description": "{gw(str) -> {provider -> weight}}: the "
                                   "weight each provider actually carries in "
                                   "that gameweek's blend. The blend "
                                   "renormalises over the providers present, "
                                   "so a provider that stops projecting drops "
                                   "out and the rest rise. Providers with no "
                                   "weight are absent from the inner map.",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": {"type": "number"},
                    },
                },
                "scored_gws": {"type": "array", "items": {"type": "integer"},
                               "description": "the settled gameweeks the fit "
                                              "pooled"},
                "n_floor": {"type": "integer",
                            "description": "player-GW observations a provider "
                                           "needs before its weight is earned"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["provider", "weight", "n_obs", "mae",
                                     "baseline_mae", "earned"],
                        "properties": {
                            "provider": {"type": "string"},
                            "weight": {"type": "number",
                                       "description": "the fitted weight, "
                                                      "before renormalisation"},
                            "applied_weight": {
                                "type": ["number", "null"],
                                "description": "the weight this provider "
                                               "actually carries at "
                                               "anchor_gw, renormalised over "
                                               "the providers present there; "
                                               "0 when it does not cover that "
                                               "gameweek"},
                            "covers_anchor": {
                                "type": "boolean",
                                "description": "the provider has xPts at "
                                               "anchor_gw"},
                            "publishes_xpts": {
                                "type": "boolean",
                                "description": "false for a feed that carries "
                                               "no xPts at all (an injury "
                                               "feed, say): it is in the fit "
                                               "table but is not a projection "
                                               "provider"},
                            "n_obs": {"type": "integer"},
                            "mae": {"type": ["number", "null"],
                                    "description": "mean of the provider's "
                                                   "per-GW overall MAE"},
                            "baseline_mae": {"type": ["number", "null"],
                                             "description": "same, for the "
                                                            "equal-weight "
                                                            "consensus"},
                            "loss": {"type": ["number", "null"],
                                     "description": "pooled MSE the weight "
                                                    "was inverted from"},
                            "baseline_loss": {"type": ["number", "null"]},
                            "earned": {"type": "boolean"},
                            "holdout": {"type": ["string", "null"]},
                        },
                    },
                },
            },
        },
        "provider_accuracy": {
            # Nullable since compact mode: null there means "this view did not
            # serve it", named in omitted_blocks, never "no providers scored".
            "type": ["object", "null"],
            "description": "the accuracy strip: each provider's overall MAE "
                           "against the equal-weight consensus baseline, PER "
                           "settled gameweek (fact_projection_score, latest "
                           "scoring per cell). Empty rows until a gameweek "
                           "settles.",
            "additionalProperties": False,
            "required": ["scope", "scored_gws", "n_floor", "rows"],
            "properties": {
                "scope": {"const": "overall"},
                "scored_gws": {"type": "array", "items": {"type": "integer"}},
                "n_floor": {"type": "integer"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["provider", "gw", "mae", "baseline_mae",
                                     "n_obs", "meets_floor"],
                        "properties": {
                            "provider": {"type": "string"},
                            "gw": {"type": "integer"},
                            "mae": {"type": "number"},
                            "baseline_mae": {"type": ["number", "null"]},
                            "rmse": {"type": ["number", "null"]},
                            "baseline_rmse": {"type": ["number", "null"]},
                            "n_obs": {"type": "integer"},
                            "meets_floor": {
                                "type": "boolean",
                                "description": "n_obs >= n_floor; a cell "
                                               "below the floor is shown, "
                                               "never ranked"},
                        },
                    },
                },
            },
        },
        "actuals": {
            "type": "object",
            "description": "{code -> {gw -> official points}} for SETTLED "
                           "gameweeks inside the window, so the matrix can "
                           "show projection vs what actually happened",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
        "settled_gws": {"type": "array", "items": {"type": "integer"}},
        "gws": {"type": "array", "items": {"type": "integer"},
                "description": "the matrix window: anchor gw .. anchor+span-1, "
                               "clamped to coverage"},
        "matrix": {
            "type": "object",
            "description": "{code(str) -> {gw(str) -> xpts}} for the window, "
                           "same source/consensus selection as rows",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
        "by_team": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["team", "avg_xpts", "n_players"],
                "properties": {
                    "team": {"type": "string"},
                    "avg_xpts": {"type": "number"},
                    "n_players": {"type": "integer"},
                },
            },
        },
        "by_position": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["pos", "avg_xpts", "n_players"],
                "properties": {
                    "pos": {"type": "string"},
                    "avg_xpts": {"type": "number"},
                    "n_players": {"type": "integer"},
                },
            },
        },
        "detail": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["code", "name", "gw_from", "gw_to", "rows", "outlier"],
            "properties": {
                "code": {"type": "integer"},
                "name": {"type": "string"},
                "gw_from": {"type": "integer"},
                "gw_to": {"type": "integer"},
                "rows": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["gw", "source", "xpts"],
                        "properties": {
                            "gw": {"type": "integer"},
                            "source": {"type": "string"},
                            "xpts": {"type": ["number", "null"]},
                            "xmins": {"type": ["number", "null"]},
                            "p_appear": {"type": ["number", "null"]},
                            "xp_if_appears": {"type": ["number", "null"]},
                        },
                    },
                },
                "outlier": {
                    "type": ["object", "null"],
                    "additionalProperties": False,
                    "required": ["source", "gw", "xpts", "delta_vs_rest"],
                    "properties": {
                        "source": {"type": "string"},
                        "gw": {"type": "integer"},
                        "xpts": {"type": "number"},
                        "delta_vs_rest": {
                            "type": "number",
                            "description": "this source's xpts minus the mean "
                                           "of the other sources at the "
                                           "chosen GW",
                        },
                    },
                },
            },
        },
    },
}

#: One registered name, two honest shapes. The branches cannot both match:
#: the gameweek shape requires ``mode``, which the artefact shape's
#: additionalProperties:false forbids.
RESULT: dict[str, Any] = {"type": "object", "oneOf": [_ARTEFACT_RESULT, _GW_RESULT]}


def _num(v) -> float | None:
    """None for missing/NaN, float otherwise (pandas round-trips None as NaN)."""
    if v is None or v != v:
        return None
    return float(v)


def _rnd(v, places: int = 3) -> float | None:
    n = _num(v)
    return None if n is None else round(n, places)
