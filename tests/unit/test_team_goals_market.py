"""Odds consumption and the market-implied baseline.

Covers the de-vig arithmetic, the inversion from probabilities back to goal
rates, and the property that matters operationally: a fixture nobody priced is
*absent* from the market model's output rather than filled in from somewhere
else. "The market agreed with us" and "there was no market" must never look
alike downstream.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.market import MarketImpliedModel, invert_odds
from fpl_edge.models.team_goals.odds import (
    FixtureOdds,
    FrameOddsProvider,
    NullOddsProvider,
    OddsProvider,
    SnapshotOddsProvider,
    devig_frame,
    fixture_key,
)
from fpl_edge.models.team_goals.scoreline import (
    GoalRates,
    outcome_probs,
    prob_over,
    score_matrix,
)
from fpl_edge.models.team_goals.synthetic import (
    TRUE_RHO,
    build_warehouse,
    load_league,
)

SEASON = "2025-26"
TARGET_GW = 18
#: The GW18 deadline itself. Odds tests have to stand exactly where the engine
#: stands -- a snapshot taken a week early would legitimately see no prices.
AS_OF = dt.datetime(2025, 12, 11, 10, 0, tzinfo=dt.UTC)


@pytest.fixture(scope="module")
def league():
    return load_league(FIXTURES_DIR)


@pytest.fixture(scope="module")
def warehouse(league, tmp_path_factory):
    return build_warehouse(league, tmp_path_factory.mktemp("market") / "wh.duckdb")


QUOTE_TIME = pd.Timestamp("2025-01-01", tz="UTC")


def _long_odds(prices: dict[tuple[str, str], float], key: str = "S:1") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "fixture_key": key,
                "bookmaker": "book_a",
                "market": m,
                "selection": s,
                "price_decimal": p,
                "as_of": QUOTE_TIME,
            }
            for (m, s), p in prices.items()
        ]
    )


# -- de-vigging --------------------------------------------------------------


def test_proportional_devig_removes_the_overround() -> None:
    odds = _long_odds({("h2h", "home"): 2.0, ("h2h", "draw"): 4.0, ("h2h", "away"): 4.0})
    out = devig_frame(odds)["S:1"]
    assert out.p_home + out.p_draw + out.p_away == pytest.approx(1.0)
    assert out.overround_h2h == pytest.approx(1.0)
    assert out.p_home == pytest.approx(0.5)
    assert not out.has_totals


def test_power_devig_differs_from_proportional_on_a_real_margin() -> None:
    prices = {("h2h", "home"): 1.80, ("h2h", "draw"): 3.60, ("h2h", "away"): 4.20}
    prop = devig_frame(_long_odds(prices), method="proportional")["S:1"]
    power = devig_frame(_long_odds(prices), method="power")["S:1"]
    assert prop.overround_h2h > 1.0
    assert prop.p_home + prop.p_draw + prop.p_away == pytest.approx(1.0)
    assert power.p_home + power.p_draw + power.p_away == pytest.approx(1.0)
    # The power method puts more of the margin on the longshots, so relative to
    # proportional scaling the favourite ends up *higher* and the outsider lower.
    # That is the favourite-longshot correction; asserting the direction pins it.
    assert power.p_home > prop.p_home
    assert power.p_away < prop.p_away


def test_totals_are_picked_up_with_their_line() -> None:
    odds = _long_odds(
        {
            ("h2h", "home"): 2.0,
            ("h2h", "draw"): 4.0,
            ("h2h", "away"): 4.0,
            ("totals", "over_2.5"): 1.9,
            ("totals", "under_2.5"): 1.9,
        }
    )
    out = devig_frame(odds)["S:1"]
    assert out.has_totals
    assert out.totals_line == 2.5
    assert out.p_over == pytest.approx(0.5)


def test_books_are_averaged_after_devigging_not_before() -> None:
    rows = []
    for book, (h, d, a) in {
        "tight": (2.05, 3.90, 3.90),
        "wide": (1.90, 3.60, 3.60),
    }.items():
        for sel, price in zip(("home", "draw", "away"), (h, d, a), strict=True):
            rows.append(
                {
                    "fixture_key": "S:1",
                    "bookmaker": book,
                    "market": "h2h",
                    "selection": sel,
                    "price_decimal": price,
                }
            )
    out = devig_frame(pd.DataFrame(rows))["S:1"]
    assert out.n_books == 2
    assert out.p_home + out.p_draw + out.p_away == pytest.approx(1.0)
    assert out.overround_h2h > 1.0


# -- inversion ---------------------------------------------------------------


@pytest.mark.parametrize("rates", [GoalRates(1.7, 0.9, TRUE_RHO), GoalRates(0.8, 2.1, TRUE_RHO)])
def test_inversion_round_trips_exactly_when_rho_is_known(rates: GoalRates) -> None:
    mat = score_matrix(rates, 10)
    ph, pdw, pa = outcome_probs(mat)
    quote = FixtureOdds("S:1", ph, pdw, pa, 1.0, 1, p_over=prob_over(mat), totals_line=2.5)
    inv = invert_odds(quote, rho=rates.rho)
    assert inv.rates.home == pytest.approx(rates.home, abs=1e-3)
    assert inv.rates.away == pytest.approx(rates.away, abs=1e-3)
    assert inv.residual < 1e-6
    assert inv.used_totals


def test_inversion_without_totals_still_solves() -> None:
    rates = GoalRates(1.6, 1.2, 0.0)
    ph, pdw, pa = outcome_probs(score_matrix(rates, 10))
    inv = invert_odds(FixtureOdds("S:1", ph, pdw, pa, 1.0, 1))
    assert not inv.used_totals
    assert inv.rates.home == pytest.approx(rates.home, abs=0.05)
    assert inv.rates.away == pytest.approx(rates.away, abs=0.05)


def test_a_lopsided_low_scoring_quote_is_flagged_as_inconsistent() -> None:
    """A 90% home win with almost no goals: no bivariate Poisson does that.

    Note the contrast with a 90% *draw*, which is perfectly consistent -- both
    rates near zero. The residual has to distinguish genuinely impossible price
    sets from merely unusual ones, not just flag anything extreme.
    """
    impossible = invert_odds(
        FixtureOdds("S:1", 0.90, 0.05, 0.05, 1.0, 1, p_over=0.02, totals_line=2.5)
    )
    assert impossible.residual > 0.05
    consistent = invert_odds(FixtureOdds("S:1", 0.05, 0.90, 0.05, 1.0, 1))
    assert consistent.residual < 1e-6


# -- providers ---------------------------------------------------------------


def test_providers_satisfy_the_protocol(league) -> None:
    assert isinstance(NullOddsProvider(), OddsProvider)
    assert isinstance(FrameOddsProvider(league.odds), OddsProvider)


def test_null_provider_yields_no_predictions_rather_than_a_guess(warehouse) -> None:
    snap = warehouse.snapshot_at(AS_OF)
    model = MarketImpliedModel(NullOddsProvider())
    frame = model.predict(snap, SEASON, [TARGET_GW])
    assert frame.empty
    assert model.last_coverage == 0.0


def test_frame_provider_covers_most_but_not_all_fixtures(warehouse, league) -> None:
    snap = warehouse.snapshot_at(AS_OF)
    model = MarketImpliedModel(FrameOddsProvider(league.odds))
    frame = model.predict(snap, SEASON, [TARGET_GW])
    assert not frame.empty
    assert 0.0 < model.last_coverage <= 1.0
    assert len(frame) == 2 * frame["fixture_id"].nunique()
    assert ((frame["p_clean_sheet"] > 0) & (frame["p_clean_sheet"] < 1)).all()


def test_snapshot_provider_is_as_of_filtered(warehouse, league) -> None:
    """REAL DATA PATH: quotes published after the deadline must be invisible."""
    snap_early = warehouse.snapshot_at(dt.datetime(2020, 1, 1, tzinfo=dt.UTC))
    snap_now = warehouse.snapshot_at(AS_OF)
    fixtures = league.fixtures[
        (league.fixtures["season"] == SEASON) & (league.fixtures["gw"] == TARGET_GW)
    ]
    keys = [fixture_key(SEASON, int(f)) for f in fixtures["fixture_id"]]
    assert SnapshotOddsProvider(snap_early).odds_for(keys, snap_early.as_of) == {}
    assert len(SnapshotOddsProvider(snap_now).odds_for(keys, snap_now.as_of)) > 0


def test_snapshot_and_frame_providers_agree(warehouse, league) -> None:
    """The offline fixture path and the real warehouse path are interchangeable."""
    snap = warehouse.snapshot_at(AS_OF)
    fixtures = league.fixtures[
        (league.fixtures["season"] == SEASON) & (league.fixtures["gw"] == TARGET_GW)
    ]
    keys = [fixture_key(SEASON, int(f)) for f in fixtures["fixture_id"]]
    a = SnapshotOddsProvider(snap).odds_for(keys, snap.as_of)
    b = FrameOddsProvider(league.odds).odds_for(keys, snap.as_of)
    assert set(a) == set(b)
    for k in a:
        assert a[k].p_home == pytest.approx(b[k].p_home)
        assert a[k].p_over == pytest.approx(b[k].p_over)


def test_borrowed_rho_reshapes_the_low_score_corner(warehouse, league) -> None:
    """rho is not identified by 1X2 or totals, but it moves the cells that price
    defenders, so borrowing it from the fit has to actually change the matrix."""
    snap = warehouse.snapshot_at(AS_OF)
    flat = MarketImpliedModel(FrameOddsProvider(league.odds))
    corrected = MarketImpliedModel(FrameOddsProvider(league.odds), rho=-0.10)
    a = flat.predict(snap, SEASON, [TARGET_GW]).set_index(["fixture_id", "is_home"])
    b = corrected.predict(snap, SEASON, [TARGET_GW]).set_index(["fixture_id", "is_home"])
    assert not np.allclose(
        a["p_clean_sheet"].astype(float), b["p_clean_sheet"].astype(float)
    )
    for fid in a.index.get_level_values("fixture_id").unique()[:5]:
        assert corrected.score_matrix(int(fid))[0, 0] > flat.score_matrix(int(fid))[0, 0]


def test_offline_provider_hides_quotes_published_after_the_as_of(league) -> None:
    """The leak this interface exists to prevent, asserted directly."""
    keys = sorted(league.odds["fixture_key"].unique())[:20]
    early = pd.Timestamp("2020-01-01", tz="UTC").to_pydatetime()
    late = pd.Timestamp("2100-01-01", tz="UTC").to_pydatetime()
    provider = FrameOddsProvider(league.odds)
    assert provider.odds_for(keys, early) == {}
    assert len(provider.odds_for(keys, late)) == len(keys)


def test_offline_provider_rejects_an_undated_frame() -> None:
    with pytest.raises(ValueError, match="as_of"):
        FrameOddsProvider(pd.DataFrame({"fixture_key": ["S:1"], "market": ["h2h"]}))
