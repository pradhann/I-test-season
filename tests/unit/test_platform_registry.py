"""The panel-script contract: validated both ways, stamped with provenance.

Result validation is the half worth defending in a test. Param validation only
protects the script from the caller; result validation protects every consumer
from the script, and it is the reason a panel can render a payload without
defensive checks on every field.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.platform import registry
from fpl_edge.platform.registry import (
    BUDGET_S,
    ParamsInvalid,
    ResultInvalid,
    register_script,
    run_script,
)
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    wh.append("dim_team", pd.DataFrame([{
        "season": "2026-27", "team_code": 1, "team_id": 1, "name": "Arsenal",
        "short_name": "ARS", "as_of": pd.Timestamp("2026-08-01", tz="UTC"),
    }]))
    wh.close()
    return path


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    """Each test gets its own registry; the real scripts are restored after."""
    saved = dict(registry._SCRIPTS)
    yield
    registry._SCRIPTS.clear()
    registry._SCRIPTS.update(saved)


SIMPLE_PARAMS = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "n": {"type": "integer", "minimum": 1, "maximum": 10},
        "label": {"type": "string", "default": "hello"},
    },
    "required": ["n"],
}
SIMPLE_RESULT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "label"],
    "properties": {"n": {"type": "integer"}, "label": {"type": "string"}},
}


def _echo(wh, *, n, label="hello"):
    """Echoes its params back."""
    return {"n": n, "label": label}


def test_params_below_the_minimum_are_refused(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    with pytest.raises(ParamsInvalid, match="minimum"):
        run_script("echo", {"n": 0}, db=db)


def test_params_above_the_maximum_are_refused(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    with pytest.raises(ParamsInvalid, match="maximum"):
        run_script("echo", {"n": 99}, db=db)


def test_a_missing_required_param_is_refused(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    with pytest.raises(ParamsInvalid):
        run_script("echo", {}, db=db)


def test_an_unknown_param_is_refused(db):
    """additionalProperties:false means a typo'd param is an error, not a
    silently ignored one that leaves the panel showing defaults."""
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    with pytest.raises(ParamsInvalid):
        run_script("echo", {"n": 1, "limitt": 5}, db=db)


def test_a_wrong_type_is_refused(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    with pytest.raises(ParamsInvalid):
        run_script("echo", {"n": "three"}, db=db)


def test_declared_defaults_are_filled_in(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    run = run_script("echo", {"n": 2}, db=db)
    assert run.result["label"] == "hello"
    assert run.provenance["params"]["label"] == "hello"


def test_a_result_of_the_wrong_shape_fails_the_run(db):
    """The script's own contract is enforced against the script."""
    register_script(
        "liar", lambda wh, **_: {"n": "not an integer", "label": "x"},
        params_schema={"type": "object", "properties": {}},
        result_schema=SIMPLE_RESULT,
    )
    with pytest.raises(ResultInvalid, match="result_schema"):
        run_script("liar", {}, db=db)


def test_a_result_with_an_extra_key_fails_the_run(db):
    register_script(
        "chatty", lambda wh, **_: {"n": 1, "label": "x", "surprise": True},
        params_schema={"type": "object", "properties": {}},
        result_schema=SIMPLE_RESULT,
    )
    with pytest.raises(ResultInvalid):
        run_script("chatty", {}, db=db)


def test_a_non_object_result_fails_the_run(db):
    register_script(
        "listy", lambda wh, **_: [1, 2, 3],
        params_schema={"type": "object", "properties": {}},
        result_schema=SIMPLE_RESULT,
    )
    with pytest.raises(ResultInvalid, match="not an object"):
        run_script("listy", {}, db=db)


def test_the_honest_empty_shape_is_always_valid(db):
    """Every script may return {empty, reason} without declaring it, which is
    what makes 'say nothing' cheaper than 'invent a row'."""
    register_script(
        "nothing", lambda wh, **_: {"empty": True, "reason": "no data yet"},
        params_schema={"type": "object", "properties": {}},
        result_schema=SIMPLE_RESULT,
    )
    run = run_script("nothing", {}, db=db)
    assert run.result == {"empty": True, "reason": "no data yet"}


def test_an_empty_without_a_reason_is_refused(db):
    """'empty: true' with no explanation is the thing being prevented."""
    register_script(
        "mute", lambda wh, **_: {"empty": True},
        params_schema={"type": "object", "properties": {}},
        result_schema=SIMPLE_RESULT,
    )
    with pytest.raises(ResultInvalid):
        run_script("mute", {}, db=db)


def test_provenance_carries_script_sha_and_timestamp(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    run = run_script("echo", {"n": 1}, db=db)
    p = run.provenance
    assert p["script"] == "echo"
    assert set(p) >= {"script", "repo_sha", "generated_at", "params"}
    assert p["repo_sha"] and isinstance(p["repo_sha"], str)
    # Parses as an instant, and is timezone-aware.
    stamp = dt.datetime.fromisoformat(p["generated_at"])
    assert stamp.tzinfo is not None


def test_provenance_includes_as_of_when_the_result_has_one(db):
    register_script(
        "timed", lambda wh, **_: {"n": 1, "label": "x", "as_of": "2026-08-01T00:00:00+00:00"},
        params_schema={"type": "object", "properties": {}},
        result_schema={
            "type": "object",
            "required": ["n", "label"],
            "properties": {"n": {"type": "integer"}, "label": {"type": "string"},
                           "as_of": {"type": "string"}},
        },
    )
    run = run_script("timed", {}, db=db)
    assert run.provenance["as_of"] == "2026-08-01T00:00:00+00:00"


def test_over_budget_marks_performance_but_still_returns(db, monkeypatch):
    """Argus fails an over-budget draft run; we return it and say so, because
    a slow honest answer at the deadline beats a fast error."""
    # A fake clock that jumps past the budget on its second read. It must not
    # be a fixed-length iterator: `registry.time` IS the stdlib module, so
    # Warehouse._connect's own monotonic() calls come through here too.
    calls = {"n": 0}

    def fake_monotonic():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else BUDGET_S + 5.0

    monkeypatch.setattr(registry.time, "monotonic", fake_monotonic)
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    run = run_script("echo", {"n": 1}, db=db)
    assert run.performance == "over_budget"
    assert run.result["n"] == 1
    assert any("budget" in n for n in run.notes)


def test_a_fast_run_is_marked_ok(db):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    run = run_script("echo", {"n": 1}, db=db)
    assert run.performance == "ok" and run.notes == []


def test_the_script_receives_a_read_only_warehouse(db):
    """A script must not be able to write, even by accident."""
    seen = {}

    def peek(wh, **_):
        seen["read_only"] = wh.sql(
            "SELECT current_setting('access_mode') AS m").iloc[0]["m"]
        return {"n": 1, "label": "x"}

    register_script("peek", peek, params_schema={"type": "object", "properties": {}},
                    result_schema=SIMPLE_RESULT)
    run_script("peek", {}, db=db)
    assert seen["read_only"].upper() == "READ_ONLY"


def test_the_read_copy_is_not_the_live_file(db):
    """Scripts read a copy, so the write lock stays free for the ingest jobs."""
    seen = {}

    def peek(wh, **_):
        seen["path"] = str(wh.path)
        seen["source"] = str(getattr(wh, "source_path", ""))
        return {"n": 1, "label": "x"}

    register_script("peek", peek, params_schema={"type": "object", "properties": {}},
                    result_schema=SIMPLE_RESULT)
    run_script("peek", {}, db=db)
    assert seen["path"] != str(db)
    assert seen["source"] == str(db)


def test_a_missing_warehouse_returns_an_honest_empty(tmp_path):
    register_script("echo", _echo, params_schema=SIMPLE_PARAMS,
                    result_schema=SIMPLE_RESULT)
    run = run_script("echo", {"n": 1}, db=tmp_path / "absent.duckdb")
    assert run.result["empty"] is True
    assert "absent.duckdb" in run.result["reason"]


def test_an_unknown_script_raises_keyerror(db):
    with pytest.raises(KeyError):
        run_script("no_such_script", {}, db=db)


def test_a_schema_that_is_not_object_rooted_is_refused():
    with pytest.raises(ValueError, match="object-rooted"):
        register_script("bad", _echo, params_schema={"type": "array"},
                        result_schema=SIMPLE_RESULT)


def test_an_invalid_json_schema_is_refused_at_registration():
    import jsonschema

    with pytest.raises(jsonschema.exceptions.SchemaError):
        register_script("bad", _echo,
                        params_schema={"type": "object", "properties": {"n": {"type": "nope"}}},
                        result_schema=SIMPLE_RESULT)
