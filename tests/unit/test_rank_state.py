"""The sufficient statistic, and the estimator the study named as job one.

``rank_objectives.md`` §8 closes on the one number it could not derive:

    the first production task is estimating effective ``s`` per squad from the
    simulator's own paired draws

So the load-bearing test here is :func:`deficit_moments` against synthetic
draws with a **known** covariance matrix: if the estimator is right, it recovers
``s^2 = var_mine + var_pace - 2 cov`` from paired samples of a distribution
whose true value we wrote down.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fpl_edge.rank import (
    PROVENANCE_PRESEASON,
    DeficitMoments,
    RankState,
    deficit_moments,
    pace_increments,
    pace_path,
)


def paired_draws(
    *,
    mean_mine: float,
    mean_pace: float,
    var_mine: float,
    var_pace: float,
    corr: float,
    n_gws: int = 4,
    n_sims: int = 400_000,
    seed: int = 20260819,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Draws from a bivariate normal with a covariance we chose.

    Returns ``(my_draws, pace_draws, true_cov)``. Both arrays are
    ``(n_gws, n_sims)`` and column ``k`` is the same simulated world in both --
    which is exactly what "paired" means and what the estimator relies on.
    """
    cov = corr * math.sqrt(var_mine * var_pace)
    sigma = np.array([[var_mine, cov], [cov, var_pace]])
    rng = np.random.default_rng(seed)
    draws = rng.multivariate_normal([mean_mine, mean_pace], sigma, size=(n_gws, n_sims))
    return draws[:, :, 0], draws[:, :, 1], cov


def test_effective_sigma_recovers_a_known_covariance():
    """The estimator must recover (m, s, cov) from a distribution we specified."""
    var_mine, var_pace, corr = 231.0, 190.0, 0.86
    mine, pace, true_cov = paired_draws(
        mean_mine=54.0, mean_pace=53.45,
        var_mine=var_mine, var_pace=var_pace, corr=corr,
    )
    got = deficit_moments(mine, pace)

    true_s = math.sqrt(var_mine + var_pace - 2.0 * true_cov)
    assert got.m == pytest.approx(0.55, abs=0.02)
    assert got.s == pytest.approx(true_s, rel=0.01)
    assert got.var_mine == pytest.approx(var_mine, rel=0.01)
    assert got.var_pace == pytest.approx(var_pace, rel=0.01)
    assert got.cov == pytest.approx(true_cov, rel=0.02)
    assert got.correlation == pytest.approx(corr, rel=0.02)
    # And the identity the whole rank layer rests on closes exactly.
    got.check_decomposition()


def test_the_covariance_channel_is_what_makes_a_template_low_risk():
    """§1: own-score SD ~15/wk, effective s ~3 -- the difference IS the covariance.

    Same own variance, two correlations with the bar. The template's effective
    volatility must collapse while its own-score SD does not move at all.
    """
    var_own = 15.2**2
    template_mine, template_pace, _ = paired_draws(
        mean_mine=54.0, mean_pace=54.3, var_mine=var_own, var_pace=var_own,
        corr=0.98, n_sims=200_000, seed=11,
    )
    diff_mine, diff_pace, _ = paired_draws(
        mean_mine=54.0, mean_pace=53.75, var_mine=var_own, var_pace=var_own,
        corr=0.80, n_sims=200_000, seed=12,
    )
    template = deficit_moments(template_mine, template_pace)
    differential = deficit_moments(diff_mine, diff_pace)

    assert math.sqrt(template.var_mine) == pytest.approx(15.2, rel=0.02)
    assert math.sqrt(differential.var_mine) == pytest.approx(15.2, rel=0.02)
    assert template.s < 4.0 < differential.s
    assert differential.s / template.s > 2.0


def test_unpaired_draws_are_refused_rather_than_silently_averaged():
    mine, pace, _ = paired_draws(
        mean_mine=54.0, mean_pace=53.0, var_mine=100.0, var_pace=90.0,
        corr=0.5, n_gws=3, n_sims=1_000,
    )
    with pytest.raises(ValueError, match="not paired"):
        deficit_moments(mine, pace[:2])


def test_moments_are_per_gameweek_not_pooled_across_the_flattened_array():
    """Pooling would fold week-to-week mean variation into s and inflate it.

    Two gameweeks with identical within-week spread but very different means:
    the honest weekly s is the within-week one, and a pooled estimator would
    report a much larger number.
    """
    rng = np.random.default_rng(3)
    n = 200_000
    pace = np.vstack([rng.normal(40.0, 1.0, n), rng.normal(70.0, 1.0, n)])
    mine = pace + np.vstack([rng.normal(0.5, 3.0, n), rng.normal(0.5, 3.0, n)])
    got = deficit_moments(mine, pace)
    assert got.s == pytest.approx(3.0, rel=0.02)
    assert got.n_gws == 2


def test_pace_path_and_increments_are_cumulative_objects():
    """Q_t is a quantile of CUMULATIVE totals; the increment is its difference.

    The quantile of a sum is not the sum of quantiles, so taking a within-week
    quantile of weekly scores would be a different (and wrong) object.
    """
    rng = np.random.default_rng(7)
    n_gws, n_rivals, n_sims = 3, 2_000, 500
    weekly = rng.normal(50.0, 10.0, (n_gws, n_rivals, n_sims))
    cumulative = np.cumsum(weekly, axis=0)

    q = pace_path(cumulative, field_size=100_000, threshold=10_000)
    assert q.shape == (n_gws, n_sims)
    # The bar is a high quantile, so it must sit above the field's mean...
    assert (q > cumulative.mean(axis=1)).all()
    # ...and it must be monotone in t, because scores only accumulate.
    assert (np.diff(q, axis=0) > 0).all()

    inc = pace_increments(q)
    assert inc.shape == q.shape
    assert np.allclose(np.cumsum(inc, axis=0), q)


def test_preseason_state_is_zero_deficit_and_a_full_season():
    # 231 + 190 - 2*192.5 = 36 = 6.0^2, so the decomposition closes.
    moments = DeficitMoments(
        m=0.55, s=6.0, var_mine=231.0, var_pace=190.0, cov=192.5,
        n_sims=4_000, n_gws=38,
    )
    moments.check_decomposition()
    state = RankState.preseason(moments)
    assert state.deficit == 0.0
    assert state.tau == 38
    assert PROVENANCE_PRESEASON in state.provenance
    # D = 0 with a positive edge is the AHEAD branch, not a neutral one.
    assert state.expected_final_margin == pytest.approx(0.55 * 38)
    assert not state.behind
    assert state.sigma == pytest.approx(6.0 * math.sqrt(38))


def test_live_state_measures_the_deficit_from_standings():
    moments = DeficitMoments(
        m=0.55, s=6.0, var_mine=231.0, var_pace=190.0, cov=192.5,
        n_sims=4_000, n_gws=19,
    )
    state = RankState.from_standings(
        my_total=1_040.0, pace_total=1_060.0, tau=19, moments=moments
    )
    assert state.deficit == pytest.approx(-20.0)
    assert "live_overall_standings" in state.provenance
    # -20 + 0.55*19 = -9.55: edge alone no longer closes this deficit, so the
    # state is on the variance-seeking side of §3's law.
    assert state.expected_final_margin == pytest.approx(-9.55)
    assert state.behind is True


def test_p_hit_matches_the_studys_gaussian_reduction():
    """Phi(z) with z = (D + m tau)/(s sqrt(tau)) -- §2's closed form, exactly."""
    from scipy.stats import norm

    state = RankState.stylised(deficit=-20.0, tau=19, m_weekly=0.55, s_weekly=6.0)
    expected = float(norm.cdf((-20.0 + 0.55 * 19) / (6.0 * math.sqrt(19))))
    assert state.p_hit == pytest.approx(expected)


def test_a_state_without_provenance_is_refused():
    with pytest.raises(ValueError, match="provenance"):
        RankState(deficit=0.0, tau=38, s_weekly=6.0, m_weekly=0.55, provenance="")


def test_zero_effective_volatility_is_refused():
    with pytest.raises(ValueError, match="positive"):
        RankState.stylised(deficit=0.0, tau=10, m_weekly=0.5, s_weekly=0.0)


def test_broken_decomposition_is_detected():
    """The one failure that would poison every theta downstream: unpaired draws."""
    bad = DeficitMoments(
        m=0.5, s=6.0, var_mine=231.0, var_pace=190.0, cov=0.0, n_sims=100, n_gws=1
    )
    with pytest.raises(ValueError, match="not paired"):
        bad.check_decomposition()
