"""The forecast itself: invariants, the cold-start path, and the measured scores.

Offline and deterministic. The backtest tests read the committed fixtures, which
are derived from real seasons, so a regression in the model shows up here as a
falling score rather than as a passing test with a worse forecast.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import OWNERSHIP_COLUMNS, OwnershipModel
from fpl_edge.models.ownership import OwnershipForecaster, build_card
from fpl_edge.models.ownership import baselines, panel
from fpl_edge.models.ownership.backtest import loso_coldstart, loso_inseason
from fpl_edge.models.ownership.captaincy import CaptaincyParams
from fpl_edge.models.ownership.drift import (
    HORIZON_EXPONENT,
    coldstart_predict,
    fit_coldstart,
    fit_inseason,
    inseason_predict,
)
from fpl_edge.models.ownership.elite import EliteTiltParams, elite_tilt
from fpl_edge.models.ownership.field import SQUAD_SIZE, project_to_simplex
from fpl_edge.store import Warehouse

UTC = dt.timezone.utc
SEASON = "2026-27"
DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)


# --------------------------------------------------------------------------
# a small synthetic warehouse
# --------------------------------------------------------------------------


def _seed_warehouse(wh: Warehouse, *, as_ofs: list[dt.datetime],
                    ownership: dict[int, list[float]]) -> None:
    codes = sorted(ownership)
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": 1, "deadline_utc": DEADLINE, "is_finished": False,
         "as_of": as_ofs[0]},
        {"season": SEASON, "gw": 2, "deadline_utc": DEADLINE + dt.timedelta(days=10),
         "is_finished": False, "as_of": as_ofs[0]},
    ]))
    wh.append("dim_player", pd.DataFrame([
        {"season": SEASON, "code": c, "element_id": c, "web_name": f"P{c}",
         "first_name": None, "second_name": None,
         "position": 4 if i == 0 else (3 if i % 2 else 2),
         "team_code": 1 + (i % 4), "as_of": as_ofs[0]}
        for i, c in enumerate(codes)
    ]))
    rows = []
    for j, as_of in enumerate(as_ofs):
        for i, c in enumerate(codes):
            rows.append({
                "season": SEASON, "code": c, "element_id": c,
                "price_tenths": 150 - 10 * i, "selected_by_pct": ownership[c][j],
                "status": "a", "chance_of_playing_next_round": None, "news": "",
                "news_added": None, "transfers_in_event": 0, "transfers_out_event": 0,
                "cost_change_start": 0, "as_of": as_of,
            })
    wh.append("fact_player_state", pd.DataFrame(rows))


N_PLAYERS = 120


def _template_ownership() -> dict[int, list[float]]:
    """A realistic template: exponentially decaying, summing to exactly 1500%.

    The sum is not decoration. Every squad holds 15 players, so ownership
    percentages sum to 1500 by identity, and a fixture that violates it would
    let a model pass tests it should fail.
    """
    base = np.exp(-np.arange(N_PLAYERS) / 25.0)
    base = base * (100.0 * SQUAD_SIZE / base.sum())
    assert base.max() < 100.0
    return {1000 + i: [float(base[i]), float(base[i])] for i in range(N_PLAYERS)}


@pytest.fixture()
def warehouse(tmp_path) -> Warehouse:
    early = dt.datetime(2026, 8, 17, 12, tzinfo=UTC)
    late = dt.datetime(2026, 8, 19, 12, tzinfo=UTC)
    wh = Warehouse(tmp_path / "own.duckdb")
    _seed_warehouse(wh, as_ofs=[early, late], ownership=_template_ownership())
    return wh


def _forecast(wh: Warehouse, at: dt.datetime) -> pd.DataFrame:
    return OwnershipForecaster().forecast(wh.snapshot_at(at), SEASON, 1)


# --------------------------------------------------------------------------
# contract and invariants
# --------------------------------------------------------------------------


def test_satisfies_the_ownership_model_protocol() -> None:
    assert isinstance(OwnershipForecaster(), OwnershipModel)


def test_returns_the_contract_columns(warehouse: Warehouse) -> None:
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert list(out.columns[: len(OWNERSHIP_COLUMNS)]) == list(OWNERSHIP_COLUMNS)
    assert len(out) == N_PLAYERS


def test_captaincy_sums_to_exactly_one(warehouse: Warehouse) -> None:
    """Every manager names exactly one captain. Not approximately one."""
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert out["captaincy_share"].sum() == pytest.approx(1.0, abs=1e-9)


def test_no_player_is_captained_more_than_started(warehouse: Warehouse) -> None:
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert (out["captaincy_share"] <= out["start_share"] + 1e-12).all()


def test_starting_shares_sum_to_the_starting_xi(warehouse: Warehouse) -> None:
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    expected = 11.0 * out["own_mean"].sum() / SQUAD_SIZE
    assert out["start_share"].sum() == pytest.approx(expected, abs=1e-6)


def test_total_effective_ownership_is_starters_plus_one_captain(
    warehouse: Warehouse,
) -> None:
    """Summed over the whole field, EO is 11 starters plus one armband."""
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert out["eo_overall"].sum() == pytest.approx(
        out["start_share"].sum() + 1.0, abs=1e-6
    )


def test_prediction_interval_brackets_the_mean(warehouse: Warehouse) -> None:
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert (out["own_lo"] <= out["own_mean"]).all()
    assert (out["own_hi"] >= out["own_mean"]).all()
    assert (out["own_sd"] > 0).all()


def test_uncertainty_widens_with_the_horizon(warehouse: Warehouse) -> None:
    """Three days out must be less certain than three hours out."""
    near = _forecast(warehouse, DEADLINE - dt.timedelta(hours=3))
    far = _forecast(warehouse, DEADLINE - dt.timedelta(days=2, hours=12))
    assert far["own_sd"].mean() > near["own_sd"].mean()


def test_gw1_takes_the_cold_start_path(warehouse: Warehouse) -> None:
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert out["path"].nunique() == 1
    assert out["path"].iloc[0].startswith("cold_start")


def test_top10k_is_flagged_as_a_prior_until_picks_are_sampled(
    warehouse: Warehouse,
) -> None:
    out = _forecast(warehouse, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    assert out["eo_top10k_is_prior"].all()
    measured = OwnershipForecaster(
        elite_params=EliteTiltParams(lead=1.0, concentration=1.1, measured=True,
                                     sample_size=250)
    ).forecast(warehouse.snapshot_at(dt.datetime(2026, 8, 19, 13, tzinfo=UTC)), SEASON, 1)
    assert not measured["eo_top10k_is_prior"].any()


def test_forecasting_a_past_deadline_is_refused(warehouse: Warehouse) -> None:
    with pytest.raises(ValueError, match="already past"):
        _forecast(warehouse, DEADLINE + dt.timedelta(hours=1))


def test_unavailable_players_are_forecast_to_be_sold(tmp_path) -> None:
    early = dt.datetime(2026, 8, 17, 12, tzinfo=UTC)
    late = dt.datetime(2026, 8, 19, 12, tzinfo=UTC)
    own = _template_ownership()
    wh = Warehouse(tmp_path / "flag.duckdb")
    _seed_warehouse(wh, as_ofs=[early, late], ownership=own)
    healthy = _forecast(wh, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    # now flag the fifth player as injured, later than every existing row
    wh.append("fact_player_state", pd.DataFrame([{
        "season": SEASON, "code": 1004, "element_id": 1004, "price_tenths": 110,
        "selected_by_pct": own[1004][1], "status": "i",
        "chance_of_playing_next_round": 0, "news": "knee", "news_added": None,
        "transfers_in_event": 0, "transfers_out_event": 0, "cost_change_start": 0,
        "as_of": dt.datetime(2026, 8, 19, 12, 30, tzinfo=UTC),
    }]))
    flagged = _forecast(wh, dt.datetime(2026, 8, 19, 13, tzinfo=UTC))
    before = float(healthy.loc[healthy["code"] == 1004, "own_mean"].iloc[0])
    after = float(flagged.loc[flagged["code"] == 1004, "own_mean"].iloc[0])
    assert after < before
    assert float(flagged.loc[flagged["code"] == 1004, "captaincy_share"].iloc[0]) == 0.0


def test_in_season_path_requires_a_field_size(warehouse: Warehouse) -> None:
    """Flow is a share of the field. Guessing the denominator is refused."""
    at = dt.datetime(2026, 8, 19, 13, tzinfo=UTC)
    wh = warehouse
    wh.append("fact_player_state", pd.DataFrame([{
        "season": SEASON, "code": 1000, "element_id": 1000, "price_tenths": 150,
        "selected_by_pct": 60.0, "status": "a", "chance_of_playing_next_round": None,
        "news": "", "news_added": None, "transfers_in_event": 50_000,
        "transfers_out_event": 10_000, "cost_change_start": 0,
        "as_of": dt.datetime(2026, 8, 19, 12, 30, tzinfo=UTC),
    }]))
    with pytest.raises(ValueError, match="field size"):
        OwnershipForecaster().forecast(wh.snapshot_at(at), SEASON, 2)


# --------------------------------------------------------------------------
# structure
# --------------------------------------------------------------------------


def test_simplex_projection_preserves_the_squad_size() -> None:
    x = np.exp(-np.arange(120) / 25.0)
    got = project_to_simplex(x, total=15.0)
    assert got.sum() == pytest.approx(15.0)
    assert (got <= 1.0 + 1e-12).all()


def test_simplex_projection_holds_the_upper_bound() -> None:
    """Scaling a near-saturated vector up must not create ownership above 100%."""
    x = np.array([0.95, 0.40, 0.30] + [0.01] * 40)
    got = project_to_simplex(x, total=3.0)
    assert got.sum() == pytest.approx(3.0)
    assert got.max() <= 1.0 + 1e-12


def test_simplex_projection_refuses_an_impossible_total() -> None:
    with pytest.raises(ValueError, match="cannot fit a total"):
        project_to_simplex(np.array([0.7, 0.3, 0.1, 0.02]), total=15.0)


def test_elite_tilt_stays_on_the_same_simplex() -> None:
    now = np.array([0.70, 0.30, 0.10, 0.02])
    fut = np.array([0.74, 0.28, 0.11, 0.02])
    out = elite_tilt(now, fut)
    assert out.sum() == pytest.approx(fut.sum())


def test_elite_tilt_leads_the_crowd() -> None:
    """A player the field is buying is owned harder by the cohort ahead of it."""
    now = np.array([0.20, 0.40, 0.40])
    fut = np.array([0.30, 0.35, 0.35])
    out = elite_tilt(now, fut, EliteTiltParams(lead=1.5, concentration=1.0))
    assert out[0] > fut[0]


def test_triple_captain_usage_never_exceeds_captaincy(warehouse: Warehouse) -> None:
    at = dt.datetime(2026, 8, 19, 13, tzinfo=UTC)
    out = OwnershipForecaster(
        captaincy_params=CaptaincyParams(triple_captain_usage=0.05)
    ).forecast(warehouse.snapshot_at(at), SEASON, 1)
    assert (out["triple_captain_share"] <= out["captaincy_share"] + 1e-12).all()
    assert out["triple_captain_share"].sum() == pytest.approx(0.05, abs=1e-9)


def test_horizon_exponent_is_the_measured_square_root_of_time() -> None:
    """Refit the diffusion exponent from the fixture; it must stay near 0.5."""
    pairs = panel.load_coldstart_pairs()
    days, mae = [], []
    for (_season, d), g in pairs.groupby(["season", "days"]):
        days.append(float(d))
        mae.append(float((g["own_true"] - g["own"]).abs().mean()))
    slope = np.polyfit(np.log(days), np.log(mae), 1)[0]
    assert slope == pytest.approx(HORIZON_EXPONENT, abs=0.1)


def test_drift_is_ignored_when_the_measurement_window_is_too_short(
    warehouse: Warehouse,
) -> None:
    """A twenty-minute window measures one 0.1% quantisation step. Extrapolating
    it three days forward would turn rounding into a sixteen-point swing."""
    from fpl_edge.models.ownership.model import MIN_DRIFT_WINDOW_RATIO

    at = dt.datetime(2026, 8, 19, 13, tzinfo=UTC)
    snap = warehouse.snapshot_at(at)
    model = OwnershipForecaster()
    players = snap.players(SEASON).sort_values("code").reset_index(drop=True)
    days_ahead = (DEADLINE - at).total_seconds() / 86400.0
    assert MIN_DRIFT_WINDOW_RATIO > 0
    assert model._drift_rate(snap, SEASON, players, days_ahead * 100) is None
