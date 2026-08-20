"""The F1 validator: paired deltas, and honesty about what it cannot resolve.

Driven by a stub simulator with known rank distributions, so the arithmetic is
checkable without a warehouse and without a 4,000-simulation run. The stub still
respects the property that matters -- every plan is scored on the SAME draws --
because that is what makes the standard errors paired.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.rank.validate import (
    PlanDelta,
    render,
    squad_plan_from_horizon,
    validate_plans,
)
from fpl_edge.sim.rank import RankDistribution


class StubSimulator:
    """Returns a prepared RankDistribution per plan, keyed by identity order."""

    def __init__(self, distributions):
        self._dists = list(distributions)
        self._seen = 0
        self.labels: list[str] = []

    def evaluate(self, plan, *, label=None):
        d = self._dists[self._seen]
        self._seen += 1
        self.labels.append(label)
        return RankDistribution(
            ranks=d.ranks,
            my_scores=d.my_scores,
            field_mean_score=d.field_mean_score,
            field_size=d.field_size,
            label=label or "",
        )


def distribution(ranks: np.ndarray, scores: np.ndarray, field_size: int = 5_896_644):
    return RankDistribution(
        ranks=ranks.astype(np.float64),
        my_scores=scores.astype(np.float64),
        field_mean_score=np.zeros_like(scores, dtype=np.float64),
        field_size=field_size,
    )


def paired_pair(n: int = 20_000, *, seed: int = 5, lift: float = 0.0):
    """Two plans on common draws: plan B beats plan A by ``lift`` in P(top 10k).

    The shared uniform is the common random number. A plan's rank is a
    deterministic function of it, so the two plans co-move exactly the way two
    squads scored on identical point draws would.
    """
    rng = np.random.default_rng(seed)
    u = rng.random(n)
    a = np.where(u < 0.20, 5_000.0, 50_000.0)
    b = np.where(u < 0.20 + lift, 5_000.0, 50_000.0)
    scores_a = 2_200.0 + 40.0 * rng.standard_normal(n)
    return distribution(a, scores_a), distribution(b, scores_a + 3.0)


def test_the_baseline_carries_no_delta_and_everything_else_is_measured_against_it():
    a, b = paired_pair(lift=0.03)
    sim = StubSimulator([a, b])
    deltas = validate_plans(sim, [object(), object()])

    assert len(deltas) == 2
    assert deltas[0].is_baseline
    assert deltas[0].delta_p_top is None
    assert not deltas[1].is_baseline
    assert deltas[0].p_top == pytest.approx(0.20, abs=0.01)
    assert deltas[1].delta_p_top == pytest.approx(0.03, abs=0.005)
    assert sim.labels == ["plan 1", "plan 2"]


def test_pairing_makes_the_standard_error_far_smaller_than_the_unpaired_one():
    """The whole reason F1 is usable as a validator at all.

    The paired SE scales with the DISAGREEMENT rate between the two plans
    (~sqrt(d/n)), while the unpaired one scales with each plan's own outcome
    rate (~sqrt(2p(1-p)/n)). So the advantage grows the more alike the plans
    are -- which is precisely the regime that matters, since the plans a solver
    is choosing between differ by one or two transfers.
    """
    ratios = []
    for lift in (0.03, 0.005):
        a, b = paired_pair(lift=lift)
        deltas = validate_plans(StubSimulator([a, b]), [object(), object()])
        paired_se = deltas[1].se_delta_p_top
        unpaired_se = (deltas[0].se_p_top**2 + deltas[1].se_p_top**2) ** 0.5
        assert paired_se is not None and paired_se < unpaired_se
        ratios.append(unpaired_se / paired_se)

    assert ratios[0] > 3.0
    assert ratios[1] > 7.0, "near-identical plans are where pairing pays most"
    assert ratios[1] > ratios[0]


def test_a_real_difference_is_reported_as_resolved():
    a, b = paired_pair(lift=0.03)
    deltas = validate_plans(StubSimulator([a, b]), [object(), object()])
    assert deltas[1].resolved
    assert "RESOLVED" in deltas[1].describe()


def test_an_unresolvable_difference_says_so_instead_of_showing_a_sign():
    """§7.4's finding, as a contract: 0 of 6 swaps separated at 2 SE.

    A validator that reported the sign of an unresolved delta would be inviting
    the reader to act on Monte Carlo noise.
    """
    a, b = paired_pair(lift=0.0001)
    deltas = validate_plans(StubSimulator([a, b]), [object(), object()])
    assert not deltas[1].resolved
    assert "unresolved at 2 SE" in deltas[1].describe()

    report = render(deltas)
    assert "0 of 1 alternatives separated" in report
    assert "That is a result, not a failure" in report
    assert "not being the argmax loop" in report


def test_identical_plans_have_exactly_zero_paired_delta():
    """Common random numbers make this exact, not approximate."""
    a, _ = paired_pair()
    deltas = validate_plans(StubSimulator([a, a]), [object(), object()])
    assert deltas[1].delta_p_top == 0.0
    assert deltas[1].se_delta_p_top == 0.0
    assert not deltas[1].resolved


def test_deep_thresholds_are_flagged_as_extrapolated():
    a, b = paired_pair(lift=0.02)
    deltas = validate_plans(StubSimulator([a, b]), [object(), object()], threshold=10_000)
    assert all(d.extrapolated for d in deltas)
    assert "Cornish-Fisher tail extrapolation is load-bearing" in render(deltas)


def test_labels_must_match_the_plans_they_name():
    a, b = paired_pair()
    with pytest.raises(ValueError, match="mislabelled"):
        validate_plans(StubSimulator([a, b]), [object(), object()], labels=["only one"])


def test_validating_nothing_is_refused():
    with pytest.raises(ValueError, match="nothing to validate"):
        validate_plans(StubSimulator([]), [])


def test_custom_labels_reach_the_simulator_and_the_report():
    a, b = paired_pair(lift=0.02)
    sim = StubSimulator([a, b])
    deltas = validate_plans(sim, [object(), object()], labels=["roll", "haaland in"])
    assert sim.labels == ["roll", "haaland in"]
    assert deltas[1].label == "haaland in"
    assert "haaland in" in render(deltas)


# ---------------------------------------------------------------------------
# The optimiser -> simulator translation
# ---------------------------------------------------------------------------


def test_a_horizon_plan_converts_to_a_simulator_plan_with_its_hits_intact():
    """A -4 the F2 objective accepted must be charged in the simulation too.

    Otherwise the validator grades a different plan from the one that was chosen.
    """
    from fpl_edge.opt.plan import GwDecision, HorizonPlan
    from fpl_edge.opt.config import ObjectiveMode
    from fpl_edge.types import Money

    class Universe:
        codes = np.arange(100, 115)

    squad = tuple(range(100, 115))
    decision = GwDecision(
        gw=1,
        squad=squad,
        fielded=squad,
        starting_xi=squad[:11],
        bench=squad[11:],
        captain=100,
        vice_captain=101,
        chip="3xc",
        transfers_in=(100,),
        transfers_out=(),
        free_transfers_available=0,
        hits=1,
        bank_after=Money(0),
        squad_value=Money(1000),
    )
    plan = HorizonPlan(
        season="2026-27",
        mode=ObjectiveMode.RANK_MV,
        decisions=(decision,),
        objective=1.0,
        solver="HiGHS",
        status="Optimal",
        solve_seconds=0.1,
    )

    converted = squad_plan_from_horizon(plan, Universe(), label="test")
    assert converted.label == "test"
    assert converted.hit_for(1) == 4
    assert converted.chips[1] == "3xc"
    assert converted.captain_multiplier(1) == 3.0
    assert converted.squad_for(1).captain == 0   # code 100 -> universe index 0
    assert converted.squad_for(1).vice == 1


def test_a_player_outside_the_simulator_universe_is_refused():
    from fpl_edge.opt.plan import GwDecision, HorizonPlan
    from fpl_edge.opt.config import ObjectiveMode
    from fpl_edge.types import Money

    class Universe:
        codes = np.arange(100, 114)  # one short

    squad = tuple(range(100, 115))
    decision = GwDecision(
        gw=1, squad=squad, fielded=squad, starting_xi=squad[:11], bench=squad[11:],
        captain=100, vice_captain=101, chip=None, transfers_in=(), transfers_out=(),
        free_transfers_available=1, hits=0, bank_after=Money(0), squad_value=Money(1000),
    )
    plan = HorizonPlan(
        season="2026-27", mode=ObjectiveMode.RANK_MV, decisions=(decision,),
        objective=1.0, solver="HiGHS", status="Optimal", solve_seconds=0.1,
    )
    with pytest.raises(ValueError, match="same snapshot"):
        squad_plan_from_horizon(plan, Universe())


def test_plan_delta_serialises_its_verdict():
    d = PlanDelta(
        label="alt", p_top=0.21, se_p_top=0.003, mean_points=2_210.0,
        delta_p_top=0.01, se_delta_p_top=0.001, delta_points=3.0, se_delta_points=0.2,
    )
    payload = d.to_dict()
    assert payload["resolved"] is True
    assert payload["delta_p_top"] == 0.01
