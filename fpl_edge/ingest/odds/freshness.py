"""How old the market data is, per market, and what that costs the reader.

The market vocabulary lives here beside the per-market staleness budget that is
keyed on it: a match-odds price and an anytime-scorer price decay at different
rates, and one global age cutoff gets one of them wrong.

A leaf. Every other module in the package reads the market names from here.

Split out of the 1,966-line ``fpl_edge/ingest/odds.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 15). The cut is by
function name, not by line range: the original's own line ranges overlapped.
``fpl_edge.ingest.odds`` re-exports the whole public surface, so every caller
imports exactly what it imported before.
"""

from __future__ import annotations
import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo
import pandas as pd
from fpl_edge.store import Warehouse


UK = ZoneInfo("Europe/London")


MARKET_H2H = "h2h"


MARKET_TOTALS = "totals"


MARKET_CLEAN_SHEET = "clean_sheet"


MARKET_ANYTIME_SCORER = "anytime_scorer"


MARKET_MAX_AGE_H: dict[str, float] = {
    MARKET_H2H: 24.0,
    MARKET_TOTALS: 24.0,
    MARKET_CLEAN_SHEET: 24.0,
    MARKET_ANYTIME_SCORER: 48.0,
    "correct_score": 48.0,
    "btts": 48.0,
    "team_totals": 48.0,
}


DEFAULT_MAX_AGE_H = 48.0


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
