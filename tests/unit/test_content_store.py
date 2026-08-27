"""Persistence semantics: what may be rewritten, and what may never be.

Three rows in this package have three different rules, and getting them
confused is how the track record died.

* ``content_item`` and ``content_claim`` are insert-once. A claim is an
  immutable utterance -- content_001_claims.sql says so, and it is the reason
  claims are deliberately not in ``PIT_KEYS``. Re-running extraction must be
  additive and must never edit what a creator said.
* ``claim_outcome`` is the opposite. It is a verdict about a world that had
  not finished happening yet, so "unscoreable because the gameweek has not
  been played" MUST become a real hit or miss once it has. Freezing the first
  verdict is what left every one of the warehouse's claims at ``hit IS NULL``
  while the scoring run computed real hits in memory and discarded them.
* ``content_source`` records the registry as it stands. The migration says the
  table exists so "a source ... whose access policy changes leaves a trace
  rather than vanishing"; an insert-once made the stored row say the opposite
  of the current policy, which is the one thing it was there to prevent.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.ingest.content.models import Action, Claim, ContentItem
from fpl_edge.ingest.content.scoring import ResultIndex, score_claims
from fpl_edge.ingest.content.sources import AccessPolicy, Source, SourceKind
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, PlayerCode

UTC = dt.UTC

SEASON = "2026-27"
DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
BEFORE = DEADLINE - dt.timedelta(hours=6)

#: Run 1 happens before a ball is kicked; run 2 after the gameweek finalises.
RUN_1 = dt.datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
RUN_2 = dt.datetime(2026, 8, 25, 9, 0, tzinfo=UTC)

CALENDAR = GameweekCalendar([(SEASON, 1, DEADLINE)])


@pytest.fixture
def store(tmp_path):
    with Warehouse(tmp_path / "store.duckdb") as warehouse:
        yield ContentStore(warehouse)


def _claim(claim_id: str, *, code: int = 111, action: Action = Action.CAPTAIN) -> Claim:
    return Claim(
        claim_id=claim_id,
        item_id=f"item-{claim_id}",
        creator="Measured Creator",
        source_key="test",
        player_code=PlayerCode(code),
        player_name="test player",
        surface_form="Test Player",
        action=action,
        season=SEASON,
        gameweek=GwId(1),
        confidence=0.8,
        rationale="captaining the test player this week",
        source_url=f"https://example.invalid/{claim_id}",
        published_at=BEFORE,
    )


def _claims_frame(claims: list[Claim]) -> pd.DataFrame:
    return pd.DataFrame([{
        "claim_id": c.claim_id, "creator": c.creator, "season": c.season,
        "gameweek": int(c.gameweek), "player_code": int(c.player_code),
        "action": str(c.action), "published_at": c.published_at,
    } for c in claims])


def _empty_index() -> ResultIndex:
    """The world before GW1: no fixture rows exist yet."""
    return ResultIndex(pd.DataFrame(), pd.DataFrame())


def _finalised_index() -> ResultIndex:
    """GW1 played. Code 111 hauls; the positional field does not."""
    results = pd.DataFrame([
        {"season": SEASON, "gw": 1, "code": 111, "fixture_id": 1,
         "total_points": 20, "starts": 1, "minutes": 90},
        {"season": SEASON, "gw": 1, "code": 222, "fixture_id": 1,
         "total_points": 2, "starts": 1, "minutes": 90},
        {"season": SEASON, "gw": 1, "code": 333, "fixture_id": 2,
         "total_points": 2, "starts": 1, "minutes": 90},
    ])
    players = pd.DataFrame([
        {"season": SEASON, "code": code, "position": 4} for code in (111, 222, 333)
    ])
    return ResultIndex(results, players)


class TestSettlementIsRevisable:
    """The defect that zeroed the track record: outcomes frozen at first write."""

    def test_an_unscoreable_claim_becomes_settled_once_the_gameweek_finalises(
        self, store: ContentStore
    ) -> None:
        """Two scoring runs, one claim, and the world moves in between.

        Run 1 sees a gameweek that has not kicked off and can only honestly say
        ``gameweek_not_played``. Run 2 runs after the results land. If the row
        does not change, a creator can never resolve a single claim no matter
        how many gameweeks pass, every earned weight is pinned at zero for a
        reason that is an artefact of the writer, and the reader reports "no
        resolved claims yet" forever.
        """
        claim = _claim("c1")
        store.insert_claims([claim])
        frame = _claims_frame([claim])

        run1, _ = score_claims(frame, _empty_index(), CALENDAR, now=RUN_1)
        store.insert_outcomes(run1)

        stored = store.wh.sql("SELECT * FROM claim_outcome").iloc[0]
        assert pd.isna(stored["hit"])
        assert stored["unscoreable"] == "gameweek_not_played"

        run2, _ = score_claims(frame, _finalised_index(), CALENDAR, now=RUN_2)
        store.insert_outcomes(run2)

        after = store.wh.sql("SELECT * FROM claim_outcome")
        assert len(after) == 1, "the rescore duplicated the claim instead of revising it"
        row = after.iloc[0]
        # pd.notna first: a still-NULL hit must fail this assertion with its own
        # message, not with "boolean value of NA is ambiguous".
        assert pd.notna(row["hit"]) and bool(row["hit"]), (
            "a claim first written as unscoreable stayed unscoreable after its "
            "gameweek finalised; the creator track record can never leave zero"
        )
        assert pd.isna(row["unscoreable"]) or row["unscoreable"] is None
        assert float(row["player_points"]) == 20.0
        assert pd.Timestamp(row["resolved_utc"]).to_pydatetime().astimezone(UTC) == RUN_2, (
            "the revised verdict kept the timestamp of the run that could not "
            "reach it, so the row no longer says when it was actually resolved"
        )

    def test_a_rescore_reports_which_verdicts_moved(self, store: ContentStore) -> None:
        """A run that revises nothing and a run that never happened differ."""
        claims = [_claim("c1"), _claim("c2", code=222)]
        store.insert_claims(claims)
        frame = _claims_frame(claims)

        first, _ = score_claims(frame, _empty_index(), CALENDAR, now=RUN_1)
        assert store.insert_outcomes(first) == (2, 0, 0)

        second, _ = score_claims(frame, _finalised_index(), CALENDAR, now=RUN_2)
        written = store.insert_outcomes(second)
        assert (written.inserted, written.revised) == (0, 2)

        third, _ = score_claims(frame, _finalised_index(), CALENDAR, now=RUN_2)
        assert store.insert_outcomes(third).revised == 0, (
            "a rescore over an unchanged world reported revisions"
        )

    def test_claims_and_items_are_still_insert_once(self, store: ContentStore) -> None:
        """The immutability that outcomes gave up must not leak to utterances.

        A claim is an immutable utterance and an item is an archived copy of
        what was published. If a re-ingest could rewrite either, the archive
        stops being evidence and the whole track record is unfalsifiable.
        """
        claim = _claim("c1")
        store.insert_claims([claim])
        store.insert_items([
            ContentItem("i1", "test", "C", "podcast", "original title", "u1",
                        BEFORE, "original text", BEFORE),
        ])

        edited = _claim("c1", action=Action.SELL)
        assert store.insert_claims([edited]) == 0
        assert store.insert_items([
            ContentItem("i1", "test", "C", "podcast", "rewritten title", "u1",
                        BEFORE, "rewritten text", BEFORE),
        ]) == 0

        assert store.wh.sql(
            "SELECT action FROM content_claim").iloc[0]["action"] == str(Action.CAPTAIN)
        assert store.wh.sql(
            "SELECT title FROM content_item").iloc[0]["title"] == "original title"


class TestSourceRegistryUpsert:
    def test_a_changed_access_policy_reaches_the_stored_row(
        self, store: ContentStore
    ) -> None:
        """The migration's stated purpose, made true.

        A source demoted to FORBIDDEN in sources.py previously left the DB
        asserting it was still OPEN -- the table advertised as the trace of a
        policy change recorded the pre-change value forever.
        """
        before = Source("s1", "Old Name", SourceKind.PODCAST,
                        "https://example.invalid/old", policy=AccessPolicy.OPEN)
        store.upsert_sources((before,))

        after = Source("s1", "New Name", SourceKind.PODCAST,
                       "https://example.invalid/new", policy=AccessPolicy.FORBIDDEN,
                       note="terms changed")
        written = store.upsert_sources((after,))

        row = store.wh.sql("SELECT * FROM content_source").iloc[0]
        assert (written.inserted, written.updated) == (0, 1)
        assert row["policy"] == str(AccessPolicy.FORBIDDEN)
        assert row["url"] == "https://example.invalid/new"
        assert row["creator"] == "New Name"
        assert row["note"] == "terms changed"

    def test_a_definition_upsert_leaves_probe_state_alone(
        self, store: ContentStore
    ) -> None:
        """Probe columns are runtime state, and the upsert has nothing to say.

        cmd_ingest calls upsert_sources on every run, before any fetching. If a
        definition write blanked these, the last real HTTP status a source
        returned would be destroyed at the start of each run and the registry
        would never be able to show which sources are failing.
        """
        source = Source("s1", "Creator", SourceKind.PODCAST, "https://example.invalid/a")
        store.upsert_sources((source,))
        store.record_probe("s1", status=403, items=0, error="forbidden",
                           at=dt.datetime(2026, 8, 20, tzinfo=UTC))

        renamed = Source("s1", "Creator", SourceKind.PODCAST,
                         "https://example.invalid/b")
        store.upsert_sources((renamed,))

        row = store.wh.sql("SELECT * FROM content_source").iloc[0]
        assert row["url"] == "https://example.invalid/b"
        assert int(row["last_http_status"]) == 403, (
            "a definition upsert clobbered the record of the last real fetch"
        )
        assert row["last_error"] == "forbidden"
        assert pd.notna(row["last_probe_utc"])

    def test_an_unchanged_registry_is_not_counted_as_an_update(
        self, store: ContentStore
    ) -> None:
        source = Source("s1", "Creator", SourceKind.PODCAST, "https://example.invalid/a")
        assert store.upsert_sources((source,)) == (1, 0)
        assert store.upsert_sources((source,)) == (0, 0)
