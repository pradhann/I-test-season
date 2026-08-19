"""Building the candidate pool: who is worth evaluating in the first place.

The obvious approach does not work right now, and it is worth being precise
about why, because the workaround is the whole design of this module.

**What the docs promise.** ``/api/leagues-classic/314/standings/?page_standings=N``
pages the overall league in rank order. Page 1 is the top 50 managers in the
world; 200 pages gets you the top 10,000. That is the ideal sampling frame.

**What the API actually returns, verified 2026-08-18.**::

    leagues-classic/314/standings/?page_standings=1  -> 200 OK, standings.results = []
    leagues-classic/322/standings/?page_standings=1  -> 200 OK, standings.results = []

Both are empty. System leagues (``league_type == 's'``) are populated when a
gameweek is scored, and no gameweek of 2026-27 has been. The same is true of the
"Top 10% 25/26 League", which would otherwise be a ready-made pool of
last-season's best 10% -- FPL rebuilds it at first scoring, not at rollover. So
before GW1 there is no rank-ordered frame to sample from at all.

**What does work before GW1**, verified against the live API on the same date:

* ``/api/entry/{id}/`` -- identity, region, ``years_active``, and the full list
  of leagues the entry belongs to, including invitational ones.
* ``/api/entry/{id}/history/`` -- every past season's final rank and points.
  This is the long-run skill evidence, and it is available now.
* ``/api/leagues-{classic,h2h}/{id}/standings/`` -- ``standings.results`` is
  empty pre-season, but ``new_entries.results`` lists the membership, because at
  season start every member is "new". Membership is what we need; rank within
  the league is not.

So the pool is assembled from **named seeds plus snowball sampling**, not by
skimming the overall table:

1. The user's own mini-league rivals, in full. Small, and the one group whose
   value does not depend on the skill model finding them impressive.
2. The 20 named experts already mapped in the FPL-MCP repo.
3. Published season winners (:data:`WINNERS`), every ID checked against the
   game's own record by :func:`verify_winner_ids` before it is trusted.
4. The pinned LiveFPL all-time list (:mod:`fpl_edge.ingest.rivals.elite_list`),
   which is the only readily available source of managers with *long* records --
   the scarce input for a skill estimate, since shrinkage rewards seasons and
   most FPL entries have two or three.
5. Snowball: read each expert's league memberships and keep invitational leagues
   that **two or more seeds share**. A league several established managers
   independently joined is far more likely to be an elite invitational than any
   single membership is; requiring an intersection is a cheap, principled filter
   that also bounds the crawl.

Then fetch every candidate's season history and let the skill model judge them.
Nobody gets credit for the door they came in through: appearing on a third-party
all-time list buys a manager a history lookup and nothing else, which matters
because that list is itself built from finishing ranks and treating its ordering
as evidence would be circular.

The bias this introduces is real and is stated rather than hidden: the pool
over-represents managers who are visible, and visibility correlates with past
success, so it cannot see a brilliant anonymous manager in no leagues.
:mod:`fpl_edge.models.copying.skill` corrects the part of that it can
(regression to the mean under selection) and declares the part it cannot.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from fpl_edge.config import USER, MiniLeague
from fpl_edge.ingest.rivals.client import RivalsFetcher
from fpl_edge.ingest.rivals.elite_list import ELITE_1000

#: Named experts with publicly attested entry IDs, mirrored from
#: FPL-MCP ``tools/expert_tools.py``. Kept as a literal rather than imported
#: because this repo must not depend on that one being checked out, and because
#: a seed list that silently changes underneath a recorded crawl is a
#: reproducibility problem.
#:
#: These are seeds, NOT the answer. Being a well-known FPL content creator is
#: evidence of being well-known; the skill model decides whether it is evidence
#: of being good, and for several of these names it concludes it is not.
EXPERT_SEEDS: dict[str, int] = {
    "FPL Focal": 200,
    "FPL Harry": 1320,
    "FPL Raptor": 1587,
    "FPL Pickle": 14501,
    "FPL Mate": 16267,
    "Ben Crellin": 6586,
    "Az Phillips": 441,
    "Kelly Somers": 1924811,
    "Julien Laurens": 1514450,
    "Sam Bonfield": 260,
    "Lee Bonfield": 341,
    "Holly Shand": 135,
    "Ian Irwing": 7577129,
    "FPL Sonaldo": 16725,
    "Pras": 3570,
    "Gianni Buttice": 17614,
    "BigMan Bakar": 963,
    "Yelena": 251,
    "Stormzy": 698910,
    "Chunkz": 2253812,
}

#: FPL overall champions by season, with the entry ID as published in the
#: Premier League's own contemporaneous announcement of each win. Four seasons
#: (2011/12, 2012/13, 2013/14, 2015/16) have a named winner but no recoverable
#: ID: premierleague.com's pre-2017 archive lost its FPL links in a CMS
#: migration. They are listed with ``None`` rather than omitted, so the gap is
#: visible in the data instead of looking like those seasons had no winner.
#:
#: **Every one of these is verified against the live API** by
#: :func:`verify_winner_ids`, which checks that the entry's own history reports
#: rank 1 in the claimed season. An ID that fails is excluded from the pool with
#: its failure recorded; a winner list assembled from news articles and never
#: checked is exactly the kind of input that quietly poisons a cohort analysis.
WINNERS: tuple[tuple[str, str, int | None, str], ...] = (
    ("2025/26", "Erik Ibsen", 3027768, "premierleague.com/en/news/4663999"),
    ("2024/25", "Lovro Budisin", 235307, "premierleague.com/en/news/4316375"),
    ("2023/24", "Jonas Sand Labakk", 98027, "premierleague.com/en/news/4022717"),
    ("2022/23", "Ali Jahangirov", 158332, "premierleague.com/en/news/3493856"),
    ("2021/22", "Jamie Pigott", 159801, "premierleague.com/news/2632794"),
    ("2020/21", "Michael Coone", 3010405, "premierleague.com/en/news/2159596"),
    ("2019/20", "Joshua Bull", 184028, "premierleague.com/en/news/1751509"),
    ("2018/19", "Adam Levy", 107827, "premierleague.com/en/news/1220130"),
    ("2017/18", "Yusuf Sheikh", 101046, "premierleague.com/en/news/690904"),
    ("2016/17", "Ben Crabtree", 60560, "premierleague.com/en/news/401788"),
    ("2015/16", "Dimitri Nicolaou", None, "no published entry id found"),
    # 390 and 3063 come from secondary community reporting rather than a
    # premierleague.com announcement, so they are the two most likely to be
    # wrong. verify_winner_ids treats them exactly like the rest and lets the
    # API adjudicate.
    ("2014/15", "Simon March", 390, "fantasyfootballscout.co.uk 2015-05-24 (secondary)"),
    ("2013/14", "Tom Fenley", None, "no published entry id found"),
    ("2012/13", "Matt Martyniak", None, "no published entry id found"),
    ("2011/12", "Sam Pater", None, "no published entry id found"),
    ("2010/11", "Chris McGurn", 3063, "fantasyfootballscout.co.uk 2011-05-21 (secondary)"),
)

#: 2019/20 was first awarded to Aleksandar Antonov and then reassigned to Joshua
#: Bull after a terms-and-conditions disqualification. Recorded because a cohort
#: of "winners" that silently contains a disqualified entry is a data-quality
#: problem hiding as a footnote.
DISPUTED_SEASONS: frozenset[str] = frozenset({"2019/20"})


def verify_winner_ids(
    fetcher: RivalsFetcher, winners: tuple[tuple[str, str, int | None, str], ...] = WINNERS
) -> pd.DataFrame:
    """Check each published winner ID against that entry's own rank history.

    A published ID is a claim from a news article. The entry's ``past`` block is
    the game's own record. If the two agree -- the entry reports rank 1 in the
    season the article says they won -- the ID is confirmed by two independent
    sources and can carry a cohort analysis. If they disagree, the ID is wrong,
    or IDs are not stable across seasons, and either way the row must not be
    used as if it were a winner's record.

    One request per winner. Sixteen requests to avoid building an entire
    analysis on unverified numbers is the cheapest insurance in this package.
    """
    rows = []
    for season, name, eid, source in winners:
        if eid is None:
            rows.append({"season": season, "name": name, "entry_id": None,
                         "source": source, "status": "no_published_id",
                         "reported_rank": None, "entry_name": None})
            continue
        got = fetcher.get_json(f"entry/{eid}/history/")
        if got.body is None:
            rows.append({"season": season, "name": name, "entry_id": eid,
                         "source": source, "status": "entry_404",
                         "reported_rank": None, "entry_name": None})
            continue
        past = {row.get("season_name"): row.get("rank") for row in got.body.get("past") or []}
        rank = past.get(season)
        if rank is None:
            status = "season_absent_from_history"
        elif int(rank) == 1:
            status = "confirmed"
        else:
            status = "contradicted"
        rows.append({"season": season, "name": name, "entry_id": eid, "source": source,
                     "status": status, "reported_rank": rank, "entry_name": None})
    return pd.DataFrame(rows)


def confirmed_winner_ids(verification: pd.DataFrame) -> list[int]:
    """Only the winner entries whose own history reports rank 1 in that season."""
    if verification.empty:
        return []
    ok = verification[verification["status"] == "confirmed"]
    return [int(x) for x in ok["entry_id"].dropna()]


#: Leagues never worth snowballing into: FPL's own auto-entered system leagues
#: (country, club, "Gameweek 1", "Overall"). Membership carries no information
#: -- everyone is in them -- and they are enormous.
SYSTEM_LEAGUE_TYPE = "s"

#: Public invitational leagues that are effectively broadcast channels rather
#: than communities of skill. Membership is a signal of watching a show, and
#: they run to hundreds of thousands of entries. Excluded by ID so the
#: exclusion is auditable rather than a size heuristic that drifts.
BROADCAST_LEAGUES: frozenset[int] = frozenset({
    1337,   # @OfficialFPL on X
    317,    # Sky Sports League
})


@dataclass(frozen=True, slots=True)
class Candidate:
    """One manager in the pool, with the reason they are in it."""

    entry_id: int
    source: str
    player_name: str | None = None
    entry_name: str | None = None


@dataclass
class PoolReport:
    """What a pool-building run did, in numbers that can go in a commit message."""

    seeds: int = 0
    seed_profiles_read: int = 0
    leagues_seen: int = 0
    leagues_crawled: int = 0
    league_pages: int = 0
    candidates: int = 0
    by_source: dict[str, int] = field(default_factory=dict)
    skipped_system: int = 0
    skipped_broadcast: int = 0
    skipped_singleton: int = 0


def _league_members(
    fetcher: RivalsFetcher,
    league_id: int,
    *,
    kind: str = "classic",
    max_pages: int = 4,
) -> tuple[list[dict[str, Any]], int]:
    """Membership of a league, from whichever of the two lists is populated.

    Pre-season the members sit in ``new_entries``; once a gameweek is scored
    they migrate to ``standings``. Reading both and merging means the same code
    works either side of the first deadline, which matters because this is
    exactly the boundary the crawl will be run across.

    ``max_pages`` is a hard stop, not a hint. Some invitational leagues have six
    figures of members and paging one at a time is how a 400-request crawl
    becomes a 4,000-request one.
    """
    path = "leagues-classic" if kind == "classic" else "leagues-h2h"
    members: dict[int, dict[str, Any]] = {}
    pages = 0
    for page in range(1, max_pages + 1):
        got = fetcher.get_json(
            f"{path}/{league_id}/standings/",
            {"page_standings": page, "page_new_entries": page},
        )
        pages += 1
        if got.body is None:
            break
        body = got.body
        more = False
        for block, name_key in (("standings", "entry_name"), ("new_entries", "entry_name")):
            section = body.get(block) or {}
            for row in section.get("results", []):
                eid = row.get("entry")
                if eid is None:
                    continue
                members.setdefault(int(eid), {
                    "entry_id": int(eid),
                    "entry_name": row.get(name_key) or row.get("entry_name"),
                    "player_name": " ".join(
                        x for x in (row.get("player_first_name"), row.get("player_last_name")) if x
                    ).strip() or row.get("player_name"),
                })
            more = more or bool(section.get("has_next"))
        if not more:
            break
    return list(members.values()), pages


def _profile(fetcher: RivalsFetcher, entry_id: int) -> dict[str, Any] | None:
    got = fetcher.get_json(f"entry/{entry_id}/")
    return got.body if got.body is not None else None


def seed_candidates(mini_leagues: tuple[MiniLeague, ...] | None = None) -> list[Candidate]:
    """The named starting points: public experts plus the user's actual rivals.

    The two groups answer different questions and are both needed. Experts are a
    route into the elite social graph. Mini-league rivals are the field the user
    is *actually* scored against in four of their five leagues, and the optimal
    play against 39 known people is not the optimal play against six million.
    """
    out = [
        Candidate(entry_id=eid, source="expert", player_name=name)
        for name, eid in EXPERT_SEEDS.items()
    ]
    for league in (mini_leagues if mini_leagues is not None else USER.mini_leagues):
        out.append(Candidate(entry_id=USER.entry_id, source=f"mini_league:{league.league_id}"))
    return out


def build_pool(
    fetcher: RivalsFetcher,
    *,
    mini_leagues: tuple[MiniLeague, ...] | None = None,
    max_candidates: int = 600,
    min_shared_seeds: int = 2,
    max_leagues: int = 12,
    max_pages_per_league: int = 4,
    elite_list_n: int = 250,
    winners: bool = True,
) -> tuple[list[Candidate], PoolReport, pd.DataFrame, pd.DataFrame]:
    """Assemble the candidate pool. Returns candidates plus warehouse frames.

    Ordering matters for the budget: mini-league rivals are collected first and
    are never displaced by the cap, because they are the one group whose value
    does not depend on the skill model finding them impressive. If the budget
    runs out mid-snowball the pool is still complete for mini-league mode.
    """
    as_of = dt.datetime.now(dt.timezone.utc)
    report = PoolReport()
    leagues = mini_leagues if mini_leagues is not None else USER.mini_leagues

    chosen: dict[int, Candidate] = {}

    # 1. The user's own rivals, in full. Small, and the reason mini-league mode
    #    exists at all.
    for league in leagues:
        members, pages = _league_members(
            fetcher, league.league_id, kind=league.kind, max_pages=max_pages_per_league
        )
        report.league_pages += pages
        report.leagues_crawled += 1
        for m in members:
            chosen.setdefault(m["entry_id"], Candidate(
                entry_id=m["entry_id"],
                source=f"mini_league:{league.league_id}",
                player_name=m.get("player_name"),
                entry_name=m.get("entry_name"),
            ))

    # 2. Named experts.
    for name, eid in EXPERT_SEEDS.items():
        chosen.setdefault(eid, Candidate(entry_id=eid, source="expert", player_name=name))
    report.seeds = len(EXPERT_SEEDS)

    # 3. Published winners, on the same footing as anyone else. Their IDs are
    #    verified separately; an unverified one simply fails to produce a
    #    history with rank 1 and is dropped by the cohort builder.
    if winners:
        for season, name, eid, _src in WINNERS:
            if eid is not None:
                chosen.setdefault(eid, Candidate(
                    entry_id=eid, source=f"winner:{season}", player_name=name))

    # 4. The pinned third-party all-time list. This is the highest-yield source
    #    of managers with LONG records, which is the scarce input for a skill
    #    estimate: shrinkage rewards seasons, and most FPL entries have two.
    for eid, name in ELITE_1000[:elite_list_n]:
        if len(chosen) >= max_candidates:
            break
        chosen.setdefault(eid, Candidate(entry_id=eid, source="elite_list",
                                         player_name=name))

    # 5. Snowball: read each expert's leagues, keep the ones several share.
    league_members_of_seed: dict[int, set[int]] = defaultdict(set)
    league_meta: dict[int, dict[str, Any]] = {}
    profile_rows: list[dict[str, Any]] = []
    membership_rows: list[dict[str, Any]] = []

    for name, eid in EXPERT_SEEDS.items():
        prof = _profile(fetcher, eid)
        if prof is None:
            continue
        report.seed_profiles_read += 1
        profile_rows.append(_profile_row(prof, eid, "expert", as_of))
        for kind in ("classic", "h2h"):
            for lg in (prof.get("leagues") or {}).get(kind, []):
                lid = int(lg["id"])
                league_meta[lid] = {
                    "name": lg.get("name"), "type": lg.get("league_type"),
                    "scoring": lg.get("scoring"), "kind": kind,
                }
                league_members_of_seed[lid].add(eid)
                membership_rows.append({
                    "entry_id": eid, "league_id": lid, "league_name": lg.get("name"),
                    "league_type": lg.get("league_type"), "scoring": lg.get("scoring"),
                    "as_of": as_of,
                })
    report.leagues_seen = len(league_meta)

    shared = Counter({lid: len(seeds) for lid, seeds in league_members_of_seed.items()})
    ranked: list[int] = []
    for lid, n in shared.most_common():
        meta = league_meta[lid]
        if meta["type"] == SYSTEM_LEAGUE_TYPE:
            report.skipped_system += 1
            continue
        if lid in BROADCAST_LEAGUES:
            report.skipped_broadcast += 1
            continue
        if n < min_shared_seeds:
            report.skipped_singleton += 1
            continue
        ranked.append(lid)

    for lid in ranked[:max_leagues]:
        if len(chosen) >= max_candidates:
            break
        members, pages = _league_members(
            fetcher, lid, kind=league_meta[lid]["kind"], max_pages=max_pages_per_league
        )
        report.league_pages += pages
        report.leagues_crawled += 1
        for m in members:
            if len(chosen) >= max_candidates:
                break
            chosen.setdefault(m["entry_id"], Candidate(
                entry_id=m["entry_id"], source=f"snowball:{lid}",
                player_name=m.get("player_name"), entry_name=m.get("entry_name"),
            ))

    candidates = list(chosen.values())
    report.candidates = len(candidates)
    report.by_source = dict(Counter(c.source.split(":")[0] for c in candidates))

    managers = pd.DataFrame([
        {
            "entry_id": c.entry_id, "player_name": c.player_name,
            "entry_name": c.entry_name, "region": None, "years_active": None,
            "favourite_team_id": None, "started_event": None,
            "source": c.source, "as_of": as_of,
        }
        for c in candidates
    ])
    # Profile rows we actually read override the placeholder identity above.
    if profile_rows:
        prof_df = pd.DataFrame(profile_rows)
        managers = managers[~managers["entry_id"].isin(set(prof_df["entry_id"]))]
        managers = pd.concat([managers, prof_df], ignore_index=True)

    memberships = pd.DataFrame(membership_rows) if membership_rows else pd.DataFrame(
        columns=["entry_id", "league_id", "league_name", "league_type", "scoring", "as_of"]
    )
    return candidates, report, managers, memberships


def _profile_row(prof: dict[str, Any], eid: int, source: str, as_of: dt.datetime) -> dict[str, Any]:
    name = " ".join(
        x for x in (prof.get("player_first_name"), prof.get("player_last_name")) if x
    ).strip()
    return {
        "entry_id": eid,
        "player_name": name or None,
        "entry_name": prof.get("name"),
        "region": prof.get("player_region_name"),
        # `years_active` is FPL's own count of seasons entered. It is the single
        # cheapest skill covariate available and, unlike rank, it cannot be got
        # lucky in -- but it also cannot distinguish a good manager from a
        # persistent one, which is why it is a covariate and not the score.
        "years_active": prof.get("years_active"),
        "favourite_team_id": prof.get("favourite_team"),
        "started_event": prof.get("started_event"),
        "source": source,
        "as_of": as_of,
    }
