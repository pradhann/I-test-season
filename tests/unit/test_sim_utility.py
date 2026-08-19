"""Rank utility: the exact functional form, and the properties it must have."""

from __future__ import annotations

import math

import numpy as np
import pytest

from fpl_edge.models.contracts import RankUtilityConfig
from fpl_edge.sim.rank import RankDistribution
from fpl_edge.sim.utility import (
    DEFAULT_CVAR_ALPHA,
    DEFAULT_STRETCH_WEIGHT,
    catastrophe_loss,
    expected_points_objective,
    make_objective,
    rank_utility,
    rank_utility_of,
)

FIELD = 5_896_644
BALANCED = RankUtilityConfig(target_rank=10_000, stretch_rank=1_000,
                             risk_lambda=0.35, field_size=FIELD)


def test_catastrophe_loss_is_zero_at_the_target_and_one_at_the_back():
    loss = catastrophe_loss(np.array([1.0, 10_000.0, float(FIELD)]), 10_000, FIELD)
    assert loss[0] == 0.0
    assert loss[1] == 0.0
    assert loss[2] == pytest.approx(1.0)


def test_catastrophe_loss_is_logarithmic_not_linear():
    """11,000th and 12,000th should be nearly the same; 400k and 2M should not."""
    loss = catastrophe_loss(np.array([11_000.0, 12_000.0, 400_000.0, 2_000_000.0]),
                            10_000, FIELD)
    assert abs(loss[1] - loss[0]) < 0.02
    assert loss[3] - loss[2] > 0.15


def test_utility_matches_the_documented_formula():
    rng = np.random.default_rng(0)
    ranks = rng.lognormal(11.0, 1.6, 20_000)
    u = rank_utility(ranks, BALANCED)

    p_t = (ranks <= 10_000).mean()
    p_s = (ranks <= 1_000).mean()
    loss = np.clip(np.log(np.maximum(ranks, 1.0) / 10_000) / math.log(FIELD / 10_000), 0, 1)
    k = math.ceil(DEFAULT_CVAR_ALPHA * len(ranks))
    cvar = np.sort(loss)[-k:].mean()
    expected = p_t + DEFAULT_STRETCH_WEIGHT * p_s - BALANCED.risk_lambda * cvar

    assert u.p_target == pytest.approx(p_t)
    assert u.p_stretch == pytest.approx(p_s)
    assert u.catastrophe == pytest.approx(cvar)
    assert u.utility == pytest.approx(expected)


def test_utility_is_monotone_in_rank():
    """Improving any simulation's rank must never lower utility.

    An objective that could be gamed by finishing worse would be unusable in an
    optimizer, which will happily find exactly that hole.
    """
    rng = np.random.default_rng(1)
    ranks = rng.lognormal(11.5, 1.4, 5_000)
    base = rank_utility(ranks, BALANCED).utility
    for factor in (0.99, 0.9, 0.5, 0.2):
        better = rank_utility(ranks * factor, BALANCED).utility
        assert better >= base - 1e-12
        base_worse = rank_utility(ranks / factor, BALANCED).utility
        assert base_worse <= rank_utility(ranks, BALANCED).utility + 1e-12


def test_risk_lambda_only_penalises_the_left_tail():
    """A better right tail must never be punished; a worse left tail must be."""
    rng = np.random.default_rng(2)
    ranks = rng.lognormal(11.5, 1.2, 8_000)
    safe = rank_utility(ranks, BALANCED)

    upside = ranks.copy()
    top = np.argsort(upside)[:400]
    upside[top] *= 0.3                                   # right tail improves
    assert rank_utility(upside, BALANCED).catastrophe == pytest.approx(safe.catastrophe)
    assert rank_utility(upside, BALANCED).utility >= safe.utility

    downside = ranks.copy()
    worst = np.argsort(downside)[-800:]
    downside[worst] = FIELD                              # left tail collapses
    assert rank_utility(downside, BALANCED).catastrophe > safe.catastrophe
    assert rank_utility(downside, BALANCED).utility < safe.utility


def test_zero_risk_lambda_reduces_to_the_hit_probabilities():
    rng = np.random.default_rng(3)
    ranks = rng.lognormal(11.0, 1.5, 4_000)
    cfg = RankUtilityConfig(target_rank=10_000, stretch_rank=1_000,
                            risk_lambda=0.0, field_size=FIELD)
    u = rank_utility(ranks, cfg)
    assert u.utility == pytest.approx(u.p_target + DEFAULT_STRETCH_WEIGHT * u.p_stretch)


def test_a_risk_averse_configuration_prefers_the_safer_of_two_equal_bets():
    """Two candidates with the same P(top 10k), different disaster exposure."""
    n = 20_000
    safe = np.concatenate([np.full(n // 10, 5_000.0), np.full(9 * n // 10, 900_000.0)])
    risky = np.concatenate([np.full(n // 10, 5_000.0), np.full(9 * n // 10, 4_500_000.0)])
    cfg = RankUtilityConfig(target_rank=10_000, stretch_rank=1_000,
                            risk_lambda=1.0, field_size=FIELD)
    assert rank_utility(safe, cfg).p_target == rank_utility(risky, cfg).p_target
    assert rank_utility(safe, cfg).utility > rank_utility(risky, cfg).utility
    flat = RankUtilityConfig(target_rank=10_000, stretch_rank=1_000,
                             risk_lambda=0.0, field_size=FIELD)
    assert rank_utility(safe, flat).utility == pytest.approx(rank_utility(risky, flat).utility)


def test_field_size_must_come_from_somewhere():
    cfg = RankUtilityConfig(field_size=None)
    with pytest.raises(ValueError, match="field_size is unknown"):
        rank_utility(np.array([1.0]), cfg)
    assert rank_utility(np.array([1.0]), cfg, field_size=FIELD).p_target == 1.0


def test_invalid_configuration_is_rejected():
    with pytest.raises(ValueError):
        rank_utility(np.array([1.0]), RankUtilityConfig(target_rank=0, field_size=FIELD))
    with pytest.raises(ValueError):
        rank_utility(np.array([1.0]), RankUtilityConfig(risk_lambda=9.0, field_size=FIELD))


def test_the_optimizer_facing_objective_is_a_plain_callable():
    rng = np.random.default_rng(5)
    d = RankDistribution(ranks=rng.lognormal(11.2, 1.3, 3_000),
                         my_scores=rng.normal(2100, 90, 3_000),
                         field_mean_score=rng.normal(2100, 40, 3_000),
                         field_size=FIELD)
    obj = make_objective(BALANCED)
    assert obj(d) == pytest.approx(rank_utility_of(d, BALANCED).utility)
    assert "P(rank<=10,000)" in obj.__doc__
    assert expected_points_objective()(d) == pytest.approx(d.my_scores.mean())


def test_expected_points_and_rank_utility_can_rank_two_candidates_differently():
    """The premise, in miniature and without any simulation at all.

    Candidate A scores more points on average. Candidate B is worse on average
    but puts far more of its mass inside the top 10k. If no such pair existed,
    rank utility could not disagree with expected points and the whole engine
    would be redundant.
    """
    n = 10_000
    ranks_a = np.full(n, 60_000.0)
    ranks_b = np.concatenate([np.full(n // 5, 4_000.0), np.full(4 * n // 5, 900_000.0)])
    pts_a, pts_b = np.full(n, 2_300.0), np.full(n, 2_270.0)
    da = RankDistribution(ranks_a, pts_a, np.full(n, 2_100.0), field_size=FIELD, label="A")
    db = RankDistribution(ranks_b, pts_b, np.full(n, 2_100.0), field_size=FIELD, label="B")

    assert expected_points_objective()(da) > expected_points_objective()(db)
    assert make_objective(BALANCED)(db) > make_objective(BALANCED)(da)
