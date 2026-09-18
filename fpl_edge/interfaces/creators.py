"""Conversational access to the content-creator pipeline.

The heavy machinery lives in ``fpl_edge.ingest.content`` and already does the
work once and stores it: every fetched item is archived and rowed in
``content_item``, every extracted claim in ``content_claim``, resolutions in
``claim_outcome``, earned weights in ``creator_score``. Nothing here re-fetches
what the warehouse already holds — this module is the phone-sized front door:

* **Name matching.** "FPl Harry", "fplwire", "Let's Talk FPL?" all resolve to
  canonical creators via alias sets built from the source roster, so a typo'd
  creator never falls through to the PLAYER resolver and becomes a question
  about Harry Maguire.
* **Freshness.** A summary states the age of what it summarises. If a creator's
  newest stored item is older than :data:`STALE_AFTER`, the caller may refresh
  just that creator's sources (`--only`) before summarising — bounded, cached,
  and skipped when fresh.
* **Summaries are claims, not prose.** The summary of a creator is their
  extracted, player-resolved claims grouped by action, plus a chip-keyword scan
  of their raw text, plus their measured scoreboard line. That is the honest
  unit: it is checkable later, which prose is not.
* **User links.** A shared YouTube/article link is transcribed (single video,
  at the user's explicit request — the same route the user's own fpl-server
  MCP tool has always used; the bulk crawler stays robots-gated), run through
  the SAME claim extractor, persisted to the SAME tables under the
  ``user_link`` source, and the findings are committed to the reports repo.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from fpl_edge.ingest.content.identity import UNRESOLVED_CREATOR
from fpl_edge.ingest.content.link_ledger import record_link_item
from fpl_edge.ingest.content.urls import youtube_id

UTC = dt.UTC
STALE_AFTER = dt.timedelta(hours=36)
REPORTS_DIR = Path.home() / "Documents/Github/fpl-reports"

CHIP_WORDS = {
    "bench boost": "bboost", "benchboost": "bboost",
    "triple captain": "3xc", "triple cap": "3xc",
    "wildcard": "wildcard", "wild card": "wildcard",
    "free hit": "freehit", "freehit": "freehit",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def roster() -> dict[str, list[str]]:
    """Canonical creator -> source keys, from the configured source list."""
    from fpl_edge.ingest.content import sources

    out: dict[str, list[str]] = {}
    for s in sources.fetchable():
        out.setdefault(s.creator, []).append(s.key)
    return out


def _aliases(creator: str, keys: list[str]) -> set[str]:
    """Normalised forms a human might type for this creator."""
    out = {_norm(creator)}
    stripped = re.sub(r"^the\s+", "", creator, flags=re.IGNORECASE)
    out.add(_norm(stripped))
    for k in keys:
        # yt_fplharry -> fplharry ; pod_fplwire -> fplwire
        out.add(_norm(k.split("_", 1)[-1]))
    return {a for a in out if len(a) >= 5}  # "fpl" alone would match everything


def match_creators(text: str) -> list[str]:
    """Every canonical creator the text plausibly names, in roster order."""
    hay = _norm(text)
    hits = []
    for creator, keys in roster().items():
        if any(alias in hay for alias in _aliases(creator, keys)):
            hits.append(creator)
    return hits


@dataclass
class CreatorSummary:
    creator: str
    newest_item: dt.datetime | None
    n_items: int
    refreshed: bool
    claims: list[dict] = field(default_factory=list)
    chip_mentions: list[tuple[str, str, str]] = field(default_factory=list)  # (chip, title, snippet)
    scoreboard: str = ""

    def render(self) -> str:
        lines = [f"— {self.creator} —"]
        if self.newest_item is None:
            lines.append("Nothing stored yet from this creator.")
            return "\n".join(lines)
        age_h = (dt.datetime.now(UTC) - self.newest_item).total_seconds() / 3600
        fresh = " (refreshed just now)" if self.refreshed else ""
        lines.append(f"{self.n_items} items stored, newest {age_h:.0f}h old{fresh}.")
        if self.claims:
            lines.append("Extracted claims (newest first):")
            for c in self.claims[:8]:
                lines.append(f"  • GW{c['gw']} {c['action']}: {c['player']} "
                             f"(conf {c['conf']:.0%})")
        else:
            lines.append("No player claims extracted from their recent items — "
                         "their text may be description-only until the nightly "
                         "job transcribes more.")
        if self.chip_mentions:
            lines.append("Chip talk:")
            for chip, title, snippet in self.chip_mentions[:3]:
                lines.append(f"  • {chip}: \"{snippet}\" ({title[:40]})")
        lines.append(self.scoreboard)
        return "\n".join(lines)


def _refresh(source_keys: list[str], *, timeout_s: int = 150) -> bool:
    """Fetch just these sources through the real pipeline. Best-effort."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "fpl_edge.ingest.content.pipeline", "ingest",
             "--only", ",".join(source_keys), "--backfill-days", "7"],
            capture_output=True, text=True, timeout=timeout_s,
        )
        return proc.returncode == 0
    except Exception:  # noqa: BLE001 - stale data with an honest age beats a crash
        return False


def summarize_creator(wh, creator: str, *, allow_refresh: bool = True) -> CreatorSummary:
    keys = roster().get(creator, [])
    newest = wh.sql(
        "SELECT max(published_at) AS t, count(*) AS n FROM content_item "
        "WHERE creator = ?", [creator],
    )
    newest_t = newest.iloc[0]["t"]
    refreshed = False
    if allow_refresh and keys and (
        newest_t is None
        or (dt.datetime.now(UTC) - newest_t.to_pydatetime()) > STALE_AFTER
    ):
        # Release our lease so the pipeline subprocess can take the writer.
        release = getattr(wh, "release", None)
        if release:
            release()
        refreshed = _refresh(keys)
        newest = wh.sql(
            "SELECT max(published_at) AS t, count(*) AS n FROM content_item "
            "WHERE creator = ?", [creator],
        )
        newest_t = newest.iloc[0]["t"]

    import pandas as pd

    def _int0(v) -> int:
        # SUM/COUNT over zero rows can be NaN, and NaN is truthy, so `or 0`
        # never fires and int(NaN) raises.
        return 0 if v is None or pd.isna(v) else int(v)

    claims = wh.sql(
        """
        -- One creator often publishes the same episode to YouTube AND a
        -- podcast feed; the same claim then rows twice. Collapse to the
        -- strongest-confidence instance per (player, action, gw).
        SELECT player_name, action, gameweek, max(confidence) AS confidence
        FROM content_claim WHERE creator = ?
        GROUP BY player_name, action, gameweek
        ORDER BY max(published_at) DESC LIMIT 12
        """,
        [creator],
    )
    items = wh.sql(
        "SELECT title, text FROM content_item WHERE creator = ? "
        "ORDER BY published_at DESC LIMIT 6", [creator],
    )
    chips = []
    for r in items.itertuples():
        low = (r.text or "").lower()
        for phrase, chip in CHIP_WORDS.items():
            i = low.find(phrase)
            if i >= 0:
                snippet = re.sub(r"\s+", " ", (r.text or "")[max(0, i - 40):i + 60]).strip()
                chips.append((chip, str(r.title), snippet))
                break

    score = wh.sql(
        """
        SELECT sum(CASE WHEN hit THEN 1 ELSE 0 END) AS hits,
               sum(CASE WHEN hit IS NOT NULL
                        AND (unscoreable IS NULL OR unscoreable = '')
                        THEN 1 ELSE 0 END) AS resolved
        FROM claim_outcome WHERE creator = ?
        """,
        [creator],
    )
    hits = _int0(score.iloc[0]["hits"])
    resolved = _int0(score.iloc[0]["resolved"])
    board = (f"Track record: {hits}/{resolved} claims correct."
             if resolved else
             "Track record: no resolved claims yet — this creator carries "
             "ZERO decision weight until gameweeks settle their calls.")

    return CreatorSummary(
        creator=creator,
        newest_item=newest_t.to_pydatetime() if newest_t is not None else None,
        n_items=_int0(newest.iloc[0]["n"]),
        refreshed=refreshed,
        claims=[{"player": r.player_name, "action": r.action,
                 "gw": int(r.gameweek), "conf": float(r.confidence)}
                for r in claims.itertuples()],
        chip_mentions=chips,
        scoreboard=board,
    )


def chip_scan(wh, *, days: int = 10) -> list[tuple[str, str, str, str]]:
    """(creator, chip, title, snippet) for recent chip talk across ALL creators."""
    since = dt.datetime.now(UTC) - dt.timedelta(days=days)
    items = wh.sql(
        "SELECT creator, title, text FROM content_item WHERE published_at >= ? "
        "ORDER BY published_at DESC", [since],
    )
    out = []
    for r in items.itertuples():
        low = (r.text or "").lower()
        for phrase, chip in CHIP_WORDS.items():
            i = low.find(phrase)
            if i >= 0:
                snippet = re.sub(r"\s+", " ", (r.text or "")[max(0, i - 50):i + 70]).strip()
                out.append((str(r.creator), chip, str(r.title), snippet))
                break
    return out


# -- user-shared links --------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+")


def find_url(text: str) -> str | None:
    m = _URL_RE.search(text)
    return m.group(0).rstrip(").,>") if m else None


def _timed_transcript(video_id: str) -> list[tuple[float | None, str]]:
    """(start_seconds, text) segments via the transcript library.

    The library returns per-snippet start times; keeping them is what makes
    "what did they say and WHEN" a query instead of a re-listen.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi

        api = YouTubeTranscriptApi()
        return [(float(sn.start), sn.text) for sn in api.fetch(video_id, languages=["en"])]
    except Exception:  # noqa: BLE001 - caller falls back to the untimed route
        return []


# -- which gameweek is this content about? ------------------------------------
#
# The owner's question, and the answer has to carry its own provenance because
# the two ways of getting it are not equally good:
#
#   STATED   The content named a week. The model's structured calls carry an
#            explicit ``gameweek`` when the speaker said one, and FPL titles
#            state it outright ("Locked & Loaded - Gameweek 1 Pod"). This is
#            evidence.
#   INFERRED ``calendar.next_after(published_at)`` -- the first deadline after
#            publication. That is a GUESS, and a decent one for a video posted
#            two days before a deadline, but wrong for anything published mid
#            week about the week after, and completely wrong for an old video
#            pasted today. It is labelled as a guess wherever it is shown.
#
# When both exist, what was SAID wins. An inference that contradicts the
# content is the inference being wrong.

#: "GW3", "gw 3", "Gameweek 12", "game week 12". Deliberately not "week 3":
#: FPL people say gameweek, and "week 3" appears in prose about anything.
_GW_IN_TEXT_RE = re.compile(r"\b(?:gw|gameweek|game\s?week)\s*[-:]?\s*(\d{1,2})\b",
                            re.IGNORECASE)


def gameweeks_in_text(text: str) -> tuple[int, ...]:
    """Every gameweek a piece of text names, in order of first appearance."""
    out: list[int] = []
    for match in _GW_IN_TEXT_RE.finditer(text or ""):
        gw = int(match.group(1))
        if 1 <= gw <= 38 and gw not in out:
            out.append(gw)
    return tuple(out)


@dataclass(frozen=True)
class GameweekResolution:
    """Which gameweek the content is about, and how that was established.

    ``basis`` is one of ``stated`` / ``inferred`` / ``corrected`` / ``unknown``
    and it is never cosmetic: an inferred week is a guess, and a UI that shows
    it without the label is presenting a guess as a fact.
    """

    gameweek: int | None
    season: str | None
    basis: str
    reason: str
    #: What ``calendar.next_after(published_at)`` said, kept even when the
    #: stated week overrode it, so the two can be compared afterwards.
    inferred: int | None = None
    #: Every gameweek the content itself named.
    stated: tuple[int, ...] = ()

    @property
    def is_guess(self) -> bool:
        return self.basis == "inferred"

    @property
    def label(self) -> str:
        if self.gameweek is None:
            return "gameweek unknown"
        if self.basis == "stated":
            return f"GW{self.gameweek} (stated in the content)"
        if self.basis == "corrected":
            return f"GW{self.gameweek} (corrected by hand)"
        return f"GW{self.gameweek} (inferred from the publish date -- a guess)"

    def public(self) -> dict:
        return {
            "gameweek": self.gameweek,
            "season": self.season,
            "basis": self.basis,
            "reason": self.reason,
            "inferred": self.inferred,
            "stated": list(self.stated),
            "is_guess": self.is_guess,
            "label": self.label,
        }


def resolve_gameweek(*, calendar, published_at: dt.datetime, title: str = "",
                     analysis=None, published_basis: str = "") -> GameweekResolution:
    """Prefer what the content said; fall back to the publish-date inference.

    ``analysis`` is a :class:`~fpl_edge.ingest.content.analyze.TranscriptAnalysis`
    or None. Its calls carry a ``gameweek`` when the speaker named one, and
    those are the strongest evidence available -- a person saying "for gameweek
    four" is not a thing to second-guess with a calendar lookup. The title is
    read too, because FPL titles state the week constantly and a title is part
    of what was published.

    When several weeks are named the most-mentioned wins, ties going to the
    earliest -- a 'GW4 and GW5' preview is about GW4 first.
    """
    from collections import Counter

    inferred_pair = calendar.next_after(published_at) if calendar else None
    inferred_gw = int(inferred_pair[1]) if inferred_pair else None
    season = inferred_pair[0] if inferred_pair else None

    counts: Counter[int] = Counter()
    if analysis is not None:
        for bucket in ("transfers_in", "transfers_out", "captaincy",
                       "differentials", "chip_advice"):
            for call in getattr(analysis, bucket, ()) or ():
                gw = getattr(call, "gameweek", None)
                if gw is not None and 1 <= int(gw) <= 38:
                    counts[int(gw)] += 1
    title_gws = gameweeks_in_text(title)
    for gw in title_gws:
        counts[gw] += 1

    if counts:
        best = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        stated = tuple(sorted(counts))
        where = []
        if title_gws:
            where.append("the title")
        if any(gw for gw in counts if gw not in title_gws) or not title_gws:
            where.append("the analysed calls")
        reason = (
            f"GW{best} was stated in the content ({' and '.join(where)}); "
            + (f"the publish-date inference said GW{inferred_gw}, and what was "
               f"actually said wins."
               if inferred_gw is not None and inferred_gw != best else
               "the publish-date inference agrees."
               if inferred_gw == best else
               "there is no publish-date inference to compare it against.")
        )
        return GameweekResolution(best, season, "stated", reason,
                                  inferred=inferred_gw, stated=stated)

    if inferred_gw is not None:
        return GameweekResolution(
            inferred_gw, season, "inferred",
            f"nothing in the title or the analysed calls named a gameweek, so "
            f"this is GW{inferred_gw} only because that is the first deadline "
            f"after {published_at.isoformat()}"
            + (f" ({published_basis})" if published_basis else "")
            + ". That is a guess and is labelled as one.",
            inferred=inferred_gw)

    return GameweekResolution(
        None, None, "unknown",
        "the content named no gameweek and the warehouse holds no deadline "
        "after this item's publication, so there is nothing to derive one "
        "from and none is invented.")


@dataclass
class LinkFindings:
    url: str
    title: str
    creator: str
    text_source: str
    n_claims: int
    claims: list[dict]
    committed: str
    analysis: object | None = None      # TranscriptAnalysis when the LLM ran
    analysis_note: str = ""             # why it did not, when it did not
    n_segments: int = 0
    # -- who said it (ask 1) -------------------------------------------------
    #: The channel/show name verbatim from the source, or None when it stated
    #: none. NEVER a name this code made up.
    channel: str | None = None
    #: How ``creator`` was arrived at: channel_id | channel_name | panel_show |
    #: host_registry | unregistered_channel | no_channel_on_page | ...
    creator_basis: str = "unresolved"
    creator_reason: str = ""
    #: Does the panel scope admit this creator? False means the reader sees a
    #: real name and the board legitimately does not carry the item.
    tracked: bool = False
    item_id: str | None = None
    published_at: dt.datetime | None = None
    published_basis: str = ""
    # -- which gameweek (ask 3) ---------------------------------------------
    gameweek: GameweekResolution | None = None

    def render(self) -> str:
        who = self.creator
        if not self.tracked and self.creator_basis != "unresolved":
            who += " (pasted, not tracked by the panel)"
        lines = [f"Transcribed: {self.title[:70]}",
                 f"Creator: {who} — {self.creator_reason or self.creator_basis}",
                 (f"({self.n_segments} timestamped segments stored, "
                  f"text via {self.text_source})")]
        if self.gameweek is not None:
            lines.append(f"Gameweek: {self.gameweek.label}")
        lines.append("")
        a = self.analysis
        if a is not None:
            lines.append("Summary:")
            lines += [f"  • {b}" for b in a.summary[:6]]

            def calls(label, items):
                if not items:
                    return
                lines.append(f"\n{label}:")
                for c in items[:5]:
                    gw = f" (GW{c.gameweek})" if c.gameweek else ""
                    lines.append(f"  • {c.player}{gw} — {c.conviction} conviction")
                    lines.append(f"    \"{c.quote[:100]}\"")

            calls("Transfers IN", a.transfers_in)
            calls("Transfers OUT", a.transfers_out)
            calls("Captaincy", a.captaincy)
            if a.chip_advice:
                lines.append("\nChips:")
                for ch in a.chip_advice[:4]:
                    gw = f" GW{ch.gameweek}" if ch.gameweek else ""
                    lines.append(f"  • {ch.chip}: {ch.stance}{gw} — "
                                 f"\"{ch.quote[:80]}\"")
            calls("Differentials", a.differentials)
            lines.append(f"\n{self.n_claims} calls persisted to the scoreboard "
                         f"(conviction bands: high=80% / med=60% / low=40% — "
                         f"tested against results, not decoration).")
        else:
            lines.append(self.analysis_note)
            if self.claims:
                lines.append(f"\nFallback keyword extraction ({self.n_claims} "
                             f"rough claims — treat as leads only):")
                for c in self.claims[:8]:
                    lines.append(f"  • GW{c['gw']} {c['action']}: {c['player']}")
        lines.append(self.committed)
        return "\n".join(lines)


#: The label a pasted item keeps when its source refuses to say who published
#: it. It is a statement about OUR knowledge, not a creator, and it is only
#: ever used with a stated reason attached (``LinkFindings.creator_reason``).


def panel_show_names(wh) -> frozenset[str]:
    """Shows the owner's active panel appears on. Empty when unreadable.

    Read here rather than inside :mod:`fpl_edge.ingest.content.youtube` so that
    the youtube module keeps taking no warehouse and issuing no queries. An
    empty set means "could not tell", and every caller degrades upward from it.
    """
    try:
        rows = wh.sql(
            "SELECT DISTINCT s.show_creator FROM panel_person_show s "
            "JOIN panel_person p USING (person_key) WHERE p.active")
    except Exception:  # noqa: BLE001 - another team's table; may not exist
        return frozenset()
    return frozenset(str(v) for v in rows["show_creator"] if v is not None)


def _creator_from_host(url: str) -> tuple[str | None, str, str]:
    """Registry creator for a non-YouTube URL, matched on the source's host.

    The podcast/blog half of ask 1. A registered feed states its show, so an
    article on ``fantasyfootballscout.co.uk`` is Fantasy Football Scout's --
    matched on the EXACT host of a registered source url, never on a substring
    of the path, and never on resemblance.
    """
    from urllib.parse import urlparse

    from fpl_edge.ingest.content.sources import ALL_SOURCES

    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    if not host:
        return None, "no_host", "the pasted URL has no host to identify"

    # A host only identifies a publisher when exactly ONE registered source
    # lives there. fantasyfootballscout.co.uk is that show; youtube.com is a
    # PLATFORM shared by thirteen registered channels and by everyone else
    # alive. Matching on it attributed a bare "https://www.youtube.com/" --
    # no video at all -- to Let's Talk FPL, with a confident basis string, on
    # the strength of being first in the registry. That is a fabrication: it
    # puts content in a named creator's mouth from a URL that names nobody.
    by_host: dict[str, list] = {}
    for source in ALL_SOURCES:
        src_host = (urlparse(source.url).hostname or "").lower().removeprefix("www.")
        if src_host:
            by_host.setdefault(src_host, []).append(source)
    here = by_host.get(host, [])
    creators_here = {s.creator for s in here}
    if len(creators_here) > 1:
        return (None, "shared_host",
                (f"{host} hosts {len(creators_here)} registered creators, so "
                 f"the host alone does not say who published this. The "
                 f"channel itself has to name them."))
    for source in here:
        return (source.creator, "host_registry",
                (f"{host} is the host of a registered source "
                 f"({source.key}), which publishes as {source.creator}"))
    return (None, "unregistered_host",
            (f"no registered source publishes at {host}, so this page's "
             f"publisher is not identified and none is invented"))


def ingest_link(wh, url: str) -> LinkFindings:
    """Transcribe a shared link, extract claims, persist, commit the findings.

    Single-item, user-initiated: the transcript route is the same one the
    user's own fpl-server MCP has always used for exactly this ask. The bulk
    crawler's robots gate is untouched.

    WHO IT IS FILED UNDER
    ---------------------
    Every pasted item used to be stored as ``creator='user-shared'`` while the
    watch page said ``"ownerChannelName":"FPL Raptor"`` three lines further
    down. That was not a privacy choice, it was an unparsed field, and it had a
    consequence: ``creator_board`` scopes to the panel, so an item under
    ``user-shared`` was transcribed and analysed correctly and then appeared
    nowhere at all. The channel is now read off the SAME response that was
    already being fetched for the title -- no extra request, which is the
    condition ``docs/data_sources.md`` 7A rests on -- and resolved through
    :func:`~fpl_edge.ingest.content.youtube.creator_for_channel`.

    Three outcomes, and the third is the one that must not be papered over:

    * a panel/registry creator -- filed under that creator, ``tracked=True``,
      and it flows into the board and the player strip like any other item;
    * a real channel that is not on the panel -- filed under the REAL channel
      name, ``tracked=False``. The reader sees who said it; the panel scope
      still excludes it, which is correct rather than a bug;
    * nothing stated -- :data:`UNRESOLVED_CREATOR` plus a reason. A generic
      label with an explanation, never a name that was guessed.

    ``source_key`` stays ``"user_link"`` in all three cases. It answers "how did
    this arrive", and the honest answer is "the owner pasted it", not "it came
    off that creator's feed".
    """
    import hashlib

    from fpl_edge.ingest.content.claims import ExtractionStats, extract_from_item
    from fpl_edge.ingest.content.fetch import ContentFetcher
    from fpl_edge.ingest.content.loaders import _fetch_article
    from fpl_edge.ingest.content.models import ContentItem
    from fpl_edge.ingest.content.pipeline import build_resolver, load_calendar
    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.ingest.content.youtube import (
        Channel,
        channel_from_watch,
        creator_for_channel,
        fetch_transcript,
        published_from_watch,
    )

    now = dt.datetime.now(UTC)
    # One authority for the video id, in ingest/content/urls.py. The copy
    # that used to live here read exactly eleven id characters and knew
    # nothing about /embed/ (ARCHITECTURE_REVIEW.md check 1).
    yt = youtube_id(url)
    channel = Channel(None, None, "not a youtube url")
    published_at, published_basis = now, ""
    host_creator = host_basis = host_reason = None
    route = ""
    transcript_wall_s = 0.0
    # For the user's OWN shared video the transcript routes are used directly:
    # both terminate at endpoints YouTube's robots.txt disallows for crawlers,
    # which is why the BULK pipeline keeps respect_robots on and stays
    # description-only. A single video, transcribed at the owner's explicit
    # request, is the exact use their fpl-server MCP has always made of the
    # same library; articles keep the robots check.
    with ContentFetcher("user_link", respect_robots=not yt) as fetcher:
        if yt:
            vid = yt
            title = f"YouTube video {vid}"
            # The watch page FIRST: it was already fetched for the title, and
            # it carries the channel and the real publication instant too. One
            # response, three fields, zero extra requests.
            watch = fetcher.get(f"https://www.youtube.com/watch?v={vid}")
            if watch.ok:
                m = re.search(r"<title>(.*?)</title>", watch.text, re.DOTALL)
                if m:
                    title = re.sub(r"\s*-\s*YouTube\s*$", "", m.group(1)).strip()
                channel = channel_from_watch(watch.text)
                stated_publish = published_from_watch(watch.text)
                if stated_publish is not None:
                    # The video's own datePublished, not the moment of the
                    # paste. This is what makes the gameweek inference answer a
                    # question about the VIDEO: stamping an eight-week-old
                    # episode with today made `calendar.next_after` return the
                    # upcoming deadline for content about a played gameweek.
                    published_at = stated_publish
                    published_basis = "the video's own datePublished"
                else:
                    published_basis = ("the watch page stated no publication "
                                       "date, so the paste time is used")
            else:
                published_basis = ("the watch page could not be read, so the "
                                   "paste time is used")
            _t0 = time.monotonic()
            lines, route = fetch_transcript(fetcher, vid, allow_disallowed_routes=True)
            transcript_wall_s = time.monotonic() - _t0
            text = " ".join(lines)
            text_source = "transcript" if lines else f"unavailable ({route})"
            if not lines:
                match = creator_for_channel(channel)
                return LinkFindings(
                    url=url, title=title,
                    creator=match.creator or channel.name or UNRESOLVED_CREATOR,
                    text_source=text_source, n_claims=0, claims=[],
                    committed="Nothing to commit: no transcript "
                              "was available for this video.",
                    channel=channel.name, creator_basis=match.basis,
                    creator_reason=match.reason,
                    published_at=published_at, published_basis=published_basis)
        else:
            text = _fetch_article(fetcher, url)
            text_source = "article"
            title = url.split("/")[-1][:60] or url
            host_creator, host_basis, host_reason = _creator_from_host(url)

    # -- who published it ----------------------------------------------------
    # Everything from here touches the warehouse. It is deliberately after the
    # fetch: link_jobs hands this function a LeasedWarehouse that connects on
    # first attribute access, so the DuckDB write lock is taken for persistence
    # and never held across the network work above.
    panel_shows = panel_show_names(wh)
    if yt:
        match = creator_for_channel(channel, panel_shows=panel_shows)
        creator = match.creator or channel.name or UNRESOLVED_CREATOR
        creator_basis, creator_reason = match.basis, match.reason
        tracked = match.tracked
    elif host_creator:
        creator, creator_basis, creator_reason = host_creator, host_basis, host_reason
        tracked = (not panel_shows) or creator in panel_shows
    else:
        creator = UNRESOLVED_CREATOR
        creator_basis = host_basis or "unresolved"
        creator_reason = host_reason or "no publisher could be identified"
        tracked = False

    # Canonicalise before hashing: watch?v=, youtu.be/ and shorts/ forms of
    # one video must dedupe to one item, or every re-paste doubles its claims.
    canonical = f"youtube:{yt}" if yt else url
    item = ContentItem(
        item_id="link_" + hashlib.sha256(canonical.encode()).hexdigest()[:16],
        source_key="user_link", creator=creator, kind="link",
        title=title, url=url, published_at=published_at, text=text,
        fetched_at=now, text_source="transcript" if text_source == "transcript" else "article",
    )
    resolver = build_resolver(wh)
    calendar, _ = load_calendar(wh)

    store = ContentStore(wh)
    store.migrate()
    store.insert_items([item])

    # Full transcript, timestamped, stored -- the queryable source of truth.
    timed_route = None
    segments = []
    if yt and text_source == "transcript":
        _t0 = time.monotonic()
        segments = _timed_transcript(yt)
        transcript_wall_s += time.monotonic() - _t0
        if segments:
            timed_route = "youtube_transcript_api"
    if not segments and text:
        segments = [(None, text)]
    wh.sql("DELETE FROM transcript_segment WHERE item_id = ?", [item.item_id])
    for seq, (start, seg_text) in enumerate(segments):
        wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
               [item.item_id, seq, start, seg_text])

    if yt and text_source == "transcript":
        # The provenance receipt (PIPELINES.md §3 defect 4). This was the one
        # transcript path that wrote transcript_segment with no
        # transcript_provenance row, so "how did this transcript come to be"
        # was unanswerable for exactly the items the owner pasted by hand.
        # Written through asr.store_provenance -- the same DDL and the same
        # INSERT the pipeline's caption and ASR paths use, never a second
        # schema. derivation='captions': this route reads published caption
        # tracks only (there is no audio branch here, so 'asr' is impossible).
        # audio_sha256/'bytes are empty and audio_seconds is None because no
        # audio was ever downloaded or decoded -- the same honest NULLs
        # transcription_from_captions records for the pipeline's caption path;
        # covered_seconds IS measurable (the last cue's start) when the cues
        # came back timed, and 0.0 when only untimed text was available.
        from fpl_edge.ingest.content import asr
        from fpl_edge.ingest.content.youtube import TimedLine

        timed = [TimedLine(start_s=float(s), text=str(t))
                 for s, t in segments if s is not None]
        if timed:
            transcription = asr.transcription_from_captions(
                timed, video_id=yt,
                route=timed_route or route or "captions",
                wall_seconds=transcript_wall_s)
        else:
            transcription = asr.Transcription(
                segments=(asr.Segment(seq=0, start_s=0.0, end_s=0.0, text=text),),
                model=route or "captions", engine="youtube_captions",
                language="en", audio_seconds=None, covered_seconds=0.0,
                wall_seconds=transcript_wall_s, audio_sha256="",
                audio_bytes=0,
                audio_url=f"https://www.youtube.com/watch?v={yt}",
            )
        # prior_* stay NULL: this item was inserted by this same call with the
        # transcript already in place, so there was no earlier show-notes text
        # being replaced -- unlike the pipeline path, which promotes text over
        # a description and records what it displaced.
        asr.store_provenance(wh, item.item_id, transcription,
                             derivation="captions")

    # Semantic analysis first; keyword windows only as an admitted fallback.
    from fpl_edge.ingest.content.analyze import (
        AnalysisUnavailable,
        analyze_transcript,
        claims_from_analysis,
        insights_from_analysis,
        load_analysis,
        store_analysis,
        store_insights,
    )

    analysis = load_analysis(wh, item.item_id)  # cached: never pay twice
    analysis_note = ""
    if analysis is None and text:
        try:
            analysis = analyze_transcript(title=title, creator=creator,
                                          text=text, text_source=item.text_source)
            store_analysis(wh, item.item_id, analysis,
                           text_source=item.text_source, chars=len(text))
        except AnalysisUnavailable as exc:
            analysis_note = str(exc)
        except Exception as exc:  # noqa: BLE001 - degraded beats dead
            analysis_note = f"Semantic analysis failed ({type(exc).__name__}); " \
                            f"falling back to keyword extraction."

    # -- which gameweek ------------------------------------------------------
    gw = resolve_gameweek(calendar=calendar, published_at=item.published_at,
                          title=title, analysis=analysis,
                          published_basis=published_basis)
    inferred = calendar.next_after(item.published_at)
    default_gw = gw.gameweek if gw.gameweek is not None else (
        int(inferred[1]) if inferred else 1)
    season = gw.season or (inferred[0] if inferred else "2026-27")

    insights = []
    if analysis is not None:
        claims, dropped = claims_from_analysis(
            analysis, item=item, resolver=resolver, default_gw=default_gw,
            season=season,
        )
        # Observations from the same reading. `locate` is deliberately omitted:
        # the quote->start_s index (TranscriptIndex) lives in the platform
        # layer, which this module must not import, and the extractor's own
        # rule is that a deep link to the wrong minute is worse than none. So
        # start_s stays NULL here and the insight still carries its verbatim
        # quote, which is the part that matters.
        from fpl_edge.ingest.content.clubs import club_resolver
        insights, ins_dropped = insights_from_analysis(
            analysis, item=item, resolver=resolver, default_gw=default_gw,
            season=season, text_source=item.text_source,
            clubs=club_resolver(wh, season),
        )
        if ins_dropped:
            dropped = list(dropped) + [d for _, d in ins_dropped]
        if dropped:
            analysis_note = ("Unresolved names (kept out of the scoreboard "
                             "rather than guessed): " + ", ".join(sorted(set(dropped))[:6]))
    else:
        stats = ExtractionStats()
        claims = extract_from_item(item, resolver, calendar, stats)

    store.insert_claims(claims)
    if insights:
        store_insights(wh, insights)
    record_link_item(wh, item_id=item.item_id, url=url, creator=creator,
                     creator_basis=creator_basis, creator_reason=creator_reason,
                     channel_name=channel.name, tracked=tracked, gw=gw)
    committed = _commit_findings(url, title, claims, analysis=analysis)
    return LinkFindings(
        url=url, title=title, creator=creator, text_source=text_source,
        n_claims=len(claims),
        claims=[{"player": c.player_name, "action": str(c.action),
                 "gw": int(c.gameweek), "conf": float(c.confidence)} for c in claims],
        committed=committed, analysis=analysis, analysis_note=analysis_note,
        n_segments=len(segments), channel=channel.name,
        creator_basis=creator_basis, creator_reason=creator_reason,
        tracked=tracked, item_id=item.item_id, published_at=item.published_at,
        published_basis=published_basis, gameweek=gw,
    )


def _commit_findings(url: str, title: str, claims, *, analysis=None) -> str:
    """One markdown note per shared link, pushed to the reports repo."""
    if not REPORTS_DIR.exists():
        return "Findings stored in the warehouse (reports repo not cloned here)."
    day = dt.datetime.now(UTC).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower())[:48].strip("-") or "link"
    notes = REPORTS_DIR / "links"
    notes.mkdir(exist_ok=True)
    path = notes / f"{day}-{slug}.md"
    body = [f"# {title}", "", f"Source: {url}", f"Analysed: {day}", ""]
    if analysis is not None:
        body += ["## Summary", ""] + [f"- {b}" for b in analysis.summary] + [""]
        for label, items in (("Transfers in", analysis.transfers_in),
                             ("Transfers out", analysis.transfers_out),
                             ("Captaincy", analysis.captaincy),
                             ("Differentials", analysis.differentials)):
            if items:
                body += [f"## {label}", ""]
                body += [f"- **{c.player}** ({c.conviction}"
                         + (f", GW{c.gameweek}" if c.gameweek else "")
                         + f") — {c.reasoning}\n  > {c.quote}" for c in items] + [""]
        if analysis.chip_advice:
            body += ["## Chips", ""]
            body += [f"- **{ch.chip}**: {ch.stance}"
                     + (f" GW{ch.gameweek}" if ch.gameweek else "")
                     + f" — {ch.reasoning}\n  > {ch.quote}"
                     for ch in analysis.chip_advice] + [""]
    body += ["## Scoreboard claims", ""]
    body += ([f"- GW{c.gameweek} **{c.action}** {c.player_name} (conf {c.confidence:.0%})"
              for c in claims] or ["- none extracted"])
    path.write_text("\n".join(body) + "\n")

    def git(*args):
        return subprocess.run(["git", *args], cwd=REPORTS_DIR,
                              capture_output=True, text=True)

    git("add", "-A")
    git("-c", "user.name=fpl-edge", "-c", "user.email=bot@fpl-edge.local",
        "commit", "-m", f"Link findings: {title[:60]}")
    push = git("push")
    where = f"fpl-reports/links/{path.name}"
    return (f"Findings committed to {where}." if push.returncode == 0
            else f"Findings committed locally to {where} (push failed).")
