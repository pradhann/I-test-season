"""Decomposed, correlated points simulation.

Points are not regressed on directly. Each fixture is simulated as a match:

1. Draw a scoreline from the team model's joint score matrix.
2. Draw each player's minutes bucket from the minutes model.
3. Allocate the team's goals and assists among the players who were on the
   pitch, weighted by their per-90 rates and their sampled minutes.
4. Derive clean sheets, concessions, saves, defensive contributions and cards.
5. Score BPS for all 22+ players and award 3/2/1 bonus by rank *within the
   fixture*.
6. Convert the stat line to points with the validated scoring map.

The correlation this produces is the entire point. Two Arsenal defenders keeping
a clean sheet is one event, not two independent ones; a Haaland haul and a City
clean sheet come from the same scoreline draw. A model that samples players
independently understates squad variance badly and will systematically misprice
every differential and every captaincy call, which is precisely the quantity
this project exists to get right.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.models.contracts import ModelCard, PointsSample
from fpl_edge.models.points.bps import allocate_bonus, bps_from_events
from fpl_edge.models.points.scoring_map import points_from_events
from fpl_edge.models.points.shares import PlayerRates
from fpl_edge.store import Snapshot
from fpl_edge.types import GwId, Position, Season

#: Share of goals that are credited with an assist. Measured from the archive
#: rather than assumed; see tests/unit/test_points_model.py.
ASSIST_RATE = 0.74


@dataclass
class DecomposedPointsModel:
    """Composes a team goal model and a minutes model into player point draws."""

    goal_model: object          # TeamStrengthModel
    minutes_model: object       # MinutesModel
    rates: PlayerRates
    card: ModelCard = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.card is None:
            self.card = ModelCard(
                name="decomposed_points",
                approach="Dixon-Coles scoreline x minutes distribution x per-90 shares, "
                         "with joint BPS ranking for bonus",
                baseline="FPL's own ep_next",
                metric="CRPS",
            )

    def simulate(
        self,
        snapshot: Snapshot,
        season: Season,
        gw: GwId,
        *,
        n_sims: int = 5_000,
        seed: int = 0,
    ) -> PointsSample:
        rng = np.random.default_rng(seed)

        players = snapshot.selectable(season)
        if players.empty:
            raise ValueError(f"no selectable players for {season} at {snapshot.as_of}")

        fixtures = snapshot.upcoming_fixtures(season, horizon_gws=1)
        fixtures = fixtures[fixtures["gw"] == gw]
        if fixtures.empty:
            raise ValueError(f"no fixtures for {season} GW{gw} at {snapshot.as_of}")

        # The goal model populates its per-fixture cache in predict(); it
        # deliberately refuses to invent a score matrix for a fixture it has not
        # been asked about, so this call is required rather than incidental.
        self.goal_model.predict(snapshot, season, [gw])
        minutes = self.minutes_model.predict(snapshot, season, [gw])
        mins_by_code = {int(r.code): r for r in minutes.itertuples()}

        codes = players["code"].to_numpy(dtype=int)
        index_of = {int(c): i for i, c in enumerate(codes)}
        n = len(codes)
        points = np.zeros((n, n_sims), dtype=np.int64)
        sampled_minutes = np.zeros((n, n_sims), dtype=np.int64)

        pos_of = dict(zip(players["code"], players["position"]))
        team_of = dict(zip(players["code"], players["team_code"]))
        rate_frame = self.rates.for_codes(codes, players["position"].to_numpy())

        for fx in fixtures.itertuples():
            matrix = self.goal_model.score_matrix(int(fx.fixture_id))
            gh, ga = _sample_scoreline(matrix, n_sims, rng)

            for team_code, scored, conceded, is_home in (
                (int(fx.home_team_code), gh, ga, True),
                (int(fx.away_team_code), ga, gh, False),
            ):
                squad = [c for c in codes if team_of.get(c) == team_code]
                if not squad:
                    continue
                self._simulate_team(
                    squad=squad, index_of=index_of, pos_of=pos_of,
                    rate_frame=rate_frame, mins_by_code=mins_by_code,
                    scored=scored, conceded=conceded, n_sims=n_sims, rng=rng,
                    points=points, sampled_minutes=sampled_minutes,
                )

        return PointsSample(
            codes=codes, gw=gw, points=points, minutes=sampled_minutes
        )

    def _simulate_team(
        self, *, squad, index_of, pos_of, rate_frame, mins_by_code,
        scored, conceded, n_sims, rng, points, sampled_minutes,
    ) -> None:
        rows = [index_of[c] for c in squad]
        mins = np.zeros((len(squad), n_sims), dtype=np.int64)

        for j, code in enumerate(squad):
            row = mins_by_code.get(code)
            if row is None:
                probs = (0.5, 0.2, 0.3)
            else:
                probs = (row.p_unavailable, row.p_cameo, row.p_full)
            probs = np.clip(np.asarray(probs, dtype=float), 0, None)
            probs = probs / probs.sum()
            bucket = rng.choice(3, size=n_sims, p=probs)
            # Minutes within a bucket: cameos spread over 1-59, starters 60-90.
            mins[j] = np.where(
                bucket == 0, 0,
                np.where(bucket == 1, rng.integers(1, 60, n_sims),
                         rng.integers(60, 91, n_sims)),
            )

        on_pitch = mins > 0
        share90 = mins / 90.0

        xg = rate_frame["xg90"].to_numpy()[rows][:, None] * share90
        xa = rate_frame["xa90"].to_numpy()[rows][:, None] * share90

        goals = _allocate(scored, xg, rng)
        assisted = rng.binomial(scored, ASSIST_RATE)
        assists = _allocate(assisted, xa, rng)

        clean = ((conceded == 0) & (mins >= 60)).astype(np.int64)
        conceded_by = np.where(on_pitch, conceded[None, :], 0).astype(np.int64)

        save_rate = rate_frame["save90"].to_numpy()[rows][:, None] * share90
        saves = rng.poisson(np.clip(save_rate, 0, None)).astype(np.int64)

        dc_rate = rate_frame["dc_rate"].to_numpy()[rows][:, None] * np.clip(share90, 0, 1)
        dc_hit = (rng.random((len(squad), n_sims)) < dc_rate).astype(np.int64)

        yellow_rate = rate_frame["yellow90"].to_numpy()[rows][:, None] * share90
        yellow = (rng.random((len(squad), n_sims)) < np.clip(yellow_rate, 0, 1)).astype(np.int64)

        zeros = np.zeros_like(goals)
        bps = np.zeros((len(squad), n_sims), dtype=np.int64)
        stat: dict[int, dict[str, np.ndarray]] = {}
        for j, code in enumerate(squad):
            pos = Position(int(pos_of[code]))
            # A goalkeeper's saves are the only per-position quantity here that
            # would otherwise leak across positions.
            sv = saves[j] if pos is Position.GKP else zeros[j]
            cs = clean[j] if pos in (Position.GKP, Position.DEF, Position.MID) else zeros[j]
            fields = dict(
                minutes=mins[j], goals_scored=goals[j], assists=assists[j],
                clean_sheets=cs, goals_conceded=conceded_by[j],
                own_goals=zeros[j], penalties_saved=zeros[j],
                penalties_missed=zeros[j], yellow_cards=yellow[j],
                red_cards=zeros[j], saves=sv,
            )
            stat[j] = fields
            # Map explicitly between the two signatures. Splatting one into the
            # other silently mismatches on goals_scored/goals and
            # clean_sheets/clean_sheet, which would zero out BPS components
            # without raising.
            dc_count = dc_hit[j] * 99  # "at or above threshold" for BPS purposes
            bps[j] = bps_from_events(
                position=pos,
                minutes=mins[j],
                goals=goals[j],
                assists=assists[j],
                clean_sheet=cs,
                saves=sv,
                goals_conceded=conceded_by[j],
                yellow=yellow[j],
                red=zeros[j],
                penalties_saved=zeros[j],
                penalties_missed=zeros[j],
                own_goals=zeros[j],
                cbi=dc_count,
                recoveries=zeros[j],
                tackles=zeros[j],
            )

        bonus = allocate_bonus(bps)

        for j, code in enumerate(squad):
            pos = Position(int(pos_of[code]))
            pts = points_from_events(
                pos, bonus=bonus[j],
                defensive_contribution=dc_hit[j] * (
                    10 if pos is Position.DEF else 12
                ),
                **stat[j],
            )
            points[index_of[code]] = pts
            sampled_minutes[index_of[code]] = mins[j]


def _sample_scoreline(matrix: np.ndarray, n_sims: int, rng) -> tuple[np.ndarray, np.ndarray]:
    """Draw (home goals, away goals) jointly from the score matrix."""
    flat = matrix.ravel()
    total = flat.sum()
    if not np.isfinite(total) or total <= 0:
        raise ValueError("score matrix does not sum to a positive number")
    idx = rng.choice(flat.size, size=n_sims, p=flat / total)
    return np.unravel_index(idx, matrix.shape)


def _allocate(team_total: np.ndarray, weights: np.ndarray, rng) -> np.ndarray:
    """Distribute a team's goals among players in proportion to their weights.

    Uses a per-simulation multinomial so the total always reconciles: the sum
    over players equals the team's sampled goals exactly. Anything looser lets
    the player model quietly disagree with the team model about how many goals
    were scored.
    """
    n_players, n_sims = weights.shape
    out = np.zeros((n_players, n_sims), dtype=np.int64)
    col_sums = weights.sum(axis=0)
    for s in range(n_sims):
        g = int(team_total[s])
        if g <= 0:
            continue
        w = weights[:, s]
        total = col_sums[s]
        if total <= 0:
            # Nobody on the pitch has an attacking rate; spread uniformly over
            # whoever played rather than dropping the goals.
            playing = w >= 0
            p = playing / max(playing.sum(), 1)
        else:
            p = w / total
        out[:, s] = rng.multinomial(g, p)
    return out
