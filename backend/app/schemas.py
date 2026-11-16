from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator


class Stock(BaseModel):
    ticker: str
    name: str
    market: str


class StockIn(BaseModel):
    """종목을 하나씩 직접 더할 때 받습니다. kospi100.csv 를 다시 고치지 않아도 되는 길입니다."""

    ticker: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str = Field(min_length=1, max_length=64)
    market: str = Field(pattern=r"^(KOSPI|KOSDAQ)$")


class Candle(BaseModel):
    """일봉 한 줄. KIS 응답도 이 모양으로 바꿔서 들고 다닙니다."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


class CandleSeries(BaseModel):
    ticker: str
    name: str
    candles: list[Candle]


class TradingDay(BaseModel):
    date: date
    is_open: bool


class CalendarCollectResult(BaseModel):
    year: int
    fetched: int
    inserted: int


class CollectionStatus(BaseModel):
    """종목별 신선도. "오늘 것이 들어왔는가"를 한눈에 보려고 둡니다."""

    ticker: str
    name: str
    last_date: date | None
    checked_at: datetime
    status: str  # success | partial | failed
    message: str | None = None


class CollectResult(BaseModel):
    """수집 결과. 받은 건수와 실제로 새로 들어간 건수를 나눠서 돌려줍니다."""

    ticker: str
    mode: str
    fetched: int
    inserted: int
    first: datetime | None = None
    last: datetime | None = None


# ──────────────────────────────────────────────────────────────
# 백테스트 — 낮(장중) vs 밤(종가 배팅)
# ──────────────────────────────────────────────────────────────


class BacktestPoint(BaseModel):
    time: datetime
    cum_day: float
    cum_night: float
    cum_buy_hold: float


class BacktestSummary(BaseModel):
    final_day: float
    final_night: float
    final_buy_hold: float
    win_rate_day: float
    win_rate_night: float


class BacktestResult(BaseModel):
    ticker: str
    name: str
    points: list[BacktestPoint]
    summary: BacktestSummary


class UniverseImportResult(BaseModel):
    """kospi100.csv 를 stocks 테이블에 더한 결과. 이미 있는 종목은 건드리지 않습니다."""

    total: int
    inserted: int


class IndexMembersImportResult(BaseModel):
    """kospi100_history.csv 를 index_members 테이블에 반영한 결과.

    같은 종목 · 같은 편입일은 덮어쓰므로(ON CONFLICT), 다시 실행해도 줄이
    늘지 않습니다 — rows 는 파일의 구간 수, upserted 는 실제 반영된 구간 수입니다.
    """

    rows: int
    upserted: int
    new_stocks: int


class UniverseTickerSummary(BaseModel):
    ticker: str
    name: str
    final_day: float
    final_night: float
    final_buy_hold: float
    win_rate_day: float
    win_rate_night: float
    candle_count: int


class UniverseResult(BaseModel):
    tickers: list[UniverseTickerSummary]
    points: list[BacktestPoint]


class Top5Point(BaseModel):
    time: datetime
    cum_portfolio: float
    cum_universe: float


class Top5Summary(BaseModel):
    final_portfolio: float
    final_universe: float
    win_rate: float
    trading_nights: int
    mdd: float
    sharpe: float
    calmar: float
    cagr: float
    universe_mdd: float
    universe_sharpe: float
    universe_calmar: float
    universe_cagr: float


class Top5Pick(BaseModel):
    time: datetime
    tickers: list[str]


class Top5Result(BaseModel):
    strategy: str
    points: list[Top5Point]
    summary: Top5Summary
    picks: list[Top5Pick]


class Top5LeaderboardRow(BaseModel):
    strategy: str
    k: int  # 이 전략에서 샤프지수가 가장 좋았던 K — 전략마다 다를 수 있습니다
    summary: Top5Summary


class Top5Leaderboard(BaseModel):
    rows: list[Top5LeaderboardRow]


# ──────────────────────────────────────────────────────────────
# 머신러닝(ML) — Week 8: 전 구간 학습, 분할 없음
# ──────────────────────────────────────────────────────────────


class FeatureImportance(BaseModel):
    name: str
    importance: float


class MlResult(BaseModel):
    """머신러닝 모델 하나의 전 구간 학습 결과. 비ML 8대 전략과 같은 Top5Result 형식이라
    같은 화면·같은 잣대(샤프·MDD·칼마·CAGR)로 바로 비교할 수 있습니다.
    """

    model_key: str  # "random_forest" 등 — ml_selection.MODEL_REGISTRY 의 키
    model_label: str  # 화면에 보여줄 이름(예: "RandomForest")
    k: int  # 샤프지수가 가장 좋았던 K(1~20) — 8대 전략과 동일하게 탐색
    trained_at: datetime  # 학습을 실행한 시각(벽시계) — 아래 둘과 다른 개념
    trained_from: datetime  # 학습 데이터의 첫 날짜 — "무슨 기간으로 학습했는지"는 이걸 봅니다
    trained_to: datetime  # 학습 데이터의 마지막 날짜
    rows: int  # 학습에 쓰인 (종목, 날짜) 표본 수
    feature_importances: list[FeatureImportance]
    result: Top5Result


class MlLeaderboardRow(BaseModel):
    model_key: str
    model_label: str
    k: int
    trained_at: datetime
    trained_from: datetime
    trained_to: datetime
    rows: int  # 학습에 쓰인 (종목, 날짜) 표본 수
    feature_importances: list[FeatureImportance]
    summary: Top5Summary


class MlLeaderboard(BaseModel):
    """MODEL_REGISTRY 의 모델들을 같은 기간·비용으로 비교합니다. 아직 학습하지
    않은 모델은 행이 통째로 빠집니다 — 8대 전략 리더보드와 달리 머신러닝은
    "학습"이라는 사전 단계가 있어서, 안 돌려본 모델은 보여줄 결과가 없습니다.
    """

    rows: list[MlLeaderboardRow]


# ──────────────────────────────────────────────────────────────
# Week 10 — 뉴럴 네트워크: Train(IS) vs Val vs Out-of-Sample 3단 비교
# ──────────────────────────────────────────────────────────────


class PeriodSummary(BaseModel):
    period: str  # "is" | "val" | "oos"
    summary: Top5Summary
    points: list[Top5Point]


class NnArchResult(BaseModel):
    """신경망 아키텍처 하나의 Train/Val/OOS 결과. 5개(MLP·1D-CNN·LSTM·GRU·Transformer)
    전부를 한 화면에서 비교합니다 — model_label 로 구분합니다.
    """

    model_key: str
    model_label: str
    family: str  # "A"(당일 스냅샷 2D) | "B"(최근 20일 시퀀스 3D)
    param_count: int
    hyperparams: dict
    loss_curve: list[dict]
    is_: PeriodSummary = Field(alias="is")
    val: PeriodSummary
    oos: PeriodSummary

    model_config = {"populate_by_name": True}


class GenerationResult(BaseModel):
    """한 세대(룰베이스 최고 · 전통ML 최고 · 신경망 최고)의 Train/Val/OOS 결과."""

    generation: str  # "rule_based" | "traditional_ml" | "neural_net"
    model_key: str
    model_label: str
    k: int
    is_: PeriodSummary = Field(alias="is")
    val: PeriodSummary
    oos: PeriodSummary

    model_config = {"populate_by_name": True}


class ComparisonReport(BaseModel):
    """룰베이스 최고 vs 전통ML 최고 vs 신경망(5종) — 같은 Train(IS)/Val/OOS 구간·
    같은 Top-5·같은 비용으로 비교한 Week 10 결과입니다.
    """

    rule_based: GenerationResult
    traditional_ml: GenerationResult
    neural_net_best: GenerationResult
    neural_net_all: list[NnArchResult]


# ──────────────────────────────────────────────────────────────
# 로그인 · 권한
# ──────────────────────────────────────────────────────────────


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class LoginOut(BaseModel):
    username: str
    role: str
    expires_at: datetime


class Me(BaseModel):
    id: int
    username: str
    role: str
    # 화면에서 메뉴를 숨길 때 쓰라고 내려보냅니다. 판단 자체는 서버가 다시 합니다
    permissions: list[str]


class MeUpdate(BaseModel):
    """내 정보 변경. 무엇을 바꾸든 지금 비밀번호를 함께 받습니다.

    역할은 여기에 없습니다 — 자기 역할을 스스로 올리지 못하게 하려는 것입니다.
    """

    current_password: str = Field(min_length=1, max_length=256)
    username: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    new_password: str | None = Field(default=None, min_length=8, max_length=256)

    @model_validator(mode="after")
    def at_least_one_change(self) -> "MeUpdate":
        if self.username is None and self.new_password is None:
            raise ValueError("바꿀 항목이 없습니다")
        return self


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime
    account_count: int


class SignupIn(BaseModel):
    """회원가입. 역할을 받지 않습니다 — 역할은 서버가 항상 user 로 넣습니다."""

    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user", pattern=r"^(admin|user)$")


class UserUpdate(BaseModel):
    """admin 이 남의 계정을 고칩니다. 비울 수 있는 항목만 보내면 그것만 바뀝니다.

    기존 비밀번호를 읽어오는 길은 없습니다 — 새로 넣는 것만 됩니다.
    """

    username: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    role: str | None = Field(default=None, pattern=r"^(admin|user)$")
    new_password: str | None = Field(default=None, min_length=8, max_length=256)

    @model_validator(mode="after")
    def at_least_one_change(self) -> "UserUpdate":
        if self.username is None and self.role is None and self.new_password is None:
            raise ValueError("바꿀 항목이 없습니다")
        return self


class AccountOut(BaseModel):
    account_no: str
    user_id: int
    username: str
    broker: str
    mode: str
    created_at: datetime


class AccountIn(BaseModel):
    account_no: str = Field(min_length=1, max_length=64)
    broker: str = Field(min_length=1, max_length=64)
    mode: str = Field(default="paper", pattern=r"^(paper|live)$")
    # 비우면 자기 자신에게 답니다. 남의 계정에 다는 것은 admin 만 됩니다
    user_id: int | None = None
