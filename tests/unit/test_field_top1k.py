"""The top-1k standings sampler: budgeted, and refusing to invent a cohort.

Fully offline. The fetcher is a stub that records every endpoint it is asked
for, so "spends no requests" and "asks for exactly these pages" are assertions
rather than hopes. The real :class:`RequestBudget` is used unmocked, because the
budget is the thing under test.

Two properties matter more than the parsing:

1. **Before the first deadline the sampler produces nothing.** The overall
   standings are empty until a gameweek is scored and every picks endpoint 404s
   until its deadline passes. A sampler that returned rows here would be
   fabricating the cohort the whole field model is supposed to measure.
2. **The spend is declared before it happens.** ``--dry-run`` costs zero
   requests and prints the arithmetic, so a human approves the number before
   the crawl walks 750 entries at a 1.1s pace.
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import pytest

from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget
from fpl_edge.ingest.http import Fetched
from fpl_edge.ingest.rivals.top1k import (
    OVERALL_LEAGUE_ID,
    PAGE_SIZE,
    SOURCE_PREFIX,
    collect,
    plan,
    run,
)

UTC = dt.timezone.utc
GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
GW2 = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
BEFORE_GW1 = GW1 - dt.timedelta(days=2)
AFTER_GW1 = GW1 + dt.timedelta(days=2)


class StubFetcher:
    """A RivalsFetcher-shaped stand-in that never touches the network."""

    def __init__(self, budget: RequestBudget, *, standings_entries: int = 0,
                 picks_ok: bool = True, deadlines: tuple[str, str] = (
                     "2026-08-21T17:30:00Z", "2026-08-28T17:30:00Z")) -> None:
        self.budget = budget
        self.standings_entries = standings_entries
        self.picks_ok = picks_ok
        # Overridable so a test that goes through ``run`` -- which stamps its
        # own wall-clock ``now`` -- can put the deadlines unambiguously in the
        # past instead of depending on the day the suite runs.
        self.deadlines = deadlines
        self.calls: list[tuple[str, dict | None]] = []

    def get_json(self, endpoint: str, params: dict | None = None) -> Fetched:
        self.calls.append((endpoint, params))
        self.budget.charge(endpoint.split("/")[0])
        return Fetched(
            body=self._body(endpoint, params), fetched_at=dt.datetime.now(UTC),
            sha256="stub", body_path=Path("/dev/null"), http_status=200,
            from_cache=False,
        )

    def close(self) -> None:  # pragma: no cover - parity with the real fetcher
        pass

    def _body(self, endpoint: str, params: dict | None):
        if endpoint == "bootstrap-static/":
            return {"events": [
                {"id": i + 1, "deadline_time": d}
                for i, d in enumerate(self.deadlines)
            ]}
        if endpoint.startswith(f"leagues-classic/{OVERALL_LEAGUE_ID}"):
            page = int((params or {}).get("page_standings", 1))
            start = (page - 1) * PAGE_SIZE
            n = max(0, min(PAGE_SIZE, self.standings_entries - start))
            results = [
                {"entry": 900_000 + start + i, "rank": start + i + 1,
                 "player_name": f"P{start + i}", "entry_name": f"T{start + i}"}
                for i in range(n)
            ]
            return {"standings": {"results": results,
                                  "has_next": start + n < self.standings_entries}}
        if "/picks/" in endpoint:
            if not self.picks_ok:
                return None                     # a real 404
            eid = int(endpoint.split("/")[1])
            picks = [
                {"element": (eid % 300) + i + 1, "position": i + 1,
                 "multiplier": 2 if i == 0 else (1 if i < 11 else 0),
                 "is_captain": i == 0, "is_vice_captain": i == 1}
                for i in range(15)
            ]
            return {"picks": picks, "active_chip": "3xc" if eid % 7 == 0 else None}
        raise AssertionError(f"stub asked for an unexpected endpoint: {endpoint}")


# -- the declared budget ------------------------------------------------------


def test_plan_is_the_request_arithmetic_a_human_approves():
    p = plan(750)
    assert p["standings_pages"] == math.ceil(750 / PAGE_SIZE) == 15
    assert p["requests_total"] == 1 + 15 + 750 == 766
    assert p["requests_bootstrap"] + p["requests_standings"] + p["requests_picks"] \
        == p["requests_total"]
    # ~1.1s of enforced spacing per request.
    assert p["minutes_at_polite_pace"] == pytest.approx(766 * 1.1 / 60, abs=0.05)
    assert plan(500)["requests_total"] == 1 + 10 + 500
    assert plan(1000)["requests_total"] == 1 + 20 + 1000


def test_plan_refuses_a_cohort_size_outside_the_sane_range():
    for bad in (0, -1, 10_001):
        with pytest.raises(ValueError, match="sane range"):
            plan(bad)


def test_dry_run_spends_nothing_and_reports_the_plan():
    """The whole point: approve the spend before it is spent."""
    out = run(n_entries=750, dry_run=True)
    assert out == {"dry_run": True, "plan": plan(750)}
    assert "requests" not in out          # no budget was even constructed


# -- the refusal before GW1 locks --------------------------------------------


def test_before_any_deadline_the_sampler_refuses_and_costs_one_request():
    """Pre-GW1 there is no top-1k in the world. Do not invent one.

    One request (the bootstrap) is enough to learn that no deadline has passed;
    the sampler must then stop rather than walk 15 standings pages to be handed
    empty results 15 times.
    """
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=0)
    frames, summary = collect(fetcher, n_entries=750, now=BEFORE_GW1)

    assert frames == {}
    assert "no gameweek deadline has passed" in summary["skipped"]
    assert budget.spent == 1
    assert [c[0] for c in fetcher.calls] == ["bootstrap-static/"]
    assert summary["plan"]["requests_total"] == 766


def test_an_explicitly_requested_unlocked_gameweek_is_refused():
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=1000)
    frames, summary = collect(fetcher, n_entries=100, gw=2, now=AFTER_GW1)
    assert frames == {}
    assert summary["skipped"] == "GW2 has not locked; its picks are private"
    assert budget.spent == 1


def test_empty_standings_after_a_deadline_are_reported_not_padded():
    """The league is populated at first scoring, not at rollover."""
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=0)
    frames, summary = collect(fetcher, n_entries=200, now=AFTER_GW1)
    assert frames == {}
    assert "nothing to sample yet" in summary["skipped"]
    # bootstrap + exactly one standings page, then stop.
    assert budget.spent == 2


# -- the run that does produce a cohort --------------------------------------


def test_a_locked_gameweek_produces_a_labelled_top1k_cohort():
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=1000)
    frames, summary = collect(fetcher, n_entries=120, now=AFTER_GW1)

    assert summary["gw"] == 1
    assert summary["season"] == "2026-27"
    assert summary["standings"]["entries"] == 120
    # bootstrap + ceil(120/50)=3 standings pages + 120 picks.
    assert budget.spent == 1 + 3 + 120

    managers = frames["dim_manager"]
    assert len(managers) == 120
    assert managers["source"].str.startswith(f"{SOURCE_PREFIX}:2026-27:gw1:rank").all()
    # Rank is preserved in the source string, so cohort membership is itself
    # point-in-time: this week's top-1k is not last week's.
    assert managers["source"].iloc[0].endswith("rank1")

    picks = frames["fact_manager_pick"]
    assert len(picks) == 120 * 15
    # Picks are stamped with the deadline, not the crawl time: that is what
    # makes a Snapshot before the deadline unable to see them.
    assert (picks["as_of"] == GW1).all()
    assert picks["is_captain"].sum() == 120

    chips = frames["fact_manager_chip"]
    assert set(chips["chip"]) == {"3xc"}
    assert (chips["as_of"] == GW1).all()


def test_the_sample_stops_at_n_entries_not_at_the_page_boundary():
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=1000)
    frames, summary = collect(fetcher, n_entries=75, now=AFTER_GW1)
    assert summary["standings"]["pages"] == 2
    assert len(frames["dim_manager"]) == 75


def test_managers_whose_picks_404_are_dropped_and_counted():
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=60, picks_ok=False)
    frames, summary = collect(fetcher, n_entries=60, now=AFTER_GW1)
    assert summary["picks"]["not_found"] == 60
    assert summary["picks"]["ok"] == 0
    assert "fact_manager_pick" not in frames
    # The manager rows still exist; they are a real cohort with no squads yet.
    assert len(frames["dim_manager"]) == 60


# -- the budget is a hard stop ------------------------------------------------


def test_the_budget_stops_the_crawl_and_the_receipt_says_where():
    """A partial sample is still a sample; a silent one is not."""
    budget = RequestBudget(limit=20)
    fetcher = StubFetcher(budget, standings_entries=1000)
    with pytest.raises(BudgetExhausted) as exc:
        collect(fetcher, n_entries=750, now=AFTER_GW1)
    assert budget.spent == 20
    assert "20/20 network requests" in budget.receipt()
    assert "Raise the limit explicitly" in str(exc.value)


def test_run_reports_its_spend_even_when_the_budget_is_exhausted(monkeypatch):
    """``run`` must always hand back a receipt, exhausted or not."""
    import fpl_edge.ingest.rivals.top1k as mod

    made: dict = {}

    def fake_fetcher(budget, *, offline=False):
        made["budget"] = budget
        return StubFetcher(
            budget, standings_entries=1000,
            deadlines=("2020-08-14T17:30:00Z", "2020-08-21T17:30:00Z"),
        )

    monkeypatch.setattr(mod, "RivalsFetcher", fake_fetcher)
    out = run(n_entries=750, budget_limit=12)

    assert out["budget_exhausted"]
    assert out["requests"]["limit"] == 12
    assert out["requests"]["spent"] == 12
    assert "write" not in out, "nothing was collected, so nothing may be written"


def test_run_defaults_its_budget_to_the_declared_plan(monkeypatch):
    import fpl_edge.ingest.rivals.top1k as mod

    seen: dict = {}

    def fake_fetcher(budget, *, offline=False):
        seen["limit"] = budget.limit
        return StubFetcher(budget, standings_entries=0)

    monkeypatch.setattr(mod, "RivalsFetcher", fake_fetcher)
    monkeypatch.setattr(mod, "_write", lambda *a, **k: pytest.fail("no write expected"))
    out = run(n_entries=300)
    assert seen["limit"] == plan(300)["requests_total"] + 10
    assert out["requests"]["spent"] <= seen["limit"]
