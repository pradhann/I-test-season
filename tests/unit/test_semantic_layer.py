"""The semantic layer's contract: stable columns, and point-in-time honesty.

The macros in ``store/views.sql`` are the query surface for chat, the UI and
the MCP server. Two promises are tested here because breaking either silently
corrupts every consumer at once:

* **Point in time** — a fact recorded after ``p_as_of`` is invisible, exactly
  as with ``Snapshot.table()``. Without this, "what was knowable at the
  deadline" quietly becomes "what we know now" and every backtest lies.
* **Column stability** — columns may be added, never renamed or removed. The
  MCP server and panels select by name.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

# Registers the manager tables' PIT keys so wh.append accepts them; the tables
# themselves are part of the base schema (see store/schema.sql).
import fpl_edge.ingest.rivals.schema  # noqa: F401  (import for side effect)
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc


def T(day: int, hour: int = 12) -> dt.datetime:
    return dt.datetime(2026, 8, day, hour, tzinfo=UTC)


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    w = Warehouse(tmp_path / "sem.duckdb")
    w.append("dim_player", pd.DataFrame([{
        "season": "2026-27", "code": 1, "element_id": 11, "web_name": "Haaland",
        "first_name": "E", "second_name": "H", "position": 4, "team_code": 43,
        "as_of": T(1),
    }]))
    w.append("dim_team", pd.DataFrame([{
        "season": "2026-27", "team_code": 43, "team_id": 13,
        "name": "Man City", "short_name": "MCI", "as_of": T(1),
    }]))
    w.append("fact_player_state", pd.DataFrame([{
        "season": "2026-27", "code": 1, "element_id": 11, "price_tenths": 155,
        "selected_by_pct": 69.2, "status": "a", "chance_of_playing_next_round": None,
        "news": "", "news_added": None, "transfers_in_event": 0,
        "transfers_out_event": 0, "cost_change_start": 0, "as_of": T(1),
    }]))
    # The projection tables live behind the projections package's own
    # versioned migrations; a fresh warehouse gets them by constructing the
    # store, exactly as ingestion does.
    ProjectionStore(w)
    return w


def _proj(wh: Warehouse, *, day: int, xp: float, provider: str = "fplform") -> None:
    ProjectionStore(wh).append("fact_projection", pd.DataFrame([{
        "provider": provider, "season": "2026-27", "gw": 2, "code": 1,
        "xp": xp, "xp_if_appears": None, "p_appear": 0.9, "xmins": None,
        "as_of": T(day),
    }]))


def _sem(wh: Warehouse, macro: str, at: dt.datetime) -> pd.DataFrame:
    return wh.sql(f"SELECT * FROM {macro}(TIMESTAMPTZ '{at.isoformat()}')")


def test_a_price_change_after_the_deadline_is_invisible(wh: Warehouse) -> None:
    """The core PIT promise, on the macro every other macro joins."""
    wh.append("fact_player_state", pd.DataFrame([{
        "season": "2026-27", "code": 1, "element_id": 11, "price_tenths": 156,
        "selected_by_pct": 70.0, "status": "a", "chance_of_playing_next_round": None,
        "news": "", "news_added": None, "transfers_in_event": 0,
        "transfers_out_event": 0, "cost_change_start": 1, "as_of": T(20),
    }]))
    at_deadline = _sem(wh, "sem_players", T(10))
    later = _sem(wh, "sem_players", T(21))
    assert float(at_deadline.iloc[0]["price"]) == 15.5, "saw a price from the future"
    assert float(later.iloc[0]["price"]) == 15.6


def test_a_projection_fetched_after_the_instant_is_invisible(wh: Warehouse) -> None:
    for day, xp in ((5, 6.0), (18, 7.5)):
        _proj(wh, day=day, xp=xp)
    early = _sem(wh, "sem_projections", T(10))
    late = _sem(wh, "sem_projections", T(19))
    assert float(early.iloc[0]["xpts"]) == 6.0
    assert float(late.iloc[0]["xpts"]) == 7.5, "the newer fetch must win once visible"
    assert len(early) == 1 and len(late) == 1, "one row per (source, gw, code)"


def test_consensus_measures_disagreement_not_agreement_theatre(wh: Warehouse) -> None:
    for provider, xp in (("fplform", 4.0), ("gh_blueladd", 6.0), ("fpl_ep", 5.0)):
        _proj(wh, day=5, xp=xp, provider=provider)
    c = _sem(wh, "sem_projection_consensus", T(10)).iloc[0]
    assert int(c["n_sources"]) == 3
    assert float(c["xpts_mean"]) == 5.0
    assert float(c["xpts_spread"]) == 2.0
    assert c["web_name"] == "Haaland"


def test_form_respects_points_finalisation_time(wh: Warehouse) -> None:
    """A result's as_of is finalisation; before it the gameweek is absent."""
    wh.append("fact_fixture", pd.DataFrame([{
        "season": "2026-27", "fixture_id": 9, "gw": 1, "kickoff_utc": T(21, 19),
        "home_team_code": 43, "away_team_code": 3, "finished": False,
        "home_score": None, "away_score": None, "as_of": T(1),
    }]))
    wh.append("fact_player_fixture", pd.DataFrame([{
        "season": "2026-27", "code": 1, "fixture_id": 9, "gw": 1, "minutes": 90,
        "goals_scored": 2, "assists": 0, "clean_sheets": 0, "goals_conceded": 1,
        "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
        "yellow_cards": 0, "red_cards": 0, "saves": 0, "bonus": 3, "bps": 60,
        "starts": 1, "tackles": 0, "clearances_blocks_interceptions": 0,
        "recoveries": 0, "defensive_contribution": 0, "expected_goals": 1.4,
        "expected_assists": 0.2, "expected_goals_conceded": 0.9,
        "total_points": 13, "was_home": True, "as_of": T(22, 8),
    }]))
    before = _sem(wh, "sem_player_form", T(21, 17))
    after = _sem(wh, "sem_player_form", T(22, 9))
    assert before.empty, "a result was visible before points finalised"
    assert float(after.iloc[0]["expected_goals"]) == 1.4


def test_ownership_carries_external_eo_beside_the_marginal(wh: Warehouse) -> None:
    ProjectionStore(wh).append("fact_external_ownership", pd.DataFrame([{
        "provider": "livefpl", "season": "2026-27", "gw": 2, "code": 1,
        "metric": "eo_top10k", "value": 1.55, "as_of": T(5),
    }]))
    df = _sem(wh, "sem_ownership", T(10))
    row = df[df["eo_metric"] == "eo_top10k"].iloc[0]
    assert float(row["eo_value"]) == 1.55
    assert float(row["selected_by_pct"]) == 69.2


def test_fixtures_unpivot_to_one_row_per_side(wh: Warehouse) -> None:
    wh.append("dim_team", pd.DataFrame([{
        "season": "2026-27", "team_code": 3, "team_id": 1,
        "name": "Arsenal", "short_name": "ARS", "as_of": T(1),
    }]))
    wh.append("fact_fixture", pd.DataFrame([{
        "season": "2026-27", "fixture_id": 7, "gw": 2, "kickoff_utc": T(28, 15),
        "home_team_code": 43, "away_team_code": 3, "finished": False,
        "home_score": None, "away_score": None, "as_of": T(1),
    }]))
    df = _sem(wh, "sem_fixtures", T(10))
    assert len(df) == 2
    home = df[df["is_home"]].iloc[0]
    assert home["team"] == "MCI" and home["opponent"] == "ARS"


CONTRACT: dict[str, set[str]] = {
    "sem_players": {"season", "code", "web_name", "position", "team_code", "team",
                    "price", "selected_by_pct", "status",
                    "chance_of_playing_next_round", "news", "element_id"},
    "sem_projections": {"season", "gw", "code", "web_name", "position", "team",
                        "price", "source", "xpts", "xmins", "xp_if_appears",
                        "p_appear", "fetched_at"},
    "sem_projection_consensus": {"season", "gw", "code", "web_name", "position",
                                 "team", "price", "n_sources", "xpts_mean",
                                 "xpts_min", "xpts_max", "xpts_spread", "xpts_sd",
                                 "xmins_mean", "n_sources_xmins"},
    "sem_player_form": {"season", "gw", "code", "fixture_id", "was_home",
                        "minutes", "total_points", "goals_scored", "assists",
                        "clean_sheets", "goals_conceded", "bonus", "bps", "starts",
                        "expected_goals", "expected_assists",
                        "expected_goals_conceded", "tackles",
                        "clearances_blocks_interceptions", "recoveries",
                        "defensive_contribution", "saves", "yellow_cards",
                        "red_cards"},
    "sem_ownership": {"season", "code", "web_name", "position", "team", "price",
                      "selected_by_pct", "eo_gw", "eo_provider", "eo_metric",
                      "eo_value"},
    "sem_fixtures": {"season", "fixture_id", "gw", "kickoff_utc", "finished",
                     "team_code", "opponent_code", "is_home", "team", "opponent",
                     "goals_for", "goals_against"},
    "sem_player_match_stats": {"source", "season", "code", "match_id", "gw",
                               "minutes_played", "goals", "assists",
                               "total_shots", "xg", "xa", "chances_created"},
}


def test_the_column_contract_only_ever_grows(wh: Warehouse) -> None:
    """Columns may be added; renames and removals break every consumer."""
    for macro, promised in CONTRACT.items():
        cols = set(_sem(wh, macro, T(10)).columns)
        missing = promised - cols
        assert not missing, f"{macro} broke its contract: missing {sorted(missing)}"
