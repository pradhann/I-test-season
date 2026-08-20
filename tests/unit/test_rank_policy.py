"""The closed forms, checked against the study CSVs that produced them.

EVIDENCE RULE: no constant reaches the objective without a citation. These tests
are how the citation is enforced -- the boundary, the hit threshold and the
policy's own P(hit) are recomputed here and compared to the committed outputs of
``scripts/rank_objective_study.py``. If someone edits a constant in
:mod:`fpl_edge.rank.policy`, it stops matching the study and these fail.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from fpl_edge.rank import (
    BALANCED,
    BASELINE_MENU,
    DIFFERENTIAL,
    PUNT,
    TEMPLATE,
    Archetype,
    RankState,
    boundary_slope,
    captaincy_score,
    certainty_equivalent,
    crossing_deficit,
    hit_is_justified,
    hit_threshold,
    lambda_effective,
    lambda_effective_soft,
    myopic_best,
    p_hit_holding,
    should_gamble,
    theta,
    variance_credit_sign,
)

STUDY = Path(__file__).resolve().parents[2] / "docs" / "platform"


def read_study(name: str) -> list[dict[str, str]]:
    with open(STUDY / name, newline="") as f:
        return list(csv.DictReader(f))


def stylised(deficit: float, tau: int, archetype: Archetype = BALANCED) -> RankState:
    return RankState.stylised(
        deficit=deficit, tau=tau, m_weekly=archetype.m, s_weekly=archetype.s
    )


# ---------------------------------------------------------------------------
# §3: the switch boundary is a straight line through the origin
# ---------------------------------------------------------------------------


def test_boundary_matches_the_committed_closed_form_at_every_tau():
    """Our crossing_deficit must reproduce rank_switchpoint.csv column for column."""
    rows = read_study("rank_switchpoint.csv")
    assert len(rows) == 38
    for row in rows:
        tau = int(row["tau"])
        got = crossing_deficit(tau, BALANCED, DIFFERENTIAL)
        assert got == pytest.approx(float(row["closed_form_diff_over_balanced"]), rel=1e-5)


def test_the_boundary_is_linear_in_tau():
    """D* = -1.064 * tau exactly -- a straight line through the origin (§3).

    Linearity is the structural claim: drift scales with tau, dispersion with
    sqrt(tau), so the comparison that decides the posture is scale-free. It is
    also why a weekly re-solved closed form loses almost nothing to a DP.
    """
    slope = boundary_slope(BALANCED, DIFFERENTIAL)
    assert slope == pytest.approx(-1.0642857, rel=1e-6)
    for tau in range(1, 39):
        assert crossing_deficit(tau, BALANCED, DIFFERENTIAL) == pytest.approx(slope * tau)
    # The quoted anchors in §3's prose.
    assert crossing_deficit(19, BALANCED, DIFFERENTIAL) == pytest.approx(-20.22, abs=0.01)
    assert crossing_deficit(4, BALANCED, DIFFERENTIAL) == pytest.approx(-4.26, abs=0.01)
    assert crossing_deficit(1, BALANCED, DIFFERENTIAL) == pytest.approx(-1.06, abs=0.01)


def test_the_other_two_boundaries_match_their_quoted_slopes():
    """§3: punt overtakes diff at ~-2.27 tau; template overtakes balanced at ~+1.15 tau."""
    assert boundary_slope(DIFFERENTIAL, PUNT) == pytest.approx(-2.26875, rel=1e-6)
    assert boundary_slope(TEMPLATE, BALANCED) == pytest.approx(1.15, rel=1e-6)


def test_the_crossing_is_symmetric_in_its_arguments():
    assert crossing_deficit(19, BALANCED, DIFFERENTIAL) == pytest.approx(
        crossing_deficit(19, DIFFERENTIAL, BALANCED)
    )


def test_two_archetypes_with_equal_volatility_never_cross():
    twin = Archetype("twin", 0.10, BALANCED.s)
    with pytest.raises(ValueError, match="never cross"):
        crossing_deficit(10, BALANCED, twin)


def test_myopic_best_switches_across_the_boundary():
    """Just above the line, balanced; just below it, the differential."""
    tau = 19
    d_star = crossing_deficit(tau, BALANCED, DIFFERENTIAL)
    assert myopic_best(d_star + 1.0, tau).name == "balanced"
    assert myopic_best(d_star - 1.0, tau).name == "diff"


def test_the_studys_static_posture_p_hits_are_reproduced():
    """p_hit_holding must match rank_policy_mc.csv's static-policy cells.

    MC SE <= 0.0011 per cell, so a 0.005 tolerance is ~4 SE: tight enough to
    catch a wrong formula, loose enough to survive Monte Carlo noise.
    """
    by_name = {a.name: a for a in BASELINE_MENU}
    checked = 0
    for row in read_study("rank_policy_mc.csv"):
        if row["policy"] not in by_name:
            continue  # 'myopic' and 'dp' are policies, not static postures
        got = p_hit_holding(by_name[row["policy"]], float(row["d0"]), int(row["tau0"]))
        assert got == pytest.approx(float(row["p_hit"]), abs=0.005), row
        checked += 1
    assert checked == 28  # 7 states x 4 static archetypes


def test_adaptivity_beats_every_static_posture_at_the_studys_cells():
    """§3's headline: the best STATIC posture loses 9-16pp to the adaptive rule.

    We do not re-run the DP here; we check that the myopic rule the policy layer
    implements picks a different archetype from the single best static one in the
    deficit cells, which is the mechanism behind the gap.
    """
    rows = {(int(r["tau0"]), float(r["d0"])): r for r in read_study("rank_policy_mc.csv")
            if r["policy"] == "myopic"}
    for (tau, d), row in rows.items():
        best_static = max(
            (p_hit_holding(a, d, tau), a.name) for a in BASELINE_MENU
        )
        assert float(row["p_hit"]) > best_static[0], (tau, d)
    # And the reported margins are the 9-16pp the study quotes.
    at_start = rows[(38, 0.0)]
    best_static_start = max(p_hit_holding(a, 0.0, 38) for a in BASELINE_MENU)
    assert float(at_start["p_hit"]) - best_static_start == pytest.approx(0.089, abs=0.005)


# ---------------------------------------------------------------------------
# §2: theta, the state-dependent price of variance
# ---------------------------------------------------------------------------


def test_theta_flips_sign_exactly_where_the_expected_margin_does():
    """theta > 0 iff behind on expectation -- §3's dP/ds > 0 <=> D + m tau < 0."""
    tau = 19
    # L = D + m tau = 0 at D = -m tau.
    d_zero = -BALANCED.m * tau
    assert theta(stylised(d_zero, tau)) == pytest.approx(0.0, abs=1e-12)
    assert theta(stylised(d_zero - 1.0, tau)) > 0.0
    assert theta(stylised(d_zero + 1.0, tau)) < 0.0
    assert should_gamble(stylised(d_zero - 1.0, tau))
    assert not should_gamble(stylised(d_zero + 1.0, tau))


def test_theta_magnitude_grows_as_the_season_runs_out():
    """§2: |theta| grows as Sigma shrinks. Late-with-a-deficit is peak appetite.

    A deficit that edge can close in March is closable only by luck in May.

    The deficit is held FIXED while tau falls, which is the real situation. (A
    deficit that scales with tau leaves theta invariant -- theta = -L/(2 s^2 tau)
    and L is then proportional to tau -- which is itself the reason the switch
    boundary is a straight line.)
    """
    thetas = [theta(stylised(-40.0, tau)) for tau in (30, 19, 10, 5, 2)]
    assert all(t > 0.0 for t in thetas)
    assert thetas == sorted(thetas), "theta must increase as tau falls"
    assert thetas[-1] / thetas[0] > 20.0, "and it must grow by more than a little"


def test_theta_equals_the_derivative_ratio_it_claims_to_be():
    """theta = (dP/d s_1^2) / (dP/d m_1), by finite differences on Phi(z).

    This is the derivation in §2, checked numerically rather than asserted.
    """
    state = stylised(-25.0, 12)
    h = 1e-6

    def p_hit(extra_mean: float, extra_var: float) -> float:
        from scipy.stats import norm

        sigma_sq = state.s_weekly**2 + extra_var + state.s_weekly**2 * (state.tau - 1)
        sigma = math.sqrt(sigma_sq)
        return float(norm.cdf((state.expected_final_margin + extra_mean) / sigma))

    d_mean = (p_hit(h, 0.0) - p_hit(-h, 0.0)) / (2 * h)
    d_var = (p_hit(0.0, h) - p_hit(0.0, -h)) / (2 * h)
    assert d_var / d_mean == pytest.approx(theta(state), rel=1e-4)


def test_theta_cap_is_off_by_default_and_clips_symmetrically_when_set():
    """The §2 trust region. Off unless the caller sets it -- no invented constant."""
    late = stylised(-60.0, 1)
    raw = theta(late)
    assert raw > 0.5, "a one-week 60-point deficit should produce a huge theta"
    assert theta(late, cap=0.01) == pytest.approx(0.01)
    ahead = stylised(+60.0, 1)
    assert theta(ahead, cap=0.01) == pytest.approx(-0.01)


def test_certainty_equivalent_prefers_variance_only_when_behind():
    """The F2 argmax must reverse between the two branches of §3's law."""
    behind = stylised(-40.0, 19)
    ahead = stylised(+40.0, 19)
    # A differential candidate: less mean, much more effective volatility.
    safe = (0.55, 6.0)
    risky = (0.25, 9.5)
    assert certainty_equivalent(*risky, behind) > certainty_equivalent(*safe, behind)
    assert certainty_equivalent(*risky, ahead) < certainty_equivalent(*safe, ahead)


# ---------------------------------------------------------------------------
# §4: captaincy, and the sign flip at 50% cohort share
# ---------------------------------------------------------------------------


def test_the_variance_credit_flips_sign_exactly_at_fifty_percent_share():
    """(1 - 2c) is zero at c = 0.5 and nowhere else. §4's structural claim."""
    assert variance_credit_sign(0.5) == 0
    assert variance_credit_sign(0.5 - 1e-12) == 1
    assert variance_credit_sign(0.5 + 1e-12) == -1

    th = theta(stylised(-40.0, 5))
    assert th > 0.0
    mu, var = 8.0, 30.0
    at_half = captaincy_score(mu, var, 0.5, th)
    assert at_half == pytest.approx(mu), "at 50% share the credit vanishes entirely"
    assert captaincy_score(mu, var, 0.49, th) > at_half
    assert captaincy_score(mu, var, 0.51, th) < at_half


def test_a_majority_owned_captain_is_variance_reducing_whatever_his_sigma():
    """§4: 'a captain owned by more than half the cohort is variance-reducing'."""
    th = theta(stylised(-40.0, 5))
    high_share_high_sigma = captaincy_score(8.0, 100.0, 0.80, th)
    high_share_low_sigma = captaincy_score(8.0, 1.0, 0.80, th)
    assert high_share_high_sigma < high_share_low_sigma < 8.0


def test_the_captaincy_punt_is_a_last_weeks_from_behind_instrument():
    """§4's grid: 30 weeks out at -60, max-EV still wins; at tau=1, the punt wins.

    Menu from rank_captaincy.csv: Haaland mu 8.6 sigma 5.8 share 48%,
    punt mu 6.8 sigma 6.4 share 3%.
    """
    haaland = (8.6, 5.8**2, 0.48)
    punt = (6.8, 6.4**2, 0.03)

    far_out = theta(stylised(-60.0, 30))
    assert captaincy_score(*haaland, far_out) > captaincy_score(*punt, far_out)

    deadline = theta(stylised(-15.0, 1))
    assert captaincy_score(*punt, deadline) > captaincy_score(*haaland, deadline)


def test_the_mid_differential_is_dominated():
    """§4: Palmer (22% share) is rank-optimal in NO cell of the grid.

    He concedes EV to Haaland without buying enough decorrelation. If the rule
    ever makes him the argmax, the rule has drifted from the study.
    """
    haaland = (8.6, 5.8**2, 0.48)
    palmer = (7.4, 5.2**2, 0.22)
    punt = (6.8, 6.4**2, 0.03)
    for tau in (1, 2, 4, 8, 12, 19, 30, 38):
        for deficit_per_week in (-4.0, -2.0, -1.0, 0.0, 1.0):
            th = theta(stylised(deficit_per_week * tau, tau))
            scores = {
                "haaland": captaincy_score(*haaland, th),
                "palmer": captaincy_score(*palmer, th),
                "punt": captaincy_score(*punt, th),
            }
            assert max(scores, key=scores.get) != "palmer", (tau, deficit_per_week)


def test_a_share_outside_zero_one_is_refused():
    with pytest.raises(ValueError, match="probability"):
        captaincy_score(8.0, 30.0, 1.4, 0.01)


# ---------------------------------------------------------------------------
# §5: the hit threshold
# ---------------------------------------------------------------------------


def test_hit_threshold_matches_the_committed_study_table():
    """g* must reproduce rank_hit_threshold.csv exactly -- it is a closed form."""
    checked = 0
    for row in read_study("rank_hit_threshold.csv"):
        got = hit_threshold(
            expected_final_margin=float(row["expected_final_margin"]),
            s_weekly=6.0,  # the study's 'balanced' baseline
            s_weekly_after=float(row["s_weekly_new"]),
            tau=int(row["tau"]),
            hold_weeks=int(row["hold_weeks"]),
        )
        assert got == pytest.approx(float(row["breakeven_total_gain"]), rel=1e-5), row
        checked += 1
    assert checked > 100


def test_hit_threshold_is_four_points_when_the_state_is_neutral():
    """L = 0 kills the correction term: points logic and rank logic agree."""
    got = hit_threshold(
        expected_final_margin=0.0, s_weekly=6.0, s_weekly_after=8.0,
        tau=12, hold_weeks=8,
    )
    assert got == pytest.approx(4.0)


def test_hit_threshold_is_monotone_in_the_expected_final_margin():
    """Variance-BUYING hits get cheaper the further behind you are, and dearer ahead.

    Variance-SHEDDING hits do the opposite. Both directions are §5's table.
    """
    def g_star(margin: float, s_after: float) -> float:
        return hit_threshold(
            expected_final_margin=margin, s_weekly=6.0, s_weekly_after=s_after,
            tau=12, hold_weeks=8,
        )

    margins = [-60.0, -30.0, 0.0, 30.0, 60.0]
    buying = [g_star(m, 8.0) for m in margins]
    shedding = [g_star(m, 5.4) for m in margins]
    assert buying == sorted(buying), "buying variance: g* rises with the margin"
    assert shedding == sorted(shedding, reverse=True), "shedding variance: g* falls"

    # The exact cells §5 quotes.
    assert buying[0] == pytest.approx(-9.9, abs=0.05)
    assert buying[1] == pytest.approx(-3.0, abs=0.05)
    assert buying[3] == pytest.approx(11.0, abs=0.05)
    assert shedding[4] == pytest.approx(0.1, abs=0.05)


def test_behind_a_hit_that_loses_expected_points_can_still_be_right():
    """§5's headline, with the MC sign check from rank_hit_threshold_mc.csv.

    At L=-30, s'=8, break-even is -2.97: a hit gaining -1.97 (losing ~2 xP)
    raised P(hit) from .0744 to .0802 in 400k paired sims.
    """
    row = next(
        r for r in read_study("rank_hit_threshold_mc.csv")
        if float(r["expected_final_margin"]) == -30.0
        and float(r["s_weekly_new"]) == 8.0
        and float(r["total_gain"]) > float(r["breakeven_total_gain"])
    )
    # Reconstruct the state the study used: L = D + m tau, balanced menu.
    tau = int(row["tau"])
    state = RankState.stylised(
        deficit=-30.0 - BALANCED.m * tau, tau=tau,
        m_weekly=BALANCED.m, s_weekly=BALANCED.s,
    )
    assert state.expected_final_margin == pytest.approx(-30.0)

    ok, g_star = hit_is_justified(
        float(row["total_gain"]), state, s_weekly_after=8.0, hold_weeks=int(row["hold_weeks"])
    )
    assert g_star == pytest.approx(float(row["breakeven_total_gain"]), rel=1e-5)
    assert g_star < 0.0, "the break-even gain is NEGATIVE this far behind"
    assert ok
    # And the MC agrees the hit raised P(hit).
    assert float(row["p_hit_with_hit"]) > float(row["p_hit_no_hit"])

    # One point below break-even it must be refused.
    refused, _ = hit_is_justified(
        g_star - 1.0, state, s_weekly_after=8.0, hold_weeks=int(row["hold_weeks"])
    )
    assert not refused


def test_the_forfeited_free_transfer_option_is_netted_off_the_gain():
    """§5's caveat: g* applies to gain NET of the banked-FT option value."""
    state = stylised(0.0, 12)
    gain = 5.0
    assert hit_is_justified(gain, state, s_weekly_after=6.0, hold_weeks=8)[0]
    assert not hit_is_justified(
        gain, state, s_weekly_after=6.0, hold_weeks=8, ft_option_value=2.0
    )[0]


def test_the_hit_cost_comes_from_the_rule_registry():
    """Nothing here hardcodes 4. The registry is the only source of rule values."""
    from fpl_edge.rules.loader import rules

    assert abs(float(rules().get("transfers.hit_cost"))) == pytest.approx(4.0)
    neutral = hit_threshold(
        expected_final_margin=0.0, s_weekly=6.0, s_weekly_after=6.0,
        tau=12, hold_weeks=4,
    )
    assert neutral == pytest.approx(abs(float(rules().get("transfers.hit_cost"))))


# ---------------------------------------------------------------------------
# §7.1: the lambda gate
# ---------------------------------------------------------------------------


def test_lambda_is_gated_off_in_deficit_states_and_on_when_ahead():
    """§7.1: a fixed lambda=0.35 fights the objective precisely when behind."""
    tau = 19
    d_zero = -BALANCED.m * tau  # L = 0
    ahead = stylised(d_zero + 5.0, tau)
    behind = stylised(d_zero - 5.0, tau)

    assert lambda_effective(0.35, ahead) == pytest.approx(0.35)
    assert lambda_effective(0.35, behind) == 0.0
    # Exactly at L = 0 the objective has not yet flipped, so the penalty stands.
    assert lambda_effective(0.35, stylised(d_zero, tau)) == pytest.approx(0.35)


def test_the_soft_gate_ramps_over_one_season_sd():
    """§7.1's literal phrasing: lambda ~= 0 once L < 0 by more than one season-SD."""
    tau = 19
    d_zero = -BALANCED.m * tau
    at_zero = stylised(d_zero, tau)
    sigma = at_zero.sigma

    assert lambda_effective_soft(0.35, at_zero) == pytest.approx(0.35)
    half = stylised(d_zero - 0.5 * sigma, tau)
    assert lambda_effective_soft(0.35, half) == pytest.approx(0.175)
    full = stylised(d_zero - sigma, tau)
    assert lambda_effective_soft(0.35, full) == pytest.approx(0.0, abs=1e-12)
    beyond = stylised(d_zero - 2.0 * sigma, tau)
    assert lambda_effective_soft(0.35, beyond) == 0.0


def test_a_negative_lambda_is_refused():
    with pytest.raises(ValueError, match="non-negative"):
        lambda_effective(-0.1, stylised(0.0, 10))
