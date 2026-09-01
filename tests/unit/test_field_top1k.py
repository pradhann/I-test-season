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

from fpl_edge.ingest.rivals.client import RequestBudget
from fpl_edge.ingest.http import Fetched
from fpl_edge.ingest.rivals.crawl import _incomplete
from fpl_edge.ingest.rivals.top1k import (
    OVERALL_LEAGUE_ID,
    PAGE_SIZE,
    SOURCE_PREFIX,
    STAGES,
    SampleSizeUnavailable,
    _sampled_so_far,
    collect,
    plan,
    run,
)


def top1k_stages() -> tuple[str, ...]:
    return STAGES

UTC = dt.timezone.utc
GW1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
GW2 = dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)
BEFORE_GW1 = GW1 - dt.timedelta(days=2)
AFTER_GW1 = GW1 + dt.timedelta(days=2)


class StubFetcher:
    """A RivalsFetcher-shaped stand-in that never touches the network."""

    def __init__(self, budget: RequestBudget, *, standings_entries: int = 0,
                 picks_ok: bool = True, transfers_ok: bool = True,
                 deadlines: tuple[str, str] = (
                     "2026-08-21T17:30:00Z", "2026-08-28T17:30:00Z")) -> None:
        self.budget = budget
        self.standings_entries = standings_entries
        self.picks_ok = picks_ok
        self.transfers_ok = transfers_ok
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
        if endpoint.endswith("/transfers/"):
            if not self.transfers_ok:
                return None                     # a real 404
            eid = int(endpoint.split("/")[1])
            # One GW1 transfer each, plus one for a gameweek this season does
            # not have -- parse_transfers must drop the undatable one rather
            # than stamp it with the crawl time.
            return [
                {"element_in": 1, "element_in_cost": 55, "element_out": 2,
                 "element_out_cost": 60, "event": 1,
                 "time": "2026-08-20T09:00:00Z", "entry": eid},
                {"element_in": 3, "element_in_cost": 45, "element_out": 4,
                 "element_out_cost": 50, "event": 99,
                 "time": "2026-08-20T09:05:00Z", "entry": eid},
            ]
        raise AssertionError(f"stub asked for an unexpected endpoint: {endpoint}")


# -- the declared budget ------------------------------------------------------


def test_plan_is_the_request_arithmetic_a_human_approves():
    p = plan(750, transfers_top=500)
    assert p["standings_pages"] == math.ceil(750 / PAGE_SIZE) == 15
    assert p["requests_total"] == 1 + 15 + 750 + 500 == 1266
    assert (p["requests_bootstrap"] + p["requests_standings"]
            + p["requests_picks"] + p["requests_transfers"]) == p["requests_total"]
    # ~1.1s of enforced spacing per request.
    assert p["minutes_at_polite_pace"] == pytest.approx(1266 * 1.1 / 60, abs=0.05)
    assert plan(500, transfers_top=500)["requests_total"] == 1 + 10 + 500 + 500
    assert plan(1000, transfers_top=500)["requests_total"] == 1 + 20 + 1000 + 500


def test_the_plan_declares_the_transfer_requests_it_will_spend():
    """The transfer stage must be visible in the arithmetic a human approves.

    A stage whose cost is not in ``plan()`` is a stage that can be added,
    starved, or removed without the declared budget ever changing -- which is
    precisely how this sampler ran for days fetching no transfers at all while
    its receipt looked exactly as expected.
    """
    # Capped below the sample size: only the best-ranked N cost a request.
    assert plan(2000, transfers_top=500)["requests_transfers"] == 500
    # Never more transfer requests than there are managers to ask about.
    assert plan(100, transfers_top=500)["requests_transfers"] == 100
    # Disabling the stage removes its cost from the declared total.
    off = plan(750, transfers_top=0)
    assert off["requests_transfers"] == 0
    assert off["requests_total"] == 1 + 15 + 750


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
    assert summary["plan"]["requests_total"] == 1266


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
    # bootstrap + ceil(120/50)=3 standings pages + 120 picks + 120 transfers
    # (the transfer cap is above the sample size here, so all of them).
    assert budget.spent == 1 + 3 + 120 + 120

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


def test_the_cohort_gets_transfers_stamped_with_the_deadline():
    """The top-1k cohort is the only one with real pick coverage; it must have
    transfer coverage too, or the warehouse can see what the field owns and
    never how it got there."""
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=40)
    frames, summary = collect(fetcher, n_entries=40, now=AFTER_GW1)

    assert summary["stages"]["transfers"] == "ok"
    transfers = frames["fact_manager_transfer"]
    # One datable transfer per manager; the GW99 row in the stub payload has no
    # deadline and must be dropped rather than stamped with the crawl time.
    assert len(transfers) == 40
    assert set(transfers["gw"]) == {1}
    assert (transfers["as_of"] == GW1).all()
    # The raw click time is kept separately: when a manager transferred is a
    # behaviour, but it was private until the deadline.
    assert (transfers["time_utc"] < transfers["as_of"]).all()
    assert set(transfers["entry_id"]) == set(frames["dim_manager"]["entry_id"])


def test_transfers_are_capped_to_the_best_ranked_managers():
    """Transfers have a 3h TTL and re-cost a request per manager every night,
    so the stage is capped in rank order as the sample grows."""
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=100)
    frames, _summary = collect(fetcher, n_entries=100, transfers_top=10, now=AFTER_GW1)

    asked = [c[0] for c in fetcher.calls if c[0].endswith("/transfers/")]
    assert len(asked) == 10
    # Rank order, not an arbitrary ten: the standings pages arrive ranked and
    # the slice is taken off the front.
    top_ten = list(frames["dim_manager"]["entry_id"][:10])
    assert [int(e.split("/")[1]) for e in asked] == top_ten


def test_transfers_can_be_switched_off_without_looking_like_an_outage():
    """A deliberately disabled stage is a skip, not a starvation."""
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=20)
    frames, summary = collect(fetcher, n_entries=20, transfers_top=0, now=AFTER_GW1)

    assert not any(c[0].endswith("/transfers/") for c in fetcher.calls)
    assert summary["stages"]["transfers"].startswith("skipped:")
    assert _incomplete(summary["stages"]) == []
    assert "fact_manager_transfer" not in frames


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
    """A partial sample is still a sample; a silent one is not.

    The budget still hard-stops at the declared limit. What changed on
    2026-08-27 is that exhaustion inside a stage no longer aborts the run --
    it is caught, named against that stage, and the stages after it still get
    their reserved requests. The exhaustion must remain *visible*: it is
    reported in ``stages`` and in ``incomplete_stages``, never swallowed.
    """
    budget = RequestBudget(limit=20)
    fetcher = StubFetcher(budget, standings_entries=1000)
    frames, summary = collect(fetcher, n_entries=750, now=AFTER_GW1)

    assert budget.spent == 20, "the declared limit is still a hard stop"
    assert "20/20 network requests" in budget.receipt()
    # The stages that could not finish say so in their own words.
    assert summary["stages"]["picks"].startswith("incomplete")
    assert summary["stages"]["transfers"].startswith("incomplete")
    assert "budget" in summary["stages"]["transfers"]
    assert _incomplete(summary["stages"]) == ["picks", "transfers"]


def test_a_starved_transfer_stage_is_never_silently_absent():
    """THE regression test for the outage that emptied fact_manager_transfer.

    A crawl that runs out of budget before the transfer stage used to be
    indistinguishable, from outside, from a crawl that had no transfers to
    fetch: no frame, no key in the summary, exit code 0, receipt green. The
    elite crawl ran in exactly that state for days and nothing noticed.

    So the contract is now: the transfer stage is NAMED before the run starts,
    and if it does not complete it appears in ``incomplete_stages`` -- whether
    it was starved mid-flight or never reached at all. This test fails if that
    outage ever becomes invisible again.
    """
    # Budget large enough for standings and the whole picks stage, but not
    # enough for transfers to finish: the exact shape of the production
    # failure, where the cheap-and-critical stage is the one that loses.
    budget = RequestBudget(limit=175)
    fetcher = StubFetcher(budget, standings_entries=60)
    frames, summary = collect(fetcher, n_entries=60, now=AFTER_GW1)

    # Picks got through; transfers did not.
    assert summary["stages"]["picks"] == "ok"
    assert "fact_manager_pick" in frames
    # Since the fetched-means-kept fix (picks.py ingest_transfers), a starved
    # stage RETURNS whatever it paid for before the budget died -- 270 paid
    # requests were once discarded by the old unwind. The partial frame is
    # therefore allowed (and expected when anything was fetched); what must
    # never change is that the shortfall stays LOUD:
    assert summary["stages"]["transfers"] != "ok"
    assert "transfers" in _incomplete(summary["stages"])
    fetched = summary["transfers"].get("requested", 0)
    if fetched and "fact_manager_transfer" in frames:
        # every row present belongs to an entry the budget actually covered
        assert len(set(frames["fact_manager_transfer"]["entry_id"])) <= fetched


def test_a_transfer_stage_that_never_ran_at_all_counts_as_incomplete():
    """Absence must be an outage, not a silence.

    ``stages`` is seeded with every expected stage set to ``not_reached``, so a
    stage that is skipped by a code path nobody remembered to update is still
    reported. Asserting on the seeded value directly is what stops a future
    refactor from simply not writing a transfers key.
    """
    stages = {name: "not_reached" for name in top1k_stages()}
    assert "transfers" in stages
    assert _incomplete(stages) == sorted(stages)


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
    # Nothing was collected, so nothing may be written -- but the receipt must
    # SAY so. `if frames: summary["write"] = ...` left an exhausted run with no
    # write key at all, which is indistinguishable from a run that wrote fine.
    assert out["write"]["status"] == "nothing-to-write"
    assert out["write"]["rows"] == {}
    assert out["failures"], "a run that committed nothing reported no failure"


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


# -- per-gameweek facts come free from the standings page ---------------------


def test_standings_rows_become_fact_manager_gw_without_extra_requests():
    """points/total/rank are on the standings page; fetching histories to get
    them would treble the crawl for data already in hand."""
    budget = RequestBudget(limit=800)
    fetcher = StubFetcher(budget, standings_entries=100)
    frames, _summary = collect(fetcher, n_entries=60, now=AFTER_GW1)

    gw_rows = frames["fact_manager_gw"]
    assert len(gw_rows) == 60
    assert (gw_rows["gw"] == 1).all()
    assert list(gw_rows["overall_rank"][:3]) == [1, 2, 3]
    # No entry/{id}/history/ request was made for any of this.
    assert not any("history" in c[0] for c in fetcher.calls)


def test_a_real_standings_page_parses_rank_points_and_totals():
    """Against the archived GW1 payload, not a hand-built approximation."""
    import json
    from pathlib import Path

    fixture = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "rivals"
         / "standings_gw1.json").read_text()
    )

    class FixtureFetcher(StubFetcher):
        def _body(self, endpoint, params):
            if endpoint.startswith("leagues-classic/"):
                return fixture
            return super()._body(endpoint, params)

    budget = RequestBudget(limit=50)
    fetcher = FixtureFetcher(budget, standings_entries=3)
    frames, summary = collect(fetcher, n_entries=3, now=AFTER_GW1)

    managers = frames["dim_manager"]
    assert len(managers) == 3
    assert managers["source"].iloc[0] == f"{SOURCE_PREFIX}:2026-27:gw1:rank1"
    gw_rows = frames["fact_manager_gw"]
    first = gw_rows.iloc[0]
    # Values from the real page: rank 1 scored 116 in GW1.
    assert int(first["overall_rank"]) == 1
    assert int(first["points"]) == 116
    assert int(first["total_points"]) == 116


# -- nightly growth -----------------------------------------------------------


def test_grow_targets_current_sample_plus_k_capped_at_10k(monkeypatch):
    import fpl_edge.ingest.rivals.top1k as mod

    monkeypatch.setattr(mod, "_sampled_so_far", lambda db: 1500)
    out = run(grow=500, dry_run=True)
    assert out["plan"]["n_entries"] == 2000

    monkeypatch.setattr(mod, "_sampled_so_far", lambda db: 9800)
    out = run(grow=500, dry_run=True)
    assert out["plan"]["n_entries"] == 10_000, "growth must stop at the top-10k"

    monkeypatch.setattr(mod, "_sampled_so_far", lambda db: 0)
    out = run(grow=750, dry_run=True)
    assert out["plan"]["n_entries"] == 750, "growth from nothing is a first run"


# -- the write is a stage too, and nobody was watching it ---------------------


def _after_gw1_fetcher(budget, *, entries=60):
    """A stub whose deadlines are unambiguously in the past for ``run``."""
    return StubFetcher(
        budget, standings_entries=entries,
        deadlines=("2020-08-14T17:30:00Z", "2020-08-21T17:30:00Z"),
    )


def test_a_locked_write_fails_the_sample_instead_of_reporting_success(monkeypatch):
    """``_write`` returns {"status": "locked"} after six attempts.

    Nothing read that. Every stage completed, ``incomplete_stages`` was empty,
    the process exited 0 and post_gw recorded the step green -- for a sample
    whose rows never reached the warehouse.
    """
    import fpl_edge.ingest.rivals.top1k as mod

    monkeypatch.setattr(mod, "RivalsFetcher",
                        lambda budget, offline=False: _after_gw1_fetcher(budget))
    monkeypatch.setattr(mod, "_write", lambda frames, db, summary, **kw: {
        "status": "locked", "attempts": 6, "error": "held by pid 999",
    })
    out = run(n_entries=50, transfers_top=10, budget_limit=400)

    assert out["incomplete_stages"] == [], "the fetch stages really did finish"
    assert out["failures"], "a sample that wrote nothing reported no failure"
    assert any("locked" in f for f in out["failures"]), out["failures"]


def test_a_healthy_sample_publishes_an_empty_failure_list(monkeypatch):
    import fpl_edge.ingest.rivals.top1k as mod

    monkeypatch.setattr(mod, "RivalsFetcher",
                        lambda budget, offline=False: _after_gw1_fetcher(budget))
    monkeypatch.setattr(mod, "_write", lambda frames, db, summary, **kw: {
        "status": "ok", "attempts": 1,
        "rows": {t: len(df) for t, df in frames.items()},
    })
    out = run(n_entries=50, transfers_top=10, budget_limit=400)
    assert out["incomplete_stages"] == []
    assert out["failures"] == []


def test_a_declared_pre_gw1_skip_commits_nothing_and_that_is_correct(monkeypatch):
    """The one run that is allowed to write nothing says why in the receipt.

    Distinguishing this from the locked-write case is the whole point: both
    commit zero rows, and only one of them is an outage.
    """
    import fpl_edge.ingest.rivals.top1k as mod

    monkeypatch.setattr(
        mod, "RivalsFetcher",
        lambda budget, offline=False: StubFetcher(
            budget, standings_entries=0,
            deadlines=("2020-08-14T17:30:00Z", "2020-08-21T17:30:00Z"),
        ),
    )
    out = run(n_entries=50, transfers_top=10, budget_limit=400)
    assert out["skipped"]
    assert out["write"]["status"] == "nothing-to-write"
    assert out["failures"] == [], out["failures"]
    assert all(v.startswith("skipped:") for v in out["stages"].values())


def test_top1k_main_exits_nonzero_when_the_write_never_committed(
    monkeypatch, capsys
):
    import sys as _sys

    import fpl_edge.ingest.rivals.top1k as mod

    monkeypatch.setattr(_sys, "argv", ["top1k", "--n", "50"])
    monkeypatch.setattr(mod, "run", lambda **kw: {
        "stages": {n: "ok" for n in STAGES}, "incomplete_stages": [],
        "failures": ["write: locked: held by pid 999"],
    })
    assert mod.main() == 1
    assert "locked" in capsys.readouterr().err


def test_allow_incomplete_does_not_forgive_a_sample_that_wrote_nothing(
    monkeypatch, capsys
):
    import sys as _sys

    import fpl_edge.ingest.rivals.top1k as mod

    monkeypatch.setattr(_sys, "argv", ["top1k", "--allow-incomplete"])
    monkeypatch.setattr(mod, "run", lambda **kw: {
        "incomplete_stages": ["transfers"],
        "failures": ["write: locked: held by pid 999"],
    })
    assert mod.main() == 1
    capsys.readouterr()


# -- --grow must not shrink the cohort when it cannot read it ----------------


class _BrokenWarehouse:
    """A read copy that opens and then fails every query."""

    def __init__(self):
        self.closed = False

    def sql(self, *a, **k):
        raise RuntimeError("Catalog Error: table with name ... does not exist")

    def close(self):
        self.closed = True


def test_a_broken_sample_read_refuses_to_grow_rather_than_shrinking(monkeypatch):
    """``except Exception: return 0`` retargeted the cohort, permanently.

    The scheduled job runs ``--grow 300``. With the swallow in place, any
    failure of this query -- a corrupt catalog, a half-materialised read copy,
    a renamed column -- turned the target from (existing + 300) into 300. The
    sampler then walked the top 300, reported every stage ok, exited 0, and the
    cohort had gone from thousands to hundreds with nothing saying so. The next
    night it would do it again.
    """
    broken = _BrokenWarehouse()
    monkeypatch.setattr("fpl_edge.store.Warehouse.read_copy",
                        classmethod(lambda cls, *a, **k: broken))

    with pytest.raises(SampleSizeUnavailable) as exc:
        _sampled_so_far(None)
    assert "shrink" in str(exc.value)
    assert broken.closed, "the throwaway read copy must still be closed"


def test_a_refused_grow_exits_nonzero_instead_of_sampling_the_top_300(
    monkeypatch, capsys
):
    import sys as _sys

    import fpl_edge.ingest.rivals.top1k as mod

    def _boom(db_path):
        raise SampleSizeUnavailable("could not read ... would silently shrink it")

    monkeypatch.setattr(mod, "_sampled_so_far", _boom)
    monkeypatch.setattr(mod, "RivalsFetcher",
                        lambda *a, **k: pytest.fail("no crawl may start"))
    monkeypatch.setattr(_sys, "argv", ["top1k", "--grow", "300", "--budget", "1200"])
    assert mod.main() == 1
    assert "shrink" in capsys.readouterr().err


def test_an_empty_warehouse_is_a_first_run_not_a_broken_read(tmp_path):
    """Absent is still allowed to mean zero -- against a real empty database.

    The distinction has to be made positively (the tables are not in the
    catalog) rather than by catching whatever the query raises, or the fix is
    just the old swallow with more words.
    """
    from fpl_edge.store import Warehouse

    db = tmp_path / "fpl.duckdb"
    with Warehouse(str(db)) as wh:
        wh.sql("SELECT 1")
    assert _sampled_so_far(str(db)) == 0
