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
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from fpl_edge.ingest.http import Fetched, Fetcher, _now, _slug
from fpl_edge.ingest.player_mapping import normalize_name
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

#: The Odds API free tier's monthly allowance. Measured, not documented: the
#: account's own ``x-requests-remaining`` + ``x-requests-used`` headers summed
#: to exactly this on 2026-08-28 (433 + 67). Written down here because every
#: cap in the repo is a fraction of it and a cap with no denominator beside it
#: is a number nobody can sanity-check.
FREE_TIER_MONTHLY_CREDITS = 500

#: How old a market's newest quote may be before a consumer must treat it as
#: stale. These are decision budgets, not vendor limits: they answer "would I
#: still act on this number at a deadline?".
#:
#: The featured markets (h2h, totals) and everything derived from them move
#: continuously and are cheap to refresh, so they get a day. The player props
#: are the expensive call and move more slowly in absolute terms -- a striker's
#: anytime price does not swing much until team news -- so they get two days,
#: which is still inside the T-48h rung of the refresh ladder.
#:
#: Nothing here deletes or hides a stale row. The contract is only that every
#: consumer can *ask*, via :func:`odds_freshness`, and therefore can never
#: present an eight-day-old price as if it were current.
MARKET_MAX_AGE_H: dict[str, float] = {
    MARKET_H2H: 24.0,
    MARKET_TOTALS: 24.0,
    MARKET_CLEAN_SHEET: 24.0,
    MARKET_ANYTIME_SCORER: 48.0,
    "correct_score": 48.0,
    "btts": 48.0,
    "team_totals": 48.0,
}

#: Fallback budget for a market not named above.
DEFAULT_MAX_AGE_H = 48.0

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
# The Odds API -- the only source that carries anytime scorer
# --------------------------------------------------------------------------


class OddsApiError(RuntimeError):
    """The Odds API refused a request, or no key is configured."""


#: Why a run was refused. The distinction is load-bearing, not cosmetic.
#:
#: ``month_exhausted`` and ``key_exhausted`` are *budgets working*: the month's
#: allowance is genuinely spent and next month (or a raised cap) fixes it.
#: ``impossible`` is a **misconfiguration**: the cap is smaller than what a
#: single run needs, so no amount of waiting will ever let the job succeed.
#:
#: That third case is the one that cost a deadline week of market data. On
#: 2026-08-28 the props ingest had been refusing every night since 2026-08-19
#: with ``cap=30`` against a run that needs 22 and a month that had already
#: spent 67 -- an arithmetic dead end that could not resolve itself, reported
#: as ``ok=True``, for nine days. A cap below one run's cost is not a budget;
#: it is an off switch nobody knows they flipped.
REFUSAL_IMPOSSIBLE = "impossible"
REFUSAL_MONTH_EXHAUSTED = "month_exhausted"
REFUSAL_KEY_EXHAUSTED = "key_exhausted"


class CreditBudgetExceeded(RuntimeError):
    """A planned run would breach the configured credit cap. Nothing was spent.

    ``kind`` is one of :data:`REFUSAL_IMPOSSIBLE`,
    :data:`REFUSAL_MONTH_EXHAUSTED`, :data:`REFUSAL_KEY_EXHAUSTED`. Callers
    must treat all three as a **failure** -- a run that fetched nothing did not
    succeed -- but only ``impossible`` names an operator error that will never
    clear on its own.
    """

    def __init__(self, message: str, *, kind: str = REFUSAL_MONTH_EXHAUSTED) -> None:
        super().__init__(message)
        self.kind = kind

    @property
    def impossible(self) -> bool:
        """Is this cap arithmetically unsatisfiable, rather than merely spent?"""
        return self.kind == REFUSAL_IMPOSSIBLE


#: The Odds API team names are the long forms; FPL uses its own short forms.
#: Explicit rather than fuzzy-matched: a wrong team mapping silently attributes
#: every one of that club's players to the wrong squad, and there are only 20
#: rows to write down. Verified against both name sets on 2026-08-18.
ODDS_API_TEAM_ALIASES: dict[str, str] = {
    "Brighton and Hove Albion": "Brighton",
    "Leeds United": "Leeds",
    "Manchester City": "Man City",
    "Manchester United": "Man Utd",
    "Newcastle United": "Newcastle",
    "Nottingham Forest": "Nott'm Forest",
    "Tottenham Hotspur": "Spurs",
    "Wolverhampton Wanderers": "Wolves",
    "West Ham United": "West Ham",
    "Sheffield United": "Sheffield Utd",
}


def resolve_team_name(api_name: str, fpl_names: set[str]) -> str:
    """Map an Odds API club name onto the FPL club name.

    Tries the explicit alias table, then an exact match, then a normalised
    match. Raises rather than guessing: an unresolved club means every player
    on it would be matched against the wrong squad.
    """
    if api_name in ODDS_API_TEAM_ALIASES:
        mapped = ODDS_API_TEAM_ALIASES[api_name]
        if mapped in fpl_names:
            return mapped
    if api_name in fpl_names:
        return api_name
    norm = {normalize_name(n): n for n in fpl_names}
    hit = norm.get(normalize_name(api_name))
    if hit:
        return hit
    raise OddsApiError(
        f"cannot map Odds API club {api_name!r} to an FPL club. "
        f"Add it to ODDS_API_TEAM_ALIASES. Known FPL clubs: {sorted(fpl_names)}"
    )


@dataclass(frozen=True, slots=True)
class OddsApiQuota:
    """Credit accounting read straight off the response headers.

    These headers are the authoritative record -- the vendor's own count, reset
    on the vendor's own schedule. We deliberately do *not* keep a local ledger
    that could drift out of sync with it.
    """

    remaining: int | None
    used: int | None
    last_cost: int | None

    @classmethod
    def from_headers(cls, h: dict[str, str]) -> OddsApiQuota:
        def g(k: str) -> int | None:
            v = h.get(k)
            return int(v) if v is not None and str(v).lstrip("-").isdigit() else None

        return cls(g("x-requests-remaining"), g("x-requests-used"), g("x-requests-last"))


@dataclass(frozen=True, slots=True)
class CreditPlan:
    """What a run intends to spend, checked before anything is spent."""

    events: int          # free
    featured: int        # markets x regions, one call for all events
    scorer: int          # markets x regions x events
    cap: int
    used_before: int | None
    remaining_before: int | None

    @property
    def total(self) -> int:
        return self.events + self.featured + self.scorer

    @property
    def impossible(self) -> bool:
        """True when the cap is below one run's cost, so no run can ever fit.

        Checked *independently of what has been used*, which is the whole
        point: ``used_before`` moves and this does not. A cap of 30 against a
        22-credit run looks survivable until you notice that ``used_before``
        only ever grows, so the very first run that pushes past 8 makes every
        subsequent run impossible forever.
        """
        return self.cap < self.total

    def check(self) -> None:
        """Refuse the run if it would breach the cap or the remaining balance.

        Order matters. The *impossible* cap is tested first and reported first,
        because when a cap is set below one run's cost the "already used N this
        month" clause is a red herring: it says the month is spent when the
        truth is that the cap can never be met, in this month or any other.
        Reading the wrong one of those two messages is what let this refuse
        silently from 2026-08-19 to 2026-08-28.
        """
        if self.impossible:
            raise CreditBudgetExceeded(
                f"MISCONFIGURED CAP: one run of this ingest costs {self.total} "
                f"credits ({self.featured} featured + {self.scorer} scorer cards) "
                f"but the configured monthly cap is {self.cap}. No run can ever "
                f"fit inside this cap -- not this month, not next month. This is "
                f"an operator error, not a spent budget. Raise the cap to at "
                f"least {self.total} (the free tier allows "
                f"{FREE_TIER_MONTHLY_CREDITS}/month; this repo's default is "
                f"{OddsApiClient.DEFAULT_MONTHLY_CAP}) or narrow the run. "
                f"Nothing was spent.",
                kind=REFUSAL_IMPOSSIBLE,
            )
        if self.remaining_before is not None and self.total > self.remaining_before:
            raise CreditBudgetExceeded(
                f"run needs {self.total} credits but only {self.remaining_before} "
                "remain on the key this month. Nothing was spent.",
                kind=REFUSAL_KEY_EXHAUSTED,
            )
        if self.used_before is not None and self.used_before + self.total > self.cap:
            raise CreditBudgetExceeded(
                f"run needs {self.total} credits; {self.used_before} already used "
                f"this month and the configured cap is {self.cap}. "
                f"Raise max_monthly_credits to proceed. Nothing was spent.",
                kind=REFUSAL_MONTH_EXHAUSTED,
            )


class OddsApiFetcher(Fetcher):
    """:class:`Fetcher` that keeps the last response headers.

    The quota counters only exist as headers, and ``Fetcher.get_json`` discards
    the response object. Subclassed rather than changing ``http.py``.
    """

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.last_headers: dict[str, str] = {}

    def _get(self, url: str, params: dict[str, Any] | None) -> Any:
        resp = super()._get(url, params)
        self.last_headers = {k.lower(): v for k, v in resp.headers.items()}
        return resp


class OddsApiClient:
    """Client for api.the-odds-api.com v4, with the free tier's budget enforced.

    Credit costs, confirmed against live ``x-requests-last`` headers on
    2026-08-18 rather than taken from the docs:

    =========================================  ======
    ``GET /v4/sports/{sport}/events``          **0**
    ``GET /v4/sports/{sport}/odds``            markets x regions (2 observed)
    ``GET /v4/sports/{sport}/events/{id}/odds``  markets x regions (1 observed)
    =========================================  ======

    Because ``/events`` is free *and* returns the quota headers, the remaining
    balance can be read without spending anything. Every run therefore checks
    its budget against the vendor's own counter before it spends a credit.
    """

    #: Default ceiling on credits used per calendar month, as a fraction of the
    #: measured :data:`FREE_TIER_MONTHLY_CREDITS`.
    #:
    #: The budget this has to cover, arithmetic first (see
    #: ``docs/data_sources.md`` §2.4 for the same table):
    #:
    #: * one refresh = 2 (featured h2h+totals, uk) + 1 per fixture priced.
    #:   Restricted to the fixtures before the next deadline that is **12** for
    #:   a 10-fixture gameweek, not the 22 an unfiltered ``/events`` list costs.
    #: * the pre-deadline ladder fires 3 times a gameweek (T-36h, T-12h, T-3h)
    #:   = 36 credits.
    #: * the nightly job tops up only when nothing has been fetched for 48h,
    #:   which outside a deadline window is about twice a week = 24 credits.
    #: * so ~60 credits a gameweek, ~258 in a 4.3-gameweek month, plus the
    #:   150-credit ceiling the extra-markets expansion enforces on itself
    #:   (:data:`~fpl_edge.ingest.odds_markets.EXPANSION_MONTHLY_CAP`).
    #:
    #: 258 + 150 = 408, so 400 is the right ceiling for the props path only if
    #: the two are read as sharing one 500-credit key -- which they do, because
    #: ``x-requests-used`` counts every consumer of it. The 100 credits between
    #: this cap and the free tier are the manual-trigger and re-run reserve.
    #:
    #: **This number must never be set below one run's cost.** A cap of 30 was
    #: passed on the command line in ``post_gw.py`` and refused every nightly
    #: run for nine days while the job reported success;
    #: :meth:`CreditPlan.impossible` now names that case explicitly rather than
    #: letting it hide behind "already used N this month".
    DEFAULT_MONTHLY_CAP = 400

    #: A cap below this cannot price even a single small gameweek and is
    #: almost certainly a typo. Used by the CLI to refuse the *flag* rather
    #: than waiting for the run to refuse itself.
    MIN_SANE_MONTHLY_CAP = 60

    def __init__(
        self,
        api_key: str | None,
        *,
        fetcher: OddsApiFetcher | None = None,
        max_monthly_credits: int = DEFAULT_MONTHLY_CAP,
    ) -> None:
        if not api_key:
            raise OddsApiError(
                "no Odds API key. Set ODDS_API_KEY in .env (gitignored). "
                "The free tier gives 500 credits/month at https://the-odds-api.com."
            )
        self.api_key = api_key
        self.max_monthly_credits = max_monthly_credits
        self._fetcher = fetcher or OddsApiFetcher("odds_api", base_url=ODDS_API_BASE)
        self.quota: OddsApiQuota | None = None
        self.spent_this_run = 0

    # -- plumbing ----------------------------------------------------------

    def _get(self, endpoint: str, params: dict[str, Any]) -> Fetched:
        got = self._fetcher.get_json(endpoint, {**params, "apiKey": self.api_key})
        self.quota = OddsApiQuota.from_headers(self._fetcher.last_headers)
        if self.quota.last_cost:
            self.spent_this_run += self.quota.last_cost
        return got

    # -- endpoints ---------------------------------------------------------

    def events(self, sport: str = "soccer_epl") -> Fetched:
        """List upcoming events. Costs 0 credits, and refreshes the quota."""
        return self._get(f"sports/{sport}/events", {})

    def featured_odds(self, sport: str = "soccer_epl", regions: str = "uk",
                      markets: str = "h2h,totals") -> Fetched:
        """h2h/totals for every upcoming event in one call."""
        return self._get(f"sports/{sport}/odds",
                         {"regions": regions, "markets": markets, "oddsFormat": "decimal"})

    def anytime_scorers(self, event_id: str, sport: str = "soccer_epl",
                        regions: str = "uk") -> Fetched:
        """Anytime-scorer prices for one event. Player props are per-event only."""
        return self._get(
            f"sports/{sport}/events/{event_id}/odds",
            {"regions": regions, "markets": "player_goal_scorer_anytime",
             "oddsFormat": "decimal"},
        )

    # -- budgeting ---------------------------------------------------------

    def plan_run(self, n_events: int, *, featured_markets: int = 2,
                 regions: int = 1) -> CreditPlan:
        """Price a run *before* spending, using the free events call for balance."""
        self.events()  # 0 credits, populates self.quota
        q = self.quota or OddsApiQuota(None, None, None)
        return CreditPlan(
            events=0,
            featured=featured_markets * regions,
            scorer=n_events * regions,
            cap=self.max_monthly_credits,
            used_before=q.used,
            remaining_before=q.remaining,
        )

    def close(self) -> None:
        self._fetcher.close()

    def __enter__(self) -> "OddsApiClient":  # noqa: PYI034
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# --------------------------------------------------------------------------
# de-vigging a one-sided, non-exclusive market
# --------------------------------------------------------------------------


def devig_anytime_scorer(
    decimal_odds: list[float] | np.ndarray,
    team_expected_goals: float,
    *,
    coverage: float = 0.95,
    method: Literal["power", "uniform"] = "power",
) -> np.ndarray:
    """Estimate fair anytime-scorer probabilities for one team's card.

    **This is an estimate, not an exact de-vig, and the distinction is real.**

    Anytime scorer is a set of *independent* yes/no bets, not a mutually
    exclusive book: eleven players can all score in one match. Their implied
    probabilities therefore do not sum to 1 and normalising them to 1 would be
    nonsense. Measured on the real card for Arsenal v Coventry (2026-08-21),
    17 selections summed to **4.748**.

    Worse, the UK books quote only the **Yes** side -- every outcome came back
    with ``name == "Yes"`` and no matching ``No``. With one side of a two-way
    market missing there is no overround to measure, so the margin has to be
    *estimated* against an external constraint rather than removed exactly.

    The constraint used here comes from the match totals market. If player *i*
    scores ``Poisson(lambda_i)`` goals then ``p_i = 1 - exp(-lambda_i)`` and,
    critically, ``sum(lambda_i) = E[team goals]`` -- goal rates are additive
    where probabilities are not. So we solve for the transform that makes the
    card's implied rates add up to the team's de-vigged expected goals.

    ``method="power"`` (default) solves ``p_i = q_i ** k``. ``method="uniform"``
    scales every implied rate by one constant. Power is the default because
    bookmakers load margin onto longshots far more heavily than onto favourites,
    and a uniform scale therefore over-shrinks the favourite. On the real
    Arsenal card, anchored to 2.998 expected goals:

    ==================  ======  =========  =========
    Selection           Quoted  Uniform    Power
    ==================  ======  =========  =========
    Gyokeres (1.78)     0.5629  0.3431     0.4121
    Odegaard (3.80)     0.2632  0.1437     0.1276
    Mosquera (12.00)    0.0833  0.0432     0.0216
    ==================  ======  =========  =========

    ``coverage`` is the share of the team's goals attributable to the listed
    players -- own goals and unlisted deep substitutes make up the remainder.
    0.95 is a stated assumption, not a measurement.

    **Treat the output as uncertain.** The shrink is large (the quoted card
    implies roughly twice the team's expected goals) and cannot be validated
    against realised results until matches are played. The raw quoted prices are
    always written to ``fact_odds`` alongside these, so nothing is lost if the
    estimate later proves badly calibrated.
    """
    q = np.asarray([implied_prob(float(o)) for o in decimal_odds], dtype=float)
    if team_expected_goals <= 0:
        raise ValueError("team_expected_goals must be positive")
    if not 0.0 < coverage <= 1.0:
        raise ValueError("coverage must be in (0, 1]")
    target = team_expected_goals * coverage

    def rate_sum(p: np.ndarray) -> float:
        return float((-np.log(1.0 - np.clip(p, 1e-12, 1 - 1e-12))).sum())

    if rate_sum(q) <= target:
        # The card already implies no more goals than the market does. Nothing
        # to remove; inflating it would be inventing value.
        return q

    if method == "uniform":
        lam_hat = -np.log(1.0 - np.clip(q, 1e-12, 1 - 1e-12))
        return 1.0 - np.exp(-lam_hat * (target / lam_hat.sum()))
    if method != "power":
        raise ValueError(f"unknown method {method!r}; use 'power' or 'uniform'")

    hi = 2.0
    while rate_sum(q**hi) > target and hi < 64.0:
        hi *= 2.0
    k = brentq(lambda k: rate_sum(q**k) - target, 1.0, hi, xtol=1e-12)
    return q**k


# --------------------------------------------------------------------------
# parsing The Odds API
# --------------------------------------------------------------------------

_ODDS_API_MARKETS = {
    "h2h": MARKET_H2H,
    "totals": MARKET_TOTALS,
    "player_goal_scorer_anytime": MARKET_ANYTIME_SCORER,
}

#: Exchange lay prices are a different market and must not be averaged in with
#: back prices; ``h2h_lay`` arrives unrequested alongside ``h2h``.
_ODDS_API_SKIP_MARKETS = {"h2h_lay", "outrights_lay"}


def parse_odds_api_events(
    payload: list[dict[str, Any]] | dict[str, Any],
    as_of: dt.datetime,
    season: str,
) -> pd.DataFrame:
    """Flatten a v4 ``/odds`` or ``/events/{id}/odds`` body into ``fact_odds`` rows.

    The trap this function exists to avoid: for player props the player's name
    is in ``outcome["description"]`` and ``outcome["name"]`` is the literal
    string ``"Yes"``. Keying the selection on ``name`` yields seventeen
    identical rows called "Yes" that all collide on the primary key, so sixteen
    of them vanish silently and the seventeenth is attributed to nobody.
    Verified against the live payload: every outcome on all three UK books had
    ``name == "Yes"``.

    Selections are therefore:

    * player props -- the player name from ``description``;
    * totals -- ``OVER_2.5`` / ``UNDER_2.5`` from ``name`` plus ``point``;
    * h2h -- ``HOME`` / ``DRAW`` / ``AWAY``, resolved against the event's own
      team names rather than left as raw club names, so the column means the
      same thing as it does for football-data.
    """
    events = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for ev in events:
        ko = ev.get("commence_time")
        kickoff = dt.datetime.fromisoformat(str(ko).replace("Z", "+00:00")) if ko else None
        home, away = ev.get("home_team", ""), ev.get("away_team", "")
        key = natural_fixture_key(season, kickoff, home, away)
        for bk in ev.get("bookmakers", []) or []:
            book = bk.get("key", "unknown")
            for mkt in bk.get("markets", []) or []:
                mkey = mkt.get("key")
                if mkey in _ODDS_API_SKIP_MARKETS:
                    continue
                market = _ODDS_API_MARKETS.get(mkey, mkey)
                for oc in mkt.get("outcomes", []) or []:
                    price = oc.get("price")
                    if price is None or float(price) <= 1.0:
                        continue
                    sel = _odds_api_selection(mkey, oc, home, away)
                    if sel is None:
                        continue
                    rows.append(_row(key, book, str(market), sel, float(price), as_of))
    return pd.DataFrame(rows, columns=[
        "fixture_key", "bookmaker", "market", "selection", "price_decimal", "as_of",
    ])


def _odds_api_selection(mkey: str | None, oc: dict[str, Any],
                        home: str, away: str) -> str | None:
    name = oc.get("name")
    desc = oc.get("description")
    if mkey == "player_goal_scorer_anytime":
        # "Yes" is not a selection; the player is.
        if not desc:
            return None
        return str(desc)
    if mkey == "totals":
        point = oc.get("point")
        return f"{str(name).upper()}_{point}" if point is not None else None
    if mkey == "h2h":
        if name == "Draw":
            return "DRAW"
        if name == home:
            return "HOME"
        if name == away:
            return "AWAY"
        return None
    return str(desc or name or "") or None


# --------------------------------------------------------------------------
# resolving bookmaker player names to FPL codes
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NameMatch:
    """One resolution attempt, successful or not. Never a guess."""

    api_name: str
    code: int | None
    web_name: str | None
    rule: str


#: Latin letters that carry a stroke or ligature rather than a combining mark.
#: ``unicodedata.normalize("NFKD", ...)`` cannot decompose these -- there is no
#: base letter plus accent to separate -- so ``player_mapping.normalize_name``
#: strips them as punctuation instead. That turns "Ødegaard" into "degaard" and
#: the bookmaker's "Odegaard" then fails to match. Folded here, before
#: normalising, rather than by editing the shared normaliser.
_LATIN_FOLD = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "œ": "oe", "Œ": "oe",
    "ß": "ss", "đ": "d", "Đ": "d", "ð": "d", "Ð": "d", "ł": "l", "Ł": "l",
    "þ": "th", "Þ": "th", "ı": "i", "ħ": "h", "ŧ": "t", "ŉ": "n",
})


def fold_name(value: object) -> str:
    """Normalise a name for comparison, folding stroked letters first.

    Verified on the real GW1 cards: without the fold, "Martin Odegaard" from
    the bookmaker and "Martin Ødegaard" from FPL do not match.
    """
    return normalize_name(str(value).translate(_LATIN_FOLD))


def _fpl_name_keys(row: pd.Series) -> tuple[str, set[str]]:
    full = fold_name(f"{row.get('first_name') or ''} {row.get('second_name') or ''}")
    return full, set(full.split())


def match_player_names(
    api_names: list[str], squad: pd.DataFrame
) -> list[NameMatch]:
    """Resolve bookmaker player names against an FPL squad.

    ``squad`` must already be narrowed to the clubs in the fixture -- that
    constraint does most of the work, because it removes the possibility of
    matching a common surname onto the wrong club.

    Rules are tried in order and each must be *unique* among the candidates. An
    ambiguous match is returned as unmatched with ``rule="ambiguous"`` rather
    than resolved by picking one, on the same principle as
    ``player_mapping.PlayerCodeIndex``: two Ben Davieses is a reason to stop,
    not a reason to choose.

    The rules, and the real cases from GW1 that motivate each:

    1. ``exact_full`` -- "Martin Zubimendi Ibanez" == first+second once
       diacritics are folded ("Martín" + "Zubimendi Ibáñez").
    2. ``exact_web`` -- the bookmaker used FPL's short name.
    3. ``api_subset`` -- API tokens are a subset of the FPL tokens.
       "Gabriel Martinelli" ⊂ "Gabriel Martinelli Silva".
    4. ``fpl_subset`` -- the reverse, for when the bookmaker is more verbose.
       "Magalhaes Gabriel" against FPL "Gabriel dos Santos Magalhães" resolves
       here because token order is ignored.
    5. ``surname_initial`` -- last token plus first initial, the last resort.
    """
    out: list[NameMatch] = []
    if squad.empty:
        return [NameMatch(n, None, None, "no_squad") for n in api_names]

    cand = []
    for _, r in squad.iterrows():
        full, toks = _fpl_name_keys(r)
        cand.append({
            "code": int(r["code"]), "web": str(r["web_name"]),
            "web_norm": fold_name(r["web_name"]), "full": full, "toks": toks,
            "surname": full.split()[-1] if full else "",
        })

    for name in api_names:
        norm = fold_name(name)
        toks = set(norm.split())
        hit: list[dict[str, Any]] = []
        rule = "unmatched"

        for label in ("exact_full", "exact_web", "api_subset",
                      "fpl_subset", "surname_initial"):
            hit = [c for c in cand if _name_rule(label, norm, toks, c)]
            if hit:
                rule = label
                break

        if len(hit) == 1:
            out.append(NameMatch(name, hit[0]["code"], hit[0]["web"], rule))
        elif len(hit) > 1:
            out.append(NameMatch(name, None, None, "ambiguous"))
        else:
            out.append(NameMatch(name, None, None, "unmatched"))
    return out


def _name_rule(rule: str, norm: str, toks: set[str], c: dict[str, Any]) -> bool:
    """Whether one candidate satisfies one matching rule. See match_player_names."""
    if rule == "exact_full":
        return bool(c["full"]) and c["full"] == norm
    if rule == "exact_web":
        return bool(c["web_norm"]) and c["web_norm"] == norm
    if rule == "api_subset":
        return bool(toks) and toks <= c["toks"]
    if rule == "fpl_subset":
        return bool(c["toks"]) and c["toks"] <= toks
    if rule == "surname_initial":
        return _surname_initial(toks, c["toks"], c["surname"])
    raise ValueError(f"unknown name rule {rule!r}")


def _surname_initial(api_toks: set[str], fpl_toks: set[str], surname: str) -> bool:
    """Share the FPL surname and agree on some initial.

    Deliberately anchored on the *last* FPL token rather than any shared token.
    "Martin Odegaard" and "Martin Zubimendi Ibanez" share the token "martin",
    and without the surname anchor that collision made Odegaard ambiguous
    against every other Martin in the fixture.
    """
    if not surname or len(surname) <= 3 or surname not in api_toks:
        return False
    return bool({t[0] for t in api_toks} & {t[0] for t in fpl_toks})


def squad_for_fixture(
    wh: Warehouse, season: str, as_of: dt.datetime, clubs: list[str]
) -> pd.DataFrame:
    """The players of the named FPL clubs, as known at ``as_of``."""
    snap = wh.snapshot_at(as_of)
    players = snap.table("dim_player", where="season = ?", params=[season])
    teams = snap.table("dim_team", where="season = ?", params=[season])
    if players.empty or teams.empty:
        return players.iloc[0:0]
    codes = set(teams[teams["name"].isin(clubs)]["team_code"])
    return players[players["team_code"].isin(codes)].copy()


# --------------------------------------------------------------------------
# freshness: the one question every consumer of fact_odds must be able to ask
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarketFreshness:
    """How old one market's newest quote is, and whether that is acceptable.

    Deliberately carries the *inputs* to the verdict (``last_as_of``,
    ``age_hours``, ``max_age_hours``) and not only the verdict, so a caller can
    render "anytime_scorer: 206h old, budget 48h" rather than a bare red dot.
    A consumer that can only see a boolean cannot explain itself to the owner.
    """

    market: str
    last_as_of: dt.datetime | None
    age_hours: float | None
    max_age_hours: float
    rows: int
    fixtures: int

    @property
    def present(self) -> bool:
        return self.last_as_of is not None

    @property
    def stale(self) -> bool:
        """Absent counts as stale. A market with no rows at all is not fresh."""
        return self.age_hours is None or self.age_hours > self.max_age_hours

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "last_as_of": self.last_as_of.isoformat() if self.last_as_of else None,
            "age_hours": (None if self.age_hours is None
                          else round(float(self.age_hours), 2)),
            "max_age_hours": float(self.max_age_hours),
            "stale": self.stale,
            "present": self.present,
            "rows": int(self.rows),
            "fixtures": int(self.fixtures),
        }


def odds_freshness(
    wh: Warehouse,
    *,
    season: str | None = None,
    now: dt.datetime | None = None,
    markets: Iterable[str] | None = None,
) -> list[MarketFreshness]:
    """Per-market age of ``fact_odds``, newest quote first.

    Every market named in :data:`MARKET_MAX_AGE_H` is returned even when it has
    no rows at all, because the failure this exists to catch is a market that
    stopped arriving -- and a market that stopped arriving is invisible if the
    report is built by grouping over the rows that are there. That is the same
    "something absent cannot be noticed unless it was expected" rule the crawl's
    stage table is built on.

    Read-only and cheap: one aggregate over ``fact_odds``. It deliberately does
    NOT go through ``snapshot_at`` -- the question is "how old is the newest
    thing we hold", which a point-in-time view would answer with the age at the
    snapshot instant instead.
    """
    now = (now or dt.datetime.now(dt.UTC)).astimezone(dt.UTC)
    wanted = list(markets) if markets is not None else list(MARKET_MAX_AGE_H)

    sql = ("SELECT market, max(as_of) AS last_as_of, count(*) AS n, "
           "count(DISTINCT fixture_key) AS fixtures FROM fact_odds")
    params: list[Any] = []
    if season:
        sql += " WHERE fixture_key LIKE ?"
        params.append(f"{season}:%")
    sql += " GROUP BY market"

    try:
        got = wh.sql(sql, params)
    except Exception:  # noqa: BLE001 - a missing table is "nothing is fresh"
        got = pd.DataFrame(columns=["market", "last_as_of", "n", "fixtures"])

    by_market = {str(r["market"]): r for _, r in got.iterrows()}
    out: list[MarketFreshness] = []
    for market in sorted(set(wanted) | set(by_market)):
        row = by_market.get(market)
        last = None
        if row is not None and row["last_as_of"] is not None:
            last = pd.Timestamp(row["last_as_of"]).to_pydatetime()
            if last.tzinfo is None:
                last = last.replace(tzinfo=dt.UTC)
            last = last.astimezone(dt.UTC)
        out.append(MarketFreshness(
            market=market,
            last_as_of=last,
            age_hours=None if last is None else (now - last).total_seconds() / 3600.0,
            max_age_hours=MARKET_MAX_AGE_H.get(market, DEFAULT_MAX_AGE_H),
            rows=int(row["n"]) if row is not None else 0,
            fixtures=int(row["fixtures"]) if row is not None else 0,
        ))
    return sorted(out, key=lambda f: (f.age_hours is None, -(f.age_hours or 0.0)))


def freshness_summary(rows: list[MarketFreshness]) -> dict[str, Any]:
    """One JSON blob for an API response or a digest line.

    ``ok`` is false when ANY market is stale. There is no partial credit here
    on purpose: the consumer asking this question is about to make a transfer
    decision, and "three of seven markets are current" is not a green light.
    """
    stale = [f.market for f in rows if f.stale]
    oldest = rows[0] if rows else None
    return {
        "ok": not stale,
        "stale_markets": stale,
        "oldest_market": oldest.market if oldest else None,
        "oldest_age_hours": (None if oldest is None or oldest.age_hours is None
                             else round(float(oldest.age_hours), 2)),
        "markets": [f.to_dict() for f in rows],
    }


# --------------------------------------------------------------------------
# live ingestion for the upcoming gameweek
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScorerIngestReport:
    """Everything a run should be judged on, including what it failed to match."""

    events: int
    credits_spent: int
    credits_remaining: int | None
    credits_used_month: int | None
    rows_written: int
    scorer_selections: int
    matched: int
    unmatched: list[NameMatch]
    clean_sheets_written: int

    @property
    def match_rate(self) -> float:
        return self.matched / self.scorer_selections if self.scorer_selections else 0.0


def _consensus(prices_by_sel: dict[str, list[float]]) -> dict[str, float]:
    """Mean decimal price per selection across books."""
    return {k: float(np.mean(v)) for k, v in prices_by_sel.items() if v}


def _fixture_goal_rates(featured: pd.DataFrame, key: str) -> GoalRates | None:
    """De-vigged Poisson rates for one fixture from its h2h (+ totals) rows."""
    g = featured[featured["fixture_key"] == key]
    h2h = g[g["market"] == MARKET_H2H]
    if h2h.empty:
        return None
    by_sel: dict[str, list[float]] = {}
    for _, r in h2h.iterrows():
        by_sel.setdefault(r["selection"], []).append(r["price_decimal"])
    avg = _consensus(by_sel)
    if not {"HOME", "DRAW", "AWAY"} <= set(avg):
        return None
    p = devig_shin([avg["HOME"], avg["DRAW"], avg["AWAY"]])

    tot = g[(g["market"] == MARKET_TOTALS) & (g["selection"].str.endswith("_2.5"))]
    p_over = None
    if not tot.empty:
        tb: dict[str, list[float]] = {}
        for _, r in tot.iterrows():
            tb.setdefault(r["selection"], []).append(r["price_decimal"])
        ta = _consensus(tb)
        if "OVER_2.5" in ta and "UNDER_2.5" in ta:
            p_over = float(devig_shin([ta["OVER_2.5"], ta["UNDER_2.5"]])[0])
    try:
        return fit_goal_rates(float(p[0]), float(p[1]), float(p[2]), p_over)
    except (ValueError, RuntimeError, np.linalg.LinAlgError):
        return None


def commence_utc(event: dict[str, Any]) -> dt.datetime | None:
    """Kickoff instant of one ``/events`` row, or None if unparseable."""
    raw = event.get("commence_time")
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))  # noqa: FURB162
    except ValueError:
        return None


def events_within(
    events: list[dict[str, Any]], horizon: dt.datetime | None
) -> list[dict[str, Any]]:
    """The events kicking off at or before ``horizon``. Identity when None.

    This is a *credit* decision, not a cosmetic one. ``/events`` returns every
    upcoming EPL fixture the vendor knows about -- 20 on 2026-08-28, i.e. two
    gameweeks -- and the scorer card costs one credit per event. Pricing the
    gameweek after next at every rung of the refresh ladder doubles the bill
    for cards that will be re-fetched three more times before they matter.

    Measured 2026-08-28: unfiltered the run planned 22 credits (2 featured +
    20 scorer); filtered to the fixtures before the *next* deadline it plans
    **12** (2 + 10). That is the number ``docs/data_sources.md`` has claimed
    since 2026-08-18, and it was only ever true for a one-gameweek horizon.

    An event with no parseable ``commence_time`` is KEPT. Dropping it would be
    a silent narrowing of the run driven by a vendor formatting change, and a
    fixture we fail to price is worse than a credit we did not need to spend.
    """
    if horizon is None:
        return list(events)
    horizon = horizon.astimezone(dt.UTC)
    out = []
    for e in events:
        ko = commence_utc(e)
        if ko is None or ko <= horizon:
            out.append(e)
    return out


@dataclass(frozen=True, slots=True)
class OddsApiFetch:
    """Everything one refresh pulled off the network, before any lock is taken.

    Exists so the two halves of a refresh can be separated in time. DuckDB
    permits a single writer and this repo has several ingests that can run at
    once; a refresh that held the write lock for the thirty-odd seconds it
    spends waiting on twelve HTTP round trips would block every other job for
    no reason, and would do it at the worst possible moment -- the hour before
    a deadline, when the DAG, the solver and the Telegram bot all want it.

    The crawl reached the same conclusion for the same reason (see
    ``fpl_edge.ingest.rivals.crawl.run``: "Fetch everything BEFORE opening the
    warehouse"). This is that rule applied to odds.
    """

    events: list[dict[str, Any]]
    as_of: dt.datetime
    #: ``(endpoint, params, Fetched)`` for every body, to be recorded in
    #: ``raw_fetch``. The endpoint travels WITH the response because
    #: :class:`~fpl_edge.ingest.http.Fetched` does not carry its own URL,
    #: and an audit row that has to guess where a body came from is not an
    #: audit row.
    fetched: list[tuple[str, str | None, Fetched]]
    featured_body: Any
    featured_fetched: Fetched
    payloads: dict[str, Any]         # event id -> scorer card body
    credits_spent: int
    credits_remaining: int | None
    credits_used_month: int | None
    plan: CreditPlan


def fetch_odds_api_gameweek(
    client: OddsApiClient,
    *,
    regions: str = "uk",
    max_monthly_credits: int = OddsApiClient.DEFAULT_MONTHLY_CAP,
    horizon: dt.datetime | None = None,
) -> OddsApiFetch:
    """The network half of a refresh. Takes no warehouse and no lock.

    Order of operations is chosen so that nothing is spent until the budget is
    known to be sufficient:

    1. ``/events`` -- **free**, and returns the quota headers. This is both the
       fixture list and the balance check.
    2. :meth:`CreditPlan.check` -- refuses the whole run if the cap is
       unsatisfiable, if the month's allowance is spent, or if the key's
       remaining balance is too small. No partial spend, ever.
    3. ``/odds`` for h2h + totals -- one call for all events.
    4. ``/events/{id}/odds`` for anytime scorer -- one call per fixture inside
       the horizon.

    Raises :class:`CreditBudgetExceeded` at step 2. A caller must treat that as
    a **failed** refresh: it fetched nothing.
    """
    ev = client.events()
    events = events_within(ev.body, horizon)
    plan = CreditPlan(
        events=0, featured=2, scorer=len(events),
        cap=max_monthly_credits,
        used_before=client.quota.used if client.quota else None,
        remaining_before=client.quota.remaining if client.quota else None,
    )
    plan.check()  # raises CreditBudgetExceeded before anything is spent

    fetched: list[tuple[str, str | None, Fetched]] = [("events", None, ev)]
    feat = client.featured_odds(regions=regions)
    fetched.append(("odds", f"regions={regions}", feat))

    payloads: dict[str, Any] = {}
    for e in events:
        got = client.anytime_scorers(e["id"], regions=regions)
        fetched.append((f"events/{e['id']}/odds", f"regions={regions}", got))
        payloads[e["id"]] = got.body

    return OddsApiFetch(
        events=events, as_of=ev.fetched_at, fetched=fetched,
        featured_body=feat.body, featured_fetched=feat, payloads=payloads,
        credits_spent=client.spent_this_run,
        credits_remaining=client.quota.remaining if client.quota else None,
        credits_used_month=client.quota.used if client.quota else None,
        plan=plan,
    )


def land_odds_api_gameweek(
    wh: Warehouse,
    season: str,
    got: OddsApiFetch,
    *,
    regions: str = "uk",
    scorer_coverage: float = 0.95,
) -> ScorerIngestReport:
    """The warehouse half of a refresh. Takes no network and holds the lock briefly.

    Writes four kinds of row to ``fact_odds``:

    * quoted h2h / totals / anytime scorer, one per bookmaker;
    * ``fair#shin`` de-vigged h2h and totals consensus;
    * ``derived#poisson`` clean sheets;
    * ``fair#scorer_power`` anytime-scorer estimates, keyed on the FPL
      ``code`` so the points model can join them, and written **only** for
      players that resolved unambiguously.

    ``as_of`` is the fetch instant for every row: these prices are observable
    now, which is what makes them legitimate input to the upcoming deadline.
    """
    as_of = got.as_of
    for endpoint, params, f in got.fetched:
        wh.record_fetch(source="odds_api", endpoint=endpoint, params=params,
                        fetched_at=f.fetched_at, sha256=f.sha256,
                        body_path=str(f.body_path), http_status=f.http_status)

    featured = parse_odds_api_events(
        got.featured_body, got.featured_fetched.fetched_at, season)
    scorer_frames = [
        parse_odds_api_events(body, as_of, season) for body in got.payloads.values()
    ]
    scorer = (pd.concat(scorer_frames, ignore_index=True)
              if scorer_frames else pd.DataFrame())

    quoted = pd.concat([featured, scorer], ignore_index=True)
    derived, matches = _derive_live_rows(
        wh, season, as_of, got.events, featured, got.payloads, scorer_coverage
    )

    all_rows = (pd.concat([quoted, derived], ignore_index=True)
                if len(derived) else quoted)
    written = wh.append("fact_odds", all_rows) if len(all_rows) else 0

    return ScorerIngestReport(
        events=len(got.events),
        credits_spent=got.credits_spent,
        credits_remaining=got.credits_remaining,
        credits_used_month=got.credits_used_month,
        rows_written=written,
        scorer_selections=len(matches),
        matched=sum(1 for m in matches if m.code is not None),
        unmatched=[m for m in matches if m.code is None],
        clean_sheets_written=int((derived["market"] == MARKET_CLEAN_SHEET).sum())
        if len(derived) else 0,
    )


def ingest_odds_api_gameweek(
    wh: Warehouse,
    season: str,
    *,
    api_key: str,
    client: OddsApiClient | None = None,
    regions: str = "uk",
    max_monthly_credits: int = OddsApiClient.DEFAULT_MONTHLY_CAP,
    scorer_coverage: float = 0.95,
    dry_run: bool = False,
    horizon: dt.datetime | None = None,
) -> ScorerIngestReport:
    """Fetch and land the next gameweek's odds, including anytime scorer.

    Kept as the one-call form for callers that already hold a warehouse. It is
    :func:`fetch_odds_api_gameweek` followed by :func:`land_odds_api_gameweek`;
    prefer :func:`refresh_odds_api`, which does not hold the write lock across
    the network half.
    """
    owns = client is None
    client = client or OddsApiClient(api_key, max_monthly_credits=max_monthly_credits)
    try:
        if dry_run:
            ev = client.events()
            events = events_within(ev.body, horizon)
            plan = CreditPlan(
                events=0, featured=2, scorer=len(events),
                cap=max_monthly_credits,
                used_before=client.quota.used if client.quota else None,
                remaining_before=client.quota.remaining if client.quota else None,
            )
            plan.check()  # a dry run still reports an unsatisfiable cap
            return ScorerIngestReport(
                events=len(events), credits_spent=0,
                credits_remaining=plan.remaining_before,
                credits_used_month=plan.used_before,
                rows_written=0, scorer_selections=0, matched=0,
                unmatched=[], clean_sheets_written=0,
            )
        got = fetch_odds_api_gameweek(
            client, regions=regions, max_monthly_credits=max_monthly_credits,
            horizon=horizon,
        )
        return land_odds_api_gameweek(
            wh, season, got, regions=regions, scorer_coverage=scorer_coverage)
    finally:
        if owns:
            client.close()


def refresh_odds_api(
    season: str,
    *,
    api_key: str,
    db_path: str | None = None,
    client: OddsApiClient | None = None,
    regions: str = "uk",
    max_monthly_credits: int = OddsApiClient.DEFAULT_MONTHLY_CAP,
    scorer_coverage: float = 0.95,
    horizon: dt.datetime | None = None,
    lock_timeout_s: float = 180.0,
) -> ScorerIngestReport:
    """Fetch first, then take the write lock. The form every scheduler should use.

    This is the whole reason :class:`OddsApiFetch` exists. The network half runs
    with no warehouse handle open at all; the warehouse is opened only once
    every body is in memory, and closed as soon as the rows are appended.
    Twelve HTTP round trips of lock contention become a sub-second write.

    Safe to call twice: ``fact_odds`` appends dedupe on the point-in-time key,
    and a second run within the same second writes the same rows to the same
    key rather than doubling them.
    """
    owns = client is None
    client = client or OddsApiClient(api_key, max_monthly_credits=max_monthly_credits)
    try:
        got = fetch_odds_api_gameweek(
            client, regions=regions, max_monthly_credits=max_monthly_credits,
            horizon=horizon,
        )
    finally:
        if owns:
            client.close()
    wh = (Warehouse(lock_timeout_s=lock_timeout_s) if db_path is None
          else Warehouse(db_path, lock_timeout_s=lock_timeout_s))
    try:
        return land_odds_api_gameweek(
            wh, season, got, regions=regions, scorer_coverage=scorer_coverage)
    finally:
        wh.close()


def _derive_live_rows(
    wh: Warehouse,
    season: str,
    as_of: dt.datetime,
    events: list[dict[str, Any]],
    featured: pd.DataFrame,
    payloads: dict[str, Any],
    coverage: float,
) -> tuple[pd.DataFrame, list[NameMatch]]:
    """Fair h2h/totals, clean sheets, and code-keyed scorer probabilities."""
    snap_teams = wh.snapshot_at(as_of).table("dim_team", where="season = ?", params=[season])
    fpl_names = set(snap_teams["name"]) if not snap_teams.empty else set()

    rows: list[dict[str, Any]] = []
    all_matches: list[NameMatch] = []

    for e in events:
        ko = dt.datetime.fromisoformat(str(e["commence_time"]).replace("Z", "+00:00"))
        key = natural_fixture_key(season, ko, e["home_team"], e["away_team"])
        rates = _fixture_goal_rates(featured, key)

        # fair consensus + clean sheets
        if rates is not None:
            cs_home, cs_away = clean_sheet_probs(rates)
            for sel, prob in (("HOME", cs_home), ("AWAY", cs_away)):
                if 0.0 < prob < 1.0:
                    rows.append(_row(key, "derived#poisson", MARKET_CLEAN_SHEET,
                                     sel, 1.0 / prob, as_of))

        # scorer card -> per-team fair probabilities, keyed on FPL code
        payload = payloads.get(e["id"])
        if payload is None or rates is None:
            continue
        by_player: dict[str, list[float]] = {}
        for bk in payload.get("bookmakers", []) or []:
            for m in bk.get("markets", []) or []:
                if m.get("key") != "player_goal_scorer_anytime":
                    continue
                for oc in m.get("outcomes", []) or []:
                    if oc.get("description") and oc.get("price", 0) > 1.0:
                        by_player.setdefault(str(oc["description"]), []).append(
                            float(oc["price"]))
        if not by_player:
            continue
        avg = _consensus(by_player)

        try:
            clubs = [resolve_team_name(e["home_team"], fpl_names),
                     resolve_team_name(e["away_team"], fpl_names)]
        except OddsApiError:
            all_matches.extend(NameMatch(n, None, None, "club_unresolved") for n in avg)
            continue

        squad = squad_for_fixture(wh, season, as_of, clubs)
        matches = match_player_names(sorted(avg), squad)
        all_matches.extend(matches)

        # Anchor each club's card to that club's own expected goals. Cards are
        # per-club in this feed, so anchoring to the match total would inflate
        # every rate by the opponent's share.
        code_to_club = {}
        if not squad.empty and not snap_teams.empty:
            club_by_code = dict(zip(snap_teams["team_code"], snap_teams["name"]))
            code_to_club = {int(r["code"]): club_by_code.get(r["team_code"])
                            for _, r in squad.iterrows()}
        lam = {clubs[0]: rates.home, clubs[1]: rates.away}

        per_club: dict[str, list[NameMatch]] = {}
        for m in matches:
            if m.code is None:
                continue
            per_club.setdefault(code_to_club.get(m.code) or clubs[0], []).append(m)

        for club, members in per_club.items():
            prices = [avg[m.api_name] for m in members]
            eg = lam.get(club)
            if not eg or eg <= 0:
                continue
            fair = devig_anytime_scorer(prices, eg, coverage=coverage, method="power")
            for m, p in zip(members, fair):
                if 0.0 < float(p) < 1.0:
                    rows.append(_row(key, "fair#scorer_power", MARKET_ANYTIME_SCORER,
                                     str(m.code), 1.0 / float(p), as_of))

    df = pd.DataFrame(rows, columns=[
        "fixture_key", "bookmaker", "market", "selection", "price_decimal", "as_of",
    ])
    return df, all_matches
