"""Building the take, and the owner's annotations on what was ingested.

The take is assembled across every sibling row of one publication, because a
video whose analysis and whose transcript segments landed under two different
URLs still has to produce one answer with timestamps on it.

``discard_item``, ``restore_item`` and ``correct_gameweek`` are thin wrappers
over the ledger in ``fpl_edge.ingest.content.link_ledger``: a discard hides an
item from every read path and deletes nothing, and a gameweek correction is
recorded beside the value it replaced.

Split out of ``fpl_edge/platform/link_jobs.py`` (ARCHITECTURE_REVIEW.md
Section 3 and Section 4 row 17).
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fpl_edge.platform.link_jobs.preflight import _now

WRITE_ATTEMPTS = 3


def _table_exists(wh, name: str) -> bool:
    return bool(wh.sql(
        "SELECT count(*) AS n FROM information_schema.tables WHERE table_name = ?",
        [name],
    ).iloc[0]["n"])


def _existing_item(db: Path | str, *, vid: str | None,
                   url: str) -> tuple[str | None, tuple[str, ...]]:
    """The item(s) already holding this publication, richest row first.

    One Andy (LTFPL) video sits in the warehouse under two urls: one row
    carries the analysis, the other carries 1,199 transcript segments. So the
    representative row is the one with an analysis, then the one with
    segments -- and every sibling id is returned too, because the take is
    assembled across all of them or it loses its timestamps.
    """
    from fpl_edge.ingest.content.urls import youtube_id
    from fpl_edge.platform.query import read_copy

    db_path = Path(db)
    if not db_path.exists():
        return None, ()
    try:
        with read_copy(db_path) as wh:
            if not _table_exists(wh, "content_item"):
                return None, ()
            if vid:
                rows = wh.sql(
                    "SELECT item_id, url FROM content_item "
                    "WHERE url IS NOT NULL AND url LIKE ?", ["%" + vid + "%"],
                )
                candidates = [
                    str(r["item_id"]) for r in rows.to_dict("records")
                    if youtube_id(str(r["url"])) == vid
                ]
            else:
                rows = wh.sql(
                    "SELECT item_id FROM content_item WHERE url = ?", [url],
                )
                candidates = [str(r["item_id"]) for r in rows.to_dict("records")]
            if not candidates:
                return None, ()
            ranked = _rank_items(wh, candidates)
    except Exception:  # noqa: BLE001 - an unreadable warehouse is not a duplicate
        return None, ()
    return ranked[0], tuple(ranked)


def _rank_items(wh, item_ids: list[str]) -> list[str]:
    """Analysis beats transcript beats bare row; ties broken by segment count."""
    placeholders = ", ".join("?" for _ in item_ids)
    analysed: set[str] = set()
    if _table_exists(wh, "content_analysis"):
        analysed = {
            str(r["item_id"]) for r in wh.sql(
                f"SELECT DISTINCT item_id FROM content_analysis "
                f"WHERE item_id IN ({placeholders})", list(item_ids)
            ).to_dict("records")
        }
    segments: dict[str, int] = {}
    if _table_exists(wh, "transcript_segment"):
        segments = {
            str(r["item_id"]): int(r["n"]) for r in wh.sql(
                f"SELECT item_id, count(*) AS n FROM transcript_segment "
                f"WHERE item_id IN ({placeholders}) GROUP BY item_id",
                list(item_ids),
            ).to_dict("records")
        }
    return sorted(
        item_ids,
        key=lambda i: (0 if i in analysed else 1,
                       0 if segments.get(i) else 1,
                       -segments.get(i, 0), i),
    )


def _item_for_url(db: Path | str, *, vid: str | None,
                  url: str) -> tuple[str | None, tuple[str, ...]]:
    """Read back what the ingester just wrote. It returns no item id itself."""
    return _existing_item(db, vid=vid, url=url)


def build_take(db: Path | str, item_ids: tuple[str, ...] | list[str],
               url: str | None) -> dict[str, Any]:
    """The inline take for one publication, with deep links.

    Built with the SAME helpers the creator board uses
    (``TranscriptIndex``/``_take``/``_resolver`` in
    :mod:`fpl_edge.platform.scripts.creators`) rather than a second shaping of
    the same data -- two surfaces that disagree about what a creator said is
    the failure this whole page exists to avoid.

    Transcript segments are pooled across every sibling row, because the pair
    of Andy rows in the warehouse keep the analysis and the segments apart and
    a per-row index would silently lose every timestamp.
    """
    from fpl_edge.platform.query import read_copy
    from fpl_edge.platform.scripts.common import SEASON_DEFAULT
    from fpl_edge.platform.scripts.creators import (
        TranscriptIndex,
        _analyses,
        _resolver,
        _take,
    )

    ids = [str(i) for i in item_ids if i]
    if not ids:
        return {"take": None, "reason": "no stored item to read a take from"}

    db_path = Path(db)
    if not db_path.exists():
        return {"take": None, "reason": f"no warehouse at {db_path}"}

    with read_copy(db_path) as wh:
        if not _table_exists(wh, "content_item"):
            return {"take": None,
                    "reason": "this warehouse has no content tables yet"}
        placeholders = ", ".join("?" for _ in ids)
        items = wh.sql(
            f"SELECT item_id, title, url, creator, text_source "
            f"FROM content_item WHERE item_id IN ({placeholders})", list(ids),
        ).to_dict("records")
        rows: list[tuple[float | None, str]] = []
        if _table_exists(wh, "transcript_segment"):
            rows = [
                (None if r["start_s"] is None else float(r["start_s"]),
                 str(r["text"]))
                for r in wh.sql(
                    f"SELECT start_s, text FROM transcript_segment "
                    f"WHERE item_id IN ({placeholders}) ORDER BY item_id, seq",
                    list(ids),
                ).to_dict("records")
            ]
        index = TranscriptIndex(rows)
        analyses = _analyses(wh, set(ids)) if _table_exists(wh, "content_analysis") else {}
        person, person_reason = _attribution(wh, ids)
        resolver = _resolver(wh, SEASON_DEFAULT, _now())
        ledger = _ledger(wh, ids)

    head = items[0] if items else {}
    link = url or (str(head.get("url")) if head.get("url") else None)
    payload: dict[str, Any] = {
        "item_id": ids[0],
        "item_ids": ids,
        "title": str(head.get("title")) if head.get("title") else None,
        "url": link,
        "text_source": str(head.get("text_source")) if head.get("text_source") else None,
        "n_segments": len(rows),
        "person": person,
        "person_reason": person_reason,
        # -- who said it, and which gameweek it is about ---------------------
        # `creator` is now the resolved channel/show rather than the
        # placeholder `user-shared`; `tracked` says whether the panel scope
        # admits it, so a real name can be shown next to an honest "not on the
        # board" note instead of the item vanishing.
        "creator": str(head.get("creator")) if head.get("creator") else None,
        "annotation": ledger,
        "gameweek": (ledger or {}).get("gameweek"),
        "gameweek_basis": (ledger or {}).get("gw_basis"),
        "gameweek_reason": (ledger or {}).get("gw_reason"),
        "tracked": None if ledger is None else ledger.get("tracked"),
        "discarded": bool((ledger or {}).get("discarded")),
    }
    if payload["discarded"]:
        # A discarded item still answers a direct poll for its own job -- the
        # owner asked for it by id -- but it says so, and every corpus-wide
        # read drops it. What is NOT shown is the take: presenting the
        # analysis of content the owner has hidden is exactly the thing the
        # discard was for.
        payload["take"] = None
        payload["reason"] = (
            "this item has been discarded"
            + (f": {(ledger or {}).get('discard_reason')}"
               if (ledger or {}).get("discard_reason") else "")
            + ". Nothing was deleted -- the transcript, the analysis and the "
              "claims are all still stored -- it is hidden from the read "
              "paths and `restore` puts it back."
        )
        return payload
    for item_id in ids:                       # analysis-carrying row wins
        if item_id in analyses:
            analysis, model = analyses[item_id]
            payload["take"] = _take(analysis, model, resolver, index, link)
            payload["reason"] = None
            return payload
    payload["take"] = None
    payload["reason"] = (
        "the transcript is stored but no analysis has been run on it yet"
        if rows else
        "no analysis and no transcript are stored for this item"
    )
    return payload


def _ledger(wh, item_ids: list[str]) -> dict[str, Any] | None:
    """The pasted-link annotation for these ids: creator basis, gameweek, discard.

    Written by :func:`fpl_edge.ingest.content.link_ledger.record_link_item`. Absent is
    a legitimate state (an item ingested before the ledger existed, or by the
    bulk pipeline), and absent means "no annotation", never "not discarded and
    definitely tracked" -- the caller renders None rather than defaults.
    """
    from fpl_edge.ingest.content.link_ledger import (
        USER_LINK_TABLE,
        _public_ledger_row,
    )

    if not _table_exists(wh, USER_LINK_TABLE):
        return None
    placeholders = ", ".join("?" for _ in item_ids)
    rows = wh.sql(
        f"SELECT * FROM {USER_LINK_TABLE} WHERE item_id IN ({placeholders})",
        list(item_ids),
    ).to_dict("records")
    if not rows:
        return None
    # The discarded state wins across siblings: one video stored under two url
    # forms is one publication, and hiding half of it would be worse than not
    # hiding it at all.
    for row in rows:
        if row.get("discarded_utc") is not None:
            public = _public_ledger_row(row)
            if public["discarded"]:
                return public
    return _public_ledger_row(rows[0])


def _attribution(wh, item_ids: list[str]) -> tuple[str | None, str]:
    """Who said it, if the panel has established that. Never guessed.

    A pasted link is now filed under its RESOLVED creator (a channel or show),
    but ``item_person`` is written by the panel's attribution pass over the
    corpus, so a just-pasted item normally has no row yet -- and that is the
    honest answer, not a gap to fill with a default. This does NOT run
    :func:`fpl_edge.ingest.content.panel.attribute_items`: that walks the whole
    corpus and writes ``item_person`` for all of it, which one paste has no
    business triggering.
    """
    if not _table_exists(wh, "item_person"):
        return None, ("the panel's person layer has not been built in this "
                      "warehouse, so no attribution is available")
    placeholders = ", ".join("?" for _ in item_ids)
    rows = wh.sql(
        f"SELECT person_key, basis FROM item_person "
        f"WHERE item_id IN ({placeholders})", list(item_ids),
    ).to_dict("records")
    if not rows:
        return None, ("no person has been attributed to this item yet; the "
                      "panel's attribution pass runs over the corpus, not on "
                      "paste, and no person is guessed in the meantime")
    return str(rows[0]["person_key"]), f"basis: {rows[0].get('basis')}"


def _ingest_once(db: Path | str, url: str):
    """``ingest_link`` under a lease that connects lazily.

    ``ingest_link`` does its fetching and transcription before it touches the
    warehouse argument, and :class:`LeasedWarehouse` connects on first
    attribute access -- so the write lock is taken for the persistence phase
    and not held across the network work that precedes it.
    """
    from fpl_edge.interfaces.creators import ingest_link
    from fpl_edge.store.warehouse import LeasedWarehouse

    lease = LeasedWarehouse(db, lock_timeout_s=120.0)
    try:
        return ingest_link(lease, url)
    finally:
        lease.release()


def _annotate(db: Path | str, action, *args, **kwargs) -> dict[str, Any]:
    """Run one small ledger write under a lease, then let the lock go.

    Every one of these is a single UPDATE on ``user_link_item`` with no
    network and no transcription anywhere near it, which is the whole reason
    they are safe to do inline in a request: the DuckDB write lock is held for
    the duration of one statement.
    """
    from fpl_edge.store.warehouse import LeasedWarehouse

    lease = LeasedWarehouse(db, lock_timeout_s=30.0)
    try:
        return action(lease, *args, **kwargs)
    finally:
        lease.release()


def discard_item(db: Path | str, item_id: str, *, reason: str = "") -> dict[str, Any]:
    """Hide an ingested item. Nothing is deleted; see interfaces.creators."""
    from fpl_edge.ingest.content.link_ledger import discard_item as _discard

    return _annotate(db, _discard, item_id, reason=reason)


def restore_item(db: Path | str, item_id: str, *, reason: str = "") -> dict[str, Any]:
    from fpl_edge.ingest.content.link_ledger import restore_item as _restore

    return _annotate(db, _restore, item_id, reason=reason)


def correct_gameweek(db: Path | str, item_id: str, gameweek: int, *,
                     note: str = "") -> dict[str, Any]:
    from fpl_edge.ingest.content.link_ledger import correct_gameweek as _correct

    return _annotate(db, _correct, item_id, int(gameweek), note=note)


def ingest_with_retry(db: Path | str, url: str, *,
                      attempts: int = WRITE_ATTEMPTS,
                      ingest: Callable[[Path | str, str], Any] | None = None):
    """Retry on lock contention with backoff; re-raise anything else.

    Losing a race for the single writer lock is expected on a machine where
    the bot, the DAG and this server all write -- so it backs off rather than
    surfacing "database is locked" as the user's answer.
    """
    run = ingest or _ingest_once
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return run(db, url)
        except Exception as exc:  # noqa: BLE001 - re-raised unless it is the lock
            message = str(exc).lower()
            if "lock" not in message and "conflict" not in message:
                raise
            last = exc
            if attempt < attempts - 1:
                time.sleep(min(8.0, 2.0 ** attempt) * (0.5 + random.random()))
    raise RuntimeError(
        f"the warehouse write lock was held by another process through "
        f"{attempts} attempts; nothing was stored. Last error: {last}"
    )
