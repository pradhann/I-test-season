"""The transcription relevance gate: deterministic, auditable, and recorded.

GPU minutes follow worth (PIPELINES.md §4.4). The two behaviours that would
hurt if they broke:

1. **The counter-example.** A generic non-FPL episode must score below the
   bar, and a "GW3 wildcard drafts" episode must score above it -- otherwise
   the gate is either a rubber stamp or a wall.
2. **A named reason, not silence.** A below-threshold item gets a
   ``content_transcribe_skip`` row reading ``relevance:<score>`` with the
   point breakdown; it must never simply vanish from the queue.

No network, no model weights, no real ASR backend anywhere in this file.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib

import pandas as pd
import pytest

from fpl_edge.ingest.content import asr
from fpl_edge.ingest.content.pipeline import (
    RELEVANCE_THRESHOLD,
    cmd_transcribe,
    relevance_score,
)
from fpl_edge.ingest.content.resolve import resolver_for
from fpl_edge.store import Warehouse

UTC = dt.UTC
NOW = dt.datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


@pytest.fixture()
def resolver():
    return resolver_for(pd.DataFrame([
        {"code": 1, "web_name": "Haaland", "first_name": "Erling",
         "second_name": "Haaland"},
        {"code": 2, "web_name": "M.Salah", "first_name": "Mohamed",
         "second_name": "Salah"},
    ]))


# -- the scorer --------------------------------------------------------------


def test_a_generic_non_fpl_episode_scores_below_the_bar(resolver):
    """THE counter-example: nothing FPL about it, nothing passes."""
    score, why = relevance_score(
        title="Sunday league grassroots coaching special",
        text="We chat about coaching kids and community pitches this week.",
        resolver=resolver, creator="Random Pod",
        published_at=NOW - dt.timedelta(days=40), now=NOW,
    )
    assert score < RELEVANCE_THRESHOLD
    assert score == 0.0
    assert why == "nothing matched"


def test_a_gameweek_wildcard_episode_scores_above_the_bar(resolver):
    score, why = relevance_score(
        title="GW3 wildcard drafts",
        text="Erling Haaland captain pick, and is Mohamed Salah the transfer "
             "target of the gameweek?",
        resolver=resolver, creator="FPL Harry",  # on the curated panel
        published_at=NOW - dt.timedelta(days=1), now=NOW,
    )
    assert score >= RELEVANCE_THRESHOLD
    # every point is named: 2 players + gameweek/transfer/captain/chip terms
    # + panel + recency
    assert "players:2" in why and "panel" in why and "recent" in why
    assert "gameweek" in why and "chip" in why


def test_the_score_is_deterministic(resolver):
    kwargs = {
        "title": "GW3 wildcard drafts", "text": "Erling Haaland captain pick",
        "resolver": resolver, "creator": "FPL Harry",
        "published_at": NOW - dt.timedelta(days=1), "now": NOW,
    }
    assert relevance_score(**kwargs) == relevance_score(**kwargs)


def test_distinct_players_count_once_each(resolver):
    """Nine mentions of one player is one player's worth of relevance."""
    once, _ = relevance_score(title="", text="Erling Haaland", resolver=resolver)
    nine, _ = relevance_score(
        title="", text=" and ".join(["Erling Haaland"] * 9), resolver=resolver)
    assert once == nine


def test_term_categories_count_once_each(resolver):
    one, _ = relevance_score(title="", text="transfer", resolver=resolver)
    many, _ = relevance_score(
        title="", text="transfer transfers transfer talk", resolver=resolver)
    assert one == many


def test_no_resolver_is_stated_not_guessed():
    score, why = relevance_score(title="GW3 preview", text="", resolver=None)
    assert "players:unavailable" in why
    assert score > 0  # the gameweek term still counts


# -- the wiring: below-threshold items get a recorded reason ------------------


def _content_db(tmp_path):
    from fpl_edge.ingest.content.store import ContentStore

    wh = Warehouse(tmp_path / "gate.duckdb")
    ContentStore(wh)
    asr.ensure_schema(wh)

    def item(item_id, creator, title, text, published):
        wh.sql(
            "INSERT INTO content_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [item_id, "pod_x", creator, "podcast", title,
             f"https://example.test/{item_id}", published, published,
             "description", text, hashlib.sha256(text.encode()).hexdigest()],
        )

    item("relevant", "FPL Harry", "GW3 wildcard drafts",
         "Captain picks and transfer targets for the gameweek.",
         dt.datetime.now(UTC) - dt.timedelta(days=1))
    item("irrelevant", "Random Pod", "Sunday league grassroots special",
         "We chat about coaching kids and community pitches.",
         dt.datetime(2020, 1, 1, tzinfo=UTC))
    wh.close()
    return tmp_path / "gate.duckdb"


def _args(db, **over):
    base = {"db": str(db), "delay": 1.0, "limit": None, "since": 0,
            "budget_s": 0.0, "kinds": "podcast", "creator": None,
            "any_creator": True, "model": None, "dry_run": False,
            "min_relevance": RELEVANCE_THRESHOLD}
    base.update(over)
    return argparse.Namespace(**base)


def _ready_backend(monkeypatch):
    monkeypatch.setattr(
        asr, "backend_status",
        lambda: asr.BackendStatus(mlx_whisper=True, decoder="pyav"))


def test_below_threshold_items_get_a_relevance_skip_row(tmp_path, monkeypatch):
    db = _content_db(tmp_path)
    _ready_backend(monkeypatch)

    assert cmd_transcribe(_args(db)) == 0

    with Warehouse(db) as wh:
        skips = {r.item_id: (r.reason, r.detail) for r in wh.sql(
            "SELECT item_id, reason, detail FROM content_transcribe_skip"
        ).itertuples(index=False)}
    reason, detail = skips["irrelevant"]
    assert reason == "relevance:0"
    assert "below threshold" in detail
    # The relevant item was NOT relevance-gated: it reached the audio stage
    # and was skipped there for having no enclosure -- a different, honest
    # reason.
    assert skips["relevant"][0] == "no_audio_url"


def test_a_relevance_skip_is_never_requeued(tmp_path, monkeypatch):
    db = _content_db(tmp_path)
    _ready_backend(monkeypatch)
    assert cmd_transcribe(_args(db)) == 0
    with Warehouse(db) as wh:
        before = len(wh.sql("SELECT * FROM content_transcribe_skip"))
    # second run: both items already in the skip ledger, queue is empty
    assert cmd_transcribe(_args(db)) == 0
    with Warehouse(db) as wh:
        assert len(wh.sql("SELECT * FROM content_transcribe_skip")) == before


def test_min_relevance_zero_disables_the_gate(tmp_path, monkeypatch):
    db = _content_db(tmp_path)
    _ready_backend(monkeypatch)
    assert cmd_transcribe(_args(db, min_relevance=0.0)) == 0
    with Warehouse(db) as wh:
        reasons = set(wh.sql(
            "SELECT reason FROM content_transcribe_skip")["reason"])
    assert reasons == {"no_audio_url"}  # nothing relevance-gated


def test_dry_run_scores_but_writes_nothing(tmp_path, monkeypatch):
    db = _content_db(tmp_path)
    _ready_backend(monkeypatch)
    assert cmd_transcribe(_args(db, dry_run=True)) == 0
    with Warehouse(db) as wh:
        tables = set(wh.sql(
            "SELECT table_name FROM information_schema.tables")["table_name"])
    assert "content_transcribe_skip" not in tables


# -- the content tier (PIPELINES.md §5 decision 3) ----------------------------


def test_the_fast_tier_is_exactly_the_panels_fetchable_sources():
    from fpl_edge.ingest.content.sources import (
        FAST_TIER,
        NIGHTLY_TIER,
        content_tier,
        fast_tier,
        fetchable,
    )
    from fpl_edge.ingest.content.youtube import PANEL_CREATORS

    fast = fast_tier()
    assert set(fast) <= set(fetchable())
    assert all(s.creator in PANEL_CREATORS for s in fast)
    keys = {s.key for s in fast}
    # a panel creator's podcast AND youtube feeds both ride the fast cadence
    assert {"pod_fplwire", "pod_fplharry", "yt_fplharry",
            "yt_letstalkfpl"} <= keys
    # off-panel sources stay nightly
    assert "pod_athletic" not in keys and "blog_ffscout" not in keys
    by_key = {s.key: s for s in fetchable()}
    assert content_tier(by_key["pod_athletic"]) == NIGHTLY_TIER
    assert content_tier(by_key["pod_fplwire"]) == FAST_TIER


def test_the_fast_rss_task_scopes_to_fast_tier_keys_only(tmp_path, monkeypatch):
    """The 4h task must ingest ONLY the fast tier, with backfill-days 1."""
    import datetime as _dt

    from fpl_edge.ingest.content.sources import fast_tier
    from fpl_edge.jobs import deadline_dag as dag
    from fpl_edge.pipelines import registry

    monkeypatch.setenv("FPL_EDGE_DISABLE_NETWORK_INGEST", "0")
    seen: list[list[str]] = []

    def fake_step(name, argv, **kw):
        seen.append(argv)
        return dag.Step(name=name, ok=True, seconds=0.0)

    monkeypatch.setattr(registry, "run_step", fake_step)
    ctx = dag.TaskContext(season="2026-27", gw=0, due_utc=NOW,
                          deadline_utc=None, now=_dt.datetime.now(_dt.UTC),
                          db_path=tmp_path / "x.duckdb")
    res = registry.run_fast_rss(ctx)
    assert res.outcome == "quiet"
    ingest_argv = seen[0]
    assert "--backfill-days" in ingest_argv
    assert ingest_argv[ingest_argv.index("--backfill-days") + 1] == "1"
    only = ingest_argv[ingest_argv.index("--only") + 1]
    assert set(only.split(",")) == {s.key for s in fast_tier()}
    # and the caption ride-along stays captions-only: youtube kinds, small budget
    captions_argv = seen[1]
    assert "--kinds" in captions_argv
    assert captions_argv[captions_argv.index("--kinds") + 1] == "youtube"
