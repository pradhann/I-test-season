"""Did copying them actually work? Measured after the fact, per decision.

This module exists because of the failure mode that makes the entire copying
thesis dangerous: **a manager being highly ranked does not make their next
transfer good.** Rank is a slow-moving summary of a hundred past decisions; the
transfer they made this morning is one new decision, taken under the same
uncertainty everyone else faces, and it is entirely possible for a genuinely
elite manager to have a below-average transfer record over any given ten-week
stretch. Copying without measuring is faith.

So every copied signal is scored against a counterfactual, and the scoring is
deliberately unforgiving.

Transfers
---------
A transfer is not "good" because the player brought in scored points. It is good
if he outscored the player sold, over the horizon the transfer was made for,
after the hit that was paid for it::

    surplus = points(in, gw..gw+h) - points(out, gw..gw+h) - hit_cost

Scoring the incoming player alone is the standard way to make every transfer
look like a triumph, because most transfers bring in a player who then scores
*something*. The subtraction is the entire measurement.

Picks
-----
A pick is scored against the field, not against zero, because owning a player
who scores 6 while the field's equivalent scores 7 is a *loss* of rank even
though it looks like a gain of points. The counterfactual is the position-
matched template player, so "elite own Salah" is judged against "field own the
most-owned midfielder", which is the choice actually being made.

Captaincy
---------
Scored as the difference from captaining the field's most-captained player,
doubled, since that is what the armband multiplies.

The number that decides everything
----------------------------------
:func:`edge_summary` returns the mean surplus per copied decision with its
standard error and a t-statistic. If the interval spans zero after a reasonable
number of decisions, the honest conclusion is that copying this cohort adds
nothing measurable, and the engine should say so rather than continue on the
strength of the cohort's reputation.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

TRANSFER_COLUMNS = [
    "entry_id", "season", "gw", "element_in", "element_out", "horizon",
    "points_in", "points_out", "hit_cost", "surplus", "success",
]

PICK_COLUMNS = [
    "entry_id", "season", "gw", "element_id", "position", "multiplier",
    "points", "counterfactual_points", "surplus",
]


@dataclass(frozen=True, slots=True)
class EdgeSummary:
    """Whether a copied signal paid, with the interval that decides it."""

    n: int
    mean_surplus: float
    se: float
    t_stat: float
    p_value: float
    ci_low: float
    ci_high: float
    hit_rate: float

    @property
    def verdict(self) -> str:
        if self.n < 20:
            return (f"n={self.n}: too few decisions to conclude anything. "
                    f"Point estimate {self.mean_surplus:+.2f} pts/decision.")
        if self.ci_low <= 0 <= self.ci_high:
            return (f"n={self.n}: {self.mean_surplus:+.2f} pts/decision, 95% CI "
                    f"[{self.ci_low:+.2f}, {self.ci_high:+.2f}] spans zero. "
                    f"No measurable edge from copying.")
        direction = "positive" if self.mean_surplus > 0 else "NEGATIVE"
        return (f"n={self.n}: {self.mean_surplus:+.2f} pts/decision, 95% CI "
                f"[{self.ci_low:+.2f}, {self.ci_high:+.2f}] -- measurable and "
                f"{direction}.")


def score_transfers(
    transfers: pd.DataFrame,
    player_gw_points: pd.DataFrame,
    *,
    horizon: int = 6,
    hit_cost_by_gw: dict[tuple[int, int], int] | None = None,
) -> pd.DataFrame:
    """Surplus of each transfer over the player it replaced.

    ``horizon`` is the number of gameweeks the transfer is judged over, counting
    the gameweek it took effect. Six is the default because it is roughly the
    span over which a transfer is a live decision rather than a historical one,
    and because judging on the single following week rewards luck and punishes
    correctly buying a player with a hard opening fixture.

    Transfers whose horizon extends past the last scored gameweek are **dropped**,
    not truncated. Truncating scores an early transfer over six weeks and a
    recent one over one, then averages them as if they were the same
    measurement, which biases the result toward whatever happened most recently.
    """
    if transfers is None or transfers.empty:
        return pd.DataFrame(columns=TRANSFER_COLUMNS)

    pts = {
        (int(r.element_id), int(r.gw)): float(r.points)
        for r in player_gw_points.itertuples()
    }
    last_gw = int(player_gw_points["gw"].max())

    rows = []
    for t in transfers.itertuples():
        gw = int(t.gw)
        if gw + horizon - 1 > last_gw:
            continue
        window = range(gw, gw + horizon)
        p_in = sum(pts.get((int(t.element_in), g), 0.0) for g in window)
        p_out = sum(pts.get((int(t.element_out), g), 0.0) for g in window)
        cost = float((hit_cost_by_gw or {}).get((int(t.entry_id), gw), 0))
        surplus = p_in - p_out - cost
        rows.append({
            "entry_id": int(t.entry_id), "season": str(t.season), "gw": gw,
            "element_in": int(t.element_in), "element_out": int(t.element_out),
            "horizon": horizon, "points_in": p_in, "points_out": p_out,
            "hit_cost": cost, "surplus": surplus, "success": bool(surplus > 0),
        })
    return pd.DataFrame(rows, columns=TRANSFER_COLUMNS)


def score_picks_against_template(
    picks: pd.DataFrame,
    player_gw_points: pd.DataFrame,
    players: pd.DataFrame,
    *,
    gw: int,
) -> pd.DataFrame:
    """Each held player's points against the position-matched template player.

    The counterfactual is the most-selected player in the same position who was
    available at that deadline. That is the right comparison because a squad
    slot is positionally constrained: not owning Salah does not mean owning
    nothing, it means owning whichever midfielder the field owns instead.
    """
    if picks.empty or players.empty:
        return pd.DataFrame(columns=PICK_COLUMNS)

    week_players = players[players["gw"] == gw] if "gw" in players.columns else players
    pts = {
        (int(r.element_id), int(r.gw)): float(r.points)
        for r in player_gw_points.itertuples()
    }
    template_by_pos = (
        week_players.sort_values("selected_by_pct", ascending=False)
        .groupby("position")["element_id"].first().to_dict()
    )
    pos_of = dict(zip(week_players["element_id"], week_players["position"]))

    rows = []
    for p in picks[picks["gw"] == gw].itertuples():
        pos = pos_of.get(int(p.element_id))
        mult = float(p.multiplier or 0)
        own = pts.get((int(p.element_id), gw), 0.0) * mult
        alt_id = template_by_pos.get(pos)
        # The counterfactual gets the SAME multiplier: the comparison is
        # "this player in this slot" versus "the template player in this slot",
        # not "this captained player" versus "that benched one".
        alt = pts.get((int(alt_id), gw), 0.0) * mult if alt_id is not None else 0.0
        rows.append({
            "entry_id": int(p.entry_id), "season": str(p.season), "gw": gw,
            "element_id": int(p.element_id), "position": pos, "multiplier": mult,
            "points": own, "counterfactual_points": alt, "surplus": own - alt,
        })
    return pd.DataFrame(rows, columns=PICK_COLUMNS)


def score_captaincy(
    picks: pd.DataFrame,
    player_gw_points: pd.DataFrame,
    most_captained: dict[int, int],
) -> pd.DataFrame:
    """Surplus of each cohort captain over the field's most-captained player."""
    pts = {
        (int(r.element_id), int(r.gw)): float(r.points)
        for r in player_gw_points.itertuples()
    }
    caps = picks[picks["is_captain"].fillna(False)]
    rows = []
    for c in caps.itertuples():
        gw = int(c.gw)
        favourite = most_captained.get(gw)
        if favourite is None:
            continue
        mine = pts.get((int(c.element_id), gw), 0.0)
        theirs = pts.get((int(favourite), gw), 0.0)
        rows.append({
            "entry_id": int(c.entry_id), "gw": gw,
            "captain": int(c.element_id), "field_captain": int(favourite),
            "deviated": int(c.element_id) != int(favourite),
            # Doubled, because the armband is what multiplies the difference.
            "surplus": 2.0 * (mine - theirs),
        })
    return pd.DataFrame(rows, columns=[
        "entry_id", "gw", "captain", "field_captain", "deviated", "surplus"
    ])


def edge_summary(surplus: pd.Series | np.ndarray) -> EdgeSummary:
    """Mean surplus per decision with a t-interval, and the honest verdict.

    Decisions by the same manager in the same week are correlated -- an elite
    cohort largely transfers in the same player -- so the naive standard error
    below is optimistic. :func:`cluster_robust_se` gives the corrected version
    and should be preferred whenever the cohort is more than a handful.
    """
    arr = np.asarray(pd.Series(surplus).dropna(), dtype=float)
    n = arr.size
    if n < 2:
        return EdgeSummary(n, float(arr.mean()) if n else float("nan"),
                           float("nan"), float("nan"), float("nan"),
                           float("nan"), float("nan"), float("nan"))
    mean = float(arr.mean())
    se = float(arr.std(ddof=1) / np.sqrt(n))
    t = mean / se if se > 0 else float("nan")
    p = float(2 * (1 - stats.t.cdf(abs(t), df=n - 1))) if np.isfinite(t) else float("nan")
    crit = float(stats.t.ppf(0.975, df=n - 1))
    return EdgeSummary(n, mean, se, t, p, mean - crit * se, mean + crit * se,
                       float((arr > 0).mean()))


def cluster_robust_se(surplus: pd.DataFrame, *, cluster: str = "gw") -> float:
    """Standard error that accounts for everyone copying the same move at once.

    When forty elite managers all transfer in the same striker in GW7, that is
    one bet observed forty times, not forty independent bets. Treating it as
    forty shrinks the standard error by a factor of roughly six and turns a
    coin-flip into a "significant" edge. Clustering on the gameweek fixes the
    dominant part of that.
    """
    if surplus.empty or cluster not in surplus.columns:
        return float("nan")
    means = surplus.groupby(cluster)["surplus"].mean()
    k = len(means)
    if k < 2:
        return float("nan")
    return float(means.std(ddof=1) / np.sqrt(k))
