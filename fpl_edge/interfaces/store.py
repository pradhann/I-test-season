"""Persistence for ideas, verdicts, context and the tracking trail.

Writes go through :meth:`Warehouse.sql`, which is the public parameterised entry
point on the shared connection. Two things follow, and both are deliberate:

* **One database file, two owners of DDL.** The migration under
  ``fpl_edge/interfaces/migrations/`` is applied here at open time and recorded
  in ``schema_migration``. The store team's ``schema.sql`` is untouched, so their
  migrations and ours never conflict, but ideas still live in the same warehouse
  as the facts they are about -- a join between an idea and a realised gameweek
  is a plain SQL join, not a cross-process reconciliation.
* **Every value is bound, never interpolated.** The raw message text is the
  single most attacker-influenced string in this codebase: it arrives from a chat
  app and is written straight to a database. It is passed as a parameter at every
  call site below. There is no f-string in this module that contains user data.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import pandas as pd

from fpl_edge.interfaces.ideas import (
    CandidateMatch,
    Comparator,
    Idea,
    IdeaContext,
    IdeaKind,
    IdeaRecord,
    IdeaStatus,
    Outcome,
    Stance,
    Verdict,
)
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, PlayerCode, Season

UTC = dt.timezone.utc

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_IDEA_COLUMNS = (
    "idea_id", "created_utc", "as_of", "source", "source_ref", "raw_text", "season",
    "kind", "subject_code", "subject_name", "comparator", "comparator_code",
    "comparator_label", "gw", "horizon_gws", "thesis", "resolution_rule",
    "parse_confidence", "status", "acted", "acted_utc", "resolved_utc", "outcome",
    "subject_points", "comparator_points",
)

_CONTEXT_COLUMNS = (
    "idea_id", "captured_utc", "position", "team_code", "price_tenths",
    "selected_by_pct", "availability", "form_points_last3", "form_percentile",
    "minutes_last3", "last_match_gw", "last_match_points", "last_match_was_home",
    "gws_since_haul", "season_ppg", "pop_home_rate", "pop_mean_gws_since_haul",
    "pop_recent_haul_rate", "pop_mean_form_pct", "pop_n",
    "supported_team_code", "is_supported_club", "pop_supported_club_rate",
    "pop_universe_n",
)


def _opt_int(value: object) -> int | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):  # type: ignore[arg-type]
        return None
    return int(value)  # type: ignore[arg-type]


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    if pd.isna(value):  # type: ignore[arg-type]
        return None
    return float(value)  # type: ignore[arg-type]


def _opt_str(value: object) -> str | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):  # type: ignore[arg-type]
        return None
    return str(value)


def _opt_ts(value: object) -> dt.datetime | None:
    if value is None or pd.isna(value):  # type: ignore[arg-type]
        return None
    ts = pd.Timestamp(value)  # type: ignore[arg-type]
    if ts.tzinfo is None:
        ts = ts.tz_localize(UTC)
    return ts.to_pydatetime().astimezone(UTC)


class IdeaRegistry:
    """The thesis registry. Owns all reads and writes of idea state."""

    def __init__(self, warehouse: Warehouse) -> None:
        self.wh = warehouse
        self.migrate()

    # -- schema --------------------------------------------------------------

    def migrate(self) -> list[str]:
        """Apply any unapplied interface migrations. Idempotent.

        Recorded with the file's sha256 so an edited migration is detectable
        rather than silently divergent between the developer's warehouse and a
        rebuilt one.
        """
        self.wh.sql(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
                version VARCHAR PRIMARY KEY,
                applied_utc TIMESTAMPTZ NOT NULL,
                sha256 VARCHAR NOT NULL
            )
            """
        )
        applied: set[str] = set(
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
                [
                    path.stem,
                    dt.datetime.now(UTC),
                    hashlib.sha256(body.encode("utf-8")).hexdigest(),
                ],
            )
            run.append(path.stem)
        return run

    # -- writes --------------------------------------------------------------

    def insert_idea(self, idea: Idea) -> bool:
        """Write the idea. Returns False if this exact id already exists.

        Called *before* the verdict is computed. If the model is down, times out
        or throws, the thought is still on the record with its thesis and its
        timestamp -- which is the property that makes the registry trustworthy as
        a record of what the user believed.
        """
        if self.get(idea.idea_id) is not None:
            return False
        self.wh.sql(
            f"INSERT INTO idea ({', '.join(_IDEA_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_IDEA_COLUMNS))})",
            [
                idea.idea_id, idea.created_utc, idea.as_of, idea.source, idea.source_ref,
                idea.raw_text, str(idea.season), str(idea.kind),
                _opt_int(idea.subject_code), idea.subject_name, str(idea.comparator),
                _opt_int(idea.comparator_code), idea.comparator_label, int(idea.gw),
                int(idea.horizon_gws), idea.thesis, idea.resolution_rule,
                float(idea.parse_confidence), str(idea.status), bool(idea.acted),
                idea.acted_utc, idea.resolved_utc,
                str(idea.outcome) if idea.outcome else None,
                idea.subject_points, idea.comparator_points,
            ],
        )
        return True

    def insert_verdict(self, verdict: Verdict) -> None:
        self.wh.sql(
            """
            INSERT INTO idea_verdict (verdict_id, idea_id, issued_utc, provider,
                                      provider_version, stance, p_thesis_true,
                                      confidence, rationale, degraded, latency_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                verdict.verdict_id, verdict.idea_id, verdict.issued_utc, verdict.provider,
                verdict.provider_version, str(verdict.stance), float(verdict.p_thesis_true),
                verdict.confidence, verdict.rationale, bool(verdict.degraded),
                float(verdict.latency_ms),
            ],
        )

    def insert_context(self, ctx: IdeaContext) -> None:
        self.wh.sql(
            f"INSERT INTO idea_context ({', '.join(_CONTEXT_COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(_CONTEXT_COLUMNS))})",
            [
                ctx.idea_id, ctx.captured_utc, ctx.position, ctx.team_code,
                ctx.price_tenths, ctx.selected_by_pct, ctx.availability,
                ctx.form_points_last3, ctx.form_percentile, ctx.minutes_last3,
                ctx.last_match_gw, ctx.last_match_points, ctx.last_match_was_home,
                ctx.gws_since_haul, ctx.season_ppg, ctx.pop_home_rate,
                ctx.pop_mean_gws_since_haul, ctx.pop_recent_haul_rate,
                ctx.pop_mean_form_pct, int(ctx.pop_n),
                ctx.supported_team_code, ctx.is_supported_club,
                ctx.pop_supported_club_rate, int(ctx.pop_universe_n),
            ],
        )

    def record_observation(
        self,
        idea_id: str,
        gw: int,
        *,
        observed_utc: dt.datetime,
        subject_points: float | None,
        comparator_points: float | None,
        note: str = "",
    ) -> None:
        """Upsert one gameweek of the tracking trail."""
        self.wh.sql("DELETE FROM idea_observation WHERE idea_id = ? AND gw = ?", [idea_id, int(gw)])
        self.wh.sql(
            "INSERT INTO idea_observation VALUES (?, ?, ?, ?, ?, ?)",
            [idea_id, int(gw), observed_utc, subject_points, comparator_points, note],
        )

    def resolve(
        self,
        idea_id: str,
        *,
        outcome: Outcome,
        subject_points: float,
        comparator_points: float,
        resolved_utc: dt.datetime,
    ) -> None:
        self.wh.sql(
            """
            UPDATE idea SET status = ?, outcome = ?, subject_points = ?,
                            comparator_points = ?, resolved_utc = ?
            WHERE idea_id = ?
            """,
            [
                str(IdeaStatus.RESOLVED), str(outcome), float(subject_points),
                float(comparator_points), resolved_utc, idea_id,
            ],
        )

    def void(self, idea_id: str, *, resolved_utc: dt.datetime, reason: str) -> None:
        self.wh.sql(
            "UPDATE idea SET status = ?, resolved_utc = ?, outcome = NULL WHERE idea_id = ?",
            [str(IdeaStatus.VOID), resolved_utc, idea_id],
        )
        self.record_observation(
            idea_id, -1, observed_utc=resolved_utc,
            subject_points=None, comparator_points=None, note=f"voided: {reason}",
        )

    def mark_acted(self, idea_id: str, *, acted: bool = True, when: dt.datetime | None = None) -> bool:
        """Record that the user did (or undid) the thing.

        Tracking does not depend on this flag -- unacted ideas are scored exactly
        the same way. The flag exists so the review can answer the far more
        interesting question of whether the ideas the user *skipped* were better
        than the ones they took.
        """
        if self.get(idea_id) is None:
            return False
        self.wh.sql(
            "UPDATE idea SET acted = ?, acted_utc = ? WHERE idea_id = ?",
            [bool(acted), when if acted else None, idea_id],
        )
        return True

    # -- pending clarifications ---------------------------------------------

    def put_pending(
        self,
        *,
        pending_id: str,
        created_utc: dt.datetime,
        source: str,
        source_ref: str,
        raw_text: str,
        question: str,
        candidates: tuple[CandidateMatch, ...],
    ) -> None:
        # Only one open question per chat: a second ambiguous message supersedes
        # the first, otherwise a bare "2" is dangerously ambiguous itself.
        self.wh.sql(
            "UPDATE idea_pending SET resolved = TRUE WHERE source = ? AND source_ref = ? AND NOT resolved",
            [source, source_ref],
        )
        self.wh.sql(
            "INSERT INTO idea_pending VALUES (?, ?, ?, ?, ?, ?, ?, FALSE)",
            [
                pending_id, created_utc, source, source_ref, raw_text, question,
                json.dumps([{"code": int(c.code), "label": c.label, "hint": c.hint} for c in candidates]),
            ],
        )

    def open_pending(self, source: str, source_ref: str) -> tuple[str, str, tuple[CandidateMatch, ...]] | None:
        """The unanswered question for this chat, if any."""
        df = self.wh.sql(
            """
            SELECT pending_id, raw_text, candidates FROM idea_pending
            WHERE source = ? AND source_ref = ? AND NOT resolved
            ORDER BY created_utc DESC LIMIT 1
            """,
            [source, source_ref],
        )
        if df.empty:
            return None
        row = df.iloc[0]
        cands = tuple(
            CandidateMatch(code=PlayerCode(int(c["code"])), label=c["label"], hint=c["hint"], score=1.0)
            for c in json.loads(row["candidates"])
        )
        return str(row["pending_id"]), str(row["raw_text"]), cands

    def close_pending(self, pending_id: str) -> None:
        self.wh.sql("UPDATE idea_pending SET resolved = TRUE WHERE pending_id = ?", [pending_id])

    # -- reads ---------------------------------------------------------------

    def get(self, idea_id: str) -> Idea | None:
        df = self.wh.sql("SELECT * FROM idea WHERE idea_id = ?", [idea_id])
        return None if df.empty else self._row_to_idea(df.iloc[0])

    def ideas(
        self,
        *,
        season: str | None = None,
        status: IdeaStatus | None = None,
        limit: int | None = None,
    ) -> list[Idea]:
        clauses, params = [], []
        if season:
            clauses.append("season = ?")
            params.append(season)
        if status:
            clauses.append("status = ?")
            params.append(str(status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM idea {where} ORDER BY created_utc DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"  # int-cast, never user text
        return [self._row_to_idea(r) for _, r in self.wh.sql(sql, params).iterrows()]

    def latest_verdict(self, idea_id: str) -> Verdict | None:
        df = self.wh.sql(
            "SELECT * FROM idea_verdict WHERE idea_id = ? ORDER BY issued_utc DESC LIMIT 1",
            [idea_id],
        )
        return None if df.empty else self._row_to_verdict(df.iloc[0])

    def context(self, idea_id: str) -> IdeaContext | None:
        df = self.wh.sql("SELECT * FROM idea_context WHERE idea_id = ?", [idea_id])
        return None if df.empty else self._row_to_context(df.iloc[0])

    def context_frame(self, season: str | None = None) -> pd.DataFrame:
        """Ideas joined to their submission-time context, for the bias probes.

        Returned as a frame rather than objects because every consumer is doing
        vectorised statistics over the whole history.
        """
        where = "WHERE i.season = ?" if season else ""
        return self.wh.sql(
            f"""
            SELECT i.idea_id, i.created_utc, i.raw_text, i.kind, i.subject_code, i.subject_name,
                   i.gw, i.horizon_gws, i.status, i.acted, i.outcome,
                   i.subject_points, i.comparator_points, i.source, i.thesis,
                   c.form_percentile, c.form_points_last3, c.last_match_was_home,
                   c.gws_since_haul, c.selected_by_pct, c.price_tenths,
                   c.season_ppg, c.pop_home_rate, c.pop_mean_gws_since_haul,
                   c.pop_recent_haul_rate, c.pop_n,
                   c.is_supported_club, c.pop_supported_club_rate, c.team_code,
                   v.stance, v.p_thesis_true, v.provider, v.degraded
            FROM idea i
            LEFT JOIN idea_context c USING (idea_id)
            LEFT JOIN (
                SELECT * EXCLUDE (rn) FROM (
                    SELECT *, ROW_NUMBER() OVER (PARTITION BY idea_id ORDER BY issued_utc DESC) rn
                    FROM idea_verdict
                ) WHERE rn = 1
            ) v USING (idea_id)
            {where}
            ORDER BY i.created_utc
            """,
            [season] if season else [],
        )

    def observations(self, idea_id: str) -> pd.DataFrame:
        return self.wh.sql(
            "SELECT gw, subject_points, comparator_points, note FROM idea_observation "
            "WHERE idea_id = ? AND gw >= 0 ORDER BY gw",
            [idea_id],
        )

    def record(self, idea_id: str) -> IdeaRecord | None:
        idea = self.get(idea_id)
        if idea is None:
            return None
        obs = self.observations(idea_id)
        return IdeaRecord(
            idea=idea,
            verdict=self.latest_verdict(idea_id),
            context=self.context(idea_id),
            observations=tuple(
                (int(r["gw"]), _opt_float(r["subject_points"]), _opt_float(r["comparator_points"]))
                for _, r in obs.iterrows()
            ),
        )

    def records(self, *, season: str | None = None, limit: int | None = None) -> list[IdeaRecord]:
        return [
            r for r in (self.record(i.idea_id) for i in self.ideas(season=season, limit=limit))
            if r is not None
        ]

    def count(self) -> int:
        return int(self.wh.sql("SELECT count(*) AS n FROM idea").iloc[0]["n"])

    # -- row mapping ---------------------------------------------------------

    @staticmethod
    def _row_to_idea(row: pd.Series) -> Idea:
        return Idea(
            idea_id=str(row["idea_id"]),
            created_utc=_opt_ts(row["created_utc"]),  # type: ignore[arg-type]
            as_of=_opt_ts(row["as_of"]),  # type: ignore[arg-type]
            source=str(row["source"]),
            source_ref=_opt_str(row["source_ref"]),
            raw_text=str(row["raw_text"]),
            season=Season(str(row["season"])),
            kind=IdeaKind(str(row["kind"])),
            subject_code=(lambda v: PlayerCode(v) if v is not None else None)(_opt_int(row["subject_code"])),
            subject_name=_opt_str(row["subject_name"]),
            comparator=Comparator(str(row["comparator"])),
            comparator_code=(lambda v: PlayerCode(v) if v is not None else None)(_opt_int(row["comparator_code"])),
            comparator_label=str(row["comparator_label"]),
            gw=GwId(int(row["gw"])),
            horizon_gws=int(row["horizon_gws"]),
            thesis=str(row["thesis"]),
            resolution_rule=str(row["resolution_rule"]),
            parse_confidence=float(row["parse_confidence"]),
            status=IdeaStatus(str(row["status"])),
            acted=bool(row["acted"]),
            acted_utc=_opt_ts(row["acted_utc"]),
            resolved_utc=_opt_ts(row["resolved_utc"]),
            outcome=(lambda v: Outcome(v) if v else None)(_opt_str(row["outcome"])),
            subject_points=_opt_float(row["subject_points"]),
            comparator_points=_opt_float(row["comparator_points"]),
        )

    @staticmethod
    def _row_to_verdict(row: pd.Series) -> Verdict:
        return Verdict(
            idea_id=str(row["idea_id"]),
            issued_utc=_opt_ts(row["issued_utc"]),  # type: ignore[arg-type]
            provider=str(row["provider"]),
            provider_version=str(row["provider_version"]),
            stance=Stance(str(row["stance"])),
            p_thesis_true=float(row["p_thesis_true"]),
            confidence=str(row["confidence"]),
            rationale=str(row["rationale"]),
            degraded=bool(row["degraded"]),
            latency_ms=float(row["latency_ms"]),
        )

    @staticmethod
    def _row_to_context(row: pd.Series) -> IdeaContext:
        return IdeaContext(
            idea_id=str(row["idea_id"]),
            captured_utc=_opt_ts(row["captured_utc"]),  # type: ignore[arg-type]
            position=_opt_int(row["position"]),
            team_code=_opt_int(row["team_code"]),
            price_tenths=_opt_int(row["price_tenths"]),
            selected_by_pct=_opt_float(row["selected_by_pct"]),
            availability=_opt_str(row["availability"]),
            form_points_last3=_opt_float(row["form_points_last3"]),
            form_percentile=_opt_float(row["form_percentile"]),
            minutes_last3=_opt_int(row["minutes_last3"]),
            last_match_gw=_opt_int(row["last_match_gw"]),
            last_match_points=_opt_int(row["last_match_points"]),
            last_match_was_home=(lambda v: None if v is None else bool(v))(
                None if pd.isna(row["last_match_was_home"]) else row["last_match_was_home"]
            ),
            gws_since_haul=_opt_int(row["gws_since_haul"]),
            season_ppg=_opt_float(row["season_ppg"]),
            pop_home_rate=_opt_float(row["pop_home_rate"]),
            pop_mean_gws_since_haul=_opt_float(row["pop_mean_gws_since_haul"]),
            pop_recent_haul_rate=_opt_float(row["pop_recent_haul_rate"]),
            pop_mean_form_pct=_opt_float(row["pop_mean_form_pct"]),
            pop_n=int(row["pop_n"] or 0),
            supported_team_code=_opt_int(row["supported_team_code"]),
            is_supported_club=(
                None if pd.isna(row["is_supported_club"]) else bool(row["is_supported_club"])
            ),
            pop_supported_club_rate=_opt_float(row["pop_supported_club_rate"]),
            pop_universe_n=int(row["pop_universe_n"] or 0),
        )
