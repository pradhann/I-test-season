"""Creator track record: the only thing that lets an opinion into the model.

The argument
------------

An unweighted consensus of content creators is the template with extra steps.
That is not a rhetorical flourish, it is arithmetic. Creators watch each other,
share a fixture ticker, and read the same ownership numbers; the modal
recommendation across the FPL content ecosystem *is* the modal FPL squad. Adding
"12 of 15 creators are buying Semenyo" to a model that already knows Semenyo's
ownership adds a correlated copy of a feature it has, with a plausible-looking
new name. It will improve backtest fit and it will not improve rank, because
everyone else already has him.

The only content signal that can be worth anything is the part that is *not*
the consensus: a creator who is right more often than the field, on the claims
where they diverge. Which means the question is never "what are creators
saying"; it is "which creators have earned the right to be listened to, measured
on their own past claims, and by how much".

So: a creator's opinion enters the model multiplied by :func:`earned_weight`,
and that function returns 0.0 until the creator has demonstrated an edge over
the coin flip at their observed sample size. No prior, no benefit of the doubt,
no participation credit. A creator with 8 claims and 6 hits gets zero weight,
because 6/8 is what a coin flip does roughly one time in seven.

How a claim is scored
---------------------

A claim names a player, an action and a gameweek. Once that gameweek finalises:

* The player's realised points for the gameweek are summed across fixtures, so
  a double gameweek counts once and correctly.
* The benchmark is the median points among *starting* players in the same
  position that gameweek. Starters, not all players: the alternative to buying
  a midfielder is another midfielder who plays, not the median of a pool
  half-full of unused substitutes, which would be about 1 point and would make
  every recommendation look like genius.
* Positive actions (buy, hold, captain, triple captain) hit when the player
  beat the benchmark. Negative actions (sell, bench, avoid) hit when the player
  failed to beat it. Scoring both with the same comparison would credit a
  creator for correctly saying "avoid" about a player who then hauled.
* Exact ties count as misses on both sides. Symmetric, and it declines to award
  credit for a recommendation that made no difference.

The leakage check that matters
------------------------------

Before any of that, the claim's ``published_at`` is compared against its own
gameweek's deadline. A claim published after the deadline is marked
``unscoreable='published_after_deadline'`` and contributes to neither the
numerator nor the denominator of the hit rate.

This is the check that stops the entire exercise being a fraud. Podcast
archives are full of episodes titled "GW12 review" published after GW12, and
they are absolutely stuffed with the words "captain" and "Haaland". Score those
and every creator has a magnificent hit rate, and the weights that come out are
pure hindsight laundered into a feature. The count of claims rejected by this
rule is reported alongside the hit rates, because a run where it rejects nothing
is a run where something is broken.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import pandas as pd

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.ingest.content.models import Action

UTC = dt.UTC

#: Below this, no weight is granted regardless of hit rate. Guards against the
#: creator who made four claims, got three right, and would otherwise outrank
#: someone measured over two hundred.
MIN_SCORED_CLAIMS = 25


def wilson_lower_bound(hits: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the 95% Wilson interval on a hit rate.

    The point estimate is the wrong number to weight on. 3/4 is a better point
    estimate than 130/200 and a far worse reason to act. The Wilson lower bound
    collapses toward 0 as n shrinks, which is exactly the behaviour wanted: it
    encodes "we do not know yet" as "no weight" without a separate rule.
    """
    if n <= 0:
        return 0.0
    phat = hits / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2 * n)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def earned_weight(hits: int, n: int) -> float:
    """Weight in [0, 1]. Zero unless an edge over the coin flip is demonstrated.

    ``2 * (wilson_lo - 0.5)`` maps a lower bound of 0.5 to 0 and 1.0 to 1. A
    creator whose 95% lower bound sits at 0.60 -- which at these sample sizes is
    a genuinely good creator -- earns 0.20. That is deliberately modest. The
    claim being made is "this creator knows something", not "this creator should
    outvote the model".
    """
    if n < MIN_SCORED_CLAIMS:
        return 0.0
    return round(min(1.0, max(0.0, 2.0 * (wilson_lower_bound(hits, n) - 0.5))), 4)


@dataclass
class ScoringStats:
    considered: int = 0
    scored: int = 0
    hits: int = 0
    rejected_late: int = 0
    unresolved_gw: int = 0
    no_result_yet: int = 0
    player_absent_from_season: int = 0
    no_benchmark: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def note(self, reason: str) -> None:
        self.reasons[reason] = self.reasons.get(reason, 0) + 1

    def render(self) -> str:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(self.reasons.items())) or "(none)"
        rate = f"{self.hits / self.scored:.1%}" if self.scored else "n/a"
        return (
            f"considered={self.considered} scored={self.scored} hits={self.hits} "
            f"({rate})\n  rejected: {detail}"
        )


class ResultIndex:
    """Realised gameweek points and positional benchmarks, built once."""

    def __init__(self, results: pd.DataFrame, players: pd.DataFrame) -> None:
        """``results``: fact_player_fixture. ``players``: dim_player (any season)."""
        if results.empty:
            self._points = pd.DataFrame(columns=["season", "gw", "code", "points"])
            self._bench: dict[tuple[str, int, int], float] = {}
            self._seasons: set[str] = set()
            self._pos: dict[tuple[str, int], int] = {}
            return

        agg = (
            results.groupby(["season", "gw", "code"], as_index=False)
            .agg(points=("total_points", "sum"),
                 starts=("starts", "sum"),
                 minutes=("minutes", "sum"))
        )
        self._points = agg
        self._lookup = {
            (str(r.season), int(r.gw), int(r.code)): float(r.points)
            for r in agg.itertuples(index=False)
        }
        self._seasons = set(agg["season"].astype(str))

        pos = players[["season", "code", "position"]].drop_duplicates()
        self._pos = {
            (str(r.season), int(r.code)): int(r.position)
            for r in pos.itertuples(index=False)
        }
        merged = agg.merge(pos, on=["season", "code"], how="inner")
        # Starters only. `starts` is present from 2022-23 onward in this
        # warehouse; where it is null, fall back to a 60-minute appearance,
        # which is the same population by a different route.
        started = merged[
            (merged["starts"].fillna(0) > 0) | (merged["minutes"].fillna(0) >= 60)
        ]
        bench = (
            started.groupby(["season", "gw", "position"])["points"]
            .median()
            .reset_index()
        )
        self._bench = {
            (str(r.season), int(r.gw), int(r.position)): float(r.points)
            for r in bench.itertuples(index=False)
        }
        self._played_gws = set(
            (str(s), int(g)) for s, g in agg[["season", "gw"]].drop_duplicates().itertuples(index=False)
        )
        self._season_players = {
            (str(r.season), int(r.code)) for r in pos.itertuples(index=False)
        }

    def gw_finalised(self, season: str, gw: int) -> bool:
        return (season, gw) in getattr(self, "_played_gws", set())

    def knows_player(self, season: str, code: int) -> bool:
        return (season, code) in getattr(self, "_season_players", set())

    def points(self, season: str, gw: int, code: int) -> float:
        """Points for the gameweek. A player with no row scored nothing.

        Absence is a real outcome, not missing data: a recommended player who
        did not make the squad returned zero to the manager who bought him.
        """
        return self._lookup.get((season, gw, code), 0.0)

    def benchmark(self, season: str, gw: int, code: int) -> tuple[float | None, str]:
        position = self._pos.get((season, code))
        if position is None:
            return None, "no_position"
        value = self._bench.get((season, gw, position))
        if value is None:
            return None, "no_positional_median"
        return value, f"pos{position}_starter_median"


def score_claims(
    claims: pd.DataFrame,
    index: ResultIndex,
    calendar: GameweekCalendar,
    *,
    now: dt.datetime,
) -> tuple[pd.DataFrame, ScoringStats]:
    """Resolve every claim whose gameweek has finalised. Returns claim_outcome rows."""
    stats = ScoringStats()
    rows: list[dict[str, object]] = []
    if claims.empty:
        return pd.DataFrame(columns=[
            "claim_id", "creator", "season", "gameweek", "player_code", "action",
            "player_points", "benchmark", "benchmark_points", "hit", "unscoreable",
            "resolved_utc",
        ]), stats

    deadlines = {(s, g): d for s, g, d in calendar._rows}

    for claim in claims.itertuples(index=False):
        stats.considered += 1
        season, gw, code = str(claim.season), int(claim.gameweek), int(claim.player_code)
        action = Action(str(claim.action))
        published = pd.Timestamp(claim.published_at)
        if published.tzinfo is None:
            published = published.tz_localize(UTC)
        published_dt = published.to_pydatetime().astimezone(UTC)

        base = {
            "claim_id": claim.claim_id, "creator": claim.creator, "season": season,
            "gameweek": gw, "player_code": code, "action": str(action),
            "player_points": None, "benchmark": "", "benchmark_points": None,
            "hit": None, "unscoreable": None, "resolved_utc": now,
        }

        deadline = deadlines.get((season, gw))
        if deadline is None:
            stats.unresolved_gw += 1
            stats.note("no_deadline_known")
            rows.append({**base, "unscoreable": "no_deadline_known"})
            continue

        # THE check. A claim published after its own deadline is hindsight.
        if published_dt >= deadline:
            stats.rejected_late += 1
            stats.note("published_after_deadline")
            rows.append({**base, "unscoreable": "published_after_deadline"})
            continue

        if not index.gw_finalised(season, gw):
            stats.no_result_yet += 1
            stats.note("gameweek_not_played")
            rows.append({**base, "unscoreable": "gameweek_not_played"})
            continue

        if not index.knows_player(season, code):
            stats.player_absent_from_season += 1
            stats.note("player_not_in_season")
            rows.append({**base, "unscoreable": "player_not_in_season"})
            continue

        benchmark, label = index.benchmark(season, gw, code)
        if benchmark is None:
            stats.no_benchmark += 1
            stats.note(label)
            rows.append({**base, "unscoreable": label})
            continue

        points = index.points(season, gw, code)
        hit = points > benchmark if action.is_positive else points < benchmark
        stats.scored += 1
        stats.hits += int(hit)
        rows.append({
            **base, "player_points": points, "benchmark": label,
            "benchmark_points": benchmark, "hit": bool(hit),
        })

    return pd.DataFrame(rows), stats


def creator_scores(
    outcomes: pd.DataFrame, claims: pd.DataFrame, *, as_of: dt.datetime
) -> pd.DataFrame:
    """Aggregate outcomes into per-creator weights, overall and per action."""
    columns = [
        "creator", "scope", "as_of", "claims_total", "claims_scored", "hits",
        "hit_rate", "wilson_lo95", "weight", "first_claim_utc", "last_claim_utc",
    ]
    if outcomes.empty:
        # Still emit a row per creator so a creator with zero scoreable claims is
        # visibly weighted at zero rather than silently missing from the table.
        if claims.empty:
            return pd.DataFrame(columns=columns)
        rows = []
        for creator, group in claims.groupby("creator"):
            rows.append({
                "creator": creator, "scope": "all", "as_of": as_of,
                "claims_total": len(group), "claims_scored": 0, "hits": 0,
                "hit_rate": None, "wilson_lo95": 0.0, "weight": 0.0,
                "first_claim_utc": pd.Timestamp(group["published_at"].min()),
                "last_claim_utc": pd.Timestamp(group["published_at"].max()),
            })
        return pd.DataFrame(rows, columns=columns)

    totals = claims.groupby("creator").agg(
        claims_total=("claim_id", "count"),
        first_claim_utc=("published_at", "min"),
        last_claim_utc=("published_at", "max"),
    )
    scored = outcomes[outcomes["hit"].notna()]
    rows: list[dict[str, object]] = []

    def emit(creator: str, scope: str, group: pd.DataFrame, total: int) -> None:
        n = len(group)
        hits = int(group["hit"].sum()) if n else 0
        rows.append({
            "creator": creator, "scope": scope, "as_of": as_of,
            "claims_total": int(total), "claims_scored": int(n), "hits": hits,
            "hit_rate": (hits / n) if n else None,
            "wilson_lo95": round(wilson_lower_bound(hits, n), 4),
            "weight": earned_weight(hits, n),
            "first_claim_utc": totals.loc[creator, "first_claim_utc"]
            if creator in totals.index else None,
            "last_claim_utc": totals.loc[creator, "last_claim_utc"]
            if creator in totals.index else None,
        })

    for creator in sorted(set(claims["creator"]) | set(outcomes["creator"])):
        total = int(totals.loc[creator, "claims_total"]) if creator in totals.index else 0
        mine = scored[scored["creator"] == creator]
        emit(creator, "all", mine, total)
        for action, group in mine.groupby("action"):
            emit(creator, str(action), group, total)

    return pd.DataFrame(rows, columns=columns)


def weight_lookup(scores: pd.DataFrame) -> dict[str, float]:
    """creator -> earned weight, from the 'all' scope. Missing creators get 0.0."""
    if scores.empty:
        return {}
    overall = scores[scores["scope"] == "all"]
    return {str(r.creator): float(r.weight) for r in overall.itertuples(index=False)}
