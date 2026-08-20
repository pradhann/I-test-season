"""The EO-vs-inclusion distinction, enforced.

These tests are the reason ``fpl_edge/models/field/share.py`` exists. The bug
they encode really happened: effective ownership (which counts a captain twice
and totals ~12) was passed to a squad-inclusion sampler that clips at 1, so
Haaland's EO of 1.139 became an inclusion probability of 0.999 and the simulated
field owned him universally. Every test below fails if that path reopens.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.models.field.share import (
    EffectiveOwnership,
    InclusionProbability,
    ShareTypeError,
    reconcile,
    require_effective_ownership,
    require_inclusion,
)
from fpl_edge.models.ownership.eo import effective_ownership as eo_algebra


def _inclusion_vector(n: int = 100) -> np.ndarray:
    """A legal inclusion vector: in [0, 1] and summing to exactly 15."""
    rng = np.random.default_rng(11)
    v = rng.random(n)
    return v * (15.0 / v.sum())


def _shares(n: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """``(start_share, captain_share)``: sum exactly 11 and exactly 1.

    Those two totals are what make EO total exactly 12. Player 0 is
    Haaland-shaped -- started by 0.72 of the field and captained by 0.6 of it,
    so his EO is 1.32, above 1 and therefore not a probability.
    """
    rng = np.random.default_rng(12)
    start = rng.random(n)
    start[0] = 5.0                      # dominant before normalisation
    start *= 11.0 / start.sum()
    start = start / start[0] * 0.72 if start[0] > 0.72 else start
    start *= 11.0 / start.sum()
    cap = np.zeros(n)
    cap[0], cap[1], cap[2] = 0.6, 0.3, 0.1
    return start, cap


def _eo_vector(n: int = 100) -> np.ndarray:
    start, cap = _shares(n)
    return eo_algebra(start, cap, validate=False)


# -- the types are not interchangeable ---------------------------------------


def test_eo_cannot_be_constructed_as_an_inclusion_probability():
    """The exact historical bug: EO handed to the squad sampler's argument."""
    with pytest.raises(ValueError, match=r"must lie in \[0, 1\]"):
        InclusionProbability(_eo_vector(), cohort="overall", provenance="bug")


def test_eo_that_stays_under_one_is_still_caught_by_its_total():
    """A magnet-free EO vector has max <= 1, so the range check cannot see it.

    The total does: eleven starters plus one armband is ~12, never 15. Without
    this second check the guard would only fire in the weeks a premium happened
    to be captained hard enough, which is exactly when it is least needed.
    """
    n = 100
    start = np.full(n, 11.0 / n)
    cap = np.zeros(n)
    cap[0], cap[1] = 0.5, 0.5
    eo = eo_algebra(start, cap, validate=False)
    assert eo.max() < 1.0
    assert eo.sum() == pytest.approx(12.0)
    with pytest.raises(ValueError, match="sum to about 15"):
        InclusionProbability(eo, cohort="overall", provenance="bug")


def test_inclusion_cannot_be_constructed_as_effective_ownership():
    with pytest.raises(ValueError, match="total about 12"):
        EffectiveOwnership(_inclusion_vector(), cohort="overall", provenance="bug")


def test_the_two_types_share_no_array_attribute_name():
    """Duck typing must fail, not merely be discouraged.

    If both classes exposed ``.values`` a mis-typed call would keep working and
    the whole module would be decoration. The array names differ on purpose.
    """
    inc = InclusionProbability(_inclusion_vector(), cohort="overall", provenance="t")
    eo = EffectiveOwnership(_eo_vector(), cohort="overall", provenance="t")
    assert not hasattr(inc, "multiplier")
    assert not hasattr(eo, "p_in_squad")
    assert not hasattr(inc, "values") and not hasattr(eo, "values")


def test_neither_type_coerces_to_an_array_implicitly():
    """``np.asarray(x)`` must not quietly produce the underlying vector."""
    inc = InclusionProbability(_inclusion_vector(), cohort="overall", provenance="t")
    eo = EffectiveOwnership(_eo_vector(), cohort="overall", provenance="t")
    for obj in (inc, eo):
        with pytest.raises(ShareTypeError):
            np.asarray(obj)


def test_guards_reject_the_wrong_type_and_bare_arrays():
    inc = InclusionProbability(_inclusion_vector(), cohort="overall", provenance="t")
    eo = EffectiveOwnership(_eo_vector(), cohort="overall", provenance="t")

    assert require_inclusion(inc) is inc.p_in_squad
    assert require_effective_ownership(eo) is eo.multiplier

    with pytest.raises(ShareTypeError, match="Haaland bug"):
        require_inclusion(eo)
    with pytest.raises(ShareTypeError, match="understates every captaincy magnet"):
        require_effective_ownership(inc)
    # A bare ndarray is the shape the bug had; it is refused at the boundary.
    with pytest.raises(ShareTypeError, match="must be an InclusionProbability"):
        require_inclusion(_inclusion_vector())
    with pytest.raises(ShareTypeError, match="must be an EffectiveOwnership"):
        require_effective_ownership(_eo_vector())


# -- the algebra the types wrap ----------------------------------------------


def test_eo_must_dominate_start_share():
    """``EO = ownership - captaincy`` is the classic sign error; reject it."""
    n = 50
    start = np.full(n, 11.0 / n)
    cap = np.zeros(n)
    cap[0], cap[1] = 0.5, 0.5
    with pytest.raises(ValueError, match="captaincy is additive"):
        EffectiveOwnership(
            multiplier=start - cap, cohort="overall", provenance="sign error",
            start_share=start, whole_universe=False,
        )


def test_a_captaincy_magnet_has_eo_above_its_inclusion():
    """The number the bug destroyed, quoted rather than asserted in prose."""
    n = 100
    start, cap = _shares(n)
    # Inclusion dominates start (benched owners still own), and totals 15 to
    # start's 11: four bench slots per manager, spread over the field.
    bench = np.full(n, 4.0 / n)
    inc_v = start + bench
    assert (inc_v >= start).all() and inc_v.sum() == pytest.approx(15.0)
    inc = InclusionProbability(inc_v, cohort="overall", provenance="t")
    eo = EffectiveOwnership(
        eo_algebra(start, cap, validate=False), cohort="overall", provenance="t",
        start_share=start, captain_share=cap,
    )
    audit = reconcile(inc, eo)
    assert audit["inclusion_sum"] == pytest.approx(15.0)
    assert audit["eo_sum"] == pytest.approx(12.0)
    assert audit["start_sum"] == pytest.approx(11.0)
    # The captained player's EO exceeds his inclusion; that gap is the whole
    # reason the two must not be the same argument.
    assert audit["argmax_eo_minus_inclusion"] == 0
    assert audit["max_eo_minus_inclusion"] > 0.3
    # And benched owners are the term EO drops and inclusion keeps.
    assert audit["max_bench_share"] > 0.0


def test_pace_increment_uses_eo_weights():
    """EO is the weight in the field's expected score -- its one correct use."""
    n = 20
    start = np.full(n, 11.0 / n)
    cap = np.zeros(n)
    cap[3] = 1.0
    eo = EffectiveOwnership(
        eo_algebra(start, cap, validate=False), cohort="overall", provenance="t",
        start_share=start, captain_share=cap,
    )
    points = np.zeros((n, 4))
    points[3, :] = 10.0
    # Everyone starts player 3 at 11/20 and the whole field captains him, so
    # his contribution is (0.55 + 1.0) * 10.
    assert eo.pace_increment(points) == pytest.approx(np.full(4, 15.5))


def test_standard_error_is_a_category_error_for_a_prior():
    inc = InclusionProbability(_inclusion_vector(), cohort="overall",
                               provenance="ownership_marginals:prior")
    with pytest.raises(ValueError, match="model error"):
        inc.standard_error()
    measured = InclusionProbability(_inclusion_vector(), cohort="top1k",
                                    provenance="empirical_picks", n_observed=625)
    se = measured.standard_error()
    # Binomial SE at p = 0.5 from 625 squads is exactly 0.02.
    p = measured.p_in_squad
    j = int(np.argmin(np.abs(p - 0.5)))
    assert se[j] == pytest.approx(np.sqrt(p[j] * (1 - p[j]) / 625))
    assert se.max() <= 0.5 / np.sqrt(625) + 1e-12
