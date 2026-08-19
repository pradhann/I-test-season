"""Shared builders for the optimiser tests. Contains no tests itself.

Two problem sources:

* :func:`synthetic_problem` -- a small, fully-controlled universe. Every
  constraint test builds one of these with exactly the pathology it wants, so
  a failure names one rule rather than "the big model went wrong".
* :func:`fixture_problem` -- the committed 592-player 2026/27 universe under
  ``tests/fixtures/opt``. Used for the realistic timing and end-to-end plan
  tests, and independent of every other team's model.
"""

from __future__ import annotations

import functools
from pathlib import Path

import numpy as np
import pandas as pd

from fpl_edge.opt import HorizonProblem, PlayerRow, Ruleset, SquadState
from fpl_edge.types import GwId, Money, PlayerCode, Position, Season, TeamCode

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "opt"
SEASON = Season("2026-27")


def synthetic_problem(
    *,
    per_position: dict[Position, int] | None = None,
    n_clubs: int = 8,
    n_gws: int = 3,
    first_gw: int = 1,
    price_of=None,
    xp_of=None,
    p_play: float = 0.9,
    state: SquadState | None = None,
    ownable: np.ndarray | None = None,
    ruleset: Ruleset | None = None,
) -> HorizonProblem:
    """A small deterministic universe with tunable prices, points and clubs.

    ``price_of(i, position, gw_index)`` and ``xp_of(i, position, gw_index)``
    override the defaults, which are a cheap monotone function of the index so
    that the optimal squad is predictable enough to reason about.
    """
    counts = per_position or {Position.GKP: 6, Position.DEF: 20, Position.MID: 20, Position.FWD: 12}
    rs = ruleset or Ruleset.from_registry()

    def default_price(i: int, pos: Position, j: int) -> int:
        return 40 + (i % 9) * 5

    def default_xp(i: int, pos: Position, j: int) -> float:
        return round(1.0 + 0.08 * (i % 9) * 5 + 0.1 * ((i * 7 + j * 3) % 5), 3)

    price_of = price_of or default_price
    xp_of = xp_of or default_xp

    players: list[PlayerRow] = []
    code = 1000
    for pos in (Position.GKP, Position.DEF, Position.MID, Position.FWD):
        for k in range(counts[pos]):
            players.append(
                PlayerRow(
                    code=PlayerCode(code),
                    name=f"{pos.name}{k}",
                    position=pos,
                    team_code=TeamCode(1 + (len(players) % n_clubs)),
                )
            )
            code += 1

    n, t = len(players), n_gws
    price = np.zeros((n, t), dtype=np.int64)
    xp = np.zeros((n, t), dtype=float)
    for i, row in enumerate(players):
        for j in range(t):
            price[i, j] = int(price_of(i, row.position, j))
            xp[i, j] = float(xp_of(i, row.position, j))
    return HorizonProblem(
        season=SEASON,
        gws=tuple(GwId(first_gw + j) for j in range(t)),
        players=tuple(players),
        price_tenths=price,
        xpts=xp,
        p_play=np.full((n, t), p_play),
        ownable=ownable if ownable is not None else np.ones((n, t), dtype=bool),
        state=state or SquadState(),
        ruleset=rs,
    )


@functools.lru_cache(maxsize=1)
def _fixture_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return (
        pd.read_csv(FIXTURES / "universe_2026_27.csv"),
        pd.read_csv(FIXTURES / "xpts_2026_27.csv"),
        pd.read_csv(FIXTURES / "prices_2026_27.csv"),
    )


def fixture_problem(
    gws: tuple[int, ...] = (1, 2, 3, 4, 5),
    *,
    state: SquadState | None = None,
    flat_prices: bool = False,
) -> HorizonProblem:
    """The committed 592-player universe over ``gws``."""
    universe, xpts, prices = _fixture_frames()
    universe = universe.sort_values("code").reset_index(drop=True)
    players = tuple(
        PlayerRow(
            code=PlayerCode(int(r.code)),
            name=str(r.web_name),
            position=Position(int(r.position)),
            team_code=TeamCode(int(r.team_code)),
        )
        for r in universe.itertuples()
    )
    order = {int(p.code): i for i, p in enumerate(players)}
    n, t = len(players), len(gws)

    price = np.zeros((n, t), dtype=np.int64)
    xp = np.zeros((n, t), dtype=float)
    pp = np.zeros((n, t), dtype=float)
    base_price = dict(zip(universe["code"].astype(int), universe["price_tenths"].astype(int)))
    price_map = {
        (int(r.code), int(r.gw)): int(r.price_tenths) for r in prices.itertuples()
    }
    xp_map = {(int(r.code), int(r.gw)): (float(r.xpts), float(r.p_play)) for r in xpts.itertuples()}
    for c, i in order.items():
        for j, gw in enumerate(gws):
            price[i, j] = base_price[c] if flat_prices else price_map[(c, gw)]
            xp[i, j], pp[i, j] = xp_map[(c, gw)]

    selectable = universe["status"].isin(["a", "d"]).to_numpy()
    held = set(state.holdings) if state else set()
    ownable = np.repeat(selectable[:, None], t, axis=1)
    for c in held:
        ownable[order[int(c)], :] = True

    return HorizonProblem(
        season=SEASON,
        gws=tuple(GwId(g) for g in gws),
        players=players,
        price_tenths=price,
        xpts=xp,
        p_play=pp,
        ownable=ownable,
        state=state or SquadState(),
        ruleset=Ruleset.from_registry(),
    )


def holdings_from(problem: HorizonProblem, codes, gw_index: int = 0) -> dict[PlayerCode, Money]:
    """Purchase prices for ``codes`` taken at ``gw_index`` prices."""
    idx = problem.index_of
    return {c: Money(int(problem.price_tenths[idx[c], gw_index])) for c in codes}
