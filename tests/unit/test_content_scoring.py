"""Track-record scoring, the earned weight, and the consensus map.

The property under test throughout is that a creator has to *earn* influence.
The tests are written so that a future change loosening the weighting -- adding
a prior, giving new creators the benefit of the doubt, dropping the minimum
sample -- breaks something with a name that says why it mattered.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.ingest.content.consensus import consensus_map, deduplicate
from fpl_edge.ingest.content.scoring import (
    MIN_SCORED_CLAIMS,
    ResultIndex,
    creator_scores,
    earned_weight,
    score_claims,
    weight_lookup,
    wilson_lower_bound,
)

UTC = dt.UTC
SEASON = "2025-26"
DEADLINE = dt.datetime(2025, 8, 15, 17, 30, tzinfo=UTC)
BEFORE = DEADLINE - dt.timedelta(days=1)
NOW = dt.datetime(2026, 8, 18, tzinfo=UTC)

CALENDAR = GameweekCalendar([(SEASON, 1, DEADLINE)])

# Five forwards start and score 12, 2, 6, 4, 5 -> positional median 5.
# A sixth is an unused sub and must be excluded from the median.
RESULTS = pd.DataFrame([
    {"season": SEASON, "gw": 1, "code": 1, "fixture_id": 1,
     "total_points": 12, "starts": 1, "minutes": 90},
    {"season": SEASON, "gw": 1, "code": 2, "fixture_id": 1,
     "total_points": 2, "starts": 1, "minutes": 90},
    {"season": SEASON, "gw": 1, "code": 3, "fixture_id": 2,
     "total_points": 6, "starts": 1, "minutes": 90},
    {"season": SEASON, "gw": 1, "code": 4, "fixture_id": 2,
     "total_points": 4, "starts": 1, "minutes": 90},
    # Scores exactly the median: the tie case.
    {"season": SEASON, "gw": 1, "code": 5, "fixture_id": 2,
     "total_points": 5, "starts": 1, "minutes": 90},
    # An unused substitute: 1 point, no start. Must NOT drag the median down.
    {"season": SEASON, "gw": 1, "code": 6, "fixture_id": 2,
     "total_points": 1, "starts": 0, "minutes": 0},
])
PLAYERS = pd.DataFrame([
    {"season": SEASON, "code": code, "position": 4} for code in (1, 2, 3, 4, 5, 6)
])


def _claims(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "claim_id": row.get("claim_id", f"c{i}"),
            "creator": row.get("creator", "Creator A"),
            "season": SEASON,
            "gameweek": row.get("gw", 1),
            "player_code": row["code"],
            "player_name": f"player {row['code']}",
            "action": row["action"],
            "confidence": row.get("confidence", 0.7),
            "published_at": row.get("published_at", BEFORE),
        }
        for i, row in enumerate(rows)
    ])


@pytest.fixture
def index() -> ResultIndex:
    return ResultIndex(RESULTS, PLAYERS)


class TestBenchmark:
    def test_the_median_is_over_starters_not_the_whole_pool(
        self, index: ResultIndex
    ) -> None:
        """Including unused subs would make almost every recommendation a hit.

        The alternative to buying a forward is another forward who plays, not a
        pool half-full of bench fodder scoring 1.
        """
        value, label = index.benchmark(SEASON, 1, 1)
        assert value == 5.0  # median of 12, 2, 6, 4, 5 -- the 0-minute sub excluded
        assert "starter_median" in label

    def test_a_double_gameweek_sums_rather_than_duplicating(self) -> None:
        results = pd.DataFrame([
            {"season": SEASON, "gw": 1, "code": 1, "fixture_id": 1,
             "total_points": 6, "starts": 1, "minutes": 90},
            {"season": SEASON, "gw": 1, "code": 1, "fixture_id": 2,
             "total_points": 9, "starts": 1, "minutes": 90},
        ])
        players = pd.DataFrame([{"season": SEASON, "code": 1, "position": 4}])
        assert ResultIndex(results, players).points(SEASON, 1, 1) == 15.0

    def test_a_player_with_no_row_scored_zero_not_missing(
        self, index: ResultIndex
    ) -> None:
        """A recommended player who never made the squad returned zero."""
        assert index.points(SEASON, 1, 4242) == 0.0


class TestHitRules:
    def test_positive_and_negative_actions_score_in_opposite_directions(
        self, index: ResultIndex
    ) -> None:
        """Player 1 hauls (12 vs median 5); player 2 flops (2 vs 5)."""
        claims = _claims([
            {"claim_id": "buy_hauler", "code": 1, "action": "buy"},
            {"claim_id": "avoid_hauler", "code": 1, "action": "avoid"},
            {"claim_id": "buy_flop", "code": 2, "action": "buy"},
            {"claim_id": "avoid_flop", "code": 2, "action": "avoid"},
        ])
        outcomes, _stats = score_claims(claims, index, CALENDAR, now=NOW)
        by_id = outcomes.set_index("claim_id")["hit"]

        assert by_id["buy_hauler"] is True or by_id["buy_hauler"] == 1
        assert by_id["avoid_hauler"] in (False, 0)
        assert by_id["buy_flop"] in (False, 0)
        assert by_id["avoid_flop"] is True or by_id["avoid_flop"] == 1

    def test_a_tie_is_a_miss_on_both_sides(self, index: ResultIndex) -> None:
        """Player 5 scored exactly the median. The advice made no difference."""
        claims = _claims([
            {"claim_id": "buy_tie", "code": 5, "action": "buy"},
            {"claim_id": "avoid_tie", "code": 5, "action": "avoid"},
        ])
        outcomes, _ = score_claims(claims, index, CALENDAR, now=NOW)
        by_id = outcomes.set_index("claim_id")["hit"]
        assert by_id["buy_tie"] in (False, 0)
        assert by_id["avoid_tie"] in (False, 0)

    def test_an_unplayed_gameweek_is_unscoreable_not_a_miss(
        self, index: ResultIndex
    ) -> None:
        calendar = GameweekCalendar([
            (SEASON, 1, DEADLINE),
            ("2026-27", 1, dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)),
        ])
        claims = _claims([{"claim_id": "future", "code": 1, "action": "buy"}])
        claims["season"] = "2026-27"
        claims["published_at"] = dt.datetime(2026, 8, 19, tzinfo=UTC)
        outcomes, stats = score_claims(claims, index, calendar, now=NOW)
        assert outcomes.iloc[0]["unscoreable"] == "gameweek_not_played"
        assert pd.isna(outcomes.iloc[0]["hit"])
        assert stats.scored == 0


class TestEarnedWeight:
    def test_wilson_collapses_toward_zero_on_small_samples(self) -> None:
        """3/4 is a better point estimate than 130/200 and a worse reason to act."""
        assert wilson_lower_bound(3, 4) < wilson_lower_bound(130, 200)

    def test_a_small_perfect_record_earns_nothing(self) -> None:
        assert earned_weight(8, 8) == 0.0

    def test_a_coin_flip_at_scale_earns_nothing(self) -> None:
        assert earned_weight(100, 200) == 0.0

    def test_a_demonstrated_edge_earns_a_modest_weight(self) -> None:
        weight = earned_weight(130, 200)  # 65%, lower bound ~0.58
        assert 0.0 < weight < 0.35, (
            "a genuinely good creator should earn influence, not outvote the model"
        )

    def test_the_minimum_sample_gate_is_real(self) -> None:
        n = MIN_SCORED_CLAIMS - 1
        assert earned_weight(n, n) == 0.0

    def test_weight_is_monotone_in_the_record(self) -> None:
        assert earned_weight(160, 200) > earned_weight(130, 200) > earned_weight(110, 200)

    def test_a_creator_with_no_scoreable_claims_still_appears_at_zero(self) -> None:
        """Absent from the table looks like an oversight; zero looks like a decision."""
        claims = _claims([{"claim_id": "x", "code": 1, "action": "buy",
                           "creator": "Silent FPL"}])
        scores = creator_scores(pd.DataFrame(), claims, as_of=NOW)
        row = scores[scores["creator"] == "Silent FPL"].iloc[0]
        assert row["claims_scored"] == 0
        assert row["weight"] == 0.0

    def test_per_action_scopes_are_emitted(self, index: ResultIndex) -> None:
        claims = _claims([
            {"claim_id": "a", "code": 1, "action": "buy"},
            {"claim_id": "b", "code": 2, "action": "avoid"},
        ])
        outcomes, _ = score_claims(claims, index, CALENDAR, now=NOW)
        scores = creator_scores(outcomes, claims, as_of=NOW)
        assert set(scores["scope"]) == {"all", "buy", "avoid"}


class TestConsensus:
    def test_republication_across_platforms_collapses_to_one_vote(self) -> None:
        """Podcast + YouTube + show notes is one opinion, not three.

        Left uncollapsed, the consensus map measures publication volume rather
        than agreement, and the most prolific creator becomes the consensus.
        """
        claims = _claims([
            {"claim_id": "yt", "code": 1, "action": "captain", "creator": "Creator A"},
            {"claim_id": "pod", "code": 1, "action": "captain", "creator": "Creator A"},
            {"claim_id": "blog", "code": 1, "action": "captain", "creator": "Creator A"},
        ])
        deduped, dropped = deduplicate(claims)
        assert len(deduped) == 1
        assert dropped == 2

    def test_deduplication_keeps_the_earliest(self) -> None:
        claims = _claims([
            {"claim_id": "late", "code": 1, "action": "captain",
             "published_at": BEFORE},
            {"claim_id": "early", "code": 1, "action": "captain",
             "published_at": BEFORE - dt.timedelta(days=2)},
        ])
        deduped, _ = deduplicate(claims)
        assert list(deduped["claim_id"]) == ["early"]

    def test_unweighted_and_weighted_columns_diverge(self) -> None:
        """The whole argument, in one assertion.

        Three creators agree. Only one has earned any weight, so the raw count
        says 3 and the weighted count says 0.4. A model reading the raw column
        is reading the template.
        """
        claims = _claims([
            {"claim_id": "a", "code": 1, "action": "captain", "creator": "Proven"},
            {"claim_id": "b", "code": 1, "action": "captain", "creator": "Unproven 1"},
            {"claim_id": "c", "code": 1, "action": "captain", "creator": "Unproven 2"},
        ])
        table = consensus_map(claims, {"Proven": 0.4, "Unproven 1": 0.0, "Unproven 2": 0.0})
        row = table.iloc[0]
        assert row["distinct_creators"] == 3
        assert row["weighted_creators"] == pytest.approx(0.4)

    def test_with_no_earned_weights_the_weighted_signal_is_zero(self) -> None:
        """The correct pre-GW1 state: creators contribute nothing to the model."""
        claims = _claims([
            {"claim_id": "a", "code": 1, "action": "captain", "creator": "X"},
            {"claim_id": "b", "code": 1, "action": "captain", "creator": "Y"},
        ])
        table = consensus_map(claims, {})
        assert table["weighted_creators"].sum() == 0.0
        assert table["weighted_share"].sum() == 0.0
        assert table["distinct_creators"].sum() == 2

    def test_concentration_separates_agreement_from_scatter(self) -> None:
        """Four creators on one name is a signal; one each on four is noise."""
        agreed = _claims([
            {"claim_id": f"a{i}", "code": 1, "action": "captain", "creator": f"C{i}"}
            for i in range(4)
        ])
        scattered = _claims([
            {"claim_id": f"s{i}", "code": i + 1, "action": "captain", "creator": f"C{i}"}
            for i in range(4)
        ])
        assert consensus_map(agreed, {}).iloc[0]["hhi"] == pytest.approx(1.0)
        assert consensus_map(scattered, {}).iloc[0]["hhi"] == pytest.approx(0.25)

    def test_shares_are_computed_within_an_action(self) -> None:
        """A crowded buy list must not dilute a clear captain signal."""
        claims = _claims([
            {"claim_id": "cap", "code": 1, "action": "captain", "creator": "A"},
            {"claim_id": "b1", "code": 2, "action": "buy", "creator": "A"},
            {"claim_id": "b2", "code": 3, "action": "buy", "creator": "B"},
            {"claim_id": "b3", "code": 4, "action": "buy", "creator": "C"},
        ])
        table = consensus_map(claims, {})
        captain = table[table["action"] == "captain"].iloc[0]
        assert captain["share"] == pytest.approx(1.0)

    def test_weight_lookup_defaults_missing_creators_to_zero(self) -> None:
        scores = pd.DataFrame([
            {"creator": "A", "scope": "all", "weight": 0.3},
            {"creator": "A", "scope": "buy", "weight": 0.9},
        ])
        assert weight_lookup(scores) == {"A": 0.3}
        assert weight_lookup(pd.DataFrame()) == {}
