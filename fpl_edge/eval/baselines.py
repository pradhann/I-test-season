"""Baseline strategies the engine must beat before it can be trusted.

These exist to make "the model is good" falsifiable. Each is deliberately
simple, deliberately plausible, and deliberately the thing a competent manager
might actually do. If the engine cannot beat them on rank-equivalent finish over
walk-forward seasons, the engine is not adding value and must say so.

The list is drawn from the project brief:

* :class:`TemplateStrategy` -- own the most-owned players. This is the one that
  matters most, because converging to it is the specific failure mode the whole
  project is built to avoid, and it is genuinely hard to beat on points.
* :class:`LastWeeksBestStrategy` -- buy whoever scored last week. The naive
  recency-chasing rule; any recommender that merely correlates with this is not
  modelling anything.
* :class:`MarketOnlyStrategy` -- pick from bookmaker-implied probabilities only.
  The market is an efficient aggregator and beating it is the real bar.
* :class:`NaiveXptsStrategy` -- maximise expected points with no regard to
  ownership or variance. Beating this on POINTS is not required; beating it on
  RANK is the entire thesis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from fpl_edge.eval.replay import Decision, SquadState, Strategy
from fpl_edge.eval.scoring import Chip, Pick
from fpl_edge.rules import rules
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Position, Season

#: A scorer maps the candidate player frame to a per-player value column.
Scorer = Callable[[Snapshot, pd.DataFrame, Season, GwId], pd.Series]


class InfeasibleSquad(RuntimeError):
    """Raised when greedy selection cannot complete a legal squad."""


def greedy_squad(
    players: pd.DataFrame,
    value: pd.Series,
    *,
    budget_tenths: int | None = None,
) -> list[int]:
    """Pick a legal 15 by value density under budget, position and club limits.

    This is intentionally a greedy heuristic, not an optimiser: baselines should
    be simple and fast. The real optimiser is a MILP and lives in fpl_edge.opt.

    Selection is by value per unit cost rather than raw value, because raw value
    would spend the entire budget on premiums and fail to fill the squad.
    """
    r = rules()
    budget = budget_tenths if budget_tenths is not None else r.get("squad.budget_tenths")
    need = dict(r.get("squad.select_by_position"))
    max_club = r.get("squad.max_per_club")

    df = players.assign(_value=value.reindex(players.index)).dropna(subset=["_value"])
    df = df[df["price_tenths"] > 0]
    df = df.assign(_density=df["_value"] / df["price_tenths"])
    df = df.sort_values("_density", ascending=False)

    chosen: list[int] = []
    spend = 0
    per_club: dict[int, int] = {}
    remaining = dict(need)

    def cheapest_remaining_cost(pool: pd.DataFrame, want: dict[str, int]) -> int:
        """Minimum spend needed to fill the still-unfilled slots."""
        total = 0
        for pos, n in want.items():
            if n <= 0:
                continue
            prices = pool[pool["position"] == Position[pos].value]["price_tenths"]
            if len(prices) < n:
                raise InfeasibleSquad(f"not enough {pos} candidates to fill {n} slots")
            total += int(prices.nsmallest(n).sum())
        return total

    for _, row in df.iterrows():
        pos = Position(int(row["position"])).name
        if remaining[pos] <= 0:
            continue
        club = int(row["team_code"])
        if per_club.get(club, 0) >= max_club:
            continue
        price = int(row["price_tenths"])

        trial = dict(remaining)
        trial[pos] -= 1
        pool = df[~df["code"].isin(chosen + [int(row["code"])])]
        try:
            floor = cheapest_remaining_cost(pool, trial)
        except InfeasibleSquad:
            continue
        if spend + price + floor > budget:
            continue

        chosen.append(int(row["code"]))
        spend += price
        remaining[pos] -= 1
        per_club[club] = per_club.get(club, 0) + 1
        if sum(remaining.values()) == 0:
            break

    if sum(remaining.values()) != 0:
        raise InfeasibleSquad(f"could not fill squad; still need {remaining}")
    return chosen


def order_picks(players: pd.DataFrame, codes: list[int], value: pd.Series) -> tuple[Pick, ...]:
    """Assign starting XI, bench order, captain and vice by value.

    Starts the highest-value legal formation, benches the rest in value order,
    and captains the highest-value starter. The reserve goalkeeper always goes
    to slot 12 because a goalkeeper can only be substituted by a goalkeeper, so
    any other bench slot for them is wasted.
    """
    r = rules()
    mn = r.get("squad.min_play_by_position")
    sub = players[players["code"].isin(codes)].copy()
    sub["_value"] = value.reindex(sub.index)
    sub = sub.sort_values("_value", ascending=False)

    by_pos = {p: sub[sub["position"] == p.value]["code"].tolist() for p in Position}
    starters: list[int] = [by_pos[Position.GKP][0]]
    for pos in (Position.DEF, Position.MID, Position.FWD):
        starters += by_pos[pos][: mn[pos.name]]

    rest = [c for c in sub["code"].tolist()
            if c not in starters and c != by_pos[Position.GKP][1]]
    starters += rest[: r.get("squad.starting_xi") - len(starters)]

    bench = [by_pos[Position.GKP][1]] + [c for c in rest if c not in starters]
    pos_of = dict(zip(sub["code"], sub["position"]))
    ranked = sub[sub["code"].isin(starters)].sort_values("_value", ascending=False)
    captain, vice = ranked["code"].tolist()[:2]

    picks = []
    for i, code in enumerate(starters + bench, start=1):
        picks.append(Pick(code=int(code), position=Position(int(pos_of[code])), order=i,
                          is_captain=code == captain, is_vice=code == vice))
    return tuple(picks)


@dataclass
class ScorerStrategy:
    """A strategy defined entirely by how it values a player."""

    name: str
    scorer: Scorer
    transfers_per_gw: int = 1

    def decide(self, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        players = snapshot.players(season)
        # NULL status means "the archive predates the field", not "unavailable":
        # vaastav's per-GW files carry no status, so filtering it out empties
        # every historical season and the backtest dies at GW1.
        players = players[players["status"].isin(["a", "d"]) | players["status"].isna()]
        value = self.scorer(snapshot, players, season, gw)

        if state is None:
            codes = greedy_squad(players, value)
            return Decision(picks=order_picks(players, codes, value), chip=Chip.NONE)

        # Hold the squad and only re-order/re-captain. Transfer logic for
        # baselines is deliberately minimal; the point of a baseline is to be a
        # floor, not a rival optimiser.
        held = [p.code for p in state.picks]
        return Decision(picks=order_picks(players, held, value), chip=Chip.NONE)


def template_scorer(snapshot: Snapshot, players: pd.DataFrame,
                    season: Season, gw: GwId) -> pd.Series:
    """Own what everyone owns. Ownership is the value."""
    return players["selected_by_pct"].astype(float)


def last_weeks_best_scorer(snapshot: Snapshot, players: pd.DataFrame,
                           season: Season, gw: GwId) -> pd.Series:
    """Buy whoever scored last week.

    Reads only finalised results visible at this snapshot, so it is a legitimate
    strategy rather than a leak -- which is exactly what makes it a fair and
    uncomfortable baseline.
    """
    results = snapshot.results_before(season)
    if results.empty:
        return pd.Series(0.0, index=players.index)
    last_gw = int(results["gw"].max())
    recent = results[results["gw"] == last_gw].groupby("code")["total_points"].sum()
    return players["code"].map(recent).fillna(0.0).astype(float)


def TemplateStrategy() -> ScorerStrategy:
    return ScorerStrategy(name="template", scorer=template_scorer)


def LastWeeksBestStrategy() -> ScorerStrategy:
    return ScorerStrategy(name="last_weeks_best", scorer=last_weeks_best_scorer)
