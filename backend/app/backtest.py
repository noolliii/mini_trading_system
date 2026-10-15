"""낮(장중) vs 밤(종가 배팅) 수익률 백테스트. DB 를 보지 않는 순수 함수만 둡니다.

R_day(t)   = C(t) / O(t) - 1      당일 시가에 사서 당일 종가에 청산
R_night(t) = O(t) / C(t-1) - 1    전일 종가에 사서 당일 시가에 청산

(1 + R_day(t)) 와 (1 + R_night(t)) 를 날마다 그대로 곱하면, 첫날 시가에 사서
마지막 날 종가에 파는 단순보유 수익률과 정확히 같아집니다 — cum_buy_hold 가
그 항등식을 확인하는 기준선입니다.
"""

from dataclasses import dataclass
from datetime import datetime

from .schemas import Candle


@dataclass(frozen=True)
class DayNightPoint:
    time: datetime
    cum_day: float       # 낮(장중)만 투자했을 때 누적수익률(%)
    cum_night: float      # 밤(종가 배팅)만 투자했을 때 누적수익률(%)
    cum_buy_hold: float   # 첫날 시가에 사서 들고만 있었을 때 누적수익률(%)


@dataclass(frozen=True)
class DayNightSummary:
    final_day: float
    final_night: float
    final_buy_hold: float
    win_rate_day: float    # R_day > 0 인 날의 비율
    win_rate_night: float  # R_night > 0 인 날의 비율 (첫날은 전일 종가가 없어 제외)


@dataclass(frozen=True)
class DayNightResult:
    points: list[DayNightPoint]
    summary: DayNightSummary


_EMPTY_SUMMARY = DayNightSummary(0.0, 0.0, 0.0, 0.0, 0.0)


def compute_day_night(candles: list[Candle]) -> DayNightResult:
    """candles 는 시간 오름차순으로 정렬돼 있어야 합니다."""
    if not candles:
        return DayNightResult(points=[], summary=_EMPTY_SUMMARY)

    entry_price = candles[0].open
    cum_day = 1.0
    cum_night = 1.0
    day_wins = 0
    night_wins = 0
    night_count = 0
    prev_close: float | None = None
    points: list[DayNightPoint] = []

    for c in candles:
        r_day = c.close / c.open - 1
        cum_day *= 1 + r_day
        if r_day > 0:
            day_wins += 1

        if prev_close is not None:
            r_night = c.open / prev_close - 1
            cum_night *= 1 + r_night
            night_count += 1
            if r_night > 0:
                night_wins += 1

        points.append(
            DayNightPoint(
                time=c.time,
                cum_day=(cum_day - 1) * 100,
                cum_night=(cum_night - 1) * 100,
                cum_buy_hold=(c.close / entry_price - 1) * 100,
            )
        )
        prev_close = c.close

    summary = DayNightSummary(
        final_day=(cum_day - 1) * 100,
        final_night=(cum_night - 1) * 100,
        final_buy_hold=points[-1].cum_buy_hold,
        win_rate_day=day_wins / len(candles),
        win_rate_night=(night_wins / night_count) if night_count else 0.0,
    )
    return DayNightResult(points=points, summary=summary)
