"""RANK_MV inside the MILP, and the SOTA adoptions around it.

The load-bearing test is
:func:`test_a_differential_must_win_behind_and_lose_ahead`: one universe, one
pair of candidates, two states. If the rank objective is wired up correctly the
*same* MILP must pick different players purely because ``(D, tau)`` changed.
That is the 9-19pp adaptivity prize (``rank_objectives.md`` §3) expressed as a
squad, and no amount of correct arithmetic elsewhere substitutes for it.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.opt import (
    FT_VALUE_LIST_SOTA,
    InfeasibleError,
    ObjectiveMode,
    OptimizerConfig,
    RankInputsUnavailableError,
    SolverConfig,
    enumerate_plans,
    score_plan,
    solve_horizon,
    validate_plan,
)
from fpl_edge.rank import RankState, build_rank_coefficients, theta
from fpl_edge.types import Position
from tests.unit.test_opt_support import synthetic_problem

# One GK slot spare, defence and attack fully determined, and SIX midfielders
# for five squad places: the whole decision collapses to "which of the last two
# midfielders do I take", which is what we want to observe.
SHAPE = {Position.GKP: 2, Position.DEF: 5, Position.MID: 6, Position.FWD: 3}

TEMPLATE_MID = 5  # index within the MID block
DIFFERENTIAL_MID = 4

#: mu, variance, cohort ownership share for the two competing midfielders.
#: The differential concedes 0.5 points of mean and buys ten times the variance
#: at a fortieth of the ownership -- the §1 archetype contrast in miniature.
TEMPLATE = {"mu": 6.0, "variance": 4.0, "share": 0.90}
DIFFERENTIAL = {"mu": 5.5, "variance": 40.0, "share": 0.02}


def candidate_problem():
    """A universe whose only free choice is template-vs-differential.

    Everything except the last midfield place is forced: two keepers, five
    defenders, three forwards, and four midfielders so good they are never
    dropped. Prices are flat and cheap so the budget never binds and cannot
    become a confounder.
    """
    mids = []

    def xp_of(i: int, pos: Position, j: int) -> float:
        if pos is not Position.MID:
            return 2.0
        k = i - (SHAPE[Position.GKP] + SHAPE[Position.DEF])
        mids.append(k)
        if k == TEMPLATE_MID:
            return TEMPLATE["mu"]
        if k == DIFFERENTIAL_MID:
            return DIFFERENTIAL["mu"]
        return 20.0  # the four undroppable midfielders

    return synthetic_problem(
        per_position=SHAPE,
        n_clubs=15,
        n_gws=1,
        price_of=lambda i, pos, j: 40,
        xp_of=xp_of,
        p_play=1.0,
    )


def mid_index(problem, k: int) -> int:
    """Row index of the k-th midfielder."""
    mids = [i for i, p in enumerate(problem.players) if p.position is Position.MID]
    return mids[k]


def coefficients_for(problem, state: RankState):
    """Rank inputs isolating the two candidates.

    Every non-candidate is given cohort share exactly 0.5, where ``(1 - 2c)``
    is zero and the variance credit vanishes identically. So their coefficients
    equal their expected points whatever theta does, and any change in the
    solved squad is attributable to the two players we are actually testing.
    """
    n, t = problem.n_players, problem.n_gws
    variance = np.full((n, t), 4.0)
    own = np.full((n, t), 0.5)
    cap = np.full((n, t), 0.5)

    for k, spec in ((TEMPLATE_MID, TEMPLATE), (DIFFERENTIAL_MID, DIFFERENTIAL)):
        i = mid_index(problem, k)
        variance[i, :] = spec["variance"]
        own[i, :] = spec["share"]
        cap[i, :] = spec["share"]

    return build_rank_coefficients(
        problem,
        state,
        variance=variance,
        own_share=own,
        captain_share=cap,
        provenance="test:constructed",
    )


def rank_config(**kwargs) -> OptimizerConfig:
    kwargs.setdefault("solver", SolverConfig(time_limit_s=60.0))
    return OptimizerConfig(
        mode=ObjectiveMode.RANK_MV, allowed_chips=frozenset(), **kwargs
    )


BEHIND = RankState.stylised(deficit=-40.0, tau=19, m_weekly=0.55, s_weekly=6.0)
AHEAD = RankState.stylised(deficit=+40.0, tau=19, m_weekly=0.55, s_weekly=6.0)


# ---------------------------------------------------------------------------
# The headline: the same MILP changes its mind with the state
# ---------------------------------------------------------------------------


def test_the_two_states_really_do_straddle_the_switch():
    """Guard the fixture: BEHIND must be a gamble state and AHEAD must not."""
    assert BEHIND.behind and theta(BEHIND) > 0.0
    assert not AHEAD.behind and theta(AHEAD) < 0.0


def test_a_differential_must_win_behind_and_lose_ahead():
    problem = candidate_problem()
    template_code = problem.players[mid_index(problem, TEMPLATE_MID)].code
    differential_code = problem.players[mid_index(problem, DIFFERENTIAL_MID)].code

    behind = solve_horizon(problem, rank_config(), rank_mv=coefficients_for(problem, BEHIND))
    ahead = solve_horizon(problem, rank_config(), rank_mv=coefficients_for(problem, AHEAD))

    assert validate_plan(problem, behind) == []
    assert validate_plan(problem, ahead) == []

    behind_squad = set(behind.decisions[0].squad)
    ahead_squad = set(ahead.decisions[0].squad)

    assert differential_code in behind_squad, (
        "behind the pace, the low-owned high-variance midfielder is the only "
        "route to the tail and must be selected"
    )
    assert template_code not in behind_squad

    assert template_code in ahead_squad, (
        "ahead of the pace, variance is a cost and the higher-mean, "
        "highly-owned midfielder must be selected"
    )
    assert differential_code not in ahead_squad


def test_expected_points_picks_the_higher_mean_in_both_states():
    """The control. Without the rank terms there is no state to respond to."""
    problem = candidate_problem()
    template_code = problem.players[mid_index(problem, TEMPLATE_MID)].code
    plain = solve_horizon(
        problem,
        OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset()),
    )
    assert template_code in set(plain.decisions[0].squad)


def test_the_rank_objective_agrees_with_the_independent_scorer():
    """The invariant the whole opt package is organised around, extended to RANK_MV."""
    problem = candidate_problem()
    coef = coefficients_for(problem, BEHIND)
    plan = solve_horizon(problem, rank_config(), rank_mv=coef)
    assert plan.objective == pytest.approx(
        score_plan(problem, plan, rank_config(), rank_mv=coef), abs=1e-6
    )


def test_the_plan_declares_the_state_and_the_approximation_it_used():
    problem = candidate_problem()
    plan = solve_horizon(problem, rank_config(), rank_mv=coefficients_for(problem, BEHIND))
    notes = " ".join(plan.notes)
    assert "theta=" in notes
    assert "test:constructed" in notes
    assert "covariance enters through Sigma inside theta" in notes
    assert "NOT as per-pair terms" in notes
    assert str(plan.mode) == "rank_mv"


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_rank_mv_without_coefficients_refuses_rather_than_returning_means():
    problem = candidate_problem()
    with pytest.raises(RankInputsUnavailableError) as exc:
        solve_horizon(problem, rank_config())
    assert "expected points reported under a rank objective" in str(exc.value)
    assert isinstance(exc.value, NotImplementedError)


def test_scoring_rank_mv_without_coefficients_refuses_too():
    problem = candidate_problem()
    coef = coefficients_for(problem, BEHIND)
    plan = solve_horizon(problem, rank_config(), rank_mv=coef)
    with pytest.raises(RankInputsUnavailableError):
        score_plan(problem, plan, rank_config())


def test_coefficients_from_a_different_universe_are_refused():
    """A misaligned coefficient is a wrong answer, not a warning."""
    problem = candidate_problem()
    other = synthetic_problem(per_position=SHAPE, n_gws=1, first_gw=7)
    coef = coefficients_for(other, BEHIND)
    with pytest.raises(ValueError, match="no gameweek"):
        solve_horizon(problem, rank_config(), rank_mv=coef)


def test_a_no_incumbent_time_limit_is_still_raised_under_rank_mv():
    """NoIncumbentError must survive the new objective, not be masked by it.

    A one-millisecond budget cannot find a feasible 15, and the contract is a
    loud failure rather than the vacuous all-zero "solution" PuLP leaves behind.
    """
    from fpl_edge.opt import NoIncumbentError

    problem = candidate_problem()
    coef = coefficients_for(problem, BEHIND)
    config = rank_config(solver=SolverConfig(time_limit_s=1e-6))
    try:
        plan = solve_horizon(problem, config, rank_mv=coef)
    except NoIncumbentError:
        return
    # If it did solve that fast, it must still be a legal plan -- never an empty one.
    assert validate_plan(problem, plan) == []
    assert len(plan.decisions[0].squad) == problem.ruleset.squad_size


# ---------------------------------------------------------------------------
# SOTA adoption: telescoping banked-FT value
# ---------------------------------------------------------------------------


def test_ft_state_values_telescope_from_the_community_table():
    """V(s) = V(s-1) + ft_value_list[s], normalised at the minimum state."""
    cfg = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, ft_value_list=FT_VALUE_LIST_SOTA)
    values = cfg.ft_state_values(1, 5)
    assert values == pytest.approx({1: 0.0, 2: 2.0, 3: 3.6, 4: 4.9, 5: 6.0})

    increments = [values[s + 1] - values[s] for s in range(1, 5)]
    assert increments == pytest.approx([2.0, 1.6, 1.3, 1.1])
    assert increments == sorted(increments, reverse=True), (
        "the MILP prices V with a concave piecewise-linear envelope, which is "
        "exact at integers only if the marginal values are non-increasing"
    )


def test_a_non_concave_ft_table_is_refused_rather_than_approximated():
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS, ft_value_list={2: 1.0, 3: 5.0}
    )
    with pytest.raises(ValueError, match="concave"):
        cfg.ft_state_values(1, 5)


def test_the_ft_term_is_off_by_default():
    """An unrecalibrated constant may be offered, never defaulted into the objective."""
    cfg = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS)
    assert cfg.ft_value_list is None
    with pytest.raises(ValueError, match="the banked-FT term is off"):
        cfg.ft_state_values(1, 5)


def test_banked_ft_value_reaches_the_objective_and_the_scorer_agrees():
    """With the term on, the MILP's objective must still equal the replay."""
    problem = synthetic_problem(per_position=SHAPE, n_gws=3)
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        ft_value_list=FT_VALUE_LIST_SOTA,
        solver=SolverConfig(time_limit_s=120.0),
    )
    plan, stats = solve_horizon(problem, cfg, return_stats=True)
    assert validate_plan(problem, plan) == []
    assert plan.objective == pytest.approx(score_plan(problem, plan, cfg), abs=1e-6)
    assert any("Banked-FT terminal value is ON" in n for n in plan.notes)


def test_valuing_banked_transfers_stops_the_horizon_end_fire_sale():
    """Without the potential, leftover FTs are worth zero and get spent.

    The truncation artefact SOTA §6.1 names. Turning the term on must weakly
    reduce the transfers made across the horizon, because the last modelled
    gameweek no longer treats a banked transfer as worthless.
    """
    problem = synthetic_problem(per_position=SHAPE, n_gws=3)
    base = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        solver=SolverConfig(time_limit_s=120.0),
    )
    valued = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        ft_value_list=FT_VALUE_LIST_SOTA,
        solver=SolverConfig(time_limit_s=120.0),
    )
    n_base = sum(d.n_transfers for d in solve_horizon(problem, base).decisions)
    n_valued = sum(d.n_transfers for d in solve_horizon(problem, valued).decisions)
    assert n_valued <= n_base


# ---------------------------------------------------------------------------
# SOTA adoption: geometric decay and report re-scoring
# ---------------------------------------------------------------------------


def test_decay_base_one_is_off_and_matches_no_discount_exactly():
    cfg = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS)
    assert cfg.discount_for(5) == (1.0, 1.0, 1.0, 1.0, 1.0)


def test_decay_base_produces_the_geometric_series():
    cfg = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, decay_base=0.9)
    assert cfg.discount_for(4) == pytest.approx((1.0, 0.9, 0.81, 0.729))


def test_an_explicit_gw_discount_still_wins_over_decay_base():
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS, decay_base=0.5, gw_discount=(1.0, 1.0)
    )
    assert cfg.discount_for(2) == (1.0, 1.0)


def test_a_decay_base_above_one_is_refused():
    """Their report_decay_base includes 1.017; we will report it, never optimise it."""
    with pytest.raises(ValueError, match="least trustworthy"):
        OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, decay_base=1.017)


def test_report_rescoring_shows_horizon_sensitivity():
    from fpl_edge.opt import decay_metrics

    problem = synthetic_problem(per_position=SHAPE, n_gws=3)
    cfg = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset())
    plan = solve_horizon(problem, cfg)
    metrics = decay_metrics(problem, plan, cfg)
    assert set(metrics) == {0.85, 1.0}
    assert metrics[1.0] == pytest.approx(score_plan(problem, plan, cfg))
    assert metrics[0.85] < metrics[1.0], "discounting later gameweeks lowers the total"


# ---------------------------------------------------------------------------
# SOTA adoption: locked / banned
# ---------------------------------------------------------------------------


def test_a_locked_player_is_owned_in_every_gameweek():
    problem = synthetic_problem(per_position=SHAPE, n_gws=2)
    worst = min(
        (i for i, p in enumerate(problem.players) if p.position is Position.MID),
        key=lambda i: problem.xpts[i].sum(),
    )
    code = problem.players[worst].code
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        locked=frozenset({int(code)}),
    )
    plan = solve_horizon(problem, cfg)
    assert validate_plan(problem, plan) == []
    for d in plan.decisions:
        assert code in d.squad


def test_a_banned_player_is_never_owned():
    problem = synthetic_problem(per_position=SHAPE, n_gws=2)
    best = max(
        (i for i, p in enumerate(problem.players) if p.position is Position.MID),
        key=lambda i: problem.xpts[i].sum(),
    )
    code = problem.players[best].code
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        banned=frozenset({int(code)}),
    )
    plan = solve_horizon(problem, cfg)
    assert validate_plan(problem, plan) == []
    for d in plan.decisions:
        assert code not in d.squad


def test_locking_and_banning_the_same_player_is_refused_at_config_time():
    with pytest.raises(ValueError, match="both locked and banned"):
        OptimizerConfig(
            mode=ObjectiveMode.EXPECTED_POINTS,
            locked=frozenset({1}),
            banned=frozenset({1}),
        )


def test_locked_players_survive_pruning():
    """SOTA §4: locked/banned must feed the safe-list, or the constraint vanishes."""
    problem = synthetic_problem(per_position=SHAPE, n_gws=1)
    worst = min(
        (i for i, p in enumerate(problem.players) if p.position is Position.MID),
        key=lambda i: problem.xpts[i].sum(),
    )
    code = int(problem.players[worst].code)
    pruned = problem.prune(2, protect={code})
    assert code in {int(p.code) for p in pruned.players}
    assert code not in {int(p.code) for p in problem.prune(2).players}


# ---------------------------------------------------------------------------
# SOTA adoption: chip scheduling control
# ---------------------------------------------------------------------------


def test_a_forced_chip_must_fire_inside_its_window():
    problem = synthetic_problem(per_position=SHAPE, n_gws=4, first_gw=10)
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset({"bboost"}),
        forced_chip_gws={"bboost": (11, 12)},
        solver=SolverConfig(time_limit_s=120.0),
    )
    plan = solve_horizon(problem, cfg)
    played = {int(d.gw): d.chip for d in plan.decisions if d.chip}
    assert set(played) <= {11, 12}
    assert list(played.values()) == ["bboost"]


def test_no_chip_gws_blocks_every_chip_in_those_weeks():
    problem = synthetic_problem(per_position=SHAPE, n_gws=4, first_gw=10)
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset({"bboost"}),
        forced_chip_gws={"bboost": (11, 12)},
        no_chip_gws=(11,),
        solver=SolverConfig(time_limit_s=120.0),
    )
    plan = solve_horizon(problem, cfg)
    assert [int(d.gw) for d in plan.decisions if d.chip] == [12]


def test_an_unsatisfiable_forced_chip_is_infeasible_not_ignored():
    problem = synthetic_problem(per_position=SHAPE, n_gws=2, first_gw=10)
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        forced_chip_gws={"bboost": (30,)},  # outside the horizon entirely
    )
    with pytest.raises(InfeasibleError, match="forced_chip_gws"):
        solve_horizon(problem, cfg)


def test_an_unknown_chip_name_is_refused():
    problem = synthetic_problem(per_position=SHAPE, n_gws=2)
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS, allowed_chip_gws={"tripple": (1,)}
    )
    with pytest.raises(ValueError, match="unknown chip"):
        solve_horizon(problem, cfg)


# ---------------------------------------------------------------------------
# SOTA adoption: alternative plans via no-good cuts
# ---------------------------------------------------------------------------


def test_enumerated_plans_are_distinct_and_ordered():
    problem = candidate_problem()
    coef = coefficients_for(problem, BEHIND)
    plans = enumerate_plans(problem, rank_config(), k=3, rank_mv=coef)

    assert len(plans) == 3
    squads = [tuple(sorted(int(c) for c in p.decisions[0].squad)) for p in plans]
    assert len(set(squads)) == 3, "a no-good cut that does not bind is not a cut"

    objectives = [p.objective for p in plans]
    assert objectives == sorted(objectives, reverse=True), (
        "each cut removes the incumbent, so the next optimum can only be worse"
    )
    for plan in plans:
        assert validate_plan(problem, plan) == []


def test_the_cut_is_recorded_on_the_plans_it_constrained():
    problem = candidate_problem()
    coef = coefficients_for(problem, BEHIND)
    plans = enumerate_plans(problem, rank_config(), k=2, rank_mv=coef)
    assert not any("no-good cut" in n for n in plans[0].notes)
    assert any("no-good cut #1" in n for n in plans[1].notes)


def test_a_larger_required_difference_forces_a_larger_change():
    """A universe with real slack, so three simultaneous changes are available.

    ``candidate_problem`` deliberately has one degree of freedom, which is what
    makes it a clean instrument for the state test and a useless one here.
    """
    problem = synthetic_problem(n_gws=1)
    cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        solver=SolverConfig(time_limit_s=120.0),
    )
    plans = enumerate_plans(problem, cfg, k=2, difference=3)
    first = set(plans[0].decisions[0].squad)
    second = set(plans[1].decisions[0].squad)
    assert len(first - second) >= 3


def test_an_unknown_cut_criterion_is_refused():
    problem = candidate_problem()
    with pytest.raises(ValueError, match="unknown cut criterion"):
        enumerate_plans(
            problem, rank_config(), k=2, criterion="vibes",
            rank_mv=coefficients_for(problem, BEHIND),
        )


def test_enumeration_honours_the_same_mode_guard_as_solving():
    problem = candidate_problem()
    with pytest.raises(RankInputsUnavailableError):
        enumerate_plans(problem, rank_config(), k=2)
