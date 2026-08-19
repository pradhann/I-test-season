"""Copying a cohort directly: own what the skilled own, weighted by consensus.

This is the strategy the whole mining exercise implies, stated plainly enough to
be falsified. It values a player by how much of the skilled cohort's effective
ownership he carries, and builds the squad from that valuation. If copying the
elite is an edge, this beats the template baseline. If it does not, the mining
was interesting and useless, and the backtester will say so.

Two design choices carry the argument.

**Effective ownership, not headcount.** The value of copying a cohort is the
mean multiplier they apply, which counts captaincy. A cohort that owns a striker
at 60% and captains him at 40% is expressing far more conviction than one that
owns him at 60% and captains him never, and a headcount cannot tell them apart.

**Lagged by construction.** The cohort's picks for gameweek *g* become public at
gameweek *g*'s deadline -- the same instant this strategy must decide. So a
strategy that copies "the cohort's current squad" is copying a squad it cannot
see. :class:`CopyCohort` therefore reads the cohort's picks from the *previous*
gameweek, which is genuinely observable, and that lag is not a limitation to be
engineered away: it is the real handicap of copying, and pretending otherwise
produces a backtest that cannot be reproduced in practice.

The lag has a consequence worth stating: copying is structurally a week behind
the bandwagon. Whether the cohort's edge survives being copied a week late is
precisely the question, and it is why this strategy exists as a testable arm
rather than as a recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from fpl_edge.eval.baselines import greedy_squad, order_picks
from fpl_edge.eval.replay import Decision, SquadState
from fpl_edge.eval.scoring import Chip
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season

from strategies.base import PolicySpec


@dataclass
class CohortPicks:
    """The cohort's public squads, keyed by gameweek.

    Supplied rather than fetched so that the strategy stays offline and
    deterministic in a backtest. ``frame`` needs ``gw``, ``entry_id``,
    ``element_id`` and ``multiplier``, and it must contain only squads whose
    deadline has passed -- the caller owns that guarantee, and
    :class:`CopyCohort` enforces the lag on top of it as a second line of defence.
    """

    frame: pd.DataFrame
    code_of_element: dict[int, int] = field(default_factory=dict)

    def eo_at(self, gw: int) -> pd.Series:
        week = self.frame[self.frame["gw"] == gw]
        if week.empty:
            return pd.Series(dtype=float)
        n = week["entry_id"].nunique()
        mult = week["multiplier"].fillna(0)
        starts = week.assign(_s=(mult >= 1).astype(int)).groupby("element_id")["_s"].sum()
        caps = week.assign(_c=(mult >= 2).astype(int)).groupby("element_id")["_c"].sum()
        tcs = week.assign(_t=(mult >= 3).astype(int)).groupby("element_id")["_t"].sum()
        eo = (starts.add(caps, fill_value=0).add(tcs, fill_value=0)) / n
        if self.code_of_element:
            eo.index = [self.code_of_element.get(int(e), int(e)) for e in eo.index]
        return eo


@dataclass
class CopyCohort:
    """Value each player by the cohort's lagged effective ownership of him.

    ``lag`` defaults to 1 and should not be lowered below it without a specific
    reason: lag 0 means reading a squad that locks at the same deadline this
    decision is made at, which is not information anybody had.
    """

    cohort: CohortPicks
    lag: int = 1
    #: Blend weight toward the field template. 1.0 copies the cohort outright;
    #: 0.0 is the template baseline. The middle of the range is the interesting
    #: part, because copying outright inherits the cohort's variance as well as
    #: its edge.
    weight: float = 1.0
    label: str = "copy_cohort"

    @property
    def name(self) -> str:
        return f"{self.label}(lag={self.lag},w={self.weight:g})"

    def decide(self, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        players = snapshot.players(season)
        players = players[players["status"].isin(["a", "d"])]

        eo = self.cohort.eo_at(int(gw) - self.lag)
        field_own = players["selected_by_pct"].astype(float) / 100.0
        cohort_val = players["code"].map(eo).fillna(0.0).astype(float)
        value = self.weight * cohort_val + (1.0 - self.weight) * field_own

        if value.abs().sum() == 0:
            # No cohort data yet -- GW1, or the crawl has not run. Fall back to
            # the field template rather than to an arbitrary squad, and be
            # explicit about it: a silent fallback to "whatever greedy picks
            # from a zero vector" would produce a nonsense opening squad and
            # make the whole season's backtest meaningless.
            value = field_own

        if state is None:
            codes = greedy_squad(players, value)
            return Decision(picks=order_picks(players, codes, value), chip=Chip.NONE)
        held = [p.code for p in state.picks]
        return Decision(picks=order_picks(players, held, value), chip=Chip.NONE)


SPECS: tuple[PolicySpec, ...] = (
    PolicySpec(
        name="copy_cohort",
        build=lambda cohort, lag=1, weight=1.0: CopyCohort(
            cohort=cohort, lag=lag, weight=weight),
        grid={"lag": [1, 2], "weight": [0.25, 0.5, 0.75, 1.0]},
        hypothesis="Owning what a demonstrably skilled cohort owns, one gameweek "
                   "late, beats owning what the field owns.",
        evidence="UNTESTED. Requires cohort picks, i.e. at least one completed "
                 "gameweek. The weight grid exists because copying outright "
                 "inherits the cohort's variance as well as its edge, and the "
                 "rank-utility objective may prefer a partial copy.",
    ),
)
