"""The recommended-squad section of the weekly report.

Renders the most recent persisted solve artefact rather than re-solving: the
MILP over the full universe takes minutes, and a report command must not. The
cost of that choice is staleness, so the section leads with when the plan was
computed and refuses to render one solved against a different gameweek.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from pathlib import Path

from fpl_edge.interfaces.report import register_section
from fpl_edge.store import Warehouse

PLAN_PATH = Path("data/warehouse/gw1_plan.json")

_POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def render_squad(wh: Warehouse, season: str, gw: int, as_of: dt.datetime) -> str | None:
    if not PLAN_PATH.exists():
        return None  # declared as a gap by the report's missing-section list
    plan = json.loads(PLAN_PATH.read_text())
    if plan["season"] != season or plan["horizon_gws"][0] != gw:
        return (
            f"## Recommended squad\n\nA persisted plan exists but targets "
            f"{plan['season']} GW{plan['horizon_gws'][0]}, not GW{gw}. "
            f"Re-run `uv run python scripts/gw1_squad.py` for this deadline."
        )

    snap = wh.snapshot_at(as_of)
    players = snap.players(season)
    name = dict(zip(players["code"], players["web_name"]))
    price = dict(zip(players["code"], players["price_tenths"]))
    own = dict(zip(players["code"], players["selected_by_pct"]))
    pos = dict(zip(players["code"], players["position"]))

    d = plan["gw1"]
    generated = dt.datetime.fromisoformat(plan["generated_at"])
    age_h = (dt.datetime.now(dt.timezone.utc) - generated).total_seconds() / 3600

    lines = [
        "## Recommended squad",
        "",
        f"Solved {generated:%Y-%m-%d %H:%M}Z ({age_h:.1f}h ago) over GWs "
        f"{plan['horizon_gws']}, objective mode **{plan['objective_mode']}**, "
        f"horizon objective {plan['objective']:.1f} pts from {plan['n_sims']} "
        f"simulation draws per GW.",
    ]
    if plan["objective_mode"] == "expected_points":
        lines += [
            "",
            "**Objective note:** this squad maximises expected points. The "
            "rank-utility divergence experiment (docs/models/simulator.md §9, "
            "measured 2026-08-19 on live models) found that at GW1, from a "
            "leading position, the xPts-optimal squad IS the rank-optimal "
            "squad: no swap in either direction improved P(top 10k), and the "
            "xPts-max captain is also the rank-max captain. Rank utility "
            "re-enters when live rank falls behind the pace, on chip weeks, "
            "and in mini-league mode.",
        ]
    for note in plan.get("notes", []):
        lines += ["", f"**Solver note:** {note}"]
    if age_h > 24:
        lines += ["", f"**Staleness warning:** {age_h:.0f}h old. Prices, injuries "
                      "and odds have moved since; re-solve before acting."]

    def row(c: int) -> str:
        cap = " **(C)**" if c == d["captain"] else (" (V)" if c == d["vice_captain"] else "")
        return (f"| {_POS.get(pos.get(c), '?')} | {name.get(c, c)}{cap} "
                f"| £{price.get(c, 0) / 10:.1f} | {own.get(c, float('nan')):.1f}% |")

    lines += ["", "| Pos | Player | Price | Owned |", "| --- | --- | --- | --- |"]
    lines += [row(c) for c in d["starting_xi"]]
    lines += ["", "Bench (in order):", "", "| Pos | Player | Price | Owned |",
              "| --- | --- | --- | --- |"]
    lines += [row(c) for c in d["bench"]]
    spend = sum(price.get(c, 0) for c in d["squad"])
    lines += ["", f"Spend £{spend / 10:.1f}m, bank £{(1000 - spend) / 10:.1f}m. "
                  f"Chip: {d['chip'] or 'none'}."]
    if d.get("chip") and len(plan["horizon_gws"]) < 38:
        lines += [
            "",
            f"**Chip caveat — do not follow this blindly.** The solver plays "
            f"{d['chip']} inside a {len(plan['horizon_gws'])}-gameweek window. "
            "A truncated horizon is structurally biased toward spending chips "
            "early: it cannot see the blank and double gameweeks later in the "
            "season where Bench Boost and Triple Captain historically pay "
            "double or more. Until season-long chip optimisation lands, treat "
            "an early-window chip call as 'the chip would not be wasted', not "
            "'this is the best week for it'. Holding it is the sound default.",
        ]
    lines += _render_diff(wh, season, gw, as_of, plan, name, pos, price)
    return "\n".join(lines)


def _render_diff(
    wh: Warehouse, season: str, gw: int, as_of: dt.datetime, plan: dict,
    name: dict, pos: dict, price: dict,
) -> list[str]:
    """Your actual fifteen against the recommended fifteen.

    Before the first deadline transfers are unlimited and free, so the whole
    decision is this set difference -- there is no hit to weigh and no free
    transfer to spend. Printing the recommended squad beside a squad the
    manager already owns, without saying which players actually differ, leaves
    the only real GW1 question as an exercise for the reader.

    A section must never fail the report, so an unreadable squad (no auth,
    dead endpoint) degrades to a note rather than an exception.
    """
    from fpl_edge.myteam.report import current_state

    try:
        state = current_state(wh, season, as_of, gw=gw)
    except Exception as exc:  # noqa: BLE001 - never let a section kill the report
        return ["", f"_Your own squad could not be read ({type(exc).__name__}), "
                    "so no comparison is shown._"]
    if state.picks is None:
        return ["", "_Your own squad is not known yet, so no comparison is "
                    "shown. `fpl myteam auth` or `fpl myteam set` enables it._"]

    mine = {int(pick.code) for pick in state.picks}
    theirs = {int(c) for c in plan["gw1"]["squad"]}
    # Sort both sides by position: an FPL transfer is always like-for-like, so
    # position-ordered columns pair each row into a swap the game would allow.
    # Index-ordered columns imply swaps that are not legal moves.
    def by_pos(codes: set[int]) -> list[int]:
        return sorted(codes, key=lambda c: (pos.get(c, 9), name.get(c, str(c))))

    out_, in_ = by_pos(mine - theirs), by_pos(theirs - mine)
    lines = ["", "### Versus your actual squad", ""]
    if not out_ and not in_:
        lines.append("Identical to what you already own. Nothing to change.")
    else:
        cost = sum(price.get(c, 0) for c in in_) - sum(price.get(c, 0) for c in out_)
        lines += [
            f"{len(in_)} change(s). Transfers before the first deadline are "
            "unlimited and free, so this costs no hit.",
            "",
            "| Out | In |", "| --- | --- |",
        ]
        for o, i in itertools.zip_longest(out_, in_):
            left = f"{_POS.get(pos.get(o), '?')} {name.get(o, o)}" if o else "—"
            right = f"{_POS.get(pos.get(i), '?')} {name.get(i, i)}" if i else "—"
            lines.append(f"| {left} | {right} |")
        lines += ["", f"Net price change £{cost / 10:+.1f}m against your "
                      f"£{state.bank.tenths / 10:.1f}m bank."]

    mine_cap = next(
        (int(pick.code) for pick in state.picks if pick.is_captain), None
    )
    rec_cap = int(plan["gw1"]["captain"])
    if mine_cap is not None:
        lines += ["", (
            f"Captain: you have **{name.get(mine_cap, mine_cap)}**, the plan "
            f"says **{name.get(rec_cap, rec_cap)}**."
            if mine_cap != rec_cap
            else f"Captain: you and the plan agree on "
                 f"**{name.get(rec_cap, rec_cap)}**."
        )]
    return lines


def register() -> None:
    register_section("squad", render_squad, priority=35, provides="squad")


register()
