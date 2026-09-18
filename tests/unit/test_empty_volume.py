"""Every registered panel over an empty warehouse. DEPLOYMENT.md §11.2 step 5.

The first boot on a fresh Railway volume creates the file, applies the schema
and every package migration, and stops. Every table exists and every one is
empty. A panel that raised in that state used to become a 500, and the UI would
draw "could not load, retry" for a condition that is neither an error nor
retryable. That is the state this file pins.

The distinction the UI depends on is kept, not collapsed. There are two shapes
and they mean different things:

* ``{"empty": true, "reason": ...}`` at 200: the data is absent, and
  the reason says what is missing.
* ``{"error": true, "panel": ..., "reason": ...}`` at 500: this code failed.

So the last test here puts a warehouse with one row in it next to the empty one
and asserts a raising panel is still a 500. Turning every exception into an
empty payload would hide real panel bugs behind an honest-looking gap, which is
the failure mode the error shape was introduced to end.

The list of panels comes from the registry, never from a copy of it here, so a
panel registered tomorrow is covered tomorrow.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import fpl_edge.platform.scripts  # noqa: F401 - importing it is what registers
from fpl_edge.platform import boot as boot_mod
from fpl_edge.platform import registry as panel_registry
from fpl_edge.platform.app.factory import create_app
from fpl_edge.store import Warehouse

UTC = dt.UTC

#: Smallest legal value per JSON Schema type, for a required param.
_SYNTH = {"string": "x", "integer": 1, "number": 1.0, "boolean": False,
          "array": [], "object": {}}


def _synth_params(schema: dict) -> dict:
    """The smallest value each required param's own schema admits."""
    props = (schema or {}).get("properties") or {}
    out: dict = {}
    for name in (schema or {}).get("required") or []:
        spec = props.get(name) or {}
        if "default" in spec:
            out[name] = spec["default"]
        elif "enum" in spec:
            out[name] = spec["enum"][0]
        elif "pattern" in spec:
            # A synthetic value the pattern refuses is answered with a 400
            # naming the field, which is a structured refusal of the CALLER's
            # request and not a panel failure.
            out[name] = "0" * 32
        else:
            kind = spec.get("type")
            if isinstance(kind, list):
                kind = kind[0]
            out[name] = _SYNTH.get(kind, "x")
    return out


@pytest.fixture()
def empty_volume(tmp_path):
    """What boot leaves behind on a first deploy: schema, migrations, no rows."""
    data_dir = tmp_path / "data"
    report = boot_mod.boot(data_dir, seed_dir=tmp_path / "no-seed-here")
    assert report.ok
    return data_dir / "warehouse" / "fpl.duckdb"


def test_boot_leaves_a_schema_only_warehouse(empty_volume):
    assert empty_volume.exists()
    with Warehouse(empty_volume, read_only=True) as wh:
        tables = set(wh.sql("SELECT table_name FROM information_schema.tables"
                            )["table_name"])
        rows = int(wh.sql("SELECT count(*) c FROM dim_player").iloc[0]["c"])
    # The spine, and the tables the lazy per-package migrations own.
    assert {"dim_player", "dim_event", "fact_fixture", "dag_firing",
            "content_item", "transcript_provenance"} <= tables
    assert rows == 0


def test_the_warehouse_reads_as_unseeded(empty_volume):
    with Warehouse.read_copy(empty_volume) as wh:
        assert panel_registry.warehouse_is_unseeded(wh) is True


def test_one_row_anywhere_in_the_spine_means_seeded(empty_volume):
    with Warehouse(empty_volume) as wh:
        wh.append("dim_team", pd.DataFrame([
            {"season": "2026-27", "team_code": 1, "team_id": 1,
             "name": "Arsenal", "short_name": "ARS",
             "as_of": pd.Timestamp("2026-08-01", tz="UTC")},
        ]))
    with Warehouse.read_copy(empty_volume) as wh:
        assert panel_registry.warehouse_is_unseeded(wh) is False


def test_every_registered_panel_answers_over_an_empty_warehouse(empty_volume):
    """No exception, and a structured payload or a structured gap for each.

    Iterates the registry so a new panel is covered without an edit here.
    """
    names = panel_registry.registered()
    assert len(names) >= 18, "the panel set should never shrink silently"
    for name in names:
        script = panel_registry.script(name)
        params = _synth_params(script.params_schema)
        try:
            run = panel_registry.run_script(name, params, db=empty_volume)
        except panel_registry.ParamsInvalid:
            # The synthetic value did not satisfy the script's own pattern.
            # That is a refusal of this test's request, not a panel failure.
            continue
        result = run.result
        assert isinstance(result, dict), f"{name} returned {type(result)}"
        if result.get("empty") is True:
            assert result.get("reason"), f"{name} returned an unexplained empty"
        else:
            assert result, f"{name} returned an empty payload with no reason"


def test_every_panel_route_answers_without_a_500(empty_volume):
    """The same sweep through the HTTP surface the browser uses."""
    client = TestClient(create_app(empty_volume))
    scripts = client.get("/api/panels").json()["scripts"]
    assert scripts
    for script in scripts:
        name = script["name"]
        params = _synth_params(script.get("params_schema"))
        response = client.post(f"/api/scripts/{name}/run", json={"params": params})
        body = response.json()
        assert response.status_code in (200, 400), (
            f"{name} answered {response.status_code}: {body}")
        assert body.get("error") is not True, (
            f"{name} returned the broken-panel shape over an empty warehouse: "
            f"{body.get('reason')}")
        if response.status_code == 200:
            result = body["result"]
            assert result.get("empty") is True or result, (
                f"{name} returned neither a payload nor a reason")


def test_a_broken_panel_is_still_a_500_once_the_warehouse_has_data(empty_volume):
    """The distinction the UI draws, pinned from the other side.

    One row anywhere in the spine means a raising panel is a defect and must
    stay loud. Without this, the empty-volume guard would be a blanket
    exception handler that hid every panel bug behind an honest-looking gap.
    """
    with Warehouse(empty_volume) as wh:
        wh.append("dim_team", pd.DataFrame([
            {"season": "2026-27", "team_code": 1, "team_id": 1,
             "name": "Arsenal", "short_name": "ARS",
             "as_of": pd.Timestamp("2026-08-01", tz="UTC")},
        ]))

    schema = {"type": "object", "additionalProperties": False,
              "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}

    def _boom(wh):
        raise ValueError("the data edge this panel cannot survive")

    panel_registry.register_script(
        "test_empty_volume_broken", _boom,
        params_schema={"type": "object", "additionalProperties": False,
                       "properties": {}},
        result_schema=schema, description="raises on purpose")
    try:
        client = TestClient(create_app(empty_volume))
        response = client.post("/api/scripts/test_empty_volume_broken/run",
                               json={})
        assert response.status_code == 500
        body = response.json()
        assert body["error"] is True
        assert body["panel"] == "test_empty_volume_broken"
        assert body["reason"].startswith("ValueError: ")
    finally:
        panel_registry._SCRIPTS.pop("test_empty_volume_broken", None)


def test_a_raising_panel_over_an_empty_warehouse_is_a_named_gap(empty_volume):
    """The same panel, the same exception, an empty warehouse: a 200 gap whose
    reason names both the absence and where the script stopped."""
    schema = {"type": "object", "additionalProperties": False,
              "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}

    def _boom(wh):
        raise KeyError(("2026-27", 21))

    panel_registry.register_script(
        "test_empty_volume_gap", _boom,
        params_schema={"type": "object", "additionalProperties": False,
                       "properties": {}},
        result_schema=schema, description="raises on purpose")
    try:
        client = TestClient(create_app(empty_volume))
        response = client.post("/api/scripts/test_empty_volume_gap/run", json={})
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["empty"] is True
        assert "no rows" in result["reason"]
        # The exception is not swallowed, it is reported inside the gap.
        assert "KeyError" in result["reason"]
    finally:
        panel_registry._SCRIPTS.pop("test_empty_volume_gap", None)


def test_health_is_200_over_an_empty_volume(empty_volume, monkeypatch):
    """The boot report rides into the payload and the status stays 200: the
    warehouse is present, writable and migrated, it simply holds no rows."""
    client = TestClient(create_app(empty_volume))
    response = client.get("/api/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warehouse_present"] is True
    assert body["boot"]["ran"] is True
    assert all(v == "ok" for v in body["migrations"].values())
    assert body["volume"]["writable"] is True
