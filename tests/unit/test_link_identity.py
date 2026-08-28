"""A pasted link's identity, its gameweek, and the two ways of aborting it.

Three owner asks, one flow, and every test here is named after the thing that
was actually wrong rather than after the function it calls:

1. **"Instead of saying user-shared -- give a meaningful title."** Every pasted
   item was filed under ``creator='user-shared'`` while the watch page said
   ``"ownerChannelName":"FPL Raptor"`` a few lines further down. Because
   ``creator_board`` scopes to the panel, those items transcribed and analysed
   correctly and then appeared nowhere at all.
2. **"On aborting -- I mean look at the summary before transcribing -- if it's
   not relevant why even transcribe?"** A paste used to run straight through
   to a transcript; a 20-minute video is ~105 seconds of local ASR spent
   before anyone has seen who made it. So the job now HALTS at a preview and
   costs nothing further if the answer is no.
3. **"And a way to find out which gw the content is on."** With the label that
   says whether it was stated or guessed.

Hermetic: no network (the fetcher is a double), no transcription, no model call
(``analyze_transcript`` is replaced by one that raises the same
``AnalysisUnavailable`` a machine with no key raises), and a tmp DuckDB per
test.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.ingest.content.youtube import (
    Channel,
    channel_from_watch,
    creator_for_channel,
)
from fpl_edge.interfaces import creators as ic
from fpl_edge.platform import link_jobs
from fpl_edge.platform.app import create_app
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc

#: The fields a real watch page carries, in the shapes measured on 2026-08-27.
RAPTOR_PAGE = (
    "<html><head><title>MY FPL GW2 TRANSFER PLANS - YouTube</title></head><body>"
    '<link itemprop="name" content="FPL Raptor">'
    '{"videoDetails":{"author":"FPL Raptor",'
    '"channelId":"UC54QLWzsMifTRjNQ02z5pCw","lengthSeconds":"1200"},'
    '"microformat":{"playerMicroformatRenderer":'
    '{"ownerChannelName":"FPL Raptor","publishDate":"2026-08-20T10:00:00-07:00"}}}'
    '{"captionTracks":[{"languageCode":"en"}]}'
    "</body></html>"
)

STRANGER_PAGE = (
    "<html><head><title>Some Guy Talks FPL - YouTube</title></head><body>"
    '{"microformat":{"playerMicroformatRenderer":'
    '{"ownerChannelName":"Barry\'s Back Garden FPL",'
    '"publishDate":"2026-08-20T10:00:00-07:00"}}}'
    "</body></html>"
)

ANONYMOUS_PAGE = (
    "<html><head><title>Untitled - YouTube</title></head><body>"
    '{"publishDate":"2026-08-20T10:00:00-07:00"}'
    "</body></html>"
)

VID = "dQw4w9WgXcQ"
WATCH = f"https://www.youtube.com/watch?v={VID}"

#: Real deadlines, so the "inferred" branch is exercised against a real
#: calendar rather than a stub that always agrees.
DEADLINES = [
    ("2026-27", 1, dt.datetime(2026, 8, 14, 17, 30, tzinfo=UTC)),
    ("2026-27", 2, dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)),
    ("2026-27", 3, dt.datetime(2026, 8, 28, 17, 30, tzinfo=UTC)),
    ("2026-27", 4, dt.datetime(2026, 9, 4, 17, 30, tzinfo=UTC)),
]


# ---------------------------------------------------------------------------
# Doubles.


class FakeResponse:
    def __init__(self, status: int, body: str = "") -> None:
        self.status = status
        self.body = body
        self.error = None
        self.robots_blocked = False

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def text(self) -> str:
        return self.body


class FakeFetcher:
    """Records every request, so the politeness claim is measurable."""

    def __init__(self, page: str) -> None:
        self.page = page
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(200, self.page)

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        pass


class StubCall:
    """One structured call out of an analysis; only ``gameweek`` matters here."""

    def __init__(self, gameweek: int | None) -> None:
        self.gameweek = gameweek


class StubAnalysis:
    def __init__(self, gameweeks: list[int | None]) -> None:
        self.transfers_in = [StubCall(gw) for gw in gameweeks]
        self.transfers_out: list[StubCall] = []
        self.captaincy: list[StubCall] = []
        self.differentials: list[StubCall] = []
        self.chip_advice: list[StubCall] = []


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)
    from fpl_edge.ingest.content.store import ContentStore

    ContentStore(wh).migrate()
    wh.close()
    return path


@pytest.fixture()
def hermetic(monkeypatch):
    """Everything ``ingest_link`` reaches for that is not the point of the test.

    ``analyze_transcript`` raises the same exception an un-keyed machine
    raises, which keeps the NO-ANTHROPIC-TOKENS rule true by construction:
    a test that quietly acquired a key would still pass, and a test that
    tried to call one would fail here.
    """
    from fpl_edge.ingest.content import analyze, claims, pipeline
    from fpl_edge.ingest.content import calendar as cal_mod

    calendar = GameweekCalendar(DEADLINES)
    monkeypatch.setattr(pipeline, "build_resolver", lambda wh: object())
    monkeypatch.setattr(pipeline, "load_calendar", lambda wh: (calendar, None))
    monkeypatch.setattr(cal_mod, "load_calendar", lambda wh: (calendar, None))
    monkeypatch.setattr(
        analyze, "analyze_transcript",
        lambda **_: (_ for _ in ()).throw(
            analyze.AnalysisUnavailable("no local model configured in tests")),
    )
    # A real Claim row, not an empty list: "a discard deletes nothing" is only
    # a measurable statement if there is something deletable in the table.
    def _one_claim(item, *_a, **_k):
        from fpl_edge.ingest.content.claims import Claim
        from fpl_edge.ingest.content.models import Action

        return [Claim(
            claim_id=f"c_{item.item_id}", item_id=item.item_id,
            creator=item.creator, source_key=item.source_key,
            player_code=1, player_name="Haaland", surface_form="haaland",
            action=Action("buy"), season="2026-27", gameweek=2,
            confidence=0.8, rationale="test", source_url=item.url,
            published_at=item.published_at, gw_inferred=False,
            extractor="test")]

    monkeypatch.setattr(claims, "extract_from_item", _one_claim)
    monkeypatch.setattr(ic, "_commit_findings",
                        lambda *_a, **_k: "not committed in tests")
    monkeypatch.setattr(ic, "_timed_transcript", lambda vid: [(0.0, "hello")])
    return calendar


def ingest(db, page: str, *, monkeypatch, url: str = WATCH,
           transcript: list[str] | None = None) -> tuple[object, FakeFetcher]:
    """Run the real ``ingest_link`` against a faked source and a real warehouse."""
    from fpl_edge.ingest.content import fetch as fetch_mod
    from fpl_edge.ingest.content import youtube as yt_mod

    fetcher = FakeFetcher(page)
    monkeypatch.setattr(fetch_mod, "ContentFetcher", lambda *a, **k: fetcher)
    monkeypatch.setattr(
        yt_mod, "fetch_transcript",
        lambda *_a, **_k: (transcript if transcript is not None
                           else ["i am bringing in haaland"], "innertube"))
    wh = Warehouse(db)
    try:
        return ic.ingest_link(wh, url), fetcher
    finally:
        wh.close()


def item_row(db, item_id: str) -> dict:
    wh = Warehouse(db, read_only=True)
    try:
        return wh.sql("SELECT * FROM content_item WHERE item_id = ?",
                      [item_id]).to_dict("records")[0]
    finally:
        wh.close()


# ---------------------------------------------------------------------------
# 1. Who published it.


def test_the_channel_named_on_the_watch_page_is_parsed_at_all():
    """The field was sitting there unread. This is the whole of ask 1's first half."""
    channel = channel_from_watch(RAPTOR_PAGE)
    assert channel.name == "FPL Raptor"
    assert channel.channel_id == "UC54QLWzsMifTRjNQ02z5pCw"
    assert channel.basis == "ownerChannelName"


def test_a_panel_creators_video_is_filed_under_that_creator_not_user_shared(
    db, hermetic, monkeypatch
):
    """The board scopes to the panel, so `user-shared` meant invisible."""
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)

    assert findings.creator == "FPL Raptor"
    assert findings.creator != "user-shared"
    assert findings.tracked is True
    assert findings.creator_basis == "channel_id"
    assert item_row(db, findings.item_id)["creator"] == "FPL Raptor"


def test_an_unregistered_channel_keeps_its_real_name_and_is_marked_untracked(
    db, hermetic, monkeypatch
):
    """The reader must see WHO said it even when the panel excludes it."""
    findings, _ = ingest(db, STRANGER_PAGE, monkeypatch=monkeypatch)

    assert findings.creator == "Barry's Back Garden FPL"
    assert findings.channel == "Barry's Back Garden FPL"
    assert findings.tracked is False
    assert findings.creator_basis == "unregistered_channel"
    assert "not tracked" in findings.render()


def test_a_page_that_names_no_channel_gets_a_generic_label_and_a_reason(
    db, hermetic, monkeypatch
):
    """No fabricated names. An unresolvable source is honest about being one."""
    findings, _ = ingest(db, ANONYMOUS_PAGE, monkeypatch=monkeypatch)

    assert findings.creator == ic.UNRESOLVED_CREATOR
    assert findings.channel is None
    assert findings.tracked is False
    assert "did not state a channel" in findings.creator_reason


def test_a_near_miss_channel_name_is_not_rounded_to_a_registered_creator():
    """'FPL Harry Clips' is not FPL Harry. Exact after folding, or nothing."""
    match = creator_for_channel(Channel("FPL Harry Clips", None, "author"))
    assert match.creator is None
    assert match.basis == "unregistered_channel"


def test_reading_the_channel_costs_the_source_no_extra_request(
    db, hermetic, monkeypatch
):
    """The politeness terms in docs/data_sources.md 7A depend on this.

    The identity is parsed out of a response that was ALREADY being fetched
    for the title, so the request count is unchanged by the whole of ask 1.
    """
    _, fetcher = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)

    assert fetcher.calls == [WATCH], (
        f"the watch page must be fetched exactly once; got {fetcher.calls}")


def test_an_article_on_a_registered_hosts_domain_is_filed_under_that_show(
    db, hermetic, monkeypatch
):
    """The podcast/blog half: a registered feed states its show."""
    from fpl_edge.ingest.content import loaders

    monkeypatch.setattr(loaders, "_fetch_article", lambda *_a, **_k: "x " * 500)
    findings, _ = ingest(
        db, "<html></html>", monkeypatch=monkeypatch,
        url="https://www.fantasyfootballscout.co.uk/2026/08/20/gw3-preview/")

    assert findings.creator == "Fantasy Football Scout"
    assert findings.creator_basis == "host_registry"


# ---------------------------------------------------------------------------
# 3. Which gameweek, and whether it is a guess.


def test_a_lookalike_host_is_not_rounded_to_a_registered_source(
    db, hermetic, monkeypatch
):
    """Exact host, or nothing. A host that merely resembles one is not it."""
    from fpl_edge.ingest.content import loaders

    monkeypatch.setattr(loaders, "_fetch_article", lambda *_a, **_k: "x " * 500)
    findings, _ = ingest(
        db, "<html></html>", monkeypatch=monkeypatch,
        url="https://fantasyfootballscout.co.uk.example.net/gw3-preview/")

    assert findings.creator == ic.UNRESOLVED_CREATOR
    assert findings.creator_basis == "unregistered_host"
    assert findings.tracked is False
    assert "no registered source publishes at" in findings.creator_reason


def test_a_gameweek_stated_in_the_title_beats_the_publish_date_inference():
    """'Locked & Loaded - Gameweek 1 Pod' published in week 3 is about GW1."""
    resolution = ic.resolve_gameweek(
        calendar=GameweekCalendar(DEADLINES),
        published_at=dt.datetime(2026, 8, 25, tzinfo=UTC),
        title="Locked & Loaded - Gameweek 1 Pod | The FPL Wire")

    assert resolution.gameweek == 1
    assert resolution.basis == "stated"
    assert resolution.inferred == 3, "the inference is kept, not discarded"
    assert resolution.is_guess is False
    assert "stated" in resolution.label


def test_a_gameweek_the_speaker_named_is_taken_from_the_analysed_calls():
    resolution = ic.resolve_gameweek(
        calendar=GameweekCalendar(DEADLINES),
        published_at=dt.datetime(2026, 8, 25, tzinfo=UTC),
        title="TEAM REVEAL", analysis=StubAnalysis([4, 4, None]))

    assert resolution.gameweek == 4
    assert resolution.basis == "stated"
    assert resolution.stated == (4,)


def test_an_inferred_gameweek_is_labelled_as_a_guess():
    """An inference is a guess and must never be shown as a fact."""
    resolution = ic.resolve_gameweek(
        calendar=GameweekCalendar(DEADLINES),
        published_at=dt.datetime(2026, 8, 25, tzinfo=UTC),
        title="MY TEAM FOR THE WEEKEND")

    assert resolution.gameweek == 3
    assert resolution.basis == "inferred"
    assert resolution.is_guess is True
    assert "guess" in resolution.label
    assert "guess" in resolution.reason


def test_no_calendar_and_no_stated_week_yields_no_gameweek_rather_than_one():
    resolution = ic.resolve_gameweek(
        calendar=GameweekCalendar([]),
        published_at=dt.datetime(2026, 8, 25, tzinfo=UTC), title="TEAM REVEAL")

    assert resolution.gameweek is None
    assert resolution.basis == "unknown"
    assert "none is invented" in resolution.reason


def test_the_publish_date_used_for_the_inference_is_the_videos_not_the_pastes(
    db, hermetic, monkeypatch
):
    """Stamping an old video with today made the inference answer about today.

    The watch page states ``publishDate``; before this it was thrown away and
    ``published_at`` was the moment of the paste, so an eight-week-old episode
    got the upcoming deadline.
    """
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)

    assert findings.published_at == dt.datetime(2026, 8, 20, 17, 0, tzinfo=UTC)
    assert findings.published_basis == "the video's own datePublished"
    # GW2's deadline is the first one after 20 Aug; the title says GW2 too.
    assert findings.gameweek.gameweek == 2


def test_the_job_reports_the_gameweek_and_how_it_was_resolved(db, hermetic,
                                                              monkeypatch):
    """Ask 3 as the UI sees it: the value AND the basis, on the poll payload."""
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    take = link_jobs.build_take(db, [findings.item_id], WATCH)

    assert take["gameweek"] == 2
    assert take["gameweek_basis"] == "stated"
    assert take["creator"] == "FPL Raptor"
    assert take["tracked"] is True


# ---------------------------------------------------------------------------
# 2b. Discarding something already ingested.


def test_a_discard_hides_the_item_and_deletes_absolutely_nothing(db, hermetic,
                                                                 monkeypatch):
    """content_claim is immutable: a claim is an utterance that was made."""
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    before = _table_counts(db)

    link_jobs.discard_item(db, findings.item_id, reason="not about FPL")

    assert _table_counts(db) == before, "a discard must not delete a single row"
    assert link_jobs.build_take(db, [findings.item_id], WATCH)["discarded"] is True


def test_a_discarded_item_is_invisible_to_the_shared_filter(db, hermetic,
                                                            monkeypatch):
    """The filter every honest read path is meant to apply."""
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    link_jobs.discard_item(db, findings.item_id, reason="mis-paste")

    wh = Warehouse(db, read_only=True)
    try:
        assert findings.item_id in ic.discarded_item_ids(wh)
        items = wh.sql("SELECT item_id FROM content_item")
        assert ic.drop_discarded(items, wh).empty
    finally:
        wh.close()


def test_a_discard_is_reversible_because_it_destroyed_nothing(db, hermetic,
                                                              monkeypatch):
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    link_jobs.discard_item(db, findings.item_id, reason="oops")
    link_jobs.restore_item(db, findings.item_id, reason="not an oops")

    assert link_jobs.build_take(db, [findings.item_id], WATCH)["discarded"] is False


def test_discarding_an_item_that_does_not_exist_is_refused_not_shrugged_off(db):
    """Reporting success for content that is still on screen is the bug."""
    with pytest.raises(KeyError):
        link_jobs.discard_item(db, "link_nope", reason="x")


def test_re_pasting_a_discarded_link_does_not_silently_un_discard_it(
    db, hermetic, monkeypatch
):
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    link_jobs.discard_item(db, findings.item_id, reason="not relevant")
    ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)

    assert link_jobs.build_take(db, [findings.item_id], WATCH)["discarded"] is True


# ---------------------------------------------------------------------------
# 3b. Correcting the gameweek by hand.


def test_a_hand_correction_is_recorded_as_one_and_keeps_what_it_replaced(
    db, hermetic, monkeypatch
):
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    row = link_jobs.correct_gameweek(db, findings.item_id, 5, note="he says GW5")

    assert row["gameweek"] == 5
    assert row["gw_basis"] == "corrected"
    assert row["gw_corrected_from"] == 2
    assert row["gw_corrected_utc"] is not None
    assert row["gw_inferred"] == 2, "the inference is kept for comparison"


def test_a_correction_does_not_rewrite_the_stored_claims(db, hermetic,
                                                         monkeypatch):
    """content_claim is immutable; the correction is an item-level annotation."""
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    row = link_jobs.correct_gameweek(db, findings.item_id, 5)

    assert "claims stored for this item keep the gameweek" in row["gw_reason"]


def test_re_running_the_ingest_does_not_overwrite_a_hand_correction(
    db, hermetic, monkeypatch
):
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    link_jobs.correct_gameweek(db, findings.item_id, 5)
    ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)

    take = link_jobs.build_take(db, [findings.item_id], WATCH)
    assert take["gameweek"] == 5
    assert take["gameweek_basis"] == "corrected"


def test_a_gameweek_outside_the_season_is_refused(db, hermetic, monkeypatch):
    findings, _ = ingest(db, RAPTOR_PAGE, monkeypatch=monkeypatch)
    with pytest.raises(ValueError):
        link_jobs.correct_gameweek(db, findings.item_id, 99)


def _table_counts(db) -> dict[str, int]:
    wh = Warehouse(db, read_only=True)
    try:
        return {
            t: int(wh.sql(f"SELECT count(*) AS n FROM {t}").iloc[0]["n"])
            for t in ("content_item", "content_claim", "transcript_segment")
        }
    finally:
        wh.close()




# ---------------------------------------------------------------------------
# 2. The preview halt: decide BEFORE anything is transcribed.
#
# The owner: "look at the summary before transcribing -- if it's not relevant
# why even transcribe?". A 20-minute video is ~105 seconds of local ASR, and
# these tests are about not spending it.


def _jobs(db, store_dir, *, ingest_fn, page: str | None = None):
    from tests.unit.test_link_jobs import FakeFetcher as JobFetcher
    from tests.unit.test_link_jobs import FakeResponse as JobResponse
    from tests.unit.test_link_jobs import preflight_with

    fetcher = JobFetcher(JobResponse(200, page or RAPTOR_PAGE))
    jobs = link_jobs.LinkJobs(db, store_dir=store_dir, background=False,
                              preflight_fn=preflight_with(fetcher),
                              ingest_fn=ingest_fn)
    jobs.test_fetcher = fetcher
    return jobs


def _must_not_run(_db, _url):  # pragma: no cover - reaching it IS the failure
    raise AssertionError(
        "the transcriber ran without a decision; the halt exists so that "
        "cannot happen")


def test_a_paste_stops_for_a_decision_instead_of_transcribing(db, tmp_path):
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    state = jobs.poll(jobs.submit(WATCH)["job_id"])

    assert state["awaiting_decision"] is True
    assert state["done"] is False
    assert state["stage"] == "preview"
    assert state["item_id"] is None


def test_the_preview_carries_everything_needed_to_judge_relevance(db, tmp_path):
    """The payload the UI renders at the halt. Every field from ONE fetch."""
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    preview = jobs.poll(jobs.submit(WATCH)["job_id"])["preview"]

    # who
    assert preview["creator"] == "FPL Raptor"
    assert preview["channel"] == "FPL Raptor"
    assert preview["creator_basis"] == "channel_id"
    assert preview["creator_reason"]
    assert preview["tracked"] is True
    # what, and when
    assert preview["title"] == "MY FPL GW2 TRANSFER PLANS"
    assert preview["published_at"].startswith("2026-08-20T17:00")
    assert preview["published_basis"] == "the video's own datePublished"
    # which gameweek, labelled
    assert preview["gameweek"]["gameweek"] == 2
    assert preview["gameweek"]["basis"] == "stated"
    assert preview["gameweek"]["is_guess"] is False
    # what saying yes would cost
    assert preview["media_seconds"] == 1200.0
    assert preview["transcript_path"] == "captions"
    assert preview["eta_s"] == pytest.approx(4.2, abs=0.1)
    assert "286" in preview["eta_basis"]


def test_the_preview_costs_the_source_exactly_one_request(db, tmp_path):
    """The halt must not be paid for with a second hit on the source."""
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    jobs.submit(WATCH)

    assert [url for url, _ in jobs.test_fetcher.calls] == [WATCH]


def test_declining_leaves_absolutely_nothing_in_the_warehouse(db, tmp_path):
    """Not 'nothing after cleanup' -- nothing, because nothing was written."""
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    job_id = jobs.submit(WATCH)["job_id"]
    before = _all_counts(db)

    state = jobs.decline(job_id, reason="this is about a different sport")

    assert state["declined"] is True
    assert state["done"] is True
    assert state["error_code"] == "declined"
    assert state["item_id"] is None
    assert _all_counts(db) == before == {"content_item": 0, "content_claim": 0,
                                         "transcript_segment": 0,
                                         "content_analysis": 0}
    assert "different sport" in state["error"]


def test_accepting_runs_the_transcription_that_declining_would_have_skipped(
    db, tmp_path
):
    from tests.unit.test_link_jobs import storing_ingest

    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=storing_ingest())
    job_id = jobs.submit(WATCH)["job_id"]
    assert jobs.poll(job_id)["awaiting_decision"] is True

    jobs.accept(job_id)
    state = jobs.poll(job_id)

    assert state["done"] is True
    assert state["error"] is None
    assert state["item_id"] == "link_new"
    assert state["awaiting_decision"] is False
    # Phase two refines the preview's answers; it never blanks them. An
    # ingester that reports no creator leaves the one the preview resolved.
    assert state["creator"] == "FPL Raptor"
    assert state["gameweek"]["gameweek"] == 2


def test_an_undecided_job_expires_rather_than_waiting_forever(db, tmp_path,
                                                              monkeypatch):
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    job_id = jobs.submit(WATCH)["job_id"]
    job = jobs._jobs[job_id]
    job.preview_expires_utc = dt.datetime.now(UTC) - dt.timedelta(seconds=1)

    state = jobs.poll(job_id)

    assert state["done"] is True
    assert state["error_code"] == "preview_expired"
    assert state["awaiting_decision"] is False
    assert _all_counts(db)["content_item"] == 0


def test_an_expired_preview_cannot_then_be_accepted(db, tmp_path):
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    job_id = jobs.submit(WATCH)["job_id"]
    jobs._jobs[job_id].preview_expires_utc = (dt.datetime.now(UTC)
                                              - dt.timedelta(seconds=1))

    with pytest.raises(link_jobs.NotAwaitingDecision):
        jobs.accept(job_id)


def test_a_refused_url_never_reaches_the_halt_because_there_is_no_decision(
    db, tmp_path
):
    """A league invite is not a relevance judgement; it is not ingestable."""
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    state = jobs.poll(jobs.submit(
        "https://fantasy.premierleague.com/leagues/auto-join/a6fgym")["job_id"])

    assert state["awaiting_decision"] is False
    assert state["done"] is True
    assert state["error_code"] == "not_an_episode"


def test_aborting_a_parked_job_is_a_decline_not_a_cancel(db, tmp_path):
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    job_id = jobs.submit(WATCH)["job_id"]

    state = jobs.cancel(job_id)

    assert state["declined"] is True
    assert state["cancelled"] is False


def _all_counts(db) -> dict[str, int]:
    wh = Warehouse(db, read_only=True)
    try:
        return {
            t: int(wh.sql(f"SELECT count(*) AS n FROM {t}").iloc[0]["n"])
            for t in ("content_item", "content_claim", "transcript_segment",
                      "content_analysis")
        }
    finally:
        wh.close()


# ---------------------------------------------------------------------------
# 2b. Cancelling a phase-two job that IS running. The secondary control.


def test_a_cancel_that_lands_after_the_write_hides_what_was_written(db, tmp_path):
    """`ingest_link` has no interruption point, so the item is discarded.

    Nothing is deleted -- that is what the immutability rule requires -- and
    nothing is visible either, which is what a cancel promises.
    """
    from tests.unit.test_link_jobs import SEGMENTS, seed_item

    holder: dict[str, object] = {}

    def ingest_fn(_db, url):
        seed_item(_db, item_id="link_new", url=url, segments=SEGMENTS)
        holder["job"].cancel.set()          # the owner cancels mid-call
        return type("F", (), {"text_source": "transcript", "title": "t",
                              "analysis_note": ""})()

    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=ingest_fn)
    job_id = jobs.submit(WATCH)["job_id"]
    holder["job"] = jobs._jobs[job_id]
    jobs.accept(job_id)
    state = jobs.poll(job_id)

    assert state["cancelled"] is True
    assert state["cancelled_after_write"] is True
    assert state["discarded_item_id"] == "link_new"
    assert state["item_id"] is None
    assert _all_counts(db)["content_item"] == 1, "the archive keeps what it read"
    assert link_jobs.build_take(db, ["link_new"], WATCH)["discarded"] is True
    assert link_jobs.build_take(db, ["link_new"], WATCH)["take"] is None


def test_cancelling_a_finished_job_is_a_conflict_not_a_lie(db, tmp_path):
    """Saying 'cancelled' about a completed ingest would hide a stored item."""
    from tests.unit.test_link_jobs import storing_ingest

    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=storing_ingest())
    job_id = jobs.submit(WATCH)["job_id"]
    jobs.accept(job_id)

    with pytest.raises(link_jobs.JobAlreadyFinished):
        jobs.cancel(job_id)


def test_cancelling_an_unknown_job_is_a_404_not_a_success(db, tmp_path):
    jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    with pytest.raises(link_jobs.UnknownJob):
        jobs.cancel("0123456789abcdef")


# ---------------------------------------------------------------------------
# The HTTP surface the UI is wired to.


def test_the_http_surface_gates_transcription_behind_an_explicit_accept(db,
                                                                        tmp_path):
    from tests.unit.test_link_jobs import storing_ingest

    app = create_app(db)
    app.state.link_jobs = _jobs(db, tmp_path / "jobs",
                                ingest_fn=storing_ingest())
    client = TestClient(app)

    job_id = client.post("/api/ingest/link", json={"url": WATCH}).json()["job_id"]
    parked = client.get(f"/api/ingest/link/{job_id}").json()
    assert parked["awaiting_decision"] is True
    assert parked["preview"]["creator"] == "FPL Raptor"
    assert parked["item_id"] is None

    accepted = client.post(f"/api/ingest/link/{job_id}/accept")
    assert accepted.status_code == 202
    assert client.get(f"/api/ingest/link/{job_id}").json()["item_id"] == "link_new"

    # Accepting twice is a conflict, not a second transcription.
    assert client.post(f"/api/ingest/link/{job_id}/accept").status_code == 409
    assert client.post("/api/ingest/link/0123456789abcdef/accept").status_code == 404


def test_the_http_surface_declines_without_storing_anything(db, tmp_path):
    app = create_app(db)
    app.state.link_jobs = _jobs(db, tmp_path / "jobs", ingest_fn=_must_not_run)
    client = TestClient(app)

    job_id = client.post("/api/ingest/link", json={"url": WATCH}).json()["job_id"]
    declined = client.post(f"/api/ingest/link/{job_id}/decline",
                           json={"reason": "not FPL"})

    assert declined.status_code == 200
    assert declined.json()["declined"] is True
    assert _all_counts(db)["content_item"] == 0
    assert client.delete("/api/ingest/link/0123456789abcdef").status_code == 404


def test_the_http_surface_exposes_discard_restore_and_gameweek(db, tmp_path):
    from tests.unit.test_link_jobs import storing_ingest

    app = create_app(db)
    app.state.link_jobs = _jobs(db, tmp_path / "jobs",
                                ingest_fn=storing_ingest())
    client = TestClient(app)
    job_id = client.post("/api/ingest/link", json={"url": WATCH}).json()["job_id"]
    client.post(f"/api/ingest/link/{job_id}/accept")

    discard = client.post("/api/content/items/link_new/discard",
                          json={"reason": "not about FPL"})
    assert discard.status_code == 200
    assert discard.json()["discarded"] is True

    restore = client.post("/api/content/items/link_new/restore", json={})
    assert restore.json()["discarded"] is False

    gw = client.post("/api/content/items/link_new/gameweek",
                     json={"gameweek": 7, "note": "he says GW7"})
    assert gw.status_code == 200
    assert gw.json()["gw_basis"] == "corrected"

    assert client.post("/api/content/items/link_new/gameweek",
                       json={"gameweek": 99}).status_code == 400
    assert client.post("/api/content/items/nope/discard",
                       json={}).status_code == 404


def test_a_shared_platform_host_names_nobody():
    """youtube.com is a platform, not a publisher.

    Matching a creator on the host alone attributed a bare
    "https://www.youtube.com/" -- carrying no video id at all -- to Let's Talk
    FPL, with a confident basis string, purely because that channel sits first
    in the registry. A host identifies a publisher only when exactly one
    registered source lives there; thirteen channels share this one.
    """
    from fpl_edge.interfaces.creators import _creator_from_host

    for url in ("https://www.youtube.com/",
                "https://www.youtube.com/watch?v=",
                "https://youtube.com/feed/subscriptions"):
        creator, basis, reason = _creator_from_host(url)
        assert creator is None, f"{url} named {creator!r} from the host alone"
        assert basis == "shared_host"
        assert "does not say who published" in reason


def test_a_host_belonging_to_one_publisher_still_identifies_it():
    """The rule must not over-correct: a blog's own domain IS its identity."""
    from fpl_edge.interfaces.creators import _creator_from_host

    creator, basis, _ = _creator_from_host(
        "https://www.fantasyfootballscout.co.uk/2026/08/27/some-article/")
    assert creator == "Fantasy Football Scout"
    assert basis == "host_registry"
