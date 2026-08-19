"""Rank estimation, tail extrapolation and Monte Carlo error."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.stats import norm

from fpl_edge.sim.rank import (
    Counterfactual,
    RankDistribution,
    _cornish_fisher_survival,
    rank_from_scores,
)

FIELD = 5_896_644


def _dist(ranks, scores=None, field_mean=None, label=""):
    n = len(ranks)
    rng = np.random.default_rng(0)
    return RankDistribution(
        ranks=np.asarray(ranks, dtype=float),
        my_scores=rng.normal(2000, 100, n) if scores is None else scores,
        field_mean_score=rng.normal(2100, 40, n) if field_mean is None else field_mean,
        field_size=FIELD,
        label=label,
    )


def test_rank_is_monotone_decreasing_in_my_score():
    rng = np.random.default_rng(1)
    rivals = rng.normal(2100, 120, size=(4_000, 200))
    lo = rank_from_scores(np.full(200, 1900.0), rivals, field_size=FIELD)
    hi = rank_from_scores(np.full(200, 2400.0), rivals, field_size=FIELD)
    assert (hi < lo).all()


def test_beating_exactly_half_the_field_gives_the_median_rank():
    rivals = np.tile(np.arange(10_001, dtype=float)[:, None], (1, 5))
    ranks = rank_from_scores(np.full(5, 5_000.0), rivals, field_size=FIELD)
    assert ranks == pytest.approx(np.full(5, 1 + 0.5 * FIELD), rel=0.01)


def test_cornish_fisher_reduces_to_the_normal_tail_at_zero_skew():
    t = np.linspace(-3, 4, 25)
    assert _cornish_fisher_survival(t, np.zeros_like(t)) == pytest.approx(norm.sf(t))


def test_positive_skew_makes_a_high_score_less_impressive():
    """With a long right tail, being 3 sigma up is a less rare event."""
    t = np.full(3, 3.0)
    heavy = _cornish_fisher_survival(t, np.full(3, 1.0))
    plain = _cornish_fisher_survival(t, np.zeros(3))
    assert (heavy > plain).all()


def test_the_parametric_tail_takes_over_where_counting_cannot_resolve():
    """With 2,000 rivals the empirical count cannot distinguish top 100 from top 1.

    The blend must therefore return a strictly positive, strictly decreasing
    probability there rather than collapsing every unbeaten simulation to the
    same rank.
    """
    rng = np.random.default_rng(2)
    rivals = rng.normal(2100, 120, size=(2_000, 400))
    a = rank_from_scores(np.full(400, 2650.0), rivals, field_size=FIELD)
    b = rank_from_scores(np.full(400, 2750.0), rivals, field_size=FIELD)
    assert (a > 1.0).all() and (b >= 1.0).all()
    assert b.mean() < a.mean(), "a better score must still improve the estimated rank"
    assert a.std() > 0, "the deep tail must not be quantised to a single value"


def test_summary_flags_which_thresholds_are_extrapolated():
    s = _dist(np.geomspace(50, 4_000_000, 2_000)).summary()
    assert s["extrapolated_top_100"] == 1
    assert s["extrapolated_top_1000"] == 1
    assert s["extrapolated_top_100000"] == 0


def test_probabilities_and_standard_errors_are_consistent():
    ranks = np.geomspace(10, 5_000_000, 20_000)
    d = _dist(ranks)
    p = d.p_top(10_000)
    assert 0.0 < p < 1.0
    assert d.se_p_top(10_000) == pytest.approx(np.sqrt(p * (1 - p) / 20_000))
    assert d.p_top(100) <= d.p_top(1_000) <= d.p_top(10_000)


def test_histogram_partitions_the_whole_distribution():
    d = _dist(np.geomspace(1, 5_000_000, 5_000))
    assert sum(d.histogram().values()) == pytest.approx(1.0)


def test_paired_counterfactual_beats_the_unpaired_standard_error():
    """The point of common random numbers, stated as an inequality.

    Two candidates that differ only slightly produce highly correlated
    indicator vectors, so the paired standard error of the difference is much
    smaller than the naive sum-of-variances one.
    """
    rng = np.random.default_rng(3)
    base = rng.lognormal(11.5, 1.1, 20_000)
    a = _dist(base * 0.97, label="a")
    b = _dist(base, label="b")
    cf = Counterfactual(a=a, b=b)
    paired = cf.se_delta_p_top(10_000)
    unpaired = np.sqrt(a.se_p_top(10_000) ** 2 + b.se_p_top(10_000) ** 2)
    assert paired < unpaired / 3.0
    assert cf.delta_p_top(10_000) > 0
    assert "delta_p_top_10000" in cf.summary()


def test_correlation_with_the_field_is_reported_and_signed():
    rng = np.random.default_rng(4)
    common = rng.normal(0, 1, 5_000)
    mine = 2000 + 90 * common + rng.normal(0, 30, 5_000)
    field = 2100 + 40 * common
    d = _dist(np.full(5_000, 5_000.0), scores=mine, field_mean=field)
    assert d.correlation_with_field() > 0.9
    assert d.beta_on_field() == pytest.approx(90 / 40, rel=0.1)
