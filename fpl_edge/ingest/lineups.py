"""Confirmed starting lineups from the Premier League's own Pulselive API.

The highest-latency edge in FPL: teamsheets publish roughly an hour before
kickoff, and the first deadline-relevant consumer is the T-90m
``lineup_captain_check`` DAG task. This module lands the feed the task was
built to wake up on (docs/platform/MASTER_PROMPT.md Phase 3.1).

The source, measured live 2026-08-24:

* ``GET https://footballapi.pulselive.com/football/fixtures?comps=1&...``
  lists fixtures (``statuses=C`` completed / ``U,L`` upcoming+live).
* ``GET https://footballapi.pulselive.com/football/fixtures/{id}`` carries
  ``teamLists``: 2 sides, each ``{teamId, formation{label}, lineup[11],
  substitutes[9]}``. Before the teamsheet drops, ``teamLists`` is
  ``[null, null]`` -- that is the "not yet" signal, not an error.
* The ``playerId`` field on lineup entries is 0. The real id is the nested
  player object's ``id``; ``altIds.opta`` ("p231416") is carried alongside.
* There is no bulk teamlists endpoint (``/football/teamlists`` 404s), so the
  ingest polls per fixture -- politely, one request a second, with the
  project's identified User-Agent, every body archived by :class:`Fetcher`.

Posture: this is premierleague.com's own internal API, undocumented, with no
published terms found (see docs/data_sources.md §"Pulselive"). Free and
unauthenticated, but that earns politeness, not entitlement: low volume (a
handful of requests per matchday), rate-limited, identified.

The identity bridge -- the hard part
------------------------------------

Pulselive keys players by its own id; the engine keys them by the stable FPL
``code``. The mapping is earned once and persisted:

1. ``bridge_pl_player`` is consulted first: a player matched in any earlier
   run joins by id, no names involved.
2. Otherwise the player is name-matched against ``dim_player`` at the
   snapshot, restricted to their OWN team (via a Pulselive-team -> team_code
   bridge matched on abbreviation, then name). Keys tried: normalised
   ``first last``, normalised ``display``, and the sorted-token form (vaastav
   taught us some East Asian names are stored surname-first: FPL has
   ``Mitoma Kaoru``, Pulselive ``Kaoru Mitoma``). Normalisation is the same
   diacritic-safe fold the rest of the engine uses (the Ødegaard trap lives
   in :func:`fpl_edge.ingest.player_mapping.normalize_name`).
3. Still nothing: a last-name-only match is accepted iff it is unique within
   the team.
4. Any tie at any tier is DROPPED AND COUNTED, never guessed -- the 2022-23
   two-Ben-Davies lesson. Misses are listed by name in the report.

Successful matches are appended to ``bridge_pl_player`` so the name path runs
at most once per player per season. Fixtures get the same treatment:
``bridge_pl_fixture`` maps Pulselive fixture ids to OUR ``fact_fixture`` ids,
matched on kickoff instant plus the (home, away) team pair.

Point-in-time honesty: ``fact_confirmed_lineup.as_of`` is the FETCH instant.
Lineups drop ~T-60m, so a deadline snapshot can never see one -- exactly the
property the leakage guards exist to protect.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import Fetcher
from fpl_edge.ingest.player_mapping import normalize_name
from fpl_edge.store import Warehouse

UTC = dt.UTC

BASE = "https://footballapi.pulselive.com/football"
SOURCE = "pulselive"
SEASON = "2026-27"

#: The endpoint checks these; the identified User-Agent still goes out with
#: every request (Fetcher never lets extra headers override it).
PL_HEADERS = {
    "Origin": "https://www.premierleague.com",
    "Referer": "https://www.premierleague.com/",
}

#: Kickoffs this far ahead of `now` are polled. 2.5h covers the T-90m task's
#: whole horizon: the first kickoff of a gameweek is ~90m after the deadline.
WINDOW_HOURS = 2.5

#: Polite gap between consecutive requests to the endpoint.
SLEEP_S = 1.0


# --------------------------------------------------------------------------
# Parsing. Pure functions over a fixture payload; testable offline.
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineupPlayer:
    pl_id: int
    display: str
    first: str
    last: str
    opta_id: str | None
    shirt: int | None
    position: str | None
    started: bool
    captain: bool


@dataclass(frozen=True, slots=True)
class LineupSide:
    pl_team_id: int
    formation: str | None
    players: tuple[LineupPlayer, ...]

    @property
    def starters(self) -> tuple[LineupPlayer, ...]:
        return tuple(p for p in self.players if p.started)


def kickoff_utc(fixture: Mapping[str, Any]) -> dt.datetime | None:
    """The fixture's kickoff instant. Millis are UTC; the label is BST noise."""
    millis = (fixture.get("kickoff") or {}).get("millis")
    if not millis:
        return None
    return dt.datetime.fromtimestamp(float(millis) / 1000.0, tz=UTC)


def _parse_entry(entry: Mapping[str, Any], *, started: bool) -> LineupPlayer | None:
    # `playerId` on the entry is 0 -- measured, not assumed. The identity is
    # the nested player object's own `id`.
    pl_id = entry.get("id")
    if pl_id in (None, 0):
        return None
    name = entry.get("name") or {}
    info = entry.get("info") or {}
    shirt = entry.get("matchShirtNumber", info.get("shirtNum"))
    position = entry.get("matchPosition", info.get("position"))
    opta = (entry.get("altIds") or {}).get("opta")
    return LineupPlayer(
        pl_id=int(pl_id),
        display=str(name.get("display") or ""),
        first=str(name.get("first") or ""),
        last=str(name.get("last") or ""),
        opta_id=str(opta) if opta else None,
        shirt=int(shirt) if shirt is not None else None,
        position=str(position) if position else None,
        started=started,
        captain=bool(entry.get("captain")),
    )


def parse_team_lists(fixture: Mapping[str, Any]) -> list[LineupSide] | None:
    """The two published sides, or None while the teamsheet has not dropped.

    ``teamLists`` is ``[null, null]`` before publication -- the normal
    pre-T-60m state, reported as "not yet", never as a failure. A side that is
    present but empty (no lineup) is treated the same way.
    """
    raw = fixture.get("teamLists")
    if not raw:
        return None
    sides: list[LineupSide] = []
    for side in raw:
        if not side or not side.get("lineup"):
            return None
        players = [
            p
            for started, block in ((True, "lineup"), (False, "substitutes"))
            for p in (_parse_entry(e, started=started) for e in side.get(block) or [])
            if p is not None
        ]
        formation = ((side.get("formation") or {}).get("label"))
        sides.append(
            LineupSide(
                pl_team_id=int(side["teamId"]),
                formation=str(formation) if formation else None,
                players=tuple(players),
            )
        )
    return sides if len(sides) == 2 else None


# --------------------------------------------------------------------------
# The identity bridge: Pulselive teams/players/fixtures -> our stable keys.
# --------------------------------------------------------------------------


def build_team_bridge(
    pl_teams: Sequence[Mapping[str, Any]],
    dim_team: pd.DataFrame,
) -> tuple[dict[int, int], list[str]]:
    """Pulselive team id -> our stable team_code. Unmatched teams are listed.

    Matched on club abbreviation first (Pulselive 'BHA' == FPL short_name
    'BHA'; measured to agree for the current 20), then on the normalised club
    or team name ('Aston Villa'), which also catches promoted sides. A tie or
    a miss is reported, never guessed.
    """
    by_abbr: dict[str, list[int]] = {}
    by_name: dict[str, list[int]] = {}
    for r in dim_team.itertuples(index=False):
        by_abbr.setdefault(str(r.short_name).upper(), []).append(int(r.team_code))
        by_name.setdefault(normalize_name(r.name), []).append(int(r.team_code))

    out: dict[int, int] = {}
    misses: list[str] = []
    seen: set[int] = set()
    for t in pl_teams:
        team = t.get("team", t)  # fixture listings nest under "team"
        pl_id = int(team["id"])
        if pl_id in seen:
            continue
        seen.add(pl_id)
        club = team.get("club") or {}
        abbr = str(club.get("abbr") or "").upper()
        candidates = by_abbr.get(abbr, []) if abbr else []
        if len(candidates) != 1:
            names = [club.get("name"), team.get("name"), club.get("shortName")]
            hits = {c for n in names if n for c in by_name.get(normalize_name(n), [])}
            candidates = sorted(hits)
        if len(candidates) == 1:
            out[pl_id] = candidates[0]
        else:
            misses.append(
                f"pulselive team {pl_id} ({team.get('name')!r}, abbr {abbr!r}): "
                f"{len(candidates)} candidate team_codes"
            )
    return out, misses


def _name_keys(*parts: str) -> set[str]:
    """Comparable keys for one rendering of a name: the normalised string and
    its sorted-token form (surname-first renderings collapse onto the same
    sorted key without ever reordering anything for display)."""
    keys: set[str] = set()
    for part in parts:
        norm = normalize_name(part)
        if norm:
            keys.add(norm)
            keys.add(" ".join(sorted(norm.split())))
    return keys


@dataclass(slots=True)
class MatchReport:
    """Per-side accounting: nothing is filled silently, misses carry names."""

    matched: int = 0
    via_bridge: int = 0
    dropped_ambiguous: int = 0
    dropped_unmatched: int = 0
    misses: list[str] = field(default_factory=list)


def match_players(
    entries: Sequence[LineupPlayer],
    pool: pd.DataFrame,
    *,
    bridge: Mapping[int, int] | None = None,
) -> tuple[dict[int, tuple[int, str]], MatchReport]:
    """Resolve lineup entries to stable codes against one team's players.

    ``pool`` is dim_player rows (code, web_name, first_name, second_name) for
    the entry's OWN team at the snapshot. Returns ``pl_id -> (code,
    matched_by)``. Ambiguity at any tier drops the player, loudly: a wrong
    code silently poisons every downstream join, an honest miss is just a
    smaller teamsheet.
    """
    bridge = bridge or {}
    full_map: dict[str, set[int]] = {}
    last_map: dict[str, set[int]] = {}
    for r in pool.itertuples(index=False):
        code = int(r.code)
        full = f"{r.first_name or ''} {r.second_name or ''}"
        for key in _name_keys(full, str(r.web_name or "")):
            full_map.setdefault(key, set()).add(code)
        last = normalize_name(r.second_name or "")
        if last:
            last_map.setdefault(last, set()).add(code)
            # web_name is usually the surname-ish handle ("Saka", "J.Timber");
            # its final token is a second last-name rendering.
            web = normalize_name(r.web_name or "")
            if web:
                last_map.setdefault(web.split()[-1], set()).add(code)

    out: dict[int, tuple[int, str]] = {}
    report = MatchReport()
    for e in entries:
        if e.pl_id in bridge:
            out[e.pl_id] = (int(bridge[e.pl_id]), "bridge")
            report.matched += 1
            report.via_bridge += 1
            continue
        keys = _name_keys(f"{e.first} {e.last}", e.display)
        hits = {c for k in keys for c in full_map.get(k, ())}
        why = "name"
        if not hits:
            hits = set(last_map.get(normalize_name(e.last), ()))
            why = "last_name"
        if len(hits) == 1:
            out[e.pl_id] = (next(iter(hits)), why)
            report.matched += 1
        elif len(hits) > 1:
            report.dropped_ambiguous += 1
            report.misses.append(
                f"AMBIGUOUS {e.display!r} (pl {e.pl_id}): codes {sorted(hits)} -- dropped"
            )
        else:
            report.dropped_unmatched += 1
            report.misses.append(f"UNMATCHED {e.display!r} (pl {e.pl_id})")
    return out, report


def match_fixtures(
    pl_fixtures: Sequence[Mapping[str, Any]],
    our_fixtures: pd.DataFrame,
    team_bridge: Mapping[int, int],
) -> dict[int, int]:
    """OUR fixture_id -> Pulselive fixture id, by kickoff instant + team pair."""
    ours: dict[tuple[dt.datetime, int, int], int] = {}
    for r in our_fixtures.itertuples(index=False):
        ko = r.kickoff_utc
        if pd.isna(ko):
            continue
        ko = pd.Timestamp(ko).to_pydatetime().astimezone(UTC)
        ours[(ko, int(r.home_team_code), int(r.away_team_code))] = int(r.fixture_id)

    out: dict[int, int] = {}
    for f in pl_fixtures:
        ko = kickoff_utc(f)
        teams = f.get("teams") or []
        if ko is None or len(teams) != 2:
            continue
        home = team_bridge.get(int(teams[0]["team"]["id"]))  # teams[0] is home
        away = team_bridge.get(int(teams[1]["team"]["id"]))
        if home is None or away is None:
            continue
        fid = ours.get((ko, home, away))
        if fid is not None:
            out[fid] = int(f["id"])
    return out


# --------------------------------------------------------------------------
# The ingest
# --------------------------------------------------------------------------


def _latest(wh: Warehouse, sql: str, params: list[Any]) -> pd.DataFrame:
    return wh.sql(sql, params)


def _stored_bridges(wh: Warehouse, season: str) -> tuple[dict[int, int], dict[int, int]]:
    players = wh.sql(
        "SELECT pl_player_id, code FROM bridge_pl_player WHERE season = ? "
        "QUALIFY row_number() OVER (PARTITION BY pl_player_id ORDER BY as_of DESC) = 1",
        [season],
    )
    fixtures = wh.sql(
        "SELECT pl_fixture_id, fixture_id FROM bridge_pl_fixture WHERE season = ? "
        "QUALIFY row_number() OVER (PARTITION BY pl_fixture_id ORDER BY as_of DESC) = 1",
        [season],
    )
    p = {int(r.pl_player_id): int(r.code) for r in players.itertuples(index=False)}
    f = {int(r.fixture_id): int(r.pl_fixture_id) for r in fixtures.itertuples(index=False)}
    return p, f


def ingest_lineups(
    wh: Warehouse,
    *,
    season: str = SEASON,
    window_hours: float = WINDOW_HOURS,
    fetcher: Fetcher | None = None,
    now: dt.datetime | None = None,
    sleep_s: float = SLEEP_S,
) -> dict[str, Any]:
    """Poll every fixture kicking off within the window; write what published.

    Returns a per-fixture status report: ``published`` (rows written, match
    accounting, misses by name), ``not-yet`` (teamLists still null -- the
    normal pre-T-60m state), or ``unbridged`` (could not be mapped to a
    Pulselive fixture; loud, because silence here would hide a whole match).
    No fixtures in the window -- a blank gameweek, a between-rounds run -- is
    a quiet, empty report, not an error.
    """
    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    snap = wh.snapshot_at(now)
    fixtures = snap.table("fact_fixture", where="season = ?", params=[season])
    report: dict[str, Any] = {"season": season, "now": now.isoformat(), "fixtures": []}
    if fixtures.empty:
        report["note"] = "no fixtures known for the season at this instant"
        return report

    horizon = now + dt.timedelta(hours=window_hours)
    ko = pd.to_datetime(fixtures["kickoff_utc"], utc=True)
    in_window = fixtures[(ko.notna()) & (ko >= now) & (ko <= horizon)].copy()
    if in_window.empty:
        report["note"] = f"no kickoffs within {window_hours}h -- nothing to poll"
        return report
    in_window = in_window.sort_values("kickoff_utc")

    teams = snap.table("dim_team", where="season = ?", params=[season])
    players = snap.table("dim_player", where="season = ?", params=[season])
    player_bridge, fixture_bridge = _stored_bridges(wh, season)

    own_fetcher = fetcher is None
    fetcher = fetcher or Fetcher(source=SOURCE, base_url=BASE, headers=PL_HEADERS)
    first_request = True

    def get(endpoint: str, params: dict[str, Any] | None = None):
        nonlocal first_request
        if not first_request and sleep_s:
            time.sleep(sleep_s)  # polite: 1 req/s, identified, archived
        first_request = False
        return fetcher.get_json(endpoint, params)

    try:
        # -- fixture bridge: only fetch listings when something is unbridged.
        unbridged = [
            int(f) for f in in_window["fixture_id"] if int(f) not in fixture_bridge
        ]
        if unbridged:
            listed: list[Mapping[str, Any]] = []
            for statuses, sort in (("U,L", "asc"), ("C", "desc")):
                got = get(
                    "fixtures",
                    {"comps": 1, "pageSize": 40, "sort": sort, "statuses": statuses},
                )
                listed.extend(got.body.get("content", []))
            team_bridge, team_misses = build_team_bridge(
                [t for f in listed for t in f.get("teams", [])], teams
            )
            if team_misses:
                report["team_bridge_misses"] = team_misses
            new_map = match_fixtures(listed, in_window, team_bridge)
            fresh = {fid: plid for fid, plid in new_map.items() if fid not in fixture_bridge}
            fixture_bridge.update(new_map)
            if fresh:
                wh.append(
                    "bridge_pl_fixture",
                    pd.DataFrame(
                        [
                            {
                                "season": season,
                                "pl_fixture_id": plid,
                                "fixture_id": fid,
                                "as_of": got.fetched_at,
                            }
                            for fid, plid in fresh.items()
                        ]
                    ),
                )

        bridge_rows: list[dict[str, Any]] = []
        # -- per fixture: poll, parse, resolve, append.
        for r in in_window.itertuples(index=False):
            fid = int(r.fixture_id)
            entry: dict[str, Any] = {
                "fixture_id": fid,
                "kickoff_utc": pd.Timestamp(r.kickoff_utc).isoformat(),
            }
            report["fixtures"].append(entry)
            pl_fid = fixture_bridge.get(fid)
            if pl_fid is None:
                entry["status"] = "unbridged"
                continue
            entry["pl_fixture_id"] = pl_fid

            fetched = get(f"fixtures/{pl_fid}")
            sides = parse_team_lists(fetched.body)
            if sides is None:
                entry["status"] = "not-yet"
                continue

            team_bridge, _ = build_team_bridge(fetched.body.get("teams", []), teams)
            rows: list[dict[str, Any]] = []
            entry["sides"] = []
            for side in sides:
                team_code = team_bridge.get(side.pl_team_id)
                side_entry: dict[str, Any] = {
                    "pl_team_id": side.pl_team_id,
                    "team_code": team_code,
                    "formation": side.formation,
                }
                entry["sides"].append(side_entry)
                if team_code is None:
                    side_entry["error"] = "team not bridged; side skipped, loudly"
                    continue
                pool = players[players["team_code"].astype(int) == int(team_code)]
                resolved, mreport = match_players(
                    side.players, pool, bridge=player_bridge
                )
                side_entry.update(
                    matched=mreport.matched,
                    via_bridge=mreport.via_bridge,
                    dropped_ambiguous=mreport.dropped_ambiguous,
                    dropped_unmatched=mreport.dropped_unmatched,
                    misses=mreport.misses,
                )
                for p in side.players:
                    got_code = resolved.get(p.pl_id)
                    if got_code is None:
                        continue
                    code, why = got_code
                    rows.append(
                        {
                            "source": SOURCE,
                            "season": season,
                            "fixture_id": fid,
                            "code": code,
                            "started": p.started,
                            "shirt": p.shirt,
                            "position_label": p.position,
                            "formation": side.formation,
                            "as_of": fetched.fetched_at,
                        }
                    )
                    if why != "bridge":
                        player_bridge[p.pl_id] = code
                        bridge_rows.append(
                            {
                                "season": season,
                                "pl_player_id": p.pl_id,
                                "code": code,
                                "opta_id": p.opta_id,
                                "matched_by": why,
                                "as_of": fetched.fetched_at,
                            }
                        )
            written = wh.append("fact_confirmed_lineup", pd.DataFrame(rows)) if rows else 0
            entry["status"] = "published"
            entry["rows_written"] = int(written)

        if bridge_rows:
            report["bridge_players_added"] = int(
                wh.append("bridge_pl_player", pd.DataFrame(bridge_rows))
            )
    finally:
        if own_fetcher:
            fetcher.close()
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entry point, shaped for the DAG's isolated-subprocess step runner."""
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", default=SEASON)
    parser.add_argument("--db", default=None, help="warehouse path (default: standard)")
    parser.add_argument("--window-hours", type=float, default=WINDOW_HOURS)
    parser.add_argument(
        "--now",
        default=None,
        help="ISO instant to centre the window on (verification against past "
        "fixtures); rows are still stamped with the real fetch instant.",
    )
    args = parser.parse_args(argv)

    now = None
    if args.now:
        now = dt.datetime.fromisoformat(args.now)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

    kwargs = {} if args.db is None else {"path": args.db}
    with Warehouse(**kwargs) as wh:
        report = ingest_lineups(
            wh, season=args.season, window_hours=args.window_hours, now=now
        )
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
