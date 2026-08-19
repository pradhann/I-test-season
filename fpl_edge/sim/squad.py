"""Squads, formations and the autosub engine.

A squad in this module is stored as *indices into a* :class:`PlayerUniverse`,
not as codes. That is deliberate: everything downstream is a gather out of an
``(n_players, n_sims)`` points matrix, and integer indices are the only
representation that makes a 10,000-manager field tractable.

The autosub engine is here rather than in the optimizer because autosubs are a
property of the *outcome*, not of the decision: they depend on realised minutes
and therefore have to be evaluated inside every Monte Carlo draw. Ignoring them
biases every squad's score downwards by roughly a point a gameweek, and biases
bench-heavy squads more than bench-light ones, so it is not a wash.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from fpl_edge.types import Position

#: How many of each position a 15-man squad must contain.
SQUAD_BY_POSITION: dict[int, int] = {1: 2, 2: 5, 3: 5, 4: 3}

#: Minimum number of each position that must be in the starting XI.
MIN_PLAY: dict[int, int] = {1: 1, 2: 3, 3: 2, 4: 1}

#: Maximum number of each position that may be in the starting XI.
MAX_PLAY: dict[int, int] = {1: 1, 2: 5, 3: 5, 4: 3}

SQUAD_SIZE = 15
XI_SIZE = 11


def valid_formations() -> tuple[tuple[int, int, int], ...]:
    """All legal (DEF, MID, FWD) outfield splits for a starting XI."""
    out = []
    for d in range(MIN_PLAY[2], MAX_PLAY[2] + 1):
        for m in range(MIN_PLAY[3], MAX_PLAY[3] + 1):
            f = 10 - d - m
            if MIN_PLAY[4] <= f <= MAX_PLAY[4]:
                out.append((d, m, f))
    return tuple(out)


VALID_FORMATIONS = valid_formations()


@dataclass(frozen=True)
class PlayerUniverse:
    """The immutable player table every simulation indexes into.

    Built once from a :class:`~fpl_edge.store.Snapshot` so that positions,
    prices and team codes cannot drift between the points model, the ownership
    model and the field model.
    """

    codes: np.ndarray        # (P,) int64 stable player codes
    position: np.ndarray     # (P,) int8 in 1..4
    team_code: np.ndarray    # (P,) int64
    price_tenths: np.ndarray  # (P,) int64
    web_name: np.ndarray     # (P,) object

    def __post_init__(self) -> None:
        n = len(self.codes)
        for name in ("position", "team_code", "price_tenths", "web_name"):
            arr = getattr(self, name)
            if len(arr) != n:
                raise ValueError(f"{name} has {len(arr)} entries, codes has {n}")
        if len(set(self.codes.tolist())) != n:
            raise ValueError("player codes must be unique")
        object.__setattr__(self, "_index_of", {int(c): i for i, c in enumerate(self.codes)})

    @property
    def n_players(self) -> int:
        return len(self.codes)

    def index_of(self, code: int) -> int:
        return self._index_of[int(code)]  # type: ignore[attr-defined]

    def indices(self, codes) -> np.ndarray:
        return np.array([self.index_of(int(c)) for c in codes], dtype=np.int64)

    def pos_mask(self, position: int) -> np.ndarray:
        return self.position == position

    @classmethod
    def from_players_frame(cls, df: pd.DataFrame) -> PlayerUniverse:
        """Build from ``Snapshot.players()`` output."""
        df = df.sort_values("code").reset_index(drop=True)
        return cls(
            codes=df["code"].to_numpy(dtype=np.int64),
            position=df["position"].to_numpy(dtype=np.int8),
            team_code=df["team_code"].to_numpy(dtype=np.int64),
            price_tenths=df["price_tenths"].to_numpy(dtype=np.int64),
            web_name=df["web_name"].to_numpy(dtype=object),
        )


@dataclass(frozen=True)
class Squad:
    """A 15-man squad with a chosen XI, bench order and captaincy.

    ``bench`` is in FPL order: the reserve keeper first, then the three outfield
    substitutes in the priority order autosubs will use.
    """

    starters: tuple[int, ...]   # 11 universe indices
    bench: tuple[int, ...]      # 4 universe indices, bench[0] is the GK
    captain: int
    vice: int

    def __post_init__(self) -> None:
        if len(self.starters) != XI_SIZE:
            raise ValueError(f"starting XI must have {XI_SIZE} players, got {len(self.starters)}")
        if len(self.bench) != SQUAD_SIZE - XI_SIZE:
            raise ValueError(f"bench must have {SQUAD_SIZE - XI_SIZE} players")
        if len(set(self.all_indices)) != SQUAD_SIZE:
            raise ValueError("squad contains duplicate players")
        if self.captain not in self.all_indices or self.vice not in self.all_indices:
            raise ValueError("captain and vice must be in the squad")
        if self.captain == self.vice:
            raise ValueError("captain and vice must differ")

    @property
    def all_indices(self) -> tuple[int, ...]:
        return tuple(self.starters) + tuple(self.bench)

    def validate(self, universe: PlayerUniverse) -> None:
        """Check squad composition, formation and the three-per-club limit."""
        idx = np.array(self.all_indices)
        pos = universe.position[idx]
        for p, want in SQUAD_BY_POSITION.items():
            got = int((pos == p).sum())
            if got != want:
                raise ValueError(f"squad has {got} of position {Position(p).name}, needs {want}")
        xi_pos = universe.position[np.array(self.starters)]
        counts = {p: int((xi_pos == p).sum()) for p in (1, 2, 3, 4)}
        if counts[1] != 1:
            raise ValueError("starting XI must contain exactly one goalkeeper")
        if (counts[2], counts[3], counts[4]) not in VALID_FORMATIONS:
            raise ValueError(f"illegal formation DEF/MID/FWD = {counts[2]}/{counts[3]}/{counts[4]}")
        if universe.position[self.bench[0]] != 1:
            raise ValueError("bench[0] must be the reserve goalkeeper")
        teams, n = np.unique(universe.team_code[idx], return_counts=True)
        if (n > 3).any():
            over = teams[n > 3].tolist()
            raise ValueError(f"more than 3 players from team(s) {over}")

    def price(self, universe: PlayerUniverse) -> int:
        return int(universe.price_tenths[np.array(self.all_indices)].sum())

    def replace(self, out_index: int, in_index: int, universe: PlayerUniverse) -> Squad:
        """Return a new squad with one player swapped, re-picking nothing else."""
        if out_index not in self.all_indices:
            raise ValueError("out_index is not in the squad")
        if in_index in self.all_indices:
            raise ValueError("in_index is already in the squad")
        if universe.position[out_index] != universe.position[in_index]:
            raise ValueError("a straight swap must be within a position")
        sw = lambda t: tuple(in_index if i == out_index else i for i in t)
        cap = in_index if self.captain == out_index else self.captain
        vc = in_index if self.vice == out_index else self.vice
        return Squad(starters=sw(self.starters), bench=sw(self.bench), captain=cap, vice=vc)

    def with_captain(self, captain: int, vice: int | None = None) -> Squad:
        vc = self.vice if vice is None else vice
        if vc == captain:
            vc = next(i for i in self.starters if i != captain)
        return Squad(starters=self.starters, bench=self.bench, captain=captain, vice=vc)


def pick_best_xi(squad_indices, expected_points: np.ndarray, universe: PlayerUniverse) -> Squad:
    """Choose the XI, bench order and captain that maximise expected points.

    This is what the *field* is assumed to do: a manager picks their eleven by
    expectation, not with hindsight. Using realised points here would give every
    rival manager perfect foresight and make the field impossible to beat.
    """
    idx = np.asarray(list(squad_indices), dtype=np.int64)
    pos = universe.position[idx]
    xp = expected_points[idx]
    by_pos = {p: idx[pos == p][np.argsort(-xp[pos == p], kind="stable")] for p in (1, 2, 3, 4)}

    best: tuple[float, tuple[int, int, int]] | None = None
    for d, m, f in VALID_FORMATIONS:
        total = (
            expected_points[by_pos[1][:1]].sum()
            + expected_points[by_pos[2][:d]].sum()
            + expected_points[by_pos[3][:m]].sum()
            + expected_points[by_pos[4][:f]].sum()
        )
        if best is None or total > best[0]:
            best = (float(total), (d, m, f))
    assert best is not None
    d, m, f = best[1]

    starters = [int(by_pos[1][0])]
    starters += [int(i) for i in by_pos[2][:d]]
    starters += [int(i) for i in by_pos[3][:m]]
    starters += [int(i) for i in by_pos[4][:f]]

    bench_out = [int(i) for i in np.concatenate([by_pos[2][d:], by_pos[3][m:], by_pos[4][f:]])]
    bench_out.sort(key=lambda i: -expected_points[i])
    bench = [int(by_pos[1][1])] + bench_out

    order = sorted(starters, key=lambda i: -expected_points[i])
    return Squad(starters=tuple(starters), bench=tuple(bench),
                 captain=order[0], vice=order[1])


# ---------------------------------------------------------------------------
# Autosubs
# ---------------------------------------------------------------------------

def apply_autosubs(
    points: np.ndarray,
    minutes: np.ndarray | None,
    positions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Run the autosub rules over a batch of squads and simulations.

    Parameters
    ----------
    points, minutes
        ``(n_squads, 15, n_sims)``. Slot order is the 11 starters in the caller's
        order, then bench GK, then the three outfield substitutes in priority
        order. ``minutes`` of ``None`` disables autosubs entirely.
    positions
        ``(n_squads, 15)`` FPL element types.

    Returns
    -------
    in_xi
        ``(n_squads, 15, n_sims)`` boolean: who ended up in the scoring XI.
    xi_points
        ``(n_squads, n_sims)`` total XI points, before captaincy.

    The rules implemented are the verified ones in ``docs/rules.md``: the
    goalkeeper is replaceable only by the bench goalkeeper; outfield subs are
    taken in bench priority order and only if the resulting XI still satisfies
    the formation minima. A substitution that would break formation is skipped
    and the next bench player is tried, which is why this cannot be reduced to
    "add the bench players who played".
    """
    points = np.asarray(points, dtype=np.float32)
    r, n, s = points.shape
    if n != SQUAD_SIZE:
        raise ValueError(f"expected {SQUAD_SIZE} slots, got {n}")
    in_xi = np.zeros((r, n, s), dtype=bool)
    in_xi[:, :XI_SIZE, :] = True
    if minutes is None:
        return in_xi, points[:, :XI_SIZE, :].sum(axis=1)

    played = np.asarray(minutes) > 0
    pos = np.asarray(positions)

    # --- goalkeeper: only the bench keeper may come on -----------------------
    gk_slot = np.argmax(pos[:, :XI_SIZE] == 1, axis=1)          # (r,)
    rows = np.arange(r)
    gk_played = played[rows, gk_slot, :]                         # (r, s)
    bench_gk_played = played[:, XI_SIZE, :]
    swap_gk = (~gk_played) & bench_gk_played
    in_xi[rows, gk_slot, :] &= ~swap_gk
    in_xi[:, XI_SIZE, :] |= swap_gk

    # --- outfield: bench priority order, formation-checked -------------------
    # counts[p] is the number of position-p players currently in the XI.
    counts = np.zeros((r, 5, s), dtype=np.int16)
    for p in (1, 2, 3, 4):
        counts[:, p, :] = (in_xi & (pos == p)[:, :, None]).sum(axis=1)

    # A starter is a candidate to be replaced if they are still in the XI and
    # did not play. available[r, p, s] counts such starters by position.
    for b in range(XI_SIZE + 1, SQUAD_SIZE):
        bench_pos = pos[:, b]                                    # (r,)
        bench_ok = played[:, b, :] & ~in_xi[:, b, :]             # (r, s)
        vacancy = (~played[:, :XI_SIZE, :]) & in_xi[:, :XI_SIZE, :]

        # Prefer swapping out a player of the same position (formation
        # unchanged); otherwise take any position whose minimum still holds.
        done = np.zeros((r, s), dtype=bool)
        for out_p in [None, 2, 3, 4]:
            if out_p is None:
                cand_p = bench_pos                               # same-position swap
                match = (pos[:, :XI_SIZE] == cand_p[:, None])[:, :, None] & vacancy
                has = match.any(axis=1)
                legal = np.ones((r, s), dtype=bool)
            else:
                match = (pos[:, :XI_SIZE] == out_p)[:, :, None] & vacancy
                has = match.any(axis=1)
                new_out = counts[:, out_p, :] - 1
                new_in = counts[np.arange(r), bench_pos, :] + 1
                same = bench_pos[:, None] == out_p
                legal_out = np.where(same, counts[:, out_p, :], new_out) >= np.array(
                    [MIN_PLAY[out_p]], dtype=np.int16
                )
                legal_in = np.where(same, counts[np.arange(r), bench_pos, :], new_in) <= np.array(
                    [MAX_PLAY[int(x)] for x in bench_pos], dtype=np.int16
                )[:, None]
                legal = legal_out & legal_in
            take = bench_ok & has & legal & ~done
            if not take.any():
                continue
            # Retire the first matching vacancy per (squad, sim).
            first = np.argmax(match, axis=1)                     # (r, s)
            sel = take
            slot_mask = np.zeros((r, XI_SIZE, s), dtype=bool)
            np.put_along_axis(slot_mask, first[:, None, :], sel[:, None, :], axis=1)
            in_xi[:, :XI_SIZE, :] &= ~slot_mask
            in_xi[:, b, :] |= sel
            out_positions = np.take_along_axis(
                np.broadcast_to(pos[:, :XI_SIZE, None], (r, XI_SIZE, s)), first[:, None, :], axis=1
            )[:, 0, :]
            for p in (2, 3, 4):
                counts[:, p, :] -= (sel & (out_positions == p)).astype(np.int16)
            for p in (2, 3, 4):
                counts[:, p, :] += (sel & (bench_pos[:, None] == p)).astype(np.int16)
            done |= sel

    xi_points = (points * in_xi).sum(axis=1)
    return in_xi, xi_points


def score_squads(
    points: np.ndarray,
    minutes: np.ndarray | None,
    positions: np.ndarray,
    captain_slot: np.ndarray,
    vice_slot: np.ndarray,
    *,
    captain_multiplier: float = 2.0,
) -> np.ndarray:
    """Total gameweek points for a batch of squads, autosubs and captaincy included.

    ``captain_slot`` / ``vice_slot`` are ``(n_squads,)`` slot positions in the
    same 15-slot layout as ``points``.
    """
    r, _, s = points.shape
    rows = np.arange(r)
    in_xi, xi_points = apply_autosubs(points, minutes, positions)
    cap_in = in_xi[rows, captain_slot, :]
    vice_in = in_xi[rows, vice_slot, :]
    if minutes is None:
        cap_played = np.ones((r, s), dtype=bool)
        vice_played = np.ones((r, s), dtype=bool)
    else:
        cap_played = np.asarray(minutes)[rows, captain_slot, :] > 0
        vice_played = np.asarray(minutes)[rows, vice_slot, :] > 0
    use_cap = cap_in & cap_played
    use_vice = (~use_cap) & vice_in & vice_played
    extra = (captain_multiplier - 1.0) * (
        np.where(use_cap, points[rows, captain_slot, :], 0.0)
        + np.where(use_vice, points[rows, vice_slot, :], 0.0)
    )
    return xi_points + extra
