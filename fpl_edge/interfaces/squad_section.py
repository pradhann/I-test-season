"""The recommended-squad section of the weekly report.

Renders the most recent persisted solve artefact rather than re-solving: the
MILP over the full universe takes minutes, and a report command must not. The
cost of that choice is staleness, so the section leads with when the plan was
computed and refuses to render one solved against a different gameweek.
"""

from __future__ import annotations

import datetime as dt
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
            "**Mode caveat:** this squad maximises expected points, not "
            "P(top-10k). The rank-utility objective is not yet wired into the "
            "solver (it raises rather than silently substituting means), so "
            "treat this as the xPts anchor the rank view will be measured "
            "against, not the final answer.",
        ]
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
    return "\n".join(lines)


def register() -> None:
    register_section("squad", render_squad, priority=35, provides="squad")


register()
