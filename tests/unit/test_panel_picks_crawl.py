"""The panel crawl: who it selects, what it refuses, what survives a failure.

Break-watch-restored for each pinned behaviour:

* the selection is verified AND active AND non-null entry_id -- an inactive
  person, an unverified id and a null id are each excluded on their own;
* a live profile whose name contradicts the roster's verified name is
  recorded as ``name_mismatch`` and NOT crawled (no picks request is spent);
* a refused entry records the HTTP status it got, and the people before it
  keep their frames -- paid-for requests are never discarded;
* a run with any non-ok person, or with nobody selected, names it in
  ``failures`` so the step exits non-zero instead of reporting a partial
  crawl as success.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import pytest

from fpl_edge.ingest.http import Fetched
from fpl_edge.ingest.rivals import panel_picks
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget
from fpl_edge.store import Warehouse

UTC = dt.UTC
GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
GW2 = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
NOW = GW2 + dt.timedelta(days=2)   # two gameweeks locked, GW3 not yet
PAST = dt.datetime(2026, 8, 1, tzinfo=UTC)


def _plant_panel(db: Path, people) -> None:
    """``people``: (person_key, display_name, entry_id, verified, active, api_name)."""
    wh = Warehouse(db)
    wh.sql(
        "CREATE TABLE IF NOT EXISTS panel_person ("
        " person_key VARCHAR PRIMARY KEY, display_name VARCHAR NOT NULL,"
        " handles_json VARCHAR, aliases_json VARCHAR, entry_id BIGINT,"
        " entry_verified BOOLEAN NOT NULL DEFAULT FALSE, entry_source_url VARCHAR,"
        " entry_api_name VARCHAR, entry_checked_utc TIMESTAMPTZ,"
        " entry_reason VARCHAR, edge_note VARCHAR, top10k_finishes INTEGER,"
        " active BOOLEAN NOT NULL DEFAULT TRUE, as_of TIMESTAMPTZ NOT NULL)"
    )
    for key, name, eid, verified, active, api_name in people:
        wh.sql(
            "INSERT INTO panel_person VALUES (?, ?, NULL, NULL, ?, ?, NULL, ?, NULL,"
            " NULL, NULL, NULL, ?, ?)",
            [key, name, eid, verified, api_name, active, PAST],
        )
    wh.close()


class StubFetcher:
    """RivalsFetcher-shaped. ``profiles`` maps entry id -> profile body, None
    for a 404, or an int HTTP status the endpoint should refuse with."""

    def __init__(self, profiles, *, limit: int = 500) -> None:
        self.profiles = profiles
        self.budget = RequestBudget(limit=limit)
        self.calls: list[str] = []

    def get_json(self, endpoint: str, params=None) -> Fetched:
        self.calls.append(endpoint)
        self.budget.charge(endpoint.split("/")[0])
        body = self._body(endpoint)
        if isinstance(body, int):
            req = httpx.Request("GET", f"https://x/{endpoint}")
            resp = httpx.Response(body, request=req)
            raise httpx.HTTPStatusError(f"{body}", request=req, response=resp)
        return Fetched(
            body=body, fetched_at=NOW, sha256="stub", body_path=Path("/dev/null"),
            http_status=404 if body is None else 200, from_cache=False,
        )

    def close(self) -> None:  # pragma: no cover
        pass

    def _body(self, endpoint: str):
        parts = endpoint.strip("/").split("/")
        if endpoint == "bootstrap-static/":
            return {"events": [
                {"id": 1, "deadline_time": "2026-08-21T17:30:00Z"},
                {"id": 2, "deadline_time": "2026-08-28T17:30:00Z"},
                {"id": 3, "deadline_time": "2026-09-04T17:30:00Z"},
            ]}
        eid = int(parts[1])
        prof = self.profiles.get(eid)
        if isinstance(prof, int):
            return prof            # refuse every endpoint for this entry
        if "history" in parts:
            return {"past": [], "chips": [{"name": "wildcard", "event": 1}],
                    "current": [{"event": g, "points": 60, "total_points": 60 * g,
                                 "overall_rank": 900, "bank": 5, "value": 1000,
                                 "event_transfers": 0, "event_transfers_cost": 0,
                                 "points_on_bench": 4} for g in (1, 2)]}
        if "picks" in parts:
            return {"picks": [
                {"element": 100 + i, "position": i + 1,
                 "multiplier": 2 if i == 0 else (1 if i < 11 else 0),
                 "is_captain": i == 0, "is_vice_captain": i == 1}
                for i in range(15)
            ], "active_chip": None}
        if "transfers" in parts:
            return [{"element_in": 301, "element_in_cost": 75, "element_out": 302,
                     "element_out_cost": 71, "entry": eid, "event": 2,
                     "time": "2026-08-27T09:00:00Z"}]
        return prof


def _profile(first: str, last: str) -> dict:
    return {"player_first_name": first, "player_last_name": last,
            "name": f"{first}'s XI", "player_region_name": "England",
            "years_active": 5, "favourite_team": 1, "started_event": 1}


ROSTER = [
    ("zophar", "Zophar", 2177, True, True, "Utkarsh D"),
    ("lateriser", "Lateriser", 6816, True, True, "Pranil Sheth"),
    ("gone", "Left The Panel", 4242, True, False, "Gone Person"),      # inactive
    ("lead", "Unverified Lead", 5151, False, True, "Some Lead"),       # unverified
    ("noid", "No Id Ned", None, False, True, None),                    # null id
    ("blank", "Blank Name", 7777, True, True, None),                   # no api name
]


# -- selection ----------------------------------------------------------------


def test_selection_is_verified_and_active_and_has_an_id(tmp_path):
    db = tmp_path / "p.duckdb"
    _plant_panel(db, ROSTER)
    with Warehouse(db) as wh:
        got = panel_picks.select_entries(wh)
    assert [e.entry_id for e in got] == [7777, 6816, 2177]  # display_name order
    by_id = {e.entry_id: e for e in got}
    assert by_id[2177].entry_api_name == "Utkarsh D"
    assert by_id[7777].entry_api_name is None


def test_a_warehouse_without_the_roster_selects_nobody(tmp_path):
    with Warehouse(tmp_path / "empty.duckdb") as wh:
        assert panel_picks.select_entries(wh) == []


# -- collect ----------------------------------------------------------------


def _entries(*ids):
    lookup = {r[2]: r for r in ROSTER}
    return [panel_picks.PanelEntry(person_key=lookup[i][0], display_name=lookup[i][1],
                                   entry_id=i, entry_api_name=lookup[i][5])
            for i in ids]


def test_a_verified_person_is_crawled_through_the_shared_ingest_path():
    fetcher = StubFetcher({2177: _profile("Utkarsh", "D")})
    frames, summary = panel_picks.collect(fetcher, _entries(2177), now=NOW)

    rec = summary["people"][0]
    assert rec["status"] == "ok" and rec["http_status"] == 200
    assert rec["picks_gws"] == [1, 2] and rec["picks_404_gws"] == []
    assert rec["transfers"] == 1 and rec["history_gws"] == 2
    assert summary["live_gws"] == [1, 2], "GW3 has not locked at NOW"

    picks = frames["fact_manager_pick"]
    assert set(picks["entry_id"]) == {2177}
    assert sorted(picks["gw"].unique()) == [1, 2]
    assert len(picks) == 30
    # as_of is the DEADLINE, the picks module's discipline, not the crawl instant
    assert set(picks.loc[picks["gw"] == 1, "as_of"]) == {GW1}
    assert frames["dim_manager"]["source"].tolist() == ["panel"]
    assert "fact_manager_transfer" in frames and "fact_manager_gw" in frames
    assert "fact_manager_chip" in frames
    # no picks request for the unlocked gameweek
    assert not any("event/3/picks" in c for c in fetcher.calls)
    assert summary["not_ok"] == []


def test_a_name_that_contradicts_the_roster_is_not_crawled():
    fetcher = StubFetcher({2177: _profile("Somebody", "Else")})
    frames, summary = panel_picks.collect(fetcher, _entries(2177), now=NOW)
    rec = summary["people"][0]
    assert rec["status"] == "name_mismatch"
    assert rec["profile_name"] == "Somebody Else"
    assert "fact_manager_pick" not in frames and "dim_manager" not in frames
    assert not any("picks" in c or "history" in c for c in fetcher.calls)
    assert summary["not_ok"][0]["status"] == "name_mismatch"


def test_a_roster_row_without_an_api_name_is_crawled_and_says_so():
    fetcher = StubFetcher({7777: _profile("Blank", "Name")})
    frames, summary = panel_picks.collect(fetcher, _entries(7777), now=NOW)
    rec = summary["people"][0]
    assert rec["status"] == "ok"
    assert "unchecked_name" in rec["detail"]
    assert "fact_manager_pick" in frames


def test_a_refusal_records_the_status_and_keeps_everyone_before_it():
    fetcher = StubFetcher({
        2177: _profile("Utkarsh", "D"),
        6816: 403,                      # the API refuses this entry outright
        7777: None,                     # and this one does not exist
    })
    frames, summary = panel_picks.collect(fetcher, _entries(2177, 6816, 7777), now=NOW)
    by_id = {p["entry_id"]: p for p in summary["people"]}
    assert by_id[2177]["status"] == "ok"
    assert by_id[6816]["status"] == "refused: HTTP 403"
    assert by_id[6816]["http_status"] == 403
    assert by_id[7777]["status"] == "entry_404"
    assert by_id[7777]["http_status"] == 404
    # Zophar's rows survived the two failures after him
    assert set(frames["fact_manager_pick"]["entry_id"]) == {2177}
    assert summary["counts"] == {"entry_404": 1, "ok": 1, "refused: HTTP 403": 1}
    assert {p["entry_id"] for p in summary["not_ok"]} == {6816, 7777}


def test_an_exhausted_budget_keeps_the_frames_already_paid_for():
    # bootstrap + profile + history + 2 picks + transfers = 6 for one person;
    # a limit of 8 dies during the second person's crawl.
    fetcher = StubFetcher({2177: _profile("Utkarsh", "D"),
                           6816: _profile("Pranil", "Sheth")}, limit=8)
    frames, summary = panel_picks.collect(fetcher, _entries(2177, 6816), now=NOW)
    assert "budget_exhausted" in summary
    by_id = {p["entry_id"]: p for p in summary["people"]}
    assert by_id[2177]["status"] == "ok"
    assert by_id[6816]["status"] == "partial"
    assert set(frames["fact_manager_pick"]["entry_id"]) == {2177}
    assert fetcher.budget.spent == 8


# -- the failure contract the step exit code rides on -----------------------


def test_failures_name_every_non_ok_person_and_an_empty_selection():
    empty = panel_picks._failures({"selected": 0}, {})
    assert empty and "nobody to crawl" in empty[0]

    summary = {"selected": 2, "not_ok": [
        {"person": "Lateriser", "entry_id": 6816, "status": "refused: HTTP 403",
         "http_status": 403, "detail": "entry/6816/ answered 403"}],
        "write": {"status": "ok", "rows": {"fact_manager_pick": 30}}}
    got = panel_picks._failures(summary, {"fact_manager_pick": object()})
    assert got == ["Lateriser (6816): refused: HTTP 403 -- entry/6816/ answered 403"]

    clean = {"selected": 1, "not_ok": [],
             "write": {"status": "ok", "rows": {"fact_manager_pick": 30}}}
    assert panel_picks._failures(clean, {"fact_manager_pick": object()}) == []

    locked = {"selected": 1, "not_ok": [], "write": {"status": "locked", "error": "busy"}}
    assert panel_picks._failures(locked, {"fact_manager_pick": object()}) == [
        "write: locked: busy"]


def test_run_writes_the_selected_people_and_exits_clean(tmp_path, monkeypatch):
    """End to end against a temp warehouse, with the fetcher stubbed at the
    module seam: selection from panel_person, the shared writer, the receipt."""
    db = tmp_path / "run.duckdb"
    _plant_panel(db, ROSTER)
    profiles = {2177: _profile("Utkarsh", "D"), 6816: _profile("Pranil", "Sheth"),
                7777: _profile("Blank", "Name")}
    made: list[StubFetcher] = []

    def fake_fetcher(budget, offline=False):
        f = StubFetcher(profiles)
        f.budget = budget
        made.append(f)
        return f

    monkeypatch.setattr(panel_picks, "RivalsFetcher", fake_fetcher)
    monkeypatch.setattr(panel_picks.dt, "datetime", _FrozenDatetime)
    out = panel_picks.run(budget_limit=100, db_path=str(db))
    assert out["failures"] == []
    assert out["write"]["status"] == "ok"
    assert out["write"]["rows"]["fact_manager_pick"] == 90
    assert set(out["write"]["rows"]) >= {"dim_manager", "fact_manager_gw",
                                         "fact_manager_transfer", "fact_manager_chip"}
    with Warehouse(db) as wh:
        got = wh.sql("SELECT entry_id, list_sort(list(DISTINCT gw)) gws "
                     "FROM fact_manager_pick GROUP BY 1 ORDER BY 1")
    assert got["entry_id"].tolist() == [2177, 6816, 7777]
    assert all(list(g) == [1, 2] for g in got["gws"])
    # the inactive, unverified and id-less rows were never asked about
    calls = " ".join(made[0].calls)
    for excluded in ("4242", "5151"):
        assert f"entry/{excluded}/" not in calls


class _FrozenDatetime(dt.datetime):
    """``dt.datetime.now`` pinned to NOW so ``live_gws`` is deterministic."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def test_main_exits_non_zero_when_a_person_is_not_ok(monkeypatch, capsys):
    monkeypatch.setattr(panel_picks, "run", lambda **kw: {
        "selected": 1, "people": [{"person": "L", "status": "refused: HTTP 403",
                                   "entry_id": 6816, "picks_gws": []}],
        "not_ok": [{"person": "L", "entry_id": 6816, "status": "refused: HTTP 403",
                    "http_status": 403, "detail": ""}],
        "failures": ["L (6816): refused: HTTP 403"], "requests": {}})
    assert panel_picks.main([]) == 1
    err = capsys.readouterr().err
    assert "FAILED" in err and "HTTP 403" in err


def test_main_exits_zero_on_a_clean_run(monkeypatch, capsys):
    monkeypatch.setattr(panel_picks, "run", lambda **kw: {
        "selected": 1, "people": [{"person": "Z", "status": "ok", "entry_id": 2177,
                                   "picks_gws": [1, 2]}],
        "not_ok": [], "failures": [], "requests": {},
        "write": {"status": "ok", "rows": {"fact_manager_pick": 30}}})
    assert panel_picks.main([]) == 0
    out = capsys.readouterr().out
    assert "1/1 people ok" in out and "picks GW1,2" in out


def test_budget_exhausted_is_an_importable_contract():
    # The module catches the client's own exception type at the person
    # boundary; a renamed exception would silently turn into a crash.
    assert panel_picks.BudgetExhausted is BudgetExhausted
    with pytest.raises(BudgetExhausted):
        RequestBudget(limit=0).charge("entry")
