"""Generate the committed synthetic fixture warehouse for the minutes model.

Why this exists
---------------
The real warehouse holds no historical ``fact_player_fixture`` rows yet (another
team is loading them). Waiting would mean shipping unmeasured code, so the
minutes model is developed and measured against a synthetic league whose
data-generating process is written down here in full.

The DGP is deliberately *not* the functional form of either model we fit:
selection is a rank-and-threshold rule over a latent score with club-specific
noise, injuries are a Markov spell process, and the publicly observable
availability flags are a lossy, sometimes-wrong view of the latent injury state.
Neither a Dirichlet-multinomial nor an axis-aligned tree ensemble can represent
that exactly, so the comparison between them is informative rather than rigged.

Run with::

    uv run python tests/fixtures/minutes/generate.py

Output is deterministic given ``SEED`` and is committed as gzipped CSV so the
test suite never regenerates it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

UTC = dt.UTC
SEED = 20260818

OUT_DIR = Path(__file__).resolve().parent

#: Labelled seasons. The walk-forward evaluation tests on the last of these.
SEASONS = ("2023-24", "2024-25", "2025-26")
#: The live season: squads and preseason availability only, no results at all.
PRESEASON = "2026-27"
#: The real GW1 deadline for 2026-27, from the API (see docs/rules.md).
PRESEASON_GW1_DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
PRESEASON_AS_OF = dt.datetime(2026, 8, 18, 9, 0, tzinfo=UTC)

N_TEAMS = 12
N_GWS = 22  # double round robin over 12 clubs

#: Squad composition per club, keyed by FPL element_type.
SQUAD = {1: 3, 2: 7, 3: 7, 4: 4}
#: Starting XI slots per position.
SLOTS = {1: 1, 2: 4, 3: 4, 4: 2}
POS_NAME = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

#: Rounds played midweek (Wednesday) rather than at the weekend.
MIDWEEK_ROUNDS = frozenset({4, 11, 18})
#: PL rounds that follow a midweek European tie for clubs in Europe.
EURO_ROUNDS = frozenset({3, 6, 9, 12, 15, 18, 21})
N_EURO_CLUBS = 5


# --------------------------------------------------------------------------
# calendar
# --------------------------------------------------------------------------


def season_start_year(season: str) -> int:
    return int(season.split("-")[0])


def round_kickoffs(season: str) -> list[dt.datetime]:
    """Kickoff instant of every round. One kickoff per round, all fixtures on it."""
    d = dt.datetime(season_start_year(season), 8, 12, 14, 0, tzinfo=UTC)
    d += dt.timedelta(days=(5 - d.weekday()) % 7)  # first Saturday on or after 12 Aug
    out: list[dt.datetime] = [d]
    for gw in range(2, N_GWS + 1):
        prev = out[-1]
        if gw in MIDWEEK_ROUNDS:
            nxt = prev + dt.timedelta(days=4)
            nxt = nxt.replace(hour=19)
        elif (gw - 1) in MIDWEEK_ROUNDS:
            nxt = prev + dt.timedelta(days=3)
            nxt = nxt.replace(hour=14)
        else:
            nxt = prev + dt.timedelta(days=7)
            nxt = nxt.replace(hour=14)
        out.append(nxt)
    return out


def round_robin(n: int) -> list[list[tuple[int, int]]]:
    """Circle-method double round robin over ``n`` teams -> 2*(n-1) rounds."""
    teams = list(range(n))
    single: list[list[tuple[int, int]]] = []
    for _ in range(n - 1):
        pairs = [(teams[i], teams[n - 1 - i]) for i in range(n // 2)]
        single.append(pairs)
        teams = [teams[0], teams[-1], *teams[1:-1]]
    rounds = [list(r) for r in single]
    rounds += [[(a, h) for (h, a) in r] for r in single]  # reverse fixtures
    return rounds


# --------------------------------------------------------------------------
# league state
# --------------------------------------------------------------------------


class League:
    """Mutable player/club state carried across seasons."""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.next_code = 100_001
        self.players: dict[int, dict] = {}
        self.rosters: list[dict[int, list[int]]] = [
            {pos: [] for pos in SQUAD} for _ in range(N_TEAMS)
        ]
        for team in range(N_TEAMS):
            for pos, size in SQUAD.items():
                for _ in range(size):
                    self.rosters[team][pos].append(self._new_player(pos))
        self.rotation = rng.uniform(0.18, 0.9, size=N_TEAMS)

    def _new_player(self, pos: int) -> int:
        rng = self.rng
        code = self.next_code
        self.next_code += 1
        self.players[code] = {
            "code": code,
            "position": pos,
            # nailedness: how far ahead of squad-mates the manager rates them
            "theta": float(rng.normal(0.0, 1.0)),
            # per-round hazard of picking up an injury
            "injury_prone": float(rng.beta(2.0, 9.0) * 0.22),
            # propensity to be hooked before 60 when starting
            "hook": float(rng.normal(0.0, 0.8) + {1: -3.0, 2: -0.6, 3: 0.2, 4: 0.7}[pos]),
            # propensity to be thrown on from the bench
            "impact": float(rng.normal(0.0, 0.9) + {1: -4.0, 2: -0.3, 3: 0.3, 4: 0.5}[pos]),
            "age": int(rng.integers(18, 34)),
        }
        return code

    def offseason(self, table_rank: list[int]) -> None:
        """Transfers, retirements and manager churn between seasons."""
        rng = self.rng
        for p in self.players.values():
            p["theta"] += float(rng.normal(0.0, 0.28))
            p["age"] += 1
            if p["age"] > 31:
                p["theta"] -= 0.25
                p["injury_prone"] = min(0.3, p["injury_prone"] * 1.15)
        # who leaves the league entirely
        for team in range(N_TEAMS):
            for pos, codes in self.rosters[team].items():
                keep = [c for c in codes if rng.random() > 0.09]
                self.rosters[team][pos] = keep
        # club-to-club moves
        for pos in SQUAD:
            movers: list[int] = []
            for team in range(N_TEAMS):
                stay = []
                for c in self.rosters[team][pos]:
                    if rng.random() < 0.10:
                        movers.append(c)
                    else:
                        stay.append(c)
                self.rosters[team][pos] = stay
            rng.shuffle(movers)
            for c in movers:
                order = sorted(range(N_TEAMS), key=lambda t: len(self.rosters[t][pos]))
                dest = int(order[int(rng.integers(0, 4))])
                self.rosters[dest][pos].append(c)
        # refill to the required composition with fresh (unseen) players
        for team in range(N_TEAMS):
            for pos, size in SQUAD.items():
                while len(self.rosters[team][pos]) < size:
                    self.rosters[team][pos].append(self._new_player(pos))
                while len(self.rosters[team][pos]) > size:
                    # squad too big: the least-rated player is moved on
                    codes = self.rosters[team][pos]
                    worst = min(codes, key=lambda c: self.players[c]["theta"])
                    codes.remove(worst)
        # managers: 30% of clubs change approach, and promoted-style churn
        for team in range(N_TEAMS):
            if rng.random() < 0.3:
                self.rotation[team] = float(rng.uniform(0.18, 0.9))
        self.table_rank = table_rank

    def squad(self, team: int) -> list[int]:
        return [c for pos in SQUAD for c in self.rosters[team][pos]]


def team_code(team: int) -> int:
    return 500 + team


# --------------------------------------------------------------------------
# one season of matches
# --------------------------------------------------------------------------


def simulate_season(
    league: League, season: str, euro_clubs: set[int], rng: np.random.Generator
) -> dict[str, pd.DataFrame]:
    kickoffs = round_kickoffs(season)
    schedule = round_robin(N_TEAMS)[:N_GWS]
    season_start = kickoffs[0] - dt.timedelta(days=25)

    # ---- latent availability spells -------------------------------------
    # state per code: 0 fit, >0 rounds of injury remaining
    injured: dict[int, int] = {}
    suspended: dict[int, int] = {}
    spell_known: dict[int, bool] = {}
    spell_end: dict[int, int] = {}

    player_team = {c: t for t in range(N_TEAMS) for c in league.squad(t)}
    codes = sorted(player_team)

    started_last: dict[int, bool] = dict.fromkeys(codes, False)

    dim_player_rows = []
    for c in codes:
        p = league.players[c]
        dim_player_rows.append(
            {
                "season": season,
                "code": c,
                "element_id": codes.index(c) + 1,
                "web_name": f"P{c}",
                "first_name": "Syn",
                "second_name": f"Thetic{c}",
                "position": p["position"],
                "team_code": team_code(player_team[c]),
                "as_of": season_start,
            }
        )

    fixture_rows = []
    result_rows = []
    state_rows = []
    event_rows = []
    last_state: dict[int, tuple] = {}
    points = np.zeros(N_TEAMS)

    # price/ownership start from nailedness
    price = {c: int(np.clip(40 + 12 * league.players[c]["theta"], 38, 130)) for c in codes}
    owned = {c: float(np.clip(rng.gamma(1.2, 3.0), 0.1, 60.0)) for c in codes}

    fixture_id = 1
    for gw in range(1, N_GWS + 1):
        ko = kickoffs[gw - 1]
        deadline = ko - dt.timedelta(minutes=90)
        final_at = ko + dt.timedelta(days=1)
        final_at = final_at.replace(hour=9, minute=0)
        event_rows.append(
            {
                "season": season,
                "gw": gw,
                "deadline_utc": deadline,
                "is_finished": False,
                "as_of": season_start,
            }
        )
        event_rows.append(
            {
                "season": season,
                "gw": gw,
                "deadline_utc": deadline,
                "is_finished": True,
                "as_of": final_at,
            }
        )

        # ---- availability transitions, resolved before the deadline -----
        for c in codes:
            if injured.get(c, 0) > 0:
                injured[c] -= 1
                if injured[c] == 0:
                    spell_known.pop(c, None)
                    spell_end.pop(c, None)
            elif suspended.get(c, 0) > 0:
                suspended[c] -= 1
            else:
                p = league.players[c]
                if rng.random() < p["injury_prone"] * 0.55:
                    length = int(1 + rng.geometric(0.32))
                    injured[c] = length
                    # 85% of spells are public before the deadline
                    spell_known[c] = bool(rng.random() < 0.85)
                    spell_end[c] = gw + length - 1
                elif rng.random() < 0.012:
                    suspended[c] = 1

        # ---- public state at the deadline -------------------------------
        for c in codes:
            status, chance, news = "a", None, ""
            if suspended.get(c, 0) > 0:
                status, chance, news = "s", 0, "Suspended"
            elif injured.get(c, 0) > 0 and spell_known.get(c, False):
                if injured[c] >= 2:
                    status, chance = "i", 0
                    news = f"Injury - expected back GW{spell_end.get(c, gw) + 1}"
                else:
                    status = "d"
                    chance = int(rng.choice([25, 50, 75]))
                    news = f"Knock - {chance}% chance of playing"
            elif rng.random() < 0.05:
                # false alarm: flagged but fit
                status = "d"
                chance = int(rng.choice([50, 75, 75]))
                news = f"Knock - {chance}% chance of playing"
            key = (status, chance, news, price[c], round(owned[c], 1))
            if last_state.get(c) != key:
                last_state[c] = key
                state_rows.append(
                    {
                        "season": season,
                        "code": c,
                        "element_id": codes.index(c) + 1,
                        "price_tenths": price[c],
                        "selected_by_pct": round(owned[c], 1),
                        "status": status,
                        "chance_of_playing_next_round": chance,
                        "news": news,
                        "news_added": deadline - dt.timedelta(hours=6) if news else None,
                        "transfers_in_event": int(rng.poisson(2000 * owned[c] / 10)),
                        "transfers_out_event": int(rng.poisson(1500 * owned[c] / 10)),
                        "cost_change_start": 0,
                        "as_of": deadline - dt.timedelta(hours=1),
                    }
                )

        # ---- matches ----------------------------------------------------
        started_now: dict[int, bool] = dict.fromkeys(codes, False)
        for home, away in schedule[gw - 1]:
            fixture_rows.append(
                {
                    "season": season,
                    "fixture_id": fixture_id,
                    "gw": gw,
                    "kickoff_utc": ko,
                    "home_team_code": team_code(home),
                    "away_team_code": team_code(away),
                    "finished": False,
                    "home_score": None,
                    "away_score": None,
                    "as_of": season_start,
                }
            )
            hs = int(rng.poisson(1.5))
            as_ = int(rng.poisson(1.2))
            points[home] += 3 if hs > as_ else (1 if hs == as_ else 0)
            points[away] += 3 if as_ > hs else (1 if hs == as_ else 0)
            fixture_rows.append(
                {
                    "season": season,
                    "fixture_id": fixture_id,
                    "gw": gw,
                    "kickoff_utc": ko,
                    "home_team_code": team_code(home),
                    "away_team_code": team_code(away),
                    "finished": True,
                    "home_score": hs,
                    "away_score": as_,
                    "as_of": final_at,
                }
            )

            for team in (home, away):
                euro_tie = team in euro_clubs and gw in EURO_ROUNDS
                sigma = league.rotation[team] * (1.55 if euro_tie else 1.0)
                chosen: list[int] = []
                for pos, n_slots in SLOTS.items():
                    pool = [
                        c
                        for c in league.rosters[team][pos]
                        if injured.get(c, 0) == 0 and suspended.get(c, 0) == 0
                    ]
                    if not pool:
                        pool = list(league.rosters[team][pos])
                    scores = {}
                    for c in pool:
                        s = league.players[c]["theta"] + rng.normal(0.0, sigma)
                        if euro_tie and started_last.get(c, False):
                            s -= abs(rng.normal(0.75, 0.35))  # rested after Europe
                        scores[c] = s
                    picked = sorted(pool, key=lambda c: -scores[c])[:n_slots]
                    chosen.extend(picked)

                bench_pool = [
                    c
                    for c in league.squad(team)
                    if c not in chosen
                    and injured.get(c, 0) == 0
                    and suspended.get(c, 0) == 0
                ]
                bench_scores = {
                    c: league.players[c]["impact"] + league.players[c]["theta"] * 0.5
                    + rng.normal(0, 1.0)
                    for c in bench_pool
                }
                n_subs = int(rng.integers(3, 6))
                subs_on = sorted(bench_pool, key=lambda c: -bench_scores[c])[:n_subs]

                for c in league.squad(team):
                    if c in chosen:
                        started_now[c] = True
                        p = league.players[c]
                        hook_p = 1.0 / (1.0 + np.exp(-(p["hook"] - 1.0 - 0.8 * p["theta"])))
                        if euro_tie:
                            hook_p = min(0.95, hook_p * 1.5)
                        if rng.random() < hook_p:
                            mins = int(np.clip(rng.normal(63, 16), 8, 89))
                        else:
                            mins = 90
                        if rng.random() < 0.02:  # in-match injury
                            mins = int(rng.integers(5, 45))
                            injured[c] = int(1 + rng.geometric(0.35))
                            spell_known[c] = True
                            spell_end[c] = gw + injured[c]
                        starts = 1
                    elif c in subs_on:
                        mins = int(np.clip(rng.gamma(2.0, 9.0) + 1, 1, 45))
                        starts = 0
                    else:
                        mins = 0
                        starts = 0
                    result_rows.append(
                        {
                            "season": season,
                            "code": c,
                            "fixture_id": fixture_id,
                            "gw": gw,
                            "minutes": mins,
                            "starts": starts,
                            "total_points": (2 if mins >= 60 else (1 if mins > 0 else 0)),
                            "was_home": team == home,
                            "as_of": final_at,
                        }
                    )
            fixture_id += 1

        started_last = started_now
        # slow price/ownership drift toward playing time
        for c in codes:
            if started_now.get(c, False):
                owned[c] = min(70.0, owned[c] * 1.02 + 0.05)
                if rng.random() < 0.06:
                    price[c] += 1
            else:
                owned[c] = max(0.1, owned[c] * 0.985)
                if rng.random() < 0.05:
                    price[c] -= 1

    order = sorted(range(N_TEAMS), key=lambda t: -points[t])
    return {
        "dim_player": pd.DataFrame(dim_player_rows),
        "fact_fixture": pd.DataFrame(fixture_rows),
        "fact_player_fixture": pd.DataFrame(result_rows),
        "fact_player_state": pd.DataFrame(state_rows),
        "dim_event": pd.DataFrame(event_rows),
        "_table_rank": order,
    }


def preseason_tables(league: League, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Squads, preseason flags and the GW1-3 fixture list for the live season.

    No results, no in-season state: exactly what is knowable on 2026-08-18.
    """
    player_team = {c: t for t in range(N_TEAMS) for c in league.squad(t)}
    codes = sorted(player_team)
    dim_player = pd.DataFrame(
        [
            {
                "season": PRESEASON,
                "code": c,
                "element_id": i + 1,
                "web_name": f"P{c}",
                "first_name": "Syn",
                "second_name": f"Thetic{c}",
                "position": league.players[c]["position"],
                "team_code": team_code(player_team[c]),
                "as_of": PRESEASON_AS_OF,
            }
            for i, c in enumerate(codes)
        ]
    )
    states = []
    for i, c in enumerate(codes):
        status, chance, news = "a", None, ""
        r = rng.random()
        if r < 0.05:
            status, chance, news = "i", 0, "Hamstring injury - expected back GW4"
        elif r < 0.11:
            status = "d"
            chance = int(rng.choice([25, 50, 75]))
            news = f"Preseason knock - {chance}% chance of playing"
        states.append(
            {
                "season": PRESEASON,
                "code": c,
                "element_id": i + 1,
                "price_tenths": int(np.clip(45 + 12 * league.players[c]["theta"], 38, 140)),
                "selected_by_pct": float(round(max(0.1, rng.gamma(1.2, 3.0)), 1)),
                "status": status,
                "chance_of_playing_next_round": chance,
                "news": news,
                "news_added": PRESEASON_AS_OF - dt.timedelta(days=2) if news else None,
                "transfers_in_event": int(rng.poisson(40_000)),
                "transfers_out_event": int(rng.poisson(10_000)),
                "cost_change_start": 0,
                "as_of": PRESEASON_AS_OF,
            }
        )
    events, fixtures = [], []
    schedule = round_robin(N_TEAMS)
    ko = PRESEASON_GW1_DEADLINE + dt.timedelta(minutes=90)
    fid = 1
    for gw in (1, 2, 3):
        k = ko + dt.timedelta(days=7 * (gw - 1))
        events.append(
            {
                "season": PRESEASON,
                "gw": gw,
                "deadline_utc": k - dt.timedelta(minutes=90),
                "is_finished": False,
                "as_of": PRESEASON_AS_OF,
            }
        )
        for home, away in schedule[gw - 1]:
            fixtures.append(
                {
                    "season": PRESEASON,
                    "fixture_id": fid,
                    "gw": gw,
                    "kickoff_utc": k,
                    "home_team_code": team_code(home),
                    "away_team_code": team_code(away),
                    "finished": False,
                    "home_score": None,
                    "away_score": None,
                    "as_of": PRESEASON_AS_OF,
                }
            )
            fid += 1
    return {
        "dim_player": dim_player,
        "fact_player_state": pd.DataFrame(states),
        "dim_event": pd.DataFrame(events),
        "fact_fixture": pd.DataFrame(fixtures),
    }


def build() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(SEED)
    league = League(rng)
    euro = set(range(N_EURO_CLUBS))
    parts: dict[str, list[pd.DataFrame]] = {}
    teams = pd.DataFrame(
        [
            {
                "season": s,
                "team_code": team_code(t),
                "team_id": t + 1,
                "name": f"Team {t:02d}",
                "short_name": f"T{t:02d}",
                "as_of": dt.datetime(season_start_year(s), 7, 1, tzinfo=UTC),
            }
            for s in (*SEASONS, PRESEASON)
            for t in range(N_TEAMS)
        ]
    )
    for season in SEASONS:
        out = simulate_season(league, season, euro, rng)
        rank = out.pop("_table_rank")
        for name, df in out.items():
            parts.setdefault(name, []).append(df)
        league.offseason(rank)
        euro = set(rank[:N_EURO_CLUBS])
    for name, df in preseason_tables(league, rng).items():
        parts.setdefault(name, []).append(df)
    tables = {name: pd.concat(dfs, ignore_index=True) for name, dfs in parts.items()}
    tables["dim_team"] = teams
    return tables


def main() -> None:
    tables = build()
    for name, df in sorted(tables.items()):
        path = OUT_DIR / f"{name}.csv.gz"
        df.to_csv(path, index=False, compression={"method": "gzip", "mtime": 0})
        print(f"{name:24s} {len(df):7d} rows  {path.stat().st_size / 1024:8.1f} KiB")


if __name__ == "__main__":
    main()
