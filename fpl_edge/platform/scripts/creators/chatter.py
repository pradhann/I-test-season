"""`player_chatter`: the panel on one player, from what creators said."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fpl_edge.ingest.content.urls import canonical_key, deep_link
from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import SEASON_DEFAULT, empty, q
from fpl_edge.platform.scripts.creators.identity import (
    _CALL_BUCKETS,
    _CONTENT_TABLES,
    _NO_TRANSCRIPT,
    _PANEL_SHOW_TABLE,
    _PANEL_TABLE,
    _TEXT_RANK,
    UTC,
    _analyses,
    _content_store,
    _display_index,
    _f,
    _gw_after,
    _gw_calendar,
    _i,
    _iso,
    _naming,
    _norm,
    _panel_roster,
    _panel_squads,
    _resolve_code,
    _resolve_gw,
    _resolver,
    _s,
    _stamps,
    _tables_present,
    _transcripts,
)

# ---------------------------------------------------------------------------
# player_chatter -- one player, three channels, no consensus.
#
# Mounted in the xPoints and Template drawers, so it answers a question asked
# while the reader is scanning a matrix: "does anybody have anything on this
# guy?" Four times in five the honest answer is no -- claims cover 119 of the
# 614 players in the pool -- so emptiness is the MODAL case here and is built
# for accordingly: three separately-explained sections, never one blank card.
#
# The ordering DID -> SAID -> NOTICED is editorial and it is the point:
#
#   owned    what panel members ACTUALLY hold. A pick with a deadline on it.
#            No epistemic problem at all, so it leads.
#   said     what they said. The aggregate creator record is 34.6% -- below
#            chance -- so this is intent, not forecast, and it is ordered
#            second for that reason.
#   noticed  intel_item: MEASURED signals (out-of-position, set-piece,
#            availability, press conference), never merged with `said`. One is
#            spoken, the other is computed, and a UI that stacks them together
#            launders the second into the authority of the first.
#
# There is deliberately NO `net`, no agreement count, no consensus score. Every
# earned creator weight in the warehouse is 0.0 across all 330 rows; collapsing
# below-chance opinions into a single number manufactures exactly the authority
# this panel exists to refuse. `counts` reports volume, which is an honest
# ordering; nothing here reports agreement.

#: Analysis stances that are OBSERVATIONS rather than positions. `watch` is
#: unmapped in `analyze._STANCE_TO_ACTION` precisely because it is not a
#: scoreable call, so it never becomes a `content_claim` row -- which is why
#: watch calls are read straight out of `content_analysis` below and stamped
#: `is_observation`. Rendering one as a buy puts a recommendation in a named
#: person's mouth that they did not make.
_OBSERVATION_STANCES = {"watch"}

#: content_claim.confidence is written from a conviction BAND, so the band is
#: recoverable exactly. Applied only to `llm:` claims: the cue extractor writes
#: a continuous score from keyword-window arithmetic (0.13 .. 0.71 in the live
#: table), and mapping that onto "high/medium/low" would invent a spoken
#: certainty out of a substring count.
_CONF_TO_CONVICTION = {0.8: "high", 0.6: "medium", 0.4: "low"}

_SAID = {
    "type": "object",
    "additionalProperties": False,
    # `gameweek` is REQUIRED. A statement with no gameweek on it cannot be
    # labelled, grouped or filtered, and a strip that mixes GW2 and GW3 talk
    # with nothing distinguishing them is the failure this whole panel was
    # rebuilt to fix.
    "required": ["person", "person_basis", "show", "action", "is_observation",
                 "conviction", "extractor", "quote", "start_s", "deep_link",
                 "published_at", "item_title", "item_url", "url_basis",
                 "gameweek", "gameweek_basis"],
    "properties": {
        # null when only the SHOW is known. The FPL Wire has four hosts with
        # four different teams, so "the Wire said" is not a person saying it.
        "person": {"type": ["string", "null"]},
        "person_basis": {"type": ["string", "null"]},
        "show": {"type": "string"},
        "action": {"type": "string"},
        # true for `watch`. Exists so a UI cannot render an observation as a
        # recommendation by omission.
        "is_observation": {"type": "boolean"},
        "conviction": {"type": ["string", "null"]},
        # "cue" or "llm:<model>". A keyword window is not a considered take and
        # the two must stay visually distinguishable.
        "extractor": {"type": "string"},
        "quote": {"type": ["string", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
        "published_at": {"type": ["string", "null"]},
        "item_title": {"type": ["string", "null"]},
        "item_url": {"type": ["string", "null"]},
        # link | atom_alternate | guid_permalink | enclosure | null.
        # `enclosure` means the "link" IS an mp3: play audio, do not open a page.
        "url_basis": {"type": ["string", "null"]},
        "item_id": {"type": "string"},
        # The gameweek this statement was ABOUT, which is not when it was made:
        # `published_at` is the second axis and they diverge by weeks.
        "gameweek": {"type": ["integer", "null"]},
        # "stated" -- the speaker named the gameweek and the extractor stored
        # it. "inferred" -- nobody named one, so it is the first gameweek whose
        # deadline fell after publication (`content_claim.gw_inferred` for a
        # claim; `_gw_after` for a watch call, which is the same rule the
        # ingester uses). null -- neither was possible. A UI may want to mark
        # an inferred gameweek; it must never present one as spoken.
        "gameweek_basis": {"type": ["string", "null"],
                           "enum": ["stated", "inferred", None]},
        "confidence": {"type": ["number", "null"]},
        # Is this show on the owner's curated panel, or merely in the corpus?
        "on_panel": {"type": "boolean"},
    },
}

_OWNED = {
    "type": "object",
    "additionalProperties": False,
    "required": ["person", "entry_id", "multiplier", "role", "gw", "as_of"],
    "properties": {
        "person": {"type": "string"},
        "entry_id": {"type": "integer"},
        "multiplier": {"type": ["integer", "null"]},
        "role": {"type": ["string", "null"]},
        "gw": {"type": "integer"},
        # The deadline the squad locked at. A pick is a fact about THAT
        # instant, not about now.
        "as_of": {"type": ["string", "null"]},
    },
}

_NOTICED = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "headline", "body", "source", "source_url",
                 "published_at", "confidence"],
    "properties": {
        "kind": {"type": "string"},
        "headline": {"type": "string"},
        "body": {"type": ["string", "null"]},
        "source": {"type": "string"},
        "source_url": {"type": ["string", "null"]},
        "published_at": {"type": ["string", "null"]},
        "confidence": {"type": ["number", "null"]},
    },
}

CHATTER_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code"],
    "properties": {
        # The stable cross-season PlayerCode, never element_id -- the same key
        # xpoints.js and template.js already index their rows on.
        "code": {"type": "integer", "minimum": 1},
        # Bounds `said` ONLY when `gw` is "all". `noticed` was never windowed
        # (a set-piece finding is a standing fact) and `owned` is a deadline
        # fact, so with a gameweek in force this parameter bounds nothing.
        "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
        # Which gameweek `said` is about. Null defaults to the next gameweek,
        # exactly as `creator_board.gw` does and through the same resolver.
        # "all" is the escape hatch: no gameweek filter, `days` bounds instead.
        "gw": {"anyOf": [
            {"type": "integer", "minimum": 1, "maximum": 38},
            {"const": "all"},
            {"type": "null"},
        ], "default": None},
    },
}

CHATTER_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    # Disjoint from the registry's {empty, reason} branch by construction:
    # these keys are required and `additionalProperties` is false, so an honest
    # empty can never also validate as a real payload.
    "required": ["code", "name", "disambiguator", "as_of", "gw", "gw_reason",
                 "owned", "owned_reason", "said", "said_by_gw", "noticed",
                 "counts"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        # Non-null exactly when this web_name belongs to 2+ players in the
        # pool ("C. Palmer (CHE)") -- the drawer heading must show it then.
        "disambiguator": {"type": ["string", "null"]},
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        # The gameweek `said` is filtered to; null when no filter is in force.
        "gw": {"type": ["integer", "null"]},
        # Always a string, never null -- same guarantee as creator_board's.
        "gw_reason": {"type": "string"},
        "owned": {"type": "array", "items": _OWNED},
        "owned_reason": {"type": "string"},
        "said": {"type": "array", "items": _SAID},
        "said_reason": {"type": ["string", "null"]},
        # The census `gw` filters. Counted over everything visible at `as_of`,
        # before the gameweek filter and before any day window, so an empty
        # `said` can always be told apart from an empty corpus.
        "said_by_gw": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["gw", "n"],
                "properties": {"gw": {"type": ["integer", "null"]},
                               "n": {"type": "integer"}},
            },
        },
        "noticed": {"type": "array", "items": _NOTICED},
        "noticed_reason": {"type": ["string", "null"]},
        "counts": {
            "type": "object",
            "additionalProperties": False,
            "required": ["said", "observations", "owned", "noticed",
                         "panel_size", "squads_known", "said_all_gw"],
            "properties": {
                "said": {"type": "integer"},
                "observations": {"type": "integer"},
                "owned": {"type": "integer"},
                "noticed": {"type": "integer"},
                "panel_size": {"type": "integer"},
                "squads_known": {"type": "integer"},
                # `said` across every gameweek. `said` is a slice of it.
                "said_all_gw": {"type": "integer"},
            },
        },
        "reason": {"type": ["string", "null"]},
    },
}


def _coverage(wh, present: set[str], moment: dt.datetime) -> dict[str, int | None]:
    """How much of the player pool each channel actually reaches, MEASURED.

    Every empty section here has to explain itself, and the explanation that
    stops a reader mistaking silence for a negative finding is "this channel
    covers N of M players". Those numbers were 119 and 286 of 614 the day this
    was written -- and writing them into a string would have made them lies the
    moment the next ingest ran. They are counted per call instead; three
    aggregates over small tables, and the phrase is dropped when the count
    cannot be taken rather than falling back to a remembered figure.
    """
    out: dict[str, int | None] = {"said": None, "noticed": None, "pool": None}
    if "content_claim" in present:
        df = q(wh, "SELECT count(DISTINCT player_code) AS n FROM content_claim "
                   "WHERE published_at < ?", (moment,))
        out["said"] = None if df.empty else _i(df.iloc[0]["n"])
    if "intel_item" in present:
        df = q(wh, "SELECT count(DISTINCT player_code) AS n FROM intel_item "
                   "WHERE published_at < ?", (moment,))
        out["noticed"] = None if df.empty else _i(df.iloc[0]["n"])
    df = q(wh, "SELECT count(DISTINCT code) AS n FROM sem_players(?) "
               "WHERE season = ?", (moment, SEASON_DEFAULT))
    out["pool"] = None if df.empty else _i(df.iloc[0]["n"])
    return out


def _of_pool(n: int | None, pool: int | None) -> str | None:
    """``"119 of the 614 players in the pool"``, or None when it is not known."""
    if n is None:
        return None
    return f"{n} of the {pool} players in the pool" if pool else f"{n} players"


def _player_name(wh, code: int, moment: dt.datetime) -> str | None:
    df = q(
        wh,
        "SELECT web_name FROM sem_players(?) WHERE season = ? AND code = ?",
        (moment, SEASON_DEFAULT, int(code)),
    )
    return None if df.empty else _s(df.iloc[0]["web_name"])


def _url_bases(wh, item_ids: set[str], present: set[str]) -> dict[str, str | None]:
    """item_id -> which feed element ``content_item.url`` came from.

    ``enclosure`` means the stored "link" is the mp3 itself -- 353 of the 387
    asset rows in the live warehouse -- so the UI must offer "play audio", not
    "open episode". An item with no asset row yields None, which is "unknown",
    not "link".
    """
    if not item_ids or "content_item_asset" not in present:
        return {}
    ids = tuple(sorted(item_ids))
    rows = q(
        wh,
        "SELECT item_id, url_basis FROM content_item_asset WHERE item_id IN ("
        + ", ".join("?" for _ in ids) + ")",
        ids,
    )
    return {str(r["item_id"]): _s(r["url_basis"]) for r in rows.to_dict("records")}


def _noticed(wh, code: int, moment: dt.datetime, present: set[str],
             coverage: dict[str, int | None],
             limit: int = 12) -> tuple[list[dict[str, Any]], str | None]:
    """``intel_item`` for one player: measured signals, never spoken ones.

    784 rows over 286 players, which is 2.4x the reach of the creator corpus
    and has never been readable from the UI. Kinds are ``out_of_position``,
    ``set_piece``, ``availability`` and ``press_conference``.

    NOT windowed by ``days``, deliberately, and this is the one place the
    window does not apply. A set-piece row is a STANDING fact -- 209 of the 215
    of them in the live warehouse predate any 30-day window -- and dropping
    "first-choice penalties" because it was recorded in May would hide the most
    decision-relevant thing on the panel. ``published_at`` travels with every
    row so the reader ages it themselves, and the reason says so out loud.
    """
    if "intel_item" not in present:
        return [], ("intel_item is not in this warehouse; run "
                    "`python -m fpl_edge.intel.collect` to build it")
    rows = q(
        wh,
        "SELECT kind, headline, body, source, source_url, published_at, "
        "confidence FROM intel_item WHERE player_code = ? AND published_at < ? "
        "ORDER BY published_at DESC LIMIT ?",
        (int(code), moment, int(limit)),
    )
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for r in rows.to_dict("records"):
        kind, headline = str(r["kind"]), str(r["headline"])
        # The same standing fact re-observed on many days is one fact. The rows
        # arrive newest first, so the survivor is the most recent observation.
        if (kind, headline) in seen:
            continue
        seen.add((kind, headline))
        out.append({
            "kind": kind,
            "headline": headline,
            "body": _s(r["body"]),
            "source": str(r["source"]),
            "source_url": _s(r["source_url"]),
            "published_at": _iso(r["published_at"]),
            "confidence": _f(r["confidence"], 3),
        })
    if not out:
        reach = _of_pool(coverage.get("noticed"), coverage.get("pool"))
        return [], (
            "no measured intel names this player"
            + (f": intel_item covers {reach}, and being absent from it is the "
               f"ordinary state, not a failed lookup" if reach else
               ", and an absent row is not a negative finding")
        )
    return out, (
        "measured signals, not statements. These are NOT limited to the "
        "window: a set-piece or out-of-position finding is a standing fact "
        "that does not expire on a 30-day boundary, so each row carries its "
        "own published_at to be aged against."
    )


def _conviction(extractor: str, confidence: float | None) -> str | None:
    """The speaker's certainty, recovered only where it was actually recorded.

    ``llm:`` claims store ``CONVICTION_CONF[band]``, so the band inverts
    exactly. ``cue`` claims store a keyword-window score, which is a property
    of the extractor and not of the speaker -- it gets None, and the raw
    ``confidence`` is emitted beside it so nothing is lost.
    """
    if not str(extractor).startswith("llm"):
        return None
    return _CONF_TO_CONVICTION.get(round(float(confidence or 0.0), 2))


def _said(wh, code: int, moment: dt.datetime, present: set[str],
          roster: list[dict[str, Any]], panel_shows: set[str],
          coverage: dict[str, int | None], *,
          gw: int | None = None, since: dt.datetime | None = None
          ) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """Everything anybody tracked SAID about this player, FOR A GAMEWEEK.

    Two reads, because the warehouse stores the two kinds of utterance in two
    places and only one of them is a claim:

    * positions (buy/sell/hold/captain/bench/avoid) come from ``content_claim``
      through ``panel.person_claims_visible_at``, which is a join on top of
      ``ContentStore.claims_visible_at`` -- the ONE sanctioned point-in-time
      read -- and carries the ``item_person`` attribution with it.
    * ``watch`` calls come from ``content_analysis``, because the claim writer
      deliberately refuses to map them: a watch is not a scoreable position, so
      it was never written as a claim, and reading claims alone would silently
      drop all 56 of them. Each is stamped ``is_observation: true``.

    A position whose spoken name the resolver refused to disambiguate never
    became a claim either. That is the extractor declining to guess, not a gap
    this function may fill in.

    GAMEWEEK, NOT DAYS
    ------------------
    ``gw`` and ``since`` are two different axes and exactly one of them is
    active. ``content_claim.gameweek`` records the gameweek a statement was
    ABOUT; ``published_at`` records when it was made. A claim made three weeks
    ago about GW3 is a GW3 claim, and windowing by days answers neither
    question honestly: once GW2 has been played a 30-day window still shows GW2
    talk beside GW3 talk with nothing separating them, and once GW3 arrives the
    GW2 statements age out of the window entirely rather than staying findable.
    So a gameweek-scoped read is NOT also day-bounded -- ``since`` is None
    whenever ``gw`` is set, and the caller reaches the old behaviour by asking
    for ``gw: "all"`` explicitly, where ``days`` is the only bound there is.

    The third return value is the whole antidote to a filter that lies by
    omission: ``[{"gw", "n"}, ...]`` over EVERY gameweek this player has
    content for, computed before any filter is applied. "Nothing for GW3" is
    only useful next to "4 statements for GW2".
    """
    import pandas as pd

    from fpl_edge.ingest.content.panel import person_claims_visible_at

    names = {p["person_key"]: p["person"] for p in roster}
    store = _content_store(wh)
    claims = person_claims_visible_at(store, moment)
    if not claims.empty:
        claims = claims[claims["player_code"].astype("Int64") == int(code)]

    # Metadata for every VISIBLE item, not for a window of them. The window
    # used to bound this read and it cost twice: a claim whose item fell
    # outside it needed a second repair query to get a title at all, and a
    # sibling row outside it could not lend its transcript to a sibling inside
    # it, so the deep link silently lost its offset. `published_at < moment` is
    # the point-in-time bound and it is the only one this read needs.
    all_items = q(
        wh,
        "SELECT item_id, creator, title, url, published_at, kind, text_source "
        "FROM content_item WHERE published_at < ?",
        (moment,),
    )
    meta: dict[str, dict[str, Any]] = {
        str(r["item_id"]): r for r in all_items.to_dict("records")
    }

    # One publication may be stored under several rows, and in the live
    # warehouse the analysis sits on one of them while the transcript sits on
    # the other. Grouping on the canonical key is what lets a watch call
    # extracted from the `youtu.be/` row find its timestamp in the `watch?v=`
    # row's segments; without it every deep link on this panel loses its offset.
    family: dict[str, set[str]] = {}
    for iid, info in meta.items():
        family.setdefault(canonical_key(_s(info.get("url")), iid), set()).add(iid)

    def _representative(item_id: str) -> tuple[dict[str, Any], str, list[str]]:
        """The richest stored row of this publication, and its whole family."""
        info = meta.get(item_id, {})
        canon = canonical_key(_s(info.get("url")), item_id)
        kin = sorted(family.get(canon, {item_id}))
        rep_id = min(
            kin,
            key=lambda i: (
                _TEXT_RANK.get(str(meta.get(i, {}).get("text_source")), 9), i),
        ) if kin else item_id
        return (meta.get(rep_id, info) or info), rep_id, kin

    # The gameweek an UNDATED call was about, by the rule the ingester already
    # uses for exactly this (`GwCalendar.next_after`). A watch call never
    # becomes a claim, so nothing stamped it at ingest; inferring it here with
    # a second rule would let one video's dated buy and undated watch land in
    # two different gameweeks.
    calendar = _gw_calendar(wh, moment)

    # -- pass one: every candidate statement, with no transcript yet ---------
    pending: list[dict[str, Any]] = []

    for c in (claims.to_dict("records") if not claims.empty else []):
        rationale = _s(c.get("rationale")) or ""
        quote = (rationale.split("| quote: ", 1)[1]
                 if "| quote: " in rationale else rationale)
        extractor = str(c.get("extractor") or "cue")
        confidence = _f(c.get("confidence"), 3)
        pending.append({
            "item_id": str(c["item_id"]),
            "action": str(c.get("action")),
            "is_observation": False,
            "conviction": _conviction(extractor, confidence),
            "extractor": extractor,
            "quote": quote,
            "person_key": _s(c.get("person_key")),
            "basis": _s(c.get("basis")),
            # content_claim.gameweek is NOT NULL: the extractor stamps every
            # claim, and `gw_inferred` records whether the speaker said which
            # gameweek or the calendar decided for them. Both are on file, so
            # neither has to be guessed back.
            "gameweek": _i(c.get("gameweek")),
            "gameweek_basis": ("inferred" if bool(c.get("gw_inferred"))
                               else "stated"),
            "confidence": confidence,
        })

    resolver = None
    analyses = _analyses(wh, set(meta)) if "content_analysis" in present else {}
    for item_id, (analysis, model) in analyses.items():
        calls = []
        for _key, source in _CALL_BUCKETS:
            calls.extend(c for c in (analysis.get(source) or [])
                         if isinstance(c, dict))
        calls = [c for c in calls
                 if str(c.get("stance") or "").strip().lower()
                 in _OBSERVATION_STANCES]
        if not calls:
            continue
        if resolver is None:
            # Built once and only when a watch call actually needs resolving.
            # It is the SAME alias index the claim extractor used, so this
            # panel and the scoreboard agree about who "Bruno" is.
            resolver = _resolver(wh, SEASON_DEFAULT, moment)
        for c in calls:
            if _resolve_code(resolver, str(c.get("player") or "")) != int(code):
                continue
            conviction = _s(c.get("conviction"))
            stated = _i(c.get("gameweek"))
            published = meta.get(item_id, {}).get("published_at")
            pending.append({
                "item_id": item_id,
                "action": "watch",
                "is_observation": True,
                "conviction": (conviction
                               if conviction in _CONF_TO_CONVICTION.values()
                               else None),
                "extractor": f"llm:{model}",
                "quote": _s(c.get("quote")),
                "person_key": None,
                "basis": None,
                "gameweek": stated if stated is not None
                else _gw_after(calendar, published),
                "gameweek_basis": ("stated" if stated is not None else
                                   ("inferred" if _gw_after(calendar, published)
                                    is not None else None)),
                "confidence": None,
            })

    # -- pass two: transcripts, but only for the publications that matter ----
    # The window used to bound this and the bound is gone, so the set is
    # narrowed by relevance instead: 8,508 segments live in the live warehouse
    # and this player's statements touch a handful of videos. Loading all of
    # them to time-stamp four quotes would be the cost of the window with none
    # of its (wrong) benefit.
    wanted: set[str] = set()
    for p in pending:
        _rep, _rep_id, kin = _representative(p["item_id"])
        wanted.update(kin)
    transcripts = (_transcripts(wh, wanted)
                   if wanted and "transcript_segment" in present else {})
    bases = _url_bases(wh, wanted, present)

    def _row(p: dict[str, Any]) -> dict[str, Any]:
        info, rep_id, kin = _representative(p["item_id"])
        url = _s(info.get("url"))
        index = _NO_TRANSCRIPT
        for sib in kin:
            if sib in transcripts:
                index = transcripts[sib]
                break
        start_s = index.find(p["quote"])
        show = _s(info.get("creator")) or "(unknown show)"
        return {
            "person": (names.get(str(p["person_key"]))
                       if p["person_key"] else None),
            "person_basis": p["basis"],
            "show": show,
            "action": p["action"],
            "is_observation": p["is_observation"],
            "conviction": p["conviction"],
            "extractor": p["extractor"],
            "quote": p["quote"] or None,
            "start_s": _f(start_s, 2),
            "deep_link": deep_link(url, start_s),
            "published_at": _iso(info.get("published_at")),
            "item_title": _s(info.get("title")),
            "item_url": url,
            "url_basis": bases.get(rep_id),
            "item_id": rep_id,
            "gameweek": p["gameweek"],
            "gameweek_basis": p["gameweek_basis"],
            "confidence": p["confidence"],
            "on_panel": show in panel_shows,
        }

    # One publication, not one stored row. The same video under `watch?v=` and
    # `youtu.be/` is one thing somebody said once; keying on the raw item id
    # shows the reader two opinions where one was voiced.
    best: dict[tuple, dict[str, Any]] = {}
    for p in pending:
        row = _row(p)
        canon = canonical_key(row["item_url"], row["item_id"])
        key = ((canon, row["show"], "watch", _norm(row["quote"] or ""))
               if p["is_observation"] else
               (canon, row["show"], row["action"], row["gameweek"],
                row["extractor"]))
        kept = best.get(key)
        # Of two rows saying the same thing, keep the one whose quote could be
        # located in a transcript: same position, better evidence.
        if kept is None or (kept["start_s"] is None and row["start_s"] is not None):
            best[key] = row
    everything = list(best.values())

    # -- what exists PER GAMEWEEK, counted before anything is filtered -------
    tally: dict[Any, int] = {}
    for r in everything:
        tally[r["gameweek"]] = tally.get(r["gameweek"], 0) + 1
    by_gw = [{"gw": g, "n": n} for g, n in
             sorted(tally.items(), key=lambda kv: (kv[0] is None, kv[0] or 0))]

    # -- and only now, the filter -------------------------------------------
    if gw is not None:
        rows = [r for r in everything if r["gameweek"] == int(gw)]
    elif since is not None:
        keep = _stamps(pd.Series([r["published_at"] for r in everything]))
        rows = [r for r, when in zip(everything, keep)
                if not pd.isna(when) and when >= since]
    else:
        rows = list(everything)
    rows.sort(key=lambda r: (r["published_at"] or "", r["show"], r["action"]),
              reverse=True)

    scope = (f"for GW{int(gw)}" if gw is not None else
             (f"in the last {(moment - since).days} days" if since is not None
              else "for any gameweek"))
    if not rows:
        if everything:
            # THE POINT OF THE WHOLE BLOCK ABOVE. An empty strip under a
            # gameweek selector reads as "nobody rates him"; what is true is
            # "nobody said this about THIS week". Those are different claims
            # and only the second one is supported. The sentence names the
            # control that is actually hiding the rows -- blaming the gameweek
            # for what the day window did would send the reader to the wrong
            # one and leave them believing the panel is silent.
            if gw is not None:
                return [], (
                    f"nothing was said about this player {scope}. The panel "
                    f"did speak about him for other gameweeks: "
                    f"{_by_gw_phrase([b for b in by_gw if b['gw'] != int(gw)])}"
                    f". This is a gameweek filter, not silence; switch the "
                    f"gameweek to read those."
                ), by_gw
            return [], (
                f"nothing was said about this player {scope}, but "
                f"{sum(b['n'] for b in by_gw)} statements about him sit "
                f"outside that window ({_by_gw_phrase(by_gw)}). This is the "
                f"day window, not silence; widen `days`, or ask for a "
                f"gameweek, which is not day-bounded."
            ), by_gw
        reach = _of_pool(coverage.get("said"), coverage.get("pool"))
        return [], (
            f"nobody tracked has mentioned this player {scope}, nor for any "
            f"other gameweek."
            + (f" Claims exist for {reach}, so silence here is the ordinary "
               f"state and not a failed lookup." if reach else "")
        ), by_gw

    # What the filter is HIDING, named. Counted against the axis that is
    # actually in force, because "17 statements in other gameweeks" and "17
    # statements older than 30 days" are different facts and swapping them
    # would send a reader to the wrong control.
    if gw is not None:
        others = [b for b in by_gw if b["gw"] != int(gw)]
        n_hidden = sum(b["n"] for b in others)
        also = (f" {n_hidden} further statement{'s' if n_hidden != 1 else ''} "
                f"about him sit{'' if n_hidden != 1 else 's'} under other "
                f"gameweeks ({_by_gw_phrase(others)})." if n_hidden else "")
    else:
        n_hidden = sum(b["n"] for b in by_gw) - len(rows)
        also = (f" {n_hidden} further statement{'s' if n_hidden != 1 else ''} "
                f"about him fall{'' if n_hidden != 1 else 's'} outside that "
                f"window; the full census is {_by_gw_phrase(by_gw)}."
                if n_hidden > 0 else "")
    unattributed = sum(1 for r in rows if r["person"] is None)
    if unattributed == len(rows):
        return rows, (
            f"Everything here is {scope}." + also + " Every statement is "
            "attributed to a SHOW, not a person: item_person carries no "
            "attribution for these items yet, and a round-table episode "
            "belongs to the show until somebody is named. Run `python -m "
            "fpl_edge.ingest.content.panel attribute` to fill in the sole-host "
            "and title bases."
        ), by_gw
    if unattributed:
        return rows, (
            f"Everything here is {scope}." + also
            + f" {unattributed} of {len(rows)} statements are attributed to "
            f"the show rather than a person: no item_person row establishes "
            f"who was speaking, and the show is the honest answer for a "
            f"round-table episode."
        ), by_gw
    return rows, (f"Everything here is {scope}." + also if also else None), by_gw


def _by_gw_phrase(by_gw: list[dict[str, Any]]) -> str:
    """``"4 for GW2, 1 for GW3"`` -- where the rest of the talk actually is."""
    if not by_gw:
        return "none"
    return ", ".join(
        f"{b['n']} for GW{b['gw']}" if b["gw"] is not None
        else f"{b['n']} with no gameweek on file"
        for b in by_gw
    )


def player_chatter(wh, *, code: int, days: int = 30,
                   gw: int | str | None = None) -> dict[str, Any]:
    """One player, three separately-sourced channels, and no consensus score.

    ``owned`` is what the panel HOLDS, ``said`` is what they said, ``noticed``
    is what was measured about him. They are never merged, never netted and
    never summed into an agreement number -- see the section header above for
    why that refusal is the feature.

    ``gw`` indexes ``said`` the way ``creator_board`` indexes the board, and
    through the same ``_resolve_gw`` ladder: null means the next gameweek, an
    integer means that one, and ``"all"`` turns the gameweek filter off and
    lets ``days`` bound the read instead. ``said_by_gw`` is always the full
    per-gameweek census, so choosing a gameweek narrows what is shown and never
    hides that the other weeks have content.
    """
    moment = dt.datetime.now(UTC)
    code = int(code)
    present = _tables_present(wh, _CONTENT_TABLES + (
        "content_analysis", "transcript_segment", "content_item_asset",
        "item_person", "intel_item", _PANEL_TABLE, _PANEL_SHOW_TABLE,
        "fact_manager_pick", "fact_manager_transfer", "dim_player",
    ))
    # This panel has three independent sources and degrades one at a time. It
    # is honestly empty only when none of the three exists at all -- a
    # warehouse with intel and no creator corpus still has something true to
    # say about a player, and returning nothing would hide it.
    if not ({"content_claim", "intel_item", "fact_manager_pick"} & present):
        return empty(
            "this warehouse holds none of the three sources this panel reads "
            "(content_claim, intel_item, fact_manager_pick). Run the content "
            "pipeline, the intel collector or a manager crawl."
        )

    name = _player_name(wh, code, moment)
    if name is None:
        return empty(
            f"no player with code {code} exists in {SEASON_DEFAULT}. This is a "
            f"stable PlayerCode, not an element_id; the two differ, and "
            f"passing an element_id here finds nobody."
        )
    # A bare web_name can name two players (two Palmers). When it does, the
    # payload carries the first-initial-plus-club form so the drawer heading
    # can never silently mean somebody else.
    disambiguator = _naming(code, name, _display_index(wh, moment))["disambiguator"]

    roster, roster_reason = _panel_roster(wh, present)
    panel_by_code, panel_meta = _panel_squads(wh, roster, present, moment)
    owned = panel_by_code.get(code, [])
    owned_reason = panel_meta["reason"] if not roster_reason else roster_reason
    if owned:
        owned_reason = (
            f"{len(owned)} of the {panel_meta['known']} panel squads that have "
            f"been read hold him. " + owned_reason
        )

    coverage = _coverage(wh, present, moment)
    panel_shows = {s for p in roster for s in p["shows"]}

    # The two axes, resolved once and kept apart. `days` bounds this read only
    # when the caller has explicitly switched the gameweek filter off: a claim
    # made three weeks ago ABOUT GW3 is a GW3 claim, and letting a day window
    # also apply would drop it from the GW3 strip for no reason a reader could
    # ever guess. See `_said`'s "GAMEWEEK, NOT DAYS".
    if gw == "all":
        want_gw, gw_reason = None, (
            f"no gameweek filter was applied (`gw: \"all\"`), so the {int(days)}-"
            f"day window is the only bound on `said`. Statements about "
            f"different gameweeks are mixed together here; `said_by_gw` says "
            f"which is which."
        )
        since: dt.datetime | None = moment - dt.timedelta(days=int(days))
    else:
        want_gw, gw_reason = _resolve_gw(wh, None if gw is None else int(gw),
                                         moment)
        since = None
        gw_reason += (
            " `days` does not bound this: a statement made three weeks ago "
            "about this gameweek is still about this gameweek. Ask for "
            "`gw: \"all\"` to read by recency instead."
        )
    said, said_reason, said_by_gw = _said(
        wh, code, moment, present, roster, panel_shows, coverage,
        gw=want_gw, since=since,
    )
    noticed, noticed_reason = _noticed(wh, code, moment, present, coverage)

    reason = None
    if not owned and not said and not noticed:
        reach = _of_pool(coverage.get("said"), coverage.get("pool"))
        scope = (f"for GW{want_gw}" if want_gw is not None
                 else f"in {int(days)} days")
        elsewhere = sum(b["n"] for b in said_by_gw)
        reason = (
            f"Nothing is on file for {name} in any of the three channels: no "
            f"panel squad that has been read holds him, nobody tracked has "
            f"mentioned him {scope}, and no measured intel row names him."
            + (f" He IS spoken about in other gameweeks "
               f"({_by_gw_phrase(said_by_gw)}), so only this gameweek is "
               f"silent." if elsewhere else "")
            + (f" Claims cover {reach}, so this is the ordinary state for most "
               f"of the pool." if reach else "")
        )

    return {
        "code": code,
        "name": name,
        "disambiguator": disambiguator,
        "as_of": moment.isoformat(),
        "window_days": int(days),
        # Which gameweek `said` is FOR, and why that one. Null means no
        # gameweek filter is in force (`gw: "all"`, or no calendar to resolve
        # against) -- `gw_reason` says which of the two.
        "gw": want_gw,
        "gw_reason": gw_reason,
        # DID first. The one channel with no epistemic problem leads.
        "owned": owned,
        "owned_reason": owned_reason,
        "said": said,
        "said_reason": said_reason,
        # Every gameweek this player has been spoken about in, counted BEFORE
        # the filter above. A gameweek selector that hides the existence of the
        # other gameweeks is worse than no selector at all.
        "said_by_gw": said_by_gw,
        "noticed": noticed,
        "noticed_reason": noticed_reason,
        "counts": {
            "said": len(said),
            "observations": sum(1 for s in said if s["is_observation"]),
            "owned": len(owned),
            "noticed": len(noticed),
            "panel_size": int(panel_meta["panel_size"]),
            "squads_known": int(panel_meta["known"]),
            # Across ALL gameweeks. `said` is a slice of this, never the whole.
            "said_all_gw": sum(b["n"] for b in said_by_gw),
        },
        "reason": reason,
    }

register_script(
    name="player_chatter",
    fn=player_chatter,
    params_schema=CHATTER_PARAMS,
    result_schema=CHATTER_RESULT,
    title="The panel on this player",
    description="One player across three channels: what panel members actually "
                "own (measured picks), what creators said about him (quoted, "
                "timestamped, weight 0.0), and what was measured about him in "
                "intel_item. No consensus score is emitted, by design.",
)
