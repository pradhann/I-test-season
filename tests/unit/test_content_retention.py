"""Audio retention: delete only what provenance PROVES is done.

The rule under test (PIPELINES.md §3 defect 3 / §4.4): a cached audio file
may be deleted only when its item holds transcript segments, the promoted
transcript text, AND a transcript_provenance row carrying audio_sha256 --
the hash outlives the file. The break-watch case that matters most is the
negative: a file with NO qualifying provenance row is NEVER deleted, whether
it is an orphan download, a captions-derived item (sha empty), or an item
whose transcript was never promoted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib

from fpl_edge.ingest.content import asr
from fpl_edge.store import Warehouse

UTC = dt.UTC
NOW = dt.datetime(2026, 8, 27, 12, 0, tzinfo=UTC)

URL_DONE = "https://example.test/done.mp3"
URL_CAPTIONS = "https://www.youtube.com/watch?v=abcdefghijk"
URL_UNPROMOTED = "https://example.test/unpromoted.mp3"


def _digest(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def _cache(tmp_path):
    cache = tmp_path / "asr_audio"
    cache.mkdir()
    (cache / f"{_digest(URL_DONE)}.mp3").write_bytes(b"A" * 100)
    (cache / f"{_digest(URL_UNPROMOTED)}.mp3").write_bytes(b"B" * 50)
    (cache / "deadbeefdeadbeefdeadbeef.mp3").write_bytes(b"C" * 25)  # orphan
    return cache


def _db(tmp_path):
    from fpl_edge.ingest.content.store import ContentStore

    wh = Warehouse(tmp_path / "ret.duckdb")
    ContentStore(wh)
    asr.ensure_schema(wh)

    def item(item_id, text_source):
        wh.sql(
            "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [item_id, "pod_x", "FPL Harry", "podcast", item_id,
             f"https://example.test/{item_id}", NOW, NOW, text_source, "text",
             hashlib.sha256(b"text").hexdigest()],
        )

    def segments(item_id):
        wh.sql("INSERT INTO transcript_segment VALUES (?, 0, 0.0, 'hello')",
               [item_id])

    def provenance(item_id, url, sha):
        wh.sql(
            "INSERT INTO transcript_provenance VALUES "
            "(?, 'asr', 'mlx-whisper', 'm', 'en', ?, ?, 100, 60.0, 58.0, "
            "5.0, 1, 'description', 'abc', ?)",
            [item_id, url, sha, NOW],
        )

    # 1. fully done: transcript promoted, segments stored, sha in provenance
    item("done", "transcript")
    segments("done")
    provenance("done", URL_DONE, "sha-of-done")
    # 2. captions-derived: provenance exists but audio_sha256 is empty --
    #    absence of the hash is absence of proof
    item("captioned", "transcript")
    segments("captioned")
    provenance("captioned", URL_CAPTIONS, "")
    # 3. transcript never promoted onto the item: not provably done
    item("unpromoted", "description")
    segments("unpromoted")
    provenance("unpromoted", URL_UNPROMOTED, "sha-of-unpromoted")
    wh.close()
    return tmp_path / "ret.duckdb"


def test_only_provenance_proven_audio_is_deleted(tmp_path):
    db = _db(tmp_path)
    cache = _cache(tmp_path)
    with Warehouse(db, read_only=True) as wh:
        sweep = asr.sweep_audio_cache(wh, cache_dir=cache)

    assert [p.name for p in sweep.deleted] == [f"{_digest(URL_DONE)}.mp3"]
    assert sweep.bytes_freed == 100
    assert not (cache / f"{_digest(URL_DONE)}.mp3").exists()
    # THE rule: everything without qualifying provenance survives
    assert (cache / f"{_digest(URL_UNPROMOTED)}.mp3").exists()
    assert (cache / "deadbeefdeadbeefdeadbeef.mp3").exists()
    assert sweep.kept_unmatched == 2


def test_dry_run_reports_and_deletes_nothing(tmp_path):
    db = _db(tmp_path)
    cache = _cache(tmp_path)
    with Warehouse(db, read_only=True) as wh:
        sweep = asr.sweep_audio_cache(wh, dry_run=True, cache_dir=cache)
    assert [p.name for p in sweep.deleted] == [f"{_digest(URL_DONE)}.mp3"]
    assert (cache / f"{_digest(URL_DONE)}.mp3").exists()  # still there
    assert "would delete 1" in sweep.summary()


def test_a_warehouse_without_provenance_deletes_nothing(tmp_path):
    cache = _cache(tmp_path)
    wh = Warehouse(tmp_path / "bare.duckdb")
    try:
        sweep = asr.sweep_audio_cache(wh, cache_dir=cache)
    finally:
        wh.close()
    assert sweep.deleted == ()
    assert sweep.kept_unmatched == 3
    assert "nothing is deletable" in sweep.note
    assert sorted(p.name for p in cache.glob("*")) == sorted(
        [f"{_digest(URL_DONE)}.mp3", f"{_digest(URL_UNPROMOTED)}.mp3",
         "deadbeefdeadbeefdeadbeef.mp3"])


def test_a_provenance_row_whose_file_is_gone_is_counted(tmp_path):
    db = _db(tmp_path)
    cache = tmp_path / "empty_cache"
    cache.mkdir()
    with Warehouse(db, read_only=True) as wh:
        sweep = asr.sweep_audio_cache(wh, cache_dir=cache)
    assert sweep.deleted == ()
    assert sweep.matched_missing == 1  # URL_DONE's file was never cached here


def test_the_registry_task_reports_deletions_to_the_ledger(tmp_path, monkeypatch):
    from fpl_edge.jobs import deadline_dag as dag
    from fpl_edge.pipelines import registry

    db = _db(tmp_path)
    cache = _cache(tmp_path)
    monkeypatch.setattr(asr, "AUDIO_CACHE", cache)
    ctx = dag.TaskContext(season="2026-27", gw=0, due_utc=NOW,
                          deadline_utc=None, now=NOW, db_path=db)
    res = registry.run_audio_retention(ctx)
    assert res.outcome == "quiet"
    assert res.ledger_written == 1  # deletion count rides to fetch_run
    assert "deleted 1" in res.detail


def test_the_cli_dry_run_flag(tmp_path, monkeypatch, capsys):
    from fpl_edge.ingest.content.pipeline import cmd_retention

    db = _db(tmp_path)
    cache = _cache(tmp_path)
    monkeypatch.setattr(asr, "AUDIO_CACHE", cache)
    assert cmd_retention(argparse.Namespace(db=str(db), dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "would delete 1" in out and "nothing deleted" in out
    assert (cache / f"{_digest(URL_DONE)}.mp3").exists()
