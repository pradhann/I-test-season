"""A pinned end-to-end run, so silent changes to the correlation machinery are loud.

The simulator's headline outputs are the product of a dozen interacting choices
-- the sampler, the stratification, the skill tilt, the tail blend. Any of them
can be changed in a way that leaves every other test passing and quietly moves
P(top 10k) by a third. This test runs a small, fully deterministic simulation and
compares its outputs to a stored fixture.

If it fails, the right response is usually to look at what changed and then, if
the change is intended, regenerate the fixture with::

    uv run python tests/unit/test_sim_regression.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import RankUtilityConfig
from fpl_edge.sim.engine import SeasonSimulator, SquadPlan, greedy_squad
from fpl_edge.sim.field import FieldConfig
from fpl_edge.sim.synthetic import toy_world
from fpl_edge.sim.utility import rank_utility_of

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sim" / "toy_season.json"
GWS = tuple(range(1, 11))
FIELD_SIZE = 1_000_000
CONFIG = RankUtilityConfig(target_rank=10_000, stretch_rank=1_000,
                           risk_lambda=0.35, field_size=FIELD_SIZE)


def run() -> dict:
    u, model, eo, cap, xp = toy_world(seed=3)

    class Own:
        card = None

        def forecast(self, snapshot, season, gw):
            return pd.DataFrame({"code": u.codes, "gw": int(gw), "eo_overall": eo,
                                 "captaincy_share": cap, "eo_top10k": eo})

    sim = SeasonSimulator(u, model, Own(), None, "toy", GWS, n_sims=2_000, seed=7,
                          field_config=FieldConfig(n_rivals=2_000, field_size=FIELD_SIZE))
    sim.prepare()

    template = greedy_squad(u, xp + 4.0 * eo, xp)
    contrarian = greedy_squad(u, xp - 4.0 * eo, xp)
    out: dict[str, float] = {
        "field_mean_season": float(sim.rival_totals.mean()),
        "field_sd_across_managers": float(sim.rival_totals.mean(axis=1).std()),
        "max_abs_eo_error": sim.realised_ownership_error(GWS[0])["max_abs_eo_error"],
    }
    for name, sq in (("template", template), ("contrarian", contrarian)):
        d = sim.evaluate(SquadPlan(sq, label=name))
        ru = rank_utility_of(d, CONFIG)
        out[f"{name}_mean_points"] = d.expected_points()
        out[f"{name}_corr_with_field"] = d.correlation_with_field()
        out[f"{name}_p_top10k"] = d.p_top(10_000)
        out[f"{name}_median_rank"] = d.median_rank()
        out[f"{name}_utility"] = ru.utility
    return out


def test_headline_outputs_have_not_drifted():
    if not FIXTURE.exists():  # pragma: no cover
        pytest.skip(f"regenerate the fixture: python {__file__}")
    expected = json.loads(FIXTURE.read_text())["values"]
    got = run()
    assert set(got) == set(expected)
    for key, want in expected.items():
        assert got[key] == pytest.approx(want, rel=1e-6, abs=1e-9), key


def test_the_pinned_run_still_shows_the_structural_relationships():
    """Guards the fixture itself: a stale fixture must not enshrine a broken model."""
    v = json.loads(FIXTURE.read_text())["values"]
    assert v["template_corr_with_field"] > v["contrarian_corr_with_field"] > 0
    assert v["max_abs_eo_error"] < 0.05
    assert 0.0 <= v["template_p_top10k"] <= 1.0


if __name__ == "__main__":  # pragma: no cover
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(
        {
            "note": "Deterministic toy-world run pinned by tests/unit/test_sim_regression.py. "
                    "Regenerate with: uv run python tests/unit/test_sim_regression.py",
            "n_sims": 2000, "n_rivals": 2000, "n_gws": len(GWS), "seed": 7,
            "values": {k: float(v) for k, v in run().items()},
        },
        indent=2, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE}")
