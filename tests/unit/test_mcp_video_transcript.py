"""The toolbelt's YouTube transcript route, unified onto fpl_edge's discipline.

PIPELINES.md §3 defect 2: ``fpl_mcp/utils/video_transcript.py`` used to be a
second Innertube client -- bare ``requests`` against the exact ``/youtubei/``
route the engine refuses as robots-disallowed, with no robots check and every
failure collapsed to ``[]``. These tests pin the unification:

* the module delegates to the engine's sanctioned panel-captions path and
  fetches captions ONLY for creators on the owner's curated panel;
* an off-panel video is REFUSED WITH A REASON that names the sanctioned
  alternative (the platform's paste-a-link preview flow) -- never a silent
  empty list;
* a 403/429 is the source declining: reported, obeyed, not retried;
* the MCP tools (``fetch_youtube_transcript``, ``summarise_fpl_youtube``)
  surface that reason to the caller whenever the transcript is empty.

Hermetic throughout: a fake fetcher stands in for the network, and the REAL
engine code (channel_from_watch, creator_for_channel, fetch_panel_captions)
runs against it.
"""

from __future__ import annotations

import json

import pytest

from fpl_mcp.utils.video_transcript import (
    TranscriptResult,
    extract_video_id,
    get_transcript,
)

VID = "dQw4w9WgXcQ"

#: A watch page whose channel is on the curated panel (PANEL_CREATORS), with
#: the Innertube key fetch_panel_captions needs.
PANEL_PAGE = (
    '<html>{"microformat":{"playerMicroformatRenderer":'
    '{"ownerChannelName":"FPL Raptor"}}}'
    '"INNERTUBE_API_KEY":"testkey"</html>'
)

#: A real, named channel that is NOT on the panel.
OFF_PANEL_PAGE = (
    '<html>{"microformat":{"playerMicroformatRenderer":'
    '{"ownerChannelName":"Barry\'s Back Garden FPL"}}}'
    '"INNERTUBE_API_KEY":"testkey"</html>'
)

PLAYER_JSON = {
    "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [
        {"languageCode": "en", "baseUrl": "https://yt.example/timedtext?v=1"},
    ]}},
}

CAPTION_XML = (
    b'<transcript>'
    b'<text start="0.0">i am bringing in haaland</text>'
    b'<text start="2.5">and captaining him</text>'
    b'</transcript>'
)


class R:
    def __init__(self, status, body=b"", error=None):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode("utf-8")
        self.error = error
        self.robots_blocked = False

    @property
    def ok(self):
        return self.status == 200 and self.error is None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


class FakeFetcher:
    """Answers like ContentFetcher, records every request."""

    def __init__(self, watch_page: str, *, watch_status: int = 200,
                 player=PLAYER_JSON, xml: bytes = CAPTION_XML):
        self.watch_page = watch_page
        self.watch_status = watch_status
        self.player = player
        self.xml = xml
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def get(self, url, **_kw):
        self.calls.append(("get", url))
        if "watch?v=" in url:
            if self.watch_status != 200:
                return R(self.watch_status)
            return R(200, self.watch_page)
        return R(200, self.xml)

    def post_json(self, url, _payload):
        self.calls.append(("post", url))
        return R(200, json.dumps(self.player))

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# extract_video_id


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/live/{VID}",
])
def test_extract_video_id_handles_the_url_shapes(url):
    assert extract_video_id(url) == VID


def test_extract_video_id_returns_none_for_junk():
    assert extract_video_id("https://example.com/watch?v=nope") is None


# ---------------------------------------------------------------------------
# get_transcript: the sanctioned route


def test_a_panel_creators_captions_come_back_through_the_engine_route():
    fetcher = FakeFetcher(PANEL_PAGE)
    result = get_transcript(VID, fetcher=fetcher)
    assert result.ok
    assert result.lines == ("i am bringing in haaland", "and captaining him")
    assert result.route == "innertube"
    assert result.creator == "FPL Raptor"
    assert result.reason is None
    # The route ran through the engine's three-request caption path (identity
    # watch + fetch_panel_captions' own watch, player, timedtext).
    kinds = [k for k, _ in fetcher.calls]
    assert kinds == ["get", "get", "post", "get"]


def test_an_off_panel_video_is_refused_with_the_reason_never_an_empty_list():
    """THE defect: the old client fetched anyone and returned [] on failure."""
    fetcher = FakeFetcher(OFF_PANEL_PAGE)
    result = get_transcript(VID, fetcher=fetcher)
    assert not result.ok
    assert result.route == "off_panel"
    assert result.reason is not None
    # The engine's own refusal, plus the sanctioned way out.
    assert "curated panel" in result.reason
    assert "/api/ingest/link" in result.reason
    assert result.channel == "Barry's Back Garden FPL"
    # Refused BEFORE the caption route spent anything: one identity fetch only.
    assert fetcher.calls == [("get", f"https://www.youtube.com/watch?v={VID}")]


def test_a_403_is_the_source_declining_and_is_not_retried():
    fetcher = FakeFetcher(PANEL_PAGE, watch_status=403)
    result = get_transcript(VID, fetcher=fetcher)
    assert not result.ok
    assert result.route == "source_refused_403"
    assert "403" in result.reason
    assert len(fetcher.calls) == 1  # one request, no retry


def test_a_video_with_no_caption_track_reports_that_not_an_empty_string():
    fetcher = FakeFetcher(PANEL_PAGE, player={"captions": {}})
    result = get_transcript(VID, fetcher=fetcher)
    assert not result.ok
    assert result.route == "no_english_track"
    assert "captions" in result.reason


def test_the_module_carries_no_bare_requests_client_any_more():
    """The second fetch stack is gone, structurally."""
    from pathlib import Path

    import fpl_mcp.utils.video_transcript as module

    assert not hasattr(module, "requests")
    src = Path(module.__file__).read_text(encoding="utf-8")
    assert "import requests" not in src
    assert "requests.get(" not in src and "requests.post(" not in src


# ---------------------------------------------------------------------------
# The MCP tools surface the reason


def _refusal(reason: str, route: str = "off_panel") -> TranscriptResult:
    return TranscriptResult(video_id=VID, lines=(), route=route, reason=reason)


def test_fetch_youtube_transcript_returns_the_refusal_reason(monkeypatch):
    from fpl_mcp.tools import transcript_tools

    monkeypatch.setattr(transcript_tools, "get_transcript",
                        lambda vid: _refusal("not on the curated panel; "
                                             "use /api/ingest/link"))
    out = transcript_tools.fetch_youtube_transcript(
        f"https://www.youtube.com/watch?v={VID}")
    assert out["transcript"] == ""
    assert out["reason"] is not None and "curated panel" in out["reason"]
    assert out["route"] == "off_panel"
    assert out["video_id"] == VID


def test_fetch_youtube_transcript_flags_an_invalid_url_with_a_reason():
    from fpl_mcp.tools import transcript_tools

    out = transcript_tools.fetch_youtube_transcript("https://example.com/x")
    assert out["transcript"] == ""
    assert out["video_id"] is None
    assert out["reason"]  # never a silent empty result


def test_fetch_youtube_transcript_passes_a_real_transcript_through(monkeypatch):
    from fpl_mcp.tools import transcript_tools

    monkeypatch.setattr(
        transcript_tools, "get_transcript",
        lambda vid: TranscriptResult(video_id=vid,
                                     lines=("hello", "world"),
                                     route="innertube"))
    out = transcript_tools.fetch_youtube_transcript(
        f"https://youtu.be/{VID}")
    assert out["transcript"] == "hello\nworld"
    assert out["reason"] is None


def test_summarise_fpl_youtube_surfaces_the_refusal_reason(monkeypatch):
    from fpl_mcp.tools import video_tools

    monkeypatch.setattr(video_tools, "get_transcript",
                        lambda vid: _refusal("YouTube declined (429); obeyed, "
                                             "not retried",
                                             route="player_429"))
    out = video_tools.summarise_fpl_youtube(
        f"https://www.youtube.com/watch?v={VID}")
    assert out["players"] == [] and out["main_points"] == []
    assert "429" in out["summary"]
    assert "429" in out["reason"]
