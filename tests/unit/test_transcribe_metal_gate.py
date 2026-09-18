"""The ASR backend gate on a host with no Metal GPU. DEPLOYMENT.md §1.3.

``cmd_transcribe`` used to run ``asr.backend_status()`` at the top of the
command and return 1 when the engine was missing, before it had read the queue.
On Linux that refused a caption-only run that needs no engine at all, which
breaks ``content_fast_rss``: its second step is
``transcribe --kinds youtube --since 2 --budget-s 300``, so that task would exit
non-zero every four hours on Railway and go to ``error`` forever.

The gate now sits where an item actually needs the GPU. Two properties follow
and both are pinned here:

* A caption-only run succeeds on a host with no engine.
* An audio item on such a host is left UNTOUCHED and UNRECORDED. Writing a
  ``content_transcribe_skip`` row would remove it from the queue that
  ``GET /api/transcripts/queue`` serves to the Mac worker, permanently, which
  would mean the move to a Mac worker silently dropped the audio half of the
  corpus.

The platform is monkeypatched rather than detected: ``mlx_whisper`` is a darwin
dependency, so on the machine this suite usually runs on it IS installed, and a
test that only ran where it is absent would never run at all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib

import pytest

from fpl_edge.ingest.content import asr
from fpl_edge.ingest.content import transcribe_cmd
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse

UTC = dt.UTC


def _linux_backend(monkeypatch) -> None:
    """What ``backend_status`` reports inside the container.

    ``pyproject.toml`` guards mlx-whisper with ``sys_platform == 'darwin'``, so
    on Linux the import fails and the engine is missing. ``av`` installs fine
    there, so the decoder is present and the engine alone is what is absent.
    """
    monkeypatch.setattr(
        asr, "backend_status",
        lambda: asr.BackendStatus(
            mlx_whisper=False, decoder="pyav",
            mlx_whisper_error="ModuleNotFoundError: No module named 'mlx_whisper'"))


def _mac_backend(monkeypatch) -> None:
    monkeypatch.setattr(
        asr, "backend_status",
        lambda: asr.BackendStatus(mlx_whisper=True, decoder="pyav"))


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "gate.duckdb"
    published = dt.datetime.now(UTC) - dt.timedelta(days=1)
    with Warehouse(path) as wh:
        ContentStore(wh)
        asr.ensure_schema(wh)
        for item_id, kind, creator in (("pod_item", "podcast", "FPL Harry"),
                                       ("vid_item", "youtube", "FPL Harry")):
            text = "Captain picks and transfer targets for the gameweek."
            wh.sql(
                "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [item_id, "pod_x", creator, kind, "GW3 wildcard drafts",
                 f"https://example.test/{item_id}", published, published,
                 "description", text,
                 hashlib.sha256(text.encode()).hexdigest()],
            )
    return path


def _args(db, **over) -> argparse.Namespace:
    base = {"db": str(db), "delay": 1.0, "limit": None, "since": 0,
            "budget_s": 0.0, "kinds": "podcast", "creator": None,
            "any_creator": True, "model": None, "dry_run": False,
            "min_relevance": 0.0}
    base.update(over)
    return argparse.Namespace(**base)


def test_kind_needs_asr_names_only_the_audio_route():
    assert transcribe_cmd.kind_needs_asr("podcast") is True
    assert transcribe_cmd.kind_needs_asr("youtube") is False


def test_a_host_with_no_engine_exits_zero(db, monkeypatch, capsys):
    """Not 1. A Linux server having no Metal GPU is permanent and correct, so
    a non-zero exit would make content_fast_rss red every four hours."""
    _linux_backend(monkeypatch)
    assert transcribe_cmd.cmd_transcribe(_args(db)) == 0
    out = capsys.readouterr().out
    assert "mlx-whisper MISSING" in out
    assert "Mac worker" in out


def test_an_audio_item_is_left_in_the_queue_with_no_skip_row(db, monkeypatch,
                                                             capsys):
    """The property the Mac worker depends on. A skip row would take the item
    out of GET /api/transcripts/queue permanently."""
    _linux_backend(monkeypatch)
    assert transcribe_cmd.cmd_transcribe(_args(db)) == 0
    assert "deferred to Mac: 1 items" in capsys.readouterr().out

    with Warehouse(db, read_only=True) as wh:
        tables = set(wh.sql("SELECT table_name FROM information_schema.tables"
                            )["table_name"])
        skips = (wh.sql("SELECT item_id, reason FROM content_transcribe_skip")
                 if "content_transcribe_skip" in tables else None)
        segments = wh.sql("SELECT * FROM transcript_segment")
        source = wh.sql("SELECT text_source FROM content_item "
                        "WHERE item_id = 'pod_item'").iloc[0]["text_source"]
    assert skips is None or skips.empty, (
        "the no-engine host recorded a skip row and took the item out of the "
        "Mac worker's queue")
    assert segments.empty
    assert source == "description"


def test_the_item_is_still_in_the_queue_the_route_serves(db, monkeypatch):
    """Read back through the same selection the queue route uses."""
    _linux_backend(monkeypatch)
    transcribe_cmd.cmd_transcribe(_args(db))
    with Warehouse(db, read_only=True) as wh:
        selection = transcribe_cmd.select_queue(
            wh, kinds=("podcast",), creators=frozenset(), since=None,
            min_relevance=0.0)
    assert set(selection.queue["item_id"]) == {"pod_item"}


def test_the_caption_route_still_runs_with_no_engine(db, monkeypatch):
    """Captions read text a third party already produced, so they need no GPU
    and must keep working on the server."""
    _linux_backend(monkeypatch)
    seen: list[str] = []

    from fpl_edge.ingest.content.youtube import TimedLine

    class _Caps:
        refused = False
        ok = True
        status = 200
        route = "test"
        lines = [TimedLine(start_s=0.0, text="a caption line"),
                 TimedLine(start_s=5.0, text="and the next one")]

    monkeypatch.setattr("fpl_edge.ingest.content.youtube.is_panel_creator",
                        lambda creator: True)
    monkeypatch.setattr("fpl_edge.ingest.content.urls.youtube_id",
                        lambda url: "abc123")
    monkeypatch.setattr("fpl_edge.ingest.content.youtube.panel_fetcher",
                        lambda: _NullFetcher())

    def _fetch(fetcher, vid, *, creator):
        seen.append(vid)
        return _Caps()

    monkeypatch.setattr("fpl_edge.ingest.content.youtube.fetch_panel_captions",
                        _fetch)

    assert transcribe_cmd.cmd_transcribe(_args(db, kinds="youtube")) == 0
    assert seen == ["abc123"]
    with Warehouse(db, read_only=True) as wh:
        rows = wh.sql("SELECT item_id FROM transcript_segment")
        prov = wh.sql("SELECT derivation FROM transcript_provenance")
    assert set(rows["item_id"]) == {"vid_item"}
    assert set(prov["derivation"]) == {"captions"}


class _NullFetcher:
    def close(self) -> None:
        pass


def test_the_nightly_task_reports_no_source_rather_than_alerting(monkeypatch,
                                                                 tmp_path):
    """DEPLOYMENT.md §1.3 item 2. ``error`` with a title is what the outbox
    sends, so the old behaviour fired "Nightly transcription FAILED" every
    night on a host that can never have the engine."""
    from fpl_edge.pipelines import contracts, registry

    _linux_backend(monkeypatch)
    monkeypatch.delenv("FPL_EDGE_DISABLE_NETWORK_INGEST", raising=False)
    recorded: list[list[str]] = []

    def _fake_step(name, argv, *, timeout=0.0):
        recorded.append(list(argv))
        return contracts.Step(name=name, ok=True, seconds=1.0, detail="ok")

    monkeypatch.setattr(registry, "run_step", _fake_step)
    ctx = contracts.TaskContext(
        season="2026-27", gw=0, due_utc=dt.datetime.now(UTC),
        deadline_utc=None, now=dt.datetime.now(UTC),
        db_path=tmp_path / "fpl.duckdb")

    result = registry.run_transcribe_nightly(ctx)
    assert result.outcome == "no_source"
    assert result.delivers is False, "a permanent gap must not page the owner"
    assert "Mac worker" in result.detail
    # Narrowed to the route that works here, rather than starting a queue it
    # cannot finish.
    assert ["--kinds", "youtube"] == recorded[0][-2:]


def test_the_nightly_task_is_quiet_on_a_host_with_the_engine(monkeypatch,
                                                             tmp_path):
    from fpl_edge.pipelines import contracts, registry

    _mac_backend(monkeypatch)
    monkeypatch.delenv("FPL_EDGE_DISABLE_NETWORK_INGEST", raising=False)
    recorded: list[list[str]] = []

    def _fake_step(name, argv, *, timeout=0.0):
        recorded.append(list(argv))
        return contracts.Step(name=name, ok=True, seconds=1.0, detail="ok")

    monkeypatch.setattr(registry, "run_step", _fake_step)
    ctx = contracts.TaskContext(
        season="2026-27", gw=0, due_utc=dt.datetime.now(UTC),
        deadline_utc=None, now=dt.datetime.now(UTC),
        db_path=tmp_path / "fpl.duckdb")

    result = registry.run_transcribe_nightly(ctx)
    assert result.outcome == "quiet"
    assert "--kinds" not in recorded[0], "the Mac must run every kind"


def test_a_real_failure_on_a_host_with_the_engine_still_alerts(monkeypatch,
                                                              tmp_path):
    """The honesty rule stays: a step that fails is an error WITH a title, so
    the outbox sends it and the ledger records it as a failure."""
    from fpl_edge.pipelines import contracts, registry

    _mac_backend(monkeypatch)
    monkeypatch.delenv("FPL_EDGE_DISABLE_NETWORK_INGEST", raising=False)
    monkeypatch.setattr(
        registry, "run_step",
        lambda name, argv, *, timeout=0.0: contracts.Step(
            name=name, ok=False, seconds=12.0, detail="exit 1: decoder broke"))
    ctx = contracts.TaskContext(
        season="2026-27", gw=0, due_utc=dt.datetime.now(UTC),
        deadline_utc=None, now=dt.datetime.now(UTC),
        db_path=tmp_path / "fpl.duckdb")

    result = registry.run_transcribe_nightly(ctx)
    assert result.outcome == "error"
    assert result.delivers is True
    assert result.title == "Nightly transcription FAILED"
