"""Squad legality, formation choice and the autosub engine.

Autosubs get the most attention here because they are the one part of the
simulator where the FPL rules are fiddly and a plausible-looking implementation
can be wrong in a way that only shows up as a systematic bias in every rank
estimate.
"""

from __future__ import annotations

import numpy as np
import pytest

from fpl_edge.sim.squad import (
    MIN_PLAY,
    VALID_FORMATIONS,
    Squad,
    apply_autosubs,
    pick_best_xi,
    score_squads,
)
from fpl_edge.sim.synthetic import toy_world

# 4-4-2 laid out as: GK, 4 DEF, 4 MID, 2 FWD, then bench GK, DEF, MID, FWD.
POS_442 = np.array([[1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 1, 2, 3, 4]])


def _pts(n_sims: int) -> np.ndarray:
    return np.tile(np.arange(15, dtype=np.float32)[None, :, None], (1, 1, n_sims))


def test_valid_formations_match_the_rules():
    assert len(VALID_FORMATIONS) == 8
    for d, m, f in VALID_FORMATIONS:
        assert d + m + f == 10
        assert d >= MIN_PLAY[2] and m >= MIN_PLAY[3] and f >= MIN_PLAY[4]
    assert (3, 4, 3) in VALID_FORMATIONS
    assert (5, 4, 1) in VALID_FORMATIONS
    assert (2, 5, 3) not in VALID_FORMATIONS   # only two defenders is illegal


def test_pick_best_xi_is_legal_and_maximises_expected_points():
    u, _, _, _, xp = toy_world(seed=1)
    chosen, club = [], {}
    for p, n in ((1, 2), (2, 5), (3, 5), (4, 3)):
        for c in np.flatnonzero(u.position == p):
            t = int(u.team_code[c])
            if len(chosen) - sum(1 for _ in ()) >= 0 and club.get(t, 0) < 3:
                chosen.append(int(c))
                club[t] = club.get(t, 0) + 1
            if sum(1 for x in chosen if u.position[x] == p) == n:
                break
    squad = pick_best_xi(chosen, xp, u)
    squad.validate(u)
    xi = xp[np.array(squad.starters)].sum()
    for other in VALID_FORMATIONS:
        by_pos = {p: sorted((c for c in chosen if u.position[c] == p),
                            key=lambda c: -xp[c]) for p in (1, 2, 3, 4)}
        alt = (xp[by_pos[1][0]] + sum(xp[c] for c in by_pos[2][:other[0]])
               + sum(xp[c] for c in by_pos[3][:other[1]])
               + sum(xp[c] for c in by_pos[4][:other[2]]))
        assert xi >= alt - 1e-9
    assert squad.captain == max(squad.starters, key=lambda i: xp[i])


def test_squad_rejects_illegal_composition():
    with pytest.raises(ValueError, match="11 players"):
        Squad(starters=tuple(range(10)), bench=(10, 11, 12, 13), captain=0, vice=1)
    with pytest.raises(ValueError, match="duplicate"):
        Squad(starters=tuple(range(11)), bench=(0, 12, 13, 14), captain=0, vice=1)
    with pytest.raises(ValueError, match="must differ"):
        Squad(starters=tuple(range(11)), bench=(11, 12, 13, 14), captain=0, vice=0)


def test_autosub_replaces_a_blanking_starter():
    pts, mins = _pts(1), np.full((1, 15, 1), 90)
    mins[0, 1, 0] = 0                       # a defender does not play
    in_xi, total = apply_autosubs(pts, mins, POS_442)
    assert not in_xi[0, 1, 0] and in_xi[0, 12, 0]
    assert total[0, 0] == pytest.approx(sum(range(11)) - 1 + 12)


def test_autosub_skips_a_bench_player_who_would_break_the_formation():
    # Three defenders start; one blanks. Bringing on the bench MID would leave
    # two defenders, so the bench DEF must be used even though it is lower
    # priority in the alternative ordering.
    pos = np.array([[1, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 1, 3, 2, 4]])
    pts, mins = _pts(1), np.full((1, 15, 1), 90)
    mins[0, 1, 0] = 0
    in_xi, _ = apply_autosubs(pts, mins, pos)
    assert in_xi[0, 13, 0], "bench DEF should come on"
    assert not in_xi[0, 12, 0], "bench MID would leave only 2 defenders"


def test_no_sub_happens_when_every_bench_option_breaks_the_formation():
    # Three defenders start and one blanks; the whole outfield bench is
    # midfielders, so bringing any of them on would leave two defenders. FPL
    # makes no substitution at all and the blanking defender scores nothing.
    pos = np.array([[1, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 1, 3, 3, 3]])
    pts, mins = _pts(1), np.full((1, 15, 1), 90)
    mins[0, 1, 0] = 0
    in_xi, _ = apply_autosubs(pts, mins, pos)
    assert not in_xi[0, 12:, 0].any(), "no legal outfield substitution exists"
    assert in_xi[0, 1, 0], "the blanking defender stays in the XI"


def test_goalkeeper_is_only_replaced_by_the_bench_goalkeeper():
    pts, mins = _pts(1), np.full((1, 15, 1), 90)
    mins[0, 0, 0] = 0
    mins[0, 11, 0] = 0                       # bench keeper also blanks
    in_xi, _ = apply_autosubs(pts, mins, POS_442)
    assert in_xi[0, 0, 0], "no outfielder may replace the keeper"
    assert not in_xi[0, 12, 0]


def test_captaincy_falls_through_to_the_vice_when_the_captain_blanks():
    # Isolate the captaincy rule from autosubs by measuring only the extra
    # points on top of whatever XI the autosub engine settles on.
    pts, mins = _pts(3), np.full((1, 15, 3), 90)
    mins[0, 9, 1] = 0                        # captain plays no minutes
    mins[0, 9, 2] = 0
    mins[0, 10, 2] = 0                       # and so does the vice
    _, xi = apply_autosubs(pts, mins, POS_442)
    total = score_squads(pts, mins, POS_442, np.array([9]), np.array([10]))
    extra = total - xi
    assert extra[0, 0] == pytest.approx(9)    # captain played: captain doubled
    assert extra[0, 1] == pytest.approx(10)   # captain blanked: vice doubled
    assert extra[0, 2] == pytest.approx(0)    # both blanked: nobody doubled


def test_disabling_minutes_disables_autosubs():
    pts = _pts(2)
    in_xi, total = apply_autosubs(pts, None, POS_442)
    assert in_xi[0, :11, :].all() and not in_xi[0, 11:, :].any()
    assert (total == sum(range(11))).all()


def test_autosubs_are_batched_consistently_across_squads():
    rng = np.random.default_rng(0)
    r = 5
    pos = np.tile(POS_442, (r, 1))
    pts = rng.integers(0, 12, size=(r, 15, 40)).astype(np.float32)
    mins = np.where(rng.random((r, 15, 40)) < 0.25, 0, 90)
    _, batched = apply_autosubs(pts, mins, pos)
    for i in range(r):
        _, single = apply_autosubs(pts[i: i + 1], mins[i: i + 1], pos[i: i + 1])
        assert np.array_equal(batched[i], single[0])
