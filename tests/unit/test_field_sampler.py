"""The three constructions, and the honesty of the degradation between them.

Offline and deterministic: the only warehouse touched is a temporary DuckDB
built by ``field_fixtures``. Nothing here reaches the network, and nothing here
reads the real database.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from fpl_edge.models.field import (
    PROVENANCE_EMPIRICAL,
    PROVENANCE_EMPIRICAL_DRIFTED,
    PROVENANCE_HYBRID,
    PROVENANCE_HYBRID_DRIFTED,
    PROVENANCE_MARGINALS_PRIOR,
    FieldSample,
    HybridConfig,
    HybridFieldSampler,
    InclusionProbability,
    load_observed_squads,
    measure_cohort,
)
from fpl_edge.sim.field import FieldConfig, FieldModel, _madow
from fpl_edge.sim.squad import SQUAD_BY_POSITION
from tests.unit.field_fixtures import (
    GW1_DEADLINE,
    SEASON,
    T_DECIDE,
    T_POOL,
    build_warehouse,
    toy,
)

POSITIONS = (1, 2, 3, 4)


# ---------------------------------------------------------------------------
# construction (i): Madow PPS under position + budget constraints
# ---------------------------------------------------------------------------


def test_madow_reproduces_its_inclusion_probabilities_exactly():
    """Madow systematic sampling is *exact* in first-order inclusion, not close.

    The proof is a measure argument on the uniform start ``u``: item j is drawn
    iff some integer offset lands ``u + i`` inside j's interval of the cumulative
    sum, and the total length of the qualifying u-set is exactly ``pi_j``. So
    sweeping u on a stratified grid of G points must reproduce pi_j to within
    the grid resolution 1/G -- there is no sampling noise to allow for, which is
    why the tolerance below is 1/G and not a multiple of a binomial SE.
    """
    rng = np.random.default_rng(3)
    p, n = 40, 5
    pi = rng.random(p)
    pi *= n / pi.sum()
    assert pi.max() < 1.0                      # no clipping needed for this row

    g = 20_000
    pi_rows = np.repeat(pi[None, :], g, axis=0)
    perm = np.repeat(np.argsort(-pi)[None, :], g, axis=0)
    u = (np.arange(g) + 0.5) / g
    picked = _madow(pi_rows, u, perm)

    assert picked.shape == (g, n)
    # Exactly n distinct items per draw -- a systematic sample never repeats.
    assert all(len(set(row.tolist())) == n for row in picked[:200])

    realised = np.bincount(picked.ravel(), minlength=p) / g
    assert np.abs(realised - pi).max() <= 1.0 / g + 1e-12


def test_pps_sampler_gives_exact_position_counts_and_correct_marginals():
    """2/5/5/3 exactly on every rival; marginals within Monte Carlo error.

    Position counts are structural (one fixed-size Madow sample per position
    group) so ``exact`` there means every single rival, not an average. The
    ownership marginals are exact *per rival in expectation*, so across a finite
    field they carry ordinary binomial noise; the bound below is 4 SE, and the
    measured max error at M=20,000 is ~0.006 against an SE of ~0.0035.
    """
    universe, own, cap, xp = toy()
    model = FieldModel(universe, FieldConfig(n_rivals=20_000, seed=1, skill_tilt=0.0))
    squads = model.sample_squads(own, cap, xp)

    counts = np.stack(
        [(universe.position[squads.slots] == p).sum(axis=1) for p in POSITIONS], axis=1
    )
    expected = np.array([SQUAD_BY_POSITION[p] for p in POSITIONS])
    assert (counts == expected).all(), "every rival must hold exactly 2/5/5/3"
    assert np.unique(counts, axis=0).shape[0] == 1

    # No duplicate players inside a squad.
    ordered = np.sort(squads.slots, axis=1)
    assert not (ordered[:, 1:] == ordered[:, :-1]).any()

    target = model.target_ownership(own)
    realised = squads.ownership_realised(universe.n_players)
    se = np.sqrt(np.clip(target, 0, 1) * (1 - np.clip(target, 0, 1)) / 20_000)
    assert np.abs(realised - target).max() <= 4 * se.max()


def test_target_ownership_reports_how_far_it_had_to_move_the_forecast():
    """The renormalisation is a measurement, not a silent repair.

    A forecast whose position group does not sum to the squad rule is moved to
    one that does; ``ownership_renormalisation`` is how a caller sees the size
    of the disagreement instead of inheriting it invisibly.
    """
    universe, own, cap, xp = toy()
    model = FieldModel(universe, FieldConfig(n_rivals=64, seed=1))
    clean = model.ownership_renormalisation(own)
    assert clean["max_abs_shift"] < 1e-6      # the toy forecast already obeys it

    broken = np.clip(own * 1.4, 0.0, 1.0)     # a forecast summing to ~21
    dirty = model.ownership_renormalisation(broken)
    assert dirty["max_abs_shift"] > 0.05
    for p in POSITIONS:
        cols = universe.position == p
        assert model.target_ownership(broken)[cols].sum() == pytest.approx(
            SQUAD_BY_POSITION[p]
        )


# ---------------------------------------------------------------------------
# the pre-GW1 state: empty tables must degrade, never fabricate
# ---------------------------------------------------------------------------


def test_before_the_first_deadline_there_are_no_observed_squads(tmp_path):
    """Picks are private until a deadline passes. The loader must say so."""
    wh, universe, _ = build_warehouse(tmp_path)
    snap = wh.snapshot_at(T_POOL)
    for cohort in ("elite", "top1k"):
        observed, reason = load_observed_squads(snap, SEASON, universe, cohort)
        assert observed is None
        assert "no gameweek deadline has passed" in reason


def test_empty_tables_degrade_to_a_labelled_prior_not_to_invented_squads(tmp_path):
    """The whole hard rule, in one test.

    The sample must still be usable (the solver needs *something* before GW1)
    and must be unmistakably a prior: zero observed squads, zero empirical
    weight, no drift, and a provenance string a backtest can filter on.
    """
    wh, universe, _ = build_warehouse(tmp_path)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_POOL), season=SEASON
    )
    sample = sampler.sample_squads(
        500, "top1k", ownership=InclusionProbability(own, cohort="overall",
                                                     provenance="forecast"),
        captaincy=cap, expected_points=xp,
    )
    assert sample.provenance == PROVENANCE_MARGINALS_PRIOR
    assert sample.is_prior and sample.n_observed == 0
    assert sample.empirical_weight == 0.0
    assert sample.drift_applied is False
    assert sample.chip_rates == {}
    assert sample.chips is None
    assert sample.n_rivals == 500
    # gw was not passed: it is derived as the gameweek this instant decides.
    assert sample.gw == 1
    assert any("prior" in note for note in sample.notes)
    with pytest.raises(ValueError, match="it has no sample"):
        sample.standard_error(0.5)


def test_the_prior_refuses_to_invent_an_ownership_forecast(tmp_path):
    """With no picks AND no forecast there is nothing to sample from."""
    wh, universe, _ = build_warehouse(tmp_path)
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_POOL), season=SEASON
    )
    with pytest.raises(ValueError, match="will not fabricate"):
        sampler.sample_squads(100, "top1k", gw=1)


def test_a_cohort_too_small_to_be_a_sample_is_treated_as_unobserved(tmp_path):
    """Below ``min_observed`` a 'sample' is a rumour, and the note says why."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_DECIDE), season=SEASON,
        config=HybridConfig(min_observed=30),
    )
    sample = sampler.sample_squads(
        200, "elite",
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    assert sample.provenance == PROVENANCE_MARGINALS_PRIOR
    assert any("min_observed" in n for n in sample.notes)


# ---------------------------------------------------------------------------
# construction (ii): empirical resampling of observed squads
# ---------------------------------------------------------------------------


def test_observed_squads_load_with_their_joint_structure(tmp_path):
    wh, universe, meta = build_warehouse(tmp_path, n_managers=8)
    snap = wh.snapshot_at(T_DECIDE)
    observed, reason = load_observed_squads(snap, SEASON, universe, "elite")
    assert observed is not None
    assert observed.n == 8
    assert observed.gw == 1
    assert "8 observed elite squads" in reason
    # The 14-pick manager is dropped, never repaired into a squad.
    assert observed.dropped == 1
    assert meta["malformed_entry"] not in set(observed.entry_ids.tolist())

    counts = np.stack(
        [(universe.position[observed.slots] == p).sum(axis=1) for p in POSITIONS],
        axis=1,
    )
    assert (counts == np.array([SQUAD_BY_POSITION[p] for p in POSITIONS])).all()

    # Co-ownership is a joint fact the marginals cannot carry; check it exists.
    own = observed.ownership(universe.n_players)
    top = np.argsort(-own)[:6]
    joint = observed.pairwise_coownership(top)
    for a in range(len(top)):
        assert joint[a, a] == pytest.approx(own[top[a]])
        for b in range(len(top)):
            assert joint[a, b] <= min(own[top[a]], own[top[b]]) + 1e-12


def test_bootstrap_is_atomic_and_deterministic(tmp_path):
    """Resampling draws whole squads, so nothing within a squad is disturbed."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8)
    observed, _ = load_observed_squads(
        wh.snapshot_at(T_DECIDE), SEASON, universe, "elite"
    )
    squads, pick = observed.bootstrap(400, np.random.default_rng(0), universe)
    assert squads.n_rivals == 400
    seen = {tuple(row) for row in squads.slots}
    originals = {tuple(row) for row in observed.slots}
    assert seen <= originals, "a bootstrapped squad must be an observed squad"
    # The atom problem, measured: 400 draws from 8 squads cover at most 8.
    assert len(seen) <= observed.n
    again, _ = observed.bootstrap(400, np.random.default_rng(0), universe)
    assert np.array_equal(squads.slots, again.slots)
    # The armband travels with the squad it was on -- not redrawn, not detached.
    assert np.array_equal(squads.captain_slot, observed.captain_slot[pick])
    assert np.array_equal(squads.slots, observed.slots[pick])


def test_hybrid_mixes_empirical_and_marginal_and_says_how_much(tmp_path):
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8, with_flow=False)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_DECIDE), season=SEASON,
        config=HybridConfig(min_observed=4, prior_strength=8.0),
    )
    sample = sampler.sample_squads(
        400, "elite", gw=1,
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    # K = 8, prior_strength = 8 => an even split, declared not guessed.
    assert sample.empirical_weight == pytest.approx(0.5)
    assert sample.n_observed == 8
    assert sample.observed_gw == 1
    assert sample.provenance == PROVENANCE_HYBRID
    assert sample.n_rivals == 400
    # SE is quoted on the observed squads, not on the resampled field.
    assert sample.standard_error(0.5) == pytest.approx(np.sqrt(0.25 / 8))


def test_gw_equal_to_the_observed_gw_is_not_drifted(tmp_path):
    """Deciding the gameweek that already locked needs no timing bridge."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_DECIDE), season=SEASON,
        config=HybridConfig(min_observed=4, prior_strength=0.0),
    )
    sample = sampler.sample_squads(
        100, "elite", gw=1,
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    assert sample.empirical_weight == pytest.approx(1.0)
    assert sample.provenance == PROVENANCE_EMPIRICAL
    assert sample.drift_applied is False


def test_deciding_the_next_gameweek_drifts_and_labels_it(tmp_path):
    """The timing result, end to end: GW2 is decided off GW1 squads + flow."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8, with_flow=True)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_DECIDE), season=SEASON,
        config=HybridConfig(min_observed=4, prior_strength=0.0),
    )
    sample = sampler.sample_squads(
        200, "elite",
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    assert sample.gw == 2, "as_of after the GW1 deadline decides GW2"
    assert sample.observed_gw == 1
    assert sample.drift_applied is True
    assert sample.provenance == PROVENANCE_EMPIRICAL_DRIFTED
    assert any("drifted GW1->GW2" in n for n in sample.notes)


# ---------------------------------------------------------------------------
# the as_of contract
# ---------------------------------------------------------------------------


def test_as_of_later_than_the_snapshot_is_refused(tmp_path):
    wh, universe, _ = build_warehouse(tmp_path)
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_POOL), season=SEASON
    )
    with pytest.raises(ValueError, match="later than this sampler's Snapshot"):
        sampler.sample_squads(10, "elite", as_of=T_DECIDE, gw=2)


def test_an_earlier_as_of_narrows_and_unsees_the_picks(tmp_path):
    """Backtest safety: asking as of Thursday must not see Friday's squads."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8)
    own, cap, xp = toy()[1:]
    sampler = HybridFieldSampler(
        universe, snapshot=wh.snapshot_at(T_DECIDE), season=SEASON,
        config=HybridConfig(min_observed=4, prior_strength=0.0),
    )
    kwargs = dict(
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    late = sampler.sample_squads(50, "elite", **kwargs)
    early = sampler.sample_squads(
        50, "elite", as_of=GW1_DEADLINE - dt.timedelta(hours=1), **kwargs
    )
    assert late.n_observed == 8
    assert early.n_observed == 0
    assert early.provenance == PROVENANCE_MARGINALS_PRIOR
    assert early.gw == 1


# ---------------------------------------------------------------------------
# cohort statistics
# ---------------------------------------------------------------------------


def test_cohort_rates_are_measurements_with_sample_sizes(tmp_path):
    wh, universe, meta = build_warehouse(tmp_path, n_managers=8)
    rates = measure_cohort(
        wh.snapshot_at(T_DECIDE), SEASON, universe, "elite"
    )
    assert rates.measured
    assert rates.n_managers == 8
    assert rates.provenance == "measured:elite:gw1:n8"
    # Every fixture squad captains slot 5 and vices slot 9, so captaincy is a
    # measurement of the fixture rather than an average of a guess.
    assert rates.captain_share.sum() == pytest.approx(1.0)
    assert rates.start_share.sum() == pytest.approx(11.0)
    assert rates.ownership.sum() == pytest.approx(15.0)
    assert (rates.ownership + 1e-12 >= rates.start_share).all()
    # Two of eight managers played a chip, one each.
    assert rates.chip_rates == {"3xc": pytest.approx(0.125),
                                "wildcard": pytest.approx(0.125)}
    assert rates.standard_error(0.5) == pytest.approx(np.sqrt(0.25 / 8))
    # EO is derived, and exceeds start share wherever the armband lands.
    eo = rates.eo()
    assert (eo + 1e-12 >= rates.start_share).all()
    assert eo.sum() == pytest.approx(11.0 + 1.0 + 0.125)


def test_an_uncrawled_cohort_is_labelled_unmeasured_not_zero(tmp_path):
    """Absence of a measurement must not arrive dressed as a measurement of 0."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8, top1k_manager=None)
    rates = measure_cohort(wh.snapshot_at(T_DECIDE), SEASON, universe, "top1k")
    assert not rates.measured
    assert rates.n_managers == 0
    assert rates.provenance.startswith("unmeasured")
    assert rates.ownership is None and rates.captain_share is None
    assert rates.chip_rates == {}
    with pytest.raises(ValueError, match="no sampling error"):
        rates.standard_error(0.5)
    with pytest.raises(ValueError, match="unmeasured cohort"):
        rates.eo()


def test_the_top1k_cohort_is_selected_on_the_sampler_source_prefix(tmp_path):
    """Cohort membership is a fact about how the row got there, not a guess."""
    wh, universe, _ = build_warehouse(tmp_path, n_managers=8, top1k_manager=2)
    snap = wh.snapshot_at(T_DECIDE)
    top1k, _ = load_observed_squads(snap, SEASON, universe, "top1k")
    elite, _ = load_observed_squads(snap, SEASON, universe, "elite")
    assert top1k is not None and top1k.n == 1
    assert elite is not None and elite.n == 7
    assert set(top1k.entry_ids.tolist()).isdisjoint(elite.entry_ids.tolist())


# ---------------------------------------------------------------------------
# the contract the simulator and the rank solver consume
# ---------------------------------------------------------------------------


def test_the_sample_is_scored_by_the_existing_simulator_path(tmp_path):
    """No translation layer: FieldSample.squads IS a FieldSquads."""
    universe, own, cap, xp = toy()
    sampler = HybridFieldSampler(universe)
    sample = sampler.sample_squads(
        300, "overall", gw=1,
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    assert isinstance(sample, FieldSample)
    rng = np.random.default_rng(5)
    points = rng.integers(0, 12, size=(universe.n_players, 64)).astype(np.float32)
    minutes = np.full((universe.n_players, 64), 90.0)
    scores = FieldModel(universe, FieldConfig(n_rivals=300)).score(
        sample.squads, points, minutes
    )
    assert scores.shape == (300, 64)
    assert np.isfinite(scores).all()


def test_share_table_matches_the_solver_column_contract(tmp_path):
    from fpl_edge.rank.coefficients import SHARE_COLUMNS

    universe, own, cap, xp = toy()
    sampler = HybridFieldSampler(universe)
    sample = sampler.sample_squads(
        400, "overall", gw=3,
        ownership=InclusionProbability(own, cohort="overall", provenance="f"),
        captaincy=cap, expected_points=xp,
    )
    table = sampler.share_table(sample)
    assert set(SHARE_COLUMNS) <= set(table.columns)
    assert len(table) == universe.n_players
    assert (table["gw"] == 3).all()
    assert table["own_share"].between(0.0, 1.0).all()
    assert table["captain_share"].between(0.0, 1.0).all()
    assert table["own_share"].sum() == pytest.approx(15.0)
    assert table["captain_share"].sum() == pytest.approx(1.0)
    assert table.attrs["provenance"] == PROVENANCE_MARGINALS_PRIOR

    # own_share is inclusion, NOT effective ownership: it must never exceed the
    # EO of the same player, and the two must not be the same vector.
    eo = sampler.effective_ownership(sample)
    inc = sampler.inclusion_probability(sample)
    assert eo.multiplier.sum() == pytest.approx(12.0)
    assert inc.p_in_squad.sum() == pytest.approx(15.0)
    assert not np.allclose(eo.multiplier, table["own_share"].to_numpy())


def test_hybrid_provenance_labels_are_the_ones_the_solver_ranks():
    """The solver orders provenance strings; a typo here silently downgrades."""
    from fpl_edge.rank.coefficients import PROVENANCE_OWNERSHIP_MARGINALS

    assert PROVENANCE_OWNERSHIP_MARGINALS == PROVENANCE_MARGINALS_PRIOR
    assert PROVENANCE_HYBRID == "hybrid(empirical+marginals)"
    assert PROVENANCE_HYBRID_DRIFTED == "hybrid(empirical+marginals)+flow_drift"
    assert PROVENANCE_EMPIRICAL == "empirical_picks"
    assert PROVENANCE_EMPIRICAL_DRIFTED == "empirical_picks+flow_drift"
