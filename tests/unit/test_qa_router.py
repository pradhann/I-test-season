"""The question router: intents match, parameters extract, non-questions fall
through to the idea inbox, and answers ship text plus images.

Warehouse-touching handlers are exercised through the routing layer with a
stub, because their data plumbing is covered by their own modules' tests; what
THIS suite pins is the contract the Telegram bot depends on.
"""

from __future__ import annotations

import re

import pytest

from fpl_edge.interfaces.qa import Answer, QuestionRouter


class _Recorder:
    """Stands in for a handler: records the call, returns a canned Answer."""

    def __init__(self, name):
        self.name = name
        self.calls = []

    def __call__(self, text, m):
        self.calls.append((text, m))
        return Answer(f"answered:{self.name}")


@pytest.fixture()
def router():
    r = QuestionRouter(wh=None)
    for intent in r.intents:
        rec = _Recorder(intent.name)
        object.__setattr__(intent, "handler", rec)
    return r


CASES = [
    ("Review my team", "review_team"),
    ("can you review my squad?", "review_team"),
    ("show my team", "review_team"),
    ("Suggest me transfers", "suggest_transfers"),
    ("what transfers should I make?", "suggest_transfers"),
    ("Which defenders have the highest xPoints", "top_by_position"),
    ("best midfielders by expected points?", "top_by_position"),
    ("top forwards for haul potential", "top_by_position"),
    ("Fetch the latest from FPL Wire and summarize", "creator_summary"),
    ("what's the latest from Let's Talk FPL?", "creator_summary"),
    # The three messages that failed live, verbatim from the screenshot:
    ("Find the key ideas from FPl Harry and FPLWire", "creator_summary"),
    ("Who is playing bench boost content creators?", "creator_chip_scan"),
    ("Summarize FPLRaptor", "creator_summary"),
    ("https://youtu.be/dQw4w9WgXcQ check this", "link_ingest"),
    ("Which fixtures to target next?", "fixtures_target"),
    ("which players are essential for the next 5 GWs", "fixtures_target"),
    ("How is my team different from the top fantasy managers", "vs_elite"),
    ("compare my squad vs elite managers", "vs_elite"),
    # Tracked-manager questions: about THEM, not me. The gate is naming a
    # curated manager or the cohort, so "suggest me transfers" stays mine.
    ("what transfers did Ben Crellin make?", "manager_transfers"),
    ("show me the elite transfers", "manager_transfers"),
    ("what do the elite own", "elite_owned"),
    ("most selected players in the top 10k", "elite_owned"),
    ("who do the top 10k captain", "elite_owned"),
    ("What are the ideas I have said for this GW", "ideas_this_gw"),
    ("my ideas this week", "ideas_this_gw"),
]


@pytest.mark.parametrize("text,expected", CASES)
def test_each_question_type_routes(router, text, expected) -> None:
    answer = router.route(text)
    assert answer is not None, f"{text!r} did not route"
    assert answer.text == f"answered:{expected}", (
        f"{text!r} routed to {answer.text}, wanted {expected}"
    )


NON_QUESTIONS = [
    "I like Rashford",
    "Semenyo captain GW12?",
    "fade Watkins",
    "thinking about buying Mbeumo",
    "Baleba looked good in preseason",
]


@pytest.mark.parametrize("text", NON_QUESTIONS)
def test_ideas_fall_through(router, text) -> None:
    """Beliefs are not questions: they must reach the idea inbox untouched."""
    assert router.route(text) is None


def test_creator_matching_handles_typos_and_multiples() -> None:
    from fpl_edge.interfaces.creators import match_creators

    got = match_creators("Find the key ideas from FPl Harry and FPLWire")
    assert got == ["FPL Harry", "The FPL Wire"]
    assert match_creators("Summarize FPLRaptor") == ["FPL Raptor"]
    # Beliefs must not trip the creator gate.
    assert match_creators("I like Rashford") == []
    assert match_creators("Semenyo captain GW12?") == []


def test_handler_failure_is_reported_not_swallowed() -> None:
    r = QuestionRouter(wh=None)
    intent = r.intents[0]

    def boom(text, m):
        raise RuntimeError("warehouse offline")

    object.__setattr__(intent, "handler", boom)
    got = r.route("review my team")
    assert got is not None and "warehouse offline" in got.text


def test_bot_sends_photos_for_answers_with_images(tmp_path) -> None:
    """End to end through the real bot: a routed answer ships its images."""
    from fpl_edge.interfaces.inbox import IdeaInbox
    from fpl_edge.interfaces.telegram import FakeTransport, TelegramConfig, build_bot
    from fpl_edge.store import Warehouse
    import fpl_edge.interfaces.telegram as tg
    import fpl_edge.interfaces.qa as qa

    wh = Warehouse(tmp_path / "t.duckdb")
    cfg = TelegramConfig(token="x", allowed_chat_ids=frozenset({7}))
    transport = FakeTransport()
    bot = build_bot(IdeaInbox(wh, season="2026-27"), config=cfg,
                    transport=transport, season="2026-27")

    class StubRouter:
        def __init__(self, *a, **k): ...
        def route(self, text):
            if "team" in text:
                return qa.Answer("here is your team", images=[("t.png", b"\x89PNG fake")])
            return None

    orig = qa.QuestionRouter
    qa.QuestionRouter = StubRouter
    try:
        transport.push_message("review my team", chat_id=7)
        bot.poll_once(timeout=0)
    finally:
        qa.QuestionRouter = orig

    texts = transport.replies_to(7)
    assert any("here is your team" in t for t in texts)
    assert transport.photos and transport.photos[0]["filename"] == "t.png"


def _seeded_wh(tmp_path):
    """A tiny real warehouse the handlers can actually read."""
    import datetime as dt

    import pandas as pd

    from fpl_edge.store import Warehouse
    from fpl_edge.types import Position

    UTC = dt.timezone.utc
    t0 = dt.datetime(2026, 8, 1, tzinfo=UTC)
    wh = Warehouse(tmp_path / "qa.duckdb")
    layout = ([(Position.GKP, 2), (Position.DEF, 5), (Position.MID, 5), (Position.FWD, 3)])
    players, states, code = [], [], 100
    for pos, n in layout:
        for i in range(n):
            code += 1
            players.append({"season": "2026-27", "code": code, "element_id": code,
                            "web_name": f"{pos.name}{i}", "first_name": "F",
                            "second_name": f"{pos.name}{i}", "position": int(pos),
                            "team_code": 1 + (code % 6), "as_of": t0})
            states.append({"season": "2026-27", "code": code, "element_id": code,
                           "price_tenths": 50, "selected_by_pct": 5.0, "status": "a",
                           "chance_of_playing_next_round": None, "news": "",
                           "news_added": None, "transfers_in_event": 0,
                           "transfers_out_event": 0, "cost_change_start": 0,
                           "as_of": t0})
    wh.append("dim_player", pd.DataFrame(players))
    wh.append("fact_player_state", pd.DataFrame(states))
    wh.append("dim_event", pd.DataFrame([{
        "season": "2026-27", "gw": 1,
        "deadline_utc": dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC),
        "is_finished": False, "as_of": t0}]))
    return wh


def _real_state(wh):
    """A MyTeamState built from the REAL dataclass, not a guess at its shape.

    This test family exists because the first live run crashed on
    `state.squad` -- an attribute the handler author invented. Constructing
    the genuine object makes any future field drift fail here, offline.
    """
    import datetime as dt

    from fpl_edge.eval.scoring import Pick
    from fpl_edge.myteam.state import MyTeamState, Provenance
    from fpl_edge.types import GwId, Position

    snap = wh.snapshot_at(dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc))
    frame = snap.players("2026-27").sort_values("code")
    by_pos = {p: frame[frame["position"] == p]["code"].tolist() for p in (1, 2, 3, 4)}
    order = (by_pos[1][:1] + by_pos[2][:4] + by_pos[3][:4] + by_pos[4][:2]
             + by_pos[1][1:2] + by_pos[2][4:5] + by_pos[3][4:5] + by_pos[4][2:3])
    pos_of = dict(zip(frame["code"], frame["position"]))
    picks = tuple(
        Pick(code=int(c), position=Position(int(pos_of[c])), order=i,
             is_captain=(i == 2), is_vice=(i == 3))
        for i, c in enumerate(order, start=1)
    )
    return MyTeamState(
        entry_id=4490171, season="2026-27", gw=GwId(1),
        as_of=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc),
        picks=picks, bought_at={p.code: 50 for p in picks}, bank_tenths=250,
        free_transfers=1, provenance=Provenance.MANUAL,
    )


def test_review_team_renders_from_a_real_state(tmp_path, monkeypatch) -> None:
    wh = _seeded_wh(tmp_path)
    r = QuestionRouter(wh)
    monkeypatch.setattr(r, "_team_state", lambda: _real_state(wh))
    monkeypatch.setattr(r, "_projection", lambda: None)
    a = r.route("review my team")
    assert a is not None and "understood that as" not in a.text, a.text
    assert a.images and a.images[0][0] == "team.png"
    assert len(a.images[0][1]) > 10_000  # a real PNG, not an error stub


def test_suggest_transfers_reads_the_plan_with_a_real_state(tmp_path, monkeypatch) -> None:
    import json

    wh = _seeded_wh(tmp_path)
    r = QuestionRouter(wh)
    state = _real_state(wh)
    monkeypatch.setattr(r, "_team_state", lambda: state)
    monkeypatch.chdir(tmp_path)
    plan_dir = tmp_path / "data/warehouse"
    plan_dir.mkdir(parents=True)
    (plan_dir / "gw1_plan.json").write_text(json.dumps({
        "generated_at": "2026-08-20T10:00:00+00:00", "objective_mode": "expected_points",
        "gw1": {"squad": [p.code for p in state.picks]}, "notes": [],
    }))
    a = r.route("suggest me transfers")
    assert a is not None and "understood that as" not in a.text, a.text
    assert "Hold" in a.text


def test_creator_summary_reads_the_real_claim_schema(tmp_path, monkeypatch) -> None:
    """The live failure class: handlers written against invented schemas."""
    wh = _seeded_wh(tmp_path)
    wh.sql("""
        CREATE TABLE content_item (
            item_id VARCHAR, source_key VARCHAR, creator VARCHAR, kind VARCHAR,
            title VARCHAR, url VARCHAR, published_at TIMESTAMPTZ, text VARCHAR,
            fetched_at TIMESTAMPTZ, text_source VARCHAR)
    """)
    wh.sql("""
        CREATE TABLE content_claim (
            claim_id VARCHAR, item_id VARCHAR, creator VARCHAR, source_key VARCHAR,
            player_code INTEGER, player_name VARCHAR, surface_form VARCHAR,
            action VARCHAR, season VARCHAR, gameweek INTEGER, confidence DOUBLE,
            rationale VARCHAR, source_url VARCHAR, published_at TIMESTAMPTZ,
            gw_inferred BOOLEAN)
    """)
    wh.sql("""
        CREATE TABLE claim_outcome (
            claim_id VARCHAR, creator VARCHAR, season VARCHAR, gameweek INTEGER,
            player_code INTEGER, action VARCHAR, player_points DOUBLE,
            benchmark VARCHAR, benchmark_points DOUBLE, hit BOOLEAN,
            unscoreable VARCHAR, resolved_utc TIMESTAMPTZ)
    """)
    # published_at is RELATIVE to the clock, not a literal. The chip-talk
    # answer filters to the last 10 days of content, so a hardcoded date is a
    # time bomb: this test was seeded '2026-08-19', passed until 2026-08-29,
    # and went red at the stroke of the window with no diff to explain it --
    # the same failure field_fixtures had with its GW2 deadline. Two days ago
    # is always inside a ten-day window.
    import datetime as dt
    recent = (dt.datetime.now(dt.UTC) - dt.timedelta(days=2)).isoformat()
    wh.sql("""
        INSERT INTO content_item VALUES
        ('i1','pod_fplwire','The FPL Wire','podcast','Ep 1','u',
         ?,'we are on the bench boost train for gw1',
         ?,'transcript')
    """, [recent, recent])
    wh.sql("""
        INSERT INTO content_claim VALUES
        ('c1','i1','The FPL Wire','pod_fplwire',101,'GKP0','gkp0','buy',
         '2026-27',1,0.6,'r','u',?,false)
    """, [recent])
    r = QuestionRouter(wh)
    import fpl_edge.interfaces.creators as cr
    monkeypatch.setattr(cr, "_refresh", lambda keys, **k: False)

    a = r.route("Fetch the latest from FPL Wire and summarize")
    assert a is not None and "understood that as" not in a.text, a.text
    assert "GW1 buy: GKP0" in a.text
    assert "ZERO decision weight" in a.text  # unearned influence stays at zero

    a2 = r.route("Who is playing bench boost content creators?")
    assert a2 is not None and "The FPL Wire" in a2.text and "bboost" in a2.text
