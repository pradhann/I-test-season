"""What the skilled own that the field does not, ranked by how copyable it is.

The output of this module is a list of ideas in priority order. An idea is a
player, and its priority is driven by two quantities that pull in opposite
directions:

**Concentration.** What share of the skilled cohort owns them. A player owned by
three of forty good managers is a rumour; one owned by thirty of forty is a
consensus, and a consensus among people with real records is the thing worth
copying.

**Absence from the template.** What share of the whole field owns them. A player
owned by 70% of the skilled *and* 68% of everybody is not an edge -- copying him
changes nothing about where you finish, because rank is decided by differences
from the field, not by points. The edge is the gap.

Multiplying the two gives the ranking. A player at 80% elite / 10% field scores
far above one at 95% elite / 90% field, which is the correct ordering for
someone trying to gain rank rather than accumulate points.

Effective ownership, not headcount
----------------------------------
Ownership share is the wrong denominator for anything involving a captain. A
player captained by half the elite contributes twice his score to those
managers' totals, and the quantity that decides rank is the *mean multiplier*
the cohort applies -- effective ownership. This module computes both and ranks
on EO gap, because the biggest single-week rank swings in FPL come from
captaincy divergence rather than ownership divergence, and a ranking built on
headcount is blind to exactly those.

The formula follows :mod:`fpl_edge.models.ownership.eo`: a triple captain
contributes 3 = started + captained + one more, so the triple-captain share is
added once more, not twice.

Risk, stated per idea
---------------------
Every idea carries the number that decides whether it is worth acting on: how
much rank you lose if it fails. A 40%-elite / 3%-field differential is a large
edge if he returns and a large hole if he blanks, and the ranking deliberately
does not hide that asymmetry inside a single score.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

IDEA_COLUMNS = [
    "element_id", "web_name", "position", "price_tenths",
    "elite_ownership", "elite_start_share", "elite_captain_share", "elite_eo",
    "field_ownership", "field_eo",
    "ownership_gap", "eo_gap", "concentration", "priority",
    "n_elite_owners", "n_elite", "owners_se", "verdict",
]


@dataclass(frozen=True, slots=True)
class TemplateConfig:
    """Knobs, with the reasoning for each default in the field comment."""

    #: Below this elite ownership an idea is one manager's punt, not a signal.
    #: 0.15 keeps ideas that four of a cohort of twenty-five hold.
    min_elite_ownership: float = 0.15
    #: Ideas whose elite-minus-field gap is smaller than this are noise given
    #: the binomial error on a cohort of a few dozen.
    min_gap: float = 0.05
    #: Weight on the captaincy term when ranking. 1.0 means a captaincy gap
    #: counts exactly as much as an ownership gap of the same size, which is
    #: what the EO algebra says it is worth.
    captain_weight: float = 1.0


def elite_ownership(
    picks: pd.DataFrame,
    *,
    gw: int,
    entry_ids: list[int] | None = None,
) -> pd.DataFrame:
    """Ownership, start share, captain share and EO within a cohort, one gameweek.

    ``multiplier`` drives everything: 0 is a benched player who contributes
    nothing to that manager's score, 1 a starter, 2 a captain, 3 a triple
    captain. Ownership counts the squad; EO counts the multiplier. Both are
    reported because the gap between them is itself informative -- a player with
    high ownership and low EO is being benched by the people who own him.
    """
    week = picks[picks["gw"] == gw]
    if entry_ids is not None:
        week = week[week["entry_id"].isin(entry_ids)]
    if week.empty:
        return pd.DataFrame(columns=[
            "element_id", "owners", "n_cohort", "ownership",
            "start_share", "captain_share", "triple_captain_share", "eo",
        ])

    n = week["entry_id"].nunique()
    grp = week.groupby("element_id")
    owners = grp["entry_id"].nunique()
    mult = week["multiplier"].fillna(0)
    starts = week.assign(_s=(mult >= 1).astype(int)).groupby("element_id")["_s"].sum()
    caps = week.assign(_c=(mult >= 2).astype(int)).groupby("element_id")["_c"].sum()
    tcs = week.assign(_t=(mult >= 3).astype(int)).groupby("element_id")["_t"].sum()

    out = pd.DataFrame({
        "element_id": owners.index,
        "owners": owners.to_numpy(),
        "n_cohort": n,
        "ownership": owners.to_numpy() / n,
        "start_share": starts.reindex(owners.index).fillna(0).to_numpy() / n,
        "captain_share": caps.reindex(owners.index).fillna(0).to_numpy() / n,
        "triple_captain_share": tcs.reindex(owners.index).fillna(0).to_numpy() / n,
    })
    # EO = start + captain + triple-captain. The last term is added ONCE more,
    # because a triple captain's 3 decomposes as started(1) + captained(1) + 1.
    out["eo"] = out["start_share"] + out["captain_share"] + out["triple_captain_share"]
    return out.reset_index(drop=True)


def _binomial_se(share: np.ndarray, n: int) -> np.ndarray:
    """Standard error of an ownership share estimated from ``n`` managers.

    Included on every idea because a cohort is usually a few dozen people, and
    at n=30 an observed 40% ownership has a standard error of 9 points. Reading
    a 12-point elite-versus-field gap as decisive at that sample size is the
    single easiest way to turn this analysis into astrology.
    """
    return np.sqrt(np.clip(share * (1 - share), 0, None) / max(n, 1))


def rank_ideas(
    elite: pd.DataFrame,
    players: pd.DataFrame,
    *,
    field_eo: pd.Series | None = None,
    config: TemplateConfig | None = None,
) -> pd.DataFrame:
    """Rank what the cohort owns and the field does not.

    ``players`` needs ``element_id``, ``web_name``, ``position``,
    ``price_tenths`` and ``selected_by_pct`` **as of the deadline in question**.
    ``field_eo`` is optional: FPL publishes field ownership but not field
    effective ownership, so where a modelled field EO exists it is used, and
    where it does not the field's ownership is used as the EO floor with the
    limitation carried into ``verdict``.
    """
    cfg = config or TemplateConfig()
    if elite.empty or players.empty:
        return pd.DataFrame(columns=IDEA_COLUMNS)

    df = elite.merge(
        players[["element_id", "web_name", "position", "price_tenths", "selected_by_pct"]],
        on="element_id", how="left",
    )
    df["field_ownership"] = df["selected_by_pct"].astype(float) / 100.0
    if field_eo is not None:
        df["field_eo"] = df["element_id"].map(field_eo).fillna(df["field_ownership"])
    else:
        # Without a modelled field EO the honest fallback is field ownership,
        # which UNDERSTATES the field's exposure to the popular captain and so
        # OVERSTATES the edge in captaining him. Flagged in `verdict` rather
        # than silently absorbed.
        df["field_eo"] = df["field_ownership"]

    n = int(df["n_cohort"].iloc[0])
    df["ownership_gap"] = df["ownership"] - df["field_ownership"]
    df["eo_gap"] = df["eo"] - df["field_eo"]
    df["owners_se"] = _binomial_se(df["ownership"].to_numpy(float), n)
    df["concentration"] = df["ownership"]

    # Priority multiplies consensus by edge. Both matter and neither substitutes
    # for the other: a unanimous pick with no gap changes nothing, and a huge
    # gap held by two people is one person's opinion twice.
    df["priority"] = df["concentration"] * np.maximum(
        df["ownership_gap"], cfg.captain_weight * df["eo_gap"]
    )

    df["verdict"] = [
        _verdict(row, cfg, field_eo is not None) for row in df.to_dict("records")
    ]
    df = df.rename(columns={
        "ownership": "elite_ownership", "start_share": "elite_start_share",
        "captain_share": "elite_captain_share", "eo": "elite_eo",
        "owners": "n_elite_owners", "n_cohort": "n_elite",
    })
    keep = df[
        (df["elite_ownership"] >= cfg.min_elite_ownership)
        & (df["ownership_gap"].abs() >= cfg.min_gap)
    ]
    return keep.sort_values("priority", ascending=False)[IDEA_COLUMNS].reset_index(drop=True)


def _verdict(row: dict, cfg: TemplateConfig, have_field_eo: bool) -> str:
    gap = row["ownership_gap"]
    se = row["owners_se"]
    if abs(gap) < 2 * se:
        base = "within 2 SE of the field: not distinguishable at this cohort size"
    elif gap > 0:
        base = "elite over-own relative to the field"
    else:
        base = "elite actively avoid a player the field holds"
    if row["captain_share"] > 0.25 and not have_field_eo:
        base += "; captaincy edge overstated (no modelled field EO)"
    return base


def template_xi(players: pd.DataFrame, *, size: int = 15) -> set[int]:
    """The ``size`` most-selected players -- the thing an idea has to differ from."""
    if players.empty:
        return set()
    return set(
        players.sort_values("selected_by_pct", ascending=False).head(size)["element_id"]
    )


def divergence_summary(elite: pd.DataFrame, players: pd.DataFrame) -> dict[str, float]:
    """How different the cohort is from the template, in one line of numbers.

    Useful as a sanity check before reading any individual idea: if the cohort's
    mean template overlap is 14 of 15, then "what the elite own that the field
    does not" is a question about one player and the ranked list below it is
    mostly noise.
    """
    if elite.empty or players.empty:
        return {}
    template = template_xi(players)
    owned = set(elite[elite["ownership"] >= 0.5]["element_id"])
    return {
        "n_elite": float(elite["n_cohort"].iloc[0]),
        "consensus_picks": float(len(owned)),
        "consensus_in_template": float(len(owned & template)),
        "consensus_off_template": float(len(owned - template)),
        "mean_elite_ownership_of_template": float(
            elite[elite["element_id"].isin(template)]["ownership"].mean()
        ) if template else float("nan"),
    }
