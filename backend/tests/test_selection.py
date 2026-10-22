"""매일 유니버스에서 Top-K 를 골라 종가 배팅하는 백테스트. DB 없이 돕니다.

가장 중요하게 보는 것은 슬라이드 7번이 경고한 미래참조 편향(Look-ahead Bias)이
실제로 없는지입니다 — 먼 미래의 일봉을 바꿔도 그보다 앞선 날의 선택은
흔들리지 않아야 합니다.
"""

from datetime import datetime

import pytest

from app.schemas import Candle
from app.selection import (
    _score_factor_combo,
    _score_mean_reversion,
    _score_momentum,
    _score_rolling_edge,
    _score_vol_filtered_edge,
    _score_vol_surge_edge,
    _score_vol_surge_trend,
    _score_volume_surge,
    backtest_top_k,
    best_k_result,
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
# 팩터콤보 — 20일선 위 · 양봉 · 꽉 찬 캔들만 통과하는 명시적 필터
# ──────────────────────────────────────────────────────────────

_RISING_20 = [100.0 + i for i in range(20)]  # 20일선 위에 있도록 만드는 과거 종가
_VARIED_NIGHTS_20 = [0.01, -0.005] * 10  # 야간샤프가 계산되도록 변동이 있는 과거 밤수익률


def test_factor_combo_rejects_a_bearish_candle() -> None:
    bearish = _candle(120, 121, 118, 119)  # close < open
    score = _score_factor_combo(bearish, _RISING_20, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score is None


def test_factor_combo_rejects_a_long_upper_shadow_even_if_bullish() -> None:
    long_wick = _candle(119, 140, 118, 120)  # 양봉이지만 윗꼬리가 캔들의 30%를 훌쩍 넘음
    score = _score_factor_combo(long_wick, _RISING_20, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score is None


def test_factor_combo_rejects_a_price_below_the_20_day_average() -> None:
    below_ma = _candle(90, 91, 89, 90)  # 20일선(평균 109.5)보다 한참 아래
    score = _score_factor_combo(below_ma, _RISING_20, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score is None


def test_factor_combo_scores_a_candidate_that_passes_every_filter() -> None:
    clean_bull = _candle(119, 121, 118, 120.5)  # 20일선 위 · 양봉 · 윗꼬리 짧음
    score = _score_factor_combo(clean_bull, _RISING_20, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score is not None
    assert score > 0


def test_backtest_top_k_accepts_factor_combo_as_a_known_strategy() -> None:
    result = backtest_top_k({}, "factor_combo")
    assert result.strategy == "factor_combo"
    assert result.points == []


# ──────────────────────────────────────────────────────────────
# 저변동 트렌드엣지 — 20일선 위 · 저변동성만 통과하는 명시적 필터
# ──────────────────────────────────────────────────────────────


def test_vol_filtered_edge_rejects_a_price_below_the_20_day_average() -> None:
    below_ma = _candle(90, 91, 89, 90)  # 20일선(평균 109.5)보다 한참 아래
    score = _score_vol_filtered_edge(below_ma, _RISING_20, _VARIED_NIGHTS_20)
    assert score is None


def test_vol_filtered_edge_rejects_high_volatility_even_in_an_uptrend() -> None:
    clean_bull = _candle(119, 121, 118, 120.5)  # 20일선 위 · 양봉
    choppy_nights = [0.05, -0.05] * 10  # 표준편차 5% — 2% 상한을 훌쩍 넘김
    score = _score_vol_filtered_edge(clean_bull, _RISING_20, choppy_nights)
    assert score is None


def test_vol_filtered_edge_scores_a_low_volatility_uptrend_candidate() -> None:
    clean_bull = _candle(119, 121, 118, 120.5)
    score = _score_vol_filtered_edge(clean_bull, _RISING_20, _VARIED_NIGHTS_20)
    assert score is not None
    assert score > 0


def test_backtest_top_k_accepts_vol_filtered_edge_as_a_known_strategy() -> None:
    result = backtest_top_k({}, "vol_filtered_edge")
    assert result.strategy == "vol_filtered_edge"
    assert result.points == []


# ──────────────────────────────────────────────────────────────
# 필터드 거래량폭발 — 추세·저변동 필터를 얹은 volume_surge
# ──────────────────────────────────────────────────────────────


def test_vol_surge_trend_rejects_a_price_below_the_20_day_average() -> None:
    below_ma = _candle(90, 91, 89, 90, v=3_000_000)
    score = _score_vol_surge_trend(below_ma, _RISING_20, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score is None


def test_vol_surge_trend_rejects_high_volatility_even_in_an_uptrend() -> None:
    high_vol_bull = _candle(119, 121, 118, 120.5, v=3_000_000)
    choppy_nights = [0.05, -0.05] * 10  # 표준편차 5% — 2% 상한을 훌쩍 넘김
    score = _score_vol_surge_trend(high_vol_bull, _RISING_20, [1_000_000] * 20, choppy_nights)
    assert score is None


def test_vol_surge_trend_scores_a_low_volatility_uptrend_candidate() -> None:
    high_vol_bull = _candle(119, 121, 118, 120.5, v=3_000_000)
    score = _score_vol_surge_trend(high_vol_bull, _RISING_20, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score == pytest.approx(3.0)  # volume_surge 점수(3.0) 그대로 통과


def test_backtest_top_k_accepts_vol_surge_trend_as_a_known_strategy() -> None:
    result = backtest_top_k({}, "vol_surge_trend")
    assert result.strategy == "vol_surge_trend"
    assert result.points == []


# ──────────────────────────────────────────────────────────────
# 거래량폭발×야간샤프 — 필터 없이 두 점수를 곱셈으로만 결합
# ──────────────────────────────────────────────────────────────


def test_vol_surge_edge_rejects_a_bearish_candle() -> None:
    bearish = _candle(120, 121, 118, 119, v=3_000_000)  # close < open
    score = _score_vol_surge_edge(bearish, [1_000_000] * 20, _VARIED_NIGHTS_20)
    assert score is None


def test_vol_surge_edge_rejects_when_night_edge_is_non_positive() -> None:
    bullish = _candle(100, 105, 99, 104, v=3_000_000)
    flat_nights = [0.01] * 20  # 변동이 없어 rolling_edge 가 None
    score = _score_vol_surge_edge(bullish, [1_000_000] * 20, flat_nights)
    assert score is None


def test_vol_surge_edge_multiplies_volume_surge_and_rolling_edge() -> None:
    bullish = _candle(100, 105, 99, 104, v=3_000_000)
    score = _score_vol_surge_edge(bullish, [1_000_000] * 20, _VARIED_NIGHTS_20)
    surge = _score_volume_surge(bullish, [1_000_000] * 20)
    edge = _score_rolling_edge(_VARIED_NIGHTS_20)
    assert score == pytest.approx(surge * edge)


def test_backtest_top_k_accepts_vol_surge_edge_as_a_known_strategy() -> None:
    result = backtest_top_k({}, "vol_surge_edge")
    assert result.strategy == "vol_surge_edge"
    assert result.points == []


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


def test_summary_carries_mdd_sharpe_calmar() -> None:
    """Week 7: 수익률뿐 아니라 위험조정 지표도 함께 돌려줍니다."""
    result = backtest_top_k(UNIVERSE_2DAY, "mean_reversion", k=1, cost=0.0)
    assert result.summary.mdd <= 0.0
    assert result.summary.calmar == pytest.approx(
        result.summary.final_portfolio / abs(result.summary.mdd)
        if result.summary.mdd
        else 0.0
    )


# ──────────────────────────────────────────────────────────────
# best_k_result — 전략마다 샤프지수가 가장 좋은 K 를 직접 찾아줍니다
# ──────────────────────────────────────────────────────────────

# A: 매일 모멘텀 점수가 B보다 높아 k=1일 땐 항상 A 혼자만 담깁니다 — 밤수익률이
# 완만하고 꾸준해(1.0%·1.5%·1.2%) 변동성이 작습니다.
# B: 모멘텀 점수는 항상 A보다 낮지만, 밤수익률이 크게 출렁입니다(5%·-4%·6%) —
# k=2(둘 다 담김)가 되면 그 변동성이 그대로 포트폴리오에 섞여 들어갑니다.
_BEST_K_UNIVERSE = {
    "A": [
        Candle(time=D0, open=100, high=110, low=99, close=109, volume=1),
        Candle(time=D1, open=110.09, high=112, low=109, close=111, volume=1),
        Candle(time=D2, open=112.665, high=115, low=112, close=114, volume=1),
        Candle(time=D3, open=115.368, high=117, low=115, close=116, volume=1),
    ],
    "B": [
        Candle(time=D0, open=100, high=108, low=99, close=100.5, volume=1),
        Candle(time=D1, open=105.525, high=115, low=100, close=106, volume=1),
        Candle(time=D2, open=101.76, high=112, low=98, close=102, volume=1),
        Candle(time=D3, open=108.12, high=120, low=100, close=109, volume=1),
    ],
}


def test_best_k_result_picks_the_k_with_the_highest_sharpe() -> None:
    k_candidates = (1, 2)
    best_k, best_result = best_k_result(_BEST_K_UNIVERSE, "momentum", cost=0.0, k_candidates=k_candidates)

    manual = {k: backtest_top_k(_BEST_K_UNIVERSE, "momentum", k=k, cost=0.0) for k in k_candidates}
    expected_k = max(manual, key=lambda kk: manual[kk].summary.sharpe)

    assert best_k == expected_k == 1  # 변동성이 작은 A 혼자인 k=1 이 더 좋은 샤프를 냅니다
    assert best_result.summary.sharpe == pytest.approx(manual[expected_k].summary.sharpe)
    assert best_result.picks == manual[expected_k].picks


def test_universe_metrics_are_the_same_across_strategies_for_the_same_period() -> None:
    """유니버스(벤치마크) 지표는 포트폴리오 전략 선택과 무관합니다 — 화면 리더보드가
    아무 전략 응답에서나 벤치마크 행을 그대로 가져다 써도 되는 이유입니다.
    """
    a = backtest_top_k(UNIVERSE_2DAY, "momentum", k=1, cost=0.0)
    b = backtest_top_k(UNIVERSE_2DAY, "mean_reversion", k=1, cost=0.0)
    assert a.summary.universe_mdd == pytest.approx(b.summary.universe_mdd)
    assert a.summary.universe_sharpe == pytest.approx(b.summary.universe_sharpe)
    assert a.summary.universe_calmar == pytest.approx(b.summary.universe_calmar)
