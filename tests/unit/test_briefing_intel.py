"""briefing_intel: the model-authored salience pass, pinned offline.

No test here ever calls a live model: the SDK call is isolated behind
``_run_model`` and every ``generate`` test injects ``run_model``. Pinned:

* input assembly — truncation to top rows, null-dropping, the as-of map,
  the character cap (rows shrink, then panels drop loudly);
* the validator — a clean item passes; invented panels, unknown codes,
  bad severities and cap overflow are rejected AND counted;
* the artefact — atomic write, round-trip, the meta-prompt hash inside it;
* failure honesty — parse failure and zero-valid-items raise and write
  nothing;
* the API route's 404-shaped empty payload and freshness fields;
* the registry row — schedule, runner wiring, ledger counts.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

from fpl_edge.pipelines import registry
from fpl_edge.platform import briefing_intel as bi
from fpl_edge.platform.app import create_app
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC

PANELS = {"squad_overview", "projection_table", "ownership_eo"}
CODES = {100, 200, 300}


def good_item(**over):
    item = {
        "headline": "Sell the flagged defender before the price drops",
        "why": "own_price_fall -1800/hr and status 'i' agree",
        "severity": 1,
        "numbers": [{"value": -1800.0, "unit": "net/hr",
                     "source_panel": "ownership_eo",
                     "as_of": "2026-08-31T09:00:00+00:00"}],
        "codes": [100],
        "drill": {"drawer": 100},
        "source_panels": ["ownership_eo", "squad_overview"],
    }
    item.update(over)
    return item


# -- input assembly ----------------------------------------------------------


def test_build_context_truncates_rows_drops_nulls_and_maps_as_of():
    results = {
        "squad_overview": {
            "as_of": "2026-08-31 09:00:00",
            "starters": [{"code": i, "xpts": None if i % 2 else 4.0}
                         for i in range(50)],
            "bank": None,
        },
        "price_radar": {"empty": True, "reason": "no snapshots"},
    }
    context, as_of, dropped = bi.build_context(results, max_rows=30)
    assert dropped == []
    assert len(context["squad_overview"]["starters"]) == 30
    # nulls are gone, both at the top level and inside rows
    assert "bank" not in context["squad_overview"]
    assert all("xpts" in r or r.keys() == {"code"}
               for r in context["squad_overview"]["starters"])
    assert "xpts" not in context["squad_overview"]["starters"][1]
    # the as-of map covers non-empty panels only, ISO-shaped
    assert as_of == {"squad_overview": "2026-08-31T09:00:00"}
    # the empty panel stays in the context as its honest self
    assert context["price_radar"]["empty"] is True


def test_build_context_shrinks_rows_then_drops_panels_under_the_char_cap():
    big = {"rows": [{"code": i, "name": "x" * 40} for i in range(30)],
           "as_of": "2026-08-31 09:00:00"}
    small = {"as_of": "2026-08-31 08:00:00", "note": "tiny"}
    context, as_of, dropped = bi.build_context(
        {"projection_table": big, "squad_overview": small}, max_chars=150)
    # the big panel could not fit even at 3 rows -> dropped, loudly
    assert dropped == ["projection_table"]
    assert set(context) == {"squad_overview"}
    assert "projection_table" not in as_of
    # and a merely-large panel shrinks instead of dropping
    context2, _, dropped2 = bi.build_context(
        {"projection_table": big}, max_chars=700)
    assert dropped2 == []
    assert 3 <= len(context2["projection_table"]["rows"]) < 30


def test_known_codes_collects_only_code_shaped_keys():
    context = {
        "squad_overview": {"starters": [{"code": 100, "price": 55}],
                           "xi_codes": [200], "gw": 3},
        "brief": {"codes": [300], "n_changes": 7, "flag": True},
    }
    assert bi.known_codes(context) == {100, 200, 300}


def test_collect_panels_degrades_a_raising_panel_to_an_honest_empty(monkeypatch):
    from fpl_edge.platform import registry as panel_registry

    def boom(wh, **kw):
        raise RuntimeError("panel exploded")

    monkeypatch.setattr(
        panel_registry, "_SCRIPTS",
        {"squad_overview": panel_registry.PanelScript(
            name="squad_overview", fn=boom,
            params_schema={"type": "object", "properties": {}},
            result_schema={"oneOf": [{"type": "object"},
                                     panel_registry.EMPTY_SCHEMA]},
            title="t", description="d")})
    out = bi.collect_panels(object(), season="2026-27",
                            panels=("squad_overview",))
    assert out["squad_overview"]["empty"] is True
    assert "panel exploded" in out["squad_overview"]["reason"]


def test_input_panels_are_all_registered():
    import fpl_edge.platform.scripts  # noqa: F401 - registers the panels
    from fpl_edge.platform.registry import registered

    assert set(bi.INPUT_PANELS) <= set(registered())


# -- the validator -----------------------------------------------------------


def test_a_good_item_is_kept():
    kept, rejected = bi.validate_items([good_item()], panels=PANELS, codes=CODES)
    assert len(kept) == 1 and rejected == 0


@pytest.mark.parametrize("bad", [
    good_item(source_panels=["made_up_panel"]),          # invented panel
    good_item(codes=[999]),                              # unknown player code
    good_item(severity=0),
    good_item(severity=4),
    good_item(severity="1"),                             # stringly typed
    good_item(headline="x" * 121),
    good_item(why="x" * 281),
    good_item(numbers=[]),                               # a claim needs a number
    good_item(numbers=[{"value": "big", "unit": "xPts",
                        "source_panel": "ownership_eo", "as_of": None}]),
    good_item(numbers=[{"value": 1.0, "unit": "xPts",
                        "source_panel": "made_up_panel", "as_of": None}]),
    good_item(drill={"drawer": 999}),                    # drill to an unshown player
    good_item(drill={"portal": "x"}),
    good_item(source_panels=[]),
    "not even an object",
])
def test_contract_violations_are_rejected_and_counted(bad):
    kept, rejected = bi.validate_items([good_item(), bad],
                                       panels=PANELS, codes=CODES)
    assert len(kept) == 1
    assert rejected == 1


def test_more_than_eight_items_are_capped_severity_sorted_and_counted():
    items = [good_item(severity=3, headline=f"watch {i}") for i in range(6)]
    items += [good_item(severity=1, headline=f"act {i}") for i in range(4)]
    kept, rejected = bi.validate_items(items, panels=PANELS, codes=CODES)
    assert len(kept) == bi.MAX_ITEMS == 8
    assert rejected == 2                       # the overflow is counted, not hidden
    assert [i["severity"] for i in kept] == [1, 1, 1, 1, 3, 3, 3, 3]


def test_tab_drill_and_null_drill_are_both_legal():
    kept, rejected = bi.validate_items(
        [good_item(drill={"tab": "fixtures"}), good_item(drill=None)],
        panels=PANELS, codes=CODES)
    assert len(kept) == 2 and rejected == 0


# -- the numeric-token rule (prose numbers checked against cited panels) -----

#: panel -> the numeric values it "served" for the token tests. good_item
#: cites ownership_eo + squad_overview and quotes -1800 in chip and prose.
VALUES = {
    "ownership_eo": [-1800.0, 55.0, 30.0, 0.1971, 100.0],
    "squad_overview": [5.415, 3.0],
    "projection_table": [6.8],
}


def test_prose_numbers_extracts_quantities_and_skips_labels():
    toks = bi.prose_numbers(
        "CHE ranks 33rd defensively in GW3; top10k cap 100% and p90 6.1, "
        "flow -2,891/hr over 36h")
    # 33rd (ordinal) kept; GW3, top10k, p90's 90, 36h all label/unit-glued
    # and skipped; 100%, 6.1 and the comma'd 2891 kept.
    assert (33.0, 0) in toks
    assert (100.0, 0) in toks
    assert (6.1, 1) in toks
    assert (2891.0, 0) in toks
    assert all(v not in (3.0, 90.0, 36.0, 10.0) for v, _ in toks)


def test_known_values_collects_numerics_per_panel():
    vals = bi.known_values({
        "a": {"x": 1.5, "rows": [{"y": 2, "s": "no", "b": True}]},
        "b": {"z": None},
    })
    assert sorted(vals["a"]) == [1.5, 2.0]
    assert vals["b"] == []


def test_a_prose_number_the_cited_panels_never_served_is_rejected():
    """R2's finding: 'ranks 33rd' when the payload says 30. The chips
    validated; the sentence did not — now it does."""
    bad = good_item(why="defence ranks 33rd, flow -1800/hr")
    ok = good_item(why="defence ranks 30th, flow -1800/hr")
    kept, rejected = bi.validate_items([ok, bad], panels=PANELS, codes=CODES,
                                       values=VALUES)
    assert len(kept) == 1 and rejected == 1
    assert "30th" in kept[0]["why"]


def test_prose_tolerates_rounding_and_percent_fraction_pairs():
    items = [
        # 5.4 is squad_overview's 5.415 printed at 1dp
        good_item(why="his 5.4 xPts beat the bench, flow -1800/hr"),
        # 19.7% is ownership_eo's 0.1971 served as a fraction
        good_item(why="clean sheet odds 19.7% say hold, flow -1800/hr"),
    ]
    kept, rejected = bi.validate_items(items, panels=PANELS, codes=CODES,
                                       values=VALUES)
    assert len(kept) == 2 and rejected == 0


def test_a_chip_value_its_named_panel_never_served_is_rejected():
    bad = good_item(numbers=[{"value": -1799.0, "unit": "net/hr",
                              "source_panel": "ownership_eo",
                              "as_of": None}],
                    why="watch the price")
    kept, rejected = bi.validate_items([bad], panels=PANELS, codes=CODES,
                                       values=VALUES)
    assert kept == [] and rejected == 1


def test_a_prose_number_from_an_uncited_panel_is_rejected():
    """6.8 exists in projection_table, but the item does not cite it —
    quoting a panel you did not name is the same invention."""
    bad = good_item(why="consensus 6.8 xPts and flow -1800/hr")
    kept, rejected = bi.validate_items([bad], panels=PANELS, codes=CODES,
                                       values=VALUES)
    assert kept == [] and rejected == 1


def test_without_values_the_numeric_rule_is_skipped():
    kept, rejected = bi.validate_items(
        [good_item(why="an unchecked 42 sails through, flow -1800/hr")],
        panels=PANELS, codes=CODES)
    assert len(kept) == 1 and rejected == 0


# -- parsing the model's answer ---------------------------------------------


def test_parse_items_accepts_a_fenced_block_and_bare_json():
    payload = {"items": [good_item()]}
    fenced = "Here you go:\n```json\n" + json.dumps(payload) + "\n```\nthanks"
    assert bi.parse_items(fenced) == payload["items"]
    assert bi.parse_items(json.dumps(payload)) == payload["items"]
    assert bi.parse_items(json.dumps(payload["items"])) == payload["items"]


def test_parse_items_raises_on_prose():
    with pytest.raises(bi.BriefingIntelError, match="parse"):
        bi.parse_items("I could not find anything salient today, sorry.")


# -- meta-prompt -------------------------------------------------------------


def test_the_meta_prompt_file_exists_and_carries_the_catalogue():
    text, digest = bi.load_meta_prompt()
    assert len(digest) == 12
    assert "4490171" in text and "top-1k" in text
    for phrase in ("xPts", "creators", "EO", "availability", "fixture",
                   "price", "severity"):
        assert phrase.lower() in text.lower()


def test_a_missing_meta_prompt_raises(tmp_path):
    with pytest.raises(bi.BriefingIntelError, match="meta-prompt"):
        bi.load_meta_prompt(tmp_path / "nope.md")


# -- generate: the pass end to end, model monkeypatched ----------------------


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


def fake_panels(monkeypatch):
    results = {
        "squad_overview": {
            "as_of": "2026-08-31 09:00:00",
            "starters": [{"code": 100, "name": "Saka", "xpts": 5.5}],
        },
        "ownership_eo": {
            "as_of": "2026-08-31 08:00:00",
            "rows": [{"code": 200, "name": "Haaland", "own_pct": 55.0}],
        },
    }
    monkeypatch.setattr(bi, "collect_panels",
                        lambda wh, season, panels=bi.INPUT_PANELS: results)
    return results


def model_answer():
    return "```json\n" + json.dumps({"items": [
        {"headline": "Haaland at 55% owned is a template gap",
         "why": "ownership_eo has him at 55.0 own% and he is not owned",
         "severity": 1,
         "numbers": [{"value": 55.0, "unit": "own%",
                      "source_panel": "ownership_eo",
                      "as_of": "2026-08-31T08:00:00"}],
         "codes": [200],
         "drill": {"drawer": 200},
         "source_panels": ["ownership_eo", "squad_overview"]},
        {"headline": "Invented player",
         "why": "cites a code the input never showed",
         "severity": 2,
         "numbers": [{"value": 9.0, "unit": "xPts",
                      "source_panel": "ownership_eo", "as_of": None}],
         "codes": [999],
         "drill": None,
         "source_panels": ["ownership_eo"]},
    ]}) + "\n```"


def test_generate_writes_the_artefact_shape_atomically(db, monkeypatch):
    fake_panels(monkeypatch)
    prompts = []

    def run_model(prompt):
        prompts.append(prompt)
        return model_answer()

    artefact = bi.generate(db, season="2026-27",
                           now=dt.datetime(2026, 8, 31, 10, 0, tzinfo=UTC),
                           run_model=run_model)
    # the prompt carried the meta-prompt and the panel JSON
    assert "top-1k" in prompts[0] and "Haaland" in prompts[0]

    path = bi.artefact_path(db)
    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()   # atomic: no torn temp
    on_disk = json.loads(path.read_text())
    assert on_disk == artefact
    assert set(artefact) >= {"generated_at", "model", "meta_prompt_hash",
                             "input_as_of", "items", "rejected_n", "duration_s"}
    assert artefact["model"] == bi.MODEL
    assert artefact["meta_prompt_hash"] == bi.load_meta_prompt()[1]
    assert artefact["input_as_of"] == {
        "squad_overview": "2026-08-31T09:00:00",
        "ownership_eo": "2026-08-31T08:00:00",
    }
    # one item survived; the invented-code item was dropped LOUDLY
    assert len(artefact["items"]) == 1
    assert artefact["rejected_n"] == 1
    assert artefact["items"][0]["codes"] == [200]


def test_generate_parse_failure_raises_and_writes_nothing(db, monkeypatch):
    fake_panels(monkeypatch)
    with pytest.raises(bi.BriefingIntelError, match="parse"):
        bi.generate(db, season="2026-27", run_model=lambda p: "no json here")
    assert not bi.artefact_path(db).exists()


def test_generate_zero_valid_items_raises_and_writes_nothing(db, monkeypatch):
    fake_panels(monkeypatch)
    answer = json.dumps({"items": [{"headline": "junk", "severity": 9}]})
    with pytest.raises(bi.BriefingIntelError, match="zero valid items"):
        bi.generate(db, season="2026-27", run_model=lambda p: answer)
    assert not bi.artefact_path(db).exists()


def test_generate_refuses_an_all_empty_panel_set(db, monkeypatch):
    monkeypatch.setattr(
        bi, "collect_panels",
        lambda wh, season, panels=bi.INPUT_PANELS: {
            "squad_overview": {"empty": True, "reason": "no squad"}})
    with pytest.raises(bi.BriefingIntelError, match="nothing to synthesise"):
        bi.generate(db, season="2026-27",
                    run_model=lambda p: pytest.fail("must not call the model"))


def test_generate_never_reaches_the_real_sdk_seam(db, monkeypatch):
    """The isolation contract: _run_model is the ONLY model seam, and
    injecting run_model bypasses it entirely."""
    fake_panels(monkeypatch)

    def forbidden(prompt, **kw):
        raise AssertionError("_run_model must not be called when injected")

    monkeypatch.setattr(bi, "_run_model", forbidden)
    artefact = bi.generate(db, season="2026-27",
                           run_model=lambda p: model_answer())
    assert artefact["items"]


# -- the API route -----------------------------------------------------------


def test_api_briefing_empty_shape_names_the_task(db):
    client = TestClient(create_app(db))
    body = client.get("/api/briefing").json()
    assert body["empty"] is True
    assert body["task"] == "briefing_intel"
    assert "briefing_intel" in body["reason"] or "artefact" in body["reason"]


def test_api_briefing_serves_the_artefact_with_freshness(db, monkeypatch):
    fake_panels(monkeypatch)
    bi.generate(db, season="2026-27",
                now=dt.datetime.now(UTC) - dt.timedelta(hours=3),
                run_model=lambda p: model_answer())
    client = TestClient(create_app(db))
    body = client.get("/api/briefing").json()
    assert body.get("empty") is None
    assert body["model"] == bi.MODEL
    assert len(body["items"]) == 1
    assert body["age_hours"] == pytest.approx(3.0, abs=0.1)
    assert body["inputs_moved"] is False


def test_inputs_moved_flags_an_as_of_past_the_window(db):
    generated = dt.datetime(2026, 8, 31, 10, 0, tzinfo=UTC)
    bi.write_artefact(bi.artefact_path(db), {
        "generated_at": generated.isoformat(),
        "model": bi.MODEL, "meta_prompt_hash": "abc123abc123",
        "input_as_of": {
            "squad_overview": (generated + dt.timedelta(hours=7)).isoformat()},
        "items": [], "rejected_n": 0, "duration_s": 1.0,
    })
    body = bi.briefing_response(db, now=generated + dt.timedelta(hours=8))
    assert body["inputs_moved"] is True
    # inside the 6h window it stays quiet
    bi.write_artefact(bi.artefact_path(db), {
        "generated_at": generated.isoformat(),
        "model": bi.MODEL, "meta_prompt_hash": "abc123abc123",
        "input_as_of": {
            "squad_overview": (generated + dt.timedelta(hours=5)).isoformat()},
        "items": [], "rejected_n": 0, "duration_s": 1.0,
    })
    assert bi.briefing_response(db)["inputs_moved"] is False


def test_an_unreadable_artefact_is_an_honest_empty(db):
    bi.artefact_path(db).write_text("{not json")
    body = bi.briefing_response(db)
    assert body["empty"] is True and body["task"] == "briefing_intel"


# -- the registry row --------------------------------------------------------


def test_briefing_intel_is_registered_daily_0740_local():
    task = registry.by_id("briefing_intel")
    assert task is not None
    assert isinstance(task.due, registry.Calendar)
    assert (task.due.hour_local, task.due.minute, task.due.tz) == (
        7, 40, "Europe/London")
    assert not task.scheduled_by_dag
    assert task.enabled
    assert "never merged into dashboard_brief" in task.description
    assert task.run is registry.run_briefing_intel


def test_run_briefing_intel_honours_the_kill_switch(tmp_path, monkeypatch):
    """The model call leaves the machine; a gated tick must never spawn it."""
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "1")
    monkeypatch.setattr(bi, "generate",
                        lambda *a, **k: pytest.fail("gated task ran the pass"))
    now = dt.datetime(2026, 8, 31, 7, 40, tzinfo=UTC)
    ctx = registry.TaskContext(season="2026-27", gw=registry.NO_GW,
                               due_utc=now, deadline_utc=None, now=now,
                               db_path=tmp_path / "fpl.duckdb")
    res = registry.run_briefing_intel(ctx)
    assert res.outcome == "no_source"
    assert "FPL_EDGE_DISABLE_NETWORK_INGEST" in res.detail


def test_run_briefing_intel_reports_kept_items_to_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    calls = {}

    def fake_generate(db_path, *, season, now=None):
        calls["args"] = (db_path, season)
        return {"items": [1, 2, 3], "rejected_n": 2,
                "meta_prompt_hash": "abc", "duration_s": 4.2}

    monkeypatch.setattr(bi, "generate", fake_generate)
    now = dt.datetime(2026, 8, 31, 7, 40, tzinfo=UTC)
    ctx = registry.TaskContext(season="2026-27", gw=registry.NO_GW,
                               due_utc=now, deadline_utc=None, now=now,
                               db_path=tmp_path / "fpl.duckdb")
    res = registry.run_briefing_intel(ctx)
    assert res.outcome == "quiet"
    assert res.ledger_written == 3           # rows_written = kept items
    assert "3 item(s) kept" in res.detail and "2 rejected" in res.detail
    assert calls["args"] == (tmp_path / "fpl.duckdb", "2026-27")


def test_a_generate_failure_propagates_for_the_error_ledger_row(tmp_path, monkeypatch):
    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")

    def fail(db_path, *, season, now=None):
        raise bi.BriefingIntelError("zero valid items survived validation")

    monkeypatch.setattr(bi, "generate", fail)
    now = dt.datetime(2026, 8, 31, 7, 40, tzinfo=UTC)
    ctx = registry.TaskContext(season="2026-27", gw=registry.NO_GW,
                               due_utc=now, deadline_utc=None, now=now,
                               db_path=tmp_path / "fpl.duckdb")
    with pytest.raises(bi.BriefingIntelError):
        registry.run_briefing_intel(ctx)


def test_the_ui_trigger_route_knows_the_task(db):
    client = TestClient(create_app(db))
    state = client.get("/api/pipelines/briefing_intel/run_state")
    assert state.status_code == 200
    assert state.json()["task_id"] == "briefing_intel"
