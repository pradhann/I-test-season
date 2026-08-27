"""The other half of the leakage story: the WEIGHTS must be point-in-time too.

``test_content_pit.py`` proves a claim published after a deadline cannot reach
a decision taken at it. That is only half the guarantee, because a consensus is
a weighted sum and the leak can enter through either factor.

The multiplication is ``claim x creator_weight``. The claims were filtered at
the deadline. The weights were not: ``_weights()`` took the newest
``creator_score`` row outright, which is the track record measured TODAY, after
every gameweek the deadline had not yet seen. So the tools filtered the past
correctly and then weighted it with the future, in a payload that echoes
``as_of`` back to the caller and whose docstring says "pass the deadline you are
deciding at".

That combination is worse than an obvious leak. There is no symptom: the
response looks point-in-time, the claim list IS point-in-time, and the only
tell is a backtest that beats live for no reason anyone can name. On the live
warehouse, ``_weights()`` returned a top creator with 52 scored claims and 24
hits at a GW1 deadline where the same creator had zero of each.

It was masked, not absent. Every earned weight is currently 0.0, so the
mechanism multiplied by zero and produced the right number for the wrong
reason. It fires the instant one creator earns a weight -- which is exactly the
moment the weighted consensus starts being used for anything.

These tests fail if a weight measured after the decision instant can influence
the answer at that instant.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.ingest.content.models import Action, Claim
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, PlayerCode
from fpl_mcp.tools import content_tools

UTC = dt.UTC

SEASON = "2026-27"
CREATOR = "Measured Creator"
PLAYER = 111

#: 2026-27 GW1. The instant the decision is taken.
DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)

#: The scoring run that produced the track record in force AT the deadline: a
#: creator with no settled claims yet, and therefore no earned weight.
EARLY_RUN = DEADLINE - dt.timedelta(days=1)

#: A later scoring run, after the gameweek finalised. The creator turns out to
#: have been right often enough to earn real weight -- knowledge that did not
#: exist at the deadline and must not reach an answer dated to it.
LATE_RUN = DEADLINE + dt.timedelta(days=4)
LATE_WEIGHT = 0.9

_SCORE_COLS = [
    "creator", "scope", "as_of", "claims_total", "claims_scored", "hits",
    "hit_rate", "wilson_lo95", "weight", "first_claim_utc", "last_claim_utc",
]


def _score_row(as_of: dt.datetime, *, scored: int, hits: int, weight: float) -> dict:
    return {
        "creator": CREATOR, "scope": "all", "as_of": as_of,
        "claims_total": 40, "claims_scored": scored, "hits": hits,
        "hit_rate": (hits / scored) if scored else None,
        "wilson_lo95": 0.5 + weight / 2.0, "weight": weight,
        "first_claim_utc": DEADLINE - dt.timedelta(days=30),
        "last_claim_utc": DEADLINE - dt.timedelta(hours=6),
    }


@pytest.fixture
def warehouse_path(tmp_path, monkeypatch):
    """A warehouse holding one pre-deadline claim and two track-record runs."""
    path = tmp_path / "weights.duckdb"
    with Warehouse(path) as warehouse:
        store = ContentStore(warehouse)
        store.insert_claims([
            Claim(
                claim_id="c1", item_id="i1", creator=CREATOR, source_key="test",
                player_code=PlayerCode(PLAYER), player_name="test player",
                surface_form="Test Player", action=Action.BUY, season=SEASON,
                gameweek=GwId(1), confidence=0.8,
                rationale="buying the test player this week",
                source_url="https://example.invalid/c1",
                published_at=DEADLINE - dt.timedelta(hours=6),
            )
        ])
        store.insert_scores(pd.DataFrame(
            [
                _score_row(EARLY_RUN, scored=0, hits=0, weight=0.0),
                _score_row(LATE_RUN, scored=40, hits=34, weight=LATE_WEIGHT),
            ],
            columns=_SCORE_COLS,
        ))
    monkeypatch.setenv("FPL_EDGE_DB", str(path))
    return path


def _consensus_at(moment: dt.datetime) -> dict:
    return content_tools.fpl_creator_consensus(
        gameweek=1, season=SEASON, as_of=moment.isoformat()
    )


class TestWeightsAreFilteredAtTheSameInstantAsClaims:
    def test_a_weight_earned_after_the_deadline_cannot_reach_the_answer(
        self, warehouse_path
    ) -> None:
        """The headline guarantee, on the factor nobody was watching.

        The claim is correctly visible: it was published before the deadline.
        The weight is not: it was measured four days later, from gameweeks the
        deadline had not seen. ``weighted_creators`` is documented as "the
        number that matters", so a future weight landing in it is a future
        number in a payload stamped with a past instant.
        """
        out = _consensus_at(DEADLINE)

        assert out["claims_visible"] == 1, "the claim itself should be visible"
        assert out["creators_with_earned_weight"] == 0
        assert [r["weighted_creators"] for r in out["consensus"]] == [0.0], (
            "a creator weight measured after the decision instant reached an "
            "answer dated to that instant -- claims filtered at the deadline, "
            "then multiplied by hindsight"
        )

    def test_the_same_weight_does_apply_once_it_has_been_earned(
        self, warehouse_path
    ) -> None:
        """A time filter, not a permanent zero.

        Without this, deleting the weighting entirely would pass the test above.
        """
        out = _consensus_at(LATE_RUN + dt.timedelta(hours=1))

        assert out["creators_with_earned_weight"] == 1
        assert [r["weighted_creators"] for r in out["consensus"]] == [LATE_WEIGHT]

    def test_the_boundary_instant_is_inclusive(self, warehouse_path) -> None:
        """``as_of <= moment``, unlike ``published_at < as_of``, and on purpose.

        A score row is not an utterance a manager had to read and act on; it is
        a derived table stamped with the instant it was computed. The row
        stamped exactly at the moment IS the state at that moment.
        """
        assert _consensus_at(LATE_RUN)["consensus"][0]["weighted_creators"] == LATE_WEIGHT
        just_before = LATE_RUN - dt.timedelta(microseconds=1)
        assert _consensus_at(just_before)["consensus"][0]["weighted_creators"] == 0.0

    def test_player_claims_weights_each_claim_at_the_moment_asked_about(
        self, warehouse_path
    ) -> None:
        """The per-claim ``creator_weight`` is the same multiplication.

        Its own docstring reads "a creator_weight of 0.0 means that creator has
        not demonstrated an edge" -- present tense, at the ``as_of`` being asked
        about, not at whenever the reader happens to run the tool.
        """
        at_deadline = content_tools.fpl_player_claims(
            PLAYER, as_of=DEADLINE.isoformat(), season=SEASON
        )
        assert at_deadline["claims_found"] == 1
        assert at_deadline["claims"][0]["creator_weight"] == 0.0, (
            "a claim visible at the deadline was labelled with a weight the "
            "creator did not earn until four days after it"
        )

        later = content_tools.fpl_player_claims(
            PLAYER, as_of=(LATE_RUN + dt.timedelta(hours=1)).isoformat(),
            season=SEASON,
        )
        assert later["claims"][0]["creator_weight"] == LATE_WEIGHT

    def test_the_track_record_reports_the_record_as_it_stood(
        self, warehouse_path
    ) -> None:
        """Same table, same defect: the newest row is not the row in force."""
        at_deadline = content_tools.fpl_creator_track_record(
            min_scored=0, as_of=DEADLINE.isoformat()
        )
        row = at_deadline["creators"][0]
        assert (row["claims_scored"], row["hits"], row["weight"]) == (0, 0, 0.0), (
            "the track record 'as of' the deadline reported claims settled "
            "after it"
        )
        assert at_deadline["aggregate"]["scored_claims"] == 0

        later = content_tools.fpl_creator_track_record(
            min_scored=0, as_of=(LATE_RUN + dt.timedelta(hours=1)).isoformat()
        )
        assert later["creators"][0]["claims_scored"] == 40
        assert later["aggregate"]["hits"] == 34

    def test_a_creator_with_no_score_row_yet_is_weightless_not_missing(
        self, warehouse_path
    ) -> None:
        """Before the first scoring run there is no record, so nothing is earned.

        Silently falling back to the newest available row would be the leak in
        its purest form: no measurement exists at this instant, so the honest
        answer is zero weight, not tomorrow's.
        """
        out = _consensus_at(EARLY_RUN - dt.timedelta(days=1))
        assert out["creators_scored"] == 0
        assert out["creators_with_earned_weight"] == 0


class TestWeightsHelper:
    """The seam itself, so a caller that forgets to pass a moment cannot compile."""

    def test_weights_requires_a_moment(self, warehouse_path) -> None:
        with (
            Warehouse(warehouse_path, read_only=True) as warehouse,
            pytest.raises(TypeError),
        ):
            content_tools._weights(warehouse)  # type: ignore[call-arg]

    def test_every_creator_score_read_in_the_module_is_filtered(self) -> None:
        """No second, unfiltered path back into ``creator_score``.

        The defect was one query in one helper while the tools around it did
        the right thing with the claims. If a raw ``FROM creator_score`` is
        ever reintroduced without an ``as_of`` bound, the leak comes back with
        no other symptom.
        """
        import inspect
        import re

        # Python string concatenation, comments and line breaks all get in the
        # way of reading the SQL off the source, so normalise first.
        source = re.sub(r'"\s*\n\s*"', "", inspect.getsource(content_tools))
        reads = [
            m.end() for m in re.finditer(r"FROM creator_score\b", source)
        ]
        assert len(reads) == 1, (
            f"{len(reads)} reads of creator_score in content_tools; every one "
            f"must be bounded by as_of, so there should be exactly one, in "
            f"_scores_as_of"
        )
        assert re.match(r"\s*WHERE as_of <= \?", source[reads[0]:]), (
            "a read of creator_score is not bounded by as_of; a decision at a "
            "past instant can be weighted by a track record measured after it"
        )
