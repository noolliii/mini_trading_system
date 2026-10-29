import csv
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import asyncpg
from fastapi import Cookie, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from . import access_log, auth, backtest, collection, db, kis, ml_selection, scheduler, selection, universe
from .auth import CurrentUserDep
from .schemas import (
    AccountIn,
    AccountOut,
    BacktestPoint,
    BacktestResult,
    BacktestSummary,
    CalendarCollectResult,
    Candle,
    CandleSeries,
    CollectionStatus,
    CollectResult,
    FeatureImportance,
    LoginIn,
    LoginOut,
    Me,
    MeUpdate,
    MlResult,
    SignupIn,
    Stock,
    StockIn,
    Top5Leaderboard,
    Top5LeaderboardRow,
    Top5Pick,
    Top5Point,
    Top5Result,
    Top5Summary,
    TradingDay,
    UniverseImportResult,
    UniverseResult,
    UniverseTickerSummary,
    UserIn,
    UserOut,
    UserUpdate,
)

KOSPI100_CSV = Path(__file__).parent / "data" / "kospi100.csv"

# uvicorn 은 자기 로거만 챙깁니다. 우리 모듈(app.kis 등)의 로그도 보이게 켭니다
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")

# 계좌번호가 URL 에 실려 접근 로그로 새는 것을 막습니다
access_log.install()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()
        await db.disconnect()


app = FastAPI(title="Mini Trading System API", version="0.3.0", lifespan=lifespan)

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


@app.post("/api/signup", response_model=LoginOut, status_code=201)
async def signup(body: SignupIn, response: Response) -> LoginOut:
    """누구나 부를 수 있습니다 — 로그인하지 않은 사람이 계정을 만드는 길입니다.

    역할은 항상 user 입니다. admin 이 되는 길은 이 엔드포인트에 없고,
    또 다른 admin 이 /api/users/{id} 로 역할을 바꿔줘야만 생깁니다.
    """
    # 받은 즉시 해시합니다. 평문은 이 줄 밖으로 나가지 않습니다
    password_hash = auth.hash_password(body.password)

    async with db.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES ($1, $2, 'user')
                RETURNING id, username, role
                """,
                body.username,
                password_hash,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail={
                "code": "username_taken",
                "message": "이미 있는 아이디입니다",
            })

    # 가입과 동시에 로그인시킵니다 — 다시 아이디·비밀번호를 치지 않게
    session_id, expires_at = await auth.create_session(row["id"])
    auth.set_session_cookie(response, session_id, expires_at)
    return LoginOut(username=row["username"], role=row["role"], expires_at=expires_at)


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


@app.patch("/api/me", response_model=Me)
async def update_me(
    body: MeUpdate,
    user: CurrentUserDep,
    session_id: Annotated[str | None, Cookie()] = None,
) -> Me:
    """내 정보만 바꿉니다. 대상은 항상 세션의 주인이고, 역할은 바꿀 수 없습니다."""
    auth.require(user, "user:update:own")

    # 쿠키를 훔쳐 쓰는 경우를 막습니다. 바꾸기 전에 지금 비밀번호를 한 번 더 받습니다
    if not await auth.verify_current_password(user.id, body.current_password):
        raise auth.auth_failed()

    username = body.username or user.username
    # 받은 즉시 해시합니다. 평문은 이 줄 밖으로 나가지 않습니다
    password_hash = auth.hash_password(body.new_password) if body.new_password else None

    async with db.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                UPDATE users
                   SET username = $2,
                       password_hash = coalesce($3, password_hash)
                 WHERE id = $1
                RETURNING username, role
                """,
                user.id,
                username,
                password_hash,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail={
                "code": "username_taken",
                "message": "이미 있는 아이디입니다",
            })

    if body.new_password:
        # 비밀번호가 바뀌었으니 다른 기기에 남아 있던 세션은 끊습니다
        await auth.destroy_other_sessions(user.id, session_id)

    return Me(
        id=user.id,
        username=row["username"],
        role=row["role"],
        permissions=sorted(auth.PERMISSIONS.get(row["role"], set())),
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


@app.patch("/api/users/{user_id}", response_model=UserOut)
async def update_user(user_id: int, body: UserUpdate, user: CurrentUserDep) -> UserOut:
    """admin 이 남의 아이디 · 역할 · 비밀번호를 고칩니다."""
    auth.require(user, "user:write")

    if body.role is not None and user_id == user.id:
        # 마지막 admin 이 스스로 user 로 내려가 잠기는 것을 막습니다
        raise HTTPException(status_code=409, detail={
            "code": "cannot_change_own_role",
            "message": "자기 역할은 바꿀 수 없습니다",
        })

    # 받은 즉시 해시합니다. 평문은 이 줄 밖으로 나가지 않습니다
    password_hash = auth.hash_password(body.new_password) if body.new_password else None

    async with db.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                UPDATE users
                   SET username = coalesce($2, username),
                       role = coalesce($3, role),
                       password_hash = coalesce($4, password_hash)
                 WHERE id = $1
                RETURNING id, username, role, created_at
                """,
                user_id,
                body.username,
                body.role,
                password_hash,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail={
                "code": "username_taken",
                "message": "이미 있는 아이디입니다",
            })

        if row is None:
            raise HTTPException(status_code=404, detail={
                "code": "user_not_found",
                "message": "없는 사용자입니다",
            })

        account_count = await conn.fetchval(
            "SELECT count(*)::int FROM accounts WHERE user_id = $1", user_id
        )

    if body.new_password or body.role is not None:
        # 비밀번호나 역할이 바뀌었으면 그 사람이 들고 있던 세션은 전부 끊습니다
        await auth.destroy_other_sessions(user_id, None)

    return UserOut(**dict(row), account_count=account_count)


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
    force: bool = Query(False, description="캐시를 무시하고 새로 발급합니다"),
) -> kis.TokenIssue:
    """만료 전이면 캐시한 토큰을 그대로 돌려줍니다 — 응답의 cached 로 구분합니다."""
    auth.require(user, "kis:token")
    try:
        return await kis.request_access_token(mode, force=force)
    except kis.KisError as e:
        raise HTTPException(status_code=502, detail={"code": "kis_error", "message": str(e)})


# ══════════════════════════════════════════════════════════════
# 종목 · 일봉
# ══════════════════════════════════════════════════════════════


@app.get("/api/stocks", response_model=list[Stock])
async def list_stocks(
    user: CurrentUserDep,
    q: str | None = Query(None, max_length=64, description="종목코드 또는 종목명의 일부"),
) -> list[Stock]:
    """종목 검색. 로그인한 사람은 누구나 읽습니다."""
    auth.require(user, "market:read")

    async with db.acquire() as conn:
        if q:
            rows = await conn.fetch(
                """
                SELECT ticker, name, market FROM stocks
                WHERE ticker LIKE $1 OR name ILIKE $1
                ORDER BY ticker
                """,
                f"%{q}%",
            )
        else:
            rows = await conn.fetch("SELECT ticker, name, market FROM stocks ORDER BY ticker")

    return [Stock(**dict(r)) for r in rows]


@app.post("/api/stocks", response_model=Stock, status_code=201)
async def create_stock(body: StockIn, user: CurrentUserDep) -> Stock:
    """종목을 하나씩 직접 더합니다. kospi100.csv 재가져오기 없이도 종목을 늘릴 수 있는 길입니다."""
    auth.require(user, "market:write")

    async with db.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO stocks (ticker, name, market)
                VALUES ($1, $2, $3)
                RETURNING ticker, name, market
                """,
                body.ticker,
                body.name,
                body.market,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(status_code=409, detail={
                "code": "ticker_exists",
                "message": "이미 있는 종목코드입니다",
            })

    return Stock(**dict(row))


@app.get("/api/candles", response_model=CandleSeries)
async def candles(
    user: CurrentUserDep,
    ticker: str = Query(min_length=6, max_length=6, pattern=r"^\d{6}$"),
    days: int = Query(365, ge=1, le=3650),
) -> CandleSeries:
    """저장해 둔 일봉을 읽습니다. KIS 로 나가지 않습니다 — DB 만 봅니다."""
    auth.require(user, "market:read")

    async with db.acquire() as conn:
        name = await conn.fetchval("SELECT name FROM stocks WHERE ticker = $1", ticker)
        if name is None:
            raise HTTPException(status_code=404, detail={
                "code": "stock_not_found",
                "message": "종목 마스터에 없는 종목코드입니다",
            })

        rows = await conn.fetch(
            """
            SELECT time, open, high, low, close, volume
            FROM daily_candles
            WHERE ticker = $1 AND time > now() - make_interval(days => $2)
            ORDER BY time
            """,
            ticker,
            days,
        )

    return CandleSeries(
        ticker=ticker,
        name=name,
        candles=[Candle(**dict(r)) for r in rows],
    )


@app.post("/api/stocks/{ticker}/collect", response_model=CollectResult)
async def collect_candles(
    ticker: str,
    user: CurrentUserDep,
    mode: str = Query("paper", pattern=r"^(paper|live)$"),
    days: int = Query(365, ge=1, le=3650),
) -> CollectResult:
    """KIS 에서 일봉을 받아 적재합니다. admin 만 — 외부로 나가는 호출이기 때문입니다.

    같은 종목·같은 날짜는 덮어씁니다. 여러 번 돌려도 줄이 늘지 않습니다.
    """
    auth.require(user, "market:write")

    async with db.acquire() as conn:
        exists = await conn.fetchval("SELECT 1 FROM stocks WHERE ticker = $1", ticker)
    if exists is None:
        raise HTTPException(status_code=404, detail={
            "code": "stock_not_found",
            "message": "종목 마스터에 없는 종목코드입니다",
        })

    end = datetime.now(kis.KST).date()
    start = end - timedelta(days=days)

    try:
        fetched = await kis.fetch_daily_candles(ticker, mode, start, end)
    except kis.KisError as e:
        raise HTTPException(status_code=502, detail={"code": "kis_error", "message": str(e)})

    if not fetched:
        return CollectResult(ticker=ticker, mode=mode, fetched=0, inserted=0)

    async with db.acquire() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM daily_candles WHERE ticker = $1", ticker
        )
        await conn.executemany(
            """
            INSERT INTO daily_candles (time, ticker, open, high, low, close, volume)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (ticker, time) DO UPDATE
               SET open = excluded.open, high = excluded.high, low = excluded.low,
                   close = excluded.close, volume = excluded.volume
            """,
            [
                (c.time, ticker, c.open, c.high, c.low, c.close, c.volume)
                for c in fetched
            ],
        )
        after = await conn.fetchval(
            "SELECT count(*) FROM daily_candles WHERE ticker = $1", ticker
        )

    return CollectResult(
        ticker=ticker,
        mode=mode,
        fetched=len(fetched),
        inserted=after - before,
        first=fetched[0].time,
        last=fetched[-1].time,
    )


@app.post("/api/stocks/{ticker}/collect-incremental", response_model=CollectResult)
async def collect_incremental(
    ticker: str,
    user: CurrentUserDep,
    mode: str = Query("paper", pattern=r"^(paper|live)$"),
) -> CollectResult:
    """마지막으로 쌓인 날짜 다음 거래일부터 오늘까지만 받습니다.

    새벽 스케줄러가 종목 전체에 돌리는 것과 같은 함수(collection.py)를 씁니다 —
    버튼으로 받을 때와 자동으로 받을 때가 다르게 동작하지 않습니다.
    """
    auth.require(user, "market:write")

    try:
        return await collection.collect_incremental_ticker(ticker, mode)
    except collection.TickerNotFound:
        raise HTTPException(status_code=404, detail={
            "code": "stock_not_found",
            "message": "종목 마스터에 없는 종목코드입니다",
        })
    except kis.KisError as e:
        raise HTTPException(status_code=502, detail={"code": "kis_error", "message": str(e)})


@app.get("/api/trading-days", response_model=list[TradingDay])
async def list_trading_days(
    user: CurrentUserDep,
    year: int = Query(..., ge=2000, le=2100),
) -> list[TradingDay]:
    """저장해 둔 캘린더를 읽습니다. KIS 로 나가지 않습니다."""
    auth.require(user, "market:read")

    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT date, is_open FROM trading_days
            WHERE date >= make_date($1, 1, 1) AND date <= make_date($1, 12, 31)
            ORDER BY date
            """,
            year,
        )

    return [TradingDay(**dict(r)) for r in rows]


@app.post("/api/trading-days/collect", response_model=CalendarCollectResult)
async def collect_trading_days(
    user: CurrentUserDep,
    year: int = Query(..., ge=2000, le=2100),
) -> CalendarCollectResult:
    """그 해의 거래일 캘린더를 KIS 실전 도메인에서 받아 저장합니다.

    주문은 내지 않습니다 — 계좌와 무관한 시장 전체의 공개 정보를 받아올 뿐입니다.
    모의(paper) 도메인은 이 TR 자체를 거절해서, kis.fetch_trading_calendar
    가 내부적으로 항상 실전 도메인을 씁니다.
    """
    auth.require(user, "market:write")

    start = date(year, 1, 1)
    end = date(year, 12, 31)

    try:
        fetched = await kis.fetch_trading_calendar(start, end)
    except kis.KisError as e:
        raise HTTPException(status_code=502, detail={"code": "kis_error", "message": str(e)})

    async with db.acquire() as conn:
        before = await conn.fetchval(
            "SELECT count(*) FROM trading_days WHERE date BETWEEN $1 AND $2", start, end
        )
        await conn.executemany(
            """
            INSERT INTO trading_days (date, is_open) VALUES ($1, $2)
            ON CONFLICT (date) DO UPDATE SET is_open = excluded.is_open
            """,
            [(d.date, d.is_open) for d in fetched],
        )
        after = await conn.fetchval(
            "SELECT count(*) FROM trading_days WHERE date BETWEEN $1 AND $2", start, end
        )

    return CalendarCollectResult(year=year, fetched=len(fetched), inserted=after - before)


# ══════════════════════════════════════════════════════════════
# 신선도 — 종목별로 오늘 것이 들어왔는지. 자동 수집은 스케줄러가 돕니다
# ══════════════════════════════════════════════════════════════


@app.get("/api/collection-status", response_model=list[CollectionStatus])
async def list_collection_status(user: CurrentUserDep) -> list[CollectionStatus]:
    """최근 수집이 종목별로 성공·실패했는지 봅니다. KIS 로 나가지 않습니다."""
    auth.require(user, "market:read")

    async with db.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT s.ticker, s.name, cs.last_date, cs.checked_at, cs.status, cs.message
            FROM stocks s
            LEFT JOIN collection_status cs USING (ticker)
            ORDER BY s.ticker
            """
        )

    return [
        CollectionStatus(
            ticker=r["ticker"],
            name=r["name"],
            last_date=r["last_date"],
            checked_at=r["checked_at"] or datetime.now(timezone.utc),
            status=r["status"] or "partial",
            message=r["message"] or ("아직 한 번도 수집하지 않았습니다" if r["status"] is None else None),
        )
        for r in rows
    ]


@app.post("/api/collection-status/run-now")
async def run_collection_now(user: CurrentUserDep, mode: str = Query("paper", pattern=r"^(paper|live)$")) -> dict[str, object]:
    """스케줄러가 평일 16:00(KST) 에 돌리는 것과 같은 일을, 기다리지 않고 지금 합니다.

    cron 시각을 당겨서 시험하는 대신 이 엔드포인트로 같은 함수를 바로 불러
    시험합니다 — 운영 스케줄은 그대로 두고 동작만 확인할 수 있습니다.
    """
    auth.require(user, "market:write")
    return await collection.run_incremental_for_all_stocks(mode)


# ══════════════════════════════════════════════════════════════
# 백테스트 — 낮(장중) vs 밤(종가 배팅)
# ══════════════════════════════════════════════════════════════


def _range_bounds(start: date | None, end: date | None) -> tuple[datetime | None, datetime | None]:
    """start/end(날짜)를 UTC 자정 timestamptz 로 바꿉니다. 비우면 None — SQL 의
    COALESCE 가 기존 days-기반 조회로 그대로 넘어갑니다."""
    start_ts = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) if start else None
    end_ts = datetime.combine(end, datetime.min.time(), tzinfo=timezone.utc) if end else None
    return start_ts, end_ts


def _validate_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail={
            "code": "invalid_range",
            "message": "시작일이 종료일보다 늦습니다",
        })


async def _fetch_candles(
    conn: asyncpg.Connection,
    ticker: str,
    days: int,
    start: date | None = None,
    end: date | None = None,
) -> list[Candle]:
    start_ts, end_ts = _range_bounds(start, end)
    rows = await conn.fetch(
        """
        SELECT time, open, high, low, close, volume
        FROM daily_candles
        WHERE ticker = $1
          AND time >= COALESCE($2::timestamptz, now() - make_interval(days => $3))
          AND time <= COALESCE($4::timestamptz, now())
        ORDER BY time
        """,
        ticker,
        start_ts,
        days,
        end_ts,
    )
    return [Candle(**dict(r)) for r in rows]


@app.get("/api/backtest/day-night", response_model=BacktestResult)
async def backtest_day_night(
    user: CurrentUserDep,
    ticker: str = Query(min_length=6, max_length=6, pattern=r"^\d{6}$"),
    days: int = Query(365, ge=2, le=3650),
    start: date | None = Query(None, description="시작일. 주면 days 대신 이 날짜부터"),
    end: date | None = Query(None, description="종료일. 비우면 지금까지"),
) -> BacktestResult:
    """낮(장중) · 밤(종가 배팅) · 단순보유 누적수익률 비교. DB 에 적재된 일봉만 봅니다."""
    auth.require(user, "market:read")
    _validate_range(start, end)

    async with db.acquire() as conn:
        name = await conn.fetchval("SELECT name FROM stocks WHERE ticker = $1", ticker)
        if name is None:
            raise HTTPException(status_code=404, detail={
                "code": "stock_not_found",
                "message": "종목 마스터에 없는 종목코드입니다",
            })
        candles = await _fetch_candles(conn, ticker, days, start, end)

    result = backtest.compute_day_night(candles)

    return BacktestResult(
        ticker=ticker,
        name=name,
        points=[BacktestPoint(**vars(p)) for p in result.points],
        summary=BacktestSummary(**vars(result.summary)),
    )


@app.post("/api/stocks/import-universe", response_model=UniverseImportResult)
async def import_universe(user: CurrentUserDep) -> UniverseImportResult:
    """번들에 들어 있는 코스피 100 종목 리스트를 stocks 테이블에 더합니다.

    DDL 이 아니라 데이터 삽입이라 db/init/ 을 건드리지 않고, 볼륨을 다시 만들
    필요도 없습니다. 이미 있는 종목(기존 8개 포함)은 이름 · 시장 구분을
    그대로 두고 건드리지 않습니다 — 새 티커만 더합니다. 실제 일봉은 여기서
    받지 않습니다 — 「수집 현황」탭의 "지금 전체 증분 수집 실행"이 새로 추가된
    종목까지 포함해 자동으로 받아옵니다.
    """
    auth.require(user, "market:write")

    with KOSPI100_CSV.open(encoding="utf-8") as f:
        rows = [(r["symbol"], r["name"]) for r in csv.DictReader(f)]

    async with db.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM stocks")
        await conn.executemany(
            """
            INSERT INTO stocks (ticker, name, market)
            VALUES ($1, $2, 'KOSPI')
            ON CONFLICT (ticker) DO NOTHING
            """,
            rows,
        )
        after = await conn.fetchval("SELECT count(*) FROM stocks")

    return UniverseImportResult(total=len(rows), inserted=after - before)


@app.get("/api/backtest/universe", response_model=UniverseResult)
async def backtest_universe(
    user: CurrentUserDep,
    days: int = Query(365, ge=2, le=3650),
    tickers: str | None = Query(None, description="콤마로 구분한 종목코드. 비우면 전체 종목"),
    start: date | None = Query(None, description="시작일. 주면 days 대신 이 날짜부터"),
    end: date | None = Query(None, description="종료일. 비우면 지금까지"),
) -> UniverseResult:
    """적재된 종목(코스피 100 포함)의 동일가중 낮 · 밤 · 단순보유 비교.

    tickers 를 주면 그 종목들만 골라서 평균을 냅니다 — 유니버스 화면의
    종목 선택 체크박스가 이걸 씁니다. 비우면 지금까지처럼 전체 종목입니다.
    """
    auth.require(user, "market:read")
    _validate_range(start, end)

    wanted = {t.strip() for t in tickers.split(",") if t.strip()} if tickers else None
    start_ts, end_ts = _range_bounds(start, end)

    async with db.acquire() as conn:
        stock_rows = await conn.fetch("SELECT ticker, name FROM stocks ORDER BY ticker")
        candle_rows = await conn.fetch(
            """
            SELECT ticker, time, open, high, low, close, volume
            FROM daily_candles
            WHERE time >= COALESCE($1::timestamptz, now() - make_interval(days => $2))
              AND time <= COALESCE($3::timestamptz, now())
            ORDER BY ticker, time
            """,
            start_ts,
            days,
            end_ts,
        )

    names = {
        r["ticker"]: r["name"] for r in stock_rows if wanted is None or r["ticker"] in wanted
    }
    by_ticker: dict[str, list[Candle]] = {}
    for r in candle_rows:
        if wanted is not None and r["ticker"] not in wanted:
            continue
        by_ticker.setdefault(r["ticker"], []).append(Candle(**dict(r)))

    result = universe.compute_universe(
        {ticker: (names.get(ticker, ticker), candles) for ticker, candles in by_ticker.items()}
    )

    return UniverseResult(
        tickers=[UniverseTickerSummary(**vars(s)) for s in result.tickers],
        points=[BacktestPoint(**vars(p)) for p in result.points],
    )


@app.get("/api/backtest/top5", response_model=Top5Result)
async def backtest_top5(
    user: CurrentUserDep,
    strategy: str = Query(
        pattern=r"^(momentum|mean_reversion|volume_surge|rolling_edge|factor_combo"
        r"|vol_filtered_edge|vol_surge_trend|vol_surge_edge)$"
    ),
    days: int = Query(365, ge=30, le=3650),
    k: int = Query(5, ge=1, le=20),
    cost: float = Query(0.0, ge=0, le=0.05, description="왕복 거래비용 비율. 예: 0.001 = 0.1%"),
    start: date | None = Query(None, description="시작일. 주면 days 대신 이 날짜부터"),
    end: date | None = Query(None, description="종료일. 비우면 지금까지"),
) -> Top5Result:
    """매일 유니버스에서 점수 상위 k종목을 골라 종가 배팅했을 때의 성과.

    점수는 그날 자신의 데이터(모멘텀 · 역추세 · 거래량폭발 · 팩터콤보) 또는
    그 전까지의 과거 밤수익률(롤링엣지 · 팩터콤보)만 씁니다 — 미래의 값을
    들여다보지 않습니다. 성과에는 누적수익률뿐 아니라 최대낙폭(MDD) ·
    샤프지수 · 칼마지수도 함께 돌려줘, 수익률만으로 전략을 고르지 않게 합니다.
    """
    auth.require(user, "market:read")
    _validate_range(start, end)
    start_ts, end_ts = _range_bounds(start, end)

    async with db.acquire() as conn:
        candle_rows = await conn.fetch(
            """
            SELECT ticker, time, open, high, low, close, volume
            FROM daily_candles
            WHERE time >= COALESCE($1::timestamptz, now() - make_interval(days => $2))
              AND time <= COALESCE($3::timestamptz, now())
            ORDER BY ticker, time
            """,
            start_ts,
            days,
            end_ts,
        )

    by_ticker: dict[str, list[Candle]] = {}
    for r in candle_rows:
        by_ticker.setdefault(r["ticker"], []).append(Candle(**dict(r)))

    try:
        result = selection.backtest_top_k(by_ticker, strategy, k=k, cost=cost)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_strategy", "message": str(e)})

    return Top5Result(
        strategy=result.strategy,
        points=[Top5Point(**vars(p)) for p in result.points],
        summary=Top5Summary(**vars(result.summary)),
        picks=[Top5Pick(**vars(p)) for p in result.picks],
    )


@app.get("/api/backtest/top5-leaderboard", response_model=Top5Leaderboard)
async def backtest_top5_leaderboard(
    user: CurrentUserDep,
    days: int = Query(365, ge=30, le=3650),
    cost: float = Query(0.0, ge=0, le=0.05, description="왕복 거래비용 비율. 예: 0.001 = 0.1%"),
    start: date | None = Query(None, description="시작일. 주면 days 대신 이 날짜부터"),
    end: date | None = Query(None, description="종료일. 비우면 지금까지"),
) -> Top5Leaderboard:
    """모든 전략을 비교하되, K(종목 수)를 하나로 고정하지 않습니다.

    전략마다 1~20 종목을 다 돌려보고 샤프지수가 가장 좋은 K 하나만 골라
    리더보드에 올립니다 — K=5 로 고정하면 어떤 전략은 불리해지는 것을 막습니다.
    """
    auth.require(user, "market:read")
    _validate_range(start, end)
    start_ts, end_ts = _range_bounds(start, end)

    async with db.acquire() as conn:
        candle_rows = await conn.fetch(
            """
            SELECT ticker, time, open, high, low, close, volume
            FROM daily_candles
            WHERE time >= COALESCE($1::timestamptz, now() - make_interval(days => $2))
              AND time <= COALESCE($3::timestamptz, now())
            ORDER BY ticker, time
            """,
            start_ts,
            days,
            end_ts,
        )

    by_ticker: dict[str, list[Candle]] = {}
    for r in candle_rows:
        by_ticker.setdefault(r["ticker"], []).append(Candle(**dict(r)))

    rows = []
    for strategy in selection.STRATEGIES:
        best_k, result = selection.best_k_result(by_ticker, strategy, cost=cost)
        rows.append(
            Top5LeaderboardRow(
                strategy=strategy,
                k=best_k,
                summary=Top5Summary(**vars(result.summary)),
            )
        )

    return Top5Leaderboard(rows=rows)


def _to_ml_result(ml_result: ml_selection.MlResult) -> MlResult:
    return MlResult(
        k=ml_result.k,
        trained_at=ml_result.trained_at,
        rows=ml_result.rows,
        feature_importances=[FeatureImportance(**vars(f)) for f in ml_result.feature_importances],
        result=Top5Result(
            strategy=ml_result.result.strategy,
            points=[Top5Point(**vars(p)) for p in ml_result.result.points],
            summary=Top5Summary(**vars(ml_result.result.summary)),
            picks=[Top5Pick(**vars(p)) for p in ml_result.result.picks],
        ),
    )


@app.post("/api/backtest/ml/train", response_model=MlResult)
async def backtest_ml_train(
    user: CurrentUserDep,
    days: int = Query(365, ge=30, le=3650),
    cost: float = Query(0.0, ge=0, le=0.05, description="왕복 거래비용 비율. 예: 0.001 = 0.1%"),
    start: date | None = Query(None, description="학습 시작일. 주면 days 대신 이 날짜부터"),
    end: date | None = Query(None, description="학습 종료일. 비우면 지금까지"),
) -> MlResult:
    """구간을 나누지 않고 받은 기간 전체로 랜덤포레스트를 학습해 모델을 저장합니다(admin 전용).

    선택은 8대 비머신러닝 전략과 똑같은 경로(selection.best_k_from_scores)를 타서
    비용차감·샤프·MDD·칼마·CAGR 계산 방식이 완전히 같습니다 — 비교가 공정합니다.
    """
    auth.require(user, "market:write")
    _validate_range(start, end)
    start_ts, end_ts = _range_bounds(start, end)

    async with db.acquire() as conn:
        candle_rows = await conn.fetch(
            """
            SELECT ticker, time, open, high, low, close, volume
            FROM daily_candles
            WHERE time >= COALESCE($1::timestamptz, now() - make_interval(days => $2))
              AND time <= COALESCE($3::timestamptz, now())
            ORDER BY ticker, time
            """,
            start_ts,
            days,
            end_ts,
        )

    by_ticker: dict[str, list[Candle]] = {}
    for r in candle_rows:
        by_ticker.setdefault(r["ticker"], []).append(Candle(**dict(r)))

    try:
        result = ml_selection.train_and_save(by_ticker, cost=cost)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"code": "insufficient_data", "message": str(e)})

    return _to_ml_result(result)


@app.get("/api/backtest/ml/latest", response_model=MlResult)
async def backtest_ml_latest(
    user: CurrentUserDep,
    days: int = Query(365, ge=30, le=3650),
    cost: float = Query(0.0, ge=0, le=0.05, description="왕복 거래비용 비율. 예: 0.001 = 0.1%"),
    start: date | None = Query(None, description="시작일. 주면 days 대신 이 날짜부터"),
    end: date | None = Query(None, description="종료일. 비우면 지금까지"),
) -> MlResult:
    """저장된 모델을 재학습 없이 불러와, 주어진 기간에 대해서만 다시 점수를 매깁니다."""
    auth.require(user, "market:read")
    _validate_range(start, end)
    start_ts, end_ts = _range_bounds(start, end)

    async with db.acquire() as conn:
        candle_rows = await conn.fetch(
            """
            SELECT ticker, time, open, high, low, close, volume
            FROM daily_candles
            WHERE time >= COALESCE($1::timestamptz, now() - make_interval(days => $2))
              AND time <= COALESCE($3::timestamptz, now())
            ORDER BY ticker, time
            """,
            start_ts,
            days,
            end_ts,
        )

    by_ticker: dict[str, list[Candle]] = {}
    for r in candle_rows:
        by_ticker.setdefault(r["ticker"], []).append(Candle(**dict(r)))

    try:
        result = ml_selection.load_and_score(by_ticker, cost=cost)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail={"code": "model_not_trained", "message": str(e)})

    return _to_ml_result(result)
