from datetime import datetime

from pydantic import BaseModel, Field


class Device(BaseModel):
    device_id: str
    name: str
    location: str | None = None


class ReadingIn(BaseModel):
    device_id: str = Field(min_length=1, max_length=64)
    metric: str = Field(min_length=1, max_length=64)
    value: float
    time: datetime | None = None  # 생략하면 서버 수신 시각을 씁니다


class IngestResult(BaseModel):
    inserted: int


class Point(BaseModel):
    t: datetime
    v: float


class Series(BaseModel):
    device_id: str
    name: str
    points: list[Point]


class SeriesResponse(BaseModel):
    metric: str
    unit: str | None = None
    resolution: str
    series: list[Series]


class LatestReading(BaseModel):
    device_id: str
    name: str
    metric: str
    value: float
    time: datetime


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


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    created_at: datetime
    account_count: int


class UserIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user", pattern=r"^(admin|user)$")


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
