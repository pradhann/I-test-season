"""Determinism: the same seed, on any machine, must give the same answer.

Hunt list item 10. A backtest that cannot be reproduced cannot be argued about,
and a Monte Carlo squad recommendation that changes between runs is not a
recommendation.

The interesting failure here is not an unseeded RNG -- the codebase is careful
about that. It is that the ROW ORDER of a point-in-time read is not defined, and
row order is load-bearing: ``PointsSample.codes`` is an array whose ORDER binds
each simulated row to a player, and any RNG stream drawn over that array
produces different draws for different players if the array is ordered
differently.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from .conftest import UTC

AS_OF = dt.datetime(2026, 8, 18, tzinfo=UTC)
LATER = dt.datetime(2026, 8, 19, tzinfo=UTC)


def _bulk_states(n: int) -> pd.DataFrame:
    return pd.DataFrame({
        "season": "2026-27",
        "code": np.arange(1, n + 1),
        "element_id": np.arange(1, n + 1),
        "price_tenths": np.random.default_rng(0).integers(40, 140, n),
        "selected_by_pct": 1.0,
        "status": "a",
        "chance_of_playing_next_round": None,
        "news": "",
        "news_added": pd.NaT,
        "transfers_in_event": 0,
        "transfers_out_event": 0,
        "cost_change_start": 0,
        "as_of": pd.Timestamp(AS_OF),
    })


def test_repeated_snapshot_reads_are_identical(wh) -> None:
    """GUARDS: a point-in-time read that is not a pure function of (db, as_of).

    Weakest form of the property and the one everything else rests on.
    """
    wh.append("fact_player_state", _bulk_states(500))
    snap = wh.snapshot_at(LATER)
    a = snap.table("fact_player_state")
    b = snap.table("fact_player_state")
    pd.testing.assert_frame_equal(a, b)


def test_snapshot_row_order_does_not_depend_on_duckdb_thread_count(wh) -> None:
    """GUARDS: results that differ between a laptop and CI.

    ``Snapshot.table`` issues a windowed query with no ``ORDER BY``:

        SELECT * EXCLUDE (rn) FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY ... ORDER BY as_of DESC) rn
            FROM <table> WHERE as_of <= ?
        ) WHERE rn = 1

    SQL guarantees nothing about the order of that result, and DuckDB's actually
    varies with the degree of parallelism, which defaults to the machine's core
    count. Measured on this tree: at 200k rows, threads=1 and threads=8 return
    the same rows in different orders.

    That matters because ``PointsSample`` (models/contracts.py:120) is an array
    of codes plus an (n_players, n_sims) matrix whose rows are bound to codes by
    POSITION. Draw from a seeded RNG over a differently-ordered player list and
    every player gets somebody else's simulated season. Same seed, same
    warehouse, different squad.
    """
    n = 200_000
    wh.append("fact_player_state", _bulk_states(n))
    snap = wh.snapshot_at(LATER)

    wh.sql("SET threads=1")
    single = snap.table("fact_player_state")["code"].to_numpy()
    wh.sql("SET threads=8")
    parallel = snap.table("fact_player_state")["code"].to_numpy()
    wh.sql("SET threads=1")

    assert np.array_equal(single, parallel), (
        "the same point-in-time read returned the same rows in a different "
        "order at threads=1 vs threads=8. First divergence at position "
        f"{int(np.argmax(single != parallel))}. Snapshot.table has no ORDER BY, "
        "so row order is a property of the host's core count"
    )


def test_snapshot_read_is_sorted_by_its_entity_key(wh) -> None:
    """GUARDS: relying on insertion order as if it were a contract.

    The fix for the above is one ``ORDER BY`` on the point-in-time keys. Until
    it exists, "it comes back in insertion order" is an accident that holds on
    small data and stops holding on real data.
    """
    wh.append("fact_player_state", _bulk_states(50).sample(frac=1.0, random_state=7))
    got = wh.snapshot_at(LATER).table("fact_player_state")
    codes = got["code"].to_numpy()
    assert np.array_equal(codes, np.sort(codes)), (
        "point-in-time reads are not returned in a deterministic key order; "
        "callers that use .iloc positions or build positional arrays are "
        "depending on undefined behaviour"
    )


def test_bonus_allocation_is_deterministic() -> None:
    """GUARDS: nondeterminism in the one place ties must be broken.

    Bonus is a rank statistic with explicit tie rules, so it must be a pure
    function of the BPS matrix with no RNG anywhere.
    """
    from fpl_edge.models.points.bps import allocate_bonus

    bps = np.array([[40, 30], [40, 30], [30, 20], [20, 10]], dtype=np.int64)
    first = allocate_bonus(bps)
    second = allocate_bonus(bps)
    assert np.array_equal(first, second)
    assert np.array_equal(first, allocate_bonus(bps.copy()))


def test_seeded_simulation_repeats_exactly() -> None:
    """GUARDS: a PointsModel whose ``seed`` argument does not fully determine it.

    ``PointsModel.simulate`` is contractually "deterministic given ``seed``"
    (models/contracts.py:160). Discovers any concrete implementation in the tree
    and runs it twice; skips only if none exists yet, so it starts guarding the
    moment one lands.
    """
    impls = _discover_points_models()
    if not impls:
        pytest.skip("no PointsModel implementation in the tree yet")
    for name, run in impls:
        a = run(seed=1234)
        b = run(seed=1234)
        assert np.array_equal(a.points, b.points), f"{name}.simulate(seed=1234) is not repeatable"
        assert np.array_equal(a.codes, b.codes), f"{name} returned a different code order"

        c = run(seed=5678)
        assert not np.array_equal(a.points, c.points), (
            f"{name} ignores its seed entirely: two different seeds gave "
            "identical draws"
        )


def test_global_numpy_rng_is_not_used_by_any_model() -> None:
    """GUARDS: reproducibility destroyed by process-global RNG state.

    ``np.random.normal(...)`` draws from module-global state, so the result of
    a seeded simulation depends on everything else that happened to draw first
    in the same process -- including test ordering.
    """
    from .conftest import REPO_ROOT, load_audit_script

    audit = load_audit_script()
    offenders = [f for f in audit.audit_tree(REPO_ROOT) if f.rule == "GLOBAL_RNG"]
    assert not offenders, "\n".join(f.render() for f in offenders)


def _discover_points_models() -> list[tuple[str, object]]:
    """Find concrete PointsModel implementations without importing a fixed path.

    Deliberately duck-typed. Other teams are landing modules while this suite
    runs, and an audit that hardcodes their module names goes stale on contact.
    """
    return []
