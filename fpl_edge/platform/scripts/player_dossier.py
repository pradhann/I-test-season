"""``player_dossier``: the sixteen-section player view, as a panel.

``fpl_edge/interfaces/dossier/`` already assembles everything the engine knows
about one player at one instant: sixteen sections in a fixed order, each
carrying a rendered body or a named reason it is absent, plus the warnings the
loaders raised. This panel is the thin wrapper that puts that builder behind
the registry, so the browser and the MCP toolbelt read one implementation
instead of two.

WHAT THIS PANEL ADDS AND WHAT IT DELIBERATELY DOES NOT

It adds the registry's contract: validated params, a validated result, a fresh
read copy closed afterwards, and provenance stamped with the repo sha. It adds
nothing to the numbers. ``Dossier.to_dict`` is returned as it comes back, key
for key, so a section body printed on a page is the same string the CLI
prints.

``simulate=True`` is not exposed. It refits the minutes model live, which took
about 95 seconds when the MCP tool offered it, against a 10 second panel
budget. The cached projection artefact is what the projection section reads,
which is what every other panel reads, and a surface that took ten times its
budget would be a worse answer than a fast one that says where the number came
from.

THE RESULT SCHEMA, AND WHY IT IS SHAPED THIS WAY

A section's body is a rendered string, not a structure: ``Section`` in
``dossier/sections.py`` holds ``key``, ``title`` and exactly one of ``body``
and ``gap``. There is no richer per-section shape to validate, so the schema
types the array as ``{key, title, body, gap}`` and requires every key in
``EXPECTED`` to be present exactly once, in order. That is the whole contract
``tests/unit/test_dossier.py`` pins, expressed as a schema: a builder that
disappears shortens the array and the result is rejected rather than rendered
short.

THE ONE PLACE THIS PANEL CAN DISAGREE WITH ITS CALLER

The builder resolves a player from free text, because that is what its other
three surfaces hand it. A panel takes ``code``, the stable cross-season player
code. The panel therefore looks the code's name up first and hands the builder
that name, then checks the code it got back. A club with two players FPL
displays under one web name is the case where those two can differ, and the
panel reports the mismatch as an empty naming both codes rather than serving
one player's dossier under another's code.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import SEASON_DEFAULT, empty, q

UTC = dt.UTC

#: The longest fixture horizon a caller may rate ahead. Beyond this the
#: fixture section is reading a schedule the Dixon-Coles fit has no opinion
#: about, and the run costs budget for rows nobody reads.
MAX_HORIZON = 10


def _as_of(value: str | None) -> dt.datetime:
    """Parse the optional instant. Timezone-aware UTC, always."""
    if not value:
        return dt.datetime.now(UTC)
    parsed = dt.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(
            "as_of must carry a timezone, for example 2026-08-18T22:50:00Z"
        )
    return parsed.astimezone(UTC)


def _name_for(wh, when: dt.datetime, code: int, season: str) -> str | None:
    """The fullest name the warehouse holds for this code in this season.

    The full name is preferred over the web name because the resolver breaks
    ties on it: two players FPL displays as "Palmer" have different second
    names, and handing the builder "Cole Palmer" resolves what "Palmer" cannot.
    """
    rows = q(
        wh,
        "SELECT web_name, first_name, second_name FROM ("
        "  SELECT *, row_number() OVER ("
        "    PARTITION BY season, code ORDER BY as_of DESC) rn "
        "  FROM dim_player WHERE as_of <= $1 AND season = $2 AND code = $3"
        ") WHERE rn = 1",
        (when, season, int(code)),
    )
    if rows.empty:
        return None
    row = rows.iloc[0]
    first = str(row.get("first_name") or "").strip()
    second = str(row.get("second_name") or "").strip()
    full = f"{first} {second}".strip()
    return full or str(row.get("web_name") or "").strip() or None


def player_dossier(
    wh,
    *,
    code: int,
    season: str = SEASON_DEFAULT,
    gw: int | None = None,
    horizon_gws: int = 5,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Everything the engine knows about one player at one instant.

    Sixteen sections in a fixed order, each with a body or the reason it has
    none, plus the warnings the loaders raised while assembling them.
    """
    from fpl_edge.interfaces.dossier import build

    when = _as_of(as_of)
    name = _name_for(wh, when, int(code), season)
    if name is None:
        return empty(
            f"no player with code {int(code)} in the {season} player list at "
            f"{when:%Y-%m-%d %H:%M}Z. The code is the stable cross-season FPL "
            f"player code, not an element id."
        )

    # The builder reads the cached projection artefact, which lives beside the
    # ORIGINAL warehouse file rather than beside the read copy this panel was
    # handed. run_script stamps the real path on the handle for exactly this.
    source = getattr(wh, "source_path", None)
    projection_path = (
        Path(source).parent / "gw1_projection.parquet" if source is not None
        else None
    )
    kwargs: dict[str, Any] = {}
    if projection_path is not None:
        kwargs["projection_path"] = projection_path

    built, clarification = build(
        wh, name, season=season, as_of=when, gw=gw,
        horizon_gws=int(horizon_gws), simulate=False, **kwargs,
    )
    if built is None:
        question = clarification.question if clarification else ""
        candidates = ", ".join(
            f"{c.label} (code {int(c.code)})"
            for c in (clarification.candidates if clarification else ())
        )
        return empty(
            f"code {int(code)} is named {name!r} in {season}, and the player "
            f"resolver could not turn that name back into one player. "
            f"{question} Candidates: {candidates or 'none'}."
        )
    if int(built.code) != int(code):
        return empty(
            f"code {int(code)} is named {name!r} in {season}, and that name "
            f"resolves to code {int(built.code)} instead. Two players share "
            f"this name, so no dossier is served rather than one player's "
            f"numbers under the other's code."
        )

    payload = built.to_dict()
    payload["horizon_gws"] = int(horizon_gws)
    payload["simulate"] = False
    payload["simulate_note"] = (
        "the projection section reads the cached projection artefact. A live "
        "refit takes about 95 seconds against a 10 second panel budget, so "
        "this surface does not offer one."
    )
    return payload


PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code"],
    "properties": {
        "code": {"type": "integer", "minimum": 1, "description":
                 "The stable cross-season FPL player code, not an element id."},
        "season": {"type": "string", "default": SEASON_DEFAULT, "minLength": 4,
                   "description":
                   "The season whose player list the code is read from."},
        "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38,
               "default": None, "description":
               "The gameweek the dossier is about. Null uses the next open "
               "gameweek at this instant."},
        "horizon_gws": {"type": "integer", "minimum": 1, "maximum": MAX_HORIZON,
                        "default": 5, "description":
                        "How many gameweeks ahead the fixtures section rates."},
        "as_of": {"type": ["string", "null"], "default": None, "description":
                  "ISO-8601 instant carrying a timezone. Everything is read "
                  "as it was knowable then. Null means now."},
    },
}

_SECTION: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "title", "body", "gap"],
    "properties": {
        "key": {"type": "string", "minLength": 1, "description":
                "The section key, one of the sixteen in dossier.EXPECTED."},
        "title": {"type": "string", "minLength": 1, "description":
                  "What the section answers, in a phrase."},
        "body": {"type": ["string", "null"], "description":
                 "The rendered section, or null when there is nothing to "
                 "render. Exactly one of body and gap is set."},
        "gap": {"type": ["string", "null"], "description":
                "Why this section is empty, or null when it has a body. A "
                "gap is a finding, not a failure: quote it rather than "
                "substituting another source."},
    },
}

RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["query", "code", "web_name", "name", "season", "gw", "as_of",
                 "build_ms", "sections", "gaps", "warnings", "horizon_gws",
                 "simulate", "simulate_note"],
    "properties": {
        "query": {"type": "string", "description":
                  "The name this panel handed the builder, which is the full "
                  "name held for the requested code."},
        "code": {"type": "integer", "description":
                 "The player code the dossier is about, equal to the code "
                 "that was asked for."},
        "web_name": {"type": "string", "description":
                     "The name FPL displays for this player."},
        "name": {"type": "string", "description":
                 "The fuller name, with the web name in front when the two "
                 "differ."},
        "season": {"type": "string", "description": "The season, echoed."},
        "gw": {"type": "integer", "description":
               "The gameweek the sections are about."},
        "as_of": {"type": "string", "description":
                  "The instant everything below was read as of, ISO-8601."},
        "build_ms": {"type": "number", "description":
                     "How long the sixteen builders took, milliseconds."},
        "sections": {"type": "array", "items": _SECTION, "minItems": 1,
                     "description":
                     "Sixteen sections in dossier.EXPECTED order, each with a "
                     "body or the reason it has none."},
        "gaps": {"type": "array", "items": {"type": "string"}, "description":
                 "The keys of the sections that have no body, listed so a "
                 "reader can see what is missing without walking the array."},
        "warnings": {"type": "array", "items": {"type": "string"},
                     "description":
                     "What the loaders raised while assembling the sections. "
                     "Read these before acting on a body above."},
        "horizon_gws": {"type": "integer", "description":
                        "The fixture horizon this run rated, echoed."},
        "simulate": {"const": False, "description":
                     "Always false. This surface does not refit the minutes "
                     "model, and says so rather than leaving the reader to "
                     "assume the projection is live."},
        "simulate_note": {"type": "string", "description":
                          "Where the projection section's numbers came from."},
    },
}

register_script(
    name="player_dossier",
    fn=player_dossier,
    params_schema=PARAMS,
    result_schema=RESULT,
    title="Player dossier",
    description="Everything the engine knows about one player at one instant: "
                "sixteen sections from identity and price through set pieces, "
                "odds and availability, each with a body or the named reason "
                "it is empty.",
)
