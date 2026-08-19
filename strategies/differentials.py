"""Differential policies: how far from the template to sit, and when.

The claim these policies test is the oldest one in FPL: that you cannot win by
owning what everybody owns. It is trivially true at the extreme -- a squad
identical to the template finishes at the template's rank by construction -- and
much less obvious anywhere useful, because the template is popular for the good
reason that it contains the best players.

Two things make the question tractable rather than philosophical.

**Rank is decided by differences.** A player owned by 60% of the field
contributes 0.6 of his score to the average manager, so owning him gains you
0.4 of his score against the field and not owning him loses you 0.6 of it. The
arithmetic is symmetric and unforgiving, and it means the *number* of
differentials is a real dial with a real cost, not a matter of temperament.

**Time changes the answer.** With thirty gameweeks left, a differential's payoff
distribution has time to be realised and variance is comparatively cheap. With
five left and a target rank in reach, variance is what loses it. So the policies
here are parameterised on the gameweek from which they apply, and a sweep over
that parameter is the only way to find out whether the run-in is genuinely
different or whether that belief is an artefact of remembering the seasons where
a late punt came off.

Point-in-time safety: ownership is read from the snapshot at the deadline, so a
differential is chosen on the ownership that was public then, not on the
ownership it had after the bandwagon arrived.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from fpl_edge.eval.replay import Decision, SquadState
from fpl_edge.eval.scoring import Pick
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Position, Season

from strategies.base import PolicySpec


@dataclass
class DifferentialFloor:
    """Hold at least ``n`` players owned by ``max_ownership`` percent or less.

    Applied only from ``from_gw`` onward, which is the parameter that encodes
    "go differential in the run-in" as something testable rather than as advice.

    When the squad is already at or above the floor the decision passes through
    untouched, so the policy is a genuine no-op wherever it does not bind and
    the backtest difference is attributable to the weeks where it did.
    """

    n: int = 3
    max_ownership: float = 5.0
    from_gw: int = 1
    name: str = "differential_floor"

    def __post_init__(self) -> None:
        self.name = f"diff>={self.n}@<={self.max_ownership:g}%from{self.from_gw}"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if int(gw) < self.from_gw or state is None:
            return decision
        players = snapshot.players(season)
        if players.empty:
            return decision
        own = dict(zip(players["code"], players["selected_by_pct"].astype(float)))

        held = [p for p in decision.picks]
        current = sum(1 for p in held if own.get(p.code, 100.0) <= self.max_ownership)
        if current >= self.n:
            return decision

        # Swap the most-owned starters for the best available differentials in
        # the same position and within the same price. Same position and same
        # price because anything else is a transfer the harness will price, and
        # this policy is about ownership, not about spending.
        need = self.n - current
        price = dict(zip(players["code"], players["price_tenths"]))
        pool = players[
            (players["selected_by_pct"].astype(float) <= self.max_ownership)
            & (~players["code"].isin({p.code for p in held}))
            & (players["status"].isin(["a", "d"]))
        ]
        if pool.empty:
            return decision

        by_own = sorted(held, key=lambda p: -own.get(p.code, 0.0))
        picks = {p.code: p for p in held}
        for victim in by_own:
            if need <= 0:
                break
            if own.get(victim.code, 100.0) <= self.max_ownership:
                continue
            budget = price.get(victim.code, 0)
            cands = pool[
                (pool["position"] == int(victim.position))
                & (pool["price_tenths"] <= budget)
                & (~pool["code"].isin(picks))
            ]
            if cands.empty:
                continue
            replacement = int(cands.sort_values("price_tenths", ascending=False).iloc[0]["code"])
            picks.pop(victim.code)
            picks[replacement] = Pick(
                code=replacement, position=victim.position, order=victim.order,
                is_captain=False, is_vice=victim.is_vice,
            )
            need -= 1

        if need == self.n - current:
            return decision
        out = tuple(sorted(picks.values(), key=lambda p: p.order))
        # Captaincy may have been on a swapped-out player. Reassign to the
        # highest-owned remaining starter rather than leaving the squad with no
        # captain, which the harness would reject.
        if not any(p.is_captain for p in out):
            starters = [p for p in out if p.is_starter]
            top = max(starters, key=lambda p: own.get(p.code, 0.0))
            out = tuple(
                Pick(code=p.code, position=p.position, order=p.order,
                     is_captain=p.code == top.code, is_vice=p.is_vice)
                for p in out
            )
        return Decision(picks=out, chip=decision.chip)


@dataclass
class OwnershipCeiling:
    """Cap the squad's mean field ownership. The 'do not be the template' dial.

    A blunter instrument than :class:`DifferentialFloor` and a more honest one:
    it constrains the whole squad's position relative to the field rather than
    counting a handful of tokens. Counting differentials can be satisfied by
    three fourth-choice defenders while the other twelve slots are pure
    template, which is a squad that has taken on the variance of being different
    without any of the upside.
    """

    max_mean_ownership: float = 25.0
    from_gw: int = 1
    name: str = "ownership_ceiling"

    def __post_init__(self) -> None:
        self.name = f"meanown<={self.max_mean_ownership:g}%from{self.from_gw}"

    def adjust(self, decision: Decision, snapshot: Snapshot, state: SquadState | None,
               season: Season, gw: GwId) -> Decision:
        if int(gw) < self.from_gw:
            return decision
        players = snapshot.players(season)
        if players.empty:
            return decision
        own = dict(zip(players["code"], players["selected_by_pct"].astype(float)))
        mean = pd.Series([own.get(p.code, 0.0) for p in decision.picks]).mean()
        if mean <= self.max_mean_ownership:
            return decision
        # Delegate the actual swapping to the floor policy with a target derived
        # from how far over the ceiling we are, rather than duplicating the
        # replacement logic. One implementation of "swap a template player for a
        # differential" is enough.
        excess = mean - self.max_mean_ownership
        need = max(1, int(round(excess / 10.0)))
        return DifferentialFloor(n=need, max_ownership=10.0, from_gw=self.from_gw).adjust(
            decision, snapshot, state, season, gw
        )


SPECS: tuple[PolicySpec, ...] = (
    PolicySpec(
        name="differential_floor",
        build=lambda n=3, max_ownership=5.0, from_gw=1: DifferentialFloor(
            n=n, max_ownership=max_ownership, from_gw=from_gw),
        grid={"n": [0, 1, 2, 3, 4, 6], "max_ownership": [2.0, 5.0, 10.0],
              "from_gw": [1, 20, 29]},
        hypothesis="Carrying N sub-5%-owned players raises rank-utility, more so "
                   "in the final ten gameweeks.",
        evidence="UNTESTED against elite squads: per-gameweek picks are public "
                 "only from GW1 of the season in progress. The from_gw=29 arm "
                 "encodes the run-in version of the claim specifically.",
    ),
    PolicySpec(
        name="ownership_ceiling",
        build=lambda max_mean_ownership=25.0, from_gw=1: OwnershipCeiling(
            max_mean_ownership=max_mean_ownership, from_gw=from_gw),
        grid={"max_mean_ownership": [15.0, 20.0, 25.0, 30.0, 40.0], "from_gw": [1, 20]},
        hypothesis="Squad-level distance from the template matters more than a "
                   "count of token differentials.",
        evidence="UNTESTED. Stated as the rival hypothesis to differential_floor "
                 "so the sweep compares them rather than testing one alone.",
    ),
)
