"""The leakage tests. If any of these fail, nothing else in the package matters.

``published_at`` is the field that separates "a creator predicted this" from "a
creator described what already happened". Content is unusually dangerous here
because the archive is full of retrospectives: an episode titled "GW12 Captain
Review" published the Monday after GW12 contains the words "captain", "Haaland"
and "GW12", parses into a perfectly well-formed claim, and would be scored as a
brilliant prediction.

These tests prove the two defences hold:

* a claim published at or after a deadline is invisible to a snapshot taken at
  that deadline (:class:`TestSnapshotVisibility`);
* the same claim is refused a hit even when scoring runs later with full
  hindsight (:class:`TestScoringRefusesLateClaims`).

The second is not redundant. The first stops a late claim reaching a *decision*;
only the second stops it inflating a creator's *weight*, and an inflated weight
poisons every future decision rather than one.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.ingest.content.models import Action, Claim
from fpl_edge.ingest.content.scoring import ResultIndex, score_claims
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, PlayerCode

UTC = dt.UTC

#: 2026-27 GW1, from the API. Never from the rules page, which renders local.
DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)

BEFORE = DEADLINE - dt.timedelta(hours=6)
AFTER = DEADLINE + dt.timedelta(hours=6)


def _claim(claim_id: str, published_at: dt.datetime, *, creator: str = "Early Creator",
           code: int = 111, action: Action = Action.CAPTAIN, gw: int = 1) -> Claim:
    return Claim(
        claim_id=claim_id,
        item_id=f"item-{claim_id}",
        creator=creator,
        source_key="test",
        player_code=PlayerCode(code),
        player_name="test player",
        surface_form="Test Player",
        action=action,
        season="2026-27",
        gameweek=GwId(gw),
        confidence=0.8,
        rationale="captaining the test player this week",
        source_url=f"https://example.invalid/{claim_id}",
        published_at=published_at,
    )


@pytest.fixture
def store(tmp_path):
    with Warehouse(tmp_path / "pit.duckdb") as warehouse:
        yield ContentStore(warehouse)


class TestSnapshotVisibility:
    def test_late_claim_is_invisible_at_the_deadline(self, store: ContentStore) -> None:
        """The headline guarantee: publish after the deadline, be unreadable at it."""
        store.insert_claims([
            _claim("early", BEFORE, creator="Early Creator"),
            _claim("late", AFTER, creator="Late Creator"),
        ])

        visible = store.claims_visible_at(DEADLINE)

        assert list(visible["claim_id"]) == ["early"], (
            "a claim published after the deadline reached a snapshot taken at it"
        )
        assert "Late Creator" not in set(visible["creator"])

    def test_claim_published_exactly_at_the_deadline_is_excluded(
        self, store: ContentStore
    ) -> None:
        """Strictly less-than, and the boundary is not an accident.

        A team locks AT the deadline instant, so a claim stamped with that exact
        instant could not have been acted on. Feed timestamps are also routinely
        rounded to the minute, which makes an exact tie far more likely to be a
        rounded-late claim than a genuinely simultaneous one.
        """
        store.insert_claims([_claim("boundary", DEADLINE)])
        assert store.claims_visible_at(DEADLINE).empty

    def test_the_same_claim_becomes_visible_at_a_later_snapshot(
        self, store: ContentStore
    ) -> None:
        """The filter is time, not a permanent quarantine."""
        store.insert_claims([_claim("late", AFTER)])
        assert store.claims_visible_at(DEADLINE).empty
        later = store.claims_visible_at(AFTER + dt.timedelta(minutes=1))
        assert list(later["claim_id"]) == ["late"]

    def test_items_obey_the_same_rule(self, store: ContentStore) -> None:
        from fpl_edge.ingest.content.models import ContentItem

        store.insert_items([
            ContentItem("i-early", "test", "C", "podcast", "early", "u1",
                        BEFORE, "text", BEFORE),
            ContentItem("i-late", "test", "C", "podcast", "late", "u2",
                        AFTER, "text", AFTER),
        ])
        assert list(store.items_visible_at(DEADLINE)["item_id"]) == ["i-early"]

    def test_naive_as_of_is_refused(self, store: ContentStore) -> None:
        """A naive timestamp is a leak waiting to happen, not a convenience."""
        with pytest.raises(ValueError, match="timezone-aware"):
            store.claims_visible_at(dt.datetime(2026, 8, 21, 17, 30))

    def test_a_claim_cannot_be_constructed_with_a_naive_timestamp(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _claim("naive", dt.datetime(2026, 8, 20, 12, 0))


class TestScoringRefusesLateClaims:
    """Visibility filtering alone would still let hindsight earn a weight."""

    @staticmethod
    def _index() -> ResultIndex:
        results = pd.DataFrame([
            # The claimed player hauls; the positional field does not.
            {"season": "2026-27", "gw": 1, "code": 111, "fixture_id": 1,
             "total_points": 20, "starts": 1, "minutes": 90},
            {"season": "2026-27", "gw": 1, "code": 222, "fixture_id": 1,
             "total_points": 2, "starts": 1, "minutes": 90},
            {"season": "2026-27", "gw": 1, "code": 333, "fixture_id": 2,
             "total_points": 2, "starts": 1, "minutes": 90},
        ])
        players = pd.DataFrame([
            {"season": "2026-27", "code": 111, "position": 4},
            {"season": "2026-27", "code": 222, "position": 4},
            {"season": "2026-27", "code": 333, "position": 4},
        ])
        return ResultIndex(results, players)

    @staticmethod
    def _calendar() -> GameweekCalendar:
        return GameweekCalendar([("2026-27", 1, DEADLINE)])

    def _score(self, claims: list[Claim]) -> pd.DataFrame:
        frame = pd.DataFrame([{
            "claim_id": c.claim_id, "creator": c.creator, "season": c.season,
            "gameweek": int(c.gameweek), "player_code": int(c.player_code),
            "action": str(c.action), "published_at": c.published_at,
        } for c in claims])
        outcomes, _ = score_claims(
            frame, self._index(), self._calendar(), now=dt.datetime(2026, 9, 1, tzinfo=UTC)
        )
        return outcomes.set_index("claim_id")

    def test_identical_claims_score_differently_on_timing_alone(self) -> None:
        """Same creator, same player, same action, same gameweek, same result.

        The ONLY difference is when it was published, and that is the whole
        difference between a prediction and a boast.
        """
        outcomes = self._score([
            _claim("early", BEFORE),
            _claim("late", AFTER),
        ])

        assert outcomes.loc["early", "hit"] is True or outcomes.loc["early", "hit"] == 1
        assert pd.isna(outcomes.loc["early", "unscoreable"])

        assert pd.isna(outcomes.loc["late", "hit"]), (
            "a claim published after the deadline earned a hit; every creator "
            "with a review-episode archive would now have a fabricated edge"
        )
        assert outcomes.loc["late", "unscoreable"] == "published_after_deadline"

    def test_a_late_claim_contributes_to_neither_numerator_nor_denominator(self) -> None:
        from fpl_edge.ingest.content.scoring import creator_scores

        claims = [_claim(f"late{i}", AFTER, creator="Hindsight FPL") for i in range(40)]
        frame = pd.DataFrame([{
            "claim_id": c.claim_id, "creator": c.creator, "season": c.season,
            "gameweek": int(c.gameweek), "player_code": int(c.player_code),
            "action": str(c.action), "published_at": c.published_at,
        } for c in claims])
        outcomes = self._score(claims).reset_index()
        scores = creator_scores(outcomes, frame, as_of=dt.datetime(2026, 9, 1, tzinfo=UTC))
        overall = scores[(scores["creator"] == "Hindsight FPL") & (scores["scope"] == "all")]

        assert int(overall.iloc[0]["claims_total"]) == 40
        assert int(overall.iloc[0]["claims_scored"]) == 0
        assert float(overall.iloc[0]["weight"]) == 0.0, (
            "forty post-hoc claims about a player who hauled produced a non-zero weight"
        )
