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
    "differentials": [],
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


def _plant_panel_member(db, display_name, entry_id):
    """Create the panel registry the entry lookup is waiting for.

    ``dim_panel_member`` does not exist yet (another team owns Stage A of
    docs/platform/CREATOR_ELITE_PROMPT.md). The panel must degrade to
    ``entry: null`` + reason without it and light up when it appears, so the
    test creates it with the documented columns.
    """
    wh = Warehouse(db)
    wh.sql(
        "CREATE TABLE IF NOT EXISTS dim_panel_member ("
        " member_key VARCHAR, display_name VARCHAR, kind VARCHAR,"
        " entry_id BIGINT, id_source_url VARCHAR, id_verified_utc TIMESTAMPTZ,"
        " verified_entry_name VARCHAR, weight DOUBLE, active BOOLEAN,"
        " as_of TIMESTAMPTZ)"
    )
    wh.sql(
        "INSERT INTO dim_panel_member VALUES (?, ?, 'creator', ?, ?, ?, ?, "
        "0.7, true, ?)",
        ["talker", display_name, entry_id, "https://example.invalid/proof",
         PAST, "Talker FC", PAST],
    )
    wh.close()


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
        assert row["entry_reason"] and "dim_panel_member" in row["entry_reason"]
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

    assert det["entry"] == {
        "entry_id": 53517, "name": "Talker FC", "verified": True,
        "source_url": "https://example.invalid/proof",
    }
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
    assert res["gw_reason"] is None


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
