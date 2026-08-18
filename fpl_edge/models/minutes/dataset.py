"""Loading a committed CSV fixture warehouse into a real DuckDB warehouse.

The evaluation harness needs a Warehouse to mint Snapshots from. Reading the
committed CSVs straight into pandas and handing those to a model would defeat
the entire point-in-time design, so the fixtures are loaded into an actual
warehouse and every read then goes through ``snapshot_at``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fpl_edge.store import Warehouse

#: Where the committed synthetic fixture warehouse lives.
FIXTURE_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "minutes"

_TABLES = (
    "dim_event",
    "dim_team",
    "dim_player",
    "fact_fixture",
    "fact_player_state",
    "fact_player_fixture",
)
_TS_COLUMNS = ("as_of", "kickoff_utc", "deadline_utc", "news_added")


def load_csv_warehouse(src: Path | str, db_path: Path | str) -> Warehouse:
    """Build a warehouse from ``<src>/<table>.csv.gz`` files."""
    src = Path(src)
    wh = Warehouse(db_path)
    for table in _TABLES:
        path = src / f"{table}.csv.gz"
        if not path.exists():
            continue
        df = pd.read_csv(path)
        for col in _TS_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
        wh.append(table, df)
    return wh
