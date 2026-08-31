"""The chat tool's registration contract: importing the server must not die.

The toolbelt registers in-process, so FastMCP evaluates every tool's type
annotations at import time. A live chat turn died with ``InvalidSignature:
Unable to evaluate type annotations for callable 'player_profile'`` while this
tool was being built -- one unresolvable annotation in one module took the
WHOLE toolbelt down, every tool at once. These tests are that outage, pinned:
the full server module must import, list its tools, and list this one among
them, with a schema whose params are the plain runtime types the module
promises.
"""

from __future__ import annotations

import asyncio


def test_the_server_imports_and_lists_tools_with_player_profile_present():
    from fpl_mcp.server import mcp  # the import IS the registration

    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert "player_profile" in names, (
        "player_profile is not registered -- and if this import raised "
        "InvalidSignature instead, an annotation broke the ENTIRE toolbelt"
    )
    # sanity: registration of this tool must not have eaten anyone else
    assert {"query", "player_dossier"} <= names


def test_the_tool_schema_carries_plain_runtime_params():
    from fpl_mcp.server import mcp

    tool = next(t for t in asyncio.run(mcp.list_tools())
                if t.name == "player_profile")
    props = tool.inputSchema["properties"]
    assert props["player"]["type"] == "string"
    assert props["season"]["type"] == "string"
    assert props["fetch_if_missing"]["type"] == "boolean"
    assert tool.inputSchema.get("required") == ["player"]


# ---------------------------------------------------------------------------
# the tool function end to end, against a tmp warehouse and no network
# ---------------------------------------------------------------------------

def _seed(tmp_path, *, with_profile):
    import datetime as dt

    import pandas as pd

    from fpl_edge.ingest.understat import UnderstatStore
    from fpl_edge.store.warehouse import Warehouse

    path = tmp_path / "fpl.duckdb"
    stamp = pd.Timestamp("2026-08-01", tz="UTC")
    wh = Warehouse(path)
    wh.append("dim_player", pd.DataFrame([{
        "season": "2026-27", "code": 223094, "element_id": 1,
        "web_name": "Haaland", "first_name": "Erling", "second_name": "Haaland",
        "position": 4, "team_code": 43, "as_of": stamp,
    }]))
    wh.append("dim_team", pd.DataFrame([{
        "season": "2026-27", "team_code": 43, "team_id": 43,
        "name": "Manchester City", "short_name": "MCI", "as_of": stamp,
    }]))
    wh.append("fact_player_state", pd.DataFrame([{
        "season": "2026-27", "code": 223094, "element_id": 1,
        "price_tenths": 145, "selected_by_pct": 60.0, "status": "a",
        "as_of": stamp,
    }]))
    if with_profile:
        store = UnderstatStore(wh)
        store.append("understat_player_map", pd.DataFrame([{
            "code": 223094, "understat_id": 8260,
            "understat_name": "Erling Haaland",
            "understat_team": "Manchester City",
            "resolved_basis": "exact",
            "as_of": pd.Timestamp("2026-08-31 12:00", tz="UTC"),
        }]))
        store.append("understat_player_match", pd.DataFrame([{
            "understat_id": 8260, "code": 223094, "season": "2026-27",
            "match_id": 1, "date": dt.date(2026, 8, 28), "minutes": 90,
            "shots": 5, "goals": 2, "assists": 0, "key_passes": 0, "npg": 2,
            "xg": 0.69, "xa": 0.0, "npxg": 0.69, "position": "FW",
            "h_team": "Crystal Palace", "a_team": "Manchester City",
            "h_goals": 1, "a_goals": 4,
            "as_of": pd.Timestamp("2026-08-31 12:00", tz="UTC"),
        }]))
    wh.close()
    return path


def test_an_absent_profile_reports_the_gap_when_told_not_to_fetch(tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DB", str(_seed(tmp_path, with_profile=False)))
    from fpl_mcp.tools.profile_tools import player_profile

    out = player_profile("Haaland", fetch_if_missing=False)
    assert "No Understat data" in out
    assert "fetch" in out.lower()
    assert "/api/players/223094/fetch_profile" in out


def test_a_cached_profile_renders_with_asof_and_the_model_disclaimer(tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DB", str(_seed(tmp_path, with_profile=True)))
    from fpl_mcp.tools.profile_tools import player_profile

    out = player_profile("Haaland", fetch_if_missing=False)
    assert "as-of 2026-08-31" in out
    assert "not FPL points" in out
    assert "Finishing" in out and "luck" in out
    assert "Crystal Palace" in out
