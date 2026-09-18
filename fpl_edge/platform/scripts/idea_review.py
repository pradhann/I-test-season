"""``idea_review``: how the logged ideas did, and what they say about the user.

The panel behind MCP tool 8 (``review_ideas``). ``idea_registry`` already
serves the ideas themselves; this serves what ``fpl_edge/interfaces/bias.py``
computes on top of them, which is a different question and was never on a
page.

Three things travel together here because reading any one alone misleads.

**The scoreboard.** Hits, the margin, the Brier score against the only
baseline worth beating (0.5 on everything), and the acted against skipped
split. That split is the reason the whole apparatus exists: the ideas worth
learning from are disproportionately the ones that were never acted on,
because those are the ones no other record of the season contains.

**The bias probes.** Four hypothesis tests against the user's own history,
Holm-Bonferroni adjusted across the probes that actually ran. Each carries its
n, its observed and expected rate, both p-values and the verdict sentence the
finding writes for itself. A probe below the observation floor reports that it
is below the floor rather than a number, and a probe whose population base
rate is degenerate reports that there is no test to run, which is a different
state from a weak result.

**The caveats.** ``review`` writes them, and they are the difference between a
Brier score that measures the engine and one that measures a price-rank prior.
They are carried verbatim, never summarised: a caveat that says the number
below measures a prior cannot be compressed without becoming the claim it
exists to prevent.

Read-only, like every panel. ``review`` takes the registry constructed over
the read copy, so nothing here settles an idea or writes an outcome; the
settlement chain owns that.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import SEASON_DEFAULT, empty

#: How many ideas the payload carries when the caller asks for them. The
#: scoreboard is computed over all of them regardless of this cap.
DEFAULT_IDEA_LIMIT = 50


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    return str(value)


def idea_review(
    wh,
    *,
    season: str = SEASON_DEFAULT,
    include_ideas: bool = True,
    limit: int = DEFAULT_IDEA_LIMIT,
) -> dict[str, Any]:
    """The scoreboard, the acted against skipped split, and the bias probes."""
    from fpl_edge.interfaces.bias import MIN_OBSERVATIONS, review
    from fpl_edge.interfaces.store import IdeaRegistry

    # The read copy is opened read-only, so the registry is built without its
    # migration step: applying DDL here would fail, and a warehouse that has
    # never held an idea is a state to report rather than one to create.
    registry, exists = IdeaRegistry.open_reader(wh)
    if not exists:
        return empty(
            "the idea registry has never been created in this warehouse. It "
            "is migrated in on first use, so submit an idea and both this "
            "panel and the bias probes fill in."
        )
    result = review(wh, season=season, registry=registry)
    board = result.scoreboard

    if board.n_total == 0:
        return empty(
            f"no idea has been logged for {season}, so there is nothing to "
            f"score and no history for the bias probes to test against. "
            f"Submit one and this fills in. "
            f"{' '.join(result.caveats)}".strip()
        )

    findings = [
        {
            "name": f.name,
            "question": f.question,
            "n": int(f.n),
            "observed": _num(f.observed),
            "expected": _num(f.expected),
            "units": f.units,
            "effect": _num(f.effect),
            "p_value": _num(f.p_value),
            "p_adjusted": _num(f.p_adjusted),
            "has_evidence": bool(f.has_evidence),
            "significant": bool(f.significant),
            "verdict": f.verdict(),
            "detail": f.detail,
        }
        for f in result.findings
    ]

    by_kind = [
        {
            "kind": str(row.get("kind")),
            "n": int(row.get("n") or 0),
            "resolved": int(row.get("resolved") or 0),
            "hit_rate": _num(row.get("hit_rate")),
        }
        for row in result.by_kind.to_dict("records")
    ]

    ideas: list[dict[str, Any]] = []
    ideas_reason = None
    if not include_ideas:
        ideas_reason = "the idea rows were not asked for on this run."
    else:
        frame = result.ideas
        if frame.empty:
            ideas_reason = "the review returned no idea rows for this season."
        else:
            records = frame.sort_values("created_utc", ascending=False)
            records = records.head(int(limit)).to_dict("records")
            for row in records:
                ideas.append({
                    "idea_id": str(row.get("idea_id")),
                    "created_utc": str(row.get("created_utc")),
                    "kind": _text(row.get("kind")),
                    "subject_name": _text(row.get("subject_name")),
                    "subject_code": (
                        None if _num(row.get("subject_code")) is None
                        else int(_num(row.get("subject_code")))
                    ),
                    "gw": (
                        None if _num(row.get("gw")) is None
                        else int(_num(row.get("gw")))
                    ),
                    "status": _text(row.get("status")),
                    "acted": bool(row.get("acted")),
                    "outcome": _text(row.get("outcome")),
                    "thesis": _text(row.get("thesis")),
                    "stance": _text(row.get("stance")),
                    "p_thesis_true": _num(row.get("p_thesis_true")),
                    "provider": _text(row.get("provider")),
                })
            if len(frame) > len(ideas):
                ideas_reason = (
                    f"{len(ideas)} of {len(frame)} ideas are listed, newest "
                    f"first. The scoreboard above is computed over all of them."
                )

    return {
        "season": season,
        "min_observations": int(MIN_OBSERVATIONS),
        "scoreboard": {
            "n_total": int(board.n_total),
            "n_resolved": int(board.n_resolved),
            "n_open": int(board.n_open),
            "n_void": int(board.n_void),
            "hit_rate": _num(board.hit_rate),
            "mean_margin": _num(board.mean_margin),
            "acted_n": int(board.acted_n),
            "acted_hit_rate": _num(board.acted_hit_rate),
            "unacted_n": int(board.unacted_n),
            "unacted_hit_rate": _num(board.unacted_hit_rate),
            "brier": _num(board.brier),
            "baseline_brier": _num(board.baseline_brier),
            "engine_agreed_hit_rate": _num(board.engine_agreed_hit_rate),
            "engine_disagreed_hit_rate": _num(board.engine_disagreed_hit_rate),
        },
        "findings": findings,
        "by_kind": by_kind,
        "caveats": list(result.caveats),
        "ideas": ideas,
        "ideas_reason": ideas_reason,
    }


PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": {"type": "string", "default": SEASON_DEFAULT, "minLength": 4,
                   "description": "The season whose ideas are reviewed."},
        "include_ideas": {"type": "boolean", "default": True, "description":
                          "Whether to carry the idea rows as well as the "
                          "scoreboard and the probes."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 500,
                  "default": DEFAULT_IDEA_LIMIT, "description":
                  "How many idea rows to carry, newest first. The scoreboard "
                  "is computed over every idea regardless of this cap."},
    },
}

_FINDING: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "question", "n", "observed", "expected", "units",
                 "effect", "p_value", "p_adjusted", "has_evidence",
                 "significant", "verdict", "detail"],
    "properties": {
        "name": {"type": "string", "description": "The probe's name."},
        "question": {"type": "string", "description":
                     "What this probe asks of the user's history."},
        "n": {"type": "integer", "description":
              "How many ideas carried the context this probe needs."},
        "observed": {"type": ["number", "null"], "description":
                     "The user's own rate or mean, null when no idea carried "
                     "the needed context."},
        "expected": {"type": ["number", "null"], "description":
                     "The population base rate the observed value is measured "
                     "against."},
        "units": {"type": "string", "description":
                  "Whether the two numbers above are a rate or a raw mean."},
        "effect": {"type": ["number", "null"], "description":
                   "Observed minus expected, in the same units."},
        "p_value": {"type": ["number", "null"], "description":
                    "The unadjusted p-value, null when no test could run."},
        "p_adjusted": {"type": ["number", "null"], "description":
                       "Holm-Bonferroni adjusted across the probes that ran. "
                       "Null means this probe was not in the family."},
        "has_evidence": {"type": "boolean", "description":
                         "True only when n reaches the observation floor and "
                         "a test actually ran."},
        "significant": {"type": "boolean", "description":
                        "True when the adjusted p-value is below 0.05, which "
                        "requires has_evidence first."},
        "verdict": {"type": "string", "description":
                    "The finding's own sentence, which says not enough "
                    "evidence or no test possible where that is the case. "
                    "Quote it rather than reading significance off the "
                    "numbers alone."},
        "detail": {"type": "string", "description":
                   "Why the probe could not run, when it could not."},
    },
}

_IDEA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["idea_id", "created_utc", "kind", "subject_name",
                 "subject_code", "gw", "status", "acted", "outcome", "thesis",
                 "stance", "p_thesis_true", "provider"],
    "properties": {
        "idea_id": {"type": "string", "description": "The registry id."},
        "created_utc": {"type": "string", "description":
                        "When the idea was submitted."},
        "kind": {"type": ["string", "null"], "description":
                 "What sort of claim it was."},
        "subject_name": {"type": ["string", "null"], "description":
                         "The player the idea is about."},
        "subject_code": {"type": ["integer", "null"], "description":
                         "That player's stable code."},
        "gw": {"type": ["integer", "null"], "description":
               "The gameweek the idea was about."},
        "status": {"type": ["string", "null"], "description":
                   "Open, resolved or void."},
        "acted": {"type": "boolean", "description":
                  "Whether the user acted on it. Scoring does not depend on "
                  "this flag; the split does."},
        "outcome": {"type": ["string", "null"], "description":
                    "Correct, incorrect or push, once resolved."},
        "thesis": {"type": ["string", "null"], "description":
                   "The falsifiable claim, as written down at the time."},
        "stance": {"type": ["string", "null"], "description":
                   "Whether the engine agreed with the thesis."},
        "p_thesis_true": {"type": ["number", "null"], "description":
                          "The engine's probability that the thesis was true."},
        "provider": {"type": ["string", "null"], "description":
                     "Which provider issued that probability. A prior is not "
                     "a points model, and the caveats say so."},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "min_observations", "scoreboard", "findings",
                 "by_kind", "caveats", "ideas", "ideas_reason"],
    "properties": {
        "season": {"type": "string", "description": "The season, echoed."},
        "min_observations": {"type": "integer", "description":
                             "The floor below which a probe reports that "
                             "there is not enough evidence."},
        "scoreboard": {
            "type": "object",
            "additionalProperties": False,
            "required": ["n_total", "n_resolved", "n_open", "n_void",
                         "hit_rate", "mean_margin", "acted_n",
                         "acted_hit_rate", "unacted_n", "unacted_hit_rate",
                         "brier", "baseline_brier", "engine_agreed_hit_rate",
                         "engine_disagreed_hit_rate"],
            "properties": {
                "n_total": {"type": "integer", "description":
                            "Ideas logged this season."},
                "n_resolved": {"type": "integer", "description":
                               "Ideas with an outcome."},
                "n_open": {"type": "integer", "description":
                           "Ideas still waiting on a result."},
                "n_void": {"type": "integer", "description":
                           "Ideas that can no longer be settled."},
                "hit_rate": {"type": ["number", "null"], "description":
                             "Share of decided ideas that were correct, "
                             "pushes excluded."},
                "mean_margin": {"type": ["number", "null"], "description":
                                "Mean signed points margin against the "
                                "comparator, with a fade counted the other "
                                "way round."},
                "acted_n": {"type": "integer", "description":
                            "Decided ideas the user acted on."},
                "acted_hit_rate": {"type": ["number", "null"], "description":
                                   "Hit rate among those."},
                "unacted_n": {"type": "integer", "description":
                              "Decided ideas the user skipped."},
                "unacted_hit_rate": {"type": ["number", "null"], "description":
                                     "Hit rate among those. The comparison "
                                     "with the acted rate is the question "
                                     "this panel exists for."},
                "brier": {"type": ["number", "null"], "description":
                          "Brier score of the engine's probabilities. Read "
                          "the caveats before reading this number."},
                "baseline_brier": {"type": ["number", "null"], "description":
                                   "Brier score of 0.5 on everything, the "
                                   "only baseline worth beating."},
                "engine_agreed_hit_rate": {"type": ["number", "null"],
                                           "description":
                                           "Hit rate where the engine put the "
                                           "thesis above even money."},
                "engine_disagreed_hit_rate": {"type": ["number", "null"],
                                              "description":
                                              "Hit rate where it did not."},
            },
        },
        "findings": {"type": "array", "items": _FINDING, "description":
                     "The bias probes, with their statistics and their own "
                     "verdict sentences."},
        "by_kind": {
            "type": "array",
            "description": "Ideas split by the kind of claim they made.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "n", "resolved", "hit_rate"],
                "properties": {
                    "kind": {"type": "string", "description": "The claim kind."},
                    "n": {"type": "integer", "description":
                          "Ideas of this kind."},
                    "resolved": {"type": "integer", "description":
                                 "How many of them were decided."},
                    "hit_rate": {"type": ["number", "null"], "description":
                                 "Hit rate among the decided ones."},
                },
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"},
                    "description":
                    "What the review says must be read alongside the numbers "
                    "above, verbatim. Carry them with any figure quoted from "
                    "the scoreboard."},
        "ideas": {"type": "array", "items": _IDEA, "description":
                  "The idea rows, newest first, capped by the limit."},
        "ideas_reason": {"type": ["string", "null"], "description":
                         "Why the idea list is short or absent, or null when "
                         "it carries every idea."},
    },
}

register_script(
    name="idea_review",
    fn=idea_review,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Idea review",
    description="How the logged ideas did and what they say about the user: "
                "the scoreboard, the acted against skipped split, calibration "
                "against an even-money baseline, and four bias probes with "
                "their statistics and caveats.",
)
