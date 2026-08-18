"""Bookmaker odds: fetch, de-vig, and land in ``fact_odds``.

Why odds at all
---------------
The points model predicts FPL returns. Bookmakers predict football. Their
closing line is the single best-calibrated public forecast of goals and clean
sheets that exists, and it is the only external yardstick we can score our own
attacking/defensive rates against. If our model says Haaland is a 62% anytime
scorer and the market says 48%, one of us is wrong and it is probably us.

What is actually reachable for free
-----------------------------------
Measured on 2026-08-18; see ``docs/data_sources.md`` for the full evidence
table with HTTP status codes.

* **football-data.co.uk** -- HTTP 200, no key, no rate limit observed,
  ``robots.txt`` is ``Disallow:`` (i.e. allow all). Carries 1X2, Over/Under 2.5
  and Asian handicap from ~20 books including Pinnacle and the Betfair
  Exchange, back to 1993-94 (O/U 2.5 from 2005-06). **No anytime scorer.**
  This is the ingestion path implemented here and wired into
  ``scripts/ingest_odds.py``.
* **The Odds API** -- HTTP 401 without a key. It is the only source found that
  publishes ``player_goal_scorer_anytime`` for the EPL, but the free tier is
  500 credits/month and historical snapshots are paid-only. A client is
  implemented below and unit-tested against a recorded fixture; it needs a key
  the account holder must obtain themselves.
* **Pinnacle / Betfair / OddsPortal / oddschecker** -- all rejected. See
  ``docs/data_sources.md`` for the specific status code or licence term.

Point-in-time discipline
------------------------
This is the subtle part and it is easy to leak here.

``mmz4281/<season>/E0.csv`` is only *published* after matches are played, but
the odds it contains were publicly quoted *before* kickoff. The honest ``as_of``
for a historical row is therefore the fixture's kickoff instant -- the last
moment the closing line was observable. That deliberately makes closing odds
invisible at an FPL deadline 90+ minutes earlier, which is correct: you could
not have bet the close when you picked your team.

``fixtures.csv`` is forward-looking and published before kickoff, so its rows
are stamped with the fetch instant. That is the live path, and it is the one
that can legitimately inform a decision made at a deadline.

De-vigging
----------
A bookmaker's quoted implied probabilities sum to more than 1; the excess is
the overround. Three removal methods are implemented because they disagree
materially on longshots, and anytime-scorer style markets live in the longshot
tail:

* ``multiplicative`` -- divide through by the sum. Simple, and wrong in a known
  direction: it under-prices favourites and over-prices longshots.
* ``shin`` -- Shin (1993), models the overround as protection against insider
  trading. The default.
* ``power`` -- solves ``p = q**k``. Adjusts the tail hardest.

On a book with no overround all three are the identity, which is asserted in
the tests.
"""

from __future__ import annotations

import datetime as dt
import io
import math
import re
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from fpl_edge.ingest.http import Fetched, Fetcher, _now, _slug
from fpl_edge.store import Warehouse

FOOTBALL_DATA_BASE = "https://www.football-data.co.uk"
ODDS_API_BASE = "https://api.the-odds-api.com/v4"

UK = ZoneInfo("Europe/London")

#: Markets we write. Kept deliberately small: these are the only ones the
#: points model can currently consume.
MARKET_H2H = "h2h"
MARKET_TOTALS = "totals"
MARKET_CLEAN_SHEET = "clean_sheet"
MARKET_ANYTIME_SCORER = "anytime_scorer"

DevigMethod = Literal["multiplicative", "shin", "power"]

#: Below this overround a book is treated as already fair and returned
#: unchanged. Guards the root-finders, which have no bracket when S == 1.
_FAIR_TOL = 1e-9


# --------------------------------------------------------------------------
# price conversions
# --------------------------------------------------------------------------


def american_to_decimal(price: float) -> float:
    """Convert American (moneyline) odds to decimal.

    Pinnacle and most US books quote American. ``-202`` means stake 202 to win
    100; ``+487`` means stake 100 to win 487.
    """
    if price == 0:
        raise ValueError("American odds of 0 are not a price")
    if price > 0:
        return 1.0 + price / 100.0
    return 1.0 + 100.0 / abs(price)


def implied_prob(decimal_odds: float) -> float:
    """Raw (vigged) implied probability of a decimal price."""
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must exceed 1.0, got {decimal_odds!r}")
    return 1.0 / decimal_odds


def overround(decimal_odds: list[float] | np.ndarray) -> float:
    """Bookmaker margin of a complete market: ``sum(1/o) - 1``.

    A 5.8% overround on a 1X2 market is typical for a UK high-street book; the
    Betfair Exchange and Pinnacle run nearer 1-2%.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    return float(q.sum() - 1.0)


# --------------------------------------------------------------------------
# de-vigging
# --------------------------------------------------------------------------


def devig_multiplicative(decimal_odds: list[float] | np.ndarray) -> np.ndarray:
    """Proportional (normalisation) de-vig: ``p_i = q_i / sum(q)``.

    Assumes the book applies its margin uniformly in probability space. It does
    not: real books load the margin onto longshots, so this systematically
    under-states favourites. Provided as a baseline and for comparison.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    return q / q.sum()


def devig_shin(decimal_odds: list[float] | np.ndarray) -> np.ndarray:
    """Shin (1993) de-vig.

    Models the book as quoting against a proportion ``z`` of insider bettors,
    and solves for the ``z`` that makes the implied fair probabilities sum to
    one::

        p_i = (sqrt(z^2 + 4(1-z) q_i^2 / S) - z) / (2(1-z)),   S = sum(q)

    Empirically closer to realised frequencies than the multiplicative method,
    and the direction of the correction is the one we care about: it pushes
    probability *towards* the favourite and away from the tail.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    return _shin_from_q(q)


def _shin_from_q(q: np.ndarray) -> np.ndarray:
    s = float(q.sum())
    if s <= 1.0 + _FAIR_TOL:
        # Already fair (or underround, e.g. an exchange mid-price). Shin has no
        # root here; normalising is the only defensible thing to do.
        return q / s

    def p_of_z(z: float) -> np.ndarray:
        return (np.sqrt(z**2 + 4.0 * (1.0 - z) * q**2 / s) - z) / (2.0 * (1.0 - z))

    lo, hi = 1e-12, 0.5
    if p_of_z(hi).sum() - 1.0 > 0:
        hi = 0.9  # pathological overround; widen the bracket
    z = brentq(lambda z: float(p_of_z(z).sum()) - 1.0, lo, hi, xtol=1e-14)
    p = p_of_z(z)
    return p / p.sum()  # kill residual float drift so the contract holds exactly


def shin_z(decimal_odds: list[float] | np.ndarray) -> float:
    """The fitted Shin insider proportion. 0 for a fair book."""
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    s = float(q.sum())
    if s <= 1.0 + _FAIR_TOL:
        return 0.0

    def p_of_z(z: float) -> np.ndarray:
        return (np.sqrt(z**2 + 4.0 * (1.0 - z) * q**2 / s) - z) / (2.0 * (1.0 - z))

    hi = 0.5 if p_of_z(0.5).sum() - 1.0 <= 0 else 0.9
    return float(brentq(lambda z: float(p_of_z(z).sum()) - 1.0, 1e-12, hi, xtol=1e-14))


def devig_power(decimal_odds: list[float] | np.ndarray) -> np.ndarray:
    """Power de-vig: solve ``k`` such that ``sum(q_i ** k) == 1``.

    Since every ``q_i < 1`` and the book is overround (``sum(q) > 1``), the
    solution has ``k > 1``, which shrinks small probabilities proportionally
    more than large ones. The most aggressive of the three on the tail.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    s = float(q.sum())
    if s <= 1.0 + _FAIR_TOL:
        return q / s
    hi = 2.0
    while float((q**hi).sum()) - 1.0 > 0 and hi < 64.0:
        hi *= 2.0
    k = brentq(lambda k: float((q**k).sum()) - 1.0, 1.0, hi, xtol=1e-14)
    p = q**k
    return p / p.sum()


_DEVIG: dict[str, Any] = {
    "multiplicative": devig_multiplicative,
    "shin": devig_shin,
    "power": devig_power,
}


def devig(
    decimal_odds: list[float] | np.ndarray, method: DevigMethod = "shin"
) -> np.ndarray:
    """De-vig a *complete* market. The result sums to 1 by construction.

    "Complete" matters: passing two of a 1X2 market's three legs produces
    confident nonsense. Anytime-scorer markets are *not* complete in this sense
    (the outcomes are not mutually exclusive) -- use :func:`devig_independent`.
    """
    if len(decimal_odds) < 2:
        raise ValueError("de-vigging needs at least two outcomes")
    try:
        fn = _DEVIG[method]
    except KeyError:
        raise ValueError(f"unknown de-vig method {method!r}; known: {sorted(_DEVIG)}") from None
    return fn(decimal_odds)


def devig_independent(
    decimal_odds: list[float] | np.ndarray, expected_total: float
) -> np.ndarray:
    """De-vig a market of *non-exclusive* yes/no legs, e.g. anytime scorer.

    Twenty-odd players can all score in the same match, so the legs do not sum
    to 1 -- they sum to the expected number of *distinct* scorers, which is
    strictly less than expected goals. Scaling to a target total is the
    standard fix; ``expected_total`` should come from the match totals market,
    not from the scorer market itself.

    Kept separate from :func:`devig` so that nobody accidentally normalises a
    scorer market to 1.0 and silently halves every striker's probability.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    if expected_total <= 0:
        raise ValueError("expected_total must be positive")
    return q * (expected_total / q.sum())


# --------------------------------------------------------------------------
# deriving clean-sheet probability from team-level markets
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GoalRates:
    """Fitted Poisson means for a fixture, plus the fit residual."""

    home: float
    away: float
    residual: float


def _match_probs(lam_h: float, lam_a: float, max_goals: int = 15) -> tuple[float, float, float, float]:
    """(P(home), P(draw), P(away), P(over 2.5)) under independent Poisson.

    ``max_goals=15`` truncates the Poisson tail at under 1e-9 of mass for any
    realistic football scoring rate, which keeps the forward model invertible to
    the precision the round-trip test demands.
    """
    gh = np.array([math.exp(-lam_h) * lam_h**k / math.factorial(k) for k in range(max_goals + 1)])
    ga = np.array([math.exp(-lam_a) * lam_a**k / math.factorial(k) for k in range(max_goals + 1)])
    joint = np.outer(gh, ga)
    idx = np.arange(max_goals + 1)
    home = float(joint[idx[:, None] > idx[None, :]].sum())
    draw = float(np.trace(joint))
    away = float(joint[idx[:, None] < idx[None, :]].sum())
    tot = idx[:, None] + idx[None, :]
    over = float(joint[tot >= 3].sum())
    return home, draw, away, over


def fit_goal_rates(
    p_home: float, p_draw: float, p_away: float, p_over25: float | None = None
) -> GoalRates:
    """Recover Poisson goal expectations from de-vigged team-level odds.

    football-data.co.uk carries no clean-sheet market, so we back one out. The
    1X2 probabilities pin down the *supremacy*; without a totals market the
    overall scoring level is only weakly identified, so pass ``p_over25``
    whenever the CSV has it (it does from 2005-06 onward).

    Independent Poisson is the honest-but-imperfect choice. Rather than assert a
    direction for its bias, it was measured against realised results for every
    Premier League team-match in 2023-24, 2024-25 and 2025-26 (n = 2,280):

    ==========================  =======
    Mean predicted clean sheet   0.2532
    Mean realised clean sheet    0.2320
    Bias                        **+2.1pp** (optimistic)
    Brier score                  0.1671
    Brier, base rate only        0.1782
    ==========================  =======

    So the derivation carries real skill (6% Brier improvement over the base
    rate) and a consistent ~2 percentage-point *over*-estimate. Downstream
    calibration should shrink it, not treat it as unbiased. The over-estimate is
    largest in the tails: +3.0pp below 0.15 and +4.7pp above 0.45.
    """
    from scipy.optimize import least_squares

    target = [p_home, p_draw, p_away] + ([p_over25] if p_over25 is not None else [])

    def resid(theta: np.ndarray) -> np.ndarray:
        lam_h, lam_a = np.exp(theta)
        h, d, a, o = _match_probs(float(lam_h), float(lam_a))
        got = [h, d, a] + ([o] if p_over25 is not None else [])
        return np.asarray(got) - np.asarray(target)

    sol = least_squares(resid, x0=np.log([1.4, 1.2]), method="lm", xtol=1e-12, ftol=1e-12)
    lam_h, lam_a = (float(v) for v in np.exp(sol.x))
    return GoalRates(home=lam_h, away=lam_a, residual=float(np.abs(sol.fun).max()))


def clean_sheet_probs(rates: GoalRates) -> tuple[float, float]:
    """(P(home clean sheet), P(away clean sheet)) = (P(away scores 0), P(home scores 0))."""
    return math.exp(-rates.away), math.exp(-rates.home)


# --------------------------------------------------------------------------
# football-data.co.uk
# --------------------------------------------------------------------------

#: Column prefix -> canonical bookmaker key. football-data uses one prefix per
#: book, a bare suffix for opening odds and a ``C`` infix for closing odds.
#: Verified against the live 2025-26 E0.csv header (132 columns) on 2026-08-18.
FD_BOOKS_1X2: dict[str, str] = {
    "B365": "bet365",
    "BFD": "betfair_sportsbook",
    "BMGM": "betmgm",
    "BV": "betvictor",
    "BW": "bwin",
    "CL": "coral",
    "LB": "ladbrokes",
    "PS": "pinnacle",
    "PP": "paddypower",
    "SKB": "skybet",
    "BFE": "betfair_exchange",
    "Max": "market_max",
    "Avg": "market_avg",
    "WH": "williamhill",
    "VC": "vcbet",
    "IW": "interwetten",
}

#: Over/Under 2.5 uses a different (shorter) prefix set; Pinnacle is ``P`` here
#: but ``PS`` in the 1X2 block, which is exactly the sort of thing that must be
#: written down rather than inferred.
FD_BOOKS_OU: dict[str, str] = {
    "B365": "bet365",
    "P": "pinnacle",
    "Max": "market_max",
    "Avg": "market_avg",
    "BFE": "betfair_exchange",
}

#: football-data spells some clubs differently from the FPL API.
FD_TEAM_ALIASES: dict[str, str] = {
    "Man United": "Manchester United",
    "Man City": "Manchester City",
    "Nott'm Forest": "Nottingham Forest",
    "Newcastle": "Newcastle United",
    "Tottenham": "Spurs",
    "Wolves": "Wolverhampton Wanderers",
    "West Ham": "West Ham United",
    "West Brom": "West Bromwich Albion",
    "Sheffield United": "Sheffield Utd",
    "Leeds": "Leeds United",
    "Leicester": "Leicester City",
    "Norwich": "Norwich City",
    "Stoke": "Stoke City",
    "Cardiff": "Cardiff City",
    "Swansea": "Swansea City",
    "Hull": "Hull City",
    "Birmingham": "Birmingham City",
    "Brighton": "Brighton & Hove Albion",
    "Luton": "Luton Town",
    "Ipswich": "Ipswich Town",
}


class TextFetcher(Fetcher):
    """:class:`Fetcher` for non-JSON bodies (CSV, HTML).

    Subclassed rather than added to ``http.py`` so the archive/retry/User-Agent
    policy stays in one place and this module owns only its own additions.
    """

    def get_text(self, endpoint: str, params: dict[str, Any] | None = None,
                 suffix: str = ".csv") -> Fetched:
        url = f"{self.base_url}/{endpoint.lstrip('/')}" if self.base_url else endpoint
        fetched_at = _now()
        resp = self._get(url, params)
        payload = resp.content
        import hashlib

        digest = hashlib.sha256(payload).hexdigest()
        out_dir = self._raw_root() / self.source
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
        path = out_dir / f"{_slug(endpoint)}_{stamp}_{digest[:8]}{suffix}"
        if not path.exists():
            path.write_bytes(payload)
        return Fetched(
            body=resp.text, fetched_at=fetched_at, sha256=digest, body_path=path,
            http_status=resp.status_code, from_cache=False,
        )

    @staticmethod
    def _raw_root():
        from fpl_edge.ingest.http import RAW_ROOT

        return RAW_ROOT


def fd_season_code(season: str) -> str:
    """``"2025-26"`` -> ``"2526"``, football-data's directory convention."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", season)
    if not m:
        raise ValueError(f"season must look like '2025-26', got {season!r}")
    return f"{m.group(1)[2:]}{m.group(2)}"


def _kickoff_utc(date_s: str, time_s: str | None) -> dt.datetime | None:
    """Parse football-data's ``dd/mm/yyyy`` + UK-local ``HH:MM`` to UTC.

    Rows before ~2019 have no Time column; those are stamped at 23:59 UK on the
    match date, which is conservative (later than any real kickoff, so the odds
    can never appear visible earlier than they truly were).
    """
    if not isinstance(date_s, str) or not date_s.strip():
        return None
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            # Naive by design: football-data prints a bare UK-local date, and
            # the zone is attached below once the time component is known.
            d = dt.datetime.strptime(date_s.strip(), fmt).date()  # noqa: DTZ007
            break
        except ValueError:
            continue
    else:
        return None
    if isinstance(time_s, str) and re.fullmatch(r"\d{1,2}:\d{2}", time_s.strip()):
        hh, mm = (int(x) for x in time_s.strip().split(":"))
    else:
        hh, mm = 23, 59
    return dt.datetime(d.year, d.month, d.day, hh, mm, tzinfo=UK).astimezone(dt.timezone.utc)


def _slugify(s: object) -> str:
    """Lowercase, hyphen-separated form used in every natural fixture key."""
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def natural_fixture_key(season: str, kickoff: dt.datetime | None, home: str, away: str) -> str:
    """Stable key for a fixture we have not yet matched to an FPL fixture_id.

    ``fact_odds.fixture_key`` is documented as "season:fixture_id once matched,
    else a natural key"; this is that natural key. Deliberately readable, so a
    failed match is obvious in the table rather than silent.
    """
    day = kickoff.date().isoformat() if kickoff is not None else "unknown"
    return f"{season}:{day}:{_slugify(home)}:{_slugify(away)}"


def parse_football_data_csv(
    text: str,
    season: str,
    *,
    as_of: dt.datetime | None = None,
    devig_method: DevigMethod = "shin",
) -> pd.DataFrame:
    """Parse a football-data CSV into ``fact_odds`` rows.

    Emits three kinds of row, all as decimal prices so they share one column:

    * every quoted book price, ``bookmaker`` = the book (closing) or
      ``<book>#open`` (opening);
    * a de-vigged consensus, ``bookmaker`` = ``fair#<method>``, priced at
      ``1/p`` so a fair probability round-trips through the schema;
    * a derived clean sheet, ``bookmaker`` = ``derived#poisson``, flagged in the
      name because it is modelled rather than quoted.

    ``as_of`` defaults to each row's kickoff instant -- see the module docstring
    on why that is the leak-free choice for a retrospectively published file.
    Pass an explicit ``as_of`` only for forward-looking files (``fixtures.csv``).
    """
    df = pd.read_csv(io.StringIO(text))
    df.columns = [str(c).lstrip("﻿").strip() for c in df.columns]
    if "HomeTeam" not in df.columns:
        raise ValueError("not a football-data match CSV: no HomeTeam column")

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        home, away = r.get("HomeTeam"), r.get("AwayTeam")
        if not isinstance(home, str) or not isinstance(away, str):
            continue
        kickoff = _kickoff_utc(r.get("Date"), r.get("Time"))
        stamp = as_of if as_of is not None else kickoff
        if stamp is None:
            continue
        key = natural_fixture_key(season, kickoff, home, away)

        for closing in (True, False):
            c = "C" if closing else ""
            tag = "" if closing else "#open"

            # -- 1X2 -------------------------------------------------------
            for pfx, book in FD_BOOKS_1X2.items():
                cols = [f"{pfx}{c}H", f"{pfx}{c}D", f"{pfx}{c}A"]
                prices = _clean_prices(r, cols)
                if prices is None:
                    continue
                for sel, price in zip(("HOME", "DRAW", "AWAY"), prices):
                    rows.append(_row(key, f"{book}{tag}", MARKET_H2H, sel, price, stamp))

            # -- Over/Under 2.5 --------------------------------------------
            for pfx, book in FD_BOOKS_OU.items():
                cols = [f"{pfx}{c}>2.5", f"{pfx}{c}<2.5"]
                prices = _clean_prices(r, cols)
                if prices is None:
                    continue
                for sel, price in zip(("OVER_2.5", "UNDER_2.5"), prices):
                    rows.append(_row(key, f"{book}{tag}", MARKET_TOTALS, sel, price, stamp))

        # -- consensus fair probabilities + derived clean sheet ------------
        rows.extend(_derived_rows(r, key, stamp, devig_method))

    return pd.DataFrame(rows, columns=[
        "fixture_key", "bookmaker", "market", "selection", "price_decimal", "as_of",
    ])


def _clean_prices(row: pd.Series, cols: list[str]) -> list[float] | None:
    """Return the prices if every column is present and a sane decimal price."""
    out: list[float] = []
    for c in cols:
        if c not in row.index:
            return None
        v = row[c]
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(f) or f <= 1.0:
            return None
        out.append(f)
    return out


def _row(key: str, book: str, market: str, sel: str, price: float,
         as_of: dt.datetime) -> dict[str, Any]:
    return {
        "fixture_key": key, "bookmaker": book, "market": market,
        "selection": sel, "price_decimal": float(price), "as_of": as_of,
    }


def _derived_rows(row: pd.Series, key: str, as_of: dt.datetime,
                  method: DevigMethod) -> list[dict[str, Any]]:
    """De-vigged 1X2/totals consensus plus a modelled clean-sheet probability.

    Uses the market average (``Avg*``) where available and Bet365 otherwise,
    because the average across books is a better-calibrated consensus than any
    single one and is present for every season that has odds at all.
    """
    out: list[dict[str, Any]] = []
    h2h = _clean_prices(row, ["AvgCH", "AvgCD", "AvgCA"]) \
        or _clean_prices(row, ["AvgH", "AvgD", "AvgA"]) \
        or _clean_prices(row, ["B365CH", "B365CD", "B365CA"]) \
        or _clean_prices(row, ["B365H", "B365D", "B365A"])
    if h2h is None:
        return out

    p = devig(h2h, method)
    for sel, prob in zip(("HOME", "DRAW", "AWAY"), p):
        out.append(_row(key, f"fair#{method}", MARKET_H2H, sel, 1.0 / float(prob), as_of))

    ou = _clean_prices(row, ["AvgC>2.5", "AvgC<2.5"]) \
        or _clean_prices(row, ["Avg>2.5", "Avg<2.5"]) \
        or _clean_prices(row, ["B365C>2.5", "B365C<2.5"]) \
        or _clean_prices(row, ["B365>2.5", "B365<2.5"])
    p_over = None
    if ou is not None:
        po = devig(ou, method)
        for sel, prob in zip(("OVER_2.5", "UNDER_2.5"), po):
            out.append(_row(key, f"fair#{method}", MARKET_TOTALS, sel, 1.0 / float(prob), as_of))
        p_over = float(po[0])

    try:
        rates = fit_goal_rates(float(p[0]), float(p[1]), float(p[2]), p_over)
    except (ValueError, RuntimeError, np.linalg.LinAlgError):
        # A degenerate row (missing draw price, a book quoting 1.01/1.01) must
        # not abort a 380-fixture ingest. The quoted prices above are already
        # emitted; only the derived clean sheet is skipped.
        return out
    cs_home, cs_away = clean_sheet_probs(rates)
    for sel, prob in (("HOME", cs_home), ("AWAY", cs_away)):
        if 0.0 < prob < 1.0:
            out.append(_row(key, "derived#poisson", MARKET_CLEAN_SHEET, sel,
                            1.0 / prob, as_of))
    return out


def ingest_football_data(
    wh: Warehouse,
    season: str,
    *,
    fetcher: TextFetcher | None = None,
    devig_method: DevigMethod = "shin",
) -> dict[str, int]:
    """Fetch and land one completed season of English top-flight odds.

    Historical file: odds are stamped at kickoff, so a backtest reading through
    ``snapshot_at(deadline)`` cannot see them early.
    """
    owns = fetcher is None
    fetcher = fetcher or TextFetcher("odds_football_data", base_url=FOOTBALL_DATA_BASE)
    endpoint = f"mmz4281/{fd_season_code(season)}/E0.csv"
    try:
        got = fetcher.get_text(endpoint)
    finally:
        if owns:
            fetcher.close()

    wh.record_fetch(
        source="odds_football_data", endpoint=endpoint, params=None,
        fetched_at=got.fetched_at, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )
    df = parse_football_data_csv(got.body, season, devig_method=devig_method)
    return {"fact_odds": wh.append("fact_odds", df), "rows_parsed": len(df)}


def ingest_football_data_fixtures(
    wh: Warehouse,
    season: str,
    *,
    fetcher: TextFetcher | None = None,
    devig_method: DevigMethod = "shin",
) -> dict[str, int]:
    """Fetch forward-looking odds for fixtures not yet played.

    ``fixtures.csv`` covers every league football-data tracks, so it is filtered
    to ``Div == 'E0'``. Stamped at the fetch instant because these prices are
    observable now -- this is the only path whose rows may legitimately inform a
    decision at the upcoming deadline.

    Measured 2026-08-18: HTTP 200, 1,484 bytes, three rows, **none of them
    E0** -- football-data publishes a fixture only a day or two ahead, so
    Premier League rows are expected to appear on the 20th for a 21st kickoff.
    Returning zero rows here is a normal state, not a failure.
    """
    owns = fetcher is None
    fetcher = fetcher or TextFetcher("odds_football_data", base_url=FOOTBALL_DATA_BASE)
    try:
        got = fetcher.get_text("fixtures.csv")
    finally:
        if owns:
            fetcher.close()

    wh.record_fetch(
        source="odds_football_data", endpoint="fixtures.csv", params=None,
        fetched_at=got.fetched_at, sha256=got.sha256,
        body_path=str(got.body_path), http_status=got.http_status,
    )
    raw = pd.read_csv(io.StringIO(got.body))
    raw.columns = [str(c).lstrip("﻿").strip() for c in raw.columns]
    e0 = raw[raw.get("Div") == "E0"] if "Div" in raw.columns else raw.iloc[0:0]
    if e0.empty:
        return {"fact_odds": 0, "rows_parsed": 0, "e0_fixtures_available": 0}
    df = parse_football_data_csv(
        e0.to_csv(index=False), season, as_of=got.fetched_at, devig_method=devig_method
    )
    return {
        "fact_odds": wh.append("fact_odds", df),
        "rows_parsed": len(df),
        "e0_fixtures_available": len(e0),
    }


# --------------------------------------------------------------------------
# fixture matching
# --------------------------------------------------------------------------


def match_fixture_keys(wh: Warehouse, season: str, as_of: dt.datetime) -> pd.DataFrame:
    """Map natural odds keys onto ``season:fixture_id``.

    Returns the mapping rather than rewriting ``fact_odds`` in place: the
    warehouse is append-only by design, and a name-matching heuristic is
    exactly the kind of thing that should be re-derivable rather than baked
    into stored facts.
    """
    snap = wh.snapshot_at(as_of)
    fx = snap.table("fact_fixture", where="season = ?", params=[season])
    teams = snap.table("dim_team", where="season = ?", params=[season])
    if fx.empty or teams.empty:
        return pd.DataFrame(columns=["fixture_key", "matched_key", "fixture_id"])

    name_by_code = dict(zip(teams["team_code"], teams["name"]))
    odds_keys = wh.sql("SELECT DISTINCT fixture_key FROM fact_odds")["fixture_key"]

    canon = _slugify
    lookup: dict[tuple[str, str, str], int] = {}
    for _, f in fx.iterrows():
        ko = f["kickoff_utc"]
        day = ko.date().isoformat() if pd.notna(ko) else "unknown"
        h = canon(name_by_code.get(f["home_team_code"], ""))
        a = canon(name_by_code.get(f["away_team_code"], ""))
        lookup[(day, h, a)] = int(f["fixture_id"])

    alias = {canon(k): canon(v) for k, v in FD_TEAM_ALIASES.items()}
    out = []
    for key in odds_keys:
        parts = str(key).split(":")
        if len(parts) != 4 or parts[0] != season:
            continue
        _, day, h, a = parts
        h2, a2 = alias.get(h, h), alias.get(a, a)
        fid = lookup.get((day, h2, a2))
        out.append({"fixture_key": key, "fixture_id": fid,
                    "matched_key": f"{season}:{fid}" if fid is not None else None})
    return pd.DataFrame(out, columns=["fixture_key", "fixture_id", "matched_key"])


# --------------------------------------------------------------------------
# The Odds API (needs a key the account holder must obtain)
# --------------------------------------------------------------------------


class OddsApiError(RuntimeError):
    """The Odds API refused a request, or no key is configured."""


@dataclass(frozen=True, slots=True)
class OddsApiQuota:
    """Credit accounting read straight off the response headers."""

    remaining: int | None
    used: int | None
    last_cost: int | None

    @classmethod
    def from_headers(cls, h: dict[str, str]) -> OddsApiQuota:
        def g(k: str) -> int | None:
            v = h.get(k)
            return int(v) if v is not None and str(v).lstrip("-").isdigit() else None

        return cls(g("x-requests-remaining"), g("x-requests-used"), g("x-requests-last"))


def parse_odds_api_events(payload: list[dict[str, Any]], as_of: dt.datetime,
                          season: str) -> pd.DataFrame:
    """Flatten a v4 ``/odds`` or ``/events/{id}/odds`` body into ``fact_odds``.

    Handles both the featured markets (``h2h``, ``totals``) and the soccer
    player props (``player_goal_scorer_anytime``), which is the market that
    justifies paying for this source at all. Prices are requested in decimal
    format so no conversion happens here.
    """
    rows: list[dict[str, Any]] = []
    for ev in payload:
        ko = ev.get("commence_time")
        kickoff = dt.datetime.fromisoformat(str(ko).replace("Z", "+00:00")) if ko else None
        key = natural_fixture_key(season, kickoff, ev.get("home_team", ""), ev.get("away_team", ""))
        for bk in ev.get("bookmakers", []) or []:
            book = bk.get("key", "unknown")
            for mkt in bk.get("markets", []) or []:
                market = _ODDS_API_MARKETS.get(mkt.get("key"), mkt.get("key"))
                for oc in mkt.get("outcomes", []) or []:
                    price = oc.get("price")
                    if price is None or float(price) <= 1.0:
                        continue
                    sel = str(oc.get("description") or oc.get("name") or "")
                    if oc.get("description") and oc.get("name"):
                        sel = f"{oc['description']}|{oc['name']}"
                    if oc.get("point") is not None:
                        sel = f"{sel}_{oc['point']}"
                    rows.append(_row(key, book, str(market), sel, float(price), as_of))
    return pd.DataFrame(rows, columns=[
        "fixture_key", "bookmaker", "market", "selection", "price_decimal", "as_of",
    ])


_ODDS_API_MARKETS = {
    "h2h": MARKET_H2H,
    "totals": MARKET_TOTALS,
    "player_goal_scorer_anytime": MARKET_ANYTIME_SCORER,
}


class OddsApiClient:
    """Thin client for api.the-odds-api.com v4.

    Not exercised against the live service anywhere in this repo: creating the
    account that issues the key is the account holder's decision, not the
    engine's. Every request costs ``markets x regions`` credits against a
    500/month free allowance, so :meth:`anytime_scorers` deliberately fetches
    one event at a time and reports the quota headers back rather than looping
    blindly.
    """

    def __init__(self, api_key: str | None, *, fetcher: Fetcher | None = None) -> None:
        if not api_key:
            raise OddsApiError(
                "no Odds API key. Set ODDS_API_KEY; free tier is 500 credits/month "
                "from https://the-odds-api.com. Historical snapshots are paid-only."
            )
        self.api_key = api_key
        self._fetcher = fetcher or Fetcher("odds_api", base_url=ODDS_API_BASE)
        self.last_quota: OddsApiQuota | None = None

    def _get(self, endpoint: str, params: dict[str, Any]) -> Fetched:
        got = self._fetcher.get_json(endpoint, {**params, "apiKey": self.api_key})
        return got

    def events(self, sport: str = "soccer_epl") -> Fetched:
        """List upcoming events. Costs 0 credits."""
        return self._get(f"sports/{sport}/events", {})

    def featured_odds(self, sport: str = "soccer_epl", regions: str = "uk",
                      markets: str = "h2h,totals") -> Fetched:
        """h2h/totals for every upcoming event. Costs len(markets) x len(regions)."""
        return self._get(f"sports/{sport}/odds",
                         {"regions": regions, "markets": markets, "oddsFormat": "decimal"})

    def anytime_scorers(self, event_id: str, sport: str = "soccer_epl",
                        regions: str = "us") -> Fetched:
        """Anytime-scorer prices for one event.

        Soccer player props are documented as available for the EPL but with
        coverage "currently limited to US bookmakers", hence the ``us`` default
        region. One credit per call at one market/one region.
        """
        return self._get(
            f"sports/{sport}/events/{event_id}/odds",
            {"regions": regions, "markets": "player_goal_scorer_anytime",
             "oddsFormat": "decimal"},
        )

    def close(self) -> None:
        self._fetcher.close()
