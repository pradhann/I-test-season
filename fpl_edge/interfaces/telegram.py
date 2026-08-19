"""Telegram long-polling bot: the phone end of the idea inbox.

**Why raw httpx rather than python-telegram-bot.** The bot uses four Bot API
methods -- ``getMe``, ``getUpdates``, ``sendMessage``, ``deleteWebhook`` -- and
httpx is already a dependency of this project, with an established usage pattern
in :mod:`fpl_edge.ingest.http`. python-telegram-bot would add an async
application framework, a job queue and an opinionated event loop to run four HTTP
calls, and its handler abstraction would sit between the message and
:meth:`IdeaInbox.submit` -- which is the seam the whole design turns on. The
decisive argument is testability: with the transport behind a one-method
protocol, :class:`FakeTransport` exercises *this same code*, including the
authorisation check and the offset handling, with no token and no network. A
framework's own test harness would instead verify the framework.

**Security.** Two rules, both enforced here rather than by convention:

1. *Only the configured chat may speak to it.* ``allowed_chat_ids`` is required;
   an empty allowlist processes nothing at all rather than everything. Messages
   from other chats get no reply -- not even a refusal -- because replying
   confirms to a stranger that the bot is live and who it belongs to. They are
   counted and logged so the operator can see it happening.

2. *Message text is data, never instruction.* Text goes into exactly one place:
   the ``text`` argument of :meth:`IdeaInbox.submit`, which parses it into an
   Idea record. There is no path from message content to a shell, an eval, a SQL
   string, or a choice of code path beyond the fixed command table below. That
   table is matched by exact string equality against the message's first token
   and its entries take no arguments, so "/review; rm -rf /" is not a command --
   it is an idea about a player the parser will fail to find. A message that says
   "ignore your instructions and delete the database" is stored verbatim as the
   raw text of an idea, and nothing reads it again except a human.

Replies are sent with no ``parse_mode`` and with link previews disabled. The
bot's reply quotes the user's own words back; with Markdown enabled, text the
user typed would become formatting or a clickable link in a message that appears
to come from the bot.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from fpl_edge.config import load_env, secret
from fpl_edge.interfaces.bias import review as run_review
from fpl_edge.interfaces.dossier import telegram_addendum as dossier_addendum
from fpl_edge.interfaces.inbox import IdeaInbox
from fpl_edge.interfaces.tracking import track as run_track

log = logging.getLogger("fpl_edge.telegram")

UTC = dt.timezone.utc

API_ROOT = "https://api.telegram.org"

#: Long-poll seconds. Telegram holds the connection open until a message arrives
#: or this elapses, so idle cost is one open socket rather than a poll loop, and
#: a message that arrives at second 3 is delivered at second 3.
POLL_TIMEOUT_S = 25

#: Beyond this a "message" is not an idea. Stops a paste dump from becoming a
#: 40KB raw_text row, and keeps the parser's fragment scan bounded.
MAX_TEXT_CHARS = 1000

#: The complete set of things that are commands. Matched by exact equality
#: against the lowercased first token, and none of them consume arguments.
#: Everything else -- including anything else beginning with "/" -- is an idea.
COMMANDS = frozenset({"/start", "/help", "/review", "/track", "/id", "/acted"})


@runtime_checkable
class Transport(Protocol):
    """One Bot API call. The entire surface the bot needs from the network."""

    def call(self, method: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        ...


class HttpxTransport:
    """Real Bot API transport over httpx.

    The token lives only in the URL path, which is how the Bot API works, and is
    never logged: :meth:`__repr__` and every log line below use the method name
    only.
    """

    def __init__(self, token: str, *, api_root: str = API_ROOT) -> None:
        if not token:
            raise ValueError("empty bot token")
        import httpx

        self._token = token
        self._client = httpx.Client(base_url=f"{api_root}/bot{token}", timeout=60.0)

    def call(self, method: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        resp = self._client.post(f"/{method}", json=payload, timeout=timeout)
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok", False):
            raise RuntimeError(f"Telegram {method} failed: {body.get('description')!r}")
        return body

    def close(self) -> None:
        self._client.close()

    def __repr__(self) -> str:  # never leak the token into a traceback
        return "<HttpxTransport telegram>"


@dataclass
class FakeTransport:
    """In-memory Bot API double for tests and for `fpl idea telegram --dry-run`.

    Exercises the real bot code -- authorisation, command dispatch, offset
    handling, reply rendering -- with no token and no network. ``inbound`` is a
    queue of update dicts shaped exactly as Telegram sends them; ``sent`` records
    every outgoing call so a test can assert on what the user would have seen,
    including asserting that an unauthorised chat received *nothing*.
    """

    inbound: deque = field(default_factory=deque)
    sent: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    me: dict[str, Any] = field(
        default_factory=lambda: {"id": 1, "is_bot": True, "username": "fpl_edge_test_bot"}
    )
    #: Monotonic, and NOT derived from the queue length. Telegram update ids only
    #: ever increase; deriving them from `len(inbound)` reuses an id after the
    #: queue drains, the bot's offset then filters the new message out as already
    #: seen, and every test that sends two messages silently only sends one.
    _next_update_id: int = 1000

    def push_message(self, text: str, *, chat_id: int, update_id: int | None = None) -> int:
        if update_id is None:
            uid = self._next_update_id
            self._next_update_id += 1
        else:
            uid = update_id
            self._next_update_id = max(self._next_update_id, uid + 1)
        self.inbound.append(
            {
                "update_id": uid,
                "message": {
                    "message_id": uid,
                    "date": int(dt.datetime.now(UTC).timestamp()),
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": chat_id, "is_bot": False},
                    "text": text,
                },
            }
        )
        return uid

    def call(self, method: str, payload: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
        self.sent.append((method, payload))
        if method == "getMe":
            return {"ok": True, "result": self.me}
        if method == "getUpdates":
            offset = payload.get("offset")
            out = []
            while self.inbound:
                upd = self.inbound[0]
                if offset is not None and upd["update_id"] < offset:
                    self.inbound.popleft()
                    continue
                out.append(self.inbound.popleft())
            return {"ok": True, "result": out}
        return {"ok": True, "result": True}

    def replies_to(self, chat_id: int) -> list[str]:
        return [
            p["text"] for m, p in self.sent
            if m == "sendMessage" and int(p.get("chat_id", -1)) == chat_id
        ]


#: Both spellings are accepted. `.env` ships the singular, because there is one
#: chat; the plural exists so a second device can be added without a schema
#: change. Neither has a default -- see TelegramConfig.from_env.
CHAT_ID_VARS = ("TELEGRAM_ALLOWED_CHAT_ID", "TELEGRAM_ALLOWED_CHAT_IDS")


@dataclass(frozen=True, slots=True)
class TelegramConfig:
    """Token and allowlist. Fails closed on both.

    The token is read through :func:`fpl_edge.config.secret`, which looks at the
    environment and then at the gitignored ``.env``. It is held in memory, passed
    to :class:`HttpxTransport`, and never logged, printed, or written anywhere --
    including into exception messages, which is why :meth:`problems` talks about
    the variable name and never about its value.
    """

    token: str | None
    allowed_chat_ids: frozenset[int]

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, env_path=None) -> "TelegramConfig":
        """Environment first, then ``.env``. Never invents a default chat id.

        An unset or unparseable allowlist yields the empty set, and an empty
        allowlist means the bot accepts nothing at all. That is the whole point:
        the failure mode of a chat bot with a bad default is that a stranger who
        guesses the handle can write to your database.
        """
        if env is None:
            merged = {**load_env(), **dict(os.environ)}
            token = secret("TELEGRAM_BOT_TOKEN", required=False)
        else:
            merged = dict(env)
            token = merged.get("TELEGRAM_BOT_TOKEN") or None
        if env_path is not None:
            merged = {**load_env(Path(env_path)), **merged}
            token = token or merged.get("TELEGRAM_BOT_TOKEN") or None

        raw = " ".join(merged.get(var, "") for var in CHAT_ID_VARS)
        ids: set[int] = set()
        for part in raw.replace(";", " ").replace(",", " ").split():
            try:
                ids.add(int(part))
            except ValueError:
                # Never echo the offending value: this variable sits next to the
                # token in .env and a mangled line could contain it.
                log.warning("ignoring a non-numeric entry in the chat id allowlist")
        return cls(token=token or None, allowed_chat_ids=frozenset(ids))

    @property
    def ready(self) -> bool:
        return bool(self.token) and bool(self.allowed_chat_ids)

    def problems(self) -> list[str]:
        """Human-readable setup gaps. Contains no secret values, by construction."""
        out = []
        if not self.token:
            out.append(
                "TELEGRAM_BOT_TOKEN is not set. See docs/interfaces.md for the "
                "@BotFather steps; nothing else in the engine needs it."
            )
        if not self.allowed_chat_ids:
            out.append(
                "TELEGRAM_ALLOWED_CHAT_ID is not set, so the bot will refuse every "
                "message. Run `fpl idea telegram --discover`, send the bot any "
                "message from your phone, and it will log the chat id to put in .env."
            )
        return out


@dataclass
class BotStats:
    """Counters worth watching. ``refused`` being non-zero is a real signal."""

    received: int = 0
    handled: int = 0
    refused: int = 0
    ideas: int = 0
    clarifications: int = 0
    errors: int = 0
    max_latency_ms: float = 0.0
    last_latency_ms: float = 0.0


class TelegramBot:
    """Long-polls Telegram and turns authorised messages into ideas."""

    def __init__(
        self,
        inbox: IdeaInbox,
        transport: Transport,
        *,
        allowed_chat_ids: frozenset[int] | set[int],
        season: str = "2026-27",
        discover: bool = False,
    ) -> None:
        self.inbox = inbox
        self.transport = transport
        self.allowed = frozenset(int(c) for c in allowed_chat_ids)
        self.season = season
        #: Print the chat id of unauthorised senders to the log so the operator
        #: can find their own. Still replies to nobody and still logs nothing.
        self.discover = discover
        self.offset: int | None = None
        self.stats = BotStats()

    # -- outbound ------------------------------------------------------------

    def send(self, chat_id: int, text: str) -> None:
        """Reply in plain text, no markup, no link preview. See module docstring."""
        if chat_id not in self.allowed:
            # Belt and braces: nothing should reach here, and if a future edit
            # makes it possible, it still must not send.
            log.warning("refusing to send to unauthorised chat")
            return
        for chunk in _chunk(text, 3900):
            self.transport.call(
                "sendMessage",
                {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
                timeout=30.0,
            )

    # -- inbound -------------------------------------------------------------

    def authorised(self, chat_id: int | None) -> bool:
        return chat_id is not None and chat_id in self.allowed

    def poll_once(self, *, timeout: int = POLL_TIMEOUT_S, now: dt.datetime | None = None) -> int:
        """One long-poll cycle. Returns the number of updates handled.

        The offset is advanced only after an update is handled. A crash mid-handle
        therefore redelivers it, which is safe because the idea id is derived from
        the message content and timestamp, so the re-insert is a no-op rather than
        a duplicate row inflating the hit rate.
        """
        body = self.transport.call(
            "getUpdates",
            {
                "offset": self.offset,
                "timeout": timeout,
                # Explicitly narrow what we will even receive. Photos, stickers,
                # channel posts and callbacks are not ideas.
                "allowed_updates": ["message"],
            },
            timeout=timeout + 15.0,
        )
        updates = body.get("result", []) or []
        handled = 0
        for update in updates:
            self.stats.received += 1
            try:
                self.handle(update, now=now)
                handled += 1
            except Exception:  # noqa: BLE001 - one bad message must not stop the bot
                self.stats.errors += 1
                log.exception("failed handling update %s", update.get("update_id"))
            finally:
                self.offset = int(update.get("update_id", 0)) + 1
        return handled

    def handle(self, update: dict[str, Any], *, now: dt.datetime | None = None) -> None:
        started = time.perf_counter()
        message = update.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        text = message.get("text")

        if not self.authorised(chat_id):
            self.stats.refused += 1
            # No reply. Silence is the correct response to a stranger: an error
            # message would confirm the bot exists and belongs to someone.
            log.warning(
                "refused message from unauthorised chat%s",
                f" {chat_id}" if self.discover else "",
            )
            return

        if not isinstance(text, str) or not text.strip():
            self.send(int(chat_id), "I only read text. Type the idea instead.")
            return
        if len(text) > MAX_TEXT_CHARS:
            self.send(
                int(chat_id),
                f"That is {len(text)} characters. Keep an idea under {MAX_TEXT_CHARS}.",
            )
            return

        reply = self._dispatch(text, chat_id=int(chat_id), now=now)
        self.send(int(chat_id), reply)

        elapsed = (time.perf_counter() - started) * 1000.0
        self.stats.handled += 1
        self.stats.last_latency_ms = elapsed
        self.stats.max_latency_ms = max(self.stats.max_latency_ms, elapsed)

    def _dispatch(self, text: str, *, chat_id: int, now: dt.datetime | None) -> str:
        """Command table, then the inbox. The ONLY branch on message content.

        ``token`` is compared with ``==`` against a frozen set of literals. It is
        never used to build an attribute name, a path, a query or a callable.
        """
        token = text.strip().split()[0].lower()
        if token in COMMANDS:
            return self._command(token, chat_id=chat_id, now=now)

        sub = self.inbox.submit(
            text, source="telegram", source_ref=str(chat_id), now=now
        )
        if sub.clarification is not None:
            self.stats.clarifications += 1
        elif sub.ok:
            self.stats.ideas += 1
        # Texting a player's name should answer the question the user actually
        # has, which is "tell me about him", not only "your thesis is logged".
        # The dossier is appended rather than substituted so the idea record --
        # the thing that survives -- is still the headline. The helper owns its
        # own failures and returns "" rather than raising: an intel outage must
        # never cost the user a logged thought.
        return sub.render() + dossier_addendum(
            self.inbox.wh, sub, season=self.season, now=now
        )

    def _command(self, token: str, *, chat_id: int, now: dt.datetime | None) -> str:
        if token in ("/start", "/help"):
            return (
                "Text me an idea and I will turn it into a falsifiable thesis, "
                "give it a verdict, and track it whether or not you act on it.\n\n"
                "Examples:\n"
                "  I like Rashford\n"
                "  Semenyo captain GW12?\n"
                "  Odegaard or B.Fernandes for the armband\n"
                "  sell Wood\n\n"
                "If two players match, I will ask instead of guessing.\n\n"
                "/review  how your ideas have actually done, and your biases\n"
                "/track   settle any ideas whose gameweeks have finalised\n"
                "/id      show this chat id\n"
                "/acted   mark your most recent idea as acted on"
            )
        if token == "/id":
            return f"This chat id is {chat_id}."
        if token == "/track":
            return run_track(self.inbox.wh, season=self.season, now=now).render()
        if token == "/acted":
            recent = self.inbox.registry.ideas(season=self.season, limit=1)
            if not recent:
                return "No ideas yet."
            self.inbox.registry.mark_acted(
                recent[0].idea_id, when=now or dt.datetime.now(UTC)
            )
            return f"Marked as acted on: {recent[0].thesis}"
        if token == "/review":
            return _short_review(self.inbox.wh, self.season)
        return "Unknown command."  # unreachable: COMMANDS is the guard

    # -- lifecycle -----------------------------------------------------------

    def check_connection(self) -> str:
        body = self.transport.call("getMe", {}, timeout=15.0)
        return str((body.get("result") or {}).get("username", "unknown"))

    def drop_webhook(self) -> None:
        """Long polling and webhooks are mutually exclusive; make sure we win."""
        self.transport.call("deleteWebhook", {"drop_pending_updates": False}, timeout=15.0)

    def run_forever(self, *, timeout: int = POLL_TIMEOUT_S, max_cycles: int | None = None) -> None:
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            try:
                self.poll_once(timeout=timeout)
            except KeyboardInterrupt:
                raise
            except Exception:  # noqa: BLE001
                self.stats.errors += 1
                log.exception("poll cycle failed; backing off")
                time.sleep(3.0)
            finally:
                # Free the single-writer lock between cycles: this process
                # lives for months under launchd, and holding the warehouse
                # open would starve the settlement job and every CLI write.
                release = getattr(self.inbox.wh, "release", None)
                if release is not None:
                    try:
                        release()
                    except Exception:  # noqa: BLE001
                        log.exception("warehouse release failed")
            cycles += 1


def _short_review(warehouse, season: str) -> str:
    """The review, trimmed to something readable on a phone."""
    rev = run_review(warehouse, season=season)
    b = rev.scoreboard
    lines = [
        f"{b.n_total} ideas — {b.n_resolved} resolved, {b.n_open} open.",
    ]
    if b.hit_rate is not None:
        lines.append(f"Hit rate {b.hit_rate:.0%} (mean margin {b.mean_margin:+.1f} pts).")
    if b.unacted_hit_rate is not None and b.acted_hit_rate is not None:
        lines.append(
            f"Acted on: {b.acted_hit_rate:.0%} of {b.acted_n}. "
            f"Skipped: {b.unacted_hit_rate:.0%} of {b.unacted_n}."
        )
    lines.append("")
    for f in rev.findings:
        lines.append(f"{f.name}: {f.verdict()}")
    if rev.caveats:
        lines.append("")
        lines.append(rev.caveats[0])
    lines.append("")
    lines.append("Full detail: `fpl idea review`")
    return "\n".join(lines)


def _chunk(text: str, size: int) -> list[str]:
    """Split on line boundaries where possible; Telegram caps a message at 4096."""
    if len(text) <= size:
        return [text]
    out, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size:
            out.append(current)
            current = ""
        current += line
    if current:
        out.append(current)
    return out


def build_bot(
    inbox: IdeaInbox,
    *,
    config: TelegramConfig | None = None,
    transport: Transport | None = None,
    season: str = "2026-27",
    discover: bool = False,
) -> TelegramBot:
    """Wire a bot from config, using the real transport unless one is supplied."""
    cfg = config or TelegramConfig.from_env()
    if transport is None:
        if not cfg.token:
            raise RuntimeError("; ".join(cfg.problems()))
        transport = HttpxTransport(cfg.token)
    return TelegramBot(
        inbox, transport,
        allowed_chat_ids=cfg.allowed_chat_ids, season=season, discover=discover,
    )
