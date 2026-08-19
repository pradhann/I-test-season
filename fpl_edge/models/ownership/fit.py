"""Fit the ownership model and write the committed parameters and measurements.

Run with ``uv run python -m fpl_edge.models.ownership.fit``. Reads only the
committed fixtures, so it is offline, deterministic and re-runnable; the two
JSON files it writes are what the shipped model and its ModelCard load.

The evaluation numbers written here are leave-one-season-out, and the
parameters written here are fitted on everything. Those are deliberately
different: you want the honest score from data the model never saw, and the
shipped model to use every season available.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.models.ownership import backtest, panel
from fpl_edge.models.ownership.drift import fit_coldstart, fit_inseason
from fpl_edge.models.ownership.model import MEASURED_PATH, PARAMS_PATH


def growth_weights() -> dict[str, float]:
    """Median ``N(gw)/N(gw+1)`` by gameweek across the real seasons."""
    fs = panel.load_field_size()
    med = fs.groupby("GW")["w"].median()
    out = {str(int(gw)): float(round(w, 6)) for gw, w in med.items()}
    out["default"] = float(round(med[med.index >= 20].median(), 6))
    return out


def main() -> None:
    P = panel.attach_field_size(panel.load_inseason_panel())
    A = panel.load_coldstart_pairs()

    ins_score, ins_by_season = backtest.loso_inseason(P)
    cold_score, cold_by_snapshot = backtest.loso_coldstart(A)
    cap_table, cap_summary = backtest.evaluate_captaincy(A)
    curve = backtest.coldstart_uncertainty_curve(A)

    ins_params = fit_inseason(P)
    cold_params = fit_coldstart(A)

    PARAMS_PATH.write_text(json.dumps({
        "inseason": {
            "coef": [float(c) for c in ins_params.coef],
            "intercept": float(ins_params.intercept),
            "scale": {"a": ins_params.scale.a, "b": ins_params.scale.b,
                      "interval_k": dict(ins_params.scale.interval_k)},
        },
        "coldstart": {
            "coef_near": [float(c) for c in cold_params.coef_near],
            "coef_far": [float(c) for c in cold_params.coef_far],
            "coef_near_nodrift": [float(c) for c in cold_params.coef_near_nodrift],
            "coef_far_nodrift": [float(c) for c in cold_params.coef_far_nodrift],
            "shrink_per_day": cold_params.shrink_per_day,
            "scale_a": cold_params.scale_a, "scale_b": cold_params.scale_b,
            "scale_day_exponent": cold_params.scale_day_exponent,
            "interval_k": dict(cold_params.interval_k),
        },
        "growth_w": growth_weights(),
    }, indent=1) + "\n")

    near = cold_by_snapshot[cold_by_snapshot["days_to_deadline"] <= 5.0]
    measured = {
        "trained_through": "2025-26 (five real seasons; leave-one-season-out)",
        "inseason": ins_score.as_dict(),
        "inseason_by_season": ins_by_season.round(4).to_dict("records"),
        "coldstart": cold_score.as_dict(),
        "coldstart_by_snapshot": cold_by_snapshot.round(4).to_dict("records"),
        "coldstart_near_deadline": {
            "n_snapshots": int(len(near)), "n_rows": int(near["n"].sum()),
            "model": float(np.average(near["model"], weights=near["n"])),
            "persistence": float(np.average(near["persistence"], weights=near["n"])),
            "momentum": float(np.average(near["momentum"], weights=near["n"])),
            "model_own_ge_1pct": float(np.average(near["model_own_ge_1pct"], weights=near["n"])),
            "persistence_own_ge_1pct": float(
                np.average(near["persistence_own_ge_1pct"], weights=near["n"])),
        },
        "captaincy_simulated": cap_summary,
        "captaincy_by_season": cap_table.round(4).to_dict("records"),
        "gw1_uncertainty": curve.round(3).to_dict("records"),
        "notes": (
            "Ownership MAE is measured against real realised ownership from "
            "vaastav/Fantasy-Premier-League, leave-one-season-out over 2021-22..2025-26.",
            "GW1 cold start is measured against real pre-deadline snapshots recovered "
            "from that dataset's git history at T-1, T-4, T-11, T-14 and T-20 days.",
            "Captaincy MAE is measured on a SIMULATED field: no public per-player "
            "captaincy-share series exists. It is a test of functional form, not of "
            "real manager behaviour.",
            "Top-10k ownership is a PRIOR, not a measurement. The overall league "
            "standings are empty and every picks endpoint returns 404 before the "
            "first deadline, verified against the live API on 2026-08-18.",
        ),
    }
    MEASURED_PATH.write_text(json.dumps(measured, indent=1) + "\n")
    print(f"wrote {PARAMS_PATH}")
    print(f"wrote {MEASURED_PATH}")
    print(f"in-season LOSO   MAE {ins_score.model:.4f}pp vs persistence "
          f"{ins_score.persistence:.4f}pp vs momentum {ins_score.momentum:.4f}pp")
    print(f"cold-start LOSO  MAE {cold_score.model:.4f}pp vs persistence "
          f"{cold_score.persistence:.4f}pp vs momentum {cold_score.momentum:.4f}pp")


if __name__ == "__main__":
    main()
