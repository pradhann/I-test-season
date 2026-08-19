"""Squad commands for the Telegram bot, without forking it.

The idea bot in :mod:`fpl_edge.interfaces.telegram` is owned by another team and
does one job well: turn a message into a falsifiable thesis. Squad entry is a
different job with a different failure mode -- an idea logged wrong costs one
row in a scoreboard, a squad captured wrong silently poisons every
recommendation for the rest of the season -- so it is handled here and installed
onto the existing bot rather than merged into it.

:func:`install` wraps ``bot._dispatch``. Messages this module claims are handled
here; everything else falls through untouched, so the idea inbox keeps working
exactly as before and there is a single long-polling loop rather than two bots
fighting over the same token.

Two things it claims:

* the commands ``/myteam``, ``/setsquad``, ``/confirm``, ``/discard`` and
  ``/sync``;
* a bare message that looks like a pasted squad -- eleven or more lines that
  resolve to distinct real players. That heuristic exists because the actual
  user journey is "paste the team from the app", and a fifteen-line list of
  footballers arriving at an idea inbox is not an idea. The threshold is
  deliberately high, and the bot says which reading it took and how to override
  it.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fpl_edge.config import USER
from fpl_edge.myteam.manual import build_draft, reconcile, split_fragments
from fpl_edge.myteam.sources import PublicEntryClient
from fpl_edge.myteam.state import PlayerIndex, reconstruct
from fpl_edge.myteam.store import DEFAULT_ROOT, MyTeamStore, NoSuchDraftError

log = logging.getLogger(__name__)
UTC = dt.timezone.utc

COMMANDS = frozenset({"/myteam", "/setsquad", "/confirm", "/discard", "/sync"})

#: How many resolvable player names in one message before it is read as a squad
#: rather than as an idea. Fifteen is a squad; the slack is for a stray header
#: or a name the resolver wants to ask about.
SQUAD_SNIFF_THRESHOLD = 11

HELP = (
    "Squad commands:\n"
    "  /setsquad  then paste your 15, any order, one per line or comma separated\n"
    "  /confirm <token>   save the squad I showed you back\n"
    "  /discard   throw away an unconfirmed squad\n"
    "  /myteam    what I know about your team, and what I cannot know\n"
    "  /sync      switch to FPL's published picks once a gameweek has started\n"
    "\n"
    "You only need to do this once. FPL does not publish anyone's picks until "
    "the gameweek kicks off, and the endpoint that would show them beforehand "
    "needs your password -- which I do not have and will never ask for. After "
    "GW1 starts I read the public picks endpoint and this becomes automatic."
)


@dataclass
class MyTeamCommands:
    """The squad half of the bot's command table.

    Holds no chat state beyond what is on disk: the pending draft lives in the
    store, so a bot restart between "here is my squad" and "confirm" does not
    lose it and does not silently save it either.
    """

    warehouse: Any
    entry_id: int = USER.entry_id
    season: str = "2026-27"
    store_root: Path = DEFAULT_ROOT
    client_factory: Callable[[], PublicEntryClient] = PublicEntryClient
    #: Injected in tests so a fixed instant produces a fixed answer.
    now: Callable[[], dt.datetime] = lambda: dt.datetime.now(UTC)

    @property
    def store(self) -> MyTeamStore:
        return MyTeamStore(self.entry_id, root=self.store_root)

    # -- dispatch ------------------------------------------------------------

    def claims(self, text: str) -> bool:
        token = text.strip().split()[0].lower() if text.strip() else ""
        if token in COMMANDS:
            return True
        return self._looks_like_a_squad(text)

    def _looks_like_a_squad(self, text: str) -> bool:
        fragments = split_fragments(text)
        if len(fragments) < SQUAD_SNIFF_THRESHOLD:
            return False
        try:
            index, resolver = self._resolver()
        except Exception:  # noqa: BLE001 - no warehouse is not a squad message
            return False
        from fpl_edge.myteam.manual import resolve_fragment

        codes = {
            r.code for r in (resolve_fragment(f, resolver, index) for f in fragments)
            if r.code is not None
        }
        return len(codes) >= SQUAD_SNIFF_THRESHOLD

    def handle(self, text: str) -> str:
        stripped = text.strip()
        token = stripped.split()[0].lower() if stripped else ""
        rest = stripped[len(token):].strip()
        if token == "/setsquad":
            return self.set_squad(rest)
        if token == "/confirm":
            return self.confirm(rest)
        if token == "/discard":
            return "Discarded." if self.store.discard() else "Nothing was waiting."
        if token == "/myteam":
            return self.show()
        if token == "/sync":
            return self.sync()
        # Sniffed as a squad.
        return self.set_squad(stripped)

    # -- commands ------------------------------------------------------------

    def _resolver(self):
        from fpl_edge.interfaces.parsing import PlayerResolver

        snapshot = self.warehouse.snapshot_at(self.now())
        index = PlayerIndex.from_snapshot(snapshot, self.season)
        return index, PlayerResolver(snapshot.players(self.season))

    def set_squad(self, text: str) -> str:
        if not text.strip():
            return (
                "Send /setsquad followed by your 15, or just paste them. Any order, "
                "one per line or comma separated. If two players share a surname, "
                "add the price or position -- 'Hughes (MID)' or 'Hughes 4.5'."
            )
        index, resolver = self._resolver()
        gw = self._next_gw()
        draft = build_draft(
            text, resolver=resolver, index=index, entry_id=self.entry_id,
            season=self.season, gw=gw, now=self.now(), source="telegram",
        )
        body = draft.render(index)
        if draft.record is None:
            return body
        self.store.stage(draft)
        return body

    def confirm(self, token: str) -> str:
        if not token:
            pending = self.store.pending()
            if pending is None:
                return "Nothing is waiting to be confirmed. Send me your 15 first."
            return (
                f"Reply `confirm {pending.digest}` -- with the token, so an old "
                "draft cannot be saved by accident."
            )
        try:
            record = self.store.confirm(token.split()[0], now=self.now())
        except NoSuchDraftError as exc:
            return str(exc)
        index, _ = self._resolver()
        names = ", ".join(index.name.get(c, str(c)) for c in record.codes)
        return (
            f"Saved your {record.season} GW{int(record.gw)} squad:\n{names}\n\n"
            "I will reconcile this against FPL's published picks the moment the "
            "gameweek kicks off, and tell you if they disagree."
        )

    def show(self) -> str:
        state = self._state()
        index, _ = self._resolver()
        lines = [state.render()]
        if state.picks:
            lines.append("")
            lines.append(f"Team value {state.team_value(index.price_now)}")
            for pick in sorted(state.picks, key=lambda p: p.order):
                tag = "" if pick.is_starter else "(bench) "
                lines.append(f"  {tag}{pick.position.name} {index.name[pick.code]}")
        else:
            lines.append("")
            lines.append("Send /setsquad and paste your 15 to fix that.")
        return "\n".join(lines)

    def sync(self) -> str:
        record = self.store.confirmed(season=self.season)
        with self.client_factory() as client:
            history = client.history(self.entry_id)
            last = int(history.last_gw) if history.last_gw is not None else 0
            if last < 1:
                return (
                    "No gameweek has finished, so FPL publishes no picks yet. The "
                    "squad you gave me is still the only source there is."
                )
            picks = client.picks(self.entry_id, last)
            if picks is None:
                return f"GW{last} picks are not public yet."
            index, _ = self._resolver()
            codes = [index.code(p.element) for p in picks.picks]
            if record is None:
                return (
                    f"GW{last} picks are public ({len(codes)} players). Reading them "
                    "automatically from here; no manual squad to compare against."
                )
            transfers = client.transfers(self.entry_id)
            between = sum(1 for t in transfers if int(record.gw) <= int(t.gw) <= last)
            return reconcile(
                record, codes, gw=last, transfers_between=between
            ).render(index)

    # -- helpers -------------------------------------------------------------

    def _state(self):
        with self.client_factory() as client:
            return reconstruct(
                self.warehouse.snapshot_at(self.now()),
                entry_id=self.entry_id,
                season=self.season,
                client=client,
                manual=self.store.confirmed(season=self.season),
            )

    def _next_gw(self) -> int:
        try:
            return int(self.warehouse.snapshot_at(self.now()).next_gw(self.season))
        except KeyError:
            return 1


def install(bot: Any, commands: MyTeamCommands) -> Any:
    """Add the squad commands to a running :class:`TelegramBot`.

    Wraps ``_dispatch`` rather than editing the bot, so the idea inbox is
    untouched and there is still exactly one poller. Returns the bot for
    chaining.
    """
    original = bot._dispatch

    def dispatch(text: str, *, chat_id: int, now: dt.datetime | None = None) -> str:
        try:
            if commands.claims(text):
                return commands.handle(text)
        except Exception:  # noqa: BLE001 - a squad failure must not eat the message
            log.exception("myteam command failed")
            return (
                "Something went wrong handling that as a squad. Nothing was saved. "
                "Try /myteam to see what I currently believe."
            )
        return original(text, chat_id=chat_id, now=now)

    bot._dispatch = dispatch  # type: ignore[method-assign]
    return bot
