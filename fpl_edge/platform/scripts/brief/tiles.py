"""The alert, tile, watch and empty accumulators, and the blocks that fill them.

``BriefCtx`` is the one clock set and the six accumulators the blocks share.
Everything here either appends to those lists or returns a payload block that
carries no solver number: the squad checks, the pitch, the price flow, the
ownership and consensus gates, the fixture turns, the squad minutes, the
header stats and the season standing. ``_iso``, ``_parse_ts``, ``_ref``,
``_FORMATIONS`` and ``best_legal_xi`` sit here rather than in ``schema.py``
because they are helpers, not schema (ARCHITECTURE_REVIEW.md Section 2
check 5, which assigns the three unnamed ones to this module by name).

``_calendar``, ``_source_panels``, ``_squad_minutes`` and
``_projection_provenance`` are named in neither A1 list. They are block
builders with no plan content, so they sit with the other block builders."""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpl_edge.platform.scripts.brief.schema import THRESHOLDS, TRANSFER_PLAN_NAME
from fpl_edge.platform.scripts.common import POSITION_NAME, UTC, next_gw, q, source_dir
from fpl_edge.platform.scripts.fixtures import fixture_board
from fpl_edge.platform.scripts.ownership import ownership_eo
from fpl_edge.platform.scripts.prices import price_radar
from fpl_edge.platform.scripts.squad import _SOURCE_LABEL, squad_overview
from fpl_edge.platform.users import PLANS_DIR, UserContext


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


#: Formation law: 1 GK, 3-5 DEF, 2-5 MID, 1-3 FWD, eleven in total.
_FORMATIONS = tuple(
    (d, m, f) for d in (3, 4, 5) for m in (2, 3, 4, 5) for f in (1, 2, 3)
    if 1 + d + m + f == 11)


def best_legal_xi(squad15: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The formation-legal XI maximising consensus xPts over the 15.

    Exhaustive over the twelve legal formations (each is a top-k slice per
    position, so the search is trivially small). A player with no xPts
    counts as 0 and is only picked when the formation forces it. Returns
    None when fewer than eleven players carry a position.
    """
    by_pos: dict[str, list[dict[str, Any]]] = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
    for p in squad15:
        if p.get("pos") in by_pos:
            by_pos[p["pos"]].append(p)
    xv = lambda p: float(p["xpts"]) if p.get("xpts") is not None else 0.0  # noqa: E731
    for pos in by_pos:
        by_pos[pos].sort(key=xv, reverse=True)
    best: tuple[float, list[dict[str, Any]], str] | None = None
    for d, m, f in _FORMATIONS:
        if (len(by_pos["GKP"]) < 1 or len(by_pos["DEF"]) < d
                or len(by_pos["MID"]) < m or len(by_pos["FWD"]) < f):
            continue
        xi = (by_pos["GKP"][:1] + by_pos["DEF"][:d]
              + by_pos["MID"][:m] + by_pos["FWD"][:f])
        total = sum(xv(p) for p in xi)
        if best is None or total > best[0] + 1e-9:
            best = (total, xi, f"{d}-{m}-{f}")
    if best is None:
        return None
    total, xi, formation = best
    xi_codes = {p["code"] for p in xi}
    # FPL's bench convention: the goalkeeper is ALWAYS bench slot 1 (a
    # sub keeper only ever replaces the keeper), then the three outfielders
    # in consensus-xPts order, nulls last. The dashboard pitch draws this
    # list as served, so the two surfaces stay one order.
    rest = [p for p in squad15 if p["code"] not in xi_codes]
    bench = ([p for p in rest if p.get("pos") == "GKP"]
             + sorted((p for p in rest if p.get("pos") != "GKP"),
                      key=xv, reverse=True))
    return {"xi": xi, "bench": bench, "formation": formation,
            "xi_xpts": round(total, 2)}


@dataclass
class BriefCtx:
    """One clock set, one warehouse handle, and the six accumulators.

    The blocks below append to these lists in the order the single function
    did; nothing here is read back except by the assembler. ``call`` and
    ``gap_alert`` were closures inside the source-panel block and are methods
    for the same reason: three later blocks call ``gap_alert``.
    """

    wh: Any
    season: str
    now: dt.datetime
    eid: int
    #: Whose brief this is. ``eid`` is this user's entry id, kept as its own
    #: field because every block reads it and none of them needs the rest.
    user: UserContext
    sources_as_of: dict[str, str | None]
    alerts: list[dict[str, Any]]
    tiles: list[tuple[float, dict[str, Any]]]
    watch: list[dict[str, Any]]
    empties: list[dict[str, Any]]
    notes: list[str]

    def gap_alert(self, panel: str, reason: str) -> None:
        self.alerts.append({
            "rule": "source_gap", "kind": "GAP", "priority": 0,
            "codes": [], "players": [], "numbers": {},
            "news": None, "status": None, "reason": reason,
            "source_panel": panel, "source_as_of": None,
            "drill": {"tab": "pipelines"},
        })


def _calendar(ctx: BriefCtx) -> tuple[
    int | None, Any, dt.datetime | None, dt.datetime | None] | None:
    """The gameweek and the deadline column. None when the season has no events."""
    wh, season, now = ctx.wh, ctx.season, ctx.now

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
        # The caller owns the empty payload: a builder returns values,
        # never the panel's own result envelope.
        return None
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

    return g_next, deadlines, next_deadline, last_deadline


def _source_panels(ctx: BriefCtx) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Call the three source panels; a raise degrades its checks, not the page."""
    wh, season, eid = ctx.wh, ctx.season, ctx.eid
    sources_as_of = ctx.sources_as_of

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

    sq = call("squad_overview", squad_overview, ctx=ctx.user)
    pr = call("price_radar", price_radar, limit=200)
    own = call("ownership_eo", ownership_eo)
    return sq, pr, own


def _squad_checks(ctx: BriefCtx, sq: dict[str, Any]) -> tuple[
    float | None, list[dict[str, Any]], list[dict[str, Any]],
    dict[str, Any] | None]:
    """Availability, bench order and captaincy, from squad_overview's own numbers."""
    alerts, watch = ctx.alerts, ctx.watch
    gap_alert = ctx.gap_alert

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
                       f",  applied in the suggested XI"
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
                "detail": "no projection artefact cached; p_haul and xPts "
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
            "reason": ("no projection artefact cached; the bench and "
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

    return xi_median, starters, squad15, suggested_xi


def _best_xi(
    ctx: BriefCtx,
    sq: dict[str, Any],
    starters: list[dict[str, Any]],
    squad15: list[dict[str, Any]],
    g_next: int | None,
    deadlines: Any,
) -> tuple[
    dict[str, Any] | None, dict[str, Any] | None, set[int], set[int]]:
    """The one lineup drawn on the pitch, and where the 15 came from."""
    now = ctx.now

    # ---- the best XI (ONE lineup for the pitch) + where the 15 came from --
    best_xi: dict[str, Any] | None = None
    squad_source: dict[str, Any] | None = None
    if not sq.get("empty"):
        sq_as_of_b = _iso(sq.get("as_of"))
        label = str(sq.get("provenance_source") or "unknown")
        live = label == _SOURCE_LABEL["PRIVATE_API"]
        last_gw = None
        for _, r in deadlines.iterrows():
            d = _parse_ts(r["deadline_utc"])
            if d is not None and d <= now:
                last_gw = int(r["gw"])
        squad_source = {
            "label": label,
            "live": live,
            # public picks are the LAST closed gameweek's team; a live or
            # manual read is the one being built for the next deadline
            "picks_gw": (last_gw if label == _SOURCE_LABEL["PUBLIC_PICKS"]
                         else g_next),
            "as_of": sq_as_of_b,
            "fix": None if live else "uv run fpl myteam auth",
        }
        found = best_legal_xi(squad15)
        if found is None:
            best_xi = {
                "xi_codes": [], "bench_codes": [], "formation": None,
                "captain": None, "captain_candidates": [],
                "captain_lead_xpts": None, "close_call": False,
                "differs": [], "n_differs": 0, "xi_xpts": None,
                "reason": "fewer than eleven players carry a position; "
                          "no legal XI can be formed from this squad read",
                "source_panel": "squad_overview", "source_as_of": sq_as_of_b,
            }
        else:
            xi = found["xi"]
            no_x = all(p.get("xpts") is None for p in squad15)
            ranked = sorted((p for p in xi if p.get("xpts") is not None),
                            key=lambda p: float(p["xpts"]), reverse=True)
            cands = [{"player": _ref(p), "xpts": p["xpts"],
                      "p_haul": p.get("p_haul")} for p in ranked[:3]]
            lead = (round(float(ranked[0]["xpts"]) - float(ranked[1]["xpts"]), 2)
                    if len(ranked) >= 2 else None)
            close_gate = float(THRESHOLDS["captain_close_call_xpts"])
            xi_set = {p["code"] for p in xi}
            locked = {p["code"] for p in starters}
            differs = ([p for p in starters if p["code"] not in xi_set]
                       + [p for p in xi if p["code"] not in locked])
            best_xi = {
                "xi_codes": [p["code"] for p in xi],
                "bench_codes": [p["code"] for p in found["bench"]],
                "formation": found["formation"],
                "captain": _ref(ranked[0]) if ranked else None,
                "captain_candidates": cands,
                "captain_lead_xpts": lead,
                "close_call": bool(lead is not None and lead < close_gate),
                "differs": [_ref(p) for p in differs],
                "n_differs": len([p for p in starters if p["code"] not in xi_set]),
                "xi_xpts": None if no_x else found["xi_xpts"],
                "reason": ("no consensus projection cached; the XI below is "
                           "the formation-legal fallback, not a ranking"
                           if no_x else None),
                "source_panel": "squad_overview", "source_as_of": sq_as_of_b,
            }

    squad_codes = {p["code"] for p in squad15}
    squad_team_codes = {p.get("team_code") for p in squad15
                       if p.get("team_code") is not None}

    return best_xi, squad_source, squad_codes, squad_team_codes


def _price_flow(
    ctx: BriefCtx, pr: dict[str, Any], squad_codes: set[int]
) -> tuple[dict[str, Any] | None, set[int]]:
    """Owned falls as alerts, named-target rises as tiles; returns the plan read."""
    wh, season = ctx.wh, ctx.season
    alerts, tiles, watch, notes = ctx.alerts, ctx.tiles, ctx.watch, ctx.notes
    gap_alert = ctx.gap_alert

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
    tplan_path = ctx.user.artefact(
        PLANS_DIR, TRANSFER_PLAN_NAME,
        legacy=Path(source_dir(wh)) / TRANSFER_PLAN_NAME)
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

    return tplan, tplan_named


def _ownership_gates(
    ctx: BriefCtx,
    own: dict[str, Any],
    sq: dict[str, Any],
    squad15: list[dict[str, Any]],
    squad_codes: set[int],
    xi_median: float | None,
) -> None:
    """Template gaps and differentials, gated; appends tiles and watch rows only."""
    sources_as_of = ctx.sources_as_of
    tiles, watch = ctx.tiles, ctx.watch
    gap_alert = ctx.gap_alert

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
                            f"{xi_median} + {d_margin}; two gates, two "
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
                          "missing); the xPts gate cannot be evaluated",
                "source_panel": "ownership_eo", "as_of": own_as_of,
            })



def _consensus_standouts(
    ctx: BriefCtx,
    sq: dict[str, Any],
    starters: list[dict[str, Any]],
    squad_codes: set[int],
    g_next: int | None,
) -> None:
    """Projection-view standouts against the XI median; tiles and watch rows only."""
    wh, season, now = ctx.wh, ctx.season, ctx.now
    sources_as_of = ctx.sources_as_of
    tiles, watch = ctx.tiles, ctx.watch

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
                          f"warehouse; ingest projections",
                "source_panel": "projection_table", "as_of": None,
            })



def _fixture_turns(
    ctx: BriefCtx,
    own: dict[str, Any],
    squad_team_codes: set[int],
    g_next: int | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """fixture_board's two windows, its own fields only; also the pitch chips."""
    wh, season = ctx.wh, ctx.season
    sources_as_of = ctx.sources_as_of
    tiles, watch = ctx.tiles, ctx.watch

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
                                    f" → {r2} (GW{far_gws[0]}–{far_gws[-1]}); "
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

    return team_fixtures, fixtures_scale, near


def _squad_minutes(
    ctx: BriefCtx, squad15: list[dict[str, Any]], g_next: int | None
) -> tuple[
    list[dict[str, Any]], dict[int, dict[str, Any]], str | None]:
    """xmins and p_appear per squad player from the provider consensus."""
    wh, season, now = ctx.wh, ctx.season, ctx.now
    sources_as_of, notes = ctx.sources_as_of, ctx.notes

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

    return squad_projection, cons_next, proj_as_of


def _creator_shift(ctx: BriefCtx) -> None:
    """The creator-shift kind, declared empty with its reason."""
    watch, empties = ctx.watch, ctx.empties

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
                  "detail": "no served change signal; see empty_kinds",
                  "source_panel": "creator_board", "as_of": None})


def _projection_provenance(sq: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    """The xPts and haul-odds provenance the squad panel already threaded."""

    # ---- projection provenance, threaded from the squad panel ------------
    # xPts = provider consensus (the Projections tab's numbers); p_haul = the
    # engine simulation with its own, possibly much older, data-birth
    # instant. Served side by side, two clocks, never conflated.
    xpts_source = None if sq.get("empty") else sq.get("xpts_source")
    xpts_as_of_v = None if sq.get("empty") else _iso(sq.get("xpts_as_of"))
    p_haul_source = None if sq.get("empty") else sq.get("p_haul_source")
    p_haul_generated = (None if sq.get("empty")
                        else _iso(sq.get("p_haul_generated")))

    return xpts_source, xpts_as_of_v, p_haul_source, p_haul_generated


def _header_stats(
    sq: dict[str, Any],
    solve: dict[str, Any],
    tplan: dict[str, Any] | None,
    ch_rule: str | None,
    chip_val: Any,
) -> dict[str, Any]:
    """The free-transfer count and the chip verdict, each with its solve state."""

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

    return header


def _standing(ctx: BriefCtx) -> dict[str, Any]:
    """Season points and rank against the field, per settled gameweek."""
    wh, season, now, eid = ctx.wh, ctx.season, ctx.now, ctx.eid
    sources_as_of = ctx.sources_as_of

    # ---- season standing: my gameweeks against FPL's own field average ----
    # The objective is P(top-1k) and the dashboard could not say where the
    # season stood. GW3 scored 23 against a field average of 51 and the rank
    # fell from 141,593 to 769,533 with nothing on the page saying so.
    standing: dict[str, Any] | None = None
    try:
        mine = q(
            wh,
            "SELECT gw, points, points_on_bench, event_transfers_cost, "
            "       overall_rank FROM ("
            "  SELECT *, row_number() OVER ("
            "    PARTITION BY entry_id, season, gw ORDER BY as_of DESC) rn "
            "  FROM fact_manager_gw WHERE entry_id = ? AND season = ? "
            "    AND as_of <= ?"
            ") WHERE rn = 1 ORDER BY gw",
            (eid, season, now),
        )
        field = q(
            wh,
            "SELECT gw, avg_entry_score FROM ("
            "  SELECT *, row_number() OVER ("
            "    PARTITION BY season, gw ORDER BY as_of DESC) rn "
            "  FROM dim_event WHERE season = ? AND as_of <= ?"
            ") WHERE rn = 1",
            (season, now),
        )
    except Exception:  # noqa: BLE001 - a missing table is a gap, not a crash
        mine = field = None
    if mine is None or mine.empty:
        standing = {
            "entry_id": eid, "gws": [], "reason":
            "no crawled gameweek for this entry yet; the manager crawl fills "
            "this in once it has read the entry's history",
        }
    else:
        by_gw = {}
        if field is not None and not field.empty:
            for r in field.to_dict("records"):
                v = r.get("avg_entry_score")
                if r.get("gw") is not None and v is not None and v == v:
                    by_gw[int(r["gw"])] = float(v)
        rows: list[dict[str, Any]] = []
        for r in mine.to_dict("records"):
            g = int(r["gw"])
            pts = None if r.get("points") is None else int(r["points"])
            fld = by_gw.get(g)
            rows.append({
                "gw": g,
                "points": pts,
                "field": fld,
                "delta": None if pts is None or fld is None else round(pts - fld, 1),
                "bench_points": (None if r.get("points_on_bench") is None
                                 else int(r["points_on_bench"])),
                "hit_cost": (None if r.get("event_transfers_cost") is None
                             else int(r["event_transfers_cost"])),
                "overall_rank": (None if r.get("overall_rank") is None
                                 else int(r["overall_rank"])),
            })
        ranks = [x["overall_rank"] for x in rows if x["overall_rank"] is not None]
        deltas = [x["delta"] for x in rows if x["delta"] is not None]
        pts_all = [x["points"] for x in rows if x["points"] is not None]
        standing = {
            "entry_id": eid,
            "overall_rank": ranks[-1] if ranks else None,
            # Negative is an improvement: FPL ranks count upward from the top.
            "rank_move": (ranks[-1] - ranks[-2]) if len(ranks) > 1 else None,
            "total_points": sum(pts_all) if pts_all else None,
            "vs_field_total": round(sum(deltas), 1) if deltas else None,
            "as_of": sources_as_of.get("squad"),
            "reason": (None if deltas else
                       "no settled gameweek carries FPL's average entry score "
                       "yet, so there is nothing to compare against"),
            "gws": rows,
        }

    return standing
