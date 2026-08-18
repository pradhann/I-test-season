"""Snapshot-mediated reads for the goal model.

Nothing in this package touches ``Warehouse`` directly. Every historical match
arrives through :class:`~fpl_edge.store.Snapshot`, whose ``as_of`` filter is what
makes a walk-forward backtest honest. The belt-and-braces assertion in
:func:`read_finished_matches` is there because the snapshot guarantees *fact
visibility* ordering, not *event time* ordering: a warehouse row mis-stamped
with an ``as_of`` before its own kickoff would slip through the SQL filter and
silently leak a result into training. We check kickoff against ``as_of`` too,
and raise rather than train on it.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from fpl_edge.store import LeakageError, Snapshot

MATCH_COLUMNS = (
    "season",
    "fixture_id",
    "gw",
    "kickoff_utc",
    "home_team_code",
    "away_team_code",
    "home_score",
    "away_score",
)


class InsufficientHistoryError(RuntimeError):
    """Raised when a snapshot carries too little history to fit a goal model.

    Deliberately fatal. The failure mode this exists to prevent is a model
    quietly returning league-average ratings for all twenty clubs because the
    historical seasons never loaded, which looks like a working model and is
    worth nothing.
    """


def read_finished_matches(snapshot: Snapshot, *, min_matches: int = 0) -> pd.DataFrame:
    """Every finished match visible at ``snapshot.as_of``, all seasons.

    One query: ``fact_fixture`` is point-in-time keyed on ``(season, fixture_id)``
    so the snapshot already returns the latest row per fixture known at the
    as-of instant, which is NULL-scored before kickoff and scored afterwards.
    """
    fx = snapshot.table("fact_fixture")
    if fx.empty:
        if min_matches:
            raise InsufficientHistoryError(
                f"no fixtures at all visible at {snapshot.as_of}; the historical "
                f"seasons have not been loaded into the warehouse"
            )
        return pd.DataFrame(columns=list(MATCH_COLUMNS))
    done = fx[
        fx["home_score"].notna() & fx["away_score"].notna() & fx["kickoff_utc"].notna()
    ].copy()
    if not done.empty:
        future = done[done["kickoff_utc"] >= snapshot.as_of]
        if not future.empty:
            raise LeakageError(
                f"{len(future)} finished matches have kickoff at or after the "
                f"snapshot instant {snapshot.as_of}; the as_of stamps on "
                f"fact_fixture are wrong and training on them would leak"
            )
    done = done[list(MATCH_COLUMNS)].astype(
        {"home_score": int, "away_score": int, "home_team_code": int, "away_team_code": int}
    )
    done = done.sort_values(["kickoff_utc", "fixture_id"]).reset_index(drop=True)
    if len(done) < min_matches:
        raise InsufficientHistoryError(
            f"only {len(done)} finished matches visible at {snapshot.as_of}, "
            f"need at least {min_matches} to fit a team goal model"
        )
    return done


def read_target_fixtures(snapshot: Snapshot, season: str, gws: list[int]) -> pd.DataFrame:
    """Not-yet-played fixtures for the requested gameweeks.

    Goes through ``Snapshot.upcoming_fixtures``, which filters on
    ``kickoff_utc > as_of``. A fixture already kicked off cannot be predicted
    without leaking, so it is absent by construction rather than by convention.
    """
    fx = snapshot.upcoming_fixtures(season)
    if fx.empty:
        return pd.DataFrame(columns=list(MATCH_COLUMNS))
    fx = fx[fx["gw"].isin(gws)].copy()
    if fx.empty:
        return pd.DataFrame(columns=list(MATCH_COLUMNS))
    return fx[list(MATCH_COLUMNS)].sort_values(["gw", "kickoff_utc"]).reset_index(drop=True)


def season_order(seasons: pd.Series | list[str]) -> list[str]:
    """FPL season labels ('2024-25') sort correctly as plain strings."""
    return sorted({str(s) for s in seasons})


def teams_in_season(matches: pd.DataFrame, season: str) -> set[int]:
    sub = matches[matches["season"] == season]
    return set(sub["home_team_code"]) | set(sub["away_team_code"])


def teams_with_history(matches: pd.DataFrame, *, before_season: str | None = None) -> set[int]:
    """Team codes with at least one finished match, optionally before a season."""
    sub = matches if before_season is None else matches[matches["season"] < before_season]
    if sub.empty:
        return set()
    return set(sub["home_team_code"]) | set(sub["away_team_code"])


def promoted_team_codes(
    matches: pd.DataFrame, target_teams: set[int], *, season: str
) -> set[int]:
    """Teams appearing in ``season`` with no prior top-flight match on record.

    This is the operative definition of "promoted" for the model: not "won the
    Championship" but "we have zero observations of this club at this level", so
    a returning club with history from three seasons ago is *not* treated as
    promoted, while a genuine newcomer is.
    """
    return set(target_teams) - teams_with_history(matches, before_season=season)


def as_of_of(snapshot: Snapshot) -> dt.datetime:
    return snapshot.as_of
