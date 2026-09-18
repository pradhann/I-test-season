"""The pasted-link ledger: one annotation row per item the owner pasted.

One row per pasted item, and it exists because three of the owner's asks all
need somewhere to put an ANNOTATION about content that must not be rewritten:

  * who the resolved creator is and HOW it was resolved (ask 1),
  * whether the item has been discarded as irrelevant (ask 2),
  * which gameweek the content is about, and whether that was stated,
    inferred, or corrected by hand (ask 3).

WHY A SIDE TABLE AND NOT A FLAG ON content_claim
------------------------------------------------
``migrations/content_001_claims.sql`` calls a claim "an immutable utterance:
it was made once, at one instant, and it never gets a newer version", and
``ContentStore._insert_new`` enforces it -- an existing row is never touched.
That rule is right and this module does not break it. A creator who said
"captain Haaland" said it; deleting the row would make the track record a
thing we curate after the fact, which is the one property it cannot have.

So DISCARD IS NOT DELETION. What the owner is revoking when they discard a
pasted link is not the utterance -- it is OUR DECISION TO CARRY IT IN THE
CORPUS. That decision is ours, it is revisable, and it belongs in a row we
own. ``discarded_utc`` is set, ``discard_reason`` records why, the archived
item, its transcript, its analysis and its claims are all left exactly as
they were, and :func:`restore_item` puts it back. Nothing is destroyed and
nothing is un-said; the reader simply stops being shown it.

Same argument for the gameweek. A correction does not overwrite
``content_claim.gameweek``: the claims keep the week they were written with,
and the correction is recorded here with the value it replaced
(``gw_corrected_from``) and the instant of the correction, so "the owner said
this is GW4" and "the inference said GW3" are both readable afterwards.

Moved out of ``fpl_edge/interfaces/creators.py`` (ARCHITECTURE_REVIEW.md check
6, move M2). It is a warehouse table, not a user-facing surface, and
``fpl_edge/ingest/content/store.py`` imports ``discarded_item_ids`` from it
inside the sanctioned read path. While the ledger lived in ``interfaces`` that
one import was half of the ``interfaces.creators`` and ``ingest.content.store``
two-cycle.

This module reads and writes one table and imports no other fpl_edge module, so
nothing here can close that cycle again.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Protocol

UTC = dt.UTC


class GameweekLike(Protocol):
    """What :func:`record_link_item` reads off a gameweek resolution.

    Spelled as a protocol rather than imported, because the concrete class is
    ``fpl_edge.interfaces.creators.GameweekResolution`` and naming it here
    would put back the ``ingest`` to ``interfaces`` edge this move removed.
    """

    gameweek: int | None
    season: str | None
    basis: str
    reason: str
    inferred: int | None
    stated: tuple[int, ...]


#: The ledger table. Owned by the pasted-link flow and by nothing else.
USER_LINK_TABLE = "user_link_item"

_LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {USER_LINK_TABLE} (
    item_id           VARCHAR PRIMARY KEY,
    url               VARCHAR NOT NULL,
    -- The creator this item was FILED under, after resolution.
    creator           VARCHAR NOT NULL,
    -- channel_id | channel_name | panel_show | unregistered_channel |
    -- host_registry | no_channel_on_page ...
    creator_basis     VARCHAR NOT NULL,
    creator_reason    VARCHAR NOT NULL,
    -- Verbatim, as the source stated it. NULL when the source stated nothing.
    channel_name      VARCHAR,
    -- Does the panel scope admit this creator? False = pasted-but-not-tracked:
    -- a real name the reader sees, legitimately absent from the board.
    tracked           BOOLEAN NOT NULL,
    season            VARCHAR,
    gameweek          INTEGER,
    gw_basis          VARCHAR,      -- stated | inferred | corrected | unknown
    gw_reason         VARCHAR,
    gw_stated_json    VARCHAR,      -- every gameweek the content named
    gw_inferred       INTEGER,      -- what calendar.next_after said, always kept
    gw_corrected_from INTEGER,
    gw_corrected_utc  TIMESTAMPTZ,
    gw_corrected_note VARCHAR,
    discarded_utc     TIMESTAMPTZ,  -- NULL = live
    discard_reason    VARCHAR,
    restored_utc      TIMESTAMPTZ,
    created_utc       TIMESTAMPTZ NOT NULL
);
"""

_LEDGER_COLS = (
    "item_id", "url", "creator", "creator_basis", "creator_reason",
    "channel_name", "tracked", "season", "gameweek", "gw_basis", "gw_reason",
    "gw_stated_json", "gw_inferred", "gw_corrected_from", "gw_corrected_utc",
    "gw_corrected_note", "discarded_utc", "discard_reason", "restored_utc",
    "created_utc",
)


def ensure_link_ledger(wh) -> None:
    """Idempotent DDL. Cheap enough to call on every write path."""
    wh.sql(_LEDGER_DDL)


def _ledger_present(wh) -> bool:
    try:
        return bool(wh.sql(
            "SELECT count(*) AS n FROM information_schema.tables "
            "WHERE table_name = ?", [USER_LINK_TABLE],
        ).iloc[0]["n"])
    except Exception:  # noqa: BLE001 - an unreadable catalog is not a ledger
        return False


def record_link_item(wh, *, item_id: str, url: str, creator: str,
                     creator_basis: str, creator_reason: str,
                     channel_name: str | None, tracked: bool,
                     gw: GameweekLike) -> None:
    """Write (or refresh) the annotation for one pasted item.

    Two things are deliberately preserved across a re-paste:

    * ``discarded_utc``. Pasting a link the owner already discarded is not a
      retraction of the discard; un-discarding is an explicit
      :func:`restore_item` call.
    * a HAND CORRECTION of the gameweek. Re-running the inference must not
      quietly overwrite the answer a human gave; the fresh inference is still
      written to ``gw_inferred`` so the two remain comparable.
    """
    ensure_link_ledger(wh)
    stated = json.dumps(list(gw.stated))
    rows = wh.sql(f"SELECT gw_basis FROM {USER_LINK_TABLE} WHERE item_id = ?",
                  [item_id])
    if len(rows):
        corrected = str(rows.iloc[0]["gw_basis"] or "") == "corrected"
        wh.sql(
            f"UPDATE {USER_LINK_TABLE} SET url = ?, creator = ?, "
            f"creator_basis = ?, creator_reason = ?, channel_name = ?, "
            f"tracked = ?, gw_stated_json = ?, gw_inferred = ? "
            f"WHERE item_id = ?",
            [url, creator, creator_basis, creator_reason, channel_name,
             bool(tracked), stated, gw.inferred, item_id],
        )
        if not corrected:
            wh.sql(
                f"UPDATE {USER_LINK_TABLE} SET season = ?, gameweek = ?, "
                f"gw_basis = ?, gw_reason = ? WHERE item_id = ?",
                [gw.season, gw.gameweek, gw.basis, gw.reason, item_id],
            )
        return
    wh.sql(
        f"INSERT INTO {USER_LINK_TABLE} ({', '.join(_LEDGER_COLS)}) "
        f"VALUES ({', '.join('?' for _ in _LEDGER_COLS)})",
        [item_id, url, creator, creator_basis, creator_reason, channel_name,
         bool(tracked), gw.season, gw.gameweek, gw.basis, gw.reason, stated,
         gw.inferred, None, None, None, None, None, None,
         dt.datetime.now(UTC)],
    )


def link_item_row(wh, item_id: str) -> dict | None:
    """The ledger annotation for one item, or None when there is no row."""
    if not _ledger_present(wh):
        return None
    rows = wh.sql(f"SELECT * FROM {USER_LINK_TABLE} WHERE item_id = ?",
                  [item_id]).to_dict("records")
    return _public_ledger_row(rows[0]) if rows else None


def _public_ledger_row(row: dict) -> dict:
    """Ledger row -> the JSON shape the API and the UI read."""
    stated: list[int] = []
    raw = row.get("gw_stated_json")
    if raw:
        try:
            stated = [int(v) for v in json.loads(str(raw))]
        except (ValueError, TypeError):
            stated = []
    discarded = row.get("discarded_utc")
    discarded = None if _is_null(discarded) else str(discarded)
    corrected_utc = row.get("gw_corrected_utc")
    return {
        "item_id": str(row.get("item_id")),
        "url": None if _is_null(row.get("url")) else str(row.get("url")),
        "creator": str(row.get("creator")),
        "creator_basis": str(row.get("creator_basis")),
        "creator_reason": str(row.get("creator_reason")),
        "channel_name": (None if _is_null(row.get("channel_name"))
                         else str(row.get("channel_name"))),
        "tracked": bool(row.get("tracked")),
        "gameweek": (None if _is_null(row.get("gameweek"))
                     else int(row.get("gameweek"))),
        "season": None if _is_null(row.get("season")) else str(row.get("season")),
        "gw_basis": (None if _is_null(row.get("gw_basis"))
                     else str(row.get("gw_basis"))),
        "gw_reason": (None if _is_null(row.get("gw_reason"))
                      else str(row.get("gw_reason"))),
        "gw_stated": stated,
        "gw_inferred": (None if _is_null(row.get("gw_inferred"))
                        else int(row.get("gw_inferred"))),
        "gw_corrected_from": (None if _is_null(row.get("gw_corrected_from"))
                              else int(row.get("gw_corrected_from"))),
        "gw_corrected_utc": None if _is_null(corrected_utc) else str(corrected_utc),
        "gw_corrected_note": (None if _is_null(row.get("gw_corrected_note"))
                              else str(row.get("gw_corrected_note"))),
        "discarded": discarded is not None,
        "discarded_utc": discarded,
        "discard_reason": (None if _is_null(row.get("discard_reason"))
                           else str(row.get("discard_reason"))),
    }


def _is_null(value) -> bool:
    if value is None:
        return True
    try:
        import pandas as pd

        return bool(pd.isna(value))
    except (TypeError, ValueError, ImportError):
        return False


class UnknownLinkItem(KeyError):
    """Asked to annotate an item this warehouse does not hold."""


def _item_exists(wh, item_id: str) -> bool:
    try:
        return bool(wh.sql(
            "SELECT count(*) AS n FROM content_item WHERE item_id = ?",
            [item_id]).iloc[0]["n"])
    except Exception:  # noqa: BLE001 - no content tables means no item
        return False


def _ensure_ledger_row(wh, item_id: str) -> None:
    """A minimal ledger row for an item that has none yet.

    Discard has to work on anything the reader can see, not only on things
    pasted since this table existed -- including items the bulk pipeline
    wrote. The placeholder records honestly that the ledger learned about this
    item at discard time and never resolved a creator for it.
    """
    ensure_link_ledger(wh)
    if len(wh.sql(f"SELECT 1 FROM {USER_LINK_TABLE} WHERE item_id = ?", [item_id])):
        return
    rows = wh.sql("SELECT creator, url FROM content_item WHERE item_id = ?",
                  [item_id]).to_dict("records")
    creator = str(rows[0]["creator"]) if rows else "unknown"
    url = str(rows[0]["url"]) if rows else ""
    wh.sql(
        f"INSERT INTO {USER_LINK_TABLE} ({', '.join(_LEDGER_COLS)}) "
        f"VALUES ({', '.join('?' for _ in _LEDGER_COLS)})",
        [item_id, url, creator, "pre_existing_item",
         ("this item was already in the corpus when the ledger first saw it; "
          "no creator resolution was run for it"), None, True, None, None, None,
         None, "[]", None, None, None, None, None, None, None,
         dt.datetime.now(UTC)],
    )


def discard_item(wh, item_id: str, *, reason: str = "") -> dict:
    """Hide an ingested item from every read path. Destroys nothing.

    See the header of this section: the utterance stands, our decision to
    carry it does not. The archived ``content_item``, its transcript segments,
    its ``content_analysis`` row and every ``content_claim`` it produced are
    left byte-for-byte as they were, and :func:`restore_item` is a real inverse.

    Raises :class:`UnknownLinkItem` when the warehouse holds no such item --
    silently succeeding on a typo'd id would report a hidden item that is still
    on screen.
    """
    if not _item_exists(wh, item_id):
        raise UnknownLinkItem(
            f"no content_item {item_id!r} in this warehouse; nothing was "
            f"discarded, because a discard that hides nothing would report "
            f"success for content that is still visible"
        )
    _ensure_ledger_row(wh, item_id)
    wh.sql(
        f"UPDATE {USER_LINK_TABLE} SET discarded_utc = ?, discard_reason = ?, "
        f"restored_utc = NULL WHERE item_id = ?",
        [dt.datetime.now(UTC), reason or "no reason given", item_id],
    )
    row = link_item_row(wh, item_id) or {}
    return {"item_id": item_id, "discarded": True,
            "discard_reason": row.get("discard_reason"),
            "discarded_utc": row.get("discarded_utc"),
            "note": ("hidden from the read paths. Nothing was deleted: the "
                     "archived item, its transcript, its analysis and its "
                     "claims are unchanged, because a claim is an utterance "
                     "that was made and cannot be un-made.")}


def restore_item(wh, item_id: str, *, reason: str = "") -> dict:
    """Undo a discard. The inverse exists because the discard destroyed nothing."""
    if not _ledger_present(wh) or not len(
        wh.sql(f"SELECT 1 FROM {USER_LINK_TABLE} WHERE item_id = ?", [item_id])
    ):
        raise UnknownLinkItem(f"no ledger row for item {item_id!r}")
    wh.sql(
        f"UPDATE {USER_LINK_TABLE} SET discarded_utc = NULL, "
        f"discard_reason = ?, restored_utc = ? WHERE item_id = ?",
        [reason or None, dt.datetime.now(UTC), item_id],
    )
    return {"item_id": item_id, "discarded": False,
            "note": "restored; it was never deleted"}


def correct_gameweek(wh, item_id: str, gameweek: int, *,
                     note: str = "") -> dict:
    """Record the owner's gameweek for an item, as a CORRECTION.

    Not an overwrite of the inference: ``gw_corrected_from`` keeps the value
    that was replaced, ``gw_inferred`` keeps what the publish-date rule said,
    ``gw_corrected_utc`` stamps when a human intervened, and ``gw_basis``
    becomes ``"corrected"`` so no reader can mistake a hand-entered week for a
    derived one.

    ``content_claim.gameweek`` is NOT touched. Those rows are immutable by the
    rule in ``content_001_claims.sql`` and the claims keep the week they were
    written with; this records that the owner says the CONTENT is about another
    one, and both remain readable.
    """
    if not 1 <= int(gameweek) <= 38:
        raise ValueError(f"gameweek {gameweek} is not a gameweek (1-38)")
    if not _item_exists(wh, item_id):
        raise UnknownLinkItem(f"no content_item {item_id!r} in this warehouse")
    _ensure_ledger_row(wh, item_id)
    before = wh.sql(f"SELECT gameweek FROM {USER_LINK_TABLE} WHERE item_id = ?",
                    [item_id]).to_dict("records")
    prior = before[0]["gameweek"] if before else None
    prior = None if _is_null(prior) else int(prior)
    wh.sql(
        f"UPDATE {USER_LINK_TABLE} SET gameweek = ?, gw_basis = 'corrected', "
        f"gw_reason = ?, gw_corrected_from = ?, gw_corrected_utc = ?, "
        f"gw_corrected_note = ? WHERE item_id = ?",
        [int(gameweek),
         (f"corrected by hand to GW{int(gameweek)}"
          + (f", replacing GW{prior}" if prior is not None else "")
          + ". The claims stored for this item keep the gameweek they were "
            "written with; this is an item-level correction, not a rewrite."),
         prior, dt.datetime.now(UTC), note or None, item_id],
    )
    return link_item_row(wh, item_id) or {}


def discarded_item_ids(wh) -> frozenset[str]:
    """Every item the owner has hidden. Empty when the ledger does not exist.

    THE filter every honest read path owes. ``build_take`` in
    :mod:`fpl_edge.platform.link_jobs` applies it, and
    ``ContentStore._drop_discarded`` in :mod:`fpl_edge.ingest.content.store`
    calls it on the sanctioned read path. Any other read path needs the same
    two lines:

        hidden = discarded_item_ids(wh)
        frame = frame[~frame["item_id"].isin(hidden)]
    """
    if not _ledger_present(wh):
        return frozenset()
    try:
        rows = wh.sql(
            f"SELECT item_id FROM {USER_LINK_TABLE} WHERE discarded_utc IS NOT NULL")
    except Exception:  # noqa: BLE001 - an unreadable ledger hides nothing
        return frozenset()
    return frozenset(str(v) for v in rows["item_id"])


def drop_discarded(frame, wh, *, column: str = "item_id"):
    """Remove discarded items from any frame carrying an ``item_id`` column."""
    hidden = discarded_item_ids(wh)
    if not hidden or frame is None or getattr(frame, "empty", True):
        return frame
    if column not in getattr(frame, "columns", ()):
        return frame
    return frame[~frame[column].astype(str).isin(hidden)]


