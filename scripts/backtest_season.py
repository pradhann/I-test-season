"""Walk a full season: strategies decide at each deadline, reality scores them.

For every gameweek deadline the strategy receives ONLY a snapshot taken at that
deadline (prices, ownership, availability, and results of finished gameweeks).
Realised outcomes are then read from the archive to score the decision --
legitimate, because the decision was already frozen.

Usage:
    uv run python scripts/backtest_season.py 2025-26
    uv run python scripts/backtest_season.py 2023-24 2024-25 2025-26
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd

from fpl_edge.eval.baselines import (
    LastWeeksBestStrategy,
    ScorerStrategy,
    TemplateStrategy,
)
from fpl_edge.eval.replay import GwResult, ReplayResult, apply_decision, revert_free_hit
from fpl_edge.eval.scoring import Chip, Outcome, score_gameweek
from fpl_edge.store import Snapshot, Warehouse
from fpl_edge.types import GwId

OUT = Path("data/warehouse/backtests")


def form_scorer(snapshot: Snapshot, players: pd.DataFrame, season, gw) -> pd.Series:
    """Naive xPts proxy: trailing mean points over the last four finished GWs.

    This is the 'competent spreadsheet manager' baseline -- better than raw
    recency (one week) but still nothing a bookmaker would call a model.
    """
    results = snapshot.results_before(str(season))
    if results.empty:
        return players["selected_by_pct"].astype(float)  # GW1: no form exists
    last = int(results["gw"].max())
    recent = results[results["gw"] > last - 4]
    ppg = recent.groupby("code")["total_points"].mean()
    return players["code"].map(ppg).fillna(0.0).astype(float)


def outcomes_for_gw(wh: Warehouse, season: str, gw: int) -> dict[int, Outcome]:
    """Realised (minutes, points) per player for one gameweek, summed over
    double fixtures. Read from the latest state of the archive: this is the
    measurement, not the decision input."""
    df = wh.sql(
        """
        SELECT code, sum(minutes) AS minutes, sum(total_points) AS points
        FROM (
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code, fixture_id
                                             ORDER BY as_of DESC) rn
                FROM fact_player_fixture WHERE season = ? AND gw = ?
            ) WHERE rn = 1
        ) GROUP BY code
        """,
        [season, gw],
    )
    return {
        int(r.code): Outcome(int(r.code), int(r.minutes or 0), int(r.points or 0))
        for r in df.itertuples()
    }


def deadlines_for(wh: Warehouse, season: str) -> list[tuple[int, dt.datetime]]:
    df = wh.sql(
        "SELECT gw, max(deadline_utc) AS d FROM dim_event WHERE season = ? "
        "GROUP BY gw ORDER BY gw",
        [season],
    )
    if df.empty:
        # Historical seasons carry no dim_event rows; reconstruct from kickoffs
        # exactly as the rules define it: 90 minutes before the first match.
        df = wh.sql(
            "SELECT gw, min(kickoff_utc) - INTERVAL 90 MINUTE AS d "
            "FROM fact_fixture WHERE season = ? AND gw IS NOT NULL "
            "GROUP BY gw ORDER BY gw",
            [season],
        )
    return [(int(r.gw), r.d.to_pydatetime()) for r in df.itertuples()]


def replay(wh: Warehouse, season: str, strategy: ScorerStrategy) -> ReplayResult:
    result = ReplayResult(season=season, strategy=strategy.name)
    state = None
    for gw, deadline in deadlines_for(wh, season):
        snap = wh.snapshot_at(deadline)
        players = snap.players(season)
        if players.empty:
            continue
        price = dict(zip(players["code"].astype(int), players["price_tenths"].astype(int)))
        team_of = dict(zip(players["code"].astype(int), players["team_code"].astype(int)))

        try:
            decision = strategy.decide(snap, state, season, GwId(gw))
            state, hits, outs, ins = apply_decision(
                state, decision, price, team_of, GwId(gw)
            )
        except Exception as exc:  # noqa: BLE001 - a failed week scores zero, loudly
            print(f"  {strategy.name} GW{gw}: decision failed ({exc}); holding")
            if state is None:
                raise
            hits, outs, ins = 0, (), ()
            decision = None

        outcomes = outcomes_for_gw(wh, season, gw)
        picks = list(state.picks)
        score = score_gameweek(picks, outcomes, chip=Chip.NONE, transfer_cost=hits)
        result.gws.append(GwResult(
            gw=GwId(gw), score=score, transfers_in=ins, transfers_out=outs,
            hits=hits, chip=Chip.NONE,
            free_transfers_before=state.free_transfers,
            bank_after=state.bank_tenths,
            squad_value_after=state.squad_value(price),
        ))
        state = revert_free_hit(state)
    return result


def main(seasons: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    strategies = [
        TemplateStrategy(),
        LastWeeksBestStrategy(),
        ScorerStrategy(name="form_4gw", scorer=form_scorer),
    ]
    with Warehouse.read_copy() as wh:
        for season in seasons:
            print(f"== {season} ==")
            rows = {}
            for strat in strategies:
                r = replay(wh, season, strat)
                rows[strat.name] = {
                    "total_net": r.total_points,
                    "gross": r.gross_points,
                    "hits": r.total_hits,
                    "cumulative": r.cumulative(),
                    "per_gw": [g.score.net for g in r.gws],
                }
                print(f"  {strat.name:<18} net {r.total_points:>5}  "
                      f"gross {r.gross_points:>5}  hits {r.total_hits}")
            (OUT / f"{season}.json").write_text(json.dumps(rows, indent=1))
            print(f"  saved {OUT / f'{season}.json'}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["2025-26"])
