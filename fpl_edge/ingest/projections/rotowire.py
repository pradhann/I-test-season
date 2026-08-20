"""Rotowire predicted Premier League lineups: a free xMins signal.

What it publishes
-----------------
``GET https://www.rotowire.com/soccer/lineups.php`` is server-rendered HTML --
no login, no API key, no JavaScript required -- carrying every fixture of the
next EPL matchday. Measured 2026-08-19: HTTP 200, 461,432 bytes, 10 fixture
boxes, 22 predicted starters each plus each club's injury list. Each side is
labelled either "Expected Lineup" (``lineup__status is-expected``) or, once
official team news lands ~60-75 minutes before kickoff, "Confirmed Lineup"
(``is-confirmed``). This is the classic minutes proxy: a named starter is the
strongest free evidence of ``xMins >= 60`` that exists before a deadline.

Permission
----------
``robots.txt`` (fetched live before every request by :func:`fetch`) allows
``User-agent: *`` everywhere except accounts/forum paths; ``/soccer/lineups.php``
is unrestricted. The site additionally publishes an ``llms.txt`` welcoming
automated readers to its lineups and projections pages provided nothing is
fabricated and content is attributed. Rows land in a private warehouse and are
never republished.

Parse contract (verified against the archived page)
----------------------------------------------------
::

    div.lineup__box                     one per fixture
      div.lineup__abbr                  team abbreviation, home first
      div.lineup__mteam                 full team name
      div.lineup__status is-expected|is-confirmed
      ul.lineup__list.is-home / .is-visit
        li.lineup__player > a[title]    full player name (display text may be
                                        abbreviated: "R. Calafiori")
        li.lineup__title  "Injuries"    boundary: entries after it are the
                                        injury list, flagged OUT / QUES in
                                        span.lineup__inj

Name resolution
---------------
Rotowire keys on nothing but names. Every name is resolved to the stable FPL
``code`` *within the roster of the team the page puts it on*, via
:func:`fpl_edge.ingest.player_mapping.normalize_name` (diacritics and stroke
letters folded: their "Martin Odegaard" matches our "Martin Ødegaard").
Anything that does not resolve uniquely is returned to the caller as
unresolved, never guessed and never silently dropped.
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

BASE = "https://www.rotowire.com"
LINEUPS_PATH = "/soccer/lineups.php"
POLITE_DELAY_S = 3.0

#: Rotowire's team abbreviations where they differ from FPL's short_name.
#: Everything not listed here matched FPL exactly on 2026-08-19. An unknown
#: abbreviation is an error, not a guess -- see :func:`to_lineup_rows`.
ABBR_TO_FPL = {"NOT": "NFO"}

#: Rotowire's player-level injury flags -> our ``certainty`` labels.
_INJ_LABEL = {"OUT": "out", "QUES": "questionable"}


class RotowireError(RuntimeError):
    """The lineups page is not the shape this module knows how to read."""


def fetch(*, client: object | None = None, delay_s: float = POLITE_DELAY_S) -> Fetched:
    """GET the lineups page, checking the live robots policy first."""
    import httpx

    url = f"{BASE}{LINEUPS_PATH}"
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
        raise RotowireError(f"{url} returned HTTP {resp.status_code}")
    payload = resp.content
    digest = hashlib.sha256(payload).hexdigest()
    out_dir = RAW_ROOT / "projections_rotowire"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"lineups_{fetched_at:%Y%m%dT%H%M%SZ}_{digest[:8]}.html"
    if not dest.exists():
        dest.write_bytes(payload)
    return Fetched(body=resp.text, fetched_at=fetched_at, sha256=digest,
                   body_path=dest, http_status=resp.status_code, from_cache=False)


@dataclass(frozen=True, slots=True)
class LineupEntry:
    """One name on one team sheet."""

    team_abbr: str
    opponent_abbr: str
    is_home: bool
    player_name: str
    position: str          # Rotowire's slot label: GK, DC, DMC, AMR, FW, ...
    predicted_start: bool  # True for the XI, False for the injury list
    certainty: str         # 'expected' | 'confirmed' | 'out' | 'questionable'


def parse_lineups(html: str) -> list[LineupEntry]:
    """The lineups page -> flat entries, one per named player.

    Fails loudly when a fixture box does not carry exactly two teams or a team
    sheet does not carry exactly eleven starters: a page that has changed shape
    must stop the ingest, not feed it a fraction of the truth.
    """
    soup = BeautifulSoup(html, "lxml")
    boxes = soup.select("div.lineup__box")
    if not boxes:
        raise RotowireError("no div.lineup__box on the page; layout has changed")
    entries: list[LineupEntry] = []
    for box in boxes:
        abbrs = [el.get_text(strip=True) for el in box.select(".lineup__abbr")]
        if len(abbrs) != 2 or not all(abbrs):
            raise RotowireError(f"fixture box has team abbrs {abbrs!r}, expected two")
        home_abbr, visit_abbr = abbrs
        statuses = [
            ("confirmed" if "is-confirmed" in el.get("class", []) else "expected")
            for el in box.select(".lineup__status")
        ]
        for side, team, opp in (("is-home", home_abbr, visit_abbr),
                                ("is-visit", visit_abbr, home_abbr)):
            ul = box.select_one(f"ul.lineup__list.{side}")
            if ul is None:
                raise RotowireError(f"{team} v {opp}: no ul.lineup__list.{side}")
            sheet_status = statuses[0 if side == "is-home" else -1] if statuses else "expected"
            in_injuries = False
            starters = 0
            for li in ul.find_all("li", recursive=False):
                classes = li.get("class", [])
                if "lineup__title" in classes:
                    in_injuries = True
                    continue
                if "lineup__player" not in classes:
                    continue
                link = li.find("a")
                name = (link.get("title") or link.get_text(strip=True)) if link else ""
                if not name:
                    raise RotowireError(f"{team}: player entry with no name")
                pos_el = li.select_one(".lineup__pos")
                pos = pos_el.get_text(strip=True) if pos_el else ""
                if in_injuries:
                    inj_el = li.select_one(".lineup__inj")
                    flag = inj_el.get_text(strip=True) if inj_el else ""
                    certainty = _INJ_LABEL.get(flag)
                    if certainty is None:
                        raise RotowireError(f"{team}: unknown injury flag {flag!r} for {name}")
                    entries.append(LineupEntry(team, opp, side == "is-home",
                                               name, pos, False, certainty))
                else:
                    starters += 1
                    entries.append(LineupEntry(team, opp, side == "is-home",
                                               name, pos, True, sheet_status))
            if starters != 11:
                raise RotowireError(
                    f"{team} v {opp}: {starters} starters parsed, expected 11. "
                    f"Either the page shape changed or the sheet is partial; "
                    f"neither is safe to ingest."
                )
    return entries


def resolve_teams(entries: list[LineupEntry], short_to_code: dict[str, int]) -> dict[str, int]:
    """Every abbreviation on the page -> FPL ``team_code``, or raise.

    ``short_to_code`` is dim_team's ``short_name -> team_code`` for the season.
    An abbreviation neither in that map nor in :data:`ABBR_TO_FPL` stops the
    ingest: a lineup attributed to the wrong club poisons twenty players at
    once, so there is no fuzzy fallback.
    """
    out: dict[str, int] = {}
    unknown: set[str] = set()
    for abbr in {e.team_abbr for e in entries}:
        fpl_short = ABBR_TO_FPL.get(abbr, abbr)
        code = short_to_code.get(fpl_short)
        if code is None:
            unknown.add(abbr)
        else:
            out[abbr] = code
    if unknown:
        raise RotowireError(
            f"team abbreviation(s) {sorted(unknown)} not in dim_team "
            f"{sorted(short_to_code)} even via ABBR_TO_FPL. Add the alias "
            f"explicitly rather than matching on similarity."
        )
    return out


def _resolve_name(name: str, roster: pd.DataFrame) -> int | None:
    """One provider name -> the unique matching code on one team's roster.

    Match ladder, strict to loose, stopping at the first rung with exactly one
    hit: exact ``first second``, exact ``web_name``, token-subset (every token
    of the shorter name appears in the longer). More than one hit at any rung
    is ambiguity, and ambiguity is None -- never a coin flip.
    """
    target = normalize_name(name)
    tokens = set(target.split())
    exact_full = roster.index[roster["norm_full"] == target]
    if len(exact_full) == 1:
        return int(roster.loc[exact_full[0], "code"])
    exact_web = roster.index[roster["norm_web"] == target]
    if len(exact_web) == 1:
        return int(roster.loc[exact_web[0], "code"])
    if len(exact_full) > 1 or len(exact_web) > 1:
        return None
    subset = roster.index[
        roster["norm_full"].map(lambda full: tokens <= set(full.split())
                                or set(full.split()) <= tokens)
    ]
    if len(subset) == 1:
        return int(roster.loc[subset[0], "code"])
    return None


def to_lineup_rows(
    entries: list[LineupEntry],
    *,
    season: str,
    gw: int,
    as_of: dt.datetime,
    rosters: pd.DataFrame,
    short_to_code: dict[str, int],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve names and shape rows for ``fact_predicted_lineup``.

    ``rosters`` is dim_player for the season: columns ``code``, ``team_code``,
    ``web_name``, ``first_name``, ``second_name``. Returns ``(rows,
    unresolved)``; unresolved names are handed back, never written.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware UTC")
    team_codes = resolve_teams(entries, short_to_code)
    prepared = rosters.assign(
        norm_full=(rosters["first_name"].fillna("") + " "
                   + rosters["second_name"].fillna("")).map(normalize_name),
        norm_web=rosters["web_name"].map(normalize_name),
    )
    rows: list[dict[str, object]] = []
    unresolved: list[dict[str, object]] = []
    for e in entries:
        team_code = team_codes[e.team_abbr]
        roster = prepared[prepared["team_code"] == team_code].reset_index(drop=True)
        code = _resolve_name(e.player_name, roster)
        if code is None:
            unresolved.append({"team_abbr": e.team_abbr, "player_name": e.player_name,
                               "position": e.position, "certainty": e.certainty})
            continue
        rows.append({"provider": "rotowire", "season": season, "gw": int(gw),
                     "code": code, "team_code": team_code,
                     "predicted_start": bool(e.predicted_start),
                     "certainty": e.certainty, "as_of": as_of})
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["as_of"] = pd.to_datetime(frame["as_of"], utc=True)
        dup = frame.duplicated(["code"], keep=False)
        if dup.any():
            # Two entries resolved onto one human (e.g. a name in both the XI
            # and the injury list, or two provider names collapsing to one
            # code). Writing both would violate the table's key with two
            # different truths; hand them all back instead.
            clash_codes = set(frame.loc[dup, "code"])
            clashed = frame[dup]
            frame = frame[~dup].reset_index(drop=True)
            for _, r in clashed.iterrows():
                unresolved.append({"team_abbr": "?", "player_name": f"code={r['code']}",
                                   "position": "", "certainty": r["certainty"],
                                   "reason": f"multiple entries resolved to code {r['code']}"})
            _ = clash_codes
    return frame, pd.DataFrame(unresolved)


def validate_fixture_pairs(
    entries: list[LineupEntry],
    fixture_pairs: set[tuple[int, int]],
    short_to_code: dict[str, int],
) -> None:
    """Every (home, away) on the page must be a fixture of the target GW.

    ``fixture_pairs`` is ``{(home_team_code, away_team_code), ...}`` from
    ``fact_fixture`` for the gameweek being written. Rotowire shows "the next
    matchday", which is usually but not necessarily the next FPL gameweek --
    a midweek cup slate or a blank GW would desynchronise the two, and this
    check is what turns that from silent corruption into a refusal.
    """
    page_pairs = {
        (short_to_code[ABBR_TO_FPL.get(e.team_abbr, e.team_abbr)],
         short_to_code[ABBR_TO_FPL.get(e.opponent_abbr, e.opponent_abbr)])
        for e in entries if e.is_home
    }
    rogue = page_pairs - fixture_pairs
    if rogue:
        raise RotowireError(
            f"{len(rogue)} fixture(s) on the lineups page are not fixtures of "
            f"the target gameweek: {sorted(rogue)}. The page is showing a "
            f"different matchday; refusing to write it under this gw."
        )
