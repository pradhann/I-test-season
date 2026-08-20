"""The rank layer as it reaches a recommendation.

Reuses the warehouse fixtures from :mod:`tests.unit.test_myteam_recommend` so
this file tests the wiring rather than re-deriving a universe.

What must hold end-to-end:

* asking for ``RANK_MV`` without coefficients refuses BEFORE the points model
  runs, like ``RANK_UTILITY`` does;
* the recommendation carries the state it was solved at, with provenance;
* a hit is judged against §5's ``g*``, not against 4;
* a supplied simulator produces paired ``Delta P(top 10k)``, and a broken one
  degrades to a note instead of taking the answer down.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.myteam.recommend import HitVerdict, recommend
from fpl_edge.opt import ObjectiveMode, OptimizerConfig, RankInputsUnavailableError
from fpl_edge.rank import RankState, build_rank_coefficients
from tests.unit.test_myteam_recommend import (  # noqa: F401 - fixtures
    NOW,
    SEASON,
    _forecast,
    index,
    state,
    warehouse,
)

BEHIND = RankState.stylised(deficit=-45.0, tau=12, m_weekly=0.55, s_weekly=6.0)
AHEAD = RankState.stylised(deficit=+45.0, tau=12, m_weekly=0.55, s_weekly=6.0)


def mid_season(state, gw: int = 5):
    """The same squad, mid-season, with no free transfers.

    GW1 transfers are unlimited and free, so a GW1 fixture can never produce a
    hit to judge. Moving the state to GW5 with zero banked transfers makes every
    change cost 4 points, which is the situation §5's rule is written for.
    """
    from dataclasses import replace

    from fpl_edge.types import GwId

    return replace(state, gw=GwId(gw), free_transfers=0)


def coefficients(snapshot, index, rank_state, gws=(1,), *, special=None, best=None):
    """Uniform variance and neutral shares, with optional per-player overrides.

    Uniform is the useful default: every player then carries the same variance
    credit, so a same-size swap changes squad variance by exactly zero and §5's
    ``g*`` collapses to the plain hit cost. ``special`` breaks that symmetry when
    a test needs a move that genuinely buys or sheds volatility.
    """
    from fpl_edge.opt import Ruleset, StaticPriceForecast, build_problem
    from fpl_edge.types import GwId, Season

    problem = build_problem(
        snapshot,
        Season(SEASON),
        [GwId(g) for g in gws],
        price_forecast=StaticPriceForecast(),
        points_forecast=_forecast(index, gws=gws, best=best),
        ruleset=Ruleset.from_registry(),
    )
    n, t = problem.n_players, problem.n_gws
    variance = np.full((n, t), 25.0)
    own = np.full((n, t), 0.10)
    cap = np.full((n, t), 0.05)
    row_of = {int(p.code): i for i, p in enumerate(problem.players)}
    for code, (var, own_share, cap_share) in (special or {}).items():
        i = row_of[int(code)]
        variance[i, :] = var
        own[i, :] = own_share
        cap[i, :] = cap_share
    return build_rank_coefficients(
        problem,
        rank_state,
        variance=variance,
        own_share=own,
        captain_share=cap,
        provenance="test:uniform",
    )


def no_chips(**kwargs) -> OptimizerConfig:
    """A config that cannot play a chip.

    Without this the optimiser answers a zero-free-transfer week by playing its
    wildcard, which is correct football and useless for testing a hit rule --
    a wildcard week has no hit to judge.
    """
    kwargs.setdefault("max_candidates_per_position", 40)
    return OptimizerConfig(
        mode=ObjectiveMode.RANK_MV, allowed_chips=frozenset(), **kwargs
    )


def test_rank_mv_without_coefficients_refuses_before_running_the_points_model(
    warehouse, index, state
) -> None:
    """The refusal must be cheap. Building the problem means running the model."""

    class Exploding:
        name = "must-not-run"

        def forecast(self, *a, **k):
            raise AssertionError("the points model must not run before the refusal")

    with pytest.raises(RankInputsUnavailableError) as exc:
        recommend(
            warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
            points_forecast=Exploding(), mode=ObjectiveMode.RANK_MV,
        )
    assert "collapses to expected points" in str(exc.value)


def test_the_recommendation_carries_the_state_it_was_solved_at(
    warehouse, index, state
) -> None:
    snapshot = warehouse.snapshot_at(NOW)
    rec = recommend(
        snapshot, state, season=SEASON, gws=[1],
        points_forecast=_forecast(index),
        mode=ObjectiveMode.RANK_MV,
        rank_mv=coefficients(snapshot, index, BEHIND),
        candidates=4,
    )
    assert rec.mode is ObjectiveMode.RANK_MV
    assert rec.rank_state is BEHIND
    summary = rec.rank_summary()
    assert "D=-45.0" in summary
    assert "tau=12" in summary
    assert "variance is a GOOD" in summary
    assert "stylised" in summary  # the provenance travels with the state


def test_the_state_changes_the_posture_reported(warehouse, index, state) -> None:
    snapshot = warehouse.snapshot_at(NOW)

    def summary_at(rank_state):
        return recommend(
            snapshot, state, season=SEASON, gws=[1],
            points_forecast=_forecast(index),
            mode=ObjectiveMode.RANK_MV,
            rank_mv=coefficients(snapshot, index, rank_state),
            candidates=2,
        ).rank_summary()

    assert "variance is a GOOD" in summary_at(BEHIND)
    assert "variance is a COST" in summary_at(AHEAD)


def test_without_a_rank_state_the_summary_says_so_rather_than_inventing_one(
    warehouse, index, state
) -> None:
    rec = recommend(
        warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
        points_forecast=_forecast(index), mode=ObjectiveMode.EXPECTED_POINTS,
        candidates=2,
    )
    assert rec.rank_state is None
    assert rec.hit_verdicts == ()
    assert "No rank state" in rec.rank_summary()


def hit_verdicts_at(snapshot, index, live, rank_state, *, target, special=None):
    rec = recommend(
        snapshot, live, season=SEASON, gws=[5],
        points_forecast=_forecast(index, gws=(5,), best=target),
        mode=ObjectiveMode.RANK_MV,
        rank_mv=coefficients(
            snapshot, index, rank_state, gws=(5,), special=special, best=target
        ),
        config=no_chips(),
        candidates=8,
    )
    return rec


def test_a_hit_is_judged_against_the_rank_breakeven_not_against_four(
    warehouse, index, state
) -> None:
    """The verdict must report g* and say what points logic would have demanded."""
    snapshot = warehouse.snapshot_at(NOW)
    live = mid_season(state)
    held = {int(p.code) for p in live.picks}
    target = next(c for c in sorted(index.price_now) if c not in held)

    rec = hit_verdicts_at(snapshot, index, live, BEHIND, target=target)
    assert rec.hit_verdicts, "a zero-free-transfer move must produce a hit to judge"

    verdict = rec.hit_verdicts[0]
    assert isinstance(verdict, HitVerdict)
    assert verdict.hit_points == 4 * verdict.hits
    assert "rank break-even" in verdict.describe()
    assert "Points logic would demand +4.00" in verdict.describe()
    assert verdict.justified == (verdict.expected_gain >= verdict.breakeven_gain)


def test_a_variance_neutral_hit_breaks_even_at_exactly_four_in_every_state(
    warehouse, index, state
) -> None:
    """§5's own arithmetic: when s' = s, the correction term L(S'-S)/S is zero.

    With uniform variance and uniform cohort shares, swapping one player for
    another changes squad variance against the bar by exactly nothing, so the
    rank threshold and the points threshold coincide. A rule that moved the bar
    here would be responding to the state rather than to the decision.
    """
    snapshot = warehouse.snapshot_at(NOW)
    live = mid_season(state)
    held = {int(p.code) for p in live.picks}
    target = next(c for c in sorted(index.price_now) if c not in held)

    for rank_state in (BEHIND, AHEAD):
        rec = hit_verdicts_at(snapshot, index, live, rank_state, target=target)
        assert rec.hit_verdicts
        for verdict in rec.hit_verdicts:
            assert verdict.s_weekly_after == pytest.approx(rank_state.s_weekly)
            assert verdict.breakeven_gain == pytest.approx(4.0)


def test_behind_the_engine_takes_a_hit_for_a_differential_and_prices_it_below_four(
    warehouse, index, state
) -> None:
    """§5's headline, end to end.

    The incoming player is a genuine differential -- sixteen times the variance
    at a fifth of the cohort ownership -- so buying him raises volatility
    against the bar sharply. From behind, the recommendation must (a) take the
    hit for him and (b) report a break-even *below* the four points it costs.
    """
    snapshot = warehouse.snapshot_at(NOW)
    live = mid_season(state)
    held = {int(p.code) for p in live.picks}
    target = next(c for c in sorted(index.price_now) if c not in held)
    special = {target: (400.0, 0.02, 0.01)}

    rec = hit_verdicts_at(
        snapshot, index, live, BEHIND, target=target, special=special
    )
    assert target in rec.chosen.into, "behind, the differential must be bought"
    assert rec.chosen.hits > 0, "and it must be worth a hit"

    verdict = next(v for v in rec.hit_verdicts if target in v.into)
    assert verdict.s_weekly_after > BEHIND.s_weekly, "the move must buy volatility"
    assert verdict.breakeven_gain < 4.0
    assert verdict.breakeven_gain < 0.0, (
        "this far behind, §5 says a hit that LOSES expected points is still "
        "justified -- the break-even gain is negative"
    )


def test_ahead_the_same_differential_is_not_bought_at_all(
    warehouse, index, state
) -> None:
    """The other branch of §3's law, on the identical fixture.

    Ahead of the pace theta is negative, so the differential's huge variance is
    priced as a cost rather than a credit and the move never reaches the
    shortlist. There is no hit to judge because there is no hit worth taking --
    which is the correct answer, not a missing one.
    """
    snapshot = warehouse.snapshot_at(NOW)
    live = mid_season(state)
    held = {int(p.code) for p in live.picks}
    target = next(c for c in sorted(index.price_now) if c not in held)
    special = {target: (400.0, 0.02, 0.01)}

    rec = hit_verdicts_at(
        snapshot, index, live, AHEAD, target=target, special=special
    )
    assert target not in rec.chosen.into
    assert not any(target in v.into for v in rec.hit_verdicts)


def test_a_paired_validator_attaches_deltas_to_the_alternatives(
    warehouse, index, state
) -> None:
    """F1 in its §8.2 role: baseline first, paired deltas for the rest."""
    from fpl_edge.sim.rank import RankDistribution

    snapshot = warehouse.snapshot_at(NOW)
    rng = np.random.default_rng(3)

    class StubSimulator:
        """Scores every plan on ONE shared uniform -- common random numbers."""

        universe = type("U", (), {"codes": np.array(sorted(index.price_now))})()

        def __init__(self):
            self.u = rng.random(5_000)
            self.k = 0

        def evaluate(self, plan, *, label=None):
            lift = 0.02 * self.k
            self.k += 1
            ranks = np.where(self.u < 0.15 + lift, 5_000.0, 500_000.0)
            return RankDistribution(
                ranks=ranks,
                my_scores=2_200.0 + 30.0 * self.u,
                field_mean_score=np.zeros_like(self.u),
                label=label or "",
            )

    rec = recommend(
        snapshot, state, season=SEASON, gws=[1],
        points_forecast=_forecast(index),
        mode=ObjectiveMode.RANK_MV,
        rank_mv=coefficients(snapshot, index, BEHIND),
        validator=StubSimulator(),
        candidates=4,
    )
    deltas = rec.alternatives_with_delta_p
    assert len(deltas) >= 2
    assert deltas[0].is_baseline
    assert deltas[0].delta_p_top is None
    assert all(d.delta_p_top is not None for d in deltas[1:])
    assert all(d.se_delta_p_top is not None for d in deltas[1:])


def test_a_broken_validator_degrades_to_a_note_rather_than_failing_the_answer(
    warehouse, index, state
) -> None:
    """§8.2 makes F1 a check on the F2 answer, not a precondition for it."""
    snapshot = warehouse.snapshot_at(NOW)

    class Broken:
        universe = type("U", (), {"codes": np.array([1, 2, 3])})()

        def evaluate(self, plan, *, label=None):
            raise RuntimeError("simulator exploded")

    rec = recommend(
        snapshot, state, season=SEASON, gws=[1],
        points_forecast=_forecast(index),
        mode=ObjectiveMode.RANK_MV,
        rank_mv=coefficients(snapshot, index, BEHIND),
        validator=Broken(),
        candidates=2,
    )
    assert rec.alternatives_with_delta_p == ()
    assert any("F1 paired validation unavailable" in n for n in rec.notes)
    assert rec.chosen is not None


def test_a_validator_without_a_universe_is_noted_not_guessed(
    warehouse, index, state
) -> None:
    snapshot = warehouse.snapshot_at(NOW)
    rec = recommend(
        snapshot, state, season=SEASON, gws=[1],
        points_forecast=_forecast(index),
        mode=ObjectiveMode.RANK_MV,
        rank_mv=coefficients(snapshot, index, BEHIND),
        validator=object(),
        candidates=2,
    )
    assert rec.alternatives_with_delta_p == ()
    assert any("without a .universe" in n for n in rec.notes)


def test_banked_ft_value_is_reported_when_the_term_is_on(warehouse, index, state) -> None:
    from fpl_edge.opt import FT_VALUE_LIST_SOTA

    snapshot = warehouse.snapshot_at(NOW)
    coef = coefficients(snapshot, index, BEHIND, gws=(1, 2))
    cfg = OptimizerConfig(
        mode=ObjectiveMode.RANK_MV,
        max_candidates_per_position=40,
        ft_value_list=FT_VALUE_LIST_SOTA,
    )
    rec = recommend(
        snapshot, state, season=SEASON, gws=[1, 2],
        points_forecast=_forecast(index, gws=(1, 2)),
        mode=ObjectiveMode.RANK_MV, rank_mv=coef, config=cfg, candidates=2,
    )
    assert rec.banked_ft_value != 0.0
    assert any("Banked-FT terminal value is ON" in n for n in rec.notes)


def test_the_term_is_absent_and_reported_as_zero_by_default(
    warehouse, index, state
) -> None:
    snapshot = warehouse.snapshot_at(NOW)
    rec = recommend(
        snapshot, state, season=SEASON, gws=[1],
        points_forecast=_forecast(index),
        mode=ObjectiveMode.RANK_MV,
        rank_mv=coefficients(snapshot, index, BEHIND),
        candidates=2,
    )
    assert rec.banked_ft_value == 0.0
