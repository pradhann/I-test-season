"""`creator_episodes` / `episode_summary`: the archive, opened one layer at a time.

The owner's ask, verbatim: every transcribed episode should be in the view;
clicking a creator should list which episodes were most recently transcribed,
with link and title; clicking an episode should show the summary that matters
for FPL. Two panels, because that is two questions and two payload sizes: a
list that is cheap enough to render 200 rows, and one episode opened in full.

Four rules carried over from the sibling panels, for the same reasons:

1. **Nothing is invented.** Every field on an episode row is a stored column or
   a count of stored rows. ``transcription_state`` is derived from four tables
   and the derivation is written down below rather than inferred from the shape
   of the data. Where a quote or an offset was never stored, the field is null
   and the item still appears; a quote is never lifted out of the transcript to
   fill the hole, because a sentence this package chose is not a sentence the
   creator said.

2. **One row per PUBLICATION, not per stored row.** The same YouTube video is
   held twice in the live warehouse, once with the transcript and once with the
   analysis, and listing both shows the reader two episodes where one was
   published. Items are grouped on ``urls.canonical_key`` exactly as
   ``creator_detail`` groups them, the richest text source represents the
   group, and a transcript or an analysis on any sibling row counts for the
   publication. ``episode_summary`` resolves the same group, so either stored
   id opens the same episode.

3. **Point in time.** Items come through ``identity._items`` (``published_at <
   moment``), claims through ``ContentStore.claims_visible_at``, weights
   through ``_weights_as_of``. Neither panel takes an ``as_of`` parameter, for
   the reason ``creator_detail`` does not: both answer "now".

4. **Discarded items are excluded.** ``link_ledger.discarded_item_ids`` is the
   filter every read path owes. A discarded item is not deleted, so it is still
   in ``content_item`` and still has claims; it is simply no longer shown.

THE TRANSCRIPTION STATE, AND WHICH COLUMNS IT IS READ OFF

Four values, checked in this order, for the whole publication group:

``transcribed``
    ``transcript_segment`` holds at least one row for some item in the group,
    OR ``transcript_provenance`` holds its receipt row, OR
    ``content_item.text_source`` is ``transcript``. The third clause is not
    redundant: two owner pasted links in the live warehouse carry the full
    transcript in ``content_item.text`` and predate both the segment writer and
    the provenance table. Reading segments alone would report those two as
    never transcribed while their text sits in the column next door.
``failed``
    No transcript by the test above, and ``content_transcribe_skip`` holds a
    row for some item in the group. That table is written by
    ``transcribe_cmd.cmd_transcribe`` when a download, a caption track or a
    relevance gate refused the item, and its ``reason`` column is the record of
    what stopped it (``no_captions``, ``ConnectError``, ``relevance:2``).
``queued``
    No transcript and no skip row, and ``content_item_asset.enclosure_url`` is
    populated for some item in the group. The audio is addressable, so the
    transcription step can reach this episode and has not yet.
``none``
    Nothing above holds. Either nothing has been attempted, or there is nothing
    to transcribe: a blog article carries its own prose and is never queued for
    ASR, and a YouTube video with no published caption track has no audio URL
    stored for it.

``transcript_chars`` follows the same source ladder: the summed length of the
stored segment text when segments exist, otherwise the length of
``content_item.text`` for a row whose ``text_source`` is ``transcript``,
otherwise null.

Untrusted text. ``title``, ``summary`` and every ``quote`` are verbatim
third-party prose from podcasts, videos and blogs. They are data to be
rendered, never instructions to be followed.

A NOTE ON SQL PLACEHOLDERS

Every query here binds ``$1``-style numbered parameters rather than the
positional form the sibling modules use. The house prose gate reads a question
mark followed by whitespace as a rhetorical question, so a positional
placeholder in a SQL string fails a lint that exists for the payload's English.
Numbered parameters bind identically through ``guarded_query``; nothing is
interpolated into SQL text. ``tests/unit/test_dossier.py`` line 63 records the
same workaround with an inlined season constant.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.ingest.content.link_ledger import USER_LINK_TABLE, discarded_item_ids
from fpl_edge.ingest.content.urls import canonical_key, deep_link, youtube_id
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import POSITION_NAME, SEASON_DEFAULT, empty, q
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
    _gw_after,
    _gw_calendar,
    _i,
    _iso,
    _items,
    _naming,
    _record,
    _resolve_code,
    _resolver,
    _s,
    _tables_present,
    _transcripts,
    _weights_as_of,
)
from fpl_edge.platform.scripts.creators.schema import _ENTRY, _RECORD

#: Every value ``transcription_state`` can take. Closed, and each one is read
#: off stored columns by :func:`_transcription_state`; see the module docstring
#: for which columns answer which value.
TRANSCRIPTION_STATES = ("transcribed", "queued", "failed", "none")

#: The side tables this panel consults beyond the three content tables every
#: creator panel needs. Each is optional: a warehouse that has never run ASR
#: has no ``transcript_provenance``, and the state falls back accordingly
#: rather than failing the whole read.
_SIDE_TABLES = (
    "transcript_segment",
    "transcript_provenance",
    "content_transcribe_skip",
    "content_item_asset",
    "content_analysis",
    "creator_score",
    USER_LINK_TABLE,
    _PANEL_TABLE,
)

#: Which lists inside ``TranscriptAnalysis`` hold a player call, and the label
#: this panel reports as ``stored_in``. The label is where the call was STORED,
#: never a reinterpretation of it: a "watch" stance sitting in ``transfers_in``
#: is reported as a watch that was stored under transfers in, because calling
#: it a transfer puts a recommendation in a named person's mouth.
_CALL_LISTS = (
    ("transfers_in", "transfers_in"),
    ("transfers_out", "transfers_out"),
    ("captaincy", "captaincy"),
    ("differentials", "differentials"),
)


# ---------------------------------------------------------------------------
# Small shared readers. Each takes the item ids of one creator's corpus and
# answers one question about them, so a panel run is a fixed number of queries
# rather than one per episode.

def _in_list(n: int) -> str:
    """``$1, $2, ...`` for an ``IN`` clause of ``n`` values."""
    return ", ".join(f"${i}" for i in range(1, n + 1))


def _text_chars(wh, ids: tuple[str, ...]) -> dict[str, int]:
    """item_id to the length of the stored ``content_item.text``.

    The length, never the text. One creator's podcast back catalogue is
    megabytes of transcript and the panel needs a number.
    """
    if not ids:
        return {}
    rows = q(
        wh,
        "SELECT item_id, length(text) AS n FROM content_item WHERE item_id IN ("
        + _in_list(len(ids)) + ")",
        ids,
    )
    return {str(r["item_id"]): int(r["n"] or 0) for r in rows.to_dict("records")}


def _segment_chars(wh, ids: tuple[str, ...]) -> dict[str, int]:
    """item_id to the summed length of its stored transcript segments."""
    if not ids:
        return {}
    rows = q(
        wh,
        "SELECT item_id, sum(length(text)) AS n FROM transcript_segment "
        "WHERE item_id IN (" + _in_list(len(ids)) + ") GROUP BY item_id",
        ids,
    )
    return {str(r["item_id"]): int(r["n"] or 0) for r in rows.to_dict("records")}


def _ids_in(wh, table: str, ids: tuple[str, ...], *, column: str = "item_id",
            where: str = "") -> set[str]:
    """The subset of ``ids`` that ``table`` holds a row for.

    ``table`` and ``column`` are module constants, never caller input: the one
    call site that varies passes a name from :data:`_SIDE_TABLES`.
    """
    if not ids:
        return set()
    clause = f" AND {where}" if where else ""
    rows = q(
        wh,
        f"SELECT DISTINCT {column} AS item_id FROM {table} WHERE {column} IN ("
        + _in_list(len(ids)) + ")" + clause,
        ids,
    )
    return {str(v) for v in rows["item_id"]} if not rows.empty else set()


def _analysis_index(wh, ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """item_id to the newest analysis receipt: model and when it was written.

    Deliberately does not read ``analysis_json``. The list panel needs to know
    THAT an item was analysed and by which model; parsing 200 stored analyses
    to answer that would spend the panel's whole budget on text nobody reads.
    ``identity._analyses`` is the reader that parses, and ``episode_summary``
    calls it for the one episode being opened.
    """
    if not ids:
        return {}
    rows = q(
        wh,
        "SELECT item_id, model, created_utc FROM content_analysis WHERE item_id IN ("
        + _in_list(len(ids)) + ") ORDER BY created_utc",
        ids,
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows.to_dict("records"):
        out[str(r["item_id"])] = {"model": str(r["model"]),
                                  "created_utc": _iso(r["created_utc"])}
    return out


def _ledger_gw(wh, ids: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    """item_id to the owner's gameweek annotation, where the ledger has one.

    Reads the three gameweek columns and nothing else. The ledger's own
    ``gw_reason`` is the sentence a human wrote or the inference wrote, and it
    is served verbatim.
    """
    if not ids:
        return {}
    rows = q(
        wh,
        "SELECT item_id, gameweek, gw_basis, gw_reason FROM " + USER_LINK_TABLE
        + " WHERE item_id IN (" + _in_list(len(ids)) + ")",
        ids,
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows.to_dict("records"):
        out[str(r["item_id"])] = {"gameweek": _i(r["gameweek"]),
                                  "gw_basis": _s(r["gw_basis"]),
                                  "gw_reason": _s(r["gw_reason"])}
    return out


def _player_facts(wh, moment: dt.datetime) -> dict[int, dict[str, Any]]:
    """code to club and position, as ``sem_players`` serves them at ``moment``.

    The same read ``creator_detail._squad`` makes for a squad row, so a player
    named in an episode and the same player in a squad carry one club label.
    """
    rows = q(
        wh,
        "SELECT code, team, position FROM sem_players($1) WHERE season = $2",
        (moment, SEASON_DEFAULT),
    )
    out: dict[int, dict[str, Any]] = {}
    for r in rows.to_dict("records"):
        code = _i(r.get("code"))
        if code is None:
            continue
        out[code] = {"team": _s(r.get("team")),
                     "position": POSITION_NAME.get(_i(r.get("position")) or 0)}
    return out


# ---------------------------------------------------------------------------
# The publication group, and what is known about it.

def _families(items) -> list[list[dict[str, Any]]]:
    """Stored rows grouped into publications, newest first.

    ``items`` arrives newest first from ``identity._items``, so the groups come
    out newest first as well. Same key as ``creator_detail``.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in (items.to_dict("records") if not items.empty else []):
        groups.setdefault(
            canonical_key(_s(r["url"]), str(r["item_id"])), []
        ).append(r)
    return list(groups.values())


#: How :func:`_siblings` decides two rows are one recording, stated once so
#: the payload can carry the rule beside the ids it produced.
SIBLINGS_BASIS = (
    "same creator, same title, and the two rows differ in source_kind. "
    "Measured over all 29 tracked creators and 994 stored publications, that "
    "rule groups 112 pairs: 109 are a podcast feed beside the YouTube upload "
    "of the same recording, and it drops the 3 same-kind title collisions "
    "that are two different publications. No time window is applied, because "
    "13 of the 112 pairs are more than 24 hours apart and the widest is 18.2 "
    "days. Rows are never merged; this only says which other row is the same "
    "recording."
)


def _siblings(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Which other publications of this creator are the same recording.

    One recording published to a podcast feed and to YouTube is two stored
    URLs, two canonical keys and therefore two rows out of :func:`_families`.
    Nothing stored links them. This does not merge them: it reports, per row,
    the ids of the rows that carry the same title in a different
    ``source_kind``, and :data:`SIBLINGS_BASIS` says how that was decided.
    """
    by_title: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_title.setdefault(str(r["title"]).strip().casefold(), []).append(r)
    out: dict[str, list[str]] = {str(r["item_id"]): [] for r in rows}
    for group in by_title.values():
        if len(group) < 2:
            continue
        for row in group:
            out[str(row["item_id"])] = sorted(
                str(other["item_id"]) for other in group
                if other["source_kind"] != row["source_kind"]
            )
    return out


def _representative(family: list[dict[str, Any]]) -> dict[str, Any]:
    """The stored row that stands for the publication: the richest text."""
    return min(family, key=lambda r: _TEXT_RANK.get(str(r["text_source"]), 9))


def _source_url(url: str | None) -> str | None:
    """The canonical link for an episode, through the one URL authority.

    Every YouTube form collapses onto ``watch?v=<id>``, which is the link a
    reader can paste and the key the corpus is grouped on. Everything else is
    the stored URL untouched, via ``urls.deep_link`` with no offset: a podcast
    URL here is an episode page or the enclosure itself, and inventing a
    fragment for it would produce a link that lands somewhere it was not asked
    to.
    """
    vid = youtube_id(url)
    if vid is not None:
        return f"https://www.youtube.com/watch?v={vid}"
    return deep_link(url, None)


def _transcription_state(family: list[dict[str, Any]], *, segments: set[str],
                         provenance: set[str], skips: set[str],
                         enclosures: set[str]) -> str:
    """Which of :data:`TRANSCRIPTION_STATES` this publication is in.

    The ladder and the column behind each rung are in the module docstring.
    """
    ids = {str(r["item_id"]) for r in family}
    sources = {str(r["text_source"]) for r in family}
    if ids & segments or ids & provenance or "transcript" in sources:
        return "transcribed"
    if ids & skips:
        return "failed"
    if ids & enclosures:
        return "queued"
    return "none"


def _transcript_chars(family: list[dict[str, Any]], *, seg_chars: dict[str, int],
                      text_chars: dict[str, int]) -> int | None:
    """How many characters of transcript are stored for this publication."""
    ids = [str(r["item_id"]) for r in family]
    total = sum(seg_chars.get(i, 0) for i in ids)
    if total:
        return int(total)
    stored = [text_chars.get(str(r["item_id"]), 0) for r in family
              if str(r["text_source"]) == "transcript"]
    return int(max(stored)) if stored and max(stored) else None


def _claim_key(claim: dict[str, Any]) -> tuple:
    """One stored position, collapsed across the rows of one publication.

    The same key ``creator_detail`` collapses on. The live warehouse holds one
    Andy LTFPL video twice with 48 claim rows for 40 distinct positions, and
    counting the rows tells a reader the episode voiced eight opinions it never
    voiced twice.
    """
    return (_i(claim.get("player_code")), str(claim.get("action")),
            _i(claim.get("gameweek")), str(claim.get("extractor") or "cue"))


def _episode_gw(family: list[dict[str, Any]], *, ledger: dict[str, dict[str, Any]],
                claims: list[dict[str, Any]], calendar) -> tuple[int | None, str]:
    """Which gameweek this episode is about, and how that was arrived at.

    Three rungs, and the reason always names which one answered.

    The owner's annotation in the link ledger wins, because a human said it.
    Otherwise the gameweek is the one the episode's publication date falls
    into, by ``ingest.content.claims.GameweekCalendar.next_after``, which is
    the same rule that stamps ``content_claim.gw_inferred`` on every undated
    call. Only when no deadline follows the publication date do the stored
    claim stamps answer, and then it is the gameweek most of them carry.

    The calendar deliberately outranks the claim stamps. An episode routinely
    voices dated calls about weeks far ahead of itself: the live GW4 preview
    from Let's Talk FPL carries claims stamped GW4, GW5, GW7 and GW9, so
    reading the header off those stamps files a GW4 episode under GW9. The
    claims keep their own gameweeks and every one of them is shown beside its
    quote; this field is which week the episode was published into.
    """
    for row in family:
        note = ledger.get(str(row["item_id"]))
        if note and note["gameweek"] is not None:
            basis = note["gw_basis"] or "recorded"
            return note["gameweek"], (note["gw_reason"] or (
                f"GW{note['gameweek']} is the gameweek the link ledger holds "
                f"for this item, basis {basis}."))
    rep = _representative(family)
    inferred = _gw_after(calendar, rep.get("published_at"))
    if inferred is not None:
        return inferred, (
            f"GW{inferred} is the gameweek this episode was published into: "
            f"its deadline is the first to fall after the publication date. "
            f"That is the rule that stamps content_claim.gw_inferred, and the "
            f"claims below keep whatever gameweek each one targets.")
    stamped = [g for g in (_i(c.get("gameweek")) for c in claims)
               if g is not None]
    if stamped:
        ranked = sorted({g: stamped.count(g) for g in stamped}.items(),
                        key=lambda kv: (-kv[1], kv[0]))
        top, n = ranked[0]
        return top, (
            f"no deadline is on file after this episode's publication date, so "
            f"this falls back to GW{top}, which {n} of its {len(stamped)} "
            f"stored claims carry. That is the corpus talking, not the "
            f"calendar.")
    return None, (
        "no gameweek could be determined: the link ledger holds none for this "
        "item, dim_event has no deadline after its publication date, and no "
        "claim was extracted from it.")


def _episode_row(family: list[dict[str, Any]], *, state: str,
                 chars: int | None, analysis: dict[str, Any] | None,
                 claim_count: int, gw: int | None, gw_reason: str
                 ) -> dict[str, Any]:
    """One publication, as both panels serve it."""
    rep = _representative(family)
    return {
        "item_id": str(rep["item_id"]),
        "title": str(rep["title"]),
        "published_at": _iso(rep["published_at"]),
        "source_url": _source_url(_s(rep["url"])),
        "source_kind": str(rep["kind"]),
        "transcription_state": state,
        "transcript_chars": chars,
        "analysis_present": analysis is not None,
        "analysis_model": None if analysis is None else analysis["model"],
        "claim_count": int(claim_count),
        "gameweek": gw,
        "gw_reason": gw_reason,
        "siblings": [],
        "siblings_basis": SIBLINGS_BASIS,
    }


def _corpus(wh, creator: str, moment: dt.datetime, present: set[str]
            ) -> dict[str, Any]:
    """Everything both panels need about one creator's publications.

    One pass: the items, the groups, and the six side reads keyed on the item
    ids. Built once so ``creator_episodes`` and ``episode_summary`` cannot
    drift into reporting different states for the same episode.
    """
    items = _items(wh, moment, creator=creator)
    hidden = discarded_item_ids(wh)
    if hidden and not items.empty:
        items = items[~items["item_id"].astype(str).isin(hidden)]
    families = _families(items)
    ids = tuple(sorted(str(r["item_id"]) for fam in families for r in fam))

    segments = (_ids_in(wh, "transcript_segment", ids)
                if "transcript_segment" in present else set())
    provenance = (_ids_in(wh, "transcript_provenance", ids)
                  if "transcript_provenance" in present else set())
    skips = (_ids_in(wh, "content_transcribe_skip", ids)
             if "content_transcribe_skip" in present else set())
    enclosures = (
        _ids_in(wh, "content_item_asset", ids, where="enclosure_url IS NOT NULL")
        if "content_item_asset" in present else set())
    seg_chars = (_segment_chars(wh, ids) if "transcript_segment" in present
                 else {})
    analyses = _analysis_index(wh, ids) if "content_analysis" in present else {}
    ledger = _ledger_gw(wh, ids) if USER_LINK_TABLE in present else {}

    claims = _content_store(wh).claims_visible_at(moment)
    if not claims.empty:
        claims = claims[claims["creator"] == creator]
    by_item: dict[str, list[dict[str, Any]]] = {}
    for r in (claims.to_dict("records") if not claims.empty else []):
        by_item.setdefault(str(r["item_id"]), []).append(r)

    calendar = _gw_calendar(wh, moment)
    text_chars = _text_chars(wh, ids)

    rows: list[dict[str, Any]] = []
    for family in families:
        fam_ids = sorted(str(r["item_id"]) for r in family)
        analysis = next((analyses[i] for i in fam_ids if i in analyses), None)
        collapsed = {_claim_key(c): c
                     for i in fam_ids for c in by_item.get(i, [])}
        gw, gw_reason = _episode_gw(
            family, ledger=ledger, claims=list(collapsed.values()),
            calendar=calendar)
        rows.append(_episode_row(
            family,
            state=_transcription_state(family, segments=segments,
                                       provenance=provenance, skips=skips,
                                       enclosures=enclosures),
            chars=_transcript_chars(family, seg_chars=seg_chars,
                                    text_chars=text_chars),
            analysis=analysis,
            claim_count=len(collapsed),
            gw=gw,
            gw_reason=gw_reason,
        ))
    linked = _siblings(rows)
    for row in rows:
        row["siblings"] = linked[str(row["item_id"])]
    return {"families": families, "rows": rows, "claims": by_item}


def _identity(wh, creator: str, present: set[str], moment: dt.datetime
              ) -> dict[str, Any]:
    """The creator's identity fields, exactly as ``creator_detail`` serves them."""
    entries, entry_reason = _entries(wh, present, moment)
    entry = entries.get(creator)
    weights = _weights_as_of(wh, moment) if "creator_score" in present else {}
    return {"entry": entry,
            "entry_reason": None if entry else entry_reason,
            "record": _record(weights.get(creator))}


def _known_creators(wh) -> list[str]:
    """Every creator name the corpus holds, registered or materialised."""
    known = q(wh, "SELECT DISTINCT creator FROM content_source "
                  "UNION SELECT DISTINCT creator FROM content_item")
    return sorted(str(n) for n in known["creator"]) if not known.empty else []


def _missing_corpus(present: set[str]) -> str | None:
    """The reason string when the content tables are not in this warehouse."""
    missing = [t for t in _CONTENT_TABLES if t not in present]
    if not missing:
        return None
    return (f"the creator corpus is not in this warehouse "
            f"({', '.join(missing)} missing). Run "
            f"`python -m fpl_edge.ingest.content.pipeline ingest`.")


# ---------------------------------------------------------------------------
# creator_episodes

def creator_episodes(wh, *, creator: str, limit: int = 30,
                     include_untranscribed: bool = False) -> dict[str, Any]:
    """Every episode this creator has published, newest first, with its state.

    One row per publication, carrying the link, the title, whether it has been
    transcribed and analysed, how many claims came out of it, and which
    gameweek it is about. ``counts`` is computed over the whole corpus before
    the transcribed filter, so turning the filter on never changes the total a
    reader is comparing against.
    """
    moment = dt.datetime.now(UTC)
    present = _tables_present(wh, _CONTENT_TABLES + _SIDE_TABLES)
    gone = _missing_corpus(present)
    if gone:
        return empty(gone)

    names = _known_creators(wh)
    if creator not in names:
        return empty(
            f"no creator named {creator!r} is tracked. On file: "
            + (", ".join(names) if names else "(none)") + "."
        )

    corpus = _corpus(wh, creator, moment, present)
    rows = corpus["rows"]
    counts = {
        "episodes_total": len(rows),
        "transcribed": sum(1 for r in rows
                           if r["transcription_state"] == "transcribed"),
        "analysed": sum(1 for r in rows if r["analysis_present"]),
    }
    shown = rows if include_untranscribed else [
        r for r in rows if r["transcription_state"] == "transcribed"]

    return {
        "creator": creator,
        "as_of": moment.isoformat(),
        **_identity(wh, creator, present, moment),
        "include_untranscribed": bool(include_untranscribed),
        "limit": int(limit),
        "counts": counts,
        "episodes": shown[: int(limit)],
    }


# ---------------------------------------------------------------------------
# episode_summary

def _claim_quote(claim: dict[str, Any]) -> str | None:
    """The verbatim evidence stored on a claim row.

    For an ``llm:`` claim the extractor stored the fragment after ``| quote:``;
    for a ``cue`` claim the rationale IS the keyword window lifted from the
    item, so it is the quote. Untrusted third-party prose either way.
    """
    rationale = _s(claim.get("rationale")) or ""
    quote = (rationale.split("| quote: ", 1)[1] if "| quote: " in rationale
             else rationale)
    return quote or None


def _evidence(*, kind: str, stored_in: str, direction: str,
              confidence: float | None, conviction: str | None,
              quote: str | None, gameweek: int | None,
              index: TranscriptIndex, url: str | None) -> dict[str, Any]:
    """One thing said about one player, with wherever it was said from.

    ``start_s`` is the offset of the transcript segment the quote begins in,
    found by normalised substring search, and null when the quote cannot be
    located or no transcript is stored. A null offset gets the episode link
    unchanged, never a guessed timestamp.
    """
    start_s = index.find(quote)
    return {
        "kind": kind,
        "stored_in": stored_in,
        "direction": direction,
        "confidence": confidence,
        "conviction": conviction,
        "quote": quote,
        "start_s": _f(start_s, 2),
        "deep_link": deep_link(url, start_s),
        "gameweek": gameweek,
    }


def _players(claims: list[dict[str, Any]], analysis: dict[str, Any] | None, *,
             resolver, index: TranscriptIndex, url: str | None,
             display: dict[str, Any], facts: dict[int, dict[str, Any]]
             ) -> list[dict[str, Any]]:
    """Every player this episode said something about, and what was said.

    Both stored channels, kept distinguishable. A ``claim`` is a row in
    ``content_claim``, which carries a numeric confidence and the extractor
    that produced it. A ``call`` is a ``PlayerCall`` inside the stored
    ``content_analysis`` JSON, which carries the speaker's conviction band as a
    word and no number. Neither is converted into the other: a band is not a
    probability and a cue window is not a semantic read.
    """
    people: dict[Any, dict[str, Any]] = {}

    def bucket(code: int | None, raw: str) -> dict[str, Any]:
        key = code if code is not None else ("name", raw.casefold())
        found = people.get(key)
        if found is None:
            info = facts.get(code) if code is not None else None
            found = {
                "code": code,
                "name": raw or "(unnamed)",
                **_naming(code, raw, display),
                "team": (info or {}).get("team"),
                "position": (info or {}).get("position"),
                "claims": [],
            }
            people[key] = found
        return found

    for c in claims:
        code = _i(c.get("player_code"))
        raw = str(c.get("player_name") or c.get("surface_form") or "")
        bucket(code, raw)["claims"].append(_evidence(
            kind="claim",
            stored_in=str(c.get("extractor") or "cue"),
            direction=str(c.get("action")),
            confidence=_f(c.get("confidence"), 3),
            conviction=None,
            quote=_claim_quote(c),
            gameweek=_i(c.get("gameweek")),
            index=index, url=url,
        ))

    for label, source in _CALL_LISTS:
        for call in ((analysis or {}).get(source) or []):
            if not isinstance(call, dict):
                continue
            raw = str(call.get("player") or "").strip()
            code = _resolve_code(resolver, raw)
            bucket(code, raw)["claims"].append(_evidence(
                kind="call",
                stored_in=label,
                direction=str(call.get("stance") or "unknown"),
                confidence=None,
                conviction=_s(call.get("conviction")),
                quote=_s(call.get("quote")),
                gameweek=_i(call.get("gameweek")),
                index=index, url=url,
            ))

    return sorted(people.values(),
                  key=lambda p: (-len(p["claims"]), str(p["display_name"])))


def _transfers(analysis: dict[str, Any] | None, *, resolver,
               index: TranscriptIndex, url: str | None,
               display: dict[str, Any]) -> list[dict[str, Any]]:
    """The transfers this episode suggested, in and out, as stored.

    Singles, because singles are what is stored. ``TranscriptAnalysis`` holds
    ``transfers_in`` and ``transfers_out`` as two independent lists and nothing
    records that one funded the other, so pairing them here would be this
    panel's inference presented as the creator's plan. ``gaps`` says so.
    """
    out: list[dict[str, Any]] = []
    for label, source in (("in", "transfers_in"), ("out", "transfers_out")):
        for call in ((analysis or {}).get(source) or []):
            if not isinstance(call, dict):
                continue
            raw = str(call.get("player") or "").strip()
            code = _resolve_code(resolver, raw)
            quote = _s(call.get("quote"))
            start_s = index.find(quote)
            out.append({
                "direction": label,
                "code": code,
                "name": raw or "(unnamed)",
                **_naming(code, raw, display),
                "stance": str(call.get("stance") or "unknown"),
                "conviction": _s(call.get("conviction")),
                "quote": quote,
                "start_s": _f(start_s, 2),
                "deep_link": deep_link(url, start_s),
                "gameweek": _i(call.get("gameweek")),
                "paired_with": None,
            })
    return out


def _captains(analysis: dict[str, Any] | None, *, resolver,
              index: TranscriptIndex, url: str | None,
              display: dict[str, Any]) -> list[dict[str, Any]]:
    """The captaincy calls, as stored, each with its quote and its offset."""
    out: list[dict[str, Any]] = []
    for call in ((analysis or {}).get("captaincy") or []):
        if not isinstance(call, dict):
            continue
        raw = str(call.get("player") or "").strip()
        code = _resolve_code(resolver, raw)
        quote = _s(call.get("quote"))
        start_s = index.find(quote)
        out.append({
            "code": code,
            "name": raw or "(unnamed)",
            **_naming(code, raw, display),
            "stance": str(call.get("stance") or "unknown"),
            "conviction": _s(call.get("conviction")),
            "quote": quote,
            "start_s": _f(start_s, 2),
            "deep_link": deep_link(url, start_s),
            "gameweek": _i(call.get("gameweek")),
        })
    return out


def _gaps(*, analysis: dict[str, Any] | None, state: str,
          summary: str | None, players: list[dict[str, Any]],
          transfers: list[dict[str, Any]], captains: list[dict[str, Any]],
          has_transcript: bool) -> list[dict[str, Any]]:
    """Which sections are empty, and why, in the dossier's gap style.

    A gap is a named section with a sentence a reader can act on. An empty list
    with no gap beside it reads as "they said nothing", which is a claim about
    the episode; a gap says which of "nobody said it" and "nobody has read it
    yet" is true.
    """
    gaps: list[dict[str, Any]] = []
    if analysis is None:
        gaps.append({"section": "analysis", "gap": (
            f"no row in content_analysis for this episode. Its transcription "
            f"state is {state}"
            + (", so the text is on file and the analysis queue has not "
               "reached it." if has_transcript else
               ", so there is no speech on file to analyse."))})
        return gaps
    if not summary:
        gaps.append({"section": "summary", "gap": (
            "the stored analysis carries an empty summary list, which the "
            "extractor writes when it found no key ideas to record.")})
    if not players:
        gaps.append({"section": "players", "gap": (
            "no claim row and no player call is stored for this episode, so "
            "nothing was said about a named player that could be recorded.")})
    if not transfers:
        gaps.append({"section": "transfers_suggested", "gap": (
            "the stored analysis holds no transfer in and no transfer out for "
            "this episode.")})
    else:
        gaps.append({"section": "transfers_suggested.paired_with", "gap": (
            "transfers are stored as two independent lists, so which sale "
            "funded which purchase is not recorded and is not inferred here.")})
    if not captains:
        gaps.append({"section": "captain_view", "gap": (
            "the stored analysis holds no captaincy call for this episode.")})
    if not has_transcript:
        gaps.append({"section": "timestamps", "gap": (
            "no transcript segment is stored for this episode, so no quote can "
            "be located in time and every start_s is null.")})
    return gaps


def episode_summary(wh, *, item_id: str) -> dict[str, Any]:
    """One episode opened: the header, the summary, and who was talked about.

    The header is the row ``creator_episodes`` serves for this publication, so
    the two panels cannot disagree about its state. Below it is the stored
    analysis, section by section, with the quote and the offset each line came
    from where those were stored and null where they were not. Nothing is
    lifted out of the transcript to fill an empty section; ``gaps`` names each
    one instead.
    """
    moment = dt.datetime.now(UTC)
    present = _tables_present(wh, _CONTENT_TABLES + _SIDE_TABLES)
    gone = _missing_corpus(present)
    if gone:
        return empty(gone)

    found = q(
        wh,
        "SELECT creator, published_at FROM content_item WHERE item_id = $1",
        (str(item_id),),
    )
    if found.empty:
        return empty(
            f"no content_item {item_id!r} is in this warehouse, so there is no "
            f"episode to open."
        )
    if str(item_id) in discarded_item_ids(wh):
        return empty(
            f"item {item_id!r} has been discarded from the corpus. Nothing was "
            f"deleted: restore it through the link ledger to read it again."
        )
    creator = str(found.iloc[0]["creator"])

    # `_corpus` builds one row per family, in order, so the two lists are
    # indexed together rather than matched by id: the episode header and the
    # list row are then the same object by construction.
    corpus = _corpus(wh, creator, moment, present)
    at = next((i for i, fam in enumerate(corpus["families"])
               if any(str(r["item_id"]) == str(item_id) for r in fam)), None)
    if at is None:
        return empty(
            f"item {item_id!r} was published at "
            f"{_iso(found.iloc[0]['published_at'])}, which is not before this "
            f"instant, so it is not visible to a reader of now."
        )
    family, header = corpus["families"][at], corpus["rows"][at]

    fam_ids = sorted(str(r["item_id"]) for r in family)
    transcripts = (_transcripts(wh, set(fam_ids))
                   if "transcript_segment" in present else {})
    index = next((transcripts[i] for i in fam_ids if i in transcripts),
                 _NO_TRANSCRIPT)
    parsed = _analyses(wh, set(fam_ids)) if "content_analysis" in present else {}
    pair = next((parsed[i] for i in fam_ids if i in parsed), None)
    analysis = pair[0] if pair else None
    receipts = (_analysis_index(wh, tuple(fam_ids))
                if "content_analysis" in present else {})
    receipt = next((receipts[i] for i in fam_ids if i in receipts), None)

    url = _s(_representative(family)["url"])
    resolver = _resolver(wh, SEASON_DEFAULT, moment)
    display = _display_index(wh, moment)
    facts = _player_facts(wh, moment)

    collapsed = {_claim_key(c): c
                 for i in fam_ids for c in corpus["claims"].get(i, [])}
    players = _players(list(collapsed.values()), analysis, resolver=resolver,
                       index=index, url=url, display=display, facts=facts)
    transfers = _transfers(analysis, resolver=resolver, index=index, url=url,
                           display=display)
    captains = _captains(analysis, resolver=resolver, index=index, url=url,
                         display=display)

    # content_analysis DOES carry a summary field: TranscriptAnalysis.summary
    # is a list of 3 to 6 bullets. It is served as the stored list plus the
    # newline joined string the contract asks for, so `summary_reason` is
    # populated only when the stored list is empty or absent.
    bullets = [str(b) for b in ((analysis or {}).get("summary") or [])
               if str(b).strip()]
    summary = "\n".join(bullets) if bullets else None
    if analysis is None:
        summary_reason = ("no analysis is stored for this episode, so there is "
                          "no summary. The claims below are what the corpus "
                          "holds about it.")
    elif not bullets:
        summary_reason = ("the stored analysis carries an empty summary list. "
                          "The claims below are the record of what was said.")
    else:
        summary_reason = None

    return {
        "creator": creator,
        "as_of": moment.isoformat(),
        "episode": header,
        "summary": summary,
        "summary_bullets": bullets,
        "summary_reason": summary_reason,
        "players": players,
        "transfers_suggested": transfers,
        "captain_view": captains,
        "gameweek": header["gameweek"],
        "gw_reason": header["gw_reason"],
        "analysis_model": header["analysis_model"],
        "analysed_at": None if receipt is None else receipt["created_utc"],
        "gaps": _gaps(analysis=analysis, state=header["transcription_state"],
                      summary=summary, players=players, transfers=transfers,
                      captains=captains, has_transcript=bool(index)),
    }


# ---------------------------------------------------------------------------
# Schemas. Every field is described in one sentence, and every object refuses
# a property this module does not write.

_EPISODE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id", "title", "published_at", "source_url", "source_kind",
                 "transcription_state", "transcript_chars", "analysis_present",
                 "analysis_model", "claim_count", "gameweek", "gw_reason"],
    "properties": {
        "item_id": {"type": "string", "description":
                    "The content_item id of the row that represents this "
                    "publication, which is the one carrying the richest text."},
        "title": {"type": "string", "description":
                  "The episode title as the creator published it, verbatim."},
        "published_at": {"type": ["string", "null"], "description":
                         "When the creator made this public, ISO-8601 UTC."},
        "source_url": {"type": ["string", "null"], "description":
                       "The canonical link to the episode, or null when no URL "
                       "is stored for it."},
        "source_kind": {"type": "string", "description":
                        "How it was published, as content_item.kind stores it: "
                        "youtube, podcast, blog or link."},
        "transcription_state": {
            "enum": list(TRANSCRIPTION_STATES),
            "description":
                "Whether a transcript is on file for this episode: transcribed "
                "when segments, a provenance receipt or transcript text are "
                "stored, failed when content_transcribe_skip holds a row, "
                "queued when the audio URL is stored and neither of those is, "
                "none otherwise.",
        },
        "transcript_chars": {"type": ["integer", "null"], "description":
                             "How many characters of transcript are stored, or "
                             "null when none are."},
        "analysis_present": {"type": "boolean", "description":
                             "Whether content_analysis holds a row for this "
                             "publication."},
        "analysis_model": {"type": ["string", "null"], "description":
                           "The model stamped on the stored analysis, or null "
                           "when there is no analysis."},
        "claim_count": {"type": "integer", "description":
                        "How many distinct stored positions this episode "
                        "produced, counted after the duplicate rows of one "
                        "publication are collapsed."},
        "gameweek": {"type": ["integer", "null"], "description":
                     "The gameweek this episode is about, or null when nothing "
                     "stored says which one."},
        "gw_reason": {"type": "string", "description":
                      "Which stored value the gameweek came from, or why there "
                      "is none. Always a sentence, never null."},
        "siblings": {"type": "array", "items": {"type": "string"},
                     "description":
                     "The item ids of this creator's other publications that "
                     "are the same recording, empty when there are none. The "
                     "rows are not merged; siblings_basis says how this was "
                     "decided."},
        "siblings_basis": {"type": "string", "description":
                           "The rule siblings was computed with, in one "
                           "sentence, and what it measures over the corpus."},
    },
}

_COUNTS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["episodes_total", "transcribed", "analysed"],
    "properties": {
        "episodes_total": {"type": "integer", "description":
                           "How many publications this creator has in the "
                           "corpus, before the transcribed filter."},
        "transcribed": {"type": "integer", "description":
                        "How many of those have a transcript on file."},
        "analysed": {"type": "integer", "description":
                     "How many of those have a stored analysis."},
    },
}

EPISODES_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator"],
    "properties": {
        "creator": {"type": "string", "minLength": 1, "description":
                    "The creator name as the corpus files them, the same "
                    "identifier creator_detail takes."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 30,
                  "description":
                  "How many episodes to return, applied after the transcribed "
                  "filter and counted in publications."},
        "include_untranscribed": {
            "type": "boolean", "default": False, "description":
            "Whether to list episodes with no transcript on file as well as "
            "the transcribed ones.",
        },
    },
}

EPISODES_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "as_of", "entry", "entry_reason", "record",
                 "include_untranscribed", "limit", "counts", "episodes"],
    "properties": {
        "creator": {"type": "string", "description":
                    "The creator this list is about."},
        "as_of": {"type": "string", "description":
                  "The instant this panel read the warehouse, ISO-8601 UTC."},
        "entry": _ENTRY,
        "entry_reason": {"type": ["string", "null"], "description":
                         "Why no FPL entry is attached to this creator, or "
                         "null when one is."},
        "record": _RECORD,
        "include_untranscribed": {"type": "boolean", "description":
                                  "The filter this run applied, echoed so the "
                                  "page can say which list it is showing."},
        "limit": {"type": "integer", "description":
                  "The row cap this run applied."},
        "counts": _COUNTS,
        "episodes": {"type": "array", "items": _EPISODE, "description":
                     "The creator's publications, newest first by published "
                     "date."},
    },
}

_EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "stored_in", "direction", "confidence", "conviction",
                 "quote", "start_s", "deep_link", "gameweek"],
    "properties": {
        "kind": {"enum": ["claim", "call"], "description":
                 "Which stored channel this came from: a content_claim row, or "
                 "a player call inside the stored analysis."},
        "stored_in": {"type": "string", "description":
                      "Where it sits in that channel: the claim extractor for "
                      "a claim, the analysis list name for a call."},
        "direction": {"type": "string", "description":
                      "The position taken, as stored: the claim action or the "
                      "call stance."},
        "confidence": {"type": ["number", "null"], "description":
                       "The numeric confidence stored on a claim row, null for "
                       "a call, which stores a word instead."},
        "conviction": {"type": ["string", "null"], "description":
                       "The conviction band stored on a call, null for a "
                       "claim, which stores a number instead."},
        "quote": {"type": ["string", "null"], "description":
                  "The verbatim words stored as evidence, or null when none "
                  "were stored."},
        "start_s": {"type": ["number", "null"], "description":
                    "Seconds into the episode where the quote was located, or "
                    "null when it could not be located."},
        "deep_link": {"type": ["string", "null"], "description":
                      "A link to that moment when the platform has a grammar "
                      "for one, otherwise the episode link."},
        "gameweek": {"type": ["integer", "null"], "description":
                     "The gameweek this position targets, as stored."},
    },
}

_NAMED = {
    "display_name": {"type": "string", "description":
                     "The canonical player name when the code resolved, the "
                     "spoken string otherwise."},
    "resolved": {"type": "boolean", "description":
                 "Whether the spoken name resolved to exactly one player."},
    "disambiguator": {"type": ["string", "null"], "description":
                      "A qualified name when two players share this one, null "
                      "otherwise."},
}

_PLAYER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "team", "position", "claims"],
    "properties": {
        "code": {"type": ["integer", "null"], "description":
                 "The stable player code, or null when the spoken name did not "
                 "resolve to exactly one player."},
        "name": {"type": "string", "description":
                 "The player name as the creator said it, verbatim."},
        **_NAMED,
        "team": {"type": ["string", "null"], "description":
                 "The player's club, or null when the code did not resolve."},
        "position": {"type": ["string", "null"], "description":
                     "The player's position, or null when the code did not "
                     "resolve."},
        "claims": {"type": "array", "items": _EVIDENCE, "description":
                   "Everything this episode said about this player, from both "
                   "stored channels."},
    },
}

_TRANSFER_CALL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["direction", "code", "name", "display_name", "resolved",
                 "disambiguator", "stance", "conviction", "quote", "start_s",
                 "deep_link", "gameweek", "paired_with"],
    "properties": {
        "direction": {"enum": ["in", "out"], "description":
                      "Which stored list this call came from, transfers in or "
                      "transfers out."},
        "code": {"type": ["integer", "null"], "description":
                 "The stable player code, or null when the name did not "
                 "resolve."},
        "name": {"type": "string", "description":
                 "The player name as the creator said it, verbatim."},
        **_NAMED,
        "stance": {"type": "string", "description":
                   "The stance stored on the call, which is not always a "
                   "transfer: a watch stored under transfers in stays a watch."},
        "conviction": {"type": ["string", "null"], "description":
                       "The conviction band the speaker's language carried, as "
                       "stored."},
        "quote": {"type": ["string", "null"], "description":
                  "The verbatim quote stored with the call, or null."},
        "start_s": {"type": ["number", "null"], "description":
                    "Seconds into the episode where the quote was located, or "
                    "null."},
        "deep_link": {"type": ["string", "null"], "description":
                      "A link to that moment, or the episode link when no "
                      "offset was found."},
        "gameweek": {"type": ["integer", "null"], "description":
                     "The gameweek the call targets, as stored."},
        "paired_with": {"type": "null", "description":
                        "Always null: nothing stored records which sale funded "
                        "which purchase, and this panel does not infer it."},
    },
}

_CAPTAIN_CALL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "stance", "conviction", "quote", "start_s", "deep_link",
                 "gameweek"],
    "properties": {
        "code": {"type": ["integer", "null"], "description":
                 "The stable player code, or null when the name did not "
                 "resolve."},
        "name": {"type": "string", "description":
                 "The player name as the creator said it, verbatim."},
        **_NAMED,
        "stance": {"type": "string", "description":
                   "The stance stored on the captaincy call."},
        "conviction": {"type": ["string", "null"], "description":
                       "The conviction band, as stored."},
        "quote": {"type": ["string", "null"], "description":
                  "The verbatim quote stored with the call, or null."},
        "start_s": {"type": ["number", "null"], "description":
                    "Seconds into the episode where the quote was located, or "
                    "null."},
        "deep_link": {"type": ["string", "null"], "description":
                      "A link to that moment, or the episode link."},
        "gameweek": {"type": ["integer", "null"], "description":
                     "The gameweek the call targets, as stored."},
    },
}

_GAP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["section", "gap"],
    "properties": {
        "section": {"type": "string", "description":
                    "Which section of this payload is empty."},
        "gap": {"type": "string", "description":
                "Why it is empty, in terms of what is and is not stored."},
    },
}

SUMMARY_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id"],
    "properties": {
        "item_id": {"type": "string", "minLength": 1, "description":
                    "The content_item id of any stored row of the episode to "
                    "open."},
    },
}

SUMMARY_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "as_of", "episode", "summary", "summary_reason",
                 "players", "transfers_suggested", "captain_view", "gameweek",
                 "gw_reason", "analysis_model", "analysed_at", "gaps"],
    "properties": {
        "creator": {"type": "string", "description":
                    "The creator this episode is filed under."},
        "as_of": {"type": "string", "description":
                  "The instant this panel read the warehouse, ISO-8601 UTC."},
        "episode": _EPISODE,
        "summary": {"type": ["string", "null"], "description":
                    "The stored summary bullets joined by newlines, or null "
                    "when none are stored."},
        "summary_bullets": {"type": "array", "items": {"type": "string"},
                            "description":
                            "The stored summary as the list it is written as."},
        "summary_reason": {"type": ["string", "null"], "description":
                           "Why there is no summary, or null when there is "
                           "one."},
        "players": {"type": "array", "items": _PLAYER, "description":
                    "Every player this episode said something about, most "
                    "talked about first."},
        "transfers_suggested": {"type": "array", "items": _TRANSFER_CALL,
                                "description":
                                "The transfer calls stored for this episode, "
                                "in and out, as singles."},
        "captain_view": {"type": "array", "items": _CAPTAIN_CALL,
                         "description":
                         "The captaincy calls stored for this episode."},
        "gameweek": {"type": ["integer", "null"], "description":
                     "The gameweek this episode is about, repeated from the "
                     "header."},
        "gw_reason": {"type": "string", "description":
                      "Where that gameweek came from, repeated from the "
                      "header."},
        "analysis_model": {"type": ["string", "null"], "description":
                           "The model that wrote the stored analysis, or null "
                           "when there is none."},
        "analysed_at": {"type": ["string", "null"], "description":
                        "When the stored analysis was written, or null when "
                        "there is none."},
        "gaps": {"type": "array", "items": _GAP, "description":
                 "Each empty section of this payload, with the reason it is "
                 "empty."},
    },
}

register_script(
    name="creator_episodes",
    fn=creator_episodes,
    params_schema=EPISODES_PARAMS,
    result_schema=EPISODES_RESULT,
    title="Creator episodes",
    description="Every episode a creator has published, newest first, with "
                "its link, whether it has been transcribed and analysed, how "
                "many claims came out of it and which gameweek it is about.",
)

register_script(
    name="episode_summary",
    fn=episode_summary,
    params_schema=SUMMARY_PARAMS,
    result_schema=SUMMARY_RESULT,
    title="Episode summary",
    description="One episode opened: the stored summary, every player talked "
                "about with the quote and timestamp each position came from, "
                "the transfer and captaincy calls, and the gaps.",
)
