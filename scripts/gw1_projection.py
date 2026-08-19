"""Produce per-player point distributions for the upcoming gameweek."""

from __future__ import annotations

import datetime as dt
import sys

import pandas as pd

from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
from fpl_edge.models.points.model import DecomposedPointsModel
from fpl_edge.models.points.shares import estimate_rates
from fpl_edge.models.team_goals import DixonColesModel
from fpl_edge.store import Warehouse

SEASON = "2026-27"
HISTORY = ["2022-23", "2023-24", "2024-25", "2025-26"]


def main(n_sims: int = 2000) -> None:
    with Warehouse.read_copy() as wh:
        latest = wh.snapshot_at(dt.datetime.now(dt.timezone.utc))
        deadline = latest.deadline(SEASON, 1)
        snap = wh.snapshot_at(deadline)
        print(f"snapshot at GW1 deadline {deadline:%Y-%m-%d %H:%MZ}")

        goals = DixonColesModel()
        goals.fit(snap, SEASON)
        print("  dixon-coles fitted")

        # Train only on seasons whose results are visible at the deadline, so
        # the minutes model cannot learn from matches it should not have seen.
        builder = TrainingSetBuilder(snapshot_at=wh.snapshot_at, catalog=snap)
        ts = builder.build(HISTORY)
        mins = GBMMinutesModel().fit(ts)
        print(f"  minutes model fitted on {len(ts.frame):,} rows "
              f"({len(ts.cold_frame):,} cold-start)")

        rates = estimate_rates(snap, HISTORY)
        print(f"  rates for {len(rates.frame)} players")

        model = DecomposedPointsModel(goal_model=goals, minutes_model=mins, rates=rates)
        sample = model.simulate(snap, SEASON, 1, n_sims=n_sims, seed=20260821)
        print(f"  simulated {sample.n_sims} draws for {len(sample.codes)} players")

        players = snap.selectable(SEASON).set_index("code")
        out = pd.DataFrame({
            "code": sample.codes,
            "xpts": sample.mean(),
            "p10": sample.quantile(0.10),
            "p90": sample.quantile(0.90),
            "p_haul": sample.p_at_least(10),
            "p_blank": (sample.points <= 2).mean(axis=1),
        }).set_index("code")
        out = out.join(players[["web_name", "position", "price_tenths", "selected_by_pct"]])
        out["price"] = out["price_tenths"] / 10
        out["value"] = out["xpts"] / out["price"]
        out.to_parquet("data/warehouse/gw1_projection.parquet")

        pos = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
        print("\nTOP 20 BY EXPECTED POINTS")
        top = out.nlargest(20, "xpts")
        print(top.assign(pos=top["position"].map(pos))[
            ["web_name", "pos", "price", "selected_by_pct", "xpts", "p10", "p90",
             "p_haul", "p_blank"]
        ].round(2).to_string(index=False))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 2000)
