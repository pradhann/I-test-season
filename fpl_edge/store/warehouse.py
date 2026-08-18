"""DuckDB warehouse with point-in-time-correct reads.

Why DuckDB rather than SQLite or bare parquet:

* Native ``ASOF JOIN`` and ``QUALIFY``. The core operation this engine performs
  is "the most recent value of X known at or before deadline T", for millions of
  (player, deadline) pairs. DuckDB expresses that as one operator. In SQLite it
  is a correlated subquery per row.
* Columnar and vectorised, so full-season backtests scan fast without an index
  zoo, and it reads/writes parquet directly for the raw archive.
* Single file, no server, trivially reproducible and easy to delete and rebuild.

The cost is that DuckDB has weaker concurrent-writer support than SQLite. That
is acceptable: ingestion is single-writer by design, and readers open the file
read-only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

DEFAULT_DB = Path("data/warehouse/fpl.duckdb")

#: Tables that carry point-in-time semantics, mapped to the columns that
#: identify a single logical entity (excluding ``as_of``).
PIT_KEYS: dict[str, tuple[str, ...]] = {
    "dim_event": ("season", "gw"),
    "dim_team": ("season", "team_code"),
    "dim_player": ("season", "code"),
    "fact_player_state": ("season", "code"),
    "fact_fixture": ("season", "fixture_id"),
    "fact_player_fixture": ("season", "code", "fixture_id"),
    "fact_odds": ("fixture_key", "bookmaker", "market", "selection"),
}


class LeakageError(RuntimeError):
    """Raised when a read would expose information from after the as-of instant."""


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(dt.timezone.utc)


@dataclass(frozen=True)
class Snapshot:
    """A read-only view of the warehouse as it was known at ``as_of``.

    Every model input must be routed through one of these. Constructing a
    Snapshot is the only sanctioned way to read mutable facts, which makes
    leakage an auditable property: grep for direct table reads outside this
    class and you have found your bug.
    """

    warehouse: "Warehouse"
    as_of: dt.datetime

    def table(
        self,
        name: str,
        *,
        where: str | None = None,
        params: Iterable[Any] = (),
    ) -> pd.DataFrame:
        """Latest row per entity with ``as_of <= self.as_of``."""
        if name not in PIT_KEYS:
            raise KeyError(f"{name!r} is not a point-in-time table; known: {sorted(PIT_KEYS)}")
        keys = ", ".join(PIT_KEYS[name])
        clause = f"AND ({where})" if where else ""
        sql = f"""
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {keys} ORDER BY as_of DESC
                ) AS rn
                FROM {name}
                WHERE as_of <= ? {clause}
            ) WHERE rn = 1
        """
        return self.warehouse.sql(sql, [self.as_of, *params])

    def players(self, season: str) -> pd.DataFrame:
        """Squad-selectable players with price, ownership and availability."""
        return self.warehouse.sql(
            """
            WITH p AS (
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code
                                                 ORDER BY as_of DESC) rn
                    FROM dim_player WHERE as_of <= ? AND season = ?
                ) WHERE rn = 1
            ), s AS (
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code
                                                 ORDER BY as_of DESC) rn
                    FROM fact_player_state WHERE as_of <= ? AND season = ?
                ) WHERE rn = 1
            )
            SELECT p.season, p.code, p.element_id, p.web_name, p.position,
                   p.team_code, s.price_tenths, s.selected_by_pct, s.status,
                   s.chance_of_playing_next_round, s.news,
                   s.transfers_in_event, s.transfers_out_event,
                   greatest(p.as_of, s.as_of) AS as_of
            FROM p JOIN s USING (season, code)
            """,
            [self.as_of, season, self.as_of, season],
        )

    def results_before(self, season: str) -> pd.DataFrame:
        """Finalised per-fixture returns visible at this instant.

        Note the ``as_of <= deadline`` filter uses points-finalisation time, so a
        gameweek that has kicked off but not been finalised is correctly absent.
        """
        return self.table("fact_player_fixture", where="season = ?", params=[season])

    def upcoming_fixtures(self, season: str, *, horizon_gws: int | None = None) -> pd.DataFrame:
        """Fixtures whose kickoff is strictly after this instant.

        Fixture *scheduling* is public in advance, so the fixture list itself is
        not leakage; the result columns are, and they are NULL here by
        construction because ``as_of`` filtering hides post-hoc updates.
        """
        fx = self.table("fact_fixture", where="season = ?", params=[season])
        if fx.empty:
            return fx
        fx = fx[fx["kickoff_utc"].notna() & (fx["kickoff_utc"] > self.as_of)]
        if horizon_gws is not None and not fx.empty:
            first = fx["gw"].min()
            fx = fx[fx["gw"] < first + horizon_gws]
        return fx.sort_values(["gw", "kickoff_utc"]).reset_index(drop=True)

    def deadline(self, season: str, gw: int) -> dt.datetime:
        df = self.table("dim_event", where="season = ? AND gw = ?", params=[season, gw])
        if df.empty:
            raise KeyError(f"No deadline known at {self.as_of} for {season} GW{gw}")
        return df.iloc[0]["deadline_utc"].to_pydatetime()

    def next_gw(self, season: str) -> int:
        """The first gameweek whose deadline has not passed at this instant."""
        df = self.table("dim_event", where="season = ?", params=[season])
        future = df[df["deadline_utc"] > self.as_of]
        if future.empty:
            raise KeyError(f"No future gameweek at {self.as_of} in {season}")
        return int(future.sort_values("deadline_utc").iloc[0]["gw"])


class Warehouse:
    """Owns the DuckDB connection and all writes."""

    def __init__(self, path: Path | str = DEFAULT_DB, *, read_only: bool = False) -> None:
        self.path = Path(path)
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self.path), read_only=read_only)
        if not read_only:
            self._con.execute(_SCHEMA_PATH.read_text())

    # -- reads ---------------------------------------------------------------

    def sql(self, query: str, params: Iterable[Any] = ()) -> pd.DataFrame:
        return self._con.execute(query, list(params)).df()

    def snapshot_at(self, as_of: dt.datetime) -> Snapshot:
        """The only sanctioned entry point for reading mutable facts.

        ``as_of`` must be tz-aware UTC. Passing a deadline here guarantees the
        caller cannot see a price, ownership figure, injury update, lineup or
        result that was not public at that moment.
        """
        return Snapshot(self, _require_utc(as_of, "as_of"))

    # -- writes --------------------------------------------------------------

    def append(self, table: str, df: pd.DataFrame) -> int:
        """Append rows, dropping exact duplicates already present.

        Ingestion is append-only and idempotent: re-running a fetch that
        returned identical data adds nothing, so raw archives can be replayed.
        """
        if df.empty:
            return 0
        if table not in PIT_KEYS:
            raise KeyError(f"unknown table {table!r}")
        if "as_of" not in df.columns:
            raise ValueError(f"{table}: every fact row must carry as_of")
        if df["as_of"].isna().any():
            raise ValueError(f"{table}: as_of contains nulls")
        as_of = pd.to_datetime(df["as_of"], utc=True)
        if as_of.dt.tz is None:
            raise ValueError(f"{table}: as_of must be tz-aware UTC")
        df = df.assign(as_of=as_of)

        keys = [*PIT_KEYS[table], "as_of"]
        self._con.register("_incoming", df)
        before = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        on = " AND ".join(f"t.{k} IS NOT DISTINCT FROM i.{k}" for k in keys)
        cols = ", ".join(df.columns)
        self._con.execute(
            f"""
            INSERT INTO {table} ({cols})
            SELECT {cols} FROM _incoming i
            WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {on})
            """
        )
        after = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        self._con.unregister("_incoming")
        return after - before

    def next_fetch_id(self) -> int:
        return int(self._con.execute("SELECT nextval('seq_fetch_id')").fetchone()[0])

    def record_fetch(
        self,
        *,
        source: str,
        endpoint: str,
        params: str | None,
        fetched_at: dt.datetime,
        sha256: str,
        body_path: str,
        http_status: int | None,
    ) -> int:
        fetch_id = self.next_fetch_id()
        self._con.execute(
            "INSERT INTO raw_fetch VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [fetch_id, source, endpoint, params,
             _require_utc(fetched_at, "fetched_at"), sha256, body_path, http_status],
        )
        return fetch_id

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> "Warehouse":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
