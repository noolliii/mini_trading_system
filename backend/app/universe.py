"""코스피 100 유니버스의 동일가중 낮 · 밤 · 단순보유 비교. DB 를 보지 않는 순수 함수만 둡니다.

종목마다 상장일 · 수집 상태가 달라 거래일 집합이 어긋날 수 있어서, 그날 값을
가진 종목들끼리만 평균을 냅니다 — 한 종목이 비어 있다고 그날 전체를 버리지 않습니다.
단순보유(cum_buy_hold)도 개별 종목을 따로 들고만 있는 게 아니라, 매일 낮 · 밤
평균수익률로 동일비중을 다시 맞추는 포트폴리오로 정의합니다 — 종목별 상장일이
달라 "첫날 가격"이 제각각인 유니버스에서는 이게 유일하게 일관된 기준선입니다.
"""

from dataclasses import dataclass
from datetime import datetime

from . import backtest
from .schemas import Candle


@dataclass(frozen=True)
class TickerSummary:
    ticker: str
    name: str
    final_day: float
    final_night: float
    final_buy_hold: float
    win_rate_day: float
    win_rate_night: float
    candle_count: int


@dataclass(frozen=True)
class UniversePoint:
    time: datetime
    cum_day: float
    cum_night: float
    cum_buy_hold: float


@dataclass(frozen=True)
class UniverseResult:
    tickers: list[TickerSummary]
    points: list[UniversePoint]


def compute_universe(universe: dict[str, tuple[str, list[Candle]]]) -> UniverseResult:
    """universe: {ticker: (종목명, 시간순 정렬된 일봉)}"""
    summaries: list[TickerSummary] = []
    day_returns_by_date: dict[datetime, list[float]] = {}
    night_returns_by_date: dict[datetime, list[float]] = {}

    for ticker, (name, candles) in universe.items():
        result = backtest.compute_day_night(candles)
        summaries.append(
            TickerSummary(
                ticker=ticker,
                name=name,
                final_day=result.summary.final_day,
                final_night=result.summary.final_night,
                final_buy_hold=result.summary.final_buy_hold,
                win_rate_day=result.summary.win_rate_day,
                win_rate_night=result.summary.win_rate_night,
                candle_count=len(candles),
            )
        )

        prev_close: float | None = None
        for c in candles:
            day_returns_by_date.setdefault(c.time, []).append(c.close / c.open - 1)
            if prev_close is not None:
                night_returns_by_date.setdefault(c.time, []).append(c.open / prev_close - 1)
            prev_close = c.close

    dates = sorted(day_returns_by_date.keys())
    points: list[UniversePoint] = []
    cum_day = 1.0
    cum_night = 1.0
    cum_buy_hold = 1.0

    for d in dates:
        day_rs = day_returns_by_date.get(d, [])
        night_rs = night_returns_by_date.get(d, [])

        if night_rs:
            avg_night = sum(night_rs) / len(night_rs)
            cum_night *= 1 + avg_night
            cum_buy_hold *= 1 + avg_night
        if day_rs:
            avg_day = sum(day_rs) / len(day_rs)
            cum_day *= 1 + avg_day
            cum_buy_hold *= 1 + avg_day

        points.append(
            UniversePoint(
                time=d,
                cum_day=(cum_day - 1) * 100,
                cum_night=(cum_night - 1) * 100,
                cum_buy_hold=(cum_buy_hold - 1) * 100,
            )
        )

    summaries.sort(key=lambda s: s.final_night, reverse=True)
    return UniverseResult(tickers=summaries, points=points)
