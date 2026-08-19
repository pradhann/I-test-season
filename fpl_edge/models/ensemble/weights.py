"""Ensemble weights, and the rule that a weight has to be earned.

The premise of the ensemble is that several independent estimates beat any one
of them. That premise says nothing about the estimates being *equally* good, and
in practice they are not: over the held-out set measured in ``docs/projections.md``
the spread between the best and worst constituent is large enough that an equal
average is worse than simply using the best single source.

So weights come from measured out-of-sample loss, and a provider with no
measurement gets no weight. :class:`EnsembleWeights` refuses to hand out a
number for a provider it has never scored -- :class:`UnearnedWeightError` rather
than a plausible-looking default -- because the failure mode this whole module
exists to prevent is a guess that has been sitting in a config file long enough
to look like a fact.

Two fitters, both reported
--------------------------
* :func:`fit_stacking` -- non-negative least squares of realised points on the
  providers' projections, weights constrained to sum to 1. This is the right
  answer when providers are correlated, because it discovers that a provider
  which is merely a noisier copy of another adds nothing and gives it ~0.
* :func:`fit_inverse_loss` -- weight proportional to ``1 / loss``. Cruder, but it
  degrades gracefully when a provider covers only part of the held-out set,
  where a joint regression would have to drop every row the provider misses.

They are reported side by side because when they disagree sharply that is a
statement about collinearity between the sources, which is itself worth seeing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import nnls

from fpl_edge.eval.calibration import brier_score, crps_ensemble, log_loss


class UnearnedWeightError(RuntimeError):
    """A weight was requested for a provider that has never been scored.

    Deliberately loud. The alternative -- returning 1/n, or 0, or the mean of
    the others -- puts an unmeasured source into a decision with a number that
    looks exactly like a measured one.
    """


@dataclass(frozen=True, slots=True)
class ProviderScore:
    """One provider's measured out-of-sample performance."""

    provider: str
    n_obs: int
    mae: float
    rmse: float
    crps: float | None
    brier_haul: float | None
    brier_blank: float | None
    log_loss_return: float | None
    bias: float
    correlation: float

    def as_row(self) -> dict[str, object]:
        return {
            "provider": self.provider, "n_obs": self.n_obs, "mae": self.mae,
            "rmse": self.rmse, "crps": self.crps, "brier_haul": self.brier_haul,
            "brier_blank": self.brier_blank, "log_loss_return": self.log_loss_return,
            "bias": self.bias, "corr": self.correlation,
        }


@dataclass(frozen=True)
class EnsembleWeights:
    """Fitted weights plus the evidence that produced them."""

    weights: dict[str, float]
    scores: dict[str, ProviderScore]
    method: str
    holdout: str
    loss_metric: str = "mae"
    fitted_at: dt.datetime = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc)
    )
    #: Providers deliberately carried at zero because nothing has scored them.
    unearned: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if self.weights and abs(total - 1.0) > 1e-6:
            raise ValueError(f"weights sum to {total:.6f}, not 1")
        for name in self.weights:
            if name not in self.scores:
                raise UnearnedWeightError(
                    f"{name!r} has a weight but no ProviderScore. Every weight in "
                    f"this object must point at the measurement that earned it."
                )

    def weight(self, provider: str) -> float:
        if provider in self.weights:
            return self.weights[provider]
        if provider in self.unearned:
            raise UnearnedWeightError(
                f"{provider!r} has never been scored out-of-sample, so it has no "
                f"earned weight. It is ingested and reported in the disagreement "
                f"table, but it does not move the ensemble until it has been "
                f"measured. Score it with backtest.walk_forward once its "
                f"projections have realised gameweeks behind them."
            )
        raise KeyError(f"unknown provider {provider!r}")

    def to_frame(self) -> pd.DataFrame:
        rows = []
        for name, score in self.scores.items():
            rows.append({
                "provider": name,
                "weight": self.weights.get(name, 0.0),
                "loss": score.mae, "loss_metric": self.loss_metric,
                "n_obs": score.n_obs, "earned": True,
                "holdout": self.holdout, "as_of": self.fitted_at,
            })
        for name in self.unearned:
            rows.append({
                "provider": name, "weight": 0.0, "loss": None,
                "loss_metric": self.loss_metric, "n_obs": 0, "earned": False,
                "holdout": "never scored", "as_of": self.fitted_at,
            })
        return pd.DataFrame(rows)


def score_provider(
    provider: str,
    predicted: np.ndarray,
    actual: np.ndarray,
    *,
    samples: np.ndarray | None = None,
) -> ProviderScore:
    """Measure one provider against realised points.

    ``samples`` (n_obs, n_sims) enables a true CRPS and the event Briers. A
    point forecast gets CRPS = MAE, which the estimator in
    ``eval.calibration.crps_ensemble`` reduces to exactly, so the column stays
    comparable across providers that do and do not simulate.
    """
    predicted = np.asarray(predicted, dtype=float)
    actual = np.asarray(actual, dtype=float)
    if predicted.shape != actual.shape:
        raise ValueError(f"{provider}: {predicted.shape} predictions vs {actual.shape}")
    resid = predicted - actual
    mae = float(np.mean(np.abs(resid)))
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    corr = float(np.corrcoef(predicted, actual)[0, 1]) if predicted.std() > 0 else float("nan")

    crps = brier_haul = brier_blank = ll_return = None
    if samples is not None:
        samples = np.asarray(samples, dtype=float)
        crps = crps_ensemble(samples, actual)
        brier_haul = brier_score((samples >= 10).mean(axis=1), (actual >= 10).astype(int))
        brier_blank = brier_score((samples <= 2).mean(axis=1), (actual <= 2).astype(int))
        ll_return = log_loss((samples >= 5).mean(axis=1), (actual >= 5).astype(int))
    else:
        crps = mae  # a point forecast's CRPS is its absolute error
    return ProviderScore(provider, int(len(actual)), mae, rmse, crps,
                         brier_haul, brier_blank, ll_return,
                         float(np.mean(resid)), corr)


def fit_stacking(panel: pd.DataFrame, providers: list[str], *,
                 target: str = "actual") -> dict[str, float]:
    """Non-negative, sum-to-one least squares of ``target`` on the providers.

    Rows with any provider missing are dropped: a joint regression cannot fit a
    coefficient on a column it cannot see, and imputing the mean would let a
    provider inherit its rivals' skill.
    """
    usable = panel.dropna(subset=[*providers, target])
    if usable.empty:
        raise ValueError("no rows where every provider and the target are present")
    X = usable[providers].to_numpy(dtype=float)
    y = usable[target].to_numpy(dtype=float)
    # Sum-to-one enforced by an augmented row rather than by post-hoc rescaling.
    # Rescaling an unconstrained NNLS solution changes the fit it claims to be.
    penalty = 1e3 * float(np.abs(y).mean() + 1.0)
    X_aug = np.vstack([X, np.full((1, X.shape[1]), penalty)])
    y_aug = np.concatenate([y, [penalty]])
    coef, _ = nnls(X_aug, y_aug)
    total = coef.sum()
    if total <= 0:
        raise ValueError("stacking produced an all-zero weight vector")
    return {p: float(w / total) for p, w in zip(providers, coef)}


def fit_inverse_loss(scores: dict[str, ProviderScore], *,
                     metric: str = "mae", power: float = 2.0) -> dict[str, float]:
    """Weight proportional to ``loss ** -power``.

    ``power=2`` rather than 1: with independent unbiased estimators the
    variance-minimising weights go as the inverse *variance*, and loss is on the
    scale of a standard deviation.
    """
    losses = {}
    for name, score in scores.items():
        value = getattr(score, metric)
        if value is None or not np.isfinite(value) or value <= 0:
            raise ValueError(f"{name}: {metric} is {value!r}, cannot invert it")
        losses[name] = float(value)
    raw = {n: v ** -power for n, v in losses.items()}
    total = sum(raw.values())
    return {n: v / total for n, v in raw.items()}


def earn_weights(
    panel: pd.DataFrame,
    providers: list[str],
    *,
    holdout: str,
    samples: dict[str, np.ndarray] | None = None,
    unearned: tuple[str, ...] = (),
    method: str = "stacking",
    target: str = "actual",
) -> EnsembleWeights:
    """Score every provider on held-out data and fit weights from the result."""
    scores: dict[str, ProviderScore] = {}
    for name in providers:
        rows = panel.dropna(subset=[name, target])
        smp = None
        if samples and name in samples:
            smp = samples[name][rows.index.to_numpy()]
        scores[name] = score_provider(
            name, rows[name].to_numpy(), rows[target].to_numpy(), samples=smp
        )
    if method == "stacking":
        weights = fit_stacking(panel, providers, target=target)
    elif method == "inverse_loss":
        weights = fit_inverse_loss(scores)
    else:
        raise ValueError(f"unknown weighting method {method!r}")
    return EnsembleWeights(weights=weights, scores=scores, method=method,
                           holdout=holdout, unearned=tuple(unearned))
