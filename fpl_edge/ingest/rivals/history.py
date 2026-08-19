"""Season and gameweek histories: the only long-run evidence of manager skill.

``/api/entry/{id}/history/`` returns three blocks:

``past``
    One row per completed season: ``season_name``, ``total_points``, ``rank``,
    ``rank_percentage``. There is no deeper archive -- FPL does not expose a
    past season's picks, transfers or chips to anyone, ever. So a manager's
    entire multi-season record, for skill-scoring purposes, is a sequence of
    final ranks. Every claim this package makes about "consistency across
    seasons" is built from exactly that, and nothing richer exists to build it
    from.

``current``
    One row per gameweek of the season in progress, including
    ``event_transfers`` and ``event_transfers_cost`` -- which is how hit-taking
    behaviour becomes measurable without needing the picks endpoint at all.

``chips``
    Chips used *this* season, with the gameweek. Empty before GW1.

Observability
-------------
``as_of`` for a ``past`` row is the fetch instant: a completed season's final
rank has been public since that season ended, and we could have read it any time
after. For a ``current`` gameweek row the honest instant is when that gameweek's
points were finalised, but the API does not state it, so the fetch instant is
used and is an upper bound -- conservative in the safe direction, since it makes
a fact look *later*-known than it was, never earlier.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from fpl_edge.ingest.rivals.client import RivalsFetcher

#: FPL's ``rank_percentage`` is a string, rounded to wildly varying precision
#: ('0.2', '1', '13'). Parsed to float where possible and left null otherwise
#: rather than defaulted, because it is used to estimate season field sizes and
#: a fabricated zero would corrupt that estimate.
def _pct(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _season_label(fpl_label: str) -> str:
    """Keep FPL's own '2024/25' label verbatim.

    Deliberately NOT normalised to the warehouse's '2024-25' form. These rows
    describe a manager's finish, not the game state, and they never join to
    ``dim_player``; converting would invent an equivalence between two things
    the API happens to spell differently and would be a silent lie the first
    time FPL changed either format.
    """
    return fpl_label


def fetch_history(fetcher: RivalsFetcher, entry_id: int) -> dict[str, Any] | None:
    got = fetcher.get_json(f"entry/{entry_id}/history/")
    return got.body


def parse_history(
    entry_id: int, body: dict[str, Any], *, as_of: dt.datetime, season: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Split one history payload into (past seasons, current gameweeks, chips)."""
    past = pd.DataFrame([
        {
            "entry_id": entry_id,
            "season": _season_label(row.get("season_name", "")),
            "total_points": row.get("total_points"),
            "overall_rank": row.get("rank"),
            "rank_percentage": _pct(row.get("rank_percentage")),
            # Verbatim, because the printed precision is data. See schema.py.
            "rank_percentage_text": (
                None if row.get("rank_percentage") in (None, "")
                else str(row.get("rank_percentage"))
            ),
            "as_of": as_of,
        }
        for row in body.get("past") or []
        if row.get("season_name")
    ])

    current = pd.DataFrame([
        {
            "entry_id": entry_id, "season": season, "gw": row.get("event"),
            "points": row.get("points"), "total_points": row.get("total_points"),
            "overall_rank": row.get("overall_rank"),
            "bank_tenths": row.get("bank"), "value_tenths": row.get("value"),
            "event_transfers": row.get("event_transfers"),
            "event_transfers_cost": row.get("event_transfers_cost"),
            "points_on_bench": row.get("points_on_bench"),
            "as_of": as_of,
        }
        for row in body.get("current") or []
        if row.get("event") is not None
    ])

    chips = pd.DataFrame([
        {
            "entry_id": entry_id, "season": season, "gw": row.get("event"),
            "chip": row.get("name"), "as_of": as_of,
        }
        for row in body.get("chips") or []
        if row.get("event") is not None and row.get("name")
    ])

    for df in (past, current, chips):
        if df.empty:
            continue
    return past, current, chips


def ingest_histories(
    fetcher: RivalsFetcher,
    entry_ids: list[int],
    *,
    season: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[int]]:
    """Fetch and parse histories for a list of entries.

    Returns the three frames concatenated plus the list of entry IDs that
    answered 404 -- deleted or never-existed entries, which are dropped rather
    than retried. A 404 here is final: entry IDs are never reused.
    """
    as_of = dt.datetime.now(dt.timezone.utc)
    pasts, currents, chips_all, missing = [], [], [], []
    for eid in entry_ids:
        body = fetch_history(fetcher, eid)
        if body is None:
            missing.append(eid)
            continue
        p, c, ch = parse_history(eid, body, as_of=as_of, season=season)
        if not p.empty:
            pasts.append(p)
        if not c.empty:
            currents.append(c)
        if not ch.empty:
            chips_all.append(ch)

    def _cat(frames: list[pd.DataFrame], cols: list[str]) -> pd.DataFrame:
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)

    return (
        _cat(pasts, ["entry_id", "season", "total_points", "overall_rank",
                     "rank_percentage", "rank_percentage_text", "as_of"]),
        _cat(currents, ["entry_id", "season", "gw", "points", "total_points",
                        "overall_rank", "bank_tenths", "value_tenths",
                        "event_transfers", "event_transfers_cost",
                        "points_on_bench", "as_of"]),
        _cat(chips_all, ["entry_id", "season", "gw", "chip", "as_of"]),
        missing,
    )
