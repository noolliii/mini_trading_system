"""코스피 100 같은 유니버스의 동일가중 낮 · 밤 · 단순보유 비교. DB 없이 돕니다.

두 종목(A, B)을 쓰는데, B는 상장(수집)이 하루 늦게 시작해서 날짜 집합이
어긋납니다 — "그날 값을 가진 종목끼리만 평균 낸다"는 규칙이 실제로 그렇게
동작하는지가 이 테스트의 핵심입니다.
"""

from datetime import datetime

import pytest

from app.schemas import Candle
from app.universe import compute_universe

D1, D2, D3 = datetime(2026, 10, 6), datetime(2026, 10, 7), datetime(2026, 10, 8)


def _candle(t: datetime, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(time=t, open=o, high=h, low=l, close=c, volume=1_000_000)


# A: 사흘 내내 있음. B: D2 부터만 있음(늦게 수집을 시작한 종목 흉내)
CANDLES_A = [
    _candle(D1, 100, 112, 98, 110),
    _candle(D2, 108, 122, 107, 120),
    _candle(D3, 121, 123, 109, 110),
]
CANDLES_B = [
    _candle(D2, 50, 56, 49, 55),
    _candle(D3, 56, 61, 55, 60),
]

UNIVERSE = {"A": ("A전자", CANDLES_A), "B": ("B물산", CANDLES_B)}


def test_first_day_only_has_the_ticker_that_exists_then() -> None:
    """D1 에는 A 밖에 없으니, 평균은 그냥 A 하나의 값이어야 합니다."""
    result = compute_universe(UNIVERSE)
    first = result.points[0]
    assert first.time == D1
    assert first.cum_day == pytest.approx(10.0)  # A: 110/100 - 1
    assert first.cum_night == pytest.approx(0.0)  # 아무도 전일 종가가 없습니다


def test_points_are_sorted_by_time() -> None:
    result = compute_universe(UNIVERSE)
    times = [p.time for p in result.points]
    assert times == sorted(times)
    assert times == [D1, D2, D3]


def test_day_times_night_telescopes_into_buy_hold_for_the_universe_too() -> None:
    result = compute_universe(UNIVERSE)
    for p in result.points:
        combined = (1 + p.cum_day / 100) * (1 + p.cum_night / 100)
        assert combined == pytest.approx(1 + p.cum_buy_hold / 100, rel=1e-9)


def test_ticker_summaries_are_ranked_by_final_night_return() -> None:
    """A 는 하루 밤(D2→D3 구간 중 D2 진입분)이 마이너스라 최종 밤수익률이 B보다 낮습니다."""
    result = compute_universe(UNIVERSE)
    assert [s.ticker for s in result.tickers] == ["B", "A"]

    a = next(s for s in result.tickers if s.ticker == "A")
    b = next(s for s in result.tickers if s.ticker == "B")

    assert a.final_night == pytest.approx(-1.0, abs=1e-6)
    assert a.final_day == pytest.approx(100 / 9, abs=1e-6)  # 10/9 * 100
    assert a.candle_count == 3

    assert b.final_night == pytest.approx(100 / 55, abs=1e-6)  # 1/55 * 100
    assert b.win_rate_day == pytest.approx(1.0)
    assert b.candle_count == 2


def test_empty_universe_returns_no_points() -> None:
    result = compute_universe({})
    assert result.points == []
    assert result.tickers == []
