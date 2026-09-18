"""the-odds-api.com: the live, credit-metered feed.

Everything here spends a metered resource, so everything here counts. The
credit plan is computed before a request is made, a refusal names which budget
stopped it, and the quota the response reports is recorded beside the rows it
paid for. A silent overspend is the failure mode this module exists to make
impossible.

Split out of the 1,966-line ``fpl_edge/ingest/odds.py``
(ARCHITECTURE_REVIEW.md Section 3 and Section 4 row 15). The cut is by
function name, not by line range: the original's own line ranges overlapped.
``fpl_edge.ingest.odds`` re-exports the whole public surface, so every caller
imports exactly what it imported before.
"""

from __future__ import annotations
import datetime as dt
from dataclasses import dataclass
from typing import Any
import numpy as np
import pandas as pd
from fpl_edge.ingest.http import Fetched, Fetcher
from fpl_edge.ingest.player_mapping import normalize_name
from fpl_edge.store import Warehouse

from fpl_edge.ingest.odds.devig import devig_anytime_scorer, devig_shin
from fpl_edge.ingest.odds.football_data import _row, natural_fixture_key
from fpl_edge.ingest.odds.freshness import MARKET_ANYTIME_SCORER, MARKET_CLEAN_SHEET, MARKET_H2H, MARKET_TOTALS
from fpl_edge.ingest.odds.matching import NameMatch, match_player_names, squad_for_fixture
from fpl_edge.ingest.odds.prices import GoalRates, clean_sheet_probs, fit_goal_rates


ODDS_API_BASE = "https://api.the-odds-api.com/v4"


FREE_TIER_MONTHLY_CREDITS = 500


class OddsApiError(RuntimeError):
    """The Odds API refused a request, or no key is configured."""


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
    #: * the pre-deadline ladder fires 3 times a gameweek (T-36h, T-12h, T-5h)
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


_ODDS_API_MARKETS = {
    "h2h": MARKET_H2H,
    "totals": MARKET_TOTALS,
    "player_goal_scorer_anytime": MARKET_ANYTIME_SCORER,
}


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
