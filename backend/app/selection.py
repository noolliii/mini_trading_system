"""매일 유니버스에서 점수 상위 K종목을 골라 종가 배팅하는 백테스트.

점수는 항상 "그날 15:20까지 알 수 있는 정보"만 씁니다 — 모멘텀 · 역추세 ·
거래량폭발은 그날 자신의 O·H·L·C·V(장이 거의 끝난 시점의 값)를, 롤링엣지는
그 전까지 실현된 과거 밤수익률만 씁니다. 매수는 그날 종가, 매도는 다음
거래일 시가이므로, 점수를 매긴 날의 종가보다 미래의 값은 들여다보지 않습니다.

4대 점수식 (Week 6 슬라이드 기준):
  momentum       Score = (C - L) / (H - L)                   고가 근처에서 마감한 종목
  mean_reversion Score = -(C / O - 1)                         장중 낙폭이 컸던 종목
  volume_surge   Score = (V / MA20_V) · I(양봉)                거래량이 평소보다 터진 양봉
  rolling_edge   Score = mean(R_night, 20일) / std(R_night, 20일)  야간 샤프지수
"""

from dataclasses import dataclass
from datetime import datetime

from .schemas import Candle

STRATEGIES: tuple[str, ...] = ("momentum", "mean_reversion", "volume_surge", "rolling_edge")

_VOLUME_WINDOW = 20
_EDGE_WINDOW = 20


def _score_momentum(c: Candle) -> float:
    span = c.high - c.low
    if span <= 0:
        return 0.0
    return (c.close - c.low) / span


def _score_mean_reversion(c: Candle) -> float:
    return -(c.close / c.open - 1)


def _score_volume_surge(c: Candle, prior_volumes: list[float]) -> float | None:
    if len(prior_volumes) < _VOLUME_WINDOW:
        return None
    ma20 = sum(prior_volumes[-_VOLUME_WINDOW:]) / _VOLUME_WINDOW
    if ma20 <= 0:
        return None
    return (c.volume / ma20) if c.close > c.open else 0.0


def _score_rolling_edge(prior_night_returns: list[float]) -> float | None:
    if len(prior_night_returns) < _EDGE_WINDOW:
        return None
    window = prior_night_returns[-_EDGE_WINDOW:]
    mean = sum(window) / len(window)
    variance = sum((x - mean) ** 2 for x in window) / len(window)
    std = variance ** 0.5
    # 부동소수점 오차로 "완전히 같은 값"도 std 가 0이 아닌 아주 작은 수로 나올 수 있어
    # 0과의 정확한 비교 대신 허용오차를 둡니다 — 실제 밤수익률 변동폭(보통 0.1%~수%)
    # 보다 한참 작은 값이라 과매매 신호로 오인할 일이 없습니다.
    if std < 1e-9:
        return None
    return mean / std


@dataclass(frozen=True)
class NightPick:
    time: datetime
    tickers: list[str]


@dataclass(frozen=True)
class Top5Point:
    time: datetime
    cum_portfolio: float
    cum_universe: float


@dataclass(frozen=True)
class Top5Summary:
    final_portfolio: float
    final_universe: float
    win_rate: float
    trading_nights: int


@dataclass(frozen=True)
class Top5Result:
    strategy: str
    points: list[Top5Point]
    summary: Top5Summary
    picks: list[NightPick]


_EMPTY_SUMMARY = Top5Summary(0.0, 0.0, 0.0, 0)


def backtest_top_k(
    universe: dict[str, list[Candle]],
    strategy: str,
    k: int = 5,
    cost: float = 0.0,
) -> Top5Result:
    if strategy not in STRATEGIES:
        raise ValueError(f"알 수 없는 전략입니다: {strategy}")

    by_ticker = {t: sorted(cs, key=lambda c: c.time) for t, cs in universe.items() if cs}
    index_of = {t: {c.time: i for i, c in enumerate(cs)} for t, cs in by_ticker.items()}
    all_dates = sorted({c.time for cs in by_ticker.values() for c in cs})

    if len(all_dates) < 2:
        return Top5Result(strategy=strategy, points=[], summary=_EMPTY_SUMMARY, picks=[])

    # 롤링엣지용 — 종목별로 실현된 밤수익률을 그 종목 자신의 날짜 순서로 누적해 둡니다
    night_returns: dict[str, list[float]] = {t: [] for t in by_ticker}

    picks: list[NightPick] = []
    portfolio_returns: list[float] = []
    universe_returns: list[float] = []

    for i, t in enumerate(all_dates[:-1]):
        t_next = all_dates[i + 1]
        candidates: list[tuple[str, float]] = []
        realized: dict[str, float] = {}

        for ticker, candles in by_ticker.items():
            idx = index_of[ticker].get(t)
            if idx is None:
                continue
            next_idx = index_of[ticker].get(t_next)
            if next_idx is None or next_idx != idx + 1:
                # 이 구간에 이 종목은 거래일이 비어 있어 오늘 밤 포지션을 못 잡습니다
                continue

            c, c_next = candles[idx], candles[next_idx]
            realized[ticker] = c_next.open / c.close - 1

            if strategy == "momentum":
                score: float | None = _score_momentum(c)
            elif strategy == "mean_reversion":
                score = _score_mean_reversion(c)
            elif strategy == "volume_surge":
                score = _score_volume_surge(c, [x.volume for x in candles[:idx]])
            else:
                score = _score_rolling_edge(night_returns[ticker])

            if score is not None:
                candidates.append((ticker, score))

        # 롤링엣지 창은 오늘 점수를 다 매긴 "뒤"에 갱신합니다 —
        # 그래야 오늘 점수가 오늘 밤수익률을 미리 들여다보지 않습니다
        for ticker, r in realized.items():
            night_returns[ticker].append(r)

        universe_returns.append(sum(realized.values()) / len(realized) if realized else 0.0)

        candidates.sort(key=lambda x: x[1], reverse=True)
        picked = [tk for tk, _ in candidates[:k]]
        picks.append(NightPick(time=t_next, tickers=picked))
        portfolio_returns.append(
            (sum(realized[tk] for tk in picked) / len(picked) - cost) if picked else 0.0
        )

    points: list[Top5Point] = []
    cum_portfolio = 1.0
    cum_universe = 1.0
    wins = 0
    trading_nights = 0

    for i, t in enumerate(all_dates[1:]):
        cum_portfolio *= 1 + portfolio_returns[i]
        cum_universe *= 1 + universe_returns[i]
        if picks[i].tickers:
            trading_nights += 1
            if portfolio_returns[i] > 0:
                wins += 1
        points.append(
            Top5Point(
                time=t,
                cum_portfolio=(cum_portfolio - 1) * 100,
                cum_universe=(cum_universe - 1) * 100,
            )
        )

    summary = Top5Summary(
        final_portfolio=points[-1].cum_portfolio,
        final_universe=points[-1].cum_universe,
        win_rate=(wins / trading_nights) if trading_nights else 0.0,
        trading_nights=trading_nights,
    )

    return Top5Result(strategy=strategy, points=points, summary=summary, picks=picks)
