"""Squads and transfers, gameweek by gameweek.

Availability, stated plainly because it drives every design choice below
-----------------------------------------------------------------------
``/api/entry/{id}/event/{gw}/picks/`` returns **404 for every entry until that
gameweek's deadline passes**, and 200 forever afterwards. Verified against the
live API on 2026-08-18, before the 2026-27 GW1 deadline of 2026-08-21T17:30Z::

    entry/4490171/event/38/picks/  ->  404  {"detail": "Not found."}
    entry/200/transfers/           ->  200  []

That is not an error condition to be handled defensively; it is the schedule
this module runs on. A 404 means "not yet", so :func:`ingest_picks` returns it
as a count rather than raising, and the caller can run the same command every
day and have it do nothing until there is something to do.

``/api/entry/{id}/transfers/`` returns the current season's transfers only, and
is cumulative -- every call returns the whole season, so it is fetched once per
manager per crawl rather than once per manager per gameweek.

Observability
-------------
A squad becomes public **at the deadline**, so ``as_of`` for a pick is that
gameweek's deadline, not the crawl instant. This matters more than it looks: if
picks were stamped with the crawl time, a Sunday-evening crawl would make the
elite's Saturday squad appear to have been unknowable until after their
captain's hat-trick, and every backtest of "copy the elite" would then be
scored against information it could not have had. Stamping the deadline is what
makes the copying model honest.

A transfer's public instant is likewise the deadline of the gameweek it applies
to, not the ``time`` field in the payload -- that is when the manager *made* the
transfer, which is private until it locks. The raw time is kept in ``time_utc``
because transfer timing (early-week punt versus deadline-day reaction to team
news) is itself a behaviour worth measuring.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals.client import RivalsFetcher


def _ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def parse_picks(
    entry_id: int, gw: int, body: dict[str, Any], *, season: str, deadline: dt.datetime
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """One picks payload into (picks, chip) frames.

    ``multiplier`` is taken from the API rather than derived from
    ``is_captain``: under Bench Boost a benched player's multiplier is 1, not 0,
    and under Triple Captain the captain's is 3. Reconstructing it from the
    boolean flags gets both cases wrong, and both cases are precisely the ones
    a chip-timing analysis cares about.
    """
    picks = pd.DataFrame([
        {
            "entry_id": entry_id, "season": season, "gw": gw,
            "element_id": p["element"], "slot": p.get("position"),
            "multiplier": p.get("multiplier"),
            "is_captain": bool(p.get("is_captain")),
            "is_vice_captain": bool(p.get("is_vice_captain")),
            "as_of": deadline,
        }
        for p in body.get("picks") or []
    ])

    chip_name = body.get("active_chip")
    chips = pd.DataFrame(
        [{"entry_id": entry_id, "season": season, "gw": gw,
          "chip": chip_name, "as_of": deadline}]
        if chip_name else [],
        columns=["entry_id", "season", "gw", "chip", "as_of"],
    )
    return picks, chips


def parse_transfers(
    entry_id: int,
    body: list[dict[str, Any]],
    *,
    season: str,
    deadlines: dict[int, dt.datetime],
) -> pd.DataFrame:
    """The whole season's transfers into one frame.

    Transfers for a gameweek whose deadline is not in ``deadlines`` are dropped
    rather than stamped with the crawl time. A transfer we cannot date correctly
    is a leakage hazard, and silently admitting one to keep a row count up is
    the exact trade this warehouse refuses to make elsewhere.
    """
    rows = []
    for t in body or []:
        gw = t.get("event")
        if gw is None or gw not in deadlines:
            continue
        rows.append({
            "entry_id": entry_id, "season": season, "gw": int(gw),
            "element_in": t.get("element_in"),
            "element_in_cost": t.get("element_in_cost"),
            "element_out": t.get("element_out"),
            "element_out_cost": t.get("element_out_cost"),
            "time_utc": _ts(t.get("time")),
            "as_of": deadlines[int(gw)],
        })
    return pd.DataFrame(rows, columns=[
        "entry_id", "season", "gw", "element_in", "element_in_cost",
        "element_out", "element_out_cost", "time_utc", "as_of",
    ])


def ingest_picks(
    fetcher: RivalsFetcher,
    entry_ids: list[int],
    gws: list[int],
    *,
    season: str,
    deadlines: dict[int, dt.datetime],
    now: dt.datetime | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Crawl squads for a set of managers across a set of gameweeks.

    Gameweeks whose deadline has not passed are skipped **without a request**.
    Asking anyway would spend one request per manager per future gameweek to be
    told 404, which on a 600-manager pool is 600 wasted requests for a fact the
    fixture list already tells us for free.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    live = [gw for gw in sorted(gws) if gw in deadlines and deadlines[gw] <= now]
    skipped_future = len(gws) - len(live)

    picks_frames: list[pd.DataFrame] = []
    chip_frames: list[pd.DataFrame] = []
    stats = {"requested": 0, "ok": 0, "not_found": 0,
             "skipped_future_gws": skipped_future, "managers": len(entry_ids)}

    for gw in live:
        for eid in entry_ids:
            got = fetcher.get_json(f"entry/{eid}/event/{gw}/picks/")
            stats["requested"] += 1
            if got.body is None:
                stats["not_found"] += 1
                continue
            stats["ok"] += 1
            p, c = parse_picks(eid, gw, got.body, season=season, deadline=deadlines[gw])
            if not p.empty:
                picks_frames.append(p)
            if not c.empty:
                chip_frames.append(c)

    picks = pd.concat(picks_frames, ignore_index=True) if picks_frames else pd.DataFrame(
        columns=["entry_id", "season", "gw", "element_id", "slot", "multiplier",
                 "is_captain", "is_vice_captain", "as_of"]
    )
    chips = pd.concat(chip_frames, ignore_index=True) if chip_frames else pd.DataFrame(
        columns=["entry_id", "season", "gw", "chip", "as_of"]
    )
    return picks, chips, stats


def ingest_transfers(
    fetcher: RivalsFetcher,
    entry_ids: list[int],
    *,
    season: str,
    deadlines: dict[int, dt.datetime],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """One request per manager for the whole season's transfers."""
    frames: list[pd.DataFrame] = []
    stats = {"requested": 0, "ok": 0, "not_found": 0, "empty": 0}
    for eid in entry_ids:
        got = fetcher.get_json(f"entry/{eid}/transfers/")
        stats["requested"] += 1
        if got.body is None:
            stats["not_found"] += 1
            continue
        stats["ok"] += 1
        if not got.body:
            stats["empty"] += 1
            continue
        df = parse_transfers(eid, got.body, season=season, deadlines=deadlines)
        if not df.empty:
            frames.append(df)
    transfers = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["entry_id", "season", "gw", "element_in", "element_in_cost",
                 "element_out", "element_out_cost", "time_utc", "as_of"]
    )
    return transfers, stats
