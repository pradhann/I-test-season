"""Assemble RANK_MV's evidence from a live warehouse.

``ObjectiveMode.RANK_MV`` needs three inputs ``EXPECTED_POINTS`` does not: a
:class:`~fpl_edge.rank.state.RankState`, per-(player, gameweek) points
variances, and the near-threshold cohort's ownership and captaincy shares.
These assemblers produce all three from measurable sources with the provenance
of each recorded -- they are the live-warehouse versions of the functions
proven out in ``scripts/rank_gw1_solve.py`` (which reads committed fixtures so
its output is reproducible in review; this module reads the warehouse the
production solve actually runs against).

EVIDENCE RULE, unchanged: every number that reaches the objective is either
measured here or its absence is recorded in the returned notes. Nothing is
silently defaulted.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fpl_edge.types import Position

#: Completed seasons the per-position points moments are measured from.
HISTORY_SEASONS: tuple[str, ...] = ("2022-23", "2023-24", "2024-25", "2025-26")


def points_moments(wh, seasons: tuple[str, ...] = HISTORY_SEASONS) -> pd.DataFrame:
    """Mean and variance of gameweek points by position, given an appearance.

    Conditional on ``minutes > 0``: the zero a non-appearance produces is the
    appearance channel, not a property of how the player scores, and mixing
    the two here would double-count it once ``p_play`` is applied.

    ``wh`` is an already-open warehouse (a read copy, typically); this function
    does not own its lifetime.
    """
    marks = ", ".join("?" for _ in seasons)
    frame = wh.sql(
        f"""
        SELECT p.position AS position,
               COUNT(*)                      AS n_appearances,
               AVG(f.total_points)           AS mean_points,
               VAR_SAMP(f.total_points)      AS var_points
        FROM fact_player_fixture f
        JOIN (SELECT DISTINCT season, code, position FROM dim_player) p
          ON p.season = f.season AND p.code = f.code
        WHERE f.season IN ({marks}) AND f.minutes > 0
        GROUP BY 1
        ORDER BY 1
        """,
        list(seasons),
    )
    if frame.empty:
        raise ValueError(
            f"no appearance rows for seasons {seasons}; variance cannot be "
            "measured and RANK_MV must not run on invented moments"
        )
    frame["position_name"] = [Position(int(p)).name for p in frame["position"]]
    frame["seasons"] = "+".join(seasons)
    return frame


def player_variances(problem, moments: pd.DataFrame, p_play: np.ndarray) -> np.ndarray:
    """Unconditional per-(player, gameweek) points variance.

    Law of total variance over {did not appear -> 0, appeared -> X}::

        Var = p * Var(X) + p(1-p) * E[X]^2

    The second term is the appearance channel and it is not small: a
    60%-certain premium carries far more variance than his conditional
    distribution alone suggests -- precisely the risk a rank-aware objective
    should price.
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


def cohort_shares(
    problem, snapshot, wh, season: str, gw: int
) -> tuple[np.ndarray, np.ndarray, str, list[str]]:
    """``(own_share, captain_share, provenance, notes)`` from real feeds.

    Ownership is FPL's own ``selected_by_pct`` at the snapshot -- marginals,
    and the provenance constant says so rather than implying a measured joint
    distribution. Captaincy is a lower bound derived from an external EO feed
    where one exists for this gameweek; absent that, shares are zero and the
    notes say what that does to the captain choice.
    """
    from fpl_edge.rank import PROVENANCE_OWNERSHIP_MARGINALS

    notes: list[str] = []
    n, t = problem.n_players, problem.n_gws

    players = snapshot.players(season)
    own_by_code = dict(
        zip(players["code"].astype(int), players["selected_by_pct"].astype(float) / 100.0)
    )
    own = np.zeros((n, t))
    for i, row in enumerate(problem.players):
        own[i, :] = np.clip(own_by_code.get(int(row.code), 0.0), 0.0, 1.0)
    notes.append(
        f"ownership: FPL selected_by_pct at the GW{gw} snapshot "
        f"(mean {own[:, 0].mean():.4f}, max {own[:, 0].max():.4f}, "
        f"sums to {own[:, 0].sum():.2f} across {n} players)"
    )

    cap = np.zeros((n, t))
    try:
        eo = wh.sql(
            """
            SELECT code, value AS eo
            FROM fact_external_ownership
            WHERE season = ? AND gw = ? AND metric = 'eo_predicted'
            """,
            [season, int(gw)],
        )
    except Exception as exc:  # noqa: BLE001 - a missing feed is a note, not a crash
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
            f"captaincy share: max(external eo_predicted - ownership, 0) for "
            f"{sum(1 for r in problem.players if int(r.code) in eo_by_code)} of {n} "
            f"players (max {cap[:, 0].max():.4f}). A LOWER BOUND: EO nets benched "
            "owners against captains, so this understates captaincy for players "
            "who are widely owned and widely benched."
        )
    else:
        notes.append(
            f"captaincy share: no external EO feed for GW{gw}; every share is "
            "zero, which prices the armband as a pure differential. Read the "
            "RANK_MV captain with that in mind."
        )
    return own, cap, PROVENANCE_OWNERSHIP_MARGINALS, notes
