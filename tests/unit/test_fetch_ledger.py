"""The fetch ledger and write-on-change (PIPELINES.md §4.2).

The dangerous edges are all PIT edges: a backfill must never be deduplicated
against the present, a contradiction must still refuse, and an error run must
never satisfy the "already checked" gate.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.store import fetch_ledger
from fpl_edge.store.warehouse import ConflictingFactError, Warehouse

UTC = dt.UTC
T0 = dt.datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
T1 = dt.datetime(2026, 8, 21, 10, 0, tzinfo=UTC)
T2 = dt.datetime(2026, 8, 22, 10, 0, tzinfo=UTC)


def _wh(tmp_path):
    return Warehouse(tmp_path / "t.duckdb")


def _state(code, status, chance, as_of):
    return {"season": "2026-27", "code": code, "element_id": code, "price_tenths": 50,
            "status": status, "chance_of_playing_next_round": chance,
            "news": None, "news_added": None, "as_of": as_of}


def _frame(*rows):
    return pd.DataFrame(list(rows))


# ----------------------------------------------------------- write-on-change


def test_an_unchanged_newer_row_is_dropped_and_counted(tmp_path):
    wh = _wh(tmp_path)
    wh.append("fact_player_state", _frame(_state(1, "a", 100, T0)))
    written, unchanged = wh.append_measured(
        "fact_player_state", _frame(_state(1, "a", 100, T1)),
        change_dedup=True)
    assert (written, unchanged) == (0, 1)
    assert len(wh.sql("SELECT * FROM fact_player_state")) == 1
    wh.close()


def test_a_changed_newer_row_is_written(tmp_path):
    wh = _wh(tmp_path)
    wh.append("fact_player_state", _frame(_state(1, "a", 100, T0)))
    written, unchanged = wh.append_measured(
        "fact_player_state", _frame(_state(1, "d", 75, T1)),
        change_dedup=True)
    assert (written, unchanged) == (1, 0)
    wh.close()


def test_a_mixed_batch_splits_correctly(tmp_path):
    wh = _wh(tmp_path)
    wh.append("fact_player_state",
              _frame(_state(1, "a", 100, T0), _state(2, "a", 100, T0)))
    written, unchanged = wh.append_measured(
        "fact_player_state",
        _frame(_state(1, "a", 100, T1),     # unchanged -> counted
               _state(2, "i", 0, T1),       # changed   -> written
               _state(3, "a", 100, T1)),    # brand new -> written
        change_dedup=True)
    assert (written, unchanged) == (2, 1)
    wh.close()


def test_a_backfill_is_never_deduplicated_against_the_present(tmp_path):
    """History is not compared to today. A row OLDER than the stored latest
    writes even when its values coincide with the current ones -- deduping it
    would erase the fact that the value ALSO held at the earlier instant,
    which is exactly what a point-in-time read needs."""
    wh = _wh(tmp_path)
    wh.append("fact_player_state", _frame(_state(1, "a", 100, T2)))
    written, unchanged = wh.append_measured(
        "fact_player_state", _frame(_state(1, "a", 100, T0)),
        change_dedup=True)
    assert (written, unchanged) == (1, 0)
    wh.close()


def test_contradiction_refusal_survives_change_dedup(tmp_path):
    """Same key, same as_of, different values must STILL refuse -- the
    change-dedup filter runs after the contradiction check and must never
    swallow one."""
    wh = _wh(tmp_path)
    wh.append("fact_player_state", _frame(_state(1, "a", 100, T0)))
    with pytest.raises(ConflictingFactError):
        wh.append("fact_player_state", _frame(_state(1, "i", 0, T0)),
                  change_dedup=True)
    wh.close()


def test_default_append_behaviour_is_byte_identical(tmp_path):
    """change_dedup defaults OFF: the hundreds of existing call sites keep
    writing every distinct-as_of row exactly as before."""
    wh = _wh(tmp_path)
    wh.append("fact_player_state", _frame(_state(1, "a", 100, T0)))
    written = wh.append("fact_player_state", _frame(_state(1, "a", 100, T1)))
    assert written == 1
    wh.close()


# ------------------------------------------------------------------ the ledger


def test_record_run_writes_ok_with_counts(tmp_path):
    wh = _wh(tmp_path)
    with fetch_ledger.record_run(wh, "ingest_projections", "fplform") as rec:
        rec.add(written=10, unchanged=4600)
        rec.credits = 0.0
    row = fetch_ledger.last_run(wh, "ingest_projections", "fplform")
    assert row["status"] == "ok"
    assert row["rows_written"] == 10 and row["rows_unchanged"] == 4600
    wh.close()


def test_a_raising_run_lands_as_error_and_reraises(tmp_path):
    wh = _wh(tmp_path)
    with pytest.raises(RuntimeError), fetch_ledger.record_run(wh, "p", "s"):
        raise RuntimeError("provider fell over")
    row = fetch_ledger.last_run(wh, "p", "s", ok_only=False)
    assert row["status"] == "error"
    assert "provider fell over" in row["note"]
    wh.close()


def test_checked_within_is_the_skip_gate(tmp_path):
    """A recent ok (or skipped_fresh) run satisfies the gate; an error run
    NEVER does, so failures always retry rather than hiding behind their own
    timestamp."""
    wh = _wh(tmp_path)
    assert not fetch_ledger.checked_within(wh, "p", hours=1)

    with fetch_ledger.record_run(wh, "p") as rec:
        rec.add(0, 100)
    assert fetch_ledger.checked_within(wh, "p", hours=1)

    with pytest.raises(RuntimeError), fetch_ledger.record_run(wh, "q"):
        raise RuntimeError("boom")
    assert not fetch_ledger.checked_within(wh, "q", hours=1)
    wh.close()


def test_skipped_fresh_counts_as_a_check(tmp_path):
    """A skip that verified freshness IS a check -- otherwise every gated tick
    would look unchecked and the gate would defeat itself."""
    wh = _wh(tmp_path)
    with fetch_ledger.record_run(wh, "p") as rec:
        rec.status = "skipped_fresh"
        rec.note = "all markets younger than 24h"
    assert fetch_ledger.checked_within(wh, "p", hours=1)
    wh.close()
