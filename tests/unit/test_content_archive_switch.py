"""The raw body archive: on by default, and switchable off for a deployment.

``ContentFetcher`` writes every fetched body under ``RAW_ROOT``. On the owner's
Mac that archive answers "what exactly did that page say when we read it" and
costs nothing. On a Railway volume it writes about 836 MB a day beside the
warehouse and nothing in the repo reads it, so a deployment turns it off and
keeps its provenance where it already lives: ``raw_fetch`` carries the source,
endpoint, sha256, status and instant of every fetch, and ``content_item``
carries the parsed text.
"""
from __future__ import annotations

import importlib

from fpl_edge.ingest.content import fetch as fetch_mod


def _reload(monkeypatch, **env):
    for key in ("FPL_EDGE_RAW", "FPL_EDGE_ARCHIVE_BODIES"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(fetch_mod)


def test_the_archive_is_on_and_under_data_raw_by_default(monkeypatch):
    mod = _reload(monkeypatch)
    assert mod.ARCHIVE_BODIES is True
    assert mod.RAW_ROOT.as_posix().endswith("data/raw/content")


def test_fpl_edge_raw_relocates_the_archive(monkeypatch):
    """DEPLOYMENT.md has always documented this variable as doing exactly this."""
    mod = _reload(monkeypatch, FPL_EDGE_RAW="/somewhere/else")
    assert mod.RAW_ROOT.as_posix() == "/somewhere/else/content"


def test_the_switch_turns_the_body_archive_off(monkeypatch):
    for value in ("0", "false", "no"):
        mod = _reload(monkeypatch, FPL_EDGE_ARCHIVE_BODIES=value)
        assert mod.ARCHIVE_BODIES is False, value


def test_a_fetcher_takes_the_switch_and_an_explicit_argument_still_wins(monkeypatch):
    mod = _reload(monkeypatch, FPL_EDGE_ARCHIVE_BODIES="0")
    assert mod.ContentFetcher("ingest").archive is False
    assert mod.ContentFetcher("ingest", archive=True).archive is True
    mod = _reload(monkeypatch)
    assert mod.ContentFetcher("ingest").archive is True
    assert mod.ContentFetcher("asr", archive=False).archive is False


def test_nothing_is_written_when_the_archive_is_off(monkeypatch, tmp_path):
    mod = _reload(monkeypatch, FPL_EDGE_ARCHIVE_BODIES="0", FPL_EDGE_RAW=str(tmp_path))
    import datetime as dt

    with mod.ContentFetcher("ingest") as fetcher:
        written = fetcher._archive(
            "https://example.invalid/a", b"body", dt.datetime.now(dt.UTC), "d" * 64)
    assert written is None
    assert not (tmp_path / "content").exists()


def test_the_body_is_written_when_it_is_on(monkeypatch, tmp_path):
    mod = _reload(monkeypatch, FPL_EDGE_RAW=str(tmp_path))
    import datetime as dt

    with mod.ContentFetcher("ingest") as fetcher:
        written = fetcher._archive(
            "https://example.invalid/a", b"body", dt.datetime.now(dt.UTC), "d" * 64)
    assert written is not None and written.read_bytes() == b"body"
    assert written.parent == tmp_path / "content" / "ingest"
