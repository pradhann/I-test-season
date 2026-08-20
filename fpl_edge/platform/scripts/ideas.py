"""idea_registry — your theses, the engine's verdict on each, and the outcome.

The join is three-way on purpose, because each table answers a different
question and the panel is worthless without all three:

* ``idea`` — what you said, when, and whether you acted on it.
* ``idea_verdict`` — what the engine thought at the time (stance, P(true)),
  latest verdict per idea only. An idea can be re-judged as evidence arrives;
  showing every verdict would make a well-tracked idea look like noise.
* ``idea_observation`` — how it actually went, per gameweek.

The point of the panel is the gap between the second and the third, held next
to whether you acted. "Acted-on ideas do worse than skipped ones" is a finding
you can only see when all three are on one row.
"""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import empty, q, season_param

PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "status": {
            "type": "string",
            "enum": ["all", "open", "resolved", "void"],
            "default": "all",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "rows", "row_count", "summary"],
    "properties": {
        "season": {"type": "string"},
        "row_count": {"type": "integer"},
        "as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["n_total", "n_open", "n_resolved", "n_acted"],
            "properties": {
                "n_total": {"type": "integer"},
                "n_open": {"type": "integer"},
                "n_resolved": {"type": "integer"},
                "n_void": {"type": "integer"},
                "n_acted": {"type": "integer"},
                "n_with_verdict": {"type": "integer"},
                "hit_rate": {"type": ["number", "null"]},
            },
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["idea_id", "created_utc", "thesis", "status", "acted"],
                "properties": {
                    "idea_id": {"type": "string"},
                    "created_utc": {"type": "string"},
                    "raw_text": {"type": ["string", "null"]},
                    "thesis": {"type": ["string", "null"]},
                    "kind": {"type": ["string", "null"]},
                    "subject_name": {"type": ["string", "null"]},
                    "comparator_label": {"type": ["string", "null"]},
                    "gw": {"type": ["integer", "null"]},
                    "horizon_gws": {"type": ["integer", "null"]},
                    "status": {"type": ["string", "null"]},
                    "acted": {"type": "boolean"},
                    "outcome": {"type": ["string", "null"]},
                    "subject_points": {"type": ["number", "null"]},
                    "comparator_points": {"type": ["number", "null"]},
                    "verdict": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "stance": {"type": ["string", "null"]},
                            "p_thesis_true": {"type": ["number", "null"]},
                            "confidence": {"type": ["string", "null"]},
                            "provider": {"type": ["string", "null"]},
                            "issued_utc": {"type": ["string", "null"]},
                            "degraded": {"type": ["boolean", "null"]},
                        },
                    },
                    "observations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["gw"],
                            "properties": {
                                "gw": {"type": "integer"},
                                "subject_points": {"type": ["number", "null"]},
                                "comparator_points": {"type": ["number", "null"]},
                                "note": {"type": ["string", "null"]},
                                "observed_utc": {"type": ["string", "null"]},
                            },
                        },
                    },
                },
            },
        },
    },
}

_STATUS_SQL = {
    "all": "",
    "open": "AND lower(i.status) = 'open'",
    "resolved": "AND lower(i.status) = 'resolved'",
    "void": "AND lower(i.status) = 'void'",
}


def _s(v):
    return None if v is None or v != v else str(v)


def _f(v):
    return None if v is None or v != v else float(v)


def _i(v):
    return None if v is None or v != v else int(v)


def idea_registry(
    wh, *, season: str, status: str = "all", limit: int = 50
) -> dict[str, Any]:
    """Ideas with their latest engine verdict and their realised observations."""
    tables = q(
        wh,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name IN ('idea', 'idea_verdict', 'idea_observation')",
    )
    have = set(tables["table_name"]) if not tables.empty else set()
    if "idea" not in have:
        return empty(
            "The idea registry has never been created in this warehouse. It is "
            "migrated in on first use -- submit an idea (`fpl idea submit`, or "
            "text the bot) and this panel fills in."
        )

    ideas = q(
        wh,
        f"""
        SELECT i.idea_id, i.created_utc, i.raw_text, i.thesis, i.kind,
               i.subject_name, i.comparator_label, i.gw, i.horizon_gws,
               i.status, i.acted, i.outcome, i.subject_points, i.comparator_points
        FROM idea i
        WHERE i.season = ? {_STATUS_SQL.get(status, "")}
        ORDER BY i.created_utc DESC
        LIMIT ?
        """,
        (season, int(limit)),
    )
    if ideas.empty:
        which = "" if status == "all" else f" with status {status!r}"
        return empty(
            f"No {season} ideas{which} recorded yet. Every idea in here arrives "
            f"from you -- text the bot a belief ('I like Saka', 'fade Haaland') "
            f"and it is parsed, judged and tracked from that moment."
        )

    ids = [str(x) for x in ideas["idea_id"].tolist()]
    placeholders = ", ".join("?" for _ in ids)

    verdicts = {}
    if "idea_verdict" in have:
        vf = q(
            wh,
            f"""
            SELECT idea_id, stance, p_thesis_true, confidence, provider,
                   issued_utc, degraded
            FROM (
                SELECT *, row_number() OVER (PARTITION BY idea_id
                                             ORDER BY issued_utc DESC) rn
                FROM idea_verdict WHERE idea_id IN ({placeholders})
            ) WHERE rn = 1
            """,
            tuple(ids),
        )
        for _, r in vf.iterrows():
            verdicts[str(r["idea_id"])] = {
                "stance": _s(r["stance"]),
                "p_thesis_true": _f(r["p_thesis_true"]),
                "confidence": _s(r["confidence"]),
                "provider": _s(r["provider"]),
                "issued_utc": _s(r["issued_utc"]),
                "degraded": None if r["degraded"] is None or r["degraded"] != r["degraded"]
                            else bool(r["degraded"]),
            }

    observations: dict[str, list[dict[str, Any]]] = {}
    if "idea_observation" in have:
        of = q(
            wh,
            f"SELECT idea_id, gw, observed_utc, subject_points, comparator_points, note "
            f"FROM idea_observation WHERE idea_id IN ({placeholders}) ORDER BY gw",
            tuple(ids),
        )
        for _, r in of.iterrows():
            observations.setdefault(str(r["idea_id"]), []).append({
                "gw": int(r["gw"]),
                "observed_utc": _s(r["observed_utc"]),
                "subject_points": _f(r["subject_points"]),
                "comparator_points": _f(r["comparator_points"]),
                "note": _s(r["note"]),
            })

    rows = []
    for _, r in ideas.iterrows():
        iid = str(r["idea_id"])
        rows.append({
            "idea_id": iid,
            "created_utc": str(r["created_utc"]),
            "raw_text": _s(r["raw_text"]),
            "thesis": _s(r["thesis"]),
            "kind": _s(r["kind"]),
            "subject_name": _s(r["subject_name"]),
            "comparator_label": _s(r["comparator_label"]),
            "gw": _i(r["gw"]),
            "horizon_gws": _i(r["horizon_gws"]),
            "status": _s(r["status"]),
            "acted": bool(r["acted"]) if r["acted"] == r["acted"] and r["acted"] is not None
                     else False,
            "outcome": _s(r["outcome"]),
            "subject_points": _f(r["subject_points"]),
            "comparator_points": _f(r["comparator_points"]),
            "verdict": verdicts.get(iid),
            "observations": observations.get(iid, []),
        })

    n_resolved = sum(1 for r in rows if (r["status"] or "").lower() == "resolved")
    hits = [r for r in rows if (r["outcome"] or "").lower() in ("hit", "miss")]
    hit_rate = (
        round(sum(1 for r in hits if (r["outcome"] or "").lower() == "hit") / len(hits), 3)
        if hits else None
    )

    notes = []
    if "idea_observation" not in have or not observations:
        notes.append(
            "No per-gameweek observations recorded yet: ideas are scored by the "
            "post-gameweek settlement job, so outcomes appear after GW1 finalises."
        )
    if "idea_verdict" not in have:
        notes.append("This warehouse has no idea_verdict table, so no engine stances are shown.")

    return {
        "season": season,
        "row_count": len(rows),
        "rows": rows,
        "as_of": max((r["created_utc"] for r in rows), default=None),
        "summary": {
            "n_total": len(rows),
            "n_open": sum(1 for r in rows if (r["status"] or "").lower() == "open"),
            "n_resolved": n_resolved,
            "n_void": sum(1 for r in rows if (r["status"] or "").lower() == "void"),
            "n_acted": sum(1 for r in rows if r["acted"]),
            "n_with_verdict": sum(1 for r in rows if r["verdict"] is not None),
            "hit_rate": hit_rate,
        },
        "notes": notes,
    }


register_script(
    "idea_registry",
    idea_registry,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Idea registry",
    description="Your ideas with the engine's latest verdict and their realised outcomes.",
)
