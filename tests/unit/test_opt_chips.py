"""Chip scheduling: availability windows, counts, and what each chip does.

The GW1 lockout is the one worth stating plainly, because it is easy to get
backwards: Wildcard and Free Hit are NOT available in GW1, but Bench Boost and
Triple Captain ARE. That comes from ``chips.windows`` in the rule registry and
is never hardcoded here.
"""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from fpl_edge.opt import (
    ChipState,
    ObjectiveMode,
    OptimizerConfig,
    Ruleset,
    SquadState,
    solve_horizon,
    validate_plan,
)
from fpl_edge.types import Money, Position
from tests.unit.test_opt_support import synthetic_problem

SMALL = {Position.GKP: 4, Position.DEF: 9, Position.MID: 9, Position.FWD: 5}


def cfg(chips=None, **kw):
    return OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(chips) if chips is not None else frozenset(),
        **kw,
    )


# -- what the registry says -------------------------------------------------


def test_registry_says_wildcard_and_freehit_are_locked_out_of_gameweek_one():
    rs = Ruleset.from_registry()
    assert not rs.chip_available_in_gw("wildcard", 1)
    assert not rs.chip_available_in_gw("freehit", 1)
    assert rs.chip_available_in_gw("bboost", 1)
    assert rs.chip_available_in_gw("3xc", 1)
    assert rs.chip_available_in_gw("wildcard", 2)
    assert rs.chip_count_each == 2
    assert rs.freehit_not_consecutive


def test_halves_are_separate_windows():
    rs = Ruleset.from_registry()
    assert rs.chip_half_of("wildcard", 19) == 0
    assert rs.chip_half_of("wildcard", 20) == 1
    assert rs.chip_half_of("bboost", 1) == 0


# -- the lockout is enforced, not just recorded -----------------------------


def _squad_needing_a_rebuild(
    n_gws: int, first_gw: int, spike_at: int | None = None, better: float = 12.0
):
    """A held squad of duds, with fifteen far better players available.

    Rebuilding needs fourteen-plus transfers, which is only sane on a chip.
    ``spike_at`` makes the replacements worth having in that one gameweek only,
    which is the shape Free Hit exists for.
    """
    per_position = {Position.GKP: 4, Position.DEF: 10, Position.MID: 10, Position.FWD: 6}
    held_slots = set(range(2)) | set(range(4, 9)) | set(range(14, 19)) | set(range(24, 27))

    def xp_of(i, pos, j):
        if i in held_slots:
            return 2.0
        if spike_at is None:
            return better
        return better if j == spike_at else 0.0

    problem = synthetic_problem(
        per_position=per_position,
        n_gws=n_gws,
        first_gw=first_gw,
        n_clubs=9,
        price_of=lambda i, pos, j: 40,
        xp_of=xp_of,
    )
    holdings = {problem.players[i].code: Money(40) for i in sorted(held_slots)}
    assert len(holdings) == problem.ruleset.squad_size
    return replace(
        problem, state=SquadState(holdings=holdings, bank=Money(0), free_transfers=1)
    )


def test_no_wildcard_in_gameweek_one_even_when_it_would_obviously_help():
    # The upgrades are worth 1.5 points each per gameweek: not worth a -4 hit,
    # but very much worth a free wildcard. So the only way to rebuild is the
    # chip, and the chip is unavailable until GW2.
    problem = _squad_needing_a_rebuild(n_gws=2, first_gw=1, better=3.5)
    plan = solve_horizon(problem, cfg(["wildcard", "freehit"]))
    assert validate_plan(problem, plan) == []
    assert plan.decisions[0].chip is None, "GW1 must not be able to play a wildcard"
    assert plan.decisions[0].hits == 0
    assert len(plan.decisions[0].transfers_in) <= 1, "only the one free transfer"
    # Either rebuild chip is fine in GW2; in the last gameweek of a horizon a
    # Free Hit and a Wildcard are worth the same, because nothing after the
    # horizon is modelled to care that the squad reverts.
    assert plan.decisions[1].chip in ("wildcard", "freehit")
    rebuilt = set(plan.decisions[1].fielded) - set(plan.decisions[0].squad)
    assert len(rebuilt) >= 5, "the chip should have rebuilt most of the team"
    assert plan.decisions[1].hits == 0


def test_bench_boost_and_triple_captain_are_available_in_gameweek_one():
    problem = synthetic_problem(per_position=SMALL, n_gws=1, first_gw=1)
    plan = solve_horizon(problem, cfg(["bboost", "3xc"]))
    assert validate_plan(problem, plan) == []
    assert plan.decisions[0].chip in ("bboost", "3xc")


# -- counts and windows -----------------------------------------------------


def test_one_wildcard_per_half_of_the_season():
    problem = _squad_needing_a_rebuild(n_gws=4, first_gw=18)  # GW18..21 straddles the split
    plan = solve_horizon(problem, cfg(["wildcard"]))
    assert validate_plan(problem, plan) == []
    played = {int(d.gw): d.chip for d in plan.decisions if d.chip}
    first_half = [gw for gw in played if gw <= 19]
    second_half = [gw for gw in played if gw >= 20]
    assert len(first_half) <= 1 and len(second_half) <= 1


def test_a_chip_already_used_this_half_cannot_be_used_again():
    problem = _squad_needing_a_rebuild(n_gws=3, first_gw=5)
    used = replace(
        problem,
        state=replace(problem.state, chips=ChipState(played={"wildcard": (3,)})),
    )
    plan = solve_horizon(used, cfg(["wildcard"]))
    assert validate_plan(used, plan) == []
    assert all(d.chip is None for d in plan.decisions)


def test_only_one_chip_per_gameweek():
    problem = synthetic_problem(per_position=SMALL, n_gws=1, first_gw=1)
    plan = solve_horizon(problem, cfg(["bboost", "3xc"]))
    assert sum(1 for d in plan.decisions if d.chip) <= 1


def test_free_hit_cannot_be_played_in_consecutive_gameweeks():
    """GW19 and GW20 sit in different halves, so both Free Hits are available
    and only the consecutive-gameweek rule stops them being adjacent."""
    problem = _squad_needing_a_rebuild(n_gws=4, first_gw=18)
    plan = solve_horizon(problem, cfg(["freehit"]))
    assert validate_plan(problem, plan) == []
    fh = [int(d.gw) for d in plan.decisions if d.chip == "freehit"]
    assert all(b - a > 1 for a, b in itertools.pairwise(fh)), fh


def test_a_free_hit_last_gameweek_blocks_one_this_gameweek():
    problem = _squad_needing_a_rebuild(n_gws=2, first_gw=20)
    blocked = replace(
        problem,
        state=replace(problem.state, chips=ChipState(played={"freehit": (19,)})),
    )
    plan = solve_horizon(blocked, cfg(["freehit"]))
    assert validate_plan(blocked, plan) == []
    assert plan.decisions[0].chip is None
    assert validate_plan(blocked, plan) == []


def test_chips_outside_the_allowed_set_are_never_scheduled():
    problem = _squad_needing_a_rebuild(n_gws=3, first_gw=2)
    plan = solve_horizon(problem, cfg([]))
    assert all(d.chip is None for d in plan.decisions)


# -- what the chips actually do --------------------------------------------


def test_bench_boost_pays_the_whole_bench():
    from fpl_edge.opt import gw_contributions

    config = cfg(["bboost"])
    problem = synthetic_problem(per_position=SMALL, n_gws=1, first_gw=1)
    plan = solve_horizon(problem, config)
    d = plan.decisions[0]
    assert d.chip == "bboost"
    idx = problem.index_of
    bench_full = sum(float(problem.xpts[idx[c], 0]) for c in d.bench)
    xi = sum(float(problem.xpts[idx[c], 0]) for c in d.starting_xi)
    captain = float(problem.xpts[idx[d.captain], 0])
    vice = (1.0 - float(problem.p_play[idx[d.captain], 0])) * float(
        problem.xpts[idx[d.vice_captain], 0]
    )
    total = gw_contributions(problem, plan, config)[0][1]
    assert total == pytest.approx(xi + captain + vice + bench_full, abs=1e-6)


def test_triple_captain_triples_rather_than_doubles():
    config = cfg(["3xc"])
    problem = synthetic_problem(per_position=SMALL, n_gws=1, first_gw=1, p_play=1.0)
    plan = solve_horizon(problem, config)
    d = plan.decisions[0]
    assert d.chip == "3xc"
    idx = problem.index_of
    xi = sum(float(problem.xpts[idx[c], 0]) for c in d.starting_xi)
    captain = float(problem.xpts[idx[d.captain], 0])
    bench = config.autosubs.gk * float(problem.xpts[idx[d.bench[0]], 0]) + sum(
        w * float(problem.xpts[idx[c], 0])
        for w, c in zip(config.autosubs.outfield, d.bench[1:])
    )
    assert plan.objective == pytest.approx(xi + 2 * captain + bench, abs=1e-6)


def test_free_hit_fields_a_one_week_squad_and_reverts():
    # The replacements are only worth having in the middle gameweek.
    problem = _squad_needing_a_rebuild(n_gws=3, first_gw=2, spike_at=1)
    plan = solve_horizon(problem, cfg(["freehit"]))
    assert validate_plan(problem, plan) == []
    middle = plan.decisions[1]
    assert middle.chip == "freehit"
    # The fielded fifteen is not the owned fifteen ...
    assert set(middle.fielded) != set(middle.squad)
    # ... the owned squad is untouched through the Free Hit ...
    assert set(middle.squad) == set(plan.decisions[0].squad)
    assert set(plan.decisions[2].squad) == set(plan.decisions[0].squad)
    # ... and no persistent transfers were recorded or charged.
    assert middle.transfers_in == () and middle.transfers_out == ()
    assert middle.hits == 0
    # Free transfers are retained across the chip.
    assert plan.decisions[2].free_transfers_available >= middle.free_transfers_available


def test_wildcard_makes_transfers_free_but_keeps_the_new_squad():
    problem = _squad_needing_a_rebuild(n_gws=3, first_gw=2)
    plan = solve_horizon(problem, cfg(["wildcard"]))
    assert validate_plan(problem, plan) == []
    wc = next(d for d in plan.decisions if d.chip == "wildcard")
    assert len(wc.transfers_in) > 5
    assert wc.hits == 0
    assert set(wc.squad) == set(wc.fielded)
    after = plan.decisions[[int(d.gw) for d in plan.decisions].index(int(wc.gw)) + 1 :]
    for d in after:
        assert len(d.transfers_in) <= d.free_transfers_available or d.hits > 0
