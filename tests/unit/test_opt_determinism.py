"""The same input must produce the same plan, byte for byte.

MIP solvers are only deterministic if you make them so. Parallel branch and
bound races workers to find incumbents, so with more than one thread the tie
broken between two equally-optimal squads depends on timing. Hence
``SolverConfig.threads = 1`` and a fixed ``seed`` by default: a decision engine
whose recommendation changes when you re-run it cannot be audited, and its
backtests cannot be reproduced.
"""

from __future__ import annotations

import json

from fpl_edge.opt import (
    ObjectiveMode,
    OptimizerConfig,
    SolverConfig,
    solve_horizon,
)
from fpl_edge.types import Position
from tests.unit.test_opt_support import synthetic_problem

SMALL = {Position.GKP: 4, Position.DEF: 9, Position.MID: 9, Position.FWD: 5}


def _fingerprint(plan) -> str:
    payload = plan.to_dict()
    payload.pop("solve_seconds")
    return json.dumps(payload, sort_keys=True)


def test_same_input_and_seed_gives_the_same_plan():
    problem = synthetic_problem(per_position=SMALL, n_gws=3)
    config = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset(),
        solver=SolverConfig(seed=7),
    )
    first = _fingerprint(solve_horizon(problem, config))
    for _ in range(2):
        assert _fingerprint(solve_horizon(problem, config)) == first


def test_determinism_holds_with_chips_in_play():
    problem = synthetic_problem(per_position=SMALL, n_gws=2, first_gw=1)
    config = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        allowed_chips=frozenset({"bboost", "3xc"}),
        solver=SolverConfig(seed=3),
    )
    a = _fingerprint(solve_horizon(problem, config))
    b = _fingerprint(solve_horizon(problem, config))
    assert a == b


def test_rebuilding_the_problem_object_does_not_change_the_answer():
    """Identical data built twice must give an identical plan."""
    config = OptimizerConfig(mode=ObjectiveMode.EXPECTED_POINTS, allowed_chips=frozenset())
    a = solve_horizon(synthetic_problem(per_position=SMALL, n_gws=2), config)
    b = solve_horizon(synthetic_problem(per_position=SMALL, n_gws=2), config)
    assert _fingerprint(a) == _fingerprint(b)


def test_default_solver_config_is_single_threaded():
    cfg = SolverConfig()
    assert cfg.threads == 1, "parallel MIP is not reproducible"
    assert cfg.seed == 0
