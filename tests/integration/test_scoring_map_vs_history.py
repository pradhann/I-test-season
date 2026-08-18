"""Validate the scoring map against real historical stat lines.

This is the strongest single check available on the points pipeline: given what
a player actually did, our mapping must reproduce the ``total_points`` the game
actually awarded, exactly, for every archived row.

Skips cleanly until the historical archive has been loaded into the warehouse.
Any mismatch is reported with the offending rows rather than as a bare count,
because the *pattern* of mismatches identifies which rule is wrong.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.points.scoring_map import points_from_events
from fpl_edge.store import DEFAULT_DB, Warehouse
from fpl_edge.types import Position

pytestmark = pytest.mark.skipif(
    not DEFAULT_DB.exists(), reason="warehouse not built; run `make ingest` first"
)

REQUIRED = [
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "total_points",
]


def _history() -> pd.DataFrame:
    with Warehouse(read_only=True) as wh:
        return wh.sql(
            """
            SELECT f.*, p.position
            FROM fact_player_fixture f
            JOIN (SELECT DISTINCT season, code, position FROM dim_player) p
              USING (season, code)
            WHERE f.minutes IS NOT NULL
            """
        )


def test_scoring_map_reproduces_historical_total_points() -> None:
    df = _history()
    if df.empty:
        pytest.skip("no historical player-fixture rows loaded yet")

    mismatches = []
    checked = 0
    for pos_id, grp in df.groupby("position"):
        pos = Position(int(pos_id))
        kw = {c: grp[c].fillna(0).to_numpy(dtype=np.int64) for c in REQUIRED if c != "total_points"}
        dc = grp.get("defensive_contribution")
        if dc is not None:
            kw["defensive_contribution"] = dc.fillna(0).to_numpy(dtype=np.int64)
        got = points_from_events(pos, **kw)
        want = grp["total_points"].to_numpy(dtype=np.int64)
        checked += len(want)
        bad = grp.loc[got != want].assign(expected=want[got != want], got=got[got != want])
        if not bad.empty:
            mismatches.append(bad)

    if mismatches:
        bad = pd.concat(mismatches)
        sample = bad[["season", "code", "gw", "position", "expected", "got"]].head(15)
        rate = len(bad) / checked
        pytest.fail(
            f"{len(bad)}/{checked} rows ({rate:.3%}) mismatch. "
            f"A nonzero rate usually means a scoring rule changed between "
            f"seasons and the registry only encodes the current one.\n{sample}"
        )
