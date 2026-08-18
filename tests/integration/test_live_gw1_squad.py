"""End-to-end: build a legal squad for the upcoming deadline from live data.

Exercises the whole lower stack together -- warehouse, point-in-time snapshot,
rule registry, greedy selection and the replay validator. Skips when the
warehouse has not been built.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.eval.baselines import TemplateStrategy
from fpl_edge.eval.replay import apply_decision
from fpl_edge.rules import rules
from fpl_edge.store import DEFAULT_DB, Warehouse
from fpl_edge.types import GwId, Position

pytestmark = pytest.mark.skipif(
    not DEFAULT_DB.exists(), reason="warehouse not built; run `make ingest` first"
)
SEASON = "2026-27"


@pytest.fixture(scope="module")
def snapshot():
    with Warehouse(read_only=True) as wh:
        # Look the deadline up from "now": the fixture list was only OBSERVED at
        # ingestion time, so an earlier snapshot correctly cannot see it. Reading
        # it from a January snapshot would be asking what we knew before we knew
        # anything -- which point-in-time filtering rightly answers with nothing.
        latest = wh.snapshot_at(dt.datetime.now(dt.timezone.utc))
        try:
            deadline = latest.deadline(SEASON, 1)
        except KeyError:
            pytest.skip("no 2026-27 events loaded")
        yield wh.snapshot_at(deadline), deadline


def test_template_squad_is_legal_and_within_budget(snapshot) -> None:
    snap, _ = snapshot
    players = snap.players(SEASON)
    if players.empty:
        pytest.skip("no players loaded")

    decision = TemplateStrategy().decide(snap, None, SEASON, GwId(1))
    price = dict(zip(players["code"], players["price_tenths"]))
    team_of = dict(zip(players["code"], players["team_code"]))

    # apply_decision runs the full legality check and raises on any violation.
    state, hits, out, into = apply_decision(None, decision, price, team_of, GwId(1))

    r = rules()
    assert len(decision.picks) == r.get("squad.size")
    assert hits == 0 and out == ()
    assert 0 <= state.bank_tenths <= r.get("squad.budget_tenths")

    counts = {p: sum(1 for x in decision.picks if x.position is p) for p in Position}
    want = r.get("squad.select_by_position")
    assert {k.name: v for k, v in counts.items()} == want


def test_reserve_goalkeeper_is_first_on_the_bench(snapshot) -> None:
    """A goalkeeper can only be replaced by a goalkeeper, so any other bench
    slot for the reserve keeper wastes a substitution priority."""
    snap, _ = snapshot
    if snap.players(SEASON).empty:
        pytest.skip("no players loaded")
    picks = TemplateStrategy().decide(snap, None, SEASON, GwId(1)).picks
    bench_gks = [p for p in picks if p.position is Position.GKP and not p.is_starter]
    assert len(bench_gks) == 1
    assert bench_gks[0].order == 12


def test_deadline_is_utc_and_matches_the_api(snapshot) -> None:
    """Guards the browser-local-time deadline bug found during rule capture."""
    _, deadline = snapshot
    assert deadline.utcoffset() == dt.timedelta(0)
    assert deadline == dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.timezone.utc)


def test_snapshot_before_deadline_reports_gw1_as_next(snapshot) -> None:
    snap, deadline = snapshot
    with Warehouse(read_only=True) as wh:
        earlier = wh.snapshot_at(deadline - dt.timedelta(seconds=1))
        assert earlier.next_gw(SEASON) == 1
