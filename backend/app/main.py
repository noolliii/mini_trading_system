import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import asyncpg
from fastapi import Cookie, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from . import auth, db, kis
from .auth import CurrentUserDep
from .schemas import (
    AccountIn,
    AccountOut,
    Device,
    IngestResult,
    LatestReading,
    LoginIn,
    LoginOut,
    Me,
    Point,
    ReadingIn,
    Series,
    SeriesResponse,
    UserIn,
    UserOut,
)

UNITS = {"temperature": "°C", "humidity": "%", "co2": "ppm"}

# 구간이 길어지면 원본 대신 1분 연속 집계를 읽습니다
RAW_WINDOW_HOURS = 6


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    try:
        yield
    finally:
        await db.disconnect()


app = FastAPI(title="IoT Sensor API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()],
    # 세션 쿠키를 주고받으려면 켜야 합니다. 이걸 켜면 allow_origins 에 * 를 쓸 수 없습니다
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """로그인 없이 열어 둡니다. 컨테이너가 살아 있는지 보는 용도입니다."""
    async with db.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════
# 로그인
# ══════════════════════════════════════════════════════════════


@app.post("/api/login", response_model=LoginOut)
async def login(body: LoginIn, response: Response) -> LoginOut:
    # 해시 비교는 여기서만 일어납니다. 브라우저도 DB 도 하지 않습니다
    user = await auth.authenticate(body.username, body.password)
    session_id, expires_at = await auth.create_session(user.id)
    auth.set_session_cookie(response, session_id, expires_at)
    return LoginOut(username=user.username, role=user.role, expires_at=expires_at)


@app.post("/api/logout", status_code=204)
async def logout(
    response: Response,
    session_id: Annotated[str | None, Cookie()] = None,
) -> None:
    """이미 만료된 세션이어도 조용히 성공시킵니다. 로그아웃이 실패할 이유는 없습니다."""
    if session_id:
        await auth.destroy_session(session_id)
    auth.clear_session_cookie(response)


@app.get("/api/me", response_model=Me)
async def me(user: CurrentUserDep) -> Me:
    return Me(
        id=user.id,
        username=user.username,
        role=user.role,
        permissions=sorted(auth.PERMISSIONS.get(user.role, set())),
    )


# ══════════════════════════════════════════════════════════════
# 사용자 — admin 만
# ══════════════════════════════════════════════════════════════


@app.get("/api/users", response_model=list[UserOut])
async def list_users(user: CurrentUserDep) -> list[UserOut]:
    auth.require(user, "user:read")
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.username, u.role, u.created_at,
                   count(a.account_no)::int AS account_count
            FROM users u
            LEFT JOIN accounts a ON a.user_id = u.id
            GROUP BY u.id
            ORDER BY u.id
            """
        )
    return [UserOut(**dict(r)) for r in rows]


@app.post("/api/users", response_model=UserOut, status_code=201)
async def create_user(body: UserIn, user: CurrentUserDep) -> UserOut:
    auth.require(user, "user:write")

    # 받은 즉시 해시합니다. 평문은 이 줄 밖으로 나가지 않습니다
    password_hash = auth.hash_password(body.password)

    async with db.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES ($1, $2, $3)
                RETURNING id, username, role, created_at
                """,
                body.username,
                password_hash,
                body.role,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail={
                "code": "username_taken",
                "message": "이미 있는 아이디입니다",
            })

    return UserOut(**dict(row), account_count=0)


@app.delete("/api/users/{user_id}", status_code=204)
async def delete_user(user_id: int, user: CurrentUserDep) -> None:
    auth.require(user, "user:write")

    if user_id == user.id:
        raise HTTPException(status_code=409, detail={
            "code": "cannot_delete_self",
            "message": "자기 자신은 지울 수 없습니다",
        })

    # accounts 와 sessions 가 ON DELETE CASCADE 라 이 한 줄로 같이 지워집니다
    async with db.acquire() as conn:
        result = await conn.execute("DELETE FROM users WHERE id = $1", user_id)

    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail={
            "code": "user_not_found",
            "message": "없는 사용자입니다",
        })


# ══════════════════════════════════════════════════════════════
# 계좌 — admin 은 전부, user 는 자기 것만
# ══════════════════════════════════════════════════════════════


@app.get("/api/accounts", response_model=list[AccountOut])
async def list_accounts(user: CurrentUserDep) -> list[AccountOut]:
    everyones = auth.allows(user.role, "account:read:any")
    if not everyones:
        auth.require(user, "account:read:own")

    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.account_no, a.user_id, u.username, a.broker, a.mode, a.created_at
            FROM accounts a
            JOIN users u ON u.id = a.user_id
            WHERE $1::boolean OR a.user_id = $2
            ORDER BY u.username, a.account_no
            """,
            everyones,
            user.id,
        )
    return [AccountOut(**dict(r)) for r in rows]


@app.post("/api/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountIn, user: CurrentUserDep) -> AccountOut:
    owner_id = body.user_id if body.user_id is not None else user.id

    # 남의 계정에 다는 것은 admin 만. 화면에서 숨기는 것과 별개로 여기서 다시 봅니다
    if owner_id != user.id:
        auth.require(user, "account:write:any")
    else:
        if not (
            auth.allows(user.role, "account:write:own")
            or auth.allows(user.role, "account:write:any")
        ):
            raise auth.forbidden()

    async with db.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO accounts (account_no, user_id, broker, mode)
                VALUES ($1, $2, $3, $4)
                RETURNING account_no, user_id, broker, mode, created_at
                """,
                body.account_no,
                owner_id,
                body.broker,
                body.mode,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail={
                "code": "account_exists",
                "message": "이미 등록된 계좌번호입니다",
            })
        except asyncpg.ForeignKeyViolationError:
            raise HTTPException(status_code=404, detail={
                "code": "user_not_found",
                "message": "없는 사용자입니다",
            })

        username = await conn.fetchval("SELECT username FROM users WHERE id = $1", owner_id)

    return AccountOut(**dict(row), username=username)


@app.delete("/api/accounts/{account_no}", status_code=204)
async def delete_account(account_no: str, user: CurrentUserDep) -> None:
    async with db.acquire() as conn:
        owner_id = await conn.fetchval(
            "SELECT user_id FROM accounts WHERE account_no = $1", account_no
        )

        if owner_id is None:
            raise HTTPException(status_code=404, detail={
                "code": "account_not_found",
                "message": "없는 계좌입니다",
            })

        if owner_id != user.id:
            auth.require(user, "account:write:any")
        elif not (
            auth.allows(user.role, "account:write:own")
            or auth.allows(user.role, "account:write:any")
        ):
            raise auth.forbidden()

        await conn.execute("DELETE FROM accounts WHERE account_no = $1", account_no)


# ══════════════════════════════════════════════════════════════
# KIS 토큰 — admin 만. 토큰 자체는 응답에 담기지 않습니다
# ══════════════════════════════════════════════════════════════


@app.post("/api/kis/token", response_model=kis.TokenIssue)
async def issue_kis_token(
    user: CurrentUserDep,
    mode: str = Query("paper", pattern=r"^(paper|live)$"),
) -> kis.TokenIssue:
    auth.require(user, "kis:token")
    try:
        return await kis.request_access_token(mode)
    except kis.KisError as e:
        raise HTTPException(status_code=502, detail={"code": "kis_error", "message": str(e)})


# ══════════════════════════════════════════════════════════════
# 센서 — 로그인한 사람만 읽습니다
# ══════════════════════════════════════════════════════════════


@app.get("/api/devices", response_model=list[Device])
async def list_devices(user: CurrentUserDep) -> list[Device]:
    auth.require(user, "sensor:read")
    async with db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT device_id, name, location FROM devices ORDER BY device_id"
        )
    return [Device(**dict(r)) for r in rows]


@app.get("/api/metrics", response_model=list[str])
async def list_metrics(user: CurrentUserDep) -> list[str]:
    auth.require(user, "sensor:read")
    async with db.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT metric FROM readings ORDER BY metric")
    return [r["metric"] for r in rows]


@app.post("/api/readings", response_model=IngestResult, status_code=201)
async def ingest(readings: list[ReadingIn]) -> IngestResult:
    """센서가 직접 넣는 경로라 세션을 요구하지 않습니다.

    장치 인증(키 헤더)은 다음 주제입니다. 지금은 교내 망 안에서만 열립니다.
    """
    if not readings:
        raise HTTPException(status_code=400, detail="readings must not be empty")

    now = datetime.now(timezone.utc)
    records = [
        (r.time or now, r.device_id, r.metric, r.value) for r in readings
    ]

    async with db.acquire() as conn:
        async with conn.transaction():
            # 등록되지 않은 장치는 즉시 만들어 둡니다 (FK 위반 방지)
            await conn.executemany(
                """
                INSERT INTO devices (device_id, name)
                VALUES ($1, $1)
                ON CONFLICT (device_id) DO NOTHING
                """,
                [(r.device_id,) for r in readings],
            )
            result = await conn.executemany(
                """
                INSERT INTO readings (time, device_id, metric, value)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (device_id, metric, time) DO UPDATE
                SET value = EXCLUDED.value
                """,
                records,
            )
    del result
    return IngestResult(inserted=len(records))


@app.get("/api/latest", response_model=list[LatestReading])
async def latest(user: CurrentUserDep, metric: str | None = None) -> list[LatestReading]:
    auth.require(user, "sensor:read")
    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (r.device_id, r.metric)
                   r.device_id, d.name, r.metric, r.value, r.time
            FROM readings r
            JOIN devices d USING (device_id)
            WHERE ($1::text IS NULL OR r.metric = $1)
              AND r.time > now() - INTERVAL '7 days'
            ORDER BY r.device_id, r.metric, r.time DESC
            """,
            metric,
        )
    return [LatestReading(**dict(r)) for r in rows]


@app.get("/api/series", response_model=SeriesResponse)
async def series(
    user: CurrentUserDep,
    metric: str = Query(..., min_length=1),
    hours: int = Query(24, ge=1, le=24 * 30),
) -> SeriesResponse:
    auth.require(user, "sensor:read")
    use_raw = hours <= RAW_WINDOW_HOURS

    if use_raw:
        sql = """
            SELECT r.device_id, d.name, r.time AS t, r.value AS v
            FROM readings r
            JOIN devices d USING (device_id)
            WHERE r.metric = $1 AND r.time > now() - make_interval(hours => $2)
            ORDER BY r.device_id, r.time
        """
    else:
        sql = """
            SELECT a.device_id, d.name, a.bucket AS t, a.avg_value AS v
            FROM readings_1m a
            JOIN devices d USING (device_id)
            WHERE a.metric = $1 AND a.bucket > now() - make_interval(hours => $2)
            ORDER BY a.device_id, a.bucket
        """

    async with db.acquire() as conn:
        rows = await conn.fetch(sql, metric, hours)

    grouped: dict[str, Series] = {}
    for r in rows:
        s = grouped.get(r["device_id"])
        if s is None:
            s = Series(device_id=r["device_id"], name=r["name"], points=[])
            grouped[r["device_id"]] = s
        s.points.append(Point(t=r["t"], v=r["v"]))

    return SeriesResponse(
        metric=metric,
        unit=UNITS.get(metric),
        resolution="raw" if use_raw else "1m",
        series=list(grouped.values()),
    )
