"""The claim grammar: every template parses, renders, and grades correctly."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from fpl_edge.theses.grammar import (
    TEMPLATES,
    UngradeableClaimError,
    default_prediction,
    grade,
    parse,
    render,
)
from fpl_edge.theses.model import ClaimType, Thesis, ThesisOutcome, ThesisSource

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 8, 18, 23, 0, tzinfo=UTC)


def _thesis(prediction: str, *, claim_type=ClaimType.BUY, comparator=(201, 202, 203, 204)):
    return Thesis(
        id="2026-08-18-test-buy",
        created=T0,
        source=ThesisSource.USER_CHAT,
        raw_input="test",
        player="Hero",
        player_code=101,
        season="2026-27",
        claim_type=claim_type,
        gw_start=1,
        horizon_gws=3,
        falsifiable_prediction=prediction,
        comparator_codes=tuple(comparator),
        model_verdict_at_creation={"as_of": T0},
    )


def _results(rows):
    """rows: (code, gw, points, minutes, starts, goals, assists)."""
    return pd.DataFrame(
        [
            {
                "code": c, "gw": g, "total_points": p, "minutes": m,
                "starts": s, "goals_scored": go, "assists": a, "fixture_id": g * 100 + c,
            }
            for c, g, p, m, s, go, a in rows
        ]
    )


def test_every_template_round_trips_through_its_own_renderer():
    samples = {
        "beats_peer_median": dict(a=1, b=6),
        "beats_peer_median_by": dict(n=10, a=2, b=7),
        "trails_peer_median": dict(a=1, b=6),
        "beats_named_player": dict(name="Haaland", code=223094, a=1, b=6),
        "beats_top_captain": dict(name="M.Salah", code=118748, k=4),
        "beats_captain_pool_median": dict(a=3, b=3),
        "starts_at_least": dict(n=4, a=1, b=6),
        "total_points_at_least": dict(n=25, a=1, b=6),
        "attacking_returns_at_least": dict(n=3, a=1, b=6),
    }
    assert set(samples) == {t.id for t in TEMPLATES}, "every template must be sampled"
    for template_id, params in samples.items():
        sentence = render(template_id, **params)
        matched, parsed = parse(sentence)
        assert matched.id == template_id
        # Renderer and parser agree on the parameters.
        for key, value in params.items():
            assert str(value) == parsed[key]


def test_free_text_is_refused_with_the_template_list():
    with pytest.raises(UngradeableClaimError) as exc:
        parse("Rashford is going to have a great season")
    assert "claim_type=watch" in str(exc.value)
    assert "beats_peer_median" in str(exc.value)


def test_backwards_window_is_refused():
    with pytest.raises(UngradeableClaimError, match="backwards"):
        parse("outscores positional price-peer median over GW6-GW1")


def test_beats_peer_median_grades_strictly_with_push():
    results = _results(
        [(101, g, 5, 90, 1, 0, 0) for g in (1, 2, 3)]
        + [(c, g, 3, 90, 1, 0, 0) for c in (201, 202, 203, 204) for g in (1, 2, 3)]
    )
    t = _thesis("outscores positional price-peer median over GW1-GW3")
    g = grade(t, results)
    assert g.outcome is ThesisOutcome.CORRECT
    assert g.subject_points == 15.0 and g.comparator_points == 9.0
    assert g.margin == 6.0

    # A tie is a push, not a win.
    tied = _results(
        [(101, g, 3, 90, 1, 0, 0) for g in (1, 2, 3)]
        + [(c, g, 3, 90, 1, 0, 0) for c in (201, 202, 203, 204) for g in (1, 2, 3)]
    )
    assert grade(t, tied).outcome is ThesisOutcome.PUSH


def test_peer_median_is_median_of_member_totals_not_per_week_medians():
    # Two peers alternate 0/6; per-week medians would sum to 6 while each
    # member's own total is 6 -- distinguishable if a third peer totals 12.
    rows = [(101, g, 3, 90, 1, 0, 0) for g in (1, 2)]
    rows += [(201, 1, 0, 90, 1, 0, 0), (201, 2, 6, 90, 1, 0, 0)]
    rows += [(202, 1, 6, 90, 1, 0, 0), (202, 2, 0, 90, 1, 0, 0)]
    rows += [(203, 1, 6, 90, 1, 0, 0), (203, 2, 6, 90, 1, 0, 0)]
    t = Thesis(
        id="x", created=T0, source=ThesisSource.USER_CHAT, raw_input="t",
        player="Hero", player_code=101, season="2026-27", claim_type=ClaimType.BUY,
        gw_start=1, horizon_gws=2,
        falsifiable_prediction="outscores positional price-peer median over GW1-GW2",
        comparator_codes=(201, 202, 203),
        model_verdict_at_creation={},
    )
    g = grade(t, _results(rows))
    assert g.comparator_points == 6.0  # median of member totals {6, 6, 12}
    assert g.outcome is ThesisOutcome.PUSH  # subject 6 vs 6


def test_missing_players_score_zero_not_nan():
    # Peer 204 has no rows at all: an unused player returns nothing.
    results = _results([(101, 1, 1, 90, 1, 0, 0)])
    t = _thesis(
        "outscores positional price-peer median over GW1-GW3", comparator=(204, 205, 206, 207)
    )
    g = grade(t, results)
    assert g.comparator_points == 0.0
    assert g.outcome is ThesisOutcome.CORRECT


def test_empty_comparator_voids_instead_of_inventing_one():
    t = _thesis("outscores positional price-peer median over GW1-GW3", comparator=())
    g = grade(t, _results([(101, 1, 5, 90, 1, 0, 0)]))
    assert g.outcome is ThesisOutcome.VOID


def test_avoid_call_inverts_the_comparison():
    results = _results(
        [(101, 1, 1, 90, 1, 0, 0)]
        + [(c, 1, 5, 90, 1, 0, 0) for c in (201, 202, 203, 204)]
    )
    t = Thesis(
        id="x", created=T0, source=ThesisSource.USER_CHAT, raw_input="t",
        player="Villain", player_code=101, season="2026-27", claim_type=ClaimType.AVOID,
        gw_start=1, horizon_gws=1,
        falsifiable_prediction="scores fewer pts than positional price-peer median over GW1-GW1",
        comparator_codes=(201, 202, 203, 204),
        model_verdict_at_creation={},
    )
    assert grade(t, results).outcome is ThesisOutcome.CORRECT


def test_named_player_and_top_captain_grade_from_the_embedded_code():
    results = _results([(101, 4, 12, 90, 1, 1, 0), (118748, 4, 7, 90, 1, 1, 0)])
    t = _thesis("outscores M.Salah (code 118748) over GW4-GW4")
    g = grade(t, results)
    assert g.outcome is ThesisOutcome.CORRECT and g.comparator_points == 7.0

    t2 = Thesis(
        id="y", created=T0, source=ThesisSource.CREATOR, creator="FPL Harry",
        raw_input="t", player="Hero", player_code=101, season="2026-27",
        claim_type=ClaimType.CAPTAIN, gw_start=4, horizon_gws=1,
        falsifiable_prediction="outscores the most-captained player M.Salah (code 118748) in GW4",
        model_verdict_at_creation={},
    )
    assert grade(t2, results).outcome is ThesisOutcome.CORRECT


def test_starts_at_least_counts_gameweeks_via_starts_column():
    rows = [
        (101, 1, 2, 90, 1, 0, 0),
        (101, 2, 1, 20, 0, 0, 0),   # cameo, not a start
        (101, 3, 2, 90, 1, 0, 0),
    ]
    t = _thesis("starts in 2+ of GW1-GW3", claim_type=ClaimType.MINUTES, comparator=())
    g = grade(t, _results(rows))
    assert g.outcome is ThesisOutcome.CORRECT and g.observed == 2.0

    t2 = _thesis("starts in 3+ of GW1-GW3", claim_type=ClaimType.MINUTES, comparator=())
    assert grade(t2, _results(rows)).outcome is ThesisOutcome.INCORRECT


def test_starts_fallback_to_60_minutes_for_archive_seasons():
    rows = pd.DataFrame(
        [
            {"code": 101, "gw": 1, "total_points": 2, "minutes": 90, "fixture_id": 1},
            {"code": 101, "gw": 2, "total_points": 1, "minutes": 30, "fixture_id": 2},
        ]
    )
    t = _thesis("starts in 1+ of GW1-GW2", claim_type=ClaimType.MINUTES, comparator=())
    g = grade(t, rows)
    assert g.observed == 1.0


def test_attacking_returns_counts_goals_plus_assists():
    rows = _results([(101, 1, 9, 90, 1, 1, 1), (101, 2, 5, 90, 1, 0, 1)])
    t = _thesis(
        "returns 3+ goal involvements over GW1-GW2",
        claim_type=ClaimType.OUT_OF_POSITION, comparator=(),
    )
    g = grade(t, rows)
    assert g.outcome is ThesisOutcome.CORRECT and g.observed == 3.0


def test_default_predictions_per_claim_type():
    assert default_prediction(ClaimType.WATCH, gw_start=1, horizon_gws=6) is None
    assert default_prediction(ClaimType.BUY, gw_start=1, horizon_gws=6) == \
        "outscores positional price-peer median over GW1-GW6"
    assert default_prediction(ClaimType.AVOID, gw_start=2, horizon_gws=3) == \
        "scores fewer pts than positional price-peer median over GW2-GW4"
    # minutes: two-thirds of the window, rounded up.
    assert default_prediction(ClaimType.MINUTES, gw_start=1, horizon_gws=6) == \
        "starts in 4+ of GW1-GW6"
    assert default_prediction(
        ClaimType.CAPTAIN, gw_start=5, horizon_gws=1,
        captain_name="Haaland", captain_code=223094,
    ) == "outscores the most-captained player Haaland (code 223094) in GW5"
    # No known captain pool: fall back to the pool-median template.
    assert default_prediction(ClaimType.CAPTAIN, gw_start=5, horizon_gws=1) == \
        "outscores the median of the frozen captain pool over GW5-GW5"
