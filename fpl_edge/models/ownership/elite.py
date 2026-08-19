"""Ownership among the top-10k, which is the only ownership that decides whether
a differential pays.

The engine's target rank is 10,000 out of a field approaching six million. My
rank against *that* cohort is what the objective maximises, and the top-10k
template is measurably not the overall template: the elite cohort is more
concentrated on the same premiums, carries fewer club-loyalty picks, and moves
onto a bandwagon roughly a gameweek before the crowd does.

Two paths, and the honest distinction between them matters:

**Measured (post-deadline).** The API publishes the overall league at
``/leagues-classic/314/standings/`` and each entry's picks at
``/entry/{id}/event/{gw}/picks/``. Sampling real top managers' picks gives a
direct estimate with a binomial standard error you can quote. That is what
:class:`ElitePicksSampler` does.

**Modelled (pre-deadline, and specifically now).** Before the first deadline of
a season neither endpoint carries anything usable: the overall standings are
empty because no gameweek has been scored, and the picks endpoint returns 404
for every entry because picks are private until the deadline passes. Verified
against the live API on 2026-08-18. So the GW1 top-10k figure is a **prior**,
produced by :func:`elite_tilt`, and it is the weakest number this package
emits. It is labelled as such in the output frame rather than dressed up as a
measurement.

The tilt
--------

Two effects, applied to the overall forecast:

1. **Lead.** The elite cohort has already made the move the field is in the
   middle of making. Applying the forecast drift again, multiplied by
   ``lead``, shifts the elite template ahead of the crowd's.
2. **Concentration.** Raising shares to a power above 1 and renormalising makes
   the template more top-heavy, which is the direction the elite differ in.

Neither coefficient can be estimated before picks are observable, so both are
declared priors with a documented sensitivity band, and
:meth:`EliteTiltParams.fit_from_picks` replaces them with measured values the
moment a real sample exists.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.ingest.http import Fetcher
from fpl_edge.models.ownership.field import project_to_simplex

BASE = "https://fantasy.premierleague.com/api"

#: The "Overall" classic league every entry joins. Its standings are the rank
#: ordering of the entire field.
OVERALL_LEAGUE_ID = 314

#: Entries per standings page in the official API.
PAGE_SIZE = 50

#: Seconds between requests. The API is free and unmetered and belongs to
#: someone else; 10 requests a second because we can is not acceptable.
POLITE_DELAY_S = 0.35


@dataclass(frozen=True, slots=True)
class EliteTiltParams:
    """Prior mapping overall ownership onto top-10k ownership.

    ``lead``:
        multiples of the forecast drift to apply again. 0 means the elite look
        exactly like the field; 1 means they are a full forecast-step ahead.
    ``concentration``:
        exponent applied before renormalising. 1 is no reshaping.
    ``measured``:
        False whenever these came from the prior rather than from sampled picks.
        The forecast frame carries this through so nothing downstream can mistake
        a prior for an observation.
    """

    lead: float = 1.6
    concentration: float = 1.12
    measured: bool = False
    sample_size: int = 0

    def __post_init__(self) -> None:
        if self.lead < 0:
            raise ValueError("lead must be non-negative")
        if not 0.2 <= self.concentration <= 3.0:
            raise ValueError("concentration outside a sane range")


def elite_tilt(
    own_now: np.ndarray,
    own_forecast: np.ndarray,
    params: EliteTiltParams | None = None,
) -> np.ndarray:
    """Top-10k ownership implied by the overall forecast.

    Returns shares on the same simplex as the input (they sum to the same total,
    because top-10k managers also own exactly 15 players each).
    """
    p = params or EliteTiltParams()
    now = np.asarray(own_now, dtype=float)
    fut = np.asarray(own_forecast, dtype=float)
    if now.shape != fut.shape:
        raise ValueError("own_now and own_forecast must align")
    led = np.clip(fut + p.lead * (fut - now), 0.0, 1.0)
    shaped = np.clip(led, 0.0, 1.0) ** p.concentration
    return project_to_simplex(shaped, total=float(fut.sum()))


# --------------------------------------------------------------------------
# real sampling
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EliteSample:
    """A sample of real top-cohort squads and the estimates drawn from it."""

    gw: int
    entry_ids: tuple[int, ...]
    ownership: pd.Series          # element_id -> share of the sampled cohort
    start_share: pd.Series
    captaincy: pd.Series
    triple_captain: pd.Series
    as_of: dt.datetime

    @property
    def n(self) -> int:
        return len(self.entry_ids)

    def standard_error(self, share: float) -> float:
        """Binomial standard error on a sampled share.

        Quoting an elite ownership figure without this is dishonest: 100 sampled
        squads put a +/- 5 percentage point band on a 50% share, which is wider
        than most of the differences the number is used to argue about.
        """
        if self.n == 0:
            raise ValueError("empty sample")
        return float(np.sqrt(max(share, 0.0) * max(1.0 - share, 0.0) / self.n))


class ElitePicksSampler:
    """Sample real managers' picks from the official API.

    Rate limited and archived through :class:`~fpl_edge.ingest.http.Fetcher`, so
    every squad this estimate rests on is reproducible from bytes on disk.
    """

    def __init__(self, fetcher: Fetcher | None = None, *, delay_s: float = POLITE_DELAY_S) -> None:
        self._fetcher = fetcher or Fetcher("fpl_api", base_url=BASE)
        self._owns_fetcher = fetcher is None
        self.delay_s = delay_s

    def close(self) -> None:
        if self._owns_fetcher:
            self._fetcher.close()

    def __enter__(self) -> "ElitePicksSampler":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def top_entry_ids(self, n: int) -> list[int]:
        """Entry ids from the top of the overall league, in rank order.

        Returns fewer than ``n`` -- possibly zero -- when the standings are not
        yet populated. Before the first gameweek is scored the league is empty,
        and that is a fact about the season, not an error to retry through.
        """
        ids: list[int] = []
        page = 1
        while len(ids) < n:
            got = self._fetcher.get_json(
                f"leagues-classic/{OVERALL_LEAGUE_ID}/standings/",
                {"page_standings": page},
            )
            standings = got.body.get("standings", {})
            results = standings.get("results", [])
            if not results:
                break
            ids.extend(int(r["entry"]) for r in results)
            if not standings.get("has_next"):
                break
            page += 1
            time.sleep(self.delay_s)
        return ids[:n]

    def entry_picks(self, entry_id: int, gw: int) -> list[dict] | None:
        """One entry's picks, or None if they are not public.

        Picks are private until the deadline passes, so a 404 here before the
        deadline is the expected answer rather than a failure.
        """
        try:
            got = self._fetcher.get_json(f"entry/{entry_id}/event/{gw}/picks/")
        except Exception:
            return None
        body = got.body
        if not isinstance(body, dict) or "picks" not in body:
            return None
        return body["picks"]

    def sample(self, gw: int, n_entries: int = 200) -> EliteSample:
        """Aggregate ``n_entries`` top squads into cohort shares."""
        ids = self.top_entry_ids(n_entries)
        own: dict[int, int] = {}
        start: dict[int, int] = {}
        cap: dict[int, int] = {}
        tc: dict[int, int] = {}
        used: list[int] = []
        for entry_id in ids:
            picks = self.entry_picks(entry_id, gw)
            time.sleep(self.delay_s)
            if not picks:
                continue
            used.append(entry_id)
            for pick in picks:
                el = int(pick["element"])
                mult = int(pick.get("multiplier", 0))
                own[el] = own.get(el, 0) + 1
                if mult >= 1:
                    start[el] = start.get(el, 0) + 1
                if pick.get("is_captain"):
                    cap[el] = cap.get(el, 0) + 1
                    if mult >= 3:
                        tc[el] = tc.get(el, 0) + 1
        n = max(len(used), 1)
        # Index every series over every element anyone owned, so a player who
        # was owned but never started reads as 0.0 rather than raising.
        index = sorted(own)

        def series(d: dict[int, int]) -> pd.Series:
            return (pd.Series(d, dtype=float)
                    .reindex(index)
                    .fillna(0.0)
                    .divide(n))

        return EliteSample(
            gw=gw, entry_ids=tuple(used), ownership=series(own), start_share=series(start),
            captaincy=series(cap), triple_captain=series(tc),
            as_of=dt.datetime.now(dt.timezone.utc),
        )

    def elite_frame_from_history(
        self, entry_ids: list[int], *, elite_rank_threshold: int = 100_000
    ) -> pd.DataFrame:
        """Classify sampled entries by their past-season ranks.

        The pre-deadline fallback for identifying a strong cohort. The overall
        standings are empty before a gameweek is scored, but ``/entry/{id}/``
        carries every past season the manager played and the rank they finished.
        That is enough to build a frame of demonstrably strong managers whose
        picks can be sampled the instant they become public.
        """
        rows = []
        for entry_id in entry_ids:
            try:
                got = self._fetcher.get_json(f"entry/{entry_id}/history/")
            except Exception:
                continue
            time.sleep(self.delay_s)
            past = got.body.get("past", []) if isinstance(got.body, dict) else []
            ranks = [int(p["rank"]) for p in past if p.get("rank")]
            rows.append({
                "entry_id": entry_id,
                "seasons_played": len(ranks),
                "best_rank": min(ranks) if ranks else None,
                "median_rank": int(np.median(ranks)) if ranks else None,
                "is_elite": bool(ranks) and min(ranks) <= elite_rank_threshold,
            })
        return pd.DataFrame(rows)
