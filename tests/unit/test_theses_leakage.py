"""Leakage proof: a thesis created at time T carries only data visible at T.

``model_verdict_at_creation`` is the registry's most corruptible field: if it
were ever recomputed after results landed, the model's "verdict at creation"
would quietly become a verdict with hindsight, and every calibration read off
it would be a lie. These tests pin the two directions of the guarantee:

* values captured at T come from the state as of T, not from later corrections
  (the seeder plants a price/ownership change after T specifically so the wrong
  implementation has something to leak), and
* resolution -- the only process that ever rewrites a thesis file -- leaves the
  creation block exactly as it was written.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.theses.create import capture_model_verdict, create_thesis
from fpl_edge.theses.model import ClaimType, ThesisSource
from fpl_edge.theses.resolve import resolve_theses
from fpl_edge.theses.store import ThesesStore
from tests.unit.test_theses_resolve import (
    SEASON,
    T_CREATE,
    T_LATE_STATE,
    T_RESOLVE,
    seed_theses_warehouse,
)

UTC = dt.timezone.utc

#: After the planted price change AND after GW5-6 finalised.
T_AFTER = dt.datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture()
def wh(tmp_path):
    warehouse = seed_theses_warehouse(tmp_path / "wh.duckdb")
    yield warehouse
    warehouse.close()


def test_verdict_at_T_sees_only_the_world_at_T(wh):
    early = capture_model_verdict(
        wh.snapshot_at(T_CREATE), season=SEASON, player_code=101, gw_start=5
    )
    late = capture_model_verdict(
        wh.snapshot_at(T_AFTER), season=SEASON, player_code=101, gw_start=5
    )

    # State: the warehouse holds a £11.0m/45% row stamped 2026-09-25. At
    # T_CREATE (Sep 16) it does not exist yet and must not appear.
    assert T_CREATE < T_LATE_STATE < T_AFTER
    assert early["price"] == 10.0 and early["ownership_pct"] == 30.0
    assert late["price"] == 11.0 and late["ownership_pct"] == 45.0

    # History: at T_CREATE only GW1-4 have finalised, where Hero scored 2/gw.
    # The 10-point weeks start in GW5; a leaky implementation would average
    # them in. 6 gws have landed by T_AFTER: (4*2 + 2*10) / 6.
    assert early["season_ppg"] == pytest.approx(2.0)
    assert early["form_points_last3"] == pytest.approx(6.0)
    assert late["season_ppg"] == pytest.approx(28 / 6)


def test_thesis_file_created_at_T_carries_the_T_verdict(wh, tmp_path):
    store = ThesesStore(tmp_path / "theses")
    thesis, path = create_thesis(
        wh, raw_input="Hero looks primed", source=ThesisSource.USER_CHAT,
        player="hero", claim_type=ClaimType.BUY, gw_start=5, horizon_gws=3,
        as_of=T_CREATE, store=store, season=SEASON,
    )
    on_disk = store.load_open()[0][0]
    verdict = on_disk.model_verdict_at_creation
    assert verdict["as_of"] == "2026-09-16T12:00:00Z"
    assert verdict["price"] == 10.0
    assert verdict["ownership_pct"] == 30.0
    assert verdict["season_ppg"] == pytest.approx(2.0)


def test_resolution_never_rewrites_the_creation_block(wh, tmp_path):
    store = ThesesStore(tmp_path / "theses")
    create_thesis(
        wh, raw_input="Hero looks primed", source=ThesisSource.USER_CHAT,
        player="hero", claim_type=ClaimType.BUY, gw_start=5, horizon_gws=3,
        as_of=T_CREATE, store=store, season=SEASON,
    )
    before = store.load_open()[0][0]

    resolve_theses(wh, season=SEASON, as_of=T_RESOLVE, store=store,
                   dry_run=False, commit=False, sync_registry=False)

    after = store.load_resolved()[0][0]
    # The resolution block appeared; nothing about creation moved -- even
    # though Hero's price, ownership, and scoring record all changed between
    # creation and resolution.
    assert after.resolution is not None
    assert after.model_verdict_at_creation == before.model_verdict_at_creation
    assert after.created == before.created
    assert after.falsifiable_prediction == before.falsifiable_prediction
    assert after.comparator_codes == before.comparator_codes
    assert after.raw_input == before.raw_input
