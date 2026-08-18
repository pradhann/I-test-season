"""Shared fixtures for the adversarial audit suite.

The audit suite is deliberately separate from ``tests/unit``. Unit tests check
that code does what its author meant. These check that what the author meant is
not a lie -- that the point-in-time story holds, that identities survive a
season boundary, that a timestamp means the same thing on two machines.

Nothing here is owned by another team. If a fixture needs data another team
produces, it builds it synthetically rather than importing their loaders, so an
audit failure is always a statement about the code under audit and never about
a fixture that moved.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pandas as pd
import pytest

from fpl_edge.store import Warehouse

UTC = dt.UTC

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The live warehouse built by ``make ingest``. Present on a developer machine,
#: absent in a clean checkout, so every test using it must skip cleanly.
LIVE_DB = REPO_ROOT / "data" / "warehouse" / "fpl.duckdb"

#: 2026-27 GW1. Deadline from the API (events[].deadline_time), NOT from the
#: rules page, which renders in browser-local time.
GW1_DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)

#: The instant this audit was written. Pre-GW1: no 2026-27 football has happened.
TODAY = dt.datetime(2026, 8, 18, 22, 48, tzinfo=UTC)


def load_audit_script() -> ModuleType:
    """Import ``scripts/audit_leakage.py`` by path (``scripts/`` is not a package)."""
    path = REPO_ROOT / "scripts" / "audit_leakage.py"
    spec = importlib.util.spec_from_file_location("_audit_leakage", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_audit_leakage"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    """An empty warehouse on disk."""
    return Warehouse(tmp_path / "audit.duckdb")


@pytest.fixture()
def live_wh() -> Warehouse:
    """The real ingested warehouse, read-only. Skips if it has not been built."""
    if not LIVE_DB.exists():
        pytest.skip("live warehouse absent; run `make ingest`")
    return Warehouse(LIVE_DB, read_only=True)


# -- synthetic row builders -------------------------------------------------
#
# Kept here rather than in each test so that the shape of a row is defined once
# and a schema change from another team breaks the audit loudly in one place.


def player_row(
    *,
    season: str,
    code: int,
    element_id: int,
    as_of: dt.datetime,
    web_name: str = "Player",
    position: int = 3,
    team_code: int = 1,
) -> dict[str, object]:
    return {
        "season": season, "code": code, "element_id": element_id,
        "web_name": web_name, "first_name": "A", "second_name": web_name,
        "position": position, "team_code": team_code, "as_of": as_of,
    }


def state_row(
    *,
    season: str,
    code: int,
    element_id: int,
    as_of: dt.datetime,
    price_tenths: int = 70,
    selected_by_pct: float | None = 5.0,
    status: str = "a",
) -> dict[str, object]:
    return {
        "season": season, "code": code, "element_id": element_id,
        "price_tenths": price_tenths, "selected_by_pct": selected_by_pct,
        "status": status, "chance_of_playing_next_round": None, "news": "",
        "news_added": None, "transfers_in_event": 0, "transfers_out_event": 0,
        "cost_change_start": 0, "as_of": as_of,
    }


def fixture_row(
    *,
    season: str,
    fixture_id: int,
    gw: int,
    kickoff_utc: dt.datetime,
    as_of: dt.datetime,
    home_team_code: int = 1,
    away_team_code: int = 2,
    home_score: int | None = None,
    away_score: int | None = None,
) -> dict[str, object]:
    return {
        "season": season, "fixture_id": fixture_id, "gw": gw,
        "kickoff_utc": kickoff_utc, "home_team_code": home_team_code,
        "away_team_code": away_team_code,
        "finished": home_score is not None,
        "home_score": home_score, "away_score": away_score, "as_of": as_of,
    }


def result_row(
    *,
    season: str,
    code: int,
    fixture_id: int,
    gw: int,
    as_of: dt.datetime,
    minutes: int = 90,
    goals_scored: int = 0,
    assists: int = 0,
    bonus: int = 0,
    bps: int = 20,
    total_points: int = 2,
) -> dict[str, object]:
    return {
        "season": season, "code": code, "fixture_id": fixture_id, "gw": gw,
        "minutes": minutes, "goals_scored": goals_scored, "assists": assists,
        "clean_sheets": 0, "goals_conceded": 0, "own_goals": 0,
        "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
        "red_cards": 0, "saves": 0, "bonus": bonus, "bps": bps, "starts": 1,
        "tackles": 0, "clearances_blocks_interceptions": 0, "recoveries": 0,
        "defensive_contribution": 0, "expected_goals": 0.0,
        "expected_assists": 0.0, "expected_goals_conceded": 0.0,
        "total_points": total_points, "was_home": True, "as_of": as_of,
    }


def frame(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)
