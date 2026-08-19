"""A seeded warehouse and idea generators, for exercising the interfaces offline.

Shipped inside the package rather than under ``tests/`` because three different
things need it: the unit tests, the ``--dry-run`` mode of the Telegram command,
and anyone smoke-testing the MCP tools without a warehouse.

The player list is small but is chosen from real 2026-27 names for the specific
properties the parser has to survive:

* **Two Palmers** (Cole, MID; Alex, GKP) -- the ambiguity case. A surname that
  genuinely does not determine a player.
* **Two Fernandes** (Bruno, web_name "B.Fernandes"; Mateus, "Fernandes") -- the
  case an initial resolves and a bare surname does not.
* **Virgil van Dijk**, whose web_name is "Virgil" -- the case that fails entirely
  if you resolve against web_name alone.
* **Ødegaard** -- the accent case, typed as "odegaard" on a phone.
* **Man Utd players** -- so the club-affinity probe has something to find.

:func:`plant_ideas` deliberately supports both a biased and an unbiased
generator. A bias probe that fires on planted bias proves only half of what
matters; the other half is that it stays quiet on a null sample, and a test suite
that never checks the second half will happily ship a probe that always fires.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from fpl_edge.store import Warehouse

UTC = dt.timezone.utc

SEASON = "2026-27"

#: Man Utd. Stable across seasons, unlike team_id.
SUPPORTED_TEAM_CODE = 1

#: Season start. Everything static is known from here.
SEASON_START = dt.datetime(2026, 8, 1, 12, tzinfo=UTC)

#: (team_code, team_id, name, short_name). team_code 1 is Man Utd, which is what
#: the club-affinity probe keys on -- team_id moves between seasons, team_code
#: does not.
TEAMS: tuple[tuple[int, int, str, str], ...] = (
    (1, 16, "Man Utd", "MUN"),
    (3, 1, "Arsenal", "ARS"),
    (43, 15, "Man City", "MCI"),
    (14, 13, "Liverpool", "LIV"),
    (8, 7, "Chelsea", "CHE"),
    (91, 3, "Bournemouth", "BOU"),
)

#: (code, web_name, first, second, position, team_code, price_tenths, owned%)
PLAYERS: tuple[tuple[int, str, str, str, int, int, int, float], ...] = (
    # Man Utd -- the supported club.
    (176297, "Rashford", "Marcus", "Rashford", 3, 1, 70, 4.1),
    (141746, "B.Fernandes", "Bruno", "Borges Fernandes", 3, 1, 120, 49.1),
    (438098, "Mainoo", "Kobbie", "Mainoo", 3, 1, 50, 1.2),
    (223340, "Martinez", "Lisandro", "Martinez", 2, 1, 50, 2.0),
    (447246, "Onana", "Andre", "Onana", 1, 1, 50, 3.0),
    # Arsenal
    (184029, "Odegaard", "Martin", "Ødegaard", 3, 3, 65, 6.0),
    (226597, "Gabriel", "Gabriel", "dos Santos Magalhaes", 2, 3, 60, 21.0),
    (154561, "Raya", "David", "Raya Martin", 1, 3, 55, 12.0),
    # Saliba is here for one reason: "salah" is three edits away from "saliba"
    # and difflib scores that 0.73, which cleared the original threshold. Mohamed
    # Salah is not in the 2026-27 league, so a message saying "salah" must
    # produce a question, not a £6.0m Arsenal centre-back.
    (222683, "Saliba", "William", "Saliba", 2, 3, 60, 18.0),
    # Man City
    (223094, "Haaland", "Erling", "Haaland", 4, 43, 145, 62.0),
    (171314, "Gvardiol", "Josko", "Gvardiol", 2, 43, 60, 8.0),
    # Liverpool
    (97032, "Virgil", "Virgil", "van Dijk", 2, 14, 65, 9.0),
    (219847, "Szoboszlai", "Dominik", "Szoboszlai", 3, 14, 90, 15.0),
    # Chelsea -- the Palmer ambiguity lives here and at Bournemouth.
    (244851, "Palmer", "Cole", "Palmer", 3, 8, 95, 10.6),
    (108413, "Palmer", "Alex", "Palmer", 1, 8, 40, 5.2),
    (232223, "Fernandes", "Mateus", "Fernandes", 3, 8, 60, 2.3),
    # Bournemouth
    (438098 + 1, "Semenyo", "Antoine", "Semenyo", 3, 91, 85, 30.0),
    (110979, "Wood", "Chris", "Wood", 4, 91, 60, 7.0),
    (178301, "Watkins", "Ollie", "Watkins", 4, 91, 80, 9.0),
)

#: Filler so price-peer comparator sets are not degenerate. Real FPL has ~590
#: players and a peer band of 30; a fixture with 18 would make every median a
#: two-player affair and hide bugs that only appear at scale.
FILLER_PER_POSITION = 12


def _filler() -> list[tuple[int, str, str, str, int, int, int, float]]:
    out = []
    code = 900_000
    for position in (1, 2, 3, 4):
        for i in range(FILLER_PER_POSITION):
            team = TEAMS[i % len(TEAMS)][0]
            name = f"Filler{position}{i:02d}"
            price = 40 + position * 5 + (i % 7) * 5
            out.append((code, name, "Filler", name, position, team, price, 0.5))
            code += 1
    return out


ALL_PLAYERS = PLAYERS + tuple(_filler())


def seed_warehouse(
    path: Path | str,
    *,
    season: str = SEASON,
    n_gws: int = 10,
    finished_gws: int = 0,
    seed: int = 7,
) -> Warehouse:
    """Build a small, realistic, point-in-time-correct warehouse.

    ``finished_gws`` controls how much history exists. Zero is the state on
    2026-08-18 -- the season has not started and no form context exists -- and
    the interfaces have to behave sensibly there, not just once results arrive.

    Results carry ``as_of`` at 09:00 the day after the last match of the
    gameweek, matching ``deadlines.points_final_at`` in the rule registry, so a
    snapshot taken mid-gameweek correctly sees nothing.
    """
    wh = Warehouse(path)
    rng = random.Random(seed)

    wh.append(
        "dim_team",
        pd.DataFrame(
            [
                {"season": season, "team_code": tc, "team_id": tid, "name": name,
                 "short_name": short, "as_of": SEASON_START}
                for tc, tid, name, short in TEAMS
            ]
        ),
    )

    wh.append(
        "dim_player",
        pd.DataFrame(
            [
                {"season": season, "code": code, "element_id": i + 1, "web_name": web,
                 "first_name": first, "second_name": second, "position": pos,
                 "team_code": tc, "as_of": SEASON_START}
                for i, (code, web, first, second, pos, tc, _, _) in enumerate(ALL_PLAYERS)
            ]
        ),
    )
    wh.append(
        "fact_player_state",
        pd.DataFrame(
            [
                {"season": season, "code": code, "element_id": i + 1, "price_tenths": price,
                 "selected_by_pct": own, "status": "a",
                 "chance_of_playing_next_round": None, "news": "", "news_added": None,
                 "transfers_in_event": 0, "transfers_out_event": 0, "cost_change_start": 0,
                 "as_of": SEASON_START}
                for i, (code, _, _, _, _, _, price, own) in enumerate(ALL_PLAYERS)
            ]
        ),
    )

    # GW1 deadline matches the real 2026-27 fixture; the rest run weekly.
    gw1 = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
    events, fixtures = [], []
    for gw in range(1, n_gws + 1):
        deadline = gw1 + dt.timedelta(days=7 * (gw - 1))
        events.append(
            {"season": season, "gw": gw, "deadline_utc": deadline,
             "is_finished": gw <= finished_gws, "as_of": SEASON_START}
        )
        for f, (home, away) in enumerate(_pairings(gw)):
            kickoff = deadline + dt.timedelta(hours=2)
            is_done = gw <= finished_gws
            fixtures.append(
                {"season": season, "fixture_id": gw * 100 + f, "gw": gw,
                 "kickoff_utc": kickoff,
                 "home_team_code": home, "away_team_code": away,
                 # A finished fixture is only observable after it was played;
                 # the warehouse refuses a result stamped before its own
                 # kickoff, so the finished row's as_of sits after full time.
                 "finished": is_done, "home_score": None, "away_score": None,
                 "as_of": kickoff + dt.timedelta(hours=2) if is_done else SEASON_START}
            )
    wh.append("dim_event", pd.DataFrame(events))
    wh.append("fact_fixture", pd.DataFrame(fixtures))

    if finished_gws:
        wh.append("fact_player_fixture", _results(season, finished_gws, gw1, rng))
    return wh


def _pairings(gw: int) -> list[tuple[int, int]]:
    """Rotate the fixture list so home/away alternates per club per gameweek."""
    codes = [t[0] for t in TEAMS]
    rotated = codes[gw % len(codes):] + codes[: gw % len(codes)]
    return [
        (rotated[i], rotated[i + 1]) if gw % 2 == 0 else (rotated[i + 1], rotated[i])
        for i in range(0, len(rotated) - 1, 2)
    ]


def _results(season: str, finished_gws: int, gw1: dt.datetime, rng: random.Random) -> pd.DataFrame:
    """Plausible per-player returns, with price-correlated scoring rates.

    Points are drawn so that expensive players score more on average. Without
    that, price rank carries no signal, the prior verdict is pure noise by
    construction, and any calibration test built on this fixture would be
    measuring the fixture rather than the code.
    """
    rows = []
    for gw in range(1, finished_gws + 1):
        deadline = gw1 + dt.timedelta(days=7 * (gw - 1))
        finalised = deadline + dt.timedelta(days=3, hours=15, minutes=30)
        venue = {}
        for f, (home, away) in enumerate(_pairings(gw)):
            venue[home] = (gw * 100 + f, True)
            venue[away] = (gw * 100 + f, False)
        for code, _, _, _, position, team_code, price, _ in ALL_PLAYERS:
            fixture_id, was_home = venue[team_code]
            if rng.random() < 0.12:  # rotation / injury
                continue
            base = max(0.4, (price - 38) / 22.0)
            pts = max(0, int(rng.gauss(base + (0.4 if was_home else 0.0), 2.4)))
            # Real FPL returns are heavily right-skewed: most weeks are 2s and
            # the season is decided by the tail. A symmetric draw produces a
            # fixture in which nobody ever hauls, which would silently disable
            # the recency probe rather than test it.
            if rng.random() < 0.06 + base / 60.0:
                pts += rng.randint(6, 13)
            minutes = 90 if rng.random() < 0.8 else rng.choice([0, 25, 62])
            if minutes == 0:
                pts = 0
            rows.append(
                {
                    "season": season, "code": code, "fixture_id": fixture_id, "gw": gw,
                    "minutes": minutes, "goals_scored": 1 if pts >= 8 else 0,
                    "assists": 0, "clean_sheets": 0, "goals_conceded": 0, "own_goals": 0,
                    "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0,
                    "red_cards": 0, "saves": 0, "bonus": 3 if pts >= 10 else 0,
                    "bps": pts * 3, "starts": 1 if minutes >= 60 else 0, "tackles": 0,
                    "clearances_blocks_interceptions": 0, "recoveries": 0,
                    "defensive_contribution": 0, "expected_goals": 0.0,
                    "expected_assists": 0.0, "expected_goals_conceded": 0.0,
                    "total_points": pts, "was_home": was_home, "as_of": finalised,
                }
            )
    return pd.DataFrame(rows)


@dataclass(frozen=True, slots=True)
class PlantedIdeas:
    """What :func:`plant_ideas` actually submitted, for assertions."""

    submitted: int
    codes: tuple[int, ...]
    texts: tuple[str, ...]


def plant_ideas(
    inbox,
    *,
    n: int,
    when: dt.datetime,
    biased: bool,
    seed: int = 11,
    season: str = SEASON,
) -> PlantedIdeas:
    """Submit ``n`` real messages through the real inbox.

    ``biased=True`` picks subjects that just played at home, are in good form and
    play for the supported club -- the behaviour the probes are meant to catch.
    ``biased=False`` picks uniformly from the eligible pool, which is the null the
    probes must *not* fire on.

    Messages go through :meth:`IdeaInbox.submit` rather than being inserted
    directly, so the fixture exercises parsing, thesis generation, context capture
    and the verdict on every single idea.
    """
    from fpl_edge.interfaces.features import player_history, player_universe

    rng = random.Random(seed)
    snap = inbox.wh.snapshot_at(when)
    players = player_universe(snap, season)
    history = player_history(snap, season)

    merged = players if history.empty else players.merge(history, on="code", how="left")
    if not history.empty:
        eligible = merged[merged["eligible"].fillna(False).astype(bool)]
        if len(eligible) >= 10:
            merged = eligible
    rows = merged.reset_index(drop=True)

    if biased and not history.empty:
        # Explicit multiplicative weights, one factor per probe. Written this way
        # rather than as a filter chain so each planted bias is visible, tunable
        # and independent -- a filter chain plants whatever the intersection
        # happens to contain, which is how an earlier version accidentally
        # planted no recency bias at all while appearing to plant three.
        form = rows["form_points_last3"].fillna(0).astype(float)
        form_rank = form.rank(pct=True).fillna(0.5)
        weights = [
            1.0
            * (3.0 if bool(home) else 1.0)                    # home bias
            * (1.0 + 4.0 * float(fr))                         # form chasing
            * (5.0 if int(tc) == SUPPORTED_TEAM_CODE else 1.0)  # club affinity
            * (3.0 if bool(haul) else 1.0)                    # recency
            for home, fr, tc, haul in zip(
                rows["last_match_was_home"].fillna(False),
                form_rank,
                rows["team_code"],
                rows["hauled_recently"].fillna(False),
            )
        ]
    else:
        weights = [1.0] * len(rows)

    codes, texts = [], []
    for i in range(n):
        row = rows.iloc[rng.choices(range(len(rows)), weights=weights, k=1)[0]]
        name = str(row["second_name"] or row["web_name"]).split()[-1]
        text = rng.choice(
            ["I like {}", "{} captain?", "thinking about {}", "{} looks good"]
        ).format(name)
        sub = inbox.submit(
            text, source="test", source_ref=f"plant{i}",
            now=when + dt.timedelta(seconds=i),
        )
        if sub.ok and sub.idea is not None:
            codes.append(int(sub.idea.subject_code or 0))
            texts.append(text)
    return PlantedIdeas(submitted=len(codes), codes=tuple(codes), texts=tuple(texts))
