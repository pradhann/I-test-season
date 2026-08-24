"""The watchlist: players the user wants kept in view until they say stop.

One table, three verbs. ``add`` appends a PIT-stamped row (and resolves any
previous open row for the same player, so history is kept but the list never
shows one player twice). ``open_items`` is what the T-30h presser digest reads,
so every pre-deadline Telegram delivery reminds the user what they said they
wanted. ``resolve`` closes an item without deleting it.

The schema is applied by the same migration runner as the idea registry
(:class:`fpl_edge.interfaces.registry.IdeaRegistry` applies every file in
``fpl_edge/interfaces/migrations/`` idempotently), so constructing a
:class:`Watchlist` against any warehouse -- including a test one -- creates the
table on first use. Name resolution is deliberately NOT here: callers (the MCP
tools, a future bot command) resolve a name to a stable ``code`` through
``sem_players`` with their own ambiguity handling, and this store only ever
sees codes. Notes are untrusted chat text: bound as parameters, never
interpolated, rendered back as plain text.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from fpl_edge.interfaces.registry import IdeaRegistry
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

DEFAULT_SEASON = "2026-27"


def _now(now: dt.datetime | None) -> dt.datetime:
    return (now or dt.datetime.now(UTC)).astimezone(UTC)


class Watchlist:
    """Append/resolve store over the ``watchlist`` table."""

    def __init__(self, warehouse: Warehouse) -> None:
        self.wh = warehouse
        # The interface migration runner owns the DDL; running it here is what
        # makes the table exist on a warehouse that has never seen a watchlist.
        IdeaRegistry(warehouse)

    def add(
        self,
        *,
        code: int,
        player_name: str,
        season: str = DEFAULT_SEASON,
        note: str | None = None,
        source: str = "mcp",
        now: dt.datetime | None = None,
    ) -> str:
        """Put a player on the list. Returns the new item id.

        If the player is already on the open list, the old row is resolved and
        a fresh row is appended -- the list shows one row per player with the
        latest note, and the history of earlier notes survives underneath.
        """
        when = _now(now)
        self.resolve(code=code, season=season, now=when, reason="superseded")
        item_id = f"wl_{when:%Y%m%dT%H%M%S}_{int(code)}"
        self.wh.sql(
            "INSERT INTO watchlist (item_id, created_utc, season, code, player_name, "
            "note, source, resolved, resolved_utc) VALUES (?, ?, ?, ?, ?, ?, ?, FALSE, NULL)",
            [item_id, when, season, int(code), player_name, note, source],
        )
        return item_id

    def open_items(self, season: str = DEFAULT_SEASON) -> pd.DataFrame:
        """Open items, oldest first: item_id, created_utc, code, player_name, note."""
        return self.wh.sql(
            "SELECT item_id, created_utc, code, player_name, note FROM watchlist "
            "WHERE season = ? AND NOT resolved ORDER BY created_utc",
            [season],
        )

    def resolve(
        self,
        *,
        code: int,
        season: str = DEFAULT_SEASON,
        now: dt.datetime | None = None,
        reason: str = "removed",
    ) -> int:
        """Close every open item for this player. Returns how many were closed.

        ``reason`` is recorded nowhere today (the row keeps its note); the
        parameter exists so ``add`` can distinguish supersession from removal
        in a later migration without changing call sites.
        """
        del reason  # see docstring
        when = _now(now)
        n = int(
            self.wh.sql(
                "SELECT count(*) AS n FROM watchlist WHERE season = ? AND code = ? AND NOT resolved",
                [season, int(code)],
            ).iloc[0]["n"]
        )
        if n:
            self.wh.sql(
                "UPDATE watchlist SET resolved = TRUE, resolved_utc = ? "
                "WHERE season = ? AND code = ? AND NOT resolved",
                [when, season, int(code)],
            )
        return n


def digest_lines(wh: Warehouse, season: str) -> list[str]:
    """The watchlist section of a pre-deadline digest, or [] when empty.

    Read-only and exception-free by contract: the caller is a scheduled digest
    task and a missing table (no item ever added, so the migration never ran on
    this warehouse) must read as "no watchlist", not as a failed refresh.
    """
    try:
        items = wh.sql(
            "SELECT player_name, note FROM watchlist "
            "WHERE season = ? AND NOT resolved ORDER BY created_utc",
            [season],
        )
    except Exception:  # noqa: BLE001 - table may not exist yet; see docstring
        return []
    if items.empty:
        return []
    lines = [f"Watchlist ({len(items)} open):"]
    for r in items.itertuples(index=False):
        note = r.note if isinstance(r.note, str) and r.note else None
        lines.append(
            f"  You wanted: {r.player_name}"
            + (f" — '{note[:80]}'" if note else "")
        )
    return lines
