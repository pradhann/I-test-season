"""The skill model, tested where it is easy to be wrong and impossible to notice.

Two categories of test here.

**Recovery tests.** Data is simulated from the model with *known* variance
components, and the estimator is asked to recover them. This is the only way to
know that ``tau^2`` is not quietly absorbing sampling noise -- which is the
classic failure, produces a beautiful-looking skill ranking, and is invisible
from the output alone because a wrong answer looks exactly like a right one.

**Property tests.** Shrinkage has properties that must hold regardless of the
data: one spectacular season must be pulled back harder than eight consistent
ones; reliability must rise with seasons; a pool with no true spread must
collapse every estimate to the mean. Each of those is a bug that would make the
shortlist wrong in a specific, plausible direction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from fpl_edge.models.copying import skill


def _panel(records):
    """Build a season frame from (entry_id, season, rank, pct) tuples."""
    return pd.DataFrame(
        [{"entry_id": e, "season": s, "overall_rank": r, "rank_percentage": p,
          "total_points": 2000} for e, s, r, p in records]
    )


# -- field size -------------------------------------------------------------

def test_field_size_from_bracket_consensus():
    """Rounded percentiles bracket the field size; the consensus recovers it.

    46% of a field, reported to the nearest whole percent, puts the field in
    [rank/0.465, rank/0.455]. Each further manager narrows it. The estimate is
    the range the most managers agree on, and it must contain the truth.
    """
    truth = 4_500_000
    rows = []
    for i, rank in enumerate([2_080_652, 900_000, 45_000]):
        pct = round(100.0 * rank / truth, 1 if rank < 100_000 else 0)
        rows.append((i, "2016/17", rank, pct))
    sizes = skill.estimate_field_sizes(_panel(rows))
    row = sizes.iloc[0]
    assert row["method"] == "bracket-consensus"
    assert row["coverage"] == 1.0, "the three consistent witnesses did not all agree"
    assert row["low"] <= truth <= row["high"]
    assert row["estimate"] == pytest.approx(truth, rel=0.05)


def test_field_size_never_below_the_worst_observed_rank():
    """A field cannot be smaller than a rank achieved in it."""
    sizes = skill.estimate_field_sizes(_panel([(1, "2020/21", 8_000_000, 90.0)]))
    assert sizes.iloc[0]["estimate"] >= 8_000_000


def test_a_contradicting_row_lowers_coverage_rather_than_destroying_the_estimate():
    """One bad witness among many must not throw the season away.

    A strict intersection is empty the moment a single row disagrees, which on a
    six-hundred-manager pool is every season. Max-coverage degrades instead: the
    outlier simply fails to join the consensus and coverage falls by 1/n.
    """
    truth = 5_000_000
    rows = [(i, "2019/20", int(truth * p / 100), p)
            for i, p in enumerate([10.0, 20.0, 30.0, 40.0])]
    rows.append((99, "2019/20", 100_000, 50.0))          # impossible: implies N=200k
    sizes = skill.estimate_field_sizes(_panel(rows))
    row = sizes.iloc[0]
    assert row["method"] == "bracket-consensus"
    assert row["coverage"] == pytest.approx(4 / 5)
    assert row["low"] <= truth <= row["high"]


def test_field_size_reports_coverage_so_weak_seasons_are_visible():
    """Half the pool agreeing is weak evidence and must be reported as such."""
    rows = [(1, "2019/20", 1_000_000, 20.0), (2, "2019/20", 1_000_000, 10.0)]
    sizes = skill.estimate_field_sizes(_panel(rows))
    assert sizes.iloc[0]["coverage"] <= 0.5


def test_percentage_bracket_respects_printed_precision():
    assert skill._pct_bracket(46.0) == pytest.approx((45.5, 46.5))
    lo, hi = skill._pct_bracket(0.2)
    assert (lo, hi) == pytest.approx((0.15, 0.25))


# -- normal scores ----------------------------------------------------------

def test_better_rank_gives_higher_z():
    sizes = pd.DataFrame([{"season": "2020/21", "estimate": 8_000_000}])
    panel = skill.to_normal_scores(
        _panel([(1, "2020/21", 1_000, 0.01), (2, "2020/21", 4_000_000, 50.0)]), sizes
    )
    z = dict(zip(panel["entry_id"], panel["z"]))
    assert z[1] > z[2]
    assert z[2] == pytest.approx(0.0, abs=1e-3), "the median finisher is not z=0"


def test_z_is_comparable_across_seasons_of_different_size():
    """Top 0.1% in a 4M field and in an 11M field must score the same."""
    sizes = pd.DataFrame([{"season": "2016/17", "estimate": 4_000_000},
                          {"season": "2022/23", "estimate": 11_000_000}])
    panel = skill.to_normal_scores(
        _panel([(1, "2016/17", 4_000, 0.1), (2, "2022/23", 11_000, 0.1)]), sizes
    )
    z = dict(zip(panel["entry_id"], panel["z"]))
    assert z[1] == pytest.approx(z[2], abs=0.01)


def test_z_is_clamped_so_one_extreme_row_cannot_poison_the_pool():
    sizes = pd.DataFrame([{"season": "2020/21", "estimate": 8_000_000}])
    panel = skill.to_normal_scores(_panel([(1, "2020/21", 1, 0.00001)]), sizes)
    assert abs(panel.iloc[0]["z"]) <= skill.Z_CLAMP


# -- variance recovery ------------------------------------------------------

def _simulate(n_managers, n_seasons, tau, sigma, seed=0):
    rng = np.random.default_rng(seed)
    theta = rng.normal(0.0, tau, n_managers)
    rows = []
    for m in range(n_managers):
        for s in range(n_seasons):
            rows.append({"entry_id": m, "season": f"{2010 + s}/{str(11 + s).zfill(2)}",
                         "z": theta[m] + rng.normal(0.0, sigma),
                         "overall_rank": 1000})
    return pd.DataFrame(rows), theta


def test_variance_components_are_recovered_from_simulated_data():
    # 2000 managers, not 400. The method-of-moments estimate of tau^2 is a
    # difference of two variances, so its own standard error scales as
    # 1/sqrt(n_managers); at n=400 a correct estimator misses by 20% on an
    # unlucky seed and the test would be measuring the seed.
    panel, _theta = _simulate(2000, 8, tau=0.5, sigma=0.8, seed=7)
    model = skill.fit_skill(panel)
    assert model.sigma2_within == pytest.approx(0.8 ** 2, rel=0.10)
    assert model.tau2_between == pytest.approx(0.5 ** 2, rel=0.20)
    assert model.icc == pytest.approx(0.25 / (0.25 + 0.64), rel=0.20)


def test_tau2_collapses_to_zero_when_every_manager_is_identical():
    """No true spread must not be read as skill. This is THE failure mode.

    If ``tau^2`` absorbed sampling noise, a pool of identical managers would
    produce a confident ranking of them, and the shortlist would be a ranking of
    luck wearing the clothes of a ranking of skill.
    """
    panel, _ = _simulate(2000, 6, tau=0.0, sigma=1.0, seed=3)
    model = skill.fit_skill(panel)
    assert model.tau2_between < 0.01
    assert model.icc < 0.03

    scores = skill.score_managers(panel, model)
    assert scores["theta_hat"].std() < 0.05, "identical managers were ranked apart"


def test_icc_is_high_when_noise_is_small():
    panel, _ = _simulate(300, 6, tau=1.0, sigma=0.2, seed=5)
    model = skill.fit_skill(panel)
    assert model.icc > 0.9


# -- shrinkage properties ---------------------------------------------------

def test_one_spectacular_season_shrinks_harder_than_eight_consistent_ones():
    """The single most important property in the module.

    A manager with one z=3.0 season and a manager averaging z=3.0 over eight
    must not receive the same estimate. If they do, the shortlist is a list of
    people who got lucky once.
    """
    rows = []
    for s in range(8):
        rows.append({"entry_id": 1, "season": f"{2010 + s}/{str(11 + s).zfill(2)}",
                     "z": 3.0, "overall_rank": 1000})
    rows.append({"entry_id": 2, "season": "2017/18", "z": 3.0, "overall_rank": 1000})
    # A background pool so the variance components are estimable at all.
    bg, _ = _simulate(200, 5, tau=0.5, sigma=0.8, seed=11)
    bg["entry_id"] += 100
    panel = pd.concat([pd.DataFrame(rows), bg], ignore_index=True)

    model = skill.fit_skill(panel)
    scores = skill.score_managers(panel, model).set_index("entry_id")
    assert scores.loc[1, "theta_hat"] > scores.loc[2, "theta_hat"]
    assert scores.loc[1, "reliability"] > scores.loc[2, "reliability"]


def test_reliability_increases_monotonically_with_seasons():
    panel, _ = _simulate(200, 10, tau=0.5, sigma=0.8, seed=13)
    model = skill.fit_skill(panel)
    rel = [
        skill.score_managers(
            panel[panel.groupby("entry_id").cumcount() < k], model
        )["reliability"].iloc[0]
        for k in (1, 3, 6, 10)
    ]
    assert rel == sorted(rel)


def test_shrinkage_pulls_toward_the_pool_mean_not_toward_zero():
    """The pool is selected, so its mean is not zero and must not be assumed so."""
    panel, _ = _simulate(200, 4, tau=0.4, sigma=0.9, seed=17)
    panel["z"] += 2.0                       # a uniformly strong pool
    model = skill.fit_skill(panel)
    scores = skill.score_managers(panel, model)
    assert model.mu == pytest.approx(2.0, abs=0.15)
    assert scores["theta_hat"].mean() == pytest.approx(2.0, abs=0.15)


def test_expected_percentile_orders_inversely_to_ability():
    panel, _ = _simulate(100, 5, tau=0.6, sigma=0.7, seed=19)
    model = skill.fit_skill(panel)
    scores = skill.score_managers(panel, model)
    assert scores["expected_percentile"].is_monotonic_increasing, (
        "scores are sorted by ability descending, so percentile must ascend"
    )


# -- persistence ------------------------------------------------------------

def test_persistence_detects_real_signal():
    panel, _ = _simulate(300, 8, tau=0.8, sigma=0.5, seed=23)
    model = skill.fit_skill(panel)
    p = skill.persistence(panel, model)
    assert p.lag1_pairs == 300 * 7
    assert p.lag1_pearson > 0.5
    assert "meaningful persistence" in p.verdict()


def test_persistence_reports_none_when_there_is_none():
    panel, _ = _simulate(300, 8, tau=0.0, sigma=1.0, seed=29)
    model = skill.fit_skill(panel)
    p = skill.persistence(panel, model)
    assert abs(p.lag1_pearson) < 0.1
    assert "noise" in p.verdict() or "weak" in p.verdict()


def test_lag1_pairs_skip_non_consecutive_seasons():
    """A manager who sat out a year must not contribute a two-year 'lag 1' pair."""
    panel = pd.DataFrame([
        {"entry_id": 1, "season": "2018/19", "z": 1.0, "overall_rank": 100},
        {"entry_id": 1, "season": "2020/21", "z": 1.0, "overall_rank": 100},
        {"entry_id": 1, "season": "2021/22", "z": 1.0, "overall_rank": 100},
    ])
    model = skill.fit_skill(panel)
    p = skill.persistence(panel, model)
    assert p.lag1_pairs == 1


def test_walk_forward_is_out_of_sample():
    panel, _ = _simulate(200, 10, tau=0.8, sigma=0.5, seed=31)
    model = skill.fit_skill(panel)
    p = skill.persistence(panel, model, cut_season="2016/17")
    wf = p.walk_forward
    assert wf["n_managers"] == 200
    assert wf["spearman"] > 0.4
    assert wf["top_quartile_beats_median_rate"] > 0.6


# -- shortlist --------------------------------------------------------------

def test_shortlist_excludes_short_records_however_good():
    panel = pd.DataFrame([
        {"entry_id": 1, "season": "2025/26", "z": 4.0, "overall_rank": 50},
        *[{"entry_id": 2, "season": f"{2018 + s}/{str(19 + s).zfill(2)}",
           "z": 2.0, "overall_rank": 5000} for s in range(6)],
    ])
    model = skill.fit_skill(panel)
    scores = skill.score_managers(panel, model)
    short = skill.shortlist(scores, panel, min_seasons=4, top_n=10)
    assert set(short["entry_id"]) == {2}, "a one-season wonder reached the shortlist"


def test_shortlist_attaches_the_real_rank_history():
    panel = pd.DataFrame([
        {"entry_id": 7, "season": f"{2018 + s}/{str(19 + s).zfill(2)}",
         "z": 2.0, "overall_rank": 5000 + s} for s in range(5)
    ])
    model = skill.fit_skill(panel)
    scores = skill.score_managers(panel, model)
    short = skill.shortlist(scores, panel, min_seasons=4)
    row = short.iloc[0]
    assert row["best_rank"] == 5000
    assert row["top10k_seasons"] == 5
    assert "2018/19:5,000" in row["rank_history"]


def test_expected_rank_round_trips_through_the_normal():
    assert skill.expected_rank(0.0, 10_000_000) == pytest.approx(5_000_000, rel=0.01)
    top = skill.expected_rank(float(stats.norm.ppf(1 - 0.001)), 10_000_000)
    assert top == pytest.approx(10_000, rel=0.05)
