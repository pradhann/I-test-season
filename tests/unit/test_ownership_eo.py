"""Effective ownership algebra, worked example by worked example.

Every test here is a sign error somebody has actually shipped.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.models.ownership import eo
from fpl_edge.models.ownership.captaincy import cap_to_start, normalise_start_share


def test_universally_owned_uncaptained_player_has_eo_one() -> None:
    assert eo.effective_ownership(1.0, 0.0)[0] == pytest.approx(1.0)


def test_universally_owned_and_captained_player_has_eo_two() -> None:
    """The ceiling for an ordinary captain: everyone doubles them."""
    assert eo.effective_ownership(1.0, 1.0)[0] == pytest.approx(2.0)


def test_universal_triple_captain_has_eo_three() -> None:
    assert eo.effective_ownership(1.0, 1.0, 1.0)[0] == pytest.approx(3.0)


def test_captaincy_is_added_not_subtracted() -> None:
    """The classic sign error. Captaincy makes a player HARDER to gain on."""
    plain = eo.effective_ownership(0.50, 0.0)[0]
    captained = eo.effective_ownership(0.50, 0.30)[0]
    assert captained > plain
    assert captained == pytest.approx(0.80)


def test_captaincy_is_a_share_of_the_field_not_of_owners() -> None:
    """EO = start + captain, never start * (1 + captain)."""
    got = eo.effective_ownership(0.60, 0.30)[0]
    assert got == pytest.approx(0.90)
    assert got != pytest.approx(0.60 * 1.30)


def test_triple_captain_adds_one_more_not_two_more() -> None:
    """A triple captain contributes 3 = 1 started + 1 captained + 1 extra."""
    base = eo.effective_ownership(0.40, 0.20, 0.0)[0]
    tripled = eo.effective_ownership(0.40, 0.20, 0.20)[0]
    assert tripled - base == pytest.approx(0.20)
    assert tripled == pytest.approx(0.80)


def test_worked_example_haaland_shaped_premium() -> None:
    """70% owned, 92% of owners start him, 45% of the whole field captains him.

    start = 0.70 * 0.92 = 0.644; EO = 0.644 + 0.45 = 1.094.
    """
    start = eo.start_share_from_ownership(0.70, 0.92)[0]
    assert start == pytest.approx(0.644)
    assert eo.effective_ownership(start, 0.45)[0] == pytest.approx(1.094)


def test_bench_boost_lifts_start_share_toward_ownership() -> None:
    without = eo.start_share_from_ownership(0.50, 0.80)[0]
    with_bb = eo.start_share_from_ownership(0.50, 0.80, bench_boost_share=0.5)[0]
    assert without == pytest.approx(0.40)
    assert with_bb == pytest.approx(0.45)
    assert with_bb <= 0.50


def test_ownership_is_not_start_share() -> None:
    """Benched players score nothing, so start share is strictly smaller."""
    assert eo.start_share_from_ownership(0.30, 0.8)[0] < 0.30


def test_captaincy_above_start_share_is_rejected() -> None:
    with pytest.raises(ValueError, match="captain_share exceeds start_share"):
        eo.effective_ownership(0.10, 0.20)


def test_triple_captain_above_captaincy_is_rejected() -> None:
    with pytest.raises(ValueError, match="triple_captain_share exceeds captain_share"):
        eo.effective_ownership(0.50, 0.10, 0.20)


def test_percentages_passed_as_fractions_are_rejected() -> None:
    """70 is not a share. Catching this is the difference between EO 1.1 and 70."""
    with pytest.raises(ValueError, match="not a percentage"):
        eo.start_share_from_ownership(70.0, 1.0)


def test_rank_edge_is_zero_on_a_fully_owned_player() -> None:
    """Owning a 100%-EO player at multiplier 1 gains nothing, however many
    points they score. This is the entire point of the objective."""
    assert eo.rank_edge([1.0], [1.0], [24.0]) == pytest.approx(0.0)


def test_rank_edge_sign() -> None:
    # I captain a player the field owns but does not captain: I gain.
    assert eo.rank_edge([2.0], [1.0], [10.0]) == pytest.approx(10.0)
    # I do not own a player the field captains heavily: I lose.
    assert eo.rank_edge([0.0], [1.5], [10.0]) == pytest.approx(-15.0)


def test_field_mean_points_is_the_eo_weighted_sum() -> None:
    """The identity the whole rank objective rests on."""
    shares = eo.FieldShares(
        ownership=np.array([0.70, 0.20, 0.05]),
        p_start_given_owned=np.array([0.9, 0.8, 0.5]),
        captain_share=np.array([0.50, 0.05, 0.0]),
    )
    points = np.array([12.0, 6.0, 2.0])
    expected = float(np.sum(shares.eo() * points))
    assert shares.field_mean_points(points) == pytest.approx(expected)


def test_my_multiplier_table_matches_the_rules() -> None:
    assert eo.my_multiplier(owned=False, started=False, captain=False) == 0
    assert eo.my_multiplier(owned=True, started=False, captain=False) == 0
    assert eo.my_multiplier(owned=True, started=True, captain=False) == 1
    assert eo.my_multiplier(owned=True, started=True, captain=True) == 2
    assert eo.my_multiplier(owned=True, started=True, captain=True, triple=True) == 3


def test_a_bench_captain_is_impossible() -> None:
    with pytest.raises(ValueError):
        eo.my_multiplier(owned=True, started=False, captain=True)


def test_cap_to_start_keeps_both_constraints() -> None:
    """Clamp-then-renormalise breaks one or the other; the projection keeps both."""
    start = np.array([0.30, 0.25, 0.20, 0.10, 0.40])
    raw = np.array([0.80, 0.10, 0.04, 0.03, 0.03])
    got = cap_to_start(raw, start)
    assert got.sum() == pytest.approx(1.0)
    assert (got <= start + 1e-12).all()
    assert got[0] == pytest.approx(0.30)  # clipped to its starting share


def test_cap_to_start_refuses_an_infeasible_field() -> None:
    with pytest.raises(ValueError, match="cannot name one"):
        cap_to_start(np.array([0.5, 0.5]), np.array([0.3, 0.3]))


def test_normalise_start_share_pins_the_starting_xi() -> None:
    own = np.array([0.70, 0.40, 0.20, 0.10] * 10)
    own = own * (15.0 / own.sum())
    p = normalise_start_share(own, np.full(own.shape, 0.5))
    assert float(np.sum(own * p)) == pytest.approx(11.0, abs=1e-6)
