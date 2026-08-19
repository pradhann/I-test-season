"""The measurements themselves.

A model card that says "it runs" is not a status. These tests assert the shipped
card carries real out-of-sample numbers, that those numbers beat both baselines,
and that re-running the evaluation on the committed fixtures reproduces them --
so a regression shows up as a failing score rather than as a green suite with a
worse forecast.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from fpl_edge.models.ownership import baselines, build_card, panel
from fpl_edge.models.ownership.backtest import loso_coldstart, loso_inseason
from fpl_edge.models.ownership.drift import fit_coldstart, fit_inseason
from fpl_edge.models.ownership.elite import EliteSample, ElitePicksSampler
from fpl_edge.models.ownership.model import MEASURED_PATH
from fpl_edge.models.ownership.simulate import simulate_field


@pytest.fixture(scope="module")
def measured() -> dict:
    return json.loads(MEASURED_PATH.read_text())


# --------------------------------------------------------------------------
# the card
# --------------------------------------------------------------------------


def test_card_carries_a_real_measured_score() -> None:
    card = build_card()
    assert card.score is not None and card.baseline_score is not None
    assert card.beats_baseline is True
    assert "persistence" in card.baseline
    assert card.trained_through is not None


def test_card_score_matches_the_committed_measurement(measured: dict) -> None:
    card = build_card()
    assert card.score == pytest.approx(measured["inseason"]["model"])
    assert card.baseline_score == pytest.approx(measured["inseason"]["persistence"])


def test_the_simulated_captaincy_caveat_survives_into_the_card() -> None:
    """Nothing downstream should be able to mistake the captaincy number for an
    empirical one."""
    notes = " ".join(build_card().notes).lower()
    assert "simulated" in notes
    assert "prior" in notes


# --------------------------------------------------------------------------
# the measurements
# --------------------------------------------------------------------------


def test_inseason_beats_both_baselines_in_every_held_out_season(measured: dict) -> None:
    rows = measured["inseason_by_season"]
    assert len(rows) == 5
    for row in rows:
        assert row["model"] < row["persistence"], row["season"]
        assert row["model"] < row["momentum"], row["season"]


def test_coldstart_beats_persistence_in_every_snapshot(measured: dict) -> None:
    for row in measured["coldstart_by_snapshot"]:
        assert row["model"] < row["persistence"], row


def test_intervals_are_calibrated(measured: dict) -> None:
    """A point forecast with a dishonest width is unusable for a rank objective."""
    for block in ("inseason", "coldstart"):
        cover = measured[block]["coverage"]
        assert cover["0.50"] == pytest.approx(0.50, abs=0.06), block
        assert cover["0.80"] == pytest.approx(0.80, abs=0.06), block
        assert cover["0.95"] == pytest.approx(0.95, abs=0.04), block


def test_inseason_loso_reproduces_the_committed_number(measured: dict) -> None:
    score, _ = loso_inseason(panel.attach_field_size(panel.load_inseason_panel()))
    assert score.model == pytest.approx(measured["inseason"]["model"], rel=1e-6)
    assert score.persistence == pytest.approx(measured["inseason"]["persistence"], rel=1e-6)


def test_coldstart_loso_reproduces_the_committed_number(measured: dict) -> None:
    score, _ = loso_coldstart(panel.load_coldstart_pairs())
    assert score.model == pytest.approx(measured["coldstart"]["model"], rel=1e-6)


def test_near_deadline_is_where_the_forecast_actually_operates(measured: dict) -> None:
    """Today is inside T-4 days of GW1, so this is the number that applies."""
    near = measured["coldstart_near_deadline"]
    assert near["n_snapshots"] >= 3
    assert near["model"] < near["persistence"]
    assert near["model"] < near["momentum"]
    assert near["model_own_ge_1pct"] < near["persistence_own_ge_1pct"]


def test_gw1_uncertainty_grows_with_the_horizon(measured: dict) -> None:
    """The forecast must not claim a three-day-out number is a one-day-out one."""
    curve = measured["gw1_uncertainty"]
    near = [r for r in curve if r["days_to_deadline"] <= 5 and r["band"] == ">15%"]
    far = [r for r in curve if r["days_to_deadline"] >= 11 and r["band"] == ">15%"]
    assert np.mean([r["mean_abs_move_pp"] for r in near]) < np.mean(
        [r["mean_abs_move_pp"] for r in far]
    )


# --------------------------------------------------------------------------
# fixtures and their identities
# --------------------------------------------------------------------------


def test_panel_ownership_respects_the_squad_size_identity() -> None:
    """Field size is derived from sum(selected)/15, so ownership must sum near 15.

    Not exactly 15 here: the committed panel drops players below 0.1% ownership.
    """
    df = panel.load_inseason_panel()
    totals = df.groupby(["season", "GW"])["own"].sum()
    assert (totals > 14.0).all()
    assert (totals <= 15.0 + 1e-6).all()


def test_coldstart_snapshots_are_all_before_their_deadline() -> None:
    pairs = panel.load_coldstart_pairs()
    assert (pairs["days"] > 0).all()
    assert pairs["season"].nunique() == 4


def test_fixture_manifest_pins_its_upstream_revisions() -> None:
    m = panel.manifest()
    revs = m["coldstart_pairs"]["revisions"]
    assert revs
    for season, entries in revs.items():
        for entry in entries:
            assert len(entry["sha"]) == 40, (season, entry)


def test_fits_are_deterministic() -> None:
    df = panel.attach_field_size(panel.load_inseason_panel())
    assert fit_inseason(df).coef == fit_inseason(df).coef
    pairs = panel.load_coldstart_pairs()
    assert fit_coldstart(pairs).coef_near == fit_coldstart(pairs).coef_near


# --------------------------------------------------------------------------
# baselines and the simulator
# --------------------------------------------------------------------------


def test_persistence_baseline_changes_nothing() -> None:
    own = np.array([0.5, 0.2])
    assert np.array_equal(baselines.persistence(own), own)


def test_transfer_momentum_adds_the_flow() -> None:
    got = baselines.transfer_momentum(np.array([0.5]), np.array([0.02]))
    assert got[0] == pytest.approx(0.52)


def test_drift_momentum_extrapolates_linearly() -> None:
    got = baselines.drift_momentum(np.array([0.52]), np.array([0.50]), 2.0, 3.0)
    assert got[0] == pytest.approx(0.55)


def test_drift_momentum_refuses_a_zero_window() -> None:
    with pytest.raises(ValueError):
        baselines.drift_momentum(np.array([0.5]), np.array([0.5]), 0.0, 1.0)


def test_captaincy_proportional_baseline_sums_to_one() -> None:
    got = baselines.captaincy_proportional(np.array([0.5, 0.3, 0.2]))
    assert got.sum() == pytest.approx(1.0)


def test_simulated_field_is_seeded_and_internally_consistent() -> None:
    own = np.array([0.60, 0.40, 0.30, 0.20, 0.10] * 6)
    own = own * (15.0 / own.sum())
    price = np.linspace(140, 40, own.size)
    pos = np.array([4, 3, 2, 3, 1] * 6)
    a = simulate_field(own, price, pos, n_managers=800, seed=7)
    b = simulate_field(own, price, pos, n_managers=800, seed=7)
    assert np.array_equal(a.captaincy, b.captaincy)
    assert a.captaincy.sum() == pytest.approx(1.0)
    assert a.start_share.sum() == pytest.approx(11.0, abs=1e-9)
    assert a.ownership.sum() == pytest.approx(15.0, abs=1e-9)
    assert (a.captaincy <= a.start_share + 1e-12).all()


def test_a_different_seed_gives_a_different_field() -> None:
    own = np.array([0.60, 0.40, 0.30, 0.20, 0.10] * 6)
    own = own * (15.0 / own.sum())
    price = np.linspace(140, 40, own.size)
    pos = np.array([4, 3, 2, 3, 1] * 6)
    a = simulate_field(own, price, pos, n_managers=800, seed=7)
    c = simulate_field(own, price, pos, n_managers=800, seed=8)
    assert not np.array_equal(a.captaincy, c.captaincy)


# --------------------------------------------------------------------------
# the elite sampler, offline
# --------------------------------------------------------------------------


class _FakeFetched:
    def __init__(self, body: object) -> None:
        self.body = body


class _FakeFetcher:
    """Replays the shape the live API returns, including its refusals."""

    def __init__(self, standings: list[dict], picks: dict[int, list[dict]]) -> None:
        self.standings = standings
        self.picks = picks
        self.calls: list[str] = []

    def get_json(self, endpoint: str, params: dict | None = None) -> _FakeFetched:
        self.calls.append(endpoint)
        if "standings" in endpoint:
            page = int((params or {}).get("page_standings", 1))
            chunk = self.standings[(page - 1) * 50: page * 50]
            return _FakeFetched({"standings": {"results": chunk,
                                               "has_next": page * 50 < len(self.standings)}})
        entry = int(endpoint.split("/")[1])
        if entry not in self.picks:
            raise RuntimeError("404")
        return _FakeFetched({"picks": self.picks[entry]})


def _squad(captain: int) -> list[dict]:
    return [
        {"element": e, "multiplier": 2 if e == captain else (1 if i < 11 else 0),
         "is_captain": e == captain, "is_vice_captain": False}
        for i, e in enumerate(range(1, 16))
    ]


def test_sampler_aggregates_picks_into_cohort_shares() -> None:
    standings = [{"entry": i} for i in range(1, 5)]
    picks = {1: _squad(3), 2: _squad(3), 3: _squad(5), 4: _squad(3)}
    sampler = ElitePicksSampler(_FakeFetcher(standings, picks), delay_s=0.0)
    sample = sampler.sample(gw=1, n_entries=4)
    assert sample.n == 4
    assert sample.captaincy[3] == pytest.approx(0.75)
    assert sample.captaincy[5] == pytest.approx(0.25)
    assert sample.ownership[3] == pytest.approx(1.0)
    assert sample.start_share[12] == pytest.approx(0.0)


def test_sampler_reports_its_standard_error() -> None:
    sample = EliteSample(
        gw=1, entry_ids=tuple(range(200)), ownership=None, start_share=None,
        captaincy=None, triple_captain=None, as_of=None,
    )
    assert sample.standard_error(0.5) == pytest.approx(0.03536, abs=1e-4)


def test_empty_standings_are_an_answer_not_a_failure() -> None:
    """Before the first gameweek is scored the overall league is genuinely empty.
    Retrying through that is how a model ends up inventing an elite template."""
    sampler = ElitePicksSampler(_FakeFetcher([], {}), delay_s=0.0)
    assert sampler.top_entry_ids(200) == []
    assert sampler.sample(gw=1, n_entries=200).n == 0


def test_picks_that_are_not_public_yet_are_skipped() -> None:
    standings = [{"entry": 1}, {"entry": 2}]
    sampler = ElitePicksSampler(_FakeFetcher(standings, {2: _squad(4)}), delay_s=0.0)
    sample = sampler.sample(gw=1, n_entries=2)
    assert sample.entry_ids == (2,)
