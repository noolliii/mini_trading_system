"""낮(장중) vs 밤(종가 배팅) 백테스트. DB 없이, 일봉 몇 개만으로 돕니다.

예시 숫자는 Week 6 강의 슬라이드(005930, 10/06~10/13)와 같습니다 — 슬라이드의
"③번 행은 밤+1.0%인데 낮-2.1% 음봉" 포인트를 여기서도 그대로 확인합니다.
"""

from datetime import datetime

import pytest

from app.backtest import compute_day_night
from app.schemas import Candle


def _candle(t: str, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(time=datetime.fromisoformat(t), open=o, high=h, low=l, close=c, volume=1_000_000)


CANDLES = [
    _candle("2026-10-06", 59200, 60800, 58900, 60500),
    _candle("2026-10-07", 61100, 62000, 60700, 61800),
    _candle("2026-10-08", 62400, 62600, 60800, 61100),  # 밤+1.0%인데 낮은 음봉(-2.1%)
    _candle("2026-10-12", 61500, 61900, 59800, 60200),
    _candle("2026-10-13", 60900, 63000, 60600, 62700),
]


def test_empty_candles_returns_empty_result() -> None:
    result = compute_day_night([])
    assert result.points == []
    assert result.summary.final_day == 0.0
    assert result.summary.win_rate_night == 0.0


def test_first_day_has_no_night_return() -> None:
    """첫날은 전일 종가가 없어 밤수익률을 정의할 수 없습니다 — 0%로 둡니다."""
    result = compute_day_night(CANDLES)
    assert result.points[0].cum_night == 0.0


def test_third_candle_is_a_bearish_day_with_a_positive_night() -> None:
    """슬라이드가 짚은 포인트: 10-08은 밤에는 올랐지만(+1.0%) 낮에는 음봉(-2.1%)입니다."""
    result = compute_day_night(CANDLES)
    r_day_3rd = CANDLES[2].close / CANDLES[2].open - 1
    r_night_3rd = CANDLES[2].open / CANDLES[1].close - 1
    assert r_day_3rd < 0
    assert r_night_3rd > 0
    assert r_day_3rd * 100 == pytest.approx(-2.0833, abs=1e-3)
    assert r_night_3rd * 100 == pytest.approx(0.9709, abs=1e-3)


def test_day_times_night_telescopes_into_buy_hold() -> None:
    """(1+낮누적)×(1+밤누적) 은 날마다 단순보유 누적과 정확히 같아야 합니다.

    낮만 따로, 밤만 따로 복리로 곱해도 둘을 합치면 "첫날 시가에 사서 들고만
    있었을 때"와 같아진다는, backtest.py 모듈 docstring의 항등식입니다.
    """
    result = compute_day_night(CANDLES)
    for p in result.points:
        combined = (1 + p.cum_day / 100) * (1 + p.cum_night / 100)
        assert combined == pytest.approx(1 + p.cum_buy_hold / 100, rel=1e-9)


def test_summary_final_matches_last_point() -> None:
    result = compute_day_night(CANDLES)
    assert result.summary.final_day == pytest.approx(result.points[-1].cum_day)
    assert result.summary.final_night == pytest.approx(result.points[-1].cum_night)
    assert result.summary.final_buy_hold == pytest.approx(result.points[-1].cum_buy_hold)


def test_win_rates_count_positive_days_only() -> None:
    result = compute_day_night(CANDLES)
    r_days = [c.close / c.open - 1 for c in CANDLES]
    expected_day_wins = sum(1 for r in r_days if r > 0)
    assert result.summary.win_rate_day == pytest.approx(expected_day_wins / len(CANDLES))

    # 밤수익률은 첫날을 뺀 4번(10-07~10-13)만 정의됩니다
    r_nights = [
        CANDLES[i].open / CANDLES[i - 1].close - 1 for i in range(1, len(CANDLES))
    ]
    expected_night_wins = sum(1 for r in r_nights if r > 0)
    assert result.summary.win_rate_night == pytest.approx(expected_night_wins / len(r_nights))
