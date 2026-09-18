"""The other half of the leakage story: the WEIGHTS must be point-in-time too.

``test_content_pit.py`` proves a claim published after a deadline cannot reach
a decision taken at it. That is only half the guarantee, because a consensus is
a weighted sum and the leak can enter through either factor.

The multiplication is ``claim x creator_weight``. The claims were filtered at
the deadline. The weights were not: the helper took the newest
``creator_score`` row outright, which is the track record measured TODAY, after
every gameweek the deadline had not yet seen. So the surfaces filtered the past
correctly and then weighted it with the future, in a payload that echoes
``as_of`` back to the caller.

That combination is worse than an obvious leak. There is no symptom: the
response looks point-in-time, the claim list IS point-in-time, and the only
tell is a backtest that beats live for no reason anyone can name. On the live
warehouse, the unbounded read returned a top creator with 52 scored claims and
24 hits at a GW1 deadline where the same creator had zero of each.

It was masked, not absent. Every earned weight is currently 0.0, so the
mechanism multiplied by zero and produced the right number for the wrong
reason. It fires the instant one creator earns a weight -- which is exactly the
moment the weighted consensus starts being used for anything.

WHERE THIS LIVES NOW. The helper used to live in the old toolbelt package,
which is gone. The logic is
``fpl_edge.platform.scripts.creators.identity._weights_as_of``, the one read of
``creator_score`` behind every creator surface: ``creator_board`` weights its
consensus with it, ``player_chatter`` labels each claim from it, and
``creator_report_card`` reaches it through ``_card_scores`` rather than keeping
a copy. Testing the helper rather than one caller is what makes the structural
assertion at the bottom possible: the bound has one home, so a second
unbounded read anywhere in the package is a failure.

These tests fail if a weight measured after the decision instant can influence
the answer at that instant.
"""

from __future__ import annotations

import datetime as dt
import inspect
import re
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.platform.scripts.creators import identity, report_card
from fpl_edge.platform.scripts.creators.identity import _weights_as_of
from fpl_edge.store import Warehouse

UTC = dt.UTC

SEASON = "2026-27"
CREATOR = "Measured Creator"

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
def warehouse_path(tmp_path):
    """A warehouse holding two track-record runs, one either side of the deadline."""
    path = tmp_path / "weights.duckdb"
    with Warehouse(path) as warehouse:
        store = ContentStore(warehouse)
        store.insert_scores(pd.DataFrame(
            [
                _score_row(EARLY_RUN, scored=0, hits=0, weight=0.0),
                _score_row(LATE_RUN, scored=40, hits=34, weight=LATE_WEIGHT),
            ],
            columns=_SCORE_COLS,
        ))
    return path


def _weights_at(path, moment: dt.datetime) -> dict:
    with Warehouse(path, read_only=True) as warehouse:
        return _weights_as_of(warehouse, moment)


class TestWeightsAreFilteredAtTheSameInstantAsClaims:
    def test_a_weight_earned_after_the_deadline_cannot_reach_the_answer(
        self, warehouse_path
    ) -> None:
        """The headline guarantee, on the factor nobody was watching.

        A claim published before the deadline is correctly visible. The weight
        it would be multiplied by was measured four days later, from gameweeks
        the deadline had not seen, so a future number would land in a payload
        stamped with a past instant.
        """
        row = _weights_at(warehouse_path, DEADLINE)[CREATOR]

        assert row["weight"] == 0.0, (
            "a creator weight measured after the decision instant reached an "
            "answer dated to that instant -- claims filtered at the deadline, "
            "then multiplied by hindsight"
        )
        assert (row["claims_scored"], row["hits"]) == (0, 0)

    def test_the_same_weight_does_apply_once_it_has_been_earned(
        self, warehouse_path
    ) -> None:
        """A time filter, not a permanent zero.

        Without this, deleting the weighting entirely would pass the test above.
        """
        row = _weights_at(warehouse_path, LATE_RUN + dt.timedelta(hours=1))[CREATOR]

        assert row["weight"] == LATE_WEIGHT
        assert (row["claims_scored"], row["hits"]) == (40, 34)

    def test_the_boundary_instant_is_inclusive(self, warehouse_path) -> None:
        """``as_of <= moment``, unlike ``published_at < as_of``, and on purpose.

        A score row is not an utterance a manager had to read and act on; it is
        a derived table stamped with the instant it was computed. The row
        stamped exactly at the moment IS the state at that moment.
        """
        assert _weights_at(warehouse_path, LATE_RUN)[CREATOR]["weight"] == LATE_WEIGHT
        just_before = LATE_RUN - dt.timedelta(microseconds=1)
        assert _weights_at(warehouse_path, just_before)[CREATOR]["weight"] == 0.0

    def test_a_creator_with_no_score_row_yet_is_absent_not_backfilled(
        self, warehouse_path
    ) -> None:
        """Before the first scoring run there is no record, so nothing is earned.

        Silently falling back to the newest available row would be the leak in
        its purest form: no measurement exists at this instant, so the honest
        answer is no row, which every caller renders as unmeasured, not
        tomorrow's number.
        """
        assert _weights_at(warehouse_path, EARLY_RUN - dt.timedelta(days=1)) == {}

    def test_the_report_card_reads_the_record_through_the_same_helper(
        self, warehouse_path
    ) -> None:
        """One bound, one home. A second copy is a second thing to forget.

        ``creator_report_card`` is the surface that quotes a track record at a
        reader, so a private unbounded read here would be the leak wearing the
        most authoritative label in the package.
        """
        with Warehouse(warehouse_path, read_only=True) as warehouse:
            assert (report_card._card_scores(warehouse, DEADLINE)
                    == _weights_as_of(warehouse, DEADLINE))


class TestWeightsHelper:
    """The seam itself, so a caller that forgets to pass a moment cannot compile."""

    def test_weights_requires_a_moment(self, warehouse_path) -> None:
        with (
            Warehouse(warehouse_path, read_only=True) as warehouse,
            pytest.raises(TypeError),
        ):
            _weights_as_of(warehouse)  # type: ignore[call-arg]

    def test_every_creator_score_read_in_the_package_is_filtered(self) -> None:
        """No second, unfiltered path back into ``creator_score``.

        The defect was one query in one helper while the code around it did the
        right thing with the claims. If a raw ``FROM creator_score`` is ever
        reintroduced without an ``as_of`` bound, the leak comes back with no
        other symptom. The scan covers the whole creators package, because the
        board, the chatter panel and the report card all read the same table
        and only one of them is allowed to hold the query.
        """
        package = Path(inspect.getfile(identity)).parent
        reads: list[tuple[str, str]] = []
        for path in sorted(package.glob("*.py")):
            # Python string concatenation, comments and line breaks all get in
            # the way of reading the SQL off the source, so normalise first.
            source = re.sub(r'"\s*\n\s*"', "", path.read_text(encoding="utf-8"))
            for match in re.finditer(r"FROM creator_score\b", source):
                reads.append((path.name, source[match.end():match.end() + 40]))

        assert len(reads) == 1, (
            f"{len(reads)} reads of creator_score in the creators package "
            f"({[name for name, _ in reads]}); every one must be bounded by "
            f"as_of, so there should be exactly one, in _weights_as_of"
        )
        name, tail = reads[0]
        assert name == "identity.py", name
        assert re.match(r"\s*WHERE scope = 'all' AND as_of <= \?", tail), (
            "a read of creator_score is not bounded by as_of; a decision at a "
            "past instant can be weighted by a track record measured after it"
        )
