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
from typing import NamedTuple

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

#: Columns of ``content_source`` that describe the source itself, as opposed to
#: the runtime state of the last probe against it.
_SOURCE_DEF_COLS = ("creator", "kind", "url", "policy", "note")


class SourceWrite(NamedTuple):
    """Registry rows created versus definitions brought up to date."""

    inserted: int
    updated: int

    @property
    def total(self) -> int:
        return self.inserted + self.updated


class OutcomeWrite(NamedTuple):
    """Settlement rows written, split by whether the verdict actually moved.

    ``revised`` is the number the run should be judged on: it is how many
    claims stopped being unscoreable, or changed verdict, because the world
    settled since the last run. A rescore that revises nothing and a rescore
    that never ran look identical without this count.
    """

    inserted: int
    revised: int
    unchanged: int

    @property
    def total(self) -> int:
        return self.inserted + self.revised + self.unchanged


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

    def upsert_sources(self, sources: tuple[Source, ...]) -> SourceWrite:
        """Bring ``content_source`` into line with the registry in sources.py.

        A genuine upsert, not an insert-once. content_001_claims.sql says this
        table exists so "a source that is later dropped or whose access policy
        changes leaves a trace rather than vanishing", and an insert-once made
        that false in the direction that matters most: flipping a source to
        FORBIDDEN in sources.py left the DB claiming it was still OPEN, so the
        row recorded the opposite of the decision it was supposed to preserve.

        Only the *definition* columns are written. The probe columns
        (last_probe_utc, last_http_status, last_items, last_error) are runtime
        state owned by :meth:`record_probe`; a definition upsert has no fresher
        knowledge of them than the DB already holds, so blanking them here
        would destroy the record of the last real fetch every time the
        pipeline started. New rows get NULLs there because nothing has probed
        them yet, which is true.
        """
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
            return SourceWrite(0, 0)
        frame = pd.DataFrame(rows).drop_duplicates(subset=["source_key"])
        self.wh._con.register("_incoming_src", frame)
        try:
            assignments = ", ".join(f"{c} = i.{c}" for c in _SOURCE_DEF_COLS)
            differs = " OR ".join(f"t.{c} IS DISTINCT FROM i.{c}" for c in _SOURCE_DEF_COLS)
            updated = int(self.wh.sql(
                f"SELECT count(*) c FROM content_source t JOIN _incoming_src i "
                f"USING (source_key) WHERE {differs}"
            ).iloc[0]["c"])
            self.wh.sql(
                f"UPDATE content_source t SET {assignments} FROM _incoming_src i "
                f"WHERE t.source_key = i.source_key AND ({differs})"
            )
            inserted = self._insert_new(frame, "content_source", "source_key")
        finally:
            self.wh._con.unregister("_incoming_src")
        return SourceWrite(inserted, updated)

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

    def insert_outcomes(self, frame: pd.DataFrame) -> OutcomeWrite:
        """Write settlements, revising any verdict this run has superseded.

        Deliberately NOT :meth:`_insert_new`. An outcome is the one thing in
        this package that is not immutable: a claim about a gameweek that has
        not kicked off is recorded ``unscoreable='gameweek_not_played'``, and
        the whole point of running `score` again after the gameweek finalises
        is that the row must become a real hit or miss. Insert-once froze the
        first verdict forever -- every claim in the warehouse sat at
        ``hit IS NULL`` while the scoring run reported real hits in memory and
        threw them away, and the creator track record read "no resolved claims
        yet" permanently.

        The choice: upsert keyed on ``claim_id``, scoped to exactly the claims
        in this frame. Not a blanket DELETE of the (season, gameweek) being
        rescored, because the frame is authoritative only for the claims it
        contains -- a narrowed or partial run would silently erase settlements
        for claims it never looked at, which is a worse failure than the one
        being fixed.

        On rewriting history: ``resolved_utc`` is restamped on every row this
        run touches, so a row always names the run that produced the verdict
        you are reading, rather than a stale timestamp attached to a fresher
        answer. That does mean claim_outcome holds the current verdict and not
        a log of past ones -- which is honest here only because an outcome is
        *derived*, not observed. It is a pure function of content_claim (immutable),
        the gameweek calendar, and fact_player_fixture (append-only and keyed
        by as_of). Every input to a superseded verdict is still in the
        warehouse, so any past settlement can be recomputed; nothing that was
        actually witnessed is being overwritten. If outcomes ever stop being
        reproducible from those inputs, this needs to become an append-only
        table with a (claim_id, resolved_utc) key instead.
        """
        if frame.empty:
            return OutcomeWrite(0, 0, 0)
        frame = frame.drop_duplicates(subset=["claim_id"])
        cols = ", ".join(frame.columns)
        self.wh._con.register("_incoming_outcomes", frame)
        try:
            matched = int(self.wh.sql(
                "SELECT count(*) c FROM claim_outcome t "
                "JOIN _incoming_outcomes i USING (claim_id)"
            ).iloc[0]["c"])
            revised = int(self.wh.sql(
                "SELECT count(*) c FROM claim_outcome t "
                "JOIN _incoming_outcomes i USING (claim_id) "
                "WHERE t.hit IS DISTINCT FROM i.hit "
                "   OR t.unscoreable IS DISTINCT FROM i.unscoreable"
            ).iloc[0]["c"])
            self.wh.sql("BEGIN TRANSACTION")
            try:
                self.wh.sql(
                    "DELETE FROM claim_outcome WHERE claim_id IN "
                    "(SELECT claim_id FROM _incoming_outcomes)"
                )
                self.wh.sql(
                    f"INSERT INTO claim_outcome ({cols}) "
                    f"SELECT {cols} FROM _incoming_outcomes"
                )
                self.wh.sql("COMMIT")
            except Exception:
                # A half-applied rescore would leave settled claims missing
                # entirely, which reads downstream as "never claimed".
                self.wh.sql("ROLLBACK")
                raise
        finally:
            self.wh._con.unregister("_incoming_outcomes")
        return OutcomeWrite(
            inserted=len(frame) - matched,
            revised=revised,
            unchanged=matched - revised,
        )

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
        """Insert rows whose key is not present yet; never touch existing rows.

        This is the RIGHT semantics for content_item and content_claim and must
        stay that way. An item is an archived copy of what was published and a
        claim is an immutable utterance -- content_001_claims.sql calls it that,
        and the whole track record depends on a claim not being editable after
        the fact. Re-running extraction over the archive must be additive.

        It is the wrong semantics for claim_outcome, which is a verdict about a
        world that had not finished happening yet. See :meth:`insert_outcomes`.
        """
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
