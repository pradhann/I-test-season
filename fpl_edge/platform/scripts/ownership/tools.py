"""The three views of the selected field: diff, what-if and momentum.

Each takes measurements the rest of the package produced and reshapes them;
none of them reads the warehouse except ``_tool_momentum``, which needs the
crawled gameweek series."""

from __future__ import annotations

from typing import Any

from fpl_edge.platform.scripts.common import q


def _tool_squad_diff(
    *,
    all_rows: list[dict[str, Any]],
    limit: int,
    sel_by_code: dict[int, dict],
    sel_n: int | None,
    squad: set | None,
    squad_meta: dict[str, Any],
    template: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], set]:
    """Tool 1: the squad against the selected field, player by player."""
    # -- tool 1: squad-vs-field diff ----------------------------------------
    # Every player on EITHER side of the identity: what I hold, what the field
    # holds, and the term itself. A player I own whom the field does not is the
    # single most important row on this panel — it is what a differential IS —
    # so it is built from the union of the two sides, never from the field's
    # top rows alone.
    by_code_row = {r["code"]: r for r in all_rows}
    field_top = [c for c, _m in sorted(
        sel_by_code.items(), key=lambda kv: -(kv[1]["eo"] or 0))[:limit]]
    rows_codes = {r["code"] for r in template}
    squad_codes = set(squad) if squad is not None else set()
    readable = bool(squad_meta.get("readable"))
    has_mult = bool(squad_meta.get("has_multipliers"))

    if not readable:
        your_note = ("your squad could not be read, so your side of the "
                     "identity is unknown, which is not zero")
    elif not has_mult:
        your_note = ("your squad read carries no multipliers, so ownership can "
                     "be compared but EO cannot, and it is not assumed "
                     "to be 1x")
    else:
        your_note = None

    diff_rows: list[dict[str, Any]] = []
    for code in sorted(squad_codes | rows_codes | set(field_top)):
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
            f_eo_tc = m["eo_tc_pp"] if m else 0.0
            f_eo_ex = m["eo_ex_tc"] if m else 0.0
            f_owned = m["owned_by"] if m else 0
            f_capped = m["captained_by"] if m else 0
            f_tripled = m["tripled_by"] if m else 0
        else:
            f_own = f_eo = f_cap = f_eo_tc = f_eo_ex = None
            f_owned = f_capped = f_tripled = None
        diff_rows.append({
            "code": code,
            "name": r["name"] if r else str(code),
            "pos": r["pos"] if r else None,
            "team": r["team"] if r else None,
            "team_code": r["team_code"] if r else None,
            "price": r["price"] if r else None,
            "in_squad": in_squad,
            "in_panel_rows": code in rows_codes,
            "in_field_top": code in set(field_top),
            "your_mult": your_mult,
            "your_role": r["your_role"] if r else None,
            "your_eo_pct": your_eo,
            "your_own_pct": your_own,
            "field_eo_pct": f_eo,
            "field_eo_ex_tc_pct": f_eo_ex,
            "field_eo_tc_pp": f_eo_tc,
            "field_own_pct": f_own,
            "field_cap_pct": f_cap,
            "field_owned_by": f_owned,
            "field_captained_by": f_capped,
            "field_tripled_by": f_tripled,
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

    return diff_rows, by_code_row, squad_codes


def _tool_whatif(
    *,
    all_rows: list[dict[str, Any]],
    includes_you: bool | None,
    sel_by_code: dict[int, dict],
    sel_denominator: str,
    sel_gw: int | None,
    sel_n: int | None,
) -> dict[str, Any]:
    """Tool 2: what one more or one fewer holder would do to the selected EO."""
    # -- tool 2: what-if exposure simulator ---------------------------------
    whatif = {
        "players": [{
            "code": r["code"], "name": r["name"], "pos": r["pos"],
            "team": r["team"], "team_code": r["team_code"], "price": r["price"],
            "status": r["status"], "your_mult": r["your_mult"],
            "in_squad": r["in_squad"],
            "field_eo_pct": (sel_by_code.get(r["code"], {}).get("eo", 0.0)
                             if sel_n else None),
            "field_eo_ex_tc_pct": (
                sel_by_code.get(r["code"], {}).get("eo_ex_tc", 0.0)
                if sel_n else None),
            "field_eo_tc_pp": (
                sel_by_code.get(r["code"], {}).get("eo_tc_pp", 0.0)
                if sel_n else None),
            "field_own_pct": (sel_by_code.get(r["code"], {}).get("own", 0.0)
                              if sel_n else None),
            "field_cap_pct": (sel_by_code.get(r["code"], {}).get("cap", 0.0)
                              if sel_n else None),
            "field_tripled_by": (
                sel_by_code.get(r["code"], {}).get("tripled_by", 0)
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
            "hypothetical squad. field_eo_pct is fixed while the selection "
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
            "managers and a different denominator. Refetch with `segments`.",
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
            f"the same {sel_n} squads, which is not a missing value. "
            f"field_eo_pct keeps every multiplier those managers applied, "
            f"chips included; field_eo_ex_tc_pct is the same number with the "
            f"triple-captain third unit taken out, and field_eo_tc_pp is the "
            f"size of that unit."
            if sel_n else
            "No field is selected, so every field_* value is null. Select at "
            "least one segment with a stored squad."
        ),
    }

    return whatif


def _tool_momentum(
    wh,
    *,
    season: str,
    by_code_row: dict[int, dict[str, Any]],
    gw: int | None,
    sel_by_gw: dict[int, dict],
    squad_codes: set,
    template: list[dict[str, Any]],
) -> dict[str, Any]:
    """Tool 3: the selected field's ownership trend across the crawled gameweeks."""
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
                      f"selection: GW{mom_gws[0]} to GW{mom_gws[-1]}.")
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
                    "eo_tc_pp": m["eo_tc_pp"] if m else 0.0,
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

    return momentum
