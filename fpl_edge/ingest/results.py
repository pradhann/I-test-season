"""Settle a finished gameweek's results into ``fact_player_fixture`` — live.

The audit's highest-leverage finding (docs/platform/data_audit.md): the only
writer of ``fact_player_fixture`` was the vaastav HISTORICAL ingest, so the
current season would have stayed at zero rows forever. Everything downstream
starves on that: ``projection_weight`` can never be earned, creator claims can
never be scored, and current-season form does not exist. This module settles
each gameweek first-hand from FPL's own API the morning after it completes.

Sources, both official and free:

* ``event/{gw}/live/`` — per-element stats for the gameweek, including the
  official xG/xA/xGC, plus ``explain`` blocks naming the fixture(s) behind
  the points.
* ``fixtures/?event={gw}`` — kickoff times and the ``finished`` flags.
* ``event-status/`` — FPL's own word on whether bonus has been added.

Honesty rules:

* **The gate is FPL's, not ours.** A gameweek settles only when every fixture
  is ``finished`` AND either every day's ``bonus_added`` is true or the
  points-finalisation instant (09:00 UK the day after the last kickoff — the
  same verified rule the historical ingest stamps with) has passed. Until
  then the function refuses with the reason; provisional numbers are never
  written as facts.
* **``as_of`` is the observation instant** (the fetch time). Unlike the
  historical backfill, which reconstructs when facts *became* public, a live
  settlement observed the API now and says so.
* **Double gameweeks are not smeared.** Per-fixture rows are built from the
  ``explain`` blocks, which carry the point-scoring raw stats per fixture.
  The stats FPL publishes only as gameweek totals (bps, xG, xA, xGC, ICT)
  cannot be attributed to a single fixture of a DGW without inventing a
  split, so on multi-fixture gameweeks those columns are NULL and the totals
  live on whichever analysis sums the gameweek. Single-fixture gameweeks —
  the overwhelmingly common case — carry every column.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from fpl_edge.ingest.http import Fetcher
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc
UK = ZoneInfo("Europe/London")
BASE = "https://fantasy.premierleague.com/api"
POINTS_FINAL_HOUR = 9

#: fact_player_fixture columns that exist as identically-named per-GW totals
#: in the live endpoint's ``stats`` block.
_TOTAL_COLUMNS = (
    "minutes", "goals_scored", "assists", "clean_sheets", "goals_conceded",
    "own_goals", "penalties_saved", "penalties_missed", "yellow_cards",
    "red_cards", "saves", "bonus", "bps", "starts", "tackles",
    "clearances_blocks_interceptions", "recoveries", "defensive_contribution",
    "total_points",
)
#: GW-total-only columns that cannot be split across a double gameweek.
_UNSPLITTABLE = ("bps", "expected_goals", "expected_assists",
                 "expected_goals_conceded")


class NotFinalError(RuntimeError):
    """The gameweek is not settled by FPL's own account. Nothing was written."""


def _points_final_utc(last_kickoff: dt.datetime) -> dt.datetime:
    uk_date = last_kickoff.astimezone(UK).date() + dt.timedelta(days=1)
    return dt.datetime.combine(
        uk_date, dt.time(POINTS_FINAL_HOUR), tzinfo=UK
    ).astimezone(UTC)


def assert_final(
    gw: int,
    fixtures: list[Mapping[str, Any]],
    event_status: Mapping[str, Any],
    *,
    now: dt.datetime,
) -> None:
    """Raise :class:`NotFinalError` unless FPL says the gameweek is done.

    FPL's flags, measured live on GW1 2026-27: ``finished_provisional`` flips
    at the whistle, ``finished`` only when the gameweek fully processes
    (typically with ``bonus_added``). So the gate is:

    * every fixture at least PROVISIONALLY finished -- an unplayed or
      abandoned match blocks settlement outright, with no time override; and
    * either FPL marks the gameweek fully processed (all ``finished`` and
      ``bonus_added``), or the verified points-finalisation rule (09:00 UK
      the day after the last kickoff) has passed. The time rule may stand in
      for the flags, because the flags have been observed to lag.
    """
    not_played = [
        f["id"] for f in fixtures
        if not (f.get("finished") or f.get("finished_provisional"))
    ]
    if not_played:
        raise NotFinalError(
            f"GW{gw}: {len(not_played)} fixture(s) not even provisionally "
            f"finished (ids {not_played[:5]}). Settling now would write "
            "numbers for matches that have not been played out."
        )
    kickoffs = [
        dt.datetime.fromisoformat(f["kickoff_time"].replace("Z", "+00:00"))
        for f in fixtures if f.get("kickoff_time")
    ]
    final_at = _points_final_utc(max(kickoffs)) if kickoffs else None
    past_final = final_at is not None and now >= final_at
    days = [s for s in event_status.get("status", []) if s.get("event") == gw]
    bonus_done = bool(days) and all(s.get("bonus_added") for s in days)
    fully_finished = all(f.get("finished") for f in fixtures)
    if not ((bonus_done and fully_finished) or past_final):
        detail = (
            f"finished {sum(bool(f.get('finished')) for f in fixtures)}"
            f"/{len(fixtures)}, bonus_added={bonus_done}"
        )
        raise NotFinalError(
            f"GW{gw}: not fully processed by FPL ({detail}) and the "
            f"points-finalisation instant ({final_at:%Y-%m-%d %H:%M}Z) has "
            "not passed. Refusing to settle provisional numbers."
        )


def build_rows(
    season: str,
    gw: int,
    live: Mapping[str, Any],
    fixtures: list[Mapping[str, Any]],
    code_by_element: Mapping[int, int],
    *,
    as_of: dt.datetime,
) -> tuple[pd.DataFrame, list[str]]:
    """Per-(player, fixture) rows from the live payload. Pure; testable."""
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []
    skipped_unmapped = 0

    for el in live.get("elements", []):
        element_id = int(el["id"])
        code = code_by_element.get(element_id)
        if code is None:
            skipped_unmapped += 1
            continue
        stats = el.get("stats", {})
        blocks = [b for b in el.get("explain", []) if b.get("stats")]
        fixture_ids = [int(b["fixture"]) for b in blocks]
        if not fixture_ids:
            continue                       # did not feature: no fixture rows
        multi = len(fixture_ids) > 1

        for block in blocks:
            fid = int(block["fixture"])
            per_fixture = {
                s["identifier"]: s["value"] for s in block.get("stats", [])
            }
            row: dict[str, Any] = {
                "season": season, "code": int(code), "fixture_id": fid,
                "gw": int(gw), "as_of": as_of,
            }
            for col in _TOTAL_COLUMNS:
                if multi:
                    # explain carries the point-scoring raw stats per fixture;
                    # a stat absent from the block genuinely was 0 there.
                    row[col] = int(per_fixture.get(col, 0))
                else:
                    row[col] = int(stats.get(col, 0) or 0)
            if multi:
                # total_points per fixture = sum of that block's points.
                row["total_points"] = int(
                    sum(s.get("points", 0) for s in block.get("stats", []))
                )
                for col in _UNSPLITTABLE:
                    row[col] = None
            else:
                for col in ("expected_goals", "expected_assists",
                            "expected_goals_conceded"):
                    v = stats.get(col)
                    row[col] = float(v) if v is not None else None
                row["bps"] = int(stats.get("bps", 0) or 0)
            # was_home needs the player's team, which the live payload does not
            # carry; NULL is honest and nothing downstream of settlement
            # requires it yet. (The fixture's home team is known; the player's
            # side is not, without a second join the caller can add later.)
            row["was_home"] = None
            rows.append(row)
        if multi:
            warnings.append(
                f"element {element_id}: {len(fixture_ids)} fixtures in GW{gw}; "
                "bps/xG/xA/xGC left NULL per fixture (only GW totals exist)."
            )

    if skipped_unmapped:
        warnings.append(
            f"{skipped_unmapped} element(s) had no code mapping at the "
            "snapshot and were skipped, loudly."
        )
    return pd.DataFrame(rows), warnings


def settle_gameweek(
    wh: Warehouse,
    season: str,
    gw: int,
    *,
    fetcher: Fetcher | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Fetch, gate, build and append. Returns a small report dict."""
    now = now or dt.datetime.now(UTC)
    fetcher = fetcher or Fetcher(source="fpl_api")

    fixtures = fetcher.get_json(f"{BASE}/fixtures/?event={int(gw)}").body
    status = fetcher.get_json(f"{BASE}/event-status/").body
    assert_final(gw, fixtures, status, now=now)
    live = fetcher.get_json(f"{BASE}/event/{int(gw)}/live/").body

    snap = wh.snapshot_at(now)
    players = snap.players(season)
    code_by_element = dict(
        zip(players["element_id"].astype(int), players["code"].astype(int))
    )
    rows, warnings = build_rows(
        season, gw, live, fixtures, code_by_element, as_of=now
    )
    written = wh.append("fact_player_fixture", rows) if not rows.empty else 0
    return {
        "season": season, "gw": int(gw), "rows_built": int(len(rows)),
        "rows_written": int(written), "warnings": warnings,
    }


def main() -> int:
    """Settle every completed-but-unsettled gameweek of the current season.

    Runs from the post-GW job. A gameweek FPL still calls provisional is
    refused (NotFinalError) and reported as pending, not failed -- the next
    run picks it up.
    """
    import json as _json

    season = "2026-27"
    with Warehouse() as wh:
        now = dt.datetime.now(UTC)
        snap = wh.snapshot_at(now)
        have = set(
            wh.sql(
                "SELECT DISTINCT gw FROM fact_player_fixture WHERE season = ?",
                [season],
            )["gw"].astype(int)
        )
        events = wh.sql(
            "SELECT DISTINCT gw FROM dim_event WHERE season = ? AND deadline_utc < ?",
            [season, now],
        )
        reports = []
        for gw in sorted(int(g) for g in events["gw"]):
            if gw in have:
                continue
            try:
                reports.append(settle_gameweek(wh, season, gw, now=now))
            except NotFinalError as exc:
                reports.append({"season": season, "gw": gw, "pending": str(exc)})
    print(_json.dumps(reports, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
