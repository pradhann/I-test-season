"""Odds: prices in, de-vigged probabilities and goal rates out.

Six modules, lowest first::

    prices.py         decimal/American odds -> probability, and goal rates
    devig.py          four published ways to remove the bookmaker's margin
    freshness.py      the market vocabulary and its per-market staleness budget
    football_data.py  the free historical archive the goal model is fitted on
    matching.py       joining three sources that disagree about names
    odds_api.py       the live, credit-metered feed

This file re-exports the public surface the single ``odds.py`` module had, so
nine test files and every caller import unchanged (ARCHITECTURE_REVIEW.md
Section 4 row 15). It imports only from this package.

Two private names are re-exported as well, because callers outside the package
already imported them off the flat module and a split is not the place to
withdraw them: ``_match_probs`` (``tests/unit/test_odds_devig.py``) and
``_slugify`` (``fpl_edge/models/ensemble/sources.py``). Neither is in
``__all__``, so neither is advertised as public.
"""

from fpl_edge.ingest.odds.devig import (
    DevigMethod,
    devig,
    devig_anytime_scorer,
    devig_independent,
    devig_multiplicative,
    devig_power,
    devig_shin,
    shin_z,
)
from fpl_edge.ingest.odds.football_data import (
    FD_BOOKS_1X2,
    FD_BOOKS_OU,
    FD_TEAM_ALIASES,
    FOOTBALL_DATA_BASE,
    TextFetcher,
    fd_season_code,
    ingest_football_data,
    ingest_football_data_fixtures,
    natural_fixture_key,
    parse_football_data_csv,
)
from fpl_edge.ingest.odds.freshness import (
    DEFAULT_MAX_AGE_H,
    MARKET_ANYTIME_SCORER,
    MARKET_CLEAN_SHEET,
    MARKET_H2H,
    MARKET_MAX_AGE_H,
    MARKET_TOTALS,
    MarketFreshness,
    UK,
    freshness_summary,
    odds_freshness,
)
from fpl_edge.ingest.odds.matching import (
    NameMatch,
    fold_name,
    match_fixture_keys,
    match_player_names,
    squad_for_fixture,
)
from fpl_edge.ingest.odds.odds_api import (
    CreditBudgetExceeded,
    CreditPlan,
    FREE_TIER_MONTHLY_CREDITS,
    ODDS_API_BASE,
    ODDS_API_TEAM_ALIASES,
    OddsApiClient,
    OddsApiError,
    OddsApiFetch,
    OddsApiFetcher,
    OddsApiQuota,
    REFUSAL_IMPOSSIBLE,
    REFUSAL_KEY_EXHAUSTED,
    REFUSAL_MONTH_EXHAUSTED,
    ScorerIngestReport,
    commence_utc,
    events_within,
    fetch_odds_api_gameweek,
    ingest_odds_api_gameweek,
    land_odds_api_gameweek,
    parse_odds_api_events,
    refresh_odds_api,
    resolve_team_name,
)
from fpl_edge.ingest.odds.football_data import _slugify as _slugify
from fpl_edge.ingest.odds.prices import _match_probs as _match_probs
from fpl_edge.ingest.odds.prices import (
    GoalRates,
    american_to_decimal,
    clean_sheet_probs,
    fit_goal_rates,
    implied_prob,
    overround,
)

__all__ = [
    "CreditBudgetExceeded",
    "CreditPlan",
    "DEFAULT_MAX_AGE_H",
    "DevigMethod",
    "FD_BOOKS_1X2",
    "FD_BOOKS_OU",
    "FD_TEAM_ALIASES",
    "FOOTBALL_DATA_BASE",
    "FREE_TIER_MONTHLY_CREDITS",
    "GoalRates",
    "MARKET_ANYTIME_SCORER",
    "MARKET_CLEAN_SHEET",
    "MARKET_H2H",
    "MARKET_MAX_AGE_H",
    "MARKET_TOTALS",
    "MarketFreshness",
    "NameMatch",
    "ODDS_API_BASE",
    "ODDS_API_TEAM_ALIASES",
    "OddsApiClient",
    "OddsApiError",
    "OddsApiFetch",
    "OddsApiFetcher",
    "OddsApiQuota",
    "REFUSAL_IMPOSSIBLE",
    "REFUSAL_KEY_EXHAUSTED",
    "REFUSAL_MONTH_EXHAUSTED",
    "ScorerIngestReport",
    "TextFetcher",
    "UK",
    "american_to_decimal",
    "clean_sheet_probs",
    "commence_utc",
    "devig",
    "devig_anytime_scorer",
    "devig_independent",
    "devig_multiplicative",
    "devig_power",
    "devig_shin",
    "events_within",
    "fd_season_code",
    "fetch_odds_api_gameweek",
    "fit_goal_rates",
    "fold_name",
    "freshness_summary",
    "implied_prob",
    "ingest_football_data",
    "ingest_football_data_fixtures",
    "ingest_odds_api_gameweek",
    "land_odds_api_gameweek",
    "match_fixture_keys",
    "match_player_names",
    "natural_fixture_key",
    "odds_freshness",
    "overround",
    "parse_football_data_csv",
    "parse_odds_api_events",
    "refresh_odds_api",
    "resolve_team_name",
    "shin_z",
    "squad_for_fixture",
]
