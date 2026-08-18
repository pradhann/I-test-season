"""Ingest the official FPL API into the point-in-time warehouse.

``as_of`` for everything here is the fetch instant, because that is genuinely
when we could first have observed it. The one exception is finalised match
returns, whose observability is governed by the points-finalisation rule
(09:00 UK the day after the gameweek's final match) rather than by when we got
around to polling.
"""

from __future__ import annotations

import datetime as dt
import warnings
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import Fetcher
from fpl_edge.store import Warehouse
from fpl_edge.types import Position

BASE = "https://fantasy.premierleague.com/api"

#: FPL's own season label, e.g. "2026-27".
def season_label(bootstrap: dict[str, Any]) -> str:
    """Derive the season from the first gameweek deadline.

    The API does not state the season anywhere, so we infer it from GW1's
    deadline year. A season starting in August 2026 is "2026-27".
    """
    events = bootstrap["events"]
    first = min(events, key=lambda e: e["deadline_time"])
    year = dt.datetime.fromisoformat(first["deadline_time"].replace("Z", "+00:00")).year
    return f"{year}-{str(year + 1)[-2:]}"


def _ts(value: str | None) -> dt.datetime | None:
    """Parse an FPL timestamp, refusing to return a naive datetime.

    Not every FPL timestamp carries a 'Z'. The old implementation returned a
    naive datetime for those, which then flowed into kickoff_utc / news_added
    and was reinterpreted as local time downstream.
    """
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # FPL publishes in UTC; an absent offset is a formatting quirk, not a
        # local time. Attach it explicitly so nothing downstream has to guess.
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def ingest_bootstrap(wh: Warehouse, fetcher: Fetcher | None = None) -> dict[str, int]:
    """Fetch bootstrap-static and write events, teams, players and player state."""
    owns_fetcher = fetcher is None
    fetcher = fetcher or Fetcher("fpl_api", base_url=BASE)
    try:
        got = fetcher.get_json("bootstrap-static/")
    finally:
        if owns_fetcher:
            fetcher.close()

    bs = got.body
    as_of = got.fetched_at
    season = season_label(bs)
    wh.record_fetch(
        source="fpl_api", endpoint="bootstrap-static/", params=None,
        fetched_at=as_of, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )

    events = pd.DataFrame([
        {"season": season, "gw": e["id"], "deadline_utc": _ts(e["deadline_time"]),
         "is_finished": e["finished"], "as_of": as_of}
        for e in bs["events"]
    ])

    teams = pd.DataFrame([
        {"season": season, "team_code": t["code"], "team_id": t["id"],
         "name": t["name"], "short_name": t["short_name"], "as_of": as_of}
        for t in bs["teams"]
    ])

    team_code_by_id = {t["id"]: t["code"] for t in bs["teams"]}

    players, states, skipped, temporary = [], [], 0, []
    for el in bs["elements"]:
        if el.get("has_temporary_code"):
            # FPL issues a placeholder `code` to a player whose registration is
            # incomplete, then REPLACES it later. Since `code` is our stable
            # cross-season join key, a temporary one will silently split that
            # player's history in two. Record them so the audit can re-map.
            temporary.append((el["code"], el.get("web_name")))
        try:
            pos = Position.from_api(el["element_type"])
        except ValueError:
            # Manager elements (2025/26 element_type 5) are dropped, never coerced.
            skipped += 1
            continue
        players.append({
            "season": season, "code": el["code"], "element_id": el["id"],
            "web_name": el["web_name"], "first_name": el.get("first_name"),
            "second_name": el.get("second_name"), "position": int(pos),
            "team_code": team_code_by_id[el["team"]], "as_of": as_of,
        })
        states.append({
            "season": season, "code": el["code"], "element_id": el["id"],
            "price_tenths": el["now_cost"],
            # None means "we do not know", 0.0 means "nobody owns him". Coercing
            # the first into the second fabricates the single most decision-
            # relevant field for a differential.
            "selected_by_pct": (
                float(el["selected_by_percent"])
                if el.get("selected_by_percent") not in (None, "")
                else None
            ),
            "status": el.get("status"),
            "chance_of_playing_next_round": el.get("chance_of_playing_next_round"),
            "news": el.get("news") or "",
            "news_added": _ts(el.get("news_added")),
            "transfers_in_event": el.get("transfers_in_event"),
            "transfers_out_event": el.get("transfers_out_event"),
            "cost_change_start": el.get("cost_change_start"),
            "can_select": el.get("can_select"),
            "can_transact": el.get("can_transact"),
            "removed": el.get("removed"),
            "as_of": as_of,
        })

    written = {
        "dim_event": wh.append("dim_event", events),
        "dim_team": wh.append("dim_team", teams),
        "dim_player": wh.append("dim_player", pd.DataFrame(players)),
        "fact_player_state": wh.append("fact_player_state", pd.DataFrame(states)),
    }
    written["skipped_non_player_elements"] = skipped
    written["temporary_codes"] = len(temporary)
    if temporary:
        warnings.warn(
            f"{len(temporary)} player(s) carry has_temporary_code=True and will be "
            f"reissued a permanent code later: {temporary[:5]}. Cross-season joins "
            f"on these codes will split the player's history until they are remapped.",
            RuntimeWarning,
            stacklevel=2,
        )
    return written


def ingest_fixtures(wh: Warehouse, fetcher: Fetcher | None = None,
                    season: str | None = None) -> dict[str, int]:
    """Fetch the fixture list.

    Fixture scheduling is public in advance, so storing it is not leakage. Score
    columns are only populated for fixtures already finished at fetch time, and
    ``as_of`` filtering keeps them hidden from earlier snapshots.
    """
    owns_fetcher = fetcher is None
    fetcher = fetcher or Fetcher("fpl_api", base_url=BASE)
    try:
        if season is None:
            season = season_label(fetcher.get_json("bootstrap-static/").body)
        bs = fetcher.get_json("bootstrap-static/").body
        got = fetcher.get_json("fixtures/")
    finally:
        if owns_fetcher:
            fetcher.close()

    team_code_by_id = {t["id"]: t["code"] for t in bs["teams"]}
    as_of = got.fetched_at
    wh.record_fetch(
        source="fpl_api", endpoint="fixtures/", params=None, fetched_at=as_of,
        sha256=got.sha256, body_path=str(got.body_path), http_status=got.http_status,
    )

    rows = [{
        "season": season, "fixture_id": f["id"], "gw": f.get("event"),
        "kickoff_utc": _ts(f.get("kickoff_time")),
        "home_team_code": team_code_by_id[f["team_h"]],
        "away_team_code": team_code_by_id[f["team_a"]],
        "finished": f.get("finished"),
        "home_score": f.get("team_h_score"), "away_score": f.get("team_a_score"),
        "as_of": as_of,
    } for f in got.body]

    return {"fact_fixture": wh.append("fact_fixture", pd.DataFrame(rows))}
