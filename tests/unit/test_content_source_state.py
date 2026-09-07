"""Per-source STATE: the replacement for calling 25 live feeds "excluded".

What is pinned here is the distinction the panel was getting wrong on
2026-09-03: 39 of 41 registered sources had answered HTTP 200 that day and 25
of them were being listed as excluded. Each test below is one of the states
that list was collapsing.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.ingest.content import source_state as ss
from fpl_edge.ingest.content.sources import ALL_SOURCES, BY_KEY, AccessPolicy, Source
from fpl_edge.store.warehouse import Warehouse

UTC = dt.UTC
NOW = dt.datetime(2026, 9, 3, 18, 0, tzinfo=UTC)

#: ``Warehouse.append`` only knows point-in-time tables, and these are not
#: among them, so rows go in with a plain INSERT.
def _insert(wh, table, rows):
    for row in rows:
        cols = ", ".join(row)
        marks = ", ".join("?" * len(row))
        wh.sql(f"INSERT INTO {table} ({cols}) VALUES ({marks})",
               list(row.values()))


def _source_row(key, **kw):
    src = BY_KEY[key]
    row = {"source_key": key, "creator": src.creator, "kind": str(src.kind),
           "url": src.url, "policy": str(src.policy), "note": src.note or None,
           "last_probe_utc": None, "last_http_status": None,
           "last_items": None, "last_error": None}
    row.update(kw)
    return row


def _item_row(item_id, key, published, fetched, creator=None):
    src = BY_KEY.get(key)
    return {"item_id": item_id, "source_key": key,
            "creator": creator or (src.creator if src else key),
            "kind": str(src.kind) if src else "link", "title": "t",
            "url": "https://example.invalid/x", "published_at": published,
            "fetched_at": fetched, "text_source": "description",
            "text": "body", "text_sha256": "0" * 64}


@pytest.fixture()
def wh(tmp_path):
    path = tmp_path / "content.duckdb"
    warehouse = Warehouse(path)
    warehouse.sql(
        "CREATE TABLE content_source (source_key VARCHAR PRIMARY KEY, "
        "creator VARCHAR, kind VARCHAR, url VARCHAR, policy VARCHAR, "
        "note VARCHAR, last_probe_utc TIMESTAMPTZ, last_http_status INTEGER, "
        "last_items INTEGER, last_error VARCHAR)")
    warehouse.sql(
        "CREATE TABLE content_item (item_id VARCHAR PRIMARY KEY, "
        "source_key VARCHAR, creator VARCHAR, kind VARCHAR, title VARCHAR, "
        "url VARCHAR, published_at TIMESTAMPTZ, fetched_at TIMESTAMPTZ, "
        "text_source VARCHAR, text VARCHAR, text_sha256 VARCHAR)")
    yield warehouse
    warehouse.close()


def _states(wh, window_days=2):
    return {r.key: r for r in ss.source_states(wh, now=NOW,
                                               window_days=window_days)}


def test_a_source_fetched_today_with_a_new_item_is_live(wh):
    _insert(wh, "content_source", [_source_row(
        "pod_fplwire", last_probe_utc=NOW - dt.timedelta(hours=6),
        last_http_status=200, last_items=1)])
    _insert(wh, "content_item", [_item_row(
        "i1", "pod_fplwire", NOW - dt.timedelta(hours=8),
        NOW - dt.timedelta(hours=6))])
    row = _states(wh)["pod_fplwire"]
    assert row.state == ss.SourceState.LIVE
    assert row.n_items == 1 and row.n_items_window == 1
    assert row.can_fetch_now is True


def test_a_healthy_feed_with_no_recent_upload_is_quiet_not_excluded(wh):
    """THE bug. 200 today, nothing published: working, not excluded."""
    _insert(wh, "content_source", [_source_row(
        "pod_fplpod", last_probe_utc=NOW - dt.timedelta(hours=6),
        last_http_status=200, last_items=0)])
    _insert(wh, "content_item", [_item_row(
        "i2", "pod_fplpod", NOW - dt.timedelta(days=9),
        NOW - dt.timedelta(days=9))])
    row = _states(wh)["pod_fplpod"]
    assert row.state == ss.SourceState.QUIET
    assert row.n_items == 1 and row.n_items_window == 0
    assert "answering" in row.reason
    assert row.can_fetch_now is True


def test_nothing_touching_a_source_for_days_is_stale(wh):
    _insert(wh, "content_source", [_source_row(
        "pod_fmlfpl", last_probe_utc=NOW - dt.timedelta(days=9),
        last_http_status=200, last_items=0)])
    _insert(wh, "content_item", [_item_row(
        "i3", "pod_fmlfpl", NOW - dt.timedelta(days=30),
        NOW - dt.timedelta(days=9))])
    assert _states(wh)["pod_fmlfpl"].state == ss.SourceState.STALE


def test_an_ingest_that_wrote_items_counts_as_having_been_fetched(wh):
    """last_probe_utc is only written by `probe`; ingest writes fetched_at.

    Reading only the probe column called sources STALE that had landed an
    item an hour earlier, which is the same class of error as calling a
    working feed excluded.
    """
    _insert(wh, "content_source", [_source_row(
        "pod_ffhub", last_probe_utc=NOW - dt.timedelta(days=30),
        last_http_status=200, last_items=2)])
    _insert(wh, "content_item", [_item_row(
        "i4", "pod_ffhub", NOW - dt.timedelta(hours=3),
        NOW - dt.timedelta(hours=1))])
    assert _states(wh)["pod_ffhub"].state == ss.SourceState.LIVE


def test_a_reachable_source_that_never_yielded_an_item_is_empty(wh):
    _insert(wh, "content_source", [_source_row(
        "blog_fplreview", last_probe_utc=NOW - dt.timedelta(hours=6),
        last_http_status=200, last_items=0)])
    row = _states(wh)["blog_fplreview"]
    assert row.state == ss.SourceState.EMPTY
    assert "has ever been stored" in row.reason


def test_a_non_200_probe_is_failing(wh):
    _insert(wh, "content_source", [_source_row(
        "yt_fpltom", last_probe_utc=NOW - dt.timedelta(hours=2),
        last_http_status=403, last_items=0)])
    row = _states(wh)["yt_fpltom"]
    assert row.state == ss.SourceState.FAILING
    assert "403" in row.reason


def test_a_source_nothing_has_ever_touched_is_unprobed(wh):
    assert _states(wh)["yt_fplfran"].state == ss.SourceState.UNPROBED


def test_policy_blocked_sources_are_blocked_and_never_offered_a_fetch(wh):
    rows = _states(wh)
    for key in ("x_fpl", "reddit_fantasypl"):
        assert rows[key].state == ss.SourceState.BLOCKED
        assert rows[key].can_fetch_now is False
        # The recorded reason travels with the state; the panel never has to
        # invent copy for why a source is off.
        assert rows[key].reason, f"{key} is blocked with no recorded reason"


def test_user_shared_is_legacy_not_an_excluded_source(wh):
    """The owner: "user-shared is legacy name". It is a paste, not a feed."""
    _insert(wh, "content_item", [        _item_row("l1", "user_link", NOW - dt.timedelta(days=8),
                  NOW - dt.timedelta(days=8), creator="user-shared"),])
    row = _states(wh)["user_link"]
    assert row.state == ss.SourceState.LEGACY
    assert row.can_fetch_now is False
    assert "pasted" in row.reason and "not an excluded source" in row.reason


def test_a_disabled_source_is_disabled_and_never_fetchable():
    """The mechanism for a creator we want and have no verified feed for."""
    src = Source("pod_nofeed", "Nobody", BY_KEY["pod_fplpod"].kind,
                 "https://example.invalid/none", enabled=False,
                 disabled_reason="no feed found in the iTunes Search API")
    state, reason = ss._classify(src, {}, {}, NOW)
    assert state == ss.SourceState.DISABLED
    assert "iTunes" in reason
    assert state not in ss.FETCHABLE_STATES


def test_a_disabled_source_must_record_why():
    with pytest.raises(ValueError, match="no recorded reason"):
        Source("x", "Y", BY_KEY["pod_fplpod"].kind, "https://e.invalid",
               enabled=False)


def test_every_registered_source_gets_exactly_one_state(wh):
    rows = ss.source_states(wh, now=NOW)
    keys = [r.key for r in rows]
    assert len(keys) == len(set(keys)), "a source was classified twice"
    assert {s.key for s in ALL_SOURCES} <= set(keys)
    counts = ss.state_counts(rows)
    assert sum(counts.values()) == len(rows)
    for row in rows:
        assert row.state in {str(s) for s in ss.SourceState}
        assert row.reason, f"{row.key} has a state with no reason"
        assert row.label


def test_fpl_fran_is_registered_with_a_verified_channel():
    """The owner asked for her by name; the URL is the one that answered 200."""
    fran = [s for s in ALL_SOURCES if s.creator == "FPL Fran"]
    assert fran, "FPL Fran is not registered"
    assert len(fran) == 1
    assert fran[0].key == "yt_fplfran"
    assert fran[0].url == "https://www.youtube.com/@FPLFran/videos"
    assert fran[0].channel_id == "UCVLnEmwu-Ajei-wvk9SA8rw"
    assert fran[0].policy is AccessPolicy.OPEN and fran[0].enabled


def test_fpl_fran_is_on_the_regularly_fetched_tier():
    from fpl_edge.ingest.content.sources import fast_tier

    assert "yt_fplfran" in {s.key for s in fast_tier()}
