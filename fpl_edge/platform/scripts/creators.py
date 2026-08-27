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
_PANEL_TABLE = "dim_panel_member"


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
          url: str | None) -> dict[str, Any]:
    quote = _s(call.get("quote"))
    start_s = index.find(quote)
    return {
        "code": _resolve_code(resolver, str(call.get("player") or "")),
        "name": str(call.get("player") or "").strip() or "(unnamed)",
        "conviction": str(call.get("conviction") or "medium"),
        "quote": quote,
        "start_s": _f(start_s, 2),
        "deep_link": deep_link(url, start_s),
    }


def _take(analysis: dict[str, Any] | None, model: str | None, resolver,
          index: TranscriptIndex, url: str | None) -> dict[str, Any] | None:
    if analysis is None:
        return None
    bullets = [str(b) for b in (analysis.get("summary") or []) if str(b).strip()]
    take: dict[str, Any] = {
        "summary": "\n".join(bullets),
        "summary_bullets": bullets,
        "model": str(model or "unknown"),
    }
    for key, source in _CALL_BUCKETS:
        take[key] = [
            _call(c, resolver, index, url)
            for c in (analysis.get(source) or []) if isinstance(c, dict)
        ]
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
    rows = q(
        wh,
        f"SELECT display_name, entry_id, id_source_url, id_verified_utc, "
        f"verified_entry_name FROM {_PANEL_TABLE} WHERE entry_id IS NOT NULL",
    )
    out: dict[str, dict[str, Any]] = {}
    for r in rows.to_dict("records"):
        entry_id = _i(r.get("entry_id"))
        if entry_id is None:
            continue
        verified_at = _s(r.get("id_verified_utc"))
        out[str(r["display_name"])] = {
            "entry_id": entry_id,
            "name": _s(r.get("verified_entry_name")) or str(r["display_name"]),
            "verified": verified_at is not None,
            "source_url": _s(r.get("id_source_url")),
        }
    return out, (
        f"no row in {_PANEL_TABLE} carries a verified entry id for this creator"
    )


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

_CALL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "conviction", "quote", "start_s", "deep_link"],
    "properties": {
        # null when the spoken name does not resolve to exactly one stable
        # player code. The name is always the creator's own words.
        "code": {"type": ["integer", "null"]},
        "name": {"type": "string"},
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

_ENTRY = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": ["entry_id", "name", "verified"],
    "properties": {
        "entry_id": {"type": "integer"},
        "name": {"type": "string"},
        "verified": {"type": "boolean"},
        "source_url": {"type": ["string", "null"]},
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

_CONSENSUS = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "buy", "sell", "captain", "net"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
        "pos": {"type": ["string", "null"]},
        "team": {"type": ["string", "null"]},
        "price": {"type": ["number", "null"]},
        "own_pct": {"type": ["number", "null"]},
        "buy": _SIDE,
        "sell": _SIDE,
        "captain": _SIDE,
        "net": {"type": "integer"},
    },
}

BOARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "days": {"type": "integer", "minimum": 1, "maximum": 365, "default": 30},
        "gw": {"type": ["integer", "null"], "minimum": 1, "maximum": 38,
               "default": None},
    },
}

# `required` keeps this disjoint from the registry's {empty, reason} branch:
# an honest empty has no `creators`, a real board always does.
BOARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["as_of", "window_days", "gw", "creators", "consensus"],
    "properties": {
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        "gw": {"type": ["integer", "null"]},
        "gw_reason": {"type": ["string", "null"]},
        "creators": {"type": "array", "items": _CREATOR},
        "consensus": {"type": "array", "items": _CONSENSUS},
        "record_note": {"type": "string"},
    },
}

_CLAIM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["code", "name", "action", "confidence", "quote", "start_s",
                 "deep_link", "extractor"],
    "properties": {
        "code": {"type": "integer"},
        "name": {"type": "string"},
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
    },
}

DETAIL_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["creator", "as_of", "entry", "squad", "transfers", "items"],
    "properties": {
        "creator": {"type": "string"},
        "as_of": {"type": "string"},
        "window_days": {"type": "integer"},
        "entry": _ENTRY,
        "entry_reason": {"type": ["string", "null"]},
        "squad": {"type": ["array", "null"], "items": _SQUAD_PICK},
        "squad_reason": {"type": ["string", "null"]},
        "transfers": {"type": "array", "items": _TRANSFER},
        "transfers_reason": {"type": ["string", "null"]},
        "items": {"type": "array", "items": _ITEM},
        "record": _RECORD,
    },
}


# ---------------------------------------------------------------------------
# creator_board

def creator_board(wh, *, days: int = 30, gw: int | None = None) -> dict[str, Any]:
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
                                                     _PANEL_TABLE))
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
    gw_reason: str | None = None
    if gw is None:
        gw = next_gw(wh, SEASON_DEFAULT, moment)
        if gw is None and not claims.empty:
            gw = _i(claims["gameweek"].max())
            gw_reason = ("no future deadline is on file; falling back to the "
                         "latest gameweek any visible claim names")
        if gw is None:
            gw_reason = ("no gameweek could be determined: dim_event has no "
                         "future deadline and no claim is visible in the window")

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

    # Publications, not stored rows: the same video under `watch?v=` and
    # `youtube.com/live/` is one thing this creator published.
    counts_all = items.groupby("creator")["_canon"].nunique().to_dict()
    counts_win = in_window.groupby("creator")["_canon"].nunique().to_dict()
    claim_counts = (claims.groupby("creator").size().to_dict()
                    if not claims.empty else {})

    names = sorted(set(by_creator_sources) | set(counts_all) | set(claim_counts))
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
        take = _take(analysis, model, resolver, index, url)

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

    return {
        "as_of": moment.isoformat(),
        "window_days": int(days),
        "gw": gw,
        "gw_reason": gw_reason,
        "creators": creators,
        "consensus": _consensus(wh, claims, gw, moment),
        "record_note": _record_note(weights),
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


def _consensus(wh, claims, gw: int | None, moment: dt.datetime) -> list[dict[str, Any]]:
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
        out.append({
            "code": code,
            "name": str(name),
            "pos": POSITION_NAME.get(_i(info.get("position")) or 0),
            "team": _s(info.get("team")),
            "price": _f(info.get("price"), 1),
            "own_pct": _f(info.get("selected_by_pct"), 1),
            "buy": sides["buy"],
            "sell": sides["sell"],
            "captain": sides["captain"],
            "net": sides["buy"]["n"] - sides["sell"]["n"],
        })
    out.sort(key=lambda r: (-r["net"], -r["captain"]["n"], r["name"]))
    return out


# ---------------------------------------------------------------------------
# creator_detail

def creator_detail(wh, *, creator: str, days: int = 60,
                   limit: int = 40) -> dict[str, Any]:
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
        take = _take(analysis, model, resolver, index, url)

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
                row = _claim_row(c, index, url)
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

    squad, squad_reason = _squad(wh, entry, present, moment)
    transfers, transfers_reason = _transfers(wh, entry, present, moment)

    return {
        "creator": creator,
        "as_of": moment.isoformat(),
        "window_days": int(days),
        "entry": entry,
        "entry_reason": None if entry else entry_reason,
        "squad": squad,
        "squad_reason": squad_reason,
        "transfers": transfers,
        "transfers_reason": transfers_reason,
        "items": out_items,
        "record": _record(weights.get(creator)),
    }


def _claim_row(c: dict[str, Any], index: TranscriptIndex,
               url: str | None) -> dict[str, Any]:
    """One claim, with its verbatim evidence and a link to the moment.

    The quote for an ``llm:`` claim is the verbatim fragment the extractor
    stored after ``| quote: ``; for a ``cue`` claim the rationale IS the
    keyword window lifted from the item, so it is the quote. Both are untrusted
    third-party prose.
    """
    rationale = _s(c.get("rationale")) or ""
    quote = rationale.split("| quote: ", 1)[1] if "| quote: " in rationale else rationale
    start_s = index.find(quote)
    return {
        "code": int(c["player_code"]),
        "name": str(c.get("player_name") or c.get("surface_form") or ""),
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
           moment: dt.datetime) -> tuple[list[dict[str, Any]] | None, str | None]:
    """The creator's locked squad, through ``sem_manager_picks(as_of)``."""
    if entry is None:
        return None, ("no verified entry id for this creator, so there is no "
                      "squad to read; see entry_reason")
    if "fact_manager_pick" not in present:
        return None, "fact_manager_pick is not in this warehouse"
    rows = q(
        wh,
        "SELECT gw, code, web_name, multiplier, is_captain FROM sem_manager_picks(?) "
        "WHERE entry_id = ? AND season = ? AND gw = ("
        "  SELECT max(gw) FROM sem_manager_picks(?) WHERE entry_id = ? AND season = ?)",
        (moment, entry["entry_id"], SEASON_DEFAULT,
         moment, entry["entry_id"], SEASON_DEFAULT),
    )
    if rows.empty:
        return None, (
            "picks become public only at a gameweek's deadline and none is "
            "stored for this entry yet"
        )
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
    gw = _i(rows.iloc[0]["gw"])
    return squad, f"locked GW{gw} squad, as published at that deadline"


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
