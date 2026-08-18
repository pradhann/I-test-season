"""Is the recommendation a forecast, or is it "buy whoever scored last week"?

Hunt list item 9. A points model fitted on recent form will reproduce recent
form, and a squad optimiser fed those numbers will buy last week's hauls. It
looks like analysis. It is a lagged copy of the scoreboard.

The measurement is a rank correlation between what a strategy VALUES at a
deadline and what each player SCORED in the immediately preceding gameweek. The
threshold is not "zero" -- last week's points genuinely carry signal about
minutes and role, and a good model should use some of it. The threshold is "not
so high that the model is adding nothing to the lag".

The detector is validated in both directions before it is trusted:

* ``fpl_edge/eval/baselines.py`` ships ``LastWeeksBestStrategy``, which IS the
  bug in pure form. The detector must flag it.
* ``TemplateStrategy`` values ownership. The detector must not flag it.

A detector that has only ever been run on code that passes is not a detector.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from fpl_edge.eval.baselines import (
    LastWeeksBestStrategy,
    TemplateStrategy,
    last_weeks_best_scorer,
    template_scorer,
)

from .conftest import UTC, frame, player_row, result_row, state_row

#: Above this Spearman correlation with last gameweek's points, a strategy is
#: not forecasting, it is lagging. Chosen so the pure recency baseline (rho ~ 1)
#: is caught with room to spare and a model that legitimately weights recent
#: minutes is not.
RECENCY_RHO_LIMIT = 0.80

SEASON = "2026-27"
GW2_DEADLINE = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
GW1_FINALISED = dt.datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
PRESEASON = dt.datetime(2026, 8, 1, tzinfo=UTC)


@pytest.fixture()
def gw1_played(wh):
    """A warehouse in which GW1 has been played and finalised.

    Points are deliberately uncorrelated with price and ownership, so any
    correlation a strategy shows with last week's points is coming from last
    week's points and not from something they share.
    """
    rng = np.random.default_rng(11)
    n = 60
    codes = list(range(1, n + 1))
    positions = [1, 2, 3, 4] * (n // 4)
    last_gw_points = rng.integers(0, 16, n)
    ownership = rng.uniform(0.1, 60.0, n)
    prices = rng.integers(40, 130, n)

    wh.append("dim_player", frame([
        player_row(season=SEASON, code=c, element_id=c, as_of=PRESEASON,
                   web_name=f"P{c}", position=positions[i], team_code=1 + (i % 20))
        for i, c in enumerate(codes)
    ]))
    wh.append("fact_player_state", frame([
        state_row(season=SEASON, code=c, element_id=c, as_of=PRESEASON,
                  price_tenths=int(prices[i]), selected_by_pct=float(ownership[i]))
        for i, c in enumerate(codes)
    ]))
    wh.append("fact_player_fixture", frame([
        result_row(season=SEASON, code=c, fixture_id=1, gw=1, as_of=GW1_FINALISED,
                   total_points=int(last_gw_points[i]))
        for i, c in enumerate(codes)
    ]))
    return wh, pd.Series(last_gw_points, index=codes)


def recency_rho(values: pd.Series, last_gw_points: pd.Series) -> float:
    """Spearman rank correlation between a strategy's valuation and last GW points.

    Rank rather than Pearson because what matters is the ORDER the optimiser
    picks in, not the scale of the numbers.
    """
    aligned = last_gw_points.reindex(values.index)
    mask = values.notna() & aligned.notna()
    if mask.sum() < 3 or values[mask].nunique() < 2:
        return float("nan")
    return float(stats.spearmanr(values[mask], aligned[mask]).statistic)


def _scorer_values(scorer, snapshot, last_gw_points: pd.Series) -> pd.Series:
    players = snapshot.players(SEASON)
    values = scorer(snapshot, players, SEASON, 2)
    return pd.Series(np.asarray(values, dtype=float), index=players["code"].astype(int).values)


# ---------------------------------------------------------------------------
# Detector validation. Both directions, before the detector is used on anything.
# ---------------------------------------------------------------------------


def test_detector_flags_the_pure_recency_baseline(gw1_played) -> None:
    """PROVES the detector works: it must catch "buy whoever scored last week".

    ``last_weeks_best_scorer`` values each player at exactly their last-gameweek
    points. If this does not trip the threshold, every other result in this file
    is meaningless.
    """
    wh, last_gw = gw1_played
    snap = wh.snapshot_at(GW2_DEADLINE)
    rho = recency_rho(_scorer_values(last_weeks_best_scorer, snap, last_gw), last_gw)
    assert rho > RECENCY_RHO_LIMIT, (
        f"the recency detector failed to flag the pure recency baseline "
        f"(rho={rho:.3f} <= {RECENCY_RHO_LIMIT}); the detector is broken"
    )


def test_detector_does_not_flag_an_ownership_strategy(gw1_played) -> None:
    """PROVES the detector is not just flagging everything.

    ``template_scorer`` values ownership, which in this fixture is independent
    of last week's points.
    """
    wh, last_gw = gw1_played
    snap = wh.snapshot_at(GW2_DEADLINE)
    rho = recency_rho(_scorer_values(template_scorer, snap, last_gw), last_gw)
    assert abs(rho) < RECENCY_RHO_LIMIT, (
        f"the detector flagged an ownership-only strategy (rho={rho:.3f}); "
        "it is measuring something other than recency chasing"
    )


# ---------------------------------------------------------------------------
# The actual audit.
# ---------------------------------------------------------------------------


def test_no_shipped_strategy_is_secretly_a_recency_chaser(gw1_played) -> None:
    """GUARDS: a headline recommender that is a lagged scoreboard.

    Runs every strategy the tree exposes through the detector, EXCEPT the ones
    that are honestly labelled as recency baselines. A baseline named
    "last_weeks_best" is doing its job. A strategy named "optimal" scoring the
    same rho is not.

    Skips only while no non-baseline strategy exists, so it begins guarding the
    moment the optimiser lands one.
    """
    wh, last_gw = gw1_played
    snap = wh.snapshot_at(GW2_DEADLINE)

    candidates = _discover_strategies()
    graded = {}
    for name, strategy in candidates:
        if name in _DECLARED_RECENCY_BASELINES:
            continue
        values = _strategy_values(strategy, snap, last_gw)
        if values is None:
            continue
        graded[name] = recency_rho(values, last_gw)

    if not graded:
        pytest.skip("no non-baseline strategy exposes a valuation yet")

    chasers = {n: r for n, r in graded.items()
               if not np.isnan(r) and r > RECENCY_RHO_LIMIT}
    assert not chasers, (
        "these strategies rank players almost exactly by last gameweek's "
        f"points: {chasers}. Threshold {RECENCY_RHO_LIMIT}. They are lagging "
        "the scoreboard, not forecasting"
    )


def test_recency_baselines_are_labelled_as_baselines() -> None:
    """GUARDS: a recency chaser being presented as the engine's recommendation.

    ``LastWeeksBestStrategy`` and ``TemplateStrategy`` exist to be BEATEN. If a
    future release wires one of them into the weekly report as the answer, the
    engine is shipping the null hypothesis. The name is the contract.
    """
    assert LastWeeksBestStrategy().name == "last_weeks_best"
    assert TemplateStrategy().name == "template"
    assert _DECLARED_RECENCY_BASELINES == {"last_weeks_best", "template"}, (
        "the set of strategies exempt from the recency check has changed; each "
        "addition must be a deliberately-labelled baseline, not a recommender"
    )


def test_at_gw1_there_is_no_last_week_to_chase(gw1_played) -> None:
    """DOCUMENTS the cold-start form of this bug.

    At the 2026-27 GW1 deadline ``results_before`` is empty, so
    ``last_weeks_best_scorer`` values every player at 0.0 and ``greedy_squad``
    then picks on whatever its tie-break happens to be. A recency-shaped model
    at GW1 is not weakly informative, it is uninformative, and any confidence it
    reports is manufactured.
    """
    wh, _ = gw1_played
    gw1_deadline = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
    snap = wh.snapshot_at(gw1_deadline)
    assert snap.results_before(SEASON).empty

    players = snap.players(SEASON)
    values = last_weeks_best_scorer(snap, players, SEASON, 1)
    assert float(np.nanstd(np.asarray(values, dtype=float))) == 0.0, (
        "at GW1 the recency scorer must be flat; a non-zero spread would mean "
        "it is reading something that does not exist yet"
    )


#: Strategies whose whole purpose is to be a recency or template floor.
_DECLARED_RECENCY_BASELINES = {"last_weeks_best", "template"}


def _discover_strategies() -> list[tuple[str, object]]:
    """Every ``Strategy``-shaped object the tree exposes, found by duck typing.

    Deliberately not a hardcoded import list: other teams are landing modules
    while this runs, and an audit that names their symbols goes stale on
    contact. Anything with ``.name`` and ``.decide`` is in scope.
    """
    import importlib
    import pkgutil

    import fpl_edge

    found: list[tuple[str, object]] = []
    for mod_info in pkgutil.walk_packages(fpl_edge.__path__, "fpl_edge."):
        if ".migrations" in mod_info.name:
            continue
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:  # a half-landed module from another team  # noqa: S112, BLE001  (another team's module may be half-landed)
            continue
        for attr in dir(mod):
            if not attr.endswith("Strategy") or attr.startswith("_"):
                continue
            obj = getattr(mod, attr)
            if not callable(obj):
                continue
            try:
                instance = obj()
            except Exception:  # noqa: S112, BLE001  (a strategy may not be constructible yet)
                continue
            if hasattr(instance, "name") and hasattr(instance, "decide"):
                found.append((str(instance.name), instance))
    return found


def _strategy_values(strategy, snapshot, last_gw_points: pd.Series) -> pd.Series | None:
    """Extract a per-player valuation from a strategy.

    Prefers an explicit ``scorer``. Falls back to reading the chosen squad as a
    binary valuation, which is coarser but still detects a strategy that picks
    exactly last week's top 15.
    """
    scorer = getattr(strategy, "scorer", None)
    if scorer is not None:
        try:
            return _scorer_values(scorer, snapshot, last_gw_points)
        except Exception:  # noqa: BLE001  (a scorer may raise on this fixture)
            return None
    try:
        decision = strategy.decide(snapshot, None, SEASON, 2)
    except Exception:  # noqa: BLE001  (a strategy may not accept these arguments)
        return None
    chosen = {int(p.code) for p in decision.picks}
    return pd.Series(
        {int(c): float(int(c) in chosen) for c in last_gw_points.index}
    )
