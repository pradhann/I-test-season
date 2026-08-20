"""Extra-markets ingestion: parsing details and the 150-credit expansion cap.

Everything runs offline. The budget tests use a stub client so the cap logic
is exercised without a key or a network call; the invariant under test is the
same one the anytime-scorer path enforces: a run that would breach the cap
spends *nothing*, ever -- there is no partial spend to clean up.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from fpl_edge.ingest.odds import CreditBudgetExceeded
from fpl_edge.ingest.odds_markets import (
    EXPANSION_MONTHLY_CAP,
    check_expansion_budget,
    ledger_record,
    ledger_spent,
    parse_extra_market_event,
)

FIX = Path(__file__).parents[1] / "fixtures" / "odds"
UTC = dt.timezone.utc
NOW = dt.datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


# -- ledger -------------------------------------------------------------------


def test_ledger_starts_at_zero(tmp_path: Path) -> None:
    assert ledger_spent(NOW, tmp_path / "ledger.json") == 0


def test_ledger_accumulates_within_month(tmp_path: Path) -> None:
    p = tmp_path / "ledger.json"
    assert ledger_record(6, "catalog probes", NOW, p) == 6
    assert ledger_record(42, "gw1 run", NOW, p) == 48
    assert ledger_spent(NOW, p) == 48
    # a new month starts a new bucket; August's spend stays on record
    sept = NOW.replace(month=9)
    assert ledger_spent(sept, p) == 0
    data = json.loads(p.read_text())
    assert data["2026-08"]["spent"] == 48
    assert len(data["2026-08"]["runs"]) == 2


def test_budget_refuses_before_spending(tmp_path: Path) -> None:
    p = tmp_path / "ledger.json"
    ledger_record(140, "previous runs", NOW, p)
    with pytest.raises(CreditBudgetExceeded, match="Nothing was spent"):
        check_expansion_budget(42, NOW, cap=EXPANSION_MONTHLY_CAP, path=p)
    # the refused run must not have been recorded
    assert ledger_spent(NOW, p) == 140


def test_budget_allows_exact_fit(tmp_path: Path) -> None:
    p = tmp_path / "ledger.json"
    ledger_record(108, "previous runs", NOW, p)
    check_expansion_budget(42, NOW, cap=150, path=p)  # 108 + 42 == 150: allowed


# -- parsing corner cases ------------------------------------------------------


def test_btts_selections_normalised() -> None:
    payload = {
        "home_team": "Arsenal", "away_team": "Coventry City",
        "commence_time": "2026-08-21T19:00:00Z",
        "bookmakers": [{
            "key": "skybet",
            "markets": [{"key": "btts", "outcomes": [
                {"name": "Yes", "price": 2.4},
                {"name": "No", "price": 1.55},
            ]}],
        }],
    }
    df = parse_extra_market_event(payload, NOW, "2026-27")
    assert set(df["selection"]) == {"YES", "NO"}
    assert (df["market"] == "btts").all()


def test_alternate_team_totals_folded_and_deduped() -> None:
    """The base and alternate feeds repeat the main line; one row survives."""
    leg = {"name": "Over", "description": "Arsenal", "price": 1.88, "point": 2.5}
    payload = {
        "home_team": "Arsenal", "away_team": "Coventry City",
        "commence_time": "2026-08-21T19:00:00Z",
        "bookmakers": [{
            "key": "fanduel",
            "markets": [
                {"key": "team_totals", "outcomes": [dict(leg)]},
                {"key": "alternate_team_totals", "outcomes": [
                    dict(leg),  # duplicate of the base line
                    {"name": "Over", "description": "Arsenal",
                     "price": 3.35, "point": 3.5},
                ]},
            ],
        }],
    }
    df = parse_extra_market_event(payload, NOW, "2026-27")
    assert set(df["market"]) == {"team_totals"}
    assert sorted(df["selection"]) == ["HOME|OVER_2.5", "HOME|OVER_3.5"]


def test_unknown_team_total_description_dropped() -> None:
    payload = {
        "home_team": "Arsenal", "away_team": "Coventry City",
        "commence_time": "2026-08-21T19:00:00Z",
        "bookmakers": [{
            "key": "fanduel",
            "markets": [{"key": "team_totals", "outcomes": [
                {"name": "Over", "description": "Chelsea", "price": 2.0, "point": 1.5},
            ]}],
        }],
    }
    assert parse_extra_market_event(payload, NOW, "2026-27").empty


def test_lay_markets_skipped(tmp_path: Path) -> None:
    payload = {
        "home_team": "Arsenal", "away_team": "Coventry City",
        "commence_time": "2026-08-21T19:00:00Z",
        "bookmakers": [{
            "key": "betfair_ex_uk",
            "markets": [{"key": "btts_lay", "outcomes": [
                {"name": "Yes", "price": 2.5},
            ]}],
        }],
    }
    assert parse_extra_market_event(payload, NOW, "2026-27").empty


def test_real_payload_selection_uniqueness() -> None:
    """No (book, market, selection) collision on the real GW1 payloads --
    collisions are how rows vanish silently in fact_odds' primary key."""
    for name in ("odds_api_correct_score_btts.json", "odds_api_team_totals.json"):
        payload = json.loads((FIX / name).read_text())
        df = parse_extra_market_event(payload, NOW, "2026-27")
        assert not df.empty
        dupes = df.duplicated(["fixture_key", "bookmaker", "market", "selection"])
        assert not dupes.any(), df[dupes]
