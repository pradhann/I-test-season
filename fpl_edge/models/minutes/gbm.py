"""Gradient-boosted classifier over the engineered minutes features.

Approach (b). Where the hierarchical model imposes a shape and fits nine
parameters, this one imposes almost nothing and lets the trees find the
interactions - "flagged AND fourth in the depth chart AND the club plays
Thursdays" is a leaf it can carve out and a Dirichlet prior cannot.

Backend
-------
The default is scikit-learn's :class:`HistGradientBoostingClassifier`: the same
histogram-binned gradient boosting as LightGBM, with the same NaN-as-a-branch
handling, and it is the backend the committed measurements were produced with.
LightGBM is available behind ``backend="lightgbm"`` but is *not* the default and
is not what the ModelCard numbers refer to - on this machine the wheel cannot
load at all (``libomp.dylib`` missing), and a model whose score depends on which
optional native library happens to import is not a model we can report on.

Two boosters, not one
---------------------
Warm rows and cold-start rows do not share a feature space: at GW1 the
current-season columns are structurally absent rather than missing at random,
and a single booster trained on the pooled data spends its capacity learning
that ``n_obs_season IS NULL`` implies gameweek 1. They are trained separately,
on their own column sets, and rows are routed by ``is_cold_start``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.minutes import measured
from fpl_edge.models.minutes.base import BaseMinutesModel, normalise
from fpl_edge.models.minutes.features import COLD_FEATURE_COLUMNS, FEATURE_COLUMNS
from fpl_edge.models.minutes.training import TrainingSet

WARM_PARAMS: dict[str, Any] = {
    "max_iter": 400,
    "learning_rate": 0.06,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "early_stopping": True,
    "validation_fraction": 0.15,
    "n_iter_no_change": 25,
}
COLD_PARAMS: dict[str, Any] = {
    "max_iter": 250,
    "learning_rate": 0.05,
    "max_leaf_nodes": 7,
    "min_samples_leaf": 30,
    "l2_regularization": 3.0,
    "early_stopping": True,
    "validation_fraction": 0.2,
    "n_iter_no_change": 20,
}


def _make_classifier(backend: str, seed: int, params: dict[str, Any]):
    if backend == "hist":
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(random_state=seed, **params)
    if backend == "lightgbm":  # pragma: no cover - optional, not the reported backend
        import lightgbm as lgb

        return lgb.LGBMClassifier(
            objective="multiclass",
            num_class=3,
            n_estimators=params["max_iter"],
            learning_rate=params["learning_rate"],
            num_leaves=params["max_leaf_nodes"],
            min_child_samples=params["min_samples_leaf"],
            reg_lambda=params["l2_regularization"],
            random_state=seed,
            deterministic=True,
            n_jobs=1,
            verbose=-1,
        )
    raise ValueError(f"unknown backend {backend!r}")


class _Booster:
    """One classifier plus the column set it was trained on."""

    def __init__(self, columns: tuple[str, ...], backend: str, seed: int,
                 params: dict[str, Any]) -> None:
        self.columns = columns
        self.backend = backend
        self.seed = seed
        self.params = params
        self.model = None
        self.used: tuple[str, ...] = columns
        self.fallback = np.full(3, 1 / 3)

    def fit(self, frame: pd.DataFrame) -> _Booster:
        if frame.empty:
            return self
        y = frame["bucket"].to_numpy(dtype=int)
        self.fallback = np.bincount(y, minlength=3) / len(y)
        if len(frame) < 100 or len(np.unique(y)) < 2:
            return self
        # A column that is entirely absent in the training fold (the earliest
        # season has no prior season, so every prev_* feature is NaN) carries no
        # signal and makes the histogram binner fail outright. Drop it here and
        # remember the survivors so predict uses the identical design matrix.
        sub = frame[list(self.columns)]
        keep = [c for c in self.columns if sub[c].notna().any() and sub[c].nunique() > 1]
        if len(keep) < 2:
            return self
        self.used = tuple(keep)
        X = frame[list(self.used)].to_numpy(dtype=float)
        self.model = _make_classifier(self.backend, self.seed, self.params).fit(X, y)
        return self

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        if self.model is None or frame.empty:
            return np.repeat(self.fallback[None, :], len(frame), axis=0)
        X = frame[list(self.used)].to_numpy(dtype=float)
        raw = self.model.predict_proba(X)
        out = np.zeros((len(frame), 3))
        for j, cls in enumerate(self.model.classes_):
            out[:, int(cls)] = raw[:, j]
        return normalise(out)


class GBMMinutesModel(BaseMinutesModel):
    """Approach (b): histogram gradient boosting, warm and cold boosters."""

    name = "gbm"

    def __init__(self, *, backend: str = "hist", seed: int = 20260818) -> None:
        super().__init__()
        self.backend = backend
        self.seed = seed
        self.warm = _Booster(FEATURE_COLUMNS, backend, seed, WARM_PARAMS)
        self.cold = _Booster(COLD_FEATURE_COLUMNS, backend, seed, COLD_PARAMS)
        self.card = ModelCard(
            name="minutes.gbm",
            approach=(
                f"Multiclass histogram gradient boosting ({backend}) over rotation, congestion, "
                "depth, availability and market features; separate cold-start booster on the "
                "prior-season-only column set"
            ),
            baseline="per-player previous-season rate (smoothed)",
            metric="multiclass log loss (walk-forward, held-out season)",
            score=measured.LOG_LOSS["gbm"],
            baseline_score=measured.LOG_LOSS["prior_season"],
            trained_through=measured.TRAINED_THROUGH,
            notes=measured.notes(
                "gbm",
                extra=(
                    ("best score on real data at every horizon, including the GW1 "
                     "cold start; loses the cold start on synthetic data"),
                    "deterministic given seed; sklearn hist backend, not LightGBM",
                ),
            ),
        )

    def fit(self, ts: TrainingSet) -> GBMMinutesModel:
        self.warm.fit(ts.warm)
        self.cold.fit(ts.cold_frame)
        self.learn_mean_minutes(ts.frame)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        out = np.zeros((len(frame), 3))
        cold_mask = frame["is_cold_start"].to_numpy(dtype=float) >= 0.5
        if (~cold_mask).any():
            out[~cold_mask] = self.warm.predict(frame[~cold_mask])
        if cold_mask.any():
            out[cold_mask] = self.cold.predict(frame[cold_mask])
        return out
