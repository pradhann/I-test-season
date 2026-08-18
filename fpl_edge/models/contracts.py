"""Interfaces every model implements.

These exist so the components can be built and tested independently and then
swapped: competing implementations of the same contract are compared on
out-of-sample calibration rather than on argument.

Two invariants run through all of them:

1. **Every model reads its inputs from a Snapshot.** A model that takes a raw
   Warehouse, a bare DataFrame of "current" data, or a filesystem path is a
   leakage risk and will be rejected in review.
2. **Every probabilistic output is a distribution, not a point estimate.** The
   objective is P(rank < threshold), which is a functional of the whole
   distribution and of its covariance with the field. A model that returns only
   a mean cannot serve it.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd

from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season


@dataclass(frozen=True, slots=True)
class ModelCard:
    """What a model is, and what it was measured at.

    Every implementation must supply one. "It runs" is not a status; the
    baseline and the score against it are the status.
    """

    name: str
    approach: str
    baseline: str
    metric: str
    score: float | None = None
    baseline_score: float | None = None
    trained_through: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def beats_baseline(self) -> bool | None:
        """Lower is better for all metrics used here (log loss, Brier, CRPS, MAE)."""
        if self.score is None or self.baseline_score is None:
            return None
        return self.score < self.baseline_score


#: Columns a minutes model must return, one row per (code, fixture_id).
MINUTES_COLUMNS = (
    "code", "fixture_id", "gw",
    "p_unavailable",  # did not appear at all
    "p_cameo",        # 1-59 minutes
    "p_full",         # 60+ minutes (the FPL-relevant boundary)
    "exp_minutes",
)

#: Columns a team strength model must return, one row per (fixture_id, team_code).
TEAM_STRENGTH_COLUMNS = (
    "fixture_id", "gw", "team_code", "opponent_code", "is_home",
    "exp_goals_for", "exp_goals_against",
    "p_clean_sheet",
)

#: Columns an ownership model must return, one row per code.
OWNERSHIP_COLUMNS = (
    "code", "gw",
    "eo_overall",       # effective ownership in the overall field, 0-1
    "captaincy_share",  # share of the field captaining this player, 0-1
    "eo_top10k",        # effective ownership among top-10k managers, 0-1
)


@runtime_checkable
class MinutesModel(Protocol):
    """Distribution over minutes buckets per player per fixture."""

    card: ModelCard

    def predict(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        """Return a frame with :data:`MINUTES_COLUMNS`.

        Probabilities across the three buckets must sum to 1 per row.
        """
        ...


@runtime_checkable
class TeamStrengthModel(Protocol):
    """Bivariate team goal model (Dixon-Coles style)."""

    card: ModelCard

    def predict(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        """Return a frame with :data:`TEAM_STRENGTH_COLUMNS`."""
        ...

    def score_matrix(self, fixture_id: int, max_goals: int = 8) -> np.ndarray:
        """Joint P(home goals = i, away goals = j). Must sum to ~1."""
        ...


@dataclass(frozen=True)
class PointsSample:
    """Monte Carlo draws of FPL points.

    ``points`` is (n_players, n_sims). Correlation matters enormously and must
    be preserved: two Arsenal defenders keeping a clean sheet is one event, not
    two independent ones, and a model that samples them independently will
    understate variance and mis-price every differential.
    """

    codes: np.ndarray          # (n_players,) stable player codes
    gw: GwId
    points: np.ndarray         # (n_players, n_sims) int/float points
    minutes: np.ndarray | None = None  # (n_players, n_sims) if available

    def __post_init__(self) -> None:
        if self.points.shape[0] != self.codes.shape[0]:
            raise ValueError(
                f"points has {self.points.shape[0]} rows but {self.codes.shape[0]} codes"
            )

    @property
    def n_sims(self) -> int:
        return int(self.points.shape[1])

    def mean(self) -> np.ndarray:
        return self.points.mean(axis=1)

    def quantile(self, q: float) -> np.ndarray:
        return np.quantile(self.points, q, axis=1)

    def p_at_least(self, threshold: float) -> np.ndarray:
        return (self.points >= threshold).mean(axis=1)


@runtime_checkable
class PointsModel(Protocol):
    """Full joint distribution of FPL points for a gameweek."""

    card: ModelCard

    def simulate(
        self,
        snapshot: Snapshot,
        season: Season,
        gw: GwId,
        *,
        n_sims: int = 10_000,
        seed: int = 0,
    ) -> PointsSample:
        """Draw correlated point samples. Must be deterministic given ``seed``."""
        ...


@runtime_checkable
class OwnershipModel(Protocol):
    """Forecast of what the field will own and captain at the next deadline."""

    card: ModelCard

    def forecast(self, snapshot: Snapshot, season: Season, gw: GwId) -> pd.DataFrame:
        """Return a frame with :data:`OWNERSHIP_COLUMNS`."""
        ...


@dataclass(frozen=True, slots=True)
class RankUtilityConfig:
    """How much we care about rank rather than points.

    ``target_rank`` is the threshold whose exceedance probability we maximise.
    ``risk_lambda`` penalises catastrophic outcomes; 0 is pure P(hit target),
    larger values trade right tail for left-tail protection.
    """

    target_rank: int = 10_000
    stretch_rank: int = 1_000
    risk_lambda: float = 0.35
    field_size: int | None = None  # None -> read from the API's total_players

    def validate(self) -> None:
        if self.target_rank <= 0:
            raise ValueError("target_rank must be positive")
        if not 0.0 <= self.risk_lambda <= 5.0:
            raise ValueError("risk_lambda out of sane range")


def validate_probability_frame(df: pd.DataFrame, cols: tuple[str, ...], *, tol: float = 1e-6) -> None:
    """Assert the given probability columns sum to 1 across each row."""
    total = df[list(cols)].sum(axis=1)
    bad = df[(total - 1.0).abs() > tol]
    if not bad.empty:
        raise ValueError(
            f"{len(bad)} rows have probabilities summing to "
            f"{bad[list(cols)].sum(axis=1).iloc[0]:.6f} rather than 1.0"
        )
