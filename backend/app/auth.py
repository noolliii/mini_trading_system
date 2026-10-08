"""로그인 · 세션 · 역할 확인.

규칙 세 가지를 지킵니다.

1. 비밀번호 원문은 이 파일 밖으로 나가지 않습니다. 저장은 bcrypt 해시로만 합니다.
2. 실패는 인증 실패 · 세션 만료 · 권한 부족 셋으로 나눠서 던집니다.
3. 누가 무엇을 할 수 있는지는 화면이 아니라 여기(PERMISSIONS)에 있습니다.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Response
from pydantic import BaseModel

from . import db

SESSION_COOKIE = "session_id"
SESSION_TTL_MINUTES = int(os.getenv("SESSION_TTL_MINUTES", "120"))
BCRYPT_ROUNDS = 12

# bcrypt 는 72 바이트까지만 봅니다. 더 긴 입력은 라이브러리가 거부하므로 먼저 잘라 둡니다
MAX_PASSWORD_BYTES = 72


class CurrentUser(BaseModel):
    id: int
    username: str
    role: str


# ──────────────────────────────────────────────────────────────
# 누가 무엇을 할 수 있나 — 이 표가 화면이 아니라 백엔드 코드에 있어야 합니다.
# 화면에서 버튼을 숨기는 것은 친절일 뿐이고, 판단은 전부 여기를 보고 합니다.
# ──────────────────────────────────────────────────────────────
PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "sensor:read",
        "account:read:any",
        "account:write:any",
        "user:read",
        "user:write",
        "kis:token",
    },
    "user": {
        "sensor:read",
        "account:read:own",
        "account:write:own",
    },
}


def allows(role: str, action: str) -> bool:
    return action in PERMISSIONS.get(role, set())


# ──────────────────────────────────────────────────────────────
# 실패 세 가지. 호출하는 쪽에서 섞이지 않게 만들어 둡니다
# ──────────────────────────────────────────────────────────────
def auth_failed() -> HTTPException:
    """인증 실패 — 아이디가 틀렸는지 비밀번호가 틀렸는지는 알려주지 않습니다."""
    return HTTPException(
        status_code=401,
        detail={
            "code": "invalid_credentials",
            "message": "아이디 또는 비밀번호가 맞지 않습니다",
        },
    )


def session_expired() -> HTTPException:
    """세션 만료 — 로그인은 했었지만 지금은 유효하지 않습니다."""
    return HTTPException(
        status_code=401,
        detail={"code": "session_expired", "message": "다시 로그인해 주세요"},
    )


def forbidden() -> HTTPException:
    """권한 부족 — 누구인지는 알지만 그 일은 할 수 없습니다."""
    return HTTPException(
        status_code=403,
        detail={"code": "forbidden", "message": "이 작업에 대한 권한이 없습니다"},
    )


def require(user: CurrentUser, action: str) -> None:
    if not allows(user.role, action):
        raise forbidden()


# ──────────────────────────────────────────────────────────────
# 비밀번호
# ──────────────────────────────────────────────────────────────
def hash_password(plain: str) -> str:
    raw = plain.encode("utf-8")[:MAX_PASSWORD_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    raw = plain.encode("utf-8")[:MAX_PASSWORD_BYTES]
    try:
        return bcrypt.checkpw(raw, hashed.encode("ascii"))
    except ValueError:
        # 저장된 값이 bcrypt 해시가 아닐 때(= 평문이 들어가 있을 때)입니다.
        # 평문과 맞춰보지 않고 그냥 실패시킵니다.
        return False


# 아이디가 없을 때도 비밀번호를 맞춰본 것과 같은 시간이 걸리게 합니다.
# 응답 시간만 보고 "이 아이디는 있다"를 알아내지 못하게 하는 장치입니다.
_TIMING_EQUALIZER_HASH = hash_password("timing-equalizer")


async def authenticate(username: str, password: str) -> CurrentUser:
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, username, password_hash, role FROM users WHERE username = $1",
            username,
        )

    if row is None:
        verify_password(password, _TIMING_EQUALIZER_HASH)
        raise auth_failed()

    if not verify_password(password, row["password_hash"]):
        raise auth_failed()

    return CurrentUser(id=row["id"], username=row["username"], role=row["role"])


# ──────────────────────────────────────────────────────────────
# 세션
# ──────────────────────────────────────────────────────────────
async def create_session(user_id: int) -> tuple[str, datetime]:
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=SESSION_TTL_MINUTES)

    async with db.acquire() as conn:
        async with conn.transaction():
            # 만료된 줄은 로그인할 때마다 조금씩 치웁니다
            await conn.execute("DELETE FROM sessions WHERE expires_at <= now()")
            await conn.execute(
                """
                INSERT INTO sessions (session_id, user_id, expires_at)
                VALUES ($1, $2, $3)
                """,
                session_id,
                user_id,
                expires_at,
            )

    return session_id, expires_at


async def destroy_session(session_id: str) -> None:
    async with db.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE session_id = $1", session_id)


def set_session_cookie(response: Response, session_id: str, expires_at: datetime) -> None:
    max_age = max(int((expires_at - datetime.now(timezone.utc)).total_seconds()), 0)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=max_age,
        httponly=True,  # 자바스크립트가 못 읽습니다
        samesite="lax",
        path="/",
        # 수업은 http://localhost 라 secure 를 켜지 않습니다.
        # https 로 배포할 때는 secure=True 로 바꿉니다.
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


async def current_user(
    session_id: Annotated[str | None, Cookie()] = None,
) -> CurrentUser:
    """쿠키의 세션으로 지금 요청한 사람을 찾습니다. 없거나 만료면 401."""
    if not session_id:
        raise session_expired()

    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT u.id, u.username, u.role, s.expires_at
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.session_id = $1
            """,
            session_id,
        )

    if row is None:
        raise session_expired()

    if row["expires_at"] <= datetime.now(timezone.utc):
        await destroy_session(session_id)
        raise session_expired()

    return CurrentUser(id=row["id"], username=row["username"], role=row["role"])


CurrentUserDep = Annotated[CurrentUser, Depends(current_user)]
