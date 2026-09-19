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

UTC = dt.UTC


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
    # gain_over_roll joined the move's key set on 2026-09-19: the alternatives
    # read as empty stubs on the Planner because an objective total with no
    # baseline beside it is not a fact a reader can use, and the serializer
    # computed the subtraction for the unconstrained row alone.
    assert set(a) == {"out", "in", "n_transfers", "hits", "hit_points",
                      "objective", "gain_over_roll", "chip", "label"}


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


def test_the_artefact_records_the_constraints_it_was_solved_under():
    art = serialize_recommendation(
        _rec(), generated_at=dt.datetime(2026, 9, 1, 12, tzinfo=UTC),
        max_candidates=20, seconds=150.0, chips_allowed=False, max_hits=0,
        constraints={"horizon": 5, "max_hits": 0, "chips": [],
                     "must_keep": [219168], "ban": [95658], "seconds": 150.0,
                     "max_candidates": 20, "candidates": 8},
    )
    assert art["constraints"]["must_keep"] == [219168]
    assert art["constraints"]["ban"] == [95658]
    json.dumps(art)
    bare = serialize_recommendation(
        _rec(), generated_at=dt.datetime(2026, 9, 1, 12, tzinfo=UTC),
        max_candidates=20, seconds=150.0)
    assert bare["constraints"] == {}, "absent constraints serialise as an empty block, never null"


def test_parse_codes_takes_integers_only():
    import typer

    from fpl_edge.cli.recommend import parse_codes
    assert parse_codes("219168, 108416,,", flag="--ban") == frozenset({219168, 108416})
    assert parse_codes("", flag="--ban") == frozenset()
    with pytest.raises(typer.BadParameter):
        parse_codes("Salah", flag="--must-keep")


def test_the_artefact_names_the_forecast_currency_and_its_fill_share():
    """The gain reads 'vs rolling, consensus forecast', not just 'vs rolling'."""
    art = serialize_recommendation(
        _rec(), generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        max_candidates=25, seconds=60.0,
        forecast={"forecast_source": "consensus", "engine_fill_share": 0.0125,
                  "rows_by_source": {"consensus": 2459, "engine_fill": 31}},
    )
    assert art["forecast_source"] == "consensus"
    assert art["forecast_engine_fill_share"] == pytest.approx(0.0125)
    assert art["forecast_rows_by_source"] == {"consensus": 2459, "engine_fill": 31}
    json.dumps(art)


def test_without_provenance_the_forecast_fields_are_null_not_guessed():
    art = _serialize(_rec())
    assert art["forecast_source"] is None
    assert art["forecast_engine_fill_share"] is None
    assert art["forecast_rows_by_source"] is None


def test_forecast_provenance_is_read_off_the_parquets_own_rows():
    import pandas as pd

    from fpl_edge.cli.recommend import forecast_provenance

    frame = pd.DataFrame({
        "code": [1, 1, 2, 2, 3, 3],
        "gw": [4, 5, 4, 5, 4, 5],
        "xpts": [1.0] * 6, "p_play": [0.9] * 6,
        "source": ["consensus", "consensus", "consensus", "engine_fill",
                   "engine_fill", "engine_fill"],
        "forecast_source": ["consensus"] * 6,
    })
    prov = forecast_provenance(frame, gws=[4, 5])
    assert prov["forecast_source"] == "consensus"
    assert prov["engine_fill_share"] == pytest.approx(0.5)
    assert prov["rows_by_source"] == {"consensus": 3, "engine_fill": 3}
    # restricted to the horizon actually solved
    prov4 = forecast_provenance(frame, gws=[4])
    assert prov4["engine_fill_share"] == pytest.approx(1 / 3)
    # a legacy parquet (no provenance columns) was written by the engine
    legacy = frame[["code", "gw", "xpts", "p_play"]]
    prov_l = forecast_provenance(legacy, gws=[4, 5])
    assert prov_l["forecast_source"] == "engine"
    assert prov_l["engine_fill_share"] is None, "unmeasured is null, never 0.0"


# ---------------------------------------- the squad the plan was solved for --
# A plan is a statement about ONE squad: `out` and `in` are diffed against the
# fifteen held when the solve ran. Applied to a different fifteen they build a
# squad the optimiser never scored. On 2026-09-08 that reached the dashboard
# as a starting XI naming a player the squad card did not list, because the
# solve predated the account connection and neither surface could tell.


def test_the_artefact_records_the_squad_it_was_solved_against():
    rec = _rec()
    xi = list(rec.chosen.plan.decisions[0].starting_xi)
    art = serialize_recommendation(
        rec, generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        max_candidates=25, seconds=60.0,
        squad_before=[131, *xi], squad_source="PRIVATE_API",
    )
    assert art["squad_before"] == sorted([131, *xi]), (
        "sorted, so a diff against the held squad is stable"
    )
    assert art["squad_source"] == "PRIVATE_API"


def test_an_unrecorded_squad_is_an_empty_list_not_a_false_claim():
    art = serialize_recommendation(
        _rec(), generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        max_candidates=25, seconds=60.0,
    )
    assert art["squad_before"] == []
    assert art["squad_source"] is None


def test_a_starting_xi_outside_the_post_transfer_squad_is_refused():
    """Writing it would publish a lineup the optimiser never scored."""
    rec = _rec()
    with pytest.raises(ValueError, match="self-inconsistent"):
        serialize_recommendation(
            rec, generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
            max_candidates=25, seconds=60.0,
            # The XI defaults to 100..110; hold none of it and sell 131.
            squad_before=[131, 900, 901],
        )


def test_a_consistent_plan_passes_the_check():
    rec = _rec()
    xi = list(rec.chosen.plan.decisions[0].starting_xi)
    art = serialize_recommendation(
        rec, generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        max_candidates=25, seconds=60.0,
        # sell 131, buy 202, and hold every player the XI names
        squad_before=[131, *xi],
    )
    after = (set(art["squad_before"]) - set(art["chosen"]["out"])) \
        | set(art["chosen"]["in"])
    assert set(art["chosen"]["starting_xi"]) <= after


def test_every_alternative_carries_its_gain_against_the_solved_roll():
    """The four rows the Planner showed as empty stubs on 2026-09-19 were
    real solved moves whose only served descriptors were a blank label and an
    objective with no baseline. The gain is the same subtraction the headline
    gets, so the losing rows are comparable with the winning one."""
    art = _serialize(_rec(roll_objective=120.1))
    assert art["chosen"]["gain_over_roll"] == pytest.approx(123.4 - 120.1)
    for alt in art["alternatives"]:
        assert alt["gain_over_roll"] == pytest.approx(
            alt["objective"] - 120.1)
    # an unmeasured gain stays null rather than collapsing to a measured tie
    no_roll = _serialize(_rec(roll_objective=None))
    assert all(a["gain_over_roll"] is None for a in no_roll["alternatives"])


def test_a_move_the_solver_did_not_label_serialises_as_absent_not_blank():
    """An empty string renders as a labelled row whose label is blank, which
    is how four unlabelled alternatives came to read as four empty stubs."""
    art = _serialize(_rec())
    assert art["chosen"]["label"] is None
    assert all(a["label"] is None for a in art["alternatives"])


def test_the_captain_numbers_say_why_the_armband_landed_where_it_did():
    """The MILP's captain is the argmax of the rank-aware captaincy matrix,
    not of xPts, so it can trail the highest projected starter and did on
    2026-09-19: 3.99 against 6.14. Both numbers are the forecast's, at the
    first gameweek of the horizon, so one artefact answers the question
    without a join and forecast_source names the currency for both."""
    xi = list(range(100, 111))
    rec = _rec()
    gw0 = {c: 2.0 for c in xi}
    gw0[120] = 3.99          # the solver's captain
    gw0[107] = 6.14          # the best projected starter
    art = serialize_recommendation(
        rec, generated_at=dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        max_candidates=25, seconds=60.0, gw0_xpts=gw0,
    )
    c = art["chosen"]
    assert c["captain"] == 120
    assert c["captain_xpts"] == pytest.approx(3.99)
    assert c["best_xi_captain"] == {"code": 107, "xpts": pytest.approx(6.14)}


def test_captain_numbers_are_absent_rather_than_zero_without_a_forecast():
    art = _serialize(_rec())
    assert art["chosen"]["captain_xpts"] is None
    assert art["chosen"]["best_xi_captain"] is None


def test_the_free_transfer_count_travels_with_its_source():
    """The dashboard header read 2 and the solver read 1, and neither surface
    said which was FPL's own my-team limit and which was the engine's accrual
    reconstruction. The count now carries the answer."""
    rec = _rec()
    now = dt.datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    assert serialize_recommendation(
        rec, generated_at=now, max_candidates=25, seconds=60.0,
    )["free_transfers_source"] is None
    art = serialize_recommendation(
        rec, generated_at=now, max_candidates=25, seconds=60.0,
        free_transfers_source="account",
    )
    assert art["free_transfers"] == 2
    assert art["free_transfers_source"] == "account"
