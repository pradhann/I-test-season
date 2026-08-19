"""How ownership moves between one deadline and the next.

Two regimes, because they are driven by different mechanisms and only one of
them has a transfer flow to lean on.

**In-season (GW >= 2).** Ownership changes by an accounting identity::

    o_p(g+1) = w * o_p(g) + (1 - w) * q_p + netflow_p / N(g+1)

where ``w = N(g)/N(g+1)`` is the dilution weight, ``q`` is the squad a newly
registered manager picks, and ``netflow`` is transfers in minus out. Only the
flow term is behavioural; the rest is arithmetic that persistence ignores. On
top of that sit three empirical effects, all fitted on real panels:

* **flow carry-over** -- last window's net flow partly repeats,
* **form chasing** -- the previous gameweek's points pull transfers in,
* **overshoot correction** -- the change in ownership over the *last* window
  partially reverses, i.e. the herd overshoots.

**Cold start (GW1).** There is no flow: ``transfers_in_event`` is identically
zero before the first deadline, and ``cost_change_start`` is zero too because
prices do not move before the season starts. The only live signals are the
ownership level itself, the registration count, FPL's own ``ep_next``
projection and availability flags. The measured structure, out of four real
seasons of pre-deadline snapshots, is a **sign flip with horizon**: far from
the deadline ownership *disperses* (heavily owned players lose share), close to
it the template *consolidates* (they gain). That flip is why one linear model
across all horizons cannot work, and why the fit carries two coefficient blocks
with a blend across a knot.

Everything here operates on plain arrays. The Snapshot-facing model in
``model.py`` is the only thing that touches the warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Mapping

import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear

from fpl_edge.models.ownership.field import project_to_simplex

#: In-season design matrix column order. Fixed, because the fitted coefficient
#: vector is stored positionally in params.json.
INSEASON_FEATURES = (
    "dilution",    # (w - 1) * own      -- mechanical share loss as the field grows
    "flow",        # net transfer share -- last window's flow, carried over
    "form",        # pts * sqrt(own)    -- form chasing, scaled by visibility
    "price_move",  # dvalue * own       -- price-change pressure
    "overshoot",   # own - own_prev     -- last window's move, partially reversed
    "level",       # own                -- mean reversion toward a flatter field
    "points",      # pts                -- form chasing, unscaled
)

#: Cold-start design matrix column order. Every column is multiplied by ``own``
#: so the predicted move scales with how much ownership there is to move.
COLDSTART_FEATURES = (
    "level",          # own
    "concentration",  # own * (own - mean own)  -- disperse early, consolidate late
    "projection",     # own * z(ep_next)        -- the crowd chases the projection
    "flagged",        # own * 1{status != 'a'}  -- injury news gets sold
    "drift",          # observed drift rate * days remaining
)

#: The drift feature needs two polls of the ownership series. Before the first
#: deadline that is the ONLY momentum signal in existence -- ``transfers_in_event``
#: is identically zero for every player until the deadline passes, and
#: ``cost_change_start`` is zero too because prices do not move before the season
#: starts. Measured on real pre-deadline snapshots, extrapolating this drift is
#: worth more than every structural feature combined.

#: Horizon in days below which the "near deadline" coefficient block applies in
#: full, and above which the "far" block does. Between the two the blocks are
#: blended linearly in days so the forecast has no discontinuity.
NEAR_KNOT_DAYS = 3.0
FAR_KNOT_DAYS = 8.0

#: Sign constraints on the cold-start coefficients, in COLDSTART_FEATURES order.
#:
#: The near-horizon block is fitted on two real snapshots. That is genuinely all
#: the data there is -- pre-deadline ownership tables only survive where somebody
#: happened to commit one -- and five free parameters on two snapshots with
#: correlated columns will happily produce a coefficient saying injured players
#: get *bought*. Observed: an unconstrained fit put the flag coefficient at
#: +0.24, which forecast a knee injury three days before the deadline as a 24%
#: ownership *rise*.
#:
#: So three of the five carry the sign the mechanism dictates. This is domain
#: knowledge doing the job a larger sample would otherwise do, and it is stated
#: rather than smuggled in as a prior.
COLDSTART_BOUNDS: tuple[tuple[float, float], ...] = (
    (-np.inf, np.inf),   # level: reversion may go either way
    (-np.inf, np.inf),   # concentration: disperses early, consolidates late
    (0.0, np.inf),       # projection: a better projection cannot lose owners
    (-np.inf, 0.0),      # flagged: an injury cannot attract buyers
    (0.0, np.inf),       # drift: momentum continues or dies, it does not invert
)

#: Pre-deadline ownership drift grows as ``days ** HORIZON_EXPONENT``. Fitted on
#: the eight real pre-deadline snapshots in the committed fixture: regressing
#: log(persistence MAE) on log(days-to-deadline) gives an exponent of 0.505 with
#: R-squared 0.98, i.e. ownership diffuses like a square-root-of-time random
#: walk. The structural columns are scaled by it so a block fitted at a one-day
#: horizon is not applied unchanged at three days.
HORIZON_EXPONENT = 0.5


def _z(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    sd = x.std()
    return (x - x.mean()) / sd if sd > 1e-12 else np.zeros_like(x)


# --------------------------------------------------------------------------
# in-season
# --------------------------------------------------------------------------


def inseason_design(
    own: np.ndarray,
    own_prev: np.ndarray,
    flow: np.ndarray,
    points: np.ndarray,
    dvalue: np.ndarray,
    w: float | np.ndarray,
) -> np.ndarray:
    """Build the in-season design matrix, columns in :data:`INSEASON_FEATURES`."""
    own = np.asarray(own, dtype=float)
    own_prev = np.asarray(own_prev, dtype=float)
    flow = np.asarray(flow, dtype=float)
    points = np.asarray(points, dtype=float)
    dvalue = np.asarray(dvalue, dtype=float)
    wv = np.broadcast_to(np.asarray(w, dtype=float), own.shape)
    return np.column_stack([
        (wv - 1.0) * own,
        flow,
        points * np.sqrt(np.clip(own, 0.0, None)),
        dvalue * own,
        own - own_prev,
        own,
        points,
    ])


@dataclass(frozen=True, slots=True)
class ScaleParams:
    """Heteroscedastic predictive scale, ``sd = a * (own + eps)^b``.

    Ownership error is emphatically not homoscedastic: a 0.2% player cannot
    move by 3 percentage points and a 60% player routinely does. ``interval_k``
    maps a nominal coverage to the multiple of ``sd`` that achieved it on the
    training residuals, which keeps the intervals honest without assuming
    Gaussian tails (they are not Gaussian; ownership moves are fat-tailed).
    """

    a: float
    b: float
    eps: float = 1e-4
    interval_k: Mapping[str, float] = dc_field(default_factory=dict)

    def sd(self, own: np.ndarray) -> np.ndarray:
        return self.a * (np.asarray(own, dtype=float) + self.eps) ** self.b

    def k(self, coverage: float) -> float:
        key = f"{coverage:.2f}"
        if key not in self.interval_k:
            raise KeyError(f"no calibrated interval multiplier for coverage {coverage}")
        return float(self.interval_k[key])


@dataclass(frozen=True, slots=True)
class InSeasonParams:
    coef: tuple[float, ...]
    intercept: float
    scale: ScaleParams

    def __post_init__(self) -> None:
        if len(self.coef) != len(INSEASON_FEATURES):
            raise ValueError(
                f"expected {len(INSEASON_FEATURES)} coefficients, got {len(self.coef)}"
            )


def inseason_predict(
    params: InSeasonParams,
    own: np.ndarray,
    own_prev: np.ndarray,
    flow: np.ndarray,
    points: np.ndarray,
    dvalue: np.ndarray,
    w: float,
    *,
    total: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(mean, sd)`` ownership shares at the next deadline.

    ``total`` projects the forecast back onto the simplex. Pass it **only** when
    the input covers the whole player set, where ``sum_p own_p == 15`` is an
    identity. Projecting a *subset* to its own current sum is actively harmful:
    it forces the aggregate share of that subset to be unchanged, which cancels
    exactly the dilution and mean-reversion terms the model exists to capture.
    Measured cost of getting this wrong on the evaluation panel: the lift over
    persistence falls from 18.7% to 4.8%.
    """
    own = np.asarray(own, dtype=float)
    X = inseason_design(own, own_prev, flow, points, dvalue, w)
    raw = own + X @ np.asarray(params.coef, dtype=float) + params.intercept
    raw = np.clip(raw, 0.0, 1.0)
    mean = raw if total is None else project_to_simplex(raw, total=total)
    return mean, params.scale.sd(own)


# --------------------------------------------------------------------------
# cold start
# --------------------------------------------------------------------------


def coldstart_design(
    own: np.ndarray,
    ep_next: np.ndarray,
    flagged: np.ndarray,
    drift_rate: np.ndarray | None,
    days: float,
) -> np.ndarray:
    """Build the cold-start design matrix, columns in :data:`COLDSTART_FEATURES`.

    ``drift_rate`` is the observed change in ownership share per day, measured
    between two polls. Pass ``None`` when only one observation exists; the
    column is then zero and the model degrades gracefully to the structural
    terms rather than inventing a trend from a single point.
    """
    own = np.asarray(own, dtype=float)
    ep = np.asarray(ep_next, dtype=float)
    fl = np.asarray(flagged, dtype=float)
    if drift_rate is None:
        dr = np.zeros_like(own)
    else:
        dr = np.nan_to_num(np.asarray(drift_rate, dtype=float), nan=0.0)
    mean_own = own.mean()
    horizon = max(float(days), 1e-6) ** HORIZON_EXPONENT
    return np.column_stack([
        horizon * own,
        horizon * own * (own - mean_own),
        horizon * own * _z(ep),
        horizon * own * fl,
        dr * days,
    ])


@dataclass(frozen=True, slots=True)
class ColdStartParams:
    """Two coefficient blocks plus a horizon-dependent shrink and scale.

    ``shrink_per_day`` is the daily rate at which the aggregate share held by
    *currently known* players leaks away to players who have not yet been added
    to the game. It is small (order 0.1%/day) but it is a bias, not noise, and
    a forecast that ignores it is systematically high on every player.
    """

    coef_near: tuple[float, ...]
    coef_far: tuple[float, ...]
    #: The same blocks refitted with the drift column removed. A coefficient
    #: vector fitted alongside a strong feature is wrong when that feature is
    #: absent -- the structural terms absorb none of the variance the drift term
    #: was carrying. So the no-drift case gets its own fit rather than the
    #: with-drift coefficients and a zeroed column.
    shrink_per_day: float
    scale_a: float
    scale_b: float
    scale_day_exponent: float
    coef_near_nodrift: tuple[float, ...] = ()
    coef_far_nodrift: tuple[float, ...] = ()
    interval_k: Mapping[str, float] = dc_field(default_factory=dict)

    def __post_init__(self) -> None:
        n = len(COLDSTART_FEATURES)
        if len(self.coef_near) != n or len(self.coef_far) != n:
            raise ValueError(f"cold-start blocks must each have {n} coefficients")
        for name in ("coef_near_nodrift", "coef_far_nodrift"):
            v = getattr(self, name)
            if v and len(v) != n:
                raise ValueError(f"{name} must have {n} coefficients")

    def blend(self, days: float, *, with_drift: bool = True) -> np.ndarray:
        """Coefficients at a given horizon, blended across the knot."""
        if days <= NEAR_KNOT_DAYS:
            t = 0.0
        elif days >= FAR_KNOT_DAYS:
            t = 1.0
        else:
            t = (days - NEAR_KNOT_DAYS) / (FAR_KNOT_DAYS - NEAR_KNOT_DAYS)
        if with_drift or not self.coef_near_nodrift:
            near = np.asarray(self.coef_near, dtype=float)
            far = np.asarray(self.coef_far, dtype=float)
        else:
            near = np.asarray(self.coef_near_nodrift, dtype=float)
            far = np.asarray(self.coef_far_nodrift, dtype=float)
        return (1.0 - t) * near + t * far

    def sd(self, own: np.ndarray, days: float) -> np.ndarray:
        own = np.asarray(own, dtype=float)
        horizon = max(days, 1e-3) ** self.scale_day_exponent
        return self.scale_a * horizon * (own + 1e-4) ** self.scale_b

    def k(self, coverage: float) -> float:
        key = f"{coverage:.2f}"
        if key not in self.interval_k:
            raise KeyError(f"no calibrated interval multiplier for coverage {coverage}")
        return float(self.interval_k[key])


def coldstart_predict(
    params: ColdStartParams,
    own: np.ndarray,
    ep_next: np.ndarray,
    flagged: np.ndarray,
    days_to_deadline: float,
    drift_rate: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(mean, sd)`` ownership shares at the GW1 deadline."""
    if days_to_deadline < 0:
        raise ValueError("days_to_deadline must be non-negative")
    own = np.asarray(own, dtype=float)
    X = coldstart_design(own, ep_next, flagged, drift_rate, days_to_deadline)
    raw = own + X @ params.blend(days_to_deadline, with_drift=drift_rate is not None)
    shrink = float(np.exp(-params.shrink_per_day * days_to_deadline))
    mean = project_to_simplex(raw, total=float(own.sum()) * shrink)
    return mean, params.sd(own, days_to_deadline)


# --------------------------------------------------------------------------
# fitting
# --------------------------------------------------------------------------


def _fit_scale(resid: np.ndarray, own: np.ndarray, eps: float = 1e-4) -> tuple[float, float]:
    """Fit ``|resid| ~ a * (own + eps)^b`` by least squares in logs."""
    r = np.abs(np.asarray(resid, dtype=float))
    o = np.asarray(own, dtype=float) + eps
    keep = r > 1e-12
    if keep.sum() < 10:
        return float(r.mean() or 1e-4), 0.0
    b, log_a = np.polyfit(np.log(o[keep]), np.log(r[keep]), 1)
    # |resid| for a half-normal has mean sd*sqrt(2/pi); rescale to an sd.
    return float(np.exp(log_a) * np.sqrt(np.pi / 2.0)), float(b)


def _calibrate_intervals(
    resid: np.ndarray, sd: np.ndarray, coverages: tuple[float, ...] = (0.5, 0.8, 0.95)
) -> dict[str, float]:
    """Empirical multiple of ``sd`` containing each nominal coverage."""
    z = np.abs(np.asarray(resid, dtype=float)) / np.maximum(np.asarray(sd, dtype=float), 1e-12)
    return {f"{c:.2f}": float(np.quantile(z, c)) for c in coverages}


def fit_inseason(panel: pd.DataFrame) -> InSeasonParams:
    """Fit the in-season block on a transition panel.

    Required columns: ``own, own_prev, own_next, flow, pts, dvalue, w``.
    """
    need = {"own", "own_prev", "own_next", "flow", "pts", "dvalue", "w"}
    missing = need - set(panel.columns)
    if missing:
        raise KeyError(f"panel is missing columns: {sorted(missing)}")
    X = inseason_design(
        panel["own"].to_numpy(), panel["own_prev"].to_numpy(), panel["flow"].to_numpy(),
        panel["pts"].to_numpy(), panel["dvalue"].to_numpy(), panel["w"].to_numpy(),
    )
    y = (panel["own_next"] - panel["own"]).to_numpy()
    Xa = np.column_stack([X, np.ones(len(X))])
    beta = np.linalg.lstsq(Xa, y, rcond=None)[0]
    resid = y - Xa @ beta
    a, b = _fit_scale(resid, panel["own"].to_numpy())
    scale = ScaleParams(a=a, b=b, interval_k=_calibrate_intervals(resid, a * (panel["own"].to_numpy() + 1e-4) ** b))
    return InSeasonParams(coef=tuple(beta[:-1]), intercept=float(beta[-1]), scale=scale)


def fit_coldstart(pairs: pd.DataFrame) -> ColdStartParams:
    """Fit the cold-start block on pre-deadline snapshot / realised GW1 pairs.

    Required columns: ``season, days, own, own_true, ep, flag``. Rows are
    grouped by ``(season, days)`` because the design is built per snapshot
    (the concentration and projection features are cross-sectional).
    """
    need = {"season", "days", "own", "own_true", "ep", "flag"}
    missing = need - set(pairs.columns)
    if missing:
        raise KeyError(f"cold-start pairs missing columns: {sorted(missing)}")
    has_drift = "drift_rate" in pairs.columns

    blocks: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {"near": [], "far": []}
    shrink_obs: list[tuple[float, float]] = []
    for (_season, days), g in pairs.groupby(["season", "days"], sort=True):
        own = g["own"].to_numpy()
        dr = g["drift_rate"].to_numpy() if has_drift else None
        X = coldstart_design(own, g["ep"].to_numpy(), g["flag"].to_numpy(), dr, float(days))
        y = g["own_true"].to_numpy() - own
        blocks["near" if days <= NEAR_KNOT_DAYS else "far"].append((X, y))
        ratio = g["own_true"].sum() / own.sum()
        if ratio > 0 and days > 0:
            shrink_obs.append((float(days), float(-np.log(ratio))))

    n_feat = len(COLDSTART_FEATURES)
    i_flag = COLDSTART_FEATURES.index("flagged")
    i_drift = COLDSTART_FEATURES.index("drift")

    def solve(*, drop_drift: bool) -> tuple[np.ndarray, np.ndarray]:
        """Fit both horizon blocks jointly with a SHARED availability effect.

        Being flagged makes the field sell you. That is one mechanism, not two,
        and it does not reverse with the forecast horizon -- so the coefficient
        is shared across the blocks rather than estimated twice. It is also the
        column the near block has least data to identify, which is exactly why
        the unconstrained per-block fit produced a sign-flipped estimate.
        """
        rows_X, rows_y = [], []
        for key, offset in (("near", 0), ("far", n_feat - 1)):
            if not blocks[key]:
                continue
            Xb = np.vstack([b[0] for b in blocks[key]]).copy()
            yb = np.concatenate([b[1] for b in blocks[key]])
            if drop_drift:
                Xb[:, i_drift] = 0.0
            # 2 * (n_feat - 1) block-specific columns, then one shared flag.
            wide = np.zeros((len(Xb), 2 * (n_feat - 1) + 1))
            cols = [c for c in range(n_feat) if c != i_flag]
            for j, c in enumerate(cols):
                wide[:, offset + j] = Xb[:, c]
            wide[:, -1] = Xb[:, i_flag]
            rows_X.append(wide)
            rows_y.append(yb)
        X = np.vstack(rows_X)
        y = np.concatenate(rows_y)
        cols = [c for c in range(n_feat) if c != i_flag]
        bounds_lo = [COLDSTART_BOUNDS[c][0] for c in cols] * 2 + [COLDSTART_BOUNDS[i_flag][0]]
        bounds_hi = [COLDSTART_BOUNDS[c][1] for c in cols] * 2 + [COLDSTART_BOUNDS[i_flag][1]]
        live = np.abs(X).max(axis=0) > 1e-12
        beta = np.zeros(X.shape[1])
        if live.any():
            idx = np.flatnonzero(live)
            beta[live] = lsq_linear(
                X[:, live], y,
                bounds=(np.array(bounds_lo)[idx], np.array(bounds_hi)[idx]),
            ).x
        out = []
        for offset in (0, n_feat - 1):
            block = np.zeros(n_feat)
            for j, c in enumerate(cols):
                block[c] = beta[offset + j]
            block[i_flag] = beta[-1]
            out.append(block)
        return out[0], out[1]

    coef_near, coef_far = solve(drop_drift=False)
    coef_near_nd, coef_far_nd = solve(drop_drift=True)
    d = np.array([s[0] for s in shrink_obs])
    L = np.array([s[1] for s in shrink_obs])
    shrink_rate = float(np.clip((d @ L) / (d @ d), 0.0, 0.05)) if len(d) else 0.0

    # residual scale, pooled with an explicit horizon exponent
    resid, own_all, day_all = [], [], []
    for (_season, days), g in pairs.groupby(["season", "days"], sort=True):
        own = g["own"].to_numpy()
        beta = coef_near if days <= NEAR_KNOT_DAYS else coef_far
        dr = g["drift_rate"].to_numpy() if has_drift else None
        pred = own + coldstart_design(
            own, g["ep"].to_numpy(), g["flag"].to_numpy(), dr, float(days)
        ) @ beta
        pred = project_to_simplex(pred, total=own.sum() * float(np.exp(-shrink_rate * days)))
        resid.append(g["own_true"].to_numpy() - pred)
        own_all.append(own)
        day_all.append(np.full(len(g), float(days)))
    r = np.concatenate(resid); o = np.concatenate(own_all); dd = np.concatenate(day_all)
    # split the scale into an ownership exponent and a horizon exponent
    a0, b0 = _fit_scale(r, o)
    with np.errstate(divide="ignore"):
        lr = np.log(np.maximum(np.abs(r), 1e-12))
    keep = np.abs(r) > 1e-12
    day_exp = float(
        np.polyfit(np.log(np.maximum(dd[keep], 1e-3)),
                   lr[keep] - b0 * np.log(o[keep] + 1e-4), 1)[0]
    )
    day_exp = float(np.clip(day_exp, 0.0, 1.5))
    scale_a = float(a0 / np.maximum(np.mean(dd**day_exp), 1e-9))
    sd = scale_a * dd**day_exp * (o + 1e-4) ** b0
    return ColdStartParams(
        coef_near=tuple(coef_near), coef_far=tuple(coef_far),
        coef_near_nodrift=tuple(coef_near_nd), coef_far_nodrift=tuple(coef_far_nd),
        shrink_per_day=shrink_rate, scale_a=scale_a, scale_b=b0,
        scale_day_exponent=day_exp, interval_k=_calibrate_intervals(r, sd),
    )
