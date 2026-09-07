"""The creator panel's own teams: picks, transfers, chips and gameweek history.

    uv run python -m fpl_edge.ingest.rivals.panel_picks --budget 700
    uv run python -m fpl_edge.ingest.rivals.panel_picks --dry-run

Who this crawls, and why the list is not in this file
-----------------------------------------------------
``panel_person`` (fpl_edge/ingest/content/panel.py, loaded from
data/panels/creator_panel_2026_27.yaml) is the roster of people whose calls
the Creators page scores, and fifteen of them carry an FPL ``entry_id`` that
was read back from ``/api/entry/{id}/`` and corroborated by self-administered
league evidence. That is the strongest identity the repo has for anybody, and
until 2026-09-07 nothing crawled it: the nightly cohort crawls
(:mod:`fpl_edge.ingest.rivals.crawl`, :mod:`~fpl_edge.ingest.rivals.elite`,
:mod:`~fpl_edge.ingest.rivals.top1k`) never read ``panel_person``, so seven
panel members had squads in ``fact_manager_pick`` by coincidence -- they
happen to be in a mini-league or on the LiveFPL list -- and eight had none.
The page then said "their teams are UNREAD" about half the panel, honestly and
uselessly.

So the selection here is a query, not a literal: every ``panel_person`` row
with ``entry_verified = true``, ``active = true`` and a non-null ``entry_id``
(:func:`select_entries`). Adding a creator to the panel file is what adds
them to this crawl; there is no second list to keep in step.

What is checked before a single pick is fetched
-----------------------------------------------
A wrong entry id does not 404, it resolves to a stranger, and this repo has
already shipped that once (the panel file's header has the story). So the
profile is fetched first and its account-holder name is compared with the
``entry_api_name`` the roster recorded at verification time, with the same
:func:`~fpl_edge.ingest.rivals.names.name_matches` the named-elite crawl
uses. A mismatch is recorded as ``name_mismatch`` and that person is NOT
crawled: the id no longer resolves to what was verified, and reading a
squad under their name would be the exact silent misattribution the
verification exists to prevent. A roster row with no ``entry_api_name`` is
crawled and marked ``unchecked_name`` -- the yaml's verification stands, and
the summary says the live check was not possible.

What is stored
--------------
The same tables through the same parsers as every other cohort crawl --
:func:`~fpl_edge.ingest.rivals.picks.ingest_picks`,
:func:`~fpl_edge.ingest.rivals.picks.ingest_transfers`,
:func:`~fpl_edge.ingest.rivals.history.ingest_histories`, committed through
:func:`~fpl_edge.ingest.rivals.crawl._write` -- so the point-in-time
discipline (a pick's ``as_of`` is its deadline; a transfer's is the deadline
it applied to) is inherited rather than re-implemented. ``dim_manager`` rows
carry ``source = 'panel'``, which the cohort resolver classifies as ``elite``
unless the same entry is already in the top-1k sample.

Per-person honesty
------------------
``summary["people"]`` has one record per selected person with a ``status``
from a closed set -- ``ok``, ``entry_404``, ``refused: HTTP <n>``,
``name_mismatch``, ``partial`` (a locked gameweek's picks answered 404, or the
budget ran out mid-person), ``not_reached`` (the budget died before them) --
plus the HTTP status that produced it and the gameweeks whose picks were
read. :func:`main` exits non-zero when any person is not ``ok`` or the write
did not commit, while everything that WAS fetched is still committed first:
the failure mode this package keeps paying for is requests paid for and
frames discarded, and the step going red is the point, not a reason to throw
the data away.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd

from fpl_edge.ingest.rivals import history as history_mod
from fpl_edge.ingest.rivals import picks as picks_mod
from fpl_edge.ingest.rivals.client import BudgetExhausted, RequestBudget, RivalsFetcher
from fpl_edge.ingest.rivals.crawl import _season_and_deadlines, _write
from fpl_edge.ingest.rivals.names import name_matches
from fpl_edge.store import Warehouse

#: dim_manager.source for rows written here. Not prefixed "top1k", so
#: fpl_edge.models.field.observed puts these managers in its `elite` cohort
#: (the curated pool) unless the top-1k sampler already holds the same entry.
SOURCE = "panel"

#: Per-person requests on a completely cold cache: profile, history,
#: transfers, plus one picks request per locked gameweek. The default budget
#: in :func:`main` is sized so a cold full-season crawl of the whole panel
#: fits: 1 bootstrap + 16 people x (3 + 38) = 657 < 700. Steady state is far
#: below that -- finished-gameweek picks are cached forever and the profile
#: and history for 12h -- so a nightly run spends ~2 requests per person.
FIXED_REQUESTS_PER_PERSON = 3
MAX_GWS = 38

@dataclass(frozen=True, slots=True)
class PanelEntry:
    """One panel person whose FPL entry the roster has verified."""

    person_key: str
    display_name: str
    entry_id: int
    #: The account-holder name the API returned when the id was verified.
    #: None when the roster did not record one.
    entry_api_name: str | None


def select_entries(wh: Warehouse) -> list[PanelEntry]:
    """Every active panel person with a verified, non-null entry id.

    The three conditions are the whole selection rule and each one is a
    refusal: an inactive person is not on the panel this season, an
    unverified id is a lead and not a fact, and a null id has nothing to
    fetch. A warehouse without ``panel_person`` yields an empty list -- the
    caller decides whether that is a failure (it is, for the scheduled run).
    """
    present = wh.sql(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = 'panel_person'"
    )
    if present.empty:
        return []
    rows = wh.sql(
        "SELECT person_key, display_name, entry_id, entry_api_name "
        "FROM panel_person "
        "WHERE entry_verified AND active AND entry_id IS NOT NULL "
        "ORDER BY display_name"
    )
    out: list[PanelEntry] = []
    for r in rows.to_dict("records"):
        api_name = r.get("entry_api_name")
        out.append(PanelEntry(
            person_key=str(r["person_key"]),
            display_name=str(r["display_name"]),
            entry_id=int(r["entry_id"]),
            entry_api_name=(str(api_name) if isinstance(api_name, str)
                            and api_name.strip() else None),
        ))
    return out


def _person_record(e: PanelEntry) -> dict[str, Any]:
    return {
        "person": e.display_name,
        "person_key": e.person_key,
        "entry_id": e.entry_id,
        "status": "not_reached",
        "http_status": None,
        "profile_name": None,
        "picks_gws": [],
        "picks_404_gws": [],
        "transfers": 0,
        "history_gws": 0,
        "detail": "",
    }


def _http_status(exc: httpx.HTTPError) -> int | None:
    """The status code behind a refusal, or None for a transport failure
    (5xx is converted to a TransportError and retried by the client; when the
    retries are spent there is no status to report, only the absence)."""
    resp = getattr(exc, "response", None)
    return int(resp.status_code) if resp is not None else None


def _refusal(rec: dict[str, Any], exc: httpx.HTTPError, where: str) -> None:
    code = _http_status(exc)
    rec["http_status"] = code
    rec["status"] = f"refused: HTTP {code}" if code is not None else "refused: transport"
    rec["detail"] = (f"{where} answered {code}" if code is not None
                     else f"{where}: {type(exc).__name__}: {str(exc)[:120]}")


def collect(
    fetcher: RivalsFetcher,
    entries: list[PanelEntry],
    *,
    now: dt.datetime | None = None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """Fetch everything about the selected people. Network only, no lock held.

    Frames are accumulated per person and every person's record is written
    before the next is attempted, so a refusal or an exhausted budget on the
    ninth person leaves eight people's rows in the returned frames rather
    than none. ``BudgetExhausted`` is caught HERE, at the person boundary,
    for the same reason: raising it through the caller is how 270 paid
    transfer requests were once discarded (picks.ingest_transfers has that
    incident).
    """
    now = now or dt.datetime.now(dt.UTC)
    summary: dict[str, Any] = {"selected": len(entries)}
    people: list[dict[str, Any]] = [_person_record(e) for e in entries]
    summary["people"] = people
    frames_acc: dict[str, list[pd.DataFrame]] = {
        "dim_manager": [], "fact_manager_season": [], "fact_manager_gw": [],
        "fact_manager_pick": [], "fact_manager_transfer": [], "fact_manager_chip": [],
    }

    season, deadlines = _season_and_deadlines(fetcher)
    summary["season"] = season
    live_gws = sorted(gw for gw, d in deadlines.items() if d <= now)
    summary["live_gws"] = live_gws

    for e, rec in zip(entries, people, strict=True):
        try:
            _collect_one(fetcher, e, rec, frames_acc, season=season,
                         deadlines=deadlines, live_gws=live_gws, now=now)
        except BudgetExhausted as exc:
            summary["budget_exhausted"] = str(exc)
            if rec["status"] == "not_reached":
                rec["detail"] = "budget exhausted before this person"
            else:
                rec["status"] = "partial"
                rec["detail"] = f"budget exhausted mid-person: {exc}".split(". Spent")[0]
            break

    frames: dict[str, pd.DataFrame] = {}
    for table, parts in frames_acc.items():
        parts = [p for p in parts if p is not None and not p.empty]
        if parts:
            df = pd.concat(parts, ignore_index=True)
            if table == "fact_manager_chip":
                df = df.drop_duplicates()
            frames[table] = df

    summary["counts"] = {
        status: sum(1 for p in people if p["status"] == status)
        for status in sorted({p["status"] for p in people})
    }
    summary["not_ok"] = [
        {"person": p["person"], "entry_id": p["entry_id"], "status": p["status"],
         "http_status": p["http_status"], "detail": p["detail"]}
        for p in people if p["status"] != "ok"
    ]
    return frames, summary


def _collect_one(
    fetcher: RivalsFetcher,
    e: PanelEntry,
    rec: dict[str, Any],
    frames_acc: dict[str, list[pd.DataFrame]],
    *,
    season: str,
    deadlines: dict[int, dt.datetime],
    live_gws: list[int],
    now: dt.datetime,
) -> None:
    """One person, all endpoints; ``rec`` is filled in as each one answers."""
    # -- profile: identity, and the name check --------------------------------
    try:
        got = fetcher.get_json(f"entry/{e.entry_id}/")
    except httpx.HTTPError as exc:
        _refusal(rec, exc, f"entry/{e.entry_id}/")
        return
    rec["http_status"] = got.http_status
    if got.body is None:
        rec["status"] = "entry_404"
        rec["detail"] = (f"entry/{e.entry_id}/ answered 404: no such entry. The "
                         f"roster's verified id does not resolve; re-verify it.")
        return
    # From here on, requests have been paid for this person: a budget that
    # dies below is "partial", not "not_reached" (collect() reads this).
    rec["status"] = "fetching"
    prof = got.body
    actual = " ".join(x for x in (prof.get("player_first_name"),
                                  prof.get("player_last_name")) if x).strip()
    rec["profile_name"] = actual or None
    if e.entry_api_name is not None and not name_matches(e.entry_api_name, actual):
        rec["status"] = "name_mismatch"
        rec["detail"] = (
            f"the roster verified entry {e.entry_id} as {e.entry_api_name!r} but "
            f"the live profile is {actual!r}; not crawled, because a squad read "
            f"under the wrong name is a misattribution, not data"
        )
        return
    name_checked = e.entry_api_name is not None
    frames_acc["dim_manager"].append(pd.DataFrame([{
        "entry_id": e.entry_id,
        "player_name": actual or e.display_name,
        "entry_name": prof.get("name"),
        "region": prof.get("player_region_name"),
        "years_active": prof.get("years_active"),
        "favourite_team_id": prof.get("favourite_team"),
        "started_event": prof.get("started_event"),
        "source": SOURCE,
        "as_of": now,
    }]))

    # -- history, picks, transfers: the shared ingest path ---------------------
    # Each call takes a one-element list so a refusal is attributable to THIS
    # person and the frames of everyone before them survive it.
    try:
        past, current, chips, missing = history_mod.ingest_histories(
            fetcher, [e.entry_id], season=season)
        rec["history_gws"] = len(current)
        rec["history_404"] = bool(missing)
        frames_acc["fact_manager_season"].append(past)
        frames_acc["fact_manager_gw"].append(current)
        frames_acc["fact_manager_chip"].append(chips)

        if live_gws:
            picks, pchips, pstats = picks_mod.ingest_picks(
                fetcher, [e.entry_id], live_gws, season=season,
                deadlines=deadlines, now=now)
            frames_acc["fact_manager_pick"].append(picks)
            frames_acc["fact_manager_chip"].append(pchips)
            got_gws = (sorted({int(g) for g in picks["gw"].unique()})
                       if not picks.empty else [])
            rec["picks_gws"] = got_gws
            rec["picks_404_gws"] = [g for g in live_gws if g not in got_gws]
            rec["picks_404"] = int(pstats.get("not_found", 0))

            transfers, tstats = picks_mod.ingest_transfers(
                fetcher, [e.entry_id], season=season, deadlines=deadlines)
            frames_acc["fact_manager_transfer"].append(transfers)
            rec["transfers"] = len(transfers)
            if tstats.get("budget_exhausted"):
                # ingest_transfers absorbs its own BudgetExhausted and returns
                # the partial frame; surface it so the loop stops honestly.
                raise BudgetExhausted(tstats["budget_exhausted"])
    except httpx.HTTPError as exc:
        req = getattr(exc, "request", None)
        where = str(req.url.path) if req is not None else f"entry/{e.entry_id}/..."
        _refusal(rec, exc, where)
        return

    if rec.get("history_404"):
        rec["status"] = "partial"
        rec["detail"] = "profile answered 200 but entry/history/ answered 404"
    elif rec["picks_404_gws"]:
        rec["status"] = "partial"
        rec["detail"] = (f"picks answered 404 for locked GW"
                         f"{', GW'.join(str(g) for g in rec['picks_404_gws'])}")
    else:
        rec["status"] = "ok"
        rec["detail"] = ("name checked against the live profile" if name_checked
                         else "unchecked_name: roster has no entry_api_name to "
                              "compare the live profile against")


def _failures(summary: dict[str, Any], frames: dict[str, pd.DataFrame]) -> list[str]:
    """Reasons the run must not present as success. Same question as the
    cohort crawl asks of every stage: if this did nothing, what would be
    different?"""
    out: list[str] = []
    if not summary.get("selected"):
        out.append("selection: panel_person has no active, verified person with "
                   "an entry_id, so there was nobody to crawl")
        return out
    for p in summary.get("not_ok") or []:
        out.append(f"{p['person']} ({p['entry_id']}): {p['status']}"
                   + (f" -- {p['detail']}" if p.get("detail") else ""))
    if summary.get("budget_exhausted"):
        out.append(f"budget: {summary['budget_exhausted']}")
    write = summary.get("write")
    if frames and write is None:
        out.append("write: frames were collected but the commit step never ran")
    elif frames and (write or {}).get("status") != "ok":
        out.append(f"write: {(write or {}).get('status')}: {(write or {}).get('error', '')}")
    elif frames and not (write or {}).get("rows"):
        out.append("write: committed zero rows although frames were non-empty")
    return out


def run(
    *,
    budget_limit: int = 700,
    db_path: str | None = None,
    offline: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Select, fetch, write, report. The selection is read from the warehouse
    the frames are then written to."""
    # Selection is a read; take it from a copy so the crawl holds no lock
    # during the minutes it spends being polite to the API.
    wh = Warehouse.read_copy() if db_path is None else Warehouse.read_copy(db_path)
    try:
        entries = select_entries(wh)
    finally:
        wh.close()
    n = len(entries)
    plan = {
        "people": n,
        "entry_ids": [e.entry_id for e in entries],
        "requests_worst_case": 1 + n * (FIXED_REQUESTS_PER_PERSON + MAX_GWS),
        "note": "1 bootstrap + per person: profile, history, transfers, and one "
                "picks request per locked gameweek; finished-GW picks are "
                "cached forever so steady state is ~2 requests per person",
    }
    if dry_run:
        return {"dry_run": True, "plan": plan,
                "people": [{"person": e.display_name, "entry_id": e.entry_id,
                            "entry_api_name": e.entry_api_name} for e in entries]}

    budget = RequestBudget(limit=budget_limit)
    fetcher = RivalsFetcher(budget, offline=offline)
    summary: dict[str, Any] = {"plan": plan, "selected": n}
    frames: dict[str, pd.DataFrame] = {}
    try:
        if entries:
            frames, collected = collect(fetcher, entries)
            summary.update(collected)
    except BudgetExhausted as exc:
        # Only the bootstrap request can raise here; collect() catches its own.
        summary["budget_exhausted"] = str(exc)
    finally:
        fetcher.close()

    if frames:
        summary["write"] = _write(frames, db_path, summary)
    summary["failures"] = _failures(summary, frames)
    summary["requests"] = {
        "limit": budget.limit, "spent": budget.spent,
        "cache_hits": budget.cache_hits, "receipt": budget.receipt(),
    }
    return summary


def _one_line(summary: dict[str, Any]) -> str:
    """The line a step's 300-character tail keeps: who is ok and who is not."""
    people = summary.get("people") or []
    ok = sum(1 for p in people if p["status"] == "ok")
    parts = [f"panel_picks_crawl: {ok}/{len(people)} people ok"]
    if people:
        gws = sorted({g for p in people for g in p.get("picks_gws", [])})
        parts.append("picks GW" + ",".join(str(g) for g in gws) if gws else "no picks")
    bad = summary.get("not_ok") or []
    if bad:
        parts.append("not ok: " + "; ".join(
            f"{p['person']}={p['status']}" for p in bad))
    write = summary.get("write") or {}
    if write:
        parts.append(f"write={write.get('status')} rows={write.get('rows')}")
    return " | ".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--budget", type=int, default=700,
                    help="hard cap on network requests; sized for a cold "
                         "full-season crawl of the whole panel")
    ap.add_argument("--db", default=None)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    out = run(budget_limit=args.budget, db_path=args.db,
              offline=args.offline, dry_run=args.dry_run)
    print(json.dumps(out, indent=2, default=str))
    if out.get("dry_run"):
        return 0
    print(_one_line(out))
    failures = out.get("failures") or []
    if failures:
        print("FAILED: " + " || ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
