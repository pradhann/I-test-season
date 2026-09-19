"""Removing the bookmaker's margin, four ways, and never silently.

An implied probability read straight off a price is not a probability: the book
is overround by design, so the three or four outcomes sum past one. Each
function here is one published method for distributing that excess back, and
they disagree, which is the point. ``devig`` dispatches on a named method and
the caller records which one it used, so a number can always be traced to the
assumption that produced it.

Split out of the 1,966-line ``fpl_edge/ingest/odds.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 15). The cut is by
function name, not by line range: the original's own line ranges overlapped.
``fpl_edge.ingest.odds`` re-exports the whole public surface, so every caller
imports exactly what it imported before.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np
from scipy.optimize import brentq

from fpl_edge.ingest.odds.prices import implied_prob

DevigMethod = Literal["multiplicative", "shin", "power"]


_FAIR_TOL = 1e-9


def devig_multiplicative(decimal_odds: list[float] | np.ndarray) -> np.ndarray:
    """Proportional (normalisation) de-vig: ``p_i = q_i / sum(q)``.

    Assumes the book applies its margin uniformly in probability space. It does
    not: real books load the margin onto longshots, so this systematically
    under-states favourites. Provided as a baseline and for comparison.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    return q / q.sum()


def devig_shin(decimal_odds: list[float] | np.ndarray) -> np.ndarray:
    """Shin (1993) de-vig.

    Models the book as quoting against a proportion ``z`` of insider bettors,
    and solves for the ``z`` that makes the implied fair probabilities sum to
    one::

        p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)),   S = sum(q)

    Empirically closer to realised frequencies than the multiplicative method,
    and the direction of the correction is the one we care about: it pushes
    probability *towards* the favourite and away from the tail.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    return _shin_from_q(q)


def _shin_from_q(q: np.ndarray) -> np.ndarray:
    s = float(q.sum())
    if s <= 1.0 + _FAIR_TOL:
        # Already fair (or underround, e.g. an exchange mid-price). Shin has no
        # root here; normalising is the only defensible thing to do.
        return q / s

    def p_of_z(z: float) -> np.ndarray:
        return (np.sqrt(z**2 + 4.0 * (1.0 - z) * q**2 / s) - z) / (2.0 * (1.0 - z))

    lo, hi = 1e-12, 0.5
    if p_of_z(hi).sum() - 1.0 > 0:
        hi = 0.9  # pathological overround; widen the bracket
    z = brentq(lambda z: float(p_of_z(z).sum()) - 1.0, lo, hi, xtol=1e-14)
    p = p_of_z(z)
    return p / p.sum()  # kill residual float drift so the contract holds exactly


def shin_z(decimal_odds: list[float] | np.ndarray) -> float:
    """The fitted Shin insider proportion. 0 for a fair book."""
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    s = float(q.sum())
    if s <= 1.0 + _FAIR_TOL:
        return 0.0

    def p_of_z(z: float) -> np.ndarray:
        return (np.sqrt(z**2 + 4.0 * (1.0 - z) * q**2 / s) - z) / (2.0 * (1.0 - z))

    hi = 0.5 if p_of_z(0.5).sum() - 1.0 <= 0 else 0.9
    return float(brentq(lambda z: float(p_of_z(z).sum()) - 1.0, 1e-12, hi, xtol=1e-14))


def devig_power(decimal_odds: list[float] | np.ndarray) -> np.ndarray:
    """Power de-vig: solve ``k`` such that ``sum(q_i ** k) == 1``.

    Since every ``q_i < 1`` and the book is overround (``sum(q) > 1``), the
    solution has ``k > 1``, which shrinks small probabilities proportionally
    more than large ones. The most aggressive of the three on the tail.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    s = float(q.sum())
    if s <= 1.0 + _FAIR_TOL:
        return q / s
    hi = 2.0
    while float((q**hi).sum()) - 1.0 > 0 and hi < 64.0:
        hi *= 2.0
    k = brentq(lambda k: float((q**k).sum()) - 1.0, 1.0, hi, xtol=1e-14)
    p = q**k
    return p / p.sum()


_DEVIG: dict[str, Any] = {
    "multiplicative": devig_multiplicative,
    "shin": devig_shin,
    "power": devig_power,
}


def devig(
    decimal_odds: list[float] | np.ndarray, method: DevigMethod = "shin"
) -> np.ndarray:
    """De-vig a *complete* market. The result sums to 1 by construction.

    "Complete" matters: passing two of a 1X2 market's three legs produces
    confident nonsense. Anytime-scorer markets are *not* complete in this sense
    (the outcomes are not mutually exclusive) -- use :func:`devig_independent`.
    """
    if len(decimal_odds) < 2:
        raise ValueError("de-vigging needs at least two outcomes")
    try:
        fn = _DEVIG[method]
    except KeyError:
        raise ValueError(f"unknown de-vig method {method!r}; known: {sorted(_DEVIG)}") from None
    return fn(decimal_odds)


def devig_independent(
    decimal_odds: list[float] | np.ndarray, expected_total: float
) -> np.ndarray:
    """De-vig a market of *non-exclusive* yes/no legs, e.g. anytime scorer.

    Twenty-odd players can all score in the same match, so the legs do not sum
    to 1 -- they sum to the expected number of *distinct* scorers, which is
    strictly less than expected goals. Scaling to a target total is the
    standard fix; ``expected_total`` should come from the match totals market,
    not from the scorer market itself.

    Kept separate from :func:`devig` so that nobody accidentally normalises a
    scorer market to 1.0 and silently halves every striker's probability.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    if expected_total <= 0:
        raise ValueError("expected_total must be positive")
    return q * (expected_total / q.sum())


def devig_anytime_scorer(
    decimal_odds: list[float] | np.ndarray,
    team_expected_goals: float,
    *,
    coverage: float = 0.95,
    method: Literal["power", "uniform"] = "power",
) -> np.ndarray:
    """Estimate fair anytime-scorer probabilities for one team's card.

    **This is an estimate, not an exact de-vig, and the distinction is real.**

    Anytime scorer is a set of *independent* yes/no bets, not a mutually
    exclusive book: eleven players can all score in one match. Their implied
    probabilities therefore do not sum to 1 and normalising them to 1 would be
    nonsense. Measured on the real card for Arsenal v Coventry (2026-08-21),
    17 selections summed to **4.748**.

    Worse, the UK books quote only the **Yes** side -- every outcome came back
    with ``name == "Yes"`` and no matching ``No``. With one side of a two-way
    market missing there is no overround to measure, so the margin has to be
    *estimated* against an external constraint rather than removed exactly.

    The constraint used here comes from the match totals market. If player *i*
    scores ``Poisson(lambda_i)`` goals then ``p_i = 1 - exp(-lambda_i)`` and,
    critically, ``sum(lambda_i) = E[team goals]`` -- goal rates are additive
    where probabilities are not. So we solve for the transform that makes the
    card's implied rates add up to the team's de-vigged expected goals.

    ``method="power"`` (default) solves ``p_i = q_i ** k``. ``method="uniform"``
    scales every implied rate by one constant. Power is the default because
    bookmakers load margin onto longshots far more heavily than onto favourites,
    and a uniform scale therefore over-shrinks the favourite. On the real
    Arsenal card, anchored to 2.998 expected goals:

    ==================  ======  =========  =========
    Selection           Quoted  Uniform    Power
    ==================  ======  =========  =========
    Gyokeres (1.78)     0.5629  0.3431     0.4121
    Odegaard (3.80)     0.2632  0.1437     0.1276
    Mosquera (12.00)    0.0833  0.0432     0.0216
    ==================  ======  =========  =========

    ``coverage`` is the share of the team's goals attributable to the listed
    players -- own goals and unlisted deep substitutes make up the remainder.
    0.95 is a stated assumption, not a measurement.

    **Treat the output as uncertain.** The shrink is large (the quoted card
    implies roughly twice the team's expected goals) and cannot be validated
    against realised results until matches are played. The raw quoted prices are
    always written to ``fact_odds`` alongside these, so nothing is lost if the
    estimate later proves badly calibrated.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    if team_expected_goals <= 0:
        raise ValueError("team_expected_goals must be positive")
    if not 0.0 < coverage <= 1.0:
        raise ValueError("coverage must be in (0, 1]")
    target = team_expected_goals * coverage

    def rate_sum(p: np.ndarray) -> float:
        return float((-np.log(1.0 - np.clip(p, 1e-12, 1 - 1e-12))).sum())

    if rate_sum(q) <= target:
        # The card already implies no more goals than the market does. Nothing
        # to remove; inflating it would be inventing value.
        return q

    if method == "uniform":
        lam_hat = -np.log(1.0 - np.clip(q, 1e-12, 1 - 1e-12))
        return 1.0 - np.exp(-lam_hat * (target / lam_hat.sum()))
    if method != "power":
        raise ValueError(f"unknown method {method!r}; use 'power' or 'uniform'")

    hi = 2.0
    while rate_sum(q**hi) > target and hi < 64.0:
        hi *= 2.0
    k = brentq(lambda k: rate_sum(q**k) - target, 1.0, hi, xtol=1e-12)
    return q**k
