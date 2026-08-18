"""Shared machinery for every minutes model and baseline.

All of them produce the same three-column distribution and the same output
frame, so they can be scored against each other row for row. The split of
responsibilities is deliberate: :meth:`predict_proba` is the model, and
:meth:`predict` is the plumbing that turns a Snapshot into rows and the rows
into :data:`~fpl_edge.models.contracts.MINUTES_COLUMNS`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import MINUTES_COLUMNS, ModelCard, validate_probability_frame
from fpl_edge.models.minutes.features import build_feature_frame
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

#: Probability floor. Zero probability on an event that happens is an unbounded
#: loss, and no minutes model is ever that certain: a nailed-on starter still
#: pulls up in the warm-up.
EPS = 1e-4

#: Fallback expected minutes within a bucket, overwritten by ``fit``.
DEFAULT_MEAN_MINUTES = (0.0, 24.0, 83.0)


def normalise(probs: np.ndarray) -> np.ndarray:
    """Clip away impossible certainty and renormalise rows to sum to 1."""
    p = np.asarray(probs, dtype=float)
    p = np.nan_to_num(p, nan=0.0, posinf=0.0, neginf=0.0)
    p = np.clip(p, EPS, None)
    return p / p.sum(axis=1, keepdims=True)


@dataclass
class _Fitted:
    mean_minutes: tuple[float, float, float] = DEFAULT_MEAN_MINUTES


class BaseMinutesModel:
    """Implements the :class:`~fpl_edge.models.contracts.MinutesModel` protocol."""

    card: ModelCard
    name: str = "minutes"

    def __init__(self) -> None:
        self._fitted = _Fitted()

    # -- to be provided by subclasses -------------------------------------

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        raise NotImplementedError

    # -- plumbing ----------------------------------------------------------

    def learn_mean_minutes(self, frame: pd.DataFrame) -> None:
        if frame.empty or "minutes" not in frame.columns:
            return
        means = []
        for b, default in zip((0, 1, 2), DEFAULT_MEAN_MINUTES):
            sel = frame.loc[frame["bucket"] == b, "minutes"]
            means.append(float(sel.mean()) if len(sel) >= 20 else default)
        self._fitted.mean_minutes = (means[0], means[1], means[2])

    def to_frame(self, ids: pd.DataFrame, probs: np.ndarray) -> pd.DataFrame:
        p = normalise(probs)
        mm = np.asarray(self._fitted.mean_minutes, dtype=float)
        out = pd.DataFrame(
            {
                "code": ids["code"].to_numpy(),
                "fixture_id": ids["fixture_id"].to_numpy(),
                "gw": ids["gw"].to_numpy(),
                "p_unavailable": p[:, 0],
                "p_cameo": p[:, 1],
                "p_full": p[:, 2],
                "exp_minutes": p @ mm,
            }
        )
        validate_probability_frame(out, ("p_unavailable", "p_cameo", "p_full"))
        return out[list(MINUTES_COLUMNS)]

    def predict(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        """Read the world through the Snapshot, then score it."""
        frame = build_feature_frame(snapshot, season, list(gws))
        if frame.empty:
            return pd.DataFrame(columns=list(MINUTES_COLUMNS))
        return self.to_frame(frame, self.predict_proba(frame))
