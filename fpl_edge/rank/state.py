"""Where the manager stands, in the only numbers the objective needs.

``P(final rank <= 10,000)`` has a **two-dimensional sufficient statistic**: the
deficit ``D`` against the running top-10k pace, and the number of remaining
gameweeks ``tau`` (``docs/platform/rank_objectives.md`` §0.1, §1). Everything
rank-aware the weekly solver does is a function of ``(D, tau)`` plus the two
moments of the *relative* weekly increment.

The load-bearing definition in this module is the second moment. Risk in rank
terms is not the volatility of my own score; it is the volatility of the
**difference** between my score and the pace increment::

    m = E[my weekly score - weekly pace increment]      edge vs the BAR
    s = SD[my weekly score - weekly pace increment]     effective volatility

    s^2 = var(mine) + var(pace) - 2 * cov(mine, pace)

A full-template squad has own-score SD ~15 pts/week but co-moves almost
perfectly with the bar, so its effective ``s`` is a few points; a differential
squad decorrelates and its ``s`` is large even at a similar own-score SD
(§1). Using own-score SD anywhere in the rank layer is the bug this module
exists to prevent, so :class:`RankState` stores ``s`` and never ``sd_mine``.

The study closes by naming its own gap (§8, "Main threat to validity"):

    the first production task is estimating effective ``s`` per squad from the
    simulator's own paired draws -- the one number this study could not derive
    from first principles.

:func:`deficit_moments` is that task. It takes paired draws -- my squad and the
pace scored on the *same* Monte Carlo point draws, which is what
:class:`fpl_edge.sim.engine.SeasonSimulator` produces by construction -- and
returns ``(m, s)`` with the covariance decomposition attached, so a caller can
see how much of ``s`` came from decorrelation rather than from own variance.

Anchoring, per the task's two regimes:

* **Pre-GW1** the honest state is ``D = 0``, ``tau = 38``: nobody has played, so
  no deficit exists to measure. :meth:`RankState.preseason`.
* **In-season**, ``D`` comes from live overall standings once gameweeks
  finalise -- my total minus the interpolated 10,000th-ranked total.
  :meth:`RankState.from_standings`.

Both carry a ``provenance`` string, because a ``theta`` computed off a
simulator-calibrated pace and one computed off measured standings are different
epistemic objects and a report that cannot tell them apart is lying.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.stats import norm

#: Pre-GW1: no gameweek has been scored, so the deficit is exactly zero and the
#: full 38-gameweek horizon remains. Not an assumption -- an identity.
PROVENANCE_PRESEASON = "preseason:D=0,tau=38"

#: ``(m, s)`` estimated from the simulator's paired draws for a specific squad.
PROVENANCE_SIM_PAIRED = "sim_paired_draws"

#: ``D`` measured from finalised overall standings (my total minus the
#: interpolated 10,000th-ranked total).
PROVENANCE_LIVE_STANDINGS = "live_overall_standings"

#: ``(m, s)`` taken from the study's stylised archetype menu rather than
#: measured. Legitimate as a bootstrap, never as a reported calibration.
PROVENANCE_STYLISED_MENU = "stylised_archetype_menu:rank_objectives.md#1"

#: The rank threshold the objective is defined against.
TOP_K = 10_000

#: Gameweeks in a season. Used only by :meth:`RankState.preseason`.
SEASON_GWS = 38


@dataclass(frozen=True, slots=True)
class DeficitMoments:
    """The two moments of ``(my weekly score - weekly pace increment)``.

    ``s`` is the number the whole rank layer turns on, and the decomposition is
    kept beside it so that "this squad is risky" can be attributed: a large ``s``
    from a large ``var_mine`` is a volatile squad, a large ``s`` from a small
    ``cov`` is a *decorrelated* one, and only the second is what buys tail
    probability from behind (§1).
    """

    #: Weekly mean edge over the pace. Small even for a good model, because the
    #: bar is set by the field's right tail, not by the average manager (§1).
    m: float
    #: Weekly SD of the difference. THE effective volatility.
    s: float
    var_mine: float
    var_pace: float
    cov: float
    n_sims: int
    n_gws: int

    def __post_init__(self) -> None:
        if self.s <= 0.0:
            raise ValueError(
                f"effective weekly SD must be positive, got {self.s}. A squad that "
                "tracks the pace exactly has no route to any rank but the one it "
                "already holds, and theta would divide by zero."
            )
        if self.n_gws < 1 or self.n_sims < 2:
            raise ValueError("need at least one gameweek and two simulations")

    @property
    def correlation(self) -> float:
        """Corr(my weekly score, weekly pace increment).

        Near 1 is a template; near 0 is a squad that has decorrelated from the
        bar. This is the covariance channel §1 says dominates.
        """
        denom = math.sqrt(max(self.var_mine * self.var_pace, 0.0))
        return float(self.cov / denom) if denom > 0.0 else float("nan")

    @property
    def season_deficit_sd(self) -> float:
        """``s * sqrt(38)`` -- the column the study's archetype table quotes."""
        return self.s * math.sqrt(SEASON_GWS)

    def check_decomposition(self, *, tol: float = 1e-6) -> None:
        """Assert ``s^2 == var_mine + var_pace - 2 cov`` to numerical tolerance.

        Cheap, and it catches the one mistake that would silently poison every
        downstream ``theta``: estimating the three pieces on draws that were not
        actually paired.
        """
        implied = self.var_mine + self.var_pace - 2.0 * self.cov
        if abs(implied - self.s**2) > tol * max(1.0, abs(implied)):
            raise ValueError(
                f"variance decomposition does not close: s^2={self.s**2:.9g} but "
                f"var_mine + var_pace - 2cov = {implied:.9g}. The draws are not "
                "paired (my squad and the pace must be scored on the SAME "
                "simulations)."
            )


def deficit_moments(
    my_draws: np.ndarray,
    pace_draws: np.ndarray,
    *,
    ddof: int = 1,
) -> DeficitMoments:
    """Estimate ``(m, s)`` from PAIRED per-gameweek draws.

    Parameters
    ----------
    my_draws
        ``(n_gws, n_sims)`` -- my squad's gameweek score in each simulation.
        A ``(n_sims,)`` array is read as a single gameweek.
    pace_draws
        ``(n_gws, n_sims)`` -- the top-10k pace *increment* in each simulation,
        on the same draws, in the same order. See :func:`pace_increments`.

    The pairing is the whole point. Column ``k`` of both arrays must be the same
    simulated world; that is what makes ``cov`` estimable at all, and it is what
    :class:`~fpl_edge.sim.engine.SeasonSimulator` gives for free by scoring my
    squad and every rival against one set of point draws.

    Moments are computed per gameweek and averaged, not pooled across the
    flattened array. Pooling would fold the week-to-week variation in mean
    fixture difficulty into ``s`` and inflate it, and ``s`` is exactly the
    number that must not be inflated: the switch boundary is linear in it (§3).
    """
    my = np.atleast_2d(np.asarray(my_draws, dtype=np.float64))
    pace = np.atleast_2d(np.asarray(pace_draws, dtype=np.float64))
    if my.shape != pace.shape:
        raise ValueError(
            f"paired draws must have identical shapes, got {my.shape} and "
            f"{pace.shape}. Unequal shapes mean the draws are not paired."
        )
    n_gws, n_sims = my.shape
    if n_sims < 2:
        raise ValueError("need at least two simulations to estimate a variance")

    diff = my - pace
    var_mine = float(np.var(my, axis=1, ddof=ddof).mean())
    var_pace = float(np.var(pace, axis=1, ddof=ddof).mean())
    var_diff = float(np.var(diff, axis=1, ddof=ddof).mean())
    # cov taken per gameweek with the same ddof, so the decomposition closes
    # exactly rather than approximately.
    covs = [
        float(np.cov(my[g], pace[g], ddof=ddof)[0, 1]) for g in range(n_gws)
    ]
    return DeficitMoments(
        m=float(diff.mean()),
        s=math.sqrt(max(var_diff, 0.0)),
        var_mine=var_mine,
        var_pace=var_pace,
        cov=float(np.mean(covs)),
        n_sims=int(n_sims),
        n_gws=int(n_gws),
    )


def pace_increments(cumulative_pace: np.ndarray) -> np.ndarray:
    """Per-gameweek pace increments from the cumulative pace path.

    ``Q_t`` -- the cumulative score of the interpolated 10,000th-ranked manager
    at week ``t`` -- is defined on *cumulative* totals (§1), because rank is a
    property of the season total, not of any single week. So the honest weekly
    increment is ``Q_t - Q_{t-1}``, taken after the quantile, and NOT the
    within-week quantile of weekly scores (the quantile of a sum is not the sum
    of quantiles, and the two differ by more than the effect being measured).

    Parameters
    ----------
    cumulative_pace
        ``(n_gws, n_sims)``, ``Q_t`` per simulation, ascending in ``t``.
    """
    q = np.atleast_2d(np.asarray(cumulative_pace, dtype=np.float64))
    return np.diff(q, axis=0, prepend=np.zeros((1, q.shape[1])))


def pace_path(
    cumulative_rival_totals: np.ndarray,
    *,
    field_size: int,
    threshold: int = TOP_K,
) -> np.ndarray:
    """``Q_t`` from simulated cumulative rival totals.

    Parameters
    ----------
    cumulative_rival_totals
        ``(n_gws, n_rivals, n_sims)`` -- each rival's *cumulative* score after
        each gameweek, all on the same draws.

    The 10,000th of ``field_size`` managers sits at the ``1 - threshold /
    field_size`` quantile, taken **within** each simulation: everyone in a given
    season experiences the same match results, so pooling across simulations
    first would fold season-to-season variation into the bar and flatten it
    (the same argument :meth:`SeasonSimulator.field_rank_ladder` makes).
    """
    arr = np.asarray(cumulative_rival_totals, dtype=np.float64)
    if arr.ndim != 3:
        raise ValueError(
            f"expected (n_gws, n_rivals, n_sims), got shape {arr.shape}"
        )
    if not 0 < threshold < field_size:
        raise ValueError(f"threshold {threshold} must lie inside a field of {field_size}")
    q = 1.0 - threshold / field_size
    return np.quantile(arr, q, axis=1)


@dataclass(frozen=True, slots=True)
class RankState:
    """``(D, tau)`` plus the relative moments that turn them into a posture.

    This object is the input to every rule in :mod:`fpl_edge.rank.policy` and
    the thing a recommendation must carry so the reader can see which branch of
    the law produced it (§7.2: a verdict quoted without its state overreaches).
    """

    #: ``D`` -- my cumulative score minus the top-10k pace. Negative is behind.
    deficit: float
    #: ``tau`` -- gameweeks remaining, including the one being decided.
    tau: int
    #: ``s`` -- effective weekly SD of (my score - pace increment). NOT own-score SD.
    s_weekly: float
    #: ``m`` -- weekly mean edge over the pace.
    m_weekly: float
    #: How ``deficit`` and the moments were obtained. Never omitted.
    provenance: str
    threshold: int = TOP_K
    notes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.tau < 1:
            raise ValueError(
                f"tau must be at least 1, got {self.tau}. With no gameweeks left "
                "there is no decision to make and theta is undefined."
            )
        if self.s_weekly <= 0.0:
            raise ValueError(f"s_weekly must be positive, got {self.s_weekly}")
        if not self.provenance:
            raise ValueError(
                "a RankState without provenance is not auditable; say where D and "
                "the moments came from"
            )

    # -- constructors --------------------------------------------------------

    @classmethod
    def preseason(
        cls,
        moments: DeficitMoments,
        *,
        tau: int = SEASON_GWS,
        threshold: int = TOP_K,
        notes: tuple[str, ...] = (),
    ) -> RankState:
        """The honest pre-GW1 state: ``D = 0``, ``tau = 38``.

        No gameweek has been scored, so there is no deficit to measure and none
        is invented. The moments still come from the simulator, because ``(m, s)``
        are properties of the squad and the field, not of the standings.
        """
        return cls(
            deficit=0.0,
            tau=int(tau),
            s_weekly=moments.s,
            m_weekly=moments.m,
            provenance=f"{PROVENANCE_PRESEASON}+{PROVENANCE_SIM_PAIRED}",
            threshold=threshold,
            notes=notes,
        )

    @classmethod
    def from_standings(
        cls,
        *,
        my_total: float,
        pace_total: float,
        tau: int,
        moments: DeficitMoments,
        threshold: int = TOP_K,
        notes: tuple[str, ...] = (),
    ) -> RankState:
        """``D`` from finalised overall standings; moments from paired draws.

        ``pace_total`` is the cumulative score of the interpolated
        ``threshold``-ranked manager as published, not a simulated one. Once
        gameweeks finalise this is measurable and the simulator's pace estimate
        should stop being used for ``D`` (it is still needed for ``m`` and ``s``,
        which are forward-looking).
        """
        return cls(
            deficit=float(my_total) - float(pace_total),
            tau=int(tau),
            s_weekly=moments.s,
            m_weekly=moments.m,
            provenance=f"{PROVENANCE_LIVE_STANDINGS}+{PROVENANCE_SIM_PAIRED}",
            threshold=threshold,
            notes=notes,
        )

    @classmethod
    def stylised(
        cls,
        *,
        deficit: float,
        tau: int,
        m_weekly: float,
        s_weekly: float,
        threshold: int = TOP_K,
        notes: tuple[str, ...] = (),
    ) -> RankState:
        """A state built from the study's stylised menu rather than measurement.

        Legitimate for tests, sensitivity sweeps and cold starts. The provenance
        label says so, and every report that prints a state prints its
        provenance, so a stylised ``theta`` cannot be mistaken for a measured one.
        """
        return cls(
            deficit=float(deficit),
            tau=int(tau),
            s_weekly=float(s_weekly),
            m_weekly=float(m_weekly),
            provenance=PROVENANCE_STYLISED_MENU,
            threshold=threshold,
            notes=notes,
        )

    # -- the derived quantities every rule uses ------------------------------

    @property
    def sigma(self) -> float:
        """``Sigma = s * sqrt(tau)`` -- SD of the final deficit.

        The F2 derivation (§2) writes ``Sigma^2 = s_1^2 + s_bar^2 (tau - 1)`` to
        let this week's candidate differ from the rest-of-season baseline. At the
        *state* level no candidate has been chosen yet, so ``s_1 = s_bar = s``
        and it collapses to ``s * sqrt(tau)``. Candidate-specific Sigma is
        :func:`fpl_edge.rank.policy.sigma_with_candidate`.
        """
        return self.s_weekly * math.sqrt(self.tau)

    @property
    def expected_final_margin(self) -> float:
        """``L = D + m * tau`` -- the final margin if nothing changes.

        The sign of this is the whole switch law: variance helps iff ``L < 0``
        (§3, ``dP/ds > 0 <=> D + m tau < 0``).
        """
        return self.deficit + self.m_weekly * self.tau

    @property
    def z(self) -> float:
        """``z = (D + m tau) / Sigma`` -- the standardised final margin."""
        return self.expected_final_margin / self.sigma

    @property
    def p_hit(self) -> float:
        """``Phi(z)`` -- P(final rank <= threshold) holding the current posture.

        Exact under the Gaussian reduction the study uses throughout (CLT over
        ``tau`` weekly increments), and MC-verified in ``rank_policy_mc.csv``.
        """
        return float(norm.cdf(self.z))

    @property
    def behind(self) -> bool:
        """``D + m tau < 0`` -- the variance-seeking branch of §3's law."""
        return self.expected_final_margin < 0.0

    def with_tau(self, tau: int) -> RankState:
        """The same posture with a different number of weeks left."""
        return RankState(
            deficit=self.deficit,
            tau=int(tau),
            s_weekly=self.s_weekly,
            m_weekly=self.m_weekly,
            provenance=self.provenance,
            threshold=self.threshold,
            notes=self.notes,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "deficit": self.deficit,
            "tau": self.tau,
            "s_weekly": self.s_weekly,
            "m_weekly": self.m_weekly,
            "sigma": self.sigma,
            "expected_final_margin": self.expected_final_margin,
            "z": self.z,
            "p_hit": self.p_hit,
            "behind": self.behind,
            "threshold": self.threshold,
            "provenance": self.provenance,
            "notes": list(self.notes),
        }

    def describe(self) -> str:
        side = "behind" if self.behind else "ahead of"
        return (
            f"D={self.deficit:+.1f} with tau={self.tau} left "
            f"(m={self.m_weekly:+.2f}/wk, s={self.s_weekly:.2f}/wk) "
            f"=> L={self.expected_final_margin:+.1f}, {side} the pace on "
            f"expectation; P(top {self.threshold:,}) = {self.p_hit:.3f} "
            f"[{self.provenance}]"
        )
