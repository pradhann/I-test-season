"""The envelope every tool returns, and the only path to ``run_script``.

One function, :func:`panel_call`, runs one panel and wraps the result. No tool
module calls ``run_script`` itself, which is what makes "a tool call is one
panel run" a property of the code rather than a convention.

THE ENVELOPE

::

    {"ok": true, "tool": "fixtures", "panel": "fixture_board",
     "result": {...}, "provenance": {...},
     "budget": {"duration_ms": 412, "performance": "ok", "notes": []},
     "gap": null}

``result`` is the panel's payload verbatim. No reshaping, no renaming, no
rounding: the bytes a model reads are the bytes the browser reads, which is
the whole reason the MCP server was rewritten onto the panels. ``provenance``
is ``ScriptRun.provenance`` unchanged, so ``repo_sha`` and ``generated_at``
are identical to the ones the browser receives for the same run.

A GAP IS NOT AN ERROR, AND AN ERROR IS NOT A GAP

``run_script`` admits ``{empty: true, reason: "..."}`` for every script, and
the dossier reports per-section gaps inside a populated result. Both reach the
caller as ``gap``: ``kind`` is ``empty`` for a whole-panel gap and ``sections``
for a result whose ``gaps`` array is non-empty. ``say`` is constant text,
present so the instruction travels with the data rather than living only in a
tool description the model may have compacted away.

An exception is a different thing and stays a different thing. ``run_script``
re-raises unless the warehouse is unseeded, so the envelope comes back as
``{"ok": false, "error": {...}}`` naming the exception type, and the caller is
told to report a failure rather than an absence. That is the same distinction
the web draws between "no data" and "could not load", from the same code.

THE BUDGET

The registry's soft budget is 10 seconds. Over it, the run still returns,
marked ``performance: "over_budget"`` with the registry's own note. This does
not convert an overrun into a refusal, and that is deliberate: if this server
refused what the browser renders, the two surfaces would disagree at exactly
the moment the warehouse is slow, which is the deadline. Work known to exceed
the budget is refused before it starts instead, by not being offered: the live
minutes refit is not a dossier parameter and the solver is
``solve_start`` plus ``solve_status`` rather than an in-call solve.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.mcp import context

UTC = dt.UTC

#: The instruction that travels with every gap. Constant text, repeated in one
#: line of each tool's description, because a model that has compacted the
#: description away still has the payload.
GAP_SAY = (
    "Report this as not available and quote the reason. Do not substitute "
    "another source, another season or an estimate."
)

#: The instruction that travels with every failure.
ERROR_SAY = (
    "Report this as a failure to load, not as an absence of data. The panel "
    "raised; it did not answer that there is nothing."
)


def _gap_from(result: dict[str, Any]) -> dict[str, Any] | None:
    """The gap a panel result carries, in the two shapes a panel can carry one."""
    if result.get("empty") is True:
        return {
            "kind": "empty",
            "reason": str(result.get("reason") or "the panel returned nothing."),
            "say": GAP_SAY,
        }
    gaps = result.get("gaps")
    if isinstance(gaps, list) and gaps:
        return {
            "kind": "sections",
            "reason": (
                f"these sections have no data at this instant: "
                f"{', '.join(_gap_name(g) for g in gaps)}. Each one carries "
                f"its own reason in the result."
            ),
            "say": GAP_SAY,
        }
    return None


def _gap_name(entry: Any) -> str:
    """The section name out of a gap entry, in the three shapes panels use.

    ``player_dossier`` lists bare section keys, ``creator_report_card`` lists
    ``{key, what, fix}`` and ``episode_summary`` lists ``{section, gap}``. All
    three mean the same thing: a named part of this payload is empty and says
    why. Only the key is lifted here; the reason stays in the result, where it
    belongs beside the rest of the section.
    """
    if isinstance(entry, dict):
        for field in ("key", "section", "name"):
            if entry.get(field):
                return str(entry[field])
        return "unnamed section"
    return str(entry)


def panel_call(
    tool: str,
    panel: str,
    params: dict[str, Any] | None = None,
    *,
    mode: str | None = None,
) -> dict[str, Any]:
    """Run one panel and wrap it. The only call to ``run_script`` in this package.

    ``params`` is passed through after ``None`` values are dropped, so a tool
    can declare an optional argument without having to know whether the panel
    prefers an absent key or an explicit null.

    ``mode`` names the shape the caller asked the panel for, on the envelope,
    next to the result rather than inside it. Two tools serve a reduced payload
    on request (``projections`` compact, ``transfer_plan`` headline) because
    the full ones exceed the caller's payload cap, and a reader of a trimmed
    payload has to be able to tell a panel that served less from a panel that
    found less. The result itself stays the panel's bytes verbatim; the panel
    names its own omissions inside it.
    """
    from fpl_edge.platform import scripts  # noqa: F401 - registers the scripts
    from fpl_edge.platform.registry import (
        ParamsInvalid,
        ResultInvalid,
        run_script,
    )

    clean = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        # The requesting user reaches the panel as a context, never as a
        # parameter: the squad, brief and plan panels dropped entry_id from
        # their params on 2026-09-18 and a param would be refused as unknown.
        run = run_script(panel, clean, db=context.db_path(), ctx=context.user_context())
    except ParamsInvalid as exc:
        return error(tool, str(exc), kind="ParamsInvalid", panel=panel,
                     params=clean)
    except ResultInvalid as exc:
        return error(tool, str(exc), kind="ResultInvalid", panel=panel,
                     params=clean)
    except Exception as exc:  # noqa: BLE001 - the panel's own message is the useful part
        return error(tool, f"{type(exc).__name__}: {exc}",
                     kind=type(exc).__name__, panel=panel, params=clean)

    return {
        "ok": True,
        "tool": tool,
        "panel": panel,
        "mode": mode,
        "result": run.result,
        "provenance": run.provenance,
        "budget": {
            "duration_ms": run.duration_ms,
            "performance": run.performance,
            "notes": list(run.notes),
        },
        "gap": _gap_from(run.result),
    }


def store_call(
    tool: str,
    store: str,
    result: Any,
    *,
    gap: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The same envelope for a write, which runs through a store, not a panel.

    ``panel`` is null and ``store`` names the module that performed the write,
    so the list of writes this server can make is readable off the payloads as
    well as off section 1.4 of the spec.
    """
    return {
        "ok": True,
        "tool": tool,
        "panel": None,
        "store": store,
        "result": result,
        "provenance": {"store": store, "repo_sha": _repo_sha()},
        "budget": None,
        "gap": gap,
    }


def refusal(tool: str, reason: str, **extra: Any) -> dict[str, Any]:
    """A request this server will not serve, with the reason and what to do.

    Distinct from an error: nothing went wrong, the answer is no. An ambiguous
    player name and a request the budget cannot hold both land here.
    """
    payload: dict[str, Any] = {
        "ok": False,
        "tool": tool,
        "panel": None,
        "result": None,
        "refused": True,
        "reason": reason,
        "say": (
            "Quote this reason to the user and follow what it asks for. Do "
            "not guess past it."
        ),
    }
    payload.update(extra)
    return payload


def error(
    tool: str,
    message: str,
    *,
    kind: str,
    panel: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A failure to load, which is not an absence of data."""
    return {
        "ok": False,
        "tool": tool,
        "panel": panel,
        "result": None,
        "error": {
            "type": kind,
            "message": message,
            "params": params or {},
            "say": ERROR_SAY,
        },
    }


def _repo_sha() -> str:
    from fpl_edge.platform.registry import repo_sha

    return repo_sha()


def resolve_player(
    tool: str,
    player: str,
    *,
    season: str,
    as_of: str | None = None,
) -> tuple[int | None, dict[str, Any] | None]:
    """Free text to one stable player code, or the candidate list.

    Returns ``(code, None)`` or ``(None, refusal)``. Resolution happens here,
    before the panel run, so an ambiguous name costs a round trip rather than
    a panel budget, and it goes through the engine's one resolver: the same
    matcher that knows "salah" and "saliba" are two edits apart and must not
    be confused, that "kdb" is a nickname no string distance recovers, and
    that a tie is answered by asking rather than by breaking it on ownership.
    Reimplementing any of that here would mean a name resolves one way in chat
    and another in the browser.

    The panel downstream takes ``code``, the stable cross-season player code,
    never an element id.
    """
    from fpl_edge.interfaces.dossier import resolve
    from fpl_edge.store.warehouse import Warehouse

    missing = context.warehouse_missing()
    if missing:
        return None, refusal(tool, missing)
    try:
        when = context.parse_as_of(as_of)
    except ValueError as exc:
        return None, refusal(tool, str(exc))

    instant = when or dt.datetime.now(UTC)
    wh = Warehouse.read_copy(context.db_path())
    try:
        code, clarification, _ = resolve(
            wh.snapshot_at(instant), str(player), season=season,
        )
    except Exception as exc:  # noqa: BLE001 - a failed resolve is a refusal
        return None, refusal(
            tool, f"could not resolve {player!r}: {type(exc).__name__}: {exc}",
        )
    finally:
        wh.close()

    if code is not None:
        return int(code), None
    candidates = [
        {"code": int(c.code), "label": c.label, "hint": c.hint,
         "score": round(float(c.score), 3)}
        for c in (clarification.candidates if clarification else ())
    ]
    return None, refusal(
        tool,
        (clarification.question if clarification
         else f"could not resolve {player!r} to one player."),
        candidates=candidates,
        resolution="ambiguous" if candidates else "not_found",
    )
