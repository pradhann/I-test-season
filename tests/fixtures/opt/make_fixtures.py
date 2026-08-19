"""Regenerate the optimiser's committed test fixtures.

Run from the repo root with a populated warehouse::

    uv run python tests/fixtures/opt/make_fixtures.py

The optimiser must be testable without the warehouse, the network, or any
other team's model, so the fixtures are committed. This script records where
they came from.

* ``universe_2026_27.csv`` is REAL: the 592 players, positions, clubs, prices
  and availability from the 2026/27 bootstrap-static ingest.
* ``xpts_2026_27.csv`` is SYNTHETIC and clearly not a points model. It is a
  deterministic function of price, position, home/away and the opponent's mean
  squad price, so the numbers vary plausibly across gameweeks and the MILP has
  something non-degenerate to chew on. Nothing in the optimiser's tests asserts
  that these numbers are good, only that the optimiser respects them.
* ``prices_2026_27.csv`` is SYNTHETIC: a small deterministic drift driven by
  the real pre-season ownership figures, standing in for the price team's
  forecast.
  Its job is to exercise the sell-on fee under both rises and falls.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from fpl_edge.store import Warehouse

SEASON = "2026-27"
AS_OF = dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.UTC)
GWS = [1, 2, 3, 4, 5]
HERE = Path(__file__).parent


def _jitter(code: int, gw: int) -> float:
    """Deterministic per-(player, gameweek) noise in [-0.5, 0.5]."""
    digest = hashlib.sha256(f"{code}:{gw}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 - 0.5


def main() -> None:
    wh = Warehouse("data/warehouse/fpl.duckdb", read_only=True)
    snap = wh.snapshot_at(AS_OF)
    players = snap.players(SEASON).sort_values("code").reset_index(drop=True)
    fixtures = snap.table("fact_fixture", where="season = ?", params=[SEASON])
    fixtures = fixtures[fixtures["gw"].isin(GWS)]

    strength = players.groupby("team_code")["price_tenths"].mean()
    strength = (strength - strength.mean()) / strength.std()

    universe = players[
        ["code", "web_name", "position", "team_code", "price_tenths", "status",
         "selected_by_pct", "transfers_in_event", "transfers_out_event"]
    ].copy()
    universe.to_csv(HERE / "universe_2026_27.csv", index=False)

    opponent: dict[tuple[int, int], tuple[int, bool]] = {}
    for row in fixtures.itertuples():
        opponent[(int(row.home_team_code), int(row.gw))] = (int(row.away_team_code), True)
        opponent[(int(row.away_team_code), int(row.gw))] = (int(row.home_team_code), False)

    rows = []
    for r in universe.itertuples():
        # Price is the market's own view of a player, so it is the least silly
        # cheap proxy for expected points available without a points model.
        base = {1: 0.055, 2: 0.062, 3: 0.070, 4: 0.075}[int(r.position)] * float(r.price_tenths)
        for gw in GWS:
            opp, home = opponent.get((int(r.team_code), gw), (None, True))
            edge = 0.0 if opp is None else -0.45 * float(strength.get(opp, 0.0))
            xp = base + edge + (0.25 if home else -0.25) + 0.9 * _jitter(int(r.code), gw)
            if str(r.status) not in ("a", "d"):
                xp = 0.0
            p_play = {"a": 0.90, "d": 0.55, "i": 0.05, "s": 0.0, "u": 0.02, "n": 0.02}.get(
                str(r.status), 0.5
            )
            rows.append(
                {
                    "code": int(r.code),
                    "gw": gw,
                    "xpts": round(max(0.0, xp), 4),
                    "p_play": p_play,
                }
            )
    pd.DataFrame(rows).to_csv(HERE / "xpts_2026_27.csv", index=False)

    price_rows = []
    for r in universe.itertuples():
        # Pre-season the API's net-transfer counters are all zero, so ownership
        # stands in for them: heavily-owned players rise, ignored ones fall.
        owned = float(r.selected_by_pct or 0.0)
        direction = 1 if owned >= 5.0 else (-1 if owned < 0.6 else 0)
        for k, gw in enumerate(GWS):
            # One tenth every other gameweek, in the direction of net transfers.
            drift = direction * (k // 2)
            price_rows.append(
                {
                    "code": int(r.code),
                    "gw": gw,
                    "price_tenths": max(35, int(r.price_tenths) + drift),
                }
            )
    pd.DataFrame(price_rows).to_csv(HERE / "prices_2026_27.csv", index=False)
    print(f"wrote {len(universe)} players, {len(rows)} xpts rows, {len(price_rows)} price rows")


if __name__ == "__main__":
    main()
