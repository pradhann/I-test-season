"""creator_board / creator_detail — the Creators tab's data path.

Shape is fixed by docs/platform/CREATOR_PANEL_CONTRACT.md; the UI is built
against it in parallel. Deviations from that document are listed at the bottom
of this docstring, none of them silent.

Four rules do all the work here, and each one exists because breaking it was
observed to produce a plausible-looking lie:

1. **Nothing is invented.** Every nullable field in the payload has a paired
   ``*_reason`` string written for a human. ``content_analysis`` currently holds
   two rows against 594 items, so *most* creators have no summarised take today
   and a backfill is running concurrently. The panel must therefore be correct
   at 2 analyses and at 200, and a creator with no analysis renders
   ``take: null`` plus ``take_reason`` naming what is actually on file for their
   latest item ("show notes only, no transcript captured"). It never renders a
   blank card, and it never renders 0.0 for "unknown".

2. **Deep links are built here, not in the browser.** Only this side knows the
   platform's URL grammar and only this side can see ``transcript_segment``.
   A quote is located in the transcript by normalised substring search and the
   segment holding it supplies ``start_s``; the link is then
   ``watch?v=<id>&t=<n>s``. With no timestamp, ``start_s`` is null and
   ``deep_link`` is the item URL unchanged -- never a guessed offset.

3. **YouTube URLs are canonicalised on the video id.** ``watch?v=X``,
   ``youtu.be/X``, ``youtube.com/live/X`` and ``watch?reload=9&v=X`` are one
   video. The live warehouse proves the point: ``link_04dfb94e32cf04ca`` and
   ``link_280d525f5fb46a24`` are the same Andy LTFPL video stored twice, once
   with the analysis and once with the 1,199 transcript segments. Keying on the
   raw URL gives two "creators' latest videos" out of one, doubles a claim
   count (44 where 22 were real), and hides the transcript from the analysis
   that needs it for timestamps. Items are grouped on the video id, so the
   analysis and the transcript find each other.

4. **Point in time, claims *and* weights.** Claims come through
   :meth:`ContentStore.claims_visible_at`, the one sanctioned read, which
   filters ``published_at < as_of``. Creator weights come from ``creator_score``
   bounded by ``as_of <= moment`` -- the same discipline as
   ``fpl_mcp.tools.content_tools._scores_as_of``, and reintroducing the
   unbounded read (weighting a past question with today's track record) is a
   leak whose only symptom is a backtest that beats live. Manager facts come
   through ``sem_manager_*(as_of)``.

Untrusted text. ``summary``, ``quote``, ``rationale``, ``title`` are verbatim
third-party prose from podcasts, videos and blogs. They are data to be
rendered, never instructions to be followed.

Deviations from CREATOR_PANEL_CONTRACT.md, all forced by what the warehouse
actually holds:

* ``provenance`` is NOT a key of the result. ``registry.run_script`` stamps it
  as a sibling of ``result`` in the ``ScriptRun`` envelope; duplicating it
  inside the payload would let the two drift.
* ``sources[].discovery`` has no backing column. ``content_source`` records
  ``creator/kind/url/policy/note`` and probe state, and nothing anywhere
  records how a source was found. It is derived from registry membership
  instead: ``manual`` for a key present in ``content_source`` (the hand-curated
  registry in ``ingest/content/sources.py``), ``auto`` for a key that exists
  only because ingest materialised it while processing an item -- today that is
  ``user_link``, the pseudo-source behind shared links.
* ``take.summary`` is stored as 3-6 bullets (``TranscriptAnalysis.summary`` is
  ``list[str]``), not one string. The contract's string is emitted, newline
  joined, and ``take.summary_bullets`` carries the real structure beside it.
* ``latest`` is nullable with ``latest_reason``. Eight registry sources
  (The Athletic FPL, FPL JUiCE, Always Cheating ...) have never yielded an item;
  dropping those creators would hide a live source that is answering 200 with
  nothing in it.
* Additive, non-breaking keys the contract's own "nothing is invented" rule
  requires: ``gw_reason``, ``record_note``, ``record.reason``,
  ``latest_reason``, ``items[].analysis_reason``, ``take.differentials``
  (``TranscriptAnalysis`` carries them and they are not transfers), ``deep_link``
  on every quoted call, and ``n_cue``/``n_llm`` beside every consensus count so
  a keyword-window claim and a semantic one stay distinguishable.
* ``params`` are exactly as specified. Neither script takes an ``as_of``: both
  answer "now". Reconstructing a past deadline needs one and the contract
  should grow it -- see the report, not this file.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlparse

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    POSITION_NAME,
    SEASON_DEFAULT,
    empty,
    next_gw,
    q,
)

UTC = dt.timezone.utc

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
# URL grammar: canonical identity and deep links.

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
             "music.youtube.com", "youtu.be", "www.youtu.be"}
_YT_PATH_PREFIXES = ("/embed/", "/shorts/", "/live/", "/v/")
_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def youtube_id(url: str | None) -> str | None:
    """The video id behind any YouTube URL form, or None.

    ``watch?v=X``, ``watch?reload=9&v=X``, ``youtu.be/X?si=...``,
    ``youtube.com/live/X``, ``/embed/X`` and ``/shorts/X`` are all one video.
    Both of the real duplicate pairs in the warehouse differ only in query
    junk, which is exactly the shape a naive URL key fails on.
    """
    if not url:
        return None
    try:
        parsed = urlparse(str(url))
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    if host not in _YT_HOSTS:
        return None
    if host.endswith("youtu.be"):
        candidate = parsed.path.lstrip("/").split("/")[0]
    elif parsed.path == "/watch":
        candidate = (parse_qs(parsed.query).get("v") or [""])[0]
    else:
        candidate = ""
        for prefix in _YT_PATH_PREFIXES:
            if parsed.path.startswith(prefix):
                candidate = parsed.path[len(prefix):].split("/")[0]
                break
    return candidate if candidate and _YT_ID.match(candidate) else None


def canonical_key(url: str | None, item_id: str) -> str:
    """One key per underlying publication, not per stored row.

    Falls back to the URL, then to the item id: a source with no URL grammar we
    understand is still one item, and two of them must not collapse together.
    """
    vid = youtube_id(url)
    if vid:
        return f"yt:{vid}"
    return f"url:{url}" if url else f"item:{item_id}"


def deep_link(url: str | None, start_s: float | None) -> str | None:
    """A link that lands on the moment, when the platform has a grammar for it.

    YouTube gets ``&t=NNNs`` against the canonical watch URL. Everything else
    gets the item URL untouched: podcast ``url`` values here are episode pages,
    not media files, and inventing a ``#t=`` fragment for a page that ignores it
    would produce a link that silently lands at the top. ``start_s`` is still
    reported so the UI can print the offset beside a plain link.
    """
    if not url:
        return None
    vid = youtube_id(url)
    if vid is None or start_s is None:
        return str(url)
    # Floor, never round. Landing a fraction of a second early replays the
    # start of the sentence; rounding up can start the viewer after the words
    # they clicked to hear, which reads as a broken link.
    return f"https://www.youtube.com/watch?v={vid}&t={max(int(float(start_s)), 0)}s"


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
               wilson_lo95, weight, as_of
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
    bullets = [str(b) for b in (analysis.get("summary") or []) if str(b).strip()]
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
        "description": "the video description / show notes only -- YouTube "
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
            "reason": _s(r.get("entry_reason")),
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
            "reason": _s(r.get("entry_reason")),
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


def _gw_calendar(wh, moment: dt.datetime) -> list[tuple[Any, int]]:
    """``[(deadline, gw), ...]`` ascending -- the rule the INGESTER already uses.

    ``ingest/content/claims.py::GwCalendar.next_after`` answers "which gameweek
    was this published before the deadline of", and it is what stamps
    ``content_claim.gameweek`` for every call the model did not date itself
    (``gw_inferred = true``, 122 rows in the live warehouse). A ``watch`` call
    never becomes a claim, so nothing stamps it -- and reading one back needs
    the SAME rule, or a panel filtered to GW2 would show a dated buy beside an
    undated watch from the same video and disagree with itself about which
    gameweek that video was about.
    """
    import pandas as pd

    out: list[tuple[Any, int]] = []
    for r in _events(wh, moment).to_dict("records"):
        gw = _i(r["gw"])
        when = pd.to_datetime(r["deadline_utc"], utc=True, errors="coerce")
        if gw is None or pd.isna(when):
            continue
        out.append((when, gw))
    out.sort(key=lambda p: p[0])
    return out


def _gw_after(calendar: list[tuple[Any, int]], when) -> int | None:
    """The first gameweek whose deadline is strictly after ``when``. None if none."""
    import pandas as pd

    if not calendar or when is None:
        return None
    stamp = pd.to_datetime(when, utc=True, errors="coerce")
    if pd.isna(stamp):
        return None
    for deadline, gw in calendar:
        if deadline > stamp:
            return gw
    return None


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
                f"{latest} -- the latest gameweek any visible claim names. "
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


# ---------------------------------------------------------------------------
# Schemas.

_SOURCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["key", "kind", "url", "last_item_at", "last_status", "discovery"],
    "properties": {
        "key": {"type": "string"},
        "kind": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "last_item_at": {"type": ["string", "null"]},
        "last_status": {"type": ["integer", "null"]},
        "discovery": {"type": "string", "enum": ["auto", "manual"]},
        "policy": {"type": ["string", "null"]},
        "last_error": {"type": ["string", "null"]},
    },
}

#: The read-side naming contract shared by every surface that serves a player
#: name a creator spoke. `name` is always the verbatim spoken string;
#: `display_name` is the canonical `web_name` when the strict resolver found
#: exactly one player (`resolved: true`), and the raw string otherwise
#: (`resolved: false` -- never guessed). `disambiguator` is non-null exactly
#: when the served web_name belongs to two or more players in the pool
#: ("C. Palmer (CHE)"), so a bare surname can never silently mean two people.
_NAMING_PROPS: dict[str, Any] = {
    "display_name": {"type": "string"},
    "resolved": {"type": "boolean"},
    "disambiguator": {"type": ["string", "null"]},
}

_CALL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "conviction", "quote", "start_s", "deep_link"],
    "properties": {
        # null when the spoken name does not resolve to exactly one stable
        # player code. The name is always the creator's own words.
        "code": {"type": ["integer", "null"]},
        "name": {"type": "string"},
        **_NAMING_PROPS,
        "conviction": {"type": "string"},
        "quote": {"type": ["string", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
    },
}

_CHIP = {
    "type": "object",
    "additionalProperties": False,
    "required": ["chip", "stance", "quote", "horizon_gw"],
    "properties": {
        "chip": {"type": "string"},
        "stance": {"type": "string"},
        "quote": {"type": ["string", "null"]},
        "horizon_gw": {"type": ["integer", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
    },
}

#: A watch call: the same shape as a recommendation plus WHERE it was raised,
#: because "watch, mentioned while discussing transfers in" says more than
#: "watch" alone.
_WATCH = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "conviction", "quote", "start_s", "deep_link", "raised_in"],
    "properties": {
        **_CALL["properties"],
        "raised_in": {"enum": ["transfers_in", "transfers_out", "captain",
                               "differentials"]},
    },
}

_TAKE = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["summary", "model", "transfers_in", "transfers_out",
                 "captain", "chips"],
    "properties": {
        "summary": {"type": "string"},
        "summary_bullets": {"type": "array", "items": {"type": "string"}},
        "model": {"type": "string"},
        "transfers_in": {"type": "array", "items": _CALL},
        "transfers_out": {"type": "array", "items": _CALL},
        "captain": {"type": "array", "items": _CALL},
        "differentials": {"type": "array", "items": _CALL},
        # Observations, not recommendations. Separated from the buy/sell lists
        # because the model stores a "watch" in whichever list it arose in, and
        # showing one as a transfer attributes a call nobody made.
        "watching": {"type": "array", "items": _WATCH},
        "chips": {"type": "array", "items": _CHIP},
    },
}

_LATEST = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["item_id", "title", "url", "published_at", "kind", "text_source"],
    "properties": {
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "published_at": {"type": "string"},
        "kind": {"type": "string"},
        "text_source": {"type": "string"},
    },
}

_RECORD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scored", "hits", "hit_rate", "wilson_lo95", "weight", "earned"],
    "properties": {
        "scored": {"type": ["integer", "null"]},
        "hits": {"type": ["integer", "null"]},
        "hit_rate": {"type": ["number", "null"]},
        "wilson_lo95": {"type": ["number", "null"]},
        "weight": {"type": ["number", "null"]},
        "earned": {"type": "boolean"},
        "reason": {"type": ["string", "null"]},
    },
}

#: A show's FPL identity. `people` is the truthful field: a show is a place
#: several managers talk, and the FPL Wire alone has four hosts with four
#: different teams. The flat entry_id/name/verified are populated ONLY when the
#: show has exactly one verified person -- otherwise they are null and the
#: caller must name a person, because "the Wire's team" does not exist.
_PERSON = {
    "type": "object",
    "additionalProperties": False,
    "required": ["person", "entry_id", "verified"],
    "properties": {
        "person": {"type": "string"},
        "entry_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
        "verified": {"type": "boolean"},
        "source_url": {"type": ["string", "null"]},
        "reason": {"type": ["string", "null"]},
    },
}

_ENTRY = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["entry_id", "name", "verified"],
    "properties": {
        "entry_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
        "verified": {"type": "boolean"},
        "source_url": {"type": ["string", "null"]},
        "people": {"type": "array", "items": _PERSON},
    },
}

_CREATOR = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "kinds", "sources", "n_items", "n_items_window",
                 "n_claims_window", "last_item_at", "latest", "take",
                 "take_reason", "record", "entry", "entry_reason"],
    "properties": {
        "creator": {"type": "string"},
        "kinds": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": _SOURCE},
        "n_items": {"type": "integer"},
        "n_items_window": {"type": "integer"},
        "n_claims_window": {"type": "integer"},
        "last_item_at": {"type": ["string", "null"]},
        "latest": _LATEST,
        "latest_reason": {"type": ["string", "null"]},
        "take": _TAKE,
        "take_reason": {"type": ["string", "null"]},
        "record": _RECORD,
        "entry": _ENTRY,
        "entry_reason": {"type": ["string", "null"]},
    },
}

_SIDE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "creators"],
    "properties": {
        "n": {"type": "integer"},
        "creators": {"type": "array", "items": {"type": "string"}},
        # Extractor stays visible: a cue claim is a keyword window, an llm
        # claim is a semantic read with a stated conviction. Averaging the two
        # into one count throws away the difference the reader needs.
        "n_cue": {"type": "integer"},
        "n_llm": {"type": "integer"},
    },
}

#: Is he in the OWNER's squad, and in what role. `in_squad` is nullable and a
#: null is not a false: when the squad cannot be read, "he is not in your team"
#: is a claim this script has no evidence for, and printing `false` for all 614
#: players is the exact fabrication the no-invention rule forbids. The reason
#: is board-level, in `mine_reason`, because it is one fact about one read.
_MINE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["in_squad", "multiplier", "role"],
    "properties": {
        "in_squad": {"type": ["boolean", "null"]},
        "multiplier": {"type": ["integer", "null"]},
        "role": {"type": ["string", "null"]},
        # read | derived | null. A pre-deadline picks payload carries no
        # multiplier at all, so `_squad_state` derives it from the role using
        # the scoring rule. Which of the two it is travels with the number.
        "source": {"type": ["string", "null"]},
    },
}

#: The DID channel at board level: how many panel members ACTUALLY hold him,
#: of how many whose squads are known. `of` is the denominator that stops `n:
#: 0` from reading as "nobody on the panel owns him" -- see `panel_squads` for
#: who is missing and why.
_PANEL_OWNED = {
    "type": "object",
    "additionalProperties": False,
    "required": ["n", "of", "people"],
    "properties": {
        "n": {"type": "integer"},
        "of": {"type": "integer"},
        "people": {"type": "array", "items": {"type": "string"}},
    },
}

_CONSENSUS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "resolved", "disambiguator", "buy", "sell",
                 "captain", "net", "mine", "panel_owned"],
    "properties": {
        "code": {"type": "integer"},
        # `name` here is already the canonical web_name when the code is in
        # the pool; `resolved: false` marks the fallback to the raw claim
        # string. `disambiguator` follows the shared naming contract.
        "name": {"type": "string"},
        "resolved": {"type": "boolean"},
        "disambiguator": {"type": ["string", "null"]},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},
        "buy": _SIDE,
        "sell": _SIDE,
        "captain": _SIDE,
        "net": {"type": "integer"},
        "mine": _MINE,
        "panel_owned": _PANEL_OWNED,
    },
}

#: What is actually known about the panel's squads, stated once for the whole
#: board rather than repeated on every row. `known < with_entry` is the normal
#: state and the difference is named, person by person.
_PANEL_SQUADS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["panel_size", "with_entry", "known", "reason"],
    "properties": {
        "panel_size": {"type": "integer"},
        "with_entry": {"type": "integer"},
        "known": {"type": "integer"},
        "gw": {"type": ["integer", "null"]},
        "unknown_people": {"type": "array", "items": {"type": "string"}},
        "no_entry_people": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
}

BOARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
        "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38,
               "default": None},
        # Which creators the board is ABOUT. The corpus holds 30-odd sources
        # because ingest casts wide; the owner follows a named panel of 16
        # people across 7 shows. Showing all 30 read as "30 tracked creators",
        # which is not what tracking means -- it is what ingesting means.
        # `panel` is the default and the honest one; `all` stays reachable so
        # the wider corpus is never hidden, only un-defaulted.
        "scope": {"enum": ["panel", "all"], "default": "panel"},
        # Reading the OWNER's squad is the one thing on this board that leaves
        # the warehouse: `_squad_state` tries the private API, then public
        # picks, then the manually entered 15. It degrades to unreadable and
        # never crashes, but a caller that does not want the round trip (a
        # test, a batch render) can turn it off and gets `in_squad: null` with
        # a reason rather than a silent `false`.
        "mine": {"type": "boolean", "default": True},
    },
}

# `required` keeps this disjoint from the registry's {empty, reason} branch:
# an honest empty has no `creators`, a real board always does.
BOARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["as_of", "window_days", "gw", "gw_reason", "creators",
                 "consensus"],
    "properties": {
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        "gw": {"type": ["integer", "null"]},
        # ALWAYS a string, never null. "GW2" alone does not say whether it was
        # requested, deduced from the calendar, or scraped off the claims
        # because the calendar was unreadable, and a reader trusts those three
        # differently. `player_chatter.gw_reason` carries the identical rule.
        "gw_reason": {"type": "string"},
        # Which creators this board is about, and which the scope excluded.
        # Named explicitly so the page can say "16 people across 7 shows"
        # rather than a count of whatever ingest happened to reach.
        "scope": {
            "type": "object", "additionalProperties": False,
            "required": ["applied"],
            "properties": {
                "applied": {"enum": ["panel", "all"]},
                "shows": {"type": "array", "items": {"type": "string"}},
                "excluded": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": ["string", "null"]},
            },
        },
        "creators": {"type": "array", "items": _CREATOR},
        "consensus": {"type": "array", "items": _CONSENSUS},
        "record_note": {"type": "string"},
        # Why every `mine.in_squad` is null, when it is. Always populated when
        # the squad could not be read; null when it could.
        "mine_reason": {"type": ["string", "null"]},
        "panel_squads": _PANEL_SQUADS,
    },
}

_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "display_name", "resolved", "disambiguator",
                 "action", "confidence", "quote", "start_s",
                 "deep_link", "extractor"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        **_NAMING_PROPS,
        "action": {"type": "string"},
        "confidence": {"type": ["number", "null"]},
        "quote": {"type": ["string", "null"]},
        "start_s": {"type": ["number", "null"]},
        "deep_link": {"type": ["string", "null"]},
        "extractor": {"type": "string"},
        "gameweek": {"type": ["integer", "null"]},
        "published_at": {"type": ["string", "null"]},
    },
}

_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["item_id", "title", "url", "published_at", "kind",
                 "text_source", "analysis", "claims"],
    "properties": {
        "item_id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": ["string", "null"]},
        "published_at": {"type": "string"},
        "kind": {"type": "string"},
        "text_source": {"type": "string"},
        "analysis": _TAKE,
        "analysis_reason": {"type": ["string", "null"]},
        "claims": {"type": "array", "items": _CLAIM},
    },
}

_SQUAD_PICK = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "pos", "price", "multiplier", "is_captain"],
    "properties": {
        "code": {"type": ["integer", "null"]},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "multiplier": {"type": ["integer", "null"]},
        "is_captain": {"type": "boolean"},
    },
}

_TRANSFER = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gw", "in_name", "in_code", "out_name", "out_code", "time_utc"],
    "properties": {
        "gw": {"type": "integer"},
        "in_name": {"type": ["string", "null"]},
        "in_code": {"type": ["integer", "null"]},
        "out_name": {"type": ["string", "null"]},
        "out_code": {"type": ["integer", "null"]},
        "time_utc": {"type": ["string", "null"]},
    },
}

DETAIL_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator"],
    "properties": {
        "creator": {"type": "string", "minLength": 1},
        "days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 60},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 40},
        # Which gameweek's SQUAD to show. `days`/`limit` bound the item list and
        # are a different axis entirely: a video published five weeks ago about
        # GW2 is still what this creator's GW2 team should be read beside.
        # Null defaults to the newest squad that has actually locked -- see
        # `_squad_gw` for why that is not literally the next gameweek.
        "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38,
               "default": None},
    },
}

DETAIL_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "as_of", "entry", "squad", "squad_gw", "squad_gws",
                 "transfers", "items"],
    "properties": {
        "creator": {"type": "string"},
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        "entry": _ENTRY,
        "entry_reason": {"type": ["string", "null"]},
        "squad": {"type": ["array", "null"], "items": _SQUAD_PICK},
        "squad_reason": {"type": ["string", "null"]},
        # The gameweek `squad` IS, and the gameweeks a squad exists for. Both
        # are required: a squad with no gameweek on it is a team from an
        # unnamed week, which is what this pair exists to stop rendering.
        "squad_gw": {"type": ["integer", "null"]},
        "squad_gws": {"type": "array", "items": {"type": "integer"}},
        "transfers": {"type": "array", "items": _TRANSFER},
        "transfers_reason": {"type": ["string", "null"]},
        "items": {"type": "array", "items": _ITEM},
        "record": _RECORD,
    },
}


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

    Shared with the ownership panel on purpose -- ``_squad_state`` is the one
    place that knows the private-API -> public-picks -> manual ladder and the
    rule for deriving a multiplier from a role when a pre-deadline payload
    carries none. A second implementation here would drift from it, and the
    symptom would be two panels disagreeing about the reader's own captain.
    """
    if not enabled:
        return None, ("the squad read was disabled by the caller "
                      "(`mine: false`), so no ownership is claimed either way")
    from fpl_edge.platform.scripts.ownership import _squad_state

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
                "has yielded no item yet -- see sources[].last_status"
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
        "mine_reason": mine_reason,
        "panel_squads": panel_meta,
    }


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
            f"{_gw_list(available)} on file, so the gameweek is selectable -- "
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
        "season's first gameweek has none behind it -- every GW1 squad is an "
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
                    f". This is a gameweek filter, not silence -- switch the "
                    f"gameweek to read those."
                ), by_gw
            return [], (
                f"nothing was said about this player {scope}, but "
                f"{sum(b['n'] for b in by_gw)} statements about him sit "
                f"outside that window ({_by_gw_phrase(by_gw)}). This is the "
                f"day window, not silence -- widen `days`, or ask for a "
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
            f"stable PlayerCode, not an element_id -- the two differ and "
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
    name="creator_board",
    fn=creator_board,
    params_schema=BOARD_PARAMS,
    result_schema=BOARD_RESULT,
    title="Creators",
    description="Every tracked FPL creator: reach, their latest item, the "
                "summarised take on it, their measured track record, and the "
                "cross-creator consensus for the gameweek.",
)

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
