"""The three baselines a minutes model has to beat to be worth running.

They are written to be *strong*, not straw men: the per-player baseline gets its
smoothing constant fitted on the training seasons rather than an arbitrary
Laplace 1, because beating a badly-tuned baseline proves nothing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import ModelCard
from fpl_edge.models.minutes import measured
from fpl_edge.models.minutes.base import BaseMinutesModel, normalise
from fpl_edge.models.minutes.training import TrainingSet


def _onehot(y: np.ndarray) -> np.ndarray:
    out = np.zeros((len(y), 3))
    out[np.arange(len(y)), y] = 1.0
    return out


def _log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = normalise(p)
    return float(-np.log(p[np.arange(len(y)), y]).mean())


class BaseRateBaseline(BaseMinutesModel):
    """(i) Predict the training-set class frequencies for everyone."""

    def __init__(self) -> None:
        super().__init__()
        self.rates = np.array([1 / 3, 1 / 3, 1 / 3])
        self.card = ModelCard(
            name="minutes.baseline.base_rate",
            approach="Constant training-set class frequencies",
            baseline="uniform 1/3 (log loss ln 3 = 1.0986)",
            metric="multiclass log loss (walk-forward, held-out season)",
            score=measured.LOG_LOSS["base_rate"],
            baseline_score=1.0986,
            trained_through=measured.TRAINED_THROUGH,
            notes=measured.notes("base_rate"),
        )

    def fit(self, ts: TrainingSet) -> BaseRateBaseline:
        y = ts.frame["bucket"].to_numpy(dtype=int)
        self.rates = np.bincount(y, minlength=3) / max(len(y), 1)
        self.learn_mean_minutes(ts.frame)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return np.repeat(self.rates[None, :], len(frame), axis=0)


class PriorSeasonRateBaseline(BaseMinutesModel):
    """(ii) Predict each player's realised rates from the previous season.

    Smoothed toward the global base rate with a pseudo-count fitted on the
    training seasons, which is the fairest version of this baseline: unsmoothed
    it is infinitely confident about a player with 22 starts and would be beaten
    by anything at all.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rates = np.array([1 / 3, 1 / 3, 1 / 3])
        self.alpha = 1.0
        self.card = ModelCard(
            name="minutes.baseline.prior_season",
            approach="Per-player previous-season bucket rates, smoothed to the base rate",
            baseline="base rate",
            metric="multiclass log loss (walk-forward, held-out season)",
            score=measured.LOG_LOSS["prior_season"],
            baseline_score=measured.LOG_LOSS["base_rate"],
            trained_through=measured.TRAINED_THROUGH,
            notes=measured.notes("prior_season"),
        )

    def fit(self, ts: TrainingSet) -> PriorSeasonRateBaseline:
        frame = ts.frame
        y = frame["bucket"].to_numpy(dtype=int)
        self.rates = np.bincount(y, minlength=3) / max(len(y), 1)
        best, best_loss = 1.0, np.inf
        for alpha in (0.5, 1, 2, 3, 5, 8, 12, 20, 30):
            self.alpha = float(alpha)
            loss = _log_loss(y, self.predict_proba(frame))
            if loss < best_loss:
                best, best_loss = float(alpha), loss
        self.alpha = best
        self.learn_mean_minutes(frame)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        n = frame["prev_n_obs"].to_numpy(dtype=float)
        n = np.nan_to_num(n, nan=0.0)
        rates = np.column_stack(
            [
                np.nan_to_num(frame["prev_unavail_rate"].to_numpy(dtype=float)),
                np.nan_to_num(frame["prev_cameo_rate"].to_numpy(dtype=float)),
                np.nan_to_num(frame["prev_full_rate"].to_numpy(dtype=float)),
            ]
        )
        counts = rates * n[:, None]
        return normalise((counts + self.alpha * self.rates[None, :]) / (n + self.alpha)[:, None])


class ChanceOfPlayingBaseline(BaseMinutesModel):
    """(iii) FPL's own ``chance_of_playing_next_round``, used as published.

    Where the field is populated it is read literally as P(features at all).
    Where it is NULL - which is the overwhelming majority of rows, because FPL
    only populates it for flagged players - there is nothing to read, so the
    status field decides and the base rate fills in. The conditional split of
    "played" into cameo and full comes from the training seasons, since FPL
    publishes no view on that at all.
    """

    def __init__(self) -> None:
        super().__init__()
        self.rates = np.array([1 / 3, 1 / 3, 1 / 3])
        self.split = np.array([0.35, 0.65])  # P(cameo | played), P(full | played)
        self.coverage = 0.0
        self.card = ModelCard(
            name="minutes.baseline.fpl_chance",
            approach="chance_of_playing_next_round read literally, base rate elsewhere",
            baseline="base rate",
            metric="multiclass log loss (walk-forward, held-out season)",
            score=measured.LOG_LOSS["fpl_chance"],
            baseline_score=measured.LOG_LOSS["base_rate"],
            trained_through=measured.TRAINED_THROUGH,
            notes=measured.notes(
                "fpl_chance",
                extra=(
                    ("scores exactly the base rate on real history: status and "
                     "chance_of_playing_next_round are NULL on 100% of pre-2026-27 rows, "
                     "so this baseline has nothing to read"),
                    ("on data that does carry availability (the synthetic fixtures) it is "
                     "informative but still the weakest of the three baselines"),
                ),
            ),
        )

    def fit(self, ts: TrainingSet) -> ChanceOfPlayingBaseline:
        frame = ts.frame
        y = frame["bucket"].to_numpy(dtype=int)
        self.rates = np.bincount(y, minlength=3) / max(len(y), 1)
        played = y > 0
        if played.sum() > 0:
            self.split = np.array([(y[played] == 1).mean(), (y[played] == 2).mean()])
        self.coverage = float(frame["has_chance"].mean())
        self.learn_mean_minutes(frame)
        return self

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        chance = frame["chance_next"].to_numpy(dtype=float)
        has = frame["has_chance"].to_numpy(dtype=float) > 0.5
        base_play = 1.0 - self.rates[0]
        p_play = np.where(has, chance / 100.0, base_play)
        # a hard status flag with no percentage published still means "out"
        hard_out = (frame["status_injured"].to_numpy(dtype=float) > 0.5) | (
            frame["status_suspended"].to_numpy(dtype=float) > 0.5
        )
        p_play = np.where(~has & hard_out, 0.0, p_play)
        p_play = np.clip(p_play, 0.02, 0.98)
        return normalise(
            np.column_stack([1 - p_play, p_play * self.split[0], p_play * self.split[1]])
        )


BASELINES = {
    "base_rate": BaseRateBaseline,
    "prior_season": PriorSeasonRateBaseline,
    "fpl_chance": ChanceOfPlayingBaseline,
}
