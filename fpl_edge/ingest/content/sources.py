"""The creator registry: who we listen to, where, and under what permission.

Every entry here was reached with a real HTTP request before it was written
down. The status codes are recorded in ``docs/content_sources.md`` with the
date they were observed, and :mod:`fpl_edge.ingest.content.probe` re-measures
them on demand rather than trusting this file.

Three rules govern what may appear here.

**robots.txt is honoured, not routed around.** ``reddit.com/robots.txt`` serves
``User-agent: * / Disallow: /`` to everyone. Reddit's ``.rss`` and ``.json``
endpoints still answer 200 to a plain client, so scraping them would *work* --
which is exactly why the decision has to be made on the policy rather than on
the status code. r/FantasyPL is registered below as
:data:`AccessPolicy.OAUTH_ONLY` and the loader refuses to fetch it without
credentials. No user-agent games, no ``old.reddit.com`` back door.

**No browser impersonation.** Everything here is reachable with the project's
honest ``fpl-edge/0.1`` user agent over ordinary httpx. Nothing in this package
forges a TLS fingerprint, solves a challenge, or pretends to be Chrome. A source
that requires that is a source we do not have.

**Mirrors are not permission.** ``nitter.net/<handle>/rss`` answered 200 in
testing and would hand us X/Twitter content for free. It is a third-party mirror
that re-serves data X's own terms restrict, so using it launders an access
decision we are not entitled to make. X is registered as
:data:`AccessPolicy.FORBIDDEN` and the loader will not fetch it by any route.

The same rule cost the single most valuable thing in this package. YouTube's
robots.txt disallows both ``/feeds/videos.xml`` and ``/youtubei/``, and every
route to a video transcript -- including ``youtube-transcript-api``, which was
tested and *works* -- goes through ``/youtubei/``. So the YouTube sources below
point at the channels' ``/videos`` pages, which are not disallowed, and yield
titles and descriptions instead of twenty minutes of speech. That is a large,
real loss, recorded here rather than quietly recovered.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field


class SourceKind(enum.StrEnum):
    YOUTUBE = "youtube"
    PODCAST = "podcast"
    BLOG = "blog"
    FORUM = "forum"
    SOCIAL = "social"


class AccessPolicy(enum.StrEnum):
    """What we are permitted to do, independent of what would technically work."""

    #: Public feed, no robots.txt objection. Fetch freely at a polite rate.
    OPEN = "open"
    #: Reachable only through an authenticated first-party API. Never scrape.
    OAUTH_ONLY = "oauth_only"
    #: Terms prohibit automated collection. Do not fetch, by any route.
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class Source:
    """One place a creator publishes.

    ``creator`` is the identity that accumulates a track record, and it is
    deliberately shared across a creator's YouTube channel and their podcast
    feed: Let's Talk FPL saying "I'm captaining Haaland" on video and saying it
    again on the podcast is one opinion published twice, not two independent
    votes. The consensus map depends on that being true (see
    :mod:`fpl_edge.ingest.content.consensus`).
    """

    key: str
    creator: str
    kind: SourceKind
    url: str
    policy: AccessPolicy = AccessPolicy.OPEN
    #: Free-text note recorded in the docs, normally the reason for a policy.
    note: str = ""
    #: YouTube only: the canonical UC... id behind the @handle. Recorded for
    #: identity even though the id's natural use -- the Atom feed at
    #: /feeds/videos.xml -- is Disallowed by youtube.com/robots.txt.
    channel_id: str | None = None
    #: YouTube only: the @handle, which is what the permitted /videos page uses.
    handle: str | None = None
    #: Set when the feed body carries only an excerpt and the item link must be
    #: fetched to obtain the text a claim could be extracted from.
    excerpt_only: bool = False


def _yt(key: str, creator: str, channel_id: str, handle: str) -> Source:
    return Source(
        key=key,
        creator=creator,
        kind=SourceKind.YOUTUBE,
        # The channel's own /videos page, NOT /feeds/videos.xml. The feed is
        # Disallowed by youtube.com/robots.txt; this page is not.
        url=f"https://www.youtube.com/@{handle}/videos",
        channel_id=channel_id,
        handle=handle,
    )


def _pod(key: str, creator: str, url: str) -> Source:
    return Source(key=key, creator=creator, kind=SourceKind.PODCAST, url=url)


#: YouTube channels, reached through their public /videos page.
#:
#: ``channel_id`` values were resolved from each @handle page's own
#: ``externalId``, not guessed -- they are recorded for identity, and because the
#: registry should be reproducible. They are deliberately *not* used to build a
#: feed URL: ``youtube.com/robots.txt`` disallows ``/feeds/videos.xml``, so the
#: obvious structured route is off the table and the permitted HTML page is used
#: instead. That costs transcripts. See :mod:`fpl_edge.ingest.content.youtube`.
YOUTUBE_SOURCES: tuple[Source, ...] = (
    _yt("yt_letstalkfpl", "Let's Talk FPL", "UCxeOc7eFxq37yW_Nc-69deA", "LetsTalkFPL"),
    _yt("yt_fplharry", "FPL Harry", "UCcPWnCj5AKC19HaySZjb25g", "FPLHarry"),
    _yt("yt_fplfocal", "FPL Focal", "UC72QokPHXQ9r98ROfNZmaDw", "FPLFocal"),
    _yt("yt_fplraptor", "FPL Raptor", "UC54QLWzsMifTRjNQ02z5pCw", "FPLRaptor"),
    _yt("yt_planetfpl", "Planet FPL", "UC8043oOKTB4uP8Nq15Kz6bg", "PlanetFPL"),
    _yt("yt_fplfamily", "FPL Family", "UCDG_EqOaaO1SSxEMZwfrSkg", "FPLFamily"),
    _yt("yt_fplblackbox", "FPL BlackBox", "UCGJ8-xqhOLwyJNuPMsVoQWQ", "FPLBlackBox"),
    _yt("yt_ffhub", "Fantasy Football Hub", "UCcqEr3DfrRwtoF2a1yW8qgQ", "FantasyFootballHub"),
    _yt("yt_fplmate", "FPL Mate", "UCweDAlFm2LnVcOqaFU4_AGA", "FPLMate"),
    _yt("yt_aboveaverage", "Above Average FPL", "UCnaJiRMf5hju0TlaeGK5CDQ", "AboveAverageFPL"),
    _yt("yt_fplgeneral", "FPL General", "UCxj4WVoWBuwXPGJsvUFPVig", "FPLGeneral"),
    _yt("yt_ffscout", "Fantasy Football Scout", "UCVEKeBG9nbkufZxHWVtDHzQ",
        "FantasyFootballScout"),
    _yt("yt_fpltom", "FPL Tom", "UC14BPCP-fxIDuG0I_dC2Kpg", "FPLTom"),
)

#: Podcast RSS. Feed URLs came from the iTunes Search API (a documented public
#: endpoint), then each was fetched and its item count verified.
#:
#: These matter far more than YouTube for the part of this task that is hard.
#: A YouTube channel feed exposes the most recent 15 uploads, all of them from
#: the last fortnight, so it can only ever produce claims about a gameweek that
#: has not been played. Podcast feeds carry the full back catalogue -- hundreds
#: of episodes reaching back multiple seasons -- which is the only place a
#: creator's *measured* hit rate can come from before GW1 is played.
PODCAST_SOURCES: tuple[Source, ...] = (
    _pod("pod_letstalkfpl", "Let's Talk FPL", "https://feeds.megaphone.fm/COMG4898871165"),
    _pod("pod_fplfocal", "FPL Focal", "https://feeds.megaphone.fm/BLU8570913833"),
    _pod("pod_fplharry", "FPL Harry", "https://feeds.megaphone.fm/BLU5639728837"),
    _pod("pod_ffscout", "Fantasy Football Scout", "https://feeds.megaphone.fm/BLU3712102693"),
    _pod("pod_planetfpl", "Planet FPL", "https://feeds.megaphone.fm/COMG5751810297"),
    _pod("pod_fplwire", "The FPL Wire", "https://feeds.megaphone.fm/BLU9598812574"),
    _pod("pod_aboveaverage", "Above Average FPL", "https://feeds.megaphone.fm/COMG2457739504"),
    _pod("pod_fplblackbox", "FPL BlackBox", "https://feeds.megaphone.fm/BLU4752868283"),
    _pod("pod_ffhub", "Fantasy Football Hub", "https://feeds.megaphone.fm/COMG9112865919"),
    _pod("pod_fplraptor", "FPL Raptor", "https://feeds.megaphone.fm/COMG8319298159"),
    _pod("pod_whogottheassist", "Who Got The Assist?", "https://feeds.megaphone.fm/COMG2325258523"),
    _pod("pod_fmlfpl", "FML FPL", "https://feeds.megaphone.fm/COMG5321018029"),
    _pod("pod_alwayscheating", "Always Cheating", "https://anchor.fm/s/10622ff04/podcast/rss"),
    _pod("pod_fplfamily", "FPL Family", "https://audioboom.com/channels/5010221.rss"),
    _pod("pod_fplpod", "FPL Pod", "https://audioboom.com/channels/5001585.rss"),
    _pod("pod_59thminute", "The 59th Minute", "https://feeds.megaphone.fm/COMG4750541235"),
    _pod("pod_athletic", "The Athletic FPL", "https://feeds.acast.com/public/shows/683750c96e5b65d787575771"),
    _pod("pod_ignoretemplate", "Ignore the Template", "https://rss.buzzsprout.com/2395080.rss"),
    _pod("pod_fpljuice", "FPL JUiCE", "https://feeds.simplecast.com/7rHIPLtQ"),
    _pod("pod_skysports", "Sky Sports FPL", "https://feeds.captivate.fm/sky-sports-fantasy/"),
    _pod("pod_gianni", "Gianni Buttice", "https://feeds.megaphone.fm/COMG2213156107"),
    _pod("pod_allinfootball", "All In Football FPL", "https://feeds.acast.com/public/shows/604b79bc69ef087644689f81"),
)

#: Blogs and newsletters. WordPress feeds serve an excerpt plus a permalink, so
#: ``excerpt_only`` marks the ones whose item body is a teaser rather than the
#: article: the loader fetches the linked page for those, subject to the same
#: robots.txt check as everything else.
BLOG_SOURCES: tuple[Source, ...] = (
    Source(
        "blog_ffscout", "Fantasy Football Scout", SourceKind.BLOG,
        "https://www.fantasyfootballscout.co.uk/feed/", excerpt_only=True,
    ),
    Source(
        "blog_allaboutfpl", "AllAboutFPL", SourceKind.BLOG,
        "https://allaboutfpl.com/feed/", excerpt_only=True,
    ),
    Source(
        "blog_fplreview", "FPL Review", SourceKind.BLOG,
        "https://fplreview.com/feed/", excerpt_only=True,
    ),
)

#: Sources we can technically reach and deliberately do not take.
RESTRICTED_SOURCES: tuple[Source, ...] = (
    Source(
        "reddit_fantasypl", "r/FantasyPL", SourceKind.FORUM,
        "https://oauth.reddit.com/r/FantasyPL/hot",
        policy=AccessPolicy.OAUTH_ONLY,
        note=(
            "reddit.com/robots.txt is 'User-agent: * / Disallow: /' as of "
            "2026-08-18. The unauthenticated .rss (200) and old.reddit .json "
            "(200) routes both answer, and both are disallowed crawling. Only "
            "the OAuth API is sanctioned; it needs REDDIT_CLIENT_ID and "
            "REDDIT_CLIENT_SECRET, which are not configured, so this source "
            "yields zero items rather than being scraped."
        ),
    ),
    Source(
        "x_fpl", "X / Twitter", SourceKind.SOCIAL,
        "https://api.twitter.com/2/tweets/search/recent",
        policy=AccessPolicy.FORBIDDEN,
        note=(
            "api.twitter.com returns 401 without a bearer token and the free "
            "tier does not include search. The third-party nitter.net mirror "
            "returns 200 and would work; it re-serves content X's terms "
            "restrict, so it is refused on policy, not on capability."
        ),
    ),
)

ALL_SOURCES: tuple[Source, ...] = (
    *YOUTUBE_SOURCES, *PODCAST_SOURCES, *BLOG_SOURCES, *RESTRICTED_SOURCES,
)

BY_KEY: dict[str, Source] = {s.key: s for s in ALL_SOURCES}


def fetchable() -> tuple[Source, ...]:
    """Sources the loader is allowed to hit."""
    return tuple(s for s in ALL_SOURCES if s.policy is AccessPolicy.OPEN)


def creators() -> tuple[str, ...]:
    return tuple(sorted({s.creator for s in ALL_SOURCES}))


@dataclass(frozen=True, slots=True)
class Scope:
    """Which sources a run is allowed to spend itself on, and why those.

    The registry has 38 fetchable sources and the owner cares about roughly
    nine people. Fetching all 38 to reach those nine costs a full backfill of
    Sky Sports FPL's archive to find one host's captain pick, and it is the
    reason an ingest run takes long enough that it does not get run before a
    deadline.

    A scope NARROWS; it never deletes. Every source stays in the registry, is
    still upserted into ``content_source``, and is one ``--only`` away. The
    default target is the panel (see :func:`fpl_edge.ingest.content.panel.
    panel_scope`), because that is what the owner asked for, but "the panel"
    is a value passed in rather than a hard-coded list -- when the panel file
    is absent this degrades to :meth:`everything` with a label saying so,
    rather than to an empty run that looks like a broken fetcher.

    ``label`` exists so the run can print the provenance of its own narrowing.
    A run that silently processed 9 of 38 sources and a run whose feeds all
    404'd produce the same item counts otherwise.
    """

    #: None means "every source you were given". An empty frozenset means
    #: "nothing matched", which is a real and different answer.
    keys: frozenset[str] | None
    label: str
    #: Requested keys that are not in the registry. Never silently dropped:
    #: a typo'd source key would otherwise narrow the run to nothing and look
    #: like a scope that simply had no work to do.
    unknown_keys: tuple[str, ...] = ()

    @classmethod
    def everything(cls, label: str = "all fetchable sources") -> Scope:
        return cls(keys=None, label=label)

    @classmethod
    def from_keys(cls, keys: Iterable[str], label: str) -> Scope:
        wanted = tuple(k.strip() for k in keys if k and k.strip())
        known = frozenset(k for k in wanted if k in BY_KEY)
        unknown = tuple(sorted({k for k in wanted if k not in BY_KEY}))
        return cls(keys=known, label=label, unknown_keys=unknown)

    @classmethod
    def from_creators(cls, names: Iterable[str], label: str) -> Scope:
        """Every registered source belonging to these creators (shows).

        Creator-keyed rather than key-keyed because that is the grain the
        corpus is stored at: ``content_item.creator`` is the show name, and a
        show publishes on both a YouTube channel and a podcast feed. Scoping
        to "The FPL Wire" must reach both.
        """
        wanted = {n.strip() for n in names if n and n.strip()}
        matched = frozenset(s.key for s in ALL_SOURCES if s.creator in wanted)
        seen = {s.creator for s in ALL_SOURCES if s.creator in wanted}
        unmatched = tuple(sorted(wanted - seen))
        return cls(keys=matched, label=label, unknown_keys=unmatched)

    def apply(self, sources: tuple[Source, ...] | None = None) -> tuple[Source, ...]:
        pool = fetchable() if sources is None else sources
        if self.keys is None:
            return tuple(pool)
        return tuple(s for s in pool if s.key in self.keys)

    def render(self) -> str:
        selected = self.apply()
        line = f"scope: {len(selected)} sources ({self.label})"
        if self.unknown_keys:
            line += f"; UNKNOWN and ignored: {', '.join(self.unknown_keys)}"
        return line


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What actually came back. Recorded verbatim, successes and failures."""

    key: str
    url: str
    http_status: int | None
    bytes_received: int
    items: int
    error: str | None = None
    skipped_reason: str | None = None
    #: Feed entries dropped because their date could not be parsed or carried no
    #: UTC offset. feeds.py refuses to guess a date, which is right -- but a
    #: silent drop is how 777 of 777 Megaphone items once vanished with the feed
    #: still reporting 200. A source losing items this way looks healthy on every
    #: other number here, so the count is carried out to the receipt.
    bad_dates: int = 0
    #: Items kept, but with no resolvable http(s) link: the feed offered no
    #: ``<link>``, no Atom alternate, no permalink GUID and no ``<enclosure>``.
    #: These are stored with a NULL url and a reason (see
    #: content_003_item_url_and_enclosure.sql), never with a GUID standing in
    #: for one. Counted here for the same reason ``bad_dates`` is: 353 of 372
    #: podcast items sat in the warehouse with a UUID in their url column and
    #: every number on this receipt said the source was healthy.
    no_url: int = 0
    #: Items DROPPED because they carry neither a GUID nor a link, so there is
    #: nothing stable to key ``item_id`` on. Every such item in one feed would
    #: hash to the same id and silently deduplicate onto a single row.
    no_identity: int = 0

    @property
    def ok(self) -> bool:
        return self.http_status == 200 and self.error is None


@dataclass
class ProbeReport:
    results: list[ProbeResult] = field(default_factory=list)

    def add(self, r: ProbeResult) -> None:
        self.results.append(r)

    @property
    def reached(self) -> list[ProbeResult]:
        return [r for r in self.results if r.ok]

    @property
    def failed(self) -> list[ProbeResult]:
        return [r for r in self.results if not r.ok and r.skipped_reason is None]

    @property
    def skipped(self) -> list[ProbeResult]:
        return [r for r in self.results if r.skipped_reason is not None]

    @property
    def bad_dates(self) -> int:
        return sum(r.bad_dates for r in self.results)

    @property
    def no_url(self) -> int:
        return sum(r.no_url for r in self.results)

    @property
    def no_identity(self) -> int:
        return sum(r.no_identity for r in self.results)

    def render(self) -> str:
        lines = [
            (
                f"{'status':>6}  {'bytes':>9}  {'items':>5}  {'baddate':>7}  "
                f"{'nourl':>5}  {'noid':>4}  key"
            )
        ]
        for r in sorted(self.results, key=lambda x: x.key):
            status = r.skipped_reason or (r.error or str(r.http_status))
            lines.append(
                f"{status:>6}  {r.bytes_received:>9}  {r.items:>5}  "
                f"{r.bad_dates:>7}  {r.no_url:>5}  {r.no_identity:>4}  {r.key}"
            )
        lines.append(
            f"-- reached {len(self.reached)}, failed {len(self.failed)}, "
            f"skipped-on-policy {len(self.skipped)}, of {len(self.results)}"
        )
        lines.append(
            f"-- items kept with no resolvable link (url NULL + reason): "
            f"{self.no_url}"
        )
        lines.append(
            f"-- items dropped for having neither a GUID nor a link: "
            f"{self.no_identity}"
        )
        # Kept LAST, where it has always been: tests read
        # render().splitlines()[-1] for this number.
        lines.append(
            f"-- entries dropped for an unparsable or offset-less date: "
            f"{self.bad_dates}"
        )
        return "\n".join(lines)
