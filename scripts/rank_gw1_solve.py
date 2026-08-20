"""GW1 solved in both objectives, side by side, on real inputs.

Run: ``uv run python scripts/rank_gw1_solve.py``

Why this script exists: ``ObjectiveMode.RANK_MV`` needs three things
``EXPECTED_POINTS`` does not -- a :class:`~fpl_edge.rank.state.RankState`,
per-player points variances, and the near-threshold cohort's ownership and
captaincy shares. This assembles all three from measurable sources, records the
provenance of each, and prints the two squads next to each other so the
difference between the objectives is a thing you can look at rather than a claim.

EVIDENCE RULE. Every number that reaches the objective is either measured here
or cited:

* **Variance** is measured from ``fact_player_fixture`` -- four completed
  seasons, 113k player-fixture rows of real FPL scoring. Per position we take
  the mean and variance of gameweek points *conditional on appearing*, then
  convert to an unconditional per-player variance with the law of total variance
  using that player's ``p_play``::

      Var(points) = p * Var(pts | played) + p(1-p) * E[pts | played]^2

  because a player who might not appear carries variance from the appearance
  channel too, and ignoring it understates exactly the players whose ownership
  is most contested. The measured table is written to
  ``docs/platform/rank_points_variance.csv``.

* **Ownership share** is FPL's own published ``selected_by_pct`` at the GW1
  snapshot, from the committed universe fixture. Provenance
  ``ownership_marginals:prior`` -- before GW1 no rival squads are public, so
  marginals are genuinely all there is, and the label says so rather than
  implying a measured joint distribution.

* **Captaincy share** is derived from LiveFPL's predicted effective ownership in
  ``fact_external_ownership``. EO is the mean multiplier the field applies, so
  ``EO - ownership`` is captains minus benched owners; clipped at zero it is a
  lower bound on captaincy share. Approximate, labelled, and real -- not a prior
  dressed as a measurement. Absent that feed the shares are zero and the run
  says so.

* **(m, s)** are the study's stylised 'balanced' archetype
  (``rank_objectives.md`` §1, anchored to the live simulator run in
  ``docs/models/simulator.md`` §9). They are NOT measured here, and the
  RankState provenance records that. The production path measures them per
  squad with :func:`fpl_edge.rank.state.deficit_moments`.

* **D and tau** are the honest pre-GW1 identity: nobody has played, so the
  deficit is exactly zero and 38 gameweeks remain.

The warehouse is read through :meth:`Warehouse.read_copy`, which copies the file
before opening it: DuckDB allows one writer OR many readers per file, and a MILP
solve holding a reader open would block every ingest job for its whole runtime.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.opt import (
    ObjectiveMode,
    OptimizerConfig,
    SolverConfig,
    score_plan,
    solve_horizon,
    validate_plan,
)
from fpl_edge.rank import RankState, build_rank_coefficients, theta
from fpl_edge.types import Position

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "opt"
OUT_DIR = ROOT / "docs" / "platform"
SEASON = "2026-27"

#: Seasons of completed FPL scoring the variance table is measured from.
HISTORY_SEASONS = ("2022-23", "2023-24", "2024-25", "2025-26")


# ---------------------------------------------------------------------------
# Evidence: per-position points moments, measured from the warehouse
# ---------------------------------------------------------------------------


def measure_points_moments() -> pd.DataFrame:
    """Mean and variance of gameweek points by position, given an appearance.

    Conditional on ``minutes > 0``: the zero a non-appearance produces is not a
    property of how the player scores, it is the appearance channel, and mixing
    the two here would double-count it once ``p_play`` is applied.
    """
    from fpl_edge.store import Warehouse

    wh = Warehouse.read_copy()
    seasons = ", ".join(f"'{s}'" for s in HISTORY_SEASONS)
    frame = wh.sql(
        f"""
        SELECT p.position AS position,
               COUNT(*)                      AS n_appearances,
               AVG(f.total_points)           AS mean_points,
               VAR_SAMP(f.total_points)      AS var_points
        FROM fact_player_fixture f
        JOIN (SELECT DISTINCT season, code, position FROM dim_player) p
          ON p.season = f.season AND p.code = f.code
        WHERE f.season IN ({seasons}) AND f.minutes > 0
        GROUP BY 1
        ORDER BY 1
        """
    )
    frame["position_name"] = [Position(int(p)).name for p in frame["position"]]
    frame["seasons"] = "+".join(HISTORY_SEASONS)
    return frame


def player_variances(
    problem, moments: pd.DataFrame, p_play: np.ndarray
) -> np.ndarray:
    """Unconditional per-(player, gameweek) points variance.

    Law of total variance over {did not appear -> 0, appeared -> X}::

        Var = p * Var(X) + p(1-p) * E[X]^2

    The second term is the appearance channel and it is not small: a 60%-certain
    premium carries far more variance than his conditional distribution alone
    suggests, which is precisely the risk a rank-aware objective should price.
    """
    mean_by_pos = dict(zip(moments["position"].astype(int), moments["mean_points"]))
    var_by_pos = dict(zip(moments["position"].astype(int), moments["var_points"]))
    out = np.zeros_like(p_play, dtype=np.float64)
    for i, row in enumerate(problem.players):
        pos = int(row.position)
        mu_cond = float(mean_by_pos[pos])
        var_cond = float(var_by_pos[pos])
        p = np.clip(p_play[i, :], 0.0, 1.0)
        out[i, :] = p * var_cond + p * (1.0 - p) * mu_cond**2
    return out


# ---------------------------------------------------------------------------
# Evidence: cohort shares
# ---------------------------------------------------------------------------


def cohort_shares(problem, universe: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, str, list[str]]:
    """``(own_share, captain_share, provenance, notes)`` from real feeds."""
    from fpl_edge.rank import PROVENANCE_OWNERSHIP_MARGINALS
    from fpl_edge.store import Warehouse

    notes: list[str] = []
    n, t = problem.n_players, problem.n_gws
    own_by_code = dict(
        zip(universe["code"].astype(int), universe["selected_by_pct"].astype(float) / 100.0)
    )
    own = np.zeros((n, t))
    for i, row in enumerate(problem.players):
        own[i, :] = np.clip(own_by_code.get(int(row.code), 0.0), 0.0, 1.0)
    notes.append(
        f"ownership: FPL selected_by_pct at the GW1 snapshot "
        f"(mean {own[:, 0].mean():.4f}, max {own[:, 0].max():.4f}, "
        f"sums to {own[:, 0].sum():.2f} across {n} players)"
    )

    cap = np.zeros((n, t))
    try:
        wh = Warehouse.read_copy()
        eo = wh.sql(
            """
            SELECT code, value AS eo
            FROM fact_external_ownership
            WHERE season = ? AND gw = 1 AND metric = 'eo_predicted'
            """,
            [SEASON],
        )
    except Exception as exc:  # noqa: BLE001
        eo = pd.DataFrame(columns=["code", "eo"])
        notes.append(f"captaincy share unavailable ({exc}); using zero.")

    if len(eo):
        eo_by_code = dict(zip(eo["code"].astype(int), eo["eo"].astype(float)))
        scale = 100.0 if max(eo_by_code.values(), default=0.0) > 1.5 else 1.0
        for i, row in enumerate(problem.players):
            e = eo_by_code.get(int(row.code))
            if e is None:
                continue
            cap[i, :] = np.clip(e / scale - own[i, 0], 0.0, 1.0)
        notes.append(
            f"captaincy share: max(livefpl eo_predicted - ownership, 0) for "
            f"{sum(1 for r in problem.players if int(r.code) in eo_by_code)} of {n} "
            f"players (max {cap[:, 0].max():.4f}). A LOWER BOUND: EO nets benched "
            "owners against captains, so this understates captaincy for players "
            "who are widely owned and widely benched."
        )
    else:
        notes.append(
            "captaincy share: NO external EO feed for GW1; every share is zero, "
            "which prices the armband as a pure differential. Read the RANK_MV "
            "captain below with that in mind."
        )
    return own, cap, PROVENANCE_OWNERSHIP_MARGINALS, notes


# ---------------------------------------------------------------------------
# The solve
# ---------------------------------------------------------------------------


def load_problem(gws=(1,)):
    from tests.unit.test_opt_support import fixture_problem

    return fixture_problem(gws)


def describe(problem, plan, coef=None) -> list[str]:
    """One squad, rendered by position with the armband marked."""
    by_code = {int(p.code): p for p in problem.players}
    idx = problem.index_of
    d = plan.decisions[0]
    xi = set(int(c) for c in d.starting_xi)
    lines = []
    for pos in (Position.GKP, Position.DEF, Position.MID, Position.FWD):
        members = [int(c) for c in d.squad if by_code[int(c)].position is pos]
        rendered = []
        for code in sorted(members, key=lambda c: (int(c) not in xi, by_code[c].name)):
            row = by_code[code]
            mark = ""
            if code == int(d.captain):
                mark = " (C)"
            elif code == int(d.vice_captain):
                mark = " (V)"
            if code not in xi:
                mark += " [bench]"
            price = int(problem.price_tenths[idx[code], 0]) / 10.0
            rendered.append(f"{row.name} {price:.1f}{mark}")
        lines.append(f"  {pos.name:<4} {', '.join(rendered)}")
    return lines


def solve_both(problem, state: RankState, variance, own, cap, provenance, seconds: float):
    coef = build_rank_coefficients(
        problem, state,
        variance=variance, own_share=own, captain_share=cap,
        provenance=provenance,
    )
    plain_cfg = OptimizerConfig(
        mode=ObjectiveMode.EXPECTED_POINTS,
        solver=SolverConfig(time_limit_s=seconds),
    )
    rank_cfg = OptimizerConfig(
        mode=ObjectiveMode.RANK_MV,
        solver=SolverConfig(time_limit_s=seconds),
    )
    plain = solve_horizon(problem, plain_cfg)
    rank = solve_horizon(problem, rank_cfg, rank_mv=coef)
    return plain, plain_cfg, rank, rank_cfg, coef


def report(problem, state, plain, plain_cfg, rank, rank_cfg, coef) -> None:
    by_code = {int(p.code): p for p in problem.players}
    print()
    print("=" * 78)
    print(f"RANK STATE: {state.describe()}")
    print(f"theta = {theta(state):+.6f} per point^2   "
          f"({'BEHIND -> variance is a good' if state.behind else 'AHEAD -> variance is a cost'})")
    print("=" * 78)

    for name, plan, cfg in (
        ("EXPECTED_POINTS", plain, plain_cfg),
        ("RANK_MV", rank, rank_cfg),
    ):
        errors = validate_plan(problem, plan)
        mv = coef if cfg.mode is ObjectiveMode.RANK_MV else None
        recomputed = score_plan(problem, plan, cfg, rank_mv=mv)
        print()
        print(f"--- {name} " + "-" * (74 - len(name)))
        for line in describe(problem, plan):
            print(line)
        d = plan.decisions[0]
        print(f"  captain      {by_code[int(d.captain)].name}   "
              f"vice {by_code[int(d.vice_captain)].name}")
        print(f"  squad value  {d.squad_value.tenths / 10:.1f}m   "
              f"bank {d.bank_after.tenths / 10:.1f}m   chip {d.chip}")
        print(f"  objective    {plan.objective:.4f}  "
              f"(scorer agrees: {recomputed:.4f}, "
              f"diff {abs(plan.objective - recomputed):.2e})")
        print(f"  status       {plan.status}  gap "
              f"{'n/a' if plan.mip_gap is None else f'{plan.mip_gap:.2e}'}  "
              f"{plan.solve_seconds:.1f}s  validation errors: {len(errors)}")

    a = {int(c) for c in plain.decisions[0].squad}
    b = {int(c) for c in rank.decisions[0].squad}
    xi_a = {int(c) for c in plain.decisions[0].starting_xi}
    xi_b = {int(c) for c in rank.decisions[0].starting_xi}
    print()
    print("--- DIFF (RANK_MV vs EXPECTED_POINTS) " + "-" * 39)
    print(f"  squad: {len(b - a)} of 15 players differ")
    if b - a:
        print("    rank-only  : " + ", ".join(sorted(by_code[c].name for c in b - a)))
    if a - b:
        print("    points-only: " + ", ".join(sorted(by_code[c].name for c in a - b)))
    # The squad and the XI are separate decisions and can disagree: the same
    # fifteen with a different eleven is a real change of posture.
    print(f"  XI   : {len(xi_b - xi_a)} of 11 differ")
    if xi_b - xi_a:
        print("    started by rank only  : "
              + ", ".join(sorted(by_code[c].name for c in xi_b - xi_a)))
    if xi_a - xi_b:
        print("    benched by rank only  : "
              + ", ".join(sorted(by_code[c].name for c in xi_a - xi_b)))
    cap_a = by_code[int(plain.decisions[0].captain)].name
    cap_b = by_code[int(rank.decisions[0].captain)].name
    print(f"  captain: {cap_a} -> {cap_b}"
          + ("  (unchanged)" if cap_a == cap_b else "  CHANGED"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, default=300.0, help="solver time limit")
    ap.add_argument(
        "--deficit", type=float, default=None,
        help="also solve a hypothetical deficit state, to show the switch",
    )
    ap.add_argument("--tau", type=int, default=38)
    args = ap.parse_args()

    print("Measuring per-position points moments from the warehouse...")
    moments = measure_points_moments()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    moments.to_csv(OUT_DIR / "rank_points_variance.csv", index=False)
    print(moments.to_string(index=False))
    print(f"-> {OUT_DIR / 'rank_points_variance.csv'}")

    problem = load_problem((1,))
    universe = pd.read_csv(FIXTURES / "universe_2026_27.csv")
    variance = player_variances(problem, moments, problem.p_play)
    own, cap, provenance, notes = cohort_shares(problem, universe)

    print()
    print(f"Universe: {problem.n_players} players, GW{int(problem.gws[0])}.")
    print(f"Variance: mean {variance[:, 0].mean():.2f}, "
          f"max {variance[:, 0].max():.2f} (unconditional, law of total variance)")
    for note in notes:
        print(f"  note: {note}")

    from fpl_edge.rank.policy import BALANCED

    states = [
        RankState.stylised(
            deficit=0.0, tau=38, m_weekly=BALANCED.m, s_weekly=BALANCED.s,
            notes=("pre-GW1: D=0 and tau=38 are identities, not estimates",),
        )
    ]
    if args.deficit is not None:
        states.append(
            RankState.stylised(
                deficit=args.deficit, tau=args.tau,
                m_weekly=BALANCED.m, s_weekly=BALANCED.s,
            )
        )

    for state in states:
        report(problem, state, *solve_both(
            problem, state, variance, own, cap, provenance, args.seconds
        ))


if __name__ == "__main__":
    main()
