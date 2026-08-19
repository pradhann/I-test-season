"""Set-piece change detection, and what a change is worth.

Two halves. The first is a pure unit test of :func:`compare`, which is the entire
detection rule expressed over two dicts -- no warehouse, no archive, no clock, so
a disagreement about what counts as a change is settled by reading twelve lines.

The second runs the real archive walker over a two-poll fixture in
``tests/fixtures/intel/archive``, because the interesting bugs are not in the
comparison. They are in the bookkeeping around it: dating a duty to the poll that
first showed it rather than the poll being processed, and noticing that a player
who has *vanished* from the list has changed rather than simply stopped existing.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from fpl_edge.intel.items import Duty, value_duty_change
from fpl_edge.intel.setpieces import ALERT_GOALS_PER_GAME, compare, scan_archive

UTC = dt.timezone.utc

ARCHIVE = Path(__file__).resolve().parents[1] / "fixtures" / "intel" / "archive"

T0 = dt.datetime(2026, 8, 19, 8, tzinfo=UTC)
T1 = dt.datetime(2026, 8, 19, 10, tzinfo=UTC)


def duty_map(**by_code: int | None) -> dict:
    return {
        (int(code), Duty.PENALTIES): (order, None, 3)
        for code, order in by_code.items()
        if order is not None
    }


class TestValuation:
    """A change is priced in goals per game, so its size is comparable to xG."""

    def test_becoming_first_choice_is_worth_a_full_penalty_share(self) -> None:
        assert value_duty_change(Duty.PENALTIES, None, 1) == pytest.approx(0.10)

    def test_losing_first_choice_is_the_exact_negative(self) -> None:
        assert value_duty_change(Duty.PENALTIES, 1, None) == pytest.approx(-0.10)

    def test_the_backup_is_worth_far_less_than_half_the_starter(self) -> None:
        """Second choice takes a penalty only when the first is off or declines.

        A linear model would price him at half the starter, which is roughly
        five times too generous and would make every backup taker look like a
        differential.
        """
        second = value_duty_change(Duty.PENALTIES, None, 2)
        assert second == pytest.approx(0.05)
        assert value_duty_change(Duty.PENALTIES, None, 4) == pytest.approx(0.0125)

    def test_a_deep_shuffle_stays_below_the_alert_threshold(self) -> None:
        assert abs(value_duty_change(Duty.PENALTIES, 4, 3)) < ALERT_GOALS_PER_GAME

    def test_promotion_to_first_choice_always_clears_it(self) -> None:
        assert abs(value_duty_change(Duty.PENALTIES, 2, 1)) >= ALERT_GOALS_PER_GAME
        assert abs(value_duty_change(Duty.PENALTIES, None, 1)) >= ALERT_GOALS_PER_GAME


class TestCompare:
    def test_no_movement_produces_nothing(self) -> None:
        table = duty_map(**{"1": 1, "2": 2})
        assert compare(table, table, season="2026-27", prior_as_of=T0, detected_at=T1) == []

    def test_a_new_first_choice_taker_is_detected_and_named(self) -> None:
        changes = compare(
            duty_map(**{"1": 1}), duty_map(**{"1": 2, "2": 1}),
            season="2026-27", prior_as_of=T0, detected_at=T1,
            names={2: "Havertz", 1: "Saka"},
        )
        by_code = {c.code: c for c in changes}
        assert by_code[2].ord_before is None and by_code[2].ord_after == 1
        assert "FIRST-CHOICE" in by_code[2].headline and "Havertz" in by_code[2].headline
        assert by_code[2].is_promotion
        assert by_code[1].ord_before == 1 and by_code[1].ord_after == 2
        assert not by_code[1].is_promotion

    def test_dropping_off_the_list_is_a_change_not_a_disappearance(self) -> None:
        """The single most valuable thing this detector can say.

        A player absent from the later observation has to produce a change with
        ``ord_after=None``. Iterating only over the *new* table -- the obvious
        implementation -- silently drops exactly this case, and losing penalty
        duty is worth as much as gaining it.
        """
        changes = compare(
            duty_map(**{"1": 1}), {},
            season="2026-27", prior_as_of=T0, detected_at=T1, names={1: "Watkins"},
        )
        assert len(changes) == 1
        assert changes[0].ord_before == 1 and changes[0].ord_after is None
        assert changes[0].delta_goals_per_game == pytest.approx(-0.10)
        assert "NO LONGER" in changes[0].headline

    def test_both_observation_instants_are_carried_on_the_change(self) -> None:
        """Provenance: which two observations produced this claim."""
        change = compare(
            {}, duty_map(**{"1": 1}), season="2026-27", prior_as_of=T0, detected_at=T1,
        )[0]
        assert change.prior_as_of == T0
        assert change.detected_at == T1

    def test_change_ids_are_content_derived_so_replay_is_idempotent(self) -> None:
        args = dict(season="2026-27", prior_as_of=T0, detected_at=T1)
        first = compare({}, duty_map(**{"1": 1}), **args)
        again = compare({}, duty_map(**{"1": 1}), **args)
        assert [c.change_id for c in first] == [c.change_id for c in again]


class TestArchiveScan:
    @pytest.fixture(scope="class")
    def scan(self):
        return scan_archive(season="2026-27", directory=ARCHIVE)

    def test_it_reads_both_polls_in_order(self, scan) -> None:
        assert scan.polls == 2
        assert scan.first_poll == T0
        assert scan.last_poll == T1
        assert "2.0h" in scan.window_note()

    def test_the_three_real_changes_are_found_and_valued(self, scan) -> None:
        pens = {c.code: c for c in scan.changes if c.duty is Duty.PENALTIES}
        assert pens[444145].ord_after == 1                      # Havertz promoted
        assert pens[223340].ord_after == 2                      # Saka demoted
        assert pens[178301].ord_after is None                   # Watkins dropped
        assert pens[444145].delta_goals_per_game == pytest.approx(0.05)
        assert pens[178301].delta_goals_per_game == pytest.approx(-0.10)

    def test_only_material_changes_become_news_items(self, scan) -> None:
        assert {i.player_code for i in scan.items} == {444145, 223340, 178301}
        assert all(abs(c.delta_goals_per_game) >= ALERT_GOALS_PER_GAME for c in scan.alerts)

    def test_a_stable_duty_is_dated_to_when_it_was_FIRST_seen(self, scan) -> None:
        """Saka's corner duty is unchanged across both polls.

        It must carry the first poll's instant, not the last. Re-stamping an
        unchanged value on every poll would make a static set-piece order look
        like continuous breaking news, and would let a dossier claim the duty was
        confirmed minutes ago when it has not been touched in weeks.
        """
        corners = [
            d for d in scan.duties
            if d.duty is Duty.CORNERS_INDIRECT and d.code == 223340
        ]
        assert len(corners) == 1
        assert corners[0].as_of == T0

    def test_a_changed_duty_is_dated_to_the_poll_that_changed_it(self, scan) -> None:
        pens = [d for d in scan.duties if d.duty is Duty.PENALTIES and d.code == 444145]
        assert pens[0].ord == 1
        assert pens[0].as_of == T1

    def test_a_dropped_duty_leaves_a_null_row_rather_than_vanishing(self, scan) -> None:
        watkins = [d for d in scan.duties if d.duty is Duty.PENALTIES and d.code == 178301]
        assert len(watkins) == 1
        assert watkins[0].ord is None and watkins[0].as_of == T1

    def test_team_code_is_the_stable_code_not_the_per_season_id(self, scan) -> None:
        """``elements[].team`` is 1..20 and reassigned every August.

        The fixture gives Arsenal per-season id 1 and stable code 3. Storing the
        id would make this row point at whichever club sorts first next season.
        """
        saka = [d for d in scan.duties if d.code == 223340][0]
        assert saka.team_code == 3

    def test_scanning_up_to_the_first_poll_only_sees_the_old_order(self) -> None:
        early = scan_archive(season="2026-27", directory=ARCHIVE, until=T0)
        assert early.polls == 1
        assert early.changes == []
        pens = {d.code: d.ord for d in early.duties if d.duty is Duty.PENALTIES}
        assert pens == {223340: 1, 444145: 2, 178301: 1}

    def test_an_empty_directory_is_reported_not_crashed(self, tmp_path) -> None:
        empty = scan_archive(season="2026-27", directory=tmp_path)
        assert empty.polls == 0 and empty.changes == []
        assert "no archived polls" in empty.window_note()
