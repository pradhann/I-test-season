"""Rank-objective study: when does rank-seeking deviate from points-seeking?

Companion to ``docs/platform/rank_objectives.md``. Every number quoted there is
produced here. Run:

    uv run python scripts/rank_objective_study.py

Writes CSVs into ``docs/platform/``. All randomness is seeded; reruns are
bit-identical.

The model of the season
-----------------------
The full simulator (``fpl_edge/sim``) draws player points and samples rival
squads. This study deliberately works one level up, on the *sufficient
statistic* for the rank objective: the deficit process

    D_t = (my cumulative points) - (cumulative top-10k pace)

where "pace" is the running score of the 10,000th-ranked manager. The
manager's weekly decision is summarised by two numbers:

    m = E[my weekly score - weekly pace increment]          (edge vs the bar)
    s = SD[my weekly score - weekly pace increment]         (effective volatility)

The crucial point, and the reason a template squad is not "low variance" in
any useful sense: s is the volatility of the DIFFERENCE. A full template moves
in lockstep with the threshold, so its s is small even though its own-score SD
is large (~15 pts/wk). A differential squad decorrelates from the bar, so its
s is large. m is edge over the 10,000th manager, not over the mean manager,
so even a good model's xP-optimal squad has modest positive m.

Calibration is anchored to the live-simulator run of 2026-08-19 recorded in
``docs/models/simulator.md`` section 9: xPts-optimal squad season mean 2,217
(sd 94) against a top-10k bar of ~2,196, i.e. m ~= +0.55/wk for the optimal
squad, own-score weekly sd 94/sqrt(38) ~= 15.2. Effective s values are chosen
so that season-total deficit SDs (s*sqrt(38)) span the plausible range between
"near-perfect coupling with the bar" and "heavily decorrelated":
template s=3 -> season deficit sd 18, punt s=13.5 -> season deficit sd 83.
These are stylised; the OUTPUT of the study is the shape and location of
switching boundaries, which the doc discusses with sensitivity in mind.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import norm

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "platform"
WEEKS = 38
SEED = 20260819

# ---------------------------------------------------------------------------
# Strategy archetypes: (m, s) per week, relative to the top-10k pace.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Strategy:
    name: str
    m: float  # weekly mean edge vs the top-10k pace
    s: float  # weekly sd of (my score - pace increment)


STRATS: list[Strategy] = [
    # Pure template: hugs the bar. Slightly NEGATIVE edge vs the 10,000th
    # manager (the bar is set by managers who out-pick the template), tiny
    # effective volatility because it co-moves with the bar.
    Strategy("template", -0.30, 3.0),
    # xP-optimal with our model's measured edge (simulator.md: +21 pts over
    # the bar across 38 GWs => +0.55/wk), moderate decorrelation.
    Strategy("balanced", 0.55, 6.0),
    # Differential tilt: give back ~0.3/wk of edge to buy decorrelation.
    Strategy("diff", 0.25, 9.5),
    # Heavy punting: negative edge, maximum volatility.
    Strategy("punt", -0.60, 13.5),
]
STRAT_BY_NAME = {st.name: st for st in STRATS}


# ---------------------------------------------------------------------------
# Study A: state dependence. Myopic (commit-for-the-rest) switch points,
# dynamic-programming policy on the deficit ladder, and MC validation.
# ---------------------------------------------------------------------------


def myopic_p_hit(d: np.ndarray, tau: int, st: Strategy) -> np.ndarray:
    """P(final deficit >= 0) if strategy ``st`` is held for all tau weeks."""
    return norm.cdf((d + st.m * tau) / (st.s * math.sqrt(tau)))


def myopic_best(d: np.ndarray, tau: int) -> np.ndarray:
    """Index into STRATS of the best commit-for-the-rest strategy."""
    p = np.stack([myopic_p_hit(d, tau, st) for st in STRATS])
    return p.argmax(axis=0)


def solve_dp(grid: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Backward induction on the deficit ladder.

    Returns (V, policy): V[tau, i] = max P(hit) with tau weeks left from
    deficit grid[i]; policy[tau, i] = argmax strategy index. tau=0 row is the
    terminal indicator. The tau=1 step is computed exactly (the integrand is a
    step function, on which quadrature is poor); tau>=2 uses 41-node
    Gauss-Hermite quadrature, where the value function is a smooth mixture of
    normal cdfs.
    """
    nodes, wts = np.polynomial.hermite_e.hermegauss(41)
    wts = wts / wts.sum()  # weights for standard normal expectation

    v = np.zeros((WEEKS + 1, grid.size))
    pol = np.zeros((WEEKS + 1, grid.size), dtype=int)
    v[0] = (grid >= 0.0).astype(float)
    for tau in range(1, WEEKS + 1):
        best_v = np.full(grid.size, -1.0)
        best_a = np.zeros(grid.size, dtype=int)
        for a, st in enumerate(STRATS):
            if tau == 1:
                ev = norm.cdf((grid + st.m) / st.s)
            else:
                # E[ V(tau-1, d + m + s*Z) ]
                ev = np.zeros(grid.size)
                for z, w in zip(nodes, wts):
                    nxt = grid + st.m + st.s * z
                    ev += w * np.interp(nxt, grid, v[tau - 1], left=0.0, right=1.0)
            upd = ev > best_v + 1e-12
            best_v[upd] = ev[upd]
            best_a[upd] = a
        v[tau] = best_v
        pol[tau] = best_a
    return v, pol


def boundaries(names: list[str], grid: np.ndarray, choice: np.ndarray) -> dict[str, float]:
    """First grid deficit (scanning upward) at which the policy switches.

    Returns, per adjacent pair encountered, the deficit of the switch.
    """
    out: dict[str, float] = {}
    for i in range(1, grid.size):
        a, b = choice[i - 1], choice[i]
        if a != b:
            key = f"{names[a]}->{names[b]}"
            if key not in out:
                out[key] = float(0.5 * (grid[i - 1] + grid[i]))
    return out


def study_state_dependence() -> None:
    grid = np.linspace(-300.0, 300.0, 2401)  # 0.25-pt steps
    v, pol = solve_dp(grid)
    names = [st.name for st in STRATS]

    rows = []
    for tau in range(1, WEEKS + 1):
        my_choice = myopic_best(grid, tau)
        my_b = boundaries(names, grid, my_choice)
        dp_b = boundaries(names, grid, pol[tau])
        # Closed-form myopic diff/balanced crossing for the doc's derivation:
        # (D + m_d*tau)/s_d = (D + m_b*tau)/s_b  =>  D* = tau*(m_b s_d - m_d s_b)/(s_b - s_d)
        sb, sd_ = STRAT_BY_NAME["balanced"], STRAT_BY_NAME["diff"]
        d_star_closed = tau * (sb.m * sd_.s - sd_.m * sb.s) / (sb.s - sd_.s)
        rows.append(
            {
                "tau": tau,
                "myopic_diff_over_balanced": my_b.get("diff->balanced", math.nan),
                "myopic_punt_over_diff": my_b.get("punt->diff", math.nan),
                "myopic_balanced_over_template": my_b.get("balanced->template", math.nan),
                "dp_diff_over_balanced": dp_b.get("diff->balanced", math.nan),
                "dp_punt_over_diff": dp_b.get("punt->diff", math.nan),
                "dp_balanced_over_template": dp_b.get("balanced->template", math.nan),
                "closed_form_diff_over_balanced": d_star_closed,
                "p_hit_dp_at_boundary": float(
                    np.interp(my_b.get("diff->balanced", 0.0), grid, v[tau])
                ),
            }
        )
    _write_csv("rank_switchpoint.csv", rows)

    # Value of the DP policy vs static and myopic policies, by Monte Carlo.
    rng = np.random.default_rng(SEED)
    n = 200_000
    starts = [(38, 0.0), (19, -40.0), (19, -20.0), (19, 0.0), (19, 20.0), (10, -30.0), (5, -15.0)]
    mc_rows = []
    for tau0, d0 in starts:
        z = rng.standard_normal((n, tau0))  # shared shocks across policies (paired)
        for policy in ["template", "balanced", "diff", "punt", "myopic", "dp"]:
            d = np.full(n, d0)
            for k in range(tau0):
                tau = tau0 - k
                if policy in STRAT_BY_NAME:
                    st_idx = np.full(n, names.index(policy))
                elif policy == "myopic":
                    st_idx = myopic_best(d, tau)
                else:  # dp
                    gi = np.clip(
                        np.searchsorted(grid, d), 0, grid.size - 1
                    )
                    st_idx = pol[tau][gi]
                m = np.array([st.m for st in STRATS])[st_idx]
                s = np.array([st.s for st in STRATS])[st_idx]
                d = d + m + s * z[:, k]
            p = float((d >= 0.0).mean())
            mc_rows.append(
                {
                    "tau0": tau0,
                    "d0": d0,
                    "policy": policy,
                    "p_hit": p,
                    "se": math.sqrt(p * (1 - p) / n),
                }
            )
    _write_csv("rank_policy_mc.csv", mc_rows)


# ---------------------------------------------------------------------------
# Study B: rank-optimal captaincy under a simple field model.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Captain:
    name: str
    mu: float  # expected captain increment (the doubled slice), pts
    sigma: float  # sd of that increment
    share: float  # fraction of the near-threshold cohort captaining him


CAPTAINS = [
    Captain("haaland", 8.6, 5.8, 0.48),
    Captain("palmer", 7.4, 5.2, 0.22),
    Captain("punt", 6.8, 6.4, 0.03),
    Captain("field_other", 6.9, 5.5, 0.27),  # residual field mix, not choosable
]
CHOOSABLE = [c for c in CAPTAINS if c.name != "field_other"]

# Same-slate correlation between the two premiums (they both benefit when the
# big teams' fixtures are soft); the punt is assumed uncorrelated.
CAP_CORR = np.eye(len(CAPTAINS))
CAP_CORR[0, 1] = CAP_CORR[1, 0] = 0.10


def captain_gain_moments(idx: int) -> tuple[float, float]:
    """Mean and variance of G_i = x_i - sum_j share_j x_j (vs the field mix)."""
    mu = np.array([c.mu for c in CAPTAINS])
    sig = np.array([c.sigma for c in CAPTAINS])
    shr = np.array([c.share for c in CAPTAINS])
    cov = CAP_CORR * np.outer(sig, sig)
    w = -shr.copy()
    w[idx] += 1.0
    return float(w @ mu), float(w @ cov @ w)


def study_captaincy() -> None:
    balanced = STRAT_BY_NAME["balanced"]
    rows = []
    rng = np.random.default_rng(SEED + 1)
    n_mc = 400_000
    mu = np.array([c.mu for c in CAPTAINS])
    sig = np.array([c.sigma for c in CAPTAINS])
    shr = np.array([c.share for c in CAPTAINS])
    cov = CAP_CORR * np.outer(sig, sig)
    chol = np.linalg.cholesky(cov)

    for tau in [1, 4, 12, 30]:
        # One captaincy decision now, balanced strategy for the remaining
        # tau-1 weeks; the current week's non-captain squad noise is folded
        # into the balanced (m, s) baseline.
        for d0 in [-60, -40, -25, -15, -8, -4, 0, 8, 25]:
            # analytic
            for i, c in enumerate(CHOOSABLE):
                g_mu, g_var = captain_gain_moments(CAPTAINS.index(c))
                z = (d0 + balanced.m * tau + g_mu) / math.sqrt(
                    balanced.s**2 * tau + g_var
                )
                rows.append(
                    {
                        "tau": tau,
                        "d0": d0,
                        "captain": c.name,
                        "gain_mean": g_mu,
                        "gain_sd": math.sqrt(g_var),
                        "p_hit": float(norm.cdf(z)),
                        "method": "analytic",
                        "se": 0.0,
                    }
                )
            # MC check on a subset (paired shocks across captains)
            if tau in (4, 30) and d0 in (-40, -15, 0):
                x = mu[None, :] + rng.standard_normal((n_mc, len(CAPTAINS))) @ chol.T
                rest = balanced.m * tau + balanced.s * math.sqrt(tau) * rng.standard_normal(n_mc)
                pace_gain = x @ shr
                for i, c in enumerate(CHOOSABLE):
                    final = d0 + rest + x[:, CAPTAINS.index(c)] - pace_gain
                    p = float((final >= 0).mean())
                    rows.append(
                        {
                            "tau": tau,
                            "d0": d0,
                            "captain": c.name,
                            "gain_mean": float((x[:, CAPTAINS.index(c)] - pace_gain).mean()),
                            "gain_sd": float((x[:, CAPTAINS.index(c)] - pace_gain).std()),
                            "p_hit": p,
                            "method": "mc",
                            "se": math.sqrt(p * (1 - p) / n_mc),
                        }
                    )
    _write_csv("rank_captaincy.csv", rows)


# ---------------------------------------------------------------------------
# Study C: the hit (-4) threshold in rank terms.
# ---------------------------------------------------------------------------


def study_hit_threshold() -> None:
    """Break-even gain for a -4 as a function of state.

    A hit costs 4 points with certainty and buys (a) delta extra expected
    points per week for h weeks and (b) a change in weekly effective sd from
    s to s_new over those h weeks. With L = D + m*tau the expected final
    margin without the hit, S = s*sqrt(tau) the no-hit season deficit sd and
    S' the with-hit sd, equalising z-scores gives the break-even total gain

        g* = 4 + L * (S' - S) / S.

    In points logic g* = 4 always. In rank logic the second term is the
    state adjustment: negative when behind and buying variance, positive when
    ahead and buying variance.
    """
    balanced = STRAT_BY_NAME["balanced"]
    rows = []
    rng = np.random.default_rng(SEED + 2)
    n_mc = 400_000
    for tau in [6, 12, 26]:
        for h in [4, 8]:
            h_eff = min(h, tau)
            for s_new in [5.4, 6.0, 6.8, 8.0]:
                s_sq = balanced.s**2 * (tau - h_eff) + s_new**2 * h_eff
                s_tot_new = math.sqrt(s_sq)
                s_tot = balanced.s * math.sqrt(tau)
                for lead in [-60, -30, -12, 0, 12, 30, 60]:
                    ell = lead  # already the expected final margin
                    g_star = 4.0 + ell * (s_tot_new - s_tot) / s_tot
                    rows.append(
                        {
                            "expected_final_margin": ell,
                            "tau": tau,
                            "hold_weeks": h_eff,
                            "s_weekly_new": s_new,
                            "sd_total_no_hit": s_tot,
                            "sd_total_hit": s_tot_new,
                            "breakeven_total_gain": g_star,
                            "breakeven_pts_per_week": g_star / h_eff,
                            "method": "analytic",
                        }
                    )
    _write_csv("rank_hit_threshold.csv", rows)

    # MC verification of the sign of the rule at four cells: gain 1 point
    # above / below the analytic break-even must raise / lower P(hit).
    check_rows = []
    for lead, tau, h, s_new in [(-30, 12, 8, 8.0), (30, 12, 8, 8.0), (-30, 12, 8, 5.4), (0, 6, 4, 6.8)]:
        balanced_s = STRAT_BY_NAME["balanced"].s
        m_wk = STRAT_BY_NAME["balanced"].m
        d0 = lead - m_wk * tau
        h_eff = min(h, tau)
        s_tot = balanced_s * math.sqrt(tau)
        s_tot_new = math.sqrt(balanced_s**2 * (tau - h_eff) + s_new**2 * h_eff)
        g_star = 4.0 + lead * (s_tot_new - s_tot) / s_tot
        z = rng.standard_normal(n_mc)
        p_no = float((d0 + m_wk * tau + s_tot * z >= 0).mean())
        for g in [g_star - 1.0, g_star + 1.0]:
            final = d0 + m_wk * tau - 4.0 + g + s_tot_new * z
            p = float((final >= 0).mean())
            check_rows.append(
                {
                    "expected_final_margin": lead,
                    "tau": tau,
                    "hold_weeks": h_eff,
                    "s_weekly_new": s_new,
                    "total_gain": g,
                    "breakeven_total_gain": g_star,
                    "p_hit_no_hit": p_no,
                    "p_hit_with_hit": p,
                    "se": math.sqrt(p * (1 - p) / n_mc),
                }
            )
    _write_csv("rank_hit_threshold_mc.csv", check_rows)


# ---------------------------------------------------------------------------
# Study D: chip timing on a DGW scenario tree (smallest honest version).
# ---------------------------------------------------------------------------


def study_chip_timing() -> None:
    """Triple Captain now vs waiting for an uncertain DGW.

    Two-stage tree. Now (tau weeks left) I can play TC on a single fixture:
    my increment ~ N(8.6, 5.8^2), and only 5% of the near-threshold cohort
    plays a chip this week. If I wait: with probability p_dgw the cup run
    materialises a double gameweek in 4 weeks, where TC yields N(14.5, 9.5^2)
    but 35% of the cohort also plays TC there (their mean chip gain enters
    the pace); with probability 1-p_dgw there is no DGW and the fallback is a
    late single-fixture TC worth N(8.2, 5.7^2) with 10% cohort usage.

    The decision is made now, before the cup outcome is known
    (non-anticipative); WAIT's action inside each scenario is the obvious
    one, so the tree has two policies. CLAIRVOYANT knows the scenario and
    upper-bounds the option value.
    """
    balanced = STRAT_BY_NAME["balanced"]
    tau = 12
    scen = {
        # name: (prob, my_mu, my_sd, cohort_share, cohort_mu)
        "now": (1.0, 8.6, 5.8, 0.05, 8.0),
        "dgw": (None, 14.5, 9.5, 0.35, 13.8),
        "no_dgw": (None, 8.2, 5.7, 0.10, 7.8),
    }
    rng = np.random.default_rng(SEED + 3)
    n_mc = 400_000
    rows = []
    for p_dgw in [0.35, 0.55, 0.75]:
        for d0 in [-40, -15, 0, 15, 40]:
            season_sd = balanced.s * math.sqrt(tau)
            drift = balanced.m * tau

            def p_hit_with_chip(my_mu: float, my_sd: float, share: float, cohort_mu: float,
                                i_played: bool) -> float:
                # Pace gains cohort_share * cohort_mu whether or not I play.
                rel_mu = (my_mu if i_played else 0.0) - share * cohort_mu
                rel_var = (my_sd**2 if i_played else 0.0)
                z = (d0 + drift + rel_mu) / math.sqrt(season_sd**2 + rel_var)
                return float(norm.cdf(z))

            # NOW: play this week; in the DGW scenario the cohort still gains.
            _, mu_n, sd_n, sh_n, cmu_n = scen["now"]
            _, mu_d, sd_d, sh_d, cmu_d = scen["dgw"]
            _, mu_f, sd_f, sh_f, cmu_f = scen["no_dgw"]
            # NOW policy: my chip now, then cohort chips land in whichever
            # scenario occurs. Deduct cohort gains from both branches.
            p_now = (
                p_dgw * _phit(d0 + drift + mu_n - sh_n * cmu_n - sh_d * cmu_d, math.hypot(season_sd, sd_n))
                + (1 - p_dgw) * _phit(d0 + drift + mu_n - sh_n * cmu_n - sh_f * cmu_f, math.hypot(season_sd, sd_n))
            )
            # WAIT: cohort's this-week chips land either way; I chip in-branch.
            p_wait = (
                p_dgw * _phit(d0 + drift + mu_d - sh_n * cmu_n - sh_d * cmu_d, math.hypot(season_sd, sd_d))
                + (1 - p_dgw) * _phit(d0 + drift + mu_f - sh_n * cmu_n - sh_f * cmu_f, math.hypot(season_sd, sd_f))
            )
            # CLAIRVOYANT: sees the scenario; picks max in each branch.
            p_clair = (
                p_dgw * max(
                    _phit(d0 + drift + mu_n - sh_n * cmu_n - sh_d * cmu_d, math.hypot(season_sd, sd_n)),
                    _phit(d0 + drift + mu_d - sh_n * cmu_n - sh_d * cmu_d, math.hypot(season_sd, sd_d)),
                )
                + (1 - p_dgw) * max(
                    _phit(d0 + drift + mu_n - sh_n * cmu_n - sh_f * cmu_f, math.hypot(season_sd, sd_n)),
                    _phit(d0 + drift + mu_f - sh_n * cmu_n - sh_f * cmu_f, math.hypot(season_sd, sd_f)),
                )
            )
            for name, p in [("now", p_now), ("wait", p_wait), ("clairvoyant", p_clair)]:
                rows.append(
                    {"p_dgw": p_dgw, "d0": d0, "tau": tau, "policy": name,
                     "p_hit": p, "method": "analytic", "se": 0.0}
                )
            # MC check at one cell per p_dgw
            if d0 == -15:
                u = rng.random(n_mc)
                is_dgw = u < p_dgw
                z_season = rng.standard_normal(n_mc)
                z_chip = rng.standard_normal(n_mc)
                cohort = sh_n * cmu_n + np.where(is_dgw, sh_d * cmu_d, sh_f * cmu_f)
                base = d0 + drift + season_sd * z_season - cohort
                fin_now = base + mu_n + sd_n * z_chip
                my_mu_w = np.where(is_dgw, mu_d, mu_f)
                my_sd_w = np.where(is_dgw, sd_d, sd_f)
                fin_wait = base + my_mu_w + my_sd_w * z_chip
                for name, fin in [("now", fin_now), ("wait", fin_wait)]:
                    p = float((fin >= 0).mean())
                    rows.append(
                        {"p_dgw": p_dgw, "d0": d0, "tau": tau, "policy": name,
                         "p_hit": p, "method": "mc",
                         "se": math.sqrt(p * (1 - p) / n_mc)}
                    )
    _write_csv("rank_chip_timing.csv", rows)


def _phit(mean: float, sd: float) -> float:
    return float(norm.cdf(mean / sd))


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def _write_csv(name: str, rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {k: (f"{v:.6g}" if isinstance(v, float) else v) for k, v in r.items()}
            )
    print(f"wrote {path} ({len(rows)} rows)")


def main() -> None:
    study_state_dependence()
    study_captaincy()
    study_hit_threshold()
    study_chip_timing()


if __name__ == "__main__":
    main()
