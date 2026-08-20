"""The closed forms. Every constant here cites the study that produced it.

``docs/platform/rank_objectives.md`` reaches one architectural conclusion (§8):
a **F3 -> F2 layered solver**. The strategic layer reads the risk posture off a
straight-line boundary in ``(D, tau)``; the tactical layer converts that posture
into a single scalar ``theta`` and ranks candidates by ``m + theta * s^2``. The
full simulator validates the shortlist and is never the argmax loop.

The evidence for preferring this to anything fancier is blunt (§3):

* the best *static* risk posture loses **9.0pp to 16.4pp** of P(top 10k) against
  the adaptive rule (``rank_policy_mc.csv``);
* exact dynamic programming beats the re-solved myopic closed form by
  **<= 0.1pp** everywhere measured, inside ~1 Monte Carlo SE.

State-dependence is worth an order of magnitude more than look-ahead. So this
module is closed forms, not a DP, and :mod:`fpl_edge.rank.validate` runs the
simulator only as a paired-CRN validator on the shortlist.

Provenance of every number below:

======================================  ==========================================
constant / formula                      source
======================================  ==========================================
archetype menu ``(m, s)``               ``rank_objectives.md`` §1 table; the same
                                        four ``Strategy`` rows as
                                        ``scripts/rank_objective_study.py``
``D* = tau (m_a s_b - m_b s_a)/(s_b-s_a)``  §3; reproduced column-for-column against
                                        ``rank_switchpoint.csv``
slope ``-1.064`` (diff over balanced)   ``rank_switchpoint.csv``,
                                        ``closed_form_diff_over_balanced``
``theta(D, tau) = -z / (2 Sigma)``      §2 (F2), from ``dP/d(s^2) = -phi(z) z / 2 Sigma^2``
``score_i = mu_i + theta (1-2c_i) sigma_i^2``  §4; menu and moments in ``rank_captaincy.csv``
``g* = 4 + L (S' - S)/S``               §5; ``rank_hit_threshold.csv``, sign-checked
                                        in ``rank_hit_threshold_mc.csv``
``lambda`` state gate                   §7.1
======================================  ==========================================

Nothing in this module reads a rule value it could hardcode: the hit cost comes
from the verified rule registry, like everywhere else in the codebase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fpl_edge.rank.state import RankState

# ---------------------------------------------------------------------------
# The archetype menu (F3's action set)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Archetype:
    """A risk posture, as the pair of relative moments it produces.

    F3 never sees a player (§2, F3 "Inputs"): it answers *how much* risk, and
    F2 answers *which player*. That separation is why a mis-calibrated menu
    shifts the boundaries but cannot change their shape.
    """

    name: str
    #: Weekly mean edge vs the top-10k pace.
    m: float
    #: Weekly effective SD of (my score - pace increment).
    s: float


#: The stylised menu from ``rank_objectives.md`` §1, identical to ``STRATS`` in
#: ``scripts/rank_objective_study.py``. Calibration is anchored to the live
#: simulator run in ``docs/models/simulator.md`` §9 (xP-optimal squad season
#: mean 2,217 vs a bar of ~2,196 => m ~= +0.55/wk; own-score weekly SD ~15.2).
#:
#: These are STYLISED and the study says so. They exist to locate boundaries,
#: not to be quoted as measurements; the production path measures ``(m, s)`` per
#: squad with :func:`fpl_edge.rank.state.deficit_moments`. §3's sensitivity
#: result is why the shape survives: the boundary is *linear* in the edges, so
#: halving both edges moves the rule from -1.06 tau to -0.53 tau -- it moves,
#: it does not deform.
TEMPLATE = Archetype("template", -0.30, 3.0)
BALANCED = Archetype("balanced", 0.55, 6.0)
DIFFERENTIAL = Archetype("diff", 0.25, 9.5)
PUNT = Archetype("punt", -0.60, 13.5)

#: Ordered from least to most volatile, which is the order the boundaries in
#: ``rank_switchpoint.csv`` are scanned in.
BASELINE_MENU: tuple[Archetype, ...] = (TEMPLATE, BALANCED, DIFFERENTIAL, PUNT)


def p_hit_holding(archetype: Archetype, deficit: float, tau: int) -> float:
    """``Phi((D + m tau) / (s sqrt(tau)))`` -- hold this posture for all tau weeks.

    The commit-for-the-rest evaluation §3's boundary is derived from.
    """
    from scipy.stats import norm

    return float(norm.cdf((deficit + archetype.m * tau) / (archetype.s * math.sqrt(tau))))


def crossing_deficit(tau: int, a: Archetype, b: Archetype) -> float:
    """The deficit at which two archetypes' z-scores cross (§3).

    ``D* = tau * (m_a s_b - m_b s_a) / (s_a - s_b)``

    Symmetric in ``(a, b)``. **A straight line through the origin**: the drift
    term scales with ``tau`` while dispersion scales with ``sqrt(tau)``, so the
    ratio that decides the comparison is scale-free in ``tau``. That is the
    whole reason a *weekly re-solved* closed form loses almost nothing to a DP
    -- the rule it would learn is already linear.

    With :data:`BALANCED` and :data:`DIFFERENTIAL` this returns ``-1.0642857 *
    tau``: **gamble when more than ~1.06 points behind per remaining gameweek**.
    """
    if a.s == b.s:
        raise ValueError(
            f"{a.name!r} and {b.name!r} have the same effective SD ({a.s}); their "
            "z-scores never cross, so no switch point exists"
        )
    return tau * (a.m * b.s - b.m * a.s) / (a.s - b.s)


def boundary_slope(a: Archetype, b: Archetype) -> float:
    """``D*/tau`` -- the slope of the switch line. ``crossing_deficit(1, a, b)``."""
    return crossing_deficit(1, a, b)


def myopic_best(deficit: float, tau: int, menu: tuple[Archetype, ...] = BASELINE_MENU) -> Archetype:
    """The commit-for-the-rest argmax over the menu -- F3's weekly answer.

    Re-solved every deadline this is the "myopic" policy in
    ``rank_policy_mc.csv``, which scores .8026 from ``(38, 0)`` against .8032 for
    exact DP and .7133 for the best static posture. The 0.06pp it concedes to DP
    is the price of not carrying a value function; the 9.0pp it takes off the
    best static posture is the prize.
    """
    if not menu:
        raise ValueError("the archetype menu is empty")
    return max(menu, key=lambda a: p_hit_holding(a, deficit, tau))


# ---------------------------------------------------------------------------
# F2: the state-dependent risk coefficient
# ---------------------------------------------------------------------------


def sigma_with_candidate(state: RankState, s_candidate: float | None = None) -> float:
    """``Sigma^2 = s_1^2 + s_bar^2 (tau - 1)`` (§2).

    This week's candidate may have a different effective SD from the
    rest-of-season baseline; only this week's is under decision. With
    ``s_candidate`` omitted it collapses to ``state.sigma``.
    """
    if s_candidate is None:
        return state.sigma
    if s_candidate <= 0.0:
        raise ValueError(f"candidate effective SD must be positive, got {s_candidate}")
    return math.sqrt(s_candidate**2 + state.s_weekly**2 * (state.tau - 1))


def theta(state: RankState, *, s_candidate: float | None = None, cap: float | None = None) -> float:
    """``theta(D, tau) = -z / (2 Sigma)`` -- the price of variance, in points.

    Derivation (§2). With Gaussian season totals,

    ``P(hit) = Phi(z)``, ``z = (D + m_1 + m_bar(tau-1)) / Sigma``, so

        ``dP/dm_1     = phi(z) / Sigma``
        ``dP/d(s_1^2) = -phi(z) z / (2 Sigma^2)``

    Dividing the second by the first gives the marginal rate of substitution
    between weekly mean and weekly variance, and every candidate can then be
    ranked by the certainty-equivalent weekly score ``m_1 + theta * s_1^2``.

    Sign and magnitude are the entire content:

    * ``z < 0`` (behind on expectation) => **theta > 0**: variance is a good, and
      the solver should pay mean for it.
    * ``z > 0`` (ahead) => **theta < 0**: variance is a cost.
    * ``|theta|`` grows as ``Sigma`` shrinks, so late-season-with-a-deficit is
      maximal variance appetite. A deficit that edge can close in March is
      closable only by luck in May.

    ``cap`` clips ``|theta|``. §2 names this failure mode explicitly: "a huge
    theta near the deadline turns the argmax into 'maximize variance', which
    must be trust-regioned". It defaults to ``None`` -- **off** -- because no
    study in this repo has calibrated a cap, and a fabricated one would be
    smuggled-in risk aversion of exactly the kind §7.1 objects to. Set it
    explicitly, with a reason, or leave it off.
    """
    sig = sigma_with_candidate(state, s_candidate)
    z = state.expected_final_margin / sig
    value = -z / (2.0 * sig)
    if cap is not None:
        if cap < 0.0:
            raise ValueError(f"theta cap must be non-negative, got {cap}")
        value = max(-cap, min(cap, value))
    return float(value)


def should_gamble(state: RankState) -> bool:
    """``dP/ds > 0 <=> D + m tau < 0`` (§3).

    Variance helps **iff you are behind on expectation**. The gambler's-ruin
    logic: from behind, low variance makes missing the target certain; the only
    route to the tail is volatility.
    """
    return state.behind


def certainty_equivalent(m_candidate: float, s_candidate: float, state: RankState,
                         *, cap: float | None = None) -> float:
    """``m_1 + theta * s_1^2`` -- F2's score for one candidate (§2).

    Both moments are **relative**: ``m_1`` is expected points minus the pace
    increment, ``s_1`` is the SD of that difference. Feeding own-score moments
    here is the mistake §1 exists to prevent.
    """
    return m_candidate + theta(state, s_candidate=s_candidate, cap=cap) * s_candidate**2


# ---------------------------------------------------------------------------
# Captaincy (§4)
# ---------------------------------------------------------------------------


def captaincy_score(mu: float, variance: float, share: float, theta_value: float) -> float:
    """``score_i = mu_i + theta (1 - 2 c_i) sigma_i^2`` (§4).

    Derivation: my relative gain from captaining ``i`` against a cohort with
    captaincy shares ``c`` is ``G_i = x_i - sum_j c_j x_j``, so under
    independence ``Var(G_i) = (1-c_i)^2 sigma_i^2 + sum_{j!=i} c_j^2 sigma_j^2``.
    Scoring with F2 and dropping the ``i``-independent terms leaves exactly the
    expression above.

    This *is* "EV x ownership x variance", with the ownership term pinned down:
    **the variance credit is scaled by ``(1 - 2 * share)``, so it flips sign at
    50% cohort share**. A captain owned by more than half the cohort is
    variance-*reducing* to pick, whatever his raw sigma -- captaining the
    field's captain is the safe move by construction, because you are then
    holding the bar rather than betting against it.

    Worked evidence (``rank_captaincy.csv``): Haaland at 48% share carries
    relative SD 3.46 against a 3%-owned punt's 7.10, despite raw sigmas of 5.8
    and 6.4. And ``theta`` carries the state, so the credit only pays when
    behind: the captaincy punt is a last-weeks-from-behind instrument, never a
    posture. Thirty weeks out, even 60 points behind, the max-EV captain is
    still correct.

    Note the structural finding this rule explains: the *mid* differential
    (22% share) is rank-optimal in no cell of the study's grid. It concedes EV
    without buying enough decorrelation. If you deviate, deviate properly.
    """
    if not 0.0 <= share <= 1.0:
        raise ValueError(f"captaincy share must be a probability, got {share}")
    if variance < 0.0:
        raise ValueError(f"variance must be non-negative, got {variance}")
    return mu + theta_value * (1.0 - 2.0 * share) * variance


def variance_credit_sign(share: float) -> int:
    """``+1`` below 50% cohort share, ``-1`` above, ``0`` exactly at it.

    The sign of ``(1 - 2c)``. Broken out because "where does the credit flip"
    is the question the rule is usually asked, and a caller should not have to
    re-derive it from the scoring expression.
    """
    if not 0.0 <= share <= 1.0:
        raise ValueError(f"captaincy share must be a probability, got {share}")
    x = 1.0 - 2.0 * share
    return 0 if x == 0.0 else (1 if x > 0.0 else -1)


# ---------------------------------------------------------------------------
# The hit (§5)
# ---------------------------------------------------------------------------


def _registry_hit_cost() -> float:
    """The hit cost, from the verified rule registry. Never hardcoded."""
    from fpl_edge.rules.loader import rules

    return abs(float(rules().get("transfers.hit_cost")))


def hit_threshold(
    *,
    expected_final_margin: float,
    s_weekly: float,
    s_weekly_after: float,
    tau: int,
    hold_weeks: int,
    hit_cost: float | None = None,
) -> float:
    """``g* = 4 + L (S' - S) / S`` -- the break-even total gain for a -4 (§5).

    A hit costs ``hit_cost`` points **with certainty**; it buys ``g`` expected
    points over a holding period ``h``, and it changes the effective weekly SD
    from ``s`` to ``s'`` over those weeks. With ``L = D + m tau`` the expected
    final margin without the hit, ``S = s sqrt(tau)`` and
    ``S'^2 = s^2 (tau - h) + s'^2 h``, equalising z-scores gives the expression
    above.

    Points logic says ``g* = 4`` always. The second term is the rank correction,
    and it is large. From ``rank_hit_threshold.csv`` (balanced, tau=12, h=8, a
    hit that raises weekly s from 6.0 to 8.0):

    ====  ======
    L     g*
    ====  ======
    -60   -9.9
    -30   -3.0
      0    4.0
    +30   11.0
    +60   17.9
    ====  ======

    Read it as: **behind, a hit that LOSES up to 10 expected points can still be
    correct**, because the differential it buys is the only thing that puts the
    target inside reach. Ahead, the same move must clear a much higher bar --
    while a variance-*shedding* hit gets cheap (g* falls to 0.1 at L=+60).
    Sign-checked by Monte Carlo in ``rank_hit_threshold_mc.csv``: at L=-30,
    s'=8, break-even is -2.97, and a hit gaining -1.97 (i.e. losing ~2 xP) still
    lifts P(hit) from .0744 to .0802, while one point below break-even lowers it.

    One caveat the scalar model omits, quoted from §5: taking a hit forfeits the
    banked-FT option, worth a state-dependent fraction of a free transfer. So
    ``g*`` is the threshold on gain **net of that option value** -- see
    :data:`fpl_edge.opt.config.FT_VALUE_LIST_SOTA`, which is how the solver
    prices the same option.
    """
    if tau < 1:
        raise ValueError(f"tau must be at least 1, got {tau}")
    if hold_weeks < 1:
        raise ValueError(f"hold_weeks must be at least 1, got {hold_weeks}")
    if s_weekly <= 0.0 or s_weekly_after <= 0.0:
        raise ValueError("effective SDs must be positive")
    h = min(int(hold_weeks), int(tau))
    cost = _registry_hit_cost() if hit_cost is None else float(hit_cost)
    s_total = s_weekly * math.sqrt(tau)
    s_total_after = math.sqrt(s_weekly**2 * (tau - h) + s_weekly_after**2 * h)
    return cost + expected_final_margin * (s_total_after - s_total) / s_total


def hit_is_justified(
    gain: float,
    state: RankState,
    *,
    s_weekly_after: float,
    hold_weeks: int,
    ft_option_value: float = 0.0,
    hit_cost: float | None = None,
) -> tuple[bool, float]:
    """``(verdict, g*)`` for a hit expected to gain ``gain`` points in total.

    ``ft_option_value`` is subtracted from the gain, not added to ``g*``, which
    is §5's own framing: the threshold applies to gain *net* of the forfeited
    banked-transfer option.
    """
    g_star = hit_threshold(
        expected_final_margin=state.expected_final_margin,
        s_weekly=state.s_weekly,
        s_weekly_after=s_weekly_after,
        tau=state.tau,
        hold_weeks=hold_weeks,
        hit_cost=hit_cost,
    )
    return (gain - ft_option_value) >= g_star, g_star


# ---------------------------------------------------------------------------
# The lambda gate (§7.1)
# ---------------------------------------------------------------------------


def lambda_effective(lambda_base: float, state: RankState) -> float:
    """State-gate the legacy CVaR penalty: ``lambda * 1[D + m tau >= 0]`` (§7.1).

    ``fpl_edge/sim/utility.py`` maximises
    ``P(R<=T) + 0.25 P(R<=S) - 0.35 CVaR_0.1[log-rank loss]`` with a **constant**
    lambda. §3 shows the objective's own risk appetite flips sign with the
    state, so a fixed penalty on the left tail is a template thumb on the scale
    *precisely in the deficit states where variance is the only route to the
    target*. The 9.0-16.4pp static-versus-adaptive gaps in ``rank_policy_mc.csv``
    are what a fixed posture costs.

    So: keep the catastrophe aversion while ahead, where it is cheap and where
    the objective agrees with it, and switch it off once the expected final
    margin turns negative. §7.1 offers the softer form "lambda ~= 0 once
    ``D + m tau < 0`` by more than one season-SD"; the hard indicator
    implemented here is the conservative reading of that -- it stops fighting
    the objective the moment the objective changes sign, rather than after a
    season-SD of further slippage. :func:`lambda_effective_soft` is the graded
    version for callers who want the ramp.

    If catastrophe aversion is not a genuine preference, §7.1's other branch
    applies and ``lambda_base`` should be zero, not gated.
    """
    if lambda_base < 0.0:
        raise ValueError(f"lambda must be non-negative, got {lambda_base}")
    return 0.0 if state.behind else float(lambda_base)


def lambda_effective_soft(lambda_base: float, state: RankState) -> float:
    """The graded gate: ramp lambda to zero over one season-SD of deficit (§7.1).

    ``lambda * clip(1 + L / Sigma, 0, 1)``. At ``L = 0`` this still carries the
    full penalty and reaches zero at ``L = -Sigma``, which is §7.1's literal
    phrasing ("lambda ~= 0 once ``D + m tau < 0`` by more than one season-SD").
    """
    if lambda_base < 0.0:
        raise ValueError(f"lambda must be non-negative, got {lambda_base}")
    ramp = 1.0 + state.expected_final_margin / state.sigma
    return float(lambda_base * min(1.0, max(0.0, ramp)))
