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

#: The columns of ``claim_outcome`` that ARE the verdict. ``resolved_utc`` is
#: not one of them: it is the pointer to when the verdict was reached, and a
#: run that changes nothing else must not move it. Everything here is compared
#: with IS DISTINCT FROM, because a NULL ``hit`` becoming a real one is the
#: single most important revision this package can record.
_VERDICT_COLS = (
    "player_points", "benchmark", "benchmark_points", "hit", "unscoreable",
)


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

    "Moved" means any of the verdict columns in :data:`_VERDICT_COLS` differs,
    not just ``hit``. A row whose ``benchmark_points`` shifted while ``hit``
    happened to stay True *did* change, and calling it unchanged would leave it
    holding a ``resolved_utc`` from a run that computed a different number.

    This count is a report, not a record: it is printed and discarded. The
    durable trace is ``claim_outcome_revision``, written by
    :meth:`ContentStore.insert_outcomes`. Two verdicts flipping in opposite
    directions between runs leave every aggregate byte-identical, so a count
    that lives only in stdout cannot be checked afterwards by anyone.
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
        # Append-only log of superseded settlements. Inline here rather than in
        # a migrations/*.sql file for the same reason schema_migration is: it
        # is machinery this class cannot function without, so it must exist
        # before the migration runner has decided anything.
        #
        # claim_outcome holds the CURRENT verdict and is rewritten in place. On
        # its own that makes a revision undetectable after the fact: two claims
        # flipping in opposite directions between runs leave the append-only
        # creator_score aggregate byte-identical, and the only report of the
        # flips is a number printed to stdout by a process that has exited. One
        # row per superseded verdict, keyed by the resolved_utc it carried,
        # makes the sequence recoverable: the prior verdicts are here and the
        # current one is in claim_outcome.
        self.wh.sql(
            """
            CREATE TABLE IF NOT EXISTS claim_outcome_revision (
                claim_id            VARCHAR NOT NULL,
                -- The resolved_utc the superseded row carried: which run
                -- produced the verdict being replaced.
                prior_resolved_utc  TIMESTAMPTZ NOT NULL,
                -- The resolved_utc of the run that replaced it.
                superseded_utc      TIMESTAMPTZ NOT NULL,
                prior_hit           BOOLEAN,
                prior_unscoreable   VARCHAR,
                prior_player_points DOUBLE,
                prior_benchmark     VARCHAR,
                prior_benchmark_points DOUBLE,
                -- Denormalised so a flip is visible without a join back to a
                -- claim_outcome row that may itself have been superseded since.
                new_hit             BOOLEAN,
                new_unscoreable     VARCHAR,
                PRIMARY KEY (claim_id, prior_resolved_utc)
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

        On rewriting history. claim_outcome holds the current verdict, not a log
        of past ones. An earlier version of this docstring argued that was
        harmless because an outcome is *derived* rather than observed -- a pure
        function of content_claim (immutable), the gameweek calendar and
        fact_player_fixture (append-only, PIT-keyed) -- so every input to a
        superseded verdict was still in the warehouse and any past settlement
        could be recomputed. That argument was wrong in three ways, and this
        method now answers each of them:

        1. It restamped ``resolved_utc`` on every row the run touched, including
           rows whose verdict had not moved. That column was the only pointer
           back to the state that produced the verdict, and restamping it
           destroyed the pointer in the same write that superseded the row. A
           row whose verdict is unchanged now KEEPS its original
           ``resolved_utc``: it still names the run that reached that verdict,
           which is the whole job of the column. Only a row that actually
           changed is restamped, and only then because the new stamp is true.
        2. The list of inputs was incomplete. ``dim_player.position`` is a
           fourth input and it is not append-only: the benchmark is the
           positional median (see
           :meth:`fpl_edge.ingest.content.scoring.ResultIndex.benchmark`), so position
           selects which bucket a claim is judged against. dim_player is
           re-ingested daily and FPL reclassifies players mid-season, so a
           reclassification silently moves the benchmark and can flip a verdict
           with no other input having changed. ``ResultIndex`` now resolves
           position point-in-time, at the deadline of the gameweek being
           scored, when it is given the calendar to do so -- which restores the
           "recomputable from stored inputs" property rather than merely
           documenting its absence.
        3. ``OutcomeWrite.revised`` was printed and thrown away. Two verdicts
           flipping in opposite directions leave the append-only creator_score
           aggregate byte-identical, so nothing in the warehouse recorded that
           anything moved. Every superseded verdict is now appended to
           ``claim_outcome_revision`` before it is overwritten, inside the same
           transaction, so the flip is still there to be found after the run
           that caused it has exited.

        What is deliberately NOT done: claim_outcome is not turned into an
        append-only (claim_id, resolved_utc) table. The current verdict is what
        every reader wants and a two-row-per-claim table would make the
        sanctioned read a window function that some caller eventually gets
        wrong. The revision log carries the history instead, and it is written
        on the same path, in the same transaction, so it cannot drift.
        """
        if frame.empty:
            return OutcomeWrite(0, 0, 0)
        frame = frame.drop_duplicates(subset=["claim_id"])
        cols = ", ".join(frame.columns)
        differs = " OR ".join(
            f"t.{c} IS DISTINCT FROM i.{c}" for c in _VERDICT_COLS
        )
        self.wh._con.register("_incoming_outcomes", frame)
        try:
            matched = int(self.wh.sql(
                "SELECT count(*) c FROM claim_outcome t "
                "JOIN _incoming_outcomes i USING (claim_id)"
            ).iloc[0]["c"])
            revised = int(self.wh.sql(
                f"SELECT count(*) c FROM claim_outcome t "
                f"JOIN _incoming_outcomes i USING (claim_id) WHERE {differs}"
            ).iloc[0]["c"])
            self.wh.sql("BEGIN TRANSACTION")
            try:
                # Log first: after the DELETE the prior verdict is gone.
                self.wh.sql(
                    f"INSERT INTO claim_outcome_revision "
                    f"(claim_id, prior_resolved_utc, superseded_utc, prior_hit, "
                    f" prior_unscoreable, prior_player_points, prior_benchmark, "
                    f" prior_benchmark_points, new_hit, new_unscoreable) "
                    f"SELECT t.claim_id, t.resolved_utc, i.resolved_utc, t.hit, "
                    f"       t.unscoreable, t.player_points, t.benchmark, "
                    f"       t.benchmark_points, i.hit, i.unscoreable "
                    f"FROM claim_outcome t JOIN _incoming_outcomes i USING (claim_id) "
                    f"WHERE ({differs}) AND NOT EXISTS ("
                    f"  SELECT 1 FROM claim_outcome_revision r "
                    f"  WHERE r.claim_id = t.claim_id "
                    f"    AND r.prior_resolved_utc = t.resolved_utc)"
                )
                # An unchanged row keeps the resolved_utc it already had. The
                # incoming frame stamps every row with this run's clock, so the
                # old timestamp has to be carried across before the delete
                # below destroys the row holding it. Materialised because the
                # join partner is about to be deleted.
                self.wh.sql(
                    f"CREATE OR REPLACE TEMP TABLE _outcomes_to_write AS "
                    f"SELECT i.* REPLACE ("
                    f"  CASE WHEN t.claim_id IS NOT NULL AND NOT ({differs}) "
                    f"       THEN t.resolved_utc ELSE i.resolved_utc END "
                    f"  AS resolved_utc) "
                    f"FROM _incoming_outcomes i "
                    f"LEFT JOIN claim_outcome t ON t.claim_id = i.claim_id"
                )
                self.wh.sql(
                    "DELETE FROM claim_outcome WHERE claim_id IN "
                    "(SELECT claim_id FROM _outcomes_to_write)"
                )
                self.wh.sql(
                    f"INSERT INTO claim_outcome ({cols}) "
                    f"SELECT {cols} FROM _outcomes_to_write"
                )
                self.wh.sql("COMMIT")
            except Exception:
                # A half-applied rescore would leave settled claims missing
                # entirely, which reads downstream as "never claimed".
                self.wh.sql("ROLLBACK")
                raise
        finally:
            try:
                self.wh.sql("DROP TABLE IF EXISTS _outcomes_to_write")
            except Exception:  # noqa: BLE001, S110
                # Scratch space. Failing to tidy it must not replace the real
                # exception with a misleading one; CREATE OR REPLACE on the
                # next call clears it regardless.
                pass
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
        frame = self.wh.sql(
            "SELECT * FROM content_claim WHERE " + " AND ".join(where)
            + " ORDER BY published_at, claim_id",
            params,
        )
        return self._drop_discarded(frame)

    def _drop_discarded(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Hide the items the owner has revoked.

        The claim itself stays: an utterance was made and cannot be un-made,
        which is the property the whole track record rests on. What the owner
        revokes is OUR decision to carry it -- a link pasted by mistake, or
        something that turned out to have nothing to do with FPL. So this is a
        read-side filter over a flag, never a delete, and `restore` is a real
        inverse.

        Every honest read path owes this filter. It lives here, on the
        sanctioned path, so a caller cannot forget it by accident.
        """
        if frame is None or frame.empty or "item_id" not in frame.columns:
            return frame
        try:
            from fpl_edge.interfaces.creators import (  # noqa: PLC0415
                discarded_item_ids,
            )
            hidden = discarded_item_ids(self.wh)
        except Exception:  # noqa: BLE001 - an unreadable ledger hides nothing
            return frame
        if not hidden:
            return frame
        return frame[~frame["item_id"].isin(hidden)]

    def items_visible_at(self, as_of: dt.datetime) -> pd.DataFrame:
        as_of = _require_utc(as_of, "as_of")
        frame = self.wh.sql(
            "SELECT * FROM content_item WHERE published_at < ? ORDER BY published_at",
            [as_of],
        )
        return self._drop_discarded(frame)

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

    def outcome_revisions(self, claim_id: str | None = None) -> pd.DataFrame:
        """Superseded settlements, oldest first. Append-only; never rewritten.

        The answer to "did this verdict always say that?". A claim with rows
        here changed its mind at least once, and the sequence of
        ``prior_hit`` -> ``new_hit`` shows in which direction. Compensating
        flips -- one claim True->False while another goes False->True in the
        same run -- leave every aggregate unchanged and are visible only here.
        """
        where = "" if claim_id is None else "WHERE claim_id = ?"
        params = [] if claim_id is None else [claim_id]
        return self.wh.sql(
            f"SELECT * FROM claim_outcome_revision {where} "
            f"ORDER BY superseded_utc, claim_id",
            params,
        )

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in ("content_source", "content_item", "content_claim",
                      "claim_outcome", "claim_outcome_revision", "creator_score"):
            out[table] = int(self.wh.sql(f"SELECT count(*) c FROM {table}").iloc[0]["c"])
        return out
