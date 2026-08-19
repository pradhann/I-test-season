"""Persistence for intel, with point-in-time reads that cannot leak.

The store owns its own migration (``fpl_edge/intel/migrations/001_intel.sql``)
and applies it against the same DuckDB file everything else uses.
``fpl_edge/store/schema.sql`` is never edited: it is the shared contract the
ingest, model and optimiser teams read, and a fourth team adding tables to it
would collide with all three. The idea registry already established this
pattern; this is the second instance of it, recorded in the same
``schema_migration`` table under a namespaced version so the two runners cannot
fight over a version string.

**The one rule.** Reads take an ``as_of`` and filter on ``published_at`` (or the
equivalent ``as_of`` / ``detected_at`` column), never on ``observed_at``. So:

* An item published after the instant you asked about is invisible. That is
  leak-proofing, and :func:`tests.unit.test_intel_pit` proves it.
* An item published *before* that instant is visible even if our poller only
  fetched it afterwards. That is not a leak: the world knew. Hiding it would
  make the backtest pessimistic in a way that flatters the model, which is the
  more insidious error because nobody files a bug about it.

Every value is bound as a parameter. Nothing in this module interpolates a
string that could have come from a news field, a headline, or a URL.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from fpl_edge.intel.items import (
    Duty,
    DutyChange,
    FormationObservation,
    IntelItem,
    IntelKind,
    OopSignal,
    SetPieceDuty,
    SourceProbe,
)
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: Version strings are prefixed so this runner and the interfaces team's runner
#: can never claim the same row in ``schema_migration``.
VERSION_PREFIX = "intel_"


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


def _opt_int(value: object) -> int | None:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    return int(value)  # type: ignore[arg-type]


def _opt_float(value: object) -> float | None:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    return float(value)  # type: ignore[arg-type]


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) and pd.isna(value):  # type: ignore[arg-type]
        return None
    return str(value)


def _ts(value: object) -> dt.datetime:
    stamp = pd.Timestamp(value)  # type: ignore[arg-type]
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(UTC)
    return stamp.to_pydatetime().astimezone(UTC)


#: Every table this module owns. Used to answer "has intel ever been collected?"
#: without attempting DDL, which matters because the warehouse permits exactly
#: one writer and a dossier is very often not it.
TABLES = (
    "intel_item", "set_piece_duty", "set_piece_change",
    "oop_signal", "formation_observation", "source_probe",
)


class IntelStore:
    """Reads and writes every intel table.

    Migrates on construction by default. Pass ``migrate=False`` when the
    warehouse was opened read-only: DuckDB allows a single writer, this project
    runs long ingests and simulations against the same file, and a dossier that
    cannot be produced because a backtest holds the write lock is a dossier that
    is useless at exactly the moment it is wanted. Use :meth:`open_reader`, which
    also tells you whether the tables exist at all.
    """

    def __init__(self, warehouse: Warehouse, *, migrate: bool = True) -> None:
        self.wh = warehouse
        if migrate:
            self.migrate()

    @classmethod
    def open_reader(cls, warehouse: Warehouse) -> tuple["IntelStore", bool]:
        """``(store, tables_exist)`` without attempting any DDL.

        The boolean is returned rather than raised because "intel has not been
        collected yet" is a state a dossier must be able to *report*, not one it
        should die on. A section that says "no intel collected; run
        `fpl intel collect`" is useful; a traceback is not.
        """
        store = cls(warehouse, migrate=False)
        return store, store.tables_exist()

    def tables_exist(self) -> bool:
        found = self.wh.sql(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN "
            "(" + ", ".join("?" * len(TABLES)) + ")",
            list(TABLES),
        )
        return len(found) == len(TABLES)

    # -- schema --------------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply unapplied intel migrations. Idempotent.

        Recorded with the file's sha256, so a migration edited after the fact is
        detectable rather than silently divergent between a developer's
        warehouse and one rebuilt from the archive.
        """
        self.wh.sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                version     VARCHAR PRIMARY KEY,
                applied_utc TIMESTAMPTZ NOT NULL,
                sha256      VARCHAR NOT NULL
            )
            """
        )
        applied = set(
            self.wh.sql("SELECT version FROM schema_migration")["version"].astype(str)
        )
        run: list[str] = []
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = f"{VERSION_PREFIX}{path.stem}"
            if version in applied:
                continue
            body = path.read_text()
            self.wh.sql(body)
            self.wh.sql(
                "INSERT INTO schema_migration VALUES (?, ?, ?)",
                [version, dt.datetime.now(UTC), hashlib.sha256(body.encode()).hexdigest()],
            )
            run.append(version)
        return run

    # -- writes --------------------------------------------------------------

    def put_items(self, items: list[IntelItem]) -> int:
        """Insert news items, skipping ids already present.

        ``item_id`` is a content hash, so re-running the collector over the same
        archived bodies inserts nothing. That is what makes the raw archive
        replayable without duplicating history.
        """
        if not items:
            return 0
        rows = [
            (
                i.item_id, i.published_at, i.observed_at, i.season, str(i.kind),
                _opt_int(i.player_code), _opt_int(i.team_code), i.headline, i.body,
                i.source, i.source_url, _opt_int(i.http_status), float(i.confidence),
            )
            for i in items
        ]
        return self._insert_new("intel_item", "item_id", rows, ncols=13)

    def put_duties(self, duties: list[SetPieceDuty]) -> int:
        if not duties:
            return 0
        before = self._count("set_piece_duty")
        for d in duties:
            self.wh.sql(
                """
                INSERT INTO set_piece_duty (season, code, duty, ord, note, team_code,
                                            source, as_of)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM set_piece_duty
                    WHERE season = ? AND code = ? AND duty = ? AND as_of = ?
                )
                """,
                [
                    d.season, int(d.code), str(d.duty), _opt_int(d.ord), d.note,
                    _opt_int(d.team_code), d.source, d.as_of,
                    d.season, int(d.code), str(d.duty), d.as_of,
                ],
            )
        return self._count("set_piece_duty") - before

    def put_changes(self, changes: list[DutyChange]) -> int:
        if not changes:
            return 0
        rows = [
            (
                c.change_id, c.season, int(c.code), _opt_int(c.team_code), str(c.duty),
                _opt_int(c.ord_before), _opt_int(c.ord_after), c.prior_as_of,
                c.detected_at, float(c.delta_goals_per_game), c.headline,
            )
            for c in changes
        ]
        return self._insert_new("set_piece_change", "change_id", rows, ncols=11)

    def put_oop(self, signals: list[OopSignal]) -> int:
        """Insert mismatch signals, skipping ones that restate the latest verdict.

        The detector runs against the whole population every collection, so
        without this a nightly job would add 36 identical rows a day forever.
        A row is written only when the verdict actually moved, which makes the
        table a history of *changes in role* rather than a log of cron runs --
        and keeps ``as_of`` meaningful, since it then marks when we first
        concluded this rather than when we last re-checked.
        """
        if not signals:
            return 0
        before = self._count("oop_signal")
        latest = self.wh.sql(
            """
            SELECT season, code, plays_like, round(score, 4) AS score FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code
                                             ORDER BY as_of DESC) rn
                FROM oop_signal
            ) WHERE rn = 1
            """
        )
        seen = {
            (str(r["season"]), int(r["code"])): (int(r["plays_like"]), float(r["score"]))
            for _, r in latest.iterrows()
        }
        for s in signals:
            if seen.get((s.season, int(s.code))) == (int(s.plays_like), round(float(s.score), 4)):
                continue
            self.wh.sql(
                """
                INSERT INTO oop_signal (season, code, fpl_position, plays_like, score,
                                        evidence, as_of)
                SELECT ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM oop_signal WHERE season = ? AND code = ? AND as_of = ?
                )
                """,
                [
                    s.season, int(s.code), int(s.fpl_position), int(s.plays_like),
                    float(s.score), s.evidence, s.as_of,
                    s.season, int(s.code), s.as_of,
                ],
            )
        return self._count("oop_signal") - before

    def put_formations(self, obs: list[FormationObservation]) -> int:
        if not obs:
            return 0
        before = self._count("formation_observation")
        for o in obs:
            self.wh.sql(
                """
                INSERT INTO formation_observation (season, team_code, fixture_id, gw,
                                                   shape, n_def, n_mid, n_fwd, as_of)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM formation_observation
                    WHERE season = ? AND team_code = ? AND fixture_id = ? AND as_of = ?
                )
                """,
                [
                    o.season, int(o.team_code), int(o.fixture_id), _opt_int(o.gw),
                    o.shape, int(o.n_def), int(o.n_mid), int(o.n_fwd), o.as_of,
                    o.season, int(o.team_code), int(o.fixture_id), o.as_of,
                ],
            )
        return self._count("formation_observation") - before

    def put_probes(self, probes: list[SourceProbe]) -> int:
        if not probes:
            return 0
        rows = [
            (
                p.probe_id, p.probed_at, p.source, p.url, _opt_int(p.http_status),
                _opt_int(p.robots_status), p.robots_allows, _opt_int(p.bytes),
                p.verdict, p.note,
            )
            for p in probes
        ]
        return self._insert_new("source_probe", "probe_id", rows, ncols=10)

    def _insert_new(self, table: str, key: str, rows: list[tuple], *, ncols: int) -> int:
        before = self._count(table)
        placeholders = ", ".join("?" * ncols)
        # `table` and `key` are module-local literals, never user data.
        sql = (
            f"INSERT INTO {table} SELECT {placeholders} "
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} WHERE {key} = ?)"
        )
        for row in rows:
            self.wh.sql(sql, [*row, row[0]])
        return self._count(table) - before

    def _count(self, table: str) -> int:
        return int(self.wh.sql(f"SELECT count(*) AS n FROM {table}").iloc[0]["n"])

    # -- point-in-time reads -------------------------------------------------

    def items(
        self,
        as_of: dt.datetime,
        *,
        player_code: int | None = None,
        team_code: int | None = None,
        kind: IntelKind | None = None,
        season: str | None = None,
        limit: int | None = None,
    ) -> list[IntelItem]:
        """News published at or before ``as_of``, newest first.

        The ``published_at <= ?`` predicate is the leak guard and is not
        optional: there is no code path in this class that reads ``intel_item``
        without it.
        """
        when = _require_utc(as_of, "as_of")
        clauses = ["published_at <= ?"]
        params: list[object] = [when]
        if player_code is not None:
            clauses.append("player_code = ?")
            params.append(int(player_code))
        if team_code is not None:
            clauses.append("team_code = ?")
            params.append(int(team_code))
        if kind is not None:
            clauses.append("kind = ?")
            params.append(str(kind))
        if season is not None:
            clauses.append("season = ?")
            params.append(season)
        sql = (
            f"SELECT * FROM intel_item WHERE {' AND '.join(clauses)} "
            "ORDER BY published_at DESC, item_id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"  # int-cast; never user text
        return [self._row_to_item(r) for _, r in self.wh.sql(sql, params).iterrows()]

    def duties(
        self,
        as_of: dt.datetime,
        *,
        season: str,
        code: int | None = None,
        team_code: int | None = None,
    ) -> list[SetPieceDuty]:
        """Latest known duty per (player, duty) at ``as_of``.

        One row per duty per player, taking the most recent observation that was
        public by then -- the standard as-of read, expressed here rather than
        through ``Snapshot.table`` because these tables are intel-owned and are
        deliberately absent from ``store.PIT_KEYS``.
        """
        when = _require_utc(as_of, "as_of")
        clauses = ["as_of <= ?", "season = ?"]
        params: list[object] = [when, season]
        if code is not None:
            clauses.append("code = ?")
            params.append(int(code))
        if team_code is not None:
            clauses.append("team_code = ?")
            params.append(int(team_code))
        df = self.wh.sql(
            f"""
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code, duty
                                             ORDER BY as_of DESC) rn
                FROM set_piece_duty WHERE {' AND '.join(clauses)}
            ) WHERE rn = 1 ORDER BY duty, ord NULLS LAST, code
            """,
            params,
        )
        return [
            SetPieceDuty(
                season=str(r["season"]), code=int(r["code"]), duty=Duty(str(r["duty"])),
                ord=_opt_int(r["ord"]), as_of=_ts(r["as_of"]), source=str(r["source"]),
                team_code=_opt_int(r["team_code"]), note=_opt_str(r["note"]),
            )
            for _, r in df.iterrows()
        ]

    def changes(
        self,
        as_of: dt.datetime,
        *,
        season: str | None = None,
        code: int | None = None,
        since: dt.datetime | None = None,
        limit: int | None = None,
    ) -> list[DutyChange]:
        """Duty changes detected at or before ``as_of``, newest first."""
        when = _require_utc(as_of, "as_of")
        clauses = ["detected_at <= ?"]
        params: list[object] = [when]
        if season is not None:
            clauses.append("season = ?")
            params.append(season)
        if code is not None:
            clauses.append("code = ?")
            params.append(int(code))
        if since is not None:
            clauses.append("detected_at >= ?")
            params.append(_require_utc(since, "since"))
        sql = (
            f"SELECT * FROM set_piece_change WHERE {' AND '.join(clauses)} "
            "ORDER BY detected_at DESC, change_id"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return [
            DutyChange(
                change_id=str(r["change_id"]), season=str(r["season"]),
                code=int(r["code"]), duty=Duty(str(r["duty"])),
                ord_before=_opt_int(r["ord_before"]), ord_after=_opt_int(r["ord_after"]),
                prior_as_of=_ts(r["prior_as_of"]), detected_at=_ts(r["detected_at"]),
                delta_goals_per_game=float(r["delta_goals_per_game"]),
                headline=str(r["headline"]), team_code=_opt_int(r["team_code"]),
            )
            for _, r in self.wh.sql(sql, params).iterrows()
        ]

    def oop(
        self, as_of: dt.datetime, *, season: str, code: int | None = None,
        min_score: float = 0.0,
    ) -> list[OopSignal]:
        when = _require_utc(as_of, "as_of")
        clauses = ["as_of <= ?", "season = ?", "score >= ?"]
        params: list[object] = [when, season, float(min_score)]
        if code is not None:
            clauses.append("code = ?")
            params.append(int(code))
        df = self.wh.sql(
            f"""
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY season, code
                                             ORDER BY as_of DESC) rn
                FROM oop_signal WHERE {' AND '.join(clauses)}
            ) WHERE rn = 1 ORDER BY score DESC
            """,
            params,
        )
        return [
            OopSignal(
                season=str(r["season"]), code=int(r["code"]),
                fpl_position=int(r["fpl_position"]), plays_like=int(r["plays_like"]),
                score=float(r["score"]), evidence=str(r["evidence"]), as_of=_ts(r["as_of"]),
            )
            for _, r in df.iterrows()
        ]

    def formations(
        self, as_of: dt.datetime, *, season: str, team_code: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        when = _require_utc(as_of, "as_of")
        clauses = ["as_of <= ?", "season = ?"]
        params: list[object] = [when, season]
        if team_code is not None:
            clauses.append("team_code = ?")
            params.append(int(team_code))
        sql = (
            f"SELECT * FROM formation_observation WHERE {' AND '.join(clauses)} "
            "ORDER BY gw DESC, fixture_id DESC"
        )
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.wh.sql(sql, params)

    def probes(self, *, source: str | None = None, limit: int = 50) -> list[SourceProbe]:
        """Source probes. Deliberately NOT as-of filtered.

        A probe is a fact about our pipeline's access to a website, not a fact
        about the football season, and a dossier should always be able to say
        "premierinjuries returned 403 when we last asked" regardless of which
        deadline is being reconstructed.
        """
        clause = "WHERE source = ?" if source else ""
        params = [source] if source else []
        df = self.wh.sql(
            f"SELECT * FROM source_probe {clause} ORDER BY probed_at DESC "
            f"LIMIT {int(limit)}",
            params,
        )
        return [
            SourceProbe(
                probe_id=str(r["probe_id"]), probed_at=_ts(r["probed_at"]),
                source=str(r["source"]), url=str(r["url"]), verdict=str(r["verdict"]),
                http_status=_opt_int(r["http_status"]),
                robots_status=_opt_int(r["robots_status"]),
                robots_allows=(
                    None if pd.isna(r["robots_allows"]) else bool(r["robots_allows"])
                ),
                bytes=_opt_int(r["bytes"]), note=_opt_str(r["note"]),
            )
            for _, r in df.iterrows()
        ]

    def counts(self) -> dict[str, int]:
        """Row counts per intel table, for `fpl intel status`."""
        return {t: self._count(t) for t in TABLES}

    @staticmethod
    def _row_to_item(row: pd.Series) -> IntelItem:
        return IntelItem(
            item_id=str(row["item_id"]),
            published_at=_ts(row["published_at"]),
            observed_at=_ts(row["observed_at"]),
            kind=IntelKind(str(row["kind"])),
            headline=str(row["headline"]),
            source=str(row["source"]),
            season=_opt_str(row["season"]),
            player_code=_opt_int(row["player_code"]),
            team_code=_opt_int(row["team_code"]),
            body=_opt_str(row["body"]),
            source_url=_opt_str(row["source_url"]),
            http_status=_opt_int(row["http_status"]),
            confidence=_opt_float(row["confidence"]) or 0.0,
        )
