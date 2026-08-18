"""Shared plumbing for everything implementing :class:`TeamStrengthModel`.

The Dixon-Coles fit, the market-implied baseline and the two naive baselines
differ only in how they produce a pair of goal rates for a fixture. Everything
after that -- build the joint matrix, take its marginals, emit the contract
frame -- is identical and lives here, so the four models are compared on the
one thing that actually differs between them.

The important invariant this base class enforces: ``p_clean_sheet`` and
``exp_goals_*`` in the output frame are read *out of* the cached score matrix,
never computed in parallel with it. ``score_matrix(fixture_id)`` therefore
cannot disagree with ``predict()``, and the test suite asserts the equality
rather than trusting it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import TEAM_STRENGTH_COLUMNS, ModelCard
from fpl_edge.models.team_goals.data import read_target_fixtures
from fpl_edge.models.team_goals.scoreline import (
    DEFAULT_MAX_GOALS,
    GoalRates,
    clean_sheet_probs,
    expected_goals,
    score_matrix,
)
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Season


class UnknownFixtureError(KeyError):
    """Raised when ``score_matrix`` is asked about a fixture never predicted."""


class BaseGoalModel(ABC):
    """Turns per-fixture goal rates into the team-strength contract frame."""

    card: ModelCard

    def __init__(self, *, max_goals: int = DEFAULT_MAX_GOALS) -> None:
        self.max_goals = max_goals
        self._rates: dict[int, GoalRates] = {}
        self._matrices: dict[int, np.ndarray] = {}
        # FPL reuses fixture_id across seasons -- 2024-25 and 2025-26 both number
        # their fixtures 1..380 -- while the contract's score_matrix(fixture_id)
        # takes no season. The cache is therefore scoped to the season of the
        # last predict() call and cleared when that changes, so a stale matrix
        # from another season can never be served under a colliding id.
        self._cached_season: str | None = None

    # -- subclass hook -------------------------------------------------------

    @abstractmethod
    def rates_for(
        self, snapshot: Snapshot, season: Season, fixtures: pd.DataFrame
    ) -> dict[int, GoalRates]:
        """Goal rates keyed by ``fixture_id`` for the given upcoming fixtures."""

    # -- contract ------------------------------------------------------------

    def predict(self, snapshot: Snapshot, season: Season, gws: list[GwId]) -> pd.DataFrame:
        fixtures = read_target_fixtures(snapshot, str(season), [int(g) for g in gws])
        if fixtures.empty:
            return pd.DataFrame(columns=list(TEAM_STRENGTH_COLUMNS))
        if self._cached_season != str(season):
            self._rates.clear()
            self._matrices.clear()
            self._cached_season = str(season)
        rates = self.rates_for(snapshot, season, fixtures)
        rows: list[dict[str, object]] = []
        for fx in fixtures.itertuples(index=False):
            fid = int(fx.fixture_id)
            if fid not in rates:
                continue
            mat = score_matrix(rates[fid], self.max_goals)
            self._rates[fid] = rates[fid]
            self._matrices[fid] = mat
            xg_home, xg_away = expected_goals(mat)
            cs_home, cs_away = clean_sheet_probs(mat)
            rows.append(
                {
                    "fixture_id": fid,
                    "gw": int(fx.gw),
                    "team_code": int(fx.home_team_code),
                    "opponent_code": int(fx.away_team_code),
                    "is_home": True,
                    "exp_goals_for": xg_home,
                    "exp_goals_against": xg_away,
                    "p_clean_sheet": cs_home,
                }
            )
            rows.append(
                {
                    "fixture_id": fid,
                    "gw": int(fx.gw),
                    "team_code": int(fx.away_team_code),
                    "opponent_code": int(fx.home_team_code),
                    "is_home": False,
                    "exp_goals_for": xg_away,
                    "exp_goals_against": xg_home,
                    "p_clean_sheet": cs_away,
                }
            )
        return pd.DataFrame(rows, columns=list(TEAM_STRENGTH_COLUMNS))

    def score_matrix(self, fixture_id: int, max_goals: int = DEFAULT_MAX_GOALS) -> np.ndarray:
        """Joint ``P(home = i, away = j)`` for a fixture seen by ``predict``.

        Scoped to the season of the most recent ``predict`` call; see the note
        in ``__init__`` on fixture_id reuse across seasons.

        Refuses unknown fixtures instead of returning a league-average matrix:
        a silently plausible matrix for a fixture the model never rated is a
        much worse failure than a traceback.
        """
        fid = int(fixture_id)
        if fid not in self._rates:
            raise UnknownFixtureError(
                f"fixture {fid} has not been predicted; call predict() first "
                f"(known: {sorted(self._rates)[:8]}{'...' if len(self._rates) > 8 else ''})"
            )
        if max_goals == self.max_goals:
            return self._matrices[fid].copy()
        return score_matrix(self._rates[fid], max_goals)

    def rates(self, fixture_id: int) -> GoalRates:
        fid = int(fixture_id)
        if fid not in self._rates:
            raise UnknownFixtureError(f"fixture {fid} has not been predicted")
        return self._rates[fid]
