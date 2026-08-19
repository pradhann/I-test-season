"""Wiring the simulator to the real models instead of the development stand-ins.

``fpl_edge.sim.synthetic`` exists so the simulator was never blocked on other
teams. It is not a forecast and its ModelCard says so. This module replaces both
stand-ins with the shipped models:

* points: :class:`~fpl_edge.models.points.model.DecomposedPointsModel`, composed
  of the Dixon-Coles team model, the GBM minutes model and the measured per-90
  rates. Its correlation structure is the one the field model depends on --
  same-club defenders share a clean sheet, team-mates compete for the same three
  bonus points -- and it is why sampling my score and the field's independently
  would misprice every differential.
* ownership: :class:`~fpl_edge.models.ownership.model.OwnershipForecaster`.

Two adapters are needed, and both are adapters rather than edits to the models
because the mismatch is the simulator's problem, not theirs.

**Gameweek scope.** ``DecomposedPointsModel.simulate`` reads its fixtures via
``upcoming_fixtures(season, horizon_gws=1)``, so it can only simulate the *next*
gameweek; asking it for GW7 from a GW1 snapshot raises. A rest-of-season run
needs all 38. :class:`GwScopedSnapshot` narrows the fixture view to the
requested gameweek and passes everything else through untouched, which is a
strictly *smaller* view than the snapshot already permitted and therefore cannot
leak.

**Per-gameweek seeding.** ``SeasonSimulator`` passes the same ``seed`` to every
gameweek. A points model that keys its generator on the seed alone then returns
the identical draw 38 times, and a season total becomes 38 copies of one
gameweek: variance collapses, every rank is deterministic given GW1, and
P(top 10k) is meaningless. :class:`MultiGwPointsModel` mixes the gameweek into
the seed. This is not cosmetic and it is asserted in ``test_sim_live.py``.

**Frozen ownership.** The ownership forecast is a *next-deadline* forecast. Its
cold-start block was fitted on horizons of 1 to 20 days; asking it, at the GW1
deadline, what ownership will be in GW38 extrapolates a square-root-of-time
diffusion 260 days out, which is not a forecast but an arithmetic accident.
:class:`FrozenOwnershipForecast` therefore forecasts once, for the next
deadline, and holds it for the whole run -- the field's *composition* then
drifts only through ``FieldConfig.churn``. That is an approximation and it is
stated: rest-of-season ownership is not forecastable from GW1 and pretending
otherwise would be the more dishonest option.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field as _field
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import ModelCard, PointsSample
from fpl_edge.sim.squad import PlayerUniverse
from fpl_edge.types import GwId, Season

DEFAULT_DB = "data/warehouse/fpl.duckdb"
LIVE_SEASON = Season("2026-27")

#: Seasons whose finished matches the models may train on. 2026-27 has not been
#: played; it is the season being forecast.
HISTORY = ["2022-23", "2023-24", "2024-25", "2025-26"]


class GwScopedSnapshot:
    """A Snapshot whose fixture view is narrowed to one gameweek.

    Everything else delegates. Narrowing can only remove rows the snapshot was
    already willing to show, so this cannot widen what the model sees.
    """

    def __init__(self, inner: Any, gw: int) -> None:
        self._inner = inner
        self._gw = int(gw)

    def upcoming_fixtures(self, season: str, *, horizon_gws: int | None = None) -> pd.DataFrame:
        fx = self._inner.upcoming_fixtures(season)
        return fx[fx["gw"] == self._gw].reset_index(drop=True)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@dataclass
class MultiGwPointsModel:
    """The real points model, usable for any gameweek and correctly re-seeded.

    ``codes`` from the inner model follow the snapshot's own ordering; the
    simulator indexes a universe sorted by code, so every draw is permuted into
    universe order here rather than hoping the two agree.
    """

    inner: Any                     # DecomposedPointsModel
    universe: PlayerUniverse
    card: ModelCard = None  # type: ignore[assignment]
    _perm: np.ndarray | None = _field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.card is None:
            inner_card = getattr(self.inner, "card", None)
            self.card = inner_card or ModelCard(
                name="decomposed_points(live)", approach="see points model",
                baseline="unknown", metric="unknown",
            )

    def simulate(self, snapshot, season: Season, gw: GwId, *, n_sims: int = 5_000,
                 seed: int = 0) -> PointsSample:
        sample = self.inner.simulate(
            GwScopedSnapshot(snapshot, int(gw)), season, gw, n_sims=n_sims,
            # Distinct per gameweek. Without this every gameweek is the same
            # draw and the season total has 1/38th of its true variance.
            seed=int(seed) + 1_000_003 * int(gw),
        )
        perm = self._permutation(sample.codes)
        return PointsSample(
            codes=self.universe.codes,
            gw=sample.gw,
            points=sample.points[perm],
            minutes=None if sample.minutes is None else sample.minutes[perm],
        )

    def _permutation(self, codes: np.ndarray) -> np.ndarray:
        if self._perm is not None:
            return self._perm
        where = {int(c): i for i, c in enumerate(codes)}
        missing = [int(c) for c in self.universe.codes if int(c) not in where]
        if missing:
            raise ValueError(
                f"{len(missing)} universe players are absent from the points sample "
                f"(first: {missing[:5]}). Both must be built from the same Snapshot."
            )
        self._perm = np.array([where[int(c)] for c in self.universe.codes], dtype=np.int64)
        return self._perm


@dataclass
class FrozenOwnershipForecast:
    """One real ownership forecast, held for the whole run.

    ``frame`` is whatever :class:`OwnershipForecaster` produced for the next
    deadline, reindexed to the universe. The ``gw`` column is rewritten per call
    so downstream code sees a consistent frame, but the numbers do not change:
    this model does not claim to know GW20 ownership and says so in its card.
    """

    frame: pd.DataFrame
    card: ModelCard
    forecast_gw: int

    def set_expected_points(self, xp: np.ndarray) -> None:
        """Accepted and ignored: the held forecast was made once, in advance."""
        self._xp = np.asarray(xp, dtype=float)

    def forecast(self, snapshot, season: Season, gw: GwId) -> pd.DataFrame:
        out = self.frame.copy()
        out["gw"] = int(gw)
        return out


# --------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------


@dataclass
class LiveWorld:
    """Everything a :class:`~fpl_edge.sim.engine.SeasonSimulator` run needs."""

    universe: PlayerUniverse
    snapshot: Any
    points_model: MultiGwPointsModel
    ownership_model: FrozenOwnershipForecast
    xp: np.ndarray
    season_xp: np.ndarray
    eo: np.ndarray
    captaincy: np.ndarray
    eo_top10k: np.ndarray
    own_mean: np.ndarray
    own_sd: np.ndarray
    field_size: int
    as_of: dt.datetime
    deadline: dt.datetime
    days_to_deadline: float
    #: The handle the Snapshot came from, so a caller can close it. Deliberately
    #: NOT named ``warehouse``: ``x.warehouse`` on a snapshot-shaped object is
    #: the escape hatch the leakage audit exists to catch, and a field with that
    #: name here would train readers to expect one.
    store: Any = None

    def close(self) -> None:
        if self.store is not None:
            self.store.close()


def build_live_world(
    snapshot: Any,
    snapshot_at: Any,
    *,
    season: Season = LIVE_SEASON,
    gw: int = 1,
    field_size: int | None = None,
    n_xp_sims: int = 2_000,
    xp_seed: int = 20_260_818,
    season_gws: tuple[int, ...] = tuple(range(1, 39)),
    n_season_xp_sims: int = 400,
    store: Any = None,
) -> LiveWorld:
    """Assemble the real points and ownership models against a Snapshot.

    Takes the Snapshot and the ``snapshot_at`` factory rather than a database
    path, so nothing in the modelling path opens a connection, names a table or
    asks the operating system what time it is. :func:`open_live_world` is the
    one function that does those things.

    Two expected-points vectors come out, and conflating them is a real error:

    ``xp``
        the *next gameweek's* expected points. This is what the ownership
        model's projection feature wants -- the crowd is chasing this week's
        fixture, not a season average.
    ``season_xp``
        the mean over every gameweek the simulator will run. This is what
        "the expected-points-maximising squad" means for a season-long
        decision. Using the GW1 vector instead makes the supposedly
        xPts-optimal baseline not optimal at all: on the 2026-27 GW1 build,
        swaps that GW1 xP called a 0.18/gw *downgrade* gained 9.98 season
        points, because the two players' remaining fixtures differ. Every
        divergence result measured against that baseline would be measuring
        the baseline's own mistake.
    """
    from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
    from fpl_edge.models.ownership.model import OwnershipForecaster
    from fpl_edge.models.points.model import DecomposedPointsModel
    from fpl_edge.models.points.shares import estimate_rates
    from fpl_edge.models.team_goals import DixonColesModel

    snap = snapshot
    as_of = snap.as_of
    players = snap.selectable(str(season))
    if players.empty:
        raise ValueError(f"no selectable players for {season} at {as_of}")
    universe = PlayerUniverse.from_players_frame(players)

    goals = DixonColesModel()
    goals.fit(snap, str(season))
    builder = TrainingSetBuilder(snapshot_at=snapshot_at, catalog=snap)
    minutes = GBMMinutesModel().fit(builder.build(HISTORY))
    rates = estimate_rates(snap, HISTORY)
    points = MultiGwPointsModel(
        inner=DecomposedPointsModel(goal_model=goals, minutes_model=minutes, rates=rates),
        universe=universe,
    )

    # Expected points for the next gameweek, used by the ownership model's
    # projection feature.
    xp = points.simulate(snap, season, GwId(gw), n_sims=n_xp_sims, seed=xp_seed).mean()

    # Season-average expected points, for squad-level decisions.
    season_total = np.zeros(universe.n_players)
    for g in season_gws:
        season_total += points.simulate(
            snap, season, GwId(int(g)), n_sims=n_season_xp_sims, seed=xp_seed
        ).mean()
    season_xp = season_total / max(len(season_gws), 1)

    deadline = snap.deadline(str(season), int(gw))
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=dt.timezone.utc)
    n_field = int(field_size or _registry_total_players())

    forecaster = OwnershipForecaster(
        expected_points={int(c): float(v) for c, v in zip(universe.codes, xp)},
        field_size=n_field,
    )
    own = forecaster.forecast(snap, season, GwId(gw))
    own = own.set_index("code").reindex(universe.codes).reset_index()

    frozen = FrozenOwnershipForecast(
        frame=own,
        forecast_gw=int(gw),
        card=ModelCard(
            name="ownership.OwnershipForecaster(frozen)",
            approach=forecaster.card.approach,
            baseline=forecaster.card.baseline,
            metric=forecaster.card.metric,
            score=forecaster.card.score,
            baseline_score=forecaster.card.baseline_score,
            trained_through=forecaster.card.trained_through,
            notes=forecaster.card.notes + (
                f"Forecast made once for GW{gw} and held for every later gameweek. "
                "Rest-of-season ownership is not forecastable from a GW1 snapshot; "
                "the field's composition drifts only through FieldConfig.churn.",
            ),
        ),
    )
    return LiveWorld(
        universe=universe, snapshot=snap, points_model=points, ownership_model=frozen,
        xp=np.asarray(xp, dtype=float),
        season_xp=np.asarray(season_xp, dtype=float),
        eo=own["eo_overall"].to_numpy(dtype=float),
        captaincy=own["captaincy_share"].to_numpy(dtype=float),
        eo_top10k=own["eo_top10k"].to_numpy(dtype=float),
        own_mean=own["own_mean"].to_numpy(dtype=float),
        own_sd=own["own_sd"].to_numpy(dtype=float),
        field_size=n_field, as_of=as_of, deadline=deadline,
        days_to_deadline=float((deadline - as_of).total_seconds() / 86400.0),
        store=store,
    )


def open_live_world(
    *, db_path: str = DEFAULT_DB, season: Season = LIVE_SEASON,
    as_of: dt.datetime | None = None, **kw,
) -> LiveWorld:
    """Entry-point seam: open the warehouse, mint a Snapshot, build the world.

    This is the ONLY function in the package that constructs a
    :class:`~fpl_edge.store.Warehouse` or names a warehouse table, and it does
    nothing else. Everything downstream takes a Snapshot. The static leakage
    audit flags it under ``DIRECT_DB`` and ``DIRECT_TABLE``, in the same
    category as ``models/minutes/evaluate.py``, ``models/team_goals/evaluate.py``
    and ``cli/main.py``: an entry point whose job is to mint Snapshots for the
    modelling code, which then reads nothing else directly.

    ``as_of`` defaults to the latest instant the warehouse has observed -- the
    live decision point, everything visible now and nothing after it. It is read
    from the data rather than from the wall clock, so a run is reproducible and
    a backtest cannot slip forward by being executed later.
    """
    from fpl_edge.store import Warehouse

    # read_copy: a live-world build reads for minutes and must not starve
    # concurrent ingest writers (DuckDB is one-writer-XOR-many-readers).
    wh = Warehouse.read_copy(db_path)
    if as_of is None:
        as_of = _latest_observation(wh, str(season))
    return build_live_world(wh.snapshot_at(as_of), wh.snapshot_at,
                            season=season, store=wh, **kw)


def _latest_observation(wh, season: str) -> dt.datetime:
    """The most recent instant the warehouse has actually seen data for."""
    row = wh.sql(
        "SELECT max(as_of) AS t FROM fact_player_state WHERE season = ?", [season]
    )
    t = row["t"].iloc[0]
    if pd.isna(t):
        raise ValueError(f"no observations for {season} in the warehouse")
    ts = pd.Timestamp(t)
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()


def _registry_total_players() -> int:
    """Field size from the rule registry, which captured it from the live API.

    The registry flags ``misc.total_players_at_fetch`` as **not final**:
    registrations are still climbing. Measured against the API on 2026-08-18 it
    had already moved from 5,896,644 to 5,950,733, so treating it as a constant
    understates the deadline field by roughly 1%, which tightens every absolute
    rank threshold. Pass ``field_size`` explicitly to override it.

    The simplex identity ``sum(selected)/15`` would be exact and needs no
    registry at all, but the warehouse stores ownership as a percentage rather
    than a count, so it cannot be applied. Guessing is refused: an in-season
    transfer flow divided by a made-up field size is a silently rescaled signal.
    """
    from fpl_edge.rules import rules

    value = rules().get("misc.total_players_at_fetch")
    if not value:
        raise ValueError(
            "the rule registry has no total_players; pass field_size explicitly "
            "rather than letting the field size be guessed"
        )
    return int(value)
