"""De-vig maths, tested directly.

The de-vig is the one piece of this pipeline that is pure arithmetic with a
checkable contract, so it gets checked properly rather than by eyeballing a
dataframe. Everything downstream -- clean-sheet calibration, scorer rates --
inherits its bias, and a de-vig that is wrong in the tail is exactly the kind
of error that looks fine on a favourite and destroys a differential call.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from fpl_edge.ingest.odds import (
    american_to_decimal,
    clean_sheet_probs,
    devig,
    devig_independent,
    devig_multiplicative,
    devig_power,
    devig_shin,
    fit_goal_rates,
    implied_prob,
    overround,
    shin_z,
)

#: A real 1X2 book. Liverpool v Bournemouth closing odds, football-data.co.uk
#: E0.csv 2025-26, fetched 2026-08-18.
REAL_1X2 = [1.31, 6.13, 9.34]

METHODS = ["multiplicative", "shin", "power"]


# -- basic conversions -------------------------------------------------------


def test_american_to_decimal_both_signs() -> None:
    # Pinnacle quoted -202/+487/+404 on Brighton v Aston Villa, 2026-08-18.
    assert american_to_decimal(-202) == pytest.approx(1.495049, rel=1e-6)
    assert american_to_decimal(487) == pytest.approx(5.87, rel=1e-9)
    assert american_to_decimal(100) == pytest.approx(2.0)
    assert american_to_decimal(-100) == pytest.approx(2.0)


def test_american_to_decimal_rejects_zero() -> None:
    with pytest.raises(ValueError):
        american_to_decimal(0)


def test_implied_prob_rejects_impossible_prices() -> None:
    for bad in (1.0, 0.5, 0.0, -2.0):
        with pytest.raises(ValueError):
            implied_prob(bad)


def test_overround_matches_hand_calculation() -> None:
    # Evens/evens is a 100% book: two legs at 2.0 -> 0.5 + 0.5 = 1.0.
    assert overround([2.0, 2.0]) == pytest.approx(0.0, abs=1e-12)
    # 1.90/1.90 is the classic -110 two-way book, ~5.26% overround.
    assert overround([1.90, 1.90]) == pytest.approx(2 / 1.9 - 1, rel=1e-12)
    assert overround(REAL_1X2) == pytest.approx(
        1 / 1.31 + 1 / 6.13 + 1 / 9.34 - 1, rel=1e-12
    )


# -- the core contract: probabilities sum to one -----------------------------


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize(
    "book",
    [
        REAL_1X2,
        [1.53, 4.20, 6.00],
        [2.10, 3.40, 3.60],
        [1.20, 7.00, 15.00],   # heavy favourite, long tail
        [1.01, 40.0, 90.0],    # extreme: the tail is where methods diverge
        [1.90, 1.90],          # two-way
    ],
)
def test_devig_sums_to_one(method: str, book: list[float]) -> None:
    p = devig(book, method)  # type: ignore[arg-type]
    assert p.sum() == pytest.approx(1.0, abs=1e-12)
    assert np.all(p > 0.0)
    assert np.all(p < 1.0)


@pytest.mark.parametrize("method", METHODS)
def test_devig_preserves_ordering(method: str) -> None:
    """De-vigging may move probabilities but must never reorder outcomes."""
    p = devig(REAL_1X2, method)  # type: ignore[arg-type]
    assert list(np.argsort(-p)) == [0, 1, 2]


@pytest.mark.parametrize("method", METHODS)
def test_devig_of_a_fair_book_is_the_identity(method: str) -> None:
    """A book with no overround must come back unchanged, not merely close.

    This is the degenerate case that breaks naive root-finders: at ``S == 1``
    both Shin's and the power method's objective is already zero at the
    boundary, so there is no sign change to bracket.
    """
    fair = [1 / 0.5, 1 / 0.3, 1 / 0.2]
    assert overround(fair) == pytest.approx(0.0, abs=1e-12)
    p = devig(fair, method)  # type: ignore[arg-type]
    np.testing.assert_allclose(p, [0.5, 0.3, 0.2], atol=1e-12)


def test_shin_z_is_zero_for_a_fair_book_and_positive_otherwise() -> None:
    assert shin_z([1 / 0.5, 1 / 0.3, 1 / 0.2]) == pytest.approx(0.0, abs=1e-9)
    z = shin_z(REAL_1X2)
    assert 0.0 < z < 0.2


def test_devig_rejects_a_single_outcome() -> None:
    with pytest.raises(ValueError, match="at least two"):
        devig([2.0])


def test_devig_rejects_unknown_method() -> None:
    with pytest.raises(ValueError, match="unknown de-vig method"):
        devig(REAL_1X2, "napkin")  # type: ignore[arg-type]


# -- the part that actually matters: where the methods disagree --------------


def test_shin_and_power_favour_the_favourite_relative_to_multiplicative() -> None:
    """The known failure mode of proportional de-vigging.

    Bookmakers load margin onto longshots, so dividing through by the overround
    systematically under-states the favourite and over-states the tail. Shin and
    the power method both correct in the same direction. If this test ever
    flips, the implementation has a sign error and every derived clean-sheet
    probability is biased.
    """
    mult = devig_multiplicative(REAL_1X2)
    shin = devig_shin(REAL_1X2)
    powr = devig_power(REAL_1X2)

    assert mult[0] < shin[0] < powr[0]      # favourite gains
    assert mult[-1] > shin[-1] > powr[-1]   # longshot loses


def test_the_gap_between_methods_widens_with_the_tail() -> None:
    """Method choice is irrelevant for coin-flips and material for longshots."""
    even_gap = abs(devig_shin([1.9, 1.9])[0] - devig_multiplicative([1.9, 1.9])[0])
    tail_gap = abs(devig_shin([1.2, 7.0, 15.0])[-1]
                   - devig_multiplicative([1.2, 7.0, 15.0])[-1])
    assert even_gap == pytest.approx(0.0, abs=1e-12)
    assert tail_gap > 0.005


def test_devig_removes_all_of_the_overround() -> None:
    """Every unit of margin is removed, no more and no less."""
    for method in METHODS:
        p = devig(REAL_1X2, method)  # type: ignore[arg-type]
        fair_prices = [1.0 / x for x in p]
        assert overround(fair_prices) == pytest.approx(0.0, abs=1e-12)


# -- non-exclusive markets ---------------------------------------------------


def test_independent_devig_does_not_normalise_to_one() -> None:
    """Anytime scorer legs are not mutually exclusive.

    Eleven players can all score in one match, so their probabilities sum to
    the expected number of distinct scorers, not to 1. Normalising a scorer
    market to 1.0 is the single most common way to halve every striker's
    probability, so it gets its own function and its own test.
    """
    scorers = [2.5, 3.5, 4.0, 7.0, 9.0]
    raw_sum = sum(1 / o for o in scorers)
    assert raw_sum > 1.0

    p = devig_independent(scorers, expected_total=1.35)
    assert p.sum() == pytest.approx(1.35, rel=1e-12)
    # ordering and relative weights are preserved
    np.testing.assert_allclose(p / p.sum(), np.array([1 / o for o in scorers]) / raw_sum)


def test_independent_devig_rejects_nonpositive_total() -> None:
    with pytest.raises(ValueError, match="expected_total"):
        devig_independent([2.0, 3.0], expected_total=0.0)


# -- derived clean sheets ----------------------------------------------------


def test_goal_rates_round_trip_from_synthetic_odds() -> None:
    """Generate odds from known Poisson rates, then recover the rates.

    If the fit cannot invert its own forward model there is no point trusting
    it on real books.
    """
    from fpl_edge.ingest.odds import _match_probs

    lam_h, lam_a = 1.85, 0.95
    h, d, a, o = _match_probs(lam_h, lam_a)
    assert h + d + a == pytest.approx(1.0, abs=1e-9)

    rates = fit_goal_rates(h, d, a, o)
    assert rates.home == pytest.approx(lam_h, rel=1e-4)
    assert rates.away == pytest.approx(lam_a, rel=1e-4)
    assert rates.residual < 1e-6


def test_clean_sheet_probability_follows_from_the_opponent_rate() -> None:
    rates = fit_goal_rates(*_probs(1.85, 0.95))
    cs_home, cs_away = clean_sheet_probs(rates)
    # P(home clean sheet) == P(away scores zero) == exp(-lambda_away)
    assert cs_home == pytest.approx(math.exp(-0.95), rel=1e-3)
    assert cs_away == pytest.approx(math.exp(-1.85), rel=1e-3)
    assert cs_home > cs_away  # the stronger side keeps more clean sheets


def _probs(lam_h: float, lam_a: float):
    from fpl_edge.ingest.odds import _match_probs

    h, d, a, o = _match_probs(lam_h, lam_a)
    return h, d, a, o


def test_clean_sheet_is_monotone_in_opponent_strength() -> None:
    """Sanity guard on the derivation used for every DEF/GKP in the model."""
    weak_opponent = clean_sheet_probs(fit_goal_rates(*_probs(1.8, 0.6)))[0]
    strong_opponent = clean_sheet_probs(fit_goal_rates(*_probs(1.8, 1.9)))[0]
    assert weak_opponent > strong_opponent


def test_derived_clean_sheet_sits_in_a_plausible_range_for_real_odds() -> None:
    """Against real closing odds the answer must be football-shaped.

    Liverpool 1.31 / 6.13 / 9.34 with Over 2.5 at 1.44: a heavy home favourite
    in a high-scoring game. Home clean sheet should be moderate (the total is
    high), away clean sheet small.
    """
    p = devig_shin(REAL_1X2)
    p_over = float(devig_shin([1.44, 2.75])[0])
    rates = fit_goal_rates(float(p[0]), float(p[1]), float(p[2]), p_over)
    cs_home, cs_away = clean_sheet_probs(rates)

    assert rates.home > rates.away          # favourite scores more
    assert 1.0 < rates.home + rates.away < 5.0
    assert 0.15 < cs_home < 0.60
    assert 0.0 < cs_away < 0.15
    assert cs_home > cs_away
