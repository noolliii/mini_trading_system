"""매일 유니버스에서 Top-K 를 골라 종가 배팅하는 백테스트. DB 없이 돕니다.

가장 중요하게 보는 것은 슬라이드 7번이 경고한 미래참조 편향(Look-ahead Bias)이
실제로 없는지입니다 — 먼 미래의 일봉을 바꿔도 그보다 앞선 날의 선택은
흔들리지 않아야 합니다.
"""

from datetime import datetime

import pytest

from app.schemas import Candle
from app.selection import (
    _score_mean_reversion,
    _score_momentum,
    _score_rolling_edge,
    _score_volume_surge,
    backtest_top_k,
)

D0, D1, D2, D3 = (
    datetime(2026, 10, 6),
    datetime(2026, 10, 7),
    datetime(2026, 10, 8),
    datetime(2026, 10, 9),
)


def _candle(o: float, h: float, l: float, c: float, v: int = 1_000_000) -> Candle:
    return Candle(time=D0, open=o, high=h, low=l, close=c, volume=v)


# ──────────────────────────────────────────────────────────────
# 점수식 단위 테스트
# ──────────────────────────────────────────────────────────────


def test_momentum_is_one_when_close_equals_high() -> None:
    assert _score_momentum(_candle(100, 110, 95, 110)) == pytest.approx(1.0)


def test_momentum_is_zero_when_high_equals_low() -> None:
    """고가=저가(거래가 거의 없던 날)면 0으로 안전하게 둡니다. 0으로 나누지 않습니다."""
    assert _score_momentum(_candle(100, 100, 100, 100)) == 0.0


def test_mean_reversion_is_positive_for_a_losing_day() -> None:
    c = _candle(100, 101, 90, 92)  # r_day = -8%
    assert _score_mean_reversion(c) == pytest.approx(0.08)


def test_volume_surge_needs_twenty_days_of_history() -> None:
    assert _score_volume_surge(_candle(100, 101, 99, 102), [1_000_000] * 19) is None
    assert _score_volume_surge(_candle(100, 101, 99, 102), [1_000_000] * 20) is not None


def test_volume_surge_is_zero_on_a_down_day_even_with_huge_volume() -> None:
    """거래량이 터져도 음봉이면 0점입니다 — 공식이 I(양봉) 을 곱하기 때문입니다."""
    down_day = _candle(100, 101, 90, 95)  # close < open
    assert _score_volume_surge(down_day, [1_000_000] * 20) == 0.0


def test_rolling_edge_needs_twenty_nights_of_history() -> None:
    varied = [0.01, -0.005] * 10  # 변동이 있어야 표준편차가 0이 아닙니다
    assert _score_rolling_edge(varied[:19]) is None
    assert _score_rolling_edge(varied) is not None


def test_rolling_edge_is_none_when_returns_never_vary() -> None:
    """표준편차가 0이면 나눌 수 없습니다 — None 으로 그 종목을 후보에서 뺍니다."""
    assert _score_rolling_edge([0.01] * 20) is None


def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError):
        backtest_top_k({}, "레버리지")


# ──────────────────────────────────────────────────────────────
# 전체 백테스트 — 3종목 중 역추세(가장 많이 빠진 종목) 고르기
# ──────────────────────────────────────────────────────────────

# X: 낮에 +4%, Y: 낮에 -8%(가장 많이 빠짐 → 역추세 1등), Z: 낮에 -1%
UNIVERSE_2DAY = {
    "X": [
        Candle(time=D0, open=100, high=105, low=99, close=104, volume=1),
        Candle(time=D1, open=108, high=109, low=107, close=108.5, volume=1),
    ],
    "Y": [
        Candle(time=D0, open=100, high=102, low=90, close=92, volume=1),
        Candle(time=D1, open=95, high=96, low=94, close=95.5, volume=1),
    ],
    "Z": [
        Candle(time=D0, open=100, high=101, low=98, close=99, volume=1),
        Candle(time=D1, open=100, high=101, low=99, close=100.5, volume=1),
    ],
}


def test_mean_reversion_picks_the_biggest_intraday_drop() -> None:
    result = backtest_top_k(UNIVERSE_2DAY, "mean_reversion", k=1, cost=0.0)

    assert len(result.picks) == 1
    assert result.picks[0].tickers == ["Y"]

    r_night_y = 95 / 92 - 1
    assert result.summary.final_portfolio == pytest.approx(r_night_y * 100)

    r_night_x = 108 / 104 - 1
    r_night_z = 100 / 99 - 1
    expected_universe = (r_night_x + r_night_y + r_night_z) / 3
    assert result.summary.final_universe == pytest.approx(expected_universe * 100)


def test_cost_is_deducted_from_the_realized_return() -> None:
    free = backtest_top_k(UNIVERSE_2DAY, "mean_reversion", k=1, cost=0.0)
    taxed = backtest_top_k(UNIVERSE_2DAY, "mean_reversion", k=1, cost=0.01)

    # 같은 종목을 고르고, 수익률만 비용만큼 정확히 낮아야 합니다
    assert taxed.picks == free.picks
    assert free.summary.final_portfolio - taxed.summary.final_portfolio == pytest.approx(1.0)


def test_future_candles_never_change_an_earlier_pick() -> None:
    """미래참조 편향이 없는지 보는 테스트 — 가장 먼 미래의 일봉을 통째로 바꿔도
    그보다 앞선 전환일의 점수·선택·실현수익은 한 글자도 바뀌면 안 됩니다.
    """
    dates = [D0, D1, D2, D3]
    base = {
        "A": [Candle(time=d, open=100 + i, high=112 + i, low=98 + i, close=108 + i, volume=1) for i, d in enumerate(dates)],
        "B": [Candle(time=d, open=50 + i, high=56 + i, low=49 + i, close=50.5 + i, volume=1) for i, d in enumerate(dates)],
    }
    mutated = {
        "A": base["A"][:-1] + [Candle(time=D3, open=200, high=999, low=1, close=500, volume=1)],
        "B": base["B"][:-1] + [Candle(time=D3, open=5, high=6000, low=1, close=4, volume=1)],
    }

    result_base = backtest_top_k(base, "momentum", k=1, cost=0.0)
    result_mutated = backtest_top_k(mutated, "momentum", k=1, cost=0.0)

    # D0→D1, D1→D2 두 전환(점수는 D0·D1 자신의 데이터로만 매깁니다)은 그대로여야 합니다
    assert result_base.picks[:2] == result_mutated.picks[:2]
    assert result_base.points[0] == result_mutated.points[0]
    assert result_base.points[1] == result_mutated.points[1]

    # 마지막 전환(D2→D3)은 D3 의 시가가 바뀌어 실현수익이 달라질 수 있습니다 —
    # 즉 "바뀔 수 있는 자리만 바뀐다"를 함께 확인합니다
    assert result_base.points[2] != result_mutated.points[2]
