"""A synthetic league with known ground truth, and a warehouse to serve it from.

Why this exists: the goal model must be testable and measurable *now*, offline,
without waiting on the historical-data load. A synthetic league whose true
attack/defence parameters are known also buys something real data cannot -- it
lets the tests assert parameter recovery and lets the promoted-club prior be
checked against the value that actually generated the data.

What it does NOT buy: any claim about real football. The generator samples from
a Dixon-Coles process, so a Dixon-Coles model is fitting a correctly specified
model, which flatters it. And the synthetic bookmaker prices off the true rates
with a configurable noise, so the market baseline's margin here is a *design
parameter of the simulator*, not evidence about real bookmakers. Both caveats
are repeated in docs/models/team_goals.md next to the numbers.

Everything is seeded. Two runs with the same seed produce byte-identical CSVs.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.models.team_goals.scoreline import GoalRates, outcome_probs, prob_over, score_matrix
from fpl_edge.store import Warehouse

TRUE_INTERCEPT = np.log(1.15)
TRUE_HOME_ADV = 0.26
TRUE_RHO = -0.08

#: Promoted clubs are drawn weaker than average, with the promotion route
#: (1 champions, 2 runner-up, 3 play-off) shifting the mean. These are the
#: numbers the fitted promoted prior is expected to recover.
PROMOTED_ATTACK_MEAN = -0.26
PROMOTED_DEFENCE_MEAN = 0.24
PROMOTED_ROUTE_SLOPE = 0.07  # per route step, worse as route number rises
PROMOTED_SD = 0.16

#: Season-to-season persistence of a club's latent strength.
AR_RHO = 0.82
AR_SD = 0.13

#: Multiplicative noise on the bookmaker's view of the true goal rates, in log
#: space. Smaller = a sharper market. This single number governs how hard the
#: market baseline is to beat, and is therefore reported alongside every result.
BOOKMAKER_LOG_NOISE = 0.08
H2H_OVERROUND = 1.05
TOTALS_OVERROUND = 1.04
TOTALS_LINE = 2.5
BOOKMAKER_NAME = "synthetic_book"

#: Fraction of fixtures nobody prices. Real odds coverage is never complete --
#: feeds drop matches, books pull markets -- and a model that is only evaluated
#: where the market spoke is evaluated on the easy half of the problem.
ODDS_MISSING_FRACTION = 0.08

FIXTURE_COLUMNS = [
    "season",
    "fixture_id",
    "gw",
    "kickoff_utc",
    "home_team_code",
    "away_team_code",
    "home_score",
    "away_score",
]


@dataclass(frozen=True)
class SyntheticLeague:
    fixtures: pd.DataFrame
    truth: pd.DataFrame
    routes: pd.DataFrame
    odds: pd.DataFrame
    events: pd.DataFrame
    teams: pd.DataFrame

    def seasons(self) -> list[str]:
        return sorted(self.fixtures["season"].unique())


def _round_robin(team_ids: list[int], rng: np.random.Generator) -> list[list[tuple[int, int]]]:
    """Circle-method double round robin: 2*(n-1) rounds of n/2 matches."""
    teams = list(team_ids)
    rng.shuffle(teams)
    n = len(teams)
    if n % 2:
        raise ValueError("need an even number of teams")
    first: list[list[tuple[int, int]]] = []
    rotation = teams[:]
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = rotation[i], rotation[n - 1 - i]
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        first.append(pairs)
        rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]
    second = [[(b, a) for a, b in rnd] for rnd in first]
    return first + second


def generate_league(
    *,
    n_seasons: int = 6,
    n_teams: int = 20,
    first_season_start_year: int = 2020,
    seed: int = 20260818,
    odds_from_season: str | None = None,
) -> SyntheticLeague:
    """Simulate ``n_seasons`` of a top flight with promotion and relegation."""
    rng = np.random.default_rng(seed)
    next_code = 1

    def new_established() -> tuple[float, float]:
        return float(rng.normal(0.0, 0.30)), float(rng.normal(0.0, 0.28))

    strength: dict[int, tuple[float, float]] = {}
    current: list[int] = []
    for _ in range(n_teams):
        strength[next_code] = new_established()
        current.append(next_code)
        next_code += 1

    fixture_rows: list[dict[str, object]] = []
    truth_rows: list[dict[str, object]] = []
    route_rows: list[dict[str, object]] = []
    event_rows: list[dict[str, object]] = []
    team_rows: list[dict[str, object]] = []
    fixture_id = 1000

    for s in range(n_seasons):
        year = first_season_start_year + s
        season = f"{year}-{str(year + 1)[-2:]}"
        season_start = dt.datetime(year, 8, 14, 11, 30, tzinfo=dt.UTC)
        for i, code in enumerate(sorted(current), start=1):
            team_rows.append(
                {
                    "season": season,
                    "team_code": code,
                    "team_id": i,
                    "name": f"Club {code:02d}",
                    "short_name": f"C{code:02d}",
                }
            )
            atk, dfn = strength[code]
            truth_rows.append(
                {"season": season, "team_code": code, "attack": atk, "defence": dfn}
            )

        rounds = _round_robin(sorted(current), rng)
        points = dict.fromkeys(current, 0)
        for gw, pairs in enumerate(rounds, start=1):
            gw_start = season_start + dt.timedelta(days=7 * (gw - 1))
            event_rows.append(
                {
                    "season": season,
                    "gw": gw,
                    "deadline_utc": gw_start - dt.timedelta(minutes=90),
                    "is_finished": False,
                }
            )
            for k, (h, a) in enumerate(pairs):
                kickoff = gw_start + dt.timedelta(hours=2 * k)
                ha, hd = strength[h]
                aa, ad = strength[a]
                lam = float(np.exp(TRUE_INTERCEPT + TRUE_HOME_ADV + ha + ad))
                mu = float(np.exp(TRUE_INTERCEPT + aa + hd))
                mat = score_matrix(GoalRates(lam, mu, TRUE_RHO), max_goals=12)
                flat = mat.ravel()
                pick = rng.choice(flat.size, p=flat / flat.sum())
                hs, as_ = int(pick // mat.shape[0]), int(pick % mat.shape[0])
                points[h] += 3 if hs > as_ else (1 if hs == as_ else 0)
                points[a] += 3 if as_ > hs else (1 if hs == as_ else 0)
                fixture_rows.append(
                    {
                        "season": season,
                        "fixture_id": fixture_id,
                        "gw": gw,
                        "kickoff_utc": kickoff,
                        "home_team_code": h,
                        "away_team_code": a,
                        "home_score": hs,
                        "away_score": as_,
                        "true_lambda_home": lam,
                        "true_lambda_away": mu,
                    }
                )
                fixture_id += 1

        if s < n_seasons - 1:
            ordered = sorted(current, key=lambda c: (-points[c], c))
            relegated = set(ordered[-3:])
            survivors = [c for c in current if c not in relegated]
            for code in survivors:
                atk, dfn = strength[code]
                strength[code] = (
                    AR_RHO * atk + float(rng.normal(0, AR_SD)),
                    AR_RHO * dfn + float(rng.normal(0, AR_SD)),
                )
            next_year = year + 1
            next_season = f"{next_year}-{str(next_year + 1)[-2:]}"
            for route in (1, 2, 3):
                code = next_code
                next_code += 1
                shift = PROMOTED_ROUTE_SLOPE * (route - 2)
                strength[code] = (
                    float(rng.normal(PROMOTED_ATTACK_MEAN - shift, PROMOTED_SD)),
                    float(rng.normal(PROMOTED_DEFENCE_MEAN + shift, PROMOTED_SD)),
                )
                survivors.append(code)
                route_rows.append(
                    {"season": next_season, "team_code": code, "route": route}
                )
            current = survivors

    fixtures = pd.DataFrame(fixture_rows)
    truth = pd.DataFrame(truth_rows)
    routes = pd.DataFrame(route_rows, columns=["season", "team_code", "route"])
    events = pd.DataFrame(event_rows)
    teams = pd.DataFrame(team_rows)
    odds = _generate_odds(fixtures, rng, from_season=odds_from_season)
    return SyntheticLeague(fixtures, truth, routes, odds, events, teams)


def _generate_odds(
    fixtures: pd.DataFrame, rng: np.random.Generator, *, from_season: str | None
) -> pd.DataFrame:
    """Bookmaker prices for 1X2 and over/under, from noisy true rates plus vig."""
    sub = fixtures if from_season is None else fixtures[fixtures["season"] >= from_season]
    if ODDS_MISSING_FRACTION > 0 and len(sub):
        keep = rng.random(len(sub)) >= ODDS_MISSING_FRACTION
        sub = sub[keep]
    rows: list[dict[str, object]] = []
    for fx in sub.itertuples(index=False):
        lam = float(fx.true_lambda_home) * float(np.exp(rng.normal(0, BOOKMAKER_LOG_NOISE)))
        mu = float(fx.true_lambda_away) * float(np.exp(rng.normal(0, BOOKMAKER_LOG_NOISE)))
        mat = score_matrix(GoalRates(lam, mu, TRUE_RHO))
        ph, pdw, pa = outcome_probs(mat)
        over = prob_over(mat, TOTALS_LINE)
        key = f"{fx.season}:{fx.fixture_id}"
        # Books price a round several days out, comfortably before the
        # gameweek deadline. Anything later would be invisible to a snapshot
        # taken at the deadline, which is where the engine actually stands.
        as_of = pd.Timestamp(fx.kickoff_utc) - pd.Timedelta(days=3)
        for market, selection, p, vig in (
            ("h2h", "home", ph, H2H_OVERROUND),
            ("h2h", "draw", pdw, H2H_OVERROUND),
            ("h2h", "away", pa, H2H_OVERROUND),
            ("totals", f"over_{TOTALS_LINE}", over, TOTALS_OVERROUND),
            ("totals", f"under_{TOTALS_LINE}", 1.0 - over, TOTALS_OVERROUND),
        ):
            price = round(max(1.0 / max(p * vig, 1e-6), 1.01), 2)
            rows.append(
                {
                    "fixture_key": key,
                    "bookmaker": BOOKMAKER_NAME,
                    "market": market,
                    "selection": selection,
                    "price_decimal": price,
                    "as_of": as_of,
                }
            )
    return pd.DataFrame(rows)


# -- persistence -------------------------------------------------------------


def write_league(league: SyntheticLeague, directory: Path | str) -> None:
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    league.fixtures.round({"true_lambda_home": 6, "true_lambda_away": 6}).to_csv(
        d / "fixtures.csv", index=False
    )
    league.truth.round(6).to_csv(d / "truth.csv", index=False)
    league.routes.to_csv(d / "routes.csv", index=False)
    league.odds.to_csv(d / "odds.csv", index=False)
    league.events.to_csv(d / "events.csv", index=False)
    league.teams.to_csv(d / "teams.csv", index=False)


def load_league(directory: Path | str) -> SyntheticLeague:
    d = Path(directory)
    fixtures = pd.read_csv(d / "fixtures.csv", parse_dates=["kickoff_utc"])
    fixtures["kickoff_utc"] = pd.to_datetime(fixtures["kickoff_utc"], utc=True)
    events = pd.read_csv(d / "events.csv", parse_dates=["deadline_utc"])
    events["deadline_utc"] = pd.to_datetime(events["deadline_utc"], utc=True)
    odds = pd.read_csv(d / "odds.csv", parse_dates=["as_of"])
    odds["as_of"] = pd.to_datetime(odds["as_of"], utc=True)
    return SyntheticLeague(
        fixtures=fixtures,
        truth=pd.read_csv(d / "truth.csv"),
        routes=pd.read_csv(d / "routes.csv"),
        odds=odds,
        events=events,
        teams=pd.read_csv(d / "teams.csv"),
    )


def build_warehouse(league: SyntheticLeague, path: Path | str) -> Warehouse:
    """Materialise the league into a real DuckDB warehouse with honest as_of.

    Each fixture is written twice, exactly as the live ingestion would see it:
    a scheduled row with NULL scores visible from the start of the season, and a
    result row visible two hours after kickoff. That is what makes a snapshot
    taken at a gameweek deadline behave the way it will in production -- past
    results present, this week's fixtures present but unscored.
    """
    wh = Warehouse(path)
    fx = league.fixtures
    season_start = fx.groupby("season")["kickoff_utc"].min() - pd.Timedelta(days=30)

    scheduled = fx[
        ["season", "fixture_id", "gw", "kickoff_utc", "home_team_code", "away_team_code"]
    ].copy()
    scheduled["finished"] = False
    scheduled["home_score"] = pd.NA
    scheduled["away_score"] = pd.NA
    scheduled["as_of"] = scheduled["season"].map(season_start)

    played = fx[FIXTURE_COLUMNS].copy()
    played["finished"] = True
    played["as_of"] = played["kickoff_utc"] + pd.Timedelta(hours=2)

    wh.append(
        "fact_fixture",
        pd.concat([scheduled, played], ignore_index=True)[
            [
                "season",
                "fixture_id",
                "gw",
                "kickoff_utc",
                "home_team_code",
                "away_team_code",
                "finished",
                "home_score",
                "away_score",
                "as_of",
            ]
        ],
    )

    events = league.events.copy()
    events["as_of"] = events["season"].map(season_start)
    wh.append("dim_event", events[["season", "gw", "deadline_utc", "is_finished", "as_of"]])

    teams = league.teams.copy()
    teams["as_of"] = teams["season"].map(season_start)
    wh.append(
        "dim_team", teams[["season", "team_code", "team_id", "name", "short_name", "as_of"]]
    )

    if not league.odds.empty:
        wh.append(
            "fact_odds",
            league.odds[
                ["fixture_key", "bookmaker", "market", "selection", "price_decimal", "as_of"]
            ],
        )
    return wh
