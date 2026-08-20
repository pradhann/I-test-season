"""Shared helpers for panel scripts.

The rule every helper here exists to serve: **a panel with no data says so.**
Not zero rows silently rendered as an empty table, not a plausible-looking
placeholder row -- an explicit ``{empty: true, reason: "..."}`` that names what
is missing and what would fix it. A dashboard that fabricates is worse than no
dashboard, because it is trusted at the deadline.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.platform.query import guarded_query

UTC = dt.timezone.utc
SEASON_DEFAULT = "2026-27"

#: The projection artefact `make solve` writes. Panels read it rather than
#: re-running a simulation: a panel has a 10s budget and a solve is minutes.
PROJECTION_NAME = "gw1_projection.parquet"

POSITION_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}


def empty(reason: str) -> dict[str, Any]:
    """The one sanctioned way for a script to return nothing."""
    return {"empty": True, "reason": reason}


def season_param() -> dict[str, Any]:
    return {"type": "string", "default": SEASON_DEFAULT, "minLength": 4}


def source_dir(wh) -> Path:
    """The directory of the *original* warehouse, not the read copy.

    Scripts run against a temp copy of the database, so ``wh.path.parent`` is a
    scratch directory with no artefacts in it. ``run_script`` stamps the real
    path on the handle; without this, every artefact lookup would silently miss.
    """
    src = getattr(wh, "source_path", None)
    return Path(src).parent if src is not None else Path(wh.path).parent


def load_projection(wh) -> pd.DataFrame | None:
    """The cached per-player simulation artefact, or None.

    Mirrors ``QuestionRouter._projection``: the solve script writes ``code`` as
    the index and every consumer wants it as a column, so normalise once here
    rather than in each caller.
    """
    path = source_dir(wh) / PROJECTION_NAME
    if not path.exists():
        return None
    df = pd.read_parquet(path)
    if "code" not in df.columns:
        df = df.reset_index()
    return df


def q(wh, sql: str, params: tuple = ()) -> pd.DataFrame:
    """Run one guarded read on an already-open read copy.

    Panel scripts do not get a second data path: this funnels to exactly the
    same guard the ``/api/query`` endpoint and the chat tool use.
    """
    res = guarded_query(sql, params, warehouse=wh)
    return pd.DataFrame(res.rows, columns=res.columns or None)


def latest_as_of(wh, table: str, season: str | None = None) -> str | None:
    where = "WHERE season = ?" if season else ""
    params = (season,) if season else ()
    df = q(wh, f"SELECT max(as_of) AS a FROM {table} {where}", params)
    if df.empty or df.iloc[0]["a"] is None:
        return None
    return str(df.iloc[0]["a"])


def next_gw(wh, season: str, now: dt.datetime | None = None) -> int | None:
    """First gameweek whose deadline has not passed. None if none is known."""
    when = (now or dt.datetime.now(UTC)).astimezone(UTC)
    df = q(
        wh,
        "SELECT gw, deadline_utc FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, gw ORDER BY as_of DESC) rn"
        "  FROM dim_event WHERE season = ?"
        ") WHERE rn = 1 AND deadline_utc > ? ORDER BY deadline_utc LIMIT 1",
        (season, when),
    )
    if df.empty:
        return None
    return int(df.iloc[0]["gw"])
