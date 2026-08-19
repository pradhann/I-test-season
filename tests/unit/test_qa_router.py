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
    ("Fetch the latest from FPL Wire and summarize", "creator_fetch"),
    ("what's the latest from Let's Talk FPL?", "creator_fetch"),
    ("Which fixtures to target next?", "fixtures_target"),
    ("which players are essential for the next 5 GWs", "fixtures_target"),
    ("How is my team different from the top fantasy managers", "vs_elite"),
    ("compare my squad vs elite managers", "vs_elite"),
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


def test_creator_name_extraction() -> None:
    r = QuestionRouter(wh=None)
    intent = next(i for i in r.intents if i.name == "creator_fetch")
    m = intent.pattern.search("Fetch the latest from FPL Wire and summarize")
    assert m and m.group("src").strip() == "FPL Wire"
    m2 = intent.pattern.search("latest from Let's Talk FPL?")
    assert m2 and "Talk FPL" in m2.group("src")


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
