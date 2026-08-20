"""Extra Odds API markets: correct score, BTTS, team totals.

What the free tier actually offers (measured, not assumed)
----------------------------------------------------------
Checked on 2026-08-19 against ``GET /v4/sports/soccer_epl/events/{id}/markets``
for Arsenal v Coventry, with the credit cost read off the vendor's own
``x-requests-last`` header:

* The markets-catalog endpoint itself cost **1 credit per call** (the same 1
  whether one region or two were requested).
* ``regions=uk`` offers ``correct_score`` (William Hill: a 44-cell grid;
  Betfair exchange: 4x4) and ``btts`` (10 books), but **no team totals**.
* ``regions=eu,us`` offers ``team_totals`` and ``alternate_team_totals`` --
  but only from US books (FanDuel, DraftKings, BetRivers on the sampled
  event). So team totals are reachable, at US-book prices, via a per-event
  call with ``regions=us``.
* Per-event odds calls cost ``markets x regions``: ``correct_score,btts`` in
  one region cost 2; ``team_totals,alternate_team_totals`` in one region
  cost 2. Both confirmed live.

Budget
------
This expansion has its own hard cap, :data:`EXPANSION_MONTHLY_CAP` (150 of the
free tier's 500/month), enforced *before* anything is spent and accounted in a
local ledger (:data:`LEDGER_PATH`) per calendar month. The ledger exists
because the vendor's ``x-requests-used`` header counts every consumer of the
key, so "how much has this expansion spent" is a question only we can answer.
A full 10-event gameweek run costs 2 (h2h+totals refresh) + 10x2 (correct
score + BTTS, uk) + 10x2 (team totals, us) = **42 credits**.

Selection normalisation
-----------------------
The generic parser in :mod:`fpl_edge.ingest.odds` cannot be reused for these
markets because its fallback selection (``description or name``) collapses a
team-totals card to one row per team, silently dropping every Over/Under leg
but the first. Formats written here:

* ``correct_score`` -- ``"<home>-<away>"`` (e.g. ``"2-0"``). The API quotes
  ``"TeamA:x|TeamB:y"`` with the *winner* first, so the raw string is
  resolved against the event's home/away names before writing; see
  :func:`fpl_edge.ingest.odds_derived.parse_correct_score_selection`.
* ``btts`` -- ``"YES"`` / ``"NO"``.
* ``team_totals`` -- ``"HOME|OVER_2.5"`` etc. ``alternate_team_totals`` rows
  are folded into the same market name: they are the same contract at more
  lines, and keeping them apart would force every consumer to union two
  markets forever.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from fpl_edge.ingest.http import Fetched
from fpl_edge.ingest.odds import (
    CreditBudgetExceeded,
    OddsApiClient,
    natural_fixture_key,
    parse_odds_api_events,
)
from fpl_edge.ingest.odds_derived import parse_correct_score_selection
from fpl_edge.store import Warehouse

MARKET_CORRECT_SCORE = "correct_score"
MARKET_BTTS = "btts"
MARKET_TEAM_TOTALS = "team_totals"

#: Hard monthly ceiling for this expansion, separate from (and inside) the
#: 400-credit cap the anytime-scorer ingest already enforces on the key.
EXPANSION_MONTHLY_CAP = 150

LEDGER_PATH = Path("data/odds_expansion_ledger.json")

#: Lay/exchange variants arrive unrequested and are a different contract.
_SKIP = {"correct_score_lay", "btts_lay", "team_totals_lay", "alternate_team_totals_lay"}


# --------------------------------------------------------------------------
# spend ledger
# --------------------------------------------------------------------------


def _month_key(now: dt.datetime) -> str:
    return f"{now.year:04d}-{now.month:02d}"


def ledger_spent(now: dt.datetime, path: Path = LEDGER_PATH) -> int:
    """Credits this expansion has spent in ``now``'s calendar month."""
    if not path.exists():
        return 0
    data = json.loads(path.read_text())
    month = data.get(_month_key(now), {})
    return int(month.get("spent", 0))


def ledger_record(
    credits: int, note: str, now: dt.datetime, path: Path = LEDGER_PATH
) -> int:
    """Append one run's spend; returns the month's new total."""
    data: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {}
    month = data.setdefault(_month_key(now), {"spent": 0, "runs": []})
    month["spent"] = int(month["spent"]) + int(credits)
    month["runs"].append({"at": now.isoformat(), "credits": int(credits), "note": note})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    return int(month["spent"])


def check_expansion_budget(
    planned: int,
    now: dt.datetime,
    *,
    cap: int = EXPANSION_MONTHLY_CAP,
    path: Path = LEDGER_PATH,
) -> None:
    """Refuse a run that would push this month's expansion spend over ``cap``."""
    spent = ledger_spent(now, path)
    if spent + planned > cap:
        raise CreditBudgetExceeded(
            f"extra-markets run needs {planned} credits but this expansion has "
            f"already spent {spent} of its {cap}/month cap. Nothing was spent."
        )


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def parse_extra_market_event(
    payload: dict[str, Any], as_of: dt.datetime, season: str
) -> pd.DataFrame:
    """Flatten one event's correct-score / BTTS / team-totals odds.

    Returns ``fact_odds``-shaped rows with the normalised selections described
    in the module docstring. Unparseable outcomes are dropped, not guessed:
    a correct-score cell whose team names do not match the event's own teams
    would otherwise be silently transposed.
    """
    home = str(payload.get("home_team", ""))
    away = str(payload.get("away_team", ""))
    ko = payload.get("commence_time")
    kickoff = dt.datetime.fromisoformat(str(ko).replace("Z", "+00:00")) if ko else None
    key = natural_fixture_key(season, kickoff, home, away)

    rows: list[dict[str, Any]] = []
    for bk in payload.get("bookmakers", []) or []:
        book = str(bk.get("key", "unknown"))
        for mkt in bk.get("markets", []) or []:
            mkey = str(mkt.get("key", ""))
            if mkey in _SKIP:
                continue
            for oc in mkt.get("outcomes", []) or []:
                price = oc.get("price")
                if price is None or float(price) <= 1.0:
                    continue
                sel = _selection(mkey, oc, home, away)
                if sel is None:
                    continue
                market = MARKET_TEAM_TOTALS if mkey == "alternate_team_totals" else mkey
                rows.append({
                    "fixture_key": key, "bookmaker": book, "market": market,
                    "selection": sel, "price_decimal": float(price), "as_of": as_of,
                })
    df = pd.DataFrame(rows, columns=[
        "fixture_key", "bookmaker", "market", "selection", "price_decimal", "as_of",
    ])
    # Books repeat a line across the base and alternate team-totals feeds; the
    # duplicate is identical by construction, so keep one.
    return df.drop_duplicates()


def _selection(mkey: str, oc: dict[str, Any], home: str, away: str) -> str | None:
    if mkey == MARKET_CORRECT_SCORE:
        parsed = parse_correct_score_selection(str(oc.get("name", "")), home, away)
        if parsed is None:
            return None
        return f"{parsed[0]}-{parsed[1]}"
    if mkey == MARKET_BTTS:
        name = str(oc.get("name", "")).upper()
        return name if name in {"YES", "NO"} else None
    if mkey in {MARKET_TEAM_TOTALS, "alternate_team_totals"}:
        name = str(oc.get("name", "")).upper()
        team = str(oc.get("description", ""))
        point = oc.get("point")
        if name not in {"OVER", "UNDER"} or point is None:
            return None
        if team == home:
            side = "HOME"
        elif team == away:
            side = "AWAY"
        else:
            return None
        return f"{side}|{name}_{point}"
    return None


# --------------------------------------------------------------------------
# ingestion
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtraMarketsReport:
    """Real numbers for one run: what was spent and what landed."""

    events: int
    credits_planned: int
    credits_spent: int
    credits_remaining: int | None
    credits_used_month: int | None
    expansion_spent_month: int
    rows_written: int
    rows_by_market: dict[str, int]


def _event_extra_odds(
    client: OddsApiClient, event_id: str, markets: str, regions: str
) -> Fetched:
    """Per-event odds for arbitrary markets. Costs ``markets x regions``."""
    return client._get(  # noqa: SLF001 -- same-package extension of the client
        f"sports/soccer_epl/events/{event_id}/odds",
        {"regions": regions, "markets": markets, "oddsFormat": "decimal"},
    )


def ingest_extra_markets(
    wh: Warehouse,
    season: str,
    *,
    api_key: str,
    client: OddsApiClient | None = None,
    include_featured: bool = True,
    include_team_totals: bool = True,
    cap: int = EXPANSION_MONTHLY_CAP,
    ledger_path: Path = LEDGER_PATH,
    dry_run: bool = False,
) -> ExtraMarketsReport:
    """Fetch correct-score, BTTS and team-totals odds for the upcoming round.

    Spending order mirrors ``ingest_odds_api_gameweek``: the free ``/events``
    call prices the run against both the vendor's remaining balance and this
    expansion's own monthly ledger before a single credit is spent.

    Per event: ``correct_score,btts`` from the UK region (2 credits) and,
    when ``include_team_totals``, ``team_totals,alternate_team_totals`` from
    the US region (2 credits) -- the only region that carries them, per the
    measured catalog in the module docstring. ``include_featured`` refreshes
    the uk h2h+totals consensus in the same run (2 credits) so the derivation
    in :mod:`fpl_edge.ingest.odds_derived` compares like-timed quotes.
    """
    owns = client is None
    client = client or OddsApiClient(api_key, max_monthly_credits=500)
    try:
        ev = client.events()
        events = ev.body
        n = len(events)
        planned = (2 if include_featured else 0) + 2 * n + (2 * n if include_team_totals else 0)
        now = ev.fetched_at
        check_expansion_budget(planned, now, cap=cap, path=ledger_path)
        if client.quota and client.quota.remaining is not None \
                and planned > client.quota.remaining:
            raise CreditBudgetExceeded(
                f"run needs {planned} credits but only {client.quota.remaining} "
                "remain on the key this month. Nothing was spent."
            )
        if dry_run:
            return ExtraMarketsReport(
                events=n, credits_planned=planned, credits_spent=0,
                credits_remaining=client.quota.remaining if client.quota else None,
                credits_used_month=client.quota.used if client.quota else None,
                expansion_spent_month=ledger_spent(now, ledger_path),
                rows_written=0, rows_by_market={},
            )

        wh.record_fetch(source="odds_api", endpoint="events", params=None,
                        fetched_at=ev.fetched_at, sha256=ev.sha256,
                        body_path=str(ev.body_path), http_status=ev.http_status)

        frames: list[pd.DataFrame] = []
        if include_featured:
            feat = client.featured_odds(regions="uk")
            wh.record_fetch(source="odds_api", endpoint="odds",
                            params="regions=uk&markets=h2h,totals",
                            fetched_at=feat.fetched_at, sha256=feat.sha256,
                            body_path=str(feat.body_path), http_status=feat.http_status)
            frames.append(parse_odds_api_events(feat.body, feat.fetched_at, season))

        plans = [("correct_score,btts", "uk")]
        if include_team_totals:
            plans.append(("team_totals,alternate_team_totals", "us"))
        for e in events:
            for markets, regions in plans:
                got = _event_extra_odds(client, e["id"], markets, regions)
                wh.record_fetch(
                    source="odds_api", endpoint=f"events/{e['id']}/odds",
                    params=f"regions={regions}&markets={markets}",
                    fetched_at=got.fetched_at, sha256=got.sha256,
                    body_path=str(got.body_path), http_status=got.http_status,
                )
                frames.append(parse_extra_market_event(got.body, got.fetched_at, season))

        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        written = wh.append("fact_odds", df) if len(df) else 0
        by_market = df.groupby("market").size().to_dict() if len(df) else {}
        month_total = ledger_record(
            client.spent_this_run,
            f"extra markets, {n} events, {written} rows",
            now, ledger_path,
        )
        return ExtraMarketsReport(
            events=n,
            credits_planned=planned,
            credits_spent=client.spent_this_run,
            credits_remaining=client.quota.remaining if client.quota else None,
            credits_used_month=client.quota.used if client.quota else None,
            expansion_spent_month=month_total,
            rows_written=written,
            rows_by_market={str(k): int(v) for k, v in by_market.items()},
        )
    finally:
        if owns:
            client.close()
