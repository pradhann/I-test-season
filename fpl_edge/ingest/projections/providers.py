"""The third-party projection providers we evaluated, and what each one is.

Every ``status`` below is an HTTP code this repo actually received on
2026-08-18/19 from a UK-routed connection with the project's honest User-Agent::

    fpl-edge/0.1 (personal research; contact via repo owner)

:func:`probe_all` re-runs the whole evaluation live, so the table can be
re-measured rather than believed. ``tests/unit/test_projections_providers.py``
asserts the registry's shape offline; the network re-measurement is marked
``network`` and is deselected by default.

The verdicts, in one line each
------------------------------
* **FPL Form** -- free CSV of per-player per-gameweek expected points, no
  account, no key. Ingested.
* **LiveFPL** -- free JSON of predicted effective ownership and top-10k /elite
  ownership. Not a points projection, but the other half of a rank decision.
  Ingested.
* **FPL Review** -- reachable, robots-permitted, but the projections live behind
  a session and a deliberately obfuscated client bundle. Not ingested.
* **Fantasy Football Hub**, **Fantasy Football Fix** -- subscription products.
  Not ingested.
* **FPL Statistics** -- did not resolve to a live server at all.
* **elfootball** -- domain does not exist in DNS.
* **Sofascore** -- 403 on ``/robots.txt`` itself, so the crawl policy is
  unreadable. Not ingested.
* **FotMob** -- ``robots.txt`` explicitly disallows ``/api/*`` for everyone but
  named search engines. Not ingested.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import httpx

from fpl_edge.ingest.http import USER_AGENT
from fpl_edge.ingest.projections.robots import fetch_policy

Verdict = Literal["ingested", "paywalled", "blocked", "dead", "forbidden"]

#: When the statuses recorded in this module were observed.
MEASURED_AT = dt.datetime(2026, 8, 19, 0, 0, tzinfo=dt.timezone.utc)


@dataclass(frozen=True, slots=True)
class Provider:
    """One candidate source, with the evidence for its verdict."""

    key: str
    name: str
    home: str
    publishes: str
    interface: str
    cost: str
    licence: str
    rate_limit: str
    covers_2026_27: bool | None
    verdict: Verdict
    reason: str
    probe_urls: tuple[str, ...] = ()
    measured_status: tuple[tuple[str, int | None], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def usable(self) -> bool:
        return self.verdict == "ingested"


PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="fplform",
        name="FPL Form",
        home="https://fplform.com",
        publishes=(
            "Per-player expected FPL points (xFPL/xP) for every fixture in a "
            "chosen gameweek range, plus the model's own probability that the "
            "player appears at all."
        ),
        interface=(
            "Plain HTML form POSTing to /export-fpl-form-data.php and returning "
            "text/csv with a Content-Disposition attachment. No account, no API "
            "key, no session cookie, no JavaScript required."
        ),
        cost="Free. The site asks for donations; nothing is gated.",
        licence=(
            "The export page states the data is copyright and MAY BE EXPORTED "
            "FOR PERSONAL USE in spreadsheets and other analysis tools; it "
            "forbids sharing the files or publishing the data, and asks for "
            "credit to 'FPL Form' with a link to https://fplform.com on any "
            "published derived analysis. Our use -- a private warehouse feeding "
            "one manager's own decisions -- is squarely inside that permission. "
            "The same page explicitly names 'comparing the accuracy of FPL Form "
            "predictions against other sources' as an intended use of the "
            "'Just Predicted Points' export, which is exactly what the ensemble "
            "does. Nothing here is republished."
        ),
        rate_limit=(
            "None published and none observed. The export is a full-season "
            "recompute, so this module fetches it at most once per run and "
            "sleeps POLITE_DELAY_S between calls."
        ),
        covers_2026_27=True,
        verdict="ingested",
        reason="HTTP 200, text/csv, 127,480 bytes covering GW1-GW8 of 2026-27.",
        probe_urls=("https://fplform.com/robots.txt",
                    "https://fplform.com/export-fpl-form-data"),
        measured_status=(("/robots.txt", 200), ("/export-fpl-form-data", 200),
                         ("/export-fpl-form-data.php (POST)", 200)),
        notes=(
            "robots.txt disallows /ajax/, /calcs-and-utilities/, /css/, /fonts/, "
            "/inc/, /phpmyadmin/ and four named files. The export endpoint is in "
            "none of those groups.",
            "The 'With Extra Columns' option returns X_pts_no_prob, X_prob and "
            "X_with_prob per gameweek, which decomposes their number into the "
            "same two factors our own model produces. That decomposition is why "
            "this export is worth more than a bare xP column: it lets their "
            "minutes opinion be scored separately from their points opinion.",
        ),
    ),
    Provider(
        key="livefpl",
        name="LiveFPL",
        home="https://www.livefpl.net",
        publishes=(
            "Predicted effective ownership for the coming gameweek "
            "(ownership + captaincy, so values exceed 1.0), plus realised "
            "ownership among the top 10k and among an 'elite' cohort. Also live "
            "rank, price-change progress and FDR. No expected points."
        ),
        interface=(
            "Static JSON on livefpl.us, fetched directly by the site's own "
            "unobfuscated front-end bundle: /predictedEOs/{gw}.json, "
            "/top10k.json, /elite.json, /planner/all_player_info.json."
        ),
        cost="Free.",
        licence=(
            "livefpl.us/robots.txt is 'User-agent: * / Allow: /' with no "
            "Disallow of any kind -- the most permissive policy of any provider "
            "evaluated. No terms page restricts automated reading."
        ),
        rate_limit=(
            "None published, none observed. These are small static files behind "
            "a CDN; one fetch per file per run."
        ),
        covers_2026_27=True,
        verdict="ingested",
        reason=(
            "HTTP 200 on every endpoint. predictedEOs/1.json carries 2026-27 GW1 "
            "effective ownership for 500+ players."
        ),
        probe_urls=("https://livefpl.us/robots.txt",
                    "https://livefpl.us/predictedEOs/1.json",
                    "https://livefpl.us/top10k.json",
                    "https://livefpl.us/elite.json",
                    "https://livefpl.us/planner/all_player_info.json"),
        measured_status=(("/robots.txt", 200), ("/predictedEOs/1.json", 200),
                         ("/top10k.json", 200), ("/elite.json", 200),
                         ("/planner/all_player_info.json", 200)),
        notes=(
            "www.livefpl.net/robots.txt is a 404 because that host redirects to "
            "plan.livefpl.net. The data is not on either host -- it is on "
            "livefpl.us, whose robots.txt is served and permissive. Checking the "
            "policy of the host you actually fetch from, not the host in the "
            "brand name, is the whole point.",
            "all_player_info.json carries the element_id -> FPL code map, which "
            "is how these rows survive next season's element_id reshuffle.",
        ),
    ),
    Provider(
        key="fplreview",
        name="FPL Review",
        home="https://fplreview.com",
        publishes=(
            "The 'Massive Data' expected-points model over a multi-gameweek "
            "horizon, a transfer planner and a solver. Widely used as the "
            "reference xP source in the FPL optimisation community."
        ),
        interface=(
            "React SPA. Every page -- /, /free-planner/, /massive-data-planner/, "
            "/team-planner/, /terms/ -- returns the same 2,111-byte shell with an "
            "empty <div id='root'>. There is no server-rendered data and no "
            "documented API."
        ),
        cost=(
            "A free tier exists behind an account; the Massive Data planner and "
            "the solver are Patreon-supported. We did not create an account, so "
            "we did not observe a price and do not quote one."
        ),
        licence=(
            "robots.txt allows 'User-agent: *' at 'Allow: /', but carries an "
            "explicit 'Disallow: /' for ClaudeBot, GPTBot, CCBot, Bytespider, "
            "Amazonbot, Applebot-Extended, Google-Extended and "
            "meta-externalagent, plus 'Content-Signal: search=yes,ai-train=no,"
            "use=reference'. The letter of the file permits our honest agent; "
            "the intent is plainly to keep automated AI consumption out."
        ),
        rate_limit="Not established.",
        covers_2026_27=None,
        verdict="blocked",
        reason=(
            "The 3.48 MB client bundle at /assets/index-*.js is run through a "
            "string-array obfuscator: identifiers are _0x-mangled, every URL "
            "literal is split into an indexed table, and the only readable "
            "network call is `xb+\"/session\",{credentials:...}` -- a "
            "cookie-authenticated session endpoint. Recovering the data URLs "
            "would mean deobfuscating a bundle that was obfuscated on purpose, "
            "and reaching them would mean holding an account session. Both are "
            "circumvention. We stopped."
        ),
        probe_urls=("https://fplreview.com/robots.txt",
                    "https://fplreview.com/free-planner/",
                    "https://fplreview.com/massive-data-planner/"),
        measured_status=(("/robots.txt", 200), ("/", 200), ("/free-planner/", 200),
                         ("/massive-data-planner/", 200), ("/team-planner/", 200),
                         ("/terms/", 200)),
        notes=(
            "This is the single most valuable source we could not take, and the "
            "honest recommendation is to take it by hand: sign up, use the free "
            "planner as a human, and paste the numbers in. The ingest path in "
            "this package accepts a CSV from disk for exactly that reason.",
        ),
    ),
    Provider(
        key="fantasyfootballhub",
        name="Fantasy Football Hub",
        home="https://www.fantasyfootballhub.co.uk",
        publishes=(
            "Player points predictions, an Opta stats suite, rate-my-team, "
            "AI transfer suggestions and expert team reveals."
        ),
        interface="Next.js app; predictions are behind a member login.",
        cost=(
            "Subscription. No price is quoted on any page reachable without an "
            "account -- /pricing is a 404 -- so we do not state one."
        ),
        licence="robots.txt allows / and disallows /login and /author.",
        rate_limit="Not established.",
        covers_2026_27=True,
        verdict="paywalled",
        reason=(
            "Homepage 200 (84,306 bytes) advertising the predictions; the "
            "predictions themselves require an account. Paying for and then "
            "scraping a subscriber product is not something this engine does."
        ),
        probe_urls=("https://www.fantasyfootballhub.co.uk/robots.txt",
                    "https://www.fantasyfootballhub.co.uk/"),
        measured_status=(("/robots.txt", 200), ("/", 200), ("/pricing", 404)),
    ),
    Provider(
        key="fantasyfootballfix",
        name="Fantasy Football Fix",
        home="https://www.fantasyfootballfix.com",
        publishes="Predicted points, 'FPL Plus' premium tools, ChatFPL, Elite XI.",
        interface="SPA behind a login; 'SIGN UP FREE' then 'Get Premium'.",
        cost=(
            "Freemium. The premium tier is the one that carries the projections; "
            "no price is on any page reachable without an account."
        ),
        licence="No robots.txt at all -- /robots.txt is a 404 from nginx.",
        rate_limit="Not established.",
        covers_2026_27=True,
        verdict="paywalled",
        reason=(
            "Homepage 200 (95,461 bytes). Everything of substance is behind "
            "account creation, which this engine will not do."
        ),
        probe_urls=("https://www.fantasyfootballfix.com/robots.txt",
                    "https://www.fantasyfootballfix.com/"),
        measured_status=(("/robots.txt", 404), ("/", 200), ("/plans/", 404)),
    ),
    Provider(
        key="fplstatistics",
        name="FPL Statistics",
        home="https://www.fplstatistics.co.uk",
        publishes="Price-change predictions and transfer-flow tables.",
        interface="Unknown -- never reached.",
        cost="Unknown.",
        licence="Unknown -- robots.txt never returned.",
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="dead",
        reason=(
            "Connection timed out on every attempt across three host forms "
            "(https://www., http://www., https:// apex) and two sessions. No TCP "
            "handshake, so not a block -- nothing answered."
        ),
        probe_urls=("https://www.fplstatistics.co.uk/robots.txt",
                    "https://www.fplstatistics.co.uk/"),
        measured_status=(("/robots.txt", None), ("/", None)),
        notes=(
            "Price prediction is in any case the one thing on this list we do "
            "not need from a third party: fact_player_state already carries "
            "transfers_in_event / transfers_out_event every poll, which is the "
            "input those sites model from.",
        ),
    ),
    Provider(
        key="elfootball",
        name="elfootball",
        home="https://elfootball.com",
        publishes="Unknown.",
        interface="n/a",
        cost="n/a",
        licence="n/a",
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="dead",
        reason=(
            "DNS resolution fails. elfootball.com, www.elfootball.com, "
            "elfootball.co.uk, elfootball.net and el-football.com all return "
            "NXDOMAIN. There is no host to ask."
        ),
        probe_urls=("https://elfootball.com/robots.txt",),
        measured_status=(("/robots.txt", None),),
    ),
    Provider(
        key="sofascore",
        name="Sofascore",
        home="https://www.sofascore.com",
        publishes="Match and player xG, ratings, lineups.",
        interface="Undocumented JSON API at api.sofascore.com/api/v1/...",
        cost="Free to view.",
        licence=(
            "Unreadable. GET /robots.txt returns HTTP 403 with a JSON error body "
            "on both www.sofascore.com and api.sofascore.com."
        ),
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="forbidden",
        reason=(
            "403 on the crawl policy itself and 403 on the API. An unreadable "
            "policy is a no, and getting past the 403 would mean impersonating a "
            "browser. Same rule that kept FBref out."
        ),
        probe_urls=("https://api.sofascore.com/robots.txt",
                    "https://api.sofascore.com/api/v1/unique-tournament/17/seasons"),
        measured_status=(("/robots.txt", 403), ("/api/v1/unique-tournament/17/seasons", 403)),
    ),
    Provider(
        key="fotmob",
        name="FotMob",
        home="https://www.fotmob.com",
        publishes="Match xG, shot maps, player ratings.",
        interface="JSON API at /api/...",
        cost="Free to view.",
        licence=(
            "robots.txt is served (200) and is explicit: the 'User-agent: *' "
            "group carries 'Disallow: /api/*', with '/api/*' re-allowed only for "
            "Googlebot, Bingbot, Qwantbot and AmazonAdBot. We are none of those."
        ),
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="forbidden",
        reason=(
            "Explicitly disallowed by robots.txt for our agent. Note that "
            "Python's urllib.robotparser answers True here because it ignores "
            "'*' in rule paths -- see robots.py. Trusting the stdlib would have "
            "produced a fetch the operator forbade."
        ),
        probe_urls=("https://www.fotmob.com/robots.txt",),
        measured_status=(("/robots.txt", 200), ("/api/leagues?id=47", 404)),
    ),
)

BY_KEY: dict[str, Provider] = {p.key: p for p in PROVIDERS}


@dataclass(frozen=True, slots=True)
class ProbeResult:
    provider: str
    url: str
    status: int | None
    bytes_: int
    content_type: str
    error: str | None
    robots_allows: bool | None


def probe_all(*, timeout: float = 20.0) -> list[ProbeResult]:
    """Re-measure every provider live. Read-only; nothing is written."""
    out: list[ProbeResult] = []
    policies: dict[str, object] = {}
    with httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT},
                      follow_redirects=True) as client:
        for provider in PROVIDERS:
            for url in provider.probe_urls:
                origin = "/".join(url.split("/", 3)[:3])
                if origin not in policies:
                    policies[origin] = fetch_policy(url, timeout=timeout)
                policy = policies[origin]
                path = "/" + url.split("/", 3)[3] if url.count("/") > 2 else "/"
                allowed = policy.allows(path) if policy.readable else None  # type: ignore[attr-defined]
                try:
                    resp = client.get(url)
                    out.append(ProbeResult(provider.key, url, resp.status_code,
                                           len(resp.content),
                                           resp.headers.get("content-type", ""),
                                           None, allowed))
                except Exception as exc:  # noqa: BLE001
                    out.append(ProbeResult(provider.key, url, None, 0, "",
                                           f"{type(exc).__name__}: {exc}"[:200], allowed))
    return out
