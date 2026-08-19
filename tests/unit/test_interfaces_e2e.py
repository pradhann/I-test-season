"""End to end: a text message becomes a tracked, falsified-or-not thesis.

This is the definition of done for the idea inbox. The requirement is that an
idea texted from a phone is in the registry *with a verdict* inside a minute, so
the test measures wall-clock time across the whole path -- Telegram update in,
parse, thesis, snapshot read, context capture, verdict, four database writes,
reply out -- and asserts against the real budget rather than a proxy for it.

The offline test runs against :class:`FakeTransport`, which is the same bot code
with the socket removed, so it measures everything except network round trip. The
network-marked test at the bottom exercises the real Bot API against the live
@fplpradhannbot and is deselected by default.
"""

from __future__ import annotations

import datetime as dt
import time

import pytest

from fpl_edge.interfaces.bias import review
from fpl_edge.interfaces.ideas import IdeaStatus, Outcome
from fpl_edge.interfaces.inbox import IdeaInbox
from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.interfaces.report import weekly_report
from fpl_edge.interfaces.telegram import FakeTransport, TelegramBot
from fpl_edge.interfaces.testing import SEASON, seed_warehouse
from fpl_edge.interfaces.tracking import track
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

#: The requirement, in seconds.
BUDGET_S = 60.0

MINE = 8782506418

#: Three days before the real 2026-27 GW1 deadline of 2026-08-21T17:30:00Z.
TONIGHT = dt.datetime(2026, 8, 18, 22, 50, tzinfo=UTC)


@pytest.fixture()
def bot(tmp_path) -> TelegramBot:
    wh = seed_warehouse(tmp_path / "e2e.duckdb", n_gws=16)
    return TelegramBot(IdeaInbox(wh), FakeTransport(), allowed_chat_ids={MINE}, season=SEASON)


def test_a_text_message_becomes_a_tracked_thesis_with_a_verdict_in_under_a_minute(
    bot: TelegramBot, capsys
) -> None:
    """The whole point of the project, in one test.

    "Semenyo captain GW12?" is exactly the sort of thing typed one-handed during
    highlights: a nickname-free surname, a gameweek, a question mark, and no
    statement of what would make it right or wrong.
    """
    message = "Semenyo captain GW12?"
    registry = IdeaRegistry(bot.inbox.wh)
    assert registry.count() == 0

    bot.transport.push_message(message, chat_id=MINE)  # type: ignore[attr-defined]

    started = time.perf_counter()
    handled = bot.poll_once(timeout=0, now=TONIGHT)
    elapsed = time.perf_counter() - started

    assert handled == 1

    # 1. It is in the registry.
    assert registry.count() == 1
    idea = registry.ideas(season=SEASON)[0]
    assert idea.raw_text == message
    assert idea.source == "telegram"
    assert idea.source_ref == str(MINE)
    assert idea.created_utc == TONIGHT

    # 2. With a falsifiable thesis: subject, comparator and window.
    assert idea.subject_name == "Semenyo"
    assert idea.gw == 12 and idea.horizon_gws == 1
    assert "Semenyo" in idea.thesis and "median" in idea.thesis and "GW12" in idea.thesis
    assert "Correct if" in idea.resolution_rule

    # 3. With a verdict, from a named and versioned provider.
    verdict = registry.latest_verdict(idea.idea_id)
    assert verdict is not None
    assert 0.0 <= verdict.p_thesis_true <= 1.0
    assert verdict.provider and verdict.provider_version
    assert verdict.confidence in ("low", "medium", "high")
    assert verdict.rationale

    # 4. With the context the bias probes will need in six months.
    context = registry.context(idea.idea_id)
    assert context is not None
    assert context.captured_utc == TONIGHT
    assert context.price_tenths is not None

    # 5. Being tracked, without the user having acted on it.
    assert idea.acted is False
    assert idea.status is IdeaStatus.OPEN

    # 6. And the user was told, in one message.
    reply = bot.transport.replies_to(MINE)[-1]  # type: ignore[attr-defined]
    assert "Logged" in reply and idea.thesis in reply
    assert "Tracking from now, acted on or not." in reply

    assert elapsed < BUDGET_S, f"{elapsed:.3f}s exceeds the {BUDGET_S:.0f}s budget"

    with capsys.disabled():
        print(
            f"\n  message      : {message!r}"
            f"\n  idea_id      : {idea.idea_id}"
            f"\n  thesis       : {idea.thesis}"
            f"\n  verdict      : P(thesis true)={verdict.p_thesis_true:.0%} "
            f"[{verdict.stance}, {verdict.provider} {verdict.provider_version}, "
            f"{verdict.confidence}]"
            f"\n  round trip   : {elapsed * 1000:.1f} ms  (budget {BUDGET_S:.0f}s, "
            f"{BUDGET_S / elapsed:.0f}x headroom)"
            f"\n  verdict cost : {verdict.latency_ms:.1f} ms of that"
        )


def test_the_full_lifecycle_from_message_to_settled_record(tmp_path, capsys) -> None:
    """Message -> thesis -> verdict -> tracked -> settled -> reviewed.

    Runs mid-season so the ideas' windows actually close. The assertion that
    matters at the end is that the idea nobody acted on was scored just as
    completely as the one that was.
    """
    wh: Warehouse = seed_warehouse(tmp_path / "life.duckdb", n_gws=16, finished_gws=14)
    bot = TelegramBot(IdeaInbox(wh), FakeTransport(), allowed_chat_ids={MINE}, season=SEASON)
    registry = IdeaRegistry(wh)
    when = dt.datetime(2026, 9, 1, 21, 40, tzinfo=UTC)  # after GW2 finalised

    for text in ("Haaland captain gw3", "Semenyo captain gw3", "Hi"):
        bot.transport.push_message(text, chat_id=MINE)  # type: ignore[attr-defined]
    bot.poll_once(timeout=0, now=when)

    ideas = registry.ideas(season=SEASON)
    assert len(ideas) == 2, "chatter must not become an idea"

    acted, skipped = ideas[1], ideas[0]
    registry.mark_acted(acted.idea_id, when=when)

    settled = track(wh, season=SEASON, now=dt.datetime(2026, 12, 20, tzinfo=UTC))
    assert settled.resolved == 2

    for idea_id in (acted.idea_id, skipped.idea_id):
        final = registry.get(idea_id)
        assert final is not None
        assert final.status is IdeaStatus.RESOLVED
        assert final.outcome in (Outcome.CORRECT, Outcome.INCORRECT, Outcome.PUSH)
        assert final.subject_points is not None and final.comparator_points is not None

    board = review(wh, season=SEASON).scoreboard
    assert board.n_resolved == 2
    assert board.acted_n + board.unacted_n >= 1

    with capsys.disabled():
        print("\n  lifecycle:")
        for idea_id in (acted.idea_id, skipped.idea_id):
            f = registry.get(idea_id)
            assert f is not None
            mark = "acted on " if f.acted else "SKIPPED  "
            print(
                f"    {mark} {f.thesis[:58]:58s} -> {str(f.outcome):9s} "
                f"({f.subject_points:.0f} vs {f.comparator_points:.0f})"
            )


def test_weekly_report_is_honest_about_what_is_not_built(tmp_path) -> None:
    """The report must name its own gaps rather than look complete."""
    wh = seed_warehouse(tmp_path / "rep.duckdb", n_gws=16)
    IdeaInbox(wh).submit("Semenyo captain GW1", source="cli", now=TONIGHT)

    rendered = weekly_report(wh, season=SEASON, as_of=TONIGHT).render()
    assert "GW1" in rendered
    assert "2026-08-21 17:30Z" in rendered  # the API's UTC deadline, not local
    assert "Semenyo" in rendered
    assert "Not in this report yet" in rendered
    assert "squad" in rendered and "transfers" in rendered


def test_submission_latency_is_reported_to_the_caller(tmp_path) -> None:
    """Every surface gets the measured number, not just the log."""
    wh = seed_warehouse(tmp_path / "lat.duckdb", n_gws=16)
    sub = IdeaInbox(wh).submit("I like Rashford", source="cli", now=TONIGHT)
    assert sub.latency_ms > 0
    assert sub.latency_ms < BUDGET_S * 1000
    assert f"{sub.latency_ms:.0f} ms" in sub.render()


def test_twenty_messages_stay_well_inside_the_budget(bot: TelegramBot, capsys) -> None:
    """One slow submission is a bad evening; a slow ingest rate is a dead inbox."""
    texts = [
        "I like Rashford", "Semenyo captain GW12?", "sell Wood",
        "Odegaard or B.Fernandes for the armband", "van dijk differential",
        "Watkins looks good", "thinking about Gabriel", "Saliba GW4",
        "Haaland captain", "bring in Szoboszlai",
        "rashfrod is due a haul", "gvardiol differential gw6",
        "give the armband to Haaland", "Mainoo looking nailed on",
        "avoid Onana", "Raya or Palmer in goal", "Martinez GW7",
        "I fancy Semenyo for the armband", "buy Odegaard", "drop Wood",
    ]
    assert len(texts) == len(set(texts)) == 20
    for i, text in enumerate(texts):
        bot.transport.push_message(text, chat_id=MINE, update_id=2000 + i)  # type: ignore[attr-defined]

    started = time.perf_counter()
    bot.poll_once(timeout=0, now=TONIGHT)
    elapsed = time.perf_counter() - started

    assert bot.stats.handled == 20
    # A few are legitimately ambiguous ("Raya or Palmer in goal" hits the two
    # Palmers), so the bar is on throughput, not on a perfect parse rate.
    assert bot.inbox.registry.count() >= 15
    assert bot.stats.max_latency_ms < BUDGET_S * 1000
    with capsys.disabled():
        print(
            f"\n  {len(texts)} messages in {elapsed:.2f}s "
            f"({elapsed / len(texts) * 1000:.0f} ms each, "
            f"worst single message {bot.stats.max_latency_ms:.0f} ms)"
        )


# -- against the live Bot API ------------------------------------------------


@pytest.mark.network
def test_live_bot_api_round_trip(tmp_path, capsys) -> None:
    """The same path over the real network, against @fplpradhannbot.

    Deselected by default (``-m 'not network'``). Skipped rather than failed when
    no token is present, so the suite runs on a machine that has never seen the
    .env. Sends nothing: getMe and a zero-timeout getUpdates only, so running it
    cannot spam the user's phone.
    """
    from fpl_edge.interfaces.telegram import HttpxTransport, TelegramConfig

    cfg = TelegramConfig.from_env()
    if not cfg.token:
        pytest.skip("TELEGRAM_BOT_TOKEN not set")

    transport = HttpxTransport(cfg.token)
    try:
        started = time.perf_counter()
        me = transport.call("getMe", {})["result"]
        latency = time.perf_counter() - started

        assert me["is_bot"] is True
        assert me["username"]
        # The token must not be recoverable from the transport's repr, which is
        # what ends up in a traceback.
        assert ":AA" not in repr(transport)

        with capsys.disabled():
            print(
                f"\n  live getMe -> @{me['username']} (id {me['id']}) "
                f"in {latency * 1000:.0f} ms"
                f"\n  allowlist  -> {sorted(cfg.allowed_chat_ids) or 'EMPTY (fails closed)'}"
            )
    finally:
        transport.close()


@pytest.mark.network
def test_live_bot_refuses_a_chat_that_is_not_the_configured_one(tmp_path) -> None:
    """Authorisation is checked against the real configured id, not a test one."""
    from fpl_edge.interfaces.telegram import TelegramConfig, build_bot

    cfg = TelegramConfig.from_env()
    if not cfg.ready:
        pytest.skip("TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHAT_ID not both set")

    wh = seed_warehouse(tmp_path / "live.duckdb", n_gws=16)
    fake = FakeTransport()
    live_config_bot = build_bot(IdeaInbox(wh), config=cfg, transport=fake, season=SEASON)

    fake.push_message("I like Rashford", chat_id=1234)  # not the configured chat
    live_config_bot.poll_once(timeout=0, now=TONIGHT)
    assert live_config_bot.inbox.registry.count() == 0
    assert fake.replies_to(1234) == []

    chat = next(iter(cfg.allowed_chat_ids))
    fake.push_message("I like Rashford", chat_id=chat)
    live_config_bot.poll_once(timeout=0, now=TONIGHT)
    assert live_config_bot.inbox.registry.count() == 1


@pytest.mark.network
def test_offset_semantics_against_the_real_getupdates(capsys) -> None:
    """Reads the live queue without consuming it, and reports what is waiting."""
    from fpl_edge.interfaces.telegram import HttpxTransport, TelegramConfig

    cfg = TelegramConfig.from_env()
    if not cfg.token:
        pytest.skip("TELEGRAM_BOT_TOKEN not set")

    transport = HttpxTransport(cfg.token)
    try:
        updates = transport.call(
            "getUpdates", {"timeout": 0, "allowed_updates": ["message"]}
        )["result"]
        # No offset passed, so nothing is acknowledged and the queue is intact.
        with capsys.disabled():
            print(f"\n  live queue: {len(updates)} pending update(s)")
            for u in updates[:5]:
                msg = u.get("message") or {}
                print(
                    f"    update_id={u['update_id']} "
                    f"chat={(msg.get('chat') or {}).get('id')} "
                    f"text={msg.get('text')!r}"
                )
        assert isinstance(updates, list)
    finally:
        transport.close()
