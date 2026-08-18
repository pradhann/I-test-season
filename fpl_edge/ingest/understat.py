"""Understat: shot-level xG, and why this module refuses to fetch by default.

The measurement, taken 2026-08-18
---------------------------------
Three things were established by actually hitting the site, and all three
matter more than the code below:

1. ``GET https://understat.com/robots.txt`` -> **HTTP 200**, body::

       User-agent: *
       Disallow: /

   Understat forbids automated crawling of every path on the site. There is no
   carve-out, no crawl-delay, no sitemap. Anything this module does by default
   has to respect that.

2. ``GET https://understat.com/league/EPL/2026`` -> **HTTP 200**, but the page
   is titled "EPL xG Table and Scorers for the **2025/2026** season" and its
   season ``<select>`` offers 2014..2025 only. Requesting 2026 or 2027 silently
   serves the 2025/26 page. **Understat has no 2026-27 season.** Confirmed
   independently: ``soccerdata.Understat(seasons="2627").read_schedule()``
   returned a DataFrame of shape ``(3, 0)`` -- no columns, no data.

   So Understat cannot inform the GW1 decision on 2026-08-21 under any
   licence or technique. It will populate as 2026-27 matches are played.

3. ``GET https://understat.com/match/28778`` -> **HTTP 200**, 30,639 bytes, but
   the body now embeds only ``var match_info = JSON.parse(...)`` -- 26
   match-level fields (team xG, shots, shots on target, deep completions,
   PPDA). The ``shotsData`` variable that used to carry shot-by-shot xG on this
   page **is no longer present** (``grep -c shotsData`` = 0). Shot-level data
   has moved behind a separate request that the HTML no longer exposes.

On soccerdata
-------------
``soccerdata==1.9.1`` does retrieve Understat successfully -- 380 scheduled
matches in 0.8s and 9,524 shot events in 250.7s for 2025-26. It manages this
because it ships ``tls_requests``, which loads a native library
(``tls-client-darwin-arm64``) to impersonate a browser's TLS fingerprint.

That is a bot-detection circumvention tool. Combined with a blanket
``Disallow: /``, wiring it in as a dependency is not something this module will
do on its own initiative. It is recorded in ``docs/data_sources.md`` so the
account holder can make that call knowingly rather than inherit it from a
transitive dependency.

What this module therefore provides
-----------------------------------
* :func:`parse_match_info` -- a pure parser over already-saved HTML. No network,
  no licence question, fully tested offline.
* :func:`fetch_match` -- gated behind :func:`robots_allows`, which reads
  Understat's live ``robots.txt`` and refuses when it disallows. There is no
  bypass flag that forges a User-Agent or a TLS fingerprint; the only override
  is an explicit argument the human sets, and it still fetches politely with
  the project's honest User-Agent and a delay.

Landing it in the warehouse
---------------------------
Deliberately not done here. Understat's unit is *team-match xG*, and the
warehouse has no table for it -- ``fact_player_fixture`` is per player and
already carries FPL's own ``expected_goals``. Adding ``fact_team_match_xg``
belongs to whoever owns ``schema.sql``, so this module returns tidy frames and
records provenance rather than inventing a destination.
"""

from __future__ import annotations

import codecs
import datetime as dt
import json
import re
import time
import urllib.robotparser
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import USER_AGENT, Fetched
from fpl_edge.ingest.odds import TextFetcher

BASE = "https://understat.com"

#: Understat's season parameter is the *starting* year: 2025 -> 2025/26.
#: Verified against the site's own season <select> on 2026-08-18, whose maximum
#: option was 2025.
LATEST_SEASON_OBSERVED = 2025

#: Seconds between requests when fetching is explicitly enabled. Understat
#: publishes no Crawl-delay (it publishes a blanket Disallow instead), so this
#: is a self-imposed floor rather than a stated limit.
POLITE_DELAY_S = 3.0


class RobotsDisallowed(RuntimeError):
    """The source's robots.txt forbids the fetch we were about to make."""


def season_start_year(season: str) -> int:
    """``"2026-27"`` -> ``2026``, Understat's URL convention."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", season)
    if not m:
        raise ValueError(f"season must look like '2026-27', got {season!r}")
    return int(m.group(1))


def season_is_available(season: str) -> bool:
    """Whether Understat had published this season as of the last measurement.

    Cheap guard so callers do not spend requests discovering that 2026-27
    silently redirects to the 2025-26 page.
    """
    return season_start_year(season) <= LATEST_SEASON_OBSERVED


def robots_allows(path: str = "/match/1", *, user_agent: str = USER_AGENT) -> bool:
    """Ask Understat's live robots.txt whether ``path`` may be fetched.

    Fetched fresh rather than hard-coded: if Understat ever relaxes the rule
    this returns True on its own, and if the site is unreachable we fail
    closed.
    """
    rp = urllib.robotparser.RobotFileParser()
    rp.set_url(f"{BASE}/robots.txt")
    try:
        rp.read()
    except Exception:  # noqa: BLE001 - any failure to read the policy means "no"
        # Fail closed. robotparser.read() can fail in a dozen ways (DNS, TLS,
        # a redirect loop, a malformed body); none of them is permission.
        return False
    return bool(rp.can_fetch(user_agent, f"{BASE}{path}"))


# --------------------------------------------------------------------------
# parsing (pure, offline, no licence question)
# --------------------------------------------------------------------------

_JSON_VAR = re.compile(r"var\s+(\w+)\s*=\s*JSON\.parse\('((?:[^'\\]|\\.)*)'\)")


def extract_json_vars(html: str) -> dict[str, Any]:
    """Pull every ``var X = JSON.parse('...')`` blob out of an Understat page.

    Understat ships its data as single-quoted, unicode-escaped JSON inside
    inline scripts. Returned as a dict so callers can see exactly which
    variables a page carried -- which is how the disappearance of ``shotsData``
    was detected rather than guessed.
    """
    out: dict[str, Any] = {}
    for m in _JSON_VAR.finditer(html):
        try:
            out[m.group(1)] = json.loads(codecs.decode(m.group(2), "unicode_escape"))
        except (ValueError, UnicodeDecodeError):
            continue
    return out


def _f(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_match_info(html: str, *, as_of: dt.datetime | None = None) -> pd.DataFrame:
    """Parse an Understat match page into two rows, one per team.

    Long format (one row per team-match) rather than wide, because that is what
    a team-strength model wants to join against fixtures, and it makes home/away
    symmetry explicit instead of encoded in column names.

    ``as_of`` defaults to the match kickoff. Understat publishes xG only after
    the match, so kickoff is *earlier* than true observability -- deliberately
    conservative in the wrong direction would be a leak, so note this is a
    **floor** and callers landing it in a PIT table must stamp it at
    full-time-plus-publication-lag, not here.
    """
    blobs = extract_json_vars(html)
    info = blobs.get("match_info")
    if not isinstance(info, dict):
        # ValueError, not TypeError: the caller passed a perfectly good string,
        # the *page* no longer has the shape we parse.
        raise ValueError(  # noqa: TRY004
            "no match_info on this page; Understat page structure has changed "
            f"(vars found: {sorted(blobs)})"
        )

    kickoff = None
    if info.get("date"):
        kickoff = dt.datetime.fromisoformat(str(info["date"])).replace(
            tzinfo=dt.timezone.utc
        )
    stamp = as_of or kickoff

    rows = []
    for side, opp in (("h", "a"), ("a", "h")):
        rows.append({
            "understat_match_id": _i(info.get("id")),
            "season_start_year": _i(info.get("season")),
            "league": info.get("league"),
            "kickoff_utc": kickoff,
            "team": info.get(f"team_{side}"),
            "opponent": info.get(f"team_{opp}"),
            "is_home": side == "h",
            "goals": _i(info.get(f"{side}_goals")),
            "goals_conceded": _i(info.get(f"{opp}_goals")),
            "xg": _f(info.get(f"{side}_xg")),
            "xg_conceded": _f(info.get(f"{opp}_xg")),
            "shots": _i(info.get(f"{side}_shot")),
            "shots_on_target": _i(info.get(f"{side}_shotOnTarget")),
            "deep_completions": _i(info.get(f"{side}_deep")),
            "ppda": _f(info.get(f"{side}_ppda")),
            "as_of": stamp,
        })
    return pd.DataFrame(rows)


def has_shot_level_data(html: str) -> bool:
    """Whether this page still carries shot-by-shot xG.

    False for every Understat match page observed on 2026-08-18. Kept as a
    live check so that if Understat restores ``shotsData`` the pipeline notices
    instead of continuing to assume it is gone.
    """
    return "shotsData" in extract_json_vars(html)


# --------------------------------------------------------------------------
# fetching (gated)
# --------------------------------------------------------------------------


def fetch_match(
    match_id: int,
    *,
    fetcher: TextFetcher | None = None,
    override_robots: bool = False,
    delay_s: float = POLITE_DELAY_S,
) -> Fetched:
    """Fetch one Understat match page, refusing unless robots.txt permits.

    ``override_robots`` exists so the account holder can make an informed,
    explicit decision for personal non-commercial use. It changes nothing about
    *how* we fetch: same honest User-Agent, same delay, no TLS impersonation, no
    cookie forgery, no bot-detection bypass. It only stops this function from
    refusing on their behalf.
    """
    if not override_robots and not robots_allows(f"/match/{match_id}"):
        raise RobotsDisallowed(
            f"{BASE}/robots.txt returns 'User-agent: * / Disallow: /', so fetching "
            f"/match/{match_id} is not permitted. Understat data for 2026-27 does "
            "not exist yet in any case (see module docstring). Pass "
            "override_robots=True only as a deliberate, informed choice."
        )
    owns = fetcher is None
    fetcher = fetcher or TextFetcher("understat", base_url=BASE)
    try:
        time.sleep(delay_s)
        return fetcher.get_text(f"match/{match_id}", suffix=".html")
    finally:
        if owns:
            fetcher.close()
