"""FBref / StatsBomb: evaluated, and deliberately not wired in.

This module exists to record a negative result properly, because "we tried
FBref and it didn't work" is worth exactly nothing to the next person without
the status codes attached.

What was measured, 2026-08-18
-----------------------------
Requests made with the project's honest User-Agent
(``fpl-edge/0.1 (personal research; contact via repo owner)``):

===============================================  ======  =============================
Request                                          Status  Body
===============================================  ======  =============================
``GET https://fbref.com/robots.txt``             403     Cloudflare interactive
                                                         challenge ("Just a moment...",
                                                         ``cType: 'interactive'``)
``GET https://fbref.com/en/comps/9/``            403     same challenge
``GET https://www.sports-reference.com/          200     served normally; the parent
robots.txt``                                             domain disallows ``GPTBot``
                                                         and ``AhrefsBot`` outright
===============================================  ======  =============================

We cannot even read FBref's ``robots.txt`` to learn what it permits, because
the challenge fires before the file is served. A source whose crawl policy is
unreadable cannot be crawled in good faith.

Why soccerdata "works" and we still do not use it
-------------------------------------------------
``soccerdata==1.9.1`` reaches FBref, and it does so by shipping
``tls_requests``, which loads a platform-native shared library
(``tls-client-darwin-arm64-1.13.1.dylib``) to impersonate a real browser's TLS
fingerprint. That is precisely the mechanism Cloudflare's challenge exists to
detect, so using it is circumventing bot detection rather than being permitted
by it.

Measured behaviour in an isolated environment on 2026-08-18:

* ``soccerdata.Understat(...).read_schedule()`` -- 380 rows in 0.8s.
* ``soccerdata.Understat(...).read_shot_events()`` -- 9,524 rows in 250.7s
  (~0.66s per match, i.e. self-rate-limited).
* ``soccerdata.FBref(leagues="ENG-Premier League", seasons="2526")
  .read_schedule()`` -- succeeded: 380 rows x 13 columns, but it took
  **269.4 seconds** to fetch what is a single season schedule page. That is
  three orders of magnitude slower than the same information from the FPL API,
  and the cost is paid on every cache miss.

So FBref is reachable *if* you impersonate a browser, and slow even then.

What we lose, and why it is survivable
--------------------------------------
FBref's distinctive value for FPL is per-90 shot/creation detail and the
StatsBomb-derived npxG and xAG columns. The FPL API already exposes
``expected_goals``, ``expected_assists`` and ``expected_goals_conceded`` per
player per fixture (see ``fact_player_fixture``), sourced from Opta. That
covers the large majority of what the points model needs. FBref would add
finishing-quality decomposition and set-piece detail, not the core rates.

If the account holder decides they want FBref, the legitimate route is the
StatsBomb open-data repository (CC BY-NC-SA 4.0, no anti-bot, but no Premier
League coverage beyond selected competitions) or a commercial licence from
Sports Reference. Both are noted in ``docs/data_sources.md``.
"""

from __future__ import annotations

BASE = "https://fbref.com"

#: Status returned by fbref.com to a plain, honestly-identified HTTP client,
#: including for ``/robots.txt`` itself. Measured 2026-08-18.
OBSERVED_STATUS = 403


class FBrefUnavailable(RuntimeError):
    """FBref cannot be fetched without circumventing bot detection."""


def why_unavailable() -> str:
    """The refusal, with the evidence, in one string a caller can log."""
    return (
        "fbref.com returns HTTP 403 with a Cloudflare interactive challenge to any "
        "honestly-identified client, including for /robots.txt (measured 2026-08-18). "
        "Reaching it requires TLS-fingerprint impersonation (as soccerdata's "
        "tls_requests dependency does), which is bot-detection circumvention, so this "
        "engine does not do it. FPL's own Opta-sourced expected_goals / expected_assists "
        "/ expected_goals_conceded in fact_player_fixture cover the core need. "
        "See docs/data_sources.md."
    )


def ingest(*_args: object, **_kwargs: object) -> None:
    """Always raises. Present so callers get the reasoning, not an ImportError."""
    raise FBrefUnavailable(why_unavailable())
