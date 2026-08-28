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
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

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


#: ``dim_manager.source`` prefixes whose ``player_name`` came back from the
#: FPL API rather than from a curated list.
#:
#: ``elite_named`` rows are written only after :func:`fpl_edge.ingest.rivals.
#: elite.verify` has checked the curated name against ``/entry/{id}/``;
#: ``expert`` rows are overwritten by ``_profile_row`` from the same endpoint;
#: ``top1k``, ``mini_league`` and ``snowball`` rows carry
#: ``player_first_name``/``player_last_name`` straight out of league standings.
#:
#: ``elite_list`` and ``winner`` are deliberately ABSENT. Those names are
#: pinned third-party text next to an ID nobody re-checked, and FPL entry IDs
#: rot every August into a different real person. Linking a creator to one of
#: those would be recording a guess as an identity.
API_NAMED_MANAGER_SOURCES = ("elite_named", "expert", "top1k", "mini_league",
                             "snowball")


@dataclass(frozen=True)
class CreatorEntry:
    """A creator resolved to an FPL entry, or the reason they were not."""

    creator: str
    entry_id: int | None
    player_name: str | None
    entry_name: str | None
    method: str
    verified: bool
    reason: str


def _person_shaped(normalised: str) -> bool:
    """At least a forename and a surname.

    A single token is not an identity: dozens of managers are called "Tom",
    and a creator called "Tom" matching all of them is not a link, it is a
    collision waiting to be written down as a fact.
    """
    return len(normalised.split()) >= 2


def verified_manager_index(wh) -> dict[str, list[dict]]:
    """Normalised API-sourced manager name -> the entries carrying that name.

    Built through :func:`fpl_edge.ingest.rivals.names.norm`, the one matcher,
    so a creator is checked against exactly the same folding that verifies a
    curated elite ID.
    """
    from fpl_edge.ingest.rivals.names import norm

    placeholders = ", ".join("?" for _ in API_NAMED_MANAGER_SOURCES)
    rows = wh.sql(
        "SELECT DISTINCT entry_id, player_name, entry_name, source FROM dim_manager "
        f"WHERE player_name IS NOT NULL AND player_name <> '' "
        f"AND split_part(source, ':', 1) IN ({placeholders})",
        list(API_NAMED_MANAGER_SOURCES),
    )
    index: dict[str, list[dict]] = {}
    for row in rows.itertuples(index=False):
        index.setdefault(norm(row.player_name), []).append({
            "entry_id": int(row.entry_id), "player_name": row.player_name,
            "entry_name": row.entry_name, "source": row.source,
        })
    return index


def link_creator_entries(wh, creators: list[str] | None = None) -> list[CreatorEntry]:
    """Link creators to FPL entries where the evidence already exists.

    The rule is deliberately narrow, and narrow is the point:

    * the creator's name, accent- and case-folded by ``names.norm``, must be
      EXACTLY a name the FPL API returned for an entry -- not a containment,
      not a fuzzy score. "Let's Talk FPL" does not become Andy's entry because
      "Andy LTFPL" looks like it might be the same person; that is a nickname
      resemblance, and the brief for this table is that a resemblance is not
      evidence;
    * the name must have at least two tokens (see :func:`_person_shaped`);
    * the name must be unambiguous -- one entry, not forty.

    Everything else comes back unresolved WITH a reason, which is what the
    panel renders. Unresolved is the expected majority: creator names are
    channel names, and a channel is not a person.
    """
    from fpl_edge.ingest.rivals.elite import ELITE_NAMED
    from fpl_edge.ingest.rivals.names import norm

    if creators is None:
        creators = sorted({
            c for c in wh.sql("SELECT DISTINCT creator FROM content_source").creator
            if c and c not in ("user-shared", UNRESOLVED_CREATOR)
        })
    index = verified_manager_index(wh)
    # The 8 hand-verified elite entries, keyed the same way. Their IDs were
    # checked against /entry/{id}/ by elite.verify, which is the strongest
    # provenance in the warehouse, so they are preferred when both agree.
    elite = {norm(e.name): e for e in ELITE_NAMED}

    out: list[CreatorEntry] = []
    for creator in creators:
        key = norm(creator)
        if not _person_shaped(key):
            out.append(CreatorEntry(creator, None, None, None, "none", False,
                                    "creator name is a single token; too "
                                    "ambiguous to identify a person"))
            continue
        hits = index.get(key, [])
        entry_ids = {h["entry_id"] for h in hits}
        if not entry_ids:
            out.append(CreatorEntry(
                creator, None, None, None, "none", False,
                "no FPL entry whose API-reported name equals this creator name"))
            continue
        if len(entry_ids) > 1:
            out.append(CreatorEntry(
                creator, None, None, None, "none", False,
                f"ambiguous: {len(entry_ids)} entries report this exact name"))
            continue
        hit = hits[0]
        named = elite.get(key)
        method = ("elite_named exact name match" if named is not None
                  else f"dim_manager({hit['source']}) exact name match")
        if named is not None and named.entry_id != hit["entry_id"]:
            out.append(CreatorEntry(
                creator, None, None, None, "none", False,
                f"conflict: ELITE_NAMED says {named.entry_id}, dim_manager says "
                f"{hit['entry_id']}; not guessing between them"))
            continue
        out.append(CreatorEntry(
            creator, hit["entry_id"], hit["player_name"], hit["entry_name"],
            method, True, "",
        ))
    return out


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


# -- the pasted-link ledger ---------------------------------------------------
#
# One row per pasted item, and it exists because three of the owner's asks all
# need somewhere to put an ANNOTATION about content that must not be rewritten:
#
#   * who the resolved creator is and HOW it was resolved (ask 1),
#   * whether the item has been discarded as irrelevant (ask 2),
#   * which gameweek the content is about, and whether that was stated,
#     inferred, or corrected by hand (ask 3).
#
# WHY A SIDE TABLE AND NOT A FLAG ON content_claim
# ------------------------------------------------
# ``migrations/content_001_claims.sql`` calls a claim "an immutable utterance:
# it was made once, at one instant, and it never gets a newer version", and
# ``ContentStore._insert_new`` enforces it -- an existing row is never touched.
# That rule is right and this module does not break it. A creator who said
# "captain Haaland" said it; deleting the row would make the track record a
# thing we curate after the fact, which is the one property it cannot have.
#
# So DISCARD IS NOT DELETION. What the owner is revoking when they discard a
# pasted link is not the utterance -- it is OUR DECISION TO CARRY IT IN THE
# CORPUS. That decision is ours, it is revisable, and it belongs in a row we
# own. ``discarded_utc`` is set, ``discard_reason`` records why, the archived
# item, its transcript, its analysis and its claims are all left exactly as
# they were, and :func:`restore_item` puts it back. Nothing is destroyed and
# nothing is un-said; the reader simply stops being shown it.
#
# Same argument for the gameweek. A correction does not overwrite
# ``content_claim.gameweek``: the claims keep the week they were written with,
# and the correction is recorded here with the value it replaced
# (``gw_corrected_from``) and the instant of the correction, so "the owner said
# this is GW4" and "the inference said GW3" are both readable afterwards.

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
                     gw: GameweekResolution) -> None:
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
    :mod:`fpl_edge.platform.link_jobs` and :func:`creator_takes` below apply it;
    ``ContentStore.claims_visible_at`` and ``platform/scripts/creators.py`` are
    owned by other agents this cycle and need the same two lines:

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
UNRESOLVED_CREATOR = "unresolved (pasted link)"


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
    yt = _YT_ID_RE.search(url)
    channel = Channel(None, None, "not a youtube url")
    published_at, published_basis = now, ""
    host_creator = host_basis = host_reason = None
    # For the user's OWN shared video the transcript routes are used directly:
    # both terminate at endpoints YouTube's robots.txt disallows for crawlers,
    # which is why the BULK pipeline keeps respect_robots on and stays
    # description-only. A single video, transcribed at the owner's explicit
    # request, is the exact use their fpl-server MCP has always made of the
    # same library; articles keep the robots check.
    with ContentFetcher("user_link", respect_robots=not yt) as fetcher:
        if yt:
            vid = yt.group(1)
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
            lines, route = fetch_transcript(fetcher, vid, allow_disallowed_routes=True)
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
    canonical = f"youtube:{yt.group(1)}" if yt else url
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
