"""MDD · 샤프지수 · 칼마지수. DB 없이 돕니다."""

import pytest

from app.metrics import cagr, calmar_ratio, daily_returns_from_cumulative, max_drawdown, sharpe_ratio


def test_daily_returns_round_trips_a_known_cumulative_series() -> None:
    # 0% -> +10% -> +21% 는 매일 정확히 +10%씩 복리로 불어난 것과 같습니다
    cum = [10.0, 21.0]
    returns = daily_returns_from_cumulative(cum)
    assert returns == pytest.approx([0.10, 0.10])


def test_max_drawdown_is_zero_for_a_straight_uptrend() -> None:
    assert max_drawdown([0.0, 5.0, 10.0, 20.0]) == pytest.approx(0.0)


def test_max_drawdown_measures_peak_to_trough_not_start_to_trough() -> None:
    """고점(+20%)에서 +4%로 떨어진 낙폭이지, 시작점(0%) 기준 낙폭이 아닙니다."""
    cum = [0.0, 20.0, 4.0, 10.0]
    expected = (1.04) / (1.20) - 1  # 약 -13.3%
    assert max_drawdown(cum) == pytest.approx(expected * 100)


def test_max_drawdown_of_empty_series_is_zero() -> None:
    assert max_drawdown([]) == 0.0


def test_sharpe_is_zero_when_returns_never_vary() -> None:
    assert sharpe_ratio([0.01] * 30) == 0.0


def test_sharpe_is_positive_for_steady_positive_returns_with_noise() -> None:
    returns = [0.01, 0.008, 0.012, 0.009, 0.011] * 10
    assert sharpe_ratio(returns) > 0


def test_sharpe_needs_at_least_two_points() -> None:
    assert sharpe_ratio([0.01]) == 0.0
    assert sharpe_ratio([]) == 0.0


def test_calmar_divides_return_by_absolute_drawdown() -> None:
    assert calmar_ratio(50.0, -25.0) == pytest.approx(2.0)


def test_calmar_is_zero_when_there_was_essentially_no_drawdown() -> None:
    assert calmar_ratio(50.0, 0.0) == 0.0


def test_cagr_of_exactly_one_year_equals_the_total_return() -> None:
    assert cagr(10.0, 252) == pytest.approx(10.0)


def test_cagr_annualizes_a_shorter_period_upward() -> None:
    # 반년(126 거래일) 만에 +10% 났다면, 1년을 유지했을 때는 그보다 더 큽니다
    assert cagr(10.0, 126) == pytest.approx((1.10 ** 2 - 1) * 100)


def test_cagr_is_zero_with_no_trading_days() -> None:
    assert cagr(10.0, 0) == 0.0


def test_cagr_floors_at_total_loss() -> None:
    assert cagr(-100.0, 252) == -100.0
