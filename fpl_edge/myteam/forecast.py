"""The seam between the points model and the optimiser.

:mod:`fpl_edge.opt` consumes a :class:`~fpl_edge.opt.interfaces.PointsForecast`:
one row per (code, gw) carrying ``xpts`` and ``p_play``. The points model in
:mod:`fpl_edge.models.points` exposes a different, richer thing -- ``simulate``,
returning a :class:`~fpl_edge.models.contracts.PointsSample` of correlated draws
for a single gameweek. Neither is wrong; they are the joint distribution and the
marginal summary the MILP can actually use.

This module is the adapter, and it is deliberately thin and deliberately visible.
Two properties matter:

* It **collapses the joint distribution to means**, which is exactly the
  information loss that :class:`~fpl_edge.opt.config.ObjectiveMode` exists to
  make explicit. An ``xpts`` column cannot express that two Arsenal defenders
  keep the same clean sheet. So this adapter is only ever legitimate under
  ``EXPECTED_POINTS``; under ``RANK_UTILITY`` the optimiser needs the simulator's
  ``RankUtilityProvider`` instead, and refuses to run without one.
* It **does not invent p_play**. ``p_play`` is P(at least one minute), which the
  sample carries in its ``minutes`` array. If a points model returns a sample
  with no minutes, this adapter raises rather than defaulting to 1.0 -- a
  vice-captain term computed against an assumed-certain captain is a silent
  mispricing of exactly the risk it exists to hedge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from fpl_edge.opt.interfaces import POINTS_FORECAST_COLUMNS
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season


class PointsForecastUnavailableError(NotImplementedError):
    """No points forecast was supplied, and none may be conjured.

    A hard failure on purpose. Every alternative -- last season's totals, FPL's
    own ``ep_next``, a flat prior -- is a model, and quietly substituting one
    would make the transfer recommendation the output of something nobody chose
    and nobody evaluated.
    """


@runtime_checkable
class SimulatingPointsModel(Protocol):
    """What :class:`~fpl_edge.models.points.model.DecomposedPointsModel` offers."""

    def simulate(
        self, snapshot: Snapshot, season: Season, gw: GwId, *, n_sims: int = ..., seed: int = ...
    ): ...


@dataclass
class SampledPointsForecast:
    """A :class:`PointsForecast` built from a simulating points model.

    One ``simulate`` call per gameweek in the horizon, because the points model
    is per-gameweek by construction (it draws a scoreline for each fixture).
    ``seed`` is offset by the gameweek so two gameweeks are not the same draws,
    and is fixed so the recommendation is reproducible: the same command on
    Tuesday and Thursday must give the same answer or the tool is not a decision
    aid, it is a mood ring.
    """

    model: SimulatingPointsModel
    n_sims: int = 5_000
    seed: int = 0
    name: str = "sampled"
    #: Filled in as gameweeks are simulated, so a caller can report the spread
    #: rather than only the mean it handed the MILP.
    samples: dict[int, object] = field(default_factory=dict)

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        rows: list[pd.DataFrame] = []
        for gw in gws:
            sample = self.model.simulate(
                snapshot, season, GwId(int(gw)), n_sims=self.n_sims, seed=self.seed + int(gw)
            )
            self.samples[int(gw)] = sample
            minutes = getattr(sample, "minutes", None)
            if minutes is None:
                raise PointsForecastUnavailableError(
                    f"{type(self.model).__name__}.simulate returned no minutes array "
                    f"for GW{int(gw)}, so P(plays) is unknown. The optimiser needs it "
                    "for the vice-captain term, and defaulting it to 1.0 would price "
                    "the captaincy as if the captain always starts."
                )
            rows.append(
                pd.DataFrame(
                    {
                        "code": np.asarray(sample.codes, dtype=np.int64),
                        "gw": int(gw),
                        "xpts": np.asarray(sample.points, dtype=np.float64).mean(axis=1),
                        "p_play": (np.asarray(minutes) > 0).mean(axis=1),
                    }
                )
            )
        frame = pd.concat(rows, ignore_index=True)[list(POINTS_FORECAST_COLUMNS)]
        return complete_universe(frame, snapshot, season, gws)


@dataclass(frozen=True)
class TablePointsForecast:
    """A points forecast read from a committed table.

    Used by the tests, and by ``fpl myteam transfers --xpts FILE``, so the
    recommendation machinery can be exercised without a five-minute model fit.
    The provenance is visible in the plan's notes: a recommendation made off a
    table says so.
    """

    frame: pd.DataFrame
    name: str = "table"

    def __post_init__(self) -> None:
        missing = set(POINTS_FORECAST_COLUMNS) - set(self.frame.columns)
        if missing:
            raise ValueError(f"points table missing columns {sorted(missing)}")

    def forecast(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        want = {int(g) for g in gws}
        out = self.frame[self.frame["gw"].astype(int).isin(want)]
        if out.empty:
            raise PointsForecastUnavailableError(
                f"the points table covers {sorted(set(self.frame['gw'].astype(int)))}, "
                f"not {sorted(want)}"
            )
        return complete_universe(
            out[list(POINTS_FORECAST_COLUMNS)].reset_index(drop=True), snapshot, season, gws
        )


def complete_universe(
    frame: pd.DataFrame, snapshot: Snapshot, season: Season, gws: list[GwId]
) -> pd.DataFrame:
    """Extend a forecast to every player the optimiser will ask about.

    :func:`~fpl_edge.opt.problem.build_problem` builds its universe from
    ``snapshot.players`` -- everyone -- and raises if the forecast is missing any
    of them. The points model simulates ``snapshot.selectable`` -- the players
    the game would actually let you pick -- which is a strictly smaller set:
    injured, suspended and removed players are ownable in principle but are not
    in a lineup the model can simulate.

    The gap is filled with zeros, and the fill is *checked*: if a player the game
    would let you select is missing a projection, that is a hole in the forecast
    and it raises rather than quietly scoring them zero. A zero for a suspended
    player is harmless because ``HorizonProblem.ownable`` already excludes him; a
    zero for a fit starter would make the optimiser sell him.
    """
    have = set(frame["code"].astype(int))
    everyone = snapshot.players(str(season))
    selectable = set(snapshot.selectable(str(season))["code"].astype(int))
    missing = set(everyone["code"].astype(int)) - have

    holes = missing & selectable
    if holes:
        raise PointsForecastUnavailableError(
            f"{len(holes)} selectable player(s) have no projection, e.g. "
            f"{sorted(holes)[:5]}. Scoring them zero would tell the optimiser to "
            "sell fit, pickable players. Fix the forecast rather than the gap."
        )
    if not missing:
        return frame
    pad = pd.DataFrame(
        [
            {"code": int(code), "gw": int(gw), "xpts": 0.0, "p_play": 0.0}
            for code in sorted(missing)
            for gw in gws
        ]
    )
    return pd.concat([frame, pad], ignore_index=True)[list(POINTS_FORECAST_COLUMNS)]


def broadcast_single_gw(frame: pd.DataFrame, gws: list[GwId]) -> pd.DataFrame:
    """Repeat a one-gameweek forecast across a horizon. Explicitly a fiction.

    Provided because a one-gameweek projection is what exists today and a caller
    may knowingly want to see a multi-week shape from it. It is not a fixture-
    aware forecast and must never be presented as one: every gameweek gets the
    same numbers, so it cannot see a blank, a double or a run of easy games,
    which is most of what a multi-week horizon is for.
    """
    base = frame.drop(columns=["gw"], errors="ignore")
    return pd.concat(
        [base.assign(gw=int(gw)) for gw in gws], ignore_index=True
    )[list(POINTS_FORECAST_COLUMNS)]
