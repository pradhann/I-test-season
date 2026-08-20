"""Reading and acknowledging the delivery outbox.

The table, the DDL and the write path all belong to
:mod:`fpl_edge.jobs.outbox` -- the DAG owns delivery. This module is the *read*
side plus acknowledgement, and it deliberately creates nothing: one delivery
table, one writer of it, or the Inbox and Telegram drift into disagreeing about
what was sent.

Two access patterns, two lock postures, and the difference matters because the
Telegram bot under launchd is holding write leases the whole time:

* **Listing** is a read and goes through a private read copy, so a browser tab
  polling the Inbox never blocks an ingest.
* **Acking** is a write and therefore needs the real lock. It takes a
  :class:`LeasedWarehouse`, does one UPDATE, and releases immediately -- the
  same pattern the bot uses between polls.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from fpl_edge.jobs import outbox
from fpl_edge.platform.query import read_copy
from fpl_edge.store.warehouse import DEFAULT_DB, LeasedWarehouse

UTC = dt.timezone.utc


def _has_table(wh) -> bool:
    df = wh.sql(
        "SELECT count(*) AS n FROM information_schema.tables "
        "WHERE table_name = 'platform_delivery'"
    )
    return bool(df.iloc[0]["n"])


def list_deliveries(
    *,
    db: Path | str = DEFAULT_DB,
    limit: int = 50,
    include_acked: bool = False,
) -> dict[str, Any]:
    """Deliveries newest first, as §2.1 specifies.

    Note the ordering difference from :func:`outbox.pending`, which is oldest
    first: that is a *send queue* and order is delivery order. This is a human
    reading a feed, and the newest thing is the one that matters.
    """
    path = Path(db)
    if not path.exists():
        return {"deliveries": [], "unacked": 0, "empty": True,
                "reason": f"no warehouse at {path}"}

    with read_copy(path) as wh:
        if not _has_table(wh):
            return {
                "deliveries": [], "unacked": 0, "empty": True,
                "reason": (
                    "Nothing has ever been delivered: the platform_delivery table "
                    "is created by the first monitor firing. It appears once the "
                    "deadline DAG runs a task that has something to say."
                ),
            }
        where = "" if include_acked else "WHERE NOT acked"
        df = wh.sql(
            f"SELECT id, monitor, kind, title, body, charts_json, created_utc, "
            f"       delivered_telegram, acked "
            f"FROM platform_delivery {where} ORDER BY created_utc DESC, id LIMIT ?",
            [int(limit)],
        )
        unacked = int(
            wh.sql("SELECT count(*) AS n FROM platform_delivery WHERE NOT acked")
            .iloc[0]["n"]
        )

    out = []
    for row in df.itertuples(index=False):
        charts = []
        if row.charts_json:
            try:
                charts = json.loads(row.charts_json)
            except (ValueError, TypeError):
                charts = []
        out.append({
            "id": str(row.id),
            "monitor": str(row.monitor),
            "kind": str(row.kind),
            "title": str(row.title),
            "body": str(row.body),
            "charts": charts,
            "created_utc": str(row.created_utc),
            "delivered_telegram": None if row.delivered_telegram is None
                                  else str(row.delivered_telegram),
            "acked": bool(row.acked),
        })
    return {"deliveries": out, "unacked": unacked, "empty": not out,
            "reason": None if out else "No undelivered messages."}


def ack(delivery_id: str, *, db: Path | str = DEFAULT_DB,
        now: dt.datetime | None = None) -> dict[str, Any]:
    """Mark one delivery acknowledged. Idempotent; unknown ids report as such.

    Acking is not deletion. The row stays, with its whole history, because the
    settlement job scores what was sent against what happened and a deleted
    alert is an alert that never has to answer for itself.
    """
    path = Path(db)
    if not path.exists():
        return {"id": delivery_id, "acked": False, "found": False,
                "reason": f"no warehouse at {path}"}

    lease = LeasedWarehouse(path)
    try:
        outbox.ensure_schema(lease)
        found = int(
            lease.sql("SELECT count(*) AS n FROM platform_delivery WHERE id = ?",
                      [delivery_id]).iloc[0]["n"]
        )
        if not found:
            return {"id": delivery_id, "acked": False, "found": False,
                    "reason": "no delivery with that id"}
        lease.sql("UPDATE platform_delivery SET acked = TRUE WHERE id = ?", [delivery_id])
        return {"id": delivery_id, "acked": True, "found": True,
                "acked_at": (now or dt.datetime.now(UTC)).astimezone(UTC).isoformat()}
    finally:
        lease.release()
