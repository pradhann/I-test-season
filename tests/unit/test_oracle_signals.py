"""The oracle's aggregation rules.

Each test encodes a way that naively averaging opinions goes wrong.
"""

from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.oracle.signals import (
    Direction,
    Signal,
    SourceKind,
    SourceWeight,
    aggregate,
)

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 8, 20, 12, tzinfo=UTC)
DEADLINE = dt.datetime(2026, 8, 21, 17, 30, tzinfo=UTC)


def sig(source, kind, direction, strength=1.0, as_of=T0, code=1) -> Signal:
    return Signal(player_code=code, kind=kind, source=source, direction=direction,
                  strength=strength, rationale=f"{source} says {direction.name}",
                  as_of=as_of)


def proven(source, kind, hit_rate=0.75, sample=200) -> SourceWeight:
    return SourceWeight(source=source, kind=kind, hit_rate=hit_rate, sample=sample)


def test_signal_rejects_naive_timestamp() -> None:
    with pytest.raises(ValueError, match="naive"):
        Signal(player_code=1, kind=SourceKind.CREATOR, source="x",
               direction=Direction.FOR, strength=1.0, rationale="",
               as_of=dt.datetime(2026, 8, 20, 12))


def test_claim_published_after_the_deadline_is_invisible() -> None:
    """The whole point of as_of: Friday-night news cannot inform a Friday
    morning decision, and a backtest of the oracle must not read the future."""
    late = sig("harry", SourceKind.CREATOR, Direction.STRONG_FOR,
               as_of=DEADLINE + dt.timedelta(hours=1))
    weights = {"harry": proven("harry", SourceKind.CREATOR)}
    assert aggregate([late], weights, as_of=DEADLINE) == {}


def test_source_with_no_track_record_carries_no_weight() -> None:
    """An unweighted consensus of pundits is the template with extra steps."""
    s = sig("unknown_pundit", SourceKind.CREATOR, Direction.STRONG_FOR)
    unmeasured = SourceWeight(source="unknown_pundit", kind=SourceKind.CREATOR,
                              hit_rate=None, sample=0)
    v = aggregate([s], {"unknown_pundit": unmeasured}, as_of=DEADLINE)[1]
    assert v.score == 0.0
    assert "unknown_pundit" in v.unweighted_sources
    assert "no track record" in unmeasured.explain()


def test_coin_flip_source_gets_zero_weight() -> None:
    sw = SourceWeight(source="coin", kind=SourceKind.CREATOR, hit_rate=0.5, sample=500)
    assert sw.weight == 0.0


def test_small_sample_is_shrunk_toward_the_prior() -> None:
    """A perfect record over four claims must not move a decision."""
    lucky = SourceWeight(source="lucky", kind=SourceKind.CREATOR, hit_rate=1.0, sample=4)
    seasoned = SourceWeight(source="seasoned", kind=SourceKind.CREATOR,
                            hit_rate=0.65, sample=400)
    assert lucky.weight < seasoned.weight


def test_five_creators_echoing_one_take_do_not_count_as_five() -> None:
    """Sources within a family are correlated; combined weight grows sub-linearly."""
    weights, one, many = {}, [], []
    for i in range(5):
        name = f"creator{i}"
        weights[name] = proven(name, SourceKind.CREATOR)
        many.append(sig(name, SourceKind.CREATOR, Direction.FOR))
    one.append(many[0])

    solo = aggregate(one, weights, as_of=DEADLINE)[1].score
    crowd = aggregate(many, weights, as_of=DEADLINE)[1].score
    assert crowd > solo                      # more evidence helps
    assert crowd < 5 * solo                  # but far less than five times


def test_independent_families_outweigh_one_loud_family() -> None:
    """Six creators agreeing is weaker than model + market + elite agreeing."""
    weights = {}
    creators = []
    for i in range(6):
        n = f"creator{i}"
        weights[n] = proven(n, SourceKind.CREATOR)
        creators.append(sig(n, SourceKind.CREATOR, Direction.FOR, code=1))

    diverse = []
    for n, k in [("dixon_coles", SourceKind.OWN_MODEL),
                 ("paddypower", SourceKind.MARKET),
                 ("elite", SourceKind.ELITE_MANAGER)]:
        weights[n] = proven(n, k)
        diverse.append(sig(n, k, Direction.FOR, code=2))

    v_creators = aggregate(creators, weights, as_of=DEADLINE)[1]
    v_diverse = aggregate(diverse, weights, as_of=DEADLINE)[2]
    assert v_diverse.confidence > v_creators.confidence


def test_opposing_signals_cancel() -> None:
    weights = {"a": proven("a", SourceKind.OWN_MODEL),
               "b": proven("b", SourceKind.OWN_MODEL)}
    both = [sig("a", SourceKind.OWN_MODEL, Direction.STRONG_FOR),
            sig("b", SourceKind.OWN_MODEL, Direction.STRONG_AGAINST)]
    assert aggregate(both, weights, as_of=DEADLINE)[1].score == pytest.approx(0.0)


def test_verdict_explains_its_working() -> None:
    weights = {"dixon_coles": proven("dixon_coles", SourceKind.OWN_MODEL)}
    v = aggregate([sig("dixon_coles", SourceKind.OWN_MODEL, Direction.FOR)],
                  weights, as_of=DEADLINE)[1]
    text = v.explain(name="Haaland")
    assert "Haaland" in text and "dixon_coles" in text
    assert v.top_reasons()[0][0].source == "dixon_coles"


def test_strength_bounds_are_enforced() -> None:
    with pytest.raises(ValueError, match="strength"):
        Signal(player_code=1, kind=SourceKind.MARKET, source="x",
               direction=Direction.FOR, strength=1.5, rationale="", as_of=T0)


def test_aggregate_requires_aware_as_of() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        aggregate([], {}, as_of=dt.datetime(2026, 8, 21, 17, 30))
