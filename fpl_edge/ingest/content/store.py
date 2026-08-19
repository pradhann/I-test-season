"""Persistence, and the one read path that is safe to use.

The migration runner mirrors :meth:`fpl_edge.interfaces.registry.IdeaRegistry.migrate`
exactly: idempotent, recorded in ``schema_migration`` with the file's sha256, and
applied to the same DuckDB file the store team owns. ``fpl_edge/store/schema.sql``
is not touched.

The read path deserves more attention than the writes.

:meth:`ContentStore.claims_visible_at` is the *only* sanctioned way to read
claims for a decision. Every other method on this class is for auditing,
backfilling or reporting, and the class deliberately does not expose a
convenience "all claims" reader that could be dropped into a model by mistake.
The reason is the same one the Snapshot class exists for over in the warehouse:
if there are two ways to read and one of them is safe, code review has to catch
the difference every single time, forever. There is one way here, it takes an
``as_of``, and it filters ``published_at < as_of``.

Strictly less-than, not less-than-or-equal. A claim published at the exact
instant of the deadline could not have been acted on -- the deadline is the
moment the team locks -- and the boundary case is far more likely to be a
timestamp rounded to the minute than a genuinely simultaneous publication.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path

import pandas as pd

from fpl_edge.ingest.content.models import Claim, ContentItem
from fpl_edge.ingest.content.sources import Source
from fpl_edge.store import Warehouse

UTC = dt.UTC

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_ITEM_COLS = (
    "item_id", "source_key", "creator", "kind", "title", "url",
    "published_at", "fetched_at", "text_source", "text", "text_sha256",
)
_CLAIM_COLS = (
    "claim_id", "item_id", "creator", "source_key", "player_code", "player_name",
    "surface_form", "action", "season", "gameweek", "confidence", "rationale",
    "source_url", "published_at", "gw_inferred", "extractor",
)


def _require_utc(ts: dt.datetime, label: str) -> dt.datetime:
    if ts.tzinfo is None:
        raise ValueError(f"{label} must be timezone-aware UTC, got naive {ts!r}")
    return ts.astimezone(UTC)


class ContentStore:
    def __init__(self, warehouse: Warehouse) -> None:
        self.wh = warehouse
        self.migrate()

    # -- schema --------------------------------------------------------------

    def migrate(self) -> list[str]:
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

    def upsert_sources(self, sources: tuple[Source, ...]) -> int:
        rows = [
            {
                "source_key": s.key, "creator": s.creator, "kind": str(s.kind),
                "url": s.url, "policy": str(s.policy), "note": s.note or None,
                "last_probe_utc": None, "last_http_status": None,
                "last_items": None, "last_error": None,
            }
            for s in sources
        ]
        if not rows:
            return 0
        frame = pd.DataFrame(rows)
        self.wh.sql("CREATE TEMP TABLE IF NOT EXISTS _src AS SELECT * FROM content_source LIMIT 0")
        self.wh._con.register("_incoming_src", frame)
        self.wh.sql(
            "INSERT INTO content_source SELECT * FROM _incoming_src i "
            "WHERE NOT EXISTS (SELECT 1 FROM content_source t "
            "                  WHERE t.source_key = i.source_key)"
        )
        self.wh._con.unregister("_incoming_src")
        return len(rows)

    def record_probe(
        self, source_key: str, *, status: int | None, items: int,
        error: str | None, at: dt.datetime,
    ) -> None:
        self.wh.sql(
            "UPDATE content_source SET last_probe_utc = ?, last_http_status = ?, "
            "last_items = ?, last_error = ? WHERE source_key = ?",
            [_require_utc(at, "probe at"), status, items, error, source_key],
        )

    def insert_items(self, items: list[ContentItem]) -> int:
        if not items:
            return 0
        frame = pd.DataFrame(
            [
                {
                    "item_id": i.item_id, "source_key": i.source_key, "creator": i.creator,
                    "kind": i.kind, "title": i.title, "url": i.url,
                    "published_at": i.published_at, "fetched_at": i.fetched_at,
                    "text_source": i.text_source, "text": i.text,
                    "text_sha256": hashlib.sha256(i.text.encode("utf-8")).hexdigest(),
                }
                for i in items
            ],
            columns=list(_ITEM_COLS),
        ).drop_duplicates(subset=["item_id"])
        return self._insert_new(frame, "content_item", "item_id")

    def insert_claims(self, claims: list[Claim]) -> int:
        if not claims:
            return 0
        frame = pd.DataFrame(
            [
                {
                    "claim_id": c.claim_id, "item_id": c.item_id, "creator": c.creator,
                    "source_key": c.source_key, "player_code": int(c.player_code),
                    "player_name": c.player_name, "surface_form": c.surface_form,
                    "action": str(c.action), "season": c.season,
                    "gameweek": int(c.gameweek), "confidence": float(c.confidence),
                    "rationale": c.rationale, "source_url": c.source_url,
                    "published_at": c.published_at, "gw_inferred": bool(c.gw_inferred),
                    "extractor": getattr(c, "extractor", "cue"),
                }
                for c in claims
            ],
            columns=list(_CLAIM_COLS),
        ).drop_duplicates(subset=["claim_id"])
        return self._insert_new(frame, "content_claim", "claim_id")

    def insert_outcomes(self, frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        return self._insert_new(frame, "claim_outcome", "claim_id")

    def insert_scores(self, frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        self.wh._con.register("_incoming_scores", frame)
        before = self.wh.sql("SELECT count(*) c FROM creator_score").iloc[0]["c"]
        self.wh.sql(
            "INSERT INTO creator_score SELECT * FROM _incoming_scores i WHERE NOT EXISTS "
            "(SELECT 1 FROM creator_score t WHERE t.creator = i.creator "
            " AND t.scope = i.scope AND t.as_of = i.as_of)"
        )
        after = self.wh.sql("SELECT count(*) c FROM creator_score").iloc[0]["c"]
        self.wh._con.unregister("_incoming_scores")
        return int(after - before)

    def _insert_new(self, frame: pd.DataFrame, table: str, key: str) -> int:
        self.wh._con.register("_incoming_rows", frame)
        before = self.wh.sql(f"SELECT count(*) c FROM {table}").iloc[0]["c"]
        cols = ", ".join(frame.columns)
        self.wh.sql(
            f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _incoming_rows i "
            f"WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.{key} = i.{key})"
        )
        after = self.wh.sql(f"SELECT count(*) c FROM {table}").iloc[0]["c"]
        self.wh._con.unregister("_incoming_rows")
        return int(after - before)

    # -- the sanctioned read -------------------------------------------------

    def claims_visible_at(
        self,
        as_of: dt.datetime,
        *,
        season: str | None = None,
        gameweek: int | None = None,
    ) -> pd.DataFrame:
        """Claims a manager could have read before ``as_of``.

        Pass the gameweek deadline. Anything published at or after that instant
        is invisible by construction, which is the property that stops a
        creator's post-match "I told you so" episode from being backfilled into
        the model as foresight.
        """
        as_of = _require_utc(as_of, "as_of")
        where = ["published_at < ?"]
        params: list[object] = [as_of]
        if season is not None:
            where.append("season = ?")
            params.append(season)
        if gameweek is not None:
            where.append("gameweek = ?")
            params.append(int(gameweek))
        return self.wh.sql(
            "SELECT * FROM content_claim WHERE " + " AND ".join(where)
            + " ORDER BY published_at, claim_id",
            params,
        )

    def items_visible_at(self, as_of: dt.datetime) -> pd.DataFrame:
        as_of = _require_utc(as_of, "as_of")
        return self.wh.sql(
            "SELECT * FROM content_item WHERE published_at < ? ORDER BY published_at",
            [as_of],
        )

    # -- audit / reporting (not for model input) -----------------------------

    def all_claims_for_scoring(self) -> pd.DataFrame:
        """Every claim, unfiltered. For outcome resolution and reporting ONLY.

        Legitimate because scoring runs *after* the gameweek finalised and asks
        "was this claim, made at time T, right about the gameweek it named?".
        The claim's own ``published_at`` is still checked against its gameweek's
        deadline inside :mod:`fpl_edge.ingest.content.scoring`, so a claim made
        too late is not merely excluded from the model -- it is refused a hit.
        """
        return self.wh.sql("SELECT * FROM content_claim ORDER BY published_at, claim_id")

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("content_source", "content_item", "content_claim",
                      "claim_outcome", "creator_score"):
            out[table] = int(self.wh.sql(f"SELECT count(*) c FROM {table}").iloc[0]["c"])
        return out
