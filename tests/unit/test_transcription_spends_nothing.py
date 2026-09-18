"""Transcription costs no Anthropic tokens, proved rather than asserted.

The owner's ask was "make sure the transcriptions are not taking tokens". The
answer is already yes: ``fpl_edge/ingest/content/asr.py`` runs MLX-Whisper
locally on the Metal GPU and there is deliberately no remote fallback. But
"already yes" is a claim about today's code, and the way it stops being true
is somebody adding a tidy little model call to clean up a messy transcript.

So the property is closed at the three doors a model call could leave by, all
of them shut before the command runs and none of them reopened for it:

* no subprocess named ``claude``;
* no ``anthropic`` or ``claude_agent_sdk`` import at call time;
* no outbound socket at all.

The last one is the strongest and subsumes the others, which is why it is
here: a transcription path that opened any connection would fail this test
whether or not the endpoint belonged to Anthropic.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import socket
import subprocess
import sys

import pytest

from fpl_edge.ingest.content import asr
from fpl_edge.ingest.content.store import ContentStore
from fpl_edge.store import Warehouse

UTC = dt.UTC
NOW = dt.datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

#: A creator on the caption ceiling, so the queue is not refused before the
#: gates under test get a chance to be crossed.
CREATOR = "FPL Harry"


class ModelCallAttempted(AssertionError):
    """Raised by whichever door a model call tried to leave by."""


class _Cue:
    """One published caption cue, in the shape asr.transcription_from_captions
    reads: a start in seconds and the words said at it."""

    def __init__(self, start_s: float, text: str) -> None:
        self.start_s, self.text = start_s, text


class _Fetcher:
    """A fetcher that fetches nothing and closes cleanly."""

    def close(self) -> None:
        return None


@pytest.fixture()
def no_doors_out(monkeypatch):
    """Every route to a model, closed and instrumented."""

    def _no_subprocess(argv, *args, **kwargs):
        raise ModelCallAttempted(f"transcription spawned a subprocess: {argv!r}")

    def _no_socket(*args, **kwargs):
        raise ModelCallAttempted("transcription opened a network connection")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)
    monkeypatch.setattr(subprocess, "Popen", _no_subprocess)
    monkeypatch.setattr(socket, "create_connection", _no_socket)
    monkeypatch.setattr(socket.socket, "connect", _no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_socket)

    for module in ("anthropic", "claude_agent_sdk"):
        monkeypatch.delitem(sys.modules, module, raising=False)
    return None


def _seed(tmp_path):
    db = tmp_path / "t.duckdb"
    wh = Warehouse(db)
    try:
        ContentStore(wh).migrate()
        asr.ensure_schema(wh)
        wh.sql(
            "INSERT INTO content_item (item_id, source_key, creator, kind, "
            "title, url, published_at, fetched_at, text_source, text, "
            "text_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["yt1", "yt_fplharry", CREATOR, "youtube",
             "GW6 transfers, captain and chip talk",
             "https://www.youtube.com/watch?v=abcdefghijk", NOW, NOW,
             "description", "who to captain in gameweek 6, transfers and chips",
             hashlib.sha256(b"yt1").hexdigest()],
        )
    finally:
        wh.close()
    return db


def _args(db, **over) -> argparse.Namespace:
    base = dict(db=str(db), delay=0.0, limit=5, since=0, budget_s=0.0,
                kinds="youtube", creator=None, any_creator=False, model=None,
                dry_run=False, min_relevance=0.0)
    base.update(over)
    return argparse.Namespace(**base)


def test_the_transcribe_command_reaches_no_model(tmp_path, monkeypatch,
                                                 no_doors_out):
    """The real entry point, with the local engine faked and every exit shut.

    The captions route is used rather than the audio one because it is the
    route that already fetches something over the network in production, and
    a test that only covered the offline-by-construction route would be
    proving the easy half.
    """
    from fpl_edge.ingest.content import transcribe_cmd, youtube

    db = _seed(tmp_path)

    monkeypatch.setattr(
        asr, "backend_status",
        lambda: asr.BackendStatus(mlx_whisper=True, decoder="pyav"))
    monkeypatch.setattr(youtube, "panel_fetcher", lambda *a, **k: _Fetcher())

    class _Captions:
        ok, refused, status, route = True, False, 200, "test"
        lines = [_Cue(0.0, "captain Haaland this week"),
                 _Cue(4.0, "and roll the free hit")]

    monkeypatch.setattr(youtube, "fetch_panel_captions",
                        lambda *a, **k: _Captions())

    assert transcribe_cmd.cmd_transcribe(_args(db)) == 0

    wh = Warehouse(db, read_only=True)
    try:
        segments = wh.sql("SELECT * FROM transcript_segment WHERE item_id='yt1'")
        assert len(segments) == 2, "the transcript was actually stored"
    finally:
        wh.close()


def test_no_anthropic_client_is_imported_by_the_transcription_path(
        tmp_path, monkeypatch, no_doors_out):
    """An import is the cheapest evidence of intent. Nothing on this path has
    a reason to load a model client, so nothing does."""
    from fpl_edge.ingest.content import transcribe_cmd, youtube

    db = _seed(tmp_path)
    monkeypatch.setattr(
        asr, "backend_status",
        lambda: asr.BackendStatus(mlx_whisper=True, decoder="pyav"))
    monkeypatch.setattr(youtube, "panel_fetcher", lambda *a, **k: _Fetcher())

    class _Captions:
        ok, refused, status, route = True, False, 200, "test"
        lines = [_Cue(0.0, "captain Haaland this week")]

    monkeypatch.setattr(youtube, "fetch_panel_captions",
                        lambda *a, **k: _Captions())

    transcribe_cmd.cmd_transcribe(_args(db))

    assert "anthropic" not in sys.modules
    assert "claude_agent_sdk" not in sys.modules


def test_the_doors_would_notice_a_model_call(no_doors_out):
    """The guard is only worth its place if it fires. Without this, a fixture
    that had stopped patching anything would pass forever and read as proof."""
    with pytest.raises(ModelCallAttempted, match="subprocess"):
        subprocess.run(["claude", "-p"])
    with pytest.raises(ModelCallAttempted, match="network"):
        socket.create_connection(("api.anthropic.com", 443))


def test_transcription_refuses_rather_than_falling_back_to_a_model(
        tmp_path, monkeypatch, no_doors_out, capsys):
    """With no local engine the command stops and says so. The failure mode
    that would cost tokens is a remote fallback, and there is not one."""
    from fpl_edge.ingest.content import transcribe_cmd

    db = _seed(tmp_path)
    monkeypatch.setattr(
        asr, "backend_status",
        lambda: asr.BackendStatus(mlx_whisper=False, decoder=None,
                                  mlx_whisper_error="not installed here"))

    assert transcribe_cmd.cmd_transcribe(_args(db)) == 1
    out = capsys.readouterr().out
    assert "no local transcription engine" in out
    assert "must not spend Anthropic tokens" in out
