"""Consuming bookmaker odds. This package does not fetch them.

Sourcing odds is another team's problem, so what lives here is the *consumer*
side: a narrow provider protocol, three implementations of it, and the de-vig
arithmetic. Swapping a committed CSV fixture for a live feed is a one-line
change at the call site and touches no model code.

    +---------------------------+------------------------------------------+
    | SnapshotOddsProvider      | REAL DATA PATH. Reads fact_odds through  |
    |                           | a Snapshot, so odds are as-of filtered    |
    |                           | exactly like every other model input.     |
    +---------------------------+------------------------------------------+
    | FrameOddsProvider         | OFFLINE PATH. Same long fact_odds schema |
    |                           | from a committed CSV. Used by the tests   |
    |                           | and the synthetic evaluation.             |
    +---------------------------+------------------------------------------+
    | NullOddsProvider          | No odds. Reports zero coverage rather     |
    |                           | than silently falling back to a model,     |
    |                           | because "the market agreed with us" and    |
    |                           | "there was no market" must not look alike. |
    +---------------------------+------------------------------------------+

The fixture key is ``"{season}:{fixture_id}"``, matching the ``fixture_key``
convention in the warehouse schema.

De-vigging
----------
Quoted prices sum to more than 1 in probability; the excess is the bookmaker's
margin and must be removed before the numbers mean anything. Two methods:

* ``proportional`` -- divide by the overround. Simple, and the standard choice.
* ``power`` -- solve ``sum p_i^k = 1``. Handles favourite-longshot bias, which
  proportional scaling does not: the margin is not actually spread evenly across
  selections in real books.

Default is proportional. Note for anyone reading the synthetic results: the
synthetic bookmaker applies a *proportional* overround, so proportional
de-vigging inverts it exactly, which flatters the market baseline there. On real
prices that is not true and ``power`` is worth testing.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from fpl_edge.store import Snapshot

ODDS_COLUMNS = ("fixture_key", "bookmaker", "market", "selection", "price_decimal")

H2H_SELECTIONS = ("home", "draw", "away")


def fixture_key(season: str, fixture_id: int) -> str:
    return f"{season}:{int(fixture_id)}"


@dataclass(frozen=True, slots=True)
class FixtureOdds:
    """De-vigged market probabilities for one fixture."""

    fixture_key: str
    p_home: float
    p_draw: float
    p_away: float
    overround_h2h: float
    n_books: int
    p_over: float | None = None
    totals_line: float | None = None
    overround_totals: float | None = None

    @property
    def has_totals(self) -> bool:
        return self.p_over is not None and self.totals_line is not None


@runtime_checkable
class OddsProvider(Protocol):
    """Everything the market baseline needs from an odds source."""

    def odds_for(
        self, fixture_keys: list[str], as_of: dt.datetime
    ) -> dict[str, FixtureOdds]:
        """De-vigged probabilities per fixture, as quoted at or before ``as_of``.

        ``as_of`` is not optional and not advisory. Closing prices are the
        sharpest in the market and also the ones no manager could have acted on;
        an odds path that forgets to filter them is a leak that shows up as a
        market baseline nobody can beat. Missing fixtures are omitted, never
        imputed.
        """
        ...


def _devig(probs: np.ndarray, method: str) -> np.ndarray:
    total = float(probs.sum())
    if total <= 0:
        raise ValueError("non-positive implied probabilities")
    if method == "proportional":
        return probs / total
    if method == "power":
        if total <= 1.0 + 1e-9:
            return probs / total
        def f(k: float) -> float:
            return float((probs**k).sum() - 1.0)
        k = brentq(f, 1.0, 20.0, xtol=1e-10)
        out = probs**k
        return out / out.sum()
    raise ValueError(f"unknown de-vig method {method!r}")


def devig_frame(odds: pd.DataFrame, *, method: str = "proportional") -> dict[str, FixtureOdds]:
    """Long-format ``fact_odds`` rows -> :class:`FixtureOdds` per fixture.

    Prices are averaged across bookmakers *after* de-vigging each book
    separately, which is the right order: averaging raw prices across books with
    different margins mixes margin into the consensus.
    """
    if odds.empty:
        return {}
    out: dict[str, FixtureOdds] = {}
    for key, grp in odds.groupby("fixture_key", sort=True):
        h2h_rows = grp[grp["market"] == "h2h"]
        books: list[np.ndarray] = []
        overrounds: list[float] = []
        for _, bgrp in h2h_rows.groupby("bookmaker"):
            price = {str(s): float(p) for s, p in zip(bgrp["selection"], bgrp["price_decimal"],
                                                      strict=True)}
            if not all(s in price for s in H2H_SELECTIONS):
                continue
            raw = np.array([1.0 / price[s] for s in H2H_SELECTIONS])
            overrounds.append(float(raw.sum()))
            books.append(_devig(raw, method))
        if not books:
            continue
        h2h = np.mean(books, axis=0)

        tot_rows = grp[grp["market"] == "totals"]
        p_over: float | None = None
        line: float | None = None
        or_tot: float | None = None
        if not tot_rows.empty:
            over_books: list[float] = []
            or_books: list[float] = []
            lines: set[float] = set()
            for _, bgrp in tot_rows.groupby("bookmaker"):
                sel = {str(s): float(p) for s, p in zip(bgrp["selection"], bgrp["price_decimal"],
                                                        strict=True)}
                over_sel = [s for s in sel if s.startswith("over")]
                under_sel = [s for s in sel if s.startswith("under")]
                if not over_sel or not under_sel:
                    continue
                o, u = over_sel[0], under_sel[0]
                lines.add(float(o.split("_")[-1]))
                raw = np.array([1.0 / sel[o], 1.0 / sel[u]])
                or_books.append(float(raw.sum()))
                over_books.append(float(_devig(raw, method)[0]))
            if over_books and len(lines) == 1:
                p_over = float(np.mean(over_books))
                line = lines.pop()
                or_tot = float(np.mean(or_books))

        out[str(key)] = FixtureOdds(
            fixture_key=str(key),
            p_home=float(h2h[0]),
            p_draw=float(h2h[1]),
            p_away=float(h2h[2]),
            overround_h2h=float(np.mean(overrounds)),
            n_books=len(books),
            p_over=p_over,
            totals_line=line,
            overround_totals=or_tot,
        )
    return out


class SnapshotOddsProvider:
    """REAL DATA PATH: odds read through a Snapshot, as-of filtered.

    ``fact_odds`` is a point-in-time table keyed on
    ``(fixture_key, bookmaker, market, selection)``, so the snapshot returns the
    latest quote known at the as-of instant. Closing prices from after the
    deadline are invisible, which is the whole point -- they are the sharpest
    prices in the market and also the ones a manager could never have acted on.
    """

    def __init__(self, snapshot: Snapshot, *, method: str = "proportional") -> None:
        self.snapshot = snapshot
        self.method = method

    def odds_for(
        self, fixture_keys: list[str], as_of: dt.datetime
    ) -> dict[str, FixtureOdds]:
        if as_of > self.snapshot.as_of:
            raise ValueError(
                f"asked for odds as of {as_of} from a snapshot pinned at "
                f"{self.snapshot.as_of}"
            )
        if not fixture_keys:
            return {}
        placeholders = ", ".join("?" for _ in fixture_keys)
        odds = self.snapshot.table(
            "fact_odds", where=f"fixture_key IN ({placeholders})", params=list(fixture_keys)
        )
        return devig_frame(odds, method=self.method)

class FrameOddsProvider:
    """OFFLINE PATH: odds from a committed long-format frame or CSV.

    Applies the same ``as_of`` filter the warehouse would, using the frame's own
    ``as_of`` column. Without that this class would be a leak with a convenient
    interface: the evaluation harness would price fixtures using quotes
    published after the deadline it is pretending to stand at, and the market
    baseline would look unbeatable for reasons that have nothing to do with the
    market. A frame with no ``as_of`` column is rejected rather than assumed
    timeless.
    """

    def __init__(self, odds: pd.DataFrame, *, method: str = "proportional") -> None:
        if not odds.empty and "as_of" not in odds.columns:
            raise ValueError("odds frame must carry an as_of column")
        self.odds = (
            odds if odds.empty else odds.assign(as_of=pd.to_datetime(odds["as_of"], utc=True))
        )
        self.method = method
        # Indexed by fixture so a ten-fixture gameweek query touches ten
        # fixtures' rows and not the whole archive. The walk-forward backtest
        # calls this once per gameweek per model; de-vigging the entire frame
        # each time turned a 40-second evaluation into a five-minute one.
        self._by_fixture = (
            {}
            if self.odds.empty
            else {str(k): g for k, g in self.odds.groupby("fixture_key", sort=False)}
        )

    @classmethod
    def from_csv(cls, path: Path | str, *, method: str = "proportional") -> FrameOddsProvider:
        return cls(pd.read_csv(path), method=method)

    def odds_for(self, fixture_keys: list[str], as_of: dt.datetime) -> dict[str, FixtureOdds]:
        if not self._by_fixture or not fixture_keys:
            return {}
        stamp = pd.Timestamp(as_of)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        parts = [
            g for k in fixture_keys if (g := self._by_fixture.get(str(k))) is not None
        ]
        if not parts:
            return {}
        visible = pd.concat(parts)
        return devig_frame(visible[visible["as_of"] <= stamp], method=self.method)


class NullOddsProvider:
    """No odds available. Coverage is zero and says so."""

    def odds_for(self, fixture_keys: list[str], as_of: dt.datetime) -> dict[str, FixtureOdds]:
        return {}
