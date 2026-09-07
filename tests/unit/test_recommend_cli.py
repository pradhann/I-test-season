"""`fpl recommend`'s artefact serializer, tested without a MILP.

serialize_recommendation is the pure seam between the optimiser's
TransferRecommendation and data/warehouse/transfer_plan.json — the artefact
the dashboard's solver card renders. These tests build a real
TransferRecommendation (real Move dataclasses, real Money, a stub plan for
the one decision the serializer reads) and pin the artefact's contract:
Money as tenths, the objective in its own named currency, gain_over_roll as
the recommendation's own property, alternatives capped, and JSON-clean output.
"""

from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from fpl_edge.cli.recommend import serialize_recommendation
from fpl_edge.myteam.recommend import HitVerdict, Move, TransferRecommendation
from fpl_edge.opt import ObjectiveMode
from fpl_edge.types import GwId, Money

UTC = dt.timezone.utc


def _plan(captain=120, vice=121, xi=None):
    return SimpleNamespace(decisions=[SimpleNamespace(
        captain=captain, vice_captain=vice,
        starting_xi=tuple(xi or range(100, 111)),
    )])


def _move(out=(131,), into=(202,), objective=123.4, hits=0, chip="",
          label="", bank=5):
    return Move(
        out=tuple(out), into=tuple(into), objective=objective, hits=hits,
        bank_after=Money(bank), plan=_plan(), chip=chip, label=label,
    )


def _rec(*, roll_objective=120.1, n_alternatives=2, hit_verdicts=()):
    chosen = _move()
    roll = (_move(out=(), into=(), objective=roll_objective, label="roll")
            if roll_objective is not None else None)
    alts = tuple(
        _move(out=(121,), into=(200 + k,), objective=122.0 - k)
        for k in range(n_alternatives)
    )
    return TransferRecommendation(
        season="2026-27", gw=GwId(3), mode=ObjectiveMode.EXPECTED_POINTS,
        horizon=(GwId(3), GwId(4), GwId(5), GwId(6), GwId(7)),
        chosen=chosen, roll=roll, alternatives=alts,
        free_transfers=2, unlimited_transfers=False,
        notes=("EXPECTED_POINTS is a surrogate.",),
        n_candidates_screened=8, n_candidates_solved=7, solve_seconds=42.5,
        hit_verdicts=tuple(hit_verdicts),
    )


def _serialize(rec):
    return serialize_recommendation(
        rec, generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        max_candidates=25, seconds=60.0,
    )


def test_the_artefact_carries_the_full_contract_and_is_json_clean():
    art = _serialize(_rec())
    # every key the brief's solve block reads must exist
    for key in ("generated_at", "season", "gw", "horizon_gws",
                "objective_mode", "free_transfers", "unlimited_transfers",
                "chosen", "roll", "gain_over_roll", "alternatives",
                "hit_verdicts", "notes", "n_candidates_screened",
                "n_candidates_solved", "solve_seconds", "bounds"):
        assert key in art, f"artefact key {key} missing"
    assert art["season"] == "2026-27" and art["gw"] == 3
    assert art["horizon_gws"] == [3, 4, 5, 6, 7]
    assert art["objective_mode"] == "expected_points", (
        "the currency is named, in writing — the read side labels every "
        "number with it"
    )
    assert art["generated_at"] == "2026-09-01T12:00:00+00:00"
    json.dumps(art)   # no numpy scalars, Money objects or datetimes leak


def test_chosen_serialises_the_move_and_the_first_gw_decision():
    art = _serialize(_rec())
    c = art["chosen"]
    assert c["out"] == [131] and c["in"] == [202]
    assert c["n_transfers"] == 1 and c["hits"] == 0 and c["hit_points"] == 0
    assert c["objective"] == pytest.approx(123.4)
    assert c["bank_after_tenths"] == 5, "Money serialises as tenths"
    assert c["captain"] == 120 and c["vice_captain"] == 121
    assert c["starting_xi"] == list(range(100, 111))


def test_gain_over_roll_is_the_recommendations_own_property():
    art = _serialize(_rec(roll_objective=120.1))
    assert art["roll"] == {"objective": pytest.approx(120.1)}
    assert art["gain_over_roll"] == pytest.approx(123.4 - 120.1)


def test_no_roll_means_null_roll_and_null_gain_never_a_zero():
    art = _serialize(_rec(roll_objective=None))
    assert art["roll"] is None
    assert art["gain_over_roll"] is None, (
        "an unmeasured gain is null, not 0.0 — a zero would read as a "
        "measured tie with rolling"
    )


def test_alternatives_are_capped_at_five():
    art = _serialize(_rec(n_alternatives=9))
    assert len(art["alternatives"]) == 5
    a = art["alternatives"][0]
    assert set(a) == {"out", "in", "n_transfers", "hits", "hit_points",
                      "objective", "chip", "label"}


def test_hit_verdicts_serialise_via_their_own_to_dict():
    v = HitVerdict(label="1-transfer move", hits=1, hit_points=4,
                   expected_gain=5.2, breakeven_gain=3.9,
                   s_weekly_after=8.0, justified=True)
    art = _serialize(_rec(hit_verdicts=(v,)))
    assert art["hit_verdicts"][0]["justified"] is True
    assert art["hit_verdicts"][0]["breakeven_gain"] == pytest.approx(3.9)


def test_bounds_names_the_caps_and_the_best_found_caveat():
    art = _serialize(_rec())
    assert "25/position" in art["bounds"]
    assert "60s per MILP" in art["bounds"]
    assert "not a proven optimum" in art["bounds"]


def test_the_transfers_mode_maps_to_fpl_recommend_in_the_runner():
    """The dashboard's Re-run is the squad-anchored solver, never the
    from-scratch ideal-squad solve, holding chips (the owner's decision) at
    the cap that actually solves the GW4-8 problem."""
    from fpl_edge.platform import solve_runner
    cmd = solve_runner._default_command("transfers")
    assert cmd.startswith("uv run fpl recommend --commit")
    assert "--no-chips" in cmd
    assert "--seconds 150" in cmd and "--max-candidates 20" in cmd
    assert "--max-hits 0" in cmd
    assert "fpl solve" not in cmd
    assert solve_runner._default_command("points").startswith("uv run fpl solve")

def test_the_artefact_records_whether_chips_were_allowed():
    """The dashboard runs --no-chips (chips are the owner's decision); a plan
    must say which regime it was solved under so a wildcard plan and a
    transfer plan are never confused."""
    import datetime as dt

    from fpl_edge.cli.recommend import serialize_recommendation
    rec = _rec() if "_rec" in globals() else None
    assert rec is not None, "test module needs its recommendation fixture"
    now = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
    assert serialize_recommendation(rec, generated_at=now, max_candidates=20, seconds=150.0)["chips_allowed"] is True
    assert serialize_recommendation(rec, generated_at=now, max_candidates=20, seconds=150.0,
                                    chips_allowed=False)["chips_allowed"] is False


def test_the_artefact_carries_the_hit_cap_and_the_displaced_unconstrained_best():
    """The dashboard runs --max-hits 0: the headline stays inside the free
    transfers and the optimiser's hit-taking top move rides beside it as
    `unconstrained`, with its own gain, never hidden."""
    import datetime as dt
    from fpl_edge.cli.recommend import serialize_recommendation
    rec = _rec()
    now = dt.datetime(2026, 9, 7, tzinfo=dt.UTC)
    base = serialize_recommendation(rec, generated_at=now, max_candidates=20, seconds=150.0)
    assert base["max_hits"] == -1 and base["unconstrained"] is None
    gated = serialize_recommendation(rec, generated_at=now, max_candidates=20, seconds=150.0,
                                     max_hits=0, unconstrained=rec.chosen)
    assert gated["max_hits"] == 0
    assert gated["unconstrained"]["out"] == [int(c) for c in rec.chosen.out]
    assert gated["unconstrained"]["gain_over_roll"] == base["gain_over_roll"]
