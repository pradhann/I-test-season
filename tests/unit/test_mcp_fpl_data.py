"""fpl_mcp's data layer after the fetch unification (PIPELINES.md §3 defect 2).

What is pinned here:

* **The cache moved.** The old module wrote plain-JSON caches
  (``bootstrap_static.json`` + one file per bootstrap key) INTO
  ``data/raw/fpl_api/`` -- the engine's hash-named provenance archive. The
  convenience cache now lives in ``data/cache/fpl_mcp/`` and writes exactly
  one file per endpoint, never a per-key spray.
* **Lookups read the warehouse.** ``get_elements_df`` / ``get_teams_df`` /
  ``get_fixtures_df`` / ``current_gameweek`` answer from the regularly
  ingested tables (dim_player, fact_player_state, dim_team, fact_fixture,
  fact_player_fixture, dim_event) -- latest row per entity -- and touch the
  network only when there is no warehouse to read.
* **No second fetch stack.** The module holds no ``requests`` client; the
  live fallback is the engine's Fetcher and entry endpoints go through the
  rivals client (``entry_json``).

Hermetic: a tmp DuckDB per test, ``_live_json`` fenced off so any network
attempt is an assertion failure, and the cache dir pointed at tmp.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest

from fpl_mcp.utils import fpl_data

UTC = dt.timezone.utc
AS_OF_OLD = dt.datetime(2026, 8, 1, tzinfo=UTC)
AS_OF = dt.datetime(2026, 8, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fenced(monkeypatch, tmp_path):
    """No network, no real warehouse, cache in tmp -- unless a test opts in."""
    def _boom(*a, **k):  # pragma: no cover - only fires on regression
        raise AssertionError("unit tests must not hit the FPL API")

    monkeypatch.setattr(fpl_data, "_live_json", _boom)
    monkeypatch.setenv("FPL_EDGE_DB", str(tmp_path / "absent.duckdb"))
    monkeypatch.setattr(fpl_data, "CACHE_DIR", tmp_path / "cache" / "fpl_mcp")
    monkeypatch.setattr(fpl_data, "_entry_fetcher", None)
    return tmp_path


def _warehouse(tmp_path, monkeypatch):
    """A minimal engine warehouse: two players, two teams, some results."""
    from fpl_edge.store.warehouse import Warehouse

    path = tmp_path / "fpl.duckdb"
    monkeypatch.setenv("FPL_EDGE_DB", str(path))
    wh = Warehouse(path)
    try:
        # A previous season's row: must never leak into the current frame.
        wh.sql("INSERT INTO dim_player VALUES ('2025-26', 999, 5, 'Haaland', "
               "'Erling', 'Haaland', 4, 43, ?)", [AS_OF_OLD])
        wh.sql("INSERT INTO dim_player VALUES ('2026-27', 999, 233, 'Haaland', "
               "'Erling', 'Haaland', 4, 43, ?)", [AS_OF])
        wh.sql("INSERT INTO dim_player VALUES ('2026-27', 888, 112, 'Bruno F.', "
               "'Bruno', 'Fernandes', 3, 1, ?)", [AS_OF])
        # Price moved between as_ofs: only the latest may render.
        wh.sql("INSERT INTO fact_player_state (season, code, element_id, "
               "price_tenths, selected_by_pct, as_of) "
               "VALUES ('2026-27', 999, 233, 140, 60.5, ?)", [AS_OF_OLD])
        wh.sql("INSERT INTO fact_player_state (season, code, element_id, "
               "price_tenths, selected_by_pct, as_of) "
               "VALUES ('2026-27', 999, 233, 145, 62.1, ?)", [AS_OF])
        wh.sql("INSERT INTO fact_player_state (season, code, element_id, "
               "price_tenths, selected_by_pct, as_of) "
               "VALUES ('2026-27', 888, 112, 90, 12.0, ?)", [AS_OF])
        wh.sql("INSERT INTO dim_team VALUES ('2026-27', 43, 13, 'Man City', "
               "'MCI', ?)", [AS_OF])
        wh.sql("INSERT INTO dim_team VALUES ('2026-27', 1, 14, 'Man Utd', "
               "'MUN', ?)", [AS_OF])
        wh.sql("INSERT INTO fact_player_fixture (season, code, fixture_id, gw, "
               "total_points, as_of) VALUES ('2026-27', 999, 1, 1, 12, ?)",
               [AS_OF])
        wh.sql("INSERT INTO fact_player_fixture (season, code, fixture_id, gw, "
               "total_points, as_of) VALUES ('2026-27', 999, 11, 2, 9, ?)",
               [AS_OF])
        wh.sql("INSERT INTO dim_event VALUES ('2026-27', 1, "
               "'2026-08-14 17:30:00+00', true, ?)", [AS_OF])
        wh.sql("INSERT INTO dim_event VALUES ('2026-27', 2, "
               "'2026-08-21 17:30:00+00', false, ?)", [AS_OF])
        wh.sql("INSERT INTO dim_event VALUES ('2026-27', 3, "
               "'2099-01-01 17:30:00+00', false, ?)", [AS_OF])
        wh.sql("INSERT INTO fact_fixture VALUES ('2026-27', 1, 1, "
               "'2026-08-15 14:00:00+00', 43, 1, true, 3, 1, ?)", [AS_OF])
    finally:
        wh.close()
    return path


# ---------------------------------------------------------------------------
# The cache relocation


def test_the_cache_dir_is_outside_the_provenance_archive():
    """The constant itself, unpatched: data/cache/fpl_mcp, never data/raw."""
    # read the module source so the patched-in-fixture value cannot fool us
    src = Path(fpl_data.__file__).read_text(encoding="utf-8")
    assert '"data" / "cache" / "fpl_mcp"' in src
    assert '"data" / "raw"' not in src  # nothing mutable under the archive


def test_bootstrap_cache_is_one_file_in_the_cache_dir(monkeypatch, fenced):
    calls = []

    def _live(endpoint):
        calls.append(endpoint)
        return {"elements": [], "teams": [], "element_types": [], "events": [],
                "chips": [], "phases": []}

    monkeypatch.setattr(fpl_data, "_live_json", _live)
    fpl_data.get_bootstrap_data()
    cache = fpl_data.CACHE_DIR
    # Exactly one cache file: the per-key spray (events.json, elements.json,
    # ...) that used to land in data/raw/fpl_api is gone.
    assert sorted(p.name for p in cache.iterdir()) == ["bootstrap_static.json"]
    # Second call is served from the cache, not the network.
    fpl_data.get_bootstrap_data()
    assert calls == ["bootstrap-static/"]
    # force_refresh really refreshes.
    fpl_data.get_bootstrap_data(force_refresh=True)
    assert calls == ["bootstrap-static/", "bootstrap-static/"]


def test_no_bare_requests_client_remains(monkeypatch):
    assert not hasattr(fpl_data, "requests")
    src = Path(fpl_data.__file__).read_text(encoding="utf-8")
    assert "import requests" not in src


# ---------------------------------------------------------------------------
# Warehouse-backed lookups


def test_elements_come_from_the_warehouse_with_no_network(
    monkeypatch, fenced, tmp_path,
):
    _warehouse(tmp_path, monkeypatch)
    df = fpl_data.get_elements_df()  # _live_json is fenced: any fetch dies
    assert len(df) == 2  # the 2025-26 row did not leak
    row = df.set_index("id").loc[233]
    assert row["first_name"] == "Erling" and row["second_name"] == "Haaland"
    assert row["team_name"] == "Man City" and int(row["team"]) == 13
    assert row["position"] == "FWD" and int(row["element_type"]) == 4
    assert int(row["now_cost"]) == 145          # the LATEST price, not 140
    assert float(row["selected_by_percent"]) == 62.1
    assert int(row["total_points"]) == 21       # 12 + 9 across fixtures


def test_teams_and_fixtures_come_from_the_warehouse(
    monkeypatch, fenced, tmp_path,
):
    _warehouse(tmp_path, monkeypatch)
    teams = fpl_data.get_teams_df()
    assert set(teams["name"]) == {"Man City", "Man Utd"}
    assert fpl_data.get_team_id_by_name("man city") == 13

    fixtures = fpl_data.get_fixtures_df()
    assert len(fixtures) == 1
    fx = fixtures.iloc[0]
    # team codes were mapped onto per-season team ids, the shape every
    # consumer of the live /fixtures/ endpoint already expects.
    assert int(fx["team_h"]) == 13 and int(fx["team_a"]) == 14
    assert bool(fx["finished"]) and int(fx["team_h_score"]) == 3

    summary = fpl_data.compute_team_summary(13)
    assert summary["games"] == 1 and summary["wins"] == 1
    assert summary["goals_scored"] == 3 and summary["goals_conceded"] == 1


def test_current_gameweek_reads_the_deadline_calendar(
    monkeypatch, fenced, tmp_path,
):
    _warehouse(tmp_path, monkeypatch)
    # GW1 and GW2 deadlines have passed, GW3's (2099) has not -> current is 2.
    assert fpl_data.current_gameweek() == 2


def test_current_gameweek_falls_back_to_live_bootstrap(monkeypatch, fenced):
    monkeypatch.setattr(
        fpl_data, "_live_json",
        lambda ep: {"events": [{"id": 7, "is_current": True}]})
    assert fpl_data.current_gameweek() == 7


# ---------------------------------------------------------------------------
# Entry endpoints: the rivals client


def test_entry_json_goes_through_the_rivals_fetcher_singleton(monkeypatch):
    seen = []

    class FakeRivals:
        def get_json(self, endpoint):
            seen.append(endpoint)
            return SimpleNamespace(body={"picks": []})

    monkeypatch.setattr(fpl_data, "_entry_fetcher", FakeRivals())
    assert fpl_data.entry_json("entry/42/event/3/picks/") == {"picks": []}
    assert seen == ["entry/42/event/3/picks/"]


def test_entry_json_builds_a_budgeted_rivals_fetcher_lazily(monkeypatch):
    """The real client, the real budget type -- constructed, not fetched."""
    monkeypatch.setattr(fpl_data, "_entry_fetcher", None)
    built = {}

    import fpl_edge.ingest.rivals.client as rc

    class Spy(rc.RivalsFetcher):
        def get_json(self, endpoint, params=None):
            built["budget"] = self.budget
            built["interval"] = self.min_interval_s
            return SimpleNamespace(body=None)

    monkeypatch.setattr(rc, "RivalsFetcher", Spy)
    assert fpl_data.entry_json("entry/1/history/") is None
    assert built["budget"].limit == fpl_data.ENTRY_BUDGET_LIMIT
    assert built["interval"] >= rc.MIN_INTERVAL_S  # pacing not weakened
