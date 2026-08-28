"""creator_board / creator_detail against a seeded warehouse.

Hermetic: every test builds its own DuckDB file, runs the content migrations
through :class:`ContentStore` and plants the exact rows under test. Nothing
here reaches the network or the live warehouse.

The fixture is built to carry the four failure modes the panel exists to avoid,
so that each one is a test rather than a paragraph in a docstring:

* **The leak.** One item and one claim are published two days in the FUTURE,
  and one ``creator_score`` row is stamped in the future too. A reader of "now"
  must see neither -- not the claim (``claims_visible_at`` filters
  ``published_at < as_of``) and not the weight (``creator_score`` is read
  bounded by ``as_of <=``). A panel that reads today's track record to answer a
  past question has no visible symptom; only a test has.
* **The duplicate.** One video is stored twice, once as ``watch?v=`` with the
  transcript and once as ``youtu.be/`` with the analysis. Keying on the raw URL
  yields two publications, double-counts the claims, and hides the transcript
  from the analysis that needs it for timestamps.
* **The blank card.** A creator whose latest item carries show notes only must
  render ``take: null`` WITH a reason a human can read.
* **The invented link.** A quote that cannot be located in a transcript gets
  ``start_s: null`` and the item URL, never a guessed offset.
"""

from __future__ import annotations

import datetime as dt
import json

import jsonschema
import pandas as pd
import pytest

import fpl_edge.platform.scripts  # noqa: F401  (registers the creator scripts)
from fpl_edge.platform.registry import run_script, script
from fpl_edge.store.warehouse import Warehouse

UTC = dt.timezone.utc
SEASON = "2026-27"

NOW = dt.datetime.now(UTC)
PAST = NOW - dt.timedelta(days=2)
FUTURE = NOW + dt.timedelta(days=2)
#: Whole seconds, on purpose. Feeds publish "…11:15:00+00:00" while shared
#: links carry sub-second precision, so the content tables hold BOTH shapes in
#: one column. A vectorised ``pd.to_datetime`` over that column infers one
#: format from the first value and coerces the rest to NaT -- which silently
#: dropped a whole creator out of the window with a count of 0 rather than an
#: error. Keeping two precisions in the fixture keeps that failure catchable.
PAST_WHOLE = PAST.replace(microsecond=0)
SEEDED = NOW - dt.timedelta(days=40)  # dim_* as_of: before any window

VIDEO_ID = "ABCdef12345"
WATCH_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
SHORT_URL = f"https://youtu.be/{VIDEO_ID}?si=trackingjunk"

HAALAND, SALIBA = 223094, 222683

#: The quote in the analysis matches this segment verbatim apart from case and
#: punctuation -- which is the realistic case, because auto-captions carry
#: neither.
SEGMENTS = [
    (0.0, "what is going on everyone welcome back"),
    (12.5, "haaland is the captain this week no question about it"),
    (30.25, "i am leaving saliba on the bench for now"),
    (48.0, "that is all from me see you next time"),
    (60.0, "keep an eye on saliba he could be a differential"),
]

ANALYSIS = {
    "summary": ["Haaland is the captain.", "Saliba benched."],
    "transfers_in": [{
        "player": "Erling Haaland", "stance": "buy", "conviction": "high",
        "gameweek": 2, "reasoning": "Best fixture.",
        "quote": "Haaland is the captain this week, no question about it",
    }],
    "transfers_out": [{
        # This quote is NOT in the transcript: it must degrade to the item URL.
        "player": "William Saliba", "stance": "sell", "conviction": "low",
        "gameweek": 2, "reasoning": "Rotation risk.",
        "quote": "a sentence that was never spoken in this video at all",
    }],
    "captaincy": [{
        "player": "Erling Haaland", "stance": "captain", "conviction": "high",
        "gameweek": 2, "reasoning": "Obvious.",
        "quote": "Haaland is the captain this week",
    }],
    "chip_advice": [{
        "chip": "wildcard", "stance": "hold", "gameweek": 7,
        "reasoning": "Wait for the fixture swing.",
        "quote": "I am leaving Saliba on the bench for now",
    }],
    # A WATCH. The claim writer refuses to map this stance -- a watch is not a
    # scoreable position -- so it never reaches `content_claim` and the only
    # place it exists is here. A panel that reads claims alone drops every one
    # of them; a panel that reads them as transfers puts a buy in somebody's
    # mouth that they never said.
    "differentials": [{
        "player": "William Saliba", "stance": "watch", "conviction": "medium",
        "gameweek": 3, "reasoning": "Worth monitoring.",
        "quote": "keep an eye on Saliba, he could be a differential",
    }],
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


def _source(wh, key, creator, kind, url, status=200):
    wh.sql(
        "INSERT INTO content_source VALUES (?, ?, ?, ?, 'open', NULL, ?, ?, 0, NULL)",
        [key, creator, kind, url, PAST, status],
    )


def _item(wh, item_id, source_key, creator, kind, title, url, published,
          text_source, text="body"):
    wh.sql(
        "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sha')",
        [item_id, source_key, creator, kind, title, url, published, published,
         text_source, text],
    )


def _claim(wh, claim_id, item_id, creator, source_key, code, name, action,
           published, *, gw=2, confidence=0.8, rationale="because",
           extractor="cue", url=WATCH_URL):
    wh.sql(
        "INSERT INTO content_claim VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, false, ?)",
        [claim_id, item_id, creator, source_key, code, name, name, action,
         SEASON, gw, confidence, rationale, url, published, extractor],
    )


def _score(wh, creator, as_of, *, scored, hits, weight, lo95):
    wh.sql(
        "INSERT INTO creator_score VALUES (?, 'all', ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        [creator, as_of, scored, scored, hits,
         (hits / scored) if scored else None, lo95, weight],
    )


def _plant_panel_member(db, display_name, entry_id, show="The Talker"):
    """Create the roster the entry lookup reads.

    This planted ``dim_panel_member``, the table CREATOR_ELITE_PROMPT.md Stage
    A specifies. Stage A never ran. What exists is ``panel_person`` /
    ``panel_person_show``, built when the owner asked for podcast co-hosts to
    be tracked individually -- and because the panel script was still reading
    the Stage A name, every creator's verified team rendered as "not
    published" while sixteen verified ids sat in the warehouse. The test now
    plants what the code reads, which is the only version of this test that
    could have caught that.
    """
    wh = Warehouse(db)
    wh.sql(
        "CREATE TABLE IF NOT EXISTS panel_person ("
        " person_key VARCHAR, display_name VARCHAR, handles_json VARCHAR,"
        " aliases_json VARCHAR, entry_id BIGINT, entry_verified BOOLEAN,"
        " entry_source_url VARCHAR, entry_api_name VARCHAR,"
        " entry_checked_utc TIMESTAMPTZ, entry_reason VARCHAR,"
        " edge_note VARCHAR, top10k_finishes INTEGER, active BOOLEAN,"
        " as_of TIMESTAMPTZ)"
    )
    wh.sql(
        "CREATE TABLE IF NOT EXISTS panel_person_show ("
        " person_key VARCHAR, show_creator VARCHAR, source_key VARCHAR,"
        " role VARCHAR, as_of TIMESTAMPTZ)"
    )
    wh.sql(
        "INSERT INTO panel_person VALUES ('talker', ?, NULL, NULL, ?, true, ?,"
        " 'Talker FC', ?, NULL, NULL, NULL, true, ?)",
        [display_name, entry_id, "https://example.invalid/proof", PAST, PAST],
    )
    wh.sql(
        "INSERT INTO panel_person_show VALUES ('talker', ?, NULL, 'host', ?)",
        [show, PAST],
    )
    wh.close()


def _plant_panel_people(db, people, *, shows=None):
    """Plant several panel PEOPLE at once, with the shows they appear on.

    ``_plant_panel_member`` covers the one-person case the entry lookup needs.
    The DID channel needs a roster: somebody whose squad has been crawled,
    somebody whose has not, and somebody with no verified entry id at all --
    because the three render as three different sentences and collapsing them
    is the exact lie this panel exists to avoid.
    """
    wh = Warehouse(db)
    wh.sql(
        "CREATE TABLE IF NOT EXISTS panel_person ("
        " person_key VARCHAR, display_name VARCHAR, handles_json VARCHAR,"
        " aliases_json VARCHAR, entry_id BIGINT, entry_verified BOOLEAN,"
        " entry_source_url VARCHAR, entry_api_name VARCHAR,"
        " entry_checked_utc TIMESTAMPTZ, entry_reason VARCHAR,"
        " edge_note VARCHAR, top10k_finishes INTEGER, active BOOLEAN,"
        " as_of TIMESTAMPTZ)"
    )
    wh.sql(
        "CREATE TABLE IF NOT EXISTS panel_person_show ("
        " person_key VARCHAR, show_creator VARCHAR, source_key VARCHAR,"
        " role VARCHAR, as_of TIMESTAMPTZ)"
    )
    for key, name, entry_id in people:
        wh.sql(
            "INSERT INTO panel_person VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?,"
            " NULL, NULL, NULL, true, ?)",
            [key, name, entry_id, entry_id is not None,
             "https://example.invalid/proof", f"{name} FC", PAST, PAST],
        )
    for key, show in (shows or []):
        wh.sql("INSERT INTO panel_person_show VALUES (?, ?, NULL, 'host', ?)",
               [key, show, PAST])
    wh.close()


def _plant_picks(db, entry_id, gw, picks):
    """One panel member's locked squad. ``picks`` is (element_id, mult, is_cap)."""
    wh = Warehouse(db)
    for slot, (element, mult, cap) in enumerate(picks, start=1):
        wh.sql(
            "INSERT INTO fact_manager_pick VALUES (?, ?, ?, ?, ?, ?, ?, false, ?)",
            [entry_id, SEASON, gw, element, slot, mult, cap,
             NOW - dt.timedelta(days=6)],
        )
    wh.close()


def _plant_intel(db, rows):
    """``intel_item`` rows: MEASURED signals, never spoken ones."""
    from fpl_edge.intel.store import IntelStore

    wh = Warehouse(db)
    IntelStore(wh)  # runs the intel migrations
    for r in rows:
        wh.sql(
            "INSERT INTO intel_item VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?,"
            " NULL, ?)",
            [r["item_id"], r["published_at"], r["published_at"], SEASON,
             r["kind"], r["player_code"], r["headline"], r.get("body"),
             r.get("source", "fpl_edge.intel.test"), r.get("source_url"),
             r.get("confidence", 0.9)],
        )
    wh.close()


@pytest.fixture(autouse=True)
def _no_network_squad(monkeypatch):
    """Every test in this module is hermetic, INCLUDING the owner's squad read.

    ``creator_board`` now populates ``mine`` through ``ownership._squad_state``,
    which is the one read on this page that leaves the warehouse: private API,
    then public picks, then the manually entered 15. Left alone it would reach
    the network from a unit test -- slow, flaky, and dependent on whose machine
    is running. Patched to raise, it exercises the honest-degradation path,
    which is the path that has to be right anyway: ``in_squad: null`` plus a
    reason, never a silent ``false``. The one test that needs a real squad
    overrides this deliberately.
    """
    from fpl_edge.interfaces import qa

    class _Refuses:
        def __init__(self, *a, **k):
            raise RuntimeError("no network in a unit test")

    monkeypatch.setattr(qa, "QuestionRouter", _Refuses)


@pytest.fixture()
def empty_db(tmp_path):
    """A warehouse with no content migrations run at all."""
    path = tmp_path / "fpl.duckdb"
    Warehouse(path).close()
    return path


@pytest.fixture()
def seeded_db(tmp_path):
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
    # GW1's deadline has passed, GW2's has not: the board should report GW2.
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "is_finished": True,
         "deadline_utc": NOW - dt.timedelta(days=6), "as_of": SEEDED},
        {"season": SEASON, "gw": 2, "is_finished": False,
         "deadline_utc": NOW + dt.timedelta(days=1), "as_of": SEEDED},
    ]))

    from fpl_edge.ingest.content.store import ContentStore

    ContentStore(wh)  # runs content_001 / content_002

    # -- a creator whose video is stored TWICE under two URL forms ---------
    _source(wh, "yt_talker", "The Talker", "youtube",
            "https://www.youtube.com/@talker")
    _item(wh, "watch_row", "yt_talker", "The Talker", "youtube",
          "GW2 Preview", WATCH_URL, PAST, "transcript")
    _item(wh, "short_row", "yt_talker", "The Talker", "youtube",
          "GW2 Preview", SHORT_URL, PAST - dt.timedelta(minutes=1), "description")
    for seq, (start_s, text) in enumerate(SEGMENTS):
        wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
               ["watch_row", seq, start_s, text])
    # The analysis is attached to the OTHER row of the same video, which is
    # exactly the live shape: only canonicalisation pairs it with the transcript.
    wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
           ["short_row", "claude-opus-5", PAST, json.dumps(ANALYSIS)])
    # The same position recorded against both stored rows: one publication.
    _claim(wh, "c_watch", "watch_row", "The Talker", "yt_talker", HAALAND,
           "Erling Haaland", "captain", PAST, extractor="llm:claude-opus-5",
           rationale="Obvious. | quote: Haaland is the captain this week")
    _claim(wh, "c_short", "short_row", "The Talker", "yt_talker", HAALAND,
           "Erling Haaland", "captain", PAST, extractor="llm:claude-opus-5",
           rationale="Obvious. | quote: Haaland is the captain this week",
           url=SHORT_URL)

    # -- a creator with items but NO analysis ------------------------------
    _source(wh, "yt_notes", "Notes Only", "youtube",
            "https://www.youtube.com/@notes")
    _item(wh, "notes_past", "yt_notes", "Notes Only", "youtube",
          "Show notes", "https://www.youtube.com/watch?v=NOTESvid001",
          PAST_WHOLE, "description")
    _claim(wh, "c_notes", "notes_past", "Notes Only", "yt_notes", HAALAND,
           "Erling Haaland", "buy", PAST_WHOLE)

    # -- THE LEAK: an item, a claim and a weight from the future ------------
    _item(wh, "notes_future", "yt_notes", "Notes Only", "youtube",
          "Tomorrow's episode",
          "https://www.youtube.com/watch?v=FUTUREvid01", FUTURE, "description")
    _claim(wh, "c_future", "notes_future", "Notes Only", "yt_notes", SALIBA,
           "William Saliba", "buy", FUTURE)
    _score(wh, "Notes Only", PAST, scored=10, hits=3, weight=0.0, lo95=0.11)
    _score(wh, "Notes Only", FUTURE, scored=100, hits=90, weight=0.9, lo95=0.82)

    # -- a registered source that has never yielded an item ----------------
    _source(wh, "pod_silent", "Silent Pod", "podcast",
            "https://example.invalid/feed.xml", status=200)

    wh.close()
    return path


def board(db, **params):
    return run_script("creator_board", params, db=db).result


def detail(db, **params):
    return run_script("creator_detail", params, db=db).result


def _by_name(res):
    return {c["creator"]: c for c in res["creators"]}


# ---------------------------------------------------------------------------
# The honest-empty path.

def test_a_warehouse_without_the_content_tables_is_an_honest_empty(empty_db):
    res = board(empty_db)
    assert res.get("empty") is True
    assert set(res) == {"empty", "reason"}
    assert "content_item" in res["reason"]
    assert "pipeline" in res["reason"]


def test_detail_for_an_unknown_creator_names_the_ones_that_exist(seeded_db):
    res = detail(seeded_db, creator="Nobody At All")
    assert res.get("empty") is True
    assert "Nobody At All" in res["reason"]
    assert "The Talker" in res["reason"] and "Notes Only" in res["reason"]


# ---------------------------------------------------------------------------
# The contract's own shape, asserted explicitly.

def test_board_validates_against_its_own_registered_schema(seeded_db):
    """run_script already validates. Assert it here anyway.

    A schema that is only ever exercised by the code that emits it drifts the
    moment someone widens it to make a run pass; this pins the payload to the
    published shape from the reader's side.
    """
    res = board(seeded_db)
    assert res.get("empty") is not True
    jsonschema.Draft202012Validator(
        script("creator_board").result_schema
    ).validate(res)


def test_detail_validates_against_its_own_registered_schema(seeded_db):
    res = detail(seeded_db, creator="The Talker")
    assert res.get("empty") is not True
    jsonschema.Draft202012Validator(
        script("creator_detail").result_schema
    ).validate(res)


def test_the_real_schema_cannot_be_satisfied_by_the_empty_shape(seeded_db):
    """The registry wraps every result as oneOf[mine, {empty, reason}].

    ``oneOf`` means EXACTLY one, so a real branch that also admitted
    ``{empty, reason}`` would make every honest empty fail validation. Both
    scripts' real branches must reject it.
    """
    for name in ("creator_board", "creator_detail"):
        inner = script(name).result_schema["oneOf"][0]
        validator = jsonschema.Draft202012Validator(inner)
        assert not validator.is_valid({"empty": True, "reason": "nothing yet"})


# ---------------------------------------------------------------------------
# Nothing is invented.

def test_a_creator_with_items_but_no_analysis_gets_a_null_take_and_a_reason(seeded_db):
    row = _by_name(board(seeded_db))["Notes Only"]
    assert row["latest"] is not None, "the latest item must be a real row"
    assert row["latest"]["item_id"] == "notes_past"
    assert row["take"] is None
    assert row["take_reason"]
    # The reason names what the item actually carries, not a generic blank.
    assert "description" in row["take_reason"] or "show notes" in row["take_reason"]


def test_items_with_mixed_timestamp_precision_all_land_in_the_window(seeded_db):
    """One column, two precisions, and both items inside the window.

    ``notes_past`` is stamped to the whole second and ``watch_row`` to the
    microsecond, which is exactly how the live tables look: feeds publish
    ``11:15:00+00:00``, shared links carry ``…54.706755+00:00``. Parsing the
    column in one vectorised call infers a single format from the first value
    and turns every other shape into ``NaT``, so a creator drops out of the
    window reporting a confident 0 rather than raising.
    """
    res = board(seeded_db)
    rows = _by_name(res)
    assert rows["Notes Only"]["n_items_window"] == 1
    assert rows["The Talker"]["n_items_window"] == 1
    assert sum(r["n_items_window"] for r in res["creators"]) == 2


def test_a_source_that_has_never_produced_an_item_says_so(seeded_db):
    row = _by_name(board(seeded_db))["Silent Pod"]
    assert row["latest"] is None
    assert row["latest_reason"] and "no item" in row["latest_reason"]
    assert row["n_items"] == 0
    assert row["sources"][0]["last_status"] == 200
    assert row["sources"][0]["last_item_at"] is None


def test_no_verified_entry_means_null_and_a_reason_never_a_guess(seeded_db):
    res = board(seeded_db)
    for row in res["creators"]:
        assert row["entry"] is None
        assert row["entry_reason"] and "panel_person" in row["entry_reason"]
    det = detail(seeded_db, creator="The Talker")
    assert det["entry"] is None and det["entry_reason"]
    assert det["squad"] is None and det["squad_reason"]


def test_an_unscored_creator_gets_nulls_not_zeros(seeded_db):
    row = _by_name(board(seeded_db))["The Talker"]
    rec = row["record"]
    assert rec["scored"] is None and rec["hits"] is None
    assert rec["hit_rate"] is None and rec["wilson_lo95"] is None
    assert rec["weight"] is None, "0.0 would read as a measured zero"
    assert rec["earned"] is False
    assert rec["reason"] and "creator_score" in rec["reason"]


def test_without_an_entry_the_transfer_list_blames_the_missing_id(seeded_db):
    det = detail(seeded_db, creator="The Talker")
    assert det["transfers"] == []
    assert det["transfers_reason"] and "entry id" in det["transfers_reason"]


def test_with_a_verified_entry_the_empty_transfer_list_is_explained(seeded_db):
    """fact_manager_transfer is empty and that is CORRECT, not a bug.

    A gameweek's transfers become public only once its deadline passes, and the
    season's first gameweek has none behind it at all. The reason has to say
    that, or a reader takes the empty list for a broken ingest.
    """
    _plant_panel_member(seeded_db, "The Talker", 53517)
    det = detail(seeded_db, creator="The Talker")

    assert det["entry"]["entry_id"] == 53517
    assert det["entry"]["name"] == "Talker FC"
    assert det["entry"]["verified"] is True
    assert det["entry"]["source_url"] == "https://example.invalid/proof"
    # A show carries PEOPLE. One host here, so the flat fields are populated
    # too -- with two they would be null and the caller must name a person.
    assert [p["person"] for p in det["entry"]["people"]] == ["The Talker"]
    assert det["entry_reason"] is None
    assert det["transfers"] == []
    assert "deadline" in det["transfers_reason"]
    assert "not a missing ingest" in det["transfers_reason"]
    # No picks are stored for that entry either, and that gets its own reason.
    assert det["squad"] is None
    assert "deadline" in det["squad_reason"]


def test_the_record_note_reports_a_measured_coin_flip(seeded_db):
    note = board(seeded_db)["record_note"]
    assert "coin flip" in note
    assert "not missing data" in note


# ---------------------------------------------------------------------------
# Deep links.

def test_a_located_quote_gets_a_youtube_timestamp_deep_link(seeded_db):
    take = _by_name(board(seeded_db))["The Talker"]["take"]
    assert take is not None
    call = take["captain"][0]
    assert call["code"] == HAALAND
    assert call["start_s"] == 12.5
    # Floored, not rounded: 12.5s -> t=12s. A link that starts after the
    # words the reader clicked to hear reads as broken.
    assert call["deep_link"] == (
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=12s"
    )


def test_an_unlocatable_quote_degrades_to_the_item_url(seeded_db):
    take = _by_name(board(seeded_db))["The Talker"]["take"]
    call = take["transfers_out"][0]
    assert call["code"] == SALIBA
    assert call["start_s"] is None, "no offset may be invented"
    # The representative row of this publication is the one with the
    # transcript, so the bare item URL is the watch form.
    assert call["deep_link"] == WATCH_URL


def test_a_creator_with_no_transcript_at_all_still_links_to_the_item(seeded_db):
    det = detail(seeded_db, creator="Notes Only")
    item = next(i for i in det["items"] if i["item_id"] == "notes_past")
    claim = item["claims"][0]
    assert claim["start_s"] is None
    assert claim["deep_link"] == "https://www.youtube.com/watch?v=NOTESvid001"


def test_chip_advice_carries_its_own_deep_link(seeded_db):
    take = _by_name(board(seeded_db))["The Talker"]["take"]
    chip = take["chips"][0]
    assert chip["chip"] == "wildcard" and chip["horizon_gw"] == 7
    assert chip["start_s"] == 30.25
    assert chip["deep_link"].endswith("&t=30s")


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VIDEO_ID}",
    f"https://www.youtube.com/watch?reload=9&v={VIDEO_ID}",
    f"https://youtu.be/{VIDEO_ID}?si=abc",
    f"https://www.youtube.com/live/{VIDEO_ID}",
    f"https://m.youtube.com/shorts/{VIDEO_ID}",
])
def test_every_youtube_url_form_reduces_to_the_same_video_id(url):
    from fpl_edge.platform.scripts.creators import canonical_key, youtube_id

    assert youtube_id(url) == VIDEO_ID
    assert canonical_key(url, "whatever") == f"yt:{VIDEO_ID}"


def test_a_non_youtube_url_keeps_its_identity_and_gets_no_fragment():
    from fpl_edge.platform.scripts.creators import canonical_key, deep_link

    url = "https://example.com/ep/12"
    assert canonical_key(url, "i1") == f"url:{url}"
    # A timestamp is known but the platform has no grammar for it: the link is
    # the episode page, unchanged, rather than a fragment that lands nowhere.
    assert deep_link(url, 812.0) == url


# ---------------------------------------------------------------------------
# One video stored twice is one publication.

def test_a_video_stored_under_two_urls_counts_once(seeded_db):
    row = _by_name(board(seeded_db))["The Talker"]
    assert row["n_items"] == 1, "watch?v= and youtu.be are the same video"
    assert row["n_items_window"] == 1


def test_the_duplicate_rows_lend_each_other_transcript_and_analysis(seeded_db):
    """The analysis is on one stored row, the transcript on the other.

    Without canonicalisation the analysis has no transcript to search, every
    ``start_s`` comes back null, and the panel silently stops producing deep
    links at all.
    """
    det = detail(seeded_db, creator="The Talker")
    assert len(det["items"]) == 1, "two stored rows, one publication"
    item = det["items"][0]
    assert item["analysis"] is not None
    assert item["analysis"]["captain"][0]["start_s"] == 12.5
    # ...and the position recorded against both rows is listed once.
    captains = [c for c in item["claims"] if c["action"] == "captain"]
    assert len(captains) == 1
    assert captains[0]["extractor"] == "llm:claude-opus-5"
    assert captains[0]["start_s"] == 12.5


def test_the_summary_keeps_its_bullets_beside_the_contract_string(seeded_db):
    take = _by_name(board(seeded_db))["The Talker"]["take"]
    assert take["summary_bullets"] == ANALYSIS["summary"]
    assert take["summary"] == "\n".join(ANALYSIS["summary"])
    assert take["model"] == "claude-opus-5"


# ---------------------------------------------------------------------------
# Point in time.

def test_a_claim_published_after_the_requested_instant_is_invisible(seeded_db):
    """THE leakage test. c_future is published two days from now.

    It must not reach the consensus, must not be counted in the window, and its
    item must not become the creator's latest -- a creator's not-yet-published
    episode informing today's board is the content equivalent of reading the
    result before the match.
    """
    res = board(seeded_db)
    row = _by_name(res)["Notes Only"]

    assert row["latest"]["item_id"] == "notes_past", (
        "an item published in the future became the creator's latest"
    )
    assert row["n_claims_window"] == 1, (
        "the future claim was counted; claims_visible_at was bypassed"
    )
    codes = {c["code"] for c in res["consensus"]}
    assert HAALAND in codes
    assert SALIBA not in codes, (
        "the claim published after the requested instant reached the consensus"
    )


def test_the_future_claim_is_invisible_in_the_detail_view_too(seeded_db):
    det = detail(seeded_db, creator="Notes Only")
    ids = {i["item_id"] for i in det["items"]}
    assert ids == {"notes_past"}
    claims = [c for i in det["items"] for c in i["claims"]]
    assert {c["code"] for c in claims} == {HAALAND}


def test_a_creator_score_stamped_in_the_future_is_not_in_force_now(seeded_db):
    """The weights half of the leak, which has no visible symptom.

    ``creator_score`` is append-only and keyed by ``as_of``. Reading the newest
    row without the ``as_of <= moment`` bound answers a question about now with
    a track record that does not exist yet -- and the payload still echoes an
    honest-looking ``as_of``, so nothing on the page looks wrong.
    """
    rec = _by_name(board(seeded_db))["Notes Only"]["record"]
    assert rec["scored"] == 10, "the future creator_score row was used"
    assert rec["hits"] == 3
    assert rec["weight"] == 0.0 and rec["earned"] is False


def test_the_board_reports_the_next_undecided_gameweek(seeded_db):
    res = board(seeded_db)
    assert res["gw"] == 2
    # `gw_reason` is a string on EVERY branch, including this happy one. "GW2"
    # alone does not tell a reader whether it was asked for, deduced from the
    # calendar, or scraped off the claims because the calendar was unreadable,
    # and those are three different amounts of trust.
    assert "next gameweek" in res["gw_reason"]
    assert board(seeded_db, gw=1)["gw_reason"] == "GW1 was requested explicitly."


def test_the_board_and_the_player_strip_resolve_the_same_gameweek(chatter_db):
    """One ladder, or the board and the strip beneath it name different weeks.

    They were two separate rules for exactly one release: `creator_board` had
    the next-deadline ladder and `player_chatter` had no gameweek at all, so
    the page could show "the panel for GW2" above a strip windowed by days that
    silently mixed GW2 and GW3 talk together.
    """
    b = board(chatter_db)
    c = chatter(chatter_db, code=HAALAND)
    assert b["gw"] == c["gw"] == 2
    assert "next gameweek" in b["gw_reason"]
    assert "next gameweek" in c["gw_reason"]


# ---------------------------------------------------------------------------
# Consensus.

def test_consensus_nets_buys_against_sells_and_keeps_extractors_apart(seeded_db):
    res = board(seeded_db)
    row = next(r for r in res["consensus"] if r["code"] == HAALAND)
    assert row["name"] == "Haaland" and row["pos"] == "FWD"
    assert row["team"] == "MCI" and row["price"] == 14.5
    assert row["own_pct"] == 62.0
    assert row["buy"]["n"] == 1 and row["buy"]["creators"] == ["Notes Only"]
    assert row["buy"]["n_cue"] == 1 and row["buy"]["n_llm"] == 0
    assert row["captain"]["n"] == 1 and row["captain"]["creators"] == ["The Talker"]
    assert row["captain"]["n_llm"] == 1 and row["captain"]["n_cue"] == 0
    assert row["sell"]["n"] == 0
    assert row["net"] == 1


def test_the_same_creator_saying_it_twice_is_one_opinion(seeded_db):
    """c_watch and c_short are the same position on two stored rows."""
    res = board(seeded_db)
    row = next(r for r in res["consensus"] if r["code"] == HAALAND)
    assert row["captain"]["n"] == 1
    assert row["captain"]["n_llm"] == 1, "the duplicate row was counted twice"


# ---------------------------------------------------------------------------
# Params.

def test_a_narrow_window_excludes_older_items(seeded_db):
    """days=1 is narrower than the two-day-old fixture items."""
    res = board(seeded_db, days=1)
    assert res["window_days"] == 1
    assert all(r["n_items_window"] == 0 for r in res["creators"])
    assert all(r["n_claims_window"] == 0 for r in res["creators"])
    # The all-time count and the latest item are NOT window scoped.
    assert _by_name(res)["Notes Only"]["n_items"] == 1
    assert res["consensus"] == []


def test_detail_limit_bounds_the_item_list(seeded_db):
    det = detail(seeded_db, creator="Notes Only", limit=1)
    assert len(det["items"]) == 1


# ---------------------------------------------------------------------------
# player_chatter -- the cross-tab panel.
#
# Mounted in the xPoints and Template drawers, so its hardest case is not a
# rich player, it is the four-in-five players nobody has said a word about.
# Every test below is about a way this panel could quietly say something untrue:
# a watch rendered as a buy, an uncrawled squad rendered as "nobody owns him",
# a measured signal stacked in with a spoken one, or a below-chance panel
# collapsed into a consensus number.

KEEPER = 154561          #: intel only: no claim, no panel member holds him.
TALKER_ENTRY = 53517
QUIET_ENTRY = 424242


def chatter(db, **params):
    return run_script("player_chatter", params, db=db).result


def _keys(obj) -> set:
    """Every key that appears anywhere in a nested payload."""
    found: set = set()
    if isinstance(obj, dict):
        found |= set(obj)
        for v in obj.values():
            found |= _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _keys(v)
    return found


@pytest.fixture()
def chatter_db(seeded_db):
    """The seeded corpus plus a panel, its crawled squads, and measured intel.

    Deliberately asymmetric, because the honest version of this panel is:

    * The Talker has a verified entry id AND a crawled GW1 squad -- he captains
      Haaland, benches Saliba, and does not own Raya. All three are FACTS.
    * Quiet Quentin has a verified entry id and NO crawled squad. Nothing at all
      is known about whether he owns anybody.
    * No Id Ned has no verified entry id, so not even his team is addressable.

    Those three states must never render as the same sentence, and the whole
    ``owned_reason`` string exists to keep them apart.
    """
    wh = Warehouse(seeded_db)
    wh.append("dim_player", pd.DataFrame([
        _player(KEEPER, 30, "Raya", "David", "Raya", 1, team_code=3),
    ]))
    wh.append("fact_player_state", pd.DataFrame([_state(KEEPER, 30, 9.0, 55)]))
    # `enclosure` means the stored "link" IS the mp3: play audio, not open page.
    wh.sql("INSERT INTO content_item_asset VALUES (?, ?, NULL, ?, NULL, ?, ?)",
           ["notes_past", "enclosure",
            "https://example.invalid/ep.mp3", "audio/mpeg", PAST])
    wh.close()

    _plant_panel_people(
        seeded_db,
        [("talker", "The Talker", TALKER_ENTRY),
         ("quiet", "Quiet Quentin", QUIET_ENTRY),
         ("noid", "No Id Ned", None)],
        shows=[("talker", "The Talker"), ("quiet", "Notes Only"),
               ("noid", "Notes Only")],
    )
    # Only The Talker's squad has ever been crawled. Quiet Quentin's has not,
    # and that difference is the whole point of the fixture.
    # Haaland captained, Saliba benched, and Raya owned by nobody at all.
    _plant_picks(seeded_db, TALKER_ENTRY, 1, [(10, 2, True), (20, 0, False)])
    _plant_intel(seeded_db, [
        {"item_id": "oop_saliba", "published_at": PAST,
         "kind": "out_of_position", "player_code": SALIBA,
         "headline": "Saliba is classified DEF but performs like a MID",
         "body": "attacking output at the 100% percentile among DEFs",
         "source": "fpl_edge.intel.oop"},
        {"item_id": "sp_keeper", "published_at": PAST,
         "kind": "set_piece", "player_code": KEEPER,
         "headline": "Raya is listed for corners (#1)", "body": None,
         "source": "fpl_edge.intel.setpiece"},
        # 200 days old and still true. A standing fact does not expire on a
        # 30-day boundary, and windowing it away would hide the most
        # decision-relevant row on the panel.
        {"item_id": "avail_keeper", "published_at": NOW - dt.timedelta(days=200),
         "kind": "availability", "player_code": KEEPER,
         "headline": "Raya returned to full training", "body": None,
         "source": "fpl_edge.intel.injuries"},
        # THE LEAK, intel edition: published two days from now.
        {"item_id": "future_haaland", "published_at": FUTURE,
         "kind": "press_conference", "player_code": HAALAND,
         "headline": "Guardiola confirms Haaland starts tomorrow", "body": None,
         "source": "fpl_edge.intel.presser"},
    ])
    return seeded_db


# -- the shape, pinned from the reader's side -------------------------------

def test_chatter_validates_against_its_own_registered_schema(chatter_db):
    res = chatter(chatter_db, code=HAALAND)
    assert res.get("empty") is not True
    jsonschema.Draft202012Validator(
        script("player_chatter").result_schema
    ).validate(res)


def test_the_chatter_schema_cannot_be_satisfied_by_the_empty_shape():
    inner = script("player_chatter").result_schema["oneOf"][0]
    assert not jsonschema.Draft202012Validator(inner).is_valid(
        {"empty": True, "reason": "nothing yet"}
    )


def test_an_element_id_passed_as_a_player_code_is_an_honest_empty(chatter_db):
    """10 is Haaland's element_id in this fixture and nobody's player code."""
    res = chatter(chatter_db, code=10)
    assert res.get("empty") is True
    assert "PlayerCode" in res["reason"] and "element_id" in res["reason"]


# -- THE MODAL CASE: nobody has said anything ------------------------------

def test_a_player_nobody_has_mentioned_is_the_modal_case_and_explains_itself(
        chatter_db):
    """Four players in five have no claim at all. That is the normal state.

    It must not render as an error, must not render as a blank, and must not
    render as "no", because "nobody has said anything about him" and "nobody
    rates him" are different statements and only the first one is true.
    """
    # GW3: the only thing anybody said about Saliba is a watch call ABOUT GW3,
    # and the default gameweek here is GW2. Asking for the wrong week is a
    # different sentence from nobody speaking -- see the gameweek block below.
    res = chatter(chatter_db, code=SALIBA, gw=3)
    assert res.get("empty") is not True, "silence is not an empty panel"
    said_actions = {s["action"] for s in res["said"]}
    assert said_actions == {"watch"}, "the only thing said about him is a watch"
    assert res["said_reason"] is None or isinstance(res["said_reason"], str)

    # ...and a player nobody has spoken about and nobody read owns still gets
    # a reason per channel rather than one blank card.
    quiet = chatter(chatter_db, code=KEEPER)
    assert quiet["said"] == []
    assert quiet["owned"] == []
    assert "mentioned" in quiet["said_reason"]
    # The count is measured, not remembered: it names the real coverage of the
    # real table rather than a number that was true the day this was written.
    assert "of the 3 players in the pool" in quiet["said_reason"]


def test_a_player_absent_from_all_three_channels_says_so_once(chatter_db):
    """No claim, no intel, and not in the one squad that has been read."""
    wh = Warehouse(chatter_db)
    wh.sql("DELETE FROM intel_item WHERE player_code = ?", [KEEPER])
    wh.close()
    res = chatter(chatter_db, code=KEEPER)
    assert res["said"] == [] and res["noticed"] == [] and res["owned"] == []
    assert res["reason"], "an all-empty panel must say why"
    for phrase in ("panel squad", "mentioned", "intel"):
        assert phrase in res["reason"]


# -- A WATCH IS AN OBSERVATION ---------------------------------------------

def test_a_watch_call_never_appears_as_a_recommendation(chatter_db):
    """`watch` is unmapped in the claim writer, so it lives only in the analysis.

    A panel that reads `content_claim` alone drops every watch call silently.
    A panel that reads the analysis without honouring the stance renders
    "keep an eye on Saliba" under a transfers-out heading, which attributes a
    sell to a named show that it never made.
    """
    res = chatter(chatter_db, code=SALIBA, gw=3)
    watches = [s for s in res["said"] if s["action"] == "watch"]
    assert len(watches) == 1, "the watch call reached the panel"
    call = watches[0]
    assert call["is_observation"] is True
    assert all(s["is_observation"] for s in res["said"]), (
        "a watch was rendered beside a recommendation without the flag"
    )
    assert res["counts"]["observations"] == 1
    assert res["counts"]["said"] == 1
    # Its quote is verbatim and its timestamp is real: the analysis sits on the
    # `youtu.be/` row and the transcript on the `watch?v=` row, so only
    # canonicalisation pairs them.
    assert call["quote"] == "keep an eye on Saliba, he could be a differential"
    assert call["start_s"] == 60.0
    assert call["deep_link"] == f"https://www.youtube.com/watch?v={VIDEO_ID}&t=60s"


# -- NOTICED: measured, separate, and not windowed -------------------------

def test_noticed_is_present_when_said_is_empty(chatter_db):
    """intel_item reaches players the creator corpus never does.

    286 players against 119 in the live warehouse. If the panel only rendered
    when somebody had spoken, the drawer would be blank for the majority of
    players it can actually say something measured about.
    """
    res = chatter(chatter_db, code=KEEPER)
    assert res["said"] == [], "nobody has said a word about him"
    assert [n["kind"] for n in res["noticed"]] == ["set_piece", "availability"]
    assert res["counts"]["noticed"] == 2
    assert res["noticed_reason"] and "measured" in res["noticed_reason"]


def test_a_standing_intel_row_is_not_dropped_by_the_window(chatter_db):
    """The availability row is 200 days old and the window is 30.

    A set-piece or out-of-position finding is a standing fact. Windowing it
    away would hide "first-choice penalties" because it was recorded in May,
    which is the single most decision-relevant row the table holds.
    """
    res = chatter(chatter_db, code=KEEPER, days=30)
    kinds = [n["kind"] for n in res["noticed"]]
    assert "availability" in kinds
    assert "NOT limited to the window" in res["noticed_reason"]
    # ...and it carries its own timestamp, so the reader can age it.
    old = next(n for n in res["noticed"] if n["kind"] == "availability")
    assert old["published_at"] is not None


def test_said_and_noticed_are_never_merged(chatter_db):
    """One is spoken, the other is computed. Stacking them launders the second.

    The two lists have disjoint shapes on purpose: nothing in `noticed` carries
    an action or a conviction, and nothing in `said` carries a kind. A UI that
    tried to render them through one component would fail loudly rather than
    silently presenting a percentile calculation as somebody's opinion.
    """
    res = chatter(chatter_db, code=SALIBA, gw=3)
    assert res["said"] and res["noticed"]
    assert all("kind" not in s for s in res["said"])
    assert all("action" not in n and "conviction" not in n
               for n in res["noticed"])
    assert all(n["source"].startswith("fpl_edge.intel") for n in res["noticed"])


def test_an_intel_row_published_after_the_requested_instant_is_invisible(
        chatter_db):
    """The leakage rule applies to the measured channel too."""
    res = chatter(chatter_db, code=HAALAND)
    assert not any(n["kind"] == "press_conference" for n in res["noticed"]), (
        "an intel row published two days from now reached the panel"
    )


# -- OWNED: unread is not unowned ------------------------------------------

def test_owned_leads_with_a_measured_pick(chatter_db):
    """A pick is a fact with a deadline on it -- no caveat needed."""
    res = chatter(chatter_db, code=HAALAND)
    assert [o["person"] for o in res["owned"]] == ["The Talker"]
    held = res["owned"][0]
    assert held["entry_id"] == TALKER_ENTRY
    assert held["multiplier"] == 2 and held["role"] == "captain"
    assert held["gw"] == 1
    # A benched pick is owned at 0x, and "bench" is read from the stored
    # multiplier rather than inferred from a slot number.
    benched = chatter(chatter_db, code=SALIBA)["owned"][0]
    assert benched["multiplier"] == 0 and benched["role"] == "bench"
    # The instant the squad LOCKED, not the instant this panel ran.
    assert held["as_of"] is not None and held["as_of"] < res["as_of"]


def test_an_uncrawled_squad_and_a_squad_that_does_not_hold_him_are_different(
        chatter_db):
    """The failure this whole reason string exists to prevent.

    Raya is NOT in the one panel squad that has been read: that is a
    measurement, and `squads_known` is 1. Nobody's squad has been read in the
    second case: that is an absence of measurement, and `squads_known` is 0.
    Both produce `owned: []`, and rendering them the same way tells a reader
    "no panel member owns him" when the truth is "we have not looked".
    """
    read_but_not_held = chatter(chatter_db, code=KEEPER)
    assert read_but_not_held["owned"] == []
    assert read_but_not_held["counts"]["squads_known"] == 1
    assert "1 of 2 panel members" in read_but_not_held["owned_reason"]
    assert "Quiet Quentin" in read_but_not_held["owned_reason"], (
        "the member whose squad is UNREAD has to be named"
    )
    assert "UNREAD" in read_but_not_held["owned_reason"]
    assert "No Id Ned" in read_but_not_held["owned_reason"]

    # The SAME roster with nothing crawled at all. Only the picks go away.
    wh = Warehouse(chatter_db)
    wh.sql("DELETE FROM fact_manager_pick")
    wh.close()
    nothing_read = chatter(chatter_db, code=KEEPER)
    assert nothing_read["owned"] == []
    assert nothing_read["counts"]["squads_known"] == 0
    assert "0 of 2 panel members" in nothing_read["owned_reason"]
    assert "The Talker" in nothing_read["owned_reason"], (
        "with nothing crawled, EVERY member is named as unread"
    )
    assert (read_but_not_held["owned_reason"]
            != nothing_read["owned_reason"]), (
        "an unread squad and a squad that does not hold him read identically"
    )


def test_the_panel_size_counts_people_not_show_appearances(chatter_db):
    res = chatter(chatter_db, code=HAALAND)
    assert res["counts"]["panel_size"] == 3
    assert res["counts"]["squads_known"] == 1


# -- NO CONSENSUS SCORE ----------------------------------------------------

def test_the_panel_emits_no_net_and_no_consensus_score(chatter_db):
    """The refusal IS the feature.

    Every earned creator weight in the warehouse is 0.0 across all 330 rows and
    the aggregate record is 34.6% -- below chance. Counting agreement into one
    number manufactures exactly the authority this panel exists to decline, so
    no such field may exist anywhere in the payload or its schema.
    """
    res = chatter(chatter_db, code=HAALAND)
    banned = {"net", "consensus", "score", "agreement", "sentiment", "weight"}
    assert not (_keys(res) & banned), f"a consensus field appeared: {_keys(res) & banned}"
    assert not (_keys(script("player_chatter").result_schema) & banned)
    # Volume is an honest ordering and it is all `counts` reports.
    assert set(res["counts"]) == {"said", "observations", "owned", "noticed",
                                  "panel_size", "squads_known", "said_all_gw"}


# -- provenance of a statement ---------------------------------------------

def test_a_keyword_window_and_a_considered_take_stay_distinguishable(chatter_db):
    res = chatter(chatter_db, code=HAALAND)
    by_show = {s["show"]: s for s in res["said"]}
    assert by_show["The Talker"]["extractor"] == "llm:claude-opus-5"
    assert by_show["The Talker"]["conviction"] == "high"
    assert by_show["Notes Only"]["extractor"] == "cue"
    # A cue score is keyword-window arithmetic, not the speaker's certainty.
    assert by_show["Notes Only"]["conviction"] is None
    assert by_show["Notes Only"]["confidence"] == 0.8


def test_the_same_video_stored_twice_is_one_statement(chatter_db):
    """c_watch and c_short are the same captain call on two stored rows."""
    res = chatter(chatter_db, code=HAALAND)
    captains = [s for s in res["said"] if s["action"] == "captain"]
    assert len(captains) == 1, "one publication, one statement"
    assert captains[0]["start_s"] == 12.5
    assert captains[0]["deep_link"] == (
        f"https://www.youtube.com/watch?v={VIDEO_ID}&t=12s"
    )


def test_a_show_is_not_a_person_until_an_item_is_attributed(chatter_db):
    """`item_person` is empty, so every statement belongs to the SHOW.

    The FPL Wire has four hosts with four different teams. Filling `person` in
    from "the show has a panel member on it" would attribute a call to somebody
    who may not have been in the room.
    """
    res = chatter(chatter_db, code=HAALAND)
    assert all(s["person"] is None for s in res["said"])
    assert all(s["person_basis"] is None for s in res["said"])
    assert "item_person" in res["said_reason"]
    assert all(s["show"] for s in res["said"]), "the show is always known"


def test_an_enclosure_url_is_flagged_as_audio_not_a_page(chatter_db):
    """353 of 387 asset rows are enclosures: the "link" is the mp3 itself."""
    res = chatter(chatter_db, code=HAALAND)
    notes = next(s for s in res["said"] if s["show"] == "Notes Only")
    assert notes["url_basis"] == "enclosure"
    talker = next(s for s in res["said"] if s["show"] == "The Talker")
    assert talker["url_basis"] is None, "unknown is not 'link'"


def test_a_narrow_window_hides_statements_but_not_measured_intel(chatter_db):
    res = chatter(chatter_db, code=SALIBA, days=1)
    assert res["window_days"] == 1
    assert res["said"] == [], "the two-day-old watch call is outside the window"
    assert res["noticed"], "intel is a standing fact and is not windowed"


# ---------------------------------------------------------------------------
# creator_board, extended for the Deadline Board.

def test_panel_owned_counts_only_the_squads_that_were_read(chatter_db):
    res = board(chatter_db)
    row = next(r for r in res["consensus"] if r["code"] == HAALAND)
    assert row["panel_owned"] == {"n": 1, "of": 1, "people": ["The Talker"]}
    meta = res["panel_squads"]
    assert meta["panel_size"] == 3 and meta["with_entry"] == 2
    assert meta["known"] == 1 and meta["gw"] == 1
    assert meta["unknown_people"] == ["Quiet Quentin"]
    assert meta["no_entry_people"] == ["No Id Ned"]
    assert "UNREAD" in meta["reason"]


def test_a_squad_that_cannot_be_read_yields_null_not_false(chatter_db):
    """`in_squad: false` is a claim and it needs a squad read behind it.

    Printing `false` for every row when nothing was read tells the owner "he is
    not in your team" 39 times over, with no evidence for any of it.
    """
    res = board(chatter_db)
    assert all(r["mine"]["in_squad"] is None for r in res["consensus"])
    assert res["mine_reason"] and "unreadable" in res["mine_reason"]


def test_the_caller_can_decline_the_squad_read_and_is_told_so(chatter_db):
    res = board(chatter_db, mine=False)
    assert all(r["mine"]["in_squad"] is None for r in res["consensus"])
    assert "disabled by the caller" in res["mine_reason"]


def test_mine_reports_the_role_and_says_a_multiplier_was_derived(
        chatter_db, monkeypatch):
    """A pre-deadline picks payload carries no multiplier, so it is derived.

    Derived from the scoring rule itself -- bench 0x, starter 1x, captain 2x --
    not guessed, and `source` says which it was so the UI never presents a
    derivation as a reading.
    """
    class _P:
        def __init__(self, code, cap, starter):
            self.code, self.is_captain, self.is_starter = code, cap, starter

    class _State:
        chips_used = ()
        gw = 2
        provenance = "PUBLIC_PICKS"

        def __init__(self):
            self.picks = [_P(HAALAND, True, True), _P(SALIBA, False, False)]

    class _Router:
        def __init__(self, *a, **k): pass
        def _team_state(self): return _State()

    from fpl_edge.interfaces import qa
    monkeypatch.setattr(qa, "QuestionRouter", _Router)

    res = board(chatter_db)
    assert res["mine_reason"] is None
    row = next(r for r in res["consensus"] if r["code"] == HAALAND)
    assert row["mine"] == {"in_squad": True, "multiplier": 2,
                           "role": "captain", "source": "derived"}
    # A player who really is not in the squad gets an honest False.
    absent = next((r for r in res["consensus"] if r["code"] != HAALAND), None)
    if absent is not None:
        assert absent["mine"]["in_squad"] is False


class TestTheScopeScopesTheBoardNotJustTheList:
    """Scoping the creator list but not the claims puts a true label on untrue
    content.

    This shipped exactly that way: the page said "the panel across 7 shows"
    above a board where 48 of 58 voices came from the 24 sources it listed as
    excluded, and 32 of 39 rows carried no panel voice at all. The UI draws the
    board from `consensus`, which is built from `claims` -- and only `names`
    was being filtered. Neither the scope nor the leak had a single test, which
    is why it could ship.
    """

    def test_a_non_panel_creators_claim_never_reaches_the_consensus(
            self, seeded_db):
        _plant_panel_member(seeded_db, "The Talker", 53517, show="The Talker")
        got = board(seeded_db, scope="panel")
        shows = set((got.get("scope") or {}).get("shows") or [])
        assert shows, "the roster must be readable or this proves nothing"
        for row in got.get("consensus") or []:
            voices = set()
            for side in ("buy", "sell", "captain"):
                voices |= set((row.get(side) or {}).get("creators") or [])
            off = voices - shows
            assert not off, (
                f"{row.get('name')} carries voices from outside the panel: "
                f"{sorted(off)} -- the board is not scoped to the label above it"
            )

    def test_asking_for_everything_still_returns_everything(self, seeded_db):
        _plant_panel_member(seeded_db, "The Talker", 53517, show="The Talker")
        narrow = board(seeded_db, scope="panel")
        wide = board(seeded_db, scope="all")
        assert len(wide.get("consensus") or []) >= len(
            narrow.get("consensus") or []), (
            "`all` must not be narrower than `panel`; the wider corpus is "
            "un-defaulted, never hidden"
        )


class TestAWatchNeverRidesInTheBuyList:
    """The analysis stores a call in whichever list it arose in, and 41 `watch`
    stances arrived inside `transfers_in`.

    Rendering those under a transfer heading put buys in named people's mouths
    -- FPL Harry saying "watch Semenyo" was displayed as Harry recommending
    him. `_take` lifts them into `watching`. That lift had no test: neutering
    it left all 57 tests in this file green, so the fix could have been undone
    silently at any time.
    """

    @staticmethod
    def _take_from(calls):
        from fpl_edge.platform.scripts import creators as C

        analysis = {"summary": ["s"], "transfers_in": calls,
                    "transfers_out": [], "captaincy": [], "differentials": []}

        class _Idx:
            def find(self, _quote):
                return None

        class _Res:
            def lookup(self, name):
                return (1, "ok")

            def find_mentions(self, *_a, **_k):
                return []

        return C._take(analysis, "claude-opus-5", _Res(), _Idx(), None)

    def test_a_watch_stored_among_the_buys_is_lifted_out_of_them(self):
        take = self._take_from([
            {"player": "Semenyo", "stance": "watch", "conviction": "low",
             "quote": "keep an eye on Semenyo"},
            {"player": "Haaland", "stance": "buy", "conviction": "high",
             "quote": "get Haaland in"},
        ])
        buys = [c["name"] for c in take["transfers_in"]]
        watching = [c["name"] for c in take["watching"]]
        assert "Semenyo" not in buys, (
            "a watch is an observation; rendering it under transfers_in "
            "attributes a recommendation nobody made"
        )
        assert "Haaland" in buys
        assert "Semenyo" in watching

    def test_the_lifted_call_remembers_where_it_was_raised(self):
        take = self._take_from([
            {"player": "Semenyo", "stance": "watch", "conviction": "low",
             "quote": "keep an eye on Semenyo"},
        ])
        assert take["watching"][0]["raised_in"] == "transfers_in", (
            "'watch, raised while discussing transfers in' says more than "
            "'watch' alone"
        )


# ---------------------------------------------------------------------------
# The gameweek axis.
#
# `content_claim.gameweek` has always held the gameweek a statement was ABOUT
# (267 GW1 claims, 174 GW2, 37 GW3, 21 GW4 in the live warehouse) and nothing
# read it back on a per-player basis. `player_chatter` was windowed by DAYS,
# which rots in two directions at once: while GW2 is live a 30-day window shows
# GW2 talk beside GW3 talk with nothing distinguishing them, and once GW3
# arrives the GW2 statements age out of the window entirely rather than staying
# findable. Days and gameweeks are two axes and the tests below exist to keep
# them from being conflated again.

def _plant_old_take_about_gw2(db):
    """A statement made 90 days ago that is still ABOUT GW2.

    This is the row that makes the two axes distinguishable: any test where
    "recent" and "about this gameweek" coincide proves nothing about which one
    the code is actually using.
    """
    old = NOW - dt.timedelta(days=90)
    wh = Warehouse(db)
    _item(wh, "old_row", "yt_notes", "Notes Only", "youtube", "Season preview",
          "https://www.youtube.com/watch?v=OLDvid00001", old, "description")
    _claim(wh, "c_old_gw2", "old_row", "Notes Only", "yt_notes", HAALAND,
           "Erling Haaland", "buy", old, gw=2)
    wh.close()


def _plant_undated_watch(db):
    """A watch call the model never dated, on an item published two days ago.

    A watch is unmapped in the claim writer, so nothing stamped a gameweek on
    it at ingest -- which is exactly the row that tempts a reader-side rule of
    its own.
    """
    undated = {
        "summary": ["Undated."],
        "transfers_in": [], "transfers_out": [], "captaincy": [],
        "differentials": [{
            "player": "Erling Haaland", "stance": "watch",
            "conviction": "medium", "reasoning": "No gameweek named.",
            "quote": "undated thought about Haaland",
        }],
    }
    wh = Warehouse(db)
    _item(wh, "undated_row", "yt_notes", "Notes Only", "youtube", "Rambles",
          "https://www.youtube.com/watch?v=UNDATEDvid1", PAST, "description")
    wh.sql("INSERT INTO content_analysis VALUES (?, ?, ?, ?)",
           ["undated_row", "claude-opus-5", PAST, json.dumps(undated)])
    wh.close()


def test_the_player_strip_defaults_to_the_next_gameweek(chatter_db):
    """Same default as the board above it, through the same resolver."""
    res = chatter(chatter_db, code=HAALAND)
    assert res["gw"] == 2
    assert "next gameweek" in res["gw_reason"]
    assert all(s["gameweek"] == 2 for s in res["said"])


def test_every_statement_carries_the_gameweek_it_was_about(chatter_db):
    """`published_at` is not it. The UI has to be able to label and group.

    Without this the strip can only sort by recency, which is the wrong axis:
    a GW3 preview recorded on Monday and a GW2 post-mortem recorded on Tuesday
    sort with the post-mortem on top and nothing on either row says which week
    it is talking about.
    """
    res = chatter(chatter_db, code=HAALAND, gw=2)
    assert res["said"], "the fixture's captain claim is a GW2 claim"
    for row in res["said"]:
        assert row["gameweek"] == 2
        assert row["gameweek_basis"] in {"stated", "inferred", None}
    # `_claim` writes gw_inferred = false, so the gameweek was stored as
    # stated and is not re-derived here.
    assert {r["gameweek_basis"] for r in res["said"]} == {"stated"}


def test_an_undated_watch_call_is_dated_by_the_ingesters_own_rule(chatter_db):
    """A watch never becomes a claim, so nothing stamps its gameweek.

    Reading it back needs the SAME rule the claim extractor used -- the first
    gameweek whose deadline falls after publication -- or one video's dated buy
    and undated watch land in two different gameweeks and the panel disagrees
    with itself about what that video was about.
    """
    _plant_undated_watch(chatter_db)
    res = chatter(chatter_db, code=HAALAND, gw=2)
    call = next(s for s in res["said"]
                if s["quote"] == "undated thought about Haaland")
    # PAST is two days ago; GW1's deadline is six days ago and GW2's is
    # tomorrow, so the first deadline after publication is GW2's.
    assert call["gameweek"] == 2
    assert call["gameweek_basis"] == "inferred", (
        "an inferred gameweek must never be presented as one the speaker named"
    )
    # ...and it is absent from GW1, which is the point of dating it at all.
    assert not any(s["quote"] == "undated thought about Haaland"
                   for s in chatter(chatter_db, code=HAALAND, gw=1)["said"])


def test_days_does_not_bound_a_gameweek_scoped_read(chatter_db):
    """A claim made three weeks ago ABOUT GW3 is a GW3 claim.

    Letting a day window also apply to a gameweek-scoped read drops statements
    for a reason no reader could ever guess, and it is what made the old strip
    rot: once GW2 was played its talk aged out of the window rather than
    staying findable under GW2.
    """
    _plant_old_take_about_gw2(chatter_db)
    res = chatter(chatter_db, code=HAALAND, gw=2, days=1)
    quotes = {s["item_title"] for s in res["said"]}
    assert "Season preview" in quotes, (
        "a 90-day-old statement about GW2 is still a GW2 statement; `days` "
        "must not bound a gameweek-scoped read"
    )
    assert "does not bound" in res["gw_reason"]


def test_gw_all_is_the_escape_hatch_where_days_still_bounds(chatter_db):
    """The old behaviour stays reachable, but only when asked for by name."""
    _plant_old_take_about_gw2(chatter_db)
    wide = chatter(chatter_db, code=HAALAND, gw="all", days=365)
    narrow = chatter(chatter_db, code=HAALAND, gw="all", days=1)
    assert wide["gw"] is None and narrow["gw"] is None
    assert "no gameweek filter" in wide["gw_reason"]
    assert any(s["item_title"] == "Season preview" for s in wide["said"])
    assert not any(s["item_title"] == "Season preview" for s in narrow["said"]), (
        "`gw: all` is the one mode where `days` is the bound"
    )
    # And it blames the right control. Saying "this is a gameweek filter"
    # when the day window did the hiding sends the reader to the wrong one.
    assert "outside that window" in narrow["said_reason"]
    assert "day window, not silence" in narrow["said_reason"]
    assert "gameweek filter" not in narrow["said_reason"]


def test_a_gameweek_filter_never_hides_that_other_gameweeks_have_content(
        chatter_db):
    """"Nothing for GW2" and "nobody rates him" are different statements.

    Saliba's only visible statement is a watch call about GW3. Asked for the
    default gameweek the strip is empty -- and an empty strip under a gameweek
    selector reads as silence unless it says, with a count, where the talk
    actually is.
    """
    res = chatter(chatter_db, code=SALIBA, gw=2)
    assert res["said"] == []
    assert res["said_by_gw"] == [{"gw": 3, "n": 1}], (
        "the census is computed before the filter, or it cannot contradict it"
    )
    assert "1 for GW3" in res["said_reason"]
    assert "not silence" in res["said_reason"]
    assert res["counts"]["said"] == 0
    assert res["counts"]["said_all_gw"] == 1


def test_the_census_counts_every_gameweek_whatever_the_filter(chatter_db):
    """`said_by_gw` must not move when `gw` does, or it is just `said` again."""
    _plant_old_take_about_gw2(chatter_db)
    seen = [chatter(chatter_db, code=HAALAND, gw=g)["said_by_gw"]
            for g in (1, 2, 3)]
    seen.append(chatter(chatter_db, code=HAALAND, gw="all", days=1)["said_by_gw"])
    assert all(c == seen[0] for c in seen), (
        "the census is the thing the filter is filtering; it cannot itself be "
        "filtered or it can never tell the reader what they are not seeing"
    )
    assert {b["gw"]: b["n"] for b in seen[0]}[2] >= 2


def test_a_gameweek_nobody_spoke_about_is_not_an_empty_corpus(chatter_db):
    res = chatter(chatter_db, code=HAALAND, gw=38)
    assert res["said"] == []
    assert "gameweek filter, not silence" in res["said_reason"]
    assert res["said_by_gw"], "the other gameweeks still have to be visible"


def test_an_all_empty_player_still_separates_the_week_from_the_world(chatter_db):
    """KEEPER is in no claim at all: that reason must NOT blame the filter."""
    res = chatter(chatter_db, code=KEEPER, gw=2)
    assert res["said"] == [] and res["said_by_gw"] == []
    assert "nor for any other gameweek" in res["said_reason"]


# ---------------------------------------------------------------------------
# creator_detail: which gameweek's team am I looking at?

def _talker_with_two_squads(db):
    """The Talker with a verified entry and TWO crawled gameweeks.

    One gameweek was the whole bug: `_squad` answered `max(gw)` and labelled it
    in prose, so a reader scrolling GW1's claims saw GW2's team above them and
    nothing in the payload could tell the UI otherwise.
    """
    _plant_panel_member(db, "The Talker", 53517)
    _plant_picks(db, 53517, 1, [(10, 2, True), (20, 1, False)])
    _plant_picks(db, 53517, 2, [(20, 2, True)])
    return db


def test_the_squad_is_selectable_by_gameweek(seeded_db):
    _talker_with_two_squads(seeded_db)
    gw1 = detail(seeded_db, creator="The Talker", gw=1)
    gw2 = detail(seeded_db, creator="The Talker", gw=2)

    assert gw1["squad_gw"] == 1 and gw2["squad_gw"] == 2
    assert gw1["squad_gws"] == gw2["squad_gws"] == [1, 2]
    assert {p["name"] for p in gw1["squad"]} == {"Haaland", "Saliba"}
    assert {p["name"] for p in gw2["squad"]} == {"Saliba"}
    # A pick is a fact about a deadline, and the captaincy moved between them.
    assert next(p for p in gw1["squad"] if p["is_captain"])["name"] == "Haaland"
    assert next(p for p in gw2["squad"] if p["is_captain"])["name"] == "Saliba"
    assert "GW1" in gw1["squad_reason"] and "requested explicitly" in gw1["squad_reason"]


def test_the_default_squad_is_the_newest_that_actually_locked(seeded_db):
    """Not literally the next gameweek: a squad is public only at its deadline.

    GW2's deadline has not passed in this fixture, so defaulting to it the way
    `creator_board` defaults would empty this panel every week of the season.
    The default is the same intent -- the team you are reading about -- against
    a table that can only ever be behind the calendar.
    """
    _plant_panel_member(seeded_db, "The Talker", 53517)
    _plant_picks(seeded_db, 53517, 1, [(10, 2, True)])
    det = detail(seeded_db, creator="The Talker")
    assert det["squad_gw"] == 1
    assert det["squad_gws"] == [1]
    assert "newest squad that has actually locked" in det["squad_reason"]
    assert "GW2's deadline has not passed" in det["squad_reason"]


def test_a_gameweek_with_no_crawled_squad_names_the_ones_that_have_one(seeded_db):
    """A bare empty sends the reader nowhere. Say which weeks ARE readable."""
    _plant_panel_member(seeded_db, "The Talker", 53517)
    _plant_picks(seeded_db, 53517, 1, [(10, 2, True)])
    det = detail(seeded_db, creator="The Talker", gw=3)
    assert det["squad"] is None
    assert det["squad_gw"] == 3
    assert det["squad_gws"] == [1], "the readable gameweeks are still named"
    assert "GW1 is on file" in det["squad_reason"]
    assert "has not been crawled" in det["squad_reason"]


def test_a_creator_with_no_entry_still_reports_the_gameweek_keys(seeded_db):
    """The two new keys are REQUIRED, so the null path has to populate them."""
    det = detail(seeded_db, creator="The Talker")
    assert det["squad"] is None
    assert det["squad_gw"] is None and det["squad_gws"] == []
    jsonschema.Draft202012Validator(
        script("creator_detail").result_schema
    ).validate(det)
