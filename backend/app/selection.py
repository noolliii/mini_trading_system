"""매일 유니버스에서 점수 상위 K종목을 골라 종가 배팅하는 백테스트.

점수는 항상 "그날 15:20까지 알 수 있는 정보"만 씁니다 — 모멘텀 · 역추세 ·
거래량폭발은 그날 자신의 O·H·L·C·V(장이 거의 끝난 시점의 값)를, 롤링엣지와
팩터콤보는 그 전까지 실현된 과거 밤수익률만 씁니다. 매수는 그날 종가, 매도는
다음 거래일 시가이므로, 점수를 매긴 날의 종가보다 미래의 값은 들여다보지
않습니다.

8대 점수식 (Week 6·7 슬라이드 기준):
  momentum          Score = (C - L) / (H - L)                   고가 근처에서 마감한 종목
  mean_reversion    Score = -(C / O - 1)                         장중 낙폭이 컸던 종목
  volume_surge      Score = (V / MA20_V) · I(양봉)                거래량이 평소보다 터진 양봉
  rolling_edge      Score = mean(R_night, 20일) / std(R_night, 20일)  야간 샤프지수
  factor_combo      20일선 위 · 양봉 · 윗꼬리 30% 이하만 통과시키고,
                    통과한 종목은 (V/MA20_V) × 야간샤프 로 순위를 매깁니다
                    (Week 7 "나만의 전략" 예시 — 블랙박스가 아니라 명시적 필터+점수)
  vol_filtered_edge 20일선 위 · 20일 밤수익률 표준편차 2% 이하만 통과시키고,
                    통과한 종목은 야간샤프(rolling_edge) 로 순위를 매깁니다 —
                    변동성이 큰 종목을 미리 걸러 MDD 를 낮추고, 위험 대비 수익으로
                    순위를 매겨 샤프를 올리는 전략입니다
  vol_surge_trend   20일선 위 · 20일 밤수익률 표준편차 2% 이하만 통과시키고,
                    통과한 종목은 거래량폭발(volume_surge) 점수로 순위를 매깁니다 —
                    거래량폭발에 추세·저변동 필터를 얹은 버전입니다
  vol_surge_edge    Score = (V/MA20_V)·I(양봉) × 야간샤프 — 필터 없이 거래량폭발과
                    야간샤프 두 신호를 곱셈으로만 결합합니다 (factor_combo 와 달리
                    추세·윗꼬리 필터가 없어, 두 신호 자체의 결합력만 봅니다)
"""

from dataclasses import dataclass
from datetime import datetime

from . import factors, metrics
from .schemas import Candle

STRATEGIES: tuple[str, ...] = (
    "momentum",
    "mean_reversion",
    "volume_surge",
    "rolling_edge",
    "factor_combo",
    "vol_filtered_edge",
    "vol_surge_trend",
    "vol_surge_edge",
)

# 리더보드가 전략마다 "가장 유리한 K" 를 찾을 때 시험해 보는 후보 — 화면의
# K 입력 범위(1~20)와 맞춥니다. 전략마다 최적 K 가 다르므로 하나로 고정하지 않습니다.
K_CANDIDATES: tuple[int, ...] = tuple(range(1, 21))

_VOLUME_WINDOW = 20
_EDGE_WINDOW = 20
# 20일 밤수익률 표준편차 상한 — 실데이터 기준 종목별 전체기간 표준편차의 중앙값(약 1.56%)과
# 75분위(약 1.77%) 보다 넉넉히 위인 2%로 잡아, 일시적으로 변동성이 튄 구간만 걸러냅니다.
_VOL_CAP = 0.02


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


def _score_factor_combo(
    c: Candle,
    prior_closes: list[float],
    prior_volumes: list[float],
    prior_night_returns: list[float],
) -> float | None:
    """20일선 위 · 양봉 · 꽉 찬 캔들만 후보로 남기고, 거래량배수 × 야간샤프로 순위를 매깁니다.

    조건을 하나라도 못 채우면 그냥 후보에서 빠집니다 — 숫자 하나로 뭉뚱그리지
    않고 "왜 빠졌는지"가 그대로 설명되는 명시적 필터입니다.
    """
    gap = factors.ma_gap(c, prior_closes)
    if gap is None or gap <= 0:
        return None  # 20일선 아래(역배열) 제외
    if c.close <= c.open:
        return None  # 음봉 제외
    if factors.upper_shadow_ratio(c) > 0.3:
        return None  # 윗꼬리가 캔들의 30% 넘는 매물 종목 제외

    vol = factors.volume_ratio(c, prior_volumes)
    edge = _score_rolling_edge(prior_night_returns)
    if vol is None or edge is None or edge <= 0:
        return None
    return vol * edge


def _score_vol_filtered_edge(
    c: Candle,
    prior_closes: list[float],
    prior_night_returns: list[float],
) -> float | None:
    """20일선 위(상승추세) · 20일 밤변동성이 크지 않은 종목만 후보로 남기고,
    통과한 종목은 야간샤프(rolling_edge)로 순위를 매깁니다.

    변동성이 큰 종목을 미리 걸러내는 것 자체가 포트폴리오 MDD 를 낮추고,
    위험 대비 수익(샤프류 지표)으로 순위를 매기는 것 자체가 샤프를 끌어올립니다.
    """
    gap = factors.ma_gap(c, prior_closes)
    if gap is None or gap <= 0:
        return None  # 20일선 아래(하락추세) 제외
    std = factors.night_return_std(prior_night_returns)
    if std is None or std > _VOL_CAP:
        return None  # 밤수익률 변동성이 너무 큰 종목 제외
    edge = _score_rolling_edge(prior_night_returns)
    if edge is None or edge <= 0:
        return None
    return edge


def _score_vol_surge_trend(
    c: Candle,
    prior_closes: list[float],
    prior_volumes: list[float],
    prior_night_returns: list[float],
) -> float | None:
    """20일선 위(상승추세) · 20일 밤변동성이 크지 않은 종목만 후보로 남기고,
    통과한 종목은 거래량폭발(volume_surge) 점수로 순위를 매깁니다.

    volume_surge 자체는 추세·변동성을 보지 않아 급등 직후 꺾이는 종목도 그대로
    담는데, 그 중 하락추세이거나 변동성이 큰 것만 미리 걸러내는 버전입니다.
    """
    gap = factors.ma_gap(c, prior_closes)
    if gap is None or gap <= 0:
        return None  # 20일선 아래(하락추세) 제외
    std = factors.night_return_std(prior_night_returns)
    if std is None or std > _VOL_CAP:
        return None  # 밤수익률 변동성이 너무 큰 종목 제외
    surge = _score_volume_surge(c, prior_volumes)
    if surge is None or surge <= 0:
        return None  # 20일 거래량 역사가 없거나 음봉이면 제외
    return surge


def _score_vol_surge_edge(
    c: Candle,
    prior_volumes: list[float],
    prior_night_returns: list[float],
) -> float | None:
    """거래량폭발(volume_surge) 점수와 야간샤프(rolling_edge) 점수를 곱해 순위를 매깁니다.

    factor_combo 와 달리 추세·윗꼬리 필터가 없습니다 — 두 신호를 곱하는 것만으로
    각각 혼자일 때보다 나아지는지를 보기 위한 비교군입니다.
    """
    surge = _score_volume_surge(c, prior_volumes)
    if surge is None or surge <= 0:
        return None
    edge = _score_rolling_edge(prior_night_returns)
    if edge is None or edge <= 0:
        return None
    return surge * edge


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
    mdd: float               # 포트폴리오 최대낙폭(%), 0 이하
    sharpe: float            # 포트폴리오 연율화 샤프지수
    calmar: float            # final_portfolio / |mdd|
    cagr: float              # 포트폴리오 연율화 복리수익률(%) — 기간이 달라도 바로 비교 가능
    # 유니버스(동일가중 전체 종목) 쪽 지표 — 전략과 무관하게 같은 기간이면 항상
    # 같은 값이라, 화면의 리더보드가 "KOSPI 100 동일가중" 벤치마크 행으로 그대로 씁니다
    universe_mdd: float
    universe_sharpe: float
    universe_calmar: float
    universe_cagr: float


@dataclass(frozen=True)
class Top5Result:
    strategy: str
    points: list[Top5Point]
    summary: Top5Summary
    picks: list[NightPick]


_EMPTY_SUMMARY = Top5Summary(0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _score_all_days(
    universe: dict[str, list[Candle]],
    strategy: str,
) -> tuple[list[datetime], list[list[tuple[str, float]]], list[dict[str, float]], list[float]]:
    """날짜 전환마다 (점수 내림차순 후보, 실현수익, 유니버스 평균수익)을 한 번만 계산합니다.

    이 계산은 K(몇 종목을 담을지)와 무관합니다 — K 는 정렬된 후보 리스트를
    몇 개까지 자를지만 정하므로, best_k_result 가 여러 K 를 시험할 때 이 무거운
    과거-점수 계산을 K 개수만큼 반복하지 않게 분리해 둡니다.
    """
    by_ticker = {t: sorted(cs, key=lambda c: c.time) for t, cs in universe.items() if cs}
    index_of = {t: {c.time: i for i, c in enumerate(cs)} for t, cs in by_ticker.items()}
    all_dates = sorted({c.time for cs in by_ticker.values() for c in cs})

    if len(all_dates) < 2:
        return [], [], [], []

    # 롤링엣지용 — 종목별로 실현된 밤수익률을 그 종목 자신의 날짜 순서로 누적해 둡니다
    night_returns: dict[str, list[float]] = {t: [] for t in by_ticker}

    sorted_candidates: list[list[tuple[str, float]]] = []
    realized_per_day: list[dict[str, float]] = []
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

            # 이격도·거래량배수는 전부 최근 20일 창만 봅니다 — candles[:idx] 전체를
            # 매번 복사하면 날짜가 지날수록 느려지는 O(일수²) 이 됩니다. 필요한
            # 창만 잘라 하루당 비용을 날짜 수와 무관하게 일정하게 유지합니다.
            window_start = max(0, idx - _VOLUME_WINDOW)
            recent = candles[window_start:idx]

            if strategy == "momentum":
                score: float | None = _score_momentum(c)
            elif strategy == "mean_reversion":
                score = _score_mean_reversion(c)
            elif strategy == "volume_surge":
                score = _score_volume_surge(c, [x.volume for x in recent])
            elif strategy == "rolling_edge":
                score = _score_rolling_edge(night_returns[ticker])
            elif strategy == "factor_combo":
                score = _score_factor_combo(
                    c,
                    [x.close for x in recent],
                    [x.volume for x in recent],
                    night_returns[ticker],
                )
            elif strategy == "vol_filtered_edge":
                score = _score_vol_filtered_edge(
                    c,
                    [x.close for x in recent],
                    night_returns[ticker],
                )
            elif strategy == "vol_surge_trend":
                score = _score_vol_surge_trend(
                    c,
                    [x.close for x in recent],
                    [x.volume for x in recent],
                    night_returns[ticker],
                )
            else:
                score = _score_vol_surge_edge(
                    c,
                    [x.volume for x in recent],
                    night_returns[ticker],
                )

            if score is not None:
                candidates.append((ticker, score))

        # 롤링엣지 창은 오늘 점수를 다 매긴 "뒤"에 갱신합니다 —
        # 그래야 오늘 점수가 오늘 밤수익률을 미리 들여다보지 않습니다
        for ticker, r in realized.items():
            night_returns[ticker].append(r)

        candidates.sort(key=lambda x: x[1], reverse=True)
        sorted_candidates.append(candidates)
        realized_per_day.append(realized)
        universe_returns.append(sum(realized.values()) / len(realized) if realized else 0.0)

    return all_dates, sorted_candidates, realized_per_day, universe_returns


def _aggregate_top_k(
    strategy: str,
    all_dates: list[datetime],
    sorted_candidates: list[list[tuple[str, float]]],
    realized_per_day: list[dict[str, float]],
    universe_returns: list[float],
    k: int,
    cost: float,
) -> Top5Result:
    """정렬해 둔 후보를 K 개까지만 잘라 포트폴리오 수익률 곡선과 지표를 만듭니다."""
    if not all_dates:
        return Top5Result(strategy=strategy, points=[], summary=_EMPTY_SUMMARY, picks=[])

    picks: list[NightPick] = []
    portfolio_returns: list[float] = []

    for i, t_next in enumerate(all_dates[1:]):
        realized = realized_per_day[i]
        picked = [tk for tk, _ in sorted_candidates[i][:k]]
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

    cum_portfolio_series = [p.cum_portfolio for p in points]
    mdd = metrics.max_drawdown(cum_portfolio_series)
    sharpe = metrics.sharpe_ratio(metrics.daily_returns_from_cumulative(cum_portfolio_series))

    cum_universe_series = [p.cum_universe for p in points]
    universe_mdd = metrics.max_drawdown(cum_universe_series)
    universe_sharpe = metrics.sharpe_ratio(metrics.daily_returns_from_cumulative(cum_universe_series))

    summary = Top5Summary(
        final_portfolio=points[-1].cum_portfolio,
        final_universe=points[-1].cum_universe,
        win_rate=(wins / trading_nights) if trading_nights else 0.0,
        trading_nights=trading_nights,
        mdd=mdd,
        sharpe=sharpe,
        calmar=metrics.calmar_ratio(points[-1].cum_portfolio, mdd),
        cagr=metrics.cagr(points[-1].cum_portfolio, len(points)),
        universe_mdd=universe_mdd,
        universe_sharpe=universe_sharpe,
        universe_calmar=metrics.calmar_ratio(points[-1].cum_universe, universe_mdd),
        universe_cagr=metrics.cagr(points[-1].cum_universe, len(points)),
    )

    return Top5Result(strategy=strategy, points=points, summary=summary, picks=picks)


def backtest_top_k(
    universe: dict[str, list[Candle]],
    strategy: str,
    k: int = 5,
    cost: float = 0.0,
) -> Top5Result:
    if strategy not in STRATEGIES:
        raise ValueError(f"알 수 없는 전략입니다: {strategy}")

    all_dates, sorted_candidates, realized_per_day, universe_returns = _score_all_days(universe, strategy)
    return _aggregate_top_k(strategy, all_dates, sorted_candidates, realized_per_day, universe_returns, k, cost)


def best_k_result(
    universe: dict[str, list[Candle]],
    strategy: str,
    cost: float = 0.0,
    k_candidates: tuple[int, ...] = K_CANDIDATES,
) -> tuple[int, Top5Result]:
    """K_CANDIDATES 를 전부 돌려보고 샤프지수가 가장 좋은 K 하나와 그 결과를 돌려줍니다.

    종목 수(K)를 하나로 고정하면 전략마다 유불리가 갈립니다 — 리더보드가
    전략끼리 공정하게 비교하려면, 각 전략에게 가장 유리한 K 를 찾아줘야 합니다.
    과거-점수 계산(_score_all_days)은 K 와 무관해 한 번만 하고, K 개수만큼
    반복하는 건 그걸 잘라 모으는 가벼운 집계(_aggregate_top_k)뿐입니다.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"알 수 없는 전략입니다: {strategy}")

    all_dates, sorted_candidates, realized_per_day, universe_returns = _score_all_days(universe, strategy)

    best_k = k_candidates[0]
    best_result = _aggregate_top_k(
        strategy, all_dates, sorted_candidates, realized_per_day, universe_returns, best_k, cost
    )
    for k in k_candidates[1:]:
        result = _aggregate_top_k(
            strategy, all_dates, sorted_candidates, realized_per_day, universe_returns, k, cost
        )
        if result.summary.sharpe > best_result.summary.sharpe:
            best_k, best_result = k, result
    return best_k, best_result
