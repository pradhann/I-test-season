"""``fpl solve --forecast-source``: the currency forecast.parquet is denominated in.

The squad-anchored solver reads forecast.parquet; every other dashboard
surface shows the provider consensus. On 2026-09-07 the engine's own model ran
~40% hot against that consensus and sold a goalkeeper for one it rated 29.2
over five gameweeks against the providers' 12.1. These tests pin the fix: a
consensus-denominated forecast where every row comes from exactly one source
and says which -- consensus where the providers cover a (player, gw), the
engine model where they do not -- with no scaling or blending inside a row.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from fpl_edge.cli.solve import (
    FORECAST_SOURCES,
    build_forecast,
    describe_forecast,
    engine_forecast_frame,
)
from fpl_edge.ingest.projections.store import ProjectionStore
from fpl_edge.store.warehouse import Warehouse

SEASON = "2026-27"
T0 = pd.Timestamp("2026-09-01 12:00", tz="UTC")
AS_OF = pd.Timestamp("2026-09-07 18:00", tz="UTC")

#: The engine's universe: four players, three gameweeks (GW4-6).
CODES = [100, 200, 300, 400]
GWS = [4, 5, 6]
ENGINE_XPTS = np.array([
    [5.0, 5.1, 5.2],     # 100: covered by both providers in GW4-5, nobody in GW6
    [4.0, 4.1, 4.2],     # 200: covered by src_a only
    [3.0, 3.1, 3.2],     # 300: no provider covers him at all
    [2.0, 2.1, 2.2],     # 400: covered; src_b serves p_appear
])
ENGINE_PPLAY = np.array([
    [0.90, 0.91, 0.92],
    [0.80, 0.81, 0.82],
    [0.70, 0.71, 0.72],
    [0.60, 0.61, 0.62],
])

#: provider -> {(gw, code): (xp, p_appear)}; nobody projects GW6.
PROVIDERS = {
    "src_a": {(4, 100): (2.0, None), (5, 100): (2.2, None),
              (4, 200): (1.0, None), (5, 200): (1.1, None),
              (4, 400): (0.5, None), (5, 400): (0.6, None)},
    "src_b": {(4, 100): (4.0, None), (5, 100): (3.8, None),
              (4, 400): (1.5, 0.25), (5, 400): (1.4, 0.30)},
}


def _stub_problem():
    return SimpleNamespace(
        players=tuple(SimpleNamespace(code=c) for c in CODES),
        gws=tuple(GWS),
        xpts=ENGINE_XPTS,
        p_play=ENGINE_PPLAY,
    )


def _seed(path):
    wh = Warehouse(path)
    store = ProjectionStore(wh)
    rows = []
    for provider, cells in PROVIDERS.items():
        for (gw, code), (xp, p_appear) in cells.items():
            rows.append({
                "provider": provider, "season": SEASON, "gw": gw, "code": code,
                "xp": xp, "xp_if_appears": None, "p_appear": p_appear,
                "xmins": None, "as_of": T0,
            })
    frame = pd.DataFrame(rows)
    for col in ("xp", "xp_if_appears", "p_appear", "xmins"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    store.append("fact_projection", frame)
    return wh


@pytest.fixture()
def wh(tmp_path):
    w = _seed(tmp_path / "fc.duckdb")
    yield w
    w.close()


def _row(frame, code, gw):
    hit = frame[(frame["code"] == code) & (frame["gw"] == gw)]
    assert len(hit) == 1, f"expected exactly one row for {code}/{gw}, got {len(hit)}"
    return hit.iloc[0]


def test_engine_source_is_the_problems_own_arrays_labelled():
    fc, meta = build_forecast(_stub_problem(), source="engine")
    assert list(fc.columns) == ["code", "gw", "xpts", "p_play", "source",
                                "forecast_source", "p_play_source", "n_sources"]
    assert len(fc) == len(CODES) * len(GWS)
    assert set(fc["source"]) == {"engine"}
    assert set(fc["forecast_source"]) == {"engine"}
    assert _row(fc, 300, 5)["xpts"] == pytest.approx(3.1)
    assert meta["forecast_source"] == "engine"
    assert meta["rows_by_source"] == {"engine": 12}
    assert meta["engine_fill_share"] == 0.0


def test_consensus_rows_come_from_the_providers_and_gaps_from_the_engine(wh):
    fc, meta = build_forecast(
        _stub_problem(), source="consensus", wh=wh, season=SEASON, as_of=AS_OF,
    )
    # the universe is complete: every (code, gw) the problem knows, once
    assert len(fc) == len(CODES) * len(GWS)
    assert not fc.duplicated(["code", "gw"]).any()
    assert set(fc["forecast_source"]) == {"consensus"}

    # both providers on 100/GW4: the equal-weight mean, not the engine's 5.0
    r = _row(fc, 100, 4)
    assert r["xpts"] == pytest.approx(3.0)
    assert r["source"] == "consensus" and r["n_sources"] == 2

    # one provider on 200/GW4: his number verbatim, still consensus
    r = _row(fc, 200, 4)
    assert r["xpts"] == pytest.approx(1.0)
    assert r["source"] == "consensus" and r["n_sources"] == 1

    # nobody covers 300: engine_fill, the engine's own number untouched
    for gw in GWS:
        r = _row(fc, 300, gw)
        assert r["source"] == "engine_fill"
        assert r["xpts"] == pytest.approx(ENGINE_XPTS[2][GWS.index(gw)])
        assert r["p_play"] == pytest.approx(ENGINE_PPLAY[2][GWS.index(gw)])
        assert r["n_sources"] == 0

    # nobody projects GW6: the whole gameweek is engine_fill
    gw6 = fc[fc["gw"] == 6]
    assert set(gw6["source"]) == {"engine_fill"}
    assert meta["coverage_by_gw"]["6"] == {"engine_fill": 4}
    assert meta["coverage_by_gw"]["4"] == {"consensus": 3, "engine_fill": 1}

    # covered: 100/200/400 in GW4 and GW5 (6); fill: 300 x3 + GW6's other three (6)
    assert meta["rows_by_source"] == {"consensus": 6, "engine_fill": 6}
    assert meta["engine_fill_share"] == pytest.approx(0.5)


def test_no_blending_within_a_row(wh):
    """Every xpts equals ONE source's number exactly: the consensus mean or
    the engine's. Nothing in between, nothing scaled."""
    fc, _ = build_forecast(
        _stub_problem(), source="consensus", wh=wh, season=SEASON, as_of=AS_OF,
    )
    eng = engine_forecast_frame(_stub_problem())
    cons = wh.sql(
        "SELECT gw, code, xpts_mean FROM sem_projection_consensus(?) WHERE season = ?",
        [AS_OF, SEASON],
    )
    for _, r in fc.iterrows():
        e = eng[(eng["code"] == r["code"]) & (eng["gw"] == r["gw"])]["xpts"].iloc[0]
        c = cons[(cons["code"] == r["code"]) & (cons["gw"] == r["gw"])]["xpts_mean"]
        if r["source"] == "consensus":
            assert r["xpts"] == pytest.approx(float(c.iloc[0]))
            assert r["xpts"] != pytest.approx(e)
        else:
            assert c.empty
            assert r["xpts"] == pytest.approx(e)


def test_p_play_is_the_providers_p_appear_when_served_else_the_engine(wh):
    fc, meta = build_forecast(
        _stub_problem(), source="consensus", wh=wh, season=SEASON, as_of=AS_OF,
    )
    # src_b serves p_appear for 400: the mean over providers that serve it
    r = _row(fc, 400, 4)
    assert r["source"] == "consensus"
    assert r["p_play"] == pytest.approx(0.25)
    assert r["p_play_source"] == "consensus"
    # 100 is covered for xpts but no provider serves p_appear: engine minutes
    r = _row(fc, 100, 4)
    assert r["source"] == "consensus"
    assert r["p_play"] == pytest.approx(0.90)
    assert r["p_play_source"] == "engine"
    assert meta["p_play_from_consensus_rows"] == 2


def test_consensus_needs_a_warehouse_and_rejects_unknown_sources():
    with pytest.raises(ValueError, match="warehouse"):
        build_forecast(_stub_problem(), source="consensus")
    with pytest.raises(ValueError, match="forecast source"):
        build_forecast(_stub_problem(), source="blend")
    assert FORECAST_SOURCES == ("engine", "consensus", "consensus_weighted")


def test_weighted_source_with_no_fit_is_all_engine_fill(wh):
    """No earned weights -> the weighted view's xpts_mean is NULL everywhere,
    which is 'not covered', which is engine_fill -- never a silent equal mean."""
    fc, meta = build_forecast(
        _stub_problem(), source="consensus_weighted", wh=wh, season=SEASON, as_of=AS_OF,
    )
    assert set(fc["source"]) == {"engine_fill"}
    assert set(fc["forecast_source"]) == {"consensus_weighted"}
    assert meta["engine_fill_share"] == 1.0
    assert "entirely engine_fill" in describe_forecast(meta)


def test_the_describe_line_names_the_mode_and_the_fill():
    line = describe_forecast({
        "forecast_source": "consensus",
        "rows_by_source": {"consensus": 2490, "engine_fill": 10},
        "engine_fill_share": 0.004,
        "coverage_by_gw": {"4": {"consensus": 498, "engine_fill": 2}},
    })
    assert line.startswith("forecast source consensus:")
    assert "engine_fill=10" in line and "0.4%" in line


def test_the_cli_exposes_the_flag_with_engine_as_the_default():
    from fpl_edge.cli.main import app

    result = CliRunner().invoke(app, ["solve", "--help"])
    assert result.exit_code == 0
    assert "--forecast-source" in result.output
    assert "consensus_weighted" in result.output
    result = CliRunner().invoke(app, ["solve", "--forecast-source", "blend",
                                      "--forecast-only"])
    assert result.exit_code != 0
    assert "forecast-source" in result.output
