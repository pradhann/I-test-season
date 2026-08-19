"""The transfer recommendation, and the two things it refuses to do.

The refusals are the point of the file. This engine exists because maximising
expected points is the wrong objective for a rank tournament, so:

* asking for ``RANK_UTILITY`` without a provider must raise, not quietly return
  means wearing a rank label; and
* asking for anything at all without a points forecast must raise, not
  substitute a projection nobody chose.

Everything else here checks that the ranking is honest: that the alternatives
are solved by the same optimiser and objective as the winner, that rolling is
always on the list so a hit has something to be judged against, and that the
hit verdict says no when the margin is inside the forecast's own error.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from fpl_edge.eval.scoring import Pick
from fpl_edge.myteam.forecast import (
    PointsForecastUnavailableError,
    TablePointsForecast,
    complete_universe,
)
from fpl_edge.myteam.recommend import (
    NoSquadError,
    TransferRecommendation,
    build_state,
    is_before_first_deadline,
    prune_keeping,
    recommend,
    screen_moves,
)
from fpl_edge.myteam.sources import EntryHistory, EntrySummary, GwPicks, PublicPick
from fpl_edge.myteam.state import PlayerIndex, reconstruct
from fpl_edge.opt import ObjectiveMode, OptimizerConfig
from fpl_edge.opt.interfaces import RankUtilityUnavailableError
from fpl_edge.store import Warehouse
from fpl_edge.types import GwId, Money, Position

UTC = dt.timezone.utc
SEASON = "2026-27"
T0 = dt.datetime(2026, 8, 1, 12, tzinfo=UTC)
NOW = dt.datetime(2026, 8, 18, 12, tzinfo=UTC)

_LAYOUT = [(Position.GKP, 6), (Position.DEF, 14), (Position.MID, 14), (Position.FWD, 10)]


@pytest.fixture()
def warehouse(tmp_path) -> Warehouse:
    wh = Warehouse(tmp_path / "t.duckdb")
    players, states = [], []
    code = 3000
    for pos, n in _LAYOUT:
        for i in range(n):
            code += 1
            players.append({
                "season": SEASON, "code": code, "element_id": code - 3000,
                "web_name": f"{pos.name}{i}", "first_name": "F",
                "second_name": f"{pos.name}{i}", "position": int(pos),
                "team_code": 1 + (i % 12), "as_of": T0,
            })
            states.append({
                "season": SEASON, "code": code, "element_id": code - 3000,
                "price_tenths": 40 + (i % 5) * 5, "selected_by_pct": 5.0, "status": "a",
                "chance_of_playing_next_round": None, "news": "", "news_added": None,
                "transfers_in_event": 0, "transfers_out_event": 0,
                "cost_change_start": 0, "as_of": T0,
            })
    wh.append("dim_player", pd.DataFrame(players))
    wh.append("fact_player_state", pd.DataFrame(states))
    wh.append("dim_event", pd.DataFrame([
        {"season": SEASON, "gw": gw,
         "deadline_utc": dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)
                         + dt.timedelta(days=7 * (gw - 1)),
         "is_finished": False, "as_of": T0}
        for gw in range(1, 11)
    ]))
    return wh


@pytest.fixture()
def index(warehouse) -> PlayerIndex:
    return PlayerIndex.from_snapshot(warehouse.snapshot_at(NOW), SEASON)


def _legal(index: PlayerIndex) -> list[int]:
    chosen: list[int] = []
    per_club: dict[int, int] = {}
    for pos, want in ((Position.GKP, 2), (Position.DEF, 5), (Position.MID, 5), (Position.FWD, 3)):
        taken = 0
        for code in sorted(
            (c for c, p in index.position.items() if p is pos),
            key=lambda c: (index.price_now[c], c),
        ):
            if per_club.get(index.team_code[code], 0) >= 3:
                continue
            chosen.append(code)
            per_club[index.team_code[code]] = per_club.get(index.team_code[code], 0) + 1
            taken += 1
            if taken == want:
                break
        assert taken == want
    return chosen


def _public(codes: list[int], index: PlayerIndex, gw: int = 1) -> GwPicks:
    by_pos = {p: [c for c in codes if index.position[c] is p] for p in Position}
    xi = by_pos[Position.GKP][:1] + by_pos[Position.DEF][:3] \
        + by_pos[Position.MID][:5] + by_pos[Position.FWD][:2]
    bench = [c for c in codes if c not in xi]
    bench.sort(key=lambda c: index.position[c] is not Position.GKP)
    ordered = xi + bench
    return GwPicks(
        gw=GwId(gw), active_chip=None,
        picks=tuple(
            PublicPick(element=index.element_by_code[c], position=i + 1,
                       multiplier=2 if i == 1 else (1 if i < 11 else 0),
                       is_captain=(i == 1), is_vice_captain=(i == 2))
            for i, c in enumerate(ordered)
        ),
        bank=None, value=None, event_transfers=0, event_transfers_cost=0,
    )


def _entry(transfers: int = 0) -> EntrySummary:
    return EntrySummary(
        entry_id=4490171, name="i-test", player_name="N P", started_event=1,
        current_event=None, entered_events=(), last_deadline_bank=None,
        last_deadline_value=None, last_deadline_total_transfers=transfers,
        years_active=10, favourite_team=16, summary_overall_points=None,
        summary_overall_rank=None,
    )


@pytest.fixture()
def state(warehouse, index):
    codes = _legal(index)
    return reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((), (), ()), transfers=(), picks=_public(codes, index),
    )


def _forecast(index: PlayerIndex, gws=(1,), *, best: int | None = None):
    """Flat xPts, with one player made clearly the best buy if asked."""
    rows = []
    for gw in gws:
        for i, code in enumerate(sorted(index.price_now)):
            xp = 1.0 + (i % 7) * 0.1
            if best is not None and code == best:
                xp = 20.0
            rows.append({"code": code, "gw": gw, "xpts": xp, "p_play": 0.9})
    return TablePointsForecast(frame=pd.DataFrame(rows), name="test-table")


# -- the two refusals ---------------------------------------------------------


def test_rank_utility_without_a_provider_refuses(warehouse, index, state) -> None:
    """The default objective is the real one, and it will not be faked."""
    with pytest.raises(RankUtilityUnavailableError):
        recommend(
            warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
            points_forecast=_forecast(index),
        )


def test_the_refusal_names_the_surrogate_rather_than_using_it(warehouse, index, state) -> None:
    with pytest.raises(RankUtilityUnavailableError) as exc:
        recommend(
            warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
            points_forecast=_forecast(index),
        )
    assert "EXPECTED_POINTS" in str(exc.value)


def test_no_points_forecast_refuses_rather_than_inventing_one(warehouse, state) -> None:
    with pytest.raises(PointsForecastUnavailableError, match="Refusing to substitute"):
        recommend(
            warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
            points_forecast=None, mode=ObjectiveMode.EXPECTED_POINTS,
        )


def test_no_squad_refuses_and_says_how_to_fix_it(warehouse, index) -> None:
    unknown = reconstruct(
        warehouse.snapshot_at(NOW), entry_id=4490171, season=SEASON, entry=_entry(),
        history=EntryHistory((), (), ()), transfers=(),
    )
    with pytest.raises(NoSquadError, match="fpl myteam set"):
        recommend(
            warehouse.snapshot_at(NOW), unknown, season=SEASON, gws=[1],
            points_forecast=_forecast(index), mode=ObjectiveMode.EXPECTED_POINTS,
        )


def test_a_forecast_missing_a_selectable_player_is_a_hole_not_a_zero(warehouse, index) -> None:
    snapshot = warehouse.snapshot_at(NOW)
    codes = sorted(index.price_now)[:-1]
    partial = pd.DataFrame(
        [{"code": c, "gw": 1, "xpts": 1.0, "p_play": 0.9} for c in codes]
    )
    with pytest.raises(PointsForecastUnavailableError, match="selectable player"):
        complete_universe(partial, snapshot, SEASON, [GwId(1)])


# -- the mechanics ------------------------------------------------------------


def test_before_the_first_deadline_transfers_are_free(state) -> None:
    assert is_before_first_deadline(state)
    opt_state = build_state(state)
    assert opt_state.is_preseason, "GW1 changes cost nothing, so holdings do not bind"


def test_pruning_never_drops_the_squad_you_own(warehouse, index, state) -> None:
    """The pre-season state is legitimately empty, so the squad needs protecting."""
    from fpl_edge.opt import StaticPriceForecast, build_problem

    problem = build_problem(
        warehouse.snapshot_at(NOW), SEASON, [GwId(1)],
        price_forecast=StaticPriceForecast(), points_forecast=_forecast(index),
        state=build_state(state),
    )
    held = [int(p.code) for p in state.picks]
    pruned = prune_keeping(problem, 3, held)
    assert set(held) <= {int(p.code) for p in pruned.players}
    # The optimiser's own prune would lose them, which is the bug being guarded.
    assert not set(held) <= {int(p.code) for p in problem.prune(3).players}


def test_the_screen_only_proposes_affordable_same_position_moves(warehouse, index, state) -> None:
    from fpl_edge.opt import StaticPriceForecast, build_problem

    problem = build_problem(
        warehouse.snapshot_at(NOW), SEASON, [GwId(1)],
        price_forecast=StaticPriceForecast(), points_forecast=_forecast(index),
        state=build_state(state),
    )
    held = [int(p.code) for p in state.picks]
    problem = prune_keeping(problem, None, held)
    moves = screen_moves(problem, state, limit=20)
    assert moves
    for out, into in moves:
        for o, i in zip(out, into):
            assert index.position[o] is index.position[i], "positions must match"
            assert i not in held, "cannot buy someone you own"


def test_rolling_is_always_an_option_on_the_table(warehouse, index, state) -> None:
    rec = recommend(
        warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
        points_forecast=_forecast(index), mode=ObjectiveMode.EXPECTED_POINTS,
        candidates=4,
    )
    assert rec.roll is not None
    assert rec.roll.n_transfers == 0
    everything = [rec.chosen, *rec.alternatives]
    assert any(m.is_roll for m in everything), "doing nothing must be comparable"


def test_the_winner_beats_every_alternative_on_the_same_objective(
    warehouse, index, state
) -> None:
    rec = recommend(
        warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
        points_forecast=_forecast(index), mode=ObjectiveMode.EXPECTED_POINTS,
        candidates=6,
    )
    assert all(rec.chosen.objective >= alt.objective for alt in rec.alternatives)


def test_a_clearly_better_player_is_bought(warehouse, index, state) -> None:
    """A 20-point midfielder nobody owns should end up in the squad."""
    held = {int(p.code) for p in state.picks}
    star = next(
        c for c, pos in index.position.items()
        if pos is Position.MID and c not in held and index.price_now[c] <= 45
    )
    rec = recommend(
        warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
        points_forecast=_forecast(index, best=star), mode=ObjectiveMode.EXPECTED_POINTS,
        candidates=6,
    )
    assert star in rec.chosen.into


def test_the_recommendation_reports_the_objective_it_used(warehouse, index, state) -> None:
    rec = recommend(
        warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
        points_forecast=_forecast(index), mode=ObjectiveMode.EXPECTED_POINTS,
        candidates=3,
    )
    rendered = rec.render(index)
    assert "expected_points" in rendered
    assert any("surrogate" in n for n in rec.notes), "the caveat must always travel"
    assert "test-table" in rendered


def test_before_the_first_deadline_the_hit_verdict_says_free(warehouse, index, state) -> None:
    rec = recommend(
        warehouse.snapshot_at(NOW), state, season=SEASON, gws=[1],
        points_forecast=_forecast(index), mode=ObjectiveMode.EXPECTED_POINTS,
        candidates=3,
    )
    assert rec.unlimited_transfers
    assert "at no cost" in rec.hit_verdict()


# -- the hit verdict ----------------------------------------------------------


def _rec(hits: int, chosen_obj: float, roll_obj: float, **over) -> TransferRecommendation:
    from fpl_edge.myteam.recommend import Move

    def move(obj: float, n: int, h: int):
        return Move(out=tuple(range(n)), into=tuple(range(100, 100 + n)),
                    objective=obj, hits=h, bank_after=Money(0), plan=None)

    return TransferRecommendation(
        season=SEASON, gw=GwId(5), mode=ObjectiveMode.EXPECTED_POINTS,
        horizon=(GwId(5),), chosen=move(chosen_obj, 2, hits),
        roll=move(roll_obj, 0, 0), alternatives=(), free_transfers=1, **over,
    )


def test_a_hit_that_loses_to_rolling_is_called_out() -> None:
    verdict = _rec(hits=1, chosen_obj=50.0, roll_obj=51.0).hit_verdict()
    assert "does NOT beat rolling" in verdict and "Do not take this hit" in verdict


def test_a_hit_winning_by_noise_is_not_endorsed() -> None:
    """0.4 points over a horizon is inside the forecast's own error."""
    verdict = _rec(hits=1, chosen_obj=50.4, roll_obj=50.0).hit_verdict()
    assert "inside the forecast's own error" in verdict and "Roll." in verdict


def test_a_hit_that_clearly_pays_for_itself_is_endorsed() -> None:
    verdict = _rec(hits=1, chosen_obj=56.0, roll_obj=50.0).hit_verdict()
    assert "already paid for" in verdict


def test_no_hit_needs_no_justification() -> None:
    assert "No hit" in _rec(hits=0, chosen_obj=51.0, roll_obj=50.0).hit_verdict()
