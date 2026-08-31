"""The chat surface of the Understat player profile (CHAT_ARCHITECTURE §6).

One panel script, two consumers: this tool reads through the same registered
``player_profile`` panel the xPoints drawer renders, so chat and dashboard can
never disagree about a player. What this tool adds -- and it is the ONLY thing
it adds -- is the sanctioned on-demand fetch: when the warehouse holds nothing
for a player, it calls :func:`fpl_edge.ingest.understat.fetch_player_profile`
(one search request, one data request, cached append-only), then reads again.
The panel itself never fetches; panels read.

Honesty rules the rendering must keep (and tests hold):

* Every answer states its **as-of** and that the numbers are **Understat's
  shot model, not FPL points** -- xG is blind to clean sheets, bonus and DC.
* ``goals - xG`` is presented as **finishing luck** with the panel's own
  caveat, never as skill.
* A name the strict resolver cannot place is a REFUSAL that lists Understat's
  candidates -- exact then containment, never edit distance -- because a
  profile written under the wrong player's code is a fabrication.

``player`` is free text from a chat: it is passed to resolvers as data, bound
as SQL parameters, never interpolated, never executed.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from fpl_mcp.server import mcp  # type: ignore

from fpl_mcp.tools import edge_tools as _edge
from fpl_mcp.tools import semantic_tools as _sem
from fpl_mcp.tools.chat_tools import DEFAULT_SEASON, _resolve_player

UTC = dt.timezone.utc


def _run_panel(code: int, season: str) -> dict:
    """The panel is the read path; the tool never grows a second one."""
    import fpl_edge.platform.scripts  # noqa: F401, PLC0415 - registration is the import
    from fpl_edge.platform.registry import run_script  # noqa: PLC0415

    return run_script("player_profile", {"code": int(code), "season": season},
                      db=_edge._db_path()).result


def _render(result: dict, label: str, *, fetched_note: str | None) -> str:
    src = result["source"]
    lines = [
        f"Player profile — {label}",
        f"as-of {result['as_of']} · understat id {src['understat_id']}"
        + (f" · resolved by {src['resolved_basis']}" if src.get("resolved_basis") else ""),
        f"NOTE: {result['note']}",
    ]
    if fetched_note:
        lines.append(fetched_note)

    t = result["totals"]
    lines.append(
        f"Totals over {t['matches']} match(es), {t['minutes']} min: "
        f"{t['shots']} shots, {t['goals']} goals vs {t['xg']:.2f} xG "
        f"({t['npg']} np goals vs {t['npxg']:.2f} npxG), "
        f"{t['assists']} assists vs {t['xa']:.2f} xA, {t['key_passes']} key passes."
    )
    fin = result["finishing"]
    lines.append(
        f"Finishing: {fin['goals_minus_xg']:+.2f} goals-minus-xG "
        f"({fin['npg_minus_npxg']:+.2f} non-penalty) — {fin['label']}"
    )
    mp = result["minutes_pattern"]
    lines.append(
        f"Minutes: {mp['starts']} start(s), {mp['sub_appearances']} sub cameo(s), "
        f"{mp['full_90s']} full 90(s), avg {mp['avg_minutes']} min; "
        f"last matches: {', '.join(str(m) for m in mp['last5_minutes'])} min."
    )
    per90 = result.get("per90")
    if per90:
        lines.append(
            f"Per 90: {per90['shots']:.2f} shots, {per90['xg']:.2f} xG, "
            f"{per90['xa']:.2f} xA, {per90['key_passes']:.2f} key passes."
        )

    lines.append("Per match (oldest first):")
    lines.append("| date | opp | min | shots | xG | goals | xA | assists | KP |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for m in result["matches"]:
        opp = (f"{m['opponent']} ({m['venue']})"
               if m.get("opponent") else (m.get("score") or "?"))
        started = "" if m["started"] else " (sub)"
        lines.append(
            f"| {m['date']} | {opp}{started} | {m['minutes']} | {m['shots']} | "
            f"{m['xg']:.2f} | {m['goals']} | {m['xa']:.2f} | {m['assists']} | "
            f"{m['key_passes']} |"
        )
    return "\n".join(lines)


@mcp.tool()
def player_profile(player: str, season: Optional[str] = None,
                   fetch_if_missing: bool = True) -> str:
    """One player's Understat season through the FPL lens.

    Shot volume, xG vs actual returns (finishing luck, labelled as luck), xA
    and key passes, and the minutes pattern. Reads the cached warehouse copy;
    when the warehouse holds nothing for the player it performs the one
    sanctioned on-demand fetch from understat.com, stores it append-only, and
    reads again. The numbers are Understat's shot model, NOT FPL points.

    ``player`` is a name ("Haaland", or "Palmer CHE" to break a tie);
    ``fetch_if_missing=False`` reports the gap instead of fetching.
    """
    unavailable = _edge._unavailable()
    if unavailable:
        return unavailable
    season = (season or DEFAULT_SEASON).strip()
    t = dt.datetime.now(UTC)

    wh = _sem._read()
    try:
        code, label, err = _resolve_player(wh, t, player, season)
    finally:
        wh.close()
    if err is not None:
        return err

    result = _run_panel(code, season)
    fetched_note = None
    if result.get("empty") and fetch_if_missing:
        from fpl_edge.ingest import understat as understat_mod  # noqa: PLC0415

        try:
            summary = understat_mod.fetch_player_profile(
                code, season, db=_edge._db_path())
        except understat_mod.UnresolvedPlayerError as exc:
            return (
                f"Refused to fetch: {exc}\n"
                "The resolver only accepts exact or containment name matches "
                "(never edit distance); nothing was stored."
            )
        except understat_mod.UnderstatError as exc:
            return f"Understat fetch failed: {exc}"
        fetched_note = (
            f"(Fetched from understat.com just now: {summary['rows_appended']} "
            f"new match row(s), {summary['rows_total']} total, "
            f"as understat id {summary['understat_id']} "
            f"[{summary['resolved_basis']}].)"
        )
        result = _run_panel(code, season)

    if result.get("empty"):
        return f"{label}: {result['reason']}"
    return _render(result, label, fetched_note=fetched_note)
