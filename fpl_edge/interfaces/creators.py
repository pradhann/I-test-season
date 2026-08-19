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
from dataclasses import dataclass, field
from pathlib import Path

UTC = dt.timezone.utc
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
    stripped = re.sub(r"^the\s+", "", creator, flags=re.I)
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
_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?[^ ]*v=|shorts/|live/)|youtu\.be/)([\w-]{11})"
)


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

    def render(self) -> str:
        lines = [f"Transcribed: {self.title[:70]}",
                 f"({self.n_segments} timestamped segments stored, "
                 f"text via {self.text_source})", ""]
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


def ingest_link(wh, url: str) -> LinkFindings:
    """Transcribe a shared link, extract claims, persist, commit the findings.

    Single-item, user-initiated: the transcript route is the same one the
    user's own fpl-server MCP has always used for exactly this ask. The bulk
    crawler's robots gate is untouched.
    """
    import hashlib

    from fpl_edge.ingest.content.claims import ExtractionStats, extract_from_item
    from fpl_edge.ingest.content.fetch import ContentFetcher
    from fpl_edge.ingest.content.loaders import _fetch_article
    from fpl_edge.ingest.content.models import ContentItem
    from fpl_edge.ingest.content.pipeline import build_resolver, load_calendar
    from fpl_edge.ingest.content.store import ContentStore
    from fpl_edge.ingest.content.youtube import fetch_transcript

    now = dt.datetime.now(UTC)
    yt = _YT_ID_RE.search(url)
    # For the user's OWN shared video the transcript routes are used directly:
    # both terminate at endpoints YouTube's robots.txt disallows for crawlers,
    # which is why the BULK pipeline keeps respect_robots on and stays
    # description-only. A single video, transcribed at the owner's explicit
    # request, is the exact use their fpl-server MCP has always made of the
    # same library; articles keep the robots check.
    with ContentFetcher("user_link", respect_robots=not yt) as fetcher:
        if yt:
            vid = yt.group(1)
            lines, route = fetch_transcript(fetcher, vid, allow_disallowed_routes=True)
            text = " ".join(lines)
            text_source = "transcript" if lines else f"unavailable ({route})"
            title = f"YouTube video {vid}"
            watch = fetcher.get(f"https://www.youtube.com/watch?v={vid}")
            if watch.ok:
                m = re.search(r"<title>(.*?)</title>", watch.text, re.S)
                if m:
                    title = re.sub(r"\s*-\s*YouTube\s*$", "", m.group(1)).strip()
            if not lines:
                return LinkFindings(url=url, title=title, creator="user-shared",
                                    text_source=text_source, n_claims=0, claims=[],
                                    committed="Nothing to commit: no transcript "
                                              "was available for this video.")
        else:
            text = _fetch_article(fetcher, url)
            text_source = "article"
            title = url.split("/")[-1][:60] or url

    # Canonicalise before hashing: watch?v=, youtu.be/ and shorts/ forms of
    # one video must dedupe to one item, or every re-paste doubles its claims.
    canonical = f"youtube:{yt.group(1)}" if yt else url
    item = ContentItem(
        item_id="link_" + hashlib.sha256(canonical.encode()).hexdigest()[:16],
        source_key="user_link", creator="user-shared", kind="link",
        title=title, url=url, published_at=now, text=text,
        fetched_at=now, text_source="transcript" if text_source == "transcript" else "article",
    )
    resolver = build_resolver(wh)
    calendar, _ = load_calendar(wh)

    store = ContentStore(wh)
    store.migrate()
    store.insert_items([item])

    # Full transcript, timestamped, stored -- the queryable source of truth.
    segments = _timed_transcript(yt.group(1)) if (yt and text_source == "transcript") else []
    if not segments and text:
        segments = [(None, text)]
    wh.sql("DELETE FROM transcript_segment WHERE item_id = ?", [item.item_id])
    for seq, (start, seg_text) in enumerate(segments):
        wh.sql("INSERT INTO transcript_segment VALUES (?, ?, ?, ?)",
               [item.item_id, seq, start, seg_text])

    # Semantic analysis first; keyword windows only as an admitted fallback.
    from fpl_edge.ingest.content.analyze import (
        AnalysisUnavailable,
        analyze_transcript,
        claims_from_analysis,
        load_analysis,
        store_analysis,
    )

    analysis = load_analysis(wh, item.item_id)  # cached: never pay twice
    analysis_note = ""
    if analysis is None and text:
        try:
            analysis = analyze_transcript(title=title, creator="user-shared", text=text)
            store_analysis(wh, item.item_id, analysis)
        except AnalysisUnavailable as exc:
            analysis_note = str(exc)
        except Exception as exc:  # noqa: BLE001 - degraded beats dead
            analysis_note = f"Semantic analysis failed ({type(exc).__name__}); " \
                            f"falling back to keyword extraction."

    inferred = calendar.next_after(item.published_at)
    default_gw = int(inferred[1]) if inferred else 1
    season = inferred[0] if inferred else "2026-27"

    if analysis is not None:
        claims, dropped = claims_from_analysis(
            analysis, item=item, resolver=resolver, default_gw=default_gw,
            season=season,
        )
        if dropped:
            analysis_note = ("Unresolved names (kept out of the scoreboard "
                             "rather than guessed): " + ", ".join(sorted(set(dropped))[:6]))
    else:
        stats = ExtractionStats()
        claims = extract_from_item(item, resolver, calendar, stats)

    store.insert_claims(claims)
    committed = _commit_findings(url, title, claims, analysis=analysis)
    return LinkFindings(
        url=url, title=title, creator="user-shared", text_source=text_source,
        n_claims=len(claims),
        claims=[{"player": c.player_name, "action": str(c.action),
                 "gw": int(c.gameweek), "conf": float(c.confidence)} for c in claims],
        committed=committed, analysis=analysis, analysis_note=analysis_note,
        n_segments=len(segments),
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
