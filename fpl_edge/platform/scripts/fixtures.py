"""The fixtures data path: a horizon ticker, a per-fixture drilldown, and the
cached ratings artefact both of them read.

The governing idea
------------------
**Every fixture is two fixtures -- one for your attackers, one for your
defenders -- and nothing here ever averages them.** A single difficulty number
is the mean of the answers to two different questions, and a mean is not an
answer to either. Measured on this repo's own fitted model, attack-ease and
defence-ease over the 40 (club, venue) pairs share only about half their
variance, and individual fixtures invert completely: hosting a side that will
not score is a defender's fixture and an attacker's trap, and the blended number
calls it "average", which is the one thing it is not.

Two numbers, and they are not interchangeable
---------------------------------------------
Every cell carries **both** of these, under separate keys, never merged:

``opponent_only``
    Holds *your* club at league average and asks only what the OPPONENT does at
    that venue. Two different clubs facing the same opponent at the same venue
    get identical numbers -- on purpose. This is a *fixture* view, not a power
    ranking, and it is what the ticker colours by.

``fixture_specific``
    Your own club's fitted strength IS in this number. Arsenal at Hull and
    Coventry at Hull differ here. This is what a drilldown shows, and it is the
    honest prediction; it is not comparable across rows, which is exactly why it
    does not drive the colour.

The two are nested under distinct keys with distinct docstrings so that a
consumer cannot reach for one and get the other. Both are always served.

Polarity, once, everywhere
--------------------------
Every ``*_ease`` field is signed and **positive means better for you**:
``attack_ease`` positive means easier to score, ``defence_ease`` positive means
easier to keep a clean sheet. The defence axis is therefore a *flip* of the
opponent's goal rate, not the rate itself. One polarity for both axes means one
diverging colour scale with a real zero -- a league-average fixture -- rather
than two scales the reader has to hold in their head.

Why a cached artefact, and what is in it
----------------------------------------
A Dixon-Coles fit is a model run with its own refresh cycle, so it belongs in a
nightly job, not in a panel with a 10s budget. ``ratings_cache.py`` already
writes ``fixture_difficulty.parquet``, but that file stores only the *blended*
number -- ``lam_O - mu_O``, min-max normalised -- and the subtraction is
irreversible: 740 rows carry exactly 40 distinct values, and no arithmetic
recovers the two halves from the one scalar. (I checked whether the split can be
algebraically recovered from the 40 blended values: it cannot. Solving for
(attack, defence) per club given the home-advantage multiplier is exact and
linear, but the three global unknowns -- the min-max scale, its offset, and the
multiplier -- are constrained by only one identity, so the system is short by
two. The split has to be *stored*, not reconstructed.)

So this module owns a second artefact, ``fixture_ratings.parquet``: one row per
club carrying the fitted attack and defence parameters plus the fit's scalars
(intercept, home advantage, rho, league means). From those, every quantity on
this page -- both lenses, every score matrix, every clean-sheet probability --
is pure arithmetic in the panel, microseconds not minutes. Build it with::

    python -m fpl_edge.platform.scripts.fixtures --build

which is what the post-gameweek job should call, alongside ``ratings_cache``.
The panel NEVER builds it: when the artefact is missing the board serves the
schedule and every difficulty field is null with a reason naming this command.

Freshness is a first-class field, not a log line
------------------------------------------------
``provenance.generated_at`` is when the panel ran, which is always "seconds
ago", and a panel that reads a four-day-old odds table and stamps itself fresh
is lying by omission. Every result here therefore carries ``inputs[]``: one row
per source with its ``as_of``, its age in hours, its row count, a state, the
threshold that decides that state, and -- the part that matters -- what
staleness *does to the number*. Thresholds are per input because staleness means
different things per source: a fit only moves when matches finish, so a week is
fine; odds move on team news, so twelve hours is not.

The empty contract
------------------
No value is ever imputed. A missing input produces ``null`` **and** a sibling
string saying why, in the words a reader can act on. Whole-panel emptiness uses
the one sanctioned ``{empty, reason}`` shape, which the root schemas here keep
disjoint via ``additionalProperties: false``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fpl_edge.platform.registry import register_script
from fpl_edge.platform.scripts.common import (
    UTC,
    empty,
    latest_as_of,
    next_gw,
    q,
    season_param,
    source_dir,
)

# ---------------------------------------------------------------------------
# artefacts and thresholds
# ---------------------------------------------------------------------------

#: Written by fpl_edge.models.team_goals.ratings_cache. The legacy blended
#: number; kept for back-compatibility and always labelled deprecated.
DIFFICULTY_NAME = "fixture_difficulty.parquet"

#: Written by ``--build`` below: the fitted split, one row per club.
RATINGS_NAME = "fixture_ratings.parquet"

#: Written by ``--build`` below: the empirical calibration regression.
CALIBRATION_NAME = "fixture_calibration.parquet"

#: A fit only moves when matches finish, so within a normal week it cannot be
#: more than one round out of date. Beyond it, the refresh job has stopped.
RATINGS_STALE_HOURS = 7 * 24.0

#: Odds move on team news, so a price older than this predates the press
#: conferences that decide the fixture. Different decay law, different cutoff.
ODDS_STALE_HOURS = 12.0
ODDS_USELESS_HOURS = 72.0

#: Predicted XIs publish around T-48h; older than a day and the XI is a guess
#: about a team sheet that has since moved.
LINEUP_STALE_HOURS = 36.0
INTEL_STALE_HOURS = 7 * 24.0

#: The colour domain, in goals per match either side of a league-average
#: fixture. Two population standard deviations, so the best and worst four
#: clubs read as off-scale and everybody else uses the full ramp instead of
#: being crushed into the middle by two outliers.
SCALE_DOMAIN = 0.60

#: A reference attacker's share of their club's goal involvement, used only to
#: convert expected goals into the FPL points the calibration block prints.
#: 4 is the midfielder goal value and the clean-sheet value.
ATTACKER_SHARE = 0.30
FPL_GOAL_POINTS = 4.0

#: Rank gap at which the two lenses are called out as disagreeing. Ranks run
#: over the 2N (opponent, venue) population, so 10 places is a quarter of it.
DIVERGENCE_RANKS = 10

#: Form window. Below this many completed matches the residual is noise and
#: says so rather than drawing a confident sparkline over one point.
FORM_WINDOW = 6
FORM_MIN_MATCHES = 3

SEASON_DEFAULT = "2026-27"


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


def _hours_since(when: Any, now: dt.datetime) -> float | None:
    """Age in hours, rounded to two decimals because it is rendered.

    Clamped at zero: a negative age means the stamp is in the future relative to
    the requested instant, which is a leak rather than a fresh input, and it is
    reported as such in `notes` rather than as a negative number in a chip.
    """
    if when is None:
        return None
    ts = pd.to_datetime(when, utc=True, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        return None
    return round(max(0.0, (now - ts.to_pydatetime()).total_seconds() / 3600.0), 2)


def _iso(when: Any) -> str | None:
    if when is None:
        return None
    ts = pd.to_datetime(when, utc=True, errors="coerce")
    if ts is pd.NaT or pd.isna(ts):
        return None
    return ts.isoformat()


def _input_row(
    name: str,
    *,
    source: str,
    as_of: Any,
    now: dt.datetime,
    stale_after_hours: float | None,
    rows: int | None,
    effect_when_stale: str,
    detail: str,
    missing: bool = False,
) -> dict[str, Any]:
    """One freshness row. ``state`` is derived, never asserted by the caller.

    Four states, and the distinction between the last two is the one that
    matters: ``stale`` means the data is old, ``missing`` means it was never
    there. A red dot that cannot tell those apart is not worth drawing.
    """
    age = _hours_since(as_of, now)
    if missing or as_of is None or not rows:
        state = "missing"
    elif stale_after_hours is not None and age is not None and age > stale_after_hours:
        state = "stale"
    else:
        state = "fresh"
    return {
        "name": name,
        "source": source,
        "as_of": _iso(as_of),
        "age_hours": None if age is None else round(age, 2),
        "rows": None if rows is None else int(rows),
        "state": state,
        "stale_after_hours": stale_after_hours,
        "effect_when_stale": effect_when_stale,
        "detail": detail,
    }


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "source", "state", "effect_when_stale", "detail"],
    "properties": {
        "name": {"type": "string"},
        "source": {"type": "string"},
        "as_of": {"type": ["string", "null"]},
        "age_hours": {"type": ["number", "null"]},
        "rows": {"type": ["integer", "null"]},
        "state": {"enum": ["fresh", "stale", "missing", "failed"]},
        "stale_after_hours": {"type": ["number", "null"]},
        "effect_when_stale": {"type": "string"},
        "detail": {"type": "string"},
    },
}


# ---------------------------------------------------------------------------
# the ratings artefact: build (a job) and read (a panel)
# ---------------------------------------------------------------------------

RATINGS_COLUMNS = (
    "season", "team_code", "attack", "defence", "is_promoted", "matches_seen",
    "intercept", "home_adv", "rho", "mean_attack", "mean_defence",
    "half_life_days", "n_matches", "effective_n", "converged",
    "fitted_at", "snapshot_as_of",
)


def build_board_ratings(wh, *, season: str = SEASON_DEFAULT,
                        now: dt.datetime | None = None) -> pd.DataFrame:
    """Fit once and return the split, one row per club. **Job code, not panel.**

    This is the whole difference between this module and ``ratings_cache``: it
    stops at ``attack`` and ``defence`` instead of subtracting them into one
    scalar. Everything the fixtures page shows is a function of these numbers
    plus the fit's three globals.
    """
    from typing import cast

    from fpl_edge.models.team_goals.dixon_coles import DixonColesModel
    from fpl_edge.types import Season

    now = (now or dt.datetime.now(UTC)).astimezone(UTC)
    snapshot = wh.snapshot_at(now)
    fit = DixonColesModel().fit(snapshot, cast(Season, season))

    fixtures = snapshot.upcoming_fixtures(season)
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
    fitted_at = dt.datetime.now(UTC)
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
            # Means over THIS season's clubs, matching ratings_cache: the
            # league-average anchor must be the league we are actually in, not
            # the four-season pool the fit was estimated over.
            "mean_attack": float(atk.mean()),
            "mean_defence": float(dfn.mean()),
            "half_life_days": fit.half_life_days,
            "n_matches": fit.n_matches,
            "effective_n": fit.effective_n,
            "converged": fit.converged,
            "fitted_at": fitted_at,
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
            f"{RATINGS_NAME} holds no {season} clubs -- it was fitted for a "
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


# ---------------------------------------------------------------------------
# odds
# ---------------------------------------------------------------------------


def _resolved_odds(wh, season: str, as_of: dt.datetime) -> tuple[pd.DataFrame, str | None]:
    """``fact_odds`` re-keyed to ``season:fixture_id``, selections normalised.

    Two live bugs are worked around here rather than in the model package, which
    this module does not own:

    1. ``fact_odds`` stores natural keys (``2026-27:2026-08-29:hull:man-united``)
       while the goal model looks up ``2026-27:11``. ``odds_with_fixture_keys``
       is the repo's designated read-time resolver and is used as-is.
    2. ``team_goals.odds.devig_frame`` matches selections as ``("home", "draw",
       "away")`` and ``startswith("over")``, but every live row is upper case
       (``HOME``, ``OVER_2.5``). Unpatched, ``devig_frame`` returns **zero**
       fixtures against a warehouse holding 131,921 odds rows. Lower-casing the
       selection column here makes it match; the fix belongs upstream.
    """
    try:
        from fpl_edge.models.ensemble.sources import odds_with_fixture_keys
        odds, _ = odds_with_fixture_keys(wh, season, as_of)
    except Exception as exc:  # noqa: BLE001
        return pd.DataFrame(), (
            f"odds could not be re-keyed to fixture ids ({type(exc).__name__}); "
            f"the market leg is dropped rather than guessed"
        )
    if odds.empty:
        return odds, f"no {season} rows in fact_odds at or before this instant"
    odds = odds.copy()
    odds["selection"] = odds["selection"].astype(str).str.lower()
    parts = odds["fixture_key"].astype(str).str.split(":")
    numeric = parts.str.len() == 2
    odds = odds[numeric].copy()
    if odds.empty:
        return odds, (
            "no odds row could be matched to a fixture id; every key is still a "
            "natural key and the name-matching resolver found no fixture"
        )
    odds["fixture_id"] = odds["fixture_key"].astype(str).str.split(":").str[1].astype(int)
    return odds, None


def _market_state(age_hours: float | None) -> str:
    if age_hours is None:
        return "unpriced"
    if age_hours > ODDS_USELESS_HOURS:
        return "expired"
    if age_hours > ODDS_STALE_HOURS:
        return "stale"
    return "priced"


MARKET_CELL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["state", "reason"],
    "properties": {
        "state": {"enum": ["priced", "stale", "expired", "unpriced"]},
        "as_of": {"type": ["string", "null"]},
        "age_hours": {"type": ["number", "null"]},
        "n_books": {"type": ["integer", "null"]},
        "reason": {"type": ["string", "null"]},
    },
}


# ---------------------------------------------------------------------------
# fixture_ticker is GONE. The legacy blended-difficulty panel was deleted
# (2026-09): its own description said "superseded by fixture_board", and
# fixture_board serves the same blend per cell as the deprecated
# `legacy_difficulty` field, so nothing the ticker published is lost -- only
# the second, blended data path is. `load_legacy_difficulty` above survives
# because fixture_board reads it for exactly that field.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# fixture_board -- the horizon ticker, both lenses
# ---------------------------------------------------------------------------

_LENS_SCHEMA_PROPS: dict[str, Any] = {
    "attack_ease": {"type": ["number", "null"],
                    "description": "Signed goals vs a league-average fixture. POSITIVE = easier to score."},
    "defence_ease": {"type": ["number", "null"],
                     "description": "Signed goals vs a league-average fixture. POSITIVE = easier to keep a clean sheet."},
    "attack_xg": {"type": ["number", "null"],
                  "description": "Expected goals FOR, from the score matrix."},
    "defence_xg": {"type": ["number", "null"],
                   "description": "Expected goals AGAINST, from the same matrix."},
    "attack_pts": {"type": ["number", "null"],
                   "description": "attack_ease converted to FPL points for a reference attacker."},
    "defence_pts": {"type": ["number", "null"],
                    "description": "defence_ease converted to FPL points for a defender/keeper."},
    "p_clean_sheet": {"type": ["number", "null"]},
    "p_opponent_clean_sheet": {"type": ["number", "null"]},
    "p_concede_2plus": {"type": ["number", "null"]},
    "attack_rank": {"type": ["integer", "null"],
                    "description": "Rank over the 2N (opponent, venue) league population. 1 = EASIEST."},
    "defence_rank": {"type": ["integer", "null"]},
    "rank_gap": {"type": ["integer", "null"],
                 "description": "attack_rank - defence_rank. Large magnitude = the blend would have lied."},
    "unavailable": {"type": ["string", "null"],
                    "description": "Why every number above is null. Renderable prose, never a code."},
}

_LENS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["unavailable"],
    "properties": _LENS_SCHEMA_PROPS,
}

BOARD_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "season": season_param(),
        "horizon": {"type": "integer", "minimum": 1, "maximum": 12, "default": 6},
        "from_gw": {"type": ["integer", "null"], "default": None,
                    "description": "Start gameweek; defaults to the next unplayed one."},
        "as_of": {"type": ["string", "null"], "default": None,
                  "description": "ISO instant. Every warehouse read is point-in-time to it."},
        "include_form": {"type": "boolean", "default": True},
        "include_calibration": {"type": "boolean", "default": True},
        "divergence_ranks": {"type": "integer", "minimum": 1, "maximum": 40,
                             "default": DIVERGENCE_RANKS},
    },
}

BOARD_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "gws", "teams", "row_count", "inputs", "scale", "as_of"],
    "properties": {
        "season": {"type": "string"},
        "as_of": {"type": "string", "description": "The point-in-time instant every read used."},
        "gws": {"type": "array", "items": {"type": "integer"}},
        "from_gw": {"type": "integer"},
        "horizon": {"type": "integer"},
        "row_count": {"type": "integer"},
        "fixture_as_of": {"type": ["string", "null"]},
        "notes": {"type": "array", "items": {"type": "string"}},
        "inputs": {"type": "array", "items": INPUT_SCHEMA},
        "scale": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "unit", "domain", "available"],
            "properties": {
                "kind": {"const": "diverging"},
                "unit": {"type": "string"},
                "polarity": {"type": "string"},
                "domain": {"type": "array", "items": {"type": "number"}},
                "domain_applies_to": {"const": "opponent_only"},
                "domain_note": {"type": "string"},
                "available": {"type": "boolean"},
                "anchor_attack_xg": {"type": ["number", "null"]},
                "anchor_defence_xg": {"type": ["number", "null"]},
                "population": {"type": ["integer", "null"]},
                "clipped_pairs": {"type": ["integer", "null"]},
                "rank_convention": {"type": "string"},
                "unavailable": {"type": ["string", "null"]},
            },
        },
        "calibration": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["headline", "model", "empirical"],
            "properties": {
                "headline": {"type": "string"},
                "model": {"type": ["object", "null"], "additionalProperties": True},
                "empirical": {"type": ["object", "null"], "additionalProperties": True},
                "unavailable": {"type": ["string", "null"]},
            },
        },
        "divergent": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["gw", "fixture_id", "team_code", "sentence"],
                "properties": {
                    "gw": {"type": "integer"},
                    "fixture_id": {"type": "integer"},
                    "team_code": {"type": "integer"},
                    "short_name": {"type": "string"},
                    "opponent_code": {"type": "integer"},
                    "opponent": {"type": "string"},
                    "is_home": {"type": "boolean"},
                    "attack_rank": {"type": "integer"},
                    "defence_rank": {"type": "integer"},
                    "gap": {"type": "integer"},
                    "sentence": {"type": "string"},
                },
            },
        },
        "teams": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["team_code", "short_name", "fixtures", "n_fixtures"],
                "properties": {
                    "team_code": {"type": "integer"},
                    "short_name": {"type": "string"},
                    "name": {"type": ["string", "null"]},
                    "n_fixtures": {"type": "integer"},
                    "n_blanks": {"type": "integer"},
                    "n_doubles": {"type": "integer"},
                    "rating": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "attack": {"type": "number"},
                            "defence": {"type": "number",
                                        "description": "Leakiness: HIGHER concedes more."},
                            "attack_rank": {"type": "integer"},
                            "defence_rank": {"type": "integer"},
                            "is_promoted": {"type": "boolean"},
                            "matches_seen": {"type": "integer"},
                        },
                    },
                    "horizon": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "properties": {
                            "attack_ease_sum": {"type": "number"},
                            "defence_ease_sum": {"type": "number"},
                            "attack_ease_per_game": {"type": "number"},
                            "defence_ease_per_game": {"type": "number"},
                            "attack_pts_sum": {"type": "number"},
                            "defence_pts_sum": {"type": "number"},
                            "attack_rank": {"type": "integer"},
                            "defence_rank": {"type": "integer"},
                            "rank_gap": {"type": "integer"},
                            "n_rated": {"type": "integer"},
                        },
                    },
                    "form": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["window_matches", "unavailable"],
                        "properties": {
                            "window_matches": {"type": "integer"},
                            "xg_for_pg": {"type": ["number", "null"]},
                            "xg_against_pg": {"type": ["number", "null"]},
                            "xg_for_resid": {"type": ["number", "null"],
                                             "description": "Actual minus what the fitted rating expected. A diagnostic, never an input."},
                            "xg_against_resid": {"type": ["number", "null"]},
                            "unavailable": {"type": ["string", "null"]},
                        },
                    },
                    "unavailable": {"type": ["string", "null"]},
                    "fixtures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["gw", "blank", "double", "opponents"],
                            "properties": {
                                "gw": {"type": "integer"},
                                "blank": {"type": "boolean"},
                                "double": {"type": "boolean"},
                                "opponents": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "additionalProperties": False,
                                        "required": ["fixture_id", "opponent", "opponent_code",
                                                     "is_home", "label", "opponent_only",
                                                     "fixture_specific", "market"],
                                        "properties": {
                                            "fixture_id": {"type": "integer"},
                                            "opponent": {"type": "string"},
                                            "opponent_code": {"type": "integer"},
                                            "is_home": {"type": "boolean"},
                                            "kickoff_utc": {"type": ["string", "null"]},
                                            "label": {"type": "string"},
                                            "opponent_only": _LENS_SCHEMA,
                                            "fixture_specific": _LENS_SCHEMA,
                                            "market": MARKET_CELL_SCHEMA,
                                            "legacy_difficulty": {"type": ["number", "null"]},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}


def _blank_lens(reason: str) -> dict[str, Any]:
    """Every field null and one sentence saying why. Never a zero, never a 0.5."""
    return {k: None for k in _LENS_SCHEMA_PROPS} | {"unavailable": reason}


def _lens(qty: dict[str, float], *, anchor_att: float, anchor_def: float,
          ref_cs: float, ref_pen: float,
          ranks: tuple[int, int] | None = None) -> dict[str, Any]:
    attack_ease = qty["xg"] - anchor_att
    defence_ease = anchor_def - qty["xg_against"]
    out = {
        "attack_ease": round(attack_ease, 4),
        "defence_ease": round(defence_ease, 4),
        "attack_xg": round(qty["xg"], 4),
        "defence_xg": round(qty["xg_against"], 4),
        "attack_pts": round(attack_ease * ATTACKER_SHARE * FPL_GOAL_POINTS, 4),
        "defence_pts": round(
            (qty["p_clean_sheet"] - ref_cs) * FPL_GOAL_POINTS
            + (qty["e_concede_penalty"] - ref_pen), 4),
        "p_clean_sheet": round(qty["p_clean_sheet"], 4),
        "p_opponent_clean_sheet": round(qty["p_opponent_clean_sheet"], 4),
        "p_concede_2plus": round(qty["p_concede_2plus"], 4),
        "attack_rank": None,
        "defence_rank": None,
        "rank_gap": None,
        "unavailable": None,
    }
    if ranks is not None:
        out["attack_rank"], out["defence_rank"] = ranks
        out["rank_gap"] = ranks[0] - ranks[1]
    return out


def _team_form(wh, season: str, now: dt.datetime, ratings: _Ratings | None
               ) -> tuple[dict[int, dict[str, Any]], int, Any]:
    """Rolling team xG for and against, and the residual against the rating.

    The aggregation trap, which is worth a comment because summing looks right:
    ``expected_goals_conceded`` is written PER PLAYER and every outfielder on the
    pitch carries the team's value, so a naive SUM gives 30-plus xGC for a single
    fixture. One representative value per team-match is the correct read; xG FOR
    is genuinely per player and is summed.

    ``was_home`` is 100% NULL for the 2026-27 rows in ``fact_player_fixture``, so
    the side is resolved through ``dim_player.team_code`` rather than that
    column. That mis-attributes a mid-season transfer's earlier matches to the
    new club; over a six-match window it is a small and stated risk, and it is
    the only join available while the column is empty.
    """
    rows = q(wh, """
        WITH fx AS (SELECT * FROM sem_fixtures(?) WHERE season = ? AND finished),
             pl AS (SELECT season, code, team_code FROM (
                      SELECT *, row_number() OVER (
                        PARTITION BY season, code ORDER BY as_of DESC) rn
                      FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1),
             pf AS (SELECT * FROM sem_player_form(?) WHERE season = ?)
        SELECT fx.team_code, fx.fixture_id, fx.gw, fx.kickoff_utc,
               fx.opponent_code, fx.is_home,
               SUM(pf.expected_goals) AS xg_for,
               MAX(pf.expected_goals_conceded) AS xg_against
        FROM fx JOIN pl ON pl.season = fx.season AND pl.team_code = fx.team_code
                JOIN pf ON pf.season = fx.season AND pf.fixture_id = fx.fixture_id
                       AND pf.code = pl.code
        GROUP BY 1, 2, 3, 4, 5, 6
    """, (now, season, season, now, now, season))
    if rows.empty:
        return {}, 0, None
    newest = pd.to_datetime(rows["kickoff_utc"], utc=True).max()
    rows = rows.sort_values("kickoff_utc", ascending=False)
    out: dict[int, dict[str, Any]] = {}
    for team, grp in rows.groupby("team_code"):
        window = grp.head(FORM_WINDOW)
        n = len(window)
        entry: dict[str, Any] = {
            "window_matches": int(n),
            "xg_for_pg": round(float(window["xg_for"].mean()), 3),
            "xg_against_pg": round(float(window["xg_against"].mean()), 3),
            "xg_for_resid": None,
            "xg_against_resid": None,
            "unavailable": None,
        }
        if n < FORM_MIN_MATCHES:
            entry["unavailable"] = (
                f"{n} completed match{'' if n == 1 else 'es'} this season -- below "
                f"the {FORM_MIN_MATCHES} needed for the residual to mean anything. "
                f"The per-game figures are shown; the residual is not."
            )
        elif ratings is not None:
            exp_for, exp_against = [], []
            for r in window.itertuples(index=False):
                if not ratings.has(int(r.team_code), int(r.opponent_code)):
                    continue
                mu, lam = ratings.fixture_rates(
                    int(r.team_code), int(r.opponent_code), bool(r.is_home))
                got = ratings.quantities(mu, lam)
                exp_for.append(got["xg"])
                exp_against.append(got["xg_against"])
            if exp_for:
                entry["xg_for_resid"] = round(
                    float(window["xg_for"].mean()) - float(np.mean(exp_for)), 3)
                entry["xg_against_resid"] = round(
                    float(window["xg_against"].mean()) - float(np.mean(exp_against)), 3)
        elif ratings is None:
            entry["unavailable"] = (
                "the fitted ratings artefact is absent, so there is nothing to "
                "take a residual against; the raw per-game figures stand alone."
            )
        out[int(team)] = entry
    return out, int(len(rows) // 2), newest


def fixture_board(
    wh, *, season: str, horizon: int = 6, from_gw: int | None = None,
    as_of: str | None = None, include_form: bool = True,
    include_calibration: bool = True, divergence_ranks: int = DIVERGENCE_RANKS,
) -> dict[str, Any]:
    """The horizon ticker: clubs down, gameweeks across, TWO difficulties a cell.

    Every cell carries ``opponent_only`` (your club held at league average --
    what the colour is for) and ``fixture_specific`` (your club's own strength
    folded in -- what a drilldown shows). They are different numbers answering
    different questions and they are never merged, never defaulted into each
    other, and never served under one name.

    Also returned: ``inputs[]`` with every source's age and what its staleness
    costs, ``scale`` so the legend is payload-led, ``calibration`` so the page
    can print how big a fixture actually is, and ``divergent[]`` -- the fixtures
    where the two lenses disagree, which is the finding a blended number erases.
    """
    now = dt.datetime.now(UTC)
    if as_of:
        parsed = pd.to_datetime(as_of, utc=True, errors="coerce")
        if parsed is pd.NaT or pd.isna(parsed):
            return empty(f"as_of={as_of!r} is not an ISO instant; nothing was read.")
        now = parsed.to_pydatetime()

    teams = q(wh, """
        SELECT team_code, short_name, name FROM (
          SELECT *, row_number() OVER (
            PARTITION BY season, team_code ORDER BY as_of DESC) rn
          FROM dim_team WHERE season = ? AND as_of <= ?) WHERE rn = 1
        ORDER BY short_name
    """, (season, now))
    if teams.empty:
        return empty(
            f"No {season} clubs known at {now.isoformat()}. dim_team comes from "
            f"the FPL bootstrap; run `make ingest`."
        )

    notes: list[str] = []
    start = from_gw if from_gw is not None else next_gw(wh, season, now)
    if start is None:
        played = q(wh, "SELECT max(gw) AS g FROM fact_fixture WHERE season = ? AND as_of <= ?",
                   (season, now))
        latest = None if played.empty else played.iloc[0]["g"]
        if latest is None:
            return empty(f"No {season} fixtures or deadlines known yet. Run `make ingest`.")
        start = int(latest)
        notes.append(
            f"Every {season} deadline has passed, so the board starts at the last "
            f"known gameweek (GW{start}) rather than a future one."
        )
    gws = list(range(int(start), int(start) + int(horizon)))

    fx = q(wh, """
        SELECT fixture_id, gw, kickoff_utc, team_code, opponent_code, is_home,
               team, opponent
        FROM sem_fixtures(?)
        WHERE season = ? AND gw >= ? AND gw <= ?
        ORDER BY gw, kickoff_utc, team_code
    """, (now, season, gws[0], gws[-1]))
    if fx.empty:
        return empty(
            f"No {season} fixtures scheduled for GW{gws[0]}-GW{gws[-1]} as known at "
            f"{now.isoformat()}. The fixture list is ingested from the FPL API; "
            f"run `make ingest`."
        )

    ratings, ratings_reason = load_ratings(wh, season)
    legacy = load_legacy_difficulty(wh, season)
    population: dict[str, Any] = {}
    if ratings is not None:
        population = ratings.population()
        if not ratings.converged:
            notes.append(
                "The stored Dixon-Coles fit did not converge; every difficulty "
                "below inherits that. Re-run the ratings build."
            )
        # A cached artefact is stamped when it was BUILT, not when it was asked
        # for, so a backdated as_of reads a fit that saw results the caller's
        # instant could not. Every warehouse read here is point-in-time; the
        # artefact cannot be, and pretending otherwise is exactly the leak the
        # snapshot discipline exists to stop. Say it loudly instead.
        built = pd.to_datetime(ratings.snapshot_as_of, utc=True, errors="coerce")
        if pd.notna(built) and built.to_pydatetime() > now:
            notes.append(
                f"LEAKAGE WARNING: as_of is {now.isoformat()} but the cached fit "
                f"was trained on the warehouse as at {built.isoformat()}, so the "
                f"difficulties below saw results that did not exist at the "
                f"requested instant. Every warehouse read is point-in-time; a "
                f"cached artefact cannot be. Rebuild with --build at the target "
                f"instant before using this for a backtest."
            )

    odds, odds_reason = _resolved_odds(wh, season, now)
    per_fixture_odds: dict[int, dict[str, Any]] = {}
    if not odds.empty:
        agg = (odds[odds["market"].isin(["h2h", "totals"])]
               .groupby("fixture_id")
               .agg(newest=("as_of", "max"), n_books=("bookmaker", "nunique")))
        for fid, quote in agg.iterrows():
            age = _hours_since(quote["newest"], now)
            per_fixture_odds[int(fid)] = {
                "state": _market_state(age),
                "as_of": _iso(quote["newest"]),
                "age_hours": age,
                "n_books": int(quote["n_books"]),
                "reason": None,
            }

    form: dict[int, dict[str, Any]] = {}
    form_matches, form_newest = 0, None
    if include_form:
        form, form_matches, form_newest = _team_form(wh, season, now, ratings)

    rating_ranks = ratings.rating_rank() if ratings is not None else {}
    anchors = {
        "anchor_att": population.get("anchor_attack_xg", 0.0),
        "anchor_def": population.get("anchor_defence_xg", 0.0),
        "ref_cs": population.get("ref_clean_sheet", 0.0),
        "ref_pen": population.get("ref_concede_penalty", 0.0),
    }

    per: dict[tuple[int, int], list[dict[str, Any]]] = {}
    divergent: list[dict[str, Any]] = []
    short = dict(zip(teams["team_code"], teams["short_name"]))
    for r in fx.itertuples(index=False):
        team, opp = int(r.team_code), int(r.opponent_code)
        is_home = bool(r.is_home)
        fid, gw = int(r.fixture_id), int(r.gw)
        label = str(r.opponent) if r.opponent is not None else short.get(opp, str(opp))

        if ratings is None:
            oo = _blank_lens(ratings_reason or "no fitted ratings artefact")
            fs = _blank_lens(ratings_reason or "no fitted ratings artefact")
        elif not ratings.has(team, opp):
            unrated = (f"{label} is not in the stored fit -- the fixture was added "
                       f"or rescheduled after the last ratings build")
            oo, fs = _blank_lens(unrated), _blank_lens(unrated)
        else:
            pair = population["by_pair"][(opp, is_home)]
            mu_a, lam_a = ratings.opponent_only_rates(opp, is_home)
            mu_f, lam_f = ratings.fixture_rates(team, opp, is_home)
            oo = _lens(ratings.quantities(mu_a, lam_a), **anchors,
                       ranks=(int(pair["attack_rank"]), int(pair["defence_rank"])))
            fs = _lens(ratings.quantities(mu_f, lam_f), **anchors)
            rank_gap = int(oo["rank_gap"] or 0)
            if abs(rank_gap) >= divergence_ranks:
                venue = "you host" if is_home else "you visit"
                easier, harder = (("defensive", "attacking") if rank_gap > 0
                                  else ("attacking", "defensive"))
                divergent.append({
                    "gw": gw, "fixture_id": fid, "team_code": team,
                    "short_name": str(short.get(team, team)),
                    "opponent_code": opp, "opponent": label, "is_home": is_home,
                    "attack_rank": int(oo["attack_rank"]),
                    "defence_rank": int(oo["defence_rank"]),
                    "gap": rank_gap,
                    "sentence": (
                        f"GW{gw} - {venue} {label}: {oo['attack_rank']} of "
                        f"{population['population']} as an attacking fixture, "
                        f"{oo['defence_rank']} as a defensive one. It is a good "
                        f"{easier} fixture and a poor {harder} one; a blended "
                        f"number would call it average."
                    ),
                })

        per.setdefault((team, gw), []).append({
            "fixture_id": fid,
            "opponent": label,
            "opponent_code": opp,
            "is_home": is_home,
            "kickoff_utc": None if r.kickoff_utc is None else _iso(r.kickoff_utc),
            "label": label.upper() if is_home else label.lower(),
            "opponent_only": oo,
            "fixture_specific": fs,
            "market": per_fixture_odds.get(fid, {
                "state": "unpriced", "as_of": None, "age_hours": None, "n_books": None,
                "reason": odds_reason or "no bookmaker has quoted this fixture yet",
            }),
            "legacy_difficulty": legacy.get((fid, team)),
        })

    out: list[dict[str, Any]] = []
    for t in teams.itertuples(index=False):
        code = int(t.team_code)
        slots, total, blanks, doubles = [], 0, 0, 0
        att_sum = def_sum = att_pts = def_pts = 0.0
        n_rated = 0
        for gw in gws:
            opps = per.get((code, gw), [])
            total += len(opps)
            blanks += 1 if not opps else 0
            doubles += 1 if len(opps) > 1 else 0
            for o in opps:
                lens = o["opponent_only"]
                if lens["attack_ease"] is not None:
                    att_sum += lens["attack_ease"]
                    def_sum += lens["defence_ease"]
                    att_pts += lens["attack_pts"]
                    def_pts += lens["defence_pts"]
                    n_rated += 1
            slots.append({"gw": gw, "blank": not opps,
                          "double": len(opps) > 1, "opponents": opps})
        if total == 0:
            # A club with nothing in the whole window is not in this league;
            # listing it as N blank gameweeks would read as a real run.
            continue
        row: dict[str, Any] = {
            "team_code": code,
            "short_name": str(t.short_name),
            "name": None if t.name is None else str(t.name),
            "n_fixtures": total, "n_blanks": blanks, "n_doubles": doubles,
            "rating": None, "horizon": None, "unavailable": None,
            "form": form.get(code, {
                "window_matches": 0, "xg_for_pg": None, "xg_against_pg": None,
                "xg_for_resid": None, "xg_against_resid": None,
                "unavailable": "no completed match for this club at this instant",
            }),
            "fixtures": slots,
        }
        if ratings is not None and ratings.has(code):
            ranks = rating_ranks[code]
            row["rating"] = {
                "attack": round(ratings.attack[code], 4),
                "defence": round(ratings.defence[code], 4),
                "attack_rank": ranks[0], "defence_rank": ranks[1],
                "is_promoted": code in ratings.promoted,
                "matches_seen": ratings.matches_seen.get(code, 0),
            }
        if n_rated:
            row["horizon"] = {
                "attack_ease_sum": round(att_sum, 4),
                "defence_ease_sum": round(def_sum, 4),
                "attack_ease_per_game": round(att_sum / n_rated, 4),
                "defence_ease_per_game": round(def_sum / n_rated, 4),
                "attack_pts_sum": round(att_pts, 4),
                "defence_pts_sum": round(def_pts, 4),
                "attack_rank": 0, "defence_rank": 0, "rank_gap": 0,
                "n_rated": n_rated,
            }
        else:
            row["unavailable"] = ratings_reason or (
                "no fitted rating covers any of this club's fixtures in the window"
            )
        out.append(row)

    if not out:
        return empty(f"No club has a fixture in GW{gws[0]}-GW{gws[-1]} for {season}.")

    # Horizon ranks: computed after every club is built, over the clubs that
    # actually have numbers, so a club with no rating is absent from the ranking
    # rather than silently ranked last.
    rated = [r for r in out if r["horizon"] is not None]
    if rated:
        att_order = pd.Series({r["team_code"]: r["horizon"]["attack_ease_sum"] for r in rated})
        def_order = pd.Series({r["team_code"]: r["horizon"]["defence_ease_sum"] for r in rated})
        att_rank = att_order.rank(ascending=False, method="min").astype(int)
        def_rank = def_order.rank(ascending=False, method="min").astype(int)
        for r in rated:
            code = r["team_code"]
            r["horizon"]["attack_rank"] = int(att_rank[code])
            r["horizon"]["defence_rank"] = int(def_rank[code])
            r["horizon"]["rank_gap"] = int(att_rank[code]) - int(def_rank[code])

    divergent.sort(key=lambda d: (-abs(d["gap"]), d["gw"]))

    calibration = None
    if include_calibration:
        calibration = _calibration_block(wh, ratings, fx, gws)

    scale: dict[str, Any] = {
        "kind": "diverging",
        "unit": "goals per match versus a league-average fixture",
        "polarity": "positive is better for you on BOTH axes; defence_ease is a flip of the opponent's goal rate",
        "domain": [-SCALE_DOMAIN, SCALE_DOMAIN],
        "domain_applies_to": "opponent_only",
        "available": ratings is not None,
        "anchor_attack_xg": None, "anchor_defence_xg": None,
        "population": None, "clipped_pairs": None,
        "rank_convention": "1 = easiest, over the 2N (opponent, venue) pairs of this league",
        # Measured on the live 2026-27 fit: the opponent-only population has SD
        # 0.29 (attack) / 0.31 (defence), so +/-0.60 is two SD and saturates 5 of
        # the 40 pairs -- the best and worst four clubs read off-scale on purpose
        # and everybody else uses the full ramp. `fixture_specific` is a WIDER
        # distribution (SD ~0.40, ~13% of cells outside this domain) because our
        # own club's strength is in it, so a renderer that colours the
        # fixture-specific number on THIS domain will clip much more than the
        # legend implies. Colour opponent_only; print fixture_specific.
        "domain_note": (
            "This domain is calibrated on the opponent-only population, which is "
            "what the ticker colours. fixture_specific is a wider distribution "
            "and will clip harder on the same ramp."
        ),
        "unavailable": ratings_reason,
    }
    if population:
        scale |= {
            "anchor_attack_xg": round(population["anchor_attack_xg"], 4),
            "anchor_defence_xg": round(population["anchor_defence_xg"], 4),
            "population": population["population"],
            "clipped_pairs": population["clipped_pairs"],
        }

    horizon_fixture_ids = {int(f) for f in fx["fixture_id"].unique()}
    ratings_detail = ratings_reason or "" if ratings is None else (
        f"Dixon-Coles, {ratings.n_matches} matches, effective n "
        f"{ratings.effective_n:.0f}, {ratings.half_life_days:.0f}-day half-life, "
        f"{'converged' if ratings.converged else 'DID NOT CONVERGE'}"
    )
    n_horizon_fixtures = len(horizon_fixture_ids)
    n_priced = len(horizon_fixture_ids & set(per_fixture_odds))

    inputs = [
        _input_row(
            "fitted ratings", source=RATINGS_NAME,
            as_of=None if ratings is None else ratings.fitted_at, now=now,
            stale_after_hours=RATINGS_STALE_HOURS,
            rows=None if ratings is None else len(ratings.codes),
            missing=ratings is None,
            effect_when_stale=(
                "the split difficulties are still shown -- a week-old fitted rating "
                "beats a made-up fresh one -- but the fit predates recent results"
            ),
            detail=ratings_detail,
        ),
        _input_row(
            "schedule", source="fact_fixture via sem_fixtures(as_of)",
            as_of=latest_as_of(wh, "fact_fixture", season), now=now,
            stale_after_hours=48.0, rows=len(fx),
            effect_when_stale="a rescheduled fixture may still be shown at its old date",
            detail=f"{len(fx)} team-fixture rows over GW{gws[0]}-GW{gws[-1]}",
        ),
        _input_row(
            "market odds", source="fact_odds (h2h + totals), re-keyed to fixture ids",
            as_of=None if odds.empty else odds["as_of"].max(), now=now,
            stale_after_hours=ODDS_STALE_HOURS, rows=len(odds),
            missing=odds.empty,
            effect_when_stale=(
                f"the market is not in any difficulty on this board -- it never is -- "
                f"but a price older than {ODDS_USELESS_HOURS:.0f}h predates the team "
                f"news that decides the fixture and the drilldown marks it expired"
            ),
            detail=odds_reason or (
                f"{n_priced} of {n_horizon_fixtures} fixtures in the horizon carry "
                f"a quote; {n_horizon_fixtures - n_priced} are not priced by any "
                f"book yet, which is normal more than two or three gameweeks out"
            ),
        ),
        _input_row(
            "team form (xG)", source="fact_player_fixture via sem_player_form(as_of)",
            as_of=form_newest, now=now, stale_after_hours=None,
            rows=form_matches, missing=not form,
            effect_when_stale=(
                "nothing: the residual is a diagnostic that the colour might be "
                "wrong, and it never enters a difficulty. Its age is the age of "
                "the last completed match, not of a fetch, so there is no "
                "staleness threshold to cross."
            ),
            detail=(
                f"{form_matches} completed team-matches this season, newest "
                f"{_iso(form_newest)}"
                if form else "no completed match for this season yet"
            ),
        ),
    ]

    notes.append(
        "Colour holds your own club at league average and asks only what the "
        "opponent does at that venue, so two clubs facing the same opponent get "
        "the same cell -- on purpose. The fixture-specific number, with your own "
        "club's strength in it, is on the same cell under `fixture_specific`."
    )
    if odds_reason is None and per_fixture_odds:
        notes.append(
            "The market is reported per cell but is NOT blended into any "
            "difficulty. `blend.py`'s weight has never been tuned out of sample, "
            "so a blend here would be an untuned constant wearing a number's "
            "clothes. The drilldown shows model and market side by side instead."
        )

    return {
        "season": season,
        "as_of": now.isoformat(),
        "gws": gws,
        "from_gw": int(start),
        "horizon": int(horizon),
        "row_count": len(out),
        "fixture_as_of": latest_as_of(wh, "fact_fixture", season),
        "teams": out,
        "inputs": inputs,
        "scale": scale,
        "calibration": calibration,
        "divergent": divergent,
        "notes": notes,
    }


def _calibration_block(wh, ratings: _Ratings | None, fx: pd.DataFrame,
                       gws: list[int]) -> dict[str, Any]:
    """How big is a fixture, in points, next to how big a club is.

    The page prints this to stop itself being over-trusted: an easy run is a
    tie-breaker between similar assets, not a reason to pick one. Two
    independent answers are served and neither is allowed to stand in for the
    other -- ``model`` is the fitted model's own arithmetic over the requested
    horizon, ``empirical`` is a regression on four seasons of realised FPL
    points. If they disagree the page should say so; averaging them would hide
    exactly the thing worth knowing.
    """
    model = model_calibration(ratings, fx, gws) if ratings is not None else None
    df, err = _read_parquet(source_dir(wh) / CALIBRATION_NAME)
    empirical = None
    emp_reason = err
    if df is not None and not df.empty and {"position", "fixture_pts_6gw"} <= set(df.columns):
        outfield = df[df["position"].isin(["DEF", "MID", "FWD"])]
        empirical = {
            "by_position": [
                {"position": str(r.position), "n_starts": int(r.n_starts),
                 "fixture_pts_6gw": round(float(r.fixture_pts_6gw), 2),
                 "team_pts_6gw": round(float(r.team_pts_6gw), 2),
                 "ratio": round(float(r.ratio), 2)}
                for r in df.itertuples(index=False)
            ],
            "outfield_fixture_pts_6gw": round(float(outfield["fixture_pts_6gw"].mean()), 2),
            "outfield_team_pts_6gw": round(float(outfield["team_pts_6gw"].mean()), 2),
            "outfield_ratio": round(
                float(outfield["team_pts_6gw"].mean() / outfield["fixture_pts_6gw"].mean()), 2),
            "seasons": str(df.iloc[0]["seasons"]),
            "method": str(df.iloc[0]["method"]),
            "computed_at": _iso(df.iloc[0]["computed_at"]),
            "caveat": (
                "max-minus-min over twenty estimated effects is biased upward by "
                "sampling noise, so this is an upper bound; the model figure, "
                "which has no estimation noise in it, is the lower bound. Read "
                "them as a bracket."
            ),
        }
    elif df is not None:
        emp_reason = f"{CALIBRATION_NAME} is present but empty or misshapen"

    if model is None and empirical is None:
        return {
            "headline": "How much a fixture is worth has not been measured here yet.",
            "model": None, "empirical": None,
            "unavailable": (
                f"neither figure is available: {BUILD_HINT}. Without it the page "
                f"must not claim a size for the fixture effect."
            ),
        }
    if model is not None:
        head = (
            f"Over {model['horizon_gws']} gameweeks, the fixture run is worth about "
            f"{model['fixture_swing_attack_pts']:.1f} points to an attacker and "
            f"{model['fixture_swing_defence_pts']:.1f} to a defender, best club to "
            f"worst. Which club you own is worth about "
            f"{model['ratio_attack']:.0f}x that. Use this page to break ties "
            f"between similar assets, not to pick them."
        )
    else:
        assert empirical is not None  # the both-None case returned above
        head = (
            f"Over six gameweeks the fixture run is worth about "
            f"{empirical['outfield_fixture_pts_6gw']:.1f} realised points to an "
            f"outfielder and the club itself about "
            f"{empirical['outfield_ratio']:.1f}x that. Tie-breaker, not picker."
        )
    return {"headline": head, "model": model, "empirical": empirical,
            "unavailable": None if empirical is not None else emp_reason}


register_script(
    "fixture_board",
    fixture_board,
    params_schema=BOARD_PARAMS,
    result_schema=BOARD_RESULT,
    title="Fixture board",
    description=(
        "The horizon ticker with BOTH difficulties per cell: opponent-only "
        "(what the colour is for) and fixture-specific (what the drilldown "
        "shows). Every input reports its own age."
    ),
)


# ---------------------------------------------------------------------------
# fixture_detail -- one fixture, expanded
# ---------------------------------------------------------------------------

#: Model and market are shown SIDE BY SIDE and flagged when they disagree by
#: more than this, never averaged. They are two estimators with different
#: biases and the mean would hide the signal their gap carries -- the rule
#: odds_derivation.md section 4 already set for competing clean-sheet methods.
DISAGREE_PP = 3.0

DETAIL_PARAMS: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["fixture_id"],
    "properties": {
        "season": season_param(),
        "fixture_id": {"type": "integer", "minimum": 1},
        "as_of": {"type": ["string", "null"], "default": None},
        "meetings_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
    },
}

_SIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["team_code", "short_name"],
    "properties": {
        "team_code": {"type": "integer"},
        "short_name": {"type": ["string", "null"]},
        "name": {"type": ["string", "null"]},
        "is_home": {"type": "boolean"},
    },
}

_BLOCK = {"type": ["object", "null"], "additionalProperties": True}

DETAIL_RESULT: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["season", "fixture_id", "gw", "home", "away", "as_of", "inputs"],
    "properties": {
        "season": {"type": "string"},
        "fixture_id": {"type": "integer"},
        "gw": {"type": "integer"},
        "as_of": {"type": "string"},
        "kickoff_utc": {"type": ["string", "null"]},
        "finished": {"type": "boolean"},
        "score": {"type": ["object", "null"], "additionalProperties": True},
        "home": _SIDE_SCHEMA,
        "away": _SIDE_SCHEMA,
        "inputs": {"type": "array", "items": INPUT_SCHEMA},
        "model": _BLOCK,
        "market": _BLOCK,
        "derived_clean_sheet": _BLOCK,
        "disagreement": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "form": _BLOCK,
        "team_news": _BLOCK,
        "intel": _BLOCK,
        "predicted_lineups": _BLOCK,
        "previous_meetings": _BLOCK,
        "creator_team_talk": _BLOCK,
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}


def _gap(reason: str, **extra: Any) -> dict[str, Any]:
    """A named gap. Not whitespace, not a zero -- a sentence a reader can act on."""
    return {"available": False, "unavailable": reason, **extra}


def _section(label: str, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one OPTIONAL section builder; an unexpected crash becomes a named gap.

    The same rule `_safe_q` applies to a missing table, applied to the whole
    section: a data edge in team intel (a NULL `ord` in set_piece_duty took the
    entire drawer down for 21 of 60 fixtures) must degrade to the house
    honest-empty shape for THAT section, loudly, instead of 500ing the panel.
    The exception type is named so the gap is a bug report, not whitespace --
    dropped loudly, never silently. The model/market core stays unguarded on
    purpose: a crash there means the panel has no headline number and should
    fail its run visibly through the API error contract.
    """
    try:
        return build()
    except Exception as exc:  # noqa: BLE001 - converted to a named, rendered gap
        return _gap(
            f"the {label} section crashed on this fixture's data "
            f"({type(exc).__name__}: {exc}) and is dropped rather than guessed "
            f"-- a data edge worth reporting, not an empty week"
        )


#: Tables created by feature migrations rather than by the base schema. A fresh
#: clone, or a warehouse whose migrations have only partly run, genuinely does
#: not have them -- ``intel_item``, ``set_piece_duty`` and ``content_insight``
#: are all absent from ``Warehouse()``'s own CREATE list. A drilldown that
#: crashed on that would take the whole panel down over an optional section.
def _safe_q(wh, table: str, sql: str, params: tuple = ()) -> tuple[pd.DataFrame, str | None]:
    """Query an OPTIONAL table. A missing table is a named gap, not a traceback."""
    try:
        return q(wh, sql, params), None
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "does not exist" in text or "Catalog Error" in text:
            return pd.DataFrame(), (
                f"`{table}` is not in this warehouse -- it is created by a feature "
                f"migration that has not run here, so there is nothing to show and "
                f"nothing to infer"
            )
        return pd.DataFrame(), (
            f"`{table}` could not be read ({type(exc).__name__}); the section is "
            f"dropped rather than guessed"
        )


def _model_block(ratings: _Ratings | None, reason: str | None,
                 home: int, away: int, now: dt.datetime) -> dict[str, Any]:
    if ratings is None:
        return _gap(reason or "no fitted ratings artefact")
    if not ratings.has(home, away):
        return _gap(
            "one or both clubs are absent from the stored fit -- the fixture was "
            "added or rescheduled after the last ratings build"
        )
    pop = ratings.population()
    anchors = {"anchor_att": pop["anchor_attack_xg"], "anchor_def": pop["anchor_defence_xg"],
               "ref_cs": pop["ref_clean_sheet"], "ref_pen": pop["ref_concede_penalty"]}
    out: dict[str, Any] = {
        "available": True, "unavailable": None,
        "fitted_at": _iso(ratings.fitted_at),
        "age_hours": _hours_since(ratings.fitted_at, now),
        "half_life_days": ratings.half_life_days,
        "n_matches": ratings.n_matches,
        "effective_n": round(ratings.effective_n, 1),
        "rho": round(ratings.rho, 4),
        "converged": ratings.converged,
    }
    for side, me, them, is_home in (("home", home, away, True), ("away", away, home, False)):
        pair = pop["by_pair"][(them, is_home)]
        mu_a, lam_a = ratings.opponent_only_rates(them, is_home)
        mu_f, lam_f = ratings.fixture_rates(me, them, is_home)
        out[side] = {
            "opponent_only": _lens(ratings.quantities(mu_a, lam_a), **anchors,
                                   ranks=(int(pair["attack_rank"]), int(pair["defence_rank"]))),
            "fixture_specific": _lens(ratings.quantities(mu_f, lam_f), **anchors),
            "rating": {"attack": round(ratings.attack[me], 4),
                       "defence": round(ratings.defence[me], 4),
                       "is_promoted": me in ratings.promoted,
                       "matches_seen": ratings.matches_seen.get(me, 0)},
        }
    # The 1X2 view: one matrix, home orientation, own strengths in.
    from fpl_edge.models.team_goals.scoreline import (
        GoalRates,
        outcome_probs,
        prob_over,
        score_matrix,
    )
    mu, lam = ratings.fixture_rates(home, away, True)
    mat = score_matrix(GoalRates(mu, lam, ratings.rho))
    p_h, p_d, p_a = outcome_probs(mat)
    out["match"] = {
        "p_home_win": round(p_h, 4), "p_draw": round(p_d, 4), "p_away_win": round(p_a, 4),
        "p_over_2_5": round(prob_over(mat, 2.5), 4),
        "home_xg": round(mu, 4), "away_xg": round(lam, 4),
        "basis": "fixture_specific -- both clubs' own fitted ratings, which is the "
                 "honest prediction. The board's colour uses opponent_only instead.",
    }
    return out


def _market_block(wh, season: str, fixture_id: int, now: dt.datetime,
                  rho: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """De-vigged prices and the goal rates they imply, plus their age.

    Returns ``(market, derived_clean_sheet)``. The second is kept separate and
    is NOT called a market: all 3,260 ``clean_sheet`` rows in ``fact_odds``
    carry ``bookmaker = 'derived#poisson'`` -- they are this repo's own Poisson
    inversion written back, not a quote anybody posted. Calling a derivation a
    bookmaker price would be the most misleading thing this page could do.
    """
    odds, reason = _resolved_odds(wh, season, now)
    if odds.empty:
        return _gap(reason or "no odds"), _gap(reason or "no odds")
    mine = odds[odds["fixture_id"] == fixture_id]
    if mine.empty:
        return (_gap(f"no bookmaker has quoted fixture {fixture_id}; "
                     f"books typically open a Premier League match around a week out"),
                _gap(f"no derived clean-sheet row for fixture {fixture_id}"))

    cs_rows = mine[mine["market"] == "clean_sheet"]
    derived: dict[str, Any]
    if cs_rows.empty:
        derived = _gap("no derived clean-sheet row for this fixture")
    else:
        cs_age = _hours_since(cs_rows["as_of"].max(), now)
        derived = {
            "available": True, "unavailable": None,
            "is_a_market": False,
            "method": str(cs_rows["bookmaker"].iloc[0]),
            "as_of": _iso(cs_rows["as_of"].max()),
            "age_hours": None if cs_age is None else round(cs_age, 2),
            "p_home_clean_sheet": None, "p_away_clean_sheet": None,
            "warning": (
                "This is NOT a posted market. Every clean_sheet row in fact_odds "
                "carries bookmaker='derived#poisson' -- it is our own inversion of "
                "the 1X2 and totals prices, written back. Books do post a real "
                "clean-sheet market; this warehouse does not ingest it."
            ),
        }
        for sel, key in (("home", "p_home_clean_sheet"), ("away", "p_away_clean_sheet")):
            hit = cs_rows[cs_rows["selection"] == sel]
            if not hit.empty:
                derived[key] = round(float(1.0 / hit["price_decimal"].mean()), 4)

    priced = mine[mine["market"].isin(["h2h", "totals"])]
    if priced.empty:
        return _gap("only derived rows exist for this fixture; no 1X2 or totals "
                    "price to de-vig"), derived

    from fpl_edge.models.team_goals.market import invert_odds
    from fpl_edge.models.team_goals.odds import devig_frame

    quotes = devig_frame(priced, method="proportional")
    key = f"{season}:{fixture_id}"
    fo = quotes.get(key)
    if fo is None:
        return _gap(
            "the 1X2 rows for this fixture are incomplete -- no single bookmaker "
            "quoted all three selections, so there is nothing to de-vig"
        ), derived

    age = _hours_since(priced["as_of"].max(), now)
    state = _market_state(age)
    inv = invert_odds(fo, rho=rho)
    from fpl_edge.models.team_goals.scoreline import GoalRates, score_matrix
    mat = score_matrix(GoalRates(inv.rates.home, inv.rates.away, rho))
    market = {
        "available": True, "unavailable": None,
        "state": state,
        "as_of": _iso(priced["as_of"].max()),
        "age_hours": None if age is None else round(age, 2),
        "n_books": int(fo.n_books),
        "devig_method": "proportional",
        "overround_h2h": round(fo.overround_h2h, 4),
        "overround_totals": None if fo.overround_totals is None else round(fo.overround_totals, 4),
        "p_home_win": round(fo.p_home, 4),
        "p_draw": round(fo.p_draw, 4),
        "p_away_win": round(fo.p_away, 4),
        "p_over_2_5": None if fo.p_over is None else round(fo.p_over, 4),
        "totals_line": fo.totals_line,
        "implied": {
            "home_xg": round(inv.rates.home, 4),
            "away_xg": round(inv.rates.away, 4),
            "residual": round(inv.residual, 6),
            "used_totals": bool(inv.used_totals),
            "p_home_clean_sheet": round(float(mat[:, 0].sum()), 4),
            "p_away_clean_sheet": round(float(mat[0, :].sum()), 4),
            "note": (
                "Two unknowns against three or four constraints, so the residual "
                "is informative: a large one means the quoted prices are not "
                "consistent with ANY bivariate Poisson, which is a data-quality "
                "signal rather than something to absorb silently."
            ),
        },
        "staleness_effect": {
            "priced": "the price is inside the 12h window and reflects current team news",
            "stale": f"older than {ODDS_STALE_HOURS:.0f}h -- shown, but it predates any press conference since",
            "expired": f"older than {ODDS_USELESS_HOURS:.0f}h -- shown greyed as a contrast only",
            "unpriced": "no quote",
        }[state],
        "casing_workaround": (
            "fact_odds stores selections upper-case (HOME, OVER_2.5) while "
            "team_goals.odds.devig_frame matches lower-case; this panel lowers "
            "them before de-vigging. Unpatched, devig_frame returns zero fixtures "
            "against a warehouse holding 131,921 odds rows."
        ),
    }
    return market, derived


def _disagreement(model: dict[str, Any], market: dict[str, Any]) -> list[dict[str, Any]]:
    """Where the two estimators part company, in percentage points. Never averaged."""
    if not model.get("available") or not market.get("available"):
        return []
    pairs = [
        ("P(home win)", model["match"]["p_home_win"], market["p_home_win"]),
        ("P(draw)", model["match"]["p_draw"], market["p_draw"]),
        ("P(away win)", model["match"]["p_away_win"], market["p_away_win"]),
        ("P(over 2.5)", model["match"]["p_over_2_5"], market.get("p_over_2_5")),
        ("P(home clean sheet)", model["home"]["fixture_specific"]["p_clean_sheet"],
         market["implied"]["p_home_clean_sheet"]),
        ("P(away clean sheet)", model["away"]["fixture_specific"]["p_clean_sheet"],
         market["implied"]["p_away_clean_sheet"]),
    ]
    out = []
    for label, m, k in pairs:
        if m is None or k is None:
            continue
        gap = (m - k) * 100.0
        out.append({
            "metric": label, "model": round(m, 4), "market": round(k, 4),
            "gap_pp": round(gap, 2), "flagged": abs(gap) >= DISAGREE_PP,
            "market_age_hours": market["age_hours"],
        })
    return out


def _news_block(wh, season: str, codes: tuple[int, int], now: dt.datetime) -> dict[str, Any]:
    rows = q(wh, """
        WITH st AS (SELECT * EXCLUDE(rn) FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM fact_player_state WHERE season = ? AND as_of <= ?) WHERE rn = 1),
             pl AS (SELECT season, code, web_name, team_code, position FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1)
        SELECT pl.team_code, pl.web_name, pl.position, st.status,
               st.chance_of_playing_next_round AS chance, st.news, st.news_added,
               st.selected_by_pct, st.as_of
        FROM st JOIN pl ON pl.season = st.season AND pl.code = st.code
        WHERE pl.team_code IN (?, ?) AND st.status <> 'a'
        ORDER BY st.selected_by_pct DESC
    """, (season, now, season, now, int(codes[0]), int(codes[1])))
    if rows.empty:
        return _gap(
            "no player at either club carries a non-available status at this "
            "instant -- which is a real answer, not a missing one"
        )
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows.itertuples(index=False):
        by.setdefault(str(int(r.team_code)), []).append({
            "web_name": str(r.web_name), "position": int(r.position),
            "status": str(r.status),
            "chance_of_playing": None if pd.isna(r.chance) else int(r.chance),
            "news": None if r.news is None else str(r.news),
            "news_added": _iso(r.news_added),
            "news_age_hours": _hours_since(r.news_added, now),
            "selected_by_pct": float(r.selected_by_pct),
        })
    return {"available": True, "unavailable": None, "by_team": by,
            "as_of": _iso(rows["as_of"].max()),
            "age_hours": _hours_since(rows["as_of"].max(), now),
            "note": "Ordered by ownership, so the notes that move decisions lead. "
                    "Every row carries its own timestamp: a three-day-old injury "
                    "note is not the same claim as a three-hour-old one."}


def _intel_block(wh, season: str, codes: tuple[int, int], now: dt.datetime) -> dict[str, Any]:
    items, items_gap = _safe_q(wh, "intel_item", """
        SELECT kind, team_code, headline, body, source, source_url, confidence,
               published_at, observed_at
        FROM intel_item
        WHERE season = ? AND observed_at <= ? AND team_code IN (?, ?)
          AND kind IN ('set_piece', 'press_conference')
        ORDER BY published_at DESC
    """, (season, now, int(codes[0]), int(codes[1])))
    duties, duties_gap = _safe_q(wh, "set_piece_duty", """
        WITH d AS (SELECT * EXCLUDE(rn) FROM (
              SELECT *, row_number() OVER (
                PARTITION BY season, code, duty ORDER BY as_of DESC) rn
              FROM set_piece_duty WHERE season = ? AND as_of <= ?) WHERE rn = 1),
             pl AS (SELECT season, code, web_name FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1)
        SELECT d.team_code, d.duty, d.ord, pl.web_name, d.source, d.as_of
        FROM d LEFT JOIN pl ON pl.season = d.season AND pl.code = d.code
        WHERE d.team_code IN (?, ?)
        ORDER BY d.team_code, d.duty, d.ord
    """, (season, now, season, now, int(codes[0]), int(codes[1])))

    missing = "; ".join(g for g in (items_gap, duties_gap) if g)
    out: dict[str, Any] = {
        "available": bool(len(items) or len(duties)),
        "unavailable": None if len(items) or len(duties) else (
            missing or "no team-level set-piece or press-conference item for either club"),
        "framing": (
            "Set pieces are shown as DUTY -- who takes them -- and never as a team "
            "trait. Set-piece goals-over-expected barely persists season to season, "
            "so 'this club over-performs on set pieces' is not a durable claim; "
            "'this player takes the corners' is."
        ),
        "set_piece_duty": {}, "set_piece_items": [], "press_conference": [],
        "as_of": None, "age_hours": None,
    }
    for r in duties.itertuples(index=False):
        out["set_piece_duty"].setdefault(str(int(r.team_code)), []).append({
            # `ord` is NULL for some bootstrap-static rows (the FPL API lists a
            # taker without ranking him). That is an absent order, not a zero
            # and not an error: `int(NaN)` here was the crash that 500'd every
            # fixture involving those clubs -- 21 of 60 board cells, silently.
            "duty": str(r.duty),
            "order": None if pd.isna(r.ord) else int(r.ord),
            "player": None if r.web_name is None else str(r.web_name),
            "source": str(r.source), "as_of": _iso(r.as_of),
        })
    for r in items.itertuples(index=False):
        entry = {
            "team_code": int(r.team_code), "headline": str(r.headline),
            "body": None if r.body is None else str(r.body),
            "source": str(r.source),
            "source_url": None if r.source_url is None else str(r.source_url),
            "confidence": None if pd.isna(r.confidence) else float(r.confidence),
            "published_at": _iso(r.published_at),
            "age_hours": _hours_since(r.published_at, now),
        }
        out["set_piece_items" if r.kind == "set_piece" else "press_conference"].append(entry)
    stamps = [s for s in (
        None if items.empty else items["observed_at"].max(),
        None if duties.empty else duties["as_of"].max(),
    ) if s is not None]
    if stamps:
        newest = max(pd.to_datetime(s, utc=True) for s in stamps)
        out["as_of"] = _iso(newest)
        out["age_hours"] = _hours_since(newest, now)
    return out


def _lineups_block(wh, season: str, gw: int, codes: tuple[int, int],
                   now: dt.datetime) -> dict[str, Any]:
    # Latest SNAPSHOT per team, not latest ROW per player. When rotowire drops a
    # player from the XI it stops emitting a row for them rather than writing
    # predicted_start = false, so a per-player "latest" resurrects every player
    # who was ever named: Palace's GW2 XI came back with thirteen starters, the
    # eleven plus two dropped a day earlier. A player absent from the newest
    # snapshot is not in the XI.
    rows = q(wh, """
        WITH vis AS (SELECT * FROM fact_predicted_lineup
                     WHERE season = ? AND gw = ? AND as_of <= ?),
             newest AS (SELECT provider, team_code, max(as_of) AS mx
                        FROM vis GROUP BY 1, 2),
             lu AS (SELECT vis.* FROM vis JOIN newest
                      ON newest.provider = vis.provider
                     AND newest.team_code = vis.team_code
                     AND newest.mx = vis.as_of),
             pl AS (SELECT season, code, web_name, position FROM (
              SELECT *, row_number() OVER (PARTITION BY season, code ORDER BY as_of DESC) rn
              FROM dim_player WHERE season = ? AND as_of <= ?) WHERE rn = 1)
        SELECT lu.provider, lu.team_code, lu.code, pl.web_name, pl.position,
               lu.predicted_start, lu.certainty, lu.as_of
        FROM lu LEFT JOIN pl ON pl.season = lu.season AND pl.code = lu.code
        WHERE lu.team_code IN (?, ?)
        ORDER BY lu.team_code, lu.predicted_start DESC, lu.certainty DESC
    """, (season, int(gw), now, season, now, int(codes[0]), int(codes[1])))
    if rows.empty:
        return _gap(
            f"no predicted XI for GW{gw} yet. rotowire publishes roughly 48 hours "
            f"before kickoff, so this is expected until then rather than missing."
        )
    age = _hours_since(rows["as_of"].max(), now)
    by: dict[str, Any] = {}
    for r in rows.itertuples(index=False):
        by.setdefault(str(int(r.team_code)), []).append({
            "web_name": None if r.web_name is None else str(r.web_name),
            "position": None if pd.isna(r.position) else int(r.position),
            "predicted_start": bool(r.predicted_start),
            # `certainty` is a VARCHAR label the provider chooses -- 'expected',
            # 'questionable', 'out', 'suspended' -- not a probability. Passed
            # through verbatim; coercing it to a number would invent a scale.
            "certainty": None if r.certainty is None else str(r.certainty),
        })
    return {
        "available": True, "unavailable": None,
        "provider": str(rows["provider"].iloc[0]),
        "gw": int(gw), "by_team": by,
        "as_of": _iso(rows["as_of"].max()),
        "age_hours": None if age is None else round(age, 2),
        "state": "fresh" if (age or 0) <= LINEUP_STALE_HOURS else "stale",
        "effect_when_stale": "a predicted XI older than a day and a half predates "
                             "the press conference that usually settles it",
    }


def _meetings_block(wh, home: int, away: int, now: dt.datetime, limit: int) -> dict[str, Any]:
    rows = q(wh, """
        SELECT season, gw, kickoff_utc, team_code, opponent_code, is_home,
               goals_for, goals_against, fixture_id
        FROM sem_fixtures(?)
        WHERE finished AND ((team_code = ? AND opponent_code = ?))
        ORDER BY kickoff_utc DESC LIMIT ?
    """, (now, int(home), int(away), int(limit)))
    if rows.empty:
        return _gap(
            "these two clubs have not met in a season this warehouse holds. There "
            "is nothing to show and nothing to infer."
        )
    xg = q(wh, """
        WITH fx AS (SELECT * FROM sem_fixtures(?) WHERE finished),
             pl AS (SELECT season, code, team_code FROM (
                      SELECT *, row_number() OVER (
                        PARTITION BY season, code ORDER BY as_of DESC) rn
                      FROM dim_player WHERE as_of <= ?) WHERE rn = 1),
             pf AS (SELECT * FROM sem_player_form(?))
        SELECT fx.fixture_id, fx.team_code, SUM(pf.expected_goals) AS xg_for
        FROM fx JOIN pl ON pl.season = fx.season AND pl.team_code = fx.team_code
                JOIN pf ON pf.season = fx.season AND pf.fixture_id = fx.fixture_id
                       AND pf.code = pl.code
        WHERE fx.team_code IN (?, ?)
        GROUP BY 1, 2
    """, (now, now, now, int(home), int(away)))
    xg_map = {(int(r.fixture_id), int(r.team_code)): round(float(r.xg_for), 2)
              for r in xg.itertuples(index=False)}
    matches = []
    for r in rows.itertuples(index=False):
        matches.append({
            "season": str(r.season), "gw": int(r.gw),
            "kickoff_utc": _iso(r.kickoff_utc),
            "venue": "home" if bool(r.is_home) else "away",
            "goals_for": None if pd.isna(r.goals_for) else int(r.goals_for),
            "goals_against": None if pd.isna(r.goals_against) else int(r.goals_against),
            "xg_for": xg_map.get((int(r.fixture_id), int(r.team_code))),
            "xg_against": xg_map.get((int(r.fixture_id), int(r.opponent_code))),
        })
    return {
        "available": True, "unavailable": None,
        "orientation": "from the home club's point of view, both venues",
        "matches": matches,
        "caution": (
            f"{len(matches)} matches across several seasons, with different "
            f"managers and mostly different players, is not evidence about this "
            f"one. Head-to-head is the most over-read object in fixture analysis; "
            f"this section says so where it shows it, not in a footnote."
        ),
    }


def _team_talk_block(wh, season: str, codes: tuple[int, int], now: dt.datetime) -> dict[str, Any]:
    have, gap = _safe_q(wh, "content_insight", "SELECT count(*) AS n FROM content_insight")
    if gap is not None:
        return _gap(gap, rows=0)
    total = 0 if have.empty else int(have.iloc[0]["n"])
    if total == 0:
        return _gap(
            "content_insight holds 0 rows. The extraction is wired into both "
            "writers now, so this means no analysed item has yet produced a "
            "team-level observation -- run `fpl-content backfill-insights` to "
            "recover them from analyses already on disk.",
            rows=0,
        )
    # Filtered to THIS fixture's two clubs. It previously took `codes` and
    # ignored them, returning every team-level insight in the season -- so an
    # Arsenal v Villa drawer showed opinions about Hull and Everton under a
    # heading that said they were about this match.
    rows, _ = _safe_q(wh, "content_insight", """
        SELECT creator, topic, entity_kind, entity_name, team_code, claim_text,
               quote, start_s, confidence, published_at, extractor
        FROM content_insight
        WHERE season = ? AND entity_kind = 'team' AND published_at <= ?
          AND team_code IN (?, ?)
        ORDER BY published_at DESC LIMIT 40
    """, (season, now, int(codes[0]), int(codes[1])))
    # An unattributable insight is counted, never shown: the reader is told how
    # much opinion exists that could not be tied to a club, rather than being
    # left to assume the silence means nobody said anything.
    unresolved, _ = _safe_q(wh, "content_insight", """
        SELECT count(*) AS n FROM content_insight
        WHERE season = ? AND entity_kind = 'team' AND published_at <= ?
          AND team_code IS NULL
    """, (season, now))
    n_unresolved = 0 if unresolved.empty else int(unresolved.iloc[0]["n"])
    unresolved_note = (
        f" {n_unresolved} team-level insight(s) this season name a club the "
        f"resolver refused to guess at (ASR mangles club names -- this "
        f"warehouse holds 'suddenland' and 'ipsswitch'); they are excluded "
        f"here rather than attached to the nearest-looking club."
        if n_unresolved else ""
    )
    if rows.empty:
        return _gap(
            f"content_insight holds {total} rows, none of them a team-level "
            f"insight about either of these clubs for {season} at this "
            f"instant.{unresolved_note}", rows=total)
    return {
        "available": True, "unavailable": None,
        "items": rows.to_dict("records"),
        "note": "Clubs are resolved once at write time by exact then "
                "containment match, never by edit distance -- on this season's "
                "twenty clubs nearest-match sends 'forester' to Brentford."
                + unresolved_note,
    }


def fixture_detail(
    wh, *, season: str, fixture_id: int, as_of: str | None = None,
    meetings_limit: int = 8,
) -> dict[str, Any]:
    """One fixture, expanded: model, market, form, news, XI, meetings, talk.

    Every section carries its own age and its own reason for being empty. The
    model and the market are shown side by side and flagged where they disagree
    by three percentage points or more; they are never averaged, because they
    are two estimators with different biases and the mean would hide the one
    signal their gap carries.

    The clean-sheet numbers in ``fact_odds`` are served under
    ``derived_clean_sheet``, never under ``market``: all 3,260 of them carry
    ``bookmaker='derived#poisson'`` and are this repo's own inversion written
    back, not a price anybody posted.
    """
    now = dt.datetime.now(UTC)
    if as_of:
        parsed = pd.to_datetime(as_of, utc=True, errors="coerce")
        if parsed is pd.NaT or pd.isna(parsed):
            return empty(f"as_of={as_of!r} is not an ISO instant; nothing was read.")
        now = parsed.to_pydatetime()

    fx = q(wh, """
        SELECT fixture_id, gw, kickoff_utc, finished, team_code, opponent_code,
               is_home, team, opponent, goals_for, goals_against
        FROM sem_fixtures(?) WHERE season = ? AND fixture_id = ?
    """, (now, season, int(fixture_id)))
    if fx.empty:
        return empty(
            f"No {season} fixture {fixture_id} is known at {now.isoformat()}. "
            f"Fixture ids come from fact_fixture; run `make ingest` if the "
            f"schedule is behind."
        )
    home_row = fx[fx["is_home"]].iloc[0]
    away_row = fx[~fx["is_home"]].iloc[0]
    home, away = int(home_row["team_code"]), int(away_row["team_code"])
    gw = int(home_row["gw"])

    names = q(wh, """
        SELECT team_code, short_name, name FROM (
          SELECT *, row_number() OVER (
            PARTITION BY season, team_code ORDER BY as_of DESC) rn
          FROM dim_team WHERE season = ? AND as_of <= ?) WHERE rn = 1
    """, (season, now))
    full = {int(r.team_code): (str(r.short_name), None if r.name is None else str(r.name))
            for r in names.itertuples(index=False)}

    ratings, ratings_reason = load_ratings(wh, season)
    model = _model_block(ratings, ratings_reason, home, away, now)
    rho = ratings.rho if ratings is not None else 0.0
    market, derived = _market_block(wh, season, int(fixture_id), now, rho)
    form, _, form_newest = _team_form(wh, season, now, ratings)

    codes = (home, away)
    news = _section("team news", lambda: _news_block(wh, season, codes, now))
    intel = _section("team intel", lambda: _intel_block(wh, season, codes, now))
    lineups = _section("predicted XI",
                       lambda: _lineups_block(wh, season, gw, codes, now))
    meetings = _section("previous meetings",
                        lambda: _meetings_block(wh, home, away, now, meetings_limit))
    talk = _section("creator team-talk",
                    lambda: _team_talk_block(wh, season, codes, now))

    inputs = [
        _input_row("fitted ratings", source=RATINGS_NAME,
                   as_of=None if ratings is None else ratings.fitted_at, now=now,
                   stale_after_hours=RATINGS_STALE_HOURS,
                   rows=None if ratings is None else len(ratings.codes),
                   missing=ratings is None,
                   effect_when_stale="the fit predates recent results; numbers still shown",
                   detail=(ratings_reason or "no fitted ratings artefact"
                           if ratings is None
                           else f"Dixon-Coles, {ratings.n_matches} matches")),
        _input_row("market odds", source="fact_odds h2h + totals",
                   as_of=market.get("as_of"), now=now,
                   stale_after_hours=ODDS_STALE_HOURS,
                   rows=market.get("n_books"), missing=not market.get("available"),
                   effect_when_stale=(
                       f"past {ODDS_USELESS_HOURS:.0f}h the price is shown only as a "
                       f"contrast; it is never blended into the model number"),
                   detail=market.get("unavailable") or
                          f"{market.get('n_books')} books, de-vigged proportionally"),
        _input_row("derived clean sheet", source="fact_odds bookmaker='derived#poisson'",
                   as_of=derived.get("as_of"), now=now,
                   stale_after_hours=ODDS_STALE_HOURS, rows=1 if derived.get("available") else 0,
                   missing=not derived.get("available"),
                   effect_when_stale="inherits the staleness of the prices it was derived from",
                   detail="our own Poisson inversion written back, NOT a posted market"),
        _input_row("predicted XI", source="fact_predicted_lineup",
                   as_of=lineups.get("as_of"), now=now,
                   stale_after_hours=LINEUP_STALE_HOURS,
                   rows=None if not lineups.get("available") else
                        sum(len(v) for v in lineups["by_team"].values()),
                   missing=not lineups.get("available"),
                   effect_when_stale="the XI predates the press conference that usually settles it",
                   detail=lineups.get("unavailable") or f"provider {lineups.get('provider')}"),
        _input_row("team intel", source="intel_item (set_piece, press_conference) + set_piece_duty",
                   as_of=intel.get("as_of"), now=now, stale_after_hours=INTEL_STALE_HOURS,
                   rows=(len(intel.get("set_piece_items", [])) +
                         len(intel.get("press_conference", []))),
                   missing=not intel.get("available"),
                   effect_when_stale="a set-piece order can change in a single training week",
                   detail=intel.get("unavailable") or "team-keyed intel; nothing else in the UI renders it"),
        _input_row("team news", source="fact_player_state",
                   as_of=news.get("as_of"), now=now, stale_after_hours=24.0,
                   rows=None if not news.get("available") else
                        sum(len(v) for v in news["by_team"].values()),
                   missing=not news.get("available"),
                   effect_when_stale="an injury flag can be lifted an hour before a deadline",
                   detail=news.get("unavailable") or "non-available statuses, ordered by ownership"),
        _input_row("creator team-talk", source="content_insight",
                   as_of=None, now=now, stale_after_hours=None, rows=talk.get("rows", 0),
                   missing=not talk.get("available"),
                   effect_when_stale="none; the extraction is not wired up at all",
                   detail=talk.get("unavailable") or "team-level insights"),
    ]

    notes = [
        ("The model number here is FIXTURE-SPECIFIC: both clubs' own fitted "
         "ratings are in it. The board's colour is opponent-only, which is a "
         "different number answering a different question. Both are on every cell."),
    ]
    if market.get("available") and market.get("state") in ("stale", "expired"):
        notes.append(
            f"The market price is {market['age_hours']:.0f} hours old "
            f"({market['state']}). It is shown for contrast and is not in any "
            f"model number on this page."
        )

    return {
        "season": season,
        "fixture_id": int(fixture_id),
        "gw": gw,
        "as_of": now.isoformat(),
        "kickoff_utc": _iso(home_row["kickoff_utc"]),
        "finished": bool(home_row["finished"]),
        "score": None if not bool(home_row["finished"]) else {
            "home": None if pd.isna(home_row["goals_for"]) else int(home_row["goals_for"]),
            "away": None if pd.isna(home_row["goals_against"]) else int(home_row["goals_against"]),
        },
        "home": {"team_code": home, "short_name": full.get(home, (None, None))[0],
                 "name": full.get(home, (None, None))[1], "is_home": True},
        "away": {"team_code": away, "short_name": full.get(away, (None, None))[0],
                 "name": full.get(away, (None, None))[1], "is_home": False},
        "inputs": inputs,
        "model": model,
        "market": market,
        "derived_clean_sheet": derived,
        "disagreement": _disagreement(model, market),
        "form": {
            "home": form.get(home, {"window_matches": 0, "unavailable":
                                    "no completed match for this club at this instant"}),
            "away": form.get(away, {"window_matches": 0, "unavailable":
                                    "no completed match for this club at this instant"}),
            "window": FORM_WINDOW,
            "newest_match": _iso(form_newest),
            "note": "xG for is summed over the club's players; xG against takes one "
                    "representative value per team-match, because "
                    "expected_goals_conceded is written per player and every "
                    "outfielder carries the team's value -- summing it gives ~30 "
                    "xGC for a single fixture.",
        },
        "team_news": news,
        "intel": intel,
        "predicted_lineups": lineups,
        "previous_meetings": meetings,
        "creator_team_talk": talk,
        "notes": notes,
    }


register_script(
    "fixture_detail",
    fixture_detail,
    params_schema=DETAIL_PARAMS,
    result_schema=DETAIL_RESULT,
    title="Fixture detail",
    description=(
        "One fixture expanded: model and market side by side with their ages, "
        "clean-sheet and goal probabilities, form, team news, set-piece and "
        "press intel, predicted XIs, previous meetings, creator team-talk."
    ),
)


# ---------------------------------------------------------------------------
# the build job -- NEVER called by a panel
# ---------------------------------------------------------------------------


def write_artefacts(
    db_path: Path | str | None = None, *, season: str = SEASON_DEFAULT,
    out_dir: Path | str | None = None, now: dt.datetime | None = None,
    calibration: bool = True,
) -> dict[str, Any]:
    """Fit from a private read copy and overwrite both cached artefacts.

    ``read_copy`` keeps the fit off the live file so ingest writers are never
    blocked -- DuckDB is one-writer-XOR-many-readers and the Telegram bot holds
    write leases between polls. Overwriting is correct: these are caches, not
    append-only facts, and yesterday's ratings for fixtures that have since
    kicked off are not history worth keeping.
    """
    from fpl_edge.store import DEFAULT_DB, Warehouse

    db_path = Path(db_path) if db_path is not None else Path(DEFAULT_DB)
    out = Path(out_dir) if out_dir is not None else db_path.parent
    report: dict[str, Any] = {}
    with Warehouse.read_copy(db_path) as wh:
        t0 = time.monotonic()
        ratings = build_board_ratings(wh, season=season, now=now)
        report["ratings_rows"] = len(ratings)
        report["ratings_seconds"] = round(time.monotonic() - t0, 2)
        ratings.to_parquet(out / RATINGS_NAME, index=False)
        report["ratings_path"] = str(out / RATINGS_NAME)
        if calibration:
            t1 = time.monotonic()
            calib = build_calibration(wh)
            report["calibration_rows"] = len(calib)
            report["calibration_seconds"] = round(time.monotonic() - t1, 2)
            calib.to_parquet(out / CALIBRATION_NAME, index=False)
            report["calibration_path"] = str(out / CALIBRATION_NAME)
    return report


def main(argv: list[str] | None = None) -> int:
    from fpl_edge.store import DEFAULT_DB

    parser = argparse.ArgumentParser(
        description="Build the cached artefacts the fixtures panels read.")
    parser.add_argument("--build", action="store_true",
                        help="fit and write fixture_ratings.parquet (+ calibration)")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--season", default=SEASON_DEFAULT)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--no-calibration", action="store_true",
                        help="skip the four-season regression (it is the slow half)")
    args = parser.parse_args(argv)
    if not args.build:
        parser.error("nothing to do; pass --build")
    report = write_artefacts(
        args.db, season=args.season, out_dir=args.out_dir,
        calibration=not args.no_calibration,
    )
    for key, value in report.items():
        print(f"{key}: {value}")
    return 0






# The CLI guard lives at the very END of the module on purpose: `python -m`
# executes top to bottom, and a SystemExit raised mid-file would leave
# fixture_detail undefined and unregistered in that process.
if __name__ == "__main__":
    raise SystemExit(main())
