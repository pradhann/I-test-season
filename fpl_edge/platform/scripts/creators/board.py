"""`creator_board`: who is saying what this gameweek, and the consensus."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.ingest.content.urls import canonical_key
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import POSITION_NAME, SEASON_DEFAULT, empty, q
from fpl_edge.platform.scripts.creators.identity import (
    _CONTENT_TABLES,
    _NO_TRANSCRIPT,
    _PANEL_SHOW_TABLE,
    _PANEL_TABLE,
    UTC,
    _analyses,
    _content_store,
    _display_index,
    _entries,
    _f,
    _i,
    _iso,
    _items,
    _naming,
    _panel_roster,
    _panel_squads,
    _record,
    _resolve_gw,
    _resolver,
    _s,
    _stamps,
    _tables_present,
    _take,
    _take_reason,
    _transcripts,
    _weights_as_of,
)
from fpl_edge.platform.scripts.creators.schema import BOARD_PARAMS, BOARD_RESULT

# ---------------------------------------------------------------------------
# creator_board

def _panel_shows(wh, present) -> tuple[set[str], str | None]:
    """The shows the owner's panel actually appears on, and why if unknown.

    Returns an empty set with a reason when the roster cannot be read, and the
    caller then shows everything rather than nothing -- an unreadable panel is
    a missing filter, not an empty world.
    """
    if _PANEL_TABLE not in present or _PANEL_SHOW_TABLE not in present:
        return set(), "no panel roster in this warehouse; showing every source"
    try:
        rows = wh.sql(
            f"SELECT DISTINCT s.show_creator FROM {_PANEL_SHOW_TABLE} s "
            f"JOIN {_PANEL_TABLE} p USING (person_key) WHERE p.active")
    except Exception as exc:  # noqa: BLE001 - a panel reports, it never crashes
        return set(), f"panel roster unreadable ({type(exc).__name__})"
    shows = {str(x) for x in rows["show_creator"] if x is not None}
    if not shows:
        return set(), "the panel roster is empty; showing every source"
    return shows, None


def _my_roles(wh, enabled: bool) -> tuple[dict[int, dict] | None, str | None]:
    """The owner's 15 with their multipliers, or (None, why-not).

    Shared with the ownership panel through ``common`` -- ``_squad_state`` is the one
    place that knows the private-API -> public-picks -> manual ladder and the
    rule for deriving a multiplier from a role when a pre-deadline payload
    carries none. A second implementation here would drift from it, and the
    symptom would be two panels disagreeing about the reader's own captain.
    """
    if not enabled:
        return None, ("the squad read was disabled by the caller "
                      "(`mine: false`), so no ownership is claimed either way")
    from fpl_edge.platform.scripts.common import _squad_state

    roles, meta = _squad_state(wh, SEASON_DEFAULT)
    if roles is None:
        return None, str(meta.get("note") or "your squad could not be read")
    return roles, None


def creator_board(wh, *, days: int = 30, gw: int | None = None,
                  scope: str = "panel", mine: bool = True) -> dict[str, Any]:
    """Every tracked creator: reach, latest item, summarised take, track record.

    One row per creator with their sources and probe state, the most recent
    real item, the LLM take on it when one exists (and a readable reason when
    it does not), and their measured record at this instant. ``consensus`` is
    the cross-creator view for the gameweek: who is buying, selling and
    captaining whom, split by extractor so a keyword hit and a semantic read
    stay distinguishable.
    """
    moment = dt.datetime.now(UTC)
    present = _tables_present(wh, _CONTENT_TABLES + ("transcript_segment",
                                                     "content_analysis",
                                                     "creator_score",
                                                     _PANEL_TABLE,
                                                     "panel_person",
                                                     "panel_person_show",
                                                     "fact_manager_pick",
                                                     "fact_manager_transfer"))
    missing = [t for t in _CONTENT_TABLES if t not in present]
    if missing:
        return empty(
            f"the creator corpus is not in this warehouse ({', '.join(missing)} "
            f"missing). Run `python -m fpl_edge.ingest.content.pipeline ingest` "
            f"to build it."
        )

    sources = q(
        wh,
        "SELECT source_key, creator, kind, url, policy, last_http_status, "
        "last_error FROM content_source ORDER BY creator, source_key",
    )
    items = _items(wh, moment)
    if items.empty and sources.empty:
        return empty(
            "the creator corpus exists but holds no sources and no items yet; "
            "run the content pipeline's `ingest` step."
        )

    since = moment - dt.timedelta(days=int(days))
    items = items.assign(
        _published=items["published_at"].map(_iso),
        _ts=_stamps(items["published_at"]),
        _canon=[canonical_key(u, i) for u, i in
                zip(items["url"], items["item_id"])],
    )
    in_window = items[items["_ts"] >= since]

    # -- claims, through the ONE sanctioned read ---------------------------
    claims = _content_store(wh).claims_visible_at(moment)
    if not claims.empty:
        claims = claims[_stamps(claims["published_at"]) >= since]

    # -- which gameweek the takes are about --------------------------------
    # The ladder lives in `_resolve_gw`, shared with `player_chatter`: one rule
    # for "which gameweek is this page about", so the board and the per-player
    # strip beneath it can never name different weeks from the same warehouse.
    gw, gw_reason = _resolve_gw(wh, gw, moment, claims)

    # -- per-source last item, so a silent source is visible as silent ------
    # `items` is ordered published_at DESC by SQL, so the first row seen for a
    # source key is its newest item. Taking max() over the stringified stamps
    # would compare "…54.706755+00:00" against "…54+00:00" lexically.
    last_by_source: dict[str, str] = {}
    for r in items.to_dict("records"):
        last_by_source.setdefault(str(r["source_key"]), str(r["_published"]))

    registry_keys = set(sources["source_key"].astype(str)) if not sources.empty else set()
    by_creator_sources: dict[str, list[dict[str, Any]]] = {}
    for r in (sources.to_dict("records") if not sources.empty else []):
        by_creator_sources.setdefault(str(r["creator"]), []).append({
            "key": str(r["source_key"]),
            "kind": str(r["kind"]),
            "url": _s(r["url"]),
            "last_item_at": last_by_source.get(str(r["source_key"])),
            "last_status": _i(r["last_http_status"]),
            "discovery": "manual",
            "policy": _s(r["policy"]),
            "last_error": _s(r["last_error"]),
        })
    # Source keys that exist only because ingest materialised them while
    # handling an item (today: `user_link`, behind shared links). They are real
    # sources of real items and dropping them would lose the items.
    for r in items.to_dict("records"):
        key = str(r["source_key"])
        if key in registry_keys:
            continue
        bucket = by_creator_sources.setdefault(str(r["creator"]), [])
        if any(s["key"] == key for s in bucket):
            continue
        bucket.append({
            "key": key, "kind": str(r["kind"]), "url": None,
            "last_item_at": last_by_source.get(key), "last_status": None,
            "discovery": "auto", "policy": None, "last_error": None,
        })

    weights = _weights_as_of(wh, moment) if "creator_score" in present else {}
    entries, entry_reason = _entries(wh, present, moment)

    # -- the latest item per creator, deduplicated on the real publication --
    latest_rows: dict[str, dict[str, Any]] = {}
    canon_members: dict[str, set[str]] = {}
    for r in items.to_dict("records"):
        canon_members.setdefault(str(r["_canon"]), set()).add(str(r["item_id"]))
    for r in items.to_dict("records"):  # already newest-first
        creator = str(r["creator"])
        if creator not in latest_rows:
            latest_rows[creator] = r

    # Every stored row of the same publication is a candidate for the analysis
    # AND for the transcript -- the two live on different rows in the live data.
    latest_family: dict[str, set[str]] = {
        c: canon_members.get(str(r["_canon"]), {str(r["item_id"])})
        for c, r in latest_rows.items()
    }
    wanted = {i for fam in latest_family.values() for i in fam}
    analyses = _analyses(wh, wanted) if "content_analysis" in present else {}
    transcripts = (_transcripts(wh, wanted)
                   if "transcript_segment" in present else {})
    resolver = _resolver(wh, SEASON_DEFAULT, moment)
    display = _display_index(wh, moment)

    # Publications, not stored rows: the same video under `watch?v=` and
    # `youtube.com/live/` is one thing this creator published.
    counts_all = items.groupby("creator")["_canon"].nunique().to_dict()
    counts_win = in_window.groupby("creator")["_canon"].nunique().to_dict()
    claim_counts = (claims.groupby("creator").size().to_dict()
                    if not claims.empty else {})

    names = sorted(set(by_creator_sources) | set(counts_all) | set(claim_counts))
    # Narrow to the panel's shows unless asked for everything. A panel that is
    # absent or empty degrades UPWARD to the whole corpus with a stated reason,
    # never DOWNWARD to nothing: an unreadable roster must not look like a
    # world in which nobody says anything.
    panel_shows, panel_reason = _panel_shows(wh, present)
    scoped_out: list[str] = []
    if scope == "panel" and panel_shows:
        scoped_out = sorted(n for n in names if n not in panel_shows)
        names = [n for n in names if n in panel_shows]
        # THE CLAIMS TOO, not only the creator list. The board the UI draws is
        # built from `consensus`, which is built from `claims` -- so scoping
        # just `names` left the page saying "the panel across 7 shows" above a
        # board where 48 of 58 voices came from the 24 sources it listed as
        # excluded, and 32 of 39 rows had no panel voice at all. A scope that
        # filters the list but not the thing the list describes is worse than
        # no scope: it puts a true label on untrue content.
        claims = claims[claims["creator"].isin(panel_shows)]
    creators: list[dict[str, Any]] = []
    for name in names:
        row = latest_rows.get(name)
        family = latest_family.get(name, set())
        analysis = model = None
        for item_id in sorted(family):
            if item_id in analyses:
                analysis, model = analyses[item_id]
                break
        index = _NO_TRANSCRIPT
        for item_id in sorted(family):
            if item_id in transcripts:
                index = transcripts[item_id]
                break
        url = _s(row["url"]) if row is not None else None
        take = _take(analysis, model, resolver, index, url, display)

        src = sorted(by_creator_sources.get(name, []), key=lambda s: s["key"])
        kinds = sorted({s["kind"] for s in src}
                       | ({str(row["kind"])} if row is not None else set()))
        creators.append({
            "creator": name,
            "kinds": kinds,
            "sources": src,
            "n_items": int(counts_all.get(name, 0)),
            "n_items_window": int(counts_win.get(name, 0)),
            "n_claims_window": int(claim_counts.get(name, 0)),
            "last_item_at": _s(row["_published"]) if row is not None else None,
            "latest": None if row is None else {
                "item_id": str(row["item_id"]),
                "title": str(row["title"]),
                "url": url,
                "published_at": str(row["_published"]),
                "kind": str(row["kind"]),
                "text_source": str(row["text_source"]),
            },
            "latest_reason": None if row is not None else (
                "every source registered for this creator has been probed and "
                "has yielded no item yet; see sources[].last_status"
            ),
            "take": take,
            "take_reason": None if take is not None else _take_reason(
                None if row is None else {"text_source": row["text_source"]},
                bool(index),
            ),
            "record": _record(weights.get(name)),
            "entry": entries.get(name),
            "entry_reason": None if name in entries else entry_reason,
        })

    creators.sort(key=lambda c: (-(c["n_claims_window"]),
                                 -(c["n_items_window"]), c["creator"]))

    # The Deadline Board's two hard-data columns. Both are computed once for
    # the whole board and looked up per row: `mine` is one squad read, and
    # `panel_owned` is one pass over the panel's crawled picks.
    roster, _roster_reason = _panel_roster(wh, present)
    panel_by_code, panel_meta = _panel_squads(wh, roster, present, moment)
    roles, mine_reason = _my_roles(wh, mine)

    return {
        "as_of": moment.isoformat(),
        "window_days": int(days),
        "gw": gw,
        "gw_reason": gw_reason,
        "scope": {"applied": scope, "shows": sorted(panel_shows),
                  "excluded": scoped_out, "reason": panel_reason},
        "creators": creators,
        "consensus": _consensus(wh, claims, gw, moment, roles, panel_by_code,
                                panel_meta, display),
        "record_note": _record_note(weights),
        "coverage": _analysis_coverage(wh, present, days, moment),
        "mine_reason": mine_reason,
        "panel_squads": panel_meta,
    }


def _analysis_coverage(wh, present: set[str], days: int,
                       moment: dt.datetime) -> dict[str, Any] | None:
    """How much of the fetched content has actually been read.

    Fetching and analysing are separate steps on separate budgets. An item
    reaches ``content_item`` the night it publishes; a claim is extracted from
    it only when the analysis queue reaches it, and that queue is capped so a
    backlog is the normal state rather than a fault. The board could not say
    so, which made "this creator's latest take is four days old" ambiguous
    between "they have not spoken" and "we have not read them".
    """
    if "content_item" not in present or "content_claim" not in present:
        return None
    since = moment - dt.timedelta(days=int(days))
    totals = q(
        wh,
        "SELECT count(*) AS items, "
        "  count(*) FILTER (WHERE i.item_id IN "
        "    (SELECT item_id FROM content_claim)) AS analysed "
        "FROM content_item i WHERE i.published_at >= ?",
        (since,),
    )
    if totals.empty:
        return None
    items = _i(totals.iloc[0]["items"]) or 0
    analysed = _i(totals.iloc[0]["analysed"]) or 0
    unread = items - analysed
    by_source = q(
        wh,
        "SELECT i.source_key, count(*) AS unread, "
        "       max(i.published_at) AS newest "
        "FROM content_item i "
        "WHERE i.published_at >= ? "
        "  AND i.item_id NOT IN (SELECT item_id FROM content_claim) "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 8",
        (since,),
    )
    rows = [{"source_key": str(r["source_key"]),
             "unread": _i(r["unread"]) or 0,
             "newest_unread": _iso(r["newest"])}
            for r in by_source.to_dict("records")]
    newest = max((r["newest_unread"] for r in rows if r["newest_unread"]),
                 default=None)
    if not unread:
        note = (f"Every one of the {items} items published in the last "
                f"{days} days has been read.")
    else:
        note = (f"{analysed} of {items} items published in the last {days} "
                f"days have been read. {unread} are fetched and still queued: "
                f"the analysis budget caps how many are read per night, so a "
                f"take can be older than the show it came from.")
    return {"window_days": int(days), "items": items, "analysed": analysed,
            "newest_unread": newest, "note": note, "by_source": rows}


def _record_note(weights: dict[str, dict[str, Any]]) -> str:
    earned = sorted(c for c, r in weights.items() if (_f(r.get("weight"), 4) or 0) > 0)
    scored = sum(1 for r in weights.values() if (_i(r.get("claims_scored")) or 0) > 0)
    if not weights:
        return ("No creator has been scored yet: creator_score is empty at this "
                "instant. This is an unmeasured record, not a measured zero.")
    if not earned:
        return (
            f"No creator has beaten a coin flip yet. {scored} of {len(weights)} "
            f"measured creators have at least one scored claim and none has a "
            f"95% Wilson lower bound above 0.5 over 25+ claims, so every earned "
            f"weight is 0.0. That is a measured result, not missing data."
        )
    return (f"{len(earned)} of {len(weights)} measured creators carry a non-zero "
            f"earned weight: {', '.join(earned)}.")


def _mine_row(roles: dict[int, dict] | None, code: int) -> dict[str, Any]:
    """Is he in the owner's squad, and how. Unknown is null, never false.

    ``in_squad: false`` is a claim -- "he is not in your team" -- and it needs
    a squad read behind it. With no read there is no evidence either way, so
    the field is null and ``mine_reason`` says why once for the board.
    """
    if roles is None:
        return {"in_squad": None, "multiplier": None, "role": None,
                "source": None}
    held = roles.get(int(code))
    if held is None:
        return {"in_squad": False, "multiplier": None, "role": None,
                "source": None}
    return {
        "in_squad": True,
        "multiplier": _i(held.get("mult")),
        "role": _s(held.get("role")),
        "source": _s(held.get("mult_source")),
    }


def _consensus(wh, claims, gw: int | None, moment: dt.datetime,
               roles: dict[int, dict] | None = None,
               panel_by_code: dict[int, list[dict[str, Any]]] | None = None,
               panel_meta: dict[str, Any] | None = None,
               display: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Who agrees with whom about which player, for the gameweek in question.

    Deduplicated to one claim per (creator, player, action, gameweek) through
    the shared helper: a creator publishing the same view on a podcast, its
    show notes and a video is one opinion, not three, and the same video stored
    twice under two URL forms is not two either.
    """
    if claims is None or claims.empty:
        return []
    frame = claims
    if gw is not None:
        frame = frame[frame["gameweek"].astype("Int64") == int(gw)]
    if frame.empty:
        return []

    from fpl_edge.ingest.content.consensus import deduplicate

    frame, _ = deduplicate(frame)
    if frame.empty:
        return []

    codes = sorted({int(c) for c in frame["player_code"].dropna().tolist()})
    meta: dict[int, dict[str, Any]] = {}
    if codes:
        rows = q(
            wh,
            "SELECT code, web_name, position, team, price, selected_by_pct "
            "FROM sem_players(?) WHERE season = ? AND code IN ("
            + ", ".join("?" for _ in codes) + ")",
            (moment, SEASON_DEFAULT, *codes),
        )
        for r in rows.to_dict("records"):
            meta[int(r["code"])] = r

    buckets = {"buy": ("buy",), "sell": ("sell",),
               "captain": ("captain", "triple_captain")}
    acc: dict[int, dict[str, Any]] = {}
    for r in frame.to_dict("records"):
        code = _i(r.get("player_code"))
        if code is None:
            continue
        entry = acc.setdefault(code, {
            "name": str(r.get("player_name") or code),
            "sides": {k: {"creators": set(), "n_cue": 0, "n_llm": 0}
                      for k in buckets},
        })
        action = str(r.get("action"))
        extractor = str(r.get("extractor") or "cue")
        for key, actions in buckets.items():
            if action in actions:
                side = entry["sides"][key]
                side["creators"].add(str(r.get("creator")))
                if extractor.startswith("llm"):
                    side["n_llm"] += 1
                else:
                    side["n_cue"] += 1

    out: list[dict[str, Any]] = []
    for code, entry in acc.items():
        info = meta.get(code, {})
        sides = {
            key: {
                "n": len(side["creators"]),
                "creators": sorted(side["creators"]),
                "n_cue": side["n_cue"],
                "n_llm": side["n_llm"],
            }
            for key, side in entry["sides"].items()
        }
        if not any(s["n"] for s in sides.values()):
            continue
        name = _s(info.get("web_name")) or entry["name"]
        naming = _naming(code, entry["name"], display)
        holders = (panel_by_code or {}).get(code, [])
        out.append({
            "code": code,
            "name": str(name),
            # Canonical read-side naming: `resolved` is false when the code is
            # absent from the pool (a transferred-out or misresolved player),
            # in which case `name` above is the raw claim string and stays so.
            "resolved": naming["resolved"],
            "disambiguator": naming["disambiguator"],
            "pos": POSITION_NAME.get(_i(info.get("position")) or 0),
            "team": _s(info.get("team")),
            "price": _f(info.get("price"), 1),
            "own_pct": _f(info.get("selected_by_pct"), 1),
            "buy": sides["buy"],
            "sell": sides["sell"],
            "captain": sides["captain"],
            "net": sides["buy"]["n"] - sides["sell"]["n"],
            "mine": _mine_row(roles, code),
            # `of` is the number of panel squads actually READ, not the panel
            # size: 2 of 7 is a measurement, 2 of 16 would be a lie about the
            # nine teams nobody has crawled.
            "panel_owned": {
                "n": len(holders),
                "of": int((panel_meta or {}).get("known") or 0),
                "people": [h["person"] for h in holders],
            },
        })
    out.sort(key=lambda r: (-r["net"], -r["captain"]["n"], r["name"]))
    return out


register_script(
    name="creator_board",
    fn=creator_board,
    params_schema=BOARD_PARAMS,
    result_schema=BOARD_RESULT,
    title="Creators",
    description="Every tracked FPL creator: reach, their latest item, the "
                "summarised take on it, their measured track record, and the "
                "cross-creator consensus for the gameweek.",
)
