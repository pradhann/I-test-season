"""No model call on an item with nothing to read.

Measured on the live warehouse, 2026-09-18, across all 799 stored analyses:

* 410 of them (51.3%) ran on items whose only text was a description: 303
  podcast, 107 YouTube. Not one of those calls could produce a claim or an
  insight, because :func:`analyze.is_scoreable` and
  :func:`analyze.insights_permitted` both refuse show notes. Each bought a
  summary of sponsor copy and a chapter list.
* 481 analyses (60.2%) ended with zero claims, and 354 of those were the
  description rows above, failing for exactly that reason.

The existing ``--min-chars`` filter does not catch this: it asks whether there
is enough prose, and a 1.2KB blurb clears 240 characters comfortably. 15 items
in the whole warehouse were caught by it. The question this gate asks is a
different one: did we store the thing itself, or only the advertisement for it.

Articles keep their body and are still analysed. The escape hatch is explicit
(``--analyse-notes``), because "analyse the show notes anyway" is a decision
somebody can take and not a default the engine should drift into.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib

from fpl_edge.ingest.content import analyse_cmd, analyze
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse
from fpl_edge.store.fetch_ledger import CallUsage

UTC = dt.UTC
NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

#: Long enough to clear --min-chars on every row, so the only thing under test
#: is the body gate and not the prose-length filter.
BODY = "genuine prose about the gameweek and who to captain in it " * 30


def _seed(db, rows) -> None:
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        for item_id, kind, text_source in rows:
            wh.sql(
                "INSERT INTO content_item (item_id, source_key, creator, kind, "
                "title, url, published_at, fetched_at, text_source, text, "
                "text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [item_id, "src", f"creator_{item_id}", kind, f"Title {item_id}",
                 f"https://example.test/{item_id}", NOW, NOW, text_source,
                 BODY, hashlib.sha256(item_id.encode()).hexdigest()])
    finally:
        wh.close()


def _args(db, **over) -> argparse.Namespace:
    base = dict(db=str(db), since=0, creator=None, retry_skipped=False,
                limit=None, min_chars=240, workers=1,
                model="claude-sonnet-5", dry_run=False, budget_s=0.0,
                token_budget=0, analyse_notes=False, summary_json=None)
    base.update(over)
    return argparse.Namespace(**base)


def _spy(monkeypatch) -> list[str]:
    """Record every item the model was asked about. Nothing reaches a
    backend: a call that got past the gate would show up here."""
    seen: list[str] = []

    def _measured(*, title, creator, text, text_source, model, client=None):
        seen.append(title)
        return analyze.MeasuredAnalysis(
            analysis=analyze.TranscriptAnalysis(
                summary=["a line"], transfers_in=[], transfers_out=[],
                captaincy=[], chip_advice=[], differentials=[], insights=[]),
            usage=CallUsage("claude-sonnet-5", 10, 5))

    monkeypatch.setattr(analyze, "analyze_transcript_measured", _measured)
    return seen


# ---------------------------------------------------------- the predicate

def test_has_body_answers_the_spend_question_not_the_scoring_one() -> None:
    assert analyze.has_body("transcript")
    assert analyze.has_body("article")
    assert not analyze.has_body("description")
    assert not analyze.has_body(None)
    assert not analyze.has_body("something nobody has written yet")


# ------------------------------------------------------------- the gate

def test_a_description_only_item_never_reaches_the_model(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed(db, [("podcast_notes", "podcast", "description"),
               ("youtube_notes", "youtube", "description")])
    seen = _spy(monkeypatch)

    assert analyse_cmd.cmd_analyze(_args(db)) == 0
    assert seen == [], "a gated item was sent to the model anyway"


def test_an_article_with_a_body_is_still_analysed(tmp_path, monkeypatch):
    """The gate is about missing text, not about which kind of item it is.
    A blog post has its body stored, so it is read."""
    db = tmp_path / "t.duckdb"
    _seed(db, [("post", "blog", "article"),
               ("episode", "podcast", "transcript"),
               ("notes", "podcast", "description")])
    seen = _spy(monkeypatch)

    assert analyse_cmd.cmd_analyze(_args(db)) == 0
    assert sorted(seen) == ["Title episode", "Title post"]


def test_a_gated_item_is_recorded_with_its_reason(tmp_path, monkeypatch):
    """Recorded, not merely skipped. Without a ledger row the item returns to
    the queue on every firing and the gate becomes a cost of its own."""
    db = tmp_path / "t.duckdb"
    _seed(db, [("notes", "podcast", "description")])
    _spy(monkeypatch)

    analyse_cmd.cmd_analyze(_args(db))

    wh = Warehouse(db, read_only=True)
    try:
        row = wh.sql("SELECT * FROM content_analysis_skip").iloc[0]
        assert row["reason"] == "no_body"
        assert row["text_source"] == "description"
        assert "no transcript and no article body" in row["detail"]
        assert wh.sql("SELECT count(*) c FROM content_analysis").iloc[0]["c"] == 0
    finally:
        wh.close()


def test_a_gated_item_is_not_re_queued_on_the_next_run(tmp_path, monkeypatch):
    db = tmp_path / "t.duckdb"
    _seed(db, [("notes", "podcast", "description")])
    _spy(monkeypatch)

    analyse_cmd.cmd_analyze(_args(db))
    summary = tmp_path / "second.json"
    analyse_cmd.cmd_analyze(_args(db, summary_json=str(summary)))

    import json

    assert json.loads(summary.read_text())["skipped_no_body"] == 0


def test_the_gate_can_be_opened_deliberately(tmp_path, monkeypatch):
    """An owner who wants show-note summaries back asks for them by name."""
    db = tmp_path / "t.duckdb"
    _seed(db, [("notes", "podcast", "description")])
    seen = _spy(monkeypatch)

    assert analyse_cmd.cmd_analyze(_args(db, analyse_notes=True)) == 0
    assert seen == ["Title notes"]


def test_the_run_summary_counts_what_the_gate_stopped(tmp_path, monkeypatch):
    """The number that answers "what did the gate save this run", so the
    saving is measured afterwards and not only argued for beforehand."""
    db = tmp_path / "t.duckdb"
    _seed(db, [("n1", "podcast", "description"),
               ("n2", "youtube", "description"),
               ("n3", "podcast", "description"),
               ("real", "podcast", "transcript")])
    seen = _spy(monkeypatch)
    summary_path = tmp_path / "summary.json"

    analyse_cmd.cmd_analyze(_args(db, summary_json=str(summary_path)))

    import json

    summary = json.loads(summary_path.read_text())
    assert summary["skipped_no_body"] == 3
    assert summary["calls"] == 1 and seen == ["Title real"]


def test_the_thin_prose_filter_still_applies_to_bodies(tmp_path, monkeypatch):
    """Two filters, two questions. An article whose stored body is a link farm
    is still refused by --min-chars, and the two reasons stay distinct in the
    skip ledger rather than merging into one."""
    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        wh.sql(
            "INSERT INTO content_item (item_id, source_key, creator, kind, "
            "title, url, published_at, fetched_at, text_source, text, "
            "text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["thin", "src", "creator", "blog", "Thin post",
             "https://example.test/thin", NOW, NOW, "article",
             "https://a.test/x https://b.test/y", hashlib.sha256(b"t").hexdigest()])
    finally:
        wh.close()
    seen = _spy(monkeypatch)

    analyse_cmd.cmd_analyze(_args(db))

    assert seen == []
    wh = Warehouse(db, read_only=True)
    try:
        assert wh.sql("SELECT reason FROM content_analysis_skip"
                      ).iloc[0]["reason"] == "too_thin"
    finally:
        wh.close()
