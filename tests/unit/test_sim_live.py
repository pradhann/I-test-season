"""The adapters that wire the shipped models into the simulator.

Three defects live here, all of them silent, all of them fatal to P(top 10k):

1. **One seed for 38 gameweeks.** ``SeasonSimulator`` hands every gameweek the
   same ``seed``. A points model that keys its generator on the seed alone
   returns the same draw 38 times, a season total becomes 38 copies of one
   gameweek, and every rank is deterministic given GW1.
2. **Codes in the wrong order.** The points model orders players by the
   snapshot; the simulator indexes a universe sorted by code. A silent
   mis-permutation gives every player somebody else's points.
3. **Effective ownership used as ownership.** EO counts a captain twice and
   sums to 12, not 15. Fed to the squad sampler it clips every captaincy magnet
   to 1.0: at the 2026-27 GW1 forecast Haaland's EO is 1.139 against a forecast
   ownership of 0.730, so the simulated field owns him ~100% instead of ~73%
   and every Haaland differential is priced against a field that does not exist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.contracts import ModelCard, PointsSample
from fpl_edge.sim.engine import _align_ownership
from fpl_edge.sim.field import FieldConfig, FieldModel
from fpl_edge.sim.live import FrozenOwnershipForecast, GwScopedSnapshot, MultiGwPointsModel
from fpl_edge.sim.squad import PlayerUniverse
from fpl_edge.sim.synthetic import toy_world


class _RecordingInner:
    """Stands in for DecomposedPointsModel: records the seed, returns a draw."""

    def __init__(self, codes: np.ndarray) -> None:
        self.codes = np.asarray(codes)
        self.seeds: list[int] = []
        self.card = ModelCard(name="fake", approach="", baseline="", metric="")

    def simulate(self, snapshot, season, gw, *, n_sims=100, seed=0):
        self.seeds.append(int(seed))
        rng = np.random.default_rng(int(seed))
        pts = rng.integers(0, 12, size=(len(self.codes), n_sims))
        return PointsSample(codes=self.codes, gw=gw, points=pts,
                            minutes=np.full((len(self.codes), n_sims), 90))


def _universe(codes) -> PlayerUniverse:
    codes = np.asarray(codes, dtype=np.int64)
    n = len(codes)
    return PlayerUniverse(
        codes=codes,
        position=np.array([1, 2, 3, 4] * (n // 4 + 1), dtype=np.int8)[:n],
        team_code=np.arange(n, dtype=np.int64) % 5 + 100,
        price_tenths=np.full(n, 50, dtype=np.int64),
        web_name=np.array([f"P{c}" for c in codes], dtype=object),
    )


# ---------------------------------------------------------------------------
# per-gameweek seeding
# ---------------------------------------------------------------------------


def test_each_gameweek_gets_its_own_seed() -> None:
    u = _universe([10, 20, 30, 40])
    inner = _RecordingInner(u.codes)
    model = MultiGwPointsModel(inner=inner, universe=u)
    for gw in (1, 2, 3, 38):
        model.simulate(None, "2026-27", gw, n_sims=8, seed=7)
    assert len(set(inner.seeds)) == 4, (
        f"38 gameweeks were handed the same seed {inner.seeds}; a season total "
        "would then be 38 copies of one gameweek"
    )


def test_the_same_gameweek_and_seed_still_reproduce_exactly() -> None:
    u = _universe([10, 20, 30, 40])
    model = MultiGwPointsModel(inner=_RecordingInner(u.codes), universe=u)
    a = model.simulate(None, "2026-27", 5, n_sims=16, seed=3)
    b = model.simulate(None, "2026-27", 5, n_sims=16, seed=3)
    assert np.array_equal(a.points, b.points)


def test_different_gameweeks_produce_different_draws() -> None:
    u = _universe([10, 20, 30, 40])
    model = MultiGwPointsModel(inner=_RecordingInner(u.codes), universe=u)
    a = model.simulate(None, "2026-27", 1, n_sims=64, seed=3)
    b = model.simulate(None, "2026-27", 2, n_sims=64, seed=3)
    assert not np.array_equal(a.points, b.points)


# ---------------------------------------------------------------------------
# code alignment
# ---------------------------------------------------------------------------


def test_draws_are_permuted_into_universe_order() -> None:
    """The inner model's ordering is not the universe's, and must not be assumed."""
    u = _universe([10, 20, 30, 40])
    inner = _RecordingInner(np.array([40, 10, 30, 20]))
    model = MultiGwPointsModel(inner=inner, universe=u)
    out = model.simulate(None, "2026-27", 1, n_sims=32, seed=1)
    assert np.array_equal(out.codes, u.codes)

    raw = np.random.default_rng(1 + 1_000_003).integers(0, 12, size=(4, 32))
    for i, code in enumerate(u.codes):
        j = int(np.flatnonzero(inner.codes == code)[0])
        assert np.array_equal(out.points[i], raw[j]), f"player {code} got another's points"


def test_a_player_missing_from_the_sample_is_an_error_not_a_zero_row() -> None:
    u = _universe([10, 20, 30, 40])
    model = MultiGwPointsModel(inner=_RecordingInner(np.array([10, 20, 30])), universe=u)
    with pytest.raises(ValueError, match="absent from the points sample"):
        model.simulate(None, "2026-27", 1, n_sims=8, seed=1)


# ---------------------------------------------------------------------------
# gameweek-scoped snapshot
# ---------------------------------------------------------------------------


class _FakeSnapshot:
    as_of = "sentinel"

    def upcoming_fixtures(self, season, *, horizon_gws=None):
        fx = pd.DataFrame({"fixture_id": [1, 2, 3], "gw": [1, 2, 3]})
        return fx if horizon_gws is None else fx.head(horizon_gws)

    def players(self, season):
        return pd.DataFrame({"code": [1]})


def test_gw_scoped_snapshot_returns_only_that_gameweek() -> None:
    scoped = GwScopedSnapshot(_FakeSnapshot(), 3)
    # The horizon argument the inner model passes is ignored: it exists to mean
    # "the next gameweek", which is the wrong gameweek for every fold but one.
    got = scoped.upcoming_fixtures("2026-27", horizon_gws=1)
    assert got["gw"].tolist() == [3]


def test_gw_scoped_snapshot_passes_everything_else_through() -> None:
    scoped = GwScopedSnapshot(_FakeSnapshot(), 2)
    assert scoped.as_of == "sentinel"
    assert scoped.players("2026-27")["code"].tolist() == [1]


# ---------------------------------------------------------------------------
# ownership vs effective ownership
# ---------------------------------------------------------------------------


def _frame(codes, own, eo, cap):
    return pd.DataFrame({
        "code": codes, "gw": 1, "eo_overall": eo, "captaincy_share": cap,
        "eo_top10k": eo, "own_mean": own,
    })


def test_the_sampler_is_given_ownership_not_effective_ownership() -> None:
    u = _universe([10, 20, 30, 40])
    df = _frame(u.codes, own=[0.73, 0.20, 0.10, 0.05],
                eo=[1.139, 0.21, 0.10, 0.05], cap=[0.57, 0.01, 0.0, 0.0])
    got = _align_ownership(df, u)
    assert got.tolist() == pytest.approx([0.73, 0.20, 0.10, 0.05])


def test_a_bare_contract_frame_falls_back_to_effective_ownership() -> None:
    """A model that emits only OWNERSHIP_COLUMNS has nothing better to offer."""
    u = _universe([10, 20, 30, 40])
    df = _frame(u.codes, own=[0.73, 0.20, 0.10, 0.05],
                eo=[0.73, 0.20, 0.10, 0.05], cap=[0.5, 0.0, 0.0, 0.0])
    got = _align_ownership(df.drop(columns=["own_mean"]), u)
    assert got.tolist() == pytest.approx([0.73, 0.20, 0.10, 0.05])


def test_feeding_effective_ownership_to_the_field_really_does_saturate_it() -> None:
    """The defect this guards is not hypothetical; here it is, measured.

    A captaincy magnet on 73% ownership has EO above 1. The sampler clips
    inclusion probabilities to 1, so passing EO makes the simulated field own
    him essentially universally -- and a 27% differential becomes a 0% one.
    """
    u, _model, eo_toy, _cap, xp = toy_world(n_teams=4, per_team=16, seed=0)
    field = FieldModel(u, FieldConfig(n_rivals=400))
    ownership = eo_toy.copy()
    magnet = int(np.argmax(ownership))
    ownership[magnet] = 0.73
    effective = ownership.copy()
    effective[magnet] = 1.139

    from_ownership = field.target_ownership(ownership)[magnet]
    from_eo = field.target_ownership(effective)[magnet]
    # 0.73 forecast ownership becomes a ~0.99 sampled one: the 27% of the field
    # who do not own him disappear, and with them the whole differential.
    assert from_ownership < 0.80
    assert from_eo > 0.95
    assert from_eo - from_ownership > 0.20
    assert field.ownership_renormalisation(effective)["n_saturated"] >= 1
    assert field.ownership_renormalisation(ownership)["n_saturated"] == 0


# ---------------------------------------------------------------------------
# frozen ownership forecast
# ---------------------------------------------------------------------------


def test_frozen_forecast_relabels_the_gameweek_without_changing_the_numbers() -> None:
    u = _universe([10, 20, 30, 40])
    df = _frame(u.codes, own=[0.7, 0.2, 0.1, 0.05],
                eo=[1.1, 0.2, 0.1, 0.05], cap=[0.4, 0.0, 0.0, 0.0])
    card = ModelCard(name="x", approach="", baseline="", metric="")
    frozen = FrozenOwnershipForecast(frame=df, card=card, forecast_gw=1)
    out = frozen.forecast(None, "2026-27", 17)
    assert out["gw"].unique().tolist() == [17]
    assert out["own_mean"].tolist() == df["own_mean"].tolist()


def test_frozen_forecast_card_admits_it_is_frozen() -> None:
    u = _universe([10, 20, 30, 40])
    df = _frame(u.codes, [0.7, 0.2, 0.1, 0.05], [1.1, 0.2, 0.1, 0.05], [0.4, 0.0, 0.0, 0.0])
    card = ModelCard(name="x", approach="", baseline="", metric="",
                     notes=("held for every later gameweek",))
    frozen = FrozenOwnershipForecast(frame=df, card=card, forecast_gw=1)
    assert any("held" in n for n in frozen.card.notes)
