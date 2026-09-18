"""Who said it, where it came from, and the loaders every panel shares.

Creator resolution, the transcript index, the gameweek rules and the warehouse
readers the four panels sit on. Imports nothing else in this package."""

from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import unicodedata
from typing import Any

from fpl_edge.ingest.content.claims import GameweekCalendar
from fpl_edge.ingest.content.urls import deep_link
from fpl_edge.platform.prose_style import normalize_prose
from fpl_edge.platform.scripts.common import SEASON_DEFAULT, next_gw, q

UTC = dt.UTC

#: Content tables live in ingest/content/migrations, not store/schema.sql, so a
#: warehouse built before those migrations ran simply does not have them.
_CONTENT_TABLES = ("content_source", "content_item", "content_claim")

#: The panel-member registry planned in docs/platform/CREATOR_ELITE_PROMPT.md
#: Stage A: ``dim_panel_member(member_key, display_name, kind, entry_id,
#: id_source_url, id_verified_utc, verified_entry_name, ...)``. It does not
#: exist yet. This script reads it if it appears and says so when it has not,
#: rather than guessing an entry id from a name collision in dim_manager --
#: 12,276 crawled managers make a name match a coin flip, and a wrong entry id
#: renders as somebody else's squad under a creator's name.
# The roster of tracked PEOPLE and the shows they appear on.
#
# This was written against `dim_panel_member`, the table CREATOR_ELITE_PROMPT.md
# Stage A specifies. Stage A never ran; what exists is `panel_person` /
# `panel_person_show`, built by the person-model work when the owner asked for
# co-hosts to be tracked individually. Two names for one concept, from two
# pieces of work that did not meet -- and the cost was silent: every entry
# lookup read a table that does not exist, so every creator's verified team
# rendered as "not published" while sixteen verified ids sat in the warehouse.
_PANEL_TABLE = "panel_person"
_PANEL_SHOW_TABLE = "panel_person_show"


# ---------------------------------------------------------------------------
# Quote -> timestamp.

_PUNCT = re.compile(r"[^a-z0-9]+")


def _norm(text: str) -> str:
    """Fold accents, drop punctuation, collapse whitespace.

    Auto-captions have no punctuation and no accents; an LLM quote has both
    ("Groß", "Sávio"). Comparing the raw strings finds nothing.
    """
    folded = unicodedata.normalize("NFKD", str(text)).casefold()
    stripped = "".join(c for c in folded if not unicodedata.combining(c))
    return _PUNCT.sub(" ", stripped).strip()


class TranscriptIndex:
    """Normalised transcript text with a char-offset -> ``start_s`` map.

    Quotes are stored per call, transcripts per segment, and a quote routinely
    straddles two segments -- matching quote against individual segments misses
    most of them. The whole transcript is normalised once into a single string
    and the segment holding a match is recovered by bisecting the offsets.
    """

    __slots__ = ("_offsets", "_starts", "text")

    def __init__(self, rows: list[tuple[float | None, str]]) -> None:
        parts: list[str] = []
        self._offsets: list[int] = []
        self._starts: list[float | None] = []
        pos = 0
        for start_s, text in rows:
            body = _norm(text)
            if not body:
                continue
            if parts:
                parts.append(" ")
                pos += 1
            self._offsets.append(pos)
            self._starts.append(start_s)
            parts.append(body)
            pos += len(body)
        self.text = "".join(parts)

    def __bool__(self) -> bool:
        return bool(self.text)

    def find(self, quote: str | None) -> float | None:
        """``start_s`` of the segment where ``quote`` begins, or None.

        Exact first, then a shrinking prefix: an LLM quote is "verbatim (light
        truncation allowed)" and captions drift, so the tail is the unreliable
        end. Six words is the floor -- shorter windows start matching filler and
        a timestamp pointing at the wrong minute is worse than no timestamp.
        """
        needle = _norm(quote or "")
        if not needle or not self.text:
            return None
        at = self.text.find(needle)
        if at < 0:
            words = needle.split()
            for span in (12, 8, 6):
                if len(words) <= span:
                    continue
                at = self.text.find(" ".join(words[:span]))
                if at >= 0:
                    break
        if at < 0:
            return None
        idx = max(bisect.bisect_right(self._offsets, at) - 1, 0)
        return self._starts[idx]


_NO_TRANSCRIPT = TranscriptIndex([])

#: When one publication is stored under several rows, the row carrying the
#: richest text represents it. A transcript beats an article beats a
#: description; the others are the same publication seen through less.
_TEXT_RANK = {"transcript": 0, "article": 1, "description": 2}


# ---------------------------------------------------------------------------
# Small JSON-boundary helpers.

def _f(x, nd: int = 2) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else round(v, nd)


def _i(x) -> int | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v != v else int(v)


def _s(x) -> str | None:
    if x is None or (isinstance(x, float) and x != x):
        return None
    text = str(x)
    return text if text and text.lower() != "nat" else None


def _plain(x) -> str | None:
    """House style on a string this module did not write.

    Some ``*_reason`` values are seed data (``data/panels/*.yaml``) rather than
    code, and the owner's rule bans the ASCII ``--`` pair the seed rows use as
    an aside. The panel renders these verbatim, so the pair is turned into a
    sentence break here rather than left for the UI to paper over. Nothing else
    about the text is touched.
    """
    text = _s(x)
    if text is None:
        return None
    while " -- " in text:
        head, _, tail = text.partition(" -- ")
        joiner = ". " if tail[:1].isupper() else "; "
        text = head.rstrip(",;: ") + joiner + tail.lstrip()
    return text


def _iso(x) -> str | None:
    """A timestamp as real ISO-8601, whatever shape it arrived in.

    ``guarded_query`` stringifies datetime columns, so a published_at reaches
    this module as ``"2026-08-25 05:06:54.706755+00:00"`` -- space separated,
    which ``Date.parse`` treats as implementation-defined. Every timestamp in
    the payload is re-emitted with the ``T``, and NEVER compared as a string:
    ``"2026-08-24 23:00+00:00" >= "2026-08-24T12:00+00:00"`` is False because
    ``' ' < 'T'``, which would silently drop a day's items from the window.
    """
    import pandas as pd

    if x is None or (isinstance(x, float) and x != x):
        return None
    stamp = pd.to_datetime(x, utc=True, errors="coerce")
    return None if pd.isna(stamp) else stamp.isoformat()


def _stamps(values):
    """Parse a column of timestamp strings ELEMENT BY ELEMENT.

    ``pd.to_datetime`` on a whole object column infers one format from the
    first value and coerces everything that does not match to ``NaT``. The
    content tables mix ``…54.706755+00:00`` (shared links, sub-second) with
    ``…15:00+00:00`` (feeds, whole seconds), so the vectorised call silently
    turned every shared-link item into NaT and dropped that creator out of the
    window with a count of 0 rather than an error. Per-element parsing infers
    per value and is immeasurable at this row count.
    """
    import pandas as pd

    return pd.Series(
        [pd.to_datetime(v, utc=True, errors="coerce") for v in values],
        index=getattr(values, "index", None),
        dtype="datetime64[ns, UTC]",
    )


def _tables_present(wh, names: tuple[str, ...]) -> set[str]:
    df = q(
        wh,
        "SELECT table_name FROM information_schema.tables WHERE table_name IN ("
        + ", ".join("?" for _ in names) + ")",
        names,
    )
    return set(df["table_name"]) if not df.empty else set()


def _content_store(wh):
    """A store bound to the read copy, without running migrations.

    ``ContentStore.__init__`` migrates, and the panel's handle is read-only by
    construction. Same construction as ``content_tools._open_store``: the point
    is to reach ``claims_visible_at``, which is the only sanctioned claim read.
    """
    from fpl_edge.ingest.content.store import ContentStore

    store = ContentStore.__new__(ContentStore)
    store.wh = wh
    return store


def _weights_as_of(wh, moment: dt.datetime) -> dict[str, dict[str, Any]]:
    """``creator_score`` scope='all' in force at ``moment``, newest per creator.

    The ``as_of <= ?`` bound is the whole point (see module docstring rule 4).
    ``creator_score`` is append-only and keyed by ``as_of`` precisely so a past
    weight is recoverable; without the bound this reads today's row and a
    question about a past deadline is answered with knowledge it did not have.
    ``<=`` rather than ``<`` because a score row is a derived-table stamp, not
    an utterance somebody had to read.
    """
    rows = q(
        wh,
        """
        SELECT creator, claims_total, claims_scored, hits, hit_rate,
               wilson_lo95, weight, first_claim_utc, last_claim_utc, as_of
        FROM (
            SELECT *, row_number() OVER (
                PARTITION BY creator ORDER BY as_of DESC) rn
            FROM creator_score WHERE scope = 'all' AND as_of <= ?
        ) WHERE rn = 1
        """,
        (moment,),
    )
    out: dict[str, dict[str, Any]] = {}
    if rows.empty:
        return out
    for r in rows.to_dict("records"):
        out[str(r["creator"])] = r
    return out


def _record(row: dict[str, Any] | None) -> dict[str, Any]:
    """The track record block. Unmeasured is null, never zero.

    ``weight`` is the exception: it is 0.0 by *definition* until a creator's
    Wilson lower bound clears 0.5 over enough claims, so a scored creator with
    no earned weight genuinely has 0.0 and saying so is not a stand-in.
    """
    if row is None:
        return {
            "scored": None, "hits": None, "hit_rate": None, "wilson_lo95": None,
            "weight": None, "earned": False,
            "reason": "no creator_score row at or before this instant; nothing "
                      "of theirs has been scored yet",
        }
    scored = _i(row.get("claims_scored")) or 0
    weight = _f(row.get("weight"), 4) or 0.0
    return {
        "scored": scored,
        "hits": _i(row.get("hits")),
        "hit_rate": _f(row.get("hit_rate"), 4),
        "wilson_lo95": _f(row.get("wilson_lo95"), 4),
        "weight": weight,
        "earned": weight > 0.0,
        "reason": None if scored else (
            "claims recorded but none scoreable yet: a claim becomes checkable "
            "only once its gameweek has finalised"
        ),
    }


# ---------------------------------------------------------------------------
# Analysis -> the contract's `take` shape.

_CALL_BUCKETS = (
    ("transfers_in", "transfers_in"),
    ("transfers_out", "transfers_out"),
    ("captain", "captaincy"),
    ("differentials", "differentials"),
)


def _resolver(wh, season: str, moment: dt.datetime):
    """The SAME alias index the claim extractor uses, at ``moment``.

    ``TranscriptAnalysis`` records the player's name as spoken, not a code, so
    the code in the payload has to be resolved. Doing it with the shared
    resolver means the panel and the scoreboard agree about who "Bruno" is; an
    ad-hoc name match here would disagree with the claim rows on the same page.
    A name that resolves to zero or to more than one player yields ``code:
    null`` -- the creator's own words survive, the identity does not get guessed.
    """
    from fpl_edge.ingest.content.resolve import resolver_for

    players = q(
        wh,
        """
        SELECT code, web_name, first_name, second_name FROM (
            SELECT *, row_number() OVER (
                PARTITION BY season, code ORDER BY as_of DESC) rn
            FROM dim_player WHERE season = ? AND as_of <= ?
        ) WHERE rn = 1
        """,
        (season, moment),
    )
    if players.empty:
        return None
    return resolver_for(players)


def _display_index(wh, moment: dt.datetime) -> dict[str, Any]:
    """Canonical display identity per player code, for READ-time naming.

    The claim rows store the name AS SPOKEN ("rayan cherki", "Martin
    Odegaard"), so one player can exist under several spellings and his claims
    split across identities. This index maps a RESOLVED code -- and only a
    resolved code; identity is never guessed from the string here -- to the
    same ``sem_players`` identity every other panel renders, so "Ødegaard" is
    one column everywhere.

    ``shared`` marks web_names carried by two or more players in the pool (two
    Palmers), so any surface serving the bare web_name can attach a first
    initial + club instead of leaving a namesake ambiguous.
    """
    rows = q(
        wh,
        """
        SELECT sp.code, sp.web_name, sp.team, dp.first_name
        FROM sem_players(?) sp
        LEFT JOIN (
            SELECT code, first_name FROM (
                SELECT code, first_name, row_number() OVER (
                    PARTITION BY season, code ORDER BY as_of DESC) rn
                FROM dim_player WHERE season = ? AND as_of <= ?
            ) WHERE rn = 1
        ) dp ON dp.code = sp.code
        WHERE sp.season = ?
        """,
        (moment, SEASON_DEFAULT, moment, SEASON_DEFAULT),
    )
    by_code: dict[int, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for r in rows.to_dict("records"):
        code = _i(r.get("code"))
        web = _s(r.get("web_name"))
        if code is None or web is None:
            continue
        by_code[code] = {"web_name": web, "team": _s(r.get("team")),
                         "first_name": _s(r.get("first_name"))}
        key = web.casefold()
        counts[key] = counts.get(key, 0) + 1
    return {"by_code": by_code,
            "shared": {k for k, n in counts.items() if n > 1}}


def _naming(code: int | None, raw_name: str | None,
            display: dict[str, Any] | None) -> dict[str, Any]:
    """The read-side naming contract every claim/call surface serves.

    * resolved (code known to the pool)  -> ``display_name`` is the canonical
      ``web_name``, ``resolved: true``.
    * unresolved -> the creator's own words survive untouched with
      ``resolved: false``. Never edit-distance, never nearest-looking.
    * namesake  -> ``disambiguator`` ("C. Palmer (CHE)") whenever the served
      web_name is carried by more than one player, so a bare surname can
      never silently mean two people.
    """
    raw = (raw_name or "").strip()
    info = (display or {}).get("by_code", {}).get(code) if code is not None else None
    if info is None:
        return {"display_name": raw or "(unnamed)", "resolved": False,
                "disambiguator": None}
    web = str(info["web_name"])
    dis = None
    if web.casefold() in (display or {}).get("shared", set()):
        initial = (info.get("first_name") or "")[:1]
        team = info.get("team")
        bits = f"{initial}. {web}" if initial else web
        dis = f"{bits} ({team})" if team else bits
    return {"display_name": web, "resolved": True, "disambiguator": dis}


def _resolve_code(resolver, name: str) -> int | None:
    """Exact alias lookup. Unknown and ambiguous both yield None.

    ``lookup``, not ``find_mentions``. The analysis field is a bare full name,
    and ``find_mentions`` is a longest-match *scan* built for prose: given
    "Martin Ødegaard" it tokenises with ``[a-z0-9]+``, loses the ``ø``, fails on
    "degaard", falls back to the single token "martin" and resolves it to David
    Raya Martín. Measured on the live warehouse: ``find_mentions`` returned
    Raya's code 154561 for Ødegaard, while ``lookup`` returns 184029. ``lookup``
    normalises the whole phrase (accents folded) against the alias index and
    refuses anything that is not exactly one player.
    """
    if resolver is None or not name:
        return None
    code, _reason = resolver.lookup(str(name))
    return None if code is None else int(code)


def _call(call: dict[str, Any], resolver, index: TranscriptIndex,
          url: str | None, display: dict[str, Any] | None = None
          ) -> dict[str, Any]:
    quote = _s(call.get("quote"))
    start_s = index.find(quote)
    raw = str(call.get("player") or "").strip()
    code = _resolve_code(resolver, raw)
    return {
        "code": code,
        # `name` stays the creator's own words, verbatim -- the naming fields
        # below are the canonical read, never a replacement for the evidence.
        "name": raw or "(unnamed)",
        **_naming(code, raw, display),
        "conviction": str(call.get("conviction") or "medium"),
        "quote": quote,
        "start_s": _f(start_s, 2),
        "deep_link": deep_link(url, start_s),
    }


def _take(analysis: dict[str, Any] | None, model: str | None, resolver,
          index: TranscriptIndex, url: str | None,
          display: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    # The house prose rule applied on the way out. These bullets are model
    # output stored in content_analysis, so the rows already written cannot be
    # rewritten at the source, and an em-dash reaching the page is the same
    # defect whoever typed it. normalize_prose rewrites the punctuation
    # losslessly; it never drops a finding.
    bullets = [normalize_prose(str(b))
               for b in (analysis.get("summary") or []) if str(b).strip()]
    take: dict[str, Any] = {
        "summary": "\n".join(bullets),
        "summary_bullets": bullets,
        "model": str(model or "unknown"),
    }
    # A "watch" is an OBSERVATION, not a recommendation, and the analysis
    # stores it inside whichever list it came up in -- 41 of them currently sit
    # in `transfers_in`. Rendering those under a transfer-in heading puts a buy
    # in a named person's mouth that they did not make: "keep an eye on Foden"
    # becomes "Foden, transfer in". That is the no-fabrication rule, not a
    # presentational preference, so watch calls are lifted out into their own
    # bucket wherever they were stored.
    watching: list[dict[str, Any]] = []
    for key, source in _CALL_BUCKETS:
        kept = []
        for c in (analysis.get(source) or []):
            if not isinstance(c, dict):
                continue
            call = _call(c, resolver, index, url, display)
            if str(c.get("stance") or "").strip().lower() == "watch":
                # Where it was stored is kept, because "watch, raised while
                # talking about transfers in" is more informative than "watch".
                watching.append({**call, "raised_in": key})
            else:
                kept.append(call)
        take[key] = kept
    take["watching"] = watching
    chips = []
    for c in analysis.get("chip_advice") or []:
        if not isinstance(c, dict):
            continue
        quote = _s(c.get("quote"))
        start_s = index.find(quote)
        chips.append({
            "chip": str(c.get("chip") or "unknown"),
            "stance": str(c.get("stance") or "unknown"),
            "quote": quote,
            "horizon_gw": _i(c.get("gameweek")),
            "start_s": _f(start_s, 2),
            "deep_link": deep_link(url, start_s),
        })
    take["chips"] = chips
    return take


def _take_reason(latest: dict[str, Any] | None, has_transcript: bool) -> str:
    """Why there is no take. Names the actual state of the actual item."""
    if latest is None:
        return ("no item has ever been collected from this creator's sources, "
                "so there is nothing to summarise")
    what = {
        "description": "the video description / show notes only. YouTube "
                       "transcripts are not collected (robots.txt disallows "
                       "the caption route), so there is no speech to analyse",
        "article": "the article text",
        "transcript": "a full transcript",
    }.get(str(latest.get("text_source")), str(latest.get("text_source")))
    tail = (" A transcript IS on file, so this item is analysable and the "
            "backfill has simply not reached it."
            if has_transcript else "")
    return (f"no analysis has been run on their latest item: it carries {what}."
            f"{tail}")


# ---------------------------------------------------------------------------
# Shared loaders.

def _items(wh, moment: dt.datetime, creator: str | None = None):
    """Items published strictly before ``moment``, newest first.

    Mirrors ``ContentStore.items_visible_at`` (``published_at < as_of``) with
    the creator filter pushed into SQL rather than into pandas.
    """
    where = ["published_at < ?"]
    params: list[Any] = [moment]
    if creator is not None:
        where.append("creator = ?")
        params.append(creator)
    return q(
        wh,
        "SELECT item_id, source_key, creator, kind, title, url, published_at, "
        "text_source FROM content_item WHERE " + " AND ".join(where)
        + " ORDER BY published_at DESC",
        tuple(params),
    )


def _analyses(wh, item_ids: set[str]) -> dict[str, tuple[dict[str, Any], str]]:
    """item_id -> (parsed analysis, model). Newest analysis per item wins.

    One row per (item, model) by design, so re-analysis with a newer model does
    not overwrite what an older one said; the panel shows the newest.
    """
    if not item_ids:
        return {}
    ids = tuple(sorted(item_ids))
    rows = q(
        wh,
        "SELECT item_id, model, created_utc, analysis_json FROM content_analysis "
        "WHERE item_id IN (" + ", ".join("?" for _ in ids) + ") "
        "ORDER BY created_utc",
        ids,
    )
    out: dict[str, tuple[dict[str, Any], str]] = {}
    for r in rows.to_dict("records"):
        try:
            parsed = json.loads(str(r["analysis_json"]))
        except (TypeError, ValueError):
            continue  # a malformed row is skipped, not rendered as an empty take
        if isinstance(parsed, dict):
            out[str(r["item_id"])] = (parsed, str(r["model"]))
    return out


def _transcripts(wh, item_ids: set[str]) -> dict[str, TranscriptIndex]:
    if not item_ids:
        return {}
    ids = tuple(sorted(item_ids))
    rows = q(
        wh,
        "SELECT item_id, start_s, text FROM transcript_segment WHERE item_id IN ("
        + ", ".join("?" for _ in ids) + ") ORDER BY item_id, seq",
        ids,
    )
    grouped: dict[str, list[tuple[float | None, str]]] = {}
    for r in rows.to_dict("records"):
        grouped.setdefault(str(r["item_id"]), []).append(
            (_f(r["start_s"], 2), str(r["text"]))
        )
    return {k: TranscriptIndex(v) for k, v in grouped.items()}


def _entries(wh, present: set[str], moment: dt.datetime
             ) -> tuple[dict[str, dict[str, Any]], str]:
    """creator -> verified entry, and the reason when there is none.

    Nothing links a creator to an FPL entry id yet. The planned home is
    ``dim_panel_member`` (CREATOR_ELITE_PROMPT.md Stage A), where ``entry_id``
    is explicitly blank until verified against the FPL API. Read it if it is
    there; refuse to guess if it is not.
    """
    reason = (
        f"no verified team id on file: {_PANEL_TABLE} does not exist yet, and "
        f"an entry id is only usable once it has been checked against "
        f"fantasy.premierleague.com/api/entry/<id>/. Guessing one from a name "
        f"match against 12k crawled managers would render a stranger's squad."
    )
    if _PANEL_TABLE not in present:
        return {}, reason
    # Keyed by SHOW, because the board's row is a show today. A show with
    # several hosts yields several people, so this returns a LIST per show --
    # collapsing four Wire hosts into one "the show's team" is exactly the
    # conflation the person model exists to end.
    rows = q(
        wh,
        f"SELECT p.display_name, p.entry_id, p.entry_source_url, "
        f"p.entry_verified, p.entry_api_name, p.entry_reason, s.show_creator "
        f"FROM {_PANEL_TABLE} p JOIN {_PANEL_SHOW_TABLE} s USING (person_key) "
        f"WHERE p.active",
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows.to_dict("records"):
        show = _s(r.get("show_creator"))
        if not show:
            continue
        person = {
            "person": str(r["display_name"]),
            "entry_id": _i(r.get("entry_id")),
            "name": _s(r.get("entry_api_name")) or str(r["display_name"]),
            "verified": bool(r.get("entry_verified")),
            "source_url": _s(r.get("entry_source_url")),
            "reason": _plain(r.get("entry_reason")),
        }
        bucket = out.setdefault(show, {"people": []})
        bucket["people"].append(person)
    for show, bucket in out.items():
        verified = [x for x in bucket["people"] if x["entry_id"] is not None]
        # The show-level fields stay populated only when the show has exactly
        # one verified person; otherwise the UI must name a person, not a show.
        head = verified[0] if len(verified) == 1 else None
        bucket.update({
            "entry_id": head["entry_id"] if head else None,
            "name": head["name"] if head else None,
            "verified": bool(head),
            "source_url": head["source_url"] if head else None,
        })
    return out, (
        f"no row in {_PANEL_TABLE} carries a verified entry id for this creator"
    )


# ---------------------------------------------------------------------------
# The panel as PEOPLE, and what those people actually own.
#
# `_entries` above answers "who is this SHOW?" and is keyed by show. Everything
# below answers "who is the PANEL, and what is in their teams?" and is keyed by
# person. They are different questions with different keys and the difference
# is load bearing: Zophar appears on The FPL Wire and on Fantasy Football Hub,
# so the show-keyed read yields 18 rows for a roster of 16 people, and a panel
# size taken from it is wrong by two.

def _panel_roster(wh, present: set[str]) -> tuple[list[dict[str, Any]], str | None]:
    """One row per active panel PERSON, with the shows they appear on.

    Returns ``([], reason)`` when the roster cannot be read, never a guess. An
    entry id is only usable once it has been checked against the FPL API, and
    the roster is the only place that check is recorded.
    """
    if _PANEL_TABLE not in present:
        return [], (
            f"the panel roster is not in this warehouse ({_PANEL_TABLE} is "
            f"missing), so no panel member can be named. Load it with "
            f"`python -m fpl_edge.ingest.content.panel load`."
        )
    rows = q(
        wh,
        f"SELECT person_key, display_name, entry_id, entry_verified, "
        f"entry_api_name, entry_reason FROM {_PANEL_TABLE} WHERE active "
        f"ORDER BY display_name",
    )
    if rows.empty:
        return [], f"{_PANEL_TABLE} exists but holds no active person"
    shows: dict[str, list[str]] = {}
    if _PANEL_SHOW_TABLE in present:
        srows = q(
            wh,
            f"SELECT person_key, show_creator FROM {_PANEL_SHOW_TABLE} "
            f"ORDER BY show_creator",
        )
        for r in srows.to_dict("records"):
            show = _s(r.get("show_creator"))
            if show:
                shows.setdefault(str(r["person_key"]), []).append(show)
    out = []
    for r in rows.to_dict("records"):
        key = str(r["person_key"])
        out.append({
            "person_key": key,
            "person": str(r["display_name"]),
            "entry_id": _i(r.get("entry_id")),
            "entry_name": _s(r.get("entry_api_name")),
            "verified": bool(r.get("entry_verified")),
            "reason": _plain(r.get("entry_reason")),
            "shows": shows.get(key, []),
        })
    return out, None


def _events(wh, moment: dt.datetime):
    """``(gw, deadline_utc)`` as known at ``moment``, one row per gameweek.

    The single read of ``dim_event`` in this module. Everything gameweek-shaped
    here -- which deadline a squad locked at, which gameweek a panel answers
    for, which gameweek an undated call was about -- is that one table asked a
    different question, and three copies of this SELECT would be three chances
    to forget the ``as_of <=`` bound.
    """
    return q(
        wh,
        "SELECT gw, deadline_utc FROM ("
        "  SELECT *, row_number() OVER (PARTITION BY season, gw ORDER BY as_of DESC) rn"
        "  FROM dim_event WHERE season = ? AND as_of <= ?"
        ") WHERE rn = 1",
        (SEASON_DEFAULT, moment),
    )


def _deadlines(wh, moment: dt.datetime) -> dict[int, str | None]:
    """gw -> the deadline it locked at, as known at ``moment``.

    ``sem_manager_picks`` does not carry ``as_of``: picks are stamped with the
    gameweek deadline at ingest, and the deadline is the instant a squad became
    a fact about the world. Reading it from ``dim_event`` rather than reaching
    into ``fact_manager_pick`` keeps the one sanctioned manager read intact.
    """
    df = _events(wh, moment)
    return {int(r["gw"]): _iso(r["deadline_utc"]) for r in df.to_dict("records")
            if _i(r["gw"]) is not None}


def _gw_calendar(wh, moment: dt.datetime) -> GameweekCalendar:
    """The ingester's own calendar, built from this module's point-in-time read.

    ``ingest/content/claims.py::GameweekCalendar.next_after`` answers "which
    gameweek was this published before the deadline of", and it is what stamps
    ``content_claim.gameweek`` for every call the model did not date itself
    (``gw_inferred = true``, 122 rows in the live warehouse). A ``watch`` call
    never becomes a claim, so nothing stamps it, and reading one back needs the
    SAME rule, or a panel filtered to GW2 would show a dated buy beside an
    undated watch from the same video and disagree with itself about which
    gameweek that video was about.

    This module used to carry its own copy of that rule. The copy is gone
    (ARCHITECTURE_REVIEW.md check 1 and move M4); what is left here is the
    adapter from ``_events`` to the ingester's class, because the panel's read
    is bounded by ``as_of <= moment`` and the ingester's is not.
    """
    import pandas as pd

    rows: list[tuple[str, int, dt.datetime]] = []
    for r in _events(wh, moment).to_dict("records"):
        gw = _i(r["gw"])
        when = pd.to_datetime(r["deadline_utc"], utc=True, errors="coerce")
        if gw is None or pd.isna(when):
            continue
        rows.append((SEASON_DEFAULT, gw, when.to_pydatetime()))
    return GameweekCalendar(rows)


def _gw_after(calendar: GameweekCalendar, when) -> int | None:
    """``next_after`` over a value that may be a pandas timestamp or NaT.

    The rule lives in :class:`GameweekCalendar`. This is the coercion its
    caller owes it: ``published_at`` arrives off a dataframe, so it can be NaT
    or a naive timestamp, and ``next_after`` compares datetimes.
    """
    import pandas as pd

    if when is None:
        return None
    stamp = pd.to_datetime(when, utc=True, errors="coerce")
    if pd.isna(stamp):
        return None
    found = calendar.next_after(stamp.to_pydatetime())
    return int(found[1]) if found is not None else None


def _resolve_gw(wh, gw: int | None, moment: dt.datetime,
                claims=None) -> tuple[int | None, str]:
    """Which gameweek a panel is answering for, and WHY. One rule, shared.

    ``creator_board`` established it and ``player_chatter`` reuses it verbatim:
    an explicit ``gw`` wins, otherwise the next gameweek whose deadline has not
    passed, otherwise the newest gameweek any visible claim names, otherwise
    nothing -- and every branch says which one it took. A second copy of this
    ladder is how two panels on one page come to disagree about what "this
    week" means, so there is exactly one.

    The reason is ALWAYS a string, never null. "GW2" alone does not tell a
    reader whether it was asked for, deduced from the calendar, or scraped off
    the claims because the calendar was unreadable, and those are three
    different amounts of trust.
    """
    if gw is not None:
        return int(gw), f"GW{int(gw)} was requested explicitly."
    nxt = next_gw(wh, SEASON_DEFAULT, moment)
    if nxt is not None:
        return int(nxt), (
            f"GW{int(nxt)} is the next gameweek: it is the first whose "
            f"deadline has not passed at this instant. No gameweek was "
            f"requested, so the panel answers for the one being played into."
        )
    if claims is not None and not claims.empty:
        latest = _i(claims["gameweek"].max())
        if latest is not None:
            return latest, (
                f"no future deadline is on file, so this falls back to GW"
                f"{latest}, the latest gameweek any visible claim names. "
                f"That is the corpus talking, not the calendar."
            )
    return None, (
        "no gameweek could be determined: dim_event has no future deadline and "
        "no visible claim names one. Nothing is filtered by gameweek here."
    )


def _panel_squads(wh, roster: list[dict[str, Any]], present: set[str],
                  moment: dt.datetime
                  ) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
    """Which panel member holds which player, from ``sem_manager_picks(as_of)``.

    Returns ``(code -> [holding, ...], meta)``. This is the DID channel and it
    is the only part of this module that is not somebody's opinion: a pick is a
    fact with a deadline on it.

    The meta block is the whole honesty of the feature. ``known`` is the number
    of panel entries whose squad has actually been crawled -- 7 of 15 today --
    and ``unknown_people`` names the rest. Without that denominator, ``n: 0``
    on a row reads as "nobody on the panel owns him" when what it means is "we
    have not looked at eight of their teams". Those are opposite claims.
    """
    verified = [p for p in roster if p["entry_id"] is not None]
    meta: dict[str, Any] = {
        "panel_size": len(roster),
        "with_entry": len(verified),
        "known": 0,
        "gw": None,
        "unknown_people": [],
        "no_entry_people": sorted(p["person"] for p in roster
                                  if p["entry_id"] is None),
        "reason": "",
    }
    by_code: dict[int, list[dict[str, Any]]] = {}
    if not roster:
        meta["reason"] = ("the panel roster could not be read, so no panel "
                          "squad can be attributed to anybody")
        return by_code, meta
    if not verified:
        meta["reason"] = (
            "no panel member carries a verified entry id, so no squad can be "
            "read. An unverified id renders a stranger's team under somebody's "
            "name, so none is guessed."
        )
        return by_code, meta
    if "fact_manager_pick" not in present:
        meta["unknown_people"] = sorted(p["person"] for p in verified)
        meta["reason"] = (
            "fact_manager_pick is not in this warehouse, so no panel squad has "
            "been crawled. This is an unread squad, not an empty one."
        )
        return by_code, meta

    ids = sorted({int(p["entry_id"]) for p in verified})
    rows = q(
        wh,
        "SELECT entry_id, gw, code, multiplier, is_captain "
        "FROM sem_manager_picks(?) WHERE season = ? AND entry_id IN ("
        + ", ".join("?" for _ in ids) + ")",
        (moment, SEASON_DEFAULT, *ids),
    )
    # Newest crawled gameweek PER ENTRY. One panel member's squad being a week
    # fresher than another's is normal, and taking a single board-wide max
    # would silently drop the stale one to zero picks.
    latest: dict[int, int] = {}
    for r in rows.to_dict("records"):
        entry, gw = _i(r["entry_id"]), _i(r["gw"])
        if entry is None or gw is None:
            continue
        latest[entry] = max(latest.get(entry, gw), gw)
    people_by_entry: dict[int, list[dict[str, Any]]] = {}
    for p in verified:
        people_by_entry.setdefault(int(p["entry_id"]), []).append(p)

    deadlines = _deadlines(wh, moment) if latest else {}
    for r in rows.to_dict("records"):
        entry, gw, code = _i(r["entry_id"]), _i(r["gw"]), _i(r["code"])
        if entry is None or code is None or gw != latest.get(entry):
            continue
        mult = _i(r["multiplier"])
        cap = bool(r["is_captain"])
        role = "captain" if cap else (
            "bench" if mult == 0 else "start" if mult is not None else None
        )
        for person in people_by_entry.get(entry, []):
            by_code.setdefault(code, []).append({
                "person": person["person"],
                "entry_id": entry,
                "multiplier": mult,
                "role": role,
                "gw": gw,
                "as_of": deadlines.get(gw),
            })
    for holdings in by_code.values():
        holdings.sort(key=lambda h: h["person"])

    meta["known"] = len(latest)
    meta["gw"] = max(latest.values()) if latest else None
    meta["unknown_people"] = sorted(
        p["person"] for p in verified if int(p["entry_id"]) not in latest
    )
    # Counted, not assumed. "No transfers are stored" is a statement about the
    # table and it has to be true of THIS warehouse at THIS instant, not a
    # remembered fact about the one it was written against.
    n_transfers = None
    if "fact_manager_transfer" in present:
        seen = q(
            wh,
            "SELECT count(*) AS n FROM sem_manager_transfers(?) WHERE season = ? "
            "AND entry_id IN (" + ", ".join("?" for _ in ids) + ")",
            (moment, SEASON_DEFAULT, *ids),
        )
        n_transfers = _i(seen.iloc[0]["n"]) if not seen.empty else None
    meta["reason"] = _panel_squads_reason(meta, n_transfers)
    return by_code, meta


def _panel_squads_reason(meta: dict[str, Any], n_transfers: int | None) -> str:
    """What is known about the panel's teams, in words a reader can act on.

    Every clause here exists to stop one specific misreading: that a zero is a
    measurement. It names how many squads were read, at which gameweek, who is
    missing, and that no transfer has ever been stored -- so "he owns him" is
    understood as "he owned him at that deadline", not "he owns him now".
    """
    parts = [
        f"{meta['known']} of {meta['with_entry']} panel members with a verified "
        f"entry id have a crawled squad"
        + (f" (GW{meta['gw']})" if meta["gw"] is not None else "")
        + "."
    ]
    if meta["unknown_people"]:
        parts.append(
            f"No picks are stored for {', '.join(meta['unknown_people'])}: their "
            f"teams are UNREAD, which is not the same as not owning him."
        )
    if meta["no_entry_people"]:
        parts.append(
            f"{', '.join(meta['no_entry_people'])} "
            f"{'have' if len(meta['no_entry_people']) > 1 else 'has'} no "
            f"verified entry id at all, so no team of theirs can be read."
        )
    if n_transfers == 0:
        parts.append(
            "No transfer is stored for any of them, so every holding here is "
            "the squad as it LOCKED at that deadline, not as it stands now."
        )
    elif n_transfers:
        parts.append(
            f"{n_transfers} transfers are stored across the panel; a holding "
            f"is still the squad as it locked at that deadline."
        )
    return " ".join(parts)
