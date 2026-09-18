"""Everything the dossier reads, loaded once.

Sixteen sections would otherwise hit the warehouse sixteen times for the same
frames. ``_Ctx`` is loaded once and passed to every builder; each loader records
its own failure as a string on the context rather than raising, so one dead
source costs one section and not the whole dossier.

``resolve`` is here too, because deciding WHICH player the query means is a read
and it happens before anything else.

Split out of the 1,674-line ``fpl_edge/interfaces/dossier.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 18). A leaf within the
package: it imports nothing from its two siblings.
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import pandas as pd
from fpl_edge.interfaces.features import player_universe
from fpl_edge.interfaces.ideas import Clarification
from fpl_edge.interfaces.parsing import PlayerResolver
from fpl_edge.store import Snapshot, Warehouse
from fpl_edge.types import PlayerCode


UTC = dt.timezone.utc


DEFAULT_SEASON = "2026-27"


DEFAULT_HISTORY = ("2022-23", "2023-24", "2024-25", "2025-26")


PROJECTION_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "warehouse" / "gw1_projection.parquet"
)


POS_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def resolve(
    snapshot: Snapshot, query: str, *, season: str
) -> tuple[PlayerCode | None, Clarification | None, pd.DataFrame]:
    """Fuzzy name -> one player, or a refusal to guess.

    Delegates to :class:`~fpl_edge.interfaces.parsing.PlayerResolver` rather than
    matching names here. That resolver already knows that "salah" and "saliba"
    are two edits apart and must not be confused, that "kdb" is a nickname no
    string distance recovers, and that a tie is answered by asking rather than by
    breaking it on ownership. Reimplementing any of that would mean a name
    resolves one way through the idea inbox and another through the dossier.
    """
    players = player_universe(snapshot, season)
    if players.empty:
        return None, Clarification(
            raw_text=query,
            question=f"No player list in the warehouse for {season} at {snapshot.as_of:%Y-%m-%d %H:%M}Z.",
            candidates=(), pending_id="", kind="no_universe",
        ), players

    res = PlayerResolver(players).resolve(query)
    if res.best is not None:
        return res.best.code, None, players

    if res.ambiguous:
        question = f"Which {query.strip()} did you mean?"
        kind = "ambiguous"
    else:
        question = f"I cannot find a player matching {query.strip()!r}."
        kind = "not_found"
    return None, Clarification(
        raw_text=query, question=question, candidates=res.candidates,
        pending_id="", kind=kind,
    ), players


@dataclass
class _Ctx:
    """Everything loaded once, so sixteen sections do not each hit the warehouse."""

    wh: Warehouse
    snap: Snapshot
    season: str
    gw: int
    as_of: dt.datetime
    code: int
    row: pd.Series
    players: pd.DataFrame
    teams: pd.DataFrame
    history: tuple[str, ...]
    warnings: list[str] = field(default_factory=list)

    # Lazily filled by the loaders below; None means "tried and could not".
    rates: Any = None
    rates_error: str | None = None
    fixtures: pd.DataFrame | None = None
    goal_model: Any = None
    goals_error: str | None = None
    ownership: pd.DataFrame | None = None
    ownership_error: str | None = None
    projection: pd.DataFrame | None = None
    projection_error: str | None = None
    projection_stamp: dt.datetime | None = None
    intel: Any = None
    intel_error: str | None = None

    @property
    def position(self) -> int:
        return int(self.row["position"])

    @property
    def team_code(self) -> int:
        return int(self.row["team_code"])

    @property
    def web_name(self) -> str:
        return str(self.row["web_name"])

    def team_name(self, code: int | None = None) -> str:
        code = self.team_code if code is None else int(code)
        if self.teams.empty:
            return f"team {code}"
        hit = self.teams[self.teams["team_code"].astype(int) == int(code)]
        return str(hit.iloc[0]["name"]) if not hit.empty else f"team {code}"


def _load_rates(ctx: _Ctx) -> None:
    from fpl_edge.models.points.shares import estimate_rates

    try:
        ctx.rates = estimate_rates(ctx.snap, list(ctx.history))
    except (ValueError, KeyError) as exc:
        ctx.rates_error = f"{type(exc).__name__}: {exc}"


def _load_fixtures(ctx: _Ctx, horizon: int) -> None:
    from fpl_edge.models.team_goals import DixonColesModel

    ctx.fixtures = ctx.snap.upcoming_fixtures(ctx.season, horizon_gws=horizon)
    try:
        model = DixonColesModel()
        model.fit(ctx.snap, ctx.season)
        ctx.goal_model = model
    except (ValueError, KeyError) as exc:
        ctx.goals_error = f"{type(exc).__name__}: {exc}"


def _load_ownership(ctx: _Ctx) -> None:
    from fpl_edge.models.ownership.model import OwnershipForecaster

    try:
        ctx.ownership = OwnershipForecaster().forecast(ctx.snap, ctx.season, ctx.gw)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        ctx.ownership_error = f"{type(exc).__name__}: {exc}"


def _load_projection(ctx: _Ctx, *, simulate: bool, n_sims: int, path: Path) -> None:
    if simulate:
        try:
            ctx.projection = _simulate_projection(ctx, n_sims=n_sims)
            ctx.projection_stamp = ctx.as_of
        except (ValueError, KeyError, ImportError) as exc:
            ctx.projection_error = f"live simulation failed — {type(exc).__name__}: {exc}"
        return
    if not path.exists():
        ctx.projection_error = (
            f"no cached projection at {path}. Run `uv run python scripts/gw1_projection.py` "
            "to produce one, or pass --simulate to run the points model inline "
            "(about 95 s: it fits the minutes model over four seasons first)."
        )
        return
    try:
        ctx.projection = pd.read_parquet(path)
        ctx.projection_stamp = dt.datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except (OSError, ValueError) as exc:
        ctx.projection_error = f"{type(exc).__name__} reading {path}: {exc}"


def _simulate_projection(ctx: _Ctx, *, n_sims: int) -> pd.DataFrame:
    """Run the real decomposed points model for this gameweek.

    Slow by construction, not by accident: the minutes model is a gradient
    boosting classifier trained on every player-fixture in the visible history,
    and the point of the decomposed model is that scorelines, minutes and shares
    are sampled *jointly* so two defenders in the same team keep a clean sheet in
    the same simulation. Neither part can be cached across as-of instants without
    reintroducing exactly the leakage the snapshot prevents.
    """
    from fpl_edge.models.minutes import GBMMinutesModel, TrainingSetBuilder
    from fpl_edge.models.points.model import DecomposedPointsModel
    from fpl_edge.models.team_goals import DixonColesModel

    goals = ctx.goal_model
    if goals is None:
        goals = DixonColesModel()
        goals.fit(ctx.snap, ctx.season)
    builder = TrainingSetBuilder(snapshot_at=ctx.wh.snapshot_at, catalog=ctx.snap)
    minutes = GBMMinutesModel().fit(builder.build(list(ctx.history)))
    rates = ctx.rates
    if rates is None:
        from fpl_edge.models.points.shares import estimate_rates

        rates = estimate_rates(ctx.snap, list(ctx.history))
    model = DecomposedPointsModel(goal_model=goals, minutes_model=minutes, rates=rates)
    sample = model.simulate(ctx.snap, ctx.season, ctx.gw, n_sims=n_sims, seed=20260821)
    return pd.DataFrame(
        {
            "code": sample.codes,
            "xpts": sample.mean(),
            "p10": sample.quantile(0.10),
            "p90": sample.quantile(0.90),
            "p_haul": sample.p_at_least(10),
            "p_blank": (sample.points <= 2).mean(axis=1),
        }
    ).set_index("code")


def _load_intel(ctx: _Ctx) -> None:
    from fpl_edge.intel.store import IntelStore

    try:
        store, exists = IntelStore.open_reader(ctx.wh)
    except Exception as exc:  # noqa: BLE001 - a broken intel table must not kill the dossier
        ctx.intel_error = f"{type(exc).__name__}: {exc}"
        return
    if not exists:
        ctx.intel_error = (
            "the intel tables do not exist in this warehouse. Run "
            "`uv run python -m fpl_edge.intel.cli collect` once to create and fill them."
        )
        return
    ctx.intel = store
