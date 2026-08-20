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
* **Rotowire** -- free server-rendered predicted/confirmed EPL lineups,
  robots-permitted, with an ``llms.txt`` welcoming automated readers. The
  best free xMins proxy found. Ingested.
* **FPL's own ep_next** -- the official API's expected points for the next
  gameweek; crude but free, universal, and the natural ensemble baseline.
  Ingested.
* **Fantasy Football Scout** -- robots fully permissive, but the free
  team-news page carries ~15 teaser names for 20 clubs; the full predicted
  XIs are member content. Not ingested.
* **WhoScored** -- ``robots.txt`` disallows ``/Predictions/`` for everyone,
  which is precisely the projections product. Not ingested.
* **Community GitHub projections** (fpl-projections-site) -- free raw JSON of
  Monte-Carlo xPts percentiles, but anonymous, unlicensed and days old.
  Watchlist, not ingested.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import httpx

from fpl_edge.ingest.http import USER_AGENT
from fpl_edge.ingest.projections.robots import fetch_policy

Verdict = Literal["ingested", "paywalled", "blocked", "dead", "forbidden", "watchlist"]

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
    Provider(
        key="rotowire",
        name="Rotowire soccer lineups",
        home="https://www.rotowire.com/soccer/lineups.php",
        publishes=(
            "Predicted starting XIs for every fixture of the next EPL matchday, "
            "upgraded to confirmed XIs when official team news lands ~60-75 min "
            "before kickoff, plus each club's OUT/QUES injury list. The classic "
            "free xMins proxy: a named starter is the strongest pre-deadline "
            "evidence of 60+ minutes that exists."
        ),
        interface=(
            "Server-rendered HTML at /soccer/lineups.php; no login, no key, no "
            "JavaScript. Parse path: div.lineup__box per fixture, .lineup__abbr "
            "team codes, ul.lineup__list.is-home/.is-visit with li.lineup__player "
            "a[title] full names; li.lineup__title 'Injuries' separates the XI "
            "from the OUT/QUES list; .lineup__status is-expected|is-confirmed."
        ),
        cost="Free. Premium exists for other sports tools; the lineups page is open.",
        licence=(
            "robots.txt allows User-agent: * everywhere relevant (Disallows are "
            "accounts/forum/legacy paths only). The site also publishes an "
            "llms.txt that explicitly welcomes automated readers to its lineup "
            "and projection pages, asking only that content be attributed and "
            "never fabricated. Content is copyright Roto Sports Inc.; rows land "
            "in a private warehouse and are never republished."
        ),
        rate_limit=(
            "None published. One 450KB page per run; POLITE_DELAY_S=3.0 before "
            "the request. Refreshing hourly on deadline day is well within "
            "politeness."
        ),
        covers_2026_27=True,
        verdict="ingested",
        reason=(
            "HTTP 200, 461,432 bytes, 10 EPL fixture boxes with 11 predicted "
            "starters per side (220 names) plus injury lists, all 2026-27 GW1 "
            "fixtures. Names resolve onto FPL codes within each club's roster."
        ),
        probe_urls=("https://www.rotowire.com/robots.txt",
                    "https://www.rotowire.com/soccer/lineups.php"),
        measured_status=(("/robots.txt", 200), ("/soccer/lineups.php", 200),
                         ("/llms.txt", 200)),
        notes=(
            "Rotowire's team abbreviations differ from FPL's short_name in one "
            "case measured so far (NOT vs NFO); rotowire.ABBR_TO_FPL holds the "
            "aliases and an unknown abbreviation refuses rather than fuzzy-matches.",
            "The page shows the next MATCHDAY, not necessarily the next FPL "
            "gameweek; validate_fixture_pairs() checks every pair on the page "
            "against fact_fixture for the target gw before anything is written.",
        ),
    ),
    Provider(
        key="fpl_ep",
        name="FPL official ep_next",
        home="https://fantasy.premierleague.com/api/bootstrap-static/",
        publishes=(
            "The game's own expected points for the next gameweek (ep_next) for "
            "every element, plus chance_of_playing_next_round, the availability "
            "percentage behind the flags. Form-derived and crude, but universal, "
            "instant, and published by the party that also sets prices."
        ),
        interface=(
            "The bootstrap-static JSON this repo already polls. ep_next / ep_this "
            "are strings on each element; chance_of_playing_next_round is an "
            "integer or null (null + status 'a' means unflagged, i.e. 100%)."
        ),
        cost="Free.",
        licence="The official public API, already relied on for prices and flags.",
        rate_limit="Shared with the existing bootstrap polling; no extra load.",
        covers_2026_27=True,
        verdict="ingested",
        reason=(
            "Already reachable (the repo's price polls read the same document). "
            "Stored under provider 'fpl_ep' so its track record is measured "
            "exactly like any third party's, and because a projection must be "
            "stored at fetch time to be scoreable at all."
        ),
        probe_urls=("https://fantasy.premierleague.com/api/bootstrap-static/",),
        measured_status=(("/api/bootstrap-static/", 200),),
        notes=(
            "ep_this is deliberately not ingested: once a gameweek kicks off it "
            "describes the past. ep_next always refers to the decision ahead.",
            "ep_next already includes FPL's own availability scaling, so it is "
            "an xp, not an xp_if_appears.",
        ),
    ),
    Provider(
        key="fantasyfootballscout",
        name="Fantasy Football Scout",
        home="https://www.fantasyfootballscout.co.uk",
        publishes=(
            "Team news, predicted lineups for all 20 clubs, and editorial. The "
            "historical reference source for predicted XIs."
        ),
        interface=(
            "WordPress; /team-news is server-rendered. The free page carries 20 "
            "club headers but only ~15 player names in total -- one teaser per "
            "club -- with the full predicted XIs marked member content."
        ),
        cost="Freemium; full predicted lineups require membership.",
        licence="robots.txt is fully permissive (User-agent: * / empty Disallow).",
        rate_limit="Not established.",
        covers_2026_27=True,
        verdict="paywalled",
        reason=(
            "HTTP 200 on /team-news (563,862 bytes, 'FPL 2026/27 - Predicted "
            "Line-ups') but only 15 player-name nodes across 20 clubs; the XIs "
            "themselves are behind membership. Rotowire gives the same signal "
            "free, so there is nothing here worth paying-and-scraping for."
        ),
        probe_urls=("https://www.fantasyfootballscout.co.uk/robots.txt",
                    "https://www.fantasyfootballscout.co.uk/team-news/"),
        measured_status=(("/robots.txt", 200), ("/team-news/", 200)),
    ),
    Provider(
        key="whoscored",
        name="WhoScored",
        home="https://www.whoscored.com",
        publishes="Ratings, detailed match stats, and predicted lineups under /Predictions/.",
        interface="JS-heavy site; predictions live under /Predictions/.",
        cost="Free to view.",
        licence=(
            "robots.txt disallows /Predictions/ (and /predictions/) for "
            "User-agent: * -- the one section that carries the projections."
        ),
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="forbidden",
        reason=(
            "The crawl policy permits most of the site but explicitly closes "
            "/Predictions/ to everyone. The part we want is the part they "
            "forbid; ToS additionally prohibits automated extraction."
        ),
        probe_urls=("https://www.whoscored.com/robots.txt",),
        measured_status=(("/robots.txt", 200),),
    ),
    Provider(
        key="premierinjuries",
        name="Premier Injuries",
        home="https://www.premierinjuries.com/injury-table.php",
        publishes=(
            "Every flagged Premier League player, with an EXPLICIT probability "
            "the player features: the Status column takes 'Ruled Out', '25%', "
            "'50%', '75%' or '100%'. Also the reason, a dated news quote, a "
            "potential return date, and a Condition label."
        ),
        interface=(
            "Server-rendered HTML table at /injury-table.php; no login, no key, "
            "no JavaScript. Parse path: tr.heading names the club, the "
            "tr.sub-head that follows carries the team_<id> class, and every "
            "tr.player-row.team_<id> is one flagged player with six cells "
            "(Player, Reason, Further Detail, Potential Return, Condition, "
            "Status), each prefixed by a div.mob-title that must be stripped."
        ),
        cost="Free.",
        licence=(
            "robots.txt is HTTP 200 and reads, in full, 'Sitemap: ... / "
            "User-agent: * / Disallow:'. An empty Disallow is RFC 9309's "
            "'nothing is disallowed' -- the most permissive policy of any "
            "provider evaluated, and the site publishes a sitemap index "
            "inviting crawlers. Content is copyright; rows land in a private "
            "warehouse and are never republished."
        ),
        rate_limit=(
            "None published. One 275KB page per run, POLITE_DELAY_S=3.0 before "
            "the request."
        ),
        covers_2026_27=True,
        verdict="ingested",
        reason=(
            "HTTP 200, 275,041 bytes, 91 flagged players across all 20 clubs "
            "for GW1 2026-27, Status distributed Ruled Out 48 / 25% 20 / "
            "50% 19 / 75% 3 / 100% 1. 86 of 91 resolve onto FPL codes."
        ),
        probe_urls=("https://www.premierinjuries.com/robots.txt",
                    "https://www.premierinjuries.com/injury-table.php"),
        measured_status=(("/robots.txt", 200), ("/injury-table.php", 200)),
        notes=(
            "The only free source found that publishes P(plays) as a NUMBER "
            "rather than as a label a consumer has to convert by guessing. It "
            "lands in p_appear and nowhere else -- a 50% chance of featuring "
            "is not an expectation of 45 minutes.",
            "The 'Potential Return' date is a real signal for gameweeks 2..N "
            "and is deliberately not expanded across them: doing so needs a "
            "recovery model and a fixture-date mapping, which is inventing an "
            "opinion the publisher never stated.",
        ),
    ),
    Provider(
        key="gh_fplbench",
        name="fplbench (GitHub, PascalAI2024)",
        home="https://github.com/PascalAI2024/fplbench",
        publishes=(
            "A leakage-safe player-gameweek panel with three heads: predicted "
            "MINUTES (pred_minutes), Defensive Contribution probability "
            "(pred_defcon) and points (pred_points, pred_points_decomposed, "
            "e_points_final), plus p10/p90 bands. Self-scoring: a CI job joins "
            "each gameweek's predictions to official actuals and appends the "
            "result to RESULTS.md."
        ),
        interface=(
            "raw.githubusercontent.com CSV at "
            "outputs/predictions/gw{gw}_{season}.csv. Deterministic path, no "
            "discovery call needed. Keys on `player_code` -- FPL's stable "
            "cross-season code -- so no element_id remap and no name matching."
        ),
        cost="Free.",
        licence=(
            "MIT for the code, with an explicit data note in LICENSE: "
            "'Underlying player data is owned by the Premier League and its "
            "data providers... Research and personal modelling use only.' The "
            "cleanest licence position of any community feed found. "
            "raw.githubusercontent.com serves 404 for /robots.txt, which RFC "
            "9309 makes 'no policy published'."
        ),
        rate_limit="GitHub raw: generous. One fetch per run.",
        covers_2026_27=True,
        verdict="ingested",
        reason=(
            "HTTP 200, 118,253 bytes, 587 players for GW1 2026-27 with "
            "pred_minutes and e_points_final on every row. All 587 codes "
            "validated against dim_player; 0 unresolved."
        ),
        probe_urls=(
            "https://raw.githubusercontent.com/PascalAI2024/fplbench/main/outputs/predictions/gw1_2026-27.csv",
        ),
        measured_status=(("outputs/predictions/gw1_2026-27.csv", 200),
                         ("raw.githubusercontent.com/robots.txt", 404)),
        notes=(
            "Publishes several points heads. We store e_points_final only -- "
            "their own board's ranking number, and the only head that includes "
            "the Defensive Contribution points that score under the 2026-27 "
            "rules. Storing two heads from one publisher under two provider "
            "names would let one source vote twice in the ensemble.",
            "Also carries the FPL API's own ep_next. Not taken from here: we "
            "read it first-hand under provider 'fpl_ep', and second-hand would "
            "double-count the same number.",
        ),
    ),
    Provider(
        key="gh_blueladd",
        name="fpl-projections (GitHub, blueladd11)",
        home="https://github.com/blueladd11-commits-tocode/fpl-projections",
        publishes=(
            "xP and xMins per player for the next gameweek, a six-gameweek xP "
            "horizon in one semicolon-joined cell, P(starts), a standard "
            "deviation per projection, and a sidecar meta.json carrying "
            "deadline_utc, generated_at_utc and hours_before_deadline."
        ),
        interface=(
            "raw.githubusercontent.com CSV under out/, filename stamped with "
            "the build time (projections_gw1_20260818T174829Z_gw1.csv), so the "
            "path is discovered via one api.github.com contents listing per "
            "run and the newest stamp wins. Keys on `element` (per-season id), "
            "remapped through dim_player."
        ),
        cost="Free.",
        licence=(
            "NO LICENSE FILE. Public and freely fetchable, and the README's "
            "stated purpose is a publicly auditable accuracy record, but "
            "nothing grants reuse rights. Private-warehouse read only, never "
            "republished. Ranked below the MIT feeds for exactly this reason."
        ),
        rate_limit=(
            "api.github.com is 60 requests/hour unauthenticated; discovery "
            "costs one per run. raw.githubusercontent is generous."
        ),
        covers_2026_27=True,
        verdict="ingested",
        reason=(
            "HTTP 200, 47,526 bytes, 469 eligible players for GW1 2026-27 with "
            "xp, xmins, p_start and a six-gameweek horizon, expanded to 2,814 "
            "rows. 0 unresolved element_ids."
        ),
        probe_urls=(
            "https://api.github.com/repos/blueladd11-commits-tocode/fpl-projections/contents/out",
        ),
        measured_status=(("contents/out", 200), ("out/projections_gw1_*.csv", 200)),
        notes=(
            "Rebuilt hourly by a GitHub Action, with every pre-deadline "
            "snapshot kept under out/archive/. The freshest cadence of any "
            "free source measured, and the only one whose past projections are "
            "retrievable WITH their publication timestamps -- which is what a "
            "track record needs and what every self-overwriting site denies.",
            "Publishes p_start, which is P(STARTS) and not P(any minutes). It "
            "is deliberately not written to p_appear: a 60-minute substitute "
            "has p_start near 0 and p_appear near 1, and conflating them would "
            "make this source look catastrophically wrong about bench players.",
        ),
    ),
    Provider(
        key="sportsgambler",
        name="SportsGambler predicted lineups",
        home="https://www.sportsgambler.com/lineups/football/england-premier-league/",
        publishes="Predicted and confirmed XIs for every Premier League fixture.",
        interface=(
            "Server-rendered HTML index with ten fixture rows; each XI is "
            "behind a per-fixture 'view lineups' page, so a full read costs "
            "eleven requests rather than one."
        ),
        cost="Free.",
        licence=(
            "robots.txt is HTTP 200 and permits everything relevant -- the "
            "Disallows are /admin/, /r/, /odds.php, /betting-sites/go/* and "
            "/team-news_player.php. It additionally publishes "
            "sitemap-lineups.xml, which is an explicit invitation to crawl the "
            "lineup pages."
        ),
        rate_limit="None published.",
        covers_2026_27=True,
        verdict="watchlist",
        reason=(
            "HTTP 200, 174,590 bytes, 20 lineup-row entries and 10 fixture "
            "toggles on the index -- but the index's toggle-content blocks are "
            "empty, so the XIs need ten further per-fixture fetches. Viable "
            "and permitted; not ingested yet because Rotowire already supplies "
            "the same signal for one request, and a second lineup source earns "
            "its eleven requests only once the ensemble can measure whether it "
            "disagrees usefully."
        ),
        probe_urls=("https://www.sportsgambler.com/robots.txt",
                    "https://www.sportsgambler.com/lineups/football/england-premier-league/"),
        measured_status=(("/robots.txt", 200),
                         ("/lineups/football/england-premier-league/", 200)),
    ),
    Provider(
        key="fantasyfootballpundit",
        name="Fantasy Football Pundit",
        home="https://www.fantasyfootballpundit.com",
        publishes="FPL team news, predicted lineups and points projections.",
        interface="Unknown -- never reached.",
        cost="Unknown.",
        licence="Unreadable: GET /robots.txt returns HTTP 403.",
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="forbidden",
        reason=(
            "403 on /robots.txt and 403 on /fpl-team-news/ (52-byte body). An "
            "unreadable crawl policy is a no, and getting past the 403 would "
            "mean impersonating a browser. Same rule that kept FBref and "
            "Sofascore out."
        ),
        probe_urls=("https://www.fantasyfootballpundit.com/robots.txt",),
        measured_status=(("/robots.txt", 403), ("/fpl-team-news/", 403)),
    ),
    Provider(
        key="fbref",
        name="FBref",
        home="https://fbref.com",
        publishes=(
            "Per-90 and per-match underlying stats (xG, xAG, progressive "
            "actions, minutes) for every Premier League player -- the input a "
            "points projection would be built FROM, rather than a projection."
        ),
        interface="Server-rendered HTML tables; no documented API.",
        cost="Free to view.",
        licence="Unreadable: GET /robots.txt returns HTTP 403.",
        rate_limit="n/a",
        covers_2026_27=None,
        verdict="forbidden",
        reason=(
            "403 on /robots.txt and 403 on /en/comps/9/Premier-League-Stats "
            "(5,625-byte challenge body). Getting past it would mean "
            "impersonating a browser or a TLS fingerprint, which this package "
            "does not do. Listed for completeness: this repo settled the same "
            "question elsewhere and the answer has not changed."
        ),
        probe_urls=("https://fbref.com/robots.txt",),
        measured_status=(("/robots.txt", 403),
                         ("/en/comps/9/Premier-League-Stats", 403)),
    ),
    Provider(
        key="derekkuang",
        name="Fantasy-Premier-League (GitHub, derekkuang)",
        home="https://github.com/derekkuang/Fantasy-Premier-League",
        publishes=(
            "A from-scratch Dixon-Coles match engine and FPL prediction "
            "system, with a web interface."
        ),
        interface="Public GitHub repository.",
        cost="Free to view.",
        licence=(
            "EXPLICITLY CLOSED. LICENSE reads 'All rights reserved... No "
            "licence, express or implied, is granted to any person to use, "
            "copy, modify, merge, publish, distribute... Viewing this "
            "repository does not grant any right to reuse its contents.'"
        ),
        rate_limit="n/a",
        covers_2026_27=True,
        verdict="forbidden",
        reason=(
            "The author has written down, in the file whose job it is to say "
            "so, that we may not use this. Recorded here rather than quietly "
            "skipped so nobody rediscovers it in three months and wonders why "
            "a live, current, free feed is missing from the inventory."
        ),
        probe_urls=(
            "https://api.github.com/repos/derekkuang/Fantasy-Premier-League/license",
        ),
        measured_status=(("/license", 200),),
    ),
    Provider(
        key="fpl_projections_site",
        name="Community GitHub projections (fpl-projections-site)",
        home="https://github.com/Juz92backup/fpl-projections-site",
        publishes=(
            "Monte-Carlo xPts for 564 players over a 5-GW horizon: mean and "
            "p10/p25/median/p75/p90 per player per gameweek, 10,000 iterations, "
            "keyed on what appear to be stable FPL codes. Also a manifest with "
            "run timestamp and season/gameweek labels."
        ),
        interface=(
            "raw.githubusercontent.com JSON: data/manifest.json (run_at, season, "
            "gameweek), data/horizons.json (per-GW percentile rows), "
            "data/players.json, data/samples-h{1,3,5}.bin. Versioned by git "
            "history, so every past projection is retrievable with its date."
        ),
        cost="Free.",
        licence=(
            "NO LICENSE file. Anonymous single author, 'derived outputs only'. "
            "Public and fetchable, but nothing grants reuse, and the account "
            "name ('...backup') suggests it may vanish."
        ),
        rate_limit="GitHub raw: generous; one fetch per run is nothing.",
        covers_2026_27=True,
        verdict="watchlist",
        reason=(
            "HTTP 200 on every data file; manifest run_at 2026-08-18T03:00Z, "
            "season 2026-27, gameweek 1 -- genuinely current. Not ingested yet: "
            "no licence, no track record, unknown author, and the ensemble "
            "would give it weight 0 until a season of history exists anyway. "
            "Re-check after a few gameweeks of published runs."
        ),
        probe_urls=(
            "https://raw.githubusercontent.com/Juz92backup/fpl-projections-site/main/data/manifest.json",
            "https://raw.githubusercontent.com/Juz92backup/fpl-projections-site/main/data/horizons.json",
        ),
        measured_status=(("data/manifest.json", 200), ("data/horizons.json", 200)),
        notes=(
            "GitHub repo search ('fpl projections', 2026-08-19) surfaced ~12 "
            "active personal projection engines but only this one publishing "
            "machine-readable weekly outputs; theFPLkiwi, the best-known "
            "historical community sheet, is dormant (last push 2023-12).",
        ),
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
