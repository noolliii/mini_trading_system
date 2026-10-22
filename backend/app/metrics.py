"""위험조정 성과 지표 — 최대낙폭(MDD) · 샤프지수 · 칼마지수. DB 를 보지 않는 순수 함수만 둡니다.

수익률이 아무리 높아도 중간에 반토막 났다 올라온 거면 실전에서 버티기 어렵습니다
(슬라이드 9번) — 이 세 지표가 그 "흔들림"을 숫자로 잡습니다. 입력은 전부
backtest.py · universe.py · selection.py 가 이미 만드는 누적수익률(%) 포인트
시리즈이고, 이 모듈은 그 뒤에 한 번 더 거치는 순수 계산입니다.
"""

import math

_TRADING_DAYS_PER_YEAR = 252


def daily_returns_from_cumulative(cum_returns_pct: list[float]) -> list[float]:
    """누적수익률(%) 시리즈를 하루하루의 단순수익률(소수)로 되돌립니다. 0%에서 시작합니다."""
    returns: list[float] = []
    prev = 0.0
    for r in cum_returns_pct:
        returns.append((1 + r / 100) / (1 + prev / 100) - 1)
        prev = r
    return returns


def max_drawdown(cum_returns_pct: list[float]) -> float:
    """고점 대비 최대 하락폭(%). 항상 0 이하 값입니다.

    cum_returns_pct 는 0%에서 시작하는 누적수익률 시리즈입니다 — 단순 뺄셈이
    아니라 (1+r)/(1+peak)-1 로, 복리 기준 낙폭을 계산합니다.
    """
    peak = 0.0  # 시작점(0%)도 고점 후보입니다
    worst = 0.0
    for r in cum_returns_pct:
        peak = max(peak, r)
        drawdown = (1 + r / 100) / (1 + peak / 100) - 1
        worst = min(worst, drawdown)
    return worst * 100


def sharpe_ratio(daily_returns: list[float]) -> float:
    """일별 수익률(소수)의 연율화 샤프지수. 변동이 없으면 0입니다."""
    if len(daily_returns) < 2:
        return 0.0
    mean = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean) ** 2 for r in daily_returns) / len(daily_returns)
    std = math.sqrt(variance)
    if std < 1e-12:
        return 0.0
    return (mean / std) * math.sqrt(_TRADING_DAYS_PER_YEAR)


def calmar_ratio(total_return_pct: float, mdd_pct: float) -> float:
    """누적수익률(%) ÷ |최대낙폭(%)|. 거의 안 빠졌다면(0에 가까우면) 0으로 둡니다."""
    if abs(mdd_pct) < 1e-9:
        return 0.0
    return total_return_pct / abs(mdd_pct)


def cagr(total_return_pct: float, trading_days: int) -> float:
    """총 누적수익률(%)을 거래일수 기준 연율화한 복리수익률(%).

    기간이 1년보다 짧거나 길어도 "1년 동안 유지했다면" 으로 환산해 서로 다른
    기간의 백테스트를 바로 비교할 수 있게 합니다. 거래일이 없으면 0, 원금을
    다 잃었으면(-100% 이하) 연율화해도 의미가 없어 -100 으로 둡니다.
    """
    if trading_days <= 0:
        return 0.0
    growth = 1 + total_return_pct / 100
    if growth <= 0:
        return -100.0
    years = trading_days / _TRADING_DAYS_PER_YEAR
    return (growth ** (1 / years) - 1) * 100
