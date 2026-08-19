"""The Odds API path: parsing, scorer de-vigging, name matching, credit budget.

Fixtures are the real live payloads recorded on 2026-08-18 against the account's
own key (the key itself is never stored — only the response bodies, which carry
no secret).

The three things that will silently produce garbage if they regress, and which
therefore get the most tests here:

1. Player props put the player in ``description``; ``name`` is the literal
   ``"Yes"``. Keying on ``name`` collapses 17 rows into one.
2. Anytime scorer is a set of *independent* yes/no bets. Normalising them to
   sum to 1 halves every striker.
3. Credits are a hard monthly budget. A run that would breach it must spend
   nothing at all, not stop halfway.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_edge.ingest.odds import (
    MARKET_ANYTIME_SCORER,
    MARKET_H2H,
    MARKET_TOTALS,
    CreditBudgetExceeded,
    CreditPlan,
    NameMatch,
    OddsApiError,
    OddsApiQuota,
    devig_anytime_scorer,
    fold_name,
    match_player_names,
    parse_odds_api_events,
    resolve_team_name,
)

FIX = Path(__file__).parents[1] / "fixtures" / "odds"
UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 8, 18, 23, 30, tzinfo=UTC)


@pytest.fixture(scope="module")
def props() -> dict:
    return json.loads((FIX / "odds_api_scorers_arsenal.json").read_text())


@pytest.fixture(scope="module")
def featured() -> list[dict]:
    return json.loads((FIX / "odds_api_featured.json").read_text())


# -- the "Yes" trap ----------------------------------------------------------


def test_every_scorer_outcome_is_literally_named_yes(props: dict) -> None:
    """The premise of the next test, asserted against the recorded payload."""
    names = {
        o["name"]
        for bk in props["bookmakers"]
        for m in bk["markets"]
        for o in m["outcomes"]
    }
    assert names == {"Yes"}


def test_scorer_selection_is_the_player_not_the_word_yes(props: dict) -> None:
    df = parse_odds_api_events(props, AS_OF, "2026-27")
    scorer = df[df["market"] == MARKET_ANYTIME_SCORER]
    assert not scorer.empty
    assert "Yes" not in set(scorer["selection"])
    assert "Viktor Gyokeres" in set(scorer["selection"])


def test_scorer_rows_do_not_collide_on_the_primary_key(props: dict) -> None:
    """Keying on ``name`` would leave one surviving row per bookmaker.

    ``fact_odds`` is keyed on (fixture_key, bookmaker, market, selection,
    as_of). Seventeen rows all called "Yes" from one book differ in nothing but
    price, so sixteen would be dropped by the append and never noticed.
    """
    df = parse_odds_api_events(props, AS_OF, "2026-27")
    scorer = df[df["market"] == MARKET_ANYTIME_SCORER]
    dupes = scorer.duplicated(
        subset=["fixture_key", "bookmaker", "market", "selection", "as_of"]
    )
    assert not dupes.any()
    per_book = scorer.groupby("bookmaker")["selection"].nunique()
    assert (per_book == 17).all(), per_book.to_dict()


def test_all_three_uk_books_are_parsed(props: dict) -> None:
    df = parse_odds_api_events(props, AS_OF, "2026-27")
    assert set(df["bookmaker"]) == {"paddypower", "williamhill", "skybet"}


# -- featured markets --------------------------------------------------------


def test_h2h_selections_are_normalised_to_home_draw_away(featured: list[dict]) -> None:
    """So the column means the same thing as it does for football-data."""
    df = parse_odds_api_events(featured, AS_OF, "2026-27")
    h2h = df[df["market"] == MARKET_H2H]
    assert set(h2h["selection"]) == {"HOME", "DRAW", "AWAY"}


def test_lay_prices_are_not_mixed_in_with_back_prices(featured: list[dict]) -> None:
    """``h2h_lay`` arrives unrequested from the exchange.

    A lay price is what you accept to take the other side. Averaging it into a
    back-price consensus biases every fair line.
    """
    raw_markets = {
        m["key"] for e in featured for bk in e["bookmakers"] for m in bk["markets"]
    }
    assert "h2h_lay" in raw_markets  # the payload really does contain them

    df = parse_odds_api_events(featured, AS_OF, "2026-27")
    assert "h2h_lay" not in set(df["market"])


def test_totals_selections_carry_the_line(featured: list[dict]) -> None:
    df = parse_odds_api_events(featured, AS_OF, "2026-27")
    tot = df[df["market"] == MARKET_TOTALS]
    assert not tot.empty
    assert all(s.startswith(("OVER_", "UNDER_")) for s in tot["selection"])
    assert "OVER_2.5" in set(tot["selection"])


def test_as_of_is_the_fetch_instant_for_live_rows(featured: list[dict]) -> None:
    """These prices are observable now, so they may inform the next deadline."""
    df = parse_odds_api_events(featured, AS_OF, "2026-27")
    assert (df["as_of"] == pd.Timestamp(AS_OF)).all()


# -- scorer de-vigging -------------------------------------------------------

#: The real consensus card for Arsenal v Coventry, 2026-08-21.
ARSENAL_CARD = [1.78, 2.13, 2.50, 2.63, 2.66, 2.67, 2.75, 2.82,
                3.80, 4.33, 4.87, 6.27, 6.27, 6.83, 8.50, 9.83, 12.00]


def test_the_scorer_card_does_not_sum_to_one() -> None:
    """The fact that makes mutually-exclusive de-vigging inapplicable."""
    total = sum(1 / o for o in ARSENAL_CARD)
    assert total == pytest.approx(4.748, abs=0.01)
    assert total > 4.0


def test_scorer_devig_does_not_normalise_to_one() -> None:
    """If this ever sums to 1.0, someone has reused the 1X2 de-vig."""
    p = devig_anytime_scorer(ARSENAL_CARD, team_expected_goals=2.649)
    assert p.sum() > 1.5
    assert not np.isclose(p.sum(), 1.0)


def test_scorer_devig_anchors_implied_goal_rates_to_expected_goals() -> None:
    """The actual contract: rates are additive, probabilities are not.

    sum(-ln(1 - p_i)) is the team's expected goals, so that is what gets
    matched — not the sum of the probabilities.
    """
    eg, cov = 2.649, 0.95
    p = devig_anytime_scorer(ARSENAL_CARD, eg, coverage=cov)
    implied = float((-np.log(1.0 - p)).sum())
    assert implied == pytest.approx(eg * cov, rel=1e-6)


def test_scorer_devig_shrinks_every_price_and_preserves_order() -> None:
    p = devig_anytime_scorer(ARSENAL_CARD, 2.649)
    q = np.array([1 / o for o in ARSENAL_CARD])
    assert (p < q).all()
    assert list(np.argsort(-p)) == list(np.argsort(-q))


def test_power_shrinks_longshots_harder_than_favourites() -> None:
    """Books load margin onto the tail; a uniform scale over-shrinks the favourite.

    This is the whole reason ``power`` is the default. If the relationship ever
    inverts, the estimate is biased against exactly the differential picks the
    model exists to find.
    """
    uni = devig_anytime_scorer(ARSENAL_CARD, 2.649, method="uniform")
    pw = devig_anytime_scorer(ARSENAL_CARD, 2.649, method="power")
    assert pw[0] > uni[0]      # favourite retains more
    assert pw[-1] < uni[-1]    # longshot loses more


def test_scorer_devig_is_a_noop_when_the_card_is_already_conservative() -> None:
    """Never inflate. If the card implies fewer goals than the market, leave it."""
    card = [20.0, 25.0, 30.0]
    p = devig_anytime_scorer(card, team_expected_goals=3.0)
    np.testing.assert_allclose(p, [1 / o for o in card])


def test_scorer_devig_rejects_nonsense_inputs() -> None:
    with pytest.raises(ValueError, match="team_expected_goals"):
        devig_anytime_scorer(ARSENAL_CARD, 0.0)
    with pytest.raises(ValueError, match="coverage"):
        devig_anytime_scorer(ARSENAL_CARD, 2.0, coverage=1.5)
    with pytest.raises(ValueError, match="unknown method"):
        devig_anytime_scorer(ARSENAL_CARD, 2.0, method="vibes")  # type: ignore[arg-type]


def test_a_bigger_expected_goals_anchor_raises_every_probability() -> None:
    low = devig_anytime_scorer(ARSENAL_CARD, 1.5)
    high = devig_anytime_scorer(ARSENAL_CARD, 2.6)
    assert (high > low).all()


# -- name matching -----------------------------------------------------------


def _squad(rows: list[tuple[int, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"code": c, "web_name": w, "first_name": f, "second_name": s} for c, w, f, s in rows]
    )


ARSENAL_SQUAD = _squad([
    (224117, "Gyökeres", "Viktor", "Gyökeres"),
    (219847, "Havertz", "Kai", "Havertz"),
    (223340, "Saka", "Bukayo", "Saka"),
    (481655, "Zubimendi", "Martín", "Zubimendi Ibáñez"),
    (226597, "Gabriel", "Gabriel", "dos Santos Magalhães"),
    (444145, "Martinelli", "Gabriel", "Martinelli Silva"),
    (198869, "White", "Benjamin", "White"),
    (204480, "Rice", "Declan", "Rice"),
    (499169, "Lewis-Skelly", "Myles", "Lewis-Skelly"),
    (169187, "Ødegaard", "Martin", "Ødegaard"),
])


def test_folding_handles_stroked_letters_that_do_not_decompose() -> None:
    """``Ø`` has no NFKD decomposition, so the shared normaliser drops it.

    Without folding, "Ødegaard" normalises to "degaard" and the bookmaker's
    "Odegaard" never matches. This was a real 1-in-17 failure on the live card.
    """
    assert fold_name("Martin Ødegaard") == "martin odegaard"
    assert fold_name("Martin Odegaard") == fold_name("Martin Ødegaard")
    assert fold_name("Viktor Gyökeres") == "viktor gyokeres"


@pytest.mark.parametrize(
    ("api_name", "expected_code", "expected_rule"),
    [
        ("Viktor Gyokeres", 224117, "exact_full"),
        ("Martin Zubimendi Ibanez", 481655, "exact_full"),   # accents folded
        ("Martin Odegaard", 169187, "exact_full"),           # stroked O folded
        ("Myles Lewis-Skelly", 499169, "exact_full"),        # hyphen folded
        ("Gabriel Martinelli", 444145, "api_subset"),        # FPL has a third token
        ("Magalhaes Gabriel", 226597, "api_subset"),         # reversed token order
        ("Ben White", 198869, "surname_initial"),            # Ben vs Benjamin
    ],
)
def test_real_bookmaker_names_resolve(api_name: str, expected_code: int,
                                      expected_rule: str) -> None:
    """Every one of these is a name the live GW1 cards actually contained."""
    m = match_player_names([api_name], ARSENAL_SQUAD)[0]
    assert m.code == expected_code, m
    assert m.rule == expected_rule


def test_a_more_verbose_bookmaker_name_still_resolves() -> None:
    """``fpl_subset`` handles the case where the book carries extra tokens.

    FPL's "Kepa Arrizabalaga" against a book writing the full
    "Kepa Arrizabalaga Revuelta".
    """
    squad = _squad([(109745, "Arrizabalaga", "Kepa", "Arrizabalaga")])
    m = match_player_names(["Kepa Arrizabalaga Revuelta"], squad)[0]
    assert m.code == 109745
    assert m.rule == "fpl_subset"


def test_odegaard_is_not_ambiguous_against_other_martins() -> None:
    """The surname anchor exists because of this exact collision.

    "Martin Odegaard" and "Martin Zubimendi Ibanez" share the token "martin".
    A rule keyed on any shared token made Odegaard ambiguous.
    """
    m = match_player_names(["Martin Odegaard"], ARSENAL_SQUAD)[0]
    assert m.code == 169187
    assert m.rule != "ambiguous"


def test_an_ambiguous_name_is_refused_rather_than_guessed() -> None:
    """Two players who genuinely cannot be told apart must both be dropped."""
    squad = _squad([
        (1, "Davies", "Ben", "Davies"),
        (2, "Davies", "Ben", "Davies"),
    ])
    m = match_player_names(["Ben Davies"], squad)[0]
    assert m.code is None
    assert m.rule == "ambiguous"


def test_an_unknown_player_is_reported_not_silently_dropped() -> None:
    m = match_player_names(["Somebody Entirely Else"], ARSENAL_SQUAD)[0]
    assert m.code is None
    assert m.rule == "unmatched"
    assert m.api_name == "Somebody Entirely Else"


def test_an_empty_squad_is_reported_as_such() -> None:
    m = match_player_names(["Bukayo Saka"], pd.DataFrame())[0]
    assert m.code is None
    assert m.rule == "no_squad"


def test_match_returns_one_result_per_input_name() -> None:
    """Nothing is ever quietly dropped from the report."""
    names = ["Bukayo Saka", "Nobody At All", "Declan Rice"]
    out = match_player_names(names, ARSENAL_SQUAD)
    assert [m.api_name for m in out] == names
    assert isinstance(out[0], NameMatch)


# -- club names --------------------------------------------------------------


FPL_CLUBS = {"Arsenal", "Man City", "Man Utd", "Spurs", "Brighton", "Leeds",
             "Newcastle", "Nott'm Forest", "Coventry City", "Liverpool"}


@pytest.mark.parametrize(
    ("api", "fpl"),
    [
        ("Manchester City", "Man City"),
        ("Manchester United", "Man Utd"),
        ("Tottenham Hotspur", "Spurs"),
        ("Brighton and Hove Albion", "Brighton"),
        ("Leeds United", "Leeds"),
        ("Newcastle United", "Newcastle"),
        ("Nottingham Forest", "Nott'm Forest"),
        ("Arsenal", "Arsenal"),
    ],
)
def test_club_names_map_to_fpl(api: str, fpl: str) -> None:
    assert resolve_team_name(api, FPL_CLUBS) == fpl


def test_an_unknown_club_raises_rather_than_guessing() -> None:
    """A wrong club silently attributes a whole squad to the wrong team."""
    with pytest.raises(OddsApiError, match="cannot map"):
        resolve_team_name("Atlantis United", FPL_CLUBS)


# -- credit budget -----------------------------------------------------------


def test_quota_is_read_from_response_headers() -> None:
    q = OddsApiQuota.from_headers(
        {"x-requests-remaining": "496", "x-requests-used": "4", "x-requests-last": "2"}
    )
    assert (q.remaining, q.used, q.last_cost) == (496, 4, 2)


def test_quota_tolerates_missing_headers() -> None:
    q = OddsApiQuota.from_headers({})
    assert (q.remaining, q.used, q.last_cost) == (None, None, None)


def test_a_plan_within_budget_is_allowed() -> None:
    CreditPlan(events=0, featured=2, scorer=10, cap=400,
               used_before=4, remaining_before=496).check()


def test_a_plan_that_would_exhaust_the_balance_is_refused() -> None:
    plan = CreditPlan(events=0, featured=2, scorer=10, cap=400,
                      used_before=495, remaining_before=5)
    with pytest.raises(CreditBudgetExceeded, match="only 5 remain"):
        plan.check()


def test_a_plan_that_would_breach_the_configured_cap_is_refused() -> None:
    """The cap is stricter than the vendor's limit, and that is the point.

    Stopping at 400 of 500 leaves room to re-run after a failure without
    blowing the month's allowance.
    """
    plan = CreditPlan(events=0, featured=2, scorer=10, cap=400,
                      used_before=395, remaining_before=105)
    with pytest.raises(CreditBudgetExceeded, match="cap is 400"):
        plan.check()


def test_the_refusal_message_states_nothing_was_spent() -> None:
    """Operationally important: a refused run leaves the balance untouched."""
    plan = CreditPlan(events=0, featured=2, scorer=10, cap=10,
                      used_before=9, remaining_before=400)
    with pytest.raises(CreditBudgetExceeded, match="Nothing was spent"):
        plan.check()


def test_plan_total_counts_only_what_is_charged() -> None:
    """The events call is free, so it must not inflate the estimate."""
    plan = CreditPlan(events=0, featured=2, scorer=10, cap=400,
                      used_before=0, remaining_before=500)
    assert plan.total == 12


def test_budget_check_passes_when_quota_headers_are_unavailable() -> None:
    """Missing headers must not become an accidental hard block."""
    CreditPlan(events=0, featured=2, scorer=10, cap=400,
               used_before=None, remaining_before=None).check()
