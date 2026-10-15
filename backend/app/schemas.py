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


class Top5Pick(BaseModel):
    time: datetime
    tickers: list[str]


class Top5Result(BaseModel):
    strategy: str
    points: list[Top5Point]
    summary: Top5Summary
    picks: list[Top5Pick]


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
