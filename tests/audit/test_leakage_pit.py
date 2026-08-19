"""Point-in-time leakage that the SQL filter cannot catch.

Hunt list item 1, dynamic half. ``tests/unit/test_warehouse_pit.py`` already
proves the ``as_of <= t`` filter works. These tests attack the assumption
underneath it: that ``as_of`` is honest, and that the Snapshot is actually a
boundary rather than a suggestion.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.store import Snapshot

from .conftest import (
    GW1_DEADLINE,
    TODAY,
    UTC,
    fixture_row,
    frame,
    player_row,
    result_row,
    state_row,
)

KICKOFF = dt.datetime(2026, 8, 21, 19, 0, tzinfo=UTC)


def test_snapshot_does_not_expose_the_raw_warehouse(wh) -> None:
    """GUARDS: a public warehouse handle on Snapshot lets one line read the
    entire future -- ``snapshot.warehouse.sql(...)`` -- while looking like
    ordinary code in review.

    This test originally demonstrated the hole. The handle is now private and
    raises LeakageError, so the test asserts the refusal instead.
    """
    from fpl_edge.store.warehouse import LeakageError

    snap = wh.snapshot_at(GW1_DEADLINE)
    assert snap.results_before("2026-27").empty, "sanctioned read is correctly empty"

    with pytest.raises(LeakageError, match="bypasses point-in-time"):
        _ = snap.warehouse

    # The escape hatch still exists for genuine introspection, but it is
    # explicit, greppable, and demands a written justification.
    raw = snap.escape_hatch_unfiltered(
        "audit test verifying the sanctioned path differs from the raw one"
    )
    assert raw is wh


def test_snapshot_cannot_be_handed_a_future_as_of_through_its_constructor(wh) -> None:
    """GUARDS: bypassing ``snapshot_at``'s tz validation.

    ``Warehouse.snapshot_at`` is described as "the only sanctioned entry point".
    ``Snapshot`` itself is a public, importable, constructible dataclass with no
    validation in ``__post_init__``, so ``Snapshot(wh, naive_datetime)`` builds
    an unchecked one and every downstream comparison against ``as_of`` then
    mixes naive and aware datetimes or silently misfilters.
    """
    with pytest.raises((ValueError, TypeError)):
        Snapshot(wh, dt.datetime(2026, 8, 21, 17, 30))  # naive, no validation  # noqa: DTZ001  (the naive datetime IS the test)


def test_result_stamped_before_its_own_kickoff_is_refused(wh) -> None:
    """GUARDS: a backfill stamping results as observable before the match.

    The whole point-in-time design rests on ``as_of`` meaning "first publicly
    observable". Nothing checks it. A historical loader that stamps every row
    with the season start date -- an extremely natural thing to write -- makes
    the entire season's results visible at GW1 and the SQL filter waves it
    through, because the filter only compares as_of to the snapshot instant.

    ``fpl_edge/models/team_goals/data.py`` defends against exactly this at read
    time for fixtures. The write path does not, and nothing defends
    ``fact_player_fixture`` at all.
    """
    wh.append("fact_fixture", frame([
        fixture_row(season="2026-27", fixture_id=1, gw=1, kickoff_utc=KICKOFF,
                    as_of=dt.datetime(2026, 8, 1, tzinfo=UTC)),
    ]))
    too_early = result_row(
        season="2026-27", code=1, fixture_id=1, gw=1,
        as_of=dt.datetime(2026, 8, 1, tzinfo=UTC),  # a fortnight before kickoff
        total_points=13,
    )
    with pytest.raises((ValueError, RuntimeError), match="(?i)as_of|kickoff|leak"):
        wh.append("fact_player_fixture", frame([too_early]))


def test_finished_score_stamped_before_kickoff_is_refused(wh) -> None:
    """GUARDS: the same lie on fact_fixture, where scores live.

    ``Snapshot.upcoming_fixtures`` claims result columns "are NULL here by
    construction". They are NULL only if as_of is honest.
    """
    lying = fixture_row(
        season="2026-27", fixture_id=1, gw=1, kickoff_utc=KICKOFF,
        as_of=dt.datetime(2026, 8, 1, tzinfo=UTC),
        home_score=2, away_score=1,
    )
    with pytest.raises((ValueError, RuntimeError), match="(?i)as_of|kickoff|leak|finish"):
        wh.append("fact_fixture", frame([lying]))


def test_upcoming_fixtures_hides_a_match_already_kicked_off(wh) -> None:
    """GUARDS: predicting a fixture that has started.

    Regression guard, currently correct. The deadline is 90 minutes before the
    first kickoff, so at GW1's deadline all ten matches are legitimately
    upcoming; an hour into the first match only nine are.
    """
    rows = [
        fixture_row(season="2026-27", fixture_id=1, gw=1, kickoff_utc=KICKOFF,
                    as_of=dt.datetime(2026, 8, 1, tzinfo=UTC)),
        fixture_row(season="2026-27", fixture_id=2, gw=1,
                    kickoff_utc=dt.datetime(2026, 8, 22, 14, 0, tzinfo=UTC),
                    as_of=dt.datetime(2026, 8, 1, tzinfo=UTC),
                    home_team_code=3, away_team_code=4),
    ]
    wh.append("fact_fixture", frame(rows))

    at_deadline = wh.snapshot_at(GW1_DEADLINE).upcoming_fixtures("2026-27")
    assert set(at_deadline["fixture_id"]) == {1, 2}

    mid_match = wh.snapshot_at(KICKOFF + dt.timedelta(hours=1)).upcoming_fixtures("2026-27")
    assert set(mid_match["fixture_id"]) == {2}, (
        "a fixture already in progress is still being offered as predictable"
    )


def test_next_gw_does_not_return_a_gameweek_whose_deadline_has_passed(wh) -> None:
    """GUARDS: building a squad for a gameweek that is already locked.

    Regression guard, currently correct: ``next_gw`` uses a strict ``>``, so at
    exactly 17:30:00 GW1 is closed. Worth pinning because the natural-looking
    ``>=`` version is wrong in the direction that loses points silently.
    """
    wh.append("dim_event", frame([
        {"season": "2026-27", "gw": 1, "deadline_utc": GW1_DEADLINE,
         "is_finished": False, "as_of": dt.datetime(2026, 8, 1, tzinfo=UTC)},
        {"season": "2026-27", "gw": 2,
         "deadline_utc": dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC),
         "is_finished": False, "as_of": dt.datetime(2026, 8, 1, tzinfo=UTC)},
    ]))
    assert wh.snapshot_at(GW1_DEADLINE - dt.timedelta(seconds=1)).next_gw("2026-27") == 1
    assert wh.snapshot_at(GW1_DEADLINE).next_gw("2026-27") == 2
    assert wh.snapshot_at(GW1_DEADLINE + dt.timedelta(minutes=1)).next_gw("2026-27") == 2


# ---------------------------------------------------------------------------
# GW1 cold start. Not a bug -- a condition. Stated as a test so nobody can
# quietly pretend there is history.
# ---------------------------------------------------------------------------


def test_gw1_cold_start_is_real_in_the_live_warehouse(live_wh) -> None:
    """DOCUMENTS: at the 2026-27 GW1 deadline there is no current-season data.

    Not a defect. A fact that every model card and every confidence interval
    must respect: zero minutes played, zero results, zero price movements, zero
    ownership history for the season being predicted. Ownership at this moment
    is preseason speculation by a field that is still growing.

    This test fails if someone loads 2026-27 results that cannot exist yet.
    """
    snap = live_wh.snapshot_at(GW1_DEADLINE)
    results = snap.results_before("2026-27")
    assert results.empty, (
        f"{len(results)} 2026-27 result rows are visible at the GW1 deadline. "
        "GW1 has not been played; this data cannot exist"
    )

    fixtures = live_wh.sql(
        "SELECT count(*) n FROM fact_fixture WHERE season = '2026-27' AND finished"
    )
    assert int(fixtures.iloc[0]["n"]) == 0, "a 2026-27 fixture is marked finished"


def test_price_is_flat_before_the_season_starts(live_wh) -> None:
    """DOCUMENTS: the price signal carries no information at GW1.

    ``prices.no_change_before_season`` is a verified rule. Every price-based
    feature -- price rises, transfer momentum, value-for-money trends -- is
    therefore constant across all players until after GW1's deadline. A model
    weighting them at GW1 is weighting noise.
    """
    from fpl_edge.rules import rules

    assert rules().get("prices.no_change_before_season") is True

    changes = live_wh.sql(
        "SELECT count(*) n FROM fact_player_state "
        "WHERE season = '2026-27' AND cost_change_start <> 0"
    )
    assert int(changes.iloc[0]["n"]) == 0, (
        "prices have moved before the season started, which contradicts the "
        "verified rule; either the rule or the ingestion is wrong"
    )


def test_ownership_at_gw1_is_a_growing_denominator(live_wh) -> None:
    """DOCUMENTS: effective ownership at GW1 is measured against a moving field.

    ``misc.total_players_at_fetch`` is recorded as 5,896,644 with the explicit
    note that the field is still growing. The archived bootstrap read that same
    day already says 5,898,206. Every ``selected_by_percent`` is a percentage of
    a denominator that will be substantially larger by the deadline, so the
    rank-utility objective's field model is calibrated against a number that is
    wrong by construction at exactly the moment it matters most.
    """
    from fpl_edge.rules import rules

    recorded = rules().get("misc.total_players_at_fetch")
    assert recorded > 0

    players = live_wh.snapshot_at(TODAY).players("2026-27")
    if players.empty:
        pytest.skip("no 2026-27 players ingested")
    total_own = float(players["selected_by_pct"].sum())
    # 15 squad slots per manager, so ownership shares sum to at most ~1500%.
    assert 0 < total_own < 1500.0, (
        f"ownership percentages sum to {total_own:.1f}, which is not a "
        "coherent share of a 15-player squad"
    )


def test_history_stops_before_the_season_being_predicted(live_wh) -> None:
    """DOCUMENTS the exact shape of the GW1 cold start.

    Historical seasons have now been loaded, so the cold start is narrower than
    "no data at all" -- but it is not gone. There are ZERO minutes played in
    2026-27, so every current-season feature (form, minutes share, price
    trajectory, set-piece role under the new manager) is undefined and every
    model is extrapolating across a summer transfer window.

    The test asserts the boundary rather than the absence: prior seasons may
    exist, the predicted season must be empty.
    """
    seasons = sorted(set(live_wh.sql("SELECT DISTINCT season FROM dim_player")["season"]))
    assert "2026-27" in seasons, "the season being predicted must be present"

    played = live_wh.sql(
        "SELECT count(*) n FROM fact_player_fixture WHERE season = '2026-27'"
    )
    assert int(played.iloc[0]["n"]) == 0, (
        f"{int(played.iloc[0]['n'])} rows of 2026-27 player-fixture data exist. "
        "GW1 has not kicked off; this data cannot be real"
    )

    prior = [s for s in seasons if s < "2026-27"]
    minutes = live_wh.sql(
        "SELECT season, sum(minutes) m FROM fact_player_fixture GROUP BY season"
    )
    assert set(minutes["season"]) <= set(prior), (
        "minutes are recorded for a season that has not been played"
    )


def test_state_and_dim_rows_are_written_with_the_same_as_of(wh) -> None:
    """DOCUMENTS: players() joins an identity row to a state row, and the two
    can carry different as_of values.

    A single max() over the pair hides the case where a price is two weeks
    staler than the identity it is attached to, and the caller has no way to
    notice. The join is legitimate -- the fix is that the skew must be
    *visible*, so players() now reports identity_as_of and state_as_of
    separately alongside the combined as_of.
    """
    wh.append("dim_player", frame([player_row(season="2026-27", code=1, element_id=1, as_of=TODAY)]))
    stale = TODAY - dt.timedelta(days=14)
    wh.append("fact_player_state",
              frame([state_row(season="2026-27", code=1, element_id=1, as_of=stale)]))

    got = wh.snapshot_at(GW1_DEADLINE).players("2026-27")
    assert not got.empty
    assert {"identity_as_of", "state_as_of", "as_of"} <= set(got.columns), (
        "players() must expose both timestamps so staleness is detectable"
    )

    row = got.iloc[0]
    skew = abs((row["identity_as_of"] - row["state_as_of"]).total_seconds())
    assert skew > 0, "the fixture deliberately creates a skew"
    # The combined column remains the conservative later of the two.
    assert row["as_of"] == max(row["identity_as_of"], row["state_as_of"])

