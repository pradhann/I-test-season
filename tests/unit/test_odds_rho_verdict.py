"""The rho verdict, pinned to its evidence.

EVIDENCE RULE, as in ``test_rank_policy.py``: no constant reaches the
derivation layer without a citation, and the citation is a committed CSV. The
claims made in ``docs/platform/odds_derivation.md`` section 5 -- that the
clean-sheet Brier surface is flat in rho, that the likelihood maximum is
interior but insignificant, and that the independent inversion wins
out-of-sample -- are re-derived here from ``docs/platform/cs_rho_grid.csv`` and
``cs_brier.csv``. If someone re-fits rho, or edits :data:`RHO_STAR` or
:data:`PREFERRED_CS_METHOD` by hand, these fail.

The last two tests check the estimator itself rather than the fitted value:
the profile machinery must be *able* to recover a rho when one is really there
(simulated scorelines), and the truncated-grid guard must exclude the 16-cell
exchange card that the measurement in :data:`CS_GRID_MIN_CELLS` indicts.

Everything runs offline: two committed CSVs, the committed GW1 payloads, and
simulated scorelines. No warehouse, no network.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_edge.ingest.odds_derived import (
    CS_GRID_MIN_CELLS,
    PREFERRED_CS_METHOD,
    RHO_STAR,
    derive_fixture_rows,
)
from fpl_edge.ingest.odds_markets import parse_extra_market_event
from fpl_edge.models.team_goals.scoreline import GoalRates, score_matrix

ROOT = Path(__file__).parents[2]
DOCS = ROOT / "docs" / "platform"
FIX = Path(__file__).parents[1] / "fixtures" / "odds"
UTC = dt.timezone.utc
AS_OF = dt.datetime(2026, 8, 19, 18, 0, tzinfo=UTC)

#: chi-square 0.95 quantile, 1 df. Above this, rho would be a real parameter.
CHI2_95_1DF = 3.841


@pytest.fixture(scope="module")
def grid() -> pd.DataFrame:
    return pd.read_csv(DOCS / "cs_rho_grid.csv")


@pytest.fixture(scope="module")
def brier() -> pd.DataFrame:
    return pd.read_csv(DOCS / "cs_brier.csv")


# -- the grid must be wide enough to have answered the question ----------------


def test_grid_is_wide_enough_to_expose_a_boundary_hit(grid: pd.DataFrame) -> None:
    """The original fit returned its own endpoint on [-0.16, +0.04].

    A grid that stops where the reported optimum sits cannot distinguish "the
    best rho" from "the edge of where we looked", so the committed profile has
    to reach well past the Dixon-Coles literature range on both sides.
    """
    assert grid["rho"].min() <= -0.28
    assert grid["rho"].max() >= 0.28
    assert len(grid) >= 25
    assert (grid["rho"] == 0.0).any(), "rho = 0 must be on the grid to compare against"


def test_brier_surface_is_flat_in_rho(grid: pd.DataFrame) -> None:
    """Finding 1: rho is not identified by clean-sheet accuracy."""
    span = float(grid["brier"].max() - grid["brier"].min())
    at_zero = float(grid.loc[grid["rho"] == 0.0, "brier"].iloc[0])
    assert span < 5e-4, f"Brier span {span} is no longer flat; re-read the verdict"
    assert span / at_zero < 0.005  # under half a percent of the level


def test_likelihood_maximum_is_interior_and_is_RHO_STAR(grid: pd.DataFrame) -> None:
    """Finding 2a: the MLE is a real interior optimum, and it is the constant."""
    best = float(grid.loc[grid["loglik"].idxmax(), "rho"])
    assert grid["rho"].min() < best < grid["rho"].max(), "MLE sits on the boundary"
    assert best == pytest.approx(RHO_STAR, abs=1e-9)


def test_rho_is_not_significantly_different_from_zero(grid: pd.DataFrame) -> None:
    """Finding 2b: LR = 0.317 on 1 df. Nowhere near 3.841."""
    ll_max = float(grid["loglik"].max())
    ll_zero = float(grid.loc[grid["rho"] == 0.0, "loglik"].iloc[0])
    lr = 2.0 * (ll_max - ll_zero)
    assert lr >= 0.0
    assert lr < CHI2_95_1DF, (
        f"LR = {lr:.3f} now exceeds the 95% critical value; rho has become a "
        "real parameter and PREFERRED_CS_METHOD should be revisited"
    )


def test_independent_poisson_wins_out_of_sample(brier: pd.DataFrame) -> None:
    """Finding 4, and the reason PREFERRED_CS_METHOD is what it is."""
    pooled = brier[brier["season"] == "2023-25 pooled"].set_index("method")
    indep = float(pooled.loc["poisson_h2h_ou", "brier"])
    dc = pooled[pooled.index.str.startswith("dc_")]["brier"]
    assert len(dc) >= 1
    assert indep <= dc.min(), "a Dixon-Coles variant now beats rho = 0 out of sample"
    assert PREFERRED_CS_METHOD == "poisson_indep"

    # ... and both beat the inversion that ignores the totals market.
    assert indep < float(pooled.loc["poisson_h2h", "brier"])


def test_market_prior_beats_the_base_rate_but_only_modestly(brier: pd.DataFrame) -> None:
    """The skill claim in section 8: real, and small. Guards against a future
    change that quietly makes the prior worse than predicting the mean."""
    row = brier[(brier["season"] == "2023-25 pooled")
                & (brier["method"] == "poisson_h2h_ou")].iloc[0]
    skill = 1.0 - float(row["brier"]) / float(row["brier_base_rate"])
    assert 0.02 < skill < 0.15, f"Brier skill {skill:.4f} left its measured range"


def test_prior_is_biased_high_out_of_sample(brier: pd.DataFrame) -> None:
    """Section 8's calibration warning, as a test: every method over-predicts.

    Downstream consumers shade high clean sheets down because of this; if the
    bias ever flips sign, that shading becomes wrong and this fails first.
    """
    pooled = brier[brier["season"] == "2023-25 pooled"]
    assert (pooled["mean_pred"] > pooled["realized_rate"]).all()


# -- the estimator itself, on data where the answer is known -------------------


def _backtest_module():
    """Import the script by path; ``scripts/`` is not a package."""
    path = ROOT / "scripts" / "backtest_clean_sheets.py"
    spec = importlib.util.spec_from_file_location("_backtest_cs", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_profile_recovers_a_rho_that_is_really_there() -> None:
    """A flat surface must mean "no signal", not "blind estimator".

    Simulate scorelines from a matrix with a strong rho = -0.15, quote the
    match back as fair 1X2 + totals prices, and check the scoreline
    log-likelihood profile peaks on the correct side of zero. Without this,
    the flatness finding could just be a broken likelihood.
    """
    mod = _backtest_module()
    rng = np.random.default_rng(20260820)
    true_rho = -0.15
    rates = GoalRates(1.55, 1.20, true_rho)
    mat = score_matrix(rates, 10)

    flat = mat.ravel() / mat.sum()
    draws = rng.choice(flat.size, size=4000, p=flat)
    fthg, ftag = np.divmod(draws, mat.shape[1])

    # fair prices for the *same* match, repeated per simulated scoreline
    h = float(np.tril(mat, -1).sum())
    d = float(np.trace(mat))
    a = float(np.triu(mat, 1).sum())
    idx = np.arange(mat.shape[0])
    over = float(mat[(idx[:, None] + idx[None, :]) > 2.5].sum())
    df = pd.DataFrame({
        "season": "sim", "FTHG": fthg, "FTAG": ftag,
        "AvgCH": 1 / h, "AvgCD": 1 / d, "AvgCA": 1 / a,
        "AvgC>2.5": 1 / over, "AvgC<2.5": 1 / (1 - over),
    })

    lls = {rho: mod.run_method(df, use_ou=True, rho=rho)[1]
           for rho in (-0.25, -0.15, -0.05, 0.0, 0.10)}
    best = max(lls, key=lls.get)
    assert best < 0.0, f"profile picked {best} from truly rho={true_rho}: {lls}"
    assert lls[true_rho] > lls[0.0], "the true rho must beat independence here"


def test_run_method_scores_clean_sheets_and_likelihood_off_one_matrix() -> None:
    """Both objectives must describe the same matrix, or they are not
    comparable -- which is the entire question section 5 answers."""
    mod = _backtest_module()
    rates = GoalRates(1.4, 1.1)
    mat = score_matrix(rates, 10)
    h = float(np.tril(mat, -1).sum())
    d = float(np.trace(mat))
    a = float(np.triu(mat, 1).sum())
    idx = np.arange(mat.shape[0])
    over = float(mat[(idx[:, None] + idx[None, :]) > 2.5].sum())
    df = pd.DataFrame([{
        "season": "sim", "FTHG": 1, "FTAG": 0,
        "AvgCH": 1 / h, "AvgCD": 1 / d, "AvgCA": 1 / a,
        "AvgC>2.5": 1 / over, "AvgC<2.5": 1 / (1 - over),
    }])
    preds, ll = mod.run_method(df, use_ou=True, rho=0.0)
    assert len(preds) == 2  # one row per side
    assert preds.set_index("side").loc["home", "real"] == 1  # away failed to score
    assert preds.set_index("side").loc["away", "real"] == 0
    assert ll == pytest.approx(float(np.log(mat[1, 0])), abs=1e-6)


# -- the truncated-grid guard --------------------------------------------------


def test_truncated_exchange_grid_is_excluded_from_cs_grid() -> None:
    """The 16-cell Betfair card missed Over 2.5 by a signed -6.2pp on GW1.

    So the pipeline must derive ``cs_grid`` from William Hill's 44 cells only,
    and must produce no cs_grid row at all if that card is absent -- silently
    falling back to a truncated grid would be worse than having no row.
    """
    payload = json.loads((FIX / "odds_api_correct_score_btts.json").read_text())
    key = "2026-27:2026-08-21:arsenal:coventry-city"
    anchor = pd.DataFrame(
        [{"fixture_key": key, "bookmaker": "bet365", "market": "h2h",
          "selection": s, "price_decimal": p, "as_of": AS_OF}
         for s, p in [("HOME", 1.30), ("DRAW", 5.75), ("AWAY", 10.0)]]
        + [{"fixture_key": key, "bookmaker": "bet365", "market": "totals",
            "selection": s, "price_decimal": p, "as_of": AS_OF}
           for s, p in [("OVER_2.5", 1.53), ("UNDER_2.5", 2.63)]]
    )
    quotes = pd.concat(
        [parse_extra_market_event(payload, AS_OF, "2026-27"), anchor],
        ignore_index=True,
    )
    cells = quotes[quotes["market"] == "correct_score"].groupby("bookmaker").size()
    assert cells["betfair_ex_uk"] < CS_GRID_MIN_CELLS <= cells["williamhill"]

    rows, reason = derive_fixture_rows(key, "2026-27", quotes, 3, 9, AS_OF)
    assert reason is None
    df = pd.DataFrame(rows)
    grid_rows = df[df["method"] == "cs_grid#power"]
    assert len(grid_rows) == 2  # one per team, from William Hill alone

    # the same call with only the exchange card must produce no cs_grid row
    only_ex = quotes[
        (quotes["market"] != "correct_score") | (quotes["bookmaker"] == "betfair_ex_uk")
    ]
    rows2, _ = derive_fixture_rows(key, "2026-27", only_ex, 3, 9, AS_OF)
    assert not [r for r in rows2 if r["method"] == "cs_grid#power"]
