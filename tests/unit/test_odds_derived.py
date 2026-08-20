"""Derived odds priors: correct-score maths, inversions, schema, pipeline.

Fixtures are real live payloads recorded 2026-08-19 (Arsenal v Coventry,
GW1 2026-27). The regressions these tests exist to catch:

1. Correct-score selections list the *winner* first ("Coventry City:1|
   Arsenal:0" is an away win). Assuming home-first transposes every away-win
   cell and inflates the home clean sheet.
2. A fairly-priced grid must round-trip: de-vig + sum must recover the exact
   clean-sheet probabilities of the matrix that priced it.
3. Team-totals ladders must invert to the lambda that generated them.
4. ``fact_odds_derived`` must behave like every other PIT table: idempotent
   appends, snapshot reads, contradiction refusal -- without touching
   ``schema.sql``.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_edge.ingest.odds_derived import (
    RHO_STAR,
    TABLE,
    clean_sheets_from_correct_score,
    derive_fixture_rows,
    ensure_derived_schema,
    invert_match_odds,
    parse_correct_score_selection,
    scorer_rate,
    team_code_by_slug,
    team_lambda_from_totals,
)
from fpl_edge.ingest.odds_markets import parse_extra_market_event
from fpl_edge.models.team_goals.scoreline import (
    GoalRates,
    clean_sheet_probs,
    score_matrix,
)
from fpl_edge.store import Warehouse
from fpl_edge.store.warehouse import PIT_KEYS

FIX = Path(__file__).parents[1] / "fixtures" / "odds"
UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 8, 19, 18, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def cs_payload() -> dict:
    return json.loads((FIX / "odds_api_correct_score_btts.json").read_text())


@pytest.fixture(scope="module")
def tt_payload() -> dict:
    return json.loads((FIX / "odds_api_team_totals.json").read_text())


# -- winner-first selection parsing ------------------------------------------


def test_correct_score_home_first() -> None:
    got = parse_correct_score_selection(
        "Arsenal:2|Coventry City:0", "Arsenal", "Coventry City"
    )
    assert got == (2, 0)


def test_correct_score_winner_first_flips() -> None:
    """The real away-win format from William Hill's GW1 card."""
    got = parse_correct_score_selection(
        "Coventry City:1|Arsenal:0", "Arsenal", "Coventry City"
    )
    assert got == (0, 1)  # Arsenal 0, Coventry 1 -- NOT (1, 0)


def test_correct_score_unknown_team_is_dropped() -> None:
    assert parse_correct_score_selection(
        "Arsenal:1|Chelsea:0", "Arsenal", "Coventry City"
    ) is None
    assert parse_correct_score_selection("2-0", "Arsenal", "Coventry City") is None


# -- CS from a fair grid must round-trip --------------------------------------


def _fair_grid(rates: GoalRates, max_goals: int = 8) -> dict[tuple[int, int], float]:
    mat = score_matrix(rates, max_goals)
    return {
        (h, a): 1.0 / mat[h, a]
        for h in range(max_goals + 1)
        for a in range(max_goals + 1)
        if mat[h, a] > 1e-9
    }


@pytest.mark.parametrize("method", ["multiplicative", "power", "shin"])
def test_cs_grid_round_trip_on_fair_prices(method: str) -> None:
    rates = GoalRates(1.9, 0.8)
    truth_h, truth_a = clean_sheet_probs(score_matrix(rates, 8))
    d = clean_sheets_from_correct_score(_fair_grid(rates), method=method)
    assert d.p_home_cs == pytest.approx(truth_h, abs=2e-3)
    assert d.p_away_cs == pytest.approx(truth_a, abs=2e-3)
    assert d.quoted_sum == pytest.approx(1.0, abs=1e-6)


def test_cs_grid_rejects_fragment() -> None:
    with pytest.raises(ValueError, match="sparse"):
        clean_sheets_from_correct_score({(1, 0): 6.0, (2, 0): 7.0})


def test_cs_grid_on_real_william_hill_card(cs_payload: dict) -> None:
    """44 real cells; the derived numbers must be sane probabilities that agree
    in direction with the market (Arsenal heavy favourites at home)."""
    df = parse_extra_market_event(cs_payload, AS_OF, "2026-27")
    wh_rows = df[(df["bookmaker"] == "williamhill") & (df["market"] == "correct_score")]
    cells = {
        tuple(int(x) for x in sel.split("-")): price
        for sel, price in zip(wh_rows["selection"], wh_rows["price_decimal"])
    }
    assert len(cells) == 44
    d = clean_sheets_from_correct_score(cells, method="power")
    assert 0.35 < d.p_home_cs < 0.75  # Arsenal CS: clear favourite territory
    assert 0.02 < d.p_away_cs < 0.25
    assert d.p_nil_nil < d.p_away_cs  # 0-0 is a subset of Coventry's CS
    # the raw grid carries overround minus the truncated tail
    assert 0.9 < d.quoted_sum < 1.6


# -- match-odds inversion ------------------------------------------------------


@pytest.mark.parametrize("rho", [0.0, RHO_STAR])
def test_invert_match_odds_round_trip(rho: float) -> None:
    rates = GoalRates(1.7, 1.1, rho)
    mat = score_matrix(rates, 10)
    h = float(np.tril(mat, -1).sum())
    d = float(np.trace(mat))
    a = float(np.triu(mat, 1).sum())
    idx = np.arange(11)
    p_over = float(mat[(idx[:, None] + idx[None, :]) > 2.5].sum())
    inv = invert_match_odds(h, d, a, p_over, rho=rho)
    assert inv.rates.home == pytest.approx(1.7, abs=1e-6)
    assert inv.rates.away == pytest.approx(1.1, abs=1e-6)
    assert inv.residual < 1e-9
    assert inv.used_totals


def test_invert_without_totals_is_weakly_identified() -> None:
    """1X2 alone pins supremacy, not level: recovery is approximate but the
    home/away ordering and outcome probabilities must still reproduce."""
    rates = GoalRates(1.7, 1.1)
    mat = score_matrix(rates, 10)
    h, d, a = (
        float(np.tril(mat, -1).sum()),
        float(np.trace(mat)),
        float(np.triu(mat, 1).sum()),
    )
    inv = invert_match_odds(h, d, a, None)
    assert inv.rates.home > inv.rates.away
    assert inv.residual < 1e-6  # 2 unknowns fit 3 constraints that sum to 1


# -- team totals ----------------------------------------------------------------


def test_team_lambda_single_line_exact() -> None:
    lam = 1.8
    from scipy.stats import poisson

    p_over_25 = float(poisson.sf(2, lam))
    assert team_lambda_from_totals([(2.5, p_over_25)]) == pytest.approx(lam, abs=1e-9)


def test_team_lambda_ladder_least_squares() -> None:
    lam = 2.2
    from scipy.stats import poisson

    lines = [(k + 0.5, float(poisson.sf(k, lam))) for k in range(5)]
    assert team_lambda_from_totals(lines) == pytest.approx(lam, abs=1e-6)


def test_team_lambda_rejects_integer_line() -> None:
    with pytest.raises(ValueError, match="half-goal"):
        team_lambda_from_totals([(2.0, 0.5)])


def test_team_totals_parse_keeps_every_rung(tt_payload: dict) -> None:
    """The generic parser collapsed team totals to one row per team; the
    dedicated parser must keep each (side, direction, line) leg distinct."""
    df = parse_extra_market_event(tt_payload, AS_OF, "2026-27")
    tt = df[df["market"] == "team_totals"]
    fanduel = tt[tt["bookmaker"] == "fanduel"]
    # fanduel quoted base (2 lines) + alternates (ladder); all legs distinct
    assert fanduel["selection"].is_unique
    assert {"HOME|OVER_2.5", "HOME|UNDER_2.5"} <= set(fanduel["selection"])
    assert (tt["selection"].str.match(r"(HOME|AWAY)\|(OVER|UNDER)_\d+\.5")).all()


# -- scorer rate ------------------------------------------------------------------


def test_scorer_rate_inverts_poisson() -> None:
    lam = 0.62
    p = 1.0 - math.exp(-lam)
    assert scorer_rate(p) == pytest.approx(lam, rel=1e-12)


def test_scorer_rate_rejects_degenerate() -> None:
    for bad in (0.0, 1.0, -0.1, 1.3):
        with pytest.raises(ValueError):
            scorer_rate(bad)


# -- schema + pipeline --------------------------------------------------------------


def test_migration_is_idempotent_and_registered(tmp_path: Path) -> None:
    with Warehouse(tmp_path / "t.duckdb") as wh:
        ensure_derived_schema(wh)
        ensure_derived_schema(wh)  # second run must be a no-op
        assert TABLE in PIT_KEYS
        cols = set(
            wh.sql(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = '{TABLE}'"
            )["column_name"]
        )
        assert {"fixture_key", "entity_type", "entity_code",
                "market", "method", "value", "as_of"} <= cols


def test_append_and_snapshot_read(tmp_path: Path) -> None:
    with Warehouse(tmp_path / "t.duckdb") as wh:
        ensure_derived_schema(wh)
        df = pd.DataFrame([{
            "fixture_key": "2026-27:2026-08-21:arsenal:coventry-city",
            "season": "2026-27", "entity_type": "team", "entity_code": 3,
            "market": "clean_sheet_prob", "method": "cs_grid#power",
            "value": 0.52, "as_of": AS_OF,
        }])
        assert wh.append(TABLE, df) == 1
        assert wh.append(TABLE, df) == 0  # idempotent
        snap = wh.snapshot_at(AS_OF)
        got = snap.table(TABLE)
        assert len(got) == 1 and got.iloc[0]["value"] == pytest.approx(0.52)
        # invisible before it existed
        early = wh.snapshot_at(AS_OF - dt.timedelta(days=1)).table(TABLE)
        assert early.empty


def test_derive_fixture_rows_end_to_end(cs_payload: dict, tt_payload: dict) -> None:
    """Full offline pipeline on the real GW1 payloads: quoted rows in, derived
    priors out, with every method present and every probability in (0, 1)."""
    quotes = pd.concat(
        [
            parse_extra_market_event(cs_payload, AS_OF, "2026-27"),
            parse_extra_market_event(tt_payload, AS_OF, "2026-27"),
            # minimal 1X2 + totals consensus, fair-ish Arsenal-heavy prices
            pd.DataFrame([
                {"fixture_key": "2026-27:2026-08-21:arsenal:coventry-city",
                 "bookmaker": "bet365", "market": "h2h", "selection": s,
                 "price_decimal": p, "as_of": AS_OF}
                for s, p in [("HOME", 1.30), ("DRAW", 5.75), ("AWAY", 10.0)]
            ] + [
                {"fixture_key": "2026-27:2026-08-21:arsenal:coventry-city",
                 "bookmaker": "bet365", "market": "totals", "selection": s,
                 "price_decimal": p, "as_of": AS_OF}
                for s, p in [("OVER_2.5", 1.53), ("UNDER_2.5", 2.63)]
            ] + [
                {"fixture_key": "2026-27:2026-08-21:arsenal:coventry-city",
                 "bookmaker": "fair#scorer_power", "market": "anytime_scorer",
                 "selection": "223340", "price_decimal": 1 / 0.41, "as_of": AS_OF},
            ]),
        ],
        ignore_index=True,
    )
    rows, reason = derive_fixture_rows(
        "2026-27:2026-08-21:arsenal:coventry-city", "2026-27",
        quotes, home_code=3, away_code=9, as_of=AS_OF,
        player_team={223340: 3},
    )
    assert reason is None
    df = pd.DataFrame(rows)
    methods = set(df["method"])
    assert {"poisson_indep", "dixon_coles", "cs_grid#power",
            "team_totals", "scorer_power"} <= methods

    probs = df[df["market"].isin(["clean_sheet_prob", "anytime_prob", "xg_share"])]
    assert ((probs["value"] > 0) & (probs["value"] < 1)).all()
    lams = df[df["market"] == "team_lambda"]
    assert ((lams["value"] > 0) & (lams["value"] < 6)).all()

    # the two CS methods must be in the same ballpark for the same team
    cs = df[(df["market"] == "clean_sheet_prob") & (df["entity_code"] == 3)]
    vals = cs.set_index("method")["value"]
    assert abs(vals["cs_grid#power"] - vals["poisson_indep"]) < 0.15

    # xg_share = lambda_player / lambda_team, consistent within the frame
    share = df[(df["market"] == "xg_share")]["value"].iloc[0]
    lam_home = lams[(lams["entity_code"] == 3)
                    & (lams["method"] == "poisson_indep")]["value"].iloc[0]
    assert share == pytest.approx(-math.log(1 - 0.41) / lam_home, rel=1e-9)


def test_derive_skips_fixture_without_h2h() -> None:
    quotes = pd.DataFrame([{
        "fixture_key": "k", "bookmaker": "bet365", "market": "totals",
        "selection": "OVER_2.5", "price_decimal": 1.9, "as_of": AS_OF,
    }])
    rows, reason = derive_fixture_rows("k", "2026-27", quotes, 3, 9, AS_OF)
    assert rows == [] and reason is not None and "1X2" in reason


def test_team_code_by_slug_covers_api_long_forms() -> None:
    teams = pd.DataFrame([
        {"team_code": 43, "name": "Man City"},
        {"team_code": 3, "name": "Arsenal"},
    ])
    m = team_code_by_slug(teams)
    assert m["man-city"] == 43
    assert m["manchester-city"] == 43  # Odds API long form via alias table
    assert m["arsenal"] == 3
