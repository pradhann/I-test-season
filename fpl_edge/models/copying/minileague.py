"""Mini-league mode: a different game, played against people you can name.

Optimising for overall rank and optimising for a 39-player mini-league are not
the same problem, and treating them as one is the most common strategic error in
FPL. Four things change:

**The denominator is tiny.** In the global field, a player owned by 30% of
managers costs you almost exactly 30% of his points in expectation. In a league
of 39, "30% ownership" is twelve specific people, and which twelve matters --
if the three above you all own him and the nine below you do not, he is a cover
pick, not a differential, and the average ownership figure says nothing about
that.

**Variance changes sign.** Against six million entries, variance is broadly good
when chasing a high rank: you cannot finish 1,000th by matching the field. In a
league of 39 where you lead by 60 points with five gameweeks left, variance is
the only thing that can beat you, and the correct play is to converge on the
squads of the people chasing you -- deliberately giving up expected points to
remove the paths where you lose.

**Position is known exactly.** Global rank is a lagging estimate. Mini-league
position is a fact you can read at any moment, and it determines the whole
strategy. Trailing by 80 with six weeks left is a different game from trailing
by 8.

**The opposition is legible.** You can read your rivals' actual squads after
every deadline. Nothing about the global field is that observable.

This module computes the quantities those four facts imply: league effective
ownership, per-rival exposure, and the cover-versus-attack recommendation that
falls out of your position and the time remaining.

Point-in-time honesty applies unchanged. Rival squads are readable only after a
deadline passes, so every function here consumes picks that were already public
and none of them can see the squad a rival is about to submit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Points per gameweek of standard deviation in a single manager's score. Used
#: to convert a lead into "how many weeks of noise is this worth". Derived from
#: the observed spread of gameweek scores rather than assumed; the default is a
#: placeholder replaced by :func:`calibrate_gw_sigma` wherever real per-gameweek
#: scores exist.
DEFAULT_GW_SIGMA = 18.0


@dataclass(frozen=True, slots=True)
class LeaguePosition:
    """Where the user stands in one mini-league, and how much game is left."""

    league_id: int
    league_name: str
    my_entry: int
    my_points: int
    my_rank: int
    n_entries: int
    leader_points: int
    #: Points to the entry immediately above; the margin that decides most
    #: mini-leagues, as opposed to the gap to the leader which usually does not.
    gap_to_next_above: int
    gap_to_next_below: int
    gws_remaining: int

    @property
    def leading(self) -> bool:
        return self.my_rank == 1


def calibrate_gw_sigma(gws: pd.DataFrame) -> float:
    """SD of a single manager's gameweek score, measured from the pool.

    Estimated within manager and then pooled, so that the spread of *ability*
    across managers does not inflate what is meant to be the spread of one
    manager's week-to-week luck.
    """
    if gws is None or gws.empty or "points" not in gws.columns:
        return DEFAULT_GW_SIGMA
    per = gws.groupby("entry_id")["points"].std(ddof=1).dropna()
    return float(per.mean()) if len(per) else DEFAULT_GW_SIGMA


def league_effective_ownership(
    picks: pd.DataFrame, *, gw: int, entry_ids: list[int]
) -> pd.DataFrame:
    """EO within the league, plus the per-rival exposure the average hides.

    ``owned_by_above`` and ``owned_by_below`` are the columns that matter and the
    ones a global-ownership view cannot produce. A player owned by everyone
    above you is a *hole in your defence*: every week he returns, you lose ground
    to all of them at once. A player owned by everyone below you is a shield --
    owning him costs nothing relative to the people you must hold off.
    """
    week = picks[(picks["gw"] == gw) & (picks["entry_id"].isin(entry_ids))]
    if week.empty:
        return pd.DataFrame(columns=[
            "element_id", "owners", "n_league", "ownership", "eo",
            "owned_by_above", "owned_by_below",
        ])
    n = week["entry_id"].nunique()
    mult = week["multiplier"].fillna(0)
    grp = week.groupby("element_id")
    owners = grp["entry_id"].nunique()
    starts = week.assign(_s=(mult >= 1).astype(int)).groupby("element_id")["_s"].sum()
    caps = week.assign(_c=(mult >= 2).astype(int)).groupby("element_id")["_c"].sum()
    tcs = week.assign(_t=(mult >= 3).astype(int)).groupby("element_id")["_t"].sum()
    return pd.DataFrame({
        "element_id": owners.index,
        "owners": owners.to_numpy(),
        "n_league": n,
        "ownership": owners.to_numpy() / n,
        "eo": (starts.reindex(owners.index).fillna(0)
               + caps.reindex(owners.index).fillna(0)
               + tcs.reindex(owners.index).fillna(0)).to_numpy() / n,
        "owned_by_above": np.nan,
        "owned_by_below": np.nan,
    }).reset_index(drop=True)


def exposure_by_rival_group(
    picks: pd.DataFrame,
    *,
    gw: int,
    above: list[int],
    below: list[int],
) -> pd.DataFrame:
    """Ownership split by whether the rival is ahead of or behind the user.

    This is the whole point of mini-league mode in one table. The decision rule
    it supports is asymmetric: cover what the people above you own, attack where
    the people below you are exposed, and ignore the global template entirely.
    """
    week = picks[picks["gw"] == gw]
    rows = []
    for element, grp in week.groupby("element_id"):
        holders = set(int(e) for e in grp["entry_id"])
        rows.append({
            "element_id": int(element),
            "owned_by_above": len(holders & set(above)),
            "n_above": len(above),
            "owned_by_below": len(holders & set(below)),
            "n_below": len(below),
            "share_above": len(holders & set(above)) / len(above) if above else np.nan,
            "share_below": len(holders & set(below)) / len(below) if below else np.nan,
        })
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Threat: owned by those chasing/leading you and not by you. Positive means
    # this player can only cost you ground.
    out["threat"] = out["share_above"].fillna(0) - out["share_below"].fillna(0)
    return out.sort_values("threat", ascending=False).reset_index(drop=True)


@dataclass(frozen=True, slots=True)
class Stance:
    """The recommended posture in one league, with the arithmetic behind it."""

    league_id: int
    posture: str            # 'cover' | 'neutral' | 'attack' | 'desperate'
    lead_in_sigmas: float
    rationale: str


def recommend_stance(
    position: LeaguePosition, *, gw_sigma: float = DEFAULT_GW_SIGMA
) -> Stance:
    """Cover or attack, decided by the lead measured in units of remaining noise.

    The only quantity that matters is the lead divided by the standard deviation
    of the points still to be played. A 60-point lead with two gameweeks left is
    unassailable; the same lead with twenty gameweeks left is barely more than
    one standard deviation and is not a lead at all. Expressing it in sigmas
    makes the two cases obviously different, which raw points does not.

    The residual SD of the *difference* between two managers over ``k`` weeks is
    ``sigma * sqrt(2k)`` -- both scores vary, so the variance adds. Forgetting
    the factor of two is a common error and makes every lead look 40% safer than
    it is.
    """
    k = max(position.gws_remaining, 0)
    resid = gw_sigma * np.sqrt(2 * k) if k > 0 else 1e-9

    if position.leading:
        lead = position.gap_to_next_below
        sigmas = lead / resid
        if sigmas > 2.0:
            return Stance(position.league_id, "cover", float(sigmas),
                          f"Leading by {lead} with {k} GWs left = {sigmas:.1f} sigma. "
                          f"Converge on the chasers' squads: give up expected points "
                          f"to delete the paths where you lose.")
        if sigmas > 0.75:
            return Stance(position.league_id, "cover", float(sigmas),
                          f"Leading by {lead} ({sigmas:.1f} sigma). Cover the chasers' "
                          f"differentials; do not initiate new ones.")
        return Stance(position.league_id, "neutral", float(sigmas),
                      f"Nominal lead of {lead} is only {sigmas:.1f} sigma over {k} GWs. "
                      f"Play the best squad; the lead is not yet an asset to protect.")

    deficit = position.gap_to_next_above
    sigmas = deficit / resid
    if sigmas < 0.75:
        return Stance(position.league_id, "neutral", float(-sigmas),
                      f"Behind by {deficit} ({sigmas:.1f} sigma over {k} GWs). "
                      f"Within noise: maximise expected points, do not force variance.")
    if sigmas < 2.0:
        return Stance(position.league_id, "attack", float(-sigmas),
                      f"Behind by {deficit} ({sigmas:.1f} sigma). Take on the "
                      f"differentials the leaders lack; matching them cannot win.")
    return Stance(position.league_id, "desperate", float(-sigmas),
                  f"Behind by {deficit} ({sigmas:.1f} sigma over {k} GWs). Only "
                  f"high-variance lines have any chance; expected points is now "
                  f"the wrong objective entirely.")


def cover_targets(
    exposure: pd.DataFrame, my_squad: set[int], *, top_n: int = 8
) -> pd.DataFrame:
    """Players owned by rivals above you that you do not own. The holes."""
    if exposure.empty:
        return exposure
    gaps = exposure[
        (~exposure["element_id"].isin(my_squad)) & (exposure["share_above"] > 0)
    ]
    return gaps.sort_values("share_above", ascending=False).head(top_n).reset_index(drop=True)


def attack_targets(
    exposure: pd.DataFrame, my_squad: set[int], *, max_share_above: float = 0.25,
    top_n: int = 8
) -> pd.DataFrame:
    """Players the rivals above you mostly do not own. The levers.

    Filtered on the rivals above rather than on global ownership: a 40%-owned
    player is a perfectly good mini-league differential if none of the four
    people ahead of you happen to have him.
    """
    if exposure.empty:
        return exposure
    cands = exposure[
        (~exposure["element_id"].isin(my_squad))
        & (exposure["share_above"].fillna(0) <= max_share_above)
    ]
    return cands.sort_values("share_above").head(top_n).reset_index(drop=True)
