"""Premier Injuries: a free, explicit P(plays) for every flagged player.

Why this is the most useful free minutes source found
-----------------------------------------------------
Every other free availability signal is a *label* -- "OUT", "doubtful", a red
flag -- which a consumer then has to convert into a number by guessing. This
one publishes the number. The Status column of ``/injury-table.php`` takes the
values ``Ruled Out``, ``25%``, ``50%``, ``75%`` and ``100%``, which is a
probability the player features, stated by the publisher rather than inferred
by us. Measured on the GW1 2026-27 page: 91 flagged players across all 20
clubs, distributed 48 / 20 / 19 / 3 / 1.

That lands in ``fact_projection.p_appear`` -- NOT in ``xmins``. A 50% chance of
featuring and an expectation of 45 minutes are different claims about different
random variables, and this site makes the first one.

It is also a genuine second opinion on a quantity we already have. FPL's own
``chance_of_playing_next_round`` (ingested as ``fpl_ep``) is the same kind of
number from a different newsroom, and the two disagree often enough that
scoring them against each other is worth doing. Copying both and weighting them
by track record is the whole platform thesis, applied to minutes.

Scope discipline: only the next gameweek
-----------------------------------------
The page also carries a "Potential Return" date -- Eli Kroupi's read
``07/11/2026`` -- which is a real and tempting signal for gameweeks 2..N. It is
deliberately not expanded across gameweeks here. Turning a return date into a
per-gameweek availability curve requires assuming a recovery model and a
fixture-to-date mapping, and inventing an opinion the publisher did not state
is exactly what this platform exists not to do. The date is a candidate for a
future column; it is not a projection today.

Permission
----------
``https://www.premierinjuries.com/robots.txt`` is HTTP 200 and reads, in full::

    Sitemap: https://www.premierinjuries.com/sitemap_index.xml
    User-Agent: *
    Disallow:

An empty ``Disallow`` is RFC 9309's "nothing is disallowed" -- the most
permissive policy possible, and the site publishes a sitemap inviting crawlers.
One 275KB page per run, POLITE_DELAY_S before it. Content stays in a private
warehouse and is never republished.

Parse contract (verified against the archived page)
----------------------------------------------------
::

    tr.heading                     club name, then "TRACK", then a count
    tr.sub-head.team_<id>          column headers; ties <id> to the club above
    tr.player-row.team_<id>        one flagged player
      td[0] Player      td[1] Reason           td[2] Further Detail
      td[3] Potential Return        td[4] Condition        td[5] Status

Each ``td`` starts with a ``div.mob-title`` repeating the column name for the
mobile layout. It is stripped before reading the cell, or every value would
arrive prefixed with its own header.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from dataclasses import dataclass

import pandas as pd
from bs4 import BeautifulSoup

from fpl_edge.ingest.http import RAW_ROOT, USER_AGENT, Fetched
from fpl_edge.ingest.player_mapping import normalize_name
from fpl_edge.ingest.projections.robots import require_allowed

BASE = "https://www.premierinjuries.com"
TABLE_PATH = "/injury-table.php"
POLITE_DELAY_S = 3.0
PROVIDER = "premierinjuries"

#: Status string -> P(the player features). "Ruled Out" is 0.0 rather than
#: NULL: the publisher is making a claim, not declining to.
STATUS_TO_P_APPEAR: dict[str, float] = {
    "ruled out": 0.0,
    "25%": 0.25,
    "50%": 0.50,
    "75%": 0.75,
    "100%": 1.0,
}

#: Premier Injuries' club names -> FPL ``dim_team.name``. Every club on the
#: 2026-08-20 page is covered; an unknown club raises rather than dropping a
#: whole squad's worth of rows silently.
CLUB_TO_FPL = {
    "afc bournemouth": "Bournemouth",
    "arsenal": "Arsenal",
    "aston villa": "Aston Villa",
    "brentford": "Brentford",
    "brighton hove albion": "Brighton",
    "chelsea": "Chelsea",
    "coventry city": "Coventry City",
    "crystal palace": "Crystal Palace",
    "everton": "Everton",
    "fulham": "Fulham",
    "hull city": "Hull City",
    "ipswich town": "Ipswich Town",
    "leeds united": "Leeds",
    "liverpool": "Liverpool",
    "manchester city": "Man City",
    "manchester united": "Man Utd",
    "newcastle united": "Newcastle",
    "nottingham forest": "Nott'm Forest",
    "sunderland": "Sunderland",
    "tottenham hotspur": "Spurs",
    "west ham united": "West Ham",
    "wolverhampton wanderers": "Wolves",
    "burnley": "Burnley",
    "leicester city": "Leicester",
    "southampton": "Southampton",
    "sheffield united": "Sheffield Utd",
    "luton town": "Luton",
    "norwich city": "Norwich",
    "watford": "Watford",
    "west bromwich albion": "West Brom",
}


class PremierInjuriesError(RuntimeError):
    """The injury table is not the shape this module knows how to read."""


@dataclass(frozen=True, slots=True)
class InjuryEntry:
    """One flagged player on one club's list."""

    club: str            # Premier Injuries' club name, verbatim
    player_name: str
    reason: str          # "Ankle/Foot Injury", "Suspended", "Other", ...
    detail: str
    potential_return: str
    condition: str       # "Not Available" | "Currently Being Assessed" | ...
    status: str          # "Ruled Out" | "25%" | "50%" | "75%" | "100%"

    @property
    def p_appear(self) -> float | None:
        return STATUS_TO_P_APPEAR.get(self.status.strip().lower())


def fetch(*, client: object | None = None, delay_s: float = POLITE_DELAY_S) -> Fetched:
    """GET the injury table, checking the live robots policy first."""
    import httpx

    url = f"{BASE}{TABLE_PATH}"
    require_allowed(url)
    time.sleep(delay_s)
    owned = client is None
    client = client or httpx.Client(
        timeout=60.0, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    )
    fetched_at = dt.datetime.now(dt.timezone.utc)
    try:
        resp = client.get(url)  # type: ignore[union-attr]
    finally:
        if owned:
            client.close()  # type: ignore[union-attr]
    if resp.status_code != 200:
        raise PremierInjuriesError(f"{url} returned HTTP {resp.status_code}")
    payload = resp.content
    digest = hashlib.sha256(payload).hexdigest()
    out_dir = RAW_ROOT / f"projections_{PROVIDER}"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"injury_table_{fetched_at:%Y%m%dT%H%M%SZ}_{digest[:8]}.html"
    if not dest.exists():
        dest.write_bytes(payload)
    return Fetched(body=resp.text, fetched_at=fetched_at, sha256=digest,
                   body_path=dest, http_status=resp.status_code, from_cache=False)


#: Link text that is site chrome rather than content.
_CHROME_LINKS = {"see player page", "track"}


def _cell(td) -> str:
    """One table cell without its duplicated mobile header or its chrome.

    Re-parsed from ``str(td)`` rather than mutated in place: ``extract()`` is
    destructive, and stripping nodes out of the shared tree would make a second
    read of the same soup return something different from the first.

    Only *named* chrome links are removed. Stripping every ``<a>`` was the
    first version and it is a trap waiting for the day the site wraps player
    names in links to their profile pages -- at which point every name would
    silently become an empty string. (It would raise on the empty name rather
    than write a blank row, but "the parser broke" is a much worse diagnosis to
    reach from than "the parser skipped the links it was told to skip".)
    """
    clone = BeautifulSoup(str(td), "lxml")
    for node in clone.select(".mob-title, .track-player"):
        node.extract()
    for a in clone.select("a"):
        if a.get_text(" ", strip=True).lower() in _CHROME_LINKS:
            a.extract()
    return " ".join(clone.get_text(" ", strip=True).split())


def parse_table(html: str) -> list[InjuryEntry]:
    """The injury table -> one entry per flagged player.

    The club a row belongs to comes from the ``team_<id>`` class it shares with
    the ``tr.sub-head`` that follows its club heading -- not from "the last
    heading seen while walking the document". Those are the same thing until
    the site nests a table or reorders a block, at which point positional
    walking silently attributes one club's injuries to another.
    """
    soup = BeautifulSoup(html, "lxml")
    team_class_to_club: dict[str, str] = {}
    for heading in soup.select("tr.heading"):
        nxt = heading.find_next_sibling("tr")
        if nxt is None:
            continue
        classes = [c for c in (nxt.get("class") or []) if c.startswith("team_")]
        if not classes:
            continue
        # "Arsenal TRACK 2" -> "Arsenal": the TRACK button and its count are
        # part of the heading's text, not part of the club's name.
        label = heading.get_text(" ", strip=True).split(" TRACK")[0].strip()
        team_class_to_club[classes[0]] = label

    rows = soup.select("tr.player-row")
    if not rows:
        raise PremierInjuriesError(
            "no tr.player-row on the page; the injury table has changed shape"
        )
    entries: list[InjuryEntry] = []
    for row in rows:
        classes = [c for c in (row.get("class") or []) if c.startswith("team_")]
        if not classes:
            raise PremierInjuriesError(
                f"player row with no team_ class: {row.get_text(' ', strip=True)[:80]!r}"
            )
        club = team_class_to_club.get(classes[0])
        if club is None:
            raise PremierInjuriesError(
                f"{classes[0]} has no club heading. Refusing to attribute these "
                f"players to a club we cannot name."
            )
        tds = row.find_all("td")
        if len(tds) < 6:
            raise PremierInjuriesError(
                f"{club}: player row has {len(tds)} cells, expected at least 6"
            )
        values = [_cell(td) for td in tds[:6]]
        if not values[0]:
            raise PremierInjuriesError(f"{club}: player row with no name")
        entries.append(InjuryEntry(club, *values))
    return entries


def to_projection_rows(
    entries: list[InjuryEntry],
    *,
    season: str,
    gw: int,
    as_of: dt.datetime,
    rosters: pd.DataFrame,
    name_to_team_code: dict[str, int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve names within each club and shape ``fact_projection`` rows.

    ``rosters`` is dim_player for the season (``code``, ``team_code``,
    ``web_name``, ``first_name``, ``second_name``); ``name_to_team_code`` is
    dim_team's ``name -> team_code``.

    Rows carry ``p_appear`` only. ``xp``, ``xp_if_appears`` and ``xmins`` stay
    NULL, because this publisher says nothing about points or minutes and a
    zero would be a claim rather than an absence.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    prepared = rosters.assign(
        norm_full=(rosters["first_name"].fillna("") + " "
                   + rosters["second_name"].fillna("")).map(normalize_name),
        norm_web=rosters["web_name"].map(normalize_name),
    )
    # Reuse Rotowire's ladder rather than writing a second one: the two sites
    # get the same names wrong in the same ways (Ben/Benjamin, Tino/Valentino),
    # and two independently drifting matchers would be two sets of bugs.
    from fpl_edge.ingest.projections.rotowire import _resolve_name

    unknown_clubs = sorted(
        {e.club for e in entries
         if CLUB_TO_FPL.get(normalize_name(e.club)) not in name_to_team_code}
    )
    if unknown_clubs:
        raise PremierInjuriesError(
            f"club(s) {unknown_clubs} do not map into dim_team "
            f"{sorted(name_to_team_code)}. Add the alias to CLUB_TO_FPL "
            f"explicitly rather than matching on similarity -- a mis-mapped "
            f"club moves a whole squad's availability onto strangers."
        )

    rows: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for e in entries:
        p_appear = e.p_appear
        if p_appear is None:
            unresolved.append({"club": e.club, "player_name": e.player_name,
                               "status": e.status,
                               "reason": f"unrecognised Status {e.status!r}"})
            continue
        team_code = name_to_team_code[CLUB_TO_FPL[normalize_name(e.club)]]
        roster = prepared[prepared["team_code"] == team_code].reset_index(drop=True)
        code = _resolve_name(e.player_name, roster)
        if code is None:
            unresolved.append({"club": e.club, "player_name": e.player_name,
                               "status": e.status,
                               "reason": "no unique match on this club's roster"})
            continue
        rows.append({"provider": PROVIDER, "season": season, "gw": int(gw),
                     "code": code, "xp": None, "xp_if_appears": None,
                     "p_appear": float(p_appear), "xmins": None, "as_of": as_of,
                     "_name": e.player_name, "_club": e.club})

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, pd.DataFrame(unresolved)

    # Two names on one club's list resolving to one code is a resolution
    # failure, and picking the worse or the better probability would be a
    # decision nobody made. Both go back counted.
    dup = frame.duplicated(["code"], keep=False)
    if dup.any():
        for _, r in frame[dup].iterrows():
            unresolved.append({"club": r["_club"], "player_name": r["_name"],
                               "status": "", "reason":
                                   f"multiple entries resolved to code {r['code']}"})
        frame = frame[~dup]
    frame = frame.drop(columns=["_name", "_club"]).reset_index(drop=True)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
    return frame, pd.DataFrame(unresolved)
