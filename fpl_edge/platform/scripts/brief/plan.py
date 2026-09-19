"""The three blocks that carry the solver's own numbers.

``_moves`` (the deterministic move rules), ``_solve_block`` (transfer_plan.json
rendered, staleness named) and ``_verdict`` (one pick per question by the
printed precedence). Split from ``tiles.py`` because these are the blocks whose
currency is the solver's objective, never summed with a consensus number."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from fpl_edge.platform.scripts.brief.schema import (
    _GAP_NOTE,
    PRECEDENCE,
    THRESHOLDS,
    TRANSFER_PLAN_NAME,
)
from fpl_edge.platform.scripts.brief.tiles import BriefCtx, _iso, _parse_ts, _ref
from fpl_edge.platform.scripts.common import POSITION_NAME, latest_as_of, q, source_dir


def _moves(
    ctx: BriefCtx,
    sq: dict[str, Any],
    squad15: list[dict[str, Any]],
    squad_codes: set[int],
    team_fixtures: list[dict[str, Any]],
    near: dict[str, Any],
    cons_next: dict[int, dict[str, Any]],
    proj_as_of: str | None,
    g_next: int | None,
) -> tuple[list[dict[str, Any]], int]:
    """The deterministic move rules: rule ids and numbers, capped and disclosed."""
    wh, season, now = ctx.wh, ctx.season, ctx.now
    sources_as_of = ctx.sources_as_of
    watch, empties, notes = ctx.watch, ctx.empties, ctx.notes

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
        moves_gap_reason = "squad unreadable; no out-leg can be priced"
    elif g_next is None:
        moves_gap_reason = "no future deadline known"
    elif not cons_next:
        moves_gap_reason = (f"no consensus projections for GW{g_next} in the "
                            f"warehouse; the rules cannot price a candidate")
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
            moves_gap_reason = ("no settled gameweek in fact_player_fixture; "
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

    return moves, moves_suppressed



def _solve_block(
    ctx: BriefCtx,
    pr: dict[str, Any],
    squad15: list[dict[str, Any]],
    squad_codes: set[int],
    tplan: dict[str, Any] | None,
    tplan_named: set[int],
    g_next: int | None,
    next_deadline: dt.datetime | None,
    last_deadline: dt.datetime | None,
) -> dict[str, Any]:
    """transfer_plan.json rendered: the solver's own numbers, staleness named."""
    wh, season, now = ctx.wh, ctx.season, ctx.now
    sources_as_of = ctx.sources_as_of
    alerts, watch = ctx.alerts, ctx.watch

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

        # A plan describes ONE squad: its `out` and `in` are diffed against the
        # fifteen held when the solve ran. Apply them to a different fifteen and
        # the result is a squad the optimiser never scored. This fired for real
        # on 2026-09-08: a plan solved against the public GW3 picks was rendered
        # beside a squad card reading live from the connected account, and its
        # starting XI named a player the card did not list.
        solved_against = [int(c) for c in (tplan.get("squad_before") or [])]
        held_now = sorted(int(c) for c in squad_codes) if squad_codes else []
        superseded = bool(solved_against and held_now
                          and set(solved_against) != set(held_now))
        if not solved_against and held_now:
            # An artefact written before squad_before existed. "Cannot check"
            # and "checked and matches" are different claims, so say which.
            watch.append({"check": "solver", "status": "gap",
                          "detail": "this plan does not record the squad it "
                                    "was solved against, so it cannot be "
                                    "checked against your fifteen; the next "
                                    "solver run records it",
                          "source_panel": "solve_plan",
                          "as_of": solve["generated_at"]})

        # Precedence: a passed deadline first. It is the stronger statement
        # and covers more ground, because prices moved and points were scored
        # as well as the squad changing. Superseded is for the case where the
        # calendar is fine and only the fifteen moved underneath the plan.
        stale_by_deadline = (gen is not None and last_deadline is not None
                             and gen < last_deadline)
        if superseded and not stale_by_deadline:
            gone = sorted(set(solved_against) - set(held_now))
            got = sorted(set(held_now) - set(solved_against))
            solve["state"] = "superseded"
            solve["reason"] = (
                f"the plan was solved against a different squad: "
                f"{len(gone)} player(s) it assumed you held are not in your "
                f"fifteen, and {len(got)} you hold were not in it. Its moves "
                f"were priced against that squad, so they do not apply here. "
                f"Re-run the solver."
            )
            alerts.append({
                "rule": "solve_superseded", "kind": "SOLVER", "priority": 0,
                "codes": [*gone, *got], "players": [],
                "numbers": {"age_hours": solve["age_hours"],
                            "players_differing": float(len(gone) + len(got))},
                "news": None, "status": None, "reason": solve["reason"],
                "source_panel": "solve_plan",
                "source_as_of": solve["generated_at"],
                "drill": {"tab": "planner"},
            })
            watch.append({"check": "solver", "status": "gap",
                          "detail": f"plan solved against a squad differing by "
                                    f"{len(gone) + len(got)} player(s)",
                          "source_panel": "solve_plan",
                          "as_of": solve["generated_at"]})
        elif stale_by_deadline:
            solve["state"] = "stale"
            solve["reason"] = (
                f"transfer plan generated {gen.date().isoformat()} for "
                f"GW{h_gws[0] if h_gws else '?'} to "
                f"GW{h_gws[-1] if h_gws else '?'}; a deadline has passed "
                f"since, so its moves were priced against a squad you no "
                f"longer have."
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
                                    f"GW{g_next if g_next else '?'}; "
                                    f"generated {gen.date().isoformat()}",
                          "source_panel": "solve_plan",
                          "as_of": solve["generated_at"]})
        else:
            fresh_h = float(THRESHOLDS["solve_fresh_window_h"])
            age_h = solve.get("age_hours")
            if age_h is not None and float(age_h) <= fresh_h:
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
                "bank_after_tenths": (int(chosen["bank_after_tenths"])
                                      if chosen.get("bank_after_tenths")
                                      is not None else None),
                # Which forecast the gain is priced in: consensus (what every
                # other surface shows) or the engine model, with the engine
                # fill share when the consensus left gaps.
                "forecast_source": tplan.get("forecast_source"),
                "forecast_engine_fill_share": tplan.get("forecast_engine_fill_share"),
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

    return solve


def _verdict(
    ctx: BriefCtx,
    squad15: list[dict[str, Any]],
    suggested_xi: dict[str, Any] | None,
    solve: dict[str, Any],
    moves: list[dict[str, Any]],
    proj_as_of: str | None,
    p_haul_generated: Any,
) -> tuple[dict[str, Any], str | None, Any]:
    """One pick per question by the printed precedence; dissent served beside it."""
    wh = ctx.wh
    sources_as_of, watch, notes = ctx.sources_as_of, ctx.watch, ctx.notes

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
            # Money, so the reader learns here rather than at the FPL site
            # that the plan cannot be executed.
            "bank_after_tenths": plan.get("bank_after_tenths"),
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
        t_rule = ("rule_moves_solver_stale"
                  if solve["state"] in ("stale", "superseded")
                  else "rule_moves_solver_missing")
        t_moves = [{"out": m["out"], "in": m["in"]} for m in moves]
        t_numbers = {"n_rule_moves": len(moves)}
        t_src, t_as_of = "dashboard_brief", proj_as_of
    else:
        t_rule, t_moves, t_numbers = "no_move_named", [], {}
        t_src, t_as_of = "dashboard_brief", None
    if plan is None and solve["state"] in ("stale", "superseded", "missing"):
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
        # A solver pick must be quoted in the SOLVER'S OWN currency: its
        # committed per-GW forecast. Decorating it with another source's
        # number once made the solver's best captain (its 6.7) wear a stale
        # simulation's 4.0 and look self-contradictory on its own card.
        if c_rule == "solver_plan_captain" and plan is not None:
            fc_path = Path(source_dir(wh)) / "forecast.parquet"
            h_gws = plan.get("horizon_gws") or []
            if fc_path.exists() and h_gws:
                try:
                    import pandas as pd
                    fdf = pd.read_parquet(fc_path)
                    first_gw = int(h_gws[0])
                    hit = fdf[(fdf["gw"] == first_gw)
                              & (fdf["code"] == int(cap_pick["code"]))]
                    if not hit.empty:
                        c_numbers["pick_solver_xpts"] = round(
                            float(hit.iloc[0]["xpts"]), 2)
                        c_numbers["solver_gw"] = first_gw
                except (OSError, KeyError, ValueError):
                    pass  # the forecast is decoration here; absence is quiet
        # Do the two voices agree about the man being captained? The gap is
        # served whenever both exist; the gate says when it is worth shouting.
        s_x, c_x = c_numbers.get("pick_solver_xpts"), c_numbers.get("pick_xpts")
        if s_x is not None and c_x is not None:
            gap_x = round(float(s_x) - float(c_x), 2)
            c_numbers["solver_vs_consensus"] = gap_x
            c_numbers["divergence_gate"] = float(
                THRESHOLDS["captain_divergence_xpts"])
        # What the armband change is worth, on the captain's OWN line. The
        # number existed in suggested_xi and the view had to reach across two
        # blocks to print it, so the one free action on the page -- no
        # transfer, no hit, a couple of expected points -- went unquoted.
        # Null when the pick is already the locked armband: there is no swap.
        if suggested_xi is not None:
            my_cap_ref = suggested_xi.get("your_captain")
            cons_cap = suggested_xi.get("captain")
            if my_cap_ref and my_cap_ref.get("code") != cap_pick["code"]:
                c_numbers["your_captain_code"] = int(my_cap_ref["code"])
                # The delta must belong to the pick this row names. suggested_xi's
                # captain_delta_xpts is the CONSENSUS captain's lead over the
                # owner's captain; when the row's pick is the solver's (a
                # different player, 2026-09-19: João Pedro against a Fernandes
                # delta) that number is about someone else. Recompute from the
                # same consensus table the pick_xpts above came from, and only
                # reuse the served delta when the pick is the consensus captain.
                my_row = sq_by_code_v.get(int(my_cap_ref["code"]))
                pick_x = c_numbers.get("pick_xpts")
                if (cons_cap and cons_cap.get("code") == cap_pick["code"]
                        and suggested_xi.get("captain_delta_xpts") is not None):
                    c_numbers["captain_delta_xpts"] = float(
                        suggested_xi["captain_delta_xpts"])
                elif my_row is not None and my_row.get("xpts") is not None and pick_x is not None:
                    c_numbers["captain_delta_xpts"] = round(
                        float(pick_x) - float(my_row["xpts"]), 2)
            if cons_cap and cons_cap.get("code") != cap_pick["code"]:
                cons_row = sq_by_code_v.get(int(cons_cap["code"]))
                c_numbers["consensus_captain_code"] = int(cons_cap["code"])
                c_numbers["consensus_captain_name"] = cons_cap.get("name")
                if cons_row is not None:
                    c_numbers["consensus_captain_xpts"] = cons_row.get("xpts")
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
                # Dated by the SIMULATION's data-birth, not the squad read:
                # this voice can be weeks older than everything beside it.
                "source_panel": "squad_overview",
                "source_as_of": p_haul_generated or sq_as_of_v,
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
    cap_gap = c_numbers.get("solver_vs_consensus")
    if cap_gap is not None:
        gate_x = float(THRESHOLDS["captain_divergence_xpts"])
        firing = abs(cap_gap) >= gate_x
        watch.append({
            "check": "captain_divergence",
            "status": "firing" if firing else "clear",
            "detail": (
                f"{cap_pick['name']}: solver "
                f"{c_numbers['pick_solver_xpts']} vs consensus "
                f"{c_numbers['pick_xpts']} xPts ({cap_gap:+.2f}); "
                + ("the armband rests on a forecast the providers do not "
                   "share" if firing else
                   f"inside the {gate_x} gate, the voices broadly agree")),
            "source_panel": "solve_plan", "as_of": plan_as_of,
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

    return verdict, ch_rule, chip_val
