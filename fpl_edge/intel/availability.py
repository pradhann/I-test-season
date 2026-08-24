"""The leak-proof injury feed: FPL's own ``news`` with FPL's own timestamp.

The injury-source survey (2026-08-18, recorded in docs/data_sources.md) 
found premierinjuries behind a Cloudflare challenge (403), physioroom down
(522), Fantasy Football Scout's team-news page substantively paywalled, and the
BBC's robots.txt explicitly forbidding extraction. Its conclusion -- that the
FPL API is the right injury feed rather than a fallback one -- rests on a
property no aggregator has: ``news_added`` states **when the news became
public**, which is precisely the ``published_at`` a point-in-time store needs.

This module turns ``fact_player_state`` rows into :class:`IntelItem` news, one
item per distinct (player, news text, news_added) triple. Two consequences:

* The same injury re-observed on 300 consecutive polls produces one item, not
  300, because the id is a hash of the content and the publication instant.
* An item is dated to ``news_added``, not to the poll. So a snapshot taken
  before the news broke cannot see it, and a snapshot taken after can -- even
  if our poller was asleep at the time, because the world knew regardless.

What this does NOT do is invent a status change with no ``news_added``. When FPL
flags a player without a timestamp the item is dropped and the shortfall is
reported by the caller, rather than being dated to the poll and thereby claiming
a precision the source does not have.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from fpl_edge.intel.bootstrap import parse_news_added
from fpl_edge.intel.items import IntelItem, IntelKind, content_id
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

SOURCE = "fpl_api:bootstrap-static"

#: FPL ``status`` codes, decoded. (The old ingest.injuries module is deleted;
#: local constant so the intel package does not force that module (and its long
#: survey docstring) into the import path of every dossier.
STATUS_MEANING: dict[str, str] = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "not in squad",
}


def _headline(row: pd.Series) -> str:
    status = STATUS_MEANING.get(str(row.get("status") or ""), "unknown status")
    chance = row.get("chance_of_playing_next_round")
    if pd.notna(chance):
        return f"{status} — FPL states {int(chance)}% chance of playing next round"
    return status


def availability_items(
    wh: Warehouse,
    *,
    season: str,
    observed_at: dt.datetime,
    since: dt.datetime | None = None,
) -> tuple[list[IntelItem], dict[str, int]]:
    """Availability news as dated intel items, plus a count of what was skipped.

    ``observed_at`` is when the collector ran. ``published_at`` always comes from
    ``news_added``. The returned counter reports ``undated``: rows carrying news
    text with no ``news_added``, which are deliberately dropped rather than
    stamped with the collector's clock.
    """
    when = observed_at.astimezone(UTC)
    # Read the whole history of the state table, not a single snapshot: a player
    # whose news changed twice should yield two items, and a snapshot read only
    # ever returns the latest row per player.
    clause = "AND as_of >= ?" if since else ""
    params: list[object] = [season]
    if since:
        params.append(since.astimezone(UTC))
    df = wh.sql(
        f"""
        SELECT season, code, status, chance_of_playing_next_round, news, news_added,
               min(as_of) AS first_seen
        FROM fact_player_state
        WHERE season = ? {clause}
        GROUP BY ALL
        """,
        params,
    )
    counts = {"rows": int(len(df)), "undated": 0, "empty": 0}
    if df.empty:
        return [], counts

    items: list[IntelItem] = []
    for _, row in df.iterrows():
        news = row.get("news")
        text = "" if news is None or pd.isna(news) else str(news).strip()
        if not text:
            counts["empty"] += 1
            continue
        published = parse_news_added(
            None if pd.isna(row.get("news_added")) else pd.Timestamp(row["news_added"]).isoformat()
        )
        if published is None:
            counts["undated"] += 1
            continue
        seen = pd.Timestamp(row["first_seen"]).to_pydatetime().astimezone(UTC)
        items.append(
            IntelItem(
                item_id=content_id("avail", int(row["code"]), published.isoformat(), text),
                published_at=published,
                # We knew at the earlier of "when we first stored this row" and
                # "now". Using `when` alone would claim the pipeline noticed a
                # July injury today, which understates our own latency.
                observed_at=max(published, min(seen, when)),
                kind=IntelKind.AVAILABILITY,
                headline=_headline(row),
                body=text,
                source=SOURCE,
                season=str(row["season"]),
                player_code=int(row["code"]),
                confidence=1.0,
            )
        )
    return items, counts


def fresh_news(items: list[IntelItem], *, as_of: dt.datetime, hours: float = 48.0) -> list[IntelItem]:
    """Items published within ``hours`` of ``as_of``.

    The window that matters for a deadline decision. FPL's flags follow a Friday
    press conference by some hours, so anything inside two days is news the
    market may not have fully absorbed; anything older is priced in.
    """
    cutoff = as_of.astimezone(UTC) - dt.timedelta(hours=hours)
    return [i for i in items if i.published_at >= cutoff and i.published_at <= as_of]
