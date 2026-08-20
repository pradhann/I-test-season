"""The timing result: deciding GW k from GW k-1 squads plus transfer flow.

Picks become public only when a deadline passes, so the freshest observed
squads at the moment the GW-k decision is made locked at GW k-1. The transfer
counters, by contrast, move all week and are readable right up to the deadline.
Drift is the arithmetic that combines them, and these tests pin the arithmetic
rather than the vibe: the per-owner conversion, the legality of a drifted squad,
the cap, the re-arm, and the refusal to drift on flow that was never measured.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from fpl_edge.models.field.drift import (
    MAX_SELL_PROB,
    DriftRates,
    FlowVelocity,
    apply_transfer_drift,
    drift_rates,
    measure_flow_velocity,
)
from fpl_edge.sim.field import FieldConfig, FieldModel
from fpl_edge.sim.squad import SQUAD_BY_POSITION, SQUAD_SIZE, XI_SIZE
from tests.unit.field_fixtures import (
    GW1_DEADLINE,
    SEASON,
    T_DECIDE,
    T_POOL,
    build_warehouse,
    state_frame,
    toy,
)

UTC = dt.timezone.utc
POSITIONS = (1, 2, 3, 4)


def _velocity(n: int, out_idx: int, out_per_day: float,
              in_idx: int, in_per_day: float) -> FlowVelocity:
    vin = np.zeros(n)
    vout = np.zeros(n)
    vin[in_idx] = in_per_day
    vout[out_idx] = out_per_day
    return FlowVelocity(in_per_day=vin, out_per_day=vout, window_days=1.0,
                        as_of=T_DECIDE, provenance="test")


# ---------------------------------------------------------------------------
# the per-owner conversion
# ---------------------------------------------------------------------------


def test_field_outflow_becomes_a_per_owner_sell_probability():
    """"400,000 sold him" is not what a sampled squad experiences.

    The event a squad experiences is "*my* manager sold him", whose probability
    is the outflow divided by the number of owners. With 10% of a 5,000,000
    field owning a player -- 500,000 owners -- an outflow of 50,000 a day over
    two days is 100,000 sales, i.e. a per-owner probability of exactly 0.20.
    """
    n = 6
    own = np.zeros(n)
    own[2] = 0.10
    vel = _velocity(n, out_idx=2, out_per_day=50_000.0, in_idx=3, in_per_day=50_000.0)
    rates = drift_rates(vel, own, field_size=5_000_000, horizon_days=2.0)
    assert rates.sell_prob[2] == pytest.approx(0.20)
    assert rates.sell_prob[[0, 1, 3, 4, 5]].tolist() == [0.0] * 5
    assert rates.buy_weight[3] == pytest.approx(100_000.0)
    assert rates.horizon_days == 2.0
    assert "horizon=2.00d" in rates.provenance


def test_sell_probability_scales_with_the_horizon():
    n = 4
    own = np.zeros(n)
    own[1] = 0.20
    vel = _velocity(n, out_idx=1, out_per_day=20_000.0, in_idx=2, in_per_day=1.0)
    half = drift_rates(vel, own, 1_000_000, 0.5).sell_prob[1]
    full = drift_rates(vel, own, 1_000_000, 1.0).sell_prob[1]
    assert full == pytest.approx(2 * half)
    assert drift_rates(vel, own, 1_000_000, 0.0).sell_prob[1] == 0.0


def test_a_collapsing_denominator_cannot_evacuate_a_player():
    """A tiny ownership denominator would otherwise manufacture a certainty."""
    n = 4
    own = np.zeros(n)
    own[0] = 0.0001
    vel = _velocity(n, out_idx=0, out_per_day=1_000_000.0, in_idx=1, in_per_day=1.0)
    rates = drift_rates(vel, own, 5_000_000, 3.0)
    assert rates.sell_prob[0] == pytest.approx(MAX_SELL_PROB)


def test_an_unowned_player_cannot_be_sold():
    n = 4
    own = np.zeros(n)
    vel = _velocity(n, out_idx=0, out_per_day=99_999.0, in_idx=1, in_per_day=1.0)
    assert drift_rates(vel, own, 5_000_000, 1.0).sell_prob.tolist() == [0.0] * n


def test_negative_horizons_and_negative_flow_are_rejected():
    n = 3
    own = np.full(n, 0.1)
    vel = _velocity(n, 0, 1.0, 1, 1.0)
    with pytest.raises(ValueError, match="non-negative"):
        drift_rates(vel, own, 1000, -1.0)
    with pytest.raises(ValueError, match="negatives are a bug"):
        FlowVelocity(in_per_day=np.array([-1.0]), out_per_day=np.array([0.0]),
                     window_days=1.0, as_of=T_DECIDE, provenance="bad")


# ---------------------------------------------------------------------------
# the drift step
# ---------------------------------------------------------------------------


def _field(universe, own, cap, xp, m=200, seed=4):
    return FieldModel(
        universe, FieldConfig(n_rivals=m, seed=seed)
    ).sample_squads(own, cap, xp)


def test_a_certain_sale_removes_the_player_from_every_squad_that_held_him():
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp)
    n = universe.n_players
    realised = squads.ownership_realised(n)
    mids = np.flatnonzero(universe.position == 3)
    sold = int(mids[np.argmax(realised[mids])])
    assert realised[sold] > 0.2, "need a widely held player for this to bite"

    sell = np.zeros(n)
    sell[sold] = 1.0
    buy = np.zeros(n)
    # Buy weight on every other midfielder priced within tolerance.
    affordable = (universe.position == 3) & (
        universe.price_tenths <= universe.price_tenths[sold] + 3
    )
    buy[affordable] = 1.0
    buy[sold] = 0.0
    rates = DriftRates(sell_prob=sell, buy_weight=buy, horizon_days=1.0,
                       provenance="test")

    drifted, receipt = apply_transfer_drift(
        squads, universe, rates, np.random.default_rng(0), expected_points=xp
    )
    held_before = int((squads.slots == sold).any(axis=1).sum())
    assert receipt["attempted"] == held_before
    assert receipt["applied"] + receipt["cancelled_no_replacement"] == held_before
    assert receipt["capped"] == 0
    if receipt["cancelled_no_replacement"] == 0:
        assert not (drifted.slots == sold).any()
    assert drifted.ownership_realised(n)[sold] < realised[sold]


def test_drift_preserves_every_squad_invariant():
    """A drifted squad must still be a squad, or the empirical construction
    has been traded for a marginal one with extra steps."""
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp, m=300)
    n = universe.n_players
    rng = np.random.default_rng(11)
    sell = rng.random(n) * 0.4
    buy = rng.random(n) * 1000.0
    drifted, receipt = apply_transfer_drift(
        squads, universe,
        DriftRates(sell, buy, horizon_days=1.0, provenance="t"),
        np.random.default_rng(2), expected_points=xp, max_swaps=2,
    )
    assert receipt["applied"] > 0
    assert drifted.slots.shape == (300, SQUAD_SIZE)

    counts = np.stack(
        [(universe.position[drifted.slots] == p).sum(axis=1) for p in POSITIONS],
        axis=1,
    )
    assert (counts == np.array([SQUAD_BY_POSITION[p] for p in POSITIONS])).all()
    ordered = np.sort(drifted.slots, axis=1)
    assert not (ordered[:, 1:] == ordered[:, :-1]).any()
    assert (universe.position[drifted.slots] == drifted.slot_pos).all()

    # 3-per-club: drift must never make club concentration worse. It cannot
    # make it *better* either, and it should not pretend to -- the Madow
    # marginal sampler that produced these squads does not enforce the rule at
    # all (25.4% of squads it draws from the live 2026-27 universe break it;
    # see docs/platform/field_model.md §2.1), so the pre-existing violations
    # are its defect to own, not drift's to launder.
    def worst_club(slots):
        return np.array([
            np.bincount(np.unique(universe.team_code[r], return_inverse=True)[1]).max()
            for r in slots
        ])

    before, after = worst_club(squads.slots), worst_club(drifted.slots)
    assert (after <= np.maximum(before, 3)).all()
    # Armbands stay inside the XI and stay distinct.
    assert (drifted.captain_slot < XI_SIZE).all()
    assert (drifted.vice_slot < XI_SIZE).all()
    assert (drifted.captain_slot != drifted.vice_slot).all()


def test_a_replacement_respects_position_and_price():
    """Legality is checked per move, not repaired afterwards."""
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp, m=150)
    n = universe.n_players
    sell = np.full(n, 0.3)
    buy = np.ones(n)
    before_price = universe.price_tenths[squads.slots].sum(axis=1)
    drifted, _ = apply_transfer_drift(
        squads, universe, DriftRates(sell, buy, 1.0, "t"),
        np.random.default_rng(9), expected_points=xp, max_swaps=2,
        price_tolerance_tenths=3,
    )
    after_price = universe.price_tenths[drifted.slots].sum(axis=1)
    # Each of at most two swaps may add at most the tolerance to squad value.
    assert (after_price <= before_price + 2 * 3).all()


def test_the_swap_cap_is_enforced_and_reported():
    """Real managers make about one transfer a week; drift must not rebuild."""
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp, m=120)
    n = universe.n_players
    sell = np.full(n, 1.0)          # every held player is sold
    buy = np.ones(n)
    drifted, receipt = apply_transfer_drift(
        squads, universe, DriftRates(sell, buy, 1.0, "t"),
        np.random.default_rng(3), expected_points=xp, max_swaps=1,
    )
    assert receipt["attempted"] == 120 * SQUAD_SIZE
    assert receipt["capped"] == 120 * (SQUAD_SIZE - 1)
    assert receipt["applied"] <= 120
    changed = (drifted.slots != squads.slots).sum(axis=1)
    assert changed.max() <= 1


def test_selling_the_captain_re_arms_onto_the_best_remaining_xi_player():
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp, m=60)
    n = universe.n_players
    rows = np.arange(60)
    captains = squads.slots[rows, squads.captain_slot]
    sell = np.zeros(n)
    sell[captains] = 1.0
    buy = np.ones(n)
    drifted, _ = apply_transfer_drift(
        squads, universe, DriftRates(sell, buy, 1.0, "t"),
        np.random.default_rng(6), expected_points=xp, max_swaps=15,
    )
    assert (drifted.captain_slot != drifted.vice_slot).all()
    for r in range(60):
        xi = drifted.slots[r, :XI_SIZE]
        best = int(np.argmax(xp[xi]))
        # The re-arm rule is "highest xP in the XI", excluding the vice slot.
        assert drifted.captain_slot[r] in (best, drifted.captain_slot[r])
        assert xp[drifted.slots[r, drifted.captain_slot[r]]] >= np.median(xp[xi])


def test_a_sale_with_no_legal_replacement_is_cancelled_not_faked():
    """Understating churn is a defect; inventing an illegal squad is a lie."""
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp, m=40)
    n = universe.n_players
    sell = np.full(n, 1.0)
    buy = np.zeros(n)               # nobody is buying anything
    drifted, receipt = apply_transfer_drift(
        squads, universe, DriftRates(sell, buy, 1.0, "t"),
        np.random.default_rng(1), expected_points=xp, max_swaps=2,
    )
    assert receipt["applied"] == 0
    assert receipt["cancelled_no_replacement"] == 40 * 2
    assert np.array_equal(drifted.slots, squads.slots)


def test_drift_is_deterministic_under_a_seed():
    universe, own, cap, xp = toy()
    squads = _field(universe, own, cap, xp, m=100)
    n = universe.n_players
    rates = DriftRates(np.full(n, 0.25), np.ones(n), 1.0, "t")
    a, ra = apply_transfer_drift(squads, universe, rates,
                                 np.random.default_rng(42), expected_points=xp)
    b, rb = apply_transfer_drift(squads, universe, rates,
                                 np.random.default_rng(42), expected_points=xp)
    assert np.array_equal(a.slots, b.slots)
    assert ra == rb


# ---------------------------------------------------------------------------
# measuring the velocity from the warehouse
# ---------------------------------------------------------------------------


def test_flow_velocity_is_none_before_the_counters_have_ever_moved(tmp_path):
    """The pre-GW1 state. None means 'drift disabled', not 'drift of zero'."""
    wh, universe, _ = build_warehouse(tmp_path, with_flow=True)
    assert measure_flow_velocity(wh.snapshot_at(T_POOL), SEASON, universe) is None


def test_a_poll_from_before_the_deadline_is_divided_by_the_right_window(tmp_path):
    """The counters reset at each deadline, so the poll gap is the wrong divisor.

    The fixture's polls straddle the GW1 deadline: one three days before, one
    four days after, so the poll gap is 7 days. But ``transfers_out_event``
    restarted at zero when GW1 locked, so the 200,000 sales it reports all
    happened in the 4 days since. Dividing by 7 understates the velocity by
    7/4 = 1.75x -- and understated flow means an under-drifted field, which is
    a stale field wearing a fresh label.
    """
    wh, universe, meta = build_warehouse(tmp_path, with_flow=True)
    snap = wh.snapshot_at(T_DECIDE)
    vel = measure_flow_velocity(snap, SEASON, universe)
    assert vel is not None
    poll_gap = (T_DECIDE - T_POOL).total_seconds() / 86400.0
    since_deadline = (T_DECIDE - GW1_DEADLINE).total_seconds() / 86400.0
    assert poll_gap == pytest.approx(7.0)
    assert since_deadline == pytest.approx(4.0)

    assert vel.window_days == pytest.approx(since_deadline)
    assert vel.out_per_day[meta["sold"]] == pytest.approx(200_000 / 4.0)
    assert vel.in_per_day[meta["bought"]] == pytest.approx(200_000 / 4.0)
    assert vel.out_per_day.sum() == pytest.approx(200_000 / 4.0)
    assert "level since the GW deadline over 4.00d" in vel.provenance


def test_two_polls_on_the_same_side_of_a_deadline_use_the_plain_difference(tmp_path):
    """The other regime: no reset between the polls, so diff over the gap."""
    wh, universe, meta = build_warehouse(tmp_path, with_flow=True)
    later = T_DECIDE + dt.timedelta(days=1)
    ti = np.zeros(universe.n_players, dtype=int)
    to = np.zeros(universe.n_players, dtype=int)
    ti[meta["bought"]] = 260_000
    to[meta["sold"]] = 260_000
    wh.append("fact_player_state",
              state_frame(universe, toy()[1], later, ti, to))

    vel = measure_flow_velocity(
        wh.snapshot_at(later), SEASON, universe, lookback=dt.timedelta(hours=12)
    )
    assert vel is not None
    assert vel.window_days == pytest.approx(1.0)
    # 260,000 - 200,000 over one day.
    assert vel.out_per_day[meta["sold"]] == pytest.approx(60_000.0)
    assert vel.in_per_day[meta["bought"]] == pytest.approx(60_000.0)
    assert "two-poll diff over 1.00d" in vel.provenance


def test_no_earlier_poll_at_all_returns_none(tmp_path):
    """One poll is not a velocity, and guessing one would be fabrication."""
    wh, universe, _ = build_warehouse(tmp_path, with_flow=True)
    snap = wh.snapshot_at(T_DECIDE)
    # A lookback that predates every poll leaves nothing to difference against.
    assert measure_flow_velocity(
        snap, SEASON, universe, lookback=dt.timedelta(days=10)
    ) is None


def test_undrifted_carry_forward_is_labelled_when_flow_is_unmeasurable(tmp_path):
    """No flow must mean a stated staleness, never a silent one."""
    from fpl_edge.models.field import HybridConfig, HybridFieldSampler
    from fpl_edge.models.field.contracts import PROVENANCE_EMPIRICAL
    from fpl_edge.models.field.share import InclusionProbability

    wh, universe, _ = build_warehouse(tmp_path, n_managers=8, with_flow=False)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_DECIDE), season=SEASON,
        config=HybridConfig(min_observed=4, prior_strength=0.0),
    )
    sample = sampler.sample_squads(
        100, "elite",
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    assert sample.gw == 2 and sample.observed_gw == 1
    assert sample.drift_applied is False
    assert sample.provenance == PROVENANCE_EMPIRICAL
    assert any("UNDRIFTED" in n for n in sample.notes)
