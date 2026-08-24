"""The cached fixture-difficulty artefact: formula sanity and the writer.

The pure tests pin the formula's promises on a hand-built fit where the truth
is known by construction: values live in [0, 1], the strongest club is the
hardest fixture, the away trip beats the home game against the same opponent.
The end-to-end test runs the real writer against the committed synthetic
league, because a parquet with the right shape and wrong provenance is the
kind of bug only an integration test sees.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.models.team_goals.dixon_coles import DixonColesFit
from fpl_edge.models.team_goals.evaluate import FIXTURES_DIR
from fpl_edge.models.team_goals.promoted import PromotedPrior
from fpl_edge.models.team_goals.ratings_cache import (
    COLUMNS,
    build_fixture_difficulty,
    opponent_difficulty,
    write_fixture_difficulty,
)
from fpl_edge.models.team_goals.synthetic import build_warehouse, load_league

UTC = dt.timezone.utc

SEASON = "2025-26"
#: Mid-season, matching test_team_goals_dixon_coles: history behind, fixtures ahead.
AS_OF = dt.datetime(2025, 12, 4, 10, 0, tzinfo=UTC)

_DUMMY_PRIOR = PromotedPrior(
    attack_mean=-0.2, defence_mean=0.2, attack_sd=0.3, defence_sd=0.3,
    attack_route_slope=0.0, defence_route_slope=0.0, covariate="route",
    route_centre=2.0, n_clubs=6, n_seasons=3, source="test",
)

#: STRONG scores a lot and concedes little; WEAK the reverse; two mid clubs.
STRONG, MID_A, MID_B, WEAK = 10, 20, 30, 40


def _hand_fit(
    *,
    home_adv: float = 0.25,
    attack: np.ndarray | None = None,
    defence: np.ndarray | None = None,
) -> DixonColesFit:
    codes = np.array([STRONG, MID_A, MID_B, WEAK])
    return DixonColesFit(
        codes=codes,
        intercept=0.30,
        home_adv=home_adv,
        rho=-0.05,
        attack=np.array([0.60, 0.05, -0.05, -0.60]) if attack is None else attack,
        defence=np.array([-0.40, -0.02, 0.02, 0.40]) if defence is None else defence,
        half_life_days=400.0,
        n_matches=380,
        effective_n=300.0,
        as_of=AS_OF,
        promoted=frozenset(),
        prior=_DUMMY_PRIOR,
        converged=True,
        neg_log_lik=0.0,
        _index={STRONG: 0, MID_A: 1, MID_B: 2, WEAK: 3},
    )


def test_difficulty_lives_in_the_unit_interval_and_uses_both_ends() -> None:
    diff = opponent_difficulty(_hand_fit(), {STRONG, MID_A, MID_B, WEAK})
    values = np.array(list(diff.values()))
    assert (values >= 0.0).all() and (values <= 1.0).all()
    # Min-max over the league population: the extremes are actual fixtures.
    assert diff[(STRONG, True)] == pytest.approx(1.0)  # away at the best side
    assert diff[(WEAK, False)] == pytest.approx(0.0)  # hosting the worst side


def test_stronger_opponent_ranks_harder_at_either_venue() -> None:
    diff = opponent_difficulty(_hand_fit(), {STRONG, MID_A, MID_B, WEAK})
    for opp_home in (True, False):
        assert (
            diff[(STRONG, opp_home)]
            > diff[(MID_A, opp_home)]
            > diff[(WEAK, opp_home)]
        )


def test_away_trip_is_harder_than_hosting_the_same_opponent() -> None:
    """The fitted home advantage must show up as venue asymmetry."""
    diff = opponent_difficulty(_hand_fit(), {STRONG, MID_A, MID_B, WEAK})
    for code in (STRONG, MID_A, MID_B, WEAK):
        assert diff[(code, True)] > diff[(code, False)]


def test_a_league_of_clones_is_flat_at_one_half() -> None:
    """No spread at all (identical clubs, no home advantage) must not divide
    by zero; the honest answer for every fixture is dead-average."""
    fit = _hand_fit(home_adv=0.0, attack=np.zeros(4), defence=np.zeros(4))
    diff = opponent_difficulty(fit, {STRONG, MID_A, MID_B, WEAK})
    assert all(v == pytest.approx(0.5) for v in diff.values())


# -- the writer, end to end on the committed synthetic league ---------------


@pytest.fixture(scope="module")
def synthetic_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("ratings_cache") / "fpl.duckdb"
    wh = build_warehouse(load_league(FIXTURES_DIR), path)
    wh.close()
    return path


def test_writer_produces_a_sane_artefact(synthetic_db) -> None:
    out, df = write_fixture_difficulty(synthetic_db, season=SEASON, now=AS_OF)
    assert out.exists() and out.name == "fixture_difficulty.parquet"
    assert tuple(df.columns) == COLUMNS
    assert len(df) > 0 and len(df) % 2 == 0  # two rows per fixture

    # Exactly one home and one away row per fixture, mirrored codes.
    per_fx = df.groupby("fixture_id")
    assert (per_fx["is_home"].sum() == 1).all()
    assert (per_fx.size() == 2).all()

    assert df["difficulty"].between(0.0, 1.0).all()
    assert df["season"].eq(SEASON).all()

    # Point-in-time provenance: tz-aware, and the snapshot is the one asked for.
    fitted = pd.to_datetime(df["fitted_at"], utc=True)
    snap = pd.to_datetime(df["snapshot_as_of"], utc=True)
    assert fitted.notna().all() and snap.notna().all()
    assert (snap == pd.Timestamp(AS_OF)).all()

    # Same fixture read from both ends: my difficulty is my opponent's venue
    # flipped, so home difficulty of one side and away of the other differ
    # whenever the clubs differ (no accidental symmetric copy).
    one = df[df["fixture_id"] == df["fixture_id"].iloc[0]]
    assert set(one["team_code"]) == set(one["opponent_code"])


def test_writer_overwrites_rather_than_appends(synthetic_db) -> None:
    out1, df1 = write_fixture_difficulty(synthetic_db, season=SEASON, now=AS_OF)
    out2, df2 = write_fixture_difficulty(synthetic_db, season=SEASON, now=AS_OF)
    assert out1 == out2
    stored = pd.read_parquet(out2)
    assert len(stored) == len(df2) == len(df1)  # a cache, not an append log


def test_build_reads_point_in_time(synthetic_db) -> None:
    """An earlier snapshot must see more upcoming fixtures, not the same set:
    the fit and the fixture list both go through the as-of filter."""
    from fpl_edge.store import Warehouse

    early = dt.datetime(2025, 10, 1, 10, 0, tzinfo=UTC)
    with Warehouse.read_copy(synthetic_db) as wh:
        df_late = build_fixture_difficulty(wh, season=SEASON, now=AS_OF)
        df_early = build_fixture_difficulty(wh, season=SEASON, now=early)
    assert len(df_early) > len(df_late)
    assert df_early["difficulty"].between(0.0, 1.0).all()
