"""Survivorship bias: who is missing from the data, and who should be.

Hunt list item 4. Two directions, both wrong:

* players and clubs that LEFT are absent from any analysis built off the current
  bootstrap, so history looks like it was played by today's Premier League;
* players who left are STILL PRESENT in the point-in-time warehouse forever,
  frozen at their last observed price and status, because nothing marks a row
  as stale when the entity stops being reported.

The second is the sharper one, because it is invisible: the optimizer is offered
a January departure at his January price at GW30 and there is no NULL to notice.
"""

from __future__ import annotations

import datetime as dt

import pytest

from .conftest import UTC, frame, player_row, state_row

AUG = dt.datetime(2026, 8, 1, tzinfo=UTC)
JAN = dt.datetime(2027, 1, 15, tzinfo=UTC)
APR = dt.datetime(2027, 4, 15, tzinfo=UTC)


def test_a_player_who_left_the_league_goes_stale_not_silent(wh) -> None:
    """GUARDS: a departed player still being selectable at his last known price.

    FPL removes elements from the bootstrap when a player leaves the league.
    The warehouse is append-only and ``Snapshot.table`` takes the latest row per
    entity with no upper bound on age, so the last row written in January is
    still "the latest known value" in April.

    Result: ``Snapshot.players()`` at GW33 offers a player who has been in Saudi
    Arabia for three months, at his January price, with ``status='a'``, and the
    optimizer will happily buy him. There is no staleness column, no maximum
    age, and no ``last_seen`` to check.
    """
    wh.append("dim_player", frame([
        player_row(season="2026-27", code=1, element_id=10, as_of=AUG, web_name="Departed"),
    ]))
    wh.append("fact_player_state", frame([
        state_row(season="2026-27", code=1, element_id=10, as_of=JAN,
                  price_tenths=95, status="a"),
    ]))
    # ...and then he is never reported again.

    april = wh.snapshot_at(APR).players("2026-27")
    assert len(april) == 1
    row = april.iloc[0]
    age_days = (APR - row["as_of"]).total_seconds() / 86400.0
    assert age_days < 14, (
        f"a player last observed {age_days:.0f} days ago is still returned as "
        f"selectable at {row['price_tenths']}/10m with status {row['status']!r}. "
        "Snapshot.players() has no staleness bound, so a departed player never "
        "leaves the selectable universe"
    )


def test_unselectable_players_are_not_offered_as_selectable(live_wh) -> None:
    """GUARDS: the optimizer being handed players it cannot legally pick.

    ``Snapshot.players()`` is documented as returning "Squad-selectable players
    with price, ownership and availability". It applies no selectability filter
    at all. In the live 2026-27 bootstrap, 32 of 592 elements carry
    ``can_select: false`` and ``status: 'u'`` -- players who have left, are on
    long-term absence, or were never registered.

    Neither ``can_select`` nor ``removed`` is ingested, so downstream code
    cannot even reconstruct the filter. ``Availability.is_selectable`` exists in
    fpl_edge/types.py:82 and nothing in the read path calls it.
    """
    players = live_wh.snapshot_at(dt.datetime(2026, 8, 18, 23, tzinfo=UTC)).players("2026-27")
    if players.empty:
        pytest.skip("no players ingested")

    unselectable = players[players["status"] == "u"]
    has_flag = "can_select" in players.columns
    assert has_flag or unselectable.empty, (
        f"players() returned {len(unselectable)} players with status 'u' out of "
        f"{len(players)}, and carries no can_select column for a caller to "
        "filter on, despite promising 'squad-selectable players'"
    )


def test_a_relegated_club_keeps_its_history(wh) -> None:
    """GUARDS: history rewritten to contain only current Premier League clubs.

    Regression guard, currently correct. ``dim_team`` is keyed on
    (season, team_code), so a club relegated in 2026 keeps its 2025-26 rows.
    A model that instead filters historical matches to the twenty clubs in the
    CURRENT bootstrap throws away every match involving the three relegated
    sides -- roughly 15% of the fixtures, and systematically the easiest ones.
    """
    rows = [
        {"season": "2025-26", "team_code": 91, "team_id": 1, "name": "Relegated FC",
         "short_name": "REL", "as_of": AUG},
        {"season": "2026-27", "team_code": 3, "team_id": 1, "name": "Arsenal",
         "short_name": "ARS", "as_of": AUG},
    ]
    wh.append("dim_team", frame(rows))
    got = wh.snapshot_at(APR).table("dim_team")
    assert set(got["team_code"]) == {91, 3}
    assert set(got[got["season"] == "2025-26"]["team_code"]) == {91}


def test_promoted_club_definition_does_not_depend_on_the_current_bootstrap() -> None:
    """GUARDS: 'promoted' being decided from today's squad list.

    Regression guard for ``fpl_edge/models/team_goals/data.py``, which defines
    promoted as "no prior top-flight match on record" -- computed from the
    match history inside the snapshot, not from an external list of who is in
    the league now. That is the version that survives a walk-forward replay.
    """
    import pandas as pd

    from fpl_edge.models.team_goals.data import promoted_team_codes

    matches = pd.DataFrame([
        {"season": "2025-26", "home_team_code": 1, "away_team_code": 2},
        {"season": "2026-27", "home_team_code": 1, "away_team_code": 3},
    ])
    promoted = promoted_team_codes(matches, {1, 3}, season="2026-27")
    assert promoted == {3}, "club 3 has no prior match on record and is promoted"

    # A club with history from an EARLIER season is not promoted, even if it was
    # absent from the immediately preceding one.
    matches2 = pd.DataFrame([
        {"season": "2024-25", "home_team_code": 4, "away_team_code": 1},
        {"season": "2026-27", "home_team_code": 4, "away_team_code": 1},
    ])
    assert promoted_team_codes(matches2, {1, 4}, season="2026-27") == set()


def test_historical_squad_is_not_intersected_with_the_current_player_list(wh) -> None:
    """GUARDS: training only on players who still exist today.

    The canonical FPL survivorship bug: build the training set by taking the
    current bootstrap's list of codes and pulling their history. Every player
    who retired, was relegated with his club, or moved abroad is excluded --
    and those are exactly the players whose late-career decline the minutes
    model most needs to learn.

    This test constructs the situation and asserts the warehouse can still see
    the departed player's history, so any filtering is a deliberate choice by a
    caller rather than a property of the store.
    """
    wh.append("dim_player", frame([
        player_row(season="2025-26", code=111, element_id=1, as_of=AUG, web_name="Retired"),
        player_row(season="2026-27", code=222, element_id=1, as_of=AUG, web_name="Current"),
    ]))
    all_players = wh.snapshot_at(APR).table("dim_player")
    current_codes = set(all_players[all_players["season"] == "2026-27"]["code"])
    historical_codes = set(all_players[all_players["season"] == "2025-26"]["code"])

    assert historical_codes - current_codes == {111}, (
        "the store must retain players absent from the current season; if this "
        "fails, survivorship has been baked into the warehouse itself"
    )
