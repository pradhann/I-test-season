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
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_VIEWS_PATH = Path(__file__).with_name("views.sql")

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
    # Derived odds (clean-sheet, xG-share, team-lambda priors). Registered
    # HERE, not as an import-time side effect in ingest/odds_derived.py:
    # registration-by-import meant Snapshot.table("fact_odds_derived") raised
    # KeyError in any process that had not happened to import that module.
    "fact_odds_derived": ("fixture_key", "entity_type", "entity_code", "market", "method"),
    # A third party's per-player per-match statistics (xG, xA, shots, defensive
    # actions), copied verbatim from the publisher. `match_id` is the
    # publisher's own match key, so one logical entity is one player in one of
    # THEIR matches; the same real-world match seen by two sources is two
    # entities on purpose. Registered here for the same reason as
    # fact_odds_derived: registration-by-import breaks every process that did
    # not happen to import the ingest module.
    "fact_player_match_stats": ("source", "season", "code", "match_id"),
    # Confirmed starting lineups copied from an official teamsheet feed.
    # as_of is the fetch instant; lineups publish ~T-60m before kickoff, so a
    # deadline snapshot correctly never sees them.
    "fact_confirmed_lineup": ("source", "season", "fixture_id", "code"),
    # Pulselive identity bridges: each player/fixture is matched once, then
    # joined by id forever after. Registered here, never by import side effect.
    "bridge_pl_player": ("season", "pl_player_id"),
    "bridge_pl_fixture": ("season", "pl_fixture_id"),
}


class LeakageError(RuntimeError):
    """Raised when a read would expose information from after the as-of instant."""


class UnknownAvailabilityError(RuntimeError):
    """The season's availability was never recorded, and the caller did not say
    what to do about that.

    The vaastav archive carries no ``status``/``news``, so every historical
    ``fact_player_state`` row has NULL availability -- deliberately (see
    ``ingest/vaastav.py``: filling it would fabricate the most
    decision-relevant field in the table). Treating that NULL as "available"
    made every injured and suspended player in four seasons selectable, which
    silently corrupted any backtest. Callers that genuinely want the
    optimistic reading opt in with ``assume_available_when_unknown=True`` --
    an explicit, greppable assumption instead of a silent default.
    """


class WarehouseLockedError(RuntimeError):
    """Another process holds the single writer lock and did not release it."""


class ConflictingFactError(ValueError):
    """Two different values claim the same entity at the same instant.

    Appending is idempotent for identical rows, but a row that shares an
    entity key and ``as_of`` while carrying different values is not a duplicate
    -- it is a contradiction. Silently keeping the first one would let a
    corrected result, a revised bonus award or a re-scraped price disappear
    without trace. The caller must resolve it explicitly, normally by giving the
    correction its own later ``as_of``.
    """


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(dt.UTC)


@dataclass(frozen=True)
class Snapshot:
    """A read-only view of the warehouse as it was known at ``as_of``.

    Every model input must be routed through one of these. Constructing a
    Snapshot is the only sanctioned way to read mutable facts, which makes
    leakage an auditable property: grep for direct table reads outside this
    class and you have found your bug.

    The warehouse handle is deliberately private. When it was public,
    ``snapshot.warehouse.sql(...)`` read the entire future in a single line and
    looked exactly like legitimate code in review.
    """

    _wh: Warehouse
    as_of: dt.datetime

    def __post_init__(self) -> None:
        # snapshot_at() validates, but nothing stopped a caller constructing a
        # Snapshot directly with a naive datetime and bypassing the check.
        object.__setattr__(self, "as_of", _require_utc(self.as_of, "Snapshot.as_of"))

    @property
    def warehouse(self) -> Warehouse:
        raise LeakageError(
            "Snapshot.warehouse is not available: reading the warehouse directly "
            "bypasses point-in-time filtering and exposes the future. Use "
            "snapshot.table(...) / players(...) / results_before(...). If you "
            "genuinely need unfiltered access, call escape_hatch_unfiltered() so "
            "the intent is explicit and greppable."
        )

    def escape_hatch_unfiltered(self, reason: str) -> Warehouse:
        """Unfiltered warehouse access. Every call site must justify itself.

        Exists so that legitimate needs (schema introspection, audits) do not
        force people to reintroduce a silent public handle.
        """
        if not reason or len(reason) < 20:
            raise ValueError("escape_hatch_unfiltered requires a substantive reason")
        return self._wh

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
        # Deterministic order. DuckDB parallelises scans, so without an explicit
        # ORDER BY the row order varies with thread count -- which makes seeded
        # model runs irreproducible across machines for no visible reason.
        return self._wh.sql(sql + f" ORDER BY {keys}", [self.as_of, *params])

    def players(self, season: str) -> pd.DataFrame:
        """Players with price, ownership and availability.

        Optional columns are selected only if the connected database actually
        has them. A read-only consumer cannot run migrations, so hard-coding a
        newer column list here breaks every reader against an older file --
        which is exactly what happened when can_select was introduced while a
        long-running writer held the lock.
        """
        # identity_as_of and state_as_of are reported separately as well as
        # combined: a single max() hides the case where a price is two weeks
        # staler than the identity row it is attached to, and a caller has no
        # way to notice.
        optional = [c for c in ("can_select", "can_transact", "removed")
                    if c in self._columns("fact_player_state")]
        extra = "".join(f"s.{c}, " for c in optional)
        return self._wh.sql(
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
                   """ + extra + """
                   p.as_of AS identity_as_of, s.as_of AS state_as_of,
                   greatest(p.as_of, s.as_of) AS as_of
            FROM p JOIN s USING (season, code)
            """,
            [self.as_of, season, self.as_of, season],
        )

    def _columns(self, table: str) -> set[str]:
        rows = self._wh.sql(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [table],
        )
        return set(rows["column_name"]) if not rows.empty else set()

    def selectable(
        self, season: str, *, assume_available_when_unknown: bool = False
    ) -> pd.DataFrame:
        """Players the game would actually let you pick right now.

        Filters on FPL's own ``can_select`` where it is known, falling back to
        ``status`` only where the flag is absent (historical rows predate it).
        Filtering on ``status`` alone is not equivalent: the fields can diverge,
        and ``can_select`` is what the game enforces.

        A NULL ``status`` means availability was never recorded, NOT that the
        player was available -- historical seasons carry no availability at
        all. Such rows are excluded by default; when the whole season is
        unrecorded that would return nobody, so it raises
        :class:`UnknownAvailabilityError` instead, and a backtest that accepts
        the optimism passes ``assume_available_when_unknown=True`` to say so
        in its own source.
        """
        df = self.players(season)
        if df.empty:
            return df
        status_known = df["status"].notna()
        if not status_known.any() and not assume_available_when_unknown:
            raise UnknownAvailabilityError(
                f"No availability was ever recorded for {season}: every status "
                "is NULL (the historical archive carries none). Refusing to "
                "guess who was selectable. If assuming everyone was available "
                "is acceptable for this analysis, say so explicitly: "
                "selectable(season, assume_available_when_unknown=True)."
            )
        if "can_select" in df.columns and df["can_select"].notna().any():
            keep = df["can_select"].fillna(True).astype(bool)
        else:
            keep = pd.Series(True, index=df.index)
        status_ok = df["status"].isin(["a", "d"])
        if assume_available_when_unknown:
            status_ok |= ~status_known
        keep &= status_ok
        if "removed" in df.columns:
            keep &= ~df["removed"].fillna(False).astype(bool)
        return df[keep].reset_index(drop=True)

    def results_before(self, season: str) -> pd.DataFrame:
        """Finalised per-fixture returns visible at this instant.

        Note the ``as_of <= deadline`` filter uses points-finalisation time, so a
        gameweek that has kicked off but not been finalised is correctly absent.
        """
        rows = self.table("fact_player_fixture", where="season = ?", params=[season])
        if rows.empty:
            return rows
        # Drop rows whose code has no dim_player entry. A manager element that
        # was correctly refused at ingestion can still leave per-fixture rows
        # behind, and those would reach a training set as an unlabelled player.
        known = self.table("dim_player", where="season = ?", params=[season])["code"]
        return rows[rows["code"].isin(set(known))].reset_index(drop=True)

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


class LeasedWarehouse:
    """A Warehouse that holds the file lock only while actually in use.

    DuckDB permits one writer per file. A long-lived process (the Telegram
    bot under launchd, KeepAlive, alive for months) that opens a plain
    Warehouse therefore starves every other writer -- the nightly settlement
    job, ideas CLI, every ingest -- for its whole life. This wrapper connects
    on first attribute access and disconnects on :meth:`release`, which the
    bot calls after each poll cycle; between messages the lock is free.
    """

    def __init__(self, path: Path | str = DEFAULT_DB, *, lock_timeout_s: float = 60.0) -> None:
        self._path = Path(path)
        self._lock_timeout_s = lock_timeout_s
        self._wh: Warehouse | None = None

    def _ensure(self) -> Warehouse:
        if self._wh is None:
            self._wh = Warehouse(self._path, lock_timeout_s=self._lock_timeout_s)
        return self._wh

    def __getattr__(self, name: str):
        return getattr(self._ensure(), name)

    def release(self) -> None:
        if self._wh is not None:
            self._wh.close()
            self._wh = None

    def close(self) -> None:
        self.release()

    def __enter__(self) -> LeasedWarehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


class Warehouse:
    """Owns the DuckDB connection and all writes."""

    def __init__(
        self,
        path: Path | str = DEFAULT_DB,
        *,
        read_only: bool = False,
        lock_timeout_s: float = 30.0,
    ) -> None:
        self.path = Path(path)
        #: A tempdir this instance owns and must delete on close(). Set only by
        #: read_copy(); plain opens own nothing.
        self._owned_tmpdir: Path | None = None
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._con = self._connect(read_only, lock_timeout_s)
        # Pin the session timezone. DuckDB stores TIMESTAMPTZ as a correct
        # instant but RENDERS it in the host's local zone, so on a machine set
        # to US/Pacific the GW1 deadline reads back as "10:30" rather than
        # "17:30Z". The instant is right, but every comparison, format and test
        # then depends on where the machine happens to be. Pinning to UTC makes
        # reads reproducible across machines and matches the rule registry's
        # rule that the API's UTC deadline is the only authority.
        self._con.execute("SET TimeZone='UTC'")
        if not read_only:
            self._con.execute(_SCHEMA_PATH.read_text())
            self._migrate()
            # The semantic layer: PIT-parameterised macros stored in the file
            # itself, so every read copy carries them. CREATE OR REPLACE makes
            # this idempotent and lets views.sql evolve with the code.
            self._con.execute(_VIEWS_PATH.read_text())

    def _connect(self, read_only: bool, timeout_s: float) -> duckdb.DuckDBPyConnection:
        """Open the database, waiting briefly for a conflicting writer.

        DuckDB allows a single writer process. That is the right trade for this
        workload -- ingestion is single-writer by design -- but a scheduled
        weekly run can still collide with an ad-hoc ingest and would otherwise
        die instantly at the least convenient moment. We wait, then fail with an
        error that says who holds the lock rather than a raw IOException.
        """
        deadline = time.monotonic() + timeout_s
        delay = 0.25
        while True:
            try:
                return duckdb.connect(str(self.path), read_only=read_only)
            except duckdb.IOException as exc:
                if "lock" not in str(exc).lower() or time.monotonic() >= deadline:
                    if "lock" in str(exc).lower():
                        raise WarehouseLockedError(
                            f"{self.path} is locked by another process and did not "
                            f"free up within {timeout_s:.0f}s. DuckDB permits one "
                            f"writer; open read_only=True for concurrent reads.\n"
                            f"{exc}"
                        ) from exc
                    raise
                time.sleep(delay)
                delay = min(delay * 2, 2.0)

    def _migrate(self) -> None:
        """Additive, idempotent column migrations.

        CREATE TABLE IF NOT EXISTS does not add columns to a table that already
        exists, so a warehouse built before a column was introduced would keep
        silently missing it.
        """
        additions = {
            "fact_player_state": [
                ("can_select", "BOOLEAN"),
                ("can_transact", "BOOLEAN"),
                ("removed", "BOOLEAN"),
            ],
        }
        for table, cols in additions.items():
            existing = {
                r[0] for r in self._con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ?", [table]
                ).fetchall()
            }
            for name, sqltype in cols:
                if name not in existing:
                    self._con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")

    # -- reads ---------------------------------------------------------------

    def sql(self, query: str, params: Iterable[Any] = ()) -> pd.DataFrame:
        return self._con.execute(query, list(params)).df()

    @classmethod
    def read_copy(cls, path: Path | str = DEFAULT_DB) -> Warehouse:
        """Open a read-only connection to a private copy of the database file.

        DuckDB allows one writer OR many readers per file, across processes. A
        model fit or MILP solve that holds a reader open for minutes therefore
        blocks every ingest job for its whole runtime. Heavy read-only work
        should read a copy: the file is tens of megabytes, the copy takes
        milliseconds, and the original stays free for writers. The copy is a
        consistent snapshot of the database at copy time, which is exactly the
        semantics a point-in-time job wants anyway.
        """
        import shutil
        import tempfile
        import weakref

        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"no warehouse at {src}")
        tmpdir = Path(tempfile.mkdtemp(prefix="fpl-read-"))
        tmp = tmpdir / src.name
        try:
            shutil.copy2(src, tmp)
            wal = src.with_suffix(src.suffix + ".wal")
            if wal.exists():
                shutil.copy2(wal, tmp.with_suffix(tmp.suffix + ".wal"))
            wh = cls(tmp, read_only=True)
        except BaseException:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise
        # The copy is this instance's private property and its responsibility:
        # close() deletes it. Before this, every caller that forgot to clean up
        # leaked a full database copy -- 457 orphaned directories (5.1 GB) were
        # found on one machine, one per DAG tick among others. The finalizer is
        # the backstop for callers that never close(); it must not capture
        # `wh` or the object could never be collected.
        wh._owned_tmpdir = tmpdir
        weakref.finalize(wh, shutil.rmtree, str(tmpdir), True)
        return wh

    def snapshot_at(self, as_of: dt.datetime) -> Snapshot:
        """The only sanctioned entry point for reading mutable facts.

        ``as_of`` must be tz-aware UTC. Passing a deadline here guarantees the
        caller cannot see a price, ownership figure, injury update, lineup or
        result that was not public at that moment.
        """
        return Snapshot(self, _require_utc(as_of, "as_of"))

    # -- writes --------------------------------------------------------------

    def append(self, table: str, df: pd.DataFrame,
               *, change_dedup: bool = False) -> int:
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
        raw = pd.to_datetime(df["as_of"])
        # Check awareness BEFORE converting. pd.to_datetime(..., utc=True)
        # localises a naive series to UTC without complaint, so the old check
        # could never fire and a naive timestamp -- typically a local-time
        # kickoff -- was silently reinterpreted as UTC. That shifts facts by
        # the host's offset and quietly breaks point-in-time filtering.
        if getattr(raw.dtype, "tz", None) is None:
            raise ValueError(
                f"{table}: as_of must be timezone-aware UTC; got naive timestamps. "
                "Localise them explicitly rather than letting pandas assume UTC."
            )
        df = df.assign(as_of=raw.dt.tz_convert("UTC"))

        # Every other timestamp column needs the same guarantee. A naive
        # kickoff_utc or deadline_utc is reinterpreted as local time by whatever
        # reads it, which is how a deadline silently moves by the host offset.
        for col in df.columns:
            if col == "as_of" or not (col.endswith(("_utc", "_at", "_added"))):
                continue
            series = df[col]
            if series.isna().all():
                continue
            parsed = pd.to_datetime(series)
            if getattr(parsed.dtype, "tz", None) is None:
                raise ValueError(
                    f"{table}.{col} contains naive timestamps and must be "
                    f"timezone-aware UTC. Localise explicitly rather than "
                    f"letting the reader assume a timezone."
                )
            df = df.assign(**{col: parsed.dt.tz_convert("UTC")})

        if table == "fact_fixture":
            has_result = df.get("finished")
            if has_result is not None and "kickoff_utc" in df.columns:
                scored = df[
                    df["finished"].fillna(False).astype(bool)
                    & df["kickoff_utc"].notna()
                    & (pd.to_datetime(df["as_of"], utc=True)
                       < pd.to_datetime(df["kickoff_utc"], utc=True))
                ]
                if not scored.empty:
                    raise ValueError(
                        f"fact_fixture: {len(scored)} finished fixture(s) claim to be "
                        f"observable before their own kickoff. A result is not public "
                        f"until the match has been played.\n"
                        f"{scored[['season', 'fixture_id', 'as_of', 'kickoff_utc']].head(3)}"
                    )

        if table == "fact_player_fixture" and "kickoff_utc" not in df.columns:
            # Cross-check against the fixture table: a result stamped observable
            # before its own kickoff is impossible, and this exact shape (as_of
            # set to a deadline rather than to finalisation) is how a backtest
            # ends up reading the future.
            try:
                kicks = self._con.execute(
                    "SELECT season, fixture_id, max(kickoff_utc) AS ko "
                    "FROM fact_fixture GROUP BY season, fixture_id"
                ).df()
            except duckdb.Error:
                kicks = pd.DataFrame()
            if not kicks.empty:
                merged = df.merge(kicks, on=["season", "fixture_id"], how="left")
                impossible = merged[
                    merged["ko"].notna()
                    & (pd.to_datetime(merged["as_of"], utc=True) < merged["ko"])
                ]
                if not impossible.empty:
                    raise ValueError(
                        f"fact_player_fixture: {len(impossible)} row(s) claim to be "
                        f"observable before their own kickoff. Set as_of to points "
                        f"finalisation, not to the deadline.\n"
                        f"{impossible[['season', 'code', 'fixture_id', 'as_of', 'ko']].head(3)}"
                    )

        keys = [*PIT_KEYS[table], "as_of"]
        # Drop exact in-batch duplicates first; identical rows are idempotent,
        # and leaving them in would make the contradiction check below fire on
        # a batch that is merely repetitive.
        df = df.drop_duplicates()
        self._con.register("_incoming", df)
        before = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        on = " AND ".join(f"t.{k} IS NOT DISTINCT FROM i.{k}" for k in keys)
        cols = ", ".join(df.columns)

        # A same-key, same-as_of row with different values is a contradiction,
        # not a duplicate. Refuse it loudly rather than keeping whichever
        # arrived first.
        payload = [c for c in df.columns if c not in keys]
        if payload:
            differs = " OR ".join(f"t.{c} IS DISTINCT FROM i.{c}" for c in payload)
            clash = self._con.execute(
                f"SELECT count(*) FROM {table} t JOIN _incoming i ON {on} WHERE {differs}"
            ).fetchone()[0]
            if clash:
                sample = self._con.execute(
                    f"SELECT {', '.join('i.' + k for k in keys)} "
                    f"FROM {table} t JOIN _incoming i ON {on} WHERE {differs} LIMIT 3"
                ).df()
                self._con.unregister("_incoming")
                raise ConflictingFactError(
                    f"{table}: {clash} incoming row(s) contradict stored values at the "
                    f"same as_of. Give the correction a later as_of instead of "
                    f"overwriting history.\n{sample}"
                )
        unchanged = 0
        if change_dedup and payload:
            # Write-on-change (fetch_ledger module docstring has the full
            # contract): rows newer than the entity's latest stored row whose
            # payload is identical are dropped and COUNTED, never written.
            # Backfills and same-as_of rows pass through untouched.
            from fpl_edge.store.fetch_ledger import drop_unchanged
            unchanged = drop_unchanged(
                self._con, table, list(PIT_KEYS[table]), payload, "_incoming")
        self._con.execute(
            f"""
            INSERT INTO {table} ({cols})
            SELECT {cols} FROM _incoming i
            WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {on})
            """
        )
        after = self._con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        self._con.unregister("_incoming")
        self._last_unchanged = unchanged
        return after - before

    def append_measured(self, table: str, df: pd.DataFrame,
                        *, change_dedup: bool = False) -> tuple[int, int]:
        """append(), returning (rows_written, rows_unchanged).

        ``rows_unchanged`` is nonzero only with ``change_dedup=True``: it
        counts incoming rows dropped because the entity's latest stored
        payload was identical -- the "refetched, unchanged" fact the caller
        records in the fetch ledger. Kept as a wrapper so the many existing
        ``append()`` call sites keep their int return unchanged.
        """
        written = self.append(table, df, change_dedup=change_dedup)
        return written, getattr(self, "_last_unchanged", 0)

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
        if self._owned_tmpdir is not None:
            import shutil

            shutil.rmtree(self._owned_tmpdir, ignore_errors=True)
            self._owned_tmpdir = None

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
