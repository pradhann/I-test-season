from __future__ import annotations

import datetime as dt

import pytest

from fpl_edge.types import Deadline, Money, Position, selling_price, GwId


def test_position_rejects_manager_element_type() -> None:
    """2025/26 element_type 5 was Manager. Coercing it would fabricate points."""
    with pytest.raises(ValueError, match="Manager"):
        Position.from_api(5)


def test_money_is_integer_tenths() -> None:
    assert Money.from_millions(7.5).tenths == 75
    with pytest.raises(ValueError):
        Money.from_millions(7.53)


@pytest.mark.parametrize(
    "bought, now, expected",
    [
        (75, 78, 76),   # official worked example: keep half the 0.3 rise, floor
        (75, 75, 75),   # no change
        (75, 70, 70),   # falls are borne in full
        (75, 76, 75),   # 0.1 rise: half of 1 tenth floors to 0
        (75, 77, 76),   # 0.2 rise -> +0.1
        (100, 111, 105),  # 1.1 rise -> +0.5 (floor of 5.5)
    ],
)
def test_selling_price(bought: int, now: int, expected: int) -> None:
    assert selling_price(Money(bought), Money(now)).tenths == expected


def test_deadline_must_be_utc_aware() -> None:
    with pytest.raises(ValueError, match="naive"):
        Deadline(GwId(1), dt.datetime(2026, 8, 21, 17, 30))
    with pytest.raises(ValueError, match="not UTC"):
        Deadline(GwId(1), dt.datetime(2026, 8, 21, 17, 30,
                                      tzinfo=dt.timezone(dt.timedelta(hours=1))))
    ok = Deadline(GwId(1), dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.timezone.utc))
    assert ok.gw == 1
