"""The named-elite crawl: verification is the feature, not a nicety.

FPL entry IDs are per-season, so a curated name -> ID mapping rots every
August -- and it rots into *other real people*, not into 404s (measured
2026-08-24: all 20 of the FPL-MCP expert seed IDs now belong to strangers).
These tests pin the behaviour that protects against that: an ID whose live
profile does not carry the curated name is excluded, recorded, and never
crawled; a verified one is crawled fully -- history, every locked gameweek's
squad, the season's transfers -- with the deadline-stamped as_of discipline
inherited from the picks module.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from fpl_edge.ingest.http import Fetched
from fpl_edge.ingest.rivals.client import RequestBudget
from fpl_edge.ingest.rivals.elite import ELITE_NAMED, EliteEntry, collect, verify

UTC = dt.timezone.utc
GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
AFTER_GW1 = GW1 + dt.timedelta(days=2)


class StubFetcher:
    """RivalsFetcher-shaped; profiles are configurable per entry id."""

    def __init__(self, profiles: dict[int, dict | None]) -> None:
        self.profiles = profiles
        self.budget = RequestBudget(limit=500)
        self.calls: list[str] = []

    def get_json(self, endpoint: str, params=None) -> Fetched:
        self.calls.append(endpoint)
        self.budget.charge(endpoint.split("/")[0])
        return Fetched(
            body=self._body(endpoint), fetched_at=dt.datetime.now(UTC),
            sha256="stub", body_path=Path("/dev/null"), http_status=200,
            from_cache=False,
        )

    def close(self) -> None:  # pragma: no cover
        pass

    def _body(self, endpoint: str):
        parts = endpoint.strip("/").split("/")
        if endpoint == "bootstrap-static/":
            return {"events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z"},
                               {"id": 2, "deadline_time": "2026-08-28T17:30:00Z"}]}
        eid = int(parts[1])
        if "history" in parts:
            return {"past": [{"season_name": "2024/25", "total_points": 2600,
                              "rank": 120, "rank_percentage": "0.1"}],
                    "current": [{"event": 1, "points": 80, "total_points": 80,
                                 "overall_rank": 5000, "bank": 5, "value": 1000,
                                 "event_transfers": 0, "event_transfers_cost": 0,
                                 "points_on_bench": 6}],
                    "chips": []}
        if "picks" in parts:
            return {"picks": [
                {"element": 100 + i, "position": i + 1,
                 "multiplier": 2 if i == 0 else (1 if i < 11 else 0),
                 "is_captain": i == 0, "is_vice_captain": i == 1}
                for i in range(15)
            ], "active_chip": None}
        if "transfers" in parts:
            return [{"element_in": 301, "element_in_cost": 75,
                     "element_out": 302, "element_out_cost": 71,
                     "entry": eid, "event": 1, "time": "2026-08-20T09:00:00Z"}]
        # profile
        return self.profiles.get(eid)


def _profile(first: str, last: str, team: str = "Some Team") -> dict:
    return {"player_first_name": first, "player_last_name": last, "name": team,
            "player_region_name": "England", "years_active": 12,
            "favourite_team": 1, "started_event": 1}


def test_a_matching_name_verifies_and_a_strangers_id_is_excluded():
    """The stale-ID failure mode: the ID resolves to a DIFFERENT real person."""
    entries = (
        EliteEntry("Ben Crellin", 53517, "test"),
        EliteEntry("Ben Crellin", 6586, "stale prior-season id"),
    )
    stub = StubFetcher({53517: _profile("Ben", "Crellin"),
                        6586: _profile("Levi", "Longworth")})
    verification, managers = verify(stub, entries)

    by_id = {int(r["entry_id"]): r["status"] for _, r in verification.iterrows()}
    assert by_id[53517] == "verified"
    assert by_id[6586] == "name_mismatch"
    assert list(managers["entry_id"]) == [53517], (
        "a stranger's identity must never be recorded under a curated name"
    )
    assert managers.iloc[0]["source"] == "elite_named"


def test_verification_ignores_case_and_accents():
    entries = (EliteEntry("Jesper Oiestad", 1690, "test"),)
    stub = StubFetcher({1690: _profile("Jesper", "Øiestad")})
    verification, managers = verify(stub, entries)
    assert verification.iloc[0]["status"] == "verified"
    assert len(managers) == 1


def test_a_deleted_entry_is_recorded_as_404_not_crawled():
    entries = (EliteEntry("Gone Manager", 999999, "test"),)
    stub = StubFetcher({999999: None})
    verification, managers = verify(stub, entries)
    assert verification.iloc[0]["status"] == "entry_404"
    assert managers.empty


def test_collect_crawls_only_verified_ids_and_keeps_pit_stamps():
    entries = (
        EliteEntry("Ben Crellin", 53517, "test"),
        EliteEntry("Ben Crellin", 6586, "stale"),
    )
    stub = StubFetcher({53517: _profile("Ben", "Crellin"),
                        6586: _profile("Levi", "Longworth")})
    frames, summary = collect(stub, entries=entries, now=AFTER_GW1)

    assert summary["verified_ids"] == [53517]
    # The stranger's ID got its verification request and nothing more.
    assert not any(c.startswith("entry/6586/") and c != "entry/6586/"
                   for c in stub.calls)

    picks = frames["fact_manager_pick"]
    assert set(picks["entry_id"]) == {53517}
    assert len(picks) == 15
    assert (picks["as_of"] == GW1).all(), "picks must be stamped with the deadline"

    transfers = frames["fact_manager_transfer"]
    assert len(transfers) == 1
    assert transfers.iloc[0]["element_in"] == 301
    assert transfers.iloc[0]["as_of"] == GW1, (
        "a transfer is public at the deadline of the gameweek it applies to"
    )
    assert frames["fact_manager_season"].iloc[0]["season"] == "2024/25"
    assert frames["fact_manager_gw"].iloc[0]["bank_tenths"] == 5


def test_nothing_verified_means_nothing_crawled():
    entries = (EliteEntry("Ben Crellin", 6586, "stale"),)
    stub = StubFetcher({6586: _profile("Levi", "Longworth")})
    frames, summary = collect(stub, entries=entries, now=AFTER_GW1)
    assert "fact_manager_pick" not in frames
    assert "skipped" in summary
    # bootstrap + 1 verification request; no history/picks/transfers.
    assert stub.budget.spent == 2


def test_the_shipped_list_has_no_duplicate_ids_and_crellin_is_right():
    ids = [e.entry_id for e in ELITE_NAMED]
    assert len(ids) == len(set(ids))
    crellin = next(e for e in ELITE_NAMED if "Crellin" in e.name)
    assert crellin.entry_id == 53517, (
        "Crellin's 2026-27 entry is 53517 (verified live 2026-08-24); "
        "6586 is a stranger"
    )
