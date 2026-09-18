"""football-data.co.uk: the free historical closing-odds archive.

One CSV per competition-season, no key, no quota, and it is the only source in
this package with real depth of history. It is therefore what the goal model is
fitted and backtested on, while the paid live feed in ``odds_api`` covers the
week ahead.

Split out of the 1,966-line ``fpl_edge/ingest/odds.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 15). The cut is by
function name, not by line range: the original's own line ranges overlapped.
``fpl_edge.ingest.odds`` re-exports the whole public surface, so every caller
imports exactly what it imported before.
"""

from __future__ import annotations
import datetime as dt
import io
import math
import re
from typing import Any
import numpy as np
import pandas as pd
from fpl_edge.ingest.http import Fetched, Fetcher, _now, _slug
from fpl_edge.store import Warehouse

from fpl_edge.ingest.odds.devig import DevigMethod, devig
from fpl_edge.ingest.odds.freshness import MARKET_CLEAN_SHEET, MARKET_H2H, MARKET_TOTALS, UK
from fpl_edge.ingest.odds.prices import clean_sheet_probs, fit_goal_rates


FOOTBALL_DATA_BASE = "https://www.football-data.co.uk"


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


FD_BOOKS_OU: dict[str, str] = {
    "B365": "bet365",
    "P": "pinnacle",
    "Max": "market_max",
    "Avg": "market_avg",
    "BFE": "betfair_exchange",
}


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
