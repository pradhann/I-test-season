"""Reaching external intel sources honestly, and recording what happened.

Two rules, and neither has an override flag:

1. **robots.txt is fetched and obeyed before the target URL is requested.** If
   robots.txt cannot be read, the source is treated as disallowed. "We could not
   read the crawl policy" is not permission.
2. **No bot-detection is circumvented.** The project's own User-Agent is sent,
   unchanged, on every request. Nothing here forges a browser UA, spoofs a TLS
   fingerprint, solves a challenge, or routes around a paywall. When a site
   refuses us, the refusal is recorded as a fact and reported to the user;
   the understat evaluation (docs/data_sources.md) already established this position for a site
   whose robots.txt is a blanket ``Disallow: /``, and this module holds the same
   line for press-conference sources.

The point of the table this writes is that "blocked" becomes a **dated
measurement someone can re-run**, rather than a claim in a docstring that rots.
A dossier that says "premierinjuries.com returned 403 on 2026-08-19" is more
useful, and more honest, than one that silently omits an injury section.

The one source that needs no permission
---------------------------------------
FPL's own ``bootstrap-static`` carries a ``scout_news_link`` per player, and in
the 2026-27 payload those point at club-published press-conference coverage --
arsenal.com's "every word of Mikel's post-Dortmund press conference", afcb.co.uk
surgery updates, and so on. That is FPL editorially attaching press-conference
material to a player, first-party, already ingested, with no licence question.
:func:`press_links_from_bootstrap` reads it, and the dossier surfaces the link
rather than the article body -- linking is not republishing.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fpl_edge.intel.bootstrap import ARCHIVE_DIR, read_archive
from fpl_edge.intel.items import IntelItem, IntelKind, SourceProbe, content_id

UTC = dt.timezone.utc

#: The same honest User-Agent the rest of the project uses. Sent unchanged.
USER_AGENT = "fpl-edge/0.1 (personal research; contact via repo owner)"

DEFAULT_TIMEOUT_S = 15.0


@dataclass(frozen=True, slots=True)
class Candidate:
    """An external source we might want, and what we already believe about it."""

    name: str
    url: str
    #: Set when the blocker is a licence or terms question rather than a
    #: technical one. Recorded here so the prober does not have to fetch a page
    #: it already knows it may not use.
    known_restriction: str | None = None


#: Press-conference and team-news candidates. The list is the survey, not an
#: aspiration: each entry was reached at least once and the outcome recorded.
#: docs/data_sources.md records the equivalent survey for injury
#: tables and reached the same conclusion for the same reasons.
PRESS_CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "premierleague.com",
        "https://www.premierleague.com/news",
    ),
    Candidate(
        "fantasyfootballscout",
        "https://www.fantasyfootballscout.co.uk/team-news/",
        known_restriction=(
            "Terms reserve team-news content to paying subscribers; the page markup "
            "carries a premium-members gate. Scraping it would be taking paid content."
        ),
    ),
    Candidate(
        "bbc.co.uk",
        "https://www.bbc.co.uk/sport/football/premier-league",
        known_restriction=(
            "robots.txt forbids scraping, crawling, systematic extraction, dataset "
            "building, TDM and RAG/agentic use of BBC content. Unambiguous no."
        ),
    ),
    Candidate(
        "premierinjuries.com",
        "https://www.premierinjuries.com/injury-table.php",
    ),
    Candidate(
        "physioroom.com",
        "https://www.physioroom.com/injury-table/premier-league/",
    ),
)


def _robots_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))


def check_robots(
    url: str, *, client: Any | None = None, timeout: float = DEFAULT_TIMEOUT_S
) -> tuple[bool | None, int | None, str]:
    """``(allowed, http status, note)`` for one URL's crawl policy.

    ``allowed`` is None when the policy could not be read, and callers must treat
    None as "do not fetch". Returning True on an unreadable robots.txt would make
    a Cloudflare challenge into implicit permission, which is exactly backwards:
    a site that will not show us its crawl policy is a site telling us to go away.
    """
    import httpx

    robots = _robots_url(url)
    owns_client = client is None
    client = client or httpx.Client(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    try:
        resp = client.get(robots)
    except Exception as exc:  # noqa: BLE001 - any transport failure means "unknown"
        return None, None, f"robots.txt unreachable: {type(exc).__name__}"
    finally:
        if owns_client:
            client.close()

    if resp.status_code == 404:
        # No robots.txt at all is the one case where absence really is
        # permission: RFC 9309 says a 404 means unrestricted.
        return True, resp.status_code, "no robots.txt (404); RFC 9309 treats this as allow-all"
    if resp.status_code != 200:
        return None, resp.status_code, f"robots.txt returned {resp.status_code}; treating as disallowed"

    parser = urllib.robotparser.RobotFileParser()
    parser.parse(resp.text.splitlines())
    allowed = parser.can_fetch(USER_AGENT, url)
    return bool(allowed), resp.status_code, ("robots.txt allows" if allowed else "robots.txt disallows")


def probe(
    candidate: Candidate, *, now: dt.datetime | None = None, timeout: float = DEFAULT_TIMEOUT_S
) -> SourceProbe:
    """Ask robots.txt, then -- only if allowed -- ask for the page itself.

    Records the real HTTP status of both requests. A candidate carrying a
    ``known_restriction`` is never fetched at all: the robots check still runs so
    the record is complete, but the content request is skipped, because the
    blocker is a licence rather than a crawl rule and fetching anyway would be
    the exact behaviour the restriction exists to prevent.
    """
    import httpx

    when = (now or dt.datetime.now(UTC)).astimezone(UTC)
    pid = content_id("probe", candidate.name, candidate.url, when.isoformat())
    allowed, robots_status, note = check_robots(candidate.url, timeout=timeout)

    if candidate.known_restriction:
        return SourceProbe(
            probe_id=pid, probed_at=when, source=candidate.name, url=candidate.url,
            verdict="disallowed", robots_status=robots_status, robots_allows=allowed,
            note=f"{candidate.known_restriction} ({note}); content not requested.",
        )
    if allowed is not True:
        return SourceProbe(
            probe_id=pid, probed_at=when, source=candidate.name, url=candidate.url,
            verdict="disallowed", robots_status=robots_status, robots_allows=allowed,
            note=f"{note}; content not requested.",
        )

    with httpx.Client(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        try:
            resp = client.get(candidate.url)
        except Exception as exc:  # noqa: BLE001
            return SourceProbe(
                probe_id=pid, probed_at=when, source=candidate.name, url=candidate.url,
                verdict="error", robots_status=robots_status, robots_allows=True,
                note=f"{type(exc).__name__}: {exc}",
            )
        size = len(resp.content)
        verdict, why = _classify(resp.status_code)
        return SourceProbe(
            probe_id=pid, probed_at=when, source=candidate.name, url=candidate.url,
            verdict=verdict, http_status=resp.status_code, robots_status=robots_status,
            robots_allows=True, bytes=size,
            note=f"{note}; {size:,} bytes returned; {why}",
        )


def _classify(status: int) -> tuple[str, str]:
    """Map an HTTP status onto a verdict, keeping "refused" apart from "moved".

    Lumping 404 in with 403 as "blocked" reads as hostility where there is none:
    physioroom returned 404 with a 65KB body on 2026-08-19, which is a site
    serving its own not-found page because the injury-table URL moved, not a
    site refusing us. The two need different follow-up -- one is a broken link to
    fix, the other is a door to stop knocking on.
    """
    if status == 200:
        return "usable", "fetched successfully"
    if status in (401, 402, 403, 429):
        return "blocked", "the site refused this client"
    if status in (404, 410):
        return "error", "the URL is gone or has moved; the candidate needs updating"
    if 500 <= status < 600:
        return "error", "the site is failing or down"
    return "error", "unexpected status"


def probe_all(
    candidates: tuple[Candidate, ...] = PRESS_CANDIDATES,
    *,
    now: dt.datetime | None = None,
) -> list[SourceProbe]:
    """Probe every candidate. Network-bound; call it from a collector, not a dossier."""
    return [probe(c, now=now) for c in candidates]


def press_links_from_bootstrap(
    *,
    season: str,
    directory: Path = ARCHIVE_DIR,
    until: dt.datetime | None = None,
) -> list[IntelItem]:
    """Press-conference and club-news links FPL itself attaches to players.

    ``published_at`` is the first poll at which the link appeared, for the same
    reason as set-piece order: FPL states no publication timestamp, so the first
    observation is the tightest honest upper bound.

    Only the URL and FPL's own headline-free context are stored. The linked
    article body is never fetched or copied -- pointing at a club's press
    conference is not republishing it.
    """
    first_seen: dict[tuple[int, str], dt.datetime] = {}
    meta: dict[tuple[int, str], tuple[str, int | None]] = {}
    for snap in read_archive(directory, until=until):
        by_id = snap.team_code_by_id()
        for e in snap.elements:
            link = e.get("scout_news_link")
            code = e.get("code")
            if not link or code is None:
                continue
            key = (int(code), str(link))
            first_seen.setdefault(key, snap.fetched_at)
            meta[key] = (str(e.get("web_name") or ""), by_id.get(int(e.get("team", 0))))

    items: list[IntelItem] = []
    for (code, link), when in sorted(first_seen.items(), key=lambda kv: kv[1]):
        name, team_code = meta[(code, link)]
        host = urllib.parse.urlsplit(link).netloc or "unknown host"
        items.append(
            IntelItem(
                item_id=content_id("press", season, code, link),
                published_at=when,
                observed_at=when,
                kind=IntelKind.PRESS_CONFERENCE,
                headline=f"FPL links {name or f'player {code}'} to club coverage on {host}",
                body=(
                    "FPL's own scout_news_link for this player. Usually a club press "
                    "conference write-up or a medical update. Dated to the first poll "
                    "that carried it, because FPL publishes no timestamp for this field."
                ),
                source="fpl_api:bootstrap-static#scout_news_link",
                source_url=link,
                season=season,
                player_code=int(code),
                team_code=team_code,
                confidence=0.9,
            )
        )
    return items


def probe_items(probes: list[SourceProbe], *, now: dt.datetime | None = None) -> list[IntelItem]:
    """Turn probe results into items so they appear in the same feed as the news."""
    when = (now or dt.datetime.now(UTC)).astimezone(UTC)
    return [
        IntelItem(
            item_id=content_id("probeitem", p.probe_id),
            published_at=p.probed_at,
            observed_at=max(p.probed_at, when),
            kind=IntelKind.SOURCE_PROBE,
            headline=p.render(),
            body=p.note,
            source=p.source,
            source_url=p.url,
            http_status=p.http_status,
            confidence=1.0,
        )
        for p in probes
    ]
