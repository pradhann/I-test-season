"""Contracts of the chat toolbelt's engine-side pieces.

The MCP tools themselves live in the FPL-MCP repository; what is tested here
is everything they lean on that must not silently change shape:

* the watchlist store (``fpl_edge.interfaces.watchlist``) — append/resolve
  semantics, one open row per player, the digest section, and graceful
  behaviour on a warehouse that has never seen a watchlist;
* the analysis helpers in ``FPL-MCP/tools/chat_core.py`` — the 10-second
  budget (a slow SUCCESS is also a failure, per Argus's contract), ``$param``
  substitution that binds rather than interpolates, and the capped summary
  rendering with its omitted-count marker.

chat_core is imported straight from the sibling FPL-MCP checkout; the tests
skip rather than fail when that checkout is absent (CI of the engine alone).
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from pathlib import Path

import pytest

from fpl_edge.interfaces.watchlist import Watchlist, digest_lines
from fpl_edge.interfaces.testing import SEASON, seed_warehouse
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 8, 18, 22, 50, tzinfo=UTC)

_MCP_ROOT = Path(__file__).resolve().parents[2].parent / "FPL-MCP"


def _chat_core():
    if not _MCP_ROOT.exists():
        pytest.skip(f"FPL-MCP checkout not found at {_MCP_ROOT}")
    if str(_MCP_ROOT) not in sys.path:
        sys.path.insert(0, str(_MCP_ROOT))
    from tools import chat_core  # noqa: PLC0415

    return chat_core


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    return seed_warehouse(tmp_path / "w.duckdb", n_gws=8)


# -- watchlist store ----------------------------------------------------------


def test_watchlist_add_list_resolve_roundtrip(wh: Warehouse) -> None:
    wl = Watchlist(wh)
    item = wl.add(code=101, player_name="Palmer", season=str(SEASON),
                  note="liking him", now=T0)
    assert item.startswith("wl_")

    items = wl.open_items(str(SEASON))
    assert len(items) == 1
    row = items.iloc[0]
    assert row["player_name"] == "Palmer"
    assert row["note"] == "liking him"

    assert wl.resolve(code=101, season=str(SEASON), now=T0) == 1
    assert wl.open_items(str(SEASON)).empty
    # Resolved, not deleted: the history row survives.
    assert int(wh.sql("SELECT count(*) n FROM watchlist").iloc[0]["n"]) == 1


def test_watchlist_readd_supersedes_rather_than_duplicating(wh: Warehouse) -> None:
    wl = Watchlist(wh)
    wl.add(code=101, player_name="Palmer", season=str(SEASON), note="old", now=T0)
    wl.add(code=101, player_name="Palmer", season=str(SEASON), note="new",
           now=T0 + dt.timedelta(hours=1))

    items = wl.open_items(str(SEASON))
    assert len(items) == 1  # one open row per player
    assert items.iloc[0]["note"] == "new"
    assert int(wh.sql("SELECT count(*) n FROM watchlist").iloc[0]["n"]) == 2


def test_watchlist_note_is_data_not_sql(wh: Warehouse) -> None:
    wl = Watchlist(wh)
    hostile = "x'); DROP TABLE watchlist; --"
    wl.add(code=7, player_name="Bob", season=str(SEASON), note=hostile, now=T0)
    items = wl.open_items(str(SEASON))
    assert items.iloc[0]["note"] == hostile  # stored verbatim, executed never


def test_watchlist_remove_when_absent_reports_zero(wh: Warehouse) -> None:
    assert Watchlist(wh).resolve(code=999, season=str(SEASON), now=T0) == 0


def test_digest_lines_shape_and_emptiness(wh: Warehouse) -> None:
    # Table exists (Watchlist ran the migration) but is empty: no section.
    Watchlist(wh)
    assert digest_lines(wh, str(SEASON)) == []

    Watchlist(wh).add(code=101, player_name="Palmer", season=str(SEASON),
                      note="liking him", now=T0)
    Watchlist(wh).add(code=102, player_name="Semenyo", season=str(SEASON), now=T0)
    lines = digest_lines(wh, str(SEASON))
    assert lines[0] == "Watchlist (2 open):"
    assert "  You wanted: Palmer — 'liking him'" in lines
    assert "  You wanted: Semenyo" in lines


def test_digest_lines_survive_a_warehouse_without_the_table(tmp_path) -> None:
    """The T-30h digest must not fail because nothing was ever watched."""
    wh = seed_warehouse(tmp_path / "bare.duckdb", n_gws=4)
    # No Watchlist() constructed, so the migration never ran here.
    assert "watchlist" not in set(
        wh.sql("SELECT table_name FROM duckdb_tables()")["table_name"]
    )
    assert digest_lines(wh, str(SEASON)) == []


# -- analysis budget ----------------------------------------------------------


def test_budget_passes_a_fast_call_through() -> None:
    core = _chat_core()
    result, err = core.run_with_budget(lambda: 42, budget_s=5.0)
    assert err is None and result == 42


def test_budget_rejects_a_slow_call_with_the_contract_text() -> None:
    core = _chat_core()
    result, err = core.run_with_budget(lambda: time.sleep(1.0) or 42, budget_s=0.2)
    assert result is None
    assert err == core.BUDGET_ERROR
    assert "push filtering and aggregation into SQL" in err
    assert "if it genuinely cannot fit, say so" in err


def test_budget_rejects_a_slow_success_too() -> None:
    """Argus's rule: finishing late is failing — the consumer hits the same wall."""
    core = _chat_core()

    def slow_success():
        time.sleep(0.3)
        return "done"

    result, err = core.run_with_budget(slow_success, budget_s=0.25)
    # The future may or may not resolve before the timeout fires; either path
    # must end in the budget error, never in a quietly late success.
    assert result is None
    assert err == core.BUDGET_ERROR


def test_budget_propagates_the_functions_own_error() -> None:
    core = _chat_core()
    with pytest.raises(ValueError, match="boom"):
        core.run_with_budget(lambda: (_ for _ in ()).throw(ValueError("boom")),
                             budget_s=5.0)


# -- $param substitution ------------------------------------------------------


def test_substitute_params_binds_in_order_and_repeats() -> None:
    core = _chat_core()
    sql, binds, missing = core.substitute_params(
        "SELECT * FROM t WHERE season = $season AND gw = $gw OR gw = $gw",
        {"season": "2026-27", "gw": 3},
    )
    assert sql == "SELECT * FROM t WHERE season = ? AND gw = ? OR gw = ?"
    assert binds == ["2026-27", 3, 3]
    assert missing == []


def test_substitute_params_never_interpolates() -> None:
    core = _chat_core()
    hostile = "'; DROP TABLE idea; --"
    sql, binds, _ = core.substitute_params("SELECT $x", {"x": hostile})
    assert hostile not in sql  # the value travels as a bind, not as SQL text
    assert binds == [hostile]


def test_substitute_params_reports_missing_instead_of_guessing() -> None:
    core = _chat_core()
    sql, binds, missing = core.substitute_params("SELECT $a, $b", {"a": 1})
    assert missing == ["b"]
    assert "$b" in sql  # left in place so the error can quote it
    assert binds == [1]


# -- capped rendering ---------------------------------------------------------


def test_render_rows_marks_omitted_count() -> None:
    core = _chat_core()
    rows = [{"n": i} for i in range(300)]
    out = core.render_rows(["n"], rows, max_rows=200)
    assert "...100 more rows omitted — aggregate or filter in SQL" in out
    assert out.count("\n") <= 204


def test_render_rows_scan_truncated_renders_a_floor() -> None:
    core = _chat_core()
    rows = [{"n": i} for i in range(250)]
    out = core.render_rows(["n"], rows, max_rows=200, scan_truncated=True)
    assert "...50+ more rows omitted" in out


def test_render_rows_byte_cap_wins_over_row_cap() -> None:
    core = _chat_core()
    rows = [{"blob": "x" * 1024} for _ in range(200)]
    out = core.render_rows(["blob"], rows, max_rows=200, max_bytes=10 * 1024)
    assert len(out.encode()) <= 11 * 1024
    assert "more rows omitted" in out
