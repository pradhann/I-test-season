"""The field model: does sampling squads actually reproduce the ownership given?

If it does not, every rank number downstream is measuring the wrong field. The
checks here are on the *sampler*, deliberately separated from the checks on
correlation in ``test_sim_correlation.py``.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.sim.field import (
    DEFAULT_FIELD_SIZE,
    FieldConfig,
    FieldModel,
    _clip_to_unit,
    _madow,
)
from fpl_edge.sim.squad import SQUAD_BY_POSITION, XI_SIZE
from fpl_edge.sim.synthetic import toy_world


@pytest.fixture(scope="module")
def world():
    return toy_world(seed=7)


def test_madow_reproduces_inclusion_probabilities_exactly():
    rng = np.random.default_rng(0)
    p, m, n = 18, 120_000, 5
    pi = _clip_to_unit((rng.random(p) + 0.05)[None, :], n)[0]
    piM = np.tile(pi, (m, 1))
    sel = _madow(piM, rng.random(m), np.argsort(rng.random((m, p)), axis=1))

    assert sel.shape == (m, n)
    assert (np.diff(np.sort(sel, axis=1), axis=1) > 0).all(), "sampling is without replacement"
    empirical = np.bincount(sel.ravel(), minlength=p) / m
    se = np.sqrt(pi * (1 - pi) / m)
    assert (np.abs(empirical - pi) < 5 * se + 0.002).all()


def test_clip_to_unit_keeps_probabilities_valid_and_the_sum_fixed():
    pi = _clip_to_unit(np.array([[0.9, 0.9, 0.9, 0.05, 0.05]]), 3.0)
    assert pi.sum() == pytest.approx(3.0)
    assert (pi <= 1.0 + 1e-9).all() and (pi >= 0.0).all()


def test_every_sampled_squad_is_position_legal(world):
    u, _, eo, cap, xp = world
    squads = FieldModel(u, FieldConfig(n_rivals=500)).sample_squads(eo, cap, xp)
    assert squads.slots.shape == (500, 15)
    for p, n in SQUAD_BY_POSITION.items():
        assert ((squads.slot_pos == p).sum(axis=1) == n).all()
    assert (np.sort(squads.slots, axis=1)[:, 1:]
            != np.sort(squads.slots, axis=1)[:, :-1]).all(), "no duplicate players"
    assert (squads.slot_pos[:, XI_SIZE] == 1).all(), "bench slot 0 is the reserve keeper"
    assert ((squads.slot_pos[:, :XI_SIZE] == 1).sum(axis=1) == 1).all()


def test_realised_ownership_matches_the_forecast(world):
    u, _, eo, cap, xp = world
    field = FieldModel(u, FieldConfig(n_rivals=8_000))
    squads = field.sample_squads(eo, cap, xp)
    target = field.target_ownership(eo)
    got = squads.ownership_realised(u.n_players)
    assert np.abs(got - target).max() < 0.03
    assert np.abs(got - target).mean() < 0.005


def test_ownership_is_renormalised_to_a_legal_squad_size(world):
    u, _, eo, _, _ = world
    field = FieldModel(u, FieldConfig(n_rivals=100))
    target = field.target_ownership(eo)
    for p, n in SQUAD_BY_POSITION.items():
        assert target[u.position == p].sum() == pytest.approx(n, abs=1e-6)
    assert field.ownership_renormalisation(eo)["max_abs_shift"] < 0.02


def test_captaincy_share_tracks_the_forecast(world):
    u, _, eo, cap, xp = world
    squads = FieldModel(u, FieldConfig(n_rivals=8_000)).sample_squads(eo, cap, xp)
    got = squads.captain_share(u.n_players)
    assert got.sum() == pytest.approx(1.0)
    assert np.abs(got - cap).max() < 0.06
    assert np.argmax(got) == np.argmax(cap)


def test_sampling_is_deterministic_given_the_seed(world):
    u, _, eo, cap, xp = world
    a = FieldModel(u, FieldConfig(n_rivals=300, seed=42)).sample_squads(eo, cap, xp)
    b = FieldModel(u, FieldConfig(n_rivals=300, seed=42)).sample_squads(eo, cap, xp)
    c = FieldModel(u, FieldConfig(n_rivals=300, seed=43)).sample_squads(eo, cap, xp)
    assert np.array_equal(a.slots, b.slots)
    assert np.array_equal(a.captain_slot, b.captain_slot)
    assert not np.array_equal(a.slots, c.slots)


def test_squads_persist_across_gameweeks_up_to_the_churn_rate(world):
    u, _, eo, cap, xp = world
    field = FieldModel(u, FieldConfig(n_rivals=1_000, churn=0.10))
    a = field.sample_squads(eo, cap, xp, gw_offset=0)
    b = field.sample_squads(eo, cap, xp, gw_offset=1)
    same = np.array([len(set(x) & set(y)) for x, y in zip(a.slots, b.slots)])
    assert same.mean() > 14.0, "a 10% churn rate must not reshuffle the whole field"


def test_stratification_reduces_the_spread_of_squad_quality(world):
    """``template_alignment`` is claimed to cluster the field. Check it does."""
    u, _, eo, cap, xp = world
    spreads = []
    for align in (0.0, 0.95):
        squads = FieldModel(
            u, FieldConfig(n_rivals=4_000, template_alignment=align)
        ).sample_squads(eo, cap, xp)
        spreads.append(xp[squads.slots].sum(axis=1).std())
    assert spreads[1] < spreads[0], "stratified sampling should make the field more alike"


def test_default_field_size_matches_the_rule_registry():
    from fpl_edge.rules import rules

    assert DEFAULT_FIELD_SIZE == rules().get("misc.total_players_at_fetch")
