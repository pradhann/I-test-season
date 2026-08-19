"""The engine: caching, plans, determinism and the paired-counterfactual contract."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import PointsSample, RankUtilityConfig
from fpl_edge.sim.engine import SeasonSimulator, SquadPlan, greedy_squad
from fpl_edge.sim.field import FieldConfig
from fpl_edge.sim.synthetic import toy_world
from fpl_edge.sim.utility import make_objective

GWS = (1, 2, 3, 4)


class _Ownership:
    card = None

    def __init__(self, universe, eo, captaincy):
        self.universe, self.eo, self.captaincy = universe, eo, captaincy

    def forecast(self, snapshot, season, gw):
        return pd.DataFrame({"code": self.universe.codes, "gw": int(gw),
                             "eo_overall": self.eo, "captaincy_share": self.captaincy,
                             "eo_top10k": self.eo})


def _sim(n_sims=1_200, n_rivals=800, seed=1):
    u, model, eo, cap, xp = toy_world(seed=3)
    sim = SeasonSimulator(u, model, _Ownership(u, eo, cap), None, "toy", GWS,
                          n_sims=n_sims, seed=seed,
                          field_config=FieldConfig(n_rivals=n_rivals, field_size=1_000_000))
    return sim, u, eo, xp


def test_preparation_is_idempotent_and_deterministic():
    a, u, eo, xp = _sim()
    a.prepare()
    first = a.rival_totals.copy()
    a.prepare()
    assert np.array_equal(a.rival_totals, first), "prepare() must not redraw"

    b, *_ = _sim()
    b.prepare()
    assert np.array_equal(b.rival_totals, first), "same seed must give the same field"

    c, *_ = _sim(seed=2)
    c.prepare()
    assert not np.array_equal(c.rival_totals, first)


def test_two_candidates_are_scored_against_an_identical_field():
    """The property that makes every comparison paired."""
    sim, u, eo, xp = _sim()
    a = sim.evaluate(SquadPlan(greedy_squad(u, xp, xp), label="a"))
    b = sim.evaluate(SquadPlan(greedy_squad(u, xp - 40.0 * eo, xp), label="b"))
    assert np.array_equal(a.field_mean_score, b.field_mean_score)
    assert not np.array_equal(a.my_scores, b.my_scores)


def test_a_points_hit_costs_exactly_its_four_points():
    sim, u, eo, xp = _sim()
    squad = greedy_squad(u, xp, xp)
    clean = sim.score_plan(SquadPlan(squad))
    hit = sim.score_plan(SquadPlan(squad, hits={GWS[1]: 4}))
    assert clean - hit == pytest.approx(np.full(len(clean), 4.0))


def test_an_override_applies_from_its_gameweek_onwards_until_reverted():
    sim, u, eo, xp = _sim()
    squad = greedy_squad(u, xp, xp)
    other = next(i for i in squad.starters if i != squad.captain)
    plan = SquadPlan(squad).with_captain(GWS[1], other)
    assert plan.squad_for(GWS[0]).captain == squad.captain
    assert plan.squad_for(GWS[1]).captain == other
    assert plan.squad_for(GWS[3]).captain == other

    one_week = SquadPlan(squad).with_captain(GWS[1], other, revert_gw=GWS[2])
    assert one_week.squad_for(GWS[1]).captain == other
    assert one_week.squad_for(GWS[2]).captain == squad.captain


def test_a_single_gameweek_captaincy_change_costs_far_less_than_a_season_long_one():
    sim, u, eo, xp = _sim()
    squad = greedy_squad(u, xp, xp)
    worse = min(squad.starters, key=lambda i: xp[i])
    base = sim.score_plan(SquadPlan(squad))
    one = base - sim.score_plan(SquadPlan(squad).with_captain(GWS[0], worse,
                                                              revert_gw=GWS[1]))
    allyear = base - sim.score_plan(SquadPlan(squad).with_captain(GWS[0], worse))
    assert allyear.mean() > 2.0 * one.mean() > 0


def test_triple_captain_triples_rather_than_doubles():
    sim, u, eo, xp = _sim()
    squad = greedy_squad(u, xp, xp)
    plain = sim.score_plan(SquadPlan(squad))
    tc = sim.score_plan(SquadPlan(squad, chips={GWS[0]: "3xc"}))
    # The extra multiplier lands on whoever actually wore the armband, which is
    # the vice in any simulation where the captain played no minutes.
    cp = sim._points[GWS[0]][squad.captain].astype(float)
    cm = sim._minutes[GWS[0]][squad.captain]
    vp = sim._points[GWS[0]][squad.vice].astype(float)
    vm = sim._minutes[GWS[0]][squad.vice]
    expected = np.where(cm > 0, cp, np.where(vm > 0, vp, 0.0))
    assert tc - plain == pytest.approx(expected)


def test_a_mismatched_points_model_is_rejected_rather_than_silently_misaligned():
    sim, u, eo, xp = _sim()

    class Wrong:
        card = None

        def simulate(self, snapshot, season, gw, *, n_sims=10, seed=0):
            return PointsSample(codes=u.codes[::-1], gw=gw,
                                points=np.zeros((u.n_players, n_sims)))

    sim.points_model = Wrong()
    with pytest.raises(ValueError, match="do not match the universe"):
        sim.prepare()


def test_fractional_points_are_rejected_before_the_int8_cache():
    sim, u, eo, xp = _sim()

    class Fractional:
        card = None

        def simulate(self, snapshot, season, gw, *, n_sims=10, seed=0):
            return PointsSample(codes=u.codes, gw=gw,
                                points=np.full((u.n_players, n_sims), 1.5))

    sim.points_model = Fractional()
    with pytest.raises(ValueError, match="must be integers"):
        sim.prepare()


def test_the_optimizer_objective_runs_end_to_end():
    sim, u, eo, xp = _sim()
    objective = make_objective(RankUtilityConfig(target_rank=10_000, stretch_rank=1_000,
                                                 risk_lambda=0.35, field_size=1_000_000))
    good = objective(sim.evaluate(SquadPlan(greedy_squad(u, xp, xp))))
    bad = objective(sim.evaluate(SquadPlan(greedy_squad(u, -xp, xp))))
    assert good > bad


def test_the_field_reproduces_the_ownership_it_was_given():
    sim, u, eo, xp = _sim(n_rivals=6_000)
    sim.prepare()
    err = sim.realised_ownership_error(GWS[0])
    assert err["max_abs_eo_error"] < 0.05
    assert err["weighted_abs_eo_error"] < 0.01
    assert err["max_abs_shift"] < 0.02, "the forecast was already a legal squad size"


def test_diagnostics_report_a_plausible_field():
    sim, u, eo, xp = _sim()
    sim.prepare()
    d = sim.diagnostics
    assert d["n_gws"] == len(GWS)
    assert d["field_mean_per_gw"] > 0
    assert d["prepare_seconds"] > 0
    ladder = sim.field_rank_ladder()
    assert ladder["rank_1,000"] > ladder["rank_100,000"] > ladder["mean"] - 200
