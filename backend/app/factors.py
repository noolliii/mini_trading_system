"""OHLCV 옆에 붙이는 파생 팩터 컬럼. DB 를 보지 않는 순수 함수만 둡니다.

selection.py 의 4대 점수식과 달리, 여기 함수들은 그 자체가 "매매 점수"가
아니라 점수를 만드는 재료입니다 — 캔들 모양 · 수급 · 추세 · 야간관성 네 갈래로
나눠서, selection.py 의 factor_combo 전략이 이들을 조합해 씁니다(슬라이드 10번).
"""

from .schemas import Candle

_WINDOW = 20  # 이동평균 · 야간승률 모두 20거래일 창을 씁니다


def close_pos(c: Candle) -> float:
    """종가가 당일 (저가~고가) 구간에서 어디쯤 끝났는가. 1에 가까울수록 고가 근처 마감."""
    span = c.high - c.low
    if span <= 0:
        return 0.5
    return (c.close - c.low) / span


def upper_shadow_ratio(c: Candle) -> float:
    """윗꼬리 길이가 캔들 전체에서 차지하는 비율. 0이면 윗꼬리가 전혀 없는 꽉 찬 캔들."""
    span = c.high - c.low
    if span <= 0:
        return 0.0
    return (c.high - c.close) / span


def turnover(c: Candle) -> float:
    """당일 거래대금 (종가 × 거래량)."""
    return c.close * c.volume


def volume_ratio(c: Candle, prior_volumes: list[float]) -> float | None:
    """당일 거래량이 직전 20일 평균 거래량의 몇 배인가. 20일 역사가 없으면 None."""
    if len(prior_volumes) < _WINDOW:
        return None
    ma = sum(prior_volumes[-_WINDOW:]) / _WINDOW
    if ma <= 0:
        return None
    return c.volume / ma


def ma_gap(c: Candle, prior_closes: list[float]) -> float | None:
    """종가가 직전 20일 이동평균보다 몇 % 위/아래에 있는가(이격도). 20일 역사가 없으면 None."""
    if len(prior_closes) < _WINDOW:
        return None
    ma = sum(prior_closes[-_WINDOW:]) / _WINDOW
    if ma <= 0:
        return None
    return c.close / ma - 1


def return_5d(c: Candle, prior_closes: list[float]) -> float | None:
    """최근 5거래일 누적 수익률. 5일 역사가 없으면 None."""
    if len(prior_closes) < 5:
        return None
    base = prior_closes[-5]
    if base <= 0:
        return None
    return c.close / base - 1


def night_win_rate(prior_night_returns: list[float]) -> float | None:
    """최근 20일 밤(종가 배팅) 수익률 중 상승한 비율. 20일 역사가 없으면 None."""
    if len(prior_night_returns) < _WINDOW:
        return None
    window = prior_night_returns[-_WINDOW:]
    return sum(1 for r in window if r > 0) / len(window)


def night_return_std(prior_night_returns: list[float]) -> float | None:
    """최근 20일 밤(종가 배팅) 수익률의 표준편차 — 변동성(위험) 지표. 20일 역사가 없으면 None."""
    if len(prior_night_returns) < _WINDOW:
        return None
    window = prior_night_returns[-_WINDOW:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    return variance ** 0.5
