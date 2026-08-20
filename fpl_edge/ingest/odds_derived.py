"""Derived bookmaker-implied probabilities: the calibrated prior layer.

``fact_odds`` stores what the market *quoted*. This module stores what the
market *means*: per-team clean-sheet probabilities, per-team expected goals
and per-player scoring rates, each tagged with the derivation method that
produced it, in a table of its own (``fact_odds_derived``). The projection
ensemble consumes these as its prior; the maths behind each derivation is
written up in ``docs/platform/odds_derivation.md``.

Why a separate table
--------------------
``fact_odds`` rows are facts about the world: a book quoted a price at an
instant. A derived probability is a *modelling choice* about those facts, and
different methods legitimately disagree (correct-score summation vs bivariate
Poisson inversion give clean-sheet numbers ~1-3pp apart on real GW1 cards).
Keeping methods side by side in their own table means the ensemble can be
re-pointed at a different method without re-ingesting anything, and a backtest
can score the methods against each other honestly.

The table is created by :func:`ensure_derived_schema` -- an additive,
idempotent migration owned by this module, deliberately not added to
``fpl_edge/store/schema.sql`` (that file describes quoted facts; this table is
derived). Importing this module also registers the table in
:data:`~fpl_edge.store.warehouse.PIT_KEYS`, which is what lets
``Warehouse.append`` and ``Snapshot.table`` treat it with the same
point-in-time discipline as every other fact table.

Method names written to ``fact_odds_derived.method``
----------------------------------------------------
* ``cs_grid#<devig>`` -- clean sheet as a sum over a de-vigged correct-score
  grid (e.g. ``cs_grid#power``).
* ``poisson_indep`` -- bivariate-Poisson inversion of 1X2 (+ totals) with
  ``rho = 0``.
* ``dixon_coles`` -- the same inversion with the backtest-fitted low-score
  dependence ``rho`` (:data:`RHO_STAR`).
* ``team_totals`` -- Poisson rate solved from the team-totals over/under
  ladder, where a book quotes one (US books only as of 2026-08-19).
* ``scorer_power`` -- per-player scoring rate from the anytime-scorer card
  after the power-transform margin estimate in
  :func:`fpl_edge.ingest.odds.devig_anytime_scorer`.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.optimize import brentq, least_squares
from scipy.stats import poisson

from fpl_edge.ingest.odds import (
    MARKET_H2H,
    MARKET_TOTALS,
    ODDS_API_TEAM_ALIASES,
    DevigMethod,
    devig,
    implied_prob,
)
from fpl_edge.models.team_goals.scoreline import (
    GoalRates,
    clean_sheet_probs,
    score_matrix,
)
from fpl_edge.store import Warehouse
from fpl_edge.store.warehouse import PIT_KEYS

# --------------------------------------------------------------------------
# schema
# --------------------------------------------------------------------------

TABLE = "fact_odds_derived"

#: Entity namespaces. ``entity_code`` is an FPL ``team_code`` for teams and an
#: FPL player ``code`` for players -- the same identifiers every other fact
#: table joins on.
ENTITY_TEAM = "team"
ENTITY_PLAYER = "player"

#: Derived market names.
D_CLEAN_SHEET = "clean_sheet_prob"
D_TEAM_LAMBDA = "team_lambda"
D_ANYTIME = "anytime_prob"
D_XG_SHARE = "xg_share"

#: Dixon-Coles low-score dependence used for the ``dixon_coles`` method rows.
#: Fitted by ``scripts/backtest_clean_sheets.py`` on 2022-23 (minimum Brier
#: against realised clean sheets) and held fixed out-of-sample thereafter.
#: See docs/platform/odds_derivation.md for the fit and its evaluation.
RHO_STAR = -0.05

_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    fixture_key  VARCHAR NOT NULL,   -- same convention as fact_odds
    season       VARCHAR NOT NULL,
    entity_type  VARCHAR NOT NULL,   -- 'team' | 'player'
    entity_code  INTEGER NOT NULL,   -- FPL team_code or player code
    market       VARCHAR NOT NULL,   -- clean_sheet_prob | team_lambda | anytime_prob | xg_share
    method       VARCHAR NOT NULL,   -- how the number was derived; see module docstring
    value        DOUBLE  NOT NULL,   -- probability for *_prob markets, a rate for team_lambda
    as_of        TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (fixture_key, entity_type, entity_code, market, method, as_of)
);
"""

#: Registered at import so that ``Warehouse.append``/``Snapshot.table`` accept
#: the table. ``setdefault`` keeps a double import harmless.
PIT_KEYS.setdefault(
    TABLE, ("fixture_key", "entity_type", "entity_code", "market", "method")
)


def ensure_derived_schema(wh: Warehouse) -> None:
    """Create ``fact_odds_derived`` if absent. Additive and idempotent."""
    wh.sql(_SCHEMA_SQL)


# --------------------------------------------------------------------------
# clean sheets from a correct-score grid
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CorrectScoreDerivation:
    """Clean-sheet probabilities read off one book's correct-score grid."""

    p_home_cs: float
    p_away_cs: float
    p_nil_nil: float
    quoted_sum: float  # sum of raw implied probabilities: 1 + overround - truncated tail
    n_cells: int


def parse_correct_score_selection(
    selection: str, home: str, away: str
) -> tuple[int, int] | None:
    """Parse The Odds API's ``"TeamA:x|TeamB:y"`` into ``(home_goals, away_goals)``.

    The order of the two halves is NOT fixed: William Hill's card for
    Arsenal v Coventry (2026-08-21) quotes ``"Arsenal:2|Coventry City:0"`` for
    home wins but flips to ``"Coventry City:1|Arsenal:0"`` for away wins --
    the winner is listed first. Assuming home-first silently transposes every
    away-win cell, which inflates the home clean sheet, so the team names are
    matched explicitly and an unrecognised name returns ``None`` rather than a
    guess.
    """
    parts = str(selection).split("|")
    if len(parts) != 2:
        return None
    goals: dict[str, int] = {}
    for part in parts:
        m = re.fullmatch(r"(.+):(\d+)", part.strip())
        if not m:
            return None
        goals[m.group(1).strip()] = int(m.group(2))
    if set(goals) != {home, away}:
        return None
    return goals[home], goals[away]


def clean_sheets_from_correct_score(
    cells: dict[tuple[int, int], float],
    *,
    method: DevigMethod = "power",
) -> CorrectScoreDerivation:
    """Clean-sheet probabilities as sums over a de-vigged correct-score grid.

    The estimator is exact given a fair grid: scores are mutually exclusive and
    a home clean sheet is precisely the event ``away_goals == 0``, so

        P(home CS) = sum_x P(x-0),    P(away CS) = sum_y P(0-y).

    No distributional assumption -- the book's own joint distribution is used,
    including whatever score dependence it prices in. That is the advantage
    over the Poisson inversion, and it is why the two methods disagree.

    Two real-world defects, both measured on live GW1 cards (2026-08-19):

    * **Truncation.** Books quote a finite grid with no "any other score"
      bucket (William Hill: 44 cells; the Betfair exchange: a 4x4 grid). The
      de-vig therefore renormalises over the quoted cells only, which pushes
      the missing tail mass back onto quoted cells roughly in proportion. The
      tail is mostly high-scoring both-score cells, so clean-sheet columns gain
      slightly more than they should. ``quoted_sum`` is reported so callers can
      see how much overround-plus-tail was removed.
    * **Longshot loading.** Correct-score margins concentrate on the tail
      (101.0-151.0 quotes are price floors, not opinions). ``method="power"``
      is the default because it shrinks exactly those cells hardest;
      ``multiplicative`` leaves too much mass in the floor-priced tail and is
      provided for sensitivity analysis. The disagreement between the two on
      the same card is reported in docs/platform/odds_derivation.md.

    ``cells`` maps ``(home_goals, away_goals)`` to a decimal price. At least
    six cells are required: below that the "market" is a fragment and the
    renormalisation is meaningless.
    """
    if len(cells) < 6:
        raise ValueError(f"correct-score grid too sparse to de-vig: {len(cells)} cells")
    keys = sorted(cells)
    prices = [cells[k] for k in keys]
    quoted_sum = float(sum(implied_prob(p) for p in prices))
    fair = devig(prices, method)
    p_home_cs = float(sum(p for (h, a), p in zip(keys, fair) if a == 0))
    p_away_cs = float(sum(p for (h, a), p in zip(keys, fair) if h == 0))
    p00 = float(next((p for (h, a), p in zip(keys, fair) if h == 0 and a == 0), 0.0))
    return CorrectScoreDerivation(
        p_home_cs=p_home_cs,
        p_away_cs=p_away_cs,
        p_nil_nil=p00,
        quoted_sum=quoted_sum,
        n_cells=len(cells),
    )


# --------------------------------------------------------------------------
# team goal rates: from 1X2 + totals, or from a team-totals ladder
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchInversion:
    """Goal rates recovered from de-vigged match-level probabilities."""

    rates: GoalRates
    residual: float  # max abs difference between target and reproduced probs
    used_totals: bool


def invert_match_odds(
    p_home: float,
    p_draw: float,
    p_away: float,
    p_over: float | None = None,
    *,
    line: float = 2.5,
    rho: float = 0.0,
    max_goals: int = 10,
) -> MatchInversion:
    """Solve ``(lambda_home, lambda_away)`` from 1X2 (+ totals) probabilities.

    The forward model is the Dixon-Coles score matrix (independent Poisson when
    ``rho = 0``): two unknowns against three constraints (four with totals), so
    the system is overdetermined and solved in the least-squares sense. The 1X2
    triple pins the *supremacy* ``lambda_h - lambda_a`` tightly but the overall
    scoring level only weakly; the totals leg is what pins ``lambda_h +
    lambda_a``, so pass ``p_over`` whenever a totals quote exists.

    This is the same inversion as
    :func:`fpl_edge.models.team_goals.market.invert_odds`; reimplemented thinly
    here (30 lines over the shared ``score_matrix``) rather than imported
    because that function is welded to the ``FixtureOdds``/provider protocol
    and this module needs to invert ad-hoc probability triples from history
    CSVs in the backtest.
    """
    target = [p_home, p_draw, p_away] + ([p_over] if p_over is not None else [])

    def resid(theta: np.ndarray) -> np.ndarray:
        lam, mu = np.exp(theta)
        mat = score_matrix(GoalRates(float(lam), float(mu), rho), max_goals)
        idx = np.arange(max_goals + 1)
        h = float(np.tril(mat, -1).sum())
        d = float(np.trace(mat))
        a = float(np.triu(mat, 1).sum())
        got = [h, d, a]
        if p_over is not None:
            tot = idx[:, None] + idx[None, :]
            got.append(float(mat[tot > line].sum()))
        return np.asarray(got) - np.asarray(target)

    sol = least_squares(resid, x0=np.log([1.45, 1.15]), method="lm", xtol=1e-12, ftol=1e-12)
    lam, mu = (float(v) for v in np.exp(sol.x))
    return MatchInversion(
        rates=GoalRates(lam, mu, rho),
        residual=float(np.abs(sol.fun).max()),
        used_totals=p_over is not None,
    )


def team_lambda_from_totals(lines: Iterable[tuple[float, float]]) -> float:
    """Poisson rate implied by a team-totals over/under ladder.

    Each entry is ``(line, p_over)`` with ``p_over`` already de-vigged against
    its own under leg (team totals is a complete two-way market per line, so a
    per-line de-vig is exact -- unlike anytime scorer, where the No side is not
    quoted at all).

    Under ``G ~ Poisson(lambda)`` and a half-goal line ``k + 0.5``::

        P(over) = P(G >= k + 1) = 1 - CDF(k; lambda)

    One line gives an exact root (solved with ``brentq``); a ladder of
    alternate lines is overdetermined, so the rate minimising the squared
    probability error across all lines is returned. The ladder is worth paying
    for when available: a single 0.5 line barely distinguishes lambda 1.6 from
    2.0, while the 1.5/2.5/3.5 rungs pin the mean tightly.

    Integer and quarter lines are rejected: a push/void leg breaks the
    two-outcome identity above and none of the observed US books quote them
    for team totals.
    """
    pts = [(float(k), float(p)) for k, p in lines]
    if not pts:
        raise ValueError("no totals lines supplied")
    for k, p in pts:
        if not 0.0 < p < 1.0:
            raise ValueError(f"p_over must be in (0, 1), got {p} at line {k}")
        if abs(k - math.floor(k) - 0.5) > 1e-9:
            raise ValueError(f"only half-goal lines are supported, got {k}")

    def p_over(lam: float, k: float) -> float:
        return float(poisson.sf(math.floor(k), lam))

    if len(pts) == 1:
        k, p = pts[0]
        return float(brentq(lambda lam: p_over(lam, k) - p, 1e-6, 30.0, xtol=1e-12))

    def sse(log_lam: np.ndarray) -> np.ndarray:
        lam = float(np.exp(log_lam[0]))
        return np.asarray([p_over(lam, k) - p for k, p in pts])

    sol = least_squares(sse, x0=[0.3], method="lm", xtol=1e-12, ftol=1e-12)
    return float(np.exp(sol.x[0]))


# --------------------------------------------------------------------------
# anytime scorer -> per-player rate and xG share
# --------------------------------------------------------------------------


def scorer_rate(p_anytime: float) -> float:
    """Poisson scoring rate implied by a fair anytime-scorer probability.

    If player *i*'s goals are ``Poisson(lambda_i)`` then
    ``P(anytime) = 1 - exp(-lambda_i)``, so ``lambda_i = -ln(1 - p)``.
    The Poisson assumption is doing real work: it says the *rates* are additive
    across a team (``sum lambda_i = lambda_team``) where the probabilities are
    not, which is exactly the property the de-vig anchor relies on.
    """
    if not 0.0 < p_anytime < 1.0:
        raise ValueError(f"p_anytime must be in (0, 1), got {p_anytime}")
    return -math.log1p(-p_anytime)


# --------------------------------------------------------------------------
# the derivation pipeline: fact_odds -> fact_odds_derived
# --------------------------------------------------------------------------


def _slug(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def team_code_by_slug(teams: pd.DataFrame) -> dict[str, int]:
    """Map fixture-key slugs onto FPL team codes.

    Natural fixture keys embed the *bookmaker's* club name, slugified
    (``manchester-city``), while ``dim_team`` holds the FPL short name
    (``Man City``). Both spellings are mapped: the FPL name directly, and each
    Odds API long form via the audited alias table in
    :mod:`fpl_edge.ingest.odds`.
    """
    by_name = {str(r["name"]): int(r["team_code"]) for _, r in teams.iterrows()}
    out = {_slug(name): code for name, code in by_name.items()}
    for api_name, fpl_name in ODDS_API_TEAM_ALIASES.items():
        if fpl_name in by_name:
            out.setdefault(_slug(api_name), by_name[fpl_name])
    return out


@dataclass(frozen=True, slots=True)
class DerivationReport:
    """What one derivation run produced, per method."""

    fixtures_seen: int
    fixtures_derived: int
    rows_written: int
    by_method: dict[str, int]
    skipped: list[str]  # fixture_key: reason


def _consensus_prob(
    g: pd.DataFrame, market: str, selections: list[str]
) -> list[float] | None:
    """Mean quoted decimal price per selection across books, or None if incomplete.

    De-vig always happens *after* averaging: averaging already-normalised
    probabilities from books with different overrounds is a subtle way to
    double-count the sharp books' margin structure.
    """
    rows = g[(g["market"] == market) & (~g["bookmaker"].str.contains("#", na=False))]
    out: list[float] = []
    for sel in selections:
        prices = rows[rows["selection"] == sel]["price_decimal"]
        if prices.empty:
            return None
        out.append(float(prices.mean()))
    return out


def derive_fixture_rows(
    fixture_key: str,
    season: str,
    quotes: pd.DataFrame,
    home_code: int,
    away_code: int,
    as_of: dt.datetime,
    *,
    player_team: dict[int, int] | None = None,
    cs_grid_method: DevigMethod = "power",
) -> tuple[list[dict[str, Any]], str | None]:
    """All derivable ``fact_odds_derived`` rows for one fixture.

    ``quotes`` is that fixture's slice of ``fact_odds``. Returns the rows plus
    an optional skip-reason (when even the 1X2 anchor is missing).

    ``player_team`` maps FPL player code -> team_code, used to attach each
    scorer-card player to the right team lambda for the xG share. Players
    missing from the map are attributed to neither team and get an
    ``anytime_prob`` row but no ``xg_share`` row.
    """
    rows: list[dict[str, Any]] = []

    def add(entity_type: str, code: int, market: str, method: str, value: float) -> None:
        rows.append({
            "fixture_key": fixture_key, "season": season,
            "entity_type": entity_type, "entity_code": int(code),
            "market": market, "method": method,
            "value": float(value), "as_of": as_of,
        })

    # -- anchor: de-vigged 1X2 (+ totals) -> lambdas -> clean sheets --------
    h2h = _consensus_prob(quotes, MARKET_H2H, ["HOME", "DRAW", "AWAY"])
    if h2h is None:
        return [], f"{fixture_key}: no complete 1X2 quote"
    p1x2 = devig(h2h, "shin")

    p_over = None
    ou = _consensus_prob(quotes, MARKET_TOTALS, ["OVER_2.5", "UNDER_2.5"])
    if ou is not None:
        p_over = float(devig(ou, "shin")[0])

    inversions: dict[str, MatchInversion] = {}
    for method, rho in (("poisson_indep", 0.0), ("dixon_coles", RHO_STAR)):
        try:
            inv = invert_match_odds(
                float(p1x2[0]), float(p1x2[1]), float(p1x2[2]), p_over, rho=rho
            )
        except (ValueError, RuntimeError):
            continue
        inversions[method] = inv
        add(ENTITY_TEAM, home_code, D_TEAM_LAMBDA, method, inv.rates.home)
        add(ENTITY_TEAM, away_code, D_TEAM_LAMBDA, method, inv.rates.away)
        mat = score_matrix(inv.rates, 10)
        cs_h, cs_a = clean_sheet_probs(mat)
        add(ENTITY_TEAM, home_code, D_CLEAN_SHEET, method, cs_h)
        add(ENTITY_TEAM, away_code, D_CLEAN_SHEET, method, cs_a)

    # -- clean sheets straight off correct-score grids ----------------------
    cs_quotes = quotes[quotes["market"] == "correct_score"]
    per_book_h, per_book_a = [], []
    for _, book_rows in cs_quotes.groupby("bookmaker"):
        cells: dict[tuple[int, int], float] = {}
        for _, r in book_rows.iterrows():
            # Selections were normalised to "<home>-<away>" at ingest (see
            # odds_markets.parse_extra_market_event), which is what resolved
            # the bookmaker's winner-first ordering against the event's teams.
            m = re.fullmatch(r"(\d+)-(\d+)", str(r["selection"]))
            if not m:
                continue
            cells[(int(m.group(1)), int(m.group(2)))] = float(r["price_decimal"])
        try:
            d = clean_sheets_from_correct_score(cells, method=cs_grid_method)
        except ValueError:
            continue
        per_book_h.append(d.p_home_cs)
        per_book_a.append(d.p_away_cs)
    if per_book_h:
        add(ENTITY_TEAM, home_code, D_CLEAN_SHEET, f"cs_grid#{cs_grid_method}",
            float(np.mean(per_book_h)))
        add(ENTITY_TEAM, away_code, D_CLEAN_SHEET, f"cs_grid#{cs_grid_method}",
            float(np.mean(per_book_a)))

    # -- team lambdas straight off team-totals ladders -----------------------
    tt = quotes[quotes["market"] == "team_totals"]
    if not tt.empty:
        for side, code in (("HOME", home_code), ("AWAY", away_code)):
            ladder: dict[float, dict[str, list[float]]] = {}
            side_rows = tt[tt["selection"].str.startswith(f"{side}|")]
            for _, r in side_rows.iterrows():
                m = re.fullmatch(r"[A-Z]+\|(OVER|UNDER)_([0-9.]+)", str(r["selection"]))
                if not m:
                    continue
                ladder.setdefault(float(m.group(2)), {}).setdefault(
                    m.group(1), []
                ).append(float(r["price_decimal"]))
            lines: list[tuple[float, float]] = []
            for point, legs in sorted(ladder.items()):
                if "OVER" not in legs or "UNDER" not in legs:
                    continue
                over = float(np.mean(legs["OVER"]))
                under = float(np.mean(legs["UNDER"]))
                lines.append((point, float(devig([over, under], "shin")[0])))
            if lines:
                try:
                    add(ENTITY_TEAM, code, D_TEAM_LAMBDA, "team_totals",
                        team_lambda_from_totals(lines))
                except (ValueError, RuntimeError):
                    pass

    # -- per-player scoring rate and xG share -------------------------------
    # fair#scorer_power rows are written by ingest_odds_api_gameweek with the
    # FPL player code as the selection; anything non-numeric is an unresolved
    # raw bookmaker name and is skipped here.
    lam_team = {
        home_code: inversions.get("poisson_indep").rates.home
        if "poisson_indep" in inversions else None,
        away_code: inversions.get("poisson_indep").rates.away
        if "poisson_indep" in inversions else None,
    }
    scorer = quotes[
        (quotes["market"] == "anytime_scorer")
        & (quotes["bookmaker"] == "fair#scorer_power")
    ]
    for _, r in scorer.iterrows():
        sel = str(r["selection"])
        if not sel.isdigit():
            continue
        code = int(sel)
        p_fair = 1.0 / float(r["price_decimal"])
        if not 0.0 < p_fair < 1.0:
            continue
        add(ENTITY_PLAYER, code, D_ANYTIME, "scorer_power", p_fair)
        lam_i = scorer_rate(p_fair)
        team = (player_team or {}).get(code)
        lam_t = lam_team.get(team) if team is not None else None
        if lam_t:
            add(ENTITY_PLAYER, code, D_XG_SHARE, "scorer_power", lam_i / lam_t)

    return rows, None


def derive_gameweek_priors(
    wh: Warehouse,
    season: str,
    *,
    as_of: dt.datetime | None = None,
    cs_grid_method: DevigMethod = "power",
) -> DerivationReport:
    """Derive priors for every fixture with a future kickoff date in its key.

    Reads the latest quotes visible at ``as_of`` (default: now) through the
    snapshot API, so the derivation obeys the same point-in-time rules as
    everything else. Writes to ``fact_odds_derived`` stamped at ``as_of``.
    """
    now = as_of or dt.datetime.now(dt.timezone.utc)
    ensure_derived_schema(wh)

    snap = wh.snapshot_at(now)
    teams = snap.table("dim_team", where="season = ?", params=[season])
    players = snap.table("dim_player", where="season = ?", params=[season])
    slug_to_code = team_code_by_slug(teams)
    player_team = (
        {int(r["code"]): int(r["team_code"]) for _, r in players.iterrows()}
        if not players.empty else {}
    )

    odds = snap.table(
        "fact_odds",
        where="fixture_key LIKE ?",
        params=[f"{season}:%"],
    )

    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    seen = derived = 0
    for key, g in odds.groupby("fixture_key"):
        parts = str(key).split(":")
        if len(parts) != 4:
            continue  # matched "season:fixture_id" keys carry no team slugs
        _, day, home_slug, away_slug = parts
        try:
            if dt.date.fromisoformat(day) < now.date():
                continue  # past fixture: priors are for upcoming decisions
        except ValueError:
            continue
        seen += 1
        home_code = slug_to_code.get(home_slug)
        away_code = slug_to_code.get(away_slug)
        if home_code is None or away_code is None:
            skipped.append(f"{key}: unmapped club slug")
            continue
        fixture_rows, reason = derive_fixture_rows(
            str(key), season, g, home_code, away_code, now,
            player_team=player_team, cs_grid_method=cs_grid_method,
        )
        if reason:
            skipped.append(reason)
            continue
        rows.extend(fixture_rows)
        derived += 1

    df = pd.DataFrame(rows, columns=[
        "fixture_key", "season", "entity_type", "entity_code",
        "market", "method", "value", "as_of",
    ])
    written = wh.append(TABLE, df) if len(df) else 0
    by_method: dict[str, int] = {}
    if len(df):
        by_method = df.groupby("method").size().to_dict()
    return DerivationReport(
        fixtures_seen=seen,
        fixtures_derived=derived,
        rows_written=written,
        by_method={str(k): int(v) for k, v in by_method.items()},
        skipped=skipped,
    )
