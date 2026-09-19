"""The envelope: a gap is not an error, an error is not a gap, budget is reported.

``docs/platform/MCP.md`` sections 5.2 to 5.4. Every tool returns the same
object, so the shape is worth pinning once here rather than once per tool.

``test_a_panel_that_raises_comes_back_as_an_error_not_a_gap`` is the load
bearing one. The web draws "no data" and "could not load" differently, from
the same distinction; if the adapter flattened them, a failed read would be
reported to a reader as an absence of data, which is a false finding rather
than a missing one.

``test_an_over_budget_run_still_carries_its_payload`` pins the deliberate
divergence from a refusal: if this server refused what the browser renders,
the two surfaces would disagree at exactly the moment the warehouse is slow.
"""

from __future__ import annotations

from typing import Any

import pytest

from fpl_edge.mcp import adapter, context
from fpl_edge.platform.registry import ParamsInvalid, ScriptRun, register_script

ANY_OBJECT: dict[str, Any] = {"type": "object", "additionalProperties": True}


@pytest.fixture()
def fake_run(monkeypatch):
    """Replace run_script at the adapter's seam, and never touch a warehouse."""
    captured: dict[str, Any] = {}

    def install(result=None, *, raises=None, duration_ms=100,
                performance="ok", notes=()):
        def run_script(name, params=None, *, db=None, ctx=None):
            captured["name"] = name
            captured["params"] = params
            captured["db"] = db
            if raises is not None:
                raise raises
            return ScriptRun(
                script=name,
                result=result if result is not None else {"rows": []},
                provenance={"script": name, "repo_sha": "abc123",
                            "generated_at": "2026-09-18T09:00:00+00:00",
                            "params": params or {}},
                duration_ms=duration_ms,
                performance=performance,
                notes=list(notes),
            )

        monkeypatch.setattr(
            "fpl_edge.platform.registry.run_script", run_script, raising=True,
        )
        return captured

    return install


def test_a_good_run_carries_the_result_and_the_provenance_unchanged(fake_run):
    fake_run({"rows": [{"a": 1}], "as_of": "2026-09-18T09:00:00+00:00"})
    out = adapter.panel_call("fixtures", "fixture_board", {"horizon": 6})
    assert out["ok"] is True
    assert out["tool"] == "fixtures"
    assert out["panel"] == "fixture_board"
    assert out["result"] == {"rows": [{"a": 1}],
                             "as_of": "2026-09-18T09:00:00+00:00"}
    assert out["provenance"]["repo_sha"] == "abc123"
    assert out["gap"] is None


def test_none_params_are_dropped_rather_than_passed_as_null(fake_run):
    captured = fake_run()
    adapter.panel_call("fixtures", "fixture_board",
                       {"horizon": 6, "from_gw": None})
    assert captured["params"] == {"horizon": 6}


def test_an_empty_panel_becomes_a_gap_with_the_instruction_attached(fake_run):
    fake_run({"empty": True, "reason": "no settled gameweeks yet."})
    out = adapter.panel_call("player_form", "player_form", {"code": 1})
    assert out["ok"] is True
    assert out["gap"]["kind"] == "empty"
    assert out["gap"]["reason"] == "no settled gameweeks yet."
    assert "Do not substitute" in out["gap"]["say"]


def test_a_result_with_named_section_gaps_becomes_a_sections_gap(fake_run):
    fake_run({"sections": [], "gaps": ["odds", "elite"]})
    out = adapter.panel_call("player", "player_dossier", {"code": 1})
    assert out["gap"]["kind"] == "sections"
    assert "odds, elite" in out["gap"]["reason"]
    assert out["result"]["gaps"] == ["odds", "elite"]


@pytest.mark.parametrize("gaps,expected", [
    (["odds", "elite"], "odds, elite"),
    ([{"key": "claims", "what": "none scored", "fix": "wait"}], "claims"),
    ([{"section": "summary", "gap": "not analysed"}], "summary"),
])
def test_the_three_shapes_a_gaps_array_takes_all_name_their_sections(
        fake_run, gaps, expected):
    """player_dossier lists keys; the two creator panels list objects."""
    fake_run({"gaps": gaps})
    out = adapter.panel_call("creator_record", "creator_report_card", {})
    assert out["gap"]["kind"] == "sections"
    assert expected in out["gap"]["reason"]


def test_a_panel_that_raises_comes_back_as_an_error_not_a_gap(fake_run):
    """THE distinction. Could not load is not the same as no data."""
    fake_run(raises=RuntimeError("the warehouse is on fire"))
    out = adapter.panel_call("fixtures", "fixture_board", {})
    assert out["ok"] is False
    assert "gap" not in out
    assert out["error"]["type"] == "RuntimeError"
    assert "on fire" in out["error"]["message"]
    assert "failure to load" in out["error"]["say"]


def test_bad_params_come_back_as_an_error_naming_the_field(fake_run):
    fake_run(raises=ParamsInvalid("fixture_board: params invalid at horizon"))
    out = adapter.panel_call("fixtures", "fixture_board", {"horizon": 99})
    assert out["ok"] is False
    assert out["error"]["type"] == "ParamsInvalid"
    assert "horizon" in out["error"]["message"]


def test_an_over_budget_run_still_carries_its_payload(fake_run):
    """A slow honest answer at the deadline beats a fast refusal."""
    fake_run({"rows": [{"a": 1}]}, duration_ms=14000,
             performance="over_budget", notes=["took 14.0s"])
    out = adapter.panel_call("fixtures", "fixture_board", {})
    assert out["ok"] is True
    assert out["result"] == {"rows": [{"a": 1}]}
    assert out["budget"]["performance"] == "over_budget"
    assert out["budget"]["notes"] == ["took 14.0s"]


def test_a_write_envelope_names_the_store_and_carries_no_panel():
    out = adapter.store_call("submit_idea", "fpl_edge.interfaces.inbox",
                             {"idea_id": "idea_1"})
    assert out["ok"] is True
    assert out["panel"] is None
    assert out["store"] == "fpl_edge.interfaces.inbox"
    assert out["result"]["idea_id"] == "idea_1"


def test_a_refusal_says_what_to_do_instead():
    out = adapter.refusal("player", "which Palmer did you mean",
                          candidates=[{"code": 1}])
    assert out["ok"] is False
    assert out["refused"] is True
    assert out["candidates"] == [{"code": 1}]
    assert "Quote this reason" in out["say"]


def test_the_adapter_runs_against_the_context_database(fake_run, monkeypatch,
                                                       tmp_path):
    captured = fake_run()
    monkeypatch.setattr(context, "db_path", lambda: tmp_path / "w.duckdb")
    adapter.panel_call("fixtures", "fixture_board", {})
    assert captured["db"] == tmp_path / "w.duckdb"


# ---------------------------------------------------------------------------
# the registry seam, exercised for real on a registered stub
# ---------------------------------------------------------------------------


def test_the_adapter_is_the_only_caller_and_one_call_is_one_run(tmp_path,
                                                                monkeypatch):
    """One tool call is one panel run, counted."""
    runs: list[str] = []

    def counting(wh, **params):
        runs.append("run")
        return {"rows": []}

    # The registry is process-global, so the probe is added to a snapshot and
    # the snapshot is put back. clear_registry() would empty it for every
    # later test in the session, because a module already imported does not
    # re-register on reload.
    from fpl_edge.platform import registry as registry_mod

    before = dict(registry_mod._SCRIPTS)
    register_script(
        "adapter_probe", counting,
        params_schema={"type": "object", "additionalProperties": False},
        result_schema={"type": "object", "additionalProperties": True,
                       "required": ["rows"],
                       "properties": {"rows": {"type": "array"}}},
    )
    try:
        from fpl_edge.store.warehouse import Warehouse

        path = tmp_path / "fpl.duckdb"
        Warehouse(path).close()
        monkeypatch.setattr(context, "db_path", lambda: path)
        out = adapter.panel_call("probe", "adapter_probe", {})
        assert out["ok"] is True
        assert runs == ["run"]
    finally:
        registry_mod._SCRIPTS.clear()
        registry_mod._SCRIPTS.update(before)


# ---------------------------------------------------------------------------
# context
# ---------------------------------------------------------------------------


def test_the_owner_context_carries_the_five_declared_fields():
    ctx = context.user_context()
    for field in ("user_id", "entry_id", "display_name", "data_root",
                  "is_owner"):
        assert hasattr(ctx, field), field
    assert isinstance(ctx.entry_id, int)


def test_the_context_is_resolved_once_per_process():
    assert context.user_context() is context.user_context()


def test_a_naive_as_of_is_rejected_with_the_reason():
    with pytest.raises(ValueError, match="timezone"):
        context.parse_as_of("2026-08-18T22:50:00")


def test_an_absent_as_of_is_none_rather_than_now():
    assert context.parse_as_of(None) is None
    assert context.parse_as_of("  ") is None


def test_an_as_of_with_a_zulu_suffix_parses_to_utc():
    parsed = context.parse_as_of("2026-08-18T22:50:00Z")
    assert parsed.tzinfo is not None
    assert parsed.isoformat() == "2026-08-18T22:50:00+00:00"


def test_the_database_path_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("FPL_EDGE_DB", str(tmp_path / "elsewhere.duckdb"))
    assert context.db_path() == tmp_path / "elsewhere.duckdb"


def test_a_missing_warehouse_is_reported_rather_than_raised(monkeypatch,
                                                            tmp_path):
    monkeypatch.setenv("FPL_EDGE_DB", str(tmp_path / "absent.duckdb"))
    reason = context.warehouse_missing()
    assert reason is not None
    assert "absent.duckdb" in reason
