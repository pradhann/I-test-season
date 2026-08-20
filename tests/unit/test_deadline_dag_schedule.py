"""The schedule arithmetic: offsets, the London wall clock, and staleness.

These are pure functions over an explicit `now`, so every case here is a frozen
instant rather than a sleep. The GW1 deadline used throughout is the real one --
2026-08-21T17:30Z -- because the offsets in DESIGN.md §3 are only meaningful
against a real deadline, and a made-up round number would hide an off-by-an-hour.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.jobs.deadline_dag import (
    DEADLINE_OFFSETS,
    LOOKBACK,
    NIGHTLY_TASK,
    STALE_WINDOW,
    due_tasks,
    next_due,
    nightly_instants,
)

UTC = dt.timezone.utc

GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
GW2 = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
DEADLINES = [(1, GW1), (2, GW2)]


def owed(now: dt.datetime, deadlines=DEADLINES) -> dict[str, list]:
    out: dict[str, list] = {}
    for d in due_tasks(deadlines, now):
        out.setdefault(d.task, []).append(d)
    return out


# -- the three deadline-relative offsets ------------------------------------


@pytest.mark.parametrize(
    "task,offset_hours,expected",
    [
        ("presser_projection_refresh", 30, dt.datetime(2026, 8, 20, 11, 30, tzinfo=UTC)),
        ("final_solve_delivery", 4, dt.datetime(2026, 8, 21, 13, 30, tzinfo=UTC)),
        ("lineup_captain_check", 1.5, dt.datetime(2026, 8, 21, 16, 0, tzinfo=UTC)),
    ],
)
def test_offsets_match_the_design_table(task, offset_hours, expected):
    assert DEADLINE_OFFSETS[task] == dt.timedelta(hours=offset_hours)
    assert GW1 - DEADLINE_OFFSETS[task] == expected


@pytest.mark.parametrize("task", list(DEADLINE_OFFSETS))
def test_not_owed_one_second_before_its_due_instant(task):
    due = GW1 - DEADLINE_OFFSETS[task]
    assert task not in owed(due - dt.timedelta(seconds=1))


@pytest.mark.parametrize("task", list(DEADLINE_OFFSETS))
def test_owed_exactly_at_its_due_instant(task):
    due = GW1 - DEADLINE_OFFSETS[task]
    got = owed(due)
    assert task in got
    assert got[task][0].due_utc == due
    assert got[task][0].stale is False
    assert got[task][0].gw == 1


@pytest.mark.parametrize("task", list(DEADLINE_OFFSETS))
def test_fresh_inside_the_stale_window_and_stale_just_outside(task):
    due = GW1 - DEADLINE_OFFSETS[task]
    inside = owed(due + STALE_WINDOW - dt.timedelta(minutes=1))[task][0]
    outside = owed(due + STALE_WINDOW + dt.timedelta(minutes=1))[task][0]
    assert inside.stale is False
    assert outside.stale is True


def test_a_firing_older_than_the_lookback_is_forgotten_not_replayed():
    """Bounded honesty: a week-long outage must not write a week of rows."""
    due = GW1 - DEADLINE_OFFSETS["presser_projection_refresh"]
    assert "presser_projection_refresh" in owed(due + LOOKBACK - dt.timedelta(hours=1))
    assert "presser_projection_refresh" not in owed(due + LOOKBACK + dt.timedelta(hours=1))


def test_the_t90m_firing_still_precedes_the_deadline():
    """The alert has to arrive while it can still change something."""
    due = GW1 - DEADLINE_OFFSETS["lineup_captain_check"]
    assert due < GW1
    assert due + STALE_WINDOW > GW1  # ...and the stale window is wide enough to be lax
    # so the ordering guard that matters is the offset itself, not the window
    assert DEADLINE_OFFSETS["lineup_captain_check"] < DEADLINE_OFFSETS["final_solve_delivery"]


def test_each_gameweek_gets_its_own_firing_key():
    """Two deadlines, two due instants -- the primary key must separate them."""
    now = GW2 - DEADLINE_OFFSETS["final_solve_delivery"]
    got = due_tasks(DEADLINES, now)
    solves = [d for d in got if d.task == "final_solve_delivery"]
    assert [d.gw for d in solves] == [1, 2] or [d.gw for d in solves] == [2]
    keys = {d.key() for d in got}
    assert len(keys) == len(got)


# -- the one wall-clock task -------------------------------------------------


def test_nightly_is_0200_london_which_is_0100_utc_in_summer():
    now = dt.datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    got = nightly_instants(now, lookback=dt.timedelta(hours=12))
    assert got == [dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC)]


def test_nightly_is_0200_london_which_is_0200_utc_in_winter():
    now = dt.datetime(2026, 12, 10, 6, 0, tzinfo=UTC)
    got = nightly_instants(now, lookback=dt.timedelta(hours=12))
    assert got == [dt.datetime(2026, 12, 10, 2, 0, tzinfo=UTC)]


def test_spring_forward_day_shifts_the_utc_instant_by_an_hour():
    """2027-03-28: clocks go 01:00 GMT -> 02:00 BST. 02:00 local still exists.

    The day before, 02:00 London is 02:00Z; on the transition day it is 01:00Z.
    Adding 24h to the previous instant would give the wrong answer, which is why
    the implementation walks local dates instead.
    """
    before = nightly_instants(
        dt.datetime(2027, 3, 27, 12, 0, tzinfo=UTC), lookback=dt.timedelta(hours=12)
    )
    on_day = nightly_instants(
        dt.datetime(2027, 3, 28, 12, 0, tzinfo=UTC), lookback=dt.timedelta(hours=12)
    )
    assert before == [dt.datetime(2027, 3, 27, 2, 0, tzinfo=UTC)]
    assert on_day == [dt.datetime(2027, 3, 28, 1, 0, tzinfo=UTC)]
    assert on_day[0] - before[0] == dt.timedelta(hours=23)


def test_autumn_back_day_shifts_it_the_other_way():
    """2026-10-25: clocks go 02:00 BST -> 01:00 GMT. 02:00 local is unambiguous."""
    before = nightly_instants(
        dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC), lookback=dt.timedelta(hours=12)
    )
    on_day = nightly_instants(
        dt.datetime(2026, 10, 25, 12, 0, tzinfo=UTC), lookback=dt.timedelta(hours=12)
    )
    # 24 Oct is still BST, so 02:00 local is 01:00Z; 25 Oct is GMT, so 02:00Z.
    assert before == [dt.datetime(2026, 10, 24, 1, 0, tzinfo=UTC)]
    assert on_day == [dt.datetime(2026, 10, 25, 2, 0, tzinfo=UTC)]
    assert on_day[0] - before[0] == dt.timedelta(hours=25)


def test_nightly_never_emits_a_future_instant():
    now = dt.datetime(2026, 8, 20, 0, 30, tzinfo=UTC)  # before 01:00Z today
    got = nightly_instants(now, lookback=dt.timedelta(hours=12))
    assert all(g <= now for g in got)
    assert dt.datetime(2026, 8, 20, 1, 0, tzinfo=UTC) not in got


def test_nightly_is_filed_under_the_gameweek_it_runs_up_to():
    now = dt.datetime(2026, 8, 20, 6, 0, tzinfo=UTC)
    radar = [d for d in due_tasks(DEADLINES, now) if d.task == NIGHTLY_TASK]
    assert radar and all(d.gw == 1 for d in radar)


# -- next_due, the reporting surface -----------------------------------------


def test_next_due_reports_all_four_tasks_ahead_of_gw1():
    now = dt.datetime(2026, 8, 20, 6, 15, tzinfo=UTC)
    got = dict(next_due(DEADLINES, now))
    assert got["presser_projection_refresh"] == dt.datetime(2026, 8, 20, 11, 30, tzinfo=UTC)
    assert got["final_solve_delivery"] == dt.datetime(2026, 8, 21, 13, 30, tzinfo=UTC)
    assert got["lineup_captain_check"] == dt.datetime(2026, 8, 21, 16, 0, tzinfo=UTC)
    assert got[NIGHTLY_TASK] == dt.datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    assert all(v > now for v in got.values())
