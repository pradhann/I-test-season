"""What a run spent, recorded where a reader can find it.

Three separate claims, all of them about measurement rather than intent:

* ``content_analysis`` carries the model the backend SAID it ran and the
  tokens it SAID the call cost, in columns a migration adds and backfills to
  NULL, beside the ``model`` column that has always held the model the code
  asked for.
* ``fetch_run.note`` carries the same three facts for a whole run, as one line
  of JSON inside the note column that already existed. The ledger grows no
  columns, and the note stays readable prose either side of that line.
* a token budget stops a batch. The wall clock already bounded how long an
  analyse firing runs; the same thirty minutes buys a handful of two-hour
  transcripts or a hundred pages of show notes, which differ by more than an
  order of magnitude in tokens, so the ceiling belongs in the unit the spend
  is measured in.

The distinction the whole file turns on: None is unknown and 0 is zero. A run
against a backend that reports nothing must not read as a run that cost
nothing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pandas as pd
import pytest

from fpl_edge.ingest.content import analyze
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
from fpl_edge.store.fetch_ledger import CallUsage, parse_spend, spend_note

UTC = dt.UTC
NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _analysis() -> analyze.TranscriptAnalysis:
    return analyze.TranscriptAnalysis(
        summary=["one line"], transfers_in=[], transfers_out=[],
        captaincy=[], chip_advice=[], differentials=[], insights=[])


# ------------------------------------------------------- the CallUsage rules

def test_an_unreported_call_is_unknown_and_not_zero() -> None:
    usage = CallUsage()
    assert usage.tokens_in is None and usage.tokens_out is None
    assert usage.model_reported is None
    # Only the budget helper collapses unknown to zero, and only because a
    # budget has to keep counting against a backend that says nothing.
    assert usage.total == 0


def test_summing_keeps_a_known_number_known_and_an_unknown_unknown() -> None:
    known = CallUsage("claude-sonnet-5", 10, 2)
    assert (known + CallUsage()).tokens_in == 10
    assert (CallUsage() + CallUsage()).tokens_in is None
    total = known + CallUsage("claude-sonnet-5", 5, 1)
    assert (total.tokens_in, total.tokens_out) == (15, 3)


def test_the_cli_envelope_sums_every_class_of_input_token() -> None:
    """Fresh, cache-creation and cache-read are all tokens the model read.
    Counting only the fresh ones under-reports a cached call by an order of
    magnitude: measured on a two-word prompt, 2 fresh against 31,199 cached."""
    usage = analyze.usage_from_cli({
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 12668,
                  "cache_read_input_tokens": 18531, "output_tokens": 4},
        "modelUsage": {"claude-sonnet-5": {"outputTokens": 4,
                                           "canonicalModel": "claude-sonnet-5"}},
    })
    assert usage.tokens_in == 31201 and usage.tokens_out == 4
    assert usage.model_reported == "claude-sonnet-5"


def test_an_envelope_with_no_usage_reports_unknown() -> None:
    assert analyze.usage_from_cli({}) == CallUsage()


def test_a_backend_that_ran_a_different_model_is_recorded_as_such() -> None:
    """The point of measuring. A CLI that ignores the flag has to be visible
    in the row, not smoothed into agreement with the request."""
    usage = analyze.usage_from_cli({
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "modelUsage": {"claude-opus-5": {"outputTokens": 1,
                                         "canonicalModel": "claude-opus-5"}},
    })
    assert usage.model_reported == "claude-opus-5"
    assert usage.model_reported != analyze.MODEL


# ------------------------------------------------------------ the migration

def test_the_migration_adds_three_nullable_columns_and_backfills_null(tmp_path):
    """An existing row cannot be given a measurement nobody took, so it gets
    NULL. Applying the migration to a table that already holds rows is the
    case that matters: the live warehouse holds 799 of them."""
    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        store = ContentStore(wh)
        store.migrate()
        # A row as the pre-migration code wrote one: four columns, positional.
        wh.sql("INSERT INTO content_analysis (item_id, model, created_utc, "
               "analysis_json) VALUES (?, ?, ?, ?)",
               ["old", "claude-opus-5", NOW, json.dumps({"summary": []})])

        cols = set(wh.sql(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'content_analysis'")["column_name"])
        assert {"model_reported", "tokens_in", "tokens_out"} <= cols

        row = wh.sql("SELECT * FROM content_analysis WHERE item_id = 'old'")
        assert pd.isna(row.iloc[0]["model_reported"])
        assert pd.isna(row.iloc[0]["tokens_in"])
        assert pd.isna(row.iloc[0]["tokens_out"])
        # The requested model is untouched: it is half the primary key and
        # re-analysis with a newer model still keys differently.
        assert row.iloc[0]["model"] == "claude-opus-5"
    finally:
        wh.close()


def test_a_measured_write_records_the_reported_model_beside_the_asked_one(
        tmp_path):
    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        analyze.store_analysis(
            wh, "item1", _analysis(), model="claude-sonnet-5",
            text_source="transcript",
            usage=CallUsage("claude-opus-5", 31201, 900))
        row = wh.sql("SELECT * FROM content_analysis").iloc[0]
        assert row["model"] == "claude-sonnet-5"
        assert row["model_reported"] == "claude-opus-5"
        assert row["tokens_in"] == 31201 and row["tokens_out"] == 900
    finally:
        wh.close()


def test_a_write_with_no_usage_lands_null_rather_than_zero(tmp_path):
    """The single-link path in interfaces.creators has no usage to pass. Its
    rows say so rather than claiming a free call."""
    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        analyze.store_analysis(wh, "item1", _analysis(),
                               model="claude-sonnet-5", text_source="article")
        row = wh.sql("SELECT * FROM content_analysis").iloc[0]
        assert pd.isna(row["tokens_in"]) and pd.isna(row["model_reported"])
    finally:
        wh.close()


# ------------------------------------------------------- the note JSON shape

def test_the_note_carries_the_three_facts_as_parseable_json() -> None:
    note = spend_note(CallUsage("claude-sonnet-5", 120, 30),
                      calls=4, budget_stopped=False)
    payload = parse_spend(f"quiet: full backlog; +4 analyses\n{note}")
    assert payload == {"model": "claude-sonnet-5", "tokens_in": 120,
                       "tokens_out": 30, "calls": 4, "budget_stopped": False}


def test_the_note_stays_parseable_with_a_log_tail_after_it() -> None:
    """An error run's note appends a log tail. The spend line has to survive
    being in the middle of a note, which is why it is one line with a prefix
    rather than the whole note being JSON."""
    note = spend_note(CallUsage("claude-sonnet-5", 1, 2), calls=1)
    full = (f"error: it fell over\n{note}\n--- log tail ---\n"
            "step content_analyse: FAILED\nTraceback (most recent call last)")
    assert parse_spend(full)["tokens_in"] == 1


def test_a_note_with_no_spend_line_parses_as_no_spend() -> None:
    assert parse_spend("quiet: nothing to do") is None
    assert parse_spend(None) is None


def test_an_unreported_run_says_null_rather_than_zero() -> None:
    payload = parse_spend(spend_note(CallUsage(), calls=0))
    assert payload["tokens_in"] is None and payload["model"] is None


# ------------------------------------------------------------- the budget

def test_the_backlog_task_carries_a_token_budget() -> None:
    from fpl_edge.pipelines import registry

    backlog = registry.by_id("content_analyse_backlog")
    assert backlog.token_budget == registry.ANALYSE_TOKEN_BUDGET
    assert backlog.token_budget > 0


def test_a_task_that_spends_nothing_carries_no_token_budget() -> None:
    """The field is None by default, and None means "this task does not spend",
    not "this task is unlimited"."""
    from fpl_edge.pipelines import registry

    assert registry.by_id("content_transcribe").token_budget is None
    assert registry.by_id("audio_retention").token_budget is None


def test_the_batch_stops_when_reported_tokens_cross_the_budget(tmp_path,
                                                               monkeypatch):
    """The whole point, end to end through ``cmd_analyze``: four queued items,
    a budget that two of them exhaust, and a run that leaves the rest queued
    rather than spending through the ceiling."""
    import argparse

    from fpl_edge.ingest.content import analyse_cmd

    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        for n in range(4):
            wh.sql(
                "INSERT INTO content_item (item_id, source_key, creator, kind, "
                "title, url, published_at, fetched_at, text_source, text, "
                "text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [f"i{n}", "src", f"creator{n}", "podcast", f"Episode {n}",
                 f"https://example.test/{n}", NOW, NOW, "transcript",
                 "a transcript with plenty of prose in it " * 20,
                 hashlib.sha256(f"i{n}".encode()).hexdigest()])
    finally:
        wh.close()

    calls: list[str] = []

    def _measured(*, title, creator, text, text_source, model, client=None):
        calls.append(title)
        return analyze.MeasuredAnalysis(
            analysis=_analysis(),
            usage=CallUsage("claude-sonnet-5", 400, 100))

    monkeypatch.setattr(analyze, "analyze_transcript_measured", _measured)

    summary_path = tmp_path / "summary.json"
    args = argparse.Namespace(
        db=str(db), since=0, creator=None, retry_skipped=False, limit=None,
        min_chars=10, workers=1, model="claude-sonnet-5", dry_run=False,
        budget_s=0.0, token_budget=1000, analyse_notes=False,
        summary_json=str(summary_path))
    assert analyse_cmd.cmd_analyze(args) == 0

    # 400 + 100 per call, so the second call reaches 1000 and the third is
    # never started.
    assert len(calls) == 2
    summary = json.loads(summary_path.read_text())
    assert summary["budget_stopped"] is True
    assert summary["tokens_in"] == 800 and summary["tokens_out"] == 200
    assert summary["model_reported"] == "claude-sonnet-5"

    wh = Warehouse(db, read_only=True)
    try:
        stored = wh.sql("SELECT * FROM content_analysis")
        assert len(stored) == 2, "the unspent items are still queued"
        assert set(stored["tokens_in"]) == {400}
    finally:
        wh.close()


def test_no_budget_means_the_whole_queue_runs(tmp_path, monkeypatch):
    """A zero budget is "no ceiling", the shape every existing caller relies
    on; it must not read as "spend nothing"."""
    import argparse

    from fpl_edge.ingest.content import analyse_cmd

    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        for n in range(3):
            wh.sql(
                "INSERT INTO content_item (item_id, source_key, creator, kind, "
                "title, url, published_at, fetched_at, text_source, text, "
                "text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [f"i{n}", "src", f"creator{n}", "blog", f"Post {n}",
                 f"https://example.test/{n}", NOW, NOW, "article",
                 "an article body with plenty of prose in it " * 20,
                 hashlib.sha256(f"i{n}".encode()).hexdigest()])
    finally:
        wh.close()

    calls: list[str] = []

    def _measured(*, title, creator, text, text_source, model, client=None):
        calls.append(title)
        return analyze.MeasuredAnalysis(analysis=_analysis(),
                                        usage=CallUsage("claude-sonnet-5", 9, 9))

    monkeypatch.setattr(analyze, "analyze_transcript_measured", _measured)
    args = argparse.Namespace(
        db=str(db), since=0, creator=None, retry_skipped=False, limit=None,
        min_chars=10, workers=1, model="claude-sonnet-5", dry_run=False,
        budget_s=0.0, token_budget=0, analyse_notes=False, summary_json=None)
    assert analyse_cmd.cmd_analyze(args) == 0
    assert len(calls) == 3


@pytest.mark.parametrize("task_id",
                         ["content_analyse", "content_analyse_backlog"])
def test_the_analyse_tasks_pass_their_budget_to_the_subprocess(
        task_id, tmp_path, monkeypatch):
    """The Task field is metadata until something reads it. This asserts the
    argv the task actually builds."""
    from fpl_edge.pipelines import contracts, registry

    captured: list[list[str]] = []

    def _run_step(name, argv, *, timeout=0.0):
        captured.append(list(argv))
        return contracts.Step(name=name, ok=True, seconds=0.1, detail="done")

    monkeypatch.setattr(registry, "run_step", _run_step)
    monkeypatch.setattr(registry, "_network_disabled", lambda: False)
    monkeypatch.setattr(registry, "_content_analysis_rows", lambda ctx: 0)

    ctx = contracts.TaskContext(
        season="2026-27", gw=registry.NO_GW, due_utc=NOW, deadline_utc=None,
        now=NOW, db_path=tmp_path / "t.duckdb")
    result = registry.runner_for(task_id)(ctx)

    argv = captured[0]
    assert "--token-budget" in argv
    budget = int(argv[argv.index("--token-budget") + 1])
    assert budget == registry.ANALYSE_TOKEN_BUDGET
    # And the note the ledger will carry is parseable even when the
    # subprocess wrote no summary file.
    assert parse_spend(result.detail) is not None


# --------------------------------------------------------------- the trigger

def test_a_ledger_record_has_no_default_trigger() -> None:
    """The fix for PIPELINES_AUDIT.md's finding. The default was "scheduler",
    so every subprocess-written row claimed a scheduler that, on 2026-09-09,
    was not even loaded."""
    from fpl_edge.store import fetch_ledger

    with pytest.raises(TypeError):
        fetch_ledger.RunRecord("p")
    with pytest.raises(ValueError, match="not in"):
        fetch_ledger.RunRecord("p", trigger="cron")


def test_a_subprocess_reads_its_trigger_from_what_spawned_it(monkeypatch):
    from fpl_edge.store import fetch_ledger

    monkeypatch.delenv(fetch_ledger.TRIGGER_ENV, raising=False)
    # Nothing spawned this, so somebody typed it.
    assert fetch_ledger.trigger_from_env() == "cli"

    monkeypatch.setenv(fetch_ledger.TRIGGER_ENV, "ui")
    assert fetch_ledger.trigger_from_env() == "ui"

    # A value nothing recognises is not silently trusted.
    monkeypatch.setenv(fetch_ledger.TRIGGER_ENV, "nonsense")
    assert fetch_ledger.trigger_from_env() == "cli"


def test_the_runner_exports_the_trigger_to_the_task_it_runs(tmp_path):
    """The UI's script-run path: a task triggered from the panel leaves
    ``trigger='ui'`` on the rows its subprocesses write, not 'scheduler'."""
    import os

    from fpl_edge.pipelines import contracts, runner
    from fpl_edge.store import fetch_ledger

    seen: list[str] = []

    def _task(ctx):
        seen.append(os.environ.get(fetch_ledger.TRIGGER_ENV, "unset"))
        return contracts.TaskResult(outcome="quiet", detail="ok")

    ctx = contracts.TaskContext(
        season="2026-27", gw=0, due_utc=NOW, deadline_utc=None, now=NOW,
        db_path=tmp_path / "t.duckdb")
    outcome = runner.execute("stub", ctx, fn=_task, trigger="ui",
                             log_dir=tmp_path)

    assert seen == ["ui"]
    assert outcome.record.trigger == "ui"
    # And the environment is left as it was found.
    assert fetch_ledger.TRIGGER_ENV not in os.environ
