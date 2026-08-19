"""Warehouse access for third-party projections.

Owns its own DDL (``migrations/*.sql``) and its own append path rather than
registering into ``fpl_edge.store.PIT_KEYS``. Two reasons:

* ``PIT_KEYS`` is the store team's contract. Mutating a module-level dict at
  import time to smuggle tables into someone else's invariant is the kind of
  thing that works until two teams do it.
* The idempotency and contradiction rules still apply, so they are implemented
  here explicitly and tested here, where they can be read next to the tables
  they protect.

The point-in-time rule is unchanged and is the whole reason this is not a CSV
on disk: ``as_of`` is the FETCH INSTANT. A provider republishes its projections
several times a day as team news lands, and each republication is a different
fact. Reading "the projection as it stood at the GW1 deadline" has to mean the
last one we actually fetched before that deadline, not the last one the provider
happened to compute.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from fpl_edge.store import Warehouse

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

UTC = dt.timezone.utc

#: Entity keys per table, excluding ``as_of``. Same shape as ``store.PIT_KEYS``
#: but owned here.
PROJECTION_KEYS: dict[str, tuple[str, ...]] = {
    "fact_projection": ("provider", "season", "gw", "code"),
    "fact_external_ownership": ("provider", "season", "gw", "code", "metric"),
}


class ConflictingProjectionError(ValueError):
    """Two different values claim the same (provider, player, gw) at one instant.

    Mirrors ``store.ConflictingFactError``. A provider that revises a projection
    must get a later ``as_of``; overwriting in place would erase the fact that
    the number we decided on was different from the number that is there now,
    which is exactly the evidence a post-mortem needs.
    """


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


class ProjectionStore:
    """Migrations, appends and as-of reads for the projection tables."""

    def __init__(self, warehouse: Warehouse) -> None:
        self.wh = warehouse
        self.migrate()

    # -- schema --------------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply unapplied migrations. Idempotent, and records each file's sha256."""
        self.wh.sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                version VARCHAR PRIMARY KEY,
                applied_utc TIMESTAMPTZ NOT NULL,
                sha256 VARCHAR NOT NULL
            )
            """
        )
        applied = set(
            self.wh.sql("SELECT version FROM schema_migration")["version"].astype(str)
        )
        run: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in applied:
                continue
            body = path.read_text()
            self.wh.sql(body)
            self.wh.sql(
                "INSERT INTO schema_migration VALUES (?, ?, ?)",
                [path.stem, dt.datetime.now(UTC),
                 hashlib.sha256(body.encode("utf-8")).hexdigest()],
            )
            run.append(path.stem)
        return run

    # -- writes --------------------------------------------------------------

    def append(self, table: str, df: pd.DataFrame) -> int:
        """Append rows, skipping exact duplicates, refusing contradictions."""
        if table not in PROJECTION_KEYS:
            raise KeyError(f"unknown projection table {table!r}")
        if df.empty:
            return 0
        if "as_of" not in df.columns:
            raise ValueError(f"{table}: every row must carry as_of")
        if df["as_of"].isna().any():
            raise ValueError(f"{table}: as_of contains nulls")
        raw = pd.to_datetime(df["as_of"])
        if getattr(raw.dtype, "tz", None) is None:
            raise ValueError(
                f"{table}: as_of must be timezone-aware UTC; got naive timestamps. "
                "Localise explicitly rather than letting pandas assume UTC."
            )
        df = df.assign(as_of=raw.dt.tz_convert("UTC")).drop_duplicates()

        keys = [*PROJECTION_KEYS[table], "as_of"]
        payload = [c for c in df.columns if c not in keys]
        self.wh.sql("SET TimeZone='UTC'")
        con = self.wh._con  # noqa: SLF001 -- this class is the writer for these tables
        con.register("_incoming_proj", df)
        try:
            on = " AND ".join(f"t.{k} IS NOT DISTINCT FROM i.{k}" for k in keys)
            if payload:
                differs = " OR ".join(f"t.{c} IS DISTINCT FROM i.{c}" for c in payload)
                clash = con.execute(
                    f"SELECT count(*) FROM {table} t JOIN _incoming_proj i ON {on} "
                    f"WHERE {differs}"
                ).fetchone()[0]
                if clash:
                    sample = con.execute(
                        f"SELECT {', '.join('i.' + k for k in keys)} FROM {table} t "
                        f"JOIN _incoming_proj i ON {on} WHERE {differs} LIMIT 3"
                    ).df()
                    raise ConflictingProjectionError(
                        f"{table}: {clash} incoming row(s) contradict stored values at "
                        f"the same as_of. A revised projection needs a later as_of.\n"
                        f"{sample}"
                    )
            before = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            cols = ", ".join(df.columns)
            con.execute(
                f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming_proj i "
                f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE {on})"
            )
            after = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            con.unregister("_incoming_proj")
        return int(after - before)

    def record_weights(self, fit_id: str, rows: pd.DataFrame) -> int:
        """Store a weight fit. Re-running the same ``fit_id`` replaces it."""
        if rows.empty:
            return 0
        self.wh.sql("DELETE FROM projection_weight WHERE fit_id = ?", [fit_id])
        con = self.wh._con  # noqa: SLF001
        con.register("_incoming_w", rows.assign(fit_id=fit_id))
        try:
            cols = ", ".join(
                ["fit_id", "provider", "weight", "loss", "loss_metric",
                 "baseline_loss", "n_obs", "earned", "holdout", "as_of"]
            )
            con.execute(f"INSERT INTO projection_weight ({cols}) SELECT {cols} FROM _incoming_w")
        finally:
            con.unregister("_incoming_w")
        return len(rows)

    # -- reads ---------------------------------------------------------------

    def as_of(
        self,
        table: str,
        instant: dt.datetime,
        *,
        where: str | None = None,
        params: list[object] | None = None,
    ) -> pd.DataFrame:
        """Latest row per entity with ``as_of <= instant``.

        The projection analogue of ``Snapshot.table``. Nothing outside this
        method should read these tables in anger: a bare ``SELECT * FROM
        fact_projection`` in a backtest reads next week's revision.
        """
        if table not in PROJECTION_KEYS:
            raise KeyError(f"unknown projection table {table!r}")
        instant = _require_utc(instant, "instant")
        keys = ", ".join(PROJECTION_KEYS[table])
        clause = f"AND ({where})" if where else ""
        return self.wh.sql(
            f"""
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY {keys} ORDER BY as_of DESC) rn
                FROM {table} WHERE as_of <= ? {clause}
            ) WHERE rn = 1 ORDER BY {keys}
            """,
            [instant, *(params or [])],
        )

    def providers(self, table: str = "fact_projection") -> pd.DataFrame:
        return self.wh.sql(
            f"SELECT provider, count(*) AS rows, count(DISTINCT code) AS players, "
            f"min(gw) AS first_gw, max(gw) AS last_gw, min(as_of) AS first_seen, "
            f"max(as_of) AS last_seen FROM {table} GROUP BY 1 ORDER BY 1"
        )
