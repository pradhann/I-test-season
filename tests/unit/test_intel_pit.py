"""Point-in-time invisibility: the property the whole intel package exists for.

If a single test in this repository is worth reading, it is this one. Every other
guarantee the intel layer offers is a convenience; this is the one that decides
whether a backtest built on it means anything.

The claim under test is narrow and absolute: **an intel item published at time T
is invisible to every snapshot taken before T, and visible to every snapshot
taken at or after it.** Not "usually", not "for the tables we remembered to
filter" -- for every read path on :class:`~fpl_edge.intel.store.IntelStore`.

The failure this prevents is not hypothetical and does not announce itself. An
injury feed with no publication timestamp dates every injury to the moment the
scraper ran, so a model replaying August 2025 "knows" about a hamstring that
tore in September. Nothing crashes. The backtest simply returns a number that is
too good, and the number is the only thing anyone looks at.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.intel.items import (
    Duty,
    DutyChange,
    FormationObservation,
    IntelItem,
    IntelKind,
    OopSignal,
    SetPieceDuty,
    value_duty_change,
)
from fpl_edge.intel.store import IntelStore
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc


def at(day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 8, day, hour, tzinfo=UTC)


@pytest.fixture()
def store(tmp_path) -> IntelStore:
    return IntelStore(Warehouse(tmp_path / "pit.duckdb"))


def _item(day: int, *, code: int = 1, observed_day: int | None = None) -> IntelItem:
    published = at(day)
    return IntelItem(
        item_id=f"item-{day}-{code}",
        published_at=published,
        # Observed later than published on purpose: the point of the test is
        # that filtering keys off publication, not off when we happened to look.
        observed_at=at(observed_day if observed_day is not None else day + 1),
        kind=IntelKind.AVAILABILITY,
        headline=f"news on day {day}",
        source="test",
        season="2026-27",
        player_code=code,
    )


class TestItemsAreInvisibleBeforePublication:
    def test_future_item_is_not_returned(self, store: IntelStore) -> None:
        store.put_items([_item(20)])
        assert store.items(at(19)) == []
        assert len(store.items(at(20))) == 1
        assert len(store.items(at(21))) == 1

    def test_boundary_is_inclusive_at_the_publication_instant(self, store: IntelStore) -> None:
        """A deadline read must see news published exactly at the deadline.

        Exclusive would be the safer-looking choice and it is wrong: FPL flags a
        player at 11:00:00 and the deadline is 11:00:00, the manager saw it.
        """
        store.put_items([_item(20)])
        one_second_before = at(20) - dt.timedelta(seconds=1)
        assert store.items(one_second_before) == []
        assert len(store.items(at(20))) == 1

    def test_a_late_observation_does_not_hide_a_public_fact(self, store: IntelStore) -> None:
        """Published on the 18th, we noticed on the 25th: still visible on the 19th.

        The opposite behaviour -- filtering on ``observed_at`` -- looks more
        conservative and is a different bug: it hides from the backtest things
        the manager genuinely knew, which flatters the model's excuses rather
        than its results.
        """
        store.put_items([_item(18, observed_day=25)])
        visible = store.items(at(19))
        assert len(visible) == 1
        assert visible[0].lag == dt.timedelta(days=7)

    def test_every_filter_still_applies_the_time_bound(self, store: IntelStore) -> None:
        store.put_items([_item(20, code=7), _item(10, code=7)])
        for kwargs in (
            {"player_code": 7},
            {"kind": IntelKind.AVAILABILITY},
            {"season": "2026-27"},
            {"limit": 50},
        ):
            assert len(store.items(at(15), **kwargs)) == 1, kwargs
            assert len(store.items(at(25), **kwargs)) == 2, kwargs

    def test_observed_before_published_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="precedes published_at"):
            IntelItem(
                item_id="impossible",
                published_at=at(20),
                observed_at=at(19),
                kind=IntelKind.AVAILABILITY,
                headline="we saw it before it happened",
                source="test",
            )


class TestDutiesAndChangesRespectTheSameBound:
    def test_duty_read_returns_the_state_as_it_then_stood(self, store: IntelStore) -> None:
        store.put_duties([
            SetPieceDuty(season="2026-27", code=1, duty=Duty.PENALTIES, ord=2,
                         as_of=at(10), source="test"),
            SetPieceDuty(season="2026-27", code=1, duty=Duty.PENALTIES, ord=1,
                         as_of=at(20), source="test"),
        ])
        assert store.duties(at(15), season="2026-27", code=1)[0].ord == 2
        assert store.duties(at(25), season="2026-27", code=1)[0].ord == 1
        assert store.duties(at(5), season="2026-27", code=1) == []

    def test_losing_a_duty_is_representable_and_time_bound(self, store: IntelStore) -> None:
        """``ord=None`` is a state, not a deletion.

        A player dropped from the penalty list must read as "not on it" after the
        drop and as "first choice" before it. If the drop were modelled by
        removing the row, the as-of read would keep returning the stale duty for
        every past AND future instant.
        """
        store.put_duties([
            SetPieceDuty(season="2026-27", code=1, duty=Duty.PENALTIES, ord=1,
                         as_of=at(10), source="test"),
            SetPieceDuty(season="2026-27", code=1, duty=Duty.PENALTIES, ord=None,
                         as_of=at(20), source="test"),
        ])
        assert store.duties(at(15), season="2026-27", code=1)[0].is_first_choice
        assert store.duties(at(25), season="2026-27", code=1)[0].ord is None

    def test_change_is_invisible_before_detection(self, store: IntelStore) -> None:
        store.put_changes([
            DutyChange(
                change_id="c1", season="2026-27", code=1, duty=Duty.PENALTIES,
                ord_before=None, ord_after=1, prior_as_of=at(10), detected_at=at(20),
                delta_goals_per_game=value_duty_change(Duty.PENALTIES, None, 1),
                headline="new first-choice taker",
            )
        ])
        assert store.changes(at(19)) == []
        assert len(store.changes(at(20))) == 1

    def test_a_change_cannot_predate_the_observation_it_follows(self) -> None:
        with pytest.raises(ValueError, match="cannot be detected before"):
            DutyChange(
                change_id="bad", season="2026-27", code=1, duty=Duty.PENALTIES,
                ord_before=1, ord_after=None, prior_as_of=at(20), detected_at=at(10),
                delta_goals_per_game=0.0, headline="time travel",
            )


class TestOtherTablesRespectTheSameBound:
    def test_oop_signal(self, store: IntelStore) -> None:
        store.put_oop([
            OopSignal(season="2026-27", code=1, fpl_position=2, plays_like=3,
                      score=0.8, evidence="", as_of=at(20)),
        ])
        assert store.oop(at(19), season="2026-27") == []
        assert len(store.oop(at(21), season="2026-27")) == 1

    def test_formation_observation(self, store: IntelStore) -> None:
        store.put_formations([
            FormationObservation(season="2026-27", team_code=1, fixture_id=1, gw=1,
                                 n_def=3, n_mid=5, n_fwd=2, as_of=at(20)),
        ])
        assert store.formations(at(19), season="2026-27").empty
        after = store.formations(at(21), season="2026-27")
        assert len(after) == 1 and after.iloc[0]["shape"] == "3-5-2"


class TestNoReadPathSkipsTheFilter:
    """A structural check, not a behavioural one.

    The behavioural tests above cover the read methods that exist today. This one
    fails when someone adds a new one and forgets, which is the realistic way the
    guarantee gets lost -- nobody removes a filter on purpose.
    """

    def test_every_as_of_read_binds_the_bound(self, store: IntelStore) -> None:
        import inspect

        exempt = {
            # Deliberately unfiltered: a probe is a fact about our access to a
            # website, not about the football season. Documented on the method.
            "probes",
            "counts", "migrate", "tables_exist", "open_reader",
        }
        readers = [
            name for name, fn in inspect.getmembers(IntelStore, inspect.isfunction)
            if not name.startswith("_") and not name.startswith("put_")
            and name not in exempt
        ]
        assert set(readers) == {"items", "duties", "changes", "oop", "formations"}, (
            "a new read method appeared on IntelStore. Add it to this list and give "
            "it an as-of test above, or add it to `exempt` with a written reason."
        )
        for name in readers:
            source = inspect.getsource(getattr(IntelStore, name))
            assert "as_of" in source or "published_at" in source or "detected_at" in source
            assert "<= ?" in source, f"{name} does not bind an upper time bound"
