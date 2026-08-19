"""Solve the GW1 squad with the MILP over the decomposed projection.

GW1 is pure squad selection: unlimited transfers before the first deadline,
so there is no transfer-cost dimension yet. The horizon still matters --
a squad picked on GW1 form alone walks into GW2-6 fixtures blind.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pandas as pd

from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
from fpl_edge.models.points.model import DecomposedPointsModel
from fpl_edge.models.points.shares import estimate_rates
from fpl_edge.models.team_goals import DixonColesModel
from fpl_edge.opt import (
    ObjectiveMode,
    OptimizerConfig,
    StaticPriceForecast,
    build_problem,
    solve_horizon,
)
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId

SEASON = "2026-27"
HISTORY = ["2022-23", "2023-24", "2024-25", "2025-26"]
HORIZON = [GwId(g) for g in (1, 2, 3, 4, 5)]


class SimulatedPointsForecast:
    """Adapts the correlated points simulator to the optimizer's frame contract."""

    def __init__(self, model: DecomposedPointsModel, n_sims: int, seed: int) -> None:
        self._model = model
        self._n_sims = n_sims
        self._seed = seed

    def forecast(self, snapshot, season, gws) -> pd.DataFrame:
        frames = []
        for gw in gws:
            sample = self._model.simulate(
                snapshot, season, gw, n_sims=self._n_sims, seed=self._seed + int(gw)
            )
            played = (sample.minutes > 0).mean(axis=1) if sample.minutes is not None else 1.0
            frames.append(pd.DataFrame({
                "code": sample.codes,
                "gw": int(gw),
                "xpts": sample.mean(),
                "p_play": played,
            }))
        return pd.concat(frames, ignore_index=True)


def main(n_sims: int = 1000) -> None:
    with Warehouse.read_copy() as wh:
        now = wh.snapshot_at(dt.datetime.now(dt.timezone.utc))
        deadline = now.deadline(SEASON, 1)
        snap = wh.snapshot_at(deadline)

        goals = DixonColesModel(); goals.fit(snap, SEASON)
        ts = TrainingSetBuilder(snapshot_at=wh.snapshot_at, catalog=snap).build(HISTORY)
        mins = GBMMinutesModel().fit(ts)
        rates = estimate_rates(snap, HISTORY)
        model = DecomposedPointsModel(goal_model=goals, minutes_model=mins, rates=rates)

        problem = build_problem(
            snap, SEASON, HORIZON,
            price_forecast=StaticPriceForecast(),
            points_forecast=SimulatedPointsForecast(model, n_sims, seed=20260821),
            state=None,
        )
        # Prune to the strongest 45 per position (by horizon xPts, plus all
        # held players -- prune() keeps holdings). The full 509-player universe
        # hits the 300s time limit and returns a ~2% incumbent, which is ~5.6
        # points of slack at this objective -- and that slack pools in exactly
        # the XI/bench assignments a reader checks first. A 45-per-position
        # pool solves to the 1e-4 gap in well under the limit, and no plausible
        # GW1 squad draws from outside it.
        config = OptimizerConfig(
            mode=ObjectiveMode.EXPECTED_POINTS,
            max_candidates_per_position=45,
        )
        plan, stats = solve_horizon(problem, config, return_stats=True)
        print(f"status={plan.status} gap={plan.mip_gap}")

        players = snap.selectable(SEASON)
        name = dict(zip(players["code"], players["web_name"]))
        price = dict(zip(players["code"], players["price_tenths"]))
        own = dict(zip(players["code"], players["selected_by_pct"]))
        pos_name = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
        pos = dict(zip(players["code"], players["position"]))

        gw1 = plan.decisions[0]
        print(f"solve: {stats}")
        print(f"\nGW1 SQUAD (5-GW horizon, expected-points mode)")
        print(f"objective over horizon: {plan.objective:.1f}\n")
        for section, codes in (("XI", gw1.starting_xi), ("BENCH", gw1.bench)):
            print(section)
            for c in codes:
                cap = " (C)" if c == gw1.captain else (" (V)" if c == gw1.vice_captain else "")
                print(f"  {pos_name[pos[c]]:>3} {name[c]:<20} £{price[c]/10:4.1f} "
                      f"{own[c]:5.1f}% owned{cap}")
        spend = sum(price[c] for c in gw1.squad)
        print(f"\nspend £{spend/10:.1f}m, bank £{(1000-spend)/10:.1f}m")

        # Persist the plan so the weekly report can render it with an honest
        # timestamp instead of re-solving (the solve takes minutes).
        import json

        artefact = {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "snapshot_as_of": deadline.isoformat(),
            "season": SEASON,
            "horizon_gws": [int(g) for g in HORIZON],
            "objective_mode": "expected_points",
            "objective": float(plan.objective),
            "n_sims": n_sims,
            "solver": str(stats),
            "gw1": {
                "squad": [int(c) for c in gw1.squad],
                "starting_xi": [int(c) for c in gw1.starting_xi],
                "bench": [int(c) for c in gw1.bench],
                "captain": int(gw1.captain),
                "vice_captain": int(gw1.vice_captain),
                "chip": gw1.chip,
                "bank_after": int(gw1.bank_after.tenths),
            },
        }
        out = pathlib.Path("data/warehouse/gw1_plan.json")
        out.write_text(json.dumps(artefact, indent=1))
        print(f"plan persisted to {out}")


if __name__ == "__main__":
    main()
