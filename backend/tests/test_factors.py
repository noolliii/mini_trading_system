"""OHLCV 파생 팩터 컬럼. DB 없이 돕니다."""

from datetime import datetime

import pytest

from app.factors import (
    close_pos,
    ma_gap,
    night_return_std,
    night_win_rate,
    return_5d,
    turnover,
    upper_shadow_ratio,
    volume_ratio,
)
from app.schemas import Candle

D0 = datetime(2026, 10, 6)


def _candle(o: float, h: float, l: float, c: float, v: int = 1_000_000) -> Candle:
    return Candle(time=D0, open=o, high=h, low=l, close=c, volume=v)


def test_close_pos_is_one_at_the_high_and_zero_at_the_low() -> None:
    assert close_pos(_candle(100, 110, 95, 110)) == pytest.approx(1.0)
    assert close_pos(_candle(100, 110, 95, 95)) == pytest.approx(0.0)


def test_close_pos_is_safe_when_high_equals_low() -> None:
    assert close_pos(_candle(100, 100, 100, 100)) == 0.5


def test_upper_shadow_ratio_is_zero_for_a_candle_closing_at_the_high() -> None:
    assert upper_shadow_ratio(_candle(100, 110, 95, 110)) == pytest.approx(0.0)


def test_upper_shadow_ratio_is_large_for_a_long_wick() -> None:
    c = _candle(100, 120, 99, 101)  # 고가 120, 종가 101 — 윗꼬리가 거의 전부
    assert upper_shadow_ratio(c) == pytest.approx((120 - 101) / (120 - 99))


def test_turnover_is_close_times_volume() -> None:
    assert turnover(_candle(100, 101, 99, 100, v=1000)) == pytest.approx(100_000)


def test_volume_ratio_needs_twenty_days_of_history() -> None:
    c = _candle(100, 101, 99, 100, v=2_000_000)
    assert volume_ratio(c, [1_000_000] * 19) is None
    assert volume_ratio(c, [1_000_000] * 20) == pytest.approx(2.0)


def test_ma_gap_needs_twenty_days_of_history() -> None:
    c = _candle(110, 111, 109, 110)
    assert ma_gap(c, [100.0] * 19) is None
    assert ma_gap(c, [100.0] * 20) == pytest.approx(0.10)


def test_return_5d_needs_five_days_of_history() -> None:
    c = _candle(110, 111, 109, 110)
    assert return_5d(c, [100.0, 100.0, 100.0, 100.0]) is None
    assert return_5d(c, [100.0, 100.0, 100.0, 100.0, 100.0]) == pytest.approx(0.10)


def test_night_win_rate_needs_twenty_nights_of_history() -> None:
    mixed = [0.01, -0.01] * 10  # 10승 10패
    assert night_win_rate(mixed[:19]) is None
    assert night_win_rate(mixed) == pytest.approx(0.5)


def test_night_return_std_needs_twenty_nights_of_history() -> None:
    varied = [0.01, -0.01] * 10
    assert night_return_std(varied[:19]) is None
    assert night_return_std(varied) == pytest.approx(0.01)


def test_night_return_std_is_zero_when_returns_never_vary() -> None:
    assert night_return_std([0.01] * 20) == pytest.approx(0.0)
