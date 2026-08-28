"""Paste-a-link ingestion: the job contract, and every refusal it owes.

Hermetic. No network (the fetcher is injected), no transcription (the ingester
is injected), no live warehouse (every test gets a tmp DuckDB file). The five
failure states these tests name are not hypotheses -- each one is already in
the warehouse, and each test is named after the thing that went wrong:

1. a non-episode URL stored as an article titled ``a6fgym`` with three
   substantive characters,
2. one video pasted twice under two URL forms,
3. a video with neither captions nor downloadable audio,
4. a source answering 403/429 -- declining, which must stop rather than retry,
5. a transcription that dies mid-way and must store nothing at all.
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import threading
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from fpl_edge.ingest.content import asr
from fpl_edge.platform import link_jobs
from fpl_edge.platform.app import create_app
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
VID = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VID}"


# ---------------------------------------------------------------------------
# Doubles.


@dataclass
class FakeResponse:
    status: int | None
    body: str = ""
    error: str | None = None
    robots_blocked: bool = False

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None

    @property
    def text(self) -> str:
        return self.body


@dataclass
class FakeFetcher:
    """Stands in for ContentFetcher, and records every call it is asked to make."""

    response: FakeResponse
    calls: list[tuple[str, int]] = field(default_factory=list)
    closed: int = 0

    def get(self, url: str, *, retries: int = 2, **_: object) -> FakeResponse:
        self.calls.append((url, retries))
        return self.response

    def close(self) -> None:
        self.closed += 1


def factory_for(fetcher: FakeFetcher):
    return lambda _kind: fetcher


def preflight_with(fetcher: FakeFetcher):
    return functools.partial(link_jobs.preflight, fetcher_factory=factory_for(fetcher))


def watch_page(*, length: int | None = 1200, captions: bool = True,
               title: str = "GW3 Team Reveal") -> str:
    parts = [f"<html><head><title>{title} - YouTube</title></head><body>"]
    if length is not None:
        parts.append(f'{{"videoDetails":{{"lengthSeconds":"{length}"}}}}')
    if captions:
        parts.append('{"captionTracks":[{"languageCode":"en"}]}')
    parts.append("</body></html>")
    return "".join(parts)


@dataclass
class FakeFindings:
    """The subset of ``LinkFindings`` the job reads back."""

    text_source: str = "transcript"
    title: str = "GW3 Team Reveal"
    analysis_note: str = ""
    n_segments: int = 0
    n_claims: int = 0


ANALYSIS = {
    "summary": ["Haaland stays captain.", "Salva is the differential."],
    "transfers_in": [{"player": "Haaland", "conviction": "high",
                      "quote": "i am bringing in haaland this week"}],
    "transfers_out": [],
    "captaincy": [{"player": "Haaland", "conviction": "high",
                   "quote": "he is the captain again for me"}],
    "differentials": [],
    "chip_advice": [],
}

SEGMENTS = [
    (0.0, "welcome back to the channel"),
    (812.0, "i am bringing in haaland this week"),
    (901.5, "he is the captain again for me"),
]


def seed_item(db, *, item_id: str, url: str, segments=(), analysis=None,
              title: str = "GW3 Team Reveal", text: str = "x" * 50) -> None:
    """One stored publication, exactly as the ingester would have left it."""
    wh = Warehouse(db)
    try:
        from fpl_edge.ingest.content.store import ContentStore

        ContentStore(wh).migrate()
        now = dt.datetime(2026, 8, 20, tzinfo=UTC)
        wh.sql(
            "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [item_id, "user_link", "user-shared", "link", title, url, now, now,
             "transcript", text, "sha"],
        )
        for seq, (start, body) in enumerate(segments):
            wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
                   [item_id, seq, start, body])
        if analysis is not None:
            wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
                   [item_id, "claude-test", now, json.dumps(analysis)])
    finally:
        wh.close()


def storing_ingest(*, item_id: str = "link_new", segments=SEGMENTS,
                   analysis=ANALYSIS, findings: FakeFindings | None = None):
    """An ingester double that leaves behind what a real one would."""

    def _ingest(db, url):
        seed_item(db, item_id=item_id, url=url, segments=segments,
                  analysis=analysis)
        return findings or FakeFindings(n_segments=len(segments))

    return _ingest


def refusing_ingest(exc: Exception):
    def _ingest(_db, _url):
        raise exc

    return _ingest


def never_called_ingest(_db, _url):  # pragma: no cover - failing it is the point
    raise AssertionError("the ingester must not run for a refused or duplicate URL")


@pytest.fixture()
def db(tmp_path):
    """An empty warehouse with the content tables present."""
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    from fpl_edge.ingest.content.store import ContentStore

    ContentStore(wh).migrate()
    wh.close()
    return path


@pytest.fixture()
def store_dir(tmp_path):
    return tmp_path / "jobs"


def run_job(db, store_dir, url, *, fetcher=None, ingest=never_called_ingest,
            accept: bool = True, **kwargs) -> dict:
    """Submit, and by default ACCEPT the preview so the job runs to the end.

    Since 2026-08-27 a paste parks at the ``preview`` halt instead of running
    straight through -- the owner decides whether the content is worth
    transcribing before any GPU second is spent. These tests are about what
    happens on either side of that halt, not about the halt itself (which
    ``test_link_identity.py`` covers), so they accept and carry on. Pass
    ``accept=False`` to observe the parked state.
    """
    fetcher = fetcher or FakeFetcher(FakeResponse(200, watch_page()))
    jobs = link_jobs.LinkJobs(db, store_dir=store_dir, background=False,
                              preflight_fn=preflight_with(fetcher),
                              ingest_fn=ingest, **kwargs)
    started = jobs.submit(url)
    state = jobs.poll(started["job_id"])
    if accept and state["awaiting_decision"]:
        jobs.accept(started["job_id"])
        state = jobs.poll(started["job_id"])
    return state


def item_count(db) -> int:
    wh = Warehouse(db, read_only=True)
    try:
        return int(wh.sql("SELECT count(*) AS n FROM content_item").iloc[0]["n"])
    finally:
        wh.close()


# ---------------------------------------------------------------------------
# 1. A non-episode URL.


def test_league_invite_is_refused_without_a_single_fetch(db, store_dir):
    """The a6fgym case. Refused on shape, before the source is even touched."""
    fetcher = FakeFetcher(FakeResponse(200, "<html>whatever</html>"))
    state = run_job(db, store_dir,
                    "https://fantasy.premierleague.com/leagues/auto-join/a6fgym",
                    fetcher=fetcher)

    assert state["done"] is True
    assert state["item_id"] is None
    assert state["error_code"] == "not_an_episode"
    assert "a6fgym" in state["error"]
    assert fetcher.calls == [], "a URL refused on shape must cost the source nothing"
    assert item_count(db) == 0


def test_a_page_with_three_substantive_characters_is_refused_not_stored(db, store_dir):
    """The same failure through the other door: a page that fetches fine and
    says nothing. Three characters is what the stored a6fgym row carried."""
    fetcher = FakeFetcher(FakeResponse(200, "<html><body><p>a6f</p></body></html>"))
    state = run_job(db, store_dir, "https://example.com/join/a6fgym", fetcher=fetcher)

    assert state["error_code"] == "not_an_episode"
    assert "3 substantive characters" in state["error"]
    assert str(link_jobs.MIN_SUBSTANTIVE_CHARS) in state["error"]
    assert state["item_id"] is None
    assert item_count(db) == 0


def test_a_youtube_channel_page_is_not_an_episode(db, store_dir):
    state = run_job(db, store_dir, "https://www.youtube.com/@LetsTalkFPL")
    assert state["error_code"] == "not_an_episode"
    assert state["item_id"] is None


# ---------------------------------------------------------------------------
# 2. The same video pasted twice under two URL forms.


@pytest.mark.parametrize("second_form", [
    f"https://youtu.be/{VID}?si=abc123",
    f"https://www.youtube.com/live/{VID}",
    f"https://m.youtube.com/watch?reload=9&v={VID}",
])
def test_the_same_video_under_another_url_form_returns_the_existing_item(
        db, store_dir, second_form):
    seed_item(db, item_id="link_analysis", url=WATCH, analysis=ANALYSIS)
    before = item_count(db)

    state = run_job(db, store_dir, second_form, ingest=never_called_ingest)

    assert state["done"] is True
    assert state["error"] is None
    assert state["item_id"] == "link_analysis"
    assert item_count(db) == before, "a re-paste must not create a second row"


def test_a_video_split_across_two_rows_returns_the_analysis_row_with_timestamps(
        db, store_dir):
    """The real pair: one Andy row carries the analysis, the other the
    transcript segments. The take must be assembled across both or every quote
    loses its timestamp."""
    seed_item(db, item_id="link_analysis", url=WATCH, analysis=ANALYSIS)
    seed_item(db, item_id="link_segments", url=f"https://youtu.be/{VID}",
              segments=SEGMENTS)

    state = run_job(db, store_dir, f"https://www.youtube.com/live/{VID}")

    assert state["item_id"] == "link_analysis"
    assert set(state["duplicate_of"]) == {"link_analysis", "link_segments"}
    take = state["result"]["take"]
    assert take is not None
    captain = take["captain"][0]
    assert captain["start_s"] == 901.5
    assert captain["deep_link"] == f"https://www.youtube.com/watch?v={VID}&t=901s"
    assert state["result"]["n_segments"] == len(SEGMENTS)


# ---------------------------------------------------------------------------
# 3. Neither captions nor downloadable audio.


def test_a_video_with_no_caption_track_is_refused_with_nothing_stored(db, store_dir):
    fetcher = FakeFetcher(FakeResponse(200, watch_page(length=1200, captions=False)))
    state = run_job(db, store_dir, WATCH, fetcher=fetcher)

    assert state["error_code"] == "no_transcript_source"
    assert "no downloadable audio" in state["error"]
    assert state["item_id"] is None
    assert item_count(db) == 0


def test_the_ingester_reporting_no_transcript_surfaces_as_no_transcript_source(
        db, store_dir):
    """The late discovery: the watch page advertised a track, the fetch found
    none. ``ingest_link`` stores nothing in that branch and neither do we."""
    fetcher = FakeFetcher(FakeResponse(200, watch_page()))
    state = run_job(db, store_dir, WATCH, fetcher=fetcher,
                    ingest=lambda *_: FakeFindings(
                        text_source="unavailable (no_english_track)"))

    assert state["error_code"] == "no_transcript_source"
    assert "no_english_track" in state["error"]
    assert state["item_id"] is None
    assert item_count(db) == 0


def test_a_pasted_media_file_is_refused_and_names_the_route_that_owns_asr(
        db, store_dir):
    state = run_job(db, store_dir, "https://feeds.example.com/ep42.mp3")

    assert state["error_code"] == "no_asr_route_for_pasted_media"
    assert state["transcript_path"] == "asr"
    assert "pipeline transcribe" in state["error"]
    assert item_count(db) == 0


# ---------------------------------------------------------------------------
# 4. 403 / 429 -- the source declining.


@pytest.mark.parametrize("status", [403, 429])
def test_a_declining_source_stops_records_the_status_and_does_not_retry(
        db, store_dir, status):
    fetcher = FakeFetcher(FakeResponse(status, ""))
    state = run_job(db, store_dir, WATCH, fetcher=fetcher)

    assert state["error_code"] == "source_refused"
    assert str(status) in state["error"]
    assert state["source_status"] == status
    assert len(fetcher.calls) == 1, "a refusal must be obeyed, not retried"
    assert fetcher.calls[0][1] == 0, (
        "ContentFetcher retries 429 by default; this path must pass retries=0"
    )
    assert item_count(db) == 0


@pytest.mark.parametrize("status", [403, 429])
def test_a_declining_source_inside_the_transcript_fetch_also_stops(
        db, store_dir, status):
    fetcher = FakeFetcher(FakeResponse(200, watch_page()))
    state = run_job(db, store_dir, WATCH, fetcher=fetcher,
                    ingest=lambda *_: FakeFindings(
                        text_source=f"unavailable (watch_{status})"))

    assert state["error_code"] == "source_refused"
    assert str(status) in state["error"]
    assert item_count(db) == 0


# ---------------------------------------------------------------------------
# 5. A transcription that fails mid-way.


def test_a_partial_transcript_stores_nothing_and_says_why(db, store_dir):
    exc = asr.PartialTranscript(
        "ep.mp3: transcript ends at 4.0 min of 41.0 min audio (10% coverage)")
    state = run_job(db, store_dir, WATCH, ingest=refusing_ingest(exc))

    assert state["error_code"] == "partial_transcript"
    assert "10% coverage" in state["error"]
    assert "nothing was stored" in state["error"]
    assert state["item_id"] is None
    assert item_count(db) == 0


def test_a_missing_local_engine_never_becomes_a_remote_call(db, store_dir):
    state = run_job(db, store_dir, WATCH,
                    ingest=refusing_ingest(asr.AsrUnavailable("missing: mlx-whisper")))

    assert state["error_code"] == "asr_unavailable"
    assert "no remote one is used" in state["error"]
    assert item_count(db) == 0


# ---------------------------------------------------------------------------
# The ETA: measured, or absent.


def test_the_eta_comes_from_the_measured_caption_rate(db, store_dir):
    fetcher = FakeFetcher(FakeResponse(200, watch_page(length=1200)))
    # 20 minutes of video at the measured 286x is ~4s, not a spinner.
    pre = link_jobs.preflight(WATCH, db, fetcher_factory=factory_for(fetcher))
    eta, basis = pre.eta_s()
    assert eta == pytest.approx(1200 / link_jobs.CAPTION_RATE, abs=0.1)
    assert "286" in basis

    state = run_job(db, store_dir, WATCH, fetcher=fetcher, ingest=storing_ingest())
    assert state["done"] is True


def test_eta_is_absent_rather_than_invented_when_the_path_is_unknown(db, store_dir):
    fetcher = FakeFetcher(FakeResponse(403, ""))

    # The rule itself, not just the terminal state: with no path there is no
    # measured rate, and a number here would be a guess wearing a unit.
    pre = link_jobs.preflight(WATCH, db, fetcher_factory=factory_for(fetcher))
    assert pre.path is None
    eta, reason = pre.eta_s()
    assert eta is None
    assert "no measured rate" in reason

    state = run_job(db, store_dir, WATCH, fetcher=fetcher)
    assert state["eta_s"] is None
    assert state["transcript_path"] is None
    assert state["eta_basis"] is None
    assert state["eta_reason"], "an absent ETA must carry the reason it is absent"


def test_eta_is_absent_when_the_path_is_known_but_the_duration_is_not(db):
    """Half a measurement is not a measurement. The rate is known here and the
    duration is not, and that still yields None rather than a plausible number."""
    fetcher = FakeFetcher(FakeResponse(200, watch_page(length=None)))
    pre = link_jobs.preflight(WATCH, db, fetcher_factory=factory_for(fetcher))

    assert pre.path == "captions"
    eta, reason = pre.eta_s()
    assert eta is None
    assert "did not state its duration" in reason


def test_the_path_is_named_before_the_wait_starts(db, store_dir):
    """The UI names captions-or-ASR before the wait, so the path and its ETA
    must be on the job while the transcription is still running."""
    release = threading.Event()
    started = threading.Event()

    def _blocking_ingest(db_, url):
        started.set()
        release.wait(10.0)
        return storing_ingest()(db_, url)

    fetcher = FakeFetcher(FakeResponse(200, watch_page(length=1200)))
    jobs = link_jobs.LinkJobs(db, store_dir=store_dir, background=True,
                              preflight_fn=preflight_with(fetcher),
                              ingest_fn=_blocking_ingest)
    job_id = jobs.submit(WATCH)["job_id"]
    jobs.wait_for(job_id, timeout=10.0)   # phase one parks at the preview
    jobs.accept(job_id)                    # phase two is what has an ETA
    try:
        assert started.wait(10.0), "the job did not reach the ingester"
        mid = jobs.poll(job_id)
        assert mid["done"] is False
        assert mid["stage"] == "transcribe"
        assert mid["transcript_path"] == "captions"
        assert mid["eta_s"] == pytest.approx(4.2, abs=0.1)
        assert "286" in mid["eta_basis"]
    finally:
        release.set()
    final = jobs.wait_for(job_id, timeout=15.0)
    assert final["done"] is True
    assert final["error"] is None


# ---------------------------------------------------------------------------
# The job outlives the request.


def test_a_finished_job_keeps_answering_polls(db, store_dir):
    fetcher = FakeFetcher(FakeResponse(200, watch_page()))
    jobs = link_jobs.LinkJobs(db, store_dir=store_dir, background=False,
                              preflight_fn=preflight_with(fetcher),
                              ingest_fn=storing_ingest())
    job_id = jobs.submit(WATCH)["job_id"]
    jobs.accept(job_id)

    first = jobs.poll(job_id)
    second = jobs.poll(job_id)

    assert first["done"] is True and first["item_id"] == "link_new"
    assert second["item_id"] == first["item_id"]
    assert second["stage"] == first["stage"] and second["pct"] == 100


def test_a_job_survives_the_process_losing_its_in_memory_copy(db, store_dir):
    """State is keyed by job_id and lives server-side, not in a request."""
    fetcher = FakeFetcher(FakeResponse(200, watch_page()))
    jobs = link_jobs.LinkJobs(db, store_dir=store_dir, background=False,
                              preflight_fn=preflight_with(fetcher),
                              ingest_fn=storing_ingest())
    job_id = jobs.submit(WATCH)["job_id"]
    jobs.accept(job_id)

    reborn = link_jobs.LinkJobs(db, store_dir=store_dir, background=False)
    state = reborn.poll(job_id)

    assert state is not None
    assert state["done"] is True
    assert state["item_id"] == "link_new"
    assert reborn.poll("deadbeef") is None


def test_a_successful_ingest_shows_the_take_with_quotes_and_timestamps(db, store_dir):
    state = run_job(db, store_dir, WATCH, ingest=storing_ingest())

    assert state["done"] is True and state["error"] is None
    assert state["stage"] == "attribute" and state["pct"] == 100
    assert state["item_id"] == "link_new"
    take = state["result"]["take"]
    assert take["summary_bullets"][0].startswith("Haaland")
    call = take["transfers_in"][0]
    assert call["quote"] == "i am bringing in haaland this week"
    assert call["start_s"] == 812.0
    assert call["deep_link"] == f"https://www.youtube.com/watch?v={VID}&t=812s"
    # A pasted link belongs to nobody until the panel says so, and the reason
    # is rendered rather than filled in with a default.
    assert state["result"]["person"] is None
    assert state["result"]["person_reason"]


# ---------------------------------------------------------------------------
# The write lock.


def test_lock_contention_is_retried_with_backoff_rather_than_crashing(db, monkeypatch):
    monkeypatch.setattr(link_jobs.time, "sleep", lambda _s: None)
    attempts = {"n": 0}

    def _flaky(_db, _url):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("Could not set lock on file: fpl.duckdb")
        return FakeFindings()

    assert link_jobs.ingest_with_retry(db, WATCH, ingest=_flaky) is not None
    assert attempts["n"] == 3


def test_a_non_lock_error_is_raised_immediately_not_retried(db):
    attempts = {"n": 0}

    def _boom(_db, _url):
        attempts["n"] += 1
        raise ValueError("the transcript was empty")

    with pytest.raises(ValueError):
        link_jobs.ingest_with_retry(db, WATCH, ingest=_boom)
    assert attempts["n"] == 1


# ---------------------------------------------------------------------------
# The HTTP contract.


@pytest.fixture()
def client(db, store_dir):
    app = create_app(db)
    fetcher = FakeFetcher(FakeResponse(200, watch_page()))
    app.state.link_jobs = link_jobs.LinkJobs(
        db, store_dir=store_dir, background=False,
        preflight_fn=preflight_with(fetcher), ingest_fn=storing_ingest())
    return TestClient(app)


def test_post_returns_a_job_id_and_the_agreed_stage_list(client):
    r = client.post("/api/ingest/link", json={"url": WATCH})
    assert r.status_code == 202
    body = r.json()
    assert body["job_id"]
    # `preview` joined the ladder on 2026-08-27; it is a HALT between the one
    # page fetch and the expensive half, not another step that just happens.
    assert body["stages"] == ["fetch", "preview", "transcribe", "analyse",
                              "attribute"]


def test_get_returns_the_agreed_poll_shape(client):
    job_id = client.post("/api/ingest/link", json={"url": WATCH}).json()["job_id"]
    client.post(f"/api/ingest/link/{job_id}/accept")
    body = client.get(f"/api/ingest/link/{job_id}").json()

    assert set(body) >= {"stage", "pct", "eta_s", "done", "error", "item_id"}
    assert body["stage"] in link_jobs.STAGES
    assert body["done"] is True
    assert body["error"] is None
    assert body["item_id"] == "link_new"


def test_polling_an_unknown_job_is_a_404(client):
    assert client.get("/api/ingest/link/0123456789abcdef").status_code == 404


def test_a_url_that_is_not_a_url_is_rejected_at_the_door(client):
    r = client.post("/api/ingest/link", json={"url": "not a link"})
    assert r.status_code == 400
    assert "http" in r.json()["detail"]


def test_a_refusable_url_still_becomes_a_job_so_the_ui_reads_one_shape(client):
    """A league invite is not a client bug; it is a real answer about a real
    URL. It arrives through `error`, not through a second response shape."""
    r = client.post("/api/ingest/link",
                    json={"url": "https://fantasy.premierleague.com/leagues/"
                                 "auto-join/a6fgym"})
    assert r.status_code == 202
    body = client.get(f"/api/ingest/link/{r.json()['job_id']}").json()
    assert body["done"] is True
    assert body["item_id"] is None
    assert body["error_code"] == "not_an_episode"
