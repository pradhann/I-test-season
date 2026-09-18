"""The cached fit: reading `fixture_ratings.parquet` back, and building it.

`build_board_ratings` and `build_calibration` are the fit; `load_ratings` and
`model_calibration` are the read side both panels use."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.platform.scripts.common import UTC, source_dir
from fpl_edge.platform.scripts.fixtures.constants import (
    ATTACKER_SHARE,
    DIFFICULTY_NAME,
    FPL_GOAL_POINTS,
    RATINGS_NAME,
    SCALE_DOMAIN,
    SEASON_DEFAULT,
)

# ---------------------------------------------------------------------------
# the ratings artefact: build (a job) and read (a panel)
# ---------------------------------------------------------------------------

RATINGS_COLUMNS = (
    "season", "team_code", "attack", "defence", "is_promoted", "matches_seen",
    "intercept", "home_adv", "rho", "mean_attack", "mean_defence",
    "half_life_days", "n_matches", "effective_n", "converged",
    "fitted_at", "snapshot_as_of",
)


@dataclass(frozen=True)
class Fitted:
    """One Dixon-Coles fit and the frames every artefact here derives from.

    Both the club split and the blended difficulty are views of the SAME fit.
    Before the merge they were two fits of one model half an hour apart, run by
    two jobs, writing two files. ``write_artefacts`` now builds this once and
    hands it to all three builders.
    """

    season: str
    snapshot: Any
    fit: Any
    fixtures: pd.DataFrame
    fitted_at: dt.datetime


def fit_once(wh, *, season: str = SEASON_DEFAULT,
             now: dt.datetime | None = None) -> Fitted:
    """Fit at ``now`` and return everything the three builders need.

    Point-in-time: the fit reads through ``wh.snapshot_at(now)``, so it sees
    the results that were public at that instant and nothing later.
    """
    from typing import cast

    from fpl_edge.models.team_goals.dixon_coles import DixonColesModel
    from fpl_edge.types import Season

    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    snapshot = wh.snapshot_at(now)
    return Fitted(
        season=season,
        snapshot=snapshot,
        fit=DixonColesModel().fit(snapshot, cast(Season, season)),
        fixtures=snapshot.upcoming_fixtures(season),
        fitted_at=dt.datetime.now(UTC),
    )


def build_board_ratings(wh, *, season: str = SEASON_DEFAULT,
                        now: dt.datetime | None = None,
                        fitted: Fitted | None = None) -> pd.DataFrame:
    """Fit once and return the split, one row per club. **Job code, not panel.**

    The split stops at ``attack`` and ``defence`` instead of subtracting them
    into one scalar, which is what the blended difficulty below does with the
    same numbers. Everything the fixtures page shows is a function of these
    plus the fit's three globals.

    ``fitted`` lets a caller that already has a fit reuse it. Passing nothing
    fits here, so a test or a one-off call still works on its own.
    """
    fitted = fitted or fit_once(wh, season=season, now=now)
    fit, snapshot = fitted.fit, fitted.snapshot
    fixtures = fitted.fixtures
    played = snapshot.table("fact_fixture")
    if fixtures.empty:
        return pd.DataFrame(columns=list(RATINGS_COLUMNS))
    codes = sorted(
        set(fixtures["home_team_code"].astype(int))
        | set(fixtures["away_team_code"].astype(int))
    )
    done = played[played["home_score"].notna() & played["away_score"].notna()]
    seen = pd.concat([done["home_team_code"], done["away_team_code"]]).value_counts()

    idx = [fit.index_of(c) for c in codes]
    atk = fit.attack[idx]
    dfn = fit.defence[idx]
    return pd.DataFrame(
        {
            "season": season,
            "team_code": codes,
            "attack": atk,
            "defence": dfn,
            "is_promoted": [c in fit.promoted for c in codes],
            "matches_seen": [int(seen.get(c, 0)) for c in codes],
            "intercept": fit.intercept,
            "home_adv": fit.home_adv,
            "rho": fit.rho,
            # Means over THIS season's clubs, matching opponent_difficulty
            # below so the two artefacts share one anchor: the
            # league-average anchor must be the league we are actually in, not
            # the four-season pool the fit was estimated over.
            "mean_attack": float(atk.mean()),
            "mean_defence": float(dfn.mean()),
            "half_life_days": fit.half_life_days,
            "n_matches": fit.n_matches,
            "effective_n": fit.effective_n,
            "converged": fit.converged,
            "fitted_at": fitted.fitted_at,
            "snapshot_as_of": snapshot.as_of,
        },
        columns=list(RATINGS_COLUMNS),
    )


class _Ratings:
    """The fitted split, in memory, with every derived quantity on tap."""

    def __init__(self, df: pd.DataFrame) -> None:
        head = df.iloc[0]
        self.season = str(head["season"])
        self.c = float(head["intercept"])
        self.g = float(head["home_adv"])
        self.rho = float(head["rho"])
        self.abar = float(head["mean_attack"])
        self.dbar = float(head["mean_defence"])
        self.half_life_days = float(head["half_life_days"])
        self.n_matches = int(head["n_matches"])
        self.effective_n = float(head["effective_n"])
        self.converged = bool(head["converged"])
        self.fitted_at = head["fitted_at"]
        self.snapshot_as_of = head["snapshot_as_of"]
        self.attack = {int(r.team_code): float(r.attack) for r in df.itertuples()}
        self.defence = {int(r.team_code): float(r.defence) for r in df.itertuples()}
        self.promoted = {int(r.team_code) for r in df.itertuples() if bool(r.is_promoted)}
        self.matches_seen = {int(r.team_code): int(r.matches_seen) for r in df.itertuples()}
        self.codes = sorted(self.attack)
        self._matrix_cache: dict[tuple[float, float], Any] = {}
        self._rank_cache: dict[str, Any] | None = None

    def has(self, *codes: int) -> bool:
        return all(int(c) in self.attack for c in codes)

    # -- rates ------------------------------------------------------------

    def in_goals(self, team: int) -> dict[str, float]:
        """The club's two parameters expressed as GOALS PER GAME.

        ``attack`` and ``defence`` are log multipliers, and every consumer that
        printed them raw was inviting a misreading: an attack of 0.434 is not
        "+0.43 goals", it multiplies the baseline by exp(0.434) = 1.54, which
        on a 1.42-goal home baseline is +0.77 goals. And ``defence`` counts
        goals CONCEDED, so its sign runs opposite to every ease number on the
        board. Goals per game carry no convention to remember and no sign to
        invert, so the panel serves them and the UI stops doing arithmetic.

        Both are venue-averaged: the club against a LEAGUE-AVERAGE opponent,
        half at home and half away, which is what "per game" has to mean for a
        number that is not attached to a fixture.
        """
        scores = sum(
            float(np.exp(self.c + self.g * h + self.attack[team] + self.dbar))
            for h in (1.0, 0.0)
        ) / 2.0
        concedes = sum(
            float(np.exp(self.c + self.g * (1.0 - h) + self.abar + self.defence[team]))
            for h in (1.0, 0.0)
        ) / 2.0
        base_scores = sum(
            float(np.exp(self.c + self.g * h + self.abar + self.dbar))
            for h in (1.0, 0.0)
        ) / 2.0
        return {
            "scores_pg": round(scores, 3),
            "concedes_pg": round(concedes, 3),
            "league_pg": round(base_scores, 3),
        }

    def opponent_only_rates(self, opponent: int, we_are_home: bool) -> tuple[float, float]:
        """``(our goals, their goals)`` for a LEAGUE-AVERAGE club vs ``opponent``.

        Our own attack and defence are replaced by the league means, which is
        precisely what makes two clubs' cells against the same opponent
        identical. ``we_are_home`` is OUR venue; the opponent's is the inverse.
        """
        h = 1.0 if we_are_home else 0.0
        mu = float(np.exp(self.c + self.g * h + self.abar + self.defence[opponent]))
        lam = float(np.exp(self.c + self.g * (1.0 - h) + self.attack[opponent] + self.dbar))
        return mu, lam

    def fixture_rates(self, team: int, opponent: int, we_are_home: bool) -> tuple[float, float]:
        """``(our goals, their goals)`` with OUR OWN fitted strength in it."""
        h = 1.0 if we_are_home else 0.0
        mu = float(np.exp(self.c + self.g * h + self.attack[team] + self.defence[opponent]))
        lam = float(np.exp(self.c + self.g * (1.0 - h) + self.attack[opponent] + self.defence[team]))
        return mu, lam

    def quantities(self, mu: float, lam: float) -> dict[str, float]:
        """Everything derived FROM the score matrix, never alongside it.

        ``scoreline.py`` makes this an invariant of the package: a clean-sheet
        probability computed from lambda directly can silently disagree with the
        matrix the simulator samples from, and the disagreement only surfaces as
        a mis-priced defender three layers downstream.
        """
        from fpl_edge.models.team_goals.scoreline import GoalRates, score_matrix

        key = (round(mu, 9), round(lam, 9))
        mat = self._matrix_cache.get(key)
        if mat is None:
            mat = score_matrix(GoalRates(mu, lam, self.rho))
            self._matrix_cache[key] = mat
        n = mat.shape[0]
        i = np.arange(n)
        conceded = mat.sum(axis=0)
        return {
            "xg": float(mat.sum(axis=1) @ i),
            "xg_against": float(conceded @ i),
            "p_clean_sheet": float(mat[:, 0].sum()),
            "p_opponent_clean_sheet": float(mat[0, :].sum()),
            "p_concede_2plus": float(conceded[2:].sum()),
            # FPL docks a defender/keeper 1 point per 2 goals conceded.
            "e_concede_penalty": float(sum(conceded[k] * -(k // 2) for k in range(n))),
        }

    # -- the league population and its anchors ----------------------------

    def population(self) -> dict[str, Any]:
        """The 2N (opponent, venue) pairs, ranked. Rank 1 is the EASIEST.

        Ranking over a fixed league population rather than over whatever is in
        the requested horizon is what makes a cell's colour mean the same thing
        in GW2 and GW32.
        """
        if self._rank_cache is not None:
            return self._rank_cache
        rows = []
        for opp in self.codes:
            for we_home in (True, False):
                mu, lam = self.opponent_only_rates(opp, we_home)
                qty = self.quantities(mu, lam)
                rows.append({"opponent": opp, "we_are_home": we_home,
                             "attack_xg": qty["xg"], "defence_xg": qty["xg_against"],
                             **qty})
        df = pd.DataFrame(rows)
        anchor_att = float(df["attack_xg"].mean())
        anchor_def = float(df["defence_xg"].mean())
        ref_cs = float(df["p_clean_sheet"].mean())
        ref_pen = float(df["e_concede_penalty"].mean())
        df["attack_ease"] = df["attack_xg"] - anchor_att
        df["defence_ease"] = anchor_def - df["defence_xg"]
        df["attack_pts"] = (df["attack_xg"] - anchor_att) * ATTACKER_SHARE * FPL_GOAL_POINTS
        df["defence_pts"] = (
            (df["p_clean_sheet"] - ref_cs) * FPL_GOAL_POINTS
            + (df["e_concede_penalty"] - ref_pen)
        )
        df["attack_rank"] = df["attack_ease"].rank(ascending=False, method="min").astype(int)
        df["defence_rank"] = df["defence_ease"].rank(ascending=False, method="min").astype(int)
        clipped = int(
            ((df["attack_ease"].abs() > SCALE_DOMAIN) | (df["defence_ease"].abs() > SCALE_DOMAIN)).sum()
        )
        self._rank_cache = {
            "by_pair": {(int(r.opponent), bool(r.we_are_home)): r._asdict()
                        for r in df.itertuples(index=False)},
            "anchor_attack_xg": anchor_att,
            "anchor_defence_xg": anchor_def,
            "ref_clean_sheet": ref_cs,
            "ref_concede_penalty": ref_pen,
            "population": len(df),
            "clipped_pairs": clipped,
        }
        return self._rank_cache

    def rating_rank(self) -> dict[int, tuple[int, int]]:
        """``team_code -> (attack rank, defence rank)``; 1 = best in the league."""
        atk = pd.Series(self.attack).rank(ascending=False, method="min")
        # `defence` is a leakiness parameter: higher concedes more, so the best
        # defence is the LOWEST value. Getting this backwards is the single
        # easiest sign error in this file.
        dfn = pd.Series(self.defence).rank(ascending=True, method="min")
        return {int(c): (int(atk[c]), int(dfn[c])) for c in self.attack}


# ---------------------------------------------------------------------------
# the blended difficulty artefact -- merged from models/team_goals/ratings_cache
# ---------------------------------------------------------------------------
#
# The blended number is the same fit's attack and defence subtracted into one
# scalar and min-maxed over the league. It is a strict LOSS of information
# against the split above, which is why the board never colours by it and the
# schema marks it deprecated. It keeps a writer because one reader still asks
# for it by name: fixture_board's per-cell `legacy_difficulty`. The MCP tool
# that was the second one is gone; the toolbelt reads the board, so it gets the
# split.

DIFFICULTY_COLUMNS = (
    "season", "gw", "fixture_id", "team_code", "opponent_code", "is_home",
    "difficulty", "fitted_at", "snapshot_as_of",
)


def opponent_difficulty(fit, season_teams: set[int]) -> dict[tuple[int, bool], float]:
    """``(opponent_code, opponent_is_home) -> difficulty`` for one fitted model.

    For a team facing opponent *O*, with *O* at that venue::

        lam_O = exp(c + g*[O at home] + attack_O + mean_defence)
        mu_O  = exp(c + g*[O away]    + mean_attack + defence_O)
        strength(O, venue) = lam_O - mu_O
        difficulty(O, venue) = (strength - min) / (max - min)

    The min and max run over the fixed population of all (club, venue) pairs,
    2N values, so the scale is a property of the league rather than of whichever
    horizon happened to be requested. Difficulty is in [0, 1] by construction,
    higher is harder, and the away trip to a side is always harder than hosting
    it because *O* gains the fitted home advantage. It is a function of opponent
    and venue only, like FPL's own FDR, so a leaky defence does not paint a
    club's whole ticker red.
    """
    codes = sorted(int(c) for c in season_teams)
    idx = [fit.index_of(c) for c in codes]
    atk = fit.attack[idx]
    dfn = fit.defence[idx]
    mean_atk = float(atk.mean())
    mean_dfn = float(dfn.mean())
    c, g = fit.intercept, fit.home_adv

    strength: dict[tuple[int, bool], float] = {}
    for code, a, d in zip(codes, atk, dfn, strict=True):
        for opp_home in (True, False):
            lam = np.exp(c + (g if opp_home else 0.0) + a + mean_dfn)
            mu = np.exp(c + (0.0 if opp_home else g) + mean_atk + d)
            strength[(code, opp_home)] = float(lam - mu)

    values = np.array(list(strength.values()))
    lo, hi = float(values.min()), float(values.max())
    if hi <= lo:  # a degenerate fit where every club is identical
        return {k: 0.5 for k in strength}
    return {k: (v - lo) / (hi - lo) for k, v in strength.items()}


def build_fixture_difficulty(wh, *, season: str = SEASON_DEFAULT,
                             now: dt.datetime | None = None,
                             fitted: Fitted | None = None) -> pd.DataFrame:
    """The blended difficulty rows for every upcoming fixture.

    Two rows per fixture, one per team, each carrying the difficulty its
    *opponent* poses at that venue. ``fitted`` reuses a fit the caller already
    has; passing nothing fits here.
    """
    fitted = fitted or fit_once(wh, season=season, now=now)
    fixtures, snapshot = fitted.fixtures, fitted.snapshot
    if fixtures.empty:
        return pd.DataFrame(columns=list(DIFFICULTY_COLUMNS))

    teams = (set(fixtures["home_team_code"].astype(int))
             | set(fixtures["away_team_code"].astype(int)))
    difficulty = opponent_difficulty(fitted.fit, teams)

    rows: list[dict] = []
    for fx in fixtures.itertuples(index=False):
        home, away = int(fx.home_team_code), int(fx.away_team_code)
        for team, opp, is_home in ((home, away, True), (away, home, False)):
            rows.append({
                "season": str(fx.season),
                "gw": int(fx.gw),
                "fixture_id": int(fx.fixture_id),
                "team_code": team,
                "opponent_code": opp,
                "is_home": is_home,
                # The opponent's venue is the inverse of ours.
                "difficulty": difficulty[(opp, not is_home)],
                "fitted_at": fitted.fitted_at,
                "snapshot_as_of": snapshot.as_of,
            })
    return pd.DataFrame(rows, columns=list(DIFFICULTY_COLUMNS))


def _read_parquet(path: Path) -> tuple[pd.DataFrame | None, str | None]:
    if not path.exists():
        return None, f"{path.name} is not next to the warehouse"
    try:
        return pd.read_parquet(path), None
    except Exception as exc:  # noqa: BLE001 - a corrupt cache is an absent cache
        return None, f"{path.name} could not be read ({type(exc).__name__}); treated as absent"


BUILD_HINT = (
    "run `python -m fpl_edge.platform.scripts.fixtures --build` (the "
    "post-gameweek job's step) to fit and write it"
)


def load_ratings(wh, season: str) -> tuple[_Ratings | None, str | None]:
    """The split ratings artefact, or ``(None, a renderable reason)``.

    Never fits. A panel that fitted would be a model run inside a 10s budget,
    and the fit belongs to a job that already knows when results land.
    """
    df, err = _read_parquet(source_dir(wh) / RATINGS_NAME)
    if df is None:
        return None, f"{err}. Attack/defence difficulty needs it: {BUILD_HINT}."
    need = {"season", "team_code", "attack", "defence", "intercept", "home_adv",
            "rho", "mean_attack", "mean_defence", "fitted_at"}
    if not need <= set(df.columns):
        missing = sorted(need - set(df.columns))
        return None, (
            f"{RATINGS_NAME} is missing {', '.join(missing)}; it predates the "
            f"split ratings. Rebuild it: {BUILD_HINT}."
        )
    df = df[df["season"].astype(str) == season]
    if df.empty:
        return None, (
            f"{RATINGS_NAME} holds no {season} clubs. It was fitted for a "
            f"different season. Rebuild it: {BUILD_HINT}."
        )
    return _Ratings(df), None


def load_legacy_difficulty(wh, season: str) -> dict[tuple[int, int], float]:
    """``(fixture_id, team_code) -> blended difficulty`` from the old artefact.

    Served under ``legacy_difficulty`` and marked deprecated in the schema so
    nothing downstream breaks while nothing new is built on it.
    """
    df, _ = _read_parquet(source_dir(wh) / DIFFICULTY_NAME)
    if df is None:
        return {}
    need = {"season", "fixture_id", "team_code", "difficulty"}
    if not need <= set(df.columns):
        return {}
    df = df[df["season"].astype(str) == season].dropna(subset=["difficulty"])
    df = df[(df["difficulty"] >= 0.0) & (df["difficulty"] <= 1.0)]
    return {(int(r.fixture_id), int(r.team_code)): float(r.difficulty)
            for r in df.itertuples(index=False)}


# ---------------------------------------------------------------------------
# calibration: how big is a fixture, really
# ---------------------------------------------------------------------------

CALIBRATION_COLUMNS = (
    "position", "n_starts", "fixture_pts_6gw", "team_pts_6gw", "ratio",
    "seasons", "method", "computed_at",
)


def build_calibration(wh, *, seasons: tuple[str, ...] = (
    "2022-23", "2023-24", "2024-25", "2025-26"),
    min_starts: int = 500, min_clubs: int = 18) -> pd.DataFrame:
    """The empirical answer to "how much is a fixture worth". **Job code.**

    Regress realised FPL points per start on (own club) + (opponent club) +
    venue, additively, per position, over completed seasons. Then walk every
    rolling six-gameweek window of every real schedule and take the spread
    across clubs of the six-fixture TOTAL. That last step is the one people skip
    and it is the whole point: schedules average out, so the fixture component
    shrinks over a horizon while the team component does not. Six times a
    per-fixture spread is not a six-gameweek spread, and quoting it as one
    overstates fixtures by roughly a factor of three.

    Only starters with 60+ minutes are used, because a fixture cannot help a
    player who does not play, and rotation is a minutes question, not a fixture
    one.

    Reads go through ``wh.sql`` rather than ``common.q``. That is deliberate and
    it is a trap worth naming: ``guarded_query`` truncates at a 10,000-row cap
    and records the truncation in a ``notes`` list that ``common.q`` throws away,
    so this regression -- which needs all 28,353 qualifying starts -- would have
    silently fitted on the first 10,000 and reported a confident wrong number.
    This is job code, not panel code, so the panel cap does not apply to it.

    ``min_starts`` and ``min_clubs`` are floors on what is worth fitting at all:
    a position with a handful of starts, or a gameweek window in which half the
    league is missing, produces effects that are noise wearing a decimal point.
    They are parameters rather than constants so a test can drive the arithmetic
    at a scale a human can check by hand.
    """
    lit = "(" + ", ".join(f"'{s}'" for s in seasons) + ")"
    starts = wh.sql(f"""
        WITH pf AS (SELECT * EXCLUDE(rn) FROM (SELECT *, row_number() OVER (
                      PARTITION BY season, code, fixture_id ORDER BY as_of DESC) rn
                    FROM fact_player_fixture WHERE season IN {lit}) WHERE rn = 1),
             fx AS (SELECT * EXCLUDE(rn) FROM (SELECT *, row_number() OVER (
                      PARTITION BY season, fixture_id ORDER BY as_of DESC) rn
                    FROM fact_fixture WHERE season IN {lit}) WHERE rn = 1),
             pl AS (SELECT * EXCLUDE(rn) FROM (SELECT *, row_number() OVER (
                      PARTITION BY season, code ORDER BY as_of DESC) rn
                    FROM dim_player WHERE season IN {lit}) WHERE rn = 1)
        SELECT pf.season, pl.position, pl.team_code AS team,
               CASE WHEN pf.was_home THEN fx.away_team_code ELSE fx.home_team_code END AS opp,
               pf.was_home, pf.total_points
        FROM pf JOIN fx ON fx.season = pf.season AND fx.fixture_id = pf.fixture_id
                JOIN pl ON pl.season = pf.season AND pl.code = pf.code
        WHERE pf.starts = 1 AND pf.minutes >= 60
    """)
    sched = wh.sql(f"""
        WITH fx AS (SELECT * EXCLUDE(rn) FROM (SELECT *, row_number() OVER (
                      PARTITION BY season, fixture_id ORDER BY as_of DESC) rn
                    FROM fact_fixture WHERE season IN {lit}) WHERE rn = 1)
        SELECT season, gw, home_team_code AS team, away_team_code AS opp FROM fx
        UNION ALL
        SELECT season, gw, away_team_code, home_team_code FROM fx
    """)
    if starts.empty or sched.empty:
        return pd.DataFrame(columns=list(CALIBRATION_COLUMNS))

    names = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
    out: list[dict[str, Any]] = []
    computed = dt.datetime.now(UTC)
    for pos_id, pos in names.items():
        d = starts[starts["position"] == pos_id].copy()
        if len(d) < min_starts:
            continue
        d["tkey"] = d["season"] + "|" + d["team"].astype(str)
        d["okey"] = d["season"] + "|" + d["opp"].astype(str)
        tc = pd.Categorical(d["tkey"])
        oc = pd.Categorical(d["okey"])
        tmat = pd.get_dummies(tc, drop_first=True).astype(float).values
        omat = pd.get_dummies(oc, drop_first=True).astype(float).values
        design = np.column_stack([
            np.ones(len(d)), d["was_home"].astype(float).values, tmat, omat,
        ])
        beta, *_ = np.linalg.lstsq(design, d["total_points"].astype(float).values, rcond=None)
        n_t = tmat.shape[1]
        team_eff = pd.Series(np.concatenate([[0.0], beta[2:2 + n_t]]), index=tc.categories)
        opp_eff = pd.Series(np.concatenate([[0.0], beta[2 + n_t:]]), index=oc.categories)

        fixture_spreads, team_spreads = [], []
        for season, sd in sched.groupby("season"):
            for g0 in range(1, 33):
                window = sd[(sd["gw"] >= g0) & (sd["gw"] <= g0 + 5)].copy()
                window["oe"] = (season + "|" + window["opp"].astype(str)).map(opp_eff)
                agg = window.groupby("team")["oe"].sum().dropna()
                if len(agg) < min_clubs:
                    continue
                team_side = pd.Series(
                    {t: team_eff.get(f"{season}|{t}", np.nan) for t in agg.index}
                ).dropna() * 6
                if team_side.empty:
                    continue
                fixture_spreads.append(float(agg.max() - agg.min()))
                team_spreads.append(float(team_side.max() - team_side.min()))
        if not fixture_spreads:
            continue
        fpts = float(np.mean(fixture_spreads))
        tpts = float(np.mean(team_spreads))
        out.append({
            "position": pos, "n_starts": len(d),
            "fixture_pts_6gw": fpts, "team_pts_6gw": tpts,
            "ratio": tpts / fpts if fpts else float("nan"),
            "seasons": ",".join(seasons),
            "method": "two-way additive least squares on starts with 60+ minutes; "
                      "spread = max-min across clubs of the six-gameweek total, "
                      "averaged over every rolling window of every season",
            "computed_at": computed,
        })
    return pd.DataFrame(out, columns=list(CALIBRATION_COLUMNS))


def model_calibration(ratings: _Ratings, fixtures: pd.DataFrame,
                      gws: list[int]) -> dict[str, Any] | None:
    """The model's own answer, over the horizon actually requested.

    Same arithmetic on both sides -- the only difference is whether our own club
    is held at league average or is itself. The ratio of the two spreads is the
    number that says "tie-breaker, not picker", and it is computed here rather
    than quoted from a document so it cannot go stale.
    """
    rows = []
    for fx in fixtures.itertuples(index=False):
        team, opp = int(fx.team_code), int(fx.opponent_code)
        if not ratings.has(team, opp):
            continue
        mu_a, lam_a = ratings.opponent_only_rates(opp, bool(fx.is_home))
        mu_o, lam_o = ratings.fixture_rates(team, opp, bool(fx.is_home))
        qa, qo = ratings.quantities(mu_a, lam_a), ratings.quantities(mu_o, lam_o)
        rows.append({"team": team, "a_xg": qa["xg"], "a_cs": qa["p_clean_sheet"],
                     "a_pen": qa["e_concede_penalty"], "o_xg": qo["xg"],
                     "o_cs": qo["p_clean_sheet"], "o_pen": qo["e_concede_penalty"]})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    n_gw = len(gws)

    def spread(att: pd.Series, cs: pd.Series, pen: pd.Series) -> tuple[float, float]:
        a = (att - att.mean()) * ATTACKER_SHARE * FPL_GOAL_POINTS
        d = (cs - cs.mean()) * FPL_GOAL_POINTS + (pen - pen.mean())
        per_club_a = a.groupby(df["team"]).mean()
        per_club_d = d.groupby(df["team"]).mean()
        return (float(per_club_a.max() - per_club_a.min()) * n_gw,
                float(per_club_d.max() - per_club_d.min()) * n_gw)

    fa, fd = spread(df["a_xg"], df["a_cs"], df["a_pen"])
    ta, td = spread(df["o_xg"], df["o_cs"], df["o_pen"])
    return {
        "horizon_gws": n_gw,
        "n_clubs": int(df["team"].nunique()),
        "fixture_swing_attack_pts": round(fa, 3),
        "fixture_swing_defence_pts": round(fd, 3),
        "team_quality_attack_pts": round(ta, 3),
        "team_quality_defence_pts": round(td, 3),
        "ratio_attack": round(ta / fa, 2) if fa else None,
        "ratio_defence": round(td / fd, 2) if fd else None,
        "method": (
            "spread across clubs of the horizon-total ease in FPL points, "
            f"attacker share {ATTACKER_SHARE}, goal/clean-sheet value "
            f"{FPL_GOAL_POINTS:.0f}. Fixture swing holds the club at league "
            "average; team quality uses its own fitted rating. Same arithmetic, "
            "one substitution."
        ),
    }
