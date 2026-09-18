"""`creator_detail`: one creator expanded into their items, squad and record."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.ingest.content.urls import canonical_key, deep_link
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import POSITION_NAME, SEASON_DEFAULT, empty, next_gw, q
from fpl_edge.platform.scripts.creators.identity import (
    _CONTENT_TABLES,
    _NO_TRANSCRIPT,
    _PANEL_TABLE,
    _TEXT_RANK,
    UTC,
    TranscriptIndex,
    _analyses,
    _content_store,
    _display_index,
    _entries,
    _f,
    _i,
    _iso,
    _items,
    _naming,
    _record,
    _resolver,
    _s,
    _stamps,
    _tables_present,
    _take,
    _take_reason,
    _transcripts,
    _weights_as_of,
)
from fpl_edge.platform.scripts.creators.schema import DETAIL_PARAMS, DETAIL_RESULT

# ---------------------------------------------------------------------------
# creator_detail

def creator_detail(wh, *, creator: str, days: int = 60, limit: int = 40,
                   gw: int | None = None) -> dict[str, Any]:
    """One creator expanded: their items, analyses, claims and public FPL trail.

    Every claim carries a deep link built here -- YouTube ``&t=NNNs`` when the
    quote can be located in a transcript, the item URL when it cannot -- and its
    ``extractor``, so a keyword-window claim and a semantic one are never
    presented as the same kind of evidence.
    """
    moment = dt.datetime.now(UTC)
    present = _tables_present(wh, _CONTENT_TABLES + ("transcript_segment",
                                                     "content_analysis",
                                                     "creator_score",
                                                     "fact_manager_transfer",
                                                     "fact_manager_pick",
                                                     _PANEL_TABLE))
    missing = [t for t in _CONTENT_TABLES if t not in present]
    if missing:
        return empty(
            f"the creator corpus is not in this warehouse ({', '.join(missing)} "
            f"missing). Run `python -m fpl_edge.ingest.content.pipeline ingest`."
        )

    items = _items(wh, moment, creator=creator)
    known = q(wh, "SELECT DISTINCT creator FROM content_source "
                  "UNION SELECT DISTINCT creator FROM content_item")
    names = sorted(str(n) for n in known["creator"]) if not known.empty else []
    if creator not in names:
        return empty(
            f"no creator named {creator!r} is tracked. On file: "
            + (", ".join(names) if names else "(none)") + "."
        )

    since = moment - dt.timedelta(days=int(days))
    if not items.empty:
        items = items.assign(
            _published=items["published_at"].map(_iso),
            _ts=_stamps(items["published_at"]),
        )
        items = items[items["_ts"] >= since]

    # One entry per PUBLICATION, not per stored row. `items` arrives newest
    # first so the groups come out newest first too; the limit applies to
    # publications, which is what a reader is counting.
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in (items.to_dict("records") if not items.empty else []):
        groups.setdefault(
            canonical_key(_s(r["url"]), str(r["item_id"])), []
        ).append(r)
    families = list(groups.values())[: int(limit)]

    ids = {str(r["item_id"]) for fam in families for r in fam}
    analyses = _analyses(wh, ids) if "content_analysis" in present else {}
    transcripts = _transcripts(wh, ids) if "transcript_segment" in present else {}
    resolver = _resolver(wh, SEASON_DEFAULT, moment)
    display = _display_index(wh, moment)

    claims = _content_store(wh).claims_visible_at(moment)
    if not claims.empty:
        claims = claims[claims["creator"] == creator]
    by_item: dict[str, list[dict[str, Any]]] = {}
    for r in (claims.to_dict("records") if not claims.empty else []):
        by_item.setdefault(str(r["item_id"]), []).append(r)

    out_items: list[dict[str, Any]] = []
    for family in families:
        rep = min(family, key=lambda r: _TEXT_RANK.get(str(r["text_source"]), 9))
        item_id = str(rep["item_id"])
        url = _s(rep["url"])
        index = _NO_TRANSCRIPT
        for sib in sorted(str(r["item_id"]) for r in family):
            if sib in transcripts:
                index = transcripts[sib]
                break
        analysis = model = None
        for sib in sorted(str(r["item_id"]) for r in family):
            if sib in analyses:
                analysis, model = analyses[sib]
                break
        take = _take(analysis, model, resolver, index, url, display)

        # Claims from every stored row of this publication, collapsed on
        # (player, action, gameweek, extractor). The live warehouse holds the
        # same Andy LTFPL video under two item_ids carrying 48 claim rows for
        # 40 distinct positions; listing both rows shows a reader eight
        # opinions that were never voiced twice.
        best: dict[tuple, dict[str, Any]] = {}
        for sib in family:
            for c in by_item.get(str(sib["item_id"]), []):
                key = (_i(c.get("player_code")), str(c.get("action")),
                       _i(c.get("gameweek")), str(c.get("extractor") or "cue"))
                row = _claim_row(c, index, url, display)
                kept = best.get(key)
                # Of two rows saying the same thing, keep the one whose quote
                # could actually be located in the transcript: same position,
                # better evidence.
                if kept is None or (kept["start_s"] is None
                                    and row["start_s"] is not None):
                    best[key] = row
        merged = list(best.values())

        out_items.append({
            "item_id": item_id,
            "title": str(rep["title"]),
            "url": url,
            "published_at": str(rep["_published"]),
            "kind": str(rep["kind"]),
            "text_source": str(rep["text_source"]),
            "analysis": take,
            "analysis_reason": None if take is not None else _take_reason(
                {"text_source": rep["text_source"]}, bool(index)
            ),
            "claims": merged,
        })

    entries, entry_reason = _entries(wh, present, moment)
    entry = entries.get(creator)
    weights = _weights_as_of(wh, moment) if "creator_score" in present else {}

    squad, squad_reason, squad_meta = _squad(wh, entry, present, moment, gw)
    transfers, transfers_reason = _transfers(wh, entry, present, moment)

    return {
        "creator": creator,
        "as_of": moment.isoformat(),
        "window_days": int(days),
        "entry": entry,
        "entry_reason": None if entry else entry_reason,
        "squad": squad,
        "squad_reason": squad_reason,
        # WHICH gameweek the squad above is, and every gameweek that has one.
        # `squad_gw` is null exactly when `squad` is; `squad_gws` is populated
        # either way, so "nothing for GW3" always names where to look instead.
        "squad_gw": squad_meta["gw"],
        "squad_gws": squad_meta["available"],
        "transfers": transfers,
        "transfers_reason": transfers_reason,
        "items": out_items,
        "record": _record(weights.get(creator)),
    }


def _claim_row(c: dict[str, Any], index: TranscriptIndex,
               url: str | None, display: dict[str, Any] | None = None
               ) -> dict[str, Any]:
    """One claim, with its verbatim evidence and a link to the moment.

    The quote for an ``llm:`` claim is the verbatim fragment the extractor
    stored after ``| quote: ``; for a ``cue`` claim the rationale IS the
    keyword window lifted from the item, so it is the quote. Both are untrusted
    third-party prose. ``name`` stays the stored spoken string ("rayan
    cherki"); ``display_name``/``resolved``/``disambiguator`` carry the
    canonical read (see ``_naming``), so the drawer never shows one player
    split across two spellings.
    """
    rationale = _s(c.get("rationale")) or ""
    quote = rationale.split("| quote: ", 1)[1] if "| quote: " in rationale else rationale
    start_s = index.find(quote)
    code = int(c["player_code"])
    raw = str(c.get("player_name") or c.get("surface_form") or "")
    return {
        "code": code,
        "name": raw,
        **_naming(code, raw, display),
        "action": str(c.get("action")),
        "confidence": _f(c.get("confidence"), 3),
        "quote": quote or None,
        "start_s": _f(start_s, 2),
        "deep_link": deep_link(url, start_s),
        "extractor": str(c.get("extractor") or "cue"),
        "gameweek": _i(c.get("gameweek")),
        "published_at": _iso(c.get("published_at")),
    }


def _squad(wh, entry: dict[str, Any] | None, present: set[str],
           moment: dt.datetime, gw: int | None = None
           ) -> tuple[list[dict[str, Any]] | None, str | None, dict[str, Any]]:
    """The creator's locked squad FOR A GAMEWEEK, through ``sem_manager_picks``.

    ``fact_manager_pick`` is keyed on (entry_id, season, gw) and a pick's
    ``as_of`` IS the deadline it locked at, so "what was his team in GW2" is a
    read, not a model. This used to answer only ``max(gw)`` -- fine while one
    gameweek had been crawled, and quietly wrong the moment two had: a reader
    scrolling GW2's claims saw GW3's team above them with no label saying so.

    The default is NOT literally "the next gameweek". Picks become public only
    when a deadline passes, so the next gameweek's squad is by definition not
    published yet; defaulting to it would empty this panel for every creator on
    every day of the season. The default is the newest squad that has actually
    LOCKED at or before the next deadline, which is the same intent -- the team
    you are reading about this week -- expressed against what exists.

    Returns ``(squad | None, reason, meta)``. ``meta`` names the gameweek the
    squad is FOR and every gameweek that has one, so "no squad for GW3" is
    never a dead end: it says where to look instead.
    """
    meta: dict[str, Any] = {"gw": None, "requested": None if gw is None else int(gw),
                            "available": []}
    if entry is None:
        return None, ("no verified entry id for this creator, so there is no "
                      "squad to read; see entry_reason"), meta
    if "fact_manager_pick" not in present:
        return None, "fact_manager_pick is not in this warehouse", meta

    # Which gameweeks this entry HAS a crawled squad for. Asked first and
    # separately: it is the answer to "then what do you have?", which is the
    # only useful thing to say when the requested gameweek has nothing.
    have = q(
        wh,
        "SELECT DISTINCT gw FROM sem_manager_picks(?) WHERE entry_id = ? "
        "AND season = ? ORDER BY gw",
        (moment, entry["entry_id"], SEASON_DEFAULT),
    )
    available = sorted({g for g in (_i(r["gw"]) for r in have.to_dict("records"))
                        if g is not None})
    meta["available"] = available
    if not available:
        return None, (
            "picks become public only at a gameweek's deadline and none is "
            "stored for this entry yet"
        ), meta

    target, basis = _squad_gw(wh, gw, available, moment)
    meta["gw"] = target
    if target is None or target not in available:
        return None, (
            f"no crawled squad for GW{int(gw)} for this entry. "
            f"{_gw_list(available)} on file, so the gameweek is selectable; "
            f"this one simply has not been crawled. A squad is public only "
            f"once its deadline has passed."
            if gw is not None else
            f"no squad could be selected. {_gw_list(available)} on file."
        ), meta

    rows = q(
        wh,
        "SELECT gw, code, web_name, multiplier, is_captain FROM sem_manager_picks(?) "
        "WHERE entry_id = ? AND season = ? AND gw = ?",
        (moment, entry["entry_id"], SEASON_DEFAULT, int(target)),
    )
    if rows.empty:  # pragma: no cover - `target` came out of `available`
        return None, (
            f"no crawled squad for GW{int(target)} for this entry. "
            f"{_gw_list(available)} on file."
        ), meta
    prices = q(
        wh, "SELECT code, price, position FROM sem_players(?) WHERE season = ?",
        (moment, SEASON_DEFAULT),
    )
    lookup = {int(r["code"]): r for r in prices.to_dict("records")
              if _i(r["code"]) is not None}
    squad = []
    for r in rows.to_dict("records"):
        code = _i(r["code"])
        info = lookup.get(code or -1, {})
        squad.append({
            "code": code,
            "name": _s(r["web_name"]) or (str(code) if code else "(unknown)"),
            "pos": POSITION_NAME.get(_i(info.get("position")) or 0),
            "price": _f(info.get("price"), 1),
            "multiplier": _i(r["multiplier"]),
            "is_captain": bool(r["is_captain"]),
        })
    return squad, (
        f"locked GW{int(target)} squad, as published at that deadline. {basis} "
        f"{_gw_list(available)} on file for this entry."
    ), meta


def _gw_list(gws: list[int]) -> str:
    """``"GW1 and GW2 are"`` / ``"GW1 is"`` -- what a reader can ask for next."""
    if not gws:
        return "No gameweek is"
    names = [f"GW{g}" for g in gws]
    if len(names) == 1:
        return f"{names[0]} is"
    return f"{', '.join(names[:-1])} and {names[-1]} are"


def _squad_gw(wh, gw: int | None, available: list[int],
              moment: dt.datetime) -> tuple[int | None, str]:
    """Which gameweek's squad to show, and the sentence explaining the choice.

    Deliberately NOT ``_resolve_gw``. That one answers "which gameweek is this
    page about" and correctly returns the next, unplayed one; a squad for an
    unplayed gameweek does not exist yet, because a squad is public only at its
    deadline. So the default here is the newest LOCKED gameweek at or before
    the next deadline -- the same question, answered against a table that can
    only ever be behind it.
    """
    if gw is not None:
        return int(gw), f"GW{int(gw)} was requested explicitly."
    nxt = next_gw(wh, SEASON_DEFAULT, moment)
    if nxt is not None and nxt in available:
        return nxt, (f"GW{nxt} is the next gameweek and its squad is already "
                     f"published.")
    locked = [g for g in available if nxt is None or g <= nxt]
    if locked:
        target = max(locked)
        return target, (
            f"No gameweek was requested. GW{target} is the newest squad that "
            f"has actually locked"
            + (f"; GW{nxt}'s deadline has not passed, so no team is public for "
               f"it yet." if nxt is not None else ".")
        )
    target = max(available)
    return target, (f"No gameweek was requested and no deadline is on file, so "
                    f"this is the newest crawled gameweek, GW{target}.")


def _transfers(wh, entry: dict[str, Any] | None, present: set[str],
               moment: dt.datetime) -> tuple[list[dict[str, Any]], str | None]:
    """Public transfers, through ``sem_manager_transfers(as_of)``.

    ``fact_manager_transfer`` is empty, and that is CORRECT rather than a bug:
    a gameweek's transfers become public only once its deadline passes, and the
    first deadline of the season has no transfers behind it at all -- everyone's
    GW1 squad is their initial pick. The reason string says that, so nobody
    reads the empty list as a broken ingest.
    """
    base = (
        "a gameweek's transfers become public only after its deadline, and the "
        "season's first gameweek has none behind it. Every GW1 squad is an "
        "initial selection, not a transfer. An empty list here is the correct "
        "state of the world, not a missing ingest."
    )
    if entry is None:
        return [], ("no verified entry id for this creator, so no transfer "
                    "history can be read; see entry_reason")
    if "fact_manager_transfer" not in present:
        return [], "fact_manager_transfer is not in this warehouse. " + base
    rows = q(
        wh,
        "SELECT gw, player_in, code_in, player_out, code_out, time_utc "
        "FROM sem_manager_transfers(?) WHERE entry_id = ? AND season = ? "
        "ORDER BY gw DESC, time_utc DESC",
        (moment, entry["entry_id"], SEASON_DEFAULT),
    )
    if rows.empty:
        return [], base
    return [
        {
            "gw": int(r["gw"]),
            "in_name": _s(r["player_in"]),
            "in_code": _i(r["code_in"]),
            "out_name": _s(r["player_out"]),
            "out_code": _i(r["code_out"]),
            "time_utc": _iso(r["time_utc"]),
        }
        for r in rows.to_dict("records")
    ], None

register_script(
    name="creator_detail",
    fn=creator_detail,
    params_schema=DETAIL_PARAMS,
    result_schema=DETAIL_RESULT,
    title="Creator detail",
    description="One creator expanded: items, analyses, timestamped claims "
                "with deep links, and their public FPL trail where a verified "
                "entry id exists.",
)
