"""Parsing and observability. The `as_of` assertions are the important ones.

Everything else in this file is ordinary parsing coverage. The tests about when
a fact became public are the ones that stop this package from quietly poisoning
every backtest that reads it: a rival's squad stamped with the crawl time rather
than the deadline lets a model "know" the elite's GW7 team on the Sunday, after
the captain's hat-trick, and every copying result computed on top of that is
worthless in a way that produces excellent-looking numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.rivals.history import parse_history
from fpl_edge.ingest.rivals.picks import parse_picks, parse_transfers
from fpl_edge.ingest.rivals.roster import _league_members

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rivals"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text())


UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 8, 19, 12, 0, tzinfo=UTC)
DEADLINE_GW3 = dt.datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


# -- history ----------------------------------------------------------------

def test_past_seasons_parse_with_ranks_and_percentages():
    past, current, chips = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert len(past) == 4
    row = past[past["season"] == "2018/19"].iloc[0]
    assert row["overall_rank"] == 9524
    assert row["rank_percentage"] == pytest.approx(0.2)
    assert row["total_points"] == 2387


def test_season_labels_keep_fpl_slash_form():
    """'2018/19' is not silently rewritten to the warehouse's '2018-19'.

    These rows describe a manager's finish and never join to dim_player, so
    converting would invent an equivalence between two unrelated spellings.
    """
    past, _c, _ch = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert set(past["season"]) == {"2018/19", "2019/20", "2021/22", "2022/23"}


def test_current_gameweeks_carry_hits_and_value():
    _p, current, _ch = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert len(current) == 4
    gw3 = current[current["gw"] == 3].iloc[0]
    assert gw3["event_transfers"] == 3
    assert gw3["event_transfers_cost"] == 8
    assert gw3["value_tenths"] == 1008


def test_chips_parse_with_gameweek():
    _p, _c, chips = parse_history(
        42, _load("history_multi_season.json"), as_of=AS_OF, season="2026-27"
    )
    assert list(chips["chip"]) == ["wildcard"]
    assert list(chips["gw"]) == [4]


def test_empty_history_yields_empty_frames_not_an_exception():
    past, current, chips = parse_history(
        1, {"past": [], "current": [], "chips": []}, as_of=AS_OF, season="2026-27"
    )
    assert past.empty and current.empty and chips.empty


def test_missing_rank_percentage_becomes_null_not_zero():
    """A null percentile is 'unknown'. Zero would mean 'finished first'."""
    body = {"past": [{"season_name": "2020/21", "total_points": 2000,
                      "rank": 500, "rank_percentage": ""}], "current": [], "chips": []}
    past, _c, _ch = parse_history(1, body, as_of=AS_OF, season="2026-27")
    assert pd.isna(past.iloc[0]["rank_percentage"])


# -- picks ------------------------------------------------------------------

def test_picks_are_stamped_with_the_deadline_not_the_crawl_time():
    picks, _chips = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    assert (picks["as_of"] == DEADLINE_GW3).all(), (
        "a squad stamped with the crawl instant lets a backtest read it after "
        "the gameweek was scored"
    )


def test_multiplier_is_taken_from_the_api_not_rebuilt_from_flags():
    """Triple captain is multiplier 3, and no boolean pair encodes that."""
    picks, _chips = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    cap = picks[picks["is_captain"]].iloc[0]
    assert cap["element_id"] == 301
    assert cap["multiplier"] == 3
    bench = picks[picks["slot"] > 11]
    assert (bench["multiplier"] == 0).all()


def test_active_chip_becomes_a_chip_row():
    _picks, chips = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    assert len(chips) == 1
    assert chips.iloc[0]["chip"] == "3xc"
    assert chips.iloc[0]["as_of"] == DEADLINE_GW3


def test_no_active_chip_produces_no_chip_row():
    body = dict(_load("picks_gw3.json"))
    body["active_chip"] = None
    _picks, chips = parse_picks(
        999, 3, body, season="2026-27", deadline=DEADLINE_GW3
    )
    assert chips.empty


def test_fifteen_picks_with_bench_slots_preserved():
    picks, _c = parse_picks(
        999, 3, _load("picks_gw3.json"), season="2026-27", deadline=DEADLINE_GW3
    )
    assert len(picks) == 15
    assert sorted(picks["slot"]) == list(range(1, 16))


# -- transfers --------------------------------------------------------------

DEADLINES = {
    2: dt.datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
    3: DEADLINE_GW3,
}


def test_transfers_are_stamped_with_their_gameweek_deadline():
    df = parse_transfers(999, _load("transfers.json"), season="2026-27", deadlines=DEADLINES)
    gw3 = df[df["gw"] == 3].iloc[0]
    assert gw3["as_of"] == DEADLINE_GW3
    # The manager MADE the transfer before the deadline; it became public AT it.
    assert gw3["time_utc"] < gw3["as_of"]


def test_transfer_for_an_unknown_gameweek_is_dropped_not_guessed():
    """Event 99 has no deadline. Admitting it would mean inventing an as_of."""
    df = parse_transfers(999, _load("transfers.json"), season="2026-27", deadlines=DEADLINES)
    assert 99 not in set(df["gw"])
    assert len(df) == 2


def test_transfer_costs_survive_parsing():
    df = parse_transfers(999, _load("transfers.json"), season="2026-27", deadlines=DEADLINES)
    row = df[df["element_in"] == 305].iloc[0]
    assert row["element_in_cost"] == 75
    assert row["element_out_cost"] == 71


def test_empty_transfer_list_is_the_normal_preseason_answer():
    df = parse_transfers(999, [], season="2026-27", deadlines=DEADLINES)
    assert df.empty
    assert list(df.columns)[:3] == ["entry_id", "season", "gw"]


# -- league membership ------------------------------------------------------

class _StubFetcher:
    def __init__(self, body):
        self.body = body
        self.calls = 0

    def get_json(self, endpoint, params=None):
        self.calls += 1

        class _F:
            pass

        f = _F()
        f.body = self.body
        return f


def test_membership_read_from_new_entries_when_standings_are_empty():
    """Pre-season every member sits in new_entries; standings is empty.

    This is the shape the API actually returns before GW1 and the reason the
    crawl can build a pool at all right now.
    """
    stub = _StubFetcher(_load("league_standings_preseason.json"))
    members, pages = _league_members(stub, 76109, kind="classic", max_pages=4)
    assert {m["entry_id"] for m in members} == {111, 222}
    assert pages == 1, "paging continued past a page that said has_next=false"
    assert members[0]["player_name"] == "Ada Lovelace"


def test_membership_paging_stops_at_the_cap():
    body = _load("league_standings_preseason.json")
    body["new_entries"]["has_next"] = True
    stub = _StubFetcher(body)
    _members, pages = _league_members(stub, 1, kind="classic", max_pages=3)
    assert pages == 3, "the page cap did not bound an endlessly-paging league"


# ===========================================================================
# The crawl's stage discipline.
#
# Everything above tests that a payload parses correctly. This section tests
# something the parsers cannot: that the stages which produce those payloads
# actually RUN, and that when one does not, somebody is told.
#
# The bug these exist for: crawl_elite ran nightly with a 400-request budget
# against a 2,015-candidate pool, spent all 400 on the history sweep (which
# ran first and uncapped, and whose 12h cache TTL meant every night re-fetched
# the same first ~370 entries), and raised BudgetExhausted before ingest_picks
# or ingest_transfers were ever called. fact_manager_transfer held zero rows
# for the entire life of the job. Every receipt said ok.
# ===========================================================================

from fpl_edge.ingest.rivals import crawl as crawl_mod  # noqa: E402
from fpl_edge.ingest.rivals import roster as roster_mod  # noqa: E402
from fpl_edge.ingest.rivals.client import RequestBudget  # noqa: E402
from fpl_edge.ingest.rivals.roster import Candidate, build_pool  # noqa: E402

GW1_DEADLINE = "2020-08-14T17:30:00Z"   # safely in the past on any run day


class CrawlStubFetcher:
    """A RivalsFetcher-shaped stand-in that records the order it was called in.

    Order is the assertion here, not just the counts: "transfers happened"
    is not the property that was broken, "transfers happened *before the
    budget was gone*" is.
    """

    def __init__(self, budget: RequestBudget, *, picks_per_manager: int = 15) -> None:
        self.budget = budget
        self.picks_per_manager = picks_per_manager
        self.calls: list[str] = []

    def get_json(self, endpoint, params=None):
        # Charge BEFORE recording, exactly as the real fetcher does: a call
        # that the budget refuses never reaches the network, so it must not
        # appear in the record of what we asked the API for.
        self.budget.charge(_crawl_kind(endpoint))
        self.calls.append(endpoint)

        class _F:
            pass

        f = _F()
        f.body = self._body(endpoint)
        return f

    def close(self):  # pragma: no cover - parity with the real fetcher
        pass

    def kinds(self) -> list[str]:
        """The call sequence collapsed to stage kinds, in order, deduped."""
        out: list[str] = []
        for endpoint in self.calls:
            kind = _crawl_kind(endpoint)
            if not out or out[-1] != kind:
                out.append(kind)
        return out

    def _body(self, endpoint: str):
        if endpoint == "bootstrap-static/":
            return {"events": [{"id": 1, "deadline_time": GW1_DEADLINE}]}
        if "/picks/" in endpoint:
            return {
                "active_chip": "bboost",
                "picks": [
                    {"element": i + 1, "position": i + 1, "multiplier": 1,
                     "is_captain": i == 0, "is_vice_captain": i == 1}
                    for i in range(self.picks_per_manager)
                ],
            }
        if endpoint.endswith("/transfers/"):
            eid = int(endpoint.split("/")[1])
            return [{"element_in": 1, "element_in_cost": 50, "element_out": 2,
                     "element_out_cost": 55, "event": 1,
                     "time": "2020-08-13T09:00:00Z", "entry": eid}]
        if endpoint.endswith("/history/"):
            return {"past": [], "chips": [],
                    "current": [{"event": 1, "points": 60, "total_points": 60,
                                 "overall_rank": 1000, "bank": 5, "value": 1000,
                                 "event_transfers": 0, "event_transfers_cost": 0,
                                 "points_on_bench": 0}]}
        raise AssertionError(f"stub asked for an unexpected endpoint: {endpoint}")


def _crawl_kind(endpoint: str) -> str:
    if "/picks/" in endpoint:
        return "picks"
    if endpoint.endswith("/transfers/"):
        return "transfers"
    if endpoint.endswith("/history/"):
        return "history"
    if endpoint == "bootstrap-static/":
        return "bootstrap"
    return "entry"


@pytest.fixture
def stub_pool(monkeypatch):
    """Replace pool-building with a fixed cohort, so the stage tests are about
    stages rather than about the snowball."""

    def _install(n: int):
        candidates = [Candidate(entry_id=1000 + i, source="elite_list") for i in range(n)]
        managers = pd.DataFrame([
            {"entry_id": c.entry_id, "player_name": None, "entry_name": None,
             "region": None, "years_active": None, "favourite_team_id": None,
             "started_event": None, "source": c.source, "as_of": AS_OF}
            for c in candidates
        ])
        empty = pd.DataFrame(columns=["entry_id", "league_id", "league_name",
                                      "league_type", "scoring", "as_of"])
        monkeypatch.setattr(
            crawl_mod.roster, "build_pool",
            lambda fetcher, **kw: (candidates, roster_mod.PoolReport(), managers, empty),
        )
        return candidates

    return _install


def _run_crawl(monkeypatch, stub_pool, *, n: int, budget: int):
    """Run crawl.run against the stub, capturing the frames it tried to write."""
    stub_pool(n)
    written: dict = {}
    fetchers: list[CrawlStubFetcher] = []

    def fake_fetcher(budget_obj, *, offline=False):
        f = CrawlStubFetcher(budget_obj)
        fetchers.append(f)
        return f

    def fake_write(frames, db_path, summary, **kw):
        written.update(frames)
        # Row counts, not an empty dict: "the write returned ok and appended
        # nothing" is itself a failure the crawl now reports, so a stub that
        # fakes it would make every test here assert against a broken run.
        return {
            "status": "ok", "attempts": 1,
            "rows": {t: int(len(df)) for t, df in frames.items()
                     if df is not None and not df.empty},
        }

    monkeypatch.setattr(crawl_mod, "RivalsFetcher", fake_fetcher)
    monkeypatch.setattr(crawl_mod, "_write", fake_write)
    summary = crawl_mod.run(budget_limit=budget, max_candidates=n)
    return summary, written, fetchers[0]


# -- THE regression test ----------------------------------------------------

def test_a_budget_exhausted_crawl_reports_the_transfer_stage_as_incomplete(
    monkeypatch, stub_pool
):
    """A starved transfer stage must be impossible to mistake for a healthy run.

    This is the test that would have caught the outage on day one. Before the
    fix, a crawl whose budget ran out before ``ingest_transfers`` produced:
    no ``fact_manager_transfer`` frame, no ``transfers`` key in the summary,
    and a receipt reporting a full, successful spend. Nothing distinguished it
    from a crawl that had legitimately finished, so nothing complained, for
    days.

    Now the transfer stage is declared in :data:`crawl.STAGES` before the run
    begins, so it is reported whether it runs or not, and a stage that did not
    complete lands in ``incomplete_stages``.
    """
    # 200 candidates need ~200 picks + ~200 transfers + ~200 histories. 120
    # requests cannot cover that, so at least one stage must be starved.
    summary, written, _f = _run_crawl(monkeypatch, stub_pool, n=200, budget=120)

    # Every stage is accounted for by NAME -- absence is not an option.
    assert set(summary["stages"]) == set(crawl_mod.STAGES)
    assert "transfers" in summary["stages"]

    # The starvation is reported, not swallowed.
    assert summary["incomplete_stages"], (
        "a crawl that could not finish its stages reported no incomplete stages; "
        "this is exactly the invisible outage the stage accounting exists to stop"
    )
    assert "transfers" in summary["incomplete_stages"]
    assert summary["stages"]["transfers"] != "ok"


def test_a_crawl_that_never_reaches_a_stage_still_names_it(monkeypatch, stub_pool):
    """Not-reached must be louder than not-mentioned.

    A budget of zero dies on the opening bootstrap request, so not one stage
    body ever executes. The old summary simply had no keys for them -- which
    reads identically to "there was nothing to do". The new one seeds every
    declared stage with ``not_reached`` up front, so a stage that never ran is
    still reported and still fails the run.
    """
    summary, _written, _f = _run_crawl(monkeypatch, stub_pool, n=200, budget=0)
    assert summary["budget_exhausted"]
    assert set(summary["stages"].values()) == {"not_reached"}
    assert summary["incomplete_stages"] == sorted(crawl_mod.STAGES)


def test_picks_and_transfers_are_fetched_before_the_history_sweep(
    monkeypatch, stub_pool
):
    """Stage order is the fix, not an accident of how the function reads.

    History is the most expensive stage and the least time-critical: a finished
    season's rank is immutable. Picks and transfers become readable at the
    deadline and are what the copying and differential models consume this
    week. So they go first, and this test pins that.
    """
    summary, _written, fetcher = _run_crawl(monkeypatch, stub_pool, n=5, budget=400)

    assert summary["incomplete_stages"] == []
    assert fetcher.kinds() == ["bootstrap", "picks", "transfers", "history"]


def test_the_history_sweep_cannot_consume_the_whole_budget(monkeypatch, stub_pool):
    """Reordering alone would only move the starvation one stage down.

    The property that actually fixes this is that NO stage may spend the whole
    budget. With 300 candidates and 200 requests, every stage is short -- but
    picks and transfers must each still get their reserved share rather than
    one stage taking everything.
    """
    summary, _written, fetcher = _run_crawl(monkeypatch, stub_pool, n=300, budget=200)

    counts: dict[str, int] = {}
    for endpoint in fetcher.calls:
        kind = _crawl_kind(endpoint)
        counts[kind] = counts.get(kind, 0) + 1

    assert counts["picks"] > 0, "picks must get requests"
    assert counts["transfers"] > 0, "transfers must get requests -- the whole bug"
    assert counts["picks"] < 300, "no stage may spend the entire budget"
    assert counts["transfers"] < 300
    assert sum(counts.values()) <= 200


def test_a_complete_crawl_writes_transfers_and_reports_no_incomplete_stages(
    monkeypatch, stub_pool
):
    """The happy path, asserted on the frame that was empty in production."""
    summary, written, _f = _run_crawl(monkeypatch, stub_pool, n=10, budget=400)

    assert summary["incomplete_stages"] == []
    assert all(v == "ok" for v in summary["stages"].values()), summary["stages"]
    transfers = written["fact_manager_transfer"]
    assert len(transfers) == 10
    assert set(transfers["gw"]) == {1}
    assert "fact_manager_pick" in written
    assert "fact_manager_season" in written


def test_crawl_main_exits_nonzero_when_a_stage_did_not_complete(monkeypatch, capsys):
    """The half of the fix that CI can actually see.

    ``run`` reporting an incomplete stage is useless if the process still exits
    0 -- post_gw.py decides a step succeeded purely on the return code, which
    is why ``crawl_elite`` showed ok=true every night while doing a quarter of
    its job.
    """
    monkeypatch.setattr(sys, "argv", ["crawl", "--budget", "10"])
    monkeypatch.setattr(
        crawl_mod, "run",
        lambda **kw: {"stages": {"transfers": "not_reached"},
                      "incomplete_stages": ["transfers"], "failures": []},
    )
    assert crawl_mod.main() == 1
    assert "transfers" in capsys.readouterr().err

    monkeypatch.setattr(
        crawl_mod, "run",
        lambda **kw: {"incomplete_stages": [], "failures": []},
    )
    assert crawl_mod.main() == 0


def test_allow_incomplete_is_the_only_way_to_pass_with_a_starved_stage(
    monkeypatch, capsys
):
    """A deliberate partial run is fine; a silent one is not. The escape hatch
    has to be typed out on the command line, where a reviewer can see it."""
    monkeypatch.setattr(sys, "argv", ["crawl", "--allow-incomplete"])
    monkeypatch.setattr(
        crawl_mod, "run",
        lambda **kw: {"incomplete_stages": ["transfers"], "failures": []},
    )
    assert crawl_mod.main() == 0


# -- the other doors into the same outage ------------------------------------
#
# Commit 63c9c0b claimed a starved crawl could no longer present as success. It
# made that true of one door -- a stage that ran out of budget -- and left three
# others open. Each of the tests below walks through one of them: a run that
# wrote nothing, a run that had nobody to crawl, and a failure that reached
# nobody. The shared question is the one to ask of every stage in this package:
# if it silently did nothing, what would be different? Where the answer was
# "nothing", these are the assertions that make it something.


def test_a_locked_write_is_a_failure_even_though_every_stage_said_ok(
    monkeypatch, stub_pool
):
    """The write is a stage too, and it was the one nobody watched.

    ``_write`` gives up after six attempts and returns ``{"status": "locked"}``.
    Nothing read that. Every fetch stage genuinely completed, so ``stages`` was
    four times "ok" and ``incomplete_stages`` was empty -- for a run that
    committed zero rows. This is the same outage as the starved history sweep,
    reached by a door the stage accounting does not watch.
    """
    stub_pool(5)
    monkeypatch.setattr(
        crawl_mod, "RivalsFetcher",
        lambda budget, offline=False: CrawlStubFetcher(budget),
    )
    monkeypatch.setattr(crawl_mod, "_write", lambda frames, db, summary, **kw: {
        "status": "locked", "attempts": 6, "error": "held by pid 999",
    })
    summary = crawl_mod.run(budget_limit=400, max_candidates=5)

    # The fetch stages really did finish -- that is exactly why they cannot be
    # the thing that catches this.
    assert summary["incomplete_stages"] == []
    assert all(v == "ok" for v in summary["stages"].values())
    assert summary["failures"], "a crawl that wrote nothing reported no failure"
    assert any("locked" in f for f in summary["failures"])


def test_a_write_that_appended_no_rows_is_not_a_successful_crawl(
    monkeypatch, stub_pool
):
    """status ok + zero rows is the quietest version of the same nothing."""
    stub_pool(5)
    monkeypatch.setattr(
        crawl_mod, "RivalsFetcher",
        lambda budget, offline=False: CrawlStubFetcher(budget),
    )
    monkeypatch.setattr(
        crawl_mod, "_write",
        lambda frames, db, summary, **kw: {"status": "ok", "attempts": 1, "rows": {}},
    )
    summary = crawl_mod.run(budget_limit=400, max_candidates=5)
    assert summary["incomplete_stages"] == []
    assert any("zero rows" in f for f in summary["failures"]), summary["failures"]


def test_crawl_main_exits_nonzero_when_the_write_never_committed(
    monkeypatch, capsys
):
    """The half CI sees. post_gw grades this step on the return code alone."""
    monkeypatch.setattr(sys, "argv", ["crawl", "--budget", "1100"])
    monkeypatch.setattr(crawl_mod, "run", lambda **kw: {
        "stages": {n: "ok" for n in crawl_mod.STAGES},
        "incomplete_stages": [],
        "failures": ["write: locked: held by pid 999"],
    })
    assert crawl_mod.main() == 1
    assert "locked" in capsys.readouterr().err


def test_allow_incomplete_does_not_forgive_a_run_that_wrote_nothing(
    monkeypatch, capsys
):
    """--allow-incomplete buys a deliberate PARTIAL crawl, nothing else.

    A run that committed no rows is not a partial anything, and the escape
    hatch for "I only wanted picks tonight" must not double as an escape hatch
    for "the warehouse was locked and we threw the run away".
    """
    monkeypatch.setattr(sys, "argv", ["crawl", "--allow-incomplete"])
    monkeypatch.setattr(crawl_mod, "run", lambda **kw: {
        "incomplete_stages": ["transfers"],
        "failures": ["write: locked: held by pid 999"],
    })
    assert crawl_mod.main() == 1
    capsys.readouterr()


def test_an_empty_pool_is_a_failure_not_four_ok_stages(monkeypatch, stub_pool):
    """Zero candidates used to buy a perfect receipt for a run that did nothing.

    ``ingest_picks([])``, ``ingest_transfers([])`` and ``ingest_histories([])``
    each spend no requests, raise nothing, and return empty frames, so all three
    left ``_stage`` through its ``else: stages[name] = "ok"`` branch. One
    bootstrap request, four green stages, no incomplete stages, exit 0.

    Live-relevant, not hypothetical: seed verification now rejects all twenty
    expert seeds, so the pool is mini-league + winners + ELITE_1000 and one
    more tightening away from empty.
    """
    summary, _written, fetcher = _run_crawl(monkeypatch, stub_pool, n=0, budget=1100)

    assert summary["failures"], (
        "a crawl with nobody to crawl reported no failure at all"
    )
    assert any("zero candidates" in f for f in summary["failures"])
    assert summary["stages"]["pool"].startswith("failed:")
    assert summary["incomplete_stages"], "the empty pool must fail the run"
    # And it must not have pretended to spend a crawl doing it.
    assert fetcher.kinds() == ["bootstrap"]


def test_stages_after_a_starved_pool_report_not_reached_not_ok(
    monkeypatch, stub_pool
):
    """Three of four stage statuses used to be actively false.

    When the pool stage itself is starved the run does fail overall -- but it
    failed while claiming picks, transfers and history had all completed, which
    is worse than a bare failure: it points the next person at the wrong stage.
    """
    summary, _written, _f = _run_crawl(monkeypatch, stub_pool, n=0, budget=1100)
    for name in ("picks", "transfers", "history"):
        status = summary["stages"][name]
        assert status.startswith("not_reached"), (name, status)
        assert "pool" in status, "the status must say WHY it never ran"


def test_a_healthy_crawl_publishes_an_empty_failure_list(monkeypatch, stub_pool):
    """The steady state, asserted so the new key cannot quietly stop existing."""
    summary, _written, _f = _run_crawl(monkeypatch, stub_pool, n=10, budget=400)
    assert summary["incomplete_stages"] == []
    assert summary["failures"] == []
    assert summary["write"]["rows"]["fact_manager_transfer"] == 10


# -- post_gw: a failure that reaches nobody has not been reported -------------


def test_a_failed_post_gw_step_is_delivered_to_the_owner(tmp_path):
    """"post_gw will notice a failed crawl" was true and useless.

    ``_run`` did capture the return code and ``main`` did return 1 -- into a
    JSON file under data/warehouse/jobs/ and stdout, neither of which anything
    in this repo reads, under a launchd plist with no KeepAlive and no failure
    action. So ``crawl_elite`` could exit 1 every night for a week and the only
    trace was a file nobody opened.

    The fix is the DAG's own outbox, not a second alerting path: one row, one
    flush, one dedupe key.
    """
    from fpl_edge.interfaces.telegram import FakeTransport, TelegramConfig
    from fpl_edge.jobs import outbox, post_gw
    from fpl_edge.store import Warehouse

    db = tmp_path / "fpl.duckdb"
    report = post_gw.JobReport(started_utc="2026-08-27T02:00:00+00:00")
    report.steps.append(post_gw.StepResult("ingest_live", True, 1.0, ""))
    report.steps.append(post_gw.StepResult(
        "crawl_elite", False, 12.0, "FAILED: stages did not complete: transfers"))

    tx = FakeTransport()
    note = post_gw.notify_failures(
        report, db_path=str(db), transport=tx,
        config=TelegramConfig(token="t", allowed_chat_ids=frozenset({4242})),
        now=dt.datetime(2026, 8, 27, 2, 30, tzinfo=UTC),
    )

    assert tx.sent, "the failure never left the process"
    text = tx.sent[0][1]["text"]
    assert "crawl_elite" in text, "the alert must name the step that failed"
    assert "transfers" in text, "and carry the detail that says which stage"
    assert "ingest_live" not in text, "a healthy step is noise in a failure alert"
    assert "sent 1" in note

    # Durable, not fire-and-forget: the row is stamped, so a re-flush is a no-op.
    with Warehouse(str(db)) as wh:
        assert outbox.pending(wh) == []


def test_a_clean_post_gw_run_notifies_nobody(tmp_path):
    """An alert that arrives every night is an alert nobody reads."""
    from fpl_edge.jobs import post_gw

    report = post_gw.JobReport(started_utc="2026-08-27T02:00:00+00:00")
    report.steps.append(post_gw.StepResult("ingest_live", True, 1.0, ""))
    report.steps.append(post_gw.StepResult("crawl_elite", True, 12.0, ""))

    def _no_warehouse(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("a clean run must not even open the warehouse")

    note = post_gw.notify_failures(
        report, db_path=str(tmp_path / "never.duckdb"), transport=_no_warehouse
    )
    assert "not sent" in note
    assert not (tmp_path / "never.duckdb").exists()


def test_a_broken_alert_channel_cannot_fail_the_job_it_reports_on(tmp_path):
    """Reporting the outage must not become the outage."""
    from fpl_edge.jobs import post_gw

    report = post_gw.JobReport(started_utc="2026-08-27T02:00:00+00:00")
    report.steps.append(post_gw.StepResult("crawl_elite", False, 1.0, "boom"))

    note = post_gw.notify_failures(
        report, db_path="/definitely/not/a/directory/fpl.duckdb"
    )
    assert note.startswith("alert: enqueue failed"), note


def test_a_repeated_failure_enqueues_one_message_per_run(tmp_path):
    """Deterministic ids stop a retried run from doubling the message."""
    from fpl_edge.interfaces.telegram import FakeTransport, TelegramConfig
    from fpl_edge.jobs import post_gw
    from fpl_edge.store import Warehouse

    db = tmp_path / "fpl.duckdb"
    cfg = TelegramConfig(token="t", allowed_chat_ids=frozenset({4242}))
    now = dt.datetime(2026, 8, 27, 2, 30, tzinfo=UTC)
    report = post_gw.JobReport(started_utc="2026-08-27T02:00:00+00:00")
    report.steps.append(post_gw.StepResult("crawl_elite", False, 1.0, "boom"))

    post_gw.notify_failures(report, db_path=str(db), transport=FakeTransport(),
                            config=cfg, now=now)
    post_gw.notify_failures(report, db_path=str(db), transport=FakeTransport(),
                            config=cfg, now=now)
    with Warehouse(str(db)) as wh:
        rows = wh.sql("SELECT * FROM platform_delivery WHERE monitor = 'post_gw'")
    assert len(rows) == 1


# -- stale seeds (B9) -------------------------------------------------------

class SeedStubFetcher:
    """Serves /entry/{id}/ profiles with configurable account-holder names."""

    def __init__(self, names: dict[int, tuple[str, str]]) -> None:
        self.names = names
        self.calls: list[str] = []

    def get_json(self, endpoint, params=None):
        self.calls.append(endpoint)

        class _F:
            pass

        f = _F()
        eid = int(endpoint.strip("/").split("/")[1])
        if endpoint.startswith("leagues-"):
            f.body = {"standings": {"results": [], "has_next": False},
                      "new_entries": {"results": [], "has_next": False}}
            return f
        if eid not in self.names:
            f.body = None
            return f
        first, last = self.names[eid]
        f.body = {
            "player_first_name": first, "player_last_name": last,
            "name": f"Team {eid}", "player_region_name": "England",
            "years_active": 5, "favourite_team": 1, "started_event": 1,
            "leagues": {"classic": [
                {"id": 700_000 + eid, "name": "Secret Elite League",
                 "league_type": "x", "scoring": "c"},
            ], "h2h": []},
        }
        return f


def test_stale_expert_seeds_are_rejected_and_never_seed_the_snowball(monkeypatch):
    """The 1,682-manager problem, in one test.

    Every one of the 20 EXPERT_SEEDS IDs now resolves to a different person.
    Until this check existed they still entered the pool as source='expert' AND
    their league memberships drove the snowball, so 1,682 of 3,498 tracked
    managers are the league-mates of twenty arbitrary strangers. A seed whose
    live profile name does not match must contribute nothing: not a candidate,
    not a dim_manager row, and above all not a league to crawl.
    """
    # Every seed resolves to someone else, which is the live 2026-08 reality.
    fetcher = SeedStubFetcher({
        eid: ("Levi", "Longworth") for eid in roster_mod.EXPERT_SEEDS.values()
    })
    candidates, report, managers, memberships = build_pool(
        fetcher, mini_leagues=(), elite_list_n=0, winners=False,
    )

    assert report.seeds == len(roster_mod.EXPERT_SEEDS)
    assert report.seeds_verified == 0
    assert report.seeds_rejected == len(roster_mod.EXPERT_SEEDS)
    assert all(r["status"] == "name_mismatch" for r in report.seed_rejections)
    # The rejection is itemised, so "the seed list rotted again" is readable
    # off a receipt without re-running the crawl.
    assert report.seed_rejections[0]["actual_name"] == "Levi Longworth"

    # Nothing they touched enters the pool.
    assert [c for c in candidates if c.source == "expert"] == []
    assert not any(c.source.startswith("snowball") for c in candidates)
    assert report.leagues_seen == 0
    assert memberships.empty
    if not managers.empty:
        assert "expert" not in set(managers["source"])
    # And no league request was ever made on their behalf.
    assert not any(c.startswith("leagues-") for c in fetcher.calls)


def test_a_seed_whose_name_still_matches_is_kept_and_snowballed(monkeypatch):
    """The check must be a verification, not a blanket ban.

    If someone refreshes an ID in EXPERT_SEEDS it has to start working again
    with no other change -- otherwise the 'fix' is just a deletion wearing a
    function's clothes.
    """
    seeds = dict(list(roster_mod.EXPERT_SEEDS.items())[:2])
    monkeypatch.setattr(roster_mod, "EXPERT_SEEDS", seeds)
    (name_a, id_a), (name_b, id_b) = list(seeds.items())
    first_a, _, last_a = name_a.partition(" ")
    first_b, _, last_b = name_b.partition(" ")
    fetcher = SeedStubFetcher({id_a: (first_a, last_a), id_b: (first_b, last_b)})

    candidates, report, managers, memberships = build_pool(
        fetcher, mini_leagues=(), elite_list_n=0, winners=False, min_shared_seeds=2,
    )

    assert report.seeds_verified == 2
    assert report.seeds_rejected == 0
    assert {c.entry_id for c in candidates if c.source == "expert"} == {id_a, id_b}
    # Their leagues were read and recorded...
    assert report.leagues_seen == 2
    assert set(memberships["entry_id"]) == {id_a, id_b}
    # ...and the profile identity, not the curated placeholder, is what is
    # written for them.
    kept = managers[managers["entry_id"] == id_a].iloc[0]
    assert kept["region"] == "England"
    assert kept["years_active"] == 5


def test_a_seed_whose_entry_404s_is_a_rejection_not_a_crash():
    fetcher = SeedStubFetcher({})     # every profile 404s
    _candidates, report, _managers, _memberships = build_pool(
        fetcher, mini_leagues=(), elite_list_n=0, winners=False,
    )
    assert report.seeds_rejected == len(roster_mod.EXPERT_SEEDS)
    assert {r["status"] for r in report.seed_rejections} == {"entry_404"}
    assert report.seed_profiles_read == 0


# -- chips (B12) ------------------------------------------------------------

def test_active_chip_is_recorded_verbatim_even_when_the_rate_looks_impossible():
    """`bboost` for 94% of the top-1k at GW1 2026-27 is REAL. Do not 'fix' it.

    Investigated 2026-08-27 against the archived bodies in data/raw/rivals/:
    the payloads say bboost, entry/{id}/history/'s own chips block agrees for
    40 of 40 managers who have both cached, every bboost body has
    points_on_bench == 0 while no-chip bodies average 7.3, and bootstrap-static
    gives bboost start_event=1 for this season with two of each team chip. The
    94% is selection -- the cohort is chosen on GW1 score and a bench boost
    adds points -- and the rate falls monotonically with rank (91% at 1-100,
    81% at 1001-2000).

    This test exists so that the next person who finds the rate implausible
    has to break an assertion that explains why it is not, rather than
    "correcting" a faithful ingest.
    """
    body = dict(_load("picks_gw3.json"))
    body["active_chip"] = "bboost"
    picks, chips = parse_picks(
        999, 1, body, season="2026-27", deadline=DEADLINE_GW3
    )
    assert list(chips["chip"]) == ["bboost"]
    # Multipliers come from the API, so a bench-boosted bench keeps whatever
    # the payload said. Deriving them from is_captain would silently rewrite
    # exactly the chip weeks a chip analysis is about.
    assert list(picks["multiplier"]) == [p["multiplier"] for p in body["picks"]]


def test_an_unknown_future_chip_name_is_carried_through_not_dropped():
    """The chip list is FPL's, not ours; 2026-27 already changed its shape.

    A parser that only admitted chips it recognised would silently lose the
    first gameweek of whatever gets introduced next, and lose it in a way that
    looks like nobody played it.
    """
    body = dict(_load("picks_gw3.json"))
    body["active_chip"] = "manager"
    _picks, chips = parse_picks(999, 1, body, season="2026-27", deadline=DEADLINE_GW3)
    assert list(chips["chip"]) == ["manager"]
