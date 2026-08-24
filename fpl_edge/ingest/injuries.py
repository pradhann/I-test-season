"""Availability and team news: what is actually scrapeable, and what to use.

The conclusion first: **the FPL API is the injury feed**, and every third-party
alternative is either bot-blocked, licence-blocked, or paid. That is a happier
answer than it sounds, and the reasoning is below.

What was measured, 2026-08-18
-----------------------------
All requests used the project's honest User-Agent.

======================================  ==========  ================================
Source                                  Status      Verdict
======================================  ==========  ================================
``premierinjuries.com/robots.txt``      **403**     Cloudflare interactive challenge;
                                                    ``/injury-table.php`` also 403.
                                                    Crawl policy unreadable, so no.
``physioroom.com/robots.txt``           200         Permissive (blocks only
                                                    ``/admin``, ``/basket``,
                                                    ``/checkout``, ``/search``)...
``physioroom.com/injury-table/...``     **522**     ...but the injury table itself
                                                    returned a Cloudflare origin
                                                    timeout. Site was down at
                                                    measurement time.
``fantasyfootballscout.co.uk``          200         ``robots.txt`` is ``Disallow:``
                                                    (allow all); ``/team-news``
                                                    serves 542KB. But the page markup
                                                    carries a ``premium members``
                                                    gate, and their Terms reserve the
                                                    content to subscribers. Scraping
                                                    it would be taking paid content.
``bbc.co.uk/robots.txt``                200         Explicitly forbids "scraping,
                                                    crawling, or systematic
                                                    extraction", datasets, TDM, and
                                                    RAG/agentic use of BBC content.
                                                    Unambiguous no.
======================================  ==========  ================================

So of five candidate feeds, one is bot-blocked, one was down, one is paywalled
in substance, one forbids it in terms, and none is both reachable and clearly
licensed.

Why the FPL API is genuinely the right answer anyway
----------------------------------------------------
Three properties that no aggregator can match:

* **It is the scoring authority.** ``status`` and
  ``chance_of_playing_next_round`` are what the game itself uses. A player
  premierinjuries lists as doubtful but FPL flags 75% is, for our purposes,
  75%.
* **It is timestamped.** ``news_added`` gives the instant an availability
  change became public, which is exactly the ``as_of`` that
  ``fact_player_state`` needs. Scraped tables carry no such stamp, so a
  backtest built on them silently leaks -- you would be applying today's injury
  list to a deadline three weeks ago.
* **It is already ingested.** ``fpl_api.ingest_bootstrap`` writes ``status``,
  ``chance_of_playing_next_round``, ``news`` and ``news_added`` on every poll.

The genuine gap is **press-conference lead time**. Managers give team news
Friday lunchtime; FPL's flags often follow by some hours. For a Saturday
11:00 deadline that gap is real but small, and it is the *only* thing a paid
feed buys here. Sizing of that edge is in ``docs/data_sources.md``.

This module therefore adds no new network source. It turns what the FPL API
already gives us into the availability signal the model wants, and it flags
the case the API is bad at: a player whose news changed very recently.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from fpl_edge.store import Warehouse

#: FPL ``status`` codes and the availability they imply.
#: ``a`` available, ``d`` doubtful, ``i`` injured, ``s`` suspended,
#: ``u`` unavailable, ``n`` on loan / not in squad.
STATUS_MEANING: dict[str, str] = {
    "a": "available",
    "d": "doubtful",
    "i": "injured",
    "s": "suspended",
    "u": "unavailable",
    "n": "not_in_squad",
}

#: Play probability to assume when ``chance_of_playing_next_round`` is NULL.
#: FPL leaves it null both for "definitely fine" and for "we haven't said", so
#: the status code has to break the tie. These are priors, not observations,
#: and the model should treat them as such.
STATUS_PRIOR: dict[str, float] = {
    "a": 1.00,
    "d": 0.50,
    "i": 0.10,
    "s": 0.00,
    "u": 0.00,
    "n": 0.00,
}


def availability(
    wh: Warehouse, season: str, as_of: dt.datetime, *, fresh_news_hours: float = 24.0
) -> pd.DataFrame:
    """Per-player availability as it was known at ``as_of``.

    Read through :meth:`Warehouse.snapshot_at`, so a flag added after the
    deadline is invisible -- which is the whole reason this goes through the
    warehouse rather than hitting the API directly.

    Columns added on top of the raw state:

    ``availability``
        The decoded status.
    ``play_prob``
        ``chance_of_playing_next_round / 100`` where FPL states it, else the
        status prior, else **NaN**. NaN means availability was never recorded
        (historical seasons carry none) -- the consumer decides what to do
        with that; this function does not guess. Treating unrecorded as 1.0
        once made every injured and suspended player in four seasons look
        certain to start.
    ``play_prob_is_stated``
        Whether the above came from FPL or from our prior. The optimiser should
        treat an assumed number very differently from a stated 100%.
    ``news_is_fresh``
        News changed within ``fresh_news_hours`` of ``as_of``. These are the
        players where a press-conference feed would actually have added
        something, and the ones to eyeball before confirming a transfer.
    """
    snap = wh.snapshot_at(as_of)
    df = snap.players(season)
    if df.empty:
        return df.assign(
            availability=pd.Series(dtype="object"),
            play_prob=pd.Series(dtype="float"),
            play_prob_is_stated=pd.Series(dtype="bool"),
            news_is_fresh=pd.Series(dtype="bool"),
        )

    # A NULL status stays NULL: it means "never recorded", not "available".
    status = df["status"]
    stated = df["chance_of_playing_next_round"]

    df = df.assign(
        availability=status.map(STATUS_MEANING).where(status.notna(), "unknown")
        .fillna("unknown"),
        play_prob_is_stated=stated.notna(),
        # No trailing fillna: a player with neither a stated chance nor a
        # recorded status gets NaN, which is the truth.
        play_prob=stated.astype("float").div(100.0)
        .fillna(status.map(STATUS_PRIOR).astype("float"))
        .clip(0.0, 1.0),
    )

    # news_added lives on fact_player_state, which snapshot.players() does not
    # project; pull it separately rather than duplicating the PIT logic.
    state = snap.table("fact_player_state", where="season = ?", params=[season])
    news_added = state.set_index("code")["news_added"] if not state.empty else pd.Series(dtype="object")
    added = df["code"].map(news_added)
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(hours=fresh_news_hours)
    df["news_is_fresh"] = pd.to_datetime(added, utc=True, errors="coerce") >= cutoff

    return df


def flagged(wh: Warehouse, season: str, as_of: dt.datetime) -> pd.DataFrame:
    """Only the players carrying a doubt, sorted by how much it will hurt.

    Ordered by ownership, because a 40%-owned doubtful player is a template
    decision and a 0.3%-owned one is noise.
    """
    df = availability(wh, season, as_of)
    if df.empty:
        return df
    # NaN play_prob (unrecorded) is not a known doubt; it is absence of data,
    # and listing all of history as "flagged" would drown the real flags.
    out = df[df["play_prob"] < 1.0]
    return out.sort_values("selected_by_pct", ascending=False).reset_index(drop=True)
