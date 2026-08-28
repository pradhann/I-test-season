"""Transcription: the scale limit, the completeness guard, and the cache.

The three things that would hurt most if they broke, and are therefore what
this file tests:

1. **The panel gate.** The 2026-08-27 policy change permits caption fetching
   for a named, bounded list of creators. If a typo in ``PANEL_CREATORS`` or a
   forgotten check widened that to "every channel in the registry", the code
   would still work perfectly and would be doing the thing the policy forbids.
   Nothing about the output would look wrong. So the gate is tested by
   asserting on the *number of HTTP requests made*, not on the return value.
2. **The completeness guard.** Whisper's characteristic failure is stopping
   early and returning successfully. A 62-minute episode transcribed to four
   minutes, stored as the whole thing, produces "the creator never mentioned
   Haaland" -- a confident false negative, which is worse than a gap.
3. **The cache.** "Nothing is ever re-fetched" is a politeness commitment made
   in docs/data_sources.md §7A. It is only true if the cache is consulted
   before the network, so that is what is asserted.

No test here loads model weights or touches the network.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

import pytest

from fpl_edge.ingest.content import asr
from fpl_edge.ingest.content.models import ContentItem
from fpl_edge.ingest.content.sources import ALL_SOURCES, Source, SourceKind
from fpl_edge.ingest.content.youtube import (
    PANEL_CREATORS,
    OffPanelRefused,
    fetch_panel_captions,
    is_panel_creator,
    timed_lines_from_xml,
)
from fpl_edge.store import Warehouse

UTC = dt.UTC
NOW = dt.datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Doubles


class FakeResponse:
    def __init__(self, *, status=200, body=b"", error=None, robots_blocked=False,
                 url="https://example.test/x"):
        self.status = status
        self.body = body
        self.error = error
        self.robots_blocked = robots_blocked
        self.url = url

    @property
    def ok(self):
        return self.status == 200 and self.error is None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


class RecordingFetcher:
    """Counts every request. The panel gate is proven by this count being 0."""

    def __init__(self, responses=None):
        self.calls: list[str] = []
        self._responses = responses or {}

    def _answer(self, url):
        self.calls.append(url)
        for prefix, resp in self._responses.items():
            if url.startswith(prefix):
                return resp
        return FakeResponse(status=404)

    def get(self, url, **_kwargs):
        return self._answer(url)

    def post_json(self, url, _payload):
        return self._answer(url)

    def close(self):
        pass


def _warehouse(tmp_path):
    """A warehouse with the content tables and one description-sourced item."""
    from fpl_edge.ingest.content.store import ContentStore

    wh = Warehouse(tmp_path / "asr.duckdb")
    ContentStore(wh)  # runs content_001 / content_002
    asr.ensure_schema(wh)
    text = "Show notes. Subscribe to the podcast."
    wh.sql(
        "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["item1", "pod_x", "FPL Harry", "podcast", "GW2 team reveal",
         "https://example.test/ep", NOW, NOW, "description", text,
         hashlib.sha256(text.encode()).hexdigest()],
    )
    return wh


def _transcription(**over):
    base = {
        "segments": (
            asr.Segment(0, 0.0, 4.0, "Haaland is my captain this week."),
            asr.Segment(1, 4.0, 9.0, "I am transferring in Semenyo."),
        ),
        "model": "mlx-community/whisper-large-v3-turbo",
        "engine": "mlx-whisper",
        "language": "en",
        "audio_seconds": 600.0,
        "covered_seconds": 595.0,
        "wall_seconds": 60.0,
        "audio_sha256": "deadbeef",
        "audio_bytes": 1234,
        "audio_url": "https://example.test/ep.mp3",
    }
    base.update(over)
    return asr.Transcription(**base)


# ---------------------------------------------------------------------------
# 1. The panel gate -- scale is the policy


def test_off_panel_caption_fetch_raises_before_any_request():
    """An off-panel creator costs YouTube zero requests, not a wasted one.

    Asserting on ``fetcher.calls`` rather than on the return value is the
    point: a gate that refuses *after* fetching has already done the thing the
    policy forbids, and every observable output would still look correct.
    The example used to be "FPL Raptor". The owner then named him, so he moved
    ONTO the panel and the test would have passed for the wrong reason -- it
    must name someone genuinely outside the ceiling, and it must keep working
    when the roster changes again.
    """
    fetcher = RecordingFetcher()
    off_panel = sorted({s.creator for s in ALL_SOURCES} - set(PANEL_CREATORS))
    assert off_panel, "the ceiling cannot be the whole registry, or this proves nothing"
    with pytest.raises(OffPanelRefused):
        fetch_panel_captions(fetcher, "abcdefghijk", creator=off_panel[0])
    assert fetcher.calls == []


def test_panel_creator_names_all_exist_in_the_source_registry():
    """A typo in PANEL_CREATORS silently disables a creator with no error.

    ``"Let's Talk FPL"`` with the wrong apostrophe matches nothing, queues
    nothing, and reports success. This is the test that catches it.
    """
    registry = {s.creator for s in ALL_SOURCES}
    assert PANEL_CREATORS <= registry, sorted(PANEL_CREATORS - registry)


def test_panel_membership_is_exact_not_substring():
    assert is_panel_creator("FPL Harry")
    assert not is_panel_creator("FPL Harry Highlights")
    assert not is_panel_creator("")
    assert not is_panel_creator(None)


def test_a_403_is_recorded_as_a_refusal_and_stops_the_route():
    """403/429 is the source declining. Recorded, flagged, not routed around."""
    fetcher = RecordingFetcher({
        "https://www.youtube.com/watch": FakeResponse(status=403),
    })
    caps = fetch_panel_captions(fetcher, "abcdefghijk", creator="FPL Harry")
    assert caps.refused is True
    assert caps.status == 403
    assert caps.lines == ()
    # One request, then stop. No second route, no retry with other parameters.
    assert len(fetcher.calls) == 1


# ---------------------------------------------------------------------------
# 2. Timestamps -- the thing deep links are built from


def test_caption_cues_keep_their_start_times():
    xml = (b'<transcript><text start="0.5" dur="2">Haaland captain</text>'
           b'<text start="12.25" dur="3">Semenyo in</text></transcript>')
    lines = timed_lines_from_xml(xml)
    assert [(line.start_s, line.text) for line in lines] == [
        (0.5, "Haaland captain"), (12.25, "Semenyo in"),
    ]


def test_a_cue_with_no_parsable_start_is_dropped_not_defaulted_to_zero():
    """A link that jumps to 0:00 of a 50-minute video looks like it worked."""
    xml = (b'<transcript><text>no start attribute</text>'
           b'<text start="oops">unparsable</text>'
           b'<text start="7.0">kept</text></transcript>')
    assert [line.text for line in timed_lines_from_xml(xml)] == ["kept"]


def test_the_millisecond_dialect_is_converted_not_taken_literally():
    """`<p t="1500">` is 1.5s. Read as seconds it is 25 minutes too late."""
    xml = b'<timedtext><body><p t="1500">later</p></body></timedtext>'
    lines = timed_lines_from_xml(xml)
    assert lines[0].start_s == pytest.approx(1.5)


def test_captions_do_not_claim_a_measured_audio_duration():
    """We never downloaded the video, so its length is unknown, not inferred."""
    from fpl_edge.ingest.content.youtube import TimedLine

    result = asr.transcription_from_captions(
        [TimedLine(0.0, "one"), TimedLine(30.0, "two")],
        video_id="abcdefghijk", route="innertube", wall_seconds=1.0,
    )
    assert result.audio_seconds is None
    assert result.coverage is None          # not 1.0, not 0%
    assert result.covered_seconds == 30.0
    assert result.engine == "youtube_captions"


# ---------------------------------------------------------------------------
# 3. The completeness guard -- a partial transcript is worse than none


def test_an_early_stopping_decode_raises_and_returns_nothing(tmp_path, monkeypatch):
    """30% of the audio transcribed, returned without error: must not pass."""
    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"not really audio")

    monkeypatch.setattr(asr, "decode_audio",
                        lambda path, decoder: (object(), 3600.0))
    fake = type("M", (), {"transcribe": staticmethod(lambda *a, **k: {
        "segments": [{"start": 0.0, "end": 1080.0, "text": "first 18 minutes"}],
        "language": "en",
    })})
    monkeypatch.setitem(__import__("sys").modules, "mlx_whisper.transcribe", fake)

    with pytest.raises(asr.PartialTranscript) as exc:
        asr.transcribe_file(
            audio_path, model="test",
            status=asr.BackendStatus(mlx_whisper=True, decoder="pyav"),
        )
    assert "30%" in str(exc.value) or "coverage" in str(exc.value)


def test_a_decode_that_returns_no_segments_raises(tmp_path, monkeypatch):
    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"x")
    monkeypatch.setattr(asr, "decode_audio",
                        lambda path, decoder: (object(), 600.0))
    fake = type("M", (), {"transcribe": staticmethod(
        lambda *a, **k: {"segments": [], "language": "en"})})
    monkeypatch.setitem(__import__("sys").modules, "mlx_whisper.transcribe", fake)

    with pytest.raises(asr.PartialTranscript):
        asr.transcribe_file(
            audio_path, model="test",
            status=asr.BackendStatus(mlx_whisper=True, decoder="pyav"),
        )


def test_a_short_trailing_outro_is_not_treated_as_a_partial(tmp_path, monkeypatch):
    """The guard must not reject the normal case of music over the credits."""
    audio_path = tmp_path / "ep.mp3"
    audio_path.write_bytes(b"x")
    monkeypatch.setattr(asr, "decode_audio",
                        lambda path, decoder: (object(), 240.0))
    fake = type("M", (), {"transcribe": staticmethod(lambda *a, **k: {
        "segments": [{"start": 0.0, "end": 195.0, "text": "the whole episode"}],
        "language": "en",
    })})
    monkeypatch.setitem(__import__("sys").modules, "mlx_whisper.transcribe", fake)

    # 81% coverage, but only 45s uncovered: below MAX_UNCOVERED_S, so kept.
    result = asr.transcribe_file(
        audio_path, model="test",
        status=asr.BackendStatus(mlx_whisper=True, decoder="pyav"),
    )
    assert result.covered_seconds == 195.0


def test_storing_an_empty_transcription_is_refused(tmp_path):
    wh = _warehouse(tmp_path)
    try:
        with pytest.raises(asr.PartialTranscript):
            asr.store_transcription(wh, "item1", _transcription(segments=()))
        with pytest.raises(asr.PartialTranscript):
            asr.store_transcription(wh, "item1", _transcription(
                segments=(asr.Segment(0, 0.0, 1.0, "   "),)))
        # And the item is untouched: still show notes, no segments.
        assert wh.sql("SELECT text_source FROM content_item WHERE item_id = 'item1'"
                      ).iloc[0]["text_source"] == "description"
        assert wh.sql("SELECT count(*) c FROM transcript_segment").iloc[0]["c"] == 0
    finally:
        wh.close()


# ---------------------------------------------------------------------------
# 4. Persistence -- shape, promotion and provenance


def test_segments_land_with_timestamps_and_the_item_is_promoted(tmp_path):
    from fpl_edge.ingest.content.analyze import is_scoreable

    wh = _warehouse(tmp_path)
    try:
        before = wh.sql(
            "SELECT text_sha256 FROM content_item WHERE item_id = 'item1'"
        ).iloc[0]["text_sha256"]

        n = asr.store_transcription(wh, "item1", _transcription())
        assert n == 2

        segs = wh.sql("SELECT seq, start_s, text FROM transcript_segment "
                      "WHERE item_id = 'item1' ORDER BY seq")
        assert list(segs["seq"]) == [0, 1]
        assert list(segs["start_s"]) == [0.0, 4.0]

        item = wh.sql("SELECT text_source, text, text_sha256 FROM content_item "
                      "WHERE item_id = 'item1'").iloc[0]
        # The gate the whole exercise exists to pass.
        assert item["text_source"] == "transcript"
        assert is_scoreable(item["text_source"]) is True
        # text moved WITH text_source. Labelling show notes a transcript would
        # be the exact mislabelling this module exists to end.
        assert "Haaland is my captain" in item["text"]
        assert item["text_sha256"] == hashlib.sha256(
            item["text"].encode()).hexdigest()

        prov = wh.sql("SELECT * FROM transcript_provenance "
                      "WHERE item_id = 'item1'").iloc[0]
        assert prov["derivation"] == "asr"
        assert prov["engine"] == "mlx-whisper"
        assert prov["prior_text_source"] == "description"
        assert prov["prior_text_sha256"] == before   # the swap is auditable
    finally:
        wh.close()


def test_derivation_distinguishes_asr_from_published_captions(tmp_path):
    wh = _warehouse(tmp_path)
    try:
        asr.store_transcription(wh, "item1", _transcription(),
                                derivation="captions")
        assert wh.sql("SELECT derivation FROM transcript_provenance"
                      ).iloc[0]["derivation"] == "captions"
    finally:
        wh.close()


def test_stale_show_notes_analyses_are_dropped_but_transcript_reads_are_kept(tmp_path):
    """`analyze` skips items that already have a row, so a stale read sticks."""
    wh = _warehouse(tmp_path)
    try:
        wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
               ["item1", "model-a", NOW,
                json.dumps({"evidence": {"text_source": "description"}})])
        wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
               ["item1", "model-b", NOW,
                json.dumps({"evidence": {"text_source": "transcript"}})])
        assert asr.stale_analyses(wh, "item1") == 1
        left = wh.sql("SELECT model FROM content_analysis WHERE item_id = 'item1'")
        assert list(left["model"]) == ["model-b"]
    finally:
        wh.close()


# ---------------------------------------------------------------------------
# 5. The cache and the refusals -- politeness, asserted


def test_cached_audio_is_used_and_the_network_is_not_touched(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "AUDIO_CACHE", tmp_path / "cache")
    url = "https://cdn.example.test/ep1.mp3"
    fetcher = RecordingFetcher({
        "https://cdn": FakeResponse(status=200, body=b"ID3" + b"\x00" * 2048),
    })

    first = asr.fetch_audio(fetcher, url)
    assert first.ok and not first.from_cache
    assert len(fetcher.calls) == 1

    second = asr.fetch_audio(fetcher, url)
    assert second.ok and second.from_cache
    assert second.path == first.path
    # The whole commitment in one assertion: no second request.
    assert len(fetcher.calls) == 1


def test_a_429_is_recorded_as_a_refusal_with_its_real_status(tmp_path, monkeypatch):
    monkeypatch.setattr(asr, "AUDIO_CACHE", tmp_path / "cache")
    fetcher = RecordingFetcher({"https://cdn": FakeResponse(status=429)})
    got = asr.fetch_audio(fetcher, "https://cdn.example.test/ep.mp3")
    assert got.ok is False
    assert got.status == 429            # the REAL code, not None, not "error"
    assert got.error == "refused_429"
    assert got.path is None


def test_an_html_error_page_served_with_a_200_is_not_transcribed(tmp_path, monkeypatch):
    """Decoding it yields noise, and noise transcribes into confident words."""
    monkeypatch.setattr(asr, "AUDIO_CACHE", tmp_path / "cache")
    fetcher = RecordingFetcher({
        "https://cdn": FakeResponse(status=200, body=b"<!DOCTYPE html><html>"
                                                     b"<body>404</body></html>"),
    })
    got = asr.fetch_audio(fetcher, "https://cdn.example.test/ep.mp3")
    assert got.ok is False
    assert got.error == "not_audio_html"
    assert list((tmp_path / "cache").glob("*")) == [] or got.path is None


# ---------------------------------------------------------------------------
# 6. Enclosure resolution -- must agree with the loader's identity rule


FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>GW2 team reveal</title>
    <guid isPermaLink="false">74b8ffec-a205-11f1-9f9e-87719b00dbeb</guid>
    <pubDate>Wed, 27 Aug 2026 06:00:00 -0000</pubDate>
    <enclosure url="https://cdn.example.test/ep2.mp3" length="19498171"
               type="audio/mpeg"/>
  </item>
  <item>
    <title>A video item we do not want</title>
    <guid isPermaLink="false">not-audio-guid</guid>
    <pubDate>Wed, 26 Aug 2026 06:00:00 -0000</pubDate>
    <enclosure url="https://cdn.example.test/ep3.pdf" type="application/pdf"/>
  </item>
</channel></rss>
"""


def test_enclosure_ids_match_the_loader_exactly():
    """If these two id rules disagree the join is silently empty.

    The failure mode is not an exception: it is ``transcribe`` reporting "no
    audio available" for a feed that publishes audio on every single item.
    """
    source = Source("pod_x", "FPL Harry", SourceKind.PODCAST,
                    "https://feeds.example.test/x")
    fetcher = RecordingFetcher({
        "https://feeds": FakeResponse(status=200, body=FEED),
    })
    found, status = asr.enclosures_from_feed(fetcher, source)

    expected = ContentItem.make_id("pod_x", "74b8ffec-a205-11f1-9f9e-87719b00dbeb")
    assert status == 200
    assert found == {expected: "https://cdn.example.test/ep2.mp3"}


def test_a_feed_that_fails_reports_its_real_status_and_no_enclosures():
    source = Source("pod_x", "FPL Harry", SourceKind.PODCAST,
                    "https://feeds.example.test/x")
    fetcher = RecordingFetcher({"https://feeds": FakeResponse(status=503)})
    found, status = asr.enclosures_from_feed(fetcher, source)
    assert found == {}
    assert status == 503


# ---------------------------------------------------------------------------
# 7. The absolute invariant


def test_the_asr_module_imports_no_inference_client():
    """No Anthropic tokens on transcription -- checked, not just documented."""
    import pathlib

    source = pathlib.Path(asr.__file__).read_text()
    for banned in ("anthropic", "openai", "AsyncAnthropic", "api.anthropic.com"):
        # Only real code, not the docstring saying it must not happen.
        code = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith(("#", '"', "*", ":"))
        )
        assert banned not in code, f"{banned!r} appears in asr.py"


def test_the_backend_reports_what_is_missing_with_the_install_command():
    status = asr.BackendStatus(mlx_whisper=False, decoder=None)
    assert status.ready is False
    hint = status.install_hint()
    assert "uv pip install" in hint and "mlx-whisper" in hint and "av" in hint
    with pytest.raises(asr.AsrUnavailable):
        asr.transcribe_file(__import__("pathlib").Path("nope.mp3"), status=status)


# ---------------------------------------------------------------------------
# 8. Meeting the other agents' schema where it actually landed


def test_no_stored_enclosure_column_reports_none_rather_than_empty(tmp_path):
    """A missing column and a feed with no audio have the same symptom.

    ``enclosure_lookup`` returns WHERE it looked so the command can tell the
    two apart in its output instead of printing "no audio available" for both.
    """
    wh = Warehouse(tmp_path / "bare.duckdb")   # no content migrations at all
    try:
        assert asr.enclosure_lookup(wh) == ({}, "none")
    finally:
        wh.close()


def test_enclosures_are_read_from_the_side_table_when_it_exists(tmp_path):
    """The concurrent feed-repair work put enclosure_url on
    ``content_item_asset``, not on ``content_item`` -- because content_item is
    written positionally by code this package does not own and widening it
    breaks those writers. Both shapes must work.
    """
    wh = _warehouse(tmp_path)
    try:
        wh.sql("DELETE FROM content_item_asset")
        wh.sql("INSERT INTO content_item_asset (item_id, enclosure_url, "
               "checked_utc) VALUES ('item1', "
               "'https://cdn.example.test/ep.mp3', ?)", [NOW])
        wh.sql("INSERT INTO content_item_asset (item_id, enclosure_url, "
               "checked_utc) VALUES ('item2', NULL, ?)", [NOW])

        found, origin = asr.enclosure_lookup(wh)
        assert origin == "content_item_asset.enclosure_url"
        # A NULL enclosure is absent, not an empty-string URL to try fetching.
        assert found == {"item1": "https://cdn.example.test/ep.mp3"}
    finally:
        wh.close()


def test_a_roster_ahead_of_the_ceiling_is_reported_not_obeyed(tmp_path):
    """PANEL_CREATORS is a ceiling. A YAML edit must not widen a crawl.

    The roster in panel.py is curated and may legitimately move ahead of this
    module's constant. When it does the caption route refuses those shows, and
    this is how the owner finds out -- instead of discovering it as an absence
    six weeks later.

    The two panel tables are built by hand here rather than by running the
    panel migration: they belong to another agent, their columns are still
    growing, and this test is about the ceiling, not about their schema. The
    two column names it does depend on are the same two ``panel.panel_scope``
    itself queries, and any mismatch degrades to ``()`` rather than raising.
    """
    from fpl_edge.ingest.content.youtube import divergence_from_roster

    wh = Warehouse(tmp_path / "roster.duckdb")
    try:
        # No panel tables at all: silent, not an error.
        assert divergence_from_roster(wh) == ()

        wh.sql("CREATE TABLE panel_person (person_key VARCHAR, active BOOLEAN)")
        wh.sql("CREATE TABLE panel_person_show (person_key VARCHAR, "
               "show_creator VARCHAR)")
        # The example must be a show the ceiling genuinely does not admit, and
        # it must STAY one. This test previously used "Solio Analytics"; the
        # owner then named Solio and supplied its handle, so it moved onto the
        # ceiling and the test asserted the opposite of the truth. A synthetic
        # name cannot be admitted by a future roster change.
        ahead = "A Show The Ceiling Does Not Admit"
        assert not is_panel_creator(ahead), "pick a name outside the ceiling"
        wh.sql("INSERT INTO panel_person VALUES ('a', true), ('b', true)")
        wh.sql("INSERT INTO panel_person_show VALUES ('a', 'FPL Harry'), "
               f"('b', '{ahead}')")

        assert divergence_from_roster(wh) == (ahead,)
        # ...and the ceiling itself has not moved.
        assert not is_panel_creator(ahead)
    finally:
        wh.close()


def test_dry_run_creates_nothing_not_even_the_ledger_ddl(tmp_path, capsys):
    """A --dry-run that writes DDL into a shared warehouse is not a dry run.

    "It is only DDL" is the argument that makes the flag untrustworthy: two
    other agents write this file, and a flag whose whole promise is "spend
    nothing" has to be checkable rather than believed.
    """
    import argparse

    from fpl_edge.ingest.content.pipeline import cmd_transcribe

    wh = _warehouse(tmp_path)
    try:
        wh.sql(
            "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ["yt1", "yt_fplharry", "FPL Harry", "youtube", "GW2 picks",
             "https://www.youtube.com/watch?v=abcdefghijk", NOW, NOW,
             "description", "notes", hashlib.sha256(b"notes").hexdigest()],
        )
        # _warehouse() calls asr.ensure_schema(); undo that so the assertion
        # below is about what THIS run created, not what the fixture did.
        wh.sql("DROP TABLE IF EXISTS content_transcribe_skip")
        wh.sql("DROP TABLE IF EXISTS transcript_provenance")
    finally:
        wh.close()

    db = tmp_path / "asr.duckdb"   # the file _warehouse() built
    args = argparse.Namespace(
        db=str(db), delay=1.0, limit=5, since=0, budget_s=0.0,
        kinds="youtube", creator=None, any_creator=False, model=None,
        dry_run=True,
    )
    assert cmd_transcribe(args) == 0
    assert "nothing transcribed, nothing written" in capsys.readouterr().out

    check = Warehouse(db, read_only=True)
    try:
        created = int(check.sql(
            "SELECT count(*) c FROM information_schema.tables WHERE table_name "
            "IN ('content_transcribe_skip', 'transcript_provenance')"
        ).iloc[0]["c"])
    finally:
        check.close()
    assert created == 0
