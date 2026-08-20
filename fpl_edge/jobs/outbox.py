"""Durable delivery outbox: the seam between "decided" and "sent".

Argus's `deliver.ts` in ~a page of Python (docs/platform/argus_architecture.md
§4.3, §4.4). The rule it exists to enforce:

    A task that decided to say something must not be able to commit that
    decision without also committing the message, and must not be able to send
    the message twice.

So `deliver()` writes the row **inside the same transaction** the caller uses to
record its firing outcome, and `flush_outbox()` is a separate pass that pushes
undelivered rows to Telegram and stamps them. If the process dies between the
two, the message is still in the table and the next flush sends it; if it dies
mid-send, the row is unstamped and the next flush retries. Nothing is lost, and
the id is the dedupe key so nothing is doubled.

**Send-only.** This module uses :class:`HttpxTransport` for exactly one Bot API
method, ``sendMessage``. It never calls ``getUpdates`` -- the long-poll bot
under launchd owns that offset, and a second poller would steal its updates and
silently eat the user's ideas. The chat allowlist comes from
:class:`TelegramConfig`, which is also the only place the token is read.

**Ownership.** The table is created here with CREATE TABLE IF NOT EXISTS rather
than in a migration, so that the platform spine (`fpl_edge/platform/`) can adopt
this module -- or this table -- without a migration collision. See the note at
the top of `fpl_edge/jobs/migrations/001_dag_firing.sql`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

log = logging.getLogger("fpl_edge.jobs.outbox")

UTC = dt.timezone.utc

#: Telegram caps a message at 4096 characters; leave room for the title line.
MAX_BODY_CHARS = 3500

DDL = """
CREATE TABLE IF NOT EXISTS platform_delivery (
    id                 VARCHAR NOT NULL,
    monitor            VARCHAR NOT NULL,
    kind               VARCHAR NOT NULL,
    title              VARCHAR NOT NULL,
    body               VARCHAR NOT NULL,
    charts_json        VARCHAR,
    created_utc        TIMESTAMPTZ NOT NULL,
    delivered_telegram TIMESTAMPTZ,
    acked              BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (id)
);
"""


class _Transport(Protocol):
    def call(self, method: str, payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        ...


@dataclass(frozen=True, slots=True)
class Delivery:
    """One canonical message. Stored once, rendered per destination."""

    id: str
    monitor: str
    kind: str
    title: str
    body: str
    charts_json: str | None
    created_utc: dt.datetime

    def render(self) -> str:
        """The wire text. One title line, then the body -- no parse_mode.

        Same reasoning as the bot's replies (fpl_edge/interfaces/telegram.py
        module docstring): with Markdown on, a player name containing an
        underscore becomes formatting, and a bare URL in injury news becomes a
        clickable link in a message that appears to come from the bot.
        """
        body = self.body if len(self.body) <= MAX_BODY_CHARS else (
            self.body[: MAX_BODY_CHARS - 3] + "..."
        )
        return f"{self.title}\n\n{body}" if body.strip() else self.title


@dataclass
class FlushResult:
    sent: int = 0
    failed: int = 0
    skipped_no_config: bool = False
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        if self.skipped_no_config:
            return "outbox: not flushed (Telegram not configured)"
        return f"outbox: sent {self.sent}, failed {self.failed}"


def ensure_schema(wh) -> None:
    """Idempotent. Cheap enough to call on every entry point."""
    wh.sql(DDL)


def delivery_id(monitor: str, created_utc: dt.datetime, title: str) -> str:
    """Deterministic id, so a retried task re-inserts rather than duplicates.

    Derived from the content that identifies the occasion -- the monitor, the
    instant it was decided, and the headline. A task that is re-run for the same
    firing produces the same id and the INSERT is a no-op; a genuinely new
    message differs in at least one of the three.
    """
    raw = f"{monitor}|{created_utc.astimezone(UTC).isoformat()}|{title}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def deliver(
    wh,
    *,
    monitor: str,
    kind: str,
    title: str,
    body: str,
    charts: Sequence[dict[str, Any]] | None = None,
    now: dt.datetime | None = None,
    extra_sql: Sequence[tuple[str, Sequence[Any]]] = (),
) -> str:
    """Enqueue one message. Returns its id.

    ``extra_sql`` is the transactional hook: statements passed here commit with
    the delivery row or not at all. The DAG uses it to stamp the firing outcome,
    which is the "neither record can commit without the other" property
    (deliver.ts:125-128). Passing nothing still gives you an atomic insert.
    """
    ensure_schema(wh)
    created = (now or dt.datetime.now(UTC)).astimezone(UTC)
    did = delivery_id(monitor, created, title)
    charts_json = json.dumps(list(charts)) if charts else None

    wh.sql("BEGIN TRANSACTION")
    try:
        wh.sql(
            "INSERT INTO platform_delivery "
            "(id, monitor, kind, title, body, charts_json, created_utc, "
            " delivered_telegram, acked) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, FALSE) "
            "ON CONFLICT DO NOTHING",
            [did, monitor, kind, title, body, charts_json, created],
        )
        for stmt, params in extra_sql:
            wh.sql(stmt, list(params))
        wh.sql("COMMIT")
    except Exception:
        wh.sql("ROLLBACK")
        raise
    return did


def pending(wh, *, limit: int = 25) -> list[Delivery]:
    """Undelivered rows, oldest first. Order is the delivery order."""
    ensure_schema(wh)
    df = wh.sql(
        "SELECT id, monitor, kind, title, body, charts_json, created_utc "
        "FROM platform_delivery WHERE delivered_telegram IS NULL "
        "ORDER BY created_utc, id LIMIT ?",
        [int(limit)],
    )
    out: list[Delivery] = []
    for row in df.itertuples(index=False):
        out.append(
            Delivery(
                id=str(row.id),
                monitor=str(row.monitor),
                kind=str(row.kind),
                title=str(row.title),
                body=str(row.body),
                charts_json=None if row.charts_json is None else str(row.charts_json),
                created_utc=row.created_utc.to_pydatetime()
                if hasattr(row.created_utc, "to_pydatetime")
                else row.created_utc,
            )
        )
    return out


def mark_delivered(wh, delivery_id_: str, *, now: dt.datetime | None = None) -> None:
    wh.sql(
        "UPDATE platform_delivery SET delivered_telegram = ? WHERE id = ?",
        [(now or dt.datetime.now(UTC)).astimezone(UTC), delivery_id_],
    )


def flush_outbox(
    wh,
    *,
    transport: _Transport | None = None,
    config=None,
    limit: int = 25,
    now: dt.datetime | None = None,
) -> FlushResult:
    """Send every undelivered row to every allowlisted chat, then stamp it.

    The stamp happens only after the send returns, so a crash mid-flight leaves
    the row pending and the next tick retries it. A message that Telegram
    rejects is left pending too, with the reason in ``errors``: it is better for
    a deadline alert to arrive late than for it to be quietly marked sent.
    """
    from fpl_edge.interfaces.telegram import HttpxTransport, TelegramConfig

    result = FlushResult()
    cfg = config or TelegramConfig.from_env()
    if not cfg.ready:
        # Nothing is dropped -- the rows stay pending for whenever .env is fixed.
        result.skipped_no_config = True
        return result

    rows = pending(wh, limit=limit)
    if not rows:
        return result

    owned = transport is None
    tx = transport or HttpxTransport(cfg.token or "")
    try:
        for row in rows:
            text = row.render()
            ok = True
            for chat_id in sorted(cfg.allowed_chat_ids):
                try:
                    tx.call(
                        "sendMessage",
                        {
                            "chat_id": int(chat_id),
                            "text": text,
                            "disable_web_page_preview": True,
                        },
                        timeout=30.0,
                    )
                except Exception as exc:  # noqa: BLE001 - reported, not raised
                    ok = False
                    result.errors.append(f"{row.monitor}: {type(exc).__name__}")
                    log.exception("outbox send failed for %s", row.monitor)
            if ok:
                mark_delivered(wh, row.id, now=now)
                result.sent += 1
            else:
                result.failed += 1
    finally:
        if owned:
            close = getattr(tx, "close", None)
            if close is not None:
                close()
    return result
