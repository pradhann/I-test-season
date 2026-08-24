"""The named elite: a curated, verified list of well-known managers, tracked fully.

    uv run python -m fpl_edge.ingest.rivals.elite --budget 200
    uv run python -m fpl_edge.ingest.rivals.elite --dry-run

The top-of-table sampler (:mod:`fpl_edge.ingest.rivals.top1k`) answers "what
does the in-form crowd own"; this module answers "what did *Ben Crellin* do"
-- a different question that needs names, not ranks. The list is short, human
-curated, and deliberately a literal in this file: adding a manager is a diff
a reviewer can see, and the crawl can never silently grow.

Why every ID is verified before a single pick is fetched
--------------------------------------------------------
FPL entry IDs are **per-season**: the same person gets a new ID each August,
assigned in registration order. A curated list therefore rots annually, and it
rots silently -- the stale ID resolves to a *different real person* rather
than 404ing. This is not hypothetical: every one of the 20 ``EXPERT_SEEDS``
IDs in :mod:`fpl_edge.ingest.rivals.roster` (mirrored from FPL-MCP, a prior
season's mapping) was checked against the live API on 2026-08-24 and every one
now belongs to someone else -- "Ben Crellin" (6586) is actually Levi
Longworth. So :func:`verify` fetches each entry's profile and requires the
account holder's name to match before the ID is trusted; a mismatch is
recorded and excluded, never crawled. One request per name is the cheapest
insurance in the package.

The IDs below come from the pinned LiveFPL all-time list
(:mod:`fpl_edge.ingest.rivals.elite_list`, captured 2026-08 and therefore
carrying *current-season* IDs) and were individually confirmed against
``/api/entry/{id}/`` on 2026-08-24.

What is stored, and when it became public
-----------------------------------------
For each verified manager: identity (``dim_manager``, source ``elite_named``),
full past-season record (``fact_manager_season``), current-season per-GW rows
(``fact_manager_gw``), locked squads for every finished gameweek
(``fact_manager_pick``, ``as_of`` = that GW's deadline), the season's full
transfer list (``fact_manager_transfer``, ``as_of`` = the deadline the
transfer applied to, raw click-time kept in ``time_utc``) and chips
(``fact_manager_chip``). The point-in-time discipline is inherited from
:mod:`fpl_edge.ingest.rivals.picks` unchanged.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import unicodedata
from dataclasses import dataclass
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals import history as history_mod
from fpl_edge.ingest.rivals import picks as picks_mod
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget, RivalsFetcher
from fpl_edge.ingest.rivals.crawl import _season_and_deadlines, _write

#: dim_manager.source for rows written by this module. NOT prefixed "top1k",
#: so fpl_edge.models.field.observed classifies these managers into its
#: `elite` cohort, and the semantic layer's sem_elite_ownership keeps them in
#: a separate cohort from the standings sample.
SOURCE = "elite_named"


@dataclass(frozen=True, slots=True)
class EliteEntry:
    """One curated manager: the claim, and where the claim comes from."""

    name: str          # the account holder's name as FPL displays it
    entry_id: int      # current-season entry ID -- re-verify every season
    note: str          # why they are on the list / provenance of the ID


#: The curated list. Extend freely -- each addition costs one verification
#: request and is crawled only if the live profile name matches. IDs verified
#: against /api/entry/{id}/ on 2026-08-24 unless noted.
ELITE_NAMED: tuple[EliteEntry, ...] = (
    EliteEntry("Ben Crellin", 53517,
               "LiveFPL all-time #2; the fixture-ticker spreadsheet author. "
               "Verified live 2026-08-24 (team name matches his known handle)."),
    EliteEntry("Andy LTFPL", 41,
               "Let's Talk FPL. LiveFPL all-time #23."),
    EliteEntry("Mark Sutherns", 252,
               "Long-running elite record; LiveFPL all-time list."),
    EliteEntry("Finn Sollie", 81099,
               "LiveFPL all-time list."),
    EliteEntry("BigMan Bakar", 5133,
               "LiveFPL all-time #22. Note: the stale FPL-MCP seed said 963, "
               "which now belongs to someone else -- kept as a reminder that "
               "IDs rot."),
    EliteEntry("Mark Hurst", 35543, "LiveFPL all-time #3."),
    EliteEntry("Michael Giovanni", 16499, "LiveFPL all-time #5."),
    EliteEntry("Torkel Wahl-Olsen", 18203, "LiveFPL all-time #6."),
)


#: Latin letters that NFKD does NOT decompose (the stroke in Ø is part of the
#: codepoint, not a combining mark). Norwegian names are common at the top of
#: FPL, so this is not a corner case.
_NON_DECOMPOSABLE = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "å": "a", "Å": "a",
    "ð": "d", "Ð": "d", "þ": "th", "Þ": "th", "ß": "ss", "đ": "d", "Đ": "d",
    "ł": "l", "Ł": "l",
})


def _norm(s: str | None) -> str:
    """Casefold and strip accents so 'Jesper Øiestad' matches 'jesper oiestad'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.translate(_NON_DECOMPOSABLE))
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def verify(
    fetcher: RivalsFetcher,
    entries: tuple[EliteEntry, ...] = ELITE_NAMED,
    *,
    as_of: dt.datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Check every curated ID against the live profile it resolves to.

    Returns ``(verification, dim_manager_rows)``. A row is ``verified`` only
    when the profile's account-holder name matches the curated name (accent-
    and case-insensitive, either direction of containment, so "Andy LTFPL"
    matches a profile named "Andy" + team branding). Only verified entries
    produce ``dim_manager`` rows; a mismatched ID identifies a *different
    person* and recording their identity under our curated name would be
    exactly the silent rot this function exists to catch.
    """
    as_of = as_of or dt.datetime.now(dt.timezone.utc)
    rows: list[dict[str, Any]] = []
    managers: list[dict[str, Any]] = []
    for e in entries:
        got = fetcher.get_json(f"entry/{e.entry_id}/")
        if got.body is None:
            rows.append({"name": e.name, "entry_id": e.entry_id,
                         "status": "entry_404", "actual_name": None})
            continue
        prof = got.body
        actual = " ".join(x for x in (prof.get("player_first_name"),
                                      prof.get("player_last_name")) if x).strip()
        a, b = _norm(e.name), _norm(actual)
        ok = bool(a) and bool(b) and (a in b or b in a)
        rows.append({"name": e.name, "entry_id": e.entry_id,
                     "status": "verified" if ok else "name_mismatch",
                     "actual_name": actual})
        if not ok:
            continue
        managers.append({
            "entry_id": e.entry_id,
            "player_name": actual or e.name,
            "entry_name": prof.get("name"),
            "region": prof.get("player_region_name"),
            "years_active": prof.get("years_active"),
            "favourite_team_id": prof.get("favourite_team"),
            "started_event": prof.get("started_event"),
            "source": SOURCE,
            "as_of": as_of,
        })
    return pd.DataFrame(rows), pd.DataFrame(managers)


def collect(
    fetcher: RivalsFetcher,
    *,
    entries: tuple[EliteEntry, ...] = ELITE_NAMED,
    now: dt.datetime | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch everything about the verified elite. Network only, no lock held."""
    now = now or dt.datetime.now(dt.timezone.utc)
    frames: dict[str, pd.DataFrame] = {}
    summary: dict[str, Any] = {}

    season, deadlines = _season_and_deadlines(fetcher)
    summary["season"] = season

    verification, managers = verify(fetcher, entries, as_of=now)
    summary["verification"] = verification.to_dict("records")
    if not managers.empty:
        frames["dim_manager"] = managers
    ids = [int(x) for x in managers["entry_id"]] if not managers.empty else []
    summary["verified_ids"] = ids
    if not ids:
        summary["skipped"] = "no curated ID passed verification; nothing crawled"
        return frames, summary

    past, current, chips, missing = history_mod.ingest_histories(
        fetcher, ids, season=season
    )
    summary["history"] = {"entries": len(ids), "entries_404": len(missing),
                          "past_season_rows": int(len(past)),
                          "current_gw_rows": int(len(current))}
    frames["fact_manager_season"] = past
    frames["fact_manager_gw"] = current
    chip_frames = [chips] if not chips.empty else []

    live_gws = [g for g, d in deadlines.items() if d <= now]
    if live_gws:
        p, c, pstats = picks_mod.ingest_picks(
            fetcher, ids, live_gws, season=season, deadlines=deadlines, now=now
        )
        summary["picks"] = pstats
        if not p.empty:
            frames["fact_manager_pick"] = p
        if not c.empty:
            chip_frames.append(c)
        t, tstats = picks_mod.ingest_transfers(
            fetcher, ids, season=season, deadlines=deadlines
        )
        summary["transfers"] = tstats
        if not t.empty:
            frames["fact_manager_transfer"] = t
    else:
        summary["picks"] = {"skipped": "no deadline has passed; squads and "
                                       "transfers are still private"}
    if chip_frames:
        frames["fact_manager_chip"] = pd.concat(
            chip_frames, ignore_index=True).drop_duplicates()
    return frames, summary


def run(
    *,
    budget_limit: int = 200,
    db_path: str | None = None,
    offline: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Verify, fetch, write. ~4 requests per curated manager per gameweek."""
    n = len(ELITE_NAMED)
    declared = {"managers": n,
                "requests_worst_case": 1 + n * 4,
                "note": "1 bootstrap + per manager: profile, history, "
                        "picks per locked GW, transfers"}
    if dry_run:
        return {"dry_run": True, "plan": declared}
    budget = RequestBudget(limit=budget_limit)
    fetcher = RivalsFetcher(budget, offline=offline)
    frames: dict[str, pd.DataFrame] = {}
    summary: dict[str, Any] = {"plan": declared}
    try:
        frames, collected = collect(fetcher)
        summary.update(collected)
    except BudgetExhausted as exc:
        summary["budget_exhausted"] = str(exc)
    finally:
        fetcher.close()

    if frames:
        summary["write"] = _write(frames, db_path, summary)
    summary["requests"] = {
        "limit": budget.limit, "spent": budget.spent,
        "cache_hits": budget.cache_hits, "receipt": budget.receipt(),
    }
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--budget", type=int, default=200)
    ap.add_argument("--db", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    out = run(budget_limit=args.budget, db_path=args.db,
              offline=args.offline, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
