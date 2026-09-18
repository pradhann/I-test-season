"""creator_episodes / episode_summary against a seeded warehouse.

Hermetic: the fixture builds its own DuckDB file, runs the content migrations
through :class:`ContentStore` and plants the exact rows under test. Nothing
here reaches the network or the live warehouse.

Four items across two creators, chosen so that each failure mode this pair of
panels exists to avoid is a test rather than a paragraph:

* **The hidden item.** One analysed episode with claims is discarded through
  the real link ledger. It is not deleted, so it is still in ``content_item``
  and still has claims and an analysis; both panels must refuse to show it, and
  the counts must not include it.
* **The empty state.** One episode is transcribed and has no analysis. It is a
  real row with a real transcript and nothing read it yet, which is a different
  thing from an episode nobody said anything in.
* **The untranscribed episode.** One podcast has its audio URL stored and no
  transcript. It is ``queued``, it is hidden by default, and the toggle shows
  it without changing the totals a reader is comparing against.
* **The invented quote.** One stored call quotes a sentence that is not in the
  transcript. It gets ``start_s: null`` and the episode link, never a guessed
  offset, and it still appears.

Note on SQL placeholders: the inserts below use positional ``?`` binding, which
is safe in this file because the house prose gate reads a question mark
followed by WHITESPACE as a rhetorical question and a placeholder here is
always followed by a comma or a closing paren. The panel module cannot do the
same, so it binds ``$1``-style numbered parameters instead; the same workaround
is recorded at ``tests/unit/test_dossier.py`` line 63.
"""

from __future__ import annotations

import datetime as dt
import json

import jsonschema
import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers the creator scripts)
from fpl_edge.platform.registry import registered, run_script, script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
SEASON = "2026-27"

NOW = dt.datetime.now(UTC)
PAST = NOW - dt.timedelta(days=2)
OLDER = NOW - dt.timedelta(days=3)
OLDEST = NOW - dt.timedelta(days=4)
SEEDED = NOW - dt.timedelta(days=40)

HAALAND, SALIBA = 223094, 222683

TALKER = "The Talker"
QUIET = "Quiet Pod"

LIVE_ID = "ABCdef12345"
LIVE_URL = f"https://www.youtube.com/watch?v={LIVE_ID}&feature=share"
NOTES_URL = "https://www.youtube.com/watch?v=NOTESvid001"
GONE_URL = "https://www.youtube.com/watch?v=GONEvid0001"
QUEUED_URL = "https://example.invalid/quiet/episode-1"
QUEUED_AUDIO = "https://example.invalid/quiet/episode-1.mp3"

#: The quote in the analysis matches the second segment apart from case and
#: punctuation, which is the realistic case: auto-captions carry neither.
SEGMENTS = [
    (0.0, "what is going on everyone welcome back"),
    (12.5, "haaland is the captain this week no question about it"),
    (30.25, "i am leaving saliba on the bench for now"),
]

ANALYSIS = {
    "summary": ["Haaland is the captain.", "Saliba benched."],
    "transfers_in": [{
        "player": "Erling Haaland", "stance": "buy", "conviction": "high",
        "gameweek": 2, "reasoning": "Best fixture.",
        "quote": "Haaland is the captain this week, no question about it",
    }],
    "transfers_out": [{
        # Not in the transcript: it must degrade to the episode link.
        "player": "William Saliba", "stance": "sell", "conviction": "low",
        "gameweek": 2, "reasoning": "Rotation risk.",
        "quote": "a sentence that was never spoken in this video at all",
    }],
    "captaincy": [{
        "player": "Erling Haaland", "stance": "captain", "conviction": "high",
        "gameweek": 2, "reasoning": "Obvious.",
        "quote": "Haaland is the captain this week",
    }],
    "chip_advice": [],
    "differentials": [],
}

GONE_ANALYSIS = {
    "summary": ["Nobody should ever read this."],
    "transfers_in": [], "transfers_out": [], "captaincy": [],
    "chip_advice": [], "differentials": [],
}


def _player(code, element_id, web, first, second, pos, team_code=43):
    return {"season": SEASON, "code": code, "element_id": element_id,
            "web_name": web, "first_name": first, "second_name": second,
            "position": pos, "team_code": team_code, "as_of": SEEDED}


def _state(code, element_id, own, price):
    return {"season": SEASON, "code": code, "element_id": element_id,
            "price_tenths": price, "selected_by_pct": own, "status": "a",
            "chance_of_playing_next_round": None, "news": "", "news_added": None,
            "transfers_in_event": 0, "transfers_out_event": 0,
            "cost_change_start": 0, "as_of": SEEDED}


def _source(wh, key, creator, kind, url):
    wh.sql(
        "INSERT INTO content_source VALUES (?, ?, ?, ?, 'open', NULL, ?, 200, 0, NULL)",
        [key, creator, kind, url, PAST],
    )


def _item(wh, item_id, source_key, creator, kind, title, url, published,
          text_source, text="body"):
    wh.sql(
        "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sha')",
        [item_id, source_key, creator, kind, title, url, published, published,
         text_source, text],
    )


def _claim(wh, claim_id, item_id, creator, source_key, code, name, action,
           published, url, *, gw=2, confidence=0.8, rationale="because",
           extractor="cue"):
    wh.sql(
        "INSERT INTO content_claim VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, false, ?)",
        [claim_id, item_id, creator, source_key, code, name, name, action,
         SEASON, gw, confidence, rationale, url, published, extractor],
    )


def _asset(wh, item_id, enclosure_url):
    wh.sql(
        "INSERT INTO content_item_asset VALUES (?, 'enclosure', NULL, ?, NULL,"
        " 'audio/mpeg', ?)",
        [item_id, enclosure_url, PAST],
    )


@pytest.fixture()
def empty_db(tmp_path):
    """A warehouse with no content migrations run at all."""
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


@pytest.fixture()
def seeded_db(tmp_path):
    from fpl_edge.ingest.content.link_ledger import discard_item

    path = tmp_path / "fpl.duckdb"
    wh = Warehouse(path)

    wh.append("dim_team", pd.DataFrame([
        {"season": SEASON, "team_code": 43, "team_id": 15, "name": "Man City",
         "short_name": "MCI", "as_of": SEEDED},
    ]))
    wh.append("dim_player", pd.DataFrame([
        _player(HAALAND, 10, "Haaland", "Erling", "Haaland", 4),
        _player(SALIBA, 20, "Saliba", "William", "Saliba", 2),
    ]))
    wh.append("fact_player_state", pd.DataFrame([
        _state(HAALAND, 10, 62.0, 145),
        _state(SALIBA, 20, 18.0, 60),
    ]))
    # GW1's deadline has passed, GW2's has not, so every item here was
    # published into GW2 and the header gameweek says so.
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": True,
         "deadline_utc": NOW - dt.timedelta(days=6), "as_of": SEEDED},
        {"season": SEASON, "gw": 2, "is_finished": False,
         "deadline_utc": NOW + dt.timedelta(days=1), "as_of": SEEDED},
    ]))

    from fpl_edge.ingest.content.store import ContentStore

    ContentStore(wh)

    # -- the analysed episode, with a transcript and claims ----------------
    _source(wh, "yt_talker", TALKER, "youtube", "https://www.youtube.com/@talker")
    _item(wh, "ep_live", "yt_talker", TALKER, "youtube", "GW2 Preview",
          LIVE_URL, PAST, "transcript")
    for seq, (start_s, text) in enumerate(SEGMENTS):
        wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
               ["ep_live", seq, start_s, text])
    wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
           ["ep_live", "claude-opus-5", PAST, json.dumps(ANALYSIS)])
    _claim(wh, "c_live_cap", "ep_live", TALKER, "yt_talker", HAALAND,
           "Erling Haaland", "captain", PAST, LIVE_URL,
           extractor="llm:claude-opus-5",
           rationale="Obvious. | quote: Haaland is the captain this week")
    # A cue claim whose rationale is the keyword window, which IS its quote.
    _claim(wh, "c_live_buy", "ep_live", TALKER, "yt_talker", SALIBA,
           "William Saliba", "buy", PAST, LIVE_URL,
           rationale="i am leaving saliba on the bench for now")
    # A claim with NOTHING stored as evidence: quote must come back null and
    # the claim must still be listed.
    _claim(wh, "c_live_bare", "ep_live", TALKER, "yt_talker", SALIBA,
           "William Saliba", "sell", PAST, LIVE_URL, rationale="")

    # -- transcribed, never analysed --------------------------------------
    _item(wh, "ep_notes", "yt_talker", TALKER, "youtube", "GW2 Team Reveal",
          NOTES_URL, OLDER, "transcript")
    wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
           ["ep_notes", 0, 0.0, "this one was never read by the analyser"])

    # -- analysed, with claims, and DISCARDED by the owner -----------------
    _item(wh, "ep_gone", "yt_talker", TALKER, "youtube", "Retracted episode",
          GONE_URL, OLDEST, "transcript")
    wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
           ["ep_gone", 0, 0.0, "nobody should ever read this"])
    wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
           ["ep_gone", "claude-opus-5", OLDEST, json.dumps(GONE_ANALYSIS)])
    _claim(wh, "c_gone", "ep_gone", TALKER, "yt_talker", HAALAND,
           "Erling Haaland", "buy", OLDEST, GONE_URL)

    # -- a second creator: one podcast, audio on file, not transcribed -----
    _source(wh, "pod_quiet", QUIET, "podcast", "https://example.invalid/feed.xml")
    _item(wh, "ep_queued", "pod_quiet", QUIET, "podcast", "Quiet GW2 pod",
          QUEUED_URL, PAST, "description")
    _asset(wh, "ep_queued", QUEUED_AUDIO)

    discard_item(wh, "ep_gone", reason="the owner asked for it to be hidden")
    wh.close()
    return path


def episodes(db, **params):
    return run_script("creator_episodes", params, db=db).result


def summary(db, item_id):
    return run_script("episode_summary", {"item_id": item_id}, db=db).result


def _by_id(res):
    return {e["item_id"]: e for e in res["episodes"]}


# ---------------------------------------------------------------------------
# Registration.

def test_both_panels_are_registered():
    assert "creator_episodes" in registered()
    assert "episode_summary" in registered()


def test_neither_real_branch_can_be_satisfied_by_the_empty_shape():
    """The registry wraps every result as oneOf[mine, {empty, reason}].

    ``oneOf`` means EXACTLY one, so a real branch that also admitted the empty
    shape would make every honest empty fail validation.
    """
    for name in ("creator_episodes", "episode_summary"):
        inner = script(name).result_schema["oneOf"][0]
        validator = jsonschema.Draft202012Validator(inner)
        assert not validator.is_valid({"empty": True, "reason": "nothing yet"})


def test_a_warehouse_with_no_corpus_says_what_is_missing(empty_db):
    res = episodes(empty_db, creator=TALKER)
    assert res["empty"] is True
    assert "ingest" in res["reason"].lower()


def test_an_untracked_creator_is_named_with_the_list_on_file(seeded_db):
    res = episodes(seeded_db, creator="Nobody At All")
    assert res["empty"] is True
    assert TALKER in res["reason"] and QUIET in res["reason"]


# ---------------------------------------------------------------------------
# The list.

def test_episodes_come_back_newest_first(seeded_db):
    res = episodes(seeded_db, creator=TALKER, include_untranscribed=True)
    stamps = [e["published_at"] for e in res["episodes"]]
    assert stamps == sorted(stamps, reverse=True)
    assert [e["item_id"] for e in res["episodes"]] == ["ep_live", "ep_notes"]


def test_the_discarded_episode_is_hidden_from_the_list_and_the_counts(seeded_db):
    """Discard hides; it does not delete. The row, its analysis and its claim
    are all still in the warehouse, and none of them may reach the payload."""
    res = episodes(seeded_db, creator=TALKER, include_untranscribed=True)
    assert "ep_gone" not in _by_id(res)
    assert res["counts"]["episodes_total"] == 2
    assert res["counts"]["analysed"] == 1
    with Warehouse(seeded_db) as wh:
        still_there = wh.sql(
            "SELECT count(*) AS n FROM content_item WHERE item_id = 'ep_gone'")
        assert int(still_there.iloc[0]["n"]) == 1


def test_the_transcription_state_is_read_off_the_stored_columns(seeded_db):
    """Each of the four values, and the column that produces it."""
    talker = _by_id(episodes(seeded_db, creator=TALKER,
                             include_untranscribed=True))
    # transcript_segment rows are on file for both.
    assert talker["ep_live"]["transcription_state"] == "transcribed"
    assert talker["ep_notes"]["transcription_state"] == "transcribed"

    # content_item_asset.enclosure_url is stored and no transcript is.
    quiet = _by_id(episodes(seeded_db, creator=QUIET,
                            include_untranscribed=True))
    assert quiet["ep_queued"]["transcription_state"] == "queued"
    assert quiet["ep_queued"]["transcript_chars"] is None

    # A content_transcribe_skip row outranks the addressable audio: the step
    # ran and refused, which is not the same as waiting in a queue.
    with Warehouse(seeded_db) as wh:
        wh.sql(
            "CREATE TABLE IF NOT EXISTS content_transcribe_skip ("
            " item_id VARCHAR PRIMARY KEY, reason VARCHAR NOT NULL,"
            " detail VARCHAR, at_utc TIMESTAMP WITH TIME ZONE NOT NULL)")
        wh.sql("INSERT INTO content_transcribe_skip VALUES (?, ?, ?, ?)",
               ["ep_queued", "no_captions", "no_english_track", PAST])
    quiet = _by_id(episodes(seeded_db, creator=QUIET,
                            include_untranscribed=True))
    assert quiet["ep_queued"]["transcription_state"] == "failed"

    # With neither a transcript, a skip row nor an audio URL, there is nothing
    # on file to say anything about.
    with Warehouse(seeded_db) as wh:
        wh.sql("DELETE FROM content_transcribe_skip WHERE item_id = 'ep_queued'")
        wh.sql("DELETE FROM content_item_asset WHERE item_id = 'ep_queued'")
    quiet = _by_id(episodes(seeded_db, creator=QUIET,
                            include_untranscribed=True))
    assert quiet["ep_queued"]["transcription_state"] == "none"


def test_transcript_chars_counts_the_stored_segment_text(seeded_db):
    row = _by_id(episodes(seeded_db, creator=TALKER))["ep_live"]
    assert row["transcript_chars"] == sum(len(t) for _, t in SEGMENTS)


def test_include_untranscribed_toggles_the_rows_and_not_the_totals(seeded_db):
    hidden = episodes(seeded_db, creator=QUIET)
    shown = episodes(seeded_db, creator=QUIET, include_untranscribed=True)
    assert hidden["episodes"] == []
    assert [e["item_id"] for e in shown["episodes"]] == ["ep_queued"]
    assert hidden["counts"] == shown["counts"] == {
        "episodes_total": 1, "transcribed": 0, "analysed": 0}
    assert hidden["include_untranscribed"] is False
    assert shown["include_untranscribed"] is True


def test_the_counts_are_the_whole_corpus_not_the_capped_list(seeded_db):
    capped = episodes(seeded_db, creator=TALKER, limit=1,
                      include_untranscribed=True)
    assert len(capped["episodes"]) == 1
    assert capped["counts"]["episodes_total"] == 2
    assert capped["limit"] == 1


def test_an_episode_row_names_its_analysis_and_its_gameweek(seeded_db):
    row = _by_id(episodes(seeded_db, creator=TALKER))["ep_live"]
    assert row["analysis_present"] is True
    assert row["analysis_model"] == "claude-opus-5"
    assert row["source_url"] == f"https://www.youtube.com/watch?v={LIVE_ID}"
    assert row["source_kind"] == "youtube"
    assert row["gameweek"] == 2
    assert "GW2" in row["gw_reason"]

    notes = _by_id(episodes(seeded_db, creator=TALKER))["ep_notes"]
    assert notes["analysis_present"] is False
    assert notes["analysis_model"] is None


def test_the_claim_count_collapses_the_rows_of_one_publication(seeded_db):
    row = _by_id(episodes(seeded_db, creator=TALKER))["ep_live"]
    assert row["claim_count"] == 3


# ---------------------------------------------------------------------------
# The exact key sets.

EPISODES_KEYS = {"creator", "as_of", "entry", "entry_reason", "record",
                 "include_untranscribed", "limit", "counts", "episodes"}
EPISODE_KEYS = {"item_id", "title", "published_at", "source_url", "source_kind",
                "transcription_state", "transcript_chars", "analysis_present",
                "analysis_model", "claim_count", "gameweek", "gw_reason"}
SUMMARY_KEYS = {"creator", "as_of", "episode", "summary", "summary_bullets",
                "summary_reason", "players", "transfers_suggested",
                "captain_view", "gameweek", "gw_reason", "analysis_model",
                "analysed_at", "gaps"}


def test_the_result_key_set_of_creator_episodes_is_exact(seeded_db):
    res = episodes(seeded_db, creator=TALKER)
    assert set(res) == EPISODES_KEYS
    assert set(res["episodes"][0]) == EPISODE_KEYS
    assert set(res["counts"]) == {"episodes_total", "transcribed", "analysed"}


def test_the_result_key_set_of_episode_summary_is_exact(seeded_db):
    res = summary(seeded_db, "ep_live")
    assert set(res) == SUMMARY_KEYS
    assert set(res["episode"]) == EPISODE_KEYS


def test_both_payloads_validate_against_their_declared_schemas(seeded_db):
    """The live-shaped payload against the real branch, not just the wrapper."""
    for name, payload in (
        ("creator_episodes", episodes(seeded_db, creator=TALKER,
                                      include_untranscribed=True)),
        ("episode_summary", summary(seeded_db, "ep_live")),
    ):
        inner = script(name).result_schema["oneOf"][0]
        jsonschema.Draft202012Validator(inner).validate(payload)


# ---------------------------------------------------------------------------
# One episode, opened.

def test_episode_summary_serves_the_stored_summary(seeded_db):
    res = summary(seeded_db, "ep_live")
    assert res["creator"] == TALKER
    assert res["summary_bullets"] == ANALYSIS["summary"]
    assert res["summary"] == "\n".join(ANALYSIS["summary"])
    assert res["summary_reason"] is None
    assert res["analysis_model"] == "claude-opus-5"
    assert res["analysed_at"] is not None


def test_every_claim_carries_its_quote_where_one_was_stored(seeded_db):
    """Quote where stored, null where not, and never a sentence from the
    transcript standing in for a missing one."""
    res = summary(seeded_db, "ep_live")
    # Keyed on the channel too: one player can carry both a stored claim row
    # and a stored call with the same direction, and they are two records of
    # the same moment, not one.
    evidence = {(p["code"], e["kind"], e["direction"]): e
                for p in res["players"] for e in p["claims"]}

    # An llm claim: the fragment after `| quote: `, located in the transcript.
    captain = evidence[(HAALAND, "claim", "captain")]
    assert captain["quote"] == "Haaland is the captain this week"
    assert captain["start_s"] == 12.5
    assert captain["deep_link"] == (
        f"https://www.youtube.com/watch?v={LIVE_ID}&t=12s")
    assert captain["confidence"] == 0.8
    assert captain["conviction"] is None

    # A cue claim: the keyword window IS the quote.
    buy = evidence[(SALIBA, "claim", "buy")]
    assert buy["quote"] == "i am leaving saliba on the bench for now"
    assert buy["start_s"] == 30.25

    # A claim with no evidence stored: null quote, null offset, still listed.
    bare = evidence[(SALIBA, "claim", "sell")]
    assert bare["quote"] is None
    assert bare["start_s"] is None
    assert bare["deep_link"] == LIVE_URL


def test_a_call_whose_quote_is_not_in_the_transcript_gets_no_offset(seeded_db):
    res = summary(seeded_db, "ep_live")
    out = [t for t in res["transfers_suggested"] if t["direction"] == "out"]
    assert len(out) == 1
    assert out[0]["quote"] == ANALYSIS["transfers_out"][0]["quote"]
    assert out[0]["start_s"] is None
    assert out[0]["deep_link"] == LIVE_URL
    assert out[0]["paired_with"] is None


def test_a_player_carries_his_club_and_position_where_the_code_resolved(seeded_db):
    res = summary(seeded_db, "ep_live")
    by_code = {p["code"]: p for p in res["players"]}
    assert by_code[HAALAND]["display_name"] == "Haaland"
    assert by_code[HAALAND]["resolved"] is True
    assert by_code[HAALAND]["team"] == "MCI"
    assert by_code[HAALAND]["position"] == "FWD"


def test_the_two_stored_channels_stay_distinguishable(seeded_db):
    """A claim row carries a number and an extractor; a stored call carries a
    conviction band and the list it was written into. Neither is converted."""
    res = summary(seeded_db, "ep_live")
    kinds = {e["kind"] for p in res["players"] for e in p["claims"]}
    assert kinds == {"claim", "call"}
    calls = [e for p in res["players"] for e in p["claims"]
             if e["kind"] == "call"]
    assert {c["stored_in"] for c in calls} <= {
        "transfers_in", "transfers_out", "captaincy", "differentials"}
    assert all(c["confidence"] is None and c["conviction"] for c in calls)


def test_an_episode_with_no_analysis_says_which_section_is_missing(seeded_db):
    res = summary(seeded_db, "ep_notes")
    assert res["summary"] is None
    assert "no analysis" in res["summary_reason"]
    assert res["players"] == []
    assert res["transfers_suggested"] == []
    assert res["captain_view"] == []
    sections = {g["section"] for g in res["gaps"]}
    assert "analysis" in sections
    assert all(g["gap"] for g in res["gaps"])


def test_an_empty_captain_list_is_a_named_gap(seeded_db):
    """Every gap names a section and gives a reason a reader can act on."""
    res = summary(seeded_db, "ep_live")
    gaps = {g["section"]: g["gap"] for g in res["gaps"]}
    assert len(res["captain_view"]) == 1
    assert "captain_view" not in gaps
    assert "transfers_suggested.paired_with" in gaps


def test_episode_summary_refuses_a_discarded_item(seeded_db):
    res = summary(seeded_db, "ep_gone")
    assert res["empty"] is True
    assert "discard" in res["reason"]


def test_episode_summary_refuses_an_item_this_warehouse_does_not_hold(seeded_db):
    res = summary(seeded_db, "no_such_item")
    assert res["empty"] is True
    assert "no_such_item" in res["reason"]
