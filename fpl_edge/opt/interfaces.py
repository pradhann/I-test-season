"""What the optimiser consumes from other teams.

The optimiser owns no forecasts. It takes prices from a price model, points
from a points model and rank utility from the simulator, all through the
protocols below, so that none of those can be quietly reimplemented here with a
worse version.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

#: Columns a price forecast must return, one row per (code, gw).
PRICE_FORECAST_COLUMNS = ("code", "gw", "price_tenths")

#: Columns a points forecast must return, one row per (code, gw).
#: ``xpts`` is E[points] over the gameweek including any double gameweek;
#: ``p_play`` is P(at least one minute), needed for the vice-captain term.
POINTS_FORECAST_COLUMNS = ("code", "gw", "xpts", "p_play")


@runtime_checkable
class PriceForecast(Protocol):
    """Forecast selling/buying prices over the horizon.

    Prices are integer tenths of a million. Never floats: 0.1m increments plus
    a floor-rounded sell-on fee makes float pounds produce off-by-one-tenth
    budget errors, and a budget that is one tenth wrong produces a squad you
    cannot actually field.
    """

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        """Return a frame with :data:`PRICE_FORECAST_COLUMNS`."""
        ...


@runtime_checkable
class PointsForecast(Protocol):
    """Per-gameweek expected points, for the EXPECTED_POINTS surrogate mode."""

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        """Return a frame with :data:`POINTS_FORECAST_COLUMNS`."""
        ...


class RankUtilityUnavailableError(NotImplementedError):
    """Raised when RANK_UTILITY is requested but no provider was supplied.

    Deliberately a hard failure. Falling back to expected points here would be
    the exact bug this codebase is organised to prevent: the caller asked for
    the rank objective, got means, and had no way to tell.
    """


class RankInputsUnavailableError(NotImplementedError):
    """Raised when RANK_MV is requested but no rank coefficients were supplied.

    The sibling of :class:`RankUtilityUnavailableError`, and for the same
    reason. ``RANK_MV`` needs three things the optimiser does not own -- a
    :class:`~fpl_edge.rank.state.RankState`, per-player variances, and the
    near-threshold cohort's ownership and captaincy shares. Absent any of them
    the honest answer is to stop, because the available fallback (drop the rank
    terms) is precisely expected points wearing a rank objective's name.
    """


#: Columns a variance forecast must return, one row per (code, gw).
#: ``points_variance`` is Var(points) for that player in that gameweek,
#: including the double-gameweek case, on the same scale as ``xpts``.
VARIANCE_FORECAST_COLUMNS = ("code", "gw", "points_variance")


@runtime_checkable
class PointsVarianceForecast(Protocol):
    """Second moments of the per-gameweek points distribution.

    Split from :class:`PointsForecast` rather than widening it, because the
    two have genuinely different sources: a mean can come from any projection
    CSV, while an honest variance comes from the simulator's own draws (or from
    a model that emits a distribution rather than a point estimate). Keeping
    them separate makes "we have means but no variances" an expressible and
    visible state instead of a silently-zero column.
    """

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        """Return a frame with :data:`VARIANCE_FORECAST_COLUMNS`."""
        ...


@runtime_checkable
class RankUtilityProvider(Protocol):
    """Simulated rank utility, in the two forms a MILP can use.

    E[U(rank)] is not separable across players -- it depends on the joint
    distribution of your total against the field's, so it cannot be written as
    a sum of per-player coefficients and dropped into an objective. The
    contract therefore has two halves:

    * :meth:`linear_coefficients` returns a *local linearisation* around an
      incumbent plan: the marginal change in E[U(rank)] from adding one unit of
      each player to the starting XI, holding everything else fixed. That is a
      first-order surrogate the MILP can optimise.
    * :meth:`evaluate_plan` returns the true simulated E[U(rank)] for a
      completed plan.

    The intended use is a small trust-region loop: linearise, solve, evaluate,
    re-linearise at the new plan, stop when :meth:`evaluate_plan` stops
    improving. The MILP objective is then a surrogate whose *reported* value is
    always the honest simulated one from :meth:`evaluate_plan`, never the
    linearisation.
    """

    def linear_coefficients(
        self,
        codes: np.ndarray,
        gws: list[GwId],
        *,
        incumbent: object | None = None,
    ) -> np.ndarray:
        """(n_players, n_gws) marginal rank utility of starting each player.

        ``incumbent`` is the plan to linearise around, or None for the field's
        template squad.
        """
        ...

    def evaluate_plan(self, plan: object) -> float:
        """True simulated E[U(rank)] for a completed plan. No linearisation."""
        ...


class StaticPriceForecast:
    """The null price forecast: today's price, held flat across the horizon.

    Not a model. It exists so the optimiser has something to consume before the
    price team ships, and so that "we did not forecast prices" is a visible
    object in the plan rather than an implicit assumption.
    """

    name = "static"

    def __init__(self, prices_tenths: dict[int, int] | None = None) -> None:
        self._prices = prices_tenths

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        if self._prices is None:
            players = snapshot.players(str(season))
            base = dict(zip(players["code"].astype(int), players["price_tenths"].astype(int)))
        else:
            base = self._prices
        rows = [
            {"code": int(code), "gw": int(gw), "price_tenths": int(px)}
            for code, px in sorted(base.items())
            for gw in gws
        ]
        return pd.DataFrame(rows, columns=list(PRICE_FORECAST_COLUMNS))


class TablePriceForecast:
    """A price forecast read from a committed table.

    Used by the tests so the optimiser's behaviour under rising and falling
    prices is pinned without depending on the price team's model.
    """

    name = "table"

    def __init__(self, frame: pd.DataFrame) -> None:
        missing = set(PRICE_FORECAST_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"price table missing columns {sorted(missing)}")
        if frame["price_tenths"].dtype.kind not in "iu":
            raise TypeError("price_tenths must be an integer number of tenths, not a float")
        self._frame = frame

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        want = {int(g) for g in gws}
        out = self._frame[self._frame["gw"].astype(int).isin(want)]
        return out[list(PRICE_FORECAST_COLUMNS)].reset_index(drop=True)
