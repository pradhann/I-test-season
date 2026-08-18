"""football-data.co.uk ingestion, offline against recorded bytes.

The fixture is the first six fixtures of the real 2025-26 ``E0.csv``, fetched
2026-08-18 (HTTP 200). Using real bytes rather than a hand-written CSV is the
point: football-data's column naming is irregular enough (``PS`` for Pinnacle
1X2 but ``P`` for Pinnacle over/under, ``C`` infix for closing) that a
synthetic fixture would only test our own assumptions back at us.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import pytest

from fpl_edge.ingest.odds import (
    MARKET_CLEAN_SHEET,
    MARKET_H2H,
    MARKET_TOTALS,
    fd_season_code,
    match_fixture_keys,
    natural_fixture_key,
    parse_football_data_csv,
)
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc
FIXTURE = Path(__file__).parents[1] / "fixtures" / "odds" / "football_data_E0_2526_sample.csv"


@pytest.fixture(scope="module")
def csv_text() -> str:
    return FIXTURE.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def parsed(csv_text: str) -> pd.DataFrame:
    return parse_football_data_csv(csv_text, "2025-26")


@pytest.fixture()
def wh(tmp_path) -> Warehouse:
    return Warehouse(tmp_path / "t.duckdb")


# -- season code -------------------------------------------------------------


def test_season_code_matches_football_datas_directory_convention() -> None:
    assert fd_season_code("2025-26") == "2526"
    assert fd_season_code("1993-94") == "9394"
    assert fd_season_code("1999-00") == "9900"


def test_season_code_rejects_a_malformed_season() -> None:
    for bad in ("2025", "2025-2026", "25-26", ""):
        with pytest.raises(ValueError):
            fd_season_code(bad)


# -- parsing -----------------------------------------------------------------


def test_parses_every_fixture_in_the_file(parsed: pd.DataFrame) -> None:
    assert parsed["fixture_key"].nunique() == 6


def test_emits_the_markets_the_points_model_consumes(parsed: pd.DataFrame) -> None:
    assert set(parsed["market"]) == {MARKET_H2H, MARKET_TOTALS, MARKET_CLEAN_SHEET}


def test_recovers_a_known_quoted_price(parsed: pd.DataFrame) -> None:
    """Bet365 had Liverpool at 1.30 to beat Bournemouth (opening line)."""
    key = natural_fixture_key(
        "2025-26", dt.datetime(2025, 8, 15, 19, tzinfo=UTC), "Liverpool", "Bournemouth"
    )
    row = parsed[
        (parsed["fixture_key"] == key)
        & (parsed["bookmaker"] == "bet365#open")
        & (parsed["market"] == MARKET_H2H)
        & (parsed["selection"] == "HOME")
    ]
    assert len(row) == 1
    assert row.iloc[0]["price_decimal"] == pytest.approx(1.30)


def test_opening_and_closing_lines_are_kept_apart(parsed: pd.DataFrame) -> None:
    """Both are stamped at kickoff, so they must not collide on the primary key.

    ``fact_odds`` is keyed on (fixture_key, bookmaker, market, selection,
    as_of). Without the ``#open`` suffix the closing line would silently fail to
    insert behind the opening one and the table would quietly hold the wrong
    number.
    """
    books = set(parsed["bookmaker"])
    assert "bet365" in books
    assert "bet365#open" in books

    dupes = parsed.duplicated(
        subset=["fixture_key", "bookmaker", "market", "selection", "as_of"]
    )
    assert not dupes.any(), parsed[dupes].head().to_dict("records")


def test_pinnacle_appears_in_both_market_blocks(parsed: pd.DataFrame) -> None:
    """The prefix trap: Pinnacle is ``PS`` for 1X2 and ``P`` for over/under."""
    pin = parsed[parsed["bookmaker"].str.startswith("pinnacle")]
    assert MARKET_H2H in set(pin["market"])
    assert MARKET_TOTALS in set(pin["market"])


def test_betfair_exchange_is_captured(parsed: pd.DataFrame) -> None:
    """Exchange prices carry the least margin, so they matter most for fair value."""
    assert (parsed["bookmaker"].str.startswith("betfair_exchange")).any()


def test_prices_are_all_valid_decimals(parsed: pd.DataFrame) -> None:
    assert (parsed["price_decimal"] > 1.0).all()
    assert parsed["price_decimal"].notna().all()


# -- de-vigged output --------------------------------------------------------


def test_fair_rows_are_labelled_as_derived_not_quoted(parsed: pd.DataFrame) -> None:
    """Nobody should mistake a modelled number for a price a book actually hung."""
    derived = parsed[parsed["bookmaker"].str.contains("#")]
    quoted_looking = {b for b in derived["bookmaker"] if not b.endswith("#open")}
    assert quoted_looking == {"fair#shin", "derived#poisson"}


def test_fair_h2h_probabilities_sum_to_one_per_fixture(parsed: pd.DataFrame) -> None:
    fair = parsed[(parsed["bookmaker"] == "fair#shin") & (parsed["market"] == MARKET_H2H)]
    assert len(fair) == 18  # 6 fixtures x 3 selections
    for _, grp in fair.groupby("fixture_key"):
        assert (1.0 / grp["price_decimal"]).sum() == pytest.approx(1.0, abs=1e-9)


def test_fair_totals_probabilities_sum_to_one_per_fixture(parsed: pd.DataFrame) -> None:
    fair = parsed[(parsed["bookmaker"] == "fair#shin") & (parsed["market"] == MARKET_TOTALS)]
    for _, grp in fair.groupby("fixture_key"):
        assert (1.0 / grp["price_decimal"]).sum() == pytest.approx(1.0, abs=1e-9)


def test_fair_price_is_longer_than_the_book_it_was_derived_from(
    parsed: pd.DataFrame,
) -> None:
    """De-vigging strips the bookmaker's edge, so every fair price gets *longer*.

    The quoted price is what the book pays after keeping its margin; the fair
    price is what a zero-margin book would pay. Since the quoted probabilities
    sum to more than one, removing the overround lowers every probability and
    therefore lengthens every price. A fair price shorter than the quoted one
    would mean the de-vig had added margin rather than removed it.

    Compared against ``market_avg`` because that is the book the fair line is
    derived from -- see the next test for why "best available" is different.
    """
    for key, grp in parsed[parsed["market"] == MARKET_H2H].groupby("fixture_key"):
        fair = grp[grp["bookmaker"] == "fair#shin"].set_index("selection")["price_decimal"]
        avg = grp[grp["bookmaker"] == "market_avg"].set_index("selection")["price_decimal"]
        if avg.empty:
            continue
        for sel, price in fair.items():
            assert price >= avg[sel] - 1e-9, f"{key} {sel}: fair {price} < avg {avg[sel]}"


def test_the_best_available_price_can_beat_fair_value(parsed: pd.DataFrame) -> None:
    """This is not a bug -- it is the entire signal.

    ``market_max`` is the best price any tracked book hung. The consensus fair
    line comes from the *average* book, so an outlier book can and does price a
    selection longer than fair, i.e. at positive expected value. If this never
    happened, the market would be perfectly homogeneous and there would be no
    disagreement for the model to exploit.
    """
    beats = 0
    for _, grp in parsed[parsed["market"] == MARKET_H2H].groupby("fixture_key"):
        fair = grp[grp["bookmaker"] == "fair#shin"].set_index("selection")["price_decimal"]
        mx = grp[grp["bookmaker"] == "market_max"].set_index("selection")["price_decimal"]
        if mx.empty:
            continue
        beats += int((mx > fair).any())
    assert beats > 0


def test_clean_sheet_is_derived_for_both_sides(parsed: pd.DataFrame) -> None:
    cs = parsed[parsed["market"] == MARKET_CLEAN_SHEET]
    assert set(cs["selection"]) == {"HOME", "AWAY"}
    assert (cs["bookmaker"] == "derived#poisson").all()
    probs = 1.0 / cs["price_decimal"]
    assert (probs > 0.0).all() and (probs < 1.0).all()


def test_clean_sheet_probabilities_are_football_shaped(parsed: pd.DataFrame) -> None:
    """A whole-file sanity band. EPL clean sheets run ~20-40% per side."""
    cs = parsed[parsed["market"] == MARKET_CLEAN_SHEET]
    probs = 1.0 / cs["price_decimal"]
    assert 0.02 < probs.min()
    assert probs.max() < 0.75
    assert 0.15 < probs.mean() < 0.50


def test_the_heavy_favourite_has_the_better_clean_sheet_chance(parsed: pd.DataFrame) -> None:
    key = natural_fixture_key(
        "2025-26", dt.datetime(2025, 8, 15, 19, tzinfo=UTC), "Liverpool", "Bournemouth"
    )
    cs = parsed[(parsed["fixture_key"] == key)
                & (parsed["market"] == MARKET_CLEAN_SHEET)].set_index("selection")
    p_home = 1.0 / cs.loc["HOME", "price_decimal"]
    p_away = 1.0 / cs.loc["AWAY", "price_decimal"]
    assert p_home > p_away


def test_devig_method_is_recorded_in_the_bookmaker_name(csv_text: str) -> None:
    """Switching method must not silently overwrite the previous answer."""
    m = parse_football_data_csv(csv_text, "2025-26", devig_method="multiplicative")
    assert "fair#multiplicative" in set(m["bookmaker"])
    assert "fair#shin" not in set(m["bookmaker"])


# -- point-in-time discipline ------------------------------------------------


def test_historical_odds_are_stamped_at_kickoff_not_at_fetch(parsed: pd.DataFrame) -> None:
    """The leak this file is most likely to cause.

    ``mmz4281/*/E0.csv`` is published *after* the matches are played. If we
    stamped it with the fetch instant, every backtest deadline would suddenly
    be able to see closing odds for matches that had not kicked off. Stamping
    at kickoff is the last instant the closing line was genuinely observable.
    """
    key = natural_fixture_key(
        "2025-26", dt.datetime(2025, 8, 15, 19, tzinfo=UTC), "Liverpool", "Bournemouth"
    )
    stamps = parsed[parsed["fixture_key"] == key]["as_of"].unique()
    assert len(stamps) == 1
    # 15/08/2025 20:00 UK (BST, UTC+1) -> 19:00Z
    assert pd.Timestamp(stamps[0]) == pd.Timestamp("2025-08-15T19:00:00Z")


def test_uk_local_kickoff_times_convert_through_bst(parsed: pd.DataFrame) -> None:
    """Aston Villa v Newcastle, 16/08/2025 12:30 UK = 11:30Z in British Summer Time.

    Getting this wrong by an hour would make odds visible before kickoff in
    winter and invisible after it in summer.
    """
    key = natural_fixture_key(
        "2025-26", dt.datetime(2025, 8, 16, 11, 30, tzinfo=UTC), "Aston Villa", "Newcastle"
    )
    stamps = parsed[parsed["fixture_key"] == key]["as_of"].unique()
    assert pd.Timestamp(stamps[0]) == pd.Timestamp("2025-08-16T11:30:00Z")


def test_forward_looking_rows_may_be_stamped_at_fetch(csv_text: str) -> None:
    """The live path (``fixtures.csv``) is observable now, so it stamps at fetch."""
    now = dt.datetime(2026, 8, 18, 22, tzinfo=UTC)
    df = parse_football_data_csv(csv_text, "2025-26", as_of=now)
    assert (df["as_of"] == pd.Timestamp(now)).all()


# -- warehouse integration ---------------------------------------------------


def test_rows_land_in_fact_odds_and_are_idempotent(wh: Warehouse, parsed: pd.DataFrame) -> None:
    n = wh.append("fact_odds", parsed)
    assert n == len(parsed)
    assert wh.append("fact_odds", parsed) == 0  # replaying the archive adds nothing


def test_snapshot_hides_odds_for_a_fixture_that_has_not_kicked_off(
    wh: Warehouse, parsed: pd.DataFrame
) -> None:
    """The whole point of stamping at kickoff, asserted end to end."""
    wh.append("fact_odds", parsed)

    before = wh.snapshot_at(dt.datetime(2025, 8, 15, 12, tzinfo=UTC)).table("fact_odds")
    assert before.empty

    after_first = wh.snapshot_at(dt.datetime(2025, 8, 15, 20, tzinfo=UTC)).table("fact_odds")
    assert after_first["fixture_key"].nunique() == 1

    all_played = wh.snapshot_at(dt.datetime(2025, 9, 1, tzinfo=UTC)).table("fact_odds")
    assert all_played["fixture_key"].nunique() == 6


def test_snapshot_returns_one_row_per_market_selection(
    wh: Warehouse, parsed: pd.DataFrame
) -> None:
    wh.append("fact_odds", parsed)
    snap = wh.snapshot_at(dt.datetime(2025, 9, 1, tzinfo=UTC)).table("fact_odds")
    assert not snap.duplicated(
        subset=["fixture_key", "bookmaker", "market", "selection"]
    ).any()


# -- fixture matching --------------------------------------------------------


def test_natural_key_is_stable_and_readable() -> None:
    k = natural_fixture_key(
        "2025-26", dt.datetime(2025, 8, 16, 11, 30, tzinfo=UTC), "Nott'm Forest", "Man City"
    )
    assert k == "2025-26:2025-08-16:nott-m-forest:man-city"


def test_natural_key_tolerates_an_unknown_kickoff() -> None:
    assert natural_fixture_key("2025-26", None, "A", "B").endswith("unknown:a:b")


def test_fixture_matching_resolves_football_data_team_spellings(
    wh: Warehouse,
) -> None:
    """football-data says "Man City"/"Tottenham"; the FPL API says otherwise."""
    as_of = dt.datetime(2025, 8, 1, tzinfo=UTC)
    wh.append("dim_team", pd.DataFrame([
        {"season": "2025-26", "team_code": 43, "team_id": 1, "name": "Manchester City",
         "short_name": "MCI", "as_of": as_of},
        {"season": "2025-26", "team_code": 6, "team_id": 2, "name": "Spurs",
         "short_name": "TOT", "as_of": as_of},
    ]))
    wh.append("fact_fixture", pd.DataFrame([{
        "season": "2025-26", "fixture_id": 77, "gw": 1,
        "kickoff_utc": dt.datetime(2025, 8, 16, 14, tzinfo=UTC),
        "home_team_code": 43, "away_team_code": 6, "finished": False,
        "home_score": None, "away_score": None, "as_of": as_of,
    }]))
    wh.append("fact_odds", pd.DataFrame([{
        "fixture_key": natural_fixture_key(
            "2025-26", dt.datetime(2025, 8, 16, 14, tzinfo=UTC), "Man City", "Tottenham"),
        "bookmaker": "bet365", "market": MARKET_H2H, "selection": "HOME",
        "price_decimal": 1.5, "as_of": dt.datetime(2025, 8, 16, 14, tzinfo=UTC),
    }]))

    m = match_fixture_keys(wh, "2025-26", dt.datetime(2025, 9, 1, tzinfo=UTC))
    assert len(m) == 1
    assert m.iloc[0]["fixture_id"] == 77
    assert m.iloc[0]["matched_key"] == "2025-26:77"


def test_unmatched_fixtures_are_reported_not_silently_dropped(wh: Warehouse) -> None:
    """A failed name match must be visible, because it will happen on promotion."""
    as_of = dt.datetime(2025, 8, 1, tzinfo=UTC)
    wh.append("dim_team", pd.DataFrame([
        {"season": "2025-26", "team_code": 43, "team_id": 1, "name": "Manchester City",
         "short_name": "MCI", "as_of": as_of},
    ]))
    wh.append("fact_fixture", pd.DataFrame([{
        "season": "2025-26", "fixture_id": 1, "gw": 1,
        "kickoff_utc": dt.datetime(2025, 8, 16, 14, tzinfo=UTC),
        "home_team_code": 43, "away_team_code": 43, "finished": False,
        "home_score": None, "away_score": None, "as_of": as_of,
    }]))
    wh.append("fact_odds", pd.DataFrame([{
        "fixture_key": "2025-26:2025-08-16:atlantis-fc:mystery-town",
        "bookmaker": "bet365", "market": MARKET_H2H, "selection": "HOME",
        "price_decimal": 1.5, "as_of": dt.datetime(2025, 8, 16, 14, tzinfo=UTC),
    }]))

    m = match_fixture_keys(wh, "2025-26", dt.datetime(2025, 9, 1, tzinfo=UTC))
    assert len(m) == 1
    assert pd.isna(m.iloc[0]["fixture_id"])
    assert m.iloc[0]["matched_key"] is None
