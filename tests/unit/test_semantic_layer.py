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


def _pick(entry: int, element: int, *, day: int, captain: bool = False,
          mult: int | None = None, slot: int = 1) -> dict:
    return {"entry_id": entry, "season": "2026-27", "gw": 1,
            "element_id": element, "slot": slot,
            "multiplier": (2 if captain else 1) if mult is None else mult,
            "is_captain": captain, "is_vice_captain": False, "as_of": T(day)}


def _manager(entry: int, source: str, name: str, *, day: int = 1) -> dict:
    return {"entry_id": entry, "player_name": name, "entry_name": f"team{entry}",
            "region": None, "years_active": None, "favourite_team_id": None,
            "started_event": None, "source": source, "as_of": T(day)}


def test_a_squad_locked_after_the_instant_is_invisible(wh: Warehouse) -> None:
    """Picks are stamped as_of = deadline; before it the squad does not exist."""
    wh.append("dim_manager", pd.DataFrame([_manager(9, "elite_named", "Ben Crellin")]))
    wh.append("fact_manager_pick", pd.DataFrame([_pick(9, 11, day=21, captain=True)]))
    before = _sem(wh, "sem_manager_picks", T(20))
    after = _sem(wh, "sem_manager_picks", T(22))
    assert before.empty, "a locked squad was visible before its deadline"
    row = after.iloc[0]
    assert row["manager_name"] == "Ben Crellin"
    assert int(row["code"]) == 1 and row["web_name"] == "Haaland"
    assert bool(row["is_captain"]) and int(row["multiplier"]) == 2


def test_an_unresolvable_element_keeps_a_null_code_not_a_dropped_row(
    wh: Warehouse,
) -> None:
    """element_id -> code misses stay visible (and countable), never silent."""
    wh.append("dim_manager", pd.DataFrame([_manager(9, "elite_named", "Ben Crellin")]))
    wh.append("fact_manager_pick", pd.DataFrame([
        _pick(9, 11, day=21, slot=1), _pick(9, 999, day=21, slot=2),
    ]))
    df = _sem(wh, "sem_manager_picks", T(22))
    assert len(df) == 2, "an unresolvable element silently shrank the squad"
    missing = df[df["element_id"] == 999]
    assert missing["code"].isna().all() and missing["web_name"].isna().all()


def test_a_transfer_becomes_public_at_its_deadline_with_both_names(
    wh: Warehouse,
) -> None:
    wh.append("dim_manager", pd.DataFrame([_manager(9, "elite_named", "Ben Crellin")]))
    wh.append("dim_player", pd.DataFrame([{
        "season": "2026-27", "code": 2, "element_id": 12, "web_name": "Salah",
        "first_name": "M", "second_name": "S", "position": 3, "team_code": 11,
        "as_of": T(1),
    }]))
    wh.append("fact_manager_transfer", pd.DataFrame([{
        "entry_id": 9, "season": "2026-27", "gw": 1,
        "element_in": 11, "element_in_cost": 155,
        "element_out": 12, "element_out_cost": 145,
        "time_utc": T(20, 9), "as_of": T(21),
    }]))
    assert _sem(wh, "sem_manager_transfers", T(20)).empty, (
        "a transfer was public before the deadline it applies to"
    )
    row = _sem(wh, "sem_manager_transfers", T(22)).iloc[0]
    assert row["player_in"] == "Haaland" and row["player_out"] == "Salah"
    assert float(row["price_in"]) == 15.5 and float(row["price_out"]) == 14.5


def test_elite_ownership_keeps_the_cohorts_apart(wh: Warehouse) -> None:
    """The standings sample and the named elite are different populations.

    Mixing them would let a thousand hot-start managers drown out the handful
    of named elites, which is exactly the question-shape ("what do THE ELITE
    own") the cohort column exists to preserve.
    """
    wh.append("dim_manager", pd.DataFrame([
        _manager(9, "elite_named", "Ben Crellin"),
        _manager(10, "top1k:2026-27:gw1:rank5", "Hot Start"),
        _manager(11, "top1k:2026-27:gw1:rank6", "Hot Start II"),
    ]))
    wh.append("fact_manager_pick", pd.DataFrame([
        _pick(9, 11, day=21, captain=True),
        _pick(10, 11, day=21, captain=True),
        _pick(11, 11, day=21, captain=False),
    ]))
    df = _sem(wh, "sem_elite_ownership", T(22))
    elite = df[df["cohort"] == "elite"].iloc[0]
    top = df[df["cohort"] == "top1k"].iloc[0]
    assert int(elite["n_managers"]) == 1 and int(top["n_managers"]) == 2
    assert float(elite["own_pct"]) == 100.0 and float(top["own_pct"]) == 100.0
    assert float(elite["captain_pct"]) == 100.0
    assert float(top["captain_pct"]) == 50.0
    # EO: elite = one captain (x2) of 1 manager = 200; top1k = 2+1 of 2 = 150.
    assert float(elite["eo_pct"]) == 200.0
    assert float(top["eo_pct"]) == 150.0


def test_an_entry_in_both_crawls_belongs_to_exactly_one_cohort(wh: Warehouse) -> None:
    """B8: two crawls found the same manager. He is ONE manager.

    Before ``sem_manager_cohort`` the classification was a DISTINCT over every
    source row, so an entry sampled by both crawls joined twice — landing in
    both cohorts and inflating both denominators. The rule now has a stated
    precedence: rank-sampled membership is an objective fact about the entry
    and outranks curation, so top1k wins.
    """
    wh.append("dim_manager", pd.DataFrame([
        _manager(9, "elite_named", "Ben Crellin"),
        _manager(9, "top1k:2026-27:gw1:rank5", "Ben Crellin", day=2),
        _manager(10, "snowball:77", "Someone Else"),
    ]))
    wh.append("fact_manager_pick", pd.DataFrame([
        _pick(9, 11, day=21, captain=True), _pick(10, 11, day=21),
    ]))

    coh = _sem(wh, "sem_manager_cohort", T(22)).set_index("entry_id")
    assert len(coh) == 2, "one entry produced more than one cohort row"
    assert coh.loc[9, "cohort"] == "top1k" and coh.loc[10, "cohort"] == "elite"
    assert int(coh.loc[9, "n_sources"]) == 2

    df = _sem(wh, "sem_elite_ownership", T(22))
    assert sorted(df["cohort"]) == ["elite", "top1k"], "a cohort was duplicated"
    by_cohort = df.set_index("cohort")
    assert int(by_cohort.loc["top1k", "n_managers"]) == 1
    assert int(by_cohort.loc["elite", "n_managers"]) == 1
    assert float(by_cohort.loc["top1k", "eo_pct"]) == 200.0
    assert float(by_cohort.loc["elite", "eo_pct"]) == 100.0

    # Membership is ANY source row at or before p_as_of, so it is monotone: on
    # day 1 only the curated row is visible and the entry reads 'elite'.
    early = _sem(wh, "sem_manager_cohort", T(1)).set_index("entry_id")
    assert early.loc[9, "cohort"] == "elite"


def test_a_squad_with_no_manager_row_is_labelled_not_dropped(wh: Warehouse) -> None:
    """An entry we hold picks for but cannot classify stays visible."""
    wh.append("dim_manager", pd.DataFrame([_manager(9, "elite_named", "Ben")]))
    wh.append("fact_manager_pick", pd.DataFrame([
        _pick(9, 11, day=21), _pick(4242, 11, day=21, captain=True),
    ]))
    df = _sem(wh, "sem_elite_ownership", T(22)).set_index("cohort")
    assert "unclassified" in df.index, "a crawled squad vanished at the cohort join"
    assert int(df.loc["unclassified", "n_managers"]) == 1
    assert float(df.loc["unclassified", "eo_pct"]) == 200.0
    assert int(df.loc["elite", "n_managers"]) == 1


def test_eo_counts_the_bench_at_zero_while_ownership_counts_it(wh: Warehouse) -> None:
    """Ownership and EO are tracked separately, and the bench is where they part."""
    wh.append("dim_manager", pd.DataFrame([
        _manager(9, "elite_named", "A"), _manager(10, "elite_named", "B"),
    ]))
    wh.append("fact_manager_pick", pd.DataFrame([
        _pick(9, 11, day=21, mult=0, slot=13),   # owned, benched
        _pick(10, 11, day=21, captain=True),     # owned, captained
    ]))
    row = _sem(wh, "sem_elite_ownership", T(22)).iloc[0]
    assert float(row["own_pct"]) == 100.0, "a benched player is still owned"
    assert int(row["benched_by"]) == 1 and int(row["started_by"]) == 1
    assert int(row["captained_by"]) == 1
    assert float(row["eo_units"]) == 2.0
    assert float(row["eo_pct"]) == 100.0, "the bench must carry no scoring exposure"


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
    "sem_projection_weights": {"provider", "weight", "loss", "loss_metric",
                               "baseline_loss", "n_obs", "earned", "holdout",
                               "fit_id", "fitted_at", "track_record_gws"},
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
    "sem_manager_picks": {"season", "gw", "entry_id", "manager_name",
                          "team_name", "source", "overall_rank", "gw_points",
                          "code", "web_name", "element_id", "slot",
                          "multiplier", "is_captain", "is_vice_captain"},
    "sem_manager_transfers": {"season", "gw", "entry_id", "manager_name",
                              "team_name", "source", "code_in", "player_in",
                              "code_out", "player_out", "element_in",
                              "element_out", "price_in", "price_out",
                              "time_utc"},
    "sem_elite_ownership": {"season", "gw", "cohort", "code", "web_name",
                            "n_managers", "owned_by", "own_pct", "captain_pct",
                            "eo_pct", "started_by", "benched_by",
                            "captained_by", "eo_units"},
    "sem_manager_cohort": {"entry_id", "cohort", "n_top1k_sources", "n_sources",
                           "sources", "first_seen"},
}


def test_the_column_contract_only_ever_grows(wh: Warehouse) -> None:
    """Columns may be added; renames and removals break every consumer."""
    for macro, promised in CONTRACT.items():
        cols = set(_sem(wh, macro, T(10)).columns)
        missing = promised - cols
        assert not missing, f"{macro} broke its contract: missing {sorted(missing)}"
